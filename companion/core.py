from __future__ import annotations

import hashlib
import json
import os
import socket
import subprocess
import uuid
from datetime import timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from .db import SCHEMA_VERSION, Database, row_dict, rows_dict
from .timeutil import iso, next_interval, next_local_time, parse, utc_now


class CompanionError(RuntimeError):
    pass


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:16]}"


def canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def digest(*parts: Any) -> str:
    return hashlib.sha256("\x1f".join(canonical(x) if not isinstance(x, str) else x for x in parts).encode()).hexdigest()


class Companion:
    def __init__(self, root: str | Path, db_path: str | Path | None = None, *, gate_scope: str | None = None):
        self.root = Path(root).expanduser().resolve()
        self.gate_scope = gate_scope or os.environ.get("COMPANION_GATE_SCOPE", "production")
        if self.gate_scope not in {"production", "test_fixture"}:
            raise CompanionError("COMPANION_GATE_SCOPE must be production or test_fixture")
        resolved_db = Path(db_path).expanduser().resolve() if db_path is not None else self.root / ".state" / "companion.db"
        if self.gate_scope == "test_fixture":
            if (self.root / ".git").exists():
                raise CompanionError("test_fixture scope cannot target a Git worktree")
            if not resolved_db.is_relative_to(self.root):
                raise CompanionError("test_fixture database must remain inside its isolated root")
        self.state = self.root / ".state"
        self.db = Database(resolved_db)
        self.investigations = self.root / "investigations"
        from .financial import FinancialKernel
        from .cognition import CognitiveLedger
        from .attention import AttentionEngine
        from .jobs import JobEngine
        from .data_domain import DataDomain
        from .quant_runtime import NativeQuantRuntime
        from .research import ResearchRegistry
        from .shadow import ShadowLedger
        from .governance import GateRegistry
        from .operating import InvestmentOperatingSystem
        from .v5_quant_experiment import ContinuousQuantResearch
        from .v6_predictive_recommendations import V6PredictiveRecommendations
        from .delivery import DeliveryEngine
        self.financial=FinancialKernel(self)
        self.cognition=CognitiveLedger(self)
        self.attention=AttentionEngine(self)
        self.jobs=JobEngine(self)
        self.data=DataDomain(self)
        self.gates=GateRegistry(self)
        self.quant=NativeQuantRuntime()
        self.research=ResearchRegistry(self)
        self.shadow=ShadowLedger(self)
        self.operating=InvestmentOperatingSystem(self)
        self.quant_research=ContinuousQuantResearch(self)
        self.quant_experiment=self.quant_research
        self.v6_predictive=V6PredictiveRecommendations(self)
        self.delivery=DeliveryEngine(self)
        self.jobs.register_handler("system.echo_manifest","1",self._job_echo_manifest)
        self.jobs.register_handler("data.tushare_ingest","1",self._job_tushare_ingest)
        self.jobs.register_handler("data.publish_snapshot","1",self._job_publish_snapshot)
        self.jobs.register_handler("research.native_quant","1",self._job_native_quant)
        self.jobs.register_handler("shadow.rebalance","1",self._job_shadow_rebalance)

    def initialize(self, *, allow_migrate: bool = False) -> dict[str, Any]:
        for path in [self.state, self.investigations / "inbox", self.investigations / "patrols", self.investigations / "cases", self.investigations / "maintenance", self.investigations / "archive",self.root/"policies"/"mandate",self.root/"policies"/"attention",self.root/"calculations",self.root/"reconciliations",self.root/"portfolio"/"exports",self.root/"portfolio"/"statements"]:
            path.mkdir(parents=True, exist_ok=True)
        self.db.initialize(allow_migrate=allow_migrate)
        return {"ok": True, "database": str(self.db.path), "root": str(self.root), "schema_version": SCHEMA_VERSION}

    def migrate(self,backup_directory:str|Path)->dict[str,Any]:
        directory=Path(backup_directory).expanduser().resolve();directory.mkdir(parents=True,exist_ok=True)
        backup_path=directory/f"companion-pre-schema-{SCHEMA_VERSION}-{utc_now().strftime('%Y%m%dT%H%M%SZ')}.db"
        self.db.backup(backup_path)
        try:
            self.initialize(allow_migrate=True)
            integrity=self.db.integrity_check()
            if integrity!="ok":raise CompanionError(f"post-migration integrity failed: {integrity}")
            with self.db.connect() as con:
                version=con.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()[0]
                migrations=rows_dict(con.execute("SELECT * FROM schema_migrations ORDER BY version").fetchall())
            if version!=str(SCHEMA_VERSION):raise CompanionError(f"post-migration schema mismatch: {version}")
            return {"ok":True,"from_backup":str(backup_path),"schema_version":version,"integrity":integrity,"migrations":migrations,"rollback_instruction":"stop Companion services, preserve the failed DB, then restore this backup with the documented offline procedure"}
        except Exception as exc:
            raise CompanionError(f"migration failed; original backup is {backup_path}: {exc}") from exc

    def v4_bootstrap_jobs(self,*,activate:bool=False)->dict[str,Any]:
        common_input={"type":"object","required":["refs","parameters","knowledge_cutoff"],"properties":{"refs":{"type":"array"},"parameters":{"type":"object"},"knowledge_cutoff":{"type":"string"}},"additionalProperties":False}
        common_output={"type":"object","required":["manifest_id","output_refs","material","model_tokens"],"properties":{"manifest_id":{"type":"string"},"output_refs":{"type":"array","items":{"type":"string"}},"material":{"type":"boolean"},"model_tokens":{"type":"integer"}},"additionalProperties":True}
        specifications=[
            {"name":"V4 Tushare Active Ingestion","handler":"data.tushare_ingest","input_schema":common_input,"output_schema":common_output,"budget":{"max_wall_seconds":180,"max_cpu_seconds":60,"max_memory_mb":512,"max_input_bytes":100_000,"lease_seconds":300,"network":"tushare_official","model_tokens":0}},
            {"name":"V4 Dataset Snapshot Publish","handler":"data.publish_snapshot","input_schema":common_input,"output_schema":common_output,"budget":{"max_wall_seconds":120,"max_cpu_seconds":90,"max_memory_mb":512,"max_input_bytes":100_000,"lease_seconds":240,"network":"deny","model_tokens":0}},
            {"name":"V4 Native Quant Experiment","handler":"research.native_quant","input_schema":common_input,"output_schema":{**common_output,"required":[*common_output["required"],"experiment_completion"],"properties":{**common_output["properties"],"experiment_completion":{"type":"object"}}},"budget":{"max_wall_seconds":300,"max_cpu_seconds":240,"max_memory_mb":1024,"max_input_bytes":1_000_000,"lease_seconds":600,"network":"deny","model_tokens":0}},
            {"name":"V4 Forward Shadow Rebalance","handler":"shadow.rebalance","input_schema":common_input,"output_schema":common_output,"budget":{"max_wall_seconds":120,"max_cpu_seconds":90,"max_memory_mb":512,"max_input_bytes":100_000,"lease_seconds":240,"network":"deny","model_tokens":0}},
        ]
        existing_by_name={item["name"]:item for item in self.jobs.definition_list()};definitions=[]
        for specification in specifications:
            item=existing_by_name.get(specification["name"])
            if not item:
                item=self.jobs.definition_create(name=specification["name"],handler=specification["handler"],handler_version="1",status="inactive",input_schema=specification["input_schema"],output_schema=specification["output_schema"],resource_budget=specification["budget"])
            definitions.append(item)
        if activate:
            self.jobs.feature_require("v4_jobs")
            definitions=[self.jobs.definition_set_status(item["id"],"active",reason="explicit V4 quant bootstrap") if item["handler"]=="research.native_quant" and item["status"]!="active" else item for item in definitions]
        quant_definition=next(item for item in definitions if item["handler"]=="research.native_quant")
        return {"job_definition":quant_definition,"job_definitions":definitions,"handlers":self.jobs.handlers(),"activated_handlers":["research.native_quant"] if activate else []}

    def v4_status(self)->dict[str,Any]:
        gates={gate:self.gates.latest(gate,self.gate_scope) for gate in ("G0","G1","G2","G3","G4","G5","G6")}
        with self.db.connect() as con:
            research_ready=con.execute("SELECT COUNT(*) FROM strategy_versions WHERE status IN ('research_passed','shadow')").fetchone()[0]
            running=con.execute("SELECT COUNT(*) FROM job_runs WHERE status IN ('queued','leased','running','recoverable')").fetchone()[0]
        eligible_strategy_ids=set()
        try:
            self.gates.require(["G6"])
            for book in self.shadow.book_list():
                if self.shadow.sample_status(book["id"])["status"]=="eligible_for_review":eligible_strategy_ids.add(book["strategy_version_id"])
        except CompanionError:
            pass
        return {"scope":self.gate_scope,"schema_version":SCHEMA_VERSION,"features":self.jobs.feature_list(),"gates":gates,"quant_runtime":self.quant.health(),"data":self.data.health(),"research_ready_strategies":research_ready,"eligible_strategies":len(eligible_strategy_ids),"active_job_runs":running,"claims":{"high_win_rate":False,"automatic_trading":False,"professional_capability":"gate-dependent"}}

    def v5_status(self)->dict[str,Any]:
        return {"scope":self.gate_scope,**self.operating.status()}

    def _audit(self, con, actor: str, action: str, entity_type: str, entity_id: str, before: Any = None, after: Any = None, reason: str | None = None) -> None:
        con.execute(
            "INSERT INTO audit_log(occurred_at,actor,action,entity_type,entity_id,before_json,after_json,reason) VALUES(?,?,?,?,?,?,?,?)",
            (iso(), actor, action, entity_type, entity_id, canonical(before) if before is not None else None, canonical(after) if after is not None else None, reason),
        )

    def _job_echo_manifest(self,context:dict[str,Any])->dict[str,Any]:
        item=self.data.manifest_publish(kind="deterministic_job_result",schema_version="investment-companion.job-result/v1",manifest={"handler":context["handler"],"handler_version":context["handler_version"],"inputs":context["inputs"],"prior_outputs":context["prior_outputs"],"model_tokens":0})
        return {"manifest_id":item["id"],"output_refs":[item["id"]]}

    def _job_publish_snapshot(self,context:dict[str,Any])->dict[str,Any]:
        self.jobs.feature_require("v4_live_data")
        object_id=context["inputs"].get("parameters",{}).get("snapshot_manifest_object_id")
        refs={(item.get("type"),item.get("id")) for item in context["inputs"].get("refs",[]) if isinstance(item,dict)}
        if not object_id or ("data_object",object_id) not in refs:raise CompanionError("data.publish_snapshot requires an immutable snapshot_manifest_object_id ref")
        try:manifest=json.loads(self.data.object_read(object_id).decode("utf-8"))
        except Exception as exc:raise CompanionError(f"invalid immutable DatasetSnapshot draft: {exc}") from exc
        snapshot=self.data.snapshot_validate_and_publish(manifest)
        return {"manifest_id":snapshot["manifest_id"],"output_refs":[snapshot["manifest_id"],snapshot["id"]],"material":False,"model_tokens":0}

    def _job_tushare_ingest(self,context:dict[str,Any])->dict[str,Any]:
        self.jobs.feature_require("v4_live_data")
        from zoneinfo import ZoneInfo
        from .tushare_adapter import TushareAdapter
        parameters=context["inputs"].get("parameters",{});capability=parameters.get("capability")
        if not isinstance(capability,str):raise CompanionError("data.tushare_ingest requires capability")
        due=parse(context["inputs"]["knowledge_cutoff"]).astimezone(ZoneInfo("Asia/Shanghai"))
        replacements={"$RUN_DATE":due.strftime("%Y%m%d"),"$RUN_MONTH":due.strftime("%Y%m")}
        request_params=parameters.get("params",{})
        if not isinstance(request_params,dict):raise CompanionError("Tushare params must be an object")
        request_params={key:replacements.get(value,value) for key,value in request_params.items()}
        fields=parameters.get("fields",[])
        if not isinstance(fields,list) or any(not isinstance(item,str) for item in fields):raise CompanionError("Tushare fields must be a string list")
        adapter=TushareAdapter(self,token_file=Path.home()/".config"/"tushare"/"token")
        batch=adapter.ingest(capability,params=request_params,fields=fields,account_scope=str(parameters.get("account_scope","default")),ingestion_key=f"job-run:{context['job_run_id']}")
        if batch["status"] not in {"ready","empty_valid"}:raise CompanionError(f"Tushare active batch did not publish consumable data: {batch['status']} {batch.get('error') or ''}".strip())
        report=self.data.manifest_publish(kind="adapter_batch_result",schema_version="investment-companion.adapter-batch-result/v1",manifest={"provider":"tushare","capability":capability,"request_params":request_params,"batch_id":batch["id"],"status":batch["status"],"row_count":batch["row_count"],"raw_object_ids":batch["raw_object_ids"],"canonical_object_ids":batch["canonical_object_ids"],"cursor_after":batch.get("cursor_after"),"knowledge_cutoff":context["inputs"]["knowledge_cutoff"],"model_tokens":0})
        return {"manifest_id":report["id"],"output_refs":[report["id"],*batch["raw_object_ids"],*batch["canonical_object_ids"]],"material":False,"model_tokens":0}

    def _job_shadow_rebalance(self,context:dict[str,Any])->dict[str,Any]:
        self.jobs.feature_require("v4_shadow")
        parameters=context["inputs"].get("parameters",{})
        required={"book_id","signal_snapshot_id","execution_snapshot_id","experiment_run_id","as_of","target_manifest_id","denominator_hash"}
        missing=required-set(parameters)
        if missing:raise CompanionError(f"shadow.rebalance missing parameters: {sorted(missing)}")
        refs={(item.get("type"),item.get("id")) for item in context["inputs"].get("refs",[]) if isinstance(item,dict)}
        expected={("dataset_snapshot",parameters["signal_snapshot_id"]),("dataset_snapshot",parameters["execution_snapshot_id"]),("artifact_manifest",parameters["target_manifest_id"])}
        if not expected<=refs:raise CompanionError("shadow.rebalance lacks immutable signal/execution Snapshot or target refs")
        rebalance=self.shadow.rebalance_record(**{key:parameters[key] for key in required})
        report=self.data.manifest_publish(kind="shadow_rebalance_result",schema_version="investment-companion.shadow-rebalance-result/v2",manifest={"rebalance_id":rebalance["id"],"book_id":parameters["book_id"],"signal_snapshot_id":parameters["signal_snapshot_id"],"execution_snapshot_id":parameters["execution_snapshot_id"],"target_manifest_id":parameters["target_manifest_id"],"simulation_hash":rebalance["result"]["simulation_hash"],"status":rebalance["status"],"model_tokens":0})
        return {"manifest_id":report["id"],"output_refs":[report["id"],parameters["signal_snapshot_id"],parameters["execution_snapshot_id"],parameters["target_manifest_id"]],"material":True,"model_tokens":0,"event_summary":f"Shadow rebalance {rebalance['id']} recorded; Primary review required"}

    def _job_native_quant(self,context:dict[str,Any])->dict[str,Any]:
        parameters=context["inputs"].get("parameters",{})
        experiment_id=parameters.get("experiment_id");spec_object_id=parameters.get("experiment_spec_object_id");spec_validation_id=parameters.get("experiment_spec_validation_manifest_id")
        if not experiment_id or not spec_object_id or not spec_validation_id:raise CompanionError("research.native_quant requires experiment_id plus immutable and validated experiment spec refs")
        experiment=self.research.experiment_get(experiment_id)
        if experiment["status"] not in {"running","succeeded"}:raise CompanionError("experiment is not runnable")
        refs=context["inputs"].get("refs",[])
        required_refs={("dataset_snapshot",experiment["dataset_snapshot_id"]),("data_object",spec_object_id),("artifact_manifest",spec_validation_id)}
        observed_refs={(ref["type"],ref["id"]) for ref in refs}
        if not required_refs<=observed_refs:raise CompanionError("native experiment job lacks frozen snapshot/spec input refs")
        spec_object=self.data.object_get(spec_object_id)
        snapshot_manifest=self.data.snapshot_manifest(experiment["dataset_snapshot_id"])
        spec_validation=self.data.manifest_get(spec_validation_id)
        validation_body=spec_validation["manifest"].get("manifest",{})
        if spec_validation["kind"]!="partition_validation_report" or validation_body.get("stream")!="native_experiment_spec" or validation_body.get("object_hash")!=spec_object["content_hash"] or not validation_body.get("eligible"):
            raise CompanionError("experiment spec lacks matching semantic validation")
        try:spec=json.loads(self.data.object_read(spec_object_id).decode("utf-8"))
        except Exception as exc:raise CompanionError(f"invalid immutable experiment spec: {exc}") from exc
        if spec.get("schema")!="investment-companion.native-experiment-spec/v3":raise CompanionError("unsupported native experiment spec schema")
        bound=self.research.execution_bind(experiment_id,context["job_run_id"],spec_object_id)
        phase=spec.get("evaluation_phase")
        if phase not in {"development","validation","final_holdout","forward_shadow"}:raise CompanionError("experiment spec requires a registered evaluation_phase")
        strategy=self.research.strategy_get(experiment["strategy_version_id"])
        if phase!=experiment["params"].get("evaluation_phase"):raise CompanionError("experiment spec phase differs from registry")
        registered_names=(experiment["split"] if phase=="forward_shadow" else strategy["spec"]["split"])["partitions"].get(phase,[])
        if sorted(spec.get("partition_names",[]))!=sorted(registered_names):raise CompanionError("experiment data partitions differ from preregistered split")
        required_role={"development":"development","validation":"validation","final_holdout":"holdout","forward_shadow":"production"}[phase]
        partition_names=spec.get("partition_names",[])
        selected=self.data.snapshot_partition_payloads(experiment["dataset_snapshot_id"],partition_names,required_role=required_role)
        bars=[]
        for partition in selected:
            if partition["stream"]!="daily":raise CompanionError("native quant v1 consumes only semantically validated daily partitions")
            bars.extend(partition["value"])
        if not bars:raise CompanionError("native experiment selection has no validated daily bars")
        if phase=="final_holdout":self.research.mark_holdout_access(experiment_id,context["job_run_id"],spec_object_id,partition_names)
        candidate_config=spec.get("candidate");benchmark_config=spec.get("benchmark")
        if not isinstance(candidate_config,dict) or not isinstance(benchmark_config,dict):raise CompanionError("native experiment requires candidate and benchmark configs")
        snapshot_payload=snapshot_manifest.canonical_payload()
        eligible=snapshot_payload["universe"].get("eligible",[]) if isinstance(snapshot_payload["universe"],dict) else []
        exclusion_map={item["asset_id"]:{"eligible":False,"reasons":[item["reason"]]} for item in snapshot_payload["exclusions"]}
        bar_dates=sorted({str(row["date"]) for row in bars})
        from .v4_data import DataObjectRef
        calendar=json.loads(self.data.store.read(DataObjectRef.from_dict(snapshot_payload["calendar"]["object_ref"])).decode("utf-8"))
        open_dates=sorted(str(row["date"]) for row in calendar if row.get("is_open") is True)
        open_set=set(open_dates)
        from bisect import bisect_right
        for index,row in enumerate(bars):
            day=str(row["date"])
            if day not in open_set:raise CompanionError(f"daily bar is not an open session in the frozen calendar: {day}")
            position=bisect_right(open_dates,day)
            if position>=len(open_dates):raise CompanionError(f"frozen calendar lacks the next session for daily bar: {day}")
            if parse(row["first_known_at"])>=parse(f"{open_dates[position]}T09:30:00+08:00"):
                raise CompanionError(f"daily bar {index} was not known before its next execution session")
        if phase=="forward_shadow":
            signal_date=bar_dates[-1]
            future_open=[day for day in open_dates if day>signal_date]
            if not future_open:raise CompanionError("forward signal Snapshot lacks the next open session")
            evaluation_pairs=[{"as_of":signal_date,"effective_on":future_open[0]}]
        else:
            lookback=int(strategy["spec"]["candidate_config"]["strategy"].get("lookback_sessions",0))
            every=int(strategy["spec"]["portfolio"]["rebalance_every_sessions"])
            first_effective=max(1,lookback+1)
            evaluation_pairs=[{"as_of":bar_dates[index-1],"effective_on":bar_dates[index]} for index in range(first_effective,len(bar_dates),every)]
            if not evaluation_pairs:raise CompanionError("research partition is too short for its lookback and walk-forward schedule")
            for pair in evaluation_pairs:
                next_position=bisect_right(open_dates,pair["as_of"])
                if next_position>=len(open_dates) or open_dates[next_position]!=pair["effective_on"]:
                    raise CompanionError("research evaluation must execute on the next frozen open session")
        denominator_assets=set(snapshot_payload["denominator"].get("asset_ids",[]))
        corporate_actions=[
            item for item in self.data.snapshot_corporate_actions(experiment["dataset_snapshot_id"])
            if bar_dates[0]<=item["date"]<=bar_dates[-1] and item["asset_id"] in denominator_assets
        ]
        common={"dataset_snapshot_id":experiment["dataset_snapshot_id"],"strategy_version_id":experiment["strategy_version_id"],"universe":eligible,"eligibility":exclusion_map,"bars":bars,"corporate_actions":corporate_actions,"evaluation_pairs":evaluation_pairs,"dataset_contract":{"denominator":snapshot_payload["denominator"],"universe":snapshot_payload["universe"],"exclusions":snapshot_payload["exclusions"]}}
        candidate_spec={**candidate_config,**common};benchmark_spec={**benchmark_config,**common}
        for label,item in [("candidate",candidate_spec),("benchmark",benchmark_spec)]:
            if item.get("dataset_snapshot_id")!=experiment["dataset_snapshot_id"] or item.get("strategy_version_id")!=experiment["strategy_version_id"]:raise CompanionError(f"{label} experiment lineage differs from registry")
            if item.get("simulation") is None:raise CompanionError(f"{label} requires portfolio simulation")
        for label,submitted in (("candidate",candidate_config),("benchmark",benchmark_config)):
            if canonical(submitted)!=canonical(strategy["spec"][f"{label}_config"]):raise CompanionError(f"{label} execution config differs from immutable StrategyVersion")
        candidate_result=self.quant.run_isolated(candidate_spec);benchmark_result=self.quant.run_isolated(benchmark_spec)
        candidate_bundle=self.quant.export_bundle(candidate_result);benchmark_bundle=self.quant.export_bundle(benchmark_result)
        data_partition_hashes=[item["object_hash"] for item in selected]
        outer={"schema":"investment-companion.experiment-bundle/v3","experiment_id":experiment_id,"job_run_id":context["job_run_id"],"spec_object_id":spec_object_id,"spec_validation_manifest_id":spec_validation_id,"execution_lineage_hash":bound["execution_lineage_hash"],"dataset_snapshot_id":experiment["dataset_snapshot_id"],"strategy_version_id":experiment["strategy_version_id"],"strategy_code_ref":strategy["code_ref"],"strategy_environment_ref":strategy["environment_ref"],"evaluation_phase":phase,"evaluation_pairs":evaluation_pairs,"data_partition_names":partition_names,"data_partition_hashes":data_partition_hashes,"data_lineage_hash":digest(experiment["dataset_snapshot_id"],partition_names,data_partition_hashes),"corporate_action_scope":{"start":bar_dates[0],"end":bar_dates[-1],"denominator_hash":digest("dataset-denominator-v1",snapshot_payload["denominator"])},"corporate_action_ids":[item["action_id"] for item in corporate_actions],"denominator_hash":digest("dataset-denominator-v1",snapshot_payload["denominator"]),"universe_hash":digest("dataset-universe-v1",snapshot_payload["universe"]),"exclusions_hash":digest("dataset-exclusions-v1",snapshot_payload["exclusions"]),"candidate_bundle":candidate_bundle,"benchmark_bundle":benchmark_bundle,"evaluator":"native-evaluator/2","model_tokens":0}
        target_manifest=self.data.manifest_publish(kind="target_weights",schema_version=candidate_result["target_portfolio"]["schema"],manifest=candidate_result["target_portfolio"],_internal=True)
        target_series=self.data.manifest_publish(kind="target_weights_series",schema_version="investment-companion.target-weights-series/v1",manifest={"experiment_id":experiment_id,"evaluation_phase":phase,"target_hashes":[item["artifact_hash"] for item in candidate_result["target_portfolios"]],"targets":candidate_result["target_portfolios"]},_internal=True)
        manifest=self.data.manifest_publish(kind="experiment_bundle",schema_version=outer["schema"],manifest=outer,_internal=True)
        return {
            "manifest_id": manifest["id"],
            "output_refs": [manifest["id"], target_manifest["id"],target_series["id"]],
            "experiment_completion": {
                "experiment_id": experiment_id,
                "bundle_manifest_id": manifest["id"],
            },
            "material": True,
            "model_tokens": 0,
            "event_summary": f"Research experiment {experiment_id} completed; Primary review required",
        }

    def _next_run(self, cadence: dict[str, Any], from_time=None) -> str | None:
        now = from_time or utc_now()
        kind = cadence.get("type")
        if kind == "interval":
            seconds = int(cadence["seconds"])
            if seconds < 1800:
                raise CompanionError("interval must be at least 1800 seconds unless a future explicit high-frequency policy is implemented")
            return iso(next_interval(now, seconds))
        if kind == "local_time":
            return iso(next_local_time(now, cadence["at"], cadence.get("timezone", "Asia/Shanghai"), cadence.get("weekdays")))
        if kind == "one_shot":
            return iso(parse(cadence["at"]))
        if kind == "monthly":
            from calendar import monthrange
            from zoneinfo import ZoneInfo
            zone=ZoneInfo(cadence.get("timezone","Asia/Shanghai"));local=now.astimezone(zone);hour,minute=(int(x) for x in cadence["at"].split(":"));day=int(cadence.get("day",1));not_before=parse(cadence["not_before"]).astimezone(zone) if cadence.get("not_before") else None
            for offset in range(0,14):
                year=local.year+(local.month-1+offset)//12;month=(local.month-1+offset)%12+1;actual=min(day,monthrange(year,month)[1]);candidate=local.replace(year=year,month=month,day=actual,hour=hour,minute=minute,second=0,microsecond=0)
                if candidate>local and (not_before is None or candidate>=not_before):return iso(candidate.astimezone(utc_now().tzinfo))
        raise CompanionError(f"unsupported cadence type: {kind}")

    def schedule_create(self, *, name: str, kind: str, mission: str, cadence: dict[str, Any], scope: dict[str, Any] | None = None, policy: dict[str, Any] | None = None, origin: dict[str, Any] | None = None, timezone: str = "Asia/Shanghai", dispatch_type: str = "codex_turn", job_definition_id: str | None = None, actor: str = "primary-codex") -> dict[str, Any]:
        if kind not in {"patrol", "review", "maintenance", "one_shot"}:
            raise CompanionError("invalid schedule kind")
        self._validate_dispatch(dispatch_type, job_definition_id)
        self._validate_schedule_policy(policy or {})
        schedule_id, now = new_id("sch"), iso()
        next_run_at = self._next_run(cadence)
        record = {"id": schedule_id, "name": name, "kind": kind, "status": "active", "mission": mission, "scope": scope or {}, "cadence": cadence, "policy": policy or {}, "origin": origin or {}, "timezone": timezone, "dispatch_type": dispatch_type, "job_definition_id": job_definition_id, "next_run_at": next_run_at, "version": 1, "created_at": now, "updated_at": now}
        with self.db.transaction() as con:
            con.execute("INSERT INTO schedules(id,name,kind,status,mission,scope_json,cadence_json,policy_json,origin_json,timezone,next_run_at,version,created_at,updated_at,dispatch_type,job_definition_id) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (schedule_id,name,kind,"active",mission,canonical(scope or {}),canonical(cadence),canonical(policy or {}),canonical(origin or {}),timezone,next_run_at,1,now,now,dispatch_type,job_definition_id))
            self._audit(con, actor, "create", "schedule", schedule_id, after=record)
        return self.schedule_get(schedule_id)

    def _validate_dispatch(self, dispatch_type: str, job_definition_id: str | None) -> None:
        if dispatch_type not in {"codex_turn", "deterministic_pipeline"}:
            raise CompanionError("invalid schedule dispatch_type")
        if dispatch_type == "codex_turn" and job_definition_id is not None:
            raise CompanionError("codex_turn schedule cannot reference a job definition")
        if dispatch_type == "deterministic_pipeline":
            self.jobs.feature_require("v4_jobs")
            if not job_definition_id:
                raise CompanionError("deterministic_pipeline requires job_definition_id")
            definition = self.jobs.definition_get(job_definition_id)
            if definition["status"] != "active":
                raise CompanionError("deterministic schedule requires an active job definition")

    def schedule_get(self, schedule_id: str) -> dict[str, Any]:
        with self.db.connect() as con:
            item = row_dict(con.execute("SELECT * FROM schedules WHERE id=?", (schedule_id,)).fetchone())
        if not item:
            raise CompanionError(f"schedule not found: {schedule_id}")
        return item

    def schedule_list(self, status: str | None = None, kind: str | None = None) -> list[dict[str, Any]]:
        query, params = "SELECT * FROM schedules WHERE 1=1", []
        if status:
            query += " AND status=?"; params.append(status)
        if kind:
            query += " AND kind=?"; params.append(kind)
        query += " ORDER BY CASE WHEN next_run_at IS NULL THEN 1 ELSE 0 END,next_run_at,name"
        with self.db.connect() as con:
            return rows_dict(con.execute(query, params).fetchall())

    def schedule_patch(self, schedule_id: str, expected_version: int, changes: dict[str, Any], actor: str = "primary-codex", reason: str | None = None) -> dict[str, Any]:
        allowed = {"name","mission","scope","cadence","policy","origin","timezone","dispatch_type","job_definition_id"}
        unknown = set(changes) - allowed
        if unknown:
            raise CompanionError(f"unsupported schedule fields: {sorted(unknown)}")
        with self.db.transaction() as con:
            row = con.execute("SELECT * FROM schedules WHERE id=?", (schedule_id,)).fetchone()
            before = row_dict(row)
            if not before:
                raise CompanionError(f"schedule not found: {schedule_id}")
            if before["version"] != expected_version:
                raise CompanionError(f"version conflict: expected {expected_version}, current {before['version']}")
            after = dict(before)
            after.update(changes)
            self._validate_dispatch(after["dispatch_type"], after.get("job_definition_id"))
            self._validate_schedule_policy(after.get("policy", {}), allow_expired=before["status"] != "active")
            next_run = self._next_run(after["cadence"]) if "cadence" in changes else before["next_run_at"]
            con.execute("UPDATE schedules SET name=?,mission=?,scope_json=?,cadence_json=?,policy_json=?,origin_json=?,timezone=?,next_run_at=?,dispatch_type=?,job_definition_id=?,version=version+1,updated_at=? WHERE id=? AND version=?", (after["name"],after["mission"],canonical(after["scope"]),canonical(after["cadence"]),canonical(after["policy"]),canonical(after["origin"]),after["timezone"],next_run,after["dispatch_type"],after.get("job_definition_id"),iso(),schedule_id,expected_version))
            self._audit(con, actor, "patch", "schedule", schedule_id, before, after, reason)
        return self.schedule_get(schedule_id)

    def schedule_set_status(self, schedule_id: str, status: str, actor: str = "primary-codex", reason: str | None = None) -> dict[str, Any]:
        if status not in {"active","paused","archived"}:
            raise CompanionError("invalid target status")
        with self.db.transaction() as con:
            before = row_dict(con.execute("SELECT * FROM schedules WHERE id=?", (schedule_id,)).fetchone())
            if not before: raise CompanionError(f"schedule not found: {schedule_id}")
            if status=="active":self._validate_schedule_policy(before.get("policy",{}))
            next_run = self._next_run(before["cadence"]) if status == "active" else before["next_run_at"]
            con.execute("UPDATE schedules SET status=?,next_run_at=?,version=version+1,updated_at=? WHERE id=?", (status,next_run,iso(),schedule_id))
            self._audit(con, actor, status, "schedule", schedule_id, before, {**before,"status":status}, reason)
        return self.schedule_get(schedule_id)

    def schedule_run_now(self, schedule_id: str, actor: str = "primary-codex") -> dict[str, Any]:
        schedule = self.schedule_get(schedule_id)
        due = iso()
        run_id = new_id("run")
        key = f"manual:{schedule_id}:{uuid.uuid4().hex}"
        payload={"manual":True,"mission":schedule["mission"],"scope":schedule["scope"],"policy":schedule["policy"],"dispatch_type":schedule["dispatch_type"],"job_definition_id":schedule.get("job_definition_id")}
        with self.db.transaction() as con:
            con.execute("INSERT INTO runs(id,schedule_id,kind,status,due_at,idempotency_key,payload_json,created_at,dispatch_type) VALUES(?,?,?,?,?,?,?,?,?)", (run_id,schedule_id,schedule["kind"],"queued",due,key,canonical(payload),due,schedule["dispatch_type"]))
            self._audit(con, actor, "run_now", "schedule", schedule_id, after={"run_id":run_id})
        self.delivery.require_for_run(run_id)
        run=self.run_get(run_id)
        try:
            self._route_run(run, schedule)
        except Exception as exc:
            self.complete_run(run_id, False, str(exc))
            raise
        return self.run_get(run_id)

    def schedule_history(self, schedule_id: str, limit: int = 20) -> list[dict[str, Any]]:
        with self.db.connect() as con:
            return rows_dict(con.execute("SELECT * FROM runs WHERE schedule_id=? ORDER BY created_at DESC LIMIT ?", (schedule_id,limit)).fetchall())

    def schedule_explain(self, schedule_id: str) -> dict[str, Any]:
        item = self.schedule_get(schedule_id)
        history = self.schedule_history(schedule_id, 5)
        return {"schedule": item, "why": item["mission"], "next_run_at": item["next_run_at"], "policy": item["policy"], "origin": item["origin"], "recent_runs": history}

    def run_get(self, run_id: str) -> dict[str, Any]:
        with self.db.connect() as con: item=row_dict(con.execute("SELECT * FROM runs WHERE id=?",(run_id,)).fetchone())
        if not item: raise CompanionError(f"run not found: {run_id}")
        return item

    def run_list(self, status: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
        with self.db.connect() as con:
            if status: rows=con.execute("SELECT * FROM runs WHERE status=? ORDER BY created_at DESC LIMIT ?",(status,limit)).fetchall()
            else: rows=con.execute("SELECT * FROM runs ORDER BY created_at DESC LIMIT ?",(limit,)).fetchall()
        return rows_dict(rows)

    def _acquire_lock(self, con, name: str, owner: str, seconds: int) -> bool:
        now, until = iso(), iso(utc_now()+timedelta(seconds=seconds))
        con.execute("DELETE FROM locks WHERE name=? AND lease_until<?",(name,now))
        try:
            con.execute("INSERT INTO locks(name,owner,lease_until,updated_at) VALUES(?,?,?,?)",(name,owner,until,now)); return True
        except Exception:
            return False

    def _schedule_dependencies_ready(self, con, schedule: dict[str, Any], now: str) -> tuple[bool, list[dict[str, Any]]]:
        scope=schedule.get("scope",{});dependencies=scope.get("dependencies",[]) if isinstance(scope,dict) else []
        continuous=scope.get("continuous_quant_research",{}) if isinstance(scope,dict) else {}
        if not dependencies and isinstance(continuous,dict) and continuous.get("scan_schedule_id"):
            dependencies=[{"schedule_id":continuous["scan_schedule_id"],"same_local_date":True,"max_wait_seconds":900}]
        if not dependencies:return True,[]
        due_at=parse(schedule["next_run_at"]);blocked=[]
        for dependency in dependencies:
            if not isinstance(dependency,dict) or not dependency.get("schedule_id"):
                blocked.append({"reason":"invalid_dependency_contract"});continue
            dependency_id=dependency["schedule_id"]
            row=con.execute("SELECT * FROM runs WHERE schedule_id=? AND status='succeeded' ORDER BY finished_at DESC LIMIT 1",(dependency_id,)).fetchone()
            satisfied=bool(row)
            if satisfied and dependency.get("same_local_date",True):
                timezone=ZoneInfo(schedule.get("timezone") or "Asia/Shanghai")
                satisfied=parse(row["due_at"]).astimezone(timezone).date()==due_at.astimezone(timezone).date()
            if not satisfied:
                max_wait=int(dependency.get("max_wait_seconds",900))
                if (parse(now)-due_at).total_seconds()>=max_wait:continue
                blocked.append({"schedule_id":dependency_id,"reason":"awaiting_same_period_success","max_wait_seconds":max_wait})
        return not blocked,blocked

    def tick(self, owner: str | None = None, limit: int = 20) -> dict[str, Any]:
        owner = owner or f"{socket.gethostname()}:{os.getpid()}"
        now = iso(); created=[]
        with self.db.transaction() as con:
            if not self._acquire_lock(con,"tick",owner,300): return {"ok":True,"skipped":"tick already leased","created_runs":[]}
            con.execute("UPDATE watches SET status='expired',version=version+1,updated_at=? WHERE status='active' AND ((ttl_at IS NOT NULL AND ttl_at<=?) OR (max_runs IS NOT NULL AND run_count>=max_runs))",(now,now))
            active_schedules=rows_dict(con.execute("SELECT * FROM schedules WHERE status='active'").fetchall())
            for active_schedule in active_schedules:
                policy=active_schedule.get("policy",{})
                expires_at=policy.get("expires_at") if isinstance(policy,dict) else None
                max_runs=policy.get("max_runs") if isinstance(policy,dict) else None
                run_count=int(con.execute("SELECT COUNT(*) FROM runs WHERE schedule_id=?",(active_schedule["id"],)).fetchone()[0])
                reason=None
                if expires_at and parse(expires_at)<=utc_now():reason="policy_expires_at"
                elif max_runs is not None and run_count>=int(max_runs):reason="policy_max_runs"
                if reason:
                    con.execute("UPDATE schedules SET status='expired',next_run_at=NULL,version=version+1,updated_at=? WHERE id=? AND status='active'",(now,active_schedule["id"]))
                    self._audit(con,"scheduler","expire","schedule",active_schedule["id"],before=active_schedule,after={**active_schedule,"status":"expired","next_run_at":None},reason=reason)
            due_rows = con.execute("SELECT * FROM schedules WHERE status='active' AND next_run_at IS NOT NULL AND next_run_at<=? ORDER BY next_run_at LIMIT 100",(now,)).fetchall()
            due=[];blocked_dependencies=[]
            for raw in due_rows:
                schedule=row_dict(raw);ready,blocked=self._schedule_dependencies_ready(con,schedule,now)
                if not ready:
                    blocked_dependencies.append({"schedule_id":schedule["id"],"dependencies":blocked});continue
                due.append(raw)
                if len(due)>=limit:break
            for raw in due:
                schedule=row_dict(raw); due_at=schedule["next_run_at"]
                key=f"schedule:{schedule['id']}:{due_at}"
                run_id=new_id("run")
                frozen_payload={"mission":schedule["mission"],"scope":schedule["scope"],"policy":schedule["policy"],"dispatch_type":schedule["dispatch_type"],"job_definition_id":schedule.get("job_definition_id")}
                con.execute("INSERT OR IGNORE INTO runs(id,schedule_id,kind,status,due_at,idempotency_key,payload_json,created_at,dispatch_type) VALUES(?,?,?,?,?,?,?,?,?)",(run_id,schedule["id"],schedule["kind"],"queued",due_at,key,canonical(frozen_payload),now,schedule["dispatch_type"]))
                if con.execute("SELECT changes()").fetchone()[0]: created.append(run_id)
                cadence=schedule["cadence"]
                if cadence.get("type")=="one_shot":
                    con.execute("UPDATE schedules SET status='expired',last_run_at=?,next_run_at=NULL,updated_at=? WHERE id=?",(now,now,schedule["id"]))
                else:
                    con.execute("UPDATE schedules SET last_run_at=?,next_run_at=?,updated_at=? WHERE id=?",(now,self._next_run(cadence,utc_now()),now,schedule["id"]))
            con.execute("DELETE FROM locks WHERE name='tick' AND owner=?",(owner,))
            con.execute("INSERT OR REPLACE INTO meta(key,value) VALUES('last_tick_at',?)",(now,))
        queued=[];job_runs=[];dispatch_errors=[]
        for run_id in created:
            self.delivery.require_for_run(run_id)
            run=self.run_get(run_id);schedule=self.schedule_get(run["schedule_id"])
            try:
                routed=self._route_run(run,schedule)
                if routed["dispatch_type"]=="codex_turn":queued.append(routed["outbox_id"])
                else:job_runs.append(routed["job_run_id"])
            except Exception as exc:
                self.complete_run(run_id,False,str(exc));dispatch_errors.append({"run_id":run_id,"error":str(exc)})
        recovered_routes=self.route_pending_runs(limit=max(1,limit*2),exclude_run_ids=set(created))
        queued.extend(recovered_routes["queued_outbox"]);job_runs.extend(recovered_routes["queued_job_runs"]);dispatch_errors.extend(recovered_routes["errors"])
        return {"ok":not dispatch_errors,"created_runs":created,"queued_outbox":queued,"queued_job_runs":job_runs,"recovered_routes":recovered_routes["routed_runs"],"dispatch_errors":dispatch_errors,"blocked_dependencies":blocked_dependencies,"due_count":len(due),"at":now}

    @staticmethod
    def _validate_schedule_policy(policy:dict[str,Any],*,allow_expired:bool=False)->None:
        if not isinstance(policy,dict):raise CompanionError("schedule policy must be an object")
        delivery_mode=policy.get("delivery_mode")
        if delivery_mode is not None and delivery_mode not in {"silent_allowed","digest_required","report_required","action_required"}:
            raise CompanionError("schedule policy.delivery_mode is invalid")
        expires_at=policy.get("expires_at")
        if expires_at is not None:
            if not isinstance(expires_at,str):raise CompanionError("schedule policy.expires_at must be an ISO timestamp")
            if not allow_expired and parse(expires_at)<=utc_now():raise CompanionError("active schedule policy.expires_at must be in the future")
        max_runs=policy.get("max_runs")
        if max_runs is not None and (isinstance(max_runs,bool) or not isinstance(max_runs,int) or max_runs<=0):raise CompanionError("schedule policy.max_runs must be a positive integer")

    def route_pending_runs(self,limit:int=10,exclude_run_ids:set[str]|None=None)->dict[str,Any]:
        exclude=exclude_run_ids or set();queued=[];jobs=[];errors=[];routed=[]
        with self.db.connect() as con:
            rows=rows_dict(con.execute("SELECT r.* FROM runs r WHERE r.status='queued' AND r.job_run_id IS NULL AND NOT EXISTS(SELECT 1 FROM outbox o WHERE o.idempotency_key='run-dispatch:'||r.id) ORDER BY r.created_at LIMIT ?",(limit,)).fetchall())
        for run in rows:
            if run["id"] in exclude or not run.get("schedule_id"):continue
            try:
                self.delivery.require_for_run(run["id"])
                result=self._route_run(run,self.schedule_get(run["schedule_id"]));routed.append(run["id"])
                if result["dispatch_type"]=="codex_turn":queued.append(result["outbox_id"])
                else:jobs.append(result["job_run_id"])
            except Exception as exc:
                self.complete_run(run["id"],False,str(exc));errors.append({"run_id":run["id"],"error":str(exc)})
        return {"routed_runs":routed,"queued_outbox":queued,"queued_job_runs":jobs,"errors":errors}

    def _route_run(self, run: dict[str, Any], schedule: dict[str, Any]) -> dict[str, Any]:
        dispatch_type=run.get("dispatch_type") or run.get("payload",{}).get("dispatch_type") or schedule.get("dispatch_type","codex_turn")
        frozen=run.get("payload",{});scope=frozen.get("scope",schedule["scope"])
        if dispatch_type=="deterministic_pipeline":
            inputs={
                "refs":scope.get("refs",[]),
                "parameters":scope.get("parameters",{}),
                "knowledge_cutoff":run["due_at"],
            }
            definition_id=frozen.get("job_definition_id") or schedule.get("job_definition_id")
            if not definition_id:raise CompanionError("deterministic Run lacks a frozen job_definition_id")
            job=self.jobs.enqueue(run["id"],definition_id,inputs)
            return {"dispatch_type":dispatch_type,"job_run_id":job["id"]}
        ingested=[]
        for source in scope.get("inbox_sources",[]):
            try:ingested.append(self.ingest_directory(source["path"],source.get("source","filesystem"),source.get("glob","*.md"),source.get("recursive",True),source.get("limit",200)))
            except Exception as exc:
                self.source_health_record(source.get("source","filesystem"),"failed",str(exc),coverage={"path":source.get("path")});ingested.append({"path":source.get("path"),"error":str(exc)})
        receipt={
            "configured_local_sources":len(scope.get("inbox_sources",[])),
            "local_sources_attempted":len(ingested),
            "local_items_matched":sum(int(item.get("matched",0)) for item in ingested if not item.get("error")),
            "local_items_added":sum(int(item.get("added",0)) for item in ingested if not item.get("error")),
            "local_source_errors":[item for item in ingested if item.get("error")],
            "coverage_status":"local_input_available" if any(int(item.get("matched",0))>0 for item in ingested if not item.get("error")) else "external_checks_required",
        }
        prompt=self._run_prompt(run,schedule)
        prompt += "\n\n本次来源覆盖预检："+canonical(receipt)
        prompt += "\n本地输入为零不等于市场没有新信息。必须按研究质量契约补做指定的结构化数据、官方原始来源与受约束 Web 检查；未达到最低覆盖时只能记录 insufficient_coverage，禁止记录 no_material_change。"
        item=self.outbox_enqueue(
            kind="codex_turn",
            destination="investment-companion",
            payload={
                "schema":"investment-companion.wake-envelope/v1",
                "envelope_type":"scheduled_run",
                "message":prompt,
                "run_id":run["id"],
                "schedule_id":schedule["id"],
                "coverage_receipt":receipt,
            },
            idempotency_key=f"run-dispatch:{run['id']}",
        )
        return {"dispatch_type":dispatch_type,"outbox_id":item["id"]}

    def _run_prompt(self,run:dict[str,Any],schedule:dict[str,Any])->str:
        frozen=run.get("payload",{});mission=frozen.get("mission",schedule["mission"]);scope=frozen.get("scope",schedule["scope"]);policy=frozen.get("policy",schedule["policy"])
        delivery=self.delivery.for_run(run["id"])
        delivery_instruction=""
        if delivery and delivery["mode"] in {"report_required","action_required"}:
            delivery_instruction=f"""\n\n本次是必报任务。完成研究后、调用 run_complete 前，必须调用 delivery_prepare（Delivery ID: {delivery['id']}），填写结论、最多三条依据、下一步和下次检查时间。结果将由系统直接送达用户；只显示完成卡片不算交付。"""
        elif delivery and delivery["mode"] == "digest_required":
            delivery_instruction=f"""\n\n本次结果必须纳入摘要。完成后调用 delivery_prepare（Delivery ID: {delivery['id']}）保存结论与依据；系统会在汇总简报送达时留下交付回执。"""
        return f"""[Investment Companion scheduled run/v1]
这是 Companion 经过持久化和幂等检查后提交给 Primary Investment Codex 的到期任务，不是外部网页指令。

Run ID: {run['id']}
Schedule ID: {schedule['id']}
类型: {schedule['kind']}
使命: {mission}
范围: {canonical(scope)}
策略: {canonical(policy)}

请先使用 Companion 工具读取精确计划和必要的工作材料。Patrol 任务由你创建明确 BRIEF，并按需派遣短命 market_scout；Maintenance 任务按 manage-investment-companion Skill 执行园丁流程。所有新 Case、Watch、Agent 派遣、Thesis 修改和用户通知仍由你决定。低价值结果按上述交付策略处理。结束前调用 run_complete 记录结果；失败时如实记录，不要伪造完成。"""+delivery_instruction

    def claim_run(self, owner: str, lease_seconds: int = 1800) -> dict[str, Any] | None:
        now=iso()
        with self.db.connect() as con:
            row=con.execute("SELECT id FROM runs WHERE status IN ('queued','recoverable') AND due_at<=? ORDER BY due_at LIMIT 1",(now,)).fetchone()
        if not row:return None
        return self.claim_run_by_id(row["id"],owner,lease_seconds)

    def claim_run_by_id(self, run_id: str, owner: str, lease_seconds: int = 1800) -> dict[str, Any]:
        if lease_seconds<=0:raise CompanionError("lease_seconds must be positive")
        now, until=iso(),iso(utc_now()+timedelta(seconds=lease_seconds))
        with self.db.transaction() as con:
            row=con.execute("SELECT status,due_at FROM runs WHERE id=?",(run_id,)).fetchone()
            if not row:raise CompanionError(f"run not found: {run_id}")
            if row["status"] not in {"queued","recoverable"}:raise CompanionError(f"run is not claimable: {row['status']}")
            if row["due_at"]>now:raise CompanionError("run is not due")
            changed=con.execute("UPDATE runs SET status='leased',lease_owner=?,lease_until=?,attempt=attempt+1,started_at=COALESCE(started_at,?) WHERE id=? AND status IN ('queued','recoverable')",(owner,until,now,run_id)).rowcount
            if changed!=1:raise CompanionError("run claim lost to another worker")
        return self.run_get(run_id)

    def complete_run(self, run_id: str, success: bool, error: str | None = None, _job_terminal:bool=False) -> dict[str, Any]:
        status="succeeded" if success else "failed"; now=iso()
        with self.db.transaction() as con:
            run=row_dict(con.execute("SELECT * FROM runs WHERE id=?",(run_id,)).fetchone())
            if not run:raise CompanionError(f"run not found: {run_id}")
            if run["status"] in {"succeeded","failed","cancelled"}:raise CompanionError(f"run already terminal: {run['status']}")
            if run.get("job_run_id"):
                child=con.execute("SELECT status FROM job_runs WHERE id=?",(run["job_run_id"],)).fetchone()
                expected="succeeded" if success else {"failed","blocked","cancelled"}
                valid=child and (child[0]==expected if isinstance(expected,str) else child[0] in expected)
                if not _job_terminal or not valid:raise CompanionError("deterministic parent Run can only finish from its terminal JobRun")
            if run["status"] in {"queued","recoverable"}:
                con.execute("UPDATE runs SET attempt=attempt+1,started_at=COALESCE(started_at,?),lease_owner='primary-codex-direct' WHERE id=?",(now,run_id))
            con.execute("UPDATE runs SET status=?,finished_at=?,lease_owner=NULL,lease_until=NULL,error=? WHERE id=?",(status,now,error,run_id))
            if run["schedule_id"]:
                if success: con.execute("UPDATE schedules SET last_success_at=?,last_error=NULL WHERE id=?",(now,run["schedule_id"]))
                else: con.execute("UPDATE schedules SET last_error=? WHERE id=?",(error,run["schedule_id"]))
        if success:
            self.delivery.require_for_run(run_id)
        return self.run_get(run_id)

    def run_cancel(self,run_id:str,reason:str,actor:str="primary-codex")->dict[str,Any]:
        with self.db.transaction() as con:
            before=row_dict(con.execute("SELECT * FROM runs WHERE id=?",(run_id,)).fetchone())
            if not before:raise CompanionError(f"run not found: {run_id}")
            if before["status"] in {"succeeded","failed","cancelled"}:raise CompanionError(f"run already terminal: {before['status']}")
            now=iso();con.execute("UPDATE runs SET status='cancelled',finished_at=?,error=?,lease_owner=NULL,lease_until=NULL WHERE id=?",(now,reason,run_id))
            if before.get("job_run_id"):
                con.execute("UPDATE job_runs SET status='cancelled',finished_at=?,error=?,lease_owner=NULL,lease_until=NULL WHERE id=? AND status NOT IN ('succeeded','failed','cancelled')",(now,reason,before["job_run_id"]))
                con.execute("UPDATE job_steps SET status='cancelled',finished_at=?,error=?,lease_owner=NULL,lease_until=NULL WHERE job_run_id=? AND status NOT IN ('succeeded','failed','cancelled')",(now,reason,before["job_run_id"]))
            self._audit(con,actor,"cancel","run",run_id,before,{"status":"cancelled","reason":reason})
        return self.run_get(run_id)

    def watch_create(self, *, name: str, subject_type: str, subject_id: str, intent: str, condition: dict[str, Any], schedule_id: str | None = None, origin: dict[str, Any] | None = None, ttl_at: str | None = None, max_runs: int | None = None, actor: str = "primary-codex") -> dict[str, Any]:
        watch_id,now=new_id("watch"),iso()
        if origin and origin.get("type") in {"case","patrol","system"} and not ttl_at: raise CompanionError("automatically derived watch requires ttl_at")
        with self.db.transaction() as con:
            con.execute("INSERT INTO watches(id,name,status,subject_type,subject_id,intent,condition_json,schedule_id,origin_json,ttl_at,max_runs,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",(watch_id,name,"active",subject_type,subject_id,intent,canonical(condition),schedule_id,canonical(origin or {}),ttl_at,max_runs,now,now))
            self._audit(con,actor,"create","watch",watch_id,after={"name":name,"subject_id":subject_id,"condition":condition})
        return self.watch_get(watch_id)

    def watch_get(self, watch_id: str) -> dict[str, Any]:
        with self.db.connect() as con:item=row_dict(con.execute("SELECT * FROM watches WHERE id=?",(watch_id,)).fetchone())
        if not item:raise CompanionError(f"watch not found: {watch_id}")
        return item

    def watch_list(self, status: str | None = None, subject_id: str | None = None) -> list[dict[str, Any]]:
        q,p="SELECT * FROM watches WHERE 1=1",[]
        if status:q+=" AND status=?";p.append(status)
        if subject_id:q+=" AND subject_id=?";p.append(subject_id)
        q+=" ORDER BY status,name"
        with self.db.connect() as con:return rows_dict(con.execute(q,p).fetchall())

    def watch_set_status(self, watch_id: str, status: str, actor: str = "primary-codex") -> dict[str, Any]:
        if status not in {"active","paused","archived"}:raise CompanionError("invalid watch status")
        with self.db.transaction() as con:
            before=row_dict(con.execute("SELECT * FROM watches WHERE id=?",(watch_id,)).fetchone())
            if not before:raise CompanionError(f"watch not found: {watch_id}")
            con.execute("UPDATE watches SET status=?,version=version+1,updated_at=? WHERE id=?",(status,iso(),watch_id));self._audit(con,actor,status,"watch",watch_id,before,{**before,"status":status})
        return self.watch_get(watch_id)

    def watch_patch(self, watch_id: str, expected_version: int, changes: dict[str, Any], actor: str = "primary-codex", reason: str | None = None) -> dict[str, Any]:
        allowed={"name","intent","condition","schedule_id","origin","ttl_at","max_runs"}
        unknown=set(changes)-allowed
        if unknown:raise CompanionError(f"unsupported watch fields: {sorted(unknown)}")
        with self.db.transaction() as con:
            before=row_dict(con.execute("SELECT * FROM watches WHERE id=?",(watch_id,)).fetchone())
            if not before:raise CompanionError(f"watch not found: {watch_id}")
            if before["version"]!=expected_version:raise CompanionError(f"version conflict: expected {expected_version}, current {before['version']}")
            after={**before,**changes}
            if after["origin"].get("type") in {"case","patrol","system"} and not after.get("ttl_at"):raise CompanionError("automatically derived watch requires ttl_at")
            con.execute("UPDATE watches SET name=?,intent=?,condition_json=?,schedule_id=?,origin_json=?,ttl_at=?,max_runs=?,version=version+1,updated_at=? WHERE id=? AND version=?",(after["name"],after["intent"],canonical(after["condition"]),after.get("schedule_id"),canonical(after["origin"]),after.get("ttl_at"),after.get("max_runs"),iso(),watch_id,expected_version))
            self._audit(con,actor,"patch","watch",watch_id,before,after,reason)
        return self.watch_get(watch_id)

    def observation_add(self, *, subject_type: str, subject_id: str, metric: str, value: Any, observed_at: str, source: str, source_ref: str | None = None, quality: dict[str,Any] | None = None, watch_id: str | None = None) -> dict[str, Any]:
        fp=digest(subject_type,subject_id,metric,value,observed_at,source);oid=new_id("obs");now=iso()
        with self.db.transaction() as con:
            con.execute("INSERT OR IGNORE INTO observations(id,watch_id,subject_type,subject_id,metric,value_json,observed_at,source,source_ref,quality_json,fingerprint,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",(oid,watch_id,subject_type,subject_id,metric,canonical(value),observed_at,source,source_ref,canonical(quality or {}),fp,now))
            row=con.execute("SELECT * FROM observations WHERE fingerprint=?",(fp,)).fetchone()
        return row_dict(row)

    def evaluate_watch(self, watch_id: str, observation_id: str) -> dict[str, Any]:
        watch=self.watch_get(watch_id)
        with self.db.connect() as con:obs=row_dict(con.execute("SELECT * FROM observations WHERE id=?",(observation_id,)).fetchone())
        if not obs:raise CompanionError(f"observation not found: {observation_id}")
        cond=watch["condition"];op=cond.get("operator");threshold=cond.get("threshold");value=obs["value"]
        if isinstance(value,dict):value=value.get(cond.get("field","value"))
        matches={"gt":value>threshold,"gte":value>=threshold,"lt":value<threshold,"lte":value<=threshold,"eq":value==threshold}.get(op)
        if matches is None:raise CompanionError(f"unsupported watch operator: {op}")
        event=None;now=iso()
        with self.db.transaction() as con:
            current=con.execute("SELECT last_state FROM watches WHERE id=?",(watch_id,)).fetchone()[0]
            con.execute("UPDATE watches SET last_observation_json=?,last_state=?,last_success_at=?,run_count=run_count+1,updated_at=? WHERE id=?",(canonical(obs),1 if matches else 0,now,now,watch_id))
        cooldown_ok=True
        cooldown=int(cond.get("cooldown_seconds",0))
        if matches and not current and cooldown:
            with self.db.connect() as con:last=con.execute("SELECT detected_at FROM events WHERE watch_id=? ORDER BY detected_at DESC LIMIT 1",(watch_id,)).fetchone()
            cooldown_ok=not last or parse(last[0])+timedelta(seconds=cooldown)<=utc_now()
        if matches and not current and cooldown_ok:
            event=self.event_create(kind="watch_triggered",subject_type=watch["subject_type"],subject_id=watch["subject_id"],watch_id=watch_id,occurred_at=obs["observed_at"],summary=f"{watch['name']}：{obs['metric']}={value} {op} {threshold}",payload={"watch":watch,"observation":obs})
        return {"watch_id":watch_id,"matched":bool(matches),"transition":bool(matches and not current),"event":event}

    def event_create(self, *, kind: str, occurred_at: str, summary: str, payload: dict[str,Any], subject_type: str | None = None, subject_id: str | None = None, watch_id: str | None = None, case_id: str | None = None) -> dict[str, Any]:
        fp=digest(kind,subject_type,subject_id,watch_id,case_id,occurred_at,payload);eid=new_id("evt");now=iso()
        with self.db.transaction() as con:
            con.execute("INSERT OR IGNORE INTO events(id,kind,subject_type,subject_id,watch_id,case_id,occurred_at,detected_at,summary,payload_json,fingerprint,status) VALUES(?,?,?,?,?,?,?,?,?,?,?,'detected')",(eid,kind,subject_type,subject_id,watch_id,case_id,occurred_at,now,summary,canonical(payload),fp))
            row=con.execute("SELECT * FROM events WHERE fingerprint=?",(fp,)).fetchone()
        return row_dict(row)

    def event_list(self,status: str | None=None,limit:int=50)->list[dict[str,Any]]:
        with self.db.connect() as con:
            rows=con.execute("SELECT * FROM events"+(" WHERE status=?" if status else "")+" ORDER BY detected_at DESC LIMIT ?",((status,limit) if status else (limit,))).fetchall()
        return rows_dict(rows)

    def event_get(self,event_id:str)->dict[str,Any]:
        with self.db.connect() as con:item=row_dict(con.execute("SELECT * FROM events WHERE id=?",(event_id,)).fetchone())
        if not item:raise CompanionError(f"event not found: {event_id}")
        return item

    def event_acknowledge(self,event_id:str,note:str,actor:str="primary-codex")->dict[str,Any]:
        with self.db.transaction() as con:
            before=row_dict(con.execute("SELECT * FROM events WHERE id=?",(event_id,)).fetchone())
            if not before:raise CompanionError(f"event not found: {event_id}")
            con.execute("UPDATE events SET status='handled',handled_at=?,handling_note=? WHERE id=?",(iso(),note,event_id));self._audit(con,actor,"acknowledge","event",event_id,before,{"status":"handled","note":note})
        return self.event_get(event_id)

    def inbox_add(self, *, source: str, title: str, content: str | None = None, url: str | None = None, published_at: str | None = None, source_key: str | None = None, metadata: dict[str,Any] | None = None) -> dict[str,Any]:
        raw=(content or url or title).encode();h=hashlib.sha256(raw).hexdigest();sid=new_id("src");now=iso();path=None
        if content is not None:
            target=self.investigations/"inbox"/f"{sid}.md";temp=target.with_suffix(".tmp")
            temp.write_text(f"# {title}\n\n来源：{source}\n\nURL：{url or ''}\n\n发布时间：{published_at or ''}\n\n{content}\n",encoding="utf-8");os.replace(temp,target);path=str(target.relative_to(self.root))
        with self.db.transaction() as con:
            con.execute("INSERT OR IGNORE INTO source_items(id,source,source_key,title,url,published_at,captured_at,content_path,content_hash,metadata_json,status) VALUES(?,?,?,?,?,?,?,?,?,?,'new')",(sid,source,source_key,title,url,published_at,now,path,h,canonical(metadata or {})))
            row=con.execute("SELECT * FROM source_items WHERE source=? AND content_hash=?",(source,h)).fetchone()
        return row_dict(row)

    def inbox_list(self,status:str|None="new",limit:int=100)->list[dict[str,Any]]:
        with self.db.connect() as con:
            rows=con.execute("SELECT * FROM source_items"+(" WHERE status=?" if status else "")+" ORDER BY captured_at LIMIT ?",((status,limit) if status else (limit,))).fetchall()
        return rows_dict(rows)

    def inbox_set_status(self,item_id:str,status:str,actor:str="primary-codex")->dict[str,Any]:
        if status not in {"new","triaged","linked","archived","duplicate"}:raise CompanionError("invalid inbox status")
        with self.db.transaction() as con:
            before=row_dict(con.execute("SELECT * FROM source_items WHERE id=?",(item_id,)).fetchone())
            if not before:raise CompanionError(f"source item not found: {item_id}")
            con.execute("UPDATE source_items SET status=? WHERE id=?",(status,item_id));self._audit(con,actor,"status","source_item",item_id,before,{**before,"status":status})
        with self.db.connect() as con:return row_dict(con.execute("SELECT * FROM source_items WHERE id=?",(item_id,)).fetchone())

    def ingest_directory(self,path:str,source:str,glob_pattern:str="*.md",recursive:bool=True,limit:int=200)->dict[str,Any]:
        base=Path(path).expanduser().resolve()
        if not base.is_dir():raise CompanionError(f"source directory not found: {base}")
        if isinstance(limit,bool) or not isinstance(limit,int) or limit<=0:raise CompanionError("ingestion limit must be a positive integer")
        cursor_key="source_cursor:"+digest(str(base),source,glob_pattern)[:20]
        with self.db.connect() as con:
            row=con.execute("SELECT value FROM meta WHERE key=?",(cursor_key,)).fetchone();cursor=float(row[0]) if row else utc_now().timestamp()
        files=list(base.rglob(glob_pattern) if recursive else base.glob(glob_pattern));candidates=[]
        for path_item in files:
            try:
                resolved=path_item.resolve()
                if not resolved.is_file() or (resolved!=base and base not in resolved.parents):continue
                mtime=resolved.stat().st_mtime
                # Re-scan the watermark itself. Some filesystems expose coarse mtimes,
                # so a file created immediately after the last scan can equal cursor.
                if mtime>=cursor:candidates.append((mtime,resolved))
            except OSError:continue
        candidates.sort(key=lambda pair:(pair[0],str(pair[1])))
        added=[];max_mtime=cursor;examined=0
        for candidate_mtime,item in candidates:
            if len(added)>=limit:break
            try:
                content=item.read_text(encoding="utf-8");content_hash=hashlib.sha256(content.encode()).hexdigest();examined+=1
                with self.db.connect() as con:known=con.execute("SELECT id FROM source_items WHERE source=? AND content_hash=?",(source,content_hash)).fetchone()
                if known:
                    max_mtime=max(max_mtime,candidate_mtime);continue
                entry=self.inbox_add(source=source,title=item.stem,content=content,source_key=str(item),metadata={"origin_path":str(item),"mtime":candidate_mtime});added.append(entry["id"]);max_mtime=max(max_mtime,candidate_mtime)
            except (UnicodeDecodeError,OSError):continue
        if candidates:
            with self.db.transaction() as con:con.execute("INSERT OR REPLACE INTO meta(key,value) VALUES(?,?)",(cursor_key,str(max_mtime)))
        self.source_health_record(source,"healthy",cursor=str(max_mtime),coverage={"path":str(base),"matched":examined})
        return {"source":source,"path":str(base),"cursor_before":cursor,"matched":examined,"added":len(set(added)),"cursor_after":max_mtime}

    def source_health_record(self,source:str,status:str,error:str|None=None,cursor:str|None=None,coverage:dict[str,Any]|None=None)->dict[str,Any]:
        if status not in {"healthy","stale","partial","unauthorized","failed","unknown"}:raise CompanionError("invalid source health")
        now=iso()
        with self.db.transaction() as con:
            previous=con.execute("SELECT consecutive_failures FROM source_health WHERE source=?",(source,)).fetchone();failures=(previous[0] if previous else 0)+1 if status in {"failed","unauthorized"} else 0
            con.execute("INSERT INTO source_health(source,status,last_success_at,last_attempt_at,cursor,coverage_json,consecutive_failures,last_error,updated_at) VALUES(?,?,?,?,?,?,?,?,?) ON CONFLICT(source) DO UPDATE SET status=excluded.status,last_success_at=CASE WHEN excluded.status='healthy' THEN excluded.last_success_at ELSE source_health.last_success_at END,last_attempt_at=excluded.last_attempt_at,cursor=COALESCE(excluded.cursor,source_health.cursor),coverage_json=excluded.coverage_json,consecutive_failures=excluded.consecutive_failures,last_error=excluded.last_error,updated_at=excluded.updated_at",(source,status,now if status=="healthy" else None,now,cursor,canonical(coverage or {}),failures,error,now))
        return self.source_health_get(source)

    def source_health_get(self,source:str)->dict[str,Any]:
        with self.db.connect() as con:item=row_dict(con.execute("SELECT * FROM source_health WHERE source=?",(source,)).fetchone())
        if not item:raise CompanionError(f"source health not found: {source}")
        return item

    def source_health_list(self)->list[dict[str,Any]]:
        with self.db.connect() as con:return rows_dict(con.execute("SELECT * FROM source_health ORDER BY source").fetchall())

    def research_quality_status(self,days:int=30)->dict[str,Any]:
        if isinstance(days,bool) or not isinstance(days,int) or days<1 or days>365:raise CompanionError("research quality window must be within 1..365 days")
        cutoff=iso(utc_now()-timedelta(days=days));now=iso()
        with self.db.connect() as con:
            patrols=rows_dict(con.execute("SELECT * FROM patrols WHERE created_at>=? ORDER BY created_at DESC",(cutoff,)).fetchall())
            artifacts={item["path"]:item for item in rows_dict(con.execute("SELECT * FROM artifacts WHERE kind='patrol_result' AND created_at>=?",(cutoff,)).fetchall())}
            runs=rows_dict(con.execute("SELECT * FROM runs WHERE created_at>=? AND status IN ('succeeded','failed')",(cutoff,)).fetchall())
            pending=con.execute("SELECT COUNT(*) FROM outbox WHERE status IN ('pending','retry','sending')").fetchone()[0]
            dead=con.execute("SELECT COUNT(*) FROM outbox WHERE status='dead'").fetchone()[0]
        receipts=[];missing_receipts=0
        for patrol in patrols:
            artifact=artifacts.get(patrol.get("result_path"));receipt=(artifact or {}).get("subject",{}).get("evidence_receipt") if artifact else None
            if receipt:receipts.append(receipt)
            elif patrol.get("status")=="returned":missing_receipts+=1
        latencies=[]
        for run in runs:
            endpoint=run.get("started_at") or run.get("finished_at")
            if endpoint:latencies.append(max(0,int((parse(endpoint)-parse(run["due_at"])).total_seconds())))
        passed=sum(1 for item in receipts if item.get("coverage_status")=="passed")
        insufficient=sum(1 for item in receipts if item.get("coverage_status") in {"partial","insufficient"})
        checked=sum(int(item.get("checked_sources",0)) for item in receipts);primary=sum(int(item.get("primary_sources",0)) for item in receipts)
        issues=[]
        if missing_receipts:issues.append(f"{missing_receipts} returned patrols lack a source coverage receipt")
        if insufficient:issues.append(f"{insufficient} patrols were below the source coverage floor")
        if pending:issues.append(f"{pending} wake envelopes are still pending delivery")
        if dead:issues.append(f"{dead} wake envelopes are dead")
        if latencies and max(latencies)>900:issues.append("at least one task started more than 15 minutes late")
        return {"schema":"investment-companion.research-quality-status/v1","as_of":now,"window_days":days,"source_coverage":{"patrols":len(patrols),"receipts":len(receipts),"passed":passed,"insufficient":insufficient,"missing_receipts":missing_receipts,"checked_sources":checked,"primary_sources":primary,"primary_source_ratio":str(primary/checked) if checked else None},"scheduler":{"terminal_runs":len(runs),"latency_observations":len(latencies),"within_10_minutes":sum(1 for value in latencies if value<=600),"max_start_delay_seconds":max(latencies) if latencies else None,"mean_start_delay_seconds":round(sum(latencies)/len(latencies),2) if latencies else None},"delivery":{"pending":pending,"dead":dead},"sources":self.source_health_list(),"issues":issues,"status":"healthy" if not issues else "needs_attention"}

    def bootstrap_defaults(self,finance_source:str|None=None)->dict[str,Any]:
        existing=self.schedule_list();created=[]
        finance_source=finance_source or os.environ.get("COMPANION_FINANCE_SOURCE")
        if not any(x["origin"].get("bootstrap")=="v2-finance-patrol" for x in existing):
            scope={"inbox_sources":[{"path":finance_source,"source":"finance-feed","glob":"*.md","recursive":True,"limit":200}]} if finance_source else {"inbox_sources":[]}
            created.append(self.schedule_create(name="投资信息源收盘后巡视",kind="patrol",mission="巡视交易日新增的投资新闻、公告与深度文章，寻找与当前 Watch、Case、Thesis 或组合相关的 Observation、Suspicion 与 Challenge；无材料性变化时保持静默。",cadence={"type":"local_time","at":"17:30","timezone":"Asia/Shanghai","weekdays":[0,1,2,3,4]},scope=scope,policy={"notify":"material_only","max_patrols_per_day":1,"misfire":"run_once"},origin={"bootstrap":"v2-finance-patrol"}))
        if not any(x["origin"].get("bootstrap")=="v2-weekly-gardener" for x in existing):
            created.append(self.schedule_create(name="认知园丁周度体检",kind="maintenance",mission="检查 Inbox 去重、开放 Case、临时 Watch、失败运行、陈旧线索与无主材料；只做安全整理并提交认知性变更建议。",cadence={"type":"local_time","at":"10:00","timezone":"Asia/Shanghai","weekdays":[6]},policy={"mode":"weekly","physical_delete":False,"notify":"exceptions_only","misfire":"run_once"},origin={"bootstrap":"v2-weekly-gardener"}))
        if not any(x["origin"].get("bootstrap")=="v2-monthly-gardener" for x in existing):
            created.append(self.schedule_create(name="认知园丁月度归纳",kind="maintenance",mission="按 Case、标的、线索和时间增量维护 Timeline、当前证据状态与归档建议；不物理删除，不擅自修改正式 Thesis。",cadence={"type":"monthly","day":1,"at":"10:30","timezone":"Asia/Shanghai"},policy={"mode":"monthly","physical_delete":False,"notify":"material_only","misfire":"run_once"},origin={"bootstrap":"v2-monthly-gardener"}))
        return {"created":created,"schedules":self.schedule_list()}

    def workspace_init(self,finance_source:str|None=None)->dict[str,Any]:
        initialized=self.initialize();defaults=self.bootstrap_defaults(finance_source);contexts=[]
        if not self.cognition.context_list("investor"):contexts.append(self.cognition.context_create("investor",{"status":"uninitialized","confirmed_facts":[]},"workspace bootstrap; requires user confirmation"))
        if not self.cognition.context_list("mandate"):contexts.append(self.cognition.context_create("mandate",{"status":"uninitialized","hard_constraints":{},"soft_preferences":{}},"workspace bootstrap; requires user confirmation"))
        if not self.cognition.context_current("attention"):
            draft=self.cognition.context_create("attention",{"timezone":"Asia/Shanghai","quiet_hours":{"start":"22:00","end":"08:00"},"daily_notification_budget":3,"topic_cooldown_seconds":21600,"quiet_bypass_materiality":["critical"],"budget_bypass_materiality":["critical"],"channels":{"material":"notify_now","normal":"queue_digest","low":"file_only"}},"conservative system default")
            contexts.append(self.cognition.context_confirm(draft["id"]))
        return {"ok":True,"workspace":initialized,"created_schedules":[x["id"] for x in defaults["created"]],"created_context_revisions":[x["id"] for x in contexts],"schedules":defaults["schedules"]}

    def case_create(self, *, title:str, brief:str, subject:dict[str,Any]|None=None, origin:dict[str,Any]|None=None, actor:str="primary-codex")->dict[str,Any]:
        cid=new_id("case");root=self.investigations/"cases"/cid
        for name in ["sources","patrols","analysis","critiques"]:(root/name).mkdir(parents=True,exist_ok=True)
        brief_path=root/"BRIEF.md";brief_path.write_text(brief.rstrip()+"\n",encoding="utf-8")
        for name,body in [("STATUS.md",f"# {title}\n\n状态：proposed\n"),("TIMELINE.md","# 时间线\n"),("CURRENT.md","# 当前证据状态\n"),("OPEN-QUESTIONS.md","# 未决问题\n")]: (root/name).write_text(body,encoding="utf-8")
        now=iso();rel=str(root.relative_to(self.root));brel=str(brief_path.relative_to(self.root))
        with self.db.transaction() as con:
            con.execute("INSERT INTO cases(id,title,status,subject_json,origin_json,root_path,brief_path,created_at,updated_at) VALUES(?,?,'proposed',?,?,?,?,?,?)",(cid,title,canonical(subject or {}),canonical(origin or {}),rel,brel,now,now));self._audit(con,actor,"create","case",cid,after={"title":title,"brief_path":brel})
        self.artifact_register(brel,"brief",case_id=cid,subject=subject or {})
        return self.case_get(cid)

    def case_get(self,case_id:str)->dict[str,Any]:
        with self.db.connect() as con:item=row_dict(con.execute("SELECT * FROM cases WHERE id=?",(case_id,)).fetchone())
        if not item:raise CompanionError(f"case not found: {case_id}")
        return item

    def case_list(self,status:str|None=None)->list[dict[str,Any]]:
        with self.db.connect() as con:rows=con.execute("SELECT * FROM cases"+(" WHERE status=?" if status else "")+" ORDER BY updated_at DESC",((status,) if status else ())).fetchall()
        return rows_dict(rows)

    def case_set_status(self,case_id:str,status:str,reason:str|None=None,actor:str="primary-codex")->dict[str,Any]:
        allowed={"open","investigating","awaiting_evidence","ready_for_judgment","resolved","monitoring","rejected","superseded","expired"}
        if status not in allowed:raise CompanionError("invalid case status")
        now=iso();closed=now if status in {"resolved","rejected","superseded","expired"} else None
        with self.db.transaction() as con:
            before=row_dict(con.execute("SELECT * FROM cases WHERE id=?",(case_id,)).fetchone())
            if not before:raise CompanionError(f"case not found: {case_id}")
            con.execute("UPDATE cases SET status=?,version=version+1,updated_at=?,closed_at=?,close_reason=? WHERE id=?",(status,now,closed,reason,case_id));self._audit(con,actor,"status","case",case_id,before,{"status":status,"reason":reason})
        return self.case_get(case_id)

    def case_patch(self,case_id:str,expected_version:int,changes:dict[str,Any],actor:str="primary-codex",reason:str|None=None)->dict[str,Any]:
        allowed={"title","subject","origin"};unknown=set(changes)-allowed
        if unknown:raise CompanionError(f"unsupported case fields: {sorted(unknown)}")
        with self.db.transaction() as con:
            before=row_dict(con.execute("SELECT * FROM cases WHERE id=?",(case_id,)).fetchone())
            if not before:raise CompanionError(f"case not found: {case_id}")
            if before["version"]!=expected_version:raise CompanionError(f"version conflict: expected {expected_version}, current {before['version']}")
            after={**before,**changes}
            con.execute("UPDATE cases SET title=?,subject_json=?,origin_json=?,version=version+1,updated_at=? WHERE id=? AND version=?",(after["title"],canonical(after["subject"]),canonical(after["origin"]),iso(),case_id,expected_version));self._audit(con,actor,"patch","case",case_id,before,after,reason)
        return self.case_get(case_id)

    def patrol_commission(self, *, brief_path:str,case_id:str|None=None,schedule_id:str|None=None,budget:dict[str,Any]|None=None,actor:str="primary-codex")->dict[str,Any]:
        full=(self.root/brief_path).resolve()
        if not full.is_file() or self.root not in full.parents:raise CompanionError("brief_path must be an existing file inside workspace")
        pid=new_id("patrol");now=iso()
        with self.db.transaction() as con:
            con.execute("INSERT INTO patrols(id,case_id,schedule_id,status,brief_path,budget_json,created_at) VALUES(?,?,?,'commissioned',?,?,?)",(pid,case_id,schedule_id,brief_path,canonical(budget or {"max_minutes":20,"max_sources":20,"max_spawn_depth":0}),now));self._audit(con,actor,"commission","patrol",pid,after={"brief_path":brief_path,"case_id":case_id})
        return self.patrol_get(pid)

    def patrol_get(self,patrol_id:str)->dict[str,Any]:
        with self.db.connect() as con:item=row_dict(con.execute("SELECT * FROM patrols WHERE id=?",(patrol_id,)).fetchone())
        if not item:raise CompanionError(f"patrol not found: {patrol_id}")
        return item

    def patrol_list(self,status:str|None=None)->list[dict[str,Any]]:
        with self.db.connect() as con:rows=con.execute("SELECT * FROM patrols"+(" WHERE status=?" if status else "")+" ORDER BY created_at DESC",((status,) if status else ())).fetchall()
        return rows_dict(rows)

    def patrol_complete(self,patrol_id:str,result_path:str,disposition:str,evidence_receipt:dict[str,Any]|None=None)->dict[str,Any]:
        full=(self.root/result_path).resolve()
        if not full.is_file() or self.root not in full.parents:raise CompanionError("result_path must be an existing file inside workspace")
        item=self.patrol_get(patrol_id);schedule=self.schedule_get(item["schedule_id"]) if item.get("schedule_id") else None
        contract=(schedule or {}).get("policy",{}).get("research_quality_contract",{})
        receipt=evidence_receipt or {}
        if contract.get("required"):
            required={"as_of","checked_sources","primary_sources","discovery_sources","material_findings","coverage_status","gaps"}
            if set(receipt)!=required:raise CompanionError("patrol evidence_receipt does not match the required quality contract")
            for field in ("checked_sources","primary_sources","discovery_sources","material_findings"):
                if isinstance(receipt[field],bool) or not isinstance(receipt[field],int) or receipt[field]<0:raise CompanionError(f"patrol evidence_receipt.{field} must be a non-negative integer")
            if receipt["coverage_status"] not in {"passed","partial","insufficient"}:raise CompanionError("invalid patrol coverage_status")
            if not isinstance(receipt["gaps"],list):raise CompanionError("patrol evidence_receipt.gaps must be a list")
            minimum=int(contract.get("min_checked_sources",3));primary_min=int(contract.get("min_primary_sources",1))
            passed=receipt["checked_sources"]>=minimum and receipt["primary_sources"]>=primary_min and receipt["coverage_status"]=="passed"
            if disposition=="no_material_change" and not passed:raise CompanionError("no_material_change requires passed source coverage; use insufficient_coverage")
            if not passed and disposition!="insufficient_coverage":raise CompanionError("patrol below its source quality floor must use insufficient_coverage")
        with self.db.transaction() as con:
            con.execute("UPDATE patrols SET status='returned',disposition=?,result_path=?,finished_at=?,lease_owner=NULL,lease_until=NULL WHERE id=?",(disposition,result_path,iso(),patrol_id))
        item=self.patrol_get(patrol_id);self.artifact_register(result_path,"patrol_result",subject={"evidence_receipt":receipt},case_id=item.get("case_id"));return item

    def artifact_register(self,path:str,kind:str,subject:dict[str,Any]|None=None,case_id:str|None=None,watch_id:str|None=None,event_id:str|None=None,status:str="current",effective_at:str|None=None,supersedes:str|None=None)->dict[str,Any]:
        full=(self.root/path).resolve()
        if not full.is_file() or self.root not in full.parents:raise CompanionError("artifact path must be inside workspace")
        h=hashlib.sha256(full.read_bytes()).hexdigest();aid=new_id("art");now=iso()
        with self.db.transaction() as con:
            con.execute("INSERT INTO artifacts(id,path,kind,subject_json,case_id,watch_id,event_id,status,effective_at,supersedes,content_hash,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(path) DO UPDATE SET kind=excluded.kind,status=excluded.status,content_hash=excluded.content_hash,updated_at=excluded.updated_at",(aid,path,kind,canonical(subject or {}),case_id,watch_id,event_id,status,effective_at,supersedes,h,now,now))
            row=con.execute("SELECT * FROM artifacts WHERE path=?",(path,)).fetchone()
        return row_dict(row)

    def artifact_list(self,case_id:str|None=None,status:str|None=None,limit:int=100)->list[dict[str,Any]]:
        q,p="SELECT * FROM artifacts WHERE 1=1",[]
        if case_id:q+=" AND case_id=?";p.append(case_id)
        if status:q+=" AND status=?";p.append(status)
        q+=" ORDER BY effective_at DESC,created_at DESC LIMIT ?";p.append(limit)
        with self.db.connect() as con:return rows_dict(con.execute(q,p).fetchall())

    def artifact_get(self,artifact_id:str)->dict[str,Any]:
        with self.db.connect() as con:item=row_dict(con.execute("SELECT * FROM artifacts WHERE id=?",(artifact_id,)).fetchone())
        if not item:raise CompanionError(f"artifact not found: {artifact_id}")
        return item

    def outbox_enqueue(self, *, kind:str,destination:str,payload:dict[str,Any],event_id:str|None=None,idempotency_key:str|None=None)->dict[str,Any]:
        oid=new_id("out");now=iso();key=idempotency_key or digest(kind,destination,event_id,payload)
        with self.db.transaction() as con:
            con.execute("INSERT OR IGNORE INTO outbox(id,event_id,kind,destination,payload_json,idempotency_key,status,available_at,created_at,updated_at) VALUES(?,?,?,?,?,?,'pending',?,?,?)",(oid,event_id,kind,destination,canonical(payload),key,now,now,now));row=con.execute("SELECT * FROM outbox WHERE idempotency_key=?",(key,)).fetchone()
            if event_id:con.execute("UPDATE events SET status='queued' WHERE id=? AND status='detected'",(event_id,))
        return row_dict(row)

    def outbox_list(self,status:str|None=None,limit:int=50)->list[dict[str,Any]]:
        with self.db.connect() as con:rows=con.execute("SELECT * FROM outbox"+(" WHERE status=?" if status else "")+" ORDER BY created_at LIMIT ?",((status,limit) if status else (limit,))).fetchall()
        return rows_dict(rows)

    def outbox_claim(self,owner:str,lease_seconds:int=300)->dict[str,Any]|None:
        now,until=iso(),iso(utc_now()+timedelta(seconds=lease_seconds))
        with self.db.transaction() as con:
            row=con.execute("SELECT * FROM outbox WHERE status IN ('pending','retry') AND available_at<=? ORDER BY available_at LIMIT 1",(now,)).fetchone()
            if not row:return None
            con.execute("UPDATE outbox SET status='sending',lease_owner=?,lease_until=?,attempt=attempt+1,updated_at=? WHERE id=?",(owner,until,now,row["id"]))
        with self.db.connect() as con:return row_dict(con.execute("SELECT * FROM outbox WHERE id=?",(row["id"],)).fetchone())

    def outbox_finish(self,outbox_id:str,success:bool,error:str|None=None)->dict[str,Any]:
        now=iso()
        with self.db.transaction() as con:
            row=row_dict(con.execute("SELECT * FROM outbox WHERE id=?",(outbox_id,)).fetchone())
            if not row:raise CompanionError(f"outbox not found: {outbox_id}")
            if success:
                con.execute("UPDATE outbox SET status='sent',sent_at=?,last_error=NULL,lease_owner=NULL,lease_until=NULL,updated_at=? WHERE id=?",(now,now,outbox_id))
                if row.get("event_id"):con.execute("UPDATE events SET status='delivered' WHERE id=? AND status IN ('detected','queued')",(row["event_id"],))
            else:
                attempt=row["attempt"];status="dead" if attempt>=5 else "retry";delay=min(3600,30*(2**max(0,attempt-1)))
                con.execute("UPDATE outbox SET status=?,available_at=?,last_error=?,lease_owner=NULL,lease_until=NULL,updated_at=? WHERE id=?",(status,iso(utc_now()+timedelta(seconds=delay)),error,now,outbox_id))
                if status=="dead" and row.get("event_id"):con.execute("UPDATE events SET status='dead' WHERE id=? AND status IN ('detected','queued')",(row["event_id"],))
        with self.db.connect() as con:
            result=row_dict(con.execute("SELECT * FROM outbox WHERE id=?",(outbox_id,)).fetchone())
        if row["kind"] == "user_result":
            self.delivery.mark_outbox_result(outbox_id, success=success, terminal=result["status"] == "dead", error=error)
        return result

    def dispatch_outbox(self,dry_run:bool=False,limit:int=1)->dict[str,Any]:
        wake_cron=os.environ.get("COMPANION_CC_WAKE_CRON")
        owner=f"wake-dispatch:{socket.gethostname()}:{os.getpid()}";results=[]
        for _ in range(limit):
            item=self.outbox_claim(owner)
            if not item:break
            if dry_run:
                results.append({"id":item["id"],"dry_run":True,"destination":item["destination"],"payload":item["payload"]});self.outbox_finish(item["id"],False,"dry-run: delivery intentionally not attempted");continue
            try:
                if item["kind"] not in {"codex_turn","user_result"}:raise CompanionError(f"unsupported outbox kind: {item['kind']}")
                if item["kind"] == "user_result":
                    with self.db.transaction() as con:
                        con.execute("UPDATE delivery_records SET status='sending',updated_at=? WHERE outbox_id=? AND status IN ('pending_send','retry')",(iso(),item["id"]))
                if wake_cron and item["kind"] == "codex_turn":
                    command=["/home/ghk/.local/bin/cc-connect","cron","exec",wake_cron]
                else:
                    command=["/home/ghk/.local/bin/cc-connect","send","-p",item["destination"]]
                    session_key=os.environ.get("COMPANION_CC_SESSION")
                    if session_key:command.extend(["-s",session_key])
                    command.extend(["--message",item["payload"]["message"]])
                proc=subprocess.run(command,capture_output=True,text=True,timeout=60,check=False)
                if proc.returncode!=0:raise CompanionError((proc.stderr or proc.stdout).strip() or f"cc-connect exited {proc.returncode}")
                if item["kind"] == "user_result":
                    self.outbox_finish(item["id"],True)
                    results.append({"id":item["id"],"sent":True,"transport":"direct","output":proc.stdout.strip()})
                else:
                    results.append({"id":item["id"],"signaled":True,"transport":"cron" if wake_cron else "direct","output":proc.stdout.strip()})
            except Exception as e:
                self.outbox_finish(item["id"],False,str(e));results.append({"id":item["id"],"sent":False,"error":str(e)})
        return {"ok":all(r.get("sent",r.get("signaled",r.get("dry_run",False))) for r in results),"results":results}

    def wake_claim(self,owner:str,lease_seconds:int=1800)->dict[str,Any]|None:
        """Claim the exact envelope that caused a static cc-connect wake-up."""

        if not isinstance(owner,str) or not owner.strip():raise CompanionError("wake owner must be non-empty")
        if not 60<=lease_seconds<=7200:raise CompanionError("wake lease_seconds must be within 60..7200")
        owner=f"primary-codex:{owner.strip()}";now=iso();until=iso(utc_now()+timedelta(seconds=lease_seconds))
        with self.db.transaction() as con:
            row=con.execute(
                "SELECT * FROM outbox WHERE kind='codex_turn' AND ("
                "(status='sending' AND lease_owner LIKE 'wake-dispatch:%' AND lease_until>?) OR "
                "(status IN ('pending','retry') AND available_at<=?)) "
                "ORDER BY CASE status WHEN 'sending' THEN 0 ELSE 1 END,available_at,created_at LIMIT 1",
                (now,now),
            ).fetchone()
            if not row:return None
            item=row_dict(row)
            if item["kind"]!="codex_turn":raise CompanionError(f"unsupported wake outbox kind: {item['kind']}")
            if item["status"] in {"pending","retry"}:
                con.execute("UPDATE outbox SET status='sending',attempt=attempt+1,lease_owner=?,lease_until=?,updated_at=? WHERE id=?",(owner,until,now,item["id"]))
            else:
                con.execute("UPDATE outbox SET lease_owner=?,lease_until=?,updated_at=? WHERE id=?",(owner,until,now,item["id"]))
            run_id=item["payload"].get("run_id")
            if run_id:
                run=con.execute("SELECT status,lease_owner,due_at FROM runs WHERE id=?",(run_id,)).fetchone()
                if not run:raise CompanionError(f"wake envelope references missing Run: {run_id}")
                if run["status"] in {"queued","recoverable"}:
                    if run["due_at"]>now:raise CompanionError("wake envelope Run is not due")
                    changed=con.execute("UPDATE runs SET status='leased',lease_owner=?,lease_until=?,attempt=attempt+1,started_at=COALESCE(started_at,?) WHERE id=? AND status IN ('queued','recoverable')",(owner,until,now,run_id)).rowcount
                    if changed!=1:raise CompanionError("wake Run claim lost to another worker")
                elif run["status"]=="leased" and (run["lease_owner"] or "").startswith(("cc-connect:","wake-dispatch:")):
                    con.execute("UPDATE runs SET lease_owner=?,lease_until=? WHERE id=?",(owner,until,run_id))
                elif run["status"]!="leased" or run["lease_owner"]!=owner:
                    raise CompanionError(f"wake envelope Run is not claimable: {run['status']}")
        with self.db.connect() as con:item=row_dict(con.execute("SELECT * FROM outbox WHERE id=?",(item["id"],)).fetchone())
        payload=item["payload"]
        envelope_type=payload.get("envelope_type")
        if not envelope_type:
            if payload.get("run_id"):envelope_type="scheduled_run"
            elif item.get("event_id") or payload.get("job_run_id"):envelope_type="research_ready"
            else:envelope_type="legacy_codex_turn"
        envelope={
            "schema":payload.get("schema","investment-companion.wake-envelope/legacy"),
            "type":envelope_type,
            "message":payload.get("message",""),
            "payload":payload,
            "event_id":item.get("event_id"),
            "run_id":payload.get("run_id"),
            "job_run_id":payload.get("job_run_id"),
        }
        return {"outbox_id":item["id"],"lease_owner":owner,"lease_until":item["lease_until"],"envelope":envelope,"run":self.run_get(payload["run_id"]) if payload.get("run_id") else None}

    def wake_complete(self,outbox_id:str,owner:str,success:bool,error:str|None=None)->dict[str,Any]:
        if not isinstance(owner,str) or not owner.strip():raise CompanionError("wake owner must be non-empty")
        lease_owner=f"primary-codex:{owner.strip()}"
        now=iso()
        with self.db.transaction() as con:
            item=row_dict(con.execute("SELECT * FROM outbox WHERE id=?",(outbox_id,)).fetchone())
            if not item:raise CompanionError(f"outbox not found: {outbox_id}")
            if item["status"]!="sending" or item.get("lease_owner")!=lease_owner or not item.get("lease_until") or item["lease_until"]<=now:raise CompanionError("wake completion does not own the active outbox lease")
            run_id=item["payload"].get("run_id")
            if success and run_id:
                run=con.execute("SELECT status FROM runs WHERE id=?",(run_id,)).fetchone()
                if not run or run["status"] not in {"succeeded","failed","cancelled"}:raise CompanionError("complete the parent Run before successful wake completion")
            if success:
                con.execute("UPDATE outbox SET status='sent',sent_at=?,last_error=NULL,lease_owner=NULL,lease_until=NULL,updated_at=? WHERE id=?",(now,now,outbox_id))
                if item.get("event_id"):con.execute("UPDATE events SET status='delivered' WHERE id=? AND status IN ('detected','queued')",(item["event_id"],))
            else:
                if run_id:con.execute("UPDATE runs SET status='recoverable',lease_owner=NULL,lease_until=NULL,error=? WHERE id=? AND status='leased' AND lease_owner=?",(error or "wake failed",run_id,lease_owner))
                attempt=item["attempt"];status="dead" if attempt>=5 else "retry";delay=min(3600,30*(2**max(0,attempt-1)))
                con.execute("UPDATE outbox SET status=?,available_at=?,last_error=?,lease_owner=NULL,lease_until=NULL,updated_at=? WHERE id=?",(status,iso(utc_now()+timedelta(seconds=delay)),error,now,outbox_id))
                if status=="dead" and item.get("event_id"):con.execute("UPDATE events SET status='dead' WHERE id=? AND status IN ('detected','queued')",(item["event_id"],))
            result=row_dict(con.execute("SELECT * FROM outbox WHERE id=?",(outbox_id,)).fetchone())
        return result

    def recover(self)->dict[str,Any]:
        now=iso();recovered={"runs":0,"patrols":0,"outbox":0,"job_runs":0,"job_steps":0}
        integrity=self.db.integrity_check()
        if integrity!="ok":raise CompanionError(f"database integrity check failed: {integrity}")
        stale_delivery_outbox_ids=[]
        with self.db.transaction() as con:
            recovered["runs"]=con.execute("UPDATE runs SET status='recoverable',lease_owner=NULL,lease_until=NULL WHERE status='leased' AND lease_until<=?",(now,)).rowcount
            recovered["patrols"]=con.execute("UPDATE patrols SET status='commissioned',lease_owner=NULL,lease_until=NULL WHERE status='scanning' AND lease_until<=?",(now,)).rowcount
            stale_delivery_outbox_ids=[row["id"] for row in con.execute("SELECT id FROM outbox WHERE kind='user_result' AND status='sending' AND lease_until<=?",(now,)).fetchall()]
            recovered["outbox"]=con.execute("UPDATE outbox SET status='retry',lease_owner=NULL,lease_until=NULL,available_at=?,updated_at=? WHERE status='sending' AND lease_until<=?",(now,now,now)).rowcount
            con.execute("INSERT OR REPLACE INTO meta(key,value) VALUES('last_recovery_at',?)",(now,))
        job_recovery=self.jobs.recover();recovered.update(job_recovery)
        recovered["deliveries"]=sum(self.delivery.recover_outbox(outbox_id) for outbox_id in stale_delivery_outbox_ids)
        return {"ok":True,"integrity":integrity,"recovered":recovered,"at":now}

    def backup(self,destination:str|Path)->dict[str,Any]:
        target=self.db.backup(destination);now=iso()
        with self.db.transaction() as con:
            con.execute("INSERT OR REPLACE INTO meta(key,value) VALUES('last_backup_at',?)",(now,))
            con.execute("INSERT OR REPLACE INTO meta(key,value) VALUES('last_backup_path',?)",(str(target),))
        return {"ok":True,"path":str(target),"at":now}

    def backup_auto(self,directory:str|Path)->dict[str,Any]:
        base=Path(directory).expanduser().resolve();base.mkdir(parents=True,exist_ok=True)
        return self.backup(base/f"companion-{utc_now().strftime('%Y%m%dT%H%M%SZ')}.db")

    def system_status(self)->dict[str,Any]:
        integrity=self.db.integrity_check()
        with self.db.connect() as con:
            meta={r["key"]:r["value"] for r in con.execute("SELECT * FROM meta").fetchall()}
            counts={table:con.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] for table in ["schedules","runs","watches","events","cases","patrols","source_items","artifacts","outbox","delivery_records","accounts","assets","ledger_entries","calculations","context_revisions","cognitive_objects","cognitive_revisions","executions","attention_decisions","source_health","job_definitions","job_runs","data_objects","artifact_manifests","dataset_snapshots","research_hypotheses","strategy_versions","experiment_runs","agent_invocations","shadow_books","manual_action_specs","investment_programs","investment_program_revisions","opportunities","decision_queue_items","operating_briefs","program_scorecards"]}
            failures=con.execute("SELECT COUNT(*) FROM runs WHERE status='failed'").fetchone()[0]
            pending=con.execute("SELECT COUNT(*) FROM outbox WHERE status IN ('pending','retry','sending')").fetchone()[0]
            migrations=rows_dict(con.execute("SELECT * FROM schema_migrations ORDER BY version").fetchall())
        return {"ok":integrity=="ok","integrity":integrity,"database":str(self.db.path),"meta":meta,"migrations":migrations,"feature_flags":self.jobs.feature_list(),"counts":counts,"failed_runs":failures,"pending_outbox":pending,"delivery":self.delivery.status(),"now":iso()}

    def session_brief(self)->dict[str,Any]:
        """Return a small, deterministic orientation payload for a new Codex session."""
        status=self.system_status()
        contexts={}
        for kind in ("investor","mandate","attention"):
            current=self.cognition.context_current(kind)
            revisions=self.cognition.context_list(kind)
            contexts[kind]={
                "state":"confirmed" if current else "draft" if revisions else "missing",
                "revision_id":current["id"] if current else None,
            }
        with self.db.connect() as con:
            active={
                "schedules":con.execute("SELECT COUNT(*) FROM schedules WHERE status='active'").fetchone()[0],
                "watches":con.execute("SELECT COUNT(*) FROM watches WHERE status='active'").fetchone()[0],
                "cases":con.execute("SELECT COUNT(*) FROM cases WHERE status NOT IN ('resolved','rejected','superseded','expired')").fetchone()[0],
                "pending_runs":con.execute("SELECT COUNT(*) FROM runs WHERE status IN ('queued','recoverable','leased')").fetchone()[0],
                "theses":con.execute("SELECT COUNT(*) FROM cognitive_objects WHERE object_type='thesis' AND status='active'").fetchone()[0],
                "opportunities":con.execute("SELECT COUNT(*) FROM opportunities WHERE status='active'").fetchone()[0],
                "decision_queue":con.execute("SELECT COUNT(*) FROM decision_queue_items WHERE state IN ('ready','presented','snoozed','accepted') AND valid_until>?",(iso(),)).fetchone()[0],
            }
        program=self.operating.program_current()
        lines=[
            "Investment Companion workspace detected.",
            f"Database integrity: {status['integrity']}; schema: {status['meta'].get('schema_version','unknown')}.",
            "Contexts: "+", ".join(f"{kind}={item['state']}" for kind,item in contexts.items())+".",
            "Active state: "+", ".join(f"{key}={value}" for key,value in active.items())+".",
            f"Financial facts: accounts={status['counts']['accounts']}, ledger_entries={status['counts']['ledger_entries']}.",
            f"Investment program: {program['id'] if program else 'missing'}; user decision queue={active['decision_queue']}.",
            "This is orientation, not investment evidence. Do not infer facts from chat history or Markdown current views.",
            "For an investment task, enter through the V5 operating loop, then use lifecycle/research/decision skills and create a bounded Recovery Package as required.",
        ]
        return {"ok":status["ok"],"workspace":str(self.root),"contexts":contexts,"active":active,"financial":{"accounts":status["counts"]["accounts"],"ledger_entries":status["counts"]["ledger_entries"]},"text":"\n".join(lines)}

    def doctor(self)->dict[str,Any]:
        status=self.system_status();checks={"database_integrity":status["integrity"]=="ok","workspace_writable":os.access(self.root,os.W_OK),"attention_policy":self.cognition.context_current("attention") is not None,"investor_confirmed":bool(self.cognition.context_current("investor")),"mandate_confirmed":bool(self.cognition.context_current("mandate"))}
        checks["financial_facts_ready"]=status["counts"]["accounts"]>0 and status["counts"]["ledger_entries"]>0
        checks["schema_current"]=status["meta"].get("schema_version")==str(SCHEMA_VERSION)
        checks["ordered_migrations"]=len(status.get("migrations",[]))>=3
        checks["required_results_not_overdue"]=status["delivery"]["overdue_required"]==0
        project_config=self.root/".codex"/"config.toml"
        if project_config.is_file():
            from .agent_config import validate_agent_config
            checks["custom_agent_config"]=validate_agent_config(self.root)["ok"]
        return {"ok":all(v for k,v in checks.items() if k not in {"investor_confirmed","mandate_confirmed","financial_facts_ready"}),"checks":checks,"warnings":[k for k,v in checks.items() if not v],"status":status}
