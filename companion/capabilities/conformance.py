from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
from typing import Any

from ..foundation import CompanionError
from .registry import HOME_INVARIANT


def probe_investment_mcp(
    root: str | Path, *, fault: str | None = None
) -> dict[str, Any]:
    """Observe the real investment MCP profile through JSON-RPC stdio."""
    if fault not in {None, "omit_home_production_health"}:
        raise CompanionError(f"unsupported conformance fault: {fault}")
    requests = [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
        {
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {"name": "investment_home", "arguments": {}},
        },
    ]
    payload = "\n".join(json.dumps(item) for item in requests) + "\n"
    project_root = Path(__file__).resolve().parents[2]
    environment = {
        **os.environ,
        "COMPANION_ROOT": str(Path(root).resolve()),
        "COMPANION_GATE_SCOPE": "test_fixture",
        "COMPANION_MCP_PROFILE": "investment",
    }
    for name in (
        "COMPANION_CAPABILITY_RECEIPT_DIR",
        "COMPANION_CORE_IDENTITY",
        "COMPANION_PLUGIN_IDENTITY",
        "COMPANION_PLUGIN_REQUIREMENTS",
    ):
        environment.pop(name, None)
    if fault:
        environment["COMPANION_CONFORMANCE_FAULT"] = fault
    else:
        environment.pop("COMPANION_CONFORMANCE_FAULT", None)
    proc = subprocess.run(
        [sys.executable, "-m", "companion.mcp_server"],
        input=payload,
        text=True,
        capture_output=True,
        cwd=project_root,
        env=environment,
        timeout=30,
        check=True,
    )
    responses = [json.loads(line) for line in proc.stdout.splitlines()]
    by_id = {item.get("id"): item for item in responses}
    tools = by_id.get(2, {}).get("result", {}).get("tools", [])
    home_call = by_id.get(3, {}).get("result", {})
    return {
        "initialize": by_id.get(1, {}).get("result"),
        "tool_names": [item.get("name") for item in tools],
        "home_tool": next(
            (item for item in tools if item.get("name") == "investment_home"), None
        ),
        "home_result": home_call.get("structuredContent", {}).get("result"),
        "home_call_error": home_call.get("content", [{}])[0].get("text")
        if home_call.get("isError")
        else None,
    }


def evaluate_investment_conformance(observation: dict[str, Any]) -> dict[str, Any]:
    checks: list[dict[str, Any]] = []
    failures: list[dict[str, Any]] = []
    initialized = bool((observation.get("initialize") or {}).get("protocolVersion"))
    checks.append({"id": "mcp.initialize", "passed": initialized})
    if not initialized:
        failures.append({"code": "mcp_initialize_failed"})

    home_listed = bool(
        "investment_home" in observation.get("tool_names", [])
        and (observation.get("home_tool") or {}).get("inputSchema")
        == {
            "type": "object",
            "properties": {},
            "required": [],
            "additionalProperties": False,
        }
    )
    checks.append({"id": "mcp.tools-list.investment-home", "passed": home_listed})
    if not home_listed:
        failures.append({
            "code": "missing_or_drifted_tool",
            "capability": "investment_home",
        })

    home = observation.get("home_result")
    health = home.get("production_health") if isinstance(home, dict) else None
    required_health = {
        "baseline", "workflows", "optional_enhancements", "incidents"
    }
    invariant_passed = isinstance(health, dict) and required_health <= set(health)
    checks.append({"id": HOME_INVARIANT, "passed": invariant_passed})
    if not invariant_passed:
        failures.append({
            "code": "invariant_violation",
            "capability": "investment_home",
            "invariant": HOME_INVARIANT,
            "counterexample": "tools/call succeeded without required production_health",
        })
    return {
        "passed": not failures,
        "profile": "investment",
        "checks": checks,
        "failures": failures,
    }
