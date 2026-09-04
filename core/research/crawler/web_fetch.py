"""
Web Fetch Crawler Implementation (Phase 1 / Part 3).

Specialized crawler for retrieving and inspecting specific web pages and documents via HTTP/HTTPS.
Integrates with the standard AutonomOS Worker and Crawler runtime.
"""
from __future__ import annotations

import hashlib
import logging
import time
from typing import TYPE_CHECKING, Any, Optional
import uuid

from core.models import Task, WorkerManifest, WorkerOutput, utc_now
from core.research.contracts.crawler_report import CrawlerReport, RawSourceReference
from core.research.contracts.crawler_task import CrawlerTask
from core.research.contracts.evidence import EvidenceItem, EvidenceProvenance
from core.research.crawler.base import BaseCrawler
from core.research.errors import (
    CrawlerExecutionError,
    FetchError,
    FetchHttpError,
    FetchParameterValidationError,
    FetchSecurityError,
    FetchTimeoutError,
)
from core.research.fetch.config import FetchConfig
from core.research.fetch.extraction import HtmlExtractor
from core.research.fetch.models import FetchParameters, FetchResponse
from core.research.fetch.provider import FetchProvider, MockFetchProvider
from core.research.fetch.providers.urllib_fetch import UrllibFetchProvider
from core.research.search.normalization import extract_domain
from core.research.search.security import sanitize_error, validate_network_target
from core.research.types import (
    CrawlerCapability,
    CrawlerHealthStatus,
    CrawlerReportStatus,
    CrawlerStatus,
    FactClassification,
    ResearchConfidence,
    SourceType,
)
from pkg.sdk.types import WorkerCapability
from pkg.sdk.worker import Worker, WorkerRuntimeContext

logger = logging.getLogger("AutonomOS.Worker.WebFetchCrawler")


class WebFetchCrawler(Worker, BaseCrawler):
    """
    Production Web Fetch Crawler in AutonomOS.
    Retrieves specific target web pages/documents and returns lineage-preserving CrawlerReports.
    """

    def __init__(
        self,
        crawler_id: Optional[str] = None,
        name: str = "WebFetchCrawler",
        provider: Optional[FetchProvider] = None,
        config: Optional[FetchConfig] = None,
        capabilities: Optional[list[CrawlerCapability]] = None,
        version: str = "1.0.0",
    ):
        cid = crawler_id or f"crawler.web_fetch.{uuid.uuid4().hex[:6]}"
        caps = capabilities or [CrawlerCapability.WEB_FETCH, CrawlerCapability.DOCUMENT_SCRAPING]
        BaseCrawler.__init__(self, crawler_id=cid, capabilities=caps, name=name)

        self.config = config or FetchConfig.from_env()
        if provider:
            self.provider = provider
        elif self.config.provider_type == "urllib":
            self.provider = UrllibFetchProvider(config=self.config)
        else:
            self.provider = MockFetchProvider(provider_id=self.config.provider_type, config=self.config)
        self._version = version

        self._manifest = WorkerManifest(
            id=self.crawler_id,
            name=self.name,
            role="Crawler",
            description="Specialist crawler executing bounded HTTP web fetch tasks",
            version=self._version,
            capabilities=[
                WorkerCapability.RESEARCH.value,
                WorkerCapability.WEB_ACCESS.value,
                WorkerCapability.STRUCTURED_OUTPUT.value,
                "WEB_FETCH",
                "DOCUMENT_SCRAPING",
                "SOURCE_COLLECTION",
                "EVIDENCE_EXTRACTION",
            ] + [c.value for c in self._capabilities],
            permissions=["web", "web.fetch", "*"],
            created_at=utc_now(),
        )
        self.transition_to(CrawlerStatus.QUEUED, reason="WebFetchCrawler initialized and queued")

    def get_manifest(self) -> WorkerManifest:
        return self._manifest

    def execute_crawler_task(
        self,
        task: CrawlerTask,
        context: Optional[WorkerRuntimeContext] = None,
    ) -> CrawlerReport:
        """
        Execute an assigned WEB_FETCH CrawlerTask.
        """
        start_time = time.perf_counter()
        self.current_task = task
        self.transition_to(CrawlerStatus.RUNNING, reason=f"Executing fetch task '{task.task_id}'")
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
            # 1. Parse and validate FetchParameters from task
            fetch_params = FetchParameters.from_crawler_task(task)

            # 2. SSRF validation check
            try:
                validate_network_target(fetch_params.url, allow_localhost=self.config.allow_localhost)
            except Exception as sec_err:
                raise FetchSecurityError(fetch_params.url, str(sec_err)) from sec_err

            if context and hasattr(context, "progress"):
                context.progress.report(30.0, f"Fetching {fetch_params.url}")

            # 3. Execute HTTP fetch through provider
            response = self.provider.fetch(fetch_params)
            self.heartbeat()

            elapsed = round(time.perf_counter() - start_time, 4)

            # 4. Handle empty/zero-byte response
            if not response.body_bytes or len(response.body_bytes) == 0:
                self.transition_to(CrawlerStatus.COMPLETED, reason="Fetch returned 0 bytes")
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
                    summary=f"Web fetch for '{response.url}' returned 0 bytes (HTTP {response.status_code}).",
                    execution_time_seconds=elapsed,
                    metadata={"status_code": response.status_code, "headers": response.headers, "url": response.url},
                )

            # 5. Extract structured text and metadata from response body
            extracted = HtmlExtractor.extract(
                body_text=response.body_text,
                base_url=response.url,
                content_type=response.content_type,
            )

            # 6. Compute SHA-256 content hash
            content_checksum = hashlib.sha256(response.body_bytes).hexdigest()
            domain = extract_domain(response.url)
            publisher_name = extracted.opengraph.get("og:site_name") or domain
            doc_title = extracted.title or f"Document from {domain}"

            # 7. Build RawSourceReference
            source_metadata = {
                "status_code": response.status_code,
                "content_type": response.content_type,
                "is_html": extracted.is_html,
                "headers": response.headers,
                "original_url": response.original_url,
                "url": response.url,
                "canonical_url": extracted.canonical_url,
                "title": extracted.title,
                "description": extracted.description,
                "author": extracted.author,
                "language": extracted.language,
                "published_date": extracted.published_date,
                "modified_date": extracted.modified_date,
                "opengraph": extracted.opengraph,
                "links": extracted.links,
                "links_count": len(extracted.links),
                "headings": extracted.headings,
                "redirect_chain": response.redirect_chain,
                "provider": response.provider_id,
            }

            raw_source = RawSourceReference(
                url_or_ref=response.url,
                title=doc_title,
                publisher=publisher_name,
                source_type=SourceType.PRIMARY_SOURCE,
                checksum=content_checksum,
                bytes_fetched=response.bytes_fetched,
                content_snippet=extracted.clean_text[:2000] if extracted.clean_text else response.body_text[:1000],
                fetched_at=response.retrieved_at,
                metadata=source_metadata,
            )

            # 8. Build EvidenceItem
            evidence_snippet = extracted.clean_text[:4000] if extracted.clean_text else response.body_text[:2000]
            evidence_fact = (
                extracted.title
                or (extracted.clean_text[:300].strip() if extracted.clean_text else f"Fetched content from {domain}")
            )

            evidence_item = EvidenceItem(
                evidence_id=f"ev-{task.task_id}-1",
                provenance=EvidenceProvenance(
                    request_id=task.request_id,
                    crawler_task_id=task.task_id,
                    crawler_id=self.crawler_id,
                    question_id=task.question_id,
                    source_ref=extracted.canonical_url or raw_source.url_or_ref,
                    correlation_id=task.correlation_id,
                    captured_at=response.retrieved_at,
                ),
                extracted_fact=evidence_fact,
                content_snippet=evidence_snippet,
                classification=FactClassification.SOURCE_CLAIM,
                confidence=ResearchConfidence.SUPPORTED,
                reliability_score=0.85,
                source_type=raw_source.source_type,
                metadata={
                    "status_code": response.status_code,
                    "content_type": response.content_type,
                    "is_html": extracted.is_html,
                    "canonical_url": extracted.canonical_url,
                    "original_url": response.original_url,
                    "url": response.url,
                    "title": extracted.title,
                },
            )

            self.tasks_completed += 1
            self.transition_to(CrawlerStatus.COMPLETED, reason=f"Fetched {response.bytes_fetched} bytes successfully")
            if context and hasattr(context, "progress"):
                context.progress.report(100.0, f"Fetched {response.bytes_fetched} bytes from {response.url}")

            report_metadata = {
                "status_code": response.status_code,
                "content_type": response.content_type,
                "is_html": extracted.is_html,
                "provider": response.provider_id,
                "url": response.url,
                "original_url": response.original_url,
                "canonical_url": extracted.canonical_url,
                "title": extracted.title,
                "description": extracted.description,
                "author": extracted.author,
                "language": extracted.language,
                "published_date": extracted.published_date,
                "modified_date": extracted.modified_date,
                "opengraph": extracted.opengraph,
                "links_count": len(extracted.links),
                "links": extracted.links[:50],
                "headings": extracted.headings,
                "redirect_chain": response.redirect_chain,
                "checksum": content_checksum,
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
                raw_sources=[raw_source],
                extracted_evidence=[evidence_item],
                summary=f"Successfully fetched and parsed {response.bytes_fetched} bytes from {response.url} (HTTP {response.status_code}).",
                execution_time_seconds=elapsed,
                metadata=report_metadata,
            )

        except FetchTimeoutError as e:
            elapsed = round(time.perf_counter() - start_time, 4)
            sanitized_msg = sanitize_error(e)
            logger.warning(f"WebFetchCrawler '{self.crawler_id}' timed out: {sanitized_msg}")
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
                summary=f"Fetch timed out for '{task.query_or_target}'.",
                error_message=sanitized_msg,
                execution_time_seconds=elapsed,
            )

        except Exception as e:
            elapsed = round(time.perf_counter() - start_time, 4)
            sanitized_msg = sanitize_error(e)
            logger.error(f"WebFetchCrawler '{self.crawler_id}' error executing task '{task.task_id}': {sanitized_msg}")
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
                summary=f"Fetch failed for '{task.query_or_target}'.",
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
            required_capability=CrawlerCapability.WEB_FETCH,
            parameters=dict(task.metadata),
        )

        report = self.execute_crawler_task(crawler_task, context=context)

        return WorkerOutput(
            success=report.status in (CrawlerReportStatus.SUCCESS, CrawlerReportStatus.EMPTY, CrawlerReportStatus.PARTIAL),
            summary=report.summary,
            report_markdown=(
                f"# Web Fetch Report\n\n"
                f"- Crawler: `{self.crawler_id}`\n"
                f"- Task: `{task.title}`\n"
                f"- Status: `{report.status.value}`\n"
                f"- URL: `{report.metadata.get('url', crawler_task.query_or_target)}`\n"
                f"- Status Code: `{report.metadata.get('status_code', 200)}`\n\n"
                f"{report.summary}"
            ),
            error_message=report.error_message,
            metadata={"crawler_report": report.to_dict()},
        )
