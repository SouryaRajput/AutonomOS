from __future__ import annotations

from dataclasses import dataclass, field
import os
from typing import Any, Optional
import urllib.parse

from core.research.errors import SearchConfigurationError


@dataclass
class SearchConfig:
    """
    Configuration specification for search providers, endpoints, and policies.
    Enforces secret isolation, boundary validation, and secure default settings.
    """
    provider_type: str = "mock"
    base_url: Optional[str] = None
    api_key: Optional[str] = None
    timeout_seconds: float = 15.0
    max_retries: int = 2
    retry_backoff_factor: float = 0.5
    default_limit: int = 5
    safe_search: bool = True
    allow_localhost: bool = False
    user_agent: str = "AutonomOS-Researcher/1.0"
    custom_headers: dict[str, str] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        self.validate()

    def validate(self) -> None:
        """Validate configuration parameters."""
        if not self.provider_type or not isinstance(self.provider_type, str):
            raise SearchConfigurationError("provider_type must be a non-empty string.")

        if self.timeout_seconds <= 0.0:
            raise SearchConfigurationError(f"timeout_seconds must be > 0.0, got {self.timeout_seconds}")

        if not isinstance(self.max_retries, int) or self.max_retries < 0 or self.max_retries > 10:
            raise SearchConfigurationError(f"max_retries must be an integer between 0 and 10, got {self.max_retries}")

        if not isinstance(self.default_limit, int) or self.default_limit < 1 or self.default_limit > 100:
            raise SearchConfigurationError(f"default_limit must be an integer between 1 and 100, got {self.default_limit}")

        if self.base_url:
            parsed = urllib.parse.urlsplit(self.base_url.strip())
            if parsed.scheme not in ("http", "https"):
                raise SearchConfigurationError(f"base_url scheme must be 'http' or 'https', got '{parsed.scheme}' in '{self.base_url}'")
            if not parsed.netloc:
                raise SearchConfigurationError(f"base_url has invalid hostname/netloc: '{self.base_url}'")

    @classmethod
    def from_env(
        cls,
        prefix: str = "SEARCH_",
        env: Optional[dict[str, str]] = None,
    ) -> SearchConfig:
        """
        Construct SearchConfig from environment variables.
        Reads SEARCH_PROVIDER, SEARCH_BASE_URL, SEARCH_API_KEY, SEARCH_TIMEOUT_SECONDS, etc.
        """
        source = env if env is not None else os.environ

        provider_type = source.get(f"{prefix}PROVIDER", source.get(f"{prefix}PROVIDER_TYPE", "mock")).lower().strip()
        base_url = source.get(f"{prefix}BASE_URL", source.get(f"{prefix}ENDPOINT"))
        api_key = source.get(f"{prefix}API_KEY", source.get(f"{prefix}KEY"))

        timeout_raw = source.get(f"{prefix}TIMEOUT_SECONDS", source.get(f"{prefix}TIMEOUT", "15.0"))
        try:
            timeout_seconds = float(timeout_raw)
        except (ValueError, TypeError):
            timeout_seconds = 15.0

        retries_raw = source.get(f"{prefix}MAX_RETRIES", "2")
        try:
            max_retries = int(retries_raw)
        except (ValueError, TypeError):
            max_retries = 2

        limit_raw = source.get(f"{prefix}DEFAULT_LIMIT", "5")
        try:
            default_limit = int(limit_raw)
        except (ValueError, TypeError):
            default_limit = 5

        safe_search_raw = source.get(f"{prefix}SAFE_SEARCH", "true").lower()
        safe_search = safe_search_raw not in ("0", "false", "no", "off")

        allow_localhost_raw = source.get(f"{prefix}ALLOW_LOCALHOST", "false").lower()
        allow_localhost = allow_localhost_raw in ("1", "true", "yes", "on")

        user_agent = source.get(f"{prefix}USER_AGENT", "AutonomOS-Researcher/1.0")

        return cls(
            provider_type=provider_type,
            base_url=base_url.strip() if base_url else None,
            api_key=api_key.strip() if api_key else None,
            timeout_seconds=timeout_seconds,
            max_retries=max_retries,
            default_limit=default_limit,
            safe_search=safe_search,
            allow_localhost=allow_localhost,
            user_agent=user_agent,
        )

    def to_safe_dict(self) -> dict[str, Any]:
        """
        Serialize configuration to dictionary with secrets masked/redacted.
        """
        masked_key = None
        if self.api_key:
            if len(self.api_key) > 8:
                masked_key = f"{self.api_key[:3]}...{self.api_key[-3:]}"
            else:
                masked_key = "[REDACTED]"

        safe_headers: dict[str, str] = {}
        sensitive_keys = {"authorization", "x-api-key", "api-key", "bearer"}
        for k, v in self.custom_headers.items():
            if k.lower() in sensitive_keys:
                safe_headers[k] = "[REDACTED]"
            else:
                safe_headers[k] = v

        return {
            "provider_type": self.provider_type,
            "base_url": self.base_url,
            "api_key": masked_key,
            "timeout_seconds": self.timeout_seconds,
            "max_retries": self.max_retries,
            "retry_backoff_factor": self.retry_backoff_factor,
            "default_limit": self.default_limit,
            "safe_search": self.safe_search,
            "allow_localhost": self.allow_localhost,
            "user_agent": self.user_agent,
            "custom_headers": safe_headers,
            "metadata": dict(self.metadata),
        }

    def __repr__(self) -> str:
        safe = self.to_safe_dict()
        items = [f"{k}={v!r}" for k, v in safe.items()]
        return f"SearchConfig({', '.join(items)})"
