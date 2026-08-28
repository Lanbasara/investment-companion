from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from ..foundation import CompanionError
from .receipts import read_current_receipt
from .registry import CapabilityRegistry, content_digest


def _incident(code: str, **details: Any) -> dict[str, Any]:
    return {"severity": "critical", "code": code, "scope": "baseline", **details}


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

    def project(values: dict[str, Any]) -> dict[str, Any]:
        return {
            name: {
                "status": value.get("status", "unverified"),
                "incidents": value.get("failures", []),
            }
            for name, value in values.items()
        }

    return (
        baseline,
        project(scopes.get("workflows", {})),
        project(scopes.get("optional_enhancements", {})),
    )


def _unverified_summary(
    *,
    provider_digest: str,
    requirements_digest: str | None,
    receipt_digest: str | None,
    incidents: list[dict[str, Any]],
    status: str = "unverified",
) -> dict[str, Any]:
    return {
        "ok": False,
        "applicable": True,
        "baseline": {"status": status, "incidents": incidents},
        "workflows": {},
        "optional_enhancements": {},
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
        )
    if current is None:
        incident = _incident("compatibility.receipt_missing")
        return _unverified_summary(
            provider_digest=provider_digest,
            requirements_digest=requirements_digest,
            receipt_digest=None,
            incidents=[incident],
        )

    receipt = current["receipt"]
    comparisons = (
        (
            "provider_digest_mismatch",
            receipt.get("provider_digest"),
            provider_digest,
        ),
        (
            "requirements_digest_mismatch",
            receipt.get("requirements_digest"),
            requirements_digest,
        ),
        (
            "mcp_profile_mismatch",
            receipt.get("mcp_profile"),
            os.environ.get("COMPANION_MCP_PROFILE", "all"),
        ),
    )
    incidents = [
        _incident(f"compatibility.{code}", expected=expected, actual=actual)
        for code, expected, actual in comparisons
        if expected != actual
    ]
    if gate_scope == "production" and receipt.get("environment") != "production":
        incidents.append(
            _incident(
                "compatibility.non_production_receipt",
                actual=receipt.get("environment"),
            )
        )
    for role, env_name in (
        ("core", "COMPANION_CORE_IDENTITY"),
        ("plugin", "COMPANION_PLUGIN_IDENTITY"),
    ):
        actual = os.environ.get(env_name)
        expected = receipt.get("release_pair", {}).get(role)
        if gate_scope == "production" and not actual:
            incidents.append(
                _incident(
                    "compatibility.release_pair_identity_missing",
                    role=role,
                    expected=expected,
                )
            )
        elif actual is not None and actual != expected:
            incidents.append(
                _incident(
                    "compatibility.release_pair_mismatch",
                    role=role,
                    expected=expected,
                    actual=actual,
                )
            )
    if incidents:
        return _unverified_summary(
            provider_digest=provider_digest,
            requirements_digest=requirements_digest,
            receipt_digest=current["digest"],
            incidents=incidents,
            status="degraded",
        )

    baseline, workflows, enhancements = _scope_projection(
        receipt.get("validation", {}).get("scopes", {})
    )
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
        "incidents": scope_incidents,
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
