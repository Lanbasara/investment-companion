from __future__ import annotations

import json
import os
from datetime import timedelta
from pathlib import Path
import subprocess
import sys
import time
from typing import Any

from ..foundation import CompanionError
from ..timeutil import iso, parse, utc_now
from .registry import (
    ACTION_ACCEPTANCE_INVARIANT,
    BRIEF_NO_ACTION_INVARIANT,
    BRIEF_PROJECTION_INVARIANT,
    CONTEXT_CONFIRMATION_INVARIANT,
    CONTINUITY_INVARIANT,
    DECISION_RESEARCH_SEPARATION_INVARIANT,
    HOME_INVARIANT,
    PORTFOLIO_LEDGER_INVARIANT,
    PORTFOLIO_TRUTH_INVARIANT,
    PROGRAM_CONFIRMATION_INVARIANT,
    PROGRAM_PROJECTION_INVARIANT,
    RECONCILIATION_INVARIANT,
    RESEARCH_BOUNDARY_INVARIANT,
    RESEARCH_WORK_INVARIANT,
    RISK_GATE_BOUNDARY_INVARIANT,
    TRANSACTION_CONFIRMATION_INVARIANT,
    DELIVERY_STATE_INVARIANT,
    EXECUTION_CONFIRMATION_INVARIANT,
    EXECUTION_RECONCILIATION_INVARIANT,
    PERFORMANCE_CALCULATION_INVARIANT,
    REVIEW_IMMUTABILITY_INVARIANT,
    CHANGE_PROPOSAL_INVARIANT,
    WORKFLOW_RUN_DELIVERY_INVARIANT,
    WORKFLOW_VERSION_INVARIANT,
    WORKFLOW_WAKE_LEASE_INVARIANT,
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
    opening = companion.financial.ledger_add(
        account_id=account["id"],
        entry_type="opening_balance",
        occurred_at=iso(),
        amount="100000",
        currency="CNY",
        source="synthetic-capability-conformance",
    )
    companion.financial.ledger_confirm(opening["id"])
    contexts = {}
    for context_type, content in (
        ("investor", {"objective": "synthetic research conformance"}),
        (
            "mandate",
            {
                "max_single_position_weight": "0.20",
            },
        ),
        ("attention", {"timezone": "Asia/Shanghai"}),
    ):
        draft = companion.cognition.context_create(
            context_type, content, reason="isolated research conformance"
        )
        contexts[f"{context_type}_revision_id"] = companion.cognition.context_confirm(
            draft["id"]
        )["id"]
    program_content = {
        "objective": "exercise research contracts without user facts",
        "success_criteria": ["all research states remain separated"],
        "benchmark": {"name": "synthetic benchmark"},
        "risk_budget": {
            "mode": "no real action",
            "bounded_action": {
                "enabled": True,
                "allowed_asset_types": ["stock"],
                "allowed_execution_plan_types": ["priced_buy"],
                "max_trade_weight": "0.02",
                "max_post_trade_weight": "0.05",
                "max_validity_sessions": 20,
                "max_active_bounded_actions": 2,
            },
        },
        "universe": {"kind": "synthetic assets"},
        "horizons": {"research": "fixture"},
        "operating_cadence": {"mode": "one shot"},
        "stop_conditions": ["conformance completed"],
        "account_ids": [account["id"]],
    }
    program = companion.operating.program_create(
        name="Synthetic research conformance program",
        content=program_content,
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
    cutoff = iso()
    companion.financial.reconcile(
        account["id"],
        cutoff,
        {
            "cash": {"CNY": "100000"},
            "positions": {},
            "position_values": {},
            "position_total_by_currency": {},
            "total_by_currency": {"CNY": "100000"},
        },
        "synthetic-capability-conformance",
    )
    return {
        "account_id": account["id"],
        "program_id": program["id"],
        "asset_ids": [item["id"] for item in assets],
        "candidate_manifest_id": candidate_manifest["id"],
        "triage_item_id": triage["id"],
        "cutoff": cutoff,
        "valid_until": iso(utc_now() + timedelta(days=7)),
        "next_check_at": iso(utc_now() + timedelta(days=2)),
        "context_refs": contexts,
        "program_content": program_content,
    }


def _seed_active_grid_report_fixture(
    root: Path,
    *,
    program_id: str,
    queue_id: str,
    decision_revision_id: str,
    account_id: str,
    valid_until: str,
) -> dict[str, Any]:
    """Seed the broker-side precondition for MCP-only sleep/dividend reports.

    Strategy creation is exercised separately through MCP.  This fixture starts
    at the user-reported active-broker state so the black-box probe can cover
    moving-grid-only transitions without touching a broker or user data.
    """
    from ..core import Companion
    from ..execution_strategy import CICC_SEMANTICS_VERSION
    from ..foundation import canonical, digest, new_id

    companion = Companion(root, gate_scope="test_fixture")
    companion.initialize()
    asset = companion.financial.asset_upsert(
        asset_type="etf",
        name="Synthetic Grid Report ETF",
        currency="CNY",
        identifiers={"synthetic_id": "GRID-REPORT-ETF"},
    )
    spec = companion.execution_strategy._normalize_spec(
        "moving_grid",
        {
            "account_id": account_id,
            "asset_id": asset["id"],
            "validity_sessions": 5,
            "monitoring_window": None,
            "initial_reference_price": "10",
            "spacing_type": "difference",
            "rise_sell_spacing": "1",
            "fall_buy_spacing": "1",
            "sell_order": {
                "price_type": "limit",
                "price_instruction": "instant",
                "custom_price": None,
            },
            "buy_order": {
                "price_type": "limit",
                "price_instruction": "instant",
                "custom_price": None,
            },
            "sell_quantity": "100",
            "buy_quantity": "100",
            "price_range": {
                "lower": "8",
                "upper": "12",
                "out_of_range_behavior": "sleep",
            },
            "position_range": {"max_net_buy": "100", "max_net_sell": "100"},
            "multiple_grid_order": True,
        },
    )
    plan_id = new_id("brokerplan")
    now = iso()
    idempotency_key = "synthetic-grid-report-fixture"
    with companion.db.transaction() as con:
        con.execute(
            "INSERT INTO broker_execution_plans("
            "id,program_id,queue_id,decision_revision_id,broker,plan_type,"
            "account_id,asset_id,spec_json,semantics_version,status,"
            "broker_condition_ref,valid_until,content_hash,idempotency_key,"
            "configured_at,current_reference_price_text,created_at,updated_at"
            ") VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                plan_id,
                program_id,
                queue_id,
                decision_revision_id,
                "cicc_wealth",
                "moving_grid",
                account_id,
                asset["id"],
                canonical(spec),
                CICC_SEMANTICS_VERSION,
                "active",
                "synthetic-grid-condition",
                valid_until,
                digest("synthetic-grid-report-fixture", spec, valid_until),
                idempotency_key,
                now,
                "10",
                now,
                now,
            ),
        )
    return companion.execution_strategy.get(plan_id)


def _call_result(response: dict[str, Any]) -> Any:
    return response.get("result", {}).get("structuredContent", {}).get("result")


def _call_error(response: dict[str, Any]) -> str | None:
    result = response.get("result", {})
    if not result.get("isError"):
        return None
    return result.get("content", [{}])[0].get("text")


def _without_fields(value: dict[str, Any], *fields: str) -> dict[str, Any]:
    return {key: item for key, item in value.items() if key not in fields}


def _stable_portfolio_projection(
    value: dict[str, Any], *, include_program_policy: bool
) -> dict[str, Any]:
    projection = _without_fields(value, "as_of")
    freshness = projection.get("truth_freshness")
    if isinstance(freshness, dict):
        projection["truth_freshness"] = _without_fields(
            freshness, "verified_at", "age_seconds"
        )
    if not include_program_policy:
        projection.pop("policy", None)
    return projection


def _stable_research_projection(
    value: dict[str, Any], *, include_active_program_queue: bool
) -> dict[str, Any]:
    projection = _without_fields(value, "as_of")
    if not include_active_program_queue:
        queue = projection.get("work_queue") or {}
        projection["work_queue"] = {"selected": queue.get("selected")}
    return projection


def _stable_evaluation_projection(value: dict[str, Any]) -> dict[str, Any]:
    return _without_fields(value, "as_of")


def _stable_program_projection(value: dict[str, Any]) -> dict[str, Any]:
    return _without_fields(value, "as_of")


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
        "COMPANION_CAPABILITY_RECEIPT_IDENTITY",
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
        initial_program_context_call = call("investment_program_context", {})

        workflow_schedule_call = call(
            "investment_workflow_update",
            {
                "operation": "schedule_create",
                "name": "Synthetic required-result workflow",
                "kind": "review",
                "mission": "Exercise isolated Schedule, Run, Wake and Delivery state.",
                "cadence": {"type": "interval", "seconds": 3600},
                "policy": {"delivery_mode": "report_required"},
            },
        )
        workflow_schedule = _call_result(workflow_schedule_call)
        patched_schedule_call = call(
            "investment_workflow_update",
            {
                "operation": "schedule_patch",
                "schedule_id": workflow_schedule["id"],
                "expected_version": workflow_schedule["version"],
                "changes": {
                    "mission": "Exercise current-version workflow mutations."
                },
                "reason": "synthetic conformance patch",
            },
        )
        patched_schedule = _call_result(patched_schedule_call)
        stale_patch_call = call(
            "investment_workflow_update",
            {
                "operation": "schedule_patch",
                "schedule_id": workflow_schedule["id"],
                "expected_version": workflow_schedule["version"],
                "changes": {"name": "silently overwritten"},
            },
        )
        paused_schedule_call = call(
            "investment_workflow_update",
            {
                "operation": "schedule_status",
                "schedule_id": patched_schedule["id"],
                "expected_version": patched_schedule["version"],
                "status": "paused",
                "reason": "exercise versioned pause",
            },
        )
        paused_schedule = _call_result(paused_schedule_call)
        stale_schedule_status_call = call(
            "investment_workflow_update",
            {
                "operation": "schedule_status",
                "schedule_id": paused_schedule["id"],
                "expected_version": patched_schedule["version"],
                "status": "archived",
            },
        )
        resumed_schedule_call = call(
            "investment_workflow_update",
            {
                "operation": "schedule_status",
                "schedule_id": paused_schedule["id"],
                "expected_version": paused_schedule["version"],
                "status": "active",
                "reason": "exercise versioned resume",
            },
        )
        resumed_schedule = _call_result(resumed_schedule_call)
        required_run_call = call(
            "investment_workflow_update",
            {
                "operation": "schedule_run_now",
                "schedule_id": resumed_schedule["id"],
            },
        )
        required_run = _call_result(required_run_call)
        required_delivery = _call_result(
            call(
                "investment_workflow_context",
                {"view": "deliveries", "mode": "report_required", "limit": 10},
            )
        )[0]
        required_wake_call = call(
            "investment_workflow_update",
            {"operation": "wake_claim", "owner": "required-worker", "lease_seconds": 300},
        )
        required_wake = _call_result(required_wake_call)
        duplicate_wake_call = call(
            "investment_workflow_update",
            {"operation": "wake_claim", "owner": "duplicate-worker", "lease_seconds": 300},
        )
        required_prepare_call = call(
            "investment_delivery_update",
            {
                "operation": "prepare",
                "delivery_id": required_delivery["id"],
                "conclusion": "no_action",
                "summary": "The synthetic required workflow completed without an action.",
                "key_evidence": ["Isolated Schedule and Run state was inspected."],
                "next_step": "Keep the synthetic fixture isolated.",
                "next_check_at": iso(utc_now() + timedelta(hours=1)),
                "source_refs": ["synthetic:workflow-required"],
            },
        )
        required_prepared = _call_result(required_prepare_call)
        required_complete_call = call(
            "investment_workflow_update",
            {"operation": "run_complete", "run_id": required_run["id"], "success": True},
        )
        required_wake_complete_call = call(
            "investment_workflow_update",
            {
                "operation": "wake_complete",
                "outbox_id": required_wake["outbox_id"],
                "owner": "required-worker",
                "success": True,
            },
        )
        required_delivery_after_run_call = call(
            "investment_workflow_context",
            {"view": "delivery", "delivery_id": required_delivery["id"]},
        )
        immutable_delivery_call = call(
            "investment_delivery_update",
            {
                "operation": "prepare",
                "delivery_id": required_delivery["id"],
                "conclusion": "action",
                "summary": "Attempt to replace the frozen result.",
                "key_evidence": [],
                "next_step": "This write must be rejected.",
                "source_refs": [],
            },
        )

        recoverable_run = _call_result(
            call(
                "investment_workflow_update",
                {"operation": "schedule_run_now", "schedule_id": resumed_schedule["id"]},
            )
        )
        recoverable_wake = _call_result(
            call(
                "investment_workflow_update",
                {"operation": "wake_claim", "owner": "recovery-worker", "lease_seconds": 300},
            )
        )
        wrong_wake_owner_call = call(
            "investment_workflow_update",
            {
                "operation": "wake_complete",
                "outbox_id": recoverable_wake["outbox_id"],
                "owner": "wrong-worker",
                "success": False,
                "error": "must not overwrite another owner's lease",
            },
        )
        failed_wake_complete_call = call(
            "investment_workflow_update",
            {
                "operation": "wake_complete",
                "outbox_id": recoverable_wake["outbox_id"],
                "owner": "recovery-worker",
                "success": False,
                "error": "synthetic recoverable failure",
            },
        )
        recoverable_run_call = call(
            "investment_workflow_context",
            {"view": "run", "run_id": recoverable_run["id"]},
        )

        digest_schedule = _call_result(
            call(
                "investment_workflow_update",
                {
                    "operation": "schedule_create",
                    "name": "Synthetic digest workflow",
                    "kind": "review",
                    "mission": "Exercise digest Delivery state.",
                    "cadence": {"type": "interval", "seconds": 3600},
                    "policy": {"delivery_mode": "digest_required"},
                },
            )
        )
        digest_run = _call_result(
            call(
                "investment_workflow_update",
                {"operation": "schedule_run_now", "schedule_id": digest_schedule["id"]},
            )
        )
        digest_wake = _call_result(
            call(
                "investment_workflow_update",
                {"operation": "wake_claim", "owner": "digest-worker", "lease_seconds": 300},
            )
        )
        digest_delivery = _call_result(
            call(
                "investment_workflow_context",
                {"view": "deliveries", "mode": "digest_required", "limit": 10},
            )
        )[0]
        digest_prepare_call = call(
            "investment_delivery_update",
            {
                "operation": "prepare",
                "delivery_id": digest_delivery["id"],
                "conclusion": "no_action",
                "summary": "The digest component is ready.",
                "key_evidence": ["The isolated digest Run completed."],
                "next_step": "Include this component in one digest.",
                "source_refs": ["synthetic:workflow-digest"],
            },
        )
        digest_complete_call = call(
            "investment_workflow_update",
            {"operation": "run_complete", "run_id": digest_run["id"], "success": True},
        )
        digest_wake_complete_call = call(
            "investment_workflow_update",
            {
                "operation": "wake_complete",
                "outbox_id": digest_wake["outbox_id"],
                "owner": "digest-worker",
                "success": True,
            },
        )
        digest_send_call = call(
            "investment_delivery_update",
            {
                "operation": "digest_send",
                "delivery_ids": [digest_delivery["id"]],
                "conclusion": "no_action",
                "summary": "One synthetic digest component is ready for transport.",
                "key_evidence": ["The component result is frozen."],
                "next_step": "Wait for the transport receipt.",
                "source_refs": ["synthetic:workflow-digest"],
            },
        )
        digest_attention = _call_result(digest_prepare_call)["attention_decision_id"]
        invalid_digest_delivery_call = call(
            "investment_delivery_update",
            {
                "operation": "attention_delivered",
                "attention_decision_id": digest_attention,
            },
        )

        failed_run = _call_result(
            call(
                "investment_workflow_update",
                {"operation": "schedule_run_now", "schedule_id": resumed_schedule["id"]},
            )
        )
        failed_run_call = call(
            "investment_workflow_update",
            {
                "operation": "run_complete",
                "run_id": failed_run["id"],
                "success": False,
                "error": "synthetic terminal failure",
            },
        )
        cancelled_run = _call_result(
            call(
                "investment_workflow_update",
                {"operation": "schedule_run_now", "schedule_id": resumed_schedule["id"]},
            )
        )
        cancelled_run_call = call(
            "investment_workflow_update",
            {
                "operation": "run_cancel",
                "run_id": cancelled_run["id"],
                "reason": "synthetic cancellation",
            },
        )
        workflow_schedule_context_call = call(
            "investment_workflow_context",
            {"view": "schedule", "schedule_id": resumed_schedule["id"]},
        )
        workflow_history_call = call(
            "investment_workflow_context",
            {"view": "schedule_history", "schedule_id": resumed_schedule["id"], "limit": 10},
        )
        workflow_runs_call = call(
            "investment_workflow_context", {"view": "runs", "limit": 20}
        )
        workflow_delivery_status_call = call(
            "investment_workflow_context", {"view": "delivery_status"}
        )
        workflow_system_status_call = call(
            "investment_workflow_context", {"view": "system_status"}
        )
        workflow_doctor_call = call(
            "investment_workflow_context", {"view": "doctor"}
        )

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
        monitoring_release_at = iso(utc_now() + timedelta(seconds=2))
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
                "next_check_at": monitoring_release_at,
            },
        )
        research_context_final_call = call("research_context", {"limit": 20})
        research_after_decision_call = call("decision_context", {})
        research_after_portfolio_call = call(
            "portfolio_context", {"account_id": research_fixture["account_id"]}
        )

        qualified_opportunity_call = call(
            "investment_opportunity_update",
            {
                "operation": "transition",
                "opportunity_id": promoted_opportunity["id"],
                "expected_version": _call_result(opportunity_transition_call)["version"],
                "to_stage": "qualified",
                "to_status": "active",
                "evidence_refs": [
                    source_one["id"],
                    source_two["id"],
                    promoted_research["validation"]["calculation_id"],
                ],
                "qualification": {
                    "validation_calculation_id": promoted_research["validation"][
                        "calculation_id"
                    ],
                    "major_unknowns": ["Decision and Risk Gate are not frozen"],
                    "decision_basis": "Research is eligible, but is not yet a Decision.",
                },
                "reason": "synthetic research passed qualification only",
            },
        )
        qualified_opportunity = _call_result(qualified_opportunity_call)
        reality_spec = {
            "version": "a-share-reality/v1",
            "currency": "CNY",
            "lot_size": 100,
            "t_plus_one": True,
            "signal_delay": "next_session",
            "commission_rate": "0.0003",
            "minimum_commission": "5",
            "sell_stamp_duty_rate": "0.0005",
            "cash_dividend_tax_rate": "0",
            "slippage_bps": "0",
            "money_quantum": "0.01",
            "price_tick": "0.01",
        }
        action_plan_base = {
            "as_of": research_fixture["cutoff"],
            "account_id": research_fixture["account_id"],
            "asset_id": asset_ids[0],
            "quantity": "100",
            "price": "10",
            "reality_spec": reality_spec,
            "market_snapshot_id": _call_result(market_snapshot_call)["id"],
            "max_market_age_seconds": 3600,
            "valid_until": research_fixture["valid_until"],
            "price_range": {"min": "9.8", "max": "10.2"},
        }
        standard_plan_call = call(
            "investment_action_plan",
            {"action_tier": "standard", **action_plan_base},
        )
        bounded_plan_call = call(
            "investment_action_plan",
            {
                "action_tier": "bounded",
                **action_plan_base,
                "validity_sessions": 5,
            },
        )
        bounded_missing_sessions_call = call(
            "investment_action_plan",
            {"action_tier": "bounded", **action_plan_base},
        )
        blocked_plan_call = call(
            "investment_action_plan",
            {
                "action_tier": "standard",
                **action_plan_base,
                "quantity": "100000",
            },
        )
        standard_plan = _call_result(standard_plan_call)
        bounded_plan = _call_result(bounded_plan_call)
        blocked_plan = _call_result(blocked_plan_call)
        decision_base = {
            "content": "# Synthetic Decision\n\nA time-bounded manual action with alternatives.",
            "decision_kind": "action",
            "account_id": research_fixture["account_id"],
            "as_of": research_fixture["cutoff"],
            "knowledge_cutoff": research_fixture["cutoff"],
            "valid_until": research_fixture["valid_until"],
            "invalidators": ["price leaves range", "Mandate changes"],
            "no_action": {"choice": "hold cash", "cost": "opportunity cost"},
            "alternatives": [
                {"choice": "hold cash"},
                {"choice": "buy fewer shares"},
            ],
        }
        unqualified_decision_call = call(
            "investment_decision_publish",
            {
                **decision_base,
                "subject": {"asset_id": asset_ids[1]},
                "thesis_revision_ids": [rejected_research["revision"]["id"]],
                "evidence_manifest_ids": [source_one["id"]],
                "risk_calculation_id": standard_plan["risk"]["calculation_id"],
                "research_validation_calculation_id": promoted_research["validation"][
                    "calculation_id"
                ],
            },
        )
        expired_decision_call = call(
            "investment_decision_publish",
            {
                **decision_base,
                "subject": {"asset_id": asset_ids[0]},
                "valid_until": "2020-01-01T00:00:00Z",
                "thesis_revision_ids": [promoted_research["revision"]["id"]],
                "evidence_manifest_ids": [source_one["id"], source_two["id"]],
                "risk_calculation_id": standard_plan["risk"]["calculation_id"],
                "research_validation_calculation_id": promoted_research["validation"][
                    "calculation_id"
                ],
            },
        )
        blocked_decision_call = call(
            "investment_decision_publish",
            {
                **decision_base,
                "subject": {"asset_id": asset_ids[0]},
                "thesis_revision_ids": [promoted_research["revision"]["id"]],
                "evidence_manifest_ids": [source_one["id"], source_two["id"]],
                "risk_calculation_id": blocked_plan["risk"]["calculation_id"],
                "research_validation_calculation_id": promoted_research["validation"][
                    "calculation_id"
                ],
            },
        )
        bounded_decision_call = call(
            "investment_decision_publish",
            {
                **decision_base,
                "subject": {"asset_id": asset_ids[0]},
                "decision_kind": "conditional_action",
                "thesis_revision_ids": [promoted_research["revision"]["id"]],
                "evidence_manifest_ids": [source_one["id"], source_two["id"]],
                "risk_calculation_id": bounded_plan["risk"]["calculation_id"],
                "research_validation_calculation_id": promoted_research["validation"][
                    "calculation_id"
                ],
                },
            )
        broker_strategy_spec = {
            "account_id": research_fixture["account_id"],
            "asset_id": asset_ids[0],
            "validity_sessions": 5,
            "monitoring_window": None,
            "trigger": {"direction": "cross_down", "monitor_price": "10"},
            "order": {
                "price_type": "limit",
                "price_instruction": "custom",
                "custom_price": "10",
            },
            "quantity": "100",
        }
        decision_publish_call = call(
            "investment_decision_publish",
            {
                **decision_base,
                "subject": {"asset_id": asset_ids[0]},
                "thesis_revision_ids": [promoted_research["revision"]["id"]],
                "evidence_manifest_ids": [source_one["id"], source_two["id"]],
                "risk_calculation_id": standard_plan["risk"]["calculation_id"],
                "research_validation_calculation_id": promoted_research["validation"][
                    "calculation_id"
                ],
                "execution_plan": {
                    "plan_type": "priced_buy",
                    "spec": broker_strategy_spec,
                },
            },
        )
        decision_publish = _call_result(decision_publish_call)
        actionable_opportunity_call = call(
            "investment_opportunity_update",
            {
                "operation": "transition",
                "opportunity_id": promoted_opportunity["id"],
                "expected_version": qualified_opportunity["version"],
                "to_stage": "actionable",
                "to_status": "active",
                "evidence_refs": [
                    source_one["id"],
                    source_two["id"],
                    promoted_research["validation"]["calculation_id"],
                    standard_plan["risk"]["calculation_id"],
                    decision_publish["revision"]["id"],
                ],
                "qualification": {
                    "validation_calculation_id": promoted_research["validation"][
                        "calculation_id"
                    ],
                    "major_unknowns": [],
                    "decision_basis": "The formal Decision froze the remaining action inputs.",
                },
                "decision_revision_id": decision_publish["revision"]["id"],
                "reason": "synthetic Decision and Risk Gate completed action qualification",
            },
        )
        actionable_opportunity = _call_result(actionable_opportunity_call)
        portfolio_before_action_call = call(
            "portfolio_context", {"account_id": research_fixture["account_id"]}
        )
        enqueue_call = call(
            "investment_action_update",
            {
                "operation": "enqueue",
                "opportunity_id": actionable_opportunity["id"],
                "decision_revision_id": decision_publish["revision"]["id"],
            },
        )
        queue = _call_result(enqueue_call)
        snoozed_call = call(
            "investment_action_update",
            {
                "operation": "respond",
                "queue_id": queue["id"],
                "state": "snoozed",
                "reason": "synthetic user requested a later review",
                "snoozed_until": iso(utc_now() + timedelta(hours=1)),
                "user_confirmation_ref": "synthetic:user-message:snooze",
            },
        )
        attention_call = call(
            "investment_delivery_update",
            {
                "operation": "attention_decide",
                "topic": "synthetic Action Card",
                "materiality": "high",
                "confidence": "decision_grade",
                "reason": "present the isolated conformance Action Card",
                "evidence": [queue["id"]],
            },
        )
        attention = _call_result(attention_call)
        attention_delivered_call = call(
            "investment_delivery_update",
            {
                "operation": "attention_delivered",
                "attention_decision_id": attention["id"],
            },
        )
        attention_feedback_call = call(
            "investment_delivery_update",
            {
                "operation": "attention_feedback",
                "attention_decision_id": attention["id"],
                "feedback": "useful",
                "note": "synthetic conformance feedback",
            },
        )
        presented_call = call(
            "investment_action_update",
            {
                "operation": "respond",
                "queue_id": queue["id"],
                "state": "presented",
                "attention_decision_id": attention["id"],
            },
        )
        missing_confirmation_call = call(
            "investment_action_update",
            {
                "operation": "respond",
                "queue_id": queue["id"],
                "state": "accepted",
            },
        )
        accepted_call = call(
            "investment_action_update",
            {
                "operation": "respond",
                "queue_id": queue["id"],
                "state": "accepted",
                "user_confirmation_ref": "synthetic:user-message:accept",
            },
        )
        decision_after_accept_call = call("decision_context", {})
        portfolio_after_accept_call = call(
            "portfolio_context", {"account_id": research_fixture["account_id"]}
        )
        closed_call = call(
            "investment_action_update",
            {
                "operation": "respond",
                "queue_id": queue["id"],
                "state": "closed",
                "reason": "synthetic response path completed",
            },
        )
        second_enqueue_call = call(
            "investment_action_update",
            {
                "operation": "enqueue",
                "opportunity_id": actionable_opportunity["id"],
                "decision_revision_id": decision_publish["revision"]["id"],
                "idempotency_key": "synthetic-rejection-path",
            },
        )
        rejected_action_call = call(
            "investment_action_update",
            {
                "operation": "respond",
                "queue_id": _call_result(second_enqueue_call)["id"],
                "state": "rejected",
                "reason": "synthetic user rejected this alternative",
                "user_confirmation_ref": "synthetic:user-message:reject",
            },
        )
        manual_enqueue_call = call(
            "investment_action_update",
            {
                "operation": "enqueue",
                "opportunity_id": actionable_opportunity["id"],
                "decision_revision_id": decision_publish["revision"]["id"],
                "idempotency_key": "synthetic-manual-execution-path",
            },
        )
        manual_queue = _call_result(manual_enqueue_call)
        manual_attention_call = call(
            "investment_delivery_update",
            {
                "operation": "attention_decide",
                "topic": "synthetic manual and broker Execution",
                "materiality": "high",
                "confidence": "decision_grade",
                "reason": "present the isolated Execution Action Card",
                "evidence": [manual_queue["id"]],
            },
        )
        manual_attention = _call_result(manual_attention_call)
        call(
            "investment_delivery_update",
            {
                "operation": "attention_delivered",
                "attention_decision_id": manual_attention["id"],
            },
        )
        call(
            "investment_action_update",
            {
                "operation": "respond",
                "queue_id": manual_queue["id"],
                "state": "presented",
                "attention_decision_id": manual_attention["id"],
            },
        )
        manual_accepted_call = call(
            "investment_action_update",
            {
                "operation": "respond",
                "queue_id": manual_queue["id"],
                "state": "accepted",
                "user_confirmation_ref": "synthetic:user-message:accept-execution",
            },
        )
        manual_before_call = call(
            "portfolio_context", {"account_id": research_fixture["account_id"]}
        )
        execution_prepare_call = call(
            "investment_execution_update",
            {
                "operation": "prepare",
                "queue_id": manual_queue["id"],
                "idempotency_key": "synthetic-manual-execution",
            },
        )
        prepared_execution = _call_result(execution_prepare_call)
        duplicate_prepare_call = call(
            "investment_execution_update",
            {
                "operation": "prepare",
                "queue_id": manual_queue["id"],
                "idempotency_key": "synthetic-manual-execution",
            },
        )
        manual_after_prepare_call = call(
            "portfolio_context", {"account_id": research_fixture["account_id"]}
        )
        strategy_create_call = call(
            "investment_execution_update",
            {
                "operation": "strategy_create",
                "queue_id": manual_queue["id"],
                "plan_type": "priced_buy",
                "spec": broker_strategy_spec,
                "valid_until": research_fixture["valid_until"],
                "idempotency_key": "synthetic-priced-buy-strategy",
            },
        )
        broker_strategy = _call_result(strategy_create_call)
        duplicate_strategy_create_call = call(
            "investment_execution_update",
            {
                "operation": "strategy_create",
                "queue_id": manual_queue["id"],
                "plan_type": "priced_buy",
                "spec": broker_strategy_spec,
                "valid_until": research_fixture["valid_until"],
                "idempotency_key": "synthetic-priced-buy-strategy",
            },
        )
        invalid_validity_call = call(
            "investment_execution_update",
            {
                "operation": "strategy_configured",
                "plan_id": broker_strategy["id"],
                "broker_condition_ref": "synthetic-condition",
                "configured_at": iso(),
                "broker_validity_sessions": 10,
                "broker_valid_until": research_fixture["valid_until"],
            },
        )
        strategy_configured_call = call(
            "investment_execution_update",
            {
                "operation": "strategy_configured",
                "plan_id": broker_strategy["id"],
                "broker_condition_ref": "synthetic-condition",
                "configured_at": iso(),
                "broker_validity_sessions": 5,
                "broker_valid_until": research_fixture["valid_until"],
            },
        )
        strategy_activate_call = call(
            "investment_execution_update",
            {
                "operation": "strategy_activate",
                "plan_id": broker_strategy["id"],
                "occurred_at": iso(),
            },
        )
        priced_strategy_sleep_call = call(
            "investment_execution_update",
            {
                "operation": "strategy_sleep",
                "plan_id": broker_strategy["id"],
                "direction": "buy",
                "sleeping": True,
                "occurred_at": iso(),
                "reason": "synthetic wrong-plan-type report",
            },
        )
        priced_strategy_dividend_call = call(
            "investment_execution_update",
            {
                "operation": "strategy_etf_dividend",
                "plan_id": broker_strategy["id"],
                "occurred_at": iso(),
                "corporate_action_ref": "synthetic:wrong-plan-dividend",
            },
        )
        grid_report_fixture = _seed_active_grid_report_fixture(
            fixture_root,
            program_id=research_fixture["program_id"],
            queue_id=manual_queue["id"],
            decision_revision_id=decision_publish["revision"]["id"],
            account_id=research_fixture["account_id"],
            valid_until=research_fixture["valid_until"],
        )
        grid_portfolio_before_call = call(
            "portfolio_context", {"account_id": research_fixture["account_id"]}
        )
        grid_sleep_call = call(
            "investment_execution_update",
            {
                "operation": "strategy_sleep",
                "plan_id": grid_report_fixture["id"],
                "direction": "buy",
                "sleeping": True,
                "occurred_at": iso(),
                "reason": "synthetic broker reported the buy direction sleeping",
            },
        )
        grid_sleep_view_call = call(
            "investment_workflow_context",
            {"view": "execution_strategy", "plan_id": grid_report_fixture["id"]},
        )
        grid_dividend_call = call(
            "investment_execution_update",
            {
                "operation": "strategy_etf_dividend",
                "plan_id": grid_report_fixture["id"],
                "occurred_at": iso(),
                "corporate_action_ref": "synthetic:etf-dividend-termination",
            },
        )
        grid_portfolio_after_call = call(
            "portfolio_context", {"account_id": research_fixture["account_id"]}
        )
        strategy_list_call = call(
            "investment_workflow_context",
            {"view": "execution_strategies", "status": "active", "limit": 10},
        )
        strategy_active_view_call = call(
            "investment_workflow_context",
            {"view": "execution_strategy", "plan_id": broker_strategy["id"]},
        )
        strategy_portfolio_before_order_call = call(
            "portfolio_context", {"account_id": research_fixture["account_id"]}
        )
        strategy_triggered_at = iso()
        strategy_order_call = call(
            "investment_execution_update",
            {
                "operation": "strategy_order_report",
                "plan_id": broker_strategy["id"],
                "broker_order_ref": "synthetic-strategy-order",
                "side": "buy",
                "quantity": "100",
                "status": "submitted",
                "triggered_at": strategy_triggered_at,
                "trigger_price": "10",
            },
        )
        strategy_order = _call_result(strategy_order_call)
        duplicate_strategy_order_call = call(
            "investment_execution_update",
            {
                "operation": "strategy_order_report",
                "plan_id": broker_strategy["id"],
                "broker_order_ref": "synthetic-strategy-order",
                "side": "buy",
                "quantity": "100",
                "status": "submitted",
                "triggered_at": strategy_triggered_at,
                "trigger_price": "10",
            },
        )
        strategy_portfolio_after_order_call = call(
            "portfolio_context", {"account_id": research_fixture["account_id"]}
        )
        strategy_termination_request_call = call(
            "investment_execution_update",
            {
                "operation": "strategy_terminate_request",
                "plan_id": broker_strategy["id"],
                "occurred_at": iso(),
                "reason": "synthetic user requested termination",
            },
        )
        strategy_terminated_call = call(
            "investment_execution_update",
            {
                "operation": "strategy_terminated",
                "plan_id": broker_strategy["id"],
                "occurred_at": iso(),
                "reason": "synthetic broker reported termination",
            },
        )
        live_order_reconcile_call = call(
            "investment_execution_update",
            {
                "operation": "strategy_reconcile",
                "plan_id": broker_strategy["id"],
                "occurred_at": iso(),
                "reconciliation_id": "synthetic-before-live-order-closed",
            },
        )
        strategy_cancelled_order_call = call(
            "investment_execution_update",
            {
                "operation": "strategy_order_report",
                "plan_id": broker_strategy["id"],
                "broker_order_ref": "synthetic-strategy-order",
                "side": "buy",
                "quantity": "100",
                "status": "cancelled",
                "triggered_at": strategy_triggered_at,
                "cancelled_quantity": "100",
            },
        )
        strategy_portfolio_after_cancel_call = call(
            "portfolio_context", {"account_id": research_fixture["account_id"]}
        )
        strategy_reconciliation_as_of = iso()
        strategy_reconciliation_call = call(
            "investment_transaction_update",
            {
                "operation": "reconcile",
                "account_id": research_fixture["account_id"],
                "as_of": strategy_reconciliation_as_of,
                "statement": {
                    "cash": {"CNY": "100000"},
                    "positions": {},
                    "position_values": {},
                    "position_total_by_currency": {},
                    "total_by_currency": {"CNY": "100000"},
                },
                "source_ref": "synthetic:strategy-final-statement",
            },
        )
        strategy_reconciliation = _call_result(strategy_reconciliation_call)
        strategy_reconciled_call = call(
            "investment_execution_update",
            {
                "operation": "strategy_reconcile",
                "plan_id": broker_strategy["id"],
                "occurred_at": strategy_reconciliation_as_of,
                "reconciliation_id": strategy_reconciliation["id"],
            },
        )
        strategy_reconciled_view_call = call(
            "investment_workflow_context",
            {"view": "execution_strategy", "plan_id": broker_strategy["id"]},
        )
        execution_order_call = call(
            "investment_execution_update",
            {
                "operation": "order",
                "execution_id": prepared_execution["id"],
                "broker_order_ref": "synthetic-broker-order",
                "ordered_at": iso(),
            },
        )
        manual_after_order_call = call(
            "portfolio_context", {"account_id": research_fixture["account_id"]}
        )
        incomplete_fill_call = call(
            "investment_execution_update",
            {
                "operation": "report_fill",
                "execution_id": prepared_execution["id"],
                "occurred_at": iso(),
                "quantity": "40",
                "price": "10",
                "fee": "1",
            },
        )
        partial_fill_call = call(
            "investment_execution_update",
            {
                "operation": "report_fill",
                "execution_id": prepared_execution["id"],
                "occurred_at": iso(),
                "quantity": "40",
                "price": "10",
                "fee": "1",
                "source": "synthetic broker report",
                "external_id": "synthetic-partial-fill",
                "final": False,
            },
        )
        partial_fill = _call_result(partial_fill_call)
        duplicate_partial_fill_call = call(
            "investment_execution_update",
            {
                "operation": "report_fill",
                "execution_id": prepared_execution["id"],
                "occurred_at": partial_fill["pending_ledger_entry"]["occurred_at"],
                "quantity": "40",
                "price": "10",
                "fee": "1",
                "source": "synthetic broker report",
                "external_id": "synthetic-partial-fill",
                "final": False,
            },
        )
        manual_after_report_call = call(
            "portfolio_context", {"account_id": research_fixture["account_id"]}
        )
        partial_confirm_call = call(
            "investment_execution_update",
            {
                "operation": "confirm_fill",
                "execution_id": prepared_execution["id"],
                "entry_id": partial_fill["pending_ledger_entry"]["id"],
                "final": False,
            },
        )
        manual_after_partial_confirm_call = call(
            "portfolio_context", {"account_id": research_fixture["account_id"]}
        )
        deviated_fill_call = call(
            "investment_execution_update",
            {
                "operation": "report_fill",
                "execution_id": prepared_execution["id"],
                "occurred_at": iso(),
                "quantity": "60",
                "price": "11",
                "fee": "1",
                "source": "synthetic broker report",
                "external_id": "synthetic-deviated-fill",
                "final": True,
            },
        )
        deviated_fill = _call_result(deviated_fill_call)
        final_confirm_call = call(
            "investment_execution_update",
            {
                "operation": "confirm_fill",
                "execution_id": prepared_execution["id"],
                "entry_id": deviated_fill["pending_ledger_entry"]["id"],
                "final": True,
            },
        )
        manual_after_final_confirm_call = call(
            "portfolio_context", {"account_id": research_fixture["account_id"]}
        )
        no_action_brief_call = call(
            "investment_brief_update",
            {
                "operation": "publish",
                "brief_type": "daily",
                "period_key": "synthetic-no-action-negative",
                "as_of": iso(),
                "conclusion": "no_action",
                "payload": {
                    "summary": "synthetic no-action claim with unfinished work",
                    "what_changed": [],
                    "decision": "no action",
                    "risks": [],
                    "next_check_at": research_fixture["next_check_at"],
                    "queue_item_ids": [],
                },
                "source_refs": [research_fixture["candidate_manifest_id"]],
                "program_id": research_fixture["program_id"],
            },
        )
        while utc_now() < parse(monitoring_release_at):
            time.sleep(0.05)
        from ..core import Companion

        recovery = Companion(fixture_root, gate_scope="test_fixture")
        recovery.initialize()
        research_recovery = recovery.recover()
        resolved_monitoring_claim_call = call(
            "investment_opportunity_update",
            {
                "operation": "work_claim",
                "item_id": children[2]["id"],
                "owner": "synthetic-researcher",
            },
        )
        resolved_monitoring_call = call(
            "investment_opportunity_update",
            {
                "operation": "research_complete",
                "item_id": children[2]["id"],
                "owner": "synthetic-researcher",
                "outcome": "rejected",
                "reason": "synthetic monitoring obligation was resolved",
                "result_refs": [
                    research_fixture["candidate_manifest_id"],
                    predictive_source["id"],
                ],
            },
        )
        brief_projection_before_portfolio_call = call(
            "portfolio_context",
            {
                "account_id": research_fixture["account_id"],
                "as_of": research_fixture["cutoff"],
            },
        )
        brief_projection_before_research_call = call(
            "research_context",
            {"work_item_id": children[2]["id"], "limit": 20},
        )
        brief_projection_before_evaluation_call = call(
            "evaluation_context", {"limit": 20}
        )
        brief_projection_before_program_call = call(
            "investment_program_context",
            {"program_id": research_fixture["program_id"]},
        )
        metrics_period_start = iso(utc_now() - timedelta(days=1))
        metrics_period_end = iso()
        metrics_call = call(
            "investment_brief_update",
            {
                "operation": "metrics_calculate",
                "period_start": metrics_period_start,
                "period_end": metrics_period_end,
                "program_id": research_fixture["program_id"],
            },
        )
        metrics = _call_result(metrics_call)
        missing_lineage_scorecard_call = call(
            "investment_brief_update",
            {
                "operation": "scorecard_publish",
                "period_start": metrics_period_start,
                "period_end": metrics_period_end,
                "metrics": [
                    {
                        "name": "untraceable",
                        "calculation_id": "calc_missing_lineage",
                        "output_path": "outputs.value",
                    }
                ],
                "comparisons": [],
                "source_refs": [],
                "caveats": ["synthetic missing Calculation lineage"],
                "program_id": research_fixture["program_id"],
            },
        )
        scorecard_call = call(
            "investment_brief_update",
            {
                "operation": "scorecard_publish",
                "period_start": metrics_period_start,
                "period_end": metrics_period_end,
                "metrics": [
                    {
                        "name": "opportunities_created",
                        "calculation_id": metrics["calculation_id"],
                        "output_path": "outputs.cohorts.opportunities_created",
                    }
                ],
                "comparisons": [],
                "source_refs": [metrics["calculation_id"]],
                "caveats": ["synthetic one-day operating cohort"],
                "program_id": research_fixture["program_id"],
            },
        )
        scorecard = _call_result(scorecard_call)
        positive_no_action_brief_call = call(
            "investment_brief_update",
            {
                "operation": "publish",
                "brief_type": "daily",
                "period_key": "synthetic-no-action-positive",
                "as_of": iso(),
                "conclusion": "no_action",
                "payload": {
                    "summary": "synthetic obligations are resolved",
                    "what_changed": ["the monitoring obligation was resolved"],
                    "decision": "no action",
                    "risks": [],
                    "next_check_at": research_fixture["next_check_at"],
                    "queue_item_ids": [],
                },
                "source_refs": [
                    research_fixture["candidate_manifest_id"],
                    metrics["calculation_id"],
                ],
                "program_id": research_fixture["program_id"],
            },
        )
        review_brief_call = call(
            "investment_brief_update",
            {
                "operation": "publish",
                "brief_type": "daily",
                "period_key": "synthetic-review-required",
                "as_of": iso(),
                "conclusion": "review_required",
                "payload": {
                    "summary": "synthetic review path after obligations resolved",
                    "what_changed": ["the monitoring obligation was resolved"],
                    "decision": "review the completed operating cycle",
                    "risks": [],
                    "next_check_at": research_fixture["next_check_at"],
                    "queue_item_ids": [],
                },
                "source_refs": [
                    research_fixture["candidate_manifest_id"],
                    metrics["calculation_id"],
                ],
                "program_id": research_fixture["program_id"],
            },
        )
        brief_projection_after_portfolio_call = call(
            "portfolio_context",
            {
                "account_id": research_fixture["account_id"],
                "as_of": research_fixture["cutoff"],
            },
        )
        brief_projection_after_research_call = call(
            "research_context",
            {"work_item_id": children[2]["id"], "limit": 20},
        )
        brief_projection_after_evaluation_call = call(
            "evaluation_context", {"limit": 20}
        )
        brief_projection_after_program_call = call(
            "investment_program_context",
            {"program_id": research_fixture["program_id"]},
        )

        replacement_program_call = call(
            "investment_program_update",
            {
                "operation": "create",
                "name": "Synthetic replacement conformance program",
                "content": research_fixture["program_content"],
                "context_refs": research_fixture["context_refs"],
                "reason": "exercise Program lifecycle through MCP",
            },
        )
        replacement_program = _call_result(replacement_program_call)
        program_context_after_draft_call = call(
            "investment_program_context", {"program_id": replacement_program["id"]}
        )
        unconfirmed_program_brief_call = call(
            "investment_brief_update",
            {
                "operation": "metrics_calculate",
                "period_start": metrics_period_start,
                "period_end": metrics_period_end,
                "program_id": replacement_program["id"],
            },
        )
        revised_content = {
            **research_fixture["program_content"],
            "objective": "exercise revised Program contracts without user facts",
        }
        revised_program_call = call(
            "investment_program_update",
            {
                "operation": "revise",
                "program_id": replacement_program["id"],
                "expected_version": replacement_program["version"],
                "content": revised_content,
                "context_refs": research_fixture["context_refs"],
                "reason": "exercise immutable Program revision",
            },
        )
        revised_program = _call_result(revised_program_call)
        stale_revision_call = call(
            "investment_program_update",
            {
                "operation": "revise",
                "program_id": replacement_program["id"],
                "expected_version": replacement_program["version"],
                "content": revised_content,
                "context_refs": research_fixture["context_refs"],
                "reason": "synthetic stale revision",
            },
        )
        revised_revision_id = revised_program["revisions"][0]["id"]
        missing_program_approval_call = call(
            "investment_program_update",
            {"operation": "confirm", "revision_id": revised_revision_id},
        )
        confirmed_program_call = call(
            "investment_program_update",
            {
                "operation": "confirm",
                "revision_id": revised_revision_id,
                "user_approval_ref": "synthetic:user-message:approve-program",
                "supersedes_program_id": research_fixture["program_id"],
            },
        )
        confirmed_program = _call_result(confirmed_program_call)
        program_context_after_confirm_call = call(
            "investment_program_context", {"program_id": confirmed_program["id"]}
        )
        paused_program_call = call(
            "investment_program_update",
            {
                "operation": "status",
                "program_id": confirmed_program["id"],
                "expected_version": confirmed_program["version"],
                "status": "paused",
                "reason": "exercise Program pause",
            },
        )
        paused_program = _call_result(paused_program_call)
        stale_status_call = call(
            "investment_program_update",
            {
                "operation": "status",
                "program_id": confirmed_program["id"],
                "expected_version": confirmed_program["version"],
                "status": "active",
                "reason": "synthetic stale resume",
            },
        )
        resumed_program_call = call(
            "investment_program_update",
            {
                "operation": "status",
                "program_id": paused_program["id"],
                "expected_version": paused_program["version"],
                "status": "active",
                "reason": "exercise Program resume",
            },
        )
        resumed_program = _call_result(resumed_program_call)
        archived_program_call = call(
            "investment_program_update",
            {
                "operation": "status",
                "program_id": resumed_program["id"],
                "expected_version": resumed_program["version"],
                "status": "archived",
                "reason": "exercise Program archive",
            },
        )
        archived_program = _call_result(archived_program_call)
        final_program_context_call = call(
            "investment_program_context", {"program_id": archived_program["id"]}
        )
        program_projection_after_portfolio_call = call(
            "portfolio_context",
            {
                "account_id": research_fixture["account_id"],
                "as_of": research_fixture["cutoff"],
            },
        )
        program_projection_after_research_call = call(
            "research_context",
            {"work_item_id": children[2]["id"], "limit": 20},
        )
        program_projection_after_evaluation_call = call(
            "evaluation_context", {"limit": 20}
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

        performance_account = _call_result(
            call(
                "investment_transaction_update",
                {
                    "operation": "account_create",
                    "name": "Synthetic performance account",
                    "base_currency": "CNY",
                },
            )
        )
        performance_asset = _call_result(
            call(
                "investment_transaction_update",
                {
                    "operation": "asset_register",
                    "asset_type": "stock",
                    "name": "Synthetic performance asset",
                    "currency": "CNY",
                    "identifiers": {"synthetic_id": "PERFORMANCE-1"},
                },
            )
        )

        def record_and_confirm(entry: dict[str, Any]) -> dict[str, Any]:
            pending_entry = _call_result(
                call(
                    "investment_transaction_update",
                    {"operation": "record", **entry},
                )
            )
            return _call_result(
                call(
                    "investment_transaction_update",
                    {"operation": "confirm", "entry_id": pending_entry["id"]},
                )
            )

        performance_opening = record_and_confirm(
            {
                "account_id": performance_account["id"],
                "entry_type": "opening_balance",
                "occurred_at": "2024-12-31T00:00:00Z",
                "amount": "1000",
                "currency": "CNY",
                "source": "synthetic-performance-ledger",
            }
        )
        performance_trade = record_and_confirm(
            {
                "account_id": performance_account["id"],
                "entry_type": "trade",
                "asset_id": performance_asset["id"],
                "occurred_at": "2025-01-02T00:00:00Z",
                "quantity": "10",
                "price": "10",
                "amount": "-100",
                "fee": "5",
                "currency": "CNY",
                "source": "synthetic-performance-ledger",
            }
        )
        performance_deposit = record_and_confirm(
            {
                "account_id": performance_account["id"],
                "entry_type": "cash_deposit",
                "occurred_at": "2025-01-05T00:00:00Z",
                "amount": "500",
                "currency": "CNY",
                "source": "synthetic-performance-ledger",
            }
        )
        performance_fee = record_and_confirm(
            {
                "account_id": performance_account["id"],
                "entry_type": "fee",
                "occurred_at": "2025-01-07T00:00:00Z",
                "amount": "-10",
                "currency": "CNY",
                "source": "synthetic-performance-ledger",
            }
        )
        performance_arguments = {
            "benchmark_mode": "compare",
            "account_id": performance_account["id"],
            "period_start": "2025-01-01T00:00:00Z",
            "period_end": "2025-01-11T00:00:00Z",
            "period_basis": "start_exclusive_end_inclusive",
            "start_prices": {},
            "end_prices": {performance_asset["id"]: "12"},
            "valuation_points": [
                {"at": "2025-01-06T00:00:00Z", "value": "1400"}
            ],
            "price_source_refs": ["synthetic:performance-prices"],
            "trade_reference_prices": {
                performance_trade["id"]: {
                    "price": "9.5",
                    "source_ref": "synthetic:pre-trade-quote",
                }
            },
            "attribution_refs": [decision_publish["revision"]["id"]],
            "benchmark_start_value": "100",
            "benchmark_end_value": "105",
            "benchmark_source_ref": "synthetic:benchmark-series",
        }
        performance_call = call(
            "investment_performance_calculate", performance_arguments
        )
        performance = _call_result(performance_call)
        missing_price_call = call(
            "investment_performance_calculate",
            {**performance_arguments, "end_prices": {}},
        )
        missing_benchmark_arguments = dict(performance_arguments)
        missing_benchmark_arguments.pop("benchmark_end_value")
        missing_benchmark_call = call(
            "investment_performance_calculate", missing_benchmark_arguments
        )
        unavailable_benchmark_call = call(
            "investment_performance_calculate",
            {
                **{
                    key: value
                    for key, value in performance_arguments.items()
                    if not key.startswith("benchmark_")
                },
                "benchmark_mode": "unavailable",
                "benchmark_unavailable_reason": (
                    "the synthetic benchmark series is intentionally absent"
                ),
            },
        )

        evaluation_research_before_call = call(
            "research_context", {"subject_id": asset_ids[0], "limit": 20}
        )
        evaluation_program_before_call = call(
            "investment_program_context", {"program_id": archived_program["id"]}
        )
        review_subject = {
            "account_id": performance_account["id"],
            "period_start": performance["period"]["start"],
            "period_end": performance["period"]["end"],
        }
        review_arguments = {
            "operation": "create",
            "subject": review_subject,
            "content": (
                "The deterministic period result requires separately validated changes."
            ),
            "conclusion": "revise",
            "calculation_ids": [performance["calculation_id"]],
            "source_refs": ["synthetic:performance-review"],
            "historical_refs": [decision_publish["revision"]["id"]],
            "proposed_changes": [
                {
                    "target_type": "thesis",
                    "target_id": promoted_research["thesis"]["id"],
                    "change": "test a narrower evidence scope",
                    "reason": "the period sample is deliberately synthetic",
                    "validation_required": True,
                },
                {
                    "target_type": "policy",
                    "target_id": archived_program["id"],
                    "change": "consider a lower risk budget",
                    "reason": "the proposal must remain inert",
                    "validation_required": True,
                },
                {
                    "target_type": "strategy",
                    "target_id": "synthetic-strategy-version",
                    "change": "test stricter entry conditions",
                    "reason": "slippage must be validated separately",
                    "validation_required": True,
                },
            ],
            "knowledge_cutoff": "2025-01-11T00:00:00Z",
        }
        missing_calculation_review_call = call(
            "investment_review_publish",
            {**review_arguments, "calculation_ids": []},
        )
        created_review_call = call("investment_review_publish", review_arguments)
        created_review = _call_result(created_review_call)
        superseded_review_call = call(
            "investment_review_publish",
            {
                "operation": "supersede",
                "review_id": created_review["review"]["id"],
                "supersedes_revision_id": created_review["revision"]["id"],
                "supersession_reason": "A later frozen interpretation is available.",
                "subject": review_subject,
                "content": "Continue without applying the earlier proposal.",
                "conclusion": "continue",
                "calculation_ids": [performance["calculation_id"]],
                "source_refs": ["synthetic:performance-review-v2"],
                "historical_refs": [
                    created_review["revision"]["id"],
                    decision_publish["revision"]["id"],
                ],
                "knowledge_cutoff": "2025-01-12T00:00:00Z",
            },
        )
        superseded_review = _call_result(superseded_review_call)
        stale_supersession_call = call(
            "investment_review_publish",
            {
                "operation": "supersede",
                "review_id": created_review["review"]["id"],
                "supersedes_revision_id": created_review["revision"]["id"],
                "supersession_reason": "Attempt to silently replace current history.",
                "subject": review_subject,
                "content": "This stale update must be rejected.",
                "conclusion": "continue",
                "calculation_ids": [performance["calculation_id"]],
                "source_refs": ["synthetic:performance-review-v3"],
                "historical_refs": [created_review["revision"]["id"]],
                "knowledge_cutoff": "2025-01-13T00:00:00Z",
            },
        )
        evaluation_context_call = call("evaluation_context", {"limit": 20})
        evaluation_research_after_call = call(
            "research_context", {"subject_id": asset_ids[0], "limit": 20}
        )
        evaluation_program_after_call = call(
            "investment_program_context", {"program_id": archived_program["id"]}
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
        "workflow_delivery": {
            "schedule": {
                "created": workflow_schedule,
                "patched": patched_schedule,
                "stale_patch_error": _call_error(stale_patch_call),
                "paused": paused_schedule,
                "stale_status_error": _call_error(stale_schedule_status_call),
                "resumed": resumed_schedule,
                "context": _call_result(workflow_schedule_context_call),
                "history": _call_result(workflow_history_call),
            },
            "required": {
                "run": required_run,
                "wake": required_wake,
                "duplicate_wake": _call_result(duplicate_wake_call),
                "prepared": required_prepared,
                "completed": _call_result(required_complete_call),
                "wake_completed": _call_result(required_wake_complete_call),
                "delivery_after_run": _call_result(required_delivery_after_run_call),
                "immutable_error": _call_error(immutable_delivery_call),
            },
            "recoverable": {
                "run": recoverable_run,
                "wake": recoverable_wake,
                "wrong_owner_error": _call_error(wrong_wake_owner_call),
                "wake_failed": _call_result(failed_wake_complete_call),
                "run_after_failure": _call_result(recoverable_run_call),
            },
            "digest": {
                "schedule": digest_schedule,
                "run": digest_run,
                "wake": digest_wake,
                "prepared": _call_result(digest_prepare_call),
                "completed": _call_result(digest_complete_call),
                "wake_completed": _call_result(digest_wake_complete_call),
                "sent": _call_result(digest_send_call),
                "false_delivery_error": _call_error(invalid_digest_delivery_call),
            },
            "terminal_runs": {
                "failed": _call_result(failed_run_call),
                "cancelled": _call_result(cancelled_run_call),
            },
            "views": {
                "runs": _call_result(workflow_runs_call),
                "delivery_status": _call_result(workflow_delivery_status_call),
                "system_status": _call_result(workflow_system_status_call),
                "doctor": _call_result(workflow_doctor_call),
            },
        },
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
            "monitoring_release_at": monitoring_release_at,
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
        "decision_action": {
            "qualified_opportunity": qualified_opportunity,
            "plans": {
                "standard": standard_plan,
                "bounded": bounded_plan,
                "blocked": blocked_plan,
                "bounded_missing_sessions_error": _call_error(
                    bounded_missing_sessions_call
                ),
            },
            "decision": decision_publish,
            "bounded_decision": _call_result(bounded_decision_call),
            "blocked_decision_error": _call_error(blocked_decision_call),
            "unqualified_decision_error": _call_error(unqualified_decision_call),
            "expired_decision_error": _call_error(expired_decision_call),
            "actionable_opportunity": actionable_opportunity,
            "queue": {
                "enqueued": queue,
                "snoozed": _call_result(snoozed_call),
                "attention": attention,
                "attention_delivered": _call_result(attention_delivered_call),
                "attention_feedback": _call_result(attention_feedback_call),
                "presented": _call_result(presented_call),
                "missing_confirmation_error": _call_error(missing_confirmation_call),
                "accepted": _call_result(accepted_call),
                "closed": _call_result(closed_call),
                "rejected": _call_result(rejected_action_call),
            },
            "before_accept": {
                "portfolio": _call_result(portfolio_before_action_call),
            },
            "after_accept": {
                "decision": _call_result(decision_after_accept_call),
                "portfolio": _call_result(portfolio_after_accept_call),
            },
            "execution": {
                "queue": _call_result(manual_accepted_call),
                "prepared": prepared_execution,
                "duplicate_prepare": _call_result(duplicate_prepare_call),
                "ordered": _call_result(execution_order_call),
                "incomplete_fill_error": _call_error(incomplete_fill_call),
                "partial_report": partial_fill,
                "duplicate_partial_report": _call_result(
                    duplicate_partial_fill_call
                ),
                "partial_confirm": _call_result(partial_confirm_call),
                "deviated_report": deviated_fill,
                "final_confirm": _call_result(final_confirm_call),
                "portfolio": {
                    "before": _call_result(manual_before_call),
                    "after_prepare": _call_result(manual_after_prepare_call),
                    "after_order": _call_result(manual_after_order_call),
                    "after_report": _call_result(manual_after_report_call),
                    "after_partial_confirm": _call_result(
                        manual_after_partial_confirm_call
                    ),
                    "after_final_confirm": _call_result(
                        manual_after_final_confirm_call
                    ),
                },
                "strategy": {
                    "queue": _call_result(manual_accepted_call),
                    "created": broker_strategy,
                    "duplicate_create": _call_result(
                        duplicate_strategy_create_call
                    ),
                    "invalid_validity_error": _call_error(invalid_validity_call),
                    "configured": _call_result(strategy_configured_call),
                    "activated": _call_result(strategy_activate_call),
                    "grid_only_reports": {
                        "priced_sleep_error": _call_error(
                            priced_strategy_sleep_call
                        ),
                        "priced_dividend_error": _call_error(
                            priced_strategy_dividend_call
                        ),
                        "seeded": grid_report_fixture,
                        "sleeping": _call_result(grid_sleep_call),
                        "sleeping_view": _call_result(grid_sleep_view_call),
                        "dividend_terminated": _call_result(
                            grid_dividend_call
                        ),
                        "portfolio": {
                            "before": _call_result(grid_portfolio_before_call),
                            "after": _call_result(grid_portfolio_after_call),
                        },
                    },
                    "list": _call_result(strategy_list_call),
                    "active_view": _call_result(strategy_active_view_call),
                    "order": strategy_order,
                    "duplicate_order": _call_result(
                        duplicate_strategy_order_call
                    ),
                    "termination_requested": _call_result(
                        strategy_termination_request_call
                    ),
                    "terminated": _call_result(strategy_terminated_call),
                    "live_order_reconcile_error": _call_error(
                        live_order_reconcile_call
                    ),
                    "cancelled_order": _call_result(
                        strategy_cancelled_order_call
                    ),
                    "full_scope_reconciliation": strategy_reconciliation,
                    "reconciled": _call_result(strategy_reconciled_call),
                    "reconciled_view": _call_result(
                        strategy_reconciled_view_call
                    ),
                    "portfolio": {
                        "before_order": _call_result(
                            strategy_portfolio_before_order_call
                        ),
                        "after_order": _call_result(
                            strategy_portfolio_after_order_call
                        ),
                        "after_cancel": _call_result(
                            strategy_portfolio_after_cancel_call
                        ),
                    },
                },
            },
        },
        "program_brief": {
            "initial_context": _call_result(initial_program_context_call),
            "brief_projection_before": {
                "portfolio": _call_result(brief_projection_before_portfolio_call),
                "research": _call_result(brief_projection_before_research_call),
                "evaluation": _call_result(
                    brief_projection_before_evaluation_call
                ),
                "program": _call_result(brief_projection_before_program_call),
            },
            "brief": {
                "no_action_error": _call_error(no_action_brief_call),
                "research_recovery": research_recovery,
                "resolved_monitoring_claim": _call_result(
                    resolved_monitoring_claim_call
                ),
                "resolved_monitoring": _call_result(resolved_monitoring_call),
                "metrics": metrics,
                "missing_lineage_error": _call_error(
                    missing_lineage_scorecard_call
                ),
                "scorecard": scorecard,
                "no_action": _call_result(positive_no_action_brief_call),
                "review_required": _call_result(review_brief_call),
            },
            "brief_projection_after": {
                "portfolio": _call_result(brief_projection_after_portfolio_call),
                "research": _call_result(brief_projection_after_research_call),
                "evaluation": _call_result(
                    brief_projection_after_evaluation_call
                ),
                "program": _call_result(brief_projection_after_program_call),
            },
            "program": {
                "created": replacement_program,
                "context_after_draft": _call_result(
                    program_context_after_draft_call
                ),
                "unconfirmed_brief_error": _call_error(
                    unconfirmed_program_brief_call
                ),
                "revised": revised_program,
                "stale_revision_error": _call_error(stale_revision_call),
                "missing_approval_error": _call_error(
                    missing_program_approval_call
                ),
                "confirmed": confirmed_program,
                "context_after_confirm": _call_result(
                    program_context_after_confirm_call
                ),
                "paused": paused_program,
                "stale_status_error": _call_error(stale_status_call),
                "resumed": resumed_program,
                "archived": archived_program,
                "final_context": _call_result(final_program_context_call),
            },
            "program_projection_after": {
                "portfolio": _call_result(program_projection_after_portfolio_call),
                "research": _call_result(program_projection_after_research_call),
                "evaluation": _call_result(
                    program_projection_after_evaluation_call
                ),
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
        "evaluation": {
            "ledger": {
                "opening": performance_opening,
                "trade": performance_trade,
                "deposit": performance_deposit,
                "fee": performance_fee,
            },
            "performance": performance,
            "unavailable_benchmark": _call_result(unavailable_benchmark_call),
            "missing_price_error": _call_error(missing_price_call),
            "missing_benchmark_error": _call_error(missing_benchmark_call),
            "missing_calculation_error": _call_error(
                missing_calculation_review_call
            ),
            "created_review": created_review,
            "superseded_review": superseded_review,
            "stale_supersession_error": _call_error(stale_supersession_call),
            "context": _call_result(evaluation_context_call),
            "research_before": _call_result(evaluation_research_before_call),
            "research_after": _call_result(evaluation_research_after_call),
            "program_before": _call_result(evaluation_program_before_call),
            "program_after": _call_result(evaluation_program_after_call),
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
    evaluation_schema = (tools.get("evaluation_context") or {}).get(
        "inputSchema", {}
    )
    check(
        "mcp.tools-list.evaluation-context",
        evaluation_schema.get("additionalProperties") is False
        and set(evaluation_schema.get("properties", {})) == {"limit"},
        {"code": "missing_or_drifted_tool", "capability": "evaluation_context"},
    )
    performance_variants = (
        (tools.get("investment_performance_calculate") or {})
        .get("inputSchema", {})
        .get("oneOf", [])
    )
    check(
        "mcp.tools-list.performance-calculate-variants",
        {
            variant.get("properties", {}).get("benchmark_mode", {}).get("const")
            for variant in performance_variants
        }
        == {"compare", "unavailable"}
        and all(
            "period_basis" in variant.get("required", [])
            and "price_source_refs" in variant.get("required", [])
            and "attribution_refs" in variant.get("required", [])
            and not {
                "modified_dietz_return",
                "transaction_cost",
                "slippage_cost",
                "maximum_drawdown",
            }
            & set(variant.get("properties", {}))
            for variant in performance_variants
        ),
        {
            "code": "missing_or_drifted_tool",
            "capability": "investment_performance_calculate",
        },
    )
    check(
        "mcp.tools-list.review-publish-variants",
        operations("investment_review_publish") == {"create", "supersede"}
        and all(
            "knowledge_cutoff" in variant.get("required", [])
            and "calculation_ids" in variant.get("required", [])
            and "historical_refs" in variant.get("required", [])
            for variant in (
                (tools.get("investment_review_publish") or {})
                .get("inputSchema", {})
                .get("oneOf", [])
            )
        ),
        {
            "code": "missing_or_drifted_tool",
            "capability": "investment_review_publish",
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
    decision_context_schema = (tools.get("decision_context") or {}).get(
        "inputSchema", {}
    )
    check(
        "mcp.tools-list.decision-context",
        decision_context_schema.get("additionalProperties") is False
        and set(decision_context_schema.get("properties", {})) == {"limit"},
        {"code": "missing_or_drifted_tool", "capability": "decision_context"},
    )
    decision_variants = (
        (tools.get("investment_decision_publish") or {})
        .get("inputSchema", {})
        .get("oneOf", [])
    )
    decision_required = {
        item.get("properties", {}).get("decision_kind", {}).get("const"): set(
            item.get("required", [])
        )
        for item in decision_variants
    }
    check(
        "mcp.tools-list.decision-publish-variants",
        set(decision_required) == {"action", "conditional_action", "no_action", "watch"}
        and {"risk_calculation_id", "research_validation_calculation_id"}
        <= decision_required.get("action", set())
        and {"risk_calculation_id", "research_validation_calculation_id"}
        <= decision_required.get("conditional_action", set())
        and "risk_calculation_id" not in decision_required.get("no_action", set()),
        {
            "code": "missing_or_drifted_tool",
            "capability": "investment_decision_publish",
        },
    )
    action_plan_variants = (
        (tools.get("investment_action_plan") or {})
        .get("inputSchema", {})
        .get("oneOf", [])
    )
    action_plan_required = {
        item.get("properties", {}).get("action_tier", {}).get("const"): set(
            item.get("required", [])
        )
        for item in action_plan_variants
    }
    action_plan_properties = {
        key
        for item in action_plan_variants
        for key in item.get("properties", {})
    }
    check(
        "mcp.tools-list.action-plan-variants",
        set(action_plan_required) == {"standard", "bounded"}
        and "validity_sessions" in action_plan_required.get("bounded", set())
        and "validity_sessions" not in action_plan_required.get("standard", set())
        and not {
            "thesis_revision_id", "research_validation_calculation_id",
            "opinion", "confidence",
        }
        & action_plan_properties,
        {
            "code": "missing_or_drifted_tool",
            "capability": "investment_action_plan",
        },
    )
    action_update_variants = (
        (tools.get("investment_action_update") or {})
        .get("inputSchema", {})
        .get("oneOf", [])
    )
    action_update_paths = {
        (
            item.get("properties", {}).get("operation", {}).get("const"),
            item.get("properties", {}).get("state", {}).get("const"),
        ): set(item.get("required", []))
        for item in action_update_variants
    }
    check(
        "mcp.tools-list.action-update-variants",
        set(action_update_paths)
        == {
            ("enqueue", None),
            ("respond", "presented"),
            ("respond", "accepted"),
            ("respond", "rejected"),
            ("respond", "snoozed"),
            ("respond", "closed"),
        }
        and "attention_decision_id"
        in action_update_paths.get(("respond", "presented"), set())
        and all(
            "user_confirmation_ref"
            in action_update_paths.get(("respond", state), set())
            for state in ("accepted", "rejected", "snoozed")
        ),
        {
            "code": "missing_or_drifted_tool",
            "capability": "investment_action_update",
        },
    )
    program_context_schema = (
        tools.get("investment_program_context") or {}
    ).get("inputSchema", {})
    check(
        "mcp.tools-list.program-context",
        program_context_schema.get("additionalProperties") is False
        and set(program_context_schema.get("properties", {}))
        == {"program_id", "status"},
        {
            "code": "missing_or_drifted_tool",
            "capability": "investment_program_context",
        },
    )
    program_variants = (
        (tools.get("investment_program_update") or {})
        .get("inputSchema", {})
        .get("oneOf", [])
    )
    program_paths = {
        (
            item.get("properties", {}).get("operation", {}).get("const"),
            item.get("properties", {}).get("status", {}).get("const"),
        ): set(item.get("required", []))
        for item in program_variants
    }
    check(
        "mcp.tools-list.program-update-variants",
        set(program_paths)
        == {
            ("create", None),
            ("revise", None),
            ("confirm", None),
            ("status", "active"),
            ("status", "paused"),
            ("status", "archived"),
        }
        and "expected_version" in program_paths.get(("revise", None), set())
        and "user_approval_ref" in program_paths.get(("confirm", None), set())
        and all(
            "expected_version" in program_paths.get(("status", status), set())
            for status in ("active", "paused", "archived")
        ),
        {
            "code": "missing_or_drifted_tool",
            "capability": "investment_program_update",
        },
    )
    brief_variants = (
        (tools.get("investment_brief_update") or {})
        .get("inputSchema", {})
        .get("oneOf", [])
    )
    brief_paths = {
        (
            item.get("properties", {}).get("operation", {}).get("const"),
            item.get("properties", {}).get("brief_type", {}).get("const"),
        ): set(item.get("required", []))
        for item in brief_variants
    }
    check(
        "mcp.tools-list.brief-update-variants",
        set(brief_paths)
        == {
            ("publish", "daily"),
            ("publish", "weekly"),
            ("publish", "monthly"),
            ("presented", None),
            ("metrics_calculate", None),
            ("scorecard_publish", None),
        }
        and {"period_start", "period_end"}
        <= brief_paths.get(("metrics_calculate", None), set())
        and {"metrics", "comparisons", "source_refs", "caveats"}
        <= brief_paths.get(("scorecard_publish", None), set()),
        {
            "code": "missing_or_drifted_tool",
            "capability": "investment_brief_update",
        },
    )
    execution_variants = (
        (tools.get("investment_execution_update") or {})
        .get("inputSchema", {})
        .get("oneOf", [])
    )
    execution_paths = [
        (
            item.get("properties", {}).get("operation", {}).get("const"),
            item.get("properties", {}).get("plan_type", {}).get("const"),
            item.get("properties", {}).get("status", {}).get("const"),
            set(item.get("required", [])),
        )
        for item in execution_variants
    ]
    execution_operations = {operation for operation, _plan, _status, _required in execution_paths}
    strategy_create_types = {
        plan_type
        for operation, plan_type, _status, _required in execution_paths
        if operation == "strategy_create"
    }
    strategy_order_statuses = {
        status
        for operation, _plan, status, _required in execution_paths
        if operation == "strategy_order_report"
    }
    check(
        "mcp.tools-list.execution-update-variants",
        execution_operations
        == {
            "prepare", "order", "report_fill", "confirm_fill", "cancel",
            "strategy_create", "strategy_configured", "strategy_activate",
            "strategy_order_report", "strategy_terminate_request",
            "strategy_terminated", "strategy_etf_dividend", "strategy_sleep",
            "strategy_exception", "strategy_reconcile",
        }
        and strategy_create_types
        == {"priced_buy", "priced_sell", "bracket_exit", "moving_grid"}
        and strategy_order_statuses
        == {
            "triggered", "submitted", "partially_filled", "filled",
            "cancelled", "rejected", "unknown",
        }
        and all(
            "idempotency_key" in required
            for operation, _plan, _status, required in execution_paths
            if operation in {"prepare", "strategy_create"}
        )
        and all(
            {"direction", "sleeping"} <= required
            for operation, _plan, _status, required in execution_paths
            if operation == "strategy_sleep"
        )
        and all(
            "reconciliation_id" in required
            for operation, _plan, _status, required in execution_paths
            if operation == "strategy_reconcile"
        ),
        {
            "code": "missing_or_drifted_tool",
            "capability": "investment_execution_update",
        },
    )
    workflow_context_variants = (
        (tools.get("investment_workflow_context") or {})
        .get("inputSchema", {})
        .get("oneOf", [])
    )
    workflow_views = {
        item.get("properties", {}).get("view", {}).get("const"): set(
            item.get("required", [])
        )
        for item in workflow_context_variants
    }
    check(
        "mcp.tools-list.workflow-context-variants",
        set(workflow_views)
        == {
            "schedules", "schedule", "schedule_history", "runs", "run",
            "deliveries", "delivery", "delivery_status", "system_status", "doctor",
            "execution_strategies", "execution_strategy",
        }
        and "schedule_id" in workflow_views.get("schedule", set())
        and "run_id" in workflow_views.get("run", set())
        and "delivery_id" in workflow_views.get("delivery", set())
        and "plan_id" in workflow_views.get("execution_strategy", set())
        and "run_id" not in workflow_views.get("execution_strategy", set()),
        {
            "code": "missing_or_drifted_tool",
            "capability": "investment_workflow_context",
        },
    )
    workflow_update_variants = (
        (tools.get("investment_workflow_update") or {})
        .get("inputSchema", {})
        .get("oneOf", [])
    )
    workflow_update_paths = {
        (
            item.get("properties", {}).get("operation", {}).get("const"),
            item.get("properties", {}).get("status", {}).get("const"),
            item.get("properties", {}).get("success", {}).get("const"),
        ): set(item.get("required", []))
        for item in workflow_update_variants
    }
    check(
        "mcp.tools-list.workflow-update-variants",
        {
            operation for operation, _status, _success in workflow_update_paths
        }
        == {
            "schedule_create", "schedule_patch", "schedule_status",
            "schedule_run_now", "run_complete", "run_cancel", "wake_claim",
            "wake_complete",
        }
        and all(
            "expected_version"
            in workflow_update_paths.get(("schedule_status", status, None), set())
            for status in ("active", "paused", "archived")
        )
        and "expected_version"
        in workflow_update_paths.get(("schedule_patch", None, None), set())
        and "error"
        in workflow_update_paths.get(("run_complete", None, False), set())
        and "error"
        in workflow_update_paths.get(("wake_complete", None, False), set()),
        {
            "code": "missing_or_drifted_tool",
            "capability": "investment_workflow_update",
        },
    )
    delivery_update_variants = (
        (tools.get("investment_delivery_update") or {})
        .get("inputSchema", {})
        .get("oneOf", [])
    )
    delivery_update_paths = {
        item.get("properties", {}).get("operation", {}).get("const"): set(
            item.get("required", [])
        )
        for item in delivery_update_variants
    }
    check(
        "mcp.tools-list.delivery-update-variants",
        set(delivery_update_paths)
        == {
            "prepare", "digest_send", "attention_decide", "attention_delivered",
            "attention_feedback",
        }
        and {"delivery_id", "conclusion", "summary", "key_evidence", "next_step"}
        <= delivery_update_paths.get("prepare", set())
        and "delivery_ids" in delivery_update_paths.get("digest_send", set())
        and "attention_decision_id"
        in delivery_update_paths.get("attention_feedback", set()),
        {
            "code": "missing_or_drifted_tool",
            "capability": "investment_delivery_update",
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

    workflow = observation.get("workflow_delivery", {})
    schedule_flow = workflow.get("schedule", {})
    check(
        WORKFLOW_VERSION_INVARIANT,
        isinstance(schedule_flow.get("stale_patch_error"), str)
        and schedule_flow["stale_patch_error"].startswith(
            "investment_workflow.version_conflict:"
        )
        and isinstance(schedule_flow.get("stale_status_error"), str)
        and schedule_flow["stale_status_error"].startswith(
            "investment_workflow.version_conflict:"
        )
        and (schedule_flow.get("patched") or {}).get("version")
        == (schedule_flow.get("created") or {}).get("version", 0) + 1
        and (schedule_flow.get("paused") or {}).get("version")
        == (schedule_flow.get("patched") or {}).get("version", 0) + 1
        and (schedule_flow.get("resumed") or {}).get("version")
        == (schedule_flow.get("paused") or {}).get("version", 0) + 1
        and (schedule_flow.get("context") or {}).get("name")
        == (schedule_flow.get("created") or {}).get("name"),
        {
            "code": "invariant_violation",
            "capability": "investment_workflow_update",
            "invariant": WORKFLOW_VERSION_INVARIANT,
            "counterexample": "a stale Schedule mutation overwrote the current version",
        },
    )
    recoverable_flow = workflow.get("recoverable", {})
    required_flow = workflow.get("required", {})
    check(
        WORKFLOW_WAKE_LEASE_INVARIANT,
        required_flow.get("duplicate_wake") is None
        and isinstance(recoverable_flow.get("wrong_owner_error"), str)
        and recoverable_flow["wrong_owner_error"].startswith(
            "investment_workflow.wake_lease_conflict:"
        )
        and (recoverable_flow.get("wake_failed") or {}).get("status") == "retry"
        and (recoverable_flow.get("run_after_failure") or {}).get("status")
        == "recoverable",
        {
            "code": "invariant_violation",
            "capability": "investment_workflow_update",
            "invariant": WORKFLOW_WAKE_LEASE_INVARIANT,
            "counterexample": "duplicate or non-owner Wake completion replaced an active lease",
        },
    )
    digest_flow = workflow.get("digest", {})
    check(
        WORKFLOW_RUN_DELIVERY_INVARIANT,
        (required_flow.get("completed") or {}).get("status") == "succeeded"
        and (required_flow.get("delivery_after_run") or {}).get("status")
        == "pending_send"
        and (required_flow.get("delivery_after_run") or {}).get("delivered_at")
        is None
        and (digest_flow.get("completed") or {}).get("status") == "succeeded"
        and all(
            item.get("status") == "pending_send" and item.get("delivered_at") is None
            for item in digest_flow.get("sent") or []
        ),
        {
            "code": "invariant_violation",
            "capability": "investment_delivery_update",
            "invariant": WORKFLOW_RUN_DELIVERY_INVARIANT,
            "counterexample": "a succeeded Run was reported as delivered without a transport receipt",
        },
    )
    terminal_runs = workflow.get("terminal_runs", {})
    queue_feedback = (
        observation.get("decision_action", {}).get("queue", {}).get(
            "attention_feedback"
        )
        or {}
    )
    check(
        DELIVERY_STATE_INVARIANT,
        isinstance(required_flow.get("immutable_error"), str)
        and required_flow["immutable_error"].startswith(
            "investment_delivery.immutable:"
        )
        and (digest_flow.get("prepared") or {}).get("status") == "queued_digest"
        and isinstance(digest_flow.get("false_delivery_error"), str)
        and "only notify_now" in digest_flow["false_delivery_error"]
        and (terminal_runs.get("failed") or {}).get("status") == "failed"
        and (terminal_runs.get("cancelled") or {}).get("status") == "cancelled"
        and queue_feedback.get("feedback") == "useful"
        and queue_feedback.get("policy_change") == "none",
        {
            "code": "invariant_violation",
            "capability": "investment_delivery_update",
            "invariant": DELIVERY_STATE_INVARIANT,
            "counterexample": "Delivery or Attention state was silently overwritten without the required transition evidence",
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
        == research.get("monitoring_release_at"),
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

    decision_action = observation.get("decision_action", {})
    decision_result = decision_action.get("decision") or {}
    decision_revision = decision_result.get("revision") or {}
    decision_metadata = decision_revision.get("metadata") or {}
    bounded_decision_metadata = (
        (decision_action.get("bounded_decision") or {}).get("revision") or {}
    ).get("metadata", {})
    decision_context_after_accept = (
        decision_action.get("after_accept", {}).get("decision") or {}
    )
    check(
        DECISION_RESEARCH_SEPARATION_INVARIANT,
        validation.get("status") == "eligible_for_decision"
        and isinstance(decision_action.get("unqualified_decision_error"), str)
        and "Research Validation" in decision_action["unqualified_decision_error"]
        and decision_metadata.get("decision_kind") == "action"
        and decision_metadata.get("valid_until")
        == (research.get("fixture") or {}).get("valid_until")
        and bool(decision_metadata.get("invalidators"))
        and bool(decision_metadata.get("no_action"))
        and bool(decision_metadata.get("alternatives"))
        and bool(decision_metadata.get("source_refs"))
        and bounded_decision_metadata.get("decision_kind") == "conditional_action"
        and bounded_decision_metadata.get("action_tier") == "bounded"
        and isinstance(decision_action.get("expired_decision_error"), str)
        and "valid_until must be in the future"
        in decision_action["expired_decision_error"]
        and len(decision_context_after_accept.get("recent_decisions", [])) == 2,
        {
            "code": "invariant_violation",
            "capability": "investment_decision_publish",
            "invariant": DECISION_RESEARCH_SEPARATION_INVARIANT,
            "counterexample": "Research Validation became a Decision or an unqualified/expired Decision was published",
        },
    )

    plans = decision_action.get("plans", {})
    standard_plan = plans.get("standard") or {}
    bounded_plan = plans.get("bounded") or {}
    blocked_plan = plans.get("blocked") or {}
    check(
        RISK_GATE_BOUNDARY_INVARIANT,
        (standard_plan.get("risk") or {}).get("status") == "pass"
        and standard_plan.get("action_tier") == "standard"
        and standard_plan.get("eligible_for_decision") is True
        and (bounded_plan.get("risk") or {}).get("status") == "pass"
        and bounded_plan.get("action_tier") == "bounded"
        and bounded_plan.get("eligible_for_conditional_decision") is True
        and (blocked_plan.get("risk") or {}).get("status") == "blocked"
        and bool((blocked_plan.get("risk") or {}).get("violations"))
        and blocked_plan.get("eligible_for_decision") is False
        and isinstance(decision_action.get("blocked_decision_error"), str)
        and "passing Risk Gate" in decision_action["blocked_decision_error"]
        and isinstance(plans.get("bounded_missing_sessions_error"), str)
        and plans["bounded_missing_sessions_error"].startswith(
            "capability.input.invalid:"
        ),
        {
            "code": "invariant_violation",
            "capability": "investment_action_plan",
            "invariant": RISK_GATE_BOUNDARY_INVARIANT,
            "counterexample": "Risk Gate acted like a research opinion or failed to veto an unaffordable action",
        },
    )

    queue = decision_action.get("queue", {})
    portfolio_before_accept = (
        decision_action.get("before_accept", {}).get("portfolio") or {}
    )
    portfolio_after_accept = (
        decision_action.get("after_accept", {}).get("portfolio") or {}
    )
    accepted = queue.get("accepted") or {}
    check(
        ACTION_ACCEPTANCE_INVARIANT,
        (queue.get("enqueued") or {}).get("state") == "ready"
        and (queue.get("snoozed") or {}).get("state") == "snoozed"
        and (queue.get("presented") or {}).get("state") == "presented"
        and accepted.get("state") == "accepted"
        and accepted.get("user_confirmation_ref")
        == "synthetic:user-message:accept"
        and (queue.get("rejected") or {}).get("state") == "rejected"
        and (queue.get("rejected") or {}).get("user_confirmation_ref")
        == "synthetic:user-message:reject"
        and isinstance(queue.get("missing_confirmation_error"), str)
        and queue["missing_confirmation_error"].startswith(
            "capability.input.invalid:"
        )
        and decision_context_after_accept.get("executions") == []
        and any(
            card.get("state") == "accepted"
            and card.get("execution_created") is False
            for card in decision_context_after_accept.get("action_cards", [])
        )
        and all(
            (portfolio_before_accept.get("portfolio") or {}).get(field)
            == (portfolio_after_accept.get("portfolio") or {}).get(field)
            for field in ("cash", "positions", "total_by_currency")
        )
        and portfolio_before_accept.get("pending_transactions")
        == portfolio_after_accept.get("pending_transactions"),
        {
            "code": "invariant_violation",
            "capability": "investment_action_update",
            "invariant": ACTION_ACCEPTANCE_INVARIANT,
            "counterexample": "Action Card response created an Execution or changed Portfolio Ledger truth",
        },
    )

    execution_flow = decision_action.get("execution", {})
    execution_portfolios = execution_flow.get("portfolio", {})
    execution_before = execution_portfolios.get("before") or {}
    before_truth = execution_before.get("portfolio") or {}

    def same_confirmed_portfolio(value: dict[str, Any]) -> bool:
        state = (value or {}).get("portfolio") or {}
        return all(
            state.get(field) == before_truth.get(field)
            for field in ("cash", "positions", "total_by_currency")
        )

    partial_report = execution_flow.get("partial_report") or {}
    duplicate_report = execution_flow.get("duplicate_partial_report") or {}
    partial_confirmation = execution_flow.get("partial_confirm") or {}
    final_confirmation = execution_flow.get("final_confirm") or {}
    after_report = execution_portfolios.get("after_report") or {}
    after_partial_confirmation = (
        execution_portfolios.get("after_partial_confirm") or {}
    )
    after_final_confirmation = (
        execution_portfolios.get("after_final_confirm") or {}
    )
    check(
        EXECUTION_CONFIRMATION_INVARIANT,
        (execution_flow.get("queue") or {}).get("state") == "accepted"
        and (execution_flow.get("prepared") or {}).get("id")
        == (execution_flow.get("duplicate_prepare") or {}).get("id")
        and (execution_flow.get("ordered") or {}).get("status") == "ordered"
        and same_confirmed_portfolio(execution_portfolios.get("after_prepare") or {})
        and same_confirmed_portfolio(execution_portfolios.get("after_order") or {})
        and same_confirmed_portfolio(after_report)
        and partial_report.get("portfolio_changed") is False
        and partial_report.get("requires_confirmation") is True
        and (partial_report.get("pending_ledger_entry") or {}).get("status")
        == "needs_confirmation"
        and (partial_report.get("pending_ledger_entry") or {}).get("id")
        == (duplicate_report.get("pending_ledger_entry") or {}).get("id")
        and len(after_report.get("pending_transactions") or []) == 1
        and isinstance(execution_flow.get("incomplete_fill_error"), str)
        and execution_flow["incomplete_fill_error"].startswith(
            "capability.input.invalid:"
        )
        and (partial_confirmation.get("execution") or {}).get("status")
        == "partially_filled"
        and (partial_confirmation.get("confirmed_ledger_entry") or {}).get(
            "status"
        )
        == "confirmed"
        and partial_confirmation.get("portfolio_changed") is True
        and not same_confirmed_portfolio(after_partial_confirmation)
        and after_partial_confirmation.get("pending_transactions") == []
        and (final_confirmation.get("execution") or {}).get("status")
        == "deviated"
        and len(
            (final_confirmation.get("execution") or {}).get(
                "ledger_entry_ids", []
            )
        )
        == 2
        and final_confirmation.get("truth") == "confirmed_ledger_replay"
        and not same_confirmed_portfolio(after_final_confirmation),
        {
            "code": "invariant_violation",
            "capability": "investment_execution_update",
            "invariant": EXECUTION_CONFIRMATION_INVARIANT,
            "counterexample": (
                "Action acceptance, Execution preparation, broker order or reported "
                "fill changed confirmed Portfolio truth before user confirmation"
            ),
        },
    )
    strategy_flow = execution_flow.get("strategy", {})
    strategy_portfolios = strategy_flow.get("portfolio", {})
    strategy_before = strategy_portfolios.get("before_order") or {}
    strategy_before_truth = strategy_before.get("portfolio") or {}

    def same_strategy_portfolio(value: dict[str, Any]) -> bool:
        state = (value or {}).get("portfolio") or {}
        return all(
            state.get(field) == strategy_before_truth.get(field)
            for field in ("cash", "positions", "total_by_currency")
        )

    created_strategy = strategy_flow.get("created") or {}
    strategy_order = strategy_flow.get("order") or {}
    duplicate_strategy_order = strategy_flow.get("duplicate_order") or {}
    terminated_strategy = strategy_flow.get("terminated") or {}
    reconciled_strategy = strategy_flow.get("reconciled") or {}
    grid_reports = strategy_flow.get("grid_only_reports") or {}
    sleeping_grid = grid_reports.get("sleeping") or {}
    sleeping_grid_view = grid_reports.get("sleeping_view") or {}
    dividend_terminated_grid = grid_reports.get("dividend_terminated") or {}
    grid_portfolios = grid_reports.get("portfolio") or {}
    grid_before_truth = (grid_portfolios.get("before") or {}).get("portfolio") or {}
    grid_after_truth = (grid_portfolios.get("after") or {}).get("portfolio") or {}
    check(
        "mcp.execution-strategy.grid-reports/v1",
        isinstance(grid_reports.get("priced_sleep_error"), str)
        and "moving_grid" in grid_reports["priced_sleep_error"]
        and isinstance(grid_reports.get("priced_dividend_error"), str)
        and "moving_grid" in grid_reports["priced_dividend_error"]
        and sleeping_grid.get("plan_type") == "moving_grid"
        and sleeping_grid.get("buy_direction_state") == "sleeping"
        and sleeping_grid.get("sell_direction_state") == "active"
        and sleeping_grid_view.get("buy_direction_state") == "sleeping"
        and sleeping_grid_view.get("sell_direction_state") == "active"
        and dividend_terminated_grid.get("status") == "terminated"
        and any(
            event.get("event_type") == "corporate_action"
            and (event.get("payload") or {}).get("corporate_action_ref")
            == "synthetic:etf-dividend-termination"
            for event in dividend_terminated_grid.get("events") or []
        )
        and all(
            grid_after_truth.get(field) == grid_before_truth.get(field)
            for field in ("cash", "positions", "total_by_currency")
        ),
        {
            "code": "invariant_violation",
            "capability": "investment_execution_update",
            "invariant": EXECUTION_CONFIRMATION_INVARIANT,
            "counterexample": (
                "directional grid sleep or ETF-dividend termination lost read/write "
                "symmetry or changed confirmed Portfolio truth"
            ),
        },
    )
    check(
        EXECUTION_RECONCILIATION_INVARIANT,
        (strategy_flow.get("queue") or {}).get("state") == "accepted"
        and created_strategy.get("plan_type") == "priced_buy"
        and created_strategy.get("id")
        == (strategy_flow.get("duplicate_create") or {}).get("id")
        and created_strategy.get("idempotency_key")
        == "synthetic-priced-buy-strategy"
        and isinstance(strategy_flow.get("invalid_validity_error"), str)
        and strategy_flow["invalid_validity_error"].startswith(
            "capability.input.invalid:"
        )
        and (strategy_flow.get("configured") or {}).get("broker_validity", {}).get(
            "sessions"
        )
        == 5
        and (strategy_flow.get("activated") or {}).get("status") == "active"
        and any(
            item.get("id") == created_strategy.get("id")
            for item in strategy_flow.get("list") or []
        )
        and (strategy_flow.get("active_view") or {}).get("id")
        == created_strategy.get("id")
        and bool(strategy_order.get("reported_order_execution_id"))
        and len(strategy_order.get("outstanding_orders") or []) == 1
        and strategy_order.get("reported_order_execution_id")
        == duplicate_strategy_order.get("reported_order_execution_id")
        and len(strategy_order.get("events") or [])
        == len(duplicate_strategy_order.get("events") or [])
        and same_strategy_portfolio(strategy_portfolios.get("after_order") or {})
        and (strategy_flow.get("termination_requested") or {}).get("status")
        == "termination_pending"
        and terminated_strategy.get("status") == "terminated"
        and len(terminated_strategy.get("outstanding_orders") or []) == 1
        and isinstance(strategy_flow.get("live_order_reconcile_error"), str)
        and "live or unknown" in strategy_flow["live_order_reconcile_error"]
        and len(
            (strategy_flow.get("cancelled_order") or {}).get(
                "outstanding_orders", []
            )
        )
        == 0
        and same_strategy_portfolio(strategy_portfolios.get("after_cancel") or {})
        and (strategy_flow.get("full_scope_reconciliation") or {}).get("status")
        == "matched"
        and (
            (strategy_flow.get("full_scope_reconciliation") or {}).get(
                "reconciliation", {}
            )
        ).get("full_scope_matched")
        is True
        and reconciled_strategy.get("status") == "reconciled"
        and (reconciled_strategy.get("reconciliation") or {}).get(
            "reconciliation_id"
        )
        == (strategy_flow.get("full_scope_reconciliation") or {}).get("id")
        and (strategy_flow.get("reconciled_view") or {}).get("id")
        == reconciled_strategy.get("id")
        and (strategy_flow.get("reconciled_view") or {}).get("reconciliation")
        == reconciled_strategy.get("reconciliation"),
        {
            "code": "invariant_violation",
            "capability": "investment_execution_update",
            "invariant": EXECUTION_RECONCILIATION_INVARIANT,
            "counterexample": (
                "broker strategy read/write state lost its reported Execution, "
                "outstanding order or full-scope reconciliation boundary"
            ),
        },
    )

    program_brief = observation.get("program_brief", {})
    program_flow = program_brief.get("program", {})
    created_program = program_flow.get("created") or {}
    revised_program = program_flow.get("revised") or {}
    confirmed_program = program_flow.get("confirmed") or {}
    confirmed_revision = confirmed_program.get("current_revision") or {}
    context_after_draft = program_flow.get("context_after_draft") or {}
    context_after_confirm = program_flow.get("context_after_confirm") or {}
    final_program_context = program_flow.get("final_context") or {}
    check(
        PROGRAM_CONFIRMATION_INVARIANT,
        created_program.get("status") == "draft"
        and (context_after_draft.get("selected") or {}).get("status") == "draft"
        and (context_after_draft.get("current") or {}).get("id")
        != created_program.get("id")
        and isinstance(program_flow.get("unconfirmed_brief_error"), str)
        and program_flow["unconfirmed_brief_error"].startswith(
            "investment_program.not_active:"
        )
        and revised_program.get("version") == created_program.get("version", 0) + 1
        and len(revised_program.get("revisions", [])) == 2
        and isinstance(program_flow.get("stale_revision_error"), str)
        and program_flow["stale_revision_error"].startswith(
            "investment_program.version_conflict:"
        )
        and isinstance(program_flow.get("missing_approval_error"), str)
        and program_flow["missing_approval_error"].startswith(
            "capability.input.invalid:"
        )
        and confirmed_program.get("status") == "active"
        and confirmed_revision.get("status") == "current"
        and confirmed_revision.get("user_approval_ref")
        == "synthetic:user-message:approve-program"
        and (context_after_confirm.get("current") or {}).get("id")
        == confirmed_program.get("id")
        and (program_flow.get("paused") or {}).get("status") == "paused"
        and isinstance(program_flow.get("stale_status_error"), str)
        and program_flow["stale_status_error"].startswith(
            "investment_program.version_conflict:"
        )
        and (program_flow.get("resumed") or {}).get("status") == "active"
        and (program_flow.get("archived") or {}).get("status") == "archived"
        and final_program_context.get("current") is None
        and len((final_program_context.get("selected") or {}).get("revisions", []))
        == 2,
        {
            "code": "invariant_violation",
            "capability": "investment_program_update",
            "invariant": PROGRAM_CONFIRMATION_INVARIANT,
            "counterexample": "an unconfirmed or stale Investment Program change became effective without the required user approval and optimistic version",
        },
    )

    brief_flow = program_brief.get("brief", {})
    metrics = brief_flow.get("metrics") or {}
    scorecard = brief_flow.get("scorecard") or {}
    published_brief = brief_flow.get("no_action") or {}
    published_snapshot = (published_brief.get("payload") or {}).get(
        "execution_snapshot", {}
    )
    check(
        BRIEF_NO_ACTION_INVARIANT,
        isinstance(brief_flow.get("no_action_error"), str)
        and brief_flow["no_action_error"].startswith(
            "investment_brief.unresolved_obligations:"
        )
        and (brief_flow.get("research_recovery") or {})
        .get("recovered", {})
        .get("research_work_monitors", 0)
        == 1
        and (brief_flow.get("resolved_monitoring") or {}).get("status")
        == "rejected"
        and published_brief.get("conclusion") == "no_action",
        {
            "code": "invariant_violation",
            "capability": "investment_brief_update",
            "invariant": BRIEF_NO_ACTION_INVARIANT,
            "counterexample": "no_action was published while a declared Research Work obligation remained open",
        },
    )
    check(
        "mcp.brief.calculation-lineage-required/v1",
        isinstance(brief_flow.get("missing_lineage_error"), str)
        and brief_flow["missing_lineage_error"].startswith(
            "investment_brief.calculation_lineage_required:"
        )
        and isinstance(metrics.get("calculation_id"), str)
        and scorecard.get("status") == "ready"
        and (scorecard.get("metrics") or [{}])[0].get("calculation_id")
        == metrics.get("calculation_id")
        and scorecard.get("source_refs") == [metrics.get("calculation_id")]
        and scorecard.get("caveats") == ["synthetic one-day operating cohort"],
        {
            "code": "missing_calculation_lineage",
            "capability": "investment_brief_update",
        },
    )

    brief_projection_before = program_brief.get("brief_projection_before", {})
    brief_projection_after = program_brief.get("brief_projection_after", {})
    program_projection_after = program_brief.get("program_projection_after", {})
    brief_truth_unchanged = (
        _stable_portfolio_projection(
            brief_projection_before.get("portfolio") or {},
            include_program_policy=True,
        )
        == _stable_portfolio_projection(
            brief_projection_after.get("portfolio") or {},
            include_program_policy=True,
        )
        and _stable_research_projection(
            brief_projection_before.get("research") or {},
            include_active_program_queue=True,
        )
        == _stable_research_projection(
            brief_projection_after.get("research") or {},
            include_active_program_queue=True,
        )
        and _stable_evaluation_projection(
            brief_projection_before.get("evaluation") or {}
        )
        == _stable_evaluation_projection(
            brief_projection_after.get("evaluation") or {}
        )
        and _stable_program_projection(
            brief_projection_before.get("program") or {}
        )
        == _stable_program_projection(
            brief_projection_after.get("program") or {}
        )
    )
    program_truth_unchanged = (
        _stable_portfolio_projection(
            brief_projection_after.get("portfolio") or {},
            include_program_policy=False,
        )
        == _stable_portfolio_projection(
            program_projection_after.get("portfolio") or {},
            include_program_policy=False,
        )
        and _stable_research_projection(
            brief_projection_after.get("research") or {},
            include_active_program_queue=False,
        )
        == _stable_research_projection(
            program_projection_after.get("research") or {},
            include_active_program_queue=False,
        )
        and _stable_evaluation_projection(
            brief_projection_after.get("evaluation") or {}
        )
        == _stable_evaluation_projection(
            program_projection_after.get("evaluation") or {}
        )
    )
    context_refs = (observation.get("research", {}).get("fixture") or {}).get(
        "context_refs"
    )
    original_program_revision = (
        (brief_projection_after.get("program") or {}).get("selected") or {}
    ).get("current_revision", {})
    check(
        PROGRAM_PROJECTION_INVARIANT,
        program_truth_unchanged
        and original_program_revision.get("context_refs") == context_refs
        and confirmed_revision.get("context_refs") == context_refs
        and ((final_program_context.get("selected") or {}).get(
            "current_revision"
        ) or {}).get("context_refs") == context_refs,
        {
            "code": "invariant_violation",
            "capability": "investment_program_update",
            "invariant": PROGRAM_PROJECTION_INVARIANT,
            "counterexample": "Program lifecycle copied or changed Context, Portfolio, Research or Performance truth instead of referencing it",
        },
    )
    check(
        BRIEF_PROJECTION_INVARIANT,
        brief_truth_unchanged
        and published_brief.get("conclusion") == "no_action"
        and published_snapshot.get("truth") == "confirmed_ledger_replay"
        and published_snapshot.get("frozen") is True
        and published_snapshot.get("calculation_id")
        in published_brief.get("source_refs", [])
        and metrics.get("calculation_id")
        in published_brief.get("source_refs", []),
        {
            "code": "invariant_violation",
            "capability": "investment_brief_update",
            "invariant": BRIEF_PROJECTION_INVARIANT,
            "counterexample": "Brief or Scorecard replaced Portfolio, Context, Research or Performance truth instead of projecting source references",
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
    continuity_qualification = (continuity.get("portfolio") or {}).get(
        "portfolio_qualification", {}
    )
    revoked_precision = (continuity.get("revoked_portfolio") or {}).get(
        "precision_boundary", {}
    )
    revoked_qualification = (continuity.get("revoked_portfolio") or {}).get(
        "portfolio_qualification", {}
    )
    check(
        CONTINUITY_INVARIANT,
        (continuity.get("confirmed") or {}).get("status") == "active"
        and continuity_precision.get("current_broker_position_proven") is False
        and continuity_precision.get("ledger_position_continuity_supported") is True
        and continuity_precision.get("precise_position_advice_allowed") is True
        and continuity_qualification.get("level") == "preflight_ready"
        and continuity_qualification.get("validity", {}).get(
            "broker_realtime_proven"
        )
        is False
        and (continuity.get("revoked") or {}).get("status") == "revoked"
        and revoked_precision.get("ledger_position_continuity_supported") is False
        and revoked_precision.get("precise_position_advice_allowed") is False
        and revoked_qualification.get("level") == "range_ready"
        and revoked_qualification.get("reason_codes")
        == ["account_continuity_invalid"],
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
    evaluation = observation.get("evaluation", {})
    performance = evaluation.get("performance") or {}
    performance_ledger = evaluation.get("ledger", {})
    external_flow_ids = {
        item.get("ledger_entry_id") for item in performance.get("external_flows", [])
    }
    check(
        PERFORMANCE_CALCULATION_INVARIANT,
        isinstance(performance.get("calculation_id"), str)
        and performance.get("time_basis", {}).get("period_boundary")
        == "start_exclusive_end_inclusive"
        and performance.get("net_external_flow") == "500"
        and performance.get("investment_gain_after_external_flows") == "5"
        and performance.get("transaction_cost") == "15"
        and performance.get("slippage_cost") == "5"
        and performance.get("total_cost") == "20"
        and performance.get("benchmark_return") == "0.05"
        and performance.get("maximum_drawdown") == "0"
        and performance.get("attribution", {}).get("status") == "linked"
        and performance.get("caveats") == []
        and (performance_ledger.get("deposit") or {}).get("id")
        in external_flow_ids,
        {
            "code": "invariant_violation",
            "capability": "investment_performance_calculate",
            "invariant": PERFORMANCE_CALCULATION_INVARIANT,
            "counterexample": "Performance outputs were accepted as model fields or diverged from the isolated confirmed Ledger",
        },
    )
    unavailable_benchmark = evaluation.get("unavailable_benchmark") or {}
    check(
        "mcp.performance.required-evidence-and-variants/v1",
        isinstance(evaluation.get("missing_price_error"), str)
        and evaluation["missing_price_error"].startswith(
            "investment_performance.valuation_required:"
        )
        and isinstance(evaluation.get("missing_benchmark_error"), str)
        and evaluation["missing_benchmark_error"].startswith(
            "capability.input.invalid:"
        )
        and unavailable_benchmark.get("benchmark", {}).get("mode")
        == "unavailable"
        and unavailable_benchmark.get("benchmark_return") is None
        and unavailable_benchmark.get("caveats") == ["benchmark_return_missing"],
        {
            "code": "missing_performance_evidence_rejection",
            "capability": "investment_performance_calculate",
        },
    )

    created_review = evaluation.get("created_review") or {}
    superseded_review = evaluation.get("superseded_review") or {}
    evaluation_context = evaluation.get("context") or {}
    review_id = (created_review.get("review") or {}).get("id")
    review_summary = next(
        (
            item
            for item in evaluation_context.get("reviews", [])
            if item.get("id") == review_id
        ),
        {},
    )
    revision_ids = [
        item.get("id") for item in review_summary.get("revisions", [])
    ]
    check(
        REVIEW_IMMUTABILITY_INVARIANT,
        (created_review.get("revision") or {}).get("revision") == 1
        and (created_review.get("revision") or {}).get("parent_id") is None
        and (superseded_review.get("revision") or {}).get("revision") == 2
        and (superseded_review.get("revision") or {}).get("parent_id")
        == (created_review.get("revision") or {}).get("id")
        and revision_ids
        == [
            (created_review.get("revision") or {}).get("id"),
            (superseded_review.get("revision") or {}).get("id"),
        ]
        and (review_summary.get("current_revision") or {}).get("id")
        == (superseded_review.get("revision") or {}).get("id")
        and created_review.get("history_preserved") is True
        and superseded_review.get("history_preserved") is True
        and isinstance(evaluation.get("stale_supersession_error"), str)
        and evaluation["stale_supersession_error"].startswith(
            "investment_review.revision_conflict:"
        )
        and isinstance(evaluation.get("missing_calculation_error"), str)
        and evaluation["missing_calculation_error"].startswith(
            "capability.input.invalid:"
        ),
        {
            "code": "invariant_violation",
            "capability": "investment_review_publish",
            "invariant": REVIEW_IMMUTABILITY_INVARIANT,
            "counterexample": "Review supersession rewrote history or published without Calculation lineage",
        },
    )
    research_unchanged = _stable_research_projection(
        evaluation.get("research_before") or {},
        include_active_program_queue=True,
    ) == _stable_research_projection(
        evaluation.get("research_after") or {},
        include_active_program_queue=True,
    )
    program_unchanged = _stable_program_projection(
        evaluation.get("program_before") or {}
    ) == _stable_program_projection(evaluation.get("program_after") or {})
    check(
        CHANGE_PROPOSAL_INVARIANT,
        (created_review.get("change_proposal") or {}).get("status")
        == "proposed"
        and (created_review.get("change_proposal") or {}).get(
            "automatic_application"
        )
        is False
        and created_review.get("automatic_changes_applied") is False
        and superseded_review.get("automatic_changes_applied") is False
        and research_unchanged
        and program_unchanged,
        {
            "code": "invariant_violation",
            "capability": "investment_review_publish",
            "invariant": CHANGE_PROPOSAL_INVARIANT,
            "counterexample": "Change Proposal silently modified Thesis, Investment Policy or Strategy Version state",
        },
    )
    return {
        "passed": not failures,
        "profile": "investment",
        "checks": checks,
        "failures": failures,
    }
