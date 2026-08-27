from __future__ import annotations

import os
from typing import Any

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
DECIMAL_MAP = {"type": "object", "additionalProperties": {}}
RECONCILIATION_STATEMENT = {
    "type": "object",
    "properties": {
        "cash": DECIMAL_MAP,
        "positions": DECIMAL_MAP,
        "position_values": DECIMAL_MAP,
        "position_total_by_currency": DECIMAL_MAP,
        "total_by_currency": DECIMAL_MAP,
        "metadata": O,
    },
    "required": [
        "cash",
        "positions",
        "position_values",
        "position_total_by_currency",
        "total_by_currency",
    ],
    "additionalProperties": False,
}

INVESTMENT_TOOLS = {
    "investment_home": ("读取今天的行动、异常、研究工作队列、绩效和交付总入口；未完成研究返回 review_required。", schema()),
    "portfolio_context": (
        "读取确认账本重建的组合、现金、个人约束和投资政策。",
        schema({"account_id": S, "as_of": S, "prices": O}),
    ),
    "research_context": (
        "读取某标的或 work_item_id 对应的候选范围、研究队列、ResearchRecord、Validation 与机会。",
        schema({"subject_id": S, "work_item_id": S, "limit": I}),
    ),
    "decision_context": (
        "读取当前建议、行动卡、失效原因和人工执行边界。",
        schema({"limit": I}),
    ),
    "evaluation_context": (
        "读取客观绩效、复盘和待验证的变更提案。",
        schema({"limit": I}),
    ),
    "investment_program_context": (
        "读取当前或历史投资计划及其不可变版本。",
        schema({"program_id": S, "status": S}),
    ),
    "investment_workflow_context": (
        "读取主动日历、运行历史和结果交付状态。",
        schema(
            {
                "view": {
                    "type": "string",
                    "enum": [
                        "schedules", "schedule", "schedule_history", "runs", "run",
                        "deliveries", "delivery", "delivery_status", "system_status", "doctor",
                    ],
                },
                "schedule_id": S,
                "run_id": S,
                "delivery_id": S,
                "status": S,
                "kind": S,
                "mode": S,
                "limit": I,
            },
            ["view"],
        ),
    ),
    "investment_context_update": (
        "创建或确认个人事实、投资约束和注意力策略；草稿不会自动生效。",
        schema(
            {
                "operation": {"type": "string", "enum": ["draft", "confirm"]},
                "context_type": {"type": "string", "enum": ["investor", "mandate", "attention"]},
                "content": O,
                "reason": S,
                "effective_from": S,
                "expires_at": S,
                "revision_id": S,
                "trial": {"type": "boolean"},
            },
            ["operation"],
        ),
    ),
    "investment_program_update": (
        "创建、修订、确认、暂停、恢复或归档投资计划；确认需要用户批准引用。",
        schema(
            {
                "operation": {
                    "type": "string", "enum": ["create", "revise", "confirm", "status"]
                },
                "name": S,
                "content": O,
                "context_refs": O,
                "reason": S,
                "expires_at": S,
                "program_id": S,
                "expected_version": I,
                "revision_id": S,
                "user_approval_ref": S,
                "trial": {"type": "boolean"},
                "supersedes_program_id": S,
                "status": {"type": "string", "enum": ["active", "paused", "archived"]},
            },
            ["operation"],
        ),
    ),
    "investment_opportunity_update": (
        "维护研究闭环：work_claim 领取任务，triage_complete 逐项分流，research_complete 绑定正式研究结果；create/transition 维护机会。不能直接创建交易。",
        schema(
            {
                "operation": {"type": "string", "enum": ["create", "transition", "work_claim", "triage_complete", "research_complete"]},
                "subject": O,
                "evidence_refs": A,
                "reason": S,
                "program_id": S,
                "thesis_id": S,
                "strategy_version_id": S,
                "opportunity_id": S,
                "expected_version": I,
                "to_stage": S,
                "to_status": S,
                "qualification": O,
                "decision_revision_id": S,
                "idempotency_key": S,
                "item_id": S,
                "owner": S,
                "lease_seconds": I,
                "dispositions": {"type": "array", "items": {
                    "type": "object",
                    "properties": {
                        "candidate_id": S,
                        "outcome": {"type": "string", "enum": ["research", "reject", "monitor"]},
                        "reason": S,
                        "subject": O,
                        "due_at": S,
                        "next_check_at": S,
                    },
                    "required": ["candidate_id", "outcome", "reason"],
                    "additionalProperties": False,
                    "allOf": [
                        {"if": {"properties": {"outcome": {"const": "monitor"}}, "required": ["outcome"]},
                         "then": {"required": ["candidate_id", "outcome", "reason", "next_check_at"]}},
                    ],
                }},
                "outcome": {"type": "string", "enum": ["promoted", "rejected", "monitoring"]},
                "result_refs": A,
                "next_check_at": S,
            },
            ["operation"],
        ) | {"allOf": [
            {"if": {"properties": {"operation": {"const": "work_claim"}}}, "then": {"required": ["operation", "item_id"]}},
            {"if": {"properties": {"operation": {"const": "triage_complete"}}}, "then": {"required": ["operation", "item_id", "dispositions"]}},
            {"if": {"properties": {"operation": {"const": "research_complete"}}}, "then": {"required": ["operation", "item_id", "outcome", "reason", "result_refs"]}},
            {"if": {"properties": {"operation": {"const": "research_complete"}, "outcome": {"const": "promoted"}}, "required": ["operation", "outcome"]}, "then": {"required": ["opportunity_id"]}},
            {"if": {"properties": {"operation": {"const": "research_complete"}, "outcome": {"const": "monitoring"}}, "required": ["operation", "outcome"]}, "then": {"required": ["next_check_at"]}},
        ]},
    ),
    "investment_transaction_update": (
        "登记账户和资产身份，记录、确认、冲销、对账金融事实，或确认人工账本连续性；只有 confirm 才改变真实组合。连续性确认不替代最终下单前的券商可用现金/持仓预检。",
        schema(
            {
                "operation": {
                    "type": "string",
                    "enum": [
                        "account_create", "asset_register", "record", "confirm", "reverse",
                        "reconcile", "continuity_confirm", "continuity_revoke",
                    ],
                },
                "name": S,
                "base_currency": S,
                "institution": S,
                "asset_type": S,
                "identifiers": O,
                "entry_id": S,
                "account_id": S,
                "entry_type": S,
                "occurred_at": S,
                "amount": {},
                "currency": S,
                "source": S,
                "asset_id": S,
                "quantity": {},
                "price": {},
                "fee": {},
                "settled_at": S,
                "external_id": S,
                "metadata": O,
                "as_of": S,
                "statement": RECONCILIATION_STATEMENT,
                "source_ref": S,
                "confirmed_at": S,
                "user_confirmation_ref": S,
                "reporting_commitment": {"type": "boolean"},
                "confirmation_id": S,
                "reason": S,
            },
            ["operation"],
        ),
    ),
    "investment_evidence_update": (
        "冻结可追溯来源证据或登记决策使用的市场快照；不会形成建议或交易。",
        schema(
            {
                "operation": {"type": "string", "enum": ["publish_source", "market_snapshot"]},
                "subject": O,
                "source": S,
                "source_group": S,
                "first_known_at": S,
                "observed_at": S,
                "published_at": S,
                "url": S,
                "claims": A,
                "evidence_type": {
                    "type": "string",
                    "enum": ["observed_fact", "official_disclosure", "validated_analysis", "predictive_signal"],
                },
                "content": S,
                "metadata": O,
                "supersedes": S,
                "asset_id": S,
                "metric": S,
                "value": {},
                "quality": S,
                "currency": S,
            },
            ["operation"],
        ),
    ),
    "investment_action_update": (
        "把可行动机会加入用户队列，或记录呈现、接受、拒绝、延后和关闭；接受不会成交。",
        schema(
            {
                "operation": {"type": "string", "enum": ["enqueue", "respond"]},
                "opportunity_id": S,
                "decision_revision_id": S,
                "manual_action_spec_id": S,
                "valid_until": S,
                "idempotency_key": S,
                "queue_id": S,
                "state": S,
                "reason": S,
                "snoozed_until": S,
                "attention_decision_id": S,
            },
            ["operation"],
        ),
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
    "investment_workflow_update": (
        "创建或修改主动日历、完成运行，并安全领取或结束唤醒信封。",
        schema(
            {
                "operation": {
                    "type": "string",
                    "enum": [
                        "schedule_create", "schedule_patch", "schedule_status",
                        "schedule_run_now", "run_complete", "run_cancel", "wake_claim",
                        "wake_complete",
                    ],
                },
                "name": S,
                "kind": S,
                "mission": S,
                "cadence": O,
                "scope": O,
                "policy": O,
                "origin": O,
                "timezone": S,
                "dispatch_type": S,
                "job_definition_id": S,
                "schedule_id": S,
                "expected_version": I,
                "changes": O,
                "status": S,
                "reason": S,
                "run_id": S,
                "success": {"type": "boolean"},
                "error": S,
                "owner": S,
                "lease_seconds": I,
                "outbox_id": S,
            },
            ["operation"],
        ),
    ),
    "investment_delivery_update": (
        "冻结或摘要发送任务结果，并记录通知门控、送达和反馈。",
        schema(
            {
                "operation": {
                    "type": "string",
                    "enum": [
                        "prepare", "digest_send", "attention_decide",
                        "attention_delivered", "attention_feedback",
                    ],
                },
                "delivery_id": S,
                "delivery_ids": A,
                "conclusion": {
                    "type": "string",
                    "enum": ["no_action", "action", "risk_action", "review_required", "insufficient_evidence", "system_degraded"],
                },
                "summary": S,
                "key_evidence": A,
                "next_step": S,
                "next_check_at": S,
                "source_refs": A,
                "topic": S,
                "materiality": S,
                "confidence": S,
                "reason": S,
                "event_id": S,
                "evidence": {"type": "array"},
                "requested_action": S,
                "attention_decision_id": S,
                "feedback": S,
                "note": S,
            },
            ["operation"],
        ),
    ),
    "investment_research_publish": (
        "把证据化研究冻结为不可变 Thesis，并可执行决策资格验证；不会自动形成 Decision 或交易。",
        schema(
            {
                "subject": O,
                "content": S,
                "evidence_manifest_ids": A,
                "knowledge_cutoff": S,
                "validation_spec": O,
            },
            ["subject", "content", "evidence_manifest_ids", "knowledge_cutoff"],
        ),
    ),
    "investment_decision_publish": (
        "冻结当前组合、约束、已验证研究、替代方案和风险结果为正式 Decision；行动判断必须同时通过研究与风险闸门，不会成交。",
        schema(
            {
                "subject": O,
                "content": S,
                "decision_kind": {
                    "type": "string",
                    "enum": ["action", "conditional_action", "no_action", "watch"],
                },
                "account_id": S,
                "as_of": S,
                "knowledge_cutoff": S,
                "valid_until": S,
                "thesis_revision_ids": A,
                "evidence_manifest_ids": A,
                "invalidators": A,
                "no_action": O,
                "alternatives": {"type": "array", "items": O},
                "prices": O,
                "risk_calculation_id": S,
                "research_validation_calculation_id": S,
                "execution_plan": O,
                "execution_sell_risk_calculation_id": S,
            },
            ["subject", "content", "decision_kind", "account_id", "as_of", "knowledge_cutoff", "valid_until", "thesis_revision_ids", "evidence_manifest_ids", "invalidators", "no_action", "alternatives"],
        ),
    ),
    "investment_action_plan": (
        "用当前确认组合、Mandate、行情和市场现实构建一份带确定性风险结果的人工行动方案。",
        schema(
            {
                "as_of": S,
                "account_id": S,
                "asset_id": S,
                "quantity": {},
                "price": {},
                "fee": {},
                "reality_spec": O,
                "market_snapshot_id": S,
                "max_market_age_seconds": I,
                "valid_until": S,
                "price_range": O,
                "average_daily_amount": {},
                "action_tier": {"type": "string", "enum": ["standard", "bounded"]},
                "validity_sessions": {"type": "integer", "enum": [5, 20, 60, 180]},
            },
            ["as_of", "account_id", "asset_id", "quantity", "price", "reality_spec", "market_snapshot_id", "max_market_age_seconds", "valid_until", "price_range"],
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
    "investment_brief_update": (
        "发布日周月简报、记录实际呈现，并计算过程指标或发布可追溯记分卡。",
        schema(
            {
                "operation": {
                    "type": "string",
                    "enum": ["publish", "presented", "metrics_calculate", "scorecard_publish"],
                },
                "brief_type": S,
                "period_key": S,
                "as_of": S,
                "conclusion": S,
                "payload": O,
                "source_refs": A,
                "idempotency_key": S,
                "program_id": S,
                "brief_id": S,
                "attention_decision_id": S,
                "period_start": S,
                "period_end": S,
                "metrics": {"type": "array", "items": O},
                "comparisons": {"type": "array", "items": O},
                "caveats": A,
            },
            ["operation"],
        ),
    ),
}


def active_tools(legacy_tools: dict[str, Any]) -> dict[str, Any]:
    profile = os.environ.get("COMPANION_MCP_PROFILE", "all")
    if profile == "investment":
        return INVESTMENT_TOOLS
    if profile == "admin":
        return legacy_tools
    if profile == "all":
        return {**INVESTMENT_TOOLS, **legacy_tools}
    raise CompanionError("COMPANION_MCP_PROFILE must be investment, admin or all")


def call_investment(companion, name: str, arguments: dict[str, Any], actor: str) -> Any:
    if name == "investment_home":
        return companion.investment.home()
    if name == "portfolio_context":
        return companion.investment.portfolio_context(**arguments)
    if name == "research_context":
        return companion.investment.research_context(**arguments)
    if name == "decision_context":
        return companion.investment.decision_context(**arguments)
    if name == "evaluation_context":
        return companion.investment.evaluation_context(**arguments)
    if name == "investment_program_context":
        return companion.investment.program_context(**arguments)
    if name == "investment_workflow_context":
        return companion.investment.workflow_context(**arguments)
    commands = companion.investment_commands
    if name == "investment_context_update":
        operation = arguments["operation"]
        return commands.context_update(
            operation=operation,
            **{key: value for key, value in arguments.items() if key != "operation"},
        )
    if name == "investment_program_update":
        operation = arguments["operation"]
        return commands.program_update(
            operation=operation,
            actor=actor,
            **{key: value for key, value in arguments.items() if key != "operation"},
        )
    if name == "investment_opportunity_update":
        operation = arguments["operation"]
        return commands.opportunity_update(
            operation=operation,
            actor=actor,
            **{key: value for key, value in arguments.items() if key != "operation"},
        )
    if name == "investment_transaction_update":
        operation = arguments["operation"]
        return commands.transaction_update(
            operation=operation,
            **{key: value for key, value in arguments.items() if key != "operation"},
        )
    if name == "investment_evidence_update":
        operation = arguments["operation"]
        return commands.evidence_update(
            operation=operation,
            **{key: value for key, value in arguments.items() if key != "operation"},
        )
    if name == "investment_action_update":
        operation = arguments["operation"]
        return commands.action_update(
            operation=operation,
            actor=actor,
            **{key: value for key, value in arguments.items() if key != "operation"},
        )
    if name == "investment_execution_update":
        operation = arguments["operation"]
        return commands.execution_update(
            operation=operation,
            actor=actor,
            **{key: value for key, value in arguments.items() if key != "operation"},
        )
    if name == "investment_workflow_update":
        operation = arguments["operation"]
        return commands.workflow_update(
            operation=operation,
            actor=actor,
            **{key: value for key, value in arguments.items() if key != "operation"},
        )
    if name == "investment_delivery_update":
        operation = arguments["operation"]
        return commands.delivery_update(
            operation=operation,
            **{key: value for key, value in arguments.items() if key != "operation"},
        )
    if name == "investment_research_publish":
        return commands.research_publish(**arguments)
    if name == "investment_decision_publish":
        return commands.decision_publish(**arguments)
    if name == "investment_action_plan":
        return commands.action_plan(**arguments)
    if name == "investment_performance_calculate":
        return commands.performance_calculate(**arguments)
    if name == "investment_review_publish":
        return commands.review_publish(**arguments)
    if name == "investment_brief_update":
        operation = arguments["operation"]
        return commands.brief_update(
            operation=operation,
            actor=actor,
            **{key: value for key, value in arguments.items() if key != "operation"},
        )
    raise CompanionError(f"unimplemented investment tool: {name}")
