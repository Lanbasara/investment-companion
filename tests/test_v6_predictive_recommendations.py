from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

import pytest

from companion.core import Companion, CompanionError
from companion.governance import GATE_CHECKLISTS
from companion.timeutil import iso, utc_now
from companion.v6_predictive_recommendations import FUND_CANDIDATE_HANDLER, FUND_HANDLER, FUND_SIGNAL_HANDLER, STOCK_HANDLER, V6_MODE


def pass_g0(companion: Companion) -> None:
    artifact = companion.data.manifest_publish(
        kind="fixture_evidence", schema_version="fixture/v1", manifest={"gate": "G0"}
    )
    evidence = companion.gates.evidence_publish(
        "G0",
        checks={key: True for key in GATE_CHECKLISTS["G0"]},
        artifacts=[artifact["id"]],
        unknowns=[],
        counterevidence=[],
        counterevidence_disposition={},
        code_version="test-fixture",
        scope="test_fixture",
    )
    companion.gates.assessment_record(
        gate="G0",
        status="go",
        evidence_manifest_id=evidence["id"],
        code_version="test-fixture",
        assessed_by="pytest",
        scope="test_fixture",
    )


def setup_v6(tmp_path: Path) -> Companion:
    companion = Companion(tmp_path, gate_scope="test_fixture")
    companion.initialize()
    pass_g0(companion)
    companion.jobs.feature_set("v4_jobs", True, reason="fixture deterministic jobs")
    companion.jobs.feature_set("v4_live_data_canary", True, reason="fixture ETF canary data")
    companion.jobs.feature_set(
        "v6_predictive_recommendations",
        True,
        config={
            "mode": V6_MODE,
            "program_id": "pytest-v6",
            "user_approval_ref": "pytest-explicit-approval",
            "started_at": iso(utc_now() - timedelta(minutes=1)),
        },
        reason="fixture V6 predictive research",
    )
    companion.v6_predictive.bootstrap(activate=True)
    return companion


def fund_universe(companion: Companion, *, task_line: str, asset_id: str) -> str:
    report = companion.v6_predictive.publish_fund_universe(
        program_id="pytest-v6",
        as_of=iso(),
        source_refs=["fixture-etf-basic", "fixture-product-document"],
        members=[
            {
                "asset_id": asset_id,
                "ts_code": asset_id.removeprefix("tushare:"),
                "name": "pytest ETF",
                "listing_venue": "SSE",
                "domestic_tradable": True,
                "classification": {"model_version": "pytest-etf-labels/1", "labels": [task_line], "status": "provisional", "evidence_refs": ["fixture-etf-basic"]},
                "product_evidence_ref": "fixture-product-document",
                "data_readiness": {"prices": "ready", "liquidity": "ready", "costs": "ready", "structure": "ready"},
            }
        ],
    )
    return report["id"]


def prediction(task_line: str, asset_id: str = "tushare:510300.SH", *, sample_status: str = "ready", fund_universe_manifest_id: str | None = None) -> dict:
    result = {
        "task_line": task_line,
        "asset_id": asset_id,
        "strategy_version_id": f"{task_line}-v1",
        "as_of": iso(),
        "horizon_sessions": 20,
        "expected_excess_return": "0.04",
        "downside_return": "-0.08",
        "all_in_cost": "0.005",
        "evidence_status": "ready",
        "sample_status": sample_status,
    }
    if task_line.startswith("fund_"):
        assert fund_universe_manifest_id is not None
        result["fund_universe_manifest_id"] = fund_universe_manifest_id
    return result


def thresholds() -> dict:
    return {"min_net_expected_excess_return": "0.01", "max_downside_return": "-0.10"}


def portfolio(*, conditions: list[str] | None = None) -> dict:
    return {
        "improves_portfolio": True,
        "constraints_satisfied": True,
        "alternative_beaten": True,
        "conditions": conditions or [],
    }


def test_v6_bootstrap_creates_separate_task_lines_and_monthly_feedback_review(tmp_path: Path):
    companion = setup_v6(tmp_path)
    result = companion.v6_predictive.bootstrap(activate=True)
    assert {item["handler"] for item in result["definitions"]} == {
        "research.v6_stock_forecast",
        "research.v6_stock_candidate_scan",
        "research.v6_stock_provisional_signal",
        "research.v6_fund_forecast",
        "research.v6_fund_candidate_scan",
        "research.v6_fund_provisional_signal",
        "data.v6_fund_canary_bundle",
        "research.v6_forecast_feedback",
    }
    assert len(result["schedules"]) == 6
    assert {item["origin"]["role"] for item in result["schedules"]} == {"stock_candidates", "stock_signals", "etf_data", "etf_candidates", "etf_signals", "monthly_feedback_review"}
    assert len(companion.v6_predictive.status()["definitions"]) == 8
    assert result["boundaries"]["separate_stock_and_fund_task_lines"] is True
    assert result["boundaries"]["automatic_decision_or_execution"] is False


def test_v6_stock_line_creates_separate_candidates_signals_and_outcomes(tmp_path: Path):
    companion = setup_v6(tmp_path)
    source = companion.data.manifest_publish(
        kind="v5_canary_quant_scan",
        schema_version="investment-companion.v5-continuous-quant-scan/v1",
        manifest={
            "program_id": "pytest-v6", "status": "ready", "as_of": "2026-07-01",
            "target_manifest_id": "fixture-target",
            "candidates": [{"asset_id": "tushare:600000.SH", "rank": 1, "momentum_20d": "0.08", "volatility_20d": "0.03", "mean_amount_provider_units": "1000000"}],
        },
    )
    candidates = companion.v6_predictive.generate_stock_research_candidates(
        program_id="pytest-v6", source_v5_scan_manifest_id=source["id"], top_k=10
    )
    candidate_body = candidates["manifest"]["manifest"]
    assert candidate_body["source_v5_scan_manifest_id"] == source["id"]
    assert candidate_body["candidates"][0]["asset_id"] == "tushare:600000.SH"
    signals = companion.v6_predictive.generate_stock_provisional_signals(
        program_id="pytest-v6", candidate_manifest_id=candidates["id"], horizon_sessions=20
    )
    signal_body = signals["manifest"]["manifest"]
    assert signal_body["model"]["id"] == "v6-mainboard-momentum-baseline-v1"
    assert signal_body["signals"][0]["recommendation_state"] == "provisional_action"
    bars = [{"asset_id": "tushare:600000.SH", "date": f"2026-07-{day:02d}", "close": str(10 + day / 10)} for day in range(1, 22)]
    outcomes = companion.v6_predictive.capture_stock_signal_outcomes(
        program_id="pytest-v6", signal_manifest_id=signals["id"], adjusted_bars=bars, observed_at="2026-08-01T16:00:00Z"
    )
    outcome = outcomes["manifest"]["manifest"]["outcomes"][0]
    assert outcome["task_line"] == "stock" and outcome["asset_id"] == "tushare:600000.SH"


def test_v6_forecast_keeps_unvalidated_status_without_suppressing_recommendation(tmp_path: Path):
    companion = setup_v6(tmp_path)
    stock = companion.v6_predictive.publish_forecast(
        program_id="pytest-v6",
        prediction=prediction("stock", "tushare:600000.SH", sample_status="insufficient_evidence"),
        thresholds=thresholds(),
        portfolio_assessment=portfolio(),
    )
    stock_body = stock["manifest"]["manifest"]
    assert stock_body["recommendation"]["state"] == "action"
    assert "forecast_sample_not_ready" in stock_body["recommendation"]["reasons"]
    assert stock_body["recommendation"]["validation_status"] == "unvalidated"

    universe_id = fund_universe(companion, task_line="fund_cross_border_etf", asset_id="tushare:510300.SH")
    fund = companion.v6_predictive.publish_forecast(
        program_id="pytest-v6",
        prediction=prediction("fund_cross_border_etf", fund_universe_manifest_id=universe_id),
        thresholds=thresholds(),
        portfolio_assessment=portfolio(conditions=["二级市场折价或溢价回到预设区间"]),
    )
    fund_body = fund["manifest"]["manifest"]
    assert fund_body["prediction"]["task_line"] == "fund_cross_border_etf"
    assert fund_body["recommendation"]["state"] == "conditional_action"
    assert companion.system_status()["counts"]["ledger_entries"] == 0
    assert companion.system_status()["counts"]["decision_queue_items"] == 0


def test_v6_fund_job_publishes_only_a_forecast_artifact(tmp_path: Path):
    companion = setup_v6(tmp_path)
    universe_id = fund_universe(companion, task_line="fund_bond_etf", asset_id="tushare:511010.SH")
    definition = companion.jobs.definition_for_handler(FUND_HANDLER)
    job = companion.jobs.enqueue_new_parent(
        definition_id=definition["id"],
        inputs={
            "refs": [],
            "parameters": {
                "program_id": "pytest-v6",
                "prediction": prediction("fund_bond_etf", "tushare:511010.SH", fund_universe_manifest_id=universe_id),
                "thresholds": thresholds(),
                "portfolio_assessment": portfolio(),
            },
            "knowledge_cutoff": iso(),
        },
        idempotency_key="pytest-v6-fund-forecast",
    )
    completed = companion.jobs.run_once("pytest-v6-worker", lease_seconds=30)
    assert completed["id"] == job["id"] and completed["status"] == "succeeded"
    report = companion.data.manifest_get(completed["output_manifest_id"])
    body = report["manifest"]["manifest"]
    assert report["kind"] == "v6_predictive_forecast"
    assert body["prediction"]["task_line"] == "fund_bond_etf"
    assert body["recommendation"]["state"] == "action"
    assert companion.system_status()["counts"]["ledger_entries"] == 0


def test_v6_fund_universe_excludes_missing_products_and_blocks_forecast(tmp_path: Path):
    companion = setup_v6(tmp_path)
    report = companion.v6_predictive.publish_fund_universe(
        program_id="pytest-v6",
        as_of=iso(),
        source_refs=["fixture-etf-basic", "fixture-product-document"],
        members=[
            {
                "asset_id": "tushare:518880.SH",
                "ts_code": "518880.SH",
                "name": "pytest gold ETF",
                "listing_venue": "SSE",
                "domestic_tradable": True,
                "classification": {"model_version": "pytest-etf-labels/1", "labels": ["gold"], "status": "provisional", "evidence_refs": ["fixture-etf-basic"]},
                "product_evidence_ref": "fixture-product-document",
                "data_readiness": {"prices": "ready", "liquidity": "missing", "costs": "ready", "structure": "ready"},
            }
        ],
    )
    body = report["manifest"]["manifest"]
    assert body["eligible_asset_ids"] == []
    assert body["exclusions"] == [{"asset_id": "tushare:518880.SH", "reasons": ["liquidity_missing"]}]
    with pytest.raises(CompanionError, match="eligible member"):
        companion.v6_predictive.publish_forecast(
            program_id="pytest-v6",
            prediction=prediction("fund_gold_etf", "tushare:518880.SH", fund_universe_manifest_id=report["id"]),
            thresholds=thresholds(),
            portfolio_assessment=portfolio(),
        )


def test_v6_unclassified_etf_is_eligible_when_market_data_is_ready(tmp_path: Path):
    companion = setup_v6(tmp_path)
    report = companion.v6_predictive.publish_fund_universe(
        program_id="pytest-v6",
        as_of=iso(),
        source_refs=["fixture-etf-basic", "fixture-product-document"],
        members=[
            {
                "asset_id": "tushare:159999.SZ",
                "ts_code": "159999.SZ",
                "name": "pytest unclassified ETF",
                "listing_venue": "SZSE",
                "domestic_tradable": True,
                "classification": {"model_version": "pytest-etf-labels/1", "labels": [], "status": "unclassified", "evidence_refs": []},
                "product_evidence_ref": "fixture-product-document",
                "data_readiness": {"prices": "ready", "liquidity": "ready", "costs": "ready", "structure": "ready"},
            }
        ],
    )
    body = report["manifest"]["manifest"]
    assert body["eligible_asset_ids"] == ["tushare:159999.SZ"]
    forecast = companion.v6_predictive.publish_forecast(
        program_id="pytest-v6",
        prediction=prediction("fund_domestic_etf", "tushare:159999.SZ", fund_universe_manifest_id=report["id"]),
        thresholds=thresholds(),
        portfolio_assessment=portfolio(),
    )
    assert forecast["manifest"]["manifest"]["recommendation"]["state"] == "action"


def test_v6_builds_domestic_etf_universe_and_features_from_frozen_provider_rows(tmp_path: Path):
    companion = setup_v6(tmp_path)
    first = date(2026, 7, 1)
    daily = [
        {"ts_code": "510300.SH", "trade_date": (first + timedelta(days=index)).strftime("%Y%m%d"), "close": str(4 + index / 100), "amount": "1000000"}
        for index in range(20)
    ]
    result = companion.v6_predictive.build_fund_universe_from_rows(
        program_id="pytest-v6",
        as_of="2026-07-20T16:00:00Z",
        etf_basic_rows=[
            {"ts_code": "510300.SH", "csname": "pytest 300 ETF", "index_code": "000300.SH", "etf_type": "股票型", "list_date": "20120528", "list_status": "L", "exchange": "SSE", "mgt_fee": "0.15"},
            {"ts_code": "510301.SH", "csname": "pytest delisted ETF", "index_code": "000300.SH", "etf_type": "股票型", "list_date": "20120528", "list_status": "D", "exchange": "SSE", "mgt_fee": "0.15"},
        ],
        fund_daily_rows=daily,
        fund_nav_rows=[{"ts_code": "510300.SH", "ann_date": "20260720", "nav_date": "20260720", "unit_nav": "4.1"}],
        fund_share_rows=[{"ts_code": "510300.SH", "trade_date": "20260720", "fd_share": "250000", "fund_type": "股票型", "market": "E"}],
        source_refs=["fixture-etf-basic", "fixture-fund-daily", "fixture-fund-nav", "fixture-fund-share"],
        lookback_sessions=20,
    )
    universe = result["universe"]["manifest"]["manifest"]
    features = result["features"]["manifest"]["manifest"]
    assert universe["eligible_asset_ids"] == ["tushare:510300.SH"]
    assert features["universe_manifest_id"] == result["universe"]["id"]
    feature = features["features"][0]
    assert feature["asset_id"] == "tushare:510300.SH"
    assert feature["observed_sessions"] == 20 and feature["average_amount"] == "1000000"
    assert feature["nav_premium"] is not None and feature["eligible_for_research"] is True
    candidates = companion.v6_predictive.generate_fund_research_candidates(
        program_id="pytest-v6", feature_snapshot_manifest_id=result["features"]["id"], top_k=5
    )
    candidate_body = candidates["manifest"]["manifest"]
    assert candidate_body["status"] == "ready"
    assert candidate_body["candidates"][0]["asset_id"] == "tushare:510300.SH"
    assert candidate_body["candidates"][0]["forecast_sample_status"] == "insufficient_evidence"
    signals = companion.v6_predictive.generate_fund_provisional_signals(
        program_id="pytest-v6", candidate_manifest_id=candidates["id"], horizon_sessions=20
    )
    signal_body = signals["manifest"]["manifest"]
    assert signal_body["model"]["id"] == "v6-domestic-etf-trend-baseline-v1"
    assert signal_body["signals"][0]["sample_status"] == "insufficient_evidence"
    assert signal_body["signals"][0]["recommendation_state"] == "provisional_action"
    assert signal_body["signals"][0]["validation_status"] == "unvalidated"
    settlement_daily = [
        {"ts_code": "510300.SH", "trade_date": (first + timedelta(days=index)).strftime("%Y%m%d"), "close": str(4 + index / 100), "amount": "1000000"}
        for index in range(40)
    ]
    outcomes = companion.v6_predictive.capture_fund_signal_outcomes(
        program_id="pytest-v6", signal_manifest_id=signals["id"], fund_daily_rows=settlement_daily, observed_at="2026-08-20T16:00:00Z"
    )
    assert outcomes["manifest"]["manifest"]["status"] == "settled"
    assert outcomes["manifest"]["manifest"]["outcomes"][0]["asset_id"] == "tushare:510300.SH"
    definition = companion.jobs.definition_for_handler(FUND_CANDIDATE_HANDLER)
    job = companion.jobs.enqueue_new_parent(
        definition_id=definition["id"],
        inputs={"refs": [], "parameters": {"program_id": "pytest-v6", "feature_snapshot_manifest_id": result["features"]["id"], "top_k": 5}, "knowledge_cutoff": iso()},
        idempotency_key="pytest-v6-fund-candidate-scan",
    )
    completed = companion.jobs.run_once("pytest-v6-worker", lease_seconds=30)
    assert completed["id"] == job["id"] and completed["status"] == "succeeded"
    signal_definition = companion.jobs.definition_for_handler(FUND_SIGNAL_HANDLER)
    signal_job = companion.jobs.enqueue_new_parent(
        definition_id=signal_definition["id"],
        inputs={"refs": [], "parameters": {"program_id": "pytest-v6", "horizon_sessions": 20}, "knowledge_cutoff": iso()},
        idempotency_key="pytest-v6-fund-provisional-signal",
    )
    signal_completed = companion.jobs.run_once("pytest-v6-worker", lease_seconds=30)
    assert signal_completed["id"] == signal_job["id"] and signal_completed["status"] == "succeeded"


def test_v6_feedback_review_reports_insufficient_evidence_and_never_mutates_strategy(tmp_path: Path):
    companion = setup_v6(tmp_path)
    forecast = companion.v6_predictive.publish_forecast(
        program_id="pytest-v6",
        prediction=prediction("stock", "tushare:601000.SH"),
        thresholds=thresholds(),
        portfolio_assessment=portfolio(),
    )
    feedback = companion.v6_predictive.publish_feedback(
        forecast_manifest_id=forecast["id"],
        realized_excess_return="0.02",
        realized_drawdown="-0.03",
        observed_at=iso(),
    )
    review = companion.v6_predictive.review(
        program_id="pytest-v6", feedback_manifest_ids=[feedback["id"]], reviewed_at=iso()
    )
    body = review["manifest"]["manifest"]
    assert body["task_lines"]["stock"]["observations"] == 1
    assert body["assessment"]["status"] == "insufficient_evidence"
    assert body["assessment"]["automatic_strategy_change"] is False
    assert companion.jobs.definition_for_handler(STOCK_HANDLER)["status"] == "active"
