"""Real inference providers for AutonomOS: Groq, OpenRouter, and Local Ollama.
Uses standard library urllib for zero external runtime dependencies.
"""
from __future__ import annotations

import json
import logging
import os
import time
from typing import Any, Optional
import urllib.error
import urllib.request
import uuid

from core.errors import AutonomOSError
from core.inference.model import (
    Cost,
    InferenceMessage,
    InferenceRequest,
    InferenceResponse,
    ModelMetadata,
    Usage,
)
from core.inference.provider import BaseInferenceProvider
from core.inference.types import (
    CostType,
    InferenceErrorCode,
    ModelCapability,
    ProviderHealthStatus,
)

logger = logging.getLogger("AutonomOS.RealProviders")


class HTTPInferenceError(AutonomOSError):
    """Raised when an HTTP inference request fails."""
    def __init__(self, provider_id: str, status_code: int, message: str, error_code: InferenceErrorCode = InferenceErrorCode.SERVER_ERROR):
        super().__init__("HTTP_INFERENCE_ERROR", f"[{provider_id}] HTTP {status_code}: {message}")
        self.provider_id = provider_id
        self.status_code = status_code
        self.error_code = error_code


class GroqProvider(BaseInferenceProvider):
    """
    Groq Cloud Inference Provider — High-speed, generous free-tier Llama 3.3 and DeepSeek R1.
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        base_url: str = "https://api.groq.com/openai/v1",
    ):
        self._api_key = api_key or os.getenv("GROQ_API_KEY")
        self._base_url = base_url.rstrip("/")
        self._health_status = ProviderHealthStatus.HEALTHY
        self._call_count = 0

        self._models = [
            ModelMetadata(
                model_id="groq/llama-3.3-70b-versatile",
                provider_id="groq",
                display_name="Llama 3.3 70B Versatile (Groq)",
                capabilities={
                    ModelCapability.TEXT_GENERATION,
                    ModelCapability.CODE_GENERATION,
                    ModelCapability.REASONING,
                    ModelCapability.STRUCTURED_OUTPUT,
                    ModelCapability.LONG_CONTEXT,
                    ModelCapability.TOOL_CALLING,
                },
                context_window=128000,
                max_output_tokens=8192,
                input_cost_per_million=0.0,
                output_cost_per_million=0.0,
                is_free_tier=True,
                latency_p50_ms=180.0,
                quality_score=94.0,
            ),
            ModelMetadata(
                model_id="groq/deepseek-r1-distill-llama-70b",
                provider_id="groq",
                display_name="DeepSeek R1 Distill 70B (Groq)",
                capabilities={
                    ModelCapability.TEXT_GENERATION,
                    ModelCapability.CODE_GENERATION,
                    ModelCapability.REASONING,
                    ModelCapability.STRUCTURED_OUTPUT,
                },
                context_window=128000,
                max_output_tokens=8192,
                input_cost_per_million=0.0,
                output_cost_per_million=0.0,
                is_free_tier=True,
                latency_p50_ms=220.0,
                quality_score=96.0,
            ),
            ModelMetadata(
                model_id="groq/llama-3.1-8b-instant",
                provider_id="groq",
                display_name="Llama 3.1 8B Instant (Groq - Lightweight)",
                capabilities={
                    ModelCapability.TEXT_GENERATION,
                    ModelCapability.CODE_GENERATION,
                    ModelCapability.STRUCTURED_OUTPUT,
                },
                context_window=128000,
                max_output_tokens=8192,
                input_cost_per_million=0.0,
                output_cost_per_million=0.0,
                is_free_tier=True,
                latency_p50_ms=80.0,
                quality_score=84.0,
            ),
        ]

    @property
    def provider_id(self) -> str:
        return "groq"

    def list_models(self) -> list[ModelMetadata]:
        return list(self._models)

    def health(self) -> ProviderHealthStatus:
        return self._health_status

    def generate(
        self,
        request: InferenceRequest,
        model: ModelMetadata,
        secret: Optional[str] = None,
    ) -> InferenceResponse:
        key = secret or self._api_key or os.getenv("GROQ_API_KEY")
        if not key:
            raise AutonomOSError("API_KEY_MISSING", "Groq API key is not configured.")

        raw_model_name = model.model_id.replace("groq/", "")
        endpoint = f"{self._base_url}/chat/completions"

        messages_payload = [{"role": m.role.value if hasattr(m.role, "value") else str(m.role), "content": m.content} for m in request.messages]

        body = {
            "model": raw_model_name,
            "messages": messages_payload,
            "temperature": request.temperature,
            "max_tokens": request.max_output_tokens or 4096,
        }

        if (request.requirements and ModelCapability.STRUCTURED_OUTPUT in request.requirements.required_capabilities) or (request.metadata and request.metadata.get("response_format", {}).get("type") == "json_object"):
            body["response_format"] = {"type": "json_object"}

        start_time = time.time()
        try:
            req_data = json.dumps(body).encode("utf-8")
            http_req = urllib.request.Request(
                endpoint,
                data=req_data,
                headers={
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {key}",
                    "User-Agent": "AutonomOS/1.0",
                },
                method="POST",
            )

            with urllib.request.urlopen(http_req, timeout=30.0) as resp:
                resp_data = resp.read().decode("utf-8")
                parsed = json.loads(resp_data)

            elapsed_ms = (time.time() - start_time) * 1000.0
            self._call_count += 1
            self._health_status = ProviderHealthStatus.HEALTHY

            choice = parsed.get("choices", [{}])[0]
            content = choice.get("message", {}).get("content", "")
            finish_reason = choice.get("finish_reason", "stop")

            usage_dict = parsed.get("usage", {})
            input_tokens = usage_dict.get("prompt_tokens", request.estimated_input_tokens())
            output_tokens = usage_dict.get("completion_tokens", len(content) // 4)
            total_tokens = usage_dict.get("total_tokens", input_tokens + output_tokens)

            return InferenceResponse(
                request_id=request.request_id,
                response_id=parsed.get("id", f"groq-{uuid.uuid4().hex[:8]}"),
                content=content,
                model_used=model.model_id,
                provider_used=self.provider_id,
                finish_reason=finish_reason,
                usage=Usage(
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                    total_tokens=total_tokens,
                ),
                cost=Cost(
                    input_cost=0.0,
                    output_cost=0.0,
                    total_cost=0.0,
                    currency="USD",
                    cost_type=CostType.ACTUAL,
                ),
                latency_ms=elapsed_ms,
                metadata={"call_count": self._call_count, "raw_model": raw_model_name},
            )

        except urllib.error.HTTPError as e:
            err_body = e.read().decode("utf-8", errors="replace")
            logger.warning("Groq HTTP %s: %s", e.code, err_body)
            if e.code == 429:
                self._health_status = ProviderHealthStatus.DEGRADED
                raise HTTPInferenceError(self.provider_id, 429, "Rate limit exceeded on Groq.", InferenceErrorCode.RATE_LIMITED) from e
            elif e.code in (401, 403):
                self._health_status = ProviderHealthStatus.UNAVAILABLE
                raise HTTPInferenceError(self.provider_id, e.code, "Authentication failed with Groq key.", InferenceErrorCode.AUTHENTICATION_ERROR) from e
            else:
                self._health_status = ProviderHealthStatus.DEGRADED
                raise HTTPInferenceError(self.provider_id, e.code, err_body, InferenceErrorCode.SERVER_ERROR) from e
        except Exception as e:
            self._health_status = ProviderHealthStatus.DEGRADED
            raise AutonomOSError("GROQ_REQUEST_FAILED", f"Groq request error: {e}") from e


class OpenRouterProvider(BaseInferenceProvider):
    """
    OpenRouter Inference Provider — Multi-model aggregator connecting free & cloud models with 1 key.
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        base_url: str = "https://openrouter.ai/api/v1",
    ):
        self._api_key = api_key or os.getenv("OPENROUTER_API_KEY")
        self._base_url = base_url.rstrip("/")
        self._health_status = ProviderHealthStatus.HEALTHY
        self._call_count = 0

        self._models = [
            ModelMetadata(
                model_id="openrouter/meta-llama/llama-3.3-70b-instruct:free",
                provider_id="openrouter",
                display_name="Llama 3.3 70B Instruct (Free)",
                capabilities={
                    ModelCapability.TEXT_GENERATION,
                    ModelCapability.CODE_GENERATION,
                    ModelCapability.REASONING,
                    ModelCapability.STRUCTURED_OUTPUT,
                    ModelCapability.LONG_CONTEXT,
                },
                context_window=128000,
                max_output_tokens=8192,
                input_cost_per_million=0.0,
                output_cost_per_million=0.0,
                is_free_tier=True,
                latency_p50_ms=450.0,
                quality_score=93.0,
            ),
            ModelMetadata(
                model_id="openrouter/deepseek/deepseek-r1:free",
                provider_id="openrouter",
                display_name="DeepSeek R1 (Free)",
                capabilities={
                    ModelCapability.TEXT_GENERATION,
                    ModelCapability.CODE_GENERATION,
                    ModelCapability.REASONING,
                    ModelCapability.STRUCTURED_OUTPUT,
                },
                context_window=64000,
                max_output_tokens=8192,
                input_cost_per_million=0.0,
                output_cost_per_million=0.0,
                is_free_tier=True,
                latency_p50_ms=600.0,
                quality_score=97.0,
            ),
            ModelMetadata(
                model_id="openrouter/qwen/qwen-2.5-coder-32b-instruct:free",
                provider_id="openrouter",
                display_name="Qwen 2.5 Coder 32B (Free)",
                capabilities={
                    ModelCapability.TEXT_GENERATION,
                    ModelCapability.CODE_GENERATION,
                    ModelCapability.STRUCTURED_OUTPUT,
                },
                context_window=32768,
                max_output_tokens=8192,
                input_cost_per_million=0.0,
                output_cost_per_million=0.0,
                is_free_tier=True,
                latency_p50_ms=350.0,
                quality_score=92.0,
            ),
            ModelMetadata(
                model_id="openrouter/google/gemini-2.0-flash-exp:free",
                provider_id="openrouter",
                display_name="Gemini 2.0 Flash Exp (Free)",
                capabilities={
                    ModelCapability.TEXT_GENERATION,
                    ModelCapability.CODE_GENERATION,
                    ModelCapability.LONG_CONTEXT,
                    ModelCapability.VISION,
                },
                context_window=1048576,
                max_output_tokens=8192,
                input_cost_per_million=0.0,
                output_cost_per_million=0.0,
                is_free_tier=True,
                latency_p50_ms=300.0,
                quality_score=94.0,
            ),
            ModelMetadata(
                model_id="openrouter/meta-llama/llama-3.1-8b-instruct:free",
                provider_id="openrouter",
                display_name="Llama 3.1 8B Instruct (Free - Lightweight)",
                capabilities={
                    ModelCapability.TEXT_GENERATION,
                    ModelCapability.CODE_GENERATION,
                },
                context_window=128000,
                max_output_tokens=4096,
                input_cost_per_million=0.0,
                output_cost_per_million=0.0,
                is_free_tier=True,
                latency_p50_ms=180.0,
                quality_score=82.0,
            ),
        ]

    @property
    def provider_id(self) -> str:
        return "openrouter"

    def list_models(self) -> list[ModelMetadata]:
        return list(self._models)

    def health(self) -> ProviderHealthStatus:
        return self._health_status

    def generate(
        self,
        request: InferenceRequest,
        model: ModelMetadata,
        secret: Optional[str] = None,
    ) -> InferenceResponse:
        key = secret or self._api_key or os.getenv("OPENROUTER_API_KEY")
        if not key:
            raise AutonomOSError("API_KEY_MISSING", "OpenRouter API key is not configured.")

        raw_model_name = model.model_id.replace("openrouter/", "")
        endpoint = f"{self._base_url}/chat/completions"

        messages_payload = [{"role": m.role.value if hasattr(m.role, "value") else str(m.role), "content": m.content} for m in request.messages]

        body = {
            "model": raw_model_name,
            "messages": messages_payload,
            "temperature": request.temperature,
            "max_tokens": request.max_output_tokens or 4096,
        }

        if (request.requirements and ModelCapability.STRUCTURED_OUTPUT in request.requirements.required_capabilities) or (request.metadata and request.metadata.get("response_format", {}).get("type") == "json_object"):
            body["response_format"] = {"type": "json_object"}

        start_time = time.time()
        try:
            req_data = json.dumps(body).encode("utf-8")
            http_req = urllib.request.Request(
                endpoint,
                data=req_data,
                headers={
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {key}",
                    "HTTP-Referer": "https://github.com/SouryaRajput/AutonomOS",
                    "X-Title": "AutonomOS Workforce",
                    "User-Agent": "AutonomOS/1.0",
                },
                method="POST",
            )

            with urllib.request.urlopen(http_req, timeout=40.0) as resp:
                resp_data = resp.read().decode("utf-8")
                parsed = json.loads(resp_data)

            elapsed_ms = (time.time() - start_time) * 1000.0
            self._call_count += 1
            self._health_status = ProviderHealthStatus.HEALTHY

            choice = parsed.get("choices", [{}])[0]
            content = choice.get("message", {}).get("content", "")
            finish_reason = choice.get("finish_reason", "stop")

            usage_dict = parsed.get("usage", {})
            input_tokens = usage_dict.get("prompt_tokens", request.estimated_input_tokens())
            output_tokens = usage_dict.get("completion_tokens", len(content) // 4)
            total_tokens = usage_dict.get("total_tokens", input_tokens + output_tokens)

            return InferenceResponse(
                request_id=request.request_id,
                response_id=parsed.get("id", f"or-{uuid.uuid4().hex[:8]}"),
                content=content,
                model_used=model.model_id,
                provider_used=self.provider_id,
                finish_reason=finish_reason,
                usage=Usage(
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                    total_tokens=total_tokens,
                ),
                cost=Cost(
                    input_cost=0.0,
                    output_cost=0.0,
                    total_cost=0.0,
                    currency="USD",
                    cost_type=CostType.ACTUAL,
                ),
                latency_ms=elapsed_ms,
                metadata={"call_count": self._call_count, "raw_model": raw_model_name},
            )

        except urllib.error.HTTPError as e:
            err_body = e.read().decode("utf-8", errors="replace")
            logger.warning("OpenRouter HTTP %s: %s", e.code, err_body)
            if e.code == 429:
                self._health_status = ProviderHealthStatus.DEGRADED
                raise HTTPInferenceError(self.provider_id, 429, "Rate limit exceeded on OpenRouter.", InferenceErrorCode.RATE_LIMITED) from e
            elif e.code in (401, 403):
                self._health_status = ProviderHealthStatus.UNAVAILABLE
                raise HTTPInferenceError(self.provider_id, e.code, "Authentication failed with OpenRouter key.", InferenceErrorCode.AUTHENTICATION_ERROR) from e
            else:
                self._health_status = ProviderHealthStatus.DEGRADED
                raise HTTPInferenceError(self.provider_id, e.code, err_body, InferenceErrorCode.SERVER_ERROR) from e
        except Exception as e:
            self._health_status = ProviderHealthStatus.DEGRADED
            raise AutonomOSError("OPENROUTER_REQUEST_FAILED", f"OpenRouter request error: {e}") from e


class OllamaProvider(BaseInferenceProvider):
    """
    Local Ollama Inference Provider — 100% offline, unlimited local fallback.
    """

    def __init__(
        self,
        base_url: str = "http://localhost:11434",
    ):
        self._base_url = (os.getenv("OLLAMA_BASE_URL") or base_url).rstrip("/")
        self._health_status = ProviderHealthStatus.HEALTHY
        self._call_count = 0

        self._models = [
            ModelMetadata(
                model_id="ollama/qwen2.5-coder:7b",
                provider_id="ollama",
                display_name="Qwen 2.5 Coder 7B (Local Ollama)",
                capabilities={
                    ModelCapability.TEXT_GENERATION,
                    ModelCapability.CODE_GENERATION,
                    ModelCapability.STRUCTURED_OUTPUT,
                },
                context_window=32768,
                max_output_tokens=4096,
                input_cost_per_million=0.0,
                output_cost_per_million=0.0,
                is_free_tier=True,
                latency_p50_ms=500.0,
                quality_score=86.0,
            ),
            ModelMetadata(
                model_id="ollama/llama3.3:latest",
                provider_id="ollama",
                display_name="Llama 3.3 (Local Ollama)",
                capabilities={
                    ModelCapability.TEXT_GENERATION,
                    ModelCapability.CODE_GENERATION,
                    ModelCapability.REASONING,
                },
                context_window=64000,
                max_output_tokens=4096,
                input_cost_per_million=0.0,
                output_cost_per_million=0.0,
                is_free_tier=True,
                latency_p50_ms=800.0,
                quality_score=90.0,
            ),
        ]

    @property
    def provider_id(self) -> str:
        return "ollama"

    def list_models(self) -> list[ModelMetadata]:
        return list(self._models)

    def health(self) -> ProviderHealthStatus:
        # Check if Ollama endpoint responds
        try:
            req = urllib.request.Request(f"{self._base_url}/api/tags", method="GET")
            with urllib.request.urlopen(req, timeout=1.5) as resp:
                if resp.status == 200:
                    self._health_status = ProviderHealthStatus.HEALTHY
                    return ProviderHealthStatus.HEALTHY
        except Exception:
            self._health_status = ProviderHealthStatus.UNAVAILABLE
        return self._health_status

    def generate(
        self,
        request: InferenceRequest,
        model: ModelMetadata,
        secret: Optional[str] = None,
    ) -> InferenceResponse:
        raw_model = model.model_id.replace("ollama/", "")
        endpoint = f"{self._base_url}/api/chat"

        messages_payload = [{"role": m.role.value if hasattr(m.role, "value") else str(m.role), "content": m.content} for m in request.messages]

        body = {
            "model": raw_model,
            "messages": messages_payload,
            "stream": False,
            "options": {
                "temperature": request.temperature,
            },
        }

        if (request.requirements and ModelCapability.STRUCTURED_OUTPUT in request.requirements.required_capabilities) or (request.metadata and request.metadata.get("response_format", {}).get("type") == "json_object"):
            body["format"] = "json"

        start_time = time.time()
        try:
            req_data = json.dumps(body).encode("utf-8")
            http_req = urllib.request.Request(
                endpoint,
                data=req_data,
                headers={"Content-Type": "application/json"},
                method="POST",
            )

            with urllib.request.urlopen(http_req, timeout=90.0) as resp:
                resp_data = resp.read().decode("utf-8")
                parsed = json.loads(resp_data)

            elapsed_ms = (time.time() - start_time) * 1000.0
            self._call_count += 1
            self._health_status = ProviderHealthStatus.HEALTHY

            content = parsed.get("message", {}).get("content", "")
            input_tokens = parsed.get("prompt_eval_count", request.estimated_input_tokens())
            output_tokens = parsed.get("eval_count", len(content) // 4)

            return InferenceResponse(
                request_id=request.request_id,
                response_id=f"ollama-{uuid.uuid4().hex[:8]}",
                content=content,
                model_used=model.model_id,
                provider_used=self.provider_id,
                finish_reason="stop",
                usage=Usage(
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                    total_tokens=input_tokens + output_tokens,
                ),
                cost=Cost(
                    input_cost=0.0,
                    output_cost=0.0,
                    total_cost=0.0,
                    currency="USD",
                    cost_type=CostType.ACTUAL,
                ),
                latency_ms=elapsed_ms,
                metadata={"call_count": self._call_count, "local": True},
            )

        except urllib.error.URLError as e:
            self._health_status = ProviderHealthStatus.UNAVAILABLE
            raise AutonomOSError("OLLAMA_UNAVAILABLE", f"Ollama local service unreachable at {self._base_url}: {e}") from e
        except Exception as e:
            self._health_status = ProviderHealthStatus.DEGRADED
            raise AutonomOSError("OLLAMA_REQUEST_FAILED", f"Ollama request error: {e}") from e


class GeminiProvider(BaseInferenceProvider):
    """
    Google Gemini Inference Provider — Generative Language API.
    Uses Google's native OpenAI-compatible REST endpoint with zero extra SDK dependencies.
    Provides access to Gemini 2.0 Flash with generous free tier (15 RPM / 1,500 RPD).
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        base_url: str = "https://generativelanguage.googleapis.com/v1beta/openai",
        transport_fn: Optional[Callable[[urllib.request.Request, float], bytes]] = None,
    ):
        self._api_key = api_key or os.getenv("GEMINI_API_KEY")
        self._base_url = base_url.rstrip("/")
        self._transport_fn = transport_fn
        self._health_status = ProviderHealthStatus.HEALTHY
        self._call_count = 0

        self._models = [
            ModelMetadata(
                model_id="gemini/gemini-2.0-flash",
                provider_id="gemini",
                display_name="Gemini 2.0 Flash (Google)",
                capabilities={
                    ModelCapability.TEXT_GENERATION,
                    ModelCapability.CODE_GENERATION,
                    ModelCapability.REASONING,
                    ModelCapability.STRUCTURED_OUTPUT,
                    ModelCapability.LONG_CONTEXT,
                    ModelCapability.TOOL_CALLING,
                    ModelCapability.VISION,
                },
                context_window=1048576,
                max_output_tokens=8192,
                input_cost_per_million=0.10,
                output_cost_per_million=0.40,
                is_free_tier=True,
                latency_p50_ms=250.0,
                quality_score=95.0,
            ),
            ModelMetadata(
                model_id="gemini/gemini-2.0-flash-lite",
                provider_id="gemini",
                display_name="Gemini 2.0 Flash Lite (Google)",
                capabilities={
                    ModelCapability.TEXT_GENERATION,
                    ModelCapability.CODE_GENERATION,
                    ModelCapability.STRUCTURED_OUTPUT,
                    ModelCapability.LONG_CONTEXT,
                },
                context_window=1048576,
                max_output_tokens=8192,
                input_cost_per_million=0.075,
                output_cost_per_million=0.30,
                is_free_tier=True,
                latency_p50_ms=150.0,
                quality_score=90.0,
            ),
            ModelMetadata(
                model_id="gemini/gemini-1.5-pro",
                provider_id="gemini",
                display_name="Gemini 1.5 Pro (Google)",
                capabilities={
                    ModelCapability.TEXT_GENERATION,
                    ModelCapability.CODE_GENERATION,
                    ModelCapability.REASONING,
                    ModelCapability.STRUCTURED_OUTPUT,
                    ModelCapability.LONG_CONTEXT,
                    ModelCapability.TOOL_CALLING,
                    ModelCapability.VISION,
                },
                context_window=2097152,
                max_output_tokens=8192,
                input_cost_per_million=1.25,
                output_cost_per_million=5.00,
                is_free_tier=True,
                latency_p50_ms=600.0,
                quality_score=97.0,
            ),
        ]

    @property
    def provider_id(self) -> str:
        return "gemini"

    def list_models(self) -> list[ModelMetadata]:
        return list(self._models)

    def health(self) -> ProviderHealthStatus:
        return self._health_status

    def generate(
        self,
        request: InferenceRequest,
        model: ModelMetadata,
        secret: Optional[str] = None,
    ) -> InferenceResponse:
        key = secret or self._api_key or os.getenv("GEMINI_API_KEY")
        if not key:
            raise AutonomOSError("API_KEY_MISSING", "Gemini API key is not configured. Set 'GEMINI_API_KEY'.")

        raw_model_name = model.model_id.replace("gemini/", "")
        endpoint = f"{self._base_url}/chat/completions"

        messages_payload = [
            {"role": m.role.value if hasattr(m.role, "value") else str(m.role), "content": m.content}
            for m in request.messages
        ]

        body: dict[str, Any] = {
            "model": raw_model_name,
            "messages": messages_payload,
            "temperature": request.temperature,
            "max_tokens": request.max_output_tokens or 4096,
        }

        if (request.requirements and ModelCapability.STRUCTURED_OUTPUT in request.requirements.required_capabilities) or (
            request.metadata and request.metadata.get("response_format", {}).get("type") == "json_object"
        ):
            body["response_format"] = {"type": "json_object"}

        start_time = time.time()
        try:
            req_data = json.dumps(body).encode("utf-8")
            http_req = urllib.request.Request(
                endpoint,
                data=req_data,
                headers={
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {key}",
                    "User-Agent": "AutonomOS/1.0",
                },
                method="POST",
            )

            if self._transport_fn:
                resp_bytes = self._transport_fn(http_req, 30.0)
                resp_data = resp_bytes.decode("utf-8")
            else:
                with urllib.request.urlopen(http_req, timeout=30.0) as resp:
                    resp_data = resp.read().decode("utf-8")

            parsed = json.loads(resp_data)
            elapsed_ms = (time.time() - start_time) * 1000.0
            self._call_count += 1
            self._health_status = ProviderHealthStatus.HEALTHY

            choice = parsed.get("choices", [{}])[0]
            content = choice.get("message", {}).get("content", "")
            finish_reason = choice.get("finish_reason", "stop")

            usage_dict = parsed.get("usage", {})
            input_tokens = usage_dict.get("prompt_tokens", request.estimated_input_tokens())
            output_tokens = usage_dict.get("completion_tokens", len(content) // 4)
            total_tokens = usage_dict.get("total_tokens", input_tokens + output_tokens)

            input_cost = (input_tokens / 1_000_000) * model.input_cost_per_million
            output_cost = (output_tokens / 1_000_000) * model.output_cost_per_million

            return InferenceResponse(
                request_id=request.request_id,
                response_id=parsed.get("id", f"gemini-{uuid.uuid4().hex[:8]}"),
                content=content,
                model_used=model.model_id,
                provider_used=self.provider_id,
                finish_reason=finish_reason,
                usage=Usage(
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                    total_tokens=total_tokens,
                ),
                cost=Cost(
                    input_cost=input_cost,
                    output_cost=output_cost,
                    total_cost=input_cost + output_cost,
                    currency="USD",
                    cost_type=CostType.ESTIMATED if model.is_free_tier else CostType.ACTUAL,
                ),
                latency_ms=elapsed_ms,
                metadata={"call_count": self._call_count, "raw_model": raw_model_name},
            )

        except urllib.error.HTTPError as e:
            err_body = e.read().decode("utf-8", errors="replace")
            logger.warning("Gemini HTTP %s: %s", e.code, err_body)
            if e.code == 429:
                self._health_status = ProviderHealthStatus.DEGRADED
                raise HTTPInferenceError(self.provider_id, 429, "Rate limit exceeded on Gemini.", InferenceErrorCode.RATE_LIMITED) from e
            elif e.code in (401, 403):
                self._health_status = ProviderHealthStatus.UNAVAILABLE
                raise HTTPInferenceError(self.provider_id, e.code, "Authentication failed with Gemini key.", InferenceErrorCode.AUTHENTICATION_ERROR) from e
            else:
                self._health_status = ProviderHealthStatus.DEGRADED
                raise HTTPInferenceError(self.provider_id, e.code, err_body, InferenceErrorCode.SERVER_ERROR) from e
        except Exception as e:
            if isinstance(e, (AutonomOSError, HTTPInferenceError)):
                raise
            self._health_status = ProviderHealthStatus.DEGRADED
            raise AutonomOSError("GEMINI_REQUEST_FAILED", f"Gemini request error: {e}") from e


class OpenAIProvider(BaseInferenceProvider):
    """
    Direct OpenAI Inference Provider — Connecting to api.openai.com/v1.
    Provides direct access to GPT-4o, GPT-4o-mini, and o3-mini reasoning models.
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        base_url: str = "https://api.openai.com/v1",
        transport_fn: Optional[Callable[[urllib.request.Request, float], bytes]] = None,
    ):
        self._api_key = api_key or os.getenv("OPENAI_API_KEY")
        self._base_url = base_url.rstrip("/")
        self._transport_fn = transport_fn
        self._health_status = ProviderHealthStatus.HEALTHY
        self._call_count = 0

        self._models = [
            ModelMetadata(
                model_id="openai/gpt-4o",
                provider_id="openai",
                display_name="GPT-4o (OpenAI)",
                capabilities={
                    ModelCapability.TEXT_GENERATION,
                    ModelCapability.CODE_GENERATION,
                    ModelCapability.REASONING,
                    ModelCapability.STRUCTURED_OUTPUT,
                    ModelCapability.LONG_CONTEXT,
                    ModelCapability.TOOL_CALLING,
                    ModelCapability.VISION,
                },
                context_window=128000,
                max_output_tokens=16384,
                input_cost_per_million=2.50,
                output_cost_per_million=10.00,
                is_free_tier=False,
                latency_p50_ms=350.0,
                quality_score=97.0,
            ),
            ModelMetadata(
                model_id="openai/gpt-4o-mini",
                provider_id="openai",
                display_name="GPT-4o Mini (OpenAI)",
                capabilities={
                    ModelCapability.TEXT_GENERATION,
                    ModelCapability.CODE_GENERATION,
                    ModelCapability.REASONING,
                    ModelCapability.STRUCTURED_OUTPUT,
                    ModelCapability.LONG_CONTEXT,
                    ModelCapability.TOOL_CALLING,
                },
                context_window=128000,
                max_output_tokens=16384,
                input_cost_per_million=0.15,
                output_cost_per_million=0.60,
                is_free_tier=False,
                latency_p50_ms=180.0,
                quality_score=91.0,
            ),
            ModelMetadata(
                model_id="openai/o3-mini",
                provider_id="openai",
                display_name="o3-mini Reasoning (OpenAI)",
                capabilities={
                    ModelCapability.TEXT_GENERATION,
                    ModelCapability.CODE_GENERATION,
                    ModelCapability.REASONING,
                    ModelCapability.STRUCTURED_OUTPUT,
                },
                context_window=200000,
                max_output_tokens=100000,
                input_cost_per_million=1.10,
                output_cost_per_million=4.40,
                is_free_tier=False,
                latency_p50_ms=1200.0,
                quality_score=98.0,
            ),
        ]

    @property
    def provider_id(self) -> str:
        return "openai"

    def list_models(self) -> list[ModelMetadata]:
        return list(self._models)

    def health(self) -> ProviderHealthStatus:
        return self._health_status

    def generate(
        self,
        request: InferenceRequest,
        model: ModelMetadata,
        secret: Optional[str] = None,
    ) -> InferenceResponse:
        key = secret or self._api_key or os.getenv("OPENAI_API_KEY")
        if not key:
            raise AutonomOSError("API_KEY_MISSING", "OpenAI API key is not configured. Set 'OPENAI_API_KEY'.")

        raw_model_name = model.model_id.replace("openai/", "")
        endpoint = f"{self._base_url}/chat/completions"

        messages_payload = [
            {"role": m.role.value if hasattr(m.role, "value") else str(m.role), "content": m.content}
            for m in request.messages
        ]

        body: dict[str, Any] = {
            "model": raw_model_name,
            "messages": messages_payload,
        }

        # Temperature is not supported on o-series reasoning models
        if not raw_model_name.startswith("o"):
            body["temperature"] = request.temperature
            body["max_tokens"] = request.max_output_tokens or 4096
        else:
            if request.max_output_tokens:
                body["max_completion_tokens"] = request.max_output_tokens

        if (request.requirements and ModelCapability.STRUCTURED_OUTPUT in request.requirements.required_capabilities) or (
            request.metadata and request.metadata.get("response_format", {}).get("type") == "json_object"
        ):
            body["response_format"] = {"type": "json_object"}

        start_time = time.time()
        try:
            req_data = json.dumps(body).encode("utf-8")
            http_req = urllib.request.Request(
                endpoint,
                data=req_data,
                headers={
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {key}",
                    "User-Agent": "AutonomOS/1.0",
                },
                method="POST",
            )

            if self._transport_fn:
                resp_bytes = self._transport_fn(http_req, 45.0)
                resp_data = resp_bytes.decode("utf-8")
            else:
                with urllib.request.urlopen(http_req, timeout=45.0) as resp:
                    resp_data = resp.read().decode("utf-8")

            parsed = json.loads(resp_data)
            elapsed_ms = (time.time() - start_time) * 1000.0
            self._call_count += 1
            self._health_status = ProviderHealthStatus.HEALTHY

            choice = parsed.get("choices", [{}])[0]
            content = choice.get("message", {}).get("content", "")
            finish_reason = choice.get("finish_reason", "stop")

            usage_dict = parsed.get("usage", {})
            input_tokens = usage_dict.get("prompt_tokens", request.estimated_input_tokens())
            output_tokens = usage_dict.get("completion_tokens", len(content) // 4)
            total_tokens = usage_dict.get("total_tokens", input_tokens + output_tokens)

            input_cost = (input_tokens / 1_000_000) * model.input_cost_per_million
            output_cost = (output_tokens / 1_000_000) * model.output_cost_per_million

            return InferenceResponse(
                request_id=request.request_id,
                response_id=parsed.get("id", f"openai-{uuid.uuid4().hex[:8]}"),
                content=content,
                model_used=model.model_id,
                provider_used=self.provider_id,
                finish_reason=finish_reason,
                usage=Usage(
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                    total_tokens=total_tokens,
                ),
                cost=Cost(
                    input_cost=input_cost,
                    output_cost=output_cost,
                    total_cost=input_cost + output_cost,
                    currency="USD",
                    cost_type=CostType.ACTUAL,
                ),
                latency_ms=elapsed_ms,
                metadata={"call_count": self._call_count, "raw_model": raw_model_name},
            )

        except urllib.error.HTTPError as e:
            err_body = e.read().decode("utf-8", errors="replace")
            logger.warning("OpenAI HTTP %s: %s", e.code, err_body)
            if e.code == 429:
                self._health_status = ProviderHealthStatus.DEGRADED
                raise HTTPInferenceError(self.provider_id, 429, "Rate limit exceeded on OpenAI.", InferenceErrorCode.RATE_LIMITED) from e
            elif e.code in (401, 403):
                self._health_status = ProviderHealthStatus.UNAVAILABLE
                raise HTTPInferenceError(self.provider_id, e.code, "Authentication failed with OpenAI key.", InferenceErrorCode.AUTHENTICATION_ERROR) from e
            else:
                self._health_status = ProviderHealthStatus.DEGRADED
                raise HTTPInferenceError(self.provider_id, e.code, err_body, InferenceErrorCode.SERVER_ERROR) from e
        except Exception as e:
            if isinstance(e, (AutonomOSError, HTTPInferenceError)):
                raise
            self._health_status = ProviderHealthStatus.DEGRADED
            raise AutonomOSError("OPENAI_REQUEST_FAILED", f"OpenAI request error: {e}") from e
