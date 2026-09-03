from __future__ import annotations

import logging
from typing import Optional

from core.research.search.config import SearchConfig
from core.research.search.provider import MockSearchProvider, SearchProvider
from core.research.search.providers.tavily import TavilySearchProvider

logger = logging.getLogger("AutonomOS.Research.SearchFactory")


def create_search_provider(config: Optional[SearchConfig] = None) -> SearchProvider:
    """
    Factory function instantiating a SearchProvider based on configuration and environment.
    """
    cfg = config or SearchConfig.from_env()
    provider_name = cfg.provider_type.lower().strip()

    if provider_name == "tavily":
        logger.info(f"Instantiating TavilySearchProvider (endpoint={cfg.base_url or 'https://api.tavily.com'})")
        return TavilySearchProvider(config=cfg)

    logger.info(f"Instantiating MockSearchProvider (provider_id={cfg.provider_type})")
    return MockSearchProvider(provider_id=cfg.provider_type)
