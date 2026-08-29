from __future__ import annotations

from typing import Any

from ..db import row_dict, rows_dict
from ..foundation import CompanionError, canonical, digest, new_id
from ..timeutil import iso, parse, utc_now
from .actionability_lifecycle import ActionabilityLifecycleMixin
from .opportunity_funding import OpportunityFundingConditionMixin
from .programs import InvestmentProgramService


STAGE_ORDER = {"observed": 0, "researching": 1, "qualified": 2, "actionable": 3}


class PortfolioDecisionService(
    OpportunityFundingConditionMixin,
    ActionabilityLifecycleMixin,
    InvestmentProgramService,
):
    """Opportunity qualification and human DecisionQueue lifecycle."""

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
        gate = self.c.actionability.validate_decision(decision_revision_id)
        return gate["revision"], gate["decision"]

    def _validate_qualification(
        self,
        qualification: Any,
        stage: str,
        opportunity: dict[str, Any],
        evidence_refs: list[str],
    ) -> dict[str, Any]:
        return self.c.actionability.validate_qualification(
            qualification, stage, opportunity, evidence_refs
        )

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
                self.c.audit.record(
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
        item = self._add_funding_condition_projection(item)
        validation = self.c.actionability.qualification_status(item)
        item["research_validation"] = validation
        item["evidence_band"] = (
            validation["current_status"]
            if validation
            else {
                "observed": "unassessed",
                "researching": "incomplete",
                "qualified": "legacy_unverified",
                "actionable": "legacy_unverified",
            }[item["stage"]]
        )
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
            opportunities = rows_dict(con.execute(query, params).fetchall())
        effective_at = iso()
        return [
            self._add_funding_condition_projection(item, as_of=effective_at)
            for item in opportunities
        ]

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
            next_qualification = self._validate_qualification(
                qualification, to_stage, item, refs
            )
        elif qualification is not None:
            raise CompanionError("qualification may be supplied only when entering qualified or actionable")
        if to_stage == "actionable" and to_status == "active":
            if item.get("funding_condition") is not None:
                raise CompanionError(
                    "Opportunity cannot become actionable while its Funding Condition is current; "
                    "confirm the funding fact and rerun Portfolio Qualification, Risk Gate, and Action Plan"
                )
            if not decision_revision_id:
                raise CompanionError("actionable Opportunity requires decision_revision_id")
            self.c.actionability.validate_actionable_transition(
                item, next_qualification, refs, decision_revision_id
            )
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
                self.c.audit.record(
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
                self.c.audit.record(
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
                self.c.audit.record(
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
        gate = self.c.actionability.validate_action_card(
            opportunity=opportunity,
            decision_revision_id=decision_revision_id,
            stage="enqueue",
        )
        revision = gate["revision"]
        bounded_limit = None
        if revision.get("metadata", {}).get("action_tier") == "bounded":
            policy = program["current_revision"]["content"].get("risk_budget", {}).get(
                "bounded_action", {}
            )
            bounded_limit = policy.get("max_active_bounded_actions")
            if (
                isinstance(bounded_limit, bool)
                or not isinstance(bounded_limit, int)
                or bounded_limit <= 0
            ):
                raise CompanionError("bounded action policy has no valid active-action limit")
        qualification_id = (opportunity.get("qualification") or {}).get(
            "validation_calculation_id"
        )
        if gate["contract"] == "1" and (
            revision["metadata"].get("research_validation_calculation_id") != qualification_id
        ):
            raise CompanionError("DecisionQueue Opportunity and Decision validation lineage differ")
        if gate["contract"] == "4" and not manual_action_spec_id:
            raise CompanionError("V4 DecisionQueue item requires a ManualActionSpec")
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
        if bounded_limit is not None:
            with self.db.connect() as con:
                active_bounded = con.execute(
                    "SELECT COUNT(*) FROM decision_queue_items q "
                    "JOIN cognitive_revisions r ON r.id=q.decision_revision_id "
                    "WHERE q.program_id=? AND q.state IN ('ready','presented','snoozed','accepted') "
                    "AND json_extract(r.metadata_json,'$.action_tier')='bounded' "
                    "AND NOT EXISTS (SELECT 1 FROM broker_execution_plans p WHERE p.queue_id=q.id "
                    "AND (p.status IN ('reconciled','cancelled') OR "
                    "(p.status IN ('terminated','expired') AND NOT EXISTS ("
                    "SELECT 1 FROM broker_managed_orders o WHERE o.plan_id=p.id "
                    "AND o.status IN ('triggered','submitted','partially_filled','unknown')"
                    "))))",
                    (program["id"],),
                ).fetchone()[0]
            if active_bounded >= bounded_limit:
                raise CompanionError("bounded action queue limit reached")
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
                if bounded_limit is not None:
                    active_bounded = con.execute(
                        "SELECT COUNT(*) FROM decision_queue_items q "
                        "JOIN cognitive_revisions r ON r.id=q.decision_revision_id "
                        "WHERE q.program_id=? AND q.state IN ('ready','presented','snoozed','accepted') "
                        "AND json_extract(r.metadata_json,'$.action_tier')='bounded' "
                        "AND NOT EXISTS (SELECT 1 FROM broker_execution_plans p WHERE p.queue_id=q.id "
                        "AND (p.status IN ('reconciled','cancelled') OR "
                        "(p.status IN ('terminated','expired') AND NOT EXISTS ("
                        "SELECT 1 FROM broker_managed_orders o WHERE o.plan_id=p.id "
                        "AND o.status IN ('triggered','submitted','partially_filled','unknown')"
                        "))))",
                        (program["id"],),
                    ).fetchone()[0]
                    if active_bounded >= bounded_limit:
                        raise CompanionError("bounded action queue limit reached")
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
                self.c.audit.record(
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
        return self._queue_item_projection(item)

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
            return [
                self._queue_item_projection(item)
                for item in rows_dict(con.execute(query, params).fetchall())
            ]

    def queue_respond(
        self,
        queue_id: str,
        *,
        state: str,
        reason: str | None = None,
        snoozed_until: str | None = None,
        attention_decision_id: str | None = None,
        user_confirmation_ref: str | None = None,
        actor: str = "primary-codex",
    ) -> dict[str, Any]:
        self.c.jobs.feature_require("v5_operating_system")
        item = self.queue_get(queue_id)
        self._require_active_program(item["program_id"])
        if state not in {"presented", "snoozed", "accepted", "rejected", "closed"}:
            raise CompanionError("invalid DecisionQueue response state")
        if user_confirmation_ref is not None:
            user_confirmation_ref = self._text(user_confirmation_ref, "DecisionQueue user_confirmation_ref")
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
            if state in {"presented", "accepted"} and not (
                state == "accepted" and self.queue_execution_started(queue_id)
            ):
                self.queue_card(
                    queue_id,
                    actionability_stage=(
                        "present" if state == "presented" else "accept"
                    ),
                )
            return {**item, "user_confirmation_ref": user_confirmation_ref} if user_confirmation_ref else item
        if state not in transitions.get(item["state"], set()):
            raise CompanionError(f"invalid DecisionQueue transition: {item['state']} -> {state}")
        attention = None
        card = None
        if state in {"presented", "accepted"}:
            card = self.queue_card(
                queue_id,
                actionability_stage=("present" if state == "presented" else "accept"),
            )
            if not card["executable_now"]:
                raise CompanionError(f"Action Card is not executable: {card['blocking_reasons']}")
        if state == "presented":
            if not attention_decision_id:
                raise CompanionError("presented DecisionQueue item requires attention_decision_id")
            attention = self.c.attention.get(attention_decision_id)
            if attention["action"] != "notify_now" or attention["status"] != "delivered":
                raise CompanionError("DecisionQueue presentation requires a delivered notify_now AttentionDecision")
            if queue_id not in attention.get("evidence", []):
                raise CompanionError("DecisionQueue AttentionDecision must cite the queue item as evidence")
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
            self.c.audit.record(
                con,
                actor,
                "respond",
                "decision_queue_item",
                queue_id,
                before={"state": item["state"]},
                after={
                    "state": state,
                    "attention_decision_id": (
                        attention["id"] if attention else item.get("attention_decision_id")
                    ),
                    "user_confirmation_ref": user_confirmation_ref,
                },
                reason=reason,
            )
        result = self.queue_get(queue_id)
        if user_confirmation_ref:
            result["user_confirmation_ref"] = user_confirmation_ref
        return result
