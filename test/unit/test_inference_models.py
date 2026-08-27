import unittest

from core.inference.model import (
    Cost,
    InferenceMessage,
    InferenceRequest,
    InferenceResponse,
    ModelMetadata,
    ModelRequirement,
    Usage,
)
from core.inference.types import CostType, ModelCapability, RoutingProfile


class TestInferenceModels(unittest.TestCase):

    def test_usage_serialization(self):
        u = Usage(input_tokens=150, output_tokens=50, total_tokens=200, cached_tokens=20)
        d = u.to_dict()
        self.assertEqual(d["total_tokens"], 200)
        self.assertEqual(d["cached_tokens"], 20)

        restored = Usage.from_dict(d)
        self.assertEqual(restored.input_tokens, 150)
        self.assertEqual(restored.output_tokens, 50)
        self.assertEqual(restored.total_tokens, 200)

    def test_cost_serialization(self):
        c = Cost(input_cost=0.00003, output_cost=0.00006, total_cost=0.00009, currency="USD", cost_type=CostType.ESTIMATED)
        d = c.to_dict()
        self.assertEqual(d["currency"], "USD")
        self.assertEqual(d["cost_type"], "ESTIMATED")

        restored = Cost.from_dict(d)
        self.assertEqual(restored.total_cost, 0.00009)
        self.assertEqual(restored.cost_type, CostType.ESTIMATED)

    def test_model_metadata_serialization(self):
        m = ModelMetadata(
            model_id="openrouter/claude-3.5-sonnet",
            provider_id="openrouter",
            display_name="Claude 3.5 Sonnet",
            capabilities={ModelCapability.TEXT_GENERATION, ModelCapability.CODE_GENERATION, ModelCapability.VISION},
            context_window=200000,
            max_output_tokens=8192,
            input_cost_per_million=3.0,
            output_cost_per_million=15.0,
            is_free_tier=False,
            latency_p50_ms=450.0,
            quality_score=95.0,
        )
        d = m.to_dict()
        self.assertEqual(d["model_id"], "openrouter/claude-3.5-sonnet")
        self.assertIn("VISION", d["capabilities"])

        restored = ModelMetadata.from_dict(d)
        self.assertEqual(restored.model_id, m.model_id)
        self.assertTrue(ModelCapability.VISION in restored.capabilities)
        self.assertEqual(restored.context_window, 200000)

    def test_model_requirement_serialization(self):
        req = ModelRequirement(
            required_capabilities={ModelCapability.CODE_GENERATION},
            preferred_capabilities={ModelCapability.VISION},
            minimum_context=64000,
            maximum_cost=0.10,
            preferred_providers=["openrouter"],
            excluded_providers=["blocked_provider"],
            routing_profile=RoutingProfile.QUALITY_FIRST,
        )
        d = req.to_dict()
        self.assertEqual(d["routing_profile"], "QUALITY_FIRST")
        self.assertEqual(d["minimum_context"], 64000)

        restored = ModelRequirement.from_dict(d)
        self.assertEqual(restored.routing_profile, RoutingProfile.QUALITY_FIRST)
        self.assertIn(ModelCapability.CODE_GENERATION, restored.required_capabilities)

    def test_inference_request_token_estimation(self):
        req = InferenceRequest(
            request_id="req-1",
            project_id="proj-1",
            task_id="task-1",
            worker_id="worker-1",
            messages=[
                InferenceMessage(role="system", content="You are a senior python engineer."),
                InferenceMessage(role="user", content="Write a quicksort implementation in python."),
            ],
        )
        est = req.estimated_input_tokens()
        self.assertGreater(est, 10)
        self.assertLess(est, 100)


if __name__ == "__main__":
    unittest.main()
