from __future__ import annotations

import json
import tempfile
import unittest

from core.enums import ArtifactType, TaskStatus
from core.events.types import EventType
from core.inference.provider import MockProvider
from core.runtime.workforce_runtime import WorkforceRuntime
from core.storage.sqlite_store import SQLiteStore
from workers.programmer.types import ProgrammingMode, ProgrammingStatus
from workers.programmer.worker import ProgrammerWorker


class TestProgrammerGoldenPath(unittest.TestCase):
    """End-to-end integration test for the Programmer specialist worker golden path."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.store = SQLiteStore(":memory:")
        self.runtime = WorkforceRuntime(self.store)
        self.project = self.runtime.create_project(
            name="Programmer Golden Project",
            root_path=self.temp_dir.name,
            description="Implement authentication microservice",
        )

        # Register Programmer Worker
        self.programmer = ProgrammerWorker("worker.programmer.golden")
        self.runtime.register_worker(self.programmer)

        # Configure Mock LLM provider response
        provider = self.runtime.inference.providers.get_provider("mock-provider")
        if isinstance(provider, MockProvider):
            self.mock_provider = provider

    def tearDown(self):
        self.runtime.close()
        self.temp_dir.cleanup()

    def test_complete_programmer_golden_path(self):
        # 1. Prepare deterministic model synthesis response
        synthesis_json = {
            "reasoning_summary": "Implemented token-based authentication service and comprehensive test suite.",
            "file_operations": [
                {
                    "action": "WRITE",
                    "path": "src/auth/service.py",
                    "content": (
                        "import hashlib\n\n"
                        "class AuthService:\n"
                        "    def hash_password(self, password: str) -> str:\n"
                        "        return hashlib.sha256(password.encode('utf-8')).hexdigest()\n\n"
                        "    def verify_token(self, token: str) -> bool:\n"
                        "        return bool(token and token.startswith('bearer_'))\n"
                    ),
                    "description": "AuthService with password hashing and token verification",
                },
                {
                    "action": "WRITE",
                    "path": "tests/test_auth.py",
                    "content": (
                        "import unittest\n"
                        "from src.auth.service import AuthService\n\n"
                        "class TestAuthService(unittest.TestCase):\n"
                        "    def test_verify_token(self):\n"
                        "        svc = AuthService()\n"
                        "        self.assertTrue(svc.verify_token('bearer_12345'))\n"
                        "        self.assertFalse(svc.verify_token('invalid'))\n"
                    ),
                    "description": "Unit tests for AuthService",
                },
            ],
            "tests_to_run": [
                "echo 'Running auth tests... 2 passed in 0.05s'",
            ],
            "build_to_run": "echo 'Build OK'",
            "assumptions": ["Python 3.12 standard library"],
            "warnings": [],
            "self_review": {
                "checks_passed": True,
                "modified_files_in_scope": True,
                "tests_added_or_updated": True,
                "regression_risk": "LOW",
                "notes": "All unit tests and build validation passed cleanly.",
            },
            "manager_summary": "Implemented AuthService and unit tests with 100% test coverage.",
        }

        self.mock_provider.set_canned_response(json.dumps(synthesis_json))

        # 2. Create programming task
        task = self.runtime.create_task(
            project_id=self.project.id,
            title="Implement AuthService token verification",
            objective="Implement AuthService with secure token validation and password hashing",
            metadata={
                "mode": "FEATURE",
                "allowed_paths": ["src/auth/*", "tests/*"],
                "requirements": ["Password hashing with SHA-256", "Bearer token validation"],
            },
        )

        # 3. Assign and Execute Task
        self.runtime.assign_task(task.id, self.programmer.get_manifest().id)
        output = self.runtime.run_task(task.id)

        # 4. Verify Output
        self.assertTrue(output.success)
        self.assertIn("Implemented AuthService", output.summary)
        self.assertEqual(len(output.created_artifacts), 1)

        prog_result = output.metadata.get("programmer_result", {})
        self.assertEqual(prog_result.get("status"), ProgrammingStatus.SUCCESS.value)
        self.assertEqual(prog_result.get("mode"), ProgrammingMode.FEATURE.value)
        self.assertEqual(len(prog_result.get("changes", [])), 2)
        self.assertEqual(len(prog_result.get("test_results", [])), 1)

        # 5. Verify Artifact in Registry & Workspace
        art_id = output.metadata.get("report_artifact_id")
        self.assertIsNotNone(art_id)
        artifacts = self.runtime.artifacts.list_artifacts_for_task(task.id)
        self.assertTrue(len(artifacts) >= 1)
        artifact = artifacts[0]
        self.assertEqual(artifact.type, ArtifactType.REPORT)
        self.assertIn("Implementation report", artifact.description)
        self.assertIn("implementation/", artifact.path)

        # 6. Verify Evidence Trail
        events = self.runtime.get_events(project_id=self.project.id)
        evidence_events = [e for e in events if e.event_type == EventType.EVIDENCE_RECORDED]
        self.assertGreaterEqual(len(evidence_events), 3)
        ev_types = {e.payload.get("evidence_type") for e in evidence_events}
        self.assertIn("CODE_CHANGE_DIFF", ev_types)
        self.assertIn("TEST_EXECUTION_EVIDENCE", ev_types)
        self.assertIn("BUILD_EXECUTION_EVIDENCE", ev_types)

        # 7. Verify Events Lineage
        events = self.runtime.get_events(project_id=self.project.id)
        worker_events = [e.payload.get("event_type") for e in events if e.event_type == EventType.WORKER_PROGRESS_LOGGED]
        self.assertIn(EventType.PROGRAMMER_STARTED.value, worker_events)
        self.assertIn(EventType.PROGRAMMER_PLAN_CREATED.value, worker_events)
        self.assertIn(EventType.PROGRAMMER_INSPECTION_PERFORMED.value, worker_events)
        self.assertIn(EventType.PROGRAMMER_CODE_MODIFIED.value, worker_events)
        self.assertIn(EventType.PROGRAMMER_DIFF_INSPECTED.value, worker_events)
        self.assertIn(EventType.PROGRAMMER_TEST_STARTED.value, worker_events)
        self.assertIn(EventType.PROGRAMMER_TEST_COMPLETED.value, worker_events)
        self.assertIn(EventType.PROGRAMMER_BUILD_STARTED.value, worker_events)
        self.assertIn(EventType.PROGRAMMER_BUILD_COMPLETED.value, worker_events)
        self.assertIn(EventType.PROGRAMMER_SELF_REVIEW_COMPLETED.value, worker_events)
        self.assertIn(EventType.PROGRAMMER_COMPLETED.value, worker_events)


if __name__ == "__main__":
    unittest.main()
