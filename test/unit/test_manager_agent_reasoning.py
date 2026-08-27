import json
import unittest

from core.inference.gateway import InferenceGateway
from core.inference.model import InferenceResponse
from core.inference.provider import MockProvider
from core.inference.registry import ModelRegistry, ProviderRegistry
from core.inference.secrets import EnvSecretStore
from core.manager.agent import ManagerAgent
from core.manager.model import ManagerConfig, ManagerState, Plan
from core.manager.types import ConfidenceLevel, ManagerActionType
from core.models import Task, WorkerManifest


class TestManagerAgentReasoning(unittest.TestCase):

    def setUp(self):
        self.providers = ProviderRegistry()
        self.models = ModelRegistry()
        self.secrets = EnvSecretStore()

        self.mock_provider = MockProvider("mock-mgr")
        self.providers.register_provider(self.mock_provider)
        self.models.sync_from_providers([self.mock_provider])

        self.gateway = InferenceGateway(self.providers, self.models, self.secrets)
        self.config = ManagerConfig()
        self.agent = ManagerAgent(self.gateway, self.config)

    def test_manager_agent_reasoning_and_cost_tracking(self):
        # Configure mock model output
        decision_payload = {
            "reasoning_summary": "Initial project decomposition complete.",
            "confidence_level": "CERTAIN",
            "assumptions": ["Greenfield repository"],
            "risks": [],
            "plan_update": {
                "objective": "Build REST API",
                "milestones": ["Milestone 1: Database Setup"],
            },
            "actions": [
                {
                    "action_type": "CREATE_TASK",
                    "parameters": {"title": "Setup SQLite Schema", "priority": 90},
                    "rationale": "Base storage foundation",
                }
            ],
        }
        self.mock_provider.set_canned_response(json.dumps(decision_payload))

        state = ManagerState(
            project_id="proj-reasoning",
            objective="Build REST API",
            current_plan=None,
        )

        decision, response = self.agent.reason(state, cycle_id="cycle-001")
        self.assertEqual(decision.reasoning_summary, "Initial project decomposition complete.")
        self.assertEqual(decision.confidence_level, ConfidenceLevel.CERTAIN)
        self.assertEqual(len(decision.actions), 1)
        self.assertEqual(decision.actions[0].action_type, ManagerActionType.CREATE_TASK)
        self.assertIsNotNone(decision.plan_update)

        # Check token and cost accumulation
        self.assertGreater(self.agent.total_tokens_used, 0)
        self.assertGreater(self.agent.total_cost_accumulated, 0.0)


if __name__ == "__main__":
    unittest.main()
