from __future__ import annotations

import logging
import os
from typing import Optional

from core.research.search.config import SearchConfig
from core.research.search.provider import MockSearchProvider, SearchProvider
from core.research.search.providers.duckduckgo import DuckDuckGoSearchProvider
from core.research.search.providers.tavily import TavilySearchProvider

logger = logging.getLogger("AutonomOS.Research.SearchFactory")


def create_search_provider(config: Optional[SearchConfig] = None) -> SearchProvider:
    """
    Factory function instantiating a SearchProvider based on configuration and environment.
    Supports 'duckduckgo', 'tavily', 'mock', and 'auto' (falling back to DuckDuckGo when no keys exist).
    """
    cfg = config or SearchConfig.from_env()
    provider_name = cfg.provider_type.lower().strip()

    if provider_name in ("duckduckgo", "ddg"):
        logger.info("Instantiating DuckDuckGoSearchProvider (keyless)")
        return DuckDuckGoSearchProvider(config=cfg)

    if provider_name == "auto":
        tavily_key = cfg.api_key or os.getenv("TAVILY_API_KEY") or os.getenv("SEARCH_API_KEY")
        if tavily_key:
            logger.info("Auto-selected TavilySearchProvider based on available API key")
            return TavilySearchProvider(config=cfg)
        logger.info("Auto-selected DuckDuckGoSearchProvider (zero keys detected, keyless fallback)")
        return DuckDuckGoSearchProvider(config=cfg)

    if provider_name == "tavily":
        logger.info(f"Instantiating TavilySearchProvider (endpoint={cfg.base_url or 'https://api.tavily.com'})")
        return TavilySearchProvider(config=cfg)

    logger.info(f"Instantiating MockSearchProvider (provider_id={cfg.provider_type})")
    return MockSearchProvider(provider_id=cfg.provider_type)
