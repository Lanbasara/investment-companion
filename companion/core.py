from __future__ import annotations

import hashlib
import json
import os
from datetime import timedelta
from pathlib import Path
from typing import Any

from .bootstrap import compose_services
from .db import SCHEMA_VERSION, Database, row_dict, rows_dict
from .foundation import CompanionError, canonical, digest, new_id
from .platform.operations import SystemOperationsService
from .platform.outbox import OutboxService
from .platform.workflow import WorkflowService
from .timeutil import iso, parse, utc_now


class Companion(WorkflowService, OutboxService, SystemOperationsService):
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
        compose_services(self)

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
        """Compatibility shim for legacy methods still hosted by Companion."""
        self.audit.record(con, actor, action, entity_type, entity_id, before, after, reason)

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
