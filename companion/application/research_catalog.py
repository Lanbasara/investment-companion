from __future__ import annotations

from typing import Any

from ..foundation import CompanionError


class ResearchCatalogService:
    """Version-neutral projection of every active research pipeline.

    Legacy scanners and forecast pipelines remain implementation adapters.  The
    catalog gives Codex one stable record shape and never upgrades a pipeline
    label into a formal StrategyVersion.  Decision eligibility continues to
    come only from an immutable Research Validation Calculation.
    """

    SCHEMA = "investment-companion.research-catalog/v1"
    RECORD_SCHEMA = "investment-companion.research-record/v1"

    _METHOD_IDS = {
        "continuous_candidate_scan": "cross-sectional-momentum/mainboard-20-session",
        "continuous_forward_review": "cross-sectional-momentum/mainboard-20-session",
        "stock_candidates": "equity-candidate-discovery",
        "stock_provisional_signals": "equity-provisional-momentum",
        "fund_candidates": "fund-candidate-discovery",
        "fund_provisional_signals": "fund-provisional-trend",
        "portfolio_forecast": "portfolio-forecast",
        "predictive_review": "predictive-outcome-review",
    }

    def __init__(self, companion):
        self.c = companion

    def context(
        self, *, subject_id: str | None = None, limit: int = 20
    ) -> dict[str, Any]:
        if limit <= 0 or limit > 100:
            raise CompanionError("research catalog limit must be within 1..100")
        records = [*self._continuous_records(), *self._predictive_records(limit)]
        if subject_id:
            records = [
                item for item in records if subject_id in self._values(item["items"])
            ]
        records.sort(
            key=lambda item: (item.get("created_at") or "", item["record_id"]),
            reverse=True,
        )
        return {
            "schema": self.SCHEMA,
            "records": records[:limit],
            "boundary": {
                "research_only_unless_validated": True,
                "formal_strategy_requires_registry_entry": True,
                "decision_requires_eligible_validation": True,
                "automatic_decision_or_execution": False,
            },
        }

    def reviews(self, *, limit: int = 20) -> list[dict[str, Any]]:
        return [
            item
            for item in self.context(limit=limit)["records"]
            if item["record_type"] in {"continuous_forward_review", "predictive_review"}
        ]

    def _continuous_records(self) -> list[dict[str, Any]]:
        status = self.c.quant_research.status()
        result = []
        latest = status.get("latest_scan")
        if latest:
            result.append(
                self._record(
                    record_id=latest["manifest_id"],
                    record_type="continuous_candidate_scan",
                    created_at=latest.get("created_at") or latest.get("as_of"),
                    as_of=latest.get("as_of"),
                    items=latest.get("candidates", []),
                    source_refs=[latest["manifest_id"]],
                    validation=self._validation_for_manifest(latest["manifest_id"]),
                )
            )
        review = status.get("latest_review")
        if review:
            review_id = review.get("manifest_id") or review.get("id")
            if review_id:
                result.append(
                    self._record(
                        record_id=review_id,
                        record_type="continuous_forward_review",
                        created_at=review.get("created_at") or review.get("reviewed_at"),
                        as_of=review.get("reviewed_at") or review.get("as_of"),
                        items=[review],
                        source_refs=[review_id],
                        validation=self._validation_for_manifest(review_id),
                    )
                )
        return result

    def _predictive_records(self, limit: int) -> list[dict[str, Any]]:
        legacy = self.c.predictive_research.context(limit=limit)
        return [
            self._record(
                record_id=item["manifest_id"],
                record_type=item["type"],
                created_at=item.get("created_at"),
                as_of=item.get("as_of"),
                items=item.get("items", []),
                source_refs=[item["manifest_id"]],
                validation=item.get("research_validation"),
            )
            for item in legacy.get("records", [])
        ]

    def _record(
        self,
        *,
        record_id: str,
        record_type: str,
        created_at: str | None,
        as_of: str | None,
        items: list[Any],
        source_refs: list[str],
        validation: dict[str, Any] | None,
    ) -> dict[str, Any]:
        strategy_ids = sorted(self._strategy_ids(items))
        registered = []
        unknown = []
        for strategy_id in strategy_ids:
            try:
                strategy = self.c.research.strategy_get(strategy_id)
            except CompanionError:
                unknown.append(strategy_id)
            else:
                registered.append(
                    {
                        "id": strategy["id"],
                        "content_hash": strategy["content_hash"],
                        "status": strategy["status"],
                    }
                )
        method_id = self._METHOD_IDS.get(record_type, record_type)
        normalized_validation = validation or {
            "calculation_id": None,
            "current_status": "research_only",
            "eligible_for_decision": False,
            "reasons": ["formal_research_validation_calculation_required"],
        }
        return {
            "schema": self.RECORD_SCHEMA,
            "record_id": record_id,
            "record_type": record_type,
            "created_at": created_at,
            "as_of": as_of,
            "method": {
                "method_id": method_id,
                "registered_strategy_versions": registered,
                "unregistered_strategy_labels": unknown,
                "identity_status": (
                    "formal_strategy_version"
                    if registered and not unknown
                    else "research_method_only"
                ),
            },
            "items": items,
            "source_refs": source_refs,
            "validation": normalized_validation,
        }

    def _validation_for_manifest(self, manifest_id: str) -> dict[str, Any] | None:
        for kind in ("thesis_validation", "strategy_validation"):
            for calculation in self.c.financial.calculation_list(kind=kind, limit=500):
                if manifest_id not in calculation["inputs"].get("evidence_manifest_ids", []):
                    continue
                try:
                    return self.c.research_validation.revalidate(calculation["id"])
                except CompanionError as exc:
                    return {
                        "calculation_id": calculation["id"],
                        "current_status": "invalid",
                        "eligible_for_decision": False,
                        "reasons": [str(exc)],
                    }
        return None

    @classmethod
    def _strategy_ids(cls, value: Any) -> set[str]:
        if isinstance(value, dict):
            result = set()
            strategy_id = value.get("strategy_version_id")
            if isinstance(strategy_id, str) and strategy_id.strip():
                result.add(strategy_id.strip())
            for item in value.values():
                result.update(cls._strategy_ids(item))
            return result
        if isinstance(value, list):
            result = set()
            for item in value:
                result.update(cls._strategy_ids(item))
            return result
        return set()

    @classmethod
    def _values(cls, value: Any) -> set[str]:
        if isinstance(value, dict):
            result = set()
            for item in value.values():
                result.update(cls._values(item))
            return result
        if isinstance(value, list):
            result = set()
            for item in value:
                result.update(cls._values(item))
            return result
        return {value} if isinstance(value, str) else set()
