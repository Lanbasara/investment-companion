from __future__ import annotations

import subprocess
from datetime import timedelta
from typing import Any

from .foundation import CompanionError, canonical, new_id
from .db import SCHEMA_VERSION, row_dict, rows_dict
from .timeutil import iso, parse, utc_now


GATES = ("G0", "G1", "G2", "G3", "G4", "G5", "G6")
G1_REQUIRED_TUSHARE_CAPABILITIES=frozenset({"stock_basic","trade_cal","daily","adj_factor","daily_basic","bak_basic","stock_st","suspend_d","stk_limit","index_weight","dividend"})
GATE_CHECKLISTS={
    "G0":{"database_integrity","schema_current","ordered_migrations","v3_regression","backup_restore","run_lifecycle"},
    "G1":{"capability_matrix","golden_corpus","pit_cutoff","source_authority","license_review","live_canary"},
    "G2":{"runtime_isolation","golden_alignment","reference_alignment","deterministic_hash","runtime_decision"},
    "G3":{"allowlist_jobs","crash_recovery","atomic_artifacts","snapshot_integrity","zero_tokens","replay"},
    "G4":{"preregistration","walk_forward_oos","holdout","benchmark","costs","risk","all_trials"},
    "G5":{"reality_model","shadow_isolation","sample_gate","decision_gate","manual_execution","attention_idempotency"},
    "G6":{"baseline_increment","drawdown","user_value","notification_burden","sample_sufficient"},
}
REPORT_VALIDATORS={
    "test_report":("pytest-acceptance-validator/1",{"test_command_completed","zero_failures","v3_v4_suite_passed"}),
    "backup_restore_report":("sqlite-backup-restore-validator/1",{"backup_created","restore_integrity_ok","source_hash_unchanged"}),
    "run_lifecycle_report":("run-lifecycle-validator/1",{"claim_lease_attempt","terminal_propagation","recovery_idempotent"}),
    "data_qualification_report":("data-qualification-validator/1",{"capability_matrix_complete","pit_scope_declared","failure_semantics_verified"}),
    "golden_corpus_report":("golden-corpus-validator/1",{"official_expectations_frozen","edge_cases_covered","raw_to_snapshot_aligned"}),
    "license_review":("license-review-validator/1",{"retention_reviewed","backup_reviewed","redistribution_reviewed"}),
    "quant_runtime_decision":("quant-runtime-decision-validator/1",{"runtime_boundary_verified","go_no_go_recorded","maintenance_cost_reviewed"}),
    "quant_golden_report":("quant-golden-validator/1",{"discrete_outputs_equal","numeric_tolerances_passed","a_share_reality_aligned"}),
    "job_fault_injection_report":("job-fault-injection-validator/1",{"lease_recovery","atomic_publish","parent_terminal_state"}),
    "replay_report":("deterministic-replay-validator/1",{"fresh_root_replay","artifact_hash_equal","zero_model_tokens"}),
    "research_method_report":("research-method-validator/1",{"preregistered","walk_forward_oos","cost_risk_baseline_passed"}),
    "experiment_registry_report":("experiment-registry-validator/1",{"all_trials_retained","holdout_access_recorded","budget_enforced"}),
    "shadow_validation_report":("shadow-forward-validator/1",{"no_backfill","signal_execution_separated","real_ledger_isolated"}),
    "manual_execution_report":("manual-execution-validator/1",{"joint_plan_frozen","manual_confirmation_only","confirmed_ledger_linked"}),
    "attention_validation_report":("attention-validator/1",{"notification_idempotent","opt_in_verified","state_labels_clear"}),
    "user_value_report":("user-value-validator/1",{"time_saved_measured","notification_burden_measured","decision_quality_reviewed"}),
    "strategy_eligibility_report":("strategy-eligibility-validator/1",{"sample_gate_met","baseline_increment_passed","risk_capacity_passed"}),
}


class GateRegistry:
    """Evidence-linked release gates; feature flags cannot bypass them."""

    def __init__(self, companion):
        self.c = companion
        self.db = companion.db

    def validation_report_publish(
        self,
        *,
        kind:str,
        checks:dict[str,bool],
        input_refs:list[str],
        commands:list[str],
        observations:dict[str,Any],
        scope:str|None=None,
        actor:str="primary-codex",
    )->dict[str,Any]:
        """Package reproducible operational evidence; it never grants a Gate by itself."""

        if kind not in REPORT_VALIDATORS:raise CompanionError(f"unsupported validation report kind: {kind}")
        validator,required_checks=REPORT_VALIDATORS[kind]
        if set(checks)!=required_checks or any(not isinstance(value,bool) for value in checks.values()):raise CompanionError(f"{kind} checks must be exactly: {sorted(required_checks)}")
        if not isinstance(input_refs,list) or not input_refs or any(not isinstance(item,str) or not item for item in input_refs) or len(input_refs)!=len(set(input_refs)):raise CompanionError("validation report requires unique immutable input refs")
        if not commands or any(not isinstance(command,str) or not command.strip() for command in commands):raise CompanionError("validation report requires concrete verification commands")
        if not isinstance(observations,dict) or not observations:raise CompanionError("validation report requires non-empty observations")
        effective_scope=scope or self.c.gate_scope
        if effective_scope not in {"production","test_fixture"}:raise CompanionError("invalid validation report scope")
        code_version=self._git_head() if effective_scope=="production" else "test-fixture"
        if effective_scope=="production" and self._git_dirty():raise CompanionError("production validation report requires a clean runtime worktree")
        hashes=[]
        for reference in input_refs:
            if reference.startswith("manifest_"):item=self.c.data.manifest_get(reference,verify=True)
            elif reference.startswith("dataobj_"):item=self.c.data.object_get(reference,verify=True)
            else:raise CompanionError("validation report inputs must be Manifest or DataObject IDs")
            hashes.append(item["content_hash"])
        payload={"validator":validator,"validator_status":"passed" if all(checks.values()) else "failed","scope":effective_scope,"code_version":code_version,"schema_version":SCHEMA_VERSION,"checks":checks,"input_refs":input_refs,"input_artifact_hashes":hashes,"commands":commands,"observations":observations,"actor":actor,"generated_at":iso()}
        return self.c.data.manifest_publish(kind=kind,schema_version=f"investment-companion.{kind.replace('_','-')}/v1",manifest=payload,_internal=True)

    def evidence_publish(
        self,
        gate: str,
        *,
        checks: dict[str, Any],
        artifacts: list[str],
        unknowns: list[str],
        counterevidence: list[str],
        counterevidence_disposition: dict[str,str] | None,
        code_version: str,
        scope: str = "production",
    ) -> dict[str, Any]:
        self._validate_gate(gate)
        if scope not in {"production", "test_fixture"}:
            raise CompanionError("invalid gate evidence scope")
        if set(checks)!=GATE_CHECKLISTS[gate]:
            raise CompanionError(f"{gate} evidence checklist must be exactly: {sorted(GATE_CHECKLISTS[gate])}")
        if not artifacts or not isinstance(artifacts, list) or not isinstance(unknowns, list):
            raise CompanionError("gate evidence requires checks, artifacts and unknowns")
        for artifact_id in artifacts:self.c.data.manifest_get(artifact_id)
        dispositions=counterevidence_disposition or {}
        if set(dispositions)!=set(counterevidence):raise CompanionError("every counterevidence item requires an explicit disposition")
        if scope=="production":
            current=self._git_head()
            if code_version!=current:raise CompanionError(f"gate evidence code_version must equal current Git HEAD {current}")
            if self._git_dirty():raise CompanionError("production gate evidence cannot be issued from a dirty worktree")
            self._validate_production_evidence(gate,artifacts)
        payload = {
            "gate": gate,
            "scope": scope,
            "checks": checks,
            "artifacts": artifacts,
            "unknowns": unknowns,
            "counterevidence": counterevidence,
            "counterevidence_disposition":dispositions,
            "code_version": code_version,
            "schema_version": SCHEMA_VERSION,
            "generated_at": iso(),
        }
        if gate == "G0":
            status = self.c.system_status()
            cutoff=iso()
            with self.db.connect() as con:
                expired_leases=sum(int(con.execute(query,(cutoff,)).fetchone()[0]) for query in (
                    "SELECT COUNT(*) FROM runs WHERE status='leased' AND lease_until IS NOT NULL AND lease_until<=?",
                    "SELECT COUNT(*) FROM job_runs WHERE status IN ('leased','running') AND lease_until IS NOT NULL AND lease_until<=?",
                    "SELECT COUNT(*) FROM job_steps WHERE status IN ('leased','running') AND lease_until IS NOT NULL AND lease_until<=?",
                ))
            payload["machine_checks"] = {
                "database_integrity": status["integrity"] == "ok",
                "schema_current": status["meta"].get("schema_version") == str(SCHEMA_VERSION),
                "ordered_migrations": len(status.get("migrations", [])) >= 2,
                "no_expired_leases": expired_leases == 0,
            }
        return self.c.data.manifest_publish(
            kind="gate_evidence",
            schema_version="investment-companion.gate-evidence/v1",
            manifest=payload,
            _internal=True,
        )

    def assessment_record(
        self,
        *,
        gate: str,
        status: str,
        evidence_manifest_id: str,
        code_version: str,
        assessed_by: str,
        approval_ref:str|None=None,
        conditions: list[str] | None = None,
        scope: str = "production",
    ) -> dict[str, Any]:
        self._validate_gate(gate)
        if status not in {"go", "conditional_go", "no_go", "pending"}:
            raise CompanionError("invalid gate assessment status")
        if scope not in {"production", "test_fixture"}:
            raise CompanionError("invalid gate assessment scope")
        manifest = self.c.data.manifest_get(evidence_manifest_id)
        if manifest["kind"] != "gate_evidence":
            raise CompanionError("gate assessment requires a gate_evidence manifest")
        evidence = manifest["manifest"].get("manifest", {})
        if evidence.get("gate") != gate or evidence.get("scope") != scope:
            raise CompanionError("gate evidence lineage mismatch")
        if evidence.get("schema_version") != SCHEMA_VERSION:
            raise CompanionError("gate evidence uses another schema version")
        if evidence.get("code_version") != code_version:
            raise CompanionError("gate evidence uses another code version")
        if scope=="production":
            current=self._git_head()
            if code_version!=current:raise CompanionError("production assessment code_version is stale")
            if self._git_dirty():raise CompanionError("production assessment cannot be recorded from a dirty runtime worktree")
        if status == "go":
            if evidence.get("unknowns"):
                raise CompanionError("Gate cannot be Go while evidence has unknowns")
            checks = evidence.get("checks", {})
            if not checks or not all(value is True for value in checks.values()):
                raise CompanionError("Gate Go requires every declared check to pass")
            machine = evidence.get("machine_checks", {})
            if machine and not all(value is True for value in machine.values()):
                raise CompanionError("Gate Go failed machine-derived checks")
            if scope == "production":
                if assessed_by!="primary-codex" or not approval_ref:raise CompanionError("production Gate Go requires Primary assessment plus an explicit user approval_ref")
            unresolved=[item for item,resolution in evidence.get("counterevidence_disposition",{}).items() if not resolution.strip() or resolution.strip().lower() in {"ignored","unresolved"}]
            if unresolved:raise CompanionError(f"Gate Go has unresolved counterevidence: {unresolved}")
            gate_index=GATES.index(gate)
            for prerequisite in GATES[:gate_index]:
                prior=self.latest(prerequisite,scope)
                if not prior or prior["status"]!="go":raise CompanionError(f"{gate} cannot be Go before {prerequisite}=go")
        if status == "conditional_go" and not conditions:
            raise CompanionError("Conditional Go requires explicit conditions")
        previous = self.latest(gate, scope)
        aid, now = new_id("gate"), iso()
        with self.db.transaction() as con:
            con.execute(
                "INSERT INTO gate_assessments(id,gate,status,scope,evidence_manifest_id,conditions_json,code_version,schema_version,assessed_by,approval_ref,supersedes,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    aid,
                    gate,
                    status,
                    scope,
                    evidence_manifest_id,
                    canonical(conditions or []),
                    code_version,
                    SCHEMA_VERSION,
                    assessed_by,
                    approval_ref,
                    previous["id"] if previous else None,
                    now,
                ),
            )
        return self.get(aid)

    def get(self, assessment_id: str) -> dict[str, Any]:
        with self.db.connect() as con:
            item = row_dict(
                con.execute("SELECT * FROM gate_assessments WHERE id=?", (assessment_id,)).fetchone()
            )
        if not item:
            raise CompanionError(f"gate assessment not found: {assessment_id}")
        return item

    def latest(self, gate: str, scope: str = "production") -> dict[str, Any] | None:
        self._validate_gate(gate)
        with self.db.connect() as con:
            return row_dict(
                con.execute(
                    "SELECT * FROM gate_assessments WHERE gate=? AND scope=? ORDER BY created_at DESC,rowid DESC LIMIT 1",
                    (gate, scope),
                ).fetchone()
            )

    def list(self, scope: str = "production") -> list[dict[str, Any]]:
        with self.db.connect() as con:
            return rows_dict(
                con.execute(
                    "SELECT * FROM gate_assessments WHERE scope=? ORDER BY gate,created_at DESC",
                    (scope,),
                ).fetchall()
            )

    def require(
        self,
        gates: list[str],
        *,
        scope: str | None = None,
        allow_conditional: bool = False,
    ) -> None:
        effective_scope = scope or self.c.gate_scope
        if effective_scope not in {"production","test_fixture"}:raise CompanionError("invalid gate requirement scope")
        missing = []
        current=self.c.data._code_version() if effective_scope=="production" else "test-fixture"
        for gate in gates:
            item = self.latest(gate, effective_scope)
            accepted = {"go", "conditional_go"} if allow_conditional else {"go"}
            fresh=bool(item and item["code_version"]==current and int(item["schema_version"])==SCHEMA_VERSION)
            if not item or item["status"] not in accepted or not fresh:
                missing.append(f"{gate}={item['status'] if item else 'missing'}")
        if missing:
            raise CompanionError("release gates not satisfied: " + ", ".join(missing))

    @staticmethod
    def _validate_gate(gate: str) -> None:
        if gate not in GATES:
            raise CompanionError("invalid release gate")

    def _git_head(self)->str:
        proc=subprocess.run(["git","rev-parse","HEAD"],cwd=self.c.root,capture_output=True,text=True,timeout=10,check=False)
        value=proc.stdout.strip()
        if proc.returncode!=0 or len(value)!=40:raise CompanionError("cannot resolve current Git commit for production gate evidence")
        return value

    def _git_dirty(self)->bool:
        proc=subprocess.run(["git","status","--porcelain","--","README.md","AGENTS.md",".codex","companion","tests","bin","systemd","schemas","docs"],cwd=self.c.root,capture_output=True,text=True,timeout=10,check=False)
        if proc.returncode!=0:raise CompanionError("cannot inspect Git worktree for gate evidence")
        return bool(proc.stdout.strip())

    def _validate_production_evidence(self,gate:str,artifact_ids:list[str])->None:
        required_kinds={
            "G0":{"test_report","backup_restore_report","run_lifecycle_report"},
            "G1":{"data_qualification_report","golden_corpus_report","license_review"},
            "G2":{"quant_runtime_decision","quant_golden_report"},
            "G3":{"job_fault_injection_report","replay_report"},
            "G4":{"research_method_report","experiment_registry_report"},
            "G5":{"shadow_validation_report","manual_execution_report","attention_validation_report"},
            "G6":{"user_value_report","strategy_eligibility_report"},
        }[gate]
        manifests=[self.c.data.manifest_get(item) for item in artifact_ids]
        kinds={item["kind"] for item in manifests}
        missing=required_kinds-kinds
        if missing:raise CompanionError(f"{gate} production evidence missing artifact kinds: {sorted(missing)}")
        for item in manifests:
            report=item["manifest"].get("manifest",{})
            validators={
                "test_report":"pytest-acceptance-validator/1","backup_restore_report":"sqlite-backup-restore-validator/1","run_lifecycle_report":"run-lifecycle-validator/1",
                "data_qualification_report":"data-qualification-validator/1","golden_corpus_report":"golden-corpus-validator/1","license_review":"license-review-validator/1",
                "quant_runtime_decision":"quant-runtime-decision-validator/1","quant_golden_report":"quant-golden-validator/1",
                "job_fault_injection_report":"job-fault-injection-validator/1","replay_report":"deterministic-replay-validator/1",
                "research_method_report":"research-method-validator/1","experiment_registry_report":"experiment-registry-validator/1",
                "shadow_validation_report":"shadow-forward-validator/1","manual_execution_report":"manual-execution-validator/1","attention_validation_report":"attention-validator/1",
                "user_value_report":"user-value-validator/1","strategy_eligibility_report":"strategy-eligibility-validator/1",
            }
            if item["kind"] in required_kinds and (report.get("validator")!=validators[item["kind"]] or report.get("validator_status")!="passed" or report.get("scope")!="production" or not report.get("input_artifact_hashes") or report.get("code_version")!=self._git_head()):raise CompanionError(f"unvalidated, unprovenanced or stale gate artifact: {item['kind']}")
        if gate=="G1":
            latest={}
            for item in self.c.data.capability_list("tushare"):
                latest.setdefault(item["capability"],item)
            bad=sorted(stream for stream in G1_REQUIRED_TUSHARE_CAPABILITIES if stream not in latest or latest[stream]["status"] not in {"healthy","empty_valid"})
            if bad:raise CompanionError(f"G1 live capabilities are not qualified: {bad}")
            stale=sorted(stream for stream in G1_REQUIRED_TUSHARE_CAPABILITIES if stream in latest and utc_now()-parse(latest[stream]["checked_at"])>timedelta(hours=72))
            if stale:raise CompanionError(f"G1 live capabilities are stale (>72h): {stale}")
            if self.c.data.health()["blocked_quality_issues"]:raise CompanionError("G1 has unresolved blocking data quality issues")
        if gate=="G2":
            health=self.c.quant.health()
            if health.get("status")!="healthy" or not health.get("deterministic"):raise CompanionError("G2 QuantRuntime health failed")
        if gate=="G4":
            with self.db.connect() as con:count=con.execute("SELECT COUNT(*) FROM strategy_versions WHERE status IN ('research_passed','shadow')").fetchone()[0]
            if not count:raise CompanionError("G4 has no registry-derived research-passed strategy")
        if gate=="G6":
            eligible=[]
            for book in self.c.shadow.book_list():
                if self.c.shadow.sample_status(book["id"])["status"]=="eligible_for_review":
                    strategy=self.c.research.strategy_get(book["strategy_version_id"])
                    if strategy["status"]=="shadow":eligible.append(book["id"])
            if not eligible:raise CompanionError("G6 has no registry-derived Shadow Book with sufficient forward evidence")
