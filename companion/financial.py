from __future__ import annotations

import csv
import io
import json
from decimal import Decimal, InvalidOperation, ROUND_DOWN, ROUND_HALF_EVEN, ROUND_HALF_UP, getcontext
from typing import Any
from zoneinfo import ZoneInfo

from .foundation import CompanionError, canonical, digest, new_id
from .db import row_dict, rows_dict
from .timeutil import iso, parse

getcontext().prec = 34
ENGINE_VERSION = "financial-kernel-4.0.0"
RECONCILIATION_SCHEMA = "investment-companion.account-reconciliation/v2"
RECONCILIATION_SCOPES = ("cash", "positions", "valuations", "total_value")
SUPPORTED_MANDATE_CONSTRAINTS={
    "minimum_cash","max_position_weight","max_single_position_weight",
    "prohibited_asset_ids","forbidden_asset_ids","allowed_asset_ids",
    "max_participation_rate","max_turnover",
}


def dec(value: Any, field: str = "value") -> Decimal:
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError):
        raise CompanionError(f"invalid decimal for {field}: {value}")
    if not result.is_finite():
        raise CompanionError(f"non-finite decimal for {field}")
    return result


def dtext(value: Decimal) -> str:
    return format(value.normalize(), "f") if value else "0"


def reconciliation_is_full_match(reconciliation: dict[str, Any] | None) -> bool:
    """Only a v2 reconciliation covering every account dimension proves a match."""
    if not reconciliation or reconciliation.get("status") != "matched":
        return False
    computed = reconciliation.get("computed")
    if not isinstance(computed, dict):
        return False
    scope = computed.get("reconciliation")
    return bool(
        isinstance(scope, dict)
        and scope.get("schema") == RECONCILIATION_SCHEMA
        and scope.get("required_scopes") == list(RECONCILIATION_SCOPES)
        and scope.get("full_scope_matched") is True
    )


def mandate_constraints(mandate:dict[str,Any]|None)->dict[str,Any]:
    if mandate is None:return {}
    if not isinstance(mandate,dict):raise CompanionError("Mandate must be an object")
    if "hard_constraints" in mandate:
        constraints=mandate["hard_constraints"]
        if not isinstance(constraints,dict):raise CompanionError("Mandate hard_constraints must be an object")
        overlapping=SUPPORTED_MANDATE_CONSTRAINTS&set(mandate)
        if overlapping:raise CompanionError(f"Mandate mixes nested and flat hard constraints: {sorted(overlapping)}")
    else:
        constraints=mandate
    unknown=set(constraints)-SUPPORTED_MANDATE_CONSTRAINTS
    if unknown:raise CompanionError(f"unsupported Mandate hard constraints: {sorted(unknown)}")
    if {"max_position_weight","max_single_position_weight"}<=set(constraints):raise CompanionError("Mandate position-weight aliases are ambiguous")
    if {"prohibited_asset_ids","forbidden_asset_ids"}<=set(constraints):raise CompanionError("Mandate prohibited-asset aliases are ambiguous")
    for field in ("prohibited_asset_ids","forbidden_asset_ids","allowed_asset_ids"):
        if field in constraints:
            value=constraints[field]
            if not isinstance(value,list) or any(not isinstance(item,str) or not item.strip() for item in value) or len(value)!=len(set(value)):raise CompanionError(f"Mandate {field} must be a unique string list")
    if "minimum_cash" in constraints:
        minimum_cash=constraints["minimum_cash"]
        if not isinstance(minimum_cash,dict):raise CompanionError("Mandate minimum_cash must be a currency-to-amount object")
        for currency,amount in minimum_cash.items():
            if not isinstance(currency,str) or len(currency)!=3 or not currency.isascii() or not currency.isalpha() or currency!=currency.upper():raise CompanionError("Mandate minimum_cash currency keys must be uppercase ISO-style codes")
            if dec(amount,f"Mandate minimum_cash.{currency}")<0:raise CompanionError(f"Mandate minimum_cash.{currency} cannot be negative")
    return constraints


class FinancialKernel:
    def __init__(self, companion):
        self.c = companion
        self.db = companion.db

    def account_create(self, name: str, base_currency: str, institution: str | None = None, metadata: dict | None = None) -> dict:
        aid, now = new_id("acct"), iso()
        currency = base_currency.upper()
        with self.db.transaction() as con:
            con.execute("INSERT INTO accounts(id,name,institution,base_currency,metadata_json,created_at,updated_at) VALUES(?,?,?,?,?,?,?)", (aid,name,institution,currency,canonical(metadata or {}),now,now))
        return self.account_get(aid)

    def account_get(self, account_id: str) -> dict:
        with self.db.connect() as con: item=row_dict(con.execute("SELECT * FROM accounts WHERE id=?",(account_id,)).fetchone())
        if not item: raise CompanionError(f"account not found: {account_id}")
        return item

    def account_list(self) -> list[dict]:
        with self.db.connect() as con:return rows_dict(con.execute("SELECT * FROM accounts ORDER BY name").fetchall())

    def asset_upsert(self, asset_type: str, name: str, currency: str, identifiers: dict, metadata: dict | None = None) -> dict:
        if not identifiers: raise CompanionError("asset requires at least one external identifier")
        stable=digest(asset_type,currency.upper(),identifiers)[:16];aid=f"asset_{stable}";now=iso()
        with self.db.transaction() as con:
            con.execute("INSERT INTO assets(id,asset_type,name,currency,identifiers_json,metadata_json,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET name=excluded.name,metadata_json=excluded.metadata_json,updated_at=excluded.updated_at",(aid,asset_type,name,currency.upper(),canonical(identifiers),canonical(metadata or {}),now,now))
        return self.asset_get(aid)

    def asset_get(self, asset_id: str) -> dict:
        with self.db.connect() as con:item=row_dict(con.execute("SELECT * FROM assets WHERE id=?",(asset_id,)).fetchone())
        if not item:raise CompanionError(f"asset not found: {asset_id}")
        return item

    def asset_list(self) -> list[dict]:
        with self.db.connect() as con:return rows_dict(con.execute("SELECT * FROM assets ORDER BY asset_type,name").fetchall())

    def ledger_add(self, *, account_id: str, entry_type: str, occurred_at: str, amount: Any, currency: str, source: str, asset_id: str | None = None, quantity: Any | None = None, price: Any | None = None, fee: Any = "0", settled_at: str | None = None, external_id: str | None = None, metadata: dict | None = None, status: str = "needs_confirmation") -> dict:
        allowed={"trade","cash_deposit","cash_withdrawal","dividend","interest","fee","tax","transfer","fx_conversion","corporate_action","opening_balance","reversal","correction"}
        if entry_type not in allowed:raise CompanionError("invalid ledger entry type")
        if status not in {"draft","needs_confirmation"}:raise CompanionError("new ledger entries must be draft or needs_confirmation")
        self.account_get(account_id)
        if asset_id:self.asset_get(asset_id)
        amount_d,fee_d=dec(amount,"amount"),dec(fee,"fee")
        qty_d=dec(quantity,"quantity") if quantity is not None else None
        price_d=dec(price,"price") if price is not None else None
        if entry_type=="trade":
            if asset_id is None or qty_d is None or price_d is None:raise CompanionError("trade requires asset_id, quantity, and price")
            asset=self.asset_get(asset_id)
            if qty_d==0 or price_d<=0:raise CompanionError("trade quantity must be non-zero and price must be positive")
            if currency.upper()!=asset["currency"]:raise CompanionError("trade currency differs from asset currency")
            if amount_d!=-(qty_d*price_d):raise CompanionError("trade amount must equal -(quantity * price); fees are recorded separately")
        fp=digest(account_id,entry_type,occurred_at,dtext(amount_d),currency.upper(),asset_id,dtext(qty_d) if qty_d is not None else None,dtext(price_d) if price_d is not None else None,dtext(fee_d),source,external_id)
        eid,now=new_id("led"),iso()
        with self.db.transaction() as con:
            con.execute("INSERT OR IGNORE INTO ledger_entries(id,account_id,entry_type,asset_id,occurred_at,settled_at,quantity_text,price_text,amount_text,currency,fee_text,status,source,external_id,metadata_json,fingerprint,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",(eid,account_id,entry_type,asset_id,occurred_at,settled_at,dtext(qty_d) if qty_d is not None else None,dtext(price_d) if price_d is not None else None,dtext(amount_d),currency.upper(),dtext(fee_d),status,source,external_id,canonical(metadata or {}),fp,now))
            row=con.execute("SELECT * FROM ledger_entries WHERE fingerprint=?",(fp,)).fetchone()
        return row_dict(row)

    def ledger_get(self, entry_id: str) -> dict:
        with self.db.connect() as con:item=row_dict(con.execute("SELECT * FROM ledger_entries WHERE id=?",(entry_id,)).fetchone())
        if not item:raise CompanionError(f"ledger entry not found: {entry_id}")
        return item

    def ledger_list(self, account_id: str | None = None, status: str | None = None, limit: int = 200) -> list[dict]:
        q,p="SELECT * FROM ledger_entries WHERE 1=1",[]
        if account_id:q+=" AND account_id=?";p.append(account_id)
        if status:q+=" AND status=?";p.append(status)
        q+=" ORDER BY occurred_at,id LIMIT ?";p.append(limit)
        with self.db.connect() as con:return rows_dict(con.execute(q,p).fetchall())

    def confirmed_ledger_hash(self,exclude_ids:list[str]|None=None)->str:
        excluded=set(exclude_ids or [])
        with self.db.connect() as con:rows=[row for row in rows_dict(con.execute("SELECT * FROM ledger_entries WHERE status IN ('confirmed','reversed') ORDER BY occurred_at,id").fetchall()) if row["id"] not in excluded]
        return digest("confirmed-ledger-v1",rows)

    def ledger_import_csv(self,content:str,source:str="broker_csv")->dict:
        required={"account_id","entry_type","occurred_at","amount","currency"};reader=csv.DictReader(io.StringIO(content));fields=set(reader.fieldnames or [])
        missing=required-fields
        if missing:raise CompanionError(f"CSV missing columns: {sorted(missing)}")
        created=[];errors=[]
        for number,row in enumerate(reader,start=2):
            try:
                item=self.ledger_add(account_id=row["account_id"],entry_type=row["entry_type"],occurred_at=row["occurred_at"],amount=row["amount"],currency=row["currency"],source=source,asset_id=row.get("asset_id") or None,quantity=row.get("quantity") or None,price=row.get("price") or None,fee=row.get("fee") or "0",settled_at=row.get("settled_at") or None,external_id=row.get("external_id") or None,metadata={"csv_row":number})
                created.append(item["id"])
            except Exception as e:errors.append({"row":number,"error":str(e)})
        return {"created":created,"errors":errors,"requires_confirmation":len(created),"total_rows":len(created)+len(errors)}

    def ledger_confirm(self, entry_id: str) -> dict:
        with self.db.transaction() as con:
            row=con.execute("SELECT status FROM ledger_entries WHERE id=?",(entry_id,)).fetchone()
            if not row:raise CompanionError(f"ledger entry not found: {entry_id}")
            if row[0] not in {"draft","needs_confirmation"}:raise CompanionError(f"entry cannot be confirmed from {row[0]}")
            con.execute("UPDATE ledger_entries SET status='confirmed',confirmed_at=? WHERE id=?",(iso(),entry_id))
        return self.ledger_get(entry_id)

    def ledger_reverse(self, entry_id: str, reason: str, occurred_at: str | None = None) -> dict:
        original=self.ledger_get(entry_id)
        if original["status"]!="confirmed":raise CompanionError("only confirmed entry can be reversed")
        reversal=self.ledger_add(account_id=original["account_id"],entry_type="reversal",asset_id=original.get("asset_id"),occurred_at=occurred_at or iso(),quantity=dtext(-dec(original["quantity_text"])) if original.get("quantity_text") else None,price=original.get("price_text"),amount=dtext(-dec(original["amount_text"])),currency=original["currency"],fee=dtext(-dec(original["fee_text"])),source="reversal",external_id=f"reverse:{entry_id}",metadata={"reason":reason,"reversal_of":entry_id})
        with self.db.transaction() as con:
            con.execute("UPDATE ledger_entries SET reversal_of=? WHERE id=?",(entry_id,reversal["id"]))
        confirmed=self.ledger_confirm(reversal["id"])
        with self.db.transaction() as con:con.execute("UPDATE ledger_entries SET status='reversed' WHERE id=?",(entry_id,))
        return confirmed

    def market_add(self, asset_id: str, metric: str, value: Any, observed_at: str, source: str, quality: str = "healthy", currency: str | None = None, metadata: dict | None = None) -> dict:
        self.asset_get(asset_id);value_d=dec(value);fp=digest(asset_id,metric,dtext(value_d),observed_at,source);mid=new_id("mkt");now=iso()
        allowed={"healthy","stale","partial","conflicting","unauthorized","failed","unknown"}
        if quality not in allowed:raise CompanionError("invalid market quality")
        with self.db.transaction() as con:
            con.execute("INSERT OR IGNORE INTO market_snapshots(id,asset_id,metric,value_text,currency,observed_at,source,quality,metadata_json,fingerprint,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",(mid,asset_id,metric,dtext(value_d),currency.upper() if currency else None,observed_at,source,quality,canonical(metadata or {}),fp,now));row=con.execute("SELECT * FROM market_snapshots WHERE fingerprint=?",(fp,)).fetchone()
        return row_dict(row)

    def portfolio_state(self, as_of: str, account_id: str | None = None, prices: dict[str,Any] | None = None) -> dict:
        q="SELECT * FROM ledger_entries WHERE status IN ('confirmed','reversed') AND occurred_at<=?";p=[as_of]
        if account_id:q+=" AND account_id=?";p.append(account_id)
        with self.db.connect() as con:entries=rows_dict(con.execute(q,p).fetchall())
        cash:dict[str,Decimal]={};positions:dict[str,Decimal]={};warnings=[]
        for e in entries:
            cash[e["currency"]]=cash.get(e["currency"],Decimal(0))+dec(e["amount_text"])-dec(e["fee_text"])
            if e.get("asset_id") and e.get("quantity_text") is not None:positions[e["asset_id"]]=positions.get(e["asset_id"],Decimal(0))+dec(e["quantity_text"])
        position_rows=[];total_by_currency=dict(cash)
        for aid,qty in sorted(positions.items()):
            if qty==0:continue
            asset=self.asset_get(aid);price=None;quality=None;observed=None
            if prices and aid in prices:price=dec(prices[aid]);quality="provided"
            else:
                with self.db.connect() as con:r=con.execute("SELECT * FROM market_snapshots WHERE asset_id=? AND metric='close' AND observed_at<=? ORDER BY observed_at DESC LIMIT 1",(aid,as_of)).fetchone()
                if r:price=dec(r["value_text"]);quality=r["quality"];observed=r["observed_at"]
            value=qty*price if price is not None else None
            if value is None:warnings.append(f"missing price for {aid}")
            else:total_by_currency[asset["currency"]]=total_by_currency.get(asset["currency"],Decimal(0))+value
            position_rows.append({"asset_id":aid,"name":asset["name"],"quantity":dtext(qty),"price":dtext(price) if price is not None else None,"market_value":dtext(value) if value is not None else None,"currency":asset["currency"],"quality":quality,"observed_at":observed})
        output={"as_of":as_of,"account_id":account_id,"cash":{k:dtext(v) for k,v in sorted(cash.items())},"positions":position_rows,"total_by_currency":{k:dtext(v) for k,v in sorted(total_by_currency.items())},"warnings":warnings}
        calc=self._record("portfolio_state","Rebuild portfolio from confirmed ledger",as_of,{"account_id":account_id,"prices":prices or {}},{},{"cash":"sum(amount-fee)","position_quantity":"sum(quantity)","market_value":"quantity*price"},output,warnings)
        output["calculation_id"]=calc["id"]
        return output

    def trade_impact(self, as_of: str, account_id: str, asset_id: str, quantity: Any, price: Any, fee: Any = "0", mandate: dict | None = None) -> dict:
        before=self.portfolio_state(as_of,account_id);qty,px,fee_d=dec(quantity),dec(price),dec(fee);asset=self.asset_get(asset_id)
        positions={p["asset_id"]:dec(p["quantity"]) for p in before["positions"]};positions[asset_id]=positions.get(asset_id,Decimal(0))+qty
        cash={k:dec(v) for k,v in before["cash"].items()};cash[asset["currency"]]=cash.get(asset["currency"],Decimal(0))-(qty*px)-fee_d
        violations=[]
        if cash[asset["currency"]]<0:violations.append({"rule":"nonnegative_cash","currency":asset["currency"],"value":dtext(cash[asset["currency"]])})
        if positions[asset_id]<0:violations.append({"rule":"no_short_position","asset_id":asset_id,"value":dtext(positions[asset_id])})
        constraints=mandate_constraints(mandate)
        if constraints:
            minimum=constraints.get("minimum_cash",{}).get(asset["currency"])
            if minimum is not None and cash[asset["currency"]]<dec(minimum):violations.append({"rule":"minimum_cash","required":str(minimum),"actual":dtext(cash[asset["currency"]])})
        output={"before":before,"after":{"cash":{k:dtext(v) for k,v in cash.items()},"positions":{k:dtext(v) for k,v in positions.items()}},"delta":{"asset_id":asset_id,"quantity":dtext(qty),"cash":dtext(-(qty*px)-fee_d),"currency":asset["currency"]},"violations":violations,"blocked":bool(violations)}
        calc=self._record("trade_impact","Simulate trade without changing ledger",as_of,{"account_id":account_id,"asset_id":asset_id,"quantity":dtext(qty),"price":dtext(px),"fee":dtext(fee_d)},{"mandate":mandate or {}},{"cash_delta":"-(quantity*price)-fee"},output,[])
        output["calculation_id"]=calc["id"];return output

    def portfolio_rebalance_plan(
        self,
        *,
        as_of:str,
        account_id:str,
        target_manifest_id:str,
        market_snapshot_ids:list[str],
        reality_spec:dict[str,Any],
        mandate:dict[str,Any],
        max_price_age_seconds:int=129600,
    )->dict[str,Any]:
        """Project target weights onto the user's hard constraints without changing Ledger.

        The target is a soft research objective; Mandate, cash, market state and
        exchange mechanics are hard constraints.  Every deviation is returned,
        and an unexecutable plan has no actionable orders.
        """

        from .quant_runtime import RealitySpec, TARGET_WEIGHTS_SCHEMA, verify_artifact_hash

        account=self.account_get(account_id);now=parse(as_of)
        if isinstance(max_price_age_seconds,bool) or not isinstance(max_price_age_seconds,int) or max_price_age_seconds<=0:
            raise CompanionError("max_price_age_seconds must be a positive integer")
        reality=RealitySpec.from_value(reality_spec).to_dict()
        if canonical(reality)!=canonical(reality_spec):raise CompanionError("portfolio plan requires a complete normalized RealitySpec")
        if account["base_currency"]!=reality["currency"]:raise CompanionError("account currency differs from RealitySpec")
        target_manifest=self.c.data.manifest_get(target_manifest_id,verify=True)
        if target_manifest["kind"]!="target_weights" or target_manifest["status"]!="ready":raise CompanionError("portfolio plan requires a ready target_weights manifest")
        target=target_manifest["manifest"].get("manifest",{})
        if target_manifest["schema_version"]!=TARGET_WEIGHTS_SCHEMA or target.get("schema")!=TARGET_WEIGHTS_SCHEMA:
            raise CompanionError("portfolio plan target_weights uses an unsupported schema")
        if not verify_artifact_hash(target):raise CompanionError("portfolio plan target_weights hash is invalid")
        local_date=now.astimezone(ZoneInfo("Asia/Shanghai")).date()
        if target.get("effective_on")!=local_date.isoformat():raise CompanionError("portfolio plan as_of date must equal target effective_on")
        if target.get("warnings"):raise CompanionError("portfolio plan refuses target_weights with unresolved warnings")
        if not isinstance(market_snapshot_ids,list) or not market_snapshot_ids or any(not isinstance(item,str) or not item for item in market_snapshot_ids) or len(market_snapshot_ids)!=len(set(market_snapshot_ids)):
            raise CompanionError("portfolio plan requires unique frozen Market Snapshot IDs")
        prices:dict[str,dict[str,Any]]={}
        with self.db.connect() as con:
            for market_id in market_snapshot_ids:
                item=row_dict(con.execute("SELECT * FROM market_snapshots WHERE id=?",(market_id,)).fetchone())
                if not item:raise CompanionError(f"market snapshot not found: {market_id}")
                if item["metric"]!="close" or item["quality"]!="healthy":raise CompanionError("portfolio plan accepts only healthy close Market Snapshots")
                observed=parse(item["observed_at"])
                if observed>now or (now-observed).total_seconds()>max_price_age_seconds:raise CompanionError("portfolio plan Market Snapshot is future-dated or stale")
                if item["asset_id"] in prices:raise CompanionError("portfolio plan has duplicate prices for an asset")
                if item.get("currency") and item["currency"]!=account["base_currency"]:raise CompanionError("portfolio plan Market Snapshot currency mismatch")
                prices[item["asset_id"]]=item
        before=self.portfolio_state(as_of,account_id,prices={asset:item["value_text"] for asset,item in prices.items()})
        if before["warnings"]:raise CompanionError(f"portfolio plan cannot value the current account: {before['warnings']}")
        current={row["asset_id"]:dec(row["quantity"],"current quantity") for row in before["positions"]}
        weight_rows=target.get("weights")
        if not isinstance(weight_rows,list):raise CompanionError("portfolio plan target weights must be an array")
        weights:dict[str,Decimal]={}
        for index,row in enumerate(weight_rows):
            if not isinstance(row,dict):raise CompanionError(f"portfolio plan target weight {index} must be an object")
            asset=row.get("asset_id")
            if not isinstance(asset,str) or not asset.strip():raise CompanionError(f"portfolio plan target weight {index} has an invalid asset_id")
            if asset in weights:raise CompanionError(f"portfolio plan has duplicate target weight for {asset}")
            weights[asset]=dec(row.get("weight"),"target weight")
        if any(weight<0 for weight in weights.values()):raise CompanionError("portfolio plan target weights cannot be negative")
        required_assets=set(current)|set(weights)
        missing_prices=sorted(required_assets-set(prices))
        if missing_prices:raise CompanionError(f"portfolio plan lacks frozen prices: {missing_prices}")
        cash=dec(before["cash"].get(account["base_currency"],"0"),"account cash")
        nav=cash+sum((quantity*dec(prices[asset]["value_text"],"price") for asset,quantity in current.items()),Decimal("0"))
        if nav<=0:raise CompanionError("portfolio plan requires positive account NAV")
        cash_weight=dec(target.get("cash_weight"),"target cash_weight")
        if cash_weight<0 or cash_weight>1 or sum(weights.values(),cash_weight)!=Decimal("1"):raise CompanionError("portfolio plan target weights and cash must sum to one")
        constraints=mandate_constraints(mandate)
        minimum_cash=dec((constraints.get("minimum_cash") or {}).get(account["base_currency"],"0"),"Mandate minimum_cash")
        if minimum_cash<0:raise CompanionError("Mandate minimum_cash cannot be negative")
        reserve=max(minimum_cash,nav*cash_weight)
        if reserve>nav:
            output={"status":"infeasible","conflicts":[{"rule":"minimum_cash","required":dtext(reserve),"nav":dtext(nav)}],"actions":[],"requested_actions":[],"deviations":[],"before":before,"target_manifest_id":target_manifest_id,"market_snapshot_ids":market_snapshot_ids}
            calc=self._record("portfolio_rebalance_plan","Joint target/Mandate portfolio projection",as_of,{"account_id":account_id,"target_manifest_id":target_manifest_id,"market_snapshot_ids":market_snapshot_ids,"max_price_age_seconds":max_price_age_seconds},{"mandate":mandate,"reality_spec":reality},{"objective":"minimize target-weight deviation subject to hard personal/execution constraints"},output,[]);output["calculation_id"]=calc["id"];return output
        max_weight=dec(constraints.get("max_position_weight",constraints.get("max_single_position_weight","1")),"Mandate max_position_weight")
        if max_weight<=0 or max_weight>1:raise CompanionError("Mandate max_position_weight must be within (0,1]")
        prohibited=set(constraints.get("prohibited_asset_ids",constraints.get("forbidden_asset_ids",[])) or [])
        allowed_raw=constraints.get("allowed_asset_ids");allowed=set(allowed_raw) if allowed_raw is not None else None
        if allowed is not None and not allowed:raise CompanionError("Mandate allowed_asset_ids cannot be empty")
        participation=constraints.get("max_participation_rate")
        participation_rate=dec(participation,"Mandate max_participation_rate") if participation is not None else None
        if participation_rate is not None and not Decimal("0")<participation_rate<=Decimal("1"):raise CompanionError("Mandate max_participation_rate must be within (0,1]")
        risky=sum(weights.values(),Decimal("0"));scale=(nav-reserve)/risky if risky else Decimal("0")
        lot=int(reality["lot_size"]);desired:dict[str,int]={};deviations=[];conflicts=[]
        for asset in sorted(required_assets):
            quantity=current.get(asset,Decimal("0"))
            if quantity!=quantity.to_integral_value():conflicts.append({"rule":"whole_share_position","asset_id":asset,"quantity":dtext(quantity)});continue
            forbidden=asset in prohibited or allowed is not None and asset not in allowed
            target_value=Decimal("0") if forbidden else min(scale*weights.get(asset,Decimal("0")),nav*max_weight)
            price=dec(prices[asset]["value_text"],"price")
            target_quantity=int((target_value/price/lot).to_integral_value(rounding=ROUND_DOWN))*lot
            desired[asset]=target_quantity
            if forbidden and weights.get(asset,Decimal("0"))>0:deviations.append({"asset_id":asset,"reason":"mandate_asset_restriction","requested_weight":dtext(weights[asset]),"planned_weight":"0"})
            elif weights.get(asset,Decimal("0"))>max_weight:deviations.append({"asset_id":asset,"reason":"mandate_concentration_cap","requested_weight":dtext(weights[asset]),"planned_weight":dtext(max_weight)})
        requested=[]
        for asset in sorted(desired):
            delta=Decimal(desired[asset])-current.get(asset,Decimal("0"))
            if delta:requested.append({"asset_id":asset,"side":"buy" if delta>0 else "sell","quantity":dtext(abs(delta))})
        projected=dict(current);projected_cash=cash;actions=[]
        for side in ("sell","buy"):
            for request in (item for item in requested if item["side"]==side):
                asset=request["asset_id"];quantity=int(dec(request["quantity"]));market=prices[asset];metadata=market.get("metadata",{})
                block=("suspended" if metadata.get("suspended") else "upper_limit_buy" if side=="buy" and metadata.get("at_upper_limit") else "lower_limit_sell" if side=="sell" and metadata.get("at_lower_limit") else None)
                if block:conflicts.append({"rule":"market_not_executable","asset_id":asset,"reason":block});continue
                if side=="sell" and reality["t_plus_one"]:
                    with self.db.connect() as con:
                        same_day=rows_dict(con.execute("SELECT quantity_text,occurred_at FROM ledger_entries WHERE account_id=? AND asset_id=? AND status='confirmed' AND quantity_text IS NOT NULL",(account_id,asset)).fetchall())
                    locked=sum((max(Decimal("0"),dec(row["quantity_text"])) for row in same_day if parse(row["occurred_at"]).astimezone(ZoneInfo("Asia/Shanghai")).date()==local_date),Decimal("0"))
                    sellable=max(Decimal("0"),current.get(asset,Decimal("0"))-locked)
                    if quantity>sellable:
                        quantity=int(sellable)
                        deviations.append({"asset_id":asset,"reason":"t_plus_one_locked","planned_quantity":str(quantity)})
                quote=dec(market["value_text"],"price");slip=dec(reality["slippage_bps"])/Decimal("10000");tick=dec(reality["price_tick"]);quantum=dec(reality["money_quantum"])
                execution_price=(quote*(Decimal("1")+slip if side=="buy" else Decimal("1")-slip)).quantize(tick,rounding=ROUND_HALF_UP)
                if participation_rate is not None:
                    amount=metadata.get("average_daily_amount",metadata.get("daily_amount"))
                    if amount is None:conflicts.append({"rule":"liquidity_data_missing","asset_id":asset});continue
                    capacity=(dec(amount,"daily amount")*participation_rate/execution_price).to_integral_value(rounding=ROUND_DOWN)
                    capacity=int(capacity/lot)*lot if side=="buy" else int(capacity)
                    if quantity>capacity:
                        quantity=max(0,capacity);deviations.append({"asset_id":asset,"reason":"liquidity_cap","planned_quantity":str(quantity)})
                if side=="buy":
                    while quantity>0:
                        notional=(execution_price*quantity).quantize(quantum,rounding=ROUND_HALF_UP)
                        fee=max(dec(reality["minimum_commission"]),notional*dec(reality["commission_rate"])).quantize(quantum,rounding=ROUND_HALF_UP)
                        if projected_cash-notional-fee>=reserve:break
                        quantity-=lot
                    if quantity<int(dec(request["quantity"])):deviations.append({"asset_id":asset,"reason":"cash_or_fee_constraint","planned_quantity":str(max(0,quantity))})
                if quantity<=0:continue
                notional=(execution_price*quantity).quantize(quantum,rounding=ROUND_HALF_UP)
                commission=max(dec(reality["minimum_commission"]),notional*dec(reality["commission_rate"])).quantize(quantum,rounding=ROUND_HALF_UP)
                tax=(notional*dec(reality["sell_stamp_duty_rate"])).quantize(quantum,rounding=ROUND_HALF_UP) if side=="sell" else Decimal("0")
                cash_delta=notional-commission-tax if side=="sell" else -notional-commission
                projected_cash+=cash_delta;projected[asset]=projected.get(asset,Decimal("0"))+(quantity if side=="buy" else -quantity)
                actions.append({"asset_id":asset,"side":side,"quantity":str(quantity),"quote":dtext(quote),"execution_price":dtext(execution_price),"notional":dtext(notional),"commission":dtext(commission),"tax":dtext(tax),"cash_delta":dtext(cash_delta),"market_snapshot_id":market["id"]})
        if projected_cash<minimum_cash:conflicts.append({"rule":"minimum_cash","required":dtext(minimum_cash),"projected":dtext(projected_cash)})
        for asset,quantity in projected.items():
            if quantity<0:conflicts.append({"rule":"no_short_position","asset_id":asset,"quantity":dtext(quantity)})
            if quantity>0 and (asset in prohibited or allowed is not None and asset not in allowed):conflicts.append({"rule":"restricted_asset_remaining","asset_id":asset,"quantity":dtext(quantity)})
        projected_values={asset:quantity*dec(prices[asset]["value_text"]) for asset,quantity in projected.items() if quantity}
        projected_nav=projected_cash+sum(projected_values.values(),Decimal("0"))
        for asset,value in projected_values.items():
            if projected_nav>0 and value/projected_nav>max_weight+Decimal("0.0000001"):conflicts.append({"rule":"max_position_weight","asset_id":asset,"actual":dtext(value/projected_nav),"maximum":dtext(max_weight)})
        turnover=sum((dec(action["notional"]) for action in actions),Decimal("0"))/nav
        maximum_turnover=constraints.get("max_turnover")
        if maximum_turnover is not None and turnover>dec(maximum_turnover,"Mandate max_turnover"):conflicts.append({"rule":"max_turnover","actual":dtext(turnover),"maximum":str(maximum_turnover)})
        status="feasible" if not conflicts else "infeasible"
        output={"status":status,"conflicts":conflicts,"actions":actions if status=="feasible" else [],"requested_actions":requested,"deviations":deviations,"before":before,"projected":{"cash":dtext(projected_cash),"positions":{asset:dtext(quantity) for asset,quantity in sorted(projected.items()) if quantity},"nav":dtext(projected_nav),"turnover":dtext(turnover)},"target_manifest_id":target_manifest_id,"target_artifact_hash":target["artifact_hash"],"market_snapshot_ids":market_snapshot_ids,"reality_spec":reality}
        warnings=[f"target deviation: {item['asset_id']} {item['reason']}" for item in deviations]
        calc=self._record("portfolio_rebalance_plan","Joint target/Mandate portfolio projection",as_of,{"account_id":account_id,"target_manifest_id":target_manifest_id,"market_snapshot_ids":market_snapshot_ids,"max_price_age_seconds":max_price_age_seconds},{"mandate":mandate,"reality_spec":reality},{"objective":"minimize target-weight deviation subject to hard personal/execution constraints","order":"sells before buys","prices":"frozen healthy Market Snapshots"},output,warnings)
        output["calculation_id"]=calc["id"]
        return output

    def max_purchase(self,as_of:str,account_id:str,asset_id:str,price:Any,minimum_cash:Any="0",fee:Any="0",lot_size:Any="1")->dict:
        state=self.portfolio_state(as_of,account_id);asset=self.asset_get(asset_id);available=dec(state["cash"].get(asset["currency"],"0"))-dec(minimum_cash)-dec(fee);px=dec(price);lot=dec(lot_size)
        if px<=0 or lot<=0:raise CompanionError("price and lot_size must be positive")
        raw=max(Decimal(0),available)/(px*lot);lots=raw.to_integral_value(rounding="ROUND_FLOOR");quantity=lots*lot;cost=quantity*px+dec(fee)
        output={"asset_id":asset_id,"currency":asset["currency"],"available_cash":dtext(available),"max_quantity":dtext(quantity),"estimated_cost":dtext(cost),"remaining_cash":dtext(dec(state["cash"].get(asset["currency"],"0"))-cost),"lot_size":dtext(lot)}
        calc=self._record("max_purchase","Maximum purchase under cash floor",as_of,{"account_id":account_id,"asset_id":asset_id,"price":str(price),"minimum_cash":str(minimum_cash),"fee":str(fee),"lot_size":str(lot_size)},{},{"max_quantity":"floor((cash-minimum_cash-fee)/(price*lot_size))*lot_size"},output,state["warnings"]);output["calculation_id"]=calc["id"];return output

    def portfolio_exposure(self,as_of:str,account_id:str,prices:dict[str,Any],base_currency:str)->dict:
        state=self.portfolio_state(as_of,account_id,prices);values={};warnings=list(state["warnings"]);total=Decimal(0)
        for p in state["positions"]:
            if p["currency"]!=base_currency:warnings.append(f"missing FX conversion for {p['asset_id']} {p['currency']}->{base_currency}");continue
            value=dec(p["market_value"]);values[p["asset_id"]]=value;total+=value
        cash=dec(state["cash"].get(base_currency,"0"));total+=cash
        weights={aid:dtext(value/total) for aid,value in values.items()} if total else {}
        output={"as_of":as_of,"account_id":account_id,"base_currency":base_currency,"total_value":dtext(total),"asset_weights":weights,"cash_weight":dtext(cash/total) if total else None,"warnings":warnings}
        calc=self._record("portfolio_exposure","Portfolio weights in one base currency",as_of,{"account_id":account_id,"prices":prices,"base_currency":base_currency},{},{"weight":"market_value/total_value"},output,warnings);output["calculation_id"]=calc["id"];return output

    def calculation_get(self, calculation_id: str) -> dict:
        with self.db.connect() as con:item=row_dict(con.execute("SELECT * FROM calculations WHERE id=?",(calculation_id,)).fetchone())
        if not item:raise CompanionError(f"calculation not found: {calculation_id}")
        expected=digest(item["engine_version"],item["kind"],item["as_of"],item["inputs"],item["assumptions"],item["formulas"],item["outputs"],item["warnings"])
        if expected!=item["reproducibility_hash"]:raise CompanionError(f"calculation reproducibility hash mismatch: {calculation_id}")
        return item

    def calculation_list(self, kind: str | None = None, limit: int = 50) -> list[dict]:
        """List verified deterministic calculations without exposing SQL callers."""
        if limit <= 0 or limit > 500:
            raise CompanionError("calculation limit must be within 1..500")
        query, params = "SELECT id FROM calculations", []
        if kind:
            query += " WHERE kind=?"
            params.append(kind)
        query += " ORDER BY created_at DESC,id DESC LIMIT ?"
        params.append(limit)
        with self.db.connect() as con:
            calculation_ids = [row["id"] for row in con.execute(query, params).fetchall()]
        return [self.calculation_get(item) for item in calculation_ids]

    def calculation_record(
        self, kind, purpose, as_of, inputs, assumptions, formulas, outputs, warnings
    ) -> dict:
        """Public deterministic Calculation registry boundary."""
        return self._record(kind, purpose, as_of, inputs, assumptions, formulas, outputs, warnings)

    def _record(self, kind,purpose,as_of,inputs,assumptions,formulas,outputs,warnings):
        rh=digest(ENGINE_VERSION,kind,as_of,inputs,assumptions,formulas,outputs,warnings);cid=new_id("calc");now=iso()
        with self.db.transaction() as con:
            con.execute("INSERT OR IGNORE INTO calculations(id,kind,purpose,engine_version,as_of,inputs_json,assumptions_json,formulas_json,outputs_json,warnings_json,reproducibility_hash,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",(cid,kind,purpose,ENGINE_VERSION,as_of,canonical(inputs),canonical(assumptions),canonical(formulas),canonical(outputs),canonical(warnings),rh,now));row=con.execute("SELECT * FROM calculations WHERE reproducibility_hash=?",(rh,)).fetchone()
        return row_dict(row)

    def reconcile(self, account_id: str, as_of: str, statement: dict, source_ref: str | None = None) -> dict:
        if not isinstance(statement, dict):
            raise CompanionError("reconciliation statement must be an object")
        computed = self.portfolio_state(as_of, account_id)
        differences: list[dict[str, Any]] = []
        scope_status: dict[str, dict[str, Any]] = {}

        def mapping(field: str) -> dict[str, Any] | None:
            if field not in statement:
                return None
            value = statement[field]
            if not isinstance(value, dict):
                raise CompanionError(f"reconciliation statement {field} must be an object")
            return value

        def missing_scope(scope: str, fields: list[str]) -> None:
            differences.append(
                {"kind": "unverified_scope", "scope": scope, "missing_fields": fields}
            )
            scope_status[scope] = {
                "status": "unverified",
                "difference_count": 1,
            }

        def finish_scope(scope: str, start: int) -> None:
            count = len(differences) - start
            scope_status[scope] = {
                "status": "matched" if count == 0 else "mismatched",
                "difference_count": count,
            }

        cash = mapping("cash")
        if cash is None:
            missing_scope("cash", ["cash"])
        else:
            start = len(differences)
            expected_cash = {
                currency: dec(value, f"statement.cash.{currency}")
                for currency, value in cash.items()
            }
            actual_cash = {
                currency: dec(value, f"computed.cash.{currency}")
                for currency, value in computed["cash"].items()
            }
            for currency in sorted(set(expected_cash) | set(actual_cash)):
                expected = expected_cash.get(currency, Decimal(0))
                actual = actual_cash.get(currency, Decimal(0))
                delta = actual - expected
                if delta:
                    differences.append(
                        {
                            "kind": "cash",
                            "scope": "cash",
                            "currency": currency,
                            "statement": dtext(expected),
                            "computed": dtext(actual),
                            "difference": dtext(delta),
                        }
                    )
            finish_scope("cash", start)

        positions = mapping("positions")
        if positions is None:
            missing_scope("positions", ["positions"])
        else:
            start = len(differences)
            expected_positions = {
                asset_id: dec(value, f"statement.positions.{asset_id}")
                for asset_id, value in positions.items()
            }
            actual_positions = {
                item["asset_id"]: dec(item["quantity"], f"computed.positions.{item['asset_id']}")
                for item in computed["positions"]
            }
            for asset_id in sorted(set(expected_positions) | set(actual_positions)):
                expected = expected_positions.get(asset_id, Decimal(0))
                actual = actual_positions.get(asset_id, Decimal(0))
                delta = actual - expected
                if delta:
                    differences.append(
                        {
                            "kind": "position",
                            "scope": "positions",
                            "asset_id": asset_id,
                            "statement": dtext(expected),
                            "computed": dtext(actual),
                            "difference": dtext(delta),
                        }
                    )
            finish_scope("positions", start)

        position_values = mapping("position_values")
        position_totals = mapping("position_total_by_currency")
        valuation_missing = [
            field
            for field, value in (
                ("position_values", position_values),
                ("position_total_by_currency", position_totals),
            )
            if value is None
        ]
        if valuation_missing:
            missing_scope("valuations", valuation_missing)
        else:
            start = len(differences)
            expected_values = {
                asset_id: dec(value, f"statement.position_values.{asset_id}")
                for asset_id, value in position_values.items()
            }
            actual_values: dict[str, Decimal | None] = {}
            actual_position_totals: dict[str, Decimal] = {}
            for item in computed["positions"]:
                asset_id = item["asset_id"]
                if item["market_value"] is None:
                    actual_values[asset_id] = None
                    differences.append(
                        {
                            "kind": "valuation_unavailable",
                            "scope": "valuations",
                            "asset_id": asset_id,
                        }
                    )
                    continue
                value = dec(item["market_value"], f"computed.position_values.{asset_id}")
                actual_values[asset_id] = value
                currency = item["currency"]
                actual_position_totals[currency] = (
                    actual_position_totals.get(currency, Decimal(0)) + value
                )
            for asset_id in sorted(set(expected_values) | set(actual_values)):
                expected = expected_values.get(asset_id, Decimal(0))
                actual = actual_values.get(asset_id)
                if actual is None:
                    if asset_id not in actual_values:
                        differences.append(
                            {
                                "kind": "position_value",
                                "scope": "valuations",
                                "asset_id": asset_id,
                                "statement": dtext(expected),
                                "computed": "0",
                                "difference": dtext(-expected),
                            }
                        )
                    continue
                delta = actual - expected
                if delta:
                    differences.append(
                        {
                            "kind": "position_value",
                            "scope": "valuations",
                            "asset_id": asset_id,
                            "statement": dtext(expected),
                            "computed": dtext(actual),
                            "difference": dtext(delta),
                        }
                    )
            expected_position_totals = {
                currency: dec(value, f"statement.position_total_by_currency.{currency}")
                for currency, value in position_totals.items()
            }
            for currency in sorted(
                set(expected_position_totals) | set(actual_position_totals)
            ):
                expected = expected_position_totals.get(currency, Decimal(0))
                actual = actual_position_totals.get(currency, Decimal(0))
                delta = actual - expected
                if delta:
                    differences.append(
                        {
                            "kind": "position_total",
                            "scope": "valuations",
                            "currency": currency,
                            "statement": dtext(expected),
                            "computed": dtext(actual),
                            "difference": dtext(delta),
                        }
                    )
            finish_scope("valuations", start)

        totals = mapping("total_by_currency")
        if totals is None:
            missing_scope("total_value", ["total_by_currency"])
        else:
            start = len(differences)
            expected_totals = {
                currency: dec(value, f"statement.total_by_currency.{currency}")
                for currency, value in totals.items()
            }
            actual_totals = {
                currency: dec(value, f"computed.total_by_currency.{currency}")
                for currency, value in computed["total_by_currency"].items()
            }
            for currency in sorted(set(expected_totals) | set(actual_totals)):
                expected = expected_totals.get(currency, Decimal(0))
                actual = actual_totals.get(currency, Decimal(0))
                delta = actual - expected
                if delta:
                    differences.append(
                        {
                            "kind": "total_value",
                            "scope": "total_value",
                            "currency": currency,
                            "statement": dtext(expected),
                            "computed": dtext(actual),
                            "difference": dtext(delta),
                        }
                    )
            finish_scope("total_value", start)

        full_scope_matched = all(
            scope_status.get(scope, {}).get("status") == "matched"
            for scope in RECONCILIATION_SCOPES
        )
        reconciliation_scope = {
            "schema": RECONCILIATION_SCHEMA,
            "required_scopes": list(RECONCILIATION_SCOPES),
            "scope_status": scope_status,
            "full_scope_matched": full_scope_matched,
        }
        persisted_computed = {**computed, "reconciliation": reconciliation_scope}
        rid, now = new_id("recon"), iso()
        status = "matched" if full_scope_matched else "needs_review"
        with self.db.transaction() as con:
            con.execute("INSERT INTO reconciliations(id,account_id,as_of,statement_json,computed_json,differences_json,status,source_ref,created_at) VALUES(?,?,?,?,?,?,?,?,?)",(rid,account_id,as_of,canonical(statement),canonical(persisted_computed),canonical(differences),status,source_ref,now))
        return {
            "id": rid,
            "status": status,
            "differences": differences,
            "computed": persisted_computed,
            "reconciliation": reconciliation_scope,
        }
