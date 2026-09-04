from __future__ import annotations

from core.research.crawler.base import BaseCrawler
from core.research.crawler.capability import CrawlerCapabilitySpec, matches_capability
from core.research.crawler.documentation import DocumentationCrawler
from core.research.crawler.lifecycle import CrawlerStateMachine
from core.research.crawler.mock_crawler import MockCrawler
from core.research.crawler.registry import CrawlerRegistry
from core.research.crawler.repository import RepositoryCrawler
from core.research.crawler.web_fetch import WebFetchCrawler
from core.research.crawler.web_search import WebSearchCrawler

__all__ = [
    "BaseCrawler",
    "CrawlerCapabilitySpec",
    "matches_capability",
    "CrawlerStateMachine",
    "CrawlerRegistry",
    "MockCrawler",
    "WebFetchCrawler",
    "WebSearchCrawler",
    "DocumentationCrawler",
    "RepositoryCrawler",
]
