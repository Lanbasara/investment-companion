from __future__ import annotations

import json
import math
import os
import stat
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Callable

from .foundation import CompanionError, canonical, digest


TUSHARE_URL = "https://api.tushare.pro"
NORMALIZER_VERSION = "tushare-normalizer/2"
CAPABILITIES: dict[str, dict[str, Any]] = {
    "stock_basic": {"required_fields": {"ts_code", "symbol", "name", "list_date"}},
    "trade_cal": {"required_fields": {"exchange", "cal_date", "is_open"}},
    "daily": {"required_fields": {"ts_code", "trade_date", "open", "high", "low", "close", "vol"}},
    # ETF prices use a distinct endpoint and stream.  V6 must preserve the
    # data lineage separation between the stock and fund prediction lines.
    "fund_daily": {"required_fields": {"ts_code", "trade_date", "open", "high", "low", "close", "vol"}},
    # Verified against the configured account on 2026-08-21.
    "fund_nav": {"required_fields": {"ts_code", "ann_date", "nav_date", "unit_nav", "accum_nav", "accum_div", "net_asset", "adj_nav", "update_flag"}},
    "fund_share": {"required_fields": {"ts_code", "trade_date", "fd_share", "fund_type", "market"}},
    # Verified against the configured account on 2026-08-21.  A caller may
    # request a smaller projection, which is deliberately classified partial.
    "etf_basic": {"required_fields": {"ts_code", "csname", "index_code", "index_name", "list_date", "list_status", "exchange", "mgt_fee", "etf_type"}},
    "adj_factor": {"required_fields": {"ts_code", "trade_date", "adj_factor"}},
    "daily_basic": {"required_fields": {"ts_code", "trade_date"}},
    "bak_basic": {"required_fields": {"ts_code", "trade_date"}},
    "stock_st": {"api_name": "stock_st", "required_fields": {"ts_code", "name"}},
    "suspend_d": {"required_fields": {"ts_code", "trade_date", "suspend_type"}},
    "stk_limit": {"required_fields": {"ts_code", "trade_date", "up_limit", "down_limit"}},
    "index_weight": {"required_fields": {"index_code", "con_code", "trade_date", "weight"}},
    "dividend": {"required_fields": {"ts_code", "end_date", "ann_date", "div_proc", "stk_div", "cash_div_tax", "ex_date"}},
}


@dataclass(frozen=True)
class ProbeResult:
    status: str
    fields: list[str]
    rows: list[dict[str, Any]]
    provider_code: int
    provider_message: str
    raw_bytes: bytes
    raw_response: dict[str, Any]
    raw_kind: str


class TushareAdapter:
    """Narrow official-HTTP adapter; it never persists or returns the token."""

    def __init__(
        self,
        companion,
        *,
        token: str | None = None,
        token_file: str | Path | None = None,
        transport: Callable[[bytes, float], bytes] | None = None,
        endpoint: str = TUSHARE_URL,
    ):
        self.c = companion
        self.data = companion.data
        self.endpoint = endpoint
        self._token = self._load_token(token, token_file)
        self._transport = transport or self._http_transport

    @staticmethod
    def _load_token(token: str | None, token_file: str | Path | None) -> str:
        value = token if token is not None else None
        path = token_file
        if value is None and path is None:
            value=os.environ.get("TUSHARE_TOKEN")
            if not value:path=os.environ.get("TUSHARE_TOKEN_FILE")
        if not value and path:
            credential_path=Path(path).expanduser()
            try:
                mode=stat.S_IMODE(credential_path.stat().st_mode)
                if os.name=="posix" and mode&0o077:
                    raise CompanionError("Tushare token file must not be accessible by group or other users")
                value = credential_path.read_text(encoding="utf-8").strip()
            except CompanionError:
                raise
            except OSError as exc:
                raise CompanionError(f"Tushare token file is not readable: {credential_path}") from exc
        normalized=value.strip() if isinstance(value,str) else ""
        if len(normalized)<8 or any(character.isspace() for character in normalized):
            raise CompanionError("Tushare token is missing; set TUSHARE_TOKEN_FILE or TUSHARE_TOKEN")
        return normalized

    def _http_transport(self, body: bytes, timeout: float) -> bytes:
        request = urllib.request.Request(
            self.endpoint,
            data=body,
            headers={"Content-Type": "application/json", "User-Agent": "investment-companion-v4/1"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return response.read()
        except urllib.error.HTTPError as exc:
            raise CompanionError(f"Tushare HTTP error: {exc.code}") from exc
        except urllib.error.URLError as exc:
            raise CompanionError(f"Tushare connection failed: {exc.reason}") from exc

    def fetch(
        self,
        capability: str,
        *,
        params: dict[str, Any] | None = None,
        fields: list[str] | None = None,
        timeout: float = 20,
    ) -> ProbeResult:
        definition = CAPABILITIES.get(capability)
        if not definition:
            raise CompanionError(f"Tushare capability is not allow-listed: {capability}")
        if params is not None and not isinstance(params,dict):
            raise CompanionError("Tushare params must be an object")
        self._reject_secret_fields(params or {})
        if fields is not None and (not isinstance(fields,list) or any(not isinstance(field,str) or not field or any(not (character.isalnum() or character=="_") for character in field) for field in fields) or len(fields)!=len(set(fields))):
            raise CompanionError("Tushare fields must be a unique identifier list")
        if isinstance(timeout,bool) or not isinstance(timeout,(int,float)) or not math.isfinite(float(timeout)) or not 0<float(timeout)<=120:
            raise CompanionError("Tushare timeout must be within (0,120] seconds")
        request_value = {
            "api_name": definition.get("api_name", capability),
            "token": self._token,
            "params": params or {},
            "fields": ",".join(fields or []),
        }
        try:
            raw = self._transport(canonical(request_value).encode("utf-8"), timeout)
        except Exception as exc:
            message=f"{type(exc).__name__}: {exc}"
            if self._token in message:message=f"{type(exc).__name__}: credential redacted from transport failure"
            response={"code":-9000,"msg":message,"data":None,"synthetic":True}
            raw=canonical(response).encode("utf-8")
            status="connector_missing" if isinstance(exc,OSError) or "connection failed" in message.lower() else "failed"
            return ProbeResult(status,[],[],-9000,message,raw,response,"synthetic_transport_error")
        if not isinstance(raw,bytes):
            message="Tushare transport did not return raw bytes"
            response={"code":-9001,"msg":message,"data":None,"synthetic":True}
            encoded=canonical(response).encode("utf-8")
            return ProbeResult("failed",[],[],-9001,message,encoded,response,"synthetic_transport_error")
        if self._token.encode("utf-8") in raw:
            raise CompanionError("Tushare response echoed the credential; raw response was not retained")
        try:
            response = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            return ProbeResult("failed",[],[],-9002,"Tushare response is not valid UTF-8 JSON",raw,{},"provider_invalid_response")
        if not isinstance(response, dict):
            return ProbeResult("failed",[],[],-9003,"Tushare response root is not an object",raw,{},"provider_invalid_response")
        raw_kind="provider_response"
        try:code = int(response.get("code", -1))
        except (TypeError,ValueError):
            return ProbeResult("failed",[],[],-9004,"Tushare response code is not an integer",raw,response,"provider_invalid_response")
        message = str(response.get("msg") or "")
        data = response.get("data") or {}
        response_fields = data.get("fields") if isinstance(data, dict) else None
        items = data.get("items") if isinstance(data, dict) else None
        if code == 0:
            if not isinstance(response_fields, list) or not isinstance(items, list):
                status = "failed";rows=[];response_fields=[];message="Tushare success response lacks fields/items arrays";raw_kind="provider_invalid_response"
            elif any(not isinstance(field,str) or not field or any(not (character.isalnum() or character=="_") for character in field) for field in response_fields) or len(response_fields)!=len(set(response_fields)):
                status="failed";rows=[];response_fields=[];message="Tushare response fields are invalid or duplicated";raw_kind="provider_invalid_response"
            else:
                if any(not isinstance(row, list) or len(row) != len(response_fields) for row in items):
                    status="failed";rows=[];message="Tushare response item width differs from fields";raw_kind="provider_invalid_response"
                else:
                    rows = [dict(zip(response_fields, row)) for row in items]
                    missing = definition["required_fields"] - set(response_fields)
                    status = "partial" if missing else "empty_valid" if not rows else "healthy"
        else:
            rows=[];response_fields=[];lower=message.lower()
            if code == -2001 or "权限" in message or "permission" in lower:
                status="unauthorized"
            elif "每分钟" in message or "频次" in message or "rate" in lower:
                status="rate_limited"
            elif "参数" in message or "parameter" in lower:
                status="invalid_request"
            else:
                status="failed"
        return ProbeResult(status,response_fields,rows,code,message,raw,response,raw_kind)

    @classmethod
    def _reject_secret_fields(cls,value:Any,path:str="params")->None:
        if isinstance(value,dict):
            for key,item in value.items():
                if not isinstance(key,str):raise CompanionError(f"Tushare {path} keys must be strings")
                normalized=key.lower().replace("-","_")
                if any(marker in normalized for marker in ("token","password","secret","authorization","api_key","apikey")):
                    raise CompanionError(f"Tushare request parameters cannot contain credential-like field: {path}.{key}")
                cls._reject_secret_fields(item,f"{path}.{key}")
        elif isinstance(value,list):
            for index,item in enumerate(value):cls._reject_secret_fields(item,f"{path}[{index}]")

    def probe(
        self,
        capability: str,
        *,
        params: dict[str, Any] | None = None,
        fields: list[str] | None = None,
        account_scope: str = "default",
    ) -> dict[str, Any]:
        checked_at=self._probe_time();result=self.fetch(capability,params=params,fields=fields)
        return self._record_probe_result(capability,result,checked_at,account_scope,{"params":params or {},"fields":fields or []})

    def _record_probe_result(self,capability:str,result:ProbeResult,checked_at:str,account_scope:str,request:dict[str,Any])->dict[str,Any]:
        raw_object=self.data.object_put_bytes(
            result.raw_bytes,
            namespace="raw",
            kind="tushare_probe_response",
            media_type="application/json" if result.raw_kind!="provider_invalid_response" else "application/octet-stream",
            metadata={"provider":"tushare","capability":capability,"checked_at":checked_at,"raw_kind":result.raw_kind},
        )
        report=self.data.manifest_publish(
            kind="capability_probe_report",
            schema_version="investment-companion.capability-probe-report/v1",
            manifest={
                "validator":"tushare-official-http-probe/1",
                "validator_status":"passed",
                "code_version":self.data._code_version(),
                "provider":"tushare",
                "capability":capability,
                "status":result.status,
                "provider_code":result.provider_code,
                "provider_message":result.provider_message[:500],
                "raw_kind":result.raw_kind,
                "request":request,
                "response_object_id":raw_object["id"],
                "response_hash":raw_object["content_hash"],
                "fields":result.fields,
                "row_count":len(result.rows),
                "checked_at":checked_at,
            },
            _internal=True,
        )
        return self.data.capability_record(
            provider="tushare",
            capability=capability,
            connector="official-http/v1",
            account_scope=account_scope,
            status=result.status,
            fields={"observed":result.fields,"required":sorted(CAPABILITIES[capability]["required_fields"])},
            failure={} if result.status in {"healthy","empty_valid"} else {"code":result.provider_code,"message":result.provider_message[:500]},
            evidence={"probe_manifest_id":report["id"],"raw_object_id":raw_object["id"]},
            checked_at=checked_at,
            _internal=True,
        )

    def ingest_canary(
        self,
        capability:str,
        *,
        params:dict[str,Any],
        fields:list[str]|None=None,
        account_scope:str="default",
        ingestion_key:str|None=None,
    )->dict[str,Any]:
        """Fetch once, retain raw bytes, normalize, validate, and close one canary batch."""
        self.c.jobs.feature_require("v4_live_data_canary")
        return self._ingest(capability,params=params,fields=fields,account_scope=account_scope,stream_mode="canary",ingestion_key=ingestion_key)

    def ingest(
        self,
        capability:str,
        *,
        params:dict[str,Any],
        fields:list[str]|None=None,
        account_scope:str="default",
        ingestion_key:str|None=None,
    )->dict[str,Any]:
        """Run one Gate-protected active batch for the scheduled production data path."""
        self.c.jobs.feature_require("v4_live_data")
        return self._ingest(capability,params=params,fields=fields,account_scope=account_scope,stream_mode="active",ingestion_key=ingestion_key)

    def _ingest(self,capability:str,*,params:dict[str,Any],fields:list[str]|None,account_scope:str,stream_mode:str,ingestion_key:str|None)->dict[str,Any]:
        stream=self.data.stream_get("tushare",capability)
        if stream["status"]!=stream_mode:raise CompanionError(f"Tushare ingestion requires a configured {stream_mode} stream")
        if stream["schema_version"]!=NORMALIZER_VERSION:raise CompanionError(f"Tushare stream requires {NORMALIZER_VERSION}")
        if ingestion_key is not None and (not isinstance(ingestion_key,str) or not ingestion_key.strip()):raise CompanionError("ingestion_key must be a non-empty string")
        key=digest("tushare-ingestion-v2",stream_mode,capability,params,fields or [],stream["schema_version"],ingestion_key or "interactive-stable")
        batch=self.data.batch_start(stream["id"],key,params,required_stream_status=stream_mode)
        if batch["status"] in {"ready","blocked","failed","empty_valid"}:return batch
        checked_at=self._probe_time();raw_object_ids=[];canonical_object_ids=[]
        try:
            result=self.fetch(capability,params=params,fields=fields)
            capability_record=self._record_probe_result(capability,result,checked_at,account_scope,{"params":params,"fields":fields or []})
            raw_object_id=capability_record["evidence"]["raw_object_id"]
            raw_object_ids.append(raw_object_id)
            if result.status=="empty_valid":
                return self.data.batch_finish(batch["id"],status="empty_valid",raw_object_ids=raw_object_ids,canonical_object_ids=[],row_count=0,cursor_after=self._cursor(params))
            if result.status!="healthy":
                return self.data.batch_finish(batch["id"],status="blocked",raw_object_ids=raw_object_ids,canonical_object_ids=[],row_count=0,error=f"capability status: {result.status}")
            raw=self.data.object_get(raw_object_id)
            normalized,stream_kind=self._normalize(capability,result.rows,checked_at,raw["content_hash"],require_identity=stream_mode=="active")
            canonical_object=self.data.object_put_json(normalized,namespace="canonical",kind=f"tushare_{capability}_canonical",metadata={"provider":"tushare","capability":capability,"batch_id":batch["id"],"parser_version":NORMALIZER_VERSION})
            canonical_object_ids.append(canonical_object["id"])
            partition_name=f"{capability}/{self._cursor(params)}"
            validation=self.data.partition_validate(object_id=canonical_object["id"],partition_name=partition_name,stream=stream_kind,role=stream_mode if stream_mode=="canary" else "production",knowledge_cutoff=checked_at)
            validation_body=validation["manifest"].get("manifest",{})
            terminal="ready" if validation_body.get("eligible") else "blocked"
            if terminal=="ready" and stream_mode=="active" and capability=="stock_basic":
                self.data.identity_apply_tushare_stock_basic(result.rows,checked_at=checked_at,raw_hash=raw["content_hash"])
            if terminal=="ready" and stream_mode=="active" and capability=="etf_basic":
                self.data.identity_apply_tushare_etf_basic(result.rows,checked_at=checked_at,raw_hash=raw["content_hash"])
            return self.data.batch_finish(batch["id"],status=terminal,raw_object_ids=raw_object_ids,canonical_object_ids=canonical_object_ids,row_count=int(validation_body.get("row_count",0)),error=None if terminal=="ready" else canonical(validation_body.get("violations",[])),cursor_after=self._cursor(params))
        except Exception as exc:
            return self.data.batch_finish(batch["id"],status="failed",raw_object_ids=raw_object_ids,canonical_object_ids=canonical_object_ids,row_count=0,error=str(exc))

    @staticmethod
    def _probe_time()->str:
        return datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00","Z")

    @staticmethod
    def _cursor(params:dict[str,Any])->str:
        return str(params.get("trade_date") or params.get("start_date") or params.get("cal_date") or digest(params)[:16])

    @staticmethod
    def _day(value:Any)->str:
        text=str(value)
        if len(text)!=8 or not text.isdigit():raise CompanionError(f"invalid Tushare date: {text}")
        return f"{text[:4]}-{text[4:6]}-{text[6:]}"

    def _normalize(self,capability:str,rows:list[dict[str,Any]],checked_at:str,raw_hash:str,*,require_identity:bool=False)->tuple[Any,str]:
        if capability in {"daily","fund_daily"}:
            values=[]
            parser_version="tushare-daily/2" if capability=="daily" else "tushare-fund-daily/1"
            for row in rows:
                day=self._day(row["trade_date"])
                asset_id=f"tushare:{row['ts_code']}"
                if require_identity:
                    resolved=self.data.identity_resolve("tushare","ts_code",str(row["ts_code"]),effective_at=f"{day}T15:00:00+08:00",knowledge_cutoff=checked_at)
                    asset_id=resolved["asset_id"]
                values.append({
                    "asset_id":asset_id,"date":day,
                    "open":str(row["open"]),"high":str(row["high"]),"low":str(row["low"]),"close":str(row["close"]),"volume":str(row["vol"]),
                    "amount":str(row.get("amount")) if row.get("amount") is not None else None,
                    "suspended":False,"at_upper_limit":False,"at_lower_limit":False,
                    "first_known_at":f"{day}T16:00:00+08:00","ingested_at":checked_at,"raw_hash":raw_hash,"parser_version":parser_version,
                })
            return values,"daily"
        if capability=="trade_cal":
            values=[{"date":self._day(row["cal_date"]),"exchange":str(row.get("exchange") or "SSE"),"is_open":str(row["is_open"]) in {"1","True","true"},"pretrade_date":self._day(row["pretrade_date"]) if row.get("pretrade_date") else None} for row in rows]
            return values,"trading_calendar"
        if capability=="dividend":
            facts=[]
            for index,row in enumerate(rows):
                if str(row.get("div_proc") or "").strip()!="实施":
                    continue
                code=str(row.get("ts_code") or "").strip();raw_ex_date=row.get("ex_date")
                if not code or not raw_ex_date:raise CompanionError(f"implemented dividend row {index} lacks ts_code/ex_date")
                ex_date=self._day(raw_ex_date);effective=f"{ex_date}T00:00:00+08:00";asset_id=f"tushare:{code}"
                if require_identity:
                    asset_id=self.data.identity_resolve("tushare","ts_code",code,effective_at=effective,knowledge_cutoff=checked_at)["asset_id"]
                try:
                    cash=Decimal(str(row.get("cash_div_tax") or "0"));stock=Decimal(str(row.get("stk_div") or "0"))
                except (InvalidOperation,ValueError) as exc:raise CompanionError(f"dividend row {index} has invalid cash/stock values") from exc
                if not cash.is_finite() or not stock.is_finite() or cash<0 or stock<0:raise CompanionError(f"dividend row {index} has negative or non-finite values")
                base_key=f"{row.get('end_date') or ''}:{row.get('ann_date') or ''}:{raw_ex_date}"
                actions=[]
                if cash>0:actions.append(("cash",{"action_type":"cash_dividend","asset_id":asset_id,"ex_date":ex_date,"cash_per_share":format(cash.normalize(),"f")}))
                if stock>0:actions.append(("stock",{"action_type":"split","asset_id":asset_id,"ex_date":ex_date,"split_ratio":format((Decimal("1")+stock).normalize(),"f")}))
                for suffix,value in actions:
                    fact_key=f"dividend:{base_key}:{suffix}";revision_id=digest("tushare-dividend-action/1",asset_id,fact_key,value,row,raw_hash)
                    facts.append({"entity_key":asset_id,"fact_key":fact_key,"effective_at":effective,"effective_to":None,"first_known_at":checked_at,"ingested_at":checked_at,"revision_id":revision_id,"supersedes":None,"raw_hash":raw_hash,"parser_version":"tushare-dividend-actions/1","quality":{"status":"provider_response_validated","implementation_only":True,"identity_resolved":not asset_id.startswith("tushare:")},"provider":"tushare","capability":"dividend","value":value})
            return sorted(facts,key=lambda item:(item["effective_at"],item["entity_key"],item["fact_key"],item["revision_id"])),"corporate_actions"
        facts=[]
        for row in rows:
            entity=str(row.get("ts_code") or row.get("con_code") or row.get("index_code") or "market")
            raw_date=row.get("trade_date") or row.get("ann_date") or row.get("list_date") or row.get("end_date") or checked_at[:10].replace("-","")
            effective=self._day(raw_date)+"T00:00:00+08:00"
            fact_key=f"{capability}:{raw_date}:{row.get('index_code','')}"
            revision_id=digest("tushare-pit/1",capability,entity,fact_key,row,raw_hash)
            entity_key=f"tushare:{entity}"
            if require_identity and row.get("ts_code") and capability not in {"stock_basic", "etf_basic"}:
                entity_key=self.data.identity_resolve("tushare","ts_code",str(row["ts_code"]),effective_at=effective,knowledge_cutoff=checked_at)["asset_id"]
            facts.append({"entity_key":entity_key,"fact_key":fact_key,"effective_at":effective,"effective_to":None,"first_known_at":checked_at,"ingested_at":checked_at,"revision_id":revision_id,"supersedes":None,"raw_hash":raw_hash,"parser_version":"tushare-pit/1","quality":{"status":"provider_response_validated","identity_resolved":not entity_key.startswith("tushare:")},"provider":"tushare","capability":capability,"value":row})
        return {"stream":capability,"facts":facts,"metadata":{"provider":"tushare","parser_version":"tushare-pit/1"}},"pit_facts"
