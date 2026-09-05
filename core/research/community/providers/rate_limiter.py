"""
Rate Limiting Middleware for Community Discussion Providers.

Implements token bucket rate limiting, jittered backoff, and retry handling for live HTTP discussion APIs.
"""
from __future__ import annotations

import logging
import random
import threading
import time
from typing import Callable, Optional

from core.research.community.models import (
    CommunityContext,
    CommunityPlatform,
    Discussion,
    DiscussionPost,
)
from core.research.community.provider import (
    DiscussionCommentsParams,
    DiscussionFetchLimits,
    DiscussionProvider,
    DiscussionRetrievalParams,
    DiscussionSearchParams,
    DiscussionSearchResponse,
)
from core.research.errors import (
    CommunityRateLimitError,
    CommunityResourceLimitError,
    CommunityTimeoutError,
)

logger = logging.getLogger("AutonomOS.Research.Community.RateLimiter")


class RateLimitedDiscussionProvider(DiscussionProvider):
    """
    Decorator that wraps any DiscussionProvider with token-bucket rate limiting
    and transparent retry with jittered backoff on rate-limit errors.
    """

    def __init__(
        self,
        inner_provider: DiscussionProvider,
        requests_per_minute: float = 30.0,
        burst_capacity: int = 5,
        max_retries: int = 3,
        base_backoff_seconds: float = 1.0,
        max_backoff_seconds: float = 30.0,
    ):
        super().__init__(
            provider_id=f"rate_limited_{inner_provider.provider_id}",
            platform=inner_provider.platform,
            name=f"RateLimited({inner_provider.name})",
            limits=inner_provider.limits,
        )
        self._inner = inner_provider
        self._rate = requests_per_minute / 60.0  # tokens per second
        self._capacity = float(burst_capacity)
        self._tokens = float(burst_capacity)
        self._last_refill = time.monotonic()
        self._lock = threading.Lock()
        self._max_retries = max_retries
        self._base_backoff = base_backoff_seconds
        self._max_backoff = max_backoff_seconds

    @property
    def inner_provider(self) -> DiscussionProvider:
        return self._inner

    def _acquire_token(self, timeout_seconds: float = 10.0) -> None:
        """Acquire a rate limit token, blocking if necessary until a token is available."""
        deadline = time.monotonic() + timeout_seconds
        while True:
            with self._lock:
                now = time.monotonic()
                elapsed = now - self._last_refill
                self._tokens = min(self._capacity, self._tokens + elapsed * self._rate)
                self._last_refill = now

                if self._tokens >= 1.0:
                    self._tokens -= 1.0
                    return

                needed = 1.0 - self._tokens
                wait_time = needed / self._rate if self._rate > 0 else 1.0

            if time.monotonic() + wait_time > deadline:
                raise CommunityResourceLimitError(
                    resource_type="requests_per_minute",
                    actual_value=int(self._capacity),
                    max_limit=int(self._rate * 60),
                )
            time.sleep(min(wait_time, 0.5))

    def _execute_with_retry(
        self,
        func: Callable[[], Any],
        operation_name: str,
        target_name: str,
        is_cancelled: Optional[Callable[[], bool]] = None,
    ) -> Any:
        retries = 0
        while True:
            self.check_cancellation(is_cancelled, target_name, operation_name)
            self._acquire_token(timeout_seconds=self.limits.timeout_seconds)

            try:
                return func()
            except (CommunityRateLimitError, CommunityResourceLimitError) as err:
                retries += 1
                if retries > self._max_retries:
                    logger.warning(
                        f"Rate limit exceeded for {operation_name} ({target_name}) after {retries} retries: {err}"
                    )
                    raise

                backoff = min(
                    self._max_backoff,
                    self._base_backoff * (2 ** (retries - 1)) + random.uniform(0.1, 0.5),
                )
                logger.info(
                    f"Rate limited during {operation_name} on {target_name}. Backing off for {backoff:.2f}s (retry {retries}/{self._max_retries})."
                )
                time.sleep(backoff)

    def search_discussions(
        self,
        params: DiscussionSearchParams,
        is_cancelled: Optional[Callable[[], bool]] = None,
    ) -> DiscussionSearchResponse:
        return self._execute_with_retry(
            lambda: self._inner.search_discussions(params, is_cancelled=is_cancelled),
            operation_name="search_discussions",
            target_name=params.query,
            is_cancelled=is_cancelled,
        )

    def get_discussion(
        self,
        params: DiscussionRetrievalParams,
        is_cancelled: Optional[Callable[[], bool]] = None,
    ) -> Discussion:
        return self._execute_with_retry(
            lambda: self._inner.get_discussion(params, is_cancelled=is_cancelled),
            operation_name="get_discussion",
            target_name=params.discussion_id,
            is_cancelled=is_cancelled,
        )

    def get_comments(
        self,
        params: DiscussionCommentsParams,
        is_cancelled: Optional[Callable[[], bool]] = None,
    ) -> list[DiscussionPost]:
        return self._execute_with_retry(
            lambda: self._inner.get_comments(params, is_cancelled=is_cancelled),
            operation_name="get_comments",
            target_name=params.discussion_id,
            is_cancelled=is_cancelled,
        )

    def get_community_metadata(
        self,
        community_id: str,
        platform: Optional[CommunityPlatform] = None,
        timeout_seconds: Optional[float] = None,
        is_cancelled: Optional[Callable[[], bool]] = None,
    ) -> CommunityContext:
        return self._execute_with_retry(
            lambda: self._inner.get_community_metadata(
                community_id=community_id,
                platform=platform,
                timeout_seconds=timeout_seconds,
                is_cancelled=is_cancelled,
            ),
            operation_name="get_community_metadata",
            target_name=community_id,
            is_cancelled=is_cancelled,
        )

    def retrieve_comment_subtree(
        self,
        discussion_id: str,
        root_comment_id: str,
        max_comments: Optional[int] = None,
        max_depth: Optional[int] = None,
        timeout_seconds: Optional[float] = None,
        is_cancelled: Optional[Callable[[], bool]] = None,
    ) -> list[DiscussionPost]:
        return self._execute_with_retry(
            lambda: self._inner.retrieve_comment_subtree(
                discussion_id=discussion_id,
                root_comment_id=root_comment_id,
                max_comments=max_comments,
                max_depth=max_depth,
                timeout_seconds=timeout_seconds,
                is_cancelled=is_cancelled,
            ),
            operation_name="retrieve_comment_subtree",
            target_name=f"{discussion_id}:{root_comment_id}",
            is_cancelled=is_cancelled,
        )

