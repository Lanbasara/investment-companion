from __future__ import annotations

import json
import os
import sys
from typing import Any

from .core import Companion, CompanionError
from .interfaces.mcp_profiles import (
    INVESTMENT_CAPABILITY_REGISTRY,
    INVESTMENT_TOOLS,
    call_investment,
)


ROOT = os.environ.get("COMPANION_ROOT", "/home/ghk/investment-home")
C = Companion(ROOT, capability_registry=INVESTMENT_CAPABILITY_REGISTRY)


def call(name: str, arguments: dict[str, Any]) -> Any:
    """Dispatch the single supported MCP release profile through the Registry."""
    if name not in INVESTMENT_TOOLS:
        raise CompanionError(f"unknown tool: {name}")
    return call_investment(C, name, arguments, "primary-codex")


def reply(request: dict[str, Any]) -> dict[str, Any] | None:
    method = request.get("method")
    request_id = request.get("id")
    if request_id is None:
        return None
    if method == "initialize":
        result = {
            "protocolVersion": "2025-06-18",
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {"name": "investment-companion", "version": "7.0.0"},
        }
    elif method == "tools/list":
        result = {
            "tools": [
                {"name": name, "description": description, "inputSchema": schema}
                for name, (description, schema) in INVESTMENT_TOOLS.items()
            ]
        }
    elif method == "tools/call":
        params = request.get("params", {})
        try:
            value = call(params["name"], params.get("arguments", {}))
            fault = (
                C.gate_scope == "test_fixture"
                and params.get("name") == "investment_home"
                and os.environ.get("COMPANION_CONFORMANCE_FAULT")
                == "omit_home_production_health"
            )
            if fault:
                value = {
                    key: item
                    for key, item in value.items()
                    if key != "production_health"
                }
            result = {
                "content": [
                    {
                        "type": "text",
                        "text": json.dumps(value, ensure_ascii=False, indent=2),
                    }
                ],
                "structuredContent": {"result": value},
            }
        except Exception as exc:
            result = {
                "content": [{"type": "text", "text": str(exc)}],
                "isError": True,
            }
    elif method == "ping":
        result = {}
    else:
        return {
            "jsonrpc": "2.0",
            "id": request_id,
            "error": {"code": -32601, "message": f"Method not found: {method}"},
        }
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def main() -> None:
    profile = os.environ.get("COMPANION_MCP_PROFILE", "investment")
    if profile != "investment":
        raise CompanionError(
            "the release MCP supports only COMPANION_MCP_PROFILE=investment"
        )
    C.initialize()
    for line in sys.stdin:
        try:
            request = json.loads(line)
            response = reply(request)
            if response is not None:
                sys.stdout.write(
                    json.dumps(response, ensure_ascii=False, separators=(",", ":"))
                    + "\n"
                )
                sys.stdout.flush()
        except Exception as exc:
            sys.stdout.write(
                json.dumps(
                    {
                        "jsonrpc": "2.0",
                        "id": None,
                        "error": {"code": -32603, "message": str(exc)},
                    }
                )
                + "\n"
            )
            sys.stdout.flush()


if __name__ == "__main__":
    main()
