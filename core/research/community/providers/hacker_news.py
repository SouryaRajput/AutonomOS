"""
HackerNewsDiscussionProvider — Live Hacker News Discussion Adapter.

Uses the official Algolia Hacker News Search API (https://hn.algolia.com/api/v1),
which is 100% public, free, keyless, and returns full nested comment trees.

Guarantees:
1. Zero external dependencies (urllib.request + json, stdlib only).
2. Zero API keys required.
3. Full recursive comment tree extraction from /items/{id}.
4. SSRF boundary protection on all outbound HTTP.
5. Structured error taxonomy mapped to CommunityError hierarchy.
"""
from __future__ import annotations

import datetime
import json
import logging
import re
import socket
import time
from typing import Any, Callable, Optional
import urllib.error
import urllib.parse
import urllib.request

from core.research.community.models import (
    AccessStatus,
    CommunityContext,
    CommunityPlatform,
    Discussion,
    DiscussionPost,
    DiscussionStatus,
    EngagementMetrics,
    ThreadStructure,
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
    CommunityDiscussionNotFoundError,
    CommunityProviderError,
    CommunityRateLimitError,
    CommunityTimeoutError,
)
from core.research.search.security import (
    sanitize_error,
    validate_network_target,
)

logger = logging.getLogger("AutonomOS.Research.Community.HackerNews")

HN_ALGOLIA_BASE_URL = "https://hn.algolia.com/api/v1"
HN_SITE_URL = "https://news.ycombinator.com"
HN_ITEM_URL = "https://news.ycombinator.com/item?id={id}"
HN_USER_AGENT = "AutonomOS-Researcher/1.0 (HackerNews Discussion Research)"

_HTML_TAG_RE = re.compile(r"<[^>]+>")
_WHITESPACE_RE = re.compile(r"\s+")


def _strip_html(raw: Optional[str]) -> str:
    """Strip HTML tags from HN comment bodies (which use HTML encoding)."""
    if not raw:
        return ""
    text = _HTML_TAG_RE.sub(" ", raw)
    text = (
        text.replace("&gt;", ">")
        .replace("&lt;", "<")
        .replace("&amp;", "&")
        .replace("&quot;", '"')
        .replace("&#x27;", "'")
    )
    return _WHITESPACE_RE.sub(" ", text).strip()


def _unix_to_iso(unix_ts: Any) -> Optional[str]:
    """Convert unix timestamp int to ISO 8601 string."""
    if isinstance(unix_ts, (int, float)) and unix_ts > 0:
        try:
            return datetime.datetime.fromtimestamp(
                unix_ts, tz=datetime.timezone.utc
            ).isoformat()
        except Exception:
            pass
    if isinstance(unix_ts, str) and unix_ts:
        return unix_ts
    return None


def _build_permalink(item_id: Any) -> str:
    """Build canonical HN item permalink."""
    return HN_ITEM_URL.format(id=item_id) if item_id else ""


def _make_community_context(story_id: str = "", source_url: str = "") -> CommunityContext:
    """Build a CommunityContext for Hacker News."""
    return CommunityContext(
        platform=CommunityPlatform.HACKER_NEWS,
        community_id="news.ycombinator.com",
        community_name="Hacker News",
        source_url=source_url or HN_SITE_URL,
        access_status=AccessStatus.PUBLIC,
    )


class HackerNewsDiscussionProvider(DiscussionProvider):
    """
    Live Hacker News Discussion Provider using the official Algolia HN Search API.

    Implements full search, thread retrieval, nested comment extraction, and subtree
    deep-dive expansion — all keyless and with zero external dependencies.
    """

    def __init__(
        self,
        base_url: Optional[str] = None,
        limits: Optional[DiscussionFetchLimits] = None,
        transport_fn: Optional[Callable[[urllib.request.Request, float], bytes]] = None,
        allow_localhost: bool = False,
    ):
        super().__init__(
            provider_id="hacker_news",
            platform=CommunityPlatform.HACKER_NEWS,
            name="Hacker News Algolia API Provider",
            limits=limits or DiscussionFetchLimits(),
        )
        self._base_url = (base_url or HN_ALGOLIA_BASE_URL).rstrip("/")
        self._transport_fn = transport_fn
        self._allow_localhost = allow_localhost

    # ------------------------------------------------------------------
    # HTTP helper
    # ------------------------------------------------------------------

    def _http_get_json(
        self,
        url: str,
        timeout_seconds: Optional[float] = None,
        is_cancelled: Optional[Callable[[], bool]] = None,
    ) -> Any:
        """Fetch and decode JSON from the Algolia HN API with SSRF protection."""
        self.check_cancellation(is_cancelled, target=url, operation="http_get_json")
        validate_network_target(url, allow_localhost=self._allow_localhost)

        timeout = timeout_seconds if timeout_seconds is not None else self.limits.timeout_seconds
        req = urllib.request.Request(
            url=url,
            headers={
                "User-Agent": HN_USER_AGENT,
                "Accept": "application/json",
            },
            method="GET",
        )

        try:
            if self._transport_fn is not None:
                raw_bytes = self._transport_fn(req, timeout)
            else:
                with urllib.request.urlopen(req, timeout=timeout) as resp:
                    raw_bytes = resp.read()

        except urllib.error.HTTPError as err:
            err_body = ""
            try:
                err_body = err.read().decode("utf-8", errors="replace")
            except Exception:
                pass
            if err.code == 404:
                raise CommunityDiscussionNotFoundError(
                    discussion_id=url,
                    message=f"HN Algolia resource not found at {url}",
                ) from err
            if err.code == 429:
                raise CommunityRateLimitError(
                    provider_id=self.provider_id,
                    retry_after_seconds=10.0,
                ) from err
            raise CommunityProviderError(
                f"HN Algolia API HTTP {err.code}: {err_body[:200]}"
            ) from err

        except (urllib.error.URLError, socket.timeout, TimeoutError) as err:
            reason = str(err)
            if isinstance(err, socket.timeout) or "timed out" in reason.lower():
                raise CommunityTimeoutError(
                    target=url,
                    operation="http_get_json",
                    timeout_seconds=timeout,
                ) from err
            raise CommunityProviderError(
                f"HN Algolia network error: {sanitize_error(err)}"
            ) from err

        try:
            return json.loads(raw_bytes.decode("utf-8"))
        except Exception as err:
            raise CommunityProviderError(
                f"Invalid JSON from HN Algolia API: {sanitize_error(err)}"
            ) from err

    # ------------------------------------------------------------------
    # Parsing helpers
    # ------------------------------------------------------------------

    def _parse_hit_to_discussion(self, hit: dict[str, Any]) -> Optional[Discussion]:
        """Convert an Algolia search hit into a Discussion stub."""
        obj_id = hit.get("objectID") or hit.get("story_id")
        title = (hit.get("title") or hit.get("story_title") or "").strip()
        if not obj_id or not title:
            return None

        author = hit.get("author") or "unknown"
        points = int(hit.get("points") or 0)
        num_comments = int(hit.get("num_comments") or 0)
        created_unix = hit.get("created_at_i")
        created_iso = _unix_to_iso(created_unix) or utc_now()
        story_url = hit.get("url") or _build_permalink(obj_id)
        permalink = _build_permalink(obj_id)
        ctx = _make_community_context(story_id=str(obj_id), source_url=permalink)

        root_post = DiscussionPost(
            post_id=str(obj_id),
            discussion_id=str(obj_id),
            content=_strip_html(hit.get("story_text") or hit.get("text") or ""),
            parent_id=None,
            author_id=author,
            created_at=created_iso,
            depth=0,
            permalink=permalink,
            is_root=True,
            engagement=EngagementMetrics(
                upvotes=points,
                reply_count=num_comments,
            ),
        )

        return Discussion(
            discussion_id=str(obj_id),
            community_context=ctx,
            title=title,
            url=story_url,
            author_id=author,
            created_at=created_iso,
            status=DiscussionStatus.OPEN,
            root_post=root_post,
            metadata={
                "permalink": permalink,
                "points": points,
                "num_comments": num_comments,
                "platform": CommunityPlatform.HACKER_NEWS.value,
            },
        )

    def _parse_item_recursive(
        self,
        node: dict[str, Any],
        discussion_id: str,
        parent_id: Optional[str],
        depth: int,
        max_depth: int,
        max_comments: int,
        collected: list[DiscussionPost],
    ) -> None:
        """Recursively parse an Algolia /items/{id} node into DiscussionPosts."""
        if depth > max_depth or len(collected) >= max_comments:
            return

        item_type = node.get("type", "")
        node_id = str(node.get("id", ""))
        if not node_id:
            return

        author = node.get("author") or "unknown"
        text = _strip_html(node.get("text") or "")
        created_unix = node.get("created_at_i")
        created_iso = _unix_to_iso(created_unix) or utc_now()
        points = int(node.get("points") or 0)
        permalink = _build_permalink(node_id)
        is_deleted = bool(node.get("deleted") or node.get("dead"))

        if item_type in ("comment", "story", "ask_hn", "show_hn", "job") and depth > 0:
            post = DiscussionPost(
                post_id=node_id,
                discussion_id=discussion_id,
                content=text if not is_deleted else "[deleted]",
                parent_id=parent_id,
                author_id=author if not is_deleted else "[deleted]",
                created_at=created_iso,
                depth=depth,
                permalink=permalink,
                engagement=EngagementMetrics(upvotes=points),
                metadata={"deleted": is_deleted, "type": item_type},
            )
            collected.append(post)

        for child in node.get("children", []):
            if len(collected) >= max_comments:
                break
            if isinstance(child, dict):
                self._parse_item_recursive(
                    child,
                    discussion_id=discussion_id,
                    parent_id=node_id,
                    depth=depth + 1,
                    max_depth=max_depth,
                    max_comments=max_comments,
                    collected=collected,
                )

    # ------------------------------------------------------------------
    # DiscussionProvider interface
    # ------------------------------------------------------------------

    def search_discussions(
        self,
        params: DiscussionSearchParams,
        is_cancelled: Optional[Callable[[], bool]] = None,
    ) -> DiscussionSearchResponse:
        """Search Hacker News via Algolia /search endpoint."""
        start = time.perf_counter()
        limit = min(params.limit, self.limits.max_search_results)
        timeout = params.timeout_seconds or self.limits.timeout_seconds

        encoded_query = urllib.parse.quote(params.query.strip(), safe="")
        url = f"{self._base_url}/search?query={encoded_query}&tags=story&hitsPerPage={limit}"

        if params.after_date:
            try:
                dt = datetime.datetime.fromisoformat(params.after_date)
                url += f"&numericFilters=created_at_i>{int(dt.timestamp())}"
            except ValueError:
                pass

        data = self._http_get_json(url, timeout_seconds=timeout, is_cancelled=is_cancelled)
        hits = data.get("hits") or []

        results: list[Discussion] = []
        for hit in hits[:limit]:
            if not isinstance(hit, dict):
                continue
            disc = self._parse_hit_to_discussion(hit)
            if disc:
                results.append(disc)

        elapsed = round(time.perf_counter() - start, 4)
        return DiscussionSearchResponse(
            query=params.query,
            results=results,
            total_found=int(data.get("nbHits", len(results))),
            execution_time_seconds=elapsed,
            provider=self.provider_id,
            platform=CommunityPlatform.HACKER_NEWS,
        )

    def get_discussion(
        self,
        params: DiscussionRetrievalParams,
        is_cancelled: Optional[Callable[[], bool]] = None,
    ) -> Discussion:
        """Retrieve a full HN story thread with nested comments via /items/{id}."""
        timeout = params.timeout_seconds or self.limits.timeout_seconds
        max_comments = min(
            params.max_comments or self.limits.max_posts_per_discussion,
            self.limits.max_posts_per_discussion,
        )
        max_depth = min(
            params.max_depth or self.limits.max_depth,
            self.limits.max_depth,
        )

        url = f"{self._base_url}/items/{urllib.parse.quote(str(params.discussion_id))}"
        data = self._http_get_json(url, timeout_seconds=timeout, is_cancelled=is_cancelled)

        if not data or not data.get("id"):
            raise CommunityDiscussionNotFoundError(
                discussion_id=params.discussion_id,
                message=f"HN item '{params.discussion_id}' returned empty response.",
            )

        item_id = str(data["id"])
        title = (data.get("title") or "").strip() or f"HN Item {item_id}"
        author = data.get("author") or "unknown"
        points = int(data.get("points") or 0)
        created_iso = _unix_to_iso(data.get("created_at_i")) or utc_now()
        story_url = data.get("url") or _build_permalink(item_id)
        permalink = _build_permalink(item_id)
        story_text = _strip_html(data.get("text") or "")
        ctx = _make_community_context(story_id=item_id, source_url=permalink)

        root_post = DiscussionPost(
            post_id=item_id,
            discussion_id=item_id,
            content=story_text,
            parent_id=None,
            author_id=author,
            created_at=created_iso,
            depth=0,
            permalink=permalink,
            is_root=True,
            engagement=EngagementMetrics(
                upvotes=points,
                reply_count=len(data.get("children", [])),
            ),
        )

        comment_posts: list[DiscussionPost] = []
        for child in data.get("children", []):
            if len(comment_posts) >= max_comments:
                break
            if isinstance(child, dict):
                self._parse_item_recursive(
                    child,
                    discussion_id=item_id,
                    parent_id=item_id,
                    depth=1,
                    max_depth=max_depth,
                    max_comments=max_comments,
                    collected=comment_posts,
                )

        all_posts = [root_post] + comment_posts
        thread = ThreadStructure(root_post_id=item_id)
        for p in all_posts:
            thread.add_post(p)

        tags = data.get("_tags") or []
        is_partial = len(comment_posts) >= max_comments

        return Discussion(
            discussion_id=item_id,
            community_context=ctx,
            title=title,
            url=story_url,
            author_id=author,
            created_at=created_iso,
            status=DiscussionStatus.OPEN,
            tags=tags,
            root_post=root_post,
            thread_structure=thread,
            metadata={
                "permalink": permalink,
                "comment_count": len(comment_posts),
                "total_posts": len(all_posts),
                "is_partial": is_partial,
                "platform": CommunityPlatform.HACKER_NEWS.value,
                "posts": [p.post_id for p in all_posts],
            },
        )

    def get_comments(
        self,
        params: DiscussionCommentsParams,
        is_cancelled: Optional[Callable[[], bool]] = None,
    ) -> list[DiscussionPost]:
        """Retrieve comments for a discussion, optionally rooted at a parent."""
        timeout = params.timeout_seconds or self.limits.timeout_seconds
        max_comments = min(
            params.max_comments or self.limits.max_posts_per_discussion,
            self.limits.max_posts_per_discussion,
        )
        max_depth = min(
            params.max_depth or self.limits.max_depth,
            self.limits.max_depth,
        )

        root_id = params.parent_id or params.discussion_id
        url = f"{self._base_url}/items/{urllib.parse.quote(str(root_id))}"
        data = self._http_get_json(url, timeout_seconds=timeout, is_cancelled=is_cancelled)

        if not data:
            return []

        discussion_id = str(params.discussion_id)
        item_id = str(data.get("id") or root_id)
        collected: list[DiscussionPost] = []

        for child in data.get("children", []):
            if len(collected) >= max_comments:
                break
            if isinstance(child, dict):
                self._parse_item_recursive(
                    child,
                    discussion_id=discussion_id,
                    parent_id=item_id,
                    depth=1,
                    max_depth=max_depth,
                    max_comments=max_comments,
                    collected=collected,
                )

        return collected

    def retrieve_comment_subtree(
        self,
        discussion_id: str,
        root_comment_id: str,
        max_comments: Optional[int] = None,
        max_depth: Optional[int] = None,
        timeout_seconds: Optional[float] = None,
        is_cancelled: Optional[Callable[[], bool]] = None,
    ) -> list[DiscussionPost]:
        """Directly fetch and expand a specific comment subtree by comment ID."""
        timeout = timeout_seconds or self.limits.timeout_seconds
        max_c = min(
            max_comments or self.limits.max_posts_per_discussion,
            self.limits.max_posts_per_discussion,
        )
        max_d = min(
            max_depth or self.limits.max_depth,
            self.limits.max_depth,
        )

        url = f"{self._base_url}/items/{urllib.parse.quote(str(root_comment_id))}"
        data = self._http_get_json(url, timeout_seconds=timeout, is_cancelled=is_cancelled)
        if not data:
            return []

        node_id = str(data.get("id") or root_comment_id)
        author = data.get("author") or "unknown"
        text = _strip_html(data.get("text") or "")
        created_iso = _unix_to_iso(data.get("created_at_i")) or utc_now()

        root_comment = DiscussionPost(
            post_id=node_id,
            discussion_id=discussion_id,
            content=text,
            parent_id=None,
            author_id=author,
            created_at=created_iso,
            depth=0,
            permalink=_build_permalink(node_id),
        )
        collected: list[DiscussionPost] = [root_comment]

        for child in data.get("children", []):
            if len(collected) >= max_c:
                break
            if isinstance(child, dict):
                self._parse_item_recursive(
                    child,
                    discussion_id=discussion_id,
                    parent_id=node_id,
                    depth=1,
                    max_depth=max_d,
                    max_comments=max_c,
                    collected=collected,
                )

        return collected

    def get_community_metadata(
        self,
        community_id: str,
        platform: Optional[CommunityPlatform] = None,
        timeout_seconds: Optional[float] = None,
        is_cancelled: Optional[Callable[[], bool]] = None,
    ) -> CommunityContext:
        """Return metadata for Hacker News as a static community context."""
        return CommunityContext(
            platform=CommunityPlatform.HACKER_NEWS,
            community_id="news.ycombinator.com",
            community_name="Hacker News",
            source_url=HN_SITE_URL,
            access_status=AccessStatus.PUBLIC,
            metadata={
                "api": "Algolia HN Search API",
                "api_url": HN_ALGOLIA_BASE_URL,
                "requires_auth": False,
                "description": "Community for sharing and discussing technology, programming, and entrepreneurship news.",
            },
        )
