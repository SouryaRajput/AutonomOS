"""
Unit tests for Web Fetch contracts, parameters, and providers (Phase 1 / Part 3 / Step 2).
"""
from __future__ import annotations

import unittest

from core.research.contracts.crawler_task import CrawlerTask
from core.research.errors import (
    FetchError,
    FetchHttpError,
    FetchNetworkError,
    FetchParameterValidationError,
    FetchSecurityError,
    FetchTimeoutError,
)
from core.research.fetch.config import FetchConfig
from core.research.fetch.models import FetchParameters, FetchResponse
from core.research.fetch.provider import MockFetchProvider
from core.research.fetch.test_provider import TestFetchProvider
from core.research.types import CrawlerCapability


class TestFetchContractsAndProvider(unittest.TestCase):

    def setUp(self):
        self.mock_provider = MockFetchProvider()
        self.test_provider = TestFetchProvider()

    def test_01_valid_fetch_parameters(self):
        params = FetchParameters(
            url="https://v8.dev/features/simd",
            max_bytes=200000,
            timeout_seconds=10.0,
            headers={"Accept": "text/html"},
        )
        self.assertEqual(params.url, "https://v8.dev/features/simd")
        self.assertEqual(params.max_bytes, 200000)
        self.assertEqual(params.timeout_seconds, 10.0)

    def test_02_empty_or_missing_url_raises_validation_error(self):
        with self.assertRaises(FetchParameterValidationError):
            FetchParameters(url="")
        with self.assertRaises(FetchParameterValidationError):
            FetchParameters(url="   ")

    def test_03_unsupported_scheme_raises_validation_error(self):
        with self.assertRaises(FetchParameterValidationError):
            FetchParameters(url="ftp://example.com/file.txt")
        with self.assertRaises(FetchParameterValidationError):
            FetchParameters(url="file:///etc/passwd")
        with self.assertRaises(FetchParameterValidationError):
            FetchParameters(url="javascript:alert(1)")

    def test_04_max_bytes_and_timeout_boundaries(self):
        with self.assertRaises(FetchParameterValidationError):
            FetchParameters(url="https://example.com", max_bytes=100)  # Too small (< 512)
        with self.assertRaises(FetchParameterValidationError):
            FetchParameters(url="https://example.com", timeout_seconds=-1.0)
        with self.assertRaises(FetchParameterValidationError):
            FetchParameters(url="https://example.com", max_redirects=-1)

    def test_05_from_crawler_task_parsing(self):
        task = CrawlerTask(
            task_id="ctask-fetch-01",
            request_id="req-1",
            plan_id="plan-1",
            question_id="q-1",
            query_or_target="https://developer.mozilla.org/en-US/docs/Web/JavaScript",
            required_capability=CrawlerCapability.WEB_FETCH,
            timeout_seconds=20,
            parameters={"max_bytes": 100000, "headers": {"X-Custom": "val"}},
        )
        params = FetchParameters.from_crawler_task(task)
        self.assertEqual(params.url, "https://developer.mozilla.org/en-US/docs/Web/JavaScript")
        self.assertEqual(params.max_bytes, 100000)
        self.assertEqual(params.timeout_seconds, 20.0)
        self.assertEqual(params.headers.get("X-Custom"), "val")

    def test_06_mock_provider_register_and_fetch(self):
        url = "https://example.org/api-doc"
        content = "<h1>API Reference</h1><p>Endpoint documentation.</p>"
        self.mock_provider.register_page(
            url=url,
            body_text=content,
            status_code=200,
            headers={"Content-Type": "text/html; charset=utf-8"},
        )

        params = FetchParameters(url=url)
        resp = self.mock_provider.fetch(params)

        self.assertEqual(resp.url, url)
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.is_success)
        self.assertIn("API Reference", resp.body_text)
        self.assertEqual(resp.content_type, "text/html; charset=utf-8")
        self.assertEqual(resp.bytes_fetched, len(content.encode("utf-8")))

    def test_07_test_provider_fault_injection_http_errors(self):
        self.test_provider.simulate_http_error(404, "Page Not Found")
        params = FetchParameters(url="https://example.org/missing")

        with self.assertRaises(FetchHttpError) as cm:
            self.test_provider.fetch(params)
        self.assertEqual(cm.exception.status_code, 404)

        # 500 error
        self.test_provider.simulate_http_error(503, "Service Unavailable")
        with self.assertRaises(FetchHttpError) as cm:
            self.test_provider.fetch(params)
        self.assertEqual(cm.exception.status_code, 503)

    def test_08_test_provider_timeout_and_network_errors(self):
        self.test_provider.simulate_timeout(5.0)
        params = FetchParameters(url="https://example.org/timeout")

        with self.assertRaises(FetchTimeoutError) as cm:
            self.test_provider.fetch(params)
        self.assertEqual(cm.exception.timeout_seconds, 5.0)

        self.test_provider.simulate_network_error("DNS resolution failed")
        with self.assertRaises(FetchNetworkError):
            self.test_provider.fetch(params)

    def test_09_ssrf_validation_blocks_localhost_private_metadata(self):
        bad_urls = [
            "http://127.0.0.1:8080/admin",
            "http://localhost:3000/keys",
            "http://169.254.169.254/latest/meta-data/",
            "http://10.0.0.1/internal-router",
            "http://192.168.1.1/config",
            "http://user:secret@public-site.org/path",
        ]
        for url in bad_urls:
            with self.assertRaises(FetchSecurityError, msg=f"Should block {url}"):
                params = FetchParameters(url=url)
                self.mock_provider.fetch(params)

    def test_10_fetch_config_from_env_and_defaults(self):
        config = FetchConfig.from_env()
        self.assertEqual(config.provider_type, "mock")
        self.assertEqual(config.default_timeout_seconds, 15.0)
        self.assertEqual(config.max_bytes, 500000)
        self.assertFalse(config.allow_localhost)
        safe = config.to_safe_dict()
        self.assertIn("provider_type", safe)


if __name__ == "__main__":
    unittest.main()
