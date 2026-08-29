from __future__ import annotations

from typing import Any

from .foundation import CompanionError
from .timeutil import iso, parse, utc_now


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
        historical_refs: list[str] | None = None,
        operation: str = "create",
        review_id: str | None = None,
        supersedes_revision_id: str | None = None,
        supersession_reason: str | None = None,
    ) -> dict[str, Any]:
        if operation not in {"create", "supersede"}:
            raise CompanionError("review operation must be create or supersede")
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
        lineage = historical_refs or [*calculation_ids, *source_refs]
        if (
            not lineage
            or any(not isinstance(item, str) or not item.strip() for item in lineage)
            or len(lineage) != len(set(lineage))
        ):
            raise CompanionError("review historical_refs must be non-empty and unique")
        cutoff = knowledge_cutoff or iso()
        if parse(cutoff) > utc_now():
            raise CompanionError("review knowledge_cutoff cannot be in the future")
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
                    "each proposed change must contain target_type, target_id, change, "
                    "reason and validation_required"
                )
            if item["target_type"] not in {"thesis", "strategy", "policy", "research_pipeline"}:
                raise CompanionError("unsupported proposed-change target_type")
            for field in ("target_id", "change", "reason"):
                if not isinstance(item[field], str) or not item[field].strip():
                    raise CompanionError(f"proposed change {field} must be non-empty")
            if item["validation_required"] is not True:
                raise CompanionError("every proposed change must require validation")

        if operation == "create":
            if any(
                value is not None
                for value in (review_id, supersedes_revision_id, supersession_reason)
            ):
                raise CompanionError(
                    "new review cannot include supersession references"
                )
            review = self.c.cognition.object_create("review", subject)
            parent_revision_id = None
        else:
            if not review_id or not supersedes_revision_id:
                raise CompanionError(
                    "investment_review.revision_conflict: supersession requires "
                    "review_id and supersedes_revision_id"
                )
            if not isinstance(supersession_reason, str) or not supersession_reason.strip():
                raise CompanionError(
                    "investment_review.revision_conflict: supersession requires a reason"
                )
            review = self.c.cognition.object_get(review_id)
            if review["object_type"] != "review":
                raise CompanionError(
                    "investment_review.revision_conflict: target is not a Review"
                )
            if review.get("current_revision_id") != supersedes_revision_id:
                raise CompanionError(
                    "investment_review.revision_conflict: supersedes_revision_id is not current"
                )
            if review["subject"] != subject:
                raise CompanionError(
                    "investment_review.revision_conflict: supersession cannot change Review subject"
                )
            parent_revision_id = supersedes_revision_id
        metadata = {
            "review_contract_version": 1,
            "operation": operation,
            "conclusion": conclusion,
            "source_refs": list(source_refs),
            "historical_refs": list(lineage),
            "supersedes_revision_id": parent_revision_id,
            "supersession_reason": supersession_reason,
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
            knowledge_cutoff=cutoff,
            calculation_ids=calculation_ids,
            metadata=metadata,
        )
        return {
            "review": self._review_projection(
                self.c.cognition.object_get(review["id"])
            ),
            "revision": self._revision_projection(revision),
            "conclusion": conclusion,
            "change_proposal": metadata["change_proposal"],
            "history_preserved": True,
            "automatic_changes_applied": False,
        }

    def summaries(self, *, limit: int = 20) -> list[dict[str, Any]]:
        if limit <= 0 or limit > 100:
            raise CompanionError("review summary limit must be within 1..100")
        summaries = []
        for review in self.c.cognition.object_list("review")[:limit]:
            revisions = [
                self._revision_projection(revision)
                for revision in self.c.cognition.revision_list(review["id"])
            ]
            summaries.append(
                {
                    **self._review_projection(review),
                    "current_revision": revisions[-1] if revisions else None,
                    "revisions": revisions,
                }
            )
        return summaries

    @staticmethod
    def _review_projection(review: dict[str, Any]) -> dict[str, Any]:
        return {
            key: review.get(key)
            for key in (
                "id",
                "object_type",
                "subject",
                "status",
                "current_revision_id",
                "created_at",
                "updated_at",
            )
        }

    @staticmethod
    def _revision_projection(revision: dict[str, Any]) -> dict[str, Any]:
        projection = {
            key: revision.get(key)
            for key in (
                "id",
                "object_id",
                "revision",
                "status",
                "parent_id",
                "knowledge_cutoff",
                "calculation_ids",
                "metadata",
                "created_at",
            )
        }
        projection["knowledge_cutoff"] = (
            projection["knowledge_cutoff"] or projection["created_at"]
        )
        return projection
