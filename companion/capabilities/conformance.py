from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
from typing import Any

from ..foundation import CompanionError
from .registry import (
    CONTEXT_CONFIRMATION_INVARIANT,
    CONTINUITY_INVARIANT,
    HOME_INVARIANT,
    PORTFOLIO_LEDGER_INVARIANT,
    PORTFOLIO_TRUTH_INVARIANT,
    RECONCILIATION_INVARIANT,
    TRANSACTION_CONFIRMATION_INVARIANT,
)


def _call_result(response: dict[str, Any]) -> Any:
    return response.get("result", {}).get("structuredContent", {}).get("result")


def _call_error(response: dict[str, Any]) -> str | None:
    result = response.get("result", {})
    if not result.get("isError"):
        return None
    return result.get("content", [{}])[0].get("text")


def probe_investment_mcp(
    root: str | Path, *, fault: str | None = None
) -> dict[str, Any]:
    """Exercise the real investment MCP profile against isolated synthetic facts."""
    if fault not in {None, "omit_home_production_health"}:
        raise CompanionError(f"unsupported conformance fault: {fault}")
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

    proc = subprocess.Popen(
        [sys.executable, "-m", "companion.mcp_server"],
        text=True,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        cwd=project_root,
        env=environment,
    )
    if proc.stdin is None or proc.stdout is None:
        proc.kill()
        raise CompanionError("investment MCP conformance stdio is unavailable")
    request_id = 0

    def request(method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        nonlocal request_id
        request_id += 1
        payload = {"jsonrpc": "2.0", "id": request_id, "method": method}
        if params is not None:
            payload["params"] = params
        proc.stdin.write(json.dumps(payload) + "\n")
        proc.stdin.flush()
        line = proc.stdout.readline()
        if not line:
            stderr = proc.stderr.read() if proc.stderr else ""
            raise CompanionError(f"investment MCP conformance ended early: {stderr}")
        response = json.loads(line)
        if response.get("id") != request_id:
            raise CompanionError("investment MCP conformance response id mismatch")
        return response

    def call(name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        return request("tools/call", {"name": name, "arguments": arguments})

    try:
        initialized = request("initialize", {}).get("result")
        tools_response = request("tools/list", {}).get("result", {})
        tools = tools_response.get("tools", [])
        home_call = call("investment_home", {})

        context_draft_call = call(
            "investment_context_update",
            {
                "operation": "draft",
                "context_type": "investor",
                "content": {"objective": "synthetic conformance objective"},
                "reason": "isolated conformance fixture",
            },
        )
        context_draft = _call_result(context_draft_call)
        context_missing_ref_call = call(
            "investment_context_update", {"operation": "confirm"}
        )
        context_confirm_call = call(
            "investment_context_update",
            {"operation": "confirm", "revision_id": context_draft["id"]},
        )

        account_call = call(
            "investment_transaction_update",
            {
                "operation": "account_create",
                "name": "Synthetic conformance account",
                "base_currency": "CNY",
            },
        )
        account = _call_result(account_call)
        account_id = account["id"]
        before_call = call(
            "portfolio_context",
            {"account_id": account_id, "as_of": "2025-01-02T00:00:00Z"},
        )

        pending_call = call(
            "investment_transaction_update",
            {
                "operation": "record",
                "account_id": account_id,
                "entry_type": "cash_deposit",
                "occurred_at": "2025-01-01T00:00:00Z",
                "amount": "100",
                "currency": "CNY",
                "source": "synthetic-conformance",
            },
        )
        pending = _call_result(pending_call)
        pending_portfolio_call = call(
            "portfolio_context",
            {"account_id": account_id, "as_of": "2025-01-02T00:00:00Z"},
        )
        transaction_missing_ref_call = call(
            "investment_transaction_update", {"operation": "confirm"}
        )
        confirmed_call = call(
            "investment_transaction_update",
            {"operation": "confirm", "entry_id": pending["id"]},
        )
        confirmed_portfolio_call = call(
            "portfolio_context",
            {"account_id": account_id, "as_of": "2025-01-02T00:00:00Z"},
        )
        reversal_call = call(
            "investment_transaction_update",
            {
                "operation": "reverse",
                "entry_id": pending["id"],
                "reason": "synthetic conformance reversal",
                "occurred_at": "2025-01-03T00:00:00Z",
            },
        )
        reversed_portfolio_call = call(
            "portfolio_context",
            {"account_id": account_id, "as_of": "2025-01-04T00:00:00Z"},
        )

        mismatch_statement = {
            "cash": {"CNY": "1"},
            "positions": {},
            "position_values": {},
            "position_total_by_currency": {},
            "total_by_currency": {"CNY": "1"},
        }
        mismatch_call = call(
            "investment_transaction_update",
            {
                "operation": "reconcile",
                "account_id": account_id,
                "as_of": "2025-01-04T00:00:00Z",
                "statement": mismatch_statement,
                "source_ref": "synthetic-mismatch",
            },
        )
        after_mismatch_call = call(
            "portfolio_context",
            {"account_id": account_id, "as_of": "2025-01-04T00:00:00Z"},
        )
        matched_statement = {
            "cash": {},
            "positions": {},
            "position_values": {},
            "position_total_by_currency": {},
            "total_by_currency": {},
        }
        matched_call = call(
            "investment_transaction_update",
            {
                "operation": "reconcile",
                "account_id": account_id,
                "as_of": "2025-01-05T00:00:00Z",
                "statement": matched_statement,
                "source_ref": "synthetic-match",
            },
        )
        continuity_missing_ref_call = call(
            "investment_transaction_update",
            {
                "operation": "continuity_confirm",
                "account_id": account_id,
                "confirmed_at": "2025-01-06T00:00:00Z",
                "reporting_commitment": True,
            },
        )
        continuity_call = call(
            "investment_transaction_update",
            {
                "operation": "continuity_confirm",
                "account_id": account_id,
                "confirmed_at": "2025-01-06T00:00:00Z",
                "user_confirmation_ref": "synthetic-continuity-confirmation",
                "reporting_commitment": True,
            },
        )
        continuity = _call_result(continuity_call)
        continuity_portfolio_call = call(
            "portfolio_context",
            {"account_id": account_id, "as_of": "2025-01-10T00:00:00Z"},
        )
        continuity_revoke_missing_ref_call = call(
            "investment_transaction_update",
            {"operation": "continuity_revoke", "reason": "missing reference fixture"},
        )
        continuity_revoke_call = call(
            "investment_transaction_update",
            {
                "operation": "continuity_revoke",
                "confirmation_id": continuity["id"],
                "reason": "synthetic continuity revocation",
            },
        )
        revoked_portfolio_call = call(
            "portfolio_context",
            {"account_id": account_id, "as_of": "2025-01-10T00:00:00Z"},
        )
    finally:
        proc.stdin.close()
        try:
            return_code = proc.wait(timeout=30)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=5)
            raise CompanionError("investment MCP conformance did not exit")
        if return_code != 0:
            stderr = proc.stderr.read() if proc.stderr else ""
            raise CompanionError(f"investment MCP conformance failed: {stderr}")

    tools_by_name = {item.get("name"): item for item in tools}
    return {
        "initialize": initialized,
        "tool_names": list(tools_by_name),
        "tools": tools_by_name,
        "home_result": _call_result(home_call),
        "home_call_error": _call_error(home_call),
        "context": {
            "draft": context_draft,
            "confirm": _call_result(context_confirm_call),
            "missing_reference_error": _call_error(context_missing_ref_call),
        },
        "ledger": {
            "before": _call_result(before_call),
            "pending_entry": pending,
            "pending_portfolio": _call_result(pending_portfolio_call),
            "missing_reference_error": _call_error(transaction_missing_ref_call),
            "confirmed_entry": _call_result(confirmed_call),
            "confirmed_portfolio": _call_result(confirmed_portfolio_call),
            "reversal_entry": _call_result(reversal_call),
            "reversed_portfolio": _call_result(reversed_portfolio_call),
        },
        "reconciliation": {
            "mismatch": _call_result(mismatch_call),
            "after_mismatch": _call_result(after_mismatch_call),
            "matched": _call_result(matched_call),
        },
        "continuity": {
            "missing_reference_error": _call_error(continuity_missing_ref_call),
            "confirmed": continuity,
            "portfolio": _call_result(continuity_portfolio_call),
            "revoke_missing_reference_error": _call_error(
                continuity_revoke_missing_ref_call
            ),
            "revoked": _call_result(continuity_revoke_call),
            "revoked_portfolio": _call_result(revoked_portfolio_call),
        },
    }


def evaluate_investment_conformance(observation: dict[str, Any]) -> dict[str, Any]:
    checks: list[dict[str, Any]] = []
    failures: list[dict[str, Any]] = []

    def check(identifier: str, passed: bool, failure: dict[str, Any]) -> None:
        checks.append({"id": identifier, "passed": bool(passed)})
        if not passed:
            failures.append(failure)

    check(
        "mcp.initialize",
        bool((observation.get("initialize") or {}).get("protocolVersion")),
        {"code": "mcp_initialize_failed"},
    )
    tools = observation.get("tools", {})
    home_schema = (tools.get("investment_home") or {}).get("inputSchema")
    check(
        "mcp.tools-list.investment-home",
        home_schema
        == {
            "type": "object",
            "properties": {},
            "required": [],
            "additionalProperties": False,
        },
        {"code": "missing_or_drifted_tool", "capability": "investment_home"},
    )
    check(
        "mcp.tools-list.portfolio-context",
        (tools.get("portfolio_context") or {}).get("inputSchema", {}).get(
            "additionalProperties"
        )
        is False,
        {"code": "missing_or_drifted_tool", "capability": "portfolio_context"},
    )

    def operations(name: str) -> set[str]:
        variants = (tools.get(name) or {}).get("inputSchema", {}).get("oneOf", [])
        return {
            variant.get("properties", {}).get("operation", {}).get("const")
            for variant in variants
        }

    check(
        "mcp.tools-list.context-update-variants",
        operations("investment_context_update") == {"draft", "confirm"},
        {
            "code": "missing_or_drifted_tool",
            "capability": "investment_context_update",
        },
    )
    check(
        "mcp.tools-list.transaction-update-variants",
        operations("investment_transaction_update")
        == {
            "account_create",
            "asset_register",
            "record",
            "confirm",
            "reverse",
            "reconcile",
            "continuity_confirm",
            "continuity_revoke",
        },
        {
            "code": "missing_or_drifted_tool",
            "capability": "investment_transaction_update",
        },
    )

    home = observation.get("home_result")
    health = home.get("production_health") if isinstance(home, dict) else None
    required_health = {"baseline", "workflows", "optional_enhancements", "incidents"}
    check(
        HOME_INVARIANT,
        isinstance(health, dict) and required_health <= set(health),
        {
            "code": "invariant_violation",
            "capability": "investment_home",
            "invariant": HOME_INVARIANT,
            "counterexample": "tools/call succeeded without required production_health",
        },
    )

    context = observation.get("context", {})
    ledger = observation.get("ledger", {})
    context_confirmed = context.get("confirm") or {}
    initial_portfolio = ledger.get("before") or {}
    check(
        CONTEXT_CONFIRMATION_INVARIANT,
        (context.get("draft") or {}).get("status") == "draft"
        and context_confirmed.get("status") == "current"
        and (initial_portfolio.get("investor") or {}).get("id")
        == context_confirmed.get("id"),
        {
            "code": "invariant_violation",
            "capability": "investment_context_update",
            "invariant": CONTEXT_CONFIRMATION_INVARIANT,
            "counterexample": "draft and confirmed Context states were not separated",
        },
    )

    pending_portfolio = ledger.get("pending_portfolio") or {}
    pending_state = pending_portfolio.get("portfolio", {})
    pending_entries = pending_portfolio.get("pending_transactions", [])
    check(
        PORTFOLIO_LEDGER_INVARIANT,
        initial_portfolio.get("truth") == "confirmed_ledger_replay"
        and pending_state.get("cash") == {}
        and [item.get("id") for item in pending_entries]
        == [(ledger.get("pending_entry") or {}).get("id")],
        {
            "code": "invariant_violation",
            "capability": "portfolio_context",
            "invariant": PORTFOLIO_LEDGER_INVARIANT,
            "counterexample": "pending Ledger Entry changed or appeared inside confirmed portfolio truth",
        },
    )
    confirmed_cash = (
        (ledger.get("confirmed_portfolio") or {}).get("portfolio", {}).get("cash")
    )
    reversed_cash = (
        (ledger.get("reversed_portfolio") or {}).get("portfolio", {}).get("cash")
    )
    reversal = ledger.get("reversal_entry") or {}
    check(
        TRANSACTION_CONFIRMATION_INVARIANT,
        (ledger.get("pending_entry") or {}).get("status") == "needs_confirmation"
        and (ledger.get("confirmed_entry") or {}).get("status") == "confirmed"
        and confirmed_cash == {"CNY": "100"}
        and reversal.get("status") == "confirmed"
        and reversal.get("entry_type") == "reversal"
        and reversed_cash == {"CNY": "0"},
        {
            "code": "invariant_violation",
            "capability": "investment_transaction_update",
            "invariant": TRANSACTION_CONFIRMATION_INVARIANT,
            "counterexample": "pending, confirmation or immutable reversal portfolio transition drifted",
        },
    )

    reconciliation = observation.get("reconciliation", {})
    mismatch = reconciliation.get("mismatch") or {}
    after_mismatch_cash = (
        (reconciliation.get("after_mismatch") or {}).get("portfolio", {}).get("cash")
    )
    check(
        RECONCILIATION_INVARIANT,
        mismatch.get("status") == "needs_review"
        and bool(mismatch.get("differences"))
        and mismatch.get("reconciliation", {}).get("full_scope_matched") is False
        and after_mismatch_cash == {"CNY": "0"}
        and (reconciliation.get("matched") or {}).get("status") == "matched",
        {
            "code": "invariant_violation",
            "capability": "investment_transaction_update",
            "invariant": RECONCILIATION_INVARIANT,
            "counterexample": "reconciliation difference auto-adjusted confirmed portfolio truth",
        },
    )

    continuity = observation.get("continuity", {})
    continuity_precision = (continuity.get("portfolio") or {}).get(
        "precision_boundary", {}
    )
    revoked_precision = (continuity.get("revoked_portfolio") or {}).get(
        "precision_boundary", {}
    )
    check(
        CONTINUITY_INVARIANT,
        (continuity.get("confirmed") or {}).get("status") == "active"
        and continuity_precision.get("current_broker_position_proven") is False
        and continuity_precision.get("ledger_position_continuity_supported") is True
        and continuity_precision.get("precise_position_advice_allowed") is True
        and (continuity.get("revoked") or {}).get("status") == "revoked"
        and revoked_precision.get("ledger_position_continuity_supported") is False
        and revoked_precision.get("precise_position_advice_allowed") is False,
        {
            "code": "invariant_violation",
            "capability": "investment_transaction_update",
            "invariant": CONTINUITY_INVARIANT,
            "counterexample": "Account Continuity masqueraded as broker proof or survived revocation",
        },
    )
    check(
        PORTFOLIO_TRUTH_INVARIANT,
        all(
            (value or {}).get("truth") == "confirmed_ledger_replay"
            for value in (
                ledger.get("before"),
                ledger.get("pending_portfolio"),
                ledger.get("confirmed_portfolio"),
                ledger.get("reversed_portfolio"),
                continuity.get("portfolio"),
                continuity.get("revoked_portfolio"),
            )
        ),
        {
            "code": "invariant_violation",
            "capability": "portfolio_context",
            "invariant": PORTFOLIO_TRUTH_INVARIANT,
            "counterexample": "portfolio truth or precision semantics were not explicit",
        },
    )

    reference_errors = [
        context.get("missing_reference_error"),
        ledger.get("missing_reference_error"),
        continuity.get("missing_reference_error"),
        continuity.get("revoke_missing_reference_error"),
    ]
    check(
        "mcp.required-confirmation-references.rejected/v1",
        all(
            isinstance(error, str) and error.startswith("capability.input.invalid:")
            for error in reference_errors
        ),
        {
            "code": "missing_negative_rejection",
            "capability": "personal_context_and_portfolio_ledger",
        },
    )
    return {
        "passed": not failures,
        "profile": "investment",
        "checks": checks,
        "failures": failures,
    }
