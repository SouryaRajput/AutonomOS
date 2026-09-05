from __future__ import annotations

import json
import re
from typing import Optional

from core.research.decomposition.model import (
    ResearchDecomposition,
    ResearchDependency,
    ResearchSubQuestion,
)
from core.research.decomposition.normalizer import canonicalize_text
from core.research.decomposition.policy import DecompositionPolicy
from core.research.decomposition.types import SubQuestionPriority, SubQuestionStatus
from core.research.decomposition.validator import DecompositionValidationError


class DecompositionConstraintError(DecompositionValidationError):
    """Raised when a research decomposition proposal violates policy constraints."""
    pass


class DecompositionConstraintValidator:
    """
    Deterministic validation engine enforcing policy constraints on decomposition proposals:
    - Sub-question count bounds (MIN, MAX, TARGET)
    - Deterministic duplicate detection (exact and canonical)
    - Root alignment checks (sub-questions must not duplicate root objective/question)
    - Quality and substantive text requirements
    - Text length bounds (question, objective, rationale)
    - Dependency count and safety bounds (resolution, no self-dep, acyclicity)
    - Payload size bounds
    """

    @classmethod
    def validate(
        cls,
        decomposition: ResearchDecomposition,
        policy: Optional[DecompositionPolicy] = None,
        raise_on_error: bool = True,
    ) -> list[str]:
        """
        Validate a ResearchDecomposition against the provided or default DecompositionPolicy.
        Returns a list of error issues.
        If raise_on_error is True and issues exist, raises DecompositionConstraintError.
        """
        if policy is None:
            policy = DecompositionPolicy()

        issues: list[str] = []

        # 1. Sub-question count enforcement
        sq_count = len(decomposition.sub_questions)
        if sq_count < policy.min_sub_questions:
            issues.append(
                f"Decomposition contains {sq_count} sub-questions; "
                f"minimum required is {policy.min_sub_questions}."
            )
        elif sq_count > policy.max_sub_questions:
            if policy.allow_reduction_if_over_max:
                policy.reduce_to_limit(decomposition)
            else:
                issues.append(
                    f"Decomposition contains {sq_count} sub-questions; "
                    f"exceeds maximum allowed limit of {policy.max_sub_questions}."
                )

        # Ensure that removing optional questions does not leave an empty decomposition
        required_count = sum(1 for sq in decomposition.sub_questions if getattr(sq, "required", True))
        if sq_count >= policy.min_sub_questions and required_count < policy.min_sub_questions:
            issues.append(
                f"Decomposition has {required_count} required sub-questions; "
                f"at least {policy.min_sub_questions} required sub-question(s) must be present."
            )

        # 2. Duplicate Detection & Sub-question Quality Checks
        canonical_questions: dict[str, str] = {}   # canonical_text -> sq_id
        canonical_objectives: dict[str, str] = {}  # canonical_text -> sq_id
        sq_map: dict[str, ResearchSubQuestion] = {}

        # Canonicalize root objective and question for root alignment checks
        canon_root_obj = canonicalize_text(decomposition.objective)
        canon_root_q = canonicalize_text(decomposition.root_question)

        total_dependencies_count = len(decomposition.dependencies)

        for idx, sq in enumerate(decomposition.sub_questions):
            if not isinstance(sq, ResearchSubQuestion):
                issues.append(f"Sub-question at index {idx} is not an instance of ResearchSubQuestion.")
                continue

            sq_id = sq.sub_question_id.strip() if sq.sub_question_id else ""
            if not sq_id:
                issues.append(f"Sub-question at index {idx} has an empty sub_question_id.")
                continue

            if sq_id in sq_map:
                issues.append(f"Duplicate sub_question_id detected: '{sq_id}'.")
            else:
                sq_map[sq_id] = sq

            # Decomposition ID match
            if sq.decomposition_id and sq.decomposition_id != decomposition.decomposition_id:
                issues.append(
                    f"Sub-question '{sq_id}' decomposition_id '{sq.decomposition_id}' "
                    f"does not match parent decomposition '{decomposition.decomposition_id}'."
                )

            # --- Question Text Quality & Bounds ---
            q_text = sq.question.strip() if sq.question else ""
            if not q_text:
                issues.append(f"Sub-question '{sq_id}' has an empty question text.")
            else:
                if not re.search(r"[a-zA-Z0-9]", q_text):
                    issues.append(f"Sub-question '{sq_id}' question lacks substantive alphanumeric content.")
                elif len(q_text) < policy.min_question_len:
                    issues.append(
                        f"Sub-question '{sq_id}' question length ({len(q_text)}) is below "
                        f"minimum ({policy.min_question_len} chars)."
                    )
                elif len(q_text) > policy.max_question_len:
                    issues.append(
                        f"Sub-question '{sq_id}' question length ({len(q_text)}) exceeds "
                        f"maximum limit ({policy.max_question_len} chars)."
                    )

            # --- Objective Text Quality & Bounds ---
            obj_text = sq.objective.strip() if sq.objective else ""
            if not obj_text:
                issues.append(f"Sub-question '{sq_id}' has an empty objective.")
            else:
                if not re.search(r"[a-zA-Z0-9]", obj_text):
                    issues.append(f"Sub-question '{sq_id}' objective lacks substantive alphanumeric content.")
                elif len(obj_text) < policy.min_objective_len:
                    issues.append(
                        f"Sub-question '{sq_id}' objective length ({len(obj_text)}) is below "
                        f"minimum ({policy.min_objective_len} chars)."
                    )
                elif len(obj_text) > policy.max_objective_len:
                    issues.append(
                        f"Sub-question '{sq_id}' objective length ({len(obj_text)}) exceeds "
                        f"maximum limit ({policy.max_objective_len} chars)."
                    )

            # --- Rationale Bounds ---
            if sq.rationale and len(sq.rationale.strip()) > policy.max_rationale_len:
                issues.append(
                    f"Sub-question '{sq_id}' rationale length ({len(sq.rationale.strip())}) exceeds "
                    f"maximum limit ({policy.max_rationale_len} chars)."
                )

            # --- Priority & Status Validation ---
            if not isinstance(sq.priority, SubQuestionPriority):
                try:
                    SubQuestionPriority(str(sq.priority))
                except ValueError:
                    issues.append(f"Sub-question '{sq_id}' has invalid priority: {sq.priority}")

            if not isinstance(sq.status, SubQuestionStatus):
                try:
                    SubQuestionStatus(str(sq.status))
                except ValueError:
                    issues.append(f"Sub-question '{sq_id}' has invalid status: {sq.status}")

            # --- Duplicate Detection ---
            canon_q = canonicalize_text(q_text)
            if canon_q:
                if canon_q in canonical_questions:
                    other_id = canonical_questions[canon_q]
                    issues.append(
                        f"Duplicate sub-question detected: '{sq_id}' has question text "
                        f"canonically identical to '{other_id}'."
                    )
                else:
                    canonical_questions[canon_q] = sq_id

            canon_obj = canonicalize_text(obj_text)
            if canon_obj:
                if canon_obj in canonical_objectives:
                    other_id = canonical_objectives[canon_obj]
                    issues.append(
                        f"Duplicate sub-question objective detected: '{sq_id}' has objective "
                        f"canonically identical to '{other_id}'."
                    )
                else:
                    canonical_objectives[canon_obj] = sq_id

            # --- Root Alignment (Must not duplicate root question/objective) ---
            if canon_q:
                if canon_root_q and canon_q == canon_root_q:
                    issues.append(
                        f"Sub-question '{sq_id}' question is trivially identical to root question."
                    )
                if canon_root_obj and canon_q == canon_root_obj:
                    issues.append(
                        f"Sub-question '{sq_id}' question is trivially identical to root objective."
                    )

            if canon_obj:
                if canon_root_q and canon_obj == canon_root_q:
                    issues.append(
                        f"Sub-question '{sq_id}' objective is trivially identical to root question."
                    )
                if canon_root_obj and canon_obj == canon_root_obj:
                    issues.append(
                        f"Sub-question '{sq_id}' objective is trivially identical to root objective."
                    )

            # --- Dependencies per question bound ---
            if len(sq.dependencies) > policy.max_dependencies_per_question:
                issues.append(
                    f"Sub-question '{sq_id}' has {len(sq.dependencies)} dependencies; "
                    f"exceeds limit ({policy.max_dependencies_per_question})."
                )

        # 3. Explicit Dependency Limits
        if total_dependencies_count > policy.max_total_dependencies:
            issues.append(
                f"Decomposition contains {total_dependencies_count} explicit dependencies; "
                f"exceeds limit ({policy.max_total_dependencies})."
            )

        # 4. Dependency Safety & Resolution Checks
        dep_graph: dict[str, set[str]] = {sq_id: set() for sq_id in sq_map}

        for sq_id, sq in sq_map.items():
            for prereq_id in sq.dependencies:
                if prereq_id == sq_id:
                    issues.append(f"Sub-question '{sq_id}' cannot depend on itself.")
                elif prereq_id not in sq_map:
                    issues.append(
                        f"Sub-question '{sq_id}' references non-existent dependency '{prereq_id}'."
                    )
                else:
                    dep_graph[sq_id].add(prereq_id)

        for dep in decomposition.dependencies:
            if not isinstance(dep, ResearchDependency):
                continue
            if dep.prerequisite_id == dep.dependent_id:
                issues.append(
                    f"Dependency '{dep.dependency_id}' defines self-dependency on '{dep.dependent_id}'."
                )
            if dep.prerequisite_id not in sq_map:
                issues.append(
                    f"Dependency '{dep.dependency_id}' references non-existent prerequisite '{dep.prerequisite_id}'."
                )
            if dep.dependent_id not in sq_map:
                issues.append(
                    f"Dependency '{dep.dependency_id}' references non-existent dependent '{dep.dependent_id}'."
                )
            if dep.dependent_id in dep_graph and dep.prerequisite_id in sq_map:
                dep_graph[dep.dependent_id].add(dep.prerequisite_id)

        # 5. Dependency Cycle Detection (Lightweight 3-color DFS)
        cls._check_dependency_cycles(dep_graph, issues)

        # 6. Payload Size Bound Check
        cls._check_payload_size(decomposition, policy, issues)

        if issues and raise_on_error:
            msg = (
                f"Decomposition policy constraints failed with {len(issues)} issue(s):\n"
                + "\n".join(f"  - {err}" for err in issues)
            )
            raise DecompositionConstraintError(msg, issues=issues)

        return issues

    @classmethod
    def _check_dependency_cycles(
        cls,
        dep_graph: dict[str, set[str]],
        issues: list[str],
    ) -> None:
        """Detect directed cycles in the sub-question dependency graph using DFS with coloring."""
        WHITE = 0  # unvisited
        GRAY = 1   # currently exploring
        BLACK = 2  # completed

        color: dict[str, int] = {node: WHITE for node in dep_graph}
        path: list[str] = []

        def dfs(node: str) -> Optional[list[str]]:
            color[node] = GRAY
            path.append(node)

            for neighbor in dep_graph.get(node, ()):
                if color.get(neighbor) == GRAY:
                    cycle_start = path.index(neighbor)
                    return path[cycle_start:] + [neighbor]
                if color.get(neighbor) == WHITE:
                    found = dfs(neighbor)
                    if found:
                        return found

            path.pop()
            color[node] = BLACK
            return None

        for node in dep_graph:
            if color[node] == WHITE:
                cycle = dfs(node)
                if cycle:
                    cycle_str = " -> ".join(cycle)
                    issues.append(f"Dependency cycle detected in decomposition: {cycle_str}")
                    return

    @classmethod
    def _check_payload_size(
        cls,
        decomposition: ResearchDecomposition,
        policy: DecompositionPolicy,
        issues: list[str],
    ) -> None:
        """Ensure serialized decomposition does not exceed max_payload_bytes."""
        try:
            payload_str = json.dumps(decomposition.to_dict(), default=str)
            payload_bytes = len(payload_str.encode("utf-8"))
            if payload_bytes > policy.max_payload_bytes:
                issues.append(
                    f"Total decomposition payload size ({payload_bytes} bytes) exceeds "
                    f"maximum allowed limit ({policy.max_payload_bytes} bytes)."
                )
        except Exception:
            pass
