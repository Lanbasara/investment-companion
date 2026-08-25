from __future__ import annotations

from datetime import time
from decimal import Decimal
from typing import Any

from .db import row_dict, rows_dict
from .financial import dec, dtext, reconciliation_is_full_match
from .foundation import CompanionError, canonical, digest, new_id
from .timeutil import iso, parse, utc_now


PLAN_TYPES = {"priced_buy", "priced_sell", "bracket_exit", "moving_grid"}
PLAN_STATUSES = {"draft", "presented", "accepted", "configured", "active", "sleeping", "termination_pending", "terminated", "reconciled", "exception", "expired", "cancelled"}
VALIDITY_SESSIONS = {5, 20, 60, 180}
ORDER_STATUSES = {"triggered", "submitted", "partially_filled", "filled", "cancelled", "rejected", "unknown"}
ORDER_TRANSITIONS = {
    "triggered": {"triggered", "submitted", "partially_filled", "filled", "cancelled", "rejected", "unknown"},
    "submitted": {"submitted", "partially_filled", "filled", "cancelled", "rejected", "unknown"},
    "partially_filled": {"partially_filled", "filled", "cancelled", "unknown"},
    "unknown": ORDER_STATUSES,
    "filled": {"filled"}, "cancelled": {"cancelled"}, "rejected": {"rejected"},
}
CICC_SEMANTICS_VERSION = "cicc-wealth-condition-orders/2026-08-25"


class BrokerExecutionStrategyService:
    """Model user-configured CICC condition orders below the existing Execution domain.

    The service never connects to the broker and never treats a configured
    condition order, trigger or fill as fact without a user/broker report.
    """

    def __init__(self, companion):self.c = companion

    def create_from_queue(self, *, queue_id: str, plan_type: str, spec: dict[str, Any], valid_until: str, idempotency_key: str, actor: str = "primary-codex") -> dict[str, Any]:
        if plan_type not in PLAN_TYPES:raise CompanionError("unsupported broker execution plan type")
        queue = self.c.operating.queue_get(queue_id)
        if queue["state"] != "accepted":raise CompanionError("broker execution plan requires an accepted Action Card")
        revision = self.c.cognition.revision_get(queue["decision_revision_id"])
        program = self.c.operating.program_get(queue["program_id"])
        normalized = self._normalize_spec(plan_type, spec)
        if parse(valid_until) <= utc_now():raise CompanionError("broker execution plan valid_until must be in the future")
        if iso(parse(valid_until))!=revision.get("metadata",{}).get("valid_until"):raise CompanionError("broker execution plan deadline must equal the Decision valid_until and the broker-displayed deadline")
        if (parse(valid_until).date()-utc_now().date()).days<normalized["validity_sessions"]-1:raise CompanionError("broker-displayed deadline cannot contain the selected number of trading sessions")
        action = self.c.operating.queue_card(queue_id)["action"]
        if normalized["account_id"] != action["account_id"] or normalized["asset_id"] != action["asset_id"]:
            raise CompanionError("broker execution plan account and asset must match its Action Card")
        authorization=revision.get("metadata",{}).get("execution_plan")
        if not authorization or authorization.get("plan_type")!=plan_type or canonical(authorization.get("spec"))!=canonical(normalized):
            raise CompanionError("Decision does not authorize this exact broker execution plan")
        key = self._text(idempotency_key, "idempotency_key")
        with self.c.db.connect() as con:existing=row_dict(con.execute("SELECT * FROM broker_execution_plans WHERE idempotency_key=?",(key,)).fetchone())
        if existing:
            if existing["content_hash"] != digest(plan_type, normalized, valid_until, queue_id):raise CompanionError("idempotency_key belongs to another broker execution plan")
            return existing
        now=iso();pid=new_id("brokerplan");content_hash=digest(plan_type,normalized,valid_until,queue_id)
        with self.c.db.transaction() as con:
            con.execute("INSERT INTO broker_execution_plans(id,program_id,queue_id,decision_revision_id,broker,plan_type,account_id,asset_id,spec_json,semantics_version,status,valid_until,content_hash,idempotency_key,created_at,updated_at) VALUES(?,?,?,?,? ,?,?,?,?,?,'draft',?,?,?,?,?)",(pid,program["id"],queue_id,revision["id"],"cicc_wealth",plan_type,normalized["account_id"],normalized["asset_id"],canonical(normalized),CICC_SEMANTICS_VERSION,iso(parse(valid_until)),content_hash,key,now,now))
            if plan_type=="moving_grid":con.execute("UPDATE broker_execution_plans SET current_reference_price_text=? WHERE id=?",(dtext(dec(normalized["initial_reference_price"])),pid))
            self.c.audit.record(con,actor,"create","broker_execution_plan",pid,after={"plan_type":plan_type,"status":"draft","semantics_version":CICC_SEMANTICS_VERSION},reason="accepted Action Card translated into a human-configured broker strategy")
        return self.get(pid)

    def get(self, plan_id: str) -> dict[str, Any]:
        with self.c.db.connect() as con:item=row_dict(con.execute("SELECT * FROM broker_execution_plans WHERE id=?",(plan_id,)).fetchone())
        if not item:raise CompanionError(f"broker execution plan not found: {plan_id}")
        item["orders"]=self.orders(plan_id);item["events"]=self.events(plan_id);item["net_quantities"]=self.net_quantities(plan_id)
        return item

    def list(self, *, status: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
        if status and status not in PLAN_STATUSES:raise CompanionError("unsupported broker execution plan status")
        if limit < 1 or limit > 500:raise CompanionError("plan limit must be within 1..500")
        query="SELECT * FROM broker_execution_plans";params=[]
        if status:query+=" WHERE status=?";params.append(status)
        query+=" ORDER BY updated_at DESC,id DESC LIMIT ?";params.append(limit)
        with self.c.db.connect() as con:return rows_dict(con.execute(query,params).fetchall())

    def expire_due(self,*,actor:str="system") -> list[str]:
        now=iso()
        with self.c.db.connect() as con:ids=[row[0] for row in con.execute("SELECT id FROM broker_execution_plans WHERE valid_until<=? AND status IN ('draft','presented','accepted','configured','active','sleeping')",(now,)).fetchall()]
        for plan_id in ids:
            with self.c.db.transaction() as con:
                con.execute("UPDATE broker_execution_plans SET status='expired',status_reason='broker strategy validity elapsed',updated_at=? WHERE id=?",(now,plan_id))
                self._event_in_tx(con,plan_id,"exception",now,{"reason":"broker strategy validity elapsed","terminal_status":"expired"},f"expired:{plan_id}:{now}",actor)
        return ids

    def recover_execution_links(self,*,actor:str="system-recovery") -> list[str]:
        with self.c.db.connect() as con:orders=rows_dict(con.execute("SELECT * FROM broker_managed_orders WHERE execution_link_state='pending' OR (status<>'rejected' AND execution_id IS NULL)").fetchall())
        recovered=[]
        for order in orders:
            plan=self.get(order["plan_id"])
            execution=self._create_order_execution(plan=plan,order_id=order["id"],broker_order_ref=order["broker_order_ref"],side=order["side"],quantity=dec(order["quantity_text"]),ordered_at=order["triggered_at"])
            with self.c.db.transaction() as con:
                con.execute("UPDATE broker_managed_orders SET execution_id=?,execution_link_state='linked',updated_at=? WHERE id=?",(execution["id"],iso(),order["id"]))
                self.c.audit.record(con,actor,"recover_execution_link","broker_managed_order",order["id"],after={"execution_id":execution["id"]},reason="recovered interrupted broker-order to Execution linkage")
            recovered.append(order["id"])
        return recovered

    def mark_configured(self, *, plan_id: str, broker_condition_ref: str, configured_at: str, actor: str = "primary-codex") -> dict[str, Any]:
        plan=self.get(plan_id)
        self._require_not_expired(plan)
        self._require_authorization_current(plan)
        if plan["status"] not in {"draft","presented","accepted","configured"}:raise CompanionError(f"plan cannot be configured from {plan['status']}")
        occurred=iso(parse(configured_at));reference=self._text(broker_condition_ref,"broker_condition_ref")
        with self.c.db.transaction() as con:
            con.execute("UPDATE broker_execution_plans SET status='configured',broker_condition_ref=?,configured_at=?,updated_at=? WHERE id=?",(reference,occurred,iso(),plan_id))
            self._event_in_tx(con,plan_id,"configured",occurred,{"broker_condition_ref":reference},f"configured:{plan_id}:{reference}",actor)
        return self.get(plan_id)

    def activate(self, *, plan_id: str, occurred_at: str, actor: str = "primary-codex") -> dict[str, Any]:
        plan=self.get(plan_id)
        self._require_not_expired(plan)
        self._require_authorization_current(plan)
        if plan["status"] not in {"configured","sleeping","active"}:raise CompanionError(f"plan cannot activate from {plan['status']}")
        occurred=iso(parse(occurred_at))
        with self.c.db.transaction() as con:
            con.execute("UPDATE broker_execution_plans SET status='active',updated_at=? WHERE id=?",(iso(),plan_id))
            self._event_in_tx(con,plan_id,"activated" if plan["status"]!="sleeping" else "sleep_exited",occurred,{},f"activate:{plan_id}:{occurred}",actor)
        return self.get(plan_id)

    def report_order(self, *, plan_id: str, broker_order_ref: str, side: str, quantity: Any, status: str, triggered_at: str, trigger_price: Any | None = None, reference_price_before: Any | None = None, reference_price_after: Any | None = None, rejection_reason: str | None = None, cancelled_quantity: Any | None = None, condition_leg: str | None = None, actor: str = "primary-codex") -> dict[str, Any]:
        plan=self.get(plan_id)
        if plan["status"] not in {"active","sleeping","termination_pending","terminated","expired"}:raise CompanionError(f"plan cannot receive broker orders from {plan['status']}")
        if side not in {"buy","sell"} or status not in ORDER_STATUSES:raise CompanionError("invalid broker managed order side or status")
        expected_side={"priced_buy":"buy","priced_sell":"sell","bracket_exit":"sell"}.get(plan["plan_type"])
        if expected_side and side!=expected_side:raise CompanionError(f"{plan['plan_type']} can report only {expected_side} orders")
        if plan["plan_type"]=="bracket_exit" and condition_leg not in {"take_profit","stop_loss"}:raise CompanionError("bracket_exit order report requires take_profit or stop_loss condition_leg")
        if plan["plan_type"]!="bracket_exit" and condition_leg is not None:raise CompanionError("condition_leg is supported only for bracket_exit")
        qty=dec(quantity,"order quantity")
        if qty <= 0 or qty != qty.to_integral_value():raise CompanionError("order quantity must be a positive whole number")
        cancelled=dec(cancelled_quantity or "0","cancelled_quantity")
        if cancelled<0 or cancelled>qty or cancelled!=cancelled.to_integral_value():raise CompanionError("cancelled_quantity must be a whole number within order quantity")
        if status=="cancelled" and cancelled_quantity is None:raise CompanionError("cancelled order requires cancelled_quantity")
        if status=="rejected" and cancelled:raise CompanionError("rejected order cannot have cancelled quantity")
        if status=="filled" and cancelled:raise CompanionError("filled order cannot have cancelled quantity")
        executed=Decimal("0") if status=="rejected" else qty
        reference=self._text(broker_order_ref,"broker_order_ref");occurred=iso(parse(triggered_at))
        with self.c.db.connect() as con:existing=row_dict(con.execute("SELECT * FROM broker_managed_orders WHERE plan_id=? AND broker_order_ref=?",(plan_id,reference)).fetchone())
        if not existing and parse(occurred)>parse(plan["valid_until"]):raise CompanionError("new broker strategy trigger occurred after the condition-order deadline")
        before=self._optional_positive(reference_price_before,"reference_price_before");after=self._optional_positive(reference_price_after,"reference_price_after")
        update_reason=None
        if plan["plan_type"]=="moving_grid" and not existing:
            current=self._current_grid_reference(plan)
            if before is None or dec(before,"reference_price_before")!=current:raise CompanionError("grid reference_price_before must equal the current broker reference")
            no_update=status=="rejected" and rejection_reason in {"insufficient_cash","insufficient_holdings"}
            if no_update and after is not None:raise CompanionError("grid reference must not update for insufficient cash or holdings rejection")
            if not no_update and after is None:raise CompanionError("grid reference update must be reported for every other trigger outcome")
            if after is not None and ((side=="sell" and dec(after,"reference_price_after")<=current) or (side=="buy" and dec(after,"reference_price_after")>=current)):raise CompanionError("grid reference must move in the trigger direction")
            update_reason="unchanged_insufficient_resources" if no_update else "broker_trigger_driven_update"
        oid=new_id("brokerorder");now=iso();payload={"broker_order_ref":reference,"side":side,"quantity":dtext(qty),"status":status,"rejection_reason":rejection_reason,"reference_update_reason":update_reason,"condition_leg":condition_leg}
        with self.c.db.transaction() as con:
            if existing:
                if existing["side"]!=side or dec(existing["quantity_text"],"existing quantity")!=qty:raise CompanionError("broker_order_ref identity changed")
                if status not in ORDER_TRANSITIONS[existing["status"]]:raise CompanionError("broker managed order status cannot regress or leave a terminal state")
                if cancelled<dec(existing["cancelled_quantity_text"],"existing cancelled quantity"):raise CompanionError("cancelled_quantity cannot decrease")
                con.execute("UPDATE broker_managed_orders SET status=?,submitted_quantity_text=?,cancelled_quantity_text=?,updated_at=? WHERE id=?",(status,dtext(executed),dtext(cancelled),now,existing["id"]))
                oid=existing["id"]
            else:
                link_state="not_applicable" if status=="rejected" else "pending"
                con.execute("INSERT INTO broker_managed_orders(id,plan_id,broker_order_ref,condition_leg,execution_link_state,side,status,quantity_text,submitted_quantity_text,cancelled_quantity_text,trigger_price_text,reference_price_before_text,reference_price_after_text,reference_update_reason,triggered_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",(oid,plan_id,reference,condition_leg,link_state,side,status,dtext(qty),dtext(executed),dtext(cancelled),dtext(dec(trigger_price,"trigger_price")) if trigger_price is not None else None,before,after,update_reason,occurred,now))
                if plan["plan_type"]=="moving_grid" and after is not None:
                    changed=con.execute("UPDATE broker_execution_plans SET current_reference_price_text=?,updated_at=? WHERE id=? AND current_reference_price_text=?",(after,now,plan_id,before)).rowcount
                    if changed!=1:raise CompanionError("grid reference changed concurrently; reload before reporting the trigger")
            self._event_in_tx(con,plan_id,"order_status" if existing else "triggered",occurred,payload,f"order:{plan_id}:{reference}:{status}:{dtext(cancelled)}",actor,order_id=oid)
            if not existing and after is not None:self._event_in_tx(con,plan_id,"reference_updated",occurred,{"before":before,"after":after,"reason":update_reason},f"reference:{plan_id}:{reference}",actor,order_id=oid)
        execution_id=existing.get("execution_id") if existing else None
        if not execution_id and status!="rejected":
            execution=self._create_order_execution(plan=plan,order_id=oid,broker_order_ref=reference,side=side,quantity=qty,ordered_at=occurred)
            execution_id=execution["id"]
            with self.c.db.transaction() as con:con.execute("UPDATE broker_managed_orders SET execution_id=?,execution_link_state='linked',updated_at=? WHERE id=?",(execution_id,iso(),oid))
        if execution_id and status=="cancelled" and cancelled==qty:
            execution=self.c.execution.get(execution_id)
            if execution["status"] not in {"cancelled","filled","deviated"}:self.c.execution.cancel(execution_id=execution_id,reason="broker reported the entire strategy order cancelled",actor=actor)
        result=self.get(plan_id);result["reported_order_execution_id"]=execution_id;return result

    def set_sleeping(self, *, plan_id:str, direction:str, sleeping:bool, occurred_at:str, reason:str, actor:str="primary-codex") -> dict[str,Any]:
        if not isinstance(sleeping,bool):raise CompanionError("sleeping must be boolean")
        plan=self.get(plan_id);occurred=iso(parse(occurred_at));explanation=self._text(reason,"sleep reason")
        if plan["plan_type"]!="moving_grid" or direction not in {"buy","sell"}:raise CompanionError("directional sleeping requires moving_grid and buy or sell direction")
        if plan["status"] not in {"active","sleeping"}:raise CompanionError("only an active grid can change directional sleep")
        field=f"{direction}_direction_state";before=plan[field];after="sleeping" if sleeping else "active"
        if before==after:return plan
        with self.c.db.transaction() as con:
            con.execute(f"UPDATE broker_execution_plans SET {field}=?,updated_at=? WHERE id=?",(after,iso(),plan_id))
            self._event_in_tx(con,plan_id,"sleep_entered" if sleeping else "sleep_exited",occurred,{"direction":direction,"reason":explanation},f"sleep:{plan_id}:{direction}:{sleeping}:{occurred}",actor)
        return self.get(plan_id)

    def report_exception(self, *, plan_id:str, occurred_at:str, reason:str, actor:str="primary-codex") -> dict[str,Any]:
        plan=self.get(plan_id)
        if plan["status"] in {"reconciled","cancelled","expired"}:raise CompanionError(f"plan cannot enter exception from {plan['status']}")
        occurred=iso(parse(occurred_at));explanation=self._text(reason,"exception reason");self._set_status(plan_id,"exception",reason=explanation)
        self._event(plan_id,"exception",occurred,{"reason":explanation},f"exception:{plan_id}:{occurred}",actor);return self.get(plan_id)

    def reconcile(self, *, plan_id:str, occurred_at:str, reconciliation_id:str, actor:str="primary-codex") -> dict[str,Any]:
        plan=self.get(plan_id)
        if plan["status"]!="terminated":raise CompanionError("only a terminated broker strategy can be reconciled")
        live=[item for item in plan["orders"] if item["status"] in {"triggered","submitted","partially_filled","unknown"}]
        if live:raise CompanionError("terminated strategy still has live or unknown broker orders")
        if any(item["status"]!="rejected" and (item.get("execution_link_state")!="linked" or not item.get("execution_id")) for item in plan["orders"]):raise CompanionError("strategy has broker orders whose Execution linkage is incomplete")
        occurred=iso(parse(occurred_at));reference=self._text(reconciliation_id,"reconciliation_id")
        with self.c.db.connect() as con:reconciliation=row_dict(con.execute("SELECT * FROM reconciliations WHERE id=?",(reference,)).fetchone())
        if not reconciliation or reconciliation["account_id"]!=plan["account_id"] or not reconciliation_is_full_match(reconciliation):raise CompanionError("strategy reconciliation requires a full-scope matched reconciliation for the same account")
        if not reconciliation.get("confirmed_ledger_hash") or reconciliation["confirmed_ledger_hash"]!=self.c.financial.confirmed_ledger_hash():raise CompanionError("strategy reconciliation does not match the current confirmed Ledger")
        latest=max([parse(plan["terminated_at"]),*(parse(item["updated_at"]) for item in plan["orders"])])
        if parse(reconciliation["as_of"])<latest or parse(occurred)<parse(reconciliation["as_of"]):raise CompanionError("strategy reconciliation must cover termination and every broker order")
        latest_confirmation=None
        for order in plan["orders"]:
            remaining=dec(order["quantity_text"])-dec(order["cancelled_quantity_text"])
            if order["status"] in {"filled","cancelled"} and remaining>0:
                execution=self.c.execution.get(order["execution_id"])
                entries=[self.c.financial.ledger_get(entry_id) for entry_id in execution["ledger_entry_ids"]]
                if execution["status"] not in {"filled","deviated"} or not entries or any(entry["status"]!="confirmed" for entry in entries):raise CompanionError("all strategy fills must be confirmed in Ledger before reconciliation")
                for entry in entries:latest_confirmation=max(latest_confirmation,parse(entry["confirmed_at"])) if latest_confirmation else parse(entry["confirmed_at"])
        if latest_confirmation and parse(reconciliation["created_at"])<latest_confirmation:raise CompanionError("strategy reconciliation predates confirmed Ledger fills")
        with self.c.db.transaction() as con:
            con.execute("UPDATE broker_execution_plans SET status='reconciled',updated_at=? WHERE id=?",(iso(),plan_id))
            self._event_in_tx(con,plan_id,"reconciled",occurred,{"reconciliation_id":reference},f"reconciled:{plan_id}:{reference}",actor)
        return self.get(plan_id)

    def correct(self, *, plan_id:str, occurred_at:str, reason:str, corrected_event_ref:str, actor:str="primary-codex") -> dict[str,Any]:
        self.get(plan_id);occurred=iso(parse(occurred_at));payload={"reason":self._text(reason,"correction reason"),"corrected_event_ref":self._text(corrected_event_ref,"corrected_event_ref")}
        self._event(plan_id,"correction",occurred,payload,f"correction:{plan_id}:{corrected_event_ref}:{occurred}",actor);return self.get(plan_id)

    def request_termination(self, *, plan_id: str, occurred_at: str, reason: str, actor: str = "primary-codex") -> dict[str, Any]:
        plan=self.get(plan_id)
        if plan["status"] not in {"configured","active","sleeping","exception"}:raise CompanionError(f"plan cannot terminate from {plan['status']}")
        occurred=iso(parse(occurred_at));explanation=self._text(reason,"termination reason")
        with self.c.db.transaction() as con:
            con.execute("UPDATE broker_execution_plans SET status='termination_pending',status_reason=?,updated_at=? WHERE id=?",(explanation,iso(),plan_id))
            self._event_in_tx(con,plan_id,"termination_requested",occurred,{"reason":explanation,"triggered_unfilled_orders_cancelled":False},f"terminate-request:{plan_id}:{occurred}",actor)
        return self.get(plan_id)

    def report_terminated(self, *, plan_id: str, occurred_at: str, reason: str, actor: str = "primary-codex") -> dict[str, Any]:
        plan=self.get(plan_id)
        if plan["status"] not in {"active","sleeping","termination_pending","terminated"}:raise CompanionError(f"plan cannot report termination from {plan['status']}")
        occurred=iso(parse(occurred_at));explanation=self._text(reason,"termination reason")
        live=[item for item in self.orders(plan_id) if item["status"] in {"triggered","submitted","partially_filled","unknown"}]
        with self.c.db.transaction() as con:
            con.execute("UPDATE broker_execution_plans SET status='terminated',status_reason=?,terminated_at=?,updated_at=? WHERE id=?",(explanation,occurred,iso(),plan_id))
            self._event_in_tx(con,plan_id,"terminated",occurred,{"reason":explanation,"live_orders_remain":len(live),"broker_does_not_auto_cancel_triggered_unfilled_orders":True},f"terminated:{plan_id}:{occurred}",actor)
        return self.get(plan_id)

    def report_etf_dividend_termination(self, *, plan_id: str, occurred_at: str, corporate_action_ref: str, actor: str = "primary-codex") -> dict[str, Any]:
        plan=self.get(plan_id)
        if plan["plan_type"]!="moving_grid":raise CompanionError("ETF dividend automatic termination applies only to moving_grid")
        if self.c.financial.asset_get(plan["asset_id"])["asset_type"]!="etf":raise CompanionError("ETF dividend automatic termination requires an ETF asset")
        occurred=iso(parse(occurred_at));reference=self._text(corporate_action_ref,"corporate_action_ref")
        if plan["status"] not in {"active","sleeping","termination_pending","terminated"}:raise CompanionError(f"plan cannot report ETF dividend termination from {plan['status']}")
        live=[item for item in self.orders(plan_id) if item["status"] in {"triggered","submitted","partially_filled","unknown"}]
        with self.c.db.transaction() as con:
            con.execute("UPDATE broker_execution_plans SET status='terminated',status_reason=?,terminated_at=?,updated_at=? WHERE id=?",("broker_auto_terminated_after_etf_dividend",occurred,iso(),plan_id))
            for event_type,payload,key in (("corporate_action",{"type":"etf_dividend","broker_behavior":"automatic_grid_termination","corporate_action_ref":reference},f"etf-dividend:{plan_id}:{reference}"),("terminated",{"reason":"broker_auto_terminated_after_etf_dividend","live_orders_remain":len(live),"broker_does_not_auto_cancel_triggered_unfilled_orders":True},f"terminated:{plan_id}:{occurred}")):
                inserted=con.execute("INSERT OR IGNORE INTO broker_execution_events(id,plan_id,event_type,occurred_at,payload_json,idempotency_key,created_at) VALUES(?,?,?,?,?,?,?)",(new_id("brokerevent"),plan_id,event_type,occurred,canonical(payload),key,iso())).rowcount
                if inserted:self.c.audit.record(con,actor,event_type,"broker_execution_plan",plan_id,after=payload,reason="user-reported CICC ETF dividend termination")
        return self.get(plan_id)

    def orders(self, plan_id: str) -> list[dict[str, Any]]:
        with self.c.db.connect() as con:return rows_dict(con.execute("SELECT * FROM broker_managed_orders WHERE plan_id=? ORDER BY triggered_at,id",(plan_id,)).fetchall())

    def events(self, plan_id: str) -> list[dict[str, Any]]:
        with self.c.db.connect() as con:return rows_dict(con.execute("SELECT * FROM broker_execution_events WHERE plan_id=? ORDER BY occurred_at,id",(plan_id,)).fetchall())

    def net_quantities(self, plan_id: str) -> dict[str, str]:
        buy=sell=Decimal("0")
        for order in self.orders(plan_id):
            quantity=dec(order["submitted_quantity_text"],"submitted order quantity")-dec(order["cancelled_quantity_text"],"cancelled order quantity")
            if order["side"]=="buy":buy+=quantity
            else:sell+=quantity
        return {"submitted_buy_less_cancelled_buy":dtext(buy),"submitted_sell_less_cancelled_sell":dtext(sell),"net_buy":dtext(buy-sell),"net_sell":dtext(sell-buy),"portfolio_truth":"confirmed Ledger fills only"}

    def _current_grid_reference(self,plan:dict[str,Any])->Decimal:
        return dec(plan["current_reference_price_text"],"current grid reference")

    def _normalize_spec(self, plan_type: str, spec: Any) -> dict[str, Any]:
        if not isinstance(spec,dict):raise CompanionError("broker execution plan spec must be an object")
        common={"account_id","asset_id","validity_sessions","monitoring_window"}
        required={
            "priced_buy":common|{"trigger","order","quantity"},"priced_sell":common|{"trigger","order","quantity"},
            "bracket_exit":common|{"base_price","take_profit","stop_loss","order","quantity"},
            "moving_grid":common|{"initial_reference_price","spacing_type","rise_sell_spacing","fall_buy_spacing","sell_order","buy_order","sell_quantity","buy_quantity","price_range","position_range","multiple_grid_order"},
        }[plan_type]
        optional={"effective_trigger_band_pct","delay_confirmation"} if plan_type!="moving_grid" else set()
        unknown=set(spec)-required-optional
        if set(spec)<required or unknown:raise CompanionError(f"{plan_type} spec fields differ from broker contract; missing={sorted(required-set(spec))}, unknown={sorted(unknown)}")
        result={key:spec[key] for key in required|optional if key in spec}
        result["account_id"]=self.c.financial.account_get(self._text(result["account_id"],"account_id"))["id"]
        result["asset_id"]=self.c.financial.asset_get(self._text(result["asset_id"],"asset_id"))["id"]
        if result["validity_sessions"] not in VALIDITY_SESSIONS:raise CompanionError("validity_sessions must be 5, 20, 60 or 180")
        self._monitoring_window(result["monitoring_window"])
        if plan_type in {"priced_buy","priced_sell"}:self._trigger(result["trigger"]);self._order(result["order"]);self._quantity(result["quantity"],"quantity",allow_fraction=plan_type=="priced_sell")
        elif plan_type=="bracket_exit":
            self._positive(result["base_price"],"base_price");self._boundary(result["take_profit"],"take_profit");self._boundary(result["stop_loss"],"stop_loss");self._order(result["order"]);self._quantity(result["quantity"],"quantity",allow_fraction=True)
            base=dec(result["base_price"])
            if result["take_profit"]["mode"]=="price" and dec(result["take_profit"]["value"])<=base:raise CompanionError("take_profit price must be above base_price")
            if result["stop_loss"]["mode"]=="price" and dec(result["stop_loss"]["value"])>=base:raise CompanionError("stop_loss price must be below base_price")
        else:self._grid(result)
        if "effective_trigger_band_pct" in result:
            value=dec(result["effective_trigger_band_pct"],"effective_trigger_band_pct")
            if not Decimal("0.01")<=value<=Decimal("99.99"):raise CompanionError("effective trigger band must be within 0.01%..99.99%")
        if "delay_confirmation" in result:self._delay(result["delay_confirmation"],separate=plan_type=="bracket_exit")
        result["broker_semantics"]={"monitor_frequency":"level_1_every_3_seconds","submission_not_guaranteed":True,"human_configuration_required":True}
        if plan_type=="moving_grid":result["broker_semantics"].update({"reference_update":"trigger_driven","no_reference_update_rejections":["insufficient_cash","insufficient_holdings"],"termination_keeps_triggered_unfilled_orders":True,"etf_dividend":"automatic_grid_termination","terminate_and_liquidate_failure":"broker_unspecified_manual_reconciliation_required"})
        return result

    def _grid(self,spec:dict[str,Any])->None:
        self._positive(spec["initial_reference_price"],"initial_reference_price")
        if spec["spacing_type"] not in {"difference","percentage"}:raise CompanionError("grid spacing_type must be difference or percentage")
        self._positive(spec["rise_sell_spacing"],"rise_sell_spacing");self._positive(spec["fall_buy_spacing"],"fall_buy_spacing")
        self._order(spec["sell_order"]);self._order(spec["buy_order"]);self._whole(spec["sell_quantity"],"sell_quantity");self._whole(spec["buy_quantity"],"buy_quantity")
        if not isinstance(spec["multiple_grid_order"],bool):raise CompanionError("multiple_grid_order must be boolean")
        price=spec["price_range"]
        if not isinstance(price,dict) or set(price)!={"lower","upper","out_of_range_behavior"}:raise CompanionError("grid price_range contract is invalid")
        if self._positive(price["lower"],"price_range.lower")>=self._positive(price["upper"],"price_range.upper"):raise CompanionError("grid price range must be ordered")
        if price["out_of_range_behavior"] not in {"sleep","terminate_and_liquidate"}:raise CompanionError("unsupported grid out-of-range behavior")
        position=spec["position_range"]
        if not isinstance(position,dict) or set(position)!={"max_net_buy","max_net_sell"}:raise CompanionError("grid position_range contract is invalid")
        self._whole(position["max_net_buy"],"max_net_buy",allow_zero=True);self._whole(position["max_net_sell"],"max_net_sell",allow_zero=True)

    def _trigger(self,value:Any)->None:
        if not isinstance(value,dict) or set(value)!={"direction","monitor_price"} or value["direction"] not in {"cross_up","cross_down"}:raise CompanionError("priced trigger requires cross_up/cross_down and monitor_price")
        self._positive(value["monitor_price"],"monitor_price")

    def _order(self,value:Any)->None:
        if not isinstance(value,dict) or set(value)!={"price_type","price_instruction","custom_price"}:raise CompanionError("order contract fields are invalid")
        if value["price_type"] not in {"limit","market"}:raise CompanionError("order price_type must be limit or market")
        limit={"custom","instant","buy_1","buy_2","buy_3","buy_4","buy_5","sell_1","sell_2","sell_3","sell_4","sell_5"};market={"exchange_market_option"};allowed=limit if value["price_type"]=="limit" else market
        if value["price_instruction"] not in allowed:raise CompanionError("unsupported broker price instruction")
        if value["price_instruction"]=="custom":self._positive(value["custom_price"],"custom_price")
        elif value["custom_price"] is not None:raise CompanionError("custom_price is allowed only for custom price instruction")

    def _boundary(self,value:Any,label:str)->None:
        if not isinstance(value,dict) or set(value)!={"mode","value"} or value["mode"] not in {"price","percentage"}:raise CompanionError(f"{label} contract is invalid")
        self._positive(value["value"],f"{label}.value")

    def _create_order_execution(self,*,plan:dict[str,Any],order_id:str,broker_order_ref:str,side:str,quantity:Decimal,ordered_at:str)->dict[str,Any]:
        revision=self.c.cognition.revision_get(plan["decision_revision_id"]);authorization=revision["metadata"]["execution_plan"]
        risk_id=authorization.get("sell_risk_calculation_id") if plan["plan_type"]=="moving_grid" and side=="sell" else authorization["buy_risk_calculation_id"]
        risk=self.c.financial.calculation_get(risk_id)
        details={"execution_contract_version":1,"queue_id":plan["queue_id"],"action":{"account_id":plan["account_id"],"asset_id":plan["asset_id"],"side":side,"quantity":dtext(quantity),"price_range":risk["assumptions"]["price_range"],"valid_until":plan["valid_until"]},"decision_revision_id":revision["id"],"frozen_risk_calculation_id":risk_id,"prepared_risk_calculation_id":risk_id,"prepared_market_snapshot_id":risk["outputs"]["market_snapshot_id"],"valid_until":plan["valid_until"],"human_execution_only":True,"broker_strategy_plan_id":plan["id"],"broker_managed_order_id":order_id}
        execution=self.c.cognition.execution_create(revision["object_id"],details,decision_revision_id=revision["id"],idempotency_key=f"strategy-order:{plan['id']}:{broker_order_ref}",status="proposed")
        return self.c.execution.mark_ordered(execution_id=execution["id"],broker_order_ref=broker_order_ref,ordered_at=ordered_at)

    def _require_not_expired(self,plan:dict[str,Any],*,allow_terminated:bool=False)->None:
        if allow_terminated and plan["status"] in {"termination_pending","terminated"}:return
        if parse(plan["valid_until"])<=utc_now():
            if plan["status"] not in {"expired","terminated","reconciled"}:self._set_status(plan["id"],"expired",reason="broker strategy validity elapsed")
            raise CompanionError("broker execution plan has expired")

    def _require_authorization_current(self,plan:dict[str,Any])->None:
        revision=self.c.cognition.revision_get(plan["decision_revision_id"]);decision=self.c.cognition.object_get(revision["object_id"])
        if decision["status"]!="issued" or decision["current_revision_id"]!=revision["id"]:raise CompanionError("broker execution plan Decision is no longer current")
        queue=self.c.operating.queue_get(plan["queue_id"])
        if queue["state"]!="accepted":raise CompanionError("broker execution plan Action Card is no longer accepted")
        card=self.c.operating.queue_card(plan["queue_id"])
        if not card["executable_now"]:raise CompanionError(f"broker execution plan no longer passes current controls: {card['blocking_reasons']}")

    def _delay(self,value:Any,*,separate:bool)->None:
        if not isinstance(value,dict):raise CompanionError("delay_confirmation must be an object")
        required={"mode","count","separate_take_profit_stop_loss_counters"} if separate else {"mode","count"}
        if set(value)!=required or value["mode"] not in {"consecutive","cumulative"} or not isinstance(value["count"],int) or not 2<=value["count"]<=20:raise CompanionError("delay confirmation contract is invalid")
        if separate and value["separate_take_profit_stop_loss_counters"] is not True:raise CompanionError("take-profit and stop-loss confirmation counters are separate")

    @staticmethod
    def _monitoring_window(value:Any)->None:
        if value is None:return
        if not isinstance(value,dict) or set(value)!={"weekdays","start","end"}:raise CompanionError("monitoring_window must be null or weekdays/start/end")
        days=value["weekdays"]
        if not isinstance(days,list) or not days or any(isinstance(day,bool) or not isinstance(day,int) or not 1<=day<=7 for day in days) or len(days)!=len(set(days)):raise CompanionError("monitoring_window weekdays must be unique ISO weekdays 1..7")
        try:start=time.fromisoformat(value["start"]);end=time.fromisoformat(value["end"])
        except (TypeError,ValueError):raise CompanionError("monitoring_window start and end must be ISO local times")
        if start>=end:raise CompanionError("monitoring_window start must precede end")

    @staticmethod
    def _text(value:Any,label:str)->str:
        if not isinstance(value,str) or not value.strip():raise CompanionError(f"{label} must be a non-empty string")
        return value.strip()

    @staticmethod
    def _positive(value:Any,label:str)->Decimal:
        result=dec(value,label)
        if result<=0:raise CompanionError(f"{label} must be positive")
        return result

    def _whole(self,value:Any,label:str,*,allow_zero:bool=False)->Decimal:
        result=dec(value,label)
        if result!=result.to_integral_value() or result<(0 if allow_zero else 1):raise CompanionError(f"{label} must be a {'non-negative' if allow_zero else 'positive'} whole number")
        return result

    def _quantity(self,value:Any,label:str,*,allow_fraction:bool)->Decimal:
        if not isinstance(value,dict):return self._whole(value,label)
        if not allow_fraction or set(value)!={"mode","fraction","resolved_quantity","holding_quantity_at_configuration"} or value["mode"]!="holding_fraction" or value["fraction"] not in {"1","1/2","1/3","1/4"}:
            raise CompanionError(f"{label} holding fraction contract is invalid")
        holding=self._whole(value["holding_quantity_at_configuration"],f"{label}.holding_quantity_at_configuration")
        resolved=self._whole(value["resolved_quantity"],f"{label}.resolved_quantity")
        numerator,denominator=(1,1) if value["fraction"]=="1" else (1,int(value["fraction"].split("/")[1]))
        if resolved>holding or resolved>holding*Decimal(numerator)/Decimal(denominator):raise CompanionError(f"{label} resolved quantity exceeds selected holding fraction")
        return resolved

    def resolved_quantity(self,spec:dict[str,Any])->Decimal:
        return self._quantity(spec["quantity"],"quantity",allow_fraction=True)

    def _optional_positive(self,value:Any,label:str)->str|None:
        return dtext(self._positive(value,label)) if value is not None else None

    def _set_status(self,plan_id:str,status:str,*,reason:str|None=None,broker_condition_ref:str|None=None,configured_at:str|None=None,terminated_at:str|None=None)->None:
        if status not in PLAN_STATUSES:raise CompanionError("unsupported broker execution plan status")
        fields=["status=?","updated_at=?"];values=[status,iso()]
        for name,value in (("status_reason",reason),("broker_condition_ref",broker_condition_ref),("configured_at",configured_at),("terminated_at",terminated_at)):
            if value is not None:fields.append(f"{name}=?");values.append(value)
        values.append(plan_id)
        with self.c.db.transaction() as con:con.execute(f"UPDATE broker_execution_plans SET {','.join(fields)} WHERE id=?",values)

    def _event(self,plan_id:str,event_type:str,occurred_at:str,payload:dict[str,Any],idempotency_key:str,actor:str,*,order_id:str|None=None)->None:
        with self.c.db.transaction() as con:
            self._event_in_tx(con,plan_id,event_type,occurred_at,payload,idempotency_key,actor,order_id=order_id)

    def _event_in_tx(self,con,plan_id:str,event_type:str,occurred_at:str,payload:dict[str,Any],idempotency_key:str,actor:str,*,order_id:str|None=None)->None:
        inserted=con.execute("INSERT OR IGNORE INTO broker_execution_events(id,plan_id,order_id,event_type,occurred_at,payload_json,idempotency_key,created_at) VALUES(?,?,?,?,?,?,?,?)",(new_id("brokerevent"),plan_id,order_id,event_type,occurred_at,canonical(payload),idempotency_key,iso())).rowcount
        if inserted:self.c.audit.record(con,actor,event_type,"broker_execution_plan",plan_id,after=payload,reason="user-reported CICC condition-order lifecycle event")
