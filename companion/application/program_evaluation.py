from __future__ import annotations

from typing import Any


class ProgramEvaluationCoverage:
    """Connect objective account performance to Program evaluation coverage."""

    def __init__(self, companion):
        self.c = companion

    def resolve(
        self, program: dict[str, Any], period_start: str, period_end: str
    ) -> dict[str, Any]:
        account_ids = program["current_revision"]["content"].get("account_ids", [])
        base = {
            "user_time": {
                "status": "insufficient_evidence",
                "reason": "no Program-scoped user-time ledger exists",
            },
            "token_and_data_cost": {
                "status": "insufficient_evidence",
                "reason": "model and data costs are not yet attributed to this Program",
            },
        }
        if len(account_ids) != 1:
            reason = "performance v1 requires exactly one Program account"
            return {
                "coverage": {
                    "portfolio_return": {"status": "insufficient_evidence", "reason": reason},
                    "benchmark_return": {"status": "insufficient_evidence", "reason": reason},
                    "transaction_cost": {"status": "insufficient_evidence", "reason": reason},
                    "maximum_drawdown": {"status": "insufficient_evidence", "reason": reason},
                    **base,
                },
                "performance_calculation_ids": [],
            }
        matching = []
        for calculation in self.c.financial.calculation_list(
            kind="account_performance", limit=500
        ):
            outputs = calculation["outputs"]
            if (
                outputs.get("account_id") == account_ids[0]
                and outputs.get("period") == {"start": period_start, "end": period_end}
            ):
                matching.append(calculation)
        if not matching:
            reason = "requires an account Performance Calculation for the exact Program period"
            return {
                "coverage": {
                    "portfolio_return": {"status": "insufficient_evidence", "reason": reason},
                    "benchmark_return": {"status": "insufficient_evidence", "reason": reason},
                    "transaction_cost": {"status": "insufficient_evidence", "reason": reason},
                    "maximum_drawdown": {"status": "insufficient_evidence", "reason": reason},
                    **base,
                },
                "performance_calculation_ids": [],
            }
        calculation = matching[0]

        def metric(name: str) -> dict[str, Any]:
            value = calculation["outputs"].get(name)
            if value is None:
                return {
                    "status": "insufficient_evidence",
                    "reason": f"Performance Calculation does not contain {name}",
                    "calculation_id": calculation["id"],
                }
            return {
                "status": "ready",
                "calculation_id": calculation["id"],
                "output_path": f"outputs.{name}",
                "value": value,
            }

        return {
            "coverage": {
                "portfolio_return": metric("modified_dietz_return"),
                "benchmark_return": metric("benchmark_return"),
                "transaction_cost": metric("transaction_cost"),
                "maximum_drawdown": metric("maximum_drawdown"),
                **base,
            },
            "performance_calculation_ids": [calculation["id"]],
        }
