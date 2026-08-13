from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from .core import Companion, CompanionError


def emit(value):
    print(json.dumps(value, ensure_ascii=False, indent=2, default=str))


def parser() -> argparse.ArgumentParser:
    p=argparse.ArgumentParser(prog="companion",description="Investment Companion V3 operational CLI")
    p.add_argument("--root",default=os.environ.get("COMPANION_ROOT","/home/ghk/investment-home"));sub=p.add_subparsers(dest="command",required=True)
    for name in ["init","status","doctor","tick","recover","bootstrap"]:sub.add_parser(name)
    wi=sub.add_parser("workspace-init");wi.add_argument("--finance-source")
    dispatch=sub.add_parser("dispatch");dispatch.add_argument("--dry-run",action="store_true");dispatch.add_argument("--limit",type=int,default=1)
    b=sub.add_parser("backup");b.add_argument("destination")
    ba=sub.add_parser("backup-auto");ba.add_argument("directory")
    sl=sub.add_parser("schedule-list");sl.add_argument("--status");sl.add_argument("--kind")
    sg=sub.add_parser("schedule-get");sg.add_argument("id")
    sc=sub.add_parser("schedule-create");sc.add_argument("--name",required=True);sc.add_argument("--kind",required=True);sc.add_argument("--mission",required=True);sc.add_argument("--cadence",required=True);sc.add_argument("--scope",default="{}");sc.add_argument("--policy",default="{}")
    ss=sub.add_parser("schedule-status");ss.add_argument("id");ss.add_argument("status",choices=["active","paused","archived"])
    sr=sub.add_parser("schedule-run-now");sr.add_argument("id")
    rl=sub.add_parser("run-list");rl.add_argument("--status")
    rc=sub.add_parser("run-claim");rc.add_argument("--owner",required=True)
    rd=sub.add_parser("run-complete");rd.add_argument("id");rd.add_argument("--failed",action="store_true");rd.add_argument("--error")
    il=sub.add_parser("inbox-list");il.add_argument("--status",default="new")
    ia=sub.add_parser("inbox-add");ia.add_argument("--source",required=True);ia.add_argument("--title",required=True);ia.add_argument("--url");ia.add_argument("--file")
    return p


def main(argv=None) -> int:
    a=parser().parse_args(argv);c=Companion(Path(a.root))
    try:
        if a.command=="init":result=c.initialize()
        elif a.command=="workspace-init":result=c.workspace_init(a.finance_source)
        else:
            c.initialize()
            if a.command=="status":result=c.system_status()
            elif a.command=="doctor":result=c.doctor()
            elif a.command=="tick":result=c.tick()
            elif a.command=="recover":result=c.recover()
            elif a.command=="bootstrap":result=c.bootstrap_defaults()
            elif a.command=="dispatch":result=c.dispatch_outbox(a.dry_run,a.limit)
            elif a.command=="backup":result=c.backup(a.destination)
            elif a.command=="backup-auto":result=c.backup_auto(a.directory)
            elif a.command=="schedule-list":result=c.schedule_list(a.status,a.kind)
            elif a.command=="schedule-get":result=c.schedule_get(a.id)
            elif a.command=="schedule-create":result=c.schedule_create(name=a.name,kind=a.kind,mission=a.mission,cadence=json.loads(a.cadence),scope=json.loads(a.scope),policy=json.loads(a.policy),actor="cli")
            elif a.command=="schedule-status":result=c.schedule_set_status(a.id,a.status,actor="cli")
            elif a.command=="schedule-run-now":result=c.schedule_run_now(a.id,actor="cli")
            elif a.command=="run-list":result=c.run_list(a.status)
            elif a.command=="run-claim":result=c.claim_run(a.owner)
            elif a.command=="run-complete":result=c.complete_run(a.id,not a.failed,a.error)
            elif a.command=="inbox-list":result=c.inbox_list(a.status)
            elif a.command=="inbox-add":result=c.inbox_add(source=a.source,title=a.title,url=a.url,content=Path(a.file).read_text(encoding="utf-8") if a.file else None)
            else:raise CompanionError("unsupported command")
        emit(result);return 0
    except (CompanionError,ValueError,KeyError) as e:
        emit({"ok":False,"error":str(e)});return 2


if __name__=="__main__":raise SystemExit(main())
