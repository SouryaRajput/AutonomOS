"""
Unit tests for GeminiProvider, OpenAIProvider, and runtime registration.
"""
from __future__ import annotations

import io
import json
import unittest
import urllib.error
import urllib.request

from core.errors import AutonomOSError
from core.inference.model import InferenceMessage, InferenceRequest, ModelMetadata
from core.inference.real_providers import (
    GeminiProvider,
    HTTPInferenceError,
    OpenAIProvider,
)
from core.inference.types import (
    CostType,
    InferenceErrorCode,
    ModelCapability,
    ProviderHealthStatus,
)
from core.runtime.workforce_runtime import WorkforceRuntime
from core.storage.memory_store import MemoryStore


class TestDirectInferenceProviders(unittest.TestCase):

    def test_gemini_models_catalog(self):
        provider = GeminiProvider(api_key="test-gemini-key")
        models = provider.list_models()
        self.assertGreaterEqual(len(models), 3)

        flash = next((m for m in models if m.model_id == "gemini/gemini-2.0-flash"), None)
        self.assertIsNotNone(flash)
        self.assertTrue(flash.is_free_tier)
        self.assertIn(ModelCapability.REASONING, flash.capabilities)
        self.assertIn(ModelCapability.LONG_CONTEXT, flash.capabilities)
        self.assertGreaterEqual(flash.context_window, 1000000)

    def test_gemini_generate_successful_mock(self):
        captured_request = None

        def mock_transport(req: urllib.request.Request, timeout: float) -> bytes:
            nonlocal captured_request
            captured_request = req
            mock_resp = {
                "id": "chatcmpl-gemini-test",
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "content": "Hello from Gemini 2.0 Flash!",
                        },
                        "finish_reason": "stop",
                    }
                ],
                "usage": {
                    "prompt_tokens": 15,
                    "completion_tokens": 8,
                    "total_tokens": 23,
                },
            }
            return json.dumps(mock_resp).encode("utf-8")

        provider = GeminiProvider(api_key="mock-key-123", transport_fn=mock_transport)
        model = provider.list_models()[0]
        req = InferenceRequest(request_id="req-test", project_id="proj-test", task_id="task-test", worker_id="worker-test", 
            messages=[InferenceMessage(role="user", content="Hello!")]
        )

        resp = provider.generate(req, model)

        self.assertEqual(resp.content, "Hello from Gemini 2.0 Flash!")
        self.assertEqual(resp.model_used, "gemini/gemini-2.0-flash")
        self.assertEqual(resp.provider_used, "gemini")
        self.assertEqual(resp.usage.input_tokens, 15)
        self.assertEqual(resp.usage.output_tokens, 8)
        self.assertEqual(resp.usage.total_tokens, 23)
        self.assertEqual(provider.health(), ProviderHealthStatus.HEALTHY)

        # Verify request headers and authorization
        self.assertIsNotNone(captured_request)
        auth_header = captured_request.headers.get("Authorization") or captured_request.headers.get("authorization")
        self.assertEqual(auth_header, "Bearer mock-key-123")

    def test_gemini_generate_rate_limit(self):
        def mock_transport_429(req: urllib.request.Request, timeout: float) -> bytes:
            fp = io.BytesIO(b"Resource exhausted: 15 RPM quota exceeded")
            raise urllib.error.HTTPError(req.full_url, 429, "Too Many Requests", {}, fp)

        provider = GeminiProvider(api_key="mock-key", transport_fn=mock_transport_429)
        model = provider.list_models()[0]
        req = InferenceRequest(request_id="req-test", project_id="proj-test", task_id="task-test", worker_id="worker-test", 
            messages=[InferenceMessage(role="user", content="Test")]
        )

        with self.assertRaises(HTTPInferenceError) as ctx:
            provider.generate(req, model)
        self.assertEqual(ctx.exception.status_code, 429)
        self.assertEqual(ctx.exception.error_code, InferenceErrorCode.RATE_LIMITED)

    def test_openai_models_catalog(self):
        provider = OpenAIProvider(api_key="test-openai-key")
        models = provider.list_models()
        self.assertGreaterEqual(len(models), 3)

        gpt4o = next((m for m in models if m.model_id == "openai/gpt-4o"), None)
        self.assertIsNotNone(gpt4o)
        self.assertIn(ModelCapability.TOOL_CALLING, gpt4o.capabilities)

        o3 = next((m for m in models if m.model_id == "openai/o3-mini"), None)
        self.assertIsNotNone(o3)
        self.assertIn(ModelCapability.REASONING, o3.capabilities)

    def test_openai_generate_successful_mock(self):
        def mock_transport(req: urllib.request.Request, timeout: float) -> bytes:
            mock_resp = {
                "id": "chatcmpl-openai-test",
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "content": "Autonomous code synthesis verified.",
                        },
                        "finish_reason": "stop",
                    }
                ],
                "usage": {
                    "prompt_tokens": 100,
                    "completion_tokens": 50,
                    "total_tokens": 150,
                },
            }
            return json.dumps(mock_resp).encode("utf-8")

        provider = OpenAIProvider(api_key="sk-openai-mock", transport_fn=mock_transport)
        model = next(m for m in provider.list_models() if m.model_id == "openai/gpt-4o-mini")
        req = InferenceRequest(request_id="req-test", project_id="proj-test", task_id="task-test", worker_id="worker-test", 
            messages=[InferenceMessage(role="user", content="Solve this")]
        )

        resp = provider.generate(req, model)

        self.assertEqual(resp.content, "Autonomous code synthesis verified.")
        self.assertEqual(resp.model_used, "openai/gpt-4o-mini")
        self.assertEqual(resp.usage.total_tokens, 150)
        self.assertGreater(resp.cost.total_cost, 0.0)

    def test_missing_api_key_raises_error(self):
        import os
        # Ensure no environment key is set for test isolation
        orig = os.environ.pop("GEMINI_API_KEY", None)
        try:
            provider = GeminiProvider(api_key=None)
            model = provider.list_models()[0]
            req = InferenceRequest(request_id="req-test", project_id="proj-test", task_id="task-test", worker_id="worker-test", messages=[InferenceMessage(role="user", content="Test")])
            with self.assertRaises(AutonomOSError) as ctx:
                provider.generate(req, model)
            # AutonomOSError(message, code) convention — message is the str representation
            self.assertIn("API_KEY_MISSING", str(ctx.exception))
        finally:
            if orig:
                os.environ["GEMINI_API_KEY"] = orig

    def test_runtime_registration_of_gemini_and_openai(self):
        store = MemoryStore()
        runtime = WorkforceRuntime(store=store)

        providers = [p.provider_id for p in runtime.providers.list_providers()]
        self.assertIn("gemini", providers)
        self.assertIn("openai", providers)
        self.assertIn("groq", providers)
        self.assertIn("openrouter", providers)

        # Check models synced to model registry
        gemini_model = runtime.models.get_model("gemini/gemini-2.0-flash")
        self.assertIsNotNone(gemini_model)
        self.assertEqual(gemini_model.provider_id, "gemini")

        openai_model = runtime.models.get_model("openai/gpt-4o")
        self.assertIsNotNone(openai_model)
        self.assertEqual(openai_model.provider_id, "openai")


if __name__ == "__main__":
    unittest.main()
