from __future__ import annotations

from typing import Any

from .foundation import CompanionError, canonical, digest, new_id
from .db import row_dict, rows_dict
from .timeutil import iso, parse, utc_now


DELIVERY_MODES = {"silent_allowed", "digest_required", "report_required", "action_required"}
RESULT_CONCLUSIONS = {"no_action", "action", "review_required", "insufficient_evidence"}


class DeliveryEngine:
    """Persist the user-facing completion contract separately from Run status.

    A Run records whether work finished.  A DeliveryRecord records whether its
    user-visible result has been prepared and actually handed to cc-connect.
    This prevents a lifecycle card from being mistaken for an investment result.
    """

    def __init__(self, companion):
        self.c = companion
        self.db = companion.db

    @staticmethod
    def mode_for_policy(policy: dict[str, Any]) -> str:
        if not isinstance(policy, dict):
            raise CompanionError("schedule policy must be an object")
        explicit = policy.get("delivery_mode")
        if explicit is not None:
            if explicit not in DELIVERY_MODES:
                raise CompanionError("invalid schedule policy.delivery_mode")
            return explicit
        if policy.get("report_every_successful_run") or policy.get("notify") == "every_successful_run":
            return "report_required"
        if policy.get("notify") in {"material_only", "exceptions_only"} or policy.get("daily_brief_input"):
            return "digest_required"
        return "silent_allowed"

    def require_for_run(self, run_id: str, *, destination: str = "investment-companion") -> dict[str, Any]:
        run = self.c.run_get(run_id)
        policy = run.get("payload", {}).get("policy", {})
        mode = self.mode_for_policy(policy)
        now = iso()
        status = "suppressed" if mode == "silent_allowed" else "queued_digest" if mode == "digest_required" else "pending_content"
        due_at = run.get("due_at") if mode in {"report_required", "action_required"} else None
        record_id = new_id("delivery")
        key = f"run-delivery:{run_id}"
        with self.db.transaction() as con:
            con.execute(
                "INSERT OR IGNORE INTO delivery_records(id,run_id,mode,status,destination,idempotency_key,due_at,available_at,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
                (record_id, run_id, mode, status, destination, key, due_at, now, now, now),
            )
            row = con.execute("SELECT * FROM delivery_records WHERE run_id=?", (run_id,)).fetchone()
        return row_dict(row)

    def get(self, delivery_id: str) -> dict[str, Any]:
        with self.db.connect() as con:
            item = row_dict(con.execute("SELECT * FROM delivery_records WHERE id=?", (delivery_id,)).fetchone())
        if not item:
            raise CompanionError(f"delivery record not found: {delivery_id}")
        return item

    def for_run(self, run_id: str) -> dict[str, Any] | None:
        with self.db.connect() as con:
            return row_dict(con.execute("SELECT * FROM delivery_records WHERE run_id=?", (run_id,)).fetchone())

    def list(self, *, status: str | None = None, mode: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
        if limit <= 0 or limit > 500:
            raise CompanionError("delivery list limit must be within 1..500")
        clauses: list[str] = []
        params: list[Any] = []
        if status:
            clauses.append("status=?")
            params.append(status)
        if mode:
            if mode not in DELIVERY_MODES:
                raise CompanionError("invalid delivery mode filter")
            clauses.append("mode=?")
            params.append(mode)
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        params.append(limit)
        with self.db.connect() as con:
            return rows_dict(con.execute(f"SELECT * FROM delivery_records{where} ORDER BY created_at DESC LIMIT ?", params).fetchall())

    @staticmethod
    def _text(value: Any, name: str) -> str:
        if not isinstance(value, str) or not value.strip():
            raise CompanionError(f"delivery {name} must be non-empty text")
        return value.strip()

    def _result(self, *, conclusion: str, summary: str, key_evidence: list[str], next_step: str, next_check_at: str | None, source_refs: list[str]) -> dict[str, Any]:
        if conclusion not in RESULT_CONCLUSIONS:
            raise CompanionError("invalid delivery conclusion")
        if not isinstance(key_evidence, list) or len(key_evidence) > 3 or any(not isinstance(item, str) or not item.strip() for item in key_evidence):
            raise CompanionError("delivery key_evidence must contain at most three non-empty items")
        if not isinstance(source_refs, list) or any(not isinstance(item, str) or not item.strip() for item in source_refs):
            raise CompanionError("delivery source_refs must be a list of stable non-empty references")
        if next_check_at is not None:
            parse(next_check_at)
        return {
            "schema": "investment-companion.result-envelope/v1",
            "conclusion": conclusion,
            "summary": self._text(summary, "summary"),
            "key_evidence": [item.strip() for item in key_evidence],
            "next_step": self._text(next_step, "next_step"),
            "next_check_at": next_check_at,
            "source_refs": [item.strip() for item in source_refs],
        }

    @staticmethod
    def render(result: dict[str, Any]) -> str:
        evidence = "；".join(result["key_evidence"]) if result["key_evidence"] else "无新增材料性变化。"
        next_check = result["next_check_at"] or "未设定"
        labels = {
            "no_action": "不行动",
            "action": "行动",
            "review_required": "需要复核",
            "insufficient_evidence": "资料不足",
        }
        return "\n".join(
            [
                f"结论：{labels[result['conclusion']]}",
                f"说明：{result['summary']}",
                f"依据：{evidence}",
                f"下一步：{result['next_step']}",
                f"下次检查：{next_check}",
            ]
        )

    def prepare(
        self,
        delivery_id: str,
        *,
        conclusion: str,
        summary: str,
        key_evidence: list[str],
        next_step: str,
        next_check_at: str | None = None,
        source_refs: list[str] | None = None,
    ) -> dict[str, Any]:
        item = self.get(delivery_id)
        if item["mode"] == "silent_allowed":
            raise CompanionError("silent_allowed delivery cannot prepare a user result")
        result = self._result(
            conclusion=conclusion,
            summary=summary,
            key_evidence=key_evidence,
            next_step=next_step,
            next_check_at=next_check_at,
            source_refs=source_refs or [],
        )
        content_hash = digest("investment-companion.result-envelope/v1", item["run_id"], result)
        now = iso()
        if item.get("content_hash"):
            if item["content_hash"] != content_hash:
                raise CompanionError("delivery result is immutable once prepared")
            return item
        # Delivery modes define the result obligation; Attention Policy only
        # decides timing.  A quiet-hour or budget decision may defer a direct
        # report into the digest queue, but never suppresses or discards it.
        attention_decision_id = None
        status = "queued_digest" if item["mode"] == "digest_required" else "pending_send"
        try:
            attention = self.c.attention.decide(
                topic=f"delivery:{item['run_id']}",
                materiality="critical" if item["mode"] == "action_required" else "normal",
                confidence="high",
                reason=f"V7 {item['mode']} result requires a user-visible outcome",
                evidence=[item["id"], *result["source_refs"]],
                requested_action="queue_digest" if item["mode"] == "digest_required" else "notify_now",
            )
            attention_decision_id = attention["id"]
            if attention["action"] != "notify_now":
                status = "queued_digest"
        except CompanionError as exc:
            # Test fixtures and first-run installations may not yet have an
            # Attention Policy.  The delivery contract is still enforceable;
            # surface the policy gap in the audit trail rather than losing a
            # required result.
            if "no current attention policy" not in str(exc):
                raise
        outbox_id = None
        with self.db.transaction() as con:
            if status == "pending_send":
                outbox_id = new_id("out")
                outbox_key = f"delivery-send:{item['id']}:{content_hash}"
                message = self.render(result)
                con.execute(
                    "INSERT INTO outbox(id,kind,destination,payload_json,idempotency_key,status,available_at,created_at,updated_at) VALUES(?,?,?,?,?,'pending',?,?,?)",
                    (outbox_id, "user_result", item["destination"], canonical({"schema": "investment-companion.user-result/v1", "delivery_id": item["id"], "message": message}), outbox_key, now, now, now),
                )
            changed = con.execute(
                "UPDATE delivery_records SET status=?,result_json=?,content_hash=?,outbox_id=?,attention_decision_id=?,available_at=?,updated_at=? WHERE id=? AND status IN ('pending_content','queued_digest','retry') AND content_hash IS NULL",
                (status, canonical(result), content_hash, outbox_id, attention_decision_id, now, now, item["id"]),
            ).rowcount
            if changed != 1:
                raise CompanionError("delivery preparation lost to another writer")
            self.c.audit.record(con, "primary-codex", "prepare", "delivery_record", item["id"], before={"status": item["status"]}, after={"status": status, "content_hash": content_hash})
        return self.get(delivery_id)

    def digest_send(self, delivery_ids: list[str], *, conclusion: str, summary: str, key_evidence: list[str], next_step: str, next_check_at: str | None = None, source_refs: list[str] | None = None) -> list[dict[str, Any]]:
        if not delivery_ids or len(delivery_ids) != len(set(delivery_ids)):
            raise CompanionError("digest delivery_ids must be unique and non-empty")
        records = [self.get(item) for item in delivery_ids]
        if any(item["mode"] != "digest_required" or item["status"] not in {"queued_digest", "pending_content", "retry"} for item in records):
            raise CompanionError("digest can only deliver queued digest-required records")
        destinations = {item["destination"] for item in records}
        if len(destinations) != 1:
            raise CompanionError("digest records must share one destination")
        result = self._result(conclusion=conclusion, summary=summary, key_evidence=key_evidence, next_step=next_step, next_check_at=next_check_at, source_refs=source_refs or [])
        now = iso()
        message = self.render(result)
        batch = digest("investment-companion.digest/v1", sorted(delivery_ids), result)
        outbox_id = new_id("out")
        with self.db.transaction() as con:
            con.execute(
                "INSERT OR IGNORE INTO outbox(id,kind,destination,payload_json,idempotency_key,status,available_at,created_at,updated_at) VALUES(?,?,?,?,?,'pending',?,?,?)",
                (outbox_id, "user_result", records[0]["destination"], canonical({"schema": "investment-companion.user-result/v1", "delivery_ids": delivery_ids, "message": message}), f"digest-send:{batch}", now, now, now),
            )
            outbox = con.execute("SELECT id FROM outbox WHERE idempotency_key=?", (f"digest-send:{batch}",)).fetchone()
            for item in records:
                changed = con.execute(
                    "UPDATE delivery_records SET status='pending_send',result_json=?,content_hash=?,outbox_id=?,available_at=?,updated_at=? WHERE id=? AND status IN ('queued_digest','pending_content','retry')",
                    (canonical(result), digest(batch, item["id"]), outbox["id"], now, now, item["id"]),
                ).rowcount
                if changed != 1:
                    raise CompanionError("digest delivery state changed concurrently")
        return [self.get(item) for item in delivery_ids]

    def mark_outbox_result(self, outbox_id: str, *, success: bool, terminal: bool, error: str | None = None) -> None:
        status = "delivered" if success else "failed" if terminal else "retry"
        now = iso()
        with self.db.transaction() as con:
            rows = con.execute("SELECT id,attention_decision_id FROM delivery_records WHERE outbox_id=?", (outbox_id,)).fetchall()
            for row in rows:
                con.execute(
                    "UPDATE delivery_records SET status=?,delivered_at=CASE WHEN ?='delivered' THEN ? ELSE delivered_at END,last_error=?,updated_at=? WHERE id=?",
                    (status, status, now, None if success else error, now, row["id"]),
                )
        if success:
            for row in rows:
                if not row["attention_decision_id"]:
                    continue
                try:
                    self.c.attention.mark_delivered(row["attention_decision_id"])
                except CompanionError:
                    # A digest decision is intentionally queue_digest, which
                    # has no individual notify-now receipt to mark.
                    pass

    def recover_outbox(self, outbox_id: str) -> int:
        with self.db.transaction() as con:
            return con.execute(
                "UPDATE delivery_records SET status='retry',updated_at=? WHERE outbox_id=? AND status IN ('pending_send','sending')",
                (iso(), outbox_id),
            ).rowcount

    def status(self) -> dict[str, Any]:
        now = iso()
        with self.db.connect() as con:
            counts = {row["status"]: row["count"] for row in con.execute("SELECT status,COUNT(*) AS count FROM delivery_records GROUP BY status")}
            overdue = con.execute(
                "SELECT COUNT(*) FROM delivery_records WHERE mode IN ('report_required','action_required') AND status NOT IN ('delivered','failed') AND due_at IS NOT NULL AND due_at<?",
                (now,),
            ).fetchone()[0]
        return {"counts": counts, "overdue_required": overdue, "now": now}

    def migrate_schedule_policies(self, *, apply: bool = False, actor: str = "primary-codex") -> dict[str, Any]:
        """Explicitly pin legacy notification prose to V7 delivery modes.

        This is intentionally opt-in: historical schedules remain readable and
        behaviour-compatible until an operator reviews the proposed mapping.
        """
        proposed: list[dict[str, Any]] = []
        for schedule in self.c.schedule_list():
            policy = schedule["policy"]
            if "delivery_mode" in policy:
                continue
            mode = self.mode_for_policy(policy)
            proposed.append({"schedule_id": schedule["id"], "name": schedule["name"], "expected_version": schedule["version"], "delivery_mode": mode})
        if apply:
            changed = []
            for item in proposed:
                schedule = self.c.schedule_get(item["schedule_id"])
                if "delivery_mode" in schedule["policy"]:
                    continue
                changed.append(
                    self.c.schedule_patch(
                        schedule["id"],
                        schedule["version"],
                        {"policy": {**schedule["policy"], "delivery_mode": item["delivery_mode"]}},
                        actor=actor,
                        reason="V7 result delivery contract migration",
                    )
                )
            return {"applied": True, "schedules": changed}
        return {"applied": False, "proposed": proposed}
