from __future__ import annotations

import logging
import re
from typing import Optional
import uuid

from core.research.contracts.crawler_task import CrawlerTask
from core.research.contracts.plan import ResearchPlan
from core.research.contracts.question import ResearchQuestion
from core.research.types import CrawlerCapability, CrawlerTaskStatus

logger = logging.getLogger("AutonomOS.Research.TaskGenerator")


class CrawlerTaskGenerator:
    """
    Translates ResearchQuestions and ResearchScope into granular, bounded CrawlerTasks.
    Attaches full lineage back to the creating ResearchRequest and ResearchPlan.
    """

    @classmethod
    def generate_tasks_for_plan(cls, plan: ResearchPlan) -> list[CrawlerTask]:
        """
        Generate all crawler tasks needed to investigate questions in a ResearchPlan.
        """
        tasks: list[CrawlerTask] = []
        max_searches = plan.scope.max_searches
        search_count = 0

        for q in plan.questions:
            queries = cls._generate_queries_for_question(q, plan)
            for i, query in enumerate(queries):
                if search_count >= max_searches:
                    break

                for cap in q.required_capabilities:
                    task_id = f"ctask-{q.question_id}-{cap.value.lower()[:4]}-{i+1}"
                    task = CrawlerTask(
                        task_id=task_id,
                        request_id=plan.request_id,
                        plan_id=plan.plan_id,
                        question_id=q.question_id,
                        query_or_target=query,
                        required_capability=cap,
                        parameters={
                            "allowed_domains": plan.scope.allowed_domains,
                            "excluded_domains": plan.scope.excluded_domains,
                            "recency_days": plan.scope.recency_days,
                            "limit": 5,
                        },
                        status=CrawlerTaskStatus.PENDING,
                        correlation_id=plan.correlation_id,
                        timeout_seconds=plan.scope.timeout_seconds,
                    )
                    tasks.append(task)
                    search_count += 1

        plan.crawler_tasks = tasks
        return tasks

    @classmethod
    def _generate_queries_for_question(cls, question: ResearchQuestion, plan: ResearchPlan) -> list[str]:
        """Formulate effective query strings with optional domain scoping."""
        raw_text = question.question_text
        
        # Remove trailing question mark and conversational prefixes
        cleaned = re.sub(r"^(what\s+is|what\s+are|how\s+does|how\s+to|why\s+does|does|is|are)\s+", "", raw_text, flags=re.IGNORECASE).rstrip("?").strip()
        if not cleaned:
            cleaned = raw_text.rstrip("?").strip()

        queries: list[str] = []
        
        # Primary query: raw text
        queries.append(raw_text.rstrip("?"))
        
        # Secondary query: cleaned text + objective context
        if plan.scope.allowed_domains:
            for domain in plan.scope.allowed_domains[:2]:
                queries.append(f"{cleaned} site:{domain}")
        else:
            queries.append(f"{cleaned} documentation guide")

        # Deduplicate preserving order
        seen: set[str] = set()
        deduped: list[str] = []
        for q in queries:
            if q.lower() not in seen:
                seen.add(q.lower())
                deduped.append(q)

        return deduped
