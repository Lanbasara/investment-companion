from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import timedelta
from threading import Barrier

import pytest

from companion.core import Companion, CompanionError
from companion.governance import GATE_CHECKLISTS
from companion.timeutil import iso, utc_now


def reality() -> dict:
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


def setup_cash_blocked_candidate(tmp_path):
    companion = Companion(tmp_path, gate_scope="test_fixture")
    companion.initialize()
    checked_time = utc_now()
    checked_at = iso(checked_time)
    valid_until = iso(checked_time + timedelta(days=1))
    mandate = companion.cognition.context_create(
        "mandate",
        {"hard_constraints": {"minimum_cash": {"CNY": "100"}}},
        reason="Funding Condition fixture",
    )
    companion.cognition.context_confirm(mandate["id"])
    account = companion.financial.account_create("Funding account", "CNY")
    opening = companion.financial.ledger_add(
        account_id=account["id"],
        entry_type="opening_balance",
        occurred_at=iso(checked_time - timedelta(days=2)),
        amount="1500",
        currency="CNY",
        source="funding-condition-fixture",
    )
    companion.financial.ledger_confirm(opening["id"])
    companion.financial.reconcile(
        account["id"],
        checked_at,
        {
            "cash": {"CNY": "1500"},
            "positions": {},
            "position_values": {},
            "position_total_by_currency": {"CNY": "0"},
            "total_by_currency": {"CNY": "1500"},
        },
        "funding-condition-statement",
    )
    asset = companion.financial.asset_upsert(
        "stock", "Funding Asset", "CNY", {"fixture": "funding-condition"}
    )
    market = companion.financial.market_add(
        asset["id"],
        "close",
        "8",
        checked_at,
        "funding-condition-fixture",
        "healthy",
        "CNY",
    )
    trade = {
        "action_tier": "standard",
        "as_of": checked_at,
        "account_id": account["id"],
        "asset_id": asset["id"],
        "quantity": "200",
        "price": "8",
        "reality_spec": reality(),
        "market_snapshot_id": market["id"],
        "max_market_age_seconds": 3600,
        "valid_until": valid_until,
        "price_range": {"min": "7", "max": "9"},
    }
    return companion, checked_time, account, asset, trade


def paths_by_type(condition: dict) -> dict[str, dict]:
    return {path["type"]: path for path in condition["paths"]}


def ledger_ids(companion: Companion) -> list[str]:
    return [item["id"] for item in companion.financial.ledger_list(limit=500)]


def setup_qualified_funding_opportunity(tmp_path):
    companion, checked_time, account, asset, trade = setup_cash_blocked_candidate(
        tmp_path
    )
    artifact = companion.data.manifest_publish(
        kind="fixture_evidence",
        schema_version="fixture/v1",
        manifest={"gate": "G0", "suite": "opportunity-funding-condition"},
    )
    evidence = companion.gates.evidence_publish(
        "G0",
        checks={key: True for key in GATE_CHECKLISTS["G0"]},
        artifacts=[artifact["id"]],
        unknowns=[],
        counterevidence=[],
        counterevidence_disposition={},
        code_version="test-fixture",
        scope="test_fixture",
    )
    companion.gates.assessment_record(
        gate="G0",
        status="go",
        evidence_manifest_id=evidence["id"],
        code_version="test-fixture",
        assessed_by="pytest",
        scope="test_fixture",
    )
    companion.jobs.feature_set(
        "v5_operating_system", True, reason="Opportunity Funding Condition fixture"
    )
    investor = companion.cognition.context_create(
        "investor",
        {"goals": ["test an auditable funding condition"]},
        reason="Opportunity Funding Condition fixture",
    )
    investor = companion.cognition.context_confirm(investor["id"])
    attention = companion.cognition.context_create(
        "attention",
        {"timezone": "UTC", "daily_notification_budget": 5},
        reason="Opportunity Funding Condition fixture",
    )
    attention = companion.cognition.context_confirm(attention["id"])
    mandate = companion.cognition.context_current("mandate")
    program = companion.operating.program_create(
        name="Funding Condition test program",
        content={
            "objective": "Test a qualified funding-dependent candidate",
            "success_criteria": ["Funding history remains auditable"],
            "benchmark": {"name": "cash"},
            "risk_budget": {"single_position_weight": "1"},
            "universe": {"asset_ids": [asset["id"]]},
            "horizons": {"research": "one week"},
            "operating_cadence": {"daily": "exceptions"},
            "stop_conditions": ["Funding Condition expires"],
            "account_ids": [account["id"]],
        },
        context_refs={
            "investor_revision_id": investor["id"],
            "mandate_revision_id": mandate["id"],
            "attention_revision_id": attention["id"],
        },
        reason="Opportunity Funding Condition fixture",
    )
    companion.operating.program_confirm(
        program["revisions"][0]["id"],
        user_approval_ref="pytest:funding-condition-program",
    )
    manifests = [
        companion.data.manifest_publish(
            kind="investment_evidence",
            schema_version="research-evidence/v1",
            manifest={
                "asset_id": asset["id"],
                "source": source,
                "source_group": group,
                "first_known_at": trade["as_of"],
                "observed_at": trade["as_of"],
            },
        )
        for source, group in (
            ("issuer filing", "issuer"),
            ("exchange notice", "exchange"),
        )
    ]
    research = companion.investment_commands.research_publish(
        subject={"asset_id": asset["id"]},
        content="# Funding candidate\n\nA bounded, falsifiable fixture Thesis.",
        evidence_manifest_ids=[item["id"] for item in manifests],
        knowledge_cutoff=trade["as_of"],
        validation_spec={
            "falsifiers": ["the candidate no longer merits research"],
            "counterevidence": {
                "searched": ["issuer", "exchange"],
                "findings": [],
            },
            "applicability": {
                "horizon": "one week",
                "conditions": ["normal liquidity"],
                "excluded_conditions": ["suspension"],
            },
            "cost_assumptions": {
                "commission": "RealitySpec",
                "tax": "RealitySpec",
                "slippage": "price range",
            },
            "max_evidence_age_days": 30,
        },
    )
    validation_id = research["validation"]["calculation_id"]
    opportunity = companion.investment_commands.opportunity_update(
        operation="create",
        subject={"account_id": account["id"], "asset_id": asset["id"]},
        evidence_refs=[manifests[0]["id"]],
        reason="Funding-dependent candidate",
        thesis_id=research["thesis"]["id"],
    )
    opportunity = companion.investment_commands.opportunity_update(
        operation="transition",
        opportunity_id=opportunity["id"],
        expected_version=opportunity["version"],
        to_stage="researching",
        to_status="active",
        evidence_refs=[item["id"] for item in manifests],
        reason="Research the funding-dependent candidate",
    )
    opportunity = companion.investment_commands.opportunity_update(
        operation="transition",
        opportunity_id=opportunity["id"],
        expected_version=opportunity["version"],
        to_stage="qualified",
        to_status="active",
        evidence_refs=[*[item["id"] for item in manifests], validation_id],
        qualification={
            "validation_calculation_id": validation_id,
            "major_unknowns": ["Current confirmed cash is insufficient"],
            "decision_basis": "Research is qualified; funding is not current cash.",
        },
        reason="Research Validation qualifies continued evaluation",
    )
    return companion, checked_time, account, asset, trade, opportunity


def side_effect_counts(companion: Companion) -> dict[str, int]:
    with companion.db.connect() as con:
        return {
            table: con.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            for table in (
                "cognitive_objects",
                "decision_queue_items",
                "executions",
                "ledger_entries",
            )
        }


def test_action_plan_records_funding_range_costs_buffer_and_paths_without_ledger_writes(
    tmp_path,
):
    companion, _checked_time, account, asset, trade = setup_cash_blocked_candidate(
        tmp_path
    )
    before_ledger = ledger_ids(companion)

    plan = companion.investment_commands.action_plan(**trade)

    assert ledger_ids(companion) == before_ledger
    assert plan["risk"]["status"] == "blocked"
    assert {item["rule"] for item in plan["risk"]["violations"]} >= {
        "nonnegative_cash",
        "minimum_cash",
    }
    assert plan["eligible_for_decision"] is False
    assert plan["automatic_decision_or_execution"] is False
    assert plan["candidate_qualification"]["level"] == "preflight_ready"

    condition = plan["funding_condition"]
    assert condition is not None
    assert condition["schema"] == "investment-companion.funding-condition/v1"
    assert condition["account_id"] == account["id"]
    assert condition["asset_id"] == asset["id"]
    assert condition["direction"] == "buy"
    assert condition["candidate_qualification_calculation_id"] == plan[
        "candidate_qualification"
    ]["calculation_id"]
    assert condition["risk_calculation_id"] == plan["risk"]["calculation_id"]
    assert condition["candidate_quantity_domain"] == {
        "kind": "up_to_requested_quantity",
        "minimum": "0",
        "maximum": "200",
        "step": "100",
    }
    assert condition["price_range"] == {
        "minimum": "7",
        "reference": "8",
        "maximum": "9",
    }
    assert condition["confirmed_cash"]["amount"] == "1500"
    assert condition["confirmed_cash"]["cash_safety_buffer"] == "100"
    assert condition["confirmed_cash"]["spendable_amount"] == "1400"
    assert condition["cost_range"]["notional"] == {
        "minimum": "1400",
        "at_reference_price": "1600",
        "maximum": "1800",
    }
    assert condition["cost_range"]["commission"] == {
        "minimum": "5",
        "at_reference_price": "5",
        "maximum": "5",
    }
    assert condition["cost_range"]["tax"] == {
        "minimum": "0",
        "at_reference_price": "0",
        "maximum": "0",
    }
    assert condition["required_additional_cash"] == {
        "minimum": "5",
        "at_reference_price": "205",
        "maximum": "405",
    }

    paths = paths_by_type(condition)
    assert set(paths) == {
        "additional_funding",
        "reduce_quantity",
        "confirmed_disposal_proceeds",
    }
    assert paths["additional_funding"]["amount_range"] == condition[
        "required_additional_cash"
    ]
    assert paths["reduce_quantity"]["state"] == (
        "available_as_non_actionable_alternative"
    )
    assert paths["reduce_quantity"]["quantity_range"] == {
        "minimum": "100",
        "maximum_across_price_range": "100",
        "maximum_at_minimum_price": "100",
        "maximum_at_reference_price": "100",
        "maximum_at_maximum_price": "100",
        "step": "100",
    }
    assert paths["confirmed_disposal_proceeds"][
        "required_net_proceeds_range"
    ] == condition["required_additional_cash"]
    for path in paths.values():
        assert path["assumptions"]["confirmed_cash_only"] is True
        assert path["assumptions"]["planned_deposits_counted_as_cash"] is False
        assert path["confirmation_requirements"]
        assert path["validity"]["supports_current_planning"] is True
        assert [item["code"] for item in path["required_reruns"]] == [
            "rerun_portfolio_qualification",
            "rerun_risk_gate",
            "rerun_action_plan",
        ]
        assert path["validity"]["valid_until"] == trade["valid_until"]
    assert condition["automatic_decision_or_execution"] is False

    frozen = companion.financial.calculation_get(condition["calculation_id"])
    assert frozen["kind"] == "funding_condition"
    assert frozen["outputs"] == {
        key: value for key, value in condition.items() if key != "calculation_id"
    }
    assert frozen["inputs"]["confirmed_portfolio_calculation_id"] == condition[
        "confirmed_cash"
    ]["portfolio_calculation_id"]
    repeated = companion.investment_commands.action_plan(**trade)
    assert repeated["funding_condition"]["calculation_id"] == condition[
        "calculation_id"
    ]
    assert ledger_ids(companion) == before_ledger


def test_planned_deposit_and_expected_sale_do_not_fund_risk_until_ledger_confirmation(
    tmp_path,
):
    companion, checked_time, account, asset, trade = setup_cash_blocked_candidate(
        tmp_path
    )
    initial = companion.investment_commands.action_plan(**trade)
    condition = initial["funding_condition"]
    deposit = companion.financial.ledger_add(
        account_id=account["id"],
        entry_type="cash_deposit",
        occurred_at=iso(checked_time),
        amount="500",
        currency="CNY",
        source="planned-deposit-fixture",
    )
    expected_sale = companion.financial.ledger_add(
        account_id=account["id"],
        entry_type="trade",
        asset_id=asset["id"],
        occurred_at=iso(checked_time),
        quantity="-100",
        price="8",
        amount="800",
        currency="CNY",
        source="expected-sale-fixture",
    )
    reported_ledger = ledger_ids(companion)

    still_blocked = companion.investment_commands.action_plan(**trade)

    assert ledger_ids(companion) == reported_ledger
    assert deposit["status"] == "needs_confirmation"
    assert expected_sale["status"] == "needs_confirmation"
    assert still_blocked["risk"]["status"] == "blocked"
    assert still_blocked["risk"]["precise_action_eligible"] is False
    assert still_blocked["candidate_qualification"]["level"] == "range_ready"
    assert still_blocked["funding_condition"]["confirmed_cash"]["amount"] == "1500"
    assert still_blocked["funding_condition"]["required_additional_cash"] == {
        "minimum": "5",
        "at_reference_price": "205",
        "maximum": "405",
    }
    assert paths_by_type(still_blocked["funding_condition"])[
        "confirmed_disposal_proceeds"
    ]["state"] == "requires_confirmed_ledger_entry"

    companion.financial.ledger_confirm(deposit["id"])
    requalified = companion.investment_commands.action_plan(**trade)

    assert requalified["risk"]["status"] == "pass"
    assert requalified["risk"]["precise_action_eligible"] is False
    assert requalified["funding_condition"] is None
    assert companion.funding_condition.current_status(
        condition["calculation_id"], as_of=iso(checked_time + timedelta(minutes=1))
    )["status"] == "facts_drifted"


def test_funding_condition_expires_and_portfolio_or_market_drift_preserves_history(
    tmp_path,
):
    companion, checked_time, account, asset, trade = setup_cash_blocked_candidate(
        tmp_path
    )
    plan = companion.investment_commands.action_plan(**trade)
    condition = plan["funding_condition"]
    frozen = companion.financial.calculation_get(condition["calculation_id"])[
        "outputs"
    ]
    assert companion.funding_condition.current_status(
        condition["calculation_id"], as_of=iso(checked_time + timedelta(seconds=1))
    )["status"] == "current"
    expired_plan = companion.investment_commands.action_plan(
        **{**trade, "as_of": trade["valid_until"]}
    )
    assert expired_plan["funding_condition"]["validity"] == {
        "status": "expired_at_as_of",
        "valid_until": trade["valid_until"],
        "invalidate_on": [
            "confirmed_ledger_change",
            "portfolio_qualification_change",
            "related_market_snapshot_change",
            "mandate_change",
            "candidate_change",
            "fee_or_tax_assumption_change",
            "expiry",
        ],
        "supports_current_planning": False,
    }

    later_time = checked_time + timedelta(minutes=1)
    newer_market = companion.financial.market_add(
        asset["id"],
        "close",
        "8.5",
        iso(later_time),
        "funding-condition-market-drift",
        "healthy",
        "CNY",
    )
    market_drift = companion.funding_condition.current_status(
        condition["calculation_id"], as_of=iso(later_time)
    )
    assert market_drift["status"] == "facts_drifted"
    assert market_drift["supports_current_planning"] is False

    refreshed_trade = {
        **trade,
        "as_of": iso(later_time),
        "price": "8.5",
        "price_range": {"min": "7.5", "max": "9.5"},
        "market_snapshot_id": newer_market["id"],
    }
    refreshed = companion.investment_commands.action_plan(**refreshed_trade)[
        "funding_condition"
    ]
    fee = companion.financial.ledger_add(
        account_id=account["id"],
        entry_type="fee",
        occurred_at=iso(later_time + timedelta(minutes=1)),
        amount="-1",
        currency="CNY",
        source="funding-condition-portfolio-drift",
    )
    companion.financial.ledger_confirm(fee["id"])
    portfolio_drift = companion.funding_condition.current_status(
        refreshed["calculation_id"],
        as_of=iso(later_time + timedelta(minutes=1)),
    )
    assert portfolio_drift["status"] == "facts_drifted"
    assert portfolio_drift["supports_current_planning"] is False

    expired = companion.funding_condition.current_status(
        refreshed["calculation_id"], as_of=iso(checked_time + timedelta(days=2))
    )
    assert expired["status"] == "expired"
    assert expired["supports_current_planning"] is False
    assert companion.financial.calculation_get(condition["calculation_id"])[
        "outputs"
    ] == frozen


def test_qualified_opportunity_sets_and_replays_funding_condition_without_side_effects(
    tmp_path,
):
    companion, _checked_time, _account, asset, trade, opportunity = (
        setup_qualified_funding_opportunity(tmp_path)
    )
    condition = companion.investment_commands.action_plan(**trade)[
        "funding_condition"
    ]
    before = side_effect_counts(companion)
    before_stage_transitions = len(opportunity["transitions"])

    linked = companion.investment_commands.opportunity_update(
        operation="funding_condition_set",
        opportunity_id=opportunity["id"],
        expected_version=opportunity["version"],
        funding_condition_calculation_id=condition["calculation_id"],
        reason="Retain the qualified candidate while confirmed cash is insufficient",
        idempotency_key="pytest:funding-condition:first",
    )
    replay = companion.investment_commands.opportunity_update(
        operation="funding_condition_set",
        opportunity_id=opportunity["id"],
        expected_version=opportunity["version"],
        funding_condition_calculation_id=condition["calculation_id"],
        reason="Retain the qualified candidate while confirmed cash is insufficient",
        idempotency_key="pytest:funding-condition:first",
    )

    assert linked["stage"] == "qualified"
    assert linked["status"] == "active"
    assert linked["version"] == opportunity["version"] + 1
    assert linked["decision_revision_id"] is None
    assert len(linked["transitions"]) == before_stage_transitions
    assert linked["funding_condition"]["state"] == "current"
    assert linked["funding_condition"]["calculation_id"] == condition[
        "calculation_id"
    ]
    assert linked["funding_condition"]["calculation"]["schema"] == (
        "investment-companion.funding-condition/v1"
    )
    assert len(linked["funding_condition_transitions"]) == 1
    assert replay["version"] == linked["version"]
    assert replay["funding_condition_transitions"] == linked[
        "funding_condition_transitions"
    ]
    assert side_effect_counts(companion) == before
    actionable_evidence = sorted(
        {
            ref
            for transition in linked["transitions"]
            for ref in transition["evidence_refs"]
        }
    )
    with pytest.raises(CompanionError, match="Funding Condition is current"):
        companion.investment_commands.opportunity_update(
            operation="transition",
            opportunity_id=linked["id"],
            expected_version=linked["version"],
            to_stage="actionable",
            to_status="active",
            evidence_refs=actionable_evidence,
            qualification={
                "validation_calculation_id": linked["qualification"][
                    "validation_calculation_id"
                ],
                "major_unknowns": [],
                "decision_basis": "Funding has not actually been confirmed.",
            },
            reason="A current Funding Condition must block promotion",
        )

    restored = companion.investment.research_context(
        subject_id=asset["id"]
    )["opportunities"][0]
    assert restored["funding_condition"]["calculation_id"] == condition[
        "calculation_id"
    ]
    assert restored["funding_condition_transitions"][0]["state"] == "current"


def test_funding_condition_replacement_preserves_superseded_and_expired_history(
    tmp_path, monkeypatch
):
    companion, _checked_time, _account, asset, trade, opportunity = (
        setup_qualified_funding_opportunity(tmp_path)
    )
    first_condition = companion.investment_commands.action_plan(**trade)[
        "funding_condition"
    ]
    second_condition = companion.investment_commands.action_plan(
        **{**trade, "quantity": "300"}
    )["funding_condition"]
    first = companion.investment_commands.opportunity_update(
        operation="funding_condition_set",
        opportunity_id=opportunity["id"],
        expected_version=opportunity["version"],
        funding_condition_calculation_id=first_condition["calculation_id"],
        reason="Retain the first funding path",
        idempotency_key="pytest:funding-condition:replace:first",
    )
    replaced = companion.investment_commands.opportunity_update(
        operation="funding_condition_set",
        opportunity_id=opportunity["id"],
        expected_version=first["version"],
        funding_condition_calculation_id=second_condition["calculation_id"],
        reason="Replace it with the current candidate quantity",
        idempotency_key="pytest:funding-condition:replace:second",
    )

    assert replaced["stage"] == "qualified"
    assert replaced["version"] == opportunity["version"] + 2
    assert replaced["funding_condition"]["calculation_id"] == second_condition[
        "calculation_id"
    ]
    assert [
        item["state"] for item in replaced["funding_condition_transitions"]
    ] == ["superseded", "current"]
    assert replaced["funding_condition_transitions"][1][
        "replaces_calculation_id"
    ] == first_condition["calculation_id"]

    with companion.db.connect() as con:
        frozen_rows = [
            dict(row)
            for row in con.execute(
                "SELECT * FROM opportunity_funding_condition_transitions "
                "WHERE opportunity_id=? ORDER BY to_version",
                (opportunity["id"],),
            ).fetchall()
        ]
    monkeypatch.setattr(
        "companion.application.portfolio_decisions.iso",
        lambda *_args, **_kwargs: trade["valid_until"],
    )
    expired = companion.investment.research_context(
        subject_id=asset["id"]
    )["opportunities"][0]
    assert expired["funding_condition"] is None
    assert [
        item["state"] for item in expired["funding_condition_transitions"]
    ] == ["superseded", "expired"]
    assert expired["funding_condition_transitions"][-1]["condition_status"] == (
        "expired"
    )
    with companion.db.connect() as con:
        assert [
            dict(row)
            for row in con.execute(
                "SELECT * FROM opportunity_funding_condition_transitions "
                "WHERE opportunity_id=? ORDER BY to_version",
                (opportunity["id"],),
            ).fetchall()
        ] == frozen_rows


def test_funding_condition_set_rejects_invalid_state_lineage_version_and_idempotency(
    tmp_path,
):
    companion, _checked_time, account, asset, trade, opportunity = (
        setup_qualified_funding_opportunity(tmp_path)
    )
    first_condition = companion.investment_commands.action_plan(**trade)[
        "funding_condition"
    ]
    second_condition = companion.investment_commands.action_plan(
        **{**trade, "quantity": "300"}
    )["funding_condition"]
    observed = companion.investment_commands.opportunity_update(
        operation="create",
        subject={"account_id": account["id"], "asset_id": asset["id"]},
        evidence_refs=[opportunity["transitions"][0]["evidence_refs"][0]],
        reason="Observed candidate cannot retain funding yet",
        thesis_id=opportunity["thesis_id"],
    )
    with pytest.raises(CompanionError, match="active qualified Opportunity"):
        companion.investment_commands.opportunity_update(
            operation="funding_condition_set",
            opportunity_id=observed["id"],
            expected_version=observed["version"],
            funding_condition_calculation_id=first_condition["calculation_id"],
            reason="Invalid observed association",
        )

    other_account = companion.financial.account_create("Other account", "CNY")
    with companion.db.transaction() as con:
        con.execute(
            "UPDATE opportunities SET subject_json=? WHERE id=?",
            (
                '{"account_id":"%s","asset_id":"%s"}'
                % (other_account["id"], asset["id"]),
                opportunity["id"],
            ),
        )
    with pytest.raises(CompanionError, match="account/asset mismatch"):
        companion.investment_commands.opportunity_update(
            operation="funding_condition_set",
            opportunity_id=opportunity["id"],
            expected_version=opportunity["version"],
            funding_condition_calculation_id=first_condition["calculation_id"],
            reason="Cross-account association",
        )
    other_asset = companion.financial.asset_upsert(
        "stock", "Other asset", "CNY", {"fixture": "cross-asset"}
    )
    with companion.db.transaction() as con:
        con.execute(
            "UPDATE opportunities SET subject_json=? WHERE id=?",
            (
                '{"account_id":"%s","asset_id":"%s"}'
                % (account["id"], other_asset["id"]),
                opportunity["id"],
            ),
        )
    with pytest.raises(CompanionError, match="account/asset mismatch"):
        companion.investment_commands.opportunity_update(
            operation="funding_condition_set",
            opportunity_id=opportunity["id"],
            expected_version=opportunity["version"],
            funding_condition_calculation_id=first_condition["calculation_id"],
            reason="Cross-asset association",
        )
    with companion.db.transaction() as con:
        con.execute(
            "UPDATE opportunities SET subject_json=? WHERE id=?",
            (
                '{"account_id":"%s","asset_id":"%s"}'
                % (account["id"], asset["id"]),
                opportunity["id"],
            ),
        )
    with pytest.raises(CompanionError, match="Funding Condition Calculation"):
        companion.investment_commands.opportunity_update(
            operation="funding_condition_set",
            opportunity_id=opportunity["id"],
            expected_version=opportunity["version"],
            funding_condition_calculation_id=first_condition["risk_calculation_id"],
            reason="Wrong Calculation kind",
        )

    linked = companion.investment_commands.opportunity_update(
        operation="funding_condition_set",
        opportunity_id=opportunity["id"],
        expected_version=opportunity["version"],
        funding_condition_calculation_id=first_condition["calculation_id"],
        reason="Valid association",
        idempotency_key="pytest:funding-condition:collision",
    )
    with pytest.raises(CompanionError, match="idempotency_key belongs to different inputs"):
        companion.investment_commands.opportunity_update(
            operation="funding_condition_set",
            opportunity_id=opportunity["id"],
            expected_version=opportunity["version"],
            funding_condition_calculation_id=second_condition["calculation_id"],
            reason="Different payload reuses the key",
            idempotency_key="pytest:funding-condition:collision",
        )
    with pytest.raises(CompanionError, match="Opportunity version conflict"):
        companion.investment_commands.opportunity_update(
            operation="funding_condition_set",
            opportunity_id=opportunity["id"],
            expected_version=opportunity["version"],
            funding_condition_calculation_id=second_condition["calculation_id"],
            reason="Stale writer loses deterministically",
            idempotency_key="pytest:funding-condition:stale",
        )
    assert linked["version"] == opportunity["version"] + 1


def test_concurrent_same_funding_condition_update_replays_one_transition(
    tmp_path, monkeypatch
):
    companion, _checked_time, _account, _asset, trade, opportunity = (
        setup_qualified_funding_opportunity(tmp_path)
    )
    condition = companion.investment_commands.action_plan(**trade)[
        "funding_condition"
    ]
    rendezvous = Barrier(2)
    transaction = companion.db.transaction

    @contextmanager
    def concurrent_transaction():
        rendezvous.wait(timeout=10)
        with transaction() as con:
            yield con

    monkeypatch.setattr(companion.db, "transaction", concurrent_transaction)

    def update():
        return companion.investment_commands.opportunity_update(
            operation="funding_condition_set",
            opportunity_id=opportunity["id"],
            expected_version=opportunity["version"],
            funding_condition_calculation_id=condition["calculation_id"],
            reason="Concurrent idempotent Funding Condition",
            idempotency_key="pytest:funding-condition:concurrent-same",
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(update) for _ in range(2)]
        results = [future.result() for future in futures]

    assert {result["version"] for result in results} == {
        opportunity["version"] + 1
    }
    assert all(result["stage"] == "qualified" for result in results)
    assert all(
        len(result["funding_condition_transitions"]) == 1 for result in results
    )


def test_concurrent_distinct_funding_condition_updates_have_one_lost_writer(
    tmp_path, monkeypatch
):
    companion, _checked_time, _account, _asset, trade, opportunity = (
        setup_qualified_funding_opportunity(tmp_path)
    )
    conditions = [
        companion.investment_commands.action_plan(**{**trade, "quantity": quantity})[
            "funding_condition"
        ]
        for quantity in (200, 300)
    ]
    rendezvous = Barrier(2)
    transaction = companion.db.transaction

    @contextmanager
    def concurrent_transaction():
        rendezvous.wait(timeout=10)
        with transaction() as con:
            yield con

    monkeypatch.setattr(companion.db, "transaction", concurrent_transaction)

    def update(index: int):
        return companion.investment_commands.opportunity_update(
            operation="funding_condition_set",
            opportunity_id=opportunity["id"],
            expected_version=opportunity["version"],
            funding_condition_calculation_id=conditions[index]["calculation_id"],
            reason=f"Concurrent distinct Funding Condition {index}",
            idempotency_key=f"pytest:funding-condition:concurrent-distinct:{index}",
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(update, index) for index in range(2)]
        results = []
        errors = []
        for future in futures:
            try:
                results.append(future.result())
            except CompanionError as exc:
                errors.append(str(exc))

    assert len(results) == 1
    assert errors == ["Opportunity Funding Condition update lost to another writer"]
    assert results[0]["stage"] == "qualified"
    assert len(results[0]["funding_condition_transitions"]) == 1


def test_confirmed_funding_ledger_entry_requires_reruns_and_never_advances_opportunity(
    tmp_path,
):
    companion, checked_time, account, _asset, trade, opportunity = (
        setup_qualified_funding_opportunity(tmp_path)
    )
    condition = companion.investment_commands.action_plan(**trade)[
        "funding_condition"
    ]
    linked = companion.investment_commands.opportunity_update(
        operation="funding_condition_set",
        opportunity_id=opportunity["id"],
        expected_version=opportunity["version"],
        funding_condition_calculation_id=condition["calculation_id"],
        reason="Wait for a confirmed funding fact",
    )
    deposit = companion.financial.ledger_add(
        account_id=account["id"],
        entry_type="cash_deposit",
        occurred_at=iso(checked_time),
        amount="500",
        currency="CNY",
        source="opportunity-funding-confirmation-fixture",
    )

    companion.financial.ledger_confirm(deposit["id"])

    unchanged = companion.operating.opportunity_get(opportunity["id"])
    assert unchanged["stage"] == "qualified"
    assert unchanged["status"] == "active"
    assert unchanged["version"] == linked["version"]
    assert unchanged["decision_revision_id"] is None
    assert unchanged["funding_condition"] is None
    assert unchanged["funding_condition_transitions"][-1]["state"] == "expired"
    assert unchanged["funding_condition_transitions"][-1][
        "condition_status"
    ] == "facts_drifted"
    assert companion.operating.queue_list() == []
    assert companion.execution.list() == []

    rerun = companion.investment_commands.action_plan(**trade)
    assert rerun["risk"]["status"] == "pass"
    assert rerun["funding_condition"] is None
    still_qualified = companion.operating.opportunity_get(opportunity["id"])
    assert still_qualified["stage"] == "qualified"
    assert still_qualified["version"] == linked["version"]


def test_confirmed_disposal_requires_reruns_and_never_advances_opportunity(
    tmp_path,
):
    companion, checked_time, account, asset, trade, opportunity = (
        setup_qualified_funding_opportunity(tmp_path)
    )
    holding = companion.financial.ledger_add(
        account_id=account["id"],
        entry_type="trade",
        asset_id=asset["id"],
        occurred_at=iso(checked_time - timedelta(minutes=1)),
        quantity="100",
        price="8",
        amount="-800",
        currency="CNY",
        source="opportunity-disposal-holding-fixture",
    )
    companion.financial.ledger_confirm(holding["id"])
    condition = companion.investment_commands.action_plan(**trade)[
        "funding_condition"
    ]
    linked = companion.investment_commands.opportunity_update(
        operation="funding_condition_set",
        opportunity_id=opportunity["id"],
        expected_version=opportunity["version"],
        funding_condition_calculation_id=condition["calculation_id"],
        reason="Wait for confirmed disposal proceeds",
    )
    disposal = companion.financial.ledger_add(
        account_id=account["id"],
        entry_type="trade",
        asset_id=asset["id"],
        occurred_at=iso(checked_time),
        quantity="-100",
        price="13",
        amount="1300",
        currency="CNY",
        source="opportunity-disposal-confirmation-fixture",
    )

    companion.financial.ledger_confirm(disposal["id"])

    unchanged = companion.operating.opportunity_get(opportunity["id"])
    assert unchanged["stage"] == "qualified"
    assert unchanged["status"] == "active"
    assert unchanged["version"] == linked["version"]
    assert unchanged["decision_revision_id"] is None
    assert unchanged["funding_condition"] is None
    assert unchanged["funding_condition_transitions"][-1]["state"] == "expired"
    assert companion.operating.queue_list() == []
    assert companion.execution.list() == []

    rerun = companion.investment_commands.action_plan(**trade)
    assert rerun["risk"]["status"] == "pass"
    assert rerun["funding_condition"] is None
    still_qualified = companion.operating.opportunity_get(opportunity["id"])
    assert still_qualified["stage"] == "qualified"
    assert still_qualified["version"] == linked["version"]
