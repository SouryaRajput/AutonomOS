from abc import ABC, abstractmethod
from typing import Any, Callable, Optional

from core.enums import ArtifactType
from core.models import Artifact, Evidence, Task, WorkerManifest, WorkerOutput


class WorkerRuntimeContext(ABC):
    """Execution context passed to a Worker by the Workforce Runtime."""

    @property
    @abstractmethod
    def task(self) -> Task:
        """The active task being executed."""
        pass

    @property
    @abstractmethod
    def project_id(self) -> str:
        """The project ID within which this task runs."""
        pass

    @abstractmethod
    def create_artifact(
        self,
        artifact_type: ArtifactType,
        relative_path: str,
        description: str,
        content: Optional[bytes | str] = None,
        metadata: Optional[dict[str, Any]] = None,
    ) -> Artifact:
        """
        Create and register an artifact output for the active task.
        If content is provided, writes the file to disk at relative_path.
        """
        pass

    @abstractmethod
    def record_evidence(self, evidence_type: str, data: str) -> Evidence:
        """Record concrete evidence verifying the task execution."""
        pass

    @abstractmethod
    def log_event(self, event_type: str, payload: dict[str, Any]) -> None:
        """Emit an execution event to the runtime event bus."""
        pass


class Worker(ABC):
    """
    Universal Worker interface.
    All AI workforce agents (Manager, Programmer, Researcher, Tester, Dummy) implement this contract.
    """

    @abstractmethod
    def get_manifest(self) -> WorkerManifest:
        """Return the static manifest, capabilities, and permissions for this worker."""
        pass

    def validate_task(self, task: Task) -> None:
        """
        Validate whether this worker can execute the given task.
        Override to enforce task-specific constraints or prerequisites.
        """
        manifest = self.get_manifest()
        if manifest.capabilities and task.risk:
            # Basic validation hooks
            pass

    @abstractmethod
    def execute_task(self, context: WorkerRuntimeContext, task: Task) -> WorkerOutput:
        """
        Execute the assigned task deterministically.
        Interacts exclusively via WorkerRuntimeContext.
        """
        pass

    def handle_abort(self, context: WorkerRuntimeContext, task: Task) -> None:
        """Cleanup or rollback resources if the task is aborted during execution."""
        pass
