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
                {"program_id", "status", "reason"},
                {"program_id", "status", "reason"},
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
        }
        if operation not in contracts:
            raise CompanionError("opportunity operation must be create or transition")
        required, allowed = contracts[operation]
        self._operation_payload(payload, required, allowed, f"opportunity {operation}")
        if operation == "create":
            return self.c.operating.opportunity_create(**payload, actor=actor)
        opportunity_id = payload.pop("opportunity_id")
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
        raise CompanionError(
            "transaction operation must be account_create, asset_register, record, confirm, "
            "reverse or reconcile"
        )

    def evidence_update(self, *, operation: str, **payload: Any) -> dict[str, Any]:
        if operation == "publish_source":
            required = {
                "subject", "source", "source_group", "first_known_at", "observed_at",
                "claims",
            }
            allowed = required | {"url", "published_at", "content", "metadata", "supersedes"}
            self._operation_payload(payload, required, allowed, "evidence publish_source")
            if not isinstance(payload["subject"], dict) or not payload["subject"]:
                raise CompanionError("evidence subject must be a non-empty object")
            if not isinstance(payload["claims"], list) or not payload["claims"]:
                raise CompanionError("evidence claims must be a non-empty list")
            if any(not isinstance(item, str) or not item.strip() for item in payload["claims"]):
                raise CompanionError("evidence claims must contain non-empty strings")
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
        actor: str = "primary-codex",
    ) -> dict[str, Any]:
        return self.c.operating.queue_respond(
            queue_id,
            state=state,
            reason=reason,
            snoozed_until=snoozed_until,
            attention_decision_id=attention_decision_id,
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
                {"queue_id", "state", "reason", "snoozed_until", "attention_decision_id"},
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
                {"schedule_id", "status"},
                {"schedule_id", "status", "reason"},
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
                payload["schedule_id"], payload["status"], actor, payload.get("reason")
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
        }
        handler = actions.get(operation)
        if not handler:
            raise CompanionError(
                "execution operation must be prepare, order, report_fill, confirm_fill or cancel"
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
    ) -> dict[str, Any]:
        if decision_kind not in {"action", "no_action", "watch"}:
            raise CompanionError("decision_kind must be action, no_action or watch")
        if not isinstance(subject, dict) or not subject:
            raise CompanionError("decision subject must be a non-empty object")
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
        if decision_kind == "action" and not research_validation:
            raise CompanionError(
                "action decision requires an eligible Research Validation Calculation"
            )
        if decision_kind == "action" and research_validation["outputs"].get("status") != "eligible_for_decision":
            raise CompanionError("action decision research is not eligible for decision")
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
        if decision_kind == "action" and (not risk or risk["outputs"].get("blocked")):
            raise CompanionError("action decision requires a passing Risk Gate Calculation")
        decision = self.c.cognition.object_create("decision", subject)
        context_refs = {
            "investor_revision_id": investor["id"],
            "mandate_revision_id": mandate["id"],
            "portfolio_calculation_id": portfolio["calculation_id"],
            "thesis_revision_ids": thesis_revision_ids,
            "research_validation_calculation_id": (
                research_validation["id"] if research_validation else None
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
                "valid_until": valid_until,
                "invalidators": invalidators,
                "no_action": no_action,
                "alternatives": alternatives,
                "source_refs": [item["id"] for item in evidence],
                "risk_calculation_id": risk["id"] if risk else None,
                "research_validation_calculation_id": (
                    research_validation["id"] if research_validation else None
                ),
                "confirmed_ledger_hash": self.c.financial.confirmed_ledger_hash(),
                "human_execution_only": True,
                "automatic_trade": False,
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
        }

    def risk_assess(self, **trade: Any) -> dict[str, Any]:
        mandate = self.c.cognition.context_current("mandate")
        if not mandate:
            raise CompanionError("risk assessment requires a confirmed current Mandate")
        if "mandate" in trade:
            raise CompanionError("risk_assess always uses the confirmed current Mandate")
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
        return self.c.risk.assess_trade(**trade, mandate=mandate["content"])

    def action_plan(self, **trade: Any) -> dict[str, Any]:
        risk = self.risk_assess(**trade)
        quantity = dec(trade["quantity"], "action quantity")
        return {
            "schema": "investment-companion.portfolio-action-plan/v1",
            "action": {
                "account_id": trade["account_id"],
                "asset_id": trade["asset_id"],
                "side": "buy" if quantity > 0 else "sell",
                "quantity": dtext(abs(quantity)),
                "reference_price": dtext(dec(trade["price"], "action price")),
                "price_range": trade["price_range"],
                "valid_until": trade["valid_until"],
            },
            "risk": risk,
            "eligible_for_decision": not risk["blocked"],
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
