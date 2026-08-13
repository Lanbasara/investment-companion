from __future__ import annotations

import csv
import io
import json
from decimal import Decimal, InvalidOperation, ROUND_HALF_EVEN, getcontext
from typing import Any

from .core import CompanionError, canonical, digest, new_id
from .db import row_dict, rows_dict
from .timeutil import iso

getcontext().prec = 34
ENGINE_VERSION = "financial-kernel-3.0.0"


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
        if entry_type=="trade" and (asset_id is None or qty_d is None or price_d is None):raise CompanionError("trade requires asset_id, quantity, and price")
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
        if mandate:
            minimum=mandate.get("minimum_cash",{}).get(asset["currency"])
            if minimum is not None and cash[asset["currency"]]<dec(minimum):violations.append({"rule":"minimum_cash","required":str(minimum),"actual":dtext(cash[asset["currency"]])})
        output={"before":before,"after":{"cash":{k:dtext(v) for k,v in cash.items()},"positions":{k:dtext(v) for k,v in positions.items()}},"delta":{"asset_id":asset_id,"quantity":dtext(qty),"cash":dtext(-(qty*px)-fee_d),"currency":asset["currency"]},"violations":violations,"blocked":bool(violations)}
        calc=self._record("trade_impact","Simulate trade without changing ledger",as_of,{"account_id":account_id,"asset_id":asset_id,"quantity":dtext(qty),"price":dtext(px),"fee":dtext(fee_d)},{"mandate":mandate or {}},{"cash_delta":"-(quantity*price)-fee"},output,[])
        output["calculation_id"]=calc["id"];return output

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
        return item

    def _record(self, kind,purpose,as_of,inputs,assumptions,formulas,outputs,warnings):
        rh=digest(ENGINE_VERSION,kind,as_of,inputs,assumptions,formulas,outputs,warnings);cid=new_id("calc");now=iso()
        with self.db.transaction() as con:
            con.execute("INSERT OR IGNORE INTO calculations(id,kind,purpose,engine_version,as_of,inputs_json,assumptions_json,formulas_json,outputs_json,warnings_json,reproducibility_hash,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",(cid,kind,purpose,ENGINE_VERSION,as_of,canonical(inputs),canonical(assumptions),canonical(formulas),canonical(outputs),canonical(warnings),rh,now));row=con.execute("SELECT * FROM calculations WHERE reproducibility_hash=?",(rh,)).fetchone()
        return row_dict(row)

    def reconcile(self, account_id: str, as_of: str, statement: dict, source_ref: str | None = None) -> dict:
        computed=self.portfolio_state(as_of,account_id);differences=[]
        for currency,expected in statement.get("cash",{}).items():
            actual=dec(computed["cash"].get(currency,"0"));delta=actual-dec(expected)
            if delta:differences.append({"kind":"cash","currency":currency,"statement":str(expected),"computed":dtext(actual),"difference":dtext(delta)})
        expected_positions={k:dec(v) for k,v in statement.get("positions",{}).items()};actual_positions={p["asset_id"]:dec(p["quantity"]) for p in computed["positions"]}
        for aid in sorted(set(expected_positions)|set(actual_positions)):
            delta=actual_positions.get(aid,Decimal(0))-expected_positions.get(aid,Decimal(0))
            if delta:difference={"kind":"position","asset_id":aid,"statement":dtext(expected_positions.get(aid,Decimal(0))),"computed":dtext(actual_positions.get(aid,Decimal(0))),"difference":dtext(delta)};differences.append(difference)
        rid,now=new_id("recon"),iso();status="matched" if not differences else "needs_review"
        with self.db.transaction() as con:
            con.execute("INSERT INTO reconciliations(id,account_id,as_of,statement_json,computed_json,differences_json,status,source_ref,created_at) VALUES(?,?,?,?,?,?,?,?,?)",(rid,account_id,as_of,canonical(statement),canonical(computed),canonical(differences),status,source_ref,now))
        return {"id":rid,"status":status,"differences":differences,"computed":computed}
