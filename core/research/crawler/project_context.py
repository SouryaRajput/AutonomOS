"""
ProjectContextCrawler — Production Specialist Crawler for PROJECT_CONTEXT_CRAWL (Phase 1 / Part 8 / Step 8).

Discovers, selects, and extracts structured source code, explicit contracts, documentation,
configurations, declared dependencies, and optional VCS state from a project workspace.

Architectural Invariants & Security Guarantees:
- Strictly READ-ONLY: Never creates, modifies, deletes, or renames files; never alters VCS or permissions.
- Zero-execution: Never runs shell commands, builds, test runners, compilers, or package managers.
- Root containment: Bounded strictly within project root; rejects directory traversals and symlink escapes.
- Secret shielding: Blocks sensitive files (.env, keys, credentials); scrubs inline secrets from content.
- Prompt injection as passive data: Code comments, READMEs, configs, and docstrings containing prompt
  injection strings remain inert text data; never parsed as instructions or tool invocations.
- Truthful partial failures: File-level failures are recorded in metadata without fabricating success.
- Cryptographic provenance: Every evidence item links to project ID, relative path, line range,
  content SHA-256 hash, captured timestamp, and research task lineage.
- State isolation: Clean execution isolation across repeated invocations on the same crawler instance.
"""
from __future__ import annotations

import logging
import os
import posixpath
import time
from typing import Any, Callable, Optional
import uuid

from core.models import Task, WorkerManifest, WorkerOutput, utc_now
from core.research.contracts.crawler_report import CrawlerReport, RawSourceReference
from core.research.contracts.crawler_task import CrawlerTask
from core.research.contracts.evidence import EvidenceItem, EvidenceProvenance
from core.research.crawler.base import BaseCrawler
from core.research.errors import (
    CrawlerExecutionError,
    ProjectAccessError,
    ProjectCancelledError,
    ProjectContextError,
    ProjectFileNotFoundError,
    ProjectMalformedFileError,
    ProjectNotFoundError,
    ProjectProviderError,
    ProjectResourceLimitError,
    ProjectSecurityError,
    ProjectTimeoutError,
    ProjectValidationError,
)
from core.research.project.context_extractor import ProjectContextExtractor
from core.research.project.discovery import (
    DiscoveredProjectStructure,
    ProjectDiscoveryOptions,
    ProjectStructureDiscoverer,
)
from core.research.project.extractor import (
    ProjectExtractionOptions,
    ProjectSourceExtractor,
)
from core.research.project.local_provider import LocalProjectWorkspaceProvider
from core.research.project.models import (
    ProjectContext,
    ProjectStructure,
)
from core.research.project.provider import (
    ProjectFetchLimits,
    ProjectWorkspaceProvider,
    normalize_project_path,
    sanitize_content_secrets,
    sanitize_project_error,
)
from core.research.project.selector import (
    ProjectContextSelector,
    ProjectSelectionOptions,
    ProjectSelectionQuery,
    SelectedProjectContext,
    classify_project_file_category,
)
from core.research.project.vcs import (
    ProjectVCSInspector,
    ProjectVCSOptions,
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

logger = logging.getLogger("AutonomOS.Worker.ProjectContextCrawler")


class ProjectContextCrawler(Worker, BaseCrawler):
    """
    Specialist Crawler for PROJECT_CONTEXT_CRAWL tasks in AutonomOS.

    Executes bounded, deterministic discovery, ranking selection, source structure extraction,
    and contextual inspection of local project workspaces with complete cryptographic provenance.
    """

    def __init__(
        self,
        crawler_id: Optional[str] = None,
        name: str = "ProjectContextCrawler",
        provider: Optional[ProjectWorkspaceProvider] = None,
        capabilities: Optional[list[CrawlerCapability]] = None,
        limits: Optional[ProjectFetchLimits] = None,
        version: str = "1.0.0",
    ):
        cid = crawler_id or f"crawler.project.{uuid.uuid4().hex[:6]}"
        caps = capabilities or [
            CrawlerCapability.PROJECT_CONTEXT_CRAWL,
            CrawlerCapability.REPOSITORY_INSPECTION,
        ]
        BaseCrawler.__init__(self, crawler_id=cid, capabilities=caps, name=name)

        self.provider = provider
        self._limits = limits or ProjectFetchLimits()
        self._version = version

        # Active ephemeral context (isolated per execution)
        self._last_context: Optional[ProjectContext] = None

        self._manifest = WorkerManifest(
            id=self.crawler_id,
            name=self.name,
            role="Crawler",
            description="Specialist crawler for discovering, selecting, and extracting structured context from project workspaces",
            version=self._version,
            capabilities=[
                WorkerCapability.RESEARCH.value,
                WorkerCapability.STRUCTURED_OUTPUT.value,
                "PROJECT_CONTEXT_CRAWL",
                "REPOSITORY_INSPECTION",
                "CODE_COLLECTION",
                "EVIDENCE_EXTRACTION",
            ] + [c.value for c in self._capabilities],
            permissions=["project", "local", "repo", "*"],
            created_at=utc_now(),
        )
        self.transition_to(CrawlerStatus.QUEUED, reason="ProjectContextCrawler initialized and queued")

    def get_manifest(self) -> WorkerManifest:
        return self._manifest

    @property
    def last_project_context(self) -> Optional[ProjectContext]:
        """Access the most recent extracted ProjectContext container."""
        return self._last_context

    def _resolve_provider(self, task: CrawlerTask) -> ProjectWorkspaceProvider:
        """
        Deterministically resolve the project workspace provider for this task.
        Checks task parameters, default injected provider, or constructs a local provider.
        """
        params = task.parameters or {}

        # 1. Directly supplied provider instance (e.g. FakeProjectWorkspaceProvider in tests)
        if "provider" in params and isinstance(params["provider"], ProjectWorkspaceProvider):
            return params["provider"]

        # 2. Injected provider at initialization
        if self.provider is not None:
            return self.provider

        # 3. Path specified in parameters or query_or_target
        root_path = (
            params.get("root_path")
            or params.get("project_root")
            or params.get("path")
            or task.query_or_target
        )

        if not root_path or not str(root_path).strip():
            raise ProjectValidationError("root_path", "No project root directory or provider provided in task.")

        cleaned_path = str(root_path).strip()
        if not os.path.isabs(cleaned_path):
            cleaned_path = os.path.abspath(cleaned_path)

        if not os.path.exists(cleaned_path) or not os.path.isdir(cleaned_path):
            raise ProjectNotFoundError(cleaned_path, reason=f"Project directory does not exist: '{cleaned_path}'")

        # Create safe LocalProjectWorkspaceProvider
        provider_limits = self._limits
        if "limits" in params and isinstance(params["limits"], ProjectFetchLimits):
            provider_limits = params["limits"]

        return LocalProjectWorkspaceProvider(
            root_path=cleaned_path,
            provider_id=f"local-{posixpath.basename(cleaned_path)}",
            limits=provider_limits,
        )

    def execute_crawler_task(
        self,
        task: CrawlerTask,
        context: Optional[WorkerRuntimeContext] = None,
    ) -> CrawlerReport:
        """
        Execute an assigned PROJECT_CONTEXT_CRAWL or REPOSITORY_INSPECTION CrawlerTask.
        Orchestrates discovery, selection, structural source extraction, context extraction,
        and safe VCS inspection with complete cryptographic provenance.
        """
        start_time = time.perf_counter()
        self.current_task = task
        self._last_context = None
        self.transition_to(CrawlerStatus.RUNNING, reason=f"Executing project task '{task.task_id}'")
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

        def is_task_cancelled() -> bool:
            return self.status == CrawlerStatus.CANCELLED or task.status.value == "CANCELLED"

        try:
            # 1. Resolve Provider
            provider = self._resolve_provider(task)
            params = task.parameters or {}

            # 2. Parse Task Parameters and Budgets
            query_str = (
                params.get("query")
                or params.get("topic")
                or params.get("question")
                or task.objective
                or task.query_or_target
                or ""
            )

            keywords = params.get("keywords")
            if isinstance(keywords, str):
                keywords = [k.strip() for k in keywords.split(",") if k.strip()]

            target_paths = params.get("target_paths") or params.get("paths")
            if isinstance(target_paths, str):
                target_paths = [target_paths.strip()]

            exclude_paths = params.get("exclude_paths") or []
            if isinstance(exclude_paths, str):
                exclude_paths = [exclude_paths.strip()]

            max_depth = int(params.get("max_depth", self._limits.max_tree_depth))
            max_files = int(params.get("max_files", 50))
            max_bytes = int(params.get("max_bytes", 1_000_000))
            max_file_size = int(params.get("max_file_size", 250_000))
            max_parse_bytes = int(params.get("max_parse_bytes", 500_000))
            max_symbols_per_file = int(params.get("max_symbols_per_file", 200))
            timeout_seconds = float(params.get("timeout_seconds", self._limits.timeout_seconds))

            extract_source = bool(params.get("extract_source", True))
            extract_contracts = bool(params.get("extract_contracts", True))
            extract_docs = bool(params.get("extract_docs", True))
            extract_configs = bool(params.get("extract_configs", True))
            extract_dependencies = bool(params.get("extract_dependencies", True))
            allow_vcs = bool(params.get("allow_vcs", True))
            include_vcs = bool(params.get("include_vcs", False))

            if context and hasattr(context, "progress"):
                context.progress.report(10.0, f"Identifying project root at '{provider.root_path}'")
            if context and hasattr(context, "events"):
                try:
                    context.events.emit(
                        "project_crawl_started",
                        {
                            "root_path": provider.root_path,
                            "query": query_str,
                            "task_id": task.task_id,
                        },
                    )
                except Exception:
                    pass

            # 3. Identify Project Root
            project_ident = provider.identify_root(
                timeout_seconds=timeout_seconds,
                is_cancelled=is_task_cancelled,
            )

            # 4. Discover Project Structure
            if context and hasattr(context, "progress"):
                context.progress.report(25.0, f"Discovering project structure for '{project_ident.name}'")

            discoverer = ProjectStructureDiscoverer()
            discovered = discoverer.discover(
                provider=provider,
                options=ProjectDiscoveryOptions(
                    max_depth=max_depth,
                    max_files=max_files * 10,  # discover broader structure for selection
                    timeout_seconds=timeout_seconds,
                ),
                is_cancelled=is_task_cancelled,
            )
            structure = discovered.structure
            if context and hasattr(context, "events"):
                try:
                    context.events.emit(
                        "project_structure_discovered",
                        {
                            "project_id": project_ident.project_id,
                            "project_name": project_ident.name,
                            "total_files": structure.total_files,
                            "total_directories": structure.total_directories,
                        },
                    )
                except Exception:
                    pass

            # 5. Deterministic Context Selection
            if context and hasattr(context, "progress"):
                context.progress.report(50.0, f"Selecting relevant files for query '{query_str}'")

            selector = ProjectContextSelector()
            selection_query = ProjectSelectionQuery(
                raw_topic=query_str,
                keywords=keywords,
                target_paths=target_paths,
                exclude_paths=exclude_paths,
            )
            selection_options = ProjectSelectionOptions(
                max_files=max_files,
                max_total_bytes=max_bytes,
                max_file_size=max_file_size,
                max_depth=max_depth,
                timeout_seconds=timeout_seconds,
            )

            selection_result = selector.select(
                provider=provider,
                query=selection_query,
                options=selection_options,
                discovered=discovered,
                is_cancelled=is_task_cancelled,
            )
            project_context = selection_result.to_project_context()

            if context and hasattr(context, "events"):
                try:
                    context.events.emit(
                        "project_context_selected",
                        {
                            "project_id": project_ident.project_id,
                            "selected_files_count": len(project_context.source_materials),
                            "total_bytes": selection_result.total_bytes_fetched,
                            "is_truncated": selection_result.is_truncated,
                        },
                    )
                except Exception:
                    pass

            # 6. Extract Source Structure & Contracts
            if extract_source and project_context.source_materials:
                if context and hasattr(context, "progress"):
                    context.progress.report(70.0, f"Extracting code structure from {len(project_context.source_materials)} files")

                source_extractor = ProjectSourceExtractor()
                source_extractor.populate_context(
                    project_context=project_context,
                    options=ProjectExtractionOptions(
                        max_parse_bytes=max_parse_bytes,
                        max_symbols_per_file=max_symbols_per_file,
                        extract_contracts=extract_contracts,
                        is_cancelled=is_task_cancelled,
                    ),
                )
                if context and hasattr(context, "events"):
                    try:
                        context.events.emit(
                            "project_extraction_completed",
                            {
                                "project_id": project_ident.project_id,
                                "symbols_count": len(project_context.symbols),
                                "contracts_count": len(project_context.contracts),
                            },
                        )
                    except Exception:
                        pass

            # 7. Extract Documentation, Configuration, and Declared Dependencies
            if (extract_docs or extract_configs or extract_dependencies) and structure.files:
                if context and hasattr(context, "progress"):
                    context.progress.report(85.0, "Extracting documentation, configs, and dependencies")

                doc_and_cfg_paths = [
                    f.relative_path for f in structure.files
                    if (
                        (extract_docs and classify_project_file_category(f.relative_path) == "doc")
                        or (extract_configs and classify_project_file_category(f.relative_path) == "config")
                        or (extract_dependencies and classify_project_file_category(f.relative_path) == "manifest")
                    )
                ][:30]  # bounded manifest/doc inspection

                if target_paths:
                    norm_targets = {normalize_project_path(tp) for tp in target_paths}
                    doc_and_cfg_paths = [
                        p for p in doc_and_cfg_paths
                        if any(p == t or p.startswith(t.rstrip("/") + "/") for t in norm_targets)
                    ]

                doc_and_cfg_files: list[tuple[str, str]] = []
                for p in doc_and_cfg_paths:
                    if is_task_cancelled():
                        raise ProjectCancelledError("project_context_crawl", p, "Task cancelled")
                    try:
                        mat = provider.get_file_content(
                            file_path=p,
                            max_bytes=max_parse_bytes,
                            timeout_seconds=min(5.0, timeout_seconds),
                            is_cancelled=is_task_cancelled,
                        )
                        if mat and mat.content:
                            doc_and_cfg_files.append((p, mat.content))
                    except Exception as fetch_err:
                        logger.debug(f"Skipping unreadable doc/config {p}: {fetch_err}")

                ProjectContextExtractor.extract_all(
                    files=doc_and_cfg_files,
                    project_context=project_context,
                    provenance=EvidenceProvenance(
                        request_id=task.request_id,
                        crawler_task_id=task.task_id,
                        crawler_id=self.crawler_id,
                        question_id=task.question_id,
                    ),
                )
                if context and hasattr(context, "events"):
                    try:
                        context.events.emit(
                            "project_docs_configs_inspected",
                            {
                                "project_id": project_ident.project_id,
                                "docs_count": len(project_context.documentation),
                                "configs_count": len(project_context.configurations),
                                "dependencies_count": len(project_context.dependencies),
                            },
                        )
                    except Exception:
                        pass

            # 8. Safe VCS Inspection (Read-Only)
            if allow_vcs:
                try:
                    vcs_ctx = provider.get_vcs_metadata(
                        allow_vcs=True,
                        timeout_seconds=min(5.0, timeout_seconds),
                        is_cancelled=is_task_cancelled,
                    )
                    project_context.vcs = vcs_ctx
                except Exception as vcs_err:
                    logger.warning(f"Project VCS inspection failed gracefully: {sanitize_project_error(vcs_err)}")

            self._last_context = project_context
            elapsed = round(time.perf_counter() - start_time, 4)

            # 9. Format CrawlerReport and Evidence
            raw_sources = project_context.to_raw_source_references()
            extracted_evidence = project_context.to_evidence_items(
                request_id=task.request_id,
                crawler_task_id=task.task_id,
                crawler_id=self.crawler_id,
                question_id=task.question_id,
                correlation_id=task.correlation_id or "",
            )

            # Bounded output guard
            if len(extracted_evidence) > max_symbols_per_file * max_files:
                extracted_evidence = extracted_evidence[: max_symbols_per_file * max_files]

            # If no files selected and failures occurred, report partial
            if not project_context.source_materials and selection_result.file_failures:
                self.transition_to(CrawlerStatus.COMPLETED, reason="Partial crawl with file failures")
                return CrawlerReport(
                    report_id=f"crep-{uuid.uuid4().hex[:8]}",
                    crawler_task_id=task.task_id,
                    crawler_id=self.crawler_id,
                    request_id=task.request_id,
                    plan_id=task.plan_id,
                    question_id=task.question_id,
                    correlation_id=task.correlation_id,
                    status=CrawlerReportStatus.PARTIAL,
                    raw_sources=[],
                    extracted_evidence=[],
                    summary=f"Project crawl completed with {len(selection_result.file_failures)} file errors.",
                    execution_time_seconds=elapsed,
                    metadata={
                        "project_id": project_ident.project_id,
                        "project_name": project_ident.name,
                        "root_path": provider.root_path,
                        "file_failures": selection_result.file_failures,
                    },
                )

            # Check for empty result
            if len(project_context.source_materials) == 0 and len(extracted_evidence) == 0:
                self.tasks_completed += 1
                self.transition_to(CrawlerStatus.COMPLETED, reason="Project crawl completed with 0 matching materials")
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
                    summary=f"No matching project materials found for query '{query_str}' in '{provider.root_path}'.",
                    execution_time_seconds=elapsed,
                    metadata={
                        "project_id": project_ident.project_id,
                        "project_name": project_ident.name,
                        "root_path": provider.root_path,
                        "discovered_files_count": structure.total_files,
                        "discovered_directories_count": structure.total_directories,
                        "selected_files_count": 0,
                        "file_failures": selection_result.file_failures,
                    },
                )

            # Determine truthful report status (PARTIAL if any file failures occurred or truncated)
            has_failures = bool(selection_result.file_failures)
            is_truncated = selection_result.is_truncated or structure.is_truncated
            report_status = CrawlerReportStatus.PARTIAL if (has_failures or is_truncated) else CrawlerReportStatus.SUCCESS

            if context and hasattr(context, "progress"):
                context.progress.report(100.0, f"Project context extraction complete ({len(extracted_evidence)} evidence items)")
            if context and hasattr(context, "events"):
                try:
                    context.events.emit(
                        "project_crawl_completed",
                        {
                            "project_id": project_ident.project_id,
                            "project_name": project_ident.name,
                            "revision": project_context.vcs.revision if project_context.vcs else None,
                            "status": report_status.value,
                            "materials_count": len(project_context.source_materials),
                            "evidence_count": len(extracted_evidence),
                            "total_bytes": selection_result.total_bytes_fetched,
                            "partial_failures_count": len(selection_result.file_failures),
                        },
                    )
                except Exception:
                    pass

            rev_tag = f" @ {project_context.vcs.revision[:8]}" if project_context.vcs and project_context.vcs.revision else ""
            summary = (
                f"Retrieved {len(project_context.source_materials)} project files ({selection_result.total_bytes_fetched} bytes), "
                f"{len(project_context.contracts)} contracts, {len(project_context.documentation)} doc sections, and "
                f"{len(project_context.dependencies)} dependencies from '{project_ident.name}'{rev_tag}."
            )

            report_metadata = {
                "project_id": project_ident.project_id,
                "project_name": project_ident.name,
                "root_path": provider.root_path,
                "project_type": project_ident.project_type.value,
                "languages": project_ident.languages,
                "discovered_files_count": structure.total_files,
                "discovered_directories_count": structure.total_directories,
                "selected_files_count": len(project_context.source_materials),
                "total_bytes_selected": selection_result.total_bytes_fetched,
                "symbols_count": len(project_context.symbols),
                "contracts_count": len(project_context.contracts),
                "docs_count": len(project_context.documentation),
                "configs_count": len(project_context.configurations),
                "dependencies_count": len(project_context.dependencies),
                "vcs_status": project_context.vcs.working_tree_state.value if project_context.vcs else "unknown",
                "file_failures": selection_result.file_failures,
                "is_truncated": is_truncated,
            }

            self.tasks_completed += 1
            self.transition_to(CrawlerStatus.COMPLETED, reason=f"Extracted {len(project_context.source_materials)} project materials")

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
                summary=summary,
                execution_time_seconds=elapsed,
                metadata=report_metadata,
            )

        except ProjectCancelledError as pce:
            self.transition_to(CrawlerStatus.CANCELLED, reason=str(pce))
            elapsed = round(time.perf_counter() - start_time, 4)
            sanitized_err = sanitize_project_error(pce)
            if context and hasattr(context, "events"):
                try:
                    context.events.emit(
                        "project_crawl_cancelled",
                        {
                            "query": query_str,
                            "reason": sanitized_err,
                        },
                    )
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
                summary=f"Project crawl cancelled: {pce}",
                error_message=sanitized_err,
                execution_time_seconds=elapsed,
                metadata={"reason": "cancelled"},
            )

        except ProjectTimeoutError as pte:
            self.tasks_failed += 1
            self.transition_to(CrawlerStatus.FAILED, reason=str(pte))
            elapsed = round(time.perf_counter() - start_time, 4)
            sanitized_err = sanitize_project_error(pte)
            if context and hasattr(context, "events"):
                try:
                    context.events.emit(
                        "project_crawl_timeout",
                        {
                            "query": query_str,
                            "error": sanitized_err,
                        },
                    )
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
                summary=f"Project crawl timed out: {pte}",
                error_message=sanitized_err,
                execution_time_seconds=elapsed,
                metadata={"reason": "timeout"},
            )

        except ProjectNotFoundError as pnfe:
            self.tasks_failed += 1
            self.transition_to(CrawlerStatus.FAILED, reason=str(pnfe))
            elapsed = round(time.perf_counter() - start_time, 4)
            sanitized_err = sanitize_project_error(pnfe)
            if context and hasattr(context, "events"):
                try:
                    context.events.emit(
                        "project_crawl_failed",
                        {
                            "error": sanitized_err,
                            "reason": "project_not_found",
                        },
                    )
                except Exception:
                    pass
            return CrawlerReport(
                report_id=f"crep-notfound-{task.task_id}",
                crawler_task_id=task.task_id,
                crawler_id=self.crawler_id,
                request_id=task.request_id,
                plan_id=task.plan_id,
                question_id=task.question_id,
                correlation_id=task.correlation_id,
                status=CrawlerReportStatus.FAILED,
                summary=f"Project unavailable: {sanitized_err}",
                error_message=sanitized_err,
                execution_time_seconds=elapsed,
                metadata={"reason": "project_not_found"},
            )

        except ProjectAccessError as pae:
            self.tasks_failed += 1
            self.transition_to(CrawlerStatus.FAILED, reason=str(pae))
            elapsed = round(time.perf_counter() - start_time, 4)
            sanitized_err = sanitize_project_error(pae)
            if context and hasattr(context, "events"):
                try:
                    context.events.emit(
                        "project_crawl_failed",
                        {
                            "error": sanitized_err,
                            "reason": "access_denied",
                        },
                    )
                except Exception:
                    pass
            return CrawlerReport(
                report_id=f"crep-access-{task.task_id}",
                crawler_task_id=task.task_id,
                crawler_id=self.crawler_id,
                request_id=task.request_id,
                plan_id=task.plan_id,
                question_id=task.question_id,
                correlation_id=task.correlation_id,
                status=CrawlerReportStatus.FAILED,
                summary=f"Insufficient permissions: {sanitized_err}",
                error_message=sanitized_err,
                execution_time_seconds=elapsed,
                metadata={"reason": "access_denied"},
            )

        except ProjectSecurityError as pse:
            self.tasks_failed += 1
            self.transition_to(CrawlerStatus.FAILED, reason=str(pse))
            elapsed = round(time.perf_counter() - start_time, 4)
            sanitized_err = sanitize_project_error(pse)
            logger.warning(f"Security violation caught during project crawl: {sanitized_err}")
            if context and hasattr(context, "events"):
                try:
                    context.events.emit(
                        "project_crawl_failed",
                        {
                            "error": sanitized_err,
                            "reason": "security_violation",
                        },
                    )
                except Exception:
                    pass
            return CrawlerReport(
                report_id=f"crep-sec-{task.task_id}",
                crawler_task_id=task.task_id,
                crawler_id=self.crawler_id,
                request_id=task.request_id,
                plan_id=task.plan_id,
                question_id=task.question_id,
                correlation_id=task.correlation_id,
                status=CrawlerReportStatus.FAILED,
                summary=f"Project security violation: {sanitized_err}",
                error_message=sanitized_err,
                execution_time_seconds=elapsed,
                metadata={"reason": "security_violation"},
            )

        except Exception as exc:
            self.tasks_failed += 1
            self.transition_to(CrawlerStatus.FAILED, reason=str(exc))
            elapsed = round(time.perf_counter() - start_time, 4)
            sanitized_err = sanitize_project_error(exc)
            logger.exception(f"Unhandled error during project context crawl: {sanitized_err}")
            if context and hasattr(context, "events"):
                try:
                    context.events.emit(
                        "project_crawl_failed",
                        {
                            "error": sanitized_err,
                            "reason": "unhandled_error",
                        },
                    )
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
                summary=f"Project crawl failed: {sanitized_err}",
                error_message=sanitized_err,
                execution_time_seconds=elapsed,
                metadata={"reason": "unhandled_error"},
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
        project_target = str(
            input_data.get("root_path")
            or input_data.get("project_root")
            or input_data.get("query")
            or getattr(task, "objective", None)
            or getattr(task, "title", None)
            or ""
        )
        ctask = CrawlerTask(
            task_id=task.id,
            request_id=getattr(task, "parent_task_id", None) or task.id,
            plan_id=f"plan-{task.id}",
            question_id="q-direct",
            query_or_target=project_target,
            required_capability=CrawlerCapability.PROJECT_CONTEXT_CRAWL,
            parameters=dict(input_data),
        )
        self.reset_status()
        report = self.execute_crawler_task(ctask, context=context)
        self.reset_status()
        return WorkerOutput(
            success=report.status in (CrawlerReportStatus.SUCCESS, CrawlerReportStatus.EMPTY, CrawlerReportStatus.PARTIAL),
            summary=report.summary,
            report_markdown=(
                f"# Project Context Report\n\n"
                f"- Crawler: `{self.crawler_id}`\n"
                f"- Target: `{task.title or project_target}`\n"
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
