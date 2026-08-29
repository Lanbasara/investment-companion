from __future__ import annotations

from copy import deepcopy
from datetime import date
import json
from pathlib import Path
from typing import Any

from ..foundation import CompanionError
from .registry import (
    CAPABILITY_CONTRACTS,
    CapabilityRegistry,
    canonical_json,
    content_digest,
)


DEPTH_POLICY_FORMAT = "investment-companion.interface-depth-policy/v1"
DEPTH_DECISIONS = {"deepen_module", "replace_interface", "migration"}


def _required_paths(schema: dict[str, Any], prefix: str = "") -> list[str]:
    paths: list[str] = []
    if schema.get("type") == "object":
        properties = schema.get("properties", {})
        for name in schema.get("required", []):
            path = f"{prefix}.{name}" if prefix else name
            paths.append(path)
            child = properties.get(name)
            if isinstance(child, dict):
                paths.extend(_required_paths(child, path))
    if schema.get("type") == "array" and isinstance(schema.get("items"), dict):
        paths.extend(_required_paths(schema["items"], f"{prefix}[]"))
    return sorted(set(paths))


def interface_surface(registry: CapabilityRegistry) -> dict[str, Any]:
    """Project only the model-visible surface governed by the Depth gate."""
    tools: dict[str, Any] = {}
    for name in sorted(registry.discovery_tools()):
        contract = CAPABILITY_CONTRACTS[name]
        item: dict[str, Any] = {
            "required_fields": _required_paths(contract.input_schema),
        }
        if contract.operations:
            kind = "views" if contract.variant_selectors == ("view",) else "operations"
            item[kind] = {
                operation: _required_paths(definition["input_schema"])
                for operation, definition in sorted(contract.operations.items())
            }
        tools[name] = item
    return {
        "profile": "investment",
        "tools": tools,
    }


def load_interface_depth_policy(path: str | Path | None = None) -> dict[str, Any]:
    policy_path = (
        Path(path).expanduser().resolve()
        if path is not None
        else Path(__file__).with_name("interface-depth-policy.json")
    )
    try:
        value = json.loads(policy_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CompanionError(f"invalid Interface Depth policy: {exc}") from exc
    if not isinstance(value, dict):
        raise CompanionError("Interface Depth policy must be a JSON object")
    return value


def validate_interface_depth(
    registry: CapabilityRegistry,
    policy: dict[str, Any],
    *,
    surface: dict[str, Any] | None = None,
    today: date | None = None,
) -> dict[str, Any]:
    """Require an explicit architectural reason for any model-visible growth."""
    current_surface = deepcopy(surface) if surface is not None else interface_surface(registry)
    current_digest = content_digest(current_surface)
    policy_digest = content_digest(policy)
    failures: list[dict[str, Any]] = []
    if policy.get("format") != DEPTH_POLICY_FORMAT:
        failures.append(
            {
                "code": "interface_depth.unsupported_policy_format",
                "expected": DEPTH_POLICY_FORMAT,
                "actual": policy.get("format"),
            }
        )
    baseline_digest = policy.get("baseline_digest")
    if not isinstance(baseline_digest, str) or not baseline_digest.startswith("sha256:"):
        failures.append(
            {"code": "interface_depth.invalid_baseline_digest"}
        )
    decision: dict[str, Any] | None = None
    if not failures and current_digest != baseline_digest:
        for candidate in policy.get("changes", []):
            if (
                isinstance(candidate, dict)
                and candidate.get("from_digest") == baseline_digest
                and candidate.get("to_digest") == current_digest
            ):
                decision = candidate
                break
        if decision is None:
            failures.append(
                {
                    "code": "interface_depth.unexplained_growth",
                    "from_digest": baseline_digest,
                    "to_digest": current_digest,
                }
            )
        else:
            kind = decision.get("decision")
            if kind not in DEPTH_DECISIONS:
                failures.append(
                    {"code": "interface_depth.invalid_decision", "decision": kind}
                )
            if not isinstance(decision.get("rationale"), str) or not decision[
                "rationale"
            ].strip():
                failures.append(
                    {"code": "interface_depth.rationale_required"}
                )
            if kind == "deepen_module" and not isinstance(
                decision.get("module"), str
            ):
                failures.append(
                    {"code": "interface_depth.module_required"}
                )
            if kind == "replace_interface" and not decision.get("replaces"):
                failures.append(
                    {"code": "interface_depth.replaced_interface_required"}
                )
            if kind == "migration":
                if not isinstance(decision.get("removal_conditions"), str) or not decision[
                    "removal_conditions"
                ].strip():
                    failures.append(
                        {"code": "interface_depth.removal_conditions_required"}
                    )
                try:
                    remove_by = date.fromisoformat(decision["remove_by"])
                except (KeyError, TypeError, ValueError):
                    failures.append(
                        {"code": "interface_depth.remove_by_required"}
                    )
                else:
                    if remove_by <= (today or date.today()):
                        failures.append(
                            {
                                "code": "interface_depth.migration_expired",
                                "remove_by": decision["remove_by"],
                            }
                        )
    return {
        "passed": not failures,
        "profile": current_surface.get("profile"),
        "surface_digest": current_digest,
        "policy_digest": policy_digest,
        "decision": decision,
        "failures": failures,
    }


def canonical_interface_surface(registry: CapabilityRegistry) -> str:
    return canonical_json(interface_surface(registry))
