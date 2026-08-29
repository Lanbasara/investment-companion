from __future__ import annotations

from datetime import timedelta

from companion.core import Companion
from companion.timeutil import iso, utc_now


def reality():
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


def validation_spec():
    return {
        "falsifiers": ["the expected driver reverses"],
        "counterevidence": {
            "searched": ["issuer evidence", "independent market evidence"],
            "findings": [],
        },
        "applicability": {
            "horizon": "one month",
            "conditions": ["normal A-share trading"],
            "excluded_conditions": ["suspension"],
        },
        "cost_assumptions": {
            "commission": "RealitySpec commission",
            "tax": "A-share sell stamp duty",
            "slippage": "execution price range",
        },
        "max_evidence_age_days": 30,
    }


def test_golden_research_to_real_performance_to_inert_change_proposal(tmp_path):
    companion = Companion(tmp_path, gate_scope="test_fixture")
    companion.initialize()
    now = utc_now()
    period_start = now - timedelta(days=2)
    decision_at = now - timedelta(days=1)
    trade_at = decision_at + timedelta(hours=1)
    account = companion.financial.account_create("Golden account", "CNY")
    asset = companion.financial.asset_upsert(
        "stock", "Golden asset", "CNY", {"ts_code": "600003.SH"}
    )
    opening = companion.financial.ledger_add(
        account_id=account["id"],
        entry_type="opening_balance",
        occurred_at=iso(period_start - timedelta(days=1)),
        amount="10000",
        currency="CNY",
        source="golden-fixture",
    )
    companion.financial.ledger_confirm(opening["id"])
    for context_type, content in (
        ("investor", {"objective": "capital growth"}),
        (
            "mandate",
            {
                "minimum_cash": {"CNY": "5000"},
                "max_single_position_weight": "0.2",
            },
        ),
    ):
        draft = companion.investment_commands.context_draft(
            context_type=context_type,
            content=content,
            reason="golden fixture",
        )
        companion.investment_commands.context_confirm(revision_id=draft["id"])

    evidence = companion.data.manifest_publish(
        kind="golden_official_evidence",
        schema_version="golden-evidence/v1",
        manifest={
            "asset_id": asset["id"],
            "source": "golden issuer",
            "source_group": "issuer",
            "first_known_at": iso(decision_at),
            "observed_at": iso(decision_at),
        },
    )
    independent_evidence = companion.data.manifest_publish(
        kind="golden_market_evidence",
        schema_version="golden-evidence/v1",
        manifest={
            "asset_id": asset["id"],
            "source": "golden market provider",
            "source_group": "market-data",
            "first_known_at": iso(decision_at),
            "observed_at": iso(decision_at),
        },
    )
    evidence_ids = [evidence["id"], independent_evidence["id"]]
    research = companion.investment_commands.research_publish(
        subject={"asset_id": asset["id"]},
        content="# Thesis\nA falsifiable golden-workflow hypothesis.",
        evidence_manifest_ids=evidence_ids,
        knowledge_cutoff=iso(decision_at),
        validation_spec=validation_spec(),
    )
    valid_until = iso(now + timedelta(days=1))
    companion.financial.reconcile(
        account["id"],
        iso(decision_at),
        {
            "cash": {"CNY": "10000"},
            "positions": {},
            "position_values": {},
            "position_total_by_currency": {"CNY": "0"},
            "total_by_currency": {"CNY": "10000"},
        },
        "golden-decision-qualification",
    )
    market = companion.financial.market_add(
        asset["id"],
        "close",
        "10",
        iso(decision_at),
        "golden-fixture",
        "healthy",
        "CNY",
    )
    risk = companion.investment_commands.risk_assess(
        as_of=iso(decision_at),
        account_id=account["id"],
        asset_id=asset["id"],
        quantity="100",
        price="10",
        reality_spec=reality(),
        market_snapshot_id=market["id"],
        max_market_age_seconds=2 * 24 * 60 * 60,
        valid_until=valid_until,
        price_range={"min": "9.8", "max": "10.2"},
    )
    decision = companion.investment_commands.decision_publish(
        subject={"asset_id": asset["id"]},
        content="# Decision\nA bounded manual purchase beats the frozen alternatives.",
        decision_kind="action",
        account_id=account["id"],
        as_of=iso(decision_at),
        knowledge_cutoff=iso(decision_at),
        valid_until=valid_until,
        thesis_revision_ids=[research["revision"]["id"]],
        evidence_manifest_ids=evidence_ids,
        invalidators=["price leaves range", "mandate changes", "thesis falsified"],
        no_action={"choice": "hold cash", "expected_effect": "avoid position risk"},
        alternatives=[{"choice": "hold cash"}, {"choice": "buy fewer shares"}],
        risk_calculation_id=risk["calculation_id"],
        research_validation_calculation_id=research["validation"]["calculation_id"],
        portfolio_qualification_calculation_id=risk["portfolio_qualification"][
            "calculation_id"
        ],
    )

    pending_trade = companion.investment_commands.transaction_record(
        account_id=account["id"],
        entry_type="trade",
        asset_id=asset["id"],
        occurred_at=iso(trade_at),
        quantity="100",
        price="10",
        amount="-1000",
        fee="5",
        currency="CNY",
        source="user-reported-fill",
        metadata={"decision_revision_id": decision["revision"]["id"]},
    )
    before_confirmation = companion.financial.portfolio_state(iso(now), account["id"])
    assert before_confirmation["positions"] == []
    companion.investment_commands.transaction_confirm(entry_id=pending_trade["id"])

    performance = companion.investment_commands.performance_calculate(
        account_id=account["id"],
        period_start=iso(period_start),
        period_end=iso(now),
        start_prices={},
        end_prices={asset["id"]: "12"},
        benchmark_start_value="100",
        benchmark_end_value="101",
        source_refs=evidence_ids,
        attribution_refs=[decision["revision"]["id"], research["revision"]["id"]],
    )
    strategies_before = companion.research.strategy_list()
    review = companion.investment_commands.review_publish(
        subject={"asset_id": asset["id"], "decision_revision_id": decision["revision"]["id"]},
        content="# Review\nThe result is positive, but one observation is not validation.",
        conclusion="revise",
        calculation_ids=[performance["calculation_id"]],
        source_refs=evidence_ids,
        proposed_changes=[
            {
                "target_type": "thesis",
                "target_id": research["thesis"]["id"],
                "change": "collect a larger forward sample",
                "reason": "one realized outcome is insufficient",
                "validation_required": True,
            }
        ],
        knowledge_cutoff=iso(now),
    )

    portfolio = companion.financial.portfolio_state(
        iso(now), account["id"], {asset["id"]: "12"}
    )
    decision_context = companion.investment.decision_context()
    evaluation_context = companion.investment.evaluation_context()
    assert risk["blocked"] is False
    assert research["validation"]["status"] == "eligible_for_decision"
    assert decision["revision"]["metadata"]["automatic_trade"] is False
    assert portfolio["positions"][0]["quantity"] == "100"
    assert performance["investment_gain_after_external_flows"] == "195"
    assert performance["modified_dietz_return"] == "0.0195"
    assert performance["excess_return"] == "0.0095"
    assert review["change_proposal"]["automatic_application"] is False
    assert decision_context["recent_decisions"][0]["current_revision"]["metadata"][
        "risk_calculation_id"
    ] == risk["calculation_id"]
    assert evaluation_context["performance"][0]["id"] == performance["calculation_id"]
    assert evaluation_context["reviews"][0]["current_revision"]["metadata"][
        "change_proposal"
    ]["automatic_application"] is False
    assert companion.research.strategy_list() == strategies_before
