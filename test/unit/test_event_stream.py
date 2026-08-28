"""Unit tests for Event Stream and real-time delivery."""
from __future__ import annotations

import os
import tempfile
import unittest

from app.application import AutonomOSApp
from core.events.types import EventType, EventSource


class TestEventStream(unittest.TestCase):

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.temp_dir.name, "test_stream.db")
        self.app = AutonomOSApp.with_sqlite(self.db_path)

    def tearDown(self):
        self.app.close()
        self.temp_dir.cleanup()

    def test_live_event_delivery(self):
        conn = self.app.stream.connect(client_id="client-1", since_sequence=0)
        self.assertTrue(conn.is_connected)

        # Log event in runtime
        self.app.runtime.log_event(
            event_type=EventType.TASK_CREATED,
            payload={"title": "Streamed Task"},
            source=EventSource.USER,
        )

        # Receive on stream
        envelope = conn.get_next_event(timeout=2.0)
        self.assertIsNotNone(envelope)
        self.assertEqual(envelope.type, "event")
        self.assertEqual(envelope.data["event_type"], EventType.TASK_CREATED.value)

        # Disconnect
        self.app.stream.disconnect("client-1")
        self.assertFalse(conn.is_connected)

    def test_reconnect_and_catchup_from_sequence(self):
        # Create some events first
        evt1 = self.app.runtime.log_event(
            event_type=EventType.PROJECT_CREATED,
            payload={"name": "P1"},
        )
        evt2 = self.app.runtime.log_event(
            event_type=EventType.TASK_CREATED,
            payload={"name": "T1"},
        )

        # Connect with since_sequence = 1 (should get evt2)
        conn = self.app.stream.connect(client_id="client-2", since_sequence=1)
        envelope = conn.get_next_event(timeout=1.0)
        self.assertIsNotNone(envelope)
        self.assertEqual(envelope.data["event_id"], evt2.event_id)

        self.app.stream.disconnect("client-2")


if __name__ == "__main__":
    unittest.main()
