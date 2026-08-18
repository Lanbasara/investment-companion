"""Narrow, deterministic quantitative runtime for Investment Companion V4.

This module deliberately has no database, credential, network, broker, Qlib, or
third-party package dependency.  Its boundary is JSON-friendly dictionaries.
It is a research/replay component: target weights and simulated fills are not
orders and must never be interpreted as confirmed portfolio facts.

The implementation is intentionally small.  It covers the transparent V4
spike strategies and the finite A-share reality rules needed by golden cases;
it is not a general-purpose backtesting engine and makes no claim of alpha.
"""

from __future__ import annotations

import hashlib
import json
import platform
import sys
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date as calendar_date
from decimal import Decimal, InvalidOperation, ROUND_DOWN, ROUND_HALF_UP, localcontext
from typing import Any, Iterable, Mapping, Protocol, Sequence


RUNTIME_VERSION = "native-quant-runtime/1"
TARGET_WEIGHTS_SCHEMA = "investment-companion/target-weights/v1"
SIMULATION_SCHEMA = "investment-companion/portfolio-simulation/v1"
REFERENCE_SCHEMA = "investment-companion/reference-calculation/v1"
HEALTH_SCHEMA = "investment-companion/quant-runtime-health/v1"
PREPARATION_SCHEMA = "investment-companion/quant-preparation/v1"
EXPERIMENT_RESULT_SCHEMA = "investment-companion/quant-experiment-result/v1"
EXPERIMENT_BUNDLE_SCHEMA = "investment-companion/experiment-bundle/v1"
RESEARCH_EXPERIMENT_BUNDLE_SCHEMA = "investment-companion.experiment-bundle/v3"


class QuantContractError(ValueError):
    """Raised when a JSON contract is invalid or internally inconsistent."""


@contextmanager
def quant_compute_io_guard():
    """Deny external I/O while the pure native kernel evaluates a frozen spec."""

    state={"active":True}
    denied={
        "open","os.listdir","os.scandir","sqlite3.connect","socket.connect","socket.bind",
        "subprocess.Popen","os.system","os.posix_spawn","os.posix_spawnp","os.fork","os.forkpty",
        "os.exec","os.spawn","pty.spawn",
    }
    def audit(event:str,_args:tuple[Any,...])->None:
        if state["active"] and event in denied:raise PermissionError(f"NativeQuantRuntime denied external I/O: {event}")
    sys.addaudithook(audit)
    try:yield
    finally:state["active"]=False


def _json_value(value: Any) -> Any:
    """Return a strict JSON value with deterministic Decimal representation."""

    if isinstance(value, Decimal):
        return _decimal_text(value)
    if value is None or isinstance(value, (str, int, bool)):
        return value
    if isinstance(value, float):
        if value != value or value in (float("inf"), float("-inf")):
            raise QuantContractError("non-finite floats are not valid contract values")
        return _decimal_text(Decimal(str(value)))
    if isinstance(value, Mapping):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    raise QuantContractError(f"unsupported contract value: {type(value).__name__}")


def canonical_json(value: Any) -> str:
    """Serialize a contract value canonically for hashing and comparison."""

    return json.dumps(
        _json_value(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def canonical_hash(value: Any) -> str:
    """Return the SHA-256 of :func:`canonical_json`."""

    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _seal(payload: Mapping[str, Any]) -> dict[str, Any]:
    sealed = _json_value(payload)
    if not isinstance(sealed, dict):  # pragma: no cover - guarded by annotation
        raise QuantContractError("sealed payload must be an object")
    if "artifact_hash" in sealed:
        raise QuantContractError("artifact_hash is reserved")
    sealed["artifact_hash"] = canonical_hash(sealed)
    return sealed


def verify_artifact_hash(artifact: Mapping[str, Any]) -> bool:
    """Verify a content-addressed result without mutating it."""

    expected = artifact.get("artifact_hash")
    if not isinstance(expected, str):
        return False
    body = {key: value for key, value in artifact.items() if key != "artifact_hash"}
    return canonical_hash(body) == expected


def _decimal(value: Any, field: str, *, nonnegative: bool = False) -> Decimal:
    if isinstance(value, bool):
        raise QuantContractError(f"{field} must be decimal-compatible")
    try:
        result = value if isinstance(value, Decimal) else Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise QuantContractError(f"{field} must be decimal-compatible") from exc
    if not result.is_finite():
        raise QuantContractError(f"{field} must be finite")
    if nonnegative and result < 0:
        raise QuantContractError(f"{field} must be non-negative")
    return result


def _decimal_text(value: Decimal) -> str:
    if not value.is_finite():
        raise QuantContractError("non-finite decimal is not valid JSON")
    if value == 0:
        return "0"
    rendered = format(value, "f")
    if "." in rendered:
        rendered = rendered.rstrip("0").rstrip(".")
    return rendered


def _money(value: Decimal, quantum: Decimal) -> Decimal:
    return value.quantize(quantum, rounding=ROUND_HALF_UP)


def _positive_int(value: Any, field: str) -> int:
    if isinstance(value, bool):
        raise QuantContractError(f"{field} must be a positive integer")
    try:
        result = int(value)
    except (TypeError, ValueError) as exc:
        raise QuantContractError(f"{field} must be a positive integer") from exc
    if result <= 0 or str(result) != str(value).strip():
        raise QuantContractError(f"{field} must be a positive integer")
    return result


def _date_text(value: Any, field: str) -> str:
    text = str(value)
    parts = text.split("-")
    if len(parts) != 3 or tuple(map(len, parts)) != (4, 2, 2) or not all(part.isdigit() for part in parts):
        raise QuantContractError(f"{field} must use YYYY-MM-DD")
    year, month, day = (int(part) for part in parts)
    try:
        calendar_date(year, month, day)
    except ValueError as exc:
        raise QuantContractError(f"{field} is not a valid calendar date") from exc
    if year < 1:  # ``date`` already enforces this; retained as contract clarity.
        raise QuantContractError(f"{field} is not a valid calendar-shaped date")
    return text


def _asset_id(value: Any) -> str:
    if value is None:
        raise QuantContractError("asset_id must be non-empty")
    text = str(value).strip()
    if not text:
        raise QuantContractError("asset_id must be non-empty")
    return text


def _unique_assets(values: Iterable[Any]) -> list[str]:
    result = sorted({_asset_id(value) for value in values})
    if not result:
        raise QuantContractError("universe must not be empty")
    return result


def _allocated_weights(assets: Sequence[str], investable_weight: Decimal) -> list[dict[str, str]]:
    if not assets:
        return []
    quantum = Decimal("0.000000000000000001")
    with localcontext() as context:
        context.prec = 50
        base = (investable_weight / Decimal(len(assets))).quantize(quantum, rounding=ROUND_DOWN)
        values = [base] * len(assets)
        values[-1] += investable_weight - sum(values, Decimal("0"))
    return [
        {"asset_id": asset, "weight": _decimal_text(weight)}
        for asset, weight in zip(assets, values)
    ]


def _target_payload(
    *,
    as_of: str,
    effective_on: str,
    dataset_snapshot_id: str,
    strategy_version_id: str,
    method: Mapping[str, Any],
    universe: Sequence[str],
    denominator: Sequence[Mapping[str, Any]],
    selected: Sequence[str],
    requested_cash_weight: Decimal,
    warnings: Sequence[str] = (),
) -> dict[str, Any]:
    selected_assets = sorted(selected) if requested_cash_weight < 1 else []
    cash_weight = requested_cash_weight if selected_assets else Decimal("1")
    investable = Decimal("1") - cash_weight
    weights = _allocated_weights(selected_assets, investable)
    weight_sum = sum((_decimal(item["weight"], "weight") for item in weights), cash_weight)
    if weight_sum != Decimal("1"):
        raise QuantContractError("target weights and cash must sum to one")
    normalized_denominator = []
    for raw in denominator:
        row = dict(raw)
        row["selected"] = row.get("asset_id") in selected_assets
        normalized_denominator.append(row)
    return _seal(
        {
            "schema": TARGET_WEIGHTS_SCHEMA,
            "as_of": as_of,
            "effective_on": effective_on,
            "dataset_snapshot_id": str(dataset_snapshot_id),
            "strategy_version_id": str(strategy_version_id),
            "method": method,
            "universe": list(universe),
            "denominator": normalized_denominator,
            "weights": weights,
            "cash_weight": _decimal_text(cash_weight),
            "weight_sum": "1",
            "warnings": sorted(set(warnings)),
            "research_only": True,
        }
    )


@dataclass(frozen=True)
class RealitySpec:
    """Versioned finite A-share execution assumptions for replay.

    Rates are decimal fractions: ``0.0003`` means three basis points.
    All fees and cash values are rounded to ``money_quantum`` per fill.
    """

    version: str = "a-share-reality/v1"
    currency: str = "CNY"
    lot_size: int = 100
    t_plus_one: bool = True
    signal_delay: str = "next_session"
    commission_rate: str = "0.0003"
    minimum_commission: str = "5"
    sell_stamp_duty_rate: str = "0.0005"
    cash_dividend_tax_rate: str = "0"
    slippage_bps: str = "0"
    money_quantum: str = "0.01"
    price_tick: str = "0.01"

    @classmethod
    def from_value(cls, value: "RealitySpec | Mapping[str, Any] | None") -> "RealitySpec":
        if value is None:
            result = cls()
        elif isinstance(value, cls):
            result = value
        elif isinstance(value, Mapping):
            allowed = set(cls.__dataclass_fields__)
            unknown = sorted(set(value) - allowed)
            if unknown:
                raise QuantContractError(f"unknown RealitySpec fields: {', '.join(unknown)}")
            defaults = cls()
            values = {key: getattr(defaults, key) for key in allowed}
            values.update(value)
            values["version"] = str(values["version"])
            values["currency"] = str(values["currency"])
            values["lot_size"] = _positive_int(values["lot_size"], "lot_size")
            for field in (
                "commission_rate",
                "minimum_commission",
                "sell_stamp_duty_rate",
                "cash_dividend_tax_rate",
                "slippage_bps",
                "money_quantum",
                "price_tick",
            ):
                values[field] = _decimal_text(_decimal(values[field], field, nonnegative=True))
            result = cls(**values)
        else:
            raise QuantContractError("reality_spec must be an object")
        result.validate()
        return result

    def validate(self) -> None:
        _positive_int(self.lot_size, "lot_size")
        for field in (
            "commission_rate",
            "minimum_commission",
            "sell_stamp_duty_rate",
            "cash_dividend_tax_rate",
            "slippage_bps",
            "money_quantum",
            "price_tick",
        ):
            number = _decimal(getattr(self, field), field, nonnegative=True)
            if field in {"money_quantum", "price_tick"} and number <= 0:
                raise QuantContractError(f"{field} must be positive")
        if not self.version or not self.currency:
            raise QuantContractError("RealitySpec version and currency are required")
        if self.t_plus_one is not True:
            raise QuantContractError("V4 A-share RealitySpec requires t_plus_one=true")
        if self.signal_delay != "next_session":
            raise QuantContractError("V4 A-share RealitySpec requires signal_delay=next_session")

    def to_dict(self) -> dict[str, Any]:
        self.validate()
        return {
            "version": str(self.version),
            "currency": str(self.currency),
            "lot_size": int(self.lot_size),
            "t_plus_one": True,
            "signal_delay": "next_session",
            "commission_rate": _decimal_text(_decimal(self.commission_rate, "commission_rate")),
            "minimum_commission": _decimal_text(_decimal(self.minimum_commission, "minimum_commission")),
            "sell_stamp_duty_rate": _decimal_text(
                _decimal(self.sell_stamp_duty_rate, "sell_stamp_duty_rate")
            ),
            "cash_dividend_tax_rate": _decimal_text(
                _decimal(self.cash_dividend_tax_rate, "cash_dividend_tax_rate")
            ),
            "slippage_bps": _decimal_text(_decimal(self.slippage_bps, "slippage_bps")),
            "money_quantum": _decimal_text(_decimal(self.money_quantum, "money_quantum")),
            "price_tick": _decimal_text(_decimal(self.price_tick, "price_tick")),
        }


@dataclass
class _Lot:
    quantity: int
    acquired_on: str


class QuantRuntime(Protocol):
    """Framework-neutral V4 port; every method consumes/returns JSON values."""

    def health(self) -> dict[str, Any]: ...

    def prepare(
        self, *, dataset_snapshot_id: str, strategy_version: Mapping[str, Any]
    ) -> dict[str, Any]: ...

    def run(self, experiment_spec: Mapping[str, Any]) -> dict[str, Any]: ...

    def export_bundle(self, experiment_run: Mapping[str, Any]) -> dict[str, Any]: ...


class NativeQuantRuntime:
    """Project-owned deterministic runtime with a JSON-only public boundary."""

    def health(self) -> dict[str, Any]:
        """Report native health without importing or requiring Qlib."""

        return {
            "schema": HEALTH_SCHEMA,
            "runtime": "native",
            "runtime_version": RUNTIME_VERSION,
            "status": "healthy",
            "deterministic": True,
            "compute_io_guard": True,
            "external_dependencies": [],
            "environment": {"python":platform.python_version(),"implementation":platform.python_implementation(),"platform":platform.platform()},
            "qlib": {"required": False, "loaded": False, "status": "not_used"},
            "boundaries": {
                "companion_database_access": False,
                "credential_access": False,
                "network_access": False,
                "broker_access": False,
            },
            "contracts": {
                "target_weights": TARGET_WEIGHTS_SCHEMA,
                "simulation": SIMULATION_SCHEMA,
                # The native wrapper is embedded inside the project-owned,
                # lineage-rich ResearchRegistry bundle.  Report both so the
                # two similarly named contracts cannot be confused in an
                # operational health check.
                "native_experiment_bundle": EXPERIMENT_BUNDLE_SCHEMA,
                "research_experiment_bundle": RESEARCH_EXPERIMENT_BUNDLE_SCHEMA,
            },
            "capabilities": [
                "cross_sectional_momentum",
                "equal_weight",
                "finite_a_share_reality",
                "reference_daily_comparison",
            ],
        }

    def run_isolated(self,experiment_spec:Mapping[str,Any])->dict[str,Any]:
        with quant_compute_io_guard():
            return self.run(experiment_spec)

    def prepare(
        self, *, dataset_snapshot_id: str, strategy_version: Mapping[str, Any]
    ) -> dict[str, Any]:
        """Freeze the two project-owned references required by a run.

        No dataset is opened here.  File resolution and Snapshot verification
        belong to the caller-side immutable data boundary.
        """

        snapshot = str(dataset_snapshot_id).strip()
        strategy = _json_value(strategy_version)
        if not snapshot or not isinstance(strategy, dict):
            raise QuantContractError("dataset_snapshot_id and strategy_version are required")
        strategy_id = str(strategy.get("strategy_version_id", "")).strip()
        if not strategy_id:
            raise QuantContractError("strategy_version.strategy_version_id is required")
        return _seal(
            {
                "schema": PREPARATION_SCHEMA,
                "runtime_version": RUNTIME_VERSION,
                "dataset_snapshot_id": snapshot,
                "strategy_version": strategy,
                "strategy_hash": canonical_hash(strategy),
                "prepared": True,
            }
        )

    def equal_weight(
        self,
        *,
        as_of: str,
        dataset_snapshot_id: str,
        strategy_version_id: str,
        universe: Iterable[str],
        eligibility: Mapping[str, Any] | None = None,
        cash_weight: Any = "0",
        effective_on: str | None = None,
    ) -> dict[str, Any]:
        """Create a transparent equal-weight target with full denominator."""

        as_of_date = _date_text(as_of, "as_of")
        if effective_on is None:
            raise QuantContractError("effective_on is required and must be after as_of")
        effective_date = _date_text(effective_on, "effective_on")
        if effective_date <= as_of_date:
            raise QuantContractError("effective_on must be after as_of to prevent same-close execution")
        assets = _unique_assets(universe)
        cash = _decimal(cash_weight, "cash_weight", nonnegative=True)
        if cash > 1:
            raise QuantContractError("cash_weight must be between zero and one")
        eligibility = eligibility or {}
        unknown = sorted(set(eligibility) - set(assets))
        if unknown:
            raise QuantContractError(f"eligibility references assets outside universe: {unknown}")

        denominator: list[dict[str, Any]] = []
        selected: list[str] = []
        for asset in assets:
            eligible, reasons = _eligibility(eligibility.get(asset, True))
            if eligible:
                selected.append(asset)
            denominator.append(
                {
                    "asset_id": asset,
                    "eligible": eligible,
                    "exclusion_reasons": reasons,
                    "score": None,
                    "rank": None,
                    "selected": eligible,
                }
            )
        warnings = ["no_eligible_assets"] if not selected else []
        return _target_payload(
            as_of=as_of_date,
            effective_on=effective_date,
            dataset_snapshot_id=dataset_snapshot_id,
            strategy_version_id=strategy_version_id,
            method={"name": "equal_weight", "version": "1"},
            universe=assets,
            denominator=denominator,
            selected=selected,
            requested_cash_weight=cash,
            warnings=warnings,
        )

    def cross_sectional_momentum(
        self,
        *,
        as_of: str,
        dataset_snapshot_id: str,
        strategy_version_id: str,
        universe: Iterable[str],
        bars: Sequence[Mapping[str, Any]],
        lookback_sessions: int,
        top_k: int,
        eligibility: Mapping[str, Any] | None = None,
        cash_weight: Any = "0",
        effective_on: str | None = None,
    ) -> dict[str, Any]:
        """Rank close-to-close momentum cross-sectionally and equal-weight top K.

        A score for asset *i* is ``close[t] / close[t-lookback] - 1``.  Only
        observations dated on or before ``as_of`` are visible.  An asset must
        have a bar exactly on ``as_of`` and at least ``lookback + 1`` valid
        observations, making stale-price use explicit rather than implicit.
        Ties are broken by stable ``asset_id`` order.
        """

        as_of_date = _date_text(as_of, "as_of")
        if effective_on is None:
            raise QuantContractError("effective_on is required and must be after as_of")
        effective_date = _date_text(effective_on, "effective_on")
        if effective_date <= as_of_date:
            raise QuantContractError("effective_on must be after as_of to prevent same-close execution")
        assets = _unique_assets(universe)
        lookback = _positive_int(lookback_sessions, "lookback_sessions")
        count = _positive_int(top_k, "top_k")
        cash = _decimal(cash_weight, "cash_weight", nonnegative=True)
        if cash > 1:
            raise QuantContractError("cash_weight must be between zero and one")
        eligibility = eligibility or {}
        unknown = sorted(set(eligibility) - set(assets))
        if unknown:
            raise QuantContractError(f"eligibility references assets outside universe: {unknown}")

        by_asset: dict[str, list[tuple[str, Decimal]]] = {asset: [] for asset in assets}
        seen: set[tuple[str, str]] = set()
        for row in bars:
            asset = _asset_id(row.get("asset_id"))
            if asset not in by_asset:
                continue
            date = _date_text(row.get("date"), "bar.date")
            if date > as_of_date:
                continue
            key = (asset, date)
            if key in seen:
                raise QuantContractError(f"duplicate bar for {asset} on {date}")
            seen.add(key)
            close = _decimal(row.get("close"), "bar.close")
            if close <= 0:
                raise QuantContractError(f"bar.close must be positive for {asset} on {date}")
            by_asset[asset].append((date, close))

        scored: list[tuple[str, Decimal]] = []
        denominator_by_asset: dict[str, dict[str, Any]] = {}
        for asset in assets:
            eligible, reasons = _eligibility(eligibility.get(asset, True))
            history = sorted(by_asset[asset])
            if eligible and (not history or history[-1][0] != as_of_date):
                eligible = False
                reasons.append("no_as_of_bar")
            if eligible and len(history) < lookback + 1:
                eligible = False
                reasons.append("insufficient_history")
            score: Decimal | None = None
            if eligible:
                with localcontext() as context:
                    context.prec = 50
                    score = history[-1][1] / history[-1 - lookback][1] - Decimal("1")
                scored.append((asset, score))
            denominator_by_asset[asset] = {
                "asset_id": asset,
                "eligible": eligible,
                "exclusion_reasons": sorted(set(reasons)),
                "score": _decimal_text(score) if score is not None else None,
                "rank": None,
                "selected": False,
            }

        ranked = sorted(scored, key=lambda item: (-item[1], item[0]))
        selected = [asset for asset, _score in ranked[:count]]
        for rank, (asset, _score) in enumerate(ranked, start=1):
            denominator_by_asset[asset]["rank"] = rank
            denominator_by_asset[asset]["selected"] = asset in selected
        warnings = ["no_eligible_assets"] if not selected else []
        return _target_payload(
            as_of=as_of_date,
            effective_on=effective_date,
            dataset_snapshot_id=dataset_snapshot_id,
            strategy_version_id=strategy_version_id,
            method={
                "name": "cross_sectional_momentum",
                "version": "1",
                "lookback_sessions": lookback,
                "top_k": count,
                "tie_break": "asset_id_ascending",
                "signal_price": "close",
            },
            universe=assets,
            denominator=[denominator_by_asset[asset] for asset in assets],
            selected=selected,
            requested_cash_weight=cash,
            warnings=warnings,
        )

    def simulate(
        self,
        *,
        bars: Sequence[Mapping[str, Any]],
        target_portfolios: Sequence[Mapping[str, Any]],
        initial_cash: Any,
        reality_spec: RealitySpec | Mapping[str, Any] | None = None,
        initial_positions: Mapping[str, Any] | None = None,
        initial_lots: Sequence[Mapping[str, Any]] | None = None,
        corporate_actions: Sequence[Mapping[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """Replay targets through a deterministic, finite A-share reality model.

        Sells are attempted before buys.  Limit flags represent the conservative
        golden-case assumption that an order cannot fill at a one-sided limit.
        This is explicit approximation, not a claim that all limit-board orders
        are impossible in real markets.
        """

        spec = RealitySpec.from_value(reality_spec)
        cash = _money(_decimal(initial_cash, "initial_cash", nonnegative=True), _decimal(spec.money_quantum, "money_quantum"))
        market = _normalize_market_bars(bars)
        targets = _normalize_targets(target_portfolios)
        actions = _normalize_corporate_actions(corporate_actions or ())
        if not market:
            raise QuantContractError("bars must not be empty")
        simulation_dates = sorted(set(market) | set(targets))
        out_of_horizon_actions = sorted(set(actions) - set(simulation_dates))
        if out_of_horizon_actions:
            raise QuantContractError(
                "corporate actions fall outside the explicit simulation sessions: "
                f"{out_of_horizon_actions}"
            )
        lots = _initial_lots(initial_positions or {}, initial_lots or (), simulation_dates[0])
        positions = _position_quantities(lots)
        last_prices: dict[str, Decimal] = {}
        fills: list[dict[str, Any]] = []
        unfilled: list[dict[str, Any]] = []
        corporate_events: list[dict[str, Any]] = []
        daily: list[dict[str, Any]] = []
        delisted_assets:set[str]=set()

        for date in simulation_dates:
            day_corporate_start=len(corporate_events)
            for action in actions.get(date,[]):
                asset=action["asset_id"];quantity=positions.get(asset,0);kind=action["action_type"]
                if kind=="cash_dividend":
                    gross=_money(Decimal(quantity)*action["cash_per_share"],_decimal(spec.money_quantum,"money_quantum"));tax=_money(gross*_decimal(spec.cash_dividend_tax_rate,"cash_dividend_tax_rate"),_decimal(spec.money_quantum,"money_quantum"));cash+=gross-tax
                    corporate_events.append({"date":date,"action_id":action["action_id"],"asset_id":asset,"action_type":kind,"quantity":quantity,"gross_cash":_decimal_text(gross),"tax":_decimal_text(tax),"net_cash":_decimal_text(gross-tax)})
                elif kind=="split":
                    before=quantity;ratio=action["split_ratio"]
                    for lot_item in lots.get(asset,[]):
                        adjusted=Decimal(lot_item.quantity)*ratio
                        if adjusted!=adjusted.to_integral_value():raise QuantContractError(f"split creates fractional shares for {asset}")
                        lot_item.quantity=int(adjusted)
                    positions[asset]=sum(item.quantity for item in lots.get(asset,[]))
                    corporate_events.append({"date":date,"action_id":action["action_id"],"asset_id":asset,"action_type":kind,"before_quantity":before,"after_quantity":positions[asset],"split_ratio":_decimal_text(ratio)})
                elif kind=="delist":
                    settlement=_money(Decimal(quantity)*action["cash_price"],_decimal(spec.money_quantum,"money_quantum"));cash+=settlement;lots.pop(asset,None);positions.pop(asset,None);delisted_assets.add(asset)
                    corporate_events.append({"date":date,"action_id":action["action_id"],"asset_id":asset,"action_type":kind,"quantity":quantity,"cash_price":_decimal_text(action["cash_price"]),"net_cash":_decimal_text(settlement)})
            day_bars = market.get(date, {})
            for asset, bar in sorted(day_bars.items()):
                last_prices[asset] = bar["close"]
            day_fee = Decimal("0")
            day_notional = Decimal("0")
            day_fill_start = len(fills)
            day_unfilled_start = len(unfilled)

            for target in targets.get(date, []):
                target_positions, valuation_warnings = _desired_positions(
                    target=target,
                    cash=cash,
                    positions=positions,
                    prices=last_prices,
                    day_bars=day_bars,
                    lot_size=spec.lot_size,
                    money_quantum=_decimal(spec.money_quantum, "money_quantum"),
                )
                target_hash = str(target["artifact_hash"])

                # Sells fund buys.  Stable asset ordering makes partial cash use reproducible.
                for asset in sorted(set(positions) | set(target_positions)):
                    current = positions.get(asset, 0)
                    desired = target_positions.get(asset, 0)
                    if desired >= current:
                        continue
                    requested = current - desired
                    bar = day_bars.get(asset)
                    reason = "delisted" if asset in delisted_assets else _trade_block_reason("sell", bar)
                    if reason:
                        unfilled.append(_unfilled(date, target_hash, asset, "sell", requested, 0, reason))
                        continue
                    sellable = sum(lot.quantity for lot in lots.get(asset, []) if lot.acquired_on < date)
                    executable = min(requested, sellable)
                    if executable:
                        fill, cash_delta = _make_fill(date, target_hash, asset, "sell", executable, bar["close"], spec)
                        cash += cash_delta
                        day_fee += _decimal(fill["total_fees"], "total_fees")
                        day_notional += _decimal(fill["notional"], "notional")
                        fills.append(fill)
                        _consume_lots(lots, asset, executable, date)
                        positions[asset] = current - executable
                    remaining = requested - executable
                    if remaining:
                        unfilled.append(
                            _unfilled(date, target_hash, asset, "sell", requested, executable, "t_plus_one_locked")
                        )

                for asset in sorted(target_positions):
                    current = positions.get(asset, 0)
                    desired = target_positions[asset]
                    if desired <= current:
                        continue
                    requested = desired - current
                    bar = day_bars.get(asset)
                    reason = "delisted" if asset in delisted_assets else _trade_block_reason("buy", bar)
                    if reason:
                        unfilled.append(_unfilled(date, target_hash, asset, "buy", requested, 0, reason))
                        continue
                    board_lot_request = requested // spec.lot_size * spec.lot_size
                    odd_lot_remainder = requested - board_lot_request
                    executable = _affordable_quantity(board_lot_request, cash, bar["close"], spec)
                    if executable:
                        fill, cash_delta = _make_fill(date, target_hash, asset, "buy", executable, bar["close"], spec)
                        cash += cash_delta
                        day_fee += _decimal(fill["total_fees"], "total_fees")
                        day_notional += _decimal(fill["notional"], "notional")
                        fills.append(fill)
                        lots.setdefault(asset, []).append(_Lot(executable, date))
                        positions[asset] = current + executable
                    cash_limited = board_lot_request - executable
                    if cash_limited:
                        unfilled.append(
                            _unfilled(
                                date,
                                target_hash,
                                asset,
                                "buy",
                                board_lot_request,
                                executable,
                                "insufficient_cash",
                            )
                        )
                    if odd_lot_remainder:
                        unfilled.append(
                            _unfilled(
                                date,
                                target_hash,
                                asset,
                                "buy",
                                odd_lot_remainder,
                                0,
                                "board_lot_constraint",
                            )
                        )

            holdings, market_value, stale = _mark_positions(positions, last_prices, day_bars, spec)
            nav = _money(cash + market_value, _decimal(spec.money_quantum, "money_quantum"))
            daily.append(
                {
                    "date": date,
                    "cash": _decimal_text(cash),
                    "holdings": holdings,
                    "market_value": _decimal_text(market_value),
                    "nav": _decimal_text(nav),
                    "fees": _decimal_text(day_fee),
                    "traded_notional": _decimal_text(day_notional),
                    "fill_count": len(fills) - day_fill_start,
                    "unfilled_count": len(unfilled) - day_unfilled_start,
                    "corporate_event_count":len(corporate_events)-day_corporate_start,
                    "warnings": sorted(set(valuation_warnings if targets.get(date) else []) | set(stale)),
                }
            )

        payload = {
            "schema": SIMULATION_SCHEMA,
            "runtime_version": RUNTIME_VERSION,
            "reality_spec": spec.to_dict(),
            "initial_cash": _decimal_text(_money(_decimal(initial_cash, "initial_cash", nonnegative=True), _decimal(spec.money_quantum, "money_quantum"))),
            "initial_positions": _initial_position_contract(initial_positions or {}, initial_lots or ()),
            "target_hashes": [target["artifact_hash"] for date in sorted(targets) for target in targets[date]],
            "daily": daily,
            "fills": fills,
            "unfilled": unfilled,
            "corporate_events":corporate_events,
            "final_cash": daily[-1]["cash"],
            "final_holdings": daily[-1]["holdings"],
            "final_nav": daily[-1]["nav"],
            "warnings": [
                "research_simulation_not_actual_execution",
                "limit_flags_use_conservative_no_fill_assumption",
                "cash_dividend_tax_rate_is_a_versioned_approximation" if corporate_events else "no_corporate_actions_applied",
            ],
            "research_only": True,
        }
        return _seal(payload)

    def run(self, experiment_spec: Mapping[str, Any]) -> dict[str, Any]:
        """Run deterministic walk-forward targets plus an optional portfolio replay."""

        spec = _json_value(experiment_spec)
        if not isinstance(spec, dict):
            raise QuantContractError("experiment_spec must be an object")
        method = spec.get("strategy", {}).get("method") if isinstance(spec.get("strategy"), Mapping) else None
        common = {
            "dataset_snapshot_id": spec.get("dataset_snapshot_id"),
            "strategy_version_id": spec.get("strategy_version_id"),
            "universe": spec.get("universe", []),
            "cash_weight": spec.get("strategy", {}).get("cash_weight", "0"),
            "eligibility": spec.get("eligibility", {}),
        }
        pairs=spec.get("evaluation_pairs")
        if pairs is None:
            pairs=[{"as_of":spec.get("as_of"),"effective_on":spec.get("effective_on")}]
        if not isinstance(pairs,list) or not pairs:
            raise QuantContractError("evaluation_pairs must be a non-empty list")
        normalized_pairs=[];targets=[];effective_dates=set();previous_effective=None
        for index,pair in enumerate(pairs):
            if not isinstance(pair,Mapping) or set(pair)!={"as_of","effective_on"}:
                raise QuantContractError(f"evaluation_pairs[{index}] must contain only as_of and effective_on")
            as_of=_date_text(pair["as_of"],f"evaluation_pairs[{index}].as_of")
            effective=_date_text(pair["effective_on"],f"evaluation_pairs[{index}].effective_on")
            if effective<=as_of or effective in effective_dates or previous_effective and effective<=previous_effective:
                raise QuantContractError("evaluation_pairs must be unique, chronological, and next-period")
            effective_dates.add(effective);previous_effective=effective
            pair_common={**common,"as_of":as_of,"effective_on":effective}
            if method == "equal_weight":
                target = self.equal_weight(**pair_common)
            elif method == "cross_sectional_momentum":
                target = self.cross_sectional_momentum(
                    **pair_common,
                    bars=spec.get("bars", []),
                    lookback_sessions=spec.get("strategy", {}).get("lookback_sessions"),
                    top_k=spec.get("strategy", {}).get("top_k"),
                )
            else:
                raise QuantContractError(f"unsupported strategy method: {method!r}")
            normalized_pairs.append({"as_of":as_of,"effective_on":effective});targets.append(target)
        dataset_contract = spec.get("dataset_contract")
        if dataset_contract is not None:
            if not isinstance(dataset_contract, Mapping) or set(dataset_contract) != {"denominator", "universe", "exclusions"}:
                raise QuantContractError("dataset_contract must contain denominator, universe and exclusions")
            sealed_targets=[]
            for target in targets:
                unsealed = {key: value for key, value in target.items() if key != "artifact_hash"}
                unsealed["dataset_denominator"] = _json_value(dataset_contract["denominator"])
                unsealed["dataset_universe"] = _json_value(dataset_contract["universe"])
                unsealed["dataset_exclusions"] = _json_value(dataset_contract["exclusions"])
                sealed_targets.append(_seal(unsealed))
            targets=sealed_targets
        simulation_config = spec.get("simulation")
        simulation = None
        if simulation_config is not None:
            if not isinstance(simulation_config, Mapping):
                raise QuantContractError("simulation must be an object")
            simulation = self.simulate(
                bars=spec.get("bars", []),
                target_portfolios=targets,
                initial_cash=simulation_config.get("initial_cash"),
                initial_positions=simulation_config.get("initial_positions", {}),
                initial_lots=simulation_config.get("initial_lots", []),
                reality_spec=simulation_config.get("reality_spec"),
                corporate_actions=spec.get("corporate_actions", []),
            )
        return _seal(
            {
                "schema": EXPERIMENT_RESULT_SCHEMA,
                "runtime_version": RUNTIME_VERSION,
                "experiment_spec_hash": canonical_hash(spec),
                "dataset_snapshot_id": spec.get("dataset_snapshot_id"),
                "strategy_version_id": spec.get("strategy_version_id"),
                "evaluation_pairs": normalized_pairs,
                "target_portfolios": targets,
                "target_portfolio": targets[-1],
                "simulation": simulation,
                "walk_forward": len(targets)>1,
                "claim_of_alpha": False,
            }
        )

    def export_bundle(self, experiment_run: Mapping[str, Any]) -> dict[str, Any]:
        """Wrap a completed result in a framework-independent immutable bundle."""

        result = _json_value(experiment_run)
        if not isinstance(result, dict) or result.get("schema") != EXPERIMENT_RESULT_SCHEMA:
            raise QuantContractError("experiment_run does not use the supported result schema")
        if not verify_artifact_hash(result):
            raise QuantContractError("experiment_run artifact_hash is invalid")
        return _seal(
            {
                "schema": EXPERIMENT_BUNDLE_SCHEMA,
                "runtime": "native",
                "runtime_version": RUNTIME_VERSION,
                "experiment_result": result,
                "experiment_result_hash": result["artifact_hash"],
                "promotion_decision": None,
                "research_only": True,
            }
        )


class ReferenceCalculator:
    """Independent, deliberately narrow daily calculator for golden cases.

    This implementation does not call ``NativeQuantRuntime.simulate`` or its
    fill helpers.  It recalculates requested holdings, fees and T+1 availability
    in a second code path, then exposes a strict daily comparator.  It supports
    the same finite assumptions used by the V4 spike, not a second general
    backtester.
    """

    def simulate(
        self,
        *,
        bars: Sequence[Mapping[str, Any]],
        target_portfolios: Sequence[Mapping[str, Any]],
        initial_cash: Any,
        reality_spec: RealitySpec | Mapping[str, Any] | None = None,
        initial_positions: Mapping[str, Any] | None = None,
        initial_lots: Sequence[Mapping[str, Any]] | None = None,
        corporate_actions: Sequence[Mapping[str, Any]] | None = None,
    ) -> dict[str, Any]:
        spec = RealitySpec.from_value(reality_spec)
        quantum = _decimal(spec.money_quantum, "money_quantum")
        price_tick = _decimal(spec.price_tick, "price_tick")
        commission_rate = _decimal(spec.commission_rate, "commission_rate")
        minimum_commission = _decimal(spec.minimum_commission, "minimum_commission")
        stamp_rate = _decimal(spec.sell_stamp_duty_rate, "sell_stamp_duty_rate")
        slip = _decimal(spec.slippage_bps, "slippage_bps") / Decimal("10000")
        market = _normalize_market_bars(bars)
        targets = _normalize_targets(target_portfolios)
        actions = _normalize_corporate_actions(corporate_actions or ())
        if not market:
            raise QuantContractError("bars must not be empty")
        dates = sorted(set(market) | set(targets))
        outside_actions=sorted(set(actions)-set(dates))
        if outside_actions:raise QuantContractError(f"reference corporate actions fall outside explicit sessions: {outside_actions}")
        reference_lots = _initial_lots(initial_positions or {}, initial_lots or (), dates[0])
        shares = _position_quantities(reference_lots)
        cash = _money(_decimal(initial_cash, "initial_cash", nonnegative=True), quantum)
        last_price: dict[str, Decimal] = {}
        records: list[dict[str, Any]] = []
        corporate_events:list[dict[str,Any]]=[];delisted:set[str]=set()
        dividend_tax=_decimal(spec.cash_dividend_tax_rate,"cash_dividend_tax_rate")

        def cost(side: str, quantity: int, quote: Decimal) -> tuple[Decimal, Decimal, Decimal, Decimal]:
            multiplier = Decimal("1") + slip if side == "buy" else Decimal("1") - slip
            execution = _money(quote * multiplier, price_tick)
            notional = _money(execution * quantity, quantum)
            commission = _money(max(minimum_commission, notional * commission_rate), quantum)
            stamp = _money(notional * stamp_rate, quantum) if side == "sell" else Decimal("0")
            return execution, notional, commission, stamp

        for date in dates:
            event_start=len(corporate_events)
            for action in actions.get(date,[]):
                asset=action["asset_id"];quantity=shares.get(asset,0);kind=action["action_type"]
                if kind=="cash_dividend":
                    gross=_money(Decimal(quantity)*action["cash_per_share"],quantum);tax=_money(gross*dividend_tax,quantum);cash=_money(cash+gross-tax,quantum)
                    corporate_events.append({"date":date,"action_id":action["action_id"],"asset_id":asset,"action_type":kind,"quantity":quantity,"gross_cash":_decimal_text(gross),"tax":_decimal_text(tax),"net_cash":_decimal_text(gross-tax)})
                elif kind=="split":
                    before=quantity
                    for lot_item in reference_lots.get(asset,[]):
                        adjusted=Decimal(lot_item.quantity)*action["split_ratio"]
                        if adjusted!=adjusted.to_integral_value():raise QuantContractError(f"reference split creates fractional shares for {asset}")
                        lot_item.quantity=int(adjusted)
                    shares[asset]=sum(item.quantity for item in reference_lots.get(asset,[]))
                    corporate_events.append({"date":date,"action_id":action["action_id"],"asset_id":asset,"action_type":kind,"before_quantity":before,"after_quantity":shares[asset],"split_ratio":_decimal_text(action["split_ratio"])})
                elif kind=="delist":
                    settlement=_money(Decimal(quantity)*action["cash_price"],quantum);cash=_money(cash+settlement,quantum);reference_lots.pop(asset,None);shares.pop(asset,None);delisted.add(asset)
                    corporate_events.append({"date":date,"action_id":action["action_id"],"asset_id":asset,"action_type":kind,"quantity":quantity,"cash_price":_decimal_text(action["cash_price"]),"net_cash":_decimal_text(settlement)})
            today = market.get(date, {})
            for asset, row in today.items():
                last_price[asset] = row["close"]
            day_fees = Decimal("0")
            outcomes: list[dict[str, Any]] = []
            for target in targets.get(date, []):
                nav_before = cash + sum(last_price.get(asset, Decimal("0")) * quantity for asset, quantity in shares.items())
                desired: dict[str, int] = {}
                for weight in target["weights"]:
                    asset = weight["asset_id"]
                    quote = today.get(asset, {}).get("close")
                    if quote is None:
                        desired[asset] = shares.get(asset, 0)
                    else:
                        with localcontext() as context:
                            context.prec = 50
                            units = (
                                nav_before
                                * _decimal(weight["weight"], "weight")
                                / quote
                                / spec.lot_size
                            ).to_integral_value(rounding=ROUND_DOWN)
                        desired[asset] = int(units) * spec.lot_size

                for asset in sorted(set(shares) | set(desired)):
                    wanted = shares.get(asset, 0) - desired.get(asset, 0)
                    if wanted <= 0:
                        continue
                    row = today.get(asset)
                    blocked = "delisted" if asset in delisted else _trade_block_reason("sell", row)
                    available = sum(lot.quantity for lot in reference_lots.get(asset, []) if lot.acquired_on < date)
                    quantity = 0 if blocked else min(wanted, available)
                    if quantity:
                        _execution, notional, commission, stamp = cost("sell", quantity, row["close"])
                        cash = _money(cash + notional - commission - stamp, quantum)
                        day_fees += commission + stamp
                        _consume_lots(reference_lots, asset, quantity, date)
                        shares[asset] = shares.get(asset, 0) - quantity
                    outcomes.append({"asset_id": asset, "side": "sell", "requested": wanted, "filled": quantity, "reason": blocked or ("t_plus_one_locked" if quantity < wanted else None)})

                for asset in sorted(desired):
                    wanted = desired[asset] - shares.get(asset, 0)
                    if wanted <= 0:
                        continue
                    row = today.get(asset)
                    blocked = "delisted" if asset in delisted else _trade_block_reason("buy", row)
                    quantity = 0
                    if not blocked:
                        candidate = wanted // spec.lot_size * spec.lot_size
                        while candidate > 0:
                            _execution, notional, commission, _stamp = cost("buy", candidate, row["close"])
                            if notional + commission <= cash:
                                quantity = candidate
                                break
                            candidate -= spec.lot_size
                    if quantity:
                        _execution, notional, commission, _stamp = cost("buy", quantity, row["close"])
                        cash = _money(cash - notional - commission, quantum)
                        day_fees += commission
                        reference_lots.setdefault(asset, []).append(_Lot(quantity, date))
                        shares[asset] = shares.get(asset, 0) + quantity
                    outcomes.append({"asset_id": asset, "side": "buy", "requested": wanted, "filled": quantity, "reason": blocked or ("insufficient_cash" if quantity < wanted else None)})

            holdings = []
            value = Decimal("0")
            for asset in sorted(shares):
                quantity = shares[asset]
                if quantity <= 0:
                    continue
                price = last_price.get(asset)
                if price is None:
                    raise QuantContractError(f"reference calculator lacks price for held asset {asset}")
                market_value = _money(price * quantity, quantum)
                value += market_value
                holdings.append(
                    {
                        "asset_id": asset,
                        "quantity": quantity,
                        "mark_price": _decimal_text(price),
                        "market_value": _decimal_text(market_value),
                    }
                )
            value = _money(value, quantum)
            records.append(
                {
                    "date": date,
                    "cash": _decimal_text(cash),
                    "holdings": holdings,
                    "market_value": _decimal_text(value),
                    "nav": _decimal_text(_money(cash + value, quantum)),
                    "fees": _decimal_text(day_fees),
                    "corporate_event_count":len(corporate_events)-event_start,
                    "outcomes": outcomes,
                }
            )
        return _seal(
            {
                "schema": REFERENCE_SCHEMA,
                "runtime_version": RUNTIME_VERSION,
                "reality_spec": spec.to_dict(),
                "daily": records,
                "corporate_events":corporate_events,
                "research_only": True,
            }
        )

    def compare_daily(
        self, primary: Mapping[str, Any], reference: Mapping[str, Any]
    ) -> dict[str, Any]:
        """Compare discrete holdings and exact Decimal monetary strings by date."""

        primary_days = {row["date"]: row for row in primary.get("daily", [])}
        reference_days = {row["date"]: row for row in reference.get("daily", [])}
        differences: list[dict[str, Any]] = []
        fields = ("cash", "holdings", "market_value", "nav", "fees", "corporate_event_count")
        for date in sorted(set(primary_days) | set(reference_days)):
            left = primary_days.get(date)
            right = reference_days.get(date)
            if left is None or right is None:
                differences.append({"date": date, "field": "day", "primary": left, "reference": right})
                continue
            for field in fields:
                if canonical_json(left.get(field)) != canonical_json(right.get(field)):
                    differences.append(
                        {
                            "date": date,
                            "field": field,
                            "primary": left.get(field),
                            "reference": right.get(field),
                        }
                    )
        return _seal(
            {
                "schema": "investment-companion/reference-comparison/v1",
                "matched": not differences,
                "compared_dates": sorted(set(primary_days) | set(reference_days)),
                "differences": differences,
            }
        )


def _eligibility(value: Any) -> tuple[bool, list[str]]:
    if isinstance(value, bool):
        return value, [] if value else ["ineligible"]
    if isinstance(value, str):
        return False, [value]
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        reasons = sorted({str(item) for item in value if str(item)})
        return not reasons, reasons
    if isinstance(value, Mapping):
        eligible = bool(value.get("eligible", False))
        raw_reasons = value.get("reasons", [])
        if isinstance(raw_reasons, str):
            raw_reasons = [raw_reasons]
        reasons = sorted({str(item) for item in raw_reasons if str(item)})
        if not eligible and not reasons:
            reasons = ["ineligible"]
        if eligible and reasons:
            raise QuantContractError("eligible assets cannot have exclusion reasons")
        return eligible, reasons
    raise QuantContractError("eligibility must be bool, reason, reason list, or object")


def _normalize_market_bars(bars: Sequence[Mapping[str, Any]]) -> dict[str, dict[str, dict[str, Any]]]:
    market: dict[str, dict[str, dict[str, Any]]] = {}
    for row in bars:
        date = _date_text(row.get("date"), "bar.date")
        asset = _asset_id(row.get("asset_id"))
        close = _decimal(row.get("close"), "bar.close")
        if close <= 0:
            raise QuantContractError(f"bar.close must be positive for {asset} on {date}")
        day = market.setdefault(date, {})
        if asset in day:
            raise QuantContractError(f"duplicate bar for {asset} on {date}")
        day[asset] = {
            "close": close,
            "suspended": bool(row.get("suspended", False)),
            "at_upper_limit": bool(row.get("at_upper_limit", False)),
            "at_lower_limit": bool(row.get("at_lower_limit", False)),
        }
    return market


def _normalize_corporate_actions(actions:Sequence[Mapping[str,Any]])->dict[str,list[dict[str,Any]]]:
    result:dict[str,list[dict[str,Any]]]={};seen=set()
    for index,raw in enumerate(actions):
        if not isinstance(raw,Mapping):raise QuantContractError(f"corporate_actions[{index}] must be an object")
        required={"action_id","asset_id","date","action_type"}
        missing=required-set(raw)
        if missing:raise QuantContractError(f"corporate_actions[{index}] missing {sorted(missing)}")
        action_id=str(raw["action_id"]).strip();asset=_asset_id(raw["asset_id"]);day=_date_text(raw["date"],"corporate action date");kind=str(raw["action_type"])
        if not action_id or action_id in seen:raise QuantContractError("corporate action IDs must be non-empty and unique")
        seen.add(action_id);item={"action_id":action_id,"asset_id":asset,"date":day,"action_type":kind}
        if kind=="cash_dividend":
            item["cash_per_share"]=_decimal(raw.get("cash_per_share"),"cash_per_share",nonnegative=True)
        elif kind=="split":
            ratio=_decimal(raw.get("split_ratio"),"split_ratio",nonnegative=True)
            if ratio<=0:raise QuantContractError("split_ratio must be positive")
            item["split_ratio"]=ratio
        elif kind=="delist":
            item["cash_price"]=_decimal(raw.get("cash_price","0"),"delist cash_price",nonnegative=True)
        else:raise QuantContractError(f"unsupported corporate action type: {kind}")
        result.setdefault(day,[]).append(item)
    order={"cash_dividend":0,"split":1,"delist":2}
    for day in result:result[day].sort(key=lambda item:(item["asset_id"],order[item["action_type"]],item["action_id"]))
    return result


def _normalize_targets(
    targets: Sequence[Mapping[str, Any]],
) -> dict[str, list[dict[str, Any]]]:
    normalized: dict[str, list[dict[str, Any]]] = {}
    seen_hashes: set[str] = set()
    for sequence, raw in enumerate(targets):
        target = _json_value(raw)
        if not isinstance(target, dict) or target.get("schema") != TARGET_WEIGHTS_SCHEMA:
            raise QuantContractError("target portfolio uses an unsupported schema")
        if not verify_artifact_hash(target):
            raise QuantContractError("target portfolio artifact_hash is invalid")
        if target["artifact_hash"] in seen_hashes:
            raise QuantContractError("duplicate target portfolio artifact")
        seen_hashes.add(target["artifact_hash"])
        date = _date_text(target.get("effective_on"), "target.effective_on")
        weights: dict[str, Decimal] = {}
        for row in target.get("weights", []):
            asset = _asset_id(row.get("asset_id"))
            if asset in weights:
                raise QuantContractError(f"duplicate target weight for {asset}")
            weight = _decimal(row.get("weight"), "target.weight", nonnegative=True)
            weights[asset] = weight
        cash = _decimal(target.get("cash_weight"), "target.cash_weight", nonnegative=True)
        if sum(weights.values(), cash) != Decimal("1"):
            raise QuantContractError("target weights and cash_weight must sum exactly to one")
        target["_input_sequence"] = sequence
        normalized.setdefault(date, []).append(target)
    for date in normalized:
        normalized[date].sort(key=lambda item: (int(item.get("sequence", item["_input_sequence"])), item["artifact_hash"]))
        for target in normalized[date]:
            target.pop("_input_sequence", None)
    return normalized


def _initial_lots(
    initial_positions: Mapping[str, Any],
    initial_lots: Sequence[Mapping[str, Any]],
    first_date: str,
) -> dict[str, list[_Lot]]:
    result: dict[str, list[_Lot]] = {}
    for asset_value, quantity_value in initial_positions.items():
        asset = _asset_id(asset_value)
        quantity = _positive_int(quantity_value, f"initial_positions.{asset}")
        result.setdefault(asset, []).append(_Lot(quantity, "0001-01-01"))
    for row in initial_lots:
        asset = _asset_id(row.get("asset_id"))
        quantity = _positive_int(row.get("quantity"), "initial_lot.quantity")
        acquired_on = _date_text(row.get("acquired_on"), "initial_lot.acquired_on")
        if acquired_on > first_date:
            raise QuantContractError("initial lot cannot be acquired after simulation starts")
        result.setdefault(asset, []).append(_Lot(quantity, acquired_on))
    for asset in result:
        result[asset].sort(key=lambda lot: lot.acquired_on)
    return result


def _initial_position_contract(
    initial_positions: Mapping[str, Any], initial_lots: Sequence[Mapping[str, Any]]
) -> dict[str, Any]:
    return {
        "sellable_positions": [
            {"asset_id": _asset_id(asset), "quantity": _positive_int(quantity, "initial_position.quantity")}
            for asset, quantity in sorted(initial_positions.items())
        ],
        "dated_lots": sorted(
            [
                {
                    "asset_id": _asset_id(row.get("asset_id")),
                    "quantity": _positive_int(row.get("quantity"), "initial_lot.quantity"),
                    "acquired_on": _date_text(row.get("acquired_on"), "initial_lot.acquired_on"),
                }
                for row in initial_lots
            ],
            key=lambda row: (row["asset_id"], row["acquired_on"], row["quantity"]),
        ),
    }


def _position_quantities(lots: Mapping[str, Sequence[_Lot]]) -> dict[str, int]:
    return {asset: sum(lot.quantity for lot in asset_lots) for asset, asset_lots in lots.items()}


def _desired_positions(
    *,
    target: Mapping[str, Any],
    cash: Decimal,
    positions: Mapping[str, int],
    prices: Mapping[str, Decimal],
    day_bars: Mapping[str, Mapping[str, Any]],
    lot_size: int,
    money_quantum: Decimal,
) -> tuple[dict[str, int], list[str]]:
    warnings: list[str] = []
    nav = cash
    for asset, quantity in positions.items():
        price = prices.get(asset)
        if price is None:
            raise QuantContractError(f"cannot value held asset {asset}: no price at or before target date")
        nav += price * quantity
    nav = _money(nav, money_quantum)
    desired: dict[str, int] = {}
    for row in target.get("weights", []):
        asset = row["asset_id"]
        bar = day_bars.get(asset)
        if bar is None:
            desired[asset] = positions.get(asset, 0)
            warnings.append(f"missing_execution_bar:{asset}")
            continue
        weight = _decimal(row["weight"], "target.weight")
        with localcontext() as context:
            context.prec = 50
            lots = (nav * weight / bar["close"] / lot_size).to_integral_value(rounding=ROUND_DOWN)
        desired[asset] = int(lots) * lot_size
    return desired, warnings


def _trade_block_reason(side: str, bar: Mapping[str, Any] | None) -> str | None:
    if bar is None:
        return "missing_bar"
    if bar["suspended"]:
        return "suspended"
    if side == "buy" and bar["at_upper_limit"]:
        return "upper_limit_buy_blocked"
    if side == "sell" and bar["at_lower_limit"]:
        return "lower_limit_sell_blocked"
    return None


def _fill_cost(side: str, quantity: int, quote: Decimal, spec: RealitySpec) -> dict[str, Decimal]:
    quantum = _decimal(spec.money_quantum, "money_quantum")
    price_tick = _decimal(spec.price_tick, "price_tick")
    slip = _decimal(spec.slippage_bps, "slippage_bps") / Decimal("10000")
    multiplier = Decimal("1") + slip if side == "buy" else Decimal("1") - slip
    execution_price = _money(quote * multiplier, price_tick)
    notional = _money(execution_price * quantity, quantum)
    commission = _money(
        max(_decimal(spec.minimum_commission, "minimum_commission"), notional * _decimal(spec.commission_rate, "commission_rate")),
        quantum,
    )
    stamp = (
        _money(notional * _decimal(spec.sell_stamp_duty_rate, "sell_stamp_duty_rate"), quantum)
        if side == "sell"
        else Decimal("0")
    )
    return {
        "execution_price": execution_price,
        "notional": notional,
        "commission": commission,
        "stamp_duty": stamp,
        "total_fees": commission + stamp,
    }


def _make_fill(
    date: str,
    target_hash: str,
    asset: str,
    side: str,
    quantity: int,
    quote: Decimal,
    spec: RealitySpec,
) -> tuple[dict[str, Any], Decimal]:
    values = _fill_cost(side, quantity, quote, spec)
    cash_delta = (
        -(values["notional"] + values["total_fees"])
        if side == "buy"
        else values["notional"] - values["total_fees"]
    )
    fill = {
        "date": date,
        "target_hash": target_hash,
        "asset_id": asset,
        "side": side,
        "quantity": quantity,
        "quote_price": _decimal_text(quote),
        "execution_price": _decimal_text(values["execution_price"]),
        "notional": _decimal_text(values["notional"]),
        "commission": _decimal_text(values["commission"]),
        "stamp_duty": _decimal_text(values["stamp_duty"]),
        "total_fees": _decimal_text(values["total_fees"]),
        "simulated": True,
    }
    return fill, cash_delta


def _affordable_quantity(requested: int, cash: Decimal, quote: Decimal, spec: RealitySpec) -> int:
    lot = spec.lot_size
    maximum_lots = requested // lot
    low, high = 0, maximum_lots
    while low < high:
        middle = (low + high + 1) // 2
        values = _fill_cost("buy", middle * lot, quote, spec)
        if values["notional"] + values["total_fees"] <= cash:
            low = middle
        else:
            high = middle - 1
    return low * lot


def _consume_lots(lots: dict[str, list[_Lot]], asset: str, quantity: int, date: str) -> None:
    remaining = quantity
    for lot in lots.get(asset, []):
        if lot.acquired_on >= date or lot.quantity <= 0:
            continue
        consumed = min(lot.quantity, remaining)
        lot.quantity -= consumed
        remaining -= consumed
        if remaining == 0:
            break
    if remaining:
        raise QuantContractError("internal lot accounting mismatch")
    lots[asset] = [lot for lot in lots.get(asset, []) if lot.quantity > 0]


def _unfilled(
    date: str,
    target_hash: str,
    asset: str,
    side: str,
    requested: int,
    filled: int,
    reason: str,
) -> dict[str, Any]:
    return {
        "date": date,
        "target_hash": target_hash,
        "asset_id": asset,
        "side": side,
        "requested_quantity": requested,
        "filled_quantity": filled,
        "remaining_quantity": requested - filled,
        "reason": reason,
    }


def _mark_positions(
    positions: Mapping[str, int],
    last_prices: Mapping[str, Decimal],
    day_bars: Mapping[str, Mapping[str, Any]],
    spec: RealitySpec,
) -> tuple[list[dict[str, Any]], Decimal, list[str]]:
    quantum = _decimal(spec.money_quantum, "money_quantum")
    holdings: list[dict[str, Any]] = []
    total = Decimal("0")
    warnings: list[str] = []
    for asset in sorted(positions):
        quantity = positions[asset]
        if quantity <= 0:
            continue
        price = last_prices.get(asset)
        if price is None:
            raise QuantContractError(f"cannot value held asset {asset}: no market price")
        if asset not in day_bars:
            warnings.append(f"stale_valuation:{asset}")
        value = _money(price * quantity, quantum)
        total += value
        holdings.append(
            {
                "asset_id": asset,
                "quantity": quantity,
                "mark_price": _decimal_text(price),
                "market_value": _decimal_text(value),
            }
        )
    return holdings, _money(total, quantum), warnings


__all__ = [
    "EXPERIMENT_BUNDLE_SCHEMA",
    "RESEARCH_EXPERIMENT_BUNDLE_SCHEMA",
    "EXPERIMENT_RESULT_SCHEMA",
    "HEALTH_SCHEMA",
    "NativeQuantRuntime",
    "QuantContractError",
    "QuantRuntime",
    "REFERENCE_SCHEMA",
    "RUNTIME_VERSION",
    "RealitySpec",
    "ReferenceCalculator",
    "SIMULATION_SCHEMA",
    "TARGET_WEIGHTS_SCHEMA",
    "canonical_hash",
    "canonical_json",
    "verify_artifact_hash",
]
