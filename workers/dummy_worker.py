from typing import Optional

from core.enums import ArtifactType
from core.errors import WorkerNotEligibleError
from core.models import Task, WorkerManifest, WorkerOutput, utc_now
from pkg.sdk.worker import Worker, WorkerRuntimeContext


class DummyWorker(Worker):
    """
    Deterministic reference worker implementation for runtime validation, testing, and golden-path verification.
    """

    def __init__(
        self,
        worker_id: str = "worker.dummy.1",
        name: str = "Dummy Test Worker",
        description: str = "Deterministic test worker implementation",
        capabilities: Optional[list[str]] = None,
        permissions: Optional[list[str]] = None,
        artifact_filename: str = "dummy_output.txt",
        artifact_content: str = "This is a deterministic test artifact content.",
        should_fail: bool = False,
        failure_message: str = "Simulated worker execution failure.",
    ):
        self._manifest = WorkerManifest(
            id=worker_id,
            name=name,
            role="Tester",
            description=description,
            capabilities=capabilities or ["simulation", "test.dummy", "filesystem.read", "filesystem.write", "tool.execute"],
            permissions=permissions or ["*"],
            created_at=utc_now(),
        )
        self.artifact_filename = artifact_filename
        self.artifact_content = artifact_content
        self.should_fail = should_fail
        self.failure_message = failure_message

    def get_manifest(self) -> WorkerManifest:
        return self._manifest

    def validate_task(self, task: Task) -> None:
        """Reject tasks if worker has simulated constraints."""
        if "REJECT_TASK" in task.title:
            raise WorkerNotEligibleError(self._manifest.id, "Task rejected by policy in title.")

    def execute_task(self, context: WorkerRuntimeContext, task: Task) -> WorkerOutput:
        context.log_event("DUMMY_WORK_STARTED", {"task_id": task.id, "title": task.title})

        # 1. Request bounded context package via Context Engine (Stage 4)
        ctx_package = context.get_context()
        context.log_event("DUMMY_CONTEXT_RECEIVED", {"items_count": len(ctx_package.items), "tokens": ctx_package.total_estimated_tokens})

        # 2. Discover available tools & Execute tool via Tool Runtime (Stage 5)
        tools = context.list_available_tools()
        context.log_event("DUMMY_TOOLS_DISCOVERED", {"tools_count": len(tools)})

        tool_result = context.execute_tool(
            tool_id="filesystem.write_file",
            arguments={"path": self.artifact_filename, "content": self.artifact_content},
        )
        context.log_event("DUMMY_TOOL_EXECUTED", {"status": tool_result.status.value, "tool_id": tool_result.tool_id})

        if self.should_fail:
            context.log_event("DUMMY_WORK_FAILED", {"reason": self.failure_message})
            return WorkerOutput(
                success=False,
                summary="Execution failed intentionally in DummyWorker.",
                error_message=self.failure_message,
            )

        # 3. Create a registered artifact
        artifact = context.create_artifact(
            artifact_type=ArtifactType.FILE,
            relative_path=self.artifact_filename,
            description=f"Generated output file for task '{task.title}'",
            content=self.artifact_content,
            metadata={"generator": self._manifest.id, "task_id": task.id},
        )

        # 4. Record simulated deterministic evidence
        evidence = context.record_evidence(
            evidence_type="DETERMINISTIC_EXECUTION_RECEIPT",
            data=f"Task '{task.id}' executed with exit_code=0; produced artifact '{artifact.id}'",
        )

        # 5. Emit progress event
        context.log_event("DUMMY_WORK_COMPLETED", {"artifact_id": artifact.id, "evidence_id": evidence.id})

        return WorkerOutput(
            success=True,
            summary=f"DummyWorker successfully executed task '{task.title}'.",
            report_markdown=f"# Task Execution Report\n\n- Task: `{task.title}`\n- Status: Success\n- Artifact: `{artifact.path}`\n",
            created_artifacts=[artifact.to_dict()],
            evidence_list=[evidence],
        )
