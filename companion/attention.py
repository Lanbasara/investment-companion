from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from .core import CompanionError, canonical, digest, new_id
from .db import row_dict, rows_dict
from .timeutil import iso, utc_now


class AttentionEngine:
    def __init__(self,companion):self.c=companion;self.db=companion.db

    def decide(self,*,topic:str,materiality:str,confidence:str,reason:str,event_id:str|None=None,evidence:list|None=None,requested_action:str="notify_now")->dict:
        policy=self.c.cognition.context_current("attention")
        if not policy:raise CompanionError("no current attention policy; create and confirm one first")
        cfg=policy["content"];now=utc_now();action=requested_action;status="decided";explanations=[]
        quiet=cfg.get("quiet_hours")
        if quiet:
            local=now.astimezone(ZoneInfo(cfg.get("timezone","Asia/Shanghai")));current=local.strftime("%H:%M");start,end=quiet["start"],quiet["end"]
            in_quiet=(start<=current<end) if start<end else (current>=start or current<end)
            if in_quiet and materiality not in set(cfg.get("quiet_bypass_materiality",["critical"])):action="queue_digest";explanations.append("quiet hours")
        cooldown=int(cfg.get("topic_cooldown_seconds",0))
        if cooldown:
            with self.db.connect() as con:last=con.execute("SELECT created_at FROM attention_decisions WHERE topic=? AND action='notify_now' ORDER BY created_at DESC LIMIT 1",(topic,)).fetchone()
            if last:
                from .timeutil import parse
                if (now-parse(last[0])).total_seconds()<cooldown:action="suppress_duplicate";explanations.append("topic cooldown")
        budget=int(cfg.get("daily_notification_budget",3));day=now.strftime("%Y-%m-%d")
        with self.db.connect() as con:used=con.execute("SELECT COUNT(*) FROM attention_decisions WHERE action='notify_now' AND created_at>=?",(day+"T00:00:00Z",)).fetchone()[0]
        if action=="notify_now" and used>=budget and materiality not in set(cfg.get("budget_bypass_materiality",["critical"])):action="queue_digest";explanations.append("daily budget")
        key=digest(event_id,topic,materiality,reason,policy["id"]);aid=new_id("attn");created=iso()
        with self.db.transaction() as con:
            con.execute("INSERT OR IGNORE INTO attention_decisions(id,event_id,policy_revision_id,action,topic,materiality,confidence,reason,evidence_json,notification_key,status,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",(aid,event_id,policy["id"],action,topic,materiality,confidence,reason+("; "+", ".join(explanations) if explanations else ""),canonical(evidence or []),key,status,created));row=con.execute("SELECT * FROM attention_decisions WHERE notification_key=?",(key,)).fetchone()
        return row_dict(row)

    def get(self,decision_id:str)->dict:
        with self.db.connect() as con:item=row_dict(con.execute("SELECT * FROM attention_decisions WHERE id=?",(decision_id,)).fetchone())
        if not item:raise CompanionError(f"attention decision not found: {decision_id}")
        return item

    def list(self,action:str|None=None,limit:int=100)->list[dict]:
        with self.db.connect() as con:rows=con.execute("SELECT * FROM attention_decisions"+(" WHERE action=?" if action else "")+" ORDER BY created_at DESC LIMIT ?",((action,limit) if action else (limit,))).fetchall()
        return rows_dict(rows)

    def feedback(self,decision_id:str,feedback:str,note:str|None=None)->dict:
        if feedback not in {"useful","not_useful","false_positive","too_late","too_frequent"}:raise CompanionError("invalid attention feedback")
        self.get(decision_id);fid=new_id("fb");now=iso()
        with self.db.transaction() as con:con.execute("INSERT INTO attention_feedback(id,attention_decision_id,feedback,note,created_at) VALUES(?,?,?,?,?)",(fid,decision_id,feedback,note,now))
        return {"id":fid,"attention_decision_id":decision_id,"feedback":feedback,"note":note,"created_at":now,"policy_change":"proposal_required" if feedback in {"not_useful","false_positive","too_frequent"} else "none"}

    def mark_delivered(self,decision_id:str)->dict:
        item=self.get(decision_id)
        if item["action"]!="notify_now":raise CompanionError("only notify_now attention decision can be marked delivered")
        with self.db.transaction() as con:con.execute("UPDATE attention_decisions SET status='delivered',delivered_at=? WHERE id=?",(iso(),decision_id))
        return self.get(decision_id)
