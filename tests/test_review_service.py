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
