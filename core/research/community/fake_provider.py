"""
Deterministic Fake Discussion Provider (Phase 1 / Part 6 / Step 2).

Provides hermetic, deterministic in-memory fixtures and fault injection capabilities
for testing CommunityCrawler and discussion retrieval pipelines across platforms.
"""
from __future__ import annotations

import copy
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
    DiscussionSearchParams,
    DiscussionSearchResponse,
)
from core.research.errors import (
    CommunityAuthenticationError,
    CommunityCancelledError,
    CommunityDiscussionNotFoundError,
    CommunityError,
    CommunityProviderError,
    CommunityRateLimitError,
    CommunityResourceLimitError,
    CommunitySecurityError,
    CommunityTimeoutError,
    CommunityValidationError,
    SearchSecurityError,
)
from core.research.search.security import validate_network_target

logger = logging.getLogger("AutonomOS.Research.FakeDiscussionProvider")


class FakeDiscussionProvider(DiscussionProvider):
    """
    In-memory deterministic discussion provider equipped with standard platform fixtures
    and configurable fault injection knobs (timeouts, failures, cancellations, rate limits,
    missing metadata, and partial retrieval).
    """

    def __init__(
        self,
        provider_id: str = "fake-discussion-provider",
        platform: CommunityPlatform = CommunityPlatform.GENERIC,
        name: str = "Fake Discussion Provider",
        limits: Optional[DiscussionFetchLimits] = None,
        populate_default_fixtures: bool = True,
    ):
        super().__init__(
            provider_id=provider_id,
            platform=platform,
            name=name,
            limits=limits or DiscussionFetchLimits(),
        )
        self._discussions: dict[str, Discussion] = {}
        self._communities: dict[str, CommunityContext] = {}

        # Fault injection knobs
        self.simulate_failure: bool = False
        self.simulated_failure_message: str = "Simulated discussion provider network failure"
        self.simulate_timeout: bool = False
        self.simulated_timeout_seconds: float = 30.0
        self.simulate_auth_error: bool = False
        self.simulate_rate_limit: bool = False
        self.simulate_partial_retrieval: bool = False
        self.latency_seconds: float = 0.0

        if populate_default_fixtures:
            self._load_default_fixtures()

    def add_community(self, community: CommunityContext) -> None:
        """Register a community context fixture."""
        self._communities[community.community_id.lower()] = community

    def add_discussion(self, discussion: Discussion) -> None:
        """Register a discussion fixture."""
        self._discussions[discussion.discussion_id] = discussion
        comm_key = discussion.community_context.community_id.lower()
        if comm_key not in self._communities:
            self._communities[comm_key] = discussion.community_context

    def clear(self) -> None:
        """Clear all stored fixtures."""
        self._discussions.clear()
        self._communities.clear()

    def _apply_fault_injections(
        self,
        target: str,
        operation: str,
        timeout_seconds: Optional[float] = None,
        is_cancelled: Optional[Callable[[], bool]] = None,
    ) -> None:
        """Evaluate active fault injection knobs and cancellation predicates."""
        self.check_cancellation(is_cancelled, target, operation)

        if self.simulate_failure:
            raise CommunityProviderError(
                f"Provider '{self.provider_id}' failed executing '{operation}' on '{target}': {self.simulated_failure_message}"
            )

        if self.simulate_auth_error:
            raise CommunityAuthenticationError(
                target=target,
                message=f"Access denied for '{target}' (simulated authentication failure)",
            )

        if self.simulate_rate_limit:
            raise CommunityRateLimitError(
                provider_id=self.provider_id,
                retry_after_seconds=30.0,
            )

        eff_timeout = timeout_seconds or self.limits.timeout_seconds
        if self.simulate_timeout or (self.latency_seconds > eff_timeout):
            raise CommunityTimeoutError(
                target=target,
                operation=operation,
                timeout_seconds=eff_timeout,
            )

        if self.latency_seconds > 0:
            time.sleep(self.latency_seconds)

    def search_discussions(
        self,
        params: DiscussionSearchParams,
        is_cancelled: Optional[Callable[[], bool]] = None,
    ) -> DiscussionSearchResponse:
        """
        Deterministic search filtering over stored in-memory discussion fixtures.
        """
        start_time = time.perf_counter()
        target_desc = f"query:{params.query}"
        if params.community_id:
            target_desc += f" in {params.community_id}"

        self._apply_fault_injections(
            target=target_desc,
            operation="search_discussions",
            timeout_seconds=params.timeout_seconds,
            is_cancelled=is_cancelled,
        )

        query_terms = [t.lower() for t in params.query.split() if t.strip()]
        matched: list[Discussion] = []

        for disc in self._discussions.values():
            self.check_cancellation(is_cancelled, target_desc, "search_discussions")

            # Platform filter
            if params.platform and disc.community_context.platform != params.platform:
                continue

            # Community ID filter
            if params.community_id and disc.community_context.community_id.lower() != params.community_id.lower():
                continue

            # Status filter
            if params.status and disc.status != params.status:
                continue

            # Date constraints
            if params.after_date and disc.created_at < params.after_date:
                continue
            if params.before_date and disc.created_at > params.before_date:
                continue

            # Tag filter
            if params.tags:
                disc_tags_lower = {t.lower() for t in disc.tags}
                if not any(req_tag.lower() in disc_tags_lower for req_tag in params.tags):
                    continue

            # Text content search matching
            title_text = disc.title.lower()
            root_content = disc.root_post.content.lower() if disc.root_post else ""
            comment_texts = " ".join(p.content.lower() for p in disc.thread_structure.get_all_posts())
            tags_text = " ".join(disc.tags).lower()
            combined_corpus = f"{title_text} {tags_text} {root_content} {comment_texts}"

            if not query_terms or any(term in combined_corpus for term in query_terms):
                # Check access restriction
                if disc.community_context.access_status == AccessStatus.PRIVATE:
                    continue
                matched.append(copy.deepcopy(disc))

        # Sort deterministically: score desc if present, then creation date desc, then ID asc
        def sort_key(d: Discussion):
            sc = d.engagement.score if d.engagement and d.engagement.score is not None else 0
            return (-sc, d.created_at, d.discussion_id)

        matched.sort(key=sort_key)

        limit = min(params.limit, self.limits.max_search_results)
        final_results = matched[:limit]
        elapsed = round(time.perf_counter() - start_time, 4)

        return DiscussionSearchResponse(
            query=params.query,
            results=final_results,
            total_found=len(matched),
            execution_time_seconds=elapsed,
            provider=self.provider_id,
            platform=self.platform,
        )

    def get_discussion(
        self,
        params: DiscussionRetrievalParams,
        is_cancelled: Optional[Callable[[], bool]] = None,
    ) -> Discussion:
        """
        Retrieve a discussion thread by ID with bounded comment hierarchy.
        """
        self._apply_fault_injections(
            target=params.discussion_id,
            operation="get_discussion",
            timeout_seconds=params.timeout_seconds,
            is_cancelled=is_cancelled,
        )

        if "://" in params.discussion_id:
            try:
                validate_network_target(params.discussion_id)
            except SearchSecurityError as e:
                raise CommunitySecurityError(params.discussion_id, f"SSRF security violation: {str(e)}")

        disc = self._discussions.get(params.discussion_id)
        if not disc:
            # Also try matching by permalink / URL
            for d in self._discussions.values():
                if d.url == params.discussion_id:
                    disc = d
                    break

        if not disc:
            raise CommunityDiscussionNotFoundError(
                discussion_id=params.discussion_id,
                message=f"Discussion '{params.discussion_id}' not found in provider '{self.provider_id}'",
            )

        # Access check
        if disc.community_context.access_status == AccessStatus.PRIVATE:
            raise CommunityAuthenticationError(
                target=params.discussion_id,
                message=f"Discussion '{params.discussion_id}' is in a private community without authorization.",
            )

        result_disc = copy.deepcopy(disc)

        if not params.include_comments:
            # Strip comments and retain only root post
            root = result_disc.root_post
            result_disc.thread_structure = ThreadStructure()
            if root:
                result_disc.thread_structure.add_post(root)
            return result_disc

        # Apply bounds to thread structure
        total_available = result_disc.thread_structure.total_posts()
        max_posts = (params.max_comments + 1) if params.max_comments is not None else self.limits.max_posts_per_discussion
        max_depth = params.max_depth or self.limits.max_depth

        flattened = result_disc.thread_structure.flatten_deterministic(
            ordering=ThreadOrdering.CHRONOLOGICAL,
            max_depth=max_depth,
            max_posts=max_posts,
        )

        bounded_tree = ThreadStructure()
        for p in flattened:
            bounded_tree.add_post(p)

        # Mark explicitly partial if posts were truncated by bounds
        if len(flattened) < total_available:
            result_disc.metadata["is_partial"] = True
            result_disc.metadata["partial_reason"] = f"bounded_thread_retrieval:{len(flattened)}<{total_available}"

        # Fault injection: partial retrieval simulation
        if self.simulate_partial_retrieval:
            result_disc.metadata["is_partial"] = True
            result_disc.metadata["partial_reason"] = "Simulated network interruption during reply tree retrieval"

        result_disc.thread_structure = bounded_tree
        return result_disc

    def get_comments(
        self,
        params: DiscussionCommentsParams,
        is_cancelled: Optional[Callable[[], bool]] = None,
    ) -> list[DiscussionPost]:
        """
        Retrieve bounded comments/replies of a discussion.
        """
        self._apply_fault_injections(
            target=params.discussion_id,
            operation="get_comments",
            timeout_seconds=params.timeout_seconds,
            is_cancelled=is_cancelled,
        )

        disc = self._discussions.get(params.discussion_id)
        if not disc:
            raise CommunityDiscussionNotFoundError(discussion_id=params.discussion_id)

        max_comments = params.max_comments or self.limits.max_posts_per_discussion
        max_depth = params.max_depth or self.limits.max_depth

        if params.parent_id:
            # Subtree replies under specific parent
            children = disc.thread_structure.get_replies(parent_id=params.parent_id)
            if max_depth is not None:
                children = [c for c in children if c.depth <= max_depth]
            return [copy.deepcopy(c) for c in children[:max_comments]]

        flattened = disc.thread_structure.flatten_deterministic(
            ordering=params.ordering,
            max_depth=max_depth,
            max_posts=max_comments,
        )
        # Exclude root post from comments query
        replies = [p for p in flattened if not p.is_root and p.post_id != disc.thread_structure.root_post_id]
        return [copy.deepcopy(r) for r in replies]

    def get_community_metadata(
        self,
        community_id: str,
        platform: Optional[CommunityPlatform] = None,
        timeout_seconds: Optional[float] = None,
        is_cancelled: Optional[Callable[[], bool]] = None,
    ) -> CommunityContext:
        """
        Retrieve community metadata by community ID.
        """
        self._apply_fault_injections(
            target=community_id,
            operation="get_community_metadata",
            timeout_seconds=timeout_seconds,
            is_cancelled=is_cancelled,
        )

        if "://" in community_id:
            try:
                validate_network_target(community_id)
            except SearchSecurityError as e:
                raise CommunitySecurityError(community_id, f"SSRF security violation: {str(e)}")

        key = community_id.lower()
        if key in self._communities:
            ctx = self._communities[key]
            if ctx.access_status == AccessStatus.PRIVATE:
                raise CommunityAuthenticationError(
                    target=community_id,
                    message=f"Community '{community_id}' is private.",
                )
            return copy.deepcopy(ctx)

        # Synthesize fallback generic public context if not pre-registered
        return CommunityContext(
            platform=platform or self.platform,
            community_id=community_id,
            community_name=community_id,
            source_url=f"https://community.autonomos.org/{community_id}",
            access_status=AccessStatus.PUBLIC,
        )

    # -------------------------------------------------------------------------
    # Realistic Fixture Initialization
    # -------------------------------------------------------------------------

    def _load_default_fixtures(self) -> None:
        """Populate rich, multi-platform discussion fixtures."""

        # 1. Reddit Python Community
        reddit_py_ctx = CommunityContext(
            platform=CommunityPlatform.REDDIT,
            community_id="r/Python",
            community_name="Python Community",
            source_url="https://www.reddit.com/r/Python",
            category="Programming",
            access_status=AccessStatus.PUBLIC,
        )
        self.add_community(reddit_py_ctx)

        disc_py_101 = Discussion(
            discussion_id="reddit-py-101",
            community_context=reddit_py_ctx,
            title="How to structure a large async Python application with TaskGroup?",
            url="https://www.reddit.com/r/Python/comments/reddit-py-101",
            author_id="async_architect",
            created_at="2026-08-20T10:00:00Z",
            status=DiscussionStatus.OPEN,
            tags=["asyncio", "architecture", "python-3.11"],
            engagement=EngagementMetrics(score=145, upvotes=160, downvotes=15, reply_count=5),
        )
        root_py_101 = DiscussionPost(
            post_id="post-root-101",
            discussion_id="reddit-py-101",
            content="We are migrating our backend services to Python 3.12 and want to use asyncio.TaskGroup. What are the common patterns for graceful cancellation and dependency injection?",
            author_id="async_architect",
            created_at="2026-08-20T10:00:00Z",
            is_root=True,
            engagement=EngagementMetrics(score=145),
        )
        disc_py_101.add_post(root_py_101)

        reply_101_1 = DiscussionPost(
            post_id="post-rep-101-1",
            discussion_id="reddit-py-101",
            content="Always ensure cleanup in try/finally blocks inside individual tasks before TaskGroup exits.",
            parent_id="post-root-101",
            author_id="core_coder",
            created_at="2026-08-20T10:15:00Z",
            engagement=EngagementMetrics(score=48),
        )
        disc_py_101.add_post(reply_101_1)

        nested_101_1_1 = DiscussionPost(
            post_id="post-nest-101-1",
            discussion_id="reddit-py-101",
            content="Also beware of asyncio.shield() behaving differently with ExceptionGroups in 3.11+.",
            parent_id="post-rep-101-1",
            author_id="trio_fan",
            created_at="2026-08-20T10:30:00Z",
            engagement=EngagementMetrics(score=22),
        )
        disc_py_101.add_post(nested_101_1_1)

        reply_101_2 = DiscussionPost(
            post_id="post-rep-101-2",
            discussion_id="reddit-py-101",
            content="Use structural concurrency libraries or dependency injectors that support async context managers.",
            parent_id="post-root-101",
            author_id="infra_dev",
            created_at="2026-08-20T10:45:00Z",
            engagement=EngagementMetrics(score=19),
        )
        disc_py_101.add_post(reply_101_2)
        self.add_discussion(disc_py_101)

        # 2. GitHub Discussions (facebook/react)
        gh_react_ctx = CommunityContext(
            platform=CommunityPlatform.GITHUB_DISCUSSIONS,
            community_id="facebook/react",
            community_name="React Discussions",
            source_url="https://github.com/facebook/react/discussions",
            repository_association="facebook/react",
            category="Q&A",
            access_status=AccessStatus.PUBLIC,
        )
        self.add_community(gh_react_ctx)

        disc_react_201 = Discussion(
            discussion_id="gh-react-201",
            community_context=gh_react_ctx,
            title="Optimizing Server Components serialization boundaries",
            url="https://github.com/facebook/react/discussions/201",
            author_id="frontend_ninja",
            created_at="2026-08-25T14:00:00Z",
            status=DiscussionStatus.RESOLVED,
            tags=["rsc", "performance", "react19"],
            engagement=EngagementMetrics(score=92, views_count=12000, is_accepted_answer=True),
        )
        root_react_201 = DiscussionPost(
            post_id="post-react-root",
            discussion_id="gh-react-201",
            content="How do we prevent large JSON payloads when passing props from Server Components to Client Components?",
            author_id="frontend_ninja",
            created_at="2026-08-25T14:00:00Z",
            is_root=True,
            engagement=EngagementMetrics(score=92),
        )
        disc_react_201.add_post(root_react_201)

        ans_react_201 = DiscussionPost(
            post_id="post-react-ans",
            discussion_id="gh-react-201",
            content="Extract only the necessary leaf properties instead of spreading the whole server entity across the client boundary.",
            parent_id="post-react-root",
            author_id="react_maintainer",
            created_at="2026-08-25T14:30:00Z",
            engagement=EngagementMetrics(score=135, is_accepted_answer=True),
        )
        disc_react_201.add_post(ans_react_201)
        self.add_discussion(disc_react_201)

        # 3. Stack Overflow (Stack Exchange)
        so_ctx = CommunityContext(
            platform=CommunityPlatform.STACK_EXCHANGE,
            community_id="stackoverflow",
            community_name="Stack Overflow",
            source_url="https://stackoverflow.com/questions",
            category="python",
            access_status=AccessStatus.PUBLIC,
        )
        self.add_community(so_ctx)

        disc_so_301 = Discussion(
            discussion_id="so-python-301",
            community_context=so_ctx,
            title="What is the difference between asyncio.gather and TaskGroup in Python 3.11?",
            url="https://stackoverflow.com/questions/so-python-301",
            author_id="curious_learner",
            created_at="2026-08-28T09:00:00Z",
            status=DiscussionStatus.RESOLVED,
            tags=["python", "asyncio", "python-3.11", "exception-handling"],
            engagement=EngagementMetrics(score=280, views_count=85000, is_accepted_answer=True),
        )
        root_so_301 = DiscussionPost(
            post_id="post-so-root",
            discussion_id="so-python-301",
            content="When should I prefer asyncio.TaskGroup over asyncio.gather when running concurrent coroutines?",
            author_id="curious_learner",
            created_at="2026-08-28T09:00:00Z",
            is_root=True,
            engagement=EngagementMetrics(score=280),
        )
        disc_so_301.add_post(root_so_301)

        ans_so_301 = DiscussionPost(
            post_id="post-so-ans",
            discussion_id="so-python-301",
            content="TaskGroup guarantees that all spawned tasks complete or are cancelled if any task fails, raising an ExceptionGroup. gather() does not cancel sibling tasks by default.",
            parent_id="post-so-root",
            author_id="python_core_dev",
            created_at="2026-08-28T09:40:00Z",
            engagement=EngagementMetrics(score=340, is_accepted_answer=True),
        )
        disc_so_301.add_post(ans_so_301)
        self.add_discussion(disc_so_301)

        # 4. Discussion with Missing Metadata & Deleted Comments
        disc_missing_meta = Discussion(
            discussion_id="disc-incomplete-401",
            community_context=reddit_py_ctx,
            title="General questions about async frameworks",
            url="https://www.reddit.com/r/Python/comments/disc-incomplete-401",
            author_id=None,  # Missing author
            created_at="2026-08-29T11:00:00Z",
            status=DiscussionStatus.UNKNOWN,
            tags=[],  # Missing tags
            engagement=EngagementMetrics(),  # Missing engagement counts
        )
        root_incomplete = DiscussionPost(
            post_id="post-inc-root",
            discussion_id="disc-incomplete-401",
            content="Any thoughts on modern async web frameworks?",
            author_id=None,
            created_at="2026-08-29T11:00:00Z",
            is_root=True,
        )
        disc_missing_meta.add_post(root_incomplete)

        deleted_comment = DiscussionPost(
            post_id="post-inc-deleted",
            discussion_id="disc-incomplete-401",
            content="[deleted by user]",
            parent_id="post-inc-root",
            author_id=None,
            created_at="2026-08-29T11:20:00Z",
            metadata={"is_deleted": True},
        )
        disc_missing_meta.add_post(deleted_comment)
        self.add_discussion(disc_missing_meta)

        # 5. Private / Restricted Community Fixture
        private_ctx = CommunityContext(
            platform=CommunityPlatform.REDDIT,
            community_id="r/internal_confidential",
            community_name="Internal Confidential",
            source_url="https://www.reddit.com/r/internal_confidential",
            access_status=AccessStatus.PRIVATE,
        )
        self.add_community(private_ctx)

        disc_private = Discussion(
            discussion_id="disc-priv-501",
            community_context=private_ctx,
            title="Private internal discussion",
            url="https://www.reddit.com/r/internal_confidential/501",
            author_id="admin",
        )
        root_priv = DiscussionPost(
            post_id="post-priv-root",
            discussion_id="disc-priv-501",
            content="Confidential notes.",
            author_id="admin",
            is_root=True,
        )
        disc_private.add_post(root_priv)
        self.add_discussion(disc_private)
