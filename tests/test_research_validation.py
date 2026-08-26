from __future__ import annotations

from datetime import timedelta

import pytest

from companion.core import Companion, CompanionError
from companion.timeutil import iso, utc_now


def validation_spec(*, max_age: int = 30):
    return {
        "falsifiers": ["the stated driver reverses"],
        "counterevidence": {
            "searched": ["issuer disclosures", "independent market evidence"],
            "findings": [],
        },
        "applicability": {
            "horizon": "one month",
            "conditions": ["normal market liquidity"],
            "excluded_conditions": ["trading suspension"],
        },
        "cost_assumptions": {
            "commission": "0.03% with minimum commission",
            "tax": "current A-share sell stamp duty",
            "slippage": "5 bps",
        },
        "max_evidence_age_days": max_age,
    }


def evidence(companion, *, source: str, group: str, known_at: str, **extra):
    return companion.data.manifest_publish(
        kind="investment_evidence",
        schema_version="research-evidence/v1",
        manifest={
            "source": source,
            "source_group": group,
            "evidence_type": "predictive_signal" if extra.get("signals") else "observed_fact",
            "first_known_at": known_at,
            "observed_at": known_at,
            **extra,
        },
    )


def test_thesis_validation_limits_non_independent_evidence_to_bounded_action(tmp_path):
    companion = Companion(tmp_path, gate_scope="test_fixture")
    companion.initialize()
    cutoff = iso()
    first = evidence(companion, source="issuer filing", group="issuer", known_at=cutoff)
    duplicate = evidence(
        companion,
        source="exchange copy of issuer filing",
        group="issuer",
        known_at=cutoff,
    )

    research = companion.investment_commands.research_publish(
        subject={"asset_id": "fixture:600000.SH"},
        content="# Thesis\nA claim that still needs independent corroboration.",
        evidence_manifest_ids=[first["id"], duplicate["id"]],
        knowledge_cutoff=cutoff,
        validation_spec=validation_spec(),
    )

    result = research["validation"]
    calculation = companion.financial.calculation_get(result["calculation_id"])
    assert result["status"] == "eligible_for_bounded_action"
    assert result["checks"]["independent_sources"] is False
    assert calculation["kind"] == "thesis_validation"
    assert calculation["outputs"]["automatic_decision_or_execution"] is False


def test_unvalidated_signal_with_non_predictive_evidence_is_bounded_not_standard(tmp_path):
    companion = Companion(tmp_path, gate_scope="test_fixture")
    companion.initialize()
    cutoff = iso()
    signal = evidence(
        companion,
        source="deterministic candidate scan",
        group="quant-signal",
        known_at=cutoff,
        signals=[
            {
                "asset_id": "fixture:600001.SH",
                "recommendation_state": "provisional_action",
                "validation_status": "unvalidated",
            }
        ],
    )
    corroboration = evidence(
        companion,
        source="issuer disclosure",
        group="issuer",
        known_at=cutoff,
    )

    research = companion.investment_commands.research_publish(
        subject={"asset_id": "fixture:600001.SH"},
        content="# Thesis\nA provisional signal is not yet validated.",
        evidence_manifest_ids=[signal["id"], corroboration["id"]],
        knowledge_cutoff=cutoff,
        validation_spec=validation_spec(),
    )

    assert research["validation"]["status"] == "eligible_for_bounded_action"
    assert research["validation"]["checks"]["predictive_evidence_validated"] is False
    recheck = companion.research_validation.revalidate(
        research["validation"]["calculation_id"]
    )
    assert recheck["eligible_for_bounded_action"] is True
    assert recheck["eligible_for_decision"] is False


def test_unvalidated_predictive_signal_alone_remains_research_only(tmp_path):
    companion = Companion(tmp_path, gate_scope="test_fixture")
    companion.initialize()
    cutoff = iso()
    signal = evidence(
        companion,
        source="deterministic candidate scan",
        group="quant-signal",
        known_at=cutoff,
        signals=[{"validation_status": "unvalidated"}],
    )
    research = companion.investment_commands.research_publish(
        subject={"asset_id": "fixture:signal-only"},
        content="# Thesis\nA signal without non-predictive evidence.",
        evidence_manifest_ids=[signal["id"]],
        knowledge_cutoff=cutoff,
        validation_spec=validation_spec(),
    )
    assert research["validation"]["status"] == "research_only"
    assert research["validation"]["non_predictive_evidence_count"] == 0

    free_text = companion.data.manifest_publish(
        kind="investment_evidence",
        schema_version="research-evidence/v1",
        manifest={
            "source": "unknown model narrative",
            "source_group": "model",
            "evidence_type": "official_disclosure",
            "first_known_at": cutoff,
            "observed_at": cutoff,
            "claims": ["预计未来20日上涨20%"],
        },
    )
    narrative = companion.investment_commands.research_publish(
        subject={"asset_id": "fixture:free-text-prediction"},
        content="# Thesis\nA free-text forecast is not a contemporaneous fact.",
        evidence_manifest_ids=[free_text["id"]],
        knowledge_cutoff=cutoff,
        validation_spec=validation_spec(),
    )
    assert narrative["validation"]["status"] == "research_only"
    assert narrative["validation"]["non_predictive_evidence_count"] == 0


def test_prediction_without_validation_marker_cannot_impersonate_observed_fact(tmp_path):
    companion = Companion(tmp_path, gate_scope="test_fixture")
    companion.initialize()
    cutoff = iso()
    disguised = companion.data.manifest_publish(
        kind="investment_evidence",
        schema_version="research-evidence/v1",
        manifest={
            "source": "unknown model",
            "source_group": "model",
            "evidence_type": "observed_fact",
            "first_known_at": cutoff,
            "observed_at": cutoff,
            "prediction": {"expected_return": "0.20", "research_only": True},
        },
    )
    research = companion.investment_commands.research_publish(
        subject={"asset_id": "fixture:disguised-prediction"},
        content="# Thesis\nA prediction is not an observed fact.",
        evidence_manifest_ids=[disguised["id"]],
        knowledge_cutoff=cutoff,
        validation_spec=validation_spec(),
    )
    assert research["validation"]["status"] == "research_only"
    assert research["validation"]["non_predictive_evidence_count"] == 0


def test_validation_rejects_future_or_stale_evidence_without_rewriting_thesis(tmp_path):
    companion = Companion(tmp_path, gate_scope="test_fixture")
    companion.initialize()
    cutoff = utc_now()
    stale_at = iso(cutoff - timedelta(days=2))
    future_at = iso(cutoff + timedelta(hours=1))
    stale = evidence(
        companion, source="stale source", group="market-data", known_at=stale_at
    )
    future = evidence(
        companion, source="future source", group="issuer", known_at=future_at
    )

    research = companion.investment_commands.research_publish(
        subject={"asset_id": "fixture:600002.SH"},
        content="# Thesis\nThe evidence timing must remain explicit.",
        evidence_manifest_ids=[stale["id"], future["id"]],
        knowledge_cutoff=iso(cutoff),
        validation_spec=validation_spec(max_age=1),
    )

    assert research["thesis"]["status"] == "active"
    assert research["validation"]["status"] == "research_only"
    assert research["validation"]["checks"]["point_in_time_available"] is False
    assert research["validation"]["checks"]["evidence_current"] is False


def test_invalid_validation_protocol_is_rejected_before_thesis_creation(tmp_path):
    companion = Companion(tmp_path, gate_scope="test_fixture")
    companion.initialize()
    cutoff = iso()
    source = evidence(companion, source="issuer", group="issuer", known_at=cutoff)
    invalid = validation_spec()
    invalid.pop("falsifiers")

    with pytest.raises(CompanionError, match="validation_spec"):
        companion.investment_commands.research_publish(
            subject={"asset_id": "fixture:600003.SH"},
            content="# Thesis\nMalformed validation protocol.",
            evidence_manifest_ids=[source["id"]],
            knowledge_cutoff=cutoff,
            validation_spec=invalid,
        )

    assert companion.cognition.object_list("thesis") == []


def test_thesis_revalidation_detects_a_superseded_revision(tmp_path):
    companion = Companion(tmp_path, gate_scope="test_fixture")
    companion.initialize()
    cutoff = iso()
    first = evidence(companion, source="issuer", group="issuer", known_at=cutoff)
    second = evidence(companion, source="exchange", group="exchange", known_at=cutoff)
    research = companion.investment_commands.research_publish(
        subject={"asset_id": "fixture:600004.SH"},
        content="# Thesis\nThe initial claim is frozen.",
        evidence_manifest_ids=[first["id"], second["id"]],
        knowledge_cutoff=cutoff,
        validation_spec=validation_spec(),
    )
    calculation_id = research["validation"]["calculation_id"]
    assert companion.research_validation.revalidate(calculation_id)[
        "eligible_for_decision"
    ] is True

    companion.cognition.publish(
        research["thesis"]["id"],
        "# Thesis\nA materially revised claim supersedes the first one.",
        knowledge_cutoff=cutoff,
    )

    current = companion.research_validation.revalidate(calculation_id)
    assert current["current_status"] == "research_only"
    assert current["eligible_for_decision"] is False
    assert "thesis_revision_changed" in current["reasons"]


def test_strategy_validation_separates_offline_and_forward_eligibility(
    tmp_path, monkeypatch
):
    companion = Companion(tmp_path, gate_scope="test_fixture")
    companion.initialize()
    strategy = {
        "id": "strategy_fixture",
        "content_hash": "strategy-content-hash",
        "status": "research_passed",
        "spec": {
            "required_evaluations": ["development", "validation", "final_holdout"],
            "evaluation_plan": [
                {"phase": "development", "seed": 7},
                {"phase": "validation", "seed": 7},
                {"phase": "final_holdout", "seed": 7},
            ],
            "pass_fail": {
                "rules": [
                    {"metric": "net_excess_return", "operator": "gt", "value": "0"}
                ]
            },
            "costs": {"reality_spec": {"version": "fixture-reality/v1"}},
            "stop_conditions": ["retire on validation failure"],
        },
    }
    runs = [
        {
            "id": f"experiment_{phase}",
            "params": {"evaluation_phase": phase},
            "seed": 7,
            "status": "succeeded",
            "dataset_snapshot_id": "snapshot_fixture",
            "bundle_manifest_id": f"manifest_{phase}",
            "metrics": {"net_excess_return": "0.01"},
            "holdout_accessed_at": iso() if phase == "final_holdout" else None,
        }
        for phase in ("development", "validation", "final_holdout")
    ]
    monkeypatch.setattr(companion.research, "strategy_get", lambda _: strategy)
    monkeypatch.setattr(companion.research, "experiment_list", lambda _: runs)
    monkeypatch.setattr(companion.shadow, "book_list", lambda: [])

    offline = companion.research_validation.validate_strategy(
        strategy_version_id=strategy["id"]
    )

    assert offline["status"] == "eligible_for_shadow"
    assert offline["checks"]["pass_fail_thresholds_met"] is True
    assert offline["checks"]["forward_sample_sufficient"] is False

    strategy["status"] = "shadow"
    monkeypatch.setattr(
        companion.shadow,
        "book_list",
        lambda: [
            {
                "id": "shadowbook_fixture",
                "strategy_version_id": strategy["id"],
                "status": "active",
            }
        ],
    )
    monkeypatch.setattr(
        companion.shadow,
        "sample_status",
        lambda _: {"status": "eligible_for_review", "checks": {"sample": True}},
    )

    forward = companion.research_validation.validate_strategy(
        strategy_version_id=strategy["id"]
    )

    assert forward["status"] == "eligible_for_decision"
    assert forward["checks"]["forward_sample_sufficient"] is True
    assert companion.financial.calculation_get(forward["calculation_id"])["kind"] == "strategy_validation"
