"""
Unit tests for DuckDuckGoSearchProvider, DDG HTML parsing, and SearchProviderFactory auto-selection.
"""
from __future__ import annotations

import io
import json
import os
import unittest
import urllib.error
import urllib.request

from core.research.errors import (
    SearchError,
    SearchRateLimitError,
    SearchSecurityError,
    SearchTimeoutError,
    SearchProviderError,
)
from core.research.search.config import SearchConfig
from core.research.search.factory import create_search_provider
from core.research.search.models import SearchParameters
from core.research.search.providers.duckduckgo import (
    DuckDuckGoSearchProvider,
    parse_ddg_html,
    unquote_ddg_redirect,
)
from core.research.search.providers.tavily import TavilySearchProvider

MOCK_DDG_HTML = """
<!DOCTYPE html>
<html>
<body>
<div class="result results_links results_links_deep web-result">
  <div class="links_main links_deep result__body">
    <h2 class="result__title">
      <a class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fwww.python.org%2F&amp;rut=1">Welcome to Python.org</a>
    </h2>
    <a class="result__snippet" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fwww.python.org%2F&amp;rut=1">
      The official home of the Python Programming Language.
    </a>
  </div>
</div>
<div class="result results_links results_links_deep web-result">
  <div class="links_main links_deep result__body">
    <h2 class="result__title">
      <a class="result__a" href="https://docs.python.org/3/">Python 3 Documentation</a>
    </h2>
    <a class="result__snippet" href="https://docs.python.org/3/">
      Browse the official Python documentation and language reference.
    </a>
  </div>
</div>
<div class="result results_links results_links_deep web-result">
  <div class="links_main links_deep result__body">
    <h2 class="result__title">
      <a class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fgithub.com%2Fpython%2Fcpython&amp;rut=2">cpython GitHub Repository</a>
    </h2>
    <a class="result__snippet">
      The Python programming language source code repository.
    </a>
  </div>
</div>
</body>
</html>
"""


class TestDuckDuckGoSearchProvider(unittest.TestCase):

    def test_unquote_ddg_redirect(self):
        # 1. With uddg redirect parameter
        raw = "//duckduckgo.com/l/?uddg=https%3A%2F%2Fwww.python.org%2Fdownloads%2F&rut=1"
        expected = "https://www.python.org/downloads/"
        self.assertEqual(unquote_ddg_redirect(raw), expected)

        # 2. Already direct URL
        direct = "https://docs.python.org/3/tutorial/"
        self.assertEqual(unquote_ddg_redirect(direct), direct)

        # 3. Empty or None
        self.assertEqual(unquote_ddg_redirect(""), "")

    def test_parse_ddg_html(self):
        results = parse_ddg_html(MOCK_DDG_HTML)
        self.assertEqual(len(results), 3)

        self.assertEqual(results[0]["url"], "https://www.python.org/")
        self.assertEqual(results[0]["title"], "Welcome to Python.org")
        self.assertIn("official home", results[0]["snippet"])

        self.assertEqual(results[1]["url"], "https://docs.python.org/3/")
        self.assertEqual(results[1]["title"], "Python 3 Documentation")

        self.assertEqual(results[2]["url"], "https://github.com/python/cpython")
        self.assertEqual(results[2]["title"], "cpython GitHub Repository")

    def test_execute_search_mocked_transport(self):
        def mock_transport(req: urllib.request.Request, timeout: float) -> bytes:
            return MOCK_DDG_HTML.encode("utf-8")

        provider = DuckDuckGoSearchProvider(
            transport_fn=mock_transport,
        )

        params = SearchParameters(query="python programming", limit=2)
        resp = provider.search(params)

        self.assertEqual(resp.provider, "duckduckgo")
        self.assertEqual(len(resp.results), 2)
        self.assertEqual(resp.results[0].title, "Welcome to Python.org")
        self.assertEqual(resp.results[0].url, "https://www.python.org/")
        self.assertEqual(resp.results[0].domain, "www.python.org")
        self.assertEqual(resp.results[0].rank, 1)
        self.assertGreaterEqual(resp.results[0].metadata["score"], 0.9)

    def test_execute_search_domain_filtering(self):
        def mock_transport(req: urllib.request.Request, timeout: float) -> bytes:
            return MOCK_DDG_HTML.encode("utf-8")

        provider = DuckDuckGoSearchProvider(
            transport_fn=mock_transport,
        )

        # Allow only github.com
        params = SearchParameters(
            query="python programming",
            domain_allowlist=["github.com"],
            limit=5,
        )
        resp = provider.search(params)
        self.assertEqual(len(resp.results), 1)
        self.assertEqual(resp.results[0].domain, "github.com")

        # Block github.com
        params_block = SearchParameters(
            query="python programming",
            domain_blocklist=["github.com"],
            limit=5,
        )
        resp_block = provider.search(params_block)
        self.assertTrue(all(r.domain != "github.com" for r in resp_block.results))

    def test_execute_search_rate_limit_error(self):
        def mock_transport_429(req: urllib.request.Request, timeout: float) -> bytes:
            fp = io.BytesIO(b"Rate limit exceeded")
            headers = {"Retry-After": "10"}
            raise urllib.error.HTTPError(req.full_url, 429, "Too Many Requests", headers, fp)

        provider = DuckDuckGoSearchProvider(transport_fn=mock_transport_429)
        params = SearchParameters(query="test rate limit")
        with self.assertRaises(SearchRateLimitError) as ctx:
            provider.search(params)
        self.assertEqual(ctx.exception.retry_after_seconds, 10.0)

    def test_execute_search_server_error(self):
        def mock_transport_500(req: urllib.request.Request, timeout: float) -> bytes:
            fp = io.BytesIO(b"Internal server error")
            raise urllib.error.HTTPError(req.full_url, 500, "Internal Server Error", {}, fp)

        provider = DuckDuckGoSearchProvider(transport_fn=mock_transport_500)
        params = SearchParameters(query="test server error")
        with self.assertRaises(SearchProviderError):
            provider.search(params)

    def test_ssrf_protection_rejects_private_ip(self):
        provider = DuckDuckGoSearchProvider(base_url="http://192.168.1.1/html/")
        params = SearchParameters(query="test ssrf")
        with self.assertRaises(SearchSecurityError):
            provider.search(params)

    def test_factory_duckduckgo_and_auto_selection(self):
        # 1. Explicit duckduckgo
        cfg_ddg = SearchConfig(provider_type="duckduckgo")
        prov_ddg = create_search_provider(cfg_ddg)
        self.assertIsInstance(prov_ddg, DuckDuckGoSearchProvider)

        # 2. Explicit ddg shorthand
        cfg_ddg_short = SearchConfig(provider_type="ddg")
        prov_ddg_short = create_search_provider(cfg_ddg_short)
        self.assertIsInstance(prov_ddg_short, DuckDuckGoSearchProvider)

        # 3. Auto mode with no API key -> defaults to DuckDuckGo
        orig_tavily_key = os.environ.pop("TAVILY_API_KEY", None)
        orig_search_key = os.environ.pop("SEARCH_API_KEY", None)
        try:
            cfg_auto_nokey = SearchConfig(provider_type="auto")
            prov_auto_nokey = create_search_provider(cfg_auto_nokey)
            self.assertIsInstance(prov_auto_nokey, DuckDuckGoSearchProvider)

            # 4. Auto mode with Tavily API key -> selects Tavily
            cfg_auto_key = SearchConfig(provider_type="auto", api_key="tvly-mock-key")
            prov_auto_key = create_search_provider(cfg_auto_key)
            self.assertIsInstance(prov_auto_key, TavilySearchProvider)
        finally:
            if orig_tavily_key:
                os.environ["TAVILY_API_KEY"] = orig_tavily_key
            if orig_search_key:
                os.environ["SEARCH_API_KEY"] = orig_search_key


if __name__ == "__main__":
    unittest.main()
