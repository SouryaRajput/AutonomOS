"""
Community Discussion Discovery Engine (Phase 1 / Part 6 / Step 3).

Discovers, normalizes, deduplicates, and deterministically ranks candidate discussion threads
across multiple community platforms (Reddit, GitHub Discussions, Stack Exchange, forums)
based on research queries, tags, repository associations, and source constraints.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import logging
import re
import time
from typing import Any, Callable, Optional
import urllib.parse
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
    compute_sha256,
    sanitize_author_identifier,
    utc_now,
)
from core.research.community.provider import (
    DiscussionFetchLimits,
    DiscussionProvider,
    DiscussionSearchParams,
    DiscussionSearchResponse,
)
from core.research.errors import (
    CommunityCancelledError,
    CommunityError,
    CommunityProviderError,
    CommunityResourceLimitError,
    CommunityTimeoutError,
    CommunityValidationError,
)
from core.research.search.normalization import normalize_url

logger = logging.getLogger("AutonomOS.Research.CommunityDiscovery")


def parse_iso_timestamp(ts: str) -> float:
    """Safely convert ISO 8601 timestamp string to Unix timestamp for sorting."""
    if not ts or not isinstance(ts, str):
        return 0.0
    try:
        from datetime import datetime
        cleaned = ts.replace("Z", "+00:00")
        return datetime.fromisoformat(cleaned).timestamp()
    except Exception:
        return 0.0


# -----------------------------------------------------------------------------
# Normalization Utilities
# -----------------------------------------------------------------------------

def normalize_discussion_url(url: str) -> str:
    """
    Safely normalize a discussion permalink URL for deduplication.
    Strips tracking query parameters and trailing slashes.
    """
    if not url or not isinstance(url, str):
        return ""
    return normalize_url(url.strip(), strip_tracking=True)


def normalize_discussion_id(raw_id: str, platform: Optional[CommunityPlatform] = None) -> str:
    """
    Normalize discussion thread identifier.
    Removes leading hashtags, URL prefixes, and trailing query parameters.
    """
    if not raw_id or not isinstance(raw_id, str):
        return ""
    cleaned = raw_id.strip()
    # Strip URL prefix if a full URL was passed as ID
    if "://" in cleaned:
        try:
            parsed = urllib.parse.urlsplit(cleaned)
            cleaned = parsed.path.rstrip("/").split("/")[-1]
        except Exception:
            pass
    # Strip leading '#' or 'id:'
    cleaned = re.sub(r"^(#|id:)", "", cleaned, flags=re.IGNORECASE).strip()
    return cleaned


def normalize_community_id(raw_id: str, platform: Optional[CommunityPlatform] = None) -> str:
    """
    Normalize community identifier (e.g., 'r/Python' -> 'r/python', 'facebook/react' -> 'facebook/react').
    """
    if not raw_id or not isinstance(raw_id, str):
        return ""
    cleaned = raw_id.strip().lower()
    return cleaned


# -----------------------------------------------------------------------------
# Candidate Domain Models
# -----------------------------------------------------------------------------

@dataclass
class DiscoveredDiscussionCandidate:
    """
    Deterministic candidate discussion discovered from a community source.
    Carries structured source metadata, objective match scoring, and evidence lineage.
    """
    candidate_id: str
    discussion_id: str
    platform: CommunityPlatform
    community_id: str
    community_name: str
    title: str
    url: str
    author_id: Optional[str] = None
    created_at: str = ""
    status: DiscussionStatus = DiscussionStatus.OPEN
    tags: list[str] = field(default_factory=list)
    engagement: EngagementMetrics = field(default_factory=EngagementMetrics)
    repository_association: Optional[str] = None
    match_score: float = 0.0
    match_reasons: list[str] = field(default_factory=list)
    content_snippet: str = ""
    content_checksum: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.candidate_id:
            self.candidate_id = f"cand-{uuid.uuid4().hex[:8]}"

        if isinstance(self.platform, str):
            self.platform = CommunityPlatform.from_string(self.platform)

        if isinstance(self.status, str):
            self.status = DiscussionStatus.from_string(self.status)

        if not self.content_checksum:
            self.content_checksum = compute_sha256(f"{self.title}:{self.content_snippet}")

        self.author_id = sanitize_author_identifier(self.author_id)

    def to_dict(self) -> dict[str, Any]:
        return {
            "candidate_id": self.candidate_id,
            "discussion_id": self.discussion_id,
            "platform": self.platform.value if isinstance(self.platform, CommunityPlatform) else str(self.platform),
            "community_id": self.community_id,
            "community_name": self.community_name,
            "title": self.title,
            "url": self.url,
            "author_id": self.author_id,
            "created_at": self.created_at,
            "status": self.status.value if isinstance(self.status, DiscussionStatus) else str(self.status),
            "tags": list(self.tags),
            "engagement": self.engagement.to_dict() if self.engagement else None,
            "repository_association": self.repository_association,
            "match_score": self.match_score,
            "match_reasons": list(self.match_reasons),
            "content_snippet": self.content_snippet,
            "content_checksum": self.content_checksum,
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> DiscoveredDiscussionCandidate:
        eng_data = data.get("engagement")
        engagement = EngagementMetrics.from_dict(eng_data) if isinstance(eng_data, dict) else EngagementMetrics()

        return cls(
            candidate_id=data.get("candidate_id", ""),
            discussion_id=data.get("discussion_id", ""),
            platform=CommunityPlatform.from_string(data.get("platform", "generic")),
            community_id=data.get("community_id", ""),
            community_name=data.get("community_name", ""),
            title=data.get("title", ""),
            url=data.get("url", ""),
            author_id=data.get("author_id"),
            created_at=data.get("created_at", ""),
            status=DiscussionStatus.from_string(data.get("status", "open")),
            tags=list(data.get("tags", [])),
            engagement=engagement,
            repository_association=data.get("repository_association"),
            match_score=float(data.get("match_score", 0.0)),
            match_reasons=list(data.get("match_reasons", [])),
            content_snippet=data.get("content_snippet", ""),
            content_checksum=data.get("content_checksum", ""),
            metadata=dict(data.get("metadata", {})),
        )


@dataclass(frozen=True)
class DiscussionDiscoveryParams:
    """
    Search and constraint parameters for community discussion discovery.
    """
    query: str
    platforms: list[CommunityPlatform] = field(default_factory=list)
    communities: list[str] = field(default_factory=list)
    repository_association: Optional[str] = None
    tags: list[str] = field(default_factory=list)
    status: Optional[DiscussionStatus] = None
    after_date: Optional[str] = None
    before_date: Optional[str] = None
    max_discussions: int = 20
    max_requests: int = 10
    timeout_seconds: Optional[float] = 30.0
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.query or not isinstance(self.query, str) or not self.query.strip():
            raise CommunityValidationError("query", "Discovery query cannot be empty.")
        if self.max_discussions <= 0:
            raise CommunityValidationError("max_discussions", "max_discussions must be > 0.")
        if self.max_requests <= 0:
            raise CommunityValidationError("max_requests", "max_requests must be > 0.")
        if self.timeout_seconds is not None and self.timeout_seconds <= 0:
            raise CommunityValidationError("timeout_seconds", "timeout_seconds must be > 0.")


@dataclass
class DiscussionDiscoveryResult:
    """
    Aggregated response from a community discussion discovery run.
    """
    query: str
    candidates: list[DiscoveredDiscussionCandidate] = field(default_factory=list)
    total_discovered: int = 0
    total_evaluated: int = 0
    requests_made: int = 0
    execution_time_seconds: float = 0.0
    providers_queried: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "query": self.query,
            "candidates": [c.to_dict() for c in self.candidates],
            "total_discovered": self.total_discovered,
            "total_evaluated": self.total_evaluated,
            "requests_made": self.requests_made,
            "execution_time_seconds": self.execution_time_seconds,
            "providers_queried": list(self.providers_queried),
            "errors": list(self.errors),
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> DiscussionDiscoveryResult:
        candidates = [DiscoveredDiscussionCandidate.from_dict(c) for c in data.get("candidates", []) if isinstance(c, dict)]
        return cls(
            query=data.get("query", ""),
            candidates=candidates,
            total_discovered=int(data.get("total_discovered", len(candidates))),
            total_evaluated=int(data.get("total_evaluated", len(candidates))),
            requests_made=int(data.get("requests_made", 0)),
            execution_time_seconds=float(data.get("execution_time_seconds", 0.0)),
            providers_queried=list(data.get("providers_queried", [])),
            errors=list(data.get("errors", [])),
            metadata=dict(data.get("metadata", {})),
        )


# -----------------------------------------------------------------------------
# Deterministic Scorer
# -----------------------------------------------------------------------------

class CommunityDiscussionScorer:
    """
    Deterministic lexical and metadata relevance scorer for discovered discussions.
    
    Strict constraints:
    - Purely lexical, structural, and objective metadata matching.
    - Zero subjective sentiment scoring.
    - Zero consensus / opinion calculations.
    - Zero author credibility judgments.
    """

    @staticmethod
    def score_candidate(
        discussion: Discussion,
        params: DiscussionDiscoveryParams,
    ) -> tuple[float, list[str]]:
        """
        Calculate deterministic match score and explanatory reasons.
        Returns: (score between 0.0 and 1.0, list of matching reason strings).
        """
        score = 0.0
        reasons: list[str] = []

        query_lower = params.query.lower().strip()
        query_terms = [t for t in re.split(r"\s+", query_lower) if t]

        title_lower = discussion.title.lower()
        root_content_lower = discussion.root_post.content.lower() if discussion.root_post else ""

        # 1. Exact phrase match in title
        if query_lower in title_lower:
            score += 0.40
            reasons.append("exact_title_match")
        else:
            # Token matches in title
            title_hits = sum(1 for term in query_terms if term in title_lower)
            if title_hits > 0 and len(query_terms) > 0:
                fraction = title_hits / len(query_terms)
                token_score = round(0.30 * fraction, 4)
                score += token_score
                reasons.append(f"title_token_match:{title_hits}/{len(query_terms)}")

        # 2. Tag matching
        disc_tags_lower = {t.lower() for t in discussion.tags}
        if params.tags:
            tag_hits = [t for t in params.tags if t.lower() in disc_tags_lower]
            if tag_hits:
                score += 0.20
                reasons.append(f"explicit_tag_match:{','.join(tag_hits)}")
        else:
            # Check if query terms appear as tags
            query_tag_hits = [term for term in query_terms if term in disc_tags_lower]
            if query_tag_hits:
                score += 0.15
                reasons.append(f"query_tag_match:{','.join(query_tag_hits)}")

        # 3. Repository association match
        if params.repository_association and discussion.community_context.repository_association:
            if params.repository_association.lower() == discussion.community_context.repository_association.lower():
                score += 0.25
                reasons.append(f"repo_association_match:{discussion.community_context.repository_association}")

        # 4. Community match
        if params.communities:
            req_comms_lower = {c.lower() for c in params.communities}
            if discussion.community_context.community_id.lower() in req_comms_lower:
                score += 0.15
                reasons.append(f"community_match:{discussion.community_context.community_id}")

        # 5. Content body matches
        content_hits = sum(1 for term in query_terms if term in root_content_lower)
        if content_hits > 0 and len(query_terms) > 0:
            fraction = content_hits / len(query_terms)
            content_score = round(0.10 * fraction, 4)
            score += content_score
            reasons.append(f"content_body_match:{content_hits}/{len(query_terms)}")

        # 6. Objective engagement boosts (strictly factual indicators)
        if discussion.engagement:
            if discussion.engagement.is_accepted_answer:
                score += 0.05
                reasons.append("accepted_answer")
            if discussion.engagement.score and discussion.engagement.score >= 50:
                score += 0.05
                reasons.append("high_engagement_score")

        # Clamp score between 0.0 and 1.0
        final_score = round(min(1.0, max(0.0, score)), 4)
        return final_score, reasons


# -----------------------------------------------------------------------------
# Community Discussion Discovery Engine
# -----------------------------------------------------------------------------

class CommunityDiscoveryEngine:
    """
    Coordinates multi-provider community discussion discovery, normalization,
    deduplication, deterministic ranking, and bounded candidate selection.
    """

    def __init__(
        self,
        providers: Optional[list[DiscussionProvider]] = None,
        default_limits: Optional[DiscussionFetchLimits] = None,
    ):
        self._providers: list[DiscussionProvider] = list(providers) if providers else []
        self._default_limits = default_limits or DiscussionFetchLimits()

        # Fallback default fake provider if no provider is configured
        if not self._providers:
            self._providers.append(FakeDiscussionProvider(limits=self._default_limits))

    def register_provider(self, provider: DiscussionProvider) -> None:
        """Register a new discussion provider."""
        if not isinstance(provider, DiscussionProvider):
            raise CommunityValidationError("provider", "Must be an instance of DiscussionProvider.")
        self._providers.append(provider)

    def get_providers(self) -> list[DiscussionProvider]:
        """Return list of registered discussion providers."""
        return list(self._providers)

    def discover(
        self,
        params: DiscussionDiscoveryParams,
        is_cancelled: Optional[Callable[[], bool]] = None,
    ) -> DiscussionDiscoveryResult:
        """
        Execute deterministic community discussion discovery across registered providers.
        """
        start_time = time.perf_counter()
        if is_cancelled is not None and is_cancelled():
            raise CommunityCancelledError(target=params.query, operation="discover")

        providers_queried: list[str] = []
        errors: list[str] = []
        raw_discussions: list[Discussion] = []
        requests_made = 0

        # Filter providers by requested platforms if specified
        target_providers = self._providers
        if params.platforms:
            target_providers = [
                p for p in self._providers
                if p.platform in params.platforms or p.platform == CommunityPlatform.GENERIC
            ]

        # Execute search queries across target providers within request limits
        for provider in target_providers:
            if is_cancelled is not None and is_cancelled():
                raise CommunityCancelledError(target=params.query, operation="discover")

            if requests_made >= params.max_requests:
                logger.info(f"Max requests budget reached ({requests_made}/{params.max_requests}).")
                break

            providers_queried.append(provider.provider_id)
            requests_made += 1

            # Prepare search parameters for this provider
            # If multiple communities are requested, iterate or query generally
            comm_target = params.communities[0] if len(params.communities) == 1 else None
            search_params = DiscussionSearchParams(
                query=params.query,
                platform=params.platforms[0] if len(params.platforms) == 1 else None,
                community_id=comm_target,
                tags=params.tags,
                status=params.status,
                after_date=params.after_date,
                before_date=params.before_date,
                limit=params.max_discussions,
                timeout_seconds=params.timeout_seconds,
            )

            try:
                response: DiscussionSearchResponse = provider.search_discussions(
                    params=search_params,
                    is_cancelled=is_cancelled,
                )
                raw_discussions.extend(response.results)
            except CommunityCancelledError:
                raise
            except CommunityTimeoutError as e:
                logger.warning(f"Provider '{provider.provider_id}' timed out during discovery: {e}")
                errors.append(f"Timeout on provider '{provider.provider_id}': {e.message}")
            except CommunityProviderError as e:
                logger.warning(f"Provider '{provider.provider_id}' failed during discovery: {e}")
                errors.append(f"Failure on provider '{provider.provider_id}': {e.message}")
            except Exception as e:
                logger.exception(f"Unexpected error on provider '{provider.provider_id}': {e}")
                errors.append(f"Unexpected error on provider '{provider.provider_id}': {str(e)}")

        total_evaluated = len(raw_discussions)

        # ---------------------------------------------------------------------
        # Normalization & Deduplication
        # ---------------------------------------------------------------------
        seen_keys: set[str] = set()
        deduped_candidates: list[DiscoveredDiscussionCandidate] = []

        for disc in raw_discussions:
            norm_url = normalize_discussion_url(disc.url)
            norm_disc_id = normalize_discussion_id(disc.discussion_id, disc.community_context.platform)
            norm_comm_id = normalize_community_id(disc.community_context.community_id, disc.community_context.platform)

            # Deduplication key composite
            primary_key = f"{disc.community_context.platform.value}:{norm_comm_id}:{norm_disc_id}"
            url_key = f"url:{norm_url}" if norm_url else ""

            if primary_key in seen_keys or (url_key and url_key in seen_keys):
                continue

            seen_keys.add(primary_key)
            if url_key:
                seen_keys.add(url_key)

            # Apply scope filtering: repository association constraint
            if params.repository_association and disc.community_context.repository_association:
                if disc.community_context.repository_association.lower() != params.repository_association.lower():
                    continue

            # Apply scope filtering: community constraint if multiple were specified
            if params.communities:
                req_comms_lower = {c.lower() for c in params.communities}
                if norm_comm_id not in req_comms_lower:
                    continue

            # Calculate deterministic match score
            match_score, match_reasons = CommunityDiscussionScorer.score_candidate(disc, params)

            root_snippet = disc.root_post.content[:2000] if disc.root_post else ""
            candidate = DiscoveredDiscussionCandidate(
                candidate_id=f"cand-{disc.discussion_id}",
                discussion_id=disc.discussion_id,
                platform=disc.community_context.platform,
                community_id=disc.community_context.community_id,
                community_name=disc.community_context.community_name,
                title=disc.title,
                url=norm_url or disc.url,
                author_id=disc.author_id,
                created_at=disc.created_at,
                status=disc.status,
                tags=list(disc.tags),
                engagement=disc.engagement,
                repository_association=disc.community_context.repository_association,
                match_score=match_score,
                match_reasons=match_reasons,
                content_snippet=root_snippet,
                content_checksum=disc.root_post.content_checksum if disc.root_post else compute_sha256(disc.title),
                metadata=dict(disc.metadata),
            )
            deduped_candidates.append(candidate)

        # ---------------------------------------------------------------------
        # Deterministic Ranking & Bounding
        # ---------------------------------------------------------------------
        # Sort key: match_score desc, engagement.score desc, created_at desc, candidate_id asc
        def candidate_sort_key(c: DiscoveredDiscussionCandidate):
            eng_score = c.engagement.score if c.engagement and c.engagement.score is not None else 0
            ts = parse_iso_timestamp(c.created_at)
            return (
                -c.match_score,
                -eng_score,
                -ts,
                c.candidate_id,
            )

        deduped_candidates.sort(key=candidate_sort_key)

        final_candidates = deduped_candidates[: params.max_discussions]
        elapsed = round(time.perf_counter() - start_time, 4)

        return DiscussionDiscoveryResult(
            query=params.query,
            candidates=final_candidates,
            total_discovered=len(final_candidates),
            total_evaluated=total_evaluated,
            requests_made=requests_made,
            execution_time_seconds=elapsed,
            providers_queried=providers_queried,
            errors=errors,
            metadata={
                "max_discussions_limit": params.max_discussions,
                "max_requests_limit": params.max_requests,
                "deduped_count": total_evaluated - len(deduped_candidates),
            },
        )
