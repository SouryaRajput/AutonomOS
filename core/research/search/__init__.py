from __future__ import annotations

from core.research.errors import (
    SearchAuthenticationError,
    SearchConfigurationError,
    SearchError,
    SearchParameterValidationError,
    SearchProviderError,
    SearchRateLimitError,
    SearchSecurityError,
    SearchTimeoutError,
)
from core.research.search.config import SearchConfig
from core.research.search.factory import create_search_provider
from core.research.search.models import (
    SearchParameters,
    SearchResponse,
    SearchResultItem,
)
from core.research.search.normalization import (
    deduplicate_search_results,
    extract_domain,
    normalize_url,
)
from core.research.search.provider import (
    MockSearchProvider,
    SearchProvider,
)
from core.research.search.providers.duckduckgo import DuckDuckGoSearchProvider
from core.research.search.providers.tavily import TavilySearchProvider
from core.research.search.security import (
    compute_effective_timeout,
    is_safe_search_url,
    sanitize_error,
    sanitize_headers,
    sanitize_secret,
    sanitize_url,
    validate_network_target,
)
from core.research.search.test_provider import TestSearchProvider

__all__ = [
    "SearchError",
    "SearchParameterValidationError",
    "SearchProviderError",
    "SearchRateLimitError",
    "SearchAuthenticationError",
    "SearchTimeoutError",
    "SearchSecurityError",
    "SearchConfigurationError",
    "SearchConfig",
    "SearchParameters",
    "SearchResultItem",
    "SearchResponse",
    "normalize_url",
    "extract_domain",
    "deduplicate_search_results",
    "SearchProvider",
    "MockSearchProvider",
    "TestSearchProvider",
    "DuckDuckGoSearchProvider",
    "TavilySearchProvider",
    "create_search_provider",
    "validate_network_target",
    "is_safe_search_url",
    "sanitize_secret",
    "sanitize_headers",
    "sanitize_url",
    "sanitize_error",
    "compute_effective_timeout",
]
