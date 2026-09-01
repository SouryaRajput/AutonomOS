from __future__ import annotations

import hashlib
import logging
from typing import Optional
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

from core.research.contracts.crawler_report import CrawlerReport
from core.research.contracts.evidence import EvidenceItem, Source
from core.research.types import SourceType

logger = logging.getLogger("AutonomOS.Research.EvidenceCollector")


class EvidenceCollector:
    """
    Collects, normalizes, deduplicates, and validates raw sources and extracted evidence items
    from crawler reports into a verified evidence pool.
    """

    TRACKING_PARAMS = {
        "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",
        "fbclid", "gclid", "ref", "source",
    }

    @classmethod
    def normalize_url(cls, raw_url: str) -> str:
        """Strip tracking query parameters and trailing slashes for canonical source matching."""
        if not raw_url or not raw_url.startswith(("http://", "https://")):
            return raw_url.strip()

        try:
            parsed = urlparse(raw_url)
            query_dict = parse_qs(parsed.query, keep_blank_values=True)
            filtered_query = {k: v for k, v in query_dict.items() if k.lower() not in cls.TRACKING_PARAMS}
            clean_query = urlencode(filtered_query, doseq=True)
            clean_path = parsed.path.rstrip("/") if parsed.path != "/" else "/"
            normalized = urlunparse((
                parsed.scheme.lower(),
                parsed.netloc.lower(),
                clean_path,
                parsed.params,
                clean_query,
                "",  # strip fragment
            ))
            return normalized
        except Exception:
            return raw_url.strip()

    @classmethod
    def deduplicate_sources(cls, candidate_sources: list[dict]) -> list[dict]:
        """Deduplicate source candidates by normalized URL."""
        seen_urls: set[str] = set()
        unique: list[dict] = []
        for cand in candidate_sources:
            raw_url = str(cand.get("url", "")).strip()
            norm = cls.normalize_url(raw_url)
            if norm and norm not in seen_urls:
                seen_urls.add(norm)
                cand["url"] = norm
                unique.append(cand)
            elif not raw_url:
                unique.append(cand)
        return unique

    @classmethod
    def classify_source_type(cls, url: str, title: str = "") -> SourceType:
        """Classify source authority based on domain and URL patterns."""
        u_lower = url.lower()
        t_lower = title.lower()

        if any(d in u_lower for d in ("docs.", "/docs", "/documentation", "readthedocs.io", "developer.", "git-scm.com", "python.org")):
            return SourceType.OFFICIAL_DOCUMENTATION
        if any(d in u_lower for d in ("github.com", "gitlab.com", "bitbucket.org")):
            return SourceType.REPOSITORY
        if any(d in u_lower for d in ("arxiv.org", "acm.org", "ieee.org", "doi.org", "sciencedirect.com")):
            return SourceType.ACADEMIC
        if any(d in u_lower for d in ("news.ycombinator.com", "stackoverflow.com", "reddit.com", "discuss.", "forum.")):
            return SourceType.COMMUNITY
        if any(d in u_lower for d in ("reuters.com", "bloomberg.com", "techcrunch.com", "theverge.com")):
            return SourceType.NEWS
        if any(d in u_lower for d in ("blog.", "/blog", "medium.com", "dev.to", "substack.com")):
            return SourceType.BLOG
        return SourceType.OTHER
