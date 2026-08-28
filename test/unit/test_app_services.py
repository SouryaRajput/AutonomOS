"""Unit tests for Application Layer Services."""
from __future__ import annotations

import os
import tempfile
import unittest

from app.application import AutonomOSApp
from app.dto import AppException, ErrorCode


class TestAppServices(unittest.TestCase):

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.temp_dir.name, "test_app.db")
        self.app = AutonomOSApp.with_sqlite(self.db_path)

    def tearDown(self):
        self.app.close()
        self.temp_dir.cleanup()

    def test_project_service_lifecycle(self):
        # Create
        proj = self.app.projects.create_project(
            name="Autonomous Backend",
            root_path=self.temp_dir.name,
            description="Microservice project",
        )
        self.assertEqual(proj["name"], "Autonomous Backend")
        self.assertEqual(proj["task_count"], 0)

        # Get
        fetched = self.app.projects.get_project(proj["id"])
        self.assertEqual(fetched["id"], proj["id"])

        # List
        projects = self.app.projects.list_projects()
        self.assertEqual(len(projects), 1)

        # Update
        updated = self.app.projects.update_project(proj["id"], name="Renamed Project")
        self.assertEqual(updated["name"], "Renamed Project")

        # Archive
        archived = self.app.projects.archive_project(proj["id"])
        self.assertEqual(archived["status"], "ARCHIVED")

    def test_project_validation_error(self):
        with self.assertRaises(AppException) as ctx:
            self.app.projects.create_project(name="", root_path="/tmp")
        self.assertEqual(ctx.exception.error.code, ErrorCode.VALIDATION_ERROR)

    def test_task_service_lifecycle(self):
        proj = self.app.projects.create_project(
            name="Task Proj",
            root_path=self.temp_dir.name,
        )
        task = self.app.tasks.create_task(
            project_id=proj["id"],
            title="Implement OAuth",
            objective="Add Google login",
            priority=2,
            risk="LOW",
        )
        self.assertEqual(task["title"], "Implement OAuth")
        self.assertEqual(task["status"], "READY")

        tasks = self.app.tasks.list_tasks(project_id=proj["id"])
        self.assertEqual(len(tasks), 1)

        fetched = self.app.tasks.get_task(task["id"])
        self.assertEqual(fetched["id"], task["id"])

    def test_conversation_service_and_persistence(self):
        proj = self.app.projects.create_project(
            name="Conv Proj",
            root_path=self.temp_dir.name,
        )
        conv = self.app.conversations.get_or_create_active_conversation(proj["id"])
        self.assertTrue(conv.is_active)

        # Post user message with manager dispatch
        messages = self.app.conversations.post_user_message(
            conversation_id=conv.id,
            content="Create a user registration endpoint",
            dispatch_manager=True,
        )
        self.assertGreaterEqual(len(messages), 2)  # User message + Manager message

        # Verify conversation persistence by reloading from fresh get
        reloaded = self.app.conversations.get_conversation(conv.id)
        self.assertEqual(len(reloaded.messages), len(messages))
        self.assertEqual(reloaded.messages[0].content, "Create a user registration endpoint")
        self.assertEqual(reloaded.messages[0].sender, "user")

    def test_autonomy_policy_and_emergency_stop(self):
        proj = self.app.projects.create_project(
            name="Policy Proj",
            root_path=self.temp_dir.name,
        )
        policy = self.app.policies.get_policy(proj["id"])
        self.assertIsNotNone(policy)

        # Emergency stop
        stop_res = self.app.policies.emergency_stop(proj["id"], reason="Manual user test stop")
        self.assertTrue(stop_res["stopped"])
        self.assertTrue(self.app.policies.is_emergency_stopped())

        # Clear emergency stop
        clear_res = self.app.policies.clear_emergency_stop(proj["id"])
        self.assertFalse(clear_res["stopped"])
        self.assertFalse(self.app.policies.is_emergency_stopped())


if __name__ == "__main__":
    unittest.main()
