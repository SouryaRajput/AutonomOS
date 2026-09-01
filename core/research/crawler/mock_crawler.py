from __future__ import annotations

import hashlib
import logging
import time
from typing import TYPE_CHECKING, Optional
import uuid

from core.research.contracts.crawler_report import CrawlerReport, RawSourceReference
from core.research.contracts.crawler_task import CrawlerTask
from core.research.contracts.evidence import EvidenceItem, EvidenceProvenance
from core.research.crawler.base import BaseCrawler
from core.research.types import (
    CrawlerCapability,
    CrawlerReportStatus,
    CrawlerStatus,
    FactClassification,
    ResearchConfidence,
    SourceType,
)

if TYPE_CHECKING:
    from pkg.sdk.worker import WorkerRuntimeContext

logger = logging.getLogger("AutonomOS.Research.MockCrawler")


class MockCrawler(BaseCrawler):
    """
    Deterministic mock crawler implementation for testing workforce orchestration.
    Demonstrates explicit lifecycle state transitions:
    CREATED -> QUEUED -> RUNNING -> COMPLETED / FAILED / CANCELLED.
    
    Does NOT pretend to have real research data; generates bounded mock collection receipts.
    """

    def __init__(
        self,
        crawler_id: Optional[str] = None,
        name: str = "Mock Specialised Crawler",
        capabilities: Optional[list[CrawlerCapability]] = None,
        simulate_failure: bool = False,
        simulate_error_message: str = "Simulated network timeout",
        simulate_empty: bool = False,
        execution_delay: float = 0.0,
    ):
        super().__init__(
            crawler_id=crawler_id or f"crawler.mock.{uuid.uuid4().hex[:6]}",
            capabilities=capabilities or [CrawlerCapability.WEB_SEARCH, CrawlerCapability.WEB_FETCH],
            name=name,
        )
        self.simulate_failure = simulate_failure
        self.simulate_error_message = simulate_error_message
        self.simulate_empty = simulate_empty
        self.execution_delay = execution_delay
        # Transition from CREATED to QUEUED so it's ready for assignment
        self.transition_to(CrawlerStatus.QUEUED, reason="Mock crawler initialized and queued")

    def execute_crawler_task(
        self,
        task: CrawlerTask,
        context: Optional[WorkerRuntimeContext] = None,
    ) -> CrawlerReport:
        """
        Execute task through the deterministic lifecycle:
        QUEUED -> RUNNING -> COMPLETED / FAILED / CANCELLED.
        """
        start_time = time.perf_counter()
        self.current_task = task

        # 1. Transition to RUNNING
        self.transition_to(CrawlerStatus.RUNNING, reason=f"Executing task '{task.task_id}'")

        if self.execution_delay > 0:
            time.sleep(self.execution_delay)

        # 2. Check if cancelled mid-execution
        if task.status.value == "CANCELLED":
            self.transition_to(CrawlerStatus.CANCELLED, reason=task.cancellation_reason or "Task cancelled")
            elapsed = round(time.perf_counter() - start_time, 3)
            return CrawlerReport(
                report_id=f"crep-cancel-{task.task_id}",
                crawler_task_id=task.task_id,
                crawler_id=self.crawler_id,
                request_id=task.request_id,
                plan_id=task.plan_id,
                question_id=task.question_id,
                correlation_id=task.correlation_id,
                status=CrawlerReportStatus.FAILED,
                summary=f"Task cancelled: {task.cancellation_reason}",
                error_message=task.cancellation_reason or "Task cancelled by supervisor",
                execution_time_seconds=elapsed,
            )

        # 3. Simulate Failure path
        if self.simulate_failure:
            self.tasks_failed += 1
            self.transition_to(CrawlerStatus.FAILED, reason=self.simulate_error_message)
            elapsed = round(time.perf_counter() - start_time, 3)
            return CrawlerReport(
                report_id=f"crep-err-{task.task_id}",
                crawler_task_id=task.task_id,
                crawler_id=self.crawler_id,
                request_id=task.request_id,
                plan_id=task.plan_id,
                question_id=task.question_id,
                correlation_id=task.correlation_id,
                status=CrawlerReportStatus.FAILED,
                summary=f"Mock crawler execution failed: {self.simulate_error_message}",
                error_message=self.simulate_error_message,
                execution_time_seconds=elapsed,
            )

        # 4. Simulate Empty Results path
        if self.simulate_empty:
            self.tasks_completed += 1
            self.transition_to(CrawlerStatus.COMPLETED, reason="Mock execution returned zero results")
            elapsed = round(time.perf_counter() - start_time, 3)
            return CrawlerReport(
                report_id=f"crep-empty-{task.task_id}",
                crawler_task_id=task.task_id,
                crawler_id=self.crawler_id,
                request_id=task.request_id,
                plan_id=task.plan_id,
                question_id=task.question_id,
                correlation_id=task.correlation_id,
                status=CrawlerReportStatus.EMPTY,
                summary=f"No results found for query: '{task.query_or_target}'",
                execution_time_seconds=elapsed,
            )

        # 5. Golden Path: COMPLETED with mock receipts
        mock_source_url = f"mock://sources/{task.question_id}"
        mock_snippet = f"Mock collection receipt for query '{task.query_or_target}' on question '{task.question_id}'."
        checksum = hashlib.sha256(mock_snippet.encode("utf-8")).hexdigest()

        raw_source = RawSourceReference(
            url_or_ref=mock_source_url,
            title=f"Mock Source for {task.query_or_target}",
            source_type=SourceType.OFFICIAL_DOCUMENTATION,
            checksum=checksum,
            bytes_fetched=len(mock_snippet),
            content_snippet=mock_snippet,
        )

        evidence = EvidenceItem(
            evidence_id=f"ev-mock-{task.task_id}-1",
            provenance=EvidenceProvenance(
                request_id=task.request_id,
                crawler_task_id=task.task_id,
                crawler_id=self.crawler_id,
                question_id=task.question_id,
                source_ref=mock_source_url,
                correlation_id=task.correlation_id,
            ),
            extracted_fact=mock_snippet,
            content_snippet=mock_snippet,
            classification=FactClassification.FACT,
            confidence=ResearchConfidence.SUPPORTED,
            reliability_score=0.85,
            source_type=SourceType.OFFICIAL_DOCUMENTATION,
        )

        self.tasks_completed += 1
        self.transition_to(CrawlerStatus.COMPLETED, reason=f"Task '{task.task_id}' completed successfully")
        elapsed = round(time.perf_counter() - start_time, 3)

        return CrawlerReport(
            report_id=f"crep-mock-{task.task_id}",
            crawler_task_id=task.task_id,
            crawler_id=self.crawler_id,
            request_id=task.request_id,
            plan_id=task.plan_id,
            question_id=task.question_id,
            correlation_id=task.correlation_id,
            status=CrawlerReportStatus.SUCCESS,
            raw_sources=[raw_source],
            extracted_evidence=[evidence],
            summary=f"Mock collection successfully gathered 1 source for '{task.query_or_target}'",
            execution_time_seconds=elapsed,
        )
