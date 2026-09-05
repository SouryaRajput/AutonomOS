"""
Production Live Discussion Provider for Stack Exchange / Stack Overflow (REST API v2.3).

Queries Stack Exchange questions, answers, and comment trees over HTTPS using urllib.
Guarantees:
1. Zero external dependencies.
2. Automatic gzip decompression of API payloads.
3. Keyless fallback (300 req/day) and authenticated support (10,000 req/day).
4. Respects API backoff directives and throttle limits.
5. Preserves Q&A hierarchy (Question -> Answer -> Comment).
"""
from __future__ import annotations

import datetime
import gzip
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

from core.inference.secrets import SecretStore
from core.research.community.models import (
    AccessStatus,
    CommunityContext,
    CommunityPlatform,
    Discussion,
    DiscussionPost,
    DiscussionStatus,
    EngagementMetrics,
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
    sanitize_secret,
    validate_network_target,
)

logger = logging.getLogger("AutonomOS.Research.Community.StackExchange")

DEFAULT_STACKEXCHANGE_BASE_URL = "https://api.stackexchange.com/2.3"
DEFAULT_SITE = "stackoverflow"


class StackExchangeDiscussionProvider(DiscussionProvider):
    """
    Live Stack Exchange REST API v2.3 Discussion Provider.
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        default_site: str = DEFAULT_SITE,
        limits: Optional[DiscussionFetchLimits] = None,
        transport_fn: Optional[Callable[[urllib.request.Request, float], bytes]] = None,
        allow_localhost: bool = False,
    ):
        super().__init__(
            provider_id="stack_exchange",
            platform=CommunityPlatform.STACK_EXCHANGE,
            name="Stack Exchange REST API Provider",
            limits=limits or DiscussionFetchLimits(),
        )
        self._allow_localhost = allow_localhost
        raw_url = base_url or os.getenv("STACKEXCHANGE_BASE_URL") or DEFAULT_STACKEXCHANGE_BASE_URL
        self._base_url = raw_url.rstrip("/")
        self._default_site = default_site.strip().lower()
        self._transport_fn = transport_fn

        # Resolve API key if available
        self._api_key = api_key or os.getenv("STACKEXCHANGE_API_KEY")
        if not self._api_key:
            try:
                store = SecretStore()
                self._api_key = store.get_secret("STACKEXCHANGE_API_KEY")
            except Exception:
                pass

    @property
    def base_url(self) -> str:
        return self._base_url

    @property
    def default_site(self) -> str:
        return self._default_site

    def _http_get_json(
        self,
        path: str,
        query_params: dict[str, Any],
        timeout_seconds: Optional[float] = None,
        is_cancelled: Optional[Callable[[], bool]] = None,
    ) -> dict[str, Any]:
        """
        Execute an HTTP GET request against the Stack Exchange API with gzip handling.
        """
        self.check_cancellation(is_cancelled, target=path, operation="http_get_json")

        # Copy params and add key if present
        params = dict(query_params)
        if self._api_key:
            params["key"] = self._api_key

        query_str = urllib.parse.urlencode(params)
        full_url = f"{self._base_url}{path}?{query_str}"

        validate_network_target(full_url, allow_localhost=self._allow_localhost)
        timeout = timeout_seconds if timeout_seconds is not None else self.limits.timeout_seconds

        req = urllib.request.Request(
            url=full_url,
            headers={
                "User-Agent": "AutonomOS-Researcher/1.0",
                "Accept": "application/json",
                "Accept-Encoding": "gzip",
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
                raw_err = http_err.read()
                # Decompress if gzipped error
                if raw_err.startswith(b"\x1f\x8b"):
                    err_body = gzip.decompress(raw_err).decode("utf-8", errors="replace")
                else:
                    err_body = raw_err.decode("utf-8", errors="replace")
            except Exception:
                pass

            sanitized_body = sanitize_secret(err_body, self._api_key or "")
            if status == 400 and ("throttle" in sanitized_body.lower() or "quota" in sanitized_body.lower()):
                raise CommunityRateLimitError(
                    provider_id=self.provider_id,
                    retry_after_seconds=60.0,
                ) from http_err
            elif status in (401, 403):
                raise CommunityAuthenticationError(
                    target=self.provider_id,
                    message=f"Stack Exchange access denied ({status}): {sanitized_body[:200]}",
                ) from http_err
            elif status == 404:
                raise CommunityDiscussionNotFoundError(
                    discussion_id=path,
                    message=f"Stack Exchange resource not found ({path}).",
                ) from http_err
            elif status == 429:
                raise CommunityRateLimitError(
                    provider_id=self.provider_id,
                    retry_after_seconds=60.0,
                ) from http_err
            else:
                raise CommunityProviderError(
                    f"Stack Exchange HTTP {status} error: {sanitized_body[:200]}"
                ) from http_err
        except (urllib.error.URLError, socket.timeout, TimeoutError) as net_err:
            if isinstance(net_err, socket.timeout) or "timed out" in str(net_err).lower():
                raise CommunityTimeoutError(
                    target=full_url,
                    operation="http_get_json",
                    timeout_seconds=timeout,
                ) from net_err
            raise CommunityProviderError(
                f"Stack Exchange network error: {sanitize_error(net_err)}"
            ) from net_err

        # Decompress gzip payload if needed
        if raw_bytes.startswith(b"\x1f\x8b"):
            try:
                decompressed = gzip.decompress(raw_bytes)
                text_content = decompressed.decode("utf-8", errors="replace")
            except Exception as gz_err:
                raise CommunityProviderError(f"Failed to decompress gzipped Stack Exchange payload: {gz_err}") from gz_err
        else:
            text_content = raw_bytes.decode("utf-8", errors="replace")

        try:
            parsed = json.loads(text_content)
        except Exception as json_err:
            raise CommunityProviderError(f"Invalid JSON received from Stack Exchange: {sanitize_error(json_err)}") from json_err

        # Check API backoff notice
        if isinstance(parsed, dict) and "backoff" in parsed:
            backoff_sec = float(parsed["backoff"])
            logger.info(f"Stack Exchange API backoff requested: {backoff_sec}s")
            time.sleep(min(backoff_sec, 5.0))

        # Check error_id in payload
        if isinstance(parsed, dict) and "error_id" in parsed:
            err_name = str(parsed.get("error_name", ""))
            err_msg = str(parsed.get("error_message", ""))
            if "throttle" in err_name.lower() or "quota" in err_name.lower():
                raise CommunityRateLimitError(provider_id=self.provider_id, retry_after_seconds=60.0)
            raise CommunityProviderError(f"Stack Exchange API error [{err_name}]: {err_msg}")

        return parsed

    def _resolve_site(self, community_id: Optional[str]) -> str:
        """Resolve Stack Exchange site identifier."""
        if not community_id:
            return self._default_site
        clean = community_id.strip().lower()
        if clean.startswith("stack_exchange/"):
            return clean.replace("stack_exchange/", "")
        if clean.startswith("se/"):
            return clean.replace("se/", "")
        return clean

    def _format_timestamp(self, ts: Any) -> str:
        if isinstance(ts, (int, float)):
            try:
                dt = datetime.datetime.fromtimestamp(ts, tz=datetime.timezone.utc)
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

        site = self._resolve_site(params.community_id)
        limit = min(params.limit, self.limits.max_search_results)

        query_args: dict[str, Any] = {
            "order": "desc",
            "sort": "relevance",
            "q": params.query.strip(),
            "site": site,
            "pagesize": limit,
            "filter": "withbody",
        }
        if params.tags:
            query_args["tagged"] = ";".join(params.tags)

        payload = self._http_get_json(
            path="/search/advanced",
            query_params=query_args,
            timeout_seconds=params.timeout_seconds,
            is_cancelled=is_cancelled,
        )

        items = payload.get("items", []) if isinstance(payload, dict) else []
        results: list[Discussion] = []

        comm_ctx = CommunityContext(
            platform=CommunityPlatform.STACK_EXCHANGE,
            community_id=site,
            community_name=f"Stack Exchange [{site}]",
            source_url=f"https://{site}.com",
            category="Q&A",
            access_status=AccessStatus.PUBLIC,
        )

        for item in items:
            if not isinstance(item, dict):
                continue
            qid = str(item.get("question_id", ""))
            title = str(item.get("title", "")).strip()
            body = str(item.get("body", "") or "")
            owner = item.get("owner", {})
            author = str(owner.get("display_name", "anonymous"))
            created_at = self._format_timestamp(item.get("creation_date"))
            updated_at = self._format_timestamp(item.get("last_activity_date"))
            score = int(item.get("score", 0))
            ans_count = int(item.get("answer_count", 0))
            is_answered = bool(item.get("is_answered", False))
            tags = [str(t) for t in item.get("tags", [])]
            link = str(item.get("link", f"https://{site}.com/questions/{qid}"))

            disc = Discussion(
                discussion_id=qid,
                community_context=comm_ctx,
                title=title,
                url=link,
                author_id=author,
                created_at=created_at,
                updated_at=updated_at,
                status=DiscussionStatus.RESOLVED if is_answered else DiscussionStatus.OPEN,
                tags=tags,
                engagement=EngagementMetrics(
                    score=score,
                    upvotes=score,
                    reply_count=ans_count,
                    is_accepted_answer=is_answered,
                ),
                metadata={
                    "site": site,
                    "question_id": qid,
                    "view_count": item.get("view_count", 0),
                },
            )

            root_post = DiscussionPost(
                post_id=f"q-{qid}",
                discussion_id=qid,
                content=body,
                author_id=author,
                created_at=created_at,
                updated_at=updated_at,
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
            metadata={"site": site},
        )

    def _parse_question_id(self, discussion_id: str) -> tuple[str, Optional[str]]:
        """Extract question ID and optional site from discussion ID or URL."""
        raw = discussion_id.strip()
        url_match = re.search(r"https?://([^/]+)/questions/(\d+)", raw)
        if url_match:
            site = url_match.group(1).replace(".com", "").replace(".stackexchange", "")
            return url_match.group(2), site

        if ":" in raw:
            parts = raw.split(":", 1)
            return parts[1], parts[0]

        clean_id = raw.replace("q-", "").replace("q_", "")
        return clean_id, None

    def get_discussion(
        self,
        params: DiscussionRetrievalParams,
        is_cancelled: Optional[Callable[[], bool]] = None,
    ) -> Discussion:
        self.check_cancellation(is_cancelled, target=params.discussion_id, operation="get_discussion")

        qid, extracted_site = self._parse_question_id(params.discussion_id)
        site = self._resolve_site(params.community_id or extracted_site)

        # 1. Fetch Question
        q_payload = self._http_get_json(
            path=f"/questions/{qid}",
            query_params={"site": site, "filter": "withbody"},
            timeout_seconds=params.timeout_seconds,
            is_cancelled=is_cancelled,
        )

        items = q_payload.get("items", [])
        if not items:
            raise CommunityDiscussionNotFoundError(
                discussion_id=qid,
                message=f"Stack Exchange question '{qid}' not found on site '{site}'.",
            )

        q_data = items[0]
        title = str(q_data.get("title", "")).strip()
        body = str(q_data.get("body", "") or "")
        author = str(q_data.get("owner", {}).get("display_name", "anonymous"))
        created_at = self._format_timestamp(q_data.get("creation_date"))
        updated_at = self._format_timestamp(q_data.get("last_activity_date"))
        score = int(q_data.get("score", 0))
        ans_count = int(q_data.get("answer_count", 0))
        is_answered = bool(q_data.get("is_answered", False))
        tags = [str(t) for t in q_data.get("tags", [])]
        link = str(q_data.get("link", f"https://{site}.com/questions/{qid}"))

        comm_ctx = CommunityContext(
            platform=CommunityPlatform.STACK_EXCHANGE,
            community_id=site,
            community_name=f"Stack Exchange [{site}]",
            source_url=f"https://{site}.com",
            category="Q&A",
            access_status=AccessStatus.PUBLIC,
        )

        disc = Discussion(
            discussion_id=qid,
            community_context=comm_ctx,
            title=title,
            url=link,
            author_id=author,
            created_at=created_at,
            updated_at=updated_at,
            status=DiscussionStatus.RESOLVED if is_answered else DiscussionStatus.OPEN,
            tags=tags,
            engagement=EngagementMetrics(
                score=score,
                upvotes=score,
                reply_count=ans_count,
                is_accepted_answer=is_answered,
            ),
            metadata={"site": site, "question_id": qid},
        )

        root_post = DiscussionPost(
            post_id=f"q-{qid}",
            discussion_id=qid,
            content=body,
            author_id=author,
            created_at=created_at,
            updated_at=updated_at,
            depth=0,
            is_root=True,
            engagement=EngagementMetrics(score=score, upvotes=score),
        )
        disc.add_post(root_post)

        # 2. Fetch Answers & Comments if requested
        if params.include_comments:
            max_comments = min(params.max_comments or 50, self.limits.max_posts_per_discussion)
            ans_payload = self._http_get_json(
                path=f"/questions/{qid}/answers",
                query_params={"site": site, "filter": "withbody", "order": "desc", "sort": "votes", "pagesize": min(max_comments, 20)},
                timeout_seconds=params.timeout_seconds,
                is_cancelled=is_cancelled,
            )
            answers = ans_payload.get("items", [])
            for ans in answers:
                aid = str(ans.get("answer_id", ""))
                ans_body = str(ans.get("body", "") or "")
                ans_author = str(ans.get("owner", {}).get("display_name", "anonymous"))
                ans_score = int(ans.get("score", 0))
                ans_accepted = bool(ans.get("is_accepted", False))
                ans_created = self._format_timestamp(ans.get("creation_date"))

                a_post = DiscussionPost(
                    post_id=f"a-{aid}",
                    discussion_id=qid,
                    content=ans_body,
                    parent_id=f"q-{qid}",
                    author_id=ans_author,
                    created_at=ans_created,
                    updated_at=ans_created,
                    depth=1,
                    is_root=False,
                    engagement=EngagementMetrics(score=ans_score, upvotes=ans_score, is_accepted_answer=ans_accepted),
                )
                disc.add_post(a_post)

                if disc.total_posts() >= max_comments:
                    break

            # 3. Fetch Comments on Question
            if disc.total_posts() < max_comments:
                comm_payload = self._http_get_json(
                    path=f"/questions/{qid}/comments",
                    query_params={"site": site, "filter": "withbody", "order": "asc", "sort": "creation", "pagesize": 10},
                    timeout_seconds=params.timeout_seconds,
                    is_cancelled=is_cancelled,
                )
                comments = comm_payload.get("items", [])
                for comm in comments:
                    cid = str(comm.get("comment_id", ""))
                    comm_body = str(comm.get("body", "") or "")
                    comm_author = str(comm.get("owner", {}).get("display_name", "anonymous"))
                    comm_score = int(comm.get("score", 0))
                    comm_created = self._format_timestamp(comm.get("creation_date"))

                    c_post = DiscussionPost(
                        post_id=f"c-{cid}",
                        discussion_id=qid,
                        content=comm_body,
                        parent_id=f"q-{qid}",
                        author_id=comm_author,
                        created_at=comm_created,
                        updated_at=comm_created,
                        depth=1,
                        is_root=False,
                        engagement=EngagementMetrics(score=comm_score, upvotes=comm_score),
                    )
                    disc.add_post(c_post)
                    if disc.total_posts() >= max_comments:
                        break

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
        site = self._resolve_site(community_id)

        payload = self._http_get_json(
            path="/info",
            query_params={"site": site},
            timeout_seconds=timeout_seconds,
            is_cancelled=is_cancelled,
        )
        items = payload.get("items", [])
        if not items:
            raise CommunityDiscussionNotFoundError(
                discussion_id=site,
                message=f"Stack Exchange site '{site}' not found.",
            )

        info = items[0]
        site_name = str(info.get("site", {}).get("name", site))

        return CommunityContext(
            platform=CommunityPlatform.STACK_EXCHANGE,
            community_id=site,
            community_name=site_name,
            source_url=f"https://{site}.com",
            category="Q&A",
            access_status=AccessStatus.PUBLIC,
            metadata={
                "total_questions": info.get("total_questions", 0),
                "total_answers": info.get("total_answers", 0),
                "total_comments": info.get("total_comments", 0),
            },
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
        """
        Fetch child comments on an answer or question for deep subtree expansion.
        """
        qid, site_opt = self._parse_question_id(discussion_id)
        site = self._resolve_site(site_opt)

        clean_root = root_comment_id.replace("a-", "").replace("q-", "")
        limit = min(max_comments or 20, 50)

        # If answer id
        if root_comment_id.startswith("a-"):
            payload = self._http_get_json(
                path=f"/answers/{clean_root}/comments",
                query_params={"site": site, "filter": "withbody", "pagesize": limit},
                timeout_seconds=timeout_seconds,
                is_cancelled=is_cancelled,
            )
        else:
            payload = self._http_get_json(
                path=f"/questions/{clean_root}/comments",
                query_params={"site": site, "filter": "withbody", "pagesize": limit},
                timeout_seconds=timeout_seconds,
                is_cancelled=is_cancelled,
            )

        items = payload.get("items", [])
        posts: list[DiscussionPost] = []
        for comm in items:
            cid = str(comm.get("comment_id", ""))
            comm_body = str(comm.get("body", "") or "")
            comm_author = str(comm.get("owner", {}).get("display_name", "anonymous"))
            comm_score = int(comm.get("score", 0))
            comm_created = self._format_timestamp(comm.get("creation_date"))

            post = DiscussionPost(
                post_id=f"c-{cid}",
                discussion_id=qid,
                content=comm_body,
                parent_id=root_comment_id,
                author_id=comm_author,
                created_at=comm_created,
                updated_at=comm_created,
                depth=2,
                is_root=False,
                engagement=EngagementMetrics(score=comm_score, upvotes=comm_score),
            )
            posts.append(post)

        return posts
