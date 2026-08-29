from __future__ import annotations

from dataclasses import FrozenInstanceError
from datetime import timedelta

import pytest

from companion.core import Companion
from companion.timeutil import iso, utc_now


def create_account_with_ledger(
    companion: Companion, *, name: str = "Primary", amount: str = "1000"
) -> dict:
    account = companion.financial.account_create(name, "CNY")
    opening = companion.financial.ledger_add(
        account_id=account["id"],
        entry_type="opening_balance",
        occurred_at=iso(utc_now() - timedelta(days=10)),
        amount=amount,
        currency="CNY",
        source="portfolio-qualification-fixture",
    )
    companion.financial.ledger_confirm(opening["id"])
    return account


def reconcile_cash(
    companion: Companion,
    account_id: str,
    *,
    as_of: str,
    statement_amount: str = "1000",
    full_scope: bool = True,
) -> dict:
    statement = {
        "cash": {"CNY": statement_amount},
        "positions": {},
    }
    if full_scope:
        statement.update(
            {
                "position_values": {},
                "position_total_by_currency": {"CNY": "0"},
                "total_by_currency": {"CNY": statement_amount},
            }
        )
    return companion.financial.reconcile(
        account_id,
        as_of,
        statement,
        "portfolio-qualification-fixture-statement",
    )


def reason_codes(calculation) -> list[str]:
    return [blocker.code for blocker in calculation.blockers]


def truth_table_counts(companion: Companion) -> dict[str, int]:
    tables = (
        "ledger_entries",
        "reconciliations",
        "account_continuity_confirmations",
        "executions",
        "broker_execution_plans",
        "calculations",
    )
    with companion.db.connect() as con:
        return {
            table: int(con.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
            for table in tables
        }


def a_share_reality() -> dict:
    return {
        "version": "a-share-reality/v1",
        "currency": "CNY",
        "lot_size": 100,
        "t_plus_one": True,
        "signal_delay": "next_session",
        "commission_rate": "0.0003",
        "minimum_commission": "5",
        "sell_stamp_duty_rate": "0.0005",
        "cash_dividend_tax_rate": "0",
        "slippage_bps": "0",
        "money_quantum": "0.01",
        "price_tick": "0.01",
    }


def test_account_qualification_matrix_is_ordered_deterministic_and_read_only(tmp_path):
    companion = Companion(tmp_path, gate_scope="test_fixture")
    companion.initialize()
    checked_at = iso()

    empty = companion.financial.account_create("No confirmed Ledger", "CNY")
    companion.financial.ledger_add(
        account_id=empty["id"],
        entry_type="opening_balance",
        occurred_at=checked_at,
        amount="1000",
        currency="CNY",
        source="unconfirmed-fixture",
    )
    before = truth_table_counts(companion)
    unavailable = companion.portfolio_qualification.evaluate_account(
        account_id=empty["id"], as_of=checked_at
    )
    assert unavailable.level == "unavailable"
    assert reason_codes(unavailable) == [
        "confirmed_ledger_unavailable",
        "pending_ledger_entries",
        "full_scope_match_never_established",
    ]
    assert truth_table_counts(companion) == before

    never_matched_account = create_account_with_ledger(
        companion, name="Never matched"
    )
    never_matched = companion.portfolio_qualification.evaluate_account(
        account_id=never_matched_account["id"], as_of=checked_at
    )
    assert never_matched.level == "range_ready"
    assert reason_codes(never_matched) == ["full_scope_match_never_established"]

    unbounded_account = create_account_with_ledger(
        companion, name="Unbounded difference"
    )
    reconcile_cash(
        companion,
        unbounded_account["id"],
        as_of=checked_at,
        full_scope=False,
    )
    unbounded = companion.portfolio_qualification.evaluate_account(
        account_id=unbounded_account["id"], as_of=checked_at
    )
    assert unbounded.level == "directional_only"
    assert reason_codes(unbounded) == ["reconciliation_uncertainty_unbounded"]

    bounded_account = create_account_with_ledger(
        companion, name="Bounded difference"
    )
    reconcile_cash(
        companion,
        bounded_account["id"],
        as_of=checked_at,
        statement_amount="900",
    )
    bounded = companion.portfolio_qualification.evaluate_account(
        account_id=bounded_account["id"], as_of=checked_at
    )
    assert bounded.level == "range_ready"
    assert reason_codes(bounded) == ["reconciliation_difference_bounded"]

    recent_account = create_account_with_ledger(
        companion, name="Recently reconciled"
    )
    reconcile_cash(companion, recent_account["id"], as_of=checked_at)
    before = truth_table_counts(companion)
    first = companion.portfolio_qualification.evaluate_account(
        account_id=recent_account["id"], as_of=checked_at
    )
    second = companion.portfolio_qualification.evaluate_account(
        account_id=recent_account["id"], as_of=checked_at
    )
    assert first == second
    assert first.level == "preflight_ready"
    assert first.blockers == ()
    assert first.calculation_id == second.calculation_id
    assert first.material_fact_fingerprint == second.material_fact_fingerprint
    assert first.account_id == recent_account["id"]
    assert first.policy_version == "portfolio-qualification-policy/v1"
    assert first.fact_lineage.confirmed_ledger_entry_ids
    assert first.fact_lineage.reconciliation_id
    assert truth_table_counts(companion) == before
    with pytest.raises(FrozenInstanceError):
        first.level = "range_ready"

    companion.financial.ledger_add(
        account_id=recent_account["id"],
        entry_type="fee",
        occurred_at=checked_at,
        amount="-1",
        currency="CNY",
        source="pending-material-drift-fixture",
    )
    drifted = companion.portfolio_qualification.evaluate_account(
        account_id=recent_account["id"], as_of=checked_at
    )
    assert drifted.level == "range_ready"
    assert reason_codes(drifted) == ["pending_ledger_entries"]
    assert drifted.calculation_id != first.calculation_id
    assert drifted.material_fact_fingerprint != first.material_fact_fingerprint


def test_stale_reconciliation_requires_valid_account_continuity(tmp_path):
    companion = Companion(tmp_path, gate_scope="test_fixture")
    companion.initialize()
    account = create_account_with_ledger(companion)
    old_as_of = iso(utc_now() - timedelta(days=5))
    reconciliation = reconcile_cash(companion, account["id"], as_of=old_as_of)
    checked_at = iso()

    stale = companion.portfolio_qualification.evaluate_account(
        account_id=account["id"], as_of=checked_at
    )
    assert stale.level == "range_ready"
    assert reason_codes(stale) == ["account_continuity_missing"]

    continuity = companion.financial.continuity_confirm(
        account_id=account["id"],
        confirmed_at=checked_at,
        user_confirmation_ref="user:test:portfolio-qualification-continuity",
        reporting_commitment=True,
    )
    supported = companion.portfolio_qualification.evaluate_account(
        account_id=account["id"], as_of=checked_at
    )
    assert supported.level == "preflight_ready"
    assert supported.blockers == ()
    assert supported.facts.account_continuity_valid is True
    assert supported.facts.broker_realtime_proven is False
    assert supported.fact_lineage.continuity_confirmation_id == continuity["id"]
    assert supported.fact_lineage.reconciliation_id == reconciliation["id"]

    companion.financial.continuity_revoke(
        confirmation_id=continuity["id"], reason="fixture omitted activity"
    )
    revoked = companion.portfolio_qualification.evaluate_account(
        account_id=account["id"], as_of=checked_at
    )
    assert revoked.level == "range_ready"
    assert reason_codes(revoked) == ["account_continuity_invalid"]


@pytest.mark.parametrize("execution_status", ["accepted", "ordered", "partially_filled"])
def test_open_execution_states_are_account_scoped(
    tmp_path, execution_status: str
):
    companion = Companion(tmp_path, gate_scope="test_fixture")
    companion.initialize()
    account = create_account_with_ledger(companion, name="Target")
    other = create_account_with_ledger(companion, name="Other")
    checked_at = iso()
    reconcile_cash(companion, account["id"], as_of=checked_at)
    reconcile_cash(companion, other["id"], as_of=checked_at)
    execution = companion.cognition.execution_create(
        None,
        {
            "action": {
                "account_id": other["id"],
                "asset_id": "fixture:asset",
                "side": "buy",
                "quantity": "1",
                "price_range": {"min": "1", "max": "1"},
            }
        },
    )
    with companion.db.transaction() as con:
        con.execute(
            "UPDATE executions SET status=? WHERE id=?",
            (execution_status, execution["id"]),
        )

    target_result = companion.portfolio_qualification.evaluate_account(
        account_id=account["id"], as_of=checked_at
    )
    other_result = companion.portfolio_qualification.evaluate_account(
        account_id=other["id"], as_of=checked_at
    )

    assert target_result.level == "preflight_ready"
    assert target_result.facts.open_execution_count == 0
    assert other_result.level == "range_ready"
    assert other_result.facts.open_execution_count == 1
    assert reason_codes(other_result) == ["open_executions"]


def test_portfolio_context_exposes_detailed_projection_and_derives_legacy_fields(tmp_path):
    companion = Companion(tmp_path, gate_scope="test_fixture")
    companion.initialize()
    account = create_account_with_ledger(companion)
    checked_at = iso()
    reconcile_cash(companion, account["id"], as_of=checked_at)

    calculation = companion.portfolio_qualification.evaluate_account(
        account_id=account["id"], as_of=checked_at
    )
    context = companion.investment.portfolio_context(
        account_id=account["id"], as_of=checked_at
    )

    qualification = context["portfolio_qualification"]
    assert qualification["calculation_id"] == calculation.calculation_id
    assert qualification["level"] == "preflight_ready"
    assert qualification["allowed_uses"][-1] == "precise_decision_support"
    assert qualification["blockers"] == []
    assert qualification["reason_codes"] == []
    assert qualification["required_actions"] == [
        {
            "code": "broker_preflight_required",
            "summary": "Verify available cash, holdings, open orders, and broker constraints in the broker App before submission.",
        }
    ]
    assert qualification["as_of"] == checked_at
    assert qualification["validity"]["broker_realtime_proven"] is False
    assert qualification["account_id"] == account["id"]
    assert qualification["policy_version"] == calculation.policy_version
    assert qualification["facts"]["confirmed_ledger_entry_count"] == 1
    assert context["truth_freshness"] == calculation.legacy_truth_freshness()
    assert context["precision_boundary"] == calculation.legacy_precision_boundary()


def test_home_exposes_compact_projection_for_its_single_account(tmp_path):
    companion = Companion(tmp_path, gate_scope="test_fixture")
    companion.initialize()
    account = create_account_with_ledger(companion)
    reconcile_cash(companion, account["id"], as_of=iso())

    home = companion.investment.home()
    qualification = home["portfolio_qualification"]
    expected = companion.portfolio_qualification.evaluate_account(
        account_id=account["id"], as_of=home["as_of"]
    )

    assert qualification == expected.stable_projection(compact=True)
    assert set(qualification) == {
        "calculation_id",
        "level",
        "allowed_uses",
        "blockers",
        "reason_codes",
        "required_actions",
        "as_of",
        "validity",
    }


def test_candidate_qualification_combines_account_and_market_evidence_without_writing(
    tmp_path,
):
    companion = Companion(tmp_path, gate_scope="test_fixture")
    companion.initialize()
    checked_at = iso()
    valid_until = iso(utc_now() + timedelta(days=7))
    account = create_account_with_ledger(companion)
    reconcile_cash(companion, account["id"], as_of=checked_at)
    asset = companion.financial.asset_upsert(
        "stock", "Candidate Asset", "CNY", {"fixture": "candidate"}
    )
    market = companion.financial.market_add(
        asset["id"],
        "close",
        "10",
        checked_at,
        "portfolio-qualification-candidate-fixture",
        "healthy",
        "CNY",
    )
    before = truth_table_counts(companion)

    calculation = companion.portfolio_qualification.evaluate_candidate(
        account_id=account["id"],
        asset_id=asset["id"],
        quantity="100",
        price="10",
        price_range={"min": "9.8", "max": "10.2"},
        market_snapshot_id=market["id"],
        max_market_age_seconds=3600,
        as_of=checked_at,
        valid_until=valid_until,
    )

    assert truth_table_counts(companion) == before
    assert calculation.level == "preflight_ready"
    assert calculation.account_qualification.level == "preflight_ready"
    assert calculation.candidate.account_id == account["id"]
    assert calculation.candidate.asset_id == asset["id"]
    assert calculation.candidate.direction == "buy"
    assert calculation.candidate.quantity == "100"
    assert calculation.candidate.reference_price == "10"
    assert calculation.candidate.price_range == ("9.8", "10.2")
    assert calculation.candidate.valid_until == valid_until
    assert calculation.market_evidence.market_snapshot_id == market["id"]
    assert calculation.market_evidence.fresh is True
    assert calculation.market_evidence.latest_relevant_snapshot_id == market["id"]
    assert calculation.market_fact_fingerprint

    projection = calculation.stable_projection()
    assert projection["account_calculation_id"] == (
        calculation.account_qualification.calculation_id
    )
    assert projection["candidate"] == {
        "account_id": account["id"],
        "asset_id": asset["id"],
        "direction": "buy",
        "quantity": "100",
        "quantity_kind": "exact_candidate",
        "reference_price": "10",
        "price_range": {"min": "9.8", "max": "10.2"},
        "valid_until": valid_until,
    }
    assert projection["market_evidence"]["market_snapshot_id"] == market["id"]
    assert projection["market_evidence"]["fresh"] is True
    assert projection["validity"]["valid_until"] == valid_until
    assert "related_market_snapshot_change" in projection["validity"][
        "recalculate_on"
    ]
    assert projection["no_action_inferred"] is False


def test_candidate_market_gaps_cap_precision_and_directional_level_withholds_quantity(
    tmp_path,
):
    companion = Companion(tmp_path, gate_scope="test_fixture")
    companion.initialize()
    checked_at = iso()
    valid_until = iso(utc_now() + timedelta(days=7))
    account = create_account_with_ledger(companion)
    reconcile_cash(companion, account["id"], as_of=checked_at)
    asset = companion.financial.asset_upsert(
        "stock", "Market Gap Asset", "CNY", {"fixture": "market-gap"}
    )

    missing = companion.portfolio_qualification.evaluate_candidate(
        account_id=account["id"],
        asset_id=asset["id"],
        quantity="100",
        price="10",
        price_range={"min": "9", "max": "11"},
        market_snapshot_id="mkt_missing_fixture",
        max_market_age_seconds=60,
        as_of=checked_at,
        valid_until=valid_until,
    )
    assert missing.level == "range_ready"
    assert reason_codes(missing) == ["market_snapshot_missing"]
    assert missing.market_evidence.fresh is False

    stale_market = companion.financial.market_add(
        asset["id"],
        "close",
        "10",
        iso(utc_now() - timedelta(hours=2)),
        "portfolio-qualification-stale-market-fixture",
        "healthy",
        "CNY",
    )
    stale = companion.portfolio_qualification.evaluate_candidate(
        account_id=account["id"],
        asset_id=asset["id"],
        quantity="100",
        price="10",
        price_range={"min": "9", "max": "11"},
        market_snapshot_id=stale_market["id"],
        max_market_age_seconds=60,
        as_of=checked_at,
        valid_until=valid_until,
    )
    assert stale.level == "range_ready"
    assert reason_codes(stale) == ["market_snapshot_stale"]

    directional_account = create_account_with_ledger(
        companion, name="Directional candidate"
    )
    reconcile_cash(
        companion,
        directional_account["id"],
        as_of=checked_at,
        full_scope=False,
    )
    fresh_market = companion.financial.market_add(
        asset["id"],
        "close",
        "11",
        checked_at,
        "portfolio-qualification-directional-market-fixture",
        "healthy",
        "CNY",
    )
    directional = companion.portfolio_qualification.evaluate_candidate(
        account_id=directional_account["id"],
        asset_id=asset["id"],
        quantity="-100",
        price="11",
        price_range={"min": "10", "max": "12"},
        market_snapshot_id=fresh_market["id"],
        max_market_age_seconds=60,
        as_of=checked_at,
        valid_until=valid_until,
    )
    assert directional.level == "directional_only"
    assert reason_codes(directional) == ["reconciliation_uncertainty_unbounded"]
    assert directional.stable_projection()["candidate"]["quantity"] is None
    assert "quantity_ranges" not in directional.allowed_uses


def test_candidate_calculation_is_immutable_and_material_drift_requires_recalculation(
    tmp_path,
):
    companion = Companion(tmp_path, gate_scope="test_fixture")
    companion.initialize()
    checked_at = iso()
    valid_until = iso(utc_now() + timedelta(days=7))
    account = create_account_with_ledger(companion)
    reconcile_cash(companion, account["id"], as_of=checked_at)
    asset = companion.financial.asset_upsert(
        "stock", "Drift Asset", "CNY", {"fixture": "candidate-drift"}
    )
    market = companion.financial.market_add(
        asset["id"],
        "close",
        "10",
        checked_at,
        "portfolio-qualification-drift-market-fixture",
        "healthy",
        "CNY",
    )
    candidate = {
        "account_id": account["id"],
        "asset_id": asset["id"],
        "quantity": "100",
        "price": "10",
        "price_range": {"min": "9", "max": "11"},
        "market_snapshot_id": market["id"],
        "max_market_age_seconds": 3600,
        "as_of": checked_at,
        "valid_until": valid_until,
    }
    original = companion.portfolio_qualification.evaluate_candidate(**candidate)
    frozen_projection = original.stable_projection()
    current_at = iso(utc_now() + timedelta(minutes=1))
    assert companion.portfolio_qualification.candidate_is_current(
        original, as_of=current_at
    ) is True

    newer_market = companion.financial.market_add(
        asset["id"],
        "close",
        "10.1",
        current_at,
        "portfolio-qualification-new-market-fixture",
        "healthy",
        "CNY",
    )
    assert companion.portfolio_qualification.candidate_is_current(
        original, as_of=current_at
    ) is False
    refreshed = companion.portfolio_qualification.evaluate_candidate(
        **{
            **candidate,
            "as_of": current_at,
            "price": "10.1",
            "market_snapshot_id": newer_market["id"],
        }
    )
    assert refreshed.level == "preflight_ready"
    assert refreshed.calculation_id != original.calculation_id
    assert refreshed.market_fact_fingerprint != original.market_fact_fingerprint

    companion.financial.ledger_add(
        account_id=account["id"],
        entry_type="fee",
        occurred_at=current_at,
        amount="-1",
        currency="CNY",
        source="portfolio-qualification-candidate-drift-fixture",
    )
    assert companion.portfolio_qualification.candidate_is_current(
        refreshed, as_of=current_at
    ) is False
    assert original.stable_projection() == frozen_projection
    with pytest.raises(FrozenInstanceError):
        original.level = "range_ready"


def test_action_plan_projects_candidate_qualification_and_derives_legacy_eligibility(
    tmp_path,
):
    companion = Companion(tmp_path, gate_scope="test_fixture")
    companion.initialize()
    checked_at = iso()
    valid_until = iso(utc_now() + timedelta(days=7))
    mandate = companion.cognition.context_create(
        "mandate",
        {"hard_constraints": {}},
        reason="candidate qualification fixture",
    )
    companion.cognition.context_confirm(mandate["id"])
    account = create_account_with_ledger(companion, amount="100000")
    reconcile_cash(
        companion,
        account["id"],
        as_of=checked_at,
        statement_amount="100000",
    )
    asset = companion.financial.asset_upsert(
        "stock", "Action Plan Asset", "CNY", {"fixture": "action-plan"}
    )
    market = companion.financial.market_add(
        asset["id"],
        "close",
        "10",
        checked_at,
        "portfolio-qualification-action-plan-fixture",
        "healthy",
        "CNY",
    )
    trade = {
        "action_tier": "standard",
        "account_id": account["id"],
        "asset_id": asset["id"],
        "quantity": "100",
        "price": "10",
        "price_range": {"min": "9", "max": "11"},
        "market_snapshot_id": market["id"],
        "max_market_age_seconds": 3600,
        "reality_spec": a_share_reality(),
        "as_of": checked_at,
        "valid_until": valid_until,
    }

    plan = companion.investment_commands.action_plan(**trade)
    expected = companion.portfolio_qualification.evaluate_candidate(
        **{
            key: trade[key]
            for key in (
                "account_id",
                "asset_id",
                "quantity",
                "price",
                "price_range",
                "market_snapshot_id",
                "max_market_age_seconds",
                "as_of",
                "valid_until",
            )
        }
    )
    assert plan["candidate_qualification"] == expected.stable_projection()
    assert plan["candidate_qualification"]["level"] == "preflight_ready"
    assert plan["risk"]["status"] == "pass"
    assert plan["eligible_for_decision"] is True
    assert plan["conditional_sizing_available"] is True
    assert plan["decision_blockers"] == []
    assert plan["action"]["quantity"] == "100"
    assert plan["action"]["quantity_status"] == (
        "finalizable_after_broker_preflight"
    )

    missing_market = companion.investment_commands.action_plan(
        **{**trade, "market_snapshot_id": "mkt_missing_action_plan_fixture"}
    )
    assert missing_market["candidate_qualification"]["level"] == "range_ready"
    assert missing_market["candidate_qualification"]["reason_codes"] == [
        "market_snapshot_missing"
    ]
    assert missing_market["risk"]["status"] == "blocked"
    assert missing_market["eligible_for_decision"] is False
    assert missing_market["conditional_sizing_available"] is True
    assert missing_market["decision_blockers"] == [
        "risk_gate",
        "portfolio_qualification",
    ]
    assert missing_market["candidate_qualification"]["no_action_inferred"] is False

    directional_account = create_account_with_ledger(
        companion, name="Directional Action Plan", amount="100000"
    )
    reconcile_cash(
        companion,
        directional_account["id"],
        as_of=checked_at,
        statement_amount="100000",
        full_scope=False,
    )
    directional = companion.investment_commands.action_plan(
        **{**trade, "account_id": directional_account["id"]}
    )
    assert directional["candidate_qualification"]["level"] == "directional_only"
    assert directional["eligible_for_decision"] is False
    assert directional["conditional_sizing_available"] is False
    assert directional["action"]["quantity"] is None
    assert directional["action"]["quantity_status"] == "withheld_by_qualification"
    assert directional["decision_blockers"] == ["portfolio_qualification"]
