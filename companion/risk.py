from __future__ import annotations

from decimal import Decimal
from typing import Any

from .db import row_dict
from .financial import dec, dtext, mandate_constraints
from .foundation import CompanionError
from .market_calendar import BROKER_VALIDITY_MAX_CALENDAR_DAYS
from .quant_runtime import RealitySpec
from .timeutil import parse


class RiskGate:
    """Deterministic pre-action veto over the current authoritative portfolio."""

    def __init__(self, companion):
        self.c = companion

    def assess_trade(
        self,
        *,
        as_of: str,
        account_id: str,
        asset_id: str,
        quantity: Any,
        price: Any,
        fee: Any = "0",
        mandate: dict[str, Any] | None = None,
        reality_spec: dict[str, Any] | None = None,
        market_snapshot_id: str | None = None,
        max_market_age_seconds: int | None = None,
        valid_until: str | None = None,
        price_range: dict[str, Any] | None = None,
        average_daily_amount: Any | None = None,
        action_tier: str = "standard",
        bounded_action_policy: dict[str, Any] | None = None,
        program_revision_id: str | None = None,
        validity_sessions: int | None = None,
    ) -> dict[str, Any]:
        now = parse(as_of)
        qty, px, fee_value = dec(quantity, "risk quantity"), dec(price, "risk price"), dec(fee, "risk fee")
        if qty == 0:
            raise CompanionError("risk quantity must be non-zero")
        if px <= 0 or fee_value < 0:
            raise CompanionError("risk price must be positive and fee non-negative")
        if action_tier not in {"standard", "bounded"}:
            raise CompanionError("action_tier must be standard or bounded")
        if max_market_age_seconds is not None and (
            isinstance(max_market_age_seconds, bool)
            or not isinstance(max_market_age_seconds, int)
            or max_market_age_seconds <= 0
        ):
            raise CompanionError("max_market_age_seconds must be a positive integer")

        impact = self.c.financial.trade_impact(
            as_of,
            account_id,
            asset_id,
            dtext(qty),
            dtext(px),
            dtext(fee_value),
            mandate,
        )
        asset = self.c.financial.asset_get(asset_id)
        account = self.c.financial.account_get(account_id)
        constraints = mandate_constraints(mandate)
        violations = [dict(item) for item in impact["violations"]]

        def add(rule: str, **details: Any) -> None:
            if not any(item.get("rule") == rule for item in violations):
                violations.append({"rule": rule, **details})

        prohibited = set(
            constraints.get("prohibited_asset_ids", constraints.get("forbidden_asset_ids", []))
        )
        allowed = set(constraints.get("allowed_asset_ids", []))
        if asset_id in prohibited:
            add("prohibited_asset", asset_id=asset_id)
        if allowed and asset_id not in allowed:
            add("asset_not_allowed", asset_id=asset_id)

        if asset["currency"] != account["base_currency"]:
            add(
                "base_currency_mismatch",
                account_currency=account["base_currency"],
                asset_currency=asset["currency"],
            )

        if reality_spec is not None:
            reality = RealitySpec.from_value(reality_spec)
            if reality.currency != asset["currency"]:
                add(
                    "reality_currency_mismatch",
                    reality_currency=reality.currency,
                    asset_currency=asset["currency"],
                )
            if qty != qty.to_integral_value():
                add("whole_quantity_required", quantity=dtext(qty))
            if qty > 0 and int(qty) % reality.lot_size:
                add("buy_lot_size", quantity=dtext(qty), lot_size=reality.lot_size)
            tick = dec(reality.price_tick, "price tick")
            if px % tick:
                add("price_tick", price=dtext(px), price_tick=dtext(tick))

        if valid_until is not None and now >= parse(valid_until):
            add("action_expired", valid_until=valid_until)

        if price_range is not None:
            if not isinstance(price_range, dict) or set(price_range) != {"min", "max"}:
                raise CompanionError("price_range must contain exactly min and max")
            low, high = dec(price_range["min"], "minimum price"), dec(
                price_range["max"], "maximum price"
            )
            if low <= 0 or low > high:
                raise CompanionError("price_range is invalid")
            if not low <= px <= high:
                add("price_out_of_range", price=dtext(px), minimum=dtext(low), maximum=dtext(high))

        if valid_until is None or price_range is None or max_market_age_seconds is None:
            raise CompanionError(
                "risk assessment requires candidate price_range, market freshness and validity"
            )
        qualification = self.c.portfolio_qualification.evaluate_candidate(
            account_id=account_id,
            asset_id=asset_id,
            quantity=dtext(qty),
            price=dtext(px),
            price_range=price_range,
            market_snapshot_id=market_snapshot_id,
            max_market_age_seconds=max_market_age_seconds,
            as_of=as_of,
            valid_until=valid_until,
        )

        market = None
        if market_snapshot_id is not None:
            with self.c.db.connect() as con:
                market = row_dict(
                    con.execute(
                        "SELECT * FROM market_snapshots WHERE id=?", (market_snapshot_id,)
                    ).fetchone()
                )
            if not market:
                add("market_snapshot_missing", market_snapshot_id=market_snapshot_id)
            else:
                if market["asset_id"] != asset_id or market["metric"] != "close":
                    add("market_snapshot_mismatch", market_snapshot_id=market_snapshot_id)
                if market["quality"] != "healthy":
                    add("market_snapshot_unhealthy", quality=market["quality"])
                if dec(market["value_text"], "market snapshot price") != px:
                    add("market_price_mismatch", market_snapshot_id=market_snapshot_id)
                if parse(market["observed_at"]) > now:
                    add("market_snapshot_from_future", observed_at=market["observed_at"])
                elif max_market_age_seconds is not None:
                    age = (now - parse(market["observed_at"])).total_seconds()
                    if age > max_market_age_seconds:
                        add(
                            "market_snapshot_stale",
                            age_seconds=str(int(age)),
                            maximum_seconds=max_market_age_seconds,
                        )
        elif max_market_age_seconds is not None:
            add("market_snapshot_missing")

        before = impact["before"]
        if before.get("warnings"):
            add("portfolio_state_incomplete", warnings=before["warnings"])
        total = dec(before["total_by_currency"].get(asset["currency"], "0"), "portfolio value")
        nav_after_fee = total - fee_value
        after_quantity = dec(
            impact["after"]["positions"].get(asset_id, "0"), "post-trade quantity"
        )
        after_value = after_quantity * px
        position_weight = None
        turnover = None
        if nav_after_fee <= 0:
            add("nonpositive_portfolio_value", value=dtext(nav_after_fee))
        else:
            position_weight = after_value / nav_after_fee
            maximum_weight = constraints.get(
                "max_single_position_weight", constraints.get("max_position_weight")
            )
            if maximum_weight is not None and position_weight > dec(
                maximum_weight, "maximum position weight"
            ):
                add(
                    "max_single_position_weight",
                    actual=dtext(position_weight),
                    maximum=dtext(dec(maximum_weight)),
                )
            turnover = abs(qty * px) / nav_after_fee
            maximum_turnover = constraints.get("max_turnover")
            if maximum_turnover is not None and turnover > dec(
                maximum_turnover, "maximum turnover"
            ):
                add(
                    "max_turnover",
                    actual=dtext(turnover),
                    maximum=dtext(dec(maximum_turnover)),
                )

        participation = None
        maximum_participation = constraints.get("max_participation_rate")
        if maximum_participation is not None:
            if average_daily_amount is None:
                add("liquidity_data_missing", required="average_daily_amount")
            else:
                daily_amount = dec(average_daily_amount, "average daily amount")
                if daily_amount <= 0:
                    add("liquidity_data_invalid", average_daily_amount=dtext(daily_amount))
                else:
                    participation = abs(qty * px) / daily_amount
                    if participation > dec(maximum_participation, "maximum participation rate"):
                        add(
                            "max_participation_rate",
                            actual=dtext(participation),
                            maximum=dtext(dec(maximum_participation)),
                        )

        if action_tier == "bounded":
            required_policy_fields = {
                "enabled",
                "allowed_asset_types",
                "allowed_execution_plan_types",
                "max_trade_weight",
                "max_post_trade_weight",
                "max_validity_sessions",
                "max_active_bounded_actions",
            }
            policy = bounded_action_policy
            if not isinstance(policy, dict) or set(policy) != required_policy_fields:
                add("bounded_action_policy_missing_or_invalid")
            else:
                allowed_types = policy.get("allowed_asset_types")
                if policy.get("enabled") is not True:
                    add("bounded_action_disabled")
                if (
                    not isinstance(allowed_types, list)
                    or not allowed_types
                    or any(not isinstance(item, str) or not item for item in allowed_types)
                ):
                    add("bounded_allowed_asset_types_invalid")
                elif asset["asset_type"] not in set(allowed_types):
                    add("bounded_asset_type_not_allowed", asset_type=asset["asset_type"])
                allowed_plan_types = policy.get("allowed_execution_plan_types")
                if (
                    not isinstance(allowed_plan_types, list)
                    or not allowed_plan_types
                    or any(
                        item not in {"priced_buy", "priced_sell", "bracket_exit"}
                        for item in allowed_plan_types
                    )
                ):
                    add("bounded_allowed_execution_plan_types_invalid")
                try:
                    max_trade_weight = dec(policy.get("max_trade_weight"), "bounded maximum trade weight")
                    max_post_trade_weight = dec(
                        policy.get("max_post_trade_weight"), "bounded maximum post-trade weight"
                    )
                except CompanionError:
                    add("bounded_weight_limits_invalid")
                else:
                    if not Decimal("0") < max_trade_weight <= Decimal("1"):
                        add("bounded_max_trade_weight_invalid")
                    if not Decimal("0") < max_post_trade_weight <= Decimal("1"):
                        add("bounded_max_post_trade_weight_invalid")
                    if turnover is not None and turnover > max_trade_weight:
                        add(
                            "bounded_max_trade_weight",
                            actual=dtext(turnover),
                            maximum=dtext(max_trade_weight),
                        )
                    if qty > 0 and position_weight is not None and position_weight > max_post_trade_weight:
                        add(
                            "bounded_max_post_trade_weight",
                            actual=dtext(position_weight),
                            maximum=dtext(max_post_trade_weight),
                        )
                max_sessions = policy.get("max_validity_sessions")
                if (
                    isinstance(max_sessions, bool)
                    or not isinstance(max_sessions, int)
                    or max_sessions not in {5, 20, 60, 180}
                ):
                    add("bounded_max_validity_sessions_invalid")
                if validity_sessions not in {5, 20, 60, 180}:
                    add("bounded_validity_sessions_missing_or_invalid")
                elif isinstance(max_sessions, int) and validity_sessions > max_sessions:
                    add(
                        "bounded_validity_sessions_too_long",
                        actual_sessions=validity_sessions,
                        maximum_sessions=max_sessions,
                    )
                if (
                    validity_sessions in BROKER_VALIDITY_MAX_CALENDAR_DAYS
                    and valid_until is not None
                    and (parse(valid_until) - now).total_seconds()
                    > BROKER_VALIDITY_MAX_CALENDAR_DAYS[validity_sessions] * 86400
                ):
                    add(
                        "bounded_deadline_exceeds_session_envelope",
                        validity_sessions=validity_sessions,
                        maximum_calendar_days=BROKER_VALIDITY_MAX_CALENDAR_DAYS[
                            validity_sessions
                        ],
                    )
                max_active = policy.get("max_active_bounded_actions")
                if isinstance(max_active, bool) or not isinstance(max_active, int) or max_active <= 0:
                    add("bounded_max_active_actions_invalid")

        outputs = {
            "status": "blocked" if violations else "pass",
            "blocked": bool(violations),
            "violations": violations,
            "trade_impact_calculation_id": impact["calculation_id"],
            "portfolio_calculation_id": before["calculation_id"],
            "market_snapshot_id": market["id"] if market else None,
            "portfolio_qualification": qualification.stable_projection(),
            "precise_action_eligible": bool(
                not violations
                and "precise_decision_support" in qualification.allowed_uses
            ),
            "metrics": {
                "post_trade_position_weight": dtext(position_weight) if position_weight is not None else None,
                "trade_turnover": dtext(turnover) if turnover is not None else None,
                "market_participation_rate": dtext(participation) if participation is not None else None,
            },
        }
        calculation = self.c.financial.calculation_record(
            "risk_gate",
            "Deterministic pre-action portfolio and execution veto",
            as_of,
            {
                "account_id": account_id,
                "asset_id": asset_id,
                "quantity": dtext(qty),
                "price": dtext(px),
                "fee": dtext(fee_value),
                "market_snapshot_id": market_snapshot_id,
                "action_tier": action_tier,
                "program_revision_id": program_revision_id,
                "validity_sessions": validity_sessions,
                "portfolio_qualification_calculation_id": (
                    qualification.calculation_id
                ),
            },
            {
                "mandate": mandate or {},
                "reality_spec": reality_spec or {},
                "max_market_age_seconds": max_market_age_seconds,
                "valid_until": valid_until,
                "price_range": price_range or {},
                "average_daily_amount": (
                    dtext(dec(average_daily_amount)) if average_daily_amount is not None else None
                ),
                "action_tier": action_tier,
                "bounded_action_policy": bounded_action_policy or {},
                "program_revision_id": program_revision_id,
                "validity_sessions": validity_sessions,
                "portfolio_qualification_lineage": (
                    qualification.audit_lineage_projection()
                ),
            },
            {
                "blocked": "any hard-rule violation",
                "position_weight": "post-trade market value / post-fee portfolio value",
                "turnover": "absolute trade value / post-fee portfolio value",
                "participation": "absolute trade value / average daily amount",
            },
            outputs,
            [],
        )
        return {**outputs, "calculation_id": calculation["id"]}
