from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from typing import Any, Mapping


PROVIDER_FORMAT = "investment-companion.capability-provider/v1"
HOME_INVARIANT = "investment_home.production_health.required/v1"
PORTFOLIO_LEDGER_INVARIANT = "portfolio_context.confirmed_ledger_only/v1"
PORTFOLIO_TRUTH_INVARIANT = "portfolio_context.truth_and_precision_explicit/v1"
CONTEXT_CONFIRMATION_INVARIANT = "investment_context_update.draft_requires_confirmation/v1"
TRANSACTION_CONFIRMATION_INVARIANT = (
    "investment_transaction_update.confirmed_ledger_only_changes_portfolio/v1"
)
RECONCILIATION_INVARIANT = "investment_transaction_update.reconciliation_never_autofills/v1"
CONTINUITY_INVARIANT = "investment_transaction_update.continuity_is_not_broker_sync/v1"
RESEARCH_BOUNDARY_INVARIANT = (
    "research.evidence_validation_thesis_never_auto_action/v1"
)
RESEARCH_WORK_INVARIANT = (
    "investment_opportunity_update.explicit_candidate_disposition/v1"
)
DECISION_RESEARCH_SEPARATION_INVARIANT = (
    "decision.research_validation_is_not_decision/v1"
)
RISK_GATE_BOUNDARY_INVARIANT = (
    "investment_action_plan.risk_gate_is_veto_not_thesis/v1"
)
ACTION_ACCEPTANCE_INVARIANT = (
    "action_card.acceptance_never_changes_portfolio/v1"
)
EXECUTION_CONFIRMATION_INVARIANT = (
    "investment_execution_update.confirmed_ledger_only_changes_portfolio/v1"
)
EXECUTION_RECONCILIATION_INVARIANT = (
    "investment_execution_update.full_scope_reconciliation_required/v1"
)
PERFORMANCE_CALCULATION_INVARIANT = (
    "investment_performance_calculate.outputs_are_kernel_calculated/v1"
)
REVIEW_IMMUTABILITY_INVARIANT = (
    "investment_review_publish.revisions_are_append_only/v1"
)
CHANGE_PROPOSAL_INVARIANT = (
    "investment_review_publish.change_proposals_are_inert/v1"
)
PROGRAM_CONFIRMATION_INVARIANT = (
    "investment_program.confirmation_and_versioning_required/v1"
)
PROGRAM_PROJECTION_INVARIANT = (
    "investment_program.references_context_without_owning_truth/v1"
)
BRIEF_NO_ACTION_INVARIANT = (
    "investment_brief.no_action_requires_resolved_obligations/v1"
)
BRIEF_PROJECTION_INVARIANT = (
    "investment_brief.references_calculations_without_owning_truth/v1"
)
WORKFLOW_RUN_DELIVERY_INVARIANT = (
    "investment_workflow.run_success_is_not_delivery/v1"
)
WORKFLOW_VERSION_INVARIANT = (
    "investment_workflow.schedule_mutations_require_current_version/v1"
)
WORKFLOW_WAKE_LEASE_INVARIANT = (
    "investment_workflow.wake_lease_is_exclusive/v1"
)
DELIVERY_STATE_INVARIANT = "investment_delivery.status_is_transport_receipt/v1"

HOME_DESCRIPTION = (
    "读取今天的行动、异常、研究工作队列、绩效和交付总入口；未完成研究返回 review_required。"
)
PORTFOLIO_CONTEXT_DESCRIPTION = (
    "读取 confirmed Ledger 重建的组合、待确认流水、当前个人约束、truth freshness 与精度边界。"
)
CONTEXT_UPDATE_DESCRIPTION = (
    "草拟或确认个人事实、投资约束和注意力策略；draft 不会自动生效。"
)
TRANSACTION_UPDATE_DESCRIPTION = (
    "登记账户和资产身份，记录、确认、冲销及对账金融事实，或管理人工账本连续性；"
    "只有 confirmed Ledger Entry 改变组合。"
)
RESEARCH_CONTEXT_DESCRIPTION = (
    "读取某标的或 work_item_id 对应的 ResearchRecord、Validation、研究义务、资格状态与下次检查。"
)
OPPORTUNITY_UPDATE_DESCRIPTION = (
    "领取并完成研究义务，逐项分流候选，或创建和推进 Opportunity；研究结果不会自动成为 Decision 或交易。"
)
EVIDENCE_UPDATE_DESCRIPTION = (
    "冻结带来源、时点和类型的 Evidence，或登记决策时点市场快照；不会自动形成建议或交易。"
)
RESEARCH_PUBLISH_DESCRIPTION = (
    "把 Evidence 冻结为不可变 Investment Thesis，并可执行 Research Validation；资格不会自动形成 Decision 或交易。"
)
DECISION_CONTEXT_DESCRIPTION = (
    "读取正式 Decision、Action Card 状态、失效原因和人工执行边界；接受 Action Card 不代表成交。"
)
DECISION_PUBLISH_DESCRIPTION = (
    "把当前组合、约束、已验证研究、替代方案和风险结果冻结为有期限的正式 Investment Decision；不会成交。"
)
ACTION_PLAN_DESCRIPTION = (
    "用当前确认组合、Mandate、行情和市场现实计算 standard 或 bounded 人工行动方案及确定性 Risk Gate。"
)
ACTION_UPDATE_DESCRIPTION = (
    "把可行动 Opportunity 加入 Action Card 队列，或记录呈现、接受、拒绝、延后和关闭；任何响应都不会成交。"
)
PROGRAM_CONTEXT_DESCRIPTION = (
    "读取当前或历史 Investment Program、不可变 revision、状态与确认 Context 引用。"
)
PROGRAM_UPDATE_DESCRIPTION = (
    "创建、修订、确认、暂停、恢复或归档 Investment Program；变更使用乐观版本，确认需要用户批准引用。"
)
BRIEF_UPDATE_DESCRIPTION = (
    "发布日周月 Brief、记录实际呈现、冻结过程指标或发布引用 Calculation 的 Scorecard；不会复制投资事实。"
)
EXECUTION_UPDATE_DESCRIPTION = (
    "记录人工 Execution、用户报告的券商订单与成交，或管理用户在券商 App 配置的条件策略；"
    "报告事实不会自动改变 Portfolio Ledger，只有用户确认的 Ledger Entry 会。"
)
EVALUATION_CONTEXT_DESCRIPTION = (
    "读取确定性 Performance Calculation、版本化 Review 与惰性 Change Proposal 摘要；"
    "不暴露 Calculation 公式或完整审计实现。"
)
PERFORMANCE_CALCULATE_DESCRIPTION = (
    "按确认账本、点时估值、现金流、费用、成交参考价和显式基准口径计算客观期间结果；"
    "收益、费用、滑点和回撤均由 Financial Kernel 生成。"
)
REVIEW_PUBLISH_DESCRIPTION = (
    "创建或追加发布引用 Calculation 与历史事实的 Review revision；Change Proposal 只等待后续验证，"
    "不会静默修改 Thesis、Investment Policy 或 Strategy Version。"
)
WORKFLOW_CONTEXT_DESCRIPTION = (
    "读取主动 Schedule、Run、Wake 关联的 Delivery，以及通用系统与诊断状态。"
)
WORKFLOW_UPDATE_DESCRIPTION = (
    "创建或按当前版本修改 Schedule，立即运行或结束 Run，并以独占租约领取和完成 Wake。"
)
DELIVERY_UPDATE_DESCRIPTION = (
    "冻结必报结果或摘要并记录 Attention 决定、实际送达与用户反馈；Run 成功不代表已送达。"
)

SET_LIKE_ARRAY_KEYS = {
    "enum",
    "errors",
    "invariants",
    "required",
    "required_outputs",
    "type",
    "workflows",
}


def _normalized_contract(value: Any, parent_key: str | None = None) -> Any:
    if isinstance(value, dict):
        return {
            key: _normalized_contract(item, key)
            for key, item in sorted(value.items())
        }
    if isinstance(value, list):
        items = [_normalized_contract(item) for item in value]
        if parent_key in SET_LIKE_ARRAY_KEYS:
            unique = {
                json.dumps(item, ensure_ascii=False, sort_keys=True, separators=(",", ":")): item
                for item in items
            }
            return [unique[key] for key in sorted(unique)]
        return items
    return value


def canonical_json(value: Any) -> str:
    """Return the contract format's deterministic JSON representation."""
    return json.dumps(
        _normalized_contract(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def content_digest(value: Any) -> str:
    return f"sha256:{hashlib.sha256(canonical_json(value).encode('utf-8')).hexdigest()}"


def object_schema(
    properties: dict[str, Any] | None = None,
    required: list[str] | None = None,
    *,
    additional_properties: bool | dict[str, Any] = False,
) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": properties or {},
        "required": required or [],
        "additionalProperties": additional_properties,
    }


def operation_schema(
    operation: str,
    properties: dict[str, Any],
    required: list[str],
) -> dict[str, Any]:
    return object_schema(
        {
            "operation": {"type": "string", "const": operation},
            **properties,
        },
        ["operation", *required],
    )


def operation_union(operations: Mapping[str, dict[str, Any]]) -> dict[str, Any]:
    variants: list[dict[str, Any]] = []
    for name in sorted(operations):
        schema = operations[name]["input_schema"]
        variants.extend(schema.get("oneOf", [schema]))
    return {"oneOf": variants}


def variant_schema(
    selector: str,
    value: str,
    properties: dict[str, Any],
    required: list[str],
) -> dict[str, Any]:
    return object_schema(
        {
            selector: {"type": "string", "const": value},
            **properties,
        },
        [selector, *required],
    )


S = {"type": "string"}
I = {"type": "integer"}
B = {"type": "boolean"}
O = {"type": "object", "additionalProperties": True}
A = {"type": "array"}
SA = {"type": "array", "items": S}
DECIMAL_MAP = {"type": "object", "additionalProperties": {}}

HOME_INPUT_SCHEMA = object_schema()

COMPATIBILITY_STATUS = {
    "type": "string",
    "enum": ["compatible", "degraded", "unverified", "not_applicable"],
}
SCOPE_STATUS = object_schema(
    {
        "status": COMPATIBILITY_STATUS,
        "incidents": {"type": "array", "items": {"type": "object"}},
    },
    ["status", "incidents"],
)
OPTIONAL_SCOPE_STATUS = object_schema(
    {
        "status": {
            "type": "string",
            "enum": [
                "compatible",
                "fallback",
                "unverified",
                "not_applicable",
            ],
        },
        "incidents": {"type": "array", "items": {"type": "object"}},
        "fallback": {},
    },
    ["status", "incidents", "fallback"],
)
PRODUCTION_HEALTH_SCHEMA = object_schema(
    {
        "ok": B,
        "applicable": B,
        "baseline": SCOPE_STATUS,
        "workflows": {"type": "object", "additionalProperties": SCOPE_STATUS},
        "optional_enhancements": {
            "type": "object",
            "additionalProperties": OPTIONAL_SCOPE_STATUS,
        },
        "incidents": {"type": "array", "items": {"type": "object"}},
        "provider_digest": {},
        "requirements_digest": {},
        "receipt_digest": {},
    },
    [
        "ok",
        "applicable",
        "baseline",
        "workflows",
        "optional_enhancements",
        "incidents",
        "provider_digest",
        "requirements_digest",
        "receipt_digest",
    ],
    additional_properties=True,
)
HOME_OUTPUT_SCHEMA = object_schema(
    {
        "schema": S,
        "as_of": S,
        "state": S,
        "message": S,
        "program": {},
        "actions": A,
        "deferred": A,
        "execution": {},
        "research": O,
        "evaluation": {},
        "workflow": O,
        "delivery": O,
        "claims": O,
        "production_health": PRODUCTION_HEALTH_SCHEMA,
    },
    [
        "schema",
        "as_of",
        "state",
        "message",
        "program",
        "actions",
        "deferred",
        "execution",
        "research",
        "evaluation",
        "workflow",
        "delivery",
        "claims",
        "production_health",
    ],
)

LEDGER_ENTRY_SCHEMA = object_schema(
    {
        "id": S,
        "account_id": S,
        "entry_type": S,
        "asset_id": {},
        "occurred_at": S,
        "settled_at": {},
        "quantity_text": {},
        "price_text": {},
        "amount_text": S,
        "currency": S,
        "fee_text": S,
        "status": {"type": "string", "enum": ["draft", "needs_confirmation", "confirmed", "reversed"]},
        "source": S,
        "external_id": {},
        "reversal_of": {},
        "metadata": O,
        "fingerprint": S,
        "created_at": S,
        "confirmed_at": {},
    },
    [
        "id",
        "account_id",
        "entry_type",
        "asset_id",
        "occurred_at",
        "settled_at",
        "quantity_text",
        "price_text",
        "amount_text",
        "currency",
        "fee_text",
        "status",
        "source",
        "external_id",
        "reversal_of",
        "metadata",
        "fingerprint",
        "created_at",
        "confirmed_at",
    ],
    additional_properties=True,
)
PENDING_LEDGER_ENTRY_SCHEMA = {
    **LEDGER_ENTRY_SCHEMA,
    "properties": {
        **LEDGER_ENTRY_SCHEMA["properties"],
        "status": {"type": "string", "const": "needs_confirmation"},
    },
}
CONFIRMED_LEDGER_ENTRY_SCHEMA = {
    **LEDGER_ENTRY_SCHEMA,
    "properties": {
        **LEDGER_ENTRY_SCHEMA["properties"],
        "status": {"type": "string", "const": "confirmed"},
    },
}
REVERSAL_LEDGER_ENTRY_SCHEMA = {
    **CONFIRMED_LEDGER_ENTRY_SCHEMA,
    "properties": {
        **CONFIRMED_LEDGER_ENTRY_SCHEMA["properties"],
        "entry_type": {"type": "string", "const": "reversal"},
        "reversal_of": S,
    },
}

PORTFOLIO_STATE_SCHEMA = object_schema(
    {
        "as_of": S,
        "account_id": S,
        "cash": DECIMAL_MAP,
        "positions": {
            "type": "array",
            "items": object_schema(
                {
                    "asset_id": S,
                    "name": S,
                    "quantity": S,
                    "price": {},
                    "market_value": {},
                    "currency": S,
                    "quality": {},
                    "observed_at": {},
                },
                ["asset_id", "name", "quantity", "price", "market_value", "currency", "quality", "observed_at"],
                additional_properties=True,
            ),
        },
        "total_by_currency": DECIMAL_MAP,
        "warnings": {"type": "array", "items": S},
        "calculation_id": S,
    },
    ["as_of", "account_id", "cash", "positions", "total_by_currency", "warnings", "calculation_id"],
    additional_properties=True,
)
TRUTH_FRESHNESS_SCHEMA = object_schema(
    {
        "verified_at": {},
        "reconciliation_id": {},
        "latest_reconciliation_as_of": {},
        "full_scope_matched": B,
        "latest_confirmed_ledger_at": {},
        "age_seconds": {},
        "stale_after_seconds": I,
        "reconciliation_stale": B,
        "status": {
            "type": "string",
            "enum": [
                "reconciliation_needs_review",
                "pending_transactions",
                "open_execution_preflight_required",
                "ledger_continuity_confirmed",
                "continuity_confirmation_required",
                "recently_reconciled",
            ],
        },
        "stale": B,
        "warning": {},
        "pending_transaction_count": I,
        "open_execution_count": I,
        "active_broker_strategy_count": I,
        "continuity_confirmation": {},
        "ledger_continuity_supported": B,
        "broker_position_recently_proven": B,
        "precise_position_advice_allowed": B,
        "required_action": S,
    },
    [
        "verified_at",
        "reconciliation_id",
        "latest_reconciliation_as_of",
        "full_scope_matched",
        "latest_confirmed_ledger_at",
        "age_seconds",
        "stale_after_seconds",
        "reconciliation_stale",
        "status",
        "stale",
        "warning",
        "pending_transaction_count",
        "open_execution_count",
        "active_broker_strategy_count",
        "continuity_confirmation",
        "ledger_continuity_supported",
        "broker_position_recently_proven",
        "precise_position_advice_allowed",
        "required_action",
    ],
    additional_properties=True,
)
PRECISION_BOUNDARY_SCHEMA = object_schema(
    {
        "current_broker_position_proven": B,
        "ledger_position_continuity_supported": B,
        "precise_position_advice_allowed": B,
        "conditional_position_advice_allowed": {"type": "boolean", "const": True},
        "market_revaluation_required": {"type": "boolean", "const": True},
        "market_moves_do_not_invalidate_quantities": {"type": "boolean", "const": True},
        "final_order_quantities_require_broker_preflight": {"type": "boolean", "const": True},
        "required_when_stale": S,
    },
    [
        "current_broker_position_proven",
        "ledger_position_continuity_supported",
        "precise_position_advice_allowed",
        "conditional_position_advice_allowed",
        "market_revaluation_required",
        "market_moves_do_not_invalidate_quantities",
        "final_order_quantities_require_broker_preflight",
        "required_when_stale",
    ],
)
PORTFOLIO_CONTEXT_INPUT_SCHEMA = object_schema({"account_id": S, "as_of": S, "prices": O})
PORTFOLIO_CONTEXT_OUTPUT_SCHEMA = object_schema(
    {
        "schema": {"type": "string", "const": "investment-companion.portfolio-context/v1"},
        "as_of": S,
        "account": object_schema(
            {"id": S, "name": S, "base_currency": S, "status": S},
            ["id", "name", "base_currency", "status"],
            additional_properties=True,
        ),
        "portfolio": PORTFOLIO_STATE_SCHEMA,
        "exposure": O,
        "pending_transactions": {"type": "array", "items": PENDING_LEDGER_ENTRY_SCHEMA},
        "investor": {"type": ["object", "null"]},
        "mandate": {"type": ["object", "null"]},
        "policy": {"type": ["object", "null"]},
        "truth": {"type": "string", "const": "confirmed_ledger_replay"},
        "truth_freshness": TRUTH_FRESHNESS_SCHEMA,
        "precision_boundary": PRECISION_BOUNDARY_SCHEMA,
    },
    [
        "schema",
        "as_of",
        "account",
        "portfolio",
        "exposure",
        "pending_transactions",
        "investor",
        "mandate",
        "policy",
        "truth",
        "truth_freshness",
        "precision_boundary",
    ],
)

CONTEXT_REVISION_SCHEMA = object_schema(
    {
        "id": S,
        "context_type": {"type": "string", "enum": ["investor", "mandate", "attention"]},
        "revision": I,
        "status": {"type": "string", "enum": ["draft", "current", "trial", "superseded", "expired"]},
        "content": O,
        "effective_from": {},
        "expires_at": {},
        "reason": {},
        "content_hash": S,
        "created_at": S,
        "confirmed_at": {},
    },
    [
        "id",
        "context_type",
        "revision",
        "status",
        "content",
        "effective_from",
        "expires_at",
        "reason",
        "content_hash",
        "created_at",
        "confirmed_at",
    ],
    additional_properties=True,
)
CONTEXT_DRAFT_OUTPUT_SCHEMA = {
    **CONTEXT_REVISION_SCHEMA,
    "properties": {
        **CONTEXT_REVISION_SCHEMA["properties"],
        "status": {"type": "string", "const": "draft"},
    },
}
CONTEXT_CONFIRM_OUTPUT_SCHEMA = {
    **CONTEXT_REVISION_SCHEMA,
    "properties": {
        **CONTEXT_REVISION_SCHEMA["properties"],
        "status": {"type": "string", "enum": ["current", "trial"]},
        "confirmed_at": S,
    },
}
CONTEXT_OPERATIONS = {
    "draft": {
        "input_schema": operation_schema(
            "draft",
            {
                "context_type": {"type": "string", "enum": ["investor", "mandate", "attention"]},
                "content": O,
                "reason": S,
                "effective_from": S,
                "expires_at": S,
            },
            ["context_type", "content", "reason"],
        ),
        "output_schema": CONTEXT_DRAFT_OUTPUT_SCHEMA,
    },
    "confirm": {
        "input_schema": operation_schema(
            "confirm",
            {"revision_id": S, "trial": B},
            ["revision_id"],
        ),
        "output_schema": CONTEXT_CONFIRM_OUTPUT_SCHEMA,
    },
}
CONTEXT_UPDATE_INPUT_SCHEMA = operation_union(CONTEXT_OPERATIONS)
CONTEXT_UPDATE_OUTPUT_SCHEMA = {
    "oneOf": [CONTEXT_DRAFT_OUTPUT_SCHEMA, CONTEXT_CONFIRM_OUTPUT_SCHEMA]
}

RECONCILIATION_STATEMENT_SCHEMA = object_schema(
    {
        "cash": DECIMAL_MAP,
        "positions": DECIMAL_MAP,
        "position_values": DECIMAL_MAP,
        "position_total_by_currency": DECIMAL_MAP,
        "total_by_currency": DECIMAL_MAP,
        "metadata": O,
    },
    ["cash", "positions", "position_values", "position_total_by_currency", "total_by_currency"],
)
ACCOUNT_SCHEMA = object_schema(
    {"id": S, "name": S, "base_currency": S, "status": S, "metadata": O, "created_at": S, "updated_at": S},
    ["id", "name", "base_currency", "status", "metadata", "created_at", "updated_at"],
    additional_properties=True,
)
ASSET_SCHEMA = object_schema(
    {"id": S, "asset_type": S, "name": S, "currency": S, "identifiers": O, "metadata": O, "created_at": S, "updated_at": S},
    ["id", "asset_type", "name", "currency", "identifiers", "metadata", "created_at", "updated_at"],
    additional_properties=True,
)
RECONCILIATION_OUTPUT_SCHEMA = object_schema(
    {
        "id": S,
        "status": {"type": "string", "enum": ["matched", "needs_review"]},
        "differences": {"type": "array", "items": O},
        "computed": O,
        "reconciliation": object_schema(
            {
                "schema": S,
                "required_scopes": {"type": "array", "items": S},
                "scope_status": O,
                "full_scope_matched": B,
            },
            ["schema", "required_scopes", "scope_status", "full_scope_matched"],
            additional_properties=True,
        ),
    },
    ["id", "status", "differences", "computed", "reconciliation"],
    additional_properties=True,
)
CONTINUITY_SCHEMA = object_schema(
    {
        "id": S,
        "account_id": S,
        "anchor_reconciliation_id": S,
        "anchor_reconciliation_as_of": S,
        "status": {"type": "string", "enum": ["active", "superseded", "revoked"]},
        "scopes": {"type": "array", "items": S},
        "reporting_commitment": B,
        "user_confirmation_ref": S,
        "confirmed_ledger_hash": S,
        "confirmed_at": S,
        "created_at": S,
    },
    [
        "id",
        "account_id",
        "anchor_reconciliation_id",
        "anchor_reconciliation_as_of",
        "status",
        "scopes",
        "reporting_commitment",
        "user_confirmation_ref",
        "confirmed_ledger_hash",
        "confirmed_at",
        "created_at",
    ],
    additional_properties=True,
)
ACTIVE_CONTINUITY_SCHEMA = {
    **CONTINUITY_SCHEMA,
    "properties": {
        **CONTINUITY_SCHEMA["properties"],
        "status": {"type": "string", "const": "active"},
        "reporting_commitment": {"type": "boolean", "const": True},
    },
}
REVOKED_CONTINUITY_SCHEMA = {
    **CONTINUITY_SCHEMA,
    "properties": {
        **CONTINUITY_SCHEMA["properties"],
        "status": {"type": "string", "const": "revoked"},
        "revoked_at": S,
        "revoke_reason": S,
    },
    "required": [*CONTINUITY_SCHEMA["required"], "revoked_at", "revoke_reason"],
}
TRANSACTION_OPERATIONS = {
    "account_create": {
        "input_schema": operation_schema(
            "account_create",
            {"name": S, "base_currency": S, "institution": S, "metadata": O},
            ["name", "base_currency"],
        ),
        "output_schema": ACCOUNT_SCHEMA,
    },
    "asset_register": {
        "input_schema": operation_schema(
            "asset_register",
            {"asset_type": S, "name": S, "currency": S, "identifiers": O, "metadata": O},
            ["asset_type", "name", "currency", "identifiers"],
        ),
        "output_schema": ASSET_SCHEMA,
    },
    "record": {
        "input_schema": operation_schema(
            "record",
            {
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
            },
            ["account_id", "entry_type", "occurred_at", "amount", "currency", "source"],
        ),
        "output_schema": PENDING_LEDGER_ENTRY_SCHEMA,
    },
    "confirm": {
        "input_schema": operation_schema("confirm", {"entry_id": S}, ["entry_id"]),
        "output_schema": CONFIRMED_LEDGER_ENTRY_SCHEMA,
    },
    "reverse": {
        "input_schema": operation_schema(
            "reverse", {"entry_id": S, "reason": S, "occurred_at": S}, ["entry_id", "reason"]
        ),
        "output_schema": REVERSAL_LEDGER_ENTRY_SCHEMA,
    },
    "reconcile": {
        "input_schema": operation_schema(
            "reconcile",
            {"account_id": S, "as_of": S, "statement": RECONCILIATION_STATEMENT_SCHEMA, "source_ref": S},
            ["account_id", "as_of", "statement"],
        ),
        "output_schema": RECONCILIATION_OUTPUT_SCHEMA,
    },
    "continuity_confirm": {
        "input_schema": operation_schema(
            "continuity_confirm",
            {"account_id": S, "confirmed_at": S, "user_confirmation_ref": S, "reporting_commitment": B},
            ["account_id", "confirmed_at", "user_confirmation_ref", "reporting_commitment"],
        ),
        "output_schema": ACTIVE_CONTINUITY_SCHEMA,
    },
    "continuity_revoke": {
        "input_schema": operation_schema(
            "continuity_revoke", {"confirmation_id": S, "reason": S}, ["confirmation_id", "reason"]
        ),
        "output_schema": REVOKED_CONTINUITY_SCHEMA,
    },
}
TRANSACTION_UPDATE_INPUT_SCHEMA = operation_union(TRANSACTION_OPERATIONS)
TRANSACTION_UPDATE_OUTPUT_SCHEMA = {
    "oneOf": [TRANSACTION_OPERATIONS[name]["output_schema"] for name in sorted(TRANSACTION_OPERATIONS)]
}


NULLABLE_STRING = {"type": ["string", "null"]}
NULLABLE_OBJECT = {"type": ["object", "null"], "additionalProperties": True}

OPPORTUNITY_SUMMARY_SCHEMA = object_schema(
    {
        "id": S,
        "program_id": S,
        "subject": O,
        "stage": {"type": "string", "enum": ["observed", "researching", "qualified", "actionable"]},
        "status": {"type": "string", "enum": ["active", "rejected", "expired", "closed"]},
        "thesis_id": NULLABLE_STRING,
        "strategy_version_id": NULLABLE_STRING,
        "decision_revision_id": NULLABLE_STRING,
        "qualification": O,
        "version": I,
        "created_at": S,
        "updated_at": S,
        "closed_at": NULLABLE_STRING,
    },
    [
        "id", "program_id", "subject", "stage", "status", "thesis_id",
        "strategy_version_id", "decision_revision_id", "qualification", "version",
        "created_at", "updated_at", "closed_at",
    ],
    additional_properties=True,
)
OPPORTUNITY_SCHEMA = object_schema(
    {
        **OPPORTUNITY_SUMMARY_SCHEMA["properties"],
        "transitions": {"type": "array", "items": O},
        "research_validation": NULLABLE_OBJECT,
        "evidence_band": S,
    },
    [
        *OPPORTUNITY_SUMMARY_SCHEMA["required"],
        "transitions", "research_validation", "evidence_band",
    ],
    additional_properties=True,
)

RESEARCH_WORK_ITEM_SCHEMA = object_schema(
    {
        "id": S,
        "program_id": S,
        "work_type": {"type": "string", "enum": ["candidate_triage", "full_research"]},
        "status": {
            "type": "string",
            "enum": ["queued", "leased", "waiting", "monitoring", "completed", "rejected", "failed", "expired"],
        },
        "priority": I,
        "source_manifest_id": S,
        "parent_id": NULLABLE_STRING,
        "subject": O,
        "candidate_scope": SA,
        "requirements": O,
        "result_refs": SA,
        "disposition": {"type": ["object", "array"], "additionalProperties": True},
        "opportunity_id": NULLABLE_STRING,
        "due_at": S,
        "next_check_at": NULLABLE_STRING,
        "lease_owner": NULLABLE_STRING,
        "lease_until": NULLABLE_STRING,
        "attempt_count": I,
        "idempotency_key": S,
        "version": I,
        "last_error": NULLABLE_STRING,
        "created_at": S,
        "updated_at": S,
        "finished_at": NULLABLE_STRING,
    },
    [
        "id", "program_id", "work_type", "status", "priority", "source_manifest_id",
        "parent_id", "subject", "candidate_scope", "requirements", "result_refs",
        "disposition", "opportunity_id", "due_at", "next_check_at", "lease_owner",
        "lease_until", "attempt_count", "idempotency_key", "version", "last_error",
        "created_at", "updated_at", "finished_at",
    ],
    additional_properties=True,
)
RESEARCH_WORK_SUMMARY_SCHEMA = object_schema(
    {
        "counts": {"type": "object", "additionalProperties": I},
        "open": I,
        "overdue": I,
        "next": {"type": "array", "items": RESEARCH_WORK_ITEM_SCHEMA},
    },
    ["counts", "open", "overdue", "next"],
)

RESEARCH_RECORD_SCHEMA = object_schema(
    {
        "schema": {"type": "string", "const": "investment-companion.research-record/v1"},
        "record_id": S,
        "record_type": S,
        "created_at": NULLABLE_STRING,
        "as_of": NULLABLE_STRING,
        "method": object_schema(
            {
                "method_id": S,
                "registered_strategy_versions": A,
                "unregistered_strategy_labels": SA,
                "identity_status": {
                    "type": "string",
                    "enum": ["formal_strategy_version", "research_method_only"],
                },
            },
            ["method_id", "registered_strategy_versions", "unregistered_strategy_labels", "identity_status"],
            additional_properties=True,
        ),
        "items": A,
        "source_refs": SA,
        "validation": O,
    },
    ["schema", "record_id", "record_type", "created_at", "as_of", "method", "items", "source_refs", "validation"],
    additional_properties=True,
)
VALIDATION_CALCULATION_SCHEMA = object_schema(
    {
        "id": S,
        "kind": {"type": "string", "enum": ["thesis_validation", "strategy_validation"]},
        "purpose": S,
        "engine_version": S,
        "as_of": S,
        "inputs": O,
        "assumptions": O,
        "formulas": O,
        "outputs": object_schema(
            {
                "status": {
                    "type": "string",
                    "enum": ["research_only", "eligible_for_bounded_action", "eligible_for_shadow", "eligible_for_decision"],
                },
                "automatic_decision_or_execution": {"type": "boolean", "const": False},
            },
            ["status", "automatic_decision_or_execution"],
            additional_properties=True,
        ),
        "warnings": A,
        "reproducibility_hash": S,
        "created_at": S,
    },
    [
        "id", "kind", "purpose", "engine_version", "as_of", "inputs", "assumptions",
        "formulas", "outputs", "warnings", "reproducibility_hash", "created_at",
    ],
    additional_properties=True,
)
RESEARCH_CONTEXT_INPUT_SCHEMA = object_schema(
    {
        "subject_id": S,
        "work_item_id": S,
        "limit": {"type": "integer", "minimum": 1, "maximum": 100},
    }
)
RESEARCH_CONTEXT_OUTPUT_SCHEMA = object_schema(
    {
        "schema": {"type": "string", "const": "investment-companion.research-context/v1"},
        "as_of": S,
        "subject_id": NULLABLE_STRING,
        "opportunities": {"type": "array", "items": OPPORTUNITY_SUMMARY_SCHEMA},
        "strategies": A,
        "records": {"type": "array", "items": RESEARCH_RECORD_SCHEMA},
        "validations": {"type": "array", "items": VALIDATION_CALCULATION_SCHEMA},
        "work_queue": object_schema(
            {
                "summary": RESEARCH_WORK_SUMMARY_SCHEMA,
                "selected": {**RESEARCH_WORK_ITEM_SCHEMA, "type": ["object", "null"]},
                "items": {"type": "array", "items": RESEARCH_WORK_ITEM_SCHEMA},
            },
            ["summary", "selected", "items"],
        ),
        "boundary": object_schema(
            {
                "research_only_unless_validated": {"type": "boolean", "const": True},
                "formal_strategy_requires_registry_entry": {"type": "boolean", "const": True},
                "decision_requires_eligible_validation": {"type": "boolean", "const": True},
                "automatic_decision_or_execution": {"type": "boolean", "const": False},
                "may_produce": SA,
                "may_not_produce": SA,
                "action_requires": O,
            },
            [
                "research_only_unless_validated", "formal_strategy_requires_registry_entry",
                "decision_requires_eligible_validation", "automatic_decision_or_execution",
                "may_produce", "may_not_produce", "action_requires",
            ],
            additional_properties=True,
        ),
    },
    [
        "schema", "as_of", "subject_id", "opportunities", "strategies", "records",
        "validations", "work_queue", "boundary",
    ],
)

TRIAGE_RESEARCH_DISPOSITION = object_schema(
    {
        "candidate_id": S,
        "outcome": {"type": "string", "const": "research"},
        "reason": S,
        "subject": O,
        "due_at": S,
    },
    ["candidate_id", "outcome", "reason"],
)
TRIAGE_REJECT_DISPOSITION = object_schema(
    {
        "candidate_id": S,
        "outcome": {"type": "string", "const": "reject"},
        "reason": S,
    },
    ["candidate_id", "outcome", "reason"],
)
TRIAGE_MONITOR_DISPOSITION = object_schema(
    {
        "candidate_id": S,
        "outcome": {"type": "string", "const": "monitor"},
        "reason": S,
        "subject": O,
        "due_at": S,
        "next_check_at": S,
    },
    ["candidate_id", "outcome", "reason", "next_check_at"],
)
TRIAGE_DISPOSITION_SCHEMA = {
    "oneOf": [
        TRIAGE_RESEARCH_DISPOSITION,
        TRIAGE_REJECT_DISPOSITION,
        TRIAGE_MONITOR_DISPOSITION,
    ]
}
RESEARCH_COMPLETE_INPUT_SCHEMA = {
    "oneOf": [
        operation_schema(
            "research_complete",
            {
                "item_id": S, "outcome": {"type": "string", "const": "promoted"},
                "reason": S, "result_refs": SA, "opportunity_id": S, "owner": S,
            },
            ["item_id", "outcome", "reason", "result_refs", "opportunity_id"],
        ),
        operation_schema(
            "research_complete",
            {
                "item_id": S, "outcome": {"type": "string", "const": "rejected"},
                "reason": S, "result_refs": SA, "owner": S,
            },
            ["item_id", "outcome", "reason"],
        ),
        operation_schema(
            "research_complete",
            {
                "item_id": S, "outcome": {"type": "string", "const": "monitoring"},
                "reason": S, "result_refs": SA, "next_check_at": S, "owner": S,
            },
            ["item_id", "outcome", "reason", "next_check_at"],
        ),
    ]
}
RESEARCH_COMPLETION_WORK_ITEM_SCHEMA = object_schema(
    {
        **RESEARCH_WORK_ITEM_SCHEMA["properties"],
        "status": {"type": "string", "enum": ["completed", "rejected", "monitoring"]},
        "disposition": object_schema(
            {
                "outcome": {
                    "type": "string",
                    "enum": ["promoted", "rejected", "monitoring"],
                },
                "reason": S,
            },
            ["outcome", "reason"],
            additional_properties=True,
        ),
    },
    RESEARCH_WORK_ITEM_SCHEMA["required"],
    additional_properties=True,
)
CREATED_OPPORTUNITY_SCHEMA = object_schema(
    {
        **OPPORTUNITY_SCHEMA["properties"],
        "stage": {"type": "string", "const": "observed"},
        "status": {"type": "string", "const": "active"},
    },
    OPPORTUNITY_SCHEMA["required"],
    additional_properties=True,
)
CLAIMED_RESEARCH_WORK_ITEM_SCHEMA = object_schema(
    {
        **RESEARCH_WORK_ITEM_SCHEMA["properties"],
        "status": {"type": "string", "const": "leased"},
        "lease_owner": S,
        "lease_until": S,
    },
    RESEARCH_WORK_ITEM_SCHEMA["required"],
    additional_properties=True,
)
COMPLETED_TRIAGE_ITEM_SCHEMA = object_schema(
    {
        **RESEARCH_WORK_ITEM_SCHEMA["properties"],
        "status": {"type": "string", "const": "completed"},
        "disposition": A,
    },
    RESEARCH_WORK_ITEM_SCHEMA["required"],
    additional_properties=True,
)
OPPORTUNITY_OPERATIONS = {
    "create": {
        "input_schema": operation_schema(
            "create",
            {
                "subject": O, "evidence_refs": SA, "reason": S, "program_id": S,
                "thesis_id": S, "strategy_version_id": S,
            },
            ["subject", "evidence_refs", "reason"],
        ),
        "output_schema": CREATED_OPPORTUNITY_SCHEMA,
    },
    "transition": {
        "input_schema": operation_schema(
            "transition",
            {
                "opportunity_id": S, "expected_version": I,
                "to_stage": {"type": "string", "enum": ["observed", "researching", "qualified", "actionable"]},
                "to_status": {"type": "string", "enum": ["active", "rejected", "expired", "closed"]},
                "evidence_refs": SA, "reason": S, "qualification": O,
                "decision_revision_id": S, "idempotency_key": S,
            },
            ["opportunity_id", "expected_version", "to_stage", "to_status", "evidence_refs", "reason"],
        ),
        "output_schema": OPPORTUNITY_SCHEMA,
    },
    "work_claim": {
        "input_schema": operation_schema(
            "work_claim",
            {"item_id": S, "owner": S, "lease_seconds": {"type": "integer", "minimum": 60, "maximum": 7200}},
            ["item_id"],
        ),
        "output_schema": CLAIMED_RESEARCH_WORK_ITEM_SCHEMA,
    },
    "triage_complete": {
        "input_schema": operation_schema(
            "triage_complete",
            {
                "item_id": S,
                "owner": S,
                "dispositions": {"type": "array", "items": TRIAGE_DISPOSITION_SCHEMA},
            },
            ["item_id", "dispositions"],
        ),
        "output_schema": object_schema(
            {
                "item": COMPLETED_TRIAGE_ITEM_SCHEMA,
                "children": {"type": "array", "items": RESEARCH_WORK_ITEM_SCHEMA},
            },
            ["item", "children"],
        ),
    },
    "research_complete": {
        "input_schema": RESEARCH_COMPLETE_INPUT_SCHEMA,
        "output_schema": RESEARCH_COMPLETION_WORK_ITEM_SCHEMA,
    },
}
OPPORTUNITY_UPDATE_INPUT_SCHEMA = operation_union(OPPORTUNITY_OPERATIONS)
OPPORTUNITY_UPDATE_OUTPUT_SCHEMA = {
    "oneOf": [
        OPPORTUNITY_SCHEMA,
        RESEARCH_WORK_ITEM_SCHEMA,
        OPPORTUNITY_OPERATIONS["triage_complete"]["output_schema"],
    ]
}

EVIDENCE_MANIFEST_SCHEMA = object_schema(
    {
        "id": S,
        "kind": {"type": "string", "const": "investment_evidence"},
        "schema_version": {"type": "string", "const": "investment-companion.evidence/v1"},
        "manifest": object_schema(
            {
                "kind": {"type": "string", "const": "investment_evidence"},
                "schema_version": {"type": "string", "const": "investment-companion.evidence/v1"},
                "manifest": O,
                "supersedes": NULLABLE_STRING,
            },
            ["kind", "schema_version", "manifest", "supersedes"],
            additional_properties=True,
        ),
        "content_hash": S,
        "supersedes": NULLABLE_STRING,
        "status": {"type": "string", "enum": ["ready", "superseded"]},
        "created_at": S,
    },
    ["id", "kind", "schema_version", "manifest", "content_hash", "supersedes", "status", "created_at"],
    additional_properties=True,
)
MARKET_SNAPSHOT_SCHEMA = object_schema(
    {
        "id": S,
        "asset_id": S,
        "metric": S,
        "value_text": S,
        "currency": NULLABLE_STRING,
        "observed_at": S,
        "source": S,
        "quality": {
            "type": "string",
            "enum": ["healthy", "stale", "partial", "conflicting", "unauthorized", "failed", "unknown"],
        },
        "metadata": O,
        "fingerprint": S,
        "created_at": S,
    },
    ["id", "asset_id", "metric", "value_text", "currency", "observed_at", "source", "quality", "metadata", "fingerprint", "created_at"],
    additional_properties=True,
)
EVIDENCE_OPERATIONS = {
    "publish_source": {
        "input_schema": operation_schema(
            "publish_source",
            {
                "subject": O, "source": S, "source_group": S, "first_known_at": S,
                "observed_at": S, "published_at": S, "url": S, "claims": SA,
                "evidence_type": {
                    "type": "string",
                    "enum": ["observed_fact", "official_disclosure", "validated_analysis", "predictive_signal"],
                },
                "content": S, "metadata": O, "supersedes": S,
            },
            ["subject", "source", "source_group", "first_known_at", "observed_at", "claims", "evidence_type"],
        ),
        "output_schema": EVIDENCE_MANIFEST_SCHEMA,
    },
    "market_snapshot": {
        "input_schema": operation_schema(
            "market_snapshot",
            {
                "asset_id": S, "metric": S, "value": {}, "observed_at": S, "source": S,
                "quality": {
                    "type": "string",
                    "enum": ["healthy", "stale", "partial", "conflicting", "unauthorized", "failed", "unknown"],
                },
                "currency": S, "metadata": O,
            },
            ["asset_id", "metric", "value", "observed_at", "source"],
        ),
        "output_schema": MARKET_SNAPSHOT_SCHEMA,
    },
}
EVIDENCE_UPDATE_INPUT_SCHEMA = operation_union(EVIDENCE_OPERATIONS)
EVIDENCE_UPDATE_OUTPUT_SCHEMA = {
    "oneOf": [EVIDENCE_MANIFEST_SCHEMA, MARKET_SNAPSHOT_SCHEMA]
}

THESIS_SCHEMA = object_schema(
    {
        "id": S,
        "object_type": {"type": "string", "const": "thesis"},
        "subject": O,
        "status": {"type": "string", "const": "active"},
        "current_revision_id": S,
        "created_at": S,
        "updated_at": S,
    },
    ["id", "object_type", "subject", "status", "current_revision_id", "created_at", "updated_at"],
    additional_properties=True,
)
THESIS_REVISION_SCHEMA = object_schema(
    {
        "id": S,
        "object_id": S,
        "revision": I,
        "status": {"type": "string", "const": "published"},
        "path": S,
        "content_hash": S,
        "parent_id": NULLABLE_STRING,
        "knowledge_cutoff": S,
        "context_refs": O,
        "calculation_ids": SA,
        "metadata": object_schema(
            {
                "research_contract_version": {"type": "integer", "const": 1},
                "evidence_manifest_ids": SA,
                "research_only": {"type": "boolean", "const": True},
                "automatic_decision_or_execution": {"type": "boolean", "const": False},
            },
            ["research_contract_version", "evidence_manifest_ids", "research_only", "automatic_decision_or_execution"],
            additional_properties=True,
        ),
        "created_at": S,
    },
    [
        "id", "object_id", "revision", "status", "path", "content_hash", "parent_id",
        "knowledge_cutoff", "context_refs", "calculation_ids", "metadata", "created_at",
    ],
    additional_properties=True,
)
THESIS_VALIDATION_SCHEMA = object_schema(
    {
        "status": {
            "type": "string",
            "enum": ["research_only", "eligible_for_bounded_action", "eligible_for_decision"],
        },
        "validation_type": {"type": "string", "const": "thesis"},
        "subject": O,
        "thesis_revision_id": S,
        "evidence_manifest_ids": SA,
        "independent_source_count": I,
        "non_predictive_evidence_count": I,
        "checks": O,
        "failures": SA,
        "eligible_for_bounded_action": B,
        "automatic_decision_or_execution": {"type": "boolean", "const": False},
        "calculation_id": S,
    },
    [
        "status", "validation_type", "subject", "thesis_revision_id", "evidence_manifest_ids",
        "independent_source_count", "non_predictive_evidence_count", "checks", "failures",
        "eligible_for_bounded_action", "automatic_decision_or_execution", "calculation_id",
    ],
    additional_properties=True,
)
RESEARCH_PUBLISH_INPUT_SCHEMA = object_schema(
    {
        "subject": O,
        "content": S,
        "evidence_manifest_ids": {"type": "array", "items": S, "minItems": 1, "uniqueItems": True},
        "knowledge_cutoff": S,
        "validation_spec": O,
    },
    ["subject", "content", "evidence_manifest_ids", "knowledge_cutoff"],
)
RESEARCH_PUBLISH_OUTPUT_SCHEMA = object_schema(
    {
        "thesis": THESIS_SCHEMA,
        "revision": THESIS_REVISION_SCHEMA,
        "validation": {**THESIS_VALIDATION_SCHEMA, "type": ["object", "null"]},
    },
    ["thesis", "revision", "validation"],
)


DECISION_OBJECT_SCHEMA = object_schema(
    {
        "id": S,
        "object_type": {"type": "string", "const": "decision"},
        "subject": O,
        "status": {"type": "string", "const": "issued"},
        "current_revision_id": S,
        "created_at": S,
        "updated_at": S,
    },
    [
        "id", "object_type", "subject", "status", "current_revision_id",
        "created_at", "updated_at",
    ],
    additional_properties=True,
)
DECISION_METADATA_SCHEMA = object_schema(
    {
        "decision_contract_version": {"type": "integer", "const": 1},
        "decision_kind": {
            "type": "string",
            "enum": ["action", "conditional_action", "no_action", "watch"],
        },
        "action_tier": {},
        "valid_until": S,
        "invalidators": SA,
        "no_action": O,
        "alternatives": {"type": "array", "items": O},
        "source_refs": SA,
        "risk_calculation_id": {},
        "research_validation_calculation_id": {},
        "confirmed_ledger_hash": S,
        "human_execution_only": {"type": "boolean", "const": True},
        "automatic_trade": {"type": "boolean", "const": False},
        "execution_plan": {},
        "published_at": S,
    },
    [
        "decision_contract_version", "decision_kind", "action_tier", "valid_until",
        "invalidators", "no_action", "alternatives", "source_refs",
        "risk_calculation_id", "research_validation_calculation_id",
        "confirmed_ledger_hash", "human_execution_only", "automatic_trade",
        "execution_plan", "published_at",
    ],
    additional_properties=True,
)
DECISION_REVISION_SCHEMA = object_schema(
    {
        "id": S,
        "object_id": S,
        "revision": I,
        "status": {"type": "string", "const": "published"},
        "path": S,
        "content_hash": S,
        "parent_id": NULLABLE_STRING,
        "knowledge_cutoff": S,
        "context_refs": object_schema(
            {
                "investor_revision_id": S,
                "mandate_revision_id": S,
                "portfolio_calculation_id": S,
                "thesis_revision_ids": SA,
                "research_validation_calculation_id": {},
            },
            [
                "investor_revision_id", "mandate_revision_id",
                "portfolio_calculation_id", "thesis_revision_ids",
                "research_validation_calculation_id",
            ],
            additional_properties=True,
        ),
        "calculation_ids": SA,
        "metadata": DECISION_METADATA_SCHEMA,
        "created_at": S,
    },
    [
        "id", "object_id", "revision", "status", "path", "content_hash",
        "parent_id", "knowledge_cutoff", "context_refs", "calculation_ids",
        "metadata", "created_at",
    ],
    additional_properties=True,
)

DECISION_COMMON_PROPERTIES = {
    "subject": O,
    "content": S,
    "account_id": S,
    "as_of": S,
    "knowledge_cutoff": S,
    "valid_until": S,
    "thesis_revision_ids": {
        "type": "array", "items": S, "minItems": 1, "uniqueItems": True,
    },
    "evidence_manifest_ids": {
        "type": "array", "items": S, "minItems": 1, "uniqueItems": True,
    },
    "invalidators": {"type": "array", "items": S, "minItems": 1},
    "no_action": O,
    "alternatives": {"type": "array", "items": O, "minItems": 1},
    "prices": O,
}
DECISION_COMMON_REQUIRED = [
    "subject", "content", "decision_kind", "account_id", "as_of",
    "knowledge_cutoff", "valid_until", "thesis_revision_ids",
    "evidence_manifest_ids", "invalidators", "no_action", "alternatives",
]


def decision_input_schema(decision_kind: str, *, action: bool) -> dict[str, Any]:
    properties = {
        "decision_kind": {"type": "string", "const": decision_kind},
        **DECISION_COMMON_PROPERTIES,
    }
    required = list(DECISION_COMMON_REQUIRED)
    if action:
        properties.update(
            {
                "risk_calculation_id": S,
                "research_validation_calculation_id": S,
                "execution_plan": O,
                "execution_sell_risk_calculation_id": S,
            }
        )
        required.extend(
            ["risk_calculation_id", "research_validation_calculation_id"]
        )
    return object_schema(properties, required)


DECISION_PUBLISH_OUTPUT_SCHEMA = object_schema(
    {
        "decision": DECISION_OBJECT_SCHEMA,
        "revision": DECISION_REVISION_SCHEMA,
        "portfolio_calculation_id": S,
        "risk_calculation_id": {},
        "research_validation_calculation_id": {},
    },
    [
        "decision", "revision", "portfolio_calculation_id",
        "risk_calculation_id", "research_validation_calculation_id",
    ],
    additional_properties=True,
)
DECISION_OPERATIONS = {
    "standard_action": {
        "input_schema": decision_input_schema("action", action=True),
        "output_schema": DECISION_PUBLISH_OUTPUT_SCHEMA,
    },
    "bounded_action": {
        "input_schema": decision_input_schema("conditional_action", action=True),
        "output_schema": DECISION_PUBLISH_OUTPUT_SCHEMA,
    },
    "no_action": {
        "input_schema": decision_input_schema("no_action", action=False),
        "output_schema": DECISION_PUBLISH_OUTPUT_SCHEMA,
    },
    "watch": {
        "input_schema": decision_input_schema("watch", action=False),
        "output_schema": DECISION_PUBLISH_OUTPUT_SCHEMA,
    },
}
DECISION_PUBLISH_INPUT_SCHEMA = operation_union(DECISION_OPERATIONS)

RISK_GATE_RESULT_SCHEMA = object_schema(
    {
        "status": {"type": "string", "enum": ["pass", "blocked"]},
        "blocked": B,
        "violations": {"type": "array", "items": O},
        "trade_impact_calculation_id": S,
        "portfolio_calculation_id": S,
        "market_snapshot_id": {},
        "metrics": O,
        "calculation_id": S,
    },
    [
        "status", "blocked", "violations", "trade_impact_calculation_id",
        "portfolio_calculation_id", "market_snapshot_id", "metrics",
        "calculation_id",
    ],
    additional_properties=True,
)
ACTION_PLAN_OUTPUT_SCHEMA = object_schema(
    {
        "schema": {
            "type": "string",
            "const": "investment-companion.portfolio-action-plan/v1",
        },
        "action": object_schema(
            {
                "account_id": S,
                "asset_id": S,
                "side": {"type": "string", "enum": ["buy", "sell"]},
                "quantity": S,
                "reference_price": S,
                "price_range": O,
                "valid_until": S,
                "validity_sessions": {},
                "quantity_status": {
                    "type": "string",
                    "enum": [
                        "finalizable_after_broker_preflight", "conditional_only",
                    ],
                },
            },
            [
                "account_id", "asset_id", "side", "quantity",
                "reference_price", "price_range", "valid_until",
                "validity_sessions", "quantity_status",
            ],
            additional_properties=True,
        ),
        "risk": RISK_GATE_RESULT_SCHEMA,
        "precision_boundary": PRECISION_BOUNDARY_SCHEMA,
        "truth_freshness": TRUTH_FRESHNESS_SCHEMA,
        "eligible_for_decision": B,
        "conditional_sizing_available": {"type": "boolean", "const": True},
        "decision_blockers": SA,
        "action_tier": {"type": "string", "enum": ["standard", "bounded"]},
        "eligible_for_conditional_decision": B,
        "automatic_decision_or_execution": {"type": "boolean", "const": False},
    },
    [
        "schema", "action", "risk", "precision_boundary", "truth_freshness",
        "eligible_for_decision", "conditional_sizing_available",
        "decision_blockers", "action_tier",
        "eligible_for_conditional_decision", "automatic_decision_or_execution",
    ],
    additional_properties=True,
)
ACTION_PLAN_COMMON_PROPERTIES = {
    "as_of": S,
    "account_id": S,
    "asset_id": S,
    "quantity": {},
    "price": {},
    "fee": {},
    "reality_spec": O,
    "market_snapshot_id": S,
    "max_market_age_seconds": {"type": "integer", "minimum": 1},
    "valid_until": S,
    "price_range": O,
    "average_daily_amount": {},
}
ACTION_PLAN_COMMON_REQUIRED = [
    "action_tier", "as_of", "account_id", "asset_id", "quantity", "price",
    "reality_spec", "market_snapshot_id", "max_market_age_seconds",
    "valid_until", "price_range",
]
ACTION_PLAN_OPERATIONS = {
    "standard": {
        "input_schema": object_schema(
            {
                "action_tier": {"type": "string", "const": "standard"},
                **ACTION_PLAN_COMMON_PROPERTIES,
            },
            ACTION_PLAN_COMMON_REQUIRED,
        ),
        "output_schema": ACTION_PLAN_OUTPUT_SCHEMA,
    },
    "bounded": {
        "input_schema": object_schema(
            {
                "action_tier": {"type": "string", "const": "bounded"},
                **ACTION_PLAN_COMMON_PROPERTIES,
                "validity_sessions": {
                    "type": "integer", "enum": [5, 20, 60, 180],
                },
            },
            [*ACTION_PLAN_COMMON_REQUIRED, "validity_sessions"],
        ),
        "output_schema": ACTION_PLAN_OUTPUT_SCHEMA,
    },
}
ACTION_PLAN_INPUT_SCHEMA = operation_union(ACTION_PLAN_OPERATIONS)

DECISION_QUEUE_SCHEMA = object_schema(
    {
        "id": S,
        "program_id": S,
        "opportunity_id": S,
        "decision_revision_id": S,
        "manual_action_spec_id": {},
        "state": {
            "type": "string",
            "enum": [
                "ready", "presented", "snoozed", "accepted", "rejected",
                "expired", "closed",
            ],
        },
        "version": I,
        "attention_decision_id": {},
        "valid_until": S,
        "idempotency_key": S,
        "response_reason": {},
        "presented_at": {},
        "snoozed_until": {},
        "responded_at": {},
        "created_at": S,
        "updated_at": S,
        "user_confirmation_ref": S,
    },
    [
        "id", "program_id", "opportunity_id", "decision_revision_id",
        "manual_action_spec_id", "state", "version", "attention_decision_id",
        "valid_until", "idempotency_key", "response_reason", "presented_at",
        "snoozed_until", "responded_at", "created_at", "updated_at",
    ],
    additional_properties=True,
)


def queue_output_schema(
    state: str, *, confirmation_required: bool = False
) -> dict[str, Any]:
    properties = {
        **DECISION_QUEUE_SCHEMA["properties"],
        "state": {"type": "string", "const": state},
    }
    required = list(DECISION_QUEUE_SCHEMA["required"])
    if confirmation_required:
        required.append("user_confirmation_ref")
    return object_schema(properties, required, additional_properties=True)


ACTION_UPDATE_OPERATIONS = {
    "enqueue": {
        "input_schema": operation_schema(
            "enqueue",
            {
                "opportunity_id": S,
                "decision_revision_id": S,
                "manual_action_spec_id": S,
                "valid_until": S,
                "idempotency_key": S,
            },
            ["opportunity_id", "decision_revision_id"],
        ),
        "output_schema": queue_output_schema("ready"),
    },
    "presented": {
        "input_schema": object_schema(
            {
                "operation": {"type": "string", "const": "respond"},
                "queue_id": S,
                "state": {"type": "string", "const": "presented"},
                "attention_decision_id": S,
            },
            ["operation", "queue_id", "state", "attention_decision_id"],
        ),
        "output_schema": queue_output_schema("presented"),
    },
    "accepted": {
        "input_schema": object_schema(
            {
                "operation": {"type": "string", "const": "respond"},
                "queue_id": S,
                "state": {"type": "string", "const": "accepted"},
                "user_confirmation_ref": S,
            },
            ["operation", "queue_id", "state", "user_confirmation_ref"],
        ),
        "output_schema": queue_output_schema("accepted", confirmation_required=True),
    },
    "rejected": {
        "input_schema": object_schema(
            {
                "operation": {"type": "string", "const": "respond"},
                "queue_id": S,
                "state": {"type": "string", "const": "rejected"},
                "reason": S,
                "user_confirmation_ref": S,
            },
            ["operation", "queue_id", "state", "reason", "user_confirmation_ref"],
        ),
        "output_schema": queue_output_schema("rejected", confirmation_required=True),
    },
    "snoozed": {
        "input_schema": object_schema(
            {
                "operation": {"type": "string", "const": "respond"},
                "queue_id": S,
                "state": {"type": "string", "const": "snoozed"},
                "reason": S,
                "snoozed_until": S,
                "user_confirmation_ref": S,
            },
            [
                "operation", "queue_id", "state", "reason", "snoozed_until",
                "user_confirmation_ref",
            ],
        ),
        "output_schema": queue_output_schema("snoozed", confirmation_required=True),
    },
    "closed": {
        "input_schema": object_schema(
            {
                "operation": {"type": "string", "const": "respond"},
                "queue_id": S,
                "state": {"type": "string", "const": "closed"},
                "reason": S,
            },
            ["operation", "queue_id", "state", "reason"],
        ),
        "output_schema": queue_output_schema("closed"),
    },
}
ACTION_UPDATE_INPUT_SCHEMA = operation_union(ACTION_UPDATE_OPERATIONS)
ACTION_UPDATE_OUTPUT_SCHEMA = DECISION_QUEUE_SCHEMA

ACTION_CARD_SCHEMA = object_schema(
    {
        "schema": {"type": "string", "const": "investment-companion.action-card/v1"},
        "queue_id": S,
        "state": {"type": "string", "enum": ["ready", "presented", "snoozed", "accepted"]},
        "subject": O,
        "valid_until": S,
        "evidence_band": S,
        "decision_revision_id": S,
        "action": O,
        "executable_now": B,
        "blocking_reasons": SA,
        "research_validation": O,
        "risk_gate": {"type": ["object", "null"]},
        "invalidators": SA,
        "no_action_alternative": O,
        "source_refs": SA,
        "human_execution_only": {"type": "boolean", "const": True},
        "execution_created": {"type": "boolean", "const": False},
        "guarantees": O,
    },
    [
        "schema", "queue_id", "state", "subject", "valid_until",
        "evidence_band", "decision_revision_id", "action", "executable_now",
        "blocking_reasons", "research_validation", "risk_gate", "invalidators",
        "no_action_alternative", "source_refs", "human_execution_only",
        "execution_created", "guarantees",
    ],
    additional_properties=True,
)
DECISION_CONTEXT_INPUT_SCHEMA = object_schema(
    {"limit": {"type": "integer", "minimum": 1, "maximum": 100}}
)
DECISION_CONTEXT_OUTPUT_SCHEMA = object_schema(
    {
        "schema": {"type": "string", "const": "investment-companion.decision-context/v1"},
        "as_of": S,
        "queue": {"type": "array", "items": DECISION_QUEUE_SCHEMA},
        "action_cards": {"type": "array", "items": ACTION_CARD_SCHEMA},
        "invalid": {"type": "array", "items": O},
        "recent_decisions": {"type": "array", "items": O},
        "executions": A,
        "execution_boundary": object_schema(
            {
                "mode": {"type": "string", "const": "human_manual_only"},
                "accepted_action_card_is_order": {"type": "boolean", "const": False},
                "reported_fill_changes_portfolio": {"type": "boolean", "const": False},
                "confirmed_ledger_fill_changes_portfolio": {"type": "boolean", "const": True},
            },
            [
                "mode", "accepted_action_card_is_order",
                "reported_fill_changes_portfolio",
                "confirmed_ledger_fill_changes_portfolio",
            ],
        ),
    },
    [
        "schema", "as_of", "queue", "action_cards", "invalid",
        "recent_decisions", "executions", "execution_boundary",
    ],
    additional_properties=True,
)

PROGRAM_CONTENT_SCHEMA = object_schema(
    {
        "objective": S,
        "success_criteria": SA,
        "benchmark": O,
        "risk_budget": O,
        "universe": O,
        "horizons": O,
        "operating_cadence": O,
        "stop_conditions": SA,
        "account_ids": SA,
    },
    [
        "objective",
        "success_criteria",
        "benchmark",
        "risk_budget",
        "universe",
        "horizons",
        "operating_cadence",
        "stop_conditions",
        "account_ids",
    ],
)
PROGRAM_CONTEXT_REFS_SCHEMA = object_schema(
    {
        "investor_revision_id": S,
        "mandate_revision_id": S,
        "attention_revision_id": S,
    },
    [
        "investor_revision_id",
        "mandate_revision_id",
        "attention_revision_id",
    ],
)
PROGRAM_REVISION_SCHEMA = object_schema(
    {
        "id": S,
        "program_id": S,
        "revision": I,
        "status": {
            "type": "string",
            "enum": ["draft", "trial", "current", "superseded", "expired"],
        },
        "content": PROGRAM_CONTENT_SCHEMA,
        "context_refs": PROGRAM_CONTEXT_REFS_SCHEMA,
        "parent_id": {},
        "reason": {},
        "effective_from": {},
        "expires_at": {},
        "user_approval_ref": {},
        "content_hash": S,
        "created_at": S,
        "confirmed_at": {},
    },
    [
        "id",
        "program_id",
        "revision",
        "status",
        "content",
        "context_refs",
        "parent_id",
        "reason",
        "effective_from",
        "expires_at",
        "user_approval_ref",
        "content_hash",
        "created_at",
        "confirmed_at",
    ],
    additional_properties=True,
)
PROGRAM_LIST_ITEM_SCHEMA = object_schema(
    {
        "id": S,
        "name": S,
        "status": {
            "type": "string",
            "enum": ["draft", "active", "paused", "superseded", "archived"],
        },
        "current_revision_id": {},
        "version": I,
        "activated_at": {},
        "closed_at": {},
        "created_at": S,
        "updated_at": S,
    },
    [
        "id",
        "name",
        "status",
        "current_revision_id",
        "version",
        "activated_at",
        "closed_at",
        "created_at",
        "updated_at",
    ],
    additional_properties=True,
)
PROGRAM_SCHEMA = object_schema(
    {
        **PROGRAM_LIST_ITEM_SCHEMA["properties"],
        "current_revision": {
            "oneOf": [PROGRAM_REVISION_SCHEMA, {"type": "null"}],
        },
        "revisions": {"type": "array", "items": PROGRAM_REVISION_SCHEMA},
    },
    [
        *PROGRAM_LIST_ITEM_SCHEMA["required"],
        "current_revision",
        "revisions",
    ],
    additional_properties=True,
)
PROGRAM_DRAFT_OUTPUT_SCHEMA = object_schema(
    {
        **PROGRAM_SCHEMA["properties"],
        "status": {"type": "string", "const": "draft"},
    },
    PROGRAM_SCHEMA["required"],
    additional_properties=True,
)
CONFIRMED_PROGRAM_REVISION_SCHEMA = object_schema(
    {
        **PROGRAM_REVISION_SCHEMA["properties"],
        "status": {"type": "string", "enum": ["current", "trial"]},
        "user_approval_ref": S,
        "confirmed_at": S,
    },
    PROGRAM_REVISION_SCHEMA["required"],
    additional_properties=True,
)
PROGRAM_CONFIRMED_OUTPUT_SCHEMA = object_schema(
    {
        **PROGRAM_SCHEMA["properties"],
        "status": {"type": "string", "const": "active"},
        "current_revision": CONFIRMED_PROGRAM_REVISION_SCHEMA,
    },
    PROGRAM_SCHEMA["required"],
    additional_properties=True,
)
PROGRAM_CONTEXT_INPUT_SCHEMA = object_schema(
    {
        "program_id": S,
        "status": {
            "type": "string",
            "enum": ["draft", "active", "paused", "superseded", "archived"],
        },
    }
)
PROGRAM_CONTEXT_OUTPUT_SCHEMA = object_schema(
    {
        "schema": {
            "type": "string",
            "const": "investment-companion.program-context/v1",
        },
        "as_of": S,
        "current": {"oneOf": [PROGRAM_SCHEMA, {"type": "null"}]},
        "selected": {"oneOf": [PROGRAM_SCHEMA, {"type": "null"}]},
        "programs": {"type": "array", "items": PROGRAM_LIST_ITEM_SCHEMA},
        "truth": {
            "type": "string",
            "const": "immutable_program_revisions_and_confirmed_context_refs",
        },
    },
    ["schema", "as_of", "current", "selected", "programs", "truth"],
)
PROGRAM_OPERATIONS = {
    "create": {
        "input_schema": operation_schema(
            "create",
            {
                "name": S,
                "content": PROGRAM_CONTENT_SCHEMA,
                "context_refs": PROGRAM_CONTEXT_REFS_SCHEMA,
                "reason": S,
                "expires_at": S,
            },
            ["name", "content", "context_refs", "reason"],
        ),
        "output_schema": PROGRAM_DRAFT_OUTPUT_SCHEMA,
    },
    "revise": {
        "input_schema": operation_schema(
            "revise",
            {
                "program_id": S,
                "expected_version": I,
                "content": PROGRAM_CONTENT_SCHEMA,
                "context_refs": PROGRAM_CONTEXT_REFS_SCHEMA,
                "reason": S,
                "expires_at": S,
            },
            [
                "program_id",
                "expected_version",
                "content",
                "context_refs",
                "reason",
            ],
        ),
        "output_schema": PROGRAM_SCHEMA,
    },
    "confirm": {
        "input_schema": operation_schema(
            "confirm",
            {
                "revision_id": S,
                "user_approval_ref": S,
                "trial": B,
                "supersedes_program_id": S,
            },
            ["revision_id", "user_approval_ref"],
        ),
        "output_schema": PROGRAM_CONFIRMED_OUTPUT_SCHEMA,
    },
    "status": {
        "input_schema": {
            "oneOf": [
                operation_schema(
                    "status",
                    {
                        "program_id": S,
                        "expected_version": I,
                        "status": {"type": "string", "const": status},
                        "reason": S,
                    },
                    ["program_id", "expected_version", "status", "reason"],
                )
                for status in ("active", "paused", "archived")
            ]
        },
        "output_schema": PROGRAM_SCHEMA,
    },
}
PROGRAM_UPDATE_INPUT_SCHEMA = operation_union(PROGRAM_OPERATIONS)
PROGRAM_UPDATE_OUTPUT_SCHEMA = PROGRAM_SCHEMA

BRIEF_BASE_PAYLOAD_INPUT_PROPERTIES = {
    "summary": S,
    "what_changed": SA,
    "decision": S,
    "risks": SA,
    "next_check_at": S,
    "queue_item_ids": SA,
}
BRIEF_BASE_PAYLOAD_INPUT_REQUIRED = list(BRIEF_BASE_PAYLOAD_INPUT_PROPERTIES)


def brief_publish_input_schema(
    brief_type: str,
    extra_payload_properties: dict[str, Any] | None = None,
    extra_payload_required: list[str] | None = None,
) -> dict[str, Any]:
    payload_properties = {
        **BRIEF_BASE_PAYLOAD_INPUT_PROPERTIES,
        **(extra_payload_properties or {}),
    }
    payload_required = [
        *BRIEF_BASE_PAYLOAD_INPUT_REQUIRED,
        *(extra_payload_required or []),
    ]
    return operation_schema(
        "publish",
        {
            "brief_type": {"type": "string", "const": brief_type},
            "period_key": S,
            "as_of": S,
            "conclusion": {
                "type": "string",
                "enum": [
                    "no_action",
                    "action",
                    "review_required",
                    "insufficient_evidence",
                ],
            },
            "payload": object_schema(payload_properties, payload_required),
            "source_refs": SA,
            "idempotency_key": S,
            "program_id": S,
        },
        [
            "brief_type",
            "period_key",
            "as_of",
            "conclusion",
            "payload",
            "source_refs",
        ],
    )


EXECUTION_SNAPSHOT_REFERENCE_SCHEMA = object_schema(
    {
        "calculation_id": S,
        "program_id": S,
        "program_revision_id": {},
        "truth": {"type": "string", "const": "confirmed_ledger_replay"},
        "frozen": {"type": "boolean", "const": True},
    },
    [
        "calculation_id",
        "program_id",
        "program_revision_id",
        "truth",
        "frozen",
    ],
    additional_properties=True,
)
BRIEF_PAYLOAD_OUTPUT_SCHEMA = object_schema(
    {
        **BRIEF_BASE_PAYLOAD_INPUT_PROPERTIES,
        "program_progress": O,
        "research_pipeline": O,
        "scorecard_id": S,
        "lessons": SA,
        "proposed_changes": SA,
        "execution_snapshot": EXECUTION_SNAPSHOT_REFERENCE_SCHEMA,
    },
    [*BRIEF_BASE_PAYLOAD_INPUT_REQUIRED, "execution_snapshot"],
    additional_properties=True,
)
OPERATING_BRIEF_SCHEMA = object_schema(
    {
        "id": S,
        "program_id": S,
        "program_revision_id": S,
        "brief_type": {"type": "string", "enum": ["daily", "weekly", "monthly"]},
        "period_key": S,
        "revision": I,
        "as_of": S,
        "conclusion": {
            "type": "string",
            "enum": ["no_action", "action", "review_required", "insufficient_evidence"],
        },
        "payload": BRIEF_PAYLOAD_OUTPUT_SCHEMA,
        "source_refs": SA,
        "content_hash": S,
        "idempotency_key": S,
        "status": {"type": "string", "enum": ["ready", "presented", "superseded"]},
        "supersedes": {},
        "attention_decision_id": {},
        "presented_at": {},
        "created_at": S,
        "updated_at": S,
    },
    [
        "id",
        "program_id",
        "program_revision_id",
        "brief_type",
        "period_key",
        "revision",
        "as_of",
        "conclusion",
        "payload",
        "source_refs",
        "content_hash",
        "idempotency_key",
        "status",
        "supersedes",
        "attention_decision_id",
        "presented_at",
        "created_at",
        "updated_at",
    ],
    additional_properties=True,
)
PRESENTED_BRIEF_SCHEMA = object_schema(
    {
        **OPERATING_BRIEF_SCHEMA["properties"],
        "status": {"type": "string", "const": "presented"},
    },
    OPERATING_BRIEF_SCHEMA["required"],
    additional_properties=True,
)
PROGRAM_METRICS_SCHEMA = object_schema(
    {
        "period": object_schema({"start": S, "end": S}, ["start", "end"]),
        "opportunity_flow": O,
        "decision_flow": O,
        "brief_flow": O,
        "cohorts": O,
        "rates": O,
        "coverage": O,
        "calculation_id": S,
    },
    [
        "period",
        "opportunity_flow",
        "decision_flow",
        "brief_flow",
        "cohorts",
        "rates",
        "coverage",
        "calculation_id",
    ],
    additional_properties=True,
)
SCORECARD_METRIC_INPUT_SCHEMA = object_schema(
    {"name": S, "calculation_id": S, "output_path": S},
    ["name", "calculation_id", "output_path"],
)
SCORECARD_COMPARISON_INPUT_SCHEMA = object_schema(
    {
        "label": S,
        "left_metric": S,
        "right_metric": S,
        "interpretation": S,
    },
    ["label", "left_metric", "right_metric", "interpretation"],
)
SCORECARD_METRIC_OUTPUT_SCHEMA = object_schema(
    {
        **SCORECARD_METRIC_INPUT_SCHEMA["properties"],
        "value": {},
        "as_of": S,
        "engine_version": S,
    },
    [*SCORECARD_METRIC_INPUT_SCHEMA["required"], "value", "as_of", "engine_version"],
)
PROGRAM_SCORECARD_SCHEMA = object_schema(
    {
        "id": S,
        "program_id": S,
        "program_revision_id": S,
        "period_start": S,
        "period_end": S,
        "revision": I,
        "status": {"type": "string", "enum": ["ready", "insufficient_evidence"]},
        "metrics": {"type": "array", "items": SCORECARD_METRIC_OUTPUT_SCHEMA},
        "comparisons": {"type": "array", "items": SCORECARD_COMPARISON_INPUT_SCHEMA},
        "source_refs": SA,
        "caveats": SA,
        "content_hash": S,
        "supersedes": {},
        "created_at": S,
    },
    [
        "id",
        "program_id",
        "program_revision_id",
        "period_start",
        "period_end",
        "revision",
        "status",
        "metrics",
        "comparisons",
        "source_refs",
        "caveats",
        "content_hash",
        "supersedes",
        "created_at",
    ],
    additional_properties=True,
)
BRIEF_OPERATIONS = {
    "publish": {
        "input_schema": {
            "oneOf": [
                brief_publish_input_schema("daily"),
                brief_publish_input_schema(
                    "weekly",
                    {"program_progress": O, "research_pipeline": O},
                    ["program_progress", "research_pipeline"],
                ),
                brief_publish_input_schema(
                    "monthly",
                    {"scorecard_id": S, "lessons": SA, "proposed_changes": SA},
                    ["scorecard_id", "lessons", "proposed_changes"],
                ),
            ]
        },
        "output_schema": OPERATING_BRIEF_SCHEMA,
    },
    "presented": {
        "input_schema": operation_schema(
            "presented",
            {"brief_id": S, "attention_decision_id": S},
            ["brief_id", "attention_decision_id"],
        ),
        "output_schema": PRESENTED_BRIEF_SCHEMA,
    },
    "metrics_calculate": {
        "input_schema": operation_schema(
            "metrics_calculate",
            {"period_start": S, "period_end": S, "program_id": S},
            ["period_start", "period_end"],
        ),
        "output_schema": PROGRAM_METRICS_SCHEMA,
    },
    "scorecard_publish": {
        "input_schema": operation_schema(
            "scorecard_publish",
            {
                "period_start": S,
                "period_end": S,
                "metrics": {"type": "array", "items": SCORECARD_METRIC_INPUT_SCHEMA},
                "comparisons": {
                    "type": "array",
                    "items": SCORECARD_COMPARISON_INPUT_SCHEMA,
                },
                "source_refs": SA,
                "caveats": SA,
                "program_id": S,
            },
            [
                "period_start",
                "period_end",
                "metrics",
                "comparisons",
                "source_refs",
                "caveats",
            ],
        ),
        "output_schema": PROGRAM_SCORECARD_SCHEMA,
    },
}
BRIEF_UPDATE_INPUT_SCHEMA = operation_union(BRIEF_OPERATIONS)
BRIEF_UPDATE_OUTPUT_SCHEMA = {
    "oneOf": [
        OPERATING_BRIEF_SCHEMA,
        PROGRAM_METRICS_SCHEMA,
        PROGRAM_SCORECARD_SCHEMA,
    ]
}

DECIMAL_INPUT_SCHEMA = {"type": ["string", "integer", "number"]}
NULLABLE_DECIMAL_INPUT_SCHEMA = {
    "type": ["string", "integer", "number", "null"]
}
MONITORING_WINDOW_INPUT_SCHEMA = {
    "oneOf": [
        {"type": "null"},
        object_schema(
            {
                "weekdays": {
                    "type": "array",
                    "items": {"type": "integer", "minimum": 1, "maximum": 7},
                    "minItems": 1,
                    "maxItems": 7,
                    "uniqueItems": True,
                },
                "start": S,
                "end": S,
            },
            ["weekdays", "start", "end"],
        ),
    ]
}
CONDITION_TRIGGER_INPUT_SCHEMA = object_schema(
    {
        "direction": {"type": "string", "enum": ["cross_up", "cross_down"]},
        "monitor_price": DECIMAL_INPUT_SCHEMA,
    },
    ["direction", "monitor_price"],
)
CONDITION_ORDER_INPUT_SCHEMA = object_schema(
    {
        "price_type": {"type": "string", "enum": ["limit", "market"]},
        "price_instruction": {
            "type": "string",
            "enum": [
                "custom", "instant", "buy_1", "buy_2", "buy_3", "buy_4",
                "buy_5", "sell_1", "sell_2", "sell_3", "sell_4", "sell_5",
                "exchange_market_option",
            ],
        },
        "custom_price": NULLABLE_DECIMAL_INPUT_SCHEMA,
    },
    ["price_type", "price_instruction", "custom_price"],
)
HOLDING_FRACTION_INPUT_SCHEMA = object_schema(
    {
        "mode": {"type": "string", "const": "holding_fraction"},
        "fraction": {"type": "string", "enum": ["1", "1/2", "1/3", "1/4"]},
        "resolved_quantity": DECIMAL_INPUT_SCHEMA,
        "holding_quantity_at_configuration": DECIMAL_INPUT_SCHEMA,
    },
    [
        "mode", "fraction", "resolved_quantity",
        "holding_quantity_at_configuration",
    ],
)
SELL_QUANTITY_INPUT_SCHEMA = {
    "oneOf": [DECIMAL_INPUT_SCHEMA, HOLDING_FRACTION_INPUT_SCHEMA]
}
DELAY_CONFIRMATION_INPUT_SCHEMA = object_schema(
    {
        "mode": {"type": "string", "enum": ["consecutive", "cumulative"]},
        "count": {"type": "integer", "minimum": 2, "maximum": 20},
    },
    ["mode", "count"],
)
BRACKET_DELAY_CONFIRMATION_INPUT_SCHEMA = object_schema(
    {
        **DELAY_CONFIRMATION_INPUT_SCHEMA["properties"],
        "separate_take_profit_stop_loss_counters": {
            "type": "boolean",
            "const": True,
        },
    },
    ["mode", "count", "separate_take_profit_stop_loss_counters"],
)
BOUNDARY_INPUT_SCHEMA = object_schema(
    {
        "mode": {"type": "string", "enum": ["price", "percentage"]},
        "value": DECIMAL_INPUT_SCHEMA,
    },
    ["mode", "value"],
)
GRID_PRICE_RANGE_INPUT_SCHEMA = object_schema(
    {
        "lower": DECIMAL_INPUT_SCHEMA,
        "upper": DECIMAL_INPUT_SCHEMA,
        "out_of_range_behavior": {
            "type": "string",
            "enum": ["sleep", "terminate_and_liquidate"],
        },
    },
    ["lower", "upper", "out_of_range_behavior"],
)
GRID_POSITION_RANGE_INPUT_SCHEMA = object_schema(
    {
        "max_net_buy": DECIMAL_INPUT_SCHEMA,
        "max_net_sell": DECIMAL_INPUT_SCHEMA,
    },
    ["max_net_buy", "max_net_sell"],
)
STRATEGY_COMMON_SPEC_PROPERTIES = {
    "account_id": S,
    "asset_id": S,
    "validity_sessions": {
        "type": "integer",
        "enum": [5, 20, 60, 180],
    },
    "monitoring_window": MONITORING_WINDOW_INPUT_SCHEMA,
}
STRATEGY_COMMON_SPEC_REQUIRED = [
    "account_id", "asset_id", "validity_sessions", "monitoring_window",
]


def priced_strategy_spec_input_schema(*, allow_fraction: bool) -> dict[str, Any]:
    return object_schema(
        {
            **STRATEGY_COMMON_SPEC_PROPERTIES,
            "trigger": CONDITION_TRIGGER_INPUT_SCHEMA,
            "order": CONDITION_ORDER_INPUT_SCHEMA,
            "quantity": (
                SELL_QUANTITY_INPUT_SCHEMA
                if allow_fraction
                else DECIMAL_INPUT_SCHEMA
            ),
            "effective_trigger_band_pct": DECIMAL_INPUT_SCHEMA,
            "delay_confirmation": DELAY_CONFIRMATION_INPUT_SCHEMA,
        },
        [*STRATEGY_COMMON_SPEC_REQUIRED, "trigger", "order", "quantity"],
    )


BRACKET_STRATEGY_SPEC_INPUT_SCHEMA = object_schema(
    {
        **STRATEGY_COMMON_SPEC_PROPERTIES,
        "base_price": DECIMAL_INPUT_SCHEMA,
        "take_profit": BOUNDARY_INPUT_SCHEMA,
        "stop_loss": BOUNDARY_INPUT_SCHEMA,
        "order": CONDITION_ORDER_INPUT_SCHEMA,
        "quantity": SELL_QUANTITY_INPUT_SCHEMA,
        "effective_trigger_band_pct": DECIMAL_INPUT_SCHEMA,
        "delay_confirmation": BRACKET_DELAY_CONFIRMATION_INPUT_SCHEMA,
    },
    [
        *STRATEGY_COMMON_SPEC_REQUIRED, "base_price", "take_profit",
        "stop_loss", "order", "quantity",
    ],
)
GRID_STRATEGY_SPEC_INPUT_SCHEMA = object_schema(
    {
        **STRATEGY_COMMON_SPEC_PROPERTIES,
        "initial_reference_price": DECIMAL_INPUT_SCHEMA,
        "spacing_type": {"type": "string", "enum": ["difference", "percentage"]},
        "rise_sell_spacing": DECIMAL_INPUT_SCHEMA,
        "fall_buy_spacing": DECIMAL_INPUT_SCHEMA,
        "sell_order": CONDITION_ORDER_INPUT_SCHEMA,
        "buy_order": CONDITION_ORDER_INPUT_SCHEMA,
        "sell_quantity": DECIMAL_INPUT_SCHEMA,
        "buy_quantity": DECIMAL_INPUT_SCHEMA,
        "price_range": GRID_PRICE_RANGE_INPUT_SCHEMA,
        "position_range": GRID_POSITION_RANGE_INPUT_SCHEMA,
        "multiple_grid_order": B,
    },
    [
        *STRATEGY_COMMON_SPEC_REQUIRED, "initial_reference_price",
        "spacing_type", "rise_sell_spacing", "fall_buy_spacing", "sell_order",
        "buy_order", "sell_quantity", "buy_quantity", "price_range",
        "position_range", "multiple_grid_order",
    ],
)
STRATEGY_SPEC_INPUT_SCHEMAS = {
    "priced_buy": priced_strategy_spec_input_schema(allow_fraction=False),
    "priced_sell": priced_strategy_spec_input_schema(allow_fraction=True),
    "bracket_exit": BRACKET_STRATEGY_SPEC_INPUT_SCHEMA,
    "moving_grid": GRID_STRATEGY_SPEC_INPUT_SCHEMA,
}

EXECUTION_SCHEMA = object_schema(
    {
        "id": S,
        "decision_id": {"type": ["string", "null"]},
        "decision_revision_id": {"type": ["string", "null"]},
        "status": {
            "type": "string",
            "enum": [
                "proposed", "presented", "accepted", "rejected", "ordered",
                "partially_filled", "filled", "cancelled", "expired",
                "superseded", "deviated",
            ],
        },
        "details": O,
        "ledger_entry_ids": SA,
        "idempotency_key": {"type": ["string", "null"]},
        "status_reason": {"type": ["string", "null"]},
        "created_at": S,
        "updated_at": S,
    },
    [
        "id", "status", "details", "ledger_entry_ids", "idempotency_key",
        "created_at", "updated_at",
    ],
    additional_properties=True,
)
REPORTED_FILL_OUTPUT_SCHEMA = object_schema(
    {
        "execution": EXECUTION_SCHEMA,
        "pending_ledger_entry": PENDING_LEDGER_ENTRY_SCHEMA,
        "portfolio_changed": {"type": "boolean", "const": False},
        "requires_confirmation": {"type": "boolean", "const": True},
    },
    [
        "execution", "pending_ledger_entry", "portfolio_changed",
        "requires_confirmation",
    ],
)
CONFIRMED_FILL_OUTPUT_SCHEMA = object_schema(
    {
        "execution": EXECUTION_SCHEMA,
        "confirmed_ledger_entry": CONFIRMED_LEDGER_ENTRY_SCHEMA,
        "portfolio": PORTFOLIO_STATE_SCHEMA,
        "portfolio_changed": {"type": "boolean", "const": True},
        "truth": {"type": "string", "const": "confirmed_ledger_replay"},
    },
    [
        "execution", "confirmed_ledger_entry", "portfolio",
        "portfolio_changed", "truth",
    ],
)

PLAN_STATUS_SCHEMA = {
    "type": "string",
    "enum": [
        "draft", "presented", "accepted", "configured", "active", "sleeping",
        "termination_pending", "terminated", "reconciled", "exception",
        "expired", "cancelled",
    ],
}
ORDER_STATUS_SCHEMA = {
    "type": "string",
    "enum": [
        "triggered", "submitted", "partially_filled", "filled", "cancelled",
        "rejected", "unknown",
    ],
}
BROKER_VALIDITY_OUTPUT_SCHEMA = object_schema(
    {
        "sessions": {"type": "integer", "enum": [5, 20, 60, 180]},
        "valid_until": S,
        "broker_condition_ref": NULLABLE_STRING,
        "configured_at": NULLABLE_STRING,
    },
    ["sessions", "valid_until", "broker_condition_ref", "configured_at"],
)
BROKER_ORDER_SCHEMA = object_schema(
    {
        "id": S,
        "plan_id": S,
        "broker_order_ref": S,
        "execution_id": NULLABLE_STRING,
        "condition_leg": {"type": ["string", "null"]},
        "execution_link_state": {
            "type": "string",
            "enum": ["pending", "linked", "not_applicable"],
        },
        "side": {"type": "string", "enum": ["buy", "sell"]},
        "status": ORDER_STATUS_SCHEMA,
        "quantity_text": S,
        "submitted_quantity_text": S,
        "cancelled_quantity_text": S,
        "trigger_price_text": NULLABLE_STRING,
        "reference_price_before_text": NULLABLE_STRING,
        "reference_price_after_text": NULLABLE_STRING,
        "reference_update_reason": NULLABLE_STRING,
        "triggered_at": S,
        "updated_at": S,
    },
    [
        "id", "plan_id", "broker_order_ref", "execution_id", "condition_leg",
        "execution_link_state", "side", "status", "quantity_text",
        "submitted_quantity_text", "cancelled_quantity_text",
        "trigger_price_text", "reference_price_before_text",
        "reference_price_after_text", "reference_update_reason", "triggered_at",
        "updated_at",
    ],
    additional_properties=True,
)
BROKER_EVENT_SCHEMA = object_schema(
    {
        "id": S,
        "plan_id": S,
        "order_id": NULLABLE_STRING,
        "event_type": {
            "type": "string",
            "enum": [
                "configured", "activated", "sleep_entered", "sleep_exited",
                "triggered", "order_status", "reference_updated",
                "termination_requested", "terminated", "corporate_action",
                "reconciled", "exception", "correction",
            ],
        },
        "occurred_at": S,
        "payload": O,
        "idempotency_key": S,
        "created_at": S,
    },
    [
        "id", "plan_id", "order_id", "event_type", "occurred_at", "payload",
        "idempotency_key", "created_at",
    ],
    additional_properties=True,
)
NET_QUANTITIES_SCHEMA = object_schema(
    {
        "submitted_buy_less_cancelled_buy": S,
        "submitted_sell_less_cancelled_sell": S,
        "net_buy": S,
        "net_sell": S,
        "portfolio_truth": {
            "type": "string",
            "const": "confirmed Ledger fills only",
        },
    },
    [
        "submitted_buy_less_cancelled_buy",
        "submitted_sell_less_cancelled_sell",
        "net_buy",
        "net_sell",
        "portfolio_truth",
    ],
)
RECONCILIATION_REFERENCE_SCHEMA = {
    "type": ["object", "null"],
    "properties": {"reconciliation_id": S, "occurred_at": S},
    "required": ["reconciliation_id", "occurred_at"],
    "additionalProperties": False,
}
BROKER_PLAN_PROPERTIES = {
    "id": S,
    "program_id": S,
    "queue_id": S,
    "decision_revision_id": S,
    "broker": {"type": "string", "const": "cicc_wealth"},
    "plan_type": {
        "type": "string",
        "enum": ["priced_buy", "priced_sell", "bracket_exit", "moving_grid"],
    },
    "account_id": S,
    "asset_id": S,
    "spec": O,
    "semantics_version": S,
    "status": PLAN_STATUS_SCHEMA,
    "broker_condition_ref": NULLABLE_STRING,
    "valid_until": S,
    "content_hash": S,
    "idempotency_key": S,
    "status_reason": NULLABLE_STRING,
    "configured_at": NULLABLE_STRING,
    "terminated_at": NULLABLE_STRING,
    "current_reference_price_text": NULLABLE_STRING,
    "buy_direction_state": {"type": "string", "enum": ["active", "sleeping"]},
    "sell_direction_state": {"type": "string", "enum": ["active", "sleeping"]},
    "created_at": S,
    "updated_at": S,
    "broker_validity": BROKER_VALIDITY_OUTPUT_SCHEMA,
}
BROKER_PLAN_REQUIRED = list(BROKER_PLAN_PROPERTIES)
BROKER_PLAN_SUMMARY_SCHEMA = object_schema(
    BROKER_PLAN_PROPERTIES,
    BROKER_PLAN_REQUIRED,
    additional_properties=True,
)
BROKER_PLAN_DETAIL_SCHEMA = object_schema(
    {
        **BROKER_PLAN_PROPERTIES,
        "orders": {"type": "array", "items": BROKER_ORDER_SCHEMA},
        "events": {"type": "array", "items": BROKER_EVENT_SCHEMA},
        "net_quantities": NET_QUANTITIES_SCHEMA,
        "outstanding_orders": {"type": "array", "items": BROKER_ORDER_SCHEMA},
        "reported_execution_ids": {
            "type": "array",
            "items": S,
            "uniqueItems": True,
        },
        "reconciliation": RECONCILIATION_REFERENCE_SCHEMA,
    },
    [
        *BROKER_PLAN_REQUIRED, "orders", "events", "net_quantities",
        "outstanding_orders", "reported_execution_ids", "reconciliation",
    ],
    additional_properties=True,
)
BROKER_ORDER_REPORT_OUTPUT_SCHEMA = object_schema(
    {
        **BROKER_PLAN_DETAIL_SCHEMA["properties"],
        "reported_order_execution_id": NULLABLE_STRING,
    },
    [*BROKER_PLAN_DETAIL_SCHEMA["required"], "reported_order_execution_id"],
    additional_properties=True,
)
RECONCILED_BROKER_PLAN_OUTPUT_SCHEMA = object_schema(
    {
        **BROKER_PLAN_DETAIL_SCHEMA["properties"],
        "status": {"type": "string", "const": "reconciled"},
        "reconciliation": object_schema(
            {"reconciliation_id": S, "occurred_at": S},
            ["reconciliation_id", "occurred_at"],
        ),
    },
    BROKER_PLAN_DETAIL_SCHEMA["required"],
    additional_properties=True,
)


def strategy_create_input_schema(plan_type: str) -> dict[str, Any]:
    return operation_schema(
        "strategy_create",
        {
            "queue_id": S,
            "plan_type": {"type": "string", "const": plan_type},
            "spec": STRATEGY_SPEC_INPUT_SCHEMAS[plan_type],
            "valid_until": S,
            "idempotency_key": S,
        },
        ["queue_id", "plan_type", "spec", "valid_until", "idempotency_key"],
    )


STRATEGY_ORDER_COMMON_PROPERTIES = {
    "plan_id": S,
    "broker_order_ref": S,
    "side": {"type": "string", "enum": ["buy", "sell"]},
    "quantity": DECIMAL_INPUT_SCHEMA,
    "triggered_at": S,
    "trigger_price": DECIMAL_INPUT_SCHEMA,
    "reference_price_before": DECIMAL_INPUT_SCHEMA,
    "reference_price_after": DECIMAL_INPUT_SCHEMA,
    "rejection_reason": S,
    "cancelled_quantity": DECIMAL_INPUT_SCHEMA,
    "condition_leg": {
        "type": "string",
        "enum": ["take_profit", "stop_loss"],
    },
}


def strategy_order_input_schema(status: str) -> dict[str, Any]:
    required = [
        "plan_id", "broker_order_ref", "side", "quantity", "status",
        "triggered_at",
    ]
    if status in {"partially_filled", "cancelled"}:
        required.append("cancelled_quantity")
    if status == "rejected":
        required.append("rejection_reason")
    return operation_schema(
        "strategy_order_report",
        {
            **STRATEGY_ORDER_COMMON_PROPERTIES,
            "status": {"type": "string", "const": status},
        },
        required,
    )


EXECUTION_OPERATIONS = {
    "prepare": {
        "input_schema": operation_schema(
            "prepare", {"queue_id": S, "idempotency_key": S},
            ["queue_id", "idempotency_key"],
        ),
        "output_schema": EXECUTION_SCHEMA,
    },
    "order": {
        "input_schema": operation_schema(
            "order",
            {"execution_id": S, "broker_order_ref": S, "ordered_at": S},
            ["execution_id", "broker_order_ref", "ordered_at"],
        ),
        "output_schema": EXECUTION_SCHEMA,
    },
    "report_fill": {
        "input_schema": operation_schema(
            "report_fill",
            {
                "execution_id": S,
                "occurred_at": S,
                "quantity": DECIMAL_INPUT_SCHEMA,
                "price": DECIMAL_INPUT_SCHEMA,
                "fee": DECIMAL_INPUT_SCHEMA,
                "source": S,
                "external_id": S,
                "settled_at": S,
                "final": B,
            },
            ["execution_id", "occurred_at", "quantity", "price", "fee", "source"],
        ),
        "output_schema": REPORTED_FILL_OUTPUT_SCHEMA,
    },
    "confirm_fill": {
        "input_schema": operation_schema(
            "confirm_fill",
            {"execution_id": S, "entry_id": S, "final": B},
            ["execution_id", "entry_id", "final"],
        ),
        "output_schema": CONFIRMED_FILL_OUTPUT_SCHEMA,
    },
    "cancel": {
        "input_schema": operation_schema(
            "cancel", {"execution_id": S, "reason": S},
            ["execution_id", "reason"],
        ),
        "output_schema": EXECUTION_SCHEMA,
    },
    "strategy_create": {
        "input_schema": {
            "oneOf": [
                strategy_create_input_schema(plan_type)
                for plan_type in sorted(STRATEGY_SPEC_INPUT_SCHEMAS)
            ]
        },
        "output_schema": BROKER_PLAN_DETAIL_SCHEMA,
    },
    "strategy_configured": {
        "input_schema": operation_schema(
            "strategy_configured",
            {
                "plan_id": S,
                "broker_condition_ref": S,
                "configured_at": S,
                "broker_validity_sessions": {
                    "type": "integer",
                    "enum": [5, 20, 60, 180],
                },
                "broker_valid_until": S,
            },
            [
                "plan_id", "broker_condition_ref", "configured_at",
                "broker_validity_sessions", "broker_valid_until",
            ],
        ),
        "output_schema": BROKER_PLAN_DETAIL_SCHEMA,
    },
    "strategy_activate": {
        "input_schema": operation_schema(
            "strategy_activate", {"plan_id": S, "occurred_at": S},
            ["plan_id", "occurred_at"],
        ),
        "output_schema": BROKER_PLAN_DETAIL_SCHEMA,
    },
    "strategy_order_report": {
        "input_schema": {
            "oneOf": [
                strategy_order_input_schema(status)
                for status in (
                    "triggered", "submitted", "partially_filled", "filled",
                    "cancelled", "rejected", "unknown",
                )
            ]
        },
        "output_schema": BROKER_ORDER_REPORT_OUTPUT_SCHEMA,
    },
    "strategy_terminate_request": {
        "input_schema": operation_schema(
            "strategy_terminate_request",
            {"plan_id": S, "occurred_at": S, "reason": S},
            ["plan_id", "occurred_at", "reason"],
        ),
        "output_schema": BROKER_PLAN_DETAIL_SCHEMA,
    },
    "strategy_terminated": {
        "input_schema": operation_schema(
            "strategy_terminated",
            {"plan_id": S, "occurred_at": S, "reason": S},
            ["plan_id", "occurred_at", "reason"],
        ),
        "output_schema": BROKER_PLAN_DETAIL_SCHEMA,
    },
    "strategy_etf_dividend": {
        "input_schema": operation_schema(
            "strategy_etf_dividend",
            {"plan_id": S, "occurred_at": S, "corporate_action_ref": S},
            ["plan_id", "occurred_at", "corporate_action_ref"],
        ),
        "output_schema": BROKER_PLAN_DETAIL_SCHEMA,
    },
    "strategy_sleep": {
        "input_schema": operation_schema(
            "strategy_sleep",
            {
                "plan_id": S,
                "direction": {"type": "string", "enum": ["buy", "sell"]},
                "sleeping": B,
                "occurred_at": S,
                "reason": S,
            },
            ["plan_id", "direction", "sleeping", "occurred_at", "reason"],
        ),
        "output_schema": BROKER_PLAN_DETAIL_SCHEMA,
    },
    "strategy_exception": {
        "input_schema": operation_schema(
            "strategy_exception",
            {"plan_id": S, "occurred_at": S, "reason": S},
            ["plan_id", "occurred_at", "reason"],
        ),
        "output_schema": BROKER_PLAN_DETAIL_SCHEMA,
    },
    "strategy_reconcile": {
        "input_schema": operation_schema(
            "strategy_reconcile",
            {"plan_id": S, "occurred_at": S, "reconciliation_id": S},
            ["plan_id", "occurred_at", "reconciliation_id"],
        ),
        "output_schema": RECONCILED_BROKER_PLAN_OUTPUT_SCHEMA,
    },
}
EXECUTION_UPDATE_INPUT_SCHEMA = operation_union(EXECUTION_OPERATIONS)
EXECUTION_UPDATE_OUTPUT_SCHEMA = {
    "oneOf": [
        EXECUTION_SCHEMA,
        REPORTED_FILL_OUTPUT_SCHEMA,
        CONFIRMED_FILL_OUTPUT_SCHEMA,
        BROKER_PLAN_DETAIL_SCHEMA,
        BROKER_ORDER_REPORT_OUTPUT_SCHEMA,
    ]
}

NON_EMPTY_UNIQUE_STRINGS_SCHEMA = {
    "type": "array", "items": S, "minItems": 1, "uniqueItems": True,
}
UNIQUE_STRINGS_SCHEMA = {
    "type": "array", "items": S, "uniqueItems": True,
}
VALUATION_POINT_INPUT_SCHEMA = object_schema(
    {"at": S, "value": DECIMAL_INPUT_SCHEMA}, ["at", "value"]
)
TRADE_REFERENCE_PRICE_INPUT_SCHEMA = object_schema(
    {"price": DECIMAL_INPUT_SCHEMA, "source_ref": S},
    ["price", "source_ref"],
)
TRADE_REFERENCE_PRICES_INPUT_SCHEMA = {
    "type": "object",
    "additionalProperties": TRADE_REFERENCE_PRICE_INPUT_SCHEMA,
}
PERFORMANCE_COMMON_INPUT_PROPERTIES = {
    "account_id": S,
    "period_start": S,
    "period_end": S,
    "period_basis": {
        "type": "string", "const": "start_exclusive_end_inclusive",
    },
    "start_prices": DECIMAL_MAP,
    "end_prices": DECIMAL_MAP,
    "valuation_points": {
        "type": "array", "items": VALUATION_POINT_INPUT_SCHEMA,
    },
    "price_source_refs": NON_EMPTY_UNIQUE_STRINGS_SCHEMA,
    "trade_reference_prices": TRADE_REFERENCE_PRICES_INPUT_SCHEMA,
    "attribution_refs": UNIQUE_STRINGS_SCHEMA,
}
PERFORMANCE_COMMON_INPUT_REQUIRED = list(PERFORMANCE_COMMON_INPUT_PROPERTIES)
EXTERNAL_FLOW_SCHEMA = object_schema(
    {
        "ledger_entry_id": S,
        "occurred_at": S,
        "amount": S,
        "weight": S,
        "weighted_amount": S,
    },
    ["ledger_entry_id", "occurred_at", "amount", "weight", "weighted_amount"],
    additional_properties=True,
)
PERFORMANCE_TIME_BASIS_SCHEMA = object_schema(
    {
        "period_boundary": {
            "type": "string", "const": "start_exclusive_end_inclusive",
        },
        "valuation": {"type": "string", "const": "point_in_time"},
    },
    ["period_boundary", "valuation"],
    additional_properties=True,
)
PERFORMANCE_BENCHMARK_SCHEMA = object_schema(
    {
        "mode": {"type": "string", "enum": ["compare", "unavailable"]},
        "return": {"type": ["string", "null"]},
        "source_ref": {"type": ["string", "null"]},
        "unavailable_reason": {"type": ["string", "null"]},
    },
    ["mode", "return", "source_ref", "unavailable_reason"],
    additional_properties=True,
)
PERFORMANCE_COSTS_SCHEMA = object_schema(
    {
        "fees_and_taxes": S,
        "slippage": S,
        "total": S,
        "slippage_trade_count": I,
        "period_trade_count": I,
    },
    [
        "fees_and_taxes", "slippage", "total", "slippage_trade_count",
        "period_trade_count",
    ],
    additional_properties=True,
)
PERFORMANCE_ATTRIBUTION_SCHEMA = object_schema(
    {
        "reference_ids": UNIQUE_STRINGS_SCHEMA,
        "status": {
            "type": "string", "enum": ["linked", "insufficient_evidence"],
        },
    },
    ["reference_ids", "status"],
    additional_properties=True,
)
PERFORMANCE_OUTPUT_SCHEMA = object_schema(
    {
        "calculation_id": S,
        "period": object_schema(
            {"start": S, "end": S}, ["start", "end"],
            additional_properties=True,
        ),
        "time_basis": PERFORMANCE_TIME_BASIS_SCHEMA,
        "account_id": S,
        "currency": S,
        "start_value": S,
        "end_value": S,
        "net_external_flow": S,
        "weighted_external_flow": S,
        "investment_gain_after_external_flows": S,
        "modified_dietz_return": {"type": ["string", "null"]},
        "benchmark_return": {"type": ["string", "null"]},
        "excess_return": {"type": ["string", "null"]},
        "transaction_cost": S,
        "slippage_cost": S,
        "total_cost": S,
        "maximum_drawdown": S,
        "external_flows": {"type": "array", "items": EXTERNAL_FLOW_SCHEMA},
        "valuation_points": {
            "type": "array",
            "items": object_schema(
                {"at": S, "value": S}, ["at", "value"],
                additional_properties=True,
            ),
        },
        "attribution_refs": UNIQUE_STRINGS_SCHEMA,
        "attribution": PERFORMANCE_ATTRIBUTION_SCHEMA,
        "benchmark": PERFORMANCE_BENCHMARK_SCHEMA,
        "costs": PERFORMANCE_COSTS_SCHEMA,
        "warnings": SA,
        "caveats": SA,
    },
    [
        "calculation_id", "period", "time_basis", "account_id", "currency",
        "start_value", "end_value", "net_external_flow",
        "weighted_external_flow", "investment_gain_after_external_flows",
        "modified_dietz_return", "benchmark_return", "excess_return",
        "transaction_cost", "slippage_cost", "total_cost",
        "maximum_drawdown", "external_flows", "valuation_points",
        "attribution_refs", "attribution", "benchmark", "costs", "warnings",
        "caveats",
    ],
    additional_properties=True,
)
PERFORMANCE_OPERATIONS = {
    "compare": {
        "input_schema": variant_schema(
            "benchmark_mode", "compare",
            {
                **PERFORMANCE_COMMON_INPUT_PROPERTIES,
                "benchmark_start_value": DECIMAL_INPUT_SCHEMA,
                "benchmark_end_value": DECIMAL_INPUT_SCHEMA,
                "benchmark_source_ref": S,
            },
            [
                *PERFORMANCE_COMMON_INPUT_REQUIRED, "benchmark_start_value",
                "benchmark_end_value", "benchmark_source_ref",
            ],
        ),
        "output_schema": PERFORMANCE_OUTPUT_SCHEMA,
    },
    "unavailable": {
        "input_schema": variant_schema(
            "benchmark_mode", "unavailable",
            {
                **PERFORMANCE_COMMON_INPUT_PROPERTIES,
                "benchmark_unavailable_reason": S,
            },
            [*PERFORMANCE_COMMON_INPUT_REQUIRED, "benchmark_unavailable_reason"],
        ),
        "output_schema": PERFORMANCE_OUTPUT_SCHEMA,
    },
}
PERFORMANCE_CALCULATE_INPUT_SCHEMA = operation_union(PERFORMANCE_OPERATIONS)

CHANGE_PROPOSAL_ITEM_SCHEMA = object_schema(
    {
        "target_type": {
            "type": "string",
            "enum": ["thesis", "strategy", "policy", "research_pipeline"],
        },
        "target_id": S,
        "change": S,
        "reason": S,
        "validation_required": {"type": "boolean", "const": True},
    },
    ["target_type", "target_id", "change", "reason", "validation_required"],
)
REVIEW_COMMON_INPUT_PROPERTIES = {
    "subject": O,
    "content": S,
    "conclusion": {
        "type": "string",
        "enum": ["continue", "revise", "stop", "insufficient_evidence"],
    },
    "calculation_ids": NON_EMPTY_UNIQUE_STRINGS_SCHEMA,
    "source_refs": NON_EMPTY_UNIQUE_STRINGS_SCHEMA,
    "historical_refs": NON_EMPTY_UNIQUE_STRINGS_SCHEMA,
    "proposed_changes": {
        "type": "array", "items": CHANGE_PROPOSAL_ITEM_SCHEMA,
    },
    "knowledge_cutoff": S,
}
REVIEW_COMMON_INPUT_REQUIRED = [
    "subject", "content", "conclusion", "calculation_ids", "source_refs",
    "historical_refs", "knowledge_cutoff",
]
CHANGE_PROPOSAL_OUTPUT_SCHEMA = object_schema(
    {
        "status": {"type": "string", "enum": ["proposed", "none"]},
        "changes": {"type": "array", "items": CHANGE_PROPOSAL_ITEM_SCHEMA},
        "requires_new_version": B,
        "automatic_application": {"type": "boolean", "const": False},
    },
    ["status", "changes", "requires_new_version", "automatic_application"],
    additional_properties=True,
)
REVIEW_OBJECT_SCHEMA = object_schema(
    {
        "id": S,
        "object_type": {"type": "string", "const": "review"},
        "subject": O,
        "status": S,
        "current_revision_id": S,
        "created_at": S,
        "updated_at": S,
    },
    [
        "id", "object_type", "subject", "status", "current_revision_id",
        "created_at", "updated_at",
    ],
    additional_properties=True,
)
REVIEW_REVISION_SCHEMA = object_schema(
    {
        "id": S,
        "object_id": S,
        "revision": I,
        "status": {"type": "string", "const": "published"},
        "parent_id": {"type": ["string", "null"]},
        "knowledge_cutoff": S,
        "calculation_ids": NON_EMPTY_UNIQUE_STRINGS_SCHEMA,
        "metadata": O,
        "created_at": S,
    },
    [
        "id", "object_id", "revision", "status", "parent_id",
        "knowledge_cutoff", "calculation_ids", "metadata", "created_at",
    ],
    additional_properties=True,
)
REVIEW_PUBLISH_OUTPUT_SCHEMA = object_schema(
    {
        "review": REVIEW_OBJECT_SCHEMA,
        "revision": REVIEW_REVISION_SCHEMA,
        "conclusion": REVIEW_COMMON_INPUT_PROPERTIES["conclusion"],
        "change_proposal": CHANGE_PROPOSAL_OUTPUT_SCHEMA,
        "history_preserved": {"type": "boolean", "const": True},
        "automatic_changes_applied": {"type": "boolean", "const": False},
    },
    [
        "review", "revision", "conclusion", "change_proposal",
        "history_preserved", "automatic_changes_applied",
    ],
    additional_properties=True,
)
REVIEW_OPERATIONS = {
    "create": {
        "input_schema": operation_schema(
            "create", REVIEW_COMMON_INPUT_PROPERTIES,
            REVIEW_COMMON_INPUT_REQUIRED,
        ),
        "output_schema": REVIEW_PUBLISH_OUTPUT_SCHEMA,
    },
    "supersede": {
        "input_schema": operation_schema(
            "supersede",
            {
                **REVIEW_COMMON_INPUT_PROPERTIES,
                "review_id": S,
                "supersedes_revision_id": S,
                "supersession_reason": S,
            },
            [
                *REVIEW_COMMON_INPUT_REQUIRED, "review_id",
                "supersedes_revision_id", "supersession_reason",
            ],
        ),
        "output_schema": REVIEW_PUBLISH_OUTPUT_SCHEMA,
    },
}
REVIEW_PUBLISH_INPUT_SCHEMA = operation_union(REVIEW_OPERATIONS)

PERFORMANCE_CALCULATION_SUMMARY_SCHEMA = object_schema(
    {
        "id": S,
        "kind": {"type": "string", "const": "account_performance"},
        "as_of": S,
        "outputs": object_schema(
            {
                key: value
                for key, value in PERFORMANCE_OUTPUT_SCHEMA["properties"].items()
                if key != "calculation_id"
            },
            [
                key
                for key in PERFORMANCE_OUTPUT_SCHEMA["required"]
                if key != "calculation_id"
            ],
            additional_properties=True,
        ),
        "warnings": SA,
        "created_at": S,
    },
    ["id", "kind", "as_of", "outputs", "warnings", "created_at"],
    additional_properties=True,
)
REVIEW_SUMMARY_SCHEMA = object_schema(
    {
        **REVIEW_OBJECT_SCHEMA["properties"],
        "current_revision": REVIEW_REVISION_SCHEMA,
        "revisions": {"type": "array", "items": REVIEW_REVISION_SCHEMA},
    },
    [*REVIEW_OBJECT_SCHEMA["required"], "current_revision", "revisions"],
    additional_properties=True,
)
EVALUATION_CONTEXT_INPUT_SCHEMA = object_schema(
    {"limit": {"type": "integer", "minimum": 1, "maximum": 100}}
)
EVALUATION_CONTEXT_OUTPUT_SCHEMA = object_schema(
    {
        "schema": {
            "type": "string", "const": "investment-companion.evaluation-context/v1",
        },
        "as_of": S,
        "performance": {
            "type": "array", "items": PERFORMANCE_CALCULATION_SUMMARY_SCHEMA,
        },
        "reviews": {"type": "array", "items": REVIEW_SUMMARY_SCHEMA},
        "research_reviews": A,
        "change_boundary": object_schema(
            {
                "review_may_propose": {"type": "boolean", "const": True},
                "review_may_apply_strategy_change": {
                    "type": "boolean", "const": False,
                },
                "new_version_and_validation_required": {
                    "type": "boolean", "const": True,
                },
                "review_history_is_append_only": {
                    "type": "boolean", "const": True,
                },
            },
            [
                "review_may_propose", "review_may_apply_strategy_change",
                "new_version_and_validation_required",
                "review_history_is_append_only",
            ],
            additional_properties=True,
        ),
    },
    [
        "schema", "as_of", "performance", "reviews", "research_reviews",
        "change_boundary",
    ],
    additional_properties=True,
)

SCHEDULE_SCHEMA = object_schema(
    {
        "id": S,
        "name": S,
        "kind": {
            "type": "string",
            "enum": ["patrol", "review", "maintenance", "one_shot"],
        },
        "status": {
            "type": "string",
            "enum": ["active", "paused", "archived", "expired"],
        },
        "mission": S,
        "scope": O,
        "cadence": O,
        "policy": O,
        "origin": O,
        "timezone": S,
        "next_run_at": NULLABLE_STRING,
        "last_run_at": NULLABLE_STRING,
        "last_success_at": NULLABLE_STRING,
        "last_error": NULLABLE_STRING,
        "version": I,
        "created_at": S,
        "updated_at": S,
        "dispatch_type": {
            "type": "string",
            "enum": ["codex_turn", "deterministic_pipeline"],
        },
        "job_definition_id": NULLABLE_STRING,
    },
    [
        "id", "name", "kind", "status", "mission", "scope", "cadence",
        "policy", "origin", "timezone", "next_run_at", "version",
        "created_at", "updated_at", "dispatch_type", "job_definition_id",
    ],
    additional_properties=True,
)
RUN_SCHEMA = object_schema(
    {
        "id": S,
        "schedule_id": NULLABLE_STRING,
        "kind": S,
        "status": {
            "type": "string",
            "enum": [
                "queued", "leased", "succeeded", "failed", "cancelled",
                "recoverable",
            ],
        },
        "due_at": S,
        "idempotency_key": S,
        "payload": O,
        "attempt": I,
        "lease_owner": NULLABLE_STRING,
        "lease_until": NULLABLE_STRING,
        "started_at": NULLABLE_STRING,
        "finished_at": NULLABLE_STRING,
        "error": NULLABLE_STRING,
        "created_at": S,
        "dispatch_type": {
            "type": "string",
            "enum": ["codex_turn", "deterministic_pipeline"],
        },
        "job_run_id": NULLABLE_STRING,
    },
    [
        "id", "schedule_id", "kind", "status", "due_at", "idempotency_key",
        "payload", "attempt", "lease_owner", "lease_until", "started_at",
        "finished_at", "error", "created_at", "dispatch_type", "job_run_id",
    ],
    additional_properties=True,
)
DELIVERY_RESULT_SCHEMA = object_schema(
    {
        "schema": {
            "type": "string",
            "const": "investment-companion.result-envelope/v1",
        },
        "conclusion": {
            "type": "string",
            "enum": [
                "no_action", "action", "risk_action", "review_required",
                "insufficient_evidence", "system_degraded",
            ],
        },
        "summary": S,
        "key_evidence": {"type": "array", "items": S, "maxItems": 3},
        "next_step": S,
        "next_check_at": NULLABLE_STRING,
        "source_refs": SA,
    },
    [
        "schema", "conclusion", "summary", "key_evidence", "next_step",
        "next_check_at", "source_refs",
    ],
    additional_properties=True,
)
DELIVERY_RECORD_SCHEMA = object_schema(
    {
        "id": S,
        "run_id": S,
        "mode": {
            "type": "string",
            "enum": [
                "silent_allowed", "digest_required", "report_required",
                "action_required",
            ],
        },
        "status": {
            "type": "string",
            "enum": [
                "pending_content", "queued_digest", "pending_send", "sending",
                "delivered", "retry", "failed", "suppressed",
            ],
        },
        "destination": S,
        "result": O,
        "content_hash": NULLABLE_STRING,
        "outbox_id": NULLABLE_STRING,
        "attention_decision_id": NULLABLE_STRING,
        "idempotency_key": S,
        "due_at": NULLABLE_STRING,
        "available_at": NULLABLE_STRING,
        "delivered_at": NULLABLE_STRING,
        "last_error": NULLABLE_STRING,
        "created_at": S,
        "updated_at": S,
    },
    [
        "id", "run_id", "mode", "status", "destination", "result",
        "content_hash", "outbox_id", "attention_decision_id",
        "idempotency_key", "due_at", "available_at", "delivered_at",
        "last_error", "created_at", "updated_at",
    ],
    additional_properties=True,
)
ACTIVE_SCHEDULE_SCHEMA = object_schema(
    {
        **SCHEDULE_SCHEMA["properties"],
        "status": {"type": "string", "const": "active"},
    },
    SCHEDULE_SCHEMA["required"],
    additional_properties=True,
)
MUTATED_SCHEDULE_SCHEMA = object_schema(
    {
        **SCHEDULE_SCHEMA["properties"],
        "status": {
            "type": "string", "enum": ["active", "paused", "archived"],
        },
    },
    SCHEDULE_SCHEMA["required"],
    additional_properties=True,
)
COMPLETED_RUN_SCHEMA = object_schema(
    {
        **RUN_SCHEMA["properties"],
        "status": {"type": "string", "enum": ["succeeded", "failed"]},
    },
    RUN_SCHEMA["required"],
    additional_properties=True,
)
CANCELLED_RUN_SCHEMA = object_schema(
    {
        **RUN_SCHEMA["properties"],
        "status": {"type": "string", "const": "cancelled"},
    },
    RUN_SCHEMA["required"],
    additional_properties=True,
)
PREPARED_DELIVERY_RECORD_SCHEMA = object_schema(
    {
        **DELIVERY_RECORD_SCHEMA["properties"],
        "mode": {
            "type": "string",
            "enum": ["digest_required", "report_required", "action_required"],
        },
        "status": {
            "type": "string",
            "enum": [
                "queued_digest", "pending_send", "sending", "delivered",
                "retry", "failed",
            ],
        },
    },
    DELIVERY_RECORD_SCHEMA["required"],
    additional_properties=True,
)
DELIVERY_STATUS_SCHEMA = object_schema(
    {
        "counts": {"type": "object", "additionalProperties": I},
        "overdue_required": I,
        "now": S,
    },
    ["counts", "overdue_required", "now"],
    additional_properties=True,
)
SYSTEM_STATUS_SCHEMA = object_schema(
    {
        "ok": B,
        "integrity": S,
        "database": S,
        "meta": O,
        "migrations": A,
        "feature_flags": {},
        "counts": {"type": "object", "additionalProperties": I},
        "failed_runs": I,
        "pending_outbox": I,
        "delivery": DELIVERY_STATUS_SCHEMA,
        "now": S,
    },
    [
        "ok", "integrity", "database", "meta", "migrations", "feature_flags",
        "counts", "failed_runs", "pending_outbox", "delivery", "now",
    ],
    additional_properties=True,
)
DOCTOR_SCHEMA = object_schema(
    {
        "ok": B,
        "checks": {"type": "object", "additionalProperties": B},
        "warnings": SA,
        "production": O,
        "compatibility": O,
        "status": SYSTEM_STATUS_SCHEMA,
    },
    ["ok", "checks", "warnings", "production", "compatibility", "status"],
    additional_properties=True,
)

WORKFLOW_CONTEXT_OPERATIONS = {
    "schedules": {
        "input_schema": variant_schema(
            "view", "schedules",
            {
                "status": {
                    "type": "string",
                    "enum": ["active", "paused", "archived", "expired"],
                },
                "kind": {
                    "type": "string",
                    "enum": ["patrol", "review", "maintenance", "one_shot"],
                },
            },
            [],
        ),
        "output_schema": {"type": "array", "items": SCHEDULE_SCHEMA},
    },
    "schedule": {
        "input_schema": variant_schema(
            "view", "schedule", {"schedule_id": S}, ["schedule_id"]
        ),
        "output_schema": SCHEDULE_SCHEMA,
    },
    "schedule_history": {
        "input_schema": variant_schema(
            "view", "schedule_history",
            {"schedule_id": S, "limit": {"type": "integer", "minimum": 1, "maximum": 100}},
            ["schedule_id"],
        ),
        "output_schema": {"type": "array", "items": RUN_SCHEMA},
    },
    "runs": {
        "input_schema": variant_schema(
            "view", "runs",
            {
                "status": {
                    "type": "string",
                    "enum": [
                        "queued", "leased", "succeeded", "failed", "cancelled",
                        "recoverable",
                    ],
                },
                "limit": {"type": "integer", "minimum": 1, "maximum": 100},
            },
            [],
        ),
        "output_schema": {"type": "array", "items": RUN_SCHEMA},
    },
    "run": {
        "input_schema": variant_schema(
            "view", "run", {"run_id": S}, ["run_id"]
        ),
        "output_schema": RUN_SCHEMA,
    },
    "deliveries": {
        "input_schema": variant_schema(
            "view", "deliveries",
            {
                "status": {
                    "type": "string",
                    "enum": [
                        "pending_content", "queued_digest", "pending_send",
                        "sending", "delivered", "retry", "failed", "suppressed",
                    ],
                },
                "mode": {
                    "type": "string",
                    "enum": [
                        "silent_allowed", "digest_required", "report_required",
                        "action_required",
                    ],
                },
                "limit": {"type": "integer", "minimum": 1, "maximum": 500},
            },
            [],
        ),
        "output_schema": {"type": "array", "items": DELIVERY_RECORD_SCHEMA},
    },
    "delivery": {
        "input_schema": variant_schema(
            "view", "delivery", {"delivery_id": S}, ["delivery_id"]
        ),
        "output_schema": DELIVERY_RECORD_SCHEMA,
    },
    "delivery_status": {
        "input_schema": variant_schema("view", "delivery_status", {}, []),
        "output_schema": DELIVERY_STATUS_SCHEMA,
    },
    "system_status": {
        "input_schema": variant_schema("view", "system_status", {}, []),
        "output_schema": SYSTEM_STATUS_SCHEMA,
    },
    "doctor": {
        "input_schema": variant_schema("view", "doctor", {}, []),
        "output_schema": DOCTOR_SCHEMA,
    },
    "execution_strategies": {
        "input_schema": variant_schema(
            "view", "execution_strategies",
            {
                "status": PLAN_STATUS_SCHEMA,
                "limit": {"type": "integer", "minimum": 1, "maximum": 500},
            },
            [],
        ),
        "output_schema": {"type": "array", "items": BROKER_PLAN_SUMMARY_SCHEMA},
    },
    "execution_strategy": {
        "input_schema": variant_schema(
            "view", "execution_strategy", {"plan_id": S}, ["plan_id"]
        ),
        "output_schema": BROKER_PLAN_DETAIL_SCHEMA,
    },
}
WORKFLOW_CONTEXT_INPUT_SCHEMA = operation_union(WORKFLOW_CONTEXT_OPERATIONS)
WORKFLOW_CONTEXT_OUTPUT_SCHEMA = {
    "oneOf": [
        item["output_schema"] for item in WORKFLOW_CONTEXT_OPERATIONS.values()
    ]
}

WORKFLOW_UPDATE_OPERATIONS = {
    "schedule_create": {
        "input_schema": operation_schema(
            "schedule_create",
            {
                "name": S,
                "kind": {
                    "type": "string",
                    "enum": ["patrol", "review", "maintenance", "one_shot"],
                },
                "mission": S,
                "cadence": O,
                "scope": O,
                "policy": O,
                "origin": O,
                "timezone": S,
                "dispatch_type": {
                    "type": "string",
                    "enum": ["codex_turn", "deterministic_pipeline"],
                },
                "job_definition_id": S,
            },
            ["name", "kind", "mission", "cadence"],
        ),
        "output_schema": ACTIVE_SCHEDULE_SCHEMA,
    },
    "schedule_patch": {
        "input_schema": operation_schema(
            "schedule_patch",
            {"schedule_id": S, "expected_version": I, "changes": O, "reason": S},
            ["schedule_id", "expected_version", "changes"],
        ),
        "output_schema": SCHEDULE_SCHEMA,
    },
    "schedule_status": {
        "input_schema": {
            "oneOf": [
                operation_schema(
                    "schedule_status",
                    {
                        "schedule_id": S,
                        "expected_version": I,
                        "status": {"type": "string", "const": status},
                        "reason": S,
                    },
                    ["schedule_id", "expected_version", "status"],
                )
                for status in ("active", "paused", "archived")
            ]
        },
        "output_schema": MUTATED_SCHEDULE_SCHEMA,
    },
    "schedule_run_now": {
        "input_schema": operation_schema(
            "schedule_run_now", {"schedule_id": S}, ["schedule_id"]
        ),
        "output_schema": RUN_SCHEMA,
    },
    "run_complete": {
        "input_schema": {
            "oneOf": [
                operation_schema(
                    "run_complete",
                    {"run_id": S, "success": {"type": "boolean", "const": True}},
                    ["run_id", "success"],
                ),
                operation_schema(
                    "run_complete",
                    {
                        "run_id": S,
                        "success": {"type": "boolean", "const": False},
                        "error": S,
                    },
                    ["run_id", "success", "error"],
                ),
            ]
        },
        "output_schema": COMPLETED_RUN_SCHEMA,
    },
    "run_cancel": {
        "input_schema": operation_schema(
            "run_cancel", {"run_id": S, "reason": S}, ["run_id", "reason"]
        ),
        "output_schema": CANCELLED_RUN_SCHEMA,
    },
    "wake_claim": {
        "input_schema": operation_schema(
            "wake_claim",
            {
                "owner": S,
                "lease_seconds": {
                    "type": "integer", "minimum": 60, "maximum": 7200,
                },
            },
            ["owner"],
        ),
        "output_schema": {
            "oneOf": [
                object_schema(
                    {
                        "outbox_id": S,
                        "lease_owner": S,
                        "lease_until": S,
                        "envelope": object_schema(
                            {
                                "schema": S,
                                "type": {
                                    "type": "string",
                                    "enum": [
                                        "scheduled_run", "research_ready",
                                        "operating_brief_ready", "legacy_codex_turn",
                                    ],
                                },
                                "message": S,
                                "payload": O,
                                "event_id": NULLABLE_STRING,
                                "run_id": NULLABLE_STRING,
                                "job_run_id": NULLABLE_STRING,
                            },
                            ["schema", "type", "message", "payload", "event_id", "run_id", "job_run_id"],
                            additional_properties=True,
                        ),
                        "run": {"oneOf": [RUN_SCHEMA, {"type": "null"}]},
                    },
                    ["outbox_id", "lease_owner", "lease_until", "envelope", "run"],
                    additional_properties=True,
                ),
                {"type": "null"},
            ]
        },
    },
    "wake_complete": {
        "input_schema": {
            "oneOf": [
                operation_schema(
                    "wake_complete",
                    {
                        "outbox_id": S,
                        "owner": S,
                        "success": {"type": "boolean", "const": True},
                    },
                    ["outbox_id", "owner", "success"],
                ),
                operation_schema(
                    "wake_complete",
                    {
                        "outbox_id": S,
                        "owner": S,
                        "success": {"type": "boolean", "const": False},
                        "error": S,
                    },
                    ["outbox_id", "owner", "success", "error"],
                ),
            ]
        },
        "output_schema": object_schema(
            {
                "id": S,
                "kind": {"type": "string", "const": "codex_turn"},
                "status": {
                    "type": "string",
                    "enum": ["sent", "retry", "dead"],
                },
                "attempt": I,
                "lease_owner": NULLABLE_STRING,
                "lease_until": NULLABLE_STRING,
                "last_error": NULLABLE_STRING,
            },
            ["id", "kind", "status", "attempt", "lease_owner", "lease_until", "last_error"],
            additional_properties=True,
        ),
    },
}
WORKFLOW_UPDATE_INPUT_SCHEMA = operation_union(WORKFLOW_UPDATE_OPERATIONS)
WORKFLOW_UPDATE_OUTPUT_SCHEMA = {
    "oneOf": [item["output_schema"] for item in WORKFLOW_UPDATE_OPERATIONS.values()]
}

DELIVERY_ARGUMENT_PROPERTIES = {
    "conclusion": {
        "type": "string",
        "enum": [
            "no_action", "action", "risk_action", "review_required",
            "insufficient_evidence", "system_degraded",
        ],
    },
    "summary": S,
    "key_evidence": {"type": "array", "items": S, "maxItems": 3},
    "next_step": S,
    "next_check_at": S,
    "source_refs": SA,
}
ATTENTION_DECISION_SCHEMA = object_schema(
    {
        "id": S,
        "event_id": NULLABLE_STRING,
        "policy_revision_id": S,
        "action": S,
        "topic": S,
        "materiality": S,
        "confidence": S,
        "reason": S,
        "evidence": A,
        "notification_key": S,
        "status": S,
        "created_at": S,
        "delivered_at": NULLABLE_STRING,
    },
    [
        "id", "event_id", "policy_revision_id", "action", "topic",
        "materiality", "confidence", "reason", "evidence", "notification_key",
        "status", "created_at", "delivered_at",
    ],
    additional_properties=True,
)
ATTENTION_FEEDBACK_SCHEMA = object_schema(
    {
        "id": S,
        "attention_decision_id": S,
        "feedback": {
            "type": "string",
            "enum": [
                "useful", "not_useful", "false_positive", "too_late",
                "too_frequent",
            ],
        },
        "note": NULLABLE_STRING,
        "created_at": S,
        "policy_change": {
            "type": "string", "enum": ["none", "proposal_required"],
        },
    },
    [
        "id", "attention_decision_id", "feedback", "note", "created_at",
        "policy_change",
    ],
    additional_properties=True,
)
DELIVERY_UPDATE_OPERATIONS = {
    "prepare": {
        "input_schema": operation_schema(
            "prepare",
            {"delivery_id": S, **DELIVERY_ARGUMENT_PROPERTIES},
            ["delivery_id", "conclusion", "summary", "key_evidence", "next_step"],
        ),
        "output_schema": PREPARED_DELIVERY_RECORD_SCHEMA,
    },
    "digest_send": {
        "input_schema": operation_schema(
            "digest_send",
            {
                "delivery_ids": {
                    "type": "array", "items": S, "minItems": 1,
                    "uniqueItems": True,
                },
                **DELIVERY_ARGUMENT_PROPERTIES,
            },
            ["delivery_ids", "conclusion", "summary", "key_evidence", "next_step"],
        ),
        "output_schema": {"type": "array", "items": DELIVERY_RECORD_SCHEMA},
    },
    "attention_decide": {
        "input_schema": operation_schema(
            "attention_decide",
            {
                "topic": S,
                "materiality": S,
                "confidence": S,
                "reason": S,
                "event_id": S,
                "evidence": A,
                "requested_action": S,
            },
            ["topic", "materiality", "confidence", "reason"],
        ),
        "output_schema": ATTENTION_DECISION_SCHEMA,
    },
    "attention_delivered": {
        "input_schema": operation_schema(
            "attention_delivered",
            {"attention_decision_id": S},
            ["attention_decision_id"],
        ),
        "output_schema": ATTENTION_DECISION_SCHEMA,
    },
    "attention_feedback": {
        "input_schema": operation_schema(
            "attention_feedback",
            {
                "attention_decision_id": S,
                "feedback": {
                    "type": "string",
                    "enum": [
                        "useful", "not_useful", "false_positive", "too_late",
                        "too_frequent",
                    ],
                },
                "note": S,
            },
            ["attention_decision_id", "feedback"],
        ),
        "output_schema": ATTENTION_FEEDBACK_SCHEMA,
    },
}
DELIVERY_UPDATE_INPUT_SCHEMA = operation_union(DELIVERY_UPDATE_OPERATIONS)
DELIVERY_UPDATE_OUTPUT_SCHEMA = {
    "oneOf": [item["output_schema"] for item in DELIVERY_UPDATE_OPERATIONS.values()]
}


@dataclass(frozen=True)
class CapabilityContract:
    description: str
    handler: str
    input_schema: dict[str, Any]
    output_schema: dict[str, Any]
    errors: tuple[str, ...]
    invariants: tuple[str, ...]
    operations: Mapping[str, dict[str, Any]] | None = None
    actor_aware: bool = False
    variant_selectors: tuple[str, ...] = ("operation",)
    variant_aliases: Mapping[str, str] | None = None
    pending_variants: Mapping[str, tuple[str, ...]] | None = None


CAPABILITY_CONTRACTS: dict[str, CapabilityContract] = {
    "investment_home": CapabilityContract(
        HOME_DESCRIPTION,
        "investment.home",
        HOME_INPUT_SCHEMA,
        HOME_OUTPUT_SCHEMA,
        ("capability.input.invalid", "capability.output.invalid"),
        (HOME_INVARIANT,),
    ),
    "portfolio_context": CapabilityContract(
        PORTFOLIO_CONTEXT_DESCRIPTION,
        "investment.portfolio_context",
        PORTFOLIO_CONTEXT_INPUT_SCHEMA,
        PORTFOLIO_CONTEXT_OUTPUT_SCHEMA,
        ("capability.input.invalid", "capability.output.invalid"),
        (PORTFOLIO_LEDGER_INVARIANT, PORTFOLIO_TRUTH_INVARIANT),
    ),
    "investment_context_update": CapabilityContract(
        CONTEXT_UPDATE_DESCRIPTION,
        "investment_commands.context_update",
        CONTEXT_UPDATE_INPUT_SCHEMA,
        CONTEXT_UPDATE_OUTPUT_SCHEMA,
        ("capability.input.invalid", "capability.output.invalid"),
        (CONTEXT_CONFIRMATION_INVARIANT,),
        CONTEXT_OPERATIONS,
    ),
    "investment_transaction_update": CapabilityContract(
        TRANSACTION_UPDATE_DESCRIPTION,
        "investment_commands.transaction_update",
        TRANSACTION_UPDATE_INPUT_SCHEMA,
        TRANSACTION_UPDATE_OUTPUT_SCHEMA,
        ("capability.input.invalid", "capability.output.invalid"),
        (TRANSACTION_CONFIRMATION_INVARIANT, RECONCILIATION_INVARIANT, CONTINUITY_INVARIANT),
        TRANSACTION_OPERATIONS,
    ),
    "research_context": CapabilityContract(
        RESEARCH_CONTEXT_DESCRIPTION,
        "investment.research_context",
        RESEARCH_CONTEXT_INPUT_SCHEMA,
        RESEARCH_CONTEXT_OUTPUT_SCHEMA,
        ("capability.input.invalid", "capability.output.invalid"),
        (RESEARCH_BOUNDARY_INVARIANT,),
    ),
    "investment_opportunity_update": CapabilityContract(
        OPPORTUNITY_UPDATE_DESCRIPTION,
        "investment_commands.opportunity_update",
        OPPORTUNITY_UPDATE_INPUT_SCHEMA,
        OPPORTUNITY_UPDATE_OUTPUT_SCHEMA,
        ("capability.input.invalid", "capability.output.invalid"),
        (RESEARCH_BOUNDARY_INVARIANT, RESEARCH_WORK_INVARIANT),
        OPPORTUNITY_OPERATIONS,
        actor_aware=True,
    ),
    "investment_evidence_update": CapabilityContract(
        EVIDENCE_UPDATE_DESCRIPTION,
        "investment_commands.evidence_update",
        EVIDENCE_UPDATE_INPUT_SCHEMA,
        EVIDENCE_UPDATE_OUTPUT_SCHEMA,
        ("capability.input.invalid", "capability.output.invalid"),
        (RESEARCH_BOUNDARY_INVARIANT,),
        EVIDENCE_OPERATIONS,
    ),
    "investment_research_publish": CapabilityContract(
        RESEARCH_PUBLISH_DESCRIPTION,
        "investment_commands.research_publish",
        RESEARCH_PUBLISH_INPUT_SCHEMA,
        RESEARCH_PUBLISH_OUTPUT_SCHEMA,
        ("capability.input.invalid", "capability.output.invalid"),
        (RESEARCH_BOUNDARY_INVARIANT,),
    ),
    "decision_context": CapabilityContract(
        DECISION_CONTEXT_DESCRIPTION,
        "investment.decision_context",
        DECISION_CONTEXT_INPUT_SCHEMA,
        DECISION_CONTEXT_OUTPUT_SCHEMA,
        ("capability.input.invalid", "capability.output.invalid"),
        (DECISION_RESEARCH_SEPARATION_INVARIANT, ACTION_ACCEPTANCE_INVARIANT),
    ),
    "investment_decision_publish": CapabilityContract(
        DECISION_PUBLISH_DESCRIPTION,
        "investment_commands.decision_publish",
        DECISION_PUBLISH_INPUT_SCHEMA,
        DECISION_PUBLISH_OUTPUT_SCHEMA,
        ("capability.input.invalid", "capability.output.invalid"),
        (DECISION_RESEARCH_SEPARATION_INVARIANT,),
        DECISION_OPERATIONS,
        variant_selectors=("decision_kind",),
        variant_aliases={
            "action": "standard_action",
            "conditional_action": "bounded_action",
        },
    ),
    "investment_action_plan": CapabilityContract(
        ACTION_PLAN_DESCRIPTION,
        "investment_commands.action_plan",
        ACTION_PLAN_INPUT_SCHEMA,
        ACTION_PLAN_OUTPUT_SCHEMA,
        ("capability.input.invalid", "capability.output.invalid"),
        (RISK_GATE_BOUNDARY_INVARIANT,),
        ACTION_PLAN_OPERATIONS,
        variant_selectors=("action_tier",),
    ),
    "investment_action_update": CapabilityContract(
        ACTION_UPDATE_DESCRIPTION,
        "investment_commands.action_update",
        ACTION_UPDATE_INPUT_SCHEMA,
        ACTION_UPDATE_OUTPUT_SCHEMA,
        ("capability.input.invalid", "capability.output.invalid"),
        (ACTION_ACCEPTANCE_INVARIANT,),
        ACTION_UPDATE_OPERATIONS,
        actor_aware=True,
        variant_selectors=("operation", "state"),
        variant_aliases={
            "respond.presented": "presented",
            "respond.accepted": "accepted",
            "respond.rejected": "rejected",
            "respond.snoozed": "snoozed",
            "respond.closed": "closed",
        },
    ),
    "investment_program_context": CapabilityContract(
        PROGRAM_CONTEXT_DESCRIPTION,
        "investment.program_context",
        PROGRAM_CONTEXT_INPUT_SCHEMA,
        PROGRAM_CONTEXT_OUTPUT_SCHEMA,
        ("capability.input.invalid", "capability.output.invalid"),
        (PROGRAM_PROJECTION_INVARIANT,),
    ),
    "investment_program_update": CapabilityContract(
        PROGRAM_UPDATE_DESCRIPTION,
        "investment_commands.program_update",
        PROGRAM_UPDATE_INPUT_SCHEMA,
        PROGRAM_UPDATE_OUTPUT_SCHEMA,
        (
            "capability.input.invalid",
            "capability.output.invalid",
            "investment_program.version_conflict",
        ),
        (PROGRAM_CONFIRMATION_INVARIANT, PROGRAM_PROJECTION_INVARIANT),
        PROGRAM_OPERATIONS,
        actor_aware=True,
    ),
    "investment_brief_update": CapabilityContract(
        BRIEF_UPDATE_DESCRIPTION,
        "investment_commands.brief_update",
        BRIEF_UPDATE_INPUT_SCHEMA,
        BRIEF_UPDATE_OUTPUT_SCHEMA,
        (
            "capability.input.invalid",
            "capability.output.invalid",
            "investment_program.not_active",
            "investment_brief.unresolved_obligations",
            "investment_brief.calculation_lineage_required",
        ),
        (BRIEF_NO_ACTION_INVARIANT, BRIEF_PROJECTION_INVARIANT),
        BRIEF_OPERATIONS,
        actor_aware=True,
    ),
    "investment_execution_update": CapabilityContract(
        EXECUTION_UPDATE_DESCRIPTION,
        "investment_commands.execution_update",
        EXECUTION_UPDATE_INPUT_SCHEMA,
        EXECUTION_UPDATE_OUTPUT_SCHEMA,
        (
            "capability.input.invalid",
            "capability.output.invalid",
            "investment_execution.state_conflict",
            "investment_execution.idempotency_conflict",
            "investment_execution.reconciliation_required",
        ),
        (
            ACTION_ACCEPTANCE_INVARIANT,
            EXECUTION_CONFIRMATION_INVARIANT,
            EXECUTION_RECONCILIATION_INVARIANT,
        ),
        EXECUTION_OPERATIONS,
        actor_aware=True,
    ),
    "evaluation_context": CapabilityContract(
        EVALUATION_CONTEXT_DESCRIPTION,
        "investment.evaluation_context",
        EVALUATION_CONTEXT_INPUT_SCHEMA,
        EVALUATION_CONTEXT_OUTPUT_SCHEMA,
        ("capability.input.invalid", "capability.output.invalid"),
        (REVIEW_IMMUTABILITY_INVARIANT, CHANGE_PROPOSAL_INVARIANT),
    ),
    "investment_performance_calculate": CapabilityContract(
        PERFORMANCE_CALCULATE_DESCRIPTION,
        "investment_commands.performance_calculate",
        PERFORMANCE_CALCULATE_INPUT_SCHEMA,
        PERFORMANCE_OUTPUT_SCHEMA,
        (
            "capability.input.invalid",
            "capability.output.invalid",
            "investment_performance.valuation_required",
            "investment_performance.benchmark_required",
        ),
        (PERFORMANCE_CALCULATION_INVARIANT,),
        PERFORMANCE_OPERATIONS,
        variant_selectors=("benchmark_mode",),
    ),
    "investment_review_publish": CapabilityContract(
        REVIEW_PUBLISH_DESCRIPTION,
        "investment_commands.review_publish",
        REVIEW_PUBLISH_INPUT_SCHEMA,
        REVIEW_PUBLISH_OUTPUT_SCHEMA,
        (
            "capability.input.invalid",
            "capability.output.invalid",
            "investment_review.calculation_lineage_required",
            "investment_review.revision_conflict",
            "investment_review.automatic_change_forbidden",
        ),
        (REVIEW_IMMUTABILITY_INVARIANT, CHANGE_PROPOSAL_INVARIANT),
        REVIEW_OPERATIONS,
    ),
    "investment_workflow_context": CapabilityContract(
        WORKFLOW_CONTEXT_DESCRIPTION,
        "investment.workflow_context",
        WORKFLOW_CONTEXT_INPUT_SCHEMA,
        WORKFLOW_CONTEXT_OUTPUT_SCHEMA,
        ("capability.input.invalid", "capability.output.invalid"),
        (WORKFLOW_RUN_DELIVERY_INVARIANT, DELIVERY_STATE_INVARIANT),
        WORKFLOW_CONTEXT_OPERATIONS,
        variant_selectors=("view",),
    ),
    "investment_workflow_update": CapabilityContract(
        WORKFLOW_UPDATE_DESCRIPTION,
        "investment_commands.workflow_update",
        WORKFLOW_UPDATE_INPUT_SCHEMA,
        WORKFLOW_UPDATE_OUTPUT_SCHEMA,
        (
            "capability.input.invalid",
            "capability.output.invalid",
            "investment_workflow.version_conflict",
            "investment_workflow.wake_lease_conflict",
        ),
        (WORKFLOW_VERSION_INVARIANT, WORKFLOW_WAKE_LEASE_INVARIANT),
        WORKFLOW_UPDATE_OPERATIONS,
        actor_aware=True,
    ),
    "investment_delivery_update": CapabilityContract(
        DELIVERY_UPDATE_DESCRIPTION,
        "investment_commands.delivery_update",
        DELIVERY_UPDATE_INPUT_SCHEMA,
        DELIVERY_UPDATE_OUTPUT_SCHEMA,
        (
            "capability.input.invalid",
            "capability.output.invalid",
            "investment_delivery.immutable",
            "investment_delivery.state_conflict",
        ),
        (WORKFLOW_RUN_DELIVERY_INVARIANT, DELIVERY_STATE_INVARIANT),
        DELIVERY_UPDATE_OPERATIONS,
    ),
}
CONTRACTED_CAPABILITY_NAMES = frozenset(CAPABILITY_CONTRACTS)
TRUSTED_CONCLUSION_OPERATIONS: dict[str, set[str] | None] = {
    "investment_research_publish": None,
    "investment_decision_publish": None,
    "investment_action_plan": None,
    "investment_action_update": {"enqueue"},
    "investment_brief_update": {"publish", "scorecard_publish"},
    "investment_performance_calculate": None,
    "investment_review_publish": None,
    "investment_delivery_update": {"prepare", "digest_send"},
}
RUNTIME_BASELINE_ERROR = "capability.runtime.baseline_unavailable"


def _requires_trusted_compatibility(
    name: str, arguments: dict[str, Any]
) -> bool:
    operations = TRUSTED_CONCLUSION_OPERATIONS.get(name, set())
    return operations is None or arguments.get("operation") in operations


@dataclass(frozen=True)
class ProviderManifest:
    document: dict[str, Any]
    digest: str

    @classmethod
    def from_document(cls, document: dict[str, Any]) -> "ProviderManifest":
        return cls(document=document, digest=content_digest(document))


class CapabilityRegistry:
    """Derive discovery, validation, dispatch and provider evidence from one seam."""

    def discovery_tools(self) -> dict[str, tuple[str, dict[str, Any]]]:
        return {
            name: (contract.description, contract.input_schema)
            for name, contract in CAPABILITY_CONTRACTS.items()
        }

    def handles(self, name: str) -> bool:
        return name in CAPABILITY_CONTRACTS

    def invoke(
        self,
        companion: Any,
        name: str,
        arguments: dict[str, Any],
        *,
        actor: str,
    ) -> Any:
        contract = CAPABILITY_CONTRACTS.get(name)
        if contract is None:
            from ..foundation import CompanionError

            raise CompanionError(f"uncontracted capability: {name}")
        from ..foundation import CompanionError
        from ..jobs import _validate_schema

        try:
            _validate_schema(arguments, contract.input_schema, f"{name} arguments")
        except CompanionError as exc:
            raise CompanionError(f"capability.input.invalid: {exc}") from exc
        if _requires_trusted_compatibility(name, arguments):
            from .runtime import compatibility_summary

            compatibility = compatibility_summary(
                self,
                companion.root,
                companion.gate_scope,
            )
            if (
                compatibility["applicable"]
                and compatibility["baseline"]["status"] != "compatible"
            ):
                incident_codes = sorted(
                    {
                        incident.get("code", "compatibility.unknown")
                        for incident in compatibility["baseline"]["incidents"]
                    }
                )
                raise CompanionError(
                    f"{RUNTIME_BASELINE_ERROR}: " + ", ".join(incident_codes)
                )
        handler: Any = companion
        for segment in contract.handler.split("."):
            handler = getattr(handler, segment)
        result = (
            handler(**arguments, actor=actor)
            if contract.actor_aware
            else handler(**arguments)
        )
        output_schema = contract.output_schema
        if contract.operations is not None:
            selector = ".".join(
                str(arguments[field])
                for field in contract.variant_selectors
                if field in arguments
            )
            operation = (
                contract.variant_aliases.get(selector, selector)
                if contract.variant_aliases is not None
                else selector
            )
            output_schema = contract.operations[operation]["output_schema"]
        try:
            _validate_schema(result, output_schema, f"{name} result")
        except CompanionError as exc:
            raise CompanionError(f"capability.output.invalid: {exc}") from exc
        return result

    def provider_manifest(self) -> ProviderManifest:
        capabilities: dict[str, Any] = {}
        for name, contract in sorted(CAPABILITY_CONTRACTS.items()):
            capability = {
                "status": "contracted",
                "description": contract.description,
                "handler": contract.handler,
                "input_schema": contract.input_schema,
                "output_schema": contract.output_schema,
                "errors": sorted(
                    set(contract.errors)
                    | (
                        {RUNTIME_BASELINE_ERROR}
                        if name in TRUSTED_CONCLUSION_OPERATIONS
                        else set()
                    )
                ),
                "invariants": list(contract.invariants),
            }
            if contract.operations is not None:
                capability["operations"] = {
                    operation: {
                        "input_schema": schemas["input_schema"],
                        "output_schema": schemas["output_schema"],
                    }
                    for operation, schemas in sorted(contract.operations.items())
                }
            if contract.pending_variants is not None:
                capability["pending_variants"] = {
                    selector: list(values)
                    for selector, values in sorted(contract.pending_variants.items())
                }
            capabilities[name] = capability
        return ProviderManifest.from_document(
            {
                "format": PROVIDER_FORMAT,
                "provider": "investment-companion-core",
                "capabilities": capabilities,
            }
        )


def investment_capability_registry() -> CapabilityRegistry:
    return CapabilityRegistry()
