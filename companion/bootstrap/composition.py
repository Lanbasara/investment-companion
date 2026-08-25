from __future__ import annotations

from ..application import (
    InvestmentBriefingService,
    InvestmentCommandService,
    InvestmentHomeService,
    ResearchCatalogService,
)
from ..application.program_evaluation import ProgramEvaluationCoverage
from ..actionability import ActionabilityService
from ..attention import AttentionEngine
from ..audit import AuditTrail
from ..cognition import CognitiveLedger
from ..data_domain import DataDomain
from ..delivery import DeliveryEngine
from ..execution import ExecutionLifecycleService
from ..execution_strategy import BrokerExecutionStrategyService
from ..financial import FinancialKernel
from ..governance import GateRegistry
from ..jobs import JobEngine
from ..market_calendar import MarketCalendar
from ..operating import InvestmentOperatingSystem
from ..performance import PerformanceEngine
from ..platform.job_handlers import DeterministicJobHandlers
from ..platform.legacy_research import PredictiveResearchAdapter
from ..quant_runtime import NativeQuantRuntime
from ..research import ResearchRegistry
from ..risk import RiskGate
from ..review import ReviewService
from ..shadow import ShadowLedger
from ..validation import ResearchValidationService
from ..v5_quant_experiment import ContinuousQuantResearch
from ..v6_predictive_recommendations import V6PredictiveRecommendations


def compose_services(companion) -> None:
    """Build the modular monolith in one explicit dependency order.

    Companion remains the compatibility facade during the strangler migration;
    service construction no longer lives inside that facade.
    """

    companion.audit = AuditTrail()
    companion.financial = FinancialKernel(companion)
    companion.risk = RiskGate(companion)
    companion.performance = PerformanceEngine(companion)
    companion.review = ReviewService(companion)
    companion.cognition = CognitiveLedger(companion)
    companion.attention = AttentionEngine(companion)
    companion.jobs = JobEngine(companion)
    companion.market_calendar = MarketCalendar()
    companion.data = DataDomain(companion)
    companion.gates = GateRegistry(companion)
    companion.quant = NativeQuantRuntime()
    companion.research = ResearchRegistry(companion)
    companion.shadow = ShadowLedger(companion)
    companion.research_validation = ResearchValidationService(companion)
    companion.actionability = ActionabilityService(companion)
    companion.execution = ExecutionLifecycleService(companion)
    companion.execution_strategy = BrokerExecutionStrategyService(companion)
    companion.program_evaluation = ProgramEvaluationCoverage(companion)
    companion.operating = InvestmentOperatingSystem(companion)
    companion.briefing = InvestmentBriefingService(companion)
    companion.quant_research = ContinuousQuantResearch(companion)
    companion.quant_experiment = companion.quant_research
    companion.v6_predictive = V6PredictiveRecommendations(companion)
    companion.predictive_research = PredictiveResearchAdapter(companion)
    companion.research_catalog = ResearchCatalogService(companion)
    companion.delivery = DeliveryEngine(companion)
    companion.investment = InvestmentHomeService(companion)
    companion.investment_commands = InvestmentCommandService(companion)
    companion.job_handlers = DeterministicJobHandlers(companion)

    companion.jobs.register_handler("system.echo_manifest", "1", companion.job_handlers.echo_manifest)
    companion.jobs.register_handler("data.tushare_ingest", "1", companion.job_handlers.tushare_ingest)
    companion.jobs.register_handler("data.publish_snapshot", "1", companion.job_handlers.publish_snapshot)
    companion.jobs.register_handler("research.native_quant", "1", companion._job_native_quant)
    companion.jobs.register_handler("shadow.rebalance", "1", companion.job_handlers.shadow_rebalance)
