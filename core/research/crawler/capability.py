from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

from core.research.types import CrawlerCapability


@dataclass
class CrawlerCapabilitySpec:
    """Specification descriptor for a crawler capability."""
    capability: CrawlerCapability
    description: str = ""
    supported_protocols: list[str] = field(default_factory=lambda: ["http", "https"])
    max_concurrency: int = 5
    rate_limit_rpm: int = 60
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "capability": self.capability.value,
            "description": self.description,
            "supported_protocols": list(self.supported_protocols),
            "max_concurrency": self.max_concurrency,
            "rate_limit_rpm": self.rate_limit_rpm,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CrawlerCapabilitySpec:
        cap_raw = data.get("capability", CrawlerCapability.WEB_SEARCH.value)
        try:
            cap = CrawlerCapability(cap_raw)
        except (ValueError, TypeError):
            cap = CrawlerCapability.WEB_SEARCH

        return cls(
            capability=cap,
            description=data.get("description", ""),
            supported_protocols=list(data.get("supported_protocols", ["http", "https"])),
            max_concurrency=int(data.get("max_concurrency", 5)),
            rate_limit_rpm=int(data.get("rate_limit_rpm", 60)),
            metadata=dict(data.get("metadata", {})),
        )


def matches_capability(
    crawler_capabilities: list[CrawlerCapability],
    required_capability: CrawlerCapability,
) -> bool:
    """Check whether a list of capabilities satisfies a required capability."""
    return required_capability in crawler_capabilities
