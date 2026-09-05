"""
ProjectMemoryCrawler — Specialist Crawler for PROJECT_MEMORY_LOOKUP.

Scans and indexes the project's persistent memory scaffold under .autonomos/memory/,
converting internal Markdown documents into structured EvidenceItem objects with full
provenance, classification, and confidence metadata.

Architecture invariants:
- Zero external dependencies (stdlib only: pathlib, re, hashlib).
- Evidence classified by document role: FACT (architecture), SOURCE_CLAIM (decisions), OBSERVATION (state/tech_debt).
- SourceType.INTERNAL ensures memory evidence is never confused with external web content.
- All evidence checksums computed deterministically from file content.
"""
from __future__ import annotations

import hashlib
import logging
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional
import uuid

from core.models import Task, WorkerManifest, WorkerOutput, utc_now as core_utc_now
from core.research.contracts.crawler_report import CrawlerReport, RawSourceReference
from core.research.contracts.crawler_task import CrawlerTask
from core.research.contracts.evidence import EvidenceItem, EvidenceProvenance
from core.research.crawler.base import BaseCrawler
from core.research.errors import CrawlerExecutionError
from core.research.types import (
    CrawlerCapability,
    CrawlerReportStatus,
    CrawlerStatus,
    FactClassification,
    ResearchConfidence,
    SourceType,
)
from pkg.sdk.types import WorkerCapability
from pkg.sdk.worker import Worker, WorkerRuntimeContext

logger = logging.getLogger("AutonomOS.Worker.ProjectMemoryCrawler")


def utc_now() -> str:
    """Generate ISO 8601 UTC timestamp."""
    return datetime.now(timezone.utc).isoformat()


def compute_sha256(content: str) -> str:
    """Compute SHA-256 hex digest."""
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def _classify_memory_file(relative_path: str) -> tuple[FactClassification, ResearchConfidence, float]:
    """
    Classify a memory file path into its FactClassification and confidence.
    Returns (classification, confidence, reliability_score).
    """
    p = relative_path.lower().replace("\\", "/")

    # Core architectural documents — high confidence structural facts
    if "architecture.md" in p or "project-map.md" in p or "project_map.md" in p:
        return FactClassification.FACT, ResearchConfidence.WELL_SUPPORTED, 1.0

    # Decision records — represent deliberate past choices
    if "/decisions/" in p or "decision" in p:
        return FactClassification.SOURCE_CLAIM, ResearchConfidence.SUPPORTED, 0.92

    # Tech debt entries — inferences about current state / future work
    if "/tech_debt/" in p or "tech_debt" in p or "tech-debt" in p:
        return FactClassification.INFERENCE, ResearchConfidence.SUPPORTED, 0.85

    # Current state — live snapshot, lower certainty vs architecture
    if "current-state.md" in p or "current_state.md" in p:
        return FactClassification.INFERENCE, ResearchConfidence.SUPPORTED, 0.88

    # Research files
    if "/research/" in p:
        return FactClassification.SOURCE_CLAIM, ResearchConfidence.SUPPORTED, 0.80

    # Generic memory document
    return FactClassification.SOURCE_CLAIM, ResearchConfidence.LIMITED_EVIDENCE, 0.75


def _extract_title_from_markdown(content: str, fallback: str) -> str:
    """Extract H1 heading from Markdown content, or fall back to the filename."""
    for line in content.splitlines():
        stripped = line.strip()
        if stripped.startswith("# "):
            return stripped[2:].strip()
    return fallback


def _extract_sections(content: str, max_snippet_chars: int = 800) -> list[dict[str, str]]:
    """
    Split Markdown content into logical H2 sections, returning list of
    {heading, body} dicts for per-section evidence granularity.
    Returns a single section if no H2 headings exist.
    """
    sections = []
    current_heading = "Overview"
    current_body_lines: list[str] = []

    for line in content.splitlines():
        stripped = line.strip()
        if stripped.startswith("## "):
            if current_body_lines:
                body = " ".join(current_body_lines).strip()
                if body:
                    sections.append({"heading": current_heading, "body": body[:max_snippet_chars]})
            current_heading = stripped[3:].strip()
            current_body_lines = []
        else:
            # Skip pure markdown decorations (---) and empty lines
            if stripped and not stripped.startswith("#") and not re.match(r"^-{3,}$", stripped):
                current_body_lines.append(stripped)

    if current_body_lines:
        body = " ".join(current_body_lines).strip()
        if body:
            sections.append({"heading": current_heading, "body": body[:max_snippet_chars]})

    return sections if sections else [{"heading": "Content", "body": content[:max_snippet_chars]}]


def _matches_query(content: str, title: str, query: str) -> bool:
    """Check whether a memory document matches the given search query."""
    if not query:
        return True
    query_lower = query.lower().strip()
    return query_lower in content.lower() or query_lower in title.lower()


class ProjectMemoryCrawler(Worker, BaseCrawler):
    """
    Production Project Memory Crawler for AutonomOS.
    Implements CrawlerCapability.PROJECT_MEMORY_LOOKUP by scanning the project's
    .autonomos/memory/ directory and converting Markdown memory files into structured
    EvidenceItem objects with full provenance, classification, and content extraction.
    """

    def __init__(
        self,
        crawler_id: Optional[str] = None,
        name: str = "ProjectMemoryCrawler",
        capabilities: Optional[list[CrawlerCapability]] = None,
        project_root: Optional[str] = None,
        version: str = "1.0.0",
    ):
        cid = crawler_id or f"crawler.project_memory.{uuid.uuid4().hex[:6]}"
        caps = capabilities or [CrawlerCapability.PROJECT_MEMORY_LOOKUP]
        BaseCrawler.__init__(self, crawler_id=cid, capabilities=caps, name=name)

        self._project_root: Optional[Path] = Path(project_root).resolve() if project_root else None
        self._version = version

        self._manifest = WorkerManifest(
            id=self.crawler_id,
            name=self.name,
            role="Crawler",
            description="Specialist crawler indexing project memory documents under .autonomos/memory/",
            version=self._version,
            capabilities=[
                WorkerCapability.RESEARCH.value,
                WorkerCapability.STRUCTURED_OUTPUT.value,
                "PROJECT_MEMORY_LOOKUP",
                "EVIDENCE_EXTRACTION",
            ] + [c.value for c in self._capabilities],
            permissions=["filesystem.readonly", "*"],
            created_at=utc_now(),
        )
        self.transition_to(CrawlerStatus.QUEUED, reason="ProjectMemoryCrawler initialized and queued")

    def get_manifest(self) -> WorkerManifest:
        return self._manifest

    def _resolve_memory_root(self, task: CrawlerTask) -> Optional[Path]:
        """
        Resolve the .autonomos/memory/ directory from:
        1. task.parameters["project_root"]
        2. task.parameters["memory_dir"] directly
        3. self._project_root
        4. task.query_or_target if it looks like a path
        Returns None if unresolvable.
        """
        params = task.parameters or {}

        # Explicit memory_dir override
        memory_dir_param = params.get("memory_dir")
        if memory_dir_param:
            p = Path(str(memory_dir_param)).resolve()
            if p.is_dir():
                return p

        # project_root param
        proj_root_param = params.get("project_root")
        if proj_root_param:
            candidate = Path(str(proj_root_param)).resolve() / ".autonomos" / "memory"
            if candidate.is_dir():
                return candidate

        # Constructor-level project root
        if self._project_root:
            candidate = self._project_root / ".autonomos" / "memory"
            if candidate.is_dir():
                return candidate

        # task.query_or_target as a directory hint
        target = task.query_or_target.strip()
        if target and not target.startswith(("http://", "https://")) and len(target) < 512:
            as_path = Path(target)
            if as_path.is_dir():
                return as_path

        return None

    def _scan_memory_dir(self, memory_root: Path) -> list[Path]:
        """Recursively collect .md files from the memory directory."""
        md_files: list[Path] = []
        try:
            for p in sorted(memory_root.rglob("*.md")):
                if p.is_file():
                    md_files.append(p)
        except (PermissionError, OSError) as e:
            logger.warning("Failed to scan memory directory %s: %s", memory_root, e)
        return md_files

    def execute_crawler_task(
        self,
        task: CrawlerTask,
        context: Optional[WorkerRuntimeContext] = None,
    ) -> CrawlerReport:
        """
        Execute the PROJECT_MEMORY_LOOKUP task.
        Scans .autonomos/memory/, filters by query, and produces EvidenceItems.
        """
        start_time = time.perf_counter()
        self.current_task = task
        if self.status in (CrawlerStatus.COMPLETED, CrawlerStatus.FAILED, CrawlerStatus.CANCELLED):
            self.transition_to(CrawlerStatus.QUEUED, reason="Re-queuing crawler for execution")
        self.transition_to(CrawlerStatus.RUNNING, reason=f"Executing memory lookup task '{task.task_id}'")
        self.heartbeat()

        # Handle pre-cancelled tasks
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
                error_message=task.cancellation_reason or "Task cancelled",
                execution_time_seconds=elapsed,
            )

        try:
            params = task.parameters or {}
            search_query = params.get("query") or params.get("topic") or task.query_or_target.strip()
            target_paths = params.get("target_paths")  # Optional list of specific relative paths
            max_docs = int(params.get("max_docs", 20))
            max_sections_per_doc = int(params.get("max_sections_per_doc", 5))

            if context and hasattr(context, "progress"):
                context.progress.report(10.0, "Resolving project memory directory")

            memory_root = self._resolve_memory_root(task)
            if memory_root is None:
                raise CrawlerExecutionError(
                    self.crawler_id,
                    task.task_id,
                    "Could not locate .autonomos/memory/ directory. Provide 'project_root' in task parameters.",
                )

            logger.info("ProjectMemoryCrawler scanning %s (query=%r)", memory_root, search_query)

            if context and hasattr(context, "progress"):
                context.progress.report(25.0, f"Scanning {memory_root}")

            md_files = self._scan_memory_dir(memory_root)
            if not md_files:
                elapsed = round(time.perf_counter() - start_time, 4)
                self.tasks_completed += 1
                self.transition_to(CrawlerStatus.COMPLETED, reason="No memory documents found")
                return CrawlerReport(
                    report_id=f"crep-{uuid.uuid4().hex[:8]}",
                    crawler_task_id=task.task_id,
                    crawler_id=self.crawler_id,
                    request_id=task.request_id,
                    plan_id=task.plan_id,
                    question_id=task.question_id,
                    correlation_id=task.correlation_id,
                    status=CrawlerReportStatus.PARTIAL,
                    summary="No memory documents found in .autonomos/memory/",
                    execution_time_seconds=elapsed,
                )

            # Filter by target_paths if specified
            if target_paths:
                target_set = {p.lower().replace("\\", "/") for p in target_paths}
                md_files = [
                    f for f in md_files
                    if any(tp in str(f).lower().replace("\\", "/") for tp in target_set)
                ]

            raw_sources: list[RawSourceReference] = []
            extracted_evidence: list[EvidenceItem] = []
            docs_processed = 0

            for md_file in md_files:
                if docs_processed >= max_docs:
                    break

                try:
                    content = md_file.read_text(encoding="utf-8", errors="replace")
                except OSError as e:
                    logger.warning("Could not read %s: %s", md_file, e)
                    continue

                rel_path = str(md_file.relative_to(memory_root.parent.parent) if memory_root.parent.parent.exists() else md_file)
                title = _extract_title_from_markdown(content, md_file.stem)

                # Skip if query doesn't match
                if search_query and not _matches_query(content, title, search_query):
                    continue

                classification, confidence, reliability = _classify_memory_file(rel_path)
                content_checksum = compute_sha256(content)
                docs_processed += 1

                raw_sources.append(
                    RawSourceReference(
                        url_or_ref=rel_path,
                        title=title,
                        publisher="AutonomOS Project Memory",
                        source_type=SourceType.PROJECT_MEMORY,
                        checksum=content_checksum,
                        bytes_fetched=len(content.encode("utf-8")),
                        content_snippet=content[:400],
                    )
                )

                # Extract per-section evidence
                sections = _extract_sections(content)
                for sec_idx, section in enumerate(sections[:max_sections_per_doc]):
                    heading = section["heading"]
                    body = section["body"]
                    if not body.strip():
                        continue

                    extracted_fact = f"[{title}] {heading}: {body[:300]}"

                    prov = EvidenceProvenance(
                        request_id=task.request_id,
                        crawler_task_id=task.task_id,
                        crawler_id=self.crawler_id,
                        question_id=task.question_id,
                        source_ref=rel_path,
                        correlation_id=task.correlation_id,
                        captured_at=utc_now(),
                    )

                    ev = EvidenceItem(
                        evidence_id=f"ev-mem-{uuid.uuid4().hex[:8]}",
                        provenance=prov,
                        extracted_fact=extracted_fact,
                        content_snippet=body[:500],
                        classification=classification,
                        confidence=confidence,
                        reliability_score=reliability,
                        source_type=SourceType.PROJECT_MEMORY,
                        metadata={
                            "memory_file": rel_path,
                            "section_heading": heading,
                            "section_index": sec_idx,
                            "document_title": title,
                            "file_checksum": content_checksum,
                        },
                    )
                    extracted_evidence.append(ev)

            elapsed = round(time.perf_counter() - start_time, 4)

            if context and hasattr(context, "progress"):
                context.progress.report(95.0, f"Processed {docs_processed} memory document(s)")

            self.tasks_completed += 1
            self.transition_to(CrawlerStatus.COMPLETED, reason="Project memory lookup completed")

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
                summary=(
                    f"Scanned {docs_processed} memory document(s) in '{memory_root.name}', "
                    f"extracted {len(extracted_evidence)} evidence item(s)"
                ),
                execution_time_seconds=elapsed,
                metadata={
                    "memory_root": str(memory_root),
                    "docs_scanned": docs_processed,
                    "evidence_count": len(extracted_evidence),
                    "query": search_query,
                },
            )

        except Exception as e:
            elapsed = round(time.perf_counter() - start_time, 4)
            self.tasks_failed += 1
            self.transition_to(CrawlerStatus.FAILED, reason=f"Memory crawler error: {e}")
            return CrawlerReport(
                report_id=f"crep-fail-{uuid.uuid4().hex[:8]}",
                crawler_task_id=task.task_id,
                crawler_id=self.crawler_id,
                request_id=task.request_id,
                plan_id=task.plan_id,
                question_id=task.question_id,
                correlation_id=task.correlation_id,
                status=CrawlerReportStatus.FAILED,
                summary=f"Project memory lookup failed: {e}",
                error_message=str(e),
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
            required_capability=CrawlerCapability.PROJECT_MEMORY_LOOKUP,
            parameters=dict(task.metadata),
        )

        report = self.execute_crawler_task(crawler_task, context=context)

        return WorkerOutput(
            success=report.status in (CrawlerReportStatus.SUCCESS, CrawlerReportStatus.EMPTY, CrawlerReportStatus.PARTIAL),
            summary=report.summary,
            report_markdown=(
                f"# Project Memory Lookup Report\n\n"
                f"- Crawler: `{self.crawler_id}`\n"
                f"- Target: `{task.title}`\n"
                f"- Status: `{report.status.value}`\n"
                f"- Evidence Items: `{len(report.extracted_evidence)}`\n\n"
                f"{report.summary}"
            ),
            error_message=report.error_message,
            metadata={"crawler_report": report.to_dict()},
        )
