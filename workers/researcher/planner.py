from __future__ import annotations

import re
from typing import Any, Optional
import uuid

from core.models import Task
from workers.researcher.model import (
    ResearchPlan,
    ResearchQuestion,
    ResearchScope,
    ResearchTaskSpec,
    Source,
)
from workers.researcher.types import (
    ResearchMode,
    ResearchQuestionStatus,
    SourceType,
)


class ResearchPlanner:
    """
    Decomposes research tasks into structured specifications, sub-questions,
    and bounded execution plans.
    """

    @classmethod
    def parse_task_spec(cls, task: Task) -> ResearchTaskSpec:
        """Parse and normalize a task into a structured ResearchTaskSpec."""
        meta = dict(task.metadata or {})
        objective = (task.objective or task.title or "").strip()

        # Parse Mode
        mode_str = str(meta.get("mode", meta.get("research_mode", "STANDARD"))).upper()
        try:
            mode = ResearchMode(mode_str)
        except ValueError:
            mode = ResearchMode.STANDARD

        # Configure scope bounds based on mode
        if mode == ResearchMode.QUICK:
            default_max_searches = 3
            default_max_fetches = 5
            default_max_sources = 8
            default_max_inf = 3
        elif mode == ResearchMode.DEEP:
            default_max_searches = 10
            default_max_fetches = 15
            default_max_sources = 25
            default_max_inf = 8
        else:  # STANDARD
            default_max_searches = 6
            default_max_fetches = 10
            default_max_sources = 15
            default_max_inf = 5

        # Parse domain restrictions & preferences
        allowed_domains = list(meta.get("allowed_domains", meta.get("domain_restrictions", [])))
        excluded_domains = list(meta.get("excluded_domains", []))
        recency_days = meta.get("recency_days")
        if recency_days is not None:
            try:
                recency_days = int(recency_days)
            except (ValueError, TypeError):
                recency_days = None

        preferred_source_types: list[SourceType] = []
        for st in meta.get("preferred_source_types", []):
            try:
                preferred_source_types.append(SourceType(st))
            except (ValueError, TypeError):
                pass

        scope = ResearchScope(
            allowed_domains=allowed_domains,
            excluded_domains=excluded_domains,
            preferred_source_types=preferred_source_types,
            recency_days=recency_days,
            max_searches=int(meta.get("max_searches", default_max_searches)),
            max_fetches=int(meta.get("max_fetches", default_max_fetches)),
            max_sources=int(meta.get("max_sources", default_max_sources)),
            max_inference_calls=int(meta.get("max_inference_calls", default_max_inf)),
            cost_limit=float(meta.get("cost_limit", 1.0)),
        )

        # Parse or Decompose Questions
        raw_questions = meta.get("questions", meta.get("research_questions", []))
        questions: list[ResearchQuestion] = []

        if raw_questions and isinstance(raw_questions, list):
            for i, q in enumerate(raw_questions):
                if isinstance(q, str):
                    questions.append(
                        ResearchQuestion(
                            question_id=f"q-{i+1}",
                            question_text=q.strip(),
                            status=ResearchQuestionStatus.UNANSWERED,
                        )
                    )
                elif isinstance(q, dict):
                    questions.append(ResearchQuestion.from_dict(q))

        # Also inspect success_criteria for implicit questions
        if not questions and task.success_criteria:
            for i, crit in enumerate(task.success_criteria):
                desc = crit.get("description") if isinstance(crit, dict) else str(crit)
                if desc:
                    questions.append(
                        ResearchQuestion(
                            question_id=f"q-{i+1}",
                            question_text=desc.strip(),
                            status=ResearchQuestionStatus.UNANSWERED,
                        )
                    )

        # If still no specific questions, decompose objective
        if not questions:
            questions = cls._decompose_objective(objective)

        constraints = list(meta.get("constraints", []))
        req_format = str(meta.get("required_output_format", "markdown_report"))
        pref_style = str(meta.get("preferred_style", "analytical"))

        return ResearchTaskSpec(
            objective=objective,
            mode=mode,
            scope=scope,
            questions=questions,
            constraints=constraints,
            required_output_format=req_format,
            preferred_style=pref_style,
            raw_task_metadata=meta,
        )

    @classmethod
    def _decompose_objective(cls, objective: str) -> list[ResearchQuestion]:
        """Deterministically decompose a high-level research objective into sub-questions."""
        questions: list[ResearchQuestion] = []
        clean_obj = objective.strip()
        if not clean_obj:
            return [
                ResearchQuestion(
                    question_id="q-1",
                    question_text="What are the key technical requirements and options?",
                    status=ResearchQuestionStatus.UNANSWERED,
                )
            ]

        # Primary core question
        questions.append(
            ResearchQuestion(
                question_id="q-1",
                question_text=f"What are the authoritative capabilities, specifications, or status of {clean_obj}?",
                status=ResearchQuestionStatus.UNANSWERED,
            )
        )

        # Comparison / Tradeoffs question if comparison is implied
        if any(w in clean_obj.lower() for w in ("compare", "vs", "versus", "choice", "which", "alternative")):
            questions.append(
                ResearchQuestion(
                    question_id="q-2",
                    question_text=f"What are the major tradeoffs, limitations, and operational concerns for {clean_obj}?",
                    status=ResearchQuestionStatus.UNANSWERED,
                )
            )
            questions.append(
                ResearchQuestion(
                    question_id="q-3",
                    question_text=f"What is the recommended choice based on project compatibility and evidence?",
                    status=ResearchQuestionStatus.UNANSWERED,
                )
            )
        else:
            questions.append(
                ResearchQuestion(
                    question_id="q-2",
                    question_text=f"What are the known limitations, compatibility constraints, and edge cases?",
                    status=ResearchQuestionStatus.UNANSWERED,
                )
            )

        return questions

    @classmethod
    def create_plan(cls, spec: ResearchTaskSpec) -> ResearchPlan:
        """Construct a structured research plan from a specification."""
        plan_id = f"rplan-{uuid.uuid4().hex[:8]}"

        steps = [
            "1. Inspect Project Context & Existing Memory",
            "2. Gather Candidate Sources & Literature via Web/Local Tools",
            "3. Analyze and Extract Claims from Fetched Sources",
            "4. Cross-Check and Detect Contradictions across Sources",
            "5. Synthesize Findings & Identify Knowledge Gaps",
            "6. Formulate Recommendations & Produce Research Report",
        ]

        if spec.mode == ResearchMode.DEEP:
            steps.insert(4, "4b. Perform Targeted Follow-up Searches on Knowledge Gaps")

        return ResearchPlan(
            plan_id=plan_id,
            objective=spec.objective,
            mode=spec.mode,
            scope=spec.scope,
            questions=spec.questions,
            planned_steps=steps,
            current_step_index=0,
        )

    @classmethod
    def generate_search_queries(
        cls,
        question: ResearchQuestion,
        spec: ResearchTaskSpec,
        existing_queries: Optional[set[str]] = None,
    ) -> list[str]:
        """Generate focused, domain-aware search queries for a research question."""
        seen = existing_queries or set()
        queries: list[str] = []

        raw_q = question.question_text
        # Clean question into core search terms
        clean_terms = re.sub(r"[^\w\s\-\.]", " ", raw_q)
        tokens = [w for w in clean_terms.split() if len(w) > 2 and w.lower() not in (
            "what", "where", "which", "when", "does", "have", "with", "from", "that", "this", "authoritative", "status", "specifications"
        )]
        core_query = " ".join(tokens[:8])

        if not core_query:
            core_query = spec.objective

        # Add domain filter if restricted
        domain_suffix = ""
        if spec.scope.allowed_domains:
            # Use primary allowed domain
            domain_suffix = f" site:{spec.scope.allowed_domains[0]}"

        # Primary query
        primary = f"{core_query}{domain_suffix}".strip()
        if primary not in seen:
            queries.append(primary)
            seen.add(primary)

        # Secondary query with documentation or specification focus
        if spec.mode in (ResearchMode.STANDARD, ResearchMode.DEEP):
            doc_query = f"{core_query} documentation official{domain_suffix}".strip()
            if doc_query not in seen and len(queries) < spec.scope.max_searches:
                queries.append(doc_query)
                seen.add(doc_query)

        return queries
