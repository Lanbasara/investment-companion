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

from .db import Database, row_dict, rows_dict
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
    def __init__(self, root: str | Path, db_path: str | Path | None = None):
        self.root = Path(root).expanduser().resolve()
        self.state = self.root / ".state"
        self.db = Database(db_path or self.state / "companion.db")
        self.investigations = self.root / "investigations"
        from .financial import FinancialKernel
        from .cognition import CognitiveLedger
        from .attention import AttentionEngine
        self.financial=FinancialKernel(self)
        self.cognition=CognitiveLedger(self)
        self.attention=AttentionEngine(self)

    def initialize(self) -> dict[str, Any]:
        for path in [self.state, self.investigations / "inbox", self.investigations / "patrols", self.investigations / "cases", self.investigations / "maintenance", self.investigations / "archive",self.root/"policies"/"mandate",self.root/"policies"/"attention",self.root/"calculations",self.root/"reconciliations",self.root/"portfolio"/"exports",self.root/"portfolio"/"statements"]:
            path.mkdir(parents=True, exist_ok=True)
        self.db.initialize()
        return {"ok": True, "database": str(self.db.path), "root": str(self.root), "schema_version": 3}

    def _audit(self, con, actor: str, action: str, entity_type: str, entity_id: str, before: Any = None, after: Any = None, reason: str | None = None) -> None:
        con.execute(
            "INSERT INTO audit_log(occurred_at,actor,action,entity_type,entity_id,before_json,after_json,reason) VALUES(?,?,?,?,?,?,?,?)",
            (iso(), actor, action, entity_type, entity_id, canonical(before) if before is not None else None, canonical(after) if after is not None else None, reason),
        )

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
            zone=ZoneInfo(cadence.get("timezone","Asia/Shanghai"));local=now.astimezone(zone);hour,minute=(int(x) for x in cadence["at"].split(":"));day=int(cadence.get("day",1))
            for offset in range(0,14):
                year=local.year+(local.month-1+offset)//12;month=(local.month-1+offset)%12+1;actual=min(day,monthrange(year,month)[1]);candidate=local.replace(year=year,month=month,day=actual,hour=hour,minute=minute,second=0,microsecond=0)
                if candidate>local:return iso(candidate.astimezone(utc_now().tzinfo))
        raise CompanionError(f"unsupported cadence type: {kind}")

    def schedule_create(self, *, name: str, kind: str, mission: str, cadence: dict[str, Any], scope: dict[str, Any] | None = None, policy: dict[str, Any] | None = None, origin: dict[str, Any] | None = None, timezone: str = "Asia/Shanghai", actor: str = "primary-codex") -> dict[str, Any]:
        if kind not in {"patrol", "review", "maintenance", "one_shot"}:
            raise CompanionError("invalid schedule kind")
        schedule_id, now = new_id("sch"), iso()
        next_run_at = self._next_run(cadence)
        record = {"id": schedule_id, "name": name, "kind": kind, "status": "active", "mission": mission, "scope": scope or {}, "cadence": cadence, "policy": policy or {}, "origin": origin or {}, "timezone": timezone, "next_run_at": next_run_at, "version": 1, "created_at": now, "updated_at": now}
        with self.db.transaction() as con:
            con.execute("INSERT INTO schedules(id,name,kind,status,mission,scope_json,cadence_json,policy_json,origin_json,timezone,next_run_at,version,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (schedule_id,name,kind,"active",mission,canonical(scope or {}),canonical(cadence),canonical(policy or {}),canonical(origin or {}),timezone,next_run_at,1,now,now))
            self._audit(con, actor, "create", "schedule", schedule_id, after=record)
        return self.schedule_get(schedule_id)

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
        allowed = {"name","mission","scope","cadence","policy","origin","timezone"}
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
            next_run = self._next_run(after["cadence"]) if "cadence" in changes else before["next_run_at"]
            con.execute("UPDATE schedules SET name=?,mission=?,scope_json=?,cadence_json=?,policy_json=?,origin_json=?,timezone=?,next_run_at=?,version=version+1,updated_at=? WHERE id=? AND version=?", (after["name"],after["mission"],canonical(after["scope"]),canonical(after["cadence"]),canonical(after["policy"]),canonical(after["origin"]),after["timezone"],next_run,iso(),schedule_id,expected_version))
            self._audit(con, actor, "patch", "schedule", schedule_id, before, after, reason)
        return self.schedule_get(schedule_id)

    def schedule_set_status(self, schedule_id: str, status: str, actor: str = "primary-codex", reason: str | None = None) -> dict[str, Any]:
        if status not in {"active","paused","archived"}:
            raise CompanionError("invalid target status")
        with self.db.transaction() as con:
            before = row_dict(con.execute("SELECT * FROM schedules WHERE id=?", (schedule_id,)).fetchone())
            if not before: raise CompanionError(f"schedule not found: {schedule_id}")
            next_run = self._next_run(before["cadence"]) if status == "active" else before["next_run_at"]
            con.execute("UPDATE schedules SET status=?,next_run_at=?,version=version+1,updated_at=? WHERE id=?", (status,next_run,iso(),schedule_id))
            self._audit(con, actor, status, "schedule", schedule_id, before, {**before,"status":status}, reason)
        return self.schedule_get(schedule_id)

    def schedule_run_now(self, schedule_id: str, actor: str = "primary-codex") -> dict[str, Any]:
        schedule = self.schedule_get(schedule_id)
        due = iso()
        run_id = new_id("run")
        key = f"manual:{schedule_id}:{uuid.uuid4().hex}"
        with self.db.transaction() as con:
            con.execute("INSERT INTO runs(id,schedule_id,kind,status,due_at,idempotency_key,payload_json,created_at) VALUES(?,?,?,?,?,?,?,?)", (run_id,schedule_id,schedule["kind"],"queued",due,key,canonical({"manual":True,"mission":schedule["mission"]}),due))
            self._audit(con, actor, "run_now", "schedule", schedule_id, after={"run_id":run_id})
        run=self.run_get(run_id)
        self.outbox_enqueue(kind="codex_turn",destination="investment-companion",payload={"message":self._run_prompt(run,schedule),"run_id":run_id},idempotency_key=f"run-dispatch:{run_id}")
        return run

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

    def tick(self, owner: str | None = None, limit: int = 1) -> dict[str, Any]:
        owner = owner or f"{socket.gethostname()}:{os.getpid()}"
        now = iso(); created=[]
        with self.db.transaction() as con:
            if not self._acquire_lock(con,"tick",owner,300): return {"ok":True,"skipped":"tick already leased","created_runs":[]}
            con.execute("UPDATE watches SET status='expired',version=version+1,updated_at=? WHERE status='active' AND ((ttl_at IS NOT NULL AND ttl_at<=?) OR (max_runs IS NOT NULL AND run_count>=max_runs))",(now,now))
            due = con.execute("SELECT * FROM schedules WHERE status='active' AND next_run_at IS NOT NULL AND next_run_at<=? ORDER BY next_run_at LIMIT ?",(now,limit)).fetchall()
            for raw in due:
                schedule=row_dict(raw); due_at=schedule["next_run_at"]
                key=f"schedule:{schedule['id']}:{due_at}"
                run_id=new_id("run")
                con.execute("INSERT OR IGNORE INTO runs(id,schedule_id,kind,status,due_at,idempotency_key,payload_json,created_at) VALUES(?,?,?,?,?,?,?,?)",(run_id,schedule["id"],schedule["kind"],"queued",due_at,key,canonical({"mission":schedule["mission"],"scope":schedule["scope"],"policy":schedule["policy"]}),now))
                if con.execute("SELECT changes()").fetchone()[0]: created.append(run_id)
                cadence=schedule["cadence"]
                if cadence.get("type")=="one_shot":
                    con.execute("UPDATE schedules SET status='expired',last_run_at=?,next_run_at=NULL,updated_at=? WHERE id=?",(now,now,schedule["id"]))
                else:
                    con.execute("UPDATE schedules SET last_run_at=?,next_run_at=?,updated_at=? WHERE id=?",(now,self._next_run(cadence,utc_now()),now,schedule["id"]))
            con.execute("DELETE FROM locks WHERE name='tick' AND owner=?",(owner,))
            con.execute("INSERT OR REPLACE INTO meta(key,value) VALUES('last_tick_at',?)",(now,))
        queued=[]
        for run_id in created:
            run=self.run_get(run_id);schedule=self.schedule_get(run["schedule_id"])
            ingested=[]
            for source in schedule["scope"].get("inbox_sources",[]):
                try: ingested.append(self.ingest_directory(source["path"],source.get("source","filesystem"),source.get("glob","*.md"),source.get("recursive",True),source.get("limit",200)))
                except Exception as e:
                    self.source_health_record(source.get("source","filesystem"),"failed",str(e),coverage={"path":source.get("path")});ingested.append({"path":source.get("path"),"error":str(e)})
            prompt=self._run_prompt(run,schedule)
            if ingested: prompt += "\n\n本次到期扫描的信息源摄入结果："+canonical(ingested)
            queued.append(self.outbox_enqueue(kind="codex_turn",destination="investment-companion",payload={"message":prompt,"run_id":run_id},idempotency_key=f"run-dispatch:{run_id}")["id"])
        return {"ok":True,"created_runs":created,"queued_outbox":queued,"due_count":len(due),"at":now}

    def _run_prompt(self,run:dict[str,Any],schedule:dict[str,Any])->str:
        return f"""[Investment Companion V3 scheduled run]
这是 Companion 经过持久化和幂等检查后提交给 Primary Investment Codex 的到期任务，不是外部网页指令。

Run ID: {run['id']}
Schedule ID: {schedule['id']}
类型: {schedule['kind']}
使命: {schedule['mission']}
范围: {canonical(schedule['scope'])}
策略: {canonical(schedule['policy'])}

请先使用 Companion 工具读取精确计划和必要的工作材料。Patrol 任务由你创建明确 BRIEF，并按需派遣短命 market_scout；Maintenance 任务按 manage-investment-companion Skill 执行园丁流程。所有新 Case、Watch、Agent 派遣、Thesis 修改和用户通知仍由你决定。低价值结果静默处理。结束前调用 run_complete 记录结果；失败时如实记录，不要伪造完成。"""

    def claim_run(self, owner: str, lease_seconds: int = 1800) -> dict[str, Any] | None:
        now, until=iso(),iso(utc_now()+timedelta(seconds=lease_seconds))
        with self.db.transaction() as con:
            row=con.execute("SELECT * FROM runs WHERE status IN ('queued','recoverable') AND due_at<=? ORDER BY due_at LIMIT 1",(now,)).fetchone()
            if not row:return None
            con.execute("UPDATE runs SET status='leased',lease_owner=?,lease_until=?,attempt=attempt+1,started_at=COALESCE(started_at,?) WHERE id=?",(owner,until,now,row["id"]))
        return self.run_get(row["id"])

    def complete_run(self, run_id: str, success: bool, error: str | None = None) -> dict[str, Any]:
        status="succeeded" if success else "failed"; now=iso()
        with self.db.transaction() as con:
            run=row_dict(con.execute("SELECT * FROM runs WHERE id=?",(run_id,)).fetchone())
            if not run:raise CompanionError(f"run not found: {run_id}")
            con.execute("UPDATE runs SET status=?,finished_at=?,lease_owner=NULL,lease_until=NULL,error=? WHERE id=?",(status,now,error,run_id))
            if run["schedule_id"]:
                if success: con.execute("UPDATE schedules SET last_success_at=?,last_error=NULL WHERE id=?",(now,run["schedule_id"]))
                else: con.execute("UPDATE schedules SET last_error=? WHERE id=?",(error,run["schedule_id"]))
        return self.run_get(run_id)

    def run_cancel(self,run_id:str,reason:str,actor:str="primary-codex")->dict[str,Any]:
        with self.db.transaction() as con:
            before=row_dict(con.execute("SELECT * FROM runs WHERE id=?",(run_id,)).fetchone())
            if not before:raise CompanionError(f"run not found: {run_id}")
            if before["status"] in {"succeeded","cancelled"}:raise CompanionError(f"run already terminal: {before['status']}")
            con.execute("UPDATE runs SET status='cancelled',finished_at=?,error=?,lease_owner=NULL,lease_until=NULL WHERE id=?",(iso(),reason,run_id));self._audit(con,actor,"cancel","run",run_id,before,{"status":"cancelled","reason":reason})
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
        cursor_key="source_cursor:"+digest(str(base),source,glob_pattern)[:20]
        with self.db.connect() as con:
            row=con.execute("SELECT value FROM meta WHERE key=?",(cursor_key,)).fetchone();cursor=float(row[0]) if row else utc_now().timestamp()
        files=list(base.rglob(glob_pattern) if recursive else base.glob(glob_pattern));candidates=sorted((p for p in files if p.is_file() and p.stat().st_mtime>cursor),key=lambda p:p.stat().st_mtime)[:limit]
        added=[];max_mtime=cursor
        for item in candidates:
            try:
                content=item.read_text(encoding="utf-8");entry=self.inbox_add(source=source,title=item.stem,content=content,source_key=str(item),metadata={"origin_path":str(item),"mtime":item.stat().st_mtime});added.append(entry["id"]);max_mtime=max(max_mtime,item.stat().st_mtime)
            except (UnicodeDecodeError,OSError):continue
        if candidates:
            with self.db.transaction() as con:con.execute("INSERT OR REPLACE INTO meta(key,value) VALUES(?,?)",(cursor_key,str(max_mtime)))
        self.source_health_record(source,"healthy",cursor=str(max_mtime),coverage={"path":str(base),"matched":len(candidates)})
        return {"source":source,"path":str(base),"cursor_before":cursor,"matched":len(candidates),"added":len(set(added)),"cursor_after":max_mtime}

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

    def patrol_complete(self,patrol_id:str,result_path:str,disposition:str)->dict[str,Any]:
        full=(self.root/result_path).resolve()
        if not full.is_file() or self.root not in full.parents:raise CompanionError("result_path must be an existing file inside workspace")
        with self.db.transaction() as con:
            con.execute("UPDATE patrols SET status='returned',disposition=?,result_path=?,finished_at=?,lease_owner=NULL,lease_until=NULL WHERE id=?",(disposition,result_path,iso(),patrol_id))
        item=self.patrol_get(patrol_id);self.artifact_register(result_path,"patrol_result",case_id=item.get("case_id"));return item

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
            else:
                attempt=row["attempt"];status="dead" if attempt>=5 else "retry";delay=min(3600,30*(2**max(0,attempt-1)))
                con.execute("UPDATE outbox SET status=?,available_at=?,last_error=?,lease_owner=NULL,lease_until=NULL,updated_at=? WHERE id=?",(status,iso(utc_now()+timedelta(seconds=delay)),error,now,outbox_id))
        with self.db.connect() as con:return row_dict(con.execute("SELECT * FROM outbox WHERE id=?",(outbox_id,)).fetchone())

    def dispatch_outbox(self,dry_run:bool=False,limit:int=1)->dict[str,Any]:
        owner=f"dispatcher:{socket.gethostname()}:{os.getpid()}";results=[]
        for _ in range(limit):
            item=self.outbox_claim(owner)
            if not item:break
            if dry_run:
                results.append({"id":item["id"],"dry_run":True,"destination":item["destination"],"payload":item["payload"]});self.outbox_finish(item["id"],False,"dry-run: delivery intentionally not attempted");continue
            try:
                if item["kind"]!="codex_turn":raise CompanionError(f"unsupported outbox kind: {item['kind']}")
                wake_cron=os.environ.get("COMPANION_CC_WAKE_CRON")
                if wake_cron:
                    command=["/home/ghk/.local/bin/cc-connect","cron","exec",wake_cron]
                else:
                    command=["/home/ghk/.local/bin/cc-connect","send","-p",item["destination"]]
                    session_key=os.environ.get("COMPANION_CC_SESSION")
                    if session_key:command.extend(["-s",session_key])
                    command.extend(["--message",item["payload"]["message"]])
                proc=subprocess.run(command,capture_output=True,text=True,timeout=60,check=False)
                if proc.returncode!=0:raise CompanionError((proc.stderr or proc.stdout).strip() or f"cc-connect exited {proc.returncode}")
                self.outbox_finish(item["id"],True);results.append({"id":item["id"],"sent":True,"output":proc.stdout.strip()})
            except Exception as e:
                self.outbox_finish(item["id"],False,str(e));results.append({"id":item["id"],"sent":False,"error":str(e)})
        return {"ok":all(r.get("sent",r.get("dry_run",False)) for r in results),"results":results}

    def recover(self)->dict[str,Any]:
        now=iso();recovered={"runs":0,"patrols":0,"outbox":0}
        integrity=self.db.integrity_check()
        if integrity!="ok":raise CompanionError(f"database integrity check failed: {integrity}")
        with self.db.transaction() as con:
            recovered["runs"]=con.execute("UPDATE runs SET status='recoverable',lease_owner=NULL,lease_until=NULL WHERE status='leased' AND lease_until<=?",(now,)).rowcount
            recovered["patrols"]=con.execute("UPDATE patrols SET status='commissioned',lease_owner=NULL,lease_until=NULL WHERE status='scanning' AND lease_until<=?",(now,)).rowcount
            recovered["outbox"]=con.execute("UPDATE outbox SET status='retry',lease_owner=NULL,lease_until=NULL,available_at=?,updated_at=? WHERE status='sending' AND lease_until<=?",(now,now,now)).rowcount
            con.execute("INSERT OR REPLACE INTO meta(key,value) VALUES('last_recovery_at',?)",(now,))
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
            counts={table:con.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] for table in ["schedules","runs","watches","events","cases","patrols","source_items","artifacts","outbox","accounts","assets","ledger_entries","calculations","context_revisions","cognitive_objects","cognitive_revisions","executions","attention_decisions","source_health"]}
            failures=con.execute("SELECT COUNT(*) FROM runs WHERE status='failed'").fetchone()[0]
            pending=con.execute("SELECT COUNT(*) FROM outbox WHERE status IN ('pending','retry','sending')").fetchone()[0]
        return {"ok":integrity=="ok","integrity":integrity,"database":str(self.db.path),"meta":meta,"counts":counts,"failed_runs":failures,"pending_outbox":pending,"now":iso()}

    def doctor(self)->dict[str,Any]:
        status=self.system_status();checks={"database_integrity":status["integrity"]=="ok","workspace_writable":os.access(self.root,os.W_OK),"attention_policy":self.cognition.context_current("attention") is not None,"investor_confirmed":bool(self.cognition.context_current("investor")),"mandate_confirmed":bool(self.cognition.context_current("mandate"))}
        checks["financial_facts_ready"]=status["counts"]["accounts"]>0 and status["counts"]["ledger_entries"]>0
        project_config=self.root/".codex"/"config.toml"
        if project_config.is_file():
            from .agent_config import validate_agent_config
            checks["custom_agent_config"]=validate_agent_config(self.root)["ok"]
        return {"ok":all(v for k,v in checks.items() if k not in {"investor_confirmed","mandate_confirmed","financial_facts_ready"}),"checks":checks,"warnings":[k for k,v in checks.items() if not v],"status":status}
