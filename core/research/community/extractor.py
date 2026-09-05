"""
Discussion Content Extraction and Normalization Engine (Phase 1 / Part 6 / Step 5).

Normalizes retrieved discussion posts, comments, and threads into structured domain models:
- Paragraphs, headings, and lists
- Code blocks (preserving exact syntax and fence language without execution)
- Quoted text and author attributions
- Hyperlinks with domain classification (internal vs external, preserved as references)
- Mentions and user references
- Deleted/tombstoned content identification
- Cryptographic SHA-256 integrity checksums and full provenance preservation
"""
from __future__ import annotations

from dataclasses import dataclass, field
import html
import logging
import re
from typing import Any, Optional
import urllib.parse
import uuid

from core.research.contracts.evidence import EvidenceProvenance
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
from core.research.community.retriever import RetrievedDiscussionThread
from core.research.errors import CommunityValidationError
from core.research.search.normalization import extract_domain, normalize_url

logger = logging.getLogger("AutonomOS.Research.DiscussionExtractor")


# -----------------------------------------------------------------------------
# Regex Extraction Patterns
# -----------------------------------------------------------------------------

# Code block pattern: ```(language)?\n(code)\n```
FENCED_CODE_BLOCK_PATTERN = re.compile(r"```([a-zA-Z0-9_+#.-]*)\n?(.*?)```", re.DOTALL)

# Inline code pattern: `code`
INLINE_CODE_PATTERN = re.compile(r"`([^`\n]+)`")

# Markdown heading pattern: # Heading
HEADING_PATTERN = re.compile(r"^(#{1,6})\s+(.+)$", re.MULTILINE)

# Blockquote pattern: > Quote line
BLOCKQUOTE_PATTERN = re.compile(r"^(?:>\s?)(.+)$", re.MULTILINE)

# Markdown link pattern: [text](url)
MARKDOWN_LINK_PATTERN = re.compile(r"\[([^\]]+)\]\(([^)]+)\)")

# Raw URL pattern: https://... or http://...
RAW_URL_PATTERN = re.compile(r"(?<!\()(https?://[^\s<>\"')]+)")

# User mention patterns: @username, /u/username, u/username
MENTION_PATTERNS = [
    re.compile(r"(?<!\w)@([a-zA-Z0-9_.-]{2,30})"),
    re.compile(r"(?<!\w)/?u/([a-zA-Z0-9_-]{2,30})", re.IGNORECASE),
]

# List item patterns: - item, * item, + item, 1. item
UNORDERED_LIST_PATTERN = re.compile(r"^[ \t]*[-*+][ \t]+(.+)$", re.MULTILINE)
ORDERED_LIST_PATTERN = re.compile(r"^[ \t]*\d+\.[ \t]+(.+)$", re.MULTILINE)

# Known deleted content signatures
DELETED_SIGNATURES = {
    "[deleted]",
    "[removed]",
    "[deleted by user]",
    "[removed by moderator]",
    "[unavailable]",
    "[deleted by author]",
}

# Known language hints
KNOWN_LANGUAGES = {
    "python", "py", "javascript", "js", "typescript", "ts", "jsx", "tsx",
    "rust", "rs", "go", "golang", "c", "cpp", "c++", "csharp", "c#", "cs",
    "java", "kotlin", "kt", "scala", "swift", "ruby", "rb", "php", "sql",
    "html", "css", "scss", "sass", "json", "yaml", "yml", "toml", "xml",
    "markdown", "md", "bash", "sh", "zsh", "shell", "powershell", "ps1",
    "dockerfile", "makefile", "graphql", "protobuf", "lua", "r", "zig",
}


# -----------------------------------------------------------------------------
# Auxiliary Extraction Domain Models
# -----------------------------------------------------------------------------

@dataclass(frozen=True)
class DiscussionLink:
    """
    Hyperlink reference extracted from discussion body.
    Categorizes internal vs external target without following or fetching the link.
    """
    url: str
    text: str = ""
    is_external: bool = True
    platform: Optional[CommunityPlatform] = None
    normalized_url: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "url": self.url,
            "text": self.text,
            "is_external": self.is_external,
            "platform": self.platform.value if self.platform else None,
            "normalized_url": self.normalized_url,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> DiscussionLink:
        plat_raw = data.get("platform")
        platform = CommunityPlatform.from_string(plat_raw) if plat_raw else None
        return cls(
            url=data.get("url", ""),
            text=data.get("text", ""),
            is_external=bool(data.get("is_external", True)),
            platform=platform,
            normalized_url=data.get("normalized_url", ""),
        )


@dataclass(frozen=True)
class DiscussionCodeBlock:
    """
    Code block extracted verbatim from discussion text.
    Never executed, evaluated, or compiled.
    """
    code: str
    language: Optional[str] = None
    line_count: int = 1

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "language": self.language,
            "line_count": self.line_count,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> DiscussionCodeBlock:
        return cls(
            code=data.get("code", ""),
            language=data.get("language"),
            line_count=int(data.get("line_count", 1)),
        )


@dataclass(frozen=True)
class DiscussionQuote:
    """
    Quoted text block extracted from discussion reply.
    """
    text: str
    author: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "text": self.text,
            "author": self.author,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> DiscussionQuote:
        return cls(
            text=data.get("text", ""),
            author=data.get("author"),
        )


# -----------------------------------------------------------------------------
# Structured Post & Discussion Containers
# -----------------------------------------------------------------------------

@dataclass
class StructuredDiscussionPost:
    """
    Normalized, structured representation of an individual post or comment.
    """
    post_id: str
    discussion_id: str
    parent_id: Optional[str] = None
    depth: int = 0
    author_id: Optional[str] = None
    created_at: str = field(default_factory=utc_now)
    updated_at: Optional[str] = None
    permalink: Optional[str] = None
    raw_content: str = ""
    normalized_text: str = ""
    paragraphs: list[str] = field(default_factory=list)
    headings: list[tuple[int, str]] = field(default_factory=list)
    lists: list[list[str]] = field(default_factory=list)
    quotes: list[DiscussionQuote] = field(default_factory=list)
    code_blocks: list[DiscussionCodeBlock] = field(default_factory=list)
    inline_code: list[str] = field(default_factory=list)
    links: list[DiscussionLink] = field(default_factory=list)
    mentions: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    engagement: EngagementMetrics = field(default_factory=EngagementMetrics)
    is_root: bool = False
    is_deleted: bool = False
    content_checksum: str = ""
    provenance: Optional[EvidenceProvenance] = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.content_checksum:
            self.content_checksum = compute_sha256(self.normalized_text or self.raw_content)

    def to_dict(self) -> dict[str, Any]:
        return {
            "post_id": self.post_id,
            "discussion_id": self.discussion_id,
            "parent_id": self.parent_id,
            "depth": self.depth,
            "author_id": self.author_id,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "permalink": self.permalink,
            "raw_content": self.raw_content,
            "normalized_text": self.normalized_text,
            "paragraphs": list(self.paragraphs),
            "headings": [list(h) for h in self.headings],
            "lists": [list(l) for l in self.lists],
            "quotes": [q.to_dict() for q in self.quotes],
            "code_blocks": [c.to_dict() for c in self.code_blocks],
            "inline_code": list(self.inline_code),
            "links": [l.to_dict() for l in self.links],
            "mentions": list(self.mentions),
            "tags": list(self.tags),
            "engagement": self.engagement.to_dict() if self.engagement else None,
            "is_root": self.is_root,
            "is_deleted": self.is_deleted,
            "content_checksum": self.content_checksum,
            "provenance": self.provenance.to_dict() if self.provenance else None,
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> StructuredDiscussionPost:
        eng_data = data.get("engagement")
        engagement = EngagementMetrics.from_dict(eng_data) if isinstance(eng_data, dict) else EngagementMetrics()
        prov_data = data.get("provenance")
        provenance = EvidenceProvenance.from_dict(prov_data) if isinstance(prov_data, dict) else None

        headings = [tuple(h) for h in data.get("headings", [])]
        quotes = [DiscussionQuote.from_dict(q) for q in data.get("quotes", [])]
        code_blocks = [DiscussionCodeBlock.from_dict(c) for c in data.get("code_blocks", [])]
        links = [DiscussionLink.from_dict(l) for l in data.get("links", [])]

        return cls(
            post_id=data.get("post_id", ""),
            discussion_id=data.get("discussion_id", ""),
            parent_id=data.get("parent_id"),
            depth=int(data.get("depth", 0)),
            author_id=data.get("author_id"),
            created_at=data.get("created_at", utc_now()),
            updated_at=data.get("updated_at"),
            permalink=data.get("permalink"),
            raw_content=data.get("raw_content", ""),
            normalized_text=data.get("normalized_text", ""),
            paragraphs=list(data.get("paragraphs", [])),
            headings=headings,
            lists=list(data.get("lists", [])),
            quotes=quotes,
            code_blocks=code_blocks,
            inline_code=list(data.get("inline_code", [])),
            links=links,
            mentions=list(data.get("mentions", [])),
            tags=list(data.get("tags", [])),
            engagement=engagement,
            is_root=bool(data.get("is_root", False)),
            is_deleted=bool(data.get("is_deleted", False)),
            content_checksum=data.get("content_checksum", ""),
            provenance=provenance,
            metadata=dict(data.get("metadata", {})),
        )


@dataclass
class StructuredDiscussion:
    """
    Root container for a fully structured, normalized discussion thread.
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
    root_post: Optional[StructuredDiscussionPost] = None
    posts: dict[str, StructuredDiscussionPost] = field(default_factory=dict)
    thread_structure: ThreadStructure = field(default_factory=ThreadStructure)
    total_code_blocks: int = 0
    total_links: int = 0
    total_quotes: int = 0
    provenance: Optional[EvidenceProvenance] = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def get_post(self, post_id: str) -> Optional[StructuredDiscussionPost]:
        """Retrieve structured post by post ID."""
        return self.posts.get(post_id)

    def total_posts(self) -> int:
        """Return total count of posts in structured discussion."""
        return len(self.posts) or self.thread_structure.total_posts()

    def add_post(self, post: Any) -> None:
        """Add post to structured discussion and update thread structure."""
        from core.research.community.models import DiscussionPost
        if isinstance(post, DiscussionPost):
            self.thread_structure.add_post(post)
            struct_post = StructuredDiscussionPost(
                post_id=post.post_id,
                discussion_id=post.discussion_id,
                author_id=post.author_id,
                raw_content=post.content,
                normalized_text=post.content,
                parent_id=post.parent_id,
                depth=post.depth,
                is_root=post.is_root,
                engagement=post.engagement,
                content_checksum=post.content_checksum,
                created_at=post.created_at,
            )
            self.posts[post.post_id] = struct_post
        elif isinstance(post, StructuredDiscussionPost):
            self.posts[post.post_id] = post

    def to_discussion_source_materials(self) -> list[DiscussionSourceMaterial]:
        """
        Convert structured posts into canonical DiscussionSourceMaterial instances for evidence synthesis.
        """
        materials: list[DiscussionSourceMaterial] = []
        for post in self.posts.values():
            mat = DiscussionSourceMaterial(
                material_id=f"mat-{self.discussion_id}-{post.post_id}",
                discussion_id=self.discussion_id,
                community_context=self.community_context,
                post_id=post.post_id,
                parent_id=post.parent_id,
                title=self.title,
                content=post.normalized_text or post.raw_content,
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
                metadata={
                    **self.metadata,
                    **post.metadata,
                    "code_blocks_count": len(post.code_blocks),
                    "links_count": len(post.links),
                    "quotes_count": len(post.quotes),
                    "is_deleted": post.is_deleted,
                },
            )
            materials.append(mat)
        return materials

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
            "posts": {pid: p.to_dict() for pid, p in self.posts.items()},
            "thread_structure": self.thread_structure.to_dict() if self.thread_structure else None,
            "total_code_blocks": self.total_code_blocks,
            "total_links": self.total_links,
            "total_quotes": self.total_quotes,
            "provenance": self.provenance.to_dict() if self.provenance else None,
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> StructuredDiscussion:
        comm_ctx = CommunityContext.from_dict(data["community_context"])
        eng_data = data.get("engagement")
        engagement = EngagementMetrics.from_dict(eng_data) if isinstance(eng_data, dict) else EngagementMetrics()
        root_data = data.get("root_post")
        root_post = StructuredDiscussionPost.from_dict(root_data) if isinstance(root_data, dict) else None

        raw_posts = data.get("posts", {})
        posts = {pid: StructuredDiscussionPost.from_dict(p) for pid, p in raw_posts.items() if isinstance(p, dict)}
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
            posts=posts,
            thread_structure=thread_structure,
            total_code_blocks=int(data.get("total_code_blocks", 0)),
            total_links=int(data.get("total_links", 0)),
            total_quotes=int(data.get("total_quotes", 0)),
            provenance=provenance,
            metadata=dict(data.get("metadata", {})),
        )


# -----------------------------------------------------------------------------
# Discussion Content Extractor Implementation
# -----------------------------------------------------------------------------

class DiscussionContentExtractor:
    """
    Deterministic structural content extractor for discussion posts and comment trees.
    Deconstructs Markdown/HTML text bodies into clean, attributable components.
    """

    @classmethod
    def sanitize_text(cls, text: str) -> str:
        """
        Sanitize control characters while preserving valid Unicode and formatting.
        Decodes HTML entities cleanly.
        """
        if not text:
            return ""
        # Remove ASCII control characters (keep \t, \n, \r)
        cleaned = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f]", "", str(text))
        # Unescape standard HTML entities (&amp; -> &, etc.)
        unescaped = html.unescape(cleaned)
        # Normalize carriage returns
        normalized = unescaped.replace("\r\n", "\n").replace("\r", "\n")
        return normalized

    @classmethod
    def extract_links(
        cls,
        text: str,
        community_context: Optional[CommunityContext] = None,
    ) -> list[DiscussionLink]:
        """
        Extract all markdown and raw hyperlinks, classifying internal vs external targets.
        """
        links: list[DiscussionLink] = []
        seen_urls: set[str] = set()

        base_domain = ""
        if community_context and community_context.source_url:
            base_domain = extract_domain(community_context.source_url)

        # 1. Extract markdown links [text](url)
        for match in MARKDOWN_LINK_PATTERN.finditer(text):
            link_text = match.group(1).strip()
            raw_url = match.group(2).strip()
            if not raw_url or raw_url in seen_urls:
                continue
            seen_urls.add(raw_url)

            is_ext = True
            if raw_url.startswith("/") or raw_url.startswith("#"):
                is_ext = False
            elif base_domain:
                link_domain = extract_domain(raw_url)
                if link_domain == base_domain or link_domain.endswith("." + base_domain):
                    is_ext = False

            links.append(DiscussionLink(
                url=raw_url,
                text=link_text,
                is_external=is_ext,
                platform=community_context.platform if community_context else None,
                normalized_url=normalize_url(raw_url) if "://" in raw_url else raw_url,
            ))

        # 2. Extract raw URLs
        for match in RAW_URL_PATTERN.finditer(text):
            raw_url = match.group(1).strip()
            if not raw_url or raw_url in seen_urls:
                continue
            seen_urls.add(raw_url)

            is_ext = True
            if base_domain:
                link_domain = extract_domain(raw_url)
                if link_domain == base_domain or link_domain.endswith("." + base_domain):
                    is_ext = False

            links.append(DiscussionLink(
                url=raw_url,
                text="",
                is_external=is_ext,
                platform=community_context.platform if community_context else None,
                normalized_url=normalize_url(raw_url),
            ))

        return links

    @classmethod
    def extract_code_blocks(cls, text: str) -> list[DiscussionCodeBlock]:
        """
        Extract fenced code blocks and detect syntax language.
        """
        code_blocks: list[DiscussionCodeBlock] = []
        for match in FENCED_CODE_BLOCK_PATTERN.finditer(text):
            raw_lang = match.group(1).strip().lower()
            code_content = match.group(2).strip()

            lang = None
            if raw_lang:
                lang = raw_lang if raw_lang in KNOWN_LANGUAGES else raw_lang
            elif code_content.startswith("#!/") or code_content.startswith("import ") or "def " in code_content:
                lang = "python"

            lines = len(code_content.splitlines()) if code_content else 1
            code_blocks.append(DiscussionCodeBlock(
                code=code_content,
                language=lang,
                line_count=lines,
            ))
        return code_blocks

    @classmethod
    def _build_quote(cls, quote_lines: list[str]) -> DiscussionQuote:
        full_quote = " ".join(quote_lines).strip()
        author = None
        if ":" in full_quote:
            prefix, rest = full_quote.split(":", 1)
            if len(prefix) < 30 and ("wrote" in prefix.lower() or prefix.startswith("@")):
                author = prefix.replace("wrote", "").replace("@", "").strip()
                full_quote = rest.strip()
        return DiscussionQuote(text=full_quote, author=author)

    @classmethod
    def extract_quotes(cls, text: str) -> list[DiscussionQuote]:
        """
        Extract blockquoted text lines.
        """
        quotes: list[DiscussionQuote] = []
        quote_lines: list[str] = []

        for line in text.splitlines():
            line_str = line.strip()
            if line_str.startswith(">"):
                quote_text = line_str.lstrip(">").strip()
                if quote_text:
                    quote_lines.append(quote_text)
            else:
                if quote_lines:
                    quotes.append(cls._build_quote(quote_lines))
                    quote_lines = []

        if quote_lines:
            quotes.append(cls._build_quote(quote_lines))

        return quotes

    @classmethod
    def extract_mentions(cls, text: str) -> list[str]:
        """
        Extract user handle mentions (@user, /u/user).
        """
        mentions: set[str] = set()
        for pattern in MENTION_PATTERNS:
            for match in pattern.finditer(text):
                user_handle = match.group(1).strip()
                if user_handle and len(user_handle) <= 30:
                    mentions.add(user_handle)
        return sorted(list(mentions))

    @classmethod
    def extract_headings(cls, text: str) -> list[tuple[int, str]]:
        """
        Extract markdown headings and their levels (1-6).
        """
        headings: list[tuple[int, str]] = []
        for match in HEADING_PATTERN.finditer(text):
            level = len(match.group(1))
            heading_text = match.group(2).strip()
            headings.append((level, heading_text))
        return headings

    @classmethod
    def extract_lists(cls, text: str) -> list[list[str]]:
        """
        Extract ordered and unordered list groups.
        """
        list_groups: list[list[str]] = []
        current_group: list[str] = []

        for line in text.splitlines():
            stripped = line.strip()
            u_match = UNORDERED_LIST_PATTERN.match(line)
            o_match = ORDERED_LIST_PATTERN.match(line)

            if u_match:
                current_group.append(u_match.group(1).strip())
            elif o_match:
                current_group.append(o_match.group(1).strip())
            else:
                if current_group:
                    list_groups.append(list(current_group))
                    current_group = []

        if current_group:
            list_groups.append(list(current_group))

        return list_groups

    @classmethod
    def extract_post(
        cls,
        post: DiscussionPost,
        community_context: Optional[CommunityContext] = None,
    ) -> StructuredDiscussionPost:
        """
        Decompose a single DiscussionPost into a StructuredDiscussionPost.
        """
        raw_text = post.content or ""
        sanitized = cls.sanitize_text(raw_text)

        # Check for deleted / tombstone status
        is_deleted = post.metadata.get("is_deleted", False)
        if not is_deleted:
            trimmed_lower = sanitized.strip().lower()
            if trimmed_lower in DELETED_SIGNATURES:
                is_deleted = True

        # Extract structural elements
        code_blocks = cls.extract_code_blocks(sanitized)
        quotes = cls.extract_quotes(sanitized)
        headings = cls.extract_headings(sanitized)
        lists = cls.extract_lists(sanitized)
        links = cls.extract_links(sanitized, community_context=community_context)
        mentions = cls.extract_mentions(sanitized)

        # Extract inline code snippets
        inline_code: list[str] = []
        # Mask fenced code blocks before matching inline code
        no_fenced = FENCED_CODE_BLOCK_PATTERN.sub("", sanitized)
        for m in INLINE_CODE_PATTERN.finditer(no_fenced):
            snippet = m.group(1).strip()
            if snippet:
                inline_code.append(snippet)

        # Extract normalized paragraphs (non-empty blocks separated by double newlines)
        # Mask fenced blocks and quotes from plain paragraph text
        cleaned_prose = FENCED_CODE_BLOCK_PATTERN.sub("", sanitized)
        cleaned_prose = re.sub(r"^>.*$", "", cleaned_prose, flags=re.MULTILINE)
        raw_paragraphs = [p.strip() for p in re.split(r"\n\s*\n", cleaned_prose) if p.strip()]
        paragraphs = [p for p in raw_paragraphs if not p.startswith("#")]

        # Normalized clean plain text
        normalized_text = "\n\n".join(paragraphs) if paragraphs else sanitized.strip()
        checksum = compute_sha256(sanitized)

        return StructuredDiscussionPost(
            post_id=post.post_id,
            discussion_id=post.discussion_id,
            parent_id=post.parent_id,
            depth=post.depth,
            author_id=post.author_id,
            created_at=post.created_at,
            updated_at=post.updated_at,
            permalink=post.permalink,
            raw_content=raw_text,
            normalized_text=normalized_text,
            paragraphs=paragraphs,
            headings=headings,
            lists=lists,
            quotes=quotes,
            code_blocks=code_blocks,
            inline_code=inline_code,
            links=links,
            mentions=mentions,
            tags=list(post.metadata.get("tags", [])),
            engagement=post.engagement,
            is_root=post.is_root,
            is_deleted=is_deleted,
            content_checksum=checksum,
            provenance=post.provenance,
            metadata=dict(post.metadata),
        )

    @classmethod
    def extract_discussion(cls, discussion: Discussion) -> StructuredDiscussion:
        """
        Deconstruct a Discussion aggregate into a StructuredDiscussion.
        """
        structured_posts: dict[str, StructuredDiscussionPost] = {}
        structured_root: Optional[StructuredDiscussionPost] = None

        total_code_blocks = 0
        total_links = 0
        total_quotes = 0

        # Extract all posts in thread structure
        for post in discussion.thread_structure.get_all_posts():
            s_post = cls.extract_post(post, community_context=discussion.community_context)
            structured_posts[s_post.post_id] = s_post
            total_code_blocks += len(s_post.code_blocks)
            total_links += len(s_post.links)
            total_quotes += len(s_post.quotes)

            if s_post.is_root or s_post.post_id == discussion.thread_structure.root_post_id:
                structured_root = s_post

        return StructuredDiscussion(
            discussion_id=discussion.discussion_id,
            community_context=discussion.community_context,
            title=discussion.title,
            url=discussion.url,
            author_id=discussion.author_id,
            created_at=discussion.created_at,
            updated_at=discussion.updated_at,
            status=discussion.status,
            tags=list(discussion.tags),
            engagement=discussion.engagement,
            root_post=structured_root,
            posts=structured_posts,
            thread_structure=discussion.thread_structure,
            total_code_blocks=total_code_blocks,
            total_links=total_links,
            total_quotes=total_quotes,
            provenance=discussion.provenance,
            metadata=dict(discussion.metadata),
        )

    @classmethod
    def extract_thread(cls, retrieved_thread: RetrievedDiscussionThread) -> StructuredDiscussion:
        """
        Deconstruct a RetrievedDiscussionThread outcome into a StructuredDiscussion.
        """
        struct_disc = cls.extract_discussion(retrieved_thread.discussion)
        struct_disc.metadata["is_partial"] = retrieved_thread.is_partial
        struct_disc.metadata["partial_reasons"] = list(retrieved_thread.partial_reasons)
        struct_disc.metadata["bytes_retrieved"] = retrieved_thread.bytes_retrieved
        struct_disc.metadata["orphans_count"] = retrieved_thread.orphans_count
        return struct_disc
