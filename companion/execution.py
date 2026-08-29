from __future__ import annotations

from typing import Any

from .db import row_dict, rows_dict
from .financial import dec, dtext
from .foundation import CompanionError
from .timeutil import iso, parse, utc_now


class ExecutionLifecycleService:
    """Track human orders and fills without confusing them with recommendations.

    Queue acceptance authorizes only preparation.  A broker order is recorded
    separately, reported fills remain pending Ledger facts, and only explicit
    confirmation lets those facts change the portfolio.
    """

    def __init__(self, companion):
        self.c = companion

    @staticmethod
    def _text(value: Any, label: str) -> str:
        if not isinstance(value, str) or not value.strip():
            raise CompanionError(f"{label} must be a non-empty string")
        return value.strip()

    def get(self, execution_id: str) -> dict[str, Any]:
        return self.c.cognition.execution_get(execution_id)

    def list(
        self,
        *,
        decision_revision_id: str | None = None,
        status: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        if limit <= 0 or limit > 500:
            raise CompanionError("Execution limit must be within 1..500")
        query, params = "SELECT * FROM executions WHERE 1=1", []
        if decision_revision_id:
            query += " AND decision_revision_id=?"
            params.append(decision_revision_id)
        if status:
            query += " AND status=?"
            params.append(status)
        query += " ORDER BY updated_at DESC,id DESC LIMIT ?"
        params.append(limit)
        with self.c.db.connect() as con:
            return rows_dict(con.execute(query, params).fetchall())

    def prepare_from_queue(
        self,
        *,
        queue_id: str,
        idempotency_key: str,
        actor: str = "primary-codex",
    ) -> dict[str, Any]:
        key = self._text(idempotency_key, "Execution idempotency_key")
        with self.c.db.connect() as con:
            existing = row_dict(
                con.execute(
                    "SELECT * FROM executions WHERE idempotency_key=?", (key,)
                ).fetchone()
            )
        if existing:
            if existing.get("details", {}).get("queue_id") != queue_id:
                raise CompanionError(
                    "Execution idempotency_key belongs to another Action Card"
                )
            return existing

        queue = self.c.operating.queue_get(queue_id)
        if queue["state"] != "accepted":
            raise CompanionError("Execution preparation requires an accepted Action Card")
        card = self.c.operating.queue_card(
            queue_id, actionability_stage="execution"
        )
        if not card["executable_now"]:
            raise CompanionError(
                f"Action Card is not currently executable: {card['blocking_reasons']}"
            )
        revision = self.c.cognition.revision_get(queue["decision_revision_id"])
        contract = str(revision["metadata"].get("decision_contract_version", ""))
        if contract == "4":
            if not queue.get("manual_action_spec_id"):
                raise CompanionError("V4 Execution requires a ManualActionSpec")
            execution = self.c.cognition.execution_create_from_action(
                queue["manual_action_spec_id"], key
            )
            return execution
        if contract != "1":
            raise CompanionError("Action Card uses an unsupported Execution contract")
        details = {
            "execution_contract_version": 1,
            "queue_id": queue_id,
            "action": card["action"],
            "decision_revision_id": revision["id"],
            "research_validation_calculation_id": card["research_validation"][
                "calculation_id"
            ],
            "frozen_risk_calculation_id": card["risk_gate"][
                "frozen_calculation_id"
            ],
            "prepared_risk_calculation_id": card["risk_gate"][
                "current_calculation_id"
            ],
            "prepared_market_snapshot_id": card["risk_gate"]["market_snapshot_id"],
            "valid_until": card["valid_until"],
            "human_execution_only": True,
        }
        execution = self.c.cognition.execution_create(
            revision["object_id"],
            details,
            decision_revision_id=revision["id"],
            idempotency_key=key,
            status="proposed",
        )
        with self.c.db.transaction() as con:
            self.c.audit.record(
                con,
                actor,
                "prepare",
                "execution",
                execution["id"],
                after={"status": "proposed", "queue_id": queue_id},
                reason="accepted Action Card prepared for human execution",
            )
        return execution

    def mark_ordered(
        self,
        *,
        execution_id: str,
        broker_order_ref: str,
        ordered_at: str,
        actor: str = "primary-codex",
    ) -> dict[str, Any]:
        reference = self._text(broker_order_ref, "broker_order_ref")
        occurred = parse(ordered_at)
        if occurred > utc_now():
            raise CompanionError("Execution ordered_at cannot be in the future")
        item = self.get(execution_id)
        deviations = []
        if occurred > parse(item["details"]["valid_until"]):
            deviations.append("order_after_action_validity")
        event = {
            "broker_order_ref": reference,
            "ordered_at": iso(occurred),
            "deviations": deviations,
        }
        prior = self._latest_audit(execution_id, "order_reported")
        if item["status"] == "ordered":
            if prior is not None and prior != event:
                raise CompanionError("ordered Execution already has another broker order report")
            if prior is None:
                with self.c.db.transaction() as con:
                    self.c.audit.record(
                        con,
                        actor,
                        "order_reported",
                        "execution",
                        execution_id,
                        after=event,
                        reason="recovered broker order audit after interrupted transition",
                    )
            return item
        if item["status"] not in {"proposed", "accepted"}:
            raise CompanionError(f"Execution cannot be ordered from {item['status']}")
        queue_id = item.get("details", {}).get("queue_id")
        if not queue_id:
            raise CompanionError("version-neutral Execution lacks its originating Action Card")
        queue = self.c.operating.queue_get(queue_id)
        if queue["decision_revision_id"] != item.get("decision_revision_id"):
            raise CompanionError("Execution and Action Card Decision lineage differ")
        if item["status"] == "proposed":
            self.c.cognition.execution_set_status(
                execution_id, "accepted", revalidate_action=False
            )
        ordered = self.c.cognition.execution_set_status(
            execution_id, "ordered", revalidate_action=False
        )
        with self.c.db.transaction() as con:
            self.c.audit.record(
                con,
                actor,
                "order_reported",
                "execution",
                execution_id,
                before={"status": item["status"]},
                after=event,
                reason="user reported a broker order; no fill assumed",
            )
        return ordered

    def report_fill(
        self,
        *,
        execution_id: str,
        occurred_at: str,
        quantity: Any,
        price: Any,
        fee: Any,
        source: str,
        external_id: str | None = None,
        settled_at: str | None = None,
        final: bool = False,
        actor: str = "primary-codex",
    ) -> dict[str, Any]:
        item = self.get(execution_id)
        if item["status"] not in {"ordered", "partially_filled"}:
            raise CompanionError(
                f"Execution fill can be reported only after ordering, not {item['status']}"
            )
        if not isinstance(final, bool):
            raise CompanionError("Execution fill final must be boolean")
        occurred = parse(occurred_at)
        if occurred > utc_now():
            raise CompanionError("Execution fill occurred_at cannot be in the future")
        action = self._action(item)
        absolute_quantity = dec(quantity, "fill quantity")
        fill_price = dec(price, "fill price")
        fill_fee = dec(fee, "fill fee")
        if absolute_quantity <= 0 or fill_price <= 0 or fill_fee < 0:
            raise CompanionError("fill quantity and price must be positive and fee non-negative")
        signed_quantity = (
            absolute_quantity if action["side"] == "buy" else -absolute_quantity
        )
        asset = self.c.financial.asset_get(action["asset_id"])
        entry = self.c.financial.ledger_add(
            account_id=action["account_id"],
            entry_type="trade",
            asset_id=action["asset_id"],
            occurred_at=iso(occurred),
            settled_at=settled_at,
            quantity=dtext(signed_quantity),
            price=dtext(fill_price),
            amount=dtext(-(signed_quantity * fill_price)),
            currency=asset["currency"],
            fee=dtext(fill_fee),
            source=self._text(source, "fill source"),
            external_id=external_id,
            metadata={
                "execution_id": execution_id,
                "queue_id": item["details"].get("queue_id"),
                "reported_final": final,
                "report_contract_version": 1,
            },
            status="needs_confirmation",
        )
        if entry["metadata"].get("reported_final") != final:
            raise CompanionError("duplicate fill report supplied a different final flag")
        with self.c.db.transaction() as con:
            self.c.audit.record(
                con,
                actor,
                "fill_reported",
                "execution",
                execution_id,
                after={
                    "ledger_entry_id": entry["id"],
                    "status": entry["status"],
                    "reported_final": final,
                },
                reason="reported fill remains pending until explicit confirmation",
            )
        return {
            "execution": item,
            "pending_ledger_entry": entry,
            "portfolio_changed": False,
            "requires_confirmation": True,
        }

    def confirm_fill(
        self,
        *,
        execution_id: str,
        entry_id: str,
        final: bool,
        actor: str = "primary-codex",
    ) -> dict[str, Any]:
        if not isinstance(final, bool):
            raise CompanionError("Execution fill final must be boolean")
        item = self.get(execution_id)
        if item["status"] in {"filled", "deviated"} and entry_id in item[
            "ledger_entry_ids"
        ]:
            self._close_queue(item, item["status"])
            return self._confirmation_result(item, self.c.financial.ledger_get(entry_id))
        if item["status"] not in {"ordered", "partially_filled"}:
            raise CompanionError(f"Execution fill cannot be confirmed from {item['status']}")
        entry = self.c.financial.ledger_get(entry_id)
        self._validate_fill_identity(item, entry)
        if entry["status"] not in {"draft", "needs_confirmation", "confirmed"}:
            raise CompanionError(f"Ledger entry cannot confirm an Execution from {entry['status']}")
        ids = [*item["ledger_entry_ids"], entry_id]
        if len(ids) != len(set(ids)):
            raise CompanionError("Execution fill is already linked")
        with self.c.db.connect() as con:
            used = con.execute(
                "SELECT e.id FROM executions e,json_each(e.ledger_entry_ids_json) j "
                "WHERE j.value=? AND e.id<>? LIMIT 1",
                (entry_id, execution_id),
            ).fetchone()
        if used:
            raise CompanionError(
                f"confirmed Ledger entry is already linked to Execution {used[0]}"
            )
        deviations, total = self._fill_deviations(item, ids, pending_entry=entry)
        target = dec(self._action(item)["quantity"], "target quantity")
        if total > target:
            deviations.append("filled_quantity_exceeds_action")
        if final and total != target:
            deviations.append("final_quantity_differs_from_action")
        if entry["status"] != "confirmed":
            entry = self.c.financial.ledger_confirm(entry_id)
        if deviations:
            status = "deviated"
            reason = "; ".join(sorted(set(deviations)))
        elif total == target:
            status, reason = "filled", None
        elif final:
            status, reason = "deviated", "final_quantity_differs_from_action"
        else:
            status, reason = "partially_filled", None
        execution = self.c.cognition.execution_set_status(
            execution_id, status, ids, reason
        )
        if status in {"filled", "deviated"}:
            self._close_queue(execution, status)
        with self.c.db.transaction() as con:
            self.c.audit.record(
                con,
                actor,
                "fill_confirmed",
                "execution",
                execution_id,
                before={"status": item["status"]},
                after={
                    "status": status,
                    "ledger_entry_ids": ids,
                    "deviations": sorted(set(deviations)),
                },
                reason="confirmed Ledger fact reconciled to Execution",
            )
        return self._confirmation_result(execution, entry)

    def cancel(
        self,
        *,
        execution_id: str,
        reason: str,
        actor: str = "primary-codex",
    ) -> dict[str, Any]:
        explanation = self._text(reason, "Execution cancellation reason")
        item = self.get(execution_id)
        cancelled = self.c.cognition.execution_set_status(
            execution_id, "cancelled", reason=explanation
        )
        self._close_queue(cancelled, "cancelled")
        with self.c.db.transaction() as con:
            self.c.audit.record(
                con,
                actor,
                "cancel",
                "execution",
                execution_id,
                before={"status": item["status"]},
                after={"status": "cancelled"},
                reason=explanation,
            )
        return cancelled

    def _require_current_accepted_card(self, execution: dict[str, Any]) -> dict[str, Any]:
        queue_id = execution.get("details", {}).get("queue_id")
        if not queue_id:
            raise CompanionError("version-neutral Execution lacks its Action Card")
        queue = self.c.operating.queue_get(queue_id)
        if queue["state"] != "accepted":
            raise CompanionError("Execution requires a currently accepted Action Card")
        card = self.c.operating.queue_card(
            queue_id, actionability_stage="execution"
        )
        if not card["executable_now"]:
            raise CompanionError(f"Action Card is no longer executable: {card['blocking_reasons']}")
        return card

    @staticmethod
    def _action(execution: dict[str, Any]) -> dict[str, Any]:
        action = execution.get("details", {}).get("action")
        required = {"account_id", "asset_id", "side", "quantity", "price_range"}
        if not isinstance(action, dict) or not required <= set(action):
            raise CompanionError("Execution lacks a complete frozen action")
        if action["side"] not in {"buy", "sell", "reduce"}:
            raise CompanionError("Execution action side is invalid")
        return action

    def _validate_fill_identity(
        self, execution: dict[str, Any], entry: dict[str, Any]
    ) -> None:
        action = self._action(execution)
        if entry["entry_type"] != "trade":
            raise CompanionError("Execution fill requires a trade Ledger entry")
        if entry["metadata"].get("execution_id") != execution["id"]:
            raise CompanionError("Ledger entry was not reported for this Execution")
        if entry["account_id"] != action["account_id"]:
            raise CompanionError("Execution fill account mismatch")
        if entry.get("asset_id") != action["asset_id"]:
            raise CompanionError("Execution fill asset mismatch")
        quantity = dec(entry.get("quantity_text"), "fill quantity")
        if (action["side"] == "buy" and quantity <= 0) or (
            action["side"] in {"sell", "reduce"} and quantity >= 0
        ):
            raise CompanionError("Execution fill side mismatch")

    def _fill_deviations(
        self,
        execution: dict[str, Any],
        entry_ids: list[str],
        *,
        pending_entry: dict[str, Any],
    ) -> tuple[list[str], Any]:
        action = self._action(execution)
        low = dec(action["price_range"]["min"], "minimum action price")
        high = dec(action["price_range"]["max"], "maximum action price")
        valid_until = parse(execution["details"]["valid_until"])
        deviations: list[str] = []
        total = dec("0")
        for entry_id in entry_ids:
            entry = pending_entry if entry_id == pending_entry["id"] else self.c.financial.ledger_get(entry_id)
            self._validate_fill_identity(execution, entry)
            total += abs(dec(entry["quantity_text"], "fill quantity"))
            price = dec(entry["price_text"], "fill price")
            if not low <= price <= high:
                deviations.append("fill_price_outside_action_range")
            if parse(entry["occurred_at"]) > valid_until:
                deviations.append("fill_after_action_validity")
        revision = self.c.cognition.revision_get(execution["decision_revision_id"])
        base_hash = revision["metadata"].get("confirmed_ledger_hash")
        if base_hash and base_hash != self.c.financial.confirmed_ledger_hash(
            exclude_ids=entry_ids
        ):
            deviations.append("base_ledger_changed_after_decision")
        return deviations, total

    def _close_queue(self, execution: dict[str, Any], outcome: str) -> None:
        if execution.get("details", {}).get("broker_strategy_plan_id"):
            return
        queue_id = execution.get("details", {}).get("queue_id")
        if not queue_id:
            return
        queue = self.c.operating.queue_get(queue_id)
        if queue["state"] == "accepted":
            self.c.operating.queue_respond(
                queue_id,
                state="closed",
                reason=f"Execution {execution['id']} ended as {outcome}",
            )

    def _confirmation_result(
        self, execution: dict[str, Any], entry: dict[str, Any]
    ) -> dict[str, Any]:
        action = self._action(execution)
        portfolio = self.c.financial.portfolio_state(
            iso(), action["account_id"]
        )
        return {
            "execution": execution,
            "confirmed_ledger_entry": entry,
            "portfolio": portfolio,
            "portfolio_changed": True,
            "truth": "confirmed_ledger_replay",
        }

    def _latest_audit(self, execution_id: str, action: str) -> dict[str, Any] | None:
        with self.c.db.connect() as con:
            row = row_dict(
                con.execute(
                    "SELECT * FROM audit_log WHERE entity_type='execution' AND entity_id=? "
                    "AND action=? ORDER BY id DESC LIMIT 1",
                    (execution_id, action),
                ).fetchone()
            )
        return row.get("after") if row else None
