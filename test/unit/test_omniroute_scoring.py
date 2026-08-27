import unittest

from core.inference.model import InferenceMessage, InferenceRequest, ModelRequirement
from core.inference.omniroute import NoProviderAvailableError, OmniRoute
from core.inference.provider import MockProvider
from core.inference.registry import ModelRegistry, ProviderRegistry
from core.inference.types import ModelCapability, RoutingProfile


class TestOmniRouteScoring(unittest.TestCase):

    def setUp(self):
        self.providers = ProviderRegistry()
        self.models = ModelRegistry()

        self.mock_provider = MockProvider(provider_id="provider-test")
        self.providers.register_provider(self.mock_provider)
        self.models.sync_from_providers([self.mock_provider])

    def test_vision_capability_routing(self):
        req = InferenceRequest(
            request_id="req-vis-1",
            project_id="proj-1",
            task_id="task-1",
            worker_id="worker-1",
            messages=[InferenceMessage(role="user", content="Analyze this screenshot")],
            requirements=ModelRequirement(
                required_capabilities={ModelCapability.VISION},
            ),
        )

        decision = OmniRoute.evaluate_route(req, self.models, self.providers)
        self.assertIsNotNone(decision.selected_model)
        self.assertEqual(decision.selected_model.model_id, "provider-test/free-vision")
        self.assertIn("provider-test/standard", decision.rejected_candidates)
        self.assertIn("Missing required capabilities", decision.rejected_candidates["provider-test/standard"][0])

    def test_context_window_filtering(self):
        req = InferenceRequest(
            request_id="req-ctx-1",
            project_id="proj-1",
            task_id="task-1",
            worker_id="worker-1",
            messages=[InferenceMessage(role="user", content="Process long codebase")],
            requirements=ModelRequirement(
                minimum_context=150000,
            ),
        )

        decision = OmniRoute.evaluate_route(req, self.models, self.providers)
        self.assertIsNotNone(decision.selected_model)
        self.assertEqual(decision.selected_model.model_id, "provider-test/reasoning-pro")
        self.assertIn("provider-test/standard", decision.rejected_candidates)

    def test_free_first_profile_scoring(self):
        req = InferenceRequest(
            request_id="req-free-1",
            project_id="proj-1",
            task_id="task-1",
            worker_id="worker-1",
            messages=[InferenceMessage(role="user", content="Quick simple question")],
            requirements=ModelRequirement(
                routing_profile=RoutingProfile.FREE_FIRST,
            ),
        )

        decision = OmniRoute.evaluate_route(req, self.models, self.providers)
        self.assertIsNotNone(decision.selected_model)
        self.assertTrue(decision.selected_model.is_free_tier)
        self.assertEqual(decision.selected_model.model_id, "provider-test/free-vision")

    def test_no_provider_available_when_impossible_requirements(self):
        req = InferenceRequest(
            request_id="req-impossible-1",
            project_id="proj-1",
            task_id="task-1",
            worker_id="worker-1",
            messages=[InferenceMessage(role="user", content="Audio task")],
            requirements=ModelRequirement(
                required_capabilities={ModelCapability.AUDIO_INPUT},
            ),
        )

        decision = OmniRoute.evaluate_route(req, self.models, self.providers)
        self.assertIsNone(decision.selected_model)
        self.assertEqual(len(decision.candidate_scores), 0)


if __name__ == "__main__":
    unittest.main()
