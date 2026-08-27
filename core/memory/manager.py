import hashlib
import json
import os
from pathlib import Path
import tempfile
import threading
from typing import Any, Optional
import uuid

from core.enums import IssueSeverity, IssueStatus, MemoryType
from core.errors import (
    MemoryAlreadyExistsError,
    MemoryConflictError,
    MemoryNotFoundError,
    ProjectNotFoundError,
)
from core.memory.model import MemoryDocument, ReferenceIssue, ValidationReport, compute_checksum, utc_now
from core.storage.base import Store


class MemoryManager:
    """
    Manages persistent project memory documents stored as human-readable Markdown files
    in the project workspace (under .autonomos/) and indexed in the metadata Store.
    """

    def __init__(self, store: Store):
        self.store = store
        self._lock = threading.RLock()

    def _get_project_root(self, project_id: str) -> Path:
        project = self.store.get_project(project_id)
        if not project:
            raise ProjectNotFoundError(project_id)
        return Path(project.root_path).resolve()

    def _resolve_full_path(self, project_id: str, relative_path: str) -> Path:
        root = self._get_project_root(project_id)
        # Normalize relative path: ensure it resides under .autonomos/ or root
        clean_rel = relative_path.lstrip("/\\")
        if not clean_rel.startswith(".autonomos"):
            clean_rel = f".autonomos/{clean_rel}"
        return (root / clean_rel).resolve()

    def _atomic_write_file(self, target_path: Path, content: str) -> None:
        """Atomically write text content to target_path using a temporary file and replace."""
        target_path.parent.mkdir(parents=True, exist_ok=True)
        # Write to temporary file in the same parent directory to ensure atomic os.replace across filesystem boundaries
        with tempfile.NamedTemporaryFile("w", dir=str(target_path.parent), delete=False, encoding="utf-8") as tf:
            tf.write(content)
            temp_name = tf.name

        try:
            os.replace(temp_name, str(target_path))
        except Exception:
            if os.path.exists(temp_name):
                os.remove(temp_name)
            raise

    # Core Memory CRUD Operations
    def create_memory(
        self,
        project_id: str,
        memory_type: MemoryType,
        title: str,
        content: str,
        relative_path: str,
        summary: str = "",
        tags: Optional[list[str]] = None,
        references: Optional[list[str]] = None,
        related_task_id: Optional[str] = None,
        related_worker_id: Optional[str] = None,
        metadata: Optional[dict[str, Any]] = None,
        memory_id: Optional[str] = None,
    ) -> MemoryDocument:
        with self._lock:
            # Validate project
            self._get_project_root(project_id)

            mid = memory_id or f"mem-{uuid.uuid4()}"
            if self.store.get_memory_document(mid):
                raise MemoryAlreadyExistsError(mid)

            clean_rel = relative_path.lstrip("/\\")
            if not clean_rel.startswith(".autonomos"):
                clean_rel = f".autonomos/{clean_rel}"

            existing_by_path = self.store.get_memory_document_by_path(project_id, clean_rel)
            if existing_by_path:
                raise MemoryAlreadyExistsError(f"Path '{clean_rel}' already exists in project '{project_id}'")

            # Write file atomically to disk
            full_path = self._resolve_full_path(project_id, clean_rel)
            self._atomic_write_file(full_path, content)

            doc = MemoryDocument(
                id=mid,
                project_id=project_id,
                memory_type=memory_type,
                title=title,
                relative_path=clean_rel,
                content=content,
                summary=summary,
                tags=tags or [],
                references=references or [],
                related_task_id=related_task_id,
                related_worker_id=related_worker_id,
                version=1,
                checksum=compute_checksum(content),
                metadata=metadata or {},
                created_at=utc_now(),
                updated_at=utc_now(),
            )

            self.store.save_memory_document(doc)
            return doc

    def read_memory(self, project_id: str, memory_id: str) -> MemoryDocument:
        with self._lock:
            doc = self.store.get_memory_document(memory_id)
            if not doc or doc.project_id != project_id:
                raise MemoryNotFoundError(memory_id)
            return doc

    def read_memory_by_path(self, project_id: str, relative_path: str) -> MemoryDocument:
        with self._lock:
            clean_rel = relative_path.lstrip("/\\")
            if not clean_rel.startswith(".autonomos"):
                clean_rel = f".autonomos/{clean_rel}"
            doc = self.store.get_memory_document_by_path(project_id, clean_rel)
            if not doc:
                raise MemoryNotFoundError(clean_rel, path=clean_rel)
            return doc

    def update_memory(
        self,
        project_id: str,
        memory_id: str,
        content: str,
        title: Optional[str] = None,
        summary: Optional[str] = None,
        tags: Optional[list[str]] = None,
        references: Optional[list[str]] = None,
        expected_version: Optional[int] = None,
    ) -> MemoryDocument:
        with self._lock:
            doc = self.read_memory(project_id, memory_id)

            if expected_version is not None and doc.version != expected_version:
                raise MemoryConflictError(memory_id, doc.version, expected_version)

            if title is not None:
                doc.title = title

            doc.update_content(
                new_content=content,
                summary=summary,
                tags=tags,
                references=references,
            )

            # Write file atomically to disk
            full_path = self._resolve_full_path(project_id, doc.relative_path)
            self._atomic_write_file(full_path, content)

            self.store.save_memory_document(doc)
            return doc

    def delete_memory(self, project_id: str, memory_id: str) -> bool:
        with self._lock:
            doc = self.read_memory(project_id, memory_id)
            full_path = self._resolve_full_path(project_id, doc.relative_path)
            if full_path.exists():
                try:
                    full_path.unlink()
                except Exception:
                    pass
            return self.store.delete_memory_document(memory_id)

    def list_memory(
        self,
        project_id: str,
        memory_type: Optional[MemoryType] = None,
        tag: Optional[str] = None,
        related_task_id: Optional[str] = None,
    ) -> list[MemoryDocument]:
        return self.store.list_memory_documents(
            project_id=project_id,
            memory_type=memory_type,
            tag=tag,
            related_task_id=related_task_id,
        )

    list_memories = list_memory

    def exists(self, project_id: str, memory_id: str) -> bool:
        doc = self.store.get_memory_document(memory_id)
        return doc is not None and doc.project_id == project_id

    # Scaffold Initialization & High-Level Memory Loggers
    def initialize_project_memory(
        self,
        project_id: str,
        project_name: str,
        description: str = "",
    ) -> dict[str, MemoryDocument]:
        """
        Initialize persistent memory scaffold in the project workspace:
        - .autonomos/memory/project-map.md
        - .autonomos/memory/architecture.md
        - .autonomos/memory/current-state.md
        """
        with self._lock:
            # 1. project-map.md
            map_content = f"""# Project Map: {project_name}

## Purpose
{description or 'Autonomous AI workforce project repository.'}

## Major Components
- **Core Runtime**: Deterministic state engine, registries, and worker host.
- **Memory Subsystem**: Persistent Markdown documentation and structured indexing.
- **Event Bus**: Append-only historical fact log.

## Important Locations
- `.autonomos/`: Managed project memory, decision logs, and runtime state.
- `core/`: Core workforce runtime implementation.
- `pkg/sdk/`: Universal worker contracts and execution interfaces.
- `workers/`: Workforce employee implementations.

## Current Development Areas
- Core runtime foundation and persistent project memory.
"""
            project_map = self.create_memory(
                project_id=project_id,
                memory_type=MemoryType.PROJECT_MAP,
                title=f"Project Map — {project_name}",
                relative_path=".autonomos/memory/project-map.md",
                content=map_content,
                summary=f"High-level component and location map for {project_name}",
                tags=["navigation", "map", "overview"],
            )

            # 2. architecture.md
            arch_content = f"""# Architecture & Invariants: {project_name}

## System Overview
The system operates as a deterministic runtime where AI models are reasoning engines rather than the system itself.

## Core Architectural Invariants
1. **The LLM is not the entire system**: Reasoning, state, memory, tools, and execution are decoupled.
2. **Context is not memory**: Persistent memory is stored in versionable external Markdown and structured stores.
3. **AI Claims are untrusted**: Deterministic verification gates validate claims before completion.
4. **All work is reversible**: Checkpoints protect against unintended changes.
5. **State is owned by the runtime**: Workers cannot arbitrarily mutate task or system status.
"""
            arch_doc = self.create_memory(
                project_id=project_id,
                memory_type=MemoryType.ARCHITECTURE,
                title=f"Architecture & Invariants — {project_name}",
                relative_path=".autonomos/memory/architecture.md",
                content=arch_content,
                summary="Core architectural decisions, invariants, and component boundaries",
                tags=["architecture", "invariants", "constraints"],
            )

            # 3. current-state.md
            state_content = f"""# Current Project State: {project_name}

## Current Milestone
Stage 3 — Persistent Project Memory & Knowledge Architecture

## Completed Capabilities
- Deterministic Task State Machine and DAG Dependency Resolver.
- Worker Lifecycle and Worker SDK Contract.
- Append-Only Event Store with Causation and Correlation Tracking.
- Markdown-first persistent project memory with atomic writes.

## Active Work
- Persistent project memory integration and reference verification.

## Known Limitations
- Context Engine and LLM routing are scheduled for subsequent stages.
"""
            current_state = self.create_memory(
                project_id=project_id,
                memory_type=MemoryType.CURRENT_STATE,
                title=f"Current State — {project_name}",
                relative_path=".autonomos/memory/current-state.md",
                content=state_content,
                summary="Active milestones, completed capabilities, and known limitations",
                tags=["status", "milestones", "state"],
            )

            return {
                "project_map": project_map,
                "architecture": arch_doc,
                "current_state": current_state,
            }

    def record_decision(
        self,
        project_id: str,
        title: str,
        context: str,
        decision: str,
        reasoning: str,
        consequences: str,
        decision_number: int = 1,
        status: str = "Accepted",
        references: Optional[list[str]] = None,
    ) -> MemoryDocument:
        """Record an Architectural Decision Record (ADR) in .autonomos/decisions/."""
        slug = title.lower().replace(" ", "-").replace("/", "-")[:40]
        rel_path = f".autonomos/decisions/{decision_number:04d}-{slug}.md"

        content = f"""# Decision {decision_number:04d}: {title}

**Status**: {status}  
**Date**: {utc_now()}

## Context
{context}

## Decision
{decision}

## Reasoning
{reasoning}

## Consequences
{consequences}
"""
        return self.create_memory(
            project_id=project_id,
            memory_type=MemoryType.DECISION,
            title=f"ADR {decision_number:04d}: {title}",
            relative_path=rel_path,
            content=content,
            summary=f"Decision: {title} ({status})",
            tags=["adr", "decision", "architecture"],
            references=references or [],
        )

    def record_report(
        self,
        project_id: str,
        task_id: str,
        worker_id: str,
        title: str,
        summary: str,
        markdown_body: str,
        report_type: str = "execution",
    ) -> MemoryDocument:
        """Record a structured worker report in .autonomos/reports/<task_id>/."""
        rel_path = f".autonomos/reports/{task_id}/{report_type}.md"
        content = f"""# {title}

- **Task ID**: `{task_id}`
- **Worker**: `{worker_id}`
- **Report Type**: `{report_type}`
- **Timestamp**: `{utc_now()}`

## Summary
{summary}

## Report Details
{markdown_body}
"""
        return self.create_memory(
            project_id=project_id,
            memory_type=MemoryType.REPORT,
            title=title,
            relative_path=rel_path,
            content=content,
            summary=summary,
            tags=["report", report_type, task_id],
            related_task_id=task_id,
            related_worker_id=worker_id,
        )

    def record_task_memory(
        self,
        project_id: str,
        task_id: str,
        title: str,
        objective: str,
        scope: str = "",
        constraints: str = "",
        relevant_files: Optional[list[str]] = None,
        findings: str = "",
        outcome: str = "",
    ) -> MemoryDocument:
        """Record human-readable persistent task memory in .autonomos/tasks/."""
        rel_path = f".autonomos/tasks/{task_id}.md"
        files_list = "\n".join(f"- `{f}`" for f in (relevant_files or [])) or "None specified."

        content = f"""# Task Memory: {title}

- **Task ID**: `{task_id}`
- **Recorded At**: `{utc_now()}`

## Objective
{objective}

## Scope & Constraints
- **Scope**: {scope or 'Standard task scope'}
- **Constraints**: {constraints or 'Standard sandbox limits'}

## Relevant Repository Files
{files_list}

## Findings & Progress
{findings or 'Execution completed normally.'}

## Final Outcome
{outcome or 'Task completed.'}
"""
        return self.create_memory(
            project_id=project_id,
            memory_type=MemoryType.TASK_MEMORY,
            title=f"Task Memory: {title}",
            relative_path=rel_path,
            content=content,
            summary=f"Task knowledge memory for {title}",
            tags=["task_memory", task_id],
            references=relevant_files or [],
            related_task_id=task_id,
        )

    def record_issue(
        self,
        project_id: str,
        title: str,
        description: str,
        severity: IssueSeverity = IssueSeverity.MEDIUM,
        status: IssueStatus = IssueStatus.OPEN,
        affected_area: str = "",
        related_task_id: Optional[str] = None,
        issue_id: Optional[str] = None,
    ) -> MemoryDocument:
        """Record a persistent issue/limitation in .autonomos/issues/."""
        iid = issue_id or f"issue-{uuid.uuid4().hex[:8]}"
        rel_path = f".autonomos/issues/{iid}.md"

        content = f"""# Issue: {title}

- **ID**: `{iid}`
- **Severity**: `{severity.value}`
- **Status**: `{status.value}`
- **Affected Area**: `{affected_area or 'General'}`
- **Related Task**: `{related_task_id or 'None'}`
- **Discovered**: `{utc_now()}`

## Description
{description}
"""
        return self.create_memory(
            project_id=project_id,
            memory_type=MemoryType.ISSUE,
            title=f"Issue: {title}",
            relative_path=rel_path,
            content=content,
            summary=title,
            tags=["issue", severity.value.lower(), status.value.lower()],
            related_task_id=related_task_id,
            memory_id=iid,
        )

    def update_current_state(
        self,
        project_id: str,
        stage: str,
        completed_milestones: list[str],
        active_work: list[str],
        known_limitations: list[str],
        notes: str = "",
    ) -> MemoryDocument:
        """Update .autonomos/memory/current-state.md with latest milestones and active work."""
        rel_path = ".autonomos/memory/current-state.md"
        doc = self.read_memory_by_path(project_id, rel_path)

        milestones_md = "\n".join(f"- {m}" for m in completed_milestones) or "- None"
        active_md = "\n".join(f"- {w}" for w in active_work) or "- None"
        limits_md = "\n".join(f"- {l}" for l in known_limitations) or "- None"

        new_content = f"""# Current Project State

## Current Stage
{stage}

## Completed Milestones
{milestones_md}

## Active Work
{active_md}

## Known Limitations
{limits_md}

## Notes
{notes or 'All subsystems operating deterministically.'}

*Last Updated: {utc_now()}*
"""
        return self.update_memory(
            project_id=project_id,
            memory_id=doc.id,
            content=new_content,
            summary=f"Stage: {stage} | Milestones: {len(completed_milestones)}",
        )

    # Reference Validation & Stale Memory Auditing
    def validate_memory_references(self, project_id: str, memory_id: str) -> ValidationReport:
        """
        Deterministically validate that all references inside a memory document actually exist:
        - Referenced file paths exist on the project filesystem.
        - Referenced task IDs exist in the Task Engine.
        - Referenced worker IDs exist in the Worker Registry.
        """
        doc = self.read_memory(project_id, memory_id)
        root = self._get_project_root(project_id)

        broken_files: list[str] = []
        broken_tasks: list[str] = []
        broken_workers: list[str] = []
        warnings: list[str] = []

        # 1. Check referenced repository files
        for ref_path in doc.references:
            clean_path = ref_path.strip().lstrip("/\\")
            full_file = root / clean_path
            if not full_file.exists():
                broken_files.append(clean_path)

        # 2. Check related task ID
        if doc.related_task_id:
            task = self.store.get_task(doc.related_task_id)
            if not task:
                broken_tasks.append(doc.related_task_id)

        # 3. Check related worker ID
        if doc.related_worker_id:
            worker = self.store.get_worker(doc.related_worker_id)
            if not worker:
                broken_workers.append(doc.related_worker_id)

        is_valid = (len(broken_files) + len(broken_tasks) + len(broken_workers)) == 0

        return ValidationReport(
            memory_id=doc.id,
            relative_path=doc.relative_path,
            is_valid=is_valid,
            broken_file_references=broken_files,
            broken_task_references=broken_tasks,
            broken_worker_references=broken_workers,
            warnings=warnings,
        )

    def audit_project_memory(self, project_id: str) -> list[ValidationReport]:
        """Audit all memory documents in a project for stale references."""
        docs = self.list_memory(project_id)
        return [self.validate_memory_references(project_id, d.id) for d in docs]
