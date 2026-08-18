from __future__ import annotations

from copy import deepcopy
from datetime import date as date_value, timedelta

import pytest

from companion.quant_runtime import (
    NativeQuantRuntime,
    QuantContractError,
    ReferenceCalculator,
    canonical_hash,
    quant_compute_io_guard,
    verify_artifact_hash,
)


def target(
    runtime: NativeQuantRuntime,
    *,
    date: str,
    universe: list[str],
    cash_weight: str = "0",
    eligibility: dict | None = None,
):
    return runtime.equal_weight(
        as_of=(date_value.fromisoformat(date) - timedelta(days=1)).isoformat(),
        effective_on=date,
        dataset_snapshot_id="snapshot-golden-1",
        strategy_version_id="strategy-golden-1",
        universe=universe,
        cash_weight=cash_weight,
        eligibility=eligibility,
    )


def test_health_is_native_isolated_and_does_not_load_qlib():
    health = NativeQuantRuntime().health()

    assert health["status"] == "healthy"
    assert health["runtime"] == "native"
    assert health["compute_io_guard"] is True
    assert health["external_dependencies"] == []
    assert health["qlib"] == {"required": False, "loaded": False, "status": "not_used"}
    assert not any(health["boundaries"].values())
    assert health["contracts"]["native_experiment_bundle"] == "investment-companion/experiment-bundle/v1"
    assert health["contracts"]["research_experiment_bundle"] == "investment-companion.experiment-bundle/v3"


def test_quant_compute_guard_denies_external_io(tmp_path):
    with pytest.raises(PermissionError,match="denied external I/O: open"):
        with quant_compute_io_guard():
            (tmp_path/"forbidden").read_bytes()


def test_equal_weight_contract_and_hash_are_order_independent():
    runtime = NativeQuantRuntime()
    first = runtime.equal_weight(
        as_of="2026-01-05",
        effective_on="2026-01-06",
        dataset_snapshot_id="snapshot-1",
        strategy_version_id="strategy-1",
        universe=["asset-c", "asset-a", "asset-b"],
        eligibility={"asset-c": {"eligible": False, "reasons": ["suspended"]}},
        cash_weight="0.1",
    )
    second = runtime.equal_weight(
        as_of="2026-01-05",
        effective_on="2026-01-06",
        dataset_snapshot_id="snapshot-1",
        strategy_version_id="strategy-1",
        universe=["asset-b", "asset-c", "asset-a"],
        eligibility={"asset-c": {"reasons": ["suspended"], "eligible": False}},
        cash_weight="0.10",
    )

    assert first == second
    assert first["weights"] == [
        {"asset_id": "asset-a", "weight": "0.45"},
        {"asset_id": "asset-b", "weight": "0.45"},
    ]
    assert first["cash_weight"] == "0.1"
    assert first["denominator"][2]["exclusion_reasons"] == ["suspended"]
    assert verify_artifact_hash(first)
    assert canonical_hash({"b": 2, "a": 1}) == canonical_hash({"a": 1, "b": 2})


def test_cross_sectional_momentum_is_pit_bounded_ranked_and_deterministic():
    runtime = NativeQuantRuntime()
    bars = [
        {"date": "2026-01-01", "asset_id": "A", "close": "10"},
        {"date": "2026-01-02", "asset_id": "A", "close": "11"},
        {"date": "2026-01-03", "asset_id": "A", "close": "12"},
        {"date": "2026-01-01", "asset_id": "B", "close": "20"},
        {"date": "2026-01-02", "asset_id": "B", "close": "18"},
        {"date": "2026-01-03", "asset_id": "B", "close": "22"},
        {"date": "2026-01-04", "asset_id": "B", "close": "999"},  # future: invisible
        {"date": "2026-01-01", "asset_id": "C", "close": "5"},
        {"date": "2026-01-03", "asset_id": "C", "close": "6"},
    ]
    kwargs = dict(
        as_of="2026-01-03",
        effective_on="2026-01-04",
        dataset_snapshot_id="snapshot-1",
        strategy_version_id="momentum-1",
        universe=["C", "B", "A"],
        lookback_sessions=2,
        top_k=1,
    )
    first = runtime.cross_sectional_momentum(bars=bars, **kwargs)
    second = runtime.cross_sectional_momentum(bars=list(reversed(bars)), **kwargs)

    assert first == second
    assert first["weights"] == [{"asset_id": "A", "weight": "1"}]
    denominator = {row["asset_id"]: row for row in first["denominator"]}
    assert denominator["A"]["score"] == "0.2"
    assert denominator["A"]["rank"] == 1
    assert denominator["B"]["score"] == "0.1"
    assert denominator["C"]["exclusion_reasons"] == ["insufficient_history"]
    assert first["effective_on"] == "2026-01-04"


def test_simulation_applies_lots_minimum_commission_stamp_duty_and_slippage():
    runtime = NativeQuantRuntime()
    buy = target(runtime, date="2026-01-02", universe=["A"], cash_weight="0.9")
    sell = target(runtime, date="2026-01-03", universe=["A"], cash_weight="1")
    bars = [
        {"date": "2026-01-02", "asset_id": "A", "close": "10"},
        {"date": "2026-01-03", "asset_id": "A", "close": "10"},
    ]
    reality = {
        "commission_rate": "0.0003",
        "minimum_commission": "5",
        "sell_stamp_duty_rate": "0.001",
        "slippage_bps": "10",
    }

    result = runtime.simulate(
        bars=bars,
        target_portfolios=[buy, sell],
        initial_cash="10000",
        reality_spec=reality,
    )

    assert [fill["quantity"] for fill in result["fills"]] == [100, 100]
    assert result["fills"][0] == {
        "date": "2026-01-02",
        "target_hash": buy["artifact_hash"],
        "asset_id": "A",
        "side": "buy",
        "quantity": 100,
        "quote_price": "10",
        "execution_price": "10.01",
        "notional": "1001",
        "commission": "5",
        "stamp_duty": "0",
        "total_fees": "5",
        "simulated": True,
    }
    assert result["fills"][1]["execution_price"] == "9.99"
    assert result["fills"][1]["notional"] == "999"
    assert result["fills"][1]["commission"] == "5"
    assert result["fills"][1]["stamp_duty"] == "1"
    assert result["final_cash"] == "9987"
    assert result["final_holdings"] == []
    assert result["daily"][0]["holdings"][0]["quantity"] % 100 == 0
    assert verify_artifact_hash(result)


def test_simulation_applies_frozen_dividend_and_split_before_trading():
    runtime=NativeQuantRuntime()
    liquidate=target(runtime,date="2026-01-03",universe=["A"],cash_weight="1")
    result=runtime.simulate(
        bars=[
            {"date":"2026-01-02","asset_id":"A","close":"5"},
            {"date":"2026-01-03","asset_id":"A","close":"5"},
        ],
        target_portfolios=[liquidate],
        initial_cash="0",
        initial_positions={"A":100},
        reality_spec={"commission_rate":"0","minimum_commission":"0","sell_stamp_duty_rate":"0","cash_dividend_tax_rate":"0.1"},
        corporate_actions=[
            {"action_id":"div-1","asset_id":"A","date":"2026-01-02","action_type":"cash_dividend","cash_per_share":"1"},
            {"action_id":"split-1","asset_id":"A","date":"2026-01-02","action_type":"split","split_ratio":"2"},
        ],
    )
    assert result["corporate_events"]==[
        {"date":"2026-01-02","action_id":"div-1","asset_id":"A","action_type":"cash_dividend","quantity":100,"gross_cash":"100","tax":"10","net_cash":"90"},
        {"date":"2026-01-02","action_id":"split-1","asset_id":"A","action_type":"split","before_quantity":100,"after_quantity":200,"split_ratio":"2"},
    ]
    assert result["fills"][0]["quantity"]==200
    assert result["final_cash"]=="1090" and result["final_holdings"]==[]
    assert verify_artifact_hash(result)
    reference=ReferenceCalculator().simulate(
        bars=[
            {"date":"2026-01-02","asset_id":"A","close":"5"},
            {"date":"2026-01-03","asset_id":"A","close":"5"},
        ],
        target_portfolios=[liquidate],
        initial_cash="0",
        initial_positions={"A":100},
        reality_spec={"commission_rate":"0","minimum_commission":"0","sell_stamp_duty_rate":"0","cash_dividend_tax_rate":"0.1"},
        corporate_actions=[
            {"action_id":"div-1","asset_id":"A","date":"2026-01-02","action_type":"cash_dividend","cash_per_share":"1"},
            {"action_id":"split-1","asset_id":"A","date":"2026-01-02","action_type":"split","split_ratio":"2"},
        ],
    )
    assert reference["corporate_events"]==result["corporate_events"]
    assert ReferenceCalculator().compare_daily(result,reference)["matched"] is True

    with pytest.raises(QuantContractError, match="outside the explicit simulation sessions"):
        runtime.simulate(
            bars=[{"date":"2026-01-02","asset_id":"A","close":"5"}],
            target_portfolios=[],
            initial_cash="10000",
            corporate_actions=[
                {"action_id":"outside-window","asset_id":"A","date":"2026-01-09","action_type":"cash_dividend","cash_per_share":"0.1"}
            ],
        )


def test_t_plus_one_suspension_and_price_limits_keep_auditable_unfilled_reasons():
    runtime = NativeQuantRuntime()
    zero = target(runtime, date="2026-01-02", universe=["LOCKED", "SUSP", "LOW"], cash_weight="1")
    buy = target(runtime, date="2026-01-02", universe=["UP"], cash_weight="0")
    bars = [
        {"date": "2026-01-02", "asset_id": "LOCKED", "close": "10"},
        {"date": "2026-01-02", "asset_id": "SUSP", "close": "10", "suspended": True},
        {"date": "2026-01-02", "asset_id": "LOW", "close": "10", "at_lower_limit": True},
        {"date": "2026-01-02", "asset_id": "UP", "close": "10", "at_upper_limit": True},
    ]
    result = runtime.simulate(
        bars=bars,
        target_portfolios=[zero, buy],
        initial_cash="10000",
        initial_lots=[
            {"asset_id": "LOCKED", "quantity": 100, "acquired_on": "2026-01-02"},
            {"asset_id": "SUSP", "quantity": 100, "acquired_on": "2026-01-01"},
            {"asset_id": "LOW", "quantity": 100, "acquired_on": "2026-01-01"},
        ],
    )

    by_asset = {row["asset_id"]: row for row in result["unfilled"]}
    assert by_asset["LOCKED"]["reason"] == "t_plus_one_locked"
    assert by_asset["SUSP"]["reason"] == "suspended"
    assert by_asset["LOW"]["reason"] == "lower_limit_sell_blocked"
    assert by_asset["UP"]["reason"] == "upper_limit_buy_blocked"
    assert result["fills"] == []


def test_reference_calculator_matches_golden_case_daily_and_detects_tampering():
    runtime = NativeQuantRuntime()
    reference = ReferenceCalculator()
    first = target(runtime, date="2026-01-02", universe=["A", "B"])
    second = target(
        runtime,
        date="2026-01-03",
        universe=["A", "B"],
        eligibility={"B": False},
        cash_weight="0.5",
    )
    bars = [
        {"date": "2026-01-02", "asset_id": "A", "close": "10"},
        {"date": "2026-01-02", "asset_id": "B", "close": "20"},
        {"date": "2026-01-03", "asset_id": "A", "close": "11"},
        {"date": "2026-01-03", "asset_id": "B", "close": "19"},
    ]
    args = {
        "bars": bars,
        "target_portfolios": [first, second],
        "initial_cash": "100000",
        "reality_spec": {
            "commission_rate": "0.001",
            "minimum_commission": "5",
            "sell_stamp_duty_rate": "0.001",
        },
    }
    primary_run = runtime.simulate(**args)
    reference_run = reference.simulate(**args)

    assert reference.compare_daily(primary_run, reference_run)["matched"] is True
    repeated = runtime.simulate(
        **{**args, "bars": list(reversed(bars)), "target_portfolios": [first, second]}
    )
    assert repeated == primary_run
    changed = deepcopy(reference_run)
    changed["daily"][0]["cash"] = "0"
    comparison = reference.compare_daily(primary_run, changed)
    assert comparison["matched"] is False
    assert comparison["differences"][0]["field"] == "cash"


def test_invalid_or_tampered_contract_fails_closed():
    runtime = NativeQuantRuntime()
    valid = target(runtime, date="2026-01-02", universe=["A"])
    tampered = deepcopy(valid)
    tampered["weights"][0]["weight"] = "0.5"

    with pytest.raises(QuantContractError, match="artifact_hash"):
        runtime.simulate(
            bars=[{"date": "2026-01-02", "asset_id": "A", "close": "10"}],
            target_portfolios=[tampered],
            initial_cash="1000",
        )
