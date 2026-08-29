from __future__ import annotations

from decimal import Decimal
from typing import Any

from .db import rows_dict
from .financial import dec, dtext
from .foundation import CompanionError
from .timeutil import parse


class PerformanceEngine:
    """Objective account performance measurement, separate from review or learning."""

    EXTERNAL_FLOW_TYPES = {"cash_deposit", "cash_withdrawal"}
    PERIOD_BASIS = "start_exclusive_end_inclusive"

    def __init__(self, companion):
        self.c = companion

    def calculate_period(
        self,
        *,
        account_id: str,
        period_start: str,
        period_end: str,
        start_prices: dict[str, Any],
        end_prices: dict[str, Any],
        benchmark_start_value: Any | None = None,
        benchmark_end_value: Any | None = None,
        benchmark_mode: str | None = None,
        benchmark_source_ref: str | None = None,
        benchmark_unavailable_reason: str | None = None,
        valuation_points: list[dict[str, Any]] | None = None,
        period_basis: str = PERIOD_BASIS,
        price_source_refs: list[str] | None = None,
        trade_reference_prices: dict[str, dict[str, Any]] | None = None,
        source_refs: list[str] | None = None,
        attribution_refs: list[str] | None = None,
    ) -> dict[str, Any]:
        if period_basis != self.PERIOD_BASIS:
            raise CompanionError(
                "performance period_basis must be start_exclusive_end_inclusive"
            )
        start, end = parse(period_start), parse(period_end)
        if end <= start:
            raise CompanionError("performance period_end must be after period_start")
        account = self.c.financial.account_get(account_id)
        base_currency = account["base_currency"]
        start_state = self.c.financial.portfolio_state(period_start, account_id, start_prices)
        end_state = self.c.financial.portfolio_state(period_end, account_id, end_prices)
        if start_state["warnings"] or end_state["warnings"]:
            raise CompanionError(
                "investment_performance.valuation_required: complete start and end "
                "valuations are required"
            )
        start_value = self._single_currency_value(start_state, base_currency, "start")
        end_value = self._single_currency_value(end_state, base_currency, "end")

        with self.c.db.connect() as con:
            entries = rows_dict(
                con.execute(
                    "SELECT * FROM ledger_entries WHERE account_id=? "
                    "AND status IN ('confirmed','reversed') AND occurred_at>? AND occurred_at<=? "
                    "ORDER BY occurred_at,id",
                    (account_id, period_start, period_end),
                ).fetchall()
            )
        foreign_entries = [item for item in entries if item["currency"] != base_currency]
        if foreign_entries:
            raise CompanionError(
                "performance v1 requires all period cash flows in account base currency"
            )

        external_flows = []
        duration = Decimal(str((end - start).total_seconds()))
        for item in entries:
            if item["entry_type"] not in self.EXTERNAL_FLOW_TYPES:
                continue
            amount = dec(item["amount_text"], "external cash flow") - dec(
                item["fee_text"], "external cash-flow fee"
            )
            elapsed = Decimal(str((parse(item["occurred_at"]) - start).total_seconds()))
            weight = (duration - elapsed) / duration
            external_flows.append(
                {
                    "ledger_entry_id": item["id"],
                    "occurred_at": item["occurred_at"],
                    "amount": dtext(amount),
                    "weight": dtext(weight),
                    "weighted_amount": dtext(amount * weight),
                }
            )
        net_external_flow = sum(
            (dec(item["amount"]) for item in external_flows), Decimal("0")
        )
        weighted_external_flow = sum(
            (dec(item["weighted_amount"]) for item in external_flows), Decimal("0")
        )
        investment_gain = end_value - start_value - net_external_flow
        dietz_denominator = start_value + weighted_external_flow
        modified_dietz_return = None
        if dietz_denominator > 0:
            modified_dietz_return = investment_gain / dietz_denominator

        transaction_cost = Decimal("0")
        for item in entries:
            transaction_cost += abs(dec(item["fee_text"], "ledger fee"))
            if item["entry_type"] in {"fee", "tax"}:
                transaction_cost += abs(dec(item["amount_text"], "explicit cost"))

        reference_prices = trade_reference_prices or {}
        if not isinstance(reference_prices, dict):
            raise CompanionError("performance trade_reference_prices must be an object")
        period_trades = {
            item["id"]: item for item in entries if item["entry_type"] == "trade"
        }
        unknown_trade_refs = set(reference_prices) - set(period_trades)
        if unknown_trade_refs:
            raise CompanionError(
                "performance trade reference is not a confirmed period trade: "
                f"{sorted(unknown_trade_refs)}"
            )
        slippage_cost = Decimal("0")
        slippage_sources: list[str] = []
        for entry_id, reference in reference_prices.items():
            if not isinstance(reference, dict) or set(reference) != {
                "price",
                "source_ref",
            }:
                raise CompanionError(
                    "each performance trade reference must contain exactly price and source_ref"
                )
            reference_price = dec(reference["price"], "trade reference price")
            if reference_price <= 0:
                raise CompanionError("performance trade reference price must be positive")
            source_ref = reference["source_ref"]
            if not isinstance(source_ref, str) or not source_ref.strip():
                raise CompanionError(
                    "performance trade reference source_ref must be non-empty"
                )
            trade = period_trades[entry_id]
            quantity = dec(trade["quantity_text"], "trade quantity")
            fill_price = dec(trade["price_text"], "trade price")
            direction = Decimal("1") if quantity > 0 else Decimal("-1")
            slippage_cost += abs(quantity) * (fill_price - reference_price) * direction
            slippage_sources.append(source_ref)

        benchmark_return = None
        explicit_benchmark_mode = benchmark_mode is not None
        inferred_benchmark_mode = (
            "compare"
            if benchmark_start_value is not None or benchmark_end_value is not None
            else "unavailable"
        )
        benchmark_mode = benchmark_mode or inferred_benchmark_mode
        if benchmark_mode not in {"compare", "unavailable"}:
            raise CompanionError("performance benchmark_mode must be compare or unavailable")
        if (benchmark_start_value is None) != (benchmark_end_value is None):
            raise CompanionError(
                "investment_performance.benchmark_required: benchmark start and end "
                "values must be supplied together"
            )
        if benchmark_mode == "compare":
            if benchmark_start_value is None or benchmark_end_value is None:
                raise CompanionError(
                    "investment_performance.benchmark_required: compare mode requires "
                    "benchmark start and end values"
                )
            if (
                not isinstance(benchmark_source_ref, str)
                or not benchmark_source_ref.strip()
            ):
                if explicit_benchmark_mode and source_refs is None:
                    raise CompanionError(
                        "investment_performance.benchmark_required: compare mode requires benchmark_source_ref"
                    )
                benchmark_source_ref = (
                    source_refs[-1]
                    if source_refs
                    else "benchmark_source_not_supplied_by_legacy_caller"
                )
            if not benchmark_source_ref:
                raise CompanionError(
                    "investment_performance.benchmark_required: compare mode requires benchmark_source_ref"
                )
            benchmark_start = dec(benchmark_start_value, "benchmark start value")
            benchmark_end = dec(benchmark_end_value, "benchmark end value")
            if benchmark_start <= 0 or benchmark_end < 0:
                raise CompanionError(
                    "benchmark values must have a positive start and non-negative end"
                )
            benchmark_return = benchmark_end / benchmark_start - Decimal("1")
            if benchmark_unavailable_reason is not None:
                raise CompanionError(
                    "performance compare mode cannot include benchmark_unavailable_reason"
                )
        else:
            if benchmark_start_value is not None or benchmark_end_value is not None:
                raise CompanionError(
                    "performance unavailable benchmark mode cannot include benchmark values"
                )
            if (
                not isinstance(benchmark_unavailable_reason, str)
                or not benchmark_unavailable_reason.strip()
            ):
                if explicit_benchmark_mode:
                    raise CompanionError(
                        "investment_performance.benchmark_required: unavailable mode requires a reason"
                    )
                benchmark_unavailable_reason = "benchmark_not_supplied_by_legacy_caller"
            if benchmark_source_ref is not None:
                raise CompanionError(
                    "performance unavailable benchmark mode cannot include benchmark_source_ref"
                )
        excess_return = (
            modified_dietz_return - benchmark_return
            if modified_dietz_return is not None and benchmark_return is not None
            else None
        )

        drawdown_points = self._valuation_points(
            start,
            end,
            start_value,
            end_value,
            valuation_points or [],
        )
        peak = Decimal("0")
        maximum_drawdown = Decimal("0")
        for item in drawdown_points:
            value = dec(item["value"], "valuation point")
            peak = max(peak, value)
            if peak > 0:
                maximum_drawdown = max(maximum_drawdown, (peak - value) / peak)

        price_refs = self._unique_refs(
            price_source_refs if price_source_refs is not None else source_refs or [],
            "price_source_refs",
        )
        refs = list(dict.fromkeys([
            *price_refs,
            *([benchmark_source_ref] if benchmark_source_ref else []),
            *slippage_sources,
        ]))
        attribution = self._unique_refs(attribution_refs or [], "attribution_refs")
        warnings = []
        if len(drawdown_points) == 2:
            warnings.append("maximum_drawdown_uses_endpoints_only")
        if benchmark_return is None:
            warnings.append("benchmark_return_missing")
        if len(reference_prices) < len(period_trades):
            warnings.append("slippage_reference_missing")
        if not attribution:
            warnings.append("decision_and_strategy_attribution_not_supplied")

        outputs = {
            "period": {"start": period_start, "end": period_end},
            "time_basis": {
                "period_boundary": self.PERIOD_BASIS,
                "valuation": "point_in_time",
            },
            "account_id": account_id,
            "currency": base_currency,
            "start_value": dtext(start_value),
            "end_value": dtext(end_value),
            "net_external_flow": dtext(net_external_flow),
            "weighted_external_flow": dtext(weighted_external_flow),
            "investment_gain_after_external_flows": dtext(investment_gain),
            "modified_dietz_return": (
                dtext(modified_dietz_return) if modified_dietz_return is not None else None
            ),
            "benchmark_return": dtext(benchmark_return) if benchmark_return is not None else None,
            "excess_return": dtext(excess_return) if excess_return is not None else None,
            "transaction_cost": dtext(transaction_cost),
            "slippage_cost": dtext(slippage_cost),
            "total_cost": dtext(transaction_cost + slippage_cost),
            "maximum_drawdown": dtext(maximum_drawdown),
            "external_flows": external_flows,
            "valuation_points": drawdown_points,
            "attribution_refs": attribution,
            "attribution": {
                "reference_ids": attribution,
                "status": "linked" if attribution else "insufficient_evidence",
            },
            "benchmark": {
                "mode": benchmark_mode,
                "return": dtext(benchmark_return) if benchmark_return is not None else None,
                "source_ref": benchmark_source_ref,
                "unavailable_reason": benchmark_unavailable_reason,
            },
            "costs": {
                "fees_and_taxes": dtext(transaction_cost),
                "slippage": dtext(slippage_cost),
                "total": dtext(transaction_cost + slippage_cost),
                "slippage_trade_count": len(reference_prices),
                "period_trade_count": len(period_trades),
            },
            "warnings": warnings,
            "caveats": warnings,
        }
        calculation = self.c.financial.calculation_record(
            "account_performance",
            "Cash-flow-adjusted account performance and benchmark comparison",
            period_end,
            {
                "account_id": account_id,
                "period_start": period_start,
                "period_end": period_end,
                "start_prices": start_prices,
                "end_prices": end_prices,
                "ledger_entry_ids": [item["id"] for item in entries],
                "source_refs": refs,
                "attribution_refs": attribution,
                "trade_reference_prices": reference_prices,
            },
            {
                "return_method": "modified_dietz",
                "external_flow_types": sorted(self.EXTERNAL_FLOW_TYPES),
                "period_basis": self.PERIOD_BASIS,
                "benchmark_start_value": benchmark_start_value,
                "benchmark_end_value": benchmark_end_value,
                "benchmark_mode": benchmark_mode,
                "benchmark_source_ref": benchmark_source_ref,
                "benchmark_unavailable_reason": benchmark_unavailable_reason,
            },
            {
                "investment_gain": "end_value - start_value - net_external_flow",
                "modified_dietz": "investment_gain / (start_value + weighted_external_flow)",
                "maximum_drawdown": "max((prior_peak - value) / prior_peak)",
            },
            outputs,
            warnings,
        )
        return {**outputs, "calculation_id": calculation["id"]}

    @staticmethod
    def _single_currency_value(state: dict[str, Any], currency: str, label: str) -> Decimal:
        nonzero = {
            key: dec(value)
            for key, value in state["total_by_currency"].items()
            if dec(value) != 0
        }
        if set(nonzero) - {currency}:
            raise CompanionError(f"performance {label} valuation contains another currency")
        return dec(state["total_by_currency"].get(currency, "0"), f"{label} value")

    @staticmethod
    def _unique_refs(values: list[str], field: str) -> list[str]:
        if any(not isinstance(item, str) or not item.strip() for item in values):
            raise CompanionError(f"performance {field} must contain non-empty strings")
        if len(values) != len(set(values)):
            raise CompanionError(f"performance {field} must be unique")
        return list(values)

    @staticmethod
    def _valuation_points(
        start,
        end,
        start_value: Decimal,
        end_value: Decimal,
        supplied: list[dict[str, Any]],
    ) -> list[dict[str, str]]:
        points = [{"at": start.isoformat().replace("+00:00", "Z"), "value": dtext(start_value)}]
        previous = start
        for item in supplied:
            if not isinstance(item, dict) or set(item) != {"at", "value"}:
                raise CompanionError("each valuation point must contain exactly at and value")
            at = parse(item["at"])
            value = dec(item["value"], "valuation point")
            if not start < at < end or at <= previous:
                raise CompanionError("valuation points must advance strictly inside the period")
            if value < 0:
                raise CompanionError("valuation point cannot be negative")
            points.append({"at": item["at"], "value": dtext(value)})
            previous = at
        points.append({"at": end.isoformat().replace("+00:00", "Z"), "value": dtext(end_value)})
        return points
