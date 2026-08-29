from __future__ import annotations

from datetime import timedelta
from typing import Any

from ..db import row_dict, rows_dict
from ..foundation import CompanionError, canonical, digest, new_id
from ..timeutil import iso, parse, utc_now


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


class InvestmentProgramService:
    """Investment policy/program lifecycle separated from operating projections."""

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
                self.c.audit.record(
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
                    self.c.audit.record(
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
            self.c.audit.record(
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
            raise CompanionError(
                "investment_program.version_conflict: InvestmentProgram version conflict"
            )
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
                raise CompanionError(
                    "investment_program.version_conflict: InvestmentProgram version conflict"
                )
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
            self.c.audit.record(
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
            self.c.audit.record(
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
        expected_version: int | None = None,
        reason: str,
        actor: str = "primary-codex",
    ) -> dict[str, Any]:
        if status not in {"active", "paused", "archived"}:
            raise CompanionError("InvestmentProgram status must be active, paused, or archived")
        self._expire_trial_programs()
        reason = self._text(reason, "InvestmentProgram status reason")
        program = self.program_get(program_id)
        if expected_version is not None and program["version"] != expected_version:
            raise CompanionError(
                "investment_program.version_conflict: InvestmentProgram version conflict"
            )
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
            changed = con.execute(
                "UPDATE investment_programs SET status=?,updated_at=?,closed_at=?,version=version+1 "
                "WHERE id=? AND version=?",
                (
                    status,
                    now,
                    now if status == "archived" else None,
                    program_id,
                    program["version"],
                ),
            ).rowcount
            if changed != 1:
                raise CompanionError(
                    "investment_program.version_conflict: InvestmentProgram version conflict"
                )
            self.c.audit.record(
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
            raise CompanionError(
                "investment_program.not_active: no active InvestmentProgram"
            )
        if program_id and current["id"] != program_id:
            raise CompanionError(
                "investment_program.not_active: operation must use the active "
                "InvestmentProgram"
            )
        alignment = self._program_alignment(current)
        if not alignment["aligned"]:
            raise CompanionError(
                "active InvestmentProgram requires review: " + "; ".join(alignment["issues"])
            )
        return current
