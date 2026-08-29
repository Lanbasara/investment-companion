from __future__ import annotations

import os
from typing import Any

from ..capabilities import (
    RECONCILIATION_STATEMENT_SCHEMA,
    investment_capability_registry,
)
from ..foundation import CompanionError


def schema(
    properties: dict[str, Any] | None = None,
    required: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": properties or {},
        "required": required or [],
        "additionalProperties": False,
    }


S = {"type": "string"}
I = {"type": "integer"}
O = {"type": "object", "additionalProperties": True}
A = {"type": "array", "items": S}
RECONCILIATION_STATEMENT = RECONCILIATION_STATEMENT_SCHEMA

UNCONTRACTED_INVESTMENT_TOOLS = {
    "evaluation_context": (
        "读取客观绩效、复盘和待验证的变更提案。",
        schema({"limit": I}),
    ),
    "investment_performance_calculate": (
        "按确认账本、现金流、基准和成本计算一个周期的客观投资结果。",
        schema(
            {
                "account_id": S,
                "period_start": S,
                "period_end": S,
                "start_prices": O,
                "end_prices": O,
                "benchmark_start_value": {},
                "benchmark_end_value": {},
                "valuation_points": {"type": "array", "items": O},
                "source_refs": A,
                "attribution_refs": A,
            },
            ["account_id", "period_start", "period_end", "start_prices", "end_prices"],
        ),
    ),
    "investment_review_publish": (
        "发布证据化复盘和惰性变更提案；不能直接修改当前策略。",
        schema(
            {
                "subject": O,
                "content": S,
                "conclusion": {
                    "type": "string",
                    "enum": ["continue", "revise", "stop", "insufficient_evidence"],
                },
                "calculation_ids": A,
                "source_refs": A,
                "proposed_changes": {"type": "array", "items": O},
                "knowledge_cutoff": S,
            },
            ["subject", "content", "conclusion", "calculation_ids", "source_refs"],
        ),
    ),
}

INVESTMENT_CAPABILITY_REGISTRY = investment_capability_registry(
    UNCONTRACTED_INVESTMENT_TOOLS
)
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
    if name == "evaluation_context":
        return companion.investment.evaluation_context(**arguments)
    commands = companion.investment_commands
    if name == "investment_performance_calculate":
        return commands.performance_calculate(**arguments)
    if name == "investment_review_publish":
        return commands.review_publish(**arguments)
    raise CompanionError(f"unimplemented investment tool: {name}")
