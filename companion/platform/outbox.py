from __future__ import annotations

import os
import socket
import subprocess
from datetime import timedelta
from typing import Any

from ..db import row_dict, rows_dict
from ..foundation import CompanionError, canonical, digest, new_id
from ..timeutil import iso, utc_now


class OutboxService:
    """Durable delivery and wake transport state, separate from investment truth."""

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
            if item["status"]!="sending" or item.get("lease_owner")!=lease_owner or not item.get("lease_until") or item["lease_until"]<=now:raise CompanionError("investment_workflow.wake_lease_conflict: wake completion does not own the active outbox lease")
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
