from __future__ import annotations

import argparse
import json
import os
import socket
import sys
from pathlib import Path

from .agent_config import validate_agent_config
from .core import Companion, CompanionError
from .interfaces.mcp_profiles import INVESTMENT_CAPABILITY_REGISTRY


def emit(value):
    print(json.dumps(value, ensure_ascii=False, indent=2, default=str))


def parser() -> argparse.ArgumentParser:
    p=argparse.ArgumentParser(prog="companion",description="Investment Companion operational CLI")
    p.add_argument("--root",default=os.environ.get("COMPANION_ROOT","/home/ghk/investment-home"));sub=p.add_subparsers(dest="command",required=True)
    for name in ["init","status","doctor","production-doctor","recover","bootstrap","agent-check","session-brief","v5-status","today","v5-quant-status","v5-experiment-status","v6-predictive-status","delivery-status","research-work-backfill-latest"]:sub.add_parser(name)
    tick=sub.add_parser("tick");tick.add_argument("--limit",type=int,default=20)
    quality=sub.add_parser("v5-research-quality");quality.add_argument("--days",type=int,default=30)
    migrate=sub.add_parser("migrate");migrate.add_argument("--backup-directory",required=True)
    sub.add_parser("v4-status")
    bootstrap_v4=sub.add_parser("v4-bootstrap-jobs");bootstrap_v4.add_argument("--activate",action="store_true")
    bootstrap_v5_experiment=sub.add_parser("v5-experiment-bootstrap");bootstrap_v5_experiment.add_argument("--activate",action="store_true")
    backfill_v5_experiment=sub.add_parser("v5-experiment-backfill");backfill_v5_experiment.add_argument("--through-date",required=True);backfill_v5_experiment.add_argument("--sessions",type=int,default=21)
    bootstrap_v5_quant=sub.add_parser("v5-quant-bootstrap");bootstrap_v5_quant.add_argument("--activate",action="store_true")
    bootstrap_v6_predictive=sub.add_parser("v6-predictive-bootstrap");bootstrap_v6_predictive.add_argument("--activate",action="store_true")
    backfill_v5_quant=sub.add_parser("v5-quant-backfill");backfill_v5_quant.add_argument("--through-date",required=True);backfill_v5_quant.add_argument("--sessions",type=int,default=21)
    worker=sub.add_parser("job-work");worker.add_argument("--limit",type=int,default=1);worker.add_argument("--owner")
    gates=sub.add_parser("gate-list");gates.add_argument("--scope",choices=["production","test_fixture"])
    gate_report=sub.add_parser("gate-report-publish");gate_report.add_argument("kind");gate_report.add_argument("--checks",required=True);gate_report.add_argument("--input-refs",required=True);gate_report.add_argument("--commands",required=True);gate_report.add_argument("--observations",required=True);gate_report.add_argument("--scope",choices=["production","test_fixture"])
    gate_evidence=sub.add_parser("gate-evidence-publish");gate_evidence.add_argument("gate");gate_evidence.add_argument("--checks",required=True);gate_evidence.add_argument("--artifacts",required=True);gate_evidence.add_argument("--unknowns",default="[]");gate_evidence.add_argument("--counterevidence",default="[]");gate_evidence.add_argument("--dispositions",default="{}");gate_evidence.add_argument("--code-version");gate_evidence.add_argument("--scope",choices=["production","test_fixture"])
    gate_assess=sub.add_parser("gate-assess");gate_assess.add_argument("gate");gate_assess.add_argument("status",choices=["go","conditional_go","no_go","pending"]);gate_assess.add_argument("evidence_manifest_id");gate_assess.add_argument("--code-version");gate_assess.add_argument("--approval-ref");gate_assess.add_argument("--conditions",default="[]");gate_assess.add_argument("--scope",choices=["production","test_fixture"])
    sub.add_parser("feature-list");sub.add_parser("quant-health");sub.add_parser("data-health")
    feature_set=sub.add_parser("feature-set");feature_set.add_argument("key");feature_mode=feature_set.add_mutually_exclusive_group(required=True);feature_mode.add_argument("--enable",action="store_true");feature_mode.add_argument("--disable",action="store_true");feature_set.add_argument("--config",default="{}");feature_set.add_argument("--reason",required=True)
    job_status=sub.add_parser("job-definition-status");job_status.add_argument("id");job_status.add_argument("status",choices=["inactive","active","paused","archived"]);job_status.add_argument("--reason",required=True)
    probe=sub.add_parser("tushare-probe");probe.add_argument("capability");probe.add_argument("--params",default="{}");probe.add_argument("--fields",default="[]");probe.add_argument("--token-file")
    stream=sub.add_parser("tushare-stream-configure");stream.add_argument("capability");stream.add_argument("--schema-version",default="tushare-normalizer/2");stream.add_argument("--config",default="{}")
    stream_status=sub.add_parser("tushare-stream-status");stream_status.add_argument("capability");stream_status.add_argument("status",choices=["canary","active","paused","blocked","archived"])
    canary=sub.add_parser("tushare-canary");canary.add_argument("capability");canary.add_argument("--params",required=True);canary.add_argument("--fields",default="[]");canary.add_argument("--token-file")
    pv=sub.add_parser("partition-validate");pv.add_argument("object_id");pv.add_argument("--name",required=True);pv.add_argument("--stream",required=True);pv.add_argument("--role",required=True);pv.add_argument("--knowledge-cutoff",required=True)
    sp=sub.add_parser("snapshot-publish");sp.add_argument("manifest_file")
    wi=sub.add_parser("workspace-init");wi.add_argument("--finance-source")
    dispatch=sub.add_parser("dispatch");dispatch.add_argument("--dry-run",action="store_true");dispatch.add_argument("--limit",type=int,default=20)
    wake_claim=sub.add_parser("wake-claim");wake_claim.add_argument("--owner",required=True);wake_claim.add_argument("--lease-seconds",type=int,default=1800)
    wake_complete=sub.add_parser("wake-complete");wake_complete.add_argument("id");wake_complete.add_argument("--owner",required=True);wake_complete.add_argument("--failed",action="store_true");wake_complete.add_argument("--error")
    b=sub.add_parser("backup");b.add_argument("destination")
    ba=sub.add_parser("backup-auto");ba.add_argument("directory")
    sl=sub.add_parser("schedule-list");sl.add_argument("--status");sl.add_argument("--kind")
    sg=sub.add_parser("schedule-get");sg.add_argument("id")
    patch_schedule=sub.add_parser("schedule-patch");patch_schedule.add_argument("id");patch_schedule.add_argument("--expected-version",type=int,required=True);patch_schedule.add_argument("--changes",required=True);patch_schedule.add_argument("--reason",required=True)
    sc=sub.add_parser("schedule-create");sc.add_argument("--name",required=True);sc.add_argument("--kind",required=True);sc.add_argument("--mission",required=True);sc.add_argument("--cadence",required=True);sc.add_argument("--scope",default="{}");sc.add_argument("--policy",default="{}");sc.add_argument("--dispatch-type",choices=["codex_turn","deterministic_pipeline"],default="codex_turn");sc.add_argument("--job-definition-id")
    ss=sub.add_parser("schedule-status");ss.add_argument("id");ss.add_argument("status",choices=["active","paused","archived"])
    sr=sub.add_parser("schedule-run-now");sr.add_argument("id")
    rl=sub.add_parser("run-list");rl.add_argument("--status")
    rc=sub.add_parser("run-claim");rc.add_argument("--owner",required=True)
    rd=sub.add_parser("run-complete");rd.add_argument("id");rd.add_argument("--failed",action="store_true");rd.add_argument("--error")
    il=sub.add_parser("inbox-list");il.add_argument("--status",default="new")
    ia=sub.add_parser("inbox-add");ia.add_argument("--source",required=True);ia.add_argument("--title",required=True);ia.add_argument("--url");ia.add_argument("--file")
    dl=sub.add_parser("delivery-list");dl.add_argument("--status");dl.add_argument("--mode");dl.add_argument("--limit",type=int,default=100)
    dg=sub.add_parser("delivery-get");dg.add_argument("id")
    dp=sub.add_parser("delivery-prepare");dp.add_argument("id");dp.add_argument("--conclusion",required=True,choices=["no_action","action","risk_action","review_required","insufficient_evidence","system_degraded"]);dp.add_argument("--summary",required=True);dp.add_argument("--key-evidence",required=True);dp.add_argument("--next-step",required=True);dp.add_argument("--next-check-at");dp.add_argument("--source-refs",default="[]")
    ds=sub.add_parser("delivery-digest-send");ds.add_argument("--ids",required=True);ds.add_argument("--conclusion",required=True,choices=["no_action","action","risk_action","review_required","insufficient_evidence","system_degraded"]);ds.add_argument("--summary",required=True);ds.add_argument("--key-evidence",required=True);ds.add_argument("--next-step",required=True);ds.add_argument("--next-check-at");ds.add_argument("--source-refs",default="[]")
    dm=sub.add_parser("delivery-migrate-schedule-policies");dm.add_argument("--apply",action="store_true")
    return p


def main(argv=None) -> int:
    a=parser().parse_args(argv);c=Companion(Path(a.root),capability_registry=INVESTMENT_CAPABILITY_REGISTRY)
    try:
        if a.command=="init":result=c.initialize()
        elif a.command=="migrate":result=c.migrate(a.backup_directory)
        elif a.command=="workspace-init":result=c.workspace_init(a.finance_source)
        elif a.command=="agent-check":result=validate_agent_config(Path(a.root))
        else:
            c.initialize()
            if a.command=="status":result=c.system_status()
            elif a.command=="v4-status":result=c.v4_status()
            elif a.command=="v5-status":result=c.v5_status()
            elif a.command in {"v5-quant-status","v5-experiment-status"}:result=c.quant_research.status()
            elif a.command=="v6-predictive-status":result=c.v6_predictive.status()
            elif a.command=="delivery-status":result=c.delivery.status()
            elif a.command=="research-work-backfill-latest":result=c.research_work.backfill_latest_candidates(actor="cli-rollout")
            elif a.command=="v5-research-quality":result=c.research_quality_status(a.days)
            elif a.command=="today":result=c.operating.today()
            elif a.command=="v4-bootstrap-jobs":result=c.v4_bootstrap_jobs(activate=a.activate)
            elif a.command in {"v5-quant-bootstrap","v5-experiment-bootstrap"}:result=c.quant_research.bootstrap(activate=a.activate)
            elif a.command=="v6-predictive-bootstrap":result=c.v6_predictive.bootstrap(activate=a.activate)
            elif a.command in {"v5-quant-backfill","v5-experiment-backfill"}:result=c.quant_research.prepare_backfill(through_date=a.through_date,sessions=a.sessions)
            elif a.command=="job-work":
                owner=a.owner or f"{socket.gethostname()}:{os.getpid()}";runs=[]
                for _ in range(max(0,a.limit)):
                    item=c.jobs.run_once(owner)
                    if item is None:break
                    runs.append(item)
                delivery=c.dispatch_outbox(limit=20) if os.environ.get("COMPANION_CC_WAKE_CRON") else {"ok":True,"results":[]}
                result={"ok":delivery["ok"],"owner":owner,"runs":runs,"delivery":delivery}
            elif a.command=="gate-list":result=c.gates.list(a.scope or c.gate_scope)
            elif a.command=="gate-report-publish":result=c.gates.validation_report_publish(kind=a.kind,checks=json.loads(a.checks),input_refs=json.loads(a.input_refs),commands=json.loads(a.commands),observations=json.loads(a.observations),scope=a.scope,actor="primary-codex")
            elif a.command=="gate-evidence-publish":
                scope=a.scope or c.gate_scope;code_version=a.code_version or (c.gates._git_head() if scope=="production" else "test-fixture")
                result=c.gates.evidence_publish(a.gate,checks=json.loads(a.checks),artifacts=json.loads(a.artifacts),unknowns=json.loads(a.unknowns),counterevidence=json.loads(a.counterevidence),counterevidence_disposition=json.loads(a.dispositions),code_version=code_version,scope=scope)
            elif a.command=="gate-assess":
                scope=a.scope or c.gate_scope;code_version=a.code_version or (c.gates._git_head() if scope=="production" else "test-fixture")
                result=c.gates.assessment_record(gate=a.gate,status=a.status,evidence_manifest_id=a.evidence_manifest_id,code_version=code_version,assessed_by="primary-codex",approval_ref=a.approval_ref,conditions=json.loads(a.conditions),scope=scope)
            elif a.command=="feature-list":result=c.jobs.feature_list()
            elif a.command=="feature-set":result=c.jobs.feature_set(a.key,a.enable,config=json.loads(a.config),actor="cli",reason=a.reason)
            elif a.command=="job-definition-status":result=c.jobs.definition_set_status(a.id,a.status,actor="cli",reason=a.reason)
            elif a.command=="quant-health":result=c.quant.health()
            elif a.command=="data-health":result=c.data.health()
            elif a.command=="tushare-probe":
                from .tushare_adapter import TushareAdapter
                result=TushareAdapter(c,token_file=a.token_file).probe(a.capability,params=json.loads(a.params),fields=json.loads(a.fields))
            elif a.command=="tushare-stream-configure":result=c.data.stream_configure(provider="tushare",capability=a.capability,schema_version=a.schema_version,config=json.loads(a.config),status="canary")
            elif a.command=="tushare-stream-status":result=c.data.stream_set_status("tushare",a.capability,a.status)
            elif a.command=="tushare-canary":
                from .tushare_adapter import TushareAdapter
                result=TushareAdapter(c,token_file=a.token_file).ingest_canary(a.capability,params=json.loads(a.params),fields=json.loads(a.fields))
            elif a.command=="partition-validate":result=c.data.partition_validate(object_id=a.object_id,partition_name=a.name,stream=a.stream,role=a.role,knowledge_cutoff=a.knowledge_cutoff)
            elif a.command=="snapshot-publish":result=c.data.snapshot_validate_and_publish(json.loads(Path(a.manifest_file).read_text(encoding="utf-8")))
            elif a.command=="session-brief":result=c.session_brief()
            elif a.command=="doctor":result=c.doctor()
            elif a.command=="production-doctor":result=c.production_health()
            elif a.command=="tick":result=c.tick(limit=a.limit)
            elif a.command=="recover":result=c.recover()
            elif a.command=="bootstrap":result=c.bootstrap_defaults()
            elif a.command=="dispatch":result=c.dispatch_outbox(a.dry_run,a.limit)
            elif a.command=="wake-claim":result=c.wake_claim(a.owner,a.lease_seconds)
            elif a.command=="wake-complete":result=c.wake_complete(a.id,a.owner,not a.failed,a.error)
            elif a.command=="backup":result=c.backup(a.destination)
            elif a.command=="backup-auto":result=c.backup_auto(a.directory)
            elif a.command=="schedule-list":result=c.schedule_list(a.status,a.kind)
            elif a.command=="schedule-get":result=c.schedule_get(a.id)
            elif a.command=="schedule-patch":result=c.schedule_patch(a.id,a.expected_version,json.loads(a.changes),actor="cli",reason=a.reason)
            elif a.command=="schedule-create":result=c.schedule_create(name=a.name,kind=a.kind,mission=a.mission,cadence=json.loads(a.cadence),scope=json.loads(a.scope),policy=json.loads(a.policy),dispatch_type=a.dispatch_type,job_definition_id=a.job_definition_id,actor="cli")
            elif a.command=="schedule-status":result=c.schedule_set_status(a.id,a.status,actor="cli")
            elif a.command=="schedule-run-now":result=c.schedule_run_now(a.id,actor="cli")
            elif a.command=="run-list":result=c.run_list(a.status)
            elif a.command=="run-claim":result=c.claim_run(a.owner)
            elif a.command=="run-complete":result=c.complete_run(a.id,not a.failed,a.error)
            elif a.command=="inbox-list":result=c.inbox_list(a.status)
            elif a.command=="inbox-add":result=c.inbox_add(source=a.source,title=a.title,url=a.url,content=Path(a.file).read_text(encoding="utf-8") if a.file else None)
            elif a.command=="delivery-list":result=c.delivery.list(status=a.status,mode=a.mode,limit=a.limit)
            elif a.command=="delivery-get":result=c.delivery.get(a.id)
            elif a.command=="delivery-prepare":result=c.delivery.prepare(a.id,conclusion=a.conclusion,summary=a.summary,key_evidence=json.loads(a.key_evidence),next_step=a.next_step,next_check_at=a.next_check_at,source_refs=json.loads(a.source_refs))
            elif a.command=="delivery-digest-send":result=c.delivery.digest_send(json.loads(a.ids),conclusion=a.conclusion,summary=a.summary,key_evidence=json.loads(a.key_evidence),next_step=a.next_step,next_check_at=a.next_check_at,source_refs=json.loads(a.source_refs))
            elif a.command=="delivery-migrate-schedule-policies":result=c.delivery.migrate_schedule_policies(apply=a.apply,actor="cli")
            else:raise CompanionError("unsupported command")
        emit(result)
        return 2 if a.command=="agent-check" and not result["ok"] else 0
    except (CompanionError,RuntimeError,ValueError,KeyError) as e:
        emit({"ok":False,"error":str(e)});return 2


if __name__=="__main__":raise SystemExit(main())
