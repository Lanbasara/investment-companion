from __future__ import annotations

from typing import Any

from .foundation import canonical
from .timeutil import iso


class AuditTrail:
    """Append-only audit writer shared by application and platform services."""

    def record(
        self,
        con,
        actor: str,
        action: str,
        entity_type: str,
        entity_id: str,
        before: Any = None,
        after: Any = None,
        reason: str | None = None,
    ) -> None:
        con.execute(
            "INSERT INTO audit_log(occurred_at,actor,action,entity_type,entity_id,before_json,after_json,reason) "
            "VALUES(?,?,?,?,?,?,?,?)",
            (
                iso(),
                actor,
                action,
                entity_type,
                entity_id,
                canonical(before) if before is not None else None,
                canonical(after) if after is not None else None,
                reason,
            ),
        )
