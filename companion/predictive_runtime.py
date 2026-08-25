from __future__ import annotations

from decimal import Decimal, InvalidOperation
import json
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from .foundation import CompanionError
from .timeutil import parse
from .tushare_adapter import TushareAdapter


def canonical_object_ready(data, object_id: str) -> bool:
    try:
        data.object_get(object_id, verify=True)
    except Exception:
        return False
    return True


def manifest_matches_cutoff_day(service, manifest_id: str, cutoff: str) -> bool:
    body = service.c.data.manifest_get(manifest_id)["manifest"]["manifest"]
    as_of = body.get("as_of")
    if not isinstance(as_of, str):
        return False
    try:
        source_day = parse(as_of).astimezone(ZoneInfo("Asia/Shanghai")).date()
    except (TypeError, ValueError):
        try:
            source_day = parse(f"{as_of[:10]}T00:00:00+08:00").date()
        except (TypeError, ValueError):
            return False
    return source_day == parse(cutoff).astimezone(ZoneInfo("Asia/Shanghai")).date()


def dated_object_refs(service, capability: str, cutoff) -> dict[str, str]:
    try:
        stream = service.c.data.stream_get("tushare", capability)
    except CompanionError:
        return {}
    result = {}
    batches = service.c.data.batch_list(stream["id"], "ready")
    for batch in sorted(batches, key=lambda item: (item.get("finished_at") or "", item["id"])):
        if not batch.get("finished_at") or parse(batch["finished_at"]) > cutoff:
            continue
        raw_day = batch.get("request_range", {}).get("trade_date")
        object_ids = batch.get("canonical_object_ids", [])
        if isinstance(raw_day, str) and len(raw_day) == 8 and raw_day.isdigit() and len(object_ids) == 1:
            if not canonical_object_ready(service.c.data, object_ids[0]):
                # A batch can remain immutable/ready after its object is later
                # quarantined by integrity verification.  Such an object is no
                # longer a usable research input and must not poison the run.
                continue
            result[f"{raw_day[:4]}-{raw_day[4:6]}-{raw_day[6:]}"] = object_ids[0]
    return result


def stock_adjusted_bars(service, cutoff: str, *, asset_ids: set[str]) -> list[dict[str, Any]]:
    """Stream frozen daily rows for only assets with unsettled signals."""
    if not asset_ids:
        return []
    instant = parse(cutoff)
    daily = dated_object_refs(service, "daily", instant)
    adjustments = dated_object_refs(service, "adj_factor", instant)
    dates = sorted(set(daily) & set(adjustments))[-21:]
    bars = []
    for day in dates:
        daily_rows = json.loads(service.c.data.object_read(daily[day]).decode("utf-8"))
        adj_payload = json.loads(service.c.data.object_read(adjustments[day]).decode("utf-8"))
        facts = adj_payload.get("facts") if isinstance(adj_payload, dict) else None
        if not isinstance(daily_rows, list) or not isinstance(facts, list):
            raise CompanionError("frozen stock settlement input contract is invalid")
        factors = {}
        for fact in facts:
            value = fact.get("value", {}) if isinstance(fact, dict) else {}
            asset = f"tushare:{value.get('ts_code')}"
            if asset in asset_ids:
                factors[asset] = _positive_decimal(value.get("adj_factor"), "adj_factor")
        for row in daily_rows:
            asset = str(row.get("asset_id") or "") if isinstance(row, dict) else ""
            if asset in asset_ids and asset in factors:
                close = _positive_decimal(row.get("close"), "close") * factors[asset]
                bars.append({"asset_id": asset, "date": day, "close": _decimal_text(close)})
        del daily_rows, adj_payload, facts, factors
    return bars


def publish_waiting_dependency(service, *, kind: str, schema_version: str, program_id: str, as_of: str, dependency: str, event_summary: str) -> dict[str, Any]:
    report = service.c.data.manifest_publish(kind=kind, schema_version=schema_version, manifest={
        "program_id": program_id, "as_of": as_of, "status": "waiting_upstream",
        "dependency": dependency, "candidates": [], "signals": [], "research_only": True,
        "not_a_recommendation": True, "model_tokens": 0,
    })
    return {"manifest_id": report["id"], "output_refs": [report["id"]], "material": True, "model_tokens": 0, "event_summary": event_summary}


def etf_bootstrap_days(calendar: list[dict[str, Any]], existing_days: set[str], day: str, *, lookback_sessions: int, per_run: int) -> list[str]:
    eligible = sorted({str(item.get("date") or "").replace("-", "") for item in calendar
                       if item.get("exchange") == "SSE" and item.get("is_open") in {True, 1, "1"}
                       and str(item.get("date") or "").replace("-", "") <= day})[-lookback_sessions:]
    return [value for value in eligible if value not in existing_days and value != day][:per_run]


def job_fund_data_bundle(service, context: dict[str, Any], *, lookback_sessions: int, bootstrap_per_run: int) -> dict[str, Any]:
    config = service.require_program()
    parameters = context["inputs"].get("parameters", {})
    if set(parameters) != {"program_id"} or parameters["program_id"] != config["program_id"]:
        raise CompanionError("ETF data bundle has invalid program parameters")
    cutoff = context["inputs"]["knowledge_cutoff"]
    day = parse(cutoff).astimezone(ZoneInfo("Asia/Shanghai")).strftime("%Y%m%d")
    calendar = service.c.quant_research._calendar_rows(parse(cutoff))
    existing = {value.replace("-", "") for value in dated_object_refs(service, "fund_daily", parse(cutoff))}
    bootstrap_days = etf_bootstrap_days(calendar, existing, day, lookback_sessions=lookback_sessions, per_run=bootstrap_per_run)
    requests = [("etf_basic", {}), ("fund_daily", {"trade_date": day}), ("fund_share", {"trade_date": day})]
    requests.extend(("fund_daily", {"trade_date": value}) for value in bootstrap_days)
    adapter = TushareAdapter(service.c, token_file=Path.home() / ".config" / "tushare" / "token")
    results, output_refs = [], []
    for index, (capability, params) in enumerate(requests):
        batch = adapter.ingest_canary(capability, params=params, account_scope=f"v6-etf:{config['program_id']}", ingestion_key=f"job-run:{context['job_run_id']}:{index}")
        results.append({"capability": capability, "batch_id": batch["id"], "status": batch["status"], "row_count": batch["row_count"], "raw_object_ids": batch["raw_object_ids"], "canonical_object_ids": batch["canonical_object_ids"], "error": batch.get("error")})
        output_refs.extend(batch["raw_object_ids"]); output_refs.extend(batch["canonical_object_ids"])
    ready = all(item["status"] in {"ready", "empty_valid"} for item in results)
    current_run_ids = {
        capability: [object_id for item in results if item["capability"] == capability for object_id in item["canonical_object_ids"]]
        for capability in ("etf_basic", "fund_daily", "fund_share")
    }
    # Preserve the frozen knowledge cutoff while admitting only the exact
    # immutable objects created by this job.  Using "now" here would also
    # admit unrelated concurrent ingestion and make replay nondeterministic.
    historical = {
        capability: service._historical_canonical_rows(
            capability, cutoff, include_object_ids=current_run_ids[capability]
        )
        for capability in current_run_ids
    }
    refs = [
        object_id
        for capability in historical
        for object_id in service._historical_canonical_object_ids(
            capability, cutoff, include_object_ids=current_run_ids[capability]
        )
    ]
    available = len({str(item.get("trade_date") or "") for item in historical["fund_daily"] if item.get("trade_date")})
    feature_id = universe_id = None; outcome_ids = []
    if ready and historical["etf_basic"] and historical["fund_daily"]:
        built = service.build_fund_universe_from_rows(program_id=config["program_id"], as_of=cutoff, etf_basic_rows=historical["etf_basic"], fund_daily_rows=historical["fund_daily"], fund_nav_rows=[], fund_share_rows=historical["fund_share"], source_refs=sorted(set(output_refs + refs)), lookback_sessions=lookback_sessions)
        universe_id, feature_id = built["universe"]["id"], built["features"]["id"]
        output_refs.extend([universe_id, feature_id])
        for signal_id in service._fund_signal_ids(config["program_id"], cutoff):
            outcome = service.capture_fund_signal_outcomes(program_id=config["program_id"], signal_manifest_id=signal_id, fund_daily_rows=historical["fund_daily"], observed_at=cutoff)
            outcome_ids.append(outcome["id"]); output_refs.append(outcome["id"])
    state = "ready" if feature_id and available >= lookback_sessions else "warming_up" if ready else "partial"
    report = service.c.data.manifest_publish(kind="v6_fund_data_bundle", schema_version="investment-companion.v6-fund-data-bundle/v1", manifest={"program_id": config["program_id"], "knowledge_cutoff": cutoff, "status": state, "requests": results, "bootstrap_days": bootstrap_days, "available_price_sessions": available, "required_price_sessions": lookback_sessions, "universe_manifest_id": universe_id, "feature_snapshot_manifest_id": feature_id, "signal_outcome_manifest_ids": outcome_ids, "research_only": True, "not_a_recommendation": True, "model_tokens": 0})
    return {"manifest_id": report["id"], "output_refs": [report["id"], *dict.fromkeys(output_refs)], "material": state != "ready", "model_tokens": 0, "event_summary": f"ETF data history is {state} ({available}/{lookback_sessions} sessions); not a forecast or Decision"}


def _positive_decimal(value: Any, field: str) -> Decimal:
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise CompanionError(f"{field} must be numeric") from exc
    if not result.is_finite() or result <= 0:
        raise CompanionError(f"{field} must be positive")
    return result


def _decimal_text(value: Decimal) -> str:
    return "0" if value == 0 else format(value.normalize(), "f")
