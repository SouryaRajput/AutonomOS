from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
import logging
import re
import time
from typing import Any, Callable, Optional, Union
import urllib.parse
import uuid

from core.research.errors import (
    StructuredDataCancelledError,
    StructuredDataLimitError,
    StructuredDataPolicyViolationError,
    StructuredDataProviderError,
    StructuredDataTimeoutError,
)
from core.research.structured.builder import StructuredDataRequestBuilder
from core.research.structured.models import (
    PaginationConfig,
    PaginationMetadata,
    PaginationType,
    SourceLocation,
    StructuredDataLimits,
    StructuredDataRequest,
    StructuredDataResponse,
    StructuredRecord,
    canonical_json_dumps,
    compute_structured_hash,
)
from core.research.structured.parser import StructuredResponseParser
from core.research.structured.policy import StructuredDataSecurityPolicy
from core.research.structured.provider import StructuredDataProvider

logger = logging.getLogger("AutonomOS.Research.Structured.Paginator")


# -----------------------------------------------------------------------------
# Retrieval Classifications & Boundaries
# -----------------------------------------------------------------------------

class RetrievalStatus(str, Enum):
    """
    Epistemic and operational classification of a paginated structured data retrieval.
    Strictly distinguishes complete datasets from intentionally bounded or partial results.
    """
    COMPLETE = "complete"                       # All pages retrieved; endpoint indicated no more data
    INTENTIONALLY_BOUNDED = "intentionally_bounded" # Configured limit reached or loop avoided prior to exhaustion
    PARTIAL = "partial"                         # Partial records retrieved before encountering provider failure
    PROVIDER_FAILURE = "provider_failure"       # Provider failure occurred before any records could be retrieved
    TIMEOUT = "timeout"                         # Retrieval exceeded maximum allowed execution deadline
    CANCELLED = "cancelled"                     # Operation cancelled by caller predicate
    EMPTY = "empty"                             # First page returned zero records and no subsequent pages


@dataclass(frozen=True)
class BoundedRetrievalLimits:
    """
    Explicit safety and resource bounds governing multi-page retrieval.
    Prevents runaway pagination, resource exhaustion, and infinite loop traps.
    """
    max_pages: int = 10                         # Maximum pages to navigate
    max_requests: int = 20                      # Maximum HTTP requests to issue
    max_records: int = 10_000                   # Maximum discrete structured records to collect
    max_response_bytes: int = 10_000_000        # 10 MB maximum per individual page response
    max_total_bytes: int = 50_000_000           # 50 MB maximum cumulative response bytes
    max_execution_seconds: float = 60.0         # Overall operation time envelope
    max_concurrency: int = 5                    # Concurrency ceiling for multi-threaded fetchers

    @classmethod
    def from_limits_and_config(
        cls,
        limits: Optional[Union[StructuredDataLimits, BoundedRetrievalLimits]] = None,
        config: Optional[PaginationConfig] = None,
    ) -> BoundedRetrievalLimits:
        """Derive bounded retrieval limits from StructuredDataLimits and PaginationConfig."""
        if isinstance(limits, BoundedRetrievalLimits):
            return limits

        max_p = config.max_pages if (config and config.max_pages is not None and config.max_pages > 0) else 10
        max_rec = limits.max_records if limits else 10_000
        max_resp_b = limits.max_bytes if limits else 10_000_000
        timeout = limits.timeout_seconds if limits else 60.0

        return cls(
            max_pages=max_p,
            max_requests=max(max_p * 2, 20),
            max_records=max_rec,
            max_response_bytes=max_resp_b,
            max_total_bytes=max_resp_b * 5,
            max_execution_seconds=timeout if timeout > 0 else 60.0,
        )


@dataclass
class PaginatedRetrievalResult:
    """
    Consolidated outcome of bounded multi-page structured retrieval.
    Maintains complete audit provenance, discrete records, and termination rationale.
    """
    status: RetrievalStatus
    records: list[StructuredRecord] = field(default_factory=list)
    pages: list[StructuredDataResponse] = field(default_factory=list)
    total_pages_fetched: int = 0
    total_requests_issued: int = 0
    total_records_collected: int = 0
    total_bytes_retrieved: int = 0
    is_exhaustive: bool = False
    termination_reason: str = ""
    error: Optional[Exception] = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status.value,
            "total_pages_fetched": self.total_pages_fetched,
            "total_requests_issued": self.total_requests_issued,
            "total_records_collected": self.total_records_collected,
            "total_bytes_retrieved": self.total_bytes_retrieved,
            "is_exhaustive": self.is_exhaustive,
            "termination_reason": self.termination_reason,
            "error": str(self.error) if self.error else None,
            "metadata": dict(self.metadata),
        }


# -----------------------------------------------------------------------------
# Link Header & Metadata Extraction
# -----------------------------------------------------------------------------

def parse_link_header(link_header: str) -> dict[str, str]:
    """
    Parse standard RFC 5988 / RFC 8288 Link headers into a rel -> URL mapping.
    Example: '<https://api.example.com/items?page=2>; rel="next"' -> {"next": "..."}
    """
    if not link_header or not isinstance(link_header, str):
        return {}

    links: dict[str, str] = {}
    # Split on commas that precede an opening angle bracket
    for part in re.split(r",\s*(?=<)", link_header.strip()):
        sections = part.split(";")
        if len(sections) < 2:
            continue
        url = sections[0].strip().strip("<>").strip()
        for param in sections[1:]:
            param_clean = param.strip()
            if param_clean.lower().startswith("rel="):
                rel = param_clean[4:].strip("\"'").lower()
                links[rel] = url
    return links


def extract_pagination_metadata(
    response: StructuredDataResponse,
    config: Optional[PaginationConfig] = None,
) -> PaginationMetadata:
    """
    Extract pagination signals from response pagination_info, HTTP Link headers,
    standard response headers, and structured payload fields.
    """
    # 1. Prefer explicitly attached provider pagination info
    has_more = False
    next_p = None
    next_c = None
    next_u = None
    total_r = None
    total_p = None
    curr_p = None
    meta_dict: dict[str, Any] = {}

    if response.pagination_info:
        info = response.pagination_info
        has_more = info.has_more
        next_p = info.next_page
        next_c = info.next_cursor
        next_u = info.next_url
        total_r = info.total_records
        total_p = info.total_pages
        curr_p = info.current_page
        meta_dict.update(info.metadata)

    # 2. Inspect Link headers (RFC 5988 / 8288)
    link_header = response.headers.get("link") or response.headers.get("Link", "")
    if link_header:
        links = parse_link_header(link_header)
        if "next" in links:
            next_u = links["next"]
            has_more = True

    # 3. Inspect common X-Pagination headers
    for k, v in response.headers.items():
        k_low = k.lower()
        if k_low in ("x-total-count", "x-total", "total-count"):
            try:
                total_r = int(v)
            except ValueError:
                pass
        elif k_low in ("x-total-pages", "x-pages"):
            try:
                total_p = int(v)
            except ValueError:
                pass
        elif k_low in ("x-next-page",):
            try:
                next_p = int(v)
                has_more = True
            except ValueError:
                pass

    # 4. Inspect payload fields if payload is dictionary
    payload = response.payload
    if isinstance(payload, dict):
        # Top-level next link / cursor / has_more keys
        if "has_more" in payload and isinstance(payload["has_more"], bool):
            has_more = payload["has_more"]

        if "next" in payload:
            next_val = payload["next"]
            if isinstance(next_val, str) and next_val.strip():
                next_u = next_val.strip()
                has_more = True
            elif next_val is None or next_val is False:
                has_more = False
        elif "next_url" in payload and isinstance(payload["next_url"], str):
            next_u = payload["next_url"].strip()
            has_more = True
        elif "next_page_url" in payload and isinstance(payload["next_page_url"], str):
            next_u = payload["next_page_url"].strip()
            has_more = True

        if "next_cursor" in payload and isinstance(payload["next_cursor"], str):
            next_c = payload["next_cursor"]
            has_more = True
        elif "cursor" in payload and isinstance(payload["cursor"], str):
            next_c = payload["cursor"]

        if "next_page" in payload:
            try:
                next_p = int(payload["next_page"])
                has_more = True
            except (ValueError, TypeError):
                pass

        if "total" in payload:
            try:
                total_r = int(payload["total"])
            except (ValueError, TypeError):
                pass
        elif "total_records" in payload:
            try:
                total_r = int(payload["total_records"])
            except (ValueError, TypeError):
                pass

        if "total_pages" in payload:
            try:
                total_p = int(payload["total_pages"])
            except (ValueError, TypeError):
                pass

        if "page" in payload:
            try:
                curr_p = int(payload["page"])
            except (ValueError, TypeError):
                pass

        # Check nested pagination metadata block (e.g. meta: { pagination: { ... } })
        for container_key in ("pagination", "meta", "page_info", "paging"):
            block = payload.get(container_key)
            if isinstance(block, dict):
                if "has_more" in block and isinstance(block["has_more"], bool):
                    has_more = block["has_more"]
                if "next_cursor" in block and isinstance(block["next_cursor"], str):
                    next_c = block["next_cursor"]
                    has_more = True
                if "next" in block and isinstance(block["next"], str):
                    next_u = block["next"]
                    has_more = True
                if "next_page" in block:
                    try:
                        next_p = int(block["next_page"])
                        has_more = True
                    except (ValueError, TypeError):
                        pass

    # If next_page or next_cursor or next_url is set, imply has_more if not explicitly False
    if (next_p or next_c or next_u) and not has_more:
        has_more = True

    return PaginationMetadata(
        has_more=has_more,
        next_page=next_p,
        next_cursor=next_c,
        next_url=next_u,
        total_records=total_r,
        total_pages=total_p,
        current_page=curr_p,
        metadata=meta_dict,
    )


# -----------------------------------------------------------------------------
# Structured Data Paginator
# -----------------------------------------------------------------------------

class StructuredDataPaginator:
    """
    Bounded pagination engine for structured data acquisition.
    Enforces resource ceilings, loop detection, and strict security validation
    on all traversed endpoints.
    """

    def __init__(
        self,
        provider: StructuredDataProvider,
        policy: Optional[StructuredDataSecurityPolicy] = None,
        parser: Optional[StructuredResponseParser] = None,
    ):
        self.provider: StructuredDataProvider = provider
        self.policy: StructuredDataSecurityPolicy = policy or StructuredDataSecurityPolicy()
        self.parser: StructuredResponseParser = parser or StructuredResponseParser()

    def paginate(
        self,
        initial_request: StructuredDataRequest,
        config: Optional[PaginationConfig] = None,
        limits: Optional[StructuredDataLimits] = None,
        is_cancelled: Optional[Callable[[], bool]] = None,
    ) -> PaginatedRetrievalResult:
        """
        Execute bounded paginated retrieval across multiple pages until completion,
        limit exhaustion, cancellation, or error.
        """
        start_time = time.monotonic()
        pagination_cfg = config or initial_request.pagination or PaginationConfig()
        retrieval_limits = BoundedRetrievalLimits.from_limits_and_config(limits, pagination_cfg)

        pages: list[StructuredDataResponse] = []
        records: list[StructuredRecord] = []
        seen_page_signatures: set[str] = set()
        seen_cursors: set[str] = set()
        seen_record_ids: set[str] = set()

        total_pages_fetched = 0
        total_requests_issued = 0
        total_bytes_retrieved = 0

        current_request = initial_request
        current_page_num = pagination_cfg.page or 1
        current_offset = pagination_cfg.offset or 0

        # Primary pagination loop
        while True:
            # 1. Cancellation check
            if is_cancelled and is_cancelled():
                return PaginatedRetrievalResult(
                    status=RetrievalStatus.CANCELLED,
                    records=records,
                    pages=pages,
                    total_pages_fetched=total_pages_fetched,
                    total_requests_issued=total_requests_issued,
                    total_records_collected=len(records),
                    total_bytes_retrieved=total_bytes_retrieved,
                    is_exhaustive=False,
                    termination_reason="Operation cancelled by caller predicate.",
                )

            # 2. Timeout check
            elapsed = time.monotonic() - start_time
            if elapsed >= retrieval_limits.max_execution_seconds:
                return PaginatedRetrievalResult(
                    status=RetrievalStatus.TIMEOUT,
                    records=records,
                    pages=pages,
                    total_pages_fetched=total_pages_fetched,
                    total_requests_issued=total_requests_issued,
                    total_records_collected=len(records),
                    total_bytes_retrieved=total_bytes_retrieved,
                    is_exhaustive=False,
                    termination_reason=f"Execution deadline ({retrieval_limits.max_execution_seconds}s) expired.",
                )

            # 3. Maximum pages check
            if total_pages_fetched >= retrieval_limits.max_pages:
                return PaginatedRetrievalResult(
                    status=RetrievalStatus.INTENTIONALLY_BOUNDED,
                    records=records,
                    pages=pages,
                    total_pages_fetched=total_pages_fetched,
                    total_requests_issued=total_requests_issued,
                    total_records_collected=len(records),
                    total_bytes_retrieved=total_bytes_retrieved,
                    is_exhaustive=False,
                    termination_reason=f"Reached maximum pages limit ({retrieval_limits.max_pages}).",
                )

            # 4. Maximum requests check
            if total_requests_issued >= retrieval_limits.max_requests:
                return PaginatedRetrievalResult(
                    status=RetrievalStatus.INTENTIONALLY_BOUNDED,
                    records=records,
                    pages=pages,
                    total_pages_fetched=total_pages_fetched,
                    total_requests_issued=total_requests_issued,
                    total_records_collected=len(records),
                    total_bytes_retrieved=total_bytes_retrieved,
                    is_exhaustive=False,
                    termination_reason=f"Reached maximum requests limit ({retrieval_limits.max_requests}).",
                )

            # 5. Maximum records check
            if len(records) >= retrieval_limits.max_records:
                return PaginatedRetrievalResult(
                    status=RetrievalStatus.INTENTIONALLY_BOUNDED,
                    records=records,
                    pages=pages,
                    total_pages_fetched=total_pages_fetched,
                    total_requests_issued=total_requests_issued,
                    total_records_collected=len(records),
                    total_bytes_retrieved=total_bytes_retrieved,
                    is_exhaustive=False,
                    termination_reason=f"Reached maximum records limit ({retrieval_limits.max_records}).",
                )

            # 6. Maximum cumulative bytes check
            if total_bytes_retrieved >= retrieval_limits.max_total_bytes:
                return PaginatedRetrievalResult(
                    status=RetrievalStatus.INTENTIONALLY_BOUNDED,
                    records=records,
                    pages=pages,
                    total_pages_fetched=total_pages_fetched,
                    total_requests_issued=total_requests_issued,
                    total_records_collected=len(records),
                    total_bytes_retrieved=total_bytes_retrieved,
                    is_exhaustive=False,
                    termination_reason=f"Reached maximum total bytes limit ({retrieval_limits.max_total_bytes}).",
                )

            # 7. Request signature & loop detection
            clean_url = current_request.endpoint_url.strip().lower()
            encoded_params = self.policy.encode_query_params(current_request.query_params)
            signature = f"{clean_url}?{encoded_params}"

            if signature in seen_page_signatures:
                return PaginatedRetrievalResult(
                    status=RetrievalStatus.INTENTIONALLY_BOUNDED,
                    records=records,
                    pages=pages,
                    total_pages_fetched=total_pages_fetched,
                    total_requests_issued=total_requests_issued,
                    total_records_collected=len(records),
                    total_bytes_retrieved=total_bytes_retrieved,
                    is_exhaustive=False,
                    termination_reason=f"Loop detected: duplicate page request signature '{signature}'.",
                )
            seen_page_signatures.add(signature)

            # 8. Execute HTTP request via provider
            total_requests_issued += 1
            try:
                response = self.provider.execute_request(current_request, is_cancelled=is_cancelled)
            except StructuredDataCancelledError:
                return PaginatedRetrievalResult(
                    status=RetrievalStatus.CANCELLED,
                    records=records,
                    pages=pages,
                    total_pages_fetched=total_pages_fetched,
                    total_requests_issued=total_requests_issued,
                    total_records_collected=len(records),
                    total_bytes_retrieved=total_bytes_retrieved,
                    is_exhaustive=False,
                    termination_reason="Provider request cancelled.",
                )
            except StructuredDataTimeoutError:
                return PaginatedRetrievalResult(
                    status=RetrievalStatus.TIMEOUT,
                    records=records,
                    pages=pages,
                    total_pages_fetched=total_pages_fetched,
                    total_requests_issued=total_requests_issued,
                    total_records_collected=len(records),
                    total_bytes_retrieved=total_bytes_retrieved,
                    is_exhaustive=False,
                    termination_reason="Provider request timed out.",
                )
            except StructuredDataProviderError as e:
                status = RetrievalStatus.PARTIAL if records else RetrievalStatus.PROVIDER_FAILURE
                return PaginatedRetrievalResult(
                    status=status,
                    records=records,
                    pages=pages,
                    total_pages_fetched=total_pages_fetched,
                    total_requests_issued=total_requests_issued,
                    total_records_collected=len(records),
                    total_bytes_retrieved=total_bytes_retrieved,
                    is_exhaustive=False,
                    termination_reason=f"Provider error: {e}",
                    error=e,
                )

            # 9. Parse response structure
            try:
                page_limits = StructuredDataLimits(
                    max_bytes=retrieval_limits.max_response_bytes,
                    max_records=retrieval_limits.max_records,
                )
                parsed_response = self.parser.parse_response(
                    response=response,
                    limits=page_limits,
                    is_cancelled=is_cancelled,
                )
            except Exception as parse_err:
                status = RetrievalStatus.PARTIAL if records else RetrievalStatus.PROVIDER_FAILURE
                return PaginatedRetrievalResult(
                    status=status,
                    records=records,
                    pages=pages,
                    total_pages_fetched=total_pages_fetched,
                    total_requests_issued=total_requests_issued,
                    total_records_collected=len(records),
                    total_bytes_retrieved=total_bytes_retrieved,
                    is_exhaustive=False,
                    termination_reason=f"Response parse failure on page {total_pages_fetched + 1}: {parse_err}",
                    error=parse_err,
                )

            pages.append(parsed_response)
            total_pages_fetched += 1
            total_bytes_retrieved += parsed_response.response_bytes

            # 10. Extract records from this page
            base_loc = self._resolve_records_location(parsed_response, pagination_cfg)
            try:
                page_records = parsed_response.extract_records(base_loc)
            except Exception:
                page_records = []

            # Deduplicate and append records
            for rec in page_records:
                if len(records) >= retrieval_limits.max_records:
                    break
                if rec.record_id not in seen_record_ids:
                    seen_record_ids.add(rec.record_id)
                    records.append(rec)

            # 11. Extract pagination metadata from response
            pagination_meta = extract_pagination_metadata(parsed_response, pagination_cfg)

            # Check empty dataset on initial page
            if total_pages_fetched == 1 and len(records) == 0 and not pagination_meta.has_more:
                return PaginatedRetrievalResult(
                    status=RetrievalStatus.EMPTY,
                    records=[],
                    pages=pages,
                    total_pages_fetched=1,
                    total_requests_issued=total_requests_issued,
                    total_records_collected=0,
                    total_bytes_retrieved=total_bytes_retrieved,
                    is_exhaustive=True,
                    termination_reason="Initial page returned 0 records and no further pages exist.",
                )

            # If provider explicitly signals no more data, terminate with COMPLETE
            if not pagination_meta.has_more:
                return PaginatedRetrievalResult(
                    status=RetrievalStatus.COMPLETE,
                    records=records,
                    pages=pages,
                    total_pages_fetched=total_pages_fetched,
                    total_requests_issued=total_requests_issued,
                    total_records_collected=len(records),
                    total_bytes_retrieved=total_bytes_retrieved,
                    is_exhaustive=True,
                    termination_reason="Endpoint indicated no further pages exist.",
                )

            # 12. Check whether next page navigation would exceed limits
            if total_pages_fetched >= retrieval_limits.max_pages:
                return PaginatedRetrievalResult(
                    status=RetrievalStatus.INTENTIONALLY_BOUNDED,
                    records=records,
                    pages=pages,
                    total_pages_fetched=total_pages_fetched,
                    total_requests_issued=total_requests_issued,
                    total_records_collected=len(records),
                    total_bytes_retrieved=total_bytes_retrieved,
                    is_exhaustive=False,
                    termination_reason=f"Configured max_pages limit ({retrieval_limits.max_pages}) reached.",
                )

            if len(records) >= retrieval_limits.max_records:
                return PaginatedRetrievalResult(
                    status=RetrievalStatus.INTENTIONALLY_BOUNDED,
                    records=records,
                    pages=pages,
                    total_pages_fetched=total_pages_fetched,
                    total_requests_issued=total_requests_issued,
                    total_records_collected=len(records),
                    total_bytes_retrieved=total_bytes_retrieved,
                    is_exhaustive=False,
                    termination_reason=f"Configured max_records limit ({retrieval_limits.max_records}) reached.",
                )

            # 13. Construct next request according to pagination mechanism
            next_req_res = self._build_next_request(
                current_request=current_request,
                config=pagination_cfg,
                meta=pagination_meta,
                total_pages_fetched=total_pages_fetched,
                current_page_num=current_page_num,
                current_offset=current_offset,
                seen_cursors=seen_cursors,
                records=records,
                pages=pages,
                total_requests_issued=total_requests_issued,
                total_bytes_retrieved=total_bytes_retrieved,
            )

            if isinstance(next_req_res, PaginatedRetrievalResult):
                return next_req_res

            current_request, current_page_num, current_offset = next_req_res

    # -------------------------------------------------------------------------
    # Internal Helpers
    # -------------------------------------------------------------------------

    def _resolve_records_location(
        self,
        response: StructuredDataResponse,
        config: PaginationConfig,
    ) -> str:
        """Identify the source location of the records array in the parsed payload."""
        configured_path = config.metadata.get("records_path")
        if configured_path and isinstance(configured_path, str):
            return configured_path

        payload = response.payload
        if isinstance(payload, list):
            return "root"
        if isinstance(payload, dict):
            if "items" in payload and isinstance(payload["items"], list):
                return "items"
            if "rows" in payload and isinstance(payload["rows"], list):
                return "rows"
            if "data" in payload and isinstance(payload["data"], list):
                return "data"
            if "records" in payload and isinstance(payload["records"], list):
                return "records"
        return "root"

    def _build_next_request(
        self,
        current_request: StructuredDataRequest,
        config: PaginationConfig,
        meta: PaginationMetadata,
        total_pages_fetched: int,
        current_page_num: int,
        current_offset: int,
        seen_cursors: set[str],
        records: list[StructuredRecord],
        pages: list[StructuredDataResponse],
        total_requests_issued: int,
        total_bytes_retrieved: int,
    ) -> Union[tuple[StructuredDataRequest, int, int], PaginatedRetrievalResult]:
        """
        Derive the subsequent StructuredDataRequest applying pagination rules
        and strict endpoint/SSRF security re-validation.
        """
        # Determine active mechanism: explicit config or auto-detected signal
        p_type = config.pagination_type
        if p_type == PaginationType.NONE:
            if meta.next_url:
                p_type = PaginationType.NEXT_URL
            elif meta.next_cursor:
                p_type = PaginationType.CURSOR
            elif meta.next_page:
                p_type = PaginationType.PAGE_NUMBER

        next_endpoint = current_request.endpoint_url
        new_params = dict(current_request.query_params)
        next_page_num = current_page_num
        next_offset = current_offset

        # A. Next-Page URL navigation (payload 'next' or HTTP Link header)
        if p_type in (PaginationType.NEXT_URL, PaginationType.LINK_HEADER) or meta.next_url:
            raw_next_url = meta.next_url
            if not raw_next_url:
                return PaginatedRetrievalResult(
                    status=RetrievalStatus.COMPLETE,
                    records=records,
                    pages=pages,
                    total_pages_fetched=total_pages_fetched,
                    total_requests_issued=total_requests_issued,
                    total_records_collected=len(records),
                    total_bytes_retrieved=total_bytes_retrieved,
                    is_exhaustive=True,
                    termination_reason="No next URL reference provided.",
                )

            # Security re-validation against SSRF, loopback, private subnets, and downgrades
            try:
                validated_next = self.policy.validate_redirect(current_request.endpoint_url, raw_next_url)
            except StructuredDataPolicyViolationError as sec_err:
                return PaginatedRetrievalResult(
                    status=RetrievalStatus.INTENTIONALLY_BOUNDED,
                    records=records,
                    pages=pages,
                    total_pages_fetched=total_pages_fetched,
                    total_requests_issued=total_requests_issued,
                    total_records_collected=len(records),
                    total_bytes_retrieved=total_bytes_retrieved,
                    is_exhaustive=False,
                    termination_reason=f"Security policy rejected next URL: {sec_err}",
                    error=sec_err,
                )

            # Split path and query parameters
            parsed_next = urllib.parse.urlsplit(validated_next)
            next_endpoint = urllib.parse.urlunsplit((parsed_next.scheme, parsed_next.netloc, parsed_next.path, "", ""))
            new_params = dict(urllib.parse.parse_qsl(parsed_next.query, keep_blank_values=True))

        # B. Cursor navigation
        elif p_type == PaginationType.CURSOR or meta.next_cursor:
            cursor_val = meta.next_cursor
            if not cursor_val:
                return PaginatedRetrievalResult(
                    status=RetrievalStatus.COMPLETE,
                    records=records,
                    pages=pages,
                    total_pages_fetched=total_pages_fetched,
                    total_requests_issued=total_requests_issued,
                    total_records_collected=len(records),
                    total_bytes_retrieved=total_bytes_retrieved,
                    is_exhaustive=True,
                    termination_reason="No next cursor provided by endpoint.",
                )

            # Loop detection on cursor
            if cursor_val in seen_cursors:
                return PaginatedRetrievalResult(
                    status=RetrievalStatus.INTENTIONALLY_BOUNDED,
                    records=records,
                    pages=pages,
                    total_pages_fetched=total_pages_fetched,
                    total_requests_issued=total_requests_issued,
                    total_records_collected=len(records),
                    total_bytes_retrieved=total_bytes_retrieved,
                    is_exhaustive=False,
                    termination_reason=f"Loop detected: cursor '{cursor_val}' was already visited.",
                )
            seen_cursors.add(cursor_val)

            cursor_param = config.cursor_param_name or "cursor"
            new_params[cursor_param] = cursor_val

        # C. Offset + Limit navigation
        elif p_type == PaginationType.OFFSET_LIMIT:
            limit_val = config.limit or config.page_size or 10
            next_offset = current_offset + limit_val
            new_params["offset"] = next_offset
            new_params["limit"] = limit_val

        # D. Page Number navigation
        else:
            next_page_num = meta.next_page or (current_page_num + 1)
            page_param = config.page_param_name or "page"
            new_params[page_param] = next_page_num
            if config.page_size:
                size_param = config.page_size_param_name or "per_page"
                new_params[size_param] = config.page_size

        # Construct next StructuredDataRequest via fluent builder
        next_request_id = f"{current_request.request_id}_p{total_pages_fetched + 1}"
        try:
            builder = (
                StructuredDataRequestBuilder(self.policy)
                .with_request_id(next_request_id)
                .with_endpoint(next_endpoint)
                .with_method(current_request.method)
                .with_headers(dict(current_request.headers))
                .with_query_params(new_params)
                .with_limits(current_request.limits)
                .with_metadata({
                    **current_request.metadata,
                    "parent_request_id": current_request.request_id,
                    "page_number": total_pages_fetched + 1,
                })
            )
            next_req = builder.build()
        except StructuredDataPolicyViolationError as pol_err:
            return PaginatedRetrievalResult(
                status=RetrievalStatus.INTENTIONALLY_BOUNDED,
                records=records,
                pages=pages,
                total_pages_fetched=total_pages_fetched,
                total_requests_issued=total_requests_issued,
                total_records_collected=len(records),
                total_bytes_retrieved=total_bytes_retrieved,
                is_exhaustive=False,
                termination_reason=f"Next request violates policy: {pol_err}",
                error=pol_err,
            )

        return next_req, next_page_num, next_offset
