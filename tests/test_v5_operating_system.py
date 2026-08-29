from __future__ import annotations

import sqlite3
from datetime import timedelta
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest

from companion.core import Companion, CompanionError, canonical
from companion.db import (
    BASELINE_CHECKSUM,
    BASELINE_MIGRATION_ID,
    MIGRATION_004_CHECKSUM,
    MIGRATION_004_ID,
    MIGRATION_004_SQL,
    MIGRATION_TABLE_SCHEMA,
    SCHEMA,
    SCHEMA_VERSION,
    V3_SCHEMA,
)
from companion.governance import GATE_CHECKLISTS
from companion.timeutil import iso, utc_now


def pass_gate(companion: Companion, gate: str) -> None:
    artifact = companion.data.manifest_publish(
        kind="fixture_evidence",
        schema_version="fixture/v1",
        manifest={"gate": gate, "suite": "v5"},
    )
    evidence = companion.gates.evidence_publish(
        gate,
        checks={key: True for key in GATE_CHECKLISTS[gate]},
        artifacts=[artifact["id"]],
        unknowns=[],
        counterevidence=[],
        counterevidence_disposition={},
        code_version="test-fixture",
        scope="test_fixture",
    )
    companion.gates.assessment_record(
        gate=gate,
        status="go",
        evidence_manifest_id=evidence["id"],
        code_version="test-fixture",
        assessed_by="pytest",
        scope="test_fixture",
    )


def pass_g0(companion: Companion) -> None:
    pass_gate(companion, "G0")


def confirmed_context(companion: Companion, context_type: str, content: dict) -> dict:
    draft = companion.cognition.context_create(context_type, content, reason="V5 fixture")
    return companion.cognition.context_confirm(draft["id"])


def ashare_reality() -> dict:
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


def research_validation_spec() -> dict:
    return {
        "falsifiers": ["cash conversion deteriorates below the registered threshold"],
        "counterevidence": {
            "searched": ["issuer disclosures", "independent exchange evidence"],
            "findings": [
                {
                    "claim": "industry cycle remains uncertain",
                    "disposition": "position size remains bounded",
                }
            ],
        },
        "applicability": {
            "horizon": "one month",
            "conditions": ["normal A-share liquidity"],
            "excluded_conditions": ["trading suspension"],
        },
        "cost_assumptions": {
            "commission": "RealitySpec commission",
            "tax": "A-share sell stamp duty",
            "slippage": "frozen execution price range",
        },
        "max_evidence_age_days": 30,
    }


def setup_operating_system(tmp_path: Path) -> tuple[Companion, dict]:
    companion = Companion(tmp_path, gate_scope="test_fixture")
    companion.initialize()
    pass_g0(companion)
    companion.jobs.feature_set("v5_operating_system", True, reason="V5 fixture")
    account = companion.financial.account_create("Primary", "CNY")
    opening = companion.financial.ledger_add(
        account_id=account["id"],
        entry_type="opening_balance",
        occurred_at=iso(utc_now() - timedelta(days=2)),
        amount="100000",
        currency="CNY",
        source="pytest",
    )
    companion.financial.ledger_confirm(opening["id"])
    investor = confirmed_context(
        companion,
        "investor",
        {"goals": ["long-term capital growth"], "liquidity_horizon": "12 months"},
    )
    mandate = confirmed_context(
        companion,
        "mandate",
        {"minimum_cash": {"CNY": "20000"}, "max_single_position_weight": "0.20"},
    )
    attention = confirmed_context(
        companion,
        "attention",
        {"timezone": "Asia/Shanghai", "daily_notification_budget": 10},
    )
    content = {
        "objective": "在个人风险和流动性约束内检验可重复的超额收益来源",
        "success_criteria": ["结果相对基准可计算", "每个行动均可追溯到证据和个人约束"],
        "benchmark": {"name": "CSI 300 total-return proxy", "calculation_policy": "frozen monthly"},
        "risk_budget": {
            "max_drawdown_review_level": "0.15",
            "single_position_weight": "0.20",
            "bounded_action": {
                "enabled": True,
                "allowed_asset_types": ["stock", "etf"],
                "allowed_execution_plan_types": ["priced_buy", "priced_sell", "bracket_exit"],
                "max_trade_weight": "0.02",
                "max_post_trade_weight": "0.05",
                "max_validity_sessions": 20,
                "max_active_bounded_actions": 2,
            },
        },
        "universe": {"markets": ["CN-A"], "exclusions": ["unverified identity"]},
        "horizons": {"research": "6-24 months", "decision_validity": "explicit per Decision"},
        "operating_cadence": {"daily": "exceptions", "weekly": "committee", "monthly": "scorecard"},
        "stop_conditions": ["evidence lineage breaks", "personal mandate changes", "net value remains unproven"],
        "account_ids": [account["id"]],
    }
    refs = {
        "investor_revision_id": investor["id"],
        "mandate_revision_id": mandate["id"],
        "attention_revision_id": attention["id"],
    }
    draft = companion.operating.program_create(
        name="Personal investment operating program",
        content=content,
        context_refs=refs,
        reason="User requested V5 operating loop",
    )
    program = companion.operating.program_confirm(
        draft["revisions"][0]["id"],
        user_approval_ref="pytest:user-approved-v5",
    )
    return companion, {
        "account": account,
        "contexts": refs,
        "content": content,
        "program": program,
    }


def build_actionable_opportunity(companion: Companion, fixture: dict, execution_plan_builder=None,asset_type="stock") -> dict:
    cutoff = iso()
    asset = companion.financial.asset_upsert(
        asset_type=asset_type,
        name="Fixture Co",
        currency="CNY",
        identifiers={"ts_code": "000001.SZ"},
    )
    source_a = companion.data.manifest_publish(
        kind="v5_research_evidence",
        schema_version="research-evidence/v1",
        manifest={
            "asset_id": asset["id"],
            "source": "official filing",
            "source_group": "issuer",
            "first_known_at": cutoff,
            "observed_at": cutoff,
        },
    )
    source_b = companion.data.manifest_publish(
        kind="v5_research_evidence",
        schema_version="research-evidence/v1",
        manifest={
            "asset_id": asset["id"],
            "source": "exchange notice",
            "source_group": "exchange",
            "first_known_at": cutoff,
            "observed_at": cutoff,
        },
    )
    evidence_ids = [source_a["id"], source_b["id"]]
    research = companion.investment_commands.research_publish(
        subject={"asset_id": asset["id"]},
        content="# Fixture thesis\n\nA bounded, falsifiable fixture thesis.",
        evidence_manifest_ids=evidence_ids,
        knowledge_cutoff=cutoff,
        validation_spec=research_validation_spec(),
    )
    thesis = research["thesis"]
    thesis_revision = research["revision"]
    validation_id = research["validation"]["calculation_id"]
    opportunity = companion.operating.opportunity_create(
        subject={"asset_id": asset["id"], "intent": "research candidate"},
        evidence_refs=[source_a["id"]],
        reason="Official filing created a bounded research question",
        thesis_id=thesis["id"],
    )
    opportunity = companion.operating.opportunity_transition(
        opportunity["id"],
        expected_version=1,
        to_stage="researching",
        to_status="active",
        evidence_refs=[source_a["id"], source_b["id"]],
        reason="Primary accepted a bounded research brief",
    )
    replay = companion.operating.opportunity_transition(
        opportunity["id"],
        expected_version=1,
        to_stage="researching",
        to_status="active",
        evidence_refs=[source_a["id"], source_b["id"]],
        reason="Primary accepted a bounded research brief",
    )
    assert replay["version"] == 2
    qualified = {
        "validation_calculation_id": validation_id,
        "major_unknowns": ["decision price and portfolio fit are not frozen"],
        "decision_basis": "Two primary sources support continued evaluation, not action.",
    }
    opportunity = companion.operating.opportunity_transition(
        opportunity["id"],
        expected_version=2,
        to_stage="qualified",
        to_status="active",
        evidence_refs=[*evidence_ids, validation_id],
        qualification=qualified,
        reason="Evidence passed the research qualification contract",
    )
    valid_until = iso(utc_now() + timedelta(days=7))
    market = companion.financial.market_add(
        asset["id"], "close", "10", cutoff, "fixture-market", "healthy", "CNY"
    )
    requested_execution_plan=execution_plan_builder(fixture["account"]["id"],asset["id"]) if execution_plan_builder else None
    if requested_execution_plan:valid_until=iso(utc_now()+timedelta(days=30))
    if requested_execution_plan and requested_execution_plan["plan_type"] in {"priced_sell","bracket_exit","moving_grid"}:
        holding=companion.financial.ledger_add(account_id=fixture["account"]["id"],entry_type="trade",asset_id=asset["id"],occurred_at=iso(utc_now()-timedelta(days=1)),quantity="1000",price="10",amount="-10000",currency="CNY",source="pytest-execution-plan-holding")
        companion.financial.ledger_confirm(holding["id"])
    primary_quantity="-100" if requested_execution_plan and requested_execution_plan["plan_type"] in {"priced_sell","bracket_exit"} else "100"
    risk_price_range={"min":"9.8","max":"10.2"}
    if requested_execution_plan:
        low,high=companion.investment_commands._execution_plan_price_bounds(requested_execution_plan["plan_type"],requested_execution_plan["spec"])
        risk_price_range={"min":str(min(low,Decimal("10"))),"max":str(max(high,Decimal("10")))}
    risk = companion.investment_commands.risk_assess(
        as_of=cutoff,
        account_id=fixture["account"]["id"],
        asset_id=asset["id"],
        quantity=primary_quantity,
        price="10",
        reality_spec=ashare_reality(),
        market_snapshot_id=market["id"],
        max_market_age_seconds=3600,
        valid_until=valid_until,
        price_range=risk_price_range,
    )
    sell_risk=None
    if requested_execution_plan and requested_execution_plan["plan_type"]=="moving_grid":
        sell_risk=companion.investment_commands.risk_assess(as_of=cutoff,account_id=fixture["account"]["id"],asset_id=asset["id"],quantity="-100",price="10",reality_spec=ashare_reality(),market_snapshot_id=market["id"],max_market_age_seconds=3600,valid_until=valid_until,price_range=risk_price_range)
    decision = companion.investment_commands.decision_publish(
        subject={"asset_id": asset["id"]},
        content="# Fixture Decision\n\nBuy a bounded position only while all gates remain current.",
        decision_kind="action",
        account_id=fixture["account"]["id"],
        as_of=cutoff,
        knowledge_cutoff=cutoff,
        valid_until=valid_until,
        thesis_revision_ids=[thesis_revision["id"]],
        evidence_manifest_ids=evidence_ids,
        invalidators=["portfolio or mandate changes", "new filing contradicts thesis"],
        no_action={"choice": "continue observing", "cost": "possible opportunity cost"},
        alternatives=[{"choice": "hold cash"}, {"choice": "buy fewer shares"}],
        risk_calculation_id=risk["calculation_id"],
        research_validation_calculation_id=validation_id,
        execution_plan=requested_execution_plan,
        execution_sell_risk_calculation_id=sell_risk["calculation_id"] if sell_risk else None,
    )
    decision_revision = decision["revision"]
    actionable = {
        "validation_calculation_id": validation_id,
        "major_unknowns": [],
        "decision_basis": "The issued Decision freezes personal facts, evidence and validity.",
    }
    opportunity = companion.operating.opportunity_transition(
        opportunity["id"],
        expected_version=3,
        to_stage="actionable",
        to_status="active",
        evidence_refs=[
            *evidence_ids,
            validation_id,
            risk["calculation_id"],
            decision_revision["id"],
        ],
        qualification=actionable,
        decision_revision_id=decision_revision["id"],
        reason="Current issued Decision closed the remaining major unknowns",
    )
    return {
        "asset": asset,
        "sources": [source_a, source_b],
        "thesis_revision": thesis_revision,
        "decision_revision": decision_revision,
        "portfolio_state": companion.financial.calculation_get(
            decision["portfolio_calculation_id"]
        )["outputs"],
        "research": research,
        "risk": risk,
        "market": market,
        "opportunity": opportunity,
        "valid_until": valid_until,
    }


def accept_action_card(companion: Companion, built: dict) -> dict:
    queue = companion.operating.queue_enqueue(
        built["opportunity"]["id"],
        decision_revision_id=built["decision_revision"]["id"],
    )
    attention = companion.attention.decide(
        topic=f"accepted-action-{queue['id']}",
        materiality="high",
        confidence="decision_grade",
        reason="Fixture presents one current Action Card",
        evidence=[queue["id"]],
    )
    companion.attention.mark_delivered(attention["id"])
    companion.operating.queue_respond(
        queue["id"], state="presented", attention_decision_id=attention["id"]
    )
    return companion.operating.queue_respond(queue["id"], state="accepted")


def test_version_neutral_profile_covers_program_opportunity_action_and_workflow(tmp_path: Path):
    companion, fixture = setup_operating_system(tmp_path)
    program = fixture["program"]
    revised_content = {**fixture["content"], "objective": "验证版本无关投资经营入口"}
    revised = companion.investment_commands.program_update(
        operation="revise",
        program_id=program["id"],
        expected_version=program["version"],
        content=revised_content,
        context_refs=fixture["contexts"],
        reason="Profile integration fixture",
    )
    assert revised["revisions"][0]["status"] == "draft"
    assert companion.investment.program_context(program_id=program["id"])["selected"]["id"] == program["id"]

    evidence = companion.investment_commands.evidence_update(
        operation="publish_source",
        subject={"asset_id": "fixture:profile"},
            source="fixture filing",
            source_group="issuer",
            evidence_type="official_disclosure",
        first_known_at=iso(),
        observed_at=iso(),
        claims=["A bounded research question exists"],
    )
    opportunity = companion.investment_commands.opportunity_update(
        operation="create",
        subject={"asset_id": "fixture:profile"},
        evidence_refs=[evidence["id"]],
        reason="Version-neutral opportunity fixture",
    )
    researching = companion.investment_commands.opportunity_update(
        operation="transition",
        opportunity_id=opportunity["id"],
        expected_version=opportunity["version"],
        to_stage="researching",
        to_status="active",
        evidence_refs=[evidence["id"]],
        reason="Accept bounded research question",
    )
    assert researching["stage"] == "researching"

    built = build_actionable_opportunity(companion, fixture)
    queue = companion.investment_commands.action_update(
        operation="enqueue",
        opportunity_id=built["opportunity"]["id"],
        decision_revision_id=built["decision_revision"]["id"],
    )
    attention = companion.investment_commands.delivery_update(
        operation="attention_decide",
        topic=f"profile-action-{queue['id']}",
        materiality="high",
        confidence="decision_grade",
        reason="Present one current action",
        evidence=[queue["id"]],
    )
    companion.investment_commands.delivery_update(
        operation="attention_delivered", attention_decision_id=attention["id"]
    )
    presented = companion.investment_commands.action_update(
        operation="respond",
        queue_id=queue["id"],
        state="presented",
        attention_decision_id=attention["id"],
    )
    assert presented["state"] == "presented"

    brief = companion.investment_commands.brief_update(
        operation="publish",
        brief_type="daily",
        period_key=utc_now().date().isoformat(),
        as_of=iso(),
        conclusion="action",
        payload={
            "summary": "One validated action awaits the user.",
            "what_changed": ["A decision passed research and risk gates."],
            "decision": "Review the bounded action.",
            "risks": ["The action expires."],
            "next_check_at": iso(utc_now() + timedelta(hours=1)),
            "queue_item_ids": [queue["id"]],
        },
        source_refs=[evidence["id"], queue["id"]],
    )
    assert brief["conclusion"] == "action"

    schedule = companion.investment_commands.workflow_update(
        operation="schedule_create",
        name="Profile integration review",
        kind="review",
        mission="Verify the version-neutral workflow facade",
        cadence={"type": "interval", "seconds": 86400},
    )
    schedules = companion.investment.workflow_context(view="schedules", status="active")
    assert schedule["id"] in {item["id"] for item in schedules}
    assert companion.investment.home()["workflow"]["active_schedule_count"] >= 1


def test_v5_program_opportunity_queue_and_user_briefs(tmp_path: Path):
    companion, fixture = setup_operating_system(tmp_path)
    built = build_actionable_opportunity(companion, fixture)
    opportunity = built["opportunity"]
    assert opportunity["stage"] == "actionable"
    assert opportunity["evidence_band"] == "eligible_for_decision"
    assert opportunity["research_validation"]["eligible_for_decision"] is True
    queue = companion.operating.queue_enqueue(
        opportunity["id"],
        decision_revision_id=built["decision_revision"]["id"],
    )
    card = companion.operating.queue_card(queue["id"])
    assert card["action"]["side"] == "buy"
    assert card["action"]["quantity"] == "100"
    assert card["executable_now"] is True
    assert card["research_validation"]["eligible_for_decision"] is True
    assert card["risk_gate"]["status"] == "pass"
    assert card["human_execution_only"] is True
    assert card["execution_created"] is False
    assert card["guarantees"] == {"profit": False, "high_win_rate": False}
    with pytest.raises(CompanionError, match="no_action brief conflicts"):
        companion.operating.brief_prepare(
            brief_type="daily",
            period_key="2026-08-18",
            as_of=iso(),
            conclusion="no_action",
            payload={
                "summary": "No action",
                "what_changed": [],
                "decision": "Wait",
                "risks": [],
                "next_check_at": iso(utc_now() + timedelta(days=1)),
                "queue_item_ids": [],
            },
            source_refs=[built["decision_revision"]["id"]],
        )
    brief = companion.operating.brief_prepare(
        brief_type="daily",
        period_key="2026-08-18",
        as_of=iso(),
        conclusion="action",
        payload={
            "summary": "One candidate reached the user decision threshold.",
            "what_changed": ["A current Decision closed the final evidence gap."],
            "decision": "Review the action card; execution remains manual.",
            "risks": ["The evidence may still fail after publication."],
            "next_check_at": iso(utc_now() + timedelta(hours=6)),
            "queue_item_ids": [queue["id"]],
        },
        source_refs=[built["decision_revision"]["id"], queue["id"]],
    )
    assert brief["conclusion"] == "action"
    today = companion.operating.today()
    assert today["mode"] == "action" and len(today["action_cards"]) == 1
    snoozed_until = iso(utc_now() + timedelta(minutes=30))
    snoozed = companion.operating.queue_respond(
        queue["id"],
        state="snoozed",
        reason="User asked to review after the current meeting",
        snoozed_until=snoozed_until,
    )
    assert snoozed["state"] == "snoozed" and snoozed["snoozed_until"] == snoozed_until
    deferred_today = companion.operating.today()
    assert deferred_today["mode"] == "review_required"
    assert deferred_today["deferred_queue"][0]["queue_id"] == queue["id"]
    with pytest.raises(CompanionError, match="no_action brief conflicts"):
        companion.operating.brief_prepare(
            brief_type="daily",
            period_key="2026-08-18-snoozed",
            as_of=iso(),
            conclusion="no_action",
            payload={
                "summary": "Must not hide a deferred action.",
                "what_changed": [],
                "decision": "Wait for the explicit snooze time.",
                "risks": [],
                "next_check_at": iso(utc_now() + timedelta(hours=1)),
                "queue_item_ids": [],
            },
            source_refs=[built["decision_revision"]["id"]],
        )
    with pytest.raises(CompanionError, match="different snoozed_until"):
        companion.operating.queue_respond(
            queue["id"],
            state="snoozed",
            reason="User asked to review after the current meeting",
            snoozed_until=iso(utc_now() + timedelta(minutes=45)),
        )
    with companion.db.transaction() as con:
        con.execute(
            "UPDATE decision_queue_items SET snoozed_until=? WHERE id=?",
            (iso(utc_now() - timedelta(seconds=1)), queue["id"]),
        )
    assert companion.operating.queue_get(queue["id"])["state"] == "ready"
    brief_attention = companion.attention.decide(
        topic="v5-action-brief",
        materiality="high",
        confidence="decision_grade",
        reason="Valid V5 action brief",
        evidence=[brief["id"]],
    )
    companion.attention.mark_delivered(brief_attention["id"])
    presented_brief = companion.operating.brief_mark_presented(
        brief["id"], attention_decision_id=brief_attention["id"]
    )
    assert presented_brief["status"] == "presented"
    unbound_attention = companion.attention.decide(
        topic="v5-unbound-action-card",
        materiality="high",
        confidence="decision_grade",
        reason="Fixture deliberately omits queue evidence",
        evidence=[],
    )
    companion.attention.mark_delivered(unbound_attention["id"])
    with pytest.raises(CompanionError, match="must cite the queue item"):
        companion.operating.queue_respond(
            queue["id"],
            state="presented",
            attention_decision_id=unbound_attention["id"],
        )
    attention = companion.attention.decide(
        topic="v5-action-card",
        materiality="high",
        confidence="decision_grade",
        reason="Valid V5 DecisionQueue item",
        evidence=[queue["id"]],
    )
    companion.attention.mark_delivered(attention["id"])
    companion.operating.queue_respond(
        queue["id"],
        state="presented",
        attention_decision_id=attention["id"],
    )
    accepted = companion.operating.queue_respond(queue["id"], state="accepted")
    assert accepted["state"] == "accepted"
    assert companion.operating.today()["mode"] == "action"
    assert companion.system_status()["counts"]["executions"] == 0
    metrics = companion.operating.program_metrics_calculate(
        period_start=iso(utc_now() - timedelta(hours=1)),
        period_end=iso(utc_now() + timedelta(seconds=1)),
    )
    assert metrics["opportunity_flow"]["observed"] == 1
    assert metrics["opportunity_flow"]["actionable"] == 1
    assert metrics["decision_flow"]["presented"] == 1
    assert metrics["decision_flow"]["accepted"] == 1
    assert metrics["rates"]["created_cohort_actionable_by_period_end"] == "1.0000"
    assert metrics["rates"]["presented_cohort_accepted_by_period_end"] == "1.0000"


def test_action_card_is_revalidated_before_it_is_presented(tmp_path: Path):
    companion, fixture = setup_operating_system(tmp_path)
    built = build_actionable_opportunity(companion, fixture)
    queue = companion.operating.queue_enqueue(
        built["opportunity"]["id"],
        decision_revision_id=built["decision_revision"]["id"],
    )
    changed = companion.financial.ledger_add(
        account_id=fixture["account"]["id"],
        entry_type="cash_deposit",
        occurred_at=iso(),
        amount="1",
        currency="CNY",
        source="portfolio changed before presentation",
    )
    companion.financial.ledger_confirm(changed["id"])
    attention = companion.attention.decide(
        topic="stale-action-card",
        materiality="high",
        confidence="decision_grade",
        reason="Attempt to present a stale card",
        evidence=[queue["id"]],
    )
    companion.attention.mark_delivered(attention["id"])
    with pytest.raises(CompanionError, match="confirmed Ledger has changed"):
        companion.operating.queue_respond(
            queue["id"], state="presented", attention_decision_id=attention["id"]
        )
    with companion.db.transaction() as con:
        con.execute(
            "UPDATE decision_queue_items SET valid_until=? WHERE id=?",
            (iso(utc_now() - timedelta(seconds=1)), queue["id"]),
        )
    assert companion.operating.queue_get(queue["id"])["state"] == "expired"
    assert companion.operating.today()["mode"] == "review_required"


def test_v5_program_fails_closed_on_context_drift_and_no_action_expires(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    companion, fixture = setup_operating_system(tmp_path)
    source = companion.inbox_add(source="official_filing", title="No change", content="fixture")
    with pytest.raises(CompanionError, match="immutable revision or fact"):
        companion.operating.opportunity_create(
            subject={"topic": "mutable object must not count as evidence"},
            evidence_refs=[fixture["program"]["id"]],
            reason="Fixture verifies immutable Opportunity evidence",
        )
    next_check = utc_now() + timedelta(hours=1)
    companion.operating.brief_prepare(
        brief_type="daily",
        period_key="2026-08-18",
        as_of=iso(),
        conclusion="no_action",
        payload={
            "summary": "No candidate reached the action threshold.",
            "what_changed": [],
            "decision": "Do not act before the next bounded check.",
            "risks": ["The source coverage remains deliberately narrow."],
            "next_check_at": iso(next_check),
            "queue_item_ids": [],
        },
        source_refs=[source["id"]],
    )
    assert companion.operating.today()["mode"] == "no_action"
    monkeypatch.setattr("companion.operating.utc_now", lambda: next_check + timedelta(seconds=1))
    assert companion.operating.today()["mode"] == "review_required"

    new_investor = confirmed_context(
        companion,
        "investor",
        {"goals": ["capital preservation before growth"], "liquidity_horizon": "6 months"},
    )
    drift = companion.operating.today()
    assert drift["mode"] == "review_required"
    assert drift["program_alignment"]["aligned"] is False
    with pytest.raises(CompanionError, match="InvestmentProgram requires review"):
        companion.operating.opportunity_create(
            subject={"topic": "must not proceed under stale constraints"},
            evidence_refs=[source["id"]],
            reason="Fixture verifies fail-closed alignment",
        )
    current = companion.operating.program_get(fixture["program"]["id"])
    refs = {**fixture["contexts"], "investor_revision_id": new_investor["id"]}
    revised = companion.operating.program_revise(
        program_id=current["id"],
        expected_version=current["version"],
        content=fixture["content"],
        context_refs=refs,
        reason="Re-align Program to the new current Investor context",
    )
    draft = next(item for item in revised["revisions"] if item["status"] == "draft")
    restored = companion.operating.program_confirm(
        draft["id"], user_approval_ref="pytest:user-reapproved-after-context-change"
    )
    assert companion.operating.status()["program_alignment"]["aligned"] is True
    assert restored["current_revision_id"] == draft["id"]


def test_production_failure_is_system_degraded_and_cannot_be_hidden_as_no_action(tmp_path:Path,monkeypatch:pytest.MonkeyPatch):
    companion,_=setup_operating_system(tmp_path)
    monkeypatch.setattr(companion,"production_health",lambda:{"ok":False,"applicable":True,"incidents":[{"check":"critical_pipelines_latest_run_succeeded"}]})
    today=companion.operating.today()
    assert today["mode"]=="system_degraded"
    assert today["production_health"]["ok"] is False
    source=companion.inbox_add(source="production_doctor",title="Pipeline failure",content="fixture")
    with pytest.raises(CompanionError,match="system_degraded cannot be published as no_action"):
        companion.operating.brief_prepare(brief_type="daily",period_key="degraded",as_of=iso(),conclusion="no_action",payload={"summary":"No action","what_changed":[],"decision":"Wait","risks":["pipeline failed"],"next_check_at":iso(utc_now()+timedelta(hours=1)),"queue_item_ids":[]},source_refs=[source["id"]])


def test_v5_superseded_decision_invalidates_existing_queue(tmp_path: Path):
    companion, fixture = setup_operating_system(tmp_path)
    built = build_actionable_opportunity(companion, fixture)
    queue = companion.operating.queue_enqueue(
        built["opportunity"]["id"],
        decision_revision_id=built["decision_revision"]["id"],
    )
    old_revision = built["decision_revision"]
    companion.cognition.publish(
        old_revision["object_id"],
        "# Revised fixture Decision\n\nThe prior action card is no longer current.",
        knowledge_cutoff=iso(),
        context_refs=old_revision["context_refs"],
        calculation_ids=old_revision["calculation_ids"],
        metadata={
            **old_revision["metadata"],
            "valid_until": iso(utc_now() + timedelta(hours=7)),
        },
    )
    today = companion.operating.today()
    assert today["mode"] == "review_required"
    assert today["invalidated_queue"][0]["queue_id"] == queue["id"]
    expired = companion.operating.queue_get(queue["id"])
    assert expired["state"] == "expired"
    assert "Decision invalidated" in expired["response_reason"]


def test_v5_confirmed_ledger_change_invalidates_existing_action_card(tmp_path: Path):
    companion, fixture = setup_operating_system(tmp_path)
    built = build_actionable_opportunity(companion, fixture)
    queue = companion.operating.queue_enqueue(
        built["opportunity"]["id"],
        decision_revision_id=built["decision_revision"]["id"],
    )
    deposit = companion.financial.ledger_add(
        account_id=fixture["account"]["id"],
        entry_type="cash_deposit",
        occurred_at=iso(),
        amount="1000",
        currency="CNY",
        source="ledger-drift-fixture",
    )
    companion.financial.ledger_confirm(deposit["id"])

    today = companion.operating.today()

    assert today["mode"] == "review_required"
    assert today["invalidated_queue"][0]["queue_id"] == queue["id"]
    expired = companion.operating.queue_get(queue["id"])
    assert expired["state"] == "expired"
    assert "confirmed Ledger has changed" in expired["response_reason"]


def test_v5_latest_market_price_rechecks_risk_before_presenting_action(tmp_path: Path, monkeypatch):
    companion, fixture = setup_operating_system(tmp_path)
    built = build_actionable_opportunity(companion, fixture)
    queue = companion.operating.queue_enqueue(
        built["opportunity"]["id"],
        decision_revision_id=built["decision_revision"]["id"],
    )
    later = utc_now() + timedelta(seconds=2)
    companion.financial.market_add(
        built["asset"]["id"],
        "close",
        "11",
        iso(later),
        "newer-market-fixture",
        "healthy",
        "CNY",
    )
    monkeypatch.setattr("companion.actionability.utc_now", lambda: later + timedelta(seconds=1))

    today = companion.operating.today()

    assert today["mode"] == "review_required"
    assert today["invalidated_queue"][0]["queue_id"] == queue["id"]
    expired = companion.operating.queue_get(queue["id"])
    assert expired["state"] == "expired"
    assert "price_out_of_range" in expired["response_reason"]


def test_execution_lifecycle_separates_acceptance_order_report_and_confirmed_fill(tmp_path: Path):
    companion, fixture = setup_operating_system(tmp_path)
    built = build_actionable_opportunity(companion, fixture)
    queue = accept_action_card(companion, built)
    before = companion.financial.portfolio_state(iso(), fixture["account"]["id"])

    execution = companion.investment_commands.execution_update(
        operation="prepare",
        queue_id=queue["id"],
        idempotency_key="execution-lifecycle-fixture",
    )
    assert execution["status"] == "proposed"
    assert companion.system_status()["counts"]["executions"] == 1
    assert companion.financial.portfolio_state(iso(), fixture["account"]["id"])[
        "positions"
    ] == before["positions"]

    execution = companion.investment_commands.execution_update(
        operation="order",
        execution_id=execution["id"],
        broker_order_ref="broker-order-fixture-1",
        ordered_at=iso(),
    )
    assert execution["status"] == "ordered"
    reported = companion.investment_commands.execution_update(
        operation="report_fill",
        execution_id=execution["id"],
        occurred_at=iso(),
        quantity="100",
        price="10",
        fee="5",
        source="user-confirmed-broker-report",
        external_id="broker-fill-fixture-1",
        final=True,
    )
    pending = reported["pending_ledger_entry"]
    assert reported["portfolio_changed"] is False
    assert pending["status"] == "needs_confirmation"
    assert companion.financial.portfolio_state(iso(), fixture["account"]["id"])[
        "positions"
    ] == before["positions"]

    confirmed = companion.investment_commands.execution_update(
        operation="confirm_fill",
        execution_id=execution["id"], entry_id=pending["id"], final=True
    )

    assert confirmed["execution"]["status"] == "filled"
    assert confirmed["confirmed_ledger_entry"]["status"] == "confirmed"
    assert confirmed["portfolio_changed"] is True
    assert confirmed["portfolio"]["positions"][0]["quantity"] == "100"
    assert companion.operating.queue_get(queue["id"])["state"] == "closed"
    decision_context = companion.investment.decision_context()
    assert decision_context["executions"][0]["id"] == execution["id"]
    assert decision_context["execution_boundary"][
        "reported_fill_changes_portfolio"
    ] is False


def test_public_ordered_execution_caps_only_its_account_portfolio_qualification(
    tmp_path: Path,
):
    companion, fixture = setup_operating_system(tmp_path)
    built = build_actionable_opportunity(companion, fixture)
    queue = accept_action_card(companion, built)
    checked_at = iso()
    companion.financial.reconcile(
        fixture["account"]["id"],
        checked_at,
        {
            "cash": {"CNY": "100000"},
            "positions": {},
            "position_values": {},
            "position_total_by_currency": {"CNY": "0"},
            "total_by_currency": {"CNY": "100000"},
        },
        "portfolio-qualification-primary-statement",
    )
    execution = companion.investment_commands.execution_update(
        operation="prepare",
        queue_id=queue["id"],
        idempotency_key="portfolio-qualification-open-execution",
    )
    companion.investment_commands.execution_update(
        operation="order",
        execution_id=execution["id"],
        broker_order_ref="portfolio-qualification-broker-order",
        ordered_at=checked_at,
    )
    other = companion.financial.account_create("Other account", "CNY")
    other_opening = companion.financial.ledger_add(
        account_id=other["id"],
        entry_type="opening_balance",
        occurred_at=iso(utc_now() - timedelta(days=1)),
        amount="500",
        currency="CNY",
        source="portfolio-qualification-other-fixture",
    )
    companion.financial.ledger_confirm(other_opening["id"])
    companion.financial.reconcile(
        other["id"],
        checked_at,
        {
            "cash": {"CNY": "500"},
            "positions": {},
            "position_values": {},
            "position_total_by_currency": {"CNY": "0"},
            "total_by_currency": {"CNY": "500"},
        },
        "portfolio-qualification-other-statement",
    )

    primary = companion.investment.portfolio_context(
        account_id=fixture["account"]["id"], as_of=checked_at
    )
    isolated = companion.investment.portfolio_context(
        account_id=other["id"], as_of=checked_at
    )

    assert primary["portfolio_qualification"]["level"] == "range_ready"
    assert primary["portfolio_qualification"]["reason_codes"] == ["open_executions"]
    assert primary["truth_freshness"]["open_execution_count"] == 1
    assert primary["truth_freshness"]["status"] == "open_execution_preflight_required"
    assert primary["precision_boundary"]["precise_position_advice_allowed"] is False
    assert isolated["portfolio_qualification"]["level"] == "preflight_ready"
    assert isolated["truth_freshness"]["open_execution_count"] == 0


def test_active_broker_strategy_caps_account_portfolio_qualification(tmp_path: Path):
    companion, fixture = setup_operating_system(tmp_path)
    builder = lambda account_id, asset_id: {
        "plan_type": "priced_buy",
        "spec": _plan_spec("priced_buy", account_id, asset_id),
    }
    built = build_actionable_opportunity(companion, fixture, builder)
    queue = accept_action_card(companion, built)
    checked_at = iso()
    companion.financial.reconcile(
        fixture["account"]["id"],
        checked_at,
        {
            "cash": {"CNY": "100000"},
            "positions": {},
            "position_values": {},
            "position_total_by_currency": {"CNY": "0"},
            "total_by_currency": {"CNY": "100000"},
        },
        "portfolio-qualification-broker-strategy-statement",
    )
    spec = _plan_spec(
        "priced_buy", fixture["account"]["id"], built["asset"]["id"]
    )
    plan = companion.investment_commands.execution_update(
        operation="strategy_create",
        queue_id=queue["id"],
        plan_type="priced_buy",
        spec=spec,
        valid_until=built["valid_until"],
        idempotency_key="portfolio-qualification-broker-strategy",
    )
    companion.investment_commands.execution_update(
        operation="strategy_configured",
        plan_id=plan["id"],
        broker_condition_ref="portfolio-qualification-condition",
        configured_at=checked_at,
        broker_validity_sessions=spec["validity_sessions"],
        broker_valid_until=built["valid_until"],
    )
    companion.investment_commands.execution_update(
        operation="strategy_activate",
        plan_id=plan["id"],
        occurred_at=checked_at,
    )

    context = companion.investment.portfolio_context(
        account_id=fixture["account"]["id"], as_of=checked_at
    )

    assert context["portfolio_qualification"]["level"] == "range_ready"
    assert context["portfolio_qualification"]["reason_codes"] == [
        "active_broker_strategies"
    ]
    assert context["truth_freshness"]["active_broker_strategy_count"] == 1
    assert context["truth_freshness"]["status"] == (
        "open_execution_preflight_required"
    )


def test_execution_lifecycle_records_real_fill_but_marks_price_deviation(tmp_path: Path):
    companion, fixture = setup_operating_system(tmp_path)
    built = build_actionable_opportunity(companion, fixture)
    queue = accept_action_card(companion, built)
    execution = companion.execution.prepare_from_queue(
        queue_id=queue["id"], idempotency_key="execution-deviation-fixture"
    )
    execution = companion.execution.mark_ordered(
        execution_id=execution["id"],
        broker_order_ref="broker-order-deviation",
        ordered_at=iso(),
    )
    reported = companion.execution.report_fill(
        execution_id=execution["id"],
        occurred_at=iso(),
        quantity="100",
        price="10.5",
        fee="5",
        source="user-confirmed-broker-report",
        external_id="broker-fill-deviation",
        final=True,
    )

    result = companion.execution.confirm_fill(
        execution_id=execution["id"],
        entry_id=reported["pending_ledger_entry"]["id"],
        final=True,
    )

    assert result["execution"]["status"] == "deviated"
    assert "fill_price_outside_action_range" in result["execution"]["status_reason"]
    assert result["confirmed_ledger_entry"]["status"] == "confirmed"
    assert result["portfolio"]["positions"][0]["quantity"] == "100"


def test_execution_lifecycle_reconciles_multiple_partial_fills(tmp_path: Path):
    companion, fixture = setup_operating_system(tmp_path)
    built = build_actionable_opportunity(companion, fixture)
    queue = accept_action_card(companion, built)
    execution = companion.execution.prepare_from_queue(
        queue_id=queue["id"], idempotency_key="execution-partial-fill-fixture"
    )
    execution = companion.execution.mark_ordered(
        execution_id=execution["id"],
        broker_order_ref="broker-order-partial",
        ordered_at=iso(),
    )
    first = companion.execution.report_fill(
        execution_id=execution["id"],
        occurred_at=iso(),
        quantity="40",
        price="10",
        fee="2",
        source="broker-report",
        external_id="partial-fill-1",
    )
    first_result = companion.execution.confirm_fill(
        execution_id=execution["id"],
        entry_id=first["pending_ledger_entry"]["id"],
        final=False,
    )
    assert first_result["execution"]["status"] == "partially_filled"
    assert first_result["portfolio"]["positions"][0]["quantity"] == "40"
    assert companion.operating.queue_get(queue["id"])["state"] == "accepted"

    second = companion.execution.report_fill(
        execution_id=execution["id"],
        occurred_at=iso(),
        quantity="60",
        price="10",
        fee="3",
        source="broker-report",
        external_id="partial-fill-2",
        final=True,
    )
    completed = companion.execution.confirm_fill(
        execution_id=execution["id"],
        entry_id=second["pending_ledger_entry"]["id"],
        final=True,
    )
    assert completed["execution"]["status"] == "filled"
    assert completed["execution"]["ledger_entry_ids"] == [
        first["pending_ledger_entry"]["id"],
        second["pending_ledger_entry"]["id"],
    ]
    assert completed["portfolio"]["positions"][0]["quantity"] == "100"
    assert companion.operating.queue_get(queue["id"])["state"] == "closed"


def test_execution_truth_is_frozen_into_daily_weekly_briefs_and_investment_home(
    tmp_path: Path,
):
    companion, fixture = setup_operating_system(tmp_path)
    built = build_actionable_opportunity(companion, fixture)
    queue = accept_action_card(companion, built)
    execution = companion.execution.prepare_from_queue(
        queue_id=queue["id"], idempotency_key="briefing-pending-execution"
    )
    companion.execution.mark_ordered(
        execution_id=execution["id"],
        broker_order_ref="briefing-order",
        ordered_at=iso(),
    )
    reported = companion.execution.report_fill(
        execution_id=execution["id"],
        occurred_at=iso(),
        quantity="100",
        price="10",
        fee="5",
        source="broker-report",
        external_id="briefing-fill",
        final=True,
    )
    pending_id = reported["pending_ledger_entry"]["id"]

    home = companion.investment.home()
    assert home["state"] == "action"
    assert home["execution"]["pending_fill_entry_ids"] == [pending_id]
    assert "确认前不会改变真实组合" in home["message"]

    base_payload = {
        "summary": "The reported fill is still awaiting confirmation.",
        "what_changed": ["The user reported a broker fill."],
        "decision": "Confirm the fill against the broker statement.",
        "risks": ["An unconfirmed report is not portfolio truth."],
        "next_check_at": iso(utc_now() + timedelta(hours=6)),
        "queue_item_ids": [queue["id"]],
    }
    with pytest.raises(CompanionError, match="unknown fields"):
        companion.operating.brief_prepare(
            brief_type="daily",
            period_key="2026-08-23-spoofed",
            as_of=iso(),
            conclusion="action",
            payload={**base_payload, "execution_snapshot": {"claimed": "filled"}},
            source_refs=[built["decision_revision"]["id"]],
        )
    daily = companion.operating.brief_prepare(
        brief_type="daily",
        period_key="2026-08-23",
        as_of=iso(),
        conclusion="action",
        payload=base_payload,
        source_refs=[built["decision_revision"]["id"], queue["id"]],
    )
    snapshot = daily["payload"]["execution_snapshot"]
    assert snapshot["pending_fill_entry_ids"] == [pending_id]
    assert snapshot["portfolio_changed_by_confirmed_fills"] is False
    assert snapshot["calculation_id"] in daily["source_refs"]
    frozen = companion.financial.calculation_get(snapshot["calculation_id"])
    assert frozen["kind"] == "execution_operating_snapshot"
    assert frozen["outputs"]["pending_fill_entry_ids"] == [pending_id]

    weekly = companion.operating.brief_prepare(
        brief_type="weekly",
        period_key="2026-W34",
        as_of=iso(),
        conclusion="action",
        payload={
            **base_payload,
            "program_progress": {"state": "fill_confirmation_pending"},
            "research_pipeline": {"state": "unchanged"},
        },
        source_refs=[built["decision_revision"]["id"], queue["id"]],
    )
    assert weekly["payload"]["execution_snapshot"]["status_counts"]["ordered"] == 1
    assert weekly["payload"]["execution_snapshot"]["pending_fill_entry_ids"] == [
        pending_id
    ]


def test_today_requires_execution_review_then_allows_an_acknowledged_no_action(
    tmp_path: Path,
):
    companion, fixture = setup_operating_system(tmp_path)
    built = build_actionable_opportunity(companion, fixture)
    queue = accept_action_card(companion, built)
    execution = companion.execution.prepare_from_queue(
        queue_id=queue["id"], idempotency_key="briefing-deviated-execution"
    )
    companion.execution.mark_ordered(
        execution_id=execution["id"],
        broker_order_ref="briefing-deviated-order",
        ordered_at=iso(),
    )
    reported = companion.execution.report_fill(
        execution_id=execution["id"],
        occurred_at=iso(),
        quantity="100",
        price="10.5",
        fee="5",
        source="broker-report",
        external_id="briefing-deviated-fill",
        final=True,
    )
    confirmed = companion.execution.confirm_fill(
        execution_id=execution["id"],
        entry_id=reported["pending_ledger_entry"]["id"],
        final=True,
    )
    assert confirmed["execution"]["status"] == "deviated"

    before_brief = companion.operating.today()
    assert before_brief["mode"] == "review_required"
    assert before_brief["execution_summary"]["deviated_execution_ids"] == [
        execution["id"]
    ]
    assert "执行结果" in before_brief["message"]

    companion.operating.brief_prepare(
        brief_type="daily",
        period_key="2026-08-23-deviation-reviewed",
        as_of=iso(),
        conclusion="no_action",
        payload={
            "summary": "The real fill was recorded with its price deviation.",
            "what_changed": ["The confirmed fill closed the Action Card."],
            "decision": "No additional trade; retain the deviation for review.",
            "risks": ["The original execution range was exceeded."],
            "next_check_at": iso(utc_now() + timedelta(hours=6)),
            "queue_item_ids": [],
        },
        source_refs=[built["decision_revision"]["id"]],
    )
    acknowledged = companion.investment.home()
    assert acknowledged["state"] == "no_action"
    assert acknowledged["execution"]["deviated_execution_ids"] == [execution["id"]]

    mismatch = companion.financial.reconcile(
        fixture["account"]["id"],
        iso(),
        {"cash": {"CNY": "999999"}, "positions": {}},
        "broker-statement-with-difference",
    )
    assert mismatch["status"] == "needs_review"
    reconciliation_today = companion.operating.today()
    assert reconciliation_today["mode"] == "review_required"
    assert reconciliation_today["execution_summary"]["reconciliation"][
        "needs_review_ids"
    ] == [mismatch["id"]]
    assert "账户对账结果存在差异" in reconciliation_today["message"]


def test_v5_unvalidated_signal_with_official_evidence_can_only_qualify_bounded(tmp_path: Path):
    companion, _fixture = setup_operating_system(tmp_path)
    cutoff = iso()
    signal = companion.data.manifest_publish(
        kind="v5_unvalidated_signal",
        schema_version="research-evidence/v1",
        manifest={
            "source": "candidate scan",
            "source_group": "quant-signal",
            "first_known_at": cutoff,
            "observed_at": cutoff,
            "signals": [{"validation_status": "unvalidated"}],
        },
    )
    filing = companion.data.manifest_publish(
        kind="investment_evidence",
        schema_version="research-evidence/v1",
        manifest={
            "source": "issuer filing",
            "source_group": "issuer",
            "evidence_type": "official_disclosure",
            "first_known_at": cutoff,
            "observed_at": cutoff,
        },
    )
    research = companion.investment_commands.research_publish(
        subject={"asset_id": "fixture:unvalidated"},
        content="# Thesis\nThe candidate is still provisional.",
        evidence_manifest_ids=[signal["id"], filing["id"]],
        knowledge_cutoff=cutoff,
        validation_spec=research_validation_spec(),
    )
    opportunity = companion.operating.opportunity_create(
        subject={"asset_id": "fixture:unvalidated"},
        evidence_refs=[signal["id"]],
        reason="Provisional signal requires research",
        thesis_id=research["thesis"]["id"],
    )
    opportunity = companion.operating.opportunity_transition(
        opportunity["id"],
        expected_version=1,
        to_stage="researching",
        to_status="active",
        evidence_refs=[signal["id"], filing["id"]],
        reason="Research completed but formal validation still fails",
    )

    qualified = companion.operating.opportunity_transition(
        opportunity["id"],
        expected_version=2,
        to_stage="qualified",
        to_status="active",
        evidence_refs=[
            signal["id"],
            filing["id"],
            research["validation"]["calculation_id"],
        ],
        qualification={
            "validation_calculation_id": research["validation"]["calculation_id"],
            "major_unknowns": ["predictive evidence remains unvalidated"],
            "decision_basis": "Only a separately capped conditional action may proceed.",
        },
        reason="Bounded research qualification",
    )
    assert qualified["stage"] == "qualified"
    assert qualified["evidence_band"] == "eligible_for_bounded_action"


def test_bounded_research_can_publish_only_a_capped_conditional_decision(tmp_path: Path):
    companion, fixture = setup_operating_system(tmp_path)
    cutoff = iso()
    asset = companion.financial.asset_upsert(
        "stock", "Bounded Candidate", "CNY", {"ts_code": "600001.SH"}
    )
    first = companion.data.manifest_publish(
        kind="investment_evidence",
        schema_version="research-evidence/v1",
        manifest={
            "source": "issuer filing",
            "source_group": "issuer",
            "evidence_type": "official_disclosure",
            "first_known_at": cutoff,
            "observed_at": cutoff,
        },
    )
    duplicate = companion.data.manifest_publish(
        kind="investment_evidence",
        schema_version="research-evidence/v1",
        manifest={
            "source": "exchange copy of issuer filing",
            "source_group": "issuer",
            "evidence_type": "official_disclosure",
            "first_known_at": cutoff,
            "observed_at": cutoff,
        },
    )
    evidence_ids = [first["id"], duplicate["id"]]
    research = companion.investment_commands.research_publish(
        subject={"asset_id": asset["id"]},
        content="# Bounded thesis\nIndependent corroboration is still pending.",
        evidence_manifest_ids=evidence_ids,
        knowledge_cutoff=cutoff,
        validation_spec=research_validation_spec(),
    )
    assert research["validation"]["status"] == "eligible_for_bounded_action"
    opportunity = companion.operating.opportunity_create(
        subject={"asset_id": asset["id"]},
        evidence_refs=[first["id"]],
        reason="Bounded candidate",
        thesis_id=research["thesis"]["id"],
    )
    opportunity = companion.operating.opportunity_transition(
        opportunity["id"],
        expected_version=1,
        to_stage="researching",
        to_status="active",
        evidence_refs=evidence_ids,
        reason="Research the bounded candidate",
    )
    bounded_qualification = {
        "validation_calculation_id": research["validation"]["calculation_id"],
        "major_unknowns": ["independent corroboration remains pending"],
        "decision_basis": "Only the capped conditional lane may proceed.",
    }
    opportunity = companion.operating.opportunity_transition(
        opportunity["id"],
        expected_version=2,
        to_stage="qualified",
        to_status="active",
        evidence_refs=[*evidence_ids, research["validation"]["calculation_id"]],
        qualification=bounded_qualification,
        reason="Bounded validation passed",
    )
    market = companion.financial.market_add(
        asset["id"], "close", "10", cutoff, "fixture-market", "healthy", "CNY"
    )
    valid_until = iso(utc_now() + timedelta(days=7))
    risk = companion.investment_commands.risk_assess(
        as_of=cutoff,
        account_id=fixture["account"]["id"],
        asset_id=asset["id"],
        quantity="100",
        price="10",
        reality_spec=ashare_reality(),
        market_snapshot_id=market["id"],
        max_market_age_seconds=3600,
        valid_until=valid_until,
        price_range={"min": "9", "max": "10.2"},
        action_tier="bounded",
        validity_sessions=5,
    )
    assert risk["status"] == "pass"
    with pytest.raises(CompanionError, match="not eligible for decision"):
        companion.investment_commands.decision_publish(
            subject={"asset_id": asset["id"]},
            content="# Invalid standard action",
            decision_kind="action",
            account_id=fixture["account"]["id"],
            as_of=cutoff,
            knowledge_cutoff=cutoff,
            valid_until=valid_until,
            thesis_revision_ids=[research["revision"]["id"]],
            evidence_manifest_ids=evidence_ids,
            invalidators=["independent evidence contradicts the thesis"],
            no_action={"choice": "wait for corroboration"},
            alternatives=[{"choice": "hold cash"}],
            risk_calculation_id=risk["calculation_id"],
            research_validation_calculation_id=research["validation"]["calculation_id"],
        )
    decision = companion.investment_commands.decision_publish(
        subject={"asset_id": asset["id"]},
        content="# Conditional action\nOnly the capped risk lane is authorized.",
        decision_kind="conditional_action",
        account_id=fixture["account"]["id"],
        as_of=cutoff,
        knowledge_cutoff=cutoff,
        valid_until=valid_until,
        thesis_revision_ids=[research["revision"]["id"]],
        evidence_manifest_ids=evidence_ids,
        invalidators=["independent evidence contradicts the thesis"],
        no_action={"choice": "wait for corroboration"},
        alternatives=[{"choice": "hold cash"}],
        risk_calculation_id=risk["calculation_id"],
        research_validation_calculation_id=research["validation"]["calculation_id"],
        execution_plan={
            "plan_type": "priced_buy",
            "spec": {
                "account_id": fixture["account"]["id"],
                "asset_id": asset["id"],
                "validity_sessions": 5,
                "monitoring_window": None,
                "trigger": {"direction": "cross_down", "monitor_price": "9"},
                "order": _condition_order(),
                "quantity": "100",
            },
        },
    )
    assert decision["revision"]["metadata"]["action_tier"] == "bounded"
    opportunity = companion.operating.opportunity_transition(
        opportunity["id"],
        expected_version=3,
        to_stage="actionable",
        to_status="active",
        evidence_refs=[
            *evidence_ids,
            research["validation"]["calculation_id"],
            risk["calculation_id"],
            decision["revision"]["id"],
        ],
        qualification=bounded_qualification,
        decision_revision_id=decision["revision"]["id"],
        reason="Capped conditional action is ready for user review",
    )
    queue = accept_action_card(
        companion,
        {"opportunity": opportunity, "decision_revision": decision["revision"]},
    )
    spec = decision["revision"]["metadata"]["execution_plan"]["spec"]
    plan = companion.execution_strategy.create_from_queue(
        queue_id=queue["id"],
        plan_type="priced_buy",
        spec=spec,
        valid_until=valid_until,
        idempotency_key="bounded-priced-buy",
    )
    assert plan["status"] == "draft"
    assert plan["spec"]["validity_sessions"] == 5


def test_v5_raw_decision_cannot_bypass_professional_action_gates(tmp_path: Path):
    companion, fixture = setup_operating_system(tmp_path)
    state = companion.financial.portfolio_state(iso(), fixture["account"]["id"])
    thesis = companion.cognition.object_create("thesis", {"asset_id": "fixture:raw"})
    thesis_revision = companion.cognition.publish(
        thesis["id"], "# Raw thesis\nA fixture thesis without formal validation."
    )
    decision = companion.cognition.object_create("decision", {"asset_id": "fixture:raw"})
    revision = companion.cognition.publish(
        decision["id"],
        "# Raw decision\nThis revision did not pass Research Validation or Risk Gate.",
        context_refs={
            "investor_revision_id": fixture["contexts"]["investor_revision_id"],
            "mandate_revision_id": fixture["contexts"]["mandate_revision_id"],
            "portfolio_calculation_id": state["calculation_id"],
            "thesis_revision_ids": [thesis_revision["id"]],
        },
        calculation_ids=[state["calculation_id"]],
        metadata={
            "valid_until": iso(utc_now() + timedelta(hours=1)),
            "invalidators": ["facts change"],
            "no_action": {"choice": "hold cash"},
        },
    )

    with pytest.raises(CompanionError, match="supported professional contract"):
        companion.actionability.validate_decision(revision["id"])


def test_v5_scorecard_values_can_only_come_from_calculations(tmp_path: Path):
    companion, fixture = setup_operating_system(tmp_path)
    state = companion.financial.portfolio_state(iso(), fixture["account"]["id"])
    period_start = iso(utc_now() - timedelta(days=30))
    period_end = iso()
    operating_metrics = companion.operating.program_metrics_calculate(
        period_start=period_start,
        period_end=period_end,
    )
    assert operating_metrics["opportunity_flow"]["observed"] == 0
    assert operating_metrics["coverage"]["portfolio_return"]["status"] == "insufficient_evidence"
    scorecard = companion.operating.scorecard_publish(
        period_start=period_start,
        period_end=period_end,
        metrics=[
            {
                "name": "ending_cash_cny",
                "calculation_id": state["calculation_id"],
                "output_path": "outputs.total_by_currency.CNY",
            },
            {
                "name": "observed_opportunities",
                "calculation_id": operating_metrics["calculation_id"],
                "output_path": "outputs.opportunity_flow.observed",
            },
        ],
        comparisons=[],
        source_refs=[state["calculation_id"]],
        caveats=["This fixture records an ending value, not a profitability claim."],
    )
    assert scorecard["metrics"][0]["value"] == "100000"
    assert scorecard["metrics"][0]["calculation_id"] == state["calculation_id"]
    assert scorecard["metrics"][1]["value"] == 0
    with pytest.raises(CompanionError, match="exactly name, calculation_id, output_path"):
        companion.operating.scorecard_publish(
            period_start=iso(utc_now() - timedelta(days=60)),
            period_end=iso(utc_now() - timedelta(days=31)),
            metrics=[
                {
                    "name": "invented_return",
                    "calculation_id": state["calculation_id"],
                    "output_path": "outputs.total_by_currency.CNY",
                    "value": "999%",
                }
            ],
            comparisons=[],
            source_refs=[state["calculation_id"]],
            caveats=[],
        )
    with pytest.raises(CompanionError, match="Calculation as_of must fall within"):
        companion.operating.scorecard_publish(
            period_start=iso(utc_now() - timedelta(days=60)),
            period_end=iso(utc_now() - timedelta(days=31)),
            metrics=[
                {
                    "name": "out_of_period_cash",
                    "calculation_id": state["calculation_id"],
                    "output_path": "outputs.total_by_currency.CNY",
                }
            ],
            comparisons=[],
            source_refs=[state["calculation_id"]],
            caveats=[],
        )


def test_program_metrics_uses_exact_account_performance_instead_of_claiming_missing_returns(tmp_path: Path):
    companion, fixture = setup_operating_system(tmp_path)
    period_start = iso(utc_now() - timedelta(days=1))
    period_end = iso()
    performance = companion.performance.calculate_period(
        account_id=fixture["account"]["id"],
        period_start=period_start,
        period_end=period_end,
        start_prices={},
        end_prices={},
        benchmark_start_value="100",
        benchmark_end_value="101",
        source_refs=["fixture-benchmark"],
    )

    metrics = companion.operating.program_metrics_calculate(
        period_start=period_start,
        period_end=period_end,
    )
    portfolio_context = companion.investment.portfolio_context()

    assert metrics["coverage"]["portfolio_return"] == {
        "status": "ready",
        "calculation_id": performance["calculation_id"],
        "output_path": "outputs.modified_dietz_return",
        "value": "0",
    }
    assert metrics["coverage"]["benchmark_return"]["status"] == "ready"
    assert metrics["coverage"]["transaction_cost"]["value"] == "0"
    calculation = companion.financial.calculation_get(metrics["calculation_id"])
    assert calculation["inputs"]["performance_calculation_ids"] == [
        performance["calculation_id"]
    ]
    assert portfolio_context["account"]["id"] == fixture["account"]["id"]
    assert portfolio_context["policy"]["program_id"] == fixture["program"]["id"]


def test_wake_envelope_claims_exact_work_and_requires_run_completion(tmp_path: Path, monkeypatch):
    companion = Companion(tmp_path, gate_scope="test_fixture")
    companion.initialize()
    schedule = companion.schedule_create(
        name="wake fixture",
        kind="one_shot",
        mission="verify exact wake handoff",
        cadence={"type": "one_shot", "at": iso(utc_now() + timedelta(days=1))},
    )
    run = companion.schedule_run_now(schedule["id"])
    monkeypatch.setenv("COMPANION_CC_WAKE_CRON", "fixture-cron")
    monkeypatch.setattr(
        "companion.platform.outbox.subprocess.run",
        lambda *args, **kwargs: SimpleNamespace(returncode=0, stdout="triggered", stderr=""),
    )
    dispatch = companion.dispatch_outbox(limit=1)
    assert dispatch["ok"] is True and dispatch["results"][0]["signaled"] is True
    assert companion.outbox_list()[0]["status"] == "sending"
    wake = companion.wake_claim("pytest-session")
    assert wake["envelope"]["type"] == "scheduled_run"
    assert wake["envelope"]["run_id"] == run["id"]
    assert wake["run"]["status"] == "leased"
    with pytest.raises(CompanionError, match="complete the parent Run"):
        companion.wake_complete(wake["outbox_id"], "pytest-session", True)
    companion.complete_run(run["id"], True)
    completed = companion.wake_complete(wake["outbox_id"], "pytest-session", True)
    assert completed["status"] == "sent"
    direct_schedule = companion.schedule_create(
        name="direct wake fixture",
        kind="one_shot",
        mission="verify direct transport still uses the claim handshake",
        cadence={"type": "one_shot", "at": iso(utc_now() + timedelta(days=1))},
    )
    direct_run = companion.schedule_run_now(direct_schedule["id"])
    monkeypatch.delenv("COMPANION_CC_WAKE_CRON")
    direct_dispatch = companion.dispatch_outbox(limit=1)
    assert direct_dispatch["results"][0]["transport"] == "direct"
    direct_wake = companion.wake_claim("pytest-direct")
    assert direct_wake["envelope"]["run_id"] == direct_run["id"]
    failed = companion.wake_complete(
        direct_wake["outbox_id"], "pytest-direct", False, "fixture session failed"
    )
    assert failed["status"] == "retry"
    assert companion.run_get(direct_run["id"])["status"] == "recoverable"


def test_beta_feature_generates_evidence_without_weakening_full_release(tmp_path: Path):
    companion = Companion(tmp_path, gate_scope="test_fixture")
    companion.initialize()
    for gate in ("G0", "G1", "G2", "G3", "G4"):
        pass_gate(companion, gate)
    with pytest.raises(CompanionError, match="user_opt_in_ref"):
        companion.jobs.feature_set("v4_decision_support_beta", True, config={}, reason="invalid fixture")
    beta = companion.jobs.feature_set(
        "v4_decision_support_beta",
        True,
        config={
            "user_opt_in_ref": "pytest:beta-opt-in",
            "expires_at": iso(utc_now() + timedelta(days=7)),
        },
        reason="bounded beta fixture",
    )
    assert beta["enabled"] is True
    assert companion.jobs.decision_support_require()["key"] == "v4_decision_support_beta"
    assert companion.jobs.feature_get("v4_decision_support")["enabled"] is False
    with companion.db.transaction() as con:
        con.execute(
            "UPDATE feature_flags SET config_json=? WHERE key='v4_decision_support_beta'",
            (canonical({"user_opt_in_ref": "pytest:beta-opt-in", "expires_at": iso(utc_now() - timedelta(seconds=1))}),),
        )
    with pytest.raises(CompanionError, match="beta has expired"):
        companion.jobs.decision_support_require()


def test_schema4_to_schema5_is_explicit_and_preserves_v3_schedules(tmp_path: Path):
    root = tmp_path / "workspace"
    root.mkdir()
    db_path = root / ".state" / "companion.db"
    db_path.parent.mkdir()
    con = sqlite3.connect(db_path)
    con.executescript(SCHEMA)
    con.executescript(V3_SCHEMA)
    con.executescript(MIGRATION_TABLE_SCHEMA)
    con.execute("INSERT INTO meta(key,value) VALUES('schema_version','3')")
    con.execute(
        "INSERT INTO schedules(id,name,kind,status,mission,cadence_json,next_run_at,created_at,updated_at) "
        "VALUES('sch_legacy','Legacy task','review','active','preserve me','{\"type\":\"interval\",\"seconds\":86400}',NULL,?,?)",
        (iso(), iso()),
    )
    con.executescript(MIGRATION_004_SQL)
    con.execute(
        "INSERT INTO schema_migrations(migration_id,version,checksum,applied_at) VALUES(?,?,?,?)",
        (BASELINE_MIGRATION_ID, 3, BASELINE_CHECKSUM, iso()),
    )
    con.execute(
        "INSERT INTO schema_migrations(migration_id,version,checksum,applied_at) VALUES(?,?,?,?)",
        (MIGRATION_004_ID, 4, MIGRATION_004_CHECKSUM, iso()),
    )
    con.execute("UPDATE meta SET value='4' WHERE key='schema_version'")
    con.commit()
    con.close()
    companion = Companion(root, gate_scope="test_fixture")
    with pytest.raises(RuntimeError, match=f"requires explicit migration to {SCHEMA_VERSION}"):
        companion.initialize()
    result = companion.migrate(tmp_path / "backups")
    assert result["schema_version"] == str(SCHEMA_VERSION)
    assert companion.schedule_get("sch_legacy")["mission"] == "preserve me"
    assert companion.schedule_get("sch_legacy")["dispatch_type"] == "codex_turn"
    assert len(companion.system_status()["migrations"]) == 8


def _condition_order(price_instruction="instant"):
    return {"price_type":"limit","price_instruction":price_instruction,"custom_price":None}


def _grid_spec(account_id,asset_id):
    return {"account_id":account_id,"asset_id":asset_id,"validity_sessions":20,"monitoring_window":None,"initial_reference_price":"10","spacing_type":"difference","rise_sell_spacing":"1","fall_buy_spacing":"1","sell_order":_condition_order(),"buy_order":_condition_order(),"sell_quantity":"100","buy_quantity":"100","price_range":{"lower":"8","upper":"12","out_of_range_behavior":"sleep"},"position_range":{"max_net_buy":"100","max_net_sell":"100"},"multiple_grid_order":True}


def _plan_spec(kind,account_id,asset_id):
    common={"account_id":account_id,"asset_id":asset_id,"validity_sessions":20,"monitoring_window":None}
    if kind=="priced_buy":return {**common,"trigger":{"direction":"cross_down","monitor_price":"9"},"order":_condition_order(),"quantity":"100"}
    if kind=="priced_sell":return {**common,"trigger":{"direction":"cross_up","monitor_price":"11"},"order":_condition_order(),"quantity":"100"}
    if kind=="bracket_exit":return {**common,"base_price":"10","take_profit":{"mode":"percentage","value":"5"},"stop_loss":{"mode":"percentage","value":"3"},"order":_condition_order(),"quantity":"100"}
    return _grid_spec(account_id,asset_id)


def test_cicc_four_condition_order_specs_are_frozen_without_invented_trigger_direction(tmp_path: Path):
    companion, fixture = setup_operating_system(tmp_path)
    asset=companion.financial.asset_upsert(asset_type="etf",name="Fixture ETF",currency="CNY",identifiers={"ts_code":"510300.SH"})
    account_id, asset_id = fixture["account"]["id"], asset["id"]
    common={"account_id":account_id,"asset_id":asset_id,"validity_sessions":20,"monitoring_window":None}
    specs={
        "priced_buy":{**common,"trigger":{"direction":"cross_up","monitor_price":"10"},"order":_condition_order(),"quantity":"100","effective_trigger_band_pct":"2","delay_confirmation":{"mode":"consecutive","count":4}},
        "priced_sell":{**common,"trigger":{"direction":"cross_down","monitor_price":"9"},"order":_condition_order(),"quantity":"100"},
        "bracket_exit":{**common,"base_price":"10","take_profit":{"mode":"percentage","value":"5"},"stop_loss":{"mode":"percentage","value":"3"},"order":_condition_order(),"quantity":"100","delay_confirmation":{"mode":"cumulative","count":4,"separate_take_profit_stop_loss_counters":True}},
        "moving_grid":_grid_spec(account_id,asset_id),
    }
    normalized={kind:companion.execution_strategy._normalize_spec(kind,spec) for kind,spec in specs.items()}
    assert normalized["priced_buy"]["trigger"]["direction"]=="cross_up"
    assert normalized["priced_sell"]["trigger"]["direction"]=="cross_down"
    assert normalized["bracket_exit"]["delay_confirmation"]["separate_take_profit_stop_loss_counters"] is True
    grid=normalized["moving_grid"]["broker_semantics"]
    assert grid["reference_update"]=="trigger_driven"
    assert grid["no_reference_update_rejections"]==["insufficient_cash","insufficient_holdings"]
    assert grid["termination_keeps_triggered_unfilled_orders"] is True
    assert grid["etf_dividend"]=="automatic_grid_termination"
    assert grid["terminate_and_liquidate_failure"]=="broker_unspecified_manual_reconciliation_required"


def test_standard_grid_action_card_blocks_when_current_sell_gate_blocks(tmp_path: Path, monkeypatch):
    companion, fixture = setup_operating_system(tmp_path)
    built = build_actionable_opportunity(
        companion,
        fixture,
        lambda account_id, asset_id: {
            "plan_type": "moving_grid",
            "spec": _grid_spec(account_id, asset_id),
        },
        asset_type="etf",
    )
    original = companion.investment_commands.risk_assess
    calls = {"count": 0}

    def block_second_gate(**kwargs):
        calls["count"] += 1
        result = original(**kwargs)
        if calls["count"] == 2:
            return {
                **result,
                "status": "blocked",
                "blocked": True,
                "violations": [{"rule": "fixture_sell_gate_block"}],
            }
        return result

    monkeypatch.setattr(companion.investment_commands, "risk_assess", block_second_gate)
    card = companion.actionability.revalidate_generic_action(
        built["decision_revision"]["id"]
    )
    assert card["executable"] is False
    assert "grid_sell:fixture_sell_gate_block" in card["reasons"]


def test_grid_lifecycle_preserves_customer_service_reference_and_termination_rules(tmp_path: Path):
    companion, fixture = setup_operating_system(tmp_path)
    built=build_actionable_opportunity(companion,fixture,lambda account_id,asset_id:{"plan_type":"moving_grid","spec":_grid_spec(account_id,asset_id)},asset_type="etf");queue=accept_action_card(companion,built)
    card=companion.operating.queue_card(queue["id"]);action=card["action"]
    spec=_grid_spec(action["account_id"],action["asset_id"])
    plan=companion.execution_strategy.create_from_queue(queue_id=queue["id"],plan_type="moving_grid",spec=spec,valid_until=built["valid_until"],idempotency_key="grid-plan")
    companion.execution_strategy.mark_configured(plan_id=plan["id"],broker_condition_ref="cicc-grid-1",configured_at=iso(),broker_validity_sessions=spec["validity_sessions"],broker_valid_until=built["valid_until"])
    companion.execution_strategy.activate(plan_id=plan["id"],occurred_at=iso())
    unchanged=companion.execution_strategy.report_order(plan_id=plan["id"],broker_order_ref="order-rejected",side="buy",quantity="100",status="rejected",triggered_at=iso(),reference_price_before="10",rejection_reason="insufficient_cash")
    assert unchanged["orders"][0]["reference_update_reason"]=="unchanged_insufficient_resources"
    with pytest.raises(CompanionError,match="reference update"):
        companion.execution_strategy.report_order(plan_id=plan["id"],broker_order_ref="order-other",side="sell",quantity="100",status="submitted",triggered_at=iso(),reference_price_before="10")
    trigger_time=iso()
    active=companion.execution_strategy.report_order(plan_id=plan["id"],broker_order_ref="order-live",side="sell",quantity="100",status="submitted",triggered_at=trigger_time,reference_price_before="10",reference_price_after="11")
    assert active["net_quantities"]["net_sell"]=="100"
    linked_execution_id=active["reported_order_execution_id"]
    with companion.db.transaction() as con:con.execute("UPDATE broker_managed_orders SET execution_id=NULL,execution_link_state='pending' WHERE broker_order_ref='order-live'")
    live_order=next(item for item in active["orders"] if item["broker_order_ref"]=="order-live")
    assert companion.execution_strategy.recover_execution_links()==[live_order["id"]]
    assert next(item for item in companion.execution_strategy.get(plan["id"])["orders"] if item["broker_order_ref"]=="order-live")["execution_id"]==linked_execution_id
    event_count=len(active["events"])
    duplicate=companion.execution_strategy.report_order(plan_id=plan["id"],broker_order_ref="order-live",side="sell",quantity="100",status="submitted",triggered_at=trigger_time)
    assert len(duplicate["events"])==event_count
    partial=companion.execution_strategy.report_order(plan_id=plan["id"],broker_order_ref="order-live",side="sell",quantity="100",status="partially_filled",triggered_at=iso(),cancelled_quantity="40")
    assert partial["net_quantities"]["net_sell"]=="60"
    with pytest.raises(CompanionError,match="cannot regress"):
        companion.execution_strategy.report_order(plan_id=plan["id"],broker_order_ref="order-live",side="sell",quantity="100",status="submitted",triggered_at=iso(),cancelled_quantity="40")
    terminated=companion.execution_strategy.report_etf_dividend_termination(plan_id=plan["id"],occurred_at=iso(),corporate_action_ref="etf-dividend-2026")
    assert terminated["status"]=="terminated"
    event=next(item for item in terminated["events"] if item["event_type"]=="terminated")
    assert event["payload"]["live_orders_remain"]==1
    assert event["payload"]["broker_does_not_auto_cancel_triggered_unfilled_orders"] is True
    assert plan["id"] in companion.briefing.projection(program_id=fixture["program"]["id"])["broker_strategy_attention_ids"]
    with pytest.raises(CompanionError,match="live or unknown"):
        companion.execution_strategy.reconcile(plan_id=plan["id"],occurred_at=iso(),reconciliation_id="statement-before-order-final")
    cancelled=companion.execution_strategy.report_order(plan_id=plan["id"],broker_order_ref="order-live",side="sell",quantity="100",status="cancelled",triggered_at=iso(),cancelled_quantity="40")
    execution_id=cancelled["reported_order_execution_id"]
    old_as_of=iso(utc_now()+timedelta(seconds=1));companion.financial.market_add(action["asset_id"],"close","10",iso(),"old-broker-statement")
    old_state=companion.financial.portfolio_state(old_as_of,action["account_id"]);old_positions={item["asset_id"]:item["quantity"] for item in old_state["positions"]};old_values={item["asset_id"]:item["market_value"] for item in old_state["positions"]};old_total=sum(Decimal(value) for value in old_state["cash"].values())+sum(Decimal(value) for value in old_values.values())
    old_reconciliation=companion.financial.reconcile(action["account_id"],old_as_of,{"cash":old_state["cash"],"positions":old_positions,"position_values":old_values,"position_total_by_currency":{"CNY":str(sum(Decimal(value) for value in old_values.values()))},"total_by_currency":{"CNY":str(old_total)}},"broker-statement-before-fill")
    fill=companion.execution.report_fill(execution_id=execution_id,occurred_at=iso(),quantity="60",price="11",fee="1",source="broker-statement",external_id="grid-fill-60",final=True)
    companion.execution.confirm_fill(execution_id=execution_id,entry_id=fill["pending_ledger_entry"]["id"],final=True)
    with pytest.raises(CompanionError,match="does not match the current confirmed Ledger"):
        companion.execution_strategy.reconcile(plan_id=plan["id"],occurred_at=old_as_of,reconciliation_id=old_reconciliation["id"])
    as_of=iso(utc_now()+timedelta(seconds=1));companion.financial.market_add(action["asset_id"],"close","11",iso(),"broker-statement")
    state=companion.financial.portfolio_state(as_of,action["account_id"]);positions={item["asset_id"]:item["quantity"] for item in state["positions"]};values={item["asset_id"]:item["market_value"] for item in state["positions"]};total=sum(Decimal(value) for value in state["cash"].values())+sum(Decimal(value) for value in values.values())
    reconciliation=companion.financial.reconcile(action["account_id"],as_of,{"cash":state["cash"],"positions":positions,"position_values":values,"position_total_by_currency":{"CNY":str(sum(Decimal(value) for value in values.values()))},"total_by_currency":{"CNY":str(total)}},"broker-statement-final")
    reconciled=companion.execution_strategy.reconcile(plan_id=plan["id"],occurred_at=as_of,reconciliation_id=reconciliation["id"])
    assert reconciled["status"]=="reconciled"


def test_condition_order_direction_price_instruction_and_fraction_contracts_fail_closed(tmp_path: Path):
    companion,fixture=setup_operating_system(tmp_path);asset=companion.financial.asset_upsert(asset_type="etf",name="ETF",currency="CNY",identifiers={"ts_code":"510300.SH"});common={"account_id":fixture["account"]["id"],"asset_id":asset["id"],"validity_sessions":20,"monitoring_window":None}
    fraction={"mode":"holding_fraction","fraction":"1/2","holding_quantity_at_configuration":"1000","resolved_quantity":"500"}
    sell=companion.execution_strategy._normalize_spec("priced_sell",{**common,"trigger":{"direction":"cross_up","monitor_price":"10"},"order":_condition_order(),"quantity":fraction})
    assert str(companion.execution_strategy.resolved_quantity(sell))=="500"
    with pytest.raises(CompanionError,match="unsupported broker price instruction"):
        companion.execution_strategy._normalize_spec("priced_buy",{**common,"trigger":{"direction":"cross_down","monitor_price":"10"},"order":{"price_type":"market","price_instruction":"custom","custom_price":"10"},"quantity":"100"})
    with pytest.raises(CompanionError,match="holding fraction"):
        companion.execution_strategy._normalize_spec("priced_buy",{**common,"trigger":{"direction":"cross_down","monitor_price":"10"},"order":_condition_order(),"quantity":fraction})
    with pytest.raises(CompanionError,match="take_profit price"):
        companion.execution_strategy._normalize_spec("bracket_exit",{**common,"base_price":"10","take_profit":{"mode":"price","value":"9"},"stop_loss":{"mode":"price","value":"8"},"order":_condition_order(),"quantity":"100"})


@pytest.mark.parametrize(("plan_type","allowed_side","forbidden_side"),[("priced_buy","buy","sell"),("priced_sell","sell","buy"),("bracket_exit","sell","buy"),("moving_grid","buy",None)])
def test_each_broker_strategy_requires_exact_decision_authorization_and_side(tmp_path:Path,plan_type:str,allowed_side:str,forbidden_side:str|None):
    companion,fixture=setup_operating_system(tmp_path)
    builder=lambda account_id,asset_id:{"plan_type":plan_type,"spec":_plan_spec(plan_type,account_id,asset_id)}
    built=build_actionable_opportunity(companion,fixture,builder,asset_type="etf" if plan_type=="moving_grid" else "stock");queue=accept_action_card(companion,built);action=companion.operating.queue_card(queue["id"])["action"]
    spec=_plan_spec(plan_type,action["account_id"],action["asset_id"])
    changed=dict(spec)
    if plan_type in {"priced_buy","priced_sell"}:changed={**spec,"trigger":{**spec["trigger"],"monitor_price":"8"}}
    elif plan_type=="bracket_exit":changed={**spec,"base_price":"11"}
    else:changed={**spec,"initial_reference_price":"9"}
    with pytest.raises(CompanionError,match="does not authorize"):
        companion.execution_strategy.create_from_queue(queue_id=queue["id"],plan_type=plan_type,spec=changed,valid_until=built["valid_until"],idempotency_key=f"wrong-{plan_type}")
    plan=companion.execution_strategy.create_from_queue(queue_id=queue["id"],plan_type=plan_type,spec=spec,valid_until=built["valid_until"],idempotency_key=f"plan-{plan_type}")
    companion.execution_strategy.mark_configured(plan_id=plan["id"],broker_condition_ref=f"broker-{plan_type}",configured_at=iso(),broker_validity_sessions=spec["validity_sessions"],broker_valid_until=built["valid_until"]);companion.execution_strategy.activate(plan_id=plan["id"],occurred_at=iso())
    if forbidden_side:
        with pytest.raises(CompanionError,match="can report only"):
            companion.execution_strategy.report_order(plan_id=plan["id"],broker_order_ref="wrong-side",side=forbidden_side,quantity="100",status="submitted",triggered_at=iso())
    extra={"reference_price_before":"10","reference_price_after":"9"} if plan_type=="moving_grid" else {"condition_leg":"take_profit"} if plan_type=="bracket_exit" else {}
    current=companion.execution_strategy.report_order(plan_id=plan["id"],broker_order_ref="right-side",side=allowed_side,quantity="100",status="submitted",triggered_at=iso(),**extra)
    assert current["orders"][0]["status"]=="submitted"
    if plan_type=="moving_grid":
        sleeping=companion.execution_strategy.set_sleeping(plan_id=plan["id"],direction="buy",sleeping=True,occurred_at=iso(),reason="broker reported max net buy reached")
        assert sleeping["buy_direction_state"]=="sleeping" and sleeping["sell_direction_state"]=="active"


def test_expired_condition_order_still_accepts_pre_deadline_order_lifecycle(tmp_path:Path):
    companion,fixture=setup_operating_system(tmp_path);builder=lambda account_id,asset_id:{"plan_type":"priced_buy","spec":_plan_spec("priced_buy",account_id,asset_id)}
    built=build_actionable_opportunity(companion,fixture,builder);queue=accept_action_card(companion,built);action=companion.operating.queue_card(queue["id"])["action"];spec=_plan_spec("priced_buy",action["account_id"],action["asset_id"])
    plan=companion.execution_strategy.create_from_queue(queue_id=queue["id"],plan_type="priced_buy",spec=spec,valid_until=built["valid_until"],idempotency_key="expiry-plan");companion.execution_strategy.mark_configured(plan_id=plan["id"],broker_condition_ref="expiry-condition",configured_at=iso(),broker_validity_sessions=spec["validity_sessions"],broker_valid_until=built["valid_until"]);companion.execution_strategy.activate(plan_id=plan["id"],occurred_at=iso())
    triggered_at=iso();companion.execution_strategy.report_order(plan_id=plan["id"],broker_order_ref="pre-deadline",side="buy",quantity="100",status="submitted",triggered_at=triggered_at)
    with companion.db.transaction() as con:con.execute("UPDATE broker_execution_plans SET valid_until=?,status='expired' WHERE id=?",(triggered_at,plan["id"]))
    updated=companion.execution_strategy.report_order(plan_id=plan["id"],broker_order_ref="pre-deadline",side="buy",quantity="100",status="cancelled",triggered_at=triggered_at,cancelled_quantity="100")
    assert updated["orders"][0]["status"]=="cancelled"
    assert plan["id"] in companion.briefing.projection(program_id=fixture["program"]["id"])["broker_strategy_attention_ids"]
    with pytest.raises(CompanionError,match="after the condition-order deadline"):
        companion.execution_strategy.report_order(plan_id=plan["id"],broker_order_ref="post-deadline",side="buy",quantity="100",status="submitted",triggered_at=iso(utc_now()+timedelta(seconds=1)))
    as_of=iso(utc_now()+timedelta(seconds=1));reconciliation=companion.financial.reconcile(action["account_id"],as_of,{"cash":{"CNY":"100000"},"positions":{},"position_values":{},"position_total_by_currency":{"CNY":"0"},"total_by_currency":{"CNY":"100000"}},"expired-strategy-statement")
    assert companion.execution_strategy.reconcile(plan_id=plan["id"],occurred_at=as_of,reconciliation_id=reconciliation["id"])["status"]=="reconciled"
