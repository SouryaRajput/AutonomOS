from __future__ import annotations

from core.research.crawler.base import BaseCrawler
from core.research.crawler.capability import CrawlerCapabilitySpec, matches_capability
from core.research.crawler.lifecycle import CrawlerStateMachine
from core.research.crawler.mock_crawler import MockCrawler
from core.research.crawler.registry import CrawlerRegistry

__all__ = [
    "BaseCrawler",
    "CrawlerCapabilitySpec",
    "matches_capability",
    "CrawlerStateMachine",
    "CrawlerRegistry",
    "MockCrawler",
]
