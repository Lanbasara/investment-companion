from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from ..foundation import CompanionError
from ..timeutil import parse
from ..tushare_adapter import TushareAdapter


class DeterministicJobHandlers:
    """Version-neutral deterministic handlers extracted from the facade."""

    def __init__(self, companion):
        self.c = companion

    def echo_manifest(self, context: dict[str, Any]) -> dict[str, Any]:
        item = self.c.data.manifest_publish(
            kind="deterministic_job_result",
            schema_version="investment-companion.job-result/v1",
            manifest={
                "handler": context["handler"],
                "handler_version": context["handler_version"],
                "inputs": context["inputs"],
                "prior_outputs": context["prior_outputs"],
                "model_tokens": 0,
            },
        )
        return {"manifest_id": item["id"], "output_refs": [item["id"]]}

    def publish_snapshot(self, context: dict[str, Any]) -> dict[str, Any]:
        self.c.jobs.feature_require("v4_live_data")
        object_id = context["inputs"].get("parameters", {}).get("snapshot_manifest_object_id")
        refs = {
            (item.get("type"), item.get("id"))
            for item in context["inputs"].get("refs", [])
            if isinstance(item, dict)
        }
        if not object_id or ("data_object", object_id) not in refs:
            raise CompanionError(
                "data.publish_snapshot requires an immutable snapshot_manifest_object_id ref"
            )
        try:
            manifest = json.loads(self.c.data.object_read(object_id).decode("utf-8"))
        except Exception as exc:
            raise CompanionError(f"invalid immutable DatasetSnapshot draft: {exc}") from exc
        snapshot = self.c.data.snapshot_validate_and_publish(manifest)
        return {
            "manifest_id": snapshot["manifest_id"],
            "output_refs": [snapshot["manifest_id"], snapshot["id"]],
            "material": False,
            "model_tokens": 0,
        }
    def tushare_ingest(self, context: dict[str, Any]) -> dict[str, Any]:
        self.c.jobs.feature_require("v4_live_data")
        parameters = context["inputs"].get("parameters", {})
        capability = parameters.get("capability")
        if not isinstance(capability, str):
            raise CompanionError("data.tushare_ingest requires capability")
        due = parse(context["inputs"]["knowledge_cutoff"]).astimezone(
            ZoneInfo("Asia/Shanghai")
        )
        replacements = {
            "$RUN_DATE": due.strftime("%Y%m%d"),
            "$RUN_MONTH": due.strftime("%Y%m"),
        }
        request_params = parameters.get("params", {})
        if not isinstance(request_params, dict):
            raise CompanionError("Tushare params must be an object")
        request_params = {
            key: replacements.get(value, value) for key, value in request_params.items()
        }
        fields = parameters.get("fields", [])
        if not isinstance(fields, list) or any(not isinstance(item, str) for item in fields):
            raise CompanionError("Tushare fields must be a string list")
        adapter = TushareAdapter(
            self.c, token_file=Path.home() / ".config" / "tushare" / "token"
        )
        batch = adapter.ingest(
            capability,
            params=request_params,
            fields=fields,
            account_scope=str(parameters.get("account_scope", "default")),
            ingestion_key=f"job-run:{context['job_run_id']}",
        )
        if batch["status"] not in {"ready", "empty_valid"}:
            error = batch.get("error") or ""
            raise CompanionError(
                f"Tushare active batch did not publish consumable data: {batch['status']} {error}".strip()
            )
        report = self.c.data.manifest_publish(
            kind="adapter_batch_result",
            schema_version="investment-companion.adapter-batch-result/v1",
            manifest={
                "provider": "tushare",
                "capability": capability,
                "request_params": request_params,
                "batch_id": batch["id"],
                "status": batch["status"],
                "row_count": batch["row_count"],
                "raw_object_ids": batch["raw_object_ids"],
                "canonical_object_ids": batch["canonical_object_ids"],
                "cursor_after": batch.get("cursor_after"),
                "knowledge_cutoff": context["inputs"]["knowledge_cutoff"],
                "model_tokens": 0,
            },
        )
        return {
            "manifest_id": report["id"],
            "output_refs": [
                report["id"],
                *batch["raw_object_ids"],
                *batch["canonical_object_ids"],
            ],
            "material": False,
            "model_tokens": 0,
        }

    def shadow_rebalance(self, context: dict[str, Any]) -> dict[str, Any]:
        self.c.jobs.feature_require("v4_shadow")
        parameters = context["inputs"].get("parameters", {})
        required = {
            "book_id",
            "signal_snapshot_id",
            "execution_snapshot_id",
            "experiment_run_id",
            "as_of",
            "target_manifest_id",
            "denominator_hash",
        }
        missing = required - set(parameters)
        if missing:
            raise CompanionError(f"shadow.rebalance missing parameters: {sorted(missing)}")
        refs = {
            (item.get("type"), item.get("id"))
            for item in context["inputs"].get("refs", [])
            if isinstance(item, dict)
        }
        expected = {
            ("dataset_snapshot", parameters["signal_snapshot_id"]),
            ("dataset_snapshot", parameters["execution_snapshot_id"]),
            ("artifact_manifest", parameters["target_manifest_id"]),
        }
        if not expected <= refs:
            raise CompanionError(
                "shadow.rebalance lacks immutable signal/execution Snapshot or target refs"
            )
        rebalance = self.c.shadow.rebalance_record(
            **{key: parameters[key] for key in required}
        )
        report = self.c.data.manifest_publish(
            kind="shadow_rebalance_result",
            schema_version="investment-companion.shadow-rebalance-result/v2",
            manifest={
                "rebalance_id": rebalance["id"],
                "book_id": parameters["book_id"],
                "signal_snapshot_id": parameters["signal_snapshot_id"],
                "execution_snapshot_id": parameters["execution_snapshot_id"],
                "target_manifest_id": parameters["target_manifest_id"],
                "simulation_hash": rebalance["result"]["simulation_hash"],
                "status": rebalance["status"],
                "model_tokens": 0,
            },
        )
        return {
            "manifest_id": report["id"],
            "output_refs": [
                report["id"],
                parameters["signal_snapshot_id"],
                parameters["execution_snapshot_id"],
                parameters["target_manifest_id"],
            ],
            "material": True,
            "model_tokens": 0,
            "event_summary": (
                f"Shadow rebalance {rebalance['id']} recorded; Primary review required"
            ),
        }
