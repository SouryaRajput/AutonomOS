import logging
from pathlib import Path
import threading
from typing import Any, Callable, Optional

from core.context.engine import ContextEngine
from core.context.model import ContextBudget, ContextPackage, ContextRequest
from core.context.types import ContextSourceType
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
    ResearcherActivationFailed,
    TaskAlreadyCompletedError,
    TaskNotFoundError,
    WorkerActivationFailedError,
    WorkerNotEligibleError,
    WorkerNotFoundError,
)
from core.events.activity import ActivityItem, ActivityProjector, format_event_log_line
from core.events.model import Event, new_event_id, utc_now
from core.events.types import EventSource, EventType
from core.inference.gateway import CircuitBreaker, InferenceGateway
from core.inference.model import (
    Cost,
    InferenceMessage,
    InferenceRequest,
    InferenceResponse,
    ModelMetadata,
    ModelRequirement,
    Usage,
)
from core.inference.provider import BaseInferenceProvider, MockProvider
from core.inference.registry import ModelRegistry, ProviderRegistry
from core.inference.secrets import EnvSecretStore, SecretStore
from core.inference.types import ModelCapability, ProviderHealthStatus, RoutingProfile
from core.manager.agent import ManagerAgent
from core.manager.controller import ManagerController
from core.workflow.coordinator import WorkflowCoordinator
from core.autonomy.service import AutonomyService
from core.manager.model import (
    ActionResult,
    CycleResult,
    ManagerAction,
    ManagerConfig,
    ManagerDecision,
    ManagerState,
    ManagerStatus,
    Plan,
)
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
from core.safety.checkpoint import CheckpointManager
from core.safety.model import Checkpoint, RollbackResult, SafetyConfig, ScopeDeviation
from core.safety.rollback import RollbackManager
from core.safety.scope import ScopeTracker
from core.safety.types import CheckpointStatus, CheckpointType, RollbackStatus
from core.task.state_machine import TaskStateMachine
from core.tools.base import BaseTool
from core.tools.model import ToolDefinition, ToolRequest, ToolResult
from core.tools.runtime import ToolRuntime
from core.verification.engine import VerificationEngine
from core.verification.model import SuccessCriterion, Verification, VerificationPlan
from core.verification.types import CheckStatus, CheckType, VerificationStatus
from core.worker.state_machine import WorkerStateMachine
from pkg.sdk.subclients import (
    ArtifactClient,
    CancellationClient,
    ContextClient,
    EventClient,
    InferenceClient,
    LoggerClient,
    MemoryClient,
    ProgressClient,
    SafetyClient,
    TaskContext,
    ToolClient,
    VerificationClient,
)
from pkg.sdk.types import WorkerCapability, WorkerConfig, WorkerRequirement
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

        # Scoped sub-clients
        self._context_client = ContextClient(runtime, task.id, worker_id, causation_id)
        self._tool_client = ToolClient(runtime, project.id, task.id, worker_id, causation_id)
        self._inference_client = InferenceClient(runtime, project.id, task.id, worker_id, causation_id)
        self._memory_client = MemoryClient(runtime, project.id, task.id, worker_id, causation_id)
        self._artifact_client = ArtifactClient(runtime, project.id, task.id, worker_id, project.root_path, causation_id)
        self._verification_client = VerificationClient(runtime, project.id, task.id, worker_id, causation_id)
        self._safety_client = SafetyClient(runtime, project.id, task.id, worker_id, causation_id)
        self._event_client = EventClient(runtime, project.id, task.id, worker_id, causation_id)
        self._logger_client = LoggerClient(self._event_client, worker_id)
        self._progress_client = ProgressClient(self._event_client)
        self._cancellation_client = CancellationClient(task.id, lambda: self._check_is_cancelled())

    def _check_is_cancelled(self) -> bool:
        try:
            t = self._runtime.tasks.get_task(self._task.id)
            return t.status == TaskStatus.CANCELLED
        except Exception:
            return False

    @property
    def task(self) -> Task:
        return self._task

    @property
    def project_id(self) -> str:
        return self._project.id

    @property
    def context(self) -> ContextClient:
        return self._context_client

    @property
    def tools(self) -> ToolClient:
        return self._tool_client

    @property
    def inference(self) -> InferenceClient:
        return self._inference_client

    @property
    def memory(self) -> MemoryClient:
        return self._memory_client

    @property
    def artifacts(self) -> ArtifactClient:
        return self._artifact_client

    @property
    def verification(self) -> VerificationClient:
        return self._verification_client

    @property
    def safety(self) -> SafetyClient:
        return self._safety_client

    @property
    def events(self) -> EventClient:
        return self._event_client

    @property
    def log(self) -> LoggerClient:
        return self._logger_client

    @property
    def progress(self) -> ProgressClient:
        return self._progress_client

    @property
    def cancellation(self) -> CancellationClient:
        return self._cancellation_client

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


class WorkforceRuntime:
    """
    Central deterministic Workforce Runtime for AutonomOS with Event & Activity System,
    Persistent Project Memory, Context Engine, and Tool Runtime.
    Coordinates projects, workers, tasks, artifacts, memory, context, tools, dependencies, and immutable audit events.
    """

    def __init__(self, store: Store, safety_config: Optional[SafetyConfig] = None):
        self.store = store
        self._lock = threading.RLock()

        self.projects = ProjectRegistry(store)
        self.workers = WorkerRegistry(store)
        self.artifacts = ArtifactRegistry(store)
        self.tasks = TaskEngine(store, self.workers)
        self.memory = MemoryManager(store)
        self.context = ContextEngine(store=store, memory_manager=self.memory)
        self.safety_config = safety_config or SafetyConfig()
        self.checkpoints = CheckpointManager(store)
        self.rollback = RollbackManager()
        self.scope = ScopeTracker()
        self.tools = ToolRuntime(
            store=store,
            artifact_registry=self.artifacts,
            event_logger=self.log_event,
            checkpoint_manager=self.checkpoints,
            safety_config=self.safety_config,
        )
        self.verification = VerificationEngine(
            tool_runtime=self.tools,
            artifact_registry=self.artifacts,
            store=self.store,
            event_logger=self.log_event,
        )
        self.providers = ProviderRegistry()
        self.models = ModelRegistry()
        self.secrets = EnvSecretStore()
        self.inference = InferenceGateway(
            provider_registry=self.providers,
            model_registry=self.models,
            secret_store=self.secrets,
            event_logger=self.log_event,
        )

        # Register real inference providers and fallback mock provider
        from core.inference.real_providers import GroqProvider, OpenRouterProvider, OllamaProvider
        groq_provider = GroqProvider()
        openrouter_provider = OpenRouterProvider()
        ollama_provider = OllamaProvider()
        default_mock_provider = MockProvider()

        self.providers.register_provider(groq_provider)
        self.providers.register_provider(openrouter_provider)
        self.providers.register_provider(ollama_provider)
        self.providers.register_provider(default_mock_provider)
        self.models.sync_from_providers([groq_provider, openrouter_provider, ollama_provider, default_mock_provider])

        # Stage 10: Manager Agent & Workforce Orchestrator
        self.manager_agent = ManagerAgent(self.inference)
        self.manager = ManagerController(self, agent=self.manager_agent)

        # Stage 14: Workforce Workflow & Collaboration
        self.workflows = WorkflowCoordinator(self)

        # Stage 15: Human-in-the-Loop & Autonomy Control
        self.autonomy = AutonomyService(self)

        # In-memory listeners for live event streaming (e.g. SSE / WebSocket / Activity feed)
        self._subscribers: list[Callable[[Event], None]] = []

        # Stage 18: Interrupted task recovery on startup
        self._recover_orphaned_tasks()

    def _recover_orphaned_tasks(self) -> int:
        """
        Identify and recover tasks left in RUNNING or ASSIGNED states across projects after process restart.
        Transitions them back to RETRYING or READY and frees worker locks.
        """
        recovered_count = 0
        try:
            projects = self.projects.list_projects()
            for p in projects:
                tasks = self.tasks.list_tasks(p.id)
                for t in tasks:
                    if t.status in (TaskStatus.RUNNING, TaskStatus.ASSIGNED):
                        if t.assigned_worker:
                            try:
                                self.workers.update_worker_status(
                                    worker_id=t.assigned_worker,
                                    target_status=WorkerStatus.IDLE,
                                    active_task_id=None,
                                )
                            except Exception:
                                pass
                        target_st = TaskStatus.RETRYING if t.attempts < t.max_attempts else TaskStatus.FAILED
                        t.status = target_st
                        t.assigned_worker = None
                        self.store.save_task(t)
                        recovered_count += 1
                        logger.info(f"Recovered orphaned task '{t.id}' in project '{p.id}' -> {target_st.value}")
        except Exception as err:
            logger.warning(f"Orphaned task recovery skipped/failed: {err}")
        return recovered_count

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
            payload={"name": manifest.name, "role": manifest.role, "capabilities": manifest.capabilities, "permissions": manifest.permissions},
            worker_id=manifest.id,
            correlation_id=manifest.id,
        )
        return manifest

    def register_worker_instance_only(self, worker: Worker) -> None:
        self.workers.register_worker_instance_only(worker)

    def activate_worker(
        self,
        worker_id_or_role: str,
        project_id: Optional[str] = None,
        task_id: Optional[str] = None,
        causation_id: Optional[str] = None,
    ) -> WorkerManifest:
        """
        Dynamically activate/provision a specialized worker through the authoritative lifecycle:
        REQUESTED -> STARTING -> INITIALIZING -> REGISTERED -> READY/IDLE.
        """
        req_evt = self.log_event(
            event_type=EventType.WORKER_ACTIVATION_REQUESTED,
            payload={"worker_id_or_role": worker_id_or_role, "task_id": task_id, "project_id": project_id},
            project_id=project_id,
            task_id=task_id,
            correlation_id=task_id or project_id,
            causation_id=causation_id,
        )

        start_evt = self.log_event(
            event_type=EventType.WORKER_ACTIVATION_STARTED,
            payload={"worker_id_or_role": worker_id_or_role, "task_id": task_id},
            project_id=project_id,
            task_id=task_id,
            correlation_id=task_id or project_id,
            causation_id=req_evt.event_id,
        )

        try:
            manifest, is_newly_registered = self.workers.provision_worker(worker_id_or_role)

            if is_newly_registered:
                self.log_event(
                    event_type=EventType.WORKER_REGISTERED,
                    payload={
                        "name": manifest.name,
                        "role": manifest.role,
                        "capabilities": manifest.capabilities,
                        "permissions": manifest.permissions,
                    },
                    worker_id=manifest.id,
                    project_id=project_id,
                    correlation_id=manifest.id,
                    causation_id=start_evt.event_id,
                )
                self.log_event(
                    event_type=EventType.WORKER_BECAME_IDLE,
                    payload={"worker_id": manifest.id, "role": manifest.role},
                    worker_id=manifest.id,
                    project_id=project_id,
                    correlation_id=manifest.id,
                    causation_id=start_evt.event_id,
                )

            self.log_event(
                event_type=EventType.WORKER_ACTIVATION_COMPLETED,
                payload={"worker_id": manifest.id, "role": manifest.role, "status": manifest.status.value},
                worker_id=manifest.id,
                project_id=project_id,
                correlation_id=manifest.id,
                causation_id=start_evt.event_id,
            )
            return manifest

        except Exception as err:
            self.log_event(
                event_type=EventType.WORKER_ACTIVATION_FAILED,
                payload={"worker_id_or_role": worker_id_or_role, "reason": str(err), "task_id": task_id},
                project_id=project_id,
                task_id=task_id,
                correlation_id=task_id or project_id,
                causation_id=start_evt.event_id,
            )
            if "research" in worker_id_or_role.lower():
                raise ResearcherActivationFailed(
                    worker_id=worker_id_or_role,
                    reason=str(err),
                    task_id=task_id,
                ) from err
            elif isinstance(err, WorkerActivationFailedError):
                raise
            else:
                raise WorkerActivationFailedError(
                    worker_id=worker_id_or_role,
                    reason=str(err),
                    task_id=task_id,
                ) from err

    def register_default_specialist_workers(self) -> None:
        """Register the production AI specialist workers: Researcher, Programmer, Tester."""
        from workers.programmer.worker import ProgrammerWorker
        from workers.researcher.worker import ResearcherWorker
        from workers.tester.worker import TesterWorker

        self.register_worker(ResearcherWorker())
        self.register_worker(ProgrammerWorker())
        self.register_worker(TesterWorker())

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
        metadata: Optional[dict[str, Any]] = None,
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
            metadata=metadata,
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
        if not self.workers.has_worker(worker_id):
            try:
                self.activate_worker(worker_id, task_id=task_id)
            except Exception:
                pass
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

    # Context Engine Operations (Stage 4)
    def request_context(
        self,
        task_id: str,
        worker_id: Optional[str] = None,
        budget: Optional[ContextBudget] = None,
        focus_areas: Optional[list[str]] = None,
        required_sources: Optional[list[ContextSourceType]] = None,
        excluded_sources: Optional[list[ContextSourceType]] = None,
        causation_id: Optional[str] = None,
    ) -> ContextPackage:
        try:
            task = self.tasks.get_task(task_id)
            proj_id = task.project_id
            target_worker = worker_id or task.assigned_worker
            corr_id = task.id
        except TaskNotFoundError:
            if task_id.startswith("mgr-state-"):
                proj_id = task_id[len("mgr-state-"):]
                target_worker = worker_id or "worker.manager.orchestrator"
                corr_id = proj_id
            else:
                raise

        req = ContextRequest(
            project_id=proj_id,
            task_id=task_id,
            worker_id=target_worker,
            budget=budget or ContextBudget(),
            focus_areas=focus_areas or [],
            required_sources=required_sources or [],
            excluded_sources=excluded_sources or [],
        )

        req_evt = self.log_event(
            event_type=EventType.CONTEXT_REQUESTED,
            payload={"request_id": req.request_id, "task_id": task_id, "worker_id": req.worker_id},
            project_id=proj_id,
            task_id=task_id,
            worker_id=req.worker_id,
            correlation_id=corr_id,
            causation_id=causation_id,
        )

        package = self.context.assemble_context(req)

        self.log_event(
            event_type=EventType.CONTEXT_ASSEMBLED,
            payload={
                "request_id": package.request_id,
                "selected_count": package.selected_count,
                "candidate_count": package.candidate_count,
                "token_estimate": package.total_estimated_tokens,
                "warnings_count": len(package.warnings),
            },
            project_id=proj_id,
            task_id=task_id,
            worker_id=req.worker_id,
            correlation_id=corr_id,
            causation_id=req_evt.event_id,
        )

        for w in package.warnings:
            self.log_event(
                event_type=EventType.CONTEXT_WARNING,
                payload={"warning_type": w.warning_type.value, "message": w.message, "target_id": w.target_id},
                project_id=proj_id,
                task_id=task_id,
                worker_id=req.worker_id,
                correlation_id=corr_id,
                causation_id=req_evt.event_id,
            )

        return package

    # Tool Runtime Operations (Stage 5)
    def execute_tool(
        self,
        project_id: str,
        task_id: str,
        worker_id: str,
        tool_id: str,
        arguments: dict[str, Any],
        timeout_seconds: Optional[int] = None,
        causation_id: Optional[str] = None,
    ) -> ToolResult:
        """Execute a tool request through the Tool Runtime."""
        req = ToolRequest(
            project_id=project_id,
            task_id=task_id,
            worker_id=worker_id,
            tool_id=tool_id,
            arguments=arguments,
            correlation_id=task_id,
            causation_id=causation_id,
            timeout_seconds=timeout_seconds,
        )
        return self.tools.execute_request(req)

    def register_tool(self, tool: BaseTool) -> None:
        """Register a custom tool in the Tool Runtime."""
        self.tools.registry.register_tool(tool)

    # Inference Gateway Operations (Stage 8)
    def request_inference(
        self,
        request: InferenceRequest,
        causation_id: Optional[str] = None,
    ) -> InferenceResponse:
        """Execute an inference request through the Inference Gateway (OmniRoute)."""
        return self.inference.execute(request, causation_id=causation_id)

    # Execution Orchestration
    def run_task(self, task_id: str) -> WorkerOutput:
        """
        Execute a task through its complete deterministic lifecycle with full event causation,
        persistent memory recording, Context Engine readiness, and Tool Runtime support.
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

            task.attempts += 1

            TaskStateMachine.validate_and_transition(task, TaskStatus.RUNNING)
            self.store.save_task(task)

            if worker_manifest.status == WorkerStatus.IDLE:
                WorkerStateMachine.validate_and_transition(
                    worker=worker_manifest,
                    target_status=WorkerStatus.ASSIGNED,
                    active_task_id=task.id,
                )
                self.store.save_worker(worker_manifest)

            WorkerStateMachine.validate_and_transition(
                worker=worker_manifest,
                target_status=WorkerStatus.RUNNING,
                active_task_id=task.id,
            )
            self.store.save_worker(worker_manifest)

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

            # Stage 6: Pre-Task Checkpoint Creation
            task_checkpoint: Optional[Checkpoint] = None
            if self.safety_config.auto_checkpoint:
                task_checkpoint = self.checkpoints.create_checkpoint(
                    project_id=project.id,
                    task_id=task.id,
                    worker_id=worker_manifest.id,
                )
                self.log_event(
                    event_type=EventType.CHECKPOINT_CREATED,
                    payload={"checkpoint_id": task_checkpoint.id, "checkpoint_type": task_checkpoint.checkpoint_type.value},
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
            worker_instance.before_task(context, task)
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
        finally:
            try:
                worker_instance.after_task(context, task, output)
            except Exception as after_err:
                logger.warning(f"Error in after_task hook for worker {worker_manifest.id}: {after_err}")

        with self._lock:
            task = self.tasks.get_task(task_id)
            worker_manifest = self.workers.get_worker(task.assigned_worker)

            if output.success:
                # Stage 6: Check Scope Deviations
                if task_checkpoint:
                    deviations = self.scope.check_deviations(
                        checkpoint=task_checkpoint,
                        project_root=project.root_path,
                        task=task,
                        config=self.safety_config,
                    )
                    if deviations:
                        self.log_event(
                            event_type=EventType.SCOPE_DEVIATION_DETECTED,
                            payload={
                                "deviations_count": len(deviations),
                                "deviations": [d.to_dict() for d in deviations],
                            },
                            project_id=project.id,
                            task_id=task.id,
                            worker_id=worker_manifest.id,
                            correlation_id=task.id,
                            causation_id=start_evt.event_id,
                        )

                # Stage 7: Authoritative Deterministic Verification
                verification = self.verification.verify_task(
                    project=project,
                    task=task,
                    worker_id=worker_manifest.id,
                    checkpoint_id=task_checkpoint.id if task_checkpoint else None,
                    causation_id=start_evt.event_id,
                )

                if verification.status != VerificationStatus.PASSED:
                    # Overrule worker claim: verification failed or is uncertain
                    output = WorkerOutput(
                        success=False,
                        summary=f"Worker claim rejected by Verification System: {verification.summary}",
                        error_message=verification.summary,
                    )

            if output.success:
                if task_checkpoint:
                    self.checkpoints.commit_checkpoint(task_checkpoint.id)
                    self.log_event(
                        event_type=EventType.CHECKPOINT_COMMITTED,
                        payload={"checkpoint_id": task_checkpoint.id},
                        project_id=project.id,
                        task_id=task.id,
                        worker_id=worker_manifest.id,
                        correlation_id=task.id,
                        causation_id=start_evt.event_id,
                    )

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
                # Stage 6: Rollback on Task Failure
                rollback_failed = False
                if task_checkpoint:
                    self.log_event(
                        event_type=EventType.CHECKPOINT_ROLLBACK_STARTED,
                        payload={"checkpoint_id": task_checkpoint.id},
                        project_id=project.id,
                        task_id=task.id,
                        worker_id=worker_manifest.id,
                        correlation_id=task.id,
                        causation_id=start_evt.event_id,
                    )
                    rb_res = self.rollback.rollback(
                        checkpoint=task_checkpoint,
                        project_root=project.root_path,
                        store=self.store,
                        memory_manager=self.memory,
                    )
                    if rb_res.status == RollbackStatus.SUCCESS:
                        self.log_event(
                            event_type=EventType.CHECKPOINT_ROLLBACK_COMPLETED,
                            payload={
                                "checkpoint_id": task_checkpoint.id,
                                "restored_count": len(rb_res.restored_files),
                                "deleted_count": len(rb_res.deleted_files),
                                "preserved_user_count": len(rb_res.preserved_user_files),
                            },
                            project_id=project.id,
                            task_id=task.id,
                            worker_id=worker_manifest.id,
                            correlation_id=task.id,
                            causation_id=start_evt.event_id,
                        )
                    else:
                        rollback_failed = True
                        self.log_event(
                            event_type=EventType.CHECKPOINT_ROLLBACK_FAILED,
                            payload={"checkpoint_id": task_checkpoint.id, "error": rb_res.error_message},
                            project_id=project.id,
                            task_id=task.id,
                            worker_id=worker_manifest.id,
                            correlation_id=task.id,
                            causation_id=start_evt.event_id,
                        )

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

                if task.attempts < task.max_attempts and not rollback_failed:
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
        return self.memory.validate_memory_references(project_id, memory_id)

    def audit_project_memory(self, project_id: str) -> list[ValidationReport]:
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
        return self.store.get_events_by_task(task_id)

    def get_project_activity(self, project_id: str, limit: Optional[int] = 50) -> list[ActivityItem]:
        events = self.store.list_events(project_id=project_id, limit=limit)
        return [ActivityProjector.project(e) for e in events]

    def get_activity_feed(self, limit: int = 50) -> list[ActivityItem]:
        events = self.store.list_events(limit=limit)
        return [ActivityProjector.project(e) for e in events]

    def get_causal_chain(self, event_id: str) -> list[Event]:
        return self.store.get_causal_chain(event_id)

    # Stage 10: Manager Orchestration Operations
    def step_manager(
        self,
        project_id: str,
        trigger_event: Optional[Event] = None,
        feedback: Optional[str] = None,
    ) -> CycleResult:
        """Execute a single reasoning and action cycle of the Manager."""
        return self.manager.execute_cycle(project_id, trigger_event=trigger_event, feedback_message=feedback)

    def run_orchestration(self, project_id: str, max_cycles: int = 15) -> CycleResult:
        """Execute automated Manager orchestration loop until project completion or stopping condition."""
        return self.manager.run_orchestration(project_id, max_cycles=max_cycles)

    def get_manager_status(self, project_id: str) -> ManagerStatus:
        """Query real-time status of the Manager Orchestrator."""
        return self.manager.get_status(project_id)

    def get_active_plan(self, project_id: str) -> Optional[Plan]:
        """Get the latest active plan for a project."""
        return self.manager.get_active_plan(project_id)

    def close(self) -> None:
        self.store.close()
