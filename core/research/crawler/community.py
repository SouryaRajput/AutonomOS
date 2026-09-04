"""
Community Crawler Implementation (Phase 1 / Part 6 / Step 8).

Specialized crawler for discovering, retrieving, and extracting structured evidence
from public community and discussion platforms (e.g. Reddit, GitHub Discussions,
Stack Exchange, forums). Integrates with the standard AutonomOS Worker and Crawler runtime.
"""
from __future__ import annotations

import logging
import re
import time
from typing import Any, Optional
import uuid

from core.models import Task, WorkerManifest, WorkerOutput, utc_now
from core.research.community.discovery import CommunityDiscoveryEngine
from core.research.community.fake_provider import FakeDiscussionProvider
from core.research.community.models import (
    AccessStatus,
    CommunityContext,
    CommunityPlatform,
    Discussion,
    DiscussionPost,
    DiscussionSourceMaterial,
    compute_sha256,
)
from core.research.community.provider import (
    DiscussionFetchLimits,
    DiscussionProvider,
    DiscussionRetrievalParams,
    DiscussionSearchParams,
)
from core.research.community.retriever import DiscussionThreadRetriever
from core.research.community.selection import (
    DiscussionSelectionEngine,
    DiscussionSelectionOutcome,
    DiscussionSelectionParams,
    DiscussionSelectionStatus,
    DiscussionTopicQuery,
)
from core.research.contracts.crawler_report import CrawlerReport, RawSourceReference
from core.research.contracts.crawler_task import CrawlerTask
from core.research.contracts.evidence import EvidenceItem, EvidenceProvenance
from core.research.crawler.base import BaseCrawler
from core.research.errors import (
    CommunityAuthenticationError,
    CommunityCancelledError,
    CommunityDiscussionNotFoundError,
    CommunityError,
    CommunityProviderError,
    CommunityRateLimitError,
    CommunityResourceLimitError,
    CommunitySecurityError,
    CommunityTimeoutError,
    CommunityValidationError,
    CrawlerExecutionError,
    SearchSecurityError,
)
from core.research.search.security import sanitize_error, sanitize_url, validate_network_target
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

logger = logging.getLogger("AutonomOS.Worker.CommunityCrawler")


class CommunityCrawler(Worker, BaseCrawler):
    """
    Production Community Crawler in AutonomOS.
    Executes bounded discovery, thread retrieval, and deterministic candidate selection
    across discussion platforms, returning structured CrawlerReports with full provenance.
    """

    def __init__(
        self,
        crawler_id: Optional[str] = None,
        name: str = "CommunityCrawler",
        provider: Optional[DiscussionProvider] = None,
        providers: Optional[list[DiscussionProvider]] = None,
        capabilities: Optional[list[CrawlerCapability]] = None,
        version: str = "1.0.0",
    ):
        cid = crawler_id or f"crawler.community.{uuid.uuid4().hex[:6]}"
        caps = capabilities or [
            CrawlerCapability.COMMUNITY_CRAWL,
        ]
        BaseCrawler.__init__(self, crawler_id=cid, capabilities=caps, name=name)

        # Initialize providers
        if providers:
            self.providers = list(providers)
            self.provider = self.providers[0] if self.providers else FakeDiscussionProvider()
        elif provider:
            self.provider = provider
            self.providers = [provider]
        else:
            self.provider = FakeDiscussionProvider()
            self.providers = [self.provider]

        self.discovery_engine = CommunityDiscoveryEngine(providers=self.providers)
        self.thread_retriever = DiscussionThreadRetriever(providers=self.providers)
        self.selection_engine = DiscussionSelectionEngine(
            providers=self.providers,
            discovery_engine=self.discovery_engine,
            thread_retriever=self.thread_retriever,
        )
        self._version = version

        self._manifest = WorkerManifest(
            id=self.crawler_id,
            name=self.name,
            role="Crawler",
            description="Specialist crawler for discovering, retrieving, and extracting public community and discussion threads",
            version=self._version,
            capabilities=[
                WorkerCapability.RESEARCH.value,
                WorkerCapability.STRUCTURED_OUTPUT.value,
                "COMMUNITY_CRAWL",
                "DISCUSSION_DISCOVERY",
                "THREAD_RETRIEVAL",
                "EVIDENCE_EXTRACTION",
            ] + [c.value for c in self._capabilities],
            permissions=["community", "web", "*"],
            created_at=utc_now(),
        )
        self.transition_to(CrawlerStatus.QUEUED, reason="CommunityCrawler initialized and queued")

    def get_manifest(self) -> WorkerManifest:
        return self._manifest

    def execute_crawler_task(
        self,
        task: CrawlerTask,
        context: Optional[WorkerRuntimeContext] = None,
    ) -> CrawlerReport:
        """
        Execute an assigned COMMUNITY_CRAWL CrawlerTask.
        Discovers discussions, retrieves relevant threads, extracts structured evidence,
        and constructs a validated CrawlerReport.
        """
        start_time = time.perf_counter()
        self.current_task = task
        self.transition_to(CrawlerStatus.RUNNING, reason=f"Executing community task '{task.task_id}'")
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
            target_str = (task.query_or_target or "").strip()
            params = task.parameters or {}

            # Parse query topic
            topic = str(params.get("topic") or params.get("query") or "").strip()
            if not topic:
                if target_str:
                    topic = target_str
                elif task.objective:
                    topic = task.objective
                else:
                    topic = "general discussion"

            # Parse filters and constraints
            keywords = params.get("keywords")
            if isinstance(keywords, str):
                keywords = [k.strip() for k in keywords.split(",") if k.strip()]
            elif not isinstance(keywords, list):
                keywords = None

            platform_param = params.get("platform") or params.get("target_platform")
            platform: Optional[CommunityPlatform] = None
            if platform_param:
                try:
                    platform = CommunityPlatform.from_string(str(platform_param))
                except Exception:
                    platform = None

            community = params.get("community") or params.get("community_id") or params.get("target_community")
            if community:
                community = str(community).strip()

            repository = params.get("repository") or params.get("repo")
            if repository:
                repository = str(repository).strip()

            after_date = params.get("after_date")
            before_date = params.get("before_date")
            tags = params.get("tags")
            if isinstance(tags, str):
                tags = [t.strip() for t in tags.split(",") if t.strip()]
            elif not isinstance(tags, list):
                tags = None

            min_score = float(params.get("min_score", 0.0))
            max_discussions = int(params.get("max_discussions", 5))
            max_comments = int(params.get("max_comments_per_discussion", params.get("max_comments", 50)))
            max_depth = int(params.get("max_reply_depth", params.get("max_depth", 5)))
            max_bytes = int(params.get("max_total_bytes", params.get("max_bytes", 1_000_000)))
            max_requests = int(params.get("max_requests", 20))
            timeout_seconds = float(params.get("timeout_seconds", task.timeout_seconds or 30.0))
            max_concurrency = int(params.get("max_concurrency", 4))
            include_replies = bool(params.get("include_replies", True))

            # Validate target URLs against SSRF if a direct discussion URL was passed
            if target_str.startswith(("http://", "https://")):
                try:
                    validate_network_target(target_str, allow_localhost=False)
                except SearchSecurityError as sse:
                    raise CommunitySecurityError(target_str, f"Community target security violation: {sse}") from sse

            def is_task_cancelled() -> bool:
                return self.status == CrawlerStatus.CANCELLED or task.status.value == "CANCELLED"

            # Telemetry: Start
            target_desc = f" in {community}" if community else ""
            if platform:
                target_desc += f" on {platform.value}"
            if context and hasattr(context, "progress"):
                context.progress.report(10.0, f"Searching community discussions for '{topic}'{target_desc}")
            if context and hasattr(context, "events"):
                try:
                    context.events.emit(
                        "community_crawl_started",
                        {
                            "query": topic,
                            "platform": platform.value if platform else "ALL",
                            "community": community or "ALL",
                            "repository": repository or "",
                        },
                    )
                except Exception:
                    pass

            # Build query and selection parameters
            topic_query = DiscussionTopicQuery.from_input(
                topic=topic,
                keywords=keywords,
                platform=platform,
                community=community,
                repository_association=repository,
                tags=tags,
                after_date=str(after_date) if after_date else None,
                before_date=str(before_date) if before_date else None,
                min_score=min_score,
            )

            selection_params = DiscussionSelectionParams(
                query=topic_query,
                max_discussions=max_discussions,
                max_comments_per_discussion=max_comments,
                max_reply_depth=max_depth,
                max_total_bytes=max_bytes,
                max_requests=max_requests,
                timeout_seconds=timeout_seconds,
                max_concurrency=max_concurrency,
                include_replies=include_replies,
            )

            # Execute selection engine
            outcome: DiscussionSelectionOutcome = self.selection_engine.select_discussions(
                params=selection_params,
                is_cancelled=is_task_cancelled,
            )

            elapsed = round(time.perf_counter() - start_time, 4)

            # Telemetry: Discovered & Selected
            if context and hasattr(context, "progress"):
                context.progress.report(
                    60.0,
                    f"Selected {len(outcome.discussions_selected)} discussions ({len(outcome.materials_retrieved)} materials) for '{topic}'",
                )
            if context and hasattr(context, "events"):
                try:
                    context.events.emit(
                        "community_discussions_discovered",
                        {
                            "query": topic,
                            "discussions_discovered_count": len(outcome.discussions_discovered),
                            "discussions_selected_count": len(outcome.discussions_selected),
                            "materials_count": len(outcome.materials_retrieved),
                        },
                    )
                except Exception:
                    pass

            # Handle cancelled outcome
            if outcome.status == DiscussionSelectionStatus.CANCELLED:
                self.transition_to(CrawlerStatus.CANCELLED, reason="Community selection cancelled")
                return CrawlerReport(
                    report_id=f"crep-cancel-{task.task_id}",
                    crawler_task_id=task.task_id,
                    crawler_id=self.crawler_id,
                    request_id=task.request_id,
                    plan_id=task.plan_id,
                    question_id=task.question_id,
                    correlation_id=task.correlation_id,
                    status=CrawlerReportStatus.FAILED,
                    summary="Community crawl cancelled.",
                    error_message="Operation cancelled",
                    execution_time_seconds=elapsed,
                )

            # Handle empty / no results outcome
            if outcome.status == DiscussionSelectionStatus.NO_RESULTS or len(outcome.materials_retrieved) == 0:
                self.tasks_completed += 1
                self.transition_to(CrawlerStatus.COMPLETED, reason="Community crawl completed with 0 relevant materials")
                if context and hasattr(context, "progress"):
                    context.progress.report(100.0, f"Search complete: 0 relevant discussions found for '{topic}'")
                if context and hasattr(context, "events"):
                    try:
                        context.events.emit(
                            "community_crawl_completed",
                            {
                                "query": topic,
                                "status": CrawlerReportStatus.EMPTY.value,
                                "materials_count": 0,
                                "total_bytes": outcome.total_bytes_fetched,
                            },
                        )
                    except Exception:
                        pass

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
                    summary=f"No relevant community discussions found for query '{topic}'.",
                    execution_time_seconds=elapsed,
                    metadata=outcome.to_dict(),
                )

            # Convert materials into RawSourceReferences and EvidenceItems
            raw_sources: list[RawSourceReference] = [m.to_raw_source_reference() for m in outcome.materials_retrieved]
            extracted_evidence: list[EvidenceItem] = []
            for m in outcome.materials_retrieved:
                ev_items = m.to_evidence_items(
                    request_id=task.request_id,
                    crawler_task_id=task.task_id,
                    crawler_id=self.crawler_id,
                    question_id=task.question_id,
                    correlation_id=task.correlation_id,
                )
                extracted_evidence.extend(ev_items)

            report_status = CrawlerReportStatus.PARTIAL if outcome.is_partial else CrawlerReportStatus.SUCCESS
            self.tasks_completed += 1
            self.transition_to(CrawlerStatus.COMPLETED, reason=f"Retrieved {len(outcome.materials_retrieved)} discussion materials")

            if context and hasattr(context, "progress"):
                context.progress.report(
                    100.0,
                    f"Retrieved {len(outcome.materials_retrieved)} discussion posts ({outcome.total_bytes_fetched} bytes) for '{topic}'",
                )
            if context and hasattr(context, "events"):
                try:
                    context.events.emit(
                        "community_crawl_completed",
                        {
                            "query": topic,
                            "status": report_status.value,
                            "materials_count": len(outcome.materials_retrieved),
                            "total_bytes": outcome.total_bytes_fetched,
                        },
                    )
                except Exception:
                    pass

            summary_text = (
                f"Successfully retrieved {len(outcome.materials_retrieved)} discussion posts "
                f"across {len(outcome.discussions_selected)} threads ({outcome.total_bytes_fetched} bytes) for query '{topic}'."
            )
            if outcome.is_partial and outcome.partial_reasons:
                summary_text += f" (Partial: {', '.join(outcome.partial_reasons)})"

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
                summary=summary_text,
                execution_time_seconds=elapsed,
                metadata=outcome.to_dict(),
            )

        except CommunityTimeoutError as te:
            elapsed = round(time.perf_counter() - start_time, 4)
            sanitized = sanitize_error(te)
            logger.warning(f"CommunityCrawler '{self.crawler_id}' timed out: {sanitized}")
            self.tasks_failed += 1
            self.health = CrawlerHealthStatus.DEGRADED
            self.transition_to(CrawlerStatus.FAILED, reason=f"Operation timed out: {sanitized}")
            if context and hasattr(context, "events"):
                try:
                    context.events.emit("community_crawl_timeout", {"query": task.query_or_target, "error": sanitized})
                except Exception:
                    pass
            return CrawlerReport(
                report_id=f"crep-timeout-{task.task_id}",
                crawler_task_id=task.task_id,
                crawler_id=self.crawler_id,
                request_id=task.request_id,
                plan_id=task.plan_id,
                question_id=task.question_id,
                correlation_id=task.correlation_id,
                status=CrawlerReportStatus.TIMED_OUT,
                summary=f"Community operation timed out: {sanitized}",
                error_message=sanitized,
                execution_time_seconds=elapsed,
            )

        except CommunityCancelledError as ce:
            elapsed = round(time.perf_counter() - start_time, 4)
            sanitized = sanitize_error(ce)
            logger.info(f"CommunityCrawler '{self.crawler_id}' cancelled: {sanitized}")
            self.transition_to(CrawlerStatus.CANCELLED, reason=sanitized)
            if context and hasattr(context, "events"):
                try:
                    context.events.emit("community_crawl_cancelled", {"query": task.query_or_target, "reason": sanitized})
                except Exception:
                    pass
            return CrawlerReport(
                report_id=f"crep-cancel-{task.task_id}",
                crawler_task_id=task.task_id,
                crawler_id=self.crawler_id,
                request_id=task.request_id,
                plan_id=task.plan_id,
                question_id=task.question_id,
                correlation_id=task.correlation_id,
                status=CrawlerReportStatus.FAILED,
                summary=f"Community crawl cancelled: {sanitized}",
                error_message=sanitized,
                execution_time_seconds=elapsed,
            )

        except (CommunityError, Exception) as e:
            elapsed = round(time.perf_counter() - start_time, 4)
            sanitized = sanitize_error(e)
            logger.error(f"CommunityCrawler '{self.crawler_id}' error executing task '{task.task_id}': {sanitized}")
            self.tasks_failed += 1
            self.health = CrawlerHealthStatus.DEGRADED
            self.transition_to(CrawlerStatus.FAILED, reason=f"Crawl error: {sanitized}")
            if context and hasattr(context, "events"):
                try:
                    context.events.emit("community_crawl_failed", {"query": task.query_or_target, "error": sanitized})
                except Exception:
                    pass
            return CrawlerReport(
                report_id=f"crep-err-{task.task_id}",
                crawler_task_id=task.task_id,
                crawler_id=self.crawler_id,
                request_id=task.request_id,
                plan_id=task.plan_id,
                question_id=task.question_id,
                correlation_id=task.correlation_id,
                status=CrawlerReportStatus.FAILED,
                summary=f"Community crawl failed: {sanitized}",
                error_message=sanitized,
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

        input_data = getattr(task, "input_data", None) or getattr(task, "metadata", None) or {}
        query_target = str(
            input_data.get("topic")
            or input_data.get("query")
            or input_data.get("url")
            or getattr(task, "objective", None)
            or getattr(task, "title", None)
            or ""
        )
        ctask = CrawlerTask(
            task_id=task.id,
            request_id=getattr(task, "parent_task_id", None) or task.id,
            plan_id=f"plan-{task.id}",
            question_id="q-direct",
            query_or_target=query_target,
            required_capability=CrawlerCapability.COMMUNITY_CRAWL,
            parameters=dict(input_data),
        )
        self.reset_status()
        report = self.execute_crawler_task(ctask, context=context)
        self.reset_status()
        return WorkerOutput(
            success=report.status in (CrawlerReportStatus.SUCCESS, CrawlerReportStatus.EMPTY, CrawlerReportStatus.PARTIAL),
            summary=report.summary,
            report_markdown=(
                f"# Community Discussion Inspection Report\n\n"
                f"- Crawler: `{self.crawler_id}`\n"
                f"- Target: `{task.title or query_target}`\n"
                f"- Status: `{report.status.value}`\n"
                f"- Sources Retrieved: `{len(report.raw_sources)}`\n"
                f"- Evidence Items: `{len(report.extracted_evidence)}`\n\n"
                f"{report.summary}"
            ),
            error_message=report.error_message,
            metadata={
                "crawler_report": report.to_dict(),
                "worker_id": self.crawler_id,
                "task_id": task.id,
                "execution_time_seconds": report.execution_time_seconds,
                "result_data": report.to_dict(),
            },
        )
