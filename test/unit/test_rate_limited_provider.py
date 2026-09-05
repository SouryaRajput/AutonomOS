"""
Unit tests for RateLimitedDiscussionProvider middleware.
"""
import unittest

from core.research.community.fake_provider import FakeDiscussionProvider
from core.research.community.models import CommunityPlatform
from core.research.community.provider import (
    DiscussionFetchLimits,
    DiscussionSearchParams,
)
from core.research.community.providers.rate_limiter import (
    RateLimitedDiscussionProvider,
)
from core.research.errors import CommunityCancelledError


class TestRateLimitedDiscussionProvider(unittest.TestCase):

    def setUp(self):
        self.inner = FakeDiscussionProvider()
        self.limiter = RateLimitedDiscussionProvider(
            inner_provider=self.inner,
            requests_per_minute=600.0,
            burst_capacity=5,
        )

    def test_properties(self):
        self.assertEqual(self.limiter.platform, CommunityPlatform.GENERIC)
        self.assertEqual(self.limiter.inner_provider, self.inner)

    def test_search_discussions_passthrough(self):
        params = DiscussionSearchParams(query="python")
        resp = self.limiter.search_discussions(params)
        self.assertIsNotNone(resp)
        self.assertEqual(resp.query, "python")

    def test_cancellation_check(self):
        params = DiscussionSearchParams(query="python")
        with self.assertRaises(CommunityCancelledError):
            self.limiter.search_discussions(params, is_cancelled=lambda: True)


if __name__ == "__main__":
    unittest.main()
