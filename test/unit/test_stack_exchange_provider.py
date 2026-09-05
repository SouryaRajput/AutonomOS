"""
Unit tests for Stack Exchange Discussion Provider (REST API v2.3).
"""
import gzip
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
from core.research.community.providers.stack_exchange import (
    StackExchangeDiscussionProvider,
)
from core.research.errors import (
    CommunityAuthenticationError,
    CommunityCancelledError,
    CommunityDiscussionNotFoundError,
    CommunityRateLimitError,
    CommunityTimeoutError,
)


class TestStackExchangeProvider(unittest.TestCase):

    def setUp(self):
        self.provider = StackExchangeDiscussionProvider(api_key="test_key_123")

    def _gzip_encode(self, data: dict) -> bytes:
        raw_json = json.dumps(data).encode("utf-8")
        return gzip.compress(raw_json)

    def test_init_and_properties(self):
        self.assertEqual(self.provider.provider_id, "stack_exchange")
        self.assertEqual(self.provider.platform, CommunityPlatform.STACK_EXCHANGE)
        self.assertEqual(self.provider.default_site, "stackoverflow")

    def test_search_discussions_success(self):
        mock_payload = {
            "items": [
                {
                    "question_id": 12345678,
                    "title": "How to handle asyncio TaskGroup exceptions in Python 3.12?",
                    "body": "<p>I am trying to catch ExceptionGroup from TaskGroup...</p>",
                    "owner": {"display_name": "py_dev"},
                    "score": 42,
                    "answer_count": 3,
                    "is_answered": True,
                    "creation_date": 1780000000,
                    "last_activity_date": 1780001000,
                    "tags": ["python", "python-asyncio", "python-3.12"],
                    "link": "https://stackoverflow.com/questions/12345678",
                }
            ],
            "has_more": False,
            "quota_remaining": 9990,
        }

        def transport_search(req: urllib.request.Request, timeout: float) -> bytes:
            self.assertIn("/search/advanced", req.full_url)
            self.assertIn("site=stackoverflow", req.full_url)
            return self._gzip_encode(mock_payload)

        provider = StackExchangeDiscussionProvider(
            api_key="test_key_123",
            transport_fn=transport_search,
        )
        params = DiscussionSearchParams(query="asyncio TaskGroup", limit=5)
        resp = provider.search_discussions(params)

        self.assertEqual(resp.total_found, 1)
        self.assertEqual(len(resp.results), 1)

        disc = resp.results[0]
        self.assertEqual(disc.discussion_id, "12345678")
        self.assertEqual(disc.title, "How to handle asyncio TaskGroup exceptions in Python 3.12?")
        self.assertEqual(disc.author_id, "py_dev")
        self.assertEqual(disc.status, DiscussionStatus.RESOLVED)
        self.assertEqual(disc.engagement.score, 42)
        self.assertEqual(disc.engagement.reply_count, 3)
        self.assertIn("python-asyncio", disc.tags)
        self.assertIsNotNone(disc.root_post)

    def test_get_discussion_with_answers_and_comments(self):
        q_payload = {
            "items": [
                {
                    "question_id": 99999,
                    "title": "What is the difference between GIL and multi-processing?",
                    "body": "Can someone explain why multiprocessing bypasses the GIL?",
                    "owner": {"display_name": "curious_coder"},
                    "score": 85,
                    "answer_count": 1,
                    "is_answered": True,
                    "creation_date": 1780000000,
                    "link": "https://stackoverflow.com/questions/99999",
                }
            ]
        }
        ans_payload = {
            "items": [
                {
                    "answer_id": 88888,
                    "body": "Multiprocessing launches independent OS processes with separate GILs.",
                    "owner": {"display_name": "guru"},
                    "score": 120,
                    "is_accepted": True,
                    "creation_date": 1780000500,
                }
            ]
        }
        comm_payload = {
            "items": [
                {
                    "comment_id": 77777,
                    "body": "Note that IPC memory overhead is higher.",
                    "owner": {"display_name": "perf_engineer"},
                    "score": 15,
                    "creation_date": 1780000800,
                }
            ]
        }

        def transport_thread(req: urllib.request.Request, timeout: float) -> bytes:
            if "/answers" in req.full_url:
                return self._gzip_encode(ans_payload)
            elif "/comments" in req.full_url:
                return self._gzip_encode(comm_payload)
            else:
                return self._gzip_encode(q_payload)

        provider = StackExchangeDiscussionProvider(transport_fn=transport_thread)
        params = DiscussionRetrievalParams(discussion_id="99999", include_comments=True)
        disc = provider.get_discussion(params)

        self.assertEqual(disc.discussion_id, "99999")
        self.assertEqual(disc.title, "What is the difference between GIL and multi-processing?")
        self.assertEqual(disc.total_posts(), 3)  # root question + 1 answer + 1 comment

        # Check Answer (depth 1)
        ans_post = disc.thread_structure.get_post("a-88888")
        self.assertIsNotNone(ans_post)
        self.assertEqual(ans_post.parent_id, "q-99999")
        self.assertEqual(ans_post.depth, 1)
        self.assertTrue(ans_post.engagement.is_accepted_answer)

        # Check Comment (depth 1)
        comm_post = disc.thread_structure.get_post("c-77777")
        self.assertIsNotNone(comm_post)
        self.assertEqual(comm_post.depth, 1)
        self.assertEqual(comm_post.author_id, "perf_engineer")

    def test_question_not_found(self):
        empty_payload = {"items": []}

        def transport_empty(req: urllib.request.Request, timeout: float) -> bytes:
            return self._gzip_encode(empty_payload)

        provider = StackExchangeDiscussionProvider(transport_fn=transport_empty)
        params = DiscussionRetrievalParams(discussion_id="nonexistent")
        with self.assertRaises(CommunityDiscussionNotFoundError):
            provider.get_discussion(params)

    def test_throttle_rate_limit_error(self):
        throttle_payload = {
            "error_id": 502,
            "error_name": "throttle_violation",
            "error_message": "Too many requests from this IP",
        }

        def transport_throttle(req: urllib.request.Request, timeout: float) -> bytes:
            return self._gzip_encode(throttle_payload)

        provider = StackExchangeDiscussionProvider(transport_fn=transport_throttle)
        params = DiscussionSearchParams(query="test")
        with self.assertRaises(CommunityRateLimitError):
            provider.search_discussions(params)

    def test_cancellation(self):
        params = DiscussionSearchParams(query="test")
        with self.assertRaises(CommunityCancelledError):
            self.provider.search_discussions(params, is_cancelled=lambda: True)

    def test_timeout(self):
        def transport_timeout(req: urllib.request.Request, timeout: float) -> bytes:
            raise socket.timeout("timed out")

        provider = StackExchangeDiscussionProvider(transport_fn=transport_timeout)
        params = DiscussionSearchParams(query="test")
        with self.assertRaises(CommunityTimeoutError):
            provider.search_discussions(params)

    def test_retrieve_comment_subtree(self):
        comments_payload = {
            "items": [
                {
                    "comment_id": 55555,
                    "body": "Subtree comment on answer",
                    "owner": {"display_name": "commenter"},
                    "score": 3,
                    "creation_date": 1780001000,
                }
            ]
        }

        def transport_subtree(req: urllib.request.Request, timeout: float) -> bytes:
            self.assertIn("/answers/88888/comments", req.full_url)
            return self._gzip_encode(comments_payload)

        provider = StackExchangeDiscussionProvider(transport_fn=transport_subtree)
        sub_posts = provider.retrieve_comment_subtree(
            discussion_id="99999",
            root_comment_id="a-88888",
        )

        self.assertEqual(len(sub_posts), 1)
        self.assertEqual(sub_posts[0].post_id, "c-55555")
        self.assertEqual(sub_posts[0].parent_id, "a-88888")

    def test_get_community_metadata(self):
        info_payload = {
            "items": [
                {
                    "total_questions": 24000000,
                    "total_answers": 35000000,
                    "site": {"name": "Stack Overflow"},
                }
            ]
        }

        def transport_info(req: urllib.request.Request, timeout: float) -> bytes:
            return self._gzip_encode(info_payload)

        provider = StackExchangeDiscussionProvider(transport_fn=transport_info)
        meta = provider.get_community_metadata("stackoverflow")
        self.assertEqual(meta.community_id, "stackoverflow")
        self.assertEqual(meta.name, "Stack Overflow")
        self.assertEqual(meta.access_status, AccessStatus.PUBLIC)
        self.assertEqual(meta.metadata["total_questions"], 24000000)


if __name__ == "__main__":
    unittest.main()
