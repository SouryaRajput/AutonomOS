import unittest

from core.inference.model import ModelMetadata
from core.inference.provider import MockProvider
from core.inference.registry import (
    ModelNotFoundError,
    ModelRegistry,
    ProviderNotFoundError,
    ProviderRegistry,
)
from core.inference.types import ModelCapability


class TestProviderAndModelRegistry(unittest.TestCase):

    def test_provider_registration_and_retrieval(self):
        reg = ProviderRegistry()
        p1 = MockProvider(provider_id="provider-a", display_name="Provider A")

        reg.register_provider(p1, api_key_ref="env:PROVIDER_A_KEY")
        self.assertEqual(len(reg.list_providers()), 1)
        self.assertEqual(reg.get_provider_key_ref("provider-a"), "env:PROVIDER_A_KEY")

        retrieved = reg.get_provider("provider-a")
        self.assertEqual(retrieved.provider_id, "provider-a")

        # Non-existent provider
        with self.assertRaises(ProviderNotFoundError):
            reg.get_provider("nonexistent")

        # Unregister
        self.assertTrue(reg.unregister_provider("provider-a"))
        self.assertEqual(len(reg.list_providers()), 0)

    def test_model_registry_sync_and_filtering(self):
        m_reg = ModelRegistry()
        p1 = MockProvider(provider_id="mock-1")
        m_reg.sync_from_providers([p1])

        all_models = m_reg.list_models()
        self.assertGreaterEqual(len(all_models), 3)

        # Filter by capability (VISION)
        vision_models = m_reg.list_models(capability=ModelCapability.VISION)
        self.assertEqual(len(vision_models), 1)
        self.assertEqual(vision_models[0].model_id, "mock-1/free-vision")

        # Filter by minimum context
        large_ctx_models = m_reg.list_models(min_context=150000)
        self.assertEqual(len(large_ctx_models), 1)
        self.assertEqual(large_ctx_models[0].model_id, "mock-1/reasoning-pro")

        # Non-existent model
        with self.assertRaises(ModelNotFoundError):
            m_reg.get_model("nonexistent/model")


if __name__ == "__main__":
    unittest.main()
