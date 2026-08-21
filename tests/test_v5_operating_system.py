from __future__ import annotations

import sqlite3
from datetime import timedelta
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
        "risk_budget": {"max_drawdown_review_level": "0.15", "single_position_weight": "0.20"},
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


def build_actionable_opportunity(companion: Companion, fixture: dict) -> dict:
    asset = companion.financial.asset_upsert(
        asset_type="stock",
        name="Fixture Co",
        currency="CNY",
        identifiers={"ts_code": "000001.SZ"},
    )
    source_a = companion.inbox_add(source="official_filing", title="Annual report", content="fixture A")
    source_b = companion.inbox_add(source="exchange", title="Exchange notice", content="fixture B")
    thesis = companion.cognition.object_create("thesis", {"asset_id": asset["id"]})
    thesis_revision = companion.cognition.publish(
        thesis["id"],
        "# Fixture thesis\n\nA bounded, falsifiable fixture thesis.",
        knowledge_cutoff=iso(),
    )
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
        "evidence_state": "corroborated",
        "independent_source_count": 2,
        "data_freshness": "current",
        "falsifiers": ["cash conversion deteriorates below the registered threshold"],
        "major_unknowns": ["decision price and portfolio fit are not frozen"],
        "counterevidence": ["industry cycle remains uncertain"],
        "decision_basis": "Two primary sources support continued evaluation, not action.",
    }
    opportunity = companion.operating.opportunity_transition(
        opportunity["id"],
        expected_version=2,
        to_stage="qualified",
        to_status="active",
        evidence_refs=[source_a["id"], source_b["id"]],
        qualification=qualified,
        reason="Evidence passed the research qualification contract",
    )
    state = companion.financial.portfolio_state(iso(), fixture["account"]["id"])
    decision = companion.cognition.object_create("decision", {"asset_id": asset["id"]})
    decision_revision = companion.cognition.publish(
        decision["id"],
        "# Fixture Decision\n\nReview the candidate within the frozen personal constraints.",
        knowledge_cutoff=iso(),
        context_refs={
            "investor_revision_id": fixture["contexts"]["investor_revision_id"],
            "mandate_revision_id": fixture["contexts"]["mandate_revision_id"],
            "portfolio_calculation_id": state["calculation_id"],
            "thesis_revision_ids": [thesis_revision["id"]],
        },
        calculation_ids=[state["calculation_id"]],
        metadata={
            "valid_until": iso(utc_now() + timedelta(hours=6)),
            "invalidators": ["portfolio or mandate changes", "new filing contradicts thesis"],
            "no_action": {"choice": "continue observing", "cost": "possible opportunity cost"},
            "source_refs": [source_a["id"], source_b["id"]],
        },
    )
    actionable = {
        "evidence_state": "decision_grade",
        "independent_source_count": 2,
        "data_freshness": "current",
        "falsifiers": ["Decision invalidator becomes true"],
        "major_unknowns": [],
        "counterevidence": ["no deterministic alpha claim exists"],
        "decision_basis": "The issued Decision freezes personal facts, evidence and validity.",
    }
    opportunity = companion.operating.opportunity_transition(
        opportunity["id"],
        expected_version=3,
        to_stage="actionable",
        to_status="active",
        evidence_refs=[source_a["id"], source_b["id"], decision_revision["id"]],
        qualification=actionable,
        decision_revision_id=decision_revision["id"],
        reason="Current issued Decision closed the remaining major unknowns",
    )
    return {
        "asset": asset,
        "sources": [source_a, source_b],
        "thesis_revision": thesis_revision,
        "decision_revision": decision_revision,
        "portfolio_state": state,
        "opportunity": opportunity,
    }


def test_v5_program_opportunity_queue_and_user_briefs(tmp_path: Path):
    companion, fixture = setup_operating_system(tmp_path)
    built = build_actionable_opportunity(companion, fixture)
    opportunity = built["opportunity"]
    assert opportunity["stage"] == "actionable"
    assert opportunity["evidence_band"] == "decision_grade"
    queue = companion.operating.queue_enqueue(
        opportunity["id"],
        decision_revision_id=built["decision_revision"]["id"],
    )
    card = companion.operating.queue_card(queue["id"])
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
        "companion.core.subprocess.run",
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
    assert len(companion.system_status()["migrations"]) == 5
