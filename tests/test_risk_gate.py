from __future__ import annotations

from datetime import timedelta

from companion.core import Companion
from companion.timeutil import iso, utc_now


def setup_portfolio(tmp_path):
    companion = Companion(tmp_path, gate_scope="test_fixture")
    companion.initialize()
    account = companion.financial.account_create("Risk fixture", "CNY")
    asset = companion.financial.asset_upsert(
        "stock", "Risk Asset", "CNY", {"ts_code": "600000.SH"}
    )
    opening = companion.financial.ledger_add(
        account_id=account["id"],
        entry_type="opening_balance",
        occurred_at=iso(utc_now() - timedelta(days=1)),
        amount="100000",
        currency="CNY",
        source="risk-fixture",
    )
    companion.financial.ledger_confirm(opening["id"])
    market = companion.financial.market_add(
        asset["id"], "close", "10", iso(), "risk-fixture", "healthy", "CNY"
    )
    return companion, account, asset, market


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


def test_risk_gate_passes_a_bounded_trade_without_changing_ledger(tmp_path):
    companion, account, asset, market = setup_portfolio(tmp_path)
    ledger_before = companion.financial.confirmed_ledger_hash()
    result = companion.risk.assess_trade(
        as_of=iso(),
        account_id=account["id"],
        asset_id=asset["id"],
        quantity="100",
        price="10",
        fee="5",
        mandate={
            "minimum_cash": {"CNY": "20000"},
            "max_single_position_weight": "0.20",
            "max_turnover": "0.05",
            "max_participation_rate": "0.10",
        },
        reality_spec=reality(),
        market_snapshot_id=market["id"],
        max_market_age_seconds=600,
        valid_until=iso(utc_now() + timedelta(minutes=30)),
        price_range={"min": "9.5", "max": "10.5"},
        average_daily_amount="100000",
    )
    assert result["status"] == "pass"
    assert result["blocked"] is False
    assert result["metrics"]["post_trade_position_weight"] == "0.01000050002500125006250312515625781"
    assert companion.financial.confirmed_ledger_hash() == ledger_before


def test_risk_gate_combines_mandate_concentration_liquidity_and_lot_vetoes(tmp_path):
    companion, account, asset, market = setup_portfolio(tmp_path)
    result = companion.risk.assess_trade(
        as_of=iso(),
        account_id=account["id"],
        asset_id=asset["id"],
        quantity="3050",
        price="10",
        fee="5",
        mandate={
            "prohibited_asset_ids": [asset["id"]],
            "max_single_position_weight": "0.20",
            "max_turnover": "0.10",
            "max_participation_rate": "0.05",
        },
        reality_spec=reality(),
        market_snapshot_id=market["id"],
        max_market_age_seconds=600,
        average_daily_amount="100000",
    )
    rules = {item["rule"] for item in result["violations"]}
    assert result["status"] == "blocked"
    assert {
        "prohibited_asset",
        "buy_lot_size",
        "max_single_position_weight",
        "max_turnover",
        "max_participation_rate",
    } <= rules


def test_risk_gate_fails_closed_when_required_market_or_liquidity_data_is_missing(tmp_path):
    companion, account, asset, _market = setup_portfolio(tmp_path)
    result = companion.risk.assess_trade(
        as_of=iso(),
        account_id=account["id"],
        asset_id=asset["id"],
        quantity="100",
        price="10",
        mandate={"max_participation_rate": "0.10"},
        reality_spec=reality(),
        max_market_age_seconds=600,
    )
    rules = {item["rule"] for item in result["violations"]}
    assert {"market_snapshot_missing", "liquidity_data_missing"} <= rules


def test_bounded_action_requires_confirmed_policy_and_enforces_smaller_caps(tmp_path):
    companion, account, asset, market = setup_portfolio(tmp_path)
    common = {
        "as_of": iso(),
        "account_id": account["id"],
        "asset_id": asset["id"],
        "quantity": "100",
        "price": "10",
        "fee": "5",
        "mandate": {},
        "reality_spec": reality(),
        "market_snapshot_id": market["id"],
        "max_market_age_seconds": 600,
        "valid_until": iso(utc_now() + timedelta(minutes=30)),
        "price_range": {"min": "9.5", "max": "10.5"},
        "action_tier": "bounded",
    }
    missing = companion.risk.assess_trade(**common)
    assert "bounded_action_policy_missing_or_invalid" in {
        item["rule"] for item in missing["violations"]
    }

    passed = companion.risk.assess_trade(
        **common,
        bounded_action_policy={
            "enabled": True,
            "allowed_asset_types": ["stock", "etf"],
            "allowed_execution_plan_types": ["priced_buy", "priced_sell", "bracket_exit"],
            "max_trade_weight": "0.011",
            "max_post_trade_weight": "0.05",
            "max_validity_sessions": 20,
            "max_active_bounded_actions": 2,
        },
        program_revision_id="programrev_fixture",
        validity_sessions=5,
    )
    assert passed["status"] == "pass"
    calculation = companion.financial.calculation_get(passed["calculation_id"])
    assert calculation["assumptions"]["action_tier"] == "bounded"

    oversized_sell = companion.risk.assess_trade(
        **{**common, "quantity": "-200"},
        bounded_action_policy={
            "enabled": True,
            "allowed_asset_types": ["stock", "etf"],
            "allowed_execution_plan_types": ["priced_buy", "priced_sell", "bracket_exit"],
            "max_trade_weight": "0.011",
            "max_post_trade_weight": "0.05",
            "max_validity_sessions": 20,
            "max_active_bounded_actions": 2,
        },
        program_revision_id="programrev_fixture",
        validity_sessions=5,
    )
    assert "bounded_max_trade_weight" in {
        item["rule"] for item in oversized_sell["violations"]
    }

    distant_deadline = companion.risk.assess_trade(
        **{**common, "valid_until": iso(utc_now() + timedelta(days=365))},
        bounded_action_policy={
            "enabled": True,
            "allowed_asset_types": ["stock", "etf"],
            "allowed_execution_plan_types": ["priced_buy", "priced_sell", "bracket_exit"],
            "max_trade_weight": "0.011",
            "max_post_trade_weight": "0.05",
            "max_validity_sessions": 20,
            "max_active_bounded_actions": 2,
        },
        program_revision_id="programrev_fixture",
        validity_sessions=5,
    )
    assert "bounded_deadline_exceeds_session_envelope" in {
        item["rule"] for item in distant_deadline["violations"]
    }
