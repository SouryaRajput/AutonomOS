import unittest

from core.enums import RiskLevel, TaskStatus
from core.errors import InvalidTaskTransitionError
from core.models import Task
from core.task.state_machine import TaskStateMachine


class TestTaskStateMachine(unittest.TestCase):

    def setUp(self):
        self.task = Task(
            id="task-1",
            project_id="proj-1",
            title="Implement Feature",
            objective="Write feature code",
            status=TaskStatus.PENDING,
            risk=RiskLevel.LOW,
        )

    def test_valid_forward_lifecycle(self):
        # PENDING -> READY
        TaskStateMachine.validate_and_transition(self.task, TaskStatus.READY)
        self.assertEqual(self.task.status, TaskStatus.READY)

        # READY -> ASSIGNED
        TaskStateMachine.validate_and_transition(self.task, TaskStatus.ASSIGNED)
        self.assertEqual(self.task.status, TaskStatus.ASSIGNED)

        # ASSIGNED -> RUNNING
        TaskStateMachine.validate_and_transition(self.task, TaskStatus.RUNNING)
        self.assertEqual(self.task.status, TaskStatus.RUNNING)
        self.assertIsNotNone(self.task.started_at)

        # RUNNING -> COMPLETED
        TaskStateMachine.validate_and_transition(self.task, TaskStatus.COMPLETED)
        self.assertEqual(self.task.status, TaskStatus.COMPLETED)
        self.assertIsNotNone(self.task.completed_at)

    def test_completed_task_cannot_return_to_running(self):
        # Enforces Rule 5
        self.task.status = TaskStatus.COMPLETED
        with self.assertRaises(InvalidTaskTransitionError):
            TaskStateMachine.validate_and_transition(self.task, TaskStatus.RUNNING)

    def test_invalid_transition_from_pending_to_completed(self):
        with self.assertRaises(InvalidTaskTransitionError):
            TaskStateMachine.validate_and_transition(self.task, TaskStatus.COMPLETED)

    def test_retry_lifecycle(self):
        self.task.status = TaskStatus.RUNNING
        # RUNNING -> RETRYING
        TaskStateMachine.validate_and_transition(self.task, TaskStatus.RETRYING)
        self.assertEqual(self.task.status, TaskStatus.RETRYING)

        # RETRYING -> READY
        TaskStateMachine.validate_and_transition(self.task, TaskStatus.READY)
        self.assertEqual(self.task.status, TaskStatus.READY)

    def test_cancellation_from_running(self):
        self.task.status = TaskStatus.RUNNING
        TaskStateMachine.validate_and_transition(self.task, TaskStatus.CANCELLED)
        self.assertEqual(self.task.status, TaskStatus.CANCELLED)
        self.assertIsNotNone(self.task.completed_at)

    def test_blocked_and_unblock_transitions(self):
        self.task.status = TaskStatus.RUNNING
        TaskStateMachine.validate_and_transition(self.task, TaskStatus.BLOCKED)
        self.assertEqual(self.task.status, TaskStatus.BLOCKED)

        TaskStateMachine.validate_and_transition(self.task, TaskStatus.READY)
        self.assertEqual(self.task.status, TaskStatus.READY)


if __name__ == "__main__":
    unittest.main()
