from __future__ import annotations

from typing import Any

from .db import row_dict
from .financial import dec, dtext
from .foundation import CompanionError
from .timeutil import iso, parse, utc_now


QUALIFICATION_FIELDS = {
    "validation_calculation_id",
    "major_unknowns",
    "decision_basis",
}

PORTFOLIO_QUALIFICATION_REASON_PREFIX = "actionability.portfolio_qualification."
PORTFOLIO_QUALIFICATION_REASON_CODES = {
    "missing": f"{PORTFOLIO_QUALIFICATION_REASON_PREFIX}missing",
    "lineage_mismatch": f"{PORTFOLIO_QUALIFICATION_REASON_PREFIX}lineage_mismatch",
    "candidate_mismatch": f"{PORTFOLIO_QUALIFICATION_REASON_PREFIX}candidate_mismatch",
    "expired": f"{PORTFOLIO_QUALIFICATION_REASON_PREFIX}expired",
    "facts_drifted": f"{PORTFOLIO_QUALIFICATION_REASON_PREFIX}facts_drifted",
    "below_preflight_ready": (
        f"{PORTFOLIO_QUALIFICATION_REASON_PREFIX}below_preflight_ready"
    ),
}


class ActionabilityError(CompanionError):
    """Stable, machine-readable failure from an Actionability lifecycle seam."""

    def __init__(self, reason_code: str, message: str):
        self.reason_code = reason_code
        super().__init__(f"{reason_code}: {message}")


class ActionabilityService:
    """Prove that research and risk still support a human investment action.

    The service reads authoritative ledgers and creates deterministic Risk Gate
    calculations.  It does not transition Opportunities, mutate the queue, or
    create Executions.
    """

    def __init__(self, companion):
        self.c = companion

    @staticmethod
    def _text(value: Any, label: str) -> str:
        if not isinstance(value, str) or not value.strip():
            raise CompanionError(f"{label} must be a non-empty string")
        return value.strip()

    @staticmethod
    def _string_list(value: Any, label: str, *, allow_empty: bool = False) -> list[str]:
        if not isinstance(value, list) or (not value and not allow_empty):
            qualifier = "possibly empty " if allow_empty else "non-empty "
            raise CompanionError(f"{label} must be a {qualifier}list")
        if any(not isinstance(item, str) or not item.strip() for item in value):
            raise CompanionError(f"{label} must contain non-empty strings")
        cleaned = [item.strip() for item in value]
        if len(cleaned) != len(set(cleaned)):
            raise CompanionError(f"{label} must not contain duplicates")
        return cleaned

    def validate_decision(self, decision_revision_id: str) -> dict[str, Any]:
        revision = self.c.cognition.revision_get(decision_revision_id)
        decision = self.c.cognition.object_get(revision["object_id"])
        if (
            decision["object_type"] != "decision"
            or decision["status"] != "issued"
            or decision.get("current_revision_id") != decision_revision_id
        ):
            raise CompanionError("Opportunity requires the current issued Decision revision")
        metadata = revision.get("metadata", {})
        for key in ("valid_until", "invalidators", "no_action"):
            if key not in metadata:
                raise CompanionError(f"actionable Decision metadata missing: {key}")
        contract = str(metadata.get("decision_contract_version", ""))
        if parse(metadata["valid_until"]) <= utc_now():
            if contract == "1" and metadata.get("decision_kind") in {
                "action",
                "conditional_action",
            }:
                self._portfolio_qualification_failure(
                    "expired",
                    "the actionable Decision and its candidate Portfolio Qualification have expired",
                )
            raise CompanionError("actionable Decision has expired")
        if not isinstance(metadata["invalidators"], list) or not metadata["invalidators"]:
            raise CompanionError("actionable Decision requires explicit invalidators")
        if not isinstance(metadata["no_action"], dict) or not metadata["no_action"]:
            raise CompanionError("actionable Decision requires a concrete no-action alternative")

        result = {
            "contract": contract,
            "revision": revision,
            "decision": decision,
            "research_validation": None,
            "frozen_risk": None,
            "portfolio_qualification": None,
            "portfolio_qualification_lineage": None,
            "actionability": None,
        }
        if contract == "4":
            return result
        if contract != "1" or metadata.get("decision_kind") not in {
            "action",
            "conditional_action",
        }:
            raise CompanionError(
                "actionable Opportunity requires an action Decision with a supported professional contract"
            )

        validation_id = metadata.get("research_validation_calculation_id")
        risk_id = metadata.get("risk_calculation_id")
        if not validation_id or validation_id not in revision.get("calculation_ids", []):
            raise CompanionError("action Decision lacks its frozen Research Validation Calculation")
        if revision.get("context_refs", {}).get("research_validation_calculation_id") != validation_id:
            raise CompanionError("action Decision Research Validation lineage is inconsistent")
        validation = self.c.research_validation.revalidate(validation_id)
        required_tier = metadata.get("action_tier", "standard")
        validation_eligible = (
            validation["eligible_for_decision"]
            if required_tier == "standard"
            else validation.get("eligible_for_bounded_action", False)
        )
        if not validation_eligible:
            raise CompanionError(
                f"action Decision research validation is no longer eligible: {validation['reasons']}"
            )
        validation_calculation = self.c.financial.calculation_get(validation_id)
        if not revision.get("knowledge_cutoff") or parse(
            validation_calculation["as_of"]
        ) != parse(revision["knowledge_cutoff"]):
            raise CompanionError("action Decision and Research Validation knowledge cutoff differ")
        validation_evidence = validation_calculation["inputs"].get(
            "evidence_manifest_ids", []
        )
        if not set(validation_evidence) <= set(metadata.get("source_refs", [])):
            raise CompanionError("action Decision omits validated research evidence")
        if validation_calculation["kind"] == "thesis_validation":
            thesis_revision_id = validation_calculation["inputs"].get("thesis_revision_id")
            if thesis_revision_id not in revision["context_refs"].get(
                "thesis_revision_ids", []
            ):
                raise CompanionError("action Decision uses Research Validation from another Thesis")
            validation_asset = validation_calculation["inputs"].get("subject", {}).get(
                "asset_id"
            )
            if validation_asset and decision.get("subject", {}).get("asset_id") != validation_asset:
                raise CompanionError("action Decision uses Research Validation from another asset")
        else:
            strategy_id = validation_calculation["inputs"].get("strategy_version_id")
            if not strategy_id or decision.get("subject", {}).get("strategy_version_id") != strategy_id:
                raise CompanionError("action Decision uses Research Validation from another StrategyVersion")
        if not risk_id or risk_id not in revision.get("calculation_ids", []):
            raise CompanionError("action Decision lacks its frozen Risk Gate Calculation")
        risk = self.c.financial.calculation_get(risk_id)
        if risk["kind"] != "risk_gate" or risk["outputs"].get("blocked"):
            raise CompanionError("action Decision requires a passing frozen Risk Gate")
        portfolio_id = revision["context_refs"].get("portfolio_calculation_id")
        if (
            not portfolio_id
            or portfolio_id not in revision.get("calculation_ids", [])
            or risk["outputs"].get("portfolio_calculation_id") != portfolio_id
        ):
            raise CompanionError("action Decision Risk Gate and Portfolio lineage differ")
        subject_asset = decision.get("subject", {}).get("asset_id")
        if subject_asset and risk["inputs"].get("asset_id") != subject_asset:
            self._portfolio_qualification_failure(
                "candidate_mismatch",
                "action Decision, Risk and Portfolio Qualification identify different assets",
            )
        qualification, qualification_lineage = self._validate_portfolio_qualification(
            revision=revision,
            risk=risk,
            current_at=iso(utc_now()),
        )

        investor = self.c.cognition.context_current("investor")
        mandate = self.c.cognition.context_current("mandate")
        if not investor or revision["context_refs"].get("investor_revision_id") != investor["id"]:
            raise CompanionError("action Decision Investor context has changed")
        if not mandate or revision["context_refs"].get("mandate_revision_id") != mandate["id"]:
            raise CompanionError("action Decision Mandate context has changed")
        if risk["assumptions"].get("mandate") != mandate["content"]:
            raise CompanionError("action Decision Risk Gate does not use the current Mandate")
        if metadata.get("confirmed_ledger_hash") != self.c.financial.confirmed_ledger_hash():
            raise CompanionError("action Decision confirmed Ledger has changed")
        if risk["assumptions"].get("valid_until") != metadata["valid_until"]:
            raise CompanionError("action Decision and Risk Gate validity differ")
        if risk["assumptions"].get("action_tier", "standard") != required_tier:
            raise CompanionError("action Decision and Risk Gate action tier differ")
        if required_tier == "bounded":
            program = self.c.operating.program_current()
            revision = program.get("current_revision") if program else None
            policy = revision.get("content", {}).get("risk_budget", {}).get(
                "bounded_action"
            ) if revision else None
            if not revision or risk["assumptions"].get("program_revision_id") != revision["id"]:
                raise CompanionError("bounded action Program revision has changed")
            if risk["assumptions"].get("bounded_action_policy") != policy:
                raise CompanionError("bounded action risk policy has changed")
        result["research_validation"] = validation
        result["frozen_risk"] = risk
        result["portfolio_qualification"] = qualification
        result["portfolio_qualification_lineage"] = qualification_lineage
        return result

    @staticmethod
    def _portfolio_qualification_failure(code: str, message: str) -> None:
        raise ActionabilityError(PORTFOLIO_QUALIFICATION_REASON_CODES[code], message)

    def _validate_portfolio_qualification(
        self,
        *,
        revision: dict[str, Any],
        risk: dict[str, Any],
        current_at: str,
    ):
        metadata = revision["metadata"]
        context_refs = revision.get("context_refs", {})
        qualification_id = metadata.get("portfolio_qualification_calculation_id")
        if not qualification_id:
            self._portfolio_qualification_failure(
                "missing",
                "the action Decision lacks its candidate Portfolio Qualification",
            )

        frozen = metadata.get("portfolio_qualification")
        lineage = metadata.get("portfolio_qualification_lineage")
        risk_frozen = risk["outputs"].get("portfolio_qualification")
        risk_lineage = risk["assumptions"].get("portfolio_qualification_lineage")
        linked_ids = {
            qualification_id,
            context_refs.get("portfolio_qualification_calculation_id"),
            frozen.get("calculation_id") if isinstance(frozen, dict) else None,
            lineage.get("calculation_id") if isinstance(lineage, dict) else None,
            risk["inputs"].get("portfolio_qualification_calculation_id"),
            risk_frozen.get("calculation_id") if isinstance(risk_frozen, dict) else None,
            risk_lineage.get("calculation_id") if isinstance(risk_lineage, dict) else None,
        }
        if (
            not isinstance(frozen, dict)
            or not isinstance(lineage, dict)
            or not isinstance(risk_frozen, dict)
            or not isinstance(risk_lineage, dict)
            or linked_ids != {qualification_id}
            or frozen != risk_frozen
            or lineage != risk_lineage
        ):
            self._portfolio_qualification_failure(
                "lineage_mismatch",
                "Opportunity, Decision and Risk must preserve one candidate Portfolio Qualification lineage",
            )

        if parse(current_at) >= parse(metadata["valid_until"]):
            self._portfolio_qualification_failure(
                "expired",
                "the candidate Portfolio Qualification validity has elapsed",
            )

        try:
            qualification, checked_lineage = (
                self.c.portfolio_qualification.revalidate_frozen_risk_candidate(
                    risk=risk,
                    calculation_id=qualification_id,
                    account_id=risk["inputs"]["account_id"],
                    as_of=risk["as_of"],
                    valid_until=metadata["valid_until"],
                    current_at=current_at,
                )
            )
        except CompanionError as exc:
            message = str(exc)
            if (
                "facts have drifted" in message
                or "candidate has expired" in message
                or "frozen lineage is inconsistent" in message
            ):
                current = self.c.portfolio_qualification.evaluate_risk_candidate(
                    risk, as_of=current_at
                )
                level_detail = (
                    f"; current level {current.level} is below preflight_ready"
                    if current.level != "preflight_ready"
                    else ""
                )
                self._portfolio_qualification_failure(
                    "facts_drifted",
                    "the frozen candidate facts have drifted"
                    f"{level_detail}; recalculate Portfolio Qualification, Risk and Decision",
                )
            if "another account" in message or "another candidate" in message:
                self._portfolio_qualification_failure("candidate_mismatch", message)
            self._portfolio_qualification_failure("lineage_mismatch", message)

        candidate = checked_lineage.get("candidate", {})
        signed_quantity = dec(risk["inputs"].get("quantity"), "Risk candidate quantity")
        risk_price_range = risk["assumptions"].get("price_range", {})
        expected_candidate = {
            "account_id": risk["inputs"].get("account_id"),
            "asset_id": risk["inputs"].get("asset_id"),
            "direction": "buy" if signed_quantity > 0 else "sell",
            "quantity": dtext(abs(signed_quantity)),
            "reference_price": dtext(dec(risk["inputs"].get("price"), "Risk candidate price")),
            "price_range": {
                "min": dtext(dec(risk_price_range.get("min"), "Risk minimum price")),
                "max": dtext(dec(risk_price_range.get("max"), "Risk maximum price")),
            },
            "market_snapshot_id": risk["inputs"].get("market_snapshot_id"),
            "max_market_age_seconds": risk["assumptions"].get(
                "max_market_age_seconds"
            ),
            "as_of": risk["as_of"],
            "valid_until": risk["assumptions"].get("valid_until"),
        }
        if candidate != expected_candidate:
            self._portfolio_qualification_failure(
                "candidate_mismatch",
                "the Risk candidate differs from the frozen Portfolio Qualification candidate",
            )
        if qualification.level != "preflight_ready" or (
            "precise_decision_support" not in qualification.allowed_uses
        ):
            self._portfolio_qualification_failure(
                "below_preflight_ready",
                f"candidate level {qualification.level} cannot form an action or conditional-action Action Card",
            )
        if risk["outputs"].get("precise_action_eligible") is not True:
            self._portfolio_qualification_failure(
                "below_preflight_ready",
                "Risk does not authorize precise action under the shared Portfolio Qualification",
            )
        return qualification, checked_lineage

    def validate_action_card(
        self,
        *,
        opportunity: dict[str, Any],
        decision_revision_id: str,
        stage: str,
    ) -> dict[str, Any]:
        """Revalidate one candidate lineage at an Action Card lifecycle seam."""

        if stage not in {"enqueue", "project", "present", "accept", "execution"}:
            raise CompanionError("unsupported Actionability lifecycle stage")
        gate = self.validate_decision(decision_revision_id)
        if gate["contract"] == "4":
            return gate
        if (
            opportunity.get("status") != "active"
            or opportunity.get("stage") != "actionable"
            or opportunity.get("decision_revision_id") != decision_revision_id
        ):
            self._portfolio_qualification_failure(
                "candidate_mismatch",
                "the Opportunity no longer identifies this actionable Decision candidate",
            )
        revision = gate["revision"]
        validation_id = (opportunity.get("qualification") or {}).get(
            "validation_calculation_id"
        )
        if validation_id != revision["metadata"].get(
            "research_validation_calculation_id"
        ):
            self._portfolio_qualification_failure(
                "lineage_mismatch",
                "Opportunity and Decision research lineage differ for the qualified candidate",
            )
        opportunity_asset = opportunity.get("subject", {}).get("asset_id")
        candidate = gate["portfolio_qualification"].candidate
        if opportunity_asset and opportunity_asset != candidate.asset_id:
            self._portfolio_qualification_failure(
                "candidate_mismatch",
                "Opportunity, Decision, Risk and Portfolio Qualification identify different candidates",
            )
        gate["actionability"] = {
            "schema": "investment-companion.actionability/v1",
            "stage": stage,
            "status": "actionable",
            "reason_code": None,
            "portfolio_qualification_calculation_id": gate[
                "portfolio_qualification"
            ].calculation_id,
            "portfolio_qualification_level": gate["portfolio_qualification"].level,
            "allowed_uses": list(gate["portfolio_qualification"].allowed_uses),
            "no_action_inferred": False,
            "human_acceptance_required": True,
            "final_broker_preflight_required": True,
        }
        return gate

    def validate_qualification(
        self,
        qualification: Any,
        stage: str,
        opportunity: dict[str, Any],
        evidence_refs: list[str],
    ) -> dict[str, Any]:
        if not isinstance(qualification, dict) or set(qualification) != QUALIFICATION_FIELDS:
            raise CompanionError(f"qualification must be exactly: {sorted(QUALIFICATION_FIELDS)}")
        calculation_id = self._text(
            qualification["validation_calculation_id"], "validation_calculation_id"
        )
        if calculation_id not in evidence_refs:
            raise CompanionError("Opportunity evidence_refs must include its Research Validation Calculation")
        calculation = self.c.financial.calculation_get(calculation_id)
        if calculation["kind"] not in {"thesis_validation", "strategy_validation"}:
            raise CompanionError("Opportunity qualification requires a Research Validation Calculation")
        validation = self.c.research_validation.revalidate(calculation_id)
        allowed = (
            {"eligible_for_bounded_action", "eligible_for_decision"}
            if stage == "actionable"
            else {"eligible_for_bounded_action", "eligible_for_shadow", "eligible_for_decision"}
        )
        if validation["current_status"] not in allowed:
            raise CompanionError(
                f"Opportunity cannot enter {stage} with validation status "
                f"{validation['current_status']}: {validation['reasons']}"
            )
        if calculation["kind"] == "thesis_validation":
            thesis_revision = self.c.cognition.revision_get(
                calculation["inputs"].get("thesis_revision_id")
            )
            if (
                not opportunity.get("thesis_id")
                or thesis_revision["object_id"] != opportunity["thesis_id"]
            ):
                raise CompanionError("Opportunity Research Validation belongs to another Thesis")
        elif calculation["inputs"].get("strategy_version_id") != opportunity.get(
            "strategy_version_id"
        ):
            raise CompanionError("Opportunity Research Validation belongs to another StrategyVersion")
        underlying_evidence = calculation["inputs"].get("evidence_manifest_ids", [])
        if not set(underlying_evidence) <= set(evidence_refs):
            raise CompanionError("Opportunity evidence_refs omit validated research evidence")
        unknowns = self._string_list(
            qualification["major_unknowns"], "major_unknowns", allow_empty=True
        )
        return {
            "validation_calculation_id": calculation_id,
            "major_unknowns": unknowns,
            "decision_basis": self._text(qualification["decision_basis"], "decision_basis"),
        }

    def qualification_status(self, opportunity: dict[str, Any]) -> dict[str, Any] | None:
        calculation_id = (opportunity.get("qualification") or {}).get(
            "validation_calculation_id"
        )
        if not calculation_id:
            return None
        try:
            return self.c.research_validation.revalidate(calculation_id)
        except CompanionError as exc:
            return {
                "calculation_id": calculation_id,
                "frozen_status": "unknown",
                "current_status": "invalid",
                "eligible_for_decision": False,
                "reasons": [str(exc)],
                "checked_at": iso(),
            }

    def validate_actionable_transition(
        self,
        opportunity: dict[str, Any],
        qualification: dict[str, Any],
        evidence_refs: list[str],
        decision_revision_id: str,
    ) -> dict[str, Any]:
        gate = self.validate_decision(decision_revision_id)
        if decision_revision_id not in evidence_refs:
            raise CompanionError("Opportunity evidence_refs must include its Decision revision")
        if gate["contract"] == "1":
            validation_id = qualification["validation_calculation_id"]
            if gate["revision"]["metadata"].get("research_validation_calculation_id") != validation_id:
                raise CompanionError("Opportunity and Decision use different Research Validation Calculations")
            risk_id = gate["revision"]["metadata"].get("risk_calculation_id")
            if risk_id not in evidence_refs:
                raise CompanionError("Opportunity evidence_refs must include its frozen Risk Gate Calculation")
            if gate["revision"]["metadata"].get("action_tier", "standard") == "standard" and qualification["major_unknowns"]:
                raise CompanionError("standard actionable Opportunity requires no major unknowns")
        return gate

    def revalidate_generic_action(self, decision_revision_id: str) -> dict[str, Any]:
        gate = self.validate_decision(decision_revision_id)
        if gate["contract"] != "1":
            raise CompanionError("generic Action Card requires Decision contract version 1")
        risk = gate["frozen_risk"]
        now = utc_now()
        with self.c.db.connect() as con:
            market = row_dict(
                con.execute(
                    "SELECT * FROM market_snapshots WHERE asset_id=? AND metric='close' "
                    "AND observed_at<=? ORDER BY observed_at DESC,id DESC LIMIT 1",
                    (risk["inputs"]["asset_id"], iso(now)),
                ).fetchone()
            )
        quantity = dec(risk["inputs"]["quantity"], "risk quantity")
        action = {
            "account_id": risk["inputs"]["account_id"],
            "asset_id": risk["inputs"]["asset_id"],
            "side": "buy" if quantity > 0 else "sell",
            "quantity": dtext(abs(quantity)),
            "price_range": risk["assumptions"].get("price_range"),
            "priority": None,
            "alternatives": gate["revision"]["metadata"].get("alternatives", []),
        }
        if not market:
            return {
                "action": action,
                "executable": False,
                "reasons": ["market_snapshot_missing"],
                "market_snapshot_id": None,
                "risk_calculation_id": None,
                "research_validation": gate["research_validation"],
                "frozen_risk_calculation_id": risk["id"],
            }
        assumptions = risk["assumptions"]
        current = self.c.investment_commands.risk_assess(
            as_of=iso(now),
            account_id=risk["inputs"]["account_id"],
            asset_id=risk["inputs"]["asset_id"],
            quantity=risk["inputs"]["quantity"],
            price=market["value_text"],
            reality_spec=assumptions["reality_spec"],
            market_snapshot_id=market["id"],
            max_market_age_seconds=assumptions["max_market_age_seconds"],
            valid_until=assumptions["valid_until"],
            price_range=assumptions["price_range"],
            average_daily_amount=assumptions.get("average_daily_amount"),
            action_tier=assumptions.get("action_tier", "standard"),
            validity_sessions=assumptions.get("validity_sessions"),
        )
        reasons = [item["rule"] for item in current["violations"]]
        if current.get("precise_action_eligible") is not True:
            reasons.append(
                PORTFOLIO_QUALIFICATION_REASON_CODES["below_preflight_ready"]
            )
        sell_current = None
        execution_plan = gate["revision"]["metadata"].get("execution_plan")
        if execution_plan and execution_plan.get("plan_type") == "moving_grid":
            sell_risk_id = execution_plan.get("sell_risk_calculation_id")
            sell_frozen = self.c.financial.calculation_get(sell_risk_id)
            sell_assumptions = sell_frozen["assumptions"]
            sell_current = self.c.investment_commands.risk_assess(
                as_of=iso(now),
                account_id=sell_frozen["inputs"]["account_id"],
                asset_id=sell_frozen["inputs"]["asset_id"],
                quantity=sell_frozen["inputs"]["quantity"],
                price=market["value_text"],
                reality_spec=sell_assumptions["reality_spec"],
                market_snapshot_id=market["id"],
                max_market_age_seconds=sell_assumptions["max_market_age_seconds"],
                valid_until=sell_assumptions["valid_until"],
                price_range=sell_assumptions["price_range"],
                average_daily_amount=sell_assumptions.get("average_daily_amount"),
                action_tier=sell_assumptions.get("action_tier", "standard"),
                validity_sessions=sell_assumptions.get("validity_sessions"),
            )
            reasons.extend(
                f"grid_sell:{item['rule']}" for item in sell_current["violations"]
            )
            if sell_current.get("precise_action_eligible") is not True:
                reasons.append(
                    "grid_sell:"
                    + PORTFOLIO_QUALIFICATION_REASON_CODES[
                        "below_preflight_ready"
                    ]
                )
        return {
            "action": action,
            "executable": not current["blocked"]
            and current.get("precise_action_eligible") is True
            and (
                sell_current is None
                or (
                    not sell_current["blocked"]
                    and sell_current.get("precise_action_eligible") is True
                )
            ),
            "reasons": reasons,
            "market_snapshot_id": market["id"],
            "risk_calculation_id": current["calculation_id"],
            "sell_risk_calculation_id": sell_current["calculation_id"] if sell_current else None,
            "portfolio_qualification": current["portfolio_qualification"],
            "sell_portfolio_qualification": (
                sell_current["portfolio_qualification"] if sell_current else None
            ),
            "research_validation": gate["research_validation"],
            "frozen_risk_calculation_id": risk["id"],
        }
