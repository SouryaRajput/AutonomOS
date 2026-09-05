"""
Discussion Thread Retrieval and Hierarchy Reconstruction Engine (Phase 1 / Part 6 / Step 4).

Performs bounded, deterministic retrieval of discussion threads, root posts,
and multi-level hierarchical comment trees while preserving parent/child relationships,
provenance, and explicit partial state tracking.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import logging
import time
from typing import Any, Callable, Optional
import uuid

from core.research.community.fake_provider import FakeDiscussionProvider
from core.research.community.models import (
    AccessStatus,
    CommunityContext,
    CommunityPlatform,
    Discussion,
    DiscussionPost,
    DiscussionStatus,
    EngagementMetrics,
    ThreadOrdering,
    ThreadStructure,
    compute_sha256,
    utc_now,
)
from core.research.community.provider import (
    DiscussionCommentsParams,
    DiscussionFetchLimits,
    DiscussionProvider,
    DiscussionRetrievalParams,
)
from core.research.errors import (
    CommunityAuthenticationError,
    CommunityCancelledError,
    CommunityDiscussionNotFoundError,
    CommunityError,
    CommunityProviderError,
    CommunityRateLimitError,
    CommunityResourceLimitError,
    CommunityTimeoutError,
    CommunityValidationError,
)

logger = logging.getLogger("AutonomOS.Research.DiscussionRetriever")


# -----------------------------------------------------------------------------
# Limits & Request Models
# -----------------------------------------------------------------------------

@dataclass(frozen=True)
class DiscussionRetrievalLimits:
    """
    Configurable resource and bounded execution limits for discussion thread retrieval.
    """
    max_discussions: int = 10                  # Maximum threads per batch retrieval
    max_comments_per_discussion: int = 200     # Maximum comments/replies per thread
    max_reply_depth: int = 10                  # Maximum nesting level to fetch
    max_total_bytes: int = 5_000_000           # 5 MB total payload limit across batch
    max_provider_operations: int = 50          # Budget for provider API calls
    max_execution_time_seconds: float = 30.0   # Per-batch execution timeout in seconds
    max_concurrency: int = 4                   # Maximum concurrent provider fetches

    def __post_init__(self) -> None:
        if self.max_discussions <= 0:
            raise CommunityValidationError("max_discussions", "max_discussions must be > 0.")
        if self.max_comments_per_discussion <= 0:
            raise CommunityValidationError("max_comments_per_discussion", "max_comments_per_discussion must be > 0.")
        if self.max_reply_depth <= 0:
            raise CommunityValidationError("max_reply_depth", "max_reply_depth must be > 0.")
        if self.max_total_bytes <= 0:
            raise CommunityValidationError("max_total_bytes", "max_total_bytes must be > 0.")
        if self.max_provider_operations <= 0:
            raise CommunityValidationError("max_provider_operations", "max_provider_operations must be > 0.")
        if self.max_execution_time_seconds <= 0:
            raise CommunityValidationError("max_execution_time_seconds", "max_execution_time_seconds must be > 0.")
        if self.max_concurrency <= 0:
            raise CommunityValidationError("max_concurrency", "max_concurrency must be > 0.")

    def to_dict(self) -> dict[str, Any]:
        return {
            "max_discussions": self.max_discussions,
            "max_comments_per_discussion": self.max_comments_per_discussion,
            "max_reply_depth": self.max_reply_depth,
            "max_total_bytes": self.max_total_bytes,
            "max_provider_operations": self.max_provider_operations,
            "max_execution_time_seconds": self.max_execution_time_seconds,
            "max_concurrency": self.max_concurrency,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> DiscussionRetrievalLimits:
        return cls(
            max_discussions=int(data.get("max_discussions", 10)),
            max_comments_per_discussion=int(data.get("max_comments_per_discussion", 200)),
            max_reply_depth=int(data.get("max_reply_depth", 10)),
            max_total_bytes=int(data.get("max_total_bytes", 5_000_000)),
            max_provider_operations=int(data.get("max_provider_operations", 50)),
            max_execution_time_seconds=float(data.get("max_execution_time_seconds", 30.0)),
            max_concurrency=int(data.get("max_concurrency", 4)),
        )


@dataclass(frozen=True)
class ThreadRetrievalRequest:
    """
    Explicit request specification for retrieving an individual discussion thread.
    """
    discussion_id: str
    platform: Optional[CommunityPlatform] = None
    community_id: Optional[str] = None
    url: Optional[str] = None
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

    def to_dict(self) -> dict[str, Any]:
        return {
            "discussion_id": self.discussion_id,
            "platform": self.platform.value if self.platform else None,
            "community_id": self.community_id,
            "url": self.url,
            "max_comments": self.max_comments,
            "max_depth": self.max_depth,
            "include_comments": self.include_comments,
            "timeout_seconds": self.timeout_seconds,
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ThreadRetrievalRequest:
        platform_raw = data.get("platform")
        platform = CommunityPlatform.from_string(platform_raw) if platform_raw else None
        return cls(
            discussion_id=data.get("discussion_id", ""),
            platform=platform,
            community_id=data.get("community_id"),
            url=data.get("url"),
            max_comments=data.get("max_comments"),
            max_depth=data.get("max_depth"),
            include_comments=bool(data.get("include_comments", True)),
            timeout_seconds=data.get("timeout_seconds"),
            metadata=dict(data.get("metadata", {})),
        )


@dataclass(frozen=True)
class BatchThreadRetrievalParams:
    """
    Specification for a batch of discussion threads to retrieve.
    """
    requests: list[ThreadRetrievalRequest] = field(default_factory=list)
    limits: Optional[DiscussionRetrievalLimits] = None
    timeout_seconds: Optional[float] = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.timeout_seconds is not None and self.timeout_seconds <= 0:
            raise CommunityValidationError("timeout_seconds", "timeout_seconds must be > 0.")


# -----------------------------------------------------------------------------
# Retrieval Outcome Models
# -----------------------------------------------------------------------------

@dataclass
class RetrievedDiscussionThread:
    """
    Retrieved discussion thread result preserving the hierarchical comment structure,
    content hashes, execution metrics, and explicit partial retrieval tracking.
    """
    discussion: Discussion
    is_partial: bool = False
    partial_reasons: list[str] = field(default_factory=list)
    total_posts_retrieved: int = 0
    max_depth_retrieved: int = 0
    orphans_count: int = 0
    bytes_retrieved: int = 0
    execution_time_seconds: float = 0.0
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "discussion": self.discussion.to_dict(),
            "is_partial": self.is_partial,
            "partial_reasons": list(self.partial_reasons),
            "total_posts_retrieved": self.total_posts_retrieved,
            "max_depth_retrieved": self.max_depth_retrieved,
            "orphans_count": self.orphans_count,
            "bytes_retrieved": self.bytes_retrieved,
            "execution_time_seconds": self.execution_time_seconds,
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> RetrievedDiscussionThread:
        disc = Discussion.from_dict(data["discussion"])
        return cls(
            discussion=disc,
            is_partial=bool(data.get("is_partial", False)),
            partial_reasons=list(data.get("partial_reasons", [])),
            total_posts_retrieved=int(data.get("total_posts_retrieved", disc.total_posts())),
            max_depth_retrieved=int(data.get("max_depth_retrieved", disc.thread_structure.get_thread_depth())),
            orphans_count=int(data.get("orphans_count", 0)),
            bytes_retrieved=int(data.get("bytes_retrieved", 0)),
            execution_time_seconds=float(data.get("execution_time_seconds", 0.0)),
            metadata=dict(data.get("metadata", {})),
        )


@dataclass
class BatchThreadRetrievalResult:
    """
    Aggregated outcome of a batch discussion retrieval run.
    """
    threads: list[RetrievedDiscussionThread] = field(default_factory=list)
    total_threads_retrieved: int = 0
    total_threads_failed: int = 0
    total_posts_retrieved: int = 0
    total_bytes_retrieved: int = 0
    operations_made: int = 0
    execution_time_seconds: float = 0.0
    errors: list[dict[str, str]] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "threads": [t.to_dict() for t in self.threads],
            "total_threads_retrieved": self.total_threads_retrieved,
            "total_threads_failed": self.total_threads_failed,
            "total_posts_retrieved": self.total_posts_retrieved,
            "total_bytes_retrieved": self.total_bytes_retrieved,
            "operations_made": self.operations_made,
            "execution_time_seconds": self.execution_time_seconds,
            "errors": list(self.errors),
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> BatchThreadRetrievalResult:
        threads = [RetrievedDiscussionThread.from_dict(t) for t in data.get("threads", []) if isinstance(t, dict)]
        return cls(
            threads=threads,
            total_threads_retrieved=int(data.get("total_threads_retrieved", len(threads))),
            total_threads_failed=int(data.get("total_threads_failed", len(data.get("errors", [])))),
            total_posts_retrieved=int(data.get("total_posts_retrieved", sum(t.total_posts_retrieved for t in threads))),
            total_bytes_retrieved=int(data.get("total_bytes_retrieved", sum(t.bytes_retrieved for t in threads))),
            operations_made=int(data.get("operations_made", 0)),
            execution_time_seconds=float(data.get("execution_time_seconds", 0.0)),
            errors=list(data.get("errors", [])),
            metadata=dict(data.get("metadata", {})),
        )


# -----------------------------------------------------------------------------
# Thread Retrieval Engine
# -----------------------------------------------------------------------------

class DiscussionThreadRetriever:
    """
    Coordinates bounded retrieval of discussion threads and comment hierarchies
    from discussion providers.
    
    Guarantees:
    - Preserves exact parent/child hierarchy (never flattens into an unstructured blob).
    - Tracks explicit partial state when limits or errors truncate the reply tree.
    - Never invents deleted content or fabricates missing parent pointers.
    - Enforces execution budgets (max_discussions, max_comments, max_reply_depth, max_total_bytes, max_provider_operations).
    - Untrusted content isolation: treats all returned post strings strictly as DATA.
    """

    def __init__(
        self,
        providers: Optional[list[DiscussionProvider]] = None,
        provider: Optional[DiscussionProvider] = None,
        default_limits: Optional[DiscussionRetrievalLimits] = None,
    ):
        self._providers: list[DiscussionProvider] = []
        if providers:
            self._providers.extend(providers)
        if provider and provider not in self._providers:
            self._providers.append(provider)
        self._default_limits = default_limits or DiscussionRetrievalLimits()

        if not self._providers:
            self._providers.append(FakeDiscussionProvider(
                limits=DiscussionFetchLimits(
                    max_posts_per_discussion=self._default_limits.max_comments_per_discussion,
                    max_depth=self._default_limits.max_reply_depth,
                    timeout_seconds=self._default_limits.max_execution_time_seconds,
                )
            ))

    def register_provider(self, provider: DiscussionProvider) -> None:
        """Register a discussion provider."""
        if not isinstance(provider, DiscussionProvider):
            raise CommunityValidationError("provider", "Must be a DiscussionProvider instance.")
        self._providers.append(provider)

    def _resolve_provider(self, platform: Optional[CommunityPlatform]) -> DiscussionProvider:
        """Select appropriate provider for given platform or return first registered."""
        if platform:
            for p in self._providers:
                if p.platform == platform:
                    return p
        return self._providers[0]

    def retrieve_thread(
        self,
        request: ThreadRetrievalRequest,
        limits: Optional[DiscussionRetrievalLimits] = None,
        is_cancelled: Optional[Callable[[], bool]] = None,
    ) -> RetrievedDiscussionThread:
        """
        Retrieve an individual discussion thread with bounded comments and explicit partial tracking.
        """
        start_time = time.perf_counter()
        if is_cancelled is not None and is_cancelled():
            raise CommunityCancelledError(target=request.discussion_id, operation="retrieve_thread")

        eff_limits = limits or self._default_limits
        eff_max_comments = request.max_comments or eff_limits.max_comments_per_discussion
        eff_max_depth = request.max_depth or eff_limits.max_reply_depth
        eff_timeout = request.timeout_seconds or eff_limits.max_execution_time_seconds

        provider = self._resolve_provider(request.platform)

        # Build retrieval parameters
        params = DiscussionRetrievalParams(
            discussion_id=request.discussion_id,
            platform=request.platform,
            community_id=request.community_id,
            max_comments=eff_max_comments,
            max_depth=eff_max_depth,
            include_comments=request.include_comments,
            timeout_seconds=eff_timeout,
        )

        discussion = provider.get_discussion(params=params, is_cancelled=is_cancelled)

        # ---------------------------------------------------------------------
        # Hierarchical Structure & Boundary Analysis
        # ---------------------------------------------------------------------
        is_partial = False
        partial_reasons: list[str] = []

        # If provider marked it partial
        if discussion.metadata.get("is_partial"):
            is_partial = True
            partial_reasons.append(discussion.metadata.get("partial_reason", "provider_partial_retrieval"))

        total_posts = discussion.total_posts()
        total_replies = discussion.total_replies()
        max_depth = discussion.thread_structure.get_thread_depth()
        orphans = discussion.thread_structure.get_orphan_posts()
        orphans_count = len(orphans)

        if orphans_count > 0:
            discussion.metadata["orphans_count"] = orphans_count
            discussion.metadata["orphan_ids"] = [o.post_id for o in orphans]

        # Check if requested comments limit was hit
        if request.include_comments and total_replies >= eff_max_comments:
            is_partial = True
            partial_reasons.append(f"max_comments_limit_reached:{total_replies}>={eff_max_comments}")

        # Check if depth limit was hit
        if request.include_comments and max_depth >= eff_max_depth:
            is_partial = True
            partial_reasons.append(f"max_depth_limit_reached:{max_depth}>={eff_max_depth}")

        # Calculate exact payload size in bytes
        bytes_retrieved = 0
        if discussion.title:
            bytes_retrieved += len(discussion.title.encode("utf-8"))
        for p in discussion.thread_structure.get_all_posts():
            if p.content:
                bytes_retrieved += len(p.content.encode("utf-8"))

        elapsed = round(time.perf_counter() - start_time, 4)

        return RetrievedDiscussionThread(
            discussion=discussion,
            is_partial=is_partial,
            partial_reasons=partial_reasons,
            total_posts_retrieved=total_posts,
            max_depth_retrieved=max_depth,
            orphans_count=orphans_count,
            bytes_retrieved=bytes_retrieved,
            execution_time_seconds=elapsed,
            metadata={
                **request.metadata,
                "provider_id": provider.provider_id,
                "platform": provider.platform.value,
            },
        )

    def retrieve_batch(
        self,
        params: BatchThreadRetrievalParams,
        is_cancelled: Optional[Callable[[], bool]] = None,
    ) -> BatchThreadRetrievalResult:
        """
        Retrieve a batch of discussion threads enforcing global budgets (max_discussions,
        max_total_bytes, max_provider_operations, timeout).
        """
        start_time = time.perf_counter()
        if is_cancelled is not None and is_cancelled():
            raise CommunityCancelledError(target="batch_retrieval", operation="retrieve_batch")

        eff_limits = params.limits or self._default_limits
        eff_timeout = params.timeout_seconds or eff_limits.max_execution_time_seconds

        retrieved_threads: list[RetrievedDiscussionThread] = []
        errors: list[dict[str, str]] = []
        operations_made = 0
        total_bytes = 0

        # Cap total requests by max_discussions limit
        requests_to_process = params.requests[: eff_limits.max_discussions]

        for req in requests_to_process:
            if is_cancelled is not None and is_cancelled():
                raise CommunityCancelledError(target=req.discussion_id, operation="retrieve_batch")

            # Check timeout budget
            elapsed_so_far = time.perf_counter() - start_time
            if elapsed_so_far > eff_timeout:
                logger.warning(f"Batch thread retrieval timed out ({elapsed_so_far:.2f}s > {eff_timeout}s).")
                errors.append({
                    "discussion_id": req.discussion_id,
                    "error": f"Batch timeout exceeded ({elapsed_so_far:.2f}s > {eff_timeout}s)",
                })
                break

            # Check operations budget
            if operations_made >= eff_limits.max_provider_operations:
                logger.warning(f"Max provider operations limit reached ({operations_made}/{eff_limits.max_provider_operations}).")
                errors.append({
                    "discussion_id": req.discussion_id,
                    "error": "Max provider operations limit reached",
                })
                break

            # Check byte budget
            if total_bytes >= eff_limits.max_total_bytes:
                logger.warning(f"Max total payload bytes limit reached ({total_bytes}/{eff_limits.max_total_bytes}).")
                errors.append({
                    "discussion_id": req.discussion_id,
                    "error": f"Max total bytes limit reached ({total_bytes} bytes)",
                })
                break

            operations_made += 1

            try:
                thread_result = self.retrieve_thread(
                    request=req,
                    limits=eff_limits,
                    is_cancelled=is_cancelled,
                )
                retrieved_threads.append(thread_result)
                total_bytes += thread_result.bytes_retrieved
            except CommunityCancelledError:
                raise
            except CommunityTimeoutError as e:
                logger.warning(f"Timeout retrieving discussion '{req.discussion_id}': {e}")
                errors.append({"discussion_id": req.discussion_id, "error": f"Timeout: {e.message}"})
            except CommunityDiscussionNotFoundError as e:
                logger.warning(f"Discussion not found '{req.discussion_id}': {e}")
                errors.append({"discussion_id": req.discussion_id, "error": f"Not found: {e.message}"})
            except CommunityAuthenticationError as e:
                logger.warning(f"Access denied for discussion '{req.discussion_id}': {e}")
                errors.append({"discussion_id": req.discussion_id, "error": f"Authentication/Access denied: {e.message}"})
            except CommunityProviderError as e:
                logger.warning(f"Provider failure retrieving discussion '{req.discussion_id}': {e}")
                errors.append({"discussion_id": req.discussion_id, "error": f"Provider error: {e.message}"})
            except Exception as e:
                logger.exception(f"Unexpected error retrieving discussion '{req.discussion_id}': {e}")
                errors.append({"discussion_id": req.discussion_id, "error": f"Unexpected error: {str(e)}"})

        total_posts_retrieved = sum(t.total_posts_retrieved for t in retrieved_threads)
        elapsed_total = round(time.perf_counter() - start_time, 4)

        return BatchThreadRetrievalResult(
            threads=retrieved_threads,
            total_threads_retrieved=len(retrieved_threads),
            total_threads_failed=len(errors),
            total_posts_retrieved=total_posts_retrieved,
            total_bytes_retrieved=total_bytes,
            operations_made=operations_made,
            execution_time_seconds=elapsed_total,
            errors=errors,
            metadata={
                "max_discussions_limit": eff_limits.max_discussions,
                "max_comments_limit": eff_limits.max_comments_per_discussion,
                "max_depth_limit": eff_limits.max_reply_depth,
            },
        )

    def expand_discussion_subtree(
        self,
        discussion: Discussion,
        root_comment_id: str,
        max_comments: int = 50,
        max_depth: int = 5,
        timeout_seconds: Optional[float] = None,
        is_cancelled: Optional[Callable[[], bool]] = None,
    ) -> RetrievedDiscussionThread:
        """
        Deeply expands a specific comment subtree of a discussion thread by fetching
        additional descendants and stitching them into the existing thread structure.
        """
        start_time = time.perf_counter()
        provider = self._resolve_provider(platform=discussion.community_context.platform)

        sub_posts = provider.retrieve_comment_subtree(
            discussion_id=discussion.discussion_id,
            root_comment_id=root_comment_id,
            max_comments=max_comments,
            max_depth=max_depth,
            timeout_seconds=timeout_seconds,
            is_cancelled=is_cancelled,
        )

        added_count = 0
        for p in sub_posts:
            if not discussion.thread_structure.has_post(p.post_id):
                discussion.add_post(p)
                added_count += 1

        total_posts = discussion.total_posts()
        depth = discussion.thread_structure.get_thread_depth()
        orphans = discussion.thread_structure.get_orphan_posts()

        bytes_retrieved = 0
        if discussion.title:
            bytes_retrieved += len(discussion.title.encode("utf-8"))
        for p in discussion.thread_structure.get_all_posts():
            if p.content:
                bytes_retrieved += len(p.content.encode("utf-8"))

        elapsed = round(time.perf_counter() - start_time, 4)

        return RetrievedDiscussionThread(
            discussion=discussion,
            is_partial=len(sub_posts) >= max_comments,
            partial_reasons=["subtree_max_comments_reached"] if len(sub_posts) >= max_comments else [],
            total_posts_retrieved=total_posts,
            max_depth_retrieved=depth,
            orphans_count=len(orphans),
            bytes_retrieved=bytes_retrieved,
            execution_time_seconds=elapsed,
            metadata={
                "expanded_root_comment_id": root_comment_id,
                "posts_added": added_count,
                "provider_id": provider.provider_id,
            },
        )

