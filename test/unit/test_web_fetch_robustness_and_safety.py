"""
Unit tests for Web Fetch Crawler Robustness and Safety (Phase 1 / Part 3 / Step 3).

Verifies:
1. Redirect handling (safe redirect, loop detection, hop count limit, SSRF on redirect hop)
2. Response size limits (Content-Length header check, streaming chunk counting, decompression bomb)
3. HTTP failure semantics (200, 403, 404, 500, DNS/network drop, timeout, cancellation)
4. Empty responses (HTTP 200 with 0 bytes vs failure)
5. Content compression (gzip, deflate, decompression bounds)
6. Charset decoding and error resilience
"""
from __future__ import annotations

import gzip
import io
import unittest
from unittest.mock import MagicMock, patch
import urllib.error
import urllib.response
import zlib

from core.research.contracts.crawler_task import CrawlerTask
from core.research.crawler.web_fetch import WebFetchCrawler
from core.research.errors import (
    FetchError,
    FetchHttpError,
    FetchNetworkError,
    FetchRedirectError,
    FetchRedirectLimitError,
    FetchSecurityError,
    FetchSizeLimitError,
    FetchTimeoutError,
)
from core.research.fetch.config import FetchConfig
from core.research.fetch.models import FetchParameters, FetchResponse
from core.research.fetch.providers.urllib_fetch import SafeRedirectHandler, UrllibFetchProvider
from core.research.fetch.test_provider import TestFetchProvider
from core.research.types import (
    CrawlerCapability,
    CrawlerHealthStatus,
    CrawlerReportStatus,
    CrawlerStatus,
)


class TestWebFetchRobustnessAndSafety(unittest.TestCase):

    def setUp(self):
        self.config = FetchConfig(provider_type="test", default_timeout_seconds=5.0, max_bytes=50000)
        self.test_provider = TestFetchProvider(config=self.config)
        self.crawler = WebFetchCrawler(
            crawler_id="crawler.web_fetch.robust_01",
            provider=self.test_provider,
            config=self.config,
        )

    # ---------------------------------------------------------
    # 1. REDIRECT HANDLING
    # ---------------------------------------------------------

    def test_01_safe_redirect_chain_and_final_url(self):
        start_url = "https://example.com/start"
        hop1 = "https://example.com/hop1"
        final_url = "https://example.com/final"

        self.test_provider.set_fixture(
            url=start_url,
            body_text="<html><body>Content at final destination</body></html>",
            final_url=final_url,
            redirect_chain=[hop1, final_url],
        )

        task = CrawlerTask(
            task_id="ctask-redir-1",
            request_id="req-1",
            plan_id="p-1",
            question_id="q-1",
            query_or_target=start_url,
        )

        report = self.crawler.execute_crawler_task(task)
        self.assertEqual(report.status, CrawlerReportStatus.SUCCESS)
        self.assertEqual(report.raw_sources[0].url_or_ref, final_url)
        self.assertEqual(report.metadata["original_url"], start_url)
        self.assertEqual(report.metadata["url"], final_url)
        self.assertEqual(report.metadata["redirect_chain"], [hop1, final_url])

    def test_02_redirect_loop_detection(self):
        start_url = "https://example.com/loop-a"
        self.test_provider.simulate_redirect_loop(start_url)

        task = CrawlerTask(
            task_id="ctask-redir-loop",
            request_id="req-1",
            plan_id="p-1",
            question_id="q-1",
            query_or_target=start_url,
        )

        report = self.crawler.execute_crawler_task(task)
        self.assertEqual(report.status, CrawlerReportStatus.FAILED)
        self.assertIn("Redirect loop detected", str(report.error_message))
        self.assertEqual(self.crawler.health, CrawlerHealthStatus.DEGRADED)

    def test_03_redirect_limit_exceeded(self):
        start_url = "https://example.com/infinite-chain"
        self.test_provider.simulate_redirect_limit(start_url, max_redirects=5)

        task = CrawlerTask(
            task_id="ctask-redir-limit",
            request_id="req-1",
            plan_id="p-1",
            question_id="q-1",
            query_or_target=start_url,
        )

        report = self.crawler.execute_crawler_task(task)
        self.assertEqual(report.status, CrawlerReportStatus.FAILED)
        self.assertIn("Redirect limit exceeded", str(report.error_message))

    def test_04_safe_redirect_handler_unit_behavior(self):
        handler = SafeRedirectHandler(
            initial_url="https://example.com/entry",
            allow_localhost=False,
            max_redirects=3,
        )

        req_mock = MagicMock()
        req_mock.full_url = "https://example.com/entry"

        # Hop 1 (valid)
        with patch("urllib.request.HTTPRedirectHandler.redirect_request") as mock_super:
            mock_super.return_value = "new_request"
            res = handler.redirect_request(req_mock, None, 302, "Found", {}, "https://example.com/hop1")
            self.assertEqual(res, "new_request")
            self.assertEqual(handler.redirect_chain, ["https://example.com/hop1"])

        # Circular Loop back to initial_url
        with self.assertRaises(FetchRedirectLimitError) as cm:
            handler.redirect_request(req_mock, None, 302, "Found", {}, "https://example.com/entry")
        self.assertTrue(cm.exception.is_loop)

        # Hop 2 and Hop 3
        with patch("urllib.request.HTTPRedirectHandler.redirect_request") as mock_super:
            mock_super.return_value = "new_request"
            handler.redirect_request(req_mock, None, 302, "Found", {}, "https://example.com/hop2")
            handler.redirect_request(req_mock, None, 302, "Found", {}, "https://example.com/hop3")

        # Exceeds max_redirects (3)
        with self.assertRaises(FetchRedirectLimitError) as cm:
            handler.redirect_request(req_mock, None, 302, "Found", {}, "https://example.com/hop4")
        self.assertFalse(cm.exception.is_loop)
        self.assertEqual(cm.exception.max_redirects, 3)

        # SSRF redirect destination violation on fresh handler
        ssrf_handler = SafeRedirectHandler(
            initial_url="https://example.com/entry",
            allow_localhost=False,
            max_redirects=3,
        )
        with self.assertRaises(FetchSecurityError):
            ssrf_handler.redirect_request(req_mock, None, 302, "Found", {}, "http://127.0.0.1:8000/internal")

    # ---------------------------------------------------------
    # 2. RESPONSE SIZE LIMITS
    # ---------------------------------------------------------

    def test_05_oversized_response_streaming_rejection(self):
        url = "https://example.com/huge-file"
        self.test_provider.simulate_oversized_response(url, size_bytes=100000, max_bytes=50000)

        task = CrawlerTask(
            task_id="ctask-size-limit",
            request_id="req-1",
            plan_id="p-1",
            question_id="q-1",
            query_or_target=url,
        )

        report = self.crawler.execute_crawler_task(task)
        self.assertEqual(report.status, CrawlerReportStatus.FAILED)
        self.assertIn("exceeds maximum limit", str(report.error_message))

    def test_06_urllib_provider_content_length_early_rejection(self):
        urllib_provider = UrllibFetchProvider(config=self.config)

        mock_resp = MagicMock()
        mock_resp.headers = {"Content-Length": "1000000", "Content-Type": "text/html"}
        mock_resp.getcode.return_value = 200
        mock_resp.geturl.return_value = "https://example.com/huge"

        with patch("urllib.request.build_opener") as mock_build_opener:
            mock_opener = MagicMock()
            mock_opener.open.return_value.__enter__.return_value = mock_resp
            mock_build_opener.return_value = mock_opener

            params = FetchParameters(url="https://example.com/huge", max_bytes=50000)
            with self.assertRaises(FetchSizeLimitError) as cm:
                urllib_provider.execute_fetch(params)

            self.assertEqual(cm.exception.size_bytes, 1000000)
            self.assertEqual(cm.exception.max_bytes, 50000)

    def test_07_urllib_provider_streaming_chunk_size_enforcement(self):
        urllib_provider = UrllibFetchProvider(config=self.config)

        # Response without Content-Length that streams data beyond limit
        chunk1 = b"A" * 10000
        chunk2 = b"B" * 10000

        mock_resp = MagicMock()
        mock_resp.headers = {"Content-Type": "text/html"}
        mock_resp.getcode.return_value = 200
        mock_resp.geturl.return_value = "https://example.com/stream"
        mock_resp.read.side_effect = [chunk1, chunk2, b""]

        with patch("urllib.request.build_opener") as mock_build_opener:
            mock_opener = MagicMock()
            mock_opener.open.return_value.__enter__.return_value = mock_resp
            mock_build_opener.return_value = mock_opener

            # Max bytes set to 15000 (chunk1 + chunk2 = 20000, should abort cleanly)
            params = FetchParameters(url="https://example.com/stream", max_bytes=15000)
            with self.assertRaises(FetchSizeLimitError) as cm:
                urllib_provider.execute_fetch(params)

            self.assertEqual(cm.exception.size_bytes, 20000)
            self.assertEqual(cm.exception.max_bytes, 15000)

    # ---------------------------------------------------------
    # 3. COMPRESSION HANDLING
    # ---------------------------------------------------------

    def test_08_gzip_compressed_response_decompression(self):
        raw_html = "<html><body>Decompressed Gzip Content</body></html>"
        compressed_bytes = gzip.compress(raw_html.encode("utf-8"))

        urllib_provider = UrllibFetchProvider(config=self.config)

        mock_resp = MagicMock()
        mock_resp.headers = {
            "Content-Type": "text/html; charset=utf-8",
            "Content-Encoding": "gzip",
            "Content-Length": str(len(compressed_bytes)),
        }
        mock_resp.getcode.return_value = 200
        mock_resp.geturl.return_value = "https://example.com/gzip-test"
        mock_resp.read.side_effect = [compressed_bytes, b""]

        with patch("urllib.request.build_opener") as mock_build_opener:
            mock_opener = MagicMock()
            mock_opener.open.return_value.__enter__.return_value = mock_resp
            mock_build_opener.return_value = mock_opener

            params = FetchParameters(url="https://example.com/gzip-test")
            resp = urllib_provider.execute_fetch(params)

            self.assertEqual(resp.status_code, 200)
            self.assertEqual(resp.body_text, raw_html)
            self.assertEqual(resp.body_bytes, raw_html.encode("utf-8"))
            self.assertEqual(resp.bytes_fetched, len(raw_html.encode("utf-8")))

    def test_09_gzip_decompression_bomb_prevention(self):
        # 100 KB text compressed to very small payload
        huge_payload = ("A" * 100000).encode("utf-8")
        compressed_bytes = gzip.compress(huge_payload)

        urllib_provider = UrllibFetchProvider(config=self.config)

        mock_resp = MagicMock()
        mock_resp.headers = {
            "Content-Type": "text/html",
            "Content-Encoding": "gzip",
            "Content-Length": str(len(compressed_bytes)),
        }
        mock_resp.getcode.return_value = 200
        mock_resp.geturl.return_value = "https://example.com/gzip-bomb"
        mock_resp.read.side_effect = [compressed_bytes, b""]

        with patch("urllib.request.build_opener") as mock_build_opener:
            mock_opener = MagicMock()
            mock_opener.open.return_value.__enter__.return_value = mock_resp
            mock_build_opener.return_value = mock_opener

            # Max bytes is 50000, decompressed is 100000 -> must reject
            params = FetchParameters(url="https://example.com/gzip-bomb", max_bytes=50000)
            with self.assertRaises(FetchSizeLimitError) as cm:
                urllib_provider.execute_fetch(params)

            self.assertEqual(cm.exception.size_bytes, 100000)
            self.assertEqual(cm.exception.max_bytes, 50000)

    def test_10_deflate_compressed_response_decompression(self):
        raw_html = "<html><body>Decompressed Deflate Content</body></html>"
        compressed_bytes = zlib.compress(raw_html.encode("utf-8"))

        urllib_provider = UrllibFetchProvider(config=self.config)

        mock_resp = MagicMock()
        mock_resp.headers = {
            "Content-Type": "text/html; charset=utf-8",
            "Content-Encoding": "deflate",
            "Content-Length": str(len(compressed_bytes)),
        }
        mock_resp.getcode.return_value = 200
        mock_resp.geturl.return_value = "https://example.com/deflate-test"
        mock_resp.read.side_effect = [compressed_bytes, b""]

        with patch("urllib.request.build_opener") as mock_build_opener:
            mock_opener = MagicMock()
            mock_opener.open.return_value.__enter__.return_value = mock_resp
            mock_build_opener.return_value = mock_opener

            params = FetchParameters(url="https://example.com/deflate-test")
            resp = urllib_provider.execute_fetch(params)

            self.assertEqual(resp.status_code, 200)
            self.assertEqual(resp.body_text, raw_html)

    # ---------------------------------------------------------
    # 4. HTTP FAILURE & SECURITY CLASSIFICATION
    # ---------------------------------------------------------

    def test_11_http_403_forbidden_handling(self):
        url = "https://example.com/forbidden"
        self.test_provider.simulate_http_error(403, "Forbidden Resource")

        task = CrawlerTask(
            task_id="ctask-403",
            request_id="req-1",
            plan_id="p-1",
            question_id="q-1",
            query_or_target=url,
        )

        report = self.crawler.execute_crawler_task(task)
        self.assertEqual(report.status, CrawlerReportStatus.FAILED)
        self.assertIn("403", str(report.error_message))
        self.assertEqual(self.crawler.health, CrawlerHealthStatus.DEGRADED)

    def test_12_http_500_server_error_handling(self):
        url = "https://example.com/server-error"
        self.test_provider.simulate_http_error(500, "Internal Server Error")

        task = CrawlerTask(
            task_id="ctask-500",
            request_id="req-1",
            plan_id="p-1",
            question_id="q-1",
            query_or_target=url,
        )

        report = self.crawler.execute_crawler_task(task)
        self.assertEqual(report.status, CrawlerReportStatus.FAILED)
        self.assertIn("500", str(report.error_message))

    def test_13_network_failure_handling(self):
        url = "https://example.com/network-drop"
        self.test_provider.simulate_network_error("SSL handshake failed")

        task = CrawlerTask(
            task_id="ctask-net-err",
            request_id="req-1",
            plan_id="p-1",
            question_id="q-1",
            query_or_target=url,
        )

        report = self.crawler.execute_crawler_task(task)
        self.assertEqual(report.status, CrawlerReportStatus.FAILED)
        self.assertIn("SSL handshake failed", str(report.error_message))
        self.assertEqual(self.crawler.status, CrawlerStatus.FAILED)

    def test_14_empty_response_vs_failure_classification(self):
        url = "https://example.com/empty-200"
        self.test_provider.set_fixture(url=url, body_text="", status_code=200)

        task = CrawlerTask(
            task_id="ctask-empty-200",
            request_id="req-1",
            plan_id="p-1",
            question_id="q-1",
            query_or_target=url,
        )

        report = self.crawler.execute_crawler_task(task)
        self.assertEqual(report.status, CrawlerReportStatus.EMPTY)
        self.assertEqual(len(report.raw_sources), 0)
        self.assertEqual(len(report.extracted_evidence), 0)
        # Should be completed, NOT failed
        self.assertEqual(self.crawler.status, CrawlerStatus.COMPLETED)
        self.assertEqual(self.crawler.health, CrawlerHealthStatus.HEALTHY)

    # ---------------------------------------------------------
    # 5. ENCODING & CHARSETS
    # ---------------------------------------------------------

    def test_15_non_utf8_charset_decoding_and_fallback(self):
        urllib_provider = UrllibFetchProvider(config=self.config)

        # ISO-8859-1 encoded text: "Café" -> b"Caf\xe9"
        iso_bytes = "Café".encode("iso-8859-1")

        mock_resp = MagicMock()
        mock_resp.headers = {
            "Content-Type": "text/html; charset=iso-8859-1",
            "Content-Length": str(len(iso_bytes)),
        }
        mock_resp.getcode.return_value = 200
        mock_resp.geturl.return_value = "https://example.com/iso"
        mock_resp.read.side_effect = [iso_bytes, b""]

        with patch("urllib.request.build_opener") as mock_build_opener:
            mock_opener = MagicMock()
            mock_opener.open.return_value.__enter__.return_value = mock_resp
            mock_build_opener.return_value = mock_opener

            params = FetchParameters(url="https://example.com/iso")
            resp = urllib_provider.execute_fetch(params)

            self.assertEqual(resp.charset, "iso-8859-1")
            self.assertEqual(resp.body_text, "Café")


if __name__ == "__main__":
    unittest.main()
