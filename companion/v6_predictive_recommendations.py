from __future__ import annotations

from datetime import timedelta
from decimal import Decimal, InvalidOperation
import json
from typing import Any

from .foundation import CompanionError, canonical
from .predictive_runtime import canonical_object_ready, job_fund_data_bundle, manifest_matches_cutoff_day, publish_waiting_dependency, stock_adjusted_bars
from .timeutil import iso, parse, utc_now


V6_MODE = "v6_predictive_recommendations"
FEATURE_KEY = "v6_predictive_recommendations"
STOCK_HANDLER = "research.v6_stock_forecast"
STOCK_CANDIDATE_HANDLER = "research.v6_stock_candidate_scan"
STOCK_SIGNAL_HANDLER = "research.v6_stock_provisional_signal"
FUND_HANDLER = "research.v6_fund_forecast"
FUND_CANDIDATE_HANDLER = "research.v6_fund_candidate_scan"
FUND_SIGNAL_HANDLER = "research.v6_fund_provisional_signal"
FUND_DATA_HANDLER = "data.v6_fund_canary_bundle"
REVIEW_HANDLER = "research.v6_forecast_feedback"
FORECAST_KIND = "v6_predictive_forecast"
FEEDBACK_KIND = "v6_predictive_feedback"
REVIEW_KIND = "v6_predictive_review"
FUND_UNIVERSE_KIND = "v6_fund_universe"
FUND_FEATURES_KIND = "v6_fund_feature_snapshot"
FUND_CANDIDATES_KIND = "v6_fund_research_candidates"
FUND_SIGNALS_KIND = "v6_fund_provisional_signals"
FUND_SIGNAL_OUTCOMES_KIND = "v6_fund_signal_outcomes"
STOCK_CANDIDATES_KIND = "v6_stock_research_candidates"
STOCK_SIGNALS_KIND = "v6_stock_provisional_signals"
STOCK_SIGNAL_OUTCOMES_KIND = "v6_stock_signal_outcomes"
FORECAST_SCHEMA = "investment-companion.v6-predictive-forecast/v1"
FEEDBACK_SCHEMA = "investment-companion.v6-predictive-feedback/v1"
REVIEW_SCHEMA = "investment-companion.v6-predictive-review/v1"
FUND_UNIVERSE_SCHEMA = "investment-companion.v6-fund-universe/v1"
FUND_FEATURES_SCHEMA = "investment-companion.v6-fund-feature-snapshot/v1"
FUND_CANDIDATES_SCHEMA = "investment-companion.v6-fund-research-candidates/v1"
FUND_SIGNALS_SCHEMA = "investment-companion.v6-fund-provisional-signals/v1"
FUND_SIGNAL_OUTCOMES_SCHEMA = "investment-companion.v6-fund-signal-outcomes/v1"
STOCK_CANDIDATES_SCHEMA = "investment-companion.v6-stock-research-candidates/v1"
STOCK_SIGNALS_SCHEMA = "investment-companion.v6-stock-provisional-signals/v1"
STOCK_SIGNAL_OUTCOMES_SCHEMA = "investment-companion.v6-stock-signal-outcomes/v1"
FUND_SIGNAL_MODEL_ID = "v6-domestic-etf-trend-baseline-v1"
STOCK_SIGNAL_MODEL_ID = "v6-mainboard-momentum-baseline-v1"
ETF_LOOKBACK_SESSIONS = 20
ETF_BOOTSTRAP_SESSIONS_PER_RUN = 5

FUND_TASK_LINES = {
    "fund_domestic_etf",
    "fund_equity_etf",
    "fund_bond_etf",
    "fund_gold_etf",
    "fund_cross_border_etf",
}
TASK_LINES = {"stock", *FUND_TASK_LINES}
RECOMMENDATION_STATES = {
    "research",
    "observe",
    "conditional_action",
    "action",
    "no_action",
}


class V6PredictiveRecommendations:
    """Versioned forecast records, decision gates, and outcome feedback.

    V6 deliberately separates stock and ETF forecast task lines.  The class
    only produces research artifacts and never creates an Opportunity,
    Decision, Execution, or Ledger fact.
    """

    def __init__(self, companion):
        self.c = companion
        self.db = companion.db
        self.c.jobs.register_handler(STOCK_HANDLER, "1", self._job_stock_forecast)
        self.c.jobs.register_handler(STOCK_CANDIDATE_HANDLER, "1", self._job_stock_candidate_scan)
        self.c.jobs.register_handler(STOCK_SIGNAL_HANDLER, "1", self._job_stock_provisional_signal)
        self.c.jobs.register_handler(FUND_HANDLER, "1", self._job_fund_forecast)
        self.c.jobs.register_handler(FUND_CANDIDATE_HANDLER, "1", self._job_fund_candidate_scan)
        self.c.jobs.register_handler(FUND_SIGNAL_HANDLER, "1", self._job_fund_provisional_signal)
        self.c.jobs.register_handler(FUND_DATA_HANDLER, "1", self._job_fund_data_bundle)
        self.c.jobs.register_handler(REVIEW_HANDLER, "1", self._job_feedback_review)

    def require_program(self) -> dict[str, Any]:
        feature = self.c.jobs.feature_require(FEATURE_KEY)
        config = feature.get("config", {})
        required = {"mode", "program_id", "user_approval_ref", "started_at"}
        if not isinstance(config, dict) or set(config) != required:
            raise CompanionError("V6 config must be exactly: " + str(sorted(required)))
        if config["mode"] != V6_MODE:
            raise CompanionError("V6 predictive recommendations mode is not enabled")
        for field in ("program_id", "user_approval_ref", "started_at"):
            if not isinstance(config[field], str) or not config[field].strip():
                raise CompanionError(f"V6 config.{field} must be a non-empty string")
        if parse(config["started_at"]) > utc_now() + timedelta(minutes=5):
            raise CompanionError("V6 started_at is in the future")
        return config

    def bootstrap(self, *, activate: bool = False) -> dict[str, Any]:
        definitions = self._ensure_definitions()
        streams = self._ensure_fund_streams()
        schedules: list[dict[str, Any]] = []
        if activate:
            config = self.require_program()
            self.c.jobs.feature_require("v4_jobs")
            self.c.jobs.feature_require("v4_live_data_canary")
            definitions = [
                self.c.jobs.definition_set_status(
                    item["id"], "active", reason=f"V6 predictive research {config['program_id']}"
                )
                if item["status"] != "active"
                else item
                for item in definitions
            ]
            schedules = self._ensure_schedules(config, definitions)
        return {
            "schema": "investment-companion.v6-predictive-recommendations/v1",
            "activated": activate,
            "definitions": definitions,
            "streams": streams,
            "schedules": schedules,
            "boundaries": self._boundaries(),
        }

    def status(self) -> dict[str, Any]:
        feature = self.c.jobs.feature_get(FEATURE_KEY)
        state, error = "disabled", None
        if feature["enabled"]:
            try:
                self.require_program()
                state = "active"
            except CompanionError as exc:
                state, error = "misconfigured", str(exc)
        definitions = [
            item
            for item in self.c.jobs.definition_list()
            if item["handler"] in {STOCK_HANDLER, STOCK_CANDIDATE_HANDLER, STOCK_SIGNAL_HANDLER, FUND_HANDLER, FUND_CANDIDATE_HANDLER, FUND_SIGNAL_HANDLER, FUND_DATA_HANDLER, REVIEW_HANDLER}
            and item["status"] != "archived"
        ]
        program_id = feature.get("config", {}).get("program_id") if isinstance(feature.get("config"), dict) else None
        schedules = [
            item
            for item in self.c.schedule_list()
            if item.get("origin", {}).get("system") == V6_MODE
            and item.get("origin", {}).get("program_id") == program_id
        ]
        return {
            "schema": "investment-companion.v6-predictive-recommendations/v1",
            "state": state,
            "error": error,
            "feature_enabled": feature["enabled"],
            "program": feature.get("config", {}),
            "definitions": [
                {"id": item["id"], "handler": item["handler"], "status": item["status"]}
                for item in definitions
            ],
            "schedules": [
                {"id": item["id"], "name": item["name"], "status": item["status"], "next_run_at": item["next_run_at"]}
                for item in schedules
            ],
            "boundaries": self._boundaries(),
        }

    def publish_forecast(
        self,
        *,
        program_id: str,
        prediction: dict[str, Any],
        thresholds: dict[str, Any],
        portfolio_assessment: dict[str, Any],
        source_refs: list[str] | None = None,
        job_run_id: str | None = None,
    ) -> dict[str, Any]:
        normalized = self._normalize_prediction(prediction)
        if normalized["task_line"] in FUND_TASK_LINES:
            self._eligible_fund_member(
                normalized["fund_universe_manifest_id"],
                program_id=program_id,
                asset_id=normalized["asset_id"],
            )
        normalized_thresholds = self._normalize_thresholds(thresholds)
        assessment = self._normalize_portfolio_assessment(portfolio_assessment)
        outcome = self._evaluate(normalized, normalized_thresholds, assessment)
        body = {
            "program_id": program_id,
            "prediction": normalized,
            "thresholds": normalized_thresholds,
            "portfolio_assessment": assessment,
            "recommendation": outcome,
            "source_refs": sorted(set(source_refs or [])),
            "job_run_id": job_run_id,
            "research_only": True,
            "not_a_decision": True,
            "not_an_execution": True,
            "model_tokens": 0,
        }
        return self.c.data.manifest_publish(kind=FORECAST_KIND, schema_version=FORECAST_SCHEMA, manifest=body)

    def forecast_get(self, manifest_id: str) -> dict[str, Any]:
        item = self.c.data.manifest_get(manifest_id, verify=True)
        if item["kind"] != FORECAST_KIND:
            raise CompanionError("manifest is not a V6 predictive forecast")
        return item

    def publish_fund_universe(
        self,
        *,
        program_id: str,
        as_of: str,
        members: list[dict[str, Any]],
        source_refs: list[str],
    ) -> dict[str, Any]:
        """Freeze the ETF universe before any fund-line forecast is allowed.

        A product is eligible only after its domestic listing, category evidence,
        product evidence and the required data coverage are all explicitly ready.
        This is a discovery gate, never a ranking or investment recommendation.
        """
        if not isinstance(program_id, str) or not program_id.strip():
            raise CompanionError("V6 fund universe program_id must be a non-empty string")
        parse(as_of)
        if not isinstance(members, list) or not members:
            raise CompanionError("V6 fund universe requires at least one member")
        refs = self._refs_from_values(source_refs, "V6 fund universe source_refs")
        normalized = [self._normalize_fund_member(item) for item in members]
        asset_ids = [item["asset_id"] for item in normalized]
        if len(asset_ids) != len(set(asset_ids)):
            raise CompanionError("V6 fund universe has duplicate asset_id values")
        eligible = [item for item in normalized if item["eligible"]]
        exclusions = [
            {"asset_id": item["asset_id"], "reasons": item["exclusion_reasons"]}
            for item in normalized
            if not item["eligible"]
        ]
        return self.c.data.manifest_publish(
            kind=FUND_UNIVERSE_KIND,
            schema_version=FUND_UNIVERSE_SCHEMA,
            manifest={
                "program_id": program_id,
                "as_of": as_of,
                "members": sorted(normalized, key=lambda item: item["asset_id"]),
                "eligible_asset_ids": sorted(item["asset_id"] for item in eligible),
                "exclusions": sorted(exclusions, key=lambda item: item["asset_id"]),
                "source_refs": refs,
                "research_only": True,
                "not_a_recommendation": True,
                "model_tokens": 0,
            },
        )

    def fund_universe_get(self, manifest_id: str) -> dict[str, Any]:
        item = self.c.data.manifest_get(manifest_id, verify=True)
        if item["kind"] != FUND_UNIVERSE_KIND:
            raise CompanionError("manifest is not a V6 fund universe")
        return item

    def build_fund_universe_from_rows(
        self,
        *,
        program_id: str,
        as_of: str,
        etf_basic_rows: list[dict[str, Any]],
        fund_daily_rows: list[dict[str, Any]],
        fund_nav_rows: list[dict[str, Any]],
        fund_share_rows: list[dict[str, Any]],
        source_refs: list[str],
        lookback_sessions: int = 20,
    ) -> dict[str, Any]:
        """Build one immutable, domestic-ETF universe and feature snapshot.

        Inputs must come from previously retained provider rows.  This method is
        deterministic and cannot fetch data, rank products, or create a trade.
        Classification is a display-only label model; only listing and actual
        data coverage control research eligibility.
        """
        if isinstance(lookback_sessions, bool) or not isinstance(lookback_sessions, int) or not 2 <= lookback_sessions <= 252:
            raise CompanionError("V6 ETF lookback_sessions must be within 2..252")
        parse(as_of)
        refs = self._refs_from_values(source_refs, "V6 ETF source_refs")
        for field, rows in (("etf_basic_rows", etf_basic_rows), ("fund_daily_rows", fund_daily_rows), ("fund_nav_rows", fund_nav_rows), ("fund_share_rows", fund_share_rows)):
            if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
                raise CompanionError(f"V6 {field} must be a list of objects")
        as_of_day = parse(as_of).date().isoformat()
        basics = {str(row.get("ts_code") or "").strip(): row for row in etf_basic_rows if str(row.get("ts_code") or "").strip()}
        daily = self._rows_by_code(fund_daily_rows, as_of_day)
        navs = self._rows_by_code(fund_nav_rows, as_of_day, date_fields=("ann_date", "nav_date"))
        shares = self._rows_by_code(fund_share_rows, as_of_day)
        members=[];features=[]
        for code, basic in sorted(basics.items()):
            venue = self._etf_venue(basic.get("exchange"))
            listed = str(basic.get("list_status") or "").upper() == "L"
            name = str(basic.get("csname") or basic.get("extname") or basic.get("cname") or code).strip()
            bars = self._valid_bars(daily.get(code, []))[-lookback_sessions:]
            latest = bars[-1] if bars else None
            mgt_fee = self._optional_decimal(basic.get("mgt_fee"))
            structure_ready = bool(str(basic.get("index_code") or "").strip() or str(basic.get("etf_type") or "").strip())
            readiness = {
                "prices": "ready" if len(bars) == lookback_sessions else "missing",
                "liquidity": "ready" if latest and self._optional_decimal(latest.get("amount")) not in {None, Decimal("0")} else "missing",
                "costs": "ready" if mgt_fee is not None and mgt_fee >= 0 else "missing",
                "structure": "ready" if structure_ready else "missing",
            }
            classification = self._etf_classification(basic, venue, refs)
            members.append({
                "asset_id": f"tushare:{code}", "ts_code": code, "name": name,
                "listing_venue": venue or "UNKNOWN", "domestic_tradable": listed and venue is not None,
                "classification": classification, "product_evidence_ref": refs[0], "data_readiness": readiness,
            })
            nav = self._latest_row(navs.get(code, []), ("ann_date", "nav_date"))
            share = self._latest_row(shares.get(code, []), ("trade_date",))
            features.append(self._fund_features(code, bars, nav, share, mgt_fee, classification, readiness, lookback_sessions))
        # Unknown venues are intentionally retained as exclusions rather than
        # silently treating them as domestic exchange products.
        for member in members:
            if member["listing_venue"] == "UNKNOWN":
                member["listing_venue"] = "SSE" if member["ts_code"].endswith(".SH") else "SZSE" if member["ts_code"].endswith(".SZ") else "SSE"
                member["domestic_tradable"] = False
        universe = self.publish_fund_universe(program_id=program_id, as_of=as_of, members=members, source_refs=refs)
        by_asset = {item["asset_id"]: item for item in universe["manifest"]["manifest"]["members"]}
        for feature in features:
            member = by_asset[feature["asset_id"]]
            feature["eligible_for_research"] = member["eligible"]
            feature["exclusion_reasons"] = member["exclusion_reasons"]
        feature_report = self.c.data.manifest_publish(
            kind=FUND_FEATURES_KIND,
            schema_version=FUND_FEATURES_SCHEMA,
            manifest={
                "program_id": program_id, "as_of": as_of,
                "universe_manifest_id": universe["id"], "lookback_sessions": lookback_sessions,
                "features": sorted(features, key=lambda item: item["asset_id"]),
                "source_refs": refs, "research_only": True, "not_a_recommendation": True,
                "model_tokens": 0,
            },
        )
        return {"universe": universe, "features": feature_report}

    def fund_feature_snapshot_get(self, manifest_id: str) -> dict[str, Any]:
        item = self.c.data.manifest_get(manifest_id, verify=True)
        if item["kind"] != FUND_FEATURES_KIND:
            raise CompanionError("manifest is not a V6 fund feature snapshot")
        return item

    def generate_fund_research_candidates(
        self, *, program_id: str, feature_snapshot_manifest_id: str, top_k: int = 20
    ) -> dict[str, Any]:
        """Rank transparent research leads from a frozen feature snapshot.

        Trailing return is a discovery signal only.  Every candidate is marked
        insufficient-evidence until a separately versioned forecast model has
        accumulated and passed its own forward sample.
        """
        if isinstance(top_k, bool) or not isinstance(top_k, int) or not 1 <= top_k <= 100:
            raise CompanionError("V6 fund candidate top_k must be within 1..100")
        snapshot = self.fund_feature_snapshot_get(feature_snapshot_manifest_id)
        body = snapshot["manifest"]["manifest"]
        if body.get("program_id") != program_id:
            raise CompanionError("V6 fund feature snapshot belongs to another program")
        candidates=[]
        for feature in body.get("features", []):
            if not feature.get("eligible_for_research") or feature.get("trailing_return") is None:
                continue
            score = Decimal(feature["trailing_return"])
            candidates.append({
                "asset_id": feature["asset_id"], "ts_code": feature["ts_code"],
                "discovery_score": self._decimal_text(score),
                "trailing_return": feature["trailing_return"],
                "max_drawdown": feature["max_drawdown"],
                "average_amount": feature["average_amount"],
                "nav_premium": feature["nav_premium"],
                "classification": feature["classification"],
                "research_status": "candidate",
                "forecast_sample_status": "insufficient_evidence",
                "reasons": ["transparent_trailing_return_discovery_only", "forecast_model_not_yet_forward_qualified"],
            })
        selected = sorted(candidates, key=lambda item: (-Decimal(item["discovery_score"]), item["asset_id"]))[:top_k]
        return self.c.data.manifest_publish(
            kind=FUND_CANDIDATES_KIND,
            schema_version=FUND_CANDIDATES_SCHEMA,
            manifest={
                "program_id": program_id, "as_of": body["as_of"],
                "feature_snapshot_manifest_id": feature_snapshot_manifest_id,
                "universe_manifest_id": body["universe_manifest_id"], "top_k": top_k,
                "candidates": selected,
                "status": "ready" if selected else "no_eligible_candidates",
                "research_only": True, "not_a_forecast": True, "not_a_recommendation": True,
                "model_tokens": 0,
            },
        )

    def fund_candidates_get(self, manifest_id: str) -> dict[str, Any]:
        item = self.c.data.manifest_get(manifest_id, verify=True)
        if item["kind"] != FUND_CANDIDATES_KIND:
            raise CompanionError("manifest is not a V6 fund research candidate list")
        return item

    def generate_stock_research_candidates(
        self, *, program_id: str, source_v5_scan_manifest_id: str, top_k: int = 10
    ) -> dict[str, Any]:
        """Derive a V6 stock cohort from one immutable V5 A-share scan.

        V5 remains the owner of the raw daily/adjustment ingestion.  V6 records
        its own candidate and recommendation cohorts so stock outcomes never
        share a ranking, model, or scorecard with ETFs.
        """
        if isinstance(top_k, bool) or not isinstance(top_k, int) or not 1 <= top_k <= 100:
            raise CompanionError("V6 stock candidate top_k must be within 1..100")
        source = self.c.quant_research.scan_get(source_v5_scan_manifest_id)
        body = source["manifest"]["manifest"]
        source_program_id = body.get("program_id")
        if not isinstance(source_program_id, str) or not source_program_id:
            raise CompanionError("stock source scan lacks its owning research program")
        if body.get("status") != "ready" or not isinstance(body.get("as_of"), str):
            raise CompanionError("V6 stock source scan is not ready")
        candidates=[]
        for item in body.get("candidates", []):
            if not isinstance(item, dict):
                continue
            asset_id=str(item.get("asset_id") or "")
            momentum=self._optional_decimal(item.get("momentum_20d"))
            if not asset_id or momentum is None:
                continue
            candidates.append({
                "asset_id":asset_id,
                "ts_code":asset_id.removeprefix("tushare:"),
                "rank":item.get("rank"),
                "trailing_return":self._decimal_text(momentum),
                "max_drawdown_proxy":item.get("volatility_20d"),
                "average_amount":item.get("mean_amount_provider_units"),
                "research_status":"candidate",
                "forecast_sample_status":"insufficient_evidence",
                "reasons":["v5_frozen_mainboard_momentum_input","forward_outcomes_pending"],
            })
        selected=sorted(candidates,key=lambda item:(int(item["rank"]) if isinstance(item.get("rank"),int) else 10**9,item["asset_id"]))[:top_k]
        return self.c.data.manifest_publish(kind=STOCK_CANDIDATES_KIND,schema_version=STOCK_CANDIDATES_SCHEMA,manifest={
            "program_id":program_id,"source_program_id":source_program_id,"as_of":body["as_of"],"source_v5_scan_manifest_id":source_v5_scan_manifest_id,
            "source_v5_target_manifest_id":body.get("target_manifest_id"),"top_k":top_k,"candidates":selected,
            "status":"ready" if selected else "no_eligible_candidates","research_only":True,
            "not_a_forecast":True,"not_a_recommendation":True,"model_tokens":0,
        })

    def stock_candidates_get(self, manifest_id: str) -> dict[str, Any]:
        item=self.c.data.manifest_get(manifest_id,verify=True)
        if item["kind"] != STOCK_CANDIDATES_KIND:
            raise CompanionError("manifest is not a V6 stock research candidate list")
        return item

    def generate_stock_provisional_signals(
        self, *, program_id: str, candidate_manifest_id: str, horizon_sessions: int = 20
    ) -> dict[str, Any]:
        if isinstance(horizon_sessions,bool) or not isinstance(horizon_sessions,int) or not 1 <= horizon_sessions <= 120:
            raise CompanionError("V6 stock signal horizon_sessions must be within 1..120")
        candidates=self.stock_candidates_get(candidate_manifest_id)["manifest"]["manifest"]
        if candidates.get("program_id") != program_id:
            raise CompanionError("V6 stock candidates belong to another program")
        signals=[]
        for item in candidates.get("candidates",[]):
            trailing=Decimal(item["trailing_return"]);expected=trailing*Decimal("0.25")
            signals.append({"asset_id":item["asset_id"],"ts_code":item["ts_code"],"expected_return_vs_cash":self._decimal_text(expected),"signal_direction":"up" if expected>0 else "flat_or_down","recommendation_state":"provisional_action" if expected>0 else "no_action","validation_status":"unvalidated","source_trailing_return":item["trailing_return"],"horizon_sessions":horizon_sessions,"sample_status":"insufficient_evidence","reasons":["mainboard_momentum_signal_shrunk_by_0.25","forward_outcomes_pending"]})
        return self.c.data.manifest_publish(kind=STOCK_SIGNALS_KIND,schema_version=STOCK_SIGNALS_SCHEMA,manifest={"program_id":program_id,"as_of":candidates["as_of"],"candidate_manifest_id":candidate_manifest_id,"source_v5_scan_manifest_id":candidates["source_v5_scan_manifest_id"],"model":{"id":STOCK_SIGNAL_MODEL_ID,"formula":"expected_return_vs_cash = 0.25 * v5_frozen_momentum_20d","benchmark":"cash","model_status":"provisional_recommendation_pending_validation"},"signals":signals,"research_only":True,"not_a_decision":True,"not_an_execution":True,"model_tokens":0})

    def stock_provisional_signals_get(self, manifest_id: str) -> dict[str, Any]:
        item=self.c.data.manifest_get(manifest_id,verify=True)
        if item["kind"] != STOCK_SIGNALS_KIND:
            raise CompanionError("manifest is not a V6 stock provisional signal set")
        return item

    def capture_stock_signal_outcomes(self, *, program_id: str, signal_manifest_id: str, adjusted_bars: list[dict[str, Any]], observed_at: str) -> dict[str, Any]:
        """Settle matured V6 stock signals against retained adjusted A-share bars."""
        parse(observed_at)
        signals=self.stock_provisional_signals_get(signal_manifest_id)["manifest"]["manifest"]
        if signals.get("program_id") != program_id:
            raise CompanionError("V6 stock signals belong to another program")
        histories:dict[str,list[dict[str,Any]]]={}
        cutoff=parse(observed_at).date().isoformat()
        for row in adjusted_bars:
            asset_id=str(row.get("asset_id") or "");day=self._row_day(row,("date","trade_date"));close=self._optional_decimal(row.get("close"))
            if asset_id and day and day <= cutoff and close is not None and close>0:
                histories.setdefault(asset_id,[]).append({"date":day,"close":close})
        for rows in histories.values():rows.sort(key=lambda item:item["date"])
        settled=self._settled_stock_signal_assets(signal_manifest_id);outcomes=[];as_of=str(signals["as_of"])[:10]
        for signal in signals.get("signals",[]):
            if signal["asset_id"] in settled:continue
            rows=histories.get(signal["asset_id"],[]);dates=[item["date"] for item in rows]
            if as_of not in dates:continue
            start=dates.index(as_of);end=start+signal["horizon_sessions"]
            if end>=len(rows):continue
            actual=rows[end]["close"]/rows[start]["close"]-Decimal("1");expected=Decimal(signal["expected_return_vs_cash"])
            outcomes.append({"task_line":"stock","asset_id":signal["asset_id"],"ts_code":signal["ts_code"],"horizon_sessions":signal["horizon_sessions"],"expected_return_vs_cash":signal["expected_return_vs_cash"],"realized_return_vs_cash":self._decimal_text(actual),"direction_correct":(expected>0 and actual>0) or (expected<=0 and actual<=0),"start_date":dates[start],"end_date":dates[end],"sample_status":"insufficient_evidence"})
        return self.c.data.manifest_publish(kind=STOCK_SIGNAL_OUTCOMES_KIND,schema_version=STOCK_SIGNAL_OUTCOMES_SCHEMA,manifest={"program_id":program_id,"signal_manifest_id":signal_manifest_id,"observed_at":observed_at,"outcomes":outcomes,"status":"settled" if outcomes else "waiting_for_horizon","research_only":True,"not_a_recommendation":True,"model_tokens":0})

    def generate_fund_provisional_signals(
        self, *, program_id: str, candidate_manifest_id: str, horizon_sessions: int = 20
    ) -> dict[str, Any]:
        """Freeze a falsifiable trend recommendation and its validation cohort.

        The forecast is shrunk from trailing return and benchmarked to cash.
        It is usable immediately as a *provisional* recommendation, while its
        forward outcomes remain explicitly unvalidated and feed periodic review.
        """
        if isinstance(horizon_sessions, bool) or not isinstance(horizon_sessions, int) or not 1 <= horizon_sessions <= 120:
            raise CompanionError("V6 fund signal horizon_sessions must be within 1..120")
        candidates=self.fund_candidates_get(candidate_manifest_id)["manifest"]["manifest"]
        if candidates.get("program_id") != program_id:raise CompanionError("V6 fund candidates belong to another program")
        signals=[]
        for item in candidates.get("candidates",[]):
            trailing=Decimal(item["trailing_return"]);expected=trailing*Decimal("0.25")
            signals.append({"asset_id":item["asset_id"],"ts_code":item["ts_code"],"expected_return_vs_cash":self._decimal_text(expected),"signal_direction":"up" if expected>0 else "flat_or_down","recommendation_state":"provisional_action" if expected>0 else "no_action","validation_status":"unvalidated","source_trailing_return":item["trailing_return"],"classification":item["classification"],"horizon_sessions":horizon_sessions,"sample_status":"insufficient_evidence","reasons":["trend_signal_shrunk_by_0.25","forward_outcomes_pending"]})
        return self.c.data.manifest_publish(kind=FUND_SIGNALS_KIND,schema_version=FUND_SIGNALS_SCHEMA,manifest={"program_id":program_id,"as_of":candidates["as_of"],"candidate_manifest_id":candidate_manifest_id,"feature_snapshot_manifest_id":candidates["feature_snapshot_manifest_id"],"model":{"id":FUND_SIGNAL_MODEL_ID,"formula":"expected_return_vs_cash = 0.25 * trailing_return","benchmark":"cash","model_status":"provisional_recommendation_pending_validation"},"signals":signals,"research_only":True,"not_a_decision":True,"not_an_execution":True,"model_tokens":0})

    def fund_provisional_signals_get(self, manifest_id: str) -> dict[str, Any]:
        item=self.c.data.manifest_get(manifest_id,verify=True)
        if item["kind"]!=FUND_SIGNALS_KIND:raise CompanionError("manifest is not a V6 fund provisional signal set")
        return item

    def capture_fund_signal_outcomes(self, *, program_id: str, signal_manifest_id: str, fund_daily_rows: list[dict[str, Any]], observed_at: str) -> dict[str, Any]:
        """Settle only matured frozen signals using later retained fund prices."""
        parse(observed_at)
        signals=self.fund_provisional_signals_get(signal_manifest_id)["manifest"]["manifest"]
        if signals.get("program_id")!=program_id:raise CompanionError("V6 fund signals belong to another program")
        histories=self._rows_by_code(fund_daily_rows,parse(observed_at).date().isoformat())
        settled=self._settled_signal_assets(signal_manifest_id);outcomes=[]
        as_of=str(signals["as_of"])[:10]
        for signal in signals.get("signals",[]):
            if signal["asset_id"] in settled:continue
            bars=self._valid_bars(histories.get(signal["ts_code"],[]));dates=[self._row_day(row,("trade_date","date")) for row in bars]
            if as_of not in dates:continue
            start=dates.index(as_of);end=start+signal["horizon_sessions"]
            if end>=len(bars):continue
            start_close=self._optional_decimal(bars[start].get("close"));end_close=self._optional_decimal(bars[end].get("close"))
            if start_close is None or end_close is None or start_close<=0:continue
            actual=end_close/start_close-Decimal("1");expected=Decimal(signal["expected_return_vs_cash"])
            outcomes.append({"task_line":"fund_domestic_etf","asset_id":signal["asset_id"],"ts_code":signal["ts_code"],"horizon_sessions":signal["horizon_sessions"],"expected_return_vs_cash":signal["expected_return_vs_cash"],"realized_return_vs_cash":self._decimal_text(actual),"direction_correct":(expected>0 and actual>0) or (expected<=0 and actual<=0),"start_date":dates[start],"end_date":dates[end],"sample_status":"insufficient_evidence"})
        return self.c.data.manifest_publish(kind=FUND_SIGNAL_OUTCOMES_KIND,schema_version=FUND_SIGNAL_OUTCOMES_SCHEMA,manifest={"program_id":program_id,"signal_manifest_id":signal_manifest_id,"observed_at":observed_at,"outcomes":outcomes,"status":"settled" if outcomes else "waiting_for_horizon","research_only":True,"not_a_recommendation":True,"model_tokens":0})

    def fund_data_bundle_get(self, manifest_id: str) -> dict[str, Any]:
        item = self.c.data.manifest_get(manifest_id, verify=True)
        if item["kind"] != "v6_fund_data_bundle":
            raise CompanionError("manifest is not a V6 fund data bundle")
        return item

    def publish_feedback(
        self,
        *,
        forecast_manifest_id: str,
        realized_excess_return: Any,
        realized_drawdown: Any,
        observed_at: str,
        source_refs: list[str] | None = None,
        job_run_id: str | None = None,
    ) -> dict[str, Any]:
        forecast = self.forecast_get(forecast_manifest_id)
        body = forecast["manifest"]["manifest"]
        prediction = body["prediction"]
        recommendation = body["recommendation"]
        realized_excess = self._decimal(realized_excess_return, "realized_excess_return")
        drawdown = self._decimal(realized_drawdown, "realized_drawdown")
        parse(observed_at)
        net_realized = realized_excess - Decimal(prediction["all_in_cost"])
        feedback = {
            "program_id": body["program_id"],
            "forecast_manifest_id": forecast_manifest_id,
            "task_line": prediction["task_line"],
            "asset_id": prediction["asset_id"],
            "strategy_version_id": prediction["strategy_version_id"],
            "recommendation_state": recommendation["state"],
            "realized_excess_return": self._decimal_text(realized_excess),
            "net_realized_excess_return": self._decimal_text(net_realized),
            "realized_drawdown": self._decimal_text(drawdown),
            "prediction_correct_after_cost": net_realized > 0,
            "recommendation_was_actionable": recommendation["state"] in {"action", "conditional_action"},
            "observed_at": observed_at,
            "source_refs": sorted(set(source_refs or [])),
            "job_run_id": job_run_id,
            "research_only": True,
            "model_tokens": 0,
        }
        return self.c.data.manifest_publish(kind=FEEDBACK_KIND, schema_version=FEEDBACK_SCHEMA, manifest=feedback)

    def review(self, *, program_id: str, feedback_manifest_ids: list[str], reviewed_at: str, signal_outcome_manifest_ids: list[str] | None = None) -> dict[str, Any]:
        parse(reviewed_at)
        feedback = []
        for manifest_id in sorted(set(feedback_manifest_ids)):
            item = self.c.data.manifest_get(manifest_id, verify=True)
            if item["kind"] != FEEDBACK_KIND:
                raise CompanionError("V6 review input contains a non-feedback manifest")
            body = item["manifest"]["manifest"]
            if body.get("program_id") != program_id:
                raise CompanionError("V6 review input belongs to another program")
            feedback.append(body)
        by_line = {
            line: self._review_metrics([item for item in feedback if item["task_line"] == line])
            for line in sorted(TASK_LINES)
        }
        all_metrics = self._review_metrics(feedback)
        signal_outcomes=[]
        for manifest_id in sorted(set(signal_outcome_manifest_ids or [])):
            item=self.c.data.manifest_get(manifest_id,verify=True)
            if item["kind"] not in {FUND_SIGNAL_OUTCOMES_KIND,STOCK_SIGNAL_OUTCOMES_KIND}:raise CompanionError("V6 review signal input is not an outcome manifest")
            body=item["manifest"]["manifest"]
            if body.get("program_id")!=program_id:raise CompanionError("V6 signal outcome belongs to another program")
            signal_outcomes.extend(body.get("outcomes",[]))
        signal_correct=sum(1 for item in signal_outcomes if item.get("direction_correct"))
        signal_by_line={}
        for line in sorted(TASK_LINES):
            line_items=[item for item in signal_outcomes if item.get("task_line")==line]
            correct=sum(1 for item in line_items if item.get("direction_correct"))
            signal_by_line[line]={"observations":len(line_items),"direction_correct_count":correct,"direction_correct_rate":self._ratio(correct,len(line_items)),"sample_status":"insufficient_evidence" if len(line_items)<10 else "ready_for_primary_review"}
        body = {
            "program_id": program_id,
            "reviewed_at": reviewed_at,
            "feedback_manifest_ids": sorted(set(feedback_manifest_ids)),
            "task_lines": by_line,
            "aggregate": all_metrics,
            "provisional_signal_outcomes":{"manifest_ids":sorted(set(signal_outcome_manifest_ids or [])),"observations":len(signal_outcomes),"direction_correct_count":signal_correct,"direction_correct_rate":self._ratio(signal_correct,len(signal_outcomes)),"sample_status":"insufficient_evidence" if len(signal_outcomes)<10 else "ready_for_primary_review","task_lines":signal_by_line},
            "assessment": {
                "status": "insufficient_evidence" if all_metrics["observations"] < 10 else "ready_for_primary_review",
                "automatic_strategy_change": False,
                "no_action_review_required": any(
                    value["observations"] >= 10 and value["actionable_count"] == 0
                    for value in by_line.values()
                ),
            },
            "research_only": True,
            "not_a_decision": True,
            "model_tokens": 0,
        }
        return self.c.data.manifest_publish(kind=REVIEW_KIND, schema_version=REVIEW_SCHEMA, manifest=body)

    def _ensure_definitions(self) -> list[dict[str, Any]]:
        common_input = {
            "type": "object",
            "required": ["refs", "parameters", "knowledge_cutoff"],
            "properties": {"refs": {"type": "array"}, "parameters": {"type": "object"}, "knowledge_cutoff": {"type": "string"}},
            "additionalProperties": False,
        }
        output = {
            "type": "object",
            "required": ["manifest_id", "output_refs", "material", "model_tokens"],
            "properties": {"manifest_id": {"type": "string"}, "output_refs": {"type": "array"}, "material": {"type": "boolean"}, "model_tokens": {"type": "integer"}},
            "additionalProperties": True,
        }
        budget = {"max_wall_seconds": 120, "max_cpu_seconds": 90, "max_memory_mb": 512, "max_input_bytes": 300_000, "max_output_bytes": 1_000_000, "lease_seconds": 300, "network": "deny", "model_tokens": 0}
        data_budget = {"max_wall_seconds": 300, "max_cpu_seconds": 120, "max_memory_mb": 1024, "max_input_bytes": 300_000, "max_output_bytes": 5_000_000, "lease_seconds": 420, "network": "tushare_official", "model_tokens": 0}
        specifications = [
            ("V6 Stock Forecast", STOCK_HANDLER),
            ("V6 Stock Candidate Scan", STOCK_CANDIDATE_HANDLER),
            ("V6 Stock Provisional Signal", STOCK_SIGNAL_HANDLER),
            ("V6 Fund / ETF Forecast", FUND_HANDLER),
            ("V6 Fund / ETF Candidate Scan", FUND_CANDIDATE_HANDLER),
            ("V6 Fund / ETF Provisional Signal", FUND_SIGNAL_HANDLER),
            ("V6 Predictive Feedback Review", REVIEW_HANDLER),
        ]
        existing = {item["handler"]: item for item in self.c.jobs.definition_list()}
        result = []
        specifications.insert(0, ("V6 Fund / ETF Canary Data", FUND_DATA_HANDLER))
        for name, handler in specifications:
            item = existing.get(handler)
            if not item:
                item = self.c.jobs.definition_create(name=name, handler=handler, handler_version="1", status="inactive", input_schema=common_input, output_schema=output, resource_budget=data_budget if handler == FUND_DATA_HANDLER else budget)
            result.append(item)
        return result

    def _ensure_fund_streams(self) -> list[dict[str, Any]]:
        from .tushare_adapter import NORMALIZER_VERSION
        result=[]
        for capability in ("etf_basic", "fund_daily", "fund_share"):
            try:
                stream=self.c.data.stream_get("tushare", capability)
            except CompanionError:
                stream=self.c.data.stream_configure(provider="tushare", capability=capability, schema_version=NORMALIZER_VERSION, config={"mode": V6_MODE, "research_only": True}, status="canary")
            if stream["schema_version"] != NORMALIZER_VERSION:
                raise CompanionError(f"V6 ETF stream uses another normalizer: {capability}")
            if stream["status"] == "active":
                raise CompanionError(f"V6 ETF research refuses an active production stream: {capability}")
            if stream["status"] in {"paused", "blocked", "inactive"}:
                stream=self.c.data.stream_set_status("tushare", capability, "canary")
            if stream["status"] != "canary":
                raise CompanionError(f"V6 ETF research requires a canary stream: {capability}")
            result.append(stream)
        return result

    def _ensure_schedules(self, config: dict[str, Any], definitions: list[dict[str, Any]]) -> list[dict[str, Any]]:
        by_handler={item["handler"]: item for item in definitions}
        policy={"no_broker": True, "no_direct_decision": True, "no_auto_strategy_change": True}
        specifications=[
            {"role":"stock_candidates", "kind":"maintenance", "name":"V6 股票：研究候选扫描", "mission":"基于冻结的 V5 A 股日线与复权扫描生成独立 V6 股票候选；候选不是预测或交易。", "cadence":{"type":"local_time","at":"18:20","timezone":"Asia/Shanghai","weekdays":[0,1,2,3,4]}, "parameters":{"program_id":config["program_id"],"top_k":10}, "handler":STOCK_CANDIDATE_HANDLER},
            {"role":"stock_signals", "kind":"maintenance", "name":"V6 股票：冻结预测信号", "mission":"冻结可否证的股票趋势预测并自动结算未来结果；从首日给出暂定推荐或不推荐。", "cadence":{"type":"local_time","at":"18:30","timezone":"Asia/Shanghai","weekdays":[0,1,2,3,4]}, "parameters":{"program_id":config["program_id"],"horizon_sessions":20}, "handler":STOCK_SIGNAL_HANDLER},
            {"role":"etf_data", "kind":"maintenance", "name":"V6 ETF：收盘数据与特征", "mission":"采集冻结的境内 ETF 身份、日线和份额，构建标的池与特征；不预测、不推荐、不交易。", "cadence":{"type":"local_time","at":"17:40","timezone":"Asia/Shanghai","weekdays":[0,1,2,3,4]}, "parameters":{"program_id":config["program_id"]}, "handler":FUND_DATA_HANDLER},
            {"role":"etf_candidates", "kind":"maintenance", "name":"V6 ETF：研究候选扫描", "mission":"从最新冻结 ETF 特征生成透明研究候选；候选不是预测或推荐。", "cadence":{"type":"local_time","at":"18:00","timezone":"Asia/Shanghai","weekdays":[0,1,2,3,4]}, "parameters":{"program_id":config["program_id"],"top_k":20}, "handler":FUND_CANDIDATE_HANDLER},
            {"role":"etf_signals", "kind":"maintenance", "name":"V6 ETF：冻结预测信号", "mission":"冻结可否证 ETF 趋势预测信号，输出暂定推荐或不推荐，并等待未来行情自动结算。", "cadence":{"type":"local_time","at":"18:10","timezone":"Asia/Shanghai","weekdays":[0,1,2,3,4]}, "parameters":{"program_id":config["program_id"],"horizon_sessions":20}, "handler":FUND_SIGNAL_HANDLER},
            {"role":"feedback_review", "kind":"review", "name":"V6 预测推荐：15 天反馈复核", "mission":"每 15 个自然日汇总股票与基金预测的冻结结果、待验证状态、命中、成本后相对收益、下行和未推荐原因；不得自动修改策略或生成交易。", "cadence":{"type":"interval","seconds":1296000}, "parameters":{"program_id":config["program_id"]}, "handler":REVIEW_HANDLER, "report":True},
        ]
        saved=[];existing=self.c.schedule_list()
        for spec in specifications:
            origin={"system":V6_MODE,"program_id":config["program_id"],"role":spec["role"],"user_approval_ref":config["user_approval_ref"]}
            expected={"name":spec["name"],"mission":spec["mission"],"cadence":spec["cadence"],"scope":{"refs":[],"parameters":spec["parameters"]},"policy":{**policy,"report_every_successful_run":bool(spec.get("report"))},"origin":origin,"dispatch_type":"deterministic_pipeline","job_definition_id":by_handler[spec["handler"]]["id"]}
            accepted_roles={spec["role"]}
            if spec["role"]=="feedback_review":
                accepted_roles.add("monthly_feedback_review")
            item=next((value for value in existing if value.get("origin",{}).get("system")==V6_MODE and value.get("origin",{}).get("program_id")==config["program_id"] and value.get("origin",{}).get("role") in accepted_roles and value["status"]!="archived"),None)
            if item:
                observed={key:item.get(key) for key in expected}
                if canonical(observed)!=canonical(expected):item=self.c.schedule_patch(item["id"],item["version"],expected,actor="v6-bootstrap",reason="align V6 ETF research schedule")
            else:item=self.c.schedule_create(kind=spec["kind"],timezone="Asia/Shanghai",actor="v6-bootstrap",**expected)
            saved.append(item)
        return saved

    def _job_stock_forecast(self, context: dict[str, Any]) -> dict[str, Any]:
        return self._job_forecast(context, allowed_task_lines={"stock"})

    def _job_stock_candidate_scan(self, context: dict[str, Any]) -> dict[str, Any]:
        config=self.require_program();parameters=context["inputs"].get("parameters",{})
        if set(parameters)!={"program_id","top_k"} or parameters["program_id"]!=config["program_id"]:
            raise CompanionError("V6 stock candidate scan has invalid program or parameters")
        signal_ids=self._stock_signal_ids(config["program_id"],context["inputs"]["knowledge_cutoff"])
        signal_assets=set()
        for signal_id in signal_ids:
            body=self.stock_provisional_signals_get(signal_id)["manifest"]["manifest"]
            signal_assets.update(str(item.get("asset_id")) for item in body.get("signals",[]) if item.get("asset_id"))
        bars=stock_adjusted_bars(self,context["inputs"]["knowledge_cutoff"],asset_ids=signal_assets) if signal_assets else []
        outcome_ids=[]
        for signal_id in signal_ids:
            outcome=self.capture_stock_signal_outcomes(program_id=config["program_id"],signal_manifest_id=signal_id,adjusted_bars=bars,observed_at=context["inputs"]["knowledge_cutoff"])
            outcome_ids.append(outcome["id"])
        scan_id=self._latest_v5_stock_scan_id(context["inputs"]["knowledge_cutoff"])
        if not scan_id or not manifest_matches_cutoff_day(self,scan_id,context["inputs"]["knowledge_cutoff"]):
            return publish_waiting_dependency(self,kind=STOCK_CANDIDATES_KIND,schema_version=STOCK_CANDIDATES_SCHEMA,program_id=config["program_id"],as_of=context["inputs"]["knowledge_cutoff"],dependency="ready_stock_source_scan",event_summary="Stock candidate scan is waiting for its frozen source scan")
        existing=self._stock_candidate_for_v5_scan(config["program_id"],scan_id)
        report=existing or self.generate_stock_research_candidates(program_id=config["program_id"],source_v5_scan_manifest_id=scan_id,top_k=parameters["top_k"])
        body=report["manifest"]["manifest"]
        return {"manifest_id":report["id"],"output_refs":[report["id"],scan_id,*outcome_ids],"material":bool(body.get("candidates")),"model_tokens":0,"event_summary":f"V6 stock candidates: {body['status']}; {len(outcome_ids)} outcome batches checked"}

    def _job_stock_provisional_signal(self, context: dict[str, Any]) -> dict[str, Any]:
        config=self.require_program();parameters=context["inputs"].get("parameters",{})
        if set(parameters)!={"program_id","horizon_sessions"} or parameters["program_id"]!=config["program_id"]:
            raise CompanionError("V6 stock provisional signal has invalid program or parameters")
        candidate_id=self._latest_stock_candidate_id(config["program_id"],context["inputs"]["knowledge_cutoff"])
        if not candidate_id or not manifest_matches_cutoff_day(self,candidate_id,context["inputs"]["knowledge_cutoff"]):
            return publish_waiting_dependency(self,kind=STOCK_SIGNALS_KIND,schema_version=STOCK_SIGNALS_SCHEMA,program_id=config["program_id"],as_of=context["inputs"]["knowledge_cutoff"],dependency="stock_research_candidate_list",event_summary="Stock signal freeze is waiting for a candidate list")
        existing=self._stock_signal_for_candidate(config["program_id"],candidate_id)
        report=existing or self.generate_stock_provisional_signals(program_id=config["program_id"],candidate_manifest_id=candidate_id,horizon_sessions=parameters["horizon_sessions"])
        signals=report["manifest"]["manifest"]["signals"]
        return {"manifest_id":report["id"],"output_refs":[report["id"],candidate_id],"material":any(item["recommendation_state"]=="provisional_action" for item in signals),"model_tokens":0,"event_summary":"V6 stock provisional recommendations frozen; forward validation pending"}

    def _job_fund_forecast(self, context: dict[str, Any]) -> dict[str, Any]:
        return self._job_forecast(context, allowed_task_lines=FUND_TASK_LINES)

    def _job_fund_candidate_scan(self, context: dict[str, Any]) -> dict[str, Any]:
        config = self.require_program()
        parameters = context["inputs"].get("parameters", {})
        allowed=({"program_id", "feature_snapshot_manifest_id", "top_k"},{"program_id","top_k"})
        if set(parameters) not in allowed or parameters["program_id"] != config["program_id"]:
            raise CompanionError("V6 fund candidate scan has invalid program or parameters")
        feature_id=parameters.get("feature_snapshot_manifest_id") or self._latest_fund_feature_snapshot_id(config["program_id"],context["inputs"]["knowledge_cutoff"])
        if not feature_id:
            return publish_waiting_dependency(self,kind=FUND_CANDIDATES_KIND,schema_version=FUND_CANDIDATES_SCHEMA,program_id=config["program_id"],as_of=context["inputs"]["knowledge_cutoff"],dependency="frozen_etf_feature_snapshot",event_summary="ETF candidate scan is waiting for its frozen feature history")
        report = self.generate_fund_research_candidates(
            program_id=config["program_id"],
            feature_snapshot_manifest_id=feature_id,
            top_k=parameters["top_k"],
        )
        body = report["manifest"]["manifest"]
        return {"manifest_id": report["id"], "output_refs": [report["id"], feature_id], "material": False, "model_tokens": 0, "event_summary": f"V6 ETF research candidates: {body['status']}; not a forecast or Decision"}

    def _job_fund_provisional_signal(self, context: dict[str, Any]) -> dict[str, Any]:
        config=self.require_program();parameters=context["inputs"].get("parameters",{})
        if set(parameters)!={"program_id","horizon_sessions"} or parameters["program_id"]!=config["program_id"]:raise CompanionError("V6 fund provisional signal has invalid program or parameters")
        candidate_id=self._latest_fund_candidate_id(config["program_id"],context["inputs"]["knowledge_cutoff"])
        if not candidate_id:
            return publish_waiting_dependency(self,kind=FUND_SIGNALS_KIND,schema_version=FUND_SIGNALS_SCHEMA,program_id=config["program_id"],as_of=context["inputs"]["knowledge_cutoff"],dependency="etf_research_candidate_list",event_summary="ETF signal freeze is waiting for a candidate list")
        report=self.generate_fund_provisional_signals(program_id=config["program_id"],candidate_manifest_id=candidate_id,horizon_sessions=parameters["horizon_sessions"])
        signals=report["manifest"]["manifest"]["signals"]
        return {"manifest_id":report["id"],"output_refs":[report["id"],candidate_id],"material":any(item["recommendation_state"]=="provisional_action" for item in signals),"model_tokens":0,"event_summary":"V6 ETF provisional recommendations frozen; forward validation pending"}

    def _job_fund_data_bundle(self, context: dict[str, Any]) -> dict[str, Any]:
        return job_fund_data_bundle(self, context, lookback_sessions=ETF_LOOKBACK_SESSIONS, bootstrap_per_run=ETF_BOOTSTRAP_SESSIONS_PER_RUN)

    def _job_forecast(self, context: dict[str, Any], *, allowed_task_lines: set[str]) -> dict[str, Any]:
        config = self.require_program()
        parameters = context["inputs"].get("parameters", {})
        required = {"program_id", "prediction", "thresholds", "portfolio_assessment"}
        if set(parameters) != required or parameters["program_id"] != config["program_id"]:
            raise CompanionError("V6 forecast job has invalid program or parameters")
        prediction = parameters["prediction"]
        if not isinstance(prediction, dict) or prediction.get("task_line") not in allowed_task_lines:
            raise CompanionError("V6 forecast was routed to the wrong task line")
        report = self.publish_forecast(program_id=config["program_id"], prediction=prediction, thresholds=parameters["thresholds"], portfolio_assessment=parameters["portfolio_assessment"], source_refs=self._refs(context), job_run_id=context["job_run_id"])
        state = report["manifest"]["manifest"]["recommendation"]["state"]
        return {"manifest_id": report["id"], "output_refs": [report["id"]], "material": state in {"action", "conditional_action"}, "model_tokens": 0, "event_summary": f"V6 {prediction['task_line']} forecast recorded as {state}; not a Decision"}

    def _job_feedback_review(self, context: dict[str, Any]) -> dict[str, Any]:
        config = self.require_program()
        parameters = context["inputs"].get("parameters", {})
        if set(parameters) != {"program_id"} or parameters["program_id"] != config["program_id"]:
            raise CompanionError("V6 feedback review accepts only the configured program_id")
        feedback_ids = self._feedback_ids(config["program_id"], before=context["inputs"]["knowledge_cutoff"])
        signal_outcome_ids=self._signal_outcome_ids(config["program_id"],context["inputs"]["knowledge_cutoff"])
        report = self.review(program_id=config["program_id"], feedback_manifest_ids=feedback_ids, signal_outcome_manifest_ids=signal_outcome_ids, reviewed_at=context["inputs"]["knowledge_cutoff"])
        status = report["manifest"]["manifest"]["assessment"]["status"]
        return {"manifest_id": report["id"], "output_refs": [report["id"], *feedback_ids], "material": True, "model_tokens": 0, "event_summary": f"V6 predictive feedback review: {status}; Primary report required"}

    def _feedback_ids(self, program_id: str, *, before: str) -> list[str]:
        cutoff = parse(before)
        with self.db.connect() as con:
            rows = con.execute("SELECT id,created_at,manifest_json FROM artifact_manifests WHERE kind=? ORDER BY created_at,id", (FEEDBACK_KIND,)).fetchall()
        result = []
        for row in rows:
            if parse(row["created_at"]) > cutoff:
                continue
            import json
            payload = json.loads(row["manifest_json"])
            if payload.get("manifest", {}).get("program_id") == program_id:
                result.append(row["id"])
        return result

    def _latest_fund_feature_snapshot_id(self, program_id: str, before: str) -> str | None:
        cutoff=parse(before)
        with self.db.connect() as con:
            rows=con.execute("SELECT id,created_at,manifest_json FROM artifact_manifests WHERE kind=? ORDER BY created_at DESC,id DESC",(FUND_FEATURES_KIND,)).fetchall()
        for row in rows:
            if parse(row["created_at"])>cutoff:continue
            payload=json.loads(row["manifest_json"])
            if payload.get("manifest",{}).get("program_id")==program_id:return row["id"]
        return None

    def _latest_fund_candidate_id(self, program_id: str, before: str) -> str | None:
        cutoff=parse(before)
        with self.db.connect() as con:rows=con.execute("SELECT id,created_at,manifest_json FROM artifact_manifests WHERE kind=? ORDER BY created_at DESC,id DESC",(FUND_CANDIDATES_KIND,)).fetchall()
        for row in rows:
            if parse(row["created_at"])>cutoff:continue
            body=json.loads(row["manifest_json"]).get("manifest",{})
            if body.get("program_id")==program_id and body.get("status")!="waiting_upstream":return row["id"]
        return None

    def _latest_stock_candidate_id(self, program_id: str, before: str) -> str | None:
        return self._latest_manifest_id(STOCK_CANDIDATES_KIND,program_id,before)

    def _stock_candidate_for_v5_scan(self, program_id: str, scan_id: str) -> dict[str, Any] | None:
        with self.db.connect() as con:rows=con.execute("SELECT id,manifest_json FROM artifact_manifests WHERE kind=? ORDER BY created_at DESC,id DESC",(STOCK_CANDIDATES_KIND,)).fetchall()
        for row in rows:
            body=json.loads(row["manifest_json"]).get("manifest",{})
            if body.get("program_id")==program_id and body.get("source_v5_scan_manifest_id")==scan_id:
                return self.stock_candidates_get(row["id"])
        return None

    def _stock_signal_for_candidate(self, program_id: str, candidate_id: str) -> dict[str, Any] | None:
        with self.db.connect() as con:rows=con.execute("SELECT id,manifest_json FROM artifact_manifests WHERE kind=? ORDER BY created_at DESC,id DESC",(STOCK_SIGNALS_KIND,)).fetchall()
        for row in rows:
            body=json.loads(row["manifest_json"]).get("manifest",{})
            if body.get("program_id")==program_id and body.get("candidate_manifest_id")==candidate_id:
                return self.stock_provisional_signals_get(row["id"])
        return None

    def _latest_manifest_id(self, kind: str, program_id: str, before: str) -> str | None:
        cutoff=parse(before)
        with self.db.connect() as con:rows=con.execute("SELECT id,created_at,manifest_json FROM artifact_manifests WHERE kind=? ORDER BY created_at DESC,id DESC",(kind,)).fetchall()
        for row in rows:
            if parse(row["created_at"])>cutoff:continue
            body=json.loads(row["manifest_json"]).get("manifest",{})
            if body.get("program_id")==program_id and body.get("status")!="waiting_upstream":return row["id"]
        return None

    def _latest_v5_stock_scan_id(self, before: str) -> str | None:
        cutoff=parse(before)
        with self.db.connect() as con:rows=con.execute("SELECT id,created_at,manifest_json FROM artifact_manifests WHERE kind='v5_canary_quant_scan' ORDER BY created_at DESC,id DESC").fetchall()
        for row in rows:
            if parse(row["created_at"])>cutoff:continue
            body=json.loads(row["manifest_json"]).get("manifest",{})
            if isinstance(body.get("program_id"),str) and body.get("status")=="ready":return row["id"]
        return None

    def _fund_signal_ids(self, program_id: str, before: str) -> list[str]:
        cutoff=parse(before);result=[]
        with self.db.connect() as con:rows=con.execute("SELECT id,created_at,manifest_json FROM artifact_manifests WHERE kind=? ORDER BY created_at,id",(FUND_SIGNALS_KIND,)).fetchall()
        for row in rows:
            body=json.loads(row["manifest_json"]).get("manifest",{})
            if parse(row["created_at"])<=cutoff and body.get("program_id")==program_id and body.get("status")!="waiting_upstream":result.append(row["id"])
        return result

    def _signal_outcome_ids(self, program_id: str, before: str) -> list[str]:
        cutoff=parse(before);result=[]
        with self.db.connect() as con:rows=con.execute("SELECT id,created_at,manifest_json FROM artifact_manifests WHERE kind IN (?,?) ORDER BY created_at,id",(FUND_SIGNAL_OUTCOMES_KIND,STOCK_SIGNAL_OUTCOMES_KIND)).fetchall()
        for row in rows:
            if parse(row["created_at"])<=cutoff and json.loads(row["manifest_json"]).get("manifest",{}).get("program_id")==program_id:result.append(row["id"])
        return result

    def _settled_signal_assets(self, signal_manifest_id: str) -> set[str]:
        result=set()
        with self.db.connect() as con:rows=con.execute("SELECT manifest_json FROM artifact_manifests WHERE kind=?",(FUND_SIGNAL_OUTCOMES_KIND,)).fetchall()
        for row in rows:
            body=json.loads(row["manifest_json"]).get("manifest",{})
            if body.get("signal_manifest_id")==signal_manifest_id:result.update(str(item.get("asset_id")) for item in body.get("outcomes",[]) if item.get("asset_id"))
        return result

    def _stock_signal_ids(self, program_id: str, before: str) -> list[str]:
        cutoff=parse(before);result=[]
        with self.db.connect() as con:rows=con.execute("SELECT id,created_at,manifest_json FROM artifact_manifests WHERE kind=? ORDER BY created_at,id",(STOCK_SIGNALS_KIND,)).fetchall()
        for row in rows:
            body=json.loads(row["manifest_json"]).get("manifest",{})
            if parse(row["created_at"])<=cutoff and body.get("program_id")==program_id and body.get("status")!="waiting_upstream":result.append(row["id"])
        return result

    def _settled_stock_signal_assets(self, signal_manifest_id: str) -> set[str]:
        result=set()
        with self.db.connect() as con:rows=con.execute("SELECT manifest_json FROM artifact_manifests WHERE kind=?",(STOCK_SIGNAL_OUTCOMES_KIND,)).fetchall()
        for row in rows:
            body=json.loads(row["manifest_json"]).get("manifest",{})
            if body.get("signal_manifest_id")==signal_manifest_id:result.update(str(item.get("asset_id")) for item in body.get("outcomes",[]) if item.get("asset_id"))
        return result

    def _rows_from_canonical_batch(self, capability: str, batch: dict[str, Any]) -> list[dict[str, Any]]:
        object_ids=batch.get("canonical_object_ids",[])
        if not object_ids:return []
        if not canonical_object_ready(self.c.data,object_ids[0]):return []
        value=json.loads(self.c.data.object_read(object_ids[0]).decode("utf-8"))
        if capability=="fund_daily":
            result=[]
            for row in value:
                code=str(row.get("asset_id") or "").removeprefix("tushare:")
                if code:
                    result.append({"ts_code":code,"trade_date":str(row.get("date") or "").replace("-",""),"close":row.get("close"),"amount":row.get("amount")})
            return result
        if isinstance(value,dict):
            return [item.get("value",{}) for item in value.get("facts",[]) if isinstance(item,dict) and isinstance(item.get("value"),dict)]
        return []

    def _historical_canonical_rows(self, capability: str, before: str, *, include_object_ids: list[str] | None = None) -> list[dict[str, Any]]:
        cutoff=parse(before);stream=self.c.data.stream_get("tushare",capability);result=[]
        included=set(include_object_ids or [])
        batches=self.c.data.batch_list(stream["id"],status="ready")
        for batch in sorted(batches,key=lambda item:(item.get("finished_at") or "",item["id"])):
            finished=batch.get("finished_at")
            object_ids=set(batch.get("canonical_object_ids",[]))
            if not finished or (parse(finished)>cutoff and not object_ids.intersection(included)):continue
            result.extend(self._rows_from_canonical_batch(capability,batch))
        return result

    def _historical_canonical_object_ids(self, capability: str, before: str, *, include_object_ids: list[str] | None = None) -> list[str]:
        cutoff=parse(before);stream=self.c.data.stream_get("tushare",capability);result=[]
        included=set(include_object_ids or [])
        for batch in self.c.data.batch_list(stream["id"],status="ready"):
            finished=batch.get("finished_at")
            object_ids=set(batch.get("canonical_object_ids",[]))
            if not finished or (parse(finished)>cutoff and not object_ids.intersection(included)):continue
            for object_id in batch.get("canonical_object_ids",[]):
                if not canonical_object_ready(self.c.data,object_id):continue
                result.append(object_id)
        return sorted(set(result))

    @staticmethod
    def _refs(context: dict[str, Any]) -> list[str]:
        return sorted({item["id"] for item in context["inputs"].get("refs", []) if isinstance(item, dict) and isinstance(item.get("id"), str)})

    def _normalize_prediction(self, prediction: dict[str, Any]) -> dict[str, Any]:
        required = {"task_line", "asset_id", "strategy_version_id", "as_of", "horizon_sessions", "expected_excess_return", "downside_return", "all_in_cost", "evidence_status", "sample_status"}
        if isinstance(prediction, dict) and prediction.get("task_line") in FUND_TASK_LINES:
            required.add("fund_universe_manifest_id")
        if not isinstance(prediction, dict) or set(prediction) != required:
            raise CompanionError("V6 prediction fields must be exactly: " + str(sorted(required)))
        line = prediction["task_line"]
        if line not in TASK_LINES:
            raise CompanionError("unsupported V6 task_line")
        for field in ("asset_id", "strategy_version_id", "as_of"):
            if not isinstance(prediction[field], str) or not prediction[field].strip():
                raise CompanionError(f"prediction.{field} must be a non-empty string")
        parse(prediction["as_of"])
        horizon = prediction["horizon_sessions"]
        if isinstance(horizon, bool) or not isinstance(horizon, int) or horizon < 1:
            raise CompanionError("prediction.horizon_sessions must be a positive integer")
        if prediction["evidence_status"] not in {"ready", "insufficient_coverage", "invalid"}:
            raise CompanionError("unsupported prediction.evidence_status")
        if prediction["sample_status"] not in {"ready", "insufficient_evidence", "invalid"}:
            raise CompanionError("unsupported prediction.sample_status")
        result = {
            "task_line": line,
            "asset_id": prediction["asset_id"],
            "strategy_version_id": prediction["strategy_version_id"],
            "as_of": prediction["as_of"],
            "horizon_sessions": horizon,
            "expected_excess_return": self._decimal_text(self._decimal(prediction["expected_excess_return"], "expected_excess_return")),
            "downside_return": self._decimal_text(self._decimal(prediction["downside_return"], "downside_return")),
            "all_in_cost": self._decimal_text(self._nonnegative_decimal(prediction["all_in_cost"], "all_in_cost")),
            "evidence_status": prediction["evidence_status"],
            "sample_status": prediction["sample_status"],
        }
        if line in FUND_TASK_LINES:
            universe_id = prediction["fund_universe_manifest_id"]
            if not isinstance(universe_id, str) or not universe_id.strip():
                raise CompanionError("prediction.fund_universe_manifest_id must be a non-empty string")
            result["fund_universe_manifest_id"] = universe_id
        return result

    def _eligible_fund_member(
        self, manifest_id: str, *, program_id: str, asset_id: str
    ) -> dict[str, Any]:
        body = self.fund_universe_get(manifest_id)["manifest"]["manifest"]
        if body.get("program_id") != program_id:
            raise CompanionError("V6 fund universe belongs to another program")
        member = next((item for item in body.get("members", []) if item.get("asset_id") == asset_id), None)
        if not member or not member.get("eligible"):
            raise CompanionError("fund prediction requires an eligible member in its frozen V6 fund universe")
        return member

    @staticmethod
    def _refs_from_values(value: list[str], field: str) -> list[str]:
        if not isinstance(value, list) or not value or any(not isinstance(item, str) or not item.strip() for item in value):
            raise CompanionError(f"{field} must be a non-empty string list")
        return sorted(set(value))

    @staticmethod
    def _etf_venue(value: Any) -> str | None:
        normalized = str(value or "").upper().strip()
        return {"SSE": "SSE", "SH": "SSE", "SZSE": "SZSE", "SZ": "SZSE"}.get(normalized)

    @staticmethod
    def _row_day(row: dict[str, Any], date_fields: tuple[str, ...]) -> str | None:
        for field in date_fields:
            value = str(row.get(field) or "").strip()
            if len(value) == 8 and value.isdigit():
                return f"{value[:4]}-{value[4:6]}-{value[6:]}"
            if len(value) == 10 and value[4] == "-" and value[7] == "-":
                return value
        return None

    @classmethod
    def _rows_by_code(cls, rows: list[dict[str, Any]], as_of_day: str, *, date_fields: tuple[str, ...] = ("trade_date", "date")) -> dict[str, list[dict[str, Any]]]:
        result: dict[str, list[dict[str, Any]]] = {}
        for row in rows:
            code = str(row.get("ts_code") or "").strip()
            day = cls._row_day(row, date_fields)
            if code and day and day <= as_of_day:
                result.setdefault(code, []).append(row)
        for values in result.values():
            values.sort(key=lambda item: cls._row_day(item, date_fields) or "")
        return result

    @classmethod
    def _latest_row(cls, rows: list[dict[str, Any]], date_fields: tuple[str, ...]) -> dict[str, Any] | None:
        return max(rows, key=lambda item: cls._row_day(item, date_fields) or "", default=None)

    @classmethod
    def _valid_bars(cls, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        by_day: dict[str, dict[str, Any]] = {}
        for row in rows:
            day = cls._row_day(row, ("trade_date", "date"))
            close = cls._optional_decimal(row.get("close"))
            if day and close is not None and close > 0:
                by_day[day] = row
        return [by_day[day] for day in sorted(by_day)]

    def _etf_classification(self, basic: dict[str, Any], venue: str | None, refs: list[str]) -> dict[str, Any]:
        labels=[]
        if venue:
            labels.append(f"venue:{venue.lower()}")
        etf_type = str(basic.get("etf_type") or "").strip()
        if etf_type:
            labels.append(f"provider_etf_type:{etf_type}")
        index_code = str(basic.get("index_code") or "").strip()
        if index_code:
            labels.append(f"tracking_index:{index_code}")
        return {"model_version": "v6-etf-labels/1", "labels": sorted(labels), "status": "provisional", "evidence_refs": refs}

    def _fund_features(self, code: str, bars: list[dict[str, Any]], nav: dict[str, Any] | None, share: dict[str, Any] | None, mgt_fee: Decimal | None, classification: dict[str, Any], readiness: dict[str, str], lookback_sessions: int) -> dict[str, Any]:
        closes = [self._optional_decimal(row.get("close")) for row in bars]
        closes = [value for value in closes if value is not None]
        amounts = [self._optional_decimal(row.get("amount")) for row in bars]
        amounts = [value for value in amounts if value is not None and value >= 0]
        peak=None;drawdown=None
        for close in closes:
            peak = close if peak is None or close > peak else peak
            current = close / peak - Decimal("1")
            drawdown = current if drawdown is None or current < drawdown else drawdown
        latest_close = closes[-1] if closes else None
        nav_value = self._optional_decimal(nav.get("unit_nav")) if nav else None
        premium = latest_close / nav_value - Decimal("1") if latest_close is not None and nav_value is not None and nav_value > 0 else None
        share_value = self._optional_decimal(share.get("fd_share")) if share else None
        return {
            "asset_id": f"tushare:{code}", "ts_code": code,
            "latest_trade_date": self._row_day(bars[-1], ("trade_date", "date")) if bars else None,
            "observed_sessions": len(bars), "required_sessions": lookback_sessions,
            "latest_close": self._decimal_text(latest_close) if latest_close is not None else None,
            "trailing_return": self._decimal_text(closes[-1] / closes[0] - Decimal("1")) if len(closes) >= 2 else None,
            "max_drawdown": self._decimal_text(drawdown) if drawdown is not None else None,
            "average_amount": self._decimal_text(sum(amounts) / Decimal(len(amounts))) if amounts else None,
            "nav_date": self._row_day(nav, ("nav_date", "ann_date")) if nav else None,
            "nav_premium": self._decimal_text(premium) if premium is not None else None,
            "share_date": self._row_day(share, ("trade_date",)) if share else None,
            "fund_share": self._decimal_text(share_value) if share_value is not None else None,
            "management_fee": self._decimal_text(mgt_fee) if mgt_fee is not None else None,
            "classification": classification, "data_readiness": dict(sorted(readiness.items())),
        }

    @staticmethod
    def _optional_decimal(value: Any) -> Decimal | None:
        if value is None or str(value).strip() == "":
            return None
        try:
            result = Decimal(str(value))
        except (InvalidOperation, ValueError):
            return None
        return result if result.is_finite() else None

    def _normalize_fund_member(self, member: dict[str, Any]) -> dict[str, Any]:
        required = {
            "asset_id", "ts_code", "name", "listing_venue", "domestic_tradable",
            "classification", "product_evidence_ref",
            "data_readiness",
        }
        if not isinstance(member, dict) or set(member) != required:
            raise CompanionError("V6 fund member fields must be exactly: " + str(sorted(required)))
        for field in ("asset_id", "ts_code", "name", "product_evidence_ref"):
            if not isinstance(member[field], str) or not member[field].strip():
                raise CompanionError(f"fund member {field} must be a non-empty string")
        if member["listing_venue"] not in {"SSE", "SZSE"}:
            raise CompanionError("fund member listing_venue must be SSE or SZSE")
        if not isinstance(member["domestic_tradable"], bool):
            raise CompanionError("fund member domestic_tradable must be boolean")
        readiness = member["data_readiness"]
        required_readiness = {"prices", "liquidity", "costs", "structure"}
        if not isinstance(readiness, dict) or set(readiness) != required_readiness:
            raise CompanionError("fund member data_readiness fields must be exactly: " + str(sorted(required_readiness)))
        if any(value not in {"ready", "missing", "invalid"} for value in readiness.values()):
            raise CompanionError("fund member data_readiness values must be ready, missing, or invalid")
        classification = member["classification"]
        classification_fields = {"model_version", "labels", "status", "evidence_refs"}
        if not isinstance(classification, dict) or set(classification) != classification_fields:
            raise CompanionError("fund member classification fields must be exactly: " + str(sorted(classification_fields)))
        if not isinstance(classification["model_version"], str) or not classification["model_version"].strip():
            raise CompanionError("fund member classification.model_version must be a non-empty string")
        if classification["status"] not in {"confirmed", "provisional", "unclassified"}:
            raise CompanionError("fund member classification.status is unsupported")
        labels = classification["labels"]
        refs = classification["evidence_refs"]
        if not isinstance(labels, list) or any(not isinstance(item, str) or not item.strip() for item in labels) or len(labels) != len(set(labels)):
            raise CompanionError("fund member classification.labels must be a unique string list")
        if not isinstance(refs, list) or any(not isinstance(item, str) or not item.strip() for item in refs) or len(refs) != len(set(refs)):
            raise CompanionError("fund member classification.evidence_refs must be a unique string list")
        if classification["status"] == "confirmed" and not refs:
            raise CompanionError("confirmed fund classification requires evidence_refs")
        reasons = []
        if not member["domestic_tradable"]:
            reasons.append("not_domestically_tradable")
        reasons.extend(f"{field}_{value}" for field, value in sorted(readiness.items()) if value != "ready")
        return {
            **member,
            "data_readiness": dict(sorted(readiness.items())),
            "classification": {**classification, "labels": sorted(labels), "evidence_refs": sorted(refs)},
            "eligible": not reasons,
            "exclusion_reasons": reasons,
        }

    def _normalize_thresholds(self, thresholds: dict[str, Any]) -> dict[str, Any]:
        required = {"min_net_expected_excess_return", "max_downside_return"}
        if not isinstance(thresholds, dict) or set(thresholds) != required:
            raise CompanionError("V6 thresholds must be exactly: " + str(sorted(required)))
        return {
            "min_net_expected_excess_return": self._decimal_text(self._decimal(thresholds["min_net_expected_excess_return"], "min_net_expected_excess_return")),
            "max_downside_return": self._decimal_text(self._decimal(thresholds["max_downside_return"], "max_downside_return")),
        }

    @staticmethod
    def _normalize_portfolio_assessment(value: dict[str, Any]) -> dict[str, Any]:
        required = {"improves_portfolio", "constraints_satisfied", "alternative_beaten", "conditions"}
        if not isinstance(value, dict) or set(value) != required:
            raise CompanionError("V6 portfolio assessment fields must be exactly: " + str(sorted(required)))
        if not all(isinstance(value[key], bool) for key in ("improves_portfolio", "constraints_satisfied", "alternative_beaten")):
            raise CompanionError("V6 portfolio booleans are invalid")
        if not isinstance(value["conditions"], list) or any(not isinstance(item, str) or not item.strip() for item in value["conditions"]):
            raise CompanionError("V6 portfolio conditions must be a string list")
        return {**value, "conditions": sorted(set(value["conditions"]))}

    def _evaluate(self, prediction: dict[str, Any], thresholds: dict[str, Any], portfolio: dict[str, Any]) -> dict[str, Any]:
        reasons = []
        expected_net = Decimal(prediction["expected_excess_return"]) - Decimal(prediction["all_in_cost"])
        if prediction["evidence_status"] != "ready":
            reasons.append("coverage_not_ready")
        if prediction["sample_status"] != "ready":
            reasons.append("forecast_sample_not_ready")
        if expected_net < Decimal(thresholds["min_net_expected_excess_return"]):
            reasons.append("net_expected_excess_below_threshold")
        if Decimal(prediction["downside_return"]) < Decimal(thresholds["max_downside_return"]):
            reasons.append("downside_exceeds_budget")
        if not portfolio["constraints_satisfied"]:
            reasons.append("portfolio_constraints_not_satisfied")
        if not portfolio["improves_portfolio"] or not portfolio["alternative_beaten"]:
            reasons.append("does_not_beat_no_action_or_alternative")
        if prediction["evidence_status"] != "ready":
            state = "research"
        elif any(reason in reasons for reason in {"net_expected_excess_below_threshold", "downside_exceeds_budget", "portfolio_constraints_not_satisfied", "does_not_beat_no_action_or_alternative"}):
            state = "no_action"
        elif portfolio["conditions"]:
            state = "conditional_action"
        else:
            state = "action"
        return {"state": state, "reasons": reasons, "validation_status":"unvalidated" if prediction["sample_status"] != "ready" else "forward_sample_available", "net_expected_excess_return": self._decimal_text(expected_net), "conditions": portfolio["conditions"], "not_a_trade": True}

    @staticmethod
    def _review_metrics(items: list[dict[str, Any]]) -> dict[str, Any]:
        observations = len(items)
        actionable = [item for item in items if item["recommendation_was_actionable"]]
        positive = [item for item in items if item["prediction_correct_after_cost"]]
        values = [Decimal(item["net_realized_excess_return"]) for item in items]
        drawdowns = [Decimal(item["realized_drawdown"]) for item in items]
        return {
            "observations": observations,
            "actionable_count": len(actionable),
            "action_rate": V6PredictiveRecommendations._ratio(len(actionable), observations),
            "positive_after_cost_count": len(positive),
            "positive_after_cost_rate": V6PredictiveRecommendations._ratio(len(positive), observations),
            "mean_net_realized_excess_return": V6PredictiveRecommendations._decimal_text(sum(values) / Decimal(observations)) if observations else None,
            "worst_realized_drawdown": V6PredictiveRecommendations._decimal_text(min(drawdowns)) if drawdowns else None,
        }

    @staticmethod
    def _ratio(numerator: int, denominator: int) -> str | None:
        return V6PredictiveRecommendations._decimal_text(Decimal(numerator) / Decimal(denominator)) if denominator else None

    @staticmethod
    def _decimal(value: Any, field: str) -> Decimal:
        try:
            result = Decimal(str(value))
        except (InvalidOperation, ValueError) as exc:
            raise CompanionError(f"{field} must be numeric") from exc
        if not result.is_finite():
            raise CompanionError(f"{field} must be finite")
        return result

    @staticmethod
    def _nonnegative_decimal(value: Any, field: str) -> Decimal:
        result = V6PredictiveRecommendations._decimal(value, field)
        if result < 0:
            raise CompanionError(f"{field} must be non-negative")
        return result

    @staticmethod
    def _positive_decimal(value: Any, field: str) -> Decimal:
        result = V6PredictiveRecommendations._decimal(value, field)
        if result <= 0:
            raise CompanionError(f"{field} must be positive")
        return result

    @staticmethod
    def _decimal_text(value: Decimal) -> str:
        return "0" if value == 0 else format(value.normalize(), "f")

    @staticmethod
    def _boundaries() -> dict[str, Any]:
        return {
            "separate_stock_and_fund_task_lines": True,
            "fund_task_lines": sorted(FUND_TASK_LINES),
            "classification_is_informational_only": True,
            "immutable_forecasts_and_feedback": True,
            "automatic_strategy_change": False,
            "automatic_decision_or_execution": False,
            "broker_or_auto_trade": False,
        }
