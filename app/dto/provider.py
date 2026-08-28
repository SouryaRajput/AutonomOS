from __future__ import annotations
from dataclasses import dataclass
from typing import Dict, Any, List, Optional
from core.inference.model import ModelMetadata

@dataclass
class ProviderDTO:
    provider_id: str
    display_name: str
    health_status: str
    model_count: int

    def to_dict(self) -> Dict[str, Any]:
        return {
            "provider_id": self.provider_id,
            "display_name": self.display_name,
            "health_status": self.health_status,
            "model_count": self.model_count
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> ProviderDTO:
        return cls(
            provider_id=data["provider_id"],
            display_name=data["display_name"],
            health_status=data["health_status"],
            model_count=data.get("model_count", 0)
        )

@dataclass
class ModelDTO:
    model_id: str
    provider_id: str
    display_name: str
    capabilities: List[str]
    context_window: int
    input_cost_per_million: float
    output_cost_per_million: float
    quality_score: float

    def to_dict(self) -> Dict[str, Any]:
        return {
            "model_id": self.model_id,
            "provider_id": self.provider_id,
            "display_name": self.display_name,
            "capabilities": self.capabilities,
            "context_window": self.context_window,
            "input_cost_per_million": self.input_cost_per_million,
            "output_cost_per_million": self.output_cost_per_million,
            "quality_score": self.quality_score
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> ModelDTO:
        return cls(
            model_id=data["model_id"],
            provider_id=data["provider_id"],
            display_name=data["display_name"],
            capabilities=data.get("capabilities", []),
            context_window=data.get("context_window", 0),
            input_cost_per_million=data.get("input_cost_per_million", 0.0),
            output_cost_per_million=data.get("output_cost_per_million", 0.0),
            quality_score=data.get("quality_score", 0.0)
        )

    @classmethod
    def from_domain(cls, metadata: ModelMetadata) -> ModelDTO:
        return cls(
            model_id=metadata.model_id,
            provider_id=metadata.provider_id,
            display_name=metadata.display_name,
            capabilities=metadata.capabilities,
            context_window=metadata.context_window,
            input_cost_per_million=metadata.input_cost_per_million,
            output_cost_per_million=metadata.output_cost_per_million,
            quality_score=metadata.quality_score
        )
