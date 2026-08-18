from __future__ import annotations

from datetime import timedelta
from decimal import Decimal, ROUND_HALF_UP
from typing import Any

from .core import CompanionError, canonical, digest, new_id
from .db import SCHEMA_VERSION, row_dict, rows_dict
from .timeutil import iso, parse, utc_now


PROGRAM_FIELDS = {
    "objective",
    "success_criteria",
    "benchmark",
    "risk_budget",
    "universe",
    "horizons",
    "operating_cadence",
    "stop_conditions",
    "account_ids",
}
CONTEXT_REF_FIELDS = {
    "investor_revision_id": "investor",
    "mandate_revision_id": "mandate",
    "attention_revision_id": "attention",
}
QUALIFICATION_FIELDS = {
    "evidence_state",
    "independent_source_count",
    "data_freshness",
    "falsifiers",
    "major_unknowns",
    "counterevidence",
    "decision_basis",
}
STAGE_ORDER = {"observed": 0, "researching": 1, "qualified": 2, "actionable": 3}


class InvestmentOperatingSystem:
    """User-facing operating loop above the existing ledgers and research runtime.

    This layer coordinates immutable references.  It deliberately does not own
    positions, prices, research results, Decisions, Executions, or performance
    arithmetic.
    """

    def __init__(self, companion):
        self.c = companion
        self.db = companion.db

    @staticmethod
    def _text(value: Any, label: str) -> str:
        if not isinstance(value, str) or not value.strip():
            raise CompanionError(f"{label} must be a non-empty string")
        return value.strip()

    @staticmethod
    def _string_list(value: Any, label: str, *, allow_empty: bool = False) -> list[str]:
        if not isinstance(value, list) or (not value and not allow_empty):
            raise CompanionError(f"{label} must be a {'possibly empty ' if allow_empty else 'non-empty '}list")
        if any(not isinstance(item, str) or not item.strip() for item in value):
            raise CompanionError(f"{label} must contain non-empty strings")
        cleaned = [item.strip() for item in value]
        if len(cleaned) != len(set(cleaned)):
            raise CompanionError(f"{label} must not contain duplicates")
        return cleaned

    def _validate_program_content(self, content: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(content, dict):
            raise CompanionError("InvestmentProgram content must be an object")
        missing = PROGRAM_FIELDS - set(content)
        if missing:
            raise CompanionError(f"InvestmentProgram content missing: {sorted(missing)}")
        unknown = set(content) - PROGRAM_FIELDS
        if unknown:
            raise CompanionError(f"InvestmentProgram content has unknown fields: {sorted(unknown)}")
        result = dict(content)
        result["objective"] = self._text(content["objective"], "objective")
        result["success_criteria"] = self._string_list(content["success_criteria"], "success_criteria")
        result["stop_conditions"] = self._string_list(content["stop_conditions"], "stop_conditions")
        for key in ("benchmark", "risk_budget", "universe", "horizons", "operating_cadence"):
            if not isinstance(content[key], dict) or not content[key]:
                raise CompanionError(f"InvestmentProgram {key} must be a non-empty object")
        accounts = self._string_list(content["account_ids"], "account_ids")
        for account_id in accounts:
            account = self.c.financial.account_get(account_id)
            if account["status"] != "active":
                raise CompanionError(f"InvestmentProgram account is not active: {account_id}")
        result["account_ids"] = accounts
        return result

    def _validate_context_refs(self, refs: dict[str, Any], *, require_current: bool) -> dict[str, str]:
        if not isinstance(refs, dict) or set(refs) != set(CONTEXT_REF_FIELDS):
            raise CompanionError(f"context_refs must be exactly: {sorted(CONTEXT_REF_FIELDS)}")
        result: dict[str, str] = {}
        for key, context_type in CONTEXT_REF_FIELDS.items():
            revision_id = self._text(refs[key], key)
            item = self.c.cognition.context_get(revision_id)
            if item["context_type"] != context_type:
                raise CompanionError(f"{key} references the wrong Context type")
            if item["status"] not in {"current", "trial"}:
                raise CompanionError(f"{key} must reference a confirmed Context")
            if require_current:
                current = self.c.cognition.context_current(context_type)
                if not current or current["id"] != revision_id:
                    raise CompanionError(f"{key} is not the current effective Context")
            result[key] = revision_id
        return result

    def program_revision_get(self, revision_id: str) -> dict[str, Any]:
        with self.db.connect() as con:
            item = row_dict(
                con.execute(
                    "SELECT * FROM investment_program_revisions WHERE id=?",
                    (revision_id,),
                ).fetchone()
            )
        if not item:
            raise CompanionError(f"InvestmentProgram revision not found: {revision_id}")
        return item

    def program_get(self, program_id: str) -> dict[str, Any]:
        with self.db.connect() as con:
            item = row_dict(con.execute("SELECT * FROM investment_programs WHERE id=?", (program_id,)).fetchone())
            revisions = rows_dict(
                con.execute(
                    "SELECT * FROM investment_program_revisions WHERE program_id=? ORDER BY revision DESC",
                    (program_id,),
                ).fetchall()
            )
        if not item:
            raise CompanionError(f"InvestmentProgram not found: {program_id}")
        item["current_revision"] = next(
            (revision for revision in revisions if revision["id"] == item.get("current_revision_id")),
            None,
        )
        item["revisions"] = revisions
        return item

    def program_list(self, status: str | None = None) -> list[dict[str, Any]]:
        query = "SELECT * FROM investment_programs"
        params: tuple[Any, ...] = ()
        if status:
            if status not in {"draft", "active", "paused", "superseded", "archived"}:
                raise CompanionError("invalid InvestmentProgram status filter")
            query += " WHERE status=?"
            params = (status,)
        query += " ORDER BY updated_at DESC"
        with self.db.connect() as con:
            return rows_dict(con.execute(query, params).fetchall())

    def _expire_trial_programs(self) -> None:
        now = iso()
        with self.db.transaction() as con:
            expired = con.execute(
                "SELECT id,program_id FROM investment_program_revisions "
                "WHERE status='trial' AND expires_at IS NOT NULL AND expires_at<=?",
                (now,),
            ).fetchall()
            for row in expired:
                con.execute(
                    "UPDATE investment_program_revisions SET status='expired' WHERE id=?",
                    (row["id"],),
                )
                paused = con.execute(
                    "UPDATE investment_programs SET status='paused',version=version+1,updated_at=? "
                    "WHERE id=? AND current_revision_id=? AND status='active'",
                    (now, row["program_id"], row["id"]),
                ).rowcount
                self.c._audit(
                    con,
                    "system",
                    "expire_trial",
                    "investment_program_revision",
                    row["id"],
                    before={"status": "trial"},
                    after={"status": "expired"},
                    reason="trial validity elapsed",
                )
                if paused:
                    self.c._audit(
                        con,
                        "system",
                        "pause",
                        "investment_program",
                        row["program_id"],
                        before={"status": "active"},
                        after={"status": "paused"},
                        reason="current trial revision expired",
                    )

    def program_current(self) -> dict[str, Any] | None:
        self._expire_trial_programs()
        now = iso()
        with self.db.connect() as con:
            row = con.execute(
                "SELECT p.id FROM investment_programs p "
                "JOIN investment_program_revisions r ON r.id=p.current_revision_id "
                "WHERE p.status='active' AND r.status IN ('current','trial') "
                "AND (r.effective_from IS NULL OR r.effective_from<=?) "
                "AND (r.expires_at IS NULL OR r.expires_at>?) "
                "ORDER BY p.activated_at DESC LIMIT 1",
                (now, now),
            ).fetchone()
        return self.program_get(row["id"]) if row else None

    def _program_alignment(self, program: dict[str, Any]) -> dict[str, Any]:
        """Revalidate mutable reality that an active Program only references."""

        issues: list[str] = []
        revision = program.get("current_revision")
        if not revision or revision.get("status") not in {"current", "trial"}:
            issues.append("current Program revision is missing or no longer effective")
        else:
            try:
                self._validate_context_refs(revision["context_refs"], require_current=True)
            except CompanionError as exc:
                issues.append(str(exc))
            try:
                self._validate_program_content(revision["content"])
            except CompanionError as exc:
                issues.append(str(exc))
        return {"aligned": not issues, "issues": issues}

    def program_create(
        self,
        *,
        name: str,
        content: dict[str, Any],
        context_refs: dict[str, Any],
        reason: str,
        expires_at: str | None = None,
        actor: str = "primary-codex",
    ) -> dict[str, Any]:
        name = self._text(name, "InvestmentProgram name")
        reason = self._text(reason, "InvestmentProgram reason")
        content = self._validate_program_content(content)
        refs = self._validate_context_refs(context_refs, require_current=False)
        if expires_at is not None and parse(expires_at) <= utc_now():
            raise CompanionError("InvestmentProgram expires_at must be in the future")
        program_id, revision_id, now = new_id("program"), new_id("programrev"), iso()
        content_hash = digest("investment-companion.investment-program/v1", program_id, content, refs)
        with self.db.transaction() as con:
            con.execute(
                "INSERT INTO investment_programs(id,name,status,created_at,updated_at) VALUES(?,?,'draft',?,?)",
                (program_id, name, now, now),
            )
            con.execute(
                "INSERT INTO investment_program_revisions(id,program_id,revision,status,content_json,context_refs_json,reason,expires_at,content_hash,created_at) "
                "VALUES(?,?,1,'draft',?,?,?,?,?,?)",
                (revision_id, program_id, canonical(content), canonical(refs), reason, expires_at, content_hash, now),
            )
            self.c._audit(
                con,
                actor,
                "create",
                "investment_program",
                program_id,
                after={"name": name, "revision_id": revision_id, "status": "draft"},
                reason=reason,
            )
        return self.program_get(program_id)

    def program_revise(
        self,
        *,
        program_id: str,
        expected_version: int,
        content: dict[str, Any],
        context_refs: dict[str, Any],
        reason: str,
        expires_at: str | None = None,
        actor: str = "primary-codex",
    ) -> dict[str, Any]:
        program = self.program_get(program_id)
        if program["status"] in {"superseded", "archived"}:
            raise CompanionError("closed InvestmentProgram cannot be revised")
        if program["version"] != expected_version:
            raise CompanionError("InvestmentProgram version conflict")
        reason = self._text(reason, "InvestmentProgram revision reason")
        content = self._validate_program_content(content)
        refs = self._validate_context_refs(context_refs, require_current=False)
        if expires_at is not None and parse(expires_at) <= utc_now():
            raise CompanionError("InvestmentProgram expires_at must be in the future")
        with self.db.connect() as con:
            next_revision = con.execute(
                "SELECT COALESCE(MAX(revision),0)+1 FROM investment_program_revisions WHERE program_id=?",
                (program_id,),
            ).fetchone()[0]
            draft = con.execute(
                "SELECT id FROM investment_program_revisions WHERE program_id=? AND status='draft'",
                (program_id,),
            ).fetchone()
        revision_id, now = new_id("programrev"), iso()
        content_hash = digest("investment-companion.investment-program/v1", program_id, content, refs)
        with self.db.connect() as con:
            identical = row_dict(
                con.execute(
                    "SELECT * FROM investment_program_revisions WHERE content_hash=?",
                    (content_hash,),
                ).fetchone()
            )
        if identical:
            if identical["program_id"] == program_id and identical["status"] == "draft":
                return self.program_get(program_id)
            raise CompanionError("an identical InvestmentProgram revision already exists")
        with self.db.transaction() as con:
            if draft:
                con.execute(
                    "UPDATE investment_program_revisions SET status='superseded' WHERE id=?",
                    (draft["id"],),
                )
            changed = con.execute(
                "UPDATE investment_programs SET version=version+1,updated_at=? WHERE id=? AND version=?",
                (now, program_id, expected_version),
            ).rowcount
            if changed != 1:
                raise CompanionError("InvestmentProgram version conflict")
            con.execute(
                "INSERT INTO investment_program_revisions(id,program_id,revision,status,content_json,context_refs_json,parent_id,reason,expires_at,content_hash,created_at) "
                "VALUES(?,?,?,'draft',?,?,?,?,?,?,?)",
                (
                    revision_id,
                    program_id,
                    next_revision,
                    canonical(content),
                    canonical(refs),
                    program.get("current_revision_id"),
                    reason,
                    expires_at,
                    content_hash,
                    now,
                ),
            )
            self.c._audit(
                con,
                actor,
                "revise",
                "investment_program",
                program_id,
                before={"version": expected_version, "current_revision_id": program.get("current_revision_id")},
                after={"version": expected_version + 1, "draft_revision_id": revision_id},
                reason=reason,
            )
        return self.program_get(program_id)

    def program_confirm(
        self,
        revision_id: str,
        *,
        user_approval_ref: str,
        trial: bool = False,
        supersedes_program_id: str | None = None,
        actor: str = "primary-codex",
    ) -> dict[str, Any]:
        self.c.jobs.feature_require("v5_operating_system")
        approval = self._text(user_approval_ref, "user_approval_ref")
        revision = self.program_revision_get(revision_id)
        target = "trial" if trial else "current"
        if revision["status"] in {"draft", target}:
            self._validate_context_refs(revision["context_refs"], require_current=True)
            self._validate_program_content(revision["content"])
        if trial and (not revision.get("expires_at") or parse(revision["expires_at"]) <= utc_now()):
            raise CompanionError("trial InvestmentProgram requires a future expires_at")
        if revision["status"] == target and revision.get("user_approval_ref") == approval:
            return self.program_get(revision["program_id"])
        if revision["status"] != "draft":
            raise CompanionError("only a draft InvestmentProgram revision can be confirmed")
        program = self.program_get(revision["program_id"])
        active = self.program_current()
        if active and active["id"] != program["id"]:
            if supersedes_program_id != active["id"]:
                raise CompanionError(
                    "another InvestmentProgram is active; explicitly identify it as supersedes_program_id"
                )
        now = iso()
        with self.db.transaction() as con:
            if active and active["id"] != program["id"]:
                con.execute(
                    "UPDATE investment_programs SET status='superseded',closed_at=?,updated_at=?,version=version+1 WHERE id=?",
                    (now, now, active["id"]),
                )
                con.execute(
                    "UPDATE investment_program_revisions SET status='superseded' "
                    "WHERE program_id=? AND status IN ('current','trial')",
                    (active["id"],),
                )
            con.execute(
                "UPDATE investment_program_revisions SET status='superseded' "
                "WHERE program_id=? AND status IN ('current','trial')",
                (program["id"],),
            )
            changed = con.execute(
                "UPDATE investment_program_revisions SET status=?,effective_from=COALESCE(effective_from,?),user_approval_ref=?,confirmed_at=? WHERE id=? AND status='draft'",
                (target, now, approval, now, revision_id),
            ).rowcount
            if changed != 1:
                raise CompanionError("InvestmentProgram revision was already confirmed or superseded")
            con.execute(
                "UPDATE investment_programs SET status='active',current_revision_id=?,activated_at=COALESCE(activated_at,?),updated_at=?,version=version+1 WHERE id=?",
                (revision_id, now, now, program["id"]),
            )
            self.c._audit(
                con,
                actor,
                "confirm_trial" if trial else "confirm",
                "investment_program",
                program["id"],
                before={"status": program["status"], "current_revision_id": program.get("current_revision_id")},
                after={"status": "active", "current_revision_id": revision_id},
                reason=approval,
            )
        return self.program_get(program["id"])

    def program_set_status(
        self,
        program_id: str,
        status: str,
        *,
        reason: str,
        actor: str = "primary-codex",
    ) -> dict[str, Any]:
        if status not in {"active", "paused", "archived"}:
            raise CompanionError("InvestmentProgram status must be active, paused, or archived")
        self._expire_trial_programs()
        reason = self._text(reason, "InvestmentProgram status reason")
        program = self.program_get(program_id)
        if program["status"] in {"superseded", "archived"}:
            raise CompanionError("closed InvestmentProgram status cannot be changed")
        if status == "active":
            self.c.jobs.feature_require("v5_operating_system")
            alignment = self._program_alignment(program)
            if not alignment["aligned"]:
                raise CompanionError("InvestmentProgram cannot resume: " + "; ".join(alignment["issues"]))
            active = self.program_current()
            if active and active["id"] != program_id:
                raise CompanionError("another InvestmentProgram is already active")
        now = iso()
        with self.db.transaction() as con:
            con.execute(
                "UPDATE investment_programs SET status=?,updated_at=?,closed_at=?,version=version+1 WHERE id=?",
                (status, now, now if status == "archived" else None, program_id),
            )
            self.c._audit(
                con,
                actor,
                status,
                "investment_program",
                program_id,
                before={"status": program["status"]},
                after={"status": status},
                reason=reason,
            )
        return self.program_get(program_id)

    def _require_active_program(self, program_id: str | None = None) -> dict[str, Any]:
        current = self.program_current()
        if not current:
            raise CompanionError("no active InvestmentProgram")
        if program_id and current["id"] != program_id:
            raise CompanionError("operation must use the active InvestmentProgram")
        alignment = self._program_alignment(current)
        if not alignment["aligned"]:
            raise CompanionError(
                "active InvestmentProgram requires review: " + "; ".join(alignment["issues"])
            )
        return current

    def _exists(self, table: str, identifier: str) -> None:
        allowed = {
            "source_items",
            "artifacts",
            "events",
            "observations",
            "market_snapshots",
            "context_revisions",
            "cognitive_objects",
            "cognitive_revisions",
            "calculations",
            "artifact_manifests",
            "data_objects",
            "dataset_snapshots",
            "research_hypotheses",
            "strategy_versions",
            "experiment_runs",
            "agent_invocations",
            "shadow_books",
            "manual_action_specs",
            "investment_programs",
            "investment_program_revisions",
            "opportunities",
            "opportunity_transitions",
            "decision_queue_items",
            "operating_briefs",
            "program_scorecards",
        }
        if table not in allowed:
            raise CompanionError("unsupported evidence table")
        with self.db.connect() as con:
            row = con.execute(f"SELECT 1 FROM {table} WHERE id=?", (identifier,)).fetchone()
        if not row:
            raise CompanionError(f"evidence reference not found: {identifier}")

    def _validate_evidence_refs(
        self,
        refs: Any,
        *,
        allow_empty: bool = False,
        immutable_only: bool = False,
    ) -> list[str]:
        result = self._string_list(refs, "evidence_refs", allow_empty=allow_empty)
        prefixes = {
            "src_": "source_items",
            "art_": "artifacts",
            "evt_": "events",
            "obs_": "observations",
            "mkt_": "market_snapshots",
            "ctx_": "context_revisions",
            "ths_": "cognitive_objects",
            "dec_": "cognitive_objects",
            "rev_": "cognitive_objects",
            "cogrev_": "cognitive_revisions",
            "calc_": "calculations",
            "manifest_": "artifact_manifests",
            "dataobj_": "data_objects",
            "snapshot_": "dataset_snapshots",
            "hyp_": "research_hypotheses",
            "strategy_": "strategy_versions",
            "experiment_": "experiment_runs",
            "agentcall_": "agent_invocations",
            "shadowbook_": "shadow_books",
            "action_": "manual_action_specs",
            "program_": "investment_programs",
            "programrev_": "investment_program_revisions",
            "opportunity_": "opportunities",
            "opptransition_": "opportunity_transitions",
            "queue_": "decision_queue_items",
            "brief_": "operating_briefs",
            "scorecard_": "program_scorecards",
        }
        immutable_tables = {
            "source_items",
            "events",
            "observations",
            "market_snapshots",
            "context_revisions",
            "cognitive_revisions",
            "calculations",
            "artifact_manifests",
            "data_objects",
            "dataset_snapshots",
            "research_hypotheses",
            "strategy_versions",
            "experiment_runs",
            "agent_invocations",
            "manual_action_specs",
            "investment_program_revisions",
            "opportunity_transitions",
            "operating_briefs",
            "program_scorecards",
        }
        for reference in result:
            table = next(
                (table for prefix, table in sorted(prefixes.items(), key=lambda item: len(item[0]), reverse=True) if reference.startswith(prefix)),
                None,
            )
            if table is None and len(reference) == 64 and all(ch in "0123456789abcdef" for ch in reference):
                table = "dataset_snapshots"
            if not table:
                raise CompanionError(f"evidence reference is not an immutable Companion ID: {reference}")
            if immutable_only and table not in immutable_tables:
                raise CompanionError(f"Opportunity evidence must use an immutable revision or fact: {reference}")
            if table == "artifact_manifests":
                self.c.data.manifest_get(reference, verify=True)
            elif table == "data_objects":
                self.c.data.object_get(reference, verify=True)
            elif table == "dataset_snapshots":
                self.c.data.snapshot_get(reference, verify=True)
            elif table == "calculations":
                self.c.financial.calculation_get(reference)
            else:
                self._exists(table, reference)
        return result

    def _validate_thesis(self, thesis_id: str | None) -> None:
        if not thesis_id:
            return
        thesis = self.c.cognition.object_get(thesis_id)
        if thesis["object_type"] != "thesis" or thesis["status"] != "active":
            raise CompanionError("Opportunity thesis_id must reference an active Thesis")

    def _validate_strategy(self, strategy_version_id: str | None) -> None:
        if not strategy_version_id:
            return
        strategy = self.c.research.strategy_get(strategy_version_id)
        if strategy["status"] not in {"research_passed", "shadow"}:
            raise CompanionError("Opportunity StrategyVersion is not research-qualified")

    def _validate_decision(self, decision_revision_id: str) -> tuple[dict[str, Any], dict[str, Any]]:
        revision = self.c.cognition.revision_get(decision_revision_id)
        decision = self.c.cognition.object_get(revision["object_id"])
        if (
            decision["object_type"] != "decision"
            or decision["status"] != "issued"
            or decision.get("current_revision_id") != decision_revision_id
        ):
            raise CompanionError("Opportunity requires the current issued Decision revision")
        metadata = revision.get("metadata", {})
        for key in ("valid_until", "invalidators", "no_action"):
            if key not in metadata:
                raise CompanionError(f"actionable Decision metadata missing: {key}")
        if parse(metadata["valid_until"]) <= utc_now():
            raise CompanionError("actionable Decision has expired")
        if not isinstance(metadata["invalidators"], list) or not metadata["invalidators"]:
            raise CompanionError("actionable Decision requires explicit invalidators")
        if not isinstance(metadata["no_action"], dict) or not metadata["no_action"]:
            raise CompanionError("actionable Decision requires a concrete no-action alternative")
        return revision, decision

    def _validate_qualification(self, qualification: Any, stage: str) -> dict[str, Any]:
        if not isinstance(qualification, dict) or set(qualification) != QUALIFICATION_FIELDS:
            raise CompanionError(f"qualification must be exactly: {sorted(QUALIFICATION_FIELDS)}")
        qualification = dict(qualification)
        state = qualification["evidence_state"]
        if state not in {"incomplete", "corroborated", "decision_grade"}:
            raise CompanionError("invalid qualification evidence_state")
        count = qualification["independent_source_count"]
        if isinstance(count, bool) or not isinstance(count, int) or count < 0:
            raise CompanionError("independent_source_count must be a non-negative integer")
        freshness = qualification["data_freshness"]
        if freshness not in {"current", "partial", "stale", "unknown"}:
            raise CompanionError("invalid qualification data_freshness")
        for field in ("falsifiers", "major_unknowns", "counterevidence"):
            qualification[field] = self._string_list(
                qualification[field], field, allow_empty=field != "falsifiers"
            )
        qualification["decision_basis"] = self._text(qualification["decision_basis"], "decision_basis")
        if stage in {"qualified", "actionable"} and (state not in {"corroborated", "decision_grade"} or count < 2):
            raise CompanionError("qualified Opportunity requires corroborated evidence from at least two independent sources")
        if stage == "actionable":
            if state != "decision_grade" or freshness != "current" or qualification["major_unknowns"]:
                raise CompanionError("actionable Opportunity requires current decision-grade evidence and no major unknowns")
        return qualification

    def opportunity_create(
        self,
        *,
        subject: dict[str, Any],
        evidence_refs: list[str],
        reason: str,
        program_id: str | None = None,
        thesis_id: str | None = None,
        strategy_version_id: str | None = None,
        actor: str = "primary-codex",
    ) -> dict[str, Any]:
        self.c.jobs.feature_require("v5_operating_system")
        program = self._require_active_program(program_id)
        if not isinstance(subject, dict) or not subject:
            raise CompanionError("Opportunity subject must be a non-empty object")
        reason = self._text(reason, "Opportunity reason")
        refs = self._validate_evidence_refs(evidence_refs, immutable_only=True)
        self._validate_thesis(thesis_id)
        self._validate_strategy(strategy_version_id)
        opportunity_id, transition_id, now = new_id("opportunity"), new_id("opptransition"), iso()
        transition_key = digest(
            "opportunity-create",
            program["id"],
            subject,
            refs,
            reason,
            thesis_id,
            strategy_version_id,
        )
        transition_hash = digest(
            "investment-companion.opportunity-transition/v1",
            opportunity_id,
            1,
            "observed",
            "active",
            refs,
            reason,
            subject,
            thesis_id,
            strategy_version_id,
            None,
            None,
        )
        saved_opportunity_id = opportunity_id
        with self.db.transaction() as con:
            existing = con.execute(
                "SELECT opportunity_id FROM opportunity_transitions WHERE idempotency_key=?",
                (transition_key,),
            ).fetchone()
            if existing:
                saved_opportunity_id = existing["opportunity_id"]
            else:
                con.execute(
                    "INSERT INTO opportunities(id,program_id,subject_json,stage,status,thesis_id,strategy_version_id,created_at,updated_at) "
                    "VALUES(?,?,?,'observed','active',?,?,?,?)",
                    (opportunity_id, program["id"], canonical(subject), thesis_id, strategy_version_id, now, now),
                )
                con.execute(
                    "INSERT INTO opportunity_transitions(id,opportunity_id,from_stage,to_stage,from_status,to_status,evidence_refs_json,reason,actor,content_hash,idempotency_key,created_at) "
                    "VALUES(?,?,'observed','observed','active','active',?,?,?,?,?,?)",
                    (transition_id, opportunity_id, canonical(refs), reason, actor, transition_hash, transition_key, now),
                )
                self.c._audit(
                    con,
                    actor,
                    "create",
                    "opportunity",
                    opportunity_id,
                    after={"program_id": program["id"], "stage": "observed", "subject": subject},
                    reason=reason,
                )
        return self.opportunity_get(saved_opportunity_id)

    def opportunity_get(self, opportunity_id: str) -> dict[str, Any]:
        with self.db.connect() as con:
            item = row_dict(con.execute("SELECT * FROM opportunities WHERE id=?", (opportunity_id,)).fetchone())
            transitions = rows_dict(
                con.execute(
                    "SELECT * FROM opportunity_transitions WHERE opportunity_id=? ORDER BY created_at,id",
                    (opportunity_id,),
                ).fetchall()
            )
        if not item:
            raise CompanionError(f"Opportunity not found: {opportunity_id}")
        item["transitions"] = transitions
        item["evidence_band"] = {
            "observed": "unassessed",
            "researching": "incomplete",
            "qualified": "corroborated",
            "actionable": "decision_grade",
        }[item["stage"]]
        return item

    def opportunity_list(
        self,
        *,
        program_id: str | None = None,
        stage: str | None = None,
        status: str | None = "active",
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        if limit <= 0 or limit > 500:
            raise CompanionError("Opportunity limit must be within 1..500")
        query, params = "SELECT * FROM opportunities WHERE 1=1", []
        if program_id:
            query += " AND program_id=?"
            params.append(program_id)
        if stage:
            if stage not in STAGE_ORDER:
                raise CompanionError("invalid Opportunity stage")
            query += " AND stage=?"
            params.append(stage)
        if status:
            if status not in {"active", "rejected", "expired", "closed"}:
                raise CompanionError("invalid Opportunity status filter")
            query += " AND status=?"
            params.append(status)
        query += " ORDER BY updated_at DESC LIMIT ?"
        params.append(limit)
        with self.db.connect() as con:
            return rows_dict(con.execute(query, params).fetchall())

    def opportunity_transition(
        self,
        opportunity_id: str,
        *,
        expected_version: int,
        to_stage: str,
        to_status: str,
        evidence_refs: list[str],
        reason: str,
        qualification: dict[str, Any] | None = None,
        decision_revision_id: str | None = None,
        idempotency_key: str | None = None,
        actor: str = "primary-codex",
    ) -> dict[str, Any]:
        self.c.jobs.feature_require("v5_operating_system")
        item = self.opportunity_get(opportunity_id)
        self._require_active_program(item["program_id"])
        if to_stage not in STAGE_ORDER or to_status not in {"active", "rejected", "expired", "closed"}:
            raise CompanionError("invalid Opportunity target state")
        reason = self._text(reason, "Opportunity transition reason")
        refs = self._validate_evidence_refs(evidence_refs, immutable_only=True)
        key = idempotency_key or digest(
            "opportunity-transition",
            opportunity_id,
            expected_version,
            to_stage,
            to_status,
            refs,
            reason,
            qualification,
            decision_revision_id,
        )
        transition_hash = digest(
            "investment-companion.opportunity-transition/v1",
            opportunity_id,
            expected_version,
            to_stage,
            to_status,
            refs,
            reason,
            qualification,
            decision_revision_id,
        )
        self._text(key, "Opportunity transition idempotency_key")
        with self.db.connect() as con:
            existing = row_dict(
                con.execute(
                    "SELECT * FROM opportunity_transitions WHERE idempotency_key=?",
                    (key,),
                ).fetchone()
            )
        if existing:
            if (
                existing["opportunity_id"] != opportunity_id
                or existing["content_hash"] != transition_hash
            ):
                raise CompanionError("Opportunity transition idempotency_key belongs to different inputs")
            return self.opportunity_get(opportunity_id)
        if item["version"] != expected_version:
            raise CompanionError("Opportunity version conflict")
        if item["status"] != "active":
            raise CompanionError("terminal Opportunity cannot transition")
        if to_status == "active":
            if STAGE_ORDER[to_stage] != STAGE_ORDER[item["stage"]] + 1:
                raise CompanionError("active Opportunity must advance exactly one evidence stage")
        elif to_stage != item["stage"]:
            raise CompanionError("closing an Opportunity must preserve its final evidence stage")
        next_qualification = item.get("qualification", {})
        next_decision_id = item.get("decision_revision_id")
        if to_stage in {"qualified", "actionable"} and to_status == "active":
            if not (item.get("thesis_id") or item.get("strategy_version_id")):
                raise CompanionError("qualified Opportunity requires an active Thesis or qualified StrategyVersion")
            self._validate_thesis(item.get("thesis_id"))
            self._validate_strategy(item.get("strategy_version_id"))
            next_qualification = self._validate_qualification(qualification, to_stage)
            if next_qualification["independent_source_count"] > len(refs):
                raise CompanionError("qualification claims more independent sources than its frozen evidence_refs")
        elif qualification is not None:
            raise CompanionError("qualification may be supplied only when entering qualified or actionable")
        if to_stage == "actionable" and to_status == "active":
            if not decision_revision_id:
                raise CompanionError("actionable Opportunity requires decision_revision_id")
            self._validate_decision(decision_revision_id)
            next_decision_id = decision_revision_id
        elif decision_revision_id is not None:
            raise CompanionError("decision_revision_id may be supplied only when entering actionable")
        now = iso()
        replayed = False
        with self.db.transaction() as con:
            changed = con.execute(
                "UPDATE opportunities SET stage=?,status=?,decision_revision_id=?,qualification_json=?,version=version+1,updated_at=?,closed_at=? "
                "WHERE id=? AND version=? AND status='active'",
                (
                    to_stage,
                    to_status,
                    next_decision_id,
                    canonical(next_qualification),
                    now,
                    now if to_status != "active" else None,
                    opportunity_id,
                    expected_version,
                ),
            ).rowcount
            if changed != 1:
                concurrent = row_dict(
                    con.execute(
                        "SELECT * FROM opportunity_transitions WHERE idempotency_key=?",
                        (key,),
                    ).fetchone()
                )
                if (
                    not concurrent
                    or concurrent["opportunity_id"] != opportunity_id
                    or concurrent["content_hash"] != transition_hash
                ):
                    raise CompanionError("Opportunity transition lost to another writer")
                replayed = True
            if not replayed:
                transition_id = new_id("opptransition")
                con.execute(
                    "INSERT INTO opportunity_transitions(id,opportunity_id,from_stage,to_stage,from_status,to_status,evidence_refs_json,reason,actor,content_hash,idempotency_key,created_at) "
                    "VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        transition_id,
                        opportunity_id,
                        item["stage"],
                        to_stage,
                        item["status"],
                        to_status,
                        canonical(refs),
                        reason,
                        actor,
                        transition_hash,
                        key,
                        now,
                    ),
                )
                self.c._audit(
                    con,
                    actor,
                    "transition",
                    "opportunity",
                    opportunity_id,
                    before={"stage": item["stage"], "status": item["status"], "version": expected_version},
                    after={"stage": to_stage, "status": to_status, "version": expected_version + 1},
                    reason=reason,
                )
        return self.opportunity_get(opportunity_id)

    def _expire_queue(self) -> int:
        now = iso()
        with self.db.transaction() as con:
            waking = con.execute(
                "SELECT id FROM decision_queue_items WHERE state='snoozed' "
                "AND snoozed_until IS NOT NULL AND snoozed_until<=? AND valid_until>?",
                (now, now),
            ).fetchall()
            for row in waking:
                con.execute(
                    "UPDATE decision_queue_items SET state='ready',snoozed_until=NULL,version=version+1,updated_at=? "
                    "WHERE id=? AND state='snoozed'",
                    (now, row["id"]),
                )
                self.c._audit(
                    con,
                    "system",
                    "snooze_elapsed",
                    "decision_queue_item",
                    row["id"],
                    before={"state": "snoozed"},
                    after={"state": "ready"},
                    reason="snoozed_until elapsed",
                )
            expiring = con.execute(
                "SELECT id,state FROM decision_queue_items "
                "WHERE state IN ('ready','presented','snoozed','accepted') AND valid_until<=?",
                (now,),
            ).fetchall()
            for row in expiring:
                con.execute(
                    "UPDATE decision_queue_items SET state='expired',snoozed_until=NULL,"
                    "response_reason='validity elapsed',responded_at=?,version=version+1,updated_at=? WHERE id=?",
                    (now, now, row["id"]),
                )
                self.c._audit(
                    con,
                    "system",
                    "expire",
                    "decision_queue_item",
                    row["id"],
                    before={"state": row["state"]},
                    after={"state": "expired"},
                    reason="validity elapsed",
                )
            return len(expiring)

    def queue_enqueue(
        self,
        opportunity_id: str,
        *,
        decision_revision_id: str,
        manual_action_spec_id: str | None = None,
        valid_until: str | None = None,
        idempotency_key: str | None = None,
        actor: str = "primary-codex",
    ) -> dict[str, Any]:
        self.c.jobs.feature_require("v5_operating_system")
        opportunity = self.opportunity_get(opportunity_id)
        program = self._require_active_program(opportunity["program_id"])
        if opportunity["status"] != "active" or opportunity["stage"] != "actionable":
            raise CompanionError("DecisionQueue accepts only active actionable Opportunities")
        if opportunity.get("decision_revision_id") != decision_revision_id:
            raise CompanionError("DecisionQueue Decision differs from the Opportunity")
        revision, _decision = self._validate_decision(decision_revision_id)
        decision_until = parse(revision["metadata"]["valid_until"])
        until = parse(valid_until) if valid_until else decision_until
        if until <= utc_now() or until > decision_until:
            raise CompanionError("DecisionQueue validity must be current and cannot outlive its Decision")
        if manual_action_spec_id:
            action = self.c.cognition.manual_action_get(manual_action_spec_id)
            if action["decision_revision_id"] != decision_revision_id:
                raise CompanionError("ManualActionSpec belongs to another Decision")
            if action["status"] not in {"draft", "presented", "accepted"}:
                raise CompanionError("ManualActionSpec is not presentable")
            if until > parse(action["valid_until"]):
                raise CompanionError("DecisionQueue cannot outlive its ManualActionSpec")
        key = idempotency_key or digest(
            "decision-queue",
            opportunity_id,
            decision_revision_id,
            manual_action_spec_id,
            iso(until),
        )
        self._text(key, "DecisionQueue idempotency_key")
        with self.db.connect() as con:
            existing = row_dict(
                con.execute("SELECT * FROM decision_queue_items WHERE idempotency_key=?", (key,)).fetchone()
            )
            active_for_decision = row_dict(
                con.execute(
                    "SELECT * FROM decision_queue_items WHERE decision_revision_id=? "
                    "AND state IN ('ready','presented','snoozed','accepted') LIMIT 1",
                    (decision_revision_id,),
                ).fetchone()
            )
        if existing:
            if (
                existing["opportunity_id"] != opportunity_id
                or existing["decision_revision_id"] != decision_revision_id
                or existing.get("manual_action_spec_id") != manual_action_spec_id
                or existing["valid_until"] != iso(until)
            ):
                raise CompanionError("DecisionQueue idempotency_key belongs to different inputs")
            return existing
        if active_for_decision:
            raise CompanionError(
                f"Decision already has an active DecisionQueue item: {active_for_decision['id']}"
            )
        queue_id, now = new_id("queue"), iso()
        saved_queue_id = queue_id
        with self.db.transaction() as con:
            concurrent = row_dict(
                con.execute("SELECT * FROM decision_queue_items WHERE idempotency_key=?", (key,)).fetchone()
            )
            if concurrent:
                if (
                    concurrent["opportunity_id"] != opportunity_id
                    or concurrent["decision_revision_id"] != decision_revision_id
                    or concurrent.get("manual_action_spec_id") != manual_action_spec_id
                    or concurrent["valid_until"] != iso(until)
                ):
                    raise CompanionError("DecisionQueue idempotency_key belongs to different inputs")
                saved_queue_id = concurrent["id"]
            else:
                concurrent_active = con.execute(
                    "SELECT id FROM decision_queue_items WHERE decision_revision_id=? "
                    "AND state IN ('ready','presented','snoozed','accepted') LIMIT 1",
                    (decision_revision_id,),
                ).fetchone()
                if concurrent_active:
                    raise CompanionError(
                        f"Decision already has an active DecisionQueue item: {concurrent_active['id']}"
                    )
                con.execute(
                    "INSERT INTO decision_queue_items(id,program_id,opportunity_id,decision_revision_id,manual_action_spec_id,state,valid_until,idempotency_key,created_at,updated_at) "
                    "VALUES(?,?,?,?,?,'ready',?,?,?,?)",
                    (
                        queue_id,
                        program["id"],
                        opportunity_id,
                        decision_revision_id,
                        manual_action_spec_id,
                        iso(until),
                        key,
                        now,
                        now,
                    ),
                )
                self.c._audit(
                    con,
                    actor,
                    "enqueue",
                    "decision_queue_item",
                    queue_id,
                    after={"opportunity_id": opportunity_id, "state": "ready", "valid_until": iso(until)},
                )
        return self.queue_get(saved_queue_id)

    def queue_get(self, queue_id: str) -> dict[str, Any]:
        self._expire_queue()
        with self.db.connect() as con:
            item = row_dict(con.execute("SELECT * FROM decision_queue_items WHERE id=?", (queue_id,)).fetchone())
        if not item:
            raise CompanionError(f"DecisionQueue item not found: {queue_id}")
        return item

    def queue_list(
        self,
        *,
        program_id: str | None = None,
        state: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        self._expire_queue()
        if limit <= 0 or limit > 500:
            raise CompanionError("DecisionQueue limit must be within 1..500")
        query, params = "SELECT * FROM decision_queue_items WHERE 1=1", []
        if program_id:
            query += " AND program_id=?"
            params.append(program_id)
        if state:
            if state not in {"ready", "presented", "snoozed", "accepted", "rejected", "expired", "closed"}:
                raise CompanionError("invalid DecisionQueue state filter")
            query += " AND state=?"
            params.append(state)
        query += " ORDER BY CASE state WHEN 'ready' THEN 0 WHEN 'presented' THEN 1 ELSE 2 END,valid_until,created_at LIMIT ?"
        params.append(limit)
        with self.db.connect() as con:
            return rows_dict(con.execute(query, params).fetchall())

    def _invalidate_queue_item(self, item: dict[str, Any], reason: str) -> None:
        now = iso()
        with self.db.transaction() as con:
            changed = con.execute(
                "UPDATE decision_queue_items SET state='expired',snoozed_until=NULL,response_reason=?,"
                "responded_at=?,version=version+1,updated_at=? WHERE id=? AND version=? "
                "AND state IN ('ready','presented','snoozed','accepted')",
                (reason, now, now, item["id"], item["version"]),
            ).rowcount
            if changed:
                self.c._audit(
                    con,
                    "system",
                    "invalidate",
                    "decision_queue_item",
                    item["id"],
                    before={"state": item["state"]},
                    after={"state": "expired"},
                    reason=reason,
                )

    def queue_card(self, queue_id: str) -> dict[str, Any]:
        item = self.queue_get(queue_id)
        if item["state"] not in {"ready", "presented", "snoozed", "accepted"}:
            raise CompanionError(f"DecisionQueue item is not active: {item['state']}")
        self._require_active_program(item["program_id"])
        opportunity = self.opportunity_get(item["opportunity_id"])
        try:
            revision, _decision = self._validate_decision(item["decision_revision_id"])
        except CompanionError as exc:
            self._invalidate_queue_item(item, f"Decision invalidated: {exc}")
            raise
        metadata = revision["metadata"]
        action_payload = None
        executable = None
        reasons: list[str] = []
        if item.get("manual_action_spec_id"):
            validation = self.c.cognition.manual_action_validate(item["manual_action_spec_id"])
            action = validation["spec"]
            if action["status"] not in {"draft", "presented", "accepted"}:
                reason = f"ManualActionSpec invalidated: {validation['reasons'] or [action['status']]}"
                self._invalidate_queue_item(item, reason)
                raise CompanionError(reason)
            action_payload = {
                key: action["spec"].get(key)
                for key in ("account_id", "asset_id", "side", "quantity", "price_range", "priority", "alternatives")
            }
            executable = validation["executable"]
            reasons = validation["reasons"]
        return {
            "schema": "investment-companion.action-card/v1",
            "queue_id": item["id"],
            "state": item["state"],
            "subject": opportunity["subject"],
            "valid_until": item["valid_until"],
            "evidence_band": opportunity["evidence_band"],
            "decision_revision_id": revision["id"],
            "action": action_payload,
            "executable_now": executable,
            "blocking_reasons": reasons,
            "invalidators": metadata["invalidators"],
            "no_action_alternative": metadata["no_action"],
            "source_refs": metadata.get("source_refs", []),
            "human_execution_only": True,
            "execution_created": False,
            "guarantees": {"profit": False, "high_win_rate": False},
        }

    def queue_respond(
        self,
        queue_id: str,
        *,
        state: str,
        reason: str | None = None,
        snoozed_until: str | None = None,
        attention_decision_id: str | None = None,
        actor: str = "primary-codex",
    ) -> dict[str, Any]:
        self.c.jobs.feature_require("v5_operating_system")
        item = self.queue_get(queue_id)
        self._require_active_program(item["program_id"])
        if state not in {"presented", "snoozed", "accepted", "rejected", "closed"}:
            raise CompanionError("invalid DecisionQueue response state")
        if state in {"snoozed", "rejected", "closed"}:
            reason = self._text(reason, f"DecisionQueue {state} reason")
        normalized_snooze = None
        if state == "snoozed":
            if not snoozed_until:
                raise CompanionError("snoozed DecisionQueue item requires snoozed_until")
            snooze_time = parse(snoozed_until)
            if snooze_time <= utc_now() or snooze_time >= parse(item["valid_until"]):
                raise CompanionError("DecisionQueue snoozed_until must be future and before valid_until")
            normalized_snooze = iso(snooze_time)
        elif snoozed_until is not None:
            raise CompanionError("snoozed_until may be supplied only for snoozed state")
        transitions = {
            "ready": {"presented", "snoozed", "rejected", "closed"},
            "presented": {"snoozed", "accepted", "rejected", "closed"},
            "snoozed": {"presented", "rejected", "closed"},
            "accepted": {"closed"},
        }
        if state == item["state"]:
            if state == "snoozed" and item.get("snoozed_until") != normalized_snooze:
                raise CompanionError("idempotent DecisionQueue snooze supplied a different snoozed_until")
            if (
                state == "presented"
                and attention_decision_id is not None
                and item.get("attention_decision_id") != attention_decision_id
            ):
                raise CompanionError("idempotent DecisionQueue presentation supplied a different AttentionDecision")
            return item
        if state not in transitions.get(item["state"], set()):
            raise CompanionError(f"invalid DecisionQueue transition: {item['state']} -> {state}")
        attention = None
        if state == "presented":
            if not attention_decision_id:
                raise CompanionError("presented DecisionQueue item requires attention_decision_id")
            attention = self.c.attention.get(attention_decision_id)
            if attention["action"] != "notify_now" or attention["status"] != "delivered":
                raise CompanionError("DecisionQueue presentation requires a delivered notify_now AttentionDecision")
            if queue_id not in attention.get("evidence", []):
                raise CompanionError("DecisionQueue AttentionDecision must cite the queue item as evidence")
        if state == "accepted" and item.get("manual_action_spec_id"):
            validation = self.c.cognition.manual_action_validate(item["manual_action_spec_id"])
            if not validation["executable"]:
                raise CompanionError(f"ManualActionSpec is not executable: {validation['reasons']}")
        if item.get("manual_action_spec_id"):
            action = self.c.cognition.manual_action_get(item["manual_action_spec_id"])
            if state == "presented" and action["status"] == "draft":
                self.c.cognition.manual_action_set_status(action["id"], "presented")
            elif state == "accepted":
                if action["status"] == "draft":
                    action = self.c.cognition.manual_action_set_status(action["id"], "presented")
                if action["status"] == "presented":
                    self.c.cognition.manual_action_set_status(action["id"], "accepted")
            elif state == "rejected" and action["status"] in {"draft", "presented"}:
                self.c.cognition.manual_action_set_status(action["id"], "rejected", reason)
        now = iso()
        with self.db.transaction() as con:
            changed = con.execute(
                "UPDATE decision_queue_items SET state=?,response_reason=?,attention_decision_id=COALESCE(?,attention_decision_id),"
                "presented_at=CASE WHEN ?='presented' THEN ? ELSE presented_at END,"
                "snoozed_until=?,"
                "responded_at=CASE WHEN ? IN ('accepted','rejected','closed') THEN ? ELSE responded_at END,"
                "version=version+1,updated_at=? WHERE id=? AND state=? AND version=?",
                (
                    state,
                    reason,
                    attention_decision_id,
                    state,
                    now,
                    normalized_snooze,
                    state,
                    now,
                    now,
                    queue_id,
                    item["state"],
                    item["version"],
                ),
            ).rowcount
            if changed != 1:
                raise CompanionError("DecisionQueue response lost to another writer")
            self.c._audit(
                con,
                actor,
                "respond",
                "decision_queue_item",
                queue_id,
                before={"state": item["state"]},
                after={"state": state, "attention_decision_id": attention["id"] if attention else item.get("attention_decision_id")},
                reason=reason,
            )
        return self.queue_get(queue_id)

    def _validate_brief_payload(self, brief_type: str, conclusion: str, payload: Any) -> dict[str, Any]:
        if brief_type not in {"daily", "weekly", "monthly"}:
            raise CompanionError("invalid operating brief type")
        if conclusion not in {"no_action", "action", "review_required", "insufficient_evidence"}:
            raise CompanionError("invalid operating brief conclusion")
        if not isinstance(payload, dict):
            raise CompanionError("operating brief payload must be an object")
        required = {"summary", "what_changed", "decision", "risks", "next_check_at", "queue_item_ids"}
        if brief_type == "weekly":
            required |= {"program_progress", "research_pipeline"}
        if brief_type == "monthly":
            required |= {"scorecard_id", "lessons", "proposed_changes"}
        missing = required - set(payload)
        if missing:
            raise CompanionError(f"operating brief payload missing: {sorted(missing)}")
        unknown = set(payload) - required
        if unknown:
            raise CompanionError(f"operating brief payload has unknown fields: {sorted(unknown)}")
        result = dict(payload)
        for key in ("summary", "decision"):
            result[key] = self._text(payload[key], key)
        for key in ("what_changed", "risks"):
            result[key] = self._string_list(payload[key], key, allow_empty=True)
        parse(self._text(payload["next_check_at"], "next_check_at"))
        result["queue_item_ids"] = self._string_list(payload["queue_item_ids"], "queue_item_ids", allow_empty=True)
        if brief_type == "weekly":
            for key in ("program_progress", "research_pipeline"):
                if not isinstance(payload[key], dict):
                    raise CompanionError(f"{key} must be an object")
        if brief_type == "monthly":
            self._exists("program_scorecards", self._text(payload["scorecard_id"], "scorecard_id"))
            for key in ("lessons", "proposed_changes"):
                result[key] = self._string_list(payload[key], key, allow_empty=True)
        return result

    def brief_prepare(
        self,
        *,
        brief_type: str,
        period_key: str,
        as_of: str,
        conclusion: str,
        payload: dict[str, Any],
        source_refs: list[str],
        idempotency_key: str | None = None,
        program_id: str | None = None,
        actor: str = "primary-codex",
    ) -> dict[str, Any]:
        self.c.jobs.feature_require("v5_operating_system")
        program = self._require_active_program(program_id)
        period_key = self._text(period_key, "period_key")
        as_of_time = parse(as_of)
        payload = self._validate_brief_payload(brief_type, conclusion, payload)
        if parse(payload["next_check_at"]) <= as_of_time:
            raise CompanionError("operating brief next_check_at must be after as_of")
        if brief_type == "monthly":
            scorecard = self.scorecard_get(payload["scorecard_id"])
            if scorecard["program_id"] != program["id"]:
                raise CompanionError("monthly brief Scorecard belongs to another InvestmentProgram")
        refs = self._validate_evidence_refs(source_refs, allow_empty=conclusion == "insufficient_evidence")
        queue_items = [self.queue_get(item_id) for item_id in payload["queue_item_ids"]]
        for item in queue_items:
            if item["program_id"] != program["id"] or item["state"] not in {"ready", "presented", "accepted"}:
                raise CompanionError("operating brief references an unavailable DecisionQueue item")
        if conclusion == "action" and not queue_items:
            raise CompanionError("action brief requires at least one DecisionQueue item")
        active_queue = self.queue_list(program_id=program["id"], limit=500)
        active_queue = [
            item
            for item in active_queue
            if item["state"] in {"ready", "presented", "snoozed", "accepted"}
        ]
        if conclusion == "no_action" and (queue_items or active_queue):
            raise CompanionError("no_action brief conflicts with an active DecisionQueue item")
        if conclusion != "action" and queue_items:
            raise CompanionError("only an action brief may reference DecisionQueue items")
        content_hash = digest(
            "investment-companion.operating-brief/v1",
            program["current_revision_id"],
            brief_type,
            period_key,
            as_of,
            conclusion,
            payload,
            refs,
        )
        key = idempotency_key or content_hash
        self._text(key, "operating brief idempotency_key")
        with self.db.connect() as con:
            existing = row_dict(con.execute("SELECT * FROM operating_briefs WHERE idempotency_key=?", (key,)).fetchone())
            previous = row_dict(
                con.execute(
                    "SELECT * FROM operating_briefs WHERE program_id=? AND brief_type=? AND period_key=? "
                    "ORDER BY revision DESC LIMIT 1",
                    (program["id"], brief_type, period_key),
                ).fetchone()
            )
        if existing:
            if existing["content_hash"] != content_hash:
                raise CompanionError("operating brief idempotency_key belongs to different content")
            return existing
        revision_number = (previous["revision"] + 1) if previous else 1
        brief_id, now = new_id("brief"), iso()
        with self.db.transaction() as con:
            if conclusion == "no_action":
                conflict = con.execute(
                    "SELECT id FROM decision_queue_items WHERE program_id=? "
                    "AND state IN ('ready','presented','snoozed','accepted') AND valid_until>? LIMIT 1",
                    (program["id"], iso()),
                ).fetchone()
                if conflict:
                    raise CompanionError("no_action brief conflicts with an active DecisionQueue item")
            if conclusion == "action":
                for queue_item in queue_items:
                    current = con.execute(
                        "SELECT state,valid_until FROM decision_queue_items WHERE id=?",
                        (queue_item["id"],),
                    ).fetchone()
                    if not current or current["state"] not in {"ready", "presented", "accepted"} or current["valid_until"] <= iso():
                        raise CompanionError("action brief DecisionQueue item changed before publication")
            if previous:
                con.execute("UPDATE operating_briefs SET status='superseded',updated_at=? WHERE id=?", (now, previous["id"]))
            con.execute(
                "INSERT INTO operating_briefs(id,program_id,program_revision_id,brief_type,period_key,revision,as_of,conclusion,payload_json,source_refs_json,content_hash,idempotency_key,status,supersedes,created_at,updated_at) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,'ready',?,?,?)",
                (
                    brief_id,
                    program["id"],
                    program["current_revision_id"],
                    brief_type,
                    period_key,
                    revision_number,
                    as_of,
                    conclusion,
                    canonical(payload),
                    canonical(refs),
                    content_hash,
                    key,
                    previous["id"] if previous else None,
                    now,
                    now,
                ),
            )
            self.c._audit(
                con,
                actor,
                "prepare",
                "operating_brief",
                brief_id,
                after={"type": brief_type, "period_key": period_key, "conclusion": conclusion, "revision": revision_number},
            )
        return self.brief_get(brief_id)

    def brief_get(self, brief_id: str) -> dict[str, Any]:
        with self.db.connect() as con:
            item = row_dict(con.execute("SELECT * FROM operating_briefs WHERE id=?", (brief_id,)).fetchone())
        if not item:
            raise CompanionError(f"operating brief not found: {brief_id}")
        return item

    def brief_list(
        self,
        *,
        program_id: str | None = None,
        brief_type: str | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        if limit <= 0 or limit > 500:
            raise CompanionError("operating brief limit must be within 1..500")
        query, params = "SELECT * FROM operating_briefs WHERE status!='superseded'", []
        if program_id:
            query += " AND program_id=?"
            params.append(program_id)
        if brief_type:
            if brief_type not in {"daily", "weekly", "monthly"}:
                raise CompanionError("invalid operating brief type filter")
            query += " AND brief_type=?"
            params.append(brief_type)
        query += " ORDER BY as_of DESC,revision DESC LIMIT ?"
        params.append(limit)
        with self.db.connect() as con:
            return rows_dict(con.execute(query, params).fetchall())

    def brief_mark_presented(
        self,
        brief_id: str,
        *,
        attention_decision_id: str,
        actor: str = "primary-codex",
    ) -> dict[str, Any]:
        item = self.brief_get(brief_id)
        self._require_active_program(item["program_id"])
        if item["status"] == "superseded":
            raise CompanionError("superseded operating brief cannot be presented")
        if item["status"] == "presented":
            if item.get("attention_decision_id") != attention_decision_id:
                raise CompanionError("operating brief was already presented with another AttentionDecision")
            return item
        attention = self.c.attention.get(attention_decision_id)
        if attention["action"] != "notify_now" or attention["status"] != "delivered":
            raise CompanionError("brief presentation requires a delivered notify_now AttentionDecision")
        if brief_id not in attention.get("evidence", []):
            raise CompanionError("operating brief AttentionDecision must cite the brief as evidence")
        now = iso()
        with self.db.transaction() as con:
            con.execute(
                "UPDATE operating_briefs SET status='presented',attention_decision_id=?,presented_at=COALESCE(presented_at,?),updated_at=? WHERE id=?",
                (attention_decision_id, now, now, brief_id),
            )
            self.c._audit(
                con,
                actor,
                "present",
                "operating_brief",
                brief_id,
                before={"status": item["status"]},
                after={"status": "presented", "attention_decision_id": attention_decision_id},
            )
        return self.brief_get(brief_id)

    @staticmethod
    def _resolve_output_path(outputs: Any, path: str) -> Any:
        if not isinstance(path, str) or not path.startswith("outputs."):
            raise CompanionError("scorecard output_path must start with outputs.")
        value = outputs
        for part in path.split(".")[1:]:
            if isinstance(value, dict) and part in value:
                value = value[part]
            elif isinstance(value, list) and part.isdigit() and int(part) < len(value):
                value = value[int(part)]
            else:
                raise CompanionError(f"scorecard output_path not found: {path}")
        if isinstance(value, (dict, list)):
            raise CompanionError("scorecard metric must resolve to a scalar Calculation output")
        return value

    @staticmethod
    def _rate(numerator: int, denominator: int) -> str | None:
        if denominator == 0:
            return None
        return format(
            (Decimal(numerator) / Decimal(denominator)).quantize(
                Decimal("0.0001"), rounding=ROUND_HALF_UP
            ),
            "f",
        )

    def program_metrics_calculate(
        self,
        *,
        period_start: str,
        period_end: str,
        program_id: str | None = None,
    ) -> dict[str, Any]:
        """Freeze auditable operating-flow metrics without inventing investment returns."""

        self.c.jobs.feature_require("v5_operating_system")
        program = self._require_active_program(program_id)
        start, end = parse(period_start), parse(period_end)
        if end <= start:
            raise CompanionError("program metrics period_end must be after period_start")
        if end > utc_now() + timedelta(seconds=5):
            raise CompanionError("program metrics period_end cannot be in the future")
        self._expire_queue()
        start_text, end_text = iso(start), iso(end)
        with self.db.connect() as con:
            opportunity_rows = rows_dict(
                con.execute(
                    "SELECT t.* FROM opportunity_transitions t "
                    "JOIN opportunities o ON o.id=t.opportunity_id "
                    "WHERE o.program_id=? AND t.created_at>=? AND t.created_at<? "
                    "ORDER BY t.created_at,t.id",
                    (program["id"], start_text, end_text),
                ).fetchall()
            )
            queue_rows = rows_dict(
                con.execute(
                    "SELECT * FROM decision_queue_items WHERE program_id=? AND created_at>=? AND created_at<? "
                    "ORDER BY created_at,id",
                    (program["id"], start_text, end_text),
                ).fetchall()
            )
            queue_audits = rows_dict(
                con.execute(
                    "SELECT a.* FROM audit_log a JOIN decision_queue_items q ON q.id=a.entity_id "
                    "WHERE a.entity_type='decision_queue_item' AND q.program_id=? "
                    "AND a.occurred_at>=? AND a.occurred_at<? ORDER BY a.occurred_at,a.id",
                    (program["id"], start_text, end_text),
                ).fetchall()
            )
            brief_rows = rows_dict(
                con.execute(
                    "SELECT * FROM operating_briefs WHERE program_id=? AND created_at>=? AND created_at<? "
                    "ORDER BY created_at,id",
                    (program["id"], start_text, end_text),
                ).fetchall()
            )

        opportunity_flow = {
            "observed": 0,
            "researching": 0,
            "qualified": 0,
            "actionable": 0,
            "rejected": 0,
            "expired": 0,
            "closed": 0,
        }
        for row in opportunity_rows:
            if row["to_status"] == "active":
                opportunity_flow[row["to_stage"]] += 1
            else:
                opportunity_flow[row["to_status"]] += 1
        opportunity_cohort = {
            row["opportunity_id"]
            for row in opportunity_rows
            if row["from_stage"] == "observed"
            and row["to_stage"] == "observed"
            and row["from_status"] == "active"
            and row["to_status"] == "active"
        }
        qualified_cohort = {
            row["opportunity_id"]
            for row in opportunity_rows
            if row["opportunity_id"] in opportunity_cohort
            and row["to_status"] == "active"
            and row["to_stage"] in {"qualified", "actionable"}
        }
        actionable_cohort = {
            row["opportunity_id"]
            for row in opportunity_rows
            if row["opportunity_id"] in opportunity_cohort
            and row["to_status"] == "active"
            and row["to_stage"] == "actionable"
        }

        queue_flow = {
            "enqueued": len(queue_rows),
            "presented": 0,
            "snoozed": 0,
            "accepted": 0,
            "rejected": 0,
            "expired": 0,
            "closed": 0,
            "invalidated": 0,
        }
        for row in queue_audits:
            after_state = row.get("after", {}).get("state")
            if after_state in queue_flow:
                queue_flow[after_state] += 1
            if row["action"] == "invalidate":
                queue_flow["invalidated"] += 1
        presented_cohort = {
            row["entity_id"]
            for row in queue_audits
            if row.get("after", {}).get("state") == "presented"
        }
        accepted_presented_cohort = {
            row["entity_id"]
            for row in queue_audits
            if row["entity_id"] in presented_cohort
            and row.get("after", {}).get("state") == "accepted"
        }

        brief_flow: dict[str, dict[str, int]] = {
            brief_type: {
                conclusion: 0
                for conclusion in ("no_action", "action", "review_required", "insufficient_evidence")
            }
            for brief_type in ("daily", "weekly", "monthly")
        }
        for row in brief_rows:
            brief_flow[row["brief_type"]][row["conclusion"]] += 1

        outputs = {
            "period": {"start": start_text, "end": end_text},
            "opportunity_flow": opportunity_flow,
            "decision_flow": queue_flow,
            "brief_flow": brief_flow,
            "cohorts": {
                "opportunities_created": len(opportunity_cohort),
                "created_opportunities_qualified_by_period_end": len(qualified_cohort),
                "created_opportunities_actionable_by_period_end": len(actionable_cohort),
                "queues_presented": len(presented_cohort),
                "presented_queues_accepted_by_period_end": len(accepted_presented_cohort),
            },
            "rates": {
                "created_cohort_qualified_by_period_end": self._rate(
                    len(qualified_cohort), len(opportunity_cohort)
                ),
                "created_cohort_actionable_by_period_end": self._rate(
                    len(actionable_cohort), len(opportunity_cohort)
                ),
                "presented_cohort_accepted_by_period_end": self._rate(
                    len(accepted_presented_cohort), len(presented_cohort)
                ),
            },
            "coverage": {
                "portfolio_return": {
                    "status": "insufficient_evidence",
                    "reason": "requires cash-flow-aware portfolio valuations for the full period",
                },
                "benchmark_return": {
                    "status": "insufficient_evidence",
                    "reason": "requires a frozen benchmark series matching the Program definition",
                },
                "user_time": {
                    "status": "insufficient_evidence",
                    "reason": "no Program-scoped user-time ledger exists",
                },
                "token_and_data_cost": {
                    "status": "insufficient_evidence",
                    "reason": "model and data costs are not yet attributed to this Program",
                },
            },
        }
        calculation = self.c.financial._record(
            "v5_program_operating_metrics",
            "Deterministic InvestmentProgram operating-flow metrics",
            end_text,
            {
                "program_id": program["id"],
                "program_revision_id": program["current_revision_id"],
                "period_start": start_text,
                "period_end": end_text,
                "opportunity_transition_ids": [row["id"] for row in opportunity_rows],
                "queue_item_ids": [row["id"] for row in queue_rows],
                "queue_audit_ids": [row["id"] for row in queue_audits],
                "brief_ids": [row["id"] for row in brief_rows],
            },
            {
                "period_boundary": "half-open [start,end)",
                "rates": "same-period created/presented cohorts observed through period_end; null when denominator is zero",
            },
            {
                "flow_count": "count immutable transition or audit records within the period",
                "rate": "numerator / denominator rounded half-up to 4 decimals",
            },
            outputs,
            [
                "cohort_rates_are_right_censored_within_the_selected_period",
                *[
                key
                for key, item in outputs["coverage"].items()
                if item["status"] == "insufficient_evidence"
                ],
            ],
        )
        return {**outputs, "calculation_id": calculation["id"]}

    def scorecard_publish(
        self,
        *,
        period_start: str,
        period_end: str,
        metrics: list[dict[str, Any]],
        comparisons: list[dict[str, Any]],
        source_refs: list[str],
        caveats: list[str],
        program_id: str | None = None,
        actor: str = "primary-codex",
    ) -> dict[str, Any]:
        self.c.jobs.feature_require("v5_operating_system")
        program = self._require_active_program(program_id)
        start, end = parse(period_start), parse(period_end)
        if end <= start:
            raise CompanionError("scorecard period_end must be after period_start")
        if end > utc_now() + timedelta(seconds=5):
            raise CompanionError("scorecard period_end cannot be in the future")
        if not isinstance(metrics, list):
            raise CompanionError("scorecard metrics must be a list")
        resolved: list[dict[str, Any]] = []
        names: set[str] = set()
        for metric in metrics:
            if not isinstance(metric, dict) or set(metric) != {"name", "calculation_id", "output_path"}:
                raise CompanionError("each scorecard metric must contain exactly name, calculation_id, output_path")
            name = self._text(metric["name"], "scorecard metric name")
            if name in names:
                raise CompanionError("scorecard metric names must be unique")
            names.add(name)
            calculation = self.c.financial.calculation_get(metric["calculation_id"])
            calculation_as_of = parse(calculation["as_of"])
            if calculation_as_of < start or calculation_as_of > end:
                raise CompanionError("scorecard Calculation as_of must fall within the scorecard period")
            value = self._resolve_output_path(calculation["outputs"], metric["output_path"])
            resolved.append(
                {
                    "name": name,
                    "calculation_id": calculation["id"],
                    "output_path": metric["output_path"],
                    "value": value,
                    "as_of": calculation["as_of"],
                    "engine_version": calculation["engine_version"],
                }
            )
        if not isinstance(comparisons, list):
            raise CompanionError("scorecard comparisons must be a list")
        normalized_comparisons = []
        for comparison in comparisons:
            if not isinstance(comparison, dict) or set(comparison) != {"label", "left_metric", "right_metric", "interpretation"}:
                raise CompanionError("scorecard comparison has an invalid shape")
            if comparison["left_metric"] not in names or comparison["right_metric"] not in names:
                raise CompanionError("scorecard comparison must reference resolved metric names")
            normalized_comparisons.append(
                {
                    "label": self._text(comparison["label"], "comparison label"),
                    "left_metric": comparison["left_metric"],
                    "right_metric": comparison["right_metric"],
                    "interpretation": self._text(comparison["interpretation"], "comparison interpretation"),
                }
            )
        refs = self._validate_evidence_refs(source_refs, allow_empty=not metrics)
        caveats = self._string_list(caveats, "scorecard caveats", allow_empty=True)
        status = "ready" if resolved else "insufficient_evidence"
        if status == "insufficient_evidence" and normalized_comparisons:
            raise CompanionError("insufficient-evidence scorecard cannot contain comparisons")
        content_hash = digest(
            "investment-companion.program-scorecard/v1",
            program["current_revision_id"],
            iso(start),
            iso(end),
            resolved,
            normalized_comparisons,
            refs,
            caveats,
        )
        with self.db.connect() as con:
            existing = row_dict(con.execute("SELECT * FROM program_scorecards WHERE content_hash=?", (content_hash,)).fetchone())
            previous = row_dict(
                con.execute(
                    "SELECT * FROM program_scorecards WHERE program_id=? AND period_start=? AND period_end=? ORDER BY revision DESC LIMIT 1",
                    (program["id"], iso(start), iso(end)),
                ).fetchone()
            )
        if existing:
            return existing
        revision_number = (previous["revision"] + 1) if previous else 1
        scorecard_id, now = new_id("scorecard"), iso()
        with self.db.transaction() as con:
            con.execute(
                "INSERT INTO program_scorecards(id,program_id,program_revision_id,period_start,period_end,revision,status,metrics_json,comparisons_json,source_refs_json,caveats_json,content_hash,supersedes,created_at) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    scorecard_id,
                    program["id"],
                    program["current_revision_id"],
                    iso(start),
                    iso(end),
                    revision_number,
                    status,
                    canonical(resolved),
                    canonical(normalized_comparisons),
                    canonical(refs),
                    canonical(caveats),
                    content_hash,
                    previous["id"] if previous else None,
                    now,
                ),
            )
            self.c._audit(
                con,
                actor,
                "publish",
                "program_scorecard",
                scorecard_id,
                after={"status": status, "period_start": iso(start), "period_end": iso(end), "revision": revision_number},
            )
        return self.scorecard_get(scorecard_id)

    def scorecard_get(self, scorecard_id: str) -> dict[str, Any]:
        with self.db.connect() as con:
            item = row_dict(con.execute("SELECT * FROM program_scorecards WHERE id=?", (scorecard_id,)).fetchone())
        if not item:
            raise CompanionError(f"program scorecard not found: {scorecard_id}")
        for metric in item["metrics"]:
            calculation = self.c.financial.calculation_get(metric["calculation_id"])
            current = self._resolve_output_path(calculation["outputs"], metric["output_path"])
            if canonical(current) != canonical(metric["value"]):
                raise CompanionError("program scorecard metric differs from its Calculation record")
        return item

    def scorecard_list(self, *, program_id: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
        if limit <= 0 or limit > 500:
            raise CompanionError("program scorecard limit must be within 1..500")
        query, params = (
            "SELECT s.* FROM program_scorecards s WHERE NOT EXISTS "
            "(SELECT 1 FROM program_scorecards newer WHERE newer.supersedes=s.id)"
        ), []
        if program_id:
            query += " AND s.program_id=?"
            params.append(program_id)
        query += " ORDER BY s.period_end DESC,s.revision DESC LIMIT ?"
        params.append(limit)
        with self.db.connect() as con:
            return rows_dict(con.execute(query, params).fetchall())

    def status(self) -> dict[str, Any]:
        self._expire_queue()
        current = self.program_current()
        alignment = self._program_alignment(current) if current else None
        with self.db.connect() as con:
            program_counts = {
                row["status"]: row["count"]
                for row in con.execute("SELECT status,COUNT(*) AS count FROM investment_programs GROUP BY status")
            }
            funnel = {
                row["stage"]: row["count"]
                for row in con.execute(
                    "SELECT stage,COUNT(*) AS count FROM opportunities WHERE status='active' GROUP BY stage"
                )
            }
            queue = {
                row["state"]: row["count"]
                for row in con.execute("SELECT state,COUNT(*) AS count FROM decision_queue_items GROUP BY state")
            }
        return {
            "schema_version": SCHEMA_VERSION,
            "current_program_id": current["id"] if current else None,
            "program_alignment": alignment,
            "programs": program_counts,
            "opportunity_funnel": {stage: funnel.get(stage, 0) for stage in STAGE_ORDER},
            "decision_queue": queue,
            "claims": {
                "automatic_trading": False,
                "profit_guarantee": False,
                "high_win_rate_guarantee": False,
            },
        }

    def today(self) -> dict[str, Any]:
        self._expire_queue()
        current = self.program_current()
        if not current:
            missing = []
            for context_type in ("investor", "mandate", "attention"):
                if not self.c.cognition.context_current(context_type):
                    missing.append(f"confirm_{context_type}")
            missing.append("create_and_confirm_investment_program")
            return {
                "mode": "setup_required",
                "next_actions": missing,
                "message": "尚未启用一份经你确认的投资经营计划。",
                "claims": {"automatic_trading": False, "profit_guarantee": False},
            }
        alignment = self._program_alignment(current)
        if not alignment["aligned"]:
            return {
                "mode": "review_required",
                "message": "当前投资经营计划引用的个人约束或账户状态已经变化，必须先修订并重新确认。",
                "program": {
                    "id": current["id"],
                    "name": current["name"],
                    "revision_id": current["current_revision_id"],
                },
                "program_alignment": alignment,
                "next_actions": ["revise_and_reconfirm_investment_program"],
                "claims": {"automatic_trading": False, "profit_guarantee": False},
            }
        queue_items = self.queue_list(program_id=current["id"], limit=20)
        queue = [
            item
            for item in queue_items
            if item["state"] in {"ready", "presented", "accepted"}
        ]
        snoozed = [item for item in queue_items if item["state"] == "snoozed"]
        cards: list[dict[str, Any]] = []
        valid_queue: list[dict[str, Any]] = []
        invalidated_queue: list[dict[str, Any]] = []
        for item in queue:
            try:
                card = self.queue_card(item["id"])
                valid_queue.append(item)
                if len(cards) < 3:
                    cards.append(card)
            except CompanionError as exc:
                invalidated_queue.append({"queue_id": item["id"], "error": str(exc)})
        queue = valid_queue
        briefs = {
            brief_type: (self.brief_list(program_id=current["id"], brief_type=brief_type, limit=1) or [None])[0]
            for brief_type in ("daily", "weekly", "monthly")
        }
        latest_daily = briefs["daily"]
        if queue:
            mode = "action"
            accepted_count = sum(item["state"] == "accepted" for item in queue)
            if accepted_count:
                message = (
                    f"有 {accepted_count} 项已接受但仍等待你手工执行或反馈，"
                    f"另有 {len(queue) - accepted_count} 项等待判断。"
                )
            else:
                message = f"有 {len(queue)} 项仍在有效期内、等待你判断的人工操作建议。"
        elif invalidated_queue:
            mode = "review_required"
            message = f"有 {len(invalidated_queue)} 项旧行动已因依据变化而失效，需要重新研究或形成新 Decision。"
        elif snoozed:
            mode = "review_required"
            next_resume = min(item.get("snoozed_until") or item["valid_until"] for item in snoozed)
            message = f"有 {len(snoozed)} 项行动被你延后，将在 {next_resume} 后重新进入判断队列。"
        elif (
            latest_daily
            and latest_daily["program_revision_id"] == current["current_revision_id"]
            and latest_daily["conclusion"] == "no_action"
            and parse(latest_daily["payload"]["next_check_at"]) > utc_now()
        ):
            mode = "no_action"
            message = "最新一轮检查没有产生达到行动门槛的建议。"
        else:
            mode = "review_required"
            message = "当前没有有效操作建议，但尚缺本期经过证据闭环的日结论。"
        return {
            "mode": mode,
            "message": message,
            "program": {
                "id": current["id"],
                "name": current["name"],
                "revision_id": current["current_revision_id"],
            },
            "action_cards": cards,
            "invalidated_queue": invalidated_queue,
            "deferred_queue": [
                {"queue_id": item["id"], "snoozed_until": item.get("snoozed_until")}
                for item in snoozed
            ],
            "latest_briefs": briefs,
            "claims": {
                "automatic_trading": False,
                "profit_guarantee": False,
                "no_action_is_a_valid_outcome": True,
            },
        }
