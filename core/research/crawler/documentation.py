"""
Documentation Crawler Implementation (Phase 1 / Part 4 / Step 2).

Specialized crawler for discovering and structuring documentation repositories,
pages, sections, and navigation hierarchies. Integrates with the standard AutonomOS
Worker and Crawler runtime.
"""
from __future__ import annotations

import logging
import time
from typing import Any, Optional
import uuid

from core.models import Task, WorkerManifest, WorkerOutput, utc_now
from core.research.contracts.crawler_report import CrawlerReport, RawSourceReference
from core.research.contracts.crawler_task import CrawlerTask
from core.research.contracts.evidence import EvidenceItem, EvidenceProvenance
from core.research.crawler.base import BaseCrawler
from core.research.docs.discovery import DocumentationDiscoveryEngine
from core.research.docs.models import DocumentationSource
from core.research.docs.scoring import CandidatePage, RelevanceScorer, TopicQuery
from core.research.docs.targeted import TargetedCrawlResult, TargetedDocumentationCrawlerEngine
from core.research.errors import (
    CrawlerExecutionError,
    DocumentationError,
    DocumentationValidationError,
    FetchError,
    FetchSecurityError,
    FetchTimeoutError,
)
from core.research.fetch.config import FetchConfig
from core.research.fetch.provider import FetchProvider, MockFetchProvider
from core.research.fetch.providers.urllib_fetch import UrllibFetchProvider
from core.research.search.security import sanitize_error
from core.research.types import (
    CrawlerCapability,
    CrawlerHealthStatus,
    CrawlerReportStatus,
    CrawlerStatus,
    SourceType,
)
from pkg.sdk.types import WorkerCapability
from pkg.sdk.worker import Worker, WorkerRuntimeContext

logger = logging.getLogger("AutonomOS.Worker.DocumentationCrawler")


class DocumentationCrawler(Worker, BaseCrawler):
    """
    Production Documentation Crawler in AutonomOS.
    Executes bounded discovery and targeted crawling of documentation sources, pages, and hierarchical sections.
    """

    def __init__(
        self,
        crawler_id: Optional[str] = None,
        name: str = "DocumentationCrawler",
        provider: Optional[FetchProvider] = None,
        config: Optional[FetchConfig] = None,
        capabilities: Optional[list[CrawlerCapability]] = None,
        version: str = "1.0.0",
    ):
        cid = crawler_id or f"crawler.documentation.{uuid.uuid4().hex[:6]}"
        caps = capabilities or [
            CrawlerCapability.DOCUMENTATION_CRAWL,
            CrawlerCapability.DOCUMENT_SCRAPING,
            CrawlerCapability.WEB_FETCH,
        ]
        BaseCrawler.__init__(self, crawler_id=cid, capabilities=caps, name=name)

        self.config = config or FetchConfig.from_env()
        self.provider = provider
        self.discovery_engine = DocumentationDiscoveryEngine(
            fetch_provider=self.provider,
            config=self.config,
            allow_localhost=self.config.allow_localhost,
        )
        self.targeted_engine = TargetedDocumentationCrawlerEngine(
            fetch_provider=self.provider,
            config=self.config,
            allow_localhost=self.config.allow_localhost,
        )
        self._version = version

        self._manifest = WorkerManifest(
            id=self.crawler_id,
            name=self.name,
            role="Crawler",
            description="Specialist crawler for discovering and structuring official documentation",
            version=self._version,
            capabilities=[
                WorkerCapability.RESEARCH.value,
                WorkerCapability.WEB_ACCESS.value,
                WorkerCapability.STRUCTURED_OUTPUT.value,
                "DOCUMENTATION_CRAWL",
                "DOCUMENT_SCRAPING",
                "WEB_FETCH",
                "SOURCE_COLLECTION",
                "EVIDENCE_EXTRACTION",
            ] + [c.value for c in self._capabilities],
            permissions=["web", "web.fetch", "*"],
            created_at=utc_now(),
        )
        self.transition_to(CrawlerStatus.QUEUED, reason="DocumentationCrawler initialized and queued")

    def get_manifest(self) -> WorkerManifest:
        return self._manifest

    def execute_crawler_task(
        self,
        task: CrawlerTask,
        context: Optional[WorkerRuntimeContext] = None,
    ) -> CrawlerReport:
        """
        Execute an assigned DOCUMENTATION_CRAWL or DOCUMENT_SCRAPING CrawlerTask.
        Supports both generic structure discovery and targeted topic/keyword crawling.
        """
        start_time = time.perf_counter()
        self.current_task = task
        self.transition_to(CrawlerStatus.RUNNING, reason=f"Executing documentation task '{task.task_id}'")
        self.heartbeat()

        # Handle pre-existing task cancellation
        if task.status.value == "CANCELLED":
            self.transition_to(CrawlerStatus.CANCELLED, reason=task.cancellation_reason or "Task cancelled")
            elapsed = round(time.perf_counter() - start_time, 4)
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

        try:
            target_str = task.query_or_target.strip()
            params = task.parameters or {}

            # Parse start_url vs topic
            if target_str.startswith(("http://", "https://")):
                start_url = target_str
                topic = params.get("topic") or params.get("query") or (task.objective if task.objective != target_str else "")
            else:
                start_url = params.get("start_url") or params.get("target_url") or params.get("url") or ""
                topic = params.get("topic") or target_str

            keywords = params.get("keywords")
            target_version = params.get("version") or params.get("target_version")
            target_language = params.get("language") or params.get("target_language")
            max_depth = int(params.get("max_depth", 2))
            max_pages = int(params.get("max_pages", 5 if (topic or keywords) else 20))
            max_requests = int(params.get("max_requests", 15))
            min_score = float(params.get("min_score", 1.0))

            if not start_url:
                raise DocumentationValidationError("start_url", "No starting URL provided in task query_or_target or parameters.")

            prov = EvidenceProvenance(
                request_id=task.request_id,
                crawler_task_id=task.task_id,
                crawler_id=self.crawler_id,
                question_id=task.question_id,
                source_ref=start_url,
                correlation_id=task.correlation_id,
                captured_at=utc_now(),
            )

            is_targeted = bool(topic or keywords)

            if context and hasattr(context, "progress"):
                action_desc = f"Crawling documentation for topic '{topic}'" if is_targeted else "Discovering documentation structure"
                context.progress.report(20.0, f"{action_desc} from {start_url}")

            if is_targeted:
                # Execute Targeted Crawl
                topic_query = TopicQuery.from_input(
                    topic=topic,
                    keywords=keywords if isinstance(keywords, list) else None,
                    target_version=target_version,
                    target_language=target_language,
                    min_score=min_score,
                )
                crawl_result = self.targeted_engine.crawl_targeted(
                    start_url=start_url,
                    topic_query=topic_query,
                    max_depth=max_depth,
                    max_pages=max_pages,
                    max_requests=max_requests,
                    provenance=prov,
                )
                doc_source = crawl_result.documentation_source
                elapsed = round(time.perf_counter() - start_time, 4)

                # Check if 0 pages discovered/fetched
                if doc_source.total_pages_count == 0:
                    self.tasks_completed += 1
                    self.transition_to(CrawlerStatus.COMPLETED, reason="Targeted crawl completed with 0 relevant pages")
                    empty_meta = {
                        "documentation_source": doc_source.to_dict(),
                        "targeted_result": crawl_result.to_dict(),
                        "topic": topic_query.raw_topic,
                        "keywords": topic_query.keywords,
                        "total_pages": 0,
                        "candidates_discovered": len(crawl_result.candidates_discovered),
                        "selected_count": len(crawl_result.selected_pages),
                        "skipped_count": len(crawl_result.skipped_candidates),
                        "page_failures": crawl_result.page_failures,
                    }
                    return CrawlerReport(
                        report_id=f"crep-{uuid.uuid4().hex[:8]}",
                        crawler_task_id=task.task_id,
                        crawler_id=self.crawler_id,
                        request_id=task.request_id,
                        plan_id=task.plan_id,
                        question_id=task.question_id,
                        correlation_id=task.correlation_id,
                        status=CrawlerReportStatus.EMPTY,
                        raw_sources=[],
                        extracted_evidence=[],
                        summary=f"No relevant documentation pages found for topic '{topic_query.raw_topic}' at '{start_url}'.",
                        execution_time_seconds=elapsed,
                        metadata=empty_meta,
                    )

                raw_sources = doc_source.to_raw_source_references()
                extracted_evidence = []
                for page in doc_source.pages:
                    ev_items = page.to_evidence_items(
                        request_id=task.request_id,
                        crawler_task_id=task.task_id,
                        crawler_id=self.crawler_id,
                        question_id=task.question_id,
                        correlation_id=task.correlation_id,
                    )
                    extracted_evidence.extend(ev_items)

                report_status = CrawlerReportStatus.PARTIAL if crawl_result.is_partial_success else CrawlerReportStatus.SUCCESS
                self.tasks_completed += 1
                self.transition_to(CrawlerStatus.COMPLETED, reason=f"Fetched {doc_source.total_pages_count} targeted pages")

                report_metadata = {
                    "documentation_source": doc_source.to_dict(),
                    "topic": topic_query.raw_topic,
                    "keywords": topic_query.keywords,
                    "total_pages": doc_source.total_pages_count,
                    "candidates_discovered": len(crawl_result.candidates_discovered),
                    "selected_count": len(crawl_result.selected_pages),
                    "skipped_count": len(crawl_result.skipped_candidates),
                    "page_failures": crawl_result.page_failures,
                    "total_requests_made": crawl_result.total_requests_made,
                    "max_depth_reached": crawl_result.max_depth_reached,
                    "root_url": doc_source.root_url,
                    "canonical_url": doc_source.canonical_url,
                    "title": doc_source.title,
                    "version": doc_source.version_context.to_dict(),
                    "language": doc_source.language,
                }

                return CrawlerReport(
                    report_id=f"crep-{uuid.uuid4().hex[:8]}",
                    crawler_task_id=task.task_id,
                    crawler_id=self.crawler_id,
                    request_id=task.request_id,
                    plan_id=task.plan_id,
                    question_id=task.question_id,
                    correlation_id=task.correlation_id,
                    status=report_status,
                    raw_sources=raw_sources,
                    extracted_evidence=extracted_evidence,
                    summary=crawl_result.outcome_summary,
                    execution_time_seconds=elapsed,
                    metadata=report_metadata,
                )

            else:
                # Generic Discovery fallback
                doc_source = self.discovery_engine.discover(
                    start_url=start_url,
                    max_depth=max_depth,
                    max_pages=max_pages,
                    provenance=prov,
                )
                self.heartbeat()
                elapsed = round(time.perf_counter() - start_time, 4)

                if doc_source.total_pages_count == 0:
                    self.transition_to(CrawlerStatus.COMPLETED, reason="Discovery completed with 0 pages")
                    return CrawlerReport(
                        report_id=f"crep-{uuid.uuid4().hex[:8]}",
                        crawler_task_id=task.task_id,
                        crawler_id=self.crawler_id,
                        request_id=task.request_id,
                        plan_id=task.plan_id,
                        question_id=task.question_id,
                        correlation_id=task.correlation_id,
                        status=CrawlerReportStatus.EMPTY,
                        raw_sources=[],
                        extracted_evidence=[],
                        summary=f"Documentation discovery for '{start_url}' found 0 pages.",
                        execution_time_seconds=elapsed,
                        metadata={"documentation_source": doc_source.to_dict()},
                    )

                raw_sources = doc_source.to_raw_source_references()
                extracted_evidence = []
                for page in doc_source.pages:
                    ev_items = page.to_evidence_items(
                        request_id=task.request_id,
                        crawler_task_id=task.task_id,
                        crawler_id=self.crawler_id,
                        question_id=task.question_id,
                        correlation_id=task.correlation_id,
                    )
                    extracted_evidence.extend(ev_items)

                self.tasks_completed += 1
                self.transition_to(CrawlerStatus.COMPLETED, reason=f"Discovered {doc_source.total_pages_count} documentation pages")

                report_metadata = {
                    "documentation_source": doc_source.to_dict(),
                    "total_pages": doc_source.total_pages_count,
                    "root_url": doc_source.root_url,
                    "canonical_url": doc_source.canonical_url,
                    "title": doc_source.title,
                    "version": doc_source.version_context.to_dict(),
                    "language": doc_source.language,
                    "discovery_metadata": doc_source.discovery_metadata,
                }

                return CrawlerReport(
                    report_id=f"crep-{uuid.uuid4().hex[:8]}",
                    crawler_task_id=task.task_id,
                    crawler_id=self.crawler_id,
                    request_id=task.request_id,
                    plan_id=task.plan_id,
                    question_id=task.question_id,
                    correlation_id=task.correlation_id,
                    status=CrawlerReportStatus.SUCCESS,
                    raw_sources=raw_sources,
                    extracted_evidence=extracted_evidence,
                    summary=f"Successfully discovered {doc_source.total_pages_count} documentation pages from '{start_url}' (root: {doc_source.root_url}).",
                    execution_time_seconds=elapsed,
                    metadata=report_metadata,
                )

        except FetchTimeoutError as e:
            elapsed = round(time.perf_counter() - start_time, 4)
            sanitized_msg = sanitize_error(e)
            logger.warning(f"DocumentationCrawler '{self.crawler_id}' timed out: {sanitized_msg}")
            self.tasks_failed += 1
            self.health = CrawlerHealthStatus.DEGRADED
            self.transition_to(CrawlerStatus.FAILED, reason=sanitized_msg)
            return CrawlerReport(
                report_id=f"crep-timeout-{task.task_id}",
                crawler_task_id=task.task_id,
                crawler_id=self.crawler_id,
                request_id=task.request_id,
                plan_id=task.plan_id,
                question_id=task.question_id,
                correlation_id=task.correlation_id,
                status=CrawlerReportStatus.TIMED_OUT,
                summary=f"Documentation discovery timed out for '{task.query_or_target}'.",
                error_message=sanitized_msg,
                execution_time_seconds=elapsed,
            )

        except Exception as e:
            elapsed = round(time.perf_counter() - start_time, 4)
            sanitized_msg = sanitize_error(e)
            logger.error(f"DocumentationCrawler '{self.crawler_id}' error executing task '{task.task_id}': {sanitized_msg}")
            self.tasks_failed += 1
            self.health = CrawlerHealthStatus.DEGRADED
            self.transition_to(CrawlerStatus.FAILED, reason=sanitized_msg)
            return CrawlerReport(
                report_id=f"crep-fail-{task.task_id}",
                crawler_task_id=task.task_id,
                crawler_id=self.crawler_id,
                request_id=task.request_id,
                plan_id=task.plan_id,
                question_id=task.question_id,
                correlation_id=task.correlation_id,
                status=CrawlerReportStatus.FAILED,
                summary=f"Documentation discovery failed for '{task.query_or_target}'.",
                error_message=sanitized_msg,
                execution_time_seconds=elapsed,
            )

    def execute_task(self, *args, **kwargs) -> WorkerOutput:
        """
        Worker SDK standard execution interface.
        Supports both execute_task(context, task) and execute_task(task, context=None).
        """
        context = None
        task = None
        if len(args) == 2:
            if isinstance(args[0], Task):
                task, context = args[0], args[1]
            else:
                context, task = args[0], args[1]
        elif len(args) == 1:
            if isinstance(args[0], Task):
                task = args[0]
            else:
                context = args[0]
        if "task" in kwargs:
            task = kwargs["task"]
        if "context" in kwargs:
            context = kwargs["context"]

        if task is None:
            raise ValueError("Task is required for execute_task")

        crawler_task = CrawlerTask(
            task_id=task.id,
            request_id=task.parent_task_id or task.id,
            plan_id=f"plan-{task.id}",
            question_id="q-direct",
            query_or_target=task.objective or task.title,
            required_capability=CrawlerCapability.DOCUMENTATION_CRAWL,
            parameters=dict(task.metadata),
        )

        report = self.execute_crawler_task(crawler_task, context=context)

        return WorkerOutput(
            success=report.status in (CrawlerReportStatus.SUCCESS, CrawlerReportStatus.EMPTY, CrawlerReportStatus.PARTIAL),
            summary=report.summary,
            report_markdown=(
                f"# Documentation Discovery Report\n\n"
                f"- Crawler: `{self.crawler_id}`\n"
                f"- Target: `{task.title}`\n"
                f"- Status: `{report.status.value}`\n"
                f"- Pages Discovered: `{report.metadata.get('total_pages', 0)}`\n\n"
                f"{report.summary}"
            ),
            error_message=report.error_message,
            metadata={"crawler_report": report.to_dict()},
        )
