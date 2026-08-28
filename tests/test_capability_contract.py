from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest

from companion.capabilities import investment_capability_registry, validate_compatibility
from companion.capabilities.conformance import (
    evaluate_investment_conformance,
    probe_investment_mcp,
)
from companion.capabilities.receipts import (
    issue_compatibility_receipt,
    read_current_receipt,
)
from companion.capabilities.registry import content_digest
from companion.capabilities.runtime import compatibility_summary
from companion.core import Companion, CompanionError
from companion.interfaces.mcp_profiles import INVESTMENT_TOOLS


def test_provider_manifest_contracts_home_and_marks_remaining_tools_uncontracted():
    registry = investment_capability_registry(INVESTMENT_TOOLS)

    first = registry.provider_manifest()
    second = investment_capability_registry(dict(reversed(INVESTMENT_TOOLS.items()))).provider_manifest()

    assert first.document == second.document
    assert first.digest == second.digest
    assert first.document["format"] == "investment-companion.capability-provider/v1"
    home = first.document["capabilities"]["investment_home"]
    assert home["status"] == "contracted"
    assert home["handler"] == "investment.home"
    assert home["input_schema"] == {
        "type": "object",
        "properties": {},
        "required": [],
        "additionalProperties": False,
    }
    assert "production_health" in home["output_schema"]["required"]
    assert home["errors"] == ["capability.input.invalid", "capability.output.invalid"]
    assert home["invariants"] == ["investment_home.production_health.required/v1"]
    assert {
        name for name, capability in first.document["capabilities"].items()
        if capability["status"] == "uncontracted"
    } == set(INVESTMENT_TOOLS) - {"investment_home"}


def baseline_requirements() -> dict:
    return {
        "format": "investment-companion.capability-requirements/v1",
        "consumer": "investment-companion-plugin",
        "capabilities": {
            "investment_home": {
                "level": "baseline_required",
                "workflows": ["investment_home"],
                "rationale": "The Home workflow distinguishes degradation from no action.",
                "input_schema": {
                    "type": "object",
                    "properties": {},
                    "required": [],
                    "additionalProperties": False,
                },
                "required_outputs": [
                    {"path": "production_health", "schema": {"type": "object"}},
                    {"path": "production_health.baseline", "schema": {"type": "object"}},
                    {
                        "path": "production_health.baseline.status",
                        "schema": {
                            "type": "string",
                            "enum": [
                                "compatible",
                                "degraded",
                                "unverified",
                                "not_applicable",
                            ],
                        },
                    },
                    {"path": "production_health.workflows", "schema": {"type": "object"}},
                    {
                        "path": "production_health.optional_enhancements",
                        "schema": {"type": "object"},
                    },
                    {"path": "production_health.incidents", "schema": {"type": "array"}},
                ],
                "errors": [],
                "invariants": ["investment_home.production_health.required/v1"],
            }
        },
    }


def test_validator_accepts_provider_that_covers_baseline_requirement():
    provider = investment_capability_registry(INVESTMENT_TOOLS).provider_manifest()

    result = validate_compatibility(provider.document, baseline_requirements())

    assert result["compatible"] is True
    assert result["failures"] == []
    assert result["provider_digest"] == provider.digest
    assert result["scopes"] == {
        "baseline": {"status": "compatible", "failures": []},
        "workflows": {
            "investment_home": {"status": "compatible", "failures": []}
        },
        "optional_enhancements": {},
    }


@pytest.mark.parametrize(
    ("drift", "expected_code"),
    [
        ("missing_capability", "missing_capability"),
        ("missing_output", "missing_required_output"),
        ("missing_invariant", "missing_invariant"),
        ("output_enum_drift", "output_schema_not_covered"),
    ],
)
def test_validator_reports_structured_baseline_drift(drift: str, expected_code: str):
    provider = deepcopy(
        investment_capability_registry(INVESTMENT_TOOLS).provider_manifest().document
    )
    if drift == "missing_capability":
        del provider["capabilities"]["investment_home"]
    elif drift == "missing_output":
        provider["capabilities"]["investment_home"]["output_schema"]["required"].remove(
            "production_health"
        )
    elif drift == "missing_invariant":
        provider["capabilities"]["investment_home"]["invariants"] = []
    else:
        provider["capabilities"]["investment_home"]["output_schema"]["properties"][
            "production_health"
        ]["properties"]["baseline"]["properties"]["status"]["enum"].append("future")

    result = validate_compatibility(provider, baseline_requirements())

    assert result["compatible"] is False
    assert expected_code in {failure["code"] for failure in result["failures"]}
    assert result["scopes"]["baseline"]["status"] == "degraded"


@pytest.mark.parametrize(("field", "value"), [("workflows", []), ("rationale", "")])
def test_validator_rejects_requirement_without_usage_evidence(field: str, value):
    requirements = baseline_requirements()
    requirements["capabilities"]["investment_home"][field] = value

    result = validate_compatibility(
        investment_capability_registry(INVESTMENT_TOOLS).provider_manifest().document,
        requirements,
    )

    assert result["compatible"] is False
    assert {
        (failure["code"], failure.get("field")) for failure in result["failures"]
    } >= {("invalid_requirement", field)}


@pytest.mark.parametrize(
    "provider_input",
    [
        {
            "type": "object",
            "properties": {"mode": {"type": "string", "enum": ["safe"]}},
            "required": ["mode"],
            "additionalProperties": False,
        },
        {
            "type": "object",
            "properties": {
                "items": {
                    "type": "array",
                    "items": {"type": "string"},
                    "maxItems": 1,
                }
            },
            "required": ["items"],
            "additionalProperties": False,
        },
    ],
)
def test_validator_rejects_provider_that_narrows_consumer_input(provider_input):
    provider = deepcopy(
        investment_capability_registry(INVESTMENT_TOOLS).provider_manifest().document
    )
    requirements = baseline_requirements()
    requirement_input = deepcopy(provider_input)
    if "mode" in requirement_input["properties"]:
        del requirement_input["properties"]["mode"]["enum"]
    else:
        del requirement_input["properties"]["items"]["maxItems"]
    provider["capabilities"]["investment_home"]["input_schema"] = provider_input
    requirements["capabilities"]["investment_home"]["input_schema"] = requirement_input

    result = validate_compatibility(provider, requirements)

    assert result["compatible"] is False
    assert "input_schema_not_covered" in {
        failure["code"] for failure in result["failures"]
    }


def test_registry_rejects_home_handler_result_that_violates_output_contract():
    registry = investment_capability_registry(INVESTMENT_TOOLS)
    companion = SimpleNamespace(
        investment=SimpleNamespace(home=lambda: {"schema": "known-drift-without-health"})
    )

    with pytest.raises(CompanionError, match="capability.output.invalid"):
        registry.invoke(companion, "investment_home", {}, actor="test")


def test_registry_rejects_invalid_dynamic_workflow_status(tmp_path):
    registry = investment_capability_registry(INVESTMENT_TOOLS)
    real = Companion(tmp_path, gate_scope="test_fixture")
    real.initialize()
    result = real.investment.home()
    result["production_health"]["workflows"] = {
        "decide-investment": {
            "status": "NOT_A_SCOPE_STATUS",
            "incidents": [],
        }
    }
    companion = SimpleNamespace(
        investment=SimpleNamespace(home=lambda: result)
    )

    with pytest.raises(CompanionError, match="capability.output.invalid"):
        registry.invoke(companion, "investment_home", {}, actor="test")


def test_platform_health_uses_an_injected_registry_without_interface_dependency(
    tmp_path,
):
    registry = investment_capability_registry(INVESTMENT_TOOLS)

    companion = Companion(
        tmp_path,
        gate_scope="test_fixture",
        capability_registry=registry,
    )

    assert companion.capability_registry is registry
    for relative_path in (
        "companion/platform/production_health.py",
        "companion/platform/operations.py",
    ):
        source = (Path(__file__).parents[1] / relative_path).read_text(encoding="utf-8")
        assert "interfaces.mcp_profiles" not in source


def test_real_investment_mcp_profile_captures_home_production_health_drift(tmp_path):
    observation = probe_investment_mcp(tmp_path)
    drift_observation = probe_investment_mcp(
        tmp_path / "drift", fault="omit_home_production_health"
    )

    positive = evaluate_investment_conformance(observation)
    negative = evaluate_investment_conformance(drift_observation)

    assert positive == {
        "passed": True,
        "profile": "investment",
        "checks": [
            {"id": "mcp.initialize", "passed": True},
            {"id": "mcp.tools-list.investment-home", "passed": True},
            {"id": "investment_home.production_health.required/v1", "passed": True},
        ],
        "failures": [],
    }
    assert negative["passed"] is False
    assert negative["failures"] == [{
        "code": "invariant_violation",
        "capability": "investment_home",
        "invariant": "investment_home.production_health.required/v1",
        "counterexample": "tools/call succeeded without required production_health",
    }]
    assert not {
        "capability_provider_manifest",
        "capability_requirements",
        "compatibility_receipt",
    } & set(observation["tool_names"])


def test_contract_digest_normalizes_set_like_array_order():
    requirements = baseline_requirements()
    reordered = deepcopy(requirements)
    home = reordered["capabilities"]["investment_home"]
    home["workflows"] = list(reversed(home["workflows"]))
    home["required_outputs"] = list(reversed(home["required_outputs"]))
    home["invariants"] = list(reversed(home["invariants"]))
    status_schema = next(
        item["schema"]
        for item in home["required_outputs"]
        if item["path"] == "production_health.baseline.status"
    )
    status_schema["enum"] = list(reversed(status_schema["enum"]))

    assert content_digest(reordered) == content_digest(requirements)


def test_optional_enhancement_failure_does_not_degrade_required_scopes(
    tmp_path, monkeypatch
):
    registry = investment_capability_registry(INVESTMENT_TOOLS)
    provider = registry.provider_manifest()
    requirements = baseline_requirements()
    requirements["capabilities"]["missing_optional"] = {
        "level": "optional_enhancement",
        "workflows": ["investment_home"],
        "rationale": "Adds a richer explanation when available.",
        "fallback": "Use the baseline health summary.",
        "input_schema": {
            "type": "object",
            "properties": {},
            "required": [],
            "additionalProperties": False,
        },
        "required_outputs": [],
        "errors": [],
        "invariants": [],
    }
    validation = validate_compatibility(provider.document, requirements)
    state_dir = tmp_path / ".state" / "capability-contract"
    receipt = issue_compatibility_receipt(
        state_dir=state_dir,
        environment="non_production",
        provider=provider.document,
        requirements=requirements,
        validation=validation,
        conformance={
            "passed": True,
            "profile": "investment",
            "checks": [],
            "failures": [],
        },
        mcp_profile="investment",
        core_identity="core-test",
        plugin_identity="plugin-test",
    )
    requirements_path = tmp_path / "requirements.json"
    requirements_path.write_text(json.dumps(requirements), encoding="utf-8")
    monkeypatch.setenv("COMPANION_PLUGIN_REQUIREMENTS", str(requirements_path))
    monkeypatch.setenv("COMPANION_CAPABILITY_RECEIPT_DIR", str(state_dir))
    monkeypatch.setenv("COMPANION_MCP_PROFILE", "investment")

    summary = compatibility_summary(registry, tmp_path, "test_fixture")

    assert validation["compatible"] is True
    assert summary["ok"] is True
    assert summary["baseline"]["status"] == "compatible"
    assert summary["workflows"]["investment_home"]["status"] == "compatible"
    assert summary["optional_enhancements"]["missing_optional"]["status"] == "degraded"
    assert summary["receipt_digest"] == receipt["digest"]


def test_non_production_receipt_is_content_addressed_and_production_is_blocked(tmp_path):
    provider = investment_capability_registry(INVESTMENT_TOOLS).provider_manifest()
    requirements = baseline_requirements()
    validation = validate_compatibility(provider.document, requirements)
    conformance = evaluate_investment_conformance(probe_investment_mcp(tmp_path / "mcp"))
    receipt_state = tmp_path / "deployment" / "capability-contract"

    first = issue_compatibility_receipt(
        state_dir=receipt_state,
        environment="non_production",
        provider=provider.document,
        requirements=requirements,
        validation=validation,
        conformance=conformance,
        mcp_profile="investment",
        core_identity="core-test",
        plugin_identity="plugin-test",
    )
    second = issue_compatibility_receipt(
        state_dir=receipt_state,
        environment="non_production",
        provider=provider.document,
        requirements=requirements,
        validation=validation,
        conformance=conformance,
        mcp_profile="investment",
        core_identity="core-test",
        plugin_identity="plugin-test",
    )

    assert first == second
    assert first["path"].name == first["digest"].removeprefix("sha256:") + ".json"
    assert first["receipt"]["provider_digest"] == provider.digest
    assert first["receipt"]["requirements_digest"] == validation["requirements_digest"]
    assert first["receipt"]["mcp_profile"] == "investment"
    assert first["receipt"]["conformance"] == conformance
    assert first["receipt"]["release_pair"] == {
        "core": "core-test",
        "plugin": "plugin-test",
    }
    assert (receipt_state / "current.json").is_file()

    with pytest.raises(CompanionError, match="uncontracted capabilities"):
        issue_compatibility_receipt(
            state_dir=receipt_state,
            environment="production",
            provider=provider.document,
            requirements=requirements,
            validation=validation,
            conformance=conformance,
            mcp_profile="investment",
            core_identity="core-test",
            plugin_identity="plugin-test",
        )

    pointer_path = receipt_state / "current.json"
    pointer = json.loads(pointer_path.read_text(encoding="utf-8"))
    pointer["environment"] = "production"
    pointer_path.write_text(json.dumps(pointer), encoding="utf-8")
    with pytest.raises(CompanionError, match="environment mismatch"):
        read_current_receipt(receipt_state)


def test_runtime_compares_current_digests_with_receipt_without_rerunning_conformance(
    tmp_path, monkeypatch
):
    registry = investment_capability_registry(INVESTMENT_TOOLS)
    provider = registry.provider_manifest()
    requirements = baseline_requirements()
    validation = validate_compatibility(provider.document, requirements)
    conformance = evaluate_investment_conformance(probe_investment_mcp(tmp_path / "mcp"))
    state_dir = tmp_path / ".state" / "capability-contract"
    receipt = issue_compatibility_receipt(
        state_dir=state_dir,
        environment="non_production",
        provider=provider.document,
        requirements=requirements,
        validation=validation,
        conformance=conformance,
        mcp_profile="investment",
        core_identity="core-test",
        plugin_identity="plugin-test",
    )
    requirements_path = tmp_path / "plugin-requirements.json"
    requirements_path.write_text(json.dumps(requirements), encoding="utf-8")
    monkeypatch.setenv("COMPANION_PLUGIN_REQUIREMENTS", str(requirements_path))
    monkeypatch.setenv("COMPANION_CAPABILITY_RECEIPT_DIR", str(state_dir))
    monkeypatch.setenv("COMPANION_MCP_PROFILE", "investment")
    monkeypatch.setattr(
        "companion.capabilities.conformance.subprocess.run",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("conformance reran")),
    )

    current = compatibility_summary(registry, tmp_path, "test_fixture")
    production = compatibility_summary(registry, tmp_path, "production")
    changed = deepcopy(requirements)
    changed["capabilities"]["investment_home"]["rationale"] += " Changed."
    requirements_path.write_text(json.dumps(changed), encoding="utf-8")
    drifted = compatibility_summary(registry, tmp_path, "test_fixture")

    assert current["ok"] is True
    assert current["baseline"]["status"] == "compatible"
    assert current["provider_digest"] == provider.digest
    assert current["requirements_digest"] == validation["requirements_digest"]
    assert current["receipt_digest"] == receipt["digest"]
    assert {
        incident["code"] for incident in production["incidents"]
    } >= {
        "compatibility.non_production_receipt",
        "compatibility.release_pair_identity_missing",
    }
    assert drifted["ok"] is False
    assert drifted["baseline"]["status"] == "degraded"
    assert [incident["code"] for incident in drifted["incidents"]] == [
        "compatibility.requirements_digest_mismatch"
    ]

    requirements_path.write_text(json.dumps(requirements), encoding="utf-8")
    from companion.core import Companion

    companion = Companion(
        tmp_path,
        gate_scope="test_fixture",
        capability_registry=registry,
    )
    companion.initialize()
    home = companion.investment.home()
    doctor = companion.doctor()
    model_doctor = companion.investment.workflow_context(view="doctor")

    assert home["production_health"]["baseline"]["status"] == "compatible"
    assert doctor["compatibility"]["summary"]["receipt_digest"] == receipt["digest"]
    assert doctor["compatibility"]["receipt"] == receipt["receipt"]
    assert model_doctor["compatibility"] == doctor["compatibility"]["summary"]
    assert "receipt" not in model_doctor["compatibility"]


def test_shared_cli_validates_requirement_document(tmp_path):
    path = tmp_path / "requirements.json"
    path.write_text(json.dumps(baseline_requirements()), encoding="utf-8")

    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "companion.capabilities",
            "validate",
            "--requirements",
            str(path),
        ],
        cwd=Path(__file__).parents[1],
        text=True,
        capture_output=True,
        check=False,
    )

    result = json.loads(proc.stdout)
    assert proc.returncode == 0
    assert result["compatible"] is True
    assert result["failures"] == []


def test_receipt_cli_verifies_release_identity_against_checkout(tmp_path):
    from companion.capabilities.__main__ import _verified_git_identity

    checkout = tmp_path / "release"
    checkout.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=checkout, check=True)
    (checkout / "release.txt").write_text("candidate\n", encoding="utf-8")
    subprocess.run(["git", "add", "release.txt"], cwd=checkout, check=True)
    subprocess.run(
        [
            "git",
            "-c",
            "user.name=Capability Test",
            "-c",
            "user.email=capability@example.invalid",
            "commit",
            "-qm",
            "candidate",
        ],
        cwd=checkout,
        check=True,
    )
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=checkout,
        text=True,
        capture_output=True,
        check=True,
    ).stdout.strip()

    assert _verified_git_identity(checkout, head, "core") == head
    with pytest.raises(CompanionError, match="core identity mismatch"):
        _verified_git_identity(checkout, "0" * 40, "core")
