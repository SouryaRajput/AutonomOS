from __future__ import annotations

import logging
from typing import Any, Optional
import urllib.parse

from core.research.errors import (
    SearchAuthenticationError,
    SearchError,
    SearchParameterValidationError,
    SearchProviderError,
    SearchRateLimitError,
    SearchTimeoutError,
)
from core.research.search.models import (
    SearchParameters,
    SearchResponse,
    SearchResultItem,
    utc_now,
)
from core.research.search.normalization import (
    extract_domain,
    normalize_url,
)
from core.research.search.provider import SearchProvider

logger = logging.getLogger("AutonomOS.Research.TestSearchProvider")


class TestSearchProvider(SearchProvider):
    """
    Deterministic in-memory test search provider for hermetic testing.
    Provides precise simulation controls for:
    - successful searches with canned or dynamic fixtures
    - zero result / empty queries
    - malformed provider responses
    - network failures, timeouts, 401/403 auth errors, 429 rate limits, and 5xx server errors
    - duplicate detection and provenance verification
    """

    def __init__(
        self,
        provider_id: str = "test-search",
        name: str = "Deterministic Test Search Provider",
    ):
        super().__init__(provider_id=provider_id, name=name)
        self.canned_fixtures: dict[str, list[dict[str, Any]]] = {}
        self.simulated_error: Optional[Exception] = None
        self.simulated_status_code: Optional[int] = None
        self.simulated_status_body: str = ""
        self.simulated_retry_after: Optional[float] = None
        self.simulated_malformed: bool = False
        self.recorded_calls: list[SearchParameters] = []
        self.calls_count: int = 0

    def set_fixture(self, query: str, results: list[dict[str, Any]]) -> TestSearchProvider:
        """Register canned search result dictionaries for an exact or substring query match."""
        self.canned_fixtures[query.strip().lower()] = results
        return self

    def simulate_failure(self, error: Exception) -> TestSearchProvider:
        """Force the provider to raise a specific exception on next search."""
        self.simulated_error = error
        return self

    def simulate_rate_limit(self, retry_after_seconds: float = 30.0) -> TestSearchProvider:
        """Simulate HTTP 429 Rate Limit error."""
        self.simulated_error = SearchRateLimitError(
            provider_id=self.provider_id,
            retry_after_seconds=retry_after_seconds,
            details={"simulated": True},
        )
        return self

    def simulate_timeout(self, timeout_seconds: float = 5.0) -> TestSearchProvider:
        """Simulate HTTP 408 / Socket Timeout."""
        self.simulated_error = SearchTimeoutError(
            provider_id=self.provider_id,
            timeout_seconds=timeout_seconds,
        )
        return self

    def simulate_auth_error(self, forbidden: bool = False) -> TestSearchProvider:
        """Simulate HTTP 401 Unauthorized or HTTP 403 Forbidden."""
        status = 403 if forbidden else 401
        msg = "Forbidden: Insufficient permissions" if forbidden else "Unauthorized: Invalid API key"
        self.simulated_error = SearchAuthenticationError(
            provider_id=self.provider_id,
            message=msg,
            details={"status_code": status},
        )
        return self

    def simulate_server_error(self, status_code: int = 503, message: str = "Service Unavailable") -> TestSearchProvider:
        """Simulate HTTP 5xx Server Error."""
        self.simulated_error = SearchProviderError(
            provider_id=self.provider_id,
            message=f"HTTP {status_code}: {message}",
            status_code=status_code,
        )
        return self

    def simulate_network_failure(self, reason: str = "Connection refused") -> TestSearchProvider:
        """Simulate low-level network failure."""
        self.simulated_error = SearchProviderError(
            provider_id=self.provider_id,
            message=f"Network transport error: {reason}",
            status_code=None,
        )
        return self

    def simulate_malformed_response(self) -> TestSearchProvider:
        """Simulate malformed schema from search backend."""
        self.simulated_malformed = True
        return self

    def reset(self) -> None:
        """Reset all simulation state, recorded calls, and fixtures."""
        self.canned_fixtures.clear()
        self.simulated_error = None
        self.simulated_status_code = None
        self.simulated_status_body = ""
        self.simulated_retry_after = None
        self.simulated_malformed = False
        self.recorded_calls.clear()
        self.calls_count = 0

    def execute_search(self, params: SearchParameters) -> SearchResponse:
        self.calls_count += 1
        self.recorded_calls.append(params)

        # 1. Raise any simulated failure
        if self.simulated_error:
            raise self.simulated_error

        # 2. Simulate malformed schema
        if self.simulated_malformed:
            raise SearchProviderError(
                self.provider_id,
                "Malformed JSON response from search backend: missing required keys",
            )

        query_key = params.query.strip().lower()
        raw_items: list[dict[str, Any]] = []

        # 3. Match canned fixtures
        if query_key in self.canned_fixtures:
            raw_items = self.canned_fixtures[query_key]
        else:
            for k, v in self.canned_fixtures.items():
                if k in query_key or query_key in k:
                    raw_items = v
                    break

        # 4. If no fixture was registered, generate deterministic mock items
        if not raw_items and not self.canned_fixtures:
            raw_items = [
                {
                    "title": f"Specification Guide: {params.query}",
                    "url": f"https://docs.example.org/{urllib.parse.quote(params.query)}/spec",
                    "snippet": f"Official standard specification and reference for {params.query}.",
                    "published_date": "2026-01-01T00:00:00Z",
                },
                {
                    "title": f"Implementation Patterns: {params.query}",
                    "url": f"https://community.example.org/{urllib.parse.quote(params.query)}/patterns",
                    "snippet": f"Community architecture patterns and performance benchmarks for {params.query}.",
                    "published_date": None,
                },
            ]

        # 5. Build normalized SearchResultItem objects
        normalized_results: list[SearchResultItem] = []
        for idx, item in enumerate(raw_items):
            url = str(item.get("url") or f"https://example.org/res/{idx+1}")
            title = str(item.get("title") or f"Result {idx+1} for {params.query}")
            snippet = str(item.get("snippet") or item.get("content") or "")
            pub_date = item.get("published_date")
            res_id = item.get("id") or item.get("provider_result_id")

            normalized_results.append(
                SearchResultItem(
                    title=title,
                    url=url,
                    original_url=url,
                    normalized_url=normalize_url(url),
                    snippet=snippet,
                    domain=extract_domain(url),
                    rank=idx + 1,
                    published_date=str(pub_date) if pub_date is not None else None,
                    provider=self.provider_id,
                    provider_result_id=str(res_id) if res_id is not None else None,
                    metadata=dict(item.get("metadata", {})),
                )
            )

        return SearchResponse(
            query=params.query,
            results=normalized_results,
            total_results=len(normalized_results),
            provider=self.provider_id,
        )
