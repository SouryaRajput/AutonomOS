import unittest

from core.events.activity import ActivityItem, ActivityLevel, ActivityProjector, format_event_log_line
from core.events.model import Event
from core.events.types import EventSource, EventType


class TestActivityProjection(unittest.TestCase):

    def test_project_task_created_event(self):
        evt = Event.create(
            event_type=EventType.TASK_CREATED,
            project_id="proj-1",
            task_id="task-1",
            payload={"title": "Implement Auth", "objective": "Write JWT authentication"},
        )
        activity = ActivityProjector.project(evt)
        self.assertIsInstance(activity, ActivityItem)
        self.assertEqual(activity.level, ActivityLevel.INFO)
        self.assertIn("Implement Auth", activity.title)
        self.assertEqual(activity.icon, "task_add")

    def test_project_task_completed_event(self):
        evt = Event.create(
            event_type=EventType.TASK_COMPLETED,
            project_id="proj-1",
            task_id="task-1",
            worker_id="worker.programmer",
            payload={"summary": "JWT authentication implemented and verified"},
        )
        activity = ActivityProjector.project(evt)
        self.assertEqual(activity.level, ActivityLevel.SUCCESS)
        self.assertIn("completed successfully", activity.title)
        self.assertEqual(activity.actor, "worker.programmer")

    def test_project_worker_progress_logged(self):
        evt = Event.create(
            event_type=EventType.WORKER_PROGRESS_LOGGED,
            source=EventSource.WORKER,
            worker_id="worker.dummy",
            task_id="task-1",
            payload={"event_type": "DUMMY_STEP_1", "data": {"lines": 20}},
        )
        activity = ActivityProjector.project(evt)
        self.assertEqual(activity.icon, "worker_log")
        self.assertIn("worker.dummy", activity.title)

    def test_format_event_log_line(self):
        evt = Event.create(
            event_type=EventType.TASK_STARTED,
            task_id="t-1",
            worker_id="w-1",
            payload={"attempt": 1, "title": "Setup DB"},
        )
        evt.sequence_number = 42
        line = format_event_log_line(evt)
        self.assertIn("#0042", line)
        self.assertIn("[TASK_STARTED]", line)
        self.assertIn("task=t-1", line)
        self.assertIn("worker=w-1", line)


if __name__ == "__main__":
    unittest.main()
