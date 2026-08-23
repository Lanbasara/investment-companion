"""Cross-cutting platform services for the modular monolith."""

from .legacy_research import PredictiveResearchAdapter
from .operations import SystemOperationsService
from .outbox import OutboxService
from .workflow import WorkflowService

__all__ = [
    "OutboxService",
    "PredictiveResearchAdapter",
    "SystemOperationsService",
    "WorkflowService",
]
