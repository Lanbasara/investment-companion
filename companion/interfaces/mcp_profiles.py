from __future__ import annotations

import os
from typing import Any

from ..capabilities import (
    RECONCILIATION_STATEMENT_SCHEMA,
    investment_capability_registry,
)
from ..foundation import CompanionError

RECONCILIATION_STATEMENT = RECONCILIATION_STATEMENT_SCHEMA

INVESTMENT_CAPABILITY_REGISTRY = investment_capability_registry()
INVESTMENT_TOOLS = INVESTMENT_CAPABILITY_REGISTRY.discovery_tools()
INVESTMENT_TOOL_PROJECTION = INVESTMENT_TOOLS


def active_tools(legacy_tools: dict[str, Any]) -> dict[str, Any]:
    profile = os.environ.get("COMPANION_MCP_PROFILE", "all")
    if profile == "investment":
        return INVESTMENT_TOOL_PROJECTION
    if profile == "admin":
        return legacy_tools
    if profile == "all":
        return {**INVESTMENT_TOOL_PROJECTION, **legacy_tools}
    raise CompanionError("COMPANION_MCP_PROFILE must be investment, admin or all")


def call_investment(companion, name: str, arguments: dict[str, Any], actor: str) -> Any:
    if INVESTMENT_CAPABILITY_REGISTRY.handles(name):
        return INVESTMENT_CAPABILITY_REGISTRY.invoke(
            companion, name, arguments, actor=actor
        )
    raise CompanionError(f"unimplemented investment tool: {name}")
