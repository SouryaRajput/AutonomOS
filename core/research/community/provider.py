"""
Discussion Provider Abstraction (Phase 1 / Part 6 / Step 2).

Decouples community discussion querying, thread retrieval, and comment navigation
from concrete hosting platforms (Reddit, GitHub Discussions, Stack Exchange, Discourse, web forums).
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
import logging
import time
from typing import Any, Callable, Optional

from core.research.community.models import (
    AccessStatus,
    CommunityContext,
    CommunityPlatform,
    Discussion,
    DiscussionPost,
    DiscussionStatus,
    ThreadOrdering,
    utc_now,
)
from core.research.errors import (
    CommunityCancelledError,
    CommunityDiscussionNotFoundError,
    CommunityError,
    CommunityProviderError,
    CommunityResourceLimitError,
    CommunityTimeoutError,
    CommunityValidationError,
)

logger = logging.getLogger("AutonomOS.Research.DiscussionProvider")


@dataclass(frozen=True)
class DiscussionFetchLimits:
    """
    Configurable resource and bounded execution limits for community discussion operations.
    """
    max_posts_per_discussion: int = 200    # Maximum comments/posts per discussion retrieval
    max_depth: int = 10                   # Maximum thread nesting depth
    max_search_results: int = 50          # Maximum discussions returned per search query
    max_content_bytes: int = 500_000      # 500 KB per post body limit
    timeout_seconds: float = 30.0         # Default per-operation timeout in seconds

    def __post_init__(self) -> None:
        if self.max_posts_per_discussion <= 0:
            raise CommunityValidationError("max_posts_per_discussion", "max_posts_per_discussion must be > 0.")
        if self.max_depth <= 0:
            raise CommunityValidationError("max_depth", "max_depth must be > 0.")
        if self.max_search_results <= 0:
            raise CommunityValidationError("max_search_results", "max_search_results must be > 0.")
        if self.max_content_bytes <= 0:
            raise CommunityValidationError("max_content_bytes", "max_content_bytes must be > 0.")
        if self.timeout_seconds <= 0:
            raise CommunityValidationError("timeout_seconds", "timeout_seconds must be > 0.")

    def to_dict(self) -> dict[str, Any]:
        return {
            "max_posts_per_discussion": self.max_posts_per_discussion,
            "max_depth": self.max_depth,
            "max_search_results": self.max_search_results,
            "max_content_bytes": self.max_content_bytes,
            "timeout_seconds": self.timeout_seconds,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> DiscussionFetchLimits:
        return cls(
            max_posts_per_discussion=int(data.get("max_posts_per_discussion", 200)),
            max_depth=int(data.get("max_depth", 10)),
            max_search_results=int(data.get("max_search_results", 50)),
            max_content_bytes=int(data.get("max_content_bytes", 500_000)),
            timeout_seconds=float(data.get("timeout_seconds", 30.0)),
        )


@dataclass(frozen=True)
class DiscussionSearchParams:
    """
    Parameters for querying/searching discussions across or within communities.
    """
    query: str
    platform: Optional[CommunityPlatform] = None
    community_id: Optional[str] = None
    tags: list[str] = field(default_factory=list)
    status: Optional[DiscussionStatus] = None
    after_date: Optional[str] = None
    before_date: Optional[str] = None
    limit: int = 10
    timeout_seconds: Optional[float] = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.query or not isinstance(self.query, str) or not self.query.strip():
            raise CommunityValidationError("query", "Search query cannot be empty.")
        if self.limit <= 0:
            raise CommunityValidationError("limit", f"Limit must be > 0 (got {self.limit}).")
        if self.timeout_seconds is not None and self.timeout_seconds <= 0:
            raise CommunityValidationError("timeout_seconds", "timeout_seconds must be > 0.")


@dataclass(frozen=True)
class DiscussionRetrievalParams:
    """
    Parameters for retrieving a specific discussion thread by ID or URL.
    """
    discussion_id: str
    platform: Optional[CommunityPlatform] = None
    community_id: Optional[str] = None
    max_comments: Optional[int] = None
    max_depth: Optional[int] = None
    include_comments: bool = True
    timeout_seconds: Optional[float] = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.discussion_id or not isinstance(self.discussion_id, str) or not self.discussion_id.strip():
            raise CommunityValidationError("discussion_id", "Discussion ID cannot be empty.")
        if self.max_comments is not None and self.max_comments <= 0:
            raise CommunityValidationError("max_comments", "max_comments must be > 0.")
        if self.max_depth is not None and self.max_depth <= 0:
            raise CommunityValidationError("max_depth", "max_depth must be > 0.")
        if self.timeout_seconds is not None and self.timeout_seconds <= 0:
            raise CommunityValidationError("timeout_seconds", "timeout_seconds must be > 0.")


@dataclass(frozen=True)
class DiscussionCommentsParams:
    """
    Parameters for retrieving comments/replies of a discussion, optionally bounded by parent or depth.
    """
    discussion_id: str
    platform: Optional[CommunityPlatform] = None
    community_id: Optional[str] = None
    parent_id: Optional[str] = None
    max_comments: Optional[int] = None
    max_depth: Optional[int] = None
    ordering: ThreadOrdering = ThreadOrdering.CHRONOLOGICAL
    timeout_seconds: Optional[float] = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.discussion_id or not isinstance(self.discussion_id, str) or not self.discussion_id.strip():
            raise CommunityValidationError("discussion_id", "Discussion ID cannot be empty.")
        if self.max_comments is not None and self.max_comments <= 0:
            raise CommunityValidationError("max_comments", "max_comments must be > 0.")
        if self.max_depth is not None and self.max_depth <= 0:
            raise CommunityValidationError("max_depth", "max_depth must be > 0.")
        if self.timeout_seconds is not None and self.timeout_seconds <= 0:
            raise CommunityValidationError("timeout_seconds", "timeout_seconds must be > 0.")


@dataclass
class DiscussionSearchResponse:
    """
    Standard response container for discussion searches.
    """
    query: str
    results: list[Discussion] = field(default_factory=list)
    total_found: int = 0
    execution_time_seconds: float = 0.0
    provider: str = ""
    platform: Optional[CommunityPlatform] = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "query": self.query,
            "results": [r.to_dict() for r in self.results],
            "total_found": self.total_found,
            "execution_time_seconds": self.execution_time_seconds,
            "provider": self.provider,
            "platform": self.platform.value if self.platform else None,
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> DiscussionSearchResponse:
        platform_raw = data.get("platform")
        platform = CommunityPlatform.from_string(platform_raw) if platform_raw else None
        results = [Discussion.from_dict(d) for d in data.get("results", []) if isinstance(d, dict)]
        return cls(
            query=data.get("query", ""),
            results=results,
            total_found=int(data.get("total_found", len(results))),
            execution_time_seconds=float(data.get("execution_time_seconds", 0.0)),
            provider=data.get("provider", ""),
            platform=platform,
            metadata=dict(data.get("metadata", {})),
        )


class DiscussionProvider(ABC):
    """
    Abstract Base Class for Discussion Data Access Providers.

    Encapsulates platform-specific API communication, query execution,
    thread fetching, and comment hierarchy traversal without coupling
    to specific platform SDKs or execution models.
    """

    def __init__(
        self,
        provider_id: str,
        platform: CommunityPlatform,
        name: str,
        limits: Optional[DiscussionFetchLimits] = None,
    ):
        if not provider_id or not provider_id.strip():
            raise CommunityValidationError("provider_id", "Provider identifier cannot be empty.")
        if not name or not name.strip():
            raise CommunityValidationError("name", "Provider name cannot be empty.")

        self._provider_id = provider_id.strip()
        self._platform = platform if isinstance(platform, CommunityPlatform) else CommunityPlatform.from_string(platform)
        self._name = name.strip()
        self._limits = limits or DiscussionFetchLimits()

    @property
    def provider_id(self) -> str:
        return self._provider_id

    @property
    def platform(self) -> CommunityPlatform:
        return self._platform

    @property
    def name(self) -> str:
        return self._name

    @property
    def limits(self) -> DiscussionFetchLimits:
        return self._limits

    def check_cancellation(
        self,
        is_cancelled: Optional[Callable[[], bool]],
        target: str,
        operation: str,
    ) -> None:
        """
        Check cancellation predicate and raise CommunityCancelledError if triggered.
        """
        if is_cancelled is not None and is_cancelled():
            logger.info(f"Community operation '{operation}' for '{target}' cancelled by caller.")
            raise CommunityCancelledError(target=target, operation=operation)

    @abstractmethod
    def search_discussions(
        self,
        params: DiscussionSearchParams,
        is_cancelled: Optional[Callable[[], bool]] = None,
    ) -> DiscussionSearchResponse:
        """
        Search for discussions matching query, community restriction, and filter criteria.

        Raises:
            CommunityTimeoutError: If the search operation times out.
            CommunityCancelledError: If cancelled by caller.
            CommunityProviderError: On provider communication failure.
        """
        pass

    @abstractmethod
    def get_discussion(
        self,
        params: DiscussionRetrievalParams,
        is_cancelled: Optional[Callable[[], bool]] = None,
    ) -> Discussion:
        """
        Retrieve a specific discussion thread by ID with bounded comments.

        Raises:
            CommunityDiscussionNotFoundError: If discussion does not exist.
            CommunityAuthenticationError: If access is restricted or private without authorization.
            CommunityTimeoutError: If retrieval times out.
            CommunityCancelledError: If cancelled by caller.
            CommunityProviderError: On provider communication failure.
        """
        pass

    @abstractmethod
    def get_comments(
        self,
        params: DiscussionCommentsParams,
        is_cancelled: Optional[Callable[[], bool]] = None,
    ) -> list[DiscussionPost]:
        """
        Retrieve comments/replies of a discussion, optionally bounded by parent_id, depth, or count.

        Raises:
            CommunityDiscussionNotFoundError: If discussion does not exist.
            CommunityTimeoutError: If retrieval times out.
            CommunityCancelledError: If cancelled by caller.
            CommunityProviderError: On provider communication failure.
        """
        pass

    @abstractmethod
    def get_community_metadata(
        self,
        community_id: str,
        platform: Optional[CommunityPlatform] = None,
        timeout_seconds: Optional[float] = None,
        is_cancelled: Optional[Callable[[], bool]] = None,
    ) -> CommunityContext:
        """
        Retrieve metadata regarding a specific community, subreddit, board, or repository discussions.

        Raises:
            CommunityDiscussionNotFoundError: If community does not exist.
            CommunityAuthenticationError: If community is private/restricted.
            CommunityTimeoutError: If retrieval times out.
            CommunityCancelledError: If cancelled by caller.
            CommunityProviderError: On provider failure.
        """
        pass

    def retrieve_comment_subtree(
        self,
        discussion_id: str,
        root_comment_id: str,
        max_comments: Optional[int] = None,
        max_depth: Optional[int] = None,
        timeout_seconds: Optional[float] = None,
        is_cancelled: Optional[Callable[[], bool]] = None,
    ) -> list[DiscussionPost]:
        """
        Retrieve a specific comment subtree/branch for deep hierarchy expansion.
        Default implementation calls get_comments targeting the parent root_comment_id.
        """
        params = DiscussionCommentsParams(
            discussion_id=discussion_id,
            platform=self.platform,
            parent_id=root_comment_id,
            max_comments=max_comments,
            max_depth=max_depth,
            timeout_seconds=timeout_seconds,
        )
        return self.get_comments(params, is_cancelled=is_cancelled)

