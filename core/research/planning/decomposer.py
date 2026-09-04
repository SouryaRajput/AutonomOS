from __future__ import annotations

import logging
import re
from typing import Optional
import uuid

from core.research.contracts.plan import ResearchPlan
from core.research.contracts.question import ResearchQuestion
from core.research.contracts.request import ResearchRequest
from core.research.types import CrawlerCapability, ResearchMode, ResearchQuestionStatus, SourceType

logger = logging.getLogger("AutonomOS.Research.Decomposer")


class ResearchDecomposer:
    """
    Decomposes a ResearchRequest into structured ResearchQuestions and creates an initial ResearchPlan.
    """

    @classmethod
    def decompose_request(cls, request: ResearchRequest) -> tuple[list[ResearchQuestion], ResearchPlan]:
        """
        Decompose incoming ResearchRequest into structured sub-questions and formulate execution plan.
        """
        plan_id = f"rplan-{uuid.uuid4().hex[:8]}"
        questions: list[ResearchQuestion] = []

        if request.questions:
            # Explicit questions provided
            for i, q_text in enumerate(request.questions):
                q_id = f"q-{i+1}"
                caps = cls._infer_capabilities(q_text, request.mode)
                target_sources = cls._infer_target_sources(q_text)
                rq = ResearchQuestion(
                    question_id=q_id,
                    question_text=q_text.strip(),
                    request_id=request.request_id,
                    plan_id=plan_id,
                    status=ResearchQuestionStatus.UNANSWERED,
                    required_capabilities=caps,
                    target_source_types=target_sources,
                )
                questions.append(rq)
        else:
            # Automatic heuristic decomposition from objective
            decomposed_texts = cls._auto_decompose_objective(request.objective, request.mode)
            for i, q_text in enumerate(decomposed_texts):
                q_id = f"q-{i+1}"
                caps = cls._infer_capabilities(q_text, request.mode)
                target_sources = cls._infer_target_sources(q_text)
                rq = ResearchQuestion(
                    question_id=q_id,
                    question_text=q_text.strip(),
                    request_id=request.request_id,
                    plan_id=plan_id,
                    status=ResearchQuestionStatus.UNANSWERED,
                    required_capabilities=caps,
                    target_source_types=target_sources,
                )
                questions.append(rq)

        planned_steps = [
            "1. Inspect Project Context & Persistent Memory",
            "2. Allocate and Provision Crawler Workforce",
            "3. Execute Crawler Tasks and Gather Reports",
            "4. Collect and Deduplicate Structured Evidence",
            "5. Perform Coverage and Contradiction Verification",
            "6. Synthesize Research Package and Deliver to Manager",
        ]

        plan = ResearchPlan(
            plan_id=plan_id,
            request_id=request.request_id,
            objective=request.objective,
            mode=request.mode,
            scope=request.scope,
            questions=questions,
            planned_steps=planned_steps,
            allocated_crawler_count=1,
            correlation_id=request.correlation_id,
        )

        return questions, plan

    @classmethod
    def _auto_decompose_objective(cls, objective: str, mode: ResearchMode) -> list[str]:
        """Generate structured analytical sub-questions for an objective."""
        cleaned = objective.strip()
        
        # Check comparison pattern: "X vs Y" or "compare X and Y"
        vs_match = re.search(r"(\b[\w\.\-]+(?:\s+[\w\.\-]+)*)\s+(?:vs\.?|versus|compared\s+to|and)\s+([\w\.\-]+(?:\s+[\w\.\-]+)*)", cleaned, re.IGNORECASE)
        if "vs" in cleaned.lower() or "compare" in cleaned.lower() and vs_match:
            parts = re.split(r"\bvs\.?\b|\bversus\b|\bcompare\b", cleaned, flags=re.IGNORECASE)
            term_a = parts[0].strip() if len(parts) > 0 else "Option A"
            term_b = parts[1].strip() if len(parts) > 1 else "Option B"
            questions = [
                f"What are the core architectural capabilities and limitations of {term_a}?",
                f"What are the core architectural capabilities and limitations of {term_b}?",
                f"What are the verified performance tradeoffs, compatibility, and community benchmarks comparing {term_a} and {term_b}?",
            ]
            if mode == ResearchMode.DEEP:
                questions.append(f"Are there known failure modes or production edge cases reported for {term_a} and {term_b}?")
            return questions

        questions = [
            f"What are the official specifications, supported APIs, and features for '{cleaned}'?",
            f"What are the architectural best practices, configuration requirements, and compatibility constraints for '{cleaned}'?",
        ]
        if mode == ResearchMode.DEEP:
            questions.append(f"What are the known failure modes, security advisories, or performance limitations for '{cleaned}'?")
        return questions

    @classmethod
    def _infer_capabilities(cls, question_text: str, mode: ResearchMode) -> list[CrawlerCapability]:
        """Infer required crawler capabilities from question text."""
        caps: list[CrawlerCapability] = []
        q_lower = question_text.lower()
        if "repo" in q_lower or "code" in q_lower or "github" in q_lower:
            caps.append(CrawlerCapability.REPOSITORY_INSPECTION)
        if "fetch" in q_lower or "url" in q_lower or "page" in q_lower or "http" in q_lower:
            caps.append(CrawlerCapability.WEB_FETCH)
        if "memory" in q_lower or "project" in q_lower:
            caps.append(CrawlerCapability.PROJECT_MEMORY_LOOKUP)
        if not caps:
            caps.append(CrawlerCapability.WEB_SEARCH)
        return caps

    @classmethod
    def _infer_target_sources(cls, question_text: str) -> list[SourceType]:
        """Infer target source types for a question."""
        q_lower = question_text.lower()
        targets: list[SourceType] = [SourceType.OFFICIAL_DOCUMENTATION, SourceType.PRIMARY_SOURCE]
        if "academic" in q_lower or "benchmark" in q_lower:
            targets.append(SourceType.ACADEMIC)
        if "forum" in q_lower or "community" in q_lower or "reddit" in q_lower:
            targets.append(SourceType.COMMUNITY)
        return targets
