from __future__ import annotations

from abc import ABC, abstractmethod
import time
from typing import Any, Iterator, Optional
import uuid

from core.inference.model import (
    Cost,
    InferenceMessage,
    InferenceRequest,
    InferenceResponse,
    ModelMetadata,
    Usage,
)
from core.inference.types import (
    CostType,
    InferenceErrorCode,
    ModelCapability,
    ProviderHealthStatus,
)


class BaseInferenceProvider(ABC):
    """Abstract interface for all external LLM / inference providers."""

    @property
    @abstractmethod
    def provider_id(self) -> str:
        """Unique provider identifier (e.g. 'openrouter', 'openai', 'anthropic', 'mock')."""
        pass

    @abstractmethod
    def list_models(self) -> list[ModelMetadata]:
        """Return available models and their capability metadata."""
        pass

    @abstractmethod
    def generate(
        self,
        request: InferenceRequest,
        model: ModelMetadata,
        secret: Optional[str] = None,
    ) -> InferenceResponse:
        """Execute a normalized inference request and return a normalized InferenceResponse."""
        pass

    @abstractmethod
    def health(self) -> ProviderHealthStatus:
        """Return current provider health status."""
        pass


class MockProvider(BaseInferenceProvider):
    """
    Deterministic Mock Inference Provider for testing and local development without API keys.
    """

    def __init__(
        self,
        provider_id: str = "mock-provider",
        display_name: str = "Deterministic Mock Provider",
        models: Optional[list[ModelMetadata]] = None,
        should_fail: bool = False,
        error_code: Optional[InferenceErrorCode] = None,
        latency_ms: float = 15.0,
        custom_response: Optional[str] = None,
    ):
        self._provider_id = provider_id
        self.display_name = display_name
        self.should_fail = should_fail
        self.error_code = error_code or InferenceErrorCode.SERVER_ERROR
        self.latency_ms = latency_ms
        self.custom_response = custom_response
        self.call_count = 0
        self.last_request: Optional[InferenceRequest] = None
        self._health_status = ProviderHealthStatus.HEALTHY

        # Default simulated models
        self._models = models or [
            ModelMetadata(
                model_id=f"{provider_id}/standard",
                provider_id=provider_id,
                display_name="Mock Standard Model",
                capabilities={ModelCapability.TEXT_GENERATION, ModelCapability.CODE_GENERATION, ModelCapability.STRUCTURED_OUTPUT},
                context_window=32000,
                max_output_tokens=4096,
                input_cost_per_million=0.15,
                output_cost_per_million=0.60,
                is_free_tier=False,
                latency_p50_ms=250.0,
                quality_score=85.0,
            ),
            ModelMetadata(
                model_id=f"{provider_id}/free-vision",
                provider_id=provider_id,
                display_name="Mock Free Vision Model",
                capabilities={ModelCapability.TEXT_GENERATION, ModelCapability.VISION, ModelCapability.TOOL_CALLING},
                context_window=128000,
                max_output_tokens=4096,
                input_cost_per_million=0.0,
                output_cost_per_million=0.0,
                is_free_tier=True,
                latency_p50_ms=400.0,
                quality_score=80.0,
            ),
            ModelMetadata(
                model_id=f"{provider_id}/reasoning-pro",
                provider_id=provider_id,
                display_name="Mock Reasoning Pro",
                capabilities={
                    ModelCapability.TEXT_GENERATION,
                    ModelCapability.CODE_GENERATION,
                    ModelCapability.REASONING,
                    ModelCapability.STRUCTURED_OUTPUT,
                    ModelCapability.LONG_CONTEXT,
                    ModelCapability.TOOL_CALLING,
                },
                context_window=200000,
                max_output_tokens=8192,
                input_cost_per_million=3.0,
                output_cost_per_million=15.0,
                is_free_tier=False,
                latency_p50_ms=900.0,
                quality_score=98.0,
            ),
        ]

    @property
    def provider_id(self) -> str:
        return self._provider_id

    def set_canned_response(self, response: str) -> None:
        """Helper method for tests to set a deterministic canned model response."""
        self.custom_response = response

    def set_health(self, status: ProviderHealthStatus) -> None:
        self._health_status = status

    def list_models(self) -> list[ModelMetadata]:
        return list(self._models)

    def generate(
        self,
        request: InferenceRequest,
        model: ModelMetadata,
        secret: Optional[str] = None,
    ) -> InferenceResponse:
        self.call_count += 1
        self.last_request = request
        time.sleep(self.latency_ms / 1000.0)

        if self.should_fail:
            from core.errors import AutonomOSError
            raise AutonomOSError("SIMULATED_PROVIDER_FAILURE", f"MockProvider simulated failure: {self.error_code.value}")

        input_toks = request.estimated_input_tokens()
        output_toks = 60
        tot_toks = input_toks + output_toks

        # Compute cost
        in_cost = (input_toks / 1_000_000.0) * model.input_cost_per_million
        out_cost = (output_toks / 1_000_000.0) * model.output_cost_per_million
        total_cost = in_cost + out_cost

        content = self.custom_response or f"Mock generated answer from {model.model_id} for request {request.request_id}."

        return InferenceResponse(
            request_id=request.request_id,
            response_id=f"resp-{uuid.uuid4().hex[:10]}",
            content=content,
            model_used=model.model_id,
            provider_used=self._provider_id,
            finish_reason="stop",
            usage=Usage(
                input_tokens=input_toks,
                output_tokens=output_toks,
                total_tokens=tot_toks,
            ),
            cost=Cost(
                input_cost=in_cost,
                output_cost=out_cost,
                total_cost=total_cost,
                currency="USD",
                cost_type=CostType.ACTUAL if model.is_free_tier else CostType.ESTIMATED,
            ),
            latency_ms=self.latency_ms,
            metadata={"call_count": self.call_count},
        )

    def set_custom_response(self, response: Any) -> None:
        """Set a canned response or callback handler."""
        self.custom_response = response

    def health(self) -> ProviderHealthStatus:
        return self._health_status
