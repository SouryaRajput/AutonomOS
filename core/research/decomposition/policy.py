from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, TYPE_CHECKING

from core.research.decomposition.types import SubQuestionPriority

if TYPE_CHECKING:
    from core.research.decomposition.model import ResearchDecomposition

# -----------------------------------------------------------------------------
# Standard Policy Constants
# -----------------------------------------------------------------------------
DEFAULT_MIN_SUB_QUESTIONS: int = 1
DEFAULT_TARGET_MIN_SUB_QUESTIONS: int = 3
DEFAULT_TARGET_MAX_SUB_QUESTIONS: int = 5
DEFAULT_TARGET_SUB_QUESTIONS: tuple[int, int] = (
    DEFAULT_TARGET_MIN_SUB_QUESTIONS,
    DEFAULT_TARGET_MAX_SUB_QUESTIONS,
)
MAX_SUB_QUESTIONS: int = 8

DEFAULT_MIN_QUESTION_LEN: int = 10
DEFAULT_MAX_QUESTION_LEN: int = 500
DEFAULT_MIN_OBJECTIVE_LEN: int = 10
DEFAULT_MAX_OBJECTIVE_LEN: int = 1000
DEFAULT_MAX_RATIONALE_LEN: int = 1000
DEFAULT_MAX_DEPENDENCIES_PER_QUESTION: int = 10
DEFAULT_MAX_TOTAL_DEPENDENCIES: int = 30
DEFAULT_MAX_PAYLOAD_BYTES: int = 256 * 1024  # 256 KB


_PRIORITY_WEIGHTS: dict[SubQuestionPriority, int] = {
    SubQuestionPriority.CRITICAL: 4,
    SubQuestionPriority.HIGH: 3,
    SubQuestionPriority.MEDIUM: 2,
    SubQuestionPriority.LOW: 1,
}


@dataclass
class DecompositionPolicy:
    """
    Configurable deterministic policy governing research decomposition proposals.
    Enforces question counts, target ranges, length bounds, dependency safety limits,
    and payload size constraints.
    """
    min_sub_questions: int = DEFAULT_MIN_SUB_QUESTIONS
    target_min_sub_questions: int = DEFAULT_TARGET_MIN_SUB_QUESTIONS
    target_max_sub_questions: int = DEFAULT_TARGET_MAX_SUB_QUESTIONS
    max_sub_questions: int = MAX_SUB_QUESTIONS
    min_question_len: int = DEFAULT_MIN_QUESTION_LEN
    max_question_len: int = DEFAULT_MAX_QUESTION_LEN
    min_objective_len: int = DEFAULT_MIN_OBJECTIVE_LEN
    max_objective_len: int = DEFAULT_MAX_OBJECTIVE_LEN
    max_rationale_len: int = DEFAULT_MAX_RATIONALE_LEN
    max_dependencies_per_question: int = DEFAULT_MAX_DEPENDENCIES_PER_QUESTION
    max_total_dependencies: int = DEFAULT_MAX_TOTAL_DEPENDENCIES
    max_payload_bytes: int = DEFAULT_MAX_PAYLOAD_BYTES
    allow_reduction_if_over_max: bool = False
    allow_exact_deduplication: bool = False
    reduction_strategy: str = "priority_first"  # "priority_first" | "first_n"

    @property
    def target_sub_questions(self) -> tuple[int, int]:
        """Return the target range as a (min, max) tuple."""
        return (self.target_min_sub_questions, self.target_max_sub_questions)

    def reduce_to_limit(
        self,
        decomposition: ResearchDecomposition,
    ) -> tuple[ResearchDecomposition, list[str]]:
        """
        Deterministically reduce sub-questions in a decomposition to fit within max_sub_questions.
        Uses reduction_strategy ('priority_first' by default).
        Prunes references to dropped sub-questions from remaining sub-questions and dependencies.
        Returns the modified decomposition and a list of reduction audit actions.
        """
        actions: list[str] = []
        if len(decomposition.sub_questions) <= self.max_sub_questions:
            return decomposition, actions

        initial_count = len(decomposition.sub_questions)
        excess_count = initial_count - self.max_sub_questions

        if self.reduction_strategy == "priority_first":
            # Rank sub-questions by priority weight descending, preserving original index as tie-breaker
            indexed_sqs = list(enumerate(decomposition.sub_questions))
            # Sort by priority weight desc, then index asc
            sorted_indexed = sorted(
                indexed_sqs,
                key=lambda item: (
                    _PRIORITY_WEIGHTS.get(
                        item[1].priority
                        if isinstance(item[1].priority, SubQuestionPriority)
                        else SubQuestionPriority.MEDIUM,
                        2,
                    ),
                    -item[0],  # higher priority first, tie-breaker lower index first
                ),
                reverse=True,
            )
            # Select top max_sub_questions, then restore original order
            selected_indexed = sorted_indexed[: self.max_sub_questions]
            selected_indexed.sort(key=lambda item: item[0])
            kept_sqs = [sq for _, sq in selected_indexed]
        else:
            # "first_n": simple truncation
            kept_sqs = decomposition.sub_questions[: self.max_sub_questions]

        kept_ids = {sq.sub_question_id for sq in kept_sqs}
        dropped_sqs = [sq for sq in decomposition.sub_questions if sq.sub_question_id not in kept_ids]
        dropped_ids = {sq.sub_question_id for sq in dropped_sqs}

        # Update decomposition's sub-question list
        decomposition.sub_questions = kept_sqs

        # Prune dependencies pointing to dropped sub-questions
        for sq in decomposition.sub_questions:
            old_deps = list(sq.dependencies)
            sq.dependencies = [dep_id for dep_id in sq.dependencies if dep_id not in dropped_ids]
            pruned_deps = set(old_deps) - set(sq.dependencies)
            if pruned_deps:
                actions.append(
                    f"Pruned dropped dependencies {sorted(pruned_deps)} from sub-question '{sq.sub_question_id}'"
                )

        # Prune explicit ResearchDependency records
        old_dep_count = len(decomposition.dependencies)
        decomposition.dependencies = [
            dep
            for dep in decomposition.dependencies
            if dep.prerequisite_id in kept_ids and dep.dependent_id in kept_ids
        ]
        pruned_explicit = old_dep_count - len(decomposition.dependencies)
        if pruned_explicit > 0:
            actions.append(f"Pruned {pruned_explicit} explicit dependency records referencing dropped sub-questions")

        action_summary = (
            f"Deterministically reduced decomposition from {initial_count} to {len(kept_sqs)} sub-questions "
            f"using strategy '{self.reduction_strategy}' (dropped {excess_count}: {sorted(dropped_ids)})"
        )
        actions.insert(0, action_summary)
        decomposition.add_trace(action_summary)

        return decomposition, actions

    def to_dict(self) -> dict[str, Any]:
        return {
            "min_sub_questions": self.min_sub_questions,
            "target_min_sub_questions": self.target_min_sub_questions,
            "target_max_sub_questions": self.target_max_sub_questions,
            "max_sub_questions": self.max_sub_questions,
            "min_question_len": self.min_question_len,
            "max_question_len": self.max_question_len,
            "min_objective_len": self.min_objective_len,
            "max_objective_len": self.max_objective_len,
            "max_rationale_len": self.max_rationale_len,
            "max_dependencies_per_question": self.max_dependencies_per_question,
            "max_total_dependencies": self.max_total_dependencies,
            "max_payload_bytes": self.max_payload_bytes,
            "allow_reduction_if_over_max": self.allow_reduction_if_over_max,
            "allow_exact_deduplication": self.allow_exact_deduplication,
            "reduction_strategy": self.reduction_strategy,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> DecompositionPolicy:
        return cls(
            min_sub_questions=int(data.get("min_sub_questions", DEFAULT_MIN_SUB_QUESTIONS)),
            target_min_sub_questions=int(data.get("target_min_sub_questions", DEFAULT_TARGET_MIN_SUB_QUESTIONS)),
            target_max_sub_questions=int(data.get("target_max_sub_questions", DEFAULT_TARGET_MAX_SUB_QUESTIONS)),
            max_sub_questions=int(data.get("max_sub_questions", MAX_SUB_QUESTIONS)),
            min_question_len=int(data.get("min_question_len", DEFAULT_MIN_QUESTION_LEN)),
            max_question_len=int(data.get("max_question_len", DEFAULT_MAX_QUESTION_LEN)),
            min_objective_len=int(data.get("min_objective_len", DEFAULT_MIN_OBJECTIVE_LEN)),
            max_objective_len=int(data.get("max_objective_len", DEFAULT_MAX_OBJECTIVE_LEN)),
            max_rationale_len=int(data.get("max_rationale_len", DEFAULT_MAX_RATIONALE_LEN)),
            max_dependencies_per_question=int(data.get("max_dependencies_per_question", DEFAULT_MAX_DEPENDENCIES_PER_QUESTION)),
            max_total_dependencies=int(data.get("max_total_dependencies", DEFAULT_MAX_TOTAL_DEPENDENCIES)),
            max_payload_bytes=int(data.get("max_payload_bytes", DEFAULT_MAX_PAYLOAD_BYTES)),
            allow_reduction_if_over_max=bool(data.get("allow_reduction_if_over_max", False)),
            allow_exact_deduplication=bool(data.get("allow_exact_deduplication", False)),
            reduction_strategy=str(data.get("reduction_strategy", "priority_first")),
        )
