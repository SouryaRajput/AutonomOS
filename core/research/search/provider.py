from __future__ import annotations

from abc import ABC, abstractmethod
import logging
import time
from typing import Any, Optional
import uuid

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
    deduplicate_search_results,
    extract_domain,
    normalize_url,
)

logger = logging.getLogger("AutonomOS.Research.SearchProvider")


class SearchProvider(ABC):
    """
    Abstract search provider interface.
    Encapsulates external API communication, query execution, result normalization,
    and provider-specific error translation.
    
    The consuming WebSearchCrawler interacts solely through this abstraction.
    """

    def __init__(self, provider_id: str, name: str):
        self._provider_id = provider_id
        self._name = name

    @property
    def provider_id(self) -> str:
        return self._provider_id

    @property
    def name(self) -> str:
        return self._name

    def search(self, params: SearchParameters) -> SearchResponse:
        """
        Execute search query with deterministic validation, timing, filtering, and deduplication.
        """
        params.validate()
        start_time = time.perf_counter()

        logger.info(f"Executing search query via provider '{self.provider_id}': '{params.query}' (limit={params.limit})")
        response = self.execute_search(params)

        # 1. Post-process and filter domain allowlist/blocklist if needed
        filtered_results: list[SearchResultItem] = []
        for item in response.results:
            domain = item.domain or extract_domain(item.url)
            # Check domain allowlist
            if params.domain_allowlist:
                if not any(domain == d or domain.endswith("." + d) for d in params.domain_allowlist):
                    continue
            # Check domain blocklist
            if params.domain_blocklist:
                if any(domain == d or domain.endswith("." + d) for d in params.domain_blocklist):
                    continue
            filtered_results.append(item)

        # 2. Deduplicate results deterministically
        deduped_results = deduplicate_search_results(filtered_results)

        # 3. Enforce requested limit
        final_results = deduped_results[: params.limit]

        elapsed = round(time.perf_counter() - start_time, 4)
        response.results = final_results
        response.execution_time_seconds = elapsed
        response.provider = self.provider_id
        return response

    @abstractmethod
    def execute_search(self, params: SearchParameters) -> SearchResponse:
        """
        Execute search on concrete provider backend and return normalized SearchResponse.
        Provider implementations must catch low-level transport errors and raise SearchProviderError tiers.
        """
        pass


class MockSearchProvider(SearchProvider):
    """
    Deterministic offline search provider for testing, simulation, and hermetic environments.
    """

    def __init__(
        self,
        mock_data: Optional[dict[str, list[dict[str, Any]]]] = None,
        provider_id: str = "mock-search",
        name: str = "Mock Search Provider",
        simulate_error: Optional[Exception] = None,
        simulate_rate_limit: bool = False,
        simulate_timeout: bool = False,
        simulate_auth_error: bool = False,
    ):
        super().__init__(provider_id=provider_id, name=name)
        self.mock_data = mock_data or {}
        self.simulate_error = simulate_error
        self.simulate_rate_limit = simulate_rate_limit
        self.simulate_timeout = simulate_timeout
        self.simulate_auth_error = simulate_auth_error
        self.calls_count = 0

    def execute_search(self, params: SearchParameters) -> SearchResponse:
        self.calls_count += 1

        if self.simulate_auth_error:
            raise SearchAuthenticationError(self.provider_id, "Simulated authentication failure: Invalid API key")

        if self.simulate_rate_limit:
            raise SearchRateLimitError(self.provider_id, retry_after_seconds=30.0)

        if self.simulate_timeout:
            raise SearchTimeoutError(self.provider_id, timeout_seconds=5.0)

        if self.simulate_error:
            raise self.simulate_error

        query_clean = params.query.strip().lower()
        raw_items: list[dict[str, Any]] = []

        matched = False
        # 1. Exact match lookup
        if params.query in self.mock_data:
            raw_items = self.mock_data[params.query]
            matched = True
        else:
            # Substring match lookup
            for k, v in self.mock_data.items():
                if k.lower() in query_clean or query_clean in k.lower():
                    raw_items = v
                    matched = True
                    break

        # 2. Fallback deterministic generator if no canned fixture was configured
        if not matched and not self.mock_data:
            raw_items = [
                {
                    "title": f"Documentation and Reference for '{params.query}'",
                    "url": f"https://docs.example.org/search?q={urllib_quote(params.query)}",
                    "snippet": f"Official documentation, API guides, and reference specifications for {params.query}.",
                    "published_date": "2026-01-15T00:00:00Z",
                },
                {
                    "title": f"Implementation Best Practices: {params.query}",
                    "url": f"https://community.example.org/topics/{urllib_quote(params.query)}",
                    "snippet": f"Comprehensive architectural overview, benchmarks, and community discussions on {params.query}.",
                    "published_date": None,  # Explicitly test None published_date
                },
            ]

        # 3. Construct normalized SearchResultItem objects
        normalized_results: list[SearchResultItem] = []
        for idx, item in enumerate(raw_items):
            url = str(item.get("url", f"https://example.org/res/{idx+1}"))
            title = str(item.get("title", f"Result {idx+1} for {params.query}"))
            snippet = str(item.get("snippet", ""))
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
                    published_date=pub_date,
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


def urllib_quote(text: str) -> str:
    import urllib.parse
    return urllib.parse.quote_plus(text)
