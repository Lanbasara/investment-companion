from __future__ import annotations

import json
import os
import re
import subprocess
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Iterable, Mapping

from .core import CompanionError, canonical, digest, new_id
from .db import row_dict, rows_dict
from .timeutil import iso, parse, utc_now
from .v4_data import (
    ContentAddressedStore,
    DataObjectRef,
    DatasetSnapshotManifest,
    canonical_sha256,
    normalize_pit_fact,
    query_pit_facts,
)


CAPABILITY_STATUSES = {
    "unknown",
    "healthy",
    "connector_missing",
    "unauthorized",
    "invalid_request",
    "rate_limited",
    "stale",
    "partial",
    "empty_valid",
    "failed",
}

PARTITION_ROLES = {"development", "validation", "holdout", "production", "reference", "canary", "test_fixture"}
PARTITION_STREAMS = {
    "daily",
    "pit_facts",
    "universe",
    "trading_calendar",
    "corporate_actions",
    "native_experiment_spec",
    "test_fixture",
}
PARTITION_SCHEMAS = {
    "daily": {
        "required": [
            "asset_id", "date", "open", "high", "low", "close", "volume",
            "first_known_at", "ingested_at", "raw_hash", "parser_version",
        ],
        "unique": ["asset_id", "date"],
        "version": "daily-bars/v1",
    },
    "pit_facts": {"required": sorted(["entity_key", "fact_key", "revision_id"]), "version": "pit-facts/v1"},
    "universe": {"required": ["asset_ids"], "version": "universe/v1"},
    "trading_calendar": {"required": ["date", "exchange", "is_open"], "version": "trading-calendar/v1"},
    "corporate_actions": {"required": ["entity_key", "fact_key", "revision_id"], "version": "corporate-actions/v1"},
    "native_experiment_spec": {"required": ["schema", "evaluation_phase", "partition_names", "candidate", "benchmark"], "version": "native-experiment-spec/v3"},
    "test_fixture": {"required": [], "version": "test-fixture/v1"},
}
_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _corporate_action_from_fact(fact:Mapping[str,Any])->dict[str,Any]:
    normalized=normalize_pit_fact(fact);value=normalized.get("value")
    if not isinstance(value,Mapping):raise CompanionError("corporate action fact value must be an object")
    action_type=str(value.get("action_type") or "")
    day=str(value.get("ex_date") or normalized["effective_at"][:10])
    asset_id=str(value.get("asset_id") or normalized["entity_key"]).strip()
    if action_type not in {"cash_dividend","split","delist"} or not _DATE.fullmatch(day) or not asset_id:raise CompanionError("corporate action requires asset_id, ex_date and supported action_type")
    result={"action_id":normalized["revision_id"],"asset_id":asset_id,"date":day,"action_type":action_type}
    if action_type=="cash_dividend":
        amount=Decimal(str(value.get("cash_per_share")))
        if not amount.is_finite() or amount<0:raise CompanionError("cash_dividend cash_per_share must be non-negative")
        result["cash_per_share"]=str(value["cash_per_share"])
    elif action_type=="split":
        ratio=Decimal(str(value.get("split_ratio")))
        if not ratio.is_finite() or ratio<=0:raise CompanionError("split split_ratio must be positive")
        result["split_ratio"]=str(value["split_ratio"])
    else:
        price=Decimal(str(value.get("cash_price","0")))
        if not price.is_finite() or price<0:raise CompanionError("delist cash_price must be non-negative")
        result["cash_price"]=str(value.get("cash_price","0"))
    return result


class DataDomain:
    """SQLite control-plane metadata around the immutable V4 file primitives."""

    def __init__(self, companion, data_root: str | Path | None = None):
        self.c = companion
        self.db = companion.db
        configured = data_root or os.environ.get("COMPANION_DATA_ROOT")
        if configured is None:
            if (companion.root / ".git").exists():
                configured = Path.home() / ".local" / "share" / "investment-companion" / "v4-data"
            else:
                configured = companion.state / "v4-data"
        configured_path=Path(configured).expanduser().resolve()
        if companion.gate_scope=="test_fixture" and not configured_path.is_relative_to(companion.root):
            raise CompanionError("test_fixture data root must remain inside its isolated root")
        self.store = ContentAddressedStore(
            configured_path,
            root_id="investment-companion-v4-data",
        )
        # Resolve once before any bounded Job child installs its process-spawn
        # audit guard.  A deployed runtime is immutable for the process lifetime.
        self._resolved_code_version = self._resolve_code_version()

    @property
    def root(self) -> Path:
        return self.store.root

    def object_put_bytes(
        self,
        content: bytes,
        *,
        namespace: str = "raw",
        kind: str = "data_object",
        media_type: str = "application/octet-stream",
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        ref = self.store.put_bytes(
            content, namespace=namespace, kind=kind, media_type=media_type
        )
        return self._register_ref(ref, metadata or {})

    def object_put_json(
        self,
        value: Any,
        *,
        namespace: str = "canonical",
        kind: str = "canonical_json",
        media_type: str = "application/json",
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        ref = self.store.put_json(
            value, namespace=namespace, kind=kind, media_type=media_type
        )
        return self._register_ref(ref, metadata or {})

    def _register_ref(self, ref: DataObjectRef, metadata: dict[str, Any]) -> dict[str, Any]:
        metadata_with_namespace={**metadata,"namespace":ref.namespace}
        object_id, now = "dataobj_"+digest(ref.root_id,ref.relative_path,ref.kind,ref.media_type,metadata_with_namespace), iso()
        with self.db.transaction() as con:
            con.execute(
                "INSERT OR IGNORE INTO data_objects(id,data_root_id,relative_path,content_hash,kind,media_type,size_bytes,status,metadata_json,created_at,verified_at) VALUES(?,?,?,?,?,?,?,'ready',?,?,?)",
                (
                    object_id,
                    ref.root_id,
                    ref.relative_path,
                    ref.sha256,
                    ref.kind,
                    ref.media_type,
                    ref.size,
                    canonical(metadata_with_namespace),
                    now,
                    now,
                ),
            )
        return self.object_get(object_id, verify=True)

    def object_get(self, object_id: str, verify: bool = True) -> dict[str, Any]:
        with self.db.connect() as con:
            item = row_dict(con.execute("SELECT * FROM data_objects WHERE id=?", (object_id,)).fetchone())
        if not item:
            raise CompanionError(f"data object not found: {object_id}")
        if verify:
            try:self.store.verify(self._ref_from_row(item))
            except Exception:
                with self.db.transaction() as con:con.execute("UPDATE data_objects SET status='quarantined' WHERE id=?",(object_id,))
                raise
            if item["status"] != "ready":
                raise CompanionError(f"data object is not ready: {object_id}")
        return item

    def object_read(self, object_id: str) -> bytes:
        return self.store.read(self._ref_from_row(self.object_get(object_id, verify=True)))

    def _ref_from_row(self, item: dict[str, Any]) -> DataObjectRef:
        return DataObjectRef(
            root_id=item["data_root_id"],
            namespace=item["metadata"].get("namespace", item["relative_path"].split("/", 1)[0]),
            relative_path=item["relative_path"],
            sha256=item["content_hash"],
            size=int(item["size_bytes"]),
            media_type=item["media_type"],
            kind=item["kind"],
        )

    def manifest_publish(
        self,
        *,
        kind: str,
        schema_version: str,
        manifest: dict[str, Any],
        supersedes: str | None = None,
        _internal:bool=False,
    ) -> dict[str, Any]:
        reserved={"gate_evidence","data_quality_report","partition_validation_report","capability_probe_report","experiment_bundle","target_weights","target_weights_series","portfolio_simulation","agent_review","test_report","backup_restore_report","run_lifecycle_report","data_qualification_report","golden_corpus_report","license_review","quant_runtime_decision","quant_golden_report","job_fault_injection_report","replay_report","research_method_report","experiment_registry_report","shadow_validation_report","manual_execution_report","attention_validation_report","user_value_report","strategy_eligibility_report"}
        if kind in reserved and not _internal:raise CompanionError(f"reserved manifest kind requires its deterministic validator: {kind}")
        if supersedes:
            self.manifest_get(supersedes)
        payload = {
            "kind": kind,
            "schema_version": schema_version,
            "manifest": manifest,
            "supersedes": supersedes,
        }
        ref = self.store.put_json(
            payload,
            namespace="manifests",
            kind="artifact_manifest",
            media_type="application/vnd.investment-companion.manifest+json",
        )
        self._register_ref(ref, {"manifest_kind": kind})
        manifest_id, now = f"manifest_{ref.sha256}", iso()
        with self.db.transaction() as con:
            con.execute(
                "INSERT OR IGNORE INTO artifact_manifests(id,kind,schema_version,manifest_json,content_hash,supersedes,status,created_at) VALUES(?,?,?,?,?,?, 'ready',?)",
                (
                    manifest_id,
                    kind,
                    schema_version,
                    canonical(payload),
                    ref.sha256,
                    supersedes,
                    now,
                ),
            )
            if supersedes:
                con.execute(
                    "UPDATE artifact_manifests SET status='superseded' WHERE id=? AND status='ready'",
                    (supersedes,),
                )
        return self.manifest_get(manifest_id, verify=True)

    def manifest_get(self, manifest_id: str, verify: bool = True) -> dict[str, Any]:
        with self.db.connect() as con:
            item = row_dict(
                con.execute("SELECT * FROM artifact_manifests WHERE id=?", (manifest_id,)).fetchone()
            )
        if not item:
            raise CompanionError(f"artifact manifest not found: {manifest_id}")
        if verify:
            try:
                raw = self._read_hash(item["content_hash"],"manifests")
                if raw.decode("utf-8") != canonical(item["manifest"]):
                    raise CompanionError("artifact manifest DB/file mismatch")
            except Exception:
                with self.db.transaction() as con:con.execute("UPDATE artifact_manifests SET status='invalid' WHERE id=?",(manifest_id,))
                raise
            if item["status"] not in {"ready","superseded"}:
                raise CompanionError(f"artifact manifest is not consumable: {manifest_id} ({item['status']})")
        return item

    def snapshot_publish(
        self, manifest: DatasetSnapshotManifest | Mapping[str, Any]
    ) -> dict[str, Any]:
        snapshot = (
            manifest
            if isinstance(manifest, DatasetSnapshotManifest)
            else DatasetSnapshotManifest.from_dict(manifest)
        )
        if snapshot.status!="ready":raise CompanionError("only a ready DatasetSnapshot may be published; blocked diagnostics stay non-consumable artifacts")
        evidence_id=snapshot.quality.get("evidence_manifest_id")
        if not evidence_id:raise CompanionError("ready DatasetSnapshot requires validator-generated quality evidence")
        evidence=self.manifest_get(evidence_id)
        if evidence["kind"]!="data_quality_report":raise CompanionError("Snapshot quality evidence has wrong kind")
        report=evidence["manifest"].get("manifest",{})
        if report.get("validator")!="snapshot-quality-validator/2" or report.get("validator_status")!="passed" or not report.get("eligible") or report.get("lineage_hash")!=self._snapshot_quality_lineage(snapshot):raise CompanionError("Snapshot quality evidence is failed or belongs to another input lineage")
        if self.c.gate_scope=="production" and report.get("code_version")!=self._code_version():raise CompanionError("Snapshot quality evidence was produced by another code version")
        ref = snapshot.publish(self.store)
        self._register_ref(ref, {"snapshot_id": snapshot.snapshot_id})
        manifest_id, now = f"manifest_{ref.sha256}", iso()
        payload = snapshot.canonical_payload()
        denominator_hash = canonical_sha256(payload["denominator"])
        universe_hash = canonical_sha256(payload["universe"])
        supersedes = snapshot.parent_snapshot_id
        with self.db.transaction() as con:
            con.execute(
                "INSERT OR IGNORE INTO artifact_manifests(id,kind,schema_version,manifest_json,content_hash,status,created_at) VALUES(?,'dataset_snapshot',?,?,?,'ready',?)",
                (manifest_id, snapshot.schema_version, canonical(payload), ref.sha256, now),
            )
            con.execute(
                "INSERT OR IGNORE INTO dataset_snapshots(id,status,knowledge_cutoff,manifest_id,content_hash,denominator_hash,universe_hash,quality_json,code_version,supersedes,created_at) VALUES(?,'ready',?,?,?,?,?,?,?,?,?)",
                (
                    snapshot.snapshot_id,
                    snapshot.knowledge_cutoff,
                    manifest_id,
                    ref.sha256,
                    denominator_hash,
                    universe_hash,
                    canonical(dict(snapshot.quality)),
                    canonical(payload["code_version"]),
                    supersedes,
                    now,
                ),
            )
        return self.snapshot_get(snapshot.snapshot_id, verify=True)

    def snapshot_validate_and_publish(self,manifest:Mapping[str,Any])->dict[str,Any]:
        value=dict(manifest);quality=dict(value.get("quality",{}));quality.pop("evidence_manifest_id",None);value["quality"]=quality
        candidate=DatasetSnapshotManifest.from_dict(value)
        evidence=self.snapshot_quality_evidence(candidate);body=evidence["manifest"].get("manifest",{})
        if not body.get("eligible"):raise CompanionError(f"DatasetSnapshot quality validation failed: {body}")
        value=candidate.as_dict();value.pop("snapshot_id",None);value["quality"]={**dict(candidate.quality),"evidence_manifest_id":evidence["id"]}
        return self.snapshot_publish(value)

    def partition_validate(
        self,
        *,
        object_id: str,
        partition_name: str,
        stream: str,
        role: str,
        knowledge_cutoff: str,
    ) -> dict[str, Any]:
        """Parse and semantically validate one canonical partition.

        The report derives row counts and keys from immutable bytes.  Callers
        cannot submit their own count, schema result, or eligibility flag.
        """

        if stream not in PARTITION_STREAMS:
            raise CompanionError(f"unsupported partition stream: {stream}")
        if role not in PARTITION_ROLES:
            raise CompanionError(f"unsupported partition role: {role}")
        if role == "test_fixture" and self.c.gate_scope != "test_fixture":
            raise CompanionError("test_fixture partitions are forbidden in production scope")
        item = self.object_get(object_id, verify=True)
        try:
            value = json.loads(self.object_read(object_id).decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise CompanionError("canonical partition is not valid UTF-8 JSON") from exc
        violations: list[str] = []
        row_count = 0
        key_range: dict[str, Any] = {}
        try:
            row_count, key_range = self._validate_partition_value(
                stream, value, knowledge_cutoff
            )
        except (CompanionError, ValueError, TypeError, InvalidOperation) as exc:
            violations.append(str(exc))
        schema_hash = canonical_sha256(PARTITION_SCHEMAS[stream])
        report = {
            "validator": "partition-semantic-validator/1",
            "validator_status": "passed" if not violations else "failed",
            "code_version": self._code_version(),
            "object_id": object_id,
            "object_hash": item["content_hash"],
            "object_kind": item["kind"],
            "partition_name": partition_name,
            "stream": stream,
            "role": role,
            "knowledge_cutoff": knowledge_cutoff,
            "row_count": row_count,
            "key_range": key_range,
            "schema_hash": schema_hash,
            "violations": violations,
            "eligible": not violations,
            "checked_at": iso(),
        }
        return self.manifest_publish(
            kind="partition_validation_report",
            schema_version="investment-companion.partition-validation-report/v1",
            manifest=report,
            _internal=True,
        )

    def snapshot_quality_evidence(self,manifest:DatasetSnapshotManifest|Mapping[str,Any])->dict[str,Any]:
        snapshot=manifest if isinstance(manifest,DatasetSnapshotManifest) else DatasetSnapshotManifest.from_dict(manifest)
        referenced=snapshot.referenced_objects();hashes=[ref.sha256 for ref in referenced]
        for ref in referenced:self.store.verify(ref)
        with self.db.connect() as con:
            object_rows=rows_dict(con.execute("SELECT id,content_hash,status FROM data_objects WHERE content_hash IN (%s)"%(",".join("?" for _ in hashes)),hashes).fetchall()) if hashes else []
            scope_ids=set(hashes)|{row["id"] for row in object_rows}
            issues=rows_dict(con.execute("SELECT * FROM data_quality_issues WHERE blocker=1 AND status IN ('open','acknowledged')").fetchall())
        blockers=[issue for issue in issues if issue["scope_id"] in scope_ids or issue["scope_type"]=="global"]
        registered_hashes={row["content_hash"] for row in object_rows if row["status"]=="ready"}
        unregistered=sorted(set(hashes)-registered_hashes)
        partition_reports=[];partition_failures=[]
        for partition in snapshot.partitions:
            report_id=partition.quality.get("validator_manifest_id")
            if not report_id:
                partition_failures.append(f"{partition.name}: missing validator_manifest_id")
                continue
            validation=self.manifest_get(report_id,verify=True)
            body=validation["manifest"].get("manifest",{})
            expected={
                "partition_name":partition.name,
                "object_hash":partition.object_ref.sha256,
                "row_count":partition.row_count,
                "schema_hash":partition.schema_hash,
                "stream":partition.quality.get("stream"),
                "role":partition.quality.get("role"),
            }
            mismatches=[key for key,value in expected.items() if body.get(key)!=value]
            if validation["kind"]!="partition_validation_report" or body.get("validator")!="partition-semantic-validator/1":
                mismatches.append("validator")
            if not body.get("eligible") or body.get("validator_status")!="passed":
                mismatches.append("eligible")
            if self.c.gate_scope=="production" and body.get("code_version")!=self._code_version():
                mismatches.append("code_version")
            if mismatches:
                partition_failures.append(f"{partition.name}: {sorted(set(mismatches))}")
            partition_reports.append(report_id)
        contract_failures=self._validate_snapshot_contract(snapshot)
        report={"validator":"snapshot-quality-validator/2","validator_status":"passed" if not blockers and not unregistered and not partition_failures and not contract_failures else "failed","code_version":self._code_version(),"lineage_hash":self._snapshot_quality_lineage(snapshot),"input_hashes":sorted(hashes),"registered_ready":not unregistered,"unregistered_hashes":unregistered,"partition_reports":partition_reports,"partition_failures":partition_failures,"contract_failures":contract_failures,"blocker_ids":[issue["id"] for issue in blockers],"eligible":not blockers and not unregistered and not partition_failures and not contract_failures,"checked_at":iso()}
        return self.manifest_publish(kind="data_quality_report",schema_version="investment-companion.data-quality-report/v1",manifest=report,_internal=True)

    def _validate_partition_value(
        self, stream: str, value: Any, knowledge_cutoff: str
    ) -> tuple[int, dict[str, Any]]:
        cutoff = parse(knowledge_cutoff)
        if stream == "daily":
            if not isinstance(value, list) or not value:
                raise CompanionError("daily partition must be a non-empty JSON array")
            required = set(PARTITION_SCHEMAS[stream]["required"])
            seen: set[tuple[str, str]] = set(); dates=[]; assets=set()
            for index,row in enumerate(value):
                if not isinstance(row,dict):raise CompanionError(f"daily[{index}] must be an object")
                missing=required-set(row)
                if missing:raise CompanionError(f"daily[{index}] missing {sorted(missing)}")
                asset=str(row["asset_id"]).strip();day=str(row["date"])
                if not asset or not _DATE.fullmatch(day):raise CompanionError(f"daily[{index}] has invalid asset_id/date")
                date.fromisoformat(day)
                key=(asset,day)
                if key in seen:raise CompanionError(f"duplicate daily key: {asset}/{day}")
                seen.add(key);dates.append(day);assets.add(asset)
                prices={name:Decimal(str(row[name])) for name in ("open","high","low","close")}
                if min(prices.values())<=0:raise CompanionError(f"daily[{index}] prices must be positive")
                if prices["high"]<max(prices["open"],prices["low"],prices["close"]):raise CompanionError(f"daily[{index}] high is inconsistent")
                if prices["low"]>min(prices["open"],prices["high"],prices["close"]):raise CompanionError(f"daily[{index}] low is inconsistent")
                if Decimal(str(row["volume"]))<0:raise CompanionError(f"daily[{index}] volume is negative")
                known=parse(row["first_known_at"]);ingested=parse(row["ingested_at"])
                if known<parse(f"{row['date']}T15:00:00+08:00"):raise CompanionError(f"daily[{index}] first_known_at precedes the market close")
                if known>ingested:raise CompanionError(f"daily[{index}] first_known_at exceeds ingested_at")
                if known>cutoff:raise CompanionError(f"daily[{index}] is unknown at snapshot cutoff")
                raw_hash=str(row["raw_hash"])
                if not re.fullmatch(r"[0-9a-f]{64}",raw_hash):raise CompanionError(f"daily[{index}] raw_hash is invalid")
                if not str(row["parser_version"]).strip():raise CompanionError(f"daily[{index}] parser_version is empty")
            return len(value),{"min_date":min(dates),"max_date":max(dates),"asset_count":len(assets)}
        if stream == "pit_facts":
            if not isinstance(value,dict) or not isinstance(value.get("facts"),list):raise CompanionError("PIT partition must contain facts[]")
            facts=[normalize_pit_fact(item) for item in value["facts"]]
            query_pit_facts(facts,knowledge_cutoff=knowledge_cutoff)
            return len(facts),{"stream":value.get("stream"),"entity_count":len({item["entity_key"] for item in facts})}
        if stream == "universe":
            if not isinstance(value,dict) or not isinstance(value.get("asset_ids"),list):raise CompanionError("universe partition requires asset_ids[]")
            assets=value["asset_ids"]
            if not assets or any(not isinstance(item,str) or not item.strip() for item in assets):raise CompanionError("universe asset_ids must be non-empty strings")
            if len(assets)!=len(set(assets)):raise CompanionError("universe contains duplicate asset_ids")
            if value.get("count",len(assets))!=len(assets):raise CompanionError("universe count mismatch")
            return len(assets),{"first_asset":min(assets),"last_asset":max(assets)}
        if stream == "trading_calendar":
            if not isinstance(value,list) or not value:raise CompanionError("trading calendar must be a non-empty array")
            seen=set();dates=[]
            for index,row in enumerate(value):
                if not isinstance(row,dict) or not {"date","exchange","is_open"}<=set(row):raise CompanionError(f"calendar[{index}] missing date/exchange/is_open")
                if not _DATE.fullmatch(str(row["date"])) or not isinstance(row["is_open"],bool):raise CompanionError(f"calendar[{index}] has invalid date/is_open")
                key=(row["exchange"],row["date"])
                if key in seen:raise CompanionError(f"duplicate calendar key: {key}")
                seen.add(key);dates.append(row["date"])
            return len(value),{"min_date":min(dates),"max_date":max(dates)}
        if stream == "corporate_actions":
            if not isinstance(value,list):raise CompanionError("corporate actions must be an array")
            facts=[normalize_pit_fact(item) for item in value]
            query_pit_facts(facts,knowledge_cutoff=knowledge_cutoff)
            for fact in facts:_corporate_action_from_fact(fact)
            return len(facts),{"entity_count":len({item["entity_key"] for item in facts})}
        if stream == "native_experiment_spec":
            if not isinstance(value,dict) or value.get("schema")!="investment-companion.native-experiment-spec/v3":raise CompanionError("native experiment spec must use v3")
            expected={"schema","evaluation_phase","partition_names","candidate","benchmark"}
            if set(value)!=expected:raise CompanionError(f"native experiment spec fields must be exactly: {sorted(expected)}")
            required=set(PARTITION_SCHEMAS[stream]["required"]);missing=required-set(value)
            if missing:raise CompanionError(f"native experiment spec missing {sorted(missing)}")
            if value["evaluation_phase"] not in {"development","validation","final_holdout","forward_shadow"}:raise CompanionError("invalid evaluation_phase")
            names=value["partition_names"]
            if not isinstance(names,list) or not names or any(not isinstance(name,str) or not name for name in names) or len(names)!=len(set(names)):raise CompanionError("partition_names must be a non-empty unique list")
            for label in ("candidate","benchmark"):
                spec=value[label]
                if not isinstance(spec,dict) or set(spec)!={"strategy","simulation"}:raise CompanionError(f"{label} experiment config must freeze only strategy and simulation")
                forbidden={"bars","universe","eligibility","dataset_snapshot_id","strategy_version_id"}&set(spec)
                if forbidden:raise CompanionError(f"{label} embeds dataset-controlled fields: {sorted(forbidden)}")
            return 1,{"evaluation_phase":value["evaluation_phase"],"partition_count":len(names)}
        if stream == "test_fixture":
            if self.c.gate_scope!="test_fixture":raise CompanionError("test fixture validator is disabled")
            return (len(value) if isinstance(value,(list,dict)) else 1),{}
        raise CompanionError(f"no semantic validator for stream: {stream}")

    def _validate_snapshot_contract(self,snapshot:DatasetSnapshotManifest)->list[str]:
        payload=snapshot.canonical_payload();failures=[]
        denominator=payload["denominator"];universe=payload["universe"];exclusions=payload["exclusions"]
        if not isinstance(denominator,dict) or not isinstance(denominator.get("asset_ids"),list):
            failures.append("denominator.asset_ids is required")
            denominator_assets=[]
        else:
            denominator_assets=denominator["asset_ids"]
            if any(not isinstance(item,str) or not item for item in denominator_assets):failures.append("denominator assets must be non-empty strings");denominator_assets=[]
            elif len(denominator_assets)!=len(set(denominator_assets)):failures.append("denominator has duplicate assets")
            if denominator.get("count")!=len(denominator_assets):failures.append("denominator count mismatch")
        if not isinstance(universe,dict) or not isinstance(universe.get("eligible"),list):
            failures.append("universe.eligible is required");eligible=[]
        else:
            eligible=universe["eligible"]
            if any(not isinstance(item,str) or not item for item in eligible):failures.append("eligible universe assets must be non-empty strings");eligible=[]
        if not isinstance(exclusions,list) or any(not isinstance(item,dict) or not {"asset_id","reason"}<=set(item) or not isinstance(item.get("asset_id"),str) or not item.get("asset_id") or not isinstance(item.get("reason"),str) or not item.get("reason") for item in exclusions):
            failures.append("exclusions require asset_id and reason");excluded=[]
        else:excluded=[item["asset_id"] for item in exclusions]
        if len(eligible)!=len(set(eligible)):failures.append("eligible universe has duplicates")
        if len(excluded)!=len(set(excluded)):failures.append("exclusions have duplicates")
        if set(eligible)&set(excluded):failures.append("eligible and excluded universes overlap")
        if set(eligible)|set(excluded)!=set(denominator_assets):failures.append("eligible plus exclusions does not equal denominator")
        for partition in snapshot.partitions:
            if partition.quality.get("stream") not in PARTITION_STREAMS:failures.append(f"{partition.name} lacks a supported stream")
            if partition.quality.get("role") not in PARTITION_ROLES:failures.append(f"{partition.name} lacks a supported role")
            if partition.quality.get("role") in {"canary","test_fixture"}:failures.append(f"{partition.name} has a non-publishable role")
        research_ranges={};research_keys={}
        for partition in snapshot.partitions:
            role=partition.quality.get("role");stream=partition.quality.get("stream")
            if role not in {"development","validation","holdout"} or stream!="daily":continue
            try:rows=json.loads(self.store.read(partition.object_ref).decode("utf-8"))
            except Exception as exc:failures.append(f"{partition.name} cannot be decoded for split validation: {exc}");continue
            keys={(row.get("asset_id"),row.get("date")) for row in rows};dates=[row.get("date") for row in rows]
            overlap=set().union(*(research_keys.values()))&keys if research_keys else set()
            if overlap:failures.append(f"research partitions overlap at {sorted(overlap)[:3]}")
            research_keys[partition.name]=keys
            current=research_ranges.get(role)
            bounds=(min(dates),max(dates))
            research_ranges[role]=(min(current[0],bounds[0]),max(current[1],bounds[1])) if current else bounds
        ordered=[role for role in ("development","validation","holdout") if role in research_ranges]
        for left,right in zip(ordered,ordered[1:]):
            if research_ranges[left][1]>=research_ranges[right][0]:failures.append(f"research split is not chronological: {left} -> {right}")
        for field,stream in (("calendar","trading_calendar"),("corporate_actions","corporate_actions")):
            value=payload[field]
            if not isinstance(value,dict) or not isinstance(value.get("object_ref"),dict) or not value.get("validator_manifest_id"):
                failures.append(f"{field} requires object_ref and validator_manifest_id");continue
            validation=self.manifest_get(value["validator_manifest_id"],verify=True);body=validation["manifest"].get("manifest",{})
            if validation["kind"]!="partition_validation_report" or not body.get("eligible") or body.get("stream")!=stream or body.get("object_hash")!=value["object_ref"].get("sha256"):
                failures.append(f"{field} validator lineage mismatch")
        code=payload["code_version"]
        if self.c.gate_scope=="production" and (not isinstance(code,dict) or code.get("commit")!=self._code_version()):failures.append("snapshot code_version is not the current production commit")
        return failures

    def _code_version(self)->str:
        return self._resolved_code_version

    def _resolve_code_version(self)->str:
        if self.c.gate_scope=="test_fixture":return "test-fixture"
        runtime_root=Path(__file__).resolve().parents[1]
        proc=subprocess.run(["git","rev-parse","HEAD"],cwd=runtime_root,capture_output=True,text=True,timeout=10,check=False)
        value=proc.stdout.strip()
        if proc.returncode!=0 or not re.fullmatch(r"[0-9a-f]{40}",value):raise CompanionError("cannot resolve production code version")
        return value

    @staticmethod
    def _snapshot_quality_lineage(snapshot:DatasetSnapshotManifest)->str:
        payload=snapshot.canonical_payload();quality=dict(payload["quality"]);quality.pop("evidence_manifest_id",None);payload["quality"]=quality
        return canonical_sha256(payload)

    def snapshot_get(self, snapshot_id: str, verify: bool = True) -> dict[str, Any]:
        with self.db.connect() as con:
            item = row_dict(
                con.execute("SELECT * FROM dataset_snapshots WHERE id=?", (snapshot_id,)).fetchone()
            )
        if not item:
            raise CompanionError(f"dataset snapshot not found: {snapshot_id}")
        if verify:
            try:
                manifest = self.manifest_get(item["manifest_id"], verify=True)
                raw = self._read_hash(item["content_hash"],"manifests")
                decoded = json.loads(raw.decode("utf-8"));loaded = DatasetSnapshotManifest.from_dict(decoded)
                if loaded.snapshot_id != snapshot_id:raise CompanionError("dataset snapshot hash mismatch")
                for reference in loaded.referenced_objects():self.store.verify(reference)
                if canonical(decoded) != canonical(manifest["manifest"]):raise CompanionError("dataset snapshot manifest DB/file mismatch")
            except Exception:
                with self.db.transaction() as con:con.execute("UPDATE dataset_snapshots SET status='invalid' WHERE id=?",(snapshot_id,))
                raise
            if item["status"] not in {"ready","superseded"}:
                raise CompanionError(f"dataset snapshot is not consumable: {snapshot_id} ({item['status']})")
        return item

    def snapshot_manifest(self,snapshot_id:str)->DatasetSnapshotManifest:
        item=self.snapshot_get(snapshot_id,verify=True)
        raw=self._read_hash(item["content_hash"],"manifests")
        loaded=DatasetSnapshotManifest.from_dict(json.loads(raw.decode("utf-8")))
        if loaded.snapshot_id!=snapshot_id:raise CompanionError("dataset snapshot hash mismatch")
        for reference in loaded.referenced_objects():self.store.verify(reference)
        return loaded

    def snapshot_partition_payloads(
        self, snapshot_id: str, names: list[str], *, required_role: str | None = None
    ) -> list[dict[str, Any]]:
        snapshot = self.snapshot_manifest(snapshot_id)
        by_name = {partition.name: partition for partition in snapshot.partitions}
        if not isinstance(names,list) or not names or any(not isinstance(name,str) or not name for name in names) or len(names) != len(set(names)):
            raise CompanionError("snapshot partition selection must be non-empty and unique")
        missing = sorted(set(names) - set(by_name))
        if missing:
            raise CompanionError(f"snapshot partition selection is missing: {missing}")
        result=[]
        for name in names:
            partition=by_name[name];report_id=partition.quality.get("validator_manifest_id")
            validation=self.manifest_get(report_id,verify=True) if report_id else None
            body=validation["manifest"].get("manifest",{}) if validation else {}
            if not validation or validation["kind"]!="partition_validation_report" or not body.get("eligible") or body.get("object_hash")!=partition.object_ref.sha256:
                raise CompanionError(f"snapshot partition is not semantically validated: {name}")
            if required_role and body.get("role")!=required_role:
                raise CompanionError(f"snapshot partition {name} is not role={required_role}")
            try:value=json.loads(self.store.read(partition.object_ref).decode("utf-8"))
            except Exception as exc:raise CompanionError(f"cannot decode snapshot partition {name}: {exc}") from exc
            result.append({"name":name,"stream":body["stream"],"role":body["role"],"object_hash":partition.object_ref.sha256,"value":value})
        return result

    def snapshot_corporate_actions(self,snapshot_id:str)->list[dict[str,Any]]:
        snapshot=self.snapshot_manifest(snapshot_id);value=snapshot.corporate_actions
        ref_value=value.get("object_ref") if hasattr(value,"get") else None;report_id=value.get("validator_manifest_id") if hasattr(value,"get") else None
        if not ref_value or not report_id:raise CompanionError("Snapshot lacks validated corporate actions")
        report=self.manifest_get(report_id,verify=True);body=report["manifest"].get("manifest",{})
        ref=DataObjectRef.from_dict(ref_value)
        if report["kind"]!="partition_validation_report" or body.get("stream")!="corporate_actions" or not body.get("eligible") or body.get("object_hash")!=ref.sha256:raise CompanionError("Snapshot corporate action validator lineage mismatch")
        try:facts=json.loads(self.store.read(ref).decode("utf-8"))
        except Exception as exc:raise CompanionError(f"cannot decode Snapshot corporate actions: {exc}") from exc
        if not isinstance(facts,list):raise CompanionError("Snapshot corporate actions must be an array")
        actions=[_corporate_action_from_fact(fact) for fact in facts]
        if len({item["action_id"] for item in actions})!=len(actions):raise CompanionError("Snapshot corporate actions contain duplicate revision IDs")
        return sorted(actions,key=lambda item:(item["date"],item["asset_id"],item["action_type"],item["action_id"]))

    def _read_hash(self,content_hash:str,namespace:str)->bytes:
        with self.db.connect() as con:
            rows=rows_dict(con.execute("SELECT * FROM data_objects WHERE content_hash=? AND status='ready' ORDER BY created_at,id",(content_hash,)).fetchall())
        matches=[row for row in rows if row["metadata"].get("namespace")==namespace]
        if not matches:raise CompanionError(f"no ready {namespace} object for hash: {content_hash}")
        return self.store.read(self._ref_from_row(matches[0]))

    def pit_publish(
        self,
        facts: Iterable[Mapping[str, Any]],
        *,
        stream: str,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        normalized = [normalize_pit_fact(fact) for fact in facts]
        normalized.sort(key=lambda fact: (fact["first_known_at"], fact["revision_id"]))
        query_pit_facts(normalized, knowledge_cutoff="9999-12-31T23:59:59Z")
        return self.object_put_json(
            {"stream": stream, "facts": normalized, "metadata": metadata or {}},
            namespace="canonical",
            kind="pit_fact_partition",
            metadata={"stream": stream, "row_count": len(normalized)},
        )

    def capability_record(
        self,
        *,
        provider: str,
        capability: str,
        connector: str,
        status: str,
        account_scope: str = "default",
        permission: dict[str, Any] | None = None,
        limits: dict[str, Any] | None = None,
        history: dict[str, Any] | None = None,
        latency: dict[str, Any] | None = None,
        fields: dict[str, Any] | None = None,
        revision: dict[str, Any] | None = None,
        license: dict[str, Any] | None = None,
        failure: dict[str, Any] | None = None,
        evidence: dict[str, Any] | None = None,
        checked_at: str | None = None,
        _internal: bool = False,
    ) -> dict[str, Any]:
        if not _internal:
            raise CompanionError("source capabilities are recorded only by an authenticated adapter probe")
        if status not in CAPABILITY_STATUSES:
            raise CompanionError("invalid source capability status")
        evidence_value=evidence or {}
        probe_id=evidence_value.get("probe_manifest_id")
        if not probe_id:raise CompanionError("source capability requires immutable probe evidence")
        probe=self.manifest_get(probe_id,verify=True);body=probe["manifest"].get("manifest",{})
        if probe["kind"]!="capability_probe_report" or body.get("provider")!=provider or body.get("capability")!=capability or body.get("status")!=status:
            raise CompanionError("source capability probe evidence lineage mismatch")
        if self.c.gate_scope=="production" and body.get("code_version")!=self._code_version():raise CompanionError("source capability probe uses another code version")
        checked_at, created_at = checked_at or body.get("checked_at"), iso()
        if not checked_at or body.get("checked_at")!=checked_at:
            raise CompanionError("source capability checked_at differs from immutable probe evidence")
        if evidence_value.get("raw_object_id")!=body.get("response_object_id"):
            raise CompanionError("source capability Raw response differs from immutable probe evidence")
        if parse(checked_at)>utc_now()+timedelta(seconds=5):
            raise CompanionError("source capability probe timestamp cannot be in the future")
        cid = new_id("capability")
        with self.db.transaction() as con:
            con.execute(
                "INSERT INTO source_capabilities(id,provider,capability,connector,account_scope,status,permission_json,limits_json,history_json,latency_json,fields_json,revision_json,license_json,failure_json,evidence_json,checked_at,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    cid,
                    provider,
                    capability,
                    connector,
                    account_scope,
                    status,
                    canonical(permission or {}),
                    canonical(limits or {}),
                    canonical(history or {}),
                    canonical(latency or {}),
                    canonical(fields or {}),
                    canonical(revision or {}),
                    canonical(license or {}),
                    canonical(failure or {}),
                    canonical(evidence_value),
                    checked_at,
                    created_at,
                ),
            )
        return self.capability_get(cid)

    def capability_get(self, capability_id: str) -> dict[str, Any]:
        with self.db.connect() as con:
            item = row_dict(
                con.execute("SELECT * FROM source_capabilities WHERE id=?", (capability_id,)).fetchone()
            )
        if not item:
            raise CompanionError(f"source capability not found: {capability_id}")
        return item

    def capability_list(
        self, provider: str | None = None, capability: str | None = None
    ) -> list[dict[str, Any]]:
        query, params = "SELECT * FROM source_capabilities WHERE 1=1", []
        if provider:
            query += " AND provider=?"
            params.append(provider)
        if capability:
            query += " AND capability=?"
            params.append(capability)
        query += " ORDER BY checked_at DESC,id DESC"
        with self.db.connect() as con:
            return rows_dict(con.execute(query, params).fetchall())

    def stream_configure(self,*,provider:str,capability:str,schema_version:str,config:dict[str,Any],status:str="canary")->dict[str,Any]:
        if status not in {"inactive","canary","paused","blocked"}:raise CompanionError("new adapter stream must be inactive/canary/paused/blocked")
        sid=new_id("stream");now=iso()
        with self.db.transaction() as con:
            con.execute("INSERT OR IGNORE INTO adapter_streams(id,provider,capability,status,schema_version,config_json,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)",(sid,provider,capability,status,schema_version,canonical(config),now,now))
            row=con.execute("SELECT * FROM adapter_streams WHERE provider=? AND capability=?",(provider,capability)).fetchone()
        return row_dict(row)

    def stream_get(self,provider:str,capability:str)->dict[str,Any]:
        with self.db.connect() as con:item=row_dict(con.execute("SELECT * FROM adapter_streams WHERE provider=? AND capability=?",(provider,capability)).fetchone())
        if not item:raise CompanionError(f"adapter stream not configured: {provider}/{capability}")
        return item

    def stream_set_status(self,provider:str,capability:str,status:str)->dict[str,Any]:
        if status not in {"canary","active","paused","blocked","archived"}:raise CompanionError("invalid adapter stream status")
        item=self.stream_get(provider,capability)
        if status=="active":self.c.jobs.feature_require("v4_live_data")
        transitions={
            "inactive":{"canary","archived"},"canary":{"active","paused","blocked","archived"},
            "active":{"paused","blocked","archived"},"paused":{"canary","active","archived"},
            "blocked":{"canary","active","archived"},"archived":set(),
        }
        if status!=item["status"] and status not in transitions[item["status"]]:raise CompanionError(f"invalid adapter stream transition: {item['status']} -> {status}")
        with self.db.transaction() as con:con.execute("UPDATE adapter_streams SET status=?,updated_at=? WHERE id=?",(status,iso(),item["id"]))
        return self.stream_get(provider,capability)

    def batch_start(self,stream_id:str,idempotency_key:str,request_range:dict[str,Any],*,required_stream_status:str="canary")->dict[str,Any]:
        if required_stream_status not in {"canary","active"}:raise CompanionError("adapter batch requires canary or active stream mode")
        bid=new_id("batch");now=iso()
        with self.db.transaction() as con:
            stream=con.execute("SELECT * FROM adapter_streams WHERE id=? AND status=?",(stream_id,required_stream_status)).fetchone()
            if not stream:raise CompanionError(f"adapter batch requires a {required_stream_status} stream")
            con.execute("INSERT OR IGNORE INTO adapter_batches(id,stream_id,status,idempotency_key,request_range_json,started_at) VALUES(?,?,'fetching',?,?,?)",(bid,stream_id,idempotency_key,canonical(request_range),now))
            row=con.execute("SELECT * FROM adapter_batches WHERE idempotency_key=?",(idempotency_key,)).fetchone()
        return row_dict(row)

    def identity_apply_tushare_stock_basic(self,rows:list[dict[str,Any]],*,checked_at:str,raw_hash:str)->dict[str,Any]:
        """Append provider identity revisions after a validated active stock_basic batch."""
        assets=[];identifiers=[]
        for index,row in enumerate(rows):
            code=str(row.get("ts_code") or "").strip();name=str(row.get("name") or "").strip();listed=str(row.get("list_date") or "")
            if not code or not name or not re.fullmatch(r"\d{8}",listed):raise CompanionError(f"stock_basic identity row {index} is incomplete")
            list_status=str(row.get("list_status") or "").upper()
            delisted=str(row.get("delist_date") or "")
            if delisted and not re.fullmatch(r"\d{8}",delisted):raise CompanionError(f"stock_basic identity row {index} has invalid delist_date")
            with self.db.connect() as con:
                existing=con.execute("SELECT asset_id FROM asset_identifiers WHERE provider='tushare' AND identifier_type='ts_code' AND identifier_value=? ORDER BY first_known_at DESC,id DESC LIMIT 1",(code,)).fetchone()
            asset=self.c.financial.asset_get(existing[0]) if existing else self.c.financial.asset_upsert("stock",name,"CNY",{"tushare_ts_code":code},{"provider":"tushare","list_status":list_status})
            effective_at=iso(parse(f"{listed[:4]}-{listed[4:6]}-{listed[6:]}T00:00:00+08:00"))
            effective_to=iso(parse(f"{delisted[:4]}-{delisted[4:6]}-{delisted[6:]}T23:59:59+08:00")) if delisted else None
            revision_id=digest("asset-identity/tushare-stock-basic/v1",code,name,effective_at,effective_to,list_status,raw_hash)
            with self.db.transaction() as con:
                current=con.execute("SELECT id,revision_id FROM asset_identifiers WHERE provider='tushare' AND identifier_type='ts_code' AND identifier_value=? ORDER BY first_known_at DESC,id DESC LIMIT 1",(code,)).fetchone()
                iid=new_id("assetid")
                con.execute("INSERT OR IGNORE INTO asset_identifiers(id,asset_id,provider,identifier_type,identifier_value,effective_at,effective_to,first_known_at,ingested_at,revision_id,supersedes,raw_hash,parser_version,quality_json) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",(iid,asset["id"],"tushare","ts_code",code,effective_at,effective_to,checked_at,checked_at,revision_id,current[0] if current and current[1]!=revision_id else None,raw_hash,"tushare-stock-basic-identity/1",canonical({"status":"provider_response_validated","list_status":list_status,"name":name})))
                saved=con.execute("SELECT * FROM asset_identifiers WHERE provider='tushare' AND identifier_type='ts_code' AND identifier_value=? AND revision_id=?",(code,revision_id)).fetchone()
            assets.append(asset["id"]);identifiers.append(row_dict(saved)["id"])
        return {"asset_ids":sorted(set(assets)),"asset_identifier_ids":sorted(set(identifiers)),"count":len(set(identifiers))}

    def identity_apply_tushare_etf_basic(self,rows:list[dict[str,Any]],*,checked_at:str,raw_hash:str)->dict[str,Any]:
        """Append provider identity revisions for validated, listed ETFs only."""
        assets=[];identifiers=[]
        for index,row in enumerate(rows):
            code=str(row.get("ts_code") or "").strip();name=str(row.get("csname") or "").strip();listed=str(row.get("list_date") or "")
            exchange=str(row.get("exchange") or "").upper();list_status=str(row.get("list_status") or "").upper()
            if not code or not name or not re.fullmatch(r"\d{8}",listed):raise CompanionError(f"etf_basic identity row {index} is incomplete")
            if exchange not in {"SSE","SZSE"}:raise CompanionError(f"etf_basic identity row {index} has unsupported exchange")
            if list_status!="L":continue
            with self.db.connect() as con:
                existing=con.execute("SELECT asset_id FROM asset_identifiers WHERE provider='tushare' AND identifier_type='ts_code' AND identifier_value=? ORDER BY first_known_at DESC,id DESC LIMIT 1",(code,)).fetchone()
            metadata={"provider":"tushare","exchange":exchange,"list_status":list_status,"index_code":row.get("index_code"),"index_name":row.get("index_name"),"mgt_fee":row.get("mgt_fee"),"etf_type":row.get("etf_type")}
            asset=self.c.financial.asset_get(existing[0]) if existing else self.c.financial.asset_upsert("etf",name,"CNY",{"tushare_ts_code":code},metadata)
            effective_at=iso(parse(f"{listed[:4]}-{listed[4:6]}-{listed[6:]}T00:00:00+08:00"))
            revision_id=digest("asset-identity/tushare-etf-basic/v1",code,name,effective_at,exchange,list_status,metadata,raw_hash)
            with self.db.transaction() as con:
                current=con.execute("SELECT id,revision_id FROM asset_identifiers WHERE provider='tushare' AND identifier_type='ts_code' AND identifier_value=? ORDER BY first_known_at DESC,id DESC LIMIT 1",(code,)).fetchone()
                iid=new_id("assetid")
                con.execute("INSERT OR IGNORE INTO asset_identifiers(id,asset_id,provider,identifier_type,identifier_value,effective_at,effective_to,first_known_at,ingested_at,revision_id,supersedes,raw_hash,parser_version,quality_json) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",(iid,asset["id"],"tushare","ts_code",code,effective_at,None,checked_at,checked_at,revision_id,current[0] if current and current[1]!=revision_id else None,raw_hash,"tushare-etf-basic-identity/1",canonical({"status":"provider_response_validated","list_status":list_status,"exchange":exchange,"name":name})))
                saved=con.execute("SELECT * FROM asset_identifiers WHERE provider='tushare' AND identifier_type='ts_code' AND identifier_value=? AND revision_id=?",(code,revision_id)).fetchone()
            assets.append(asset["id"]);identifiers.append(row_dict(saved)["id"])
        return {"asset_ids":sorted(set(assets)),"asset_identifier_ids":sorted(set(identifiers)),"count":len(set(identifiers))}

    def identity_resolve(self,provider:str,identifier_type:str,identifier_value:str,*,effective_at:str,knowledge_cutoff:str)->dict[str,Any]:
        at=iso(parse(effective_at));cutoff=iso(parse(knowledge_cutoff))
        with self.db.connect() as con:
            rows=rows_dict(con.execute("SELECT * FROM asset_identifiers WHERE provider=? AND identifier_type=? AND identifier_value=? AND effective_at<=? AND (effective_to IS NULL OR effective_to>=?) AND first_known_at<=? ORDER BY first_known_at DESC,ingested_at DESC,id DESC",(provider,identifier_type,identifier_value,at,at,cutoff)).fetchall())
        if not rows:raise CompanionError(f"unresolved asset identity: {provider}/{identifier_type}/{identifier_value}")
        asset_ids={row["asset_id"] for row in rows}
        if len(asset_ids)!=1:raise CompanionError(f"ambiguous asset identity: {provider}/{identifier_type}/{identifier_value}")
        return rows[0]

    def identity_list(self,provider:str|None=None,identifier_value:str|None=None)->list[dict[str,Any]]:
        query="SELECT * FROM asset_identifiers WHERE 1=1";params=[]
        if provider:query+=" AND provider=?";params.append(provider)
        if identifier_value:query+=" AND identifier_value=?";params.append(identifier_value)
        query+=" ORDER BY provider,identifier_value,first_known_at DESC"
        with self.db.connect() as con:return rows_dict(con.execute(query,params).fetchall())

    def batch_finish(self,batch_id:str,*,status:str,raw_object_ids:list[str],canonical_object_ids:list[str],row_count:int,error:str|None=None,cursor_after:str|None=None)->dict[str,Any]:
        if status not in {"ready","blocked","failed","empty_valid"}:raise CompanionError("invalid adapter batch terminal status")
        now=iso()
        with self.db.transaction() as con:
            batch=con.execute("SELECT * FROM adapter_batches WHERE id=?",(batch_id,)).fetchone()
            if not batch:raise CompanionError("adapter batch not found")
            if batch["status"] in {"ready","blocked","failed","empty_valid"}:return row_dict(batch)
            con.execute("UPDATE adapter_batches SET status=?,raw_object_ids_json=?,canonical_object_ids_json=?,row_count=?,error=?,cursor_after=?,finished_at=? WHERE id=?",(status,canonical(raw_object_ids),canonical(canonical_object_ids),int(row_count),error,cursor_after,now,batch_id))
            if status in {"ready","empty_valid"}:con.execute("UPDATE adapter_streams SET cursor=?,watermark=?,last_success_at=?,last_error=NULL,updated_at=? WHERE id=?",(cursor_after,cursor_after,now,now,batch["stream_id"]))
            else:con.execute("UPDATE adapter_streams SET last_error=?,updated_at=? WHERE id=?",(error,now,batch["stream_id"]))
            row=con.execute("SELECT * FROM adapter_batches WHERE id=?",(batch_id,)).fetchone()
        return row_dict(row)

    def batch_list(self,stream_id:str|None=None,status:str|None=None)->list[dict[str,Any]]:
        query="SELECT * FROM adapter_batches WHERE 1=1";params=[]
        if stream_id:query+=" AND stream_id=?";params.append(stream_id)
        if status:query+=" AND status=?";params.append(status)
        query+=" ORDER BY started_at DESC,id DESC"
        with self.db.connect() as con:return rows_dict(con.execute(query,params).fetchall())

    def quality_issue_add(
        self,
        *,
        scope_type: str,
        scope_id: str,
        severity: str,
        code: str,
        message: str,
        blocker: bool = False,
        evidence: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if severity not in {"info", "warning", "error", "critical"}:
            raise CompanionError("invalid data quality severity")
        qid, now = new_id("dq"), iso()
        with self.db.transaction() as con:
            con.execute(
                "INSERT INTO data_quality_issues(id,scope_type,scope_id,severity,code,message,blocker,evidence_json,created_at) VALUES(?,?,?,?,?,?,?,?,?)",
                (
                    qid,
                    scope_type,
                    scope_id,
                    severity,
                    code,
                    message,
                    1 if blocker else 0,
                    canonical(evidence or {}),
                    now,
                ),
            )
            row = con.execute("SELECT * FROM data_quality_issues WHERE id=?", (qid,)).fetchone()
        return row_dict(row)

    def health(self) -> dict[str, Any]:
        with self.db.connect() as con:
            blocked = con.execute(
                "SELECT COUNT(*) FROM data_quality_issues WHERE blocker=1 AND status IN ('open','acknowledged')"
            ).fetchone()[0]
            history = rows_dict(con.execute("SELECT provider,capability,status,checked_at FROM source_capabilities ORDER BY checked_at DESC,id DESC").fetchall())
            seen=set();latest=[]
            for item in history:
                key=(item["provider"],item["capability"])
                if key in seen:continue
                seen.add(key);latest.append(item)
        return {
            "data_root_id": self.store.root_id,
            "data_root": str(self.root),
            "blocked_quality_issues": blocked,
            "latest_capabilities": latest,
            "live_data_enabled": self.c.jobs.feature_get("v4_live_data")["enabled"],
        }
