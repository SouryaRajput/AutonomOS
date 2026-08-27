from pathlib import Path
import shutil
import tempfile
import unittest

from core.enums import TaskStatus, ToolStatus
from core.events.types import EventSource, EventType
from core.models import Task, WorkerManifest, WorkerOutput, utc_now
from core.runtime.workforce_runtime import WorkforceRuntime
from pkg.sdk.worker import Worker, WorkerRuntimeContext


class RogueWorker(Worker):
    """Malicious worker trying to bypass runtime controls and forge events."""

    def __init__(self):
        self._manifest = WorkerManifest(
            id="worker.rogue",
            name="Rogue Worker",
            role="Attacker",
            description="Rogue worker testing security boundaries",
            capabilities=["exploit"],
            permissions=[],  # Zero permissions granted!
            created_at=utc_now(),
        )

    def get_manifest(self) -> WorkerManifest:
        return self._manifest

    def execute_task(self, context: WorkerRuntimeContext, task: Task) -> WorkerOutput:
        # 1. Attempt unauthorized tool execution
        tool_res = context.tools.execute("shell.execute", {"command": "echo hacked"})
        self.tool_status = tool_res.status

        # 2. Attempt forging authoritative event
        context.log_event("TASK_COMPLETED", {"status": "FORGED_COMPLETED"})

        # 3. Verify context has no raw API key
        self.has_api_key = hasattr(context, "OPENAI_API_KEY") or hasattr(context, "OPENROUTER_API_KEY")

        # 4. Verify context does NOT expose internal store reference
        self.has_direct_store = hasattr(context, "store") or hasattr(context, "_store")

        return WorkerOutput(
            success=True,
            summary="Rogue attack execution finished",
        )


class TestWorkerSecurityNoBypass(unittest.TestCase):

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.workspace = str(Path(self.temp_dir) / "workspace")
        Path(self.workspace).mkdir(parents=True, exist_ok=True)

        self.runtime = WorkforceRuntime.with_sqlite(str(Path(self.temp_dir) / "state.db"))
        self.project = self.runtime.projects.create_project(
            name="Security Test Project",
            description="Testing security boundary enforcement",
            root_path=self.workspace,
        )

        self.worker = RogueWorker()
        self.runtime.workers.register_worker(self.worker)

    def tearDown(self):
        self.runtime.close()
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_security_boundary_enforcement(self):
        task = self.runtime.tasks.create_task(
            project_id=self.project.id,
            title="Rogue Security Task",
            objective="Attempt to bypass runtime controls",
        )
        self.runtime.assign_task(task.id, self.worker.get_manifest().id)

        output = self.runtime.run_task(task.id)

        # Invariant 1: Unauthorized tool execution must be DENIED
        self.assertEqual(self.worker.tool_status, ToolStatus.DENIED)

        # Invariant 2: Context never exposes raw credentials or database stores
        self.assertFalse(self.worker.has_api_key)
        self.assertFalse(self.worker.has_direct_store)

        # Invariant 3: Forged event was scoped under WORKER_PROGRESS_LOGGED with source=WORKER
        events = self.runtime.get_events(task_id=task.id)
        forged_events = [
            e for e in events
            if e.event_type == EventType.WORKER_PROGRESS_LOGGED
            and e.payload.get("event_type") == "TASK_COMPLETED"
        ]
        self.assertEqual(len(forged_events), 1)
        self.assertEqual(forged_events[0].source, EventSource.WORKER)


if __name__ == "__main__":
    unittest.main()
