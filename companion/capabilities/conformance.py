from __future__ import annotations

import json
import os
from datetime import timedelta
from pathlib import Path
import subprocess
import sys
from typing import Any

from ..foundation import CompanionError
from .registry import (
    CONTEXT_CONFIRMATION_INVARIANT,
    CONTINUITY_INVARIANT,
    HOME_INVARIANT,
    PORTFOLIO_LEDGER_INVARIANT,
    PORTFOLIO_TRUTH_INVARIANT,
    RECONCILIATION_INVARIANT,
    RESEARCH_BOUNDARY_INVARIANT,
    RESEARCH_WORK_INVARIANT,
    TRANSACTION_CONFIRMATION_INVARIANT,
)


def _seed_research_fixture(root: Path) -> dict[str, Any]:
    """Create only isolated synthetic facts that the MCP workflow must consume."""
    from ..core import Companion
    from ..governance import GATE_CHECKLISTS
    from ..timeutil import iso, utc_now

    companion = Companion(root, gate_scope="test_fixture")
    companion.initialize()
    gate_artifact = companion.data.manifest_publish(
        kind="synthetic_conformance_gate",
        schema_version="synthetic/v1",
        manifest={"gate": "G0", "user_data": False},
    )
    gate_evidence = companion.gates.evidence_publish(
        "G0",
        checks={key: True for key in GATE_CHECKLISTS["G0"]},
        artifacts=[gate_artifact["id"]],
        unknowns=[],
        counterevidence=[],
        counterevidence_disposition={},
        code_version="test-fixture",
        scope="test_fixture",
    )
    companion.gates.assessment_record(
        gate="G0",
        status="go",
        evidence_manifest_id=gate_evidence["id"],
        code_version="test-fixture",
        assessed_by="capability-conformance",
        scope="test_fixture",
    )
    companion.jobs.feature_set(
        "v5_operating_system", True, reason="isolated research conformance"
    )
    account = companion.financial.account_create(
        "Synthetic research account", "CNY"
    )
    contexts = {}
    for context_type, content in (
        ("investor", {"objective": "synthetic research conformance"}),
        ("mandate", {"maximum_loss": "synthetic only"}),
        ("attention", {"timezone": "Asia/Shanghai"}),
    ):
        draft = companion.cognition.context_create(
            context_type, content, reason="isolated research conformance"
        )
        contexts[f"{context_type}_revision_id"] = companion.cognition.context_confirm(
            draft["id"]
        )["id"]
    program = companion.operating.program_create(
        name="Synthetic research conformance program",
        content={
            "objective": "exercise research contracts without user facts",
            "success_criteria": ["all research states remain separated"],
            "benchmark": {"name": "synthetic benchmark"},
            "risk_budget": {"mode": "no real action"},
            "universe": {"kind": "synthetic assets"},
            "horizons": {"research": "fixture"},
            "operating_cadence": {"mode": "one shot"},
            "stop_conditions": ["conformance completed"],
            "account_ids": [account["id"]],
        },
        context_refs=contexts,
        reason="isolated research conformance",
    )
    program = companion.operating.program_confirm(
        program["revisions"][0]["id"],
        user_approval_ref="synthetic:capability-conformance",
    )
    assets = [
        companion.financial.asset_upsert(
            asset_type="stock",
            name=f"Synthetic Research Asset {index}",
            currency="CNY",
            identifiers={"synthetic_id": f"RESEARCH-{index}"},
        )
        for index in range(1, 4)
    ]
    candidate_manifest = companion.data.manifest_publish(
        kind="v6_stock_research_candidates",
        schema_version="synthetic/v1",
        manifest={
            "program_id": program["id"],
            "as_of": iso(),
            "status": "ready",
            "candidates": [{"asset_id": item["id"]} for item in assets],
            "research_only": True,
            "not_a_recommendation": True,
            "synthetic": True,
        },
    )
    triage = companion.research_work.enqueue_candidate_manifest(
        candidate_manifest["id"], actor="capability-conformance"
    )["item"]
    return {
        "account_id": account["id"],
        "program_id": program["id"],
        "asset_ids": [item["id"] for item in assets],
        "candidate_manifest_id": candidate_manifest["id"],
        "triage_item_id": triage["id"],
        "cutoff": iso(),
        "next_check_at": iso(utc_now() + timedelta(days=2)),
    }


def _call_result(response: dict[str, Any]) -> Any:
    return response.get("result", {}).get("structuredContent", {}).get("result")


def _call_error(response: dict[str, Any]) -> str | None:
    result = response.get("result", {})
    if not result.get("isError"):
        return None
    return result.get("content", [{}])[0].get("text")


def probe_investment_mcp(
    root: str | Path, *, fault: str | None = None
) -> dict[str, Any]:
    """Exercise the real investment MCP profile against isolated synthetic facts."""
    if fault not in {None, "omit_home_production_health"}:
        raise CompanionError(f"unsupported conformance fault: {fault}")
    project_root = Path(__file__).resolve().parents[2]
    fixture_root = Path(root).resolve()
    research_fixture = _seed_research_fixture(fixture_root)
    environment = {
        **os.environ,
        "COMPANION_ROOT": str(fixture_root),
        "COMPANION_GATE_SCOPE": "test_fixture",
        "COMPANION_MCP_PROFILE": "investment",
    }
    for name in (
        "COMPANION_CAPABILITY_RECEIPT_DIR",
        "COMPANION_CORE_IDENTITY",
        "COMPANION_PLUGIN_IDENTITY",
        "COMPANION_PLUGIN_REQUIREMENTS",
    ):
        environment.pop(name, None)
    if fault:
        environment["COMPANION_CONFORMANCE_FAULT"] = fault
    else:
        environment.pop("COMPANION_CONFORMANCE_FAULT", None)

    proc = subprocess.Popen(
        [sys.executable, "-m", "companion.mcp_server"],
        text=True,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        cwd=project_root,
        env=environment,
    )
    if proc.stdin is None or proc.stdout is None:
        proc.kill()
        raise CompanionError("investment MCP conformance stdio is unavailable")
    request_id = 0

    def request(method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        nonlocal request_id
        request_id += 1
        payload = {"jsonrpc": "2.0", "id": request_id, "method": method}
        if params is not None:
            payload["params"] = params
        proc.stdin.write(json.dumps(payload) + "\n")
        proc.stdin.flush()
        line = proc.stdout.readline()
        if not line:
            stderr = proc.stderr.read() if proc.stderr else ""
            raise CompanionError(f"investment MCP conformance ended early: {stderr}")
        response = json.loads(line)
        if response.get("id") != request_id:
            raise CompanionError("investment MCP conformance response id mismatch")
        return response

    def call(name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        return request("tools/call", {"name": name, "arguments": arguments})

    try:
        initialized = request("initialize", {}).get("result")
        tools_response = request("tools/list", {}).get("result", {})
        tools = tools_response.get("tools", [])
        home_call = call("investment_home", {})

        research_before_decision_call = call("decision_context", {})
        research_before_portfolio_call = call(
            "portfolio_context", {"account_id": research_fixture["account_id"]}
        )
        research_context_initial_call = call(
            "research_context",
            {
                "work_item_id": research_fixture["triage_item_id"],
                "limit": 10,
            },
        )
        triage_claim_call = call(
            "investment_opportunity_update",
            {
                "operation": "work_claim",
                "item_id": research_fixture["triage_item_id"],
                "owner": "synthetic-researcher",
            },
        )
        asset_ids = research_fixture["asset_ids"]
        incomplete_triage_call = call(
            "investment_opportunity_update",
            {
                "operation": "triage_complete",
                "item_id": research_fixture["triage_item_id"],
                "owner": "synthetic-researcher",
                "dispositions": [
                    {
                        "candidate_id": asset_ids[0],
                        "outcome": "research",
                        "reason": "synthetic promoted path",
                    },
                    {
                        "candidate_id": asset_ids[1],
                        "outcome": "research",
                        "reason": "synthetic rejected path",
                    },
                ],
            },
        )
        triage_complete_call = call(
            "investment_opportunity_update",
            {
                "operation": "triage_complete",
                "item_id": research_fixture["triage_item_id"],
                "owner": "synthetic-researcher",
                "dispositions": [
                    {
                        "candidate_id": asset_ids[0],
                        "outcome": "research",
                        "reason": "synthetic promoted path",
                    },
                    {
                        "candidate_id": asset_ids[1],
                        "outcome": "research",
                        "reason": "synthetic rejected path",
                    },
                    {
                        "candidate_id": asset_ids[2],
                        "outcome": "research",
                        "reason": "synthetic monitoring path",
                    },
                ],
            },
        )
        triage_complete = _call_result(triage_complete_call)
        children = triage_complete["children"]

        invalid_evidence_call = call(
            "investment_evidence_update",
            {
                "operation": "publish_source",
                "subject": {"asset_id": asset_ids[0]},
                "source": "synthetic missing-claims source",
                "source_group": "synthetic-invalid",
                "first_known_at": research_fixture["cutoff"],
                "observed_at": research_fixture["cutoff"],
                "evidence_type": "official_disclosure",
            },
        )

        def publish_source(
            asset_id: str, source: str, group: str, evidence_type: str
        ) -> dict[str, Any]:
            return call(
                "investment_evidence_update",
                {
                    "operation": "publish_source",
                    "subject": {"asset_id": asset_id},
                    "source": source,
                    "source_group": group,
                    "first_known_at": research_fixture["cutoff"],
                    "observed_at": research_fixture["cutoff"],
                    "claims": [f"{source} synthetic claim"],
                    "evidence_type": evidence_type,
                    "metadata": {"synthetic": True},
                },
            )

        source_one_call = publish_source(
            asset_ids[0], "synthetic issuer filing", "synthetic-issuer", "official_disclosure"
        )
        source_two_call = publish_source(
            asset_ids[0], "synthetic exchange notice", "synthetic-exchange", "official_disclosure"
        )
        predictive_source_call = publish_source(
            asset_ids[2], "synthetic predictive scan", "synthetic-signal", "predictive_signal"
        )
        source_one = _call_result(source_one_call)
        source_two = _call_result(source_two_call)
        predictive_source = _call_result(predictive_source_call)
        market_snapshot_call = call(
            "investment_evidence_update",
            {
                "operation": "market_snapshot",
                "asset_id": asset_ids[0],
                "metric": "close",
                "value": "10",
                "observed_at": research_fixture["cutoff"],
                "source": "synthetic market",
                "quality": "healthy",
                "currency": "CNY",
            },
        )

        invalid_reference_call = call(
            "investment_research_publish",
            {
                "subject": {"asset_id": asset_ids[0]},
                "content": "# Synthetic invalid reference thesis",
                "evidence_manifest_ids": ["manifest_" + "0" * 64],
                "knowledge_cutoff": research_fixture["cutoff"],
            },
        )
        validation_spec = {
            "falsifiers": ["the synthetic driver reverses"],
            "counterevidence": {
                "searched": ["synthetic issuer", "synthetic exchange"],
                "findings": [],
            },
            "applicability": {
                "horizon": "synthetic month",
                "conditions": ["synthetic normal liquidity"],
                "excluded_conditions": ["synthetic suspension"],
            },
            "cost_assumptions": {
                "commission": "synthetic commission",
                "tax": "synthetic tax",
                "slippage": "synthetic slippage",
            },
            "max_evidence_age_days": 30,
        }
        promoted_research_call = call(
            "investment_research_publish",
            {
                "subject": {"asset_id": asset_ids[0]},
                "content": "# Synthetic promoted Thesis\n\nA falsifiable research-only claim.",
                "evidence_manifest_ids": [source_one["id"], source_two["id"]],
                "knowledge_cutoff": research_fixture["cutoff"],
                "validation_spec": validation_spec,
            },
        )
        promoted_research = _call_result(promoted_research_call)
        rejected_research_call = call(
            "investment_research_publish",
            {
                "subject": {"asset_id": asset_ids[1]},
                "content": "# Synthetic rejected Thesis\n\nA claim that remains unvalidated.",
                "evidence_manifest_ids": [source_one["id"]],
                "knowledge_cutoff": research_fixture["cutoff"],
            },
        )
        rejected_research = _call_result(rejected_research_call)
        promoted_opportunity_call = call(
            "investment_opportunity_update",
            {
                "operation": "create",
                "subject": {"asset_id": asset_ids[0]},
                "evidence_refs": [source_one["id"]],
                "reason": "synthetic validated research question",
                "program_id": research_fixture["program_id"],
                "thesis_id": promoted_research["thesis"]["id"],
            },
        )
        promoted_opportunity = _call_result(promoted_opportunity_call)
        opportunity_transition_call = call(
            "investment_opportunity_update",
            {
                "operation": "transition",
                "opportunity_id": promoted_opportunity["id"],
                "expected_version": promoted_opportunity["version"],
                "to_stage": "researching",
                "to_status": "active",
                "evidence_refs": [source_one["id"], source_two["id"]],
                "reason": "synthetic evidence collection started",
            },
        )
        rejected_opportunity_call = call(
            "investment_opportunity_update",
            {
                "operation": "create",
                "subject": {"asset_id": asset_ids[1]},
                "evidence_refs": [source_one["id"]],
                "reason": "synthetic unvalidated research question",
                "program_id": research_fixture["program_id"],
                "thesis_id": rejected_research["thesis"]["id"],
            },
        )
        rejected_opportunity = _call_result(rejected_opportunity_call)

        promoted_claim_call = call(
            "investment_opportunity_update",
            {
                "operation": "work_claim",
                "item_id": children[0]["id"],
                "owner": "synthetic-researcher",
            },
        )
        missing_validation_call = call(
            "investment_opportunity_update",
            {
                "operation": "research_complete",
                "item_id": children[0]["id"],
                "owner": "synthetic-researcher",
                "outcome": "promoted",
                "reason": "synthetic missing formal validation",
                "result_refs": [research_fixture["candidate_manifest_id"]],
                "opportunity_id": promoted_opportunity["id"],
            },
        )
        promoted_complete_call = call(
            "investment_opportunity_update",
            {
                "operation": "research_complete",
                "item_id": children[0]["id"],
                "owner": "synthetic-researcher",
                "outcome": "promoted",
                "reason": "synthetic eligible formal validation",
                "result_refs": [
                    research_fixture["candidate_manifest_id"],
                    promoted_research["validation"]["calculation_id"],
                ],
                "opportunity_id": promoted_opportunity["id"],
            },
        )
        rejected_claim_call = call(
            "investment_opportunity_update",
            {
                "operation": "work_claim",
                "item_id": children[1]["id"],
                "owner": "synthetic-researcher",
            },
        )
        rejected_complete_call = call(
            "investment_opportunity_update",
            {
                "operation": "research_complete",
                "item_id": children[1]["id"],
                "owner": "synthetic-researcher",
                "outcome": "rejected",
                "reason": "synthetic counterevidence failed the Thesis",
                "result_refs": [research_fixture["candidate_manifest_id"]],
            },
        )
        monitoring_claim_call = call(
            "investment_opportunity_update",
            {
                "operation": "work_claim",
                "item_id": children[2]["id"],
                "owner": "synthetic-researcher",
            },
        )
        monitoring_complete_call = call(
            "investment_opportunity_update",
            {
                "operation": "research_complete",
                "item_id": children[2]["id"],
                "owner": "synthetic-researcher",
                "outcome": "monitoring",
                "reason": "synthetic event has not occurred",
                "result_refs": [
                    research_fixture["candidate_manifest_id"],
                    predictive_source["id"],
                ],
                "next_check_at": research_fixture["next_check_at"],
            },
        )
        research_context_final_call = call("research_context", {"limit": 20})
        research_after_decision_call = call("decision_context", {})
        research_after_portfolio_call = call(
            "portfolio_context", {"account_id": research_fixture["account_id"]}
        )

        context_draft_call = call(
            "investment_context_update",
            {
                "operation": "draft",
                "context_type": "investor",
                "content": {"objective": "synthetic conformance objective"},
                "reason": "isolated conformance fixture",
            },
        )
        context_draft = _call_result(context_draft_call)
        context_missing_ref_call = call(
            "investment_context_update", {"operation": "confirm"}
        )
        context_confirm_call = call(
            "investment_context_update",
            {"operation": "confirm", "revision_id": context_draft["id"]},
        )

        account_call = call(
            "investment_transaction_update",
            {
                "operation": "account_create",
                "name": "Synthetic conformance account",
                "base_currency": "CNY",
            },
        )
        account = _call_result(account_call)
        account_id = account["id"]
        before_call = call(
            "portfolio_context",
            {"account_id": account_id, "as_of": "2025-01-02T00:00:00Z"},
        )

        pending_call = call(
            "investment_transaction_update",
            {
                "operation": "record",
                "account_id": account_id,
                "entry_type": "cash_deposit",
                "occurred_at": "2025-01-01T00:00:00Z",
                "amount": "100",
                "currency": "CNY",
                "source": "synthetic-conformance",
            },
        )
        pending = _call_result(pending_call)
        pending_portfolio_call = call(
            "portfolio_context",
            {"account_id": account_id, "as_of": "2025-01-02T00:00:00Z"},
        )
        transaction_missing_ref_call = call(
            "investment_transaction_update", {"operation": "confirm"}
        )
        confirmed_call = call(
            "investment_transaction_update",
            {"operation": "confirm", "entry_id": pending["id"]},
        )
        confirmed_portfolio_call = call(
            "portfolio_context",
            {"account_id": account_id, "as_of": "2025-01-02T00:00:00Z"},
        )
        reversal_call = call(
            "investment_transaction_update",
            {
                "operation": "reverse",
                "entry_id": pending["id"],
                "reason": "synthetic conformance reversal",
                "occurred_at": "2025-01-03T00:00:00Z",
            },
        )
        reversed_portfolio_call = call(
            "portfolio_context",
            {"account_id": account_id, "as_of": "2025-01-04T00:00:00Z"},
        )

        mismatch_statement = {
            "cash": {"CNY": "1"},
            "positions": {},
            "position_values": {},
            "position_total_by_currency": {},
            "total_by_currency": {"CNY": "1"},
        }
        mismatch_call = call(
            "investment_transaction_update",
            {
                "operation": "reconcile",
                "account_id": account_id,
                "as_of": "2025-01-04T00:00:00Z",
                "statement": mismatch_statement,
                "source_ref": "synthetic-mismatch",
            },
        )
        after_mismatch_call = call(
            "portfolio_context",
            {"account_id": account_id, "as_of": "2025-01-04T00:00:00Z"},
        )
        matched_statement = {
            "cash": {},
            "positions": {},
            "position_values": {},
            "position_total_by_currency": {},
            "total_by_currency": {},
        }
        matched_call = call(
            "investment_transaction_update",
            {
                "operation": "reconcile",
                "account_id": account_id,
                "as_of": "2025-01-05T00:00:00Z",
                "statement": matched_statement,
                "source_ref": "synthetic-match",
            },
        )
        continuity_missing_ref_call = call(
            "investment_transaction_update",
            {
                "operation": "continuity_confirm",
                "account_id": account_id,
                "confirmed_at": "2025-01-06T00:00:00Z",
                "reporting_commitment": True,
            },
        )
        continuity_call = call(
            "investment_transaction_update",
            {
                "operation": "continuity_confirm",
                "account_id": account_id,
                "confirmed_at": "2025-01-06T00:00:00Z",
                "user_confirmation_ref": "synthetic-continuity-confirmation",
                "reporting_commitment": True,
            },
        )
        continuity = _call_result(continuity_call)
        continuity_portfolio_call = call(
            "portfolio_context",
            {"account_id": account_id, "as_of": "2025-01-10T00:00:00Z"},
        )
        continuity_revoke_missing_ref_call = call(
            "investment_transaction_update",
            {"operation": "continuity_revoke", "reason": "missing reference fixture"},
        )
        continuity_revoke_call = call(
            "investment_transaction_update",
            {
                "operation": "continuity_revoke",
                "confirmation_id": continuity["id"],
                "reason": "synthetic continuity revocation",
            },
        )
        revoked_portfolio_call = call(
            "portfolio_context",
            {"account_id": account_id, "as_of": "2025-01-10T00:00:00Z"},
        )
    finally:
        proc.stdin.close()
        try:
            return_code = proc.wait(timeout=30)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=5)
            raise CompanionError("investment MCP conformance did not exit")
        if return_code != 0:
            stderr = proc.stderr.read() if proc.stderr else ""
            raise CompanionError(f"investment MCP conformance failed: {stderr}")

    tools_by_name = {item.get("name"): item for item in tools}
    return {
        "initialize": initialized,
        "tool_names": list(tools_by_name),
        "tools": tools_by_name,
        "home_result": _call_result(home_call),
        "home_call_error": _call_error(home_call),
        "research": {
            "fixture": research_fixture,
            "context_initial": _call_result(research_context_initial_call),
            "triage_claim": _call_result(triage_claim_call),
            "incomplete_triage_error": _call_error(incomplete_triage_call),
            "triage_complete": triage_complete,
            "invalid_evidence_error": _call_error(invalid_evidence_call),
            "evidence": {
                "source_one": source_one,
                "source_two": source_two,
                "predictive_source": predictive_source,
                "market_snapshot": _call_result(market_snapshot_call),
            },
            "invalid_reference_error": _call_error(invalid_reference_call),
            "promoted_research": promoted_research,
            "rejected_research": rejected_research,
            "opportunities": {
                "promoted": promoted_opportunity,
                "transitioned": _call_result(opportunity_transition_call),
                "rejected": rejected_opportunity,
            },
            "claims": {
                "promoted": _call_result(promoted_claim_call),
                "rejected": _call_result(rejected_claim_call),
                "monitoring": _call_result(monitoring_claim_call),
            },
            "missing_validation_error": _call_error(missing_validation_call),
            "outcomes": {
                "promoted": _call_result(promoted_complete_call),
                "rejected": _call_result(rejected_complete_call),
                "monitoring": _call_result(monitoring_complete_call),
            },
            "context_final": _call_result(research_context_final_call),
            "boundary_before": {
                "decision": _call_result(research_before_decision_call),
                "portfolio": _call_result(research_before_portfolio_call),
            },
            "boundary_after": {
                "decision": _call_result(research_after_decision_call),
                "portfolio": _call_result(research_after_portfolio_call),
            },
        },
        "context": {
            "draft": context_draft,
            "confirm": _call_result(context_confirm_call),
            "missing_reference_error": _call_error(context_missing_ref_call),
        },
        "ledger": {
            "before": _call_result(before_call),
            "pending_entry": pending,
            "pending_portfolio": _call_result(pending_portfolio_call),
            "missing_reference_error": _call_error(transaction_missing_ref_call),
            "confirmed_entry": _call_result(confirmed_call),
            "confirmed_portfolio": _call_result(confirmed_portfolio_call),
            "reversal_entry": _call_result(reversal_call),
            "reversed_portfolio": _call_result(reversed_portfolio_call),
        },
        "reconciliation": {
            "mismatch": _call_result(mismatch_call),
            "after_mismatch": _call_result(after_mismatch_call),
            "matched": _call_result(matched_call),
        },
        "continuity": {
            "missing_reference_error": _call_error(continuity_missing_ref_call),
            "confirmed": continuity,
            "portfolio": _call_result(continuity_portfolio_call),
            "revoke_missing_reference_error": _call_error(
                continuity_revoke_missing_ref_call
            ),
            "revoked": _call_result(continuity_revoke_call),
            "revoked_portfolio": _call_result(revoked_portfolio_call),
        },
    }


def evaluate_investment_conformance(observation: dict[str, Any]) -> dict[str, Any]:
    checks: list[dict[str, Any]] = []
    failures: list[dict[str, Any]] = []

    def check(identifier: str, passed: bool, failure: dict[str, Any]) -> None:
        checks.append({"id": identifier, "passed": bool(passed)})
        if not passed:
            failures.append(failure)

    check(
        "mcp.initialize",
        bool((observation.get("initialize") or {}).get("protocolVersion")),
        {"code": "mcp_initialize_failed"},
    )
    tools = observation.get("tools", {})
    home_schema = (tools.get("investment_home") or {}).get("inputSchema")
    check(
        "mcp.tools-list.investment-home",
        home_schema
        == {
            "type": "object",
            "properties": {},
            "required": [],
            "additionalProperties": False,
        },
        {"code": "missing_or_drifted_tool", "capability": "investment_home"},
    )
    check(
        "mcp.tools-list.portfolio-context",
        (tools.get("portfolio_context") or {}).get("inputSchema", {}).get(
            "additionalProperties"
        )
        is False,
        {"code": "missing_or_drifted_tool", "capability": "portfolio_context"},
    )

    def operations(name: str) -> set[str]:
        variants = (tools.get(name) or {}).get("inputSchema", {}).get("oneOf", [])
        return {
            variant.get("properties", {}).get("operation", {}).get("const")
            for variant in variants
        }

    check(
        "mcp.tools-list.context-update-variants",
        operations("investment_context_update") == {"draft", "confirm"},
        {
            "code": "missing_or_drifted_tool",
            "capability": "investment_context_update",
        },
    )
    check(
        "mcp.tools-list.transaction-update-variants",
        operations("investment_transaction_update")
        == {
            "account_create",
            "asset_register",
            "record",
            "confirm",
            "reverse",
            "reconcile",
            "continuity_confirm",
            "continuity_revoke",
        },
        {
            "code": "missing_or_drifted_tool",
            "capability": "investment_transaction_update",
        },
    )
    research_context_schema = (tools.get("research_context") or {}).get(
        "inputSchema", {}
    )
    check(
        "mcp.tools-list.research-context",
        research_context_schema.get("additionalProperties") is False
        and set(research_context_schema.get("properties", {}))
        == {"subject_id", "work_item_id", "limit"},
        {"code": "missing_or_drifted_tool", "capability": "research_context"},
    )
    opportunity_variants = (
        (tools.get("investment_opportunity_update") or {})
        .get("inputSchema", {})
        .get("oneOf", [])
    )
    opportunity_operations = [
        item.get("properties", {}).get("operation", {}).get("const")
        for item in opportunity_variants
    ]
    research_complete_variants = [
        item
        for item in opportunity_variants
        if item.get("properties", {}).get("operation", {}).get("const")
        == "research_complete"
    ]
    research_complete_required = {
        item.get("properties", {}).get("outcome", {}).get("const"): set(
            item.get("required", [])
        )
        for item in research_complete_variants
    }
    check(
        "mcp.tools-list.opportunity-update-variants",
        set(opportunity_operations)
        == {"create", "transition", "work_claim", "triage_complete", "research_complete"}
        and opportunity_operations.count("research_complete") == 3
        and {
            "item_id", "outcome", "reason", "result_refs", "opportunity_id"
        }
        <= research_complete_required.get("promoted", set())
        and {"item_id", "outcome", "reason"}
        <= research_complete_required.get("rejected", set())
        and {"item_id", "outcome", "reason", "next_check_at"}
        <= research_complete_required.get("monitoring", set()),
        {
            "code": "missing_or_drifted_tool",
            "capability": "investment_opportunity_update",
        },
    )
    check(
        "mcp.tools-list.evidence-update-variants",
        operations("investment_evidence_update")
        == {"publish_source", "market_snapshot"},
        {
            "code": "missing_or_drifted_tool",
            "capability": "investment_evidence_update",
        },
    )
    research_publish_schema = (
        tools.get("investment_research_publish") or {}
    ).get("inputSchema", {})
    check(
        "mcp.tools-list.research-publish",
        research_publish_schema.get("additionalProperties") is False
        and {
            "subject", "content", "evidence_manifest_ids", "knowledge_cutoff"
        }
        <= set(research_publish_schema.get("required", [])),
        {
            "code": "missing_or_drifted_tool",
            "capability": "investment_research_publish",
        },
    )

    home = observation.get("home_result")
    health = home.get("production_health") if isinstance(home, dict) else None
    required_health = {"baseline", "workflows", "optional_enhancements", "incidents"}
    check(
        HOME_INVARIANT,
        isinstance(health, dict) and required_health <= set(health),
        {
            "code": "invariant_violation",
            "capability": "investment_home",
            "invariant": HOME_INVARIANT,
            "counterexample": "tools/call succeeded without required production_health",
        },
    )

    research = observation.get("research", {})
    initial_research_context = research.get("context_initial") or {}
    triage = research.get("triage_complete") or {}
    triage_item = triage.get("item") or {}
    triage_children = triage.get("children") or []
    check(
        RESEARCH_WORK_INVARIANT,
        len(
            (initial_research_context.get("work_queue", {}).get("selected") or {}).get(
                "candidate_scope", []
            )
        )
        == 3
        and isinstance(research.get("incomplete_triage_error"), str)
        and "explicitly cover every candidate" in research["incomplete_triage_error"]
        and triage_item.get("status") == "completed"
        and len(triage_children) == 3,
        {
            "code": "invariant_violation",
            "capability": "investment_opportunity_update",
            "invariant": RESEARCH_WORK_INVARIANT,
            "counterexample": "candidate triage completed without explicit disposition of the frozen scope",
        },
    )

    research_outcomes = research.get("outcomes", {})
    promoted_outcome = research_outcomes.get("promoted") or {}
    rejected_outcome = research_outcomes.get("rejected") or {}
    monitoring_outcome = research_outcomes.get("monitoring") or {}
    check(
        "mcp.research-work.outcomes/v1",
        promoted_outcome.get("status") == "completed"
        and promoted_outcome.get("disposition", {}).get("outcome") == "promoted"
        and isinstance(promoted_outcome.get("opportunity_id"), str)
        and rejected_outcome.get("status") == "rejected"
        and rejected_outcome.get("disposition", {}).get("outcome") == "rejected"
        and monitoring_outcome.get("status") == "monitoring"
        and monitoring_outcome.get("disposition", {}).get("outcome") == "monitoring"
        and monitoring_outcome.get("next_check_at")
        == (research.get("fixture") or {}).get("next_check_at"),
        {
            "code": "research_outcome_drift",
            "capability": "investment_opportunity_update",
        },
    )
    negative_errors = [
        research.get("invalid_evidence_error"),
        research.get("invalid_reference_error"),
        research.get("missing_validation_error"),
    ]
    check(
        "mcp.research-negative-cases.rejected/v1",
        isinstance(negative_errors[0], str)
        and negative_errors[0].startswith("capability.input.invalid:")
        and isinstance(negative_errors[1], str)
        and "manifest" in negative_errors[1]
        and "not found" in negative_errors[1]
        and isinstance(negative_errors[2], str)
        and "eligible formal Thesis Validation" in negative_errors[2],
        {
            "code": "missing_negative_rejection",
            "capability": "research_workflow",
        },
    )

    promoted_research = research.get("promoted_research") or {}
    validation = promoted_research.get("validation") or {}
    thesis_metadata = (promoted_research.get("revision") or {}).get("metadata", {})
    predictive_manifest = (
        research.get("evidence", {}).get("predictive_source") or {}
    ).get("manifest", {}).get("manifest", {})
    before_decision = research.get("boundary_before", {}).get("decision") or {}
    after_decision = research.get("boundary_after", {}).get("decision") or {}
    before_portfolio = research.get("boundary_before", {}).get("portfolio") or {}
    after_portfolio = research.get("boundary_after", {}).get("portfolio") or {}

    def no_decision_or_action(value: dict[str, Any]) -> bool:
        return all(
            value.get(field) == []
            for field in ("queue", "action_cards", "recent_decisions", "executions")
        )

    check(
        RESEARCH_BOUNDARY_INVARIANT,
        validation.get("status") == "eligible_for_decision"
        and validation.get("automatic_decision_or_execution") is False
        and thesis_metadata.get("research_only") is True
        and thesis_metadata.get("automatic_decision_or_execution") is False
        and predictive_manifest.get("evidence_type") == "predictive_signal"
        and no_decision_or_action(before_decision)
        and no_decision_or_action(after_decision)
        and all(
            (before_portfolio.get("portfolio") or {}).get(field)
            == (after_portfolio.get("portfolio") or {}).get(field)
            for field in ("cash", "positions", "total_by_currency")
        )
        and before_portfolio.get("pending_transactions")
        == after_portfolio.get("pending_transactions")
        and before_portfolio.get("truth") == "confirmed_ledger_replay"
        and after_portfolio.get("truth") == "confirmed_ledger_replay",
        {
            "code": "invariant_violation",
            "capability": "research_workflow",
            "invariant": RESEARCH_BOUNDARY_INVARIANT,
            "counterexample": "Evidence, prediction, Validation or Thesis created a Decision, Action Card, Execution or Ledger fact",
        },
    )

    context = observation.get("context", {})
    ledger = observation.get("ledger", {})
    context_confirmed = context.get("confirm") or {}
    initial_portfolio = ledger.get("before") or {}
    check(
        CONTEXT_CONFIRMATION_INVARIANT,
        (context.get("draft") or {}).get("status") == "draft"
        and context_confirmed.get("status") == "current"
        and (initial_portfolio.get("investor") or {}).get("id")
        == context_confirmed.get("id"),
        {
            "code": "invariant_violation",
            "capability": "investment_context_update",
            "invariant": CONTEXT_CONFIRMATION_INVARIANT,
            "counterexample": "draft and confirmed Context states were not separated",
        },
    )

    pending_portfolio = ledger.get("pending_portfolio") or {}
    pending_state = pending_portfolio.get("portfolio", {})
    pending_entries = pending_portfolio.get("pending_transactions", [])
    check(
        PORTFOLIO_LEDGER_INVARIANT,
        initial_portfolio.get("truth") == "confirmed_ledger_replay"
        and pending_state.get("cash") == {}
        and [item.get("id") for item in pending_entries]
        == [(ledger.get("pending_entry") or {}).get("id")],
        {
            "code": "invariant_violation",
            "capability": "portfolio_context",
            "invariant": PORTFOLIO_LEDGER_INVARIANT,
            "counterexample": "pending Ledger Entry changed or appeared inside confirmed portfolio truth",
        },
    )
    confirmed_cash = (
        (ledger.get("confirmed_portfolio") or {}).get("portfolio", {}).get("cash")
    )
    reversed_cash = (
        (ledger.get("reversed_portfolio") or {}).get("portfolio", {}).get("cash")
    )
    reversal = ledger.get("reversal_entry") or {}
    check(
        TRANSACTION_CONFIRMATION_INVARIANT,
        (ledger.get("pending_entry") or {}).get("status") == "needs_confirmation"
        and (ledger.get("confirmed_entry") or {}).get("status") == "confirmed"
        and confirmed_cash == {"CNY": "100"}
        and reversal.get("status") == "confirmed"
        and reversal.get("entry_type") == "reversal"
        and reversed_cash == {"CNY": "0"},
        {
            "code": "invariant_violation",
            "capability": "investment_transaction_update",
            "invariant": TRANSACTION_CONFIRMATION_INVARIANT,
            "counterexample": "pending, confirmation or immutable reversal portfolio transition drifted",
        },
    )

    reconciliation = observation.get("reconciliation", {})
    mismatch = reconciliation.get("mismatch") or {}
    after_mismatch_cash = (
        (reconciliation.get("after_mismatch") or {}).get("portfolio", {}).get("cash")
    )
    check(
        RECONCILIATION_INVARIANT,
        mismatch.get("status") == "needs_review"
        and bool(mismatch.get("differences"))
        and mismatch.get("reconciliation", {}).get("full_scope_matched") is False
        and after_mismatch_cash == {"CNY": "0"}
        and (reconciliation.get("matched") or {}).get("status") == "matched",
        {
            "code": "invariant_violation",
            "capability": "investment_transaction_update",
            "invariant": RECONCILIATION_INVARIANT,
            "counterexample": "reconciliation difference auto-adjusted confirmed portfolio truth",
        },
    )

    continuity = observation.get("continuity", {})
    continuity_precision = (continuity.get("portfolio") or {}).get(
        "precision_boundary", {}
    )
    revoked_precision = (continuity.get("revoked_portfolio") or {}).get(
        "precision_boundary", {}
    )
    check(
        CONTINUITY_INVARIANT,
        (continuity.get("confirmed") or {}).get("status") == "active"
        and continuity_precision.get("current_broker_position_proven") is False
        and continuity_precision.get("ledger_position_continuity_supported") is True
        and continuity_precision.get("precise_position_advice_allowed") is True
        and (continuity.get("revoked") or {}).get("status") == "revoked"
        and revoked_precision.get("ledger_position_continuity_supported") is False
        and revoked_precision.get("precise_position_advice_allowed") is False,
        {
            "code": "invariant_violation",
            "capability": "investment_transaction_update",
            "invariant": CONTINUITY_INVARIANT,
            "counterexample": "Account Continuity masqueraded as broker proof or survived revocation",
        },
    )
    check(
        PORTFOLIO_TRUTH_INVARIANT,
        all(
            (value or {}).get("truth") == "confirmed_ledger_replay"
            for value in (
                ledger.get("before"),
                ledger.get("pending_portfolio"),
                ledger.get("confirmed_portfolio"),
                ledger.get("reversed_portfolio"),
                continuity.get("portfolio"),
                continuity.get("revoked_portfolio"),
            )
        ),
        {
            "code": "invariant_violation",
            "capability": "portfolio_context",
            "invariant": PORTFOLIO_TRUTH_INVARIANT,
            "counterexample": "portfolio truth or precision semantics were not explicit",
        },
    )

    reference_errors = [
        context.get("missing_reference_error"),
        ledger.get("missing_reference_error"),
        continuity.get("missing_reference_error"),
        continuity.get("revoke_missing_reference_error"),
    ]
    check(
        "mcp.required-confirmation-references.rejected/v1",
        all(
            isinstance(error, str) and error.startswith("capability.input.invalid:")
            for error in reference_errors
        ),
        {
            "code": "missing_negative_rejection",
            "capability": "personal_context_and_portfolio_ledger",
        },
    )
    return {
        "passed": not failures,
        "profile": "investment",
        "checks": checks,
        "failures": failures,
    }
