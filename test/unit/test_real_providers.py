"""Unit tests for real inference providers (Groq, OpenRouter, Ollama) and OmniRoute failover."""
import json
import unittest
from unittest.mock import MagicMock, patch
import urllib.error

from core.inference.gateway import InferenceGateway
from core.inference.model import InferenceMessage, InferenceRequest, ModelMetadata
from core.inference.real_providers import (
    GroqProvider,
    HTTPInferenceError,
    OllamaProvider,
    OpenRouterProvider,
)
from core.inference.registry import ModelRegistry, ProviderRegistry
from core.inference.types import InferenceErrorCode, ModelCapability, ProviderHealthStatus


class TestRealProviders(unittest.TestCase):
    def setUp(self):
        self.groq = GroqProvider(api_key="gsk-test-key")
        self.openrouter = OpenRouterProvider(api_key="sk-or-test-key")
        self.ollama = OllamaProvider(base_url="http://localhost:11434")

    def test_provider_model_listings(self):
        groq_models = self.groq.list_models()
        self.assertGreaterEqual(len(groq_models), 3)
        groq_ids = [m.model_id for m in groq_models]
        self.assertIn("groq/llama-3.3-70b-versatile", groq_ids)
        self.assertIn("groq/llama-3.1-8b-instant", groq_ids)

        or_models = self.openrouter.list_models()
        self.assertGreaterEqual(len(or_models), 4)
        or_ids = [m.model_id for m in or_models]
        self.assertIn("openrouter/meta-llama/llama-3.3-70b-instruct:free", or_ids)
        self.assertIn("openrouter/deepseek/deepseek-r1:free", or_ids)

        ollama_models = self.ollama.list_models()
        self.assertGreaterEqual(len(ollama_models), 2)

    @patch("urllib.request.urlopen")
    def test_groq_successful_generation(self, mock_urlopen):
        # Mock HTTP response from Groq
        canned_response = {
            "id": "chatcmpl-test-123",
            "choices": [
                {
                    "message": {"role": "assistant", "content": "Hello from real Groq Llama 3.3!"},
                    "finish_reason": "stop",
                }
            ],
            "usage": {"prompt_tokens": 15, "completion_tokens": 10, "total_tokens": 25},
        }
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps(canned_response).encode("utf-8")
        mock_resp.__enter__.return_value = mock_resp
        mock_urlopen.return_value = mock_resp

        req = InferenceRequest(
            request_id="req-test-1",
            project_id="proj-test-1",
            task_id="task-test-1",
            worker_id="worker.programmer",
            messages=[InferenceMessage(role="user", content="Hello!")],
        )
        model = self.groq.list_models()[0]
        resp = self.groq.generate(req, model)

        self.assertEqual(resp.content, "Hello from real Groq Llama 3.3!")
        self.assertEqual(resp.provider_used, "groq")
        self.assertEqual(resp.usage.total_tokens, 25)

    @patch("urllib.request.urlopen")
    def test_openrouter_rate_limit_error_handling(self, mock_urlopen):
        # Mock HTTP 429 Rate Limit from OpenRouter
        err = urllib.error.HTTPError("https://openrouter.ai/api/v1", 429, "Rate limit exceeded", {}, None)
        err.read = MagicMock(return_value=b'{"error": {"message": "Rate limit exceeded"}}')
        mock_urlopen.side_effect = err

        req = InferenceRequest(
            request_id="req-test-2",
            project_id="proj-test-1",
            task_id="task-test-1",
            worker_id="worker.programmer",
            messages=[InferenceMessage(role="user", content="Hi")],
        )
        model = self.openrouter.list_models()[0]

        with self.assertRaises(HTTPInferenceError) as ctx:
            self.openrouter.generate(req, model)

        self.assertEqual(ctx.exception.status_code, 429)
        self.assertEqual(ctx.exception.error_code, InferenceErrorCode.RATE_LIMITED)
        self.assertEqual(self.openrouter.health(), ProviderHealthStatus.DEGRADED)

    def test_omniroute_automatic_failover(self):
        """When primary provider fails with 429, OmniRoute falls back to secondary provider."""
        providers = ProviderRegistry()
        models = ModelRegistry()

        # Primary: Failing Groq
        failing_groq = GroqProvider(api_key="gsk-test")
        failing_groq.generate = MagicMock(side_effect=HTTPInferenceError("groq", 429, "Rate limited", InferenceErrorCode.RATE_LIMITED))

        # Secondary: Working OpenRouter
        working_or = OpenRouterProvider(api_key="sk-or-test")
        working_resp = MagicMock()
        working_resp.content = "Response from fallback OpenRouter"
        working_resp.provider_used = "openrouter"
        working_resp.model_used = "openrouter/meta-llama/llama-3.3-70b-instruct:free"
        working_or.generate = MagicMock(return_value=working_resp)

        providers.register_provider(failing_groq)
        providers.register_provider(working_or)
        models.sync_from_providers([failing_groq, working_or])

        gateway = InferenceGateway(provider_registry=providers, model_registry=models)
        req = InferenceRequest(
            request_id="req-test-3",
            project_id="proj-test-1",
            task_id="task-test-1",
            worker_id="worker.programmer",
            messages=[InferenceMessage(role="user", content="Test fallback")],
        )

        res = gateway.execute(req)
        self.assertEqual(res.content, "Response from fallback OpenRouter")


if __name__ == "__main__":
    unittest.main()
