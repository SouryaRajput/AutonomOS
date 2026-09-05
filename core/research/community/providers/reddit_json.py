"""
Production Live Discussion Provider for Reddit (.json Public Web API).

Queries Reddit public discussions and nested comment trees directly over HTTPS
without requiring OAuth keys or developer portal approval.
Guarantees:
1. Zero external dependencies.
2. Respects Reddit User-Agent standards.
3. SSRF boundary protection.
4. Recursive comment tree extraction and depth parsing.
5. Deterministic taxonomy mapping of HTTP 403, 404, 429, and network errors.
"""
from __future__ import annotations

import datetime
import json
import logging
import os
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
    CommunityDiscussionNotFoundError,
    CommunityError,
    CommunityProviderError,
    CommunityRateLimitError,
    CommunityResourceLimitError,
    CommunityTimeoutError,
    CommunityValidationError,
)
from core.research.search.security import (
    sanitize_error,
    validate_network_target,
)

logger = logging.getLogger("AutonomOS.Research.Community.RedditJson")

DEFAULT_REDDIT_USER_AGENT = "AutonomOS-Researcher/1.0 (Discussion Research Client)"
REDDIT_BASE_URL = "https://www.reddit.com"


class RedditJsonProvider(DiscussionProvider):
    """
    Live Reddit Discussion Provider using Reddit's public JSON API endpoints.
    """

    def __init__(
        self,
        user_agent: Optional[str] = None,
        base_url: Optional[str] = None,
        limits: Optional[DiscussionFetchLimits] = None,
        transport_fn: Optional[Callable[[urllib.request.Request, float], bytes]] = None,
        allow_localhost: bool = False,
    ):
        super().__init__(
            provider_id="reddit_json",
            platform=CommunityPlatform.REDDIT,
            name="Reddit Public JSON API Provider",
            limits=limits or DiscussionFetchLimits(),
        )
        self._allow_localhost = allow_localhost
        raw_url = base_url or os.getenv("REDDIT_BASE_URL") or REDDIT_BASE_URL
        self._base_url = raw_url.rstrip("/")
        self._user_agent = (user_agent or os.getenv("REDDIT_USER_AGENT") or DEFAULT_REDDIT_USER_AGENT).strip()
        self._transport_fn = transport_fn

    @property
    def base_url(self) -> str:
        return self._base_url

    def _http_get_json(
        self,
        url: str,
        timeout_seconds: Optional[float] = None,
        is_cancelled: Optional[Callable[[], bool]] = None,
    ) -> Any:
        """
        Fetch JSON from a Reddit URL with SSRF protection and error taxonomy mapping.
        """
        self.check_cancellation(is_cancelled, target=url, operation="http_get_json")
        validate_network_target(url, allow_localhost=self._allow_localhost)

        timeout = timeout_seconds if timeout_seconds is not None else self.limits.timeout_seconds
        req = urllib.request.Request(
            url=url,
            headers={
                "User-Agent": self._user_agent,
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
        except urllib.error.HTTPError as http_err:
            status = http_err.code
            err_body = ""
            try:
                err_body = http_err.read().decode("utf-8", errors="replace")
            except Exception:
                pass

            if status in (401, 403):
                raise CommunityAuthenticationError(
                    target=self.provider_id,
                    message=f"Reddit community/discussion access restricted ({status}): {err_body[:200]}",
                ) from http_err
            elif status == 404:
                raise CommunityDiscussionNotFoundError(
                    discussion_id=url,
                    message=f"Reddit resource not found at URL {url} (404).",
                ) from http_err
            elif status == 429:
                raise CommunityRateLimitError(
                    provider_id=self.provider_id,
                    retry_after_seconds=60.0,
                ) from http_err
            else:
                raise CommunityProviderError(
                    f"Reddit HTTP {status} error: {err_body[:200]}"
                ) from http_err
        except (urllib.error.URLError, socket.timeout, TimeoutError) as net_err:
            if isinstance(net_err, socket.timeout) or "timed out" in str(net_err).lower():
                raise CommunityTimeoutError(
                    target=url,
                    operation="http_get_json",
                    timeout_seconds=timeout,
                ) from net_err
            raise CommunityProviderError(
                f"Reddit network transport error: {sanitize_error(net_err)}"
            ) from net_err

        try:
            return json.loads(raw_bytes.decode("utf-8"))
        except Exception as json_err:
            raise CommunityProviderError(
                f"Invalid JSON response from Reddit: {sanitize_error(json_err)}"
            ) from json_err

    def _format_timestamp(self, utc_val: Any) -> str:
        if isinstance(utc_val, (int, float)):
            try:
                dt = datetime.datetime.fromtimestamp(utc_val, tz=datetime.timezone.utc)
                return dt.strftime("%Y-%m-%dT%H:%M:%SZ")
            except Exception:
                return utc_now()
        return utc_now()

    def search_discussions(
        self,
        params: DiscussionSearchParams,
        is_cancelled: Optional[Callable[[], bool]] = None,
    ) -> DiscussionSearchResponse:
        start_time = time.monotonic()
        self.check_cancellation(is_cancelled, target=params.query, operation="search_discussions")

        limit = min(params.limit, self.limits.max_search_results)
        query_encoded = urllib.parse.quote(params.query.strip())

        if params.community_id:
            sub = params.community_id.replace("r/", "").replace("/", "").strip()
            search_url = f"{self._base_url}/r/{sub}/search.json?q={query_encoded}&restrict_sr=1&limit={limit}&sort=relevance"
        else:
            search_url = f"{self._base_url}/search.json?q={query_encoded}&limit={limit}&sort=relevance"

        payload = self._http_get_json(
            url=search_url,
            timeout_seconds=params.timeout_seconds,
            is_cancelled=is_cancelled,
        )

        results: list[Discussion] = []
        data_block = payload.get("data", {}) if isinstance(payload, dict) else {}
        children = data_block.get("children", [])

        for child in children:
            if not isinstance(child, dict) or child.get("kind") != "t3":
                continue
            item = child.get("data", {})
            post_id = str(item.get("id", ""))
            subreddit = str(item.get("subreddit", params.community_id or "reddit"))
            title = str(item.get("title", "")).strip()
            selftext = str(item.get("selftext", "") or "")
            author = str(item.get("author", "[deleted]"))
            permalink = str(item.get("permalink", ""))
            full_url = f"https://www.reddit.com{permalink}" if permalink.startswith("/") else permalink
            created_at = self._format_timestamp(item.get("created_utc"))
            score = int(item.get("ups", item.get("score", 0)))
            num_comments = int(item.get("num_comments", 0))
            is_pinned = bool(item.get("stickied", False))
            is_nsfw = bool(item.get("over_18", False))
            flair = item.get("link_flair_text")
            tags = [flair] if flair else []

            comm_ctx = CommunityContext(
                platform=CommunityPlatform.REDDIT,
                community_id=f"r/{subreddit}",
                community_name=f"r/{subreddit}",
                source_url=f"https://www.reddit.com/r/{subreddit}",
                category="Discussion",
                access_status=AccessStatus.PUBLIC if not is_nsfw else AccessStatus.RESTRICTED,
            )

            disc = Discussion(
                discussion_id=post_id,
                community_context=comm_ctx,
                title=title,
                url=full_url,
                author_id=author,
                created_at=created_at,
                updated_at=created_at,
                status=DiscussionStatus.OPEN,
                tags=tags,
                engagement=EngagementMetrics(
                    score=score,
                    upvotes=score,
                    reply_count=num_comments,
                    is_pinned=is_pinned,
                ),
                metadata={
                    "subreddit": subreddit,
                    "permalink": permalink,
                    "is_nsfw": is_nsfw,
                    "upvote_ratio": item.get("upvote_ratio", 1.0),
                },
            )
            root_post = DiscussionPost(
                post_id=f"root-{post_id}",
                discussion_id=post_id,
                content=selftext,
                author_id=author,
                created_at=created_at,
                updated_at=created_at,
                depth=0,
                is_root=True,
                engagement=EngagementMetrics(score=score, upvotes=score),
            )
            disc.add_post(root_post)
            results.append(disc)

        return DiscussionSearchResponse(
            query=params.query,
            results=results,
            total_found=len(results),
            execution_time_seconds=time.monotonic() - start_time,
            provider=self.provider_id,
            platform=self.platform,
            metadata={"search_url": search_url},
        )

    def _extract_comments_recursive(
        self,
        children: list[Any],
        discussion_id: str,
        parent_id: Optional[str],
        depth: int,
        max_depth: int,
        max_posts: int,
        target_discussion: Discussion,
    ) -> None:
        """Recursively traverse Reddit comment trees and attach to target discussion."""
        for child in children:
            if target_discussion.thread_structure.total_posts() >= max_posts:
                return
            if not isinstance(child, dict) or child.get("kind") != "t1":
                continue

            c_data = child.get("data", {})
            c_id = str(c_data.get("id", ""))
            c_body = str(c_data.get("body", "") or "")
            c_author = str(c_data.get("author", "[deleted]"))
            c_created = self._format_timestamp(c_data.get("created_utc"))
            c_score = int(c_data.get("ups", c_data.get("score", 0)))
            c_parent_raw = str(c_data.get("parent_id", ""))
            
            norm_parent = parent_id
            if not norm_parent and c_parent_raw.startswith("t1_"):
                norm_parent = c_parent_raw[3:]

            post = DiscussionPost(
                post_id=c_id,
                discussion_id=discussion_id,
                content=c_body,
                parent_id=norm_parent or f"root-{discussion_id}",
                author_id=c_author,
                created_at=c_created,
                updated_at=c_created,
                depth=depth,
                is_root=False,
                engagement=EngagementMetrics(score=c_score, upvotes=c_score),
                metadata={"is_submitter": bool(c_data.get("is_submitter", False))},
            )
            target_discussion.add_post(post)

            if depth < max_depth:
                replies_raw = c_data.get("replies")
                if isinstance(replies_raw, dict):
                    rep_children = replies_raw.get("data", {}).get("children", [])
                    self._extract_comments_recursive(
                        children=rep_children,
                        discussion_id=discussion_id,
                        parent_id=c_id,
                        depth=depth + 1,
                        max_depth=max_depth,
                        max_posts=max_posts,
                        target_discussion=target_discussion,
                    )

    def get_discussion(
        self,
        params: DiscussionRetrievalParams,
        is_cancelled: Optional[Callable[[], bool]] = None,
    ) -> Discussion:
        self.check_cancellation(is_cancelled, target=params.discussion_id, operation="get_discussion")

        disc_id = params.discussion_id.strip()
        if disc_id.startswith("t3_"):
            disc_id = disc_id[3:]

        max_comments = min(params.max_comments or 50, self.limits.max_posts_per_discussion)
        max_depth = min(params.max_depth or 10, self.limits.max_depth)

        if "reddit.com" in disc_id:
            url = disc_id if disc_id.endswith(".json") else f"{disc_id.rstrip('/')}.json"
        elif params.community_id:
            sub = params.community_id.replace("r/", "").replace("/", "").strip()
            url = f"{self._base_url}/r/{sub}/comments/{disc_id}.json?limit={max_comments}&depth={max_depth}"
        else:
            url = f"{self._base_url}/comments/{disc_id}.json?limit={max_comments}&depth={max_depth}"

        payload = self._http_get_json(url=url, timeout_seconds=params.timeout_seconds, is_cancelled=is_cancelled)

        if not isinstance(payload, list) or len(payload) == 0:
            raise CommunityDiscussionNotFoundError(
                discussion_id=disc_id,
                message=f"Reddit discussion '{disc_id}' returned empty or unexpected structure.",
            )

        post_listing = payload[0].get("data", {}).get("children", [])
        if not post_listing:
            raise CommunityDiscussionNotFoundError(
                discussion_id=disc_id,
                message=f"Reddit discussion '{disc_id}' not found.",
            )

        root_item = post_listing[0].get("data", {})
        clean_id = str(root_item.get("id", disc_id))
        subreddit = str(root_item.get("subreddit", params.community_id or "reddit"))
        community = f"r/{subreddit}"
        title = str(root_item.get("title", "")).strip()
        selftext = str(root_item.get("selftext", "") or "")
        author = str(root_item.get("author", "[deleted]"))
        permalink = str(root_item.get("permalink", ""))
        full_url = f"https://www.reddit.com{permalink}" if permalink.startswith("/") else permalink
        created_at = self._format_timestamp(root_item.get("created_utc"))
        score = int(root_item.get("ups", root_item.get("score", 0)))
        num_comments = int(root_item.get("num_comments", 0))
        is_pinned = bool(root_item.get("stickied", False))
        is_nsfw = bool(root_item.get("over_18", False))
        flair = root_item.get("link_flair_text")
        tags = [flair] if flair else []

        comm_ctx = CommunityContext(
            platform=CommunityPlatform.REDDIT,
            community_id=community,
            community_name=community,
            source_url=f"https://www.reddit.com/{community}",
            category="Discussion",
            access_status=AccessStatus.PUBLIC if not is_nsfw else AccessStatus.RESTRICTED,
        )

        disc = Discussion(
            discussion_id=clean_id,
            community_context=comm_ctx,
            title=title,
            url=full_url,
            author_id=author,
            created_at=created_at,
            updated_at=created_at,
            status=DiscussionStatus.OPEN,
            tags=tags,
            engagement=EngagementMetrics(
                score=score,
                upvotes=score,
                reply_count=num_comments,
                is_pinned=is_pinned,
            ),
            metadata={
                "subreddit": subreddit,
                "permalink": permalink,
                "is_nsfw": is_nsfw,
            },
        )

        root_post = DiscussionPost(
            post_id=f"root-{clean_id}",
            discussion_id=clean_id,
            content=selftext,
            author_id=author,
            created_at=created_at,
            updated_at=created_at,
            depth=0,
            is_root=True,
            engagement=EngagementMetrics(score=score, upvotes=score),
        )
        disc.add_post(root_post)

        if params.include_comments and len(payload) > 1:
            comments_listing = payload[1].get("data", {}).get("children", [])
            self._extract_comments_recursive(
                children=comments_listing,
                discussion_id=clean_id,
                parent_id=f"root-{clean_id}",
                depth=1,
                max_depth=max_depth,
                max_posts=max_comments,
                target_discussion=disc,
            )

        return disc

    def get_comments(
        self,
        params: DiscussionCommentsParams,
        is_cancelled: Optional[Callable[[], bool]] = None,
    ) -> list[DiscussionPost]:
        retrieval_params = DiscussionRetrievalParams(
            discussion_id=params.discussion_id,
            community_id=params.community_id,
            max_comments=params.max_comments,
            max_depth=params.max_depth,
            include_comments=True,
            timeout_seconds=params.timeout_seconds,
        )
        disc = self.get_discussion(retrieval_params, is_cancelled=is_cancelled)
        comments = disc.thread_structure.get_comments_only()

        if params.parent_id:
            comments = [c for c in comments if c.parent_id == params.parent_id]
        if params.max_depth:
            comments = [c for c in comments if c.depth <= params.max_depth]
        if params.max_comments:
            comments = comments[: params.max_comments]

        return comments

    def get_community_metadata(
        self,
        community_id: str,
        platform: Optional[CommunityPlatform] = None,
        timeout_seconds: Optional[float] = None,
        is_cancelled: Optional[Callable[[], bool]] = None,
    ) -> CommunityContext:
        self.check_cancellation(is_cancelled, target=community_id, operation="get_community_metadata")
        sub = community_id.replace("r/", "").replace("/", "").strip()
        url = f"{self._base_url}/r/{sub}/about.json"

        payload = self._http_get_json(url=url, timeout_seconds=timeout_seconds, is_cancelled=is_cancelled)
        data = payload.get("data", {}) if isinstance(payload, dict) else {}

        if not data or "display_name" not in data:
            raise CommunityDiscussionNotFoundError(
                discussion_id=f"r/{sub}",
                message=f"Subreddit r/{sub} not found.",
            )

        title = str(data.get("title", f"r/{sub}"))
        description = str(data.get("public_description", "") or "")
        subscribers = int(data.get("subscribers", 0))
        sub_type = str(data.get("subreddit_type", "public"))

        return CommunityContext(
            platform=CommunityPlatform.REDDIT,
            community_id=f"r/{sub}",
            community_name=title,
            source_url=f"https://www.reddit.com/r/{sub}",
            category="Subreddit",
            access_status=AccessStatus.PUBLIC if sub_type == "public" else AccessStatus.RESTRICTED,
            metadata={"subscribers": subscribers, "subreddit_type": sub_type},
        )
