"""
Unit tests for GitHub Discussions GraphQL Provider.
"""
import json
import socket
import unittest
import urllib.error
import urllib.request

from core.research.community.models import (
    AccessStatus,
    CommunityPlatform,
    DiscussionStatus,
)
from core.research.community.provider import (
    DiscussionCommentsParams,
    DiscussionFetchLimits,
    DiscussionRetrievalParams,
    DiscussionSearchParams,
)
from core.research.community.providers.github_discussions import (
    GitHubDiscussionsProvider,
)
from core.research.errors import (
    CommunityAuthenticationError,
    CommunityCancelledError,
    CommunityDiscussionNotFoundError,
    CommunityRateLimitError,
    CommunityResourceLimitError,
    CommunityTimeoutError,
)


class TestGitHubDiscussionsProvider(unittest.TestCase):

    def setUp(self):
        self.mock_token = "ghp_testSecretToken1234567890"

    def test_init_and_properties(self):
        provider = GitHubDiscussionsProvider(token=self.mock_token)
        self.assertEqual(provider.provider_id, "github_discussions")
        self.assertEqual(provider.platform, CommunityPlatform.GITHUB_DISCUSSIONS)
        self.assertTrue(provider.has_token)
        self.assertEqual(provider.endpoint, "https://api.github.com/graphql")

    def test_missing_token_raises_auth_error(self):
        provider = GitHubDiscussionsProvider(token="")
        provider._token = ""
        params = DiscussionSearchParams(query="asyncio", platform=CommunityPlatform.GITHUB_DISCUSSIONS)
        with self.assertRaises(CommunityAuthenticationError) as ctx:
            provider.search_discussions(params)
        self.assertIn("GitHub token is missing", str(ctx.exception))

    def test_token_redacted_in_http_error(self):
        def transport_error(req: urllib.request.Request, timeout: float) -> bytes:
            raise urllib.error.HTTPError(
                url="https://api.github.com/graphql",
                code=401,
                msg="Unauthorized",
                hdrs={},
                fp=None,
            )

        provider = GitHubDiscussionsProvider(token=self.mock_token, transport_fn=transport_error)
        params = DiscussionSearchParams(query="bug", platform=CommunityPlatform.GITHUB_DISCUSSIONS)
        with self.assertRaises(CommunityAuthenticationError) as ctx:
            provider.search_discussions(params)
        self.assertNotIn(self.mock_token, str(ctx.exception))

    def test_search_discussions_success(self):
        mock_response = {
            "data": {
                "search": {
                    "discussionCount": 1,
                    "nodes": [
                        {
                            "id": "D_kwDOB...",
                            "number": 42,
                            "title": "How to configure SSL?",
                            "body": "I am having trouble with custom SSL certs.",
                            "url": "https://github.com/org/repo/discussions/42",
                            "createdAt": "2026-08-01T12:00:00Z",
                            "updatedAt": "2026-08-02T15:00:00Z",
                            "upvoteCount": 15,
                            "closed": False,
                            "answerChosenAt": "2026-08-02T14:00:00Z",
                            "author": {"login": "devuser"},
                            "category": {"id": "DIC_123", "name": "Q&A"},
                            "repository": {"nameWithOwner": "org/repo"},
                            "comments": {"totalCount": 3},
                        }
                    ],
                }
            }
        }

        def transport_success(req: urllib.request.Request, timeout: float) -> bytes:
            self.assertEqual(req.headers["Authorization"], f"Bearer {self.mock_token}")
            self.assertEqual(req.headers["User-agent"], "AutonomOS-Researcher/1.0")
            body = json.loads(req.data.decode("utf-8"))
            self.assertIn("query", body)
            return json.dumps(mock_response).encode("utf-8")

        provider = GitHubDiscussionsProvider(token=self.mock_token, transport_fn=transport_success)
        params = DiscussionSearchParams(
            query="SSL certs",
            community_id="org/repo",
            limit=5,
        )
        resp = provider.search_discussions(params)
        self.assertEqual(resp.total_found, 1)
        self.assertEqual(len(resp.results), 1)

        disc = resp.results[0]
        self.assertEqual(disc.title, "How to configure SSL?")
        self.assertEqual(disc.author_id, "devuser")
        self.assertEqual(disc.community_context.community_id, "org/repo")
        self.assertEqual(disc.status, DiscussionStatus.RESOLVED)
        self.assertEqual(disc.engagement.score, 15)
        self.assertEqual(disc.tags, ["Q&A"])

    def test_get_discussion_with_nested_hierarchy(self):
        mock_response = {
            "data": {
                "repository": {
                    "nameWithOwner": "org/repo",
                    "discussion": {
                        "id": "D_kwDOB42",
                        "number": 42,
                        "title": "Thread Root",
                        "body": "Root question description",
                        "url": "https://github.com/org/repo/discussions/42",
                        "createdAt": "2026-08-01T10:00:00Z",
                        "updatedAt": "2026-08-01T12:00:00Z",
                        "upvoteCount": 10,
                        "closed": False,
                        "answerChosenAt": None,
                        "author": {"login": "root_author"},
                        "category": {"id": "C_1", "name": "General"},
                        "comments": {
                            "totalCount": 2,
                            "nodes": [
                                {
                                    "id": "DC_101",
                                    "body": "First answer comment",
                                    "createdAt": "2026-08-01T11:00:00Z",
                                    "updatedAt": "2026-08-01T11:00:00Z",
                                    "upvoteCount": 5,
                                    "isAnswer": True,
                                    "author": {"login": "helper1"},
                                    "replies": {
                                        "totalCount": 1,
                                        "nodes": [
                                            {
                                                "id": "DC_102",
                                                "body": "Reply to first comment",
                                                "createdAt": "2026-08-01T11:30:00Z",
                                                "updatedAt": "2026-08-01T11:30:00Z",
                                                "upvoteCount": 2,
                                                "author": {"login": "root_author"},
                                            }
                                        ],
                                    },
                                }
                            ],
                        },
                    },
                }
            }
        }

        def transport_thread(req: urllib.request.Request, timeout: float) -> bytes:
            return json.dumps(mock_response).encode("utf-8")

        provider = GitHubDiscussionsProvider(token=self.mock_token, transport_fn=transport_thread)
        params = DiscussionRetrievalParams(
            discussion_id="org/repo/42",
            community_id="org/repo",
            max_comments=20,
        )
        disc = provider.get_discussion(params)
        self.assertEqual(disc.discussion_id, "D_kwDOB42")
        self.assertEqual(disc.title, "Thread Root")
        self.assertIsNotNone(disc.root_post)
        self.assertEqual(disc.root_post.content, "Root question description")
        self.assertEqual(disc.thread_structure.total_posts(), 3)  # 1 root + 2 comments

        # Level 1 Comment
        c1 = disc.thread_structure.get_post("DC_101")
        self.assertIsNotNone(c1)
        self.assertEqual(c1.depth, 1)
        self.assertTrue(c1.engagement.is_accepted_answer)
        self.assertEqual(c1.author_id, "helper1")

        # Level 2 Reply
        r1 = disc.thread_structure.get_post("DC_102")
        self.assertIsNotNone(r1)
        self.assertEqual(r1.parent_id, "DC_101")
        self.assertEqual(r1.depth, 2)
        self.assertEqual(r1.author_id, "root_author")

    def test_get_discussion_not_found(self):
        mock_response = {"data": {"repository": {"nameWithOwner": "org/repo", "discussion": None}}}

        def transport_not_found(req: urllib.request.Request, timeout: float) -> bytes:
            return json.dumps(mock_response).encode("utf-8")

        provider = GitHubDiscussionsProvider(token=self.mock_token, transport_fn=transport_not_found)
        params = DiscussionRetrievalParams(discussion_id="org/repo/999")
        with self.assertRaises(CommunityDiscussionNotFoundError):
            provider.get_discussion(params)

    def test_graphql_rate_limit_handling(self):
        mock_response = {
            "errors": [
                {"type": "RATE_LIMITED", "message": "API rate limit exceeded for user ID."}
            ]
        }

        def transport_rate_limit(req: urllib.request.Request, timeout: float) -> bytes:
            return json.dumps(mock_response).encode("utf-8")

        provider = GitHubDiscussionsProvider(token=self.mock_token, transport_fn=transport_rate_limit)
        params = DiscussionSearchParams(query="test")
        with self.assertRaises(CommunityRateLimitError):
            provider.search_discussions(params)

    def test_cancellation_support(self):
        provider = GitHubDiscussionsProvider(token=self.mock_token)
        params = DiscussionSearchParams(query="test")
        with self.assertRaises(CommunityCancelledError):
            provider.search_discussions(params, is_cancelled=lambda: True)

    def test_network_timeout_handling(self):
        def transport_timeout(req: urllib.request.Request, timeout: float) -> bytes:
            raise socket.timeout("timed out")

        provider = GitHubDiscussionsProvider(token=self.mock_token, transport_fn=transport_timeout)
        params = DiscussionSearchParams(query="timeout_test")
        with self.assertRaises(CommunityTimeoutError):
            provider.search_discussions(params)

    def test_get_community_metadata(self):
        mock_response = {
            "data": {
                "repository": {
                    "nameWithOwner": "facebook/react",
                    "description": "The library for web and native user interfaces.",
                    "url": "https://github.com/facebook/react",
                    "isPrivate": False,
                    "hasDiscussionsEnabled": True,
                    "stargazerCount": 220000,
                    "discussionCategories": {
                        "nodes": [
                            {"id": "CAT_1", "name": "Announcements", "description": ""},
                            {"id": "CAT_2", "name": "Q&A", "description": ""},
                        ]
                    },
                }
            }
        }

        def transport_meta(req: urllib.request.Request, timeout: float) -> bytes:
            return json.dumps(mock_response).encode("utf-8")

        provider = GitHubDiscussionsProvider(token=self.mock_token, transport_fn=transport_meta)
        meta = provider.get_community_metadata("facebook/react")
        self.assertEqual(meta.community_id, "facebook/react")
        self.assertEqual(meta.access_status, AccessStatus.PUBLIC)
        self.assertIn("Announcements", meta.metadata["categories"])
        self.assertIn("Q&A", meta.metadata["categories"])
        self.assertEqual(meta.metadata["stars"], 220000)


if __name__ == "__main__":
    unittest.main()
