"""
Repository Crawler Implementation (Phase 1 / Part 5 / Step 4).

Specialized crawler for discovering, inspecting, ranking, and retrieving source material
from code repositories. Integrates with the standard AutonomOS Worker and Crawler runtime.
"""
from __future__ import annotations

import logging
import re
import time
from typing import Any, Optional
import uuid

from core.models import Task, WorkerManifest, WorkerOutput, utc_now
from core.research.contracts.crawler_report import CrawlerReport, RawSourceReference
from core.research.contracts.crawler_task import CrawlerTask
from core.research.contracts.evidence import EvidenceItem, EvidenceProvenance
from core.research.crawler.base import BaseCrawler
from core.research.errors import (
    CrawlerExecutionError,
    RepositoryCancelledError,
    RepositoryError,
    RepositoryFileNotFoundError,
    RepositoryNotFoundError,
    RepositoryProviderError,
    RepositoryResourceLimitError,
    RepositoryRevisionNotFoundError,
    RepositorySecurityError,
    RepositoryTimeoutError,
    RepositoryValidationError,
)
from core.research.repo.discovery import DiscoveredRepository, RepositoryDiscoveryEngine, RepositoryDiscoveryOptions
from core.research.repo.mock_provider import MockRepositoryProvider
from core.research.repo.models import (
    RepositoryIdentity,
    RepositoryProviderType,
    RepositoryRevision,
    RepositorySource,
    RepositorySourceMaterial,
)
from core.research.repo.provider import RepositoryFetchLimits, RepositoryProvider
from core.research.repo.targeted import RepoTopicQuery, TargetedRepoCrawlResult, TargetedRepositoryEngine
from core.research.search.security import sanitize_error
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

logger = logging.getLogger("AutonomOS.Worker.RepositoryCrawler")


class RepositoryCrawler(Worker, BaseCrawler):
    """
    Production Repository Crawler in AutonomOS.
    Executes bounded discovery and targeted inspection of code repositories,
    ranking candidate files and retrieving source materials with cryptographic provenance.
    """

    def __init__(
        self,
        crawler_id: Optional[str] = None,
        name: str = "RepositoryCrawler",
        provider: Optional[RepositoryProvider] = None,
        capabilities: Optional[list[CrawlerCapability]] = None,
        version: str = "1.0.0",
    ):
        cid = crawler_id or f"crawler.repository.{uuid.uuid4().hex[:6]}"
        caps = capabilities or [
            CrawlerCapability.REPOSITORY_INSPECTION,
        ]
        BaseCrawler.__init__(self, crawler_id=cid, capabilities=caps, name=name)

        self.provider = provider or MockRepositoryProvider()
        self.discovery_engine = RepositoryDiscoveryEngine(provider=self.provider)
        self.targeted_engine = TargetedRepositoryEngine(
            provider=self.provider,
            discovery_engine=self.discovery_engine,
        )
        self._version = version

        self._manifest = WorkerManifest(
            id=self.crawler_id,
            name=self.name,
            role="Crawler",
            description="Specialist crawler for discovering and inspecting code repositories",
            version=self._version,
            capabilities=[
                WorkerCapability.RESEARCH.value,
                WorkerCapability.STRUCTURED_OUTPUT.value,
                "REPOSITORY_INSPECTION",
                "CODE_COLLECTION",
                "EVIDENCE_EXTRACTION",
            ] + [c.value for c in self._capabilities],
            permissions=["repo", "local", "*"],
            created_at=utc_now(),
        )
        self.transition_to(CrawlerStatus.QUEUED, reason="RepositoryCrawler initialized and queued")

    def get_manifest(self) -> WorkerManifest:
        return self._manifest

    def execute_crawler_task(
        self,
        task: CrawlerTask,
        context: Optional[WorkerRuntimeContext] = None,
    ) -> CrawlerReport:
        """
        Execute an assigned REPOSITORY_INSPECTION CrawlerTask.
        Supports both generic structure discovery and targeted keyword/topic inspection.
        """
        start_time = time.perf_counter()
        self.current_task = task
        self.transition_to(CrawlerStatus.RUNNING, reason=f"Executing repository task '{task.task_id}'")
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

            # Parse target repository vs topic/query
            repo_target = params.get("repo") or params.get("repository") or params.get("url") or ""
            topic = params.get("topic") or params.get("query") or ""

            if not repo_target:
                url_match = re.search(r"(https?://[^\s]+|git@[^\s]+|file://[^\s]+)", target_str)
                if url_match:
                    repo_target = url_match.group(1).rstrip(".,;?'\"")
                    if not topic:
                        topic = target_str.replace(url_match.group(0), "").strip()
                else:
                    repo_id_match = re.search(r"\b(repo-[a-zA-Z0-9_\-\.]+)\b", target_str)
                    if repo_id_match:
                        repo_target = repo_id_match.group(1)
                        if not topic:
                            topic = target_str
                    else:
                        repo_target = target_str

            if not topic:
                if task.objective and task.objective != repo_target:
                    topic = task.objective
                elif target_str != repo_target:
                    topic = target_str

            keywords = params.get("keywords")
            target_path = params.get("target_path") or params.get("path")
            target_language = params.get("language") or params.get("target_language")
            target_categories = params.get("categories") or params.get("target_categories")
            revision = params.get("revision") or params.get("branch") or params.get("tag")

            max_depth = int(params.get("max_depth", 10))
            max_files = int(params.get("max_files", 10))
            max_bytes = int(params.get("max_bytes", 1_000_000))
            max_file_size = int(params.get("max_file_size", 250_000))
            max_requests = int(params.get("max_requests", 20))
            extract_structure = bool(params.get("extract_structure", True))
            max_parse_bytes = int(params.get("max_parse_bytes", 500_000))
            min_score = float(params.get("min_score", 1.0))
            timeout_seconds = float(params.get("timeout_seconds", 30.0))

            if not repo_target:
                raise RepositoryValidationError("repo_target", "No repository target or URL provided in task.")

            # Validate HTTP/HTTPS targets against SSRF
            if repo_target.startswith(("http://", "https://")):
                from core.research.search.security import SearchSecurityError, validate_network_target
                try:
                    validate_network_target(repo_target, allow_localhost=False)
                except SearchSecurityError as sse:
                    raise RepositorySecurityError(repo_target, f"Repository target security violation: {sse}") from sse

            is_targeted = bool(topic or keywords or target_path)

            def is_task_cancelled() -> bool:
                return self.status == CrawlerStatus.CANCELLED or task.status.value == "CANCELLED"

            if context and hasattr(context, "progress"):
                action_desc = f"Crawling repository for topic '{topic}'" if is_targeted else "Discovering repository structure"
                context.progress.report(10.0, f"{action_desc} in '{repo_target}' (revision: {revision or 'default'})")
            if context and hasattr(context, "events"):
                try:
                    context.events.emit(
                        "repository_crawl_started",
                        {
                            "repository": repo_target,
                            "revision": revision or "default",
                            "is_targeted": is_targeted,
                            "topic": topic,
                        },
                    )
                except Exception:
                    pass

            if is_targeted:
                # 1. Execute Targeted Repository Crawl
                topic_query = RepoTopicQuery.from_input(
                    topic=topic,
                    keywords=keywords if isinstance(keywords, list) else None,
                    target_path=target_path,
                    target_language=target_language,
                    target_categories=target_categories if isinstance(target_categories, list) else None,
                    min_score=min_score,
                )

                if context and hasattr(context, "progress"):
                    context.progress.report(30.0, f"Evaluating candidate file relevance for '{topic_query.raw_topic}'")

                crawl_result = self.targeted_engine.crawl_targeted(
                    target=repo_target,
                    query=topic_query,
                    revision=revision,
                    max_files=max_files,
                    max_bytes=max_bytes,
                    max_file_size=max_file_size,
                    max_depth=max_depth,
                    max_requests=max_requests,
                    extract_structure=extract_structure,
                    max_parse_bytes=max_parse_bytes,
                    timeout_seconds=timeout_seconds,
                    is_cancelled=is_task_cancelled,
                )

                repo_source = crawl_result.repository_source
                elapsed = round(time.perf_counter() - start_time, 4)

                if context and hasattr(context, "events"):
                    try:
                        context.events.emit(
                            "repository_candidates_selected",
                            {
                                "repository": repo_target,
                                "candidates_count": len(crawl_result.candidates_discovered),
                                "selected_count": len(crawl_result.selected_files),
                            },
                        )
                    except Exception:
                        pass

                # If 0 candidate files found above min_score
                if len(crawl_result.candidates_discovered) == 0 or len(crawl_result.materials_retrieved) == 0:
                    self.tasks_completed += 1
                    self.transition_to(CrawlerStatus.COMPLETED, reason="Targeted repository crawl completed with 0 relevant files")
                    if context and hasattr(context, "progress"):
                        context.progress.report(100.0, f"Targeted inspection complete: 0 relevant files found in '{repo_target}'")
                    empty_meta = {
                        "repository_source": repo_source.to_dict(),
                        "topic": topic_query.raw_topic,
                        "keywords": topic_query.keywords,
                        "candidates_discovered": len(crawl_result.candidates_discovered),
                        "selected_count": len(crawl_result.selected_files),
                        "materials_count": 0,
                        "file_failures": crawl_result.file_failures,
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
                        summary=f"No relevant repository files found for query '{topic_query.raw_topic}' in '{repo_target}'.",
                        execution_time_seconds=elapsed,
                        metadata=empty_meta,
                    )

                # Convert materials to RawSourceReferences and EvidenceItems
                raw_sources = repo_source.to_raw_source_references()
                extracted_evidence = repo_source.to_evidence_items(
                    request_id=task.request_id,
                    crawler_task_id=task.task_id,
                    crawler_id=self.crawler_id,
                    question_id=task.question_id,
                    correlation_id=task.correlation_id,
                )

                report_status = CrawlerReportStatus.PARTIAL if crawl_result.is_partial_success else CrawlerReportStatus.SUCCESS
                self.tasks_completed += 1
                self.transition_to(CrawlerStatus.COMPLETED, reason=f"Fetched {len(crawl_result.materials_retrieved)} repository materials")

                if context and hasattr(context, "progress"):
                    context.progress.report(100.0, f"Retrieved {len(crawl_result.materials_retrieved)} files ({crawl_result.total_bytes_fetched} bytes) from '{repo_target}'")
                if context and hasattr(context, "events"):
                    try:
                        context.events.emit(
                            "repository_crawl_completed",
                            {
                                "repository": repo_target,
                                "status": report_status.value,
                                "materials_count": len(crawl_result.materials_retrieved),
                                "total_bytes": crawl_result.total_bytes_fetched,
                            },
                        )
                    except Exception:
                        pass

                report_metadata = {
                    "repository_source": repo_source.to_dict(),
                    "revision": repo_source.revision.to_dict() if repo_source.revision else None,
                    "topic": topic_query.raw_topic,
                    "candidates_count": len(crawl_result.candidates_discovered),
                    "selected_count": len(crawl_result.selected_files),
                    "materials_count": len(crawl_result.materials_retrieved),
                    "structured_files_count": crawl_result.metadata.get("structured_files_count", 0),
                    "total_bytes_fetched": crawl_result.total_bytes_fetched,
                    "file_failures": crawl_result.file_failures,
                }

                rev_tag = f" @ {repo_source.revision.commit_sha[:8]}" if repo_source.revision and repo_source.revision.commit_sha else ""
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
                    summary=f"Successfully retrieved {len(crawl_result.materials_retrieved)} targeted files ({crawl_result.total_bytes_fetched} bytes) from '{repo_target}'{rev_tag}.",
                    execution_time_seconds=elapsed,
                    metadata=report_metadata,
                )

            else:
                # 2. Generic Structure Discovery
                disc_opts = RepositoryDiscoveryOptions(
                    max_files=max_files,
                    max_depth=max_depth,
                    timeout_seconds=timeout_seconds,
                )
                discovered = self.discovery_engine.discover(
                    target=repo_target,
                    revision=revision,
                    options=disc_opts,
                    is_cancelled=is_task_cancelled,
                )
                repo_source = discovered.to_source_model()
                elapsed = round(time.perf_counter() - start_time, 4)

                self.tasks_completed += 1
                self.transition_to(CrawlerStatus.COMPLETED, reason=f"Discovered repository structure with {discovered.total_files} files")

                if context and hasattr(context, "progress"):
                    context.progress.report(100.0, f"Discovered {discovered.total_files} files across {discovered.total_directories} directories in '{repo_target}'")
                if context and hasattr(context, "events"):
                    try:
                        context.events.emit(
                            "repository_discovery_completed",
                            {
                                "repository": repo_target,
                                "total_files": discovered.total_files,
                                "total_directories": discovered.total_directories,
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
                    status=CrawlerReportStatus.SUCCESS,
                    raw_sources=[],
                    extracted_evidence=[],
                    summary=f"Discovered repository structure for '{repo_target}' with {discovered.total_files} files.",
                    execution_time_seconds=elapsed,
                    metadata={"discovered_repository": discovered.to_dict()},
                )

        except RepositoryTimeoutError as te:
            elapsed = round(time.perf_counter() - start_time, 4)
            sanitized = sanitize_error(te)
            logger.warning(f"RepositoryCrawler '{self.crawler_id}' timed out: {sanitized}")
            self.tasks_failed += 1
            self.health = CrawlerHealthStatus.DEGRADED
            self.transition_to(CrawlerStatus.FAILED, reason=f"Operation timed out: {sanitized}")
            if context and hasattr(context, "events"):
                try:
                    context.events.emit("repository_crawl_timeout", {"repository": repo_target, "error": sanitized})
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
                summary=f"Repository operation timed out: {sanitized}",
                error_message=sanitized,
                execution_time_seconds=elapsed,
            )

        except RepositoryCancelledError as ce:
            elapsed = round(time.perf_counter() - start_time, 4)
            sanitized = sanitize_error(ce)
            logger.info(f"RepositoryCrawler '{self.crawler_id}' cancelled: {sanitized}")
            self.transition_to(CrawlerStatus.CANCELLED, reason=sanitized)
            if context and hasattr(context, "events"):
                try:
                    context.events.emit("repository_crawl_cancelled", {"repository": repo_target, "reason": sanitized})
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
                summary=f"Repository crawl cancelled: {sanitized}",
                error_message=sanitized,
                execution_time_seconds=elapsed,
            )

        except (RepositoryError, Exception) as e:
            elapsed = round(time.perf_counter() - start_time, 4)
            sanitized = sanitize_error(e)
            logger.error(f"RepositoryCrawler '{self.crawler_id}' error executing task '{task.task_id}': {sanitized}")
            self.tasks_failed += 1
            self.health = CrawlerHealthStatus.DEGRADED
            self.transition_to(CrawlerStatus.FAILED, reason=f"Crawl error: {sanitized}")
            if context and hasattr(context, "events"):
                try:
                    context.events.emit("repository_crawl_failed", {"repository": repo_target, "error": sanitized})
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
                summary=f"Repository crawl failed: {sanitized}",
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
        repo_target = str(
            input_data.get("repo")
            or input_data.get("repository")
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
            query_or_target=repo_target,
            required_capability=CrawlerCapability.REPOSITORY_INSPECTION,
            parameters=dict(input_data),
        )
        self.reset_status()
        report = self.execute_crawler_task(ctask, context=context)
        self.reset_status()
        return WorkerOutput(
            success=report.status in (CrawlerReportStatus.SUCCESS, CrawlerReportStatus.EMPTY, CrawlerReportStatus.PARTIAL),
            summary=report.summary,
            report_markdown=(
                f"# Repository Inspection Report\n\n"
                f"- Crawler: `{self.crawler_id}`\n"
                f"- Target: `{task.title or repo_target}`\n"
                f"- Status: `{report.status.value}`\n"
                f"- Files Retrieved: `{len(report.raw_sources)}`\n"
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
