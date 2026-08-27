import unittest

from core.context.model import ContextBudget, ContextItem, ContextPackage
from core.context.types import ContextSourceType
from core.enums import ArtifactType, ToolStatus
from core.models import WorkerManifest, WorkerOutput, utc_now
from core.tools.model import ToolResult
from pkg.sdk.harness import WorkerTestHarness
from pkg.sdk.worker import Worker, WorkerRuntimeContext


class HarnessTestWorker(Worker):
    """Worker designed to verify all harness mocks."""

    def __init__(self):
        self._manifest = WorkerManifest(
            id="worker.harness.test",
            name="Harness Test Worker",
            role="Tester",
            description="Harness test worker",
            capabilities=["test"],
            permissions=["*"],
            created_at=utc_now(),
        )

    def get_manifest(self) -> WorkerManifest:
        return self._manifest

    def execute_task(self, context: WorkerRuntimeContext, task) -> WorkerOutput:
        # 1. Context
        ctx_pkg = context.context.get()
        assert len(ctx_pkg.items) == 1

        # 2. Tool
        tool_res = context.tools.execute("custom_mock_tool", {"param": "val"})
        assert tool_res.status == ToolStatus.SUCCESS
        assert tool_res.output == "Canned tool output"

        # 3. Inference
        inf_res = context.inference.generate([{"role": "user", "content": "Hi"}])
        assert inf_res.content == "Canned LLM answer"

        # 4. Artifact & Evidence
        art = context.artifacts.create(ArtifactType.FILE, "test.txt", "Sample", content="test")
        ev = context.record_evidence("MOCK_EVIDENCE", "Data 123")

        # 5. Events & Progress
        context.progress.report(75.0, "Progress message")
        context.log.info("Finished execution in harness")

        return WorkerOutput(
            success=True,
            summary="Harness test worker finished.",
            created_artifacts=[art.to_dict()],
            evidence_list=[ev],
        )


class TestWorkerTestHarness(unittest.TestCase):

    def test_worker_test_harness_mocking_and_recording(self):
        harness = WorkerTestHarness(worker_id="worker.harness.test")

        # Setup mock context
        harness.set_context_response(
            ContextPackage(
                request_id="ctx-test-1",
                project_id="proj-test",
                task_id="task-harness-1",
                worker_id="worker.harness.test",
                items=[
                    ContextItem(
                        id="c1",
                        source_type=ContextSourceType.DECISION,
                        source_id="mem-1",
                        title="Mock Item",
                        content="Sample content",
                    )
                ],
                total_estimated_tokens=10,
                total_characters=14,
                budget=ContextBudget(),
            )
        )

        # Setup mock tool
        harness.mock_tool(
            "custom_mock_tool",
            ToolResult(
                result_id="res-mock-1",
                request_id="req-mock-1",
                tool_id="custom_mock_tool",
                status=ToolStatus.SUCCESS,
                output="Canned tool output",
            ),
        )

        # Setup mock inference
        harness.set_inference_response("Canned LLM answer")

        worker = HarnessTestWorker()
        output = harness.run(worker)

        self.assertTrue(output.success)
        self.assertEqual(len(harness.tool_executions), 1)
        self.assertEqual(len(harness.context_requests), 1)
        self.assertEqual(len(harness.inference_requests), 1)
        self.assertEqual(len(harness.created_artifacts), 1)
        self.assertEqual(len(harness.recorded_evidence), 1)
        self.assertGreaterEqual(len(harness.emitted_events), 2)


if __name__ == "__main__":
    unittest.main()
