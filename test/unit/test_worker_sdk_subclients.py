from pathlib import Path
import shutil
import tempfile
import unittest

from core.context.model import ContextBudget
from core.enums import ArtifactType, RiskLevel, TaskStatus
from core.models import Project, Task, WorkerManifest, utc_now
from core.runtime.workforce_runtime import WorkforceRuntime
from pkg.sdk.errors import WorkerCancelledError
from pkg.sdk.subclients import TaskContext


class TestWorkerSDKSubclients(unittest.TestCase):

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.workspace = str(Path(self.temp_dir) / "workspace")
        Path(self.workspace).mkdir(parents=True, exist_ok=True)

        self.runtime = WorkforceRuntime.with_sqlite(str(Path(self.temp_dir) / "state.db"))
        self.project = self.runtime.projects.create_project(
            name="SDK Subclient Project",
            description="Testing worker subclients",
            root_path=self.workspace,
        )

        self.task = self.runtime.tasks.create_task(
            project_id=self.project.id,
            title="Subclient Task",
            objective="Test subclients",
        )
        self.worker = WorkerManifest(
            id="test-worker",
            name="Test Worker",
            role="Tester",
            description="Testing worker subclients",
            permissions=["*"],
            created_at=utc_now(),
        )
        self.runtime.store.save_worker(self.worker)

    def tearDown(self):
        self.runtime.close()
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_task_context_safe_projection(self):
        tctx = TaskContext.from_task(self.task)
        self.assertEqual(tctx.id, self.task.id)
        self.assertEqual(tctx.title, self.task.title)
        self.assertEqual(tctx.project_id, self.project.id)
        self.assertEqual(tctx.risk, RiskLevel.LOW)

    def test_subclients_through_runtime_context(self):
        from core.runtime.workforce_runtime import _DefaultWorkerRuntimeContext

        ctx = _DefaultWorkerRuntimeContext(
            task=self.task,
            project=self.project,
            artifact_registry=self.runtime.artifacts,
            worker_id="test-worker",
            runtime=self.runtime,
        )

        # 1. Context Client
        ctx_pkg = ctx.context.get(budget=ContextBudget(max_tokens=1000))
        self.assertIsNotNone(ctx_pkg)

        # 2. Tool Client
        tool_res = ctx.tools.execute("filesystem.write_file", {"path": "sub_test.txt", "content": "hello subclient\n"})
        self.assertTrue((Path(self.workspace) / "sub_test.txt").exists())

        # 3. Inference Client
        inf_resp = ctx.inference.generate([{"role": "user", "content": "hello"}])
        self.assertIsNotNone(inf_resp)
        self.assertGreater(inf_resp.usage.total_tokens, 0)

        # 4. Memory Client
        doc = ctx.memory.record_decision(
            title="Adopt Subclients",
            context="Testing SDK subclients",
            decision="Subclients provide clean separation",
            reasoning="Separation of concerns",
            consequences="Modular worker code",
        )
        self.assertIsNotNone(doc)
        read_doc = ctx.memory.read(doc.id)
        self.assertIn("Adopt Subclients", read_doc.title)

        # 5. Artifact Client
        art = ctx.artifacts.create(ArtifactType.FILE, "sub_artifact.txt", "Test artifact", content="Artifact content")
        self.assertTrue(art.path.endswith("sub_artifact.txt"))

        # 6. Verification Client
        verif = ctx.verification.request()
        self.assertIsNotNone(verif)

        # 7. Safety Client
        chk_id = ctx.safety.request_checkpoint("test_checkpoint")
        self.assertTrue(chk_id.startswith("chk-"))

        # 8. Events & Logging & Progress
        ctx.log.info("Test log message", key="val")
        ctx.progress.report(50.0, "Halfway done")

        # 9. Cancellation Client
        self.assertFalse(ctx.cancellation.is_cancelled())
        ctx.cancellation.check()  # should not raise

        # Mark task cancelled
        self.task.status = TaskStatus.CANCELLED
        self.runtime.store.save_task(self.task)

        self.assertTrue(ctx.cancellation.is_cancelled())
        with self.assertRaises(WorkerCancelledError):
            ctx.cancellation.check()


if __name__ == "__main__":
    unittest.main()
