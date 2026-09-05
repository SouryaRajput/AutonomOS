"""
Production Live Discussion Provider for GitHub Discussions (GraphQL API v4).

Queries GitHub Discussions using standard library urllib and GraphQL queries.
Guarantees:
1. Zero external dependencies.
2. Token/Secret protection (sanitizes tokens from error messages and logs).
3. SSRF boundary protection.
4. Deterministic mapping of GraphQL errors and HTTP status codes to CommunityError taxonomy.
5. Hierarchical comment/reply tree preservation.
"""
from __future__ import annotations

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
    sanitize_secret,
    validate_network_target,
)

logger = logging.getLogger("AutonomOS.Research.Community.GitHubDiscussions")

DEFAULT_GITHUB_GRAPHQL_ENDPOINT = "https://api.github.com/graphql"


class GitHubDiscussionsProvider(DiscussionProvider):
    """
    Live GitHub Discussions Provider communicating with GitHub GraphQL API v4.
    """

    def __init__(
        self,
        token: Optional[str] = None,
        endpoint: Optional[str] = None,
        limits: Optional[DiscussionFetchLimits] = None,
        transport_fn: Optional[Callable[[urllib.request.Request, float], bytes]] = None,
        allow_localhost: bool = False,
    ):
        super().__init__(
            provider_id="github_discussions",
            platform=CommunityPlatform.GITHUB_DISCUSSIONS,
            name="GitHub Discussions GraphQL Provider",
            limits=limits or DiscussionFetchLimits(),
        )
        self._allow_localhost = allow_localhost
        self._endpoint = (endpoint or os.getenv("GITHUB_GRAPHQL_ENDPOINT") or DEFAULT_GITHUB_GRAPHQL_ENDPOINT).strip()
        self._transport_fn = transport_fn

        # Resolve token securely
        self._token = token or os.getenv("GITHUB_TOKEN") or os.getenv("GH_TOKEN")
        if not self._token:
            # Fallback to SecretStore
            try:
                store = SecretStore()
                self._token = store.get_secret("GITHUB_TOKEN") or store.get_secret("GH_TOKEN")
            except Exception:
                pass

    @property
    def endpoint(self) -> str:
        return self._endpoint

    @property
    def has_token(self) -> bool:
        return bool(self._token and self._token.strip())

    def _execute_graphql(
        self,
        query: str,
        variables: Optional[dict[str, Any]] = None,
        timeout_seconds: Optional[float] = None,
        is_cancelled: Optional[Callable[[], bool]] = None,
    ) -> dict[str, Any]:
        """
        Execute a raw GraphQL query against GitHub's API.
        """
        self.check_cancellation(is_cancelled, target=self.endpoint, operation="graphql_query")

        if not self._token:
            raise CommunityAuthenticationError(
                target=self.provider_id,
                message="GitHub token is missing. Configure 'GITHUB_TOKEN' environment variable or pass token to provider.",
            )

        # 1. SSRF target validation
        validate_network_target(self._endpoint, allow_localhost=self._allow_localhost)

        timeout = timeout_seconds if timeout_seconds is not None else self.limits.timeout_seconds
        payload_data = json.dumps({"query": query, "variables": variables or {}}).encode("utf-8")

        req = urllib.request.Request(
            url=self._endpoint,
            data=payload_data,
            headers={
                "Authorization": f"Bearer {self._token.strip()}",
                "User-Agent": "AutonomOS-Researcher/1.0",
                "Content-Type": "application/json",
                "Accept": "application/vnd.github.v4+json, application/json",
            },
            method="POST",
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

            sanitized_body = sanitize_secret(err_body, self._token)
            if status in (401, 403):
                raise CommunityAuthenticationError(
                    target=self.provider_id,
                    message=f"GitHub Discussions authentication failed ({status}): {sanitized_body}",
                ) from http_err
            elif status == 404:
                raise CommunityDiscussionNotFoundError(
                    discussion_id=str(variables),
                    message=f"GitHub resource not found (404): {sanitized_body}",
                ) from http_err
            elif status == 429:
                raise CommunityRateLimitError(
                    provider_id=self.provider_id,
                    retry_after_seconds=60.0,
                ) from http_err
            else:
                raise CommunityProviderError(
                    f"GitHub GraphQL API returned HTTP {status}: {sanitized_body}"
                ) from http_err
        except (urllib.error.URLError, socket.timeout, TimeoutError) as net_err:
            if isinstance(net_err, socket.timeout) or "timed out" in str(net_err).lower():
                raise CommunityTimeoutError(
                    target=self.endpoint,
                    operation="graphql_query",
                    timeout_seconds=timeout,
                ) from net_err
            raise CommunityProviderError(
                f"GitHub Discussions network transport error: {sanitize_error(net_err)}"
            ) from net_err

        try:
            parsed = json.loads(raw_bytes.decode("utf-8"))
        except Exception as json_err:
            raise CommunityProviderError(
                f"Invalid JSON received from GitHub GraphQL: {sanitize_error(json_err)}"
            ) from json_err

        # Check GraphQL errors
        if "errors" in parsed and parsed["errors"]:
            err_messages = [e.get("message", "") for e in parsed["errors"] if isinstance(e, dict)]
            err_combined = "; ".join(err_messages)
            sanitized_err = sanitize_secret(err_combined, self._token)

            if any("rate limit" in m.lower() for m in err_messages):
                raise CommunityRateLimitError(
                    provider_id=self.provider_id,
                    retry_after_seconds=60.0,
                )
            if any("could not resolve to a repository" in m.lower() or "not found" in m.lower() for m in err_messages):
                raise CommunityDiscussionNotFoundError(discussion_id=str(variables), message=f"GitHub resource not found: {sanitized_err}")
            if any("bad credentials" in m.lower() or "requires authentication" in m.lower() for m in err_messages):
                raise CommunityAuthenticationError(target=self.provider_id, message=f"GitHub authentication error: {sanitized_err}")

            raise CommunityProviderError(f"GitHub GraphQL query errors: {sanitized_err}")

        return parsed.get("data", {})

    def search_discussions(
        self,
        params: DiscussionSearchParams,
        is_cancelled: Optional[Callable[[], bool]] = None,
    ) -> DiscussionSearchResponse:
        start_time = time.monotonic()
        self.check_cancellation(is_cancelled, target=params.query, operation="search_discussions")

        # Build GitHub search query
        query_terms = [params.query.strip()]
        if params.community_id and "/" in params.community_id:
            query_terms.insert(0, f"repo:{params.community_id.strip()}")
        
        gql_search_query = " ".join(query_terms)
        first_count = min(params.limit, self.limits.max_search_results)

        graphql_query = """
        query SearchDiscussions($query: String!, $first: Int!) {
          search(query: $query, type: DISCUSSION, first: $first) {
            discussionCount
            nodes {
              ... on Discussion {
                id
                number
                title
                body
                url
                createdAt
                updatedAt
                upvoteCount
                closed
                answerChosenAt
                author {
                  login
                }
                category {
                  id
                  name
                }
                repository {
                  nameWithOwner
                }
                comments {
                  totalCount
                }
              }
            }
          }
        }
        """

        data = self._execute_graphql(
            query=graphql_query,
            variables={"query": gql_search_query, "first": first_count},
            timeout_seconds=params.timeout_seconds,
            is_cancelled=is_cancelled,
        )

        search_data = data.get("search", {})
        total_found = search_data.get("discussionCount", 0)
        nodes = search_data.get("nodes", [])

        results: list[Discussion] = []
        for node in nodes:
            if not isinstance(node, dict) or not node.get("title"):
                continue

            repo_info = node.get("repository") or {}
            repo_name = repo_info.get("nameWithOwner", params.community_id or "github")
            author_info = node.get("author") or {}
            author = author_info.get("login", "ghost")
            category_info = node.get("category") or {}
            cat_name = category_info.get("name", "General")

            disc_id = str(node.get("id") or f"{repo_name}#{node.get('number')}")
            title = node.get("title", "").strip()
            body = node.get("body", "") or ""
            url = node.get("url", "")
            created_at = node.get("createdAt", utc_now())
            updated_at = node.get("updatedAt", created_at)
            upvotes = int(node.get("upvoteCount", 0))
            is_closed = bool(node.get("closed", False))
            is_answered = bool(node.get("answerChosenAt"))

            status = DiscussionStatus.RESOLVED if is_answered else (DiscussionStatus.CLOSED if is_closed else DiscussionStatus.OPEN)

            comm_ctx = CommunityContext(
                platform=CommunityPlatform.GITHUB_DISCUSSIONS,
                community_id=repo_name,
                community_name=f"GitHub {repo_name}",
                source_url=f"https://github.com/{repo_name}/discussions",
                repository_association=repo_name,
                category=cat_name,
                access_status=AccessStatus.PUBLIC,
            )

            disc = Discussion(
                discussion_id=disc_id,
                community_context=comm_ctx,
                title=title,
                url=url,
                author_id=author,
                created_at=created_at,
                updated_at=updated_at,
                status=status,
                tags=[cat_name],
                engagement=EngagementMetrics(
                    score=upvotes,
                    upvotes=upvotes,
                    reply_count=int(node.get("comments", {}).get("totalCount", 0)),
                    is_accepted_answer=is_answered,
                ),
                metadata={
                    "discussion_number": node.get("number"),
                    "category_id": category_info.get("id"),
                    "repository": repo_name,
                    "is_answered": is_answered,
                },
            )
            root_post = DiscussionPost(
                post_id=f"root-{disc_id}",
                discussion_id=disc_id,
                content=body,
                author_id=author,
                created_at=created_at,
                updated_at=updated_at,
                depth=0,
                is_root=True,
                engagement=EngagementMetrics(score=upvotes, upvotes=upvotes),
            )
            disc.add_post(root_post)
            results.append(disc)

        return DiscussionSearchResponse(
            query=params.query,
            results=results,
            total_found=total_found,
            execution_time_seconds=time.monotonic() - start_time,
            provider=self.provider_id,
            platform=self.platform,
            metadata={"community_id": params.community_id, "first_requested": first_count},
        )

    def _parse_target(self, discussion_id: str, community_id: Optional[str]) -> tuple[str, str, int]:
        """Extract (owner, repo, number) from discussion_id or community_id."""
        raw = discussion_id.strip()
        url_match = re.search(r"github\.com/([^/]+)/([^/]+)/discussions/(\d+)", raw)
        if url_match:
            return url_match.group(1), url_match.group(2), int(url_match.group(3))

        id_match = re.match(r"^([^/]+)/([^/#]+)[/#](\d+)$", raw)
        if id_match:
            return id_match.group(1), id_match.group(2), int(id_match.group(3))

        if raw.isdigit() and community_id and "/" in community_id:
            parts = community_id.strip().split("/", 1)
            return parts[0], parts[1], int(raw)

        return "", "", 0

    def get_discussion(
        self,
        params: DiscussionRetrievalParams,
        is_cancelled: Optional[Callable[[], bool]] = None,
    ) -> Discussion:
        self.check_cancellation(is_cancelled, target=params.discussion_id, operation="get_discussion")
        owner, repo, number = self._parse_target(params.discussion_id, params.community_id)

        max_comments = min(params.max_comments or 50, self.limits.max_posts_per_discussion)

        if owner and repo and number > 0:
            graphql_query = """
            query GetDiscussion($owner: String!, $name: String!, $number: Int!, $commentsFirst: Int!) {
              repository(owner: $owner, name: $name) {
                nameWithOwner
                discussion(number: $number) {
                  id
                  number
                  title
                  body
                  url
                  createdAt
                  updatedAt
                  upvoteCount
                  closed
                  answerChosenAt
                  author {
                    login
                  }
                  category {
                    id
                    name
                  }
                  comments(first: $commentsFirst) {
                    totalCount
                    nodes {
                      id
                      body
                      createdAt
                      updatedAt
                      upvoteCount
                      isAnswer
                      author {
                        login
                      }
                      replies(first: 20) {
                        totalCount
                        nodes {
                          id
                          body
                          createdAt
                          updatedAt
                          upvoteCount
                          author {
                            login
                          }
                        }
                      }
                    }
                  }
                }
              }
            }
            """
            data = self._execute_graphql(
                query=graphql_query,
                variables={"owner": owner, "name": repo, "number": number, "commentsFirst": max_comments},
                timeout_seconds=params.timeout_seconds,
                is_cancelled=is_cancelled,
            )
            repo_data = data.get("repository") or {}
            node = repo_data.get("discussion")
            if not node:
                raise CommunityDiscussionNotFoundError(
                    discussion_id=params.discussion_id,
                    message=f"GitHub Discussion #{number} not found in repository {owner}/{repo}.",
                )
            repo_name = repo_data.get("nameWithOwner", f"{owner}/{repo}")
        else:
            graphql_query = """
            query GetDiscussionNode($id: ID!, $commentsFirst: Int!) {
              node(id: $id) {
                ... on Discussion {
                  id
                  number
                  title
                  body
                  url
                  createdAt
                  updatedAt
                  upvoteCount
                  closed
                  answerChosenAt
                  author {
                    login
                  }
                  category {
                    id
                    name
                  }
                  repository {
                    nameWithOwner
                  }
                  comments(first: $commentsFirst) {
                    totalCount
                    nodes {
                      id
                      body
                      createdAt
                      updatedAt
                      upvoteCount
                      isAnswer
                      author {
                        login
                      }
                      replies(first: 20) {
                        totalCount
                        nodes {
                          id
                          body
                          createdAt
                          updatedAt
                          upvoteCount
                          author {
                            login
                          }
                        }
                      }
                    }
                  }
                }
              }
            }
            """
            data = self._execute_graphql(
                query=graphql_query,
                variables={"id": params.discussion_id, "commentsFirst": max_comments},
                timeout_seconds=params.timeout_seconds,
                is_cancelled=is_cancelled,
            )
            node = data.get("node")
            if not node or "title" not in node:
                raise CommunityDiscussionNotFoundError(
                    discussion_id=params.discussion_id,
                    message=f"GitHub Discussion node '{params.discussion_id}' not found.",
                )
            repo_name = node.get("repository", {}).get("nameWithOwner", params.community_id or "github")

        author = (node.get("author") or {}).get("login", "ghost")
        cat_name = (node.get("category") or {}).get("name", "General")
        disc_id = str(node.get("id"))
        title = node.get("title", "").strip()
        body = node.get("body", "") or ""
        url = node.get("url", "")
        created_at = node.get("createdAt", utc_now())
        updated_at = node.get("updatedAt", created_at)
        upvotes = int(node.get("upvoteCount", 0))
        is_closed = bool(node.get("closed", False))
        is_answered = bool(node.get("answerChosenAt"))

        status = DiscussionStatus.RESOLVED if is_answered else (DiscussionStatus.CLOSED if is_closed else DiscussionStatus.OPEN)

        comm_ctx = CommunityContext(
            platform=CommunityPlatform.GITHUB_DISCUSSIONS,
            community_id=repo_name,
            community_name=f"GitHub {repo_name}",
            source_url=f"https://github.com/{repo_name}/discussions",
            repository_association=repo_name,
            category=cat_name,
            access_status=AccessStatus.PUBLIC,
        )

        comments_info = node.get("comments") or {}
        disc = Discussion(
            discussion_id=disc_id,
            community_context=comm_ctx,
            title=title,
            url=url,
            author_id=author,
            created_at=created_at,
            updated_at=updated_at,
            status=status,
            tags=[cat_name],
            engagement=EngagementMetrics(
                score=upvotes,
                upvotes=upvotes,
                reply_count=int(comments_info.get("totalCount", 0)),
                is_accepted_answer=is_answered,
            ),
            metadata={
                "discussion_number": node.get("number"),
                "repository": repo_name,
                "is_answered": is_answered,
            },
        )

        root_post = DiscussionPost(
            post_id=f"root-{disc_id}",
            discussion_id=disc_id,
            content=body,
            author_id=author,
            created_at=created_at,
            updated_at=updated_at,
            depth=0,
            is_root=True,
            engagement=EngagementMetrics(score=upvotes, upvotes=upvotes),
        )
        disc.add_post(root_post)

        if params.include_comments:
            comment_nodes = comments_info.get("nodes") or []
            for c_node in comment_nodes:
                if not isinstance(c_node, dict):
                    continue
                c_id = str(c_node.get("id"))
                c_body = c_node.get("body", "") or ""
                c_author = (c_node.get("author") or {}).get("login", "ghost")
                c_created = c_node.get("createdAt", created_at)
                c_score = int(c_node.get("upvoteCount", 0))
                c_is_ans = bool(c_node.get("isAnswer", False))

                c_post = DiscussionPost(
                    post_id=c_id,
                    discussion_id=disc_id,
                    content=c_body,
                    parent_id=f"root-{disc_id}",
                    author_id=c_author,
                    created_at=c_created,
                    updated_at=c_node.get("updatedAt", c_created),
                    depth=1,
                    is_root=False,
                    engagement=EngagementMetrics(score=c_score, upvotes=c_score, is_accepted_answer=c_is_ans),
                )
                disc.add_post(c_post)

                replies_info = c_node.get("replies") or {}
                reply_nodes = replies_info.get("nodes") or []
                for r_node in reply_nodes:
                    if not isinstance(r_node, dict):
                        continue
                    r_id = str(r_node.get("id"))
                    r_body = r_node.get("body", "") or ""
                    r_author = (r_node.get("author") or {}).get("login", "ghost")
                    r_created = r_node.get("createdAt", c_created)
                    r_score = int(r_node.get("upvoteCount", 0))

                    r_post = DiscussionPost(
                        post_id=r_id,
                        discussion_id=disc_id,
                        content=r_body,
                        parent_id=c_id,
                        author_id=r_author,
                        created_at=r_created,
                        updated_at=r_node.get("updatedAt", r_created),
                        depth=2,
                        is_root=False,
                        engagement=EngagementMetrics(score=r_score, upvotes=r_score),
                    )
                    disc.add_post(r_post)

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
        if "/" not in community_id:
            raise CommunityValidationError("community_id", "GitHub community_id must be in format 'owner/repo'.")

        owner, repo = community_id.strip().split("/", 1)

        graphql_query = """
        query GetRepoDiscussionsInfo($owner: String!, $name: String!) {
          repository(owner: $owner, name: $name) {
            nameWithOwner
            description
            url
            isPrivate
            hasDiscussionsEnabled
            stargazerCount
            discussionCategories(first: 25) {
              nodes {
                id
                name
                description
              }
            }
          }
        }
        """

        data = self._execute_graphql(
            query=graphql_query,
            variables={"owner": owner, "name": repo},
            timeout_seconds=timeout_seconds,
            is_cancelled=is_cancelled,
        )

        repo_data = data.get("repository")
        if not repo_data:
            raise CommunityDiscussionNotFoundError(
                discussion_id=community_id,
                message=f"GitHub repository '{community_id}' not found.",
            )

        cat_nodes = (repo_data.get("discussionCategories") or {}).get("nodes") or []
        categories = [c.get("name", "") for c in cat_nodes if isinstance(c, dict) and c.get("name")]

        return CommunityContext(
            platform=CommunityPlatform.GITHUB_DISCUSSIONS,
            community_id=repo_data.get("nameWithOwner", community_id),
            community_name=f"GitHub {community_id}",
            source_url=repo_data.get("url", f"https://github.com/{community_id}"),
            repository_association=community_id,
            category="Discussions",
            access_status=AccessStatus.RESTRICTED if repo_data.get("isPrivate") else AccessStatus.PUBLIC,
            metadata={
                "stars": repo_data.get("stargazerCount", 0),
                "has_discussions_enabled": repo_data.get("hasDiscussionsEnabled", True),
                "categories": categories,
            },
        )
