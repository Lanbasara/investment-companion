from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

from ..timeutil import iso


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
            history = self.schedule_history(schedule["id"], 1)
            latest = history[0] if history else None
            healthy = bool(latest and latest.get("status") == "succeeded")
            result.append({
                "schedule_id": schedule["id"], "role": schedule.get("origin", {}).get("role"),
                "latest_run": latest.get("id") if latest else None,
                "latest_status": latest.get("status") if latest else None,
                "finished_at": latest.get("finished_at") if latest else None,
                "healthy": healthy,
            })
        return sorted(result, key=lambda item: (str(item["role"]), item["schedule_id"]))
