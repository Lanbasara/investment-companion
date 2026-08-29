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
