from __future__ import annotations

import json
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from companion.core import Companion, CompanionError
from companion.governance import GATE_CHECKLISTS
from companion.timeutil import iso, utc_now
from companion.tushare_adapter import TushareAdapter
from companion.v5_quant_experiment import DATA_HANDLER, REQUEST_FIELDS, SCAN_HANDLER


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


def setup_trial(tmp_path: Path) -> Companion:
    companion = Companion(tmp_path, gate_scope="test_fixture")
    companion.initialize()
    pass_g0(companion)
    companion.jobs.feature_set("v4_jobs", True, reason="fixture deterministic jobs")
    companion.jobs.feature_set(
        "v4_live_data_canary",
        True,
        config={
            "mode": "v5_quant_experiment",
            "trial_id": "pytest-v5-quant",
            "user_approval_ref": "pytest-explicit-approval",
            "started_at": iso(utc_now() - timedelta(minutes=1)),
            "expires_at": iso(utc_now() + timedelta(days=30)),
            "max_trading_days": 20,
            "max_requests_per_job": 3,
        },
        reason="fixture controlled trial",
    )
    companion.quant_experiment.bootstrap(activate=True)
    return companion


def test_controlled_bootstrap_is_idempotent_and_preserves_hard_boundaries(tmp_path: Path):
    companion = setup_trial(tmp_path)
    second = companion.quant_experiment.bootstrap(activate=True)
    assert len(second["definitions"]) == 2
    assert {item["handler"] for item in second["definitions"]} == {DATA_HANDLER, SCAN_HANDLER}
    assert len(second["schedules"]) == 2
    assert all(item["status"] == "active" for item in second["schedules"])
    assert companion.jobs.feature_get("v4_live_data")["enabled"] is False
    assert companion.jobs.feature_get("v4_shadow")["enabled"] is False
    assert companion.jobs.feature_get("v4_decision_support_beta")["enabled"] is False
    assert second["boundaries"]["broker_or_auto_trade"] is False
    status = companion.quant_experiment.status()
    assert status["state"] == "active" and len(status["definitions"]) == 2


def test_canary_bundle_job_uses_only_frozen_tushare_contract(tmp_path: Path, monkeypatch):
    companion = setup_trial(tmp_path)
    response = {
        "code": 0,
        "msg": None,
        "data": {
            "fields": REQUEST_FIELDS["trade_cal"],
            "items": [["SSE", "20260818", 1, "20260817"]],
        },
    }
    monkeypatch.setattr(
        TushareAdapter,
        "_load_token",
        staticmethod(lambda _token, _token_file: "fixture-token-value"),
    )
    monkeypatch.setattr(
        TushareAdapter,
        "_http_transport",
        lambda _self, _body, _timeout: json.dumps(response).encode(),
    )
    definition = companion.jobs.definition_for_handler(DATA_HANDLER)
    job = companion.jobs.enqueue_new_parent(
        definition_id=definition["id"],
        inputs={
            "refs": [],
            "parameters": {
                "trial_id": "pytest-v5-quant",
                "requests": [
                    {
                        "capability": "trade_cal",
                        "params": {
                            "exchange": "SSE",
                            "start_date": "20260801",
                            "end_date": "20260831",
                        },
                        "fields": REQUEST_FIELDS["trade_cal"],
                    }
                ],
            },
            "knowledge_cutoff": iso(),
        },
        idempotency_key="pytest-v5-canary-bundle",
    )
    result = companion.jobs.run_once("pytest-worker", lease_seconds=30)
    assert result["id"] == job["id"] and result["status"] == "succeeded"
    body = companion.data.manifest_get(result["output_manifest_id"])["manifest"]["manifest"]
    assert body["status"] == "ready" and body["model_tokens"] == 0
    assert body["forbidden_downstream"] == ["decision", "execution", "ledger", "broker"]


def test_real_data_shape_scan_records_forward_outcome_without_decision_or_ledger(
    tmp_path: Path,
):
    companion = setup_trial(tmp_path)
    start = date(2026, 6, 1)
    sessions = [start + timedelta(days=index) for index in range(23)]
    assets = [f"{600000 + index:06d}.SH" for index in range(120)]

    def transport(body: bytes, _timeout: float) -> bytes:
        request = json.loads(body.decode())
        capability = request["api_name"]
        if capability == "trade_cal":
            rows = [
                ["SSE", day.strftime("%Y%m%d"), 1, sessions[max(0, index - 1)].strftime("%Y%m%d")]
                for index, day in enumerate(sessions)
            ]
            response = {"code": 0, "msg": None, "data": {"fields": REQUEST_FIELDS["trade_cal"], "items": rows}}
        else:
            day = request["params"]["trade_date"]
            day_index = next(index for index, value in enumerate(sessions) if value.strftime("%Y%m%d") == day)
            if capability == "daily":
                rows = []
                for index, asset in enumerate(assets):
                    base = Decimal("8") + Decimal(index) / Decimal("100")
                    growth = Decimal(day_index) * (Decimal("0.006") + Decimal(index) / Decimal("100000"))
                    close = (base * (Decimal("1") + growth)).quantize(Decimal("0.0001"))
                    rows.append([
                        asset,
                        day,
                        str(close),
                        str(close + Decimal("0.1")),
                        str(close - Decimal("0.1")),
                        str(close),
                        str(1_000_000 - index * 100),
                        str(100_000_000 - index * 10_000),
                    ])
                response = {"code": 0, "msg": None, "data": {"fields": REQUEST_FIELDS["daily"], "items": rows}}
            else:
                rows = [[asset, day, "1"] for asset in assets]
                response = {"code": 0, "msg": None, "data": {"fields": REQUEST_FIELDS["adj_factor"], "items": rows}}
        return json.dumps(response).encode()

    adapter = TushareAdapter(companion, token="fixture-token-value", transport=transport)
    adapter.ingest_canary(
        "trade_cal",
        params={
            "exchange": "SSE",
            "start_date": sessions[0].strftime("%Y%m%d"),
            "end_date": sessions[-1].strftime("%Y%m%d"),
        },
        fields=REQUEST_FIELDS["trade_cal"],
    )
    for day in sessions[:21]:
        compact = day.strftime("%Y%m%d")
        adapter.ingest_canary("daily", params={"trade_date": compact}, fields=REQUEST_FIELDS["daily"], ingestion_key=compact)
        adapter.ingest_canary("adj_factor", params={"trade_date": compact}, fields=REQUEST_FIELDS["adj_factor"], ingestion_key=compact)

    definition = companion.jobs.definition_for_handler(SCAN_HANDLER)
    ledger_before = companion.system_status()["counts"]["ledger_entries"]
    first = companion.jobs.enqueue_new_parent(
        definition_id=definition["id"],
        inputs={"refs": [], "parameters": {"trial_id": "pytest-v5-quant"}, "knowledge_cutoff": iso()},
        idempotency_key="pytest-v5-scan-1",
    )
    completed = companion.jobs.run_once("pytest-scan-worker", lease_seconds=60)
    assert completed["id"] == first["id"] and completed["status"] == "succeeded"
    first_body = companion.data.manifest_get(completed["output_manifest_id"])["manifest"]["manifest"]
    assert first_body["status"] == "ready" and len(first_body["candidates"]) == 10
    assert first_body["research_only"] is True and first_body["not_a_decision"] is True
    assert first_body["trial_scorecard"]["observations"] == 0

    next_day = sessions[21]
    compact = next_day.strftime("%Y%m%d")
    adapter.ingest_canary("daily", params={"trade_date": compact}, fields=REQUEST_FIELDS["daily"], ingestion_key=compact)
    adapter.ingest_canary("adj_factor", params={"trade_date": compact}, fields=REQUEST_FIELDS["adj_factor"], ingestion_key=compact)
    second = companion.jobs.enqueue_new_parent(
        definition_id=definition["id"],
        inputs={"refs": [], "parameters": {"trial_id": "pytest-v5-quant"}, "knowledge_cutoff": iso()},
        idempotency_key="pytest-v5-scan-2",
    )
    completed_second = companion.jobs.run_once("pytest-scan-worker", lease_seconds=60)
    assert completed_second["id"] == second["id"] and completed_second["status"] == "succeeded"
    second_body = companion.data.manifest_get(completed_second["output_manifest_id"])["manifest"]["manifest"]
    assert second_body["trial_scorecard"]["observations"] == 1
    assert second_body["forward_observation"]["predecessor_manifest_id"] == completed["output_manifest_id"]
    assert companion.system_status()["counts"]["ledger_entries"] == ledger_before
    assert companion.operating.opportunity_list(status=None) == []
    assert companion.jobs.feature_get("v4_live_data")["enabled"] is False


def test_expired_trial_fails_closed(tmp_path: Path):
    companion = setup_trial(tmp_path)
    feature = companion.jobs.feature_get("v4_live_data_canary")
    expired = {**feature["config"], "expires_at": iso(utc_now() - timedelta(seconds=1))}
    companion.jobs.feature_set(
        "v4_live_data_canary", True, config=expired, reason="fixture expiry"
    )
    with pytest.raises(CompanionError, match="expired"):
        companion.quant_experiment.require_trial()
    assert companion.quant_experiment.status()["state"] == "expired"
