from __future__ import annotations

from typing import Any

from ..foundation import CompanionError
from ..timeutil import iso


class InvestmentHomeService:
    """Version-neutral application facade for the five user workflows.

    This layer composes existing truth owners.  It does not own investment
    facts, perform trades, or let research mutate live strategies.
    """

    SCHEMA = "investment-companion.investment-home/v1"

    def __init__(self, companion):
        self.c = companion

    def home(self) -> dict[str, Any]:
        effective_at = iso()
        today = self.c.operating.today()
        status = self.c.operating.status()
        delivery = self.c.delivery.status()
        performance = self.c.financial.calculation_list(
            kind="account_performance", limit=1
        )
        research = self.c.research_catalog.context(limit=3)
        current_program = self.c.operating.program_current()
        research_work = self.c.research_work.summary(program_id=current_program["id"] if current_program else None)
        validations = self._research_validations(limit=3)
        active_schedules = self.c.schedule_list(status="active")
        recent_runs = self.c.run_list(limit=50)
        account_id = self._single_program_account()
        portfolio_qualification = (
            self.c.portfolio_qualification.evaluate_account(
                account_id=account_id, as_of=effective_at
            ).stable_projection(compact=True)
            if account_id
            else None
        )
        return {
            "schema": self.SCHEMA,
            "as_of": effective_at,
            "state": today["mode"],
            "message": today["message"],
            "program": today.get("program"),
            "actions": today.get("action_cards", today.get("next_actions", [])),
            "deferred": today.get("deferred_queue", []),
            "execution": today.get("execution_summary"),
            "research": {
                "records": research["records"],
                "boundary": research["boundary"],
                "validations": validations,
                "work_queue": research_work,
            },
            "evaluation": performance[0] if performance else None,
            "portfolio_qualification": portfolio_qualification,
            "workflow": {
                "active_schedule_count": len(active_schedules),
                "next_schedules": [
                    {
                        "id": item["id"],
                        "name": item["name"],
                        "kind": item["kind"],
                        "next_run_at": item.get("next_run_at"),
                    }
                    for item in active_schedules[:5]
                ],
                "pending_run_count": sum(
                    item["status"] in {"queued", "recoverable", "leased"}
                    for item in recent_runs
                ),
            },
            "delivery": delivery,
            "claims": {
                **status["claims"],
                "manual_execution_only": True,
                "research_cannot_trade": True,
            },
            "production_health": self.c.production_health(),
        }

    def program_context(
        self, *, program_id: str | None = None, status: str | None = None
    ) -> dict[str, Any]:
        return {
            "schema": "investment-companion.program-context/v1",
            "as_of": iso(),
            "current": self.c.operating.program_current(),
            "selected": self.c.operating.program_get(program_id) if program_id else None,
            "programs": self.c.operating.program_list(status),
            "truth": "immutable_program_revisions_and_confirmed_context_refs",
        }

    def workflow_context(
        self,
        *,
        view: str,
        schedule_id: str | None = None,
        run_id: str | None = None,
        plan_id: str | None = None,
        delivery_id: str | None = None,
        status: str | None = None,
        kind: str | None = None,
        mode: str | None = None,
        limit: int = 50,
    ) -> dict[str, Any] | list[dict[str, Any]]:
        if view == "schedules":
            return self.c.schedule_list(status, kind)
        if view == "schedule":
            if not schedule_id:
                raise CompanionError("workflow schedule view requires schedule_id")
            return self.c.schedule_get(schedule_id)
        if view == "schedule_history":
            if not schedule_id:
                raise CompanionError("workflow schedule_history view requires schedule_id")
            return self.c.schedule_history(schedule_id, limit)
        if view == "runs":
            return self.c.run_list(status, limit)
        if view == "run":
            if not run_id:
                raise CompanionError("workflow run view requires run_id")
            return self.c.run_get(run_id)
        if view == "deliveries":
            return self.c.delivery.list(status=status, mode=mode, limit=limit)
        if view == "delivery":
            if not delivery_id:
                raise CompanionError("workflow delivery view requires delivery_id")
            return self.c.delivery.get(delivery_id)
        if view == "delivery_status":
            return self.c.delivery.status()
        if view == "system_status":
            return self.c.system_status()
        if view == "doctor":
            return self.c.doctor_model_projection()
        if view == "execution_strategies":
            return self.c.execution_strategy.list(status=status, limit=limit)
        if view == "execution_strategy":
            if not plan_id:
                raise CompanionError("workflow execution_strategy view requires plan_id")
            return self.c.execution_strategy.get(plan_id)
        raise CompanionError(
            "workflow view must be schedules, schedule, schedule_history, runs, run, "
            "deliveries, delivery, delivery_status, system_status, doctor, execution_strategies or execution_strategy"
        )

    def portfolio_context(
        self,
        *,
        account_id: str | None = None,
        as_of: str | None = None,
        prices: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        account_id = account_id or self._single_program_account()
        if not account_id:
            raise CompanionError("portfolio context requires an account_id")
        effective_at = as_of or iso()
        account = self.c.financial.account_get(account_id)
        portfolio = self.c.financial.portfolio_state(effective_at, account_id, prices)
        exposure = self.c.financial.portfolio_exposure(
            effective_at,
            account_id,
            prices or {},
            account["base_currency"],
        )
        investor = self.c.cognition.context_current("investor")
        mandate = self.c.cognition.context_current("mandate")
        qualification = self.c.portfolio_qualification.evaluate_account(
            account_id=account_id, as_of=effective_at
        )
        truth_freshness = qualification.legacy_truth_freshness()
        return {
            "schema": "investment-companion.portfolio-context/v1",
            "as_of": effective_at,
            "account": account,
            "portfolio": portfolio,
            "exposure": exposure,
            "pending_transactions": self.c.financial.ledger_list(
                account_id, "needs_confirmation", 50
            ),
            "investor": investor,
            "mandate": mandate,
            "policy": self._policy_summary(),
            "truth": "confirmed_ledger_replay",
            "portfolio_qualification": qualification.stable_projection(),
            "truth_freshness": truth_freshness,
            "precision_boundary": qualification.legacy_precision_boundary(),
        }

    def research_context(
        self, *, subject_id: str | None = None, work_item_id: str | None = None, limit: int = 20
    ) -> dict[str, Any]:
        opportunities = self.c.operating.opportunity_list(limit=limit)
        if subject_id:
            opportunities = [
                item for item in opportunities if subject_id in self._subject_values(item)
            ]
        catalog = self.c.research_catalog.context(subject_id=subject_id, limit=limit)
        validations = self._research_validations(subject_id=subject_id, limit=limit)
        return {
            "schema": "investment-companion.research-context/v1",
            "as_of": iso(),
            "subject_id": subject_id,
            "opportunities": opportunities,
            "strategies": self.c.research.strategy_list(),
            "records": catalog["records"],
            "validations": validations,
            "work_queue": {
                "summary": self.c.research_work.summary(program_id=(self.c.operating.program_current() or {}).get("id")),
                "selected": self.c.research_work.get(work_item_id) if work_item_id else None,
                "items": self.c.research_work.list(program_id=(self.c.operating.program_current() or {}).get("id"), limit=limit),
            },
            "boundary": {
                **catalog["boundary"],
                "may_produce": ["evidence", "hypothesis", "signal", "opportunity"],
                "may_not_produce": ["ledger_entry", "execution", "automatic_trade"],
                "action_requires": {
                    "standard": "eligible_for_decision research validation",
                    "bounded_conditional": "eligible_for_bounded_action plus confirmed bounded risk policy",
                },
            },
        }

    def decision_context(self, *, limit: int = 20) -> dict[str, Any]:
        queue = self.c.operating.queue_list(limit=limit)
        executions = self.c.execution.list(limit=limit)
        cards, invalid = [], []
        for item in queue:
            if item["state"] not in {"ready", "presented", "accepted"}:
                continue
            try:
                cards.append(self.c.operating.queue_card(item["id"]))
            except CompanionError as exc:
                invalid.append({"queue_id": item["id"], "error": str(exc)})
        return {
            "schema": "investment-companion.decision-context/v1",
            "as_of": iso(),
            "queue": queue,
            "action_cards": cards,
            "invalid": invalid,
            "recent_decisions": self._objects_with_current_revision("decision", limit),
            "executions": executions,
            "execution_boundary": {
                "mode": "human_manual_only",
                "accepted_action_card_is_order": False,
                "reported_fill_changes_portfolio": False,
                "confirmed_ledger_fill_changes_portfolio": True,
            },
        }

    def evaluation_context(self, *, limit: int = 20) -> dict[str, Any]:
        return {
            "schema": "investment-companion.evaluation-context/v1",
            "as_of": iso(),
            "performance": self._performance_summaries(limit),
            "reviews": self.c.review.summaries(limit=limit),
            "research_reviews": self.c.research_catalog.reviews(limit=limit),
            "change_boundary": {
                "review_may_propose": True,
                "review_may_apply_strategy_change": False,
                "new_version_and_validation_required": True,
                "review_history_is_append_only": True,
            },
        }

    def _performance_summaries(self, limit: int) -> list[dict[str, Any]]:
        summaries = []
        for calculation in self.c.financial.calculation_list(
            kind="account_performance", limit=limit
        ):
            outputs = dict(calculation["outputs"])
            warnings = list(calculation["warnings"])
            attribution_refs = list(outputs.get("attribution_refs", []))
            transaction_cost = outputs.get("transaction_cost", "0")
            outputs.setdefault(
                "time_basis",
                {
                    "period_boundary": "start_exclusive_end_inclusive",
                    "valuation": "point_in_time",
                },
            )
            outputs.setdefault("slippage_cost", "0")
            outputs.setdefault("total_cost", transaction_cost)
            outputs.setdefault(
                "attribution",
                {
                    "reference_ids": attribution_refs,
                    "status": (
                        "linked" if attribution_refs else "insufficient_evidence"
                    ),
                },
            )
            outputs.setdefault(
                "benchmark",
                {
                    "mode": (
                        "compare"
                        if outputs.get("benchmark_return") is not None
                        else "unavailable"
                    ),
                    "return": outputs.get("benchmark_return"),
                    "source_ref": None,
                    "unavailable_reason": (
                        None
                        if outputs.get("benchmark_return") is not None
                        else "historical_calculation_did_not_record_benchmark_source"
                    ),
                },
            )
            outputs.setdefault(
                "costs",
                {
                    "fees_and_taxes": transaction_cost,
                    "slippage": "0",
                    "total": transaction_cost,
                    "slippage_trade_count": 0,
                    "period_trade_count": 0,
                },
            )
            outputs.setdefault("caveats", warnings)
            summaries.append(
                {
                    "id": calculation["id"],
                    "kind": calculation["kind"],
                    "as_of": calculation["as_of"],
                    "outputs": outputs,
                    "warnings": warnings,
                    "created_at": calculation["created_at"],
                }
            )
        return summaries

    def _single_program_account(self) -> str | None:
        program = self.c.operating.program_current()
        if program:
            account_ids = (program.get("current_revision") or {}).get("content", {}).get(
                "account_ids", []
            )
            if len(account_ids) == 1:
                return account_ids[0]
        accounts = self.c.financial.account_list()
        return accounts[0]["id"] if len(accounts) == 1 else None

    def _policy_summary(self) -> dict[str, Any] | None:
        program = self.c.operating.program_current()
        if not program:
            return None
        content = program["current_revision"]["content"]
        return {
            "program_id": program["id"],
            "objective": content.get("objective"),
            "benchmark": content.get("benchmark"),
            "risk_budget": content.get("risk_budget"),
            "stop_conditions": content.get("stop_conditions", []),
        }

    @staticmethod
    def _scan_summary(status: dict[str, Any]) -> dict[str, Any]:
        latest = status.get("latest_scan")
        return {
            "state": status.get("state"),
            "program_id": status.get("program", {}).get("program_id"),
            "latest_scan": latest,
            "latest_review": status.get("latest_review"),
            "error": status.get("error"),
        }

    @classmethod
    def _subject_values(cls, value: Any) -> set[str]:
        if isinstance(value, dict):
            result: set[str] = set()
            for item in value.values():
                result.update(cls._subject_values(item))
            return result
        if isinstance(value, list):
            result = set()
            for item in value:
                result.update(cls._subject_values(item))
            return result
        return {value} if isinstance(value, str) else set()

    def _objects_with_current_revision(
        self, object_type: str, limit: int
    ) -> list[dict[str, Any]]:
        result = []
        for item in self.c.cognition.object_list(object_type)[:limit]:
            revision_id = item.get("current_revision_id")
            result.append(
                {
                    "object": item,
                    "current_revision": (
                        self.c.cognition.revision_get(revision_id) if revision_id else None
                    ),
                }
            )
        return result

    def _research_validations(
        self, *, subject_id: str | None = None, limit: int = 20
    ) -> list[dict[str, Any]]:
        items = [
            *self.c.financial.calculation_list(kind="thesis_validation", limit=limit),
            *self.c.financial.calculation_list(kind="strategy_validation", limit=limit),
        ]
        if subject_id:
            items = [item for item in items if subject_id in self._subject_values(item)]
        items.sort(key=lambda item: (item["created_at"], item["id"]), reverse=True)
        return items[:limit]
