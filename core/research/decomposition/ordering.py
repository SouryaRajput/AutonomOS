from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional

from core.research.decomposition.model import (
    ResearchDecomposition,
    ResearchSubQuestion,
)
from core.research.decomposition.types import (
    SubQuestionPriority,
    SubQuestionStatus,
)
from core.research.decomposition.validator import DecompositionValidationError


def utc_now() -> str:
    """Generate ISO 8601 UTC timestamp."""
    return datetime.now(timezone.utc).isoformat()


_PRIORITY_WEIGHTS: dict[str, int] = {
    SubQuestionPriority.CRITICAL.value: 4,
    SubQuestionPriority.HIGH.value: 3,
    SubQuestionPriority.MEDIUM.value: 2,
    SubQuestionPriority.LOW.value: 1,
}


def get_priority_weight(priority: SubQuestionPriority | str) -> int:
    """Return numeric weight for sorting priorities (CRITICAL=4, HIGH=3, MEDIUM=2, LOW=1)."""
    val = priority.value if isinstance(priority, SubQuestionPriority) else str(priority).upper()
    return _PRIORITY_WEIGHTS.get(val, 2)


@dataclass
class QuestionReadiness:
    """
    Readiness metadata for a single research sub-question within an execution plan.
    """
    sub_question_id: str
    is_ready: bool
    status: SubQuestionStatus
    prerequisites: list[str] = field(default_factory=list)
    blocking_dependencies: list[str] = field(default_factory=list)
    stage_index: int = 0
    execution_order: int = 1

    def to_dict(self) -> dict[str, Any]:
        return {
            "sub_question_id": self.sub_question_id,
            "is_ready": self.is_ready,
            "status": self.status.value if isinstance(self.status, SubQuestionStatus) else str(self.status),
            "prerequisites": list(self.prerequisites),
            "blocking_dependencies": list(self.blocking_dependencies),
            "stage_index": self.stage_index,
            "execution_order": self.execution_order,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> QuestionReadiness:
        st_raw = data.get("status", SubQuestionStatus.PENDING.value)
        try:
            status = SubQuestionStatus(st_raw)
        except ValueError:
            status = SubQuestionStatus.PENDING
        return cls(
            sub_question_id=str(data.get("sub_question_id", "")),
            is_ready=bool(data.get("is_ready", False)),
            status=status,
            prerequisites=list(data.get("prerequisites", [])),
            blocking_dependencies=list(data.get("blocking_dependencies", [])),
            stage_index=int(data.get("stage_index", 0)),
            execution_order=int(data.get("execution_order", 1)),
        )


@dataclass
class ExecutionStage:
    """
    A wave/stage of sub-questions that can be executed concurrently or in wave sequence.
    All prerequisites for sub-questions in stage K reside in stages < K.
    Within each stage, sub-questions are deterministically ordered by priority descending,
    then original generation index.
    """
    stage_index: int
    sub_question_ids: list[str] = field(default_factory=list)
    sub_questions: list[ResearchSubQuestion] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "stage_index": self.stage_index,
            "sub_question_ids": list(self.sub_question_ids),
            "sub_questions": [sq.to_dict() for sq in self.sub_questions],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ExecutionStage:
        sqs = [
            ResearchSubQuestion.from_dict(sq)
            for sq in data.get("sub_questions", [])
            if isinstance(sq, dict)
        ]
        return cls(
            stage_index=int(data.get("stage_index", 0)),
            sub_question_ids=list(data.get("sub_question_ids", [])),
            sub_questions=sqs,
        )


@dataclass
class DecompositionOrderPlan:
    """
    Authoritative deterministic ordering plan for a ResearchDecomposition.
    Provides both total linear topological ordering and stage/wave grouping,
    with dynamic readiness query evaluation helpers.
    """
    decomposition_id: str
    linear_execution_order: list[ResearchSubQuestion] = field(default_factory=list)
    linear_execution_ids: list[str] = field(default_factory=list)
    stages: list[ExecutionStage] = field(default_factory=list)
    readiness_map: dict[str, QuestionReadiness] = field(default_factory=dict)
    prerequisites_map: dict[str, list[str]] = field(default_factory=dict)
    created_at: str = field(default_factory=utc_now)

    def get_ready_questions(
        self,
        completed_ids: Optional[set[str]] = None,
    ) -> list[ResearchSubQuestion]:
        """
        Return all sub-questions that are ready for execution given a set of completed IDs.
        A question is ready if:
        1. It is not already in completed_ids.
        2. All of its prerequisites are in completed_ids.
        Questions are returned in deterministic priority-then-index order.
        """
        done = completed_ids or set()
        ready: list[ResearchSubQuestion] = []
        for sq in self.linear_execution_order:
            if sq.sub_question_id in done:
                continue
            prereqs = self.prerequisites_map.get(sq.sub_question_id, [])
            if all(p in done for p in prereqs):
                ready.append(sq)
        return ready

    def is_ready(
        self,
        sub_question_id: str,
        completed_ids: Optional[set[str]] = None,
    ) -> bool:
        """Check if a specific sub-question is ready for execution."""
        done = completed_ids or set()
        if sub_question_id in done:
            return False
        prereqs = self.prerequisites_map.get(sub_question_id, [])
        return all(p in done for p in prereqs)

    def get_blocking_dependencies(
        self,
        sub_question_id: str,
        completed_ids: Optional[set[str]] = None,
    ) -> list[str]:
        """Return the list of prerequisite IDs that are still unsatisfied for a sub-question."""
        done = completed_ids or set()
        prereqs = self.prerequisites_map.get(sub_question_id, [])
        return [p for p in prereqs if p not in done]

    def get_readiness(
        self,
        sub_question_id: str,
        completed_ids: Optional[set[str]] = None,
    ) -> Optional[QuestionReadiness]:
        """Return a dynamic QuestionReadiness descriptor reflecting current completed IDs."""
        base = self.readiness_map.get(sub_question_id)
        if base is None:
            return None
        done = completed_ids or set()
        blocking = self.get_blocking_dependencies(sub_question_id, done)
        is_ready = len(blocking) == 0 and (sub_question_id not in done)
        status = SubQuestionStatus.READY if is_ready else (
            SubQuestionStatus.COMPLETE if sub_question_id in done else SubQuestionStatus.BLOCKED
        )
        return QuestionReadiness(
            sub_question_id=sub_question_id,
            is_ready=is_ready,
            status=status,
            prerequisites=list(base.prerequisites),
            blocking_dependencies=blocking,
            stage_index=base.stage_index,
            execution_order=base.execution_order,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "decomposition_id": self.decomposition_id,
            "linear_execution_ids": list(self.linear_execution_ids),
            "linear_execution_order": [sq.to_dict() for sq in self.linear_execution_order],
            "stages": [stage.to_dict() for stage in self.stages],
            "readiness_map": {k: v.to_dict() for k, v in self.readiness_map.items()},
            "prerequisites_map": {k: list(v) for k, v in self.prerequisites_map.items()},
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> DecompositionOrderPlan:
        order = [
            ResearchSubQuestion.from_dict(sq)
            for sq in data.get("linear_execution_order", [])
            if isinstance(sq, dict)
        ]
        stages = [
            ExecutionStage.from_dict(st)
            for st in data.get("stages", [])
            if isinstance(st, dict)
        ]
        readiness = {
            k: QuestionReadiness.from_dict(v)
            for k, v in data.get("readiness_map", {}).items()
            if isinstance(v, dict)
        }
        return cls(
            decomposition_id=str(data.get("decomposition_id", "")),
            linear_execution_order=order,
            linear_execution_ids=list(data.get("linear_execution_ids", [])),
            stages=stages,
            readiness_map=readiness,
            prerequisites_map=dict(data.get("prerequisites_map", {})),
            created_at=str(data.get("created_at", utc_now())),
        )


class SubQuestionOrderPlanner:
    """
    Deterministic ordering engine for ResearchDecomposition sub-questions.
    Produces safe, reproducible execution orders respecting:
    1. Dependencies (topological ordering; prerequisites strictly precede dependents)
    2. Priority (CRITICAL > HIGH > MEDIUM > LOW as tie-breaker among ready questions)
    3. Original generation order (stable final tie-breaker)
    """

    @classmethod
    def plan_order(cls, decomposition: ResearchDecomposition) -> DecompositionOrderPlan:
        """
        Compute total linear order, stage/wave grouping, and readiness map for a decomposition.
        Raises DecompositionValidationError if a dependency cycle is detected.
        """
        if decomposition is None:
            raise TypeError("Cannot plan order for None; expected ResearchDecomposition.")

        sub_questions = list(decomposition.sub_questions)
        if not sub_questions:
            return DecompositionOrderPlan(decomposition_id=decomposition.decomposition_id)

        # 1. Indexing & Graph Construction
        sq_map: dict[str, ResearchSubQuestion] = {sq.sub_question_id: sq for sq in sub_questions}
        index_map: dict[str, int] = {sq.sub_question_id: idx for idx, sq in enumerate(sub_questions)}

        # Aggregate prerequisites from both sq.dependencies and decomposition.dependencies
        prereqs_map: dict[str, set[str]] = {sq.sub_question_id: set() for sq in sub_questions}
        dependents_map: dict[str, set[str]] = {sq.sub_question_id: set() for sq in sub_questions}

        for sq in sub_questions:
            for prereq_id in sq.dependencies:
                if prereq_id in sq_map and prereq_id != sq.sub_question_id:
                    prereqs_map[sq.sub_question_id].add(prereq_id)
                    dependents_map[prereq_id].add(sq.sub_question_id)

        for dep in decomposition.dependencies:
            p_id = dep.prerequisite_id
            d_id = dep.dependent_id
            if p_id in sq_map and d_id in sq_map and p_id != d_id:
                prereqs_map[d_id].add(p_id)
                dependents_map[p_id].add(d_id)

        # 2. Cycle Detection (3-color DFS)
        cls._verify_acyclic(sq_map, prereqs_map)

        # 3. Stage / Wave Computation (Longest path from DAG sources)
        stage_assignments: dict[str, int] = {}

        def get_stage(node_id: str, memo: dict[str, int]) -> int:
            if node_id in memo:
                return memo[node_id]
            prereqs = prereqs_map[node_id]
            if not prereqs:
                memo[node_id] = 0
                return 0
            val = max(get_stage(p, memo) for p in prereqs) + 1
            memo[node_id] = val
            return val

        memo: dict[str, int] = {}
        for sq_id in sq_map:
            stage_assignments[sq_id] = get_stage(sq_id, memo)

        # Group by stage
        max_stage = max(stage_assignments.values()) if stage_assignments else 0
        stages_dict: dict[int, list[ResearchSubQuestion]] = {s: [] for s in range(max_stage + 1)}
        for sq_id, stage_idx in stage_assignments.items():
            stages_dict[stage_idx].append(sq_map[sq_id])

        # Deterministically sort questions within each stage
        stages: list[ExecutionStage] = []
        for s in range(max_stage + 1):
            sq_list = stages_dict[s]
            sq_list.sort(
                key=lambda sq: (
                    -get_priority_weight(sq.priority),
                    index_map[sq.sub_question_id],
                )
            )
            stages.append(
                ExecutionStage(
                    stage_index=s,
                    sub_question_ids=[sq.sub_question_id for sq in sq_list],
                    sub_questions=sq_list,
                )
            )

        # 4. Total Linear Topological Ordering (Kahn's Algorithm with Priority Queue)
        in_degrees: dict[str, int] = {sq_id: len(prereqs_map[sq_id]) for sq_id in sq_map}
        ready_queue = [sq_map[sq_id] for sq_id, deg in in_degrees.items() if deg == 0]

        linear_order: list[ResearchSubQuestion] = []
        while ready_queue:
            # Sort ready nodes by priority descending, then original index ascending
            ready_queue.sort(
                key=lambda sq: (
                    -get_priority_weight(sq.priority),
                    index_map[sq.sub_question_id],
                )
            )
            # Pick best ready question
            best_sq = ready_queue.pop(0)
            linear_order.append(best_sq)

            # Reduce in-degree for dependents
            for dep_id in dependents_map[best_sq.sub_question_id]:
                in_degrees[dep_id] -= 1
                if in_degrees[dep_id] == 0:
                    ready_queue.append(sq_map[dep_id])

        # 5. Build Readiness Map
        readiness_map: dict[str, QuestionReadiness] = {}
        serial_prereqs_map = {sq_id: sorted(prereqs_map[sq_id]) for sq_id in sq_map}

        for exec_idx, sq in enumerate(linear_order, start=1):
            sq_id = sq.sub_question_id
            blocking = sorted(prereqs_map[sq_id])
            is_ready = len(blocking) == 0
            readiness_map[sq_id] = QuestionReadiness(
                sub_question_id=sq_id,
                is_ready=is_ready,
                status=SubQuestionStatus.READY if is_ready else SubQuestionStatus.BLOCKED,
                prerequisites=list(blocking),
                blocking_dependencies=list(blocking),
                stage_index=stage_assignments[sq_id],
                execution_order=exec_idx,
            )

        return DecompositionOrderPlan(
            decomposition_id=decomposition.decomposition_id,
            linear_execution_order=linear_order,
            linear_execution_ids=[sq.sub_question_id for sq in linear_order],
            stages=stages,
            readiness_map=readiness_map,
            prerequisites_map=serial_prereqs_map,
        )

    @classmethod
    def _verify_acyclic(
        cls,
        sq_map: dict[str, ResearchSubQuestion],
        prereqs_map: dict[str, set[str]],
    ) -> None:
        """
        Verify that the dependency graph is an acyclic DAG using DFS with 3 colors.
        Raises DecompositionValidationError on cycle.
        """
        WHITE = 0  # unvisited
        GRAY = 1   # exploring
        BLACK = 2  # completed

        color: dict[str, int] = {node: WHITE for node in sq_map}
        path: list[str] = []

        def dfs(node: str) -> Optional[list[str]]:
            color[node] = GRAY
            path.append(node)

            for neighbor in prereqs_map.get(node, ()):
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

        for node in sq_map:
            if color[node] == WHITE:
                cycle = dfs(node)
                if cycle:
                    cycle_str = " -> ".join(cycle)
                    raise DecompositionValidationError(
                        f"Dependency cycle detected in decomposition: {cycle_str}",
                        issues=[f"Dependency cycle detected in decomposition: {cycle_str}"],
                    )


# Alias for explicit domain clarity
DecompositionOrderPlanner = SubQuestionOrderPlanner
