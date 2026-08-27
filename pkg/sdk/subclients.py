from __future__ import annotations

from dataclasses import dataclass
import logging
from typing import TYPE_CHECKING, Any, Callable, Optional
import uuid

from core.context.model import ContextBudget, ContextPackage
from core.enums import ArtifactType, IssueSeverity, IssueStatus, RiskLevel
from core.inference.model import (
    InferenceMessage,
    InferenceRequest,
    InferenceResponse,
    ModelRequirement,
)
from core.memory.model import MemoryDocument
from core.models import Artifact, Evidence, Task
from core.tools.model import ToolDefinition, ToolResult
from core.verification.model import SuccessCriterion, VerificationPlan, VerificationResult
from pkg.sdk.errors import WorkerCancelledError

if TYPE_CHECKING:
    from core.runtime.workforce_runtime import WorkforceRuntime

logger = logging.getLogger("AutonomOS.WorkerSDK")


@dataclass(frozen=True)
class TaskContext:
    """Safe, read-oriented projection of the active task provided to the worker."""
    id: str
    project_id: str
    title: str
    objective: str
    priority: int
    risk: RiskLevel
    success_criteria: list[dict[str, Any]]
    dependencies: list[str]
    artifacts: list[str]
    attempts: int
    max_attempts: int
    parent_task_id: Optional[str] = None
    metadata: dict[str, Any] = None

    @classmethod
    def from_task(cls, task: Task) -> TaskContext:
        return cls(
            id=task.id,
            project_id=task.project_id,
            parent_task_id=task.parent_task_id,
            title=task.title,
            objective=task.objective,
            priority=task.priority,
            risk=task.risk,
            success_criteria=list(task.success_criteria),
            dependencies=list(task.dependencies),
            artifacts=list(task.artifacts),
            attempts=task.attempts,
            max_attempts=task.max_attempts,
            metadata=dict(task.metadata or {}),
        )


class ContextClient:
    """Scoped client for requesting bounded context from the Context Engine."""

    def __init__(self, runtime: WorkforceRuntime, task_id: str, worker_id: str, causation_id: Optional[str] = None):
        self._runtime = runtime
        self._task_id = task_id
        self._worker_id = worker_id
        self._causation_id = causation_id

    def get(
        self,
        budget: Optional[ContextBudget] = None,
        focus_areas: Optional[list[str]] = None,
    ) -> ContextPackage:
        """Request a prioritized, deduplicated ContextPackage within a specified token budget."""
        return self._runtime.request_context(
            task_id=self._task_id,
            worker_id=self._worker_id,
            budget=budget,
            focus_areas=focus_areas,
            causation_id=self._causation_id,
        )

    def request(
        self,
        budget: Optional[ContextBudget] = None,
        focus_areas: Optional[list[str]] = None,
    ) -> ContextPackage:
        """Alias for get()."""
        return self.get(budget=budget, focus_areas=focus_areas)


class ToolClient:
    """Scoped client for executing authorized tools through the Tool Runtime."""

    def __init__(
        self,
        runtime: WorkforceRuntime,
        project_id: str,
        task_id: str,
        worker_id: str,
        causation_id: Optional[str] = None,
    ):
        self._runtime = runtime
        self._project_id = project_id
        self._task_id = task_id
        self._worker_id = worker_id
        self._causation_id = causation_id

    def execute(
        self,
        tool_id: str,
        arguments: dict[str, Any],
        timeout_seconds: Optional[int] = None,
    ) -> ToolResult:
        """Execute a tool through the Tool Runtime with full authorization and workspace confinement."""
        from core.tools.model import ToolRequest
        req = ToolRequest(
            project_id=self._project_id,
            task_id=self._task_id,
            worker_id=self._worker_id,
            tool_id=tool_id,
            arguments=arguments,
            correlation_id=self._task_id,
            causation_id=self._causation_id,
            timeout_seconds=timeout_seconds,
        )
        return self._runtime.tools.execute_request(req)

    def list(self) -> list[ToolDefinition]:
        """Discover tools that this worker is authorized to execute."""
        worker = self._runtime.workers.get_worker(self._worker_id)
        return self._runtime.tools.registry.list_tools_for_worker(worker)


class InferenceClient:
    """Scoped client for invoking LLMs through the Inference Gateway (OmniRoute)."""

    def __init__(
        self,
        runtime: WorkforceRuntime,
        project_id: str,
        task_id: str,
        worker_id: str,
        causation_id: Optional[str] = None,
    ):
        self._runtime = runtime
        self._project_id = project_id
        self._task_id = task_id
        self._worker_id = worker_id
        self._causation_id = causation_id

    def generate(
        self,
        messages: list[InferenceMessage | dict[str, Any]],
        requirements: Optional[ModelRequirement] = None,
        temperature: float = 0.7,
        max_output_tokens: Optional[int] = None,
        tools: Optional[list[dict[str, Any]]] = None,
        metadata: Optional[dict[str, Any]] = None,
    ) -> InferenceResponse:
        """Submit a normalized inference request through OmniRoute with automatic provider fallback."""
        norm_messages: list[InferenceMessage] = []
        for m in messages:
            if isinstance(m, InferenceMessage):
                norm_messages.append(m)
            elif isinstance(m, dict):
                norm_messages.append(InferenceMessage.from_dict(m))
            else:
                norm_messages.append(InferenceMessage(role="user", content=str(m)))

        req = InferenceRequest(
            request_id=f"req-inf-{uuid.uuid4().hex[:8]}",
            project_id=self._project_id,
            task_id=self._task_id,
            worker_id=self._worker_id,
            messages=norm_messages,
            requirements=requirements or ModelRequirement(),
            temperature=temperature,
            max_output_tokens=max_output_tokens,
            tools=tools,
            metadata=metadata or {},
        )
        return self._runtime.request_inference(req, causation_id=self._causation_id)

    def estimate_tokens(self, messages: list[InferenceMessage | dict[str, Any]]) -> int:
        """Estimate token count for a message list."""
        total_chars = 0
        for m in messages:
            if isinstance(m, InferenceMessage):
                total_chars += len(m.content)
            elif isinstance(m, dict):
                total_chars += len(m.get("content", ""))
            else:
                total_chars += len(str(m))
        return max(1, total_chars // 4)


class MemoryClient:
    """Scoped client for interacting with Persistent Project Memory."""

    def __init__(
        self,
        runtime: WorkforceRuntime,
        project_id: str,
        task_id: str,
        worker_id: str,
        causation_id: Optional[str] = None,
    ):
        self._runtime = runtime
        self._project_id = project_id
        self._task_id = task_id
        self._worker_id = worker_id
        self._causation_id = causation_id

    def read(self, relative_path_or_id: str) -> Optional[MemoryDocument]:
        """Read a memory document by relative path (e.g. '.autonomos/memory/architecture.md') or ID."""
        doc = self._runtime.store.get_memory_document(relative_path_or_id)
        if not doc:
            clean_rel = relative_path_or_id.lstrip("/\\")
            if not clean_rel.startswith(".autonomos"):
                clean_rel = f".autonomos/{clean_rel}"
            doc = self._runtime.store.get_memory_document_by_path(self._project_id, clean_rel)
            if not doc:
                doc = self._runtime.store.get_memory_document_by_path(self._project_id, relative_path_or_id)
        return doc

    def record_decision(
        self,
        title: str,
        context: str,
        decision: str,
        reasoning: str,
        consequences: str,
        decision_number: int = 1,
        status: str = "Accepted",
        references: Optional[list[str]] = None,
    ) -> MemoryDocument:
        """Record an Architectural Decision Record (ADR) in project memory."""
        return self._runtime.record_decision(
            project_id=self._project_id,
            title=title,
            context=context,
            decision=decision,
            reasoning=reasoning,
            consequences=consequences,
            decision_number=decision_number,
            status=status,
            references=references,
        )

    def record_issue(
        self,
        title: str,
        description: str,
        severity: IssueSeverity = IssueSeverity.MEDIUM,
        status: IssueStatus = IssueStatus.OPEN,
        affected_area: str = "",
    ) -> MemoryDocument:
        """Record an issue, defect, or tech debt item in project memory."""
        return self._runtime.record_issue(
            project_id=self._project_id,
            title=title,
            description=description,
            severity=severity,
            status=status,
            affected_area=affected_area,
            related_task_id=self._task_id,
        )

    def update_state(
        self,
        stage: str,
        completed_milestones: list[str],
        active_work: list[str],
        known_limitations: list[str],
        notes: str = "",
    ) -> MemoryDocument:
        """Update the project's current_state.md memory document."""
        return self._runtime.update_current_state(
            project_id=self._project_id,
            stage=stage,
            completed_milestones=completed_milestones,
            active_work=active_work,
            known_limitations=known_limitations,
            notes=notes,
        )


class ArtifactClient:
    """Scoped client for creating and querying task artifacts."""

    def __init__(
        self,
        runtime: WorkforceRuntime,
        project_id: str,
        task_id: str,
        worker_id: str,
        project_root: str,
        causation_id: Optional[str] = None,
    ):
        self._runtime = runtime
        self._project_id = project_id
        self._task_id = task_id
        self._worker_id = worker_id
        self._project_root = project_root
        self._causation_id = causation_id

    def create(
        self,
        artifact_type: ArtifactType,
        relative_path: str,
        description: str,
        content: Optional[bytes | str] = None,
        metadata: Optional[dict[str, Any]] = None,
    ) -> Artifact:
        """Create and register an artifact output for the active task."""
        from core.events.types import EventSource, EventType
        artifact = self._runtime.artifacts.register_artifact(
            project_id=self._project_id,
            task_id=self._task_id,
            worker_id=self._worker_id,
            artifact_type=artifact_type,
            relative_path=relative_path,
            description=description,
            content=content,
            base_dir=self._project_root,
            metadata=metadata,
        )
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
            correlation_id=self._task_id,
            causation_id=self._causation_id,
            project_id=self._project_id,
            task_id=self._task_id,
            worker_id=self._worker_id,
            artifact_id=artifact.id,
        )
        return artifact

    def get(self, artifact_id: str) -> Optional[Artifact]:
        """Retrieve an artifact by ID."""
        return self._runtime.artifacts.get_artifact(artifact_id)

    def list(self) -> list[Artifact]:
        """List all artifacts produced for this task."""
        return self._runtime.artifacts.list_artifacts_for_task(self._task_id)


class VerificationClient:
    """Scoped client for requesting authoritative verification through the Verification Engine."""

    def __init__(
        self,
        runtime: WorkforceRuntime,
        project_id: str,
        task_id: str,
        worker_id: str,
        causation_id: Optional[str] = None,
    ):
        self._runtime = runtime
        self._project_id = project_id
        self._task_id = task_id
        self._worker_id = worker_id
        self._causation_id = causation_id

    def request(
        self,
        plan: Optional[VerificationPlan] = None,
        criteria: Optional[list[SuccessCriterion]] = None,
    ) -> VerificationResult:
        """Request deterministic verification of the active task against success criteria."""
        project = self._runtime.projects.get_project(self._project_id)
        task = self._runtime.tasks.get_task(self._task_id)
        vplan = plan
        if not vplan and criteria:
            vplan = VerificationPlan(id=f"plan-worker-{self._task_id}", task_id=self._task_id, criteria=criteria)

        verification = self._runtime.verification.verify_task(
            project=project,
            task=task,
            worker_id=self._worker_id,
            plan=vplan,
            causation_id=self._causation_id,
        )
        return verification.to_result()


class SafetyClient:
    """Scoped client for interacting safely with checkpoint & safety services."""

    def __init__(
        self,
        runtime: WorkforceRuntime,
        project_id: str,
        task_id: str,
        worker_id: str,
        causation_id: Optional[str] = None,
    ):
        self._runtime = runtime
        self._project_id = project_id
        self._task_id = task_id
        self._worker_id = worker_id
        self._causation_id = causation_id

    def request_checkpoint(self, label: str = "") -> str:
        """Request that the runtime create an explicit pre-change checkpoint."""
        from core.events.types import EventType
        chk = self._runtime.checkpoints.create_checkpoint(
            project_id=self._project_id,
            task_id=self._task_id,
            worker_id=self._worker_id,
        )
        self._runtime.log_event(
            event_type=EventType.CHECKPOINT_CREATED,
            payload={"checkpoint_id": chk.id, "label": label, "checkpoint_type": chk.checkpoint_type.value},
            project_id=self._project_id,
            task_id=self._task_id,
            worker_id=self._worker_id,
            correlation_id=self._task_id,
            causation_id=self._causation_id,
        )
        return chk.id


class EventClient:
    """Scoped client for worker-originated progress and diagnostic events."""

    def __init__(
        self,
        runtime: WorkforceRuntime,
        project_id: str,
        task_id: str,
        worker_id: str,
        causation_id: Optional[str] = None,
    ):
        self._runtime = runtime
        self._project_id = project_id
        self._task_id = task_id
        self._worker_id = worker_id
        self._causation_id = causation_id

    def emit(self, event_type: str, payload: dict[str, Any]) -> None:
        """Emit a non-authoritative worker progress event."""
        from core.events.types import EventSource, EventType
        self._runtime.log_event(
            event_type=EventType.WORKER_PROGRESS_LOGGED,
            payload={"event_type": event_type, "data": payload},
            source=EventSource.WORKER,
            correlation_id=self._task_id,
            causation_id=self._causation_id,
            project_id=self._project_id,
            task_id=self._task_id,
            worker_id=self._worker_id,
        )


class LoggerClient:
    """Structured logger with correlation tracking and automatic secret scrubbing."""

    def __init__(self, event_client: EventClient, worker_id: str):
        self._events = event_client
        self._worker_id = worker_id

    def info(self, msg: str, **kwargs) -> None:
        self._events.emit("LOG_INFO", {"level": "INFO", "message": msg, **kwargs})

    def warning(self, msg: str, **kwargs) -> None:
        self._events.emit("LOG_WARNING", {"level": "WARNING", "message": msg, **kwargs})

    def error(self, msg: str, **kwargs) -> None:
        self._events.emit("LOG_ERROR", {"level": "ERROR", "message": msg, **kwargs})

    def debug(self, msg: str, **kwargs) -> None:
        self._events.emit("LOG_DEBUG", {"level": "DEBUG", "message": msg, **kwargs})


class ProgressClient:
    """Scoped client for reporting progress percentages during task execution."""

    def __init__(self, event_client: EventClient):
        self._events = event_client

    def report(self, percentage: float, message: str = "") -> None:
        """Report execution progress (0.0 to 100.0). Does not alter authoritative task status."""
        clamped = max(0.0, min(100.0, float(percentage)))
        self._events.emit("PROGRESS_REPORTED", {"percentage": clamped, "message": message})


class CancellationClient:
    """Scoped client for monitoring task cancellation."""

    def __init__(self, task_id: str, is_cancelled_fn: Callable[[], bool]):
        self._task_id = task_id
        self._is_cancelled_fn = is_cancelled_fn

    def is_cancelled(self) -> bool:
        """Check whether the active task has been marked for cancellation."""
        return self._is_cancelled_fn()

    def check(self) -> None:
        """Raise WorkerCancelledError if the active task is cancelled."""
        if self.is_cancelled():
            raise WorkerCancelledError(self._task_id)
