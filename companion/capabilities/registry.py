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
    return {"oneOf": [operations[name]["input_schema"] for name in sorted(operations)]}


S = {"type": "string"}
I = {"type": "integer"}
B = {"type": "boolean"}
O = {"type": "object", "additionalProperties": True}
A = {"type": "array"}
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
PRODUCTION_HEALTH_SCHEMA = object_schema(
    {
        "ok": B,
        "applicable": B,
        "baseline": SCOPE_STATUS,
        "workflows": {"type": "object", "additionalProperties": SCOPE_STATUS},
        "optional_enhancements": {
            "type": "object",
            "additionalProperties": SCOPE_STATUS,
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


@dataclass(frozen=True)
class CapabilityContract:
    description: str
    handler: str
    input_schema: dict[str, Any]
    output_schema: dict[str, Any]
    errors: tuple[str, ...]
    invariants: tuple[str, ...]
    operations: Mapping[str, dict[str, Any]] | None = None


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
}
CONTRACTED_CAPABILITY_NAMES = frozenset(CAPABILITY_CONTRACTS)


@dataclass(frozen=True)
class ProviderManifest:
    document: dict[str, Any]
    digest: str

    @classmethod
    def from_document(cls, document: dict[str, Any]) -> "ProviderManifest":
        return cls(document=document, digest=content_digest(document))


class CapabilityRegistry:
    """Derive discovery, validation, dispatch and provider evidence from one seam."""

    def __init__(self, tools: Mapping[str, tuple[str, dict[str, Any]]]):
        self._legacy_tools = dict(tools)

    def discovery_tools(self) -> dict[str, tuple[str, dict[str, Any]]]:
        tools = dict(self._legacy_tools)
        tools.update(
            {
                name: (contract.description, contract.input_schema)
                for name, contract in CAPABILITY_CONTRACTS.items()
            }
        )
        return tools

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
        del actor
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
        handler: Any = companion
        for segment in contract.handler.split("."):
            handler = getattr(handler, segment)
        result = handler(**arguments)
        output_schema = contract.output_schema
        if contract.operations is not None:
            operation = arguments.get("operation")
            output_schema = contract.operations[str(operation)]["output_schema"]
        try:
            _validate_schema(result, output_schema, f"{name} result")
        except CompanionError as exc:
            raise CompanionError(f"capability.output.invalid: {exc}") from exc
        return result

    def provider_manifest(self) -> ProviderManifest:
        capabilities: dict[str, Any] = {}
        for name, (description, input_schema) in sorted(self.discovery_tools().items()):
            contract = CAPABILITY_CONTRACTS.get(name)
            if contract is None:
                capabilities[name] = {
                    "status": "uncontracted",
                    "description": description,
                    "handler": "interfaces.mcp_profiles.call_investment",
                    "input_schema": input_schema,
                }
                continue
            capability = {
                "status": "contracted",
                "description": contract.description,
                "handler": contract.handler,
                "input_schema": contract.input_schema,
                "output_schema": contract.output_schema,
                "errors": list(contract.errors),
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
            capabilities[name] = capability
        return ProviderManifest.from_document(
            {
                "format": PROVIDER_FORMAT,
                "provider": "investment-companion-core",
                "capabilities": capabilities,
            }
        )


def investment_capability_registry(
    tools: Mapping[str, tuple[str, dict[str, Any]]] | None = None,
) -> CapabilityRegistry:
    return CapabilityRegistry(tools or {})
