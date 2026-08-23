from __future__ import annotations

from datetime import timedelta
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from companion.core import Companion, CompanionError
from companion.timeutil import iso, utc_now
from companion.v6_predictive_recommendations import (
    STOCK_CANDIDATES_KIND,
    STOCK_CANDIDATES_SCHEMA,
    STOCK_SIGNALS_KIND,
    STOCK_SIGNALS_SCHEMA,
)


def ashare_reality():
    return {
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


def thesis_validation_spec():
    return {
        "falsifiers": ["earnings or price evidence reverses the thesis"],
        "counterevidence": {
            "searched": ["issuer disclosures", "independent market data"],
            "findings": [],
        },
        "applicability": {
            "horizon": "one month",
            "conditions": ["A-share market remains normally tradable"],
            "excluded_conditions": ["trading suspension"],
        },
        "cost_assumptions": {
            "commission": "RealitySpec commission",
            "tax": "A-share sell stamp duty",
            "slippage": "bounded by the execution price range",
        },
        "max_evidence_age_days": 30,
    }


def test_home_is_a_small_version_neutral_entrypoint(tmp_path):
    companion = Companion(tmp_path, gate_scope="test_fixture")
    companion.initialize()

    home = companion.investment.home()

    assert set(home) == {
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
    }
    assert home["schema"] == "investment-companion.investment-home/v1"
    assert home["state"] == "setup_required"
    assert home["claims"]["automatic_trading"] is False
    assert home["claims"]["manual_execution_only"] is True
    assert home["claims"]["research_cannot_trade"] is True


def test_context_workbenches_compose_truth_owners_without_new_state(tmp_path):
    companion = Companion(tmp_path, gate_scope="test_fixture")
    companion.initialize()
    account = companion.financial.account_create("Primary", "CNY")
    opening = companion.financial.ledger_add(
        account_id=account["id"],
        entry_type="opening_balance",
        occurred_at=iso(utc_now() - timedelta(days=2)),
        amount="1000",
        currency="CNY",
        source="fixture",
    )
    companion.financial.ledger_confirm(opening["id"])
    ledger_before = companion.financial.ledger_list()

    portfolio = companion.investment.portfolio_context()
    research = companion.investment.research_context()
    decision = companion.investment.decision_context()
    evaluation = companion.investment.evaluation_context()

    assert portfolio["account"]["id"] == account["id"]
    assert portfolio["portfolio"]["cash"] == {"CNY": "1000"}
    assert portfolio["truth"] == "confirmed_ledger_replay"
    assert research["boundary"]["may_not_produce"] == [
        "ledger_entry",
        "execution",
        "automatic_trade",
    ]
    assert decision["execution_boundary"]["mode"] == "human_manual_only"
    assert decision["execution_boundary"]["accepted_action_card_is_order"] is False
    assert evaluation["change_boundary"]["review_may_apply_strategy_change"] is False
    assert companion.financial.ledger_list() == ledger_before


def test_evaluation_context_reads_verified_performance_calculations(tmp_path):
    companion = Companion(tmp_path, gate_scope="test_fixture")
    companion.initialize()
    account = companion.financial.account_create("Primary", "CNY")
    start = utc_now() - timedelta(days=2)
    end = utc_now()
    opening = companion.financial.ledger_add(
        account_id=account["id"],
        entry_type="opening_balance",
        occurred_at=iso(start - timedelta(days=1)),
        amount="1000",
        currency="CNY",
        source="fixture",
    )
    companion.financial.ledger_confirm(opening["id"])
    calculated = companion.performance.calculate_period(
        account_id=account["id"],
        period_start=iso(start),
        period_end=iso(end),
        start_prices={},
        end_prices={},
    )

    context = companion.investment.evaluation_context(limit=1)

    assert context["performance"][0]["id"] == calculated["calculation_id"]


def test_narrow_commands_keep_confirmation_and_current_mandate_boundaries(tmp_path):
    companion = Companion(tmp_path, gate_scope="test_fixture")
    companion.initialize()
    account = companion.investment_commands.transaction_update(
        operation="account_create", name="Primary", base_currency="CNY"
    )
    asset = companion.investment_commands.transaction_update(
        operation="asset_register",
        asset_type="stock",
        name="Fixture",
        currency="CNY",
        identifiers={"ts_code": "600001.SH"},
    )
    opening = companion.investment_commands.transaction_update(
        operation="record",
        account_id=account["id"],
        entry_type="opening_balance",
        occurred_at=iso(utc_now() - timedelta(days=1)),
        amount="1000",
        currency="CNY",
        source="user",
    )
    assert opening["status"] == "needs_confirmation"
    assert companion.financial.portfolio_state(iso(), account["id"])["cash"] == {}
    companion.investment_commands.transaction_update(
        operation="confirm", entry_id=opening["id"]
    )
    assert companion.financial.portfolio_state(iso(), account["id"])["cash"] == {
        "CNY": "1000"
    }
    reconciliation = companion.investment_commands.transaction_update(
        operation="reconcile",
        account_id=account["id"],
        as_of=iso(),
        statement={"cash": {"CNY": "1000"}, "positions": {}},
        source_ref="fixture-statement",
    )
    assert reconciliation["status"] == "matched"
    mandate = companion.investment_commands.context_update(
        operation="draft",
        context_type="mandate",
        content={"max_single_position_weight": "0.2"},
        reason="fixture",
    )
    companion.investment_commands.context_update(
        operation="confirm", revision_id=mandate["id"]
    )
    checked_at = iso()
    market = companion.investment_commands.evidence_update(
        operation="market_snapshot",
        asset_id=asset["id"],
        metric="close",
        value="2",
        observed_at=checked_at,
        source="fixture",
        quality="healthy",
        currency="CNY",
    )

    plan = companion.investment_commands.action_plan(
        as_of=checked_at,
        account_id=account["id"],
        asset_id=asset["id"],
        quantity="500",
        price="2",
        reality_spec=ashare_reality(),
        market_snapshot_id=market["id"],
        max_market_age_seconds=600,
        valid_until=iso(utc_now() + timedelta(hours=1)),
        price_range={"min": "1.9", "max": "2.1"},
    )

    assert plan["eligible_for_decision"] is False
    assert plan["automatic_decision_or_execution"] is False
    assert plan["risk"]["blocked"] is True
    assert any(
        item["rule"] == "max_single_position_weight"
        for item in plan["risk"]["violations"]
    )


def test_investment_mcp_profile_exposes_only_version_neutral_workbenches_and_commands(tmp_path):
    requests = "\n".join(
        [
            json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}),
            json.dumps({"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}}),
            json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": 3,
                    "method": "tools/call",
                    "params": {"name": "investment_home", "arguments": {}},
                }
            ),
            json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": 4,
                    "method": "tools/call",
                    "params": {"name": "v5_today", "arguments": {}},
                }
            ),
        ]
    ) + "\n"
    proc = subprocess.run(
        [sys.executable, "-m", "companion.mcp_server"],
        input=requests,
        text=True,
        capture_output=True,
        cwd=Path(__file__).parents[1],
        env={
            **os.environ,
            "COMPANION_ROOT": str(tmp_path),
            "COMPANION_GATE_SCOPE": "test_fixture",
            "COMPANION_MCP_PROFILE": "investment",
        },
        timeout=30,
        check=True,
    )
    responses = [json.loads(line) for line in proc.stdout.splitlines()]
    names = {item["name"] for item in responses[1]["result"]["tools"]}
    assert len(names) == 22
    assert {
        "investment_home",
        "portfolio_context",
        "investment_program_context",
        "investment_workflow_context",
        "investment_context_update",
        "investment_program_update",
        "investment_opportunity_update",
        "investment_research_publish",
        "investment_decision_publish",
        "investment_review_publish",
        "investment_transaction_update",
        "investment_evidence_update",
        "investment_action_update",
        "investment_execution_update",
        "investment_workflow_update",
        "investment_delivery_update",
        "investment_action_plan",
        "investment_brief_update",
    } <= names
    assert "investment_context_draft" not in names
    assert "investment_context_confirm" not in names
    assert "investment_action_respond" not in names
    assert "investment_transaction_record" not in names
    assert "investment_transaction_confirm" not in names
    assert "investment_risk_assess" not in names
    assert not any(name.startswith(("v4_", "v5_", "v6_")) for name in names)
    assert responses[2]["result"]["structuredContent"]["result"]["state"] == "setup_required"
    assert responses[3]["result"]["isError"] is True
    assert "unknown tool" in responses[3]["result"]["content"][0]["text"]


def test_research_context_normalizes_legacy_predictive_records_without_promoting_strategy(tmp_path):
    companion = Companion(tmp_path, gate_scope="test_fixture")
    companion.initialize()
    asset_id = "tushare:600001.SH"
    candidates = companion.data.manifest_publish(
        kind=STOCK_CANDIDATES_KIND,
        schema_version=STOCK_CANDIDATES_SCHEMA,
        manifest={
            "program_id": "fixture-program",
            "as_of": "2026-08-22",
            "candidates": [
                {
                    "asset_id": asset_id,
                    "ts_code": "600001.SH",
                    "research_status": "candidate",
                }
            ],
        },
    )
    signals = companion.data.manifest_publish(
        kind=STOCK_SIGNALS_KIND,
        schema_version=STOCK_SIGNALS_SCHEMA,
        manifest={
            "program_id": "fixture-program",
            "as_of": "2026-08-22",
            "candidate_manifest_id": candidates["id"],
            "signals": [
                {
                    "asset_id": asset_id,
                    "recommendation_state": "provisional_action",
                    "validation_status": "unvalidated",
                }
            ],
        },
    )
    corroboration = companion.data.manifest_publish(
        kind="predictive_context_corroboration",
        schema_version="research-evidence/v1",
        manifest={
            "source": "issuer filing",
            "source_group": "issuer",
            "first_known_at": "2026-08-22T00:00:00Z",
            "observed_at": "2026-08-22T00:00:00Z",
        },
    )
    research = companion.investment_commands.research_publish(
        subject={"asset_id": asset_id},
        content="# Predictive candidate review\nThe legacy candidate remains research evidence only.",
        evidence_manifest_ids=[candidates["id"], corroboration["id"]],
        knowledge_cutoff="2026-08-22T12:00:00Z",
        validation_spec=thesis_validation_spec(),
    )

    context = companion.investment.research_context(subject_id=asset_id)

    records = context["records"]
    assert {item["record_id"] for item in records} == {candidates["id"], signals["id"]}
    assert {item["record_type"] for item in records} == {
        "stock_candidates",
        "stock_provisional_signals",
    }
    assert context["boundary"]["formal_strategy_requires_registry_entry"] is True
    assert context["boundary"]["decision_requires_eligible_validation"] is True
    by_id = {item["record_id"]: item for item in records}
    assert by_id[candidates["id"]]["validation"]["calculation_id"] == research["validation"]["calculation_id"]
    assert by_id[candidates["id"]]["validation"]["eligible_for_decision"] is False
    assert by_id[signals["id"]]["validation"]["reasons"] == [
        "formal_research_validation_calculation_required"
    ]
    assert all(item["method"]["identity_status"] == "research_method_only" for item in records)


def test_research_and_decision_commands_freeze_evidence_portfolio_and_risk(tmp_path):
    companion = Companion(tmp_path, gate_scope="test_fixture")
    companion.initialize()
    account = companion.financial.account_create("Primary", "CNY")
    asset = companion.financial.asset_upsert(
        "stock", "Decision fixture", "CNY", {"ts_code": "600002.SH"}
    )
    opening = companion.financial.ledger_add(
        account_id=account["id"],
        entry_type="opening_balance",
        occurred_at=iso(utc_now() - timedelta(days=1)),
        amount="1000",
        currency="CNY",
        source="fixture",
    )
    companion.financial.ledger_confirm(opening["id"])
    investor = companion.investment_commands.context_draft(
        context_type="investor",
        content={"goal": "capital growth"},
        reason="fixture",
    )
    mandate = companion.investment_commands.context_draft(
        context_type="mandate",
        content={"max_single_position_weight": "0.5"},
        reason="fixture",
    )
    companion.investment_commands.context_confirm(revision_id=investor["id"])
    companion.investment_commands.context_confirm(revision_id=mandate["id"])
    cutoff = iso()
    evidence = companion.data.manifest_publish(
        kind="fixture_official_evidence",
        schema_version="fixture/v1",
        manifest={
            "asset_id": asset["id"],
            "claim": "fixture issuer evidence",
            "source": "fixture issuer",
            "source_group": "issuer",
            "first_known_at": cutoff,
            "observed_at": cutoff,
        },
    )
    market_evidence = companion.data.manifest_publish(
        kind="fixture_market_evidence",
        schema_version="fixture/v1",
        manifest={
            "asset_id": asset["id"],
            "claim": "fixture independent market evidence",
            "source": "fixture market provider",
            "source_group": "market-data",
            "first_known_at": cutoff,
            "observed_at": cutoff,
        },
    )
    evidence_ids = [evidence["id"], market_evidence["id"]]
    research = companion.investment_commands.research_publish(
        subject={"asset_id": asset["id"]},
        content="# Thesis\nEvidence-backed fixture.",
        evidence_manifest_ids=evidence_ids,
        knowledge_cutoff=cutoff,
        validation_spec=thesis_validation_spec(),
    )
    assert research["validation"]["status"] == "eligible_for_decision"
    as_of = iso()
    valid_until = iso(utc_now() + timedelta(days=1))
    market = companion.financial.market_add(
        asset["id"], "close", "1", as_of, "fixture", "healthy", "CNY"
    )
    risk = companion.investment_commands.risk_assess(
        as_of=as_of,
        account_id=account["id"],
        asset_id=asset["id"],
        quantity="100",
        price="1",
        reality_spec=ashare_reality(),
        market_snapshot_id=market["id"],
        max_market_age_seconds=600,
        valid_until=valid_until,
        price_range={"min": "0.95", "max": "1.05"},
    )
    ledger_before = companion.financial.ledger_list()

    decision_args = {
        "subject": {"asset_id": asset["id"]},
        "content": "# Decision\nBuy only within the frozen risk boundary.",
        "decision_kind": "action",
        "account_id": account["id"],
        "as_of": as_of,
        "knowledge_cutoff": cutoff,
        "valid_until": valid_until,
        "thesis_revision_ids": [research["revision"]["id"]],
        "evidence_manifest_ids": evidence_ids,
        "invalidators": ["price leaves range", "mandate changes"],
        "no_action": {"choice": "hold cash", "reason": "avoid unvalidated risk"},
        "alternatives": [{"choice": "hold cash"}, {"choice": "smaller position"}],
        "risk_calculation_id": risk["calculation_id"],
    }
    with pytest.raises(CompanionError, match="Research Validation"):
        companion.investment_commands.decision_publish(**decision_args)
    decision = companion.investment_commands.decision_publish(
        **decision_args,
        research_validation_calculation_id=research["validation"]["calculation_id"],
    )

    revision = decision["revision"]
    assert decision["decision"]["status"] == "issued"
    assert revision["context_refs"]["investor_revision_id"] == investor["id"]
    assert revision["context_refs"]["mandate_revision_id"] == mandate["id"]
    assert revision["metadata"]["risk_calculation_id"] == risk["calculation_id"]
    assert revision["metadata"]["research_validation_calculation_id"] == research["validation"]["calculation_id"]
    assert revision["metadata"]["automatic_trade"] is False
    assert companion.financial.ledger_list() == ledger_before
