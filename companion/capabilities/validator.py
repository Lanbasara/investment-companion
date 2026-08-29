from __future__ import annotations

from decimal import Decimal, InvalidOperation
from pathlib import Path
import re
from typing import Any

from .registry import PROVIDER_FORMAT, canonical_json, content_digest


REQUIREMENTS_FORMAT = "investment-companion.capability-requirements/v1"
DEPENDENCY_LEVELS = {
    "baseline_required",
    "workflow_required",
    "optional_enhancement",
}
ANNOTATION_KEYS = {"$comment", "default", "description", "examples", "title"}
KNOWN_SCHEMA_KEYS = {
    "additionalProperties",
    "const",
    "enum",
    "exclusiveMaximum",
    "exclusiveMinimum",
    "format",
    "items",
    "maxItems",
    "maxLength",
    "maxProperties",
    "maximum",
    "minItems",
    "minLength",
    "minProperties",
    "minimum",
    "multipleOf",
    "oneOf",
    "pattern",
    "properties",
    "required",
    "type",
    "uniqueItems",
}


def _requirement_failures(name: str, requirement: Any) -> list[dict[str, Any]]:
    if not isinstance(requirement, dict):
        return [{
            "code": "invalid_requirement",
            "capability": name,
            "field": "capability",
            "scope": "baseline",
        }]
    level = requirement.get("level")
    outputs = requirement.get("required_outputs")
    required_operations = requirement.get("required_operations", {})
    operations_valid = isinstance(required_operations, dict) and all(
        isinstance(operation, str)
        and bool(operation.strip())
        and isinstance(operation_requirement, dict)
        and isinstance(operation_requirement.get("required_outputs"), list)
        and all(
            isinstance(item, dict)
            and isinstance(item.get("path"), str)
            and bool(item["path"].strip())
            and isinstance(item.get("schema"), dict)
            for item in operation_requirement["required_outputs"]
        )
        for operation, operation_requirement in required_operations.items()
    )
    fields_valid = {
        "level": level in DEPENDENCY_LEVELS,
        "workflows": isinstance(requirement.get("workflows"), list)
        and bool(requirement["workflows"])
        and all(
            isinstance(item, str) and bool(item.strip())
            for item in requirement["workflows"]
        ),
        "rationale": isinstance(requirement.get("rationale"), str)
        and bool(requirement["rationale"].strip()),
        "input_schema": isinstance(requirement.get("input_schema"), dict),
        "required_outputs": isinstance(outputs, list)
        and all(
            isinstance(item, dict)
            and isinstance(item.get("path"), str)
            and bool(item["path"].strip())
            and isinstance(item.get("schema"), dict)
            for item in outputs
        ),
        "errors": isinstance(requirement.get("errors"), list)
        and all(isinstance(item, str) for item in requirement.get("errors", [])),
        "invariants": isinstance(requirement.get("invariants"), list)
        and all(isinstance(item, str) for item in requirement.get("invariants", [])),
        "required_operations": operations_valid,
    }
    if level == "optional_enhancement":
        fields_valid["fallback"] = isinstance(requirement.get("fallback"), str) and bool(
            requirement["fallback"].strip()
        )
    return [
        {
            "code": "invalid_requirement",
            "capability": name,
            "field": field,
            "scope": "baseline",
        }
        for field, valid in fields_valid.items()
        if not valid
    ]


def _schema_types(schema: dict[str, Any]) -> set[str] | None:
    value = schema.get("type")
    if value is None:
        return None
    return set(value if isinstance(value, list) else [value])


def _types_include(superset: set[str] | None, subset: set[str] | None) -> bool:
    if superset is None:
        return True
    if subset is None:
        return False
    return all(
        value in superset or value == "integer" and "number" in superset
        for value in subset
    )


def _json_values(values: list[Any]) -> set[str]:
    return {canonical_json(value) for value in values}


def _lower_bound(schema: dict[str, Any]) -> tuple[Decimal, bool] | None:
    key = "exclusiveMinimum" if "exclusiveMinimum" in schema else "minimum"
    if key not in schema:
        return None
    try:
        return Decimal(str(schema[key])), key == "exclusiveMinimum"
    except InvalidOperation:
        return None


def _upper_bound(schema: dict[str, Any]) -> tuple[Decimal, bool] | None:
    key = "exclusiveMaximum" if "exclusiveMaximum" in schema else "maximum"
    if key not in schema:
        return None
    try:
        return Decimal(str(schema[key])), key == "exclusiveMaximum"
    except InvalidOperation:
        return None


def _lower_includes(superset: tuple[Decimal, bool] | None, subset: tuple[Decimal, bool] | None) -> bool:
    if superset is None:
        return True
    if subset is None:
        return False
    if superset[0] < subset[0]:
        return True
    return superset[0] == subset[0] and (not superset[1] or subset[1])


def _upper_includes(superset: tuple[Decimal, bool] | None, subset: tuple[Decimal, bool] | None) -> bool:
    if superset is None:
        return True
    if subset is None:
        return False
    if superset[0] > subset[0]:
        return True
    return superset[0] == subset[0] and (not superset[1] or subset[1])


def _minimum_includes(superset: dict[str, Any], subset: dict[str, Any], key: str) -> bool:
    value = superset.get(key)
    return value is None or subset.get(key) is not None and value <= subset[key]


def _maximum_includes(superset: dict[str, Any], subset: dict[str, Any], key: str) -> bool:
    value = superset.get(key)
    return value is None or subset.get(key) is not None and value >= subset[key]


def _multiple_includes(superset: dict[str, Any], subset: dict[str, Any]) -> bool:
    value = superset.get("multipleOf")
    if value is None:
        return True
    other = subset.get("multipleOf")
    if other is None:
        return False
    try:
        return Decimal(str(other)) % Decimal(str(value)) == 0
    except (InvalidOperation, ZeroDivisionError):
        return False


def _additional_schema(value: Any) -> dict[str, Any] | None:
    if value is False:
        return None
    return value if isinstance(value, dict) else {}


def _schema_includes(superset: dict[str, Any], subset: dict[str, Any]) -> bool:
    """Return whether every value allowed by subset is also allowed by superset."""
    if "oneOf" in superset or "oneOf" in subset:
        def variants(schema: dict[str, Any]) -> list[dict[str, Any]]:
            choices = schema.get("oneOf")
            if choices is None:
                return [schema]
            base = {key: value for key, value in schema.items() if key != "oneOf"}
            return [{**base, **choice} for choice in choices]

        return all(
            any(_schema_includes(accepted, required) for accepted in variants(superset))
            for required in variants(subset)
        )
    superset_types = _schema_types(superset)
    subset_types = _schema_types(subset)
    if not _types_include(superset_types, subset_types):
        return False
    if "const" in superset:
        if "const" not in subset or canonical_json(superset["const"]) != canonical_json(subset["const"]):
            return False
    superset_enum = superset.get("enum")
    subset_enum = subset.get("enum")
    if superset_enum is not None and (
        subset_enum is None or not _json_values(subset_enum) <= _json_values(superset_enum)
    ):
        return False
    for key in ("minItems", "minLength", "minProperties"):
        if not _minimum_includes(superset, subset, key):
            return False
    for key in ("maxItems", "maxLength", "maxProperties"):
        if not _maximum_includes(superset, subset, key):
            return False
    if not _lower_includes(_lower_bound(superset), _lower_bound(subset)):
        return False
    if not _upper_includes(_upper_bound(superset), _upper_bound(subset)):
        return False
    if not _multiple_includes(superset, subset):
        return False
    if superset.get("uniqueItems") is True and subset.get("uniqueItems") is not True:
        return False
    for key in ("format", "pattern"):
        if key in superset and superset.get(key) != subset.get(key):
            return False

    possible_types = subset_types or superset_types or set()
    if "object" in possible_types:
        if not set(superset.get("required", [])) <= set(subset.get("required", [])):
            return False
        superset_properties = superset.get("properties", {})
        subset_properties = subset.get("properties", {})
        superset_additional = _additional_schema(superset.get("additionalProperties", True))
        subset_additional = _additional_schema(subset.get("additionalProperties", True))
        for name, subset_property in subset_properties.items():
            accepted = superset_properties.get(name, superset_additional)
            if accepted is None or not _schema_includes(accepted, subset_property):
                return False
        if subset_additional is not None:
            for name, accepted in superset_properties.items():
                if name not in subset_properties and not _schema_includes(accepted, subset_additional):
                    return False
            if superset_additional is None or not _schema_includes(
                superset_additional, subset_additional
            ):
                return False
    if "array" in possible_types:
        subset_items = subset.get("items", {})
        accepted_items = superset.get("items", {})
        if not _schema_includes(accepted_items, subset_items):
            return False

    for key, value in superset.items():
        if key in KNOWN_SCHEMA_KEYS or key in ANNOTATION_KEYS:
            continue
        if key not in subset or canonical_json(value) != canonical_json(subset[key]):
            return False
    return True


def _required_output_schema(schema: dict[str, Any], path: str) -> dict[str, Any] | None:
    current = schema
    for segment in path.split("."):
        if current.get("type") != "object" or segment not in current.get("required", []):
            return None
        current = current.get("properties", {}).get(segment)
        if not isinstance(current, dict):
            return None
    return current


def _add_scope_result(
    target: dict[str, Any], name: str, failures: list[dict[str, Any]]
) -> None:
    result = target.setdefault(name, {"status": "compatible", "failures": []})
    result["failures"].extend(failures)
    if failures:
        result["status"] = "degraded"


def _scope_contract_digests(
    provider: dict[str, Any], requirements: dict[str, Any]
) -> dict[str, Any]:
    """Fingerprint the exact contract evidence consumed by each runtime scope."""
    provided = provider.get("capabilities", {})
    required = requirements.get("capabilities", {})
    if not isinstance(provided, dict):
        provided = {}
    if not isinstance(required, dict):
        required = {}
    baseline: dict[str, Any] = {
        "provider_format": provider.get("format"),
        "requirements_format": requirements.get("format"),
        "consumer": requirements.get("consumer"),
        "capabilities": {},
    }
    workflows: dict[str, dict[str, Any]] = {}
    optional: dict[str, Any] = {}
    capabilities: dict[str, Any] = {}
    for name, requirement in sorted(required.items()):
        item = {
            "provider": provided.get(name),
            "requirement": requirement,
        }
        level = requirement.get("level") if isinstance(requirement, dict) else None
        requirement_workflows = (
            requirement.get("workflows", []) if isinstance(requirement, dict) else []
        )
        capabilities[name] = {
            "digest": content_digest(item),
            "level": level,
            "workflows": requirement_workflows,
            "fallback": requirement.get("fallback")
            if isinstance(requirement, dict)
            else None,
        }
        if level == "baseline_required":
            baseline["capabilities"][name] = item
        elif level == "workflow_required":
            for workflow in requirement_workflows:
                workflows.setdefault(workflow, {})[name] = item
        elif level == "optional_enhancement":
            optional[name] = item
    return {
        "baseline": content_digest(baseline),
        "workflows": {
            name: content_digest(value) for name, value in sorted(workflows.items())
        },
        "optional_enhancements": {
            name: content_digest(value) for name, value in sorted(optional.items())
        },
        "capabilities": capabilities,
    }


def _selector_values(schema: dict[str, Any], selector: str) -> set[str]:
    values: set[str] = set()
    properties = schema.get("properties", {})
    selected = properties.get(selector) if isinstance(properties, dict) else None
    if isinstance(selected, dict):
        if isinstance(selected.get("const"), str):
            values.add(selected["const"])
        values.update(
            item for item in selected.get("enum", []) if isinstance(item, str)
        )
    for choice in schema.get("oneOf", []):
        if isinstance(choice, dict):
            values.update(_selector_values(choice, selector))
    return values


def _source_workflow(path: Path) -> str | None:
    parts = path.parts
    try:
        index = parts.index("skills")
    except ValueError:
        return None
    return parts[index + 1] if len(parts) > index + 1 else None


def validate_compatibility(
    provider: dict[str, Any],
    requirements: dict[str, Any],
    *,
    usage_sources: list[str | Path] | None = None,
) -> dict[str, Any]:
    """Compare a consumer's minimum needs against provider guarantees."""
    failures: list[dict[str, Any]] = []
    scopes: dict[str, Any] = {
        "baseline": {"status": "compatible", "failures": []},
        "workflows": {},
        "optional_enhancements": {},
    }
    if provider.get("format") != PROVIDER_FORMAT:
        failures.append({
            "code": "unsupported_provider_format",
            "expected": PROVIDER_FORMAT,
            "actual": provider.get("format"),
            "scope": "baseline",
        })
    if requirements.get("format") != REQUIREMENTS_FORMAT:
        failures.append({
            "code": "unsupported_requirements_format",
            "expected": REQUIREMENTS_FORMAT,
            "actual": requirements.get("format"),
            "scope": "baseline",
        })
    if not isinstance(requirements.get("consumer"), str) or not requirements.get("consumer", "").strip():
        failures.append({
            "code": "invalid_requirements_consumer",
            "field": "consumer",
            "scope": "baseline",
        })
    provided = provider.get("capabilities", {})
    required = requirements.get("capabilities", {})
    if not isinstance(provided, dict) or not isinstance(required, dict):
        failures.append({
            "code": "invalid_contract_capabilities",
            "field": "capabilities",
            "scope": "baseline",
        })
        provided = provided if isinstance(provided, dict) else {}
        required = required if isinstance(required, dict) else {}

    for name, requirement in sorted(required.items()):
        requirement_failures = _requirement_failures(name, requirement)
        level = requirement.get("level") if isinstance(requirement, dict) else None
        workflows = requirement.get("workflows", []) if isinstance(requirement, dict) else []
        scope = {
            "baseline_required": "baseline",
            "workflow_required": "workflows",
            "optional_enhancement": "optional_enhancements",
        }.get(level, "baseline")
        common = {"capability": name, "scope": scope, "workflows": workflows}
        capability_failures = list(requirement_failures)
        capability = provided.get(name)
        if capability is None:
            capability_failures.append({**common, "code": "missing_capability"})
        elif not isinstance(capability, dict) or capability.get("status") != "contracted":
            capability_failures.append({**common, "code": "uncontracted_capability"})
        elif isinstance(requirement, dict) and not requirement_failures:
            if not _schema_includes(
                capability.get("input_schema", {}), requirement.get("input_schema", {})
            ):
                capability_failures.append({**common, "code": "input_schema_not_covered"})
            for output in requirement.get("required_outputs", []):
                if not isinstance(output, dict):
                    continue
                path = output.get("path", "")
                guaranteed = _required_output_schema(
                    capability.get("output_schema", {}), path
                )
                if guaranteed is None:
                    capability_failures.append({
                        **common, "code": "missing_required_output", "path": path
                    })
                elif not _schema_includes(output.get("schema", {}), guaranteed):
                    capability_failures.append({
                        **common, "code": "output_schema_not_covered", "path": path
                    })
            provided_operations = capability.get("operations", {})
            for operation, operation_requirement in sorted(
                requirement.get("required_operations", {}).items()
            ):
                provided_operation = provided_operations.get(operation)
                if not isinstance(provided_operation, dict):
                    capability_failures.append({
                        **common,
                        "code": "missing_operation",
                        "operation": operation,
                    })
                    continue
                for output in operation_requirement.get("required_outputs", []):
                    path = output.get("path", "")
                    guaranteed = _required_output_schema(
                        provided_operation.get("output_schema", {}), path
                    )
                    if guaranteed is None:
                        capability_failures.append({
                            **common,
                            "code": "missing_operation_output",
                            "operation": operation,
                            "path": path,
                        })
                    elif not _schema_includes(output.get("schema", {}), guaranteed):
                        capability_failures.append({
                            **common,
                            "code": "operation_output_schema_not_covered",
                            "operation": operation,
                            "path": path,
                        })
            for error in sorted(
                set(requirement.get("errors", [])) - set(capability.get("errors", []))
            ):
                capability_failures.append({
                    **common, "code": "missing_error", "error": error
                })
            for invariant in sorted(
                set(requirement.get("invariants", []))
                - set(capability.get("invariants", []))
            ):
                capability_failures.append({
                    **common,
                    "code": "missing_invariant",
                    "invariant": invariant,
                })
        failures.extend(capability_failures)

        if level == "baseline_required":
            scopes["baseline"]["failures"].extend(capability_failures)
            if capability_failures:
                scopes["baseline"]["status"] = "degraded"
            for workflow in workflows:
                _add_scope_result(scopes["workflows"], workflow, capability_failures)
        elif level == "workflow_required":
            for workflow in workflows:
                _add_scope_result(scopes["workflows"], workflow, capability_failures)
        elif level == "optional_enhancement":
            _add_scope_result(scopes["optional_enhancements"], name, capability_failures)
            scopes["optional_enhancements"][name]["fallback"] = requirement.get(
                "fallback"
            )
        if requirement_failures and level != "baseline_required":
            scopes["baseline"]["failures"].extend(requirement_failures)
            scopes["baseline"]["status"] = "degraded"

    usage_audit = None
    if usage_sources is not None:
        declared = set(required)
        contracted = {
            name
            for name, capability in provided.items()
            if isinstance(capability, dict) and capability.get("status") == "contracted"
        }
        uses: dict[str, list[str]] = {}
        selector_uses: dict[str, dict[str, set[str]]] = {}
        source_failures: list[dict[str, Any]] = []
        for source in usage_sources:
            path = Path(source).expanduser().resolve()
            try:
                prose = path.read_text(encoding="utf-8")
            except OSError as exc:
                source_failures.append({
                    "code": "capability_usage_source_unreadable",
                    "capability": "capability_usage_audit",
                    "scope": "baseline",
                    "source": str(path),
                    "error": str(exc),
                })
                continue
            for name in sorted(contracted):
                if re.search(rf"(?<![A-Za-z0-9_]){re.escape(name)}(?![A-Za-z0-9_])", prose):
                    uses.setdefault(name, []).append(str(path))
                    workflow = _source_workflow(path)
                    requirement = required.get(name, {})
                    if (
                        workflow
                        and workflow not in requirement.get("workflows", [])
                    ):
                        source_failures.append(
                            {
                                "code": "undeclared_workflow_capability_usage",
                                "capability": name,
                                "workflow": workflow,
                                "scope": "baseline",
                                "source": str(path),
                            }
                        )
            workflow = _source_workflow(path)
            if workflow:
                for selector, value in re.findall(
                    r"\b(operation|view)\s*=\s*[\"']([^\"']+)[\"']", prose
                ):
                    selector_uses.setdefault(workflow, {}).setdefault(
                        selector, set()
                    ).add(value)
        undeclared = [
            {
                "code": "undeclared_capability_usage",
                "capability": name,
                "scope": "baseline",
                "sources": sources,
            }
            for name, sources in sorted(uses.items())
            if name not in declared
        ]
        selector_failures: list[dict[str, Any]] = []
        for workflow, selectors in sorted(selector_uses.items()):
            workflow_requirements = [
                requirement
                for requirement in required.values()
                if isinstance(requirement, dict)
                and workflow in requirement.get("workflows", [])
            ]
            for selector, values in sorted(selectors.items()):
                declared_values = set().union(
                    *(
                        _selector_values(requirement.get("input_schema", {}), selector)
                        for requirement in workflow_requirements
                    )
                )
                for value in sorted(values - declared_values):
                    selector_failures.append(
                        {
                            "code": "undeclared_selector_usage",
                            "capability": "capability_usage_audit",
                            "workflow": workflow,
                            "selector": selector,
                            "value": value,
                            "scope": "baseline",
                        }
                    )
        usage_failures = [*source_failures, *undeclared, *selector_failures]
        failures.extend(usage_failures)
        if usage_failures:
            scopes["baseline"]["failures"].extend(usage_failures)
            scopes["baseline"]["status"] = "degraded"
        usage_audit = {
            "ok": not usage_failures,
            "sources": [str(Path(item).expanduser().resolve()) for item in usage_sources],
            "used_contracted_capabilities": {
                name: sources for name, sources in sorted(uses.items())
            },
            "used_selectors": {
                workflow: {
                    selector: sorted(values)
                    for selector, values in sorted(selectors.items())
                }
                for workflow, selectors in sorted(selector_uses.items())
            },
            "failures": usage_failures,
        }

    format_failures = [item for item in failures if item.get("capability") is None]
    if format_failures:
        scopes["baseline"]["failures"].extend(format_failures)
        scopes["baseline"]["status"] = "degraded"
    blocking = [
        item for item in failures
        if item.get("scope") in {"baseline", "workflows"}
    ]
    result = {
        "compatible": not blocking,
        "provider_digest": content_digest(provider),
        "requirements_digest": content_digest(requirements),
        "scope_digests": _scope_contract_digests(provider, requirements),
        "scopes": scopes,
        "failures": failures,
    }
    if usage_audit is not None:
        result["usage_audit"] = usage_audit
    return result
