from __future__ import annotations

import subprocess
from datetime import timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from ..timeutil import iso, parse, utc_now


CRITICAL_RESEARCH_ROLES = {
    "data", "scan", "stock_candidates", "stock_signals",
    "etf_data", "etf_candidates", "etf_signals",
}
SYSTEMD_UNITS = (
    "companion-tick.service", "companion-job-worker.service",
    "companion-backup.service", "companion-recover.service",
)


class ProductionHealthService:
    """Read-only production identity, service and investment-pipeline checks."""

    def production_health(self) -> dict[str, Any]:
        if self.gate_scope != "production":
            return {"ok": True, "applicable": False, "checks": {}, "incidents": []}
        pointer = self.root / ".state" / "runtime-code-root"
        runtime = Path(pointer.read_text(encoding="utf-8").strip()).resolve() if pointer.is_file() else None
        runtime_head = self._runtime_head(runtime)
        gate = self.gates.latest("G0", "production")
        checks: dict[str, bool] = {
            "runtime_pointer_exists": bool(runtime and runtime.is_dir()),
            "runtime_commit_matches_g0": bool(runtime_head and gate and runtime_head == gate.get("code_version")),
        }
        services = self._service_health(runtime)
        checks["services_use_runtime"] = all(item["runtime_match"] for item in services)
        checks["services_last_result_success"] = all(item["result"] == "success" and item["exec_status"] == "0" for item in services)
        pipelines = self._pipeline_health()
        checks["critical_pipeline_roles_complete"] = {item["role"] for item in pipelines} == CRITICAL_RESEARCH_ROLES
        checks["critical_pipelines_have_runs"] = all(item["latest_run"] is not None for item in pipelines)
        checks["critical_pipelines_latest_run_succeeded"] = all(item["healthy"] for item in pipelines)
        checks["critical_pipeline_outputs_ready"] = all(item["semantic_ready"] for item in pipelines)
        freshness = self._research_freshness()
        checks["stock_market_data_sessions_complete"] = freshness["stock_market_data_sessions_complete"]
        checks["stock_scan_semantically_fresh"] = freshness["stock_scan_semantically_fresh"]
        checks["etf_features_semantically_fresh"] = freshness["etf_features_semantically_fresh"]
        with self.db.connect() as con:orphan_count=con.execute("SELECT COUNT(*) FROM broker_managed_orders WHERE status<>'rejected' AND (execution_id IS NULL OR execution_link_state<>'linked')").fetchone()[0]
        checks["broker_orders_linked_to_execution"] = orphan_count==0
        incidents = [
            {"severity": "critical", "check": key}
            for key, passed in checks.items() if not passed
        ]
        return {
            "ok": not incidents, "applicable": True, "checked_at": iso(),
            "runtime": {"pointer": str(runtime) if runtime else None, "commit": runtime_head, "g0_commit": gate.get("code_version") if gate else None},
            "checks": checks, "services": services, "pipelines": pipelines,
            "research_freshness": freshness,
            "broker_execution_orphan_count": orphan_count,
            "incidents": incidents,
        }

    @staticmethod
    def _runtime_head(runtime: Path | None) -> str | None:
        if not runtime or not runtime.is_dir():return None
        proc = subprocess.run(["git", "rev-parse", "HEAD"], cwd=runtime, capture_output=True, text=True, timeout=10, check=False)
        value = proc.stdout.strip()
        return value if proc.returncode == 0 and len(value) == 40 else None

    @staticmethod
    def _service_health(runtime: Path | None) -> list[dict[str, Any]]:
        result = []
        expected = str(runtime) if runtime else None
        for unit in SYSTEMD_UNITS:
            proc = subprocess.run(
                ["systemctl", "--user", "show", unit, "-p", "WorkingDirectory", "-p", "Result", "-p", "ExecMainStatus"],
                capture_output=True, text=True, timeout=10, check=False,
            )
            values = dict(line.split("=", 1) for line in proc.stdout.splitlines() if "=" in line)
            working, status, code = values.get("WorkingDirectory", ""), values.get("Result", ""), values.get("ExecMainStatus", "")
            result.append({"unit": unit, "working_directory": working or None, "result": status or None, "exec_status": code or None, "runtime_match": proc.returncode == 0 and working == expected})
        return result

    def _pipeline_health(self) -> list[dict[str, Any]]:
        result = []
        schedules = [item for item in self.schedule_list(status="active") if item.get("origin", {}).get("role") in CRITICAL_RESEARCH_ROLES]
        for schedule in schedules:
            history = self.schedule_history(schedule["id"], 2)
            latest = history[0] if history else None
            previous = history[1] if len(history) > 1 else None
            reference = (latest or {}).get("created_at") or (latest or {}).get("due_at")
            recent = bool(reference and parse(reference) >= utc_now() - timedelta(minutes=15))
            in_progress = bool(
                latest and latest.get("status") in {"queued", "running"} and recent
                and previous and previous.get("status") == "succeeded"
            )
            healthy = bool(latest and (latest.get("status") == "succeeded" or in_progress))
            output_status = None
            semantic_ready = True
            if latest and latest.get("status") == "succeeded" and latest.get("job_run_id"):
                job = self.jobs.run_get(latest["job_run_id"])
                manifest_id = job.get("output_manifest_id")
                if manifest_id:
                    body = self.data.manifest_get(manifest_id, verify=True)["manifest"]["manifest"]
                    output_status = body.get("status")
                    semantic_ready = output_status != "waiting_upstream"
            result.append({
                "schedule_id": schedule["id"], "role": schedule.get("origin", {}).get("role"),
                "latest_run": latest.get("id") if latest else None,
                "latest_status": latest.get("status") if latest else None,
                "finished_at": latest.get("finished_at") if latest else None,
                "healthy": healthy, "in_progress": in_progress,
                "output_status": output_status, "semantic_ready": semantic_ready,
            })
        return sorted(result, key=lambda item: (str(item["role"]), item["schedule_id"]))

    def _research_freshness(self) -> dict[str, Any]:
        """Check data meaning, not merely whether the scheduler returned success."""
        from ..predictive_runtime import dated_object_refs
        from ..v5_quant_experiment import SCAN_MANIFEST_KIND
        from ..v6_predictive_recommendations import FUND_FEATURES_KIND

        now = utc_now()
        calendar = self.quant_research._calendar_rows(now)
        local_now = now.astimezone(ZoneInfo("Asia/Shanghai"))
        local_day = local_now.date().isoformat()
        # The last research role is scheduled for 18:35 China time.  Before
        # that daily completion window, today's session is not yet required.
        require_today = (local_now.hour, local_now.minute) >= (18, 45)
        sessions = sorted({
            str(row.get("date")) for row in calendar
            if row.get("exchange") == "SSE" and row.get("is_open") in {True, 1, "1"}
            and (str(row.get("date")) < local_day or (require_today and str(row.get("date")) == local_day))
        })[-21:]
        expected = sessions[-1] if sessions else None
        daily = dated_object_refs(self.quant_research, "daily", now)
        adjusted = dated_object_refs(self.quant_research, "adj_factor", now)
        missing = [day for day in sessions if day not in daily or day not in adjusted]

        latest_scan_as_of = None
        latest_etf_trade_date = None
        with self.db.connect() as con:
            row = con.execute(
                "SELECT manifest_json FROM artifact_manifests WHERE kind=? ORDER BY created_at DESC,id DESC LIMIT 1",
                (SCAN_MANIFEST_KIND,),
            ).fetchone()
            if row:
                import json
                body = json.loads(row["manifest_json"]).get("manifest", {})
                latest_scan_as_of = body.get("as_of") or body.get("details", {}).get("as_of")
            row = con.execute(
                "SELECT manifest_json FROM artifact_manifests WHERE kind=? ORDER BY created_at DESC,id DESC LIMIT 1",
                (FUND_FEATURES_KIND,),
            ).fetchone()
            if row:
                import json
                features = json.loads(row["manifest_json"]).get("manifest", {}).get("features", [])
                dates = [item.get("latest_trade_date") for item in features if item.get("latest_trade_date")]
                latest_etf_trade_date = max(dates) if dates else None
        scan_day = str(latest_scan_as_of or "")[:10] or None
        return {
            "expected_latest_session": expected,
            "required_sessions": sessions,
            "missing_stock_sessions": missing,
            "latest_stock_scan_date": scan_day,
            "latest_etf_feature_trade_date": latest_etf_trade_date,
            "stock_market_data_sessions_complete": bool(expected and not missing),
            "stock_scan_semantically_fresh": bool(expected and scan_day == expected),
            "etf_features_semantically_fresh": bool(expected and latest_etf_trade_date and latest_etf_trade_date >= expected),
        }
