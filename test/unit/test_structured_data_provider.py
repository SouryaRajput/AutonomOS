"""
Unit tests for StructuredDataProvider Abstraction and FakeStructuredDataProvider (Phase 1 / Part 7 / Step 2).

Covers:
- Provider contract compliance and abstract base class invariants
- Successful JSON requests (single object, nested object, array of objects)
- Paginated requests with PaginationMetadata
- XML and CSV structured fixtures
- Empty responses (HTTP 204 No Content)
- Malformed / corrupted response handling
- HTTP error responses (404 Not Found, 500 Internal Server Error)
- Authentication failures (401/403 access denial)
- Rate limiting (429 with retry_after_seconds)
- Timeout fault injection
- Cancellation propagation via is_cancelled predicate
- Response size limit enforcement (StructuredDataLimitError)
- Source metadata extraction (get_source_metadata)
- Provenance preservation and isolation (cloning prevents mutation leakage)
- Custom fixture registration (add_fixture, add_response)
"""
from __future__ import annotations

import unittest

from core.research.contracts.evidence import EvidenceProvenance
from core.research.errors import (
    StructuredDataAuthenticationError,
    StructuredDataCancelledError,
    StructuredDataLimitError,
    StructuredDataMalformedResponseError,
    StructuredDataNotFoundError,
    StructuredDataProviderError,
    StructuredDataRateLimitError,
    StructuredDataSecurityError,
    StructuredDataTimeoutError,
    StructuredDataValidationError,
)
from core.research.structured.fake_provider import (
    URL_CORRUPT,
    URL_CSV_DATA,
    URL_EMPTY_204,
    URL_ITEMS_ARRAY,
    URL_PAGINATED,
    URL_STATUS,
    URL_USER_NESTED,
    URL_XML_FEED,
    FakeStructuredDataProvider,
)
from core.research.structured.models import (
    HttpMethod,
    PaginationMetadata,
    StructuredContentType,
    StructuredDataLimits,
    StructuredDataRequest,
    StructuredDataResponse,
    StructuredDataSource,
    StructuredSourceType,
)
from core.research.structured.provider import StructuredDataProvider


def _make_request(
    endpoint_url: str = URL_STATUS,
    method: HttpMethod = HttpMethod.GET,
    request_id: str = "req-test-001",
    limits: StructuredDataLimits = None,
) -> StructuredDataRequest:
    return StructuredDataRequest(
        request_id=request_id,
        endpoint_url=endpoint_url,
        method=method,
        limits=limits or StructuredDataLimits(),
    )


class TestStructuredDataProviderContracts(unittest.TestCase):
    """Tests for StructuredDataProvider abstract base class behavior and contracts."""

    def test_cannot_instantiate_abstract_provider(self):
        with self.assertRaises(TypeError):
            StructuredDataProvider("test-id", "Test Provider")

    def test_provider_initialization_validation(self):
        with self.assertRaises(StructuredDataValidationError):
            FakeStructuredDataProvider(provider_id="", name="Valid Name")

        with self.assertRaises(StructuredDataValidationError):
            FakeStructuredDataProvider(provider_id="valid-id", name="")

    def test_provider_properties(self):
        provider = FakeStructuredDataProvider(
            provider_id="custom-structured-provider",
            name="Custom Name",
            default_limits=StructuredDataLimits(max_bytes=5_000_000),
        )
        self.assertEqual(provider.provider_id, "custom-structured-provider")
        self.assertEqual(provider.name, "Custom Name")
        self.assertEqual(provider.default_limits.max_bytes, 5_000_000)

    def test_security_validation_ssrf_rejects_disallowed_loopback(self):
        provider = FakeStructuredDataProvider(allow_localhost=False)
        req = StructuredDataRequest(
            request_id="req-ssrf",
            endpoint_url="http://127.0.0.1:8080/admin/dump",
        )
        with self.assertRaises(StructuredDataSecurityError):
            provider.execute_request(req)


class TestFakeStructuredDataProviderSuccessfulRetrieval(unittest.TestCase):
    """Tests for retrieving standard structured fixtures."""

    def setUp(self):
        self.provider = FakeStructuredDataProvider()

    def test_get_single_json_object(self):
        req = _make_request(URL_STATUS)
        resp = self.provider.execute_request(req)
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.is_success)
        self.assertEqual(resp.normalized_content_type, StructuredContentType.JSON)
        self.assertEqual(resp.payload["status"], "healthy")
        self.assertEqual(resp.payload["service"], "catalog-api")
        self.assertNotEqual(resp.raw_payload_hash, "")

    def test_get_nested_json_object(self):
        req = _make_request(URL_USER_NESTED)
        resp = self.provider.execute_request(req)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.payload["id"], 42)
        self.assertEqual(resp.payload["profile"]["first_name"], "Ada")
        self.assertEqual(resp.payload["profile"]["contact"]["email"], "ada@example.org")

    def test_get_items_array_and_fetch_content_decomposition(self):
        req = _make_request(URL_ITEMS_ARRAY)
        resp, records = self.provider.fetch_content(req, base_location="root")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(len(records), 3)
        self.assertEqual(records[0].source_location.path, "[0]")
        self.assertEqual(records[0].value["sku"], "WIDGET-01")
        self.assertEqual(records[1].source_location.path, "[1]")
        self.assertEqual(records[2].source_location.path, "[2]")

    def test_paginated_catalog_fixture(self):
        req = _make_request(URL_PAGINATED)
        resp = self.provider.execute_request(req)
        self.assertEqual(resp.status_code, 200)
        self.assertIsNotNone(resp.pagination_info)
        self.assertTrue(resp.pagination_info.has_more)
        self.assertEqual(resp.pagination_info.next_page, 2)
        self.assertEqual(resp.pagination_info.next_cursor, "cursor_page_2")
        self.assertEqual(resp.pagination_info.total_records, 4)

    def test_xml_feed_fixture(self):
        req = _make_request(URL_XML_FEED)
        resp = self.provider.execute_request(req)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.normalized_content_type, StructuredContentType.XML)
        self.assertIn("<feed", resp.payload)
        self.assertIn("System Updates", resp.payload)

    def test_csv_data_fixture(self):
        req = _make_request(URL_CSV_DATA)
        resp = self.provider.execute_request(req)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.normalized_content_type, StructuredContentType.CSV)
        self.assertIn("cpu_percent", resp.payload)

    def test_empty_204_response_fixture(self):
        req = _make_request(URL_EMPTY_204)
        resp = self.provider.execute_request(req)
        self.assertEqual(resp.status_code, 204)
        self.assertTrue(resp.is_success)
        self.assertIsNone(resp.payload)

    def test_corrupted_response_fixture(self):
        req = _make_request(URL_CORRUPT)
        resp = self.provider.execute_request(req)
        self.assertEqual(resp.status_code, 200)
        # Preserves raw corrupt text without crashing or pretending it parsed cleanly
        self.assertIn("broken", resp.payload or "")


class TestFaultInjectionAndErrorTaxonomy(unittest.TestCase):
    """Tests for fault injection knobs and error handling."""

    def setUp(self):
        self.provider = FakeStructuredDataProvider()

    def test_cancellation_check_raises_cancelled_error(self):
        req = _make_request(URL_STATUS)
        with self.assertRaises(StructuredDataCancelledError) as ctx:
            self.provider.execute_request(req, is_cancelled=lambda: True)
        self.assertIn(URL_STATUS, str(ctx.exception))

    def test_timeout_simulation_raises_timeout_error(self):
        self.provider.simulate_timeout = True
        self.provider.simulated_timeout_seconds = 15.0
        req = _make_request(URL_STATUS)
        with self.assertRaises(StructuredDataTimeoutError) as ctx:
            self.provider.execute_request(req)
        self.assertEqual(ctx.exception.timeout_seconds, 15.0)

    def test_provider_failure_simulation_raises_provider_error(self):
        self.provider.simulate_failure = True
        self.provider.simulated_failure_message = "Database connection pool exhausted"
        req = _make_request(URL_STATUS)
        with self.assertRaises(StructuredDataProviderError) as ctx:
            self.provider.execute_request(req)
        self.assertIn("Database connection pool exhausted", str(ctx.exception))

    def test_auth_error_simulation_raises_authentication_error(self):
        self.provider.simulate_auth_error = True
        self.provider.simulated_auth_status = 403
        self.provider.simulated_auth_message = "Forbidden: IP not on allowlist"
        req = _make_request(URL_STATUS)
        with self.assertRaises(StructuredDataAuthenticationError) as ctx:
            self.provider.execute_request(req)
        self.assertEqual(ctx.exception.status_code, 403)

    def test_rate_limit_simulation_raises_rate_limit_error(self):
        self.provider.simulate_rate_limit = True
        self.provider.simulated_retry_after = 120.0
        req = _make_request(URL_STATUS)
        with self.assertRaises(StructuredDataRateLimitError) as ctx:
            self.provider.execute_request(req)
        self.assertEqual(ctx.exception.retry_after_seconds, 120.0)

    def test_malformed_response_simulation_raises_malformed_error(self):
        self.provider.simulate_malformed_response = True
        req = _make_request(URL_STATUS)
        with self.assertRaises(StructuredDataMalformedResponseError):
            self.provider.execute_request(req)

    def test_unregistered_endpoint_raises_not_found(self):
        req = _make_request("mock://api.example.com/v1/missing_endpoint")
        with self.assertRaises(StructuredDataNotFoundError):
            self.provider.execute_request(req)

    def test_http_error_simulation_returns_non_2xx_response(self):
        self.provider.simulate_http_error = 500
        req = _make_request(URL_STATUS)
        resp = self.provider.execute_request(req)
        self.assertEqual(resp.status_code, 500)
        self.assertFalse(resp.is_success)

    def test_simulated_404_http_error_raises_not_found(self):
        self.provider.simulate_http_error = 404
        req = _make_request(URL_STATUS)
        with self.assertRaises(StructuredDataNotFoundError):
            self.provider.execute_request(req)

    def test_response_size_limit_enforcement_raises_limit_error(self):
        # Set max_bytes to 50 bytes (smaller than status fixture payload)
        tiny_limits = StructuredDataLimits(max_bytes=50)
        req = _make_request(URL_STATUS, limits=tiny_limits)
        with self.assertRaises(StructuredDataLimitError):
            self.provider.execute_request(req)

    def test_simulated_oversized_response_raises_limit_error(self):
        self.provider.simulate_oversized_response = True
        self.provider.simulated_oversized_bytes = 25_000_000
        req = _make_request(URL_STATUS)
        with self.assertRaises(StructuredDataLimitError):
            self.provider.execute_request(req)


class TestSourceMetadataAndFixtureRegistration(unittest.TestCase):
    """Tests for metadata retrieval and registering custom fixtures."""

    def setUp(self):
        self.provider = FakeStructuredDataProvider(populate_default_fixtures=False)

    def test_custom_fixture_registration_and_retrieval(self):
        custom_url = "mock://custom.api/v1/data"
        self.provider.add_fixture(
            endpoint_url=custom_url,
            payload={"custom_metric": 42.0},
            content_type="application/json",
            status_code=200,
            provider_name="custom-telemetry",
            source_type=StructuredSourceType.REST_API,
        )

        req = _make_request(custom_url)
        resp = self.provider.execute_request(req)
        self.assertEqual(resp.payload["custom_metric"], 42.0)

        meta = self.provider.get_source_metadata(custom_url)
        self.assertEqual(meta.provider, "custom-telemetry")
        self.assertEqual(meta.source_type, StructuredSourceType.REST_API)
        self.assertEqual(meta.content_type, StructuredContentType.JSON)

    def test_get_source_metadata_unregistered_raises_not_found(self):
        with self.assertRaises(StructuredDataNotFoundError):
            self.provider.get_source_metadata("mock://missing.api/info")

    def test_get_source_metadata_respects_cancellation(self):
        with self.assertRaises(StructuredDataCancelledError):
            self.provider.get_source_metadata(URL_STATUS, is_cancelled=lambda: True)

    def test_fixture_cloning_prevents_mutation_leakage(self):
        custom_url = "mock://custom.api/mutation-test"
        self.provider.add_fixture(
            endpoint_url=custom_url,
            payload={"items": ["a", "b"]},
            content_type="application/json",
        )

        req1 = _make_request(custom_url, request_id="req-1")
        resp1 = self.provider.execute_request(req1)
        resp1.payload["items"].append("corrupted_value")

        req2 = _make_request(custom_url, request_id="req-2")
        resp2 = self.provider.execute_request(req2)
        self.assertEqual(len(resp2.payload["items"]), 2)
        self.assertNotIn("corrupted_value", resp2.payload["items"])


if __name__ == "__main__":
    unittest.main()
