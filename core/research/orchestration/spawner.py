from __future__ import annotations

import logging
from typing import Optional
import uuid

from core.research.contracts.crawler_task import CrawlerTask
from core.research.contracts.plan import ResearchPlan
from core.research.crawler.base import BaseCrawler
from core.research.crawler.registry import CrawlerRegistry
from core.research.types import CrawlerCapability, CrawlerStatus

logger = logging.getLogger("AutonomOS.Research.CrawlerSpawner")


class CrawlerSpawner:
    """
    Dynamic Crawler Spawner.
    Responsible for dynamically scaling and provisioning N crawlers matching
    the required capabilities declared in a ResearchPlan or requested on-demand.
    Supports 1-to-1, 1-to-N crawler allocations, dynamic replacements, and on-demand expansion.
    """

    def __init__(self, registry: Optional[CrawlerRegistry] = None):
        self.registry = registry or CrawlerRegistry()

    def spawn_crawlers_for_plan(
        self,
        plan: ResearchPlan,
    ) -> list[BaseCrawler]:
        """
        Dynamically provision N crawlers matching the capabilities required by the plan.
        Sizing is determined dynamically by the number of distinct capabilities and task volume,
        bounded by plan.scope.max_crawlers.
        """
        # Determine unique required capabilities across all questions and tasks
        required_caps: set[CrawlerCapability] = set()
        for q in plan.questions:
            required_caps.update(q.required_capabilities)
        if not required_caps:
            required_caps = {CrawlerCapability.WEB_SEARCH}

        max_allowed = max(1, plan.scope.max_crawlers)
        # Determine target crawler count based on scope, question count, and task volume
        target_count = min(max_allowed, max(len(required_caps), len(plan.questions), len(plan.crawler_tasks)))
        if target_count < 1:
            target_count = 1

        allocated_crawlers: list[BaseCrawler] = []

        # 1. First, attempt to match from registered active crawler instances in registry
        for cap in required_caps:
            matched = self.registry.get_crawlers_for_capability(cap)
            for c in matched:
                if c not in allocated_crawlers and len(allocated_crawlers) < target_count and not c.is_busy():
                    allocated_crawlers.append(c)

        # 2. If more crawlers needed to reach target_count, instantiate dynamic crawlers
        caps_list = list(required_caps)
        cap_idx = 0
        while len(allocated_crawlers) < target_count:
            cap = caps_list[cap_idx % len(caps_list)]
            cap_idx += 1
            dynamic_crawler = self._create_crawler_for_capability(cap, index=len(allocated_crawlers) + 1)
            self.registry.register_crawler_instance(dynamic_crawler)
            allocated_crawlers.append(dynamic_crawler)

        plan.allocated_crawler_count = len(allocated_crawlers)
        logger.info(f"Dynamically spawned/allocated {len(allocated_crawlers)} crawlers for plan '{plan.plan_id}'")
        return allocated_crawlers

    def spawn_crawler(
        self,
        capabilities: list[CrawlerCapability] | CrawlerCapability,
        name: Optional[str] = None,
    ) -> BaseCrawler:
        """Dynamically spawn a single crawler instance declaring specific capabilities."""
        caps = [capabilities] if isinstance(capabilities, CrawlerCapability) else list(capabilities)
        if not caps:
            caps = [CrawlerCapability.WEB_SEARCH]

        crawler_id = f"crawler.{caps[0].value.lower()}.{uuid.uuid4().hex[:6]}"
        crawler = self._create_crawler_for_capabilities(caps, crawler_id=crawler_id, name=name)
        self.registry.register_crawler_instance(crawler)
        logger.info(f"Dynamically spawned single crawler '{crawler.id}' with capabilities: {[c.value for c in caps]}")
        return crawler

    def spawn_replacement_crawler(
        self,
        failed_crawler_id: str,
        required_capabilities: list[CrawlerCapability],
    ) -> BaseCrawler:
        """
        Dynamically spawn a replacement crawler when a previous crawler fails or is unresponsive.
        Terminates the failed crawler in the registry and creates a fresh instance.
        """
        logger.warning(f"Spawning replacement crawler for failed instance '{failed_crawler_id}'")
        try:
            old = self.registry.get_crawler(failed_crawler_id)
            old.terminate()
        except Exception:
            pass

        replacement = self.spawn_crawler(
            capabilities=required_capabilities,
            name=f"Replacement for {failed_crawler_id}",
        )
        return replacement

    def spawn_additional_crawlers(
        self,
        needed_capabilities: list[CrawlerCapability],
        count: int = 1,
    ) -> list[BaseCrawler]:
        """
        Dynamically scale out the workforce by spawning additional crawlers later
        when the Researcher identifies evidence gaps or expanding research questions.
        """
        spawned: list[BaseCrawler] = []
        caps_list = needed_capabilities or [CrawlerCapability.WEB_SEARCH]
        for i in range(count):
            cap = caps_list[i % len(caps_list)]
            crawler = self.spawn_crawler(capabilities=[cap, CrawlerCapability.WEB_FETCH])
            spawned.append(crawler)
        logger.info(f"Spawned {len(spawned)} additional crawlers on-demand for workforce expansion")
        return spawned

    def spawn_crawlers_for_tasks(
        self,
        tasks: list[CrawlerTask],
        max_crawlers: int = 5,
    ) -> list[BaseCrawler]:
        """
        Spawn or allocate crawlers to cover a batch of CrawlerTasks based on capability matching.
        """
        allocated: list[BaseCrawler] = []
        for task in tasks:
            if len(allocated) >= max_crawlers:
                break
            # Find eligible idle crawler
            eligible = self.registry.find_eligible_crawlers(
                capabilities=task.required_capabilities,
                idle_only=True,
            )
            if eligible:
                chosen = eligible[0]
                if chosen not in allocated:
                    allocated.append(chosen)
            else:
                new_crawler = self.spawn_crawler(capabilities=task.required_capabilities)
                allocated.append(new_crawler)

        return allocated

    def _create_crawler_for_capability(self, capability: CrawlerCapability, index: int = 1) -> BaseCrawler:
        """Instantiate a standard foundational crawler instance for a given capability."""
        return self._create_crawler_for_capabilities(
            capabilities=[capability, CrawlerCapability.WEB_FETCH],
            crawler_id=f"crawler.{capability.value.lower()}.{index}",
            name=f"Specialist Crawler [{capability.value}]",
        )

    def _create_crawler_for_capabilities(
        self,
        capabilities: list[CrawlerCapability],
        crawler_id: Optional[str] = None,
        name: Optional[str] = None,
    ) -> BaseCrawler:
        """Instantiate a crawler instance configured with multiple capabilities."""
        from workers.crawler.worker import CrawlerWorker
        return CrawlerWorker(
            crawler_id=crawler_id,
            name=name or f"Specialist Crawler [{capabilities[0].value}]",
            capabilities=capabilities,
        )
