"""
Community / Discussion Source and Structural Domain Models (Phase 1 / Part 6 / Step 1).

Establishes provider-neutral, strongly typed domain representations for:
- Community Context (platform, community identifier, repository association, source URL, access status)
- Discussion Posts & Comments (post ID, author ID, content, timestamps, parent ID, depth, permalink, engagement, checksum)
- Thread Hierarchy & Structure (tree relationships, deterministic traversal, cycle protection, bounded depth, orphan handling)
- Discussion Aggregate (platform, title, status, tags, engagement, root post, threaded posts, provenance)
- Discussion Source Material (extracted posts/slices, checksums, conversion to RawSourceReference and EvidenceItem contracts)
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
import hashlib
import re
from typing import Any, Optional
import uuid

from core.research.contracts.crawler_report import RawSourceReference
from core.research.contracts.evidence import EvidenceItem, EvidenceProvenance
from core.research.errors import CommunityValidationError
from core.research.types import FactClassification, ResearchConfidence, SourceType


def utc_now() -> str:
    """Generate ISO 8601 UTC timestamp."""
    return datetime.now(timezone.utc).isoformat()


def compute_sha256(content: str | bytes) -> str:
    """Compute SHA-256 hex digest for cryptographic integrity."""
    raw = content.encode("utf-8") if isinstance(content, str) else content
    return hashlib.sha256(raw).hexdigest()


def sanitize_author_identifier(author_id: Optional[str]) -> Optional[str]:
    """
    Sanitize author pseudonym or public identifier.
    Removes control characters and excessive whitespace.
    Never collects or attaches private profile or PII data.
    """
    if author_id is None:
        return None
    cleaned = re.sub(r"[\x00-\x1f\x7f-\x9f]", "", str(author_id)).strip()
    return cleaned if cleaned else None


# -----------------------------------------------------------------------------
# Enums & Value Classifications
# -----------------------------------------------------------------------------

class CommunityPlatform(str, Enum):
    """
    Taxonomy of supported community and discussion platforms.
    """
    REDDIT = "reddit"
    GITHUB_DISCUSSIONS = "github_discussions"
    STACK_EXCHANGE = "stack_exchange"
    DISCOURSE = "discourse"
    FORUM = "forum"
    GENERIC = "generic"

    @classmethod
    def from_string(cls, val: str | CommunityPlatform) -> CommunityPlatform:
        if isinstance(val, cls):
            return val
        if not val or not isinstance(val, str):
            return cls.GENERIC
        normalized = val.strip().lower().replace("-", "_").replace(" ", "_")
        for member in cls:
            if member.value == normalized:
                return member
        return cls.GENERIC


class DiscussionStatus(str, Enum):
    """
    Lifecycle and moderation status of a discussion thread.
    """
    OPEN = "open"
    CLOSED = "closed"
    LOCKED = "locked"
    PINNED = "pinned"
    RESOLVED = "resolved"
    ARCHIVED = "archived"
    UNKNOWN = "unknown"

    @classmethod
    def from_string(cls, val: str | DiscussionStatus) -> DiscussionStatus:
        if isinstance(val, cls):
            return val
        if not val or not isinstance(val, str):
            return cls.UNKNOWN
        normalized = val.strip().lower()
        for member in cls:
            if member.value == normalized:
                return member
        return cls.UNKNOWN


class AccessStatus(str, Enum):
    """
    Access level classification for a community or discussion.
    """
    PUBLIC = "public"
    RESTRICTED = "restricted"
    PRIVATE = "private"
    UNKNOWN = "unknown"

    @classmethod
    def from_string(cls, val: str | AccessStatus) -> AccessStatus:
        if isinstance(val, cls):
            return val
        if not val or not isinstance(val, str):
            return cls.PUBLIC
        normalized = val.strip().lower()
        for member in cls:
            if member.value == normalized:
                return member
        return cls.UNKNOWN


class ThreadOrdering(str, Enum):
    """
    Deterministic traversal and flattening modes for hierarchical discussion threads.
    """
    CHRONOLOGICAL = "chronological"
    THREADED_DFS = "threaded_dfs"
    THREADED_BFS = "threaded_bfs"
    TOP_LEVEL_FIRST = "top_level_first"

    @classmethod
    def from_string(cls, val: str | ThreadOrdering) -> ThreadOrdering:
        if isinstance(val, cls):
            return val
        if not val or not isinstance(val, str):
            return cls.CHRONOLOGICAL
        normalized = val.strip().lower()
        for member in cls:
            if member.value == normalized:
                return member
        return cls.CHRONOLOGICAL


# -----------------------------------------------------------------------------
# Engagement Metrics
# -----------------------------------------------------------------------------

@dataclass
class EngagementMetrics:
    """
    Public engagement and activity counters for a discussion or post.
    Strictly quantitative indicators; zero subjective sentiment or credibility weighting.
    """
    score: Optional[int] = None
    upvotes: Optional[int] = None
    downvotes: Optional[int] = None
    reply_count: Optional[int] = None
    views_count: Optional[int] = None
    is_accepted_answer: bool = False
    is_pinned: bool = False
    is_locked: bool = False
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "score": self.score,
            "upvotes": self.upvotes,
            "downvotes": self.downvotes,
            "reply_count": self.reply_count,
            "views_count": self.views_count,
            "is_accepted_answer": self.is_accepted_answer,
            "is_pinned": self.is_pinned,
            "is_locked": self.is_locked,
            "extra": dict(self.extra),
        }

    @classmethod
    def from_dict(cls, data: Optional[dict[str, Any]]) -> EngagementMetrics:
        if not data or not isinstance(data, dict):
            return cls()
        return cls(
            score=data.get("score") if isinstance(data.get("score"), (int, float)) else None,
            upvotes=data.get("upvotes") if isinstance(data.get("upvotes"), (int, float)) else None,
            downvotes=data.get("downvotes") if isinstance(data.get("downvotes"), (int, float)) else None,
            reply_count=data.get("reply_count") if isinstance(data.get("reply_count"), (int, float)) else None,
            views_count=data.get("views_count") if isinstance(data.get("views_count"), (int, float)) else None,
            is_accepted_answer=bool(data.get("is_accepted_answer", False)),
            is_pinned=bool(data.get("is_pinned", False)),
            is_locked=bool(data.get("is_locked", False)),
            extra=dict(data.get("extra", {})),
        )


# -----------------------------------------------------------------------------
# Community Context
# -----------------------------------------------------------------------------

@dataclass
class CommunityContext:
    """
    Contextual container identifying the origin community/forum and its platform attributes.
    """
    platform: CommunityPlatform
    community_id: str
    community_name: str
    source_url: str
    repository_association: Optional[str] = None
    category: Optional[str] = None
    access_status: AccessStatus = AccessStatus.PUBLIC
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if isinstance(self.platform, str):
            self.platform = CommunityPlatform.from_string(self.platform)
        if isinstance(self.access_status, str):
            self.access_status = AccessStatus.from_string(self.access_status)

        if not self.community_id or not isinstance(self.community_id, str) or not self.community_id.strip():
            raise CommunityValidationError("community_id", "Community ID must be a non-empty string.")
        self.community_id = self.community_id.strip()

        if not self.community_name or not isinstance(self.community_name, str) or not self.community_name.strip():
            self.community_name = self.community_id
        else:
            self.community_name = self.community_name.strip()

        if self.source_url is None:
            self.source_url = ""
        else:
            self.source_url = str(self.source_url).strip()

        if self.repository_association:
            self.repository_association = str(self.repository_association).strip()

        if self.category:
            self.category = str(self.category).strip()

    def to_dict(self) -> dict[str, Any]:
        return {
            "platform": self.platform.value if isinstance(self.platform, CommunityPlatform) else str(self.platform),
            "community_id": self.community_id,
            "community_name": self.community_name,
            "source_url": self.source_url,
            "repository_association": self.repository_association,
            "category": self.category,
            "access_status": self.access_status.value if isinstance(self.access_status, AccessStatus) else str(self.access_status),
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CommunityContext:
        if not isinstance(data, dict):
            raise CommunityValidationError("community_context", "CommunityContext data must be a dictionary.")
        return cls(
            platform=CommunityPlatform.from_string(data.get("platform", "generic")),
            community_id=data.get("community_id", ""),
            community_name=data.get("community_name", ""),
            source_url=data.get("source_url", ""),
            repository_association=data.get("repository_association"),
            category=data.get("category"),
            access_status=AccessStatus.from_string(data.get("access_status", "public")),
            metadata=dict(data.get("metadata", {})),
        )


# -----------------------------------------------------------------------------
# Discussion Post / Comment
# -----------------------------------------------------------------------------

@dataclass
class DiscussionPost:
    """
    Representation of an individual discussion post, root submission, or threaded comment.
    Treats all body text strictly as untrusted DATA.
    """
    post_id: str
    discussion_id: str
    content: str
    parent_id: Optional[str] = None
    author_id: Optional[str] = None
    created_at: str = field(default_factory=utc_now)
    updated_at: Optional[str] = None
    depth: int = 0
    permalink: Optional[str] = None
    engagement: EngagementMetrics = field(default_factory=EngagementMetrics)
    is_root: bool = False
    content_checksum: str = ""
    provenance: Optional[EvidenceProvenance] = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.post_id or not isinstance(self.post_id, str) or not self.post_id.strip():
            raise CommunityValidationError("post_id", "Post ID must be a non-empty string.")
        self.post_id = self.post_id.strip()

        if not self.discussion_id or not isinstance(self.discussion_id, str) or not self.discussion_id.strip():
            raise CommunityValidationError("discussion_id", "Discussion ID must be a non-empty string.")
        self.discussion_id = self.discussion_id.strip()

        if self.content is None:
            self.content = ""
        elif not isinstance(self.content, str):
            self.content = str(self.content)

        if self.parent_id is not None:
            self.parent_id = str(self.parent_id).strip()
            if not self.parent_id:
                self.parent_id = None

        if self.parent_id is None and not self.is_root and self.depth == 0:
            # By default top-level post with no parent at depth 0 is treated as root candidate
            self.is_root = True

        self.author_id = sanitize_author_identifier(self.author_id)

        if self.depth < 0:
            raise CommunityValidationError("depth", f"Post depth cannot be negative: {self.depth}")

        if not self.created_at:
            self.created_at = utc_now()

        if not self.content_checksum:
            self.content_checksum = compute_sha256(self.content)

        if isinstance(self.engagement, dict):
            self.engagement = EngagementMetrics.from_dict(self.engagement)

    def to_dict(self) -> dict[str, Any]:
        return {
            "post_id": self.post_id,
            "discussion_id": self.discussion_id,
            "parent_id": self.parent_id,
            "author_id": self.author_id,
            "content": self.content,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "depth": self.depth,
            "permalink": self.permalink,
            "engagement": self.engagement.to_dict() if self.engagement else None,
            "is_root": self.is_root,
            "content_checksum": self.content_checksum,
            "provenance": self.provenance.to_dict() if self.provenance else None,
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> DiscussionPost:
        if not isinstance(data, dict):
            raise CommunityValidationError("discussion_post", "DiscussionPost data must be a dictionary.")
        prov_data = data.get("provenance")
        provenance = EvidenceProvenance.from_dict(prov_data) if isinstance(prov_data, dict) else None
        eng_data = data.get("engagement")
        engagement = EngagementMetrics.from_dict(eng_data) if isinstance(eng_data, dict) else EngagementMetrics()

        return cls(
            post_id=data.get("post_id", ""),
            discussion_id=data.get("discussion_id", ""),
            content=data.get("content", ""),
            parent_id=data.get("parent_id"),
            author_id=data.get("author_id"),
            created_at=data.get("created_at", utc_now()),
            updated_at=data.get("updated_at"),
            depth=int(data.get("depth", 0)),
            permalink=data.get("permalink"),
            engagement=engagement,
            is_root=bool(data.get("is_root", False)),
            content_checksum=data.get("content_checksum", ""),
            provenance=provenance,
            metadata=dict(data.get("metadata", {})),
        )


# Alias DiscussionComment to DiscussionPost for seamless interchangeability
DiscussionComment = DiscussionPost


# -----------------------------------------------------------------------------
# Thread Structure & Hierarchy
# -----------------------------------------------------------------------------

@dataclass
class ThreadStructure:
    """
    Deterministic hierarchical tree container managing parent/child relationships,
    cycle detection, bounded traversal, and orphan recovery for a discussion thread.
    """
    root_post_id: Optional[str] = None
    _posts: dict[str, DiscussionPost] = field(default_factory=dict)
    _children: dict[str, list[str]] = field(default_factory=dict)  # parent_id -> list of child post_ids

    def add_post(self, post: DiscussionPost) -> None:
        """
        Add a post/comment into the thread tree with cycle detection,
        depth validation, and deterministic ordering registration.
        """
        if not isinstance(post, DiscussionPost):
            raise CommunityValidationError("post", "Post must be an instance of DiscussionPost.")

        post_id = post.post_id

        # Duplicate ID check
        if post_id in self._posts:
            existing = self._posts[post_id]
            if existing.content_checksum != post.content_checksum or existing.parent_id != post.parent_id:
                raise CommunityValidationError("post_id", f"Duplicate post ID '{post_id}' with conflicting content or parent.")
            # Identical post already present
            return

        # Cycle detection: trace parent pointers upwards
        if post.parent_id:
            curr_parent = post.parent_id
            visited_ancestors: set[str] = {post_id}
            while curr_parent is not None:
                if curr_parent == post_id or curr_parent in visited_ancestors:
                    raise CommunityValidationError(
                        "parent_id",
                        f"Cycle detected in thread hierarchy: post '{post_id}' references ancestor '{curr_parent}'"
                    )
                visited_ancestors.add(curr_parent)
                parent_obj = self._posts.get(curr_parent)
                curr_parent = parent_obj.parent_id if parent_obj else None

        # Determine root status
        if post.is_root or (post.parent_id is None and self.root_post_id is None):
            self.root_post_id = post_id
            post.is_root = True

        # Calculate / adjust depth relative to parent if parent is already known
        if post.parent_id and post.parent_id in self._posts:
            post.depth = self._posts[post.parent_id].depth + 1

        self._posts[post_id] = post

        # Register child relationship
        parent_key = post.parent_id or "__root__"
        if parent_key not in self._children:
            self._children[parent_key] = []
        if post_id not in self._children[parent_key]:
            self._children[parent_key].append(post_id)

    def get_post(self, post_id: str) -> Optional[DiscussionPost]:
        """Retrieve post by ID."""
        return self._posts.get(post_id)

    def get_root_post(self) -> Optional[DiscussionPost]:
        """Retrieve the designated root post if present."""
        if self.root_post_id and self.root_post_id in self._posts:
            return self._posts[self.root_post_id]
        # Fallback: look for post with is_root=True or parent_id=None
        for post in self._posts.values():
            if post.is_root or post.parent_id is None:
                return post
        return None

    def get_replies(self, parent_id: Optional[str] = None) -> list[DiscussionPost]:
        """
        Get direct child replies of a given parent ID.
        If parent_id is None, returns all top-level / root posts.
        """
        parent_key = parent_id or "__root__"
        child_ids = self._children.get(parent_key, [])
        replies = [self._posts[cid] for cid in child_ids if cid in self._posts]
        # Deterministic sorting: creation timestamp asc, then post_id asc
        replies.sort(key=lambda p: (p.created_at, p.post_id))
        return replies

    def get_all_posts(self) -> list[DiscussionPost]:
        """Return all posts currently in thread."""
        return list(self._posts.values())

    def get_ancestors(self, post_id: str) -> list[DiscussionPost]:
        """
        Return the chain of ancestor posts from root down to parent.
        """
        ancestors: list[DiscussionPost] = []
        curr = self._posts.get(post_id)
        if not curr:
            return ancestors

        curr_parent_id = curr.parent_id
        visited: set[str] = {post_id}
        while curr_parent_id and curr_parent_id in self._posts:
            if curr_parent_id in visited:
                break
            visited.add(curr_parent_id)
            parent_post = self._posts[curr_parent_id]
            ancestors.insert(0, parent_post)
            curr_parent_id = parent_post.parent_id

        return ancestors

    def get_orphan_posts(self) -> list[DiscussionPost]:
        """
        Identify posts whose parent_id is specified but not found in the thread.
        """
        orphans: list[DiscussionPost] = []
        for post in self._posts.values():
            if post.parent_id and post.parent_id not in self._posts:
                orphans.append(post)
        orphans.sort(key=lambda p: (p.created_at, p.post_id))
        return orphans

    def reparent_orphans_to_root(self) -> int:
        """
        Re-link any orphaned posts to the thread root post (or top-level if no root).
        Returns the count of reparented posts.
        """
        orphans = self.get_orphan_posts()
        if not orphans:
            return 0

        target_parent = self.root_post_id
        target_depth = 1 if target_parent else 0

        count = 0
        for orphan in orphans:
            old_parent = orphan.parent_id
            if old_parent and old_parent in self._children:
                if orphan.post_id in self._children[old_parent]:
                    self._children[old_parent].remove(orphan.post_id)

            orphan.parent_id = target_parent
            orphan.depth = target_depth
            new_key = target_parent or "__root__"
            if new_key not in self._children:
                self._children[new_key] = []
            if orphan.post_id not in self._children[new_key]:
                self._children[new_key].append(orphan.post_id)
            count += 1

        return count

    def get_thread_depth(self) -> int:
        """Calculate maximum tree depth across all posts."""
        if not self._posts:
            return 0
        return max(post.depth for post in self._posts.values())

    def total_posts(self) -> int:
        """Total number of posts and comments in the structure."""
        return len(self._posts)

    def total_replies(self) -> int:
        """Total number of reply comments (excluding root post)."""
        root_id = self.root_post_id
        return sum(1 for p in self._posts.values() if p.post_id != root_id and not p.is_root)

    def flatten_deterministic(
        self,
        ordering: ThreadOrdering = ThreadOrdering.CHRONOLOGICAL,
        max_depth: Optional[int] = None,
        max_posts: Optional[int] = None,
    ) -> list[DiscussionPost]:
        """
        Flatten discussion tree into a deterministic ordered sequence.
        Respects bounded limits (max_depth, max_posts).
        """
        if isinstance(ordering, str):
            ordering = ThreadOrdering.from_string(ordering)

        result: list[DiscussionPost] = []

        if ordering == ThreadOrdering.CHRONOLOGICAL:
            candidates = list(self._posts.values())
            if max_depth is not None:
                candidates = [p for p in candidates if p.depth <= max_depth]
            candidates.sort(key=lambda p: (p.created_at, p.post_id))
            result = candidates[:max_posts] if max_posts is not None else candidates

        elif ordering == ThreadOrdering.THREADED_DFS:
            # Depth-first pre-order traversal starting from top-level posts
            top_level = self.get_replies(parent_id=None)
            stack: list[str] = [p.post_id for p in reversed(top_level)]
            visited: set[str] = set()

            while stack:
                if max_posts is not None and len(result) >= max_posts:
                    break
                pid = stack.pop()
                if pid in visited or pid not in self._posts:
                    continue
                visited.add(pid)
                curr_post = self._posts[pid]
                if max_depth is not None and curr_post.depth > max_depth:
                    continue
                result.append(curr_post)

                # Push children in reverse chronological order so earliest is popped first
                children = self.get_replies(parent_id=pid)
                for child in reversed(children):
                    if child.post_id not in visited:
                        stack.append(child.post_id)

        elif ordering == ThreadOrdering.THREADED_BFS:
            # Breadth-first level-by-level traversal
            top_level = self.get_replies(parent_id=None)
            queue: deque[str] = deque([p.post_id for p in top_level])
            visited = set()

            while queue:
                if max_posts is not None and len(result) >= max_posts:
                    break
                pid = queue.popleft()
                if pid in visited or pid not in self._posts:
                    continue
                visited.add(pid)
                curr_post = self._posts[pid]
                if max_depth is not None and curr_post.depth > max_depth:
                    continue
                result.append(curr_post)

                children = self.get_replies(parent_id=pid)
                for child in children:
                    if child.post_id not in visited:
                        queue.append(child.post_id)

        elif ordering == ThreadOrdering.TOP_LEVEL_FIRST:
            # All root / level 0 posts first, then level 1, level 2, etc.
            candidates = list(self._posts.values())
            if max_depth is not None:
                candidates = [p for p in candidates if p.depth <= max_depth]
            candidates.sort(key=lambda p: (p.depth, p.created_at, p.post_id))
            result = candidates[:max_posts] if max_posts is not None else candidates

        return result

    def to_dict(self) -> dict[str, Any]:
        return {
            "root_post_id": self.root_post_id,
            "posts": {pid: p.to_dict() for pid, p in self._posts.items()},
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ThreadStructure:
        if not isinstance(data, dict):
            raise CommunityValidationError("thread_structure", "ThreadStructure data must be a dictionary.")
        tree = cls(root_post_id=data.get("root_post_id"))
        raw_posts = data.get("posts", {})
        if isinstance(raw_posts, dict):
            # Sort posts to insert parents first when possible to preserve depth
            post_objs = [DiscussionPost.from_dict(p) for p in raw_posts.values() if isinstance(p, dict)]
            # Two-pass insertion to resolve dependencies cleanly
            for p in post_objs:
                if p.parent_id is None or p.is_root:
                    tree.add_post(p)
            for p in post_objs:
                if p.parent_id is not None and not p.is_root:
                    tree.add_post(p)
        return tree


# -----------------------------------------------------------------------------
# Discussion Source Material (Extracted Snippet/Post for Evidence)
# -----------------------------------------------------------------------------

@dataclass
class DiscussionSourceMaterial:
    """
    Unit of extracted discussion source material representing a post, comment,
    or thread slice for research evidence evaluation and synthesis.
    """
    material_id: str
    discussion_id: str
    community_context: CommunityContext
    post_id: str
    parent_id: Optional[str] = None
    title: str = ""
    content: str = ""
    author_id: Optional[str] = None
    permalink: str = ""
    depth: int = 0
    is_root: bool = False
    is_accepted_answer: bool = False
    score: Optional[int] = None
    tags: list[str] = field(default_factory=list)
    status: DiscussionStatus = DiscussionStatus.OPEN
    content_checksum: str = ""
    provenance: Optional[EvidenceProvenance] = None
    created_at: str = ""
    retrieved_at: str = field(default_factory=utc_now)
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.material_id:
            self.material_id = f"mat-comm-{uuid.uuid4().hex[:8]}"

        if isinstance(self.status, str):
            self.status = DiscussionStatus.from_string(self.status)

        if not self.content_checksum:
            self.content_checksum = compute_sha256(self.content)

        if not self.retrieved_at:
            self.retrieved_at = utc_now()

        self.author_id = sanitize_author_identifier(self.author_id)

    @property
    def source_ref(self) -> str:
        """Generate authoritative canonical source reference string."""
        if self.permalink:
            return self.permalink
        base = self.community_context.source_url or f"community://{self.community_context.platform.value}/{self.community_context.community_id}"
        return f"{base}/discussions/{self.discussion_id}#{self.post_id}"

    def to_raw_source_reference(self) -> RawSourceReference:
        """Convert extracted discussion material into standard RawSourceReference."""
        stype = SourceType.FORUM if self.community_context.platform == CommunityPlatform.FORUM else SourceType.COMMUNITY

        meta: dict[str, Any] = {
            "material_id": self.material_id,
            "platform": self.community_context.platform.value,
            "community_id": self.community_context.community_id,
            "community_name": self.community_context.community_name,
            "discussion_id": self.discussion_id,
            "post_id": self.post_id,
            "parent_id": self.parent_id,
            "depth": self.depth,
            "author_id": self.author_id,
            "is_root": self.is_root,
            "is_accepted_answer": self.is_accepted_answer,
            "score": self.score,
            "tags": list(self.tags),
            "status": self.status.value,
            "content_checksum": self.content_checksum,
            "repository_association": self.community_context.repository_association,
        }

        display_title = f"[{self.community_context.platform.value}] {self.title or self.discussion_id}"
        if not self.is_root:
            display_title += f" (Reply {self.post_id})"

        return RawSourceReference(
            url_or_ref=self.source_ref,
            title=display_title,
            publisher=self.community_context.community_name or self.community_context.community_id,
            source_type=stype,
            checksum=self.content_checksum,
            bytes_fetched=len(self.content.encode("utf-8")),
            content_snippet=self.content[:2000],
            fetched_at=self.retrieved_at,
            metadata=meta,
        )

    def to_evidence_items(
        self,
        request_id: str,
        crawler_task_id: str,
        crawler_id: str,
        question_id: str = "",
        correlation_id: str = "",
    ) -> list[EvidenceItem]:
        """
        Convert extracted discussion material into standard EvidenceItem.
        Strictly categorizes content as FactClassification.SOURCE_CLAIM (untrusted external claim).
        """
        prov = EvidenceProvenance(
            request_id=request_id,
            crawler_task_id=crawler_task_id,
            crawler_id=crawler_id,
            question_id=question_id,
            source_ref=self.source_ref,
            correlation_id=correlation_id,
            captured_at=self.retrieved_at,
        )

        label_parts = [f"Community post in {self.community_context.community_id}"]
        if self.title:
            label_parts.append(f"'{self.title}'")
        if self.is_root:
            label_parts.append("(Root Topic)")
        else:
            label_parts.append(f"(Reply {self.post_id}, depth {self.depth})")
        if self.is_accepted_answer:
            label_parts.append("[Accepted Answer]")

        fact_label = " ".join(label_parts)
        stype = SourceType.FORUM if self.community_context.platform == CommunityPlatform.FORUM else SourceType.COMMUNITY

        # Confidence: supported if marked accepted answer or high engagement score, otherwise limited evidence
        if self.is_accepted_answer or (self.score is not None and self.score >= 10):
            confidence = ResearchConfidence.SUPPORTED
        else:
            confidence = ResearchConfidence.LIMITED_EVIDENCE

        meta: dict[str, Any] = {
            "material_id": self.material_id,
            "platform": self.community_context.platform.value,
            "community_id": self.community_context.community_id,
            "discussion_id": self.discussion_id,
            "post_id": self.post_id,
            "parent_id": self.parent_id,
            "depth": self.depth,
            "author_id": self.author_id,
            "is_root": self.is_root,
            "is_accepted_answer": self.is_accepted_answer,
            "score": self.score,
            "tags": list(self.tags),
            "status": self.status.value,
            "content_checksum": self.content_checksum,
            "repository_association": self.community_context.repository_association,
        }

        evidence = EvidenceItem(
            evidence_id=f"ev-comm-{self.material_id}",
            provenance=prov,
            extracted_fact=fact_label,
            content_snippet=self.content[:4000],
            classification=FactClassification.SOURCE_CLAIM,
            confidence=confidence,
            reliability_score=0.6,
            source_type=stype,
            checksum=self.content_checksum,
            metadata=meta,
        )
        return [evidence]

    def to_dict(self) -> dict[str, Any]:
        return {
            "material_id": self.material_id,
            "discussion_id": self.discussion_id,
            "community_context": self.community_context.to_dict(),
            "post_id": self.post_id,
            "parent_id": self.parent_id,
            "title": self.title,
            "content": self.content,
            "author_id": self.author_id,
            "permalink": self.permalink,
            "depth": self.depth,
            "is_root": self.is_root,
            "is_accepted_answer": self.is_accepted_answer,
            "score": self.score,
            "tags": list(self.tags),
            "status": self.status.value if isinstance(self.status, DiscussionStatus) else str(self.status),
            "content_checksum": self.content_checksum,
            "provenance": self.provenance.to_dict() if self.provenance else None,
            "created_at": self.created_at,
            "retrieved_at": self.retrieved_at,
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> DiscussionSourceMaterial:
        if not isinstance(data, dict):
            raise CommunityValidationError("source_material", "DiscussionSourceMaterial data must be a dictionary.")
        comm_ctx = CommunityContext.from_dict(data["community_context"])
        prov_data = data.get("provenance")
        provenance = EvidenceProvenance.from_dict(prov_data) if isinstance(prov_data, dict) else None

        return cls(
            material_id=data.get("material_id", f"mat-comm-{uuid.uuid4().hex[:8]}"),
            discussion_id=data.get("discussion_id", ""),
            community_context=comm_ctx,
            post_id=data.get("post_id", ""),
            parent_id=data.get("parent_id"),
            title=data.get("title", ""),
            content=data.get("content", ""),
            author_id=data.get("author_id"),
            permalink=data.get("permalink", ""),
            depth=int(data.get("depth", 0)),
            is_root=bool(data.get("is_root", False)),
            is_accepted_answer=bool(data.get("is_accepted_answer", False)),
            score=data.get("score") if isinstance(data.get("score"), (int, float)) else None,
            tags=list(data.get("tags", [])),
            status=DiscussionStatus.from_string(data.get("status", "open")),
            content_checksum=data.get("content_checksum", ""),
            provenance=provenance,
            created_at=data.get("created_at", ""),
            retrieved_at=data.get("retrieved_at", utc_now()),
            metadata=dict(data.get("metadata", {})),
        )


# -----------------------------------------------------------------------------
# Discussion Root Aggregate
# -----------------------------------------------------------------------------

@dataclass
class Discussion:
    """
    Root aggregate domain container representing a full community discussion thread,
    forum topic, or Q&A exchange.
    """
    discussion_id: str
    community_context: CommunityContext
    title: str
    url: str
    author_id: Optional[str] = None
    created_at: str = field(default_factory=utc_now)
    updated_at: Optional[str] = None
    status: DiscussionStatus = DiscussionStatus.OPEN
    tags: list[str] = field(default_factory=list)
    engagement: EngagementMetrics = field(default_factory=EngagementMetrics)
    root_post: Optional[DiscussionPost] = None
    thread_structure: ThreadStructure = field(default_factory=ThreadStructure)
    provenance: Optional[EvidenceProvenance] = None
    retrieved_at: str = field(default_factory=utc_now)
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.discussion_id or not isinstance(self.discussion_id, str) or not self.discussion_id.strip():
            raise CommunityValidationError("discussion_id", "Discussion ID must be a non-empty string.")
        self.discussion_id = self.discussion_id.strip()

        if self.title is None:
            self.title = ""
        else:
            self.title = str(self.title).strip()

        if self.url is None:
            self.url = ""
        else:
            self.url = str(self.url).strip()

        if isinstance(self.status, str):
            self.status = DiscussionStatus.from_string(self.status)

        self.author_id = sanitize_author_identifier(self.author_id)

        if not self.created_at:
            self.created_at = utc_now()

        if not self.retrieved_at:
            self.retrieved_at = utc_now()

        if isinstance(self.engagement, dict):
            self.engagement = EngagementMetrics.from_dict(self.engagement)

        # If root_post is provided, ensure it is added into thread_structure
        if self.root_post is not None:
            if not self.root_post.discussion_id:
                self.root_post.discussion_id = self.discussion_id
            self.root_post.is_root = True
            if self.thread_structure.get_post(self.root_post.post_id) is None:
                self.thread_structure.add_post(self.root_post)

    def add_post(self, post: DiscussionPost) -> None:
        """
        Add a post/comment to this discussion.
        Automatically verifies discussion ID affinity and registers in thread hierarchy.
        """
        if not isinstance(post, DiscussionPost):
            raise CommunityValidationError("post", "Must be a DiscussionPost instance.")

        if not post.discussion_id:
            post.discussion_id = self.discussion_id
        elif post.discussion_id != self.discussion_id:
            raise CommunityValidationError(
                "discussion_id",
                f"Post discussion ID '{post.discussion_id}' does not match discussion '{self.discussion_id}'"
            )

        if post.is_root and self.root_post is None:
            self.root_post = post

        self.thread_structure.add_post(post)

    def get_post(self, post_id: str) -> Optional[DiscussionPost]:
        """Retrieve post by ID from thread structure."""
        return self.thread_structure.get_post(post_id)

    def total_posts(self) -> int:
        """Total number of posts in discussion."""
        return self.thread_structure.total_posts()

    def total_replies(self) -> int:
        """Total number of reply comments."""
        return self.thread_structure.total_replies()

    def to_source_materials(
        self,
        ordering: ThreadOrdering = ThreadOrdering.CHRONOLOGICAL,
        max_depth: Optional[int] = None,
        max_posts: Optional[int] = None,
    ) -> list[DiscussionSourceMaterial]:
        """
        Extract bounded, ordered collection of DiscussionSourceMaterial instances
        for research pipeline evidence synthesis.
        """
        flattened = self.thread_structure.flatten_deterministic(
            ordering=ordering,
            max_depth=max_depth,
            max_posts=max_posts,
        )

        materials: list[DiscussionSourceMaterial] = []
        for post in flattened:
            mat = DiscussionSourceMaterial(
                material_id=f"mat-{self.discussion_id}-{post.post_id}",
                discussion_id=self.discussion_id,
                community_context=self.community_context,
                post_id=post.post_id,
                parent_id=post.parent_id,
                title=self.title,
                content=post.content,
                author_id=post.author_id,
                permalink=post.permalink or self.url,
                depth=post.depth,
                is_root=post.is_root,
                is_accepted_answer=post.engagement.is_accepted_answer if post.engagement else False,
                score=post.engagement.score if post.engagement else None,
                tags=list(self.tags),
                status=self.status,
                content_checksum=post.content_checksum,
                provenance=post.provenance or self.provenance,
                created_at=post.created_at,
                retrieved_at=self.retrieved_at,
                metadata={
                    **self.metadata,
                    **post.metadata,
                },
            )
            materials.append(mat)
        return materials

    def to_raw_source_references(
        self,
        ordering: ThreadOrdering = ThreadOrdering.CHRONOLOGICAL,
        max_depth: Optional[int] = None,
        max_posts: Optional[int] = None,
    ) -> list[RawSourceReference]:
        """Convert all discussion materials into standard RawSourceReference instances."""
        materials = self.to_source_materials(ordering=ordering, max_depth=max_depth, max_posts=max_posts)
        return [m.to_raw_source_reference() for m in materials]

    def to_evidence_items(
        self,
        request_id: str,
        crawler_task_id: str,
        crawler_id: str,
        question_id: str = "",
        correlation_id: str = "",
        ordering: ThreadOrdering = ThreadOrdering.CHRONOLOGICAL,
        max_depth: Optional[int] = None,
        max_posts: Optional[int] = None,
    ) -> list[EvidenceItem]:
        """Aggregate evidence items across all discussion materials."""
        materials = self.to_source_materials(ordering=ordering, max_depth=max_depth, max_posts=max_posts)
        items: list[EvidenceItem] = []
        for mat in materials:
            evs = mat.to_evidence_items(
                request_id=request_id,
                crawler_task_id=crawler_task_id,
                crawler_id=crawler_id,
                question_id=question_id,
                correlation_id=correlation_id,
            )
            items.extend(evs)
        return items

    def to_dict(self) -> dict[str, Any]:
        return {
            "discussion_id": self.discussion_id,
            "community_context": self.community_context.to_dict(),
            "title": self.title,
            "url": self.url,
            "author_id": self.author_id,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "status": self.status.value if isinstance(self.status, DiscussionStatus) else str(self.status),
            "tags": list(self.tags),
            "engagement": self.engagement.to_dict() if self.engagement else None,
            "root_post": self.root_post.to_dict() if self.root_post else None,
            "thread_structure": self.thread_structure.to_dict() if self.thread_structure else None,
            "provenance": self.provenance.to_dict() if self.provenance else None,
            "retrieved_at": self.retrieved_at,
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Discussion:
        if not isinstance(data, dict):
            raise CommunityValidationError("discussion", "Discussion data must be a dictionary.")
        comm_ctx = CommunityContext.from_dict(data["community_context"])
        eng_data = data.get("engagement")
        engagement = EngagementMetrics.from_dict(eng_data) if isinstance(eng_data, dict) else EngagementMetrics()
        root_data = data.get("root_post")
        root_post = DiscussionPost.from_dict(root_data) if isinstance(root_data, dict) else None
        tree_data = data.get("thread_structure")
        thread_structure = ThreadStructure.from_dict(tree_data) if isinstance(tree_data, dict) else ThreadStructure()
        prov_data = data.get("provenance")
        provenance = EvidenceProvenance.from_dict(prov_data) if isinstance(prov_data, dict) else None

        return cls(
            discussion_id=data.get("discussion_id", ""),
            community_context=comm_ctx,
            title=data.get("title", ""),
            url=data.get("url", ""),
            author_id=data.get("author_id"),
            created_at=data.get("created_at", utc_now()),
            updated_at=data.get("updated_at"),
            status=DiscussionStatus.from_string(data.get("status", "open")),
            tags=list(data.get("tags", [])),
            engagement=engagement,
            root_post=root_post,
            thread_structure=thread_structure,
            provenance=provenance,
            retrieved_at=data.get("retrieved_at", utc_now()),
            metadata=dict(data.get("metadata", {})),
        )
