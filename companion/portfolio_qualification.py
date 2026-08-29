from __future__ import annotations

from dataclasses import dataclass
import json
from typing import Any

from .db import row_dict, rows_dict
from .financial import dec, dtext, reconciliation_is_full_match
from .foundation import CompanionError, canonical, digest
from .timeutil import iso, parse


POLICY_VERSION = "portfolio-qualification-policy/v1"
STALE_AFTER_SECONDS = 3 * 24 * 60 * 60
OPEN_EXECUTION_STATUSES = ("accepted", "ordered", "partially_filled")
ACTIVE_BROKER_STRATEGY_STATUSES = (
    "active",
    "sleeping",
    "termination_pending",
    "exception",
)
MATERIAL_DRIFT_EVENTS = (
    "confirmed_ledger_change",
    "reconciliation_change",
    "account_continuity_change",
    "pending_ledger_change",
    "open_execution_change",
    "broker_strategy_change",
)
CANDIDATE_MATERIAL_DRIFT_EVENTS = (
    *MATERIAL_DRIFT_EVENTS,
    "related_market_snapshot_change",
)
LEVEL_RANK = {
    "unavailable": 0,
    "directional_only": 1,
    "range_ready": 2,
    "preflight_ready": 3,
}
ALLOWED_USES = {
    "unavailable": (
        "research",
        "account_fact_repair",
    ),
    "directional_only": (
        "research",
        "account_fact_repair",
        "directional_view",
        "watch",
    ),
    "range_ready": (
        "research",
        "account_fact_repair",
        "directional_view",
        "watch",
        "quantity_ranges",
        "conditional_plans",
        "funding_conditions",
    ),
    "preflight_ready": (
        "research",
        "account_fact_repair",
        "directional_view",
        "watch",
        "quantity_ranges",
        "conditional_plans",
        "funding_conditions",
        "precise_decision_support",
    ),
}


@dataclass(frozen=True)
class PortfolioQualificationBlocker:
    code: str
    summary: str
    required_action_code: str
    required_action_summary: str
    level_cap: str

    def projection(self) -> dict[str, str]:
        return {"code": self.code, "summary": self.summary}

    def required_action(self) -> dict[str, str]:
        return {
            "code": self.required_action_code,
            "summary": self.required_action_summary,
        }


@dataclass(frozen=True)
class PortfolioQualificationFactLineage:
    confirmed_ledger_entry_ids: tuple[str, ...]
    confirmed_ledger_fingerprint: str
    reconciliation_id: str | None
    reconciliation_fingerprint: str | None
    continuity_confirmation_id: str | None
    continuity_fingerprint: str | None
    pending_ledger_entry_ids: tuple[str, ...]
    open_execution_ids: tuple[str, ...]
    active_broker_strategy_ids: tuple[str, ...]


@dataclass(frozen=True)
class PortfolioQualificationFacts:
    confirmed_ledger_entry_count: int
    latest_confirmed_ledger_at: str | None
    latest_reconciliation_as_of: str | None
    latest_reconciliation_status: str | None
    latest_reconciliation_full_scope_matched: bool
    full_scope_match_ever_established: bool
    reconciliation_age_seconds: int | None
    reconciliation_stale: bool
    reconciliation_uncertainty_unbounded: bool
    reconciliation_difference_bounded: bool
    pending_ledger_entry_count: int
    open_execution_count: int
    active_broker_strategy_count: int
    account_continuity_present: bool
    account_continuity_valid: bool
    continuity_confirmation_json: str | None
    broker_realtime_proven: bool = False


@dataclass(frozen=True)
class PortfolioQualificationCalculation:
    calculation_id: str
    account_id: str
    as_of: str
    policy_version: str
    level: str
    allowed_uses: tuple[str, ...]
    blockers: tuple[PortfolioQualificationBlocker, ...]
    required_actions: tuple[tuple[str, str], ...]
    fact_lineage: PortfolioQualificationFactLineage
    facts: PortfolioQualificationFacts
    material_fact_fingerprint: str

    def stable_projection(self, *, compact: bool = False) -> dict[str, Any]:
        projection: dict[str, Any] = {
            "calculation_id": self.calculation_id,
            "level": self.level,
            "allowed_uses": list(self.allowed_uses),
            "blockers": [blocker.projection() for blocker in self.blockers],
            "reason_codes": [blocker.code for blocker in self.blockers],
            "required_actions": [
                {"code": code, "summary": summary}
                for code, summary in self.required_actions
            ],
            "as_of": self.as_of,
            "validity": {
                "status": "current_at_as_of",
                "recalculate_on": list(MATERIAL_DRIFT_EVENTS),
                "broker_realtime_proven": False,
                "final_broker_preflight_required": True,
            },
        }
        if compact:
            return projection
        return {
            **projection,
            "account_id": self.account_id,
            "policy_version": self.policy_version,
            "facts": {
                "confirmed_ledger_entry_count": self.facts.confirmed_ledger_entry_count,
                "latest_confirmed_ledger_at": self.facts.latest_confirmed_ledger_at,
                "latest_reconciliation_as_of": self.facts.latest_reconciliation_as_of,
                "latest_reconciliation_status": self.facts.latest_reconciliation_status,
                "latest_reconciliation_full_scope_matched": (
                    self.facts.latest_reconciliation_full_scope_matched
                ),
                "full_scope_match_ever_established": (
                    self.facts.full_scope_match_ever_established
                ),
                "reconciliation_stale": self.facts.reconciliation_stale,
                "pending_ledger_entry_count": self.facts.pending_ledger_entry_count,
                "open_execution_count": self.facts.open_execution_count,
                "active_broker_strategy_count": self.facts.active_broker_strategy_count,
                "account_continuity_present": self.facts.account_continuity_present,
                "account_continuity_valid": self.facts.account_continuity_valid,
                "broker_realtime_proven": False,
            },
        }

    def legacy_truth_freshness(self) -> dict[str, Any]:
        codes = {blocker.code for blocker in self.blockers}
        if codes & {
            "reconciliation_uncertainty_unbounded",
            "reconciliation_difference_bounded",
        }:
            status = "reconciliation_needs_review"
            warning = (
                "latest reconciliation did not match cash, positions, valuations, and total value"
            )
            required_action = (
                "resolve the latest reconciliation differences; conditional advice remains allowed"
            )
        elif self.facts.pending_ledger_entry_count:
            status = "pending_transactions"
            warning = (
                "pending transactions must be confirmed or rejected before exact order sizing"
            )
            required_action = (
                "resolve pending transactions; conditional advice remains allowed"
            )
        elif self.facts.open_execution_count or self.facts.active_broker_strategy_count:
            status = "open_execution_preflight_required"
            warning = (
                "open orders or broker-managed conditions can still change available cash or holdings"
            )
            required_action = (
                "reconcile open executions and broker conditions; conditional advice remains allowed"
            )
        elif self.facts.reconciliation_stale and self.facts.account_continuity_valid:
            status = "ledger_continuity_confirmed"
            warning = (
                "broker statement is dated, but quantities and cash remain usable under the active "
                "user reporting commitment; refresh market prices and preflight the final order in the broker App"
            )
            required_action = (
                "refresh market prices and verify broker available cash/holdings before submitting the final order"
            )
        elif self.facts.reconciliation_stale:
            status = "continuity_confirmation_required"
            warning = (
                "broker statement is dated and no active user reporting commitment proves ledger continuity"
            )
            required_action = (
                "ask the user to confirm no unrecorded trades, cash flows, income/fees/taxes, "
                "corporate actions or open orders since the last matched reconciliation; "
                "until then provide ranges and conditional quantities, not silence or no_action"
            )
        else:
            status = "recently_reconciled"
            warning = None
            required_action = (
                "refresh market prices and verify broker available cash/holdings before submitting the final order"
            )
        continuity = (
            json.loads(self.facts.continuity_confirmation_json)
            if self.facts.continuity_confirmation_json
            else None
        )
        return {
            "verified_at": (
                self.facts.latest_reconciliation_as_of
                if self.facts.latest_reconciliation_full_scope_matched
                else None
            ),
            "reconciliation_id": self.fact_lineage.reconciliation_id,
            "latest_reconciliation_as_of": self.facts.latest_reconciliation_as_of,
            "full_scope_matched": self.facts.latest_reconciliation_full_scope_matched,
            "latest_confirmed_ledger_at": self.facts.latest_confirmed_ledger_at,
            "age_seconds": self.facts.reconciliation_age_seconds,
            "stale_after_seconds": STALE_AFTER_SECONDS,
            "reconciliation_stale": self.facts.reconciliation_stale,
            "status": status,
            "stale": self.level != "preflight_ready",
            "warning": warning,
            "pending_transaction_count": self.facts.pending_ledger_entry_count,
            "open_execution_count": self.facts.open_execution_count,
            "active_broker_strategy_count": self.facts.active_broker_strategy_count,
            "continuity_confirmation": continuity,
            "ledger_continuity_supported": self.facts.account_continuity_valid,
            "broker_position_recently_proven": bool(
                self.facts.latest_reconciliation_full_scope_matched
                and not self.facts.reconciliation_stale
            ),
            "precise_position_advice_allowed": self.level == "preflight_ready",
            "required_action": required_action,
        }

    def legacy_precision_boundary(self) -> dict[str, Any]:
        freshness = self.legacy_truth_freshness()
        return {
            "current_broker_position_proven": freshness[
                "broker_position_recently_proven"
            ],
            "ledger_position_continuity_supported": freshness[
                "ledger_continuity_supported"
            ],
            "precise_position_advice_allowed": freshness[
                "precise_position_advice_allowed"
            ],
            "conditional_position_advice_allowed": True,
            "market_revaluation_required": True,
            "market_moves_do_not_invalidate_quantities": True,
            "final_order_quantities_require_broker_preflight": True,
            "required_when_stale": freshness["required_action"],
        }


@dataclass(frozen=True)
class PortfolioQualificationCandidate:
    account_id: str
    asset_id: str
    direction: str
    quantity: str
    reference_price: str
    price_range: tuple[str, str]
    market_snapshot_id: str | None
    max_market_age_seconds: int
    as_of: str
    valid_until: str

    def projection(self, *, level: str) -> dict[str, Any]:
        quantity_available = LEVEL_RANK[level] >= LEVEL_RANK["range_ready"]
        return {
            "account_id": self.account_id,
            "asset_id": self.asset_id,
            "direction": self.direction,
            "quantity": self.quantity if quantity_available else None,
            "quantity_kind": (
                "exact_candidate"
                if quantity_available
                else "withheld_below_range_ready"
            ),
            "reference_price": self.reference_price,
            "price_range": {
                "min": self.price_range[0],
                "max": self.price_range[1],
            },
            "valid_until": self.valid_until,
        }


@dataclass(frozen=True)
class PortfolioQualificationMarketEvidence:
    market_snapshot_id: str | None
    latest_relevant_snapshot_id: str | None
    observed_at: str | None
    age_seconds: int | None
    max_age_seconds: int
    fresh: bool

    def projection(self) -> dict[str, Any]:
        return {
            "market_snapshot_id": self.market_snapshot_id,
            "latest_relevant_snapshot_id": self.latest_relevant_snapshot_id,
            "observed_at": self.observed_at,
            "age_seconds": self.age_seconds,
            "max_age_seconds": self.max_age_seconds,
            "fresh": self.fresh,
        }


@dataclass(frozen=True)
class PortfolioCandidateQualificationCalculation:
    calculation_id: str
    account_qualification: PortfolioQualificationCalculation
    candidate: PortfolioQualificationCandidate
    market_evidence: PortfolioQualificationMarketEvidence
    policy_version: str
    level: str
    allowed_uses: tuple[str, ...]
    blockers: tuple[PortfolioQualificationBlocker, ...]
    required_actions: tuple[tuple[str, str], ...]
    market_fact_fingerprint: str
    material_fact_fingerprint: str

    @property
    def as_of(self) -> str:
        return self.candidate.as_of

    def stable_projection(self) -> dict[str, Any]:
        return {
            "calculation_id": self.calculation_id,
            "account_calculation_id": self.account_qualification.calculation_id,
            "level": self.level,
            "allowed_uses": list(self.allowed_uses),
            "blockers": [blocker.projection() for blocker in self.blockers],
            "reason_codes": [blocker.code for blocker in self.blockers],
            "required_actions": [
                {"code": code, "summary": summary}
                for code, summary in self.required_actions
            ],
            "as_of": self.as_of,
            "validity": {
                "status": "current_at_as_of",
                "valid_until": self.candidate.valid_until,
                "recalculate_on": list(CANDIDATE_MATERIAL_DRIFT_EVENTS),
                "market_snapshot_fresh": self.market_evidence.fresh,
                "broker_realtime_proven": False,
                "final_broker_preflight_required": True,
            },
            "account_id": self.candidate.account_id,
            "policy_version": self.policy_version,
            "candidate": self.candidate.projection(level=self.level),
            "market_evidence": self.market_evidence.projection(),
            "no_action_inferred": False,
        }


class PortfolioQualificationService:
    """Single read-only authority for account portfolio-fact qualification."""

    def __init__(self, companion):
        self.c = companion

    def evaluate_account(
        self, *, account_id: str, as_of: str
    ) -> PortfolioQualificationCalculation:
        self.c.financial.account_get(account_id)
        effective_at = iso(parse(as_of))
        cutoff = parse(effective_at)
        with self.c.db.connect() as con:
            confirmed_ledger = rows_dict(
                con.execute(
                    "SELECT * FROM ledger_entries WHERE account_id=? "
                    "AND status IN ('confirmed','reversed') "
                    "AND julianday(occurred_at)<=julianday(?) "
                    "ORDER BY julianday(occurred_at),id",
                    (account_id, effective_at),
                ).fetchall()
            )
            pending_ledger = rows_dict(
                con.execute(
                    "SELECT * FROM ledger_entries WHERE account_id=? "
                    "AND status='needs_confirmation' ORDER BY julianday(occurred_at),id",
                    (account_id,),
                ).fetchall()
            )
            reconciliations = rows_dict(
                con.execute(
                    "SELECT * FROM reconciliations WHERE account_id=? "
                    "AND julianday(as_of)<=julianday(?) "
                    "ORDER BY julianday(as_of) DESC,rowid DESC",
                    (account_id, effective_at),
                ).fetchall()
            )
            continuities = rows_dict(
                con.execute(
                    "SELECT * FROM account_continuity_confirmations "
                    "WHERE account_id=? ORDER BY julianday(confirmed_at) DESC,rowid DESC",
                    (account_id,),
                ).fetchall()
            )
            execution_rows = rows_dict(
                con.execute(
                    "SELECT * FROM executions WHERE status IN (?,?,?) ORDER BY id",
                    OPEN_EXECUTION_STATUSES,
                ).fetchall()
            )
            broker_strategies = rows_dict(
                con.execute(
                    "SELECT * FROM broker_execution_plans WHERE account_id=? "
                    "AND status IN (?,?,?,?) ORDER BY id",
                    (account_id, *ACTIVE_BROKER_STRATEGY_STATUSES),
                ).fetchall()
            )

        open_executions = [
            row
            for row in execution_rows
            if self._execution_account_id(row) == account_id
        ]
        latest_reconciliation = reconciliations[0] if reconciliations else None
        latest_full_match = next(
            (
                reconciliation
                for reconciliation in reconciliations
                if reconciliation_is_full_match(reconciliation)
            ),
            None,
        )
        latest_is_full_match = reconciliation_is_full_match(latest_reconciliation)
        differences = (
            latest_reconciliation.get("differences", [])
            if latest_reconciliation
            else []
        )
        uncertainty_unbounded = bool(
            latest_reconciliation
            and not latest_is_full_match
            and any(
                difference.get("kind") in {"unverified_scope", "valuation_unavailable"}
                for difference in differences
            )
        )
        difference_bounded = bool(
            latest_reconciliation
            and not latest_is_full_match
            and not uncertainty_unbounded
        )
        reconciliation_age_seconds = (
            max(
                0,
                int(
                    (
                        cutoff - parse(latest_reconciliation["as_of"])
                    ).total_seconds()
                ),
            )
            if latest_is_full_match
            else None
        )
        reconciliation_stale = bool(
            not latest_is_full_match
            or reconciliation_age_seconds is None
            or reconciliation_age_seconds > STALE_AFTER_SECONDS
        )
        continuity = continuities[0] if continuities else None
        anchor = next(
            (
                reconciliation
                for reconciliation in reconciliations
                if continuity
                and reconciliation["id"] == continuity.get("anchor_reconciliation_id")
            ),
            None,
        )
        continuity_valid = bool(
            continuity
            and continuity.get("status") == "active"
            and bool(continuity.get("reporting_commitment"))
            and anchor
            and reconciliation_is_full_match(anchor)
            and parse(continuity["confirmed_at"])
            >= parse(continuity["anchor_reconciliation_as_of"])
            and parse(continuity["confirmed_at"]) <= cutoff
        )

        blockers: list[tuple[int, PortfolioQualificationBlocker]] = []

        def block(
            priority: int,
            *,
            code: str,
            summary: str,
            required_action_code: str,
            required_action_summary: str,
            level_cap: str,
        ) -> None:
            blockers.append(
                (
                    priority,
                    PortfolioQualificationBlocker(
                        code=code,
                        summary=summary,
                        required_action_code=required_action_code,
                        required_action_summary=required_action_summary,
                        level_cap=level_cap,
                    ),
                )
            )

        if not confirmed_ledger:
            block(
                0,
                code="confirmed_ledger_unavailable",
                summary="No confirmed Ledger entry is available for this account at the decision time.",
                required_action_code="establish_confirmed_ledger",
                required_action_summary="Confirm an opening balance or other account Ledger fact before making portfolio judgments.",
                level_cap="unavailable",
            )
        if uncertainty_unbounded:
            block(
                10,
                code="reconciliation_uncertainty_unbounded",
                summary="The latest reconciliation leaves at least one account scope unverified or unvalued.",
                required_action_code="resolve_unbounded_reconciliation",
                required_action_summary="Provide and resolve a full-scope reconciliation before using quantities.",
                level_cap="directional_only",
            )
        elif difference_bounded:
            block(
                20,
                code="reconciliation_difference_bounded",
                summary="The latest full-scope comparison has explicit, bounded differences.",
                required_action_code="resolve_bounded_reconciliation",
                required_action_summary="Resolve the listed reconciliation differences before exact sizing.",
                level_cap="range_ready",
            )
        if pending_ledger:
            block(
                30,
                code="pending_ledger_entries",
                summary="One or more reported financial events still await user confirmation.",
                required_action_code="resolve_pending_ledger_entries",
                required_action_summary="Confirm or reject every pending Ledger entry before exact sizing.",
                level_cap="range_ready",
            )
        if open_executions:
            block(
                40,
                code="open_executions",
                summary="Accepted, ordered, or partially filled activity can still change cash or holdings.",
                required_action_code="reconcile_open_executions",
                required_action_summary="Finish, cancel, or reconcile open Executions before exact sizing.",
                level_cap="range_ready",
            )
        if broker_strategies:
            block(
                50,
                code="active_broker_strategies",
                summary="A broker-managed strategy can still create orders or change available resources.",
                required_action_code="reconcile_broker_strategies",
                required_action_summary="Terminate or reconcile active broker strategies before exact sizing.",
                level_cap="range_ready",
            )
        if not latest_reconciliation:
            block(
                60,
                code="full_scope_match_never_established",
                summary="This account has never established a full-scope matched reconciliation.",
                required_action_code="establish_full_scope_match",
                required_action_summary="Complete a full-scope account reconciliation to establish an anchor.",
                level_cap="range_ready",
            )
        elif latest_is_full_match and reconciliation_stale and not continuity_valid:
            block(
                70,
                code=(
                    "account_continuity_invalid"
                    if continuity
                    else "account_continuity_missing"
                ),
                summary=(
                    "The latest Account Continuity confirmation is not active or valid for its matched anchor."
                    if continuity
                    else "The matched reconciliation is stale and has no Account Continuity confirmation."
                ),
                required_action_code="confirm_account_continuity",
                required_action_summary=(
                    "Confirm that no relevant financial events were omitted since the matched anchor, or reconcile again."
                ),
                level_cap="range_ready",
            )

        ordered_blockers = tuple(
            blocker for _, blocker in sorted(blockers, key=lambda item: (item[0], item[1].code))
        )
        level = self._level_for_blockers(ordered_blockers)
        required_actions = self._required_actions(ordered_blockers)

        material_facts = {
            "confirmed_ledger": confirmed_ledger,
            "latest_reconciliation": latest_reconciliation,
            "latest_full_match_id": latest_full_match["id"] if latest_full_match else None,
            "continuity": continuity,
            "pending_ledger": pending_ledger,
            "open_executions": open_executions,
            "active_broker_strategies": broker_strategies,
        }
        fact_fingerprint = digest(
            "portfolio-qualification-material-facts/v1",
            account_id,
            material_facts,
        )
        lineage = PortfolioQualificationFactLineage(
            confirmed_ledger_entry_ids=tuple(row["id"] for row in confirmed_ledger),
            confirmed_ledger_fingerprint=digest(
                "portfolio-qualification-confirmed-ledger/v1", confirmed_ledger
            ),
            reconciliation_id=(
                latest_reconciliation["id"] if latest_reconciliation else None
            ),
            reconciliation_fingerprint=(
                digest("portfolio-qualification-reconciliation/v1", latest_reconciliation)
                if latest_reconciliation
                else None
            ),
            continuity_confirmation_id=continuity["id"] if continuity else None,
            continuity_fingerprint=(
                digest("portfolio-qualification-continuity/v1", continuity)
                if continuity
                else None
            ),
            pending_ledger_entry_ids=tuple(row["id"] for row in pending_ledger),
            open_execution_ids=tuple(row["id"] for row in open_executions),
            active_broker_strategy_ids=tuple(
                row["id"] for row in broker_strategies
            ),
        )
        facts = PortfolioQualificationFacts(
            confirmed_ledger_entry_count=len(confirmed_ledger),
            latest_confirmed_ledger_at=(
                confirmed_ledger[-1]["occurred_at"] if confirmed_ledger else None
            ),
            latest_reconciliation_as_of=(
                latest_reconciliation["as_of"] if latest_reconciliation else None
            ),
            latest_reconciliation_status=(
                latest_reconciliation["status"] if latest_reconciliation else None
            ),
            latest_reconciliation_full_scope_matched=latest_is_full_match,
            full_scope_match_ever_established=latest_full_match is not None,
            reconciliation_age_seconds=reconciliation_age_seconds,
            reconciliation_stale=reconciliation_stale,
            reconciliation_uncertainty_unbounded=uncertainty_unbounded,
            reconciliation_difference_bounded=difference_bounded,
            pending_ledger_entry_count=len(pending_ledger),
            open_execution_count=len(open_executions),
            active_broker_strategy_count=len(broker_strategies),
            account_continuity_present=continuity is not None,
            account_continuity_valid=continuity_valid,
            continuity_confirmation_json=(
                canonical(
                    {
                        **continuity,
                        "reporting_commitment": bool(
                            continuity.get("reporting_commitment")
                        ),
                    }
                )
                if continuity
                else None
            ),
        )
        calculation_id = "pqual_" + digest(
            "portfolio-qualification-calculation/v1",
            POLICY_VERSION,
            account_id,
            effective_at,
            fact_fingerprint,
        )[:24]
        return PortfolioQualificationCalculation(
            calculation_id=calculation_id,
            account_id=account_id,
            as_of=effective_at,
            policy_version=POLICY_VERSION,
            level=level,
            allowed_uses=ALLOWED_USES[level],
            blockers=ordered_blockers,
            required_actions=required_actions,
            fact_lineage=lineage,
            facts=facts,
            material_fact_fingerprint=fact_fingerprint,
        )

    def evaluate_candidate(
        self,
        *,
        account_id: str,
        asset_id: str,
        quantity: Any,
        price: Any,
        price_range: dict[str, Any],
        market_snapshot_id: str | None,
        max_market_age_seconds: int,
        as_of: str,
        valid_until: str,
    ) -> PortfolioCandidateQualificationCalculation:
        """Qualify one bounded candidate without owning portfolio or market truth."""

        account_qualification = self.evaluate_account(
            account_id=account_id,
            as_of=as_of,
        )
        self.c.financial.asset_get(asset_id)
        effective_at = account_qualification.as_of
        cutoff = parse(effective_at)
        if (
            isinstance(max_market_age_seconds, bool)
            or not isinstance(max_market_age_seconds, int)
            or max_market_age_seconds <= 0
        ):
            raise CompanionError(
                "candidate max_market_age_seconds must be a positive integer"
            )
        if not isinstance(price_range, dict) or set(price_range) != {"min", "max"}:
            raise CompanionError("candidate price_range must contain exactly min and max")
        signed_quantity = dec(quantity, "candidate quantity")
        reference_price = dec(price, "candidate price")
        minimum_price = dec(price_range["min"], "candidate minimum price")
        maximum_price = dec(price_range["max"], "candidate maximum price")
        if signed_quantity == 0:
            raise CompanionError("candidate quantity must be non-zero")
        if reference_price <= 0 or minimum_price <= 0 or minimum_price > maximum_price:
            raise CompanionError("candidate price and price_range must be positive and ordered")

        candidate = PortfolioQualificationCandidate(
            account_id=account_id,
            asset_id=asset_id,
            direction="buy" if signed_quantity > 0 else "sell",
            quantity=dtext(abs(signed_quantity)),
            reference_price=dtext(reference_price),
            price_range=(dtext(minimum_price), dtext(maximum_price)),
            market_snapshot_id=market_snapshot_id,
            max_market_age_seconds=max_market_age_seconds,
            as_of=effective_at,
            valid_until=iso(parse(valid_until)),
        )
        with self.c.db.connect() as con:
            selected_market = (
                row_dict(
                    con.execute(
                        "SELECT * FROM market_snapshots WHERE id=?",
                        (market_snapshot_id,),
                    ).fetchone()
                )
                if market_snapshot_id
                else None
            )
            latest_market = row_dict(
                con.execute(
                    "SELECT * FROM market_snapshots WHERE asset_id=? AND metric='close' "
                    "AND julianday(observed_at)<=julianday(?) "
                    "ORDER BY julianday(observed_at) DESC,julianday(created_at) DESC,rowid DESC "
                    "LIMIT 1",
                    (asset_id, effective_at),
                ).fetchone()
            )

        market_blockers: list[PortfolioQualificationBlocker] = []

        def market_block(
            *,
            code: str,
            summary: str,
            required_action_code: str,
            required_action_summary: str,
        ) -> None:
            market_blockers.append(
                PortfolioQualificationBlocker(
                    code=code,
                    summary=summary,
                    required_action_code=required_action_code,
                    required_action_summary=required_action_summary,
                    level_cap="range_ready",
                )
            )

        age_seconds = None
        if not selected_market:
            market_block(
                code="market_snapshot_missing",
                summary="The candidate has no related frozen Market Snapshot.",
                required_action_code="refresh_market_snapshot",
                required_action_summary="Freeze a current healthy close-price Market Snapshot for this asset.",
            )
        else:
            if (
                selected_market["asset_id"] != asset_id
                or selected_market["metric"] != "close"
            ):
                market_block(
                    code="market_snapshot_mismatch",
                    summary="The selected Market Snapshot does not represent this asset's close price.",
                    required_action_code="select_matching_market_snapshot",
                    required_action_summary="Select a close-price Market Snapshot for the candidate asset.",
                )
            if selected_market["quality"] != "healthy":
                market_block(
                    code="market_snapshot_unhealthy",
                    summary="The selected Market Snapshot is not marked healthy.",
                    required_action_code="refresh_market_snapshot",
                    required_action_summary="Freeze a healthy Market Snapshot before exact sizing.",
                )
            observed_at = parse(selected_market["observed_at"])
            if observed_at > cutoff:
                market_block(
                    code="market_snapshot_from_future",
                    summary="The selected Market Snapshot was not available at the decision time.",
                    required_action_code="select_point_in_time_market_snapshot",
                    required_action_summary="Use a Market Snapshot observed no later than the decision time.",
                )
            else:
                age_seconds = max(0, int((cutoff - observed_at).total_seconds()))
                if age_seconds > max_market_age_seconds:
                    market_block(
                        code="market_snapshot_stale",
                        summary="The selected Market Snapshot exceeds the candidate freshness limit.",
                        required_action_code="refresh_market_snapshot",
                        required_action_summary="Freeze a newer healthy Market Snapshot before exact sizing.",
                    )
            if latest_market and latest_market["id"] != selected_market["id"]:
                market_block(
                    code="market_snapshot_superseded",
                    summary="A newer relevant Market Snapshot exists for the candidate asset.",
                    required_action_code="use_latest_market_snapshot",
                    required_action_summary="Recalculate the candidate with the latest relevant Market Snapshot.",
                )
            try:
                market_price = dec(
                    selected_market["value_text"], "candidate market snapshot price"
                )
            except CompanionError:
                market_price = None
            if market_price != reference_price:
                market_block(
                    code="market_price_mismatch",
                    summary="The candidate reference price does not match the frozen Market Snapshot.",
                    required_action_code="align_candidate_market_price",
                    required_action_summary="Recalculate the candidate using the frozen Market Snapshot price.",
                )

        market_fresh = not market_blockers
        market_evidence = PortfolioQualificationMarketEvidence(
            market_snapshot_id=(selected_market["id"] if selected_market else market_snapshot_id),
            latest_relevant_snapshot_id=(latest_market["id"] if latest_market else None),
            observed_at=(selected_market["observed_at"] if selected_market else None),
            age_seconds=age_seconds,
            max_age_seconds=max_market_age_seconds,
            fresh=market_fresh,
        )
        blockers = tuple((*account_qualification.blockers, *market_blockers))
        level = self._level_for_blockers(blockers)
        required_actions = self._required_actions(blockers)
        market_facts = {
            "selected_market": selected_market,
            "latest_relevant_market": latest_market,
        }
        market_fact_fingerprint = digest(
            "portfolio-qualification-candidate-market/v1",
            asset_id,
            market_facts,
        )
        material_fact_fingerprint = digest(
            "portfolio-qualification-candidate-material-facts/v1",
            account_qualification.material_fact_fingerprint,
            market_fact_fingerprint,
        )
        calculation_id = "pqual_candidate_" + digest(
            "portfolio-qualification-candidate-calculation/v1",
            POLICY_VERSION,
            {
                "account_id": candidate.account_id,
                "asset_id": candidate.asset_id,
                "direction": candidate.direction,
                "quantity": candidate.quantity,
                "reference_price": candidate.reference_price,
                "price_range": candidate.price_range,
                "market_snapshot_id": candidate.market_snapshot_id,
                "max_market_age_seconds": candidate.max_market_age_seconds,
                "as_of": candidate.as_of,
                "valid_until": candidate.valid_until,
            },
            material_fact_fingerprint,
        )[:24]
        return PortfolioCandidateQualificationCalculation(
            calculation_id=calculation_id,
            account_qualification=account_qualification,
            candidate=candidate,
            market_evidence=market_evidence,
            policy_version=POLICY_VERSION,
            level=level,
            allowed_uses=ALLOWED_USES[level],
            blockers=blockers,
            required_actions=required_actions,
            market_fact_fingerprint=market_fact_fingerprint,
            material_fact_fingerprint=material_fact_fingerprint,
        )

    def candidate_is_current(
        self,
        calculation: PortfolioCandidateQualificationCalculation,
        *,
        as_of: str,
    ) -> bool:
        """Return whether an immutable candidate still supports its recorded uses."""

        effective_at = iso(parse(as_of))
        if parse(effective_at) < parse(calculation.as_of):
            return False
        if parse(effective_at) >= parse(calculation.candidate.valid_until):
            return False
        signed_quantity = (
            calculation.candidate.quantity
            if calculation.candidate.direction == "buy"
            else f"-{calculation.candidate.quantity}"
        )
        current = self.evaluate_candidate(
            account_id=calculation.candidate.account_id,
            asset_id=calculation.candidate.asset_id,
            quantity=signed_quantity,
            price=calculation.candidate.reference_price,
            price_range={
                "min": calculation.candidate.price_range[0],
                "max": calculation.candidate.price_range[1],
            },
            market_snapshot_id=calculation.candidate.market_snapshot_id,
            max_market_age_seconds=calculation.candidate.max_market_age_seconds,
            as_of=effective_at,
            valid_until=calculation.candidate.valid_until,
        )
        return bool(
            current.material_fact_fingerprint
            == calculation.material_fact_fingerprint
            and current.level == calculation.level
        )

    @staticmethod
    def _level_for_blockers(
        blockers: tuple[PortfolioQualificationBlocker, ...],
    ) -> str:
        level = "preflight_ready"
        for blocker in blockers:
            if LEVEL_RANK[blocker.level_cap] < LEVEL_RANK[level]:
                level = blocker.level_cap
        return level

    @staticmethod
    def _required_actions(
        blockers: tuple[PortfolioQualificationBlocker, ...],
    ) -> tuple[tuple[str, str], ...]:
        required_actions: list[tuple[str, str]] = []
        for blocker in blockers:
            action = (
                blocker.required_action_code,
                blocker.required_action_summary,
            )
            if action not in required_actions:
                required_actions.append(action)
        required_actions.append(
            (
                "broker_preflight_required",
                "Verify available cash, holdings, open orders, and broker constraints in the broker App before submission.",
            )
        )
        return tuple(required_actions)

    @staticmethod
    def _execution_account_id(execution: dict[str, Any]) -> str | None:
        details = execution.get("details")
        if not isinstance(details, dict):
            return None
        action = details.get("action")
        if isinstance(action, dict) and isinstance(action.get("account_id"), str):
            return action["account_id"]
        account_id = details.get("account_id")
        return account_id if isinstance(account_id, str) else None
