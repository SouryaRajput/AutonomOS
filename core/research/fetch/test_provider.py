"""
Deterministic In-Memory Test Fetch Provider (Phase 1 / Part 3 / Step 3).

Harness for deterministic testing of WebFetchCrawler without requiring live internet connectivity.
Supports precise fault injection for HTTP errors, network timeouts, DNS drops, redirects,
redirect loops, redirect limits, oversized streams, compression, and encoding anomalies.
"""
from __future__ import annotations

import gzip
import time
from typing import Any, Optional
import zlib

from core.research.errors import (
    FetchError,
    FetchHttpError,
    FetchNetworkError,
    FetchRedirectLimitError,
    FetchSizeLimitError,
    FetchTimeoutError,
)
from core.research.fetch.config import FetchConfig
from core.research.fetch.models import FetchParameters, FetchResponse, utc_now
from core.research.fetch.provider import FetchProvider
from core.research.search.security import sanitize_headers


class TestFetchProvider(FetchProvider):
    """
    Deterministic fault-injecting fetch provider for tests.
    """

    def __init__(self, provider_id: str = "test-fetch", config: Optional[FetchConfig] = None):
        super().__init__(provider_id=provider_id, config=config)
        self._fixtures: dict[str, dict[str, Any]] = {}
        self._simulated_failure: Optional[Exception] = None
        self.call_history: list[FetchParameters] = []

    def set_fixture(
        self,
        url: str,
        body_text: str = "",
        status_code: int = 200,
        headers: Optional[dict[str, str]] = None,
        content_type: str = "text/html; charset=utf-8",
        final_url: Optional[str] = None,
        redirect_chain: Optional[list[str]] = None,
        body_bytes: Optional[bytes] = None,
        content_encoding: Optional[str] = None,
    ) -> None:
        """Register a canned URL response fixture."""
        raw_headers = dict(headers or {"Content-Type": content_type, "Server": "test-fetch/1.0"})
        if content_encoding:
            raw_headers["Content-Encoding"] = content_encoding

        self._fixtures[url.strip()] = {
            "body_text": body_text,
            "body_bytes": body_bytes,
            "status_code": status_code,
            "headers": raw_headers,
            "content_type": content_type,
            "final_url": final_url or url.strip(),
            "redirect_chain": redirect_chain or [],
            "content_encoding": content_encoding,
        }

    def simulate_failure(self, error: Exception) -> None:
        """Force subsequent fetches to fail with the given exception."""
        self._simulated_failure = error

    def simulate_http_error(self, status_code: int, message: str = "") -> None:
        """Simulate an HTTP 4xx or 5xx error."""
        self._simulated_failure = FetchHttpError(
            url="http://test.simulated",
            status_code=status_code,
            message=message or f"HTTP {status_code} Error",
        )

    def simulate_timeout(self, timeout_seconds: float = 5.0) -> None:
        """Simulate network timeout."""
        self._simulated_failure = FetchTimeoutError("http://test.simulated", timeout_seconds)

    def simulate_network_error(self, message: str = "Connection refused") -> None:
        """Simulate network connection drop."""
        self._simulated_failure = FetchNetworkError("http://test.simulated", message)

    def simulate_redirect_loop(self, url: str) -> None:
        """Simulate a circular redirect loop."""
        self._simulated_failure = FetchRedirectLimitError(
            url=url,
            redirect_count=3,
            max_redirects=5,
            is_loop=True,
        )

    def simulate_redirect_limit(self, url: str, max_redirects: int = 5) -> None:
        """Simulate exceeding maximum allowed redirects."""
        self._simulated_failure = FetchRedirectLimitError(
            url=url,
            redirect_count=max_redirects + 1,
            max_redirects=max_redirects,
            is_loop=False,
        )

    def simulate_oversized_response(self, url: str, size_bytes: int = 1_000_000, max_bytes: int = 500_000) -> None:
        """Simulate a response exceeding size limit."""
        self._simulated_failure = FetchSizeLimitError(
            url=url,
            size_bytes=size_bytes,
            max_bytes=max_bytes,
        )

    def reset(self) -> None:
        """Clear all registered fixtures, failures, and telemetry history."""
        self._fixtures.clear()
        self._simulated_failure = None
        self.call_history.clear()

    def execute_fetch(self, params: FetchParameters) -> FetchResponse:
        self.call_history.append(params)

        if self._simulated_failure is not None:
            err = self._simulated_failure
            self._simulated_failure = None  # One-shot by default
            if isinstance(err, FetchHttpError):
                err.url = params.url
            elif isinstance(err, (FetchTimeoutError, FetchNetworkError, FetchRedirectLimitError, FetchSizeLimitError)):
                err.url = params.url
            raise err

        target = params.url.strip()
        if target in self._fixtures:
            data = self._fixtures[target]
            status_code = int(data["status_code"])

            # Check if status_code is HTTP error
            if status_code >= 400:
                raise FetchHttpError(
                    url=params.url,
                    status_code=status_code,
                    message=f"HTTP {status_code} Error",
                    headers=sanitize_headers(dict(data["headers"])),
                )

            # Check for compressed bytes
            raw_bytes = data["body_bytes"]
            content_encoding = data.get("content_encoding")

            if raw_bytes is not None:
                if content_encoding == "gzip":
                    decompressed = gzip.decompress(raw_bytes)
                elif content_encoding == "deflate":
                    decompressed = zlib.decompress(raw_bytes)
                else:
                    decompressed = raw_bytes

                if len(decompressed) > params.max_bytes:
                    raise FetchSizeLimitError(params.url, len(decompressed), params.max_bytes)

                body_bytes = decompressed[:params.max_bytes]
                body_text = body_bytes.decode("utf-8", errors="replace")
            else:
                body_text = str(data["body_text"])
                body_bytes = body_text.encode("utf-8")
                if len(body_bytes) > params.max_bytes:
                    raise FetchSizeLimitError(params.url, len(body_bytes), params.max_bytes)

            raw_headers = dict(data["headers"])

            return FetchResponse(
                url=data["final_url"],
                original_url=params.url,
                status_code=status_code,
                headers=sanitize_headers(raw_headers),
                content_type=str(data["content_type"]),
                body_bytes=body_bytes,
                body_text=body_text,
                bytes_fetched=len(body_bytes),
                redirect_chain=list(data["redirect_chain"]),
                retrieved_at=utc_now(),
                provider_id=self.provider_id,
            )

        # Default fallback response
        fallback_body = (
            f"<!DOCTYPE html><html><head><title>Test Doc {target}</title></head>"
            f"<body><article><p>Simulated content for {target}.</p></article></body></html>"
        )
        body_bytes = fallback_body.encode("utf-8")
        if len(body_bytes) > params.max_bytes:
            raise FetchSizeLimitError(params.url, len(body_bytes), params.max_bytes)

        headers = {"Content-Type": "text/html; charset=utf-8", "Content-Length": str(len(body_bytes))}

        return FetchResponse(
            url=params.url,
            original_url=params.url,
            status_code=200,
            headers=sanitize_headers(headers),
            content_type="text/html; charset=utf-8",
            body_bytes=body_bytes,
            body_text=fallback_body,
            bytes_fetched=len(body_bytes),
            redirect_chain=[],
            retrieved_at=utc_now(),
            provider_id=self.provider_id,
        )
