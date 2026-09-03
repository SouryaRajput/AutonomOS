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
    SearchError,
    SearchParameterValidationError,
    SearchSecurityError,
    SearchTimeoutError,
)
from core.research.search.config import SearchConfig
from core.research.search.models import SearchParameters
from core.research.search.normalization import (
    deduplicate_search_results,
    extract_domain,
    normalize_url,
)
from core.research.search.provider import MockSearchProvider, SearchProvider
from core.research.search.security import (
    compute_effective_timeout,
    sanitize_error,
    validate_network_target,
)
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

logger = logging.getLogger("AutonomOS.Worker.WebSearchCrawler")


class WebSearchCrawler(Worker, BaseCrawler):
    """
    Production Web Search Crawler in AutonomOS.
    Specialized in executing bounded web search collection tasks via pluggable SearchProviders.
    
    Guarantees:
    1. Zero provider-specific branching in crawler logic.
    2. Treats search result text strictly as untrusted external data.
    3. Preserves comprehensive lineage and checksum provenance.
    4. Deterministically enforces limits, domain filtering, and deduplication.
    5. Gracefully handles empty, partial, or malformed provider responses.
    """

    def __init__(
        self,
        crawler_id: Optional[str] = None,
        name: str = "WebSearchCrawler",
        provider: Optional[SearchProvider] = None,
        config: Optional[SearchConfig] = None,
        capabilities: Optional[list[CrawlerCapability]] = None,
        version: str = "1.0.0",
    ):
        cid = crawler_id or f"crawler.web_search.{uuid.uuid4().hex[:6]}"
        caps = capabilities or [CrawlerCapability.WEB_SEARCH, CrawlerCapability.WEB_FETCH]
        BaseCrawler.__init__(self, crawler_id=cid, capabilities=caps, name=name)

        self.config = config or SearchConfig.from_env()
        self.provider = provider or MockSearchProvider(provider_id=self.config.provider_type)
        self._version = version

        self._manifest = WorkerManifest(
            id=self.crawler_id,
            name=self.name,
            role="Crawler",
            description="Specialist crawler executing bounded web search tasks",
            version=self._version,
            capabilities=[
                WorkerCapability.RESEARCH.value,
                WorkerCapability.WEB_ACCESS.value,
                WorkerCapability.STRUCTURED_OUTPUT.value,
                "WEB_SEARCH",
                "SOURCE_COLLECTION",
                "EVIDENCE_EXTRACTION",
            ] + [c.value for c in self._capabilities],
            permissions=["web", "web.search", "web.fetch", "*"],
            created_at=utc_now(),
        )
        self.transition_to(CrawlerStatus.QUEUED, reason="WebSearchCrawler initialized and queued")

    def get_manifest(self) -> WorkerManifest:
        return self._manifest

    def execute_crawler_task(
        self,
        task: CrawlerTask,
        context: Optional[WorkerRuntimeContext] = None,
    ) -> CrawlerReport:
        """
        Execute an assigned CrawlerTask requiring WEB_SEARCH.
        Executes query via SearchProvider, normalizes results, applies guards,
        and constructs a fully traceable CrawlerReport.
        """
        start_time = time.perf_counter()
        self.current_task = task

        # Check early cancellation
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

        self.transition_to(CrawlerStatus.RUNNING, reason=f"Executing search task '{task.task_id}'")
        self.heartbeat()
        logger.info(f"WebSearchCrawler '{self.crawler_id}' executing search task '{task.task_id}' for query: '{task.query_or_target}'")

        raw_sources: list[RawSourceReference] = []
        extracted_evidence: list[EvidenceItem] = []
        summary = ""
        report_status = CrawlerReportStatus.SUCCESS
        error_msg: Optional[str] = None

        try:
            # 1. Validate task capability compatibility
            self.validate_task_compatibility(task)

            # 2. Extract and validate structured SearchParameters
            search_params = SearchParameters.from_crawler_task(task)

            # Check for contradictory domain rules
            conflicting_domains = set(search_params.domain_allowlist).intersection(set(search_params.domain_blocklist))
            if conflicting_domains:
                raise SearchParameterValidationError(
                    "domain_filter",
                    f"Conflicting domain rules for: {list(conflicting_domains)}. A domain cannot be both allowed and blocked.",
                )

            # Validate domain targets against network security boundary
            for d in search_params.domain_allowlist:
                validate_network_target(d, allow_localhost=self.config.allow_localhost)

            # 3. Calculate effective network timeout
            elapsed_pre = time.perf_counter() - start_time
            effective_timeout = compute_effective_timeout(
                task_timeout=float(task.timeout_seconds) if task.timeout_seconds else None,
                config_timeout=self.config.timeout_seconds,
                elapsed_seconds=elapsed_pre,
            )

            # Check mid-execution cancellation
            if task.status.value == "CANCELLED":
                raise CrawlerExecutionError(self.crawler_id, task.task_id, "Task was cancelled prior to search execution.")

            # 4. Execute search through SearchProvider
            response = self.provider.search(search_params)

            # 5. Result Validation & Defensive Normalization
            search_items = response.results if response and isinstance(response.results, list) else []

            # Defensive local domain filtering & deduplication
            filtered_items = []
            for item in search_items:
                if not item or not item.url:
                    continue
                domain = item.domain or extract_domain(item.url)
                if search_params.domain_allowlist:
                    if not any(domain == d or domain.endswith("." + d) for d in search_params.domain_allowlist):
                        continue
                if search_params.domain_blocklist:
                    if any(domain == d or domain.endswith("." + d) for d in search_params.domain_blocklist):
                        continue
                filtered_items.append(item)

            deduped_items = deduplicate_search_results(filtered_items)
            final_items = deduped_items[: search_params.limit]

            # 6. Transform into RawSourceReference & EvidenceItem collections
            for idx, item in enumerate(final_items):
                snippet_text = str(item.snippet or "").strip()
                title_text = str(item.title or f"Result {idx+1}").strip()
                target_url = item.normalized_url or item.url
                domain_name = item.domain or extract_domain(target_url)

                # Compute deterministic checksum of content snippet
                checksum = hashlib.sha256(snippet_text.encode("utf-8")).hexdigest()

                source_type = (
                    SourceType.OFFICIAL_DOCUMENTATION
                    if ("doc" in target_url.lower() or "docs" in domain_name.lower() or "spec" in target_url.lower())
                    else SourceType.COMMUNITY if ("forum" in domain_name.lower() or "reddit" in domain_name.lower() or "community" in domain_name.lower())
                    else SourceType.OTHER
                )

                raw_sources.append(
                    RawSourceReference(
                        url_or_ref=target_url,
                        title=title_text,
                        publisher=domain_name,
                        source_type=source_type,
                        checksum=checksum,
                        bytes_fetched=len(snippet_text),
                        content_snippet=snippet_text[:1000],
                        metadata={
                            "rank": item.rank,
                            "published_date": item.published_date,
                            "original_url": item.original_url,
                            "provider": self.provider.provider_id,
                            "provider_result_id": item.provider_result_id,
                            **item.metadata,
                        },
                    )
                )

                extracted_evidence.append(
                    EvidenceItem(
                        evidence_id=f"ev-{task.task_id}-{idx+1}",
                        provenance=EvidenceProvenance(
                            request_id=task.request_id,
                            crawler_task_id=task.task_id,
                            crawler_id=self.crawler_id,
                            question_id=task.question_id,
                            source_ref=target_url,
                            correlation_id=task.correlation_id,
                        ),
                        extracted_fact=snippet_text or f"Search result item from {domain_name} for query '{task.query_or_target}'",
                        content_snippet=snippet_text,
                        classification=FactClassification.FACT if source_type == SourceType.OFFICIAL_DOCUMENTATION else FactClassification.SOURCE_CLAIM,
                        confidence=ResearchConfidence.SUPPORTED,
                        reliability_score=0.85 if source_type == SourceType.OFFICIAL_DOCUMENTATION else 0.70,
                        source_type=source_type,
                        metadata={"rank": item.rank, "published_date": item.published_date},
                    )
                )

            # 7. Outcome Determination: Empty vs Success
            if not final_items:
                report_status = CrawlerReportStatus.EMPTY
                summary = f"Search returned 0 results for query '{task.query_or_target}'"
                logger.info(f"Search completed with zero results for query '{task.query_or_target}'")
            else:
                report_status = CrawlerReportStatus.SUCCESS
                distinct_domains = len({s.publisher for s in raw_sources})
                summary = f"Gathered {len(raw_sources)} search results from {distinct_domains} domains for query '{task.query_or_target}'"

            self.tasks_completed += 1
            self.transition_to(CrawlerStatus.COMPLETED, reason=summary)

        except SearchTimeoutError as timeout_err:
            logger.warning(f"WebSearchCrawler '{self.crawler_id}' query timed out: {timeout_err}")
            report_status = CrawlerReportStatus.TIMED_OUT
            error_msg = sanitize_error(timeout_err)
            summary = f"Search timed out: {error_msg}"
            self.tasks_failed += 1
            self.health = CrawlerHealthStatus.DEGRADED
            self.transition_to(CrawlerStatus.FAILED, reason=error_msg)

        except (SearchError, SearchSecurityError, Exception) as err:
            logger.error(f"WebSearchCrawler '{self.crawler_id}' error executing task '{task.task_id}': {err}")
            report_status = CrawlerReportStatus.FAILED
            error_msg = sanitize_error(err)
            summary = f"Search execution failed: {error_msg}"
            self.tasks_failed += 1
            self.health = CrawlerHealthStatus.DEGRADED
            self.transition_to(CrawlerStatus.FAILED, reason=error_msg)

        elapsed = round(time.perf_counter() - start_time, 4)
        return CrawlerReport(
            report_id=f"crep-{task.task_id}",
            crawler_task_id=task.task_id,
            crawler_id=self.crawler_id,
            request_id=task.request_id,
            plan_id=task.plan_id,
            question_id=task.question_id,
            correlation_id=task.correlation_id,
            status=report_status,
            raw_sources=raw_sources,
            extracted_evidence=extracted_evidence,
            summary=summary,
            error_message=error_msg,
            execution_time_seconds=elapsed,
            metadata={
                "provider": self.provider.provider_id,
                "query": task.query_or_target,
                "results_count": len(raw_sources),
            },
        )

    def execute_task(self, context: WorkerRuntimeContext, task: Task) -> WorkerOutput:
        """
        Implement standard Worker SDK interface for direct runtime task execution.
        """
        crawler_task = CrawlerTask(
            task_id=task.id,
            request_id=task.parent_task_id or task.id,
            plan_id=f"plan-{task.id}",
            question_id="q-direct",
            query_or_target=task.objective or task.title,
            required_capability=CrawlerCapability.WEB_SEARCH,
        )
        report = self.execute_crawler_task(crawler_task, context=context)

        return WorkerOutput(
            success=report.status in (CrawlerReportStatus.SUCCESS, CrawlerReportStatus.EMPTY, CrawlerReportStatus.PARTIAL),
            summary=report.summary,
            report_markdown=f"# Web Search Report\n\n- Crawler: `{self.crawler_id}`\n- Task: `{task.title}`\n- Status: `{report.status.value}`\n- Sources: {len(report.raw_sources)}\n\n{report.summary}",
            error_message=report.error_message,
            metadata={"crawler_report": report.to_dict()},
        )
