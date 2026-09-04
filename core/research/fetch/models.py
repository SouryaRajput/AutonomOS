"""
Web Fetch Data Contracts and Parameters (Phase 1 / Part 3).

Establishes provider-neutral contracts for target URL retrieval, response representations,
and parameters parsed from incoming CrawlerTask objects.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Optional
import urllib.parse

from core.research.errors import FetchParameterValidationError

if TYPE_CHECKING:
    from core.research.contracts.crawler_task import CrawlerTask


def utc_now() -> str:
    """Generate ISO 8601 UTC timestamp."""
    return datetime.now(timezone.utc).isoformat()


@dataclass
class FetchParameters:
    """
    Validated, strongly-typed parameters for an individual HTTP web fetch.
    """
    url: str
    max_bytes: int = 500000          # 500 KB default ceiling
    timeout_seconds: float = 15.0
    headers: dict[str, str] = field(default_factory=dict)
    follow_redirects: bool = True
    max_redirects: int = 5
    allowed_schemes: tuple[str, ...] = ("http", "https")

    def __post_init__(self):
        self.validate()

    def validate(self) -> None:
        """Enforce strict parameter bounds and scheme requirements."""
        if not self.url or not isinstance(self.url, str) or not self.url.strip():
            raise FetchParameterValidationError("url", "Target URL cannot be empty or whitespace.")

        parsed = urllib.parse.urlparse(self.url.strip())
        if not parsed.scheme:
            raise FetchParameterValidationError("url", f"URL '{self.url}' is missing a scheme (expected http or https).")

        if parsed.scheme.lower() not in self.allowed_schemes:
            raise FetchParameterValidationError(
                "url",
                f"Unsupported URL scheme '{parsed.scheme}'. Only {self.allowed_schemes} are allowed.",
            )

        if not parsed.netloc:
            raise FetchParameterValidationError("url", f"URL '{self.url}' is missing a network host / domain.")

        if self.max_bytes < 512 or self.max_bytes > 20_000_000:
            raise FetchParameterValidationError(
                "max_bytes",
                f"Fetch max_bytes must be between 512 B and 20 MB (got {self.max_bytes}).",
            )

        if self.timeout_seconds <= 0.0 or self.timeout_seconds > 300.0:
            raise FetchParameterValidationError(
                "timeout_seconds",
                f"Timeout must be between 0.1s and 300.0s (got {self.timeout_seconds}).",
            )

        if self.max_redirects < 0 or self.max_redirects > 20:
            raise FetchParameterValidationError(
                "max_redirects",
                f"max_redirects must be between 0 and 20 (got {self.max_redirects}).",
            )

    @classmethod
    def from_crawler_task(cls, task: CrawlerTask) -> FetchParameters:
        """
        Extract and validate fetch parameters from a generic CrawlerTask.
        """
        url = task.query_or_target.strip()
        params = task.parameters or {}

        if not (url.startswith("http://") or url.startswith("https://")):
            if "." in url and " " not in url:
                url = f"https://{url}"
            else:
                url = f"https://mock.example.org/fetch?q={urllib.parse.quote(url)}"

        max_bytes = int(params.get("max_bytes", 500000))
        timeout_sec = float(params.get("timeout_seconds", float(task.timeout_seconds or 15.0)))
        headers = dict(params.get("headers", {}))
        follow_redirects = bool(params.get("follow_redirects", True))
        max_redirects = int(params.get("max_redirects", 5))

        return cls(
            url=url,
            max_bytes=max_bytes,
            timeout_seconds=timeout_sec,
            headers=headers,
            follow_redirects=follow_redirects,
            max_redirects=max_redirects,
        )


@dataclass
class FetchResponse:
    """
    Standardized, normalized response from a web fetch provider.
    """
    url: str                                  # Final resolved URL after any redirects
    original_url: str                         # Initial requested URL
    status_code: int                          # HTTP status code (e.g. 200)
    headers: dict[str, str] = field(default_factory=dict)
    content_type: str = "text/html"
    body_bytes: bytes = b""
    body_text: str = ""
    charset: str = "utf-8"
    bytes_fetched: int = 0
    execution_time_seconds: float = 0.0
    redirect_chain: list[str] = field(default_factory=list)
    retrieved_at: str = field(default_factory=utc_now)
    provider_id: str = "default"

    @property
    def is_success(self) -> bool:
        """True if HTTP status code is in 2xx range."""
        return 200 <= self.status_code < 300

    def to_dict(self) -> dict[str, Any]:
        return {
            "url": self.url,
            "original_url": self.original_url,
            "status_code": self.status_code,
            "headers": self.headers,
            "content_type": self.content_type,
            "bytes_fetched": self.bytes_fetched,
            "execution_time_seconds": self.execution_time_seconds,
            "redirect_chain": self.redirect_chain,
            "retrieved_at": self.retrieved_at,
            "provider_id": self.provider_id,
            "is_success": self.is_success,
        }
