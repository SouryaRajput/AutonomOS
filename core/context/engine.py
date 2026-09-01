import json
import os
from pathlib import Path
import threading
from typing import Any, Optional

from core.context.model import ContextBudget, ContextItem, ContextPackage, ContextRequest, ContextWarning, estimate_tokens
from core.context.scoring import RelevanceScorer
from core.context.types import ContextPriority, ContextSourceType, ContextWarningType
from core.enums import MemoryType, TaskStatus
from core.errors import ProjectNotFoundError, TaskNotFoundError
from core.memory.manager import MemoryManager
from core.models import Task
from core.storage.base import Store


class ContextEngine:
    """
    Deterministic Context Engine for AutonomOS.
    Assembles bounded, prioritized, and explainable context packages for AI workers
    from Persistent Project Memory, Task Engine, Repository files, Artifacts, and Events.
    """

    def __init__(self, store: Store, memory_manager: MemoryManager):
        self.store = store
        self.memory = memory_manager
        self._lock = threading.RLock()

    def assemble_context(self, request: ContextRequest) -> ContextPackage:
        """
        Execute the deterministic context assembly pipeline:
        1. Retrieve authoritative task & project state.
        2. Discover candidates across all information sources.
        3. Score candidates with explainable relevance algorithm.
        4. Deduplicate overlapping content.
        5. Enforce context budget limits & safe truncation.
        6. Assemble final deterministic ContextPackage.
        """
        with self._lock:
            # 1. Validate Project & Task
            project = self.store.get_project(request.project_id)
            if not project:
                raise ProjectNotFoundError(request.project_id)

            task = self.store.get_task(request.task_id)
            if not task:
                if request.task_id.startswith("mgr-state-") or request.task_id.startswith("project-ctx-"):
                    task = Task(
                        id=request.task_id,
                        project_id=project.id,
                        title=f"Manager Orchestration: {project.name}",
                        objective="Orchestrating autonomous workforce planning and task execution",
                    )
                else:
                    raise TaskNotFoundError(request.task_id)

            # 2. Discover Candidates
            candidates, discovery_warnings = self._discover_candidates(request, task, project.root_path)

            # 3. Score Candidates
            explicit_refs = self._extract_explicit_references(task, request)
            scored_items = [
                RelevanceScorer.score_item(
                    item=item,
                    task=task,
                    request=request,
                    explicit_references=explicit_refs,
                )
                for item in candidates
            ]

            # Filter out excluded source types
            if request.excluded_sources:
                scored_items = [it for it in scored_items if it.source_type not in request.excluded_sources]

            # 4. Deduplicate
            deduped_items, dedup_warnings = self._deduplicate_items(scored_items)

            # 5. Sort according to authoritative context ordering
            sorted_items = self._sort_context_items(deduped_items)

            # 6. Budget Allocation & Truncation
            selected_items, budget_warnings = self._allocate_budget(sorted_items, request.budget)

            all_warnings = discovery_warnings + dedup_warnings + budget_warnings

            total_tokens = sum(it.token_estimate for it in selected_items)
            total_chars = sum(it.character_count for it in selected_items)

            package = ContextPackage(
                request_id=request.request_id,
                project_id=request.project_id,
                task_id=request.task_id,
                worker_id=request.worker_id,
                items=selected_items,
                total_estimated_tokens=total_tokens,
                total_characters=total_chars,
                budget=request.budget,
                warnings=all_warnings,
                candidate_count=len(candidates),
                selected_count=len(selected_items),
                metadata=request.metadata,
            )

            # Save debug snapshot
            try:
                self.save_context_snapshot(package, project.root_path)
            except Exception:
                pass  # Non-fatal if snapshot directory unwritable in isolated tests

            return package

    def _extract_explicit_references(self, task: Task, request: ContextRequest) -> set[str]:
        refs: set[str] = set()
        for r in task.context_references:
            if isinstance(r, dict):
                ref_path = r.get("path") or r.get("target") or r.get("file")
                if ref_path:
                    refs.add(str(ref_path).strip().lstrip("/\\"))
            elif isinstance(r, str):
                refs.add(r.strip().lstrip("/\\"))

        for dep_id in task.dependencies:
            refs.add(dep_id.strip())

        for fa in request.focus_areas:
            refs.add(fa.strip().lstrip("/\\"))

        return refs

    def _discover_candidates(
        self,
        request: ContextRequest,
        task: Task,
        project_root: str,
    ) -> tuple[list[ContextItem], list[ContextWarning]]:
        candidates: list[ContextItem] = []
        warnings: list[ContextWarning] = []
        root_path = Path(project_root).resolve()

        # (A) Active Task Objective (Mandatory)
        criteria_md = "\n".join(
            f"- [ ] {c.get('description', c)}" if isinstance(c, dict) else f"- [ ] {c}"
            for c in task.success_criteria
        ) or "None specified."

        deps_md = "\n".join(f"- `{d}`" for d in task.dependencies) or "No prerequisite dependencies."

        task_content = f"""# Task: {task.title}

- **Task ID**: `{task.id}`
- **Status**: `{task.status.value}`
- **Priority**: `{task.priority}` | **Risk**: `{task.risk.value}`
- **Attempts**: {task.attempts}/{task.max_attempts}

## Objective
{task.objective or 'No objective specified.'}

## Success Criteria
{criteria_md}

## Dependencies
{deps_md}
"""
        task_item = ContextItem(
            id=f"ctx-task-{task.id}",
            source_type=ContextSourceType.TASK_OBJECTIVE,
            source_id=task.id,
            title=f"Task Objective: {task.title}",
            content=task_content,
            priority=ContextPriority.MANDATORY,
            is_required=True,
            metadata={"task_id": task.id, "status": task.status.value},
        )
        candidates.append(task_item)

        # (B) Project Memory: Project Map, Architecture, Current State
        memory_docs = self.store.list_memory_documents(project_id=request.project_id)
        for doc in memory_docs:
            source_type = ContextSourceType.PROJECT_MAP
            if doc.memory_type == MemoryType.ARCHITECTURE:
                source_type = ContextSourceType.ARCHITECTURE
            elif doc.memory_type == MemoryType.CURRENT_STATE:
                source_type = ContextSourceType.CURRENT_STATE
            elif doc.memory_type == MemoryType.DECISION:
                source_type = ContextSourceType.DECISION
            elif doc.memory_type == MemoryType.TASK_MEMORY:
                source_type = ContextSourceType.TASK_MEMORY
            elif doc.memory_type == MemoryType.REPORT:
                source_type = ContextSourceType.REPORT
            elif doc.memory_type == MemoryType.ISSUE:
                source_type = ContextSourceType.ISSUE

            # Source-of-Truth Discrepancy Check
            if doc.memory_type == MemoryType.TASK_MEMORY and doc.related_task_id == task.id:
                if f"Status: COMPLETED" in doc.content and task.status != TaskStatus.COMPLETED:
                    warnings.append(
                        ContextWarning(
                            warning_type=ContextWarningType.CONTRADICTORY_MEMORY,
                            message=f"Task memory '{doc.relative_path}' reports COMPLETED but authoritative Task Engine state is '{task.status.value}'. Authoritative state takes precedence.",
                            target_id=doc.id,
                        )
                    )

            item = ContextItem(
                id=f"ctx-mem-{doc.id}",
                source_type=source_type,
                source_id=doc.id,
                title=doc.title,
                content=doc.content,
                metadata={
                    "relative_path": doc.relative_path,
                    "tags": doc.tags,
                    "task_id": doc.related_task_id,
                    "worker_id": doc.related_worker_id,
                    "version": doc.version,
                    "updated_at": doc.updated_at,
                },
            )
            candidates.append(item)

        # (C) Explicit Repository Source Code Files
        for ref_dict in task.context_references:
            ref_path = ref_dict.get("path") if isinstance(ref_dict, dict) else ref_dict
            if not ref_path:
                continue
            clean_path = str(ref_path).strip().lstrip("/\\")
            # If not in .autonomos memory, check repo file
            if not clean_path.startswith(".autonomos"):
                full_file = root_path / clean_path
                if full_file.exists() and full_file.is_file():
                    try:
                        file_content = full_file.read_text(encoding="utf-8")
                        is_ref_only = len(file_content) > 6000  # Convert very large files to reference pointers
                        if is_ref_only:
                            head_snippet = "\n".join(file_content.splitlines()[:60])
                            snippet = f"""# File: {clean_path} (Truncated / Reference Pointer)
```
{head_snippet}
... [Full file size: {len(file_content)} characters. Use workspace tools to inspect remaining lines] ...
```"""
                        else:
                            snippet = f"""# File: {clean_path}
```
{file_content}
```"""

                        file_item = ContextItem(
                            id=f"ctx-file-{clean_path.replace('/', '-')}",
                            source_type=ContextSourceType.REPOSITORY_FILE,
                            source_id=clean_path,
                            title=f"Source Code: {clean_path}",
                            content=snippet,
                            is_reference_only=is_ref_only,
                            priority=ContextPriority.HIGH,
                            metadata={"relative_path": clean_path, "file_size": len(file_content)},
                        )
                        candidates.append(file_item)
                    except Exception as e:
                        warnings.append(
                            ContextWarning(
                                warning_type=ContextWarningType.MISSING_FILE,
                                message=f"Failed to read referenced file '{clean_path}': {str(e)}",
                                target_id=clean_path,
                            )
                        )
                else:
                    warnings.append(
                        ContextWarning(
                            warning_type=ContextWarningType.MISSING_FILE,
                            message=f"Referenced repository file '{clean_path}' not found on disk.",
                            target_id=clean_path,
                        )
                    )

        # (D) Produced Artifact Metadata
        artifacts = self.store.list_artifacts_for_project(request.project_id)
        for art in artifacts:
            # Include artifacts from task or prerequisites
            if art.task_id == task.id or art.task_id in task.dependencies:
                art_item = ContextItem(
                    id=f"ctx-art-{art.id}",
                    source_type=ContextSourceType.ARTIFACT_METADATA,
                    source_id=art.id,
                    title=f"Artifact: {art.path}",
                    content=f"Artifact `{art.path}` ({art.type.value}): {art.description}\nChecksum: `{art.checksum or 'N/A'}`",
                    metadata={"task_id": art.task_id, "artifact_id": art.id, "relative_path": art.path},
                )
                candidates.append(art_item)

        # (E) Recent Audit Events (Timeline)
        task_events = self.store.get_events_by_task(task.id)
        if task_events:
            events_summary = "\n".join(
                f"- `[{e.timestamp}]` #{e.sequence_number:04d} **{e.event_type.value}** ({e.source.value})"
                for e in task_events[-10:]
            )
            event_item = ContextItem(
                id=f"ctx-events-{task.id}",
                source_type=ContextSourceType.RECENT_EVENTS,
                source_id=f"events-{task.id}",
                title=f"Recent Event Timeline for Task {task.id}",
                content=f"# Recent Event Timeline\n{events_summary}",
                metadata={"task_id": task.id, "event_count": len(task_events)},
            )
            candidates.append(event_item)

        return candidates, warnings

    def _deduplicate_items(
        self,
        items: list[ContextItem],
    ) -> tuple[list[ContextItem], list[ContextWarning]]:
        deduped: list[ContextItem] = []
        seen_checksums: dict[str, ContextItem] = {}
        seen_source_keys: dict[tuple[ContextSourceType, str], ContextItem] = {}
        warnings: list[ContextWarning] = []

        for item in items:
            source_key = (item.source_type, item.source_id)

            # 1. Exact Source Key Match
            if source_key in seen_source_keys:
                existing = seen_source_keys[source_key]
                warnings.append(
                    ContextWarning(
                        warning_type=ContextWarningType.DUPLICATE_REMOVED,
                        message=f"Duplicate source item '{item.title}' removed in favor of higher priority candidate.",
                        target_id=item.id,
                    )
                )
                if item.relevance_score > existing.relevance_score:
                    # Replace with higher scoring
                    deduped.remove(existing)
                    deduped.append(item)
                    seen_source_keys[source_key] = item
                continue

            # 2. Exact Checksum Match
            if item.checksum and item.checksum in seen_checksums:
                warnings.append(
                    ContextWarning(
                        warning_type=ContextWarningType.DUPLICATE_REMOVED,
                        message=f"Duplicate content checksum for '{item.title}' removed.",
                        target_id=item.id,
                    )
                )
                continue

            seen_source_keys[source_key] = item
            if item.checksum:
                seen_checksums[item.checksum] = item
            deduped.append(item)

        return deduped, warnings

    def _sort_context_items(self, items: list[ContextItem]) -> list[ContextItem]:
        """
        Sort items deterministically using:
        1. Mandatory / Required flag (Mandatory first).
        2. Relevance score (Descending).
        3. Canonical Source Order:
           TASK_OBJECTIVE -> EXPLICIT_REFERENCE -> ARCHITECTURE -> PROJECT_MAP ->
           CURRENT_STATE -> DECISION -> REPORT -> TASK_MEMORY -> REPOSITORY_FILE -> ARTIFACT_METADATA -> RECENT_EVENTS
        4. Stable ID tie-breaker for reproducibility.
        """
        source_order = {
            ContextSourceType.TASK_OBJECTIVE: 1,
            ContextSourceType.EXPLICIT_REFERENCE: 2,
            ContextSourceType.ARCHITECTURE: 3,
            ContextSourceType.PROJECT_MAP: 4,
            ContextSourceType.CURRENT_STATE: 5,
            ContextSourceType.DECISION: 6,
            ContextSourceType.REPORT: 7,
            ContextSourceType.TASK_MEMORY: 8,
            ContextSourceType.REPOSITORY_FILE: 9,
            ContextSourceType.ARTIFACT_METADATA: 10,
            ContextSourceType.RECENT_EVENTS: 11,
            ContextSourceType.ISSUE: 12,
        }

        return sorted(
            items,
            key=lambda it: (
                0 if it.is_required else 1,
                -round(it.relevance_score, 4),
                source_order.get(it.source_type, 99),
                it.id,
            ),
        )

    def _allocate_budget(
        self,
        sorted_items: list[ContextItem],
        budget: ContextBudget,
    ) -> tuple[list[ContextItem], list[ContextWarning]]:
        selected: list[ContextItem] = []
        warnings: list[ContextWarning] = []

        current_tokens = 0
        current_chars = 0

        # Step 1: Add all mandatory/required items unconditionally
        mandatory_items = [it for it in sorted_items if it.is_required]
        optional_items = [it for it in sorted_items if not it.is_required]

        for m_item in mandatory_items:
            selected.append(m_item)
            current_tokens += m_item.token_estimate
            current_chars += m_item.character_count

        if current_tokens > budget.max_tokens:
            warnings.append(
                ContextWarning(
                    warning_type=ContextWarningType.BUDGET_EXCEEDED,
                    message=f"Mandatory task context ({current_tokens} tokens) exceeds total budget ({budget.max_tokens} tokens). Protected mandatory items retained.",
                    details={"current_tokens": current_tokens, "max_tokens": budget.max_tokens},
                )
            )

        # Step 2: Add optional items in ranked order while within remaining budget
        for opt_item in optional_items:
            if len(selected) >= budget.max_items:
                break

            remaining_tokens = budget.max_tokens - current_tokens
            remaining_chars = budget.max_characters - current_chars

            if remaining_tokens <= 50:
                break  # Budget fully consumed

            if opt_item.token_estimate <= remaining_tokens and opt_item.character_count <= remaining_chars:
                selected.append(opt_item)
                current_tokens += opt_item.token_estimate
                current_chars += opt_item.character_count
            else:
                # Truncate large files or reports safely if remaining budget allows meaningful content
                if opt_item.source_type in (ContextSourceType.REPOSITORY_FILE, ContextSourceType.REPORT) and remaining_tokens >= 150:
                    truncated_char_limit = remaining_chars - 200
                    if truncated_char_limit >= 400:
                        truncated_content = opt_item.content[:truncated_char_limit] + "\n\n[... Truncated due to context budget limits ...]"
                        opt_item.content = truncated_content
                        opt_item.character_count = len(truncated_content)
                        opt_item.token_estimate = estimate_tokens(truncated_content)

                        selected.append(opt_item)
                        current_tokens += opt_item.token_estimate
                        current_chars += opt_item.character_count

                        warnings.append(
                            ContextWarning(
                                warning_type=ContextWarningType.TRUNCATED_ITEM,
                                message=f"Context item '{opt_item.title}' was safely truncated to fit remaining token budget.",
                                target_id=opt_item.id,
                                details={"original_size": opt_item.character_count, "budget_remaining": remaining_tokens},
                            )
                        )
                # Else omit optional item

        return selected, warnings

    def save_context_snapshot(self, package: ContextPackage, project_root: str) -> Path:
        """Save a human & machine readable snapshot of the context package on disk."""
        snapshot_dir = Path(project_root) / ".autonomos" / "context_snapshots"
        snapshot_dir.mkdir(parents=True, exist_ok=True)
        file_path = snapshot_dir / f"{package.request_id}.json"

        with open(file_path, "w", encoding="utf-8") as f:
            json.dump(package.to_dict(), f, indent=2)

        return file_path
