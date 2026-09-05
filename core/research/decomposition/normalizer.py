from __future__ import annotations

import re
from typing import Any, Optional

from core.research.decomposition.model import (
    ResearchDecomposition,
    ResearchDependency,
    ResearchSubQuestion,
)
from core.research.decomposition.types import (
    ResearchDependencyType,
    SubQuestionPriority,
    SubQuestionStatus,
    SubQuestionType,
)


def canonicalize_text(text: str) -> str:
    """
    Produce canonical representation of a text string for exact/canonical deduplication:
    - Normalizes internal whitespace sequences to a single space
    - Lowercases text
    - Strips leading and trailing punctuation and whitespace
    """
    if not text or not isinstance(text, str):
        return ""
    norm = re.sub(r"\s+", " ", text).strip().lower()
    norm = norm.strip("?.!,;:\"' \t\n\r`~-")
    return norm


def normalize_whitespace(text: str) -> str:
    """Strip leading/trailing whitespace and collapse internal whitespace sequences."""
    if not text or not isinstance(text, str):
        return ""
    return re.sub(r"\s+", " ", text).strip()


class DecompositionNormalizer:
    """
    Deterministic preprocessor for ResearchDecomposition proposals.
    Transforms raw or LLM-proposed decompositions into a canonical, cleaned,
    deduplicated, and predictably typed structure.
    Guaranteed idempotent: normalize(normalize(decomp)) == normalize(decomp).
    """

    @classmethod
    def normalize(
        cls,
        decomposition: ResearchDecomposition,
        record_trace: bool = True,
    ) -> ResearchDecomposition:
        """
        Clean and normalize all text, enums, lists, and dependency collections
        within a ResearchDecomposition.
        """
        if decomposition is None:
            raise TypeError("Cannot normalize None; expected a ResearchDecomposition instance.")
        if not isinstance(decomposition, ResearchDecomposition):
            raise TypeError(f"Expected ResearchDecomposition instance, got {type(decomposition).__name__}")

        actions: list[str] = []

        # 1. Root IDs & Texts
        decomposition.decomposition_id = decomposition.decomposition_id.strip() if decomposition.decomposition_id else ""
        decomposition.research_request_id = decomposition.research_request_id.strip() if decomposition.research_request_id else ""
        if decomposition.research_intent_id:
            decomposition.research_intent_id = decomposition.research_intent_id.strip() or None

        norm_obj = normalize_whitespace(decomposition.objective)
        if norm_obj != decomposition.objective:
            actions.append("normalized_root_objective_whitespace")
            decomposition.objective = norm_obj

        norm_root_q = normalize_whitespace(decomposition.root_question)
        if norm_root_q != decomposition.root_question:
            actions.append("normalized_root_question_whitespace")
            decomposition.root_question = norm_root_q

        # 2. Coverage Requirements & Unresolved Decisions
        decomposition.coverage_requirements = cls._clean_string_list(
            decomposition.coverage_requirements, actions, "coverage_requirements"
        )
        decomposition.unresolved_decisions = cls._clean_string_list(
            decomposition.unresolved_decisions, actions, "unresolved_decisions"
        )

        # 3. Normalize Sub-Questions
        for sq in decomposition.sub_questions:
            cls._normalize_sub_question(sq, decomposition.decomposition_id, actions)

        # 4. Normalize Explicit Dependencies
        decomposition.dependencies = cls._normalize_dependencies(
            decomposition.dependencies, actions
        )

        if actions and record_trace:
            decomposition.add_trace(f"Normalizer applied {len(actions)} cleanup actions: {', '.join(actions[:5])}")

        return decomposition

    @classmethod
    def _normalize_sub_question(
        cls,
        sq: ResearchSubQuestion,
        decomposition_id: str,
        actions: list[str],
    ) -> None:
        """Normalize an individual sub-question's strings, enums, and dependencies."""
        if not isinstance(sq, ResearchSubQuestion):
            return

        sq.sub_question_id = sq.sub_question_id.strip() if sq.sub_question_id else ""
        if not sq.decomposition_id:
            sq.decomposition_id = decomposition_id
        else:
            sq.decomposition_id = sq.decomposition_id.strip()

        if sq.parent_id:
            sq.parent_id = sq.parent_id.strip() or None

        # Text fields
        sq.question = normalize_whitespace(sq.question)
        sq.objective = normalize_whitespace(sq.objective)
        sq.rationale = normalize_whitespace(sq.rationale)

        # Enums coercion if strings were passed
        if isinstance(sq.sub_question_type, str) and not isinstance(sq.sub_question_type, SubQuestionType):
            try:
                sq.sub_question_type = SubQuestionType(sq.sub_question_type.upper())
            except ValueError:
                sq.sub_question_type = SubQuestionType.FACT_FINDING

        if isinstance(sq.priority, str) and not isinstance(sq.priority, SubQuestionPriority):
            try:
                sq.priority = SubQuestionPriority(sq.priority.upper())
            except ValueError:
                sq.priority = SubQuestionPriority.MEDIUM

        if isinstance(sq.status, str) and not isinstance(sq.status, SubQuestionStatus):
            try:
                sq.status = SubQuestionStatus(sq.status.upper())
            except ValueError:
                sq.status = SubQuestionStatus.PENDING

        # String lists
        sq.assumptions = cls._clean_string_list(sq.assumptions, actions, f"sq_{sq.sub_question_id}_assumptions")
        sq.ambiguities = cls._clean_string_list(sq.ambiguities, actions, f"sq_{sq.sub_question_id}_ambiguities")

        # Dependencies list (preserve order, strip, deduplicate, drop self)
        seen_deps: set[str] = set()
        clean_deps: list[str] = []
        for d in sq.dependencies:
            if not isinstance(d, str):
                continue
            cd = d.strip()
            if cd and cd != sq.sub_question_id and cd not in seen_deps:
                seen_deps.add(cd)
                clean_deps.append(cd)
        sq.dependencies = clean_deps

    @classmethod
    def _normalize_dependencies(
        cls,
        dependencies: list[ResearchDependency],
        actions: list[str],
    ) -> list[ResearchDependency]:
        """Normalize explicit dependencies and deduplicate directed edges."""
        seen_edges: set[tuple[str, str]] = set()
        cleaned: list[ResearchDependency] = []

        for dep in dependencies:
            if not isinstance(dep, ResearchDependency):
                continue
            dep.dependency_id = dep.dependency_id.strip() if dep.dependency_id else ""
            dep.prerequisite_id = dep.prerequisite_id.strip() if dep.prerequisite_id else ""
            dep.dependent_id = dep.dependent_id.strip() if dep.dependent_id else ""
            dep.rationale = normalize_whitespace(dep.rationale)

            # Enum coercion
            if isinstance(dep.dependency_type, str) and not isinstance(dep.dependency_type, ResearchDependencyType):
                try:
                    dep.dependency_type = ResearchDependencyType(dep.dependency_type.upper())
                except ValueError:
                    dep.dependency_type = ResearchDependencyType.PREREQUISITE

            edge = (dep.prerequisite_id, dep.dependent_id)
            if edge in seen_edges:
                actions.append(f"removed_duplicate_dependency:{edge[0]}->{edge[1]}")
                continue
            seen_edges.add(edge)
            cleaned.append(dep)

        return cleaned

    @classmethod
    def _clean_string_list(
        cls,
        items: list[str],
        actions: list[str],
        context_name: str,
    ) -> list[str]:
        """Strip whitespace, drop empty strings, and deduplicate preserving order."""
        if not items:
            return []
        seen: set[str] = set()
        cleaned: list[str] = []
        for item in items:
            if not isinstance(item, str):
                continue
            c = normalize_whitespace(item)
            if not c:
                continue
            key = c.lower()
            if key in seen:
                actions.append(f"removed_duplicate_string_in_{context_name}")
                continue
            seen.add(key)
            cleaned.append(c)
        return cleaned
