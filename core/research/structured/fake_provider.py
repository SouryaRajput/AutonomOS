"""
Deterministic Fake Structured Data Provider (Phase 1 / Part 7 / Step 2).

Provides hermetic, in-memory fixtures and configurable fault-injection knobs for
testing StructuredDataCrawler, extraction pipelines, and error handling.

Guarantees:
- Pure source/transport access: strictly NO semantic research or field inference.
- Rich deterministic fixtures: JSON (single, nested, array, paginated), XML, CSV, empty, corrupt.
- Comprehensive fault injection: timeouts, HTTP errors, auth failures, rate limits, malformed responses, cancellations, oversized responses.
- Invariant: Failures are NEVER silently converted into empty successful results.
"""
from __future__ import annotations

import copy
import logging
import time
from typing import Any, Callable, Optional
import urllib.parse
import uuid

from core.research.contracts.evidence import EvidenceProvenance
from core.research.errors import (
    StructuredDataAuthenticationError,
    StructuredDataAuthorizationError,
    StructuredDataCancelledError,
    StructuredDataLimitError,
    StructuredDataMalformedResponseError,
    StructuredDataNotFoundError,
    StructuredDataProviderError,
    StructuredDataQuotaExceededError,
    StructuredDataRateLimitError,
    StructuredDataTimeoutError,
)
from core.research.structured.models import (
    HttpMethod,
    PaginationMetadata,
    SourceLocation,
    StructuredContentType,
    StructuredDataLimits,
    StructuredDataRequest,
    StructuredDataResponse,
    StructuredDataSource,
    StructuredRecord,
    StructuredSchema,
    StructuredSourceType,
    canonical_json_dumps,
    compute_structured_hash,
    utc_now,
)
from core.research.structured.provider import StructuredDataProvider

logger = logging.getLogger("AutonomOS.Research.FakeStructuredDataProvider")


# Standard default fixture URLs
URL_STATUS = "mock://api.example.com/v1/status"
URL_USER_NESTED = "mock://api.example.com/v1/users/42"
URL_ITEMS_ARRAY = "mock://api.example.com/v1/items"
URL_PAGINATED = "mock://api.example.com/v1/catalog"
URL_XML_FEED = "mock://api.example.com/v1/feed.xml"
URL_CSV_DATA = "mock://api.example.com/v1/dataset.csv"
URL_EMPTY_204 = "mock://api.example.com/v1/empty"
URL_CORRUPT = "mock://api.example.com/v1/corrupt"


class FakeStructuredDataProvider(StructuredDataProvider):
    """
    Deterministic in-memory structured data provider with standard fixtures and
    configurable fault injection capabilities.
    """

    def __init__(
        self,
        provider_id: str = "fake-structured-provider",
        name: str = "Fake Structured Data Provider",
        default_limits: Optional[StructuredDataLimits] = None,
        populate_default_fixtures: bool = True,
        allow_localhost: bool = True,
    ):
        super().__init__(
            provider_id=provider_id,
            name=name,
            default_limits=default_limits or StructuredDataLimits(),
            allow_localhost=allow_localhost,
        )
        # Fixture registry: (endpoint_url, method_name) -> StructuredDataResponse
        self._fixtures: dict[tuple[str, str], StructuredDataResponse] = {}
        # Source metadata registry: endpoint_url -> StructuredDataSource
        self._sources: dict[str, StructuredDataSource] = {}

        # Fault injection knobs
        self.simulate_failure: bool = False
        self.simulated_failure_message: str = "Simulated structured data provider failure"
        self.simulate_timeout: bool = False
        self.simulated_timeout_seconds: float = 30.0
        self.simulate_auth_error: bool = False
        self.simulated_auth_status: int = 401
        self.simulated_auth_message: str = "Invalid API key or access token"
        self.simulate_rate_limit: bool = False
        self.simulated_retry_after: float = 60.0
        self.rate_limit_countdown: int = 0
        self.simulate_quota_exhausted: bool = False
        self.simulate_quota_message: str = "Daily request quota exceeded"
        self.required_auth_headers: dict[str, str] = {}
        self.required_query_params: dict[str, str] = {}
        self.simulate_malformed_response: bool = False
        self.simulate_oversized_response: bool = False
        self.simulated_oversized_bytes: int = 20_000_000
        self.simulate_http_error: Optional[int] = None
        self.latency_seconds: float = 0.0
        self.request_log: list[StructuredDataRequest] = []

        if populate_default_fixtures:
            self._load_default_fixtures()

    # -------------------------------------------------------------------------
    # Fixture Registration API
    # -------------------------------------------------------------------------

    def add_response(
        self,
        endpoint_url: str,
        response: StructuredDataResponse,
        method: HttpMethod = HttpMethod.GET,
    ) -> None:
        """Register a pre-built StructuredDataResponse fixture for an endpoint and method."""
        key = (endpoint_url.strip().lower(), method.value.upper())
        self._fixtures[key] = response

    def register_fixture(
        self,
        response_or_url: Union[StructuredDataResponse, str],
        response: Optional[StructuredDataResponse] = None,
        method: HttpMethod = HttpMethod.GET,
        endpoint_url: Optional[str] = None,
    ) -> None:
        """Convenience method to register a fixture either as (response) or (url, response)."""
        if isinstance(response_or_url, StructuredDataResponse):
            url = (
                endpoint_url
                or response_or_url.metadata.get("endpoint_url")
                or (response_or_url.provenance.source_ref if response_or_url.provenance else "")
            )
            self.add_response(endpoint_url=url, response=response_or_url, method=method)
        else:
            if response is None:
                raise ValueError("Response must be provided when endpoint_url is passed as first argument.")
            self.add_response(endpoint_url=response_or_url, response=response, method=method)

    def add_fixture(
        self,
        endpoint_url: str,
        payload: Any,
        content_type: str = "application/json",
        status_code: int = 200,
        status_message: str = "OK",
        method: HttpMethod = HttpMethod.GET,
        pagination: Optional[PaginationMetadata] = None,
        headers: Optional[dict[str, str]] = None,
        source_type: StructuredSourceType = StructuredSourceType.REST_API,
        provider_name: str = "mock-provider",
    ) -> None:
        """Register a fixture by providing payload, content type, and status parameters."""
        url = endpoint_url.strip().lower()
        m_str = method.value.upper()

        prov = EvidenceProvenance(
            request_id=f"prov-req-{uuid.uuid4().hex[:6]}",
            crawler_task_id=f"prov-task-{uuid.uuid4().hex[:6]}",
            crawler_id=self.provider_id,
            source_ref=endpoint_url,
        )

        nct = StructuredContentType.from_mime_type(content_type)
        resp = StructuredDataResponse(
            response_id=f"resp-{uuid.uuid4().hex[:8]}",
            request_id=f"req-{uuid.uuid4().hex[:8]}",
            status_code=status_code,
            status_message=status_message,
            content_type=content_type,
            normalized_content_type=nct,
            payload=payload,
            pagination_info=pagination,
            headers=dict(headers or {}),
            provenance=prov,
        )
        self._fixtures[(url, m_str)] = resp

        # Also register source metadata
        self._sources[url] = StructuredDataSource(
            source_id=f"src-{uuid.uuid4().hex[:6]}",
            provider=provider_name,
            endpoint_url=endpoint_url,
            source_type=source_type,
            content_type=nct,
            provenance=prov,
        )

    # -------------------------------------------------------------------------
    # StructuredDataProvider Interface
    # -------------------------------------------------------------------------

    def execute_request(
        self,
        request: StructuredDataRequest,
        is_cancelled: Optional[Callable[[], bool]] = None,
    ) -> StructuredDataResponse:
        """
        Execute structured data request against in-memory fixtures with fault injection.
        """
        endpoint = request.endpoint_url
        method_str = request.method.value.upper()
        norm_url = endpoint.strip().lower()

        # 1. Check cancellation upfront
        self.check_cancellation(is_cancelled, target=endpoint, operation="execute_request")

        # 2. Security validation
        self.validate_request_security(request)

        # Record request
        self.request_log.append(request)

        # 3. Simulate latency if requested
        if self.latency_seconds > 0:
            time.sleep(self.latency_seconds)
            self.check_cancellation(is_cancelled, target=endpoint, operation="execute_request")

        # 4. Fault injection: Timeout
        if self.simulate_timeout:
            logger.info(f"Fault injection: simulating timeout for {endpoint}")
            raise StructuredDataTimeoutError(
                target=endpoint,
                operation="execute_request",
                timeout_seconds=self.simulated_timeout_seconds,
            )

        # 5. Fault injection: General provider failure
        if self.simulate_failure:
            logger.info(f"Fault injection: simulating provider failure for {endpoint}")
            raise StructuredDataProviderError(self.simulated_failure_message)

        # 6. Fault injection: Authentication error (401/403)
        if self.simulate_auth_error:
            logger.info(f"Fault injection: simulating auth error for {endpoint}")
            if self.simulated_auth_status == 403:
                raise StructuredDataAuthorizationError(
                    target=endpoint,
                    status_code=403,
                    message=self.simulated_auth_message,
                )
            raise StructuredDataAuthenticationError(
                target=endpoint,
                status_code=self.simulated_auth_status,
                message=self.simulated_auth_message,
            )

        # 6b. Verification of required auth headers
        if self.required_auth_headers:
            for req_h, req_v in self.required_auth_headers.items():
                actual_v = request.headers.get(req_h) or request.headers.get(req_h.lower())
                if actual_v is None:
                    raise StructuredDataAuthenticationError(
                        target=endpoint,
                        status_code=401,
                        message=f"Missing required authorization header '{req_h}'",
                    )
                if actual_v != req_v:
                    raise StructuredDataAuthenticationError(
                        target=endpoint,
                        status_code=401,
                        message=f"Invalid credential in authorization header '{req_h}'",
                    )

        # 6c. Verification of required query parameters
        if self.required_query_params:
            for req_p, req_v in self.required_query_params.items():
                actual_v = request.query_params.get(req_p)
                if actual_v is None:
                    raise StructuredDataAuthenticationError(
                        target=endpoint,
                        status_code=401,
                        message=f"Missing required query parameter '{req_p}'",
                    )
                if str(actual_v) != str(req_v):
                    raise StructuredDataAuthenticationError(
                        target=endpoint,
                        status_code=401,
                        message=f"Invalid credential in query parameter '{req_p}'",
                    )

        # 7. Fault injection: Quota exhaustion
        if self.simulate_quota_exhausted:
            logger.info(f"Fault injection: simulating quota exhaustion for {endpoint}")
            raise StructuredDataQuotaExceededError(
                provider_id=self.provider_id,
                message=self.simulate_quota_message,
            )

        # 7b. Fault injection: Countdown Rate limiting (fails N times, then succeeds)
        if self.rate_limit_countdown > 0:
            self.rate_limit_countdown -= 1
            logger.info(f"Fault injection: countdown rate limit for {endpoint} (remaining: {self.rate_limit_countdown})")
            raise StructuredDataRateLimitError(
                provider_id=self.provider_id,
                retry_after_seconds=self.simulated_retry_after,
            )

        # 7c. Fault injection: Rate limiting (429)
        if self.simulate_rate_limit:
            logger.info(f"Fault injection: simulating rate limit for {endpoint}")
            raise StructuredDataRateLimitError(
                provider_id=self.provider_id,
                retry_after_seconds=self.simulated_retry_after,
            )

        # 8. Fault injection: Malformed response
        if self.simulate_malformed_response:
            logger.info(f"Fault injection: simulating malformed response for {endpoint}")
            raise StructuredDataMalformedResponseError(
                target=endpoint,
                content_type="application/json",
                reason="Unexpected EOF in JSON input",
            )

        # 9. Fault injection: Oversized response
        if self.simulate_oversized_response:
            limits = request.limits or self.default_limits
            self.enforce_size_limit(self.simulated_oversized_bytes, limits)

        # 10. Fault injection: Specific HTTP error code (e.g. 404, 500)
        if self.simulate_http_error is not None:
            err_code = self.simulate_http_error
            if err_code == 404:
                raise StructuredDataNotFoundError(target=endpoint, message="HTTP 404 Not Found")
            prov = EvidenceProvenance(
                request_id=request.request_id,
                crawler_task_id="task-err",
                crawler_id=self.provider_id,
                source_ref=endpoint,
            )
            return StructuredDataResponse(
                response_id=f"resp-err-{uuid.uuid4().hex[:6]}",
                request_id=request.request_id,
                status_code=err_code,
                status_message=f"HTTP Error {err_code}",
                content_type="application/json",
                payload={"error": f"HTTP {err_code}"},
                provenance=prov,
            )

        # 11. Retrieve fixture
        key = None
        if request.query_params:
            encoded_q = urllib.parse.urlencode(sorted(request.query_params.items()), doseq=True).lower()
            q_key = (f"{norm_url}?{encoded_q}", method_str)
            if q_key in self._fixtures:
                key = q_key
            else:
                req_q = {str(k).lower(): str(v).lower() for k, v in request.query_params.items()}
                for (fix_url, fix_method) in self._fixtures:
                    if fix_method == method_str and "?" in fix_url:
                        fix_base, fix_query = fix_url.split("?", 1)
                        if fix_base.lower() == norm_url:
                            fix_q = {k.lower(): v.lower() for k, v in urllib.parse.parse_qsl(fix_query, keep_blank_values=True)}
                            if fix_q == req_q:
                                key = (fix_url, fix_method)
                                break

        if key is None:
            if (norm_url, method_str) in self._fixtures:
                key = (norm_url, method_str)
            elif "?" in norm_url:
                base_url = norm_url.split("?", 1)[0]
                if (base_url, method_str) in self._fixtures:
                    key = (base_url, method_str)
            elif (norm_url, "GET") in self._fixtures:
                key = (norm_url, "GET")
            else:
                logger.warning(f"Endpoint fixture not found: {endpoint} [{method_str}]")
                raise StructuredDataNotFoundError(
                    target=endpoint,
                    message=f"No mock fixture registered for '{endpoint}' [{method_str}]",
                )

        fixture = self._fixtures[key]

        # 12. Check payload size against request limits
        limits = request.limits or self.default_limits
        self.enforce_size_limit(fixture.response_bytes, limits)

        # 13. Deep copy to ensure hermetic immutability across calls
        cloned = copy.deepcopy(fixture)
        cloned.request_id = request.request_id
        cloned.retrieved_at = utc_now()
        return cloned

    def get_source_metadata(
        self,
        endpoint_url: str,
        is_cancelled: Optional[Callable[[], bool]] = None,
    ) -> StructuredDataSource:
        """
        Retrieve metadata regarding a structured data endpoint without body retrieval.
        """
        self.check_cancellation(is_cancelled, target=endpoint_url, operation="get_source_metadata")

        if self.simulate_timeout:
            raise StructuredDataTimeoutError(
                target=endpoint_url,
                operation="get_source_metadata",
                timeout_seconds=self.simulated_timeout_seconds,
            )

        if self.simulate_failure:
            raise StructuredDataProviderError(self.simulated_failure_message)

        norm_url = endpoint_url.strip().lower()
        if norm_url in self._sources:
            return copy.deepcopy(self._sources[norm_url])

        # If fixture exists but no explicit source registered, derive from fixture
        for (f_url, _), f_resp in self._fixtures.items():
            if f_url == norm_url:
                src = StructuredDataSource(
                    source_id=f"src-{uuid.uuid4().hex[:6]}",
                    provider="mock-derived-provider",
                    endpoint_url=endpoint_url,
                    source_type=StructuredSourceType.REST_API,
                    content_type=f_resp.normalized_content_type,
                    provenance=f_resp.provenance,
                )
                self._sources[norm_url] = src
                return copy.deepcopy(src)

        raise StructuredDataNotFoundError(
            target=endpoint_url,
            message=f"Source metadata not found for '{endpoint_url}'",
        )

    # -------------------------------------------------------------------------
    # Default Fixtures
    # -------------------------------------------------------------------------

    def _load_default_fixtures(self) -> None:
        """Populate standard deterministic fixtures covering JSON, XML, CSV, pagination, empty, and corrupt."""

        # 1. Single JSON Object (Status)
        self.add_fixture(
            endpoint_url=URL_STATUS,
            payload={"status": "healthy", "service": "catalog-api", "version": "2.4.0", "uptime_seconds": 86400},
            content_type="application/json",
            status_code=200,
            provider_name="system-status",
        )

        # 2. Nested JSON Object (User profile)
        self.add_fixture(
            endpoint_url=URL_USER_NESTED,
            payload={
                "id": 42,
                "username": "ada_lovelace",
                "profile": {
                    "first_name": "Ada",
                    "last_name": "Lovelace",
                    "title": "First Programmer",
                    "contact": {"email": "ada@example.org", "verified": True},
                },
                "roles": ["admin", "analyst"],
                "active": True,
            },
            content_type="application/json",
            status_code=200,
            provider_name="user-service",
        )

        # 3. Array of Structured Objects (Items list)
        self.add_fixture(
            endpoint_url=URL_ITEMS_ARRAY,
            payload=[
                {"id": 1, "sku": "WIDGET-01", "name": "Standard Widget", "price": 9.99, "stock": 100},
                {"id": 2, "sku": "WIDGET-02", "name": "Premium Widget", "price": 19.99, "stock": 50},
                {"id": 3, "sku": "GADGET-01", "name": "Deluxe Gadget", "price": 49.99, "stock": 25},
            ],
            content_type="application/json",
            status_code=200,
            provider_name="inventory-api",
        )

        # 4. Paginated JSON Array with PaginationMetadata
        self.add_fixture(
            endpoint_url=URL_PAGINATED,
            payload={
                "page": 1,
                "per_page": 2,
                "total": 4,
                "items": [
                    {"id": 101, "name": "Item 101", "category": "electronics"},
                    {"id": 102, "name": "Item 102", "category": "books"},
                ],
            },
            content_type="application/json",
            status_code=200,
            pagination=PaginationMetadata(
                has_more=True,
                next_page=2,
                next_cursor="cursor_page_2",
                total_records=4,
                total_pages=2,
                current_page=1,
            ),
            provider_name="catalog-api",
        )

        # 5. XML Feed
        xml_content = (
            '<?xml version="1.0" encoding="UTF-8"?>\n'
            '<feed xmlns="http://www.w3.org/2005/Atom">\n'
            "  <title>System Updates</title>\n"
            "  <entry><id>1</id><title>Release v1</title></entry>\n"
            "  <entry><id>2</id><title>Release v2</title></entry>\n"
            "</feed>"
        )
        self.add_fixture(
            endpoint_url=URL_XML_FEED,
            payload=xml_content,
            content_type="application/xml",
            status_code=200,
            source_type=StructuredSourceType.XML_FEED,
            provider_name="rss-feed-service",
        )

        # 6. CSV Dataset
        csv_content = (
            "id,metric_name,value,recorded_at\n"
            "1,cpu_percent,14.5,2026-09-05T00:00:00Z\n"
            "2,memory_percent,62.1,2026-09-05T00:00:00Z\n"
            "3,disk_percent,44.8,2026-09-05T00:00:00Z\n"
        )
        self.add_fixture(
            endpoint_url=URL_CSV_DATA,
            payload=csv_content,
            content_type="text/csv",
            status_code=200,
            source_type=StructuredSourceType.CSV_ENDPOINT,
            provider_name="telemetry-csv",
        )

        # 7. Empty Response (204 No Content)
        self.add_fixture(
            endpoint_url=URL_EMPTY_204,
            payload=None,
            content_type="application/json",
            status_code=204,
            status_message="No Content",
            provider_name="empty-service",
        )

        # 8. Corrupted / Malformed Payload
        corrupt_raw = '{"status": "ok", "broken": [1, 2, '  # invalid unclosed JSON
        self.add_fixture(
            endpoint_url=URL_CORRUPT,
            payload=corrupt_raw,
            content_type="application/json",
            status_code=200,
            provider_name="corrupt-service",
        )
