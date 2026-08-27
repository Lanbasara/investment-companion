from __future__ import annotations

from datetime import timedelta

import pytest

from companion.core import CompanionError
from companion.interfaces.mcp_profiles import call_investment
from companion.timeutil import iso, utc_now
from tests.test_v5_operating_system import research_validation_spec, setup_operating_system


def candidate_manifest(companion, program_id: str, *, kind: str = "v6_stock_research_candidates", candidates=None):
    return companion.data.manifest_publish(
        kind=kind,
        schema_version="fixture/v1",
        manifest={
            "program_id": program_id,
            "as_of": iso(),
            "status": "ready",
            "candidates": candidates or [
                {"asset_id": "tushare:600001.SH"},
                {"asset_id": "tushare:600002.SH"},
            ],
            "research_only": True,
            "not_a_recommendation": True,
        },
    )


def test_candidate_enqueue_is_idempotent_and_requires_complete_triage(tmp_path):
    companion, fixture = setup_operating_system(tmp_path)
    manifest = candidate_manifest(companion, fixture["program"]["id"])
    first = companion.research_work.enqueue_candidate_manifest(manifest["id"])
    second = companion.research_work.enqueue_candidate_manifest(manifest["id"])
    assert first["created"] is True
    assert second["created"] is False
    assert first["item"]["id"] == second["item"]["id"]
    assert companion.operating.today()["mode"] == "review_required"
    assert "研究队列" in companion.operating.today()["message"]
    claimed = companion.investment_commands.opportunity_update(
        operation="work_claim", item_id=first["item"]["id"], actor="primary"
    )
    with pytest.raises(CompanionError, match="explicitly cover every candidate"):
        companion.research_work.complete_triage(
            claimed["id"], owner="primary",
            dispositions=[{"candidate_id": "tushare:600001.SH", "outcome": "reject", "reason": "fails liquidity"}],
        )


def test_triage_rejects_subject_substitution_and_multiple_etf_representatives(tmp_path):
    companion, fixture = setup_operating_system(tmp_path)
    manifest = candidate_manifest(companion, fixture["program"]["id"])
    item = companion.research_work.enqueue_candidate_manifest(manifest["id"])["item"]
    companion.research_work.claim(item["id"], owner="primary")
    with pytest.raises(CompanionError, match="retain candidate identity"):
        companion.research_work.complete_triage(item["id"], owner="primary", dispositions=[
            {"candidate_id": "tushare:600001.SH", "outcome": "research", "reason": "substitution", "subject": {"asset_id": "tushare:600099.SH", "note": "tushare:600001.SH"}},
            {"candidate_id": "tushare:600002.SH", "outcome": "reject", "reason": "covered"},
        ])

    classification = {"labels": ["tracking_index:000300.SH"]}
    fund = candidate_manifest(companion, fixture["program"]["id"], kind="v6_fund_research_candidates", candidates=[
        {"asset_id": "tushare:510300.SH", "classification": classification},
        {"asset_id": "tushare:510310.SH", "classification": classification},
    ])
    fund_item = companion.research_work.enqueue_candidate_manifest(fund["id"])["item"]
    companion.research_work.claim(fund_item["id"], owner="primary")
    with pytest.raises(CompanionError, match="at most one research or monitoring representative"):
        companion.research_work.complete_triage(fund_item["id"], owner="primary", dispositions=[
            {"candidate_id": "tushare:510300.SH", "outcome": "research", "reason": "first"},
            {"candidate_id": "tushare:510310.SH", "outcome": "monitor", "reason": "second", "next_check_at": iso(utc_now() + timedelta(days=1))},
        ])


def test_no_action_brief_is_rejected_while_research_work_is_open(tmp_path):
    companion, fixture = setup_operating_system(tmp_path)
    manifest = candidate_manifest(companion, fixture["program"]["id"])
    companion.research_work.enqueue_candidate_manifest(manifest["id"])
    with pytest.raises(CompanionError, match="unfinished research work"):
        companion.operating.brief_prepare(
            brief_type="daily", period_key="research-open", as_of=iso(), conclusion="no_action",
            payload={"summary": "Wait", "what_changed": [], "decision": "Wait", "risks": [],
                     "next_check_at": iso(utc_now() + timedelta(hours=1)), "queue_item_ids": []},
            source_refs=[manifest["id"]],
        )


def test_triage_creates_research_and_monitor_obligations_without_trading(tmp_path):
    companion, fixture = setup_operating_system(tmp_path)
    manifest = candidate_manifest(companion, fixture["program"]["id"])
    triage = companion.research_work.enqueue_candidate_manifest(manifest["id"])["item"]
    companion.research_work.claim(triage["id"], owner="primary")
    result = companion.research_work.complete_triage(
        triage["id"], owner="primary",
        dispositions=[
            {"candidate_id": "tushare:600001.SH", "outcome": "research", "reason": "best data-ready lead"},
            {"candidate_id": "tushare:600002.SH", "outcome": "monitor", "reason": "wait for filing", "next_check_at": iso(utc_now() + timedelta(days=2))},
        ],
    )
    assert [item["status"] for item in result["children"]] == ["queued", "monitoring"]
    assert companion.research_work.summary()["open"] == 2
    with companion.db.connect() as con:
        assert con.execute("SELECT COUNT(*) FROM decision_queue_items").fetchone()[0] == 0
        assert con.execute("SELECT COUNT(*) FROM executions").fetchone()[0] == 0
        assert con.execute("SELECT COUNT(*) FROM ledger_entries WHERE entry_type<>'opening_balance'").fetchone()[0] == 0


def test_full_research_cannot_claim_promotion_without_real_opportunity(tmp_path):
    companion, fixture = setup_operating_system(tmp_path)
    manifest = candidate_manifest(companion, fixture["program"]["id"])
    triage = companion.research_work.enqueue_candidate_manifest(manifest["id"])["item"]
    companion.research_work.claim(triage["id"], owner="primary")
    child = companion.research_work.complete_triage(
        triage["id"], owner="primary",
        dispositions=[
            {"candidate_id": "tushare:600001.SH", "outcome": "research", "reason": "promising"},
            {"candidate_id": "tushare:600002.SH", "outcome": "reject", "reason": "inferior alternative"},
        ],
    )["children"][0]
    companion.research_work.claim(child["id"], owner="researcher")
    with pytest.raises(CompanionError, match="requires opportunity_id"):
        companion.research_work.complete_research(
            child["id"], owner="researcher", outcome="promoted", reason="done", result_refs=[manifest["id"]]
        )
    rejected = companion.research_work.complete_research(
        child["id"], owner="researcher", outcome="rejected", reason="thesis failed", result_refs=[manifest["id"]]
    )
    assert rejected["status"] == "rejected"


def test_promoted_research_requires_matching_current_thesis_validation_and_source(tmp_path):
    companion, fixture = setup_operating_system(tmp_path)
    source = candidate_manifest(companion, fixture["program"]["id"])
    triage = companion.research_work.enqueue_candidate_manifest(source["id"])["item"]
    context = call_investment(companion, "research_context", {"work_item_id": triage["id"], "limit": 5}, "primary")
    assert context["work_queue"]["selected"]["candidate_scope"] == triage["candidate_scope"]
    call_investment(companion, "investment_opportunity_update", {"operation": "work_claim", "item_id": triage["id"]}, "primary")
    child = call_investment(companion, "investment_opportunity_update", {
        "operation": "triage_complete", "item_id": triage["id"], "dispositions": [
            {"candidate_id": "tushare:600001.SH", "outcome": "research", "reason": "best lead"},
            {"candidate_id": "tushare:600002.SH", "outcome": "reject", "reason": "inferior"},
        ],
    }, "primary")["children"][0]
    subject = {"asset_id": "tushare:600001.SH"}; cutoff = iso()
    evidence = [companion.investment_commands.evidence_update(
        operation="publish_source", subject=subject, source=f"source-{index}", source_group=f"group-{index}",
        first_known_at=cutoff, observed_at=cutoff, claims=[f"claim-{index}"], evidence_type="official_disclosure",
    ) for index in (1, 2)]
    first = companion.investment_commands.research_publish(
        subject=subject, content="# Thesis\n\nFalsifiable primary thesis.",
        evidence_manifest_ids=[item["id"] for item in evidence], knowledge_cutoff=cutoff,
        validation_spec=research_validation_spec(),
    )
    unrelated = companion.investment_commands.research_publish(
        subject={"asset_id": "tushare:600099.SH"}, content="# Other thesis\n\nA separate versioned thesis.",
        evidence_manifest_ids=[item["id"] for item in evidence], knowledge_cutoff=cutoff,
        validation_spec=research_validation_spec(),
    )
    opportunity = companion.operating.opportunity_create(
        subject=subject, evidence_refs=[source["id"]], reason="candidate selected after comparison",
        program_id=fixture["program"]["id"], thesis_id=first["thesis"]["id"],
    )
    wrong_subject_opportunity = companion.operating.opportunity_create(
        subject=subject, evidence_refs=[source["id"]], reason="deliberately mismatched thesis fixture",
        program_id=fixture["program"]["id"], thesis_id=unrelated["thesis"]["id"],
    )
    call_investment(companion, "investment_opportunity_update", {"operation": "work_claim", "item_id": child["id"]}, "researcher")
    with pytest.raises(CompanionError, match="does not match|eligible formal"):
        companion.research_work.complete_research(
            child["id"], owner="researcher", outcome="promoted", reason="wrong validation",
            result_refs=[source["id"], unrelated["validation"]["calculation_id"]], opportunity_id=wrong_subject_opportunity["id"],
        )
    completed = call_investment(companion, "investment_opportunity_update", {
        "operation": "research_complete", "item_id": child["id"], "outcome": "promoted",
        "reason": "validated research", "result_refs": [source["id"], first["validation"]["calculation_id"]],
        "opportunity_id": opportunity["id"],
    }, "researcher")
    assert completed["status"] == "completed" and completed["opportunity_id"] == opportunity["id"]


def test_expired_lease_and_due_monitor_recover_to_queue(tmp_path):
    companion, fixture = setup_operating_system(tmp_path)
    manifest = candidate_manifest(companion, fixture["program"]["id"])
    item = companion.research_work.enqueue_candidate_manifest(manifest["id"])["item"]
    companion.research_work.claim(item["id"], owner="dead-worker")
    with companion.db.transaction() as con:
        con.execute("UPDATE research_work_items SET lease_until=? WHERE id=?", (iso(utc_now() - timedelta(seconds=1)), item["id"]))
    recovered = companion.recover()["recovered"]
    assert recovered["research_work_leases"] == 1
    assert companion.research_work.get(item["id"])["status"] == "queued"


def test_rollout_backfill_attaches_only_latest_candidate_cohort_per_kind(tmp_path):
    companion, fixture = setup_operating_system(tmp_path)
    older = candidate_manifest(companion, fixture["program"]["id"])
    newer = candidate_manifest(companion, fixture["program"]["id"], kind="v6_fund_research_candidates")
    result = companion.research_work.backfill_latest_candidates()
    assert result["created"] == 2
    assert {item["source_manifest_id"] for item in result["items"]} == {older["id"], newer["id"]}
    assert companion.research_work.backfill_latest_candidates()["created"] == 0


def test_archived_program_work_does_not_pollute_current_program_home(tmp_path):
    companion, fixture = setup_operating_system(tmp_path)
    old_program = fixture["program"]
    manifest = candidate_manifest(companion, old_program["id"])
    companion.research_work.enqueue_candidate_manifest(manifest["id"])
    companion.operating.program_set_status(old_program["id"], "archived", reason="replace fixture program")
    draft = companion.operating.program_create(
        name="Replacement program", content=fixture["content"], context_refs=fixture["contexts"], reason="replacement",
    )
    current = companion.operating.program_confirm(
        draft["revisions"][0]["id"], user_approval_ref="pytest:replacement",
    )
    assert companion.research_work.summary(program_id=current["id"])["open"] == 0
    assert companion.investment.home()["research"]["work_queue"]["open"] == 0
    assert companion.investment.research_context(limit=10)["work_queue"]["items"] == []


def test_archived_program_orphan_candidate_does_not_fail_current_program_doctor_check(tmp_path):
    companion, fixture = setup_operating_system(tmp_path)
    candidate_manifest(companion, fixture["program"]["id"])
    companion.operating.program_set_status(fixture["program"]["id"], "archived", reason="replace fixture program")
    draft = companion.operating.program_create(
        name="Clean current program", content=fixture["content"], context_refs=fixture["contexts"], reason="replacement",
    )
    current = companion.operating.program_confirm(draft["revisions"][0]["id"], user_approval_ref="pytest:clean-current")
    assert companion._latest_candidates_have_work(current["id"]) is True
