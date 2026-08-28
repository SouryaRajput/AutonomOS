from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Callable, Optional

from core.context.model import ContextBudget, ContextPackage
from core.enums import ArtifactType
from core.models import Artifact, Evidence, Task, WorkerManifest, WorkerOutput
from core.tools.model import ToolDefinition, ToolResult
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
from pkg.sdk.types import WorkerConfig


class WorkerRuntimeContext(ABC):
    """
    Universal execution context passed to a Worker by the Workforce Runtime.
    Provides controlled, scoped access to Task, Context, Tools, Inference,
    Memory, Artifacts, Verification, Safety, Events, and Logging.
    """

    # --- Sub-Client Accessors ---

    @property
    @abstractmethod
    def task(self) -> Task:
        """The active task being executed."""
        pass

    @property
    def task_context(self) -> TaskContext:
        """Safe, read-oriented projection of the active task."""
        return TaskContext.from_task(self.task)

    @property
    @abstractmethod
    def project_id(self) -> str:
        """The project ID within which this task runs."""
        pass

    @property
    @abstractmethod
    def context(self) -> ContextClient:
        """Scoped client for requesting bounded context from Context Engine."""
        pass

    @property
    @abstractmethod
    def tools(self) -> ToolClient:
        """Scoped client for executing authorized tools through Tool Runtime."""
        pass

    @property
    @abstractmethod
    def inference(self) -> InferenceClient:
        """Scoped client for invoking models through Inference Gateway (OmniRoute)."""
        pass

    @property
    @abstractmethod
    def memory(self) -> MemoryClient:
        """Scoped client for interacting with Persistent Project Memory."""
        pass

    @property
    @abstractmethod
    def artifacts(self) -> ArtifactClient:
        """Scoped client for creating and querying task artifacts."""
        pass

    @property
    @abstractmethod
    def verification(self) -> VerificationClient:
        """Scoped client for requesting authoritative verification."""
        pass

    @property
    @abstractmethod
    def safety(self) -> SafetyClient:
        """Scoped client for requesting safe checkpoints."""
        pass

    @property
    @abstractmethod
    def events(self) -> EventClient:
        """Scoped client for emitting worker progress and diagnostic events."""
        pass

    @property
    @abstractmethod
    def log(self) -> LoggerClient:
        """Structured logger with automatic secret scrubbing and correlation."""
        pass

    @property
    @abstractmethod
    def progress(self) -> ProgressClient:
        """Scoped client for reporting progress percentages."""
        pass

    @property
    @abstractmethod
    def cancellation(self) -> CancellationClient:
        """Scoped client for checking task cancellation."""
        pass

    @property
    def config(self) -> WorkerConfig:
        """Worker-specific configuration."""
        return WorkerConfig()

    # --- Direct Convenience Methods (Preserving Stage 1-8 compatibility) ---

    def get_context(
        self,
        budget: Optional[ContextBudget] = None,
        focus_areas: Optional[list[str]] = None,
    ) -> ContextPackage:
        """Request a bounded, prioritized ContextPackage assembled deterministically by the Context Engine."""
        return self.context.get(budget=budget, focus_areas=focus_areas)

    def execute_tool(
        self,
        tool_id: str,
        arguments: dict[str, Any],
        timeout_seconds: Optional[int] = None,
    ) -> ToolResult:
        """Request execution of a tool through the Tool Runtime."""
        return self.tools.execute(tool_id=tool_id, arguments=arguments, timeout_seconds=timeout_seconds)

    def list_available_tools(self) -> list[ToolDefinition]:
        """Discover tools that this worker is authorized to execute."""
        return self.tools.list()

    def create_artifact(
        self,
        artifact_type: ArtifactType,
        relative_path: str,
        description: str,
        content: Optional[bytes | str] = None,
        metadata: Optional[dict[str, Any]] = None,
    ) -> Artifact:
        """Create and register an artifact output for the active task."""
        return self.artifacts.create(
            artifact_type=artifact_type,
            relative_path=relative_path,
            description=description,
            content=content,
            metadata=metadata,
        )

    @abstractmethod
    def record_evidence(self, evidence_type: str, data: str) -> Evidence:
        """Record concrete evidence verifying the task execution."""
        pass

    def request_verification(
        self,
        target_type: str = "TASK",
        target_id: Optional[str] = None,
        evidence_ids: Optional[list[str]] = None,
        notes: str = "",
        **kwargs,
    ) -> Optional[Any]:
        """Request deterministic verification of a task."""
        if hasattr(self.verification, "verify"):
            return self.verification.verify()
        return None

    def log_event(self, event_type: str, payload: dict[str, Any]) -> None:
        """Emit an execution event to the runtime event bus."""
        self.events.emit(event_type, payload)


class Worker(ABC):
    """
    Universal Worker interface.
    All AI workforce agents (Manager, Programmer, Researcher, Tester, Dummy) implement this contract.
    """

    @abstractmethod
    def get_manifest(self) -> WorkerManifest:
        """Return the static manifest, capabilities, and permissions for this worker."""
        pass

    def initialize(self) -> None:
        """Lifecycle hook called when the worker is registered or initialized by the runtime."""
        pass

    def validate_task(self, task: Task) -> None:
        """
        Validate whether this worker is eligible and capable of executing the given task.
        Raise WorkerNotEligibleError if incompatible.
        """
        pass

    def before_task(self, context: WorkerRuntimeContext, task: Task) -> None:
        """Lifecycle hook invoked immediately before task execution begins."""
        pass

    @abstractmethod
    def execute_task(self, context: WorkerRuntimeContext, task: Task) -> WorkerOutput:
        """
        Execute the task within the provided runtime context and return the structured output.
        Must NOT mutate task status directly; returning WorkerOutput allows the runtime to transition state.
        """
        pass

    def after_task(self, context: WorkerRuntimeContext, task: Task, output: WorkerOutput) -> None:
        """Lifecycle hook invoked immediately after task execution completes (or fails)."""
        pass

    def shutdown(self) -> None:
        """Lifecycle hook called when the worker is unregistered or the runtime is shutting down."""
        pass
