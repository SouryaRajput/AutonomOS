"""
Community Discussion Providers Package.

Exposes live network adapters and middleware for community platforms:
- GitHubDiscussionsProvider (GitHub GraphQL API v4)
- HackerNewsDiscussionProvider (Algolia HN Search API — keyless)
- RedditJsonProvider (Reddit .json Public Web API)
- StackExchangeDiscussionProvider (Stack Exchange REST API v2.3)
- RateLimitedDiscussionProvider (Token-bucket rate limiter)
"""
from core.research.community.providers.github_discussions import GitHubDiscussionsProvider
from core.research.community.providers.hacker_news import HackerNewsDiscussionProvider
from core.research.community.providers.rate_limiter import RateLimitedDiscussionProvider
from core.research.community.providers.reddit_json import RedditJsonProvider
from core.research.community.providers.stack_exchange import StackExchangeDiscussionProvider

__all__ = [
    "GitHubDiscussionsProvider",
    "HackerNewsDiscussionProvider",
    "RedditJsonProvider",
    "StackExchangeDiscussionProvider",
    "RateLimitedDiscussionProvider",
]
