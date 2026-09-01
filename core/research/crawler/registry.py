from __future__ import annotations

import logging
import threading
from typing import Any, Callable, Optional

from core.research.crawler.base import BaseCrawler
from core.research.errors import CrawlerNotFoundError
from core.research.types import CrawlerCapability, CrawlerHealthStatus, CrawlerStatus

logger = logging.getLogger("AutonomOS.Research.CrawlerRegistry")


class CrawlerRegistry:
    """
    Thread-safe registry for crawler instances, definitions, and capability factories.
    Enables capability-based dynamic discovery, status tracking, and workforce health monitoring.
    """

    def __init__(self):
        self._lock = threading.RLock()
        self._crawlers: dict[str, BaseCrawler] = {}
        self._factories: dict[CrawlerCapability, list[Callable[[], BaseCrawler]]] = {}
        self._type_registrations: dict[CrawlerCapability, list[type[BaseCrawler]]] = {}

    def register_crawler_instance(self, crawler: BaseCrawler) -> None:
        """Register an existing crawler instance."""
        with self._lock:
            self._crawlers[crawler.id] = crawler
            logger.info(f"Registered crawler instance '{crawler.id}' with capabilities: {[c.value for c in crawler.get_capabilities()]}")

    def register_crawler_type(
        self,
        capability: CrawlerCapability,
        crawler_class: type[BaseCrawler],
    ) -> None:
        """Register a crawler class type associated with a capability."""
        with self._lock:
            if capability not in self._type_registrations:
                self._type_registrations[capability] = []
            if crawler_class not in self._type_registrations[capability]:
                self._type_registrations[capability].append(crawler_class)

    def register_crawler_factory(
        self,
        capability: CrawlerCapability,
        factory: Callable[[], BaseCrawler],
    ) -> None:
        """Register a dynamic factory function producing crawlers with a given capability."""
        with self._lock:
            if capability not in self._factories:
                self._factories[capability] = []
            self._factories[capability].append(factory)

    def unregister_crawler(self, crawler_id: str) -> Optional[BaseCrawler]:
        """Unregister a crawler instance."""
        with self._lock:
            crawler = self._crawlers.pop(crawler_id, None)
            if crawler:
                crawler.terminate()
            return crawler

    def get_crawler(self, crawler_id: str) -> BaseCrawler:
        """Retrieve a registered crawler by ID."""
        with self._lock:
            if crawler_id not in self._crawlers:
                raise CrawlerNotFoundError(crawler_id)
            return self._crawlers[crawler_id]

    def get_crawlers_for_capability(self, capability: CrawlerCapability) -> list[BaseCrawler]:
        """Find all registered instances matching the requested capability."""
        with self._lock:
            return [c for c in self._crawlers.values() if c.has_capability(capability) and c.status != CrawlerStatus.TERMINATED]

    def find_eligible_crawlers(
        self,
        capabilities: list[CrawlerCapability],
        require_all: bool = False,
        idle_only: bool = False,
    ) -> list[BaseCrawler]:
        """
        Find registered crawlers satisfying the capability requirements.
        If require_all is True, crawler must have all listed capabilities.
        If idle_only is True, crawler must not be busy.
        """
        with self._lock:
            candidates: list[BaseCrawler] = []
            for c in self._crawlers.values():
                if c.status == CrawlerStatus.TERMINATED:
                    continue
                if idle_only and c.is_busy():
                    continue

                if require_all:
                    if all(c.has_capability(cap) for cap in capabilities):
                        candidates.append(c)
                else:
                    if any(c.has_capability(cap) for cap in capabilities):
                        candidates.append(c)

            return candidates

    def list_idle_crawlers(self) -> list[BaseCrawler]:
        """List all registered crawlers that are not currently executing a task."""
        with self._lock:
            return [
                c for c in self._crawlers.values()
                if not c.is_busy() and c.status not in (CrawlerStatus.TERMINATED, CrawlerStatus.FAILED)
            ]

    def list_healthy_crawlers(self) -> list[BaseCrawler]:
        """List all crawlers reporting HEALTHY status."""
        with self._lock:
            return [
                c for c in self._crawlers.values()
                if c.health == CrawlerHealthStatus.HEALTHY and c.status != CrawlerStatus.TERMINATED
            ]

    def list_available_capabilities(self) -> list[CrawlerCapability]:
        """List all distinct capabilities supported by registered instances, types, or factories."""
        with self._lock:
            caps: set[CrawlerCapability] = set()
            for c in self._crawlers.values():
                if c.status != CrawlerStatus.TERMINATED:
                    caps.update(c.get_capabilities())
            caps.update(self._type_registrations.keys())
            caps.update(self._factories.keys())
            return list(caps)

    def list_active_crawlers(self) -> list[BaseCrawler]:
        """List all active registered crawler instances."""
        with self._lock:
            return [c for c in self._crawlers.values() if c.status != CrawlerStatus.TERMINATED]

    def get_crawler_health_summary(self) -> dict[str, Any]:
        """Return a statistical overview of the registered crawler workforce health and status."""
        with self._lock:
            total = len(self._crawlers)
            healthy = sum(1 for c in self._crawlers.values() if c.health == CrawlerHealthStatus.HEALTHY)
            degraded = sum(1 for c in self._crawlers.values() if c.health == CrawlerHealthStatus.DEGRADED)
            dead = sum(1 for c in self._crawlers.values() if c.health == CrawlerHealthStatus.DEAD or c.status == CrawlerStatus.TERMINATED)
            busy = sum(1 for c in self._crawlers.values() if c.is_busy())
            idle = sum(1 for c in self._crawlers.values() if not c.is_busy() and c.status != CrawlerStatus.TERMINATED)
            completed_tasks = sum(c.tasks_completed for c in self._crawlers.values())
            failed_tasks = sum(c.tasks_failed for c in self._crawlers.values())

            return {
                "total_crawlers": total,
                "healthy_count": healthy,
                "degraded_count": degraded,
                "dead_count": dead,
                "busy_count": busy,
                "idle_count": idle,
                "total_tasks_completed": completed_tasks,
                "total_tasks_failed": failed_tasks,
            }
