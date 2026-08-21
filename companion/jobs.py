from __future__ import annotations

import math
import multiprocessing
import os
import resource
import sys
import time
from datetime import timedelta
from typing import Any, Callable

from .core import CompanionError, canonical, digest, new_id
from .db import row_dict, rows_dict
from .timeutil import iso, parse, utc_now


JobHandler = Callable[[dict[str, Any]], dict[str, Any]]


def _isolated_handler_entry(handler: JobHandler, context: dict[str, Any], budget: dict[str, Any], pipe) -> None:
    """Execute a trusted allow-listed handler inside a resource-bounded child.

    The handler registry remains the security boundary.  The child adds hard
    process limits and denies network/process-spawn audit events so a
    deterministic job cannot quietly turn into an Agent or data fetcher.
    """

    try:
        for key in list(os.environ):
            upper = key.upper()
            if any(marker in upper for marker in ("TOKEN", "SECRET", "PASSWORD", "API_KEY", "ACCESS_KEY", "PRIVATE_KEY", "CREDENTIAL", "AUTHORIZATION")):
                os.environ.pop(key, None)
        cpu_seconds = max(1, int(math.ceil(float(budget["max_cpu_seconds"]))))
        memory_bytes = int(budget["max_memory_mb"]) * 1024 * 1024
        resource.setrlimit(resource.RLIMIT_CPU, (cpu_seconds, cpu_seconds))
        resource.setrlimit(resource.RLIMIT_AS, (memory_bytes, memory_bytes))
        resource.setrlimit(resource.RLIMIT_NOFILE, (128, 128))

        network_profile = budget.get("network", "deny")

        def deny_nondeterminism(event: str, _args: tuple[Any, ...]) -> None:
            process_events = {
                "subprocess.Popen", "os.system", "os.posix_spawn", "os.posix_spawnp",
                "os.fork", "os.forkpty", "os.exec", "os.spawn", "pty.spawn",
            }
            network_events = {"socket.connect", "socket.bind"}
            if event in process_events or (network_profile == "deny" and event in network_events):
                raise PermissionError(f"deterministic job denied audit event: {event}")

        sys.addaudithook(deny_nondeterminism)
        result = handler(context)
        pipe.send(("ok", result))
    except BaseException as exc:  # child must serialize deterministic failure
        pipe.send(("error", f"{type(exc).__name__}: {exc}"))
    finally:
        pipe.close()


class JobEngine:
    """Typed, allow-listed deterministic work below the V2/V3 Run object.

    Handler names stored in SQLite are data, never commands.  A worker will
    execute a name only when the running program registered the exact name and
    version in this in-memory registry.
    """

    def __init__(self, companion):
        self.c = companion
        self.db = companion.db
        self._handlers: dict[str, tuple[str, JobHandler]] = {}

    FEATURE_REQUIREMENTS={
        "v4_jobs":["G0"],
        "v4_live_data_canary":["G0"],
        "v4_live_data":["G0","G1"],
        "v4_shadow":["G0","G1","G2","G3","G4"],
        "v4_decision_support_beta":["G0","G1","G2","G3","G4"],
        "v4_decision_support":["G0","G1","G2","G3","G4","G5"],
        "v5_operating_system":["G0"],
        "v6_predictive_recommendations":["G0"],
    }
    HANDLER_FEATURES={
        "data.tushare_ingest":"v4_live_data",
        "data.tushare_canary_bundle":"v4_live_data_canary",
        "data.publish_snapshot":"v4_live_data",
        "research.canary_market_scan":"v4_live_data_canary",
        "research.continuous_quant_review":"v4_live_data_canary",
        "research.v6_stock_forecast":"v6_predictive_recommendations",
        "research.v6_stock_candidate_scan":"v6_predictive_recommendations",
        "research.v6_stock_provisional_signal":"v6_predictive_recommendations",
        "research.v6_fund_forecast":"v6_predictive_recommendations",
        "research.v6_fund_candidate_scan":"v6_predictive_recommendations",
        "research.v6_fund_provisional_signal":"v6_predictive_recommendations",
        "research.v6_forecast_feedback":"v6_predictive_recommendations",
        "shadow.rebalance":"v4_shadow",
    }

    def register_handler(self, name: str, version: str, handler: JobHandler) -> None:
        if not name or any(ch not in "abcdefghijklmnopqrstuvwxyz0123456789._-" for ch in name):
            raise CompanionError("invalid deterministic handler name")
        current = self._handlers.get(name)
        if current and current[0] != version:
            raise CompanionError(f"handler already registered with another version: {name}")
        self._handlers[name] = (version, handler)

    def handlers(self) -> list[dict[str, str]]:
        return [
            {"name": name, "version": version}
            for name, (version, _) in sorted(self._handlers.items())
        ]

    def feature_get(self, key: str) -> dict[str, Any]:
        with self.db.connect() as con:
            item = row_dict(con.execute("SELECT * FROM feature_flags WHERE key=?", (key,)).fetchone())
        if not item:
            raise CompanionError(f"feature flag not found: {key}")
        item["enabled"] = bool(item["enabled"])
        return item

    def feature_list(self) -> list[dict[str, Any]]:
        with self.db.connect() as con:
            items = rows_dict(con.execute("SELECT * FROM feature_flags ORDER BY key").fetchall())
        for item in items:
            item["enabled"] = bool(item["enabled"])
        return items

    def feature_require(self,key:str)->dict[str,Any]:
        item=self.feature_get(key)
        if not item["enabled"]:raise CompanionError(f"{key} feature is disabled")
        if key=="v4_agent_research":raise CompanionError("v4_agent_research is not implemented and cannot be used")
        self.c.gates.require(self.FEATURE_REQUIREMENTS.get(key,[]))
        return item

    def decision_support_require(self)->dict[str,Any]:
        """Allow explicit beta use to generate G5 evidence without weakening full release."""

        full=self.feature_get("v4_decision_support")
        if full["enabled"]:
            return self.feature_require("v4_decision_support")
        beta=self.feature_get("v4_decision_support_beta")
        if beta["enabled"]:
            checked=self.feature_require("v4_decision_support_beta")
            config=checked["config"]
            if not isinstance(config.get("user_opt_in_ref"),str) or not config["user_opt_in_ref"].strip():
                raise CompanionError("V4 decision support beta lacks user opt-in")
            if not isinstance(config.get("expires_at"),str) or parse(config["expires_at"])<=utc_now():
                raise CompanionError("V4 decision support beta has expired")
            return checked
        raise CompanionError("V4 decision support is disabled (full and beta)")

    def feature_set(
        self,
        key: str,
        enabled: bool,
        config: dict[str, Any] | None = None,
        actor: str = "primary-codex",
        reason: str | None = None,
    ) -> dict[str, Any]:
        before = self.feature_get(key)
        if enabled:
            if key=="v4_agent_research":raise CompanionError("v4_agent_research is not implemented and cannot be enabled")
            self.c.gates.require(self.FEATURE_REQUIREMENTS.get(key,[]))
            if key=="v4_decision_support_beta":
                candidate=config if config is not None else before["config"]
                if not isinstance(candidate,dict) or not isinstance(candidate.get("user_opt_in_ref"),str) or not candidate["user_opt_in_ref"].strip():
                    raise CompanionError("v4_decision_support_beta requires config.user_opt_in_ref")
                if not isinstance(candidate.get("expires_at"),str) or parse(candidate["expires_at"])<=utc_now():
                    raise CompanionError("v4_decision_support_beta requires a future config.expires_at")
        now = iso()
        with self.db.transaction() as con:
            con.execute(
                "UPDATE feature_flags SET enabled=?,config_json=?,updated_at=? WHERE key=?",
                (1 if enabled else 0, canonical(config if config is not None else before["config"]), now, key),
            )
            self.c._audit(
                con,
                actor,
                "feature_enable" if enabled else "feature_disable",
                "feature_flag",
                key,
                before,
                {"enabled": enabled, "config": config if config is not None else before["config"]},
                reason,
            )
        return self.feature_get(key)

    def definition_create(
        self,
        *,
        name: str,
        handler: str,
        handler_version: str,
        input_schema: dict[str, Any] | None = None,
        output_schema: dict[str, Any] | None = None,
        resource_budget: dict[str, Any] | None = None,
        config: dict[str, Any] | None = None,
        status: str = "inactive",
        actor: str = "primary-codex",
    ) -> dict[str, Any]:
        if status not in {"inactive", "active"}:
            raise CompanionError("new job definition must be inactive or active")
        if status=="active":
            self.c.gates.require(["G0"])
            self._require_handler_feature(handler)
        self._require_registered(handler, handler_version)
        budget = {
            "max_wall_seconds": 300,
            "max_cpu_seconds": 240,
            "max_memory_mb": 1024,
            "max_input_bytes": 1_000_000,
            "max_output_bytes": 1_000_000,
            "lease_seconds": 600,
            "network": "deny",
            "model_tokens": 0,
        }
        budget.update(resource_budget or {})
        self._validate_budget(budget)
        cfg = config or {}
        steps = cfg.get("steps") or [
            {"name": "execute", "handler": handler, "handler_version": handler_version}
        ]
        self._validate_steps(steps)
        self._validate_network_profile(budget, steps)
        cfg = {**cfg, "steps": steps}
        did, now = new_id("jobdef"), iso()
        with self.db.transaction() as con:
            con.execute(
                "INSERT INTO job_definitions(id,name,handler,handler_version,status,input_schema_json,output_schema_json,resource_budget_json,config_json,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (
                    did,
                    name,
                    handler,
                    handler_version,
                    status,
                    canonical(input_schema or {}),
                    canonical(output_schema or {}),
                    canonical(budget),
                    canonical(cfg),
                    now,
                    now,
                ),
            )
            self.c._audit(con, actor, "create", "job_definition", did, after={"name": name, "handler": handler, "status": status})
        return self.definition_get(did)

    def definition_get(self, definition_id: str) -> dict[str, Any]:
        with self.db.connect() as con:
            item = row_dict(con.execute("SELECT * FROM job_definitions WHERE id=?", (definition_id,)).fetchone())
        if not item:
            raise CompanionError(f"job definition not found: {definition_id}")
        return item

    def definition_list(self, status: str | None = None) -> list[dict[str, Any]]:
        with self.db.connect() as con:
            if status:
                rows = con.execute(
                    "SELECT * FROM job_definitions WHERE status=? ORDER BY name", (status,)
                ).fetchall()
            else:
                rows = con.execute("SELECT * FROM job_definitions ORDER BY name").fetchall()
        return rows_dict(rows)

    def definition_for_handler(self, handler: str, status: str = "active") -> dict[str, Any] | None:
        with self.db.connect() as con:
            return row_dict(con.execute("SELECT * FROM job_definitions WHERE handler=? AND status=? ORDER BY created_at LIMIT 1",(handler,status)).fetchone())

    def definition_set_status(
        self,
        definition_id: str,
        status: str,
        actor: str = "primary-codex",
        reason: str | None = None,
    ) -> dict[str, Any]:
        if status not in {"inactive", "active", "paused", "archived"}:
            raise CompanionError("invalid job definition status")
        before = self.definition_get(definition_id)
        if status == "active":
            self.c.gates.require(["G0"])
            self._require_handler_feature(before["handler"])
            self._require_registered(before["handler"], before["handler_version"])
            self._validate_steps(before["config"].get("steps", []))
            self._validate_budget(before["resource_budget"])
            self._validate_network_profile(before["resource_budget"], before["config"].get("steps", []))
        with self.db.transaction() as con:
            con.execute(
                "UPDATE job_definitions SET status=?,updated_at=? WHERE id=?",
                (status, iso(), definition_id),
            )
            self.c._audit(con, actor, status, "job_definition", definition_id, before, {**before, "status": status}, reason)
        return self.definition_get(definition_id)

    def enqueue(
        self,
        parent_run_id: str,
        definition_id: str,
        inputs: dict[str, Any],
    ) -> dict[str, Any]:
        definition = self.definition_get(definition_id)
        if definition["status"] != "active":
            raise CompanionError("deterministic job definition is not active")
        self.feature_require("v4_jobs")
        self._require_handler_feature(definition["handler"])
        self._require_registered(definition["handler"], definition["handler_version"])
        self._validate_input_refs(inputs)
        _validate_schema(inputs,definition["input_schema"],"job inputs")
        key = f"parent-run:{parent_run_id}"
        jid, now = new_id("jobrun"), iso()
        steps = definition["config"].get("steps") or [
            {
                "name": "execute",
                "handler": definition["handler"],
                "handler_version": definition["handler_version"],
            }
        ]
        self._validate_steps(steps)
        with self.db.transaction() as con:
            con.execute(
                "INSERT OR IGNORE INTO job_runs(id,parent_run_id,job_definition_id,status,idempotency_key,inputs_json,created_at) VALUES(?,?,?,'queued',?,?,?)",
                (jid, parent_run_id, definition_id, key, canonical(inputs), now),
            )
            row = con.execute("SELECT * FROM job_runs WHERE idempotency_key=?", (key,)).fetchone()
            saved=row_dict(row)
            if not saved or saved["parent_run_id"]!=parent_run_id or saved["job_definition_id"]!=definition_id or canonical(saved["inputs"])!=canonical(inputs):
                raise CompanionError("job idempotency key is already bound to different immutable inputs")
            jid = saved["id"]
            for ordinal, step in enumerate(steps):
                sid = new_id("jobstep")
                step_key = f"{key}:{ordinal}:{step['name']}"
                con.execute(
                    "INSERT OR IGNORE INTO job_steps(id,job_run_id,name,ordinal,handler,handler_version,status,idempotency_key,input_refs_json,created_at) VALUES(?,?,?,?,?,?,'pending',?,?,?)",
                    (
                        sid,
                        jid,
                        step["name"],
                        ordinal,
                        step["handler"],
                        step["handler_version"],
                        step_key,
                        canonical(step.get("input_refs", [])),
                        now,
                    ),
                )
            con.execute(
                "UPDATE runs SET dispatch_type='deterministic_pipeline',job_run_id=? WHERE id=?",
                (jid, parent_run_id),
            )
        return self.run_get(jid)

    def enqueue_new_parent(
        self,
        *,
        definition_id: str,
        inputs: dict[str, Any],
        idempotency_key: str,
        kind: str = "maintenance",
    ) -> dict[str, Any]:
        """Atomically create an unscheduled parent Run and its deterministic child."""

        definition=self.definition_get(definition_id)
        if definition["status"]!="active":raise CompanionError("deterministic job definition is not active")
        self.feature_require("v4_jobs")
        self._require_handler_feature(definition["handler"])
        self._require_registered(definition["handler"],definition["handler_version"])
        self._validate_input_refs(inputs);_validate_schema(inputs,definition["input_schema"],"job inputs")
        steps=definition["config"].get("steps") or [{"name":"execute","handler":definition["handler"],"handler_version":definition["handler_version"]}]
        self._validate_steps(steps)
        now=iso();parent_id=new_id("run");jid=new_id("jobrun")
        with self.db.transaction() as con:
            con.execute("INSERT OR IGNORE INTO runs(id,schedule_id,kind,status,due_at,idempotency_key,payload_json,created_at,dispatch_type) VALUES(?,NULL,?,'queued',?,?,?,?, 'deterministic_pipeline')",(parent_id,kind,now,idempotency_key,canonical({"deterministic_inputs":inputs}),now))
            parent=row_dict(con.execute("SELECT * FROM runs WHERE idempotency_key=?",(idempotency_key,)).fetchone())
            expected_payload={"deterministic_inputs":inputs}
            if not parent or parent["kind"]!=kind or parent["dispatch_type"]!="deterministic_pipeline" or canonical(parent["payload"])!=canonical(expected_payload):
                raise CompanionError("parent Run idempotency key is already bound to different immutable inputs")
            parent_id=parent["id"]
            existing=row_dict(con.execute("SELECT * FROM job_runs WHERE parent_run_id=?",(parent_id,)).fetchone())
            if existing:
                if existing["job_definition_id"]!=definition_id or canonical(existing["inputs"])!=canonical(inputs):
                    raise CompanionError("parent Run is already bound to a different deterministic job")
                return self.run_get(existing["id"])
            child_key=f"parent-run:{parent_id}"
            con.execute("INSERT INTO job_runs(id,parent_run_id,job_definition_id,status,idempotency_key,inputs_json,created_at) VALUES(?,?,?,'queued',?,?,?)",(jid,parent_id,definition_id,child_key,canonical(inputs),now))
            for ordinal,step in enumerate(steps):
                con.execute("INSERT INTO job_steps(id,job_run_id,name,ordinal,handler,handler_version,status,idempotency_key,input_refs_json,created_at) VALUES(?,?,?,?,?,?,'pending',?,?,?)",(new_id("jobstep"),jid,step["name"],ordinal,step["handler"],step["handler_version"],f"{child_key}:{ordinal}:{step['name']}",canonical(step.get("input_refs",[])),now))
            con.execute("UPDATE runs SET job_run_id=? WHERE id=?",(jid,parent_id))
        return self.run_get(jid)

    def run_get(self, job_run_id: str) -> dict[str, Any]:
        with self.db.connect() as con:
            item = row_dict(con.execute("SELECT * FROM job_runs WHERE id=?", (job_run_id,)).fetchone())
            steps = rows_dict(
                con.execute(
                    "SELECT * FROM job_steps WHERE job_run_id=? ORDER BY ordinal", (job_run_id,)
                ).fetchall()
            )
        if not item:
            raise CompanionError(f"job run not found: {job_run_id}")
        item["steps"] = steps
        return item

    def run_list(self, status: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
        with self.db.connect() as con:
            if status:
                rows = con.execute(
                    "SELECT * FROM job_runs WHERE status=? ORDER BY created_at DESC LIMIT ?",
                    (status, limit),
                ).fetchall()
            else:
                rows = con.execute(
                    "SELECT * FROM job_runs ORDER BY created_at DESC LIMIT ?", (limit,)
                ).fetchall()
        return rows_dict(rows)

    def claim(self, owner: str, lease_seconds: int = 1800) -> dict[str, Any] | None:
        if lease_seconds <= 0:
            raise CompanionError("lease_seconds must be positive")
        now, until = iso(), iso(utc_now() + timedelta(seconds=lease_seconds))
        with self.db.transaction() as con:
            row = con.execute(
                "SELECT j.* FROM job_runs j JOIN runs r ON r.id=j.parent_run_id WHERE j.status IN ('queued','recoverable') AND r.status IN ('queued','recoverable') ORDER BY j.created_at LIMIT 1"
            ).fetchone()
            if not row:
                return None
            parent_changed=con.execute(
                "UPDATE runs SET status='leased',lease_owner=?,lease_until=?,attempt=attempt+1,started_at=COALESCE(started_at,?) WHERE id=? AND status IN ('queued','recoverable')",
                (owner,until,now,row["parent_run_id"]),
            ).rowcount
            changed = con.execute(
                "UPDATE job_runs SET status='leased',lease_owner=?,lease_until=?,attempt=attempt+1,started_at=COALESCE(started_at,?) WHERE id=? AND status IN ('queued','recoverable')",
                (owner, until, now, row["id"]),
            ).rowcount
            if changed != 1 or parent_changed!=1:
                raise CompanionError("atomic parent/job claim lost")
        return self.run_get(row["id"])

    def run_once(self, owner: str, lease_seconds: int = 1800) -> dict[str, Any] | None:
        self.feature_require("v4_jobs")
        item = self.claim(owner, lease_seconds)
        if not item:
            return None
        job_run_id = item["id"]
        parent_run_id = item["parent_run_id"]
        try:
            definition = self.definition_get(item["job_definition_id"])
            self._require_handler_feature(definition["handler"])
            budget = definition["resource_budget"]
            self._validate_budget(budget)
            runtime_steps=definition["config"].get("steps",[])
            self._validate_steps(runtime_steps)
            self._validate_network_profile(budget,runtime_steps)
            max_input_bytes = int(budget.get("max_input_bytes", 1_000_000))
            if len(canonical(item["inputs"]).encode("utf-8")) > max_input_bytes:
                return self._fail(job_run_id, parent_run_id, "resource budget exceeded: input bytes")
        except Exception as exc:
            return self._fail(job_run_id,parent_run_id,str(exc))
        try:
            self._validate_input_refs(item["inputs"])
            with self.db.transaction() as con:
                con.execute("UPDATE job_runs SET status='running' WHERE id=? AND status='leased'", (job_run_id,))
            last_result: dict[str, Any] = {}
            last_execution: dict[str, Any] | None = None
            steps = self.run_get(job_run_id)["steps"]
            for index, step in enumerate(steps):
                if step["status"] == "succeeded":
                    last_result = step.get("result", {})
                    continue
                execution = self._execute_step(item, step, owner, budget)
                last_result = execution["result"]
                if index < len(steps) - 1:
                    self._commit_step_success(step["id"], execution)
                else:
                    last_execution = execution
            manifest_id = last_result.get("manifest_id")
            if not manifest_id:
                raise CompanionError("deterministic handler did not publish a manifest")
            self.c.data.manifest_get(manifest_id)
            self._validate_output_refs(last_result.get("output_refs", []))
            _validate_schema(last_result,definition["output_schema"],"job output")
            prepared_completion = self.c.research.prepare_job_completion(
                job_run_id, last_result.get("experiment_completion")
            )
            now = iso()
            with self.db.transaction() as con:
                if last_execution is not None:
                    self._commit_step_success(last_execution["step_id"], last_execution, con=con)
                changed = con.execute(
                    "UPDATE job_runs SET status='succeeded',output_manifest_id=?,finished_at=?,lease_owner=NULL,lease_until=NULL,error=NULL WHERE id=? AND status='running'",
                    (manifest_id, now, job_run_id),
                ).rowcount
                if changed != 1:
                    raise CompanionError("job terminal transition lost")
                self.c.research.finalize_job_success(con, job_run_id, prepared_completion, now)
                parent = row_dict(con.execute("SELECT * FROM runs WHERE id=?", (parent_run_id,)).fetchone())
                if not parent or parent["status"] != "leased" or parent.get("job_run_id") != job_run_id:
                    raise CompanionError("parent Run is not atomically bound to the running JobRun")
                con.execute(
                    "UPDATE runs SET status='succeeded',finished_at=?,lease_owner=NULL,lease_until=NULL,error=NULL WHERE id=?",
                    (now, parent_run_id),
                )
                if parent.get("schedule_id"):
                    con.execute(
                        "UPDATE schedules SET last_success_at=?,last_error=NULL WHERE id=?",
                        (now, parent["schedule_id"]),
                    )
            delivery = self.c.delivery.require_for_run(parent_run_id)
            # A digest is a deferred user result, not an optional card.  Wake
            # Primary Codex so it freezes a ResultEnvelope before the close
            # summary batches it with other completed work.
            must_prepare_delivery = delivery["mode"] in {"digest_required", "report_required", "action_required"}
            if last_result.get("material") or must_prepare_delivery:
                event_kind = "delivery_result_required" if must_prepare_delivery else "deterministic_job_ready"
                event=self.c.event_create(kind=event_kind,occurred_at=now,summary=last_result.get("event_summary","Deterministic research artifact ready for Primary review"),payload={"job_run_id":job_run_id,"manifest_id":manifest_id,"research_only":True,"delivery_id":delivery["id"],"delivery_mode":delivery["mode"]})
                delivery_message = (
                    f"\nDelivery ID: {delivery['id']}\n本任务属于 {delivery['mode']}；请先核验研究产物，再调用 delivery_prepare 形成用户可见的结论、依据与下一步。"
                    if must_prepare_delivery else ""
                )
                self.c.outbox_enqueue(
                    kind="codex_turn",
                    destination="investment-companion",
                    event_id=event["id"],
                    payload={
                        "schema":"investment-companion.wake-envelope/v1",
                        "envelope_type":"research_ready",
                        "job_run_id":job_run_id,
                        "manifest_id":manifest_id,
                        "message":f"[Investment Companion research ready/v1]\nJob Run: {job_run_id}\nManifest: {manifest_id}\n这是待 Primary Codex 审阅的研究产物，不是用户行动建议。请核验 Gate、证据与反证后决定是否静默、继续研究或形成正式 Decision。"+delivery_message,
                    },
                    idempotency_key=f"job-ready:{job_run_id}:{delivery['id'] if must_prepare_delivery else 'material'}",
                )
            return self.run_get(job_run_id)
        except Exception as exc:
            return self._fail(job_run_id, parent_run_id, str(exc))

    def cancel(self, job_run_id: str, reason: str) -> dict[str, Any]:
        item = self.run_get(job_run_id)
        if item["status"] in {"succeeded", "failed", "cancelled"}:
            raise CompanionError(f"job run already terminal: {item['status']}")
        now = iso()
        with self.db.transaction() as con:
            con.execute(
                "UPDATE job_runs SET status='cancelled',finished_at=?,error=?,lease_owner=NULL,lease_until=NULL WHERE id=?",
                (now, reason, job_run_id),
            )
            con.execute(
                "UPDATE job_steps SET status='cancelled',finished_at=?,error=?,lease_owner=NULL,lease_until=NULL WHERE job_run_id=? AND status NOT IN ('succeeded','failed','cancelled')",
                (now, reason, job_run_id),
            )
        parent = self.c.run_get(item["parent_run_id"])
        if parent["status"] not in {"succeeded", "failed", "cancelled"}:
            self.c.run_cancel(parent["id"], reason)
        return self.run_get(job_run_id)

    def recover(self) -> dict[str, int]:
        now = iso()
        with self.db.transaction() as con:
            steps = con.execute(
                "UPDATE job_steps SET status='recoverable',lease_owner=NULL,lease_until=NULL WHERE status IN ('leased','running') AND lease_until IS NOT NULL AND lease_until<=?",
                (now,),
            ).rowcount
            runs = con.execute(
                "UPDATE job_runs SET status='recoverable',lease_owner=NULL,lease_until=NULL WHERE status IN ('leased','running') AND lease_until IS NOT NULL AND lease_until<=?",
                (now,),
            ).rowcount
        return {"job_runs": runs, "job_steps": steps}

    def _execute_step(
        self,
        job_run: dict[str, Any],
        step: dict[str, Any],
        owner: str,
        budget: dict[str, Any],
    ) -> dict[str, Any]:
        version, handler = self._require_registered(step["handler"], step["handler_version"])
        now = iso()
        lease_until = iso(utc_now() + timedelta(seconds=int(budget.get("lease_seconds", 1800))))
        with self.db.transaction() as con:
            changed = con.execute(
                "UPDATE job_steps SET status='running',lease_owner=?,lease_until=?,attempt=attempt+1,started_at=COALESCE(started_at,?) WHERE id=? AND status IN ('pending','recoverable','leased')",
                (owner, lease_until, now, step["id"]),
            ).rowcount
            if changed != 1:
                raise CompanionError(f"job step is not runnable: {step['id']}")
        started = time.monotonic()
        context = {
            "job_run_id": job_run["id"],
            "parent_run_id": job_run["parent_run_id"],
            "step": step["name"],
            "handler": step["handler"],
            "handler_version": version,
            "inputs": job_run["inputs"],
            "prior_outputs": self._prior_outputs(job_run["id"], step["ordinal"]),
        }
        process_context = multiprocessing.get_context("fork")
        parent_pipe, child_pipe = process_context.Pipe(duplex=False)
        process = process_context.Process(
            target=_isolated_handler_entry,
            args=(handler, context, budget, child_pipe),
            name=f"companion-job-{step['id']}",
        )
        process.start()
        child_pipe.close()
        max_wall = int(budget["max_wall_seconds"])
        heartbeat_at = time.monotonic() + min(30, max(1, int(budget["lease_seconds"]) // 3))
        message = None
        while process.is_alive() and time.monotonic() - started <= max_wall:
            process.join(timeout=0.25)
            if parent_pipe.poll():
                message = parent_pipe.recv()
                break
            if time.monotonic() >= heartbeat_at:
                self._heartbeat(job_run["id"], job_run["parent_run_id"], step["id"], owner, int(budget["lease_seconds"]))
                heartbeat_at = time.monotonic() + min(30, max(1, int(budget["lease_seconds"]) // 3))
        if message is not None:
            process.join(timeout=2)
        if process.is_alive():
            process.terminate()
            process.join(timeout=2)
            if process.is_alive():
                process.kill()
                process.join(timeout=2)
            parent_pipe.close()
            raise CompanionError("resource budget exceeded: hard wall timeout")
        if message is None and parent_pipe.poll():
            message = parent_pipe.recv()
        parent_pipe.close()
        if not message:
            raise CompanionError(f"isolated handler exited without result (exit={process.exitcode})")
        state, payload = message
        if state != "ok":
            raise CompanionError(str(payload))
        result = payload
        if not isinstance(result, dict):
            raise CompanionError("deterministic handler result must be an object")
        if len(canonical(result).encode("utf-8"))>int(budget["max_output_bytes"]):
            raise CompanionError("resource budget exceeded: output bytes")
        duration_ms = int((time.monotonic() - started) * 1000)
        if duration_ms > max_wall * 1000:
            raise CompanionError("resource budget exceeded: wall time")
        output_refs = result.get("output_refs", [])
        manifest_id = result.get("manifest_id")
        if manifest_id and manifest_id not in output_refs:
            output_refs = [*output_refs, manifest_id]
        if int(result.get("model_tokens", 0)) != 0:
            raise CompanionError("deterministic handler reported model token use")
        return {
            "step_id": step["id"],
            "result": result,
            "output_refs": output_refs,
            "duration_ms": duration_ms,
            "resource_usage": {"wall_ms": duration_ms, "model_tokens": 0, "isolated_process": True},
        }

    def _commit_step_success(self, step_id: str, execution: dict[str, Any], *, con=None) -> None:
        def apply(connection) -> None:
            changed = connection.execute(
                "UPDATE job_steps SET status='succeeded',output_refs_json=?,result_json=?,finished_at=?,duration_ms=?,resource_usage_json=?,lease_owner=NULL,lease_until=NULL,error=NULL WHERE id=? AND status='running'",
                (
                    canonical(execution["output_refs"]),
                    canonical(execution["result"]),
                    iso(),
                    execution["duration_ms"],
                    canonical(execution["resource_usage"]),
                    step_id,
                ),
            ).rowcount
            if changed != 1:
                raise CompanionError(f"job step terminal transition lost: {step_id}")
        if con is not None:
            apply(con)
        else:
            with self.db.transaction() as transaction:
                apply(transaction)

    def _heartbeat(self, job_run_id: str, parent_run_id: str, step_id: str, owner: str, lease_seconds: int) -> None:
        until = iso(utc_now() + timedelta(seconds=lease_seconds))
        with self.db.transaction() as con:
            for table, identifier in (("job_steps", step_id), ("job_runs", job_run_id), ("runs", parent_run_id)):
                changed = con.execute(
                    f"UPDATE {table} SET lease_until=? WHERE id=? AND lease_owner=? AND status IN ('leased','running')",
                    (until, identifier, owner),
                ).rowcount
                if changed != 1:
                    raise CompanionError(f"lease heartbeat lost for {table}:{identifier}")

    def _fail(self, job_run_id: str, parent_run_id: str, error: str) -> dict[str, Any]:
        now = iso()
        with self.db.transaction() as con:
            con.execute(
                "UPDATE job_steps SET status='failed',finished_at=?,error=?,lease_owner=NULL,lease_until=NULL WHERE job_run_id=? AND status='running'",
                (now, error, job_run_id),
            )
            con.execute(
                "UPDATE job_runs SET status='failed',finished_at=?,error=?,lease_owner=NULL,lease_until=NULL WHERE id=? AND status NOT IN ('succeeded','cancelled')",
                (now, error, job_run_id),
            )
            self.c.research.fail_bound_job(con, job_run_id, error, now)
            parent = row_dict(con.execute("SELECT * FROM runs WHERE id=?", (parent_run_id,)).fetchone())
            if parent and parent["status"] not in {"succeeded", "failed", "cancelled"}:
                con.execute(
                    "UPDATE runs SET status='failed',finished_at=?,error=?,lease_owner=NULL,lease_until=NULL WHERE id=?",
                    (now, error, parent_run_id),
                )
                if parent.get("schedule_id"):
                    con.execute("UPDATE schedules SET last_error=? WHERE id=?", (error, parent["schedule_id"]))
        return self.run_get(job_run_id)

    @staticmethod
    def _validate_budget(budget: dict[str, Any]) -> None:
        required = {"max_wall_seconds", "max_cpu_seconds", "max_memory_mb", "max_input_bytes", "max_output_bytes", "lease_seconds", "network", "model_tokens"}
        missing = required - set(budget)
        if missing:
            raise CompanionError(f"resource budget missing: {sorted(missing)}")
        for key in ("max_wall_seconds", "max_cpu_seconds", "max_memory_mb", "max_input_bytes", "max_output_bytes", "lease_seconds"):
            if isinstance(budget[key], bool) or int(budget[key]) <= 0:
                raise CompanionError(f"resource budget {key} must be positive")
        if budget["network"] not in {"deny","tushare_official"} or int(budget["model_tokens"]) != 0:
            raise CompanionError("deterministic jobs require an allow-listed network profile and model_tokens=0")

    @staticmethod
    def _validate_network_profile(budget:dict[str,Any],steps:list[dict[str,Any]])->None:
        profile=budget.get("network","deny")
        if profile=="deny":return
        allowed={"data.tushare_ingest","data.tushare_canary_bundle","data.v6_fund_canary_bundle"}
        if profile!="tushare_official" or not steps or any(step.get("handler") not in allowed for step in steps):
            raise CompanionError("network access is restricted to the allow-listed Tushare data handlers")

    def _validate_output_refs(self, refs: list[Any]) -> None:
        if not isinstance(refs, list) or not refs:
            raise CompanionError("deterministic handler requires non-empty output_refs")
        for reference in refs:
            if not isinstance(reference, str):
                raise CompanionError("job output refs must be string identifiers")
            if reference.startswith("manifest_"):
                self.c.data.manifest_get(reference, verify=True)
            elif reference.startswith("dataobj_"):
                self.c.data.object_get(reference, verify=True)
            else:
                self.c.data.snapshot_get(reference, verify=True)

    def _require_registered(self, name: str, version: str) -> tuple[str, JobHandler]:
        registered = self._handlers.get(name)
        if not registered:
            raise CompanionError(f"deterministic handler is not allow-listed: {name}")
        if registered[0] != version:
            raise CompanionError(
                f"handler version mismatch for {name}: definition={version}, runtime={registered[0]}"
            )
        return registered

    def _require_handler_feature(self,handler:str)->None:
        feature=self.HANDLER_FEATURES.get(handler)
        if feature:self.feature_require(feature)

    def _validate_steps(self, steps: list[dict[str, Any]]) -> None:
        if not steps:
            raise CompanionError("deterministic job requires at least one step")
        names: set[str] = set()
        for step in steps:
            required = {"name", "handler", "handler_version"}
            if required - set(step):
                raise CompanionError("job step is missing name/handler/handler_version")
            if step["name"] in names:
                raise CompanionError("job step names must be unique")
            names.add(step["name"])
            self._require_registered(step["handler"], step["handler_version"])

    def _validate_input_refs(self, inputs: dict[str, Any]) -> None:
        refs = inputs.get("refs", [])
        if not isinstance(refs, list):
            raise CompanionError("job input refs must be a list")
        for ref in refs:
            if not isinstance(ref, dict) or set(ref) != {"type", "id"}:raise CompanionError("each job input ref must contain only type and id")
            if ref["type"] == "data_object":self.c.data.object_get(ref["id"],verify=True)
            elif ref["type"] == "dataset_snapshot":self.c.data.snapshot_get(ref["id"],verify=True)
            elif ref["type"] == "artifact_manifest":self.c.data.manifest_get(ref["id"],verify=True)
            else:raise CompanionError(f"unsupported immutable input ref type: {ref['type']}")

    def _prior_outputs(self, job_run_id: str, ordinal: int) -> list[str]:
        with self.db.connect() as con:
            rows = con.execute(
                "SELECT output_refs_json FROM job_steps WHERE job_run_id=? AND ordinal<? AND status='succeeded' ORDER BY ordinal",
                (job_run_id, ordinal),
            ).fetchall()
        result: list[str] = []
        import json

        for row in rows:
            result.extend(json.loads(row[0]))
        return result


def _validate_schema(value:Any,schema:dict[str,Any],path:str)->None:
    if not schema:return
    expected=schema.get("type")
    checks={"object":dict,"array":list,"string":str,"integer":int,"number":(int,float),"boolean":bool,"null":type(None)}
    if expected in checks and (not isinstance(value,checks[expected]) or expected in {"integer","number"} and isinstance(value,bool)):
        raise CompanionError(f"{path} must be {expected}")
    if "enum" in schema and value not in schema["enum"]:raise CompanionError(f"{path} is not in enum")
    if expected=="object":
        required=set(schema.get("required",[]));missing=required-set(value)
        if missing:raise CompanionError(f"{path} missing required fields: {sorted(missing)}")
        properties=schema.get("properties",{})
        if schema.get("additionalProperties") is False:
            extra=set(value)-set(properties)
            if extra:raise CompanionError(f"{path} has unsupported fields: {sorted(extra)}")
        for key,subschema in properties.items():
            if key in value:_validate_schema(value[key],subschema,f"{path}.{key}")
    if expected=="array" and "items" in schema:
        for index,item in enumerate(value):_validate_schema(item,schema["items"],f"{path}[{index}]")
