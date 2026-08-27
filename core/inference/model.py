from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional
import uuid

from core.inference.types import (
    CostType,
    InferenceErrorCode,
    InferenceRequestStatus,
    ModelCapability,
    ProviderHealthStatus,
    RoutingProfile,
)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class Usage:
    """Token usage counters reported or estimated from an inference operation."""
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    cached_tokens: int = 0
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "total_tokens": self.total_tokens,
            "cached_tokens": self.cached_tokens,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Usage:
        return cls(
            input_tokens=int(data.get("input_tokens", 0)),
            output_tokens=int(data.get("output_tokens", 0)),
            total_tokens=int(data.get("total_tokens", 0)),
            cached_tokens=int(data.get("cached_tokens", 0)),
            metadata=dict(data.get("metadata", {})),
        )


@dataclass
class Cost:
    """Financial cost tracking associated with an inference request."""
    input_cost: float = 0.0
    output_cost: float = 0.0
    total_cost: float = 0.0
    currency: str = "USD"
    cost_type: CostType = CostType.ESTIMATED

    def to_dict(self) -> dict[str, Any]:
        return {
            "input_cost": round(self.input_cost, 6),
            "output_cost": round(self.output_cost, 6),
            "total_cost": round(self.total_cost, 6),
            "currency": self.currency,
            "cost_type": self.cost_type.value if isinstance(self.cost_type, CostType) else self.cost_type,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Cost:
        return cls(
            input_cost=float(data.get("input_cost", 0.0)),
            output_cost=float(data.get("output_cost", 0.0)),
            total_cost=float(data.get("total_cost", 0.0)),
            currency=data.get("currency", "USD"),
            cost_type=CostType(data.get("cost_type", "ESTIMATED")),
        )


@dataclass
class ModelMetadata:
    """Metadata describing a specific model supported by an inference provider."""
    model_id: str
    provider_id: str
    display_name: str
    capabilities: set[ModelCapability] = field(default_factory=set)
    context_window: int = 128000
    max_output_tokens: int = 4096
    input_cost_per_million: float = 0.0
    output_cost_per_million: float = 0.0
    is_free_tier: bool = False
    latency_p50_ms: float = 500.0
    quality_score: float = 80.0  # 0.0 to 100.0 scale
    active: bool = True
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "model_id": self.model_id,
            "provider_id": self.provider_id,
            "display_name": self.display_name,
            "capabilities": [c.value if isinstance(c, ModelCapability) else c for c in self.capabilities],
            "context_window": self.context_window,
            "max_output_tokens": self.max_output_tokens,
            "input_cost_per_million": self.input_cost_per_million,
            "output_cost_per_million": self.output_cost_per_million,
            "is_free_tier": self.is_free_tier,
            "latency_p50_ms": self.latency_p50_ms,
            "quality_score": self.quality_score,
            "active": self.active,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ModelMetadata:
        caps = {ModelCapability(c) for c in data.get("capabilities", [])}
        return cls(
            model_id=data["model_id"],
            provider_id=data["provider_id"],
            display_name=data.get("display_name", data["model_id"]),
            capabilities=caps,
            context_window=int(data.get("context_window", 128000)),
            max_output_tokens=int(data.get("max_output_tokens", 4096)),
            input_cost_per_million=float(data.get("input_cost_per_million", 0.0)),
            output_cost_per_million=float(data.get("output_cost_per_million", 0.0)),
            is_free_tier=bool(data.get("is_free_tier", False)),
            latency_p50_ms=float(data.get("latency_p50_ms", 500.0)),
            quality_score=float(data.get("quality_score", 80.0)),
            active=bool(data.get("active", True)),
            metadata=dict(data.get("metadata", {})),
        )


@dataclass
class ModelRequirement:
    """Explicit capability & constraint requirement submitted with an inference request."""
    required_capabilities: set[ModelCapability] = field(default_factory=set)
    preferred_capabilities: set[ModelCapability] = field(default_factory=set)
    minimum_context: int = 4000
    maximum_latency_ms: Optional[float] = None
    maximum_cost: Optional[float] = None
    preferred_providers: list[str] = field(default_factory=list)
    excluded_providers: list[str] = field(default_factory=list)
    preferred_models: list[str] = field(default_factory=list)
    excluded_models: list[str] = field(default_factory=list)
    routing_profile: RoutingProfile = RoutingProfile.BEST_AVAILABLE
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "required_capabilities": [c.value if isinstance(c, ModelCapability) else c for c in self.required_capabilities],
            "preferred_capabilities": [c.value if isinstance(c, ModelCapability) else c for c in self.preferred_capabilities],
            "minimum_context": self.minimum_context,
            "maximum_latency_ms": self.maximum_latency_ms,
            "maximum_cost": self.maximum_cost,
            "preferred_providers": self.preferred_providers,
            "excluded_providers": self.excluded_providers,
            "preferred_models": self.preferred_models,
            "excluded_models": self.excluded_models,
            "routing_profile": self.routing_profile.value if isinstance(self.routing_profile, RoutingProfile) else self.routing_profile,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ModelRequirement:
        return cls(
            required_capabilities={ModelCapability(c) for c in data.get("required_capabilities", [])},
            preferred_capabilities={ModelCapability(c) for c in data.get("preferred_capabilities", [])},
            minimum_context=int(data.get("minimum_context", 4000)),
            maximum_latency_ms=data.get("maximum_latency_ms"),
            maximum_cost=data.get("maximum_cost"),
            preferred_providers=list(data.get("preferred_providers", [])),
            excluded_providers=list(data.get("excluded_providers", [])),
            preferred_models=list(data.get("preferred_models", [])),
            excluded_models=list(data.get("excluded_models", [])),
            routing_profile=RoutingProfile(data.get("routing_profile", "BEST_AVAILABLE")),
            metadata=dict(data.get("metadata", {})),
        )


@dataclass
class InferenceMessage:
    """Normalized chat message passed into an inference request."""
    role: str
    content: str
    name: Optional[str] = None
    tool_calls: Optional[list[dict[str, Any]]] = None
    tool_call_id: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        d = {"role": self.role, "content": self.content}
        if self.name:
            d["name"] = self.name
        if self.tool_calls:
            d["tool_calls"] = self.tool_calls
        if self.tool_call_id:
            d["tool_call_id"] = self.tool_call_id
        return d

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> InferenceMessage:
        return cls(
            role=data["role"],
            content=data.get("content", ""),
            name=data.get("name"),
            tool_calls=data.get("tool_calls"),
            tool_call_id=data.get("tool_call_id"),
        )


@dataclass
class InferenceRequest:
    """Normalized request submitted to the Inference Gateway."""
    request_id: str
    project_id: str
    task_id: str
    worker_id: str
    messages: list[InferenceMessage]
    requirements: ModelRequirement = field(default_factory=ModelRequirement)
    temperature: float = 0.7
    max_output_tokens: Optional[int] = None
    timeout_seconds: float = 60.0
    tools: Optional[list[dict[str, Any]]] = None
    stream: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)

    def estimated_input_tokens(self) -> int:
        """Heuristic character-to-token estimator for prompt bounding."""
        total_chars = sum(len(m.content) for m in self.messages)
        return max(1, total_chars // 4)


@dataclass
class InferenceResponse:
    """Normalized response returned from the Inference Gateway."""
    request_id: str
    response_id: str
    content: str
    model_used: str
    provider_used: str
    finish_reason: str = "stop"
    tool_calls: Optional[list[dict[str, Any]]] = None
    usage: Usage = field(default_factory=Usage)
    cost: Cost = field(default_factory=Cost)
    latency_ms: float = 0.0
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "request_id": self.request_id,
            "response_id": self.response_id,
            "content": self.content,
            "model_used": self.model_used,
            "provider_used": self.provider_used,
            "finish_reason": self.finish_reason,
            "tool_calls": self.tool_calls,
            "usage": self.usage.to_dict(),
            "cost": self.cost.to_dict(),
            "latency_ms": round(self.latency_ms, 2),
            "metadata": self.metadata,
        }


@dataclass
class RoutingCandidate:
    """A scored model candidate evaluated by OmniRoute."""
    model: ModelMetadata
    score: float
    estimated_cost: float
    reasons: list[str] = field(default_factory=list)


@dataclass
class RoutingDecision:
    """Explainable result of an OmniRoute candidate selection evaluation."""
    request_id: str
    selected_model: Optional[ModelMetadata]
    candidate_scores: list[RoutingCandidate] = field(default_factory=list)
    rejected_candidates: dict[str, list[str]] = field(default_factory=dict)
    rationale: str = ""
