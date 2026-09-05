from __future__ import annotations

from typing import Optional, TYPE_CHECKING

from core.errors import AutonomOSError
from core.research.decomposition.model import (
    ResearchDecomposition,
    ResearchDependency,
    ResearchSubQuestion,
)

if TYPE_CHECKING:
    from core.research.decomposition.policy import DecompositionPolicy


class DecompositionValidationError(ValueError):
    """Raised when a ResearchDecomposition or ResearchSubQuestion violates structural invariants."""
    def __init__(self, message: str, issues: Optional[list[str]] = None):
        super().__init__(message)
        self.issues = issues or [message]


class DecompositionValidator:
    """
    Deterministic validation engine enforcing structural invariants on ResearchDecomposition:
    1. Decomposition and request IDs must be non-empty.
    2. Every sub-question belongs to the decomposition.
    3. Non-root sub-questions must reference valid parent IDs.
    4. Parent-child hierarchy must form an acyclic forest.
    5. Sub-question depths must strictly match hierarchy (parent.depth + 1).
    6. Sub-question depths must not exceed decomposition max_depth_limit.
    7. All sub-question IDs and dependency IDs must be unique.
    8. No question can depend on itself.
    9. Dependency prerequisite and dependent IDs must resolve to existing sub-questions.
    10. Dependency graph must not contain directed cycles.
    11. Sub-question question text and objective must not be empty.
    12. Provenance must link to originating research request.
    13. Internal ResearchSubQuestion must not be confused with user-facing ResearchQuestion.
    """

    @classmethod
    def validate(
        cls,
        decomposition: ResearchDecomposition,
        policy: Optional[DecompositionPolicy] = None,
        raise_on_error: bool = True,
    ) -> list[str]:
        """
        Validate full decomposition against all structural invariants.
        If policy is provided, also validates deterministic policy constraints.
        Returns list of error messages. If raise_on_error is True, raises DecompositionValidationError on failure.
        """
        errors: list[str] = []

        # 1. Root Decomposition Invariants
        if not decomposition.decomposition_id or not decomposition.decomposition_id.strip():
            errors.append("Decomposition ID must not be empty.")

        if not decomposition.research_request_id or not decomposition.research_request_id.strip():
            errors.append("Decomposition must reference a valid non-empty research_request_id.")

        if not decomposition.objective or not decomposition.objective.strip():
            errors.append("Decomposition objective must not be empty.")

        if not decomposition.root_question or not decomposition.root_question.strip():
            errors.append("Decomposition root_question must not be empty.")

        if not (0.0 <= decomposition.decomposition_confidence <= 1.0):
            errors.append(f"Decomposition confidence must be within [0.0, 1.0], got {decomposition.decomposition_confidence}.")

        if decomposition.max_depth_limit < 1:
            errors.append(f"max_depth_limit must be at least 1, got {decomposition.max_depth_limit}.")

        # 2. Sub-Questions Collection & Uniqueness
        sq_map: dict[str, ResearchSubQuestion] = {}
        for idx, sq in enumerate(decomposition.sub_questions):
            # Guard against passing user-facing ResearchQuestion
            if hasattr(sq, "options") and hasattr(sq, "custom_answer_allowed"):
                errors.append(
                    f"Sub-question at index {idx} appears to be a user-facing ResearchQuestion. "
                    "ResearchSubQuestion must be used for internal research planning."
                )
                continue

            if not isinstance(sq, ResearchSubQuestion):
                errors.append(f"Item at index {idx} is not an instance of ResearchSubQuestion.")
                continue

            if not sq.sub_question_id or not sq.sub_question_id.strip():
                errors.append(f"Sub-question at index {idx} has an empty sub_question_id.")
                continue

            if sq.sub_question_id in sq_map:
                errors.append(f"Duplicate sub_question_id detected: '{sq.sub_question_id}'.")
            else:
                sq_map[sq.sub_question_id] = sq

            # Decomposition ID match
            if sq.decomposition_id and sq.decomposition_id != decomposition.decomposition_id:
                errors.append(
                    f"Sub-question '{sq.sub_question_id}' decomposition_id '{sq.decomposition_id}' "
                    f"does not match decomposition '{decomposition.decomposition_id}'."
                )

            # Objective and question non-empty
            if not sq.question or not sq.question.strip():
                errors.append(f"Sub-question '{sq.sub_question_id}' has an empty question.")

            if not sq.objective or not sq.objective.strip():
                errors.append(f"Sub-question '{sq.sub_question_id}' has an empty objective.")

            # Provenance check
            if sq.provenance:
                if (
                    sq.provenance.research_request_id
                    and sq.provenance.research_request_id != decomposition.research_request_id
                ):
                    errors.append(
                        f"Sub-question '{sq.sub_question_id}' provenance request ID '{sq.provenance.research_request_id}' "
                        f"does not match decomposition request ID '{decomposition.research_request_id}'."
                    )

        # 3. Hierarchy & Depth Invariants
        for sq_id, sq in sq_map.items():
            if sq.parent_id is not None:
                if sq.parent_id == sq_id:
                    errors.append(f"Sub-question '{sq_id}' cannot be its own parent.")
                elif sq.parent_id not in sq_map:
                    errors.append(f"Sub-question '{sq_id}' references non-existent parent_id '{sq.parent_id}'.")
                else:
                    parent = sq_map[sq.parent_id]
                    expected_depth = parent.decomposition_depth + 1
                    if sq.decomposition_depth != expected_depth:
                        errors.append(
                            f"Sub-question '{sq_id}' depth ({sq.decomposition_depth}) does not match "
                            f"hierarchy: parent '{parent.sub_question_id}' depth is {parent.decomposition_depth}, "
                            f"expected {expected_depth}."
                        )
            else:
                if sq.decomposition_depth != 1:
                    errors.append(
                        f"Root sub-question '{sq_id}' must have decomposition_depth = 1, got {sq.decomposition_depth}."
                    )

            if sq.decomposition_depth > decomposition.max_depth_limit:
                errors.append(
                    f"Sub-question '{sq_id}' depth ({sq.decomposition_depth}) exceeds "
                    f"max_depth_limit ({decomposition.max_depth_limit})."
                )

        # Check parent-child cycles (hierarchy acyclicity)
        cls._check_parent_cycles(sq_map, errors)

        # 4. Dependency Invariants
        seen_dep_ids: set[str] = set()
        dep_graph: dict[str, set[str]] = {sq_id: set() for sq_id in sq_map}

        # Dependencies listed in sub_question.dependencies
        for sq_id, sq in sq_map.items():
            for prereq_id in sq.dependencies:
                if prereq_id == sq_id:
                    errors.append(f"Sub-question '{sq_id}' cannot depend on itself.")
                elif prereq_id not in sq_map:
                    errors.append(f"Sub-question '{sq_id}' references non-existent dependency '{prereq_id}'.")
                else:
                    dep_graph[sq_id].add(prereq_id)

        # Dependencies listed in decomposition.dependencies
        for idx, dep in enumerate(decomposition.dependencies):
            if not isinstance(dep, ResearchDependency):
                errors.append(f"Dependency at index {idx} is not an instance of ResearchDependency.")
                continue

            if not dep.dependency_id or not dep.dependency_id.strip():
                errors.append(f"Dependency at index {idx} has an empty dependency_id.")
            elif dep.dependency_id in seen_dep_ids:
                errors.append(f"Duplicate dependency_id detected: '{dep.dependency_id}'.")
            else:
                seen_dep_ids.add(dep.dependency_id)

            if not dep.prerequisite_id or not dep.prerequisite_id.strip():
                errors.append(f"Dependency '{dep.dependency_id}' has an empty prerequisite_id.")
            elif dep.prerequisite_id not in sq_map:
                errors.append(f"Dependency '{dep.dependency_id}' references non-existent prerequisite_id '{dep.prerequisite_id}'.")

            if not dep.dependent_id or not dep.dependent_id.strip():
                errors.append(f"Dependency '{dep.dependency_id}' has an empty dependent_id.")
            elif dep.dependent_id not in sq_map:
                errors.append(f"Dependency '{dep.dependency_id}' references non-existent dependent_id '{dep.dependent_id}'.")

            if dep.prerequisite_id and dep.dependent_id and dep.prerequisite_id == dep.dependent_id:
                errors.append(f"Dependency '{dep.dependency_id}' defines self-dependency on '{dep.dependent_id}'.")

            if dep.dependent_id in dep_graph and dep.prerequisite_id in sq_map:
                dep_graph[dep.dependent_id].add(dep.prerequisite_id)

        # 5. Dependency Cycle Detection (Topological Sort / Kahn's algorithm)
        cls._check_dependency_cycles(dep_graph, errors)

        # 6. Policy Constraints Check (if policy is provided)
        if policy is not None:
            from core.research.decomposition.constraints import DecompositionConstraintValidator
            policy_issues = DecompositionConstraintValidator.validate(
                decomposition, policy=policy, raise_on_error=False
            )
            errors.extend(policy_issues)

        if errors and raise_on_error:
            msg = (
                f"Decomposition validation failed with {len(errors)} error(s):\n"
                + "\n".join(f"  - {err}" for err in errors)
            )
            raise DecompositionValidationError(msg, issues=errors)

        return errors

    @classmethod
    def validate_constraints(
        cls,
        decomposition: ResearchDecomposition,
        policy: Optional[DecompositionPolicy] = None,
        raise_on_error: bool = True,
    ) -> list[str]:
        """
        Validate decomposition proposal against deterministic policy constraints.
        Uses default DecompositionPolicy if none provided.
        """
        from core.research.decomposition.constraints import DecompositionConstraintValidator
        return DecompositionConstraintValidator.validate(
            decomposition, policy=policy, raise_on_error=raise_on_error
        )

    @classmethod
    def _check_parent_cycles(
        cls,
        sq_map: dict[str, ResearchSubQuestion],
        errors: list[str],
    ) -> None:
        """Detect circular parent-child references (e.g. A -> B -> A)."""
        for start_id in sq_map:
            visited: set[str] = set()
            curr: Optional[str] = start_id
            while curr is not None:
                if curr in visited:
                    errors.append(f"Circular parent-child hierarchy detected involving sub-question '{curr}'.")
                    break
                visited.add(curr)
                parent_node = sq_map.get(curr)
                curr = parent_node.parent_id if parent_node else None

    @classmethod
    def _check_dependency_cycles(
        cls,
        dep_graph: dict[str, set[str]],
        errors: list[str],
    ) -> None:
        """
        Detect directed cycles in the sub-question dependency graph using DFS with coloring.
        dep_graph maps: dependent_id -> set of prerequisite_ids
        Cycle means dependent requires prerequisite, which transitively requires dependent.
        """
        WHITE = 0  # unvisited
        GRAY = 1   # currently exploring (in recursion stack)
        BLACK = 2  # completed

        color: dict[str, int] = {node: WHITE for node in dep_graph}
        path: list[str] = []

        def dfs(node: str) -> Optional[list[str]]:
            color[node] = GRAY
            path.append(node)

            for neighbor in dep_graph.get(node, ()):
                if color.get(neighbor) == GRAY:
                    # Found cycle
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
                    errors.append(f"Dependency cycle detected in decomposition: {cycle_str}")
                    return
