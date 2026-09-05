"""
Deterministic Discussion Relevance and Selection Engine (Phase 1 / Part 6 / Step 6).

Implements deterministic, budget-aware candidate selection, post/comment scoring,
and hierarchy-preserving thread pruning for community and discussion sources.
Guarantees strict reproducibility:
same input + same provider data + same task constraints = same selected material.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
import logging
import re
import time
from typing import Any, Callable, Optional
import urllib.parse
import uuid

from core.research.community.discovery import (
    CommunityDiscussionScorer,
    CommunityDiscoveryEngine,
    DiscoveredDiscussionCandidate,
    DiscussionDiscoveryParams,
    DiscussionDiscoveryResult,
    normalize_community_id,
    normalize_discussion_id,
    normalize_discussion_url,
    parse_iso_timestamp,
)
from core.research.community.extractor import (
    DiscussionCodeBlock,
    DiscussionContentExtractor,
    DiscussionLink,
    DiscussionQuote,
    StructuredDiscussion,
    StructuredDiscussionPost,
)
from core.research.community.fake_provider import FakeDiscussionProvider
from core.research.community.models import (
    AccessStatus,
    CommunityContext,
    CommunityPlatform,
    Discussion,
    DiscussionPost,
    DiscussionSourceMaterial,
    DiscussionStatus,
    EngagementMetrics,
    ThreadOrdering,
    ThreadStructure,
    compute_sha256,
    sanitize_author_identifier,
    utc_now,
)
from core.research.community.provider import (
    DiscussionFetchLimits,
    DiscussionProvider,
    DiscussionRetrievalParams,
    DiscussionSearchParams,
    DiscussionSearchResponse,
)
from core.research.community.retriever import (
    BatchThreadRetrievalParams,
    BatchThreadRetrievalResult,
    DiscussionRetrievalLimits,
    DiscussionThreadRetriever,
    RetrievedDiscussionThread,
    ThreadRetrievalRequest,
)
from core.research.contracts.crawler_report import CrawlerReport, RawSourceReference
from core.research.contracts.crawler_task import CrawlerTask
from core.research.contracts.evidence import EvidenceItem, EvidenceProvenance
from core.research.errors import (
    CommunityAuthenticationError,
    CommunityCancelledError,
    CommunityDiscussionNotFoundError,
    CommunityError,
    CommunityProviderError,
    CommunityResourceLimitError,
    CommunityTimeoutError,
    CommunityValidationError,
)
from core.research.types import (
    CrawlerReportStatus,
    FactClassification,
    ResearchConfidence,
    SourceType,
)

logger = logging.getLogger("AutonomOS.Research.DiscussionSelection")

STOP_WORDS = {
    "a", "an", "the", "and", "or", "in", "on", "at", "to", "for", "with",
    "of", "by", "from", "is", "are", "was", "were", "it", "this", "that",
    "as", "be", "into", "all", "some",
}


# -----------------------------------------------------------------------------
# Query & Selection Specification Models
# -----------------------------------------------------------------------------

@dataclass(frozen=True)
class DiscussionTopicQuery:
    """
    Structured query and constraint specification for targeted discussion selection.
    """
    raw_topic: str
    keywords: list[str] = field(default_factory=list)
    target_platform: Optional[CommunityPlatform] = None
    target_community: Optional[str] = None
    repository_association: Optional[str] = None
    after_date: Optional[str] = None
    before_date: Optional[str] = None
    tags: list[str] = field(default_factory=list)
    min_score: float = 0.2
    include_ancestors: bool = True
    include_replies: bool = False
    max_reply_depth_from_match: int = 1

    @classmethod
    def from_input(
        cls,
        topic: str = "",
        keywords: Optional[list[str]] = None,
        target_platform: Optional[str | CommunityPlatform] = None,
        target_community: Optional[str] = None,
        repository_association: Optional[str] = None,
        after_date: Optional[str] = None,
        before_date: Optional[str] = None,
        tags: Optional[list[str]] = None,
        min_score: float = 0.2,
        include_ancestors: bool = True,
        include_replies: bool = False,
        max_reply_depth_from_match: int = 1,
    ) -> DiscussionTopicQuery:
        kws = [k.strip().lower() for k in (keywords or []) if k and isinstance(k, str) and k.strip()]
        tgs = [t.strip().lower() for t in (tags or []) if t and isinstance(t, str) and t.strip()]
        plat = CommunityPlatform.from_string(target_platform) if target_platform else None
        comm = normalize_community_id(target_community) if target_community else None

        return cls(
            raw_topic=(topic or "").strip(),
            keywords=kws,
            target_platform=plat,
            target_community=comm,
            repository_association=(repository_association or "").strip() if repository_association else None,
            after_date=(after_date or "").strip() if after_date else None,
            before_date=(before_date or "").strip() if before_date else None,
            tags=tgs,
            min_score=max(0.0, float(min_score)),
            include_ancestors=bool(include_ancestors),
            include_replies=bool(include_replies),
            max_reply_depth_from_match=max(0, int(max_reply_depth_from_match)),
        )

    @classmethod
    def from_crawler_task(cls, task: CrawlerTask) -> DiscussionTopicQuery:
        """
        Extract a strongly typed DiscussionTopicQuery from a standard CrawlerTask.
        """
        meta = task.metadata or {}
        topic = task.query_or_target or meta.get("topic", "") or meta.get("query", "")
        keywords = meta.get("keywords", [])
        if isinstance(keywords, str):
            keywords = [k.strip() for k in keywords.split(",") if k.strip()]

        tags = meta.get("tags", [])
        if isinstance(tags, str):
            tags = [t.strip() for t in tags.split(",") if t.strip()]

        return cls.from_input(
            topic=topic,
            keywords=keywords,
            target_platform=meta.get("platform") or meta.get("target_platform"),
            target_community=meta.get("community") or meta.get("community_id") or meta.get("target_community"),
            repository_association=meta.get("repository") or meta.get("repository_association"),
            after_date=meta.get("after_date") or meta.get("since"),
            before_date=meta.get("before_date") or meta.get("until"),
            tags=tags,
            min_score=float(meta.get("min_score", 0.4)),
            include_ancestors=bool(meta.get("include_ancestors", True)),
            include_replies=bool(meta.get("include_replies", False)),
            max_reply_depth_from_match=int(meta.get("max_reply_depth_from_match", 1)),
        )

    def extract_search_terms(self) -> list[str]:
        """
        Tokenize raw topic and keywords into normalized search terms, filtering stop words.
        """
        terms: list[str] = []
        if self.raw_topic:
            clean_topic = re.sub(r"[^a-zA-Z0-9_\-./]", " ", self.raw_topic)
            for word in clean_topic.split():
                w_lower = word.lower()
                if len(w_lower) >= 2 and w_lower not in STOP_WORDS and w_lower not in terms:
                    terms.append(w_lower)
                for sub in re.split(r"[-_./]", word):
                    s_lower = sub.lower()
                    if len(s_lower) >= 2 and s_lower not in STOP_WORDS and s_lower not in terms:
                        terms.append(s_lower)
        for kw in self.keywords:
            clean_kw = re.sub(r"[^a-zA-Z0-9_\-./]", " ", kw)
            for word in clean_kw.split():
                k_lower = word.lower()
                if len(k_lower) >= 2 and k_lower not in STOP_WORDS and k_lower not in terms:
                    terms.append(k_lower)
                for sub in re.split(r"[-_./]", word):
                    s_lower = sub.lower()
                    if len(s_lower) >= 2 and s_lower not in STOP_WORDS and s_lower not in terms:
                        terms.append(s_lower)
        return terms

    def matches_constraints(
        self,
        platform: Optional[CommunityPlatform],
        community_id: Optional[str],
        created_at: Optional[str],
        repository_association: Optional[str] = None,
    ) -> tuple[bool, Optional[str]]:
        """
        Verify if a candidate discussion satisfies all hard filtering constraints.
        Returns (is_match, failure_reason).
        """
        if self.target_platform and platform and platform != self.target_platform:
            return False, f"Platform '{platform.value}' does not match target '{self.target_platform.value}'"

        if self.target_community and community_id:
            norm_target = normalize_community_id(self.target_community)
            norm_comm = normalize_community_id(community_id)
            if norm_target != norm_comm:
                return False, f"Community '{norm_comm}' does not match target '{norm_target}'"

        if self.repository_association and repository_association:
            if self.repository_association.lower() not in repository_association.lower():
                return False, f"Repository '{repository_association}' does not match '{self.repository_association}'"

        if created_at:
            ts = parse_iso_timestamp(created_at)
            if self.after_date:
                after_ts = parse_iso_timestamp(self.after_date)
                if ts < after_ts:
                    return False, f"Created date '{created_at}' is before '{self.after_date}'"
            if self.before_date:
                before_ts = parse_iso_timestamp(self.before_date)
                if ts > before_ts:
                    return False, f"Created date '{created_at}' is after '{self.before_date}'"

        return True, None


@dataclass
class DiscussionSelectionParams:
    """
    Execution and resource limits for targeted discussion selection.
    """
    topic_query: DiscussionTopicQuery
    max_discussions: int = 5
    max_comments_per_discussion: int = 20
    max_reply_depth: int = 5
    max_total_bytes: int = 1_000_000
    max_requests: int = 25
    timeout_seconds: float = 30.0
    max_concurrency: int = 4
    is_cancelled: Optional[Callable[[], bool]] = None

    def __post_init__(self) -> None:
        if self.max_discussions <= 0:
            raise CommunityValidationError("max_discussions", "max_discussions must be greater than 0.")
        if self.max_comments_per_discussion < 0:
            raise CommunityValidationError("max_comments_per_discussion", "max_comments_per_discussion cannot be negative.")
        if self.max_reply_depth <= 0:
            raise CommunityValidationError("max_reply_depth", "max_reply_depth must be greater than 0.")
        if self.max_total_bytes <= 0:
            raise CommunityValidationError("max_total_bytes", "max_total_bytes must be greater than 0.")
        if self.max_requests <= 0:
            raise CommunityValidationError("max_requests", "max_requests must be greater than 0.")
        if self.timeout_seconds <= 0.0:
            raise CommunityValidationError("timeout_seconds", "timeout_seconds must be greater than 0.")
        if self.max_concurrency <= 0:
            raise CommunityValidationError("max_concurrency", "max_concurrency must be greater than 0.")


# -----------------------------------------------------------------------------
# Scored Post and Selection Context Models
# -----------------------------------------------------------------------------

@dataclass
class ScoredDiscussionPost:
    """
    Post or comment scored deterministically against a DiscussionTopicQuery.
    """
    post: StructuredDiscussionPost
    relevance_score: float
    matched_terms: list[str] = field(default_factory=list)
    match_reasons: list[str] = field(default_factory=list)
    is_direct_match: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "post_id": self.post.post_id,
            "discussion_id": self.post.discussion_id,
            "relevance_score": round(self.relevance_score, 4),
            "matched_terms": list(self.matched_terms),
            "match_reasons": list(self.match_reasons),
            "is_direct_match": self.is_direct_match,
            "post": self.post.to_dict(),
        }


@dataclass
class SelectedDiscussionContext:
    """
    Pruned, hierarchy-preserving discussion slice containing matching posts
    and necessary ancestor/reply context for full comprehension.
    """
    discussion: StructuredDiscussion
    matching_posts: list[ScoredDiscussionPost] = field(default_factory=list)
    context_posts: list[StructuredDiscussionPost] = field(default_factory=list)
    pruned_thread_structure: ThreadStructure = field(default_factory=ThreadStructure)
    total_bytes: int = 0
    is_partial: bool = False
    partial_reasons: list[str] = field(default_factory=list)

    @property
    def total_retained_posts(self) -> int:
        return len(self.matching_posts) + len(self.context_posts)

    def to_dict(self) -> dict[str, Any]:
        return {
            "discussion_id": self.discussion.discussion_id,
            "title": self.discussion.title,
            "url": self.discussion.url,
            "platform": self.discussion.community_context.platform.value,
            "community_id": self.discussion.community_context.community_id,
            "matching_posts_count": len(self.matching_posts),
            "context_posts_count": len(self.context_posts),
            "total_retained_posts": self.total_retained_posts,
            "total_bytes": self.total_bytes,
            "is_partial": self.is_partial,
            "partial_reasons": list(self.partial_reasons),
            "matching_posts": [p.to_dict() for p in self.matching_posts],
            "context_post_ids": [p.post_id for p in self.context_posts],
            "pruned_thread_structure": self.pruned_thread_structure.to_dict(),
        }

    def to_discussion_source_materials(self) -> list[DiscussionSourceMaterial]:
        """
        Convert all retained posts (matching + ancestors/replies) into canonical DiscussionSourceMaterial instances.
        """
        materials: list[DiscussionSourceMaterial] = []
        matching_map = {p.post.post_id: p for p in self.matching_posts}

        all_posts: list[StructuredDiscussionPost] = [p.post for p in self.matching_posts] + list(self.context_posts)
        if self.discussion and hasattr(self.discussion, "posts"):
            for dp in self.discussion.posts.values():
                all_posts.append(dp)

        # Deduplicate posts by post_id
        seen_pids: set[str] = set()
        deduped_posts: list[StructuredDiscussionPost] = []
        for p in all_posts:
            if p.post_id not in seen_pids:
                seen_pids.add(p.post_id)
                deduped_posts.append(p)

        for post in deduped_posts:
            scored_item = matching_map.get(post.post_id)
            is_direct = scored_item is not None
            rel_score = scored_item.relevance_score if scored_item else 0.0
            reasons = scored_item.match_reasons if scored_item else ["ancestor_context"]

            mat = DiscussionSourceMaterial(
                material_id=f"mat-{self.discussion.discussion_id}-{post.post_id}",
                discussion_id=self.discussion.discussion_id,
                community_context=self.discussion.community_context,
                post_id=post.post_id,
                parent_id=post.parent_id,
                title=self.discussion.title,
                content=post.normalized_text or post.raw_content,
                author_id=post.author_id,
                permalink=post.permalink or self.discussion.url,
                depth=post.depth,
                is_root=post.is_root,
                is_accepted_answer=post.engagement.is_accepted_answer if post.engagement else False,
                score=post.engagement.score if post.engagement else None,
                tags=list(self.discussion.tags),
                status=self.discussion.status,
                content_checksum=post.content_checksum,
                provenance=post.provenance or self.discussion.provenance,
                created_at=post.created_at,
                metadata={
                    **self.discussion.metadata,
                    **post.metadata,
                    "is_direct_match": is_direct,
                    "relevance_score": rel_score,
                    "match_reasons": reasons,
                    "code_blocks_count": len(post.code_blocks),
                    "links_count": len(post.links),
                    "quotes_count": len(post.quotes),
                    "is_deleted": post.is_deleted,
                },
            )
            materials.append(mat)
        return materials


# -----------------------------------------------------------------------------
# Discussion Relevance Scorer
# -----------------------------------------------------------------------------

class DiscussionRelevanceScorer:
    """
    Deterministic scoring engine for discussion posts and comment trees.
    Evaluates lexical token overlaps, title matches, code keyword matches,
    tags, and factual structural markers without LLMs, sentiment, or popularity-as-truth bias.
    """

    @classmethod
    def score_post(
        cls,
        post: StructuredDiscussionPost,
        query: DiscussionTopicQuery,
        discussion_title: str = "",
        discussion_tags: Optional[list[str]] = None,
    ) -> ScoredDiscussionPost:
        """
        Score a single post/comment against query terms and metadata signals.
        """
        score = 0.0
        matched_terms: set[str] = set()
        reasons: list[str] = []

        terms = query.extract_search_terms()
        if not terms:
            return ScoredDiscussionPost(
                post=post,
                relevance_score=1.0 if not post.is_deleted else 0.0,
                matched_terms=[],
                match_reasons=["default_match_no_query_terms"],
                is_direct_match=not post.is_deleted,
            )

        # Skip deleted / tombstoned posts from direct positive scoring
        if post.is_deleted:
            return ScoredDiscussionPost(
                post=post,
                relevance_score=0.0,
                matched_terms=[],
                match_reasons=["post_is_deleted"],
                is_direct_match=False,
            )

        post_text = (post.normalized_text or post.raw_content or "").lower()
        post_tokens = set(re.findall(r"\b[a-zA-Z0-9_\-./]{2,}\b", post_text))

        # 1. Exact topic phrase match in post body (+0.40)
        if query.raw_topic and len(query.raw_topic) >= 3 and query.raw_topic.lower() in post_text:
            score += 0.40
            matched_terms.add(query.raw_topic.lower())
            reasons.append(f"exact_topic_phrase_match:'{query.raw_topic}'")

        # 2. Token overlap in post body (+0.30 * fraction)
        body_matched = [t for t in terms if t in post_tokens or t in post_text]
        if body_matched:
            fraction = len(body_matched) / len(terms)
            score += 0.30 * fraction
            for t in body_matched:
                matched_terms.add(t)
            reasons.append(f"body_token_overlap:{len(body_matched)}/{len(terms)}")

        # 3. Code block keywords match (+0.25)
        code_matched = []
        for cb in post.code_blocks:
            cb_text = cb.code.lower()
            for t in terms:
                if t in cb_text and t not in code_matched:
                    code_matched.append(t)
        if code_matched:
            score += 0.25
            for t in code_matched:
                matched_terms.add(t)
            reasons.append(f"code_block_match:{code_matched}")

        # 4. Heading keyword match (+0.20)
        heading_matched = []
        for _, h_text in post.headings:
            h_lower = h_text.lower()
            for t in terms:
                if t in h_lower and t not in heading_matched:
                    heading_matched.append(t)
        if heading_matched:
            score += 0.20
            for t in heading_matched:
                matched_terms.add(t)
            reasons.append(f"heading_match:{heading_matched}")

        # 5. Root post title association match (+0.15)
        if post.is_root and discussion_title:
            title_lower = discussion_title.lower()
            title_matched = [t for t in terms if t in title_lower]
            if title_matched:
                score += 0.15 * (len(title_matched) / len(terms))
                for t in title_matched:
                    matched_terms.add(t)
                reasons.append(f"title_match:{title_matched}")

        # 6. Tag matches (+0.15)
        all_tags = set([t.lower() for t in post.tags] + [t.lower() for t in (discussion_tags or [])])
        tag_matched = [t for t in terms if t in all_tags]
        if tag_matched:
            score += 0.15
            for t in tag_matched:
                matched_terms.add(t)
            reasons.append(f"tag_match:{tag_matched}")

        # 7. Factual structural signal: accepted answer (+0.10)
        if post.engagement and post.engagement.is_accepted_answer:
            score += 0.10
            reasons.append("accepted_answer_bonus")

        # Bound score to [0.0, 1.0]
        final_score = min(1.0, max(0.0, score))
        is_direct = final_score >= query.min_score and final_score > 0.0

        return ScoredDiscussionPost(
            post=post,
            relevance_score=final_score,
            matched_terms=sorted(list(matched_terms)),
            match_reasons=reasons,
            is_direct_match=is_direct,
        )


# -----------------------------------------------------------------------------
# Discussion Selection Outcome
# -----------------------------------------------------------------------------

@dataclass
class DiscussionSelectionResult:
    """
    Overall outcome of targeted discussion selection and context retrieval.
    """
    selected_discussions: list[SelectedDiscussionContext] = field(default_factory=list)
    discovered_candidates_count: int = 0
    total_discussions_inspected: int = 0
    total_posts_scored: int = 0
    total_selected_posts: int = 0
    total_bytes_retrieved: int = 0
    is_partial: bool = False
    partial_reasons: list[str] = field(default_factory=list)
    errors: list[dict[str, Any]] = field(default_factory=list)
    execution_time_seconds: float = 0.0
    outcome_status: CrawlerReportStatus = CrawlerReportStatus.SUCCESS
    outcome_summary: str = ""

    def to_crawler_report(
        self,
        task: CrawlerTask,
        crawler_id: str = "crawler.community.default",
    ) -> CrawlerReport:
        """
        Convert selection result into a standardized AutonomOS CrawlerReport contract.
        """
        raw_sources: list[RawSourceReference] = []
        extracted_evidence: list[EvidenceItem] = []

        for s_disc in self.selected_discussions:
            disc = s_disc.discussion
            # RawSourceReference for discussion
            raw_sources.append(RawSourceReference(
                url_or_ref=disc.url,
                title=disc.title,
                publisher=disc.community_context.platform.value,
                source_type=SourceType.COMMUNITY,
                checksum=compute_sha256(disc.title + disc.url),
                bytes_fetched=s_disc.total_bytes,
                content_snippet=disc.title,
                fetched_at=disc.created_at,
                metadata={
                    "community_id": disc.community_context.community_id,
                    "platform": disc.community_context.platform.value,
                    "total_retained_posts": s_disc.total_retained_posts,
                    "is_partial": s_disc.is_partial,
                    "partial_reasons": s_disc.partial_reasons,
                },
            ))

            # EvidenceItem for each selected material
            materials = s_disc.to_discussion_source_materials()
            for mat in materials:
                items = mat.to_evidence_items(
                    request_id=task.request_id,
                    crawler_task_id=task.task_id,
                    crawler_id=crawler_id,
                    question_id=task.question_id,
                    correlation_id=task.correlation_id,
                )
                extracted_evidence.extend(items)

        status = self.outcome_status
        if status == CrawlerReportStatus.SUCCESS and not extracted_evidence:
            summary = self.outcome_summary or f"No relevant discussions matched query criteria."
        else:
            summary = self.outcome_summary or (
                f"Selected {len(extracted_evidence)} posts across {len(self.selected_discussions)} discussions "
                f"(inspected {self.total_discussions_inspected} threads, {self.total_posts_scored} posts scored)."
            )

        error_msg = "; ".join([e.get("error", "") for e in self.errors if e.get("error")]) if self.errors else None

        return CrawlerReport(
            report_id=f"crep-{task.task_id}-{uuid.uuid4().hex[:6]}",
            crawler_task_id=task.task_id,
            crawler_id=crawler_id,
            request_id=task.request_id,
            plan_id=task.plan_id,
            question_id=task.question_id,
            correlation_id=task.correlation_id,
            status=status,
            raw_sources=raw_sources,
            extracted_evidence=extracted_evidence,
            summary=summary,
            error_message=error_msg,
            execution_time_seconds=self.execution_time_seconds,
            metadata={
                "discovered_candidates_count": self.discovered_candidates_count,
                "total_discussions_inspected": self.total_discussions_inspected,
                "total_posts_scored": self.total_posts_scored,
                "total_selected_posts": self.total_selected_posts,
                "total_bytes_retrieved": self.total_bytes_retrieved,
                "is_partial": self.is_partial,
                "partial_reasons": list(self.partial_reasons),
                "errors": list(self.errors),
            },
        )


# -----------------------------------------------------------------------------
# Discussion Selection Engine Implementation
# -----------------------------------------------------------------------------

class DiscussionSelectionEngine:
    """
    Deterministic targeted discussion selection engine.
    Orchestrates discovery, multi-provider ranking, bounded retrieval,
    content extraction, relevance scoring, and hierarchy-preserving context pruning.
    """

    def __init__(
        self,
        providers: Optional[list[DiscussionProvider]] = None,
        discovery_engine: Optional[CommunityDiscoveryEngine] = None,
        thread_retriever: Optional[DiscussionThreadRetriever] = None,
    ):
        provs = providers or [FakeDiscussionProvider()]
        self.providers = provs
        self.discovery_engine = discovery_engine or CommunityDiscoveryEngine(providers=self.providers)
        self.thread_retriever = thread_retriever or DiscussionThreadRetriever(providers=self.providers)

    def select_and_retrieve(
        self,
        params: DiscussionSelectionParams,
        task: Optional[CrawlerTask] = None,
    ) -> DiscussionSelectionResult:
        """
        Execute targeted discussion selection and bounded retrieval workflow.
        """
        start_time = time.perf_counter()
        query = params.topic_query

        # Check pre-existing cancellation
        if params.is_cancelled and params.is_cancelled():
            return DiscussionSelectionResult(
                outcome_status=CrawlerReportStatus.FAILED,
                outcome_summary="Discussion selection was cancelled before execution.",
                execution_time_seconds=round(time.perf_counter() - start_time, 4),
            )

        # 1. Discover candidate discussions
        platforms = [query.target_platform] if query.target_platform else []
        communities = [query.target_community] if query.target_community else []
        discovery_params = DiscussionDiscoveryParams(
            query=query.raw_topic or " ".join(query.keywords) or "discussion",
            platforms=platforms,
            communities=communities,
            repository_association=query.repository_association,
            after_date=query.after_date,
            before_date=query.before_date,
            tags=list(query.tags),
            max_discussions=params.max_discussions * 2,  # Fetch wider candidate pool for post-level pruning
            max_requests=params.max_requests,
            timeout_seconds=params.timeout_seconds,
        )

        try:
            discovery_result = self.discovery_engine.discover(
                discovery_params,
                is_cancelled=params.is_cancelled,
            )
        except CommunityCancelledError:
            return DiscussionSelectionResult(
                outcome_status=CrawlerReportStatus.FAILED,
                outcome_summary="Discussion discovery was cancelled.",
                execution_time_seconds=round(time.perf_counter() - start_time, 4),
            )
        except CommunityTimeoutError as e:
            return DiscussionSelectionResult(
                outcome_status=CrawlerReportStatus.TIMED_OUT,
                outcome_summary=f"Discussion discovery timed out: {str(e)}",
                errors=[{"stage": "discovery", "error": str(e)}],
                execution_time_seconds=round(time.perf_counter() - start_time, 4),
            )
        except (CommunityProviderError, CommunityAuthenticationError) as e:
            return DiscussionSelectionResult(
                outcome_status=CrawlerReportStatus.FAILED,
                outcome_summary=f"Discussion discovery failed: {str(e)}",
                errors=[{"stage": "discovery", "error": str(e)}],
                execution_time_seconds=round(time.perf_counter() - start_time, 4),
            )

        if not discovery_result.candidates:
            if discovery_result.errors:
                is_timeout = any("timeout" in err.lower() for err in discovery_result.errors)
                return DiscussionSelectionResult(
                    discovered_candidates_count=0,
                    outcome_status=CrawlerReportStatus.TIMED_OUT if is_timeout else CrawlerReportStatus.FAILED,
                    outcome_summary=f"Discussion discovery failed: {'; '.join(discovery_result.errors)}",
                    errors=[{"stage": "discovery", "error": err} for err in discovery_result.errors],
                    execution_time_seconds=round(time.perf_counter() - start_time, 4),
                )
            return DiscussionSelectionResult(
                discovered_candidates_count=0,
                outcome_status=CrawlerReportStatus.SUCCESS,
                outcome_summary="No candidate discussions found matching discovery criteria.",
                execution_time_seconds=round(time.perf_counter() - start_time, 4),
            )

        # 2. Filter candidate discussions against hard constraints and score threshold
        valid_candidates: list[DiscoveredDiscussionCandidate] = []
        for cand in discovery_result.candidates:
            is_match, _ = query.matches_constraints(
                platform=cand.platform,
                community_id=cand.community_id,
                created_at=cand.created_at,
                repository_association=cand.metadata.get("repository_association"),
            )
            if is_match:
                valid_candidates.append(cand)

        if not valid_candidates:
            return DiscussionSelectionResult(
                discovered_candidates_count=len(discovery_result.candidates),
                outcome_status=CrawlerReportStatus.SUCCESS,
                outcome_summary="Candidate discussions were found but none satisfied query constraints.",
                execution_time_seconds=round(time.perf_counter() - start_time, 4),
            )

        # Cap candidates to max_discussions budget
        bounded_candidates = valid_candidates[: params.max_discussions]

        # 3. Retrieve discussion threads
        thread_requests = [
            ThreadRetrievalRequest(
                discussion_id=cand.discussion_id,
                platform=cand.platform,
                community_id=cand.community_id,
                max_comments=params.max_comments_per_discussion,
                max_depth=params.max_reply_depth,
            )
            for cand in bounded_candidates
        ]

        retrieval_limits = DiscussionRetrievalLimits(
            max_discussions=params.max_discussions,
            max_comments_per_discussion=params.max_comments_per_discussion,
            max_reply_depth=params.max_reply_depth,
            max_total_bytes=params.max_total_bytes,
            max_provider_operations=params.max_requests,
            max_execution_time_seconds=params.timeout_seconds,
            max_concurrency=params.max_concurrency,
        )

        batch_params = BatchThreadRetrievalParams(
            requests=thread_requests,
            limits=retrieval_limits,
            timeout_seconds=params.timeout_seconds,
        )

        try:
            batch_result = self.thread_retriever.retrieve_batch(
                batch_params,
                is_cancelled=params.is_cancelled,
            )
        except CommunityCancelledError:
            return DiscussionSelectionResult(
                outcome_status=CrawlerReportStatus.FAILED,
                outcome_summary="Discussion thread retrieval was cancelled.",
                execution_time_seconds=round(time.perf_counter() - start_time, 4),
            )
        except Exception as e:
            return DiscussionSelectionResult(
                outcome_status=CrawlerReportStatus.FAILED,
                outcome_summary=f"Discussion thread retrieval failed: {str(e)}",
                errors=[{"stage": "retrieval", "error": str(e)}],
                execution_time_seconds=round(time.perf_counter() - start_time, 4),
            )

        # 4. Extract, score, and contextually prune each retrieved thread
        selected_discussions: list[SelectedDiscussionContext] = []
        total_posts_scored = 0
        total_selected_posts = 0
        total_bytes_accumulated = 0
        is_overall_partial = any(t.is_partial for t in batch_result.threads) or (
            batch_result.total_threads_failed > 0 and batch_result.total_threads_retrieved > 0
        )
        partial_reasons: list[str] = []
        for t in batch_result.threads:
            if t.partial_reasons:
                partial_reasons.extend(t.partial_reasons)
        if batch_result.total_threads_failed > 0:
            partial_reasons.append(f"{batch_result.total_threads_failed} discussion thread(s) failed during retrieval")

        for retrieved_thread in batch_result.threads:
            # Extract structured discussion
            structured_disc = DiscussionContentExtractor.extract_thread(retrieved_thread)

            # Score every post
            scored_posts: list[ScoredDiscussionPost] = []
            for post in structured_disc.posts.values():
                scored = DiscussionRelevanceScorer.score_post(
                    post=post,
                    query=query,
                    discussion_title=structured_disc.title,
                    discussion_tags=structured_disc.tags,
                )
                scored_posts.append(scored)
                total_posts_scored += 1

            # Deterministic sorting of matching posts: (-relevance_score, -post_score, -created_at_ts, post_id_asc)
            matching_posts = [p for p in scored_posts if p.is_direct_match]
            matching_posts.sort(
                key=lambda sp: (
                    -sp.relevance_score,
                    -(sp.post.engagement.score or 0) if sp.post.engagement else 0,
                    -parse_iso_timestamp(sp.post.created_at),
                    sp.post.post_id,
                )
            )

            # If no posts met min_score threshold, check if root post or thread title is relevant
            if not matching_posts:
                # Check root post score
                if structured_disc.root_post:
                    root_scored = DiscussionRelevanceScorer.score_post(
                        post=structured_disc.root_post,
                        query=query,
                        discussion_title=structured_disc.title,
                        discussion_tags=structured_disc.tags,
                    )
                    if root_scored.relevance_score >= query.min_score:
                        matching_posts = [root_scored]

            if not matching_posts:
                continue

            # 5. Build pruned hierarchy with ancestor and reply context
            retained_pids: set[str] = set()
            context_posts: list[StructuredDiscussionPost] = []
            matching_pids = {mp.post.post_id for mp in matching_posts}

            for mp in matching_posts:
                retained_pids.add(mp.post.post_id)

                # Ancestor preservation
                if query.include_ancestors:
                    ancestors = structured_disc.thread_structure.get_ancestors(mp.post.post_id)
                    for anc in ancestors:
                        if anc.post_id not in retained_pids:
                            retained_pids.add(anc.post_id)
                            anc_struct = structured_disc.get_post(anc.post_id)
                            if anc_struct and anc_struct.post_id not in matching_pids:
                                context_posts.append(anc_struct)

                # Bounded reply preservation
                if query.include_replies and query.max_reply_depth_from_match > 0:
                    replies = structured_disc.thread_structure.get_replies(mp.post.post_id)
                    for rep in replies[: query.max_reply_depth_from_match]:
                        if rep.post_id not in retained_pids:
                            retained_pids.add(rep.post_id)
                            rep_struct = structured_disc.get_post(rep.post_id)
                            if rep_struct and rep_struct.post_id not in matching_pids:
                                context_posts.append(rep_struct)

            # Construct pruned ThreadStructure
            pruned_tree = ThreadStructure(root_post_id=structured_disc.thread_structure.root_post_id)
            for pid in retained_pids:
                raw_p = structured_disc.thread_structure.get_post(pid)
                if raw_p:
                    pruned_tree.add_post(raw_p)

            thread_bytes = retrieved_thread.bytes_retrieved
            total_bytes_accumulated += thread_bytes

            disc_partial = retrieved_thread.is_partial
            disc_partial_reasons = list(retrieved_thread.partial_reasons)

            # Check byte budget
            if total_bytes_accumulated > params.max_total_bytes:
                disc_partial = True
                disc_partial_reasons.append(
                    f"Discussion exceeds byte limit ({total_bytes_accumulated} > {params.max_total_bytes})"
                )
                is_overall_partial = True
                partial_reasons.extend(disc_partial_reasons)

            selected_ctx = SelectedDiscussionContext(
                discussion=structured_disc,
                matching_posts=matching_posts,
                context_posts=context_posts,
                pruned_thread_structure=pruned_tree,
                total_bytes=thread_bytes,
                is_partial=disc_partial,
                partial_reasons=disc_partial_reasons,
            )
            selected_discussions.append(selected_ctx)
            total_selected_posts += len(matching_posts)

            # Stop if byte limit exceeded
            if total_bytes_accumulated >= params.max_total_bytes:
                break

        # Deterministic sorting of selected discussions: (-top_match_score, -created_at_ts, discussion_id_asc)
        selected_discussions.sort(
            key=lambda sc: (
                -(sc.matching_posts[0].relevance_score if sc.matching_posts else 0.0),
                -parse_iso_timestamp(sc.discussion.created_at),
                sc.discussion.discussion_id,
            )
        )

        elapsed = round(time.perf_counter() - start_time, 4)

        if not selected_discussions:
            return DiscussionSelectionResult(
                discovered_candidates_count=len(discovery_result.candidates),
                total_discussions_inspected=len(batch_result.threads),
                total_posts_scored=total_posts_scored,
                total_selected_posts=0,
                total_bytes_retrieved=total_bytes_accumulated,
                outcome_status=CrawlerReportStatus.SUCCESS,
                outcome_summary="Discussions were inspected but no posts met relevance criteria.",
                errors=list(batch_result.errors),
                execution_time_seconds=elapsed,
            )

        report_status = CrawlerReportStatus.PARTIAL if is_overall_partial else CrawlerReportStatus.SUCCESS

        return DiscussionSelectionResult(
            selected_discussions=selected_discussions,
            discovered_candidates_count=len(discovery_result.candidates),
            total_discussions_inspected=len(batch_result.threads),
            total_posts_scored=total_posts_scored,
            total_selected_posts=total_selected_posts,
            total_bytes_retrieved=total_bytes_accumulated,
            is_partial=is_overall_partial,
            partial_reasons=sorted(list(set(partial_reasons))),
            errors=list(batch_result.errors),
            execution_time_seconds=elapsed,
            outcome_status=report_status,
            outcome_summary=(
                f"Selected {total_selected_posts} relevant posts across {len(selected_discussions)} discussions."
            ),
        )

    def execute_task(
        self,
        task: CrawlerTask,
        crawler_id: str = "crawler.community.default",
    ) -> CrawlerReport:
        """
        Execute an end-to-end CrawlerTask and return a CrawlerReport.
        """
        topic_query = DiscussionTopicQuery.from_crawler_task(task)
        meta = task.metadata or {}

        params = DiscussionSelectionParams(
            topic_query=topic_query,
            max_discussions=int(meta.get("max_discussions", 5)),
            max_comments_per_discussion=int(meta.get("max_comments", 20)),
            max_reply_depth=int(meta.get("max_depth", 5)),
            max_total_bytes=int(meta.get("max_bytes", 1_000_000)),
            max_requests=int(meta.get("max_requests", 25)),
            timeout_seconds=float(meta.get("timeout_seconds", 30.0)),
            max_concurrency=int(meta.get("max_concurrency", 4)),
            is_cancelled=lambda: task.status.value == "CANCELLED",
        )

        result = self.select_and_retrieve(params, task=task)
        return result.to_crawler_report(task=task, crawler_id=crawler_id)
