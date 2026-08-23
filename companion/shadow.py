from __future__ import annotations

import json
from datetime import date, datetime, time
from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo

from .foundation import CompanionError, canonical, digest, new_id
from .db import row_dict, rows_dict
from .financial import dec, dtext
from .timeutil import iso, parse, utc_now
from .quant_runtime import verify_artifact_hash
from .quant_runtime import RealitySpec
from .research import evaluate_native_experiment_bundle
from .v4_data import canonical_sha256


class ShadowLedger:
    """Portfolio-first simulated lifecycle, physically separate from the real ledger."""

    def __init__(self, companion):
        self.c = companion
        self.db = companion.db

    def book_create(
        self,
        *,
        strategy_version_id: str,
        name: str,
        initial_cash: Any,
        reality_spec: dict[str, Any],
        sample_gate: dict[str, Any],
        base_currency: str = "CNY",
    ) -> dict[str, Any]:
        self.c.jobs.feature_require("v4_shadow")
        strategy = self.c.research.strategy_get(strategy_version_id)
        if strategy["status"] != "shadow":
            raise CompanionError("shadow book requires an explicitly promoted shadow strategy")
        cash = dec(initial_cash, "initial_cash")
        if cash <= 0:
            raise CompanionError("shadow initial_cash must be positive")
        try:
            normalized_reality = RealitySpec.from_value(reality_spec).to_dict()
        except Exception as exc:
            raise CompanionError(f"invalid Shadow RealitySpec: {exc}") from exc
        if canonical(normalized_reality) != canonical(reality_spec):
            raise CompanionError("Shadow must freeze a complete normalized RealitySpec")
        registered_reality = strategy["spec"]["costs"]["reality_spec"]
        if canonical(normalized_reality) != canonical(registered_reality):
            raise CompanionError("Shadow RealitySpec differs from immutable StrategyVersion")
        if base_currency.upper() != normalized_reality["currency"].upper():
            raise CompanionError("Shadow base currency differs from RealitySpec currency")
        required_gate = {"minimum_days", "minimum_rebalances", "minimum_decisions","minimum_market_regimes"}
        missing_gate = required_gate - set(sample_gate)
        if missing_gate:
            raise CompanionError(f"shadow sample gate missing: {sorted(missing_gate)}")
        invalid_gate={key:value for key,value in sample_gate.items() if key in required_gate and (isinstance(value,bool) or not isinstance(value,int))}
        if invalid_gate:raise CompanionError(f"shadow sample gate values must be integers: {invalid_gate}")
        floors={"minimum_days":90,"minimum_rebalances":12,"minimum_decisions":5,"minimum_market_regimes":2}
        below={key:(sample_gate[key],minimum) for key,minimum in floors.items() if int(sample_gate[key])<minimum}
        if below:raise CompanionError(f"shadow sample gate is below system floor: {below}")
        bid, now = new_id("shadowbook"), iso()
        with self.db.transaction() as con:
            con.execute(
                "INSERT INTO shadow_books(id,strategy_version_id,name,status,base_currency,initial_cash_text,reality_spec_json,sample_gate_json,created_at,updated_at) VALUES(?,?,?,'active',?,?,?,?,?,?)",
                (
                    bid,
                    strategy_version_id,
                    name,
                    base_currency.upper(),
                    dtext(cash),
                    canonical(normalized_reality),
                    canonical(sample_gate),
                    now,
                    now,
                ),
            )
        return self.book_get(bid)

    def book_get(self, book_id: str) -> dict[str, Any]:
        with self.db.connect() as con:
            item = row_dict(con.execute("SELECT * FROM shadow_books WHERE id=?", (book_id,)).fetchone())
        if not item:
            raise CompanionError(f"shadow book not found: {book_id}")
        return item

    def book_list(self, status: str | None = None) -> list[dict[str, Any]]:
        with self.db.connect() as con:
            if status:
                rows = con.execute(
                    "SELECT * FROM shadow_books WHERE status=? ORDER BY created_at DESC", (status,)
                ).fetchall()
            else:
                rows = con.execute("SELECT * FROM shadow_books ORDER BY created_at DESC").fetchall()
        return rows_dict(rows)

    def rebalance_record(
        self,
        *,
        book_id: str,
        signal_snapshot_id: str,
        execution_snapshot_id: str,
        experiment_run_id: str,
        as_of: str,
        target_manifest_id: str,
        denominator_hash: str,
    ) -> dict[str, Any]:
        self.c.jobs.feature_require("v4_shadow")
        book = self.book_get(book_id)
        if book["status"] != "active":
            raise CompanionError("shadow book is not active")
        signal_snapshot = self.c.data.snapshot_get(signal_snapshot_id, verify=True)
        execution_snapshot = self.c.data.snapshot_get(execution_snapshot_id, verify=True)
        if signal_snapshot["status"] != "ready" or execution_snapshot["status"] != "ready":
            raise CompanionError("shadow rebalance requires ready signal and execution snapshots")
        if signal_snapshot["denominator_hash"] != denominator_hash:
            raise CompanionError("shadow denominator differs from frozen signal snapshot")
        if (
            execution_snapshot["denominator_hash"] != signal_snapshot["denominator_hash"]
            or execution_snapshot["universe_hash"] != signal_snapshot["universe_hash"]
        ):
            raise CompanionError("execution Snapshot denominator/universe drift requires review")
        target_manifest=self.c.data.manifest_get(target_manifest_id)
        if target_manifest["status"]!="ready" or target_manifest["kind"]!="target_weights":raise CompanionError("target_weights manifest has wrong kind or state")
        experiment = self.c.research.experiment_get(experiment_run_id)
        if experiment["status"] != "succeeded":
            raise CompanionError("shadow rebalance requires a successful experiment")
        if experiment["strategy_version_id"] != book["strategy_version_id"]:
            raise CompanionError("experiment and shadow book use different strategies")
        if experiment["dataset_snapshot_id"] != signal_snapshot_id:
            raise CompanionError("experiment and rebalance use different signal snapshots")
        phase=experiment.get("params",{}).get("evaluation_phase")
        if self.c.gate_scope=="production" and phase!="forward_shadow":
            raise CompanionError("production Shadow accepts only untuned forward_shadow signals")
        if self.c.gate_scope=="test_fixture" and phase not in {"final_holdout","forward_shadow"}:
            raise CompanionError("fixture Shadow accepts only final_holdout or forward_shadow signals")
        if not experiment.get("bundle_manifest_id"):raise CompanionError("shadow experiment lacks immutable bundle")
        bundle_manifest=self.c.data.manifest_get(experiment["bundle_manifest_id"])
        outer=bundle_manifest["manifest"].get("manifest",{})
        evaluate_native_experiment_bundle(outer)
        candidate_bundle=outer["candidate_bundle"];candidate_result=candidate_bundle["experiment_result"]
        target=candidate_result["target_portfolio"]
        if not verify_artifact_hash(target):raise CompanionError("experiment target hash invalid")
        published_target=target_manifest["manifest"].get("manifest",{})
        if canonical(published_target)!=canonical(target):raise CompanionError("target manifest differs from ExperimentBundle")
        if target.get("dataset_snapshot_id")!=signal_snapshot_id or target.get("strategy_version_id")!=book["strategy_version_id"]:raise CompanionError("target_weights lineage mismatch")
        if target.get("effective_on")!=as_of[:10]:raise CompanionError("shadow as_of must equal target effective_on")
        if target.get("as_of","")>=target.get("effective_on",""):raise CompanionError("target violates signal/execution delay")
        as_of_date=date.fromisoformat(as_of[:10]);created_date=parse(book["created_at"]).astimezone(ZoneInfo("Asia/Shanghai")).date();today=utc_now().astimezone(ZoneInfo("Asia/Shanghai")).date()
        if as_of_date<created_date:raise CompanionError("Shadow Book cannot be historically backfilled")
        if as_of_date>today:raise CompanionError("Shadow Book cannot record a future rebalance")
        if canonical_sha256(target.get("dataset_denominator"))!=signal_snapshot["denominator_hash"]:raise CompanionError("target denominator content differs from signal Snapshot")
        if canonical_sha256(target.get("dataset_universe"))!=signal_snapshot["universe_hash"]:raise CompanionError("target universe content differs from signal Snapshot")
        self._validate_forward_chronology(
            book=book,
            experiment=experiment,
            signal_snapshot=signal_snapshot,
            execution_snapshot=execution_snapshot,
            signal_date=date.fromisoformat(target["as_of"]),
            execution_date=as_of_date,
        )
        prior_cash,prior_positions,last_as_of=self._expected_state(book_id,book)
        if last_as_of and as_of[:10]<=last_as_of[:10]:raise CompanionError("Shadow rebalances must advance monotonically by trading date")
        bars,regime=self._execution_market(execution_snapshot_id,as_of[:10],book)
        required_assets=set(prior_positions)|{row["asset_id"] for row in target.get("weights",[])}
        observed_assets={row["asset_id"] for row in bars}
        missing_assets=sorted(required_assets-observed_assets)
        if missing_assets:raise CompanionError(f"Shadow execution market lacks held/target assets: {missing_assets}")
        actions=[item for item in self.c.data.snapshot_corporate_actions(execution_snapshot_id) if item["date"]==as_of[:10]]
        simulation=self.c.quant.simulate(bars=bars,target_portfolios=[target],initial_cash=prior_cash,initial_positions=prior_positions,reality_spec=book["reality_spec"],corporate_actions=actions)
        if not verify_artifact_hash(simulation):raise CompanionError("Shadow deterministic simulation hash invalid")
        if simulation["initial_cash"]!=dtext(dec(prior_cash)) or self._simulation_initial_positions(simulation)!=prior_positions:raise CompanionError("Shadow simulation does not continue the prior book state")
        if target["artifact_hash"] not in simulation.get("target_hashes",[]):raise CompanionError("Shadow simulation does not consume frozen target_weights")
        normalized=self._coerce_simulation(simulation,as_of);self._validate_simulation(normalized)
        projected=dict(prior_positions)
        for event in simulation.get("corporate_events",[]):
            if event["action_type"]=="split":projected[event["asset_id"]]=int(event["after_quantity"])
            elif event["action_type"]=="delist":projected.pop(event["asset_id"],None)
        for fill in normalized["fills"]:
            quantity=int(dec(fill["quantity"]));projected[fill["asset_id"]]=projected.get(fill["asset_id"],0)+(quantity if fill["side"]=="buy" else -quantity)
        projected={asset:quantity for asset,quantity in projected.items() if quantity}
        final_positions={row["asset_id"]:int(row["quantity"]) for row in simulation["final_holdings"]}
        if projected!=final_positions:raise CompanionError("Shadow fill projection differs from deterministic final holdings")
        key = digest(
            "shadow-rebalance-v1",
            book_id,
            signal_snapshot_id,
            execution_snapshot_id,
            experiment_run_id,
            as_of,
            target_manifest_id,
            denominator_hash,
            simulation["artifact_hash"],
        )
        with self.db.connect() as con:
            existing = row_dict(
                con.execute(
                    "SELECT * FROM shadow_rebalances WHERE idempotency_key=?", (key,)
                ).fetchone()
            )
        if existing:
            return self.rebalance_get(existing["id"])
        rid, now = new_id("shadowreb"), iso()
        fills=normalized.get("fills",[]);blocked=normalized.get("blocked",[])
        if fills and blocked:
            status = "partially_filled"
        elif fills:
            status = "filled"
        elif blocked:
            status = "blocked"
        else:
            status = "simulated"
        ledger_before = self._real_ledger_count()
        with self.db.transaction() as con:
            con.execute(
                "INSERT INTO shadow_rebalances(id,book_id,signal_snapshot_id,execution_snapshot_id,experiment_run_id,as_of,target_manifest_id,denominator_hash,status,result_json,idempotency_key,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    rid,
                    book_id,
                    signal_snapshot_id,
                    execution_snapshot_id,
                    experiment_run_id,
                    as_of,
                    target_manifest_id,
                    denominator_hash,
                    status,
                    canonical({"experiment_bundle_id":experiment["bundle_manifest_id"],"target_manifest_id":target_manifest_id,"signal_snapshot_id":signal_snapshot_id,"execution_snapshot_id":execution_snapshot_id,"simulation_hash":simulation["artifact_hash"],"simulation":simulation,"prior_cash":prior_cash,"prior_positions":prior_positions,"market_regime":regime}),
                    key,
                    now,
                ),
            )
            for index, fill in enumerate(fills):
                self.c.financial.asset_get(fill["asset_id"])
                fid = new_id("shadowfill")
                fill_key = f"{key}:fill:{index}"
                con.execute(
                    "INSERT INTO shadow_fills(id,rebalance_id,asset_id,side,quantity_text,price_text,gross_text,fee_text,tax_text,status,reason,trade_date,settle_date,idempotency_key,created_at) VALUES(?,?,?,?,?,?,?,?,?,'simulated',?,?,?,?,?)",
                    (
                        fid,
                        rid,
                        fill["asset_id"],
                        fill["side"],
                        dtext(dec(fill["quantity"], "quantity")),
                        dtext(dec(fill["price"], "price")),
                        dtext(dec(fill["gross"], "gross")),
                        dtext(dec(fill.get("fee", "0"), "fee")),
                        dtext(dec(fill.get("tax", "0"), "tax")),
                        fill.get("reason"),
                        fill["trade_date"],
                        fill.get("settle_date"),
                        fill_key,
                        now,
                    ),
                )
            for index, blocked_fill in enumerate(blocked):
                self.c.financial.asset_get(blocked_fill["asset_id"])
                fid = new_id("shadowfill")
                block_key = f"{key}:blocked:{index}"
                con.execute(
                    "INSERT INTO shadow_fills(id,rebalance_id,asset_id,side,quantity_text,price_text,gross_text,fee_text,tax_text,status,reason,trade_date,settle_date,idempotency_key,created_at) VALUES(?,?,?,?,?,?,?,?,?,'blocked',?,?,?,?,?)",
                    (
                        fid,
                        rid,
                        blocked_fill["asset_id"],
                        blocked_fill["side"],
                        dtext(dec(blocked_fill.get("quantity", "0"))),
                        dtext(dec(blocked_fill.get("price", "0"))),
                        "0",
                        "0",
                        "0",
                        blocked_fill["reason"],
                        blocked_fill.get("trade_date", as_of[:10]),
                        None,
                        block_key,
                        now,
                    ),
                )
            self._metric_insert(con,book_id,as_of,simulation["final_nav"],simulation["final_cash"],{"source_simulation_hash":simulation["artifact_hash"],"market_regime":regime},now)
        if self._real_ledger_count() != ledger_before:
            raise RuntimeError("shadow isolation invariant violated: real ledger changed")
        return self.rebalance_get(rid)

    def _validate_forward_chronology(
        self,
        *,
        book:dict[str,Any],
        experiment:dict[str,Any],
        signal_snapshot:dict[str,Any],
        execution_snapshot:dict[str,Any],
        signal_date:date,
        execution_date:date,
    )->None:
        """Reject a production replay that could have seen execution-day information."""

        if self.c.gate_scope!="production":
            return
        if signal_snapshot["id"]==execution_snapshot["id"]:
            raise CompanionError("production Shadow requires distinct signal and execution Snapshots")
        required_times={
            "book.created_at":book.get("created_at"),
            "experiment.started_at":experiment.get("started_at"),
            "experiment.finished_at":experiment.get("finished_at"),
            "signal_snapshot.created_at":signal_snapshot.get("created_at"),
            "execution_snapshot.created_at":execution_snapshot.get("created_at"),
            "signal_snapshot.knowledge_cutoff":signal_snapshot.get("knowledge_cutoff"),
            "execution_snapshot.knowledge_cutoff":execution_snapshot.get("knowledge_cutoff"),
        }
        missing=sorted(key for key,value in required_times.items() if not value)
        if missing:
            raise CompanionError(f"production Shadow chronology is incomplete: {missing}")
        values={key:parse(value) for key,value in required_times.items()}
        zone=ZoneInfo("Asia/Shanghai")
        signal_close=datetime.combine(signal_date,time(15,0),zone).astimezone(values["book.created_at"].tzinfo)
        execution_open=datetime.combine(execution_date,time(9,30),zone).astimezone(values["book.created_at"].tzinfo)
        execution_close=datetime.combine(execution_date,time(15,0),zone).astimezone(values["book.created_at"].tzinfo)
        if signal_date>=execution_date:
            raise CompanionError("production Shadow signal must precede its execution session")
        if values["book.created_at"]>values["experiment.started_at"]:
            raise CompanionError("Shadow Book must exist before its forward signal run starts")
        if values["signal_snapshot.created_at"]>values["experiment.started_at"]:
            raise CompanionError("signal Snapshot must be published before its experiment starts")
        if not signal_close<=values["signal_snapshot.knowledge_cutoff"]<=values["experiment.started_at"]:
            raise CompanionError("signal Snapshot cutoff must follow signal close and precede experiment start")
        if not values["experiment.finished_at"]<execution_open:
            raise CompanionError("forward signal must finish before the execution session opens")
        if values["execution_snapshot.knowledge_cutoff"]<execution_close:
            raise CompanionError("execution Snapshot cutoff precedes the close price used for simulation")
        if values["execution_snapshot.created_at"]<values["execution_snapshot.knowledge_cutoff"]:
            raise CompanionError("execution Snapshot was published before its declared knowledge cutoff")

    def rebalance_get(self, rebalance_id: str) -> dict[str, Any]:
        with self.db.connect() as con:
            item = row_dict(
                con.execute(
                    "SELECT * FROM shadow_rebalances WHERE id=?", (rebalance_id,)
                ).fetchone()
            )
            fills = rows_dict(
                con.execute(
                    "SELECT * FROM shadow_fills WHERE rebalance_id=? ORDER BY created_at,id",
                    (rebalance_id,),
                ).fetchall()
            )
        if not item:
            raise CompanionError(f"shadow rebalance not found: {rebalance_id}")
        item["fills"] = fills
        return item

    def metric_record(
        self, book_id: str, as_of: str, nav: Any, cash: Any, metrics: dict[str, Any],_derived:bool=False
    ) -> dict[str, Any]:
        if not _derived:raise CompanionError("shadow metrics are derived only from a validated rebalance")
        self.book_get(book_id)
        with self.db.transaction() as con:
            row=self._metric_insert(con,book_id,as_of,nav,cash,metrics,iso())
        return row_dict(row)

    def _metric_insert(self,con,book_id:str,as_of:str,nav:Any,cash:Any,metrics:dict[str,Any],now:str):
        nav_d,cash_d=dec(nav,"nav"),dec(cash,"cash")
        if nav_d<0 or cash_d<0:raise CompanionError("shadow nav/cash cannot be negative")
        content_hash=digest("shadow-metric-v1",book_id,as_of,dtext(nav_d),dtext(cash_d),metrics)
        mid=new_id("shadowmetric")
        con.execute("INSERT OR IGNORE INTO shadow_metrics(id,book_id,as_of,nav_text,cash_text,metrics_json,content_hash,created_at) VALUES(?,?,?,?,?,?,?,?)",(mid,book_id,as_of,dtext(nav_d),dtext(cash_d),canonical(metrics),content_hash,now))
        return con.execute("SELECT * FROM shadow_metrics WHERE content_hash=?",(content_hash,)).fetchone()

    def _expected_state(self,book_id:str,book:dict[str,Any])->tuple[str,dict[str,int],str|None]:
        with self.db.connect() as con:
            latest=row_dict(con.execute("SELECT * FROM shadow_metrics WHERE book_id=? ORDER BY as_of DESC LIMIT 1",(book_id,)).fetchone())
        positions={asset:int(dec(quantity)) for asset,quantity in self.positions(book_id).items()}
        if latest:return latest["cash_text"],positions,latest["as_of"]
        if positions:raise CompanionError("new Shadow Book has positions without a prior metric")
        return book["initial_cash_text"],{},None

    @staticmethod
    def _simulation_initial_positions(simulation:dict[str,Any])->dict[str,int]:
        contract=simulation.get("initial_positions",{})
        if contract.get("dated_lots"):
            raise CompanionError("forward Shadow simulation cannot inject dated initial lots")
        result={}
        for row in contract.get("sellable_positions",[]):
            asset=row["asset_id"]
            if asset in result:raise CompanionError("duplicate Shadow initial position")
            result[asset]=int(row["quantity"])
        return result

    def _execution_market(self,snapshot_id:str,as_of:str,book:dict[str,Any])->tuple[list[dict[str,Any]],str]:
        snapshot=self.c.data.snapshot_manifest(snapshot_id)
        names=[partition.name for partition in snapshot.partitions if partition.quality.get("stream")=="daily" and partition.quality.get("role") in {"production","reference"}]
        if not names:raise CompanionError("Shadow snapshot has no validated production/reference daily partition")
        payloads=self.c.data.snapshot_partition_payloads(snapshot_id,names)
        all_rows=[];seen={}
        for payload in payloads:
            if payload["stream"]!="daily":continue
            for row in payload["value"]:
                key=(row["asset_id"],row["date"])
                if key in seen and canonical(seen[key])!=canonical(row):raise CompanionError(f"conflicting Shadow market row: {key}")
                seen[key]=row
        all_rows=list(seen.values())
        current=[row for row in all_rows if row["date"]==as_of]
        if not current:raise CompanionError("Shadow snapshot has no execution-date market rows")
        calendar_ref=snapshot.calendar.get("object_ref") if hasattr(snapshot.calendar,"get") else None
        if not calendar_ref:raise CompanionError("Shadow snapshot lacks a trading calendar ref")
        from .v4_data import DataObjectRef
        calendar=json.loads(self.c.data.store.read(DataObjectRef.from_dict(calendar_ref)).decode("utf-8"))
        if not any(row.get("date")==as_of and row.get("is_open") is True for row in calendar):raise CompanionError("Shadow as_of is not an open session in the frozen calendar")
        strategy=self.c.research.strategy_get(book["strategy_version_id"])
        benchmark=strategy["spec"].get("benchmark",{});asset=benchmark.get("asset_id") or benchmark.get("index_code")
        history=sorted((row for row in all_rows if asset and row["asset_id"]==asset and row["date"]<=as_of),key=lambda row:row["date"])
        regime="unclassified"
        if len(history)>=10:
            change=Decimal(str(history[-1]["close"]))/Decimal(str(history[max(0,len(history)-20)]["close"]))-Decimal("1")
            regime="up" if change>=Decimal("0.05") else "down" if change<=Decimal("-0.05") else "range"
        return current,regime

    def positions(self, book_id: str) -> dict[str, str]:
        self.book_get(book_id)
        positions: dict[str, Decimal] = {}
        with self.db.connect() as con:
            rebalances=rows_dict(con.execute("SELECT id,result_json FROM shadow_rebalances WHERE book_id=? ORDER BY as_of,id",(book_id,)).fetchall())
            fills_by_rebalance={item["id"]:rows_dict(con.execute("SELECT * FROM shadow_fills WHERE rebalance_id=? AND status='simulated' ORDER BY trade_date,id",(item["id"],)).fetchall()) for item in rebalances}
        for rebalance in rebalances:
            for event in rebalance["result"].get("simulation",{}).get("corporate_events",[]):
                if event["action_type"]=="split":positions[event["asset_id"]]=Decimal(str(event["after_quantity"]))
                elif event["action_type"]=="delist":positions.pop(event["asset_id"],None)
            for fill in fills_by_rebalance[rebalance["id"]]:
                quantity = dec(fill["quantity_text"])
                if fill["side"] == "sell":quantity = -quantity
                positions[fill["asset_id"]] = positions.get(fill["asset_id"], Decimal(0)) + quantity
        return {asset_id: dtext(quantity) for asset_id, quantity in sorted(positions.items()) if quantity}

    def sample_status(self, book_id: str) -> dict[str, Any]:
        book = self.book_get(book_id)
        gate = book["sample_gate"]
        with self.db.connect() as con:
            row = con.execute(
                "SELECT COUNT(*),MIN(as_of),MAX(as_of) FROM shadow_rebalances WHERE book_id=?",
                (book_id,),
            ).fetchone()
            decision_count=con.execute("SELECT COUNT(DISTINCT o.id) FROM cognitive_revisions r JOIN cognitive_objects o ON o.id=r.object_id WHERE o.object_type='decision' AND json_extract(r.metadata_json,'$.shadow_book_id')=? AND json_extract(r.metadata_json,'$.decision_contract_version')=4 AND json_extract(r.metadata_json,'$.decision_mode') IN ('beta','strategy_eligible') AND r.status='published'",(book_id,)).fetchone()[0]
            metric_count=con.execute("SELECT COUNT(*) FROM shadow_metrics WHERE book_id=?",(book_id,)).fetchone()[0]
            results=[item[0] for item in con.execute("SELECT result_json FROM shadow_rebalances WHERE book_id=?",(book_id,)).fetchall()]
        regimes={json.loads(raw).get("market_regime") for raw in results};regimes.discard(None);regimes.discard("unclassified")
        count, first, last = int(row[0]), row[1], row[2]
        today=utc_now().astimezone(ZoneInfo("Asia/Shanghai")).date()
        days=(date.fromisoformat(last[:10])-date.fromisoformat(first[:10])).days+1 if first and last else 0
        checks = {
            "minimum_days": days >= int(gate["minimum_days"]),
            "minimum_rebalances": count >= int(gate["minimum_rebalances"]),
            "minimum_decisions": decision_count >= int(gate["minimum_decisions"]),
            "minimum_market_regimes":len(regimes)>=int(gate["minimum_market_regimes"]),
            "state_continuity":metric_count==count and (not last or date.fromisoformat(last[:10])<=today),
        }
        return {
            "book_id": book_id,
            "status": "eligible_for_review" if all(checks.values()) else "insufficient_evidence",
            "observed": {"days": days, "rebalances": count, "decisions": decision_count,"market_regimes":sorted(regimes)},
            "required": gate,
            "checks": checks,
        }

    @staticmethod
    def _validate_simulation(simulation: dict[str, Any]) -> None:
        required = {"fills", "blocked", "nav", "cash"}
        missing = required - set(simulation)
        if missing:
            raise CompanionError(f"shadow simulation missing: {sorted(missing)}")
        for fill in simulation["fills"]:
            missing_fill = {"asset_id", "side", "quantity", "price", "gross", "trade_date"} - set(fill)
            if missing_fill:
                raise CompanionError(f"shadow fill missing: {sorted(missing_fill)}")
            if fill["side"] not in {"buy", "sell"}:
                raise CompanionError("invalid shadow fill side")
            if dec(fill["quantity"]) <= 0 or dec(fill["price"]) <= 0:
                raise CompanionError("shadow fill quantity and price must be positive")
        for blocked in simulation["blocked"]:
            if {"asset_id", "side", "reason"} - set(blocked):
                raise CompanionError("blocked shadow fill needs asset_id, side and reason")

    @staticmethod
    def _coerce_simulation(simulation: dict[str,Any],as_of:str)->dict[str,Any]:
        if {"fills","blocked","nav","cash"}<=set(simulation):return simulation
        if simulation.get("schema")!="investment-companion/portfolio-simulation/v1":return simulation
        fills=[]
        for fill in simulation.get("fills",[]):
            fills.append({
                "asset_id":fill["asset_id"],"side":fill["side"],"quantity":fill["quantity"],
                "price":fill["execution_price"],"gross":fill["notional"],
                "fee":fill.get("commission","0"),"tax":fill.get("stamp_duty","0"),
                "trade_date":fill["date"],"settle_date":fill.get("settle_date"),
                "reason":"deterministic_reality_fill",
            })
        blocked=[]
        for item in simulation.get("unfilled",[]):
            blocked.append({
                "asset_id":item["asset_id"],"side":item["side"],
                "quantity":item.get("requested_quantity","0"),"price":"0",
                "trade_date":item.get("date",as_of[:10]),"reason":item["reason"],
            })
        return {
            "fills":fills,"blocked":blocked,"nav":simulation["final_nav"],
            "cash":simulation["final_cash"],"metrics":{"source_simulation_hash":simulation.get("artifact_hash")},
            "source_simulation":simulation,
        }

    def _real_ledger_count(self) -> int:
        with self.db.connect() as con:
            return int(con.execute("SELECT COUNT(*) FROM ledger_entries").fetchone()[0])
