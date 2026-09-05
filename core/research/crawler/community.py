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
    DiscussionSelectionParams,
    DiscussionSelectionResult,
    DiscussionTopicQuery,
    SelectedDiscussionContext,
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

            min_score = float(params.get("min_score", 0.2))
            max_discussions = int(params.get("max_discussions", 5))
            max_comments = int(params.get("max_comments_per_discussion", params.get("max_comments", 50)))
            max_depth = int(params.get("max_reply_depth", params.get("max_depth", 5)))
            max_bytes = int(params.get("max_total_bytes", params.get("max_bytes", 1_000_000)))
            max_requests = int(params.get("max_requests", 20))
            timeout_seconds = float(params.get("timeout_seconds", task.timeout_seconds or 30.0))
            max_concurrency = int(params.get("max_concurrency", 4))
            include_replies = bool(params.get("include_replies", True))
            expand_subtrees = bool(params.get("expand_subtrees", False))
            subtree_root_id = params.get("subtree_root_id")

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
                target_platform=platform,
                target_community=community,
                repository_association=repository,
                tags=tags,
                after_date=str(after_date) if after_date else None,
                before_date=str(before_date) if before_date else None,
                min_score=min_score,
                include_ancestors=True,
                include_replies=include_replies,
            )

            selection_params = DiscussionSelectionParams(
                topic_query=topic_query,
                max_discussions=max_discussions,
                max_comments_per_discussion=max_comments,
                max_reply_depth=max_depth,
                max_total_bytes=max_bytes,
                max_requests=max_requests,
                timeout_seconds=timeout_seconds,
                max_concurrency=max_concurrency,
                is_cancelled=is_task_cancelled,
            )

            # Execute selection engine
            outcome: DiscussionSelectionResult = self.selection_engine.select_and_retrieve(
                params=selection_params,
                task=task,
            )

            # Dynamic Subtree Expansion (Limitation 2 mitigation)
            if outcome.selected_discussions and (expand_subtrees or subtree_root_id):
                for sel in outcome.selected_discussions:
                    try:
                        if subtree_root_id and sel.discussion.thread_structure.has_post(str(subtree_root_id)):
                            self.thread_retriever.expand_discussion_subtree(
                                discussion=sel.discussion,
                                root_comment_id=str(subtree_root_id),
                                max_comments=max_comments,
                                max_depth=max_depth,
                                timeout_seconds=timeout_seconds,
                                is_cancelled=is_task_cancelled,
                            )
                        elif expand_subtrees:
                            comments = sel.discussion.thread_structure.get_comments_only()
                            if comments:
                                top_c = max(comments, key=lambda c: (c.engagement.score or 0))
                                self.thread_retriever.expand_discussion_subtree(
                                    discussion=sel.discussion,
                                    root_comment_id=top_c.post_id,
                                    max_comments=max_comments,
                                    max_depth=max_depth,
                                    timeout_seconds=timeout_seconds,
                                    is_cancelled=is_task_cancelled,
                                )
                    except Exception as exp_err:
                        logger.warning(f"Subtree expansion failed for discussion '{sel.discussion.discussion_id}': {exp_err}")

            elapsed = round(time.perf_counter() - start_time, 4)

            # Telemetry: Discovered & Selected
            if context and hasattr(context, "progress"):
                context.progress.report(
                    60.0,
                    f"Selected {len(outcome.selected_discussions)} discussions ({outcome.total_selected_posts} posts) for '{topic}'",
                )
            if context and hasattr(context, "events"):
                try:
                    context.events.emit(
                        "community_discussions_discovered",
                        {
                            "query": topic,
                            "discussions_discovered_count": outcome.discovered_candidates_count,
                            "discussions_selected_count": len(outcome.selected_discussions),
                            "materials_count": outcome.total_selected_posts,
                        },
                    )
                except Exception:
                    pass

            # Handle cancelled outcome
            if outcome.outcome_status == CrawlerReportStatus.FAILED and "cancelled" in (outcome.outcome_summary or "").lower():
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

            # Check if empty / no results
            if outcome.outcome_status == CrawlerReportStatus.EMPTY or (len(outcome.selected_discussions) == 0 and not outcome.errors):
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
                                "total_bytes": outcome.total_bytes_retrieved,
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
                    metadata={
                        "discovered_candidates_count": outcome.discovered_candidates_count,
                        "total_discussions_inspected": outcome.total_discussions_inspected,
                        "total_posts_scored": outcome.total_posts_scored,
                        "total_selected_posts": outcome.total_selected_posts,
                        "total_bytes_retrieved": outcome.total_bytes_retrieved,
                    },
                )

            # Convert result into report
            report = outcome.to_crawler_report(task=task, crawler_id=self.crawler_id)
            report.execution_time_seconds = elapsed

            if report.status in (CrawlerReportStatus.SUCCESS, CrawlerReportStatus.PARTIAL):
                self.tasks_completed += 1
                self.transition_to(CrawlerStatus.COMPLETED, reason=f"Retrieved {outcome.total_selected_posts} discussion materials")
            elif report.status == CrawlerReportStatus.TIMED_OUT:
                self.tasks_failed += 1
                self.health = CrawlerHealthStatus.DEGRADED
                self.transition_to(CrawlerStatus.FAILED, reason=report.error_message or "Operation timed out")
            else:
                self.tasks_failed += 1
                self.health = CrawlerHealthStatus.DEGRADED
                self.transition_to(CrawlerStatus.FAILED, reason=report.error_message or "Crawl failed")

            if context and hasattr(context, "progress"):
                context.progress.report(
                    100.0,
                    f"Retrieved {outcome.total_selected_posts} discussion posts ({outcome.total_bytes_retrieved} bytes) for '{topic}'",
                )
            if context and hasattr(context, "events"):
                try:
                    context.events.emit(
                        "community_crawl_completed",
                        {
                            "query": topic,
                            "status": report.status.value,
                            "materials_count": outcome.total_selected_posts,
                            "total_bytes": outcome.total_bytes_retrieved,
                        },
                    )
                except Exception:
                    pass

            return report

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
