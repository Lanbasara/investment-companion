from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from typing import Any, Mapping


PROVIDER_FORMAT = "investment-companion.capability-provider/v1"
HOME_INVARIANT = "investment_home.production_health.required/v1"
HOME_DESCRIPTION = (
    "读取今天的行动、异常、研究工作队列、绩效和交付总入口；未完成研究返回 review_required。"
)
HOME_INPUT_SCHEMA = {
    "type": "object",
    "properties": {},
    "required": [],
    "additionalProperties": False,
}
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
    additional_properties: bool = False,
) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": properties or {},
        "required": required or [],
        "additionalProperties": additional_properties,
    }


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
        "ok": {"type": "boolean"},
        "applicable": {"type": "boolean"},
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
        "schema": {"type": "string"},
        "as_of": {"type": "string"},
        "state": {"type": "string"},
        "message": {"type": "string"},
        "program": {},
        "actions": {"type": "array"},
        "deferred": {"type": "array"},
        "execution": {},
        "research": {"type": "object"},
        "evaluation": {},
        "workflow": {"type": "object"},
        "delivery": {"type": "object"},
        "claims": {"type": "object"},
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
    additional_properties=False,
)


@dataclass(frozen=True)
class ProviderManifest:
    document: dict[str, Any]
    digest: str

    @classmethod
    def from_document(cls, document: dict[str, Any]) -> "ProviderManifest":
        return cls(document=document, digest=content_digest(document))


class CapabilityRegistry:
    """Public registry used to derive discovery, dispatch and provider evidence."""

    def __init__(self, tools: Mapping[str, tuple[str, dict[str, Any]]]):
        self._tools = dict(tools)
        self._tools["investment_home"] = (HOME_DESCRIPTION, HOME_INPUT_SCHEMA)

    def discovery_tools(self) -> dict[str, tuple[str, dict[str, Any]]]:
        return dict(self._tools)

    def handles(self, name: str) -> bool:
        capability = self.provider_manifest().document["capabilities"].get(name)
        return bool(capability and capability.get("status") == "contracted")

    def invoke(
        self,
        companion: Any,
        name: str,
        arguments: dict[str, Any],
        *,
        actor: str,
    ) -> Any:
        del actor
        capability = self.provider_manifest().document["capabilities"].get(name)
        if not capability or capability.get("status") != "contracted":
            from ..foundation import CompanionError

            raise CompanionError(f"uncontracted capability: {name}")
        from ..foundation import CompanionError
        from ..jobs import _validate_schema

        try:
            _validate_schema(arguments, capability["input_schema"], f"{name} arguments")
        except CompanionError as exc:
            raise CompanionError(f"capability.input.invalid: {exc}") from exc
        handler: Any = companion
        for segment in capability["handler"].split("."):
            handler = getattr(handler, segment)
        result = handler(**arguments)
        try:
            _validate_schema(result, capability["output_schema"], f"{name} result")
        except CompanionError as exc:
            raise CompanionError(f"capability.output.invalid: {exc}") from exc
        return result

    def provider_manifest(self) -> ProviderManifest:
        capabilities: dict[str, Any] = {}
        for name in sorted(self._tools):
            description, input_schema = self._tools[name]
            if name == "investment_home":
                capabilities[name] = {
                    "status": "contracted",
                    "description": description,
                    "handler": "investment.home",
                    "input_schema": input_schema,
                    "output_schema": HOME_OUTPUT_SCHEMA,
                    "errors": [
                        "capability.input.invalid",
                        "capability.output.invalid",
                    ],
                    "invariants": [HOME_INVARIANT],
                }
            else:
                capabilities[name] = {
                    "status": "uncontracted",
                    "description": description,
                    "handler": "interfaces.mcp_profiles.call_investment",
                    "input_schema": input_schema,
                }
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
