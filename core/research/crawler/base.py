from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Optional
import uuid

from core.research.contracts.crawler_report import CrawlerReport
from core.research.contracts.crawler_task import CrawlerTask
from core.research.crawler.lifecycle import CrawlerStateMachine
from core.research.errors import CrawlerExecutionError
from core.research.types import (
    CrawlerCapability,
    CrawlerHealthStatus,
    CrawlerReportStatus,
    CrawlerStatus,
)

if TYPE_CHECKING:
    from pkg.sdk.worker import WorkerRuntimeContext


def utc_now() -> str:
    """Generate ISO 8601 UTC timestamp."""
    return datetime.now(timezone.utc).isoformat()


class BaseCrawler(ABC):
    """
    Abstract interface and lifecycle container for all Crawler workforce agents.
    Crawlers are dedicated to executing granular data collection tasks,
    extracting structured evidence, and returning machine-validatable CrawlerReports.
    They do NOT plan overarching research or synthesize cross-source conclusions.
    """

    def __init__(
        self,
        crawler_id: Optional[str] = None,
        capabilities: Optional[list[CrawlerCapability]] = None,
        name: str = "BaseCrawler",
    ):
        self.crawler_id = crawler_id or f"crawler-{uuid.uuid4().hex[:6]}"
        self.name = name
        self._capabilities = capabilities or [CrawlerCapability.WEB_SEARCH, CrawlerCapability.WEB_FETCH]
        self.status = CrawlerStatus.CREATED
        self.health = CrawlerHealthStatus.HEALTHY
        self.current_task: Optional[CrawlerTask] = None
        self.tasks_completed: int = 0
        self.tasks_failed: int = 0
        self.last_heartbeat: str = utc_now()
        self.created_at: str = utc_now()

    @property
    def id(self) -> str:
        return self.crawler_id

    @property
    def health_status(self) -> CrawlerHealthStatus:
        """Return the crawler's health status."""
        return self.health

    @property
    def capabilities(self) -> list[CrawlerCapability]:
        """Return the list of declared capabilities for this crawler."""
        return list(self._capabilities)

    def get_capabilities(self) -> list[CrawlerCapability]:
        """Return the list of declared capabilities for this crawler."""
        return list(self._capabilities)

    def has_capability(self, capability: CrawlerCapability) -> bool:
        """Check whether this crawler supports a specific capability."""
        return capability in self._capabilities

    def is_busy(self) -> bool:
        """Check whether the crawler is currently busy executing a task."""
        return self.status == CrawlerStatus.RUNNING and self.current_task is not None

    def heartbeat(self) -> str:
        """Update and return the crawler's liveness heartbeat."""
        self.last_heartbeat = utc_now()
        return self.last_heartbeat

    def transition_to(self, target_status: CrawlerStatus, reason: str = "") -> None:
        """Deterministically validate and transition the crawler status."""
        CrawlerStateMachine.validate_transition(self.status, target_status, reason=reason)
        self.status = target_status
        self.heartbeat()

    def reset_status(self) -> None:
        """Reset the crawler back to QUEUED/ready status after completing, failing, or cancelling a task."""
        if self.status in (CrawlerStatus.COMPLETED, CrawlerStatus.FAILED, CrawlerStatus.CANCELLED):
            self.transition_to(CrawlerStatus.QUEUED, reason="Reset crawler for next task")
        self.current_task = None

    def terminate(self) -> None:
        """Terminate the crawler instance."""
        if self.status != CrawlerStatus.TERMINATED:
            self.transition_to(CrawlerStatus.TERMINATED, reason="Crawler instance terminated")
            self.health = CrawlerHealthStatus.DEAD

    def cancel_task(self, task_id: str, reason: str = "Cancelled by supervisor") -> bool:
        """
        Request cancellation of an active task.
        Returns True if the task was successfully cancelled.
        """
        if self.current_task and self.current_task.task_id == task_id:
            self.current_task.cancel(reason=reason)
            if self.status == CrawlerStatus.RUNNING:
                self.transition_to(CrawlerStatus.CANCELLED, reason=reason)
            return True
        return False

    def validate_task_compatibility(self, task: CrawlerTask) -> None:
        """
        Validate whether this crawler is eligible to execute the given task.
        Raises ValueError if the required capability is unsupported.
        """
        # Match against either required_capability or any in required_capabilities
        needed_caps = task.required_capabilities or [task.required_capability]
        if not any(self.has_capability(c) for c in needed_caps):
            raise ValueError(
                f"Crawler '{self.crawler_id}' with capabilities {[c.value for c in self._capabilities]} "
                f"cannot execute task requiring {[c.value for c in needed_caps]}"
            )

    @abstractmethod
    def execute_crawler_task(
        self,
        task: CrawlerTask,
        context: Optional[WorkerRuntimeContext] = None,
    ) -> CrawlerReport:
        """
        Execute the assigned crawler task within the provided runtime context.
        Must return a structured CrawlerReport with full lineage to the task and request.
        """
        pass

    def execute(
        self,
        task: CrawlerTask,
        context: Optional[WorkerRuntimeContext] = None,
    ) -> CrawlerReport:
        """Convenience alias for execute_crawler_task."""
        return self.execute_crawler_task(task, context=context)
