from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional

from core.inference.types import ModelCapability


class WorkerCapability(str, Enum):
    """Taxonomy of meaningful capabilities an AutonomOS worker can declare."""
    RESEARCH = "RESEARCH"
    CODE_GENERATION = "CODE_GENERATION"
    CODE_ANALYSIS = "CODE_ANALYSIS"
    TESTING = "TESTING"
    UI_ANALYSIS = "UI_ANALYSIS"
    DOCUMENTATION = "DOCUMENTATION"
    WEB_ACCESS = "WEB_ACCESS"
    FILE_MANIPULATION = "FILE_MANIPULATION"
    CODE_EXECUTION = "CODE_EXECUTION"
    VISION = "VISION"
    AUDIO = "AUDIO"
    STRUCTURED_OUTPUT = "STRUCTURED_OUTPUT"
    SIMULATION = "SIMULATION"


@dataclass
class WorkerRequirement:
    """Infrastructure requirements and preferences declared by a worker."""
    required_tools: list[str] = field(default_factory=list)
    preferred_inference_capabilities: set[ModelCapability] = field(default_factory=set)
    required_context_categories: list[str] = field(default_factory=list)
    minimum_context_window: int = 4000
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "required_tools": self.required_tools,
            "preferred_inference_capabilities": [
                c.value if isinstance(c, ModelCapability) else c for c in self.preferred_inference_capabilities
            ],
            "required_context_categories": self.required_context_categories,
            "minimum_context_window": self.minimum_context_window,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> WorkerRequirement:
        caps = {ModelCapability(c) for c in data.get("preferred_inference_capabilities", [])}
        return cls(
            required_tools=list(data.get("required_tools", [])),
            preferred_inference_capabilities=caps,
            required_context_categories=list(data.get("required_context_categories", [])),
            minimum_context_window=int(data.get("minimum_context_window", 4000)),
            metadata=dict(data.get("metadata", {})),
        )


@dataclass
class WorkerConfig:
    """Worker-specific configuration options passed during execution."""
    custom_settings: dict[str, Any] = field(default_factory=dict)
    preferred_style: str = "standard"
    timeout_seconds: Optional[int] = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "custom_settings": self.custom_settings,
            "preferred_style": self.preferred_style,
            "timeout_seconds": self.timeout_seconds,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> WorkerConfig:
        return cls(
            custom_settings=dict(data.get("custom_settings", {})),
            preferred_style=str(data.get("preferred_style", "standard")),
            timeout_seconds=data.get("timeout_seconds"),
        )
