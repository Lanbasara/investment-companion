from __future__ import annotations

from typing import Any

from ..actionability import PORTFOLIO_QUALIFICATION_REASON_PREFIX
from ..db import rows_dict
from ..foundation import CompanionError
from ..timeutil import iso


class ActionabilityLifecycleMixin:
    """Project and persist Action Card validation without owning its facts."""

    @staticmethod
    def _queue_item_projection(item: dict[str, Any]) -> dict[str, Any]:
        reason = item.get("response_reason")
        if isinstance(reason, str):
            reason_code = reason.partition(":")[0]
            if reason_code.startswith("actionability."):
                return {
                    **item,
                    "reason_code": reason_code,
                    "no_action_inferred": False,
                }
        return item

    def qualification_gap_items(
        self, *, program_id: str
    ) -> list[dict[str, Any]]:
        """Return unresolved fact-sufficiency failures without inferring no_action."""

        with self.db.connect() as con:
            rows = rows_dict(
                con.execute(
                    "SELECT q.* FROM decision_queue_items q "
                    "JOIN opportunities o ON o.id=q.opportunity_id "
                    "WHERE q.program_id=? AND q.state='expired' "
                    "AND q.response_reason LIKE ? "
                    "AND o.status='active' AND o.stage='actionable' "
                    "AND o.decision_revision_id=q.decision_revision_id "
                    "ORDER BY q.updated_at DESC,q.id DESC",
                    (program_id, f"{PORTFOLIO_QUALIFICATION_REASON_PREFIX}%"),
                ).fetchall()
            )
        return [self._queue_item_projection(item) for item in rows]

    def require_no_action_qualification_resolved(
        self, *, program_id: str, connection: Any | None = None
    ) -> None:
        query = (
            "SELECT q.id FROM decision_queue_items q "
            "JOIN opportunities o ON o.id=q.opportunity_id "
            "WHERE q.program_id=? AND q.state='expired' "
            "AND q.response_reason LIKE ? "
            "AND o.status='active' AND o.stage='actionable' "
            "AND o.decision_revision_id=q.decision_revision_id LIMIT 1"
        )
        if connection is None:
            with self.db.connect() as con:
                gap = con.execute(
                    query,
                    (program_id, f"{PORTFOLIO_QUALIFICATION_REASON_PREFIX}%"),
                ).fetchone()
        else:
            gap = connection.execute(
                query,
                (program_id, f"{PORTFOLIO_QUALIFICATION_REASON_PREFIX}%"),
            ).fetchone()
        if gap:
            raise CompanionError(
                "investment_brief.portfolio_qualification_unresolved: "
                "insufficient portfolio facts cannot be published as no_action"
            )

    def queue_execution_started(self, queue_id: str) -> bool:
        """Return whether an accepted card has crossed into its Execution lifecycle."""

        with self.db.connect() as con:
            execution = con.execute(
                "SELECT 1 FROM executions "
                "WHERE json_extract(details_json,'$.queue_id')=? LIMIT 1",
                (queue_id,),
            ).fetchone()
            strategy = con.execute(
                "SELECT 1 FROM broker_execution_plans WHERE queue_id=? "
                "AND status IN ('active','sleeping','termination_pending','exception') "
                "LIMIT 1",
                (queue_id,),
            ).fetchone()
        return bool(execution or strategy)

    def revalidate_actionable_queue(
        self,
        items: list[dict[str, Any]],
        *,
        program_id: str,
        max_cards: int | None,
    ) -> dict[str, list[dict[str, Any]]]:
        valid, cards, invalid = [], [], []
        for item in items:
            if item["state"] == "accepted" and self.queue_execution_started(
                item["id"]
            ):
                valid.append(item)
                continue
            try:
                card = self.queue_card(item["id"])
                valid.append(item)
                if max_cards is None or len(cards) < max_cards:
                    cards.append(card)
            except CompanionError as exc:
                invalid.append(
                    {
                        "queue_id": item["id"],
                        "error": str(exc),
                        "reason_code": getattr(exc, "reason_code", None),
                        "no_action_inferred": False,
                    }
                )
        invalid_ids = {item["queue_id"] for item in invalid}
        for item in self.qualification_gap_items(program_id=program_id):
            if item["id"] not in invalid_ids:
                invalid.append(
                    {
                        "queue_id": item["id"],
                        "error": item["response_reason"],
                        "reason_code": item.get("reason_code"),
                        "no_action_inferred": False,
                    }
                )
        return {"queue": valid, "cards": cards, "invalid": invalid}

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
                self.c.audit.record(
                    con,
                    "system",
                    "invalidate",
                    "decision_queue_item",
                    item["id"],
                    before={"state": item["state"]},
                    after={"state": "expired"},
                    reason=reason,
                )

    def queue_card(
        self, queue_id: str, *, actionability_stage: str = "project"
    ) -> dict[str, Any]:
        item = self.queue_get(queue_id)
        if item["state"] not in {"ready", "presented", "snoozed", "accepted"}:
            raise CompanionError(f"DecisionQueue item is not active: {item['state']}")
        self._require_active_program(item["program_id"])
        opportunity = self.opportunity_get(item["opportunity_id"])
        try:
            gate = self.c.actionability.validate_action_card(
                opportunity=opportunity,
                decision_revision_id=item["decision_revision_id"],
                stage=actionability_stage,
            )
        except CompanionError as exc:
            reason = (
                str(exc)
                if getattr(exc, "reason_code", None)
                else f"Decision invalidated: {exc}"
            )
            self._invalidate_queue_item(item, reason)
            raise
        revision, metadata = gate["revision"], gate["revision"]["metadata"]
        action_payload, executable, reasons = None, None, []
        research_validation, risk_gate = gate["research_validation"], None
        if item.get("manual_action_spec_id"):
            validation = self.c.cognition.manual_action_validate(
                item["manual_action_spec_id"]
            )
            action = validation["spec"]
            if action["status"] not in {"draft", "presented", "accepted"}:
                reason = (
                    "ManualActionSpec invalidated: "
                    f"{validation['reasons'] or [action['status']]}"
                )
                self._invalidate_queue_item(item, reason)
                raise CompanionError(reason)
            action_payload = {
                key: action["spec"].get(key)
                for key in (
                    "account_id",
                    "asset_id",
                    "side",
                    "quantity",
                    "price_range",
                    "priority",
                    "alternatives",
                )
            }
            executable, reasons = validation["executable"], validation["reasons"]
        else:
            try:
                current = self.c.actionability.revalidate_generic_action(revision["id"])
            except CompanionError as exc:
                self._invalidate_queue_item(
                    item, f"Action Card validation failed: {exc}"
                )
                raise
            action_payload = current["action"]
            executable, reasons = current["executable"], current["reasons"]
            research_validation = current["research_validation"]
            risk_gate = {
                "frozen_calculation_id": current["frozen_risk_calculation_id"],
                "current_calculation_id": current["risk_calculation_id"],
                "market_snapshot_id": current["market_snapshot_id"],
                "status": "pass" if executable else "blocked",
            }
            if not executable:
                reason = f"Action Card invalidated by current Risk Gate: {reasons}"
                self._invalidate_queue_item(item, reason)
                raise CompanionError(reason)
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
            "research_validation": research_validation,
            "risk_gate": risk_gate,
            "portfolio_qualification": (
                gate["portfolio_qualification"].stable_projection()
                if gate["portfolio_qualification"]
                else None
            ),
            "actionability": gate["actionability"],
            "no_action_inferred": False,
            "invalidators": metadata["invalidators"],
            "no_action_alternative": metadata["no_action"],
            "source_refs": metadata.get("source_refs", []),
            "human_execution_only": True,
            "execution_created": False,
            "guarantees": {"profit": False, "high_win_rate": False},
        }
