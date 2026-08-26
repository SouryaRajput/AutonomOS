import unittest

from core.enums import WorkerStatus
from core.errors import InvalidWorkerTransitionError, WorkerAlreadyExistsError, WorkerNotFoundError
from core.runtime.worker_registry import WorkerRegistry
from core.storage.memory_store import MemoryStore
from workers.dummy_worker import DummyWorker


class TestWorkerRegistry(unittest.TestCase):

    def setUp(self):
        self.store = MemoryStore()
        self.registry = WorkerRegistry(self.store)

    def test_register_and_get_worker(self):
        worker = DummyWorker(worker_id="w-1", name="Worker 1")
        manifest = self.registry.register_worker(worker)
        self.assertEqual(manifest.id, "w-1")
        self.assertEqual(manifest.status, WorkerStatus.IDLE)

        fetched = self.registry.get_worker("w-1")
        self.assertEqual(fetched.name, "Worker 1")
        self.assertIn("simulation", fetched.capabilities)

    def test_register_duplicate_worker_fails(self):
        w1 = DummyWorker(worker_id="w-1")
        self.registry.register_worker(w1)
        w2 = DummyWorker(worker_id="w-1")
        with self.assertRaises(WorkerAlreadyExistsError):
            self.registry.register_worker(w2)

    def test_get_nonexistent_worker_fails(self):
        with self.assertRaises(WorkerNotFoundError):
            self.registry.get_worker("ghost-worker")

    def test_worker_lifecycle_transitions(self):
        w = DummyWorker(worker_id="w-1")
        self.registry.register_worker(w)

        # IDLE -> ASSIGNED
        self.registry.update_worker_status("w-1", WorkerStatus.ASSIGNED, active_task_id="t-1")
        w_curr = self.registry.get_worker("w-1")
        self.assertEqual(w_curr.status, WorkerStatus.ASSIGNED)
        self.assertEqual(w_curr.active_task_id, "t-1")

        # ASSIGNED -> RUNNING
        self.registry.update_worker_status("w-1", WorkerStatus.RUNNING, active_task_id="t-1")
        self.assertEqual(self.registry.get_worker("w-1").status, WorkerStatus.RUNNING)

        # RUNNING -> REPORTING
        self.registry.update_worker_status("w-1", WorkerStatus.REPORTING, active_task_id="t-1")
        self.assertEqual(self.registry.get_worker("w-1").status, WorkerStatus.REPORTING)

        # REPORTING -> IDLE
        self.registry.update_worker_status("w-1", WorkerStatus.IDLE)
        w_idle = self.registry.get_worker("w-1")
        self.assertEqual(w_idle.status, WorkerStatus.IDLE)
        self.assertIsNone(w_idle.active_task_id)

    def test_invalid_worker_transition_fails(self):
        w = DummyWorker(worker_id="w-1")
        self.registry.register_worker(w)  # IDLE

        # IDLE -> REPORTING is invalid
        with self.assertRaises(InvalidWorkerTransitionError):
            self.registry.update_worker_status("w-1", WorkerStatus.REPORTING)


if __name__ == "__main__":
    unittest.main()
