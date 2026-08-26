import pathlib
import shutil
import tempfile
import unittest

from core.errors import PersistenceError
from core.events.model import Event, new_event_id
from core.events.types import EventSource, EventType
from core.storage.memory_store import MemoryStore
from core.storage.sqlite_store import SQLiteStore


class TestEvents(unittest.TestCase):

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.db_path = str(pathlib.Path(self.temp_dir) / "test_events.db")
        self.memory_store = MemoryStore()
        self.sqlite_store = SQLiteStore(self.db_path)

    def tearDown(self):
        self.sqlite_store.close()
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_event_model_creation_and_serialization(self):
        evt = Event.create(
            event_type=EventType.TASK_CREATED,
            source=EventSource.TASK_ENGINE,
            project_id="proj-1",
            task_id="task-100",
            correlation_id="corr-xyz",
            causation_id="evt-parent-1",
            payload={"title": "Test Task", "priority": 1},
            metadata={"client": "cli"},
        )
        self.assertTrue(evt.event_id.startswith("evt-"))
        self.assertEqual(evt.event_type, EventType.TASK_CREATED)
        self.assertEqual(evt.correlation_id, "corr-xyz")
        self.assertEqual(evt.causation_id, "evt-parent-1")

        # Test to_dict and from_dict
        d = evt.to_dict()
        self.assertEqual(d["event_id"], evt.event_id)
        self.assertEqual(d["event_type"], "TASK_CREATED")
        self.assertEqual(d["source"], "TASK_ENGINE")

        restored = Event.from_dict(d)
        self.assertEqual(restored.event_id, evt.event_id)
        self.assertEqual(restored.event_type, EventType.TASK_CREATED)
        self.assertEqual(restored.payload["title"], "Test Task")

    def test_memory_store_event_persistence_and_ordering(self):
        self._test_store_event_persistence(self.memory_store)

    def test_sqlite_store_event_persistence_and_ordering(self):
        self._test_store_event_persistence(self.sqlite_store)

    def _test_store_event_persistence(self, store):
        e1 = Event.create(
            event_type=EventType.PROJECT_CREATED,
            project_id="p-1",
            correlation_id="c-1",
            payload={"name": "Project 1"},
        )
        e2 = Event.create(
            event_type=EventType.TASK_CREATED,
            project_id="p-1",
            task_id="t-1",
            correlation_id="c-1",
            causation_id=e1.event_id,
            payload={"title": "Task 1"},
        )
        e3 = Event.create(
            event_type=EventType.TASK_ASSIGNED,
            project_id="p-1",
            task_id="t-1",
            worker_id="w-1",
            correlation_id="c-1",
            causation_id=e2.event_id,
            payload={"worker_id": "w-1"},
        )

        p1 = store.append_event(e1)
        p2 = store.append_event(e2)
        p3 = store.append_event(e3)

        self.assertIsNotNone(p1.sequence_number)
        self.assertIsNotNone(p2.sequence_number)
        self.assertIsNotNone(p3.sequence_number)
        self.assertTrue(p1.sequence_number < p2.sequence_number < p3.sequence_number)

        # Retrieve by ID
        fetched_e2 = store.get_event(e2.event_id)
        self.assertIsNotNone(fetched_e2)
        self.assertEqual(fetched_e2.event_id, e2.event_id)
        self.assertEqual(fetched_e2.causation_id, e1.event_id)

        # Query by task
        task_events = store.get_events_by_task("t-1")
        self.assertEqual(len(task_events), 2)
        self.assertEqual([e.event_type for e in task_events], [EventType.TASK_CREATED, EventType.TASK_ASSIGNED])

        # Query by correlation ID
        corr_events = store.get_events_by_correlation_id("c-1")
        self.assertEqual(len(corr_events), 3)

        # Query with sequence filter
        after_e1 = store.list_events(since_sequence=p1.sequence_number)
        self.assertEqual(len(after_e1), 2)

    def test_immutability_duplicate_event_id_fails(self):
        for store in (self.memory_store, self.sqlite_store):
            evt = Event.create(
                event_type=EventType.TASK_CREATED,
                task_id="t-1",
                event_id="evt-fixed-id-123",
            )
            store.append_event(evt)
            # Attempt to re-append identical event ID must fail
            with self.assertRaises(PersistenceError):
                store.append_event(evt)

    def test_causal_chain_reconstruction(self):
        for store in (self.memory_store, self.sqlite_store):
            # Chain: E1 -> E2 -> E3 -> E4
            e1 = store.append_event(Event.create(event_type=EventType.TASK_CREATED, task_id="t-1"))
            e2 = store.append_event(Event.create(event_type=EventType.TASK_ASSIGNED, task_id="t-1", causation_id=e1.event_id))
            e3 = store.append_event(Event.create(event_type=EventType.TASK_STARTED, task_id="t-1", causation_id=e2.event_id))
            e4 = store.append_event(Event.create(event_type=EventType.TASK_COMPLETED, task_id="t-1", causation_id=e3.event_id))

            chain = store.get_causal_chain(e4.event_id)
            self.assertEqual(len(chain), 4)
            self.assertEqual([e.event_id for e in chain], [e1.event_id, e2.event_id, e3.event_id, e4.event_id])
            self.assertEqual([e.event_type for e in chain], [
                EventType.TASK_CREATED,
                EventType.TASK_ASSIGNED,
                EventType.TASK_STARTED,
                EventType.TASK_COMPLETED,
            ])


if __name__ == "__main__":
    unittest.main()
