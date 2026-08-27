from pathlib import Path
import shutil
import tempfile
import unittest

from core.enums import ToolStatus
from core.events.types import EventType
from core.models import Project, Task, WorkerManifest, utc_now
from core.storage.memory_store import MemoryStore
from core.tools.model import ToolRequest
from core.tools.runtime import ToolRuntime


class TestToolEventsAndLimits(unittest.TestCase):

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.workspace = str(Path(self.temp_dir) / "workspace")
        Path(self.workspace).mkdir(parents=True, exist_ok=True)

        self.store = MemoryStore()
        self.emitted_events = []

        def event_logger(**kwargs):
            from core.events.model import Event
            evt = Event.create(**kwargs)
            self.emitted_events.append(evt)
            return evt

        self.tool_runtime = ToolRuntime(store=self.store, event_logger=event_logger)

        self.project = Project(
            id="proj-ev-1",
            name="Event Project",
            description="Event logging project workspace",
            root_path=self.workspace,
            created_at=utc_now(),
        )
        self.store.save_project(self.project)

        self.task = Task(
            id="task-ev-1",
            project_id="proj-ev-1",
            title="Event Task",
            objective="Event logging task objective",
            created_at=utc_now(),
        )
        self.store.save_task(self.task)

        self.worker = WorkerManifest(
            id="worker.ev",
            name="Event Worker",
            role="Programmer",
            description="Event test worker",
            permissions=["*"],
            created_at=utc_now(),
        )
        self.store.save_worker(self.worker)

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_full_tool_event_lineage_and_causation(self):
        req = ToolRequest(
            project_id=self.project.id,
            task_id=self.task.id,
            worker_id=self.worker.id,
            tool_id="filesystem.write_file",
            arguments={"path": "notes.md", "content": "# Notes\nEvent tracking test."},
        )
        res = self.tool_runtime.execute_request(req)
        self.assertEqual(res.status, ToolStatus.SUCCESS)

        # Verify event stream
        event_types = [e.event_type for e in self.emitted_events]
        self.assertIn(EventType.TOOL_REQUESTED, event_types)
        self.assertIn(EventType.TOOL_AUTHORIZED, event_types)
        self.assertIn(EventType.TOOL_STARTED, event_types)
        self.assertIn(EventType.TOOL_COMPLETED, event_types)

        # Check correlation and causation
        started_evt = next(e for e in self.emitted_events if e.event_type == EventType.TOOL_STARTED)
        completed_evt = next(e for e in self.emitted_events if e.event_type == EventType.TOOL_COMPLETED)

        self.assertEqual(started_evt.correlation_id, self.task.id)
        self.assertEqual(completed_evt.causation_id, started_evt.event_id)

    def test_tool_output_truncation_when_exceeding_limit(self):
        large_file = Path(self.workspace) / "large.txt"
        large_file.write_text("A" * 300000)  # 300KB

        req = ToolRequest(
            project_id=self.project.id,
            task_id=self.task.id,
            worker_id=self.worker.id,
            tool_id="filesystem.read_file",
            arguments={"path": "large.txt"},
        )
        res = self.tool_runtime.execute_request(req)
        self.assertEqual(res.status, ToolStatus.SUCCESS)
        self.assertTrue(res.is_truncated)
        self.assertIn("truncated due to output limit", res.output["content"])


if __name__ == "__main__":
    unittest.main()
