from typing import Optional

from core.enums import ArtifactType, WorkerStatus
from core.errors import WorkerNotEligibleError
from core.models import Evidence, Task, WorkerManifest, WorkerOutput
from pkg.sdk.worker import Worker, WorkerRuntimeContext


class DummyWorker(Worker):
    """
    Minimal, deterministic test worker used to verify the Stage 1 runtime.
    Does not make LLM calls or network requests.
    """

    def __init__(
        self,
        worker_id: str = "worker.dummy",
        name: str = "Dummy Test Worker",
        role: str = "Runtime Verification & Simulation",
        should_fail: bool = False,
        failure_message: str = "Simulated worker execution failure.",
        artifact_filename: str = "hello.txt",
        artifact_content: str = "Hello from AutonomOS DummyWorker!\nTask executed successfully.",
    ):
        self._manifest = WorkerManifest(
            id=worker_id,
            name=name,
            role=role,
            description="Deterministic dummy worker for testing runtime orchestration without LLM.",
            version="1.0.0",
            capabilities=["simulation", "placeholder_generation"],
            permissions=["filesystem.write", "filesystem.read"],
            tools=["filesystem.write"],
            model_policy={"model_type": "deterministic_dummy"},
            status=WorkerStatus.IDLE,
        )
        self.should_fail = should_fail
        self.failure_message = failure_message
        self.artifact_filename = artifact_filename
        self.artifact_content = artifact_content

    def get_manifest(self) -> WorkerManifest:
        return self._manifest

    def validate_task(self, task: Task) -> None:
        """Reject tasks if worker has simulated constraints."""
        if "REJECT_TASK" in task.title:
            raise WorkerNotEligibleError(self._manifest.id, "Task rejected by policy in title.")

    def execute_task(self, context: WorkerRuntimeContext, task: Task) -> WorkerOutput:
        context.log_event("DUMMY_WORK_STARTED", {"task_id": task.id, "title": task.title})

        if self.should_fail:
            context.log_event("DUMMY_WORK_FAILED", {"reason": self.failure_message})
            return WorkerOutput(
                success=False,
                summary="Execution failed intentionally in DummyWorker.",
                error_message=self.failure_message,
            )

        # 1. Create a simulated artifact
        artifact = context.create_artifact(
            artifact_type=ArtifactType.FILE,
            relative_path=self.artifact_filename,
            description=f"Generated output file for task '{task.title}'",
            content=self.artifact_content,
            metadata={"generator": self._manifest.id, "task_id": task.id},
        )

        # 2. Record simulated deterministic evidence
        evidence = context.record_evidence(
            evidence_type="DETERMINISTIC_EXECUTION_RECEIPT",
            data=f"Task '{task.id}' executed with exit_code=0; produced artifact '{artifact.id}'",
        )

        # 3. Emit progress event
        context.log_event("DUMMY_WORK_COMPLETED", {"artifact_id": artifact.id, "evidence_id": evidence.id})

        return WorkerOutput(
            success=True,
            summary=f"DummyWorker successfully executed task '{task.title}'.",
            report_markdown=f"# Task Execution Report\n\n- Task: `{task.title}`\n- Status: Success\n- Artifact: `{artifact.path}`\n",
            created_artifacts=[artifact.to_dict()],
            evidence_list=[evidence],
        )
