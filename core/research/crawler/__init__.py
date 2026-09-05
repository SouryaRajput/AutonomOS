from __future__ import annotations

from core.research.crawler.base import BaseCrawler
from core.research.crawler.capability import CrawlerCapabilitySpec, matches_capability
from core.research.crawler.community import CommunityCrawler
from core.research.crawler.documentation import DocumentationCrawler
from core.research.crawler.lifecycle import CrawlerStateMachine
from core.research.crawler.project_context import ProjectContextCrawler
from core.research.crawler.project_memory import ProjectMemoryCrawler
from core.research.crawler.registry import CrawlerRegistry
from core.research.crawler.repository import RepositoryCrawler
from core.research.crawler.structured import StructuredDataCrawler
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
    "CommunityCrawler",
    "ProjectMemoryCrawler",
    "ProjectContextCrawler",
    "StructuredDataCrawler",
]
