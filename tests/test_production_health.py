from pathlib import Path

from companion.core import Companion
from companion.platform.production_health import ProductionHealthService, SYSTEMD_UNITS


def test_production_doctor_detects_runtime_drift_and_latest_pipeline_failure(tmp_path: Path, monkeypatch):
    companion = Companion(tmp_path, gate_scope="test_fixture")
    companion.initialize()
    companion.gate_scope = "production"
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    (tmp_path / ".state" / "runtime-code-root").write_text(str(runtime), encoding="utf-8")
    monkeypatch.setattr(companion.gates, "latest", lambda *_args: {"code_version": "g0-commit"})
    monkeypatch.setattr(ProductionHealthService, "_runtime_head", staticmethod(lambda _runtime: "runtime-commit"))
    monkeypatch.setattr(ProductionHealthService, "_service_health", staticmethod(lambda _runtime: [
        {"unit": unit, "working_directory": str(runtime), "result": "success", "exec_status": "0", "runtime_match": True}
        for unit in SYSTEMD_UNITS
    ]))
    monkeypatch.setattr(companion, "schedule_list", lambda **_kwargs: [
        {"id": "scan", "origin": {"role": "scan"}},
        {"id": "signals", "origin": {"role": "stock_signals"}},
    ])
    monkeypatch.setattr(companion, "schedule_history", lambda schedule_id, _limit: [{
        "id": f"run-{schedule_id}", "status": "failed" if schedule_id == "scan" else "succeeded", "finished_at": "2026-08-25T10:00:00Z"
    }])

    result = companion.production_health()

    assert result["ok"] is False
    assert result["checks"]["runtime_commit_matches_g0"] is False
    assert result["checks"]["critical_pipelines_latest_run_succeeded"] is False
    assert {item["check"] for item in result["incidents"]} == {
        "runtime_commit_matches_g0", "critical_pipelines_latest_run_succeeded"
    }


def test_production_health_is_not_applicable_to_isolated_test_fixtures(tmp_path: Path):
    companion = Companion(tmp_path, gate_scope="test_fixture")
    companion.initialize()
    assert companion.production_health() == {"ok": True, "applicable": False, "checks": {}, "incidents": []}
