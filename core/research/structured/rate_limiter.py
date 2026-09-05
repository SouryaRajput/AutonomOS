"""
Rate Limiting, Retry-After Parsing, and Bounded Backoff Middleware (Phase 1 / Part 7 / Step 7).

Coordinates rate-limit handling for StructuredDataCrawler:
- Parses numeric and RFC 7231 HTTP-date Retry-After headers.
- Implements bounded exponential backoff with jitter.
- Enforces strict maximum retry counts to prevent infinite retry loops.
- Provides cancellation responsiveness during backoff sleep.
- Differentiates temporary 429 rate limits from non-retryable 401/403 auth errors and quota exhaustion.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import email.utils
import logging
import random
import time
from typing import Callable, Optional

from core.research.errors import (
    StructuredDataAuthenticationError,
    StructuredDataAuthorizationError,
    StructuredDataCancelledError,
    StructuredDataQuotaExceededError,
    StructuredDataRateLimitError,
)
from core.research.structured.models import (
    StructuredDataLimits,
    StructuredDataRequest,
    StructuredDataResponse,
    StructuredDataSource,
    StructuredRecord,
)
from core.research.structured.provider import StructuredDataProvider

logger = logging.getLogger("AutonomOS.Research.Structured.RateLimiter")


def parse_retry_after(
    header_value: Optional[str],
    default_seconds: float = 5.0,
    max_allowed_seconds: float = 60.0,
) -> float:
    """
    Parse a Retry-After header value into seconds.
    Supports:
    1. Integer or float delta-seconds (e.g. "120", "2.5")
    2. HTTP-date formats defined in RFC 7231 / RFC 2822 (e.g. "Wed, 21 Oct 2026 07:28:00 GMT")
    Binds output between 0.0 and max_allowed_seconds.
    """
    if not header_value or not isinstance(header_value, str):
        return default_seconds

    raw = header_value.strip()
    if not raw:
        return default_seconds

    # 1. Try parsing numeric delta seconds
    try:
        val = float(raw)
        if val >= 0:
            return min(val, max_allowed_seconds)
    except (ValueError, TypeError):
        pass

    # 2. Try parsing HTTP-date
    try:
        dt = email.utils.parsedate_to_datetime(raw)
        if dt:
            now = datetime.now(timezone.utc)
            delta = (dt - now).total_seconds()
            if delta > 0:
                return min(delta, max_allowed_seconds)
            return 0.0
    except Exception:
        pass

    return default_seconds


@dataclass(frozen=True)
class RateLimitConfig:
    """Bounded configuration for rate limit retry and backoff middleware."""
    max_retries: int = 3
    base_backoff_seconds: float = 1.0
    max_backoff_seconds: float = 30.0
    backoff_multiplier: float = 2.0
    jitter: bool = True
    honor_retry_after: bool = True
    max_retry_after_seconds: float = 60.0


class RateLimitedStructuredDataProvider(StructuredDataProvider):
    """
    Decorator wrapping a StructuredDataProvider to provide bounded retries and
    cancellation-aware backoff on 429 rate limit errors.
    """

    def __init__(
        self,
        inner_provider: StructuredDataProvider,
        config: Optional[RateLimitConfig] = None,
    ):
        super().__init__(
            provider_id=f"rate_limited_{inner_provider.provider_id}",
            name=f"RateLimited({inner_provider.name})",
            default_limits=inner_provider.default_limits,
            allow_localhost=inner_provider.allow_localhost,
        )
        self._inner = inner_provider
        self.config = config or RateLimitConfig()

    @property
    def inner_provider(self) -> StructuredDataProvider:
        return self._inner

    def execute_request(
        self,
        request: StructuredDataRequest,
        is_cancelled: Optional[Callable[[], bool]] = None,
    ) -> StructuredDataResponse:
        """
        Execute request with bounded jittered exponential backoff upon 429 rate limits.
        """
        retries = 0

        while True:
            self.check_cancellation(is_cancelled, target=request.endpoint_url, operation="execute_request")

            try:
                response = self._inner.execute_request(request, is_cancelled=is_cancelled)

                # Check if response status code is 429
                if response.status_code == 429:
                    retry_header = response.headers.get("retry-after") or response.headers.get("Retry-After")
                    retry_seconds = parse_retry_after(
                        retry_header,
                        default_seconds=self.config.base_backoff_seconds,
                        max_allowed_seconds=self.config.max_retry_after_seconds,
                    )
                    raise StructuredDataRateLimitError(
                        provider_id=self._inner.provider_id,
                        retry_after_seconds=retry_seconds,
                    )

                return response

            except StructuredDataRateLimitError as rate_err:
                # Quota exhaustion is non-retryable
                if isinstance(rate_err, StructuredDataQuotaExceededError):
                    logger.warning(f"Quota exhausted for '{request.endpoint_url}'. Halting retries.")
                    raise

                retries += 1
                if retries > self.config.max_retries:
                    logger.warning(
                        f"Max retries ({self.config.max_retries}) exceeded for rate-limited endpoint '{request.endpoint_url}': {rate_err}"
                    )
                    raise

                # Calculate backoff duration
                if self.config.honor_retry_after and rate_err.retry_after_seconds > 0:
                    backoff = min(rate_err.retry_after_seconds, self.config.max_retry_after_seconds)
                else:
                    exp = self.config.base_backoff_seconds * (self.config.backoff_multiplier ** (retries - 1))
                    backoff = min(exp, self.config.max_backoff_seconds)

                if self.config.jitter:
                    backoff += random.uniform(0.05, 0.25)

                logger.info(
                    f"Rate limit 429 on '{request.endpoint_url}'. Backing off for {backoff:.2f}s (retry {retries}/{self.config.max_retries})."
                )

                # Cooperative cancellation-aware sleep
                self._sleep_with_cancellation(backoff, is_cancelled, target=request.endpoint_url)

    def _sleep_with_cancellation(
        self,
        total_seconds: float,
        is_cancelled: Optional[Callable[[], bool]],
        target: str,
        slice_seconds: float = 0.05,
    ) -> None:
        """
        Sleep in tiny non-blocking slices, continually checking for cooperative cancellation.
        """
        remaining = total_seconds
        while remaining > 0:
            self.check_cancellation(is_cancelled, target=target, operation="rate_limit_backoff")
            step = min(remaining, slice_seconds)
            time.sleep(step)
            remaining -= step
        self.check_cancellation(is_cancelled, target=target, operation="rate_limit_backoff")

    def get_source_metadata(
        self,
        endpoint_url: str,
        is_cancelled: Optional[Callable[[], bool]] = None,
    ) -> StructuredDataSource:
        return self._inner.get_source_metadata(endpoint_url, is_cancelled=is_cancelled)
