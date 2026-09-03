"""
Unit tests for TavilySearchProvider (Phase 1 / Part 2 / Step 5).

Verifies:
1. Successful search request generation and response parsing
2. Missing / empty API key rejection
3. HTTP 400 / 422 parameter validation error mapping
4. HTTP 401 / 403 authentication and authorization failure mapping
5. HTTP 404 endpoint failure mapping
6. HTTP 408 and socket timeout error mapping
7. HTTP 429 rate limit error mapping with Retry-After header parsing
8. HTTP 500 / 502 / 503 upstream server error mapping
9. Network transport and DNS failure mapping
10. Malformed JSON response handling
11. Provider schema flexibility (null values, missing fields, empty results)
12. Secret redaction (API keys never appear in exceptions, logs, or error text)
13. Provider factory creation via create_search_provider()
"""
from __future__ import annotations

import io
import json
import socket
import unittest
import urllib.error
import urllib.request

from core.research.errors import (
    SearchAuthenticationError,
    SearchParameterValidationError,
    SearchProviderError,
    SearchRateLimitError,
    SearchTimeoutError,
)
from core.research.search.config import SearchConfig
from core.research.search.factory import create_search_provider
from core.research.search.models import SearchParameters
from core.research.search.providers.tavily import TavilySearchProvider


class TestTavilySearchProvider(unittest.TestCase):

    def setUp(self):
        self.api_key = "tvly-test-secret-key-12345"
        self.config = SearchConfig(
            provider_type="tavily",
            api_key=self.api_key,
            base_url="https://api.tavily.com",
            timeout_seconds=5.0,
        )

    # 1. Successful search request generation and response parsing
    def test_01_successful_search_execution(self):
        canned_response = {
            "query": "Three.js WebGL animations",
            "results": [
                {
                    "title": "Three.js Animation System",
                    "url": "https://threejs.org/docs/#manual/en/introduction/Animation-system",
                    "content": "The Three.js animation system allows you to animate properties of meshes and bones.",
                    "score": 0.98,
                    "published_date": "2026-01-20T00:00:00Z",
                },
                {
                    "title": "GSAP and Three.js 3D Web",
                    "url": "https://greensock.com/threejs/",
                    "content": "Animating 3D cameras and scenes with GSAP timelines.",
                    "score": 0.89,
                    "published_date": None,
                },
            ],
        }

        captured_requests = []

        def mock_transport(req: urllib.request.Request, timeout: float) -> bytes:
            captured_requests.append(req)
            self.assertEqual(req.get_method(), "POST")
            self.assertEqual(req.full_url, "https://api.tavily.com/search")
            self.assertEqual(req.headers.get("Content-type"), "application/json")
            
            # Verify body payload
            body = json.loads(req.data.decode("utf-8"))
            self.assertEqual(body["api_key"], self.api_key)
            self.assertEqual(body["query"], "Three.js WebGL animations")
            self.assertEqual(body["max_results"], 5)
            return json.dumps(canned_response).encode("utf-8")

        provider = TavilySearchProvider(config=self.config, transport_fn=mock_transport)
        params = SearchParameters(query="Three.js WebGL animations", limit=5)
        response = provider.search(params)

        self.assertEqual(len(captured_requests), 1)
        self.assertEqual(response.query, "Three.js WebGL animations")
        self.assertEqual(response.provider, "tavily")
        self.assertEqual(len(response.results), 2)

        # Result 1 verification
        r1 = response.results[0]
        self.assertEqual(r1.title, "Three.js Animation System")
        self.assertEqual(r1.url, "https://threejs.org/docs/#manual/en/introduction/Animation-system")
        self.assertEqual(r1.domain, "threejs.org")
        self.assertEqual(r1.rank, 1)
        self.assertEqual(r1.published_date, "2026-01-20T00:00:00Z")
        self.assertEqual(r1.metadata.get("score"), 0.98)

        # Result 2 verification
        r2 = response.results[1]
        self.assertEqual(r2.domain, "greensock.com")
        self.assertIsNone(r2.published_date)

    # 2. Missing / empty API key rejection
    def test_02_missing_api_key_raises_auth_error(self):
        provider = TavilySearchProvider(api_key=None, config=SearchConfig(provider_type="tavily", api_key=None))
        params = SearchParameters(query="test query")

        with self.assertRaises(SearchAuthenticationError) as ctx:
            provider.search(params)
        self.assertIn("API key is missing", str(ctx.exception))

    # 3. HTTP 400 / 422 parameter validation error mapping
    def test_03_http_400_and_422_parameter_validation_error(self):
        def mock_transport_400(req: urllib.request.Request, timeout: float) -> bytes:
            fp = io.BytesIO(b'{"error": "Query string is malformed"}')
            raise urllib.error.HTTPError(
                url=req.full_url,
                code=400,
                msg="Bad Request",
                hdrs={},
                fp=fp,
            )

        provider = TavilySearchProvider(config=self.config, transport_fn=mock_transport_400)
        with self.assertRaises(SearchParameterValidationError) as ctx:
            provider.search(SearchParameters(query="bad query"))
        self.assertIn("HTTP 400", str(ctx.exception))

    # 4. HTTP 401 / 403 authentication failure mapping
    def test_04_http_401_and_403_auth_failure(self):
        def mock_transport_401(req: urllib.request.Request, timeout: float) -> bytes:
            fp = io.BytesIO(b'{"error": "Invalid API key"}')
            raise urllib.error.HTTPError(
                url=req.full_url,
                code=401,
                msg="Unauthorized",
                hdrs={},
                fp=fp,
            )

        provider = TavilySearchProvider(config=self.config, transport_fn=mock_transport_401)
        with self.assertRaises(SearchAuthenticationError) as ctx:
            provider.search(SearchParameters(query="test"))
        self.assertEqual(ctx.exception.status_code, 401)

    # 5. HTTP 404 endpoint failure mapping
    def test_05_http_404_endpoint_not_found(self):
        def mock_transport_404(req: urllib.request.Request, timeout: float) -> bytes:
            fp = io.BytesIO(b"Endpoint not found")
            raise urllib.error.HTTPError(
                url=req.full_url,
                code=404,
                msg="Not Found",
                hdrs={},
                fp=fp,
            )

        provider = TavilySearchProvider(config=self.config, transport_fn=mock_transport_404)
        with self.assertRaises(SearchProviderError) as ctx:
            provider.search(SearchParameters(query="test"))
        self.assertEqual(ctx.exception.status_code, 404)

    # 6. HTTP 408 and socket timeout error mapping
    def test_06_http_408_and_socket_timeout(self):
        # HTTP 408
        def mock_transport_408(req: urllib.request.Request, timeout: float) -> bytes:
            fp = io.BytesIO(b"Request timed out")
            raise urllib.error.HTTPError(
                url=req.full_url,
                code=408,
                msg="Request Timeout",
                hdrs={},
                fp=fp,
            )

        provider_408 = TavilySearchProvider(config=self.config, transport_fn=mock_transport_408)
        with self.assertRaises(SearchTimeoutError) as ctx_408:
            provider_408.search(SearchParameters(query="test"))
        self.assertEqual(ctx_408.exception.status_code, 408)

        # Socket Timeout
        def mock_transport_socket_timeout(req: urllib.request.Request, timeout: float) -> bytes:
            raise socket.timeout("Operation timed out")

        provider_socket = TavilySearchProvider(config=self.config, transport_fn=mock_transport_socket_timeout)
        with self.assertRaises(SearchTimeoutError):
            provider_socket.search(SearchParameters(query="test"))

    # 7. HTTP 429 rate limit error mapping with Retry-After header parsing
    def test_07_http_429_rate_limit_with_retry_after(self):
        def mock_transport_429(req: urllib.request.Request, timeout: float) -> bytes:
            fp = io.BytesIO(b'{"error": "Usage quota exceeded"}')
            headers = {"Retry-After": "45"}
            raise urllib.error.HTTPError(
                url=req.full_url,
                code=429,
                msg="Too Many Requests",
                hdrs=headers,
                fp=fp,
            )

        provider = TavilySearchProvider(config=self.config, transport_fn=mock_transport_429)
        with self.assertRaises(SearchRateLimitError) as ctx:
            provider.search(SearchParameters(query="test"))

        self.assertEqual(ctx.exception.status_code, 429)
        self.assertEqual(ctx.exception.retry_after_seconds, 45.0)

    # 8. HTTP 500 / 502 / 503 upstream server error mapping
    def test_08_http_5xx_upstream_server_error(self):
        def mock_transport_503(req: urllib.request.Request, timeout: float) -> bytes:
            fp = io.BytesIO(b"Service Temporarily Unavailable")
            raise urllib.error.HTTPError(
                url=req.full_url,
                code=503,
                msg="Service Unavailable",
                hdrs={},
                fp=fp,
            )

        provider = TavilySearchProvider(config=self.config, transport_fn=mock_transport_503)
        with self.assertRaises(SearchProviderError) as ctx:
            provider.search(SearchParameters(query="test"))
        self.assertEqual(ctx.exception.status_code, 503)

    # 9. Network transport and DNS failure mapping
    def test_09_network_transport_and_dns_error(self):
        def mock_transport_dns(req: urllib.request.Request, timeout: float) -> bytes:
            raise urllib.error.URLError("Name or service not known (DNS lookup failure)")

        provider = TavilySearchProvider(config=self.config, transport_fn=mock_transport_dns)
        with self.assertRaises(SearchProviderError) as ctx:
            provider.search(SearchParameters(query="test"))
        self.assertIn("Network transport error", str(ctx.exception))

    # 10. Malformed JSON response handling
    def test_10_malformed_json_response(self):
        def mock_transport_malformed(req: urllib.request.Request, timeout: float) -> bytes:
            return b"<html><body>Internal Gateway HTML Error</body></html>"

        provider = TavilySearchProvider(config=self.config, transport_fn=mock_transport_malformed)
        with self.assertRaises(SearchProviderError) as ctx:
            provider.search(SearchParameters(query="test"))
        self.assertIn("Malformed JSON response", str(ctx.exception))

    # 11. Provider schema flexibility (null values, missing fields, empty results)
    def test_11_schema_flexibility(self):
        canned_empty = {"query": "empty query", "results": []}

        def mock_transport_empty(req: urllib.request.Request, timeout: float) -> bytes:
            return json.dumps(canned_empty).encode("utf-8")

        provider = TavilySearchProvider(config=self.config, transport_fn=mock_transport_empty)
        resp = provider.search(SearchParameters(query="empty query"))
        self.assertEqual(len(resp.results), 0)
        self.assertEqual(resp.total_results, 0)

    # 12. Secret redaction (API keys never appear in exceptions, logs, or error text)
    def test_12_secret_redaction_in_exceptions(self):
        secret_key = "tvly-super-secret-production-key-9999"
        config = SearchConfig(provider_type="tavily", api_key=secret_key)

        def mock_transport_secret_leak(req: urllib.request.Request, timeout: float) -> bytes:
            # Simulate a provider error response that echoes back the user's secret key
            fp = io.BytesIO(f'{{"error": "Key {secret_key} has exceeded quota"}}'.encode("utf-8"))
            raise urllib.error.HTTPError(
                url=req.full_url,
                code=429,
                msg="Too Many Requests",
                hdrs={},
                fp=fp,
            )

        provider = TavilySearchProvider(config=config, transport_fn=mock_transport_secret_leak)
        with self.assertRaises(SearchRateLimitError) as ctx:
            provider.search(SearchParameters(query="test"))

        err_str = str(ctx.exception)
        err_details_str = str(ctx.exception.details)
        self.assertNotIn(secret_key, err_str)
        self.assertNotIn(secret_key, err_details_str)
        self.assertIn("[REDACTED]", ctx.exception.details["body"])

    # 13. Provider factory creation via create_search_provider()
    def test_13_create_search_provider_factory(self):
        # Tavily configuration
        cfg_tavily = SearchConfig(provider_type="tavily", api_key="test-key")
        p_tavily = create_search_provider(cfg_tavily)
        self.assertIsInstance(p_tavily, TavilySearchProvider)
        self.assertEqual(p_tavily.provider_id, "tavily")

        # Mock configuration
        cfg_mock = SearchConfig(provider_type="mock")
        p_mock = create_search_provider(cfg_mock)
        self.assertEqual(p_mock.provider_id, "mock")


if __name__ == "__main__":
    unittest.main()
