from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import re
from typing import TYPE_CHECKING, Any, Optional
import uuid

from core.research.errors import SearchParameterValidationError
from core.research.search.normalization import extract_domain, normalize_url

if TYPE_CHECKING:
    from core.research.contracts.crawler_task import CrawlerTask


def utc_now() -> str:
    """Generate ISO 8601 UTC timestamp."""
    return datetime.now(timezone.utc).isoformat()


_DOMAIN_REGEX = re.compile(
    r"^(?:[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?\.)+[a-zA-Z]{2,}$"
)


@dataclass
class SearchResultItem:
    """
    Provider-neutral normalized search result.
    Preserves provenance, ranked order, publication date (or None), and metadata.
    """
    title: str
    url: str
    original_url: Optional[str] = None
    normalized_url: Optional[str] = None
    snippet: str = ""
    domain: str = ""
    rank: int = 1
    published_date: Optional[str] = None
    provider: str = ""
    retrieved_at: str = field(default_factory=utc_now)
    provider_result_id: Optional[str] = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        if not self.original_url:
            self.original_url = self.url
        if not self.normalized_url:
            self.normalized_url = normalize_url(self.url)
        if not self.domain:
            self.domain = extract_domain(self.url)

    def to_dict(self) -> dict[str, Any]:
        return {
            "title": self.title,
            "url": self.url,
            "original_url": self.original_url,
            "normalized_url": self.normalized_url,
            "snippet": self.snippet,
            "domain": self.domain,
            "rank": self.rank,
            "published_date": self.published_date,
            "provider": self.provider,
            "retrieved_at": self.retrieved_at,
            "provider_result_id": self.provider_result_id,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SearchResultItem:
        return cls(
            title=str(data.get("title", "")),
            url=str(data.get("url", "")),
            original_url=data.get("original_url"),
            normalized_url=data.get("normalized_url"),
            snippet=str(data.get("snippet", "")),
            domain=str(data.get("domain", "")),
            rank=int(data.get("rank", 1)),
            published_date=data.get("published_date"),
            provider=str(data.get("provider", "")),
            retrieved_at=str(data.get("retrieved_at", utc_now())),
            provider_result_id=data.get("provider_result_id"),
            metadata=dict(data.get("metadata", {})),
        )


@dataclass
class SearchParameters:
    """
    Structured search task parameters with validation.
    Extractable directly from CrawlerTask or raw configuration dictionaries.
    """
    query: str
    limit: int = 5
    freshness: Optional[str] = None
    language: Optional[str] = None
    region: Optional[str] = None
    safe_search: bool = True
    domain_allowlist: list[str] = field(default_factory=list)
    domain_blocklist: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def validate(self) -> None:
        """
        Validate parameters against schema, boundary, and sanity constraints.
        Raises SearchParameterValidationError on any invalid configuration.
        """
        if not self.query or not isinstance(self.query, str) or not self.query.strip():
            raise SearchParameterValidationError("query", "Search query cannot be empty or whitespace.")

        if not isinstance(self.limit, int) or self.limit < 1 or self.limit > 100:
            raise SearchParameterValidationError(
                "limit",
                f"Search limit must be an integer between 1 and 100, got '{self.limit}'",
            )

        # Validate domain filters
        for domain in self.domain_allowlist:
            self._validate_domain_format(domain, "domain_allowlist")

        for domain in self.domain_blocklist:
            self._validate_domain_format(domain, "domain_blocklist")

    @staticmethod
    def _validate_domain_format(domain: str, param_name: str) -> None:
        if not domain or not isinstance(domain, str) or not domain.strip():
            raise SearchParameterValidationError(param_name, "Domain entry cannot be empty.")
        clean = domain.strip().lower()
        if "://" in clean or "/" in clean or " " in clean or not _DOMAIN_REGEX.match(clean):
            raise SearchParameterValidationError(
                param_name,
                f"Invalid domain format '{domain}'. Must be a valid hostname (e.g. 'docs.python.org').",
            )

    @classmethod
    def from_crawler_task(cls, task: CrawlerTask) -> SearchParameters:
        """
        Extract and construct validated SearchParameters from a generic CrawlerTask.
        Parses task.query_or_target, task.parameters, and task.constraints.
        """
        query = task.query_or_target or task.objective
        params = dict(task.parameters or {})
        constraints = list(task.constraints or [])

        limit = int(params.get("limit", 5))
        freshness = params.get("freshness")
        language = params.get("language")
        region = params.get("region")
        safe_search = bool(params.get("safe_search", True))

        domain_allowlist = list(params.get("domain_allowlist", []))
        domain_blocklist = list(params.get("domain_blocklist", []))

        # Parse domain directives from constraints (e.g. "domain:github.com", "-domain:spam.com")
        for c in constraints:
            c_str = str(c).strip()
            if c_str.startswith("domain:") or c_str.startswith("allow:"):
                dom = c_str.split(":", 1)[1].strip()
                if dom and dom not in domain_allowlist:
                    domain_allowlist.append(dom)
            elif c_str.startswith("-domain:") or c_str.startswith("block:"):
                dom = c_str.split(":", 1)[1].strip()
                if dom and dom not in domain_blocklist:
                    domain_blocklist.append(dom)

        search_params = cls(
            query=query,
            limit=limit,
            freshness=freshness,
            language=language,
            region=region,
            safe_search=safe_search,
            domain_allowlist=domain_allowlist,
            domain_blocklist=domain_blocklist,
            metadata=dict(task.metadata or {}),
        )
        search_params.validate()
        return search_params

    def to_dict(self) -> dict[str, Any]:
        return {
            "query": self.query,
            "limit": self.limit,
            "freshness": self.freshness,
            "language": self.language,
            "region": self.region,
            "safe_search": self.safe_search,
            "domain_allowlist": list(self.domain_allowlist),
            "domain_blocklist": list(self.domain_blocklist),
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SearchParameters:
        params = cls(
            query=str(data.get("query", "")),
            limit=int(data.get("limit", 5)),
            freshness=data.get("freshness"),
            language=data.get("language"),
            region=data.get("region"),
            safe_search=bool(data.get("safe_search", True)),
            domain_allowlist=list(data.get("domain_allowlist", [])),
            domain_blocklist=list(data.get("domain_blocklist", [])),
            metadata=dict(data.get("metadata", {})),
        )
        params.validate()
        return params


@dataclass
class SearchResponse:
    """
    Standardized search query response returned by any concrete SearchProvider.
    """
    query: str
    results: list[SearchResultItem] = field(default_factory=list)
    total_results: Optional[int] = None
    provider: str = ""
    execution_time_seconds: float = 0.0
    retrieved_at: str = field(default_factory=utc_now)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "query": self.query,
            "results": [r.to_dict() for r in self.results],
            "total_results": self.total_results,
            "provider": self.provider,
            "execution_time_seconds": self.execution_time_seconds,
            "retrieved_at": self.retrieved_at,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SearchResponse:
        results = [
            SearchResultItem.from_dict(r) if isinstance(r, dict) else r
            for r in data.get("results", [])
        ]
        return cls(
            query=str(data.get("query", "")),
            results=results,
            total_results=data.get("total_results"),
            provider=str(data.get("provider", "")),
            execution_time_seconds=float(data.get("execution_time_seconds", 0.0)),
            retrieved_at=str(data.get("retrieved_at", utc_now())),
            metadata=dict(data.get("metadata", {})),
        )
