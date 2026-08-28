"""Integration tests for the unified AutonomOSApp container."""
from __future__ import annotations

import os
import tempfile
import unittest

from app import AutonomOSApp
from core.events.types import EventType


class TestAppIntegration(unittest.TestCase):

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.temp_dir.name, "integration.db")
        self.app = AutonomOSApp.with_sqlite(self.db_path)

    def tearDown(self):
        self.app.close()
        self.temp_dir.cleanup()

    def test_full_project_workflow_and_conversation_flow(self):
        # 1. Create project
        proj = self.app.projects.create_project(
            name="Fintech Platform",
            root_path=self.temp_dir.name,
            description="Ledger and payment engine",
        )
        self.assertEqual(proj["name"], "Fintech Platform")

        # 2. Connect real-time event stream
        conn = self.app.stream.connect(client_id="ui-client-1", since_sequence=0)

        # 3. Create active conversation and post user objective
        conv = self.app.conversations.get_or_create_active_conversation(proj["id"])
        messages = self.app.conversations.post_user_message(
            conversation_id=conv.id,
            content="Build double-entry ledger module with balance invariants",
            dispatch_manager=True,
        )
        self.assertGreaterEqual(len(messages), 2)

        # 4. Check manager plan and status through application services
        status = self.app.workflows.get_manager_status(proj["id"])
        self.assertEqual(status["project_id"], proj["id"])

        plan = self.app.workflows.get_active_plan(proj["id"])
        # Active plan may be created during manager cycle
        if plan:
            self.assertEqual(plan["project_id"], proj["id"])

        # 5. Check activity feed and events
        activity = self.app.events.get_activity_feed(limit=10)
        self.assertGreater(len(activity), 0)

        # 6. Stream received events
        envelope = conn.get_next_event(timeout=1.0)
        self.assertIsNotNone(envelope)

        # 7. Workers discovery through service
        workers = self.app.workers.list_workers()
        self.assertIsInstance(workers, list)

        # 8. Models discovery
        models = self.app.providers.list_models()
        self.assertIsInstance(models, list)

        self.app.stream.disconnect("ui-client-1")


if __name__ == "__main__":
    unittest.main()
