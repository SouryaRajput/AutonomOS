"""
Unit tests for Reddit JSON Discussion Provider.
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
    DiscussionRetrievalParams,
    DiscussionSearchParams,
)
from core.research.community.providers.reddit_json import RedditJsonProvider
from core.research.errors import (
    CommunityAuthenticationError,
    CommunityCancelledError,
    CommunityDiscussionNotFoundError,
    CommunityRateLimitError,
    CommunityResourceLimitError,
    CommunityTimeoutError,
)


class TestRedditJsonProvider(unittest.TestCase):

    def test_init_and_properties(self):
        provider = RedditJsonProvider(user_agent="CustomAgent/1.0")
        self.assertEqual(provider.provider_id, "reddit_json")
        self.assertEqual(provider.platform, CommunityPlatform.REDDIT)
        self.assertEqual(provider._user_agent, "CustomAgent/1.0")

    def test_search_discussions_success(self):
        mock_payload = {
            "kind": "Listing",
            "data": {
                "children": [
                    {
                        "kind": "t3",
                        "data": {
                            "id": "1abcde",
                            "subreddit": "Python",
                            "title": "Asyncio Best Practices 2026",
                            "selftext": "Here is a guide on structured concurrency.",
                            "author": "python_fan",
                            "permalink": "/r/Python/comments/1abcde/asyncio_best_practices_2026/",
                            "created_utc": 1780000000.0,
                            "ups": 150,
                            "num_comments": 25,
                            "stickied": False,
                            "over_18": False,
                            "link_flair_text": "Resource",
                        },
                    }
                ]
            },
        }

        def transport_search(req: urllib.request.Request, timeout: float) -> bytes:
            self.assertIn("r/Python/search.json", req.full_url)
            self.assertEqual(req.headers["User-agent"], "CustomAgent/1.0")
            return json.dumps(mock_payload).encode("utf-8")

        provider = RedditJsonProvider(user_agent="CustomAgent/1.0", transport_fn=transport_search)
        params = DiscussionSearchParams(
            query="asyncio",
            community_id="Python",
            limit=5,
        )
        resp = provider.search_discussions(params)
        self.assertEqual(resp.total_found, 1)
        self.assertEqual(len(resp.results), 1)

        disc = resp.results[0]
        self.assertEqual(disc.discussion_id, "1abcde")
        self.assertEqual(disc.community_context.community_id, "r/Python")
        self.assertEqual(disc.title, "Asyncio Best Practices 2026")
        self.assertEqual(disc.author_id, "python_fan")
        self.assertEqual(disc.engagement.score, 150)
        self.assertEqual(disc.engagement.reply_count, 25)
        self.assertEqual(disc.tags, ["Resource"])

    def test_get_discussion_with_recursive_comments(self):
        mock_payload = [
            {
                "kind": "Listing",
                "data": {
                    "children": [
                        {
                            "kind": "t3",
                            "data": {
                                "id": "thread123",
                                "subreddit": "learnpython",
                                "title": "How does GIL work?",
                                "selftext": "Can someone explain the GIL in simple terms?",
                                "author": "learner",
                                "permalink": "/r/learnpython/comments/thread123/gil/",
                                "created_utc": 1780000000.0,
                                "ups": 45,
                                "num_comments": 2,
                            },
                        }
                    ]
                },
            },
            {
                "kind": "Listing",
                "data": {
                    "children": [
                        {
                            "kind": "t1",
                            "data": {
                                "id": "comment_c1",
                                "author": "expert1",
                                "body": "The Global Interpreter Lock ensures one thread runs bytecode at a time.",
                                "created_utc": 1780001000.0,
                                "ups": 20,
                                "parent_id": "t3_thread123",
                                "replies": {
                                    "kind": "Listing",
                                    "data": {
                                        "children": [
                                            {
                                                "kind": "t1",
                                                "data": {
                                                    "id": "reply_r1",
                                                    "author": "learner",
                                                    "body": "Does PEP 703 remove it?",
                                                    "created_utc": 1780002000.0,
                                                    "ups": 5,
                                                    "parent_id": "t1_comment_c1",
                                                    "replies": "",
                                                },
                                            }
                                        ]
                                    },
                                },
                            },
                        }
                    ]
                },
            },
        ]

        def transport_thread(req: urllib.request.Request, timeout: float) -> bytes:
            self.assertIn("comments/thread123.json", req.full_url)
            return json.dumps(mock_payload).encode("utf-8")

        provider = RedditJsonProvider(user_agent="CustomAgent/1.0", transport_fn=transport_thread)
        params = DiscussionRetrievalParams(discussion_id="thread123", community_id="r/learnpython")
        disc = provider.get_discussion(params)

        self.assertEqual(disc.discussion_id, "thread123")
        self.assertEqual(disc.community_context.community_id, "r/learnpython")
        self.assertEqual(disc.title, "How does GIL work?")
        self.assertIsNotNone(disc.root_post)
        self.assertEqual(disc.root_post.content, "Can someone explain the GIL in simple terms?")
        self.assertEqual(disc.thread_structure.total_posts(), 3)  # 1 root + 2 comments

        # Level 1 comment
        c1 = disc.thread_structure.get_post("comment_c1")
        self.assertIsNotNone(c1)
        self.assertEqual(c1.depth, 1)
        self.assertEqual(c1.author_id, "expert1")

        # Level 2 reply
        r1 = disc.thread_structure.get_post("reply_r1")
        self.assertIsNotNone(r1)
        self.assertEqual(r1.parent_id, "comment_c1")
        self.assertEqual(r1.depth, 2)
        self.assertEqual(r1.author_id, "learner")

    def test_get_discussion_not_found(self):
        def transport_404(req: urllib.request.Request, timeout: float) -> bytes:
            raise urllib.error.HTTPError(
                url=req.full_url,
                code=404,
                msg="Not Found",
                hdrs={},
                fp=None,
            )

        provider = RedditJsonProvider(transport_fn=transport_404)
        params = DiscussionRetrievalParams(discussion_id="missing_id")
        with self.assertRaises(CommunityDiscussionNotFoundError):
            provider.get_discussion(params)

    def test_restricted_subreddit_auth_error(self):
        def transport_403(req: urllib.request.Request, timeout: float) -> bytes:
            raise urllib.error.HTTPError(
                url=req.full_url,
                code=403,
                msg="Forbidden",
                hdrs={},
                fp=None,
            )

        provider = RedditJsonProvider(transport_fn=transport_403)
        params = DiscussionSearchParams(query="test", community_id="private_sub")
        with self.assertRaises(CommunityAuthenticationError):
            provider.search_discussions(params)

    def test_rate_limit_429(self):
        def transport_429(req: urllib.request.Request, timeout: float) -> bytes:
            raise urllib.error.HTTPError(
                url=req.full_url,
                code=429,
                msg="Too Many Requests",
                hdrs={},
                fp=None,
            )

        provider = RedditJsonProvider(transport_fn=transport_429)
        params = DiscussionSearchParams(query="test")
        with self.assertRaises(CommunityRateLimitError):
            provider.search_discussions(params)

    def test_cancellation(self):
        provider = RedditJsonProvider()
        params = DiscussionSearchParams(query="test")
        with self.assertRaises(CommunityCancelledError):
            provider.search_discussions(params, is_cancelled=lambda: True)

    def test_timeout(self):
        def transport_timeout(req: urllib.request.Request, timeout: float) -> bytes:
            raise socket.timeout("timed out")

        provider = RedditJsonProvider(transport_fn=transport_timeout)
        params = DiscussionSearchParams(query="test")
        with self.assertRaises(CommunityTimeoutError):
            provider.search_discussions(params)

    def test_get_community_metadata(self):
        mock_payload = {
            "kind": "t5",
            "data": {
                "display_name": "Python",
                "title": "Python Programming",
                "public_description": "News about the programming language Python",
                "subscribers": 1200000,
                "subreddit_type": "public",
            },
        }

        def transport_meta(req: urllib.request.Request, timeout: float) -> bytes:
            self.assertIn("r/Python/about.json", req.full_url)
            return json.dumps(mock_payload).encode("utf-8")

        provider = RedditJsonProvider(transport_fn=transport_meta)
        meta = provider.get_community_metadata("Python")
        self.assertEqual(meta.community_id, "r/Python")
        self.assertEqual(meta.community_name, "Python Programming")
        self.assertEqual(meta.access_status, AccessStatus.PUBLIC)
        self.assertEqual(meta.metadata["subscribers"], 1200000)


if __name__ == "__main__":
    unittest.main()
