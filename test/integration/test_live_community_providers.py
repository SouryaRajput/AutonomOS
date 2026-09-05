"""
Integration test for Live Community Providers with CommunityCrawler.
"""
import os
import unittest

from core.research.community.models import CommunityPlatform
from core.research.community.providers.github_discussions import (
    GitHubDiscussionsProvider,
)
from core.research.community.providers.reddit_json import RedditJsonProvider
from core.research.crawler.community import CommunityCrawler
from core.research.types import (
    CrawlerCapability,
    CrawlerReportStatus,
)


class TestLiveCommunityProvidersIntegration(unittest.TestCase):

    def test_community_crawler_with_github_discussions_provider(self):
        token = os.getenv("GITHUB_TOKEN", "test_mock_token")
        provider = GitHubDiscussionsProvider(token=token)

        crawler = CommunityCrawler(
            crawler_id="crawler.community.github.1",
            name="GitHub Community Specialist",
            provider=provider,
        )

        self.assertEqual(crawler.provider.platform, CommunityPlatform.GITHUB_DISCUSSIONS)
        self.assertIn("COMMUNITY_CRAWL", crawler.get_manifest().capabilities)

    def test_community_crawler_with_reddit_json_provider(self):
        provider = RedditJsonProvider(user_agent="AutonomOS-Test/1.0")

        crawler = CommunityCrawler(
            crawler_id="crawler.community.reddit.1",
            name="Reddit Community Specialist",
            provider=provider,
        )

        self.assertEqual(crawler.provider.platform, CommunityPlatform.REDDIT)
        self.assertIn("COMMUNITY_CRAWL", crawler.get_manifest().capabilities)

    def test_multi_provider_community_crawler(self):
        gh_provider = GitHubDiscussionsProvider(token="mock_gh_token")
        reddit_provider = RedditJsonProvider(user_agent="AutonomOS-Test/1.0")

        crawler = CommunityCrawler(
            crawler_id="crawler.community.multi.1",
            name="Multi Platform Specialist",
            providers=[gh_provider, reddit_provider],
        )

        self.assertEqual(len(crawler.providers), 2)
        self.assertEqual(crawler.providers[0].platform, CommunityPlatform.GITHUB_DISCUSSIONS)
        self.assertEqual(crawler.providers[1].platform, CommunityPlatform.REDDIT)


if __name__ == "__main__":
    unittest.main()
