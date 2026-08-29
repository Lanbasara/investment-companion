from __future__ import annotations

from datetime import timedelta
from decimal import Decimal, ROUND_HALF_UP
from typing import Any

from ..financial import dec, dtext
from ..foundation import CompanionError
from ..quant_runtime import RealitySpec
from ..timeutil import iso, parse, utc_now


class InvestmentCommandService:
    """Narrow write commands that preserve domain confirmation boundaries."""

    def __init__(self, companion):
        self.c = companion

    def context_draft(
        self,
        *,
        context_type: str,
        content: dict[str, Any],
        reason: str,
        effective_from: str | None = None,
        expires_at: str | None = None,
    ) -> dict[str, Any]:
        return self.c.cognition.context_create(
            context_type,
            content,
            reason,
            effective_from,
            expires_at,
        )

    def context_confirm(self, *, revision_id: str, trial: bool = False) -> dict[str, Any]:
        return self.c.cognition.context_confirm(revision_id, trial)

    def context_update(self, *, operation: str, **payload: Any) -> dict[str, Any]:
        if operation == "draft":
            required = {"context_type", "content", "reason"}
            allowed = required | {"effective_from", "expires_at"}
            self._operation_payload(payload, required, allowed, "context draft")
            return self.context_draft(**payload)
        if operation == "confirm":
            self._operation_payload(
                payload, {"revision_id"}, {"revision_id", "trial"}, "context confirm"
            )
            return self.context_confirm(**payload)
        raise CompanionError("context operation must be draft or confirm")

    def program_update(
        self, *, operation: str, actor: str = "primary-codex", **payload: Any
    ) -> dict[str, Any]:
        contracts = {
            "create": (
                {"name", "content", "context_refs", "reason"},
                {"name", "content", "context_refs", "reason", "expires_at"},
            ),
            "revise": (
                {"program_id", "expected_version", "content", "context_refs", "reason"},
                {
                    "program_id", "expected_version", "content", "context_refs", "reason",
                    "expires_at",
                },
            ),
            "confirm": (
                {"revision_id", "user_approval_ref"},
                {"revision_id", "user_approval_ref", "trial", "supersedes_program_id"},
            ),
            "status": (
                {"program_id", "expected_version", "status", "reason"},
                {"program_id", "expected_version", "status", "reason"},
            ),
        }
        if operation not in contracts:
            raise CompanionError("program operation must be create, revise, confirm or status")
        required, allowed = contracts[operation]
        self._operation_payload(payload, required, allowed, f"program {operation}")
        if operation == "create":
            return self.c.operating.program_create(**payload, actor=actor)
        if operation == "revise":
            return self.c.operating.program_revise(**payload, actor=actor)
        if operation == "confirm":
            revision_id = payload.pop("revision_id")
            return self.c.operating.program_confirm(revision_id, **payload, actor=actor)
        program_id = payload.pop("program_id")
        status = payload.pop("status")
        return self.c.operating.program_set_status(
            program_id, status, **payload, actor=actor
        )

    def opportunity_update(
        self, *, operation: str, actor: str = "primary-codex", **payload: Any
    ) -> dict[str, Any]:
        contracts = {
            "create": (
                {"subject", "evidence_refs", "reason"},
                {
                    "subject", "evidence_refs", "reason", "program_id", "thesis_id",
                    "strategy_version_id",
                },
            ),
            "transition": (
                {
                    "opportunity_id", "expected_version", "to_stage", "to_status",
                    "evidence_refs", "reason",
                },
                {
                    "opportunity_id", "expected_version", "to_stage", "to_status",
                    "evidence_refs", "reason", "qualification", "decision_revision_id",
                    "idempotency_key",
                },
            ),
            "funding_condition_set": (
                {
                    "opportunity_id",
                    "expected_version",
                    "funding_condition_calculation_id",
                    "reason",
                },
                {
                    "opportunity_id",
                    "expected_version",
                    "funding_condition_calculation_id",
                    "reason",
                    "idempotency_key",
                },
            ),
            "work_claim": (
                {"item_id"}, {"item_id", "owner", "lease_seconds"},
            ),
            "triage_complete": (
                {"item_id", "dispositions"}, {"item_id", "owner", "dispositions"},
            ),
            "research_complete": (
                {"item_id", "outcome", "reason"},
                {"item_id", "owner", "outcome", "reason", "result_refs", "opportunity_id", "next_check_at"},
            ),
        }
        if operation not in contracts:
            raise CompanionError("unsupported opportunity or research-work operation")
        required, allowed = contracts[operation]
        self._operation_payload(payload, required, allowed, f"opportunity {operation}")
        if operation in {"work_claim", "triage_complete", "research_complete"}:
            item_id = payload.pop("item_id")
            owner = payload.pop("owner", actor)
            if operation == "work_claim":
                return self.c.research_work.claim(item_id, owner=owner, **payload)
            if operation == "triage_complete":
                return self.c.research_work.complete_triage(item_id, owner=owner, **payload)
            return self.c.research_work.complete_research(item_id, owner=owner, **payload)
        if operation == "create":
            return self.c.operating.opportunity_create(**payload, actor=actor)
        opportunity_id = payload.pop("opportunity_id")
        if operation == "funding_condition_set":
            return self.c.operating.opportunity_set_funding_condition(
                opportunity_id, **payload, actor=actor
            )
        return self.c.operating.opportunity_transition(
            opportunity_id, **payload, actor=actor
        )

    def transaction_record(self, **entry: Any) -> dict[str, Any]:
        """Record a claimed real-world fact, always pending user confirmation."""
        if "status" in entry:
            raise CompanionError("transaction_record does not accept status")
        return self.c.financial.ledger_add(**entry, status="needs_confirmation")

    def transaction_confirm(self, *, entry_id: str) -> dict[str, Any]:
        return self.c.financial.ledger_confirm(entry_id)

    def transaction_update(self, *, operation: str, **payload: Any) -> dict[str, Any]:
        if operation == "account_create":
            required = {"name", "base_currency"}
            allowed = required | {"institution", "metadata"}
            self._operation_payload(payload, required, allowed, "account create")
            return self.c.financial.account_create(**payload)
        if operation == "asset_register":
            required = {"asset_type", "name", "currency", "identifiers"}
            allowed = required | {"metadata"}
            self._operation_payload(payload, required, allowed, "asset register")
            return self.c.financial.asset_upsert(**payload)
        if operation == "record":
            required = {"account_id", "entry_type", "occurred_at", "amount", "currency", "source"}
            allowed = required | {
                "asset_id", "quantity", "price", "fee", "settled_at", "external_id", "metadata"
            }
            self._operation_payload(payload, required, allowed, "transaction record")
            return self.transaction_record(**payload)
        if operation == "confirm":
            self._operation_payload(payload, {"entry_id"}, {"entry_id"}, "transaction confirm")
            return self.transaction_confirm(entry_id=payload["entry_id"])
        if operation == "reverse":
            self._operation_payload(
                payload,
                {"entry_id", "reason"},
                {"entry_id", "reason", "occurred_at"},
                "transaction reverse",
            )
            return self.c.financial.ledger_reverse(
                payload["entry_id"], payload["reason"], payload.get("occurred_at")
            )
        if operation == "reconcile":
            self._operation_payload(
                payload,
                {"account_id", "as_of", "statement"},
                {"account_id", "as_of", "statement", "source_ref"},
                "transaction reconcile",
            )
            return self.c.financial.reconcile(
                payload["account_id"],
                payload["as_of"],
                payload["statement"],
                payload.get("source_ref"),
            )
        if operation == "continuity_confirm":
            self._operation_payload(
                payload,
                {"account_id", "confirmed_at", "user_confirmation_ref", "reporting_commitment"},
                {"account_id", "confirmed_at", "user_confirmation_ref", "reporting_commitment"},
                "transaction continuity_confirm",
            )
            return self.c.financial.continuity_confirm(**payload)
        if operation == "continuity_revoke":
            self._operation_payload(
                payload,
                {"confirmation_id", "reason"},
                {"confirmation_id", "reason"},
                "transaction continuity_revoke",
            )
            return self.c.financial.continuity_revoke(**payload)
        raise CompanionError(
            "transaction operation must be account_create, asset_register, record, confirm, "
            "reverse, reconcile, continuity_confirm or continuity_revoke"
        )

    def evidence_update(self, *, operation: str, **payload: Any) -> dict[str, Any]:
        if operation == "publish_source":
            required = {
                "subject", "source", "source_group", "first_known_at", "observed_at",
                "claims", "evidence_type",
            }
            allowed = required | {"url", "published_at", "content", "metadata", "supersedes"}
            self._operation_payload(payload, required, allowed, "evidence publish_source")
            if not isinstance(payload["subject"], dict) or not payload["subject"]:
                raise CompanionError("evidence subject must be a non-empty object")
            if not isinstance(payload["claims"], list) or not payload["claims"]:
                raise CompanionError("evidence claims must be a non-empty list")
            if any(not isinstance(item, str) or not item.strip() for item in payload["claims"]):
                raise CompanionError("evidence claims must contain non-empty strings")
            if payload["evidence_type"] not in {
                "observed_fact",
                "official_disclosure",
                "validated_analysis",
                "predictive_signal",
            }:
                raise CompanionError("unsupported evidence_type")
            first_known = parse(payload["first_known_at"])
            observed = parse(payload["observed_at"])
            if first_known > observed:
                raise CompanionError("evidence first_known_at cannot be later than observed_at")
            if observed > utc_now() + timedelta(seconds=5):
                raise CompanionError("evidence observed_at cannot be in the future")
            supersedes = payload.pop("supersedes", None)
            return self.c.data.manifest_publish(
                kind="investment_evidence",
                schema_version="investment-companion.evidence/v1",
                manifest=payload,
                supersedes=supersedes,
            )
        if operation == "market_snapshot":
            required = {"asset_id", "metric", "value", "observed_at", "source"}
            allowed = required | {"quality", "currency", "metadata"}
            self._operation_payload(payload, required, allowed, "evidence market_snapshot")
            return self.c.financial.market_add(**payload)
        raise CompanionError("evidence operation must be publish_source or market_snapshot")

    def action_respond(
        self,
        *,
        queue_id: str,
        state: str,
        reason: str | None = None,
        snoozed_until: str | None = None,
        attention_decision_id: str | None = None,
        user_confirmation_ref: str | None = None,
        actor: str = "primary-codex",
    ) -> dict[str, Any]:
        return self.c.operating.queue_respond(
            queue_id,
            state=state,
            reason=reason,
            snoozed_until=snoozed_until,
            attention_decision_id=attention_decision_id,
            user_confirmation_ref=user_confirmation_ref,
            actor=actor,
        )

    def action_update(
        self, *, operation: str, actor: str = "primary-codex", **payload: Any
    ) -> dict[str, Any]:
        contracts = {
            "enqueue": (
                {"opportunity_id", "decision_revision_id"},
                {
                    "opportunity_id", "decision_revision_id", "manual_action_spec_id",
                    "valid_until", "idempotency_key",
                },
            ),
            "respond": (
                {"queue_id", "state"},
                {
                    "queue_id", "state", "reason", "snoozed_until",
                    "attention_decision_id", "user_confirmation_ref",
                },
            ),
        }
        if operation not in contracts:
            raise CompanionError("action operation must be enqueue or respond")
        required, allowed = contracts[operation]
        self._operation_payload(payload, required, allowed, f"action {operation}")
        if operation == "enqueue":
            opportunity_id = payload.pop("opportunity_id")
            return self.c.operating.queue_enqueue(
                opportunity_id, **payload, actor=actor
            )
        if payload.get("state") in {"accepted", "rejected", "snoozed"}:
            reference = payload.get("user_confirmation_ref")
            if not isinstance(reference, str) or not reference.strip():
                raise CompanionError(
                    "Action Card user response requires user_confirmation_ref"
                )
        return self.action_respond(**payload, actor=actor)

    def workflow_update(
        self, *, operation: str, actor: str = "primary-codex", **payload: Any
    ) -> dict[str, Any] | None:
        contracts = {
            "schedule_create": (
                {"name", "kind", "mission", "cadence"},
                {
                    "name", "kind", "mission", "cadence", "scope", "policy", "origin",
                    "timezone", "dispatch_type", "job_definition_id",
                },
            ),
            "schedule_patch": (
                {"schedule_id", "expected_version", "changes"},
                {"schedule_id", "expected_version", "changes", "reason"},
            ),
            "schedule_status": (
                {"schedule_id", "expected_version", "status"},
                {"schedule_id", "expected_version", "status", "reason"},
            ),
            "schedule_run_now": ({"schedule_id"}, {"schedule_id"}),
            "run_complete": ({"run_id", "success"}, {"run_id", "success", "error"}),
            "run_cancel": ({"run_id", "reason"}, {"run_id", "reason"}),
            "wake_claim": ({"owner"}, {"owner", "lease_seconds"}),
            "wake_complete": (
                {"outbox_id", "owner", "success"},
                {"outbox_id", "owner", "success", "error"},
            ),
        }
        if operation not in contracts:
            raise CompanionError(
                "workflow operation must be schedule_create, schedule_patch, schedule_status, "
                "schedule_run_now, run_complete, run_cancel, wake_claim or wake_complete"
            )
        required, allowed = contracts[operation]
        self._operation_payload(payload, required, allowed, f"workflow {operation}")
        if operation == "schedule_create":
            return self.c.schedule_create(**payload, actor=actor)
        if operation == "schedule_patch":
            return self.c.schedule_patch(
                payload["schedule_id"], payload["expected_version"], payload["changes"],
                actor, payload.get("reason"),
            )
        if operation == "schedule_status":
            if payload["status"] not in {"active", "paused", "archived"}:
                raise CompanionError("schedule status must be active, paused or archived")
            return self.c.schedule_set_status(
                payload["schedule_id"], payload["status"], actor, payload.get("reason"),
                expected_version=payload["expected_version"],
            )
        if operation == "schedule_run_now":
            return self.c.schedule_run_now(payload["schedule_id"], actor)
        if operation == "run_complete":
            return self.c.complete_run(
                payload["run_id"], payload["success"], payload.get("error")
            )
        if operation == "run_cancel":
            return self.c.run_cancel(payload["run_id"], payload["reason"], actor)
        if operation == "wake_claim":
            return self.c.wake_claim(payload["owner"], payload.get("lease_seconds", 1800))
        return self.c.wake_complete(
            payload["outbox_id"], payload["owner"], payload["success"], payload.get("error")
        )

    def delivery_update(
        self, *, operation: str, **payload: Any
    ) -> dict[str, Any] | list[dict[str, Any]]:
        result_fields = {
            "conclusion", "summary", "key_evidence", "next_step", "next_check_at",
            "source_refs",
        }
        contracts = {
            "prepare": (
                {"delivery_id", "conclusion", "summary", "key_evidence", "next_step"},
                {"delivery_id"} | result_fields,
            ),
            "digest_send": (
                {"delivery_ids", "conclusion", "summary", "key_evidence", "next_step"},
                {"delivery_ids"} | result_fields,
            ),
            "attention_decide": (
                {"topic", "materiality", "confidence", "reason"},
                {
                    "topic", "materiality", "confidence", "reason", "event_id", "evidence",
                    "requested_action",
                },
            ),
            "attention_delivered": (
                {"attention_decision_id"}, {"attention_decision_id"}
            ),
            "attention_feedback": (
                {"attention_decision_id", "feedback"},
                {"attention_decision_id", "feedback", "note"},
            ),
        }
        if operation not in contracts:
            raise CompanionError(
                "delivery operation must be prepare, digest_send, attention_decide, "
                "attention_delivered or attention_feedback"
            )
        required, allowed = contracts[operation]
        self._operation_payload(payload, required, allowed, f"delivery {operation}")
        if operation == "prepare":
            delivery_id = payload.pop("delivery_id")
            return self.c.delivery.prepare(delivery_id, **payload)
        if operation == "digest_send":
            delivery_ids = payload.pop("delivery_ids")
            return self.c.delivery.digest_send(delivery_ids, **payload)
        if operation == "attention_decide":
            return self.c.attention.decide(**payload)
        decision_id = payload.pop("attention_decision_id")
        if operation == "attention_delivered":
            return self.c.attention.mark_delivered(decision_id)
        return self.c.attention.feedback(decision_id, **payload)

    def brief_update(
        self, *, operation: str, actor: str = "primary-codex", **payload: Any
    ) -> dict[str, Any]:
        contracts = {
            "publish": (
                {"brief_type", "period_key", "as_of", "conclusion", "payload", "source_refs"},
                {
                    "brief_type", "period_key", "as_of", "conclusion", "payload",
                    "source_refs", "idempotency_key", "program_id",
                },
            ),
            "presented": (
                {"brief_id", "attention_decision_id"},
                {"brief_id", "attention_decision_id"},
            ),
            "metrics_calculate": (
                {"period_start", "period_end"},
                {"period_start", "period_end", "program_id"},
            ),
            "scorecard_publish": (
                {"period_start", "period_end", "metrics", "comparisons", "source_refs", "caveats"},
                {
                    "period_start", "period_end", "metrics", "comparisons", "source_refs",
                    "caveats", "program_id",
                },
            ),
        }
        if operation not in contracts:
            raise CompanionError(
                "brief operation must be publish, presented, metrics_calculate or scorecard_publish"
            )
        required, allowed = contracts[operation]
        self._operation_payload(payload, required, allowed, f"brief {operation}")
        if operation == "publish":
            return self.c.operating.brief_prepare(**payload, actor=actor)
        if operation == "presented":
            brief_id = payload.pop("brief_id")
            return self.c.operating.brief_mark_presented(
                brief_id, **payload, actor=actor
            )
        if operation == "metrics_calculate":
            return self.c.operating.program_metrics_calculate(**payload)
        return self.c.operating.scorecard_publish(**payload, actor=actor)

    def execution_update(
        self, *, operation: str, actor: str = "primary-codex", **payload: Any
    ) -> dict[str, Any]:
        actions = {
            "prepare": self.c.execution.prepare_from_queue,
            "order": self.c.execution.mark_ordered,
            "report_fill": self.c.execution.report_fill,
            "confirm_fill": self.c.execution.confirm_fill,
            "cancel": self.c.execution.cancel,
            "strategy_create": self.c.execution_strategy.create_from_queue,
            "strategy_configured": self.c.execution_strategy.mark_configured,
            "strategy_activate": self.c.execution_strategy.activate,
            "strategy_order_report": self.c.execution_strategy.report_order,
            "strategy_terminate_request": self.c.execution_strategy.request_termination,
            "strategy_terminated": self.c.execution_strategy.report_terminated,
            "strategy_etf_dividend": self.c.execution_strategy.report_etf_dividend_termination,
            "strategy_sleep": self.c.execution_strategy.set_sleeping,
            "strategy_exception": self.c.execution_strategy.report_exception,
            "strategy_reconcile": self.c.execution_strategy.reconcile,
        }
        handler = actions.get(operation)
        if not handler:
            raise CompanionError(
                "unsupported execution lifecycle operation"
            )
        contracts = {
            "prepare": ({"queue_id", "idempotency_key"}, {"queue_id", "idempotency_key"}),
            "order": (
                {"execution_id", "broker_order_ref", "ordered_at"},
                {"execution_id", "broker_order_ref", "ordered_at"},
            ),
            "report_fill": (
                {"execution_id", "occurred_at", "quantity", "price", "fee", "source"},
                {
                    "execution_id", "occurred_at", "quantity", "price", "fee", "source",
                    "external_id", "settled_at", "final",
                },
            ),
            "confirm_fill": (
                {"execution_id", "entry_id", "final"},
                {"execution_id", "entry_id", "final"},
            ),
            "cancel": ({"execution_id", "reason"}, {"execution_id", "reason"}),
            "strategy_create": ({"queue_id","plan_type","spec","valid_until","idempotency_key"},{"queue_id","plan_type","spec","valid_until","idempotency_key"}),
            "strategy_configured": ({"plan_id","broker_condition_ref","configured_at","broker_validity_sessions","broker_valid_until"},{"plan_id","broker_condition_ref","configured_at","broker_validity_sessions","broker_valid_until"}),
            "strategy_activate": ({"plan_id","occurred_at"},{"plan_id","occurred_at"}),
            "strategy_order_report": ({"plan_id","broker_order_ref","side","quantity","status","triggered_at"},{"plan_id","broker_order_ref","side","quantity","status","triggered_at","trigger_price","reference_price_before","reference_price_after","rejection_reason","cancelled_quantity","condition_leg"}),
            "strategy_terminate_request": ({"plan_id","occurred_at","reason"},{"plan_id","occurred_at","reason"}),
            "strategy_terminated": ({"plan_id","occurred_at","reason"},{"plan_id","occurred_at","reason"}),
            "strategy_etf_dividend": ({"plan_id","occurred_at","corporate_action_ref"},{"plan_id","occurred_at","corporate_action_ref"}),
            "strategy_sleep": ({"plan_id","direction","sleeping","occurred_at","reason"},{"plan_id","direction","sleeping","occurred_at","reason"}),
            "strategy_exception": ({"plan_id","occurred_at","reason"},{"plan_id","occurred_at","reason"}),
            "strategy_reconcile": ({"plan_id","occurred_at","reconciliation_id"},{"plan_id","occurred_at","reconciliation_id"}),
        }
        required, allowed = contracts[operation]
        self._operation_payload(payload, required, allowed, f"execution {operation}")
        return handler(**payload, actor=actor)

    @staticmethod
    def _operation_payload(
        payload: dict[str, Any], required: set[str], allowed: set[str], label: str
    ) -> None:
        missing = required - set(payload)
        unknown = set(payload) - allowed
        if missing:
            raise CompanionError(f"{label} missing: {sorted(missing)}")
        if unknown:
            raise CompanionError(f"{label} has unknown fields: {sorted(unknown)}")

    def research_publish(
        self,
        *,
        subject: dict[str, Any],
        content: str,
        evidence_manifest_ids: list[str],
        knowledge_cutoff: str,
        validation_spec: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if not isinstance(subject, dict) or not subject:
            raise CompanionError("research subject must be a non-empty object")
        if not isinstance(content, str) or not content.strip():
            raise CompanionError("research content must be non-empty")
        evidence = self._ready_manifests(evidence_manifest_ids)
        if parse(knowledge_cutoff) > utc_now():
            raise CompanionError("research knowledge_cutoff cannot be in the future")
        normalized_validation = (
            self.c.research_validation.normalize_thesis_spec(validation_spec)
            if validation_spec is not None
            else None
        )
        thesis = self.c.cognition.object_create("thesis", subject)
        revision = self.c.cognition.publish(
            thesis["id"],
            content,
            knowledge_cutoff=knowledge_cutoff,
            metadata={
                "research_contract_version": 1,
                "evidence_manifest_ids": [item["id"] for item in evidence],
                "research_only": True,
                "automatic_decision_or_execution": False,
            },
        )
        validation = (
            self.c.research_validation.validate_thesis(
                thesis_revision_id=revision["id"],
                evidence_manifest_ids=[item["id"] for item in evidence],
                knowledge_cutoff=knowledge_cutoff,
                validation_spec=normalized_validation,
            )
            if normalized_validation is not None
            else None
        )
        return {
            "thesis": self.c.cognition.object_get(thesis["id"]),
            "revision": revision,
            "validation": validation,
        }

    def decision_publish(
        self,
        *,
        subject: dict[str, Any],
        content: str,
        decision_kind: str,
        account_id: str,
        as_of: str,
        knowledge_cutoff: str,
        valid_until: str,
        thesis_revision_ids: list[str],
        evidence_manifest_ids: list[str],
        invalidators: list[str],
        no_action: dict[str, Any],
        alternatives: list[dict[str, Any]],
        prices: dict[str, Any] | None = None,
        risk_calculation_id: str | None = None,
        research_validation_calculation_id: str | None = None,
        portfolio_qualification_calculation_id: str | None = None,
        execution_plan: dict[str, Any] | None = None,
        execution_sell_risk_calculation_id: str | None = None,
    ) -> dict[str, Any]:
        if decision_kind not in {"action", "conditional_action", "no_action", "watch"}:
            raise CompanionError(
                "decision_kind must be action, conditional_action, no_action or watch"
            )
        if not isinstance(subject, dict) or not subject:
            raise CompanionError("decision subject must be a non-empty object")
        candidate_claims = self._decision_candidate_claims(subject)
        if not isinstance(content, str) or not content.strip():
            raise CompanionError("decision content must be non-empty")
        if not thesis_revision_ids or len(thesis_revision_ids) != len(set(thesis_revision_ids)):
            raise CompanionError("decision requires unique Thesis revision IDs")
        for revision_id in thesis_revision_ids:
            revision = self.c.cognition.revision_get(revision_id)
            thesis = self.c.cognition.object_get(revision["object_id"])
            if (
                thesis["object_type"] != "thesis"
                or thesis["status"] != "active"
                or thesis.get("current_revision_id") != revision_id
            ):
                raise CompanionError("decision requires current active Thesis revisions")
        now = utc_now()
        if parse(as_of) > now:
            raise CompanionError("decision as_of cannot be in the future")
        if parse(knowledge_cutoff) > now:
            raise CompanionError("decision knowledge_cutoff cannot be in the future")
        if parse(knowledge_cutoff) > parse(as_of):
            raise CompanionError("decision knowledge_cutoff cannot be later than as_of")
        if parse(valid_until) <= now:
            raise CompanionError("decision valid_until must be in the future")
        if not invalidators or any(not isinstance(item, str) or not item.strip() for item in invalidators):
            raise CompanionError("decision requires explicit invalidators")
        if not isinstance(no_action, dict) or not no_action:
            raise CompanionError("decision requires a concrete no_action alternative")
        if not alternatives or any(not isinstance(item, dict) or not item for item in alternatives):
            raise CompanionError("decision requires concrete alternatives")
        evidence = self._ready_manifests(evidence_manifest_ids)
        investor = self.c.cognition.context_current("investor")
        mandate = self.c.cognition.context_current("mandate")
        if not investor or not mandate:
            raise CompanionError("decision requires confirmed current Investor and Mandate")
        portfolio = self.c.financial.portfolio_state(as_of, account_id, prices)
        if portfolio["warnings"]:
            raise CompanionError("decision requires a complete current portfolio valuation")
        calculation_ids = [portfolio["calculation_id"]]
        research_validation = None
        if research_validation_calculation_id:
            research_validation = self._research_validation_for_decision(
                calculation_id=research_validation_calculation_id,
                subject=subject,
                thesis_revision_ids=thesis_revision_ids,
                evidence_manifest_ids=[item["id"] for item in evidence],
                knowledge_cutoff=knowledge_cutoff,
            )
            calculation_ids.append(research_validation["id"])
        action_tier = "bounded" if decision_kind == "conditional_action" else "standard"
        is_action_decision = decision_kind in {"action", "conditional_action"}
        if is_action_decision and not research_validation:
            raise CompanionError(
                "action decision requires an eligible Research Validation Calculation"
            )
        current_validation = (
            self.c.research_validation.revalidate(research_validation["id"])
            if research_validation and is_action_decision
            else None
        )
        if decision_kind == "action" and not current_validation["eligible_for_decision"]:
            raise CompanionError("action decision research is not eligible for decision")
        if decision_kind == "conditional_action" and not current_validation.get(
            "eligible_for_bounded_action", False
        ):
            raise CompanionError("conditional action research is not eligible for bounded action")
        risk = None
        if risk_calculation_id:
            risk = self.c.financial.calculation_get(risk_calculation_id)
            if risk["kind"] != "risk_gate":
                raise CompanionError("decision risk_calculation_id must be a Risk Gate Calculation")
            if risk["inputs"].get("account_id") != account_id:
                raise CompanionError("decision Risk Gate belongs to another account")
            if risk["as_of"] != as_of or risk["outputs"].get("portfolio_calculation_id") != portfolio["calculation_id"]:
                raise CompanionError("decision Risk Gate does not use the frozen current portfolio")
            if risk["assumptions"].get("mandate") != mandate["content"]:
                raise CompanionError("decision Risk Gate does not use the current Mandate")
            if not risk["outputs"].get("market_snapshot_id") or not risk["assumptions"].get("reality_spec"):
                raise CompanionError("action Decision requires frozen market and execution reality")
            if risk["assumptions"].get("valid_until") != valid_until:
                raise CompanionError("decision and Risk Gate must use the same validity")
            subject_asset = subject.get("asset_id")
            if subject_asset and risk["inputs"].get("asset_id") != subject_asset:
                raise CompanionError("decision Risk Gate belongs to another asset")
            calculation_ids.append(risk["id"])
        if is_action_decision and (not risk or risk["outputs"].get("blocked")):
            raise CompanionError("action decision requires a passing Risk Gate Calculation")
        if is_action_decision and risk["assumptions"].get("action_tier", "standard") != action_tier:
            raise CompanionError("decision kind and Risk Gate action tier differ")
        portfolio_qualification = None
        portfolio_qualification_lineage = None
        if is_action_decision and not portfolio_qualification_calculation_id:
            raise CompanionError(
                "action decision requires a current Portfolio Qualification Calculation"
            )
        if (
            decision_kind == "watch"
            and not portfolio_qualification_calculation_id
            and (
                risk is not None
                or any(candidate_claims.values())
            )
        ):
            raise CompanionError(
                "candidate-specific watch Decision requires a Portfolio Qualification Calculation"
            )
        if portfolio_qualification_calculation_id is not None:
            if not risk:
                raise CompanionError(
                    "Decision Portfolio Qualification requires its matching Risk Gate Calculation"
                )
            (
                portfolio_qualification,
                portfolio_qualification_lineage,
            ) = self._portfolio_qualification_for_decision(
                risk=risk,
                calculation_id=portfolio_qualification_calculation_id,
                decision_kind=decision_kind,
                subject=subject,
                candidate_claims=candidate_claims,
                account_id=account_id,
                as_of=as_of,
                valid_until=valid_until,
            )
        authorized_execution_plan=None
        if execution_plan is not None:
            if not is_action_decision or not isinstance(execution_plan,dict) or set(execution_plan)!={"plan_type","spec"}:
                raise CompanionError("execution_plan requires an action Decision and exactly plan_type/spec")
            plan_type=execution_plan["plan_type"]
            normalized=self.c.execution_strategy._normalize_spec(plan_type,execution_plan["spec"])
            if action_tier == "bounded":
                policy = risk["assumptions"].get("bounded_action_policy", {})
                allowed_plan_types = policy.get("allowed_execution_plan_types", [])
                if plan_type == "moving_grid":
                    raise CompanionError(
                        "bounded conditional actions do not support moving_grid"
                    )
                if plan_type not in allowed_plan_types:
                    raise CompanionError("execution plan type is not allowed by bounded policy")
                if normalized["validity_sessions"] != risk["assumptions"].get(
                    "validity_sessions"
                ):
                    raise CompanionError("execution plan and bounded Risk Gate validity differ")
            if normalized["account_id"]!=account_id or normalized["asset_id"]!=subject.get("asset_id"):
                raise CompanionError("execution_plan account or asset differs from Decision")
            risk_quantity=abs(dec(risk["inputs"]["quantity"],"risk quantity"))
            requested=normalized["position_range"]["max_net_buy"] if plan_type=="moving_grid" else self.c.execution_strategy.resolved_quantity(normalized)
            if dec(requested,"execution plan quantity")>risk_quantity:
                raise CompanionError("execution_plan exceeds the frozen Risk Gate quantity")
            primary_signed=dec(risk["inputs"]["quantity"],"risk quantity")
            if plan_type=="priced_buy" and primary_signed<=0:raise CompanionError("priced_buy requires a positive buy Risk Gate quantity")
            if plan_type in {"priced_sell","bracket_exit"} and primary_signed>=0:raise CompanionError(f"{plan_type} requires a negative sell Risk Gate quantity")
            required_low,required_high=self._execution_plan_price_bounds(plan_type,normalized)
            risk_range=risk["assumptions"]["price_range"]
            if dec(risk_range["min"],"Risk Gate minimum price")>required_low or dec(risk_range["max"],"Risk Gate maximum price")<required_high:raise CompanionError("execution_plan price domain exceeds the frozen Risk Gate price range")
            sell_risk_id=None
            if plan_type=="moving_grid":
                if primary_signed<=0:raise CompanionError("moving_grid primary Risk Gate must cover net buying")
                if not execution_sell_risk_calculation_id:raise CompanionError("moving_grid requires a separate sell Risk Gate")
                sell_risk=self.c.financial.calculation_get(execution_sell_risk_calculation_id)
                if sell_risk["kind"]!="risk_gate" or sell_risk["outputs"].get("blocked") or sell_risk["inputs"].get("account_id")!=account_id or sell_risk["inputs"].get("asset_id")!=subject.get("asset_id") or sell_risk["as_of"]!=as_of or sell_risk["outputs"].get("portfolio_calculation_id")!=portfolio["calculation_id"] or sell_risk["assumptions"].get("mandate")!=mandate["content"] or sell_risk["assumptions"].get("valid_until")!=valid_until or sell_risk["assumptions"].get("action_tier","standard")!=action_tier or sell_risk["assumptions"].get("program_revision_id")!=risk["assumptions"].get("program_revision_id") or sell_risk["assumptions"].get("bounded_action_policy")!=risk["assumptions"].get("bounded_action_policy"):
                    raise CompanionError("moving_grid sell Risk Gate does not match the Decision context")
                sell_quantity=dec(sell_risk["inputs"]["quantity"],"grid sell risk quantity")
                if sell_quantity>=0 or abs(sell_quantity)<dec(normalized["position_range"]["max_net_sell"],"max_net_sell"):raise CompanionError("moving_grid sell Risk Gate does not cover max_net_sell")
                sell_range=sell_risk["assumptions"]["price_range"]
                if dec(sell_range["min"])>required_low or dec(sell_range["max"])<required_high:raise CompanionError("moving_grid sell price domain exceeds the frozen sell Risk Gate range")
                calculation_ids.append(sell_risk["id"]);sell_risk_id=sell_risk["id"]
            elif execution_sell_risk_calculation_id is not None:raise CompanionError("separate sell Risk Gate is only supported for moving_grid")
            authorized_execution_plan={"plan_type":plan_type,"spec":normalized,"buy_risk_calculation_id":risk["id"],"sell_risk_calculation_id":sell_risk_id}
        decision = self.c.cognition.object_create("decision", subject)
        context_refs = {
            "investor_revision_id": investor["id"],
            "mandate_revision_id": mandate["id"],
            "portfolio_calculation_id": portfolio["calculation_id"],
            "thesis_revision_ids": thesis_revision_ids,
            "research_validation_calculation_id": (
                research_validation["id"] if research_validation else None
            ),
            "portfolio_qualification_calculation_id": (
                portfolio_qualification.calculation_id
                if portfolio_qualification
                else None
            ),
        }
        revision = self.c.cognition.publish(
            decision["id"],
            content,
            knowledge_cutoff=knowledge_cutoff,
            context_refs=context_refs,
            calculation_ids=calculation_ids,
            metadata={
                "decision_contract_version": 1,
                "decision_kind": decision_kind,
                "action_tier": action_tier if is_action_decision else None,
                "valid_until": valid_until,
                "invalidators": invalidators,
                "no_action": no_action,
                "alternatives": alternatives,
                "source_refs": [item["id"] for item in evidence],
                "risk_calculation_id": risk["id"] if risk else None,
                "research_validation_calculation_id": (
                    research_validation["id"] if research_validation else None
                ),
                "portfolio_qualification_calculation_id": (
                    portfolio_qualification.calculation_id
                    if portfolio_qualification
                    else None
                ),
                "portfolio_qualification": (
                    portfolio_qualification.stable_projection()
                    if portfolio_qualification
                    else None
                ),
                "portfolio_qualification_lineage": (
                    portfolio_qualification_lineage
                ),
                "action_card_eligible": is_action_decision,
                "confirmed_ledger_hash": self.c.financial.confirmed_ledger_hash(),
                "human_execution_only": True,
                "automatic_trade": False,
                "execution_plan": authorized_execution_plan,
                "published_at": iso(),
            },
        )
        return {
            "decision": self.c.cognition.object_get(decision["id"]),
            "revision": revision,
            "portfolio_calculation_id": portfolio["calculation_id"],
            "risk_calculation_id": risk["id"] if risk else None,
            "research_validation_calculation_id": (
                research_validation["id"] if research_validation else None
            ),
            "portfolio_qualification_calculation_id": (
                portfolio_qualification.calculation_id
                if portfolio_qualification
                else None
            ),
        }

    def _portfolio_qualification_for_decision(
        self,
        *,
        risk: dict[str, Any],
        calculation_id: str,
        decision_kind: str,
        subject: dict[str, Any],
        candidate_claims: dict[str, list[Any]],
        account_id: str,
        as_of: str,
        valid_until: str,
    ):
        current, lineage = (
            self.c.portfolio_qualification.revalidate_frozen_risk_candidate(
                risk=risk,
                calculation_id=calculation_id,
                account_id=account_id,
                as_of=as_of,
                valid_until=valid_until,
                current_at=iso(),
            )
        )
        subject_asset = subject.get("asset_id")
        if subject_asset and current.candidate.asset_id != subject_asset:
            raise CompanionError(
                "Decision Portfolio Qualification belongs to another candidate"
            )
        self._validate_decision_candidate_claims(current, candidate_claims)
        if decision_kind in {"action", "conditional_action"}:
            if current.level != "preflight_ready":
                raise CompanionError(
                    "action decision requires a current preflight_ready Portfolio Qualification"
                )
            if risk["outputs"].get("precise_action_eligible") is not True:
                raise CompanionError(
                    "action decision is not eligible for precise action under its Risk and Portfolio Qualification"
                )
        elif decision_kind == "watch":
            if "watch" not in current.allowed_uses:
                raise CompanionError(
                    "watch Decision exceeds the Portfolio Qualification allowed uses"
                )
            if (
                candidate_claims["exact_quantities"]
                and "precise_decision_support" not in current.allowed_uses
            ):
                raise CompanionError(
                    "watch Decision exact quantity exceeds the Portfolio Qualification allowed uses"
                )
            if (
                candidate_claims["quantity_ranges"]
                and "quantity_ranges" not in current.allowed_uses
            ):
                raise CompanionError(
                    "watch Decision quantity range exceeds the Portfolio Qualification allowed uses"
                )
        return current, lineage

    @staticmethod
    def _decision_candidate_claims(value: Any) -> dict[str, list[Any]]:
        claims: dict[str, list[Any]] = {
            "directions": [],
            "exact_quantities": [],
            "quantity_ranges": [],
        }

        def collect(item: Any) -> None:
            if isinstance(item, dict):
                for key, child in item.items():
                    if key in {"direction", "side"}:
                        claims["directions"].append(child)
                    elif key in {"quantity", "exact_quantity"}:
                        claims["exact_quantities"].append(child)
                    elif key in {"quantity_range", "quantity_domain"}:
                        claims["quantity_ranges"].append(child)
                    else:
                        collect(child)
            elif isinstance(item, list):
                for child in item:
                    collect(child)

        collect(value)
        return claims

    @staticmethod
    def _validate_decision_candidate_claims(
        qualification: Any, claims: dict[str, list[Any]]
    ) -> None:
        candidate = qualification.candidate
        directions = claims["directions"]
        if any(direction not in {"buy", "sell"} for direction in directions):
            raise CompanionError("Decision candidate direction must be buy or sell")
        if any(direction != candidate.direction for direction in directions):
            raise CompanionError(
                "Decision candidate direction differs from the frozen Portfolio Qualification"
            )

        maximum = dec(candidate.quantity, "qualified candidate quantity")
        for raw_quantity in claims["exact_quantities"]:
            quantity = dec(raw_quantity, "Decision candidate quantity")
            if quantity == 0 or abs(quantity) > maximum:
                raise CompanionError(
                    "Decision candidate quantity exceeds the frozen Portfolio Qualification quantity domain"
                )
            if quantity < 0 and candidate.direction != "sell":
                raise CompanionError(
                    "Decision candidate quantity direction differs from the frozen Portfolio Qualification"
                )
            if quantity > 0 and not directions and candidate.direction != "buy":
                raise CompanionError(
                    "Decision candidate quantity direction differs from the frozen Portfolio Qualification"
                )

        for raw_range in claims["quantity_ranges"]:
            if not isinstance(raw_range, dict) or set(raw_range) != {"min", "max"}:
                raise CompanionError(
                    "Decision candidate quantity range must contain exactly min and max"
                )
            minimum = dec(raw_range["min"], "Decision candidate minimum quantity")
            maximum_requested = dec(
                raw_range["max"], "Decision candidate maximum quantity"
            )
            if minimum < 0 or minimum > maximum_requested or maximum_requested > maximum:
                raise CompanionError(
                    "Decision candidate quantity range exceeds the frozen Portfolio Qualification quantity domain"
                )

    @staticmethod
    def _execution_plan_price_bounds(plan_type:str,spec:dict[str,Any])->tuple[Decimal,Decimal]:
        if plan_type=="moving_grid":return dec(spec["price_range"]["lower"]),dec(spec["price_range"]["upper"])
        if plan_type in {"priced_buy","priced_sell"}:
            prices=[dec(spec["trigger"]["monitor_price"])]
            if spec["order"]["price_instruction"]=="custom":prices.append(dec(spec["order"]["custom_price"]))
            return min(prices),max(prices)
        base=dec(spec["base_price"])
        take=dec(spec["take_profit"]["value"]);stop=dec(spec["stop_loss"]["value"])
        take_price=take if spec["take_profit"]["mode"]=="price" else base*(Decimal("1")+take/Decimal("100"))
        stop_price=stop if spec["stop_loss"]["mode"]=="price" else base*(Decimal("1")-stop/Decimal("100"))
        return min(stop_price,take_price),max(stop_price,take_price)

    def risk_assess(self, **trade: Any) -> dict[str, Any]:
        mandate = self.c.cognition.context_current("mandate")
        if not mandate:
            raise CompanionError("risk assessment requires a confirmed current Mandate")
        forbidden = {"mandate", "bounded_action_policy", "program_revision_id"} & set(trade)
        if forbidden:
            raise CompanionError(
                "risk_assess derives Mandate and bounded-action policy from confirmed state"
            )
        required = {
            "reality_spec",
            "market_snapshot_id",
            "max_market_age_seconds",
            "valid_until",
            "price_range",
        }
        missing = required - set(trade)
        if missing:
            raise CompanionError(f"risk_assess missing production inputs: {sorted(missing)}")
        reality = RealitySpec.from_value(trade["reality_spec"])
        action_tier = trade.get("action_tier", "standard")
        bounded_policy = None
        program_revision_id = None
        if action_tier == "bounded":
            program = self.c.operating.program_current()
            if program and program.get("current_revision"):
                program_revision_id = program["current_revision"]["id"]
                bounded_policy = program["current_revision"]["content"].get("risk_budget", {}).get(
                    "bounded_action"
                )
        quantity, price = dec(trade["quantity"]), dec(trade["price"])
        gross = abs(quantity) * price
        commission = max(Decimal(reality.minimum_commission), gross * Decimal(reality.commission_rate))
        tax = gross * Decimal(reality.sell_stamp_duty_rate) if quantity < 0 else Decimal("0")
        expected_fee = (commission + tax).quantize(
            Decimal(reality.money_quantum), rounding=ROUND_HALF_UP
        )
        if "fee" in trade and dec(trade["fee"]) != expected_fee:
            raise CompanionError("risk_assess fee differs from RealitySpec")
        trade = {**trade, "fee": dtext(expected_fee), "reality_spec": reality.to_dict()}
        return self.c.risk.assess_trade(
            **trade,
            mandate=mandate["content"],
            bounded_action_policy=bounded_policy,
            program_revision_id=program_revision_id,
        )

    def action_plan(self, **trade: Any) -> dict[str, Any]:
        risk = self.risk_assess(**trade)
        quantity = dec(trade["quantity"], "action quantity")
        risk_calculation = self.c.financial.calculation_get(risk["calculation_id"])
        qualification = self.c.portfolio_qualification.evaluate_risk_candidate(
            risk_calculation
        )
        if risk["portfolio_qualification"]["calculation_id"] != (
            qualification.calculation_id
        ):
            raise CompanionError(
                "Risk and Action Plan produced different Portfolio Qualifications"
            )
        mandate_revision = self.c.cognition.context_current("mandate")
        if not mandate_revision:
            raise CompanionError("Action Plan requires a confirmed current Mandate")
        funding_condition = self.c.funding_condition.evaluate(
            risk=risk_calculation,
            qualification=qualification,
            mandate_revision=mandate_revision,
        )
        precision = qualification.account_qualification.legacy_precision_boundary()
        truth_freshness = qualification.account_qualification.legacy_truth_freshness()
        exact_sizing = "precise_decision_support" in qualification.allowed_uses
        conditional_sizing = "quantity_ranges" in qualification.allowed_uses
        risk_clear = not risk["blocked"]
        quantity_status = (
            "finalizable_after_broker_preflight"
            if exact_sizing
            else "conditional_only"
            if conditional_sizing
            else "withheld_by_qualification"
        )
        return {
            "schema": "investment-companion.portfolio-action-plan/v1",
            "action": {
                "account_id": trade["account_id"],
                "asset_id": trade["asset_id"],
                "side": "buy" if quantity > 0 else "sell",
                "quantity": dtext(abs(quantity)) if exact_sizing else None,
                "reference_price": dtext(dec(trade["price"], "action price")),
                "price_range": trade["price_range"],
                "valid_until": trade["valid_until"],
                "validity_sessions": trade.get("validity_sessions"),
                "quantity_status": quantity_status,
            },
            "risk": risk,
            "candidate_qualification": qualification.stable_projection(),
            "funding_condition": funding_condition,
            "precision_boundary": precision,
            "truth_freshness": truth_freshness,
            "eligible_for_decision": risk["precise_action_eligible"],
            "conditional_sizing_available": conditional_sizing,
            "decision_blockers": (["risk_gate"] if not risk_clear else [])
            + (["portfolio_qualification"] if not exact_sizing else []),
            "action_tier": trade.get("action_tier", "standard"),
            "eligible_for_conditional_decision": trade.get("action_tier", "standard") == "bounded"
            and risk["precise_action_eligible"],
            "automatic_decision_or_execution": False,
        }

    def performance_calculate(self, **period: Any) -> dict[str, Any]:
        return self.c.performance.calculate_period(**period)

    def review_publish(self, **review: Any) -> dict[str, Any]:
        return self.c.review.publish(**review)

    def _ready_manifests(self, manifest_ids: list[str]) -> list[dict[str, Any]]:
        if not manifest_ids or len(manifest_ids) != len(set(manifest_ids)):
            raise CompanionError("evidence_manifest_ids must be non-empty and unique")
        result = [self.c.data.manifest_get(item, verify=True) for item in manifest_ids]
        if any(item["status"] != "ready" for item in result):
            raise CompanionError("evidence manifests must be current and ready")
        return result

    def _research_validation_for_decision(
        self,
        *,
        calculation_id: str,
        subject: dict[str, Any],
        thesis_revision_ids: list[str],
        evidence_manifest_ids: list[str],
        knowledge_cutoff: str,
    ) -> dict[str, Any]:
        calculation = self.c.financial.calculation_get(calculation_id)
        if calculation["kind"] not in {"thesis_validation", "strategy_validation"}:
            raise CompanionError(
                "research_validation_calculation_id must be a Research Validation Calculation"
            )
        if parse(calculation["as_of"]) != parse(knowledge_cutoff):
            raise CompanionError(
                "decision and Research Validation must use the same knowledge_cutoff"
            )
        if calculation["kind"] == "thesis_validation":
            revision_id = calculation["inputs"].get("thesis_revision_id")
            if revision_id not in thesis_revision_ids:
                raise CompanionError("Research Validation belongs to another Thesis revision")
            validation_evidence = calculation["inputs"].get("evidence_manifest_ids", [])
            if not set(validation_evidence) <= set(evidence_manifest_ids):
                raise CompanionError(
                    "decision evidence does not include the validated Thesis evidence"
                )
            validation_asset = calculation["inputs"].get("subject", {}).get("asset_id")
            if validation_asset and subject.get("asset_id") != validation_asset:
                raise CompanionError("Research Validation belongs to another asset")
        else:
            strategy_id = calculation["inputs"].get("strategy_version_id")
            if not strategy_id or subject.get("strategy_version_id") != strategy_id:
                raise CompanionError(
                    "Strategy Validation requires the matching strategy_version_id in Decision subject"
                )
            strategy = self.c.research.strategy_get(strategy_id)
            if strategy["status"] != "shadow":
                raise CompanionError("validated Strategy is no longer active in Shadow")
            validation_evidence = calculation["inputs"].get("evidence_manifest_ids", [])
            if not set(validation_evidence) <= set(evidence_manifest_ids):
                raise CompanionError(
                    "decision evidence does not include the validated Strategy evidence"
                )
        return calculation
