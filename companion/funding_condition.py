from __future__ import annotations

from decimal import Decimal, ROUND_CEILING, ROUND_FLOOR, ROUND_HALF_UP
from typing import Any

from .financial import dec, dtext, mandate_constraints
from .foundation import CompanionError
from .timeutil import iso, parse


CALCULATION_KIND = "funding_condition"
POLICY_VERSION = "funding-condition-policy/v1"
SCHEMA_VERSION = "investment-companion.funding-condition/v1"
RECALCULATE_ON = (
    "confirmed_ledger_change",
    "portfolio_qualification_change",
    "related_market_snapshot_change",
    "mandate_change",
    "candidate_change",
    "fee_or_tax_assumption_change",
    "expiry",
)
REQUIRED_RERUNS = (
    (
        "rerun_portfolio_qualification",
        "Recalculate candidate Portfolio Qualification from current facts and market evidence.",
    ),
    (
        "rerun_risk_gate",
        "Recalculate the independent Risk Gate from the current confirmed portfolio and Mandate.",
    ),
    (
        "rerun_action_plan",
        "Build a new Action Plan; this Funding Condition never authorizes execution.",
    ),
)


class FundingConditionService:
    """Calculate auditable alternatives for a cash-blocked buy candidate."""

    def __init__(self, companion):
        self.c = companion

    @staticmethod
    def _reruns() -> list[dict[str, str]]:
        return [
            {"code": code, "summary": summary}
            for code, summary in REQUIRED_RERUNS
        ]

    @staticmethod
    def _money(value: Decimal, quantum: Decimal) -> Decimal:
        if value <= 0:
            return Decimal("0")
        return value.quantize(quantum, rounding=ROUND_CEILING)

    @staticmethod
    def _trade_cost(
        *,
        quantity: Decimal,
        price: Decimal,
        commission_rate: Decimal,
        minimum_commission: Decimal,
        tax_rate: Decimal,
        money_quantum: Decimal,
    ) -> dict[str, Decimal]:
        notional = quantity * price
        raw_commission = max(minimum_commission, notional * commission_rate)
        raw_tax = notional * tax_rate
        total_fee = (raw_commission + raw_tax).quantize(
            money_quantum, rounding=ROUND_HALF_UP
        )
        tax = raw_tax.quantize(money_quantum, rounding=ROUND_HALF_UP)
        commission = total_fee - tax
        return {
            "notional": notional,
            "commission": commission,
            "tax": tax,
            "fee": total_fee,
            "total": notional + total_fee,
        }

    def _maximum_supported_quantity(
        self,
        *,
        requested_quantity: Decimal,
        lot_size: Decimal,
        price: Decimal,
        spendable_cash: Decimal,
        commission_rate: Decimal,
        minimum_commission: Decimal,
        tax_rate: Decimal,
        money_quantum: Decimal,
    ) -> Decimal:
        maximum_lots = int(
            (requested_quantity / lot_size).to_integral_value(rounding=ROUND_FLOOR)
        )
        low, high, supported = 0, maximum_lots, 0
        while low <= high:
            middle = (low + high) // 2
            quantity = lot_size * middle
            cost = self._trade_cost(
                quantity=quantity,
                price=price,
                commission_rate=commission_rate,
                minimum_commission=minimum_commission,
                tax_rate=tax_rate,
                money_quantum=money_quantum,
            )["total"]
            if middle == 0 or cost <= spendable_cash:
                supported = middle
                low = middle + 1
            else:
                high = middle - 1
        return lot_size * supported

    def evaluate(
        self,
        *,
        risk: dict[str, Any],
        qualification: Any,
        mandate_revision: dict[str, Any],
    ) -> dict[str, Any] | None:
        """Record a Funding Condition only for a cash-blocked qualified buy."""

        if risk.get("kind") != "risk_gate":
            raise CompanionError("Funding Condition requires a Risk Gate Calculation")
        signed_quantity = dec(risk["inputs"]["quantity"], "Funding Condition quantity")
        if signed_quantity <= 0:
            return None
        if "funding_conditions" not in qualification.allowed_uses:
            return None
        cash_rules = {
            item.get("rule")
            for item in risk["outputs"].get("violations", [])
            if isinstance(item, dict)
        }
        if not cash_rules.intersection({"nonnegative_cash", "minimum_cash"}):
            return None
        frozen_qualification_id = risk["inputs"].get(
            "portfolio_qualification_calculation_id"
        )
        if qualification.calculation_id != frozen_qualification_id:
            raise CompanionError(
                "Funding Condition and Risk use different Portfolio Qualifications"
            )
        if mandate_revision.get("content") != risk["assumptions"].get("mandate"):
            raise CompanionError("Funding Condition and Risk use different Mandates")

        account = self.c.financial.account_get(risk["inputs"]["account_id"])
        asset = self.c.financial.asset_get(risk["inputs"]["asset_id"])
        currency = asset["currency"]
        if currency != account["base_currency"]:
            return None
        confirmed_portfolio_id = risk["outputs"]["portfolio_calculation_id"]
        confirmed_portfolio = self.c.financial.calculation_get(
            confirmed_portfolio_id
        )
        if confirmed_portfolio.get("kind") != "portfolio_state":
            raise CompanionError(
                "Funding Condition requires a confirmed Portfolio Calculation"
            )

        reality = risk["assumptions"].get("reality_spec") or {}
        lot_size = dec(reality.get("lot_size"), "Funding Condition lot size")
        commission_rate = dec(
            reality.get("commission_rate"), "Funding Condition commission rate"
        )
        minimum_commission = dec(
            reality.get("minimum_commission"),
            "Funding Condition minimum commission",
        )
        money_quantum = dec(
            reality.get("money_quantum"), "Funding Condition money quantum"
        )
        if lot_size <= 0 or commission_rate < 0 or minimum_commission < 0:
            raise CompanionError("Funding Condition RealitySpec costs are invalid")
        if money_quantum <= 0:
            raise CompanionError("Funding Condition money quantum must be positive")
        tax_rate = Decimal("0")

        price_range = risk["assumptions"]["price_range"]
        minimum_price = dec(price_range["min"], "Funding Condition minimum price")
        maximum_price = dec(price_range["max"], "Funding Condition maximum price")
        reference_price = dec(
            risk["inputs"]["price"], "Funding Condition reference price"
        )
        quantity = abs(signed_quantity)
        costs = {
            "minimum_price": self._trade_cost(
                quantity=quantity,
                price=minimum_price,
                commission_rate=commission_rate,
                minimum_commission=minimum_commission,
                tax_rate=tax_rate,
                money_quantum=money_quantum,
            ),
            "reference_price": self._trade_cost(
                quantity=quantity,
                price=reference_price,
                commission_rate=commission_rate,
                minimum_commission=minimum_commission,
                tax_rate=tax_rate,
                money_quantum=money_quantum,
            ),
            "maximum_price": self._trade_cost(
                quantity=quantity,
                price=maximum_price,
                commission_rate=commission_rate,
                minimum_commission=minimum_commission,
                tax_rate=tax_rate,
                money_quantum=money_quantum,
            ),
        }
        if costs["reference_price"]["fee"] != dec(
            risk["inputs"]["fee"], "Risk fee"
        ):
            raise CompanionError(
                "Funding Condition fee differs from the frozen Risk calculation"
            )

        mandate = mandate_constraints(mandate_revision["content"])
        safety_buffer = dec(
            (mandate.get("minimum_cash") or {}).get(currency, "0"),
            "Funding Condition cash safety buffer",
        )
        confirmed_cash = dec(
            confirmed_portfolio["outputs"]["cash"].get(currency, "0"),
            "Funding Condition confirmed cash",
        )
        cash_above_buffer = confirmed_cash - safety_buffer
        spendable_cash = max(Decimal("0"), cash_above_buffer)
        shortfalls = {
            key: self._money(
                cost["total"] + safety_buffer - confirmed_cash, money_quantum
            )
            for key, cost in costs.items()
        }
        if shortfalls["reference_price"] <= 0:
            return None

        supported = {
            "minimum_price": self._maximum_supported_quantity(
                requested_quantity=quantity,
                lot_size=lot_size,
                price=minimum_price,
                spendable_cash=spendable_cash,
                commission_rate=commission_rate,
                minimum_commission=minimum_commission,
                tax_rate=tax_rate,
                money_quantum=money_quantum,
            ),
            "reference_price": self._maximum_supported_quantity(
                requested_quantity=quantity,
                lot_size=lot_size,
                price=reference_price,
                spendable_cash=spendable_cash,
                commission_rate=commission_rate,
                minimum_commission=minimum_commission,
                tax_rate=tax_rate,
                money_quantum=money_quantum,
            ),
            "maximum_price": self._maximum_supported_quantity(
                requested_quantity=quantity,
                lot_size=lot_size,
                price=maximum_price,
                spendable_cash=spendable_cash,
                commission_rate=commission_rate,
                minimum_commission=minimum_commission,
                tax_rate=tax_rate,
                money_quantum=money_quantum,
            ),
        }
        guaranteed_quantity = min(supported.values())
        effective_at = iso(parse(risk["as_of"]))
        valid_until = qualification.candidate.valid_until
        validity_status = (
            "current_at_as_of"
            if parse(effective_at) < parse(valid_until)
            else "expired_at_as_of"
        )
        validity = {
            "status": validity_status,
            "valid_until": valid_until,
            "invalidate_on": list(RECALCULATE_ON),
            "supports_current_planning": validity_status == "current_at_as_of",
        }
        reruns = self._reruns()
        common_assumptions = {
            "confirmed_cash_only": True,
            "planned_deposits_counted_as_cash": False,
            "expected_disposal_proceeds_counted_as_cash": False,
            "candidate_price_range_unchanged": True,
            "mandate_and_cost_spec_unchanged": True,
        }
        amount_range = {
            "minimum": dtext(shortfalls["minimum_price"]),
            "at_reference_price": dtext(shortfalls["reference_price"]),
            "maximum": dtext(shortfalls["maximum_price"]),
        }
        paths = [
            {
                "type": "additional_funding",
                "state": "requires_confirmed_ledger_entry",
                "amount_range": amount_range,
                "assumptions": {
                    **common_assumptions,
                    "funding_currency": currency,
                },
                "validity": dict(validity),
                "confirmation_requirements": [
                    {
                        "code": "confirmed_cash_deposit_ledger_entry",
                        "summary": "The deposit must exist as a confirmed Ledger Entry before cash is recalculated.",
                    }
                ],
                "required_reruns": list(reruns),
            },
            {
                "type": "reduce_quantity",
                "state": (
                    "available_as_non_actionable_alternative"
                    if guaranteed_quantity > 0
                    else "unavailable_with_current_cash"
                ),
                "quantity_range": {
                    "minimum": dtext(lot_size) if guaranteed_quantity > 0 else "0",
                    "maximum_across_price_range": dtext(guaranteed_quantity),
                    "maximum_at_minimum_price": dtext(supported["minimum_price"]),
                    "maximum_at_reference_price": dtext(supported["reference_price"]),
                    "maximum_at_maximum_price": dtext(supported["maximum_price"]),
                    "step": dtext(lot_size),
                },
                "assumptions": {
                    **common_assumptions,
                    "quantity_respects_lot_size": True,
                },
                "validity": dict(validity),
                "confirmation_requirements": [
                    {
                        "code": "confirm_reduced_candidate_quantity",
                        "summary": "The user must choose a reduced quantity before recalculation.",
                    }
                ],
                "required_reruns": list(reruns),
            },
            {
                "type": "confirmed_disposal_proceeds",
                "state": "requires_confirmed_ledger_entry",
                "required_net_proceeds_range": amount_range,
                "assumptions": {
                    **common_assumptions,
                    "disposal_proceeds_are_zero_until_confirmed": True,
                },
                "validity": dict(validity),
                "confirmation_requirements": [
                    {
                        "code": "confirmed_disposal_ledger_entry",
                        "summary": "Net disposal proceeds must exist as a confirmed Ledger Entry before reevaluation.",
                    }
                ],
                "required_reruns": list(reruns),
            },
        ]
        output = {
            "schema": SCHEMA_VERSION,
            "policy_version": POLICY_VERSION,
            "account_id": account["id"],
            "asset_id": asset["id"],
            "direction": "buy",
            "currency": currency,
            "as_of": effective_at,
            "candidate_quantity_domain": {
                "kind": "up_to_requested_quantity",
                "minimum": "0",
                "maximum": dtext(quantity),
                "step": dtext(lot_size),
            },
            "price_range": {
                "minimum": dtext(minimum_price),
                "reference": dtext(reference_price),
                "maximum": dtext(maximum_price),
            },
            "confirmed_cash": {
                "amount": dtext(confirmed_cash),
                "cash_safety_buffer": dtext(safety_buffer),
                "cash_above_buffer": dtext(cash_above_buffer),
                "spendable_amount": dtext(spendable_cash),
                "portfolio_calculation_id": confirmed_portfolio_id,
            },
            "cost_range": {
                "notional": {
                    "minimum": dtext(costs["minimum_price"]["notional"]),
                    "at_reference_price": dtext(
                        costs["reference_price"]["notional"]
                    ),
                    "maximum": dtext(costs["maximum_price"]["notional"]),
                },
                "commission": {
                    "minimum": dtext(costs["minimum_price"]["commission"]),
                    "at_reference_price": dtext(
                        costs["reference_price"]["commission"]
                    ),
                    "maximum": dtext(costs["maximum_price"]["commission"]),
                },
                "tax": {
                    "minimum": dtext(costs["minimum_price"]["tax"]),
                    "at_reference_price": dtext(costs["reference_price"]["tax"]),
                    "maximum": dtext(costs["maximum_price"]["tax"]),
                },
                "total": {
                    "minimum": dtext(costs["minimum_price"]["total"]),
                    "at_reference_price": dtext(
                        costs["reference_price"]["total"]
                    ),
                    "maximum": dtext(costs["maximum_price"]["total"]),
                },
            },
            "required_additional_cash": amount_range,
            "paths": paths,
            "validity": validity,
            "candidate_qualification_calculation_id": qualification.calculation_id,
            "risk_calculation_id": risk["id"],
            "automatic_decision_or_execution": False,
        }
        calculation = self.c.financial.calculation_record(
            CALCULATION_KIND,
            "Cash, cost and quantity alternatives for a qualified buy candidate",
            effective_at,
            {
                "account_id": account["id"],
                "asset_id": asset["id"],
                "direction": "buy",
                "currency": currency,
                "candidate_quantity_domain": output["candidate_quantity_domain"],
                "price_range": output["price_range"],
                "market_snapshot_id": qualification.candidate.market_snapshot_id,
                "valid_until": valid_until,
                "risk_calculation_id": risk["id"],
                "portfolio_qualification_calculation_id": (
                    qualification.calculation_id
                ),
                "confirmed_portfolio_calculation_id": confirmed_portfolio_id,
                "mandate_revision_id": mandate_revision["id"],
                "mandate_content_hash": mandate_revision["content_hash"],
            },
            {
                "mandate": mandate_revision["content"],
                "reality_spec": reality,
                "portfolio_qualification_lineage": (
                    qualification.audit_lineage_projection()
                ),
                **common_assumptions,
            },
            {
                "candidate_cost": "quantity * price + rounded commission + buy tax",
                "commission": "max(minimum_commission, notional * commission_rate)",
                "buy_tax": "zero under the frozen RealitySpec; sell taxes never fund a buy",
                "required_additional_cash": (
                    "max(0, candidate_cost + mandate_cash_buffer - confirmed_cash)"
                ),
                "reduced_quantity": (
                    "largest lot-aligned quantity whose cost does not exceed confirmed cash above buffer"
                ),
            },
            output,
            [],
        )
        return {**output, "calculation_id": calculation["id"]}

    def current_status(
        self, calculation_id: str, *, as_of: str
    ) -> dict[str, Any]:
        """Report whether a historical Funding Condition can support planning now."""

        calculation = self.c.financial.calculation_get(calculation_id)
        if calculation.get("kind") != CALCULATION_KIND:
            raise CompanionError(
                "Funding Condition status requires a Funding Condition Calculation"
            )
        current_at = iso(parse(as_of))
        valid_until = calculation["outputs"]["validity"]["valid_until"]
        if parse(current_at) < parse(calculation["as_of"]):
            status = "not_yet_effective"
        elif parse(current_at) >= parse(valid_until):
            status = "expired"
        else:
            risk = self.c.financial.calculation_get(
                calculation["inputs"]["risk_calculation_id"]
            )
            frozen_qualification_id = calculation["inputs"].get(
                "portfolio_qualification_calculation_id"
            )
            frozen_projection = risk["outputs"].get("portfolio_qualification") or {}
            frozen_lineage = risk["assumptions"].get(
                "portfolio_qualification_lineage"
            ) or {}
            if (
                risk["inputs"].get("portfolio_qualification_calculation_id")
                != frozen_qualification_id
                or frozen_projection.get("calculation_id")
                != frozen_qualification_id
                or frozen_lineage.get("calculation_id")
                != frozen_qualification_id
            ):
                status = "lineage_mismatch"
            else:
                current_qualification = (
                    self.c.portfolio_qualification.evaluate_risk_candidate(
                        risk, as_of=current_at
                    )
                )
                if (
                    current_qualification.material_fact_fingerprint
                    != frozen_lineage.get("material_fact_fingerprint")
                    or current_qualification.level != frozen_projection.get("level")
                ):
                    status = "facts_drifted"
                else:
                    mandate = self.c.cognition.context_current("mandate")
                    if (
                        not mandate
                        or mandate["id"]
                        != calculation["inputs"].get("mandate_revision_id")
                        or mandate["content_hash"]
                        != calculation["inputs"].get("mandate_content_hash")
                    ):
                        status = "mandate_drifted"
                    else:
                        status = "current"
        return {
            "calculation_id": calculation_id,
            "as_of": current_at,
            "status": status,
            "supports_current_planning": status == "current",
            "required_reruns": self._reruns(),
        }

    def calculation_is_current(self, calculation_id: str, *, as_of: str) -> bool:
        return bool(
            self.current_status(calculation_id, as_of=as_of)[
                "supports_current_planning"
            ]
        )
