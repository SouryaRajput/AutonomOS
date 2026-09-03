"""
Unit tests for Search Contracts and Provider Abstraction (Phase 1 / Part 2 / Step 2).

Verifies:
1. Valid search parameter validation and creation
2. Missing query validation error
3. Invalid result limits (<=0, >100)
4. Invalid domain filters
5. Normalized search result item construction and serialization
6. Missing publication date represented as None
7. URL normalization (scheme, ports, tracking params, trailing slashes, fragments)
8. Deterministic deduplication (by normalized URL and provider ID)
9. Preservation of independent sources with similar topics
10. Capability integration with CrawlerCapability.WEB_SEARCH and CrawlerTask
11. SearchProvider template method (timing, limits, domain filtering, error translation)
12. MockSearchProvider error simulation (RateLimit, Timeout, Auth, Custom)
"""
from __future__ import annotations

import unittest

from core.research.contracts.crawler_task import CrawlerTask
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
)
from core.research.search.normalization import (
    deduplicate_search_results,
    extract_domain,
    normalize_url,
)
from core.research.search.provider import (
    MockSearchProvider,
    SearchProvider,
)
from core.research.types import CrawlerCapability, CrawlerTaskStatus


class TestSearchContractsAndProvider(unittest.TestCase):

    # 1. Valid search parameters
    def test_01_valid_search_parameters(self):
        params = SearchParameters(
            query="distributed consensus algorithms",
            limit=10,
            freshness="month",
            language="en",
            region="us",
            safe_search=True,
            domain_allowlist=["github.com", "docs.python.org"],
            domain_blocklist=["spam.com"],
        )
        params.validate()
        self.assertEqual(params.query, "distributed consensus algorithms")
        self.assertEqual(params.limit, 10)
        self.assertEqual(params.freshness, "month")
        self.assertEqual(params.domain_allowlist, ["github.com", "docs.python.org"])

        # Dict round-trip
        data = params.to_dict()
        rehydrated = SearchParameters.from_dict(data)
        self.assertEqual(rehydrated.query, params.query)
        self.assertEqual(rehydrated.limit, params.limit)
        self.assertEqual(rehydrated.domain_allowlist, params.domain_allowlist)

    # 2. Missing query
    def test_02_missing_or_empty_query_raises_validation_error(self):
        with self.assertRaises(SearchParameterValidationError) as ctx:
            p = SearchParameters(query="")
            p.validate()
        self.assertEqual(ctx.exception.parameter, "query")

        with self.assertRaises(SearchParameterValidationError):
            p = SearchParameters(query="   \n\t  ")
            p.validate()

    # 3. Invalid result limit
    def test_03_invalid_result_limit_raises_validation_error(self):
        with self.assertRaises(SearchParameterValidationError) as ctx1:
            p = SearchParameters(query="valid query", limit=0)
            p.validate()
        self.assertEqual(ctx1.exception.parameter, "limit")

        with self.assertRaises(SearchParameterValidationError) as ctx2:
            p = SearchParameters(query="valid query", limit=-5)
            p.validate()
        self.assertEqual(ctx2.exception.parameter, "limit")

        with self.assertRaises(SearchParameterValidationError) as ctx3:
            p = SearchParameters(query="valid query", limit=101)
            p.validate()
        self.assertEqual(ctx3.exception.parameter, "limit")

    # 4. Invalid domain filters
    def test_04_invalid_domain_filters_raise_validation_error(self):
        # Protocol should not be in domain filter
        with self.assertRaises(SearchParameterValidationError) as ctx:
            p = SearchParameters(query="query", domain_allowlist=["https://docs.python.org"])
            p.validate()
        self.assertEqual(ctx.exception.parameter, "domain_allowlist")

        # Path slash should not be in domain filter
        with self.assertRaises(SearchParameterValidationError):
            p = SearchParameters(query="query", domain_blocklist=["spam.com/bad/path"])
            p.validate()

        # Whitespace or malformed hostname
        with self.assertRaises(SearchParameterValidationError):
            p = SearchParameters(query="query", domain_allowlist=["invalid domain with spaces"])
            p.validate()

    # 5. Normalized result construction
    def test_05_normalized_result_construction_and_serialization(self):
        item = SearchResultItem(
            title="Three.js Documentation",
            url="https://threejs.org/docs/index.html?utm_source=google#Manual",
            snippet="WebGL 3D library for JavaScript.",
            rank=1,
            published_date="2026-02-01T12:00:00Z",
            provider="mock-provider",
            provider_result_id="res-001",
        )
        self.assertEqual(item.title, "Three.js Documentation")
        self.assertEqual(item.original_url, "https://threejs.org/docs/index.html?utm_source=google#Manual")
        self.assertEqual(item.normalized_url, "https://threejs.org/docs/index.html")
        self.assertEqual(item.domain, "threejs.org")
        self.assertEqual(item.rank, 1)
        self.assertEqual(item.published_date, "2026-02-01T12:00:00Z")

        # Serialization round-trip
        data = item.to_dict()
        rebuilt = SearchResultItem.from_dict(data)
        self.assertEqual(rebuilt.url, item.url)
        self.assertEqual(rebuilt.normalized_url, item.normalized_url)
        self.assertEqual(rebuilt.domain, item.domain)
        self.assertEqual(rebuilt.provider_result_id, "res-001")

    # 6. Missing publication date represented as None
    def test_06_missing_publication_date_preserved_as_none(self):
        item = SearchResultItem(
            title="Undated Article",
            url="https://example.org/article",
            snippet="No publication timestamp.",
            published_date=None,
        )
        self.assertIsNone(item.published_date)
        d = item.to_dict()
        self.assertIsNone(d["published_date"])
        rehydrated = SearchResultItem.from_dict(d)
        self.assertIsNone(rehydrated.published_date)

    # 7. URL Normalization
    def test_07_url_normalization(self):
        # Scheme and Host lowercase + strip default ports
        self.assertEqual(
            normalize_url("HTTP://Example.COM:80/path"),
            "http://example.com/path",
        )
        self.assertEqual(
            normalize_url("HTTPS://Example.COM:443/path/"),
            "https://example.com/path",
        )

        # Root trailing slash
        self.assertEqual(
            normalize_url("https://example.org/"),
            "https://example.org",
        )

        # Collapse duplicate slashes
        self.assertEqual(
            normalize_url("https://example.org//docs///api"),
            "https://example.org/docs/api",
        )

        # Fragment removal
        self.assertEqual(
            normalize_url("https://example.org/docs#overview"),
            "https://example.org/docs",
        )

        # Tracking parameter removal and query sorting
        self.assertEqual(
            normalize_url("https://example.org/search?utm_source=twitter&b=2&utm_medium=social&a=1"),
            "https://example.org/search?a=1&b=2",
        )

        # Domain extraction
        self.assertEqual(extract_domain("https://docs.python.org:443/3/library/"), "docs.python.org")
        self.assertEqual(extract_domain("api.github.com"), "api.github.com")

    # 8. Duplicate detection
    def test_08_duplicate_detection(self):
        item1 = SearchResultItem(
            title="Python Docs 1",
            url="https://docs.python.org/3/library/index.html?utm_source=feed",
            provider_result_id="res-1",
        )
        item2 = SearchResultItem(
            title="Python Docs Duplicate URL with fragment",
            url="https://docs.python.org/3/library/index.html#topics",
            provider_result_id="res-2",
        )
        item3 = SearchResultItem(
            title="Different page same domain",
            url="https://docs.python.org/3/tutorial/index.html",
            provider_result_id="res-3",
        )
        item4 = SearchResultItem(
            title="Duplicate by provider ID",
            url="https://different-mirror.org/python",
            provider_result_id="res-1",  # Duplicate ID with item1
        )

        results = [item1, item2, item3, item4]
        deduped = deduplicate_search_results(results)

        # item2 should be dropped (same normalized URL as item1)
        # item4 should be dropped (same provider_result_id as item1)
        # item1 and item3 should remain
        self.assertEqual(len(deduped), 2)
        self.assertEqual(deduped[0].url, item1.url)
        self.assertEqual(deduped[1].url, item3.url)

    # 9. Do NOT deduplicate independent sources with similar titles
    def test_09_preserves_independent_sources_with_similar_titles(self):
        item_a = SearchResultItem(
            title="Raft Consensus Algorithm Explained",
            url="https://raft.github.io/paper.pdf",
            snippet="Official specification of Raft.",
        )
        item_b = SearchResultItem(
            title="Raft Consensus Algorithm Explained",
            url="https://towardsdatascience.com/raft-consensus-algorithm-explained",
            snippet="Community breakdown and analysis of Raft.",
        )
        item_c = SearchResultItem(
            title="Raft Consensus Algorithm Explained",
            url="https://blog.cloudflare.com/raft-consensus-in-edge/",
            snippet="Cloudflare real-world deployment notes.",
        )

        deduped = deduplicate_search_results([item_a, item_b, item_c])
        self.assertEqual(len(deduped), 3)
        self.assertEqual([d.domain for d in deduped], ["raft.github.io", "towardsdatascience.com", "blog.cloudflare.com"])

    # 10. Capability integration with CrawlerCapability.WEB_SEARCH and CrawlerTask
    def test_10_search_parameters_from_crawler_task(self):
        task = CrawlerTask(
            task_id="ctask-001",
            request_id="req-001",
            plan_id="plan-001",
            question_id="q-001",
            query_or_target="WebAssembly SIMD performance",
            required_capability=CrawlerCapability.WEB_SEARCH,
            constraints=["domain:v8.dev", "-domain:unverified-blog.org"],
            parameters={"limit": 8, "freshness": "year", "safe_search": True},
        )

        self.assertEqual(task.required_capability, CrawlerCapability.WEB_SEARCH)
        params = SearchParameters.from_crawler_task(task)

        self.assertEqual(params.query, "WebAssembly SIMD performance")
        self.assertEqual(params.limit, 8)
        self.assertEqual(params.freshness, "year")
        self.assertEqual(params.domain_allowlist, ["v8.dev"])
        self.assertEqual(params.domain_blocklist, ["unverified-blog.org"])

    # 11. SearchProvider search execution, domain filtering, and limit enforcement
    def test_11_search_provider_execution_and_filtering(self):
        canned_data = {
            "webgl shaders": [
                {"title": "MDN WebGL", "url": "https://developer.mozilla.org/en-US/docs/Web/API/WebGL_API", "snippet": "MDN guide"},
                {"title": "Shadertoy", "url": "https://www.shadertoy.com/view/123", "snippet": "Community shaders"},
                {"title": "Spam Blog", "url": "https://spam.com/low-quality", "snippet": "Ad content"},
                {"title": "Khronos Specs", "url": "https://www.khronos.org/registry/webgl/specs/latest/", "snippet": "Official spec"},
            ]
        }

        provider = MockSearchProvider(mock_data=canned_data, provider_id="test-search")
        params = SearchParameters(
            query="webgl shaders",
            limit=2,
            domain_blocklist=["spam.com"],
        )

        resp = provider.search(params)
        self.assertIsInstance(resp, SearchResponse)
        self.assertEqual(resp.provider, "test-search")
        self.assertTrue(resp.execution_time_seconds >= 0.0)

        # Enforces limit=2 and excludes spam.com
        self.assertEqual(len(resp.results), 2)
        urls = [r.url for r in resp.results]
        self.assertIn("https://developer.mozilla.org/en-US/docs/Web/API/WebGL_API", urls)
        self.assertNotIn("https://spam.com/low-quality", urls)

    # 12. MockSearchProvider error simulation
    def test_12_provider_error_hierarchy(self):
        # Rate Limit Error
        p_rate = MockSearchProvider(simulate_rate_limit=True)
        with self.assertRaises(SearchRateLimitError) as ctx_rate:
            p_rate.search(SearchParameters(query="test"))
        self.assertEqual(ctx_rate.exception.status_code, 429)
        self.assertEqual(ctx_rate.exception.retry_after_seconds, 30.0)

        # Timeout Error
        p_time = MockSearchProvider(simulate_timeout=True)
        with self.assertRaises(SearchTimeoutError) as ctx_time:
            p_time.search(SearchParameters(query="test"))
        self.assertEqual(ctx_time.exception.status_code, 408)
        self.assertEqual(ctx_time.exception.timeout_seconds, 5.0)

        # Auth Error
        p_auth = MockSearchProvider(simulate_auth_error=True)
        with self.assertRaises(SearchAuthenticationError) as ctx_auth:
            p_auth.search(SearchParameters(query="test"))
        self.assertEqual(ctx_auth.exception.status_code, 401)

        # Generic Provider Error
        p_custom = MockSearchProvider(simulate_error=SearchProviderError("mock", "Backend 500 error", status_code=500))
        with self.assertRaises(SearchProviderError) as ctx_custom:
            p_custom.search(SearchParameters(query="test"))
        self.assertEqual(ctx_custom.exception.status_code, 500)


if __name__ == "__main__":
    unittest.main()
