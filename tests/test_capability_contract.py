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


def test_provider_manifest_contracts_personal_finance_research_decision_and_program_workflows():
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
    contracted = {
        "investment_home",
        "portfolio_context",
        "investment_context_update",
        "investment_transaction_update",
        "research_context",
        "investment_opportunity_update",
        "investment_evidence_update",
        "investment_research_publish",
        "decision_context",
        "investment_decision_publish",
        "investment_action_plan",
        "investment_action_update",
        "investment_program_context",
        "investment_program_update",
        "investment_brief_update",
        "investment_workflow_context",
        "investment_workflow_update",
        "investment_delivery_update",
    }
    assert {
        name for name, capability in first.document["capabilities"].items()
        if capability["status"] == "uncontracted"
    } == set(INVESTMENT_TOOLS) - contracted
    assert {
        name for name, capability in first.document["capabilities"].items()
        if capability["status"] == "contracted"
    } == contracted

    context = first.document["capabilities"]["investment_context_update"]
    assert context["handler"] == "investment_commands.context_update"
    assert set(context["operations"]) == {"draft", "confirm"}
    assert {
        variant["properties"]["operation"]["const"]
        for variant in context["input_schema"]["oneOf"]
    } == {"draft", "confirm"}
    transaction = first.document["capabilities"]["investment_transaction_update"]
    assert transaction["handler"] == "investment_commands.transaction_update"
    assert set(transaction["operations"]) == {
        "account_create",
        "asset_register",
        "record",
        "confirm",
        "reverse",
        "reconcile",
        "continuity_confirm",
        "continuity_revoke",
    }
    assert first.document["capabilities"]["portfolio_context"]["handler"] == (
        "investment.portfolio_context"
    )
    research_context = first.document["capabilities"]["research_context"]
    assert research_context["handler"] == "investment.research_context"
    assert research_context["invariants"] == [
        "research.evidence_validation_thesis_never_auto_action/v1"
    ]
    opportunity = first.document["capabilities"]["investment_opportunity_update"]
    assert opportunity["handler"] == "investment_commands.opportunity_update"
    assert set(opportunity["operations"]) == {
        "create", "transition", "work_claim", "triage_complete", "research_complete"
    }
    assert len([
        variant
        for variant in opportunity["input_schema"]["oneOf"]
        if variant["properties"]["operation"]["const"] == "research_complete"
    ]) == 3
    evidence = first.document["capabilities"]["investment_evidence_update"]
    assert set(evidence["operations"]) == {"publish_source", "market_snapshot"}
    assert first.document["capabilities"]["investment_research_publish"]["handler"] == (
        "investment_commands.research_publish"
    )
    decision_context = first.document["capabilities"]["decision_context"]
    assert decision_context["handler"] == "investment.decision_context"
    assert {
        "decision.research_validation_is_not_decision/v1",
        "action_card.acceptance_never_changes_portfolio/v1",
    } <= set(decision_context["invariants"])
    decision_publish = first.document["capabilities"]["investment_decision_publish"]
    assert decision_publish["handler"] == "investment_commands.decision_publish"
    assert set(decision_publish["operations"]) == {
        "standard_action",
        "bounded_action",
        "no_action",
        "watch",
    }
    assert {
        variant["properties"]["decision_kind"]["const"]
        for variant in decision_publish["input_schema"]["oneOf"]
    } == {"action", "conditional_action", "no_action", "watch"}
    action_plan = first.document["capabilities"]["investment_action_plan"]
    assert set(action_plan["operations"]) == {"standard", "bounded"}
    assert {
        variant["properties"]["action_tier"]["const"]
        for variant in action_plan["input_schema"]["oneOf"]
    } == {"standard", "bounded"}
    action_update = first.document["capabilities"]["investment_action_update"]
    assert action_update["handler"] == "investment_commands.action_update"
    assert set(action_update["operations"]) == {
        "enqueue",
        "presented",
        "accepted",
        "rejected",
        "snoozed",
        "closed",
    }
    program_context = first.document["capabilities"]["investment_program_context"]
    assert program_context["handler"] == "investment.program_context"
    assert program_context["output_schema"]["properties"]["truth"]["const"] == (
        "immutable_program_revisions_and_confirmed_context_refs"
    )
    program_update = first.document["capabilities"]["investment_program_update"]
    assert program_update["handler"] == "investment_commands.program_update"
    assert set(program_update["operations"]) == {
        "create", "revise", "confirm", "status"
    }
    assert set(program_update["errors"]) == {
        "capability.input.invalid",
        "capability.output.invalid",
        "investment_program.version_conflict",
    }
    assert {
        variant["properties"]["operation"]["const"]
        for variant in program_update["input_schema"]["oneOf"]
    } == {"create", "revise", "confirm", "status"}
    assert {
        variant["properties"]["status"]["const"]
        for variant in program_update["input_schema"]["oneOf"]
        if variant["properties"]["operation"]["const"] == "status"
    } == {"active", "paused", "archived"}
    brief_update = first.document["capabilities"]["investment_brief_update"]
    assert brief_update["handler"] == "investment_commands.brief_update"
    assert set(brief_update["operations"]) == {
        "publish", "presented", "metrics_calculate", "scorecard_publish"
    }
    assert set(brief_update["errors"]) == {
        "capability.input.invalid",
        "capability.output.invalid",
        "investment_program.not_active",
        "investment_brief.unresolved_obligations",
        "investment_brief.calculation_lineage_required",
    }
    assert {
        variant["properties"]["brief_type"]["const"]
        for variant in brief_update["input_schema"]["oneOf"]
        if variant["properties"]["operation"]["const"] == "publish"
    } == {"daily", "weekly", "monthly"}
    workflow_context = first.document["capabilities"]["investment_workflow_context"]
    assert workflow_context["handler"] == "investment.workflow_context"
    assert set(workflow_context["operations"]) == {
        "schedules", "schedule", "schedule_history", "runs", "run",
        "deliveries", "delivery", "delivery_status", "system_status", "doctor",
    }
    assert workflow_context["pending_variants"] == {
        "view": ["execution_strategies", "execution_strategy"]
    }
    assert {
        variant["properties"]["view"]["const"]
        for variant in workflow_context["input_schema"]["oneOf"]
    } == set(workflow_context["operations"])
    workflow_update = first.document["capabilities"]["investment_workflow_update"]
    assert workflow_update["handler"] == "investment_commands.workflow_update"
    assert set(workflow_update["operations"]) == {
        "schedule_create", "schedule_patch", "schedule_status",
        "schedule_run_now", "run_complete", "run_cancel", "wake_claim",
        "wake_complete",
    }
    workflow_variants = workflow_update["input_schema"]["oneOf"]
    assert {
        variant["properties"]["status"]["const"]
        for variant in workflow_variants
        if variant["properties"]["operation"]["const"] == "schedule_status"
    } == {"active", "paused", "archived"}
    assert all(
        "expected_version" in variant["required"]
        for variant in workflow_variants
        if variant["properties"]["operation"]["const"] == "schedule_status"
    )
    delivery_update = first.document["capabilities"]["investment_delivery_update"]
    assert delivery_update["handler"] == "investment_commands.delivery_update"
    assert set(delivery_update["operations"]) == {
        "prepare", "digest_send", "attention_decide", "attention_delivered",
        "attention_feedback",
    }
    for capability_name in (
        "decision_context",
        "investment_decision_publish",
        "investment_action_plan",
        "investment_action_update",
        "investment_program_update",
    ):
        assert first.document["capabilities"][capability_name]["output_schema"][
            "additionalProperties"
        ] is True
    assert all(
        operation["output_schema"]["additionalProperties"] is True
        for capability_name in (
            "investment_decision_publish",
            "investment_action_plan",
            "investment_action_update",
            "investment_program_update",
            "investment_brief_update",
        )
        for operation in first.document["capabilities"][capability_name][
            "operations"
        ].values()
    )
    model_visible_program_surface = json.dumps(
        {
            name: INVESTMENT_TOOLS[name]
            for name in (
                "investment_program_context",
                "investment_program_update",
                "investment_brief_update",
            )
        },
        sort_keys=True,
    ).lower()
    assert all(
        token not in model_visible_program_surface
        for token in (
            "receipt",
            "provider_manifest",
            "pipeline_metadata",
            "job_metadata",
        )
    )


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


def test_plugin_usage_audit_reports_contracted_research_capability_missing_from_requirements(
    tmp_path,
):
    prose = tmp_path / "SKILL.md"
    prose.write_text(
        "Use `research_context` and `investment_research_publish` for research.",
        encoding="utf-8",
    )

    result = validate_compatibility(
        investment_capability_registry(INVESTMENT_TOOLS).provider_manifest().document,
        baseline_requirements(),
        usage_sources=[prose],
    )

    assert result["compatible"] is False
    assert result["usage_audit"]["ok"] is False
    assert {
        failure["capability"]
        for failure in result["usage_audit"]["failures"]
        if failure["code"] == "undeclared_capability_usage"
    } == {"research_context", "investment_research_publish"}


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


@pytest.mark.parametrize(
    ("drift", "expected_code"),
    [
        ("missing_operation", "missing_operation"),
        ("missing_operation_output", "missing_operation_output"),
    ],
)
def test_validator_checks_required_operation_outputs(drift, expected_code):
    provider = deepcopy(
        investment_capability_registry(INVESTMENT_TOOLS).provider_manifest().document
    )
    transaction = provider["capabilities"]["investment_transaction_update"]
    requirements = baseline_requirements()
    requirements["capabilities"]["investment_transaction_update"] = {
        "level": "workflow_required",
        "workflows": ["manage-investment-lifecycle"],
        "rationale": "The workflow confirms a specifically referenced pending entry.",
        "input_schema": deepcopy(transaction["input_schema"]),
        "required_outputs": [],
        "required_operations": {
            "confirm": {
                "required_outputs": [
                    {
                        "path": "status",
                        "schema": {"type": "string", "const": "confirmed"},
                    }
                ]
            }
        },
        "errors": ["capability.input.invalid"],
        "invariants": [
            "investment_transaction_update.confirmed_ledger_only_changes_portfolio/v1"
        ],
    }
    if drift == "missing_operation":
        del transaction["operations"]["confirm"]
    else:
        transaction["operations"]["confirm"]["output_schema"]["required"].remove(
            "status"
        )

    result = validate_compatibility(provider, requirements)

    assert result["compatible"] is False
    assert expected_code in {failure["code"] for failure in result["failures"]}


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


@pytest.mark.parametrize(
    "arguments",
    [
        {"operation": "confirm"},
        {"operation": "draft", "revision_id": "ctx_wrong_variant"},
        {"operation": "confirm", "revision_id": "ctx_1", "content": {}},
    ],
)
def test_registry_rejects_ambiguous_or_incomplete_context_variants(arguments):
    registry = investment_capability_registry(INVESTMENT_TOOLS)

    with pytest.raises(CompanionError, match="capability.input.invalid"):
        registry.invoke(SimpleNamespace(), "investment_context_update", arguments, actor="test")


@pytest.mark.parametrize(
    "arguments",
    [
        {"operation": "confirm"},
        {"operation": "continuity_revoke", "reason": "missing confirmation"},
        {
            "operation": "continuity_confirm",
            "account_id": "acct_1",
            "confirmed_at": "2025-01-01T00:00:00Z",
            "reporting_commitment": True,
        },
        {"operation": "confirm", "entry_id": "led_1", "amount": "100"},
    ],
)
def test_registry_rejects_missing_references_and_cross_operation_fields(arguments):
    registry = investment_capability_registry(INVESTMENT_TOOLS)

    with pytest.raises(CompanionError, match="capability.input.invalid"):
        registry.invoke(SimpleNamespace(), "investment_transaction_update", arguments, actor="test")


@pytest.mark.parametrize(
    "arguments",
    [
        {"operation": "create", "subject": {"asset_id": "fixture"}, "reason": "missing refs"},
        {"operation": "work_claim", "owner": "researcher"},
        {
            "operation": "triage_complete",
            "item_id": "researchwork_1",
            "dispositions": [
                {
                    "candidate_id": "fixture",
                    "outcome": "monitor",
                    "reason": "missing next check",
                }
            ],
        },
        {
            "operation": "research_complete",
            "item_id": "researchwork_1",
            "outcome": "promoted",
            "reason": "missing formal references",
        },
        {
            "operation": "research_complete",
            "item_id": "researchwork_1",
            "outcome": "rejected",
            "reason": "cross-variant field",
            "next_check_at": "2030-01-01T00:00:00Z",
        },
    ],
)
def test_registry_rejects_ambiguous_or_incomplete_research_operation_variants(arguments):
    registry = investment_capability_registry(INVESTMENT_TOOLS)

    with pytest.raises(CompanionError, match="capability.input.invalid"):
        registry.invoke(
            SimpleNamespace(),
            "investment_opportunity_update",
            arguments,
            actor="test",
        )


@pytest.mark.parametrize(
    ("capability", "arguments"),
    [
        (
            "investment_decision_publish",
            {
                "decision_kind": "action",
                "subject": {"asset_id": "asset_1"},
                "content": "missing action qualifications",
                "account_id": "account_1",
                "as_of": "2026-01-01T00:00:00Z",
                "knowledge_cutoff": "2026-01-01T00:00:00Z",
                "valid_until": "2026-01-02T00:00:00Z",
                "thesis_revision_ids": ["revision_1"],
                "evidence_manifest_ids": ["manifest_1"],
                "invalidators": ["price leaves range"],
                "no_action": {"choice": "hold cash"},
                "alternatives": [{"choice": "smaller position"}],
            },
        ),
        (
            "investment_action_plan",
            {
                "action_tier": "bounded",
                "as_of": "2026-01-01T00:00:00Z",
                "account_id": "account_1",
                "asset_id": "asset_1",
                "quantity": "100",
                "price": "10",
                "reality_spec": {},
                "market_snapshot_id": "market_1",
                "max_market_age_seconds": 60,
                "valid_until": "2026-01-02T00:00:00Z",
                "price_range": {"min": "9", "max": "11"},
            },
        ),
        (
            "investment_action_update",
            {
                "operation": "respond",
                "queue_id": "queue_1",
                "state": "accepted",
            },
        ),
        (
            "investment_action_update",
            {
                "operation": "respond",
                "queue_id": "queue_1",
                "state": "snoozed",
                "reason": "review later",
                "user_confirmation_ref": "message_1",
            },
        ),
    ],
)
def test_registry_rejects_incomplete_decision_and_action_variants(capability, arguments):
    registry = investment_capability_registry(INVESTMENT_TOOLS)

    with pytest.raises(CompanionError, match="capability.input.invalid"):
        registry.invoke(SimpleNamespace(), capability, arguments, actor="test")


@pytest.mark.parametrize(
    ("capability", "arguments"),
    [
        (
            "investment_program_update",
            {"operation": "confirm", "revision_id": "programrev_1"},
        ),
        (
            "investment_program_update",
            {
                "operation": "status",
                "program_id": "program_1",
                "status": "paused",
                "reason": "missing optimistic version",
            },
        ),
        (
            "investment_program_update",
            {
                "operation": "revise",
                "program_id": "program_1",
                "expected_version": 1,
                "reason": "cross-variant field",
                "user_approval_ref": "message_1",
            },
        ),
        (
            "investment_brief_update",
            {
                "operation": "publish",
                "brief_type": "weekly",
                "period_key": "2026-W35",
                "as_of": "2026-08-29T00:00:00Z",
                "conclusion": "review_required",
                "payload": {
                    "summary": "missing weekly projections",
                    "what_changed": [],
                    "decision": "review",
                    "risks": [],
                    "next_check_at": "2026-08-30T00:00:00Z",
                    "queue_item_ids": [],
                },
                "source_refs": [],
            },
        ),
        (
            "investment_brief_update",
            {
                "operation": "scorecard_publish",
                "period_start": "2026-08-01T00:00:00Z",
                "period_end": "2026-08-29T00:00:00Z",
                "metrics": [{"name": "return", "value": "0.1"}],
                "comparisons": [],
                "source_refs": [],
                "caveats": [],
            },
        ),
    ],
)
def test_registry_rejects_incomplete_program_and_brief_variants(
    capability, arguments
):
    registry = investment_capability_registry(INVESTMENT_TOOLS)

    with pytest.raises(CompanionError, match="capability.input.invalid"):
        registry.invoke(SimpleNamespace(), capability, arguments, actor="test")


@pytest.mark.parametrize(
    ("capability", "arguments"),
    [
        ("investment_workflow_context", {"view": "schedule"}),
        (
            "investment_workflow_context",
            {"view": "execution_strategy", "run_id": "plan_1"},
        ),
        (
            "investment_workflow_update",
            {
                "operation": "schedule_status",
                "schedule_id": "schedule_1",
                "status": "paused",
            },
        ),
        (
            "investment_workflow_update",
            {"operation": "wake_claim", "owner": "worker", "lease_seconds": 30},
        ),
        (
            "investment_workflow_update",
            {"operation": "run_complete", "run_id": "run_1", "success": False},
        ),
        (
            "investment_workflow_update",
            {
                "operation": "wake_complete",
                "outbox_id": "outbox_1",
                "owner": "worker",
                "success": False,
            },
        ),
        (
            "investment_delivery_update",
            {
                "operation": "prepare",
                "delivery_id": "delivery_1",
                "conclusion": "no_action",
                "summary": "missing next step",
                "key_evidence": [],
            },
        ),
        (
            "investment_delivery_update",
            {
                "operation": "digest_send",
                "delivery_ids": ["delivery_1", "delivery_1"],
                "conclusion": "no_action",
                "summary": "duplicate records",
                "key_evidence": [],
                "next_step": "wait",
            },
        ),
        (
            "investment_delivery_update",
            {
                "operation": "attention_feedback",
                "attention_decision_id": "attention_1",
                "feedback": "silently_change_policy",
            },
        ),
    ],
)
def test_registry_rejects_incomplete_workflow_and_delivery_variants(
    capability, arguments
):
    registry = investment_capability_registry(INVESTMENT_TOOLS)

    with pytest.raises(CompanionError, match="capability.input.invalid"):
        registry.invoke(SimpleNamespace(), capability, arguments, actor="test")


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

    assert positive["passed"] is True
    assert positive["profile"] == "investment"
    assert positive["failures"] == []
    assert all(check["passed"] for check in positive["checks"])
    assert {check["id"] for check in positive["checks"]} >= {
        "investment_context_update.draft_requires_confirmation/v1",
        "portfolio_context.confirmed_ledger_only/v1",
        "portfolio_context.truth_and_precision_explicit/v1",
        "investment_transaction_update.confirmed_ledger_only_changes_portfolio/v1",
        "investment_transaction_update.reconciliation_never_autofills/v1",
        "investment_transaction_update.continuity_is_not_broker_sync/v1",
        "investment_opportunity_update.explicit_candidate_disposition/v1",
        "research.evidence_validation_thesis_never_auto_action/v1",
        "mcp.research-work.outcomes/v1",
        "mcp.research-negative-cases.rejected/v1",
        "mcp.required-confirmation-references.rejected/v1",
        "decision.research_validation_is_not_decision/v1",
        "investment_action_plan.risk_gate_is_veto_not_thesis/v1",
        "action_card.acceptance_never_changes_portfolio/v1",
        "mcp.tools-list.decision-publish-variants",
        "mcp.tools-list.action-plan-variants",
        "mcp.tools-list.action-update-variants",
        "investment_program.confirmation_and_versioning_required/v1",
        "investment_program.references_context_without_owning_truth/v1",
        "investment_brief.no_action_requires_resolved_obligations/v1",
        "investment_brief.references_calculations_without_owning_truth/v1",
        "mcp.brief.calculation-lineage-required/v1",
        "mcp.tools-list.program-context",
        "mcp.tools-list.program-update-variants",
        "mcp.tools-list.brief-update-variants",
        "mcp.tools-list.workflow-context-variants",
        "mcp.tools-list.workflow-update-variants",
        "mcp.tools-list.delivery-update-variants",
        "investment_workflow.schedule_mutations_require_current_version/v1",
        "investment_workflow.wake_lease_is_exclusive/v1",
        "investment_workflow.run_success_is_not_delivery/v1",
        "investment_delivery.status_is_transport_receipt/v1",
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
    assert all(
        set(tool) == {"name", "description", "inputSchema"}
        for tool in observation["tools"].values()
    )
    assert observation["ledger"]["pending_entry"]["status"] == "needs_confirmation"
    assert observation["ledger"]["confirmed_entry"]["status"] == "confirmed"
    assert observation["ledger"]["reversal_entry"]["entry_type"] == "reversal"
    assert observation["reconciliation"]["mismatch"]["status"] == "needs_review"
    assert observation["continuity"]["portfolio"]["precision_boundary"] == {
        "current_broker_position_proven": False,
        "ledger_position_continuity_supported": True,
        "precise_position_advice_allowed": True,
        "conditional_position_advice_allowed": True,
        "market_revaluation_required": True,
        "market_moves_do_not_invalidate_quantities": True,
        "final_order_quantities_require_broker_preflight": True,
        "required_when_stale": "refresh market prices and verify broker available cash/holdings before submitting the final order",
    }
    assert observation["research"]["promoted_research"]["validation"]["status"] == (
        "eligible_for_decision"
    )
    assert {
        name: item["status"]
        for name, item in observation["research"]["outcomes"].items()
    } == {
        "promoted": "completed",
        "rejected": "rejected",
        "monitoring": "monitoring",
    }
    decision_action = observation["decision_action"]
    assert decision_action["decision"]["revision"]["metadata"]["decision_kind"] == (
        "action"
    )
    assert decision_action["plans"]["standard"]["risk"]["status"] == "pass"
    assert decision_action["plans"]["bounded"][
        "eligible_for_conditional_decision"
    ] is True
    assert decision_action["bounded_decision"]["revision"]["metadata"][
        "action_tier"
    ] == "bounded"
    assert decision_action["plans"]["blocked"]["risk"]["status"] == "blocked"
    assert decision_action["queue"]["accepted"]["state"] == "accepted"
    assert decision_action["queue"]["rejected"]["state"] == "rejected"
    assert decision_action["after_accept"]["decision"]["executions"] == []
    program_brief = observation["program_brief"]
    assert program_brief["program"]["created"]["status"] == "draft"
    assert program_brief["program"]["confirmed"]["current_revision"][
        "user_approval_ref"
    ] == "synthetic:user-message:approve-program"
    assert program_brief["program"]["archived"]["status"] == "archived"
    assert program_brief["program"]["stale_revision_error"].startswith(
        "investment_program.version_conflict:"
    )
    assert program_brief["program"]["stale_status_error"].startswith(
        "investment_program.version_conflict:"
    )
    assert program_brief["program"]["unconfirmed_brief_error"].startswith(
        "investment_program.not_active:"
    )
    assert program_brief["brief"]["no_action_error"].startswith(
        "investment_brief.unresolved_obligations:"
    )
    assert program_brief["brief"]["missing_lineage_error"].startswith(
        "investment_brief.calculation_lineage_required:"
    )
    assert program_brief["brief"]["no_action"]["conclusion"] == (
        "no_action"
    )
    assert program_brief["brief"]["review_required"]["conclusion"] == (
        "review_required"
    )
    assert program_brief["brief"]["scorecard"]["metrics"][0][
        "calculation_id"
    ] == program_brief["brief"]["metrics"]["calculation_id"]
    workflow = observation["workflow_delivery"]
    assert workflow["schedule"]["resumed"]["version"] == 4
    assert workflow["required"]["completed"]["status"] == "succeeded"
    assert workflow["required"]["delivery_after_run"]["status"] == "pending_send"
    assert workflow["recoverable"]["run_after_failure"]["status"] == "recoverable"
    assert workflow["digest"]["prepared"]["status"] == "queued_digest"
    assert workflow["digest"]["sent"][0]["status"] == "pending_send"
    assert workflow["terminal_runs"]["failed"]["status"] == "failed"
    assert workflow["terminal_runs"]["cancelled"]["status"] == "cancelled"


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


def test_runtime_health_scopes_workflow_requirement_drift_to_affected_workflow(
    tmp_path, monkeypatch
):
    registry = investment_capability_registry(INVESTMENT_TOOLS)
    provider = registry.provider_manifest()
    requirements = {
        "format": "investment-companion.capability-requirements/v1",
        "consumer": "investment-companion-plugin",
        "capabilities": {
            "investment_workflow_update": {
                "level": "workflow_required",
                "workflows": ["manage-investment-companion"],
                "rationale": "The companion workflow mutates versioned schedules.",
                "input_schema": deepcopy(
                    provider.document["capabilities"]["investment_workflow_update"][
                        "input_schema"
                    ]
                ),
                "required_outputs": [],
                "required_operations": {},
                "errors": [],
                "invariants": [],
            },
            "research_context": {
                "level": "workflow_required",
                "workflows": ["research-investment"],
                "rationale": "Research remains independently readable.",
                "input_schema": deepcopy(
                    provider.document["capabilities"]["research_context"][
                        "input_schema"
                    ]
                ),
                "required_outputs": [],
                "required_operations": {},
                "errors": [],
                "invariants": [],
            },
        },
    }
    validation = validate_compatibility(provider.document, requirements)
    state_dir = tmp_path / ".state" / "capability-contract"
    issue_compatibility_receipt(
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
    drifted = deepcopy(requirements)
    drifted["capabilities"]["investment_workflow_update"][
        "required_operations"
    ] = {"future_schedule_replace": {"required_outputs": []}}
    requirements_path = tmp_path / "requirements.json"
    requirements_path.write_text(json.dumps(drifted), encoding="utf-8")
    monkeypatch.setenv("COMPANION_PLUGIN_REQUIREMENTS", str(requirements_path))
    monkeypatch.setenv("COMPANION_CAPABILITY_RECEIPT_DIR", str(state_dir))
    monkeypatch.setenv("COMPANION_MCP_PROFILE", "investment")

    summary = compatibility_summary(registry, tmp_path, "test_fixture")

    assert summary["ok"] is False
    assert summary["baseline"]["status"] == "degraded"
    assert summary["workflows"]["manage-investment-companion"]["status"] == (
        "degraded"
    )
    assert summary["workflows"]["research-investment"]["status"] == "compatible"
    assert {
        incident["code"]
        for incident in summary["workflows"]["manage-investment-companion"][
            "incidents"
        ]
    } == {"missing_operation"}


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
