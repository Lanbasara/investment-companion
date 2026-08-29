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
    "investment_execution_update": (
        "管理人工执行和券商托管条件单：一次性委托、定价买卖、止盈止损与网格均需用户在券商配置并回报状态，不会自动改变组合。",
        schema(
            {
                "operation": {
                    "type": "string",
                    "enum": ["prepare", "order", "report_fill", "confirm_fill", "cancel", "strategy_create", "strategy_configured", "strategy_activate", "strategy_order_report", "strategy_terminate_request", "strategy_terminated", "strategy_etf_dividend", "strategy_sleep", "strategy_exception", "strategy_reconcile"],
                },
                "queue_id": S,
                "idempotency_key": S,
                "execution_id": S,
                "broker_order_ref": S,
                "ordered_at": S,
                "occurred_at": S,
                "quantity": {},
                "price": {},
                "fee": {},
                "source": S,
                "external_id": S,
                "settled_at": S,
                "entry_id": S,
                "final": {"type": "boolean"},
                "reason": S,
                "plan_id": S,
                "plan_type": {"type":"string","enum":["priced_buy","priced_sell","bracket_exit","moving_grid"]},
                "spec": O,
                "valid_until": S,
                "broker_condition_ref": S,
                "configured_at": S,
                "broker_validity_sessions": {"type":"integer","enum":[5,20,60,180]},
                "broker_valid_until": S,
                "triggered_at": S,
                "trigger_price": {},
                "reference_price_before": {},
                "reference_price_after": {},
                "rejection_reason": S,
                "cancelled_quantity": {},
                "condition_leg": {"type":"string","enum":["take_profit","stop_loss"]},
                "status": S,
                "side": S,
                "corporate_action_ref": S,
                "sleeping": {"type":"boolean"},
                "direction": {"type":"string","enum":["buy","sell"]},
                "reconciliation_id": S,
                "corrected_event_ref": S,
            },
            ["operation"],
        ),
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
    if name == "investment_execution_update":
        operation = arguments["operation"]
        return commands.execution_update(
            operation=operation,
            actor=actor,
            **{key: value for key, value in arguments.items() if key != "operation"},
        )
    if name == "investment_performance_calculate":
        return commands.performance_calculate(**arguments)
    if name == "investment_review_publish":
        return commands.review_publish(**arguments)
    raise CompanionError(f"unimplemented investment tool: {name}")
