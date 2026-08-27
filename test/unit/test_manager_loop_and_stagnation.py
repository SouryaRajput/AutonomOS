import json
import unittest

from core.events.types import EventType
from core.inference.provider import MockProvider
from core.manager.model import ManagerConfig
from core.manager.types import ManagerActionType
from core.runtime.workforce_runtime import WorkforceRuntime
from core.storage.sqlite_store import SQLiteStore


class TestManagerLoopAndStagnation(unittest.TestCase):

    def setUp(self):
        self.store = SQLiteStore(":memory:")
        self.runtime = WorkforceRuntime(self.store)
        self.project = self.runtime.create_project("Stagnation Test", "Test Loop Protections")

        provider = self.runtime.providers.get_provider("mock-provider")
        if isinstance(provider, MockProvider):
            self.mock_provider = provider

    def tearDown(self):
        self.runtime.close()

    def test_consecutive_idle_cycles_trigger_stagnation(self):
        # Configure ManagerConfig with max_cycles_without_progress = 3
        self.runtime.manager.config.max_cycles_without_progress = 3

        # Model repeatedly returns WAIT (no tasks created, no progress)
        wait_decision = {
            "reasoning_summary": "Nothing to do right now, waiting.",
            "confidence_level": "CERTAIN",
            "actions": [
                {
                    "action_type": "WAIT",
                    "parameters": {"reason": "Idle waiting"},
                    "rationale": "No tasks ready",
                }
            ],
        }
        self.mock_provider.set_canned_response(json.dumps(wait_decision))

        # Cycle 1
        res1 = self.runtime.step_manager(self.project.id)
        self.assertFalse(res1.escalated)

        # Cycle 2
        res2 = self.runtime.step_manager(self.project.id)
        self.assertFalse(res2.escalated)

        # Cycle 3: Hits stagnation limit
        res3 = self.runtime.step_manager(self.project.id)
        self.assertTrue(res3.escalated)

        # Check stagnation event
        events = self.runtime.get_events(project_id=self.project.id)
        event_types = [e.event_type for e in events]
        self.assertIn(EventType.MANAGER_STAGNATION_DETECTED, event_types)

    def test_cost_limit_halts_execution(self):
        # Set very low max_cost_per_project
        self.runtime.manager.config.max_cost_per_project = 0.0001
        self.runtime.manager.agent.total_cost_accumulated = 0.0002  # Force budget breach

        res = self.runtime.step_manager(self.project.id)
        self.assertTrue(res.escalated)
        self.assertIn("Cost budget", res.status_summary)

        events = self.runtime.get_events(project_id=self.project.id)
        event_types = [e.event_type for e in events]
        self.assertIn(EventType.MANAGER_ESCALATED, event_types)


if __name__ == "__main__":
    unittest.main()
