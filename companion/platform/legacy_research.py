from __future__ import annotations

from typing import Any

from ..foundation import CompanionError
from ..v6_predictive_recommendations import (
    FORECAST_KIND,
    FUND_CANDIDATES_KIND,
    FUND_SIGNALS_KIND,
    REVIEW_KIND,
    STOCK_CANDIDATES_KIND,
    STOCK_SIGNALS_KIND,
)


class PredictiveResearchAdapter:
    """Platform adapter from a versioned pipeline to stable research records."""

    KINDS = {
        STOCK_CANDIDATES_KIND: "stock_candidates",
        STOCK_SIGNALS_KIND: "stock_provisional_signals",
        FUND_CANDIDATES_KIND: "fund_candidates",
        FUND_SIGNALS_KIND: "fund_provisional_signals",
        FORECAST_KIND: "portfolio_forecast",
        REVIEW_KIND: "predictive_review",
    }

    def __init__(self, companion):
        self.c = companion

    def context(
        self, *, subject_id: str | None = None, limit: int = 20
    ) -> dict[str, Any]:
        if limit <= 0 or limit > 100:
            raise CompanionError("predictive research context limit must be within 1..100")
        status = self.c.v6_predictive.status()
        program_id = status.get("program", {}).get("program_id")
        validations = self._validation_index()
        records = []
        for kind, record_type in self.KINDS.items():
            for manifest in self.c.data.manifest_list(kind=kind, limit=limit):
                body = manifest["manifest"].get("manifest", {})
                if program_id and body.get("program_id") != program_id:
                    continue
                normalized = self._normalize(
                    manifest, record_type, validations.get(manifest["id"])
                )
                if subject_id:
                    normalized["items"] = [
                        item
                        for item in normalized["items"]
                        if subject_id in self._values(item)
                    ]
                    if not normalized["items"]:
                        continue
                records.append(normalized)
        records.sort(key=lambda item: (item["created_at"], item["manifest_id"]), reverse=True)
        return {
            "state": status["state"],
            "error": status.get("error"),
            "program_id": program_id,
            "records": records[:limit],
            "boundary": {
                "research_only": True,
                "automatic_decision_or_execution": False,
                "forward_validation_required": True,
                "formal_validation_calculation_required": True,
            },
        }

    def reviews(self, *, limit: int = 20) -> list[dict[str, Any]]:
        if limit <= 0 or limit > 100:
            raise CompanionError("predictive review limit must be within 1..100")
        status = self.c.v6_predictive.status()
        program_id = status.get("program", {}).get("program_id")
        result = []
        for manifest in self.c.data.manifest_list(kind=REVIEW_KIND, limit=limit):
            body = manifest["manifest"].get("manifest", {})
            if program_id and body.get("program_id") != program_id:
                continue
            result.append(self._normalize(manifest, "predictive_review", None))
        return result

    def _validation_index(self) -> dict[str, dict[str, Any]]:
        result: dict[str, dict[str, Any]] = {}
        for kind in ("thesis_validation", "strategy_validation"):
            for calculation in self.c.financial.calculation_list(kind=kind, limit=500):
                try:
                    validation = self.c.research_validation.revalidate(calculation["id"])
                except CompanionError as exc:
                    validation = {
                        "calculation_id": calculation["id"],
                        "current_status": "invalid",
                        "eligible_for_decision": False,
                        "reasons": [str(exc)],
                    }
                for manifest_id in calculation["inputs"].get("evidence_manifest_ids", []):
                    result.setdefault(manifest_id, validation)
        return result

    @staticmethod
    def _normalize(
        manifest: dict[str, Any],
        record_type: str,
        validation: dict[str, Any] | None,
    ) -> dict[str, Any]:
        body = manifest["manifest"]["manifest"]
        if record_type.endswith("candidates"):
            items = body.get("candidates", [])
        elif record_type.endswith("signals"):
            items = body.get("signals", [])
        elif record_type == "portfolio_forecast":
            items = [
                {
                    "prediction": body.get("prediction"),
                    "recommendation": body.get("recommendation"),
                    "portfolio_assessment": body.get("portfolio_assessment"),
                }
            ]
        else:
            items = [
                {
                    "reviewed_at": body.get("reviewed_at"),
                    "assessment": body.get("assessment"),
                    "aggregate": body.get("aggregate"),
                    "task_lines": body.get("task_lines"),
                    "provisional_signal_outcomes": body.get("provisional_signal_outcomes"),
                }
            ]
        return {
            "type": record_type,
            "manifest_id": manifest["id"],
            "created_at": manifest["created_at"],
            "as_of": body.get("as_of", body.get("reviewed_at")),
            "items": items,
            "research_validation": validation
            or {
                "calculation_id": None,
                "current_status": "research_only",
                "eligible_for_decision": False,
                "reasons": ["formal_research_validation_calculation_required"],
            },
        }

    @classmethod
    def _values(cls, value: Any) -> set[str]:
        if isinstance(value, dict):
            result: set[str] = set()
            for item in value.values():
                result.update(cls._values(item))
            return result
        if isinstance(value, list):
            result = set()
            for item in value:
                result.update(cls._values(item))
            return result
        return {value} if isinstance(value, str) else set()
