import logging
from pathlib import Path
import threading
from typing import Any, Callable, Optional

from core.enums import (
    ArtifactType,
    DependencyType,
    IssueSeverity,
    IssueStatus,
    MemoryType,
    ProjectStatus,
    RiskLevel,
    TaskStatus,
    WorkerStatus,
)
from core.errors import (
    ExecutionFailedError,
    TaskAlreadyCompletedError,
    TaskNotFoundError,
    WorkerNotEligibleError,
    WorkerNotFoundError,
)
from core.events.activity import ActivityItem, ActivityProjector, format_event_log_line
from core.events.model import Event, new_event_id, utc_now
from core.events.types import EventSource, EventType
from core.memory.manager import MemoryManager
from core.memory.model import MemoryDocument, ValidationReport
from core.models import (
    Artifact,
    Evidence,
    Project,
    Task,
    WorkerManifest,
    WorkerOutput,
)
from core.runtime.artifact_registry import ArtifactRegistry
from core.runtime.project_registry import ProjectRegistry
from core.runtime.task_engine import TaskEngine
from core.runtime.worker_registry import WorkerRegistry
from core.storage.base import Store
from core.storage.sqlite_store import SQLiteStore
from core.task.state_machine import TaskStateMachine
from core.worker.state_machine import WorkerStateMachine
from pkg.sdk.worker import Worker, WorkerRuntimeContext

logger = logging.getLogger("AutonomOS.Runtime")


class _DefaultWorkerRuntimeContext(WorkerRuntimeContext):
    """Internal runtime context provided to workers during execution."""

    def __init__(
        self,
        task: Task,
        project: Project,
        artifact_registry: ArtifactRegistry,
        worker_id: str,
        runtime: "WorkforceRuntime",
        causation_id: Optional[str] = None,
    ):
        self._task = task
        self._project = project
        self._artifact_registry = artifact_registry
        self._worker_id = worker_id
        self._runtime = runtime
        self._causation_id = causation_id
        self.evidence_records: list[Evidence] = []

    @property
    def task(self) -> Task:
        return self._task

    @property
    def project_id(self) -> str:
        return self._project.id

    def create_artifact(
        self,
        artifact_type: ArtifactType,
        relative_path: str,
        description: str,
        content: Optional[bytes | str] = None,
        metadata: Optional[dict[str, Any]] = None,
    ) -> Artifact:
        artifact = self._artifact_registry.register_artifact(
            project_id=self._project.id,
            task_id=self._task.id,
            worker_id=self._worker_id,
            artifact_type=artifact_type,
            relative_path=relative_path,
            description=description,
            content=content,
            base_dir=self._project.root_path,
            metadata=metadata,
        )
        # Emit authoritative ARTIFACT_CREATED event
        self._runtime.log_event(
            event_type=EventType.ARTIFACT_CREATED,
            payload={
                "artifact_id": artifact.id,
                "path": artifact.path,
                "type": artifact.type.value,
                "description": description,
                "checksum": artifact.checksum,
            },
            source=EventSource.RUNTIME,
            correlation_id=self._task.id,
            causation_id=self._causation_id,
            project_id=self._project.id,
            task_id=self._task.id,
            worker_id=self._worker_id,
            artifact_id=artifact.id,
        )
        return artifact

    def record_evidence(self, evidence_type: str, data: str) -> Evidence:
        ev = Evidence(
            id=new_event_id(),
            task_id=self._task.id,
            evidence_type=evidence_type,
            data=data,
            created_at=utc_now(),
        )
        self.evidence_records.append(ev)
        # Emit EVIDENCE_RECORDED event
        self._runtime.log_event(
            event_type=EventType.EVIDENCE_RECORDED,
            payload={"evidence_id": ev.id, "evidence_type": evidence_type, "data_snippet": data[:200]},
            source=EventSource.RUNTIME,
            correlation_id=self._task.id,
            causation_id=self._causation_id,
            project_id=self._project.id,
            task_id=self._task.id,
            worker_id=self._worker_id,
        )
        return ev

    def log_event(self, event_type: str, payload: dict[str, Any]) -> None:
        """
        Worker-emitted progress log.
        Crucial Stage 2 Boundary: Scoped as WORKER_PROGRESS_LOGGED with source=WORKER.
        Does not alter authoritative runtime state.
        """
        self._runtime.log_event(
            event_type=EventType.WORKER_PROGRESS_LOGGED,
            payload={"event_type": event_type, "data": payload},
            source=EventSource.WORKER,
            correlation_id=self._task.id,
            causation_id=self._causation_id,
            project_id=self._project.id,
            task_id=self._task.id,
            worker_id=self._worker_id,
        )


class WorkforceRuntime:
    """
    Central deterministic Workforce Runtime for AutonomOS with Event & Activity System
    and Persistent Project Memory.
    Coordinates projects, workers, tasks, artifacts, memory, dependencies, and immutable audit events.
    """

    def __init__(self, store: Store):
        self.store = store
        self._lock = threading.RLock()

        self.projects = ProjectRegistry(store)
        self.workers = WorkerRegistry(store)
        self.artifacts = ArtifactRegistry(store)
        self.tasks = TaskEngine(store, self.workers)
        self.memory = MemoryManager(store)

        # In-memory listeners for live event streaming (e.g. SSE / WebSocket / Activity feed)
        self._subscribers: list[Callable[[Event], None]] = []

    @classmethod
    def with_sqlite(cls, db_path: str) -> "WorkforceRuntime":
        """Factory method to initialize runtime with embedded SQLite persistence."""
        store = SQLiteStore(db_path)
        return cls(store)

    @property
    def event_log(self) -> list[dict[str, Any]]:
        """Backward-compatibility property returning list of event dictionaries."""
        events = self.store.list_events()
        return [e.to_dict() for e in events]

    def subscribe_events(self, subscriber: Callable[[Event], None]) -> None:
        """Subscribe to real-time events emitted by the runtime."""
        with self._lock:
            self._subscribers.append(subscriber)

    def log_event(
        self,
        event_type: EventType | str,
        payload: dict[str, Any],
        source: EventSource = EventSource.RUNTIME,
        correlation_id: Optional[str] = None,
        causation_id: Optional[str] = None,
        project_id: Optional[str] = None,
        task_id: Optional[str] = None,
        worker_id: Optional[str] = None,
        artifact_id: Optional[str] = None,
        metadata: Optional[dict[str, Any]] = None,
    ) -> Event:
        """
        Record and persist an immutable authoritative event in the Event Store.
        """
        if isinstance(event_type, str):
            try:
                etype = EventType(event_type)
            except ValueError:
                etype = EventType.WORKER_PROGRESS_LOGGED
                payload = {"custom_event_type": event_type, **payload}
        else:
            etype = event_type

        evt = Event.create(
            event_type=etype,
            source=source,
            correlation_id=correlation_id,
            causation_id=causation_id,
            project_id=project_id,
            task_id=task_id,
            worker_id=worker_id,
            artifact_id=artifact_id,
            payload=payload,
            metadata=metadata,
        )

        persisted_event = self.store.append_event(evt)
        logger.info(format_event_log_line(persisted_event))

        # Notify subscribers
        with self._lock:
            subscribers = list(self._subscribers)
        for sub in subscribers:
            try:
                sub(persisted_event)
            except Exception as e:
                logger.warning(f"Event subscriber threw exception: {e}")

        return persisted_event

    # Project Operations
    def create_project(
        self,
        name: str,
        root_path: str,
        description: str = "",
        project_id: Optional[str] = None,
        configuration: Optional[dict[str, Any]] = None,
        initialize_memory: bool = True,
    ) -> Project:
        project = self.projects.create_project(
            name=name,
            root_path=root_path,
            description=description,
            project_id=project_id,
            configuration=configuration,
        )
        proj_evt = self.log_event(
            event_type=EventType.PROJECT_CREATED,
            payload={"name": project.name, "description": project.description, "root_path": project.root_path},
            project_id=project.id,
            correlation_id=project.id,
        )

        # Stage 3: Initialize persistent project memory scaffold (.autonomos/memory/)
        if initialize_memory:
            scaffold_docs = self.memory.initialize_project_memory(
                project_id=project.id,
                project_name=project.name,
                description=project.description,
            )
            for doc in scaffold_docs.values():
                self.log_event(
                    event_type=EventType.MEMORY_CREATED,
                    payload={"memory_id": doc.id, "title": doc.title, "relative_path": doc.relative_path},
                    project_id=project.id,
                    correlation_id=project.id,
                    causation_id=proj_evt.event_id,
                )

        return project

    # Worker Operations
    def register_worker(self, worker: Worker, initial_status: WorkerStatus = WorkerStatus.IDLE) -> WorkerManifest:
        manifest = self.workers.register_worker(worker, initial_status=initial_status)
        self.log_event(
            event_type=EventType.WORKER_REGISTERED,
            payload={"name": manifest.name, "role": manifest.role, "capabilities": manifest.capabilities},
            worker_id=manifest.id,
            correlation_id=manifest.id,
        )
        return manifest

    def register_worker_instance_only(self, worker: Worker) -> None:
        self.workers.register_worker_instance_only(worker)

    # Task Operations
    def create_task(
        self,
        project_id: str,
        title: str,
        objective: str = "",
        parent_task_id: Optional[str] = None,
        priority: int = 1,
        risk: RiskLevel = RiskLevel.LOW,
        dependencies: Optional[list[str]] = None,
        success_criteria: Optional[list[dict[str, Any]]] = None,
        context_references: Optional[list[dict[str, Any]]] = None,
        max_attempts: int = 3,
        task_id: Optional[str] = None,
    ) -> Task:
        task = self.tasks.create_task(
            project_id=project_id,
            title=title,
            objective=objective,
            parent_task_id=parent_task_id,
            priority=priority,
            risk=risk,
            dependencies=dependencies,
            success_criteria=success_criteria,
            context_references=context_references,
            max_attempts=max_attempts,
            task_id=task_id,
        )
        self.log_event(
            event_type=EventType.TASK_CREATED,
            payload={"title": task.title, "objective": task.objective, "status": task.status.value, "risk": task.risk.value},
            project_id=project_id,
            task_id=task.id,
            correlation_id=task.id,
        )
        return task

    def add_task_dependency(
        self,
        dependent_task_id: str,
        prerequisite_task_id: str,
        dependency_type: DependencyType = DependencyType.STRICT_SUCCESS,
    ) -> None:
        self.tasks.add_dependency(dependent_task_id, prerequisite_task_id, dependency_type)
        self.log_event(
            event_type=EventType.DEPENDENCY_ADDED,
            payload={
                "dependent_task_id": dependent_task_id,
                "prerequisite_task_id": prerequisite_task_id,
                "type": dependency_type.value,
            },
            task_id=dependent_task_id,
            correlation_id=dependent_task_id,
        )

    def assign_task(self, task_id: str, worker_id: str) -> tuple[Task, WorkerManifest]:
        task, worker_manifest = self.tasks.assign_task(task_id, worker_id)
        assign_evt = self.log_event(
            event_type=EventType.TASK_ASSIGNED,
            payload={"worker_id": worker_id, "title": task.title},
            project_id=task.project_id,
            task_id=task_id,
            worker_id=worker_id,
            correlation_id=task_id,
        )
        self.log_event(
            event_type=EventType.WORKER_ASSIGNED,
            payload={"task_id": task_id, "task_title": task.title},
            project_id=task.project_id,
            task_id=task_id,
            worker_id=worker_id,
            correlation_id=task_id,
            causation_id=assign_evt.event_id,
        )
        return task, worker_manifest

    # Execution Orchestration
    def run_task(self, task_id: str) -> WorkerOutput:
        """
        Execute a task through its complete deterministic lifecycle with full event causation
        and persistent memory recording:
        1. Validate state & assigned worker.
        2. Transition task and worker to RUNNING.
        3. Emit TASK_STARTED and WORKER_STARTED with causation linkage.
        4. Execute worker with runtime context.
        5. Handle success/failure, update attempts, register artifacts.
        6. Record persistent worker report and task memory markdown documents.
        7. Transition task to COMPLETED or FAILED/RETRYING.
        8. Release worker back to IDLE.
        9. Trigger downstream dependency resolution.
        """
        with self._lock:
            task = self.tasks.get_task(task_id)

            if task.status == TaskStatus.COMPLETED:
                raise TaskAlreadyCompletedError(task_id)

            if not task.assigned_worker:
                raise WorkerNotFoundError("No worker assigned to task")

            worker_manifest = self.workers.get_worker(task.assigned_worker)
            worker_instance = self.workers.get_worker_instance(task.assigned_worker)
            project = self.projects.get_project(task.project_id)

            # Increment attempts
            task.attempts += 1

            # Transition task to RUNNING
            TaskStateMachine.validate_and_transition(task, TaskStatus.RUNNING)
            self.store.save_task(task)

            # Transition worker to RUNNING
            WorkerStateMachine.validate_and_transition(
                worker=worker_manifest,
                target_status=WorkerStatus.RUNNING,
                active_task_id=task.id,
            )
            self.store.save_worker(worker_manifest)

            # Find latest assign event for causation linking
            recent_assigns = self.store.list_events(task_id=task.id, event_types=[EventType.TASK_ASSIGNED], limit=1)
            assign_causation_id = recent_assigns[-1].event_id if recent_assigns else None

            start_evt = self.log_event(
                event_type=EventType.TASK_STARTED,
                payload={"attempt": task.attempts, "worker_id": worker_manifest.id, "title": task.title},
                project_id=project.id,
                task_id=task.id,
                worker_id=worker_manifest.id,
                correlation_id=task.id,
                causation_id=assign_causation_id,
            )
            self.log_event(
                event_type=EventType.WORKER_STARTED,
                payload={"task_id": task.id, "attempt": task.attempts},
                project_id=project.id,
                task_id=task.id,
                worker_id=worker_manifest.id,
                correlation_id=task.id,
                causation_id=start_evt.event_id,
            )

        context = _DefaultWorkerRuntimeContext(
            task=task,
            project=project,
            artifact_registry=self.artifacts,
            worker_id=worker_manifest.id,
            runtime=self,
            causation_id=start_evt.event_id,
        )

        output: WorkerOutput
        try:
            output = worker_instance.execute_task(context, task)
        except Exception as e:
            output = WorkerOutput(
                success=False,
                summary=f"Worker threw unhandled exception: {str(e)}",
                error_message=str(e),
            )
            self.log_event(
                event_type=EventType.EXECUTION_FAILED,
                payload={"error": str(e), "task_id": task.id},
                project_id=project.id,
                task_id=task.id,
                worker_id=worker_manifest.id,
                correlation_id=task.id,
                causation_id=start_evt.event_id,
            )

        with self._lock:
            task = self.tasks.get_task(task_id)
            worker_manifest = self.workers.get_worker(task.assigned_worker)

            if output.success:
                # 1. Transition worker RUNNING -> REPORTING -> IDLE
                WorkerStateMachine.validate_and_transition(worker_manifest, WorkerStatus.REPORTING, active_task_id=task.id)
                self.store.save_worker(worker_manifest)

                WorkerStateMachine.validate_and_transition(worker_manifest, WorkerStatus.IDLE, active_task_id=None)
                self.store.save_worker(worker_manifest)

                self.log_event(
                    event_type=EventType.WORKER_FINISHED,
                    payload={"task_id": task.id, "status": "SUCCESS"},
                    project_id=project.id,
                    task_id=task.id,
                    worker_id=worker_manifest.id,
                    correlation_id=task.id,
                    causation_id=start_evt.event_id,
                )
                self.log_event(
                    event_type=EventType.WORKER_BECAME_IDLE,
                    payload={"worker_id": worker_manifest.id},
                    project_id=project.id,
                    worker_id=worker_manifest.id,
                    correlation_id=task.id,
                )

                # 2. Stage 3 Memory: Record execution report and task memory
                if output.report_markdown:
                    report_doc = self.memory.record_report(
                        project_id=project.id,
                        task_id=task.id,
                        worker_id=worker_manifest.id,
                        title=f"Execution Report: {task.title}",
                        summary=output.summary,
                        markdown_body=output.report_markdown,
                        report_type="execution",
                    )
                    self.log_event(
                        event_type=EventType.REPORT_CREATED,
                        payload={"report_id": report_doc.id, "title": report_doc.title, "path": report_doc.relative_path},
                        project_id=project.id,
                        task_id=task.id,
                        worker_id=worker_manifest.id,
                        correlation_id=task.id,
                        causation_id=start_evt.event_id,
                    )

                task_mem = self.memory.record_task_memory(
                    project_id=project.id,
                    task_id=task.id,
                    title=task.title,
                    objective=task.objective,
                    findings=output.summary,
                    outcome="COMPLETED",
                )
                self.log_event(
                    event_type=EventType.MEMORY_CREATED,
                    payload={"memory_id": task_mem.id, "title": task_mem.title, "path": task_mem.relative_path},
                    project_id=project.id,
                    task_id=task.id,
                    worker_id=worker_manifest.id,
                    correlation_id=task.id,
                    causation_id=start_evt.event_id,
                )

                # 3. Transition task RUNNING -> COMPLETED
                TaskStateMachine.validate_and_transition(task, TaskStatus.COMPLETED)
                self.store.save_task(task)

                complete_evt = self.log_event(
                    event_type=EventType.TASK_COMPLETED,
                    payload={
                        "summary": output.summary,
                        "artifacts_count": len(task.artifacts),
                        "attempts": task.attempts,
                    },
                    project_id=project.id,
                    task_id=task.id,
                    worker_id=worker_manifest.id,
                    correlation_id=task.id,
                    causation_id=start_evt.event_id,
                )

                # 4. Resolve downstream tasks in DAG
                newly_ready = self.tasks.dependency_resolver.resolve_downstream_tasks(task.id)
                for nr in newly_ready:
                    self.log_event(
                        event_type=EventType.TASK_READY,
                        payload={"reason": f"Prerequisite {task.id} completed"},
                        project_id=project.id,
                        task_id=nr.id,
                        correlation_id=nr.id,
                        causation_id=complete_evt.event_id,
                    )

            else:
                # Execution Failed
                WorkerStateMachine.validate_and_transition(worker_manifest, WorkerStatus.FAILED, active_task_id=task.id)
                self.store.save_worker(worker_manifest)

                WorkerStateMachine.validate_and_transition(worker_manifest, WorkerStatus.IDLE, active_task_id=None)
                self.store.save_worker(worker_manifest)

                self.log_event(
                    event_type=EventType.WORKER_FAILED,
                    payload={"reason": output.error_message, "task_id": task.id},
                    project_id=project.id,
                    task_id=task.id,
                    worker_id=worker_manifest.id,
                    correlation_id=task.id,
                    causation_id=start_evt.event_id,
                )

                if task.attempts < task.max_attempts:
                    TaskStateMachine.validate_and_transition(task, TaskStatus.RETRYING, reason=output.error_message)
                    self.store.save_task(task)
                    self.log_event(
                        event_type=EventType.TASK_RETRYING,
                        payload={
                            "attempt": task.attempts,
                            "max_attempts": task.max_attempts,
                            "reason": output.error_message,
                        },
                        project_id=project.id,
                        task_id=task.id,
                        worker_id=worker_manifest.id,
                        correlation_id=task.id,
                        causation_id=start_evt.event_id,
                    )
                else:
                    TaskStateMachine.validate_and_transition(task, TaskStatus.FAILED, reason=output.error_message)
                    self.store.save_task(task)
                    self.log_event(
                        event_type=EventType.TASK_FAILED,
                        payload={
                            "attempt": task.attempts,
                            "reason": output.error_message,
                        },
                        project_id=project.id,
                        task_id=task.id,
                        worker_id=worker_manifest.id,
                        correlation_id=task.id,
                        causation_id=start_evt.event_id,
                    )

        return output

    # Stage 3 Memory Operations
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
        """Record an architectural decision and emit DECISION_CREATED event."""
        doc = self.memory.record_decision(
            project_id=project_id,
            title=title,
            context=context,
            decision=decision,
            reasoning=reasoning,
            consequences=consequences,
            decision_number=decision_number,
            status=status,
            references=references,
        )
        self.log_event(
            event_type=EventType.DECISION_CREATED,
            payload={"decision_id": doc.id, "title": doc.title, "path": doc.relative_path, "status": status},
            project_id=project_id,
            correlation_id=project_id,
        )
        return doc

    def record_issue(
        self,
        project_id: str,
        title: str,
        description: str,
        severity: IssueSeverity = IssueSeverity.MEDIUM,
        status: IssueStatus = IssueStatus.OPEN,
        affected_area: str = "",
        related_task_id: Optional[str] = None,
    ) -> MemoryDocument:
        """Record a persistent issue and emit ISSUE_RECORDED event."""
        doc = self.memory.record_issue(
            project_id=project_id,
            title=title,
            description=description,
            severity=severity,
            status=status,
            affected_area=affected_area,
            related_task_id=related_task_id,
        )
        self.log_event(
            event_type=EventType.ISSUE_RECORDED,
            payload={
                "issue_id": doc.id,
                "title": doc.title,
                "severity": severity.value,
                "status": status.value,
                "path": doc.relative_path,
            },
            project_id=project_id,
            task_id=related_task_id,
            correlation_id=project_id,
        )
        return doc

    def update_current_state(
        self,
        project_id: str,
        stage: str,
        completed_milestones: list[str],
        active_work: list[str],
        known_limitations: list[str],
        notes: str = "",
    ) -> MemoryDocument:
        """Update current project state and emit MEMORY_UPDATED event."""
        doc = self.memory.update_current_state(
            project_id=project_id,
            stage=stage,
            completed_milestones=completed_milestones,
            active_work=active_work,
            known_limitations=known_limitations,
            notes=notes,
        )
        self.log_event(
            event_type=EventType.MEMORY_UPDATED,
            payload={"memory_id": doc.id, "title": doc.title, "path": doc.relative_path, "version": doc.version},
            project_id=project_id,
            correlation_id=project_id,
        )
        return doc

    def validate_memory_references(self, project_id: str, memory_id: str) -> ValidationReport:
        """Validate references in a specific memory document."""
        return self.memory.validate_memory_references(project_id, memory_id)

    def audit_project_memory(self, project_id: str) -> list[ValidationReport]:
        """Audit all memory documents in a project for stale/broken references."""
        return self.memory.audit_project_memory(project_id)

    # Activity & Event Queries
    def get_events(
        self,
        project_id: Optional[str] = None,
        task_id: Optional[str] = None,
        worker_id: Optional[str] = None,
        correlation_id: Optional[str] = None,
        event_types: Optional[list[EventType]] = None,
        since_sequence: Optional[int] = None,
        limit: Optional[int] = None,
    ) -> list[Event]:
        """Query raw immutable events from the store."""
        return self.store.list_events(
            project_id=project_id,
            task_id=task_id,
            worker_id=worker_id,
            correlation_id=correlation_id,
            event_types=event_types,
            since_sequence=since_sequence,
            limit=limit,
        )

    def get_task_timeline(self, task_id: str) -> list[Event]:
        """Get chronological event timeline for a task."""
        return self.store.get_events_by_task(task_id)

    def get_project_activity(self, project_id: str, limit: Optional[int] = 50) -> list[ActivityItem]:
        """Get human-readable activity feed for a project."""
        events = self.store.list_events(project_id=project_id, limit=limit)
        return [ActivityProjector.project(e) for e in events]

    def get_activity_feed(self, limit: int = 50) -> list[ActivityItem]:
        """Get global human-readable activity feed."""
        events = self.store.list_events(limit=limit)
        return [ActivityProjector.project(e) for e in events]

    def get_causal_chain(self, event_id: str) -> list[Event]:
        """Reconstruct the causal chain of events leading up to this event."""
        return self.store.get_causal_chain(event_id)

    def close(self) -> None:
        self.store.close()
