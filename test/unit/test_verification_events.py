from pathlib import Path
import shutil
import tempfile
import unittest

from core.events.model import Event, new_event_id
from core.events.types import EventSource, EventType
from core.models import Project, Task, utc_now
from core.runtime.artifact_registry import ArtifactRegistry
from core.safety.checkpoint import CheckpointManager
from core.safety.model import SafetyConfig
from core.storage.memory_store import MemoryStore
from core.tools.runtime import ToolRuntime
from core.verification.engine import VerificationEngine
from core.verification.model import SuccessCriterion, VerificationPlan
from core.verification.types import CheckType, VerificationStatus


class TestVerificationEvents(unittest.TestCase):

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.workspace = str(Path(self.temp_dir) / "workspace")
        Path(self.workspace).mkdir(parents=True, exist_ok=True)

        self.store = MemoryStore()
        self.artifacts = ArtifactRegistry(self.store)
        self.checkpoints = CheckpointManager(self.store)
        self.tools = ToolRuntime(
            store=self.store,
            artifact_registry=self.artifacts,
            checkpoint_manager=self.checkpoints,
            safety_config=SafetyConfig(),
        )

        self.project = Project(
            id="proj-vevt-1",
            name="Event Project",
            description="Testing events",
            root_path=self.workspace,
            created_at=utc_now(),
        )
        self.store.save_project(self.project)

        self.task = Task(
            id="task-vevt-1",
            project_id="proj-vevt-1",
            title="Event Task",
            objective="Testing events",
            created_at=utc_now(),
        )
        self.store.save_task(self.task)

        def log_event(event_type, payload, **kwargs):
            evt = Event(
                event_id=new_event_id(),
                event_type=event_type,
                source=kwargs.get("source", EventSource.RUNTIME),
                payload=payload,
                project_id=kwargs.get("project_id"),
                task_id=kwargs.get("task_id"),
                worker_id=kwargs.get("worker_id"),
                correlation_id=kwargs.get("correlation_id"),
                causation_id=kwargs.get("causation_id"),
                timestamp=utc_now(),
            )
            return self.store.append_event(evt)

        self.engine = VerificationEngine(
            tool_runtime=self.tools,
            artifact_registry=self.artifacts,
            store=self.store,
            event_logger=log_event,
        )

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_verification_event_stream_and_lineage(self):
        (Path(self.workspace) / "test_file.txt").write_text("valid")

        plan = VerificationPlan(
            id="plan-ev-1",
            task_id=self.task.id,
            criteria=[
                SuccessCriterion(id="c1", description="exists", check_type=CheckType.FILE_EXISTS, parameters={"path": "test_file.txt"}, required=True),
            ],
        )

        res = self.engine.verify_task(self.project, self.task, plan=plan)
        self.assertEqual(res.status, VerificationStatus.PASSED)

        events = self.store.list_events(task_id=self.task.id)
        event_types = [e.event_type for e in events]

        self.assertIn(EventType.VERIFICATION_STARTED, event_types)
        self.assertIn(EventType.CHECK_STARTED, event_types)
        self.assertIn(EventType.CHECK_PASSED, event_types)
        self.assertIn(EventType.VERIFICATION_PASSED, event_types)

        # Check correlation id
        verif_evt = next(e for e in events if e.event_type == EventType.VERIFICATION_PASSED)
        self.assertEqual(verif_evt.correlation_id, self.task.id)


if __name__ == "__main__":
    unittest.main()
