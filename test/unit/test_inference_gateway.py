import unittest

from core.events.types import EventType
from core.inference.gateway import InferenceGateway
from core.inference.model import InferenceMessage, InferenceRequest, ModelRequirement
from core.inference.omniroute import NoProviderAvailableError
from core.inference.provider import MockProvider
from core.inference.registry import ModelRegistry, ProviderRegistry
from core.inference.secrets import EnvSecretStore
from core.inference.types import ModelCapability
from core.storage.memory_store import MemoryStore


class TestInferenceGateway(unittest.TestCase):

    def setUp(self):
        self.store = MemoryStore()
        self.providers = ProviderRegistry()
        self.models = ModelRegistry()
        self.secrets = EnvSecretStore()

        self.mock_provider = MockProvider(provider_id="mock-gw")
        self.providers.register_provider(self.mock_provider, api_key_ref="env:MOCK_KEY")
        self.models.sync_from_providers([self.mock_provider])
        self.secrets.set_secret("MOCK_KEY", "sk-secret-mock-key-999")

        self.gateway = InferenceGateway(
            provider_registry=self.providers,
            model_registry=self.models,
            secret_store=self.secrets,
            event_logger=self.store.append_event,
        )

    def test_successful_inference_execution(self):
        req = InferenceRequest(
            request_id="req-gw-1",
            project_id="proj-1",
            task_id="task-1",
            worker_id="worker-1",
            messages=[InferenceMessage(role="user", content="Hello Gateway!")],
            requirements=ModelRequirement(),
        )

        resp = self.gateway.execute(req)

        self.assertEqual(resp.request_id, "req-gw-1")
        self.assertIn("Mock generated answer", resp.content)
        self.assertEqual(resp.provider_used, "mock-gw")
        self.assertGreater(resp.usage.total_tokens, 0)

        # Verify emitted events
        events = self.store.list_events()
        event_types = [e.event_type for e in events]
        self.assertIn(EventType.INFERENCE_REQUESTED, event_types)
        self.assertIn(EventType.INFERENCE_ROUTED, event_types)
        self.assertIn(EventType.INFERENCE_STARTED, event_types)
        self.assertIn(EventType.INFERENCE_COMPLETED, event_types)

    def test_no_provider_available_raises_error(self):
        req = InferenceRequest(
            request_id="req-gw-unsupported",
            project_id="proj-1",
            task_id="task-1",
            worker_id="worker-1",
            messages=[InferenceMessage(role="user", content="Audio task")],
            requirements=ModelRequirement(
                required_capabilities={ModelCapability.AUDIO_OUTPUT},
            ),
        )

        with self.assertRaises(NoProviderAvailableError):
            self.gateway.execute(req)


if __name__ == "__main__":
    unittest.main()
