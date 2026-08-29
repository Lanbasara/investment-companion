from __future__ import annotations

import json
import os
from copy import deepcopy
from pathlib import Path
from typing import Any

from ..foundation import CompanionError
from .receipts import read_current_receipt
from .registry import CapabilityRegistry, content_digest


def _incident(code: str, *, scope: str = "baseline", **details: Any) -> dict[str, Any]:
    return {"severity": "critical", "code": code, "scope": scope, **details}


def _receipt_state_dir(root: Path) -> Path:
    return Path(
        os.environ.get(
            "COMPANION_CAPABILITY_RECEIPT_DIR",
            str(root / ".state" / "capability-contract"),
        )
    ).expanduser().resolve()


def _scope_projection(scopes: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    baseline_source = scopes.get("baseline", {})
    baseline = {
        "status": baseline_source.get("status", "unverified"),
        "incidents": baseline_source.get("failures", []),
    }

    def project(values: dict[str, Any], *, optional: bool = False) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for name, value in values.items():
            status = value.get("status", "unverified")
            fallback = value.get("fallback")
            if optional and status == "degraded":
                status = "fallback" if fallback else "unverified"
            item = {
                "status": status,
                "incidents": value.get("failures", []),
            }
            if optional:
                item["fallback"] = fallback
            result[name] = item
        return result

    return (
        baseline,
        project(scopes.get("workflows", {})),
        project(scopes.get("optional_enhancements", {}), optional=True),
    )


def _unverified_summary(
    *,
    provider_digest: str,
    requirements_digest: str | None,
    receipt_digest: str | None,
    incidents: list[dict[str, Any]],
    status: str = "unverified",
    scopes: dict[str, Any] | None = None,
) -> dict[str, Any]:
    _static_baseline, workflows, enhancements = _scope_projection(scopes or {})
    return {
        "ok": False,
        "applicable": True,
        "baseline": {"status": status, "incidents": incidents},
        "workflows": workflows,
        "optional_enhancements": enhancements,
        "incidents": incidents,
        "provider_digest": provider_digest,
        "requirements_digest": requirements_digest,
        "receipt_digest": receipt_digest,
    }


def compatibility_summary(
    registry: CapabilityRegistry,
    root: str | Path,
    gate_scope: str,
) -> dict[str, Any]:
    """Compare current digests with an existing receipt without running conformance."""
    root_path = Path(root).expanduser().resolve()
    provider_digest = registry.provider_manifest().digest
    configured_requirements = os.environ.get("COMPANION_PLUGIN_REQUIREMENTS")
    state_dir = _receipt_state_dir(root_path)
    if gate_scope == "test_fixture" and not configured_requirements and not (state_dir / "current.json").is_file():
        return {
            "ok": True,
            "applicable": False,
            "baseline": {"status": "not_applicable", "incidents": []},
            "workflows": {},
            "optional_enhancements": {},
            "incidents": [],
            "provider_digest": provider_digest,
            "requirements_digest": None,
            "receipt_digest": None,
        }
    if not configured_requirements:
        incident = _incident("compatibility.requirements_missing")
        return _unverified_summary(
            provider_digest=provider_digest,
            requirements_digest=None,
            receipt_digest=None,
            incidents=[incident],
        )
    try:
        requirements = json.loads(
            Path(configured_requirements).expanduser().resolve().read_text(encoding="utf-8")
        )
        requirements_digest = content_digest(requirements)
    except (OSError, json.JSONDecodeError) as exc:
        incident = _incident("compatibility.requirements_invalid", error=str(exc))
        return _unverified_summary(
            provider_digest=provider_digest,
            requirements_digest=None,
            receipt_digest=None,
            incidents=[incident],
            status="degraded",
        )
    from .validator import validate_compatibility

    static_validation = validate_compatibility(
        registry.provider_manifest().document, requirements
    )
    try:
        current = read_current_receipt(state_dir)
    except CompanionError as exc:
        incident = _incident("compatibility.receipt_invalid", error=str(exc))
        return _unverified_summary(
            provider_digest=provider_digest,
            requirements_digest=requirements_digest,
            receipt_digest=None,
            incidents=[incident],
            status="degraded",
            scopes=static_validation["scopes"],
        )
    if current is None:
        incident = _incident("compatibility.receipt_missing")
        return _unverified_summary(
            provider_digest=provider_digest,
            requirements_digest=requirements_digest,
            receipt_digest=None,
            incidents=[incident],
            scopes=static_validation["scopes"],
        )

    receipt = current["receipt"]
    hard_incidents: list[dict[str, Any]] = []
    contract_incidents: list[dict[str, Any]] = []
    for code, expected, actual in (
        ("provider_digest_mismatch", receipt.get("provider_digest"), provider_digest),
        (
            "requirements_digest_mismatch",
            receipt.get("requirements_digest"),
            requirements_digest,
        ),
    ):
        if expected != actual:
            contract_incidents.append(
                _incident(
                    f"compatibility.{code}",
                    scope="contract",
                    expected=expected,
                    actual=actual,
                )
            )
    profile = os.environ.get("COMPANION_MCP_PROFILE", "investment")
    if receipt.get("mcp_profile") != profile:
        hard_incidents.append(
            _incident(
                "compatibility.mcp_profile_mismatch",
                expected=receipt.get("mcp_profile"),
                actual=profile,
            )
        )
    if receipt.get("contract_format") != registry.provider_manifest().document.get(
        "format"
    ):
        hard_incidents.append(
            _incident(
                "compatibility.contract_format_mismatch",
                expected=receipt.get("contract_format"),
                actual=registry.provider_manifest().document.get("format"),
            )
        )
    if gate_scope == "production" and receipt.get("environment") != "production":
        hard_incidents.append(
            _incident(
                "compatibility.non_production_receipt",
                actual=receipt.get("environment"),
            )
        )
    configured_receipt_identity = os.environ.get(
        "COMPANION_CAPABILITY_RECEIPT_IDENTITY"
    )
    if gate_scope == "production" and not configured_receipt_identity:
        hard_incidents.append(
            _incident(
                "compatibility.receipt_identity_missing",
                expected=current["digest"],
            )
        )
    elif (
        configured_receipt_identity is not None
        and configured_receipt_identity != current["digest"]
    ):
        hard_incidents.append(
            _incident(
                "compatibility.receipt_identity_mismatch",
                expected=configured_receipt_identity,
                actual=current["digest"],
            )
        )
    for role, env_name in (
        ("core", "COMPANION_CORE_IDENTITY"),
        ("plugin", "COMPANION_PLUGIN_IDENTITY"),
    ):
        actual = os.environ.get(env_name)
        expected = receipt.get("release_pair", {}).get(role)
        if gate_scope == "production" and not actual:
            hard_incidents.append(
                _incident(
                    "compatibility.release_pair_identity_missing",
                    role=role,
                    expected=expected,
                )
            )
        elif actual is not None and actual != expected:
            hard_incidents.append(
                _incident(
                    "compatibility.release_pair_mismatch",
                    role=role,
                    expected=expected,
                    actual=actual,
                )
            )
    from .depth import load_interface_depth_policy, validate_interface_depth

    try:
        interface_depth = validate_interface_depth(
            registry, load_interface_depth_policy()
        )
    except CompanionError as exc:
        interface_depth = {
            "passed": False,
            "surface_digest": None,
            "policy_digest": None,
            "failures": [{"code": "interface_depth.policy_invalid", "error": str(exc)}],
        }
    receipt_depth = receipt.get("interface_depth", {})
    for code, expected, actual in (
        (
            "interface_surface_digest_mismatch",
            receipt_depth.get("surface_digest"),
            interface_depth.get("surface_digest"),
        ),
        (
            "interface_policy_digest_mismatch",
            receipt_depth.get("policy_digest"),
            interface_depth.get("policy_digest"),
        ),
    ):
        if expected != actual:
            hard_incidents.append(
                _incident(f"compatibility.{code}", expected=expected, actual=actual)
            )
    if not interface_depth.get("passed"):
        hard_incidents.extend(
            _incident("compatibility.interface_depth_failed", failure=failure)
            for failure in interface_depth.get("failures", [])
        )

    scopes = deepcopy(static_validation["scopes"])
    receipt_scope_digests = receipt.get("validation", {}).get("scope_digests")
    if not isinstance(receipt_scope_digests, dict):
        hard_incidents.append(
            _incident("compatibility.receipt_scope_digests_missing")
        )
        receipt_scope_digests = {}
    current_scope_digests = static_validation["scope_digests"]

    def add_scope_drift(kind: str, name: str, expected: Any, actual: Any) -> None:
        failure = _incident(
            "compatibility.scope_digest_mismatch",
            scope=kind,
            name=name,
            expected=expected,
            actual=actual,
        )
        if kind == "baseline":
            target = scopes["baseline"]
        elif kind == "workflow":
            target = scopes["workflows"].setdefault(
                name, {"status": "compatible", "failures": []}
            )
        else:
            target = scopes["optional_enhancements"].setdefault(
                name, {"status": "compatible", "failures": [], "fallback": None}
            )
        target["failures"].append(failure)
        target["status"] = "degraded"

    expected_baseline = receipt_scope_digests.get("baseline")
    actual_baseline = current_scope_digests["baseline"]
    if expected_baseline != actual_baseline:
        add_scope_drift("baseline", "baseline", expected_baseline, actual_baseline)
    for key, kind in (
        ("workflows", "workflow"),
        ("optional_enhancements", "optional_enhancement"),
    ):
        expected_values = receipt_scope_digests.get(key, {})
        actual_values = current_scope_digests[key]
        for name in sorted(set(expected_values) | set(actual_values)):
            if expected_values.get(name) != actual_values.get(name):
                add_scope_drift(
                    kind,
                    name,
                    expected_values.get(name),
                    actual_values.get(name),
                )
    if hard_incidents:
        scopes["baseline"]["failures"].extend(hard_incidents)
        scopes["baseline"]["status"] = "degraded"

    baseline, workflows, enhancements = _scope_projection(scopes)
    blocking = baseline["status"] != "compatible" or any(
        item["status"] != "compatible" for item in workflows.values()
    )
    scope_incidents = [
        *baseline["incidents"],
        *(failure for item in workflows.values() for failure in item["incidents"]),
        *(failure for item in enhancements.values() for failure in item["incidents"]),
    ]
    return {
        "ok": not blocking,
        "applicable": True,
        "baseline": baseline,
        "workflows": workflows,
        "optional_enhancements": enhancements,
        "incidents": [*contract_incidents, *scope_incidents],
        "provider_digest": provider_digest,
        "requirements_digest": requirements_digest,
        "receipt_digest": current["digest"],
    }


def compatibility_diagnostics(
    registry: CapabilityRegistry,
    root: str | Path,
    gate_scope: str,
) -> dict[str, Any]:
    """Doctor projection with the full current receipt for operator diagnosis."""
    root_path = Path(root).expanduser().resolve()
    summary = compatibility_summary(registry, root_path, gate_scope)
    try:
        current = read_current_receipt(_receipt_state_dir(root_path))
    except CompanionError as exc:
        return {"summary": summary, "receipt": None, "receipt_error": str(exc)}
    return {
        "summary": summary,
        "receipt": current["receipt"] if current else None,
        "receipt_error": None,
    }
