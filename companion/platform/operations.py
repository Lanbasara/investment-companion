from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from ..db import SCHEMA_VERSION, row_dict, rows_dict
from ..foundation import CompanionError
from ..timeutil import iso, utc_now


class SystemOperationsService:
    """Recovery, backup and health reporting for the modular runtime."""

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
        recovered.update(self.research_work.refresh())
        recovered["broker_execution_links"]=len(self.execution_strategy.recover_execution_links())
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
            counts={table:con.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] for table in ["schedules","runs","watches","events","cases","patrols","source_items","artifacts","outbox","delivery_records","accounts","assets","ledger_entries","calculations","context_revisions","cognitive_objects","cognitive_revisions","executions","broker_execution_plans","broker_managed_orders","broker_execution_events","attention_decisions","source_health","job_definitions","job_runs","data_objects","artifact_manifests","dataset_snapshots","research_hypotheses","strategy_versions","experiment_runs","agent_invocations","shadow_books","manual_action_specs","investment_programs","investment_program_revisions","opportunities","opportunity_funding_condition_transitions","research_work_items","decision_queue_items","operating_briefs","program_scorecards"]}
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
                "research_work":con.execute("SELECT COUNT(*) FROM research_work_items WHERE status IN ('queued','leased','waiting','monitoring')").fetchone()[0],
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
            "Orientation only—not evidence or a recommendation.",
            "For investment questions, start with investment_home and load relevant confirmed facts and current evidence.",
            "Prior research and briefs are dated evidence, not standing instructions; respect validity and changed facts.",
            "Compare action, bounded conditional action, no action and one alternative. Never infer a transaction or bypass user confirmation.",
        ]
        return {"ok":status["ok"],"workspace":str(self.root),"contexts":contexts,"active":active,"financial":{"accounts":status["counts"]["accounts"],"ledger_entries":status["counts"]["ledger_entries"]},"text":"\n".join(lines)}

    def doctor(self)->dict[str,Any]:
        status=self.system_status();checks={"database_integrity":status["integrity"]=="ok","workspace_writable":os.access(self.root,os.W_OK),"attention_policy":self.cognition.context_current("attention") is not None,"investor_confirmed":bool(self.cognition.context_current("investor")),"mandate_confirmed":bool(self.cognition.context_current("mandate"))}
        checks["financial_facts_ready"]=status["counts"]["accounts"]>0 and status["counts"]["ledger_entries"]>0
        checks["schema_current"]=status["meta"].get("schema_version")==str(SCHEMA_VERSION)
        checks["ordered_migrations"]=len(status.get("migrations",[]))>=3
        checks["required_results_not_overdue"]=status["delivery"]["overdue_required"]==0
        current_program=self.operating.program_current()
        research_work=self.research_work.summary(program_id=current_program["id"] if current_program else None)
        checks["research_work_not_overdue"]=research_work["overdue"]==0
        project_config=self.root/".codex"/"config.toml"
        if project_config.is_file():
            from ..agent_config import validate_agent_config
            checks["custom_agent_config"]=validate_agent_config(self.root)["ok"]
        production=self.production_health()
        checks["production_runtime_and_pipelines"]=production["ok"]
        from ..capabilities.runtime import compatibility_diagnostics
        compatibility=compatibility_diagnostics(self.capability_registry,self.root,self.gate_scope)
        return {"ok":all(v for k,v in checks.items() if k not in {"investor_confirmed","mandate_confirmed","financial_facts_ready"}),"checks":checks,"warnings":[k for k,v in checks.items() if not v],"production":production,"compatibility":compatibility,"status":status}

    def doctor_model_projection(self)->dict[str,Any]:
        result=self.doctor();result["compatibility"]=result["compatibility"]["summary"]
        return result
