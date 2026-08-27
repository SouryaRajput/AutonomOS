import json
import unittest

from core.events.types import EventType
from core.inference.provider import MockProvider
from core.manager.types import AutonomyLevel, ConfidenceLevel, ManagerActionType
from core.runtime.workforce_runtime import WorkforceRuntime
from core.storage.sqlite_store import SQLiteStore
from workers.dummy_worker import DummyWorker


class TestManagerUserInputAndAutonomy(unittest.TestCase):

    def setUp(self):
        self.store = SQLiteStore(":memory:")
        self.runtime = WorkforceRuntime(self.store)
        self.project = self.runtime.create_project("Auth Project", "Build authentication layer")
        self.worker = DummyWorker("worker-auth", "Auth Engineer")
        self.runtime.register_worker(self.worker)

        provider = self.runtime.providers.get_provider("mock-provider")
        if isinstance(provider, MockProvider):
            self.mock_provider = provider

    def tearDown(self):
        self.runtime.close()

    def test_manager_requests_user_input_and_resumes_on_feedback(self):
        # 1. Manager identifies missing auth provider requirement
        decision_1 = {
            "reasoning_summary": "Need clarification on auth provider selection before creating tasks.",
            "confidence_level": "NEEDS_INFORMATION",
            "actions": [
                {
                    "action_type": "REQUEST_USER_INPUT",
                    "parameters": {
                        "question": "Which authentication provider should be integrated?",
                        "reason": "Specification does not indicate OAuth vs Custom JWT vs Firebase",
                        "options": ["Firebase Auth", "Custom JWT", "Auth0"],
                    },
                    "rationale": "Clarify critical architecture dependency",
                }
            ],
        }
        self.mock_provider.set_canned_response(json.dumps(decision_1))

        res1 = self.runtime.step_manager(self.project.id)
        self.assertTrue(res1.user_input_required)

        # Check status shows pending user input
        status = self.runtime.get_manager_status(self.project.id)
        self.assertTrue(status.pending_user_input)

        # Check event
        events = self.runtime.get_events(project_id=self.project.id)
        event_types = [e.event_type for e in events]
        self.assertIn(EventType.MANAGER_USER_INPUT_REQUIRED, event_types)

        # 2. User provides feedback: "Use Custom JWT"
        decision_2 = {
            "reasoning_summary": "User selected Custom JWT. Proceeding to create JWT auth task.",
            "confidence_level": "CERTAIN",
            "actions": [
                {
                    "action_type": "CREATE_TASK",
                    "parameters": {
                        "title": "Implement Custom JWT Authentication",
                        "objective": "Build token issuance and verification endpoints",
                        "priority": 85,
                    },
                    "rationale": "Directly requested by user feedback",
                }
            ],
        }
        self.mock_provider.set_canned_response(json.dumps(decision_2))

        res2 = self.runtime.step_manager(self.project.id, feedback="User decided: Use Custom JWT.")
        self.assertTrue(res2.progress_detected)

        tasks = self.runtime.tasks.list_tasks(self.project.id)
        self.assertEqual(len(tasks), 1)
        self.assertEqual(tasks[0].title, "Implement Custom JWT Authentication")


if __name__ == "__main__":
    unittest.main()
