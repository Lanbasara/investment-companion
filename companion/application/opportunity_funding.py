from __future__ import annotations

from typing import Any

from ..db import row_dict, rows_dict
from ..foundation import CompanionError, digest, new_id
from ..timeutil import iso


class OpportunityFundingConditionMixin:
    """Keep Funding Condition association policy behind the Opportunity seam."""

    def _funding_condition_projection(
        self, opportunity_id: str, *, as_of: str | None = None
    ) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
        with self.db.connect() as con:
            transitions = rows_dict(
                con.execute(
                    "SELECT * FROM opportunity_funding_condition_transitions "
                    "WHERE opportunity_id=? ORDER BY to_version,id",
                    (opportunity_id,),
                ).fetchall()
            )
        if not transitions:
            return None, []

        effective_at = as_of or iso()
        latest_id = transitions[-1]["id"]
        history: list[dict[str, Any]] = []
        current: dict[str, Any] | None = None
        for transition in transitions:
            calculation_id = transition["to_calculation_id"]
            calculation = self.c.financial.calculation_get(calculation_id)
            condition = {
                **calculation["outputs"],
                "calculation_id": calculation_id,
            }
            if transition["id"] == latest_id:
                condition_status = self.c.funding_condition.current_status(
                    calculation_id, as_of=effective_at
                )
                state = (
                    "current"
                    if condition_status["supports_current_planning"]
                    else "expired"
                )
            else:
                condition_status = {
                    "status": "superseded",
                    "supports_current_planning": False,
                    "required_reruns": [],
                }
                state = "superseded"
            projection = {
                "transition_id": transition["id"],
                "calculation_id": calculation_id,
                "replaces_calculation_id": transition["from_calculation_id"],
                "state": state,
                "condition_status": condition_status["status"],
                "supports_current_planning": (
                    state == "current"
                    and condition_status["supports_current_planning"]
                ),
                "opportunity_version": transition["to_version"],
                "linked_at": transition["created_at"],
                "reason": transition["reason"],
                "actor": transition["actor"],
                "required_reruns": condition_status["required_reruns"],
                "calculation": condition,
            }
            history.append(projection)
            if state == "current":
                current = projection
        return current, history

    def _add_funding_condition_projection(
        self, opportunity: dict[str, Any], *, as_of: str | None = None
    ) -> dict[str, Any]:
        current, history = self._funding_condition_projection(
            opportunity["id"], as_of=as_of
        )
        return {
            **opportunity,
            "funding_condition": current,
            "funding_condition_transitions": history,
        }

    def opportunity_set_funding_condition(
        self,
        opportunity_id: str,
        *,
        expected_version: int,
        funding_condition_calculation_id: str,
        reason: str,
        idempotency_key: str | None = None,
        actor: str = "primary-codex",
    ) -> dict[str, Any]:
        """Attach or replace the current Funding Condition without promotion."""

        self.c.jobs.feature_require("v5_operating_system")
        reason = self._text(reason, "Opportunity Funding Condition reason")
        key = idempotency_key or digest(
            "opportunity-funding-condition-set",
            opportunity_id,
            expected_version,
            funding_condition_calculation_id,
            reason,
        )
        self._text(key, "Opportunity Funding Condition idempotency_key")
        transition_hash = digest(
            "investment-companion.opportunity-funding-condition-transition/v1",
            opportunity_id,
            expected_version,
            funding_condition_calculation_id,
            reason,
        )
        with self.db.connect() as con:
            existing = row_dict(
                con.execute(
                    "SELECT * FROM opportunity_funding_condition_transitions "
                    "WHERE idempotency_key=?",
                    (key,),
                ).fetchone()
            )
        if existing:
            if (
                existing["opportunity_id"] != opportunity_id
                or existing["content_hash"] != transition_hash
            ):
                raise CompanionError(
                    "Opportunity Funding Condition idempotency_key belongs to different inputs"
                )
            return self.opportunity_get(opportunity_id)

        item = self.opportunity_get(opportunity_id)
        self._require_active_program(item["program_id"])
        if item["status"] != "active" or item["stage"] != "qualified":
            raise CompanionError(
                "Funding Condition may be set only on an active qualified Opportunity"
            )
        if item["version"] != expected_version:
            raise CompanionError("Opportunity version conflict")

        calculation = self.c.financial.calculation_get(
            funding_condition_calculation_id
        )
        if calculation.get("kind") != "funding_condition":
            raise CompanionError(
                "Opportunity requires a Funding Condition Calculation"
            )
        subject = item.get("subject") or {}
        candidate_account_id = subject.get("account_id")
        candidate_asset_id = subject.get("asset_id")
        condition_account_id = calculation["outputs"].get("account_id")
        condition_asset_id = calculation["outputs"].get("asset_id")
        if (
            not candidate_account_id
            or not candidate_asset_id
            or candidate_account_id != condition_account_id
            or candidate_asset_id != condition_asset_id
        ):
            raise CompanionError(
                "Opportunity and Funding Condition account/asset mismatch"
            )
        condition_status = self.c.funding_condition.current_status(
            funding_condition_calculation_id, as_of=iso()
        )
        if not condition_status["supports_current_planning"]:
            raise CompanionError(
                "Opportunity Funding Condition Calculation is not current"
            )
        with self.db.connect() as con:
            previous = row_dict(
                con.execute(
                    "SELECT * FROM opportunity_funding_condition_transitions "
                    "WHERE opportunity_id=? ORDER BY to_version DESC,id DESC LIMIT 1",
                    (opportunity_id,),
                ).fetchone()
            )
        previous_calculation_id = (
            previous["to_calculation_id"] if previous else None
        )
        if previous_calculation_id == funding_condition_calculation_id:
            raise CompanionError(
                "Funding Condition Calculation is already current for this Opportunity"
            )

        now = iso()
        replayed = False
        with self.db.transaction() as con:
            changed = con.execute(
                "UPDATE opportunities SET version=version+1,updated_at=? "
                "WHERE id=? AND version=? AND status='active' AND stage='qualified'",
                (now, opportunity_id, expected_version),
            ).rowcount
            if changed != 1:
                concurrent = row_dict(
                    con.execute(
                        "SELECT * FROM opportunity_funding_condition_transitions "
                        "WHERE idempotency_key=?",
                        (key,),
                    ).fetchone()
                )
                if (
                    not concurrent
                    or concurrent["opportunity_id"] != opportunity_id
                    or concurrent["content_hash"] != transition_hash
                ):
                    raise CompanionError(
                        "Opportunity Funding Condition update lost to another writer"
                    )
                replayed = True
            if not replayed:
                transition_id = new_id("oppfundingtransition")
                con.execute(
                    "INSERT INTO opportunity_funding_condition_transitions("
                    "id,opportunity_id,from_calculation_id,to_calculation_id,"
                    "from_version,to_version,reason,actor,content_hash,"
                    "idempotency_key,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        transition_id,
                        opportunity_id,
                        previous_calculation_id,
                        funding_condition_calculation_id,
                        expected_version,
                        expected_version + 1,
                        reason,
                        actor,
                        transition_hash,
                        key,
                        now,
                    ),
                )
                self.c.audit.record(
                    con,
                    actor,
                    "set_funding_condition",
                    "opportunity",
                    opportunity_id,
                    before={
                        "stage": item["stage"],
                        "status": item["status"],
                        "version": expected_version,
                        "funding_condition_calculation_id": (
                            previous_calculation_id
                        ),
                    },
                    after={
                        "stage": item["stage"],
                        "status": item["status"],
                        "version": expected_version + 1,
                        "funding_condition_calculation_id": (
                            funding_condition_calculation_id
                        ),
                    },
                    reason=reason,
                )
        return self.opportunity_get(opportunity_id)
