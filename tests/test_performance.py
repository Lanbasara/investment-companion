from __future__ import annotations

from datetime import timedelta

from companion.core import Companion
from companion.timeutil import iso, utc_now


def test_performance_calculates_cash_flow_adjusted_gain_benchmark_and_drawdown(tmp_path):
    companion = Companion(tmp_path, gate_scope="test_fixture")
    companion.initialize()
    account = companion.financial.account_create("Performance fixture", "CNY")
    asset = companion.financial.asset_upsert(
        "stock", "Performance Asset", "CNY", {"ts_code": "600001.SH"}
    )
    start = utc_now() - timedelta(days=10)
    end = utc_now()
    opening = companion.financial.ledger_add(
        account_id=account["id"],
        entry_type="opening_balance",
        occurred_at=iso(start - timedelta(days=1)),
        amount="1000",
        currency="CNY",
        source="performance-fixture",
    )
    trade = companion.financial.ledger_add(
        account_id=account["id"],
        entry_type="trade",
        asset_id=asset["id"],
        occurred_at=iso(start - timedelta(hours=12)),
        quantity="50",
        price="10",
        amount="-500",
        currency="CNY",
        source="performance-fixture",
    )
    companion.financial.ledger_confirm(opening["id"])
    companion.financial.ledger_confirm(trade["id"])

    result = companion.performance.calculate_period(
        account_id=account["id"],
        period_start=iso(start),
        period_end=iso(end),
        start_prices={asset["id"]: "10"},
        end_prices={asset["id"]: "12"},
        benchmark_start_value="100",
        benchmark_end_value="105",
        valuation_points=[{"at": iso(start + timedelta(days=5)), "value": "900"}],
        source_refs=["fixture-price-series", "fixture-benchmark-series"],
        attribution_refs=["fixture-decision-revision", "fixture-strategy-version"],
    )
    assert result["start_value"] == "1000"
    assert result["end_value"] == "1100"
    assert result["modified_dietz_return"] == "0.1"
    assert result["benchmark_return"] == "0.05"
    assert result["excess_return"] == "0.05"
    assert result["maximum_drawdown"] == "0.1"
    assert result["warnings"] == []
    assert companion.financial.calculation_get(result["calculation_id"])["outputs"][
        "investment_gain_after_external_flows"
    ] == "100"


def test_performance_removes_external_cash_flow_from_investment_gain(tmp_path):
    companion = Companion(tmp_path, gate_scope="test_fixture")
    companion.initialize()
    account = companion.financial.account_create("Cash-flow fixture", "CNY")
    start = utc_now() - timedelta(days=10)
    end = utc_now()
    opening = companion.financial.ledger_add(
        account_id=account["id"],
        entry_type="opening_balance",
        occurred_at=iso(start - timedelta(days=1)),
        amount="1000",
        currency="CNY",
        source="performance-fixture",
    )
    deposit = companion.financial.ledger_add(
        account_id=account["id"],
        entry_type="cash_deposit",
        occurred_at=iso(start + timedelta(days=5)),
        amount="500",
        currency="CNY",
        source="performance-fixture",
    )
    companion.financial.ledger_confirm(opening["id"])
    companion.financial.ledger_confirm(deposit["id"])
    revisions_before = companion.cognition.object_list("review")

    result = companion.performance.calculate_period(
        account_id=account["id"],
        period_start=iso(start),
        period_end=iso(end),
        start_prices={},
        end_prices={},
    )
    assert result["end_value"] == "1500"
    assert result["net_external_flow"] == "500"
    assert result["investment_gain_after_external_flows"] == "0"
    assert result["modified_dietz_return"] == "0"
    assert companion.cognition.object_list("review") == revisions_before
