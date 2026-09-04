"""
Fetch Provider Abstraction and Mock Implementation (Phase 1 / Part 3).

Decouples WebFetchCrawler from underlying HTTP transport mechanics and network libraries.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
import logging
import time
from typing import Any, Optional

from core.research.errors import (
    FetchError,
    FetchParameterValidationError,
    FetchSecurityError,
)
from core.research.fetch.config import FetchConfig
from core.research.fetch.models import FetchParameters, FetchResponse, utc_now
from core.research.search.security import (
    sanitize_error,
    sanitize_headers,
    validate_network_target,
)

logger = logging.getLogger("AutonomOS.Research.FetchProvider")


class FetchProvider(ABC):
    """
    Abstract Base Class for HTTP web fetch providers.
    Template method pattern enforces parameter validation, network security,
    and consistent error mapping.
    """

    def __init__(self, provider_id: str = "fetch-provider", config: Optional[FetchConfig] = None):
        self.provider_id = provider_id
        self.config = config or FetchConfig.from_env()

    def fetch(self, params: FetchParameters) -> FetchResponse:
        """
        Public template method executing a bounded HTTP web fetch.
        Validates target SSRF boundaries and measures execution timing.
        """
        if not isinstance(params, FetchParameters):
            raise FetchParameterValidationError("params", "Must be an instance of FetchParameters.")

        params.validate()

        # Enforce network SSRF boundary
        try:
            validate_network_target(params.url, allow_localhost=self.config.allow_localhost)
        except Exception as sec_err:
            raise FetchSecurityError(params.url, str(sec_err)) from sec_err

        start_time = time.perf_counter()
        try:
            response = self.execute_fetch(params)
            elapsed = round(time.perf_counter() - start_time, 4)
            response.execution_time_seconds = elapsed
            response.provider_id = self.provider_id
            return response
        except FetchError:
            raise
        except Exception as e:
            sanitized = sanitize_error(e)
            logger.error(f"Fetch provider '{self.provider_id}' failed for '{params.url}': {sanitized}")
            raise FetchError(f"Fetch provider '{self.provider_id}' failed: {sanitized}") from e

    @abstractmethod
    def execute_fetch(self, params: FetchParameters) -> FetchResponse:
        """
        Execute raw HTTP fetch and return FetchResponse.
        Subclasses must implement transport-specific logic.
        """
        pass


class MockFetchProvider(FetchProvider):
    """
    Deterministic offline in-memory fetch provider for unit tests and local simulation.
    """

    def __init__(self, provider_id: str = "mock-fetch", config: Optional[FetchConfig] = None):
        super().__init__(provider_id=provider_id, config=config)
        self._canned_responses: dict[str, dict[str, Any]] = {}
        self.history: list[FetchParameters] = []

    def register_page(
        self,
        url: str,
        body_text: str,
        status_code: int = 200,
        headers: Optional[dict[str, str]] = None,
        content_type: str = "text/html; charset=utf-8",
        final_url: Optional[str] = None,
        redirect_chain: Optional[list[str]] = None,
    ) -> None:
        """Register a canned URL response for deterministic testing."""
        self._canned_responses[url.strip()] = {
            "body_text": body_text,
            "status_code": status_code,
            "headers": headers or {"Content-Type": content_type, "Server": "mock-server/1.0"},
            "content_type": content_type,
            "final_url": final_url or url.strip(),
            "redirect_chain": redirect_chain or [],
        }

    def execute_fetch(self, params: FetchParameters) -> FetchResponse:
        self.history.append(params)
        target = params.url.strip()

        if target in self._canned_responses:
            data = self._canned_responses[target]
            body_text = str(data["body_text"])[:params.max_bytes]
            body_bytes = body_text.encode("utf-8")
            raw_headers = dict(data["headers"])
            return FetchResponse(
                url=data["final_url"],
                original_url=params.url,
                status_code=int(data["status_code"]),
                headers=sanitize_headers(raw_headers),
                content_type=str(data["content_type"]),
                body_bytes=body_bytes,
                body_text=body_text,
                bytes_fetched=len(body_bytes),
                redirect_chain=list(data["redirect_chain"]),
                retrieved_at=utc_now(),
                provider_id=self.provider_id,
            )

        # Default fallback content
        fallback_text = (
            f"<!DOCTYPE html><html><head><title>Mock Page for {target}</title></head>"
            f"<body><h1>Mock Content</h1><p>Deterministic fetched content for {target}.</p></body></html>"
        )[:params.max_bytes]
        body_bytes = fallback_text.encode("utf-8")
        headers = {"Content-Type": "text/html; charset=utf-8", "Content-Length": str(len(body_bytes))}

        return FetchResponse(
            url=params.url,
            original_url=params.url,
            status_code=200,
            headers=sanitize_headers(headers),
            content_type="text/html; charset=utf-8",
            body_bytes=body_bytes,
            body_text=fallback_text,
            bytes_fetched=len(body_bytes),
            redirect_chain=[],
            retrieved_at=utc_now(),
            provider_id=self.provider_id,
        )
