from __future__ import annotations

from typing import Any

from .foundation import CompanionError
from .timeutil import iso


class ReviewService:
    """Publish evidence-backed reviews and inert change proposals.

    A proposal is a cognitive fact. Applying it requires an explicit new
    Thesis or Strategy Version through the existing research lifecycle.
    """

    CONCLUSIONS = {"continue", "revise", "stop", "insufficient_evidence"}

    def __init__(self, companion):
        self.c = companion

    def publish(
        self,
        *,
        subject: dict[str, Any],
        content: str,
        conclusion: str,
        calculation_ids: list[str],
        source_refs: list[str],
        proposed_changes: list[dict[str, Any]] | None = None,
        knowledge_cutoff: str | None = None,
    ) -> dict[str, Any]:
        if conclusion not in self.CONCLUSIONS:
            raise CompanionError("unsupported review conclusion")
        if not isinstance(subject, dict) or not subject:
            raise CompanionError("review subject must be a non-empty object")
        if not isinstance(content, str) or not content.strip():
            raise CompanionError("review content must be non-empty")
        if not calculation_ids or len(calculation_ids) != len(set(calculation_ids)):
            raise CompanionError("review requires unique deterministic Calculation IDs")
        calculations = [self.c.financial.calculation_get(item) for item in calculation_ids]
        if any(not isinstance(item, str) or not item.strip() for item in source_refs):
            raise CompanionError("review source_refs must contain non-empty strings")
        if len(source_refs) != len(set(source_refs)):
            raise CompanionError("review source_refs must be unique")
        changes = proposed_changes or []
        if not isinstance(changes, list) or any(not isinstance(item, dict) for item in changes):
            raise CompanionError("review proposed_changes must be a list of objects")
        if conclusion == "revise" and not changes:
            raise CompanionError("a revise conclusion requires at least one proposed change")
        if conclusion in {"continue", "insufficient_evidence"} and changes:
            raise CompanionError(f"{conclusion} review cannot contain proposed changes")
        for item in changes:
            required = {"target_type", "target_id", "change", "reason", "validation_required"}
            if set(item) != required:
                raise CompanionError(
                    "each proposed change must contain target_type, target_id, change, reason and validation_required"
                )
            if item["target_type"] not in {"thesis", "strategy", "policy", "research_pipeline"}:
                raise CompanionError("unsupported proposed-change target_type")
            for field in ("target_id", "change", "reason"):
                if not isinstance(item[field], str) or not item[field].strip():
                    raise CompanionError(f"proposed change {field} must be non-empty")
            if item["validation_required"] is not True:
                raise CompanionError("every proposed change must require validation")

        review = self.c.cognition.object_create("review", subject)
        metadata = {
            "review_contract_version": 1,
            "conclusion": conclusion,
            "source_refs": list(source_refs),
            "calculation_engines": [
                {
                    "calculation_id": item["id"],
                    "kind": item["kind"],
                    "engine_version": item["engine_version"],
                }
                for item in calculations
            ],
            "change_proposal": {
                "status": "proposed" if changes else "none",
                "changes": changes,
                "requires_new_version": bool(changes),
                "automatic_application": False,
            },
            "published_at": iso(),
        }
        revision = self.c.cognition.publish(
            review["id"],
            content.strip(),
            knowledge_cutoff=knowledge_cutoff,
            calculation_ids=calculation_ids,
            metadata=metadata,
        )
        return {
            "review": self.c.cognition.object_get(review["id"]),
            "revision": revision,
            "conclusion": conclusion,
            "change_proposal": metadata["change_proposal"],
        }
