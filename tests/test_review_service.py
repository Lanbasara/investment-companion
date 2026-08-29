from __future__ import annotations

from companion.core import Companion
from companion.timeutil import iso


def calculation(companion):
    return companion.financial._record(
        "fixture_performance",
        "Fixture performance evidence",
        iso(),
        {"fixture": True},
        {},
        {"return": "fixture"},
        {"excess_return": "-0.02", "maximum_drawdown": "0.10"},
        [],
    )


def test_review_can_propose_but_cannot_apply_a_strategy_change(tmp_path):
    companion = Companion(tmp_path, gate_scope="test_fixture")
    companion.initialize()
    evidence = calculation(companion)
    strategies_before = companion.research.strategy_list()

    result = companion.review.publish(
        subject={"strategy_version_id": "strategy_fixture"},
        content="The frozen method underperformed after costs; validate a stricter entry condition.",
        conclusion="revise",
        calculation_ids=[evidence["id"]],
        source_refs=["fixture-forward-review"],
        proposed_changes=[
            {
                "target_type": "strategy",
                "target_id": "strategy_fixture",
                "change": "raise the entry threshold",
                "reason": "cost-after excess return was negative",
                "validation_required": True,
            }
        ],
        knowledge_cutoff=iso(),
    )
    assert result["change_proposal"]["status"] == "proposed"
    assert result["change_proposal"]["requires_new_version"] is True
    assert result["change_proposal"]["automatic_application"] is False
    assert companion.research.strategy_list() == strategies_before
    assert result["revision"]["metadata"]["conclusion"] == "revise"


def test_insufficient_evidence_review_cannot_smuggle_in_a_change(tmp_path):
    companion = Companion(tmp_path, gate_scope="test_fixture")
    companion.initialize()
    evidence = calculation(companion)
    try:
        companion.review.publish(
            subject={"research_pipeline": "fixture"},
            content="The sample is still insufficient.",
            conclusion="insufficient_evidence",
            calculation_ids=[evidence["id"]],
            source_refs=["fixture-review"],
            proposed_changes=[
                {
                    "target_type": "research_pipeline",
                    "target_id": "fixture",
                    "change": "lower the threshold",
                    "reason": "produce more actions",
                    "validation_required": True,
                }
            ],
        )
    except Exception as exc:
        assert "cannot contain proposed changes" in str(exc)
    else:
        raise AssertionError("insufficient evidence review changed the method")


def test_review_supersession_appends_history_and_keeps_change_proposals_inert(tmp_path):
    companion = Companion(tmp_path, gate_scope="test_fixture")
    companion.initialize()
    evidence = calculation(companion)
    thesis = companion.cognition.object_create("thesis", {"asset_id": "fixture"})
    thesis_revision = companion.cognition.publish(
        thesis["id"], "Fixture thesis", knowledge_cutoff=iso()
    )
    mandate = companion.cognition.context_create(
        "mandate", {"max_single_position_weight": "0.2"}, reason="fixture"
    )
    mandate = companion.cognition.context_confirm(mandate["id"])
    strategies_before = companion.research.strategy_list()
    subject = {"thesis_id": thesis["id"], "revision_id": thesis_revision["id"]}

    created = companion.review.publish(
        operation="create",
        subject=subject,
        content="The frozen evidence suggests a separately validated revision.",
        conclusion="revise",
        calculation_ids=[evidence["id"]],
        source_refs=["fixture:review-source"],
        historical_refs=[thesis_revision["id"], mandate["id"]],
        proposed_changes=[
            {
                "target_type": "thesis",
                "target_id": thesis["id"],
                "change": "test a narrower scope",
                "reason": "the current evidence has limited coverage",
                "validation_required": True,
            },
            {
                "target_type": "policy",
                "target_id": mandate["id"],
                "change": "consider a lower risk budget",
                "reason": "drawdown evidence should be reviewed",
                "validation_required": True,
            },
            {
                "target_type": "strategy",
                "target_id": "strategy_fixture",
                "change": "test a stricter entry threshold",
                "reason": "cost evidence should be reviewed",
                "validation_required": True,
            },
        ],
        knowledge_cutoff=iso(),
    )
    original_revision = companion.cognition.revision_get(created["revision"]["id"])
    superseded = companion.review.publish(
        operation="supersede",
        review_id=created["review"]["id"],
        supersedes_revision_id=created["revision"]["id"],
        supersession_reason="A larger frozen sample is now available.",
        subject=subject,
        content="The larger sample supports continuing without a rule change.",
        conclusion="continue",
        calculation_ids=[evidence["id"]],
        source_refs=["fixture:review-source-v2"],
        historical_refs=[created["revision"]["id"], thesis_revision["id"]],
        knowledge_cutoff=iso(),
    )

    assert superseded["revision"]["revision"] == 2
    assert superseded["revision"]["parent_id"] == created["revision"]["id"]
    assert companion.cognition.revision_get(created["revision"]["id"]) == original_revision
    assert [
        item["revision"]
        for item in companion.cognition.revision_list(created["review"]["id"])
    ] == [1, 2]
    assert companion.cognition.object_get(thesis["id"])[
        "current_revision_id"
    ] == thesis_revision["id"]
    assert companion.cognition.context_current("mandate")["id"] == mandate["id"]
    assert companion.research.strategy_list() == strategies_before
    assert superseded["automatic_changes_applied"] is False

    try:
        companion.review.publish(
            operation="supersede",
            review_id=created["review"]["id"],
            supersedes_revision_id=created["revision"]["id"],
            supersession_reason="Attempt a stale silent rewrite.",
            subject=subject,
            content="This must not replace revision two.",
            conclusion="continue",
            calculation_ids=[evidence["id"]],
            source_refs=["fixture:review-source-v3"],
            historical_refs=[created["revision"]["id"]],
            knowledge_cutoff=iso(),
        )
    except Exception as exc:
        assert "revision_conflict" in str(exc)
    else:
        raise AssertionError("stale Review supersession rewrote current history")
