from __future__ import annotations

from datetime import timedelta
import re
from typing import Any

from .foundation import CompanionError
from .timeutil import iso, parse, utc_now


class ResearchValidationService:
    """Deterministic eligibility checks between research and investment decisions.

    The service does not decide whether a claim is true.  It verifies that the
    exact research version has enough frozen, point-in-time, independently
    sourced and falsifiable evidence to be considered by a Decision.  Strategy
    validation reuses the existing experiment and Shadow truth owners instead
    of implementing another backtester.
    """

    THESIS_SPEC_FIELDS = {
        "falsifiers",
        "counterevidence",
        "applicability",
        "cost_assumptions",
        "max_evidence_age_days",
    }
    COUNTEREVIDENCE_FIELDS = {"searched", "findings"}
    FINDING_FIELDS = {"claim", "disposition"}
    APPLICABILITY_FIELDS = {"horizon", "conditions", "excluded_conditions"}
    COST_FIELDS = {"commission", "tax", "slippage"}

    def __init__(self, companion):
        self.c = companion

    def normalize_thesis_spec(self, value: Any) -> dict[str, Any]:
        """Validate the declared research protocol before publishing a Thesis."""

        if not isinstance(value, dict) or set(value) != self.THESIS_SPEC_FIELDS:
            raise CompanionError(
                f"research validation_spec must be exactly: {sorted(self.THESIS_SPEC_FIELDS)}"
            )
        falsifiers = self._strings(value["falsifiers"], "falsifiers")
        counterevidence = value["counterevidence"]
        if not isinstance(counterevidence, dict) or set(counterevidence) != self.COUNTEREVIDENCE_FIELDS:
            raise CompanionError(
                "validation counterevidence must contain only searched and findings"
            )
        searched = self._strings(counterevidence["searched"], "counterevidence.searched")
        findings = counterevidence["findings"]
        if not isinstance(findings, list):
            raise CompanionError("counterevidence.findings must be a list")
        normalized_findings = []
        for finding in findings:
            if not isinstance(finding, dict) or set(finding) != self.FINDING_FIELDS:
                raise CompanionError(
                    "each counterevidence finding must contain only claim and disposition"
                )
            normalized_findings.append(
                {
                    "claim": self._text(finding["claim"], "counterevidence claim"),
                    "disposition": self._text(
                        finding["disposition"], "counterevidence disposition"
                    ),
                }
            )
        applicability = value["applicability"]
        if not isinstance(applicability, dict) or set(applicability) != self.APPLICABILITY_FIELDS:
            raise CompanionError(
                f"validation applicability must be exactly: {sorted(self.APPLICABILITY_FIELDS)}"
            )
        normalized_applicability = {
            "horizon": self._text(applicability["horizon"], "applicability.horizon"),
            "conditions": self._strings(
                applicability["conditions"], "applicability.conditions"
            ),
            "excluded_conditions": self._strings(
                applicability["excluded_conditions"],
                "applicability.excluded_conditions",
                allow_empty=True,
            ),
        }
        costs = value["cost_assumptions"]
        if not isinstance(costs, dict) or set(costs) != self.COST_FIELDS:
            raise CompanionError(
                f"validation cost_assumptions must be exactly: {sorted(self.COST_FIELDS)}"
            )
        normalized_costs = {
            field: self._text(str(costs[field]), f"cost_assumptions.{field}")
            for field in sorted(self.COST_FIELDS)
        }
        max_age = value["max_evidence_age_days"]
        if isinstance(max_age, bool) or not isinstance(max_age, int) or not 0 <= max_age <= 3650:
            raise CompanionError("max_evidence_age_days must be an integer within 0..3650")
        return {
            "falsifiers": falsifiers,
            "counterevidence": {"searched": searched, "findings": normalized_findings},
            "applicability": normalized_applicability,
            "cost_assumptions": normalized_costs,
            "max_evidence_age_days": max_age,
        }

    def validate_thesis(
        self,
        *,
        thesis_revision_id: str,
        evidence_manifest_ids: list[str],
        knowledge_cutoff: str,
        validation_spec: dict[str, Any],
    ) -> dict[str, Any]:
        spec = self.normalize_thesis_spec(validation_spec)
        cutoff = parse(knowledge_cutoff)
        if cutoff > utc_now():
            raise CompanionError("research validation knowledge_cutoff cannot be in the future")
        revision = self.c.cognition.revision_get(thesis_revision_id)
        thesis = self.c.cognition.object_get(revision["object_id"])
        if thesis["object_type"] != "thesis":
            raise CompanionError("thesis validation requires a Thesis revision")
        manifests = self._manifests(evidence_manifest_ids)
        recorded_evidence = revision.get("metadata", {}).get("evidence_manifest_ids", [])
        checks: dict[str, bool] = {
            "current_thesis_revision": thesis.get("current_revision_id") == revision["id"]
            and thesis["status"] == "active",
            "exact_published_evidence": recorded_evidence == evidence_manifest_ids,
            "minimum_frozen_evidence": len(manifests) >= 2,
            "evidence_ready": all(item["status"] == "ready" for item in manifests),
            "point_in_time_available": True,
            "evidence_current": True,
            "independent_sources": False,
            "predictive_evidence_validated": True,
            "falsifiable": bool(spec["falsifiers"]),
            "counterevidence_searched": bool(spec["counterevidence"]["searched"]),
            "applicability_declared": bool(spec["applicability"]["conditions"]),
            "costs_declared": all(spec["cost_assumptions"].values()),
        }
        evidence_details = []
        source_groups: set[str] = set()
        non_predictive_evidence_count = 0
        for item in manifests:
            body = item["manifest"].get("manifest", {})
            source = body.get("source")
            source_group = body.get("source_group")
            first_known_at = body.get("first_known_at")
            observed_at = body.get("observed_at")
            detail = {
                "manifest_id": item["id"],
                "kind": item["kind"],
                "content_hash": item["content_hash"],
                "source": source,
                "source_group": source_group,
                "first_known_at": first_known_at,
                "observed_at": observed_at,
            }
            if not isinstance(source, str) or not source.strip():
                checks["independent_sources"] = False
            if isinstance(source_group, str) and source_group.strip():
                source_groups.add(source_group.strip())
            else:
                checks["independent_sources"] = False
            try:
                first_known = parse(first_known_at)
                observed = parse(observed_at)
            except Exception:
                checks["point_in_time_available"] = False
                checks["evidence_current"] = False
            else:
                if first_known > cutoff or observed > cutoff or first_known > observed:
                    checks["point_in_time_available"] = False
                if cutoff - observed > timedelta(days=spec["max_evidence_age_days"]):
                    checks["evidence_current"] = False
            if self._has_unvalidated_prediction(body):
                checks["predictive_evidence_validated"] = False
            if item["kind"] == "investment_evidence" and body.get("evidence_type") in {
                "observed_fact",
                "official_disclosure",
                "validated_analysis",
            } and not self._contains_predictive_content(body):
                non_predictive_evidence_count += 1
            evidence_details.append(detail)
        checks["independent_sources"] = (
            checks["minimum_frozen_evidence"]
            and len(source_groups) >= 2
            and all(
                isinstance(item["source"], str)
                and item["source"].strip()
                and isinstance(item["source_group"], str)
                and item["source_group"].strip()
                for item in evidence_details
            )
        )
        failures = [name for name, passed in checks.items() if not passed]
        bounded_hard_checks = {
            key: value
            for key, value in checks.items()
            if key not in {"minimum_frozen_evidence", "independent_sources", "predictive_evidence_validated"}
        }
        bounded_eligible = all(bounded_hard_checks.values()) and non_predictive_evidence_count >= 1
        status = (
            "eligible_for_decision"
            if not failures
            else "eligible_for_bounded_action"
            if bounded_eligible
            else "research_only"
        )
        outputs = {
            "status": status,
            "validation_type": "thesis",
            "subject": thesis["subject"],
            "thesis_revision_id": revision["id"],
            "evidence_manifest_ids": evidence_manifest_ids,
            "independent_source_count": len(source_groups),
            "non_predictive_evidence_count": non_predictive_evidence_count,
            "checks": checks,
            "failures": failures,
            "eligible_for_bounded_action": bounded_eligible,
            "automatic_decision_or_execution": False,
        }
        calculation = self.c.financial.calculation_record(
            "thesis_validation",
            "Decision eligibility of one immutable Thesis revision",
            knowledge_cutoff,
            {
                "thesis_revision_id": revision["id"],
                "thesis_content_hash": revision["content_hash"],
                "subject": thesis["subject"],
                "evidence_manifest_ids": evidence_manifest_ids,
                "evidence": evidence_details,
            },
            spec,
            {
                "decision_eligibility": "all mandatory lineage, PIT, freshness, independence, falsifiability, counterevidence, applicability, cost and predictive-validation checks are true",
                "independent_source_count": "count(distinct evidence.source_group)",
                "freshness": "knowledge_cutoff - evidence.observed_at <= max_evidence_age_days",
            },
            outputs,
            failures,
        )
        return {**outputs, "calculation_id": calculation["id"]}

    def validate_strategy(self, *, strategy_version_id: str, as_of: str | None = None) -> dict[str, Any]:
        """Summarize existing Experiment and Shadow gates without duplicating them."""

        strategy = self.c.research.strategy_get(strategy_version_id)
        evaluation_phases = set(strategy["spec"].get("required_evaluations", []))
        runs = [
            item
            for item in self.c.research.experiment_list(strategy_version_id)
            if item.get("params", {}).get("evaluation_phase") in evaluation_phases
        ]
        expected_plan = {
            (item["phase"], int(item["seed"]))
            for item in strategy["spec"].get("evaluation_plan", [])
        }
        observed_plan = [
            (item.get("params", {}).get("evaluation_phase"), int(item["seed"]))
            for item in runs
        ]
        threshold_failures = []
        threshold_phases = set()
        for run in runs:
            if run.get("params", {}).get("evaluation_phase") in {"validation", "final_holdout"}:
                if run["status"] == "succeeded":
                    threshold_phases.add(run["params"]["evaluation_phase"])
                threshold_failures.extend(
                    self.c.research.metric_failures(
                        strategy["spec"].get("pass_fail"), run.get("metrics", {})
                    )
                )
        final_holdout = [
            item for item in runs if item.get("params", {}).get("evaluation_phase") == "final_holdout"
        ]
        snapshots = {item["dataset_snapshot_id"] for item in runs}
        checks = {
            "immutable_evaluation_plan": len(observed_plan) == len(set(observed_plan))
            and set(observed_plan) == expected_plan,
            "all_evaluations_succeeded": bool(runs)
            and all(item["status"] == "succeeded" for item in runs),
            "single_frozen_dataset": len(snapshots) == 1,
            "final_holdout_used_once": len(final_holdout) == 1
            and bool(final_holdout[0].get("holdout_accessed_at")),
            "cost_model_frozen": bool(
                strategy["spec"].get("costs", {}).get("reality_spec")
            ),
            "pass_fail_thresholds_met": threshold_phases
            == {"validation", "final_holdout"}
            and not threshold_failures,
            "research_promotion_recorded": strategy["status"] in {"research_passed", "shadow"},
        }
        offline_ready = all(checks.values())
        books = [
            item
            for item in self.c.shadow.book_list()
            if item["strategy_version_id"] == strategy_version_id and item["status"] == "active"
        ]
        forward_samples = [self.c.shadow.sample_status(item["id"]) for item in books]
        forward_ready = any(item["status"] == "eligible_for_review" for item in forward_samples)
        status = (
            "eligible_for_decision"
            if offline_ready and strategy["status"] == "shadow" and forward_ready
            else "eligible_for_shadow"
            if offline_ready
            else "research_only"
        )
        failures = [name for name, passed in checks.items() if not passed]
        if offline_ready and not forward_ready:
            failures.append("forward_sample_sufficient")
        evidence_manifest_ids = sorted(
            item["bundle_manifest_id"] for item in runs if item.get("bundle_manifest_id")
        )
        effective_as_of = as_of or iso()
        if parse(effective_as_of) > utc_now():
            raise CompanionError("strategy validation as_of cannot be in the future")
        outputs = {
            "status": status,
            "validation_type": "strategy",
            "strategy_version_id": strategy_version_id,
            "checks": {**checks, "forward_sample_sufficient": forward_ready},
            "failures": failures,
            "forward_samples": forward_samples,
            "automatic_strategy_change": False,
            "automatic_decision_or_execution": False,
        }
        calculation = self.c.financial.calculation_record(
            "strategy_validation",
            "Offline and forward eligibility of one immutable Strategy Version",
            effective_as_of,
            {
                "strategy_version_id": strategy_version_id,
                "strategy_content_hash": strategy["content_hash"],
                "experiment_run_ids": [item["id"] for item in runs],
                "evidence_manifest_ids": evidence_manifest_ids,
                "shadow_book_ids": [item["id"] for item in books],
            },
            {
                "pass_fail": strategy["spec"].get("pass_fail"),
                "costs": strategy["spec"].get("costs"),
                "stop_conditions": strategy["spec"].get("stop_conditions"),
            },
            {
                "offline_eligibility": "exact preregistered plan, one dataset, successful validation and final holdout, frozen costs, passing thresholds and explicit promotion",
                "decision_eligibility": "offline eligibility and sufficient untuned forward Shadow sample",
            },
            outputs,
            failures,
        )
        return {**outputs, "calculation_id": calculation["id"]}

    def revalidate(self, calculation_id: str, *, as_of: str | None = None) -> dict[str, Any]:
        """Recheck whether a frozen validation is still usable without upgrading it."""

        calculation = self.c.financial.calculation_get(calculation_id)
        if calculation["kind"] not in {"thesis_validation", "strategy_validation"}:
            raise CompanionError("research validation requires a thesis_validation or strategy_validation Calculation")
        now = parse(as_of) if as_of else utc_now()
        if now > utc_now() + timedelta(seconds=5):
            raise CompanionError("research validation recheck cannot use a future as_of")
        frozen_status = calculation["outputs"].get("status", "research_only")
        current_status = frozen_status
        reasons: list[str] = []
        if calculation["kind"] == "thesis_validation":
            if frozen_status not in {"eligible_for_bounded_action", "eligible_for_decision"}:
                reasons.append("frozen_validation_not_action_eligible")
            revision = self.c.cognition.revision_get(
                calculation["inputs"].get("thesis_revision_id")
            )
            thesis = self.c.cognition.object_get(revision["object_id"])
            if (
                thesis["object_type"] != "thesis"
                or thesis["status"] != "active"
                or thesis.get("current_revision_id") != revision["id"]
                or revision["content_hash"] != calculation["inputs"].get("thesis_content_hash")
            ):
                reasons.append("thesis_revision_changed")
            maximum_age = int(calculation["assumptions"].get("max_evidence_age_days", 0))
            evidence_by_id = {
                item.get("manifest_id"): item
                for item in calculation["inputs"].get("evidence", [])
                if isinstance(item, dict)
            }
            for manifest_id in calculation["inputs"].get("evidence_manifest_ids", []):
                manifest = self.c.data.manifest_get(manifest_id, verify=True)
                frozen = evidence_by_id.get(manifest_id, {})
                if manifest["status"] != "ready" or manifest["content_hash"] != frozen.get("content_hash"):
                    reasons.append(f"evidence_not_current:{manifest_id}")
                    continue
                try:
                    observed = parse(frozen.get("observed_at"))
                except Exception:
                    reasons.append(f"evidence_time_invalid:{manifest_id}")
                    continue
                if now - observed > timedelta(days=maximum_age):
                    reasons.append(f"evidence_stale:{manifest_id}")
            if reasons:
                current_status = "research_only"
        else:
            if frozen_status not in {"eligible_for_shadow", "eligible_for_decision"}:
                reasons.append("frozen_validation_not_research_eligible")
            strategy_id = calculation["inputs"].get("strategy_version_id")
            strategy = self.c.research.strategy_get(strategy_id)
            if strategy["content_hash"] != calculation["inputs"].get("strategy_content_hash"):
                reasons.append("strategy_version_changed")
            for experiment_id in calculation["inputs"].get("experiment_run_ids", []):
                experiment = self.c.research.experiment_get(experiment_id)
                if experiment["status"] != "succeeded" or experiment["strategy_version_id"] != strategy_id:
                    reasons.append(f"experiment_not_current:{experiment_id}")
            for manifest_id in calculation["inputs"].get("evidence_manifest_ids", []):
                manifest = self.c.data.manifest_get(manifest_id, verify=True)
                if manifest["status"] != "ready":
                    reasons.append(f"evidence_not_current:{manifest_id}")
            if strategy["status"] not in {"research_passed", "shadow"}:
                reasons.append("strategy_not_research_qualified")
                current_status = "research_only"
            elif frozen_status == "eligible_for_decision":
                books = [
                    item
                    for item in self.c.shadow.book_list()
                    if item["strategy_version_id"] == strategy_id and item["status"] == "active"
                ]
                forward_ready = any(
                    self.c.shadow.sample_status(item["id"])["status"] == "eligible_for_review"
                    for item in books
                )
                if strategy["status"] != "shadow" or not forward_ready:
                    reasons.append("forward_sample_no_longer_eligible")
                    current_status = "eligible_for_shadow"
            integrity_reasons = [
                reason for reason in reasons if reason != "forward_sample_no_longer_eligible"
            ]
            if integrity_reasons:
                current_status = "research_only"
        return {
            "calculation_id": calculation["id"],
            "kind": calculation["kind"],
            "frozen_status": frozen_status,
            "current_status": current_status,
            "eligible_for_decision": current_status == "eligible_for_decision" and not reasons,
            "eligible_for_bounded_action": current_status
            in {"eligible_for_bounded_action", "eligible_for_decision"}
            and not reasons,
            "reasons": reasons,
            "checked_at": iso(now),
        }

    @staticmethod
    def _has_unvalidated_prediction(value: Any) -> bool:
        if isinstance(value, dict):
            validation = value.get("validation_status")
            if validation is not None and validation not in {
                "validated",
                "forward_sample_available",
            }:
                return True
            return any(ResearchValidationService._has_unvalidated_prediction(item) for item in value.values())
        if isinstance(value, list):
            return any(ResearchValidationService._has_unvalidated_prediction(item) for item in value)
        return False

    @staticmethod
    def _contains_predictive_content(value: Any) -> bool:
        predictive_keys = {
            "prediction",
            "predictions",
            "forecast",
            "forecasts",
            "expected_return",
            "target_price",
            "recommendation_state",
            "signal_direction",
            "signals",
        }
        if isinstance(value, dict):
            if predictive_keys & set(value):
                return True
            return any(
                ResearchValidationService._contains_predictive_content(item)
                for item in value.values()
            )
        if isinstance(value, list):
            return any(
                ResearchValidationService._contains_predictive_content(item)
                for item in value
            )
        if isinstance(value, str):
            # Bounded eligibility must be earned by contemporaneous facts, not
            # by hiding a forecast in a free-text claim under a factual label.
            predictive_language = re.compile(
                r"(?:未来|预计|预期|预测|目标价|将(?:上涨|下跌|增长|下降)|"
                r"forecast|expected\s+(?:return|price|growth)|target\s+price|"
                r"will\s+(?:rise|fall|increase|decrease|grow|decline))",
                re.IGNORECASE,
            )
            return bool(predictive_language.search(value))
        return False

    def _manifests(self, manifest_ids: list[str]) -> list[dict[str, Any]]:
        if not isinstance(manifest_ids, list) or not manifest_ids:
            raise CompanionError("research validation requires evidence Manifest IDs")
        if any(not isinstance(item, str) or not item for item in manifest_ids):
            raise CompanionError("research validation Manifest IDs must be non-empty strings")
        if len(manifest_ids) != len(set(manifest_ids)):
            raise CompanionError("research validation Manifest IDs must be unique")
        return [self.c.data.manifest_get(item, verify=True) for item in manifest_ids]

    @staticmethod
    def _text(value: Any, field: str) -> str:
        if not isinstance(value, str) or not value.strip():
            raise CompanionError(f"{field} must be non-empty")
        return value.strip()

    @classmethod
    def _strings(cls, value: Any, field: str, *, allow_empty: bool = False) -> list[str]:
        if not isinstance(value, list):
            raise CompanionError(f"{field} must be a list")
        result = [cls._text(item, field) for item in value]
        if len(result) != len(set(result)):
            raise CompanionError(f"{field} must be unique")
        if not allow_empty and not result:
            raise CompanionError(f"{field} must be non-empty")
        return result
