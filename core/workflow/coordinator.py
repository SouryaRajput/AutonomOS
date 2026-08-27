from __future__ import annotations

import threading
import time
from typing import TYPE_CHECKING, Any, Callable, Optional
import uuid

from core.enums import ArtifactType, TaskStatus
from core.events.types import EventSource, EventType
from core.errors import ValidationError
from core.models import Task
from core.workflow.handoff import HandoffManager
from core.workflow.model import (
    DefectLinkage,
    ExecutionAttempt,
    WorkerHandoff,
    WorkflowBudget,
    WorkflowSnapshot,
    WorkforceWorkflow,
    utc_now,
)
from core.workflow.summary import WorkflowSummaryGenerator
from core.workflow.types import (
    ApprovalStatus,
    HandoffType,
    ReassignmentReason,
    WorkflowPriority,
    WorkflowStatus,
)
from pkg.sdk.types import WorkerCapability
from pkg.sdk.worker import WorkerOutput

if TYPE_CHECKING:
    from core.runtime.workforce_runtime import WorkforceRuntime


class WorkflowCoordinator:
    """
    Central Workforce Collaboration & Workflow Coordination Engine for AutonomOS.
    Manages multi-worker execution graphs, handoffs, defect loops, iteration budgets,
    attempt histories, and crash restart recovery.
    """

    def __init__(self, runtime: WorkforceRuntime):
        self.runtime = runtime
        self._lock = threading.RLock()
        self._handoffs: dict[str, list[WorkerHandoff]] = {}  # workflow_id -> handoffs
        self._attempts: dict[str, list[ExecutionAttempt]] = {}  # task_id -> attempts
        self._defect_links: dict[str, list[DefectLinkage]] = {}  # workflow_id -> defect links

    def create_workflow(
        self,
        project_id: str,
        title: str,
        objective: str,
        root_task_id: Optional[str] = None,
        priority: WorkflowPriority = WorkflowPriority.NORMAL,
        budget: Optional[WorkflowBudget] = None,
        metadata: Optional[dict[str, Any]] = None,
    ) -> WorkforceWorkflow:
        with self._lock:
            # Validate project exists
            project = self.runtime.projects.get_project(project_id)
            if not project:
                raise ValidationError(f"Project '{project_id}' not found.")

            workflow_id = f"wf-{uuid.uuid4().hex[:8]}"
            workflow = WorkforceWorkflow(
                id=workflow_id,
                project_id=project_id,
                title=title,
                objective=objective,
                root_task_id=root_task_id,
                status=WorkflowStatus.CREATED,
                priority=priority,
                budget=budget or WorkflowBudget(),
                metadata=dict(metadata or {}),
            )

            # Persist workflow in store
            if hasattr(self.runtime.store, "save_workflow"):
                self.runtime.store.save_workflow(workflow)

            self.runtime.log_event(
                event_type=EventType.WORKFLOW_CREATED,
                payload={
                    "workflow_id": workflow.id,
                    "title": workflow.title,
                    "objective": workflow.objective,
                    "priority": workflow.priority.value,
                },
                project_id=project_id,
                source=EventSource.RUNTIME,
            )
            return workflow

    def get_workflow(self, workflow_id: str) -> Optional[WorkforceWorkflow]:
        with self._lock:
            if hasattr(self.runtime.store, "get_workflow"):
                return self.runtime.store.get_workflow(workflow_id)
            return None

    def list_workflows(self, project_id: Optional[str] = None) -> list[WorkforceWorkflow]:
        with self._lock:
            if hasattr(self.runtime.store, "list_workflows"):
                return self.runtime.store.list_workflows(project_id=project_id)
            return []

    def update_workflow_status(
        self,
        workflow_id: str,
        new_status: WorkflowStatus,
        reason: str = "",
    ) -> WorkforceWorkflow:
        with self._lock:
            wf = self.get_workflow(workflow_id)
            if not wf:
                raise ValidationError(f"Workflow '{workflow_id}' not found.")

            old_status = wf.status
            wf.status = new_status
            wf.updated_at = utc_now()
            if new_status in (WorkflowStatus.COMPLETED, WorkflowStatus.FAILED, WorkflowStatus.CANCELLED):
                wf.completed_at = utc_now()

            if hasattr(self.runtime.store, "save_workflow"):
                self.runtime.store.save_workflow(wf)

            self.runtime.log_event(
                event_type=EventType.WORKFLOW_STATUS_CHANGED,
                payload={
                    "workflow_id": wf.id,
                    "old_status": old_status.value,
                    "new_status": new_status.value,
                    "reason": reason,
                },
                project_id=wf.project_id,
                source=EventSource.RUNTIME,
            )
            return wf

    def add_task_to_workflow(self, workflow_id: str, task_id: str) -> None:
        with self._lock:
            wf = self.get_workflow(workflow_id)
            if not wf:
                raise ValidationError(f"Workflow '{workflow_id}' not found.")

            if task_id not in wf.tasks:
                wf.tasks.append(task_id)
                wf.updated_at = utc_now()
                if hasattr(self.runtime.store, "save_workflow"):
                    self.runtime.store.save_workflow(wf)

    def record_execution_attempt(
        self,
        task_id: str,
        worker_id: str,
        status: str,
        worker_version: str = "1.0.0",
        error_message: Optional[str] = None,
        duration_ms: float = 0.0,
        workflow_id: Optional[str] = None,
        metadata: Optional[dict[str, Any]] = None,
    ) -> ExecutionAttempt:
        with self._lock:
            attempts_list = self._attempts.setdefault(task_id, [])
            attempt_num = len(attempts_list) + 1

            attempt = ExecutionAttempt(
                id=f"att-{uuid.uuid4().hex[:8]}",
                task_id=task_id,
                attempt_number=attempt_num,
                worker_id=worker_id,
                worker_version=worker_version,
                status=status,
                workflow_id=workflow_id,
                error_message=error_message,
                duration_ms=duration_ms,
                completed_at=utc_now(),
                metadata=dict(metadata or {}),
            )
            attempts_list.append(attempt)

            if hasattr(self.runtime.store, "save_execution_attempt"):
                self.runtime.store.save_execution_attempt(attempt)

            return attempt

    def list_attempts(self, task_id: str) -> list[ExecutionAttempt]:
        with self._lock:
            if hasattr(self.runtime.store, "list_execution_attempts"):
                persisted = self.runtime.store.list_execution_attempts(task_id)
                if persisted:
                    return persisted
            return self._attempts.get(task_id, [])

    def record_handoff(
        self,
        project_id: str,
        source_worker: str,
        source_task: Task,
        output: WorkerOutput,
        destination_worker: Optional[str] = None,
        destination_task_id: Optional[str] = None,
        handoff_type: HandoffType = HandoffType.GENERAL,
        workflow_id: Optional[str] = None,
    ) -> WorkerHandoff:
        with self._lock:
            handoff = HandoffManager.create_handoff(
                project_id=project_id,
                source_worker=source_worker,
                source_task=source_task,
                output=output,
                destination_worker=destination_worker,
                destination_task_id=destination_task_id,
                handoff_type=handoff_type,
                workflow_id=workflow_id,
            )
            HandoffManager.validate_handoff(handoff, target_project_id=project_id)

            if workflow_id:
                self._handoffs.setdefault(workflow_id, []).append(handoff)

            if hasattr(self.runtime.store, "save_worker_handoff"):
                self.runtime.store.save_worker_handoff(handoff)

            self.runtime.log_event(
                event_type=EventType.WORKFLOW_HANDOFF_EXECUTED,
                payload={
                    "handoff_id": handoff.id,
                    "workflow_id": workflow_id,
                    "source_worker": source_worker,
                    "destination_worker": destination_worker,
                    "source_task_id": source_task.id,
                    "destination_task_id": destination_task_id,
                    "handoff_type": handoff_type.value,
                    "artifacts": handoff.artifacts,
                    "evidence": handoff.evidence,
                    "summary": handoff.summary,
                },
                project_id=project_id,
                task_id=source_task.id,
                source=EventSource.RUNTIME,
            )
            return handoff

    def list_handoffs(self, workflow_id: str) -> list[WorkerHandoff]:
        with self._lock:
            if hasattr(self.runtime.store, "list_worker_handoffs"):
                persisted = self.runtime.store.list_worker_handoffs(workflow_id)
                if persisted:
                    return persisted
            return self._handoffs.get(workflow_id, [])

    def record_defect_link(
        self,
        workflow_id: str,
        defect_id: str,
        originating_task_id: str,
        originating_worker_id: str,
        fix_task_id: Optional[str] = None,
        fix_worker_id: Optional[str] = None,
        retest_task_id: Optional[str] = None,
        retest_worker_id: Optional[str] = None,
    ) -> DefectLinkage:
        with self._lock:
            link = DefectLinkage(
                defect_id=defect_id,
                originating_task_id=originating_task_id,
                originating_worker_id=originating_worker_id,
                fix_task_id=fix_task_id,
                fix_worker_id=fix_worker_id,
                retest_task_id=retest_task_id,
                retest_worker_id=retest_worker_id,
            )
            self._defect_links.setdefault(workflow_id, []).append(link)
            return link

    def match_worker_by_capability(self, required_capabilities: set[WorkerCapability]) -> Optional[str]:
        """Deterministically match the best registered worker based on declared capabilities."""
        registered_workers = self.runtime.workers.list_workers()
        best_worker_id: Optional[str] = None
        max_matches = -1

        for w in registered_workers:
            w_caps = {WorkerCapability(c) for c in w.capabilities if isinstance(c, str) or isinstance(c, WorkerCapability)}
            matching = len(w_caps.intersection(required_capabilities))
            if matching > max_matches and required_capabilities.issubset(w_caps):
                max_matches = matching
                best_worker_id = w.id

        return best_worker_id

    def get_workflow_snapshot(self, workflow_id: str) -> Optional[WorkflowSnapshot]:
        with self._lock:
            wf = self.get_workflow(workflow_id)
            if not wf:
                return None

            active_tasks: list[str] = []
            completed_tasks: list[str] = []
            blocked_tasks: list[str] = []
            failed_tasks: list[str] = []
            worker_assignments: dict[str, str] = {}

            for tid in wf.tasks:
                task = self.runtime.tasks.get_task(tid)
                if task:
                    if task.assigned_worker:
                        worker_assignments[task.id] = task.assigned_worker
                    if task.status == TaskStatus.COMPLETED:
                        completed_tasks.append(task.id)
                    elif task.status == TaskStatus.BLOCKED:
                        blocked_tasks.append(task.id)
                    elif task.status == TaskStatus.FAILED:
                        failed_tasks.append(task.id)
                    elif task.status in (TaskStatus.PENDING, TaskStatus.RUNNING):
                        active_tasks.append(task.id)

            recent_handoffs = self.list_handoffs(workflow_id)
            open_defects = [d for d in self._defect_links.get(workflow_id, []) if d.status != "VERIFIED"]

            return WorkflowSnapshot(
                workflow_id=wf.id,
                project_id=wf.project_id,
                title=wf.title,
                status=wf.status,
                active_tasks=active_tasks,
                completed_tasks=completed_tasks,
                blocked_tasks=blocked_tasks,
                failed_tasks=failed_tasks,
                worker_assignments=worker_assignments,
                recent_handoffs=recent_handoffs,
                open_defects=open_defects,
                iteration_count=wf.current_step,
            )

    def generate_and_save_workflow_summary(self, workflow_id: str) -> Optional[str]:
        with self._lock:
            wf = self.get_workflow(workflow_id)
            if not wf:
                return None

            snapshot = self.get_workflow_snapshot(workflow_id)
            if not snapshot:
                return None

            summary_md = WorkflowSummaryGenerator.generate_markdown_summary(wf, snapshot)
            rel_path = f"workflows/{workflow_id}/summary.md"

            art = self.runtime.artifacts.register_artifact(
                project_id=wf.project_id,
                artifact_type=ArtifactType.REPORT,
                relative_path=rel_path,
                description=f"Workflow Summary for '{wf.title}'",
                content=summary_md,
                base_dir=self.runtime.projects.get_project(wf.project_id).root_path,
                metadata={"workflow_id": workflow_id, "status": wf.status.value},
            )
            return rel_path

    def pause_workflow(self, workflow_id: str, reason: str = "User requested pause") -> WorkforceWorkflow:
        return self.update_workflow_status(workflow_id, WorkflowStatus.PAUSED, reason=reason)

    def resume_workflow(self, workflow_id: str, reason: str = "User requested resume") -> WorkforceWorkflow:
        return self.update_workflow_status(workflow_id, WorkflowStatus.RUNNING, reason=reason)

    def cancel_workflow(self, workflow_id: str, reason: str = "User requested cancellation") -> WorkforceWorkflow:
        return self.update_workflow_status(workflow_id, WorkflowStatus.CANCELLED, reason=reason)
