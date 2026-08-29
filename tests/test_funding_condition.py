from __future__ import annotations

from datetime import timedelta

from companion.core import Companion
from companion.timeutil import iso, utc_now


def reality() -> dict:
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


def setup_cash_blocked_candidate(tmp_path):
    companion = Companion(tmp_path, gate_scope="test_fixture")
    companion.initialize()
    checked_time = utc_now()
    checked_at = iso(checked_time)
    valid_until = iso(checked_time + timedelta(days=1))
    mandate = companion.cognition.context_create(
        "mandate",
        {"hard_constraints": {"minimum_cash": {"CNY": "100"}}},
        reason="Funding Condition fixture",
    )
    companion.cognition.context_confirm(mandate["id"])
    account = companion.financial.account_create("Funding account", "CNY")
    opening = companion.financial.ledger_add(
        account_id=account["id"],
        entry_type="opening_balance",
        occurred_at=iso(checked_time - timedelta(days=2)),
        amount="1500",
        currency="CNY",
        source="funding-condition-fixture",
    )
    companion.financial.ledger_confirm(opening["id"])
    companion.financial.reconcile(
        account["id"],
        checked_at,
        {
            "cash": {"CNY": "1500"},
            "positions": {},
            "position_values": {},
            "position_total_by_currency": {"CNY": "0"},
            "total_by_currency": {"CNY": "1500"},
        },
        "funding-condition-statement",
    )
    asset = companion.financial.asset_upsert(
        "stock", "Funding Asset", "CNY", {"fixture": "funding-condition"}
    )
    market = companion.financial.market_add(
        asset["id"],
        "close",
        "8",
        checked_at,
        "funding-condition-fixture",
        "healthy",
        "CNY",
    )
    trade = {
        "action_tier": "standard",
        "as_of": checked_at,
        "account_id": account["id"],
        "asset_id": asset["id"],
        "quantity": "200",
        "price": "8",
        "reality_spec": reality(),
        "market_snapshot_id": market["id"],
        "max_market_age_seconds": 3600,
        "valid_until": valid_until,
        "price_range": {"min": "7", "max": "9"},
    }
    return companion, checked_time, account, asset, trade


def paths_by_type(condition: dict) -> dict[str, dict]:
    return {path["type"]: path for path in condition["paths"]}


def ledger_ids(companion: Companion) -> list[str]:
    return [item["id"] for item in companion.financial.ledger_list(limit=500)]


def test_action_plan_records_funding_range_costs_buffer_and_paths_without_ledger_writes(
    tmp_path,
):
    companion, _checked_time, account, asset, trade = setup_cash_blocked_candidate(
        tmp_path
    )
    before_ledger = ledger_ids(companion)

    plan = companion.investment_commands.action_plan(**trade)

    assert ledger_ids(companion) == before_ledger
    assert plan["risk"]["status"] == "blocked"
    assert {item["rule"] for item in plan["risk"]["violations"]} >= {
        "nonnegative_cash",
        "minimum_cash",
    }
    assert plan["eligible_for_decision"] is False
    assert plan["automatic_decision_or_execution"] is False
    assert plan["candidate_qualification"]["level"] == "preflight_ready"

    condition = plan["funding_condition"]
    assert condition is not None
    assert condition["schema"] == "investment-companion.funding-condition/v1"
    assert condition["account_id"] == account["id"]
    assert condition["asset_id"] == asset["id"]
    assert condition["direction"] == "buy"
    assert condition["candidate_qualification_calculation_id"] == plan[
        "candidate_qualification"
    ]["calculation_id"]
    assert condition["risk_calculation_id"] == plan["risk"]["calculation_id"]
    assert condition["candidate_quantity_domain"] == {
        "kind": "up_to_requested_quantity",
        "minimum": "0",
        "maximum": "200",
        "step": "100",
    }
    assert condition["price_range"] == {
        "minimum": "7",
        "reference": "8",
        "maximum": "9",
    }
    assert condition["confirmed_cash"]["amount"] == "1500"
    assert condition["confirmed_cash"]["cash_safety_buffer"] == "100"
    assert condition["confirmed_cash"]["spendable_amount"] == "1400"
    assert condition["cost_range"]["notional"] == {
        "minimum": "1400",
        "at_reference_price": "1600",
        "maximum": "1800",
    }
    assert condition["cost_range"]["commission"] == {
        "minimum": "5",
        "at_reference_price": "5",
        "maximum": "5",
    }
    assert condition["cost_range"]["tax"] == {
        "minimum": "0",
        "at_reference_price": "0",
        "maximum": "0",
    }
    assert condition["required_additional_cash"] == {
        "minimum": "5",
        "at_reference_price": "205",
        "maximum": "405",
    }

    paths = paths_by_type(condition)
    assert set(paths) == {
        "additional_funding",
        "reduce_quantity",
        "confirmed_disposal_proceeds",
    }
    assert paths["additional_funding"]["amount_range"] == condition[
        "required_additional_cash"
    ]
    assert paths["reduce_quantity"]["state"] == (
        "available_as_non_actionable_alternative"
    )
    assert paths["reduce_quantity"]["quantity_range"] == {
        "minimum": "100",
        "maximum_across_price_range": "100",
        "maximum_at_minimum_price": "100",
        "maximum_at_reference_price": "100",
        "maximum_at_maximum_price": "100",
        "step": "100",
    }
    assert paths["confirmed_disposal_proceeds"][
        "required_net_proceeds_range"
    ] == condition["required_additional_cash"]
    for path in paths.values():
        assert path["assumptions"]["confirmed_cash_only"] is True
        assert path["assumptions"]["planned_deposits_counted_as_cash"] is False
        assert path["confirmation_requirements"]
        assert path["validity"]["supports_current_planning"] is True
        assert [item["code"] for item in path["required_reruns"]] == [
            "rerun_portfolio_qualification",
            "rerun_risk_gate",
            "rerun_action_plan",
        ]
        assert path["validity"]["valid_until"] == trade["valid_until"]
    assert condition["automatic_decision_or_execution"] is False

    frozen = companion.financial.calculation_get(condition["calculation_id"])
    assert frozen["kind"] == "funding_condition"
    assert frozen["outputs"] == {
        key: value for key, value in condition.items() if key != "calculation_id"
    }
    assert frozen["inputs"]["confirmed_portfolio_calculation_id"] == condition[
        "confirmed_cash"
    ]["portfolio_calculation_id"]
    repeated = companion.investment_commands.action_plan(**trade)
    assert repeated["funding_condition"]["calculation_id"] == condition[
        "calculation_id"
    ]
    assert ledger_ids(companion) == before_ledger


def test_planned_deposit_and_expected_sale_do_not_fund_risk_until_ledger_confirmation(
    tmp_path,
):
    companion, checked_time, account, asset, trade = setup_cash_blocked_candidate(
        tmp_path
    )
    initial = companion.investment_commands.action_plan(**trade)
    condition = initial["funding_condition"]
    deposit = companion.financial.ledger_add(
        account_id=account["id"],
        entry_type="cash_deposit",
        occurred_at=iso(checked_time),
        amount="500",
        currency="CNY",
        source="planned-deposit-fixture",
    )
    expected_sale = companion.financial.ledger_add(
        account_id=account["id"],
        entry_type="trade",
        asset_id=asset["id"],
        occurred_at=iso(checked_time),
        quantity="-100",
        price="8",
        amount="800",
        currency="CNY",
        source="expected-sale-fixture",
    )
    reported_ledger = ledger_ids(companion)

    still_blocked = companion.investment_commands.action_plan(**trade)

    assert ledger_ids(companion) == reported_ledger
    assert deposit["status"] == "needs_confirmation"
    assert expected_sale["status"] == "needs_confirmation"
    assert still_blocked["risk"]["status"] == "blocked"
    assert still_blocked["risk"]["precise_action_eligible"] is False
    assert still_blocked["candidate_qualification"]["level"] == "range_ready"
    assert still_blocked["funding_condition"]["confirmed_cash"]["amount"] == "1500"
    assert still_blocked["funding_condition"]["required_additional_cash"] == {
        "minimum": "5",
        "at_reference_price": "205",
        "maximum": "405",
    }
    assert paths_by_type(still_blocked["funding_condition"])[
        "confirmed_disposal_proceeds"
    ]["state"] == "requires_confirmed_ledger_entry"

    companion.financial.ledger_confirm(deposit["id"])
    requalified = companion.investment_commands.action_plan(**trade)

    assert requalified["risk"]["status"] == "pass"
    assert requalified["risk"]["precise_action_eligible"] is False
    assert requalified["funding_condition"] is None
    assert companion.funding_condition.current_status(
        condition["calculation_id"], as_of=iso(checked_time + timedelta(minutes=1))
    )["status"] == "facts_drifted"


def test_funding_condition_expires_and_portfolio_or_market_drift_preserves_history(
    tmp_path,
):
    companion, checked_time, account, asset, trade = setup_cash_blocked_candidate(
        tmp_path
    )
    plan = companion.investment_commands.action_plan(**trade)
    condition = plan["funding_condition"]
    frozen = companion.financial.calculation_get(condition["calculation_id"])[
        "outputs"
    ]
    assert companion.funding_condition.current_status(
        condition["calculation_id"], as_of=iso(checked_time + timedelta(seconds=1))
    )["status"] == "current"
    expired_plan = companion.investment_commands.action_plan(
        **{**trade, "as_of": trade["valid_until"]}
    )
    assert expired_plan["funding_condition"]["validity"] == {
        "status": "expired_at_as_of",
        "valid_until": trade["valid_until"],
        "invalidate_on": [
            "confirmed_ledger_change",
            "portfolio_qualification_change",
            "related_market_snapshot_change",
            "mandate_change",
            "candidate_change",
            "fee_or_tax_assumption_change",
            "expiry",
        ],
        "supports_current_planning": False,
    }

    later_time = checked_time + timedelta(minutes=1)
    newer_market = companion.financial.market_add(
        asset["id"],
        "close",
        "8.5",
        iso(later_time),
        "funding-condition-market-drift",
        "healthy",
        "CNY",
    )
    market_drift = companion.funding_condition.current_status(
        condition["calculation_id"], as_of=iso(later_time)
    )
    assert market_drift["status"] == "facts_drifted"
    assert market_drift["supports_current_planning"] is False

    refreshed_trade = {
        **trade,
        "as_of": iso(later_time),
        "price": "8.5",
        "price_range": {"min": "7.5", "max": "9.5"},
        "market_snapshot_id": newer_market["id"],
    }
    refreshed = companion.investment_commands.action_plan(**refreshed_trade)[
        "funding_condition"
    ]
    fee = companion.financial.ledger_add(
        account_id=account["id"],
        entry_type="fee",
        occurred_at=iso(later_time + timedelta(minutes=1)),
        amount="-1",
        currency="CNY",
        source="funding-condition-portfolio-drift",
    )
    companion.financial.ledger_confirm(fee["id"])
    portfolio_drift = companion.funding_condition.current_status(
        refreshed["calculation_id"],
        as_of=iso(later_time + timedelta(minutes=1)),
    )
    assert portfolio_drift["status"] == "facts_drifted"
    assert portfolio_drift["supports_current_planning"] is False

    expired = companion.funding_condition.current_status(
        refreshed["calculation_id"], as_of=iso(checked_time + timedelta(days=2))
    )
    assert expired["status"] == "expired"
    assert expired["supports_current_planning"] is False
    assert companion.financial.calculation_get(condition["calculation_id"])[
        "outputs"
    ] == frozen
