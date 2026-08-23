from __future__ import annotations

import json
from decimal import Decimal
from typing import Any

from .foundation import CompanionError, canonical, digest, new_id
from .db import row_dict, rows_dict
from .timeutil import iso
from .quant_runtime import RealitySpec, verify_artifact_hash


HYPOTHESIS_REQUIRED = {
    "economic_logic",
    "falsifiers",
    "universe",
    "data_requirements",
    "benchmark",
    "stop_conditions",
}

STRATEGY_REQUIRED = {
    "method",
    "universe",
    "features",
    "label",
    "availability_lag",
    "portfolio",
    "costs",
    "benchmark",
    "candidate_config",
    "benchmark_config",
    "split",
    "evaluation_plan",
    "leakage_controls",
    "metrics",
    "pass_fail",
    "max_experiments",
    "minimum_experiments",
    "required_evaluations",
    "stop_conditions",
}


class ResearchRegistry:
    """Project-owned immutable hypotheses, strategies and experiment truth."""

    def __init__(self, companion):
        self.c = companion
        self.db = companion.db

    def hypothesis_create(
        self,
        *,
        name: str,
        spec: dict[str, Any],
        experiment_budget: int,
        preregister: bool = True,
    ) -> dict[str, Any]:
        missing = HYPOTHESIS_REQUIRED - set(spec)
        if missing:
            raise CompanionError(f"hypothesis preregistration missing: {sorted(missing)}")
        if experiment_budget <= 0:
            raise CompanionError("experiment budget must be positive")
        hid, now = new_id("hyp"), iso()
        content_hash = digest("research-hypothesis-v1", name, spec, experiment_budget)
        with self.db.transaction() as con:
            con.execute(
                "INSERT OR IGNORE INTO research_hypotheses(id,name,status,spec_json,content_hash,experiment_budget,created_at,updated_at) VALUES(?,?,?, ?,?,?,?,?)",
                (
                    hid,
                    name,
                    "preregistered" if preregister else "draft",
                    canonical(spec),
                    content_hash,
                    experiment_budget,
                    now,
                    now,
                ),
            )
            row = con.execute(
                "SELECT * FROM research_hypotheses WHERE content_hash=?", (content_hash,)
            ).fetchone()
        return row_dict(row)

    def hypothesis_get(self, hypothesis_id: str) -> dict[str, Any]:
        with self.db.connect() as con:
            item = row_dict(
                con.execute("SELECT * FROM research_hypotheses WHERE id=?", (hypothesis_id,)).fetchone()
            )
        if not item:
            raise CompanionError(f"research hypothesis not found: {hypothesis_id}")
        return item

    def hypothesis_list(self, status: str | None = None) -> list[dict[str, Any]]:
        with self.db.connect() as con:
            if status:
                rows = con.execute(
                    "SELECT * FROM research_hypotheses WHERE status=? ORDER BY created_at DESC",
                    (status,),
                ).fetchall()
            else:
                rows = con.execute(
                    "SELECT * FROM research_hypotheses ORDER BY created_at DESC"
                ).fetchall()
        return rows_dict(rows)

    def strategy_register(
        self,
        *,
        hypothesis_id: str,
        spec: dict[str, Any],
        code_ref: str,
        environment_ref: str,
        parent_id: str | None = None,
    ) -> dict[str, Any]:
        hypothesis = self.hypothesis_get(hypothesis_id)
        if hypothesis["status"] != "preregistered":
            raise CompanionError("strategy requires a preregistered hypothesis")
        missing = STRATEGY_REQUIRED - set(spec)
        if missing:
            raise CompanionError(f"strategy preregistration missing: {sorted(missing)}")
        if int(spec["max_experiments"]) <= 0:
            raise CompanionError("strategy max_experiments must be positive")
        if int(spec["max_experiments"]) > int(hypothesis["experiment_budget"]):
            raise CompanionError("strategy experiment budget exceeds hypothesis budget")
        if int(spec["minimum_experiments"])<=0 or int(spec["minimum_experiments"])>int(spec["max_experiments"]):raise CompanionError("strategy minimum_experiments must be within experiment budget")
        required_phases={"development","validation","final_holdout"}
        if not isinstance(spec["required_evaluations"],list) or set(spec["required_evaluations"])!=required_phases or len(spec["required_evaluations"])!=3:raise CompanionError("strategy must preregister development, validation and final_holdout exactly once")
        split=spec["split"]
        partitions=split.get("partitions") if isinstance(split,dict) else None
        if not isinstance(partitions,dict):raise CompanionError("strategy split must preregister physical partition names")
        if set(partitions)!=required_phases:raise CompanionError("strategy split partition phases must equal required_evaluations")
        observed=[]
        for phase,names in partitions.items():
            if not isinstance(names,list) or not names or any(not isinstance(name,str) or not name for name in names) or len(names)!=len(set(names)):raise CompanionError(f"strategy split {phase} must be a non-empty unique partition list")
            observed.extend(names)
        if len(observed)!=len(set(observed)):raise CompanionError("development/validation/holdout partitions must be physically disjoint")
        plan=spec["evaluation_plan"]
        if not isinstance(plan,list) or len(plan)!=3:raise CompanionError("strategy evaluation_plan must contain exactly one development, validation and final_holdout run")
        planned=[]
        for item in plan:
            if not isinstance(item,dict) or set(item)!={"phase","seed"}:raise CompanionError("each evaluation_plan item must contain only phase and seed")
            if item["phase"] not in required_phases or isinstance(item["seed"],bool) or not isinstance(item["seed"],int):raise CompanionError("invalid evaluation_plan phase or seed")
            planned.append((item["phase"],item["seed"]))
        if {phase for phase,_ in planned}!=required_phases or len(planned)!=len(set(planned)):raise CompanionError("evaluation_plan phases must be unique and complete")
        if int(spec["max_experiments"])!=len(plan) or int(spec["minimum_experiments"])!=len(plan):raise CompanionError("strategy experiment limits must equal the immutable evaluation_plan")
        candidate_config=spec["candidate_config"];benchmark_config=spec["benchmark_config"]
        for label,config in (("candidate",candidate_config),("benchmark",benchmark_config)):
            if not isinstance(config,dict) or set(config)!={"strategy","simulation"} or not isinstance(config["strategy"],dict) or not isinstance(config["simulation"],dict):raise CompanionError(f"strategy {label}_config must contain only strategy and simulation")
            if set(config["simulation"])!={"initial_cash","initial_positions","reality_spec"}:raise CompanionError(f"strategy {label}_config simulation must freeze only initial_cash, initial_positions and reality_spec")
            if config["simulation"]["initial_positions"]!={}:raise CompanionError("V4 transparent research requires empty initial_positions")
        try:
            candidate_cash=Decimal(str(candidate_config["simulation"]["initial_cash"]));benchmark_cash=Decimal(str(benchmark_config["simulation"]["initial_cash"]))
        except Exception as exc:raise CompanionError("candidate and benchmark initial_cash must be numeric") from exc
        if not candidate_cash.is_finite() or candidate_cash<=0 or candidate_cash!=benchmark_cash:raise CompanionError("candidate and benchmark require the same positive initial_cash")
        if candidate_config["strategy"].get("method")!=spec["method"]:raise CompanionError("candidate_config method differs from StrategySpec method")
        if benchmark_config["strategy"].get("method")!=spec["benchmark"].get("method"):raise CompanionError("benchmark_config method differs from StrategySpec benchmark")
        if not isinstance(spec["portfolio"],dict) or any(candidate_config["strategy"].get(key)!=value for key,value in spec["portfolio"].items()):raise CompanionError("candidate_config differs from preregistered portfolio")
        if spec["availability_lag"]!="next_session":raise CompanionError("V4 transparent strategies require availability_lag=next_session")
        leakage=spec["leakage_controls"]
        required_leakage={"feature_lag_sessions","purge_sessions","embargo_sessions","point_in_time_universe","labels_excluded_from_features"}
        if not isinstance(leakage,dict) or set(leakage)!=required_leakage:raise CompanionError(f"strategy leakage_controls must be exactly: {sorted(required_leakage)}")
        if leakage["feature_lag_sessions"]!=1 or leakage["point_in_time_universe"] is not True or leakage["labels_excluded_from_features"] is not True:raise CompanionError("transparent baseline requires one-session feature lag, PIT universe and label exclusion")
        for field in ("purge_sessions","embargo_sessions"):
            if isinstance(leakage[field],bool) or not isinstance(leakage[field],int) or leakage[field]<0:raise CompanionError(f"leakage_controls.{field} must be a non-negative integer")
        rebalance=spec["portfolio"].get("rebalance_every_sessions")
        if isinstance(rebalance,bool) or not isinstance(rebalance,int) or rebalance<=0:raise CompanionError("portfolio.rebalance_every_sessions must be a positive integer")
        required_metrics={"net_excess_return","max_drawdown","turnover","total_fees","unfilled_rate","observation_days"}
        if not isinstance(spec["metrics"],list) or any(not isinstance(metric,str) or not metric for metric in spec["metrics"]) or len(spec["metrics"])!=len(set(spec["metrics"])) or not required_metrics<=set(spec["metrics"]):raise CompanionError(f"strategy metrics must include the professional minimum: {sorted(required_metrics)}")
        self._validate_pass_fail_contract(spec["pass_fail"])
        reality=spec["costs"].get("reality_spec") if isinstance(spec["costs"],dict) else None
        if not isinstance(reality,dict) or canonical(candidate_config["simulation"].get("reality_spec"))!=canonical(reality) or canonical(benchmark_config["simulation"].get("reality_spec"))!=canonical(reality):raise CompanionError("candidate and benchmark must use the exact preregistered RealitySpec")
        try:normalized_reality=RealitySpec.from_value(reality).to_dict()
        except Exception as exc:raise CompanionError(f"invalid preregistered RealitySpec: {exc}") from exc
        if canonical(reality)!=canonical(normalized_reality):raise CompanionError("StrategyVersion must freeze the complete normalized RealitySpec without implicit defaults")
        if spec["method"] in {"llm_signal", "reinforcement_learning", "autonomous_agent"}:
            raise CompanionError("V4 transparent baseline phase forbids this strategy method")
        if parent_id:
            parent = self.strategy_get(parent_id)
            if parent["hypothesis_id"] != hypothesis_id:
                raise CompanionError("parent strategy belongs to another hypothesis")
        with self.db.connect() as con:
            version = con.execute(
                "SELECT COALESCE(MAX(version),0)+1 FROM strategy_versions WHERE hypothesis_id=?",
                (hypothesis_id,),
            ).fetchone()[0]
        content_hash = digest(
            "strategy-spec-v1", hypothesis_id, version, spec, code_ref, environment_ref, parent_id
        )
        sid, now = new_id("strategy"), iso()
        with self.db.transaction() as con:
            con.execute(
                "INSERT OR IGNORE INTO strategy_versions(id,hypothesis_id,version,status,spec_json,content_hash,parent_id,code_ref,environment_ref,created_at) VALUES(?,?,?,'preregistered',?,?,?,?,?,?)",
                (
                    sid,
                    hypothesis_id,
                    version,
                    canonical(spec),
                    content_hash,
                    parent_id,
                    code_ref,
                    environment_ref,
                    now,
                ),
            )
            row = con.execute(
                "SELECT * FROM strategy_versions WHERE content_hash=?", (content_hash,)
            ).fetchone()
        return row_dict(row)

    def strategy_get(self, strategy_id: str) -> dict[str, Any]:
        with self.db.connect() as con:
            item = row_dict(
                con.execute("SELECT * FROM strategy_versions WHERE id=?", (strategy_id,)).fetchone()
            )
        if not item:
            raise CompanionError(f"strategy version not found: {strategy_id}")
        return item

    def strategy_list(
        self, hypothesis_id: str | None = None, status: str | None = None
    ) -> list[dict[str, Any]]:
        query, params = "SELECT * FROM strategy_versions WHERE 1=1", []
        if hypothesis_id:
            query += " AND hypothesis_id=?"
            params.append(hypothesis_id)
        if status:
            query += " AND status=?"
            params.append(status)
        query += " ORDER BY hypothesis_id,version DESC"
        with self.db.connect() as con:
            return rows_dict(con.execute(query, params).fetchall())

    def experiment_start(
        self,
        *,
        strategy_version_id: str,
        dataset_snapshot_id: str,
        split: dict[str, Any],
        params: dict[str, Any] | None = None,
        seed: int = 0,
        access_holdout: bool = False,
    ) -> dict[str, Any]:
        self.c.gates.require(["G0","G1","G2","G3"])
        strategy = self.strategy_get(strategy_version_id)
        if strategy["status"] != "preregistered":
            raise CompanionError("new experiments require a preregistered strategy")
        with self.db.connect() as con:
            snapshot = row_dict(
                con.execute(
                    "SELECT * FROM dataset_snapshots WHERE id=?", (dataset_snapshot_id,)
                ).fetchone()
            )
        if not snapshot or snapshot["status"] != "ready":
            raise CompanionError("experiment requires a ready dataset snapshot")
        if split != strategy["spec"]["split"]:
            raise CompanionError("experiment split differs from preregistered StrategySpec")
        self._validate_leakage_gaps(strategy,dataset_snapshot_id)
        phase=(params or {}).get("evaluation_phase")
        if phase not in strategy["spec"]["required_evaluations"]:raise CompanionError("experiment must declare a preregistered evaluation_phase")
        if set(params or {})!={"evaluation_phase"}:raise CompanionError("experiment params may contain only evaluation_phase; parameter changes require a new StrategyVersion")
        plan={(entry["phase"],int(entry["seed"])) for entry in strategy["spec"]["evaluation_plan"]}
        slot=(phase,int(seed))
        if slot not in plan:raise CompanionError("experiment phase/seed is not in the immutable evaluation_plan")
        if access_holdout:raise CompanionError("holdout access is recorded only by the deterministic worker")
        key = digest(
            "experiment-v1", strategy_version_id, dataset_snapshot_id, split, params or {}, seed
        )
        existing_runs=self.experiment_list(strategy_version_id)
        with self.db.connect() as con:
            existing = row_dict(con.execute("SELECT * FROM experiment_runs WHERE idempotency_key=?", (key,)).fetchone())
        if existing:
            return existing
        if any(run["dataset_snapshot_id"]!=dataset_snapshot_id for run in existing_runs):raise CompanionError("one StrategyVersion evaluation_plan must use one DatasetSnapshot")
        if any((run["params"].get("evaluation_phase"),int(run["seed"]))==slot for run in existing_runs):raise CompanionError("evaluation_plan slot is already registered")
        phase_order={"development":0,"validation":1,"final_holdout":2}
        earlier={(planned_phase,planned_seed) for planned_phase,planned_seed in plan if phase_order[planned_phase]<phase_order[phase]}
        completed={(run["params"].get("evaluation_phase"),int(run["seed"])) for run in existing_runs if run["status"]=="succeeded"}
        if not earlier<=completed:raise CompanionError(f"{phase} cannot start before earlier evaluation_plan slots succeed")
        if phase=="final_holdout":
            validation_runs=[run for run in existing_runs if run["params"].get("evaluation_phase")=="validation" and run["status"]=="succeeded"]
            if len(validation_runs)!=1:raise CompanionError("final_holdout requires exactly one successful validation run")
            validation_failures=self._metric_failures(strategy["spec"]["pass_fail"],validation_runs[0]["metrics"])
            if validation_failures:raise CompanionError(f"final_holdout remains sealed because validation failed: {validation_failures}")
        hypothesis = self.hypothesis_get(strategy["hypothesis_id"])
        limit = min(
            int(hypothesis["experiment_budget"]), int(strategy["spec"]["max_experiments"])
        )
        if int(hypothesis["experiment_count"]) >= limit:
            raise CompanionError("preregistered experiment budget exhausted")
        eid, now = new_id("experiment"), iso()
        with self.db.transaction() as con:
            current = con.execute(
                "SELECT experiment_count FROM research_hypotheses WHERE id=?",
                (hypothesis["id"],),
            ).fetchone()[0]
            if current >= limit:
                raise CompanionError("preregistered experiment budget exhausted")
            con.execute(
                "INSERT INTO experiment_runs(id,strategy_version_id,dataset_snapshot_id,status,idempotency_key,split_json,params_json,seed,holdout_accessed_at,started_at,created_at) VALUES(?,?,?,'running',?,?,?,?,?,?,?)",
                (
                    eid,
                    strategy_version_id,
                    dataset_snapshot_id,
                    key,
                    canonical(split),
                    canonical(params or {}),
                    int(seed),
                    None,
                    now,
                    now,
                ),
            )
            con.execute(
                "UPDATE research_hypotheses SET experiment_count=experiment_count+1,updated_at=? WHERE id=?",
                (now, hypothesis["id"]),
            )
        return self.experiment_get(eid)

    def _validate_leakage_gaps(self,strategy:dict[str,Any],snapshot_id:str)->None:
        """Validate split boundaries from validator metadata without opening holdout rows."""

        snapshot=self.c.data.snapshot_manifest(snapshot_id);by_name={partition.name:partition for partition in snapshot.partitions}
        bounds={}
        for phase,names in strategy["spec"]["split"]["partitions"].items():
            ranges=[]
            for name in names:
                partition=by_name.get(name)
                if not partition:raise CompanionError(f"strategy split partition is absent from Snapshot: {name}")
                report_id=partition.quality.get("validator_manifest_id");report=self.c.data.manifest_get(report_id,verify=True) if report_id else None
                body=report["manifest"].get("manifest",{}) if report else {}
                expected_role={"development":"development","validation":"validation","final_holdout":"holdout"}[phase]
                key_range=body.get("key_range",{})
                if not report or report["kind"]!="partition_validation_report" or body.get("stream")!="daily" or body.get("role")!=expected_role or not body.get("eligible") or not key_range.get("min_date") or not key_range.get("max_date"):
                    raise CompanionError(f"strategy split partition lacks qualified date metadata: {name}")
                ranges.append((key_range["min_date"],key_range["max_date"]))
            bounds[phase]=(min(item[0] for item in ranges),max(item[1] for item in ranges))
        from .v4_data import DataObjectRef
        calendar_ref=snapshot.calendar.get("object_ref") if hasattr(snapshot.calendar,"get") else None
        if not calendar_ref:raise CompanionError("Strategy Snapshot lacks a frozen trading calendar")
        calendar=json.loads(self.c.data.store.read(DataObjectRef.from_dict(calendar_ref)).decode("utf-8"))
        open_dates=sorted(str(row["date"]) for row in calendar if row.get("is_open") is True)
        required=int(strategy["spec"]["leakage_controls"]["purge_sessions"])+int(strategy["spec"]["leakage_controls"]["embargo_sessions"])
        order=("development","validation","final_holdout")
        for left,right in zip(order,order[1:]):
            observed=sum(1 for day in open_dates if bounds[left][1]<day<bounds[right][0])
            if observed<required:raise CompanionError(f"strategy split {left}->{right} has {observed} open-session gap; leakage controls require {required}")

    def experiment_complete(
        self,
        experiment_id: str,
        *,
        success: bool,
        bundle_manifest_id: str | None = None,
        metrics: dict[str, Any] | None = None,
        error: str | None = None,
        job_run_id:str|None=None,
    ) -> dict[str, Any]:
        raise CompanionError(
            "ExperimentRun is terminalized only by the atomic JobRun commit; direct completion is forbidden"
        )

    def forward_signal_start(
        self,
        *,
        strategy_version_id:str,
        dataset_snapshot_id:str,
        partition_names:list[str],
    )->dict[str,Any]:
        """Create an untuned operational signal run for an already-shadowed strategy."""
        self.c.jobs.feature_require("v4_shadow")
        strategy=self.strategy_get(strategy_version_id)
        if strategy["status"]!="shadow":raise CompanionError("forward signal requires a shadow strategy")
        snapshot=self.c.data.snapshot_get(dataset_snapshot_id,verify=True)
        if snapshot["status"]!="ready":raise CompanionError("forward signal requires a ready DatasetSnapshot")
        if not isinstance(partition_names,list) or not partition_names or any(not isinstance(name,str) or not name for name in partition_names) or len(partition_names)!=len(set(partition_names)):raise CompanionError("forward signal requires unique production partition names")
        selected=self.c.data.snapshot_partition_payloads(dataset_snapshot_id,partition_names,required_role="production")
        if any(item["stream"]!="daily" for item in selected):raise CompanionError("forward signal v1 accepts only production daily partitions")
        split={"partitions":{"forward_shadow":sorted(partition_names)}}
        params={"evaluation_phase":"forward_shadow"}
        key=digest("forward-signal-v1",strategy_version_id,dataset_snapshot_id,split)
        with self.db.connect() as con:
            existing=row_dict(con.execute("SELECT * FROM experiment_runs WHERE idempotency_key=?",(key,)).fetchone())
        if existing:return existing
        eid,now=new_id("experiment"),iso()
        with self.db.transaction() as con:
            con.execute("INSERT INTO experiment_runs(id,strategy_version_id,dataset_snapshot_id,status,idempotency_key,split_json,params_json,seed,started_at,created_at) VALUES(?,?,?,'running',?,?,?,?,?,?)",(eid,strategy_version_id,dataset_snapshot_id,key,canonical(split),canonical(params),0,now,now))
        return self.experiment_get(eid)

    def prepare_job_completion(
        self, job_run_id: str, intent: dict[str, Any] | None
    ) -> dict[str, Any] | None:
        """Validate an experiment completion intent without changing registry state."""

        if intent is None:
            return None
        if not isinstance(intent, dict) or set(intent) != {"experiment_id", "bundle_manifest_id"}:
            raise CompanionError("invalid experiment completion intent")
        experiment_id = intent["experiment_id"]
        bundle_manifest_id = intent["bundle_manifest_id"]
        item = self.experiment_get(experiment_id)
        if item["status"] != "running":
            raise CompanionError(f"experiment is not running: {item['status']}")
        self._verify_execution_binding(item, job_run_id)
        manifest = self.c.data.manifest_get(bundle_manifest_id, verify=True)
        if manifest["status"] != "ready" or manifest["kind"] != "experiment_bundle":
            raise CompanionError("ExperimentBundle manifest is not ready")
        payload = manifest["manifest"]
        if payload.get("kind") != "experiment_bundle":
            raise CompanionError("manifest kind/contents mismatch")
        bundle = payload.get("manifest", {})
        if bundle.get("experiment_id") != experiment_id:
            raise CompanionError("ExperimentBundle belongs to another experiment")
        if (
            bundle.get("job_run_id") != item.get("job_run_id")
            or bundle.get("spec_object_id") != item.get("spec_object_id")
            or bundle.get("execution_lineage_hash") != item.get("execution_lineage_hash")
        ):
            raise CompanionError("ExperimentBundle execution lineage mismatch")
        snapshot=self.c.data.snapshot_manifest(item["dataset_snapshot_id"])
        partitions={partition.name:partition.object_ref.sha256 for partition in snapshot.partitions}
        names=bundle.get("data_partition_names",[]);hashes=bundle.get("data_partition_hashes",[])
        if len(names)!=len(hashes) or any(partitions.get(name)!=content_hash for name,content_hash in zip(names,hashes)):
            raise CompanionError("ExperimentBundle data partitions differ from DatasetSnapshot")
        metrics = evaluate_native_experiment_bundle(bundle)
        return {
            "experiment_id": experiment_id,
            "bundle_manifest_id": bundle_manifest_id,
            "metrics": metrics,
        }

    def finalize_job_success(self, con, job_run_id: str, prepared: dict[str, Any] | None, now: str) -> None:
        """Join ExperimentRun success to the JobStep/JobRun/Run transaction."""

        if prepared is None:
            return
        changed = con.execute(
            "UPDATE experiment_runs SET status='succeeded',bundle_manifest_id=?,metrics_json=?,finished_at=?,error=NULL WHERE id=? AND status='running' AND job_run_id=?",
            (
                prepared["bundle_manifest_id"],
                canonical(prepared["metrics"]),
                now,
                prepared["experiment_id"],
                job_run_id,
            ),
        ).rowcount
        if changed != 1:
            raise CompanionError("ExperimentRun atomic terminal transition lost")

    def fail_bound_job(self, con, job_run_id: str, error: str, now: str) -> None:
        """Fail an experiment whenever its sole bound deterministic job fails."""

        con.execute(
            "UPDATE experiment_runs SET status='failed',finished_at=?,error=? WHERE job_run_id=? AND status IN ('queued','running')",
            (now, error, job_run_id),
        )

    def execution_bind(self,experiment_id:str,job_run_id:str,spec_object_id:str)->dict[str,Any]:
        item=self.experiment_get(experiment_id)
        if item["status"]=="succeeded" and item.get("job_run_id")==job_run_id and item.get("spec_object_id")==spec_object_id:return item
        if item["status"]!="running":raise CompanionError("experiment execution binding requires running state")
        self._verify_running_job(job_run_id,experiment_id)
        lineage=digest("experiment-execution-v1",experiment_id,job_run_id,spec_object_id,item["dataset_snapshot_id"],item["strategy_version_id"])
        if item.get("job_run_id") and (item["job_run_id"]!=job_run_id or item.get("spec_object_id")!=spec_object_id):raise CompanionError("experiment is already bound to another execution")
        with self.db.transaction() as con:
            con.execute("UPDATE experiment_runs SET job_run_id=?,spec_object_id=?,execution_lineage_hash=? WHERE id=?",(job_run_id,spec_object_id,lineage,experiment_id))
        return self.experiment_get(experiment_id)

    def mark_holdout_access(self,experiment_id:str,job_run_id:str,spec_object_id:str,partition_names:list[str])->dict[str,Any]:
        item=self.experiment_get(experiment_id);self._verify_execution_binding(item,job_run_id)
        if item.get("spec_object_id")!=spec_object_id:raise CompanionError("holdout spec differs from bound experiment input")
        strategy=self.strategy_get(item["strategy_version_id"])
        expected=strategy["spec"]["split"]["partitions"].get("final_holdout",[])
        if sorted(partition_names)!=sorted(expected):raise CompanionError("holdout access differs from preregistered physical partitions")
        with self.db.transaction() as con:con.execute("UPDATE experiment_runs SET holdout_accessed_at=?,params_json=? WHERE id=?",(iso(),canonical({**item["params"],"holdout_spec_object_id":spec_object_id,"holdout_partition_names":sorted(partition_names),"evaluation_role":"final_holdout"}),experiment_id))
        return self.experiment_get(experiment_id)

    def _verify_running_job(self,job_run_id:str,experiment_id:str)->None:
        with self.db.connect() as con:
            row=con.execute("SELECT j.status,s.handler,s.status,j.inputs_json FROM job_runs j JOIN job_steps s ON s.job_run_id=j.id WHERE j.id=? AND s.status='running' ORDER BY s.ordinal LIMIT 1",(job_run_id,)).fetchone()
        if not row or row[0]!="running" or row[1]!="research.native_quant" or row[2]!="running":raise CompanionError("experiment must execute inside running research.native_quant JobStep")
        import json
        inputs=json.loads(row[3]);
        if inputs.get("parameters",{}).get("experiment_id")!=experiment_id:raise CompanionError("JobRun is bound to another experiment")

    def _verify_execution_binding(self,item:dict[str,Any],job_run_id:str|None)->None:
        if not job_run_id or item.get("job_run_id")!=job_run_id:raise CompanionError("experiment completion requires its bound deterministic JobRun")
        self._verify_running_job(job_run_id,item["id"])

    def experiment_get(self, experiment_id: str) -> dict[str, Any]:
        with self.db.connect() as con:
            item = row_dict(
                con.execute("SELECT * FROM experiment_runs WHERE id=?", (experiment_id,)).fetchone()
            )
        if not item:
            raise CompanionError(f"experiment run not found: {experiment_id}")
        return item

    def experiment_submit(self,experiment_id:str,spec:dict[str,Any])->dict[str,Any]:
        """Freeze, validate and atomically enqueue a deterministic experiment."""
        item=self.experiment_get(experiment_id)
        if item["status"]!="running":raise CompanionError("only a running ExperimentRun can be submitted")
        if item.get("job_run_id"):return self.c.jobs.run_get(item["job_run_id"])
        phase=item["params"].get("evaluation_phase")
        if spec.get("evaluation_phase")!=phase:raise CompanionError("experiment spec phase differs from registry")
        strategy=self.strategy_get(item["strategy_version_id"])
        expected_top={"schema","evaluation_phase","partition_names","candidate","benchmark"}
        if set(spec)!=expected_top:raise CompanionError(f"experiment spec fields must be exactly: {sorted(expected_top)}")
        for label in ("candidate","benchmark"):
            submitted=spec.get(label)
            if canonical(submitted)!=canonical(strategy["spec"][f"{label}_config"]):raise CompanionError(f"experiment {label} config differs from immutable StrategyVersion")
        snapshot=self.c.data.snapshot_manifest(item["dataset_snapshot_id"])
        obj=self.c.data.object_put_json(spec,namespace="canonical",kind="native_experiment_spec",metadata={"experiment_id":experiment_id,"evaluation_phase":phase})
        validation=self.c.data.partition_validate(object_id=obj["id"],partition_name=f"experiment-specs/{experiment_id}",stream="native_experiment_spec",role={"development":"development","validation":"validation","final_holdout":"holdout","forward_shadow":"production"}[phase],knowledge_cutoff=snapshot.knowledge_cutoff)
        body=validation["manifest"].get("manifest",{})
        if not body.get("eligible"):raise CompanionError(f"experiment spec semantic validation failed: {body.get('violations',[])}")
        definition=self.c.jobs.definition_for_handler("research.native_quant")
        if not definition:raise CompanionError("active research.native_quant JobDefinition is missing")
        inputs={
            "refs":[
                {"type":"dataset_snapshot","id":item["dataset_snapshot_id"]},
                {"type":"data_object","id":obj["id"]},
                {"type":"artifact_manifest","id":validation["id"]},
            ],
            "parameters":{"experiment_id":experiment_id,"experiment_spec_object_id":obj["id"],"experiment_spec_validation_manifest_id":validation["id"]},
            "knowledge_cutoff":snapshot.knowledge_cutoff,
        }
        return self.c.jobs.enqueue_new_parent(definition_id=definition["id"],inputs=inputs,idempotency_key=f"experiment-submit:{experiment_id}",kind="maintenance")

    def experiment_list(
        self, strategy_version_id: str | None = None, status: str | None = None
    ) -> list[dict[str, Any]]:
        query, params = "SELECT * FROM experiment_runs WHERE 1=1", []
        if strategy_version_id:
            query += " AND strategy_version_id=?"
            params.append(strategy_version_id)
        if status:
            query += " AND status=?"
            params.append(status)
        query += " ORDER BY created_at DESC"
        with self.db.connect() as con:
            return rows_dict(con.execute(query, params).fetchall())

    def promotion_decide(
        self,
        experiment_id: str,
        decision: str,
        reason: str,
        evidence: dict[str, Any] | None = None,
        actor: str = "primary-codex",
    ) -> dict[str, Any]:
        allowed = {"reject", "revise", "research_passed", "shadow", "retire"}
        if decision not in allowed:
            raise CompanionError("invalid promotion decision")
        experiment = self.experiment_get(experiment_id)
        strategy=self.strategy_get(experiment["strategy_version_id"])
        transition_allowed={
            "reject":{"preregistered"},"revise":{"preregistered"},
            "research_passed":{"preregistered"},"shadow":{"research_passed"},
            "retire":{"preregistered","research_passed","shadow"},
        }
        if strategy["status"] not in transition_allowed[decision]:raise CompanionError(f"invalid strategy promotion transition: {strategy['status']} -> {decision}")
        if decision == "shadow":
            self.c.jobs.feature_require("v4_shadow")
        if decision in {"research_passed", "shadow"}:
            if experiment["status"] != "succeeded":
                raise CompanionError("only a successful experiment can advance")
            required_metrics={"net_excess_return","max_drawdown","turnover","total_fees","unfilled_rate","observation_days"}
            missing_metrics=required_metrics-set(experiment["metrics"])
            if missing_metrics:raise CompanionError(f"research promotion lacks deterministic evaluation metrics: {sorted(missing_metrics)}")
            if experiment["params"].get("evaluation_phase")!="final_holdout" or not experiment.get("holdout_accessed_at"):raise CompanionError("research promotion must use the registered final_holdout evaluation")
            runs=self.experiment_list(strategy["id"])
            expected_plan={(entry["phase"],int(entry["seed"])) for entry in strategy["spec"]["evaluation_plan"]}
            observed_plan=[(run["params"].get("evaluation_phase"),int(run["seed"])) for run in runs]
            if len(observed_plan)!=len(set(observed_plan)) or set(observed_plan)!=expected_plan:raise CompanionError("research promotion requires the exact immutable evaluation_plan")
            if any(run["dataset_snapshot_id"]!=experiment["dataset_snapshot_id"] for run in runs):raise CompanionError("research promotion evaluations use different DatasetSnapshots")
            if any(run["status"]!="succeeded" for run in runs):raise CompanionError("every preregistered evaluation must succeed before promotion")
            threshold_failures=[]
            for run in runs:
                phase=run["params"].get("evaluation_phase")
                if phase not in {"validation","final_holdout"}:
                    continue
                failures=self._metric_failures(strategy["spec"]["pass_fail"],run["metrics"])
                threshold_failures.extend(f"{phase}: {failure}" for failure in failures)
            if threshold_failures:
                raise CompanionError(f"deterministic promotion gate failed: {threshold_failures}")
        pid, now = new_id("promotion"), iso()
        with self.db.transaction() as con:
            con.execute(
                "INSERT INTO promotion_decisions(id,experiment_run_id,decision,reason,evidence_json,actor,created_at) VALUES(?,?,?,?,?,?,?)",
                (pid, experiment_id, decision, reason, canonical(evidence or {}), actor, now),
            )
            target_status = {
                "reject": "rejected",
                "revise": "rejected",
                "research_passed": "research_passed",
                "shadow": "shadow",
                "retire": "retired",
            }[decision]
            con.execute(
                "UPDATE strategy_versions SET status=? WHERE id=?",
                (target_status, experiment["strategy_version_id"]),
            )
        return self.promotion_get(pid)

    def promotion_get(self, promotion_id: str) -> dict[str, Any]:
        with self.db.connect() as con:
            item = row_dict(
                con.execute("SELECT * FROM promotion_decisions WHERE id=?", (promotion_id,)).fetchone()
            )
        if not item:
            raise CompanionError(f"promotion decision not found: {promotion_id}")
        return item

    def metric_failures(self, rules: Any, metrics: dict[str, Any]) -> list[str]:
        """Public deterministic evaluator for preregistered Strategy thresholds."""
        return self._metric_failures(rules, metrics)

    @staticmethod
    def _metric_failures(rules: Any, metrics: dict[str, Any]) -> list[str]:
        if isinstance(rules, dict):
            rules = rules.get("rules", [])
        if not isinstance(rules, list) or not rules:
            return ["no machine-checkable pass/fail rules preregistered"]
        failures = []
        for rule in rules:
            metric, operator, threshold = rule.get("metric"), rule.get("operator"), rule.get("value")
            if metric not in metrics:
                failures.append(f"missing metric {metric}")
                continue
            value = metrics[metric]
            if isinstance(value,(str,int,float)) and isinstance(threshold,(str,int,float)):
                try:value,threshold=Decimal(str(value)),Decimal(str(threshold))
                except Exception:pass
            passed = {
                "gte": value >= threshold,
                "gt": value > threshold,
                "lte": value <= threshold,
                "lt": value < threshold,
                "eq": value == threshold,
            }.get(operator)
            if passed is None:
                failures.append(f"unsupported operator {operator}")
            elif not passed:
                failures.append(f"{metric}={value} failed {operator} {threshold}")
        return failures

    def _validate_pass_fail_contract(self,value:Any)->None:
        if not isinstance(value,dict) or set(value)!={"rules"} or not isinstance(value["rules"],list):raise CompanionError("strategy pass_fail must contain only machine-checkable rules")
        rules=value["rules"];by_metric={}
        for rule in rules:
            if not isinstance(rule,dict) or set(rule)!={"metric","operator","value"}:raise CompanionError("each pass/fail rule must contain only metric, operator and value")
            metric=rule["metric"]
            if metric in by_metric:raise CompanionError(f"duplicate pass/fail rule for {metric}")
            try:threshold=Decimal(str(rule["value"]))
            except Exception as exc:raise CompanionError(f"pass/fail threshold is not numeric: {metric}") from exc
            if not threshold.is_finite():raise CompanionError(f"pass/fail threshold is non-finite: {metric}")
            by_metric[metric]=(rule["operator"],threshold)
        required={"net_excess_return","max_drawdown","turnover","unfilled_rate","observation_days"}
        if not required<=set(by_metric):raise CompanionError(f"pass_fail lacks required risk/baseline rules: {sorted(required-set(by_metric))}")
        operator,threshold=by_metric["net_excess_return"]
        if operator not in {"gt","gte"} or threshold<0 or operator=="gte" and threshold==0:raise CompanionError("net_excess_return must require a positive increment over benchmark")
        operator,threshold=by_metric["max_drawdown"]
        if operator not in {"gt","gte"} or not Decimal("-1")<threshold<=0:raise CompanionError("max_drawdown must have a finite loss floor between -1 and 0")
        operator,threshold=by_metric["turnover"]
        if operator not in {"lt","lte"} or threshold<=0:raise CompanionError("turnover must have a positive upper bound")
        operator,threshold=by_metric["unfilled_rate"]
        if operator not in {"lt","lte"} or not Decimal("0")<=threshold<Decimal("1"):raise CompanionError("unfilled_rate must have an upper bound below 1")
        operator,threshold=by_metric["observation_days"]
        minimum=Decimal("1") if self.c.gate_scope=="test_fixture" else Decimal("252")
        if operator not in {"gt","gte"} or threshold<minimum:raise CompanionError(f"observation_days must require at least {minimum} days in this scope")


def evaluate_native_experiment_bundle(bundle:dict[str,Any])->dict[str,Any]:
    if bundle.get("schema")!="investment-companion.experiment-bundle/v3":raise CompanionError("unsupported ExperimentBundle schema")
    if bundle.get("model_tokens")!=0:raise CompanionError("deterministic experiment bundle must use zero model tokens")
    names=bundle.get("data_partition_names");hashes=bundle.get("data_partition_hashes")
    if not isinstance(names,list) or not names or not isinstance(hashes,list) or len(names)!=len(hashes):raise CompanionError("ExperimentBundle lacks physical data partition lineage")
    if bundle.get("data_lineage_hash")!=digest(bundle.get("dataset_snapshot_id"),names,hashes):raise CompanionError("ExperimentBundle data lineage hash mismatch")
    candidate=bundle.get("candidate_bundle");benchmark=bundle.get("benchmark_bundle")
    if not isinstance(candidate,dict) or not isinstance(benchmark,dict):raise CompanionError("ExperimentBundle requires candidate and benchmark")
    candidate_result=_verified_quant_result(candidate,"candidate")
    benchmark_result=_verified_quant_result(benchmark,"benchmark")
    if candidate_result.get("dataset_snapshot_id")!=benchmark_result.get("dataset_snapshot_id"):raise CompanionError("candidate and benchmark use different snapshots")
    if candidate_result.get("dataset_snapshot_id")!=bundle.get("dataset_snapshot_id"):raise CompanionError("quant results differ from ExperimentBundle snapshot")
    if candidate_result.get("strategy_version_id")!=bundle.get("strategy_version_id") or benchmark_result.get("strategy_version_id")!=bundle.get("strategy_version_id"):raise CompanionError("quant results differ from ExperimentBundle strategy")
    pairs=bundle.get("evaluation_pairs")
    if not isinstance(pairs,list) or pairs!=candidate_result.get("evaluation_pairs") or pairs!=benchmark_result.get("evaluation_pairs"):
        raise CompanionError("ExperimentBundle evaluation pairs differ from deterministic quant results")
    action_ids=bundle.get("corporate_action_ids")
    if not isinstance(action_ids,list) or any(not isinstance(item,str) or not item for item in action_ids) or len(action_ids)!=len(set(action_ids)):raise CompanionError("ExperimentBundle corporate action lineage is invalid")
    action_scope=bundle.get("corporate_action_scope")
    if not isinstance(action_scope,dict) or set(action_scope)!={"start","end","denominator_hash"} or action_scope["start"]>action_scope["end"] or action_scope["denominator_hash"]!=bundle.get("denominator_hash"):
        raise CompanionError("ExperimentBundle corporate action scope is invalid")
    for label,result in (("candidate",candidate_result),("benchmark",benchmark_result)):
        targets=result.get("target_portfolios")
        if not isinstance(targets,list) or not targets or result.get("target_portfolio")!=targets[-1]:raise CompanionError(f"{label} lacks a complete target_weights series")
        if len(result.get("evaluation_pairs",[]))!=len(targets):raise CompanionError(f"{label} evaluation pair/target count mismatch")
        for target in targets:
            if not verify_artifact_hash(target):raise CompanionError(f"{label} target_weights hash is invalid")
            expected={
                "denominator_hash":digest("dataset-denominator-v1",target.get("dataset_denominator")),
                "universe_hash":digest("dataset-universe-v1",target.get("dataset_universe")),
                "exclusions_hash":digest("dataset-exclusions-v1",target.get("dataset_exclusions")),
            }
            if any(bundle.get(key)!=value for key,value in expected.items()):raise CompanionError(f"{label} target dataset contract differs from Snapshot lineage")
        observed_actions=[item.get("action_id") for item in result.get("simulation",{}).get("corporate_events",[])]
        if sorted(observed_actions)!=sorted(action_ids):raise CompanionError(f"{label} corporate action lineage differs from Snapshot")
    candidate_metrics=_simulation_metrics(candidate_result.get("simulation"),"candidate")
    benchmark_metrics=_simulation_metrics(benchmark_result.get("simulation"),"benchmark")
    return {
        "evaluation_version":"native-evaluator/2",
        "candidate_total_return":candidate_metrics["total_return"],
        "benchmark_total_return":benchmark_metrics["total_return"],
        "net_excess_return":_decimal_text(Decimal(candidate_metrics["total_return"])-Decimal(benchmark_metrics["total_return"])),
        "max_drawdown":candidate_metrics["max_drawdown"],
        "turnover":candidate_metrics["turnover"],
        "total_fees":candidate_metrics["total_fees"],
        "unfilled_rate":candidate_metrics["unfilled_rate"],
        "observation_days":candidate_metrics["observation_days"],
        "candidate_result_hash":candidate_result["artifact_hash"],
        "benchmark_result_hash":benchmark_result["artifact_hash"],
    }


def _verified_quant_result(bundle:dict[str,Any],label:str)->dict[str,Any]:
    if not verify_artifact_hash(bundle):raise CompanionError(f"{label} quant bundle hash is invalid")
    result=bundle.get("experiment_result")
    if not isinstance(result,dict) or not verify_artifact_hash(result):raise CompanionError(f"{label} experiment result hash is invalid")
    if bundle.get("experiment_result_hash")!=result["artifact_hash"]:raise CompanionError(f"{label} result lineage hash mismatch")
    return result


def _simulation_metrics(simulation:Any,label:str)->dict[str,Any]:
    if not isinstance(simulation,dict) or not verify_artifact_hash(simulation):raise CompanionError(f"{label} requires a hashed portfolio simulation")
    daily=simulation.get("daily",[])
    if not daily:raise CompanionError(f"{label} simulation has no daily observations")
    initial=Decimal(str(simulation["initial_cash"]));final=Decimal(str(simulation["final_nav"]))
    if initial<=0:raise CompanionError("simulation initial cash must be positive")
    total_return=final/initial-Decimal("1")
    peak=Decimal(str(daily[0]["nav"]));max_drawdown=Decimal("0");notional=Decimal("0");nav_sum=Decimal("0");fees=Decimal("0")
    for row in daily:
        nav=Decimal(str(row["nav"]));peak=max(peak,nav)
        if peak: max_drawdown=min(max_drawdown,nav/peak-Decimal("1"))
        notional+=Decimal(str(row.get("traded_notional","0")));fees+=Decimal(str(row.get("fees","0")));nav_sum+=nav
    avg_nav=nav_sum/Decimal(len(daily));turnover=notional/avg_nav if avg_nav else Decimal("0")
    total_attempts=len(simulation.get("fills",[]))+len(simulation.get("unfilled",[]));unfilled_rate=Decimal(len(simulation.get("unfilled",[])))/Decimal(total_attempts) if total_attempts else Decimal("0")
    return {"total_return":_decimal_text(total_return),"max_drawdown":_decimal_text(max_drawdown),"turnover":_decimal_text(turnover),"total_fees":_decimal_text(fees),"unfilled_rate":_decimal_text(unfilled_rate),"observation_days":len(daily)}


def _decimal_text(value:Decimal)->str:
    if value==0:return "0"
    return format(value.normalize(),"f")
