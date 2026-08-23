from __future__ import annotations

import json
import math
import re
from collections import Counter
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation, localcontext
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from .foundation import CompanionError, canonical, digest
from .db import row_dict, rows_dict
from .timeutil import iso, parse, utc_now
from .tushare_adapter import NORMALIZER_VERSION, TushareAdapter


CONTINUOUS_MODE = "v5_continuous_quant_research"
LEGACY_TRIAL_MODE = "v5_quant_experiment"
PROGRAM_SCHEMA = "investment-companion.v5-continuous-quant-research/v1"
DATA_HANDLER = "data.tushare_canary_bundle"
SCAN_HANDLER = "research.canary_market_scan"
REVIEW_HANDLER = "research.continuous_quant_review"
DATA_DEFINITION_NAME = "V5 Continuous Tushare Research Bundle"
SCAN_DEFINITION_NAME = "V5 Continuous Quant Market Scan"
REVIEW_DEFINITION_NAME = "V5 Continuous Quant Monthly Review"
LEGACY_DEFINITION_NAMES = {
    "V5 Controlled Tushare Canary Bundle",
    "V5 Controlled Quant Market Scan",
}
SCAN_MANIFEST_KIND = "v5_canary_quant_scan"
TARGET_MANIFEST_KIND = "v5_canary_target_weights"
REVIEW_MANIFEST_KIND = "v5_continuous_quant_review"
STRATEGY_ID = "v5-continuous-mainboard-momentum-v1"
LOOKBACK_SESSIONS = 20
TOP_K = 10
MIN_RESEARCH_PERSISTENCE = 3
RESEARCH_SHORTLIST_MAX = 3
MIN_DIRECTIONAL_REVIEW_OBSERVATIONS = 10
MIN_PRICE = Decimal("3")
MAX_DAILY_VOLATILITY = Decimal("0.045")
LIQUIDITY_KEEP_FRACTION = Decimal("0.40")
MIN_CANDIDATE_CHANGES = 3

REQUEST_FIELDS = {
    "daily": ["ts_code", "trade_date", "open", "high", "low", "close", "vol", "amount"],
    "adj_factor": ["ts_code", "trade_date", "adj_factor"],
    "trade_cal": ["exchange", "cal_date", "is_open", "pretrade_date"],
}


class ContinuousQuantResearch:
    """Continuous real-data research below the formal V4 promotion gates.

    It deliberately does not publish a DatasetSnapshot, StrategyVersion, Decision,
    Shadow Book, Execution, or Ledger fact.  The output is an immutable research
    lead produced with zero model tokens; Primary Codex decides whether any lead
    deserves a separate, evidence-backed Opportunity.
    """

    def __init__(self, companion):
        self.c = companion
        self.db = companion.db
        self.c.jobs.register_handler(DATA_HANDLER, "1", self._job_canary_bundle)
        self.c.jobs.register_handler(SCAN_HANDLER, "1", self._job_market_scan)
        self.c.jobs.register_handler(REVIEW_HANDLER, "1", self._job_monthly_review)

    def require_program(self) -> dict[str, Any]:
        feature = self.c.jobs.feature_require("v4_live_data_canary")
        config = feature.get("config", {})
        required = {
            "mode",
            "program_id",
            "user_approval_ref",
            "started_at",
            "max_requests_per_job",
        }
        if not isinstance(config, dict) or set(config) != required:
            raise CompanionError(
                "continuous quant research config must be exactly: " + str(sorted(required))
            )
        if config["mode"] != CONTINUOUS_MODE:
            raise CompanionError("continuous quant research mode is not enabled")
        for field in ("program_id", "user_approval_ref"):
            if not isinstance(config[field], str) or not config[field].strip():
                raise CompanionError(f"continuous quant research requires config.{field}")
        started, now = parse(config["started_at"]), utc_now()
        if started > now + timedelta(minutes=5):
            raise CompanionError("continuous quant research started_at is in the future")
        requests = config["max_requests_per_job"]
        if isinstance(requests, bool) or not isinstance(requests, int) or not 1 <= requests <= 3:
            raise CompanionError("continuous quant research max_requests_per_job must be within 1..3")
        return config

    def require_trial(self) -> dict[str, Any]:
        """Compatibility alias for older callers; no time-boxed trial remains."""

        return self.require_program()

    def _migrate_legacy_config(self) -> dict[str, Any] | None:
        feature = self.c.jobs.feature_get("v4_live_data_canary")
        config = feature.get("config", {})
        if not feature["enabled"] or not isinstance(config, dict):
            return None
        if config.get("mode") != LEGACY_TRIAL_MODE:
            return None
        required = {
            "mode",
            "trial_id",
            "user_approval_ref",
            "started_at",
            "expires_at",
            "max_trading_days",
            "max_requests_per_job",
        }
        if set(config) != required:
            raise CompanionError("legacy quant experiment config cannot be migrated safely")
        migrated = {
            "mode": CONTINUOUS_MODE,
            "program_id": config["trial_id"],
            "user_approval_ref": config["user_approval_ref"],
            "started_at": config["started_at"],
            "max_requests_per_job": config["max_requests_per_job"],
        }
        self.c.jobs.feature_set(
            "v4_live_data_canary",
            True,
            config=migrated,
            actor="v5-continuous-quant-migration",
            reason="remove time-boxed trial; preserve real-world research lineage",
        )
        return migrated

    def bootstrap(self, *, activate: bool = False) -> dict[str, Any]:
        definitions = self._ensure_definitions()
        streams = [self._ensure_canary_stream(capability) for capability in REQUEST_FIELDS]
        schedules: list[dict[str, Any]] = []
        if activate:
            self.c.jobs.feature_require("v4_jobs")
            self._migrate_legacy_config()
            config = self.require_program()
            definitions = [
                self.c.jobs.definition_set_status(
                    item["id"],
                    "active",
                    reason=f"continuous quant research {config['program_id']}",
                )
                if item["status"] != "active"
                else item
                for item in definitions
            ]
            schedules = self._ensure_schedules(config, definitions)
            self._archive_legacy_definitions({item["id"] for item in definitions})
        return {
            "schema": PROGRAM_SCHEMA,
            "activated": activate,
            "definitions": definitions,
            "streams": streams,
            "schedules": schedules,
            "boundaries": self._boundaries(),
        }

    def status(self) -> dict[str, Any]:
        feature = self.c.jobs.feature_get("v4_live_data_canary")
        config = feature.get("config", {})
        state = "disabled"
        error = None
        if feature["enabled"]:
            try:
                self.require_program()
                state = "active"
            except CompanionError as exc:
                state = "migration_required" if config.get("mode") == LEGACY_TRIAL_MODE else "misconfigured"
                error = str(exc)
        all_definitions = [
            item
            for item in self.c.jobs.definition_list()
            if item["handler"] in {DATA_HANDLER, SCAN_HANDLER, REVIEW_HANDLER}
        ]
        definitions = [item for item in all_definitions if item["status"] != "archived"]
        schedules = [
            item
            for item in self.c.schedule_list()
            if item.get("origin", {}).get("system") in {CONTINUOUS_MODE, LEGACY_TRIAL_MODE}
        ]
        program_id = self._config_program_id(config)
        latest = self._latest_scan(program_id)
        latest_review = self._latest_review(program_id)
        with self.db.connect() as con:
            job_counts = {
                row["status"]: row["count"]
                for row in con.execute(
                    "SELECT status,COUNT(*) AS count FROM job_runs "
                    "WHERE job_definition_id IN (SELECT id FROM job_definitions WHERE handler IN (?,?,?)) "
                    "GROUP BY status",
                    (DATA_HANDLER, SCAN_HANDLER, REVIEW_HANDLER),
                ).fetchall()
            }
        return {
            "schema": PROGRAM_SCHEMA,
            "state": state,
            "error": error,
            "feature_enabled": feature["enabled"],
            "program": config,
            "definitions": [
                {"id": item["id"], "handler": item["handler"], "status": item["status"]}
                for item in definitions
            ],
            "archived_definition_count": len(all_definitions) - len(definitions),
            "schedules": [
                {
                    "id": item["id"],
                    "name": item["name"],
                    "status": item["status"],
                    "next_run_at": item["next_run_at"],
                    "role": item.get("origin", {}).get("role"),
                }
                for item in schedules
            ],
            "job_counts": job_counts,
            "latest_scan": self._scan_summary(latest) if latest else None,
            "latest_review": self._review_summary(latest_review) if latest_review else None,
            "reporting": {
                "daily": "latest scan is an input to the close review; it does not wait for the monthly review",
                "monthly": "a deterministic forward review always wakes Primary for a user-facing report",
            },
            "boundaries": self._boundaries(),
        }

    def scan_get(self, manifest_id: str) -> dict[str, Any]:
        item = self.c.data.manifest_get(manifest_id, verify=True)
        if item["kind"] != SCAN_MANIFEST_KIND:
            raise CompanionError("manifest is not a continuous quant scan")
        return item

    def review_get(self, manifest_id: str) -> dict[str, Any]:
        item = self.c.data.manifest_get(manifest_id, verify=True)
        if item["kind"] != REVIEW_MANIFEST_KIND:
            raise CompanionError("manifest is not a continuous quant review")
        return item

    def prepare_backfill(self, *, through_date: str, sessions: int = LOOKBACK_SESSIONS + 1) -> dict[str, Any]:
        config = self.require_program()
        self.c.jobs.feature_require("v4_jobs")
        definitions = self._ensure_definitions()
        data_definition = next(item for item in definitions if item["handler"] == DATA_HANDLER)
        if data_definition["status"] != "active":
            raise CompanionError("continuous research data JobDefinition is not active")
        through = self._compact_day(through_date, "through_date")
        if isinstance(sessions, bool) or not isinstance(sessions, int) or not 21 <= sessions <= 61:
            raise CompanionError("backfill sessions must be within 21..61")
        cutoff = iso()
        calendar = self._calendar_rows(parse(cutoff))
        through_iso = self._iso_day(through)
        covered = bool(calendar and min(row["date"] for row in calendar) <= through_iso <= max(row["date"] for row in calendar))
        if not covered:
            start = (date.fromisoformat(through_iso) - timedelta(days=120)).strftime("%Y%m%d")
            end = (date.fromisoformat(through_iso) + timedelta(days=60)).strftime("%Y%m%d")
            inputs = {
                "refs": [],
                "parameters": {
                    "program_id": config["program_id"],
                    "requests": [
                        {
                            "capability": "trade_cal",
                            "params": {"exchange": "SSE", "start_date": start, "end_date": end},
                            "fields": REQUEST_FIELDS["trade_cal"],
                        }
                    ],
                },
                "knowledge_cutoff": cutoff,
            }
            job = self.c.jobs.enqueue_new_parent(
                definition_id=data_definition["id"],
                inputs=inputs,
                idempotency_key=f"{CONTINUOUS_MODE}:{config['program_id']}:calendar:{start}:{end}",
            )
            return {
                "schema": PROGRAM_SCHEMA,
                "phase": "calendar_enqueued",
                "job_run_ids": [job["id"]],
                "next": "run the deterministic worker, then call prepare_backfill again",
            }
        selected = [
            day.replace("-", "")
            for day in self.c.market_calendar.sessions_through(
                calendar, through=through_iso, count=sessions
            )
        ]
        jobs = []
        skipped = []
        for day in selected:
            missing = [
                capability
                for capability in ("daily", "adj_factor")
                if not self._has_ready_date(capability, day, parse(cutoff))
            ]
            if not missing:
                skipped.append(day)
                continue
            requests = [
                {
                    "capability": capability,
                    "params": {"trade_date": day},
                    "fields": REQUEST_FIELDS[capability],
                }
                for capability in missing
            ]
            prefix = f"{CONTINUOUS_MODE}:{config['program_id']}:backfill:{day}"
            with self.db.connect() as con:
                attempt = int(
                    con.execute(
                        "SELECT COUNT(*) FROM runs WHERE idempotency_key LIKE ?", (prefix + "%",)
                    ).fetchone()[0]
                )
            job = self.c.jobs.enqueue_new_parent(
                definition_id=data_definition["id"],
                inputs={
                    "refs": [],
                    "parameters": {"program_id": config["program_id"], "requests": requests},
                    "knowledge_cutoff": cutoff,
                },
                idempotency_key=f"{prefix}:attempt-{attempt + 1}",
            )
            jobs.append(job["id"])
        return {
            "schema": PROGRAM_SCHEMA,
            "phase": "backfill_enqueued" if jobs else "backfill_ready",
            "through_date": through_iso,
            "sessions": selected,
            "job_run_ids": jobs,
            "already_ready": skipped,
        }

    def _ensure_definitions(self) -> list[dict[str, Any]]:
        common_input = {
            "type": "object",
            "required": ["refs", "parameters", "knowledge_cutoff"],
            "properties": {
                "refs": {"type": "array"},
                "parameters": {"type": "object"},
                "knowledge_cutoff": {"type": "string"},
            },
            "additionalProperties": False,
        }
        common_output = {
            "type": "object",
            "required": ["manifest_id", "output_refs", "material", "model_tokens"],
            "properties": {
                "manifest_id": {"type": "string"},
                "output_refs": {"type": "array", "items": {"type": "string"}},
                "material": {"type": "boolean"},
                "model_tokens": {"type": "integer"},
            },
            "additionalProperties": True,
        }
        specifications = [
            {
                "name": DATA_DEFINITION_NAME,
                "handler": DATA_HANDLER,
                "budget": {
                    "max_wall_seconds": 240,
                    "max_cpu_seconds": 90,
                    "max_memory_mb": 768,
                    "max_input_bytes": 100_000,
                    "max_output_bytes": 500_000,
                    "lease_seconds": 360,
                    "network": "tushare_official",
                    "model_tokens": 0,
                },
            },
            {
                "name": SCAN_DEFINITION_NAME,
                "handler": SCAN_HANDLER,
                "budget": {
                    "max_wall_seconds": 300,
                    "max_cpu_seconds": 240,
                    "max_memory_mb": 1536,
                    "max_input_bytes": 100_000,
                    "max_output_bytes": 1_000_000,
                    "lease_seconds": 600,
                    "network": "deny",
                    "model_tokens": 0,
                },
            },
            {
                "name": REVIEW_DEFINITION_NAME,
                "handler": REVIEW_HANDLER,
                "budget": {
                    "max_wall_seconds": 180,
                    "max_cpu_seconds": 120,
                    "max_memory_mb": 768,
                    "max_input_bytes": 100_000,
                    "max_output_bytes": 1_000_000,
                    "lease_seconds": 300,
                    "network": "deny",
                    "model_tokens": 0,
                },
            },
        ]
        by_name = {item["name"]: item for item in self.c.jobs.definition_list()}
        result = []
        for specification in specifications:
            item = by_name.get(specification["name"])
            if item:
                if (
                    item["handler"] != specification["handler"]
                    or item["handler_version"] != "1"
                    or canonical(item["resource_budget"]) != canonical(specification["budget"])
                ):
                    raise CompanionError(f"continuous quant JobDefinition drift: {item['name']}")
            else:
                item = self.c.jobs.definition_create(
                    name=specification["name"],
                    handler=specification["handler"],
                    handler_version="1",
                    status="inactive",
                    input_schema=common_input,
                    output_schema=common_output,
                    resource_budget=specification["budget"],
                    config={"continuous_research": True},
                )
            result.append(item)
        return result

    def _archive_legacy_definitions(self, keep_ids: set[str]) -> None:
        for item in self.c.jobs.definition_list():
            if item["id"] in keep_ids or item["name"] not in LEGACY_DEFINITION_NAMES:
                continue
            if item["status"] != "archived":
                self.c.jobs.definition_set_status(
                    item["id"],
                    "archived",
                    actor="v5-continuous-quant-migration",
                    reason="superseded by continuous quant research definition",
                )

    def _ensure_canary_stream(self, capability: str) -> dict[str, Any]:
        try:
            stream = self.c.data.stream_get("tushare", capability)
        except CompanionError:
            return self.c.data.stream_configure(
                provider="tushare",
                capability=capability,
                schema_version=NORMALIZER_VERSION,
                config={"mode": CONTINUOUS_MODE, "research_only": True},
                status="canary",
            )
        if stream["schema_version"] != NORMALIZER_VERSION:
            raise CompanionError(f"Tushare {capability} stream uses another normalizer")
        if stream["status"] == "active":
            raise CompanionError(f"continuous research refuses an unqualified active stream: {capability}")
        if stream["status"] in {"paused", "blocked", "inactive"}:
            stream = self.c.data.stream_set_status("tushare", capability, "canary")
        if stream["status"] != "canary":
            raise CompanionError(f"continuous research requires a qualification stream: {capability}")
        return stream

    def _ensure_schedules(
        self, config: dict[str, Any], definitions: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        by_handler = {item["handler"]: item for item in definitions}
        common_policy = {
            "continuous_research": True,
            "no_broker": True,
            "no_direct_decision": True,
            "notify": "material_only",
        }
        specifications = [
            {
                "role": "data",
                "kind": "maintenance",
                "name": "V5 持续量化研究：收盘数据",
                "mission": "持续采集当日 Tushare 日线和复权因子，形成不可变、可审计的研究输入。",
                "cadence": {
                    "type": "local_time",
                    "at": "17:20",
                    "timezone": "Asia/Shanghai",
                    "weekdays": [0, 1, 2, 3, 4],
                },
                "scope": {
                    "refs": [],
                    "parameters": {
                        "program_id": config["program_id"],
                        "requests": [
                            {
                                "capability": capability,
                                "params": {"trade_date": "$RUN_DATE"},
                                "fields": REQUEST_FIELDS[capability],
                            }
                            for capability in ("daily", "adj_factor")
                        ],
                    },
                },
                "policy": {**common_policy, "notify": "exceptions_only"},
                "definition": by_handler[DATA_HANDLER],
            },
            {
                "role": "scan",
                "kind": "maintenance",
                "name": "V5 持续量化研究：机会扫描",
                "mission": "用冻结输入运行透明量化基线、记录下一交易日结果，并把候选变化与持续性提供给当日复盘。",
                "cadence": {
                    "type": "local_time",
                    "at": "18:10",
                    "timezone": "Asia/Shanghai",
                    "weekdays": [0, 1, 2, 3, 4],
                },
                "scope": {"refs": [], "parameters": {"program_id": config["program_id"]}},
                "policy": {**common_policy, "daily_brief_input": True},
                "definition": by_handler[SCAN_HANDLER],
            },
            {
                "role": "monthly_review",
                "kind": "review",
                "name": "V5 持续量化研究：月度严格前向复盘",
                "mission": "每月严格复核真实运行期的完整扫描、前向结果、数据可靠性和审计完整性；无论结论好坏都向用户报告，并提出继续、深化或修改建议。",
                "cadence": self._monthly_review_cadence(config["started_at"]),
                "scope": {"refs": [], "parameters": {"program_id": config["program_id"]}},
                "policy": {
                    **common_policy,
                    "notify": "every_successful_run",
                    "report_every_successful_run": True,
                    "strict_forward_review": True,
                },
                "definition": by_handler[REVIEW_HANDLER],
            },
        ]
        existing = self.c.schedule_list()
        saved = []
        for specification in specifications:
            origin = {
                "system": CONTINUOUS_MODE,
                "program_id": config["program_id"],
                "role": specification["role"],
                "user_approval_ref": config["user_approval_ref"],
            }
            item = next(
                (
                    schedule
                    for schedule in existing
                    if schedule.get("origin", {}).get("system")
                    in {CONTINUOUS_MODE, LEGACY_TRIAL_MODE}
                    and self._origin_program_id(schedule.get("origin", {}))
                    == config["program_id"]
                    and schedule.get("origin", {}).get("role") == specification["role"]
                    and schedule.get("status") != "archived"
                ),
                None,
            )
            expected = {
                "name": specification["name"],
                "mission": specification["mission"],
                "cadence": specification["cadence"],
                "scope": specification["scope"],
                "policy": specification["policy"],
                "origin": origin,
                "dispatch_type": "deterministic_pipeline",
                "job_definition_id": specification["definition"]["id"],
            }
            if item:
                observed = {key: item.get(key) for key in expected}
                if canonical(observed) != canonical(expected):
                    item = self.c.schedule_patch(
                        item["id"],
                        item["version"],
                        expected,
                        actor="v5-continuous-quant-migration",
                        reason="remove trial limits and adopt continuous research policy",
                    )
                if item["status"] in {"paused", "expired"}:
                    item = self.c.schedule_set_status(
                        item["id"],
                        "active",
                        actor="v5-continuous-quant-migration",
                        reason="continuous research activation",
                    )
            else:
                item = self.c.schedule_create(
                    name=specification["name"],
                    kind=specification["kind"],
                    mission=specification["mission"],
                    cadence=specification["cadence"],
                    scope=specification["scope"],
                    policy=specification["policy"],
                    origin=origin,
                    timezone="Asia/Shanghai",
                    dispatch_type="deterministic_pipeline",
                    job_definition_id=specification["definition"]["id"],
                )
            saved.append(item)
        return saved

    @staticmethod
    def _monthly_review_cadence(started_at: str) -> dict[str, Any]:
        local = parse(started_at).astimezone(ZoneInfo("Asia/Shanghai"))
        year = local.year + (1 if local.month == 12 else 0)
        month = 1 if local.month == 12 else local.month + 1
        first_review = datetime(year, month, 19, 19, 0, tzinfo=ZoneInfo("Asia/Shanghai"))
        return {
            "type": "monthly",
            "day": 19,
            "at": "19:00",
            "timezone": "Asia/Shanghai",
            "not_before": iso(first_review),
        }

    def _job_canary_bundle(self, context: dict[str, Any]) -> dict[str, Any]:
        config = self.require_program()
        parameters = context["inputs"].get("parameters", {})
        if self._parameter_program_id(parameters) != config["program_id"]:
            raise CompanionError("data bundle program_id differs from the enabled research program")
        requests = parameters.get("requests")
        if (
            not isinstance(requests, list)
            or not requests
            or len(requests) > config["max_requests_per_job"]
        ):
            raise CompanionError("data bundle request count exceeds the per-job request budget")
        adapter = TushareAdapter(self.c, token_file=Path.home() / ".config" / "tushare" / "token")
        due = parse(context["inputs"]["knowledge_cutoff"]).astimezone(ZoneInfo("Asia/Shanghai"))
        replacements = {"$RUN_DATE": due.strftime("%Y%m%d")}
        results = []
        output_refs: list[str] = []
        for index, request in enumerate(requests):
            capability, params, fields = self._validate_request(request, replacements)
            try:
                batch = adapter.ingest_canary(
                    capability,
                    params=params,
                    fields=fields,
                    account_scope=f"continuous-research:{config['program_id']}",
                    ingestion_key=f"job-run:{context['job_run_id']}:{index}",
                )
                results.append(
                    {
                        "capability": capability,
                        "params": params,
                        "batch_id": batch["id"],
                        "status": batch["status"],
                        "row_count": batch["row_count"],
                        "raw_object_ids": batch["raw_object_ids"],
                        "canonical_object_ids": batch["canonical_object_ids"],
                        "error": batch.get("error"),
                    }
                )
                output_refs.extend(batch["raw_object_ids"])
                output_refs.extend(batch["canonical_object_ids"])
            except Exception as exc:
                results.append(
                    {
                        "capability": capability,
                        "params": params,
                        "status": "failed",
                        "row_count": 0,
                        "raw_object_ids": [],
                        "canonical_object_ids": [],
                        "error": f"{type(exc).__name__}: {exc}",
                    }
                )
        successful = {"ready", "empty_valid"}
        status = "ready" if all(item["status"] in successful for item in results) else "partial"
        report = self.c.data.manifest_publish(
            kind="v5_canary_data_bundle",
            schema_version="investment-companion.v5-continuous-data-bundle/v1",
            manifest={
                "program_id": config["program_id"],
                "job_run_id": context["job_run_id"],
                "knowledge_cutoff": context["inputs"]["knowledge_cutoff"],
                "status": status,
                "requests": results,
                "research_only": True,
                "model_tokens": 0,
                "forbidden_downstream": ["decision", "execution", "ledger", "broker"],
            },
        )
        return {
            "manifest_id": report["id"],
            "output_refs": [report["id"], *dict.fromkeys(output_refs)],
            "material": status != "ready",
            "model_tokens": 0,
            "event_summary": f"Continuous Tushare research bundle is {status}; Primary review required",
        }

    def _job_market_scan(self, context: dict[str, Any]) -> dict[str, Any]:
        config = self.require_program()
        parameters = context["inputs"].get("parameters", {})
        if set(parameters) not in ({"program_id"}, {"trial_id"}):
            raise CompanionError("market scan accepts only program_id")
        if self._parameter_program_id(parameters) != config["program_id"]:
            raise CompanionError("market scan program_id differs from the enabled research program")
        cutoff = parse(context["inputs"]["knowledge_cutoff"])
        daily = self._dated_payloads("daily", cutoff)
        adjustments = self._dated_payloads("adj_factor", cutoff)
        common_dates = sorted(set(daily) & set(adjustments))
        input_ids = [
            item["object_id"] for day in common_dates for item in (daily[day], adjustments[day])
        ]
        if len(common_dates) < LOOKBACK_SESSIONS + 1:
            return self._publish_nonready_scan(
                context,
                config,
                "warming_up",
                {
                    "available_sessions": len(common_dates),
                    "required_sessions": LOOKBACK_SESSIONS + 1,
                },
                input_ids,
            )
        selected_dates = common_dates[-(LOOKBACK_SESSIONS + 1) :]
        as_of = selected_dates[-1]
        latest = self._latest_scan(config["program_id"])
        if latest and self._scan_body(latest).get("as_of") == as_of:
            return self._publish_nonready_scan(
                context,
                config,
                "no_new_session",
                {"as_of": as_of, "previous_manifest_id": latest["id"]},
                input_ids,
            )
        previous = self._latest_scan(config["program_id"], before_date=as_of)
        calendar = self._calendar_rows(cutoff)
        effective_on = self.c.market_calendar.next_open_session(calendar, after=as_of)
        if effective_on is None:
            return self._publish_nonready_scan(
                context,
                config,
                "waiting_calendar",
                {"as_of": as_of},
                input_ids,
            )
        forward_observation_eligible = self._generated_before_effective_open(
            context["inputs"]["knowledge_cutoff"], effective_on
        )
        bars, histories, exclusions, source_ids = self._build_adjusted_bars(
            selected_dates, daily, adjustments
        )
        latest_assets = sorted(histories)
        preliminary: list[str] = []
        eligibility: dict[str, dict[str, Any]] = {}
        metrics: dict[str, dict[str, Decimal]] = {}
        for asset in latest_assets:
            history = histories[asset]
            reasons = list(exclusions.get(asset, []))
            if not self._is_mainboard(asset):
                reasons.append("outside_mainboard_baseline")
            if len(history) != LOOKBACK_SESSIONS + 1 or history[-1][0] != as_of:
                reasons.append("incomplete_21_session_history")
            if not reasons:
                closes = [item[1] for item in history]
                amounts = [item[2] for item in history]
                if history[-1][3] < MIN_PRICE:
                    reasons.append("price_below_floor")
                one_day = closes[-1] / closes[-2] - Decimal("1")
                volatility = self._volatility(closes)
                if abs(one_day) >= Decimal("0.095"):
                    reasons.append("latest_limit_like_move")
                if volatility > MAX_DAILY_VOLATILITY:
                    reasons.append("volatility_above_baseline")
                metrics[asset] = {
                    "one_day_return": one_day,
                    "volatility": volatility,
                    "mean_amount": sum(amounts) / Decimal(len(amounts)),
                    "latest_adjusted_close": closes[-1],
                }
            if reasons:
                eligibility[asset] = {"eligible": False, "reasons": sorted(set(reasons))}
            else:
                preliminary.append(asset)
        keep_count = min(
            len(preliminary),
            max(100, int(math.ceil(len(preliminary) * float(LIQUIDITY_KEEP_FRACTION)))),
        )
        liquid = set(
            sorted(preliminary, key=lambda asset: (-metrics[asset]["mean_amount"], asset))[:keep_count]
        )
        for asset in preliminary:
            if asset not in liquid:
                eligibility[asset] = {"eligible": False, "reasons": ["outside_liquidity_band"]}
            else:
                eligibility[asset] = {"eligible": True, "reasons": []}
        lineage_hash = digest(
            "v5-continuous-scan-input/v1",
            config["program_id"],
            selected_dates,
            [self.c.data.object_get(object_id)["content_hash"] for object_id in source_ids],
        )
        target = self.c.quant.cross_sectional_momentum(
            as_of=as_of,
            effective_on=effective_on,
            dataset_snapshot_id=f"canary:{lineage_hash}",
            strategy_version_id=STRATEGY_ID,
            universe=latest_assets,
            bars=bars,
            lookback_sessions=LOOKBACK_SESSIONS,
            top_k=TOP_K,
            eligibility=eligibility,
            cash_weight="0",
        )
        target_manifest = self.c.data.manifest_publish(
            kind=TARGET_MANIFEST_KIND,
            schema_version=target["schema"],
            manifest={
                **target,
                "program_id": config["program_id"],
                "research_only": True,
                "not_a_decision": True,
            },
        )
        denominator = {item["asset_id"]: item for item in target["denominator"]}
        selected_assets = [item["asset_id"] for item in target["weights"]]
        prior_ready_scans = [
            item
            for item in self._scan_manifests(config["program_id"])
            if self._scan_body(item).get("status") == "ready"
            and self._scan_body(item).get("as_of", "") < as_of
        ]
        candidates = []
        for asset in selected_assets:
            values = metrics[asset]
            consecutive = 1
            for prior in reversed(prior_ready_scans):
                prior_assets = {
                    item.get("asset_id")
                    for item in self._scan_body(prior).get("candidates", [])
                    if isinstance(item, dict)
                }
                if asset not in prior_assets:
                    break
                consecutive += 1
            candidates.append(
                {
                    "asset_id": asset,
                    "rank": denominator[asset]["rank"],
                    "momentum_20d": denominator[asset]["score"],
                    "one_day_return": self._decimal_text(values["one_day_return"]),
                    "volatility_20d": self._decimal_text(values["volatility"]),
                    "mean_amount_provider_units": self._decimal_text(values["mean_amount"]),
                    "signal_adjusted_close": self._decimal_text(values["latest_adjusted_close"]),
                    "consecutive_scan_appearances": consecutive,
                }
            )
        forward = self._forward_observation(previous, histories, as_of) if previous else None
        outcomes = self._prior_outcomes(config["program_id"])
        if forward:
            outcomes.append(forward)
        scorecard = self._forward_scorecard(outcomes)
        previous_candidates = set(
            item["asset_id"] for item in self._scan_body(previous).get("candidates", [])
        ) if previous else set()
        current_candidates = set(selected_assets)
        candidate_changes = len(previous_candidates ^ current_candidates)
        candidate_delta = {
            "entered": sorted(current_candidates - previous_candidates),
            "exited": sorted(previous_candidates - current_candidates),
            "persisted": sorted(current_candidates & previous_candidates),
        }
        research_shortlist = [
            {
                "asset_id": item["asset_id"],
                "rank": item["rank"],
                "consecutive_scan_appearances": item["consecutive_scan_appearances"],
                "trigger": f"top_5_for_at_least_{MIN_RESEARCH_PERSISTENCE}_consecutive_scans",
            }
            for item in candidates
            if item["rank"] <= 5
            and item["consecutive_scan_appearances"] >= MIN_RESEARCH_PERSISTENCE
        ][:RESEARCH_SHORTLIST_MAX]
        material = (
            previous is None
            or candidate_changes >= MIN_CANDIDATE_CHANGES
            or bool(research_shortlist)
            or (
                scorecard["observations"] > 0
                and scorecard["observations"] % 5 == 0
            )
        )
        exclusion_counts = Counter(
            reason for item in eligibility.values() for reason in item.get("reasons", [])
        )
        report_body = {
            "program_id": config["program_id"],
            "job_run_id": context["job_run_id"],
            "status": "ready",
            "as_of": as_of,
            "effective_on": effective_on,
            "forward_observation_eligible": forward_observation_eligible,
            "knowledge_cutoff": context["inputs"]["knowledge_cutoff"],
            "strategy": {
                "id": STRATEGY_ID,
                "method": "cross_sectional_momentum",
                "lookback_sessions": LOOKBACK_SESSIONS,
                "top_k": TOP_K,
                "universe": "Shanghai/Shenzhen main board code baseline",
                "filters": {
                    "min_price": str(MIN_PRICE),
                    "max_daily_volatility": str(MAX_DAILY_VOLATILITY),
                    "liquidity_keep_fraction": str(LIQUIDITY_KEEP_FRACTION),
                    "latest_limit_like_move_excluded": True,
                },
            },
            "input_lineage_hash": lineage_hash,
            "input_object_ids": source_ids,
            "input_content_hashes": [
                self.c.data.object_get(object_id)["content_hash"] for object_id in source_ids
            ],
            "session_dates": selected_dates,
            "universe_count": len(latest_assets),
            "eligible_count": sum(1 for value in eligibility.values() if value["eligible"]),
            "exclusion_counts": dict(sorted(exclusion_counts.items())),
            "target_manifest_id": target_manifest["id"],
            "candidates": candidates,
            "candidate_changes": candidate_changes,
            "candidate_delta": candidate_delta,
            "research_shortlist": research_shortlist,
            "research_trigger_is_not_a_recommendation": True,
            "forward_observation": forward,
            "forward_scorecard": scorecard,
            "material": material,
            "research_only": True,
            "not_a_recommendation": True,
            "not_a_decision": True,
            "model_tokens": 0,
            "limitations": [
                "canary_data_not_G1_qualified",
                "code_based_mainboard_universe_is_not_point_in_time_official_membership",
                "st_delisting_and_listing_age_filters_are_not_yet_point_in_time_qualified",
                "provider_amount_units_are_used_only_for_cross_sectional_ranking",
                "short_forward_observation_is_not_alpha_evidence",
                "no_costed_portfolio_or_execution_claim",
                *(
                    []
                    if forward_observation_eligible
                    else ["scan_generated_after_effective_session_open_forward_observation_excluded"]
                ),
            ],
        }
        report = self.c.data.manifest_publish(
            kind=SCAN_MANIFEST_KIND,
            schema_version="investment-companion.v5-continuous-quant-scan/v1",
            manifest=report_body,
        )
        return {
            "manifest_id": report["id"],
            "output_refs": [report["id"], target_manifest["id"], *source_ids],
            "material": material,
            "model_tokens": 0,
            "event_summary": (
                f"Continuous quant scan {as_of}: {len(candidates)} leads, "
                f"{len(research_shortlist)} persistent research triggers, "
                f"{scorecard['observations']} forward observations; not an action recommendation"
            ),
        }

    def _publish_nonready_scan(
        self,
        context: dict[str, Any],
        config: dict[str, Any],
        status: str,
        details: dict[str, Any],
        input_ids: list[str],
    ) -> dict[str, Any]:
        report = self.c.data.manifest_publish(
            kind=SCAN_MANIFEST_KIND,
            schema_version="investment-companion.v5-continuous-quant-scan/v1",
            manifest={
                "program_id": config["program_id"],
                "job_run_id": context["job_run_id"],
                "status": status,
                "knowledge_cutoff": context["inputs"]["knowledge_cutoff"],
                "details": details,
                "input_object_ids": list(dict.fromkeys(input_ids)),
                "material": False,
                "research_only": True,
                "not_a_recommendation": True,
                "not_a_decision": True,
                "model_tokens": 0,
            },
        )
        return {
            "manifest_id": report["id"],
            "output_refs": [report["id"], *dict.fromkeys(input_ids)],
            "material": False,
            "model_tokens": 0,
        }

    def _job_monthly_review(self, context: dict[str, Any]) -> dict[str, Any]:
        config = self.require_program()
        parameters = context["inputs"].get("parameters", {})
        if set(parameters) not in ({"program_id"}, {"trial_id"}):
            raise CompanionError("monthly quant review accepts only program_id")
        program_id = self._parameter_program_id(parameters)
        if program_id != config["program_id"]:
            raise CompanionError("monthly review program_id differs from the enabled research program")

        cutoff = parse(context["inputs"]["knowledge_cutoff"])
        previous_review = self._latest_review(program_id, before=cutoff)
        previous_body = self._review_body(previous_review)
        period_start = parse(previous_body["reviewed_through"]) if previous_review else parse(config["started_at"])
        period_items = [
            item
            for item in self._scan_manifests(program_id)
            if period_start < parse(item["created_at"]) <= cutoff
        ]
        period_ready = [item for item in period_items if self._scan_body(item).get("status") == "ready"]
        all_ready = [
            item
            for item in self._scan_manifests(program_id)
            if parse(item["created_at"]) <= cutoff
            and self._scan_body(item).get("status") == "ready"
        ]
        all_outcomes = self._prior_outcomes(program_id)
        period_outcome_map = {}
        for item in period_ready:
            observation = self._scan_body(item).get("forward_observation")
            if isinstance(observation, dict):
                key = (observation.get("from_date"), observation.get("to_date"))
                period_outcome_map[key] = observation
        period_outcomes = [period_outcome_map[key] for key in sorted(period_outcome_map)]
        data_items = [
            item
            for item in self._program_manifests("v5_canary_data_bundle", program_id)
            if period_start < parse(item["created_at"]) <= cutoff
        ]

        integrity_failures = []
        for item in period_items:
            try:
                self.c.data.manifest_get(item["id"], verify=True)
            except Exception as exc:
                integrity_failures.append({"manifest_id": item["id"], "error": str(exc)})

        period_metrics = self._review_metrics(period_ready, period_outcomes, data_items)
        cumulative_metrics = self._review_metrics(all_ready, all_outcomes, [])
        audit = {
            "checked_scan_manifests": len(period_items),
            "verified_scan_manifests": len(period_items) - len(integrity_failures),
            "integrity_failures": integrity_failures,
            "complete_denominator_retained": all(
                bool(self._scan_body(item).get("target_manifest_id")) for item in period_ready
            ),
            "model_tokens_in_deterministic_jobs": 0,
        }
        assessment = self._review_assessment(period_metrics, audit)
        source_refs = [item["id"] for item in [*period_items, *data_items]]
        report_body = {
            "program_id": program_id,
            "job_run_id": context["job_run_id"],
            "status": "ready",
            "review_type": "strict_forward_review",
            "period_start": iso(period_start),
            "period_end": iso(cutoff),
            "reviewed_through": iso(cutoff),
            "period_metrics": period_metrics,
            "cumulative_metrics": cumulative_metrics,
            "audit": audit,
            "assessment": assessment,
            "source_manifest_ids": source_refs,
            "method_frozen_during_period": True,
            "report_required": True,
            "automatic_method_change": False,
            "automatic_trading": False,
            "model_tokens": 0,
            "limitations": [
                "research_stream_data_is_not_yet_G1_qualified",
                "one_day_forward_outcomes_do_not_establish_durable_alpha",
                "baseline_omits_a_costed_executable_portfolio",
                "recommendations_require_separate_company_research_and_personal_portfolio_context",
            ],
        }
        report = self.c.data.manifest_publish(
            kind=REVIEW_MANIFEST_KIND,
            schema_version="investment-companion.v5-continuous-quant-review/v1",
            manifest=report_body,
        )
        return {
            "manifest_id": report["id"],
            "output_refs": [report["id"], *source_refs],
            "material": True,
            "model_tokens": 0,
            "event_summary": (
                f"Monthly continuous quant review is ready: {period_metrics['forward_observations']} "
                f"forward observations, assessment={assessment['status']}; user report required"
            ),
        }

    def _review_metrics(
        self,
        scans: list[dict[str, Any]],
        outcomes: list[dict[str, Any]],
        data_items: list[dict[str, Any]],
    ) -> dict[str, Any]:
        selected_returns = [Decimal(str(item["selected_equal_weight_return"])) for item in outcomes]
        baseline_returns = [Decimal(str(item["universe_median_return"])) for item in outcomes]
        excess_values = [Decimal(str(item["excess_vs_universe_median"])) for item in outcomes]
        selected_total, max_drawdown = self._compound_and_drawdown(selected_returns)
        baseline_total, _ = self._compound_and_drawdown(baseline_returns)
        relative_total = (
            (Decimal("1") + selected_total) / (Decimal("1") + baseline_total) - Decimal("1")
            if baseline_total > Decimal("-1")
            else None
        )
        turnover_values = [
            Decimal(str(self._scan_body(item).get("candidate_changes", 0)))
            / Decimal(2 * TOP_K)
            for item in scans
            if self._scan_body(item).get("candidate_changes") is not None
        ]
        unique_candidates = {
            candidate.get("asset_id")
            for item in scans
            for candidate in self._scan_body(item).get("candidates", [])
            if isinstance(candidate, dict) and candidate.get("asset_id")
        }
        data_statuses = Counter(self._scan_body(item).get("status") for item in data_items)
        ready_bundles = data_statuses.get("ready", 0)
        return {
            "ready_scans": len(scans),
            "scan_dates": [self._scan_body(item).get("as_of") for item in scans],
            "forward_observations": len(outcomes),
            "positive_excess_count": sum(1 for value in excess_values if value > 0),
            "positive_excess_rate": self._ratio_text(
                sum(1 for value in excess_values if value > 0), len(excess_values)
            ),
            "mean_excess_vs_universe_median": self._mean_text(excess_values),
            "median_excess_vs_universe_median": self._median_text(excess_values),
            "compounded_selected_return": self._decimal_text(selected_total) if selected_returns else None,
            "compounded_universe_median_return": self._decimal_text(baseline_total) if baseline_returns else None,
            "compounded_relative_return": self._decimal_text(relative_total) if relative_total is not None else None,
            "selected_path_max_drawdown": self._decimal_text(max_drawdown) if selected_returns else None,
            "mean_candidate_turnover_rate": self._mean_text(turnover_values),
            "unique_candidates": len(unique_candidates),
            "data_bundles": len(data_items),
            "ready_data_bundles": ready_bundles,
            "data_bundle_success_rate": self._ratio_text(ready_bundles, len(data_items)),
            "minimum_observations_for_directional_assessment": MIN_DIRECTIONAL_REVIEW_OBSERVATIONS,
        }

    def _review_assessment(
        self, metrics: dict[str, Any], audit: dict[str, Any]
    ) -> dict[str, Any]:
        observations = int(metrics["forward_observations"])
        if audit["integrity_failures"] or metrics["data_bundle_success_rate"] not in {None, "1"}:
            return {
                "status": "data_reliability_problem",
                "next_action": "repair_data_chain_before_judging_method",
                "reason": "the period contains data or audit failures",
            }
        if observations < MIN_DIRECTIONAL_REVIEW_OBSERVATIONS:
            return {
                "status": "insufficient_forward_evidence",
                "next_action": "continue_running_and_report_without_restricting_features",
                "reason": f"only {observations} eligible forward observations are available",
            }
        mean_excess = Decimal(str(metrics["mean_excess_vs_universe_median"]))
        relative = Decimal(str(metrics["compounded_relative_return"]))
        positive_rate = Decimal(str(metrics["positive_excess_rate"]))
        if mean_excess <= 0 and relative <= 0:
            return {
                "status": "ineffective_baseline",
                "next_action": "revise_or_replace_the_quant_baseline",
                "reason": "both mean and compounded benchmark-relative results are non-positive",
            }
        if positive_rate < Decimal("0.5") or mean_excess <= 0 or relative <= 0:
            return {
                "status": "mixed_evidence",
                "next_action": "investigate_failure_regimes_before_changing_or_promoting",
                "reason": "directional metrics disagree",
            }
        return {
            "status": "promising_not_proven",
            "next_action": "continue_forward_measurement_and_deepen_company_research",
            "reason": "directional metrics are positive but do not establish durable alpha",
        }

    @staticmethod
    def _compound_and_drawdown(values: list[Decimal]) -> tuple[Decimal, Decimal]:
        wealth = peak = Decimal("1")
        max_drawdown = Decimal("0")
        for value in values:
            wealth *= Decimal("1") + value
            peak = max(peak, wealth)
            if peak > 0:
                max_drawdown = min(max_drawdown, wealth / peak - Decimal("1"))
        return wealth - Decimal("1"), max_drawdown

    def _mean_text(self, values: list[Decimal]) -> str | None:
        return self._decimal_text(sum(values) / Decimal(len(values))) if values else None

    def _median_text(self, values: list[Decimal]) -> str | None:
        if not values:
            return None
        ordered = sorted(values)
        middle = len(ordered) // 2
        value = (
            ordered[middle]
            if len(ordered) % 2
            else (ordered[middle - 1] + ordered[middle]) / Decimal("2")
        )
        return self._decimal_text(value)

    def _ratio_text(self, numerator: int, denominator: int) -> str | None:
        return (
            self._decimal_text(Decimal(numerator) / Decimal(denominator))
            if denominator
            else None
        )

    def _validate_request(
        self, request: Any, replacements: dict[str, str]
    ) -> tuple[str, dict[str, Any], list[str]]:
        if not isinstance(request, dict) or set(request) != {"capability", "params", "fields"}:
            raise CompanionError("canary request must contain only capability, params and fields")
        capability = request["capability"]
        if capability not in REQUEST_FIELDS:
            raise CompanionError(f"controlled canary capability is forbidden: {capability}")
        fields = request["fields"]
        if fields != REQUEST_FIELDS[capability]:
            raise CompanionError(f"controlled canary {capability} fields differ from the frozen contract")
        params = request["params"]
        if not isinstance(params, dict):
            raise CompanionError("controlled canary params must be an object")
        params = {key: replacements.get(value, value) for key, value in params.items()}
        if capability in {"daily", "adj_factor"}:
            if set(params) != {"trade_date"}:
                raise CompanionError(f"controlled {capability} request accepts only trade_date")
            self._compact_day(params["trade_date"], "trade_date")
        else:
            if set(params) != {"exchange", "start_date", "end_date"} or params["exchange"] != "SSE":
                raise CompanionError("controlled trade_cal request requires SSE/start_date/end_date")
            start = date.fromisoformat(self._iso_day(self._compact_day(params["start_date"], "start_date")))
            end = date.fromisoformat(self._iso_day(self._compact_day(params["end_date"], "end_date")))
            if end < start or end - start > timedelta(days=550):
                raise CompanionError("controlled trade_cal range must be ordered and at most 550 days")
        return capability, params, fields

    def _dated_payloads(self, capability: str, cutoff) -> dict[str, dict[str, Any]]:
        try:
            stream = self.c.data.stream_get("tushare", capability)
        except CompanionError:
            return {}
        batches = self.c.data.batch_list(stream["id"], "ready")
        result: dict[str, dict[str, Any]] = {}
        for batch in sorted(batches, key=lambda item: (item.get("finished_at") or "", item["id"])):
            if not batch.get("finished_at") or parse(batch["finished_at"]) > cutoff:
                continue
            raw_day = batch.get("request_range", {}).get("trade_date")
            if not isinstance(raw_day, str) or not re.fullmatch(r"\d{8}", raw_day):
                continue
            object_ids = batch.get("canonical_object_ids", [])
            if len(object_ids) != 1:
                continue
            object_id = object_ids[0]
            payload = json.loads(self.c.data.object_read(object_id).decode("utf-8"))
            result[self._iso_day(raw_day)] = {
                "batch_id": batch["id"],
                "object_id": object_id,
                "payload": payload,
                "finished_at": batch["finished_at"],
            }
        return result

    def _calendar_rows(self, cutoff) -> list[dict[str, Any]]:
        try:
            stream = self.c.data.stream_get("tushare", "trade_cal")
        except CompanionError:
            return []
        rows: dict[tuple[str, str], dict[str, Any]] = {}
        for batch in reversed(self.c.data.batch_list(stream["id"], "ready")):
            if not batch.get("finished_at") or parse(batch["finished_at"]) > cutoff:
                continue
            for object_id in batch.get("canonical_object_ids", []):
                payload = json.loads(self.c.data.object_read(object_id).decode("utf-8"))
                if not isinstance(payload, list):
                    continue
                for item in payload:
                    if isinstance(item, dict) and {"date", "exchange", "is_open"} <= set(item):
                        rows[(str(item["exchange"]), str(item["date"]))] = item
        return [rows[key] for key in sorted(rows)]

    def _has_ready_date(self, capability: str, compact_day: str, cutoff) -> bool:
        return self._iso_day(compact_day) in self._dated_payloads(capability, cutoff)

    def _build_adjusted_bars(
        self,
        session_dates: list[str],
        daily: dict[str, dict[str, Any]],
        adjustments: dict[str, dict[str, Any]],
    ) -> tuple[
        list[dict[str, Any]],
        dict[str, list[tuple[str, Decimal, Decimal, Decimal]]],
        dict[str, list[str]],
        list[str],
    ]:
        bars: list[dict[str, Any]] = []
        histories: dict[str, list[tuple[str, Decimal, Decimal, Decimal]]] = {}
        exclusions: dict[str, list[str]] = {}
        source_ids: list[str] = []
        for day in session_dates:
            daily_item, adj_item = daily[day], adjustments[day]
            source_ids.extend([daily_item["object_id"], adj_item["object_id"]])
            daily_rows = daily_item["payload"]
            adj_payload = adj_item["payload"]
            if not isinstance(daily_rows, list) or not isinstance(adj_payload, dict):
                raise CompanionError("controlled scan input contract is invalid")
            facts = adj_payload.get("facts")
            if adj_payload.get("stream") != "adj_factor" or not isinstance(facts, list):
                raise CompanionError("controlled scan adj_factor input contract is invalid")
            factors: dict[str, Decimal] = {}
            for fact in facts:
                value = fact.get("value", {}) if isinstance(fact, dict) else {}
                if str(value.get("trade_date")) != day.replace("-", ""):
                    raise CompanionError("adj_factor response date differs from its request")
                asset = f"tushare:{value.get('ts_code')}"
                factor = self._positive_decimal(value.get("adj_factor"), "adj_factor")
                if asset in factors:
                    raise CompanionError(f"duplicate adj_factor for {asset}/{day}")
                factors[asset] = factor
            observed: set[str] = set()
            for row in daily_rows:
                if not isinstance(row, dict) or row.get("date") != day:
                    raise CompanionError("daily response date differs from its request")
                asset = str(row.get("asset_id") or "")
                if asset in observed:
                    raise CompanionError(f"duplicate daily row for {asset}/{day}")
                observed.add(asset)
                factor = factors.get(asset)
                if factor is None:
                    exclusions.setdefault(asset, []).append("missing_adj_factor")
                    continue
                close = self._positive_decimal(row.get("close"), "close")
                amount = self._positive_decimal(row.get("amount"), "amount", allow_zero=True)
                adjusted = close * factor
                histories.setdefault(asset, []).append((day, adjusted, amount, close))
                bars.append({"asset_id": asset, "date": day, "close": self._decimal_text(adjusted)})
        return bars, histories, exclusions, list(dict.fromkeys(source_ids))

    def _forward_observation(
        self,
        previous: dict[str, Any],
        histories: dict[str, list[tuple[str, Decimal, Decimal, Decimal]]],
        as_of: str,
    ) -> dict[str, Any] | None:
        body = self._scan_body(previous)
        prior_date = body.get("as_of")
        if (
            body.get("status") != "ready"
            or body.get("forward_observation_eligible") is not True
            or not prior_date
            or prior_date >= as_of
        ):
            return None
        selected_returns = []
        for item in body.get("candidates", []):
            asset = item.get("asset_id")
            history = {row[0]: row[1] for row in histories.get(asset, [])}
            if prior_date in history and as_of in history:
                selected_returns.append(history[as_of] / history[prior_date] - Decimal("1"))
        target_id = body.get("target_manifest_id")
        target_body = self.c.data.manifest_get(target_id)["manifest"].get("manifest", {}) if target_id else {}
        baseline_assets = {
            item.get("asset_id")
            for item in target_body.get("denominator", [])
            if isinstance(item, dict) and item.get("eligible") is True
        }
        universe_returns = []
        for asset, history_rows in histories.items():
            if asset not in baseline_assets:
                continue
            history = {row[0]: row[1] for row in history_rows}
            if prior_date in history and as_of in history:
                universe_returns.append(history[as_of] / history[prior_date] - Decimal("1"))
        if not selected_returns or not universe_returns:
            return None
        selected_return = sum(selected_returns) / Decimal(len(selected_returns))
        ordered = sorted(universe_returns)
        middle = len(ordered) // 2
        median = (
            ordered[middle]
            if len(ordered) % 2
            else (ordered[middle - 1] + ordered[middle]) / Decimal("2")
        )
        return {
            "predecessor_manifest_id": previous["id"],
            "from_date": prior_date,
            "to_date": as_of,
            "selected_equal_weight_return": self._decimal_text(selected_return),
            "universe_median_return": self._decimal_text(median),
            "excess_vs_universe_median": self._decimal_text(selected_return - median),
            "selected_observed_count": len(selected_returns),
            "research_only": True,
        }

    def _prior_outcomes(self, program_id: str) -> list[dict[str, Any]]:
        outcomes = {}
        for item in self._scan_manifests(program_id):
            observation = self._scan_body(item).get("forward_observation")
            if isinstance(observation, dict):
                outcomes[(observation.get("from_date"),observation.get("to_date"))]=observation
        return [outcomes[key] for key in sorted(outcomes)]

    def _forward_scorecard(self, outcomes: list[dict[str, Any]]) -> dict[str, Any]:
        values = [Decimal(str(item["excess_vs_universe_median"])) for item in outcomes]
        return {
            "observations": len(values),
            "positive_excess_count": sum(1 for value in values if value > 0),
            "positive_excess_rate": self._decimal_text(
                Decimal(sum(1 for value in values if value > 0)) / Decimal(len(values))
            ) if values else None,
            "mean_excess_vs_universe_median": self._decimal_text(sum(values) / Decimal(len(values)))
            if values
            else None,
            "status": (
                "insufficient_evidence"
                if len(values) < MIN_DIRECTIONAL_REVIEW_OBSERVATIONS
                else "directional_review_ready"
            ),
            "not_alpha_evidence": True,
        }

    def _scan_manifests(self, program_id: str | None) -> list[dict[str, Any]]:
        if not program_id:
            return []
        return self._program_manifests(SCAN_MANIFEST_KIND, program_id)

    def _program_manifests(self, kind: str, program_id: str) -> list[dict[str, Any]]:
        with self.db.connect() as con:
            items = rows_dict(
                con.execute(
                    "SELECT * FROM artifact_manifests WHERE kind=? ORDER BY created_at,id",
                    (kind,),
                ).fetchall()
            )
        return [
            item
            for item in items
            if self._manifest_program_id(self._scan_body(item)) == program_id
        ]

    def _latest_scan(
        self, program_id: str | None, *, before_date: str | None = None
    ) -> dict[str, Any] | None:
        items = []
        for item in self._scan_manifests(program_id):
            body = self._scan_body(item)
            if body.get("status") != "ready":
                continue
            if before_date and (not body.get("as_of") or body["as_of"] >= before_date):
                continue
            items.append(item)
        return items[-1] if items else None

    def _review_manifests(self, program_id: str | None) -> list[dict[str, Any]]:
        if not program_id:
            return []
        return self._program_manifests(REVIEW_MANIFEST_KIND, program_id)

    def _latest_review(
        self, program_id: str | None, *, before=None
    ) -> dict[str, Any] | None:
        items = self._review_manifests(program_id)
        if before is not None:
            items = [item for item in items if parse(item["created_at"]) < before]
        return items[-1] if items else None

    @staticmethod
    def _scan_body(item: dict[str, Any] | None) -> dict[str, Any]:
        return item.get("manifest", {}).get("manifest", {}) if item else {}

    @staticmethod
    def _review_body(item: dict[str, Any] | None) -> dict[str, Any]:
        return item.get("manifest", {}).get("manifest", {}) if item else {}

    def _scan_summary(self, item: dict[str, Any]) -> dict[str, Any]:
        body = self._scan_body(item)
        return {
            "manifest_id": item["id"],
            "status": body.get("status"),
            "as_of": body.get("as_of"),
            "effective_on": body.get("effective_on"),
            "forward_observation_eligible": body.get("forward_observation_eligible", False),
            "candidate_count": len(body.get("candidates", [])),
            "candidates": body.get("candidates", []),
            "candidate_delta": body.get("candidate_delta"),
            "research_shortlist": body.get("research_shortlist", []),
            "forward_scorecard": body.get("forward_scorecard") or body.get("trial_scorecard"),
            "material": body.get("material", False),
        }

    def _review_summary(self, item: dict[str, Any]) -> dict[str, Any]:
        body = self._review_body(item)
        return {
            "manifest_id": item["id"],
            "period_start": body.get("period_start"),
            "period_end": body.get("period_end"),
            "period_metrics": body.get("period_metrics"),
            "assessment": body.get("assessment"),
            "report_required": body.get("report_required", False),
        }

    @staticmethod
    def _config_program_id(config: Any) -> str | None:
        if not isinstance(config, dict):
            return None
        value = config.get("program_id") or config.get("trial_id")
        return value if isinstance(value, str) and value else None

    @staticmethod
    def _origin_program_id(origin: Any) -> str | None:
        if not isinstance(origin, dict):
            return None
        value = origin.get("program_id") or origin.get("trial_id")
        return value if isinstance(value, str) and value else None

    @staticmethod
    def _manifest_program_id(body: Any) -> str | None:
        if not isinstance(body, dict):
            return None
        value = body.get("program_id") or body.get("trial_id")
        return value if isinstance(value, str) and value else None

    @staticmethod
    def _parameter_program_id(parameters: Any) -> str | None:
        if not isinstance(parameters, dict):
            return None
        value = parameters.get("program_id") or parameters.get("trial_id")
        return value if isinstance(value, str) and value else None

    @staticmethod
    def _is_mainboard(asset: str) -> bool:
        match = re.fullmatch(r"tushare:(\d{6})\.(SH|SZ)", asset)
        if not match:
            return False
        code, exchange = match.groups()
        return (
            exchange == "SH" and code.startswith(("600", "601", "603", "605"))
        ) or (
            exchange == "SZ" and code.startswith(("000", "001", "002", "003"))
        )

    def _generated_before_effective_open(self, knowledge_cutoff: str, effective_on: str) -> bool:
        return self.c.market_calendar.generated_before_open(knowledge_cutoff, effective_on)

    @staticmethod
    def _volatility(closes: list[Decimal]) -> Decimal:
        returns = [closes[index] / closes[index - 1] - Decimal("1") for index in range(1, len(closes))]
        mean = sum(returns) / Decimal(len(returns))
        variance = sum((value - mean) ** 2 for value in returns) / Decimal(len(returns))
        with localcontext() as context:
            context.prec = 50
            return variance.sqrt()

    @staticmethod
    def _positive_decimal(value: Any, field: str, *, allow_zero: bool = False) -> Decimal:
        try:
            result = Decimal(str(value))
        except (InvalidOperation, ValueError) as exc:
            raise CompanionError(f"{field} must be numeric") from exc
        if not result.is_finite() or result < 0 or (result == 0 and not allow_zero):
            raise CompanionError(f"{field} must be {'non-negative' if allow_zero else 'positive'}")
        return result

    @staticmethod
    def _decimal_text(value: Decimal) -> str:
        if value == 0:
            return "0"
        return format(value.normalize(), "f")

    @staticmethod
    def _compact_day(value: Any, field: str) -> str:
        text = str(value).replace("-", "")
        if not re.fullmatch(r"\d{8}", text):
            raise CompanionError(f"{field} must be YYYYMMDD or YYYY-MM-DD")
        date.fromisoformat(f"{text[:4]}-{text[4:6]}-{text[6:]}")
        return text

    @staticmethod
    def _iso_day(compact: str) -> str:
        return f"{compact[:4]}-{compact[4:6]}-{compact[6:]}"

    @staticmethod
    def _boundaries() -> dict[str, Any]:
        return {
            "real_market_data": True,
            "deterministic_compute": True,
            "model_tokens_in_jobs": 0,
            "persistent_scan_can_trigger_full_research": True,
            "separate_research_can_reach_manual_decision": True,
            "dataset_snapshot_published": False,
            "automatic_formal_strategy_promotion": False,
            "shadow_portfolio": False,
            "scan_directly_creates_decision_or_action_card": False,
            "scan_directly_writes_ledger": False,
            "broker_or_auto_trade": False,
        }


# Kept for source compatibility with V5.0 callers. The implementation is no longer a trial.
ControlledQuantExperiment = ContinuousQuantResearch
