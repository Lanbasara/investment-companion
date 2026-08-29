from __future__ import annotations

import ast
import json
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / "companion"


def internal_imports(module_path: Path) -> set[str]:
    modules = {path.stem for path in PACKAGE.glob("*.py")}
    tree = ast.parse(module_path.read_text(encoding="utf-8"))
    result: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            name = node.module.rsplit(".", 1)[-1]
            if name in modules:
                result.add(name)
    return result


def test_pure_kernels_do_not_depend_on_stateful_companion_modules():
    for module in ("foundation", "timeutil", "quant_runtime", "v4_data"):
        assert internal_imports(PACKAGE / f"{module}.py") == set()


def test_truth_owners_do_not_import_coordination_or_versioned_pipelines():
    forbidden = {
        "attention",
        "core",
        "delivery",
        "operating",
        "v5_quant_experiment",
        "v6_predictive_recommendations",
    }
    for module in ("financial", "cognition", "data_domain"):
        assert internal_imports(PACKAGE / f"{module}.py").isdisjoint(forbidden)


def test_domain_and_platform_modules_use_foundation_instead_of_composition_root():
    allowed_core_importers = {"cli", "mcp_server"}
    actual_core_importers = {
        path.stem
        for path in PACKAGE.glob("*.py")
        if path.stem != "core" and "core" in internal_imports(path)
    }
    assert actual_core_importers == allowed_core_importers


def test_services_use_public_audit_trail_instead_of_companion_private_method():
    for module in ("delivery.py", "jobs.py", "operating.py"):
        source = (PACKAGE / module).read_text(encoding="utf-8")
        assert ".c._audit(" not in source


def test_domain_services_use_public_calculation_registry():
    for module in ("risk.py", "performance.py", "operating.py"):
        source = (PACKAGE / module).read_text(encoding="utf-8")
        assert ".financial._record(" not in source
        assert ".financial.calculation_record(" in source


def test_companion_delegates_service_construction_to_composition_root():
    source = (PACKAGE / "core.py").read_text(encoding="utf-8")
    assert "compose_services(self)" in source
    for implementation in (
        "FinancialKernel(self)",
        "InvestmentOperatingSystem(self)",
        "ContinuousQuantResearch(self)",
        "V6PredictiveRecommendations(self)",
    ):
        assert implementation not in source


def test_deterministic_platform_handlers_are_not_added_back_to_companion():
    source = (PACKAGE / "core.py").read_text(encoding="utf-8")
    for method in (
        "_job_echo_manifest",
        "_job_publish_snapshot",
        "_job_tushare_ingest",
        "_job_shadow_rebalance",
    ):
        assert f"def {method}(" not in source


def test_schedule_and_run_workflow_are_not_hosted_by_companion_facade():
    facade = (PACKAGE / "core.py").read_text(encoding="utf-8")
    workflow = (PACKAGE / "platform" / "workflow.py").read_text(encoding="utf-8")
    for method in ("schedule_create", "tick", "route_pending_runs", "complete_run"):
        assert f"def {method}(" not in facade
        assert f"def {method}(" in workflow


def test_outbox_transport_is_not_hosted_by_companion_facade():
    facade = (PACKAGE / "core.py").read_text(encoding="utf-8")
    outbox = (PACKAGE / "platform" / "outbox.py").read_text(encoding="utf-8")
    for method in ("outbox_enqueue", "dispatch_outbox", "wake_claim", "wake_complete"):
        assert f"def {method}(" not in facade
        assert f"def {method}(" in outbox


def test_system_operations_are_not_hosted_by_companion_facade():
    facade = (PACKAGE / "core.py").read_text(encoding="utf-8")
    operations = (PACKAGE / "platform" / "operations.py").read_text(encoding="utf-8")
    for method in ("recover", "backup", "system_status", "doctor"):
        assert f"def {method}(" not in facade
        assert f"def {method}(" in operations


def test_risk_gate_is_a_deterministic_service_not_a_pipeline_or_agent():
    imports = internal_imports(PACKAGE / "risk.py")
    assert imports.isdisjoint(
        {"attention", "delivery", "operating", "v5_quant_experiment", "v6_predictive_recommendations"}
    )
    source = (PACKAGE / "risk.py").read_text(encoding="utf-8")
    assert "Agent" not in source


def test_actionability_is_a_gate_not_an_operating_or_versioned_pipeline():
    imports = internal_imports(PACKAGE / "actionability.py")
    assert imports.isdisjoint(
        {"operating", "v5_quant_experiment", "v6_predictive_recommendations"}
    )
    source = (PACKAGE / "actionability.py").read_text(encoding="utf-8")
    assert "UPDATE opportunities" not in source
    assert "INSERT INTO decision_queue_items" not in source
    assert "execution_create" not in source


def test_execution_lifecycle_has_no_broker_or_versioned_pipeline_dependency():
    imports = internal_imports(PACKAGE / "execution.py")
    assert imports.isdisjoint(
        {
            "delivery",
            "mcp_server",
            "v5_quant_experiment",
            "v6_predictive_recommendations",
        }
    )
    source = (PACKAGE / "execution.py").read_text(encoding="utf-8")
    assert "requests." not in source
    assert "http://" not in source and "https://" not in source
    assert "status=\"confirmed\"" not in source


def test_performance_measurement_does_not_depend_on_review_or_learning():
    imports = internal_imports(PACKAGE / "performance.py")
    assert imports.isdisjoint(
        {"cognition", "operating", "research", "v5_quant_experiment", "v6_predictive_recommendations"}
    )
    source = (PACKAGE / "performance.py").read_text(encoding="utf-8")
    assert "cognitive_revision" not in source
    assert "strategy_versions" not in source


def test_review_service_has_no_strategy_update_path():
    source = (PACKAGE / "review.py").read_text(encoding="utf-8")
    assert "UPDATE strategy_versions" not in source
    assert ".strategy_register(" not in source
    assert '"automatic_application": False' in source


def test_research_validation_is_version_neutral_and_cannot_mutate_investment_truth():
    imports = internal_imports(PACKAGE / "validation.py")
    assert imports.isdisjoint(
        {
            "core",
            "operating",
            "v5_quant_experiment",
            "v6_predictive_recommendations",
        }
    )
    source = (PACKAGE / "validation.py").read_text(encoding="utf-8")
    assert ".financial.calculation_record(" in source
    assert "UPDATE strategy_versions" not in source
    assert "INSERT INTO ledger_entries" not in source
    assert "automatic_decision_or_execution" in source


def test_action_decision_requires_both_research_validation_and_risk():
    source = (PACKAGE / "application" / "commands.py").read_text(encoding="utf-8")
    assert "action decision requires an eligible Research Validation Calculation" in source
    assert "action decision requires a passing Risk Gate Calculation" in source


def test_market_calendar_is_pure_and_version_neutral():
    imports = internal_imports(PACKAGE / "market_calendar.py")
    assert imports <= {"foundation", "timeutil"}
    source = (PACKAGE / "market_calendar.py").read_text(encoding="utf-8")
    assert "v5" not in source.lower()
    assert "v6" not in source.lower()


def test_application_facade_composes_truth_owners_without_owning_truth():
    source = (PACKAGE / "application" / "investment_home.py").read_text(encoding="utf-8")
    assert "INSERT INTO ledger_entries" not in source
    assert "UPDATE strategy_versions" not in source
    assert ".ledger_add(" not in source
    assert ".strategy_register(" not in source
    assert "human_manual_only" in source


def test_portfolio_qualification_is_the_only_account_precision_fact_assembler():
    home = (PACKAGE / "application" / "investment_home.py").read_text(
        encoding="utf-8"
    )
    commands = (PACKAGE / "application" / "commands.py").read_text(
        encoding="utf-8"
    )
    qualification = (PACKAGE / "portfolio_qualification.py").read_text(
        encoding="utf-8"
    )

    assert "def _portfolio_truth_freshness(" not in home
    assert "SELECT * FROM reconciliations" not in home
    assert "SELECT * FROM executions" not in home
    assert "PortfolioQualificationService" in qualification
    assert "evaluate_risk_candidate(" in commands
    assert "revalidate_frozen_risk_candidate(" in commands
    assert "portfolio_context(" not in commands
    assert "LEVEL_RANK" not in commands
    for mutation in (
        "INSERT INTO ledger_entries",
        "UPDATE ledger_entries",
        "INSERT INTO reconciliations",
        "UPDATE reconciliations",
        "INSERT INTO account_continuity_confirmations",
        "UPDATE account_continuity_confirmations",
        "INSERT INTO executions",
        "UPDATE executions",
        "INSERT INTO broker_execution_plans",
        "UPDATE broker_execution_plans",
    ):
        assert mutation not in qualification


def test_investment_program_lifecycle_is_not_hosted_by_operating_projection():
    operating = (PACKAGE / "operating.py").read_text(encoding="utf-8")
    programs = (PACKAGE / "application" / "programs.py").read_text(encoding="utf-8")
    assert "def program_create(" not in operating
    assert "def program_confirm(" not in operating
    assert "def program_create(" in programs
    assert "def program_confirm(" in programs


def test_portfolio_decision_and_queue_are_not_hosted_by_operating_projection():
    operating = (PACKAGE / "operating.py").read_text(encoding="utf-8")
    decisions = (PACKAGE / "application" / "portfolio_decisions.py").read_text(
        encoding="utf-8"
    )
    for method in ("opportunity_create", "opportunity_transition", "queue_enqueue", "queue_respond"):
        assert f"def {method}(" not in operating
        assert f"def {method}(" in decisions


def test_research_catalog_is_version_neutral_and_does_not_promote_pipeline_labels():
    source = (PACKAGE / "application" / "research_catalog.py").read_text(encoding="utf-8")
    assert "v5_" not in source.lower()
    assert "v6_" not in source.lower()
    assert ".strategy_register(" not in source
    assert "formal_strategy_requires_registry_entry" in source
    application_sources = "\n".join(
        path.read_text(encoding="utf-8")
        for path in (PACKAGE / "application").glob("*.py")
    )
    assert "v6_predictive_recommendations" not in application_sources


def test_briefing_projection_freezes_truth_without_becoming_a_truth_owner():
    source = (PACKAGE / "application" / "briefing.py").read_text(encoding="utf-8")
    assert ".financial.calculation_record(" in source
    assert "INSERT INTO ledger_entries" not in source
    assert "UPDATE executions" not in source
    assert ".ledger_add(" not in source
    assert ".execution_set_status(" not in source
    assert "automatic" not in source.lower()


def test_default_investment_tool_profile_is_bounded_complete_and_version_neutral():
    from companion.interfaces.mcp_profiles import INVESTMENT_TOOLS

    tools = list(INVESTMENT_TOOLS)
    assert 15 <= len(tools) <= 22
    assert not [name for name in tools if re.match(r"^v\d+_", name)]
    assert {
        "investment_home",
        "portfolio_context",
        "research_context",
        "investment_program_update",
        "investment_opportunity_update",
        "investment_evidence_update",
        "investment_action_update",
        "investment_workflow_update",
        "investment_delivery_update",
        "investment_brief_update",
    } <= set(tools)


def test_research_pipelines_cannot_write_investment_truths_directly():
    forbidden_fragments = (
        "INSERT INTO ledger_entries",
        "INSERT INTO executions",
        "INSERT INTO decision_queue_items",
        ".financial.ledger_",
        ".cognition.execution_",
        ".operating.queue_enqueue",
    )
    for module in ("v5_quant_experiment.py", "v6_predictive_recommendations.py"):
        source = (PACKAGE / module).read_text(encoding="utf-8")
        assert not [fragment for fragment in forbidden_fragments if fragment in source]


def test_legacy_god_modules_have_a_no_growth_budget():
    maximum_lines = {
        "core.py": 600,
        "db.py": 1102,
        "operating.py": 800,
        "v5_quant_experiment.py": 1607,
        "v6_predictive_recommendations.py": 1260,
    }
    actual = {
        name: len((PACKAGE / name).read_text(encoding="utf-8").splitlines())
        for name in maximum_lines
    }
    assert all(actual[name] <= limit for name, limit in maximum_lines.items())


def test_extracted_services_have_bounded_responsibilities():
    maximum_lines = {
        "application/programs.py": 500,
        "application/portfolio_decisions.py": 900,
        "application/research_catalog.py": 250,
        "platform/workflow.py": 425,
        "platform/outbox.py": 200,
        "platform/operations.py": 120,
        "platform/legacy_research.py": 200,
    }
    actual = {
        name: len((PACKAGE / name).read_text(encoding="utf-8").splitlines())
        for name in maximum_lines
    }
    assert all(actual[name] <= limit for name, limit in maximum_lines.items())


def test_release_mcp_has_no_legacy_schema_or_dispatcher_authority():
    from companion.interfaces.mcp_profiles import INVESTMENT_TOOLS

    source = (PACKAGE / "mcp_server.py").read_text(encoding="utf-8")
    assert "TOOLS =" not in source
    assert "TOOLS={" not in source
    assert "if name ==" not in source
    assert not [name for name in INVESTMENT_TOOLS if re.match(r"^v\d+_", name)]
    assert "recovery_package_create" not in INVESTMENT_TOOLS
    assert "recovery_package_create" not in (ROOT / "AGENTS.md").read_text(
        encoding="utf-8"
    )


def test_golden_workflow_catalog_is_complete_and_references_real_tests():
    catalog_path = ROOT / "tests" / "fixtures" / "architecture" / "golden-workflows.json"
    catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
    assert catalog["schema"] == "investment-companion.architecture-golden-workflows/v1"
    required_ids = {
        "confirmed-ledger-is-the-only-portfolio-truth",
        "research-output-cannot-become-a-trade",
        "decision-risk-and-human-execution-remain-distinct",
        "performance-is-measured-and-learning-is-versioned",
        "completed-work-is-not-delivered-result",
    }
    scenarios = catalog["scenarios"]
    assert {item["id"] for item in scenarios} == required_ids
    assert all(item["invariants"] and item["required_test_refs"] for item in scenarios)

    test_qualnames: dict[str, set[str]] = {}
    for path in (ROOT / "tests").glob("test_*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        qualnames: set[str] = set()
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                qualnames.add(node.name)
            if isinstance(node, ast.ClassDef):
                for child in node.body:
                    if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        qualnames.add(f"{node.name}::{child.name}")
        test_qualnames[path.relative_to(ROOT).as_posix()] = qualnames
    for scenario in scenarios:
        for reference in scenario["required_test_refs"]:
            path, *qualname = reference.split("::")
            assert path in test_qualnames
            assert "::".join(qualname) in test_qualnames[path]
