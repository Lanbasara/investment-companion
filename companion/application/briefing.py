from __future__ import annotations

from collections import Counter
from datetime import timedelta
from typing import Any

from ..db import rows_dict
from ..financial import reconciliation_is_full_match
from ..foundation import CompanionError
from ..timeutil import iso, parse, utc_now


class InvestmentBriefingService:
    """Project execution truth into user briefs without becoming a truth owner."""

    SCHEMA = "investment-companion.execution-briefing/v1"
    EXECUTION_STATUSES = (
        "proposed",
        "presented",
        "accepted",
        "rejected",
        "ordered",
        "partially_filled",
        "filled",
        "cancelled",
        "expired",
        "superseded",
        "deviated",
    )
    OPEN_STATUSES = {"proposed", "presented", "accepted", "ordered", "partially_filled"}

    def __init__(self, companion):
        self.c = companion

    def projection(
        self,
        *,
        program_id: str,
        as_of: str | None = None,
        since: str | None = None,
    ) -> dict[str, Any]:
        snapshot, _sources = self._build(
            program_id=program_id,
            as_of=as_of or iso(),
            since=since,
        )
        return snapshot

    def freeze_for_brief(
        self,
        *,
        program_id: str,
        brief_type: str,
        as_of: str,
    ) -> dict[str, Any]:
        if brief_type not in {"daily", "weekly", "monthly"}:
            raise CompanionError("invalid operating brief type")
        as_of_time = parse(as_of)
        if as_of_time > utc_now():
            raise CompanionError("operating brief as_of cannot be in the future")
        window = {"daily": 1, "weekly": 7, "monthly": 31}[brief_type]
        since = iso(as_of_time - timedelta(days=window))
        snapshot, sources = self._build(
            program_id=program_id,
            as_of=as_of,
            since=since,
        )
        if "current_projection_changed_after_as_of" in snapshot["warnings"]:
            raise CompanionError(
                "execution or reconciliation state changed after brief as_of; "
                "prepare the brief with a current as_of"
            )
        calculation = self.c.financial.calculation_record(
            "execution_operating_snapshot",
            "Freeze order, fill, Ledger confirmation, deviation, and reconciliation state for a user brief",
            as_of,
            {
                "program_id": program_id,
                "brief_type": brief_type,
                "window_start": since,
                **sources,
            },
            {
                "accepted_action_is_not_an_order": True,
                "reported_fill_changes_portfolio": False,
                "confirmed_fill_changes_portfolio": True,
                "execution_is_manual": True,
            },
            {
                "program_execution": "Execution.queue_id belongs to a Program DecisionQueue item",
                "pending_fill": "Ledger.status in {draft,needs_confirmation}",
                "portfolio_change": "linked Ledger.status == confirmed",
                "reconciliation": "latest Reconciliation per Program account",
            },
            snapshot,
            snapshot["warnings"],
        )
        return {**snapshot, "calculation_id": calculation["id"], "frozen": True}

    @staticmethod
    def today_signal(snapshot: dict[str, Any]) -> dict[str, str] | None:
        pending = snapshot["pending_fill_entry_ids"]
        if pending:
            return {
                "mode": "action",
                "message": f"有 {len(pending)} 笔已报告成交等待你确认；确认前不会改变真实组合。",
            }
        accepted = snapshot["accepted_without_execution_queue_ids"]
        proposed = snapshot["proposed_execution_ids"]
        if accepted or proposed:
            return {
                "mode": "action",
                "message": f"有 {len(accepted) + len(proposed)} 项已接受建议尚未完成券商订单反馈。",
            }
        strategy_attention = snapshot["broker_strategy_attention_ids"]
        if strategy_attention:
            return {"mode":"review_required","message":f"有 {len(strategy_attention)} 个券商条件单处于终止待核对或异常状态，需要检查未成交委托和持仓。"}
        reconciliation = snapshot["reconciliation"]
        if reconciliation["needs_review_ids"]:
            return {
                "mode": "review_required",
                "message": f"有 {len(reconciliation['needs_review_ids'])} 个账户对账结果存在差异或口径不完整，需要核验。",
            }
        material_events = [
            item
            for item in snapshot["events_since"]
            if item["action"] in {"fill_confirmed", "cancel"}
        ]
        if material_events:
            return {
                "mode": "review_required",
                "message": f"自上次日结后有 {len(material_events)} 项执行结果，需要纳入本期复盘。",
            }
        return None

    def _build(
        self,
        *,
        program_id: str,
        as_of: str,
        since: str | None,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        as_of_time = parse(as_of)
        since_time = parse(since) if since else None
        if since_time and since_time > as_of_time:
            raise CompanionError("execution briefing since cannot be after as_of")
        program = self.c.operating.program_get(program_id)
        revision = program.get("current_revision") or {}
        account_ids = list(revision.get("content", {}).get("account_ids", []))
        self.c.execution_strategy.expire_due()
        with self.c.db.connect() as con:
            queues = rows_dict(
                con.execute(
                    "SELECT * FROM decision_queue_items WHERE program_id=? ORDER BY created_at,id",
                    (program_id,),
                ).fetchall()
            )
            executions = rows_dict(
                con.execute("SELECT * FROM executions ORDER BY created_at,id").fetchall()
            )
            strategies = rows_dict(con.execute("SELECT * FROM broker_execution_plans WHERE program_id=? ORDER BY created_at,id",(program_id,)).fetchall())
        queue_by_id = {item["id"]: item for item in queues}
        decision_revisions = {item["decision_revision_id"] for item in queues}
        executions = [item for item in executions if self._belongs_to_program(item, queue_by_id, decision_revisions)]
        execution_ids = {item["id"] for item in executions}
        linked_ledger_ids = {
            entry_id for item in executions for entry_id in item.get("ledger_entry_ids", [])
        }
        ledgers = self._execution_ledgers(execution_ids, linked_ledger_ids)
        reconciliations = self._latest_reconciliations(account_ids)
        events = self._execution_events(execution_ids, as_of=as_of, since=since)
        counts = Counter(item["status"] for item in executions)
        execution_queue_ids = {
            item.get("details", {}).get("queue_id") for item in executions
        }
        strategy_queue_ids={item["queue_id"] for item in strategies}
        accepted_without_execution = sorted(
            item["id"]
            for item in queues
            if item["state"] == "accepted" and item["id"] not in execution_queue_ids and item["id"] not in strategy_queue_ids
        )
        pending_ledgers = sorted(
            item["id"]
            for item in ledgers
            if item["status"] in {"draft", "needs_confirmation"}
        )
        confirmed_ledgers = sorted(
            item["id"] for item in ledgers if item["status"] == "confirmed"
        )
        proposed = sorted(
            item["id"]
            for item in executions
            if item["status"] in {"proposed", "presented", "accepted"}
        )
        open_orders = sorted(
            item["id"]
            for item in executions
            if item["status"] in {"ordered", "partially_filled"}
        )
        deviated = sorted(
            item["id"] for item in executions if item["status"] == "deviated"
        )
        reconciliation_summary = self._reconciliation_summary(
            account_ids, reconciliations
        )
        warnings = []
        current_rows = [*queues, *executions, *strategies, *ledgers, *reconciliations]
        if any(parse(item.get("updated_at") or item.get("confirmed_at") or item["created_at"]) > as_of_time for item in current_rows):
            warnings.append("current_projection_changed_after_as_of")
        snapshot = {
            "schema": self.SCHEMA,
            "program_id": program_id,
            "program_revision_id": program.get("current_revision_id"),
            "as_of": as_of,
            "window_start": since,
            "status_counts": {
                status: counts.get(status, 0) for status in self.EXECUTION_STATUSES
            },
            "open_execution_ids": sorted(
                item["id"] for item in executions if item["status"] in self.OPEN_STATUSES
            ),
            "proposed_execution_ids": proposed,
            "open_order_execution_ids": open_orders,
            "pending_fill_entry_ids": pending_ledgers,
            "confirmed_fill_entry_ids": confirmed_ledgers,
            "deviated_execution_ids": deviated,
            "accepted_without_execution_queue_ids": accepted_without_execution,
            "broker_strategy_ids": [item["id"] for item in strategies],
            "active_broker_strategy_ids": [item["id"] for item in strategies if item["status"] in {"configured","active","sleeping"}],
            "broker_strategy_attention_ids": [item["id"] for item in strategies if item["status"] in {"termination_pending","terminated","exception"}],
            "reconciliation": reconciliation_summary,
            "events_since": events,
            "portfolio_changed_by_confirmed_fills": bool(confirmed_ledgers),
            "truth": "confirmed_ledger_replay",
            "warnings": warnings,
        }
        sources = {
            "queue_states": [self._queue_source(item) for item in queues],
            "execution_states": [self._execution_source(item) for item in executions],
            "ledger_states": [self._ledger_source(item) for item in ledgers],
            "reconciliation_states": [
                self._reconciliation_source(item) for item in reconciliations
            ],
            "execution_events": events,
            "broker_strategy_states": [{"id":item["id"],"plan_type":item["plan_type"],"status":item["status"],"queue_id":item["queue_id"],"updated_at":item["updated_at"]} for item in strategies],
        }
        return snapshot, sources

    @staticmethod
    def _belongs_to_program(
        execution: dict[str, Any],
        queue_by_id: dict[str, dict[str, Any]],
        decision_revisions: set[str],
    ) -> bool:
        queue_id = execution.get("details", {}).get("queue_id")
        if queue_id:
            return queue_id in queue_by_id
        return execution.get("decision_revision_id") in decision_revisions

    def _execution_ledgers(
        self, execution_ids: set[str], linked_ledger_ids: set[str]
    ) -> list[dict[str, Any]]:
        if not execution_ids:
            return []
        execution_placeholders = ",".join("?" for _ in execution_ids)
        query = f"SELECT * FROM ledger_entries WHERE json_extract(metadata_json,'$.execution_id') IN ({execution_placeholders})"
        params = list(sorted(execution_ids))
        if linked_ledger_ids:
            ledger_placeholders = ",".join("?" for _ in linked_ledger_ids)
            query += f" OR id IN ({ledger_placeholders})"
            params.extend(sorted(linked_ledger_ids))
        query += " ORDER BY occurred_at,id"
        with self.c.db.connect() as con:
            return rows_dict(con.execute(query, params).fetchall())

    def _latest_reconciliations(self, account_ids: list[str]) -> list[dict[str, Any]]:
        if not account_ids:
            return []
        placeholders = ",".join("?" for _ in account_ids)
        with self.c.db.connect() as con:
            rows = rows_dict(
                con.execute(
                    f"SELECT * FROM reconciliations WHERE account_id IN ({placeholders}) ORDER BY account_id,julianday(as_of) DESC,rowid DESC",
                    tuple(account_ids),
                ).fetchall()
            )
        latest: dict[str, dict[str, Any]] = {}
        for item in rows:
            latest.setdefault(item["account_id"], item)
        return [latest[item] for item in account_ids if item in latest]

    def _execution_events(
        self,
        execution_ids: set[str],
        *,
        as_of: str,
        since: str | None,
    ) -> list[dict[str, Any]]:
        if not execution_ids:
            return []
        placeholders = ",".join("?" for _ in execution_ids)
        query = (
            f"SELECT id,occurred_at,action,entity_id FROM audit_log "
            f"WHERE entity_type='execution' AND entity_id IN ({placeholders}) "
            "AND julianday(occurred_at)<=julianday(?)"
        )
        params: list[Any] = [*sorted(execution_ids), as_of]
        if since:
            query += " AND julianday(occurred_at)>julianday(?)"
            params.append(since)
        query += " ORDER BY occurred_at,id"
        with self.c.db.connect() as con:
            return [dict(row) for row in con.execute(query, params).fetchall()]

    @staticmethod
    def _reconciliation_summary(
        account_ids: list[str], reconciliations: list[dict[str, Any]]
    ) -> dict[str, Any]:
        by_account = {item["account_id"]: item for item in reconciliations}
        return {
            "latest": [
                {
                    "id": item["id"],
                    "account_id": item["account_id"],
                    "as_of": item["as_of"],
                    "status": item["status"],
                    "full_scope_matched": reconciliation_is_full_match(item),
                    "difference_count": len(item["differences"]),
                }
                for item in reconciliations
            ],
            "needs_review_ids": sorted(
                item["id"]
                for item in reconciliations
                if not reconciliation_is_full_match(item)
            ),
            "missing_account_ids": sorted(
                account_id for account_id in account_ids if account_id not in by_account
            ),
        }

    @staticmethod
    def _queue_source(item: dict[str, Any]) -> dict[str, Any]:
        return {
            key: item.get(key)
            for key in ("id", "decision_revision_id", "state", "version", "valid_until", "updated_at")
        }

    @staticmethod
    def _execution_source(item: dict[str, Any]) -> dict[str, Any]:
        return {
            "id": item["id"],
            "decision_revision_id": item.get("decision_revision_id"),
            "queue_id": item.get("details", {}).get("queue_id"),
            "status": item["status"],
            "status_reason": item.get("status_reason"),
            "ledger_entry_ids": item.get("ledger_entry_ids", []),
            "updated_at": item["updated_at"],
        }

    @staticmethod
    def _ledger_source(item: dict[str, Any]) -> dict[str, Any]:
        return {
            key: item.get(key)
            for key in (
                "id",
                "account_id",
                "asset_id",
                "occurred_at",
                "quantity_text",
                "price_text",
                "fee_text",
                "status",
                "confirmed_at",
            )
        }

    @staticmethod
    def _reconciliation_source(item: dict[str, Any]) -> dict[str, Any]:
        return {
            "id": item["id"],
            "account_id": item["account_id"],
            "as_of": item["as_of"],
            "status": item["status"],
            "full_scope_matched": reconciliation_is_full_match(item),
            "difference_count": len(item["differences"]),
            "source_ref": item.get("source_ref"),
            "created_at": item["created_at"],
        }
