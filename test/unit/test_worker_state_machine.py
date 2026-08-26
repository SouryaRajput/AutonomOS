import unittest

from core.enums import WorkerStatus
from core.errors import InvalidWorkerTransitionError
from core.models import WorkerManifest
from core.worker.state_machine import WorkerStateMachine


class TestWorkerStateMachine(unittest.TestCase):

    def setUp(self):
        self.worker = WorkerManifest(
            id="worker-1",
            name="Tester Worker",
            role="Testing",
            description="Performs test execution",
            status=WorkerStatus.REGISTERED,
        )

    def test_full_worker_lifecycle(self):
        # REGISTERED -> IDLE
        WorkerStateMachine.validate_and_transition(self.worker, WorkerStatus.IDLE)
        self.assertEqual(self.worker.status, WorkerStatus.IDLE)

        # IDLE -> ASSIGNED
        WorkerStateMachine.validate_and_transition(self.worker, WorkerStatus.ASSIGNED, active_task_id="task-100")
        self.assertEqual(self.worker.status, WorkerStatus.ASSIGNED)
        self.assertEqual(self.worker.active_task_id, "task-100")

        # ASSIGNED -> RUNNING
        WorkerStateMachine.validate_and_transition(self.worker, WorkerStatus.RUNNING, active_task_id="task-100")
        self.assertEqual(self.worker.status, WorkerStatus.RUNNING)

        # RUNNING -> REPORTING
        WorkerStateMachine.validate_and_transition(self.worker, WorkerStatus.REPORTING, active_task_id="task-100")
        self.assertEqual(self.worker.status, WorkerStatus.REPORTING)

        # REPORTING -> IDLE
        WorkerStateMachine.validate_and_transition(self.worker, WorkerStatus.IDLE)
        self.assertEqual(self.worker.status, WorkerStatus.IDLE)
        self.assertIsNone(self.worker.active_task_id)

    def test_worker_failure_and_recovery(self):
        self.worker.status = WorkerStatus.RUNNING
        self.worker.active_task_id = "task-1"

        # RUNNING -> FAILED
        WorkerStateMachine.validate_and_transition(self.worker, WorkerStatus.FAILED)
        self.assertEqual(self.worker.status, WorkerStatus.FAILED)

        # FAILED -> IDLE
        WorkerStateMachine.validate_and_transition(self.worker, WorkerStatus.IDLE)
        self.assertEqual(self.worker.status, WorkerStatus.IDLE)
        self.assertIsNone(self.worker.active_task_id)

    def test_invalid_transition_rejected(self):
        self.worker.status = WorkerStatus.IDLE
        with self.assertRaises(InvalidWorkerTransitionError):
            WorkerStateMachine.validate_and_transition(self.worker, WorkerStatus.REPORTING)


if __name__ == "__main__":
    unittest.main()
