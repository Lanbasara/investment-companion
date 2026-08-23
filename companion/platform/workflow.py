from __future__ import annotations

import os
import socket
import uuid
from datetime import timedelta
from typing import Any
from zoneinfo import ZoneInfo

from ..db import row_dict, rows_dict
from ..foundation import CompanionError, canonical, digest, new_id
from ..timeutil import iso, next_interval, next_local_time, parse, utc_now


class WorkflowService:
    """Persistent Schedule and Run orchestration, independent of the facade."""

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
