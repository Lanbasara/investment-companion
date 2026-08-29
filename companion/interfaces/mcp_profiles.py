from __future__ import annotations

from typing import Any

from ..capabilities import (
    RECONCILIATION_STATEMENT_SCHEMA,
    investment_capability_registry,
)
RECONCILIATION_STATEMENT = RECONCILIATION_STATEMENT_SCHEMA

INVESTMENT_CAPABILITY_REGISTRY = investment_capability_registry()
INVESTMENT_TOOLS = INVESTMENT_CAPABILITY_REGISTRY.discovery_tools()
INVESTMENT_TOOL_PROJECTION = INVESTMENT_TOOLS


def call_investment(companion, name: str, arguments: dict[str, Any], actor: str) -> Any:
    if INVESTMENT_CAPABILITY_REGISTRY.handles(name):
        return INVESTMENT_CAPABILITY_REGISTRY.invoke(
            companion, name, arguments, actor=actor
        )
    from ..foundation import CompanionError

    raise CompanionError(f"unimplemented investment tool: {name}")
