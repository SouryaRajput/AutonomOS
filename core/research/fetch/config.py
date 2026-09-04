"""
Fetch Subsystem Configuration (Phase 1 / Part 3).

Manages runtime configuration, network constraints, timeout bounds, and safe serialization.
"""
from __future__ import annotations

from dataclasses import dataclass
import os
from typing import Any


@dataclass
class FetchConfig:
    """Configuration settings for web fetch execution and network boundaries."""
    provider_type: str = "mock"
    default_timeout_seconds: float = 15.0
    max_bytes: int = 500000
    max_redirects: int = 5
    user_agent: str = "AutonomOS-Researcher/1.0 (+https://autonomos.ai/bot)"
    allow_localhost: bool = False

    @classmethod
    def from_env(cls) -> FetchConfig:
        """Load fetch configuration from environment variables with sensible defaults."""
        provider_type = os.getenv("FETCH_PROVIDER", "mock").strip().lower()
        timeout = float(os.getenv("FETCH_TIMEOUT_SECONDS", "15.0"))
        max_bytes = int(os.getenv("FETCH_MAX_BYTES", "500000"))
        max_redirects = int(os.getenv("FETCH_MAX_REDIRECTS", "5"))
        user_agent = os.getenv("FETCH_USER_AGENT", "AutonomOS-Researcher/1.0 (+https://autonomos.ai/bot)").strip()
        allow_localhost = os.getenv("FETCH_ALLOW_LOCALHOST", "false").strip().lower() in ("1", "true", "yes")

        return cls(
            provider_type=provider_type,
            default_timeout_seconds=timeout,
            max_bytes=max_bytes,
            max_redirects=max_redirects,
            user_agent=user_agent,
            allow_localhost=allow_localhost,
        )

    def to_safe_dict(self) -> dict[str, Any]:
        """Return safe representation of configuration dictionary."""
        return {
            "provider_type": self.provider_type,
            "default_timeout_seconds": self.default_timeout_seconds,
            "max_bytes": self.max_bytes,
            "max_redirects": self.max_redirects,
            "user_agent": self.user_agent,
            "allow_localhost": self.allow_localhost,
        }

    def __repr__(self) -> str:
        return (
            f"FetchConfig(provider='{self.provider_type}', "
            f"timeout={self.default_timeout_seconds}s, "
            f"max_bytes={self.max_bytes}, "
            f"allow_localhost={self.allow_localhost})"
        )
