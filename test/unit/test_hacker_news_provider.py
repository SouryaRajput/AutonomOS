"""
Unit tests for HackerNewsDiscussionProvider.

All HTTP is mocked via transport_fn to keep tests hermetic (no network calls).
"""
import json
import unittest
import urllib.request
from typing import Any

from core.research.community.models import (
    CommunityPlatform,
    DiscussionStatus,
)
from core.research.community.provider import (
    DiscussionCommentsParams,
    DiscussionFetchLimits,
    DiscussionRetrievalParams,
    DiscussionSearchParams,
)
from core.research.community.providers.hacker_news import (
    HackerNewsDiscussionProvider,
    _strip_html,
    _unix_to_iso,
    _build_permalink,
)
from core.research.errors import (
    CommunityDiscussionNotFoundError,
    CommunityProviderError,
    CommunityRateLimitError,
    CommunityTimeoutError,
)


def _json_transport(payload: Any) -> "Callable[[urllib.request.Request, float], bytes]":
    """Returns a transport_fn that always returns the given payload as JSON bytes."""
    body = json.dumps(payload).encode("utf-8")
    def _transport(req: urllib.request.Request, timeout: float) -> bytes:
        return body
    return _transport


def _status_transport(status: int):
    """Returns a transport_fn that raises HTTPError with the given status."""
    import urllib.error
    def _transport(req: urllib.request.Request, timeout: float) -> bytes:
        raise urllib.error.HTTPError(url=req.full_url, code=status, msg="Error", hdrs={}, fp=None)
    return _transport


MOCK_SEARCH_RESPONSE = {
    "hits": [
        {
            "objectID": "12345",
            "title": "Ask HN: How do I do X?",
            "author": "pg",
            "points": 100,
            "num_comments": 50,
            "created_at_i": 1700000000,
            "url": None,
        },
        {
            "objectID": "67890",
            "title": "Show HN: My project Y",
            "author": "tptacek",
            "points": 200,
            "num_comments": 30,
            "created_at_i": 1700010000,
            "url": "https://example.com/project",
        },
    ],
    "nbHits": 2,
}

MOCK_ITEM_RESPONSE = {
    "id": 12345,
    "type": "story",
    "title": "Ask HN: How do I do X?",
    "author": "pg",
    "points": 100,
    "created_at_i": 1700000000,
    "text": "<p>This is the <b>story</b> body</p>",
    "url": None,
    "children": [
        {
            "id": 11111,
            "type": "comment",
            "author": "dang",
            "text": "<p>First comment</p>",
            "created_at_i": 1700001000,
            "points": 5,
            "children": [
                {
                    "id": 22222,
                    "type": "comment",
                    "author": "rkoutnik",
                    "text": "Nested reply here.",
                    "created_at_i": 1700002000,
                    "points": 2,
                    "children": [],
                }
            ],
        },
    ],
}


class TestHNHelperFunctions(unittest.TestCase):

    def test_strip_html_removes_tags(self):
        self.assertEqual(_strip_html("<p>Hello <b>world</b></p>"), "Hello world")

    def test_strip_html_decodes_entities(self):
        result = _strip_html("x &amp; y &gt; z")
        self.assertIn("&", result)
        self.assertIn(">", result)

    def test_strip_html_empty(self):
        self.assertEqual(_strip_html(None), "")
        self.assertEqual(_strip_html(""), "")

    def test_unix_to_iso_converts(self):
        result = _unix_to_iso(1700000000)
        self.assertIsNotNone(result)
        self.assertIn("2023", result)  # Approximate year check

    def test_unix_to_iso_none_for_bad(self):
        self.assertIsNone(_unix_to_iso(None))
        self.assertIsNone(_unix_to_iso(0))

    def test_build_permalink(self):
        url = _build_permalink(12345)
        self.assertIn("12345", url)
        self.assertIn("ycombinator.com", url)


class TestHackerNewsSearchDiscussions(unittest.TestCase):

    def setUp(self):
        self.provider = HackerNewsDiscussionProvider(
            transport_fn=_json_transport(MOCK_SEARCH_RESPONSE)
        )

    def test_search_returns_discussions(self):
        params = DiscussionSearchParams(query="async python", limit=5)
        response = self.provider.search_discussions(params)
        self.assertEqual(len(response.results), 2)
        self.assertEqual(response.platform, CommunityPlatform.HACKER_NEWS)

    def test_search_discussion_fields(self):
        params = DiscussionSearchParams(query="python")
        response = self.provider.search_discussions(params)
        first = response.results[0]
        self.assertEqual(first.discussion_id, "12345")
        self.assertEqual(first.title, "Ask HN: How do I do X?")
        self.assertIsNotNone(first.root_post)
        self.assertEqual(first.root_post.author_id, "pg")

    def test_search_total_found(self):
        params = DiscussionSearchParams(query="test")
        response = self.provider.search_discussions(params)
        self.assertEqual(response.total_found, 2)

    def test_search_provider_field(self):
        params = DiscussionSearchParams(query="test")
        response = self.provider.search_discussions(params)
        self.assertEqual(response.provider, "hacker_news")


class TestHackerNewsGetDiscussion(unittest.TestCase):

    def setUp(self):
        self.provider = HackerNewsDiscussionProvider(
            transport_fn=_json_transport(MOCK_ITEM_RESPONSE)
        )

    def test_get_discussion_returns_discussion(self):
        params = DiscussionRetrievalParams(
            discussion_id="12345",
            platform=CommunityPlatform.HACKER_NEWS,
        )
        disc = self.provider.get_discussion(params)
        self.assertEqual(disc.discussion_id, "12345")
        self.assertEqual(disc.title, "Ask HN: How do I do X?")

    def test_get_discussion_root_post(self):
        params = DiscussionRetrievalParams(
            discussion_id="12345",
            platform=CommunityPlatform.HACKER_NEWS,
        )
        disc = self.provider.get_discussion(params)
        self.assertEqual(disc.root_post.author_id, "pg")
        # HTML should be stripped from body
        self.assertNotIn("<p>", disc.root_post.content)
        self.assertIn("story", disc.root_post.content.lower())

    def test_get_discussion_has_posts(self):
        params = DiscussionRetrievalParams(
            discussion_id="12345",
            platform=CommunityPlatform.HACKER_NEWS,
        )
        disc = self.provider.get_discussion(params)
        # Should have root + 1 comment + 1 nested = 3
        self.assertGreaterEqual(disc.total_posts(), 2)

    def test_get_discussion_platform(self):
        params = DiscussionRetrievalParams(
            discussion_id="12345",
            platform=CommunityPlatform.HACKER_NEWS,
        )
        disc = self.provider.get_discussion(params)
        self.assertEqual(disc.community_context.platform, CommunityPlatform.HACKER_NEWS)

    def test_get_discussion_community_context(self):
        params = DiscussionRetrievalParams(
            discussion_id="12345",
            platform=CommunityPlatform.HACKER_NEWS,
        )
        disc = self.provider.get_discussion(params)
        self.assertEqual(disc.community_context.community_name, "Hacker News")


class TestHackerNewsGetComments(unittest.TestCase):

    def setUp(self):
        self.provider = HackerNewsDiscussionProvider(
            transport_fn=_json_transport(MOCK_ITEM_RESPONSE)
        )

    def test_get_comments_returns_posts(self):
        params = DiscussionCommentsParams(
            discussion_id="12345",
            platform=CommunityPlatform.HACKER_NEWS,
        )
        posts = self.provider.get_comments(params)
        self.assertGreaterEqual(len(posts), 1)

    def test_get_comments_has_author(self):
        params = DiscussionCommentsParams(
            discussion_id="12345",
            platform=CommunityPlatform.HACKER_NEWS,
        )
        posts = self.provider.get_comments(params)
        self.assertEqual(posts[0].author_id, "dang")

    def test_get_comments_html_stripped(self):
        params = DiscussionCommentsParams(
            discussion_id="12345",
            platform=CommunityPlatform.HACKER_NEWS,
        )
        posts = self.provider.get_comments(params)
        for post in posts:
            self.assertNotIn("<p>", post.content)


class TestHackerNewsErrorHandling(unittest.TestCase):

    def test_search_404_raises_not_found(self):
        provider = HackerNewsDiscussionProvider(transport_fn=_status_transport(404))
        params = DiscussionSearchParams(query="test")
        with self.assertRaises(CommunityDiscussionNotFoundError):
            provider.search_discussions(params)

    def test_search_429_raises_rate_limit(self):
        provider = HackerNewsDiscussionProvider(transport_fn=_status_transport(429))
        params = DiscussionSearchParams(query="test")
        with self.assertRaises(CommunityRateLimitError):
            provider.search_discussions(params)

    def test_search_500_raises_provider_error(self):
        provider = HackerNewsDiscussionProvider(transport_fn=_status_transport(500))
        params = DiscussionSearchParams(query="test")
        with self.assertRaises(CommunityProviderError):
            provider.search_discussions(params)

    def test_timeout_raises_timeout_error(self):
        import socket

        def timeout_transport(req, t):
            raise socket.timeout("timed out")

        provider = HackerNewsDiscussionProvider(transport_fn=timeout_transport)
        params = DiscussionSearchParams(query="test")
        with self.assertRaises(CommunityTimeoutError):
            provider.search_discussions(params)

    def test_invalid_json_raises_provider_error(self):
        def bad_json_transport(req, t):
            return b"not valid json !!!"

        provider = HackerNewsDiscussionProvider(transport_fn=bad_json_transport)
        params = DiscussionSearchParams(query="test")
        with self.assertRaises(CommunityProviderError):
            provider.search_discussions(params)


class TestHackerNewsProviderMeta(unittest.TestCase):

    def test_provider_id(self):
        provider = HackerNewsDiscussionProvider()
        self.assertEqual(provider.provider_id, "hacker_news")

    def test_platform(self):
        provider = HackerNewsDiscussionProvider()
        self.assertEqual(provider.platform, CommunityPlatform.HACKER_NEWS)

    def test_get_community_metadata(self):
        provider = HackerNewsDiscussionProvider()
        ctx = provider.get_community_metadata("news.ycombinator.com")
        self.assertEqual(ctx.platform, CommunityPlatform.HACKER_NEWS)
        self.assertEqual(ctx.community_id, "news.ycombinator.com")
        self.assertEqual(ctx.community_name, "Hacker News")


if __name__ == "__main__":
    unittest.main()
