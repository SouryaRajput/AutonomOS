import unittest

from core.events.types import EventType
from core.inference.gateway import InferenceGateway
from core.inference.model import InferenceMessage, InferenceRequest, ModelRequirement
from core.inference.provider import MockProvider
from core.inference.registry import ModelRegistry, ProviderRegistry
from core.inference.secrets import EnvSecretStore
from core.storage.memory_store import MemoryStore


class TestInferenceFallbackIntegration(unittest.TestCase):

    def setUp(self):
        self.store = MemoryStore()
        self.providers = ProviderRegistry()
        self.models = ModelRegistry()
        self.secrets = EnvSecretStore()

        # Provider A: Fails (e.g. rate limited / server error)
        self.provider_a = MockProvider(
            provider_id="provider-a",
            display_name="Failing Provider A",
            should_fail=True,
        )
        self.providers.register_provider(self.provider_a)
        self.models.sync_from_providers([self.provider_a])

        # Provider B: Healthy fallback
        self.provider_b = MockProvider(
            provider_id="provider-b",
            display_name="Healthy Provider B",
            should_fail=False,
            custom_response="Successfully resolved via Fallback Provider B!",
        )
        self.providers.register_provider(self.provider_b)
        self.models.sync_from_providers([self.provider_b])

        self.gateway = InferenceGateway(
            provider_registry=self.providers,
            model_registry=self.models,
            secret_store=self.secrets,
            event_logger=self.store.append_event,
            max_fallback_attempts=3,
        )

    def test_automatic_omniroute_fallback_to_healthy_provider(self):
        # Prefer provider-a so it is ranked first
        req = InferenceRequest(
            request_id="req-fallback-1",
            project_id="proj-fb-1",
            task_id="task-fb-1",
            worker_id="worker-fb",
            messages=[InferenceMessage(role="user", content="Execute complex reasoning prompt.")],
            requirements=ModelRequirement(
                preferred_providers=["provider-a"],
            ),
        )

        response = self.gateway.execute(req)

        # Invariant: Response succeeded through fallback provider-b
        self.assertEqual(response.provider_used, "provider-b")
        self.assertIn("Fallback Provider B", response.content)

        # Verify complete event audit stream
        events = self.store.list_events()
        event_types = [e.event_type for e in events]

        self.assertIn(EventType.INFERENCE_REQUESTED, event_types)
        self.assertIn(EventType.INFERENCE_ROUTED, event_types)
        self.assertIn(EventType.INFERENCE_FAILED, event_types)
        self.assertIn(EventType.INFERENCE_FALLBACK, event_types)
        self.assertIn(EventType.INFERENCE_COMPLETED, event_types)


if __name__ == "__main__":
    unittest.main()
