"""
Structured Data Provider Abstraction (Phase 1 / Part 7 / Step 2).

Decouples structured data source access (REST, GraphQL, JSON/JSONL, XML, CSV) from
crawler orchestration, task planning, and evidence evaluation.

Guarantees:
- Pure source/transport access: strictly NO semantic research, field interpretation, summarization, or truth evaluation.
- Explicit support for endpoints, HTTP methods, query parameters, request bodies, approved headers, timeouts, cancellation, and resource bounds.
- Failures (HTTP errors, auth failures, malformed payloads, timeouts) are never silently converted into empty successful results.
- Full preservation of status codes, content types, byte counts, content hashes, and audit provenance.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
import logging
from typing import Callable, Optional

from core.research.errors import (
    StructuredDataCancelledError,
    StructuredDataLimitError,
    StructuredDataSecurityError,
    StructuredDataValidationError,
)
from core.research.search.security import validate_network_target
from core.research.structured.models import (
    StructuredDataLimits,
    StructuredDataRequest,
    StructuredDataResponse,
    StructuredDataSource,
    StructuredRecord,
)

logger = logging.getLogger("AutonomOS.Research.StructuredDataProvider")


class StructuredDataProvider(ABC):
    """
    Abstract Base Class for Structured Data Source Providers.

    Encapsulates network communication, request execution, header negotiation,
    timeout management, and cancellation checks for structured data endpoints.
    """

    def __init__(
        self,
        provider_id: str,
        name: str,
        default_limits: Optional[StructuredDataLimits] = None,
        allow_localhost: bool = False,
    ):
        if not provider_id or not isinstance(provider_id, str) or not provider_id.strip():
            raise StructuredDataValidationError("provider_id", "Provider identifier cannot be empty.")
        if not name or not isinstance(name, str) or not name.strip():
            raise StructuredDataValidationError("name", "Provider name cannot be empty.")

        self._provider_id = provider_id.strip()
        self._name = name.strip()
        self._default_limits = default_limits or StructuredDataLimits()
        self._allow_localhost = allow_localhost

    @property
    def provider_id(self) -> str:
        return self._provider_id

    @property
    def name(self) -> str:
        return self._name

    @property
    def default_limits(self) -> StructuredDataLimits:
        return self._default_limits

    @property
    def allow_localhost(self) -> bool:
        return self._allow_localhost

    def check_cancellation(
        self,
        is_cancelled: Optional[Callable[[], bool]],
        target: str,
        operation: str = "request",
    ) -> None:
        """
        Evaluate cancellation predicate and raise StructuredDataCancelledError if triggered.
        """
        if is_cancelled is not None and is_cancelled():
            logger.info(f"Structured data operation '{operation}' for '{target}' cancelled by caller.")
            raise StructuredDataCancelledError(target=target, operation=operation)

    def validate_request_security(self, request: StructuredDataRequest) -> None:
        """
        Validate request endpoint against SSRF and network security policies.
        """
        endpoint = request.endpoint_url
        if endpoint.startswith("mock://"):
            return
        try:
            validate_network_target(endpoint, allow_localhost=self._allow_localhost)
        except Exception as e:
            logger.warning(f"Security validation rejected structured data endpoint '{endpoint}': {e}")
            raise StructuredDataSecurityError(target=endpoint, reason=str(e)) from e

    def enforce_size_limit(self, response_bytes: int, limits: StructuredDataLimits) -> None:
        """
        Enforce response payload size limit.
        """
        if response_bytes > limits.max_bytes:
            raise StructuredDataLimitError(
                limit_name="max_bytes",
                actual_value=response_bytes,
                max_allowed=limits.max_bytes,
            )

    @abstractmethod
    def execute_request(
        self,
        request: StructuredDataRequest,
        is_cancelled: Optional[Callable[[], bool]] = None,
    ) -> StructuredDataResponse:
        """
        Execute a structured data request and return the normalized response.

        Must support:
        - endpoint URL
        - HTTP method (GET, POST, etc.)
        - query parameters
        - request body
        - approved headers
        - timeout bounds
        - cancellation predicate
        - resource limits

        Raises:
            StructuredDataCancelledError: If cancelled by caller.
            StructuredDataTimeoutError: If operation times out.
            StructuredDataAuthenticationError: If access is rejected (401/403).
            StructuredDataRateLimitError: If rate limit is hit (429).
            StructuredDataNotFoundError: If endpoint not found (404).
            StructuredDataMalformedResponseError: If payload cannot be parsed.
            StructuredDataLimitError: If response size exceeds limits.
            StructuredDataProviderError: On general provider failure.
        """
        pass

    @abstractmethod
    def get_source_metadata(
        self,
        endpoint_url: str,
        is_cancelled: Optional[Callable[[], bool]] = None,
    ) -> StructuredDataSource:
        """
        Retrieve metadata regarding a structured data source/endpoint without
        fetching large payloads.

        Raises:
            StructuredDataCancelledError: If cancelled by caller.
            StructuredDataTimeoutError: If operation times out.
            StructuredDataProviderError: On failure.
        """
        pass

    def fetch_content(
        self,
        request: StructuredDataRequest,
        base_location: str = "root",
        is_cancelled: Optional[Callable[[], bool]] = None,
    ) -> tuple[StructuredDataResponse, list[StructuredRecord]]:
        """
        Template method executing request and decomposing the response into
        discrete StructuredRecord entities at the specified base location.
        """
        response = self.execute_request(request, is_cancelled=is_cancelled)
        records = response.extract_records(base_location=base_location)
        return response, records
