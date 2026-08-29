from __future__ import annotations

from datetime import timedelta
from decimal import Decimal, ROUND_HALF_UP
from typing import Any

from .foundation import CompanionError, canonical, digest, new_id
from .db import SCHEMA_VERSION, row_dict, rows_dict
from .timeutil import iso, parse, utc_now
from .application.portfolio_decisions import PortfolioDecisionService, STAGE_ORDER


class InvestmentOperatingSystem(PortfolioDecisionService):
    """User-facing operating loop above the existing ledgers and research runtime.

    This layer coordinates immutable references.  It deliberately does not own
    positions, prices, research results, Decisions, Executions, or performance
    arithmetic.
    """

    def __init__(self, companion):
        self.c = companion
        self.db = companion.db

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
        production_health=self.c.production_health()
        if conclusion=="no_action" and not production_health["ok"]:
            raise CompanionError("system_degraded cannot be published as no_action")
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
        if conclusion == "no_action":
            self.require_no_action_qualification_resolved(program_id=program["id"])
        if conclusion == "no_action" and (queue_items or active_queue):
            raise CompanionError(
                "investment_brief.unresolved_obligations: no_action brief conflicts "
                "with an active DecisionQueue item"
            )
        if conclusion != "action" and queue_items:
            raise CompanionError("only an action brief may reference DecisionQueue items")
        execution_snapshot = self.c.briefing.freeze_for_brief(program_id=program["id"], brief_type=brief_type, as_of=as_of)
        payload, refs = {**payload, "execution_snapshot": execution_snapshot}, list(dict.fromkeys([*refs, execution_snapshot["calculation_id"]]))
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
                self.require_no_action_qualification_resolved(
                    program_id=program["id"], connection=con
                )
                conflict = con.execute(
                    "SELECT id FROM decision_queue_items WHERE program_id=? "
                    "AND state IN ('ready','presented','snoozed','accepted') AND valid_until>? LIMIT 1",
                    (program["id"], iso()),
                ).fetchone()
                if conflict:
                    raise CompanionError(
                        "investment_brief.unresolved_obligations: no_action brief "
                        "conflicts with an active DecisionQueue item"
                    )
                unfinished = con.execute(
                    "SELECT id FROM research_work_items WHERE program_id=? "
                    "AND status IN ('queued','leased','waiting','monitoring') LIMIT 1",
                    (program["id"],),
                ).fetchone()
                if unfinished:
                    raise CompanionError(
                        "investment_brief.unresolved_obligations: no_action brief "
                        "conflicts with unfinished research work"
                    )
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
            self.c.audit.record(
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
            self.c.audit.record(
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
            raise CompanionError(
                "investment_brief.calculation_lineage_required: scorecard "
                "output_path must start with outputs."
            )
        value = outputs
        for part in path.split(".")[1:]:
            if isinstance(value, dict) and part in value:
                value = value[part]
            elif isinstance(value, list) and part.isdigit() and int(part) < len(value):
                value = value[int(part)]
            else:
                raise CompanionError(
                    "investment_brief.calculation_lineage_required: scorecard "
                    f"output_path not found: {path}"
                )
        if isinstance(value, (dict, list)):
            raise CompanionError(
                "investment_brief.calculation_lineage_required: scorecard metric "
                "must resolve to a scalar Calculation output"
            )
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
        evaluation = self.c.program_evaluation.resolve(program, start_text, end_text)

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
            "coverage": evaluation["coverage"],
        }
        calculation = self.c.financial.calculation_record(
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
                "performance_calculation_ids": evaluation["performance_calculation_ids"],
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
            try:
                calculation = self.c.financial.calculation_get(
                    metric["calculation_id"]
                )
            except CompanionError as error:
                raise CompanionError(
                    "investment_brief.calculation_lineage_required: scorecard "
                    f"Calculation is unavailable: {error}"
                ) from error
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
            self.c.audit.record(
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
        actionability = self.revalidate_actionable_queue(queue, program_id=current["id"], max_cards=3)
        queue = actionability["queue"]
        cards = actionability["cards"]
        invalidated_queue = actionability["invalid"]
        briefs = {
            brief_type: (self.brief_list(program_id=current["id"], brief_type=brief_type, limit=1) or [None])[0]
            for brief_type in ("daily", "weekly", "monthly")
        }
        latest_daily = briefs["daily"]
        execution_summary = self.c.briefing.projection(program_id=current["id"], as_of=iso(), since=latest_daily["created_at"] if latest_daily else None)
        execution_signal = self.c.briefing.today_signal(execution_summary)
        research_work = self.c.research_work.summary(program_id=current["id"])
        production_health=self.c.production_health()
        if not production_health["ok"]:
            mode="system_degraded";message="关键运行或研究流水线异常，系统当前无法形成可信的行动/不行动判断。"
            if execution_signal:message+=f" 同时存在独立的执行事项：{execution_signal['message']}"
        elif queue:
            mode = "action"
            accepted_count = sum(item["state"] == "accepted" for item in queue)
            if execution_signal and execution_signal["mode"] == "action":
                message = execution_signal["message"]
            elif accepted_count:
                message = (
                    f"有 {accepted_count} 项已接受但仍等待你手工执行或反馈，"
                    f"另有 {len(queue) - accepted_count} 项等待判断。"
                )
            else:
                message = f"有 {len(queue)} 项仍在有效期内、等待你判断的人工操作建议。"
        elif invalidated_queue:
            mode = "review_required"
            message = f"有 {len(invalidated_queue)} 项旧行动因组合事实资格不足或依据变化而失效；这表示需要重新核实事实与形成新 Decision，不表示 no_action。"
        elif snoozed:
            mode = "review_required"
            next_resume = min(item.get("snoozed_until") or item["valid_until"] for item in snoozed)
            message = f"有 {len(snoozed)} 项行动被你延后，将在 {next_resume} 后重新进入判断队列。"
        elif execution_signal:
            mode, message = execution_signal["mode"], execution_signal["message"]
        elif research_work["open"]:
            mode = "review_required"
            overdue = research_work["overdue"]
            message = (
                f"研究队列有 {research_work['open']} 项待完成，其中 {overdue} 项已逾期；"
                "候选尚未完成分流或完整研究，不能把当前状态写成不行动。"
            )
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
            "portfolio_fact_sufficiency": {
                "status": "insufficient_for_action" if invalidated_queue else "no_unresolved_actionability_gap",
                "no_action_inferred": False,
            },
            "deferred_queue": [
                {"queue_id": item["id"], "snoozed_until": item.get("snoozed_until")}
                for item in snoozed
            ],
            "execution_summary": execution_summary,
            "production_health": production_health,
            "urgent_execution_review": execution_signal if not production_health["ok"] else None,
            "research_work": research_work,
            "latest_briefs": briefs,
            "claims": {
                "automatic_trading": False,
                "profit_guarantee": False,
                "no_action_is_a_valid_outcome": True,
            },
        }
