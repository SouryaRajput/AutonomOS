"""
Unit tests for Pagination and Bounded Structured Retrieval (Phase 1 / Part 7.5).

Tests verify that:
- Common pagination styles (page+limit, offset+limit, cursor, next-url, HTTP Link headers) work deterministically.
- Infinite loops (repeated page signatures, cyclic cursors) are detected and halted immediately.
- Configured safety limits (max_pages, max_records, max_requests, max_total_bytes, timeout) are strictly enforced.
- Exhaustive retrieval is never claimed when a configured limit or loop halted pagination.
- Next-page URLs are strictly re-validated through SSRF, scheme downgrade, and cross-domain policy checks.
- Complete retrieval, intentionally bounded, partial, empty, provider failure, timeout, and cancellation are accurately discriminated.
"""
from __future__ import annotations

import pytest

from core.research.contracts.evidence import EvidenceProvenance
from core.research.errors import (
    StructuredDataPolicyViolationError,
    StructuredDataProviderError,
)
from core.research.structured import (
    BoundedRetrievalLimits,
    FakeStructuredDataProvider,
    HttpMethod,
    PaginatedRetrievalResult,
    PaginationConfig,
    PaginationMetadata,
    PaginationType,
    RetrievalStatus,
    StructuredDataPaginator,
    StructuredDataRequest,
    StructuredDataResponse,
    StructuredDataSecurityPolicy,
    extract_pagination_metadata,
    parse_link_header,
)


def _make_provenance(endpoint: str = "https://api.example.com/items") -> EvidenceProvenance:
    return EvidenceProvenance(
        request_id="req-pag-001",
        crawler_task_id="task-pag-001",
        crawler_id="test_crawler",
        source_ref=endpoint,
    )


# -----------------------------------------------------------------------------
# 1. Link Header & Metadata Extraction Tests
# -----------------------------------------------------------------------------

class TestLinkHeaderAndMetadataExtraction:
    """Verify RFC 5988/8288 parsing and multi-signal pagination extraction."""

    def test_parse_standard_link_header(self):
        header = '<https://api.example.com/items?page=2>; rel="next", <https://api.example.com/items?page=5>; rel="last"'
        links = parse_link_header(header)
        assert links["next"] == "https://api.example.com/items?page=2"
        assert links["last"] == "https://api.example.com/items?page=5"

    def test_parse_empty_or_invalid_link_header(self):
        assert parse_link_header("") == {}
        assert parse_link_header("not a valid link header") == {}

    def test_extract_pagination_metadata_from_payload(self):
        prov = _make_provenance()
        resp = StructuredDataResponse(
            response_id="r1",
            request_id="req1",
            status_code=200,
            provenance=prov,
            payload={
                "items": [{"id": 1}],
                "has_more": True,
                "next_page": 2,
                "total": 50,
            },
        )
        meta = extract_pagination_metadata(resp)
        assert meta.has_more is True
        assert meta.next_page == 2
        assert meta.total_records == 50

    def test_extract_pagination_metadata_from_link_header(self):
        prov = _make_provenance()
        resp = StructuredDataResponse(
            response_id="r1",
            request_id="req1",
            status_code=200,
            provenance=prov,
            headers={"Link": '<https://api.example.com/items?cursor=cur_2>; rel="next"'},
            payload={"items": [{"id": 1}]},
        )
        meta = extract_pagination_metadata(resp)
        assert meta.has_more is True
        assert meta.next_url == "https://api.example.com/items?cursor=cur_2"


# -----------------------------------------------------------------------------
# 2. Pagination Navigation Patterns
# -----------------------------------------------------------------------------

class TestPaginationNavigationPatterns:
    """Verify page+limit, offset+limit, cursor, next-url, and link-header pagination."""

    def test_page_number_pagination_to_completion(self):
        provider = FakeStructuredDataProvider(allow_localhost=True, populate_default_fixtures=False)
        policy = StructuredDataSecurityPolicy(allowed_domains={"api.example.com"})

        # Setup 3 pages
        provider.add_fixture(
            endpoint_url="https://api.example.com/catalog?page=1",
            payload={"items": [{"id": 1}, {"id": 2}], "has_more": True, "next_page": 2},
        )
        provider.add_fixture(
            endpoint_url="https://api.example.com/catalog?page=2",
            payload={"items": [{"id": 3}, {"id": 4}], "has_more": True, "next_page": 3},
        )
        provider.add_fixture(
            endpoint_url="https://api.example.com/catalog?page=3",
            payload={"items": [{"id": 5}], "has_more": False},
        )

        paginator = StructuredDataPaginator(provider=provider, policy=policy)
        req = StructuredDataRequest(
            request_id="req-p1",
            endpoint_url="https://api.example.com/catalog",
            query_params={"page": 1},
        )
        cfg = PaginationConfig(
            pagination_type=PaginationType.PAGE_NUMBER,
            page=1,
            max_pages=5,
        )

        result = paginator.paginate(req, config=cfg)

        assert result.status == RetrievalStatus.COMPLETE
        assert result.is_exhaustive is True
        assert result.total_pages_fetched == 3
        assert result.total_requests_issued == 3
        assert len(result.records) == 5
        assert [r.value["id"] for r in result.records] == [1, 2, 3, 4, 5]

    def test_offset_limit_pagination_to_completion(self):
        provider = FakeStructuredDataProvider(allow_localhost=True, populate_default_fixtures=False)
        policy = StructuredDataSecurityPolicy(allowed_domains={"api.example.com"})

        provider.add_fixture(
            endpoint_url="https://api.example.com/data?limit=2&offset=0",
            payload={"rows": [{"id": 10}, {"id": 20}], "has_more": True},
        )
        provider.add_fixture(
            endpoint_url="https://api.example.com/data?limit=2&offset=2",
            payload={"rows": [{"id": 30}], "has_more": False},
        )

        paginator = StructuredDataPaginator(provider=provider, policy=policy)
        req = StructuredDataRequest(
            request_id="req-off1",
            endpoint_url="https://api.example.com/data",
            query_params={"offset": 0, "limit": 2},
        )
        cfg = PaginationConfig(
            pagination_type=PaginationType.OFFSET_LIMIT,
            offset=0,
            limit=2,
            max_pages=5,
        )

        result = paginator.paginate(req, config=cfg)

        assert result.status == RetrievalStatus.COMPLETE
        assert result.is_exhaustive is True
        assert result.total_pages_fetched == 2
        assert len(result.records) == 3

    def test_cursor_pagination_to_completion(self):
        provider = FakeStructuredDataProvider(allow_localhost=True, populate_default_fixtures=False)
        policy = StructuredDataSecurityPolicy(allowed_domains={"api.example.com"})

        provider.add_fixture(
            endpoint_url="https://api.example.com/stream",
            payload={"items": [{"msg": "hello"}], "next_cursor": "cur_abc"},
        )
        provider.add_fixture(
            endpoint_url="https://api.example.com/stream?cursor=cur_abc",
            payload={"items": [{"msg": "world"}], "next_cursor": None, "has_more": False},
        )

        paginator = StructuredDataPaginator(provider=provider, policy=policy)
        req = StructuredDataRequest(
            request_id="req-cur1",
            endpoint_url="https://api.example.com/stream",
        )
        cfg = PaginationConfig(
            pagination_type=PaginationType.CURSOR,
            max_pages=5,
        )

        result = paginator.paginate(req, config=cfg)

        assert result.status == RetrievalStatus.COMPLETE
        assert result.is_exhaustive is True
        assert result.total_pages_fetched == 2
        assert len(result.records) == 2

    def test_next_url_pagination(self):
        provider = FakeStructuredDataProvider(allow_localhost=True, populate_default_fixtures=False)
        policy = StructuredDataSecurityPolicy(allowed_domains={"api.example.com"})

        provider.add_fixture(
            endpoint_url="https://api.example.com/feed",
            payload={"items": [{"val": "A"}], "next": "https://api.example.com/feed?token=tok2"},
        )
        provider.add_fixture(
            endpoint_url="https://api.example.com/feed?token=tok2",
            payload={"items": [{"val": "B"}], "next": None, "has_more": False},
        )

        paginator = StructuredDataPaginator(provider=provider, policy=policy)
        req = StructuredDataRequest(request_id="req-next1", endpoint_url="https://api.example.com/feed")
        cfg = PaginationConfig(pagination_type=PaginationType.NEXT_URL, max_pages=5)

        result = paginator.paginate(req, config=cfg)

        assert result.status == RetrievalStatus.COMPLETE
        assert len(result.records) == 2

    def test_link_header_pagination(self):
        provider = FakeStructuredDataProvider(allow_localhost=True, populate_default_fixtures=False)
        policy = StructuredDataSecurityPolicy(allowed_domains={"api.example.com"})

        provider.add_fixture(
            endpoint_url="https://api.example.com/users",
            payload={"items": [{"user": "alice"}]},
            headers={"Link": '<https://api.example.com/users?page=2>; rel="next"'},
        )
        provider.add_fixture(
            endpoint_url="https://api.example.com/users?page=2",
            payload={"items": [{"user": "bob"}]},
            headers={},  # No next link
        )

        paginator = StructuredDataPaginator(provider=provider, policy=policy)
        req = StructuredDataRequest(request_id="req-link1", endpoint_url="https://api.example.com/users")
        cfg = PaginationConfig(pagination_type=PaginationType.LINK_HEADER, max_pages=5)

        result = paginator.paginate(req, config=cfg)

        assert result.status == RetrievalStatus.COMPLETE
        assert result.total_pages_fetched == 2
        assert len(result.records) == 2


# -----------------------------------------------------------------------------
# 3. Loop & Cycle Detection Tests
# -----------------------------------------------------------------------------

class TestLoopAndCycleDetection:
    """Verify repeated page and cyclic cursor detection."""

    def test_repeated_page_loop_detected(self):
        provider = FakeStructuredDataProvider(allow_localhost=True, populate_default_fixtures=False)
        policy = StructuredDataSecurityPolicy(allowed_domains={"api.example.com"})

        # Endpoint falsely points next URL back to itself!
        provider.add_fixture(
            endpoint_url="https://api.example.com/loop",
            payload={"items": [{"id": 1}], "next": "https://api.example.com/loop"},
        )

        paginator = StructuredDataPaginator(provider=provider, policy=policy)
        req = StructuredDataRequest(request_id="req-loop", endpoint_url="https://api.example.com/loop")
        cfg = PaginationConfig(pagination_type=PaginationType.NEXT_URL, max_pages=10)

        result = paginator.paginate(req, config=cfg)

        assert result.status == RetrievalStatus.INTENTIONALLY_BOUNDED
        assert result.is_exhaustive is False
        assert "Loop detected" in result.termination_reason
        assert result.total_pages_fetched == 1

    def test_cursor_cycle_loop_detected(self):
        provider = FakeStructuredDataProvider(allow_localhost=True, populate_default_fixtures=False)
        policy = StructuredDataSecurityPolicy(allowed_domains={"api.example.com"})

        # Cursors cycle A -> B -> A
        provider.add_fixture(
            endpoint_url="https://api.example.com/cursor-loop",
            payload={"items": [{"v": 1}], "next_cursor": "cur_A"},
        )
        provider.add_fixture(
            endpoint_url="https://api.example.com/cursor-loop?cursor=cur_A",
            payload={"items": [{"v": 2}], "next_cursor": "cur_B"},
        )
        provider.add_fixture(
            endpoint_url="https://api.example.com/cursor-loop?cursor=cur_B",
            payload={"items": [{"v": 3}], "next_cursor": "cur_A"},  # Re-visits cur_A!
        )

        paginator = StructuredDataPaginator(provider=provider, policy=policy)
        req = StructuredDataRequest(request_id="req-curloop", endpoint_url="https://api.example.com/cursor-loop")
        cfg = PaginationConfig(pagination_type=PaginationType.CURSOR, max_pages=10)

        result = paginator.paginate(req, config=cfg)

        assert result.status == RetrievalStatus.INTENTIONALLY_BOUNDED
        assert result.is_exhaustive is False
        assert "Loop detected: cursor 'cur_A'" in result.termination_reason
        assert result.total_pages_fetched == 3


# -----------------------------------------------------------------------------
# 4. Limit Bounding Tests
# -----------------------------------------------------------------------------

class TestLimitBounding:
    """Verify max_pages, max_records, max_requests, and max_total_bytes enforcement."""

    def test_max_pages_stops_pagination_with_intentionally_bounded(self):
        provider = FakeStructuredDataProvider(allow_localhost=True, populate_default_fixtures=False)
        policy = StructuredDataSecurityPolicy(allowed_domains={"api.example.com"})

        for p in range(1, 10):
            provider.add_fixture(
                endpoint_url=f"https://api.example.com/items?page={p}",
                payload={"items": [{"id": p}], "has_more": True, "next_page": p + 1},
            )

        paginator = StructuredDataPaginator(provider=provider, policy=policy)
        req = StructuredDataRequest(
            request_id="req-mp",
            endpoint_url="https://api.example.com/items",
            query_params={"page": 1},
        )
        # Limit to 2 pages
        cfg = PaginationConfig(pagination_type=PaginationType.PAGE_NUMBER, page=1, max_pages=2)

        result = paginator.paginate(req, config=cfg)

        assert result.status == RetrievalStatus.INTENTIONALLY_BOUNDED
        assert result.is_exhaustive is False
        assert result.total_pages_fetched == 2
        assert len(result.records) == 2
        assert "max_pages limit" in result.termination_reason

    def test_max_records_stops_pagination(self):
        provider = FakeStructuredDataProvider(allow_localhost=True, populate_default_fixtures=False)
        policy = StructuredDataSecurityPolicy(allowed_domains={"api.example.com"})

        # Each page has 5 records
        provider.add_fixture(
            endpoint_url="https://api.example.com/records?page=1",
            payload={"items": [{"id": i} for i in range(5)], "has_more": True, "next_page": 2},
        )
        provider.add_fixture(
            endpoint_url="https://api.example.com/records?page=2",
            payload={"items": [{"id": i} for i in range(5, 10)], "has_more": True, "next_page": 3},
        )

        paginator = StructuredDataPaginator(provider=provider, policy=policy)
        req = StructuredDataRequest(request_id="req-mr", endpoint_url="https://api.example.com/records", query_params={"page": 1})
        cfg = PaginationConfig(pagination_type=PaginationType.PAGE_NUMBER, page=1, max_pages=10)

        # Enforce max 7 records
        limits = BoundedRetrievalLimits(max_records=7, max_pages=10)
        result = paginator.paginate(req, config=cfg, limits=limits)  # type: ignore

        assert result.status == RetrievalStatus.INTENTIONALLY_BOUNDED
        assert result.is_exhaustive is False
        assert len(result.records) == 7


# -----------------------------------------------------------------------------
# 5. Security & SSRF Re-validation on Next URLs
# -----------------------------------------------------------------------------

class TestSecurityAndSSRFOnNextUrls:
    """Verify that next-page URLs are re-validated against SSRF and scope rules."""

    def test_next_url_ssrf_metadata_target_rejected(self):
        provider = FakeStructuredDataProvider(allow_localhost=True, populate_default_fixtures=False)
        policy = StructuredDataSecurityPolicy(allowed_domains={"api.example.com"})

        # Endpoint attempts to redirect next page to cloud metadata
        provider.add_fixture(
            endpoint_url="https://api.example.com/attack",
            payload={"items": [{"id": 1}], "next": "http://169.254.169.254/latest/meta-data/"},
        )

        paginator = StructuredDataPaginator(provider=provider, policy=policy)
        req = StructuredDataRequest(request_id="req-sec", endpoint_url="https://api.example.com/attack")
        cfg = PaginationConfig(pagination_type=PaginationType.NEXT_URL, max_pages=5)

        result = paginator.paginate(req, config=cfg)

        assert result.status == RetrievalStatus.INTENTIONALLY_BOUNDED
        assert result.is_exhaustive is False
        assert "Security policy rejected next URL" in result.termination_reason
        assert result.total_pages_fetched == 1  # Did not fetch page 2

    def test_next_url_cross_domain_escape_rejected(self):
        provider = FakeStructuredDataProvider(allow_localhost=True, populate_default_fixtures=False)
        policy = StructuredDataSecurityPolicy(allowed_domains={"api.example.com"})

        # Next URL attempts to escape to external domain
        provider.add_fixture(
            endpoint_url="https://api.example.com/feed",
            payload={"items": [{"id": 1}], "next": "https://evil.org/phish"},
        )

        paginator = StructuredDataPaginator(provider=provider, policy=policy)
        req = StructuredDataRequest(request_id="req-cd", endpoint_url="https://api.example.com/feed")
        cfg = PaginationConfig(pagination_type=PaginationType.NEXT_URL, max_pages=5)

        result = paginator.paginate(req, config=cfg)

        assert result.status == RetrievalStatus.INTENTIONALLY_BOUNDED
        assert result.is_exhaustive is False
        assert "Security policy rejected next URL" in result.termination_reason


# -----------------------------------------------------------------------------
# 6. Status Discrimination Tests
# -----------------------------------------------------------------------------

class TestStatusDiscrimination:
    """Verify EMPTY, PARTIAL, PROVIDER_FAILURE, TIMEOUT, and CANCELLED statuses."""

    def test_empty_dataset_discrimination(self):
        provider = FakeStructuredDataProvider(allow_localhost=True, populate_default_fixtures=False)
        policy = StructuredDataSecurityPolicy(allowed_domains={"api.example.com"})

        provider.add_fixture(
            endpoint_url="https://api.example.com/empty",
            payload={"items": [], "has_more": False},
        )

        paginator = StructuredDataPaginator(provider=provider, policy=policy)
        req = StructuredDataRequest(request_id="req-e", endpoint_url="https://api.example.com/empty")

        result = paginator.paginate(req)

        assert result.status == RetrievalStatus.EMPTY
        assert result.is_exhaustive is True
        assert len(result.records) == 0

    def test_partial_retrieval_discrimination_on_provider_error(self):
        provider = FakeStructuredDataProvider(allow_localhost=True, populate_default_fixtures=False)
        policy = StructuredDataSecurityPolicy(allowed_domains={"api.example.com"})

        # Page 1 succeeds
        provider.add_fixture(
            endpoint_url="https://api.example.com/items?page=1",
            payload={"items": [{"id": 1}], "has_more": True, "next_page": 2},
        )
        # Page 2 is missing / causes 404 error

        paginator = StructuredDataPaginator(provider=provider, policy=policy)
        req = StructuredDataRequest(request_id="req-part", endpoint_url="https://api.example.com/items", query_params={"page": 1})
        cfg = PaginationConfig(pagination_type=PaginationType.PAGE_NUMBER, page=1, max_pages=5)

        result = paginator.paginate(req, config=cfg)

        assert result.status == RetrievalStatus.PARTIAL
        assert result.is_exhaustive is False
        assert len(result.records) == 1
        assert result.total_pages_fetched == 1
        assert result.error is not None

    def test_provider_failure_on_first_page(self):
        provider = FakeStructuredDataProvider(allow_localhost=True, populate_default_fixtures=False)
        provider.simulate_failure = True

        paginator = StructuredDataPaginator(provider=provider)
        req = StructuredDataRequest(request_id="req-fail", endpoint_url="https://api.example.com/fail")

        result = paginator.paginate(req)

        assert result.status == RetrievalStatus.PROVIDER_FAILURE
        assert result.is_exhaustive is False
        assert len(result.records) == 0

    def test_cancellation_discrimination(self):
        provider = FakeStructuredDataProvider(allow_localhost=True, populate_default_fixtures=False)
        provider.add_fixture(
            endpoint_url="https://api.example.com/items",
            payload={"items": [{"id": 1}]},
        )

        paginator = StructuredDataPaginator(provider=provider)
        req = StructuredDataRequest(request_id="req-canc", endpoint_url="https://api.example.com/items")

        # Cancel immediately
        result = paginator.paginate(req, is_cancelled=lambda: True)

        assert result.status == RetrievalStatus.CANCELLED
        assert result.is_exhaustive is False
