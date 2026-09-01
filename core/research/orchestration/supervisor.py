from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING, Any, Optional

from core.research.contracts.crawler_report import CrawlerReport
from core.research.contracts.crawler_task import CrawlerTask
from core.research.crawler.base import BaseCrawler
from core.research.state.model import ResearchState
from core.research.types import (
    CrawlerHealthStatus,
    CrawlerReportStatus,
    CrawlerStatus,
    CrawlerTaskStatus,
)

if TYPE_CHECKING:
    from core.research.orchestration.spawner import CrawlerSpawner
    from pkg.sdk.worker import WorkerRuntimeContext

logger = logging.getLogger("AutonomOS.Research.CrawlerSupervisor")


class CrawlerSupervisor:
    """
    Supervises the execution, health, lifecycle, and failure recovery of CrawlerTasks
    across the active Crawler workforce.
    
    Guarantees:
    1. Tasks and crawlers deterministically transition through lifecycle states.
    2. Failed crawlers do not silently disappear.
    3. Running tasks can be explicitly cancelled.
    4. Failed crawlers can be dynamically replaced with on-demand retries.
    """

    def __init__(self):
        self._active_assignments: dict[str, BaseCrawler] = {}  # task_id -> crawler

    def assign_and_execute_all(
        self,
        crawlers: list[BaseCrawler],
        tasks: list[CrawlerTask],
        state: Optional[ResearchState] = None,
        context: Optional[WorkerRuntimeContext] = None,
    ) -> list[CrawlerReport]:
        """
        Distribute tasks among available crawlers, execute them, and collect all reports.
        """
        if not crawlers:
            logger.warning("No crawlers available for execution.")
            return []

        reports: list[CrawlerReport] = []
        crawler_index = 0

        for task in tasks:
            # Select an eligible crawler with matching capability
            needed_caps = task.required_capabilities or [task.required_capability]
            eligible_crawlers = [c for c in crawlers if any(c.has_capability(cap) for cap in needed_caps) and not c.is_busy()]
            if not eligible_crawlers:
                eligible_crawlers = [c for c in crawlers if any(c.has_capability(cap) for cap in needed_caps)]
            if not eligible_crawlers:
                eligible_crawlers = crawlers  # fallback

            assigned_crawler = eligible_crawlers[crawler_index % len(eligible_crawlers)]
            crawler_index += 1

            report = self.execute_task(assigned_crawler, task, context=context)
            reports.append(report)
            if state:
                state.record_crawler_report(report)

        return reports

    def execute_task(
        self,
        crawler: BaseCrawler,
        task: CrawlerTask,
        context: Optional[WorkerRuntimeContext] = None,
    ) -> CrawlerReport:
        """
        Safely execute a single CrawlerTask on a specific crawler.
        Manages lifecycle transitions, error isolation, timing, and receipt creation.
        """
        start_time = time.perf_counter()
        task.attempts += 1
        task.assigned_crawler_id = crawler.id
        if task.status != CrawlerTaskStatus.CANCELLED:
            task.status = CrawlerTaskStatus.RUNNING
        self._active_assignments[task.task_id] = crawler

        logger.info(f"Supervisor dispatching task '{task.task_id}' ({task.query_or_target}) to crawler '{crawler.id}'")

        try:
            crawler.validate_task_compatibility(task)
            report = crawler.execute_crawler_task(task, context=context)
            report.execution_time_seconds = round(time.perf_counter() - start_time, 3)

            if task.status == CrawlerTaskStatus.CANCELLED:
                pass
            elif report.status in (CrawlerReportStatus.SUCCESS, CrawlerReportStatus.PARTIAL, CrawlerReportStatus.EMPTY):
                task.status = CrawlerTaskStatus.COMPLETED
            elif report.status == CrawlerReportStatus.FAILED:
                task.status = CrawlerTaskStatus.FAILED
                task.error_message = report.error_message

            # Reset crawler status so it can accept next task
            crawler.reset_status()
            return report

        except Exception as err:
            logger.error(f"Crawler '{crawler.id}' crashed executing task '{task.task_id}': {err}")
            elapsed = round(time.perf_counter() - start_time, 3)
            if task.status != CrawlerTaskStatus.CANCELLED:
                task.status = CrawlerTaskStatus.FAILED
                task.error_message = str(err)
            crawler.tasks_failed += 1
            crawler.health = CrawlerHealthStatus.DEGRADED

            try:
                crawler.transition_to(CrawlerStatus.FAILED, reason=f"Execution error: {err}")
                crawler.reset_status()
            except Exception:
                pass

            return CrawlerReport(
                report_id=f"crep-err-{task.task_id}",
                crawler_task_id=task.task_id,
                crawler_id=crawler.id,
                request_id=task.request_id,
                plan_id=task.plan_id,
                question_id=task.question_id,
                correlation_id=task.correlation_id,
                status=CrawlerReportStatus.FAILED,
                summary=f"Crawler '{crawler.id}' execution failed.",
                error_message=str(err),
                execution_time_seconds=elapsed,
            )
        finally:
            self._active_assignments.pop(task.task_id, None)

    def cancel_task(self, task_id: str, reason: str = "Cancelled by supervisor") -> bool:
        """
        Explicitly cancel a running crawler task.
        Notifies the assigned crawler and transitions the task status to CANCELLED.
        """
        crawler = self._active_assignments.get(task_id)
        if crawler:
            logger.info(f"Supervisor cancelling task '{task_id}' on crawler '{crawler.id}'")
            return crawler.cancel_task(task_id, reason=reason)
        return False

    def cancel_all(self, reason: str = "Cancelled by supervisor") -> int:
        """Cancel all currently running tasks across the workforce."""
        count = 0
        active_ids = list(self._active_assignments.keys())
        for tid in active_ids:
            if self.cancel_task(tid, reason=reason):
                count += 1
        return count

    def replace_failed_crawler_and_retry(
        self,
        failed_crawler: BaseCrawler,
        task: CrawlerTask,
        spawner: CrawlerSpawner,
        state: Optional[ResearchState] = None,
        context: Optional[WorkerRuntimeContext] = None,
    ) -> CrawlerReport:
        """
        Dynamic recovery: Spawns a replacement crawler when a crawler fails or is degraded,
        and re-dispatches the task to the fresh replacement.
        """
        needed_caps = task.required_capabilities or [task.required_capability]
        replacement = spawner.spawn_replacement_crawler(
            failed_crawler_id=failed_crawler.id,
            required_capabilities=needed_caps,
        )
        logger.info(f"Re-dispatching task '{task.task_id}' to replacement crawler '{replacement.id}'")
        report = self.execute_task(replacement, task, context=context)
        if state:
            state.record_crawler_report(report)
        return report

    def get_workforce_status(self, crawlers: list[BaseCrawler]) -> dict[str, Any]:
        """Return real-time operational status overview of given crawlers."""
        return {
            "total_monitored": len(crawlers),
            "running": sum(1 for c in crawlers if c.status == CrawlerStatus.RUNNING),
            "queued": sum(1 for c in crawlers if c.status == CrawlerStatus.QUEUED),
            "completed": sum(1 for c in crawlers if c.status == CrawlerStatus.COMPLETED),
            "failed": sum(1 for c in crawlers if c.status == CrawlerStatus.FAILED),
            "healthy": sum(1 for c in crawlers if c.health == CrawlerHealthStatus.HEALTHY),
            "active_tasks": len(self._active_assignments),
        }
