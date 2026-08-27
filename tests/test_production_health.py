from pathlib import Path

from companion.core import Companion
from companion.platform.production_health import ProductionHealthService, SYSTEMD_UNITS


def test_research_freshness_does_not_require_current_session_before_completion_window(tmp_path: Path, monkeypatch):
    companion = Companion(tmp_path, gate_scope="test_fixture")
    companion.initialize()
    monkeypatch.setattr("companion.platform.production_health.utc_now", lambda: __import__("datetime").datetime(2026, 8, 25, 23, 40, tzinfo=__import__("datetime").timezone.utc))
    monkeypatch.setattr(companion.quant_research, "_calendar_rows", lambda _now: [
        {"exchange": "SSE", "date": "2026-08-25", "is_open": "1"},
        {"exchange": "SSE", "date": "2026-08-26", "is_open": "1"},
    ])
    monkeypatch.setattr("companion.predictive_runtime.dated_object_refs", lambda _service, _capability, _now: {"2026-08-25": "object"})

    result = companion._research_freshness()

    assert result["expected_latest_session"] == "2026-08-25"
    assert result["missing_stock_sessions"] == []


def test_research_freshness_accepts_no_new_session_scan_as_semantically_current(tmp_path: Path, monkeypatch):
    companion = Companion(tmp_path, gate_scope="test_fixture")
    companion.initialize()
    monkeypatch.setattr("companion.platform.production_health.utc_now", lambda: __import__("datetime").datetime(2026, 8, 25, 12, 0, tzinfo=__import__("datetime").timezone.utc))
    monkeypatch.setattr(companion.quant_research, "_calendar_rows", lambda _now: [
        {"exchange": "SSE", "date": "2026-08-25", "is_open": "1"},
    ])
    monkeypatch.setattr("companion.predictive_runtime.dated_object_refs", lambda _service, _capability, _now: {"2026-08-25": "object"})
    companion.data.manifest_publish(
        kind="v5_canary_quant_scan",
        schema_version="investment-companion.v5-continuous-quant-scan/v1",
        manifest={"status": "no_new_session", "details": {"as_of": "2026-08-25"}},
    )

    result = companion._research_freshness()

    assert result["latest_stock_scan_date"] == "2026-08-25"
    assert result["stock_scan_semantically_fresh"] is True


def test_research_freshness_accepts_scan_newer_than_expected_session(tmp_path: Path, monkeypatch):
    companion = Companion(tmp_path, gate_scope="test_fixture")
    companion.initialize()
    monkeypatch.setattr("companion.platform.production_health.utc_now", lambda: __import__("datetime").datetime(2026, 8, 27, 10, 30, tzinfo=__import__("datetime").timezone.utc))
    monkeypatch.setattr(companion.quant_research, "_calendar_rows", lambda _now: [
        {"exchange": "SSE", "date": "2026-08-26", "is_open": "1"},
        {"exchange": "SSE", "date": "2026-08-27", "is_open": "1"},
    ])
    monkeypatch.setattr("companion.predictive_runtime.dated_object_refs", lambda _service, _capability, _now: {"2026-08-26": "object"})
    companion.data.manifest_publish(
        kind="v5_canary_quant_scan",
        schema_version="investment-companion.v5-continuous-quant-scan/v1",
        manifest={"status": "ready", "details": {"as_of": "2026-08-27"}},
    )

    result = companion._research_freshness()

    assert result["expected_latest_session"] == "2026-08-26"
    assert result["latest_stock_scan_date"] == "2026-08-27"
    assert result["stock_scan_semantically_fresh"] is True


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
    monkeypatch.setattr(companion, "_research_freshness", lambda: {
        "stock_market_data_sessions_complete": True,
        "stock_scan_semantically_fresh": True,
        "etf_features_semantically_fresh": True,
    })
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
        "runtime_commit_matches_g0", "critical_pipeline_roles_complete", "critical_pipelines_latest_run_succeeded"
    }


def test_production_health_is_not_applicable_to_isolated_test_fixtures(tmp_path: Path):
    companion = Companion(tmp_path, gate_scope="test_fixture")
    companion.initialize()
    assert companion.production_health() == {"ok": True, "applicable": False, "checks": {}, "incidents": []}


def test_service_health_treats_running_oneshot_without_final_result_as_healthy(tmp_path: Path, monkeypatch):
    runtime = tmp_path / "runtime"
    runtime.mkdir()

    class Result:
        returncode = 0
        stdout = "\n".join([
            f"WorkingDirectory={runtime}",
            "Result=",
            "ExecMainStatus=",
            "ActiveState=activating",
            "SubState=start",
        ])

    monkeypatch.setattr("companion.platform.production_health.subprocess.run", lambda *_args, **_kwargs: Result())

    services = ProductionHealthService._service_health(runtime)

    assert len(services) == len(SYSTEMD_UNITS)
    assert all(item["healthy"] for item in services)
    assert all(item["active_state"] == "activating" for item in services)


def test_service_health_supplies_user_bus_environment_to_mcp_subprocess(tmp_path: Path, monkeypatch):
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    captured = []

    class Result:
        returncode = 0
        stderr = ""
        stdout = "\n".join([
            f"WorkingDirectory={runtime}", "Result=success", "ExecMainStatus=0",
            "ActiveState=inactive", "SubState=dead",
        ])

    def fake_run(*_args, **kwargs):
        captured.append(kwargs["env"])
        return Result()

    monkeypatch.delenv("XDG_RUNTIME_DIR", raising=False)
    monkeypatch.delenv("DBUS_SESSION_BUS_ADDRESS", raising=False)
    monkeypatch.setattr("companion.platform.production_health.os.getuid", lambda: 1234)
    monkeypatch.setattr("companion.platform.production_health.subprocess.run", fake_run)

    services = ProductionHealthService._service_health(runtime)

    assert all(item["healthy"] for item in services)
    assert all(env["XDG_RUNTIME_DIR"] == "/run/user/1234" for env in captured)
    assert all(env["DBUS_SESSION_BUS_ADDRESS"] == "unix:path=/run/user/1234/bus" for env in captured)


def test_production_doctor_fails_when_successful_pipeline_outputs_are_stale(tmp_path: Path, monkeypatch):
    companion = Companion(tmp_path, gate_scope="test_fixture")
    companion.initialize()
    companion.gate_scope = "production"
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    (tmp_path / ".state" / "runtime-code-root").write_text(str(runtime), encoding="utf-8")
    monkeypatch.setattr(companion.gates, "latest", lambda *_args: {"code_version": "same"})
    monkeypatch.setattr(ProductionHealthService, "_runtime_head", staticmethod(lambda _runtime: "same"))
    monkeypatch.setattr(ProductionHealthService, "_service_health", staticmethod(lambda _runtime: [
        {"unit": unit, "working_directory": str(runtime), "result": "success", "exec_status": "0", "runtime_match": True}
        for unit in SYSTEMD_UNITS
    ]))
    monkeypatch.setattr(companion, "_pipeline_health", lambda: [
        {"role": role, "latest_run": f"run-{role}", "healthy": True, "semantic_ready": True}
        for role in sorted({"data", "scan", "stock_candidates", "stock_signals", "etf_data", "etf_candidates", "etf_signals"})
    ])
    monkeypatch.setattr(companion, "_research_freshness", lambda: {
        "expected_latest_session": "2026-08-25",
        "missing_stock_sessions": ["2026-08-24"],
        "latest_stock_scan_date": "2026-08-25",
        "latest_etf_feature_trade_date": "2026-08-21",
        "stock_market_data_sessions_complete": False,
        "stock_scan_semantically_fresh": True,
        "etf_features_semantically_fresh": False,
    })

    result = companion.production_health()

    assert result["ok"] is False
    assert {item["check"] for item in result["incidents"]} == {
        "stock_market_data_sessions_complete", "etf_features_semantically_fresh"
    }


def test_pipeline_health_treats_recent_queued_successor_as_in_progress(tmp_path: Path, monkeypatch):
    companion = Companion(tmp_path, gate_scope="test_fixture")
    companion.initialize()
    now = "2026-08-26T10:05:00Z"
    monkeypatch.setattr("companion.platform.production_health.utc_now", lambda: __import__("datetime").datetime(2026, 8, 26, 10, 5, tzinfo=__import__("datetime").timezone.utc))
    monkeypatch.setattr(companion, "schedule_list", lambda **_kwargs: [
        {"id": "etf", "origin": {"role": "etf_candidates"}},
    ])
    monkeypatch.setattr(companion, "schedule_history", lambda _schedule_id, limit: [
        {"id": "new", "status": "queued", "created_at": now},
        {"id": "old", "status": "succeeded", "finished_at": "2026-08-25T10:00:00Z"},
    ][:limit])

    result = companion._pipeline_health()

    assert result[0]["healthy"] is True
    assert result[0]["in_progress"] is True
    assert result[0]["semantic_ready"] is True


def test_pipeline_health_rejects_succeeded_run_with_waiting_upstream_output(tmp_path: Path, monkeypatch):
    companion = Companion(tmp_path, gate_scope="test_fixture")
    companion.initialize()
    waiting = companion.data.manifest_publish(
        kind="v6_stock_research_candidates", schema_version="fixture/v1",
        manifest={"status": "waiting_upstream"},
    )
    monkeypatch.setattr(companion, "schedule_list", lambda **_kwargs: [
        {"id": "stock", "origin": {"role": "stock_candidates"}},
    ])
    monkeypatch.setattr(companion, "schedule_history", lambda _schedule_id, _limit: [
        {"id": "run", "status": "succeeded", "job_run_id": "job"},
    ])
    monkeypatch.setattr(companion.jobs, "run_get", lambda _job_id: {"output_manifest_id": waiting["id"]})

    result = companion._pipeline_health()

    assert result[0]["healthy"] is True
    assert result[0]["output_status"] == "waiting_upstream"
    assert result[0]["semantic_ready"] is False
