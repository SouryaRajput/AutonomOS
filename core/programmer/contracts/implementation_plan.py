from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
import json
from typing import Any, Optional, Sequence

from core.programmer.contracts.escalation import (
    ProgrammerEscalation,
    ProgrammerEscalationCategory,
)
from core.programmer.contracts.execution import ProgrammerExecution
from core.programmer.contracts.filesystem_resolver import is_subpath_or_equal
from core.programmer.contracts.identifiers import (
    new_escalation_candidate_id,
    new_escalation_id,
    new_plan_id,
    new_plan_step_id,
    validate_escalation_candidate_id,
    validate_execution_id,
    validate_plan_id,
    validate_plan_step_id,
    validate_work_order_id,
)
from core.programmer.contracts.work_order import ProgrammerWorkOrder
from core.programmer.errors import (
    ProgrammerLineageError,
    ProgrammerValidationError,
)
from core.programmer.types import (
    ProgrammerBlockerSeverity,
)


def utc_now() -> str:
    """Generate ISO 8601 UTC timestamp."""
    return datetime.now(timezone.utc).isoformat()


@dataclass
class EscalationCandidate:
    """
    Structured document representing a prospective blocker or authority escalation
    identified during planning before code modifications begin.

    Guarantees:
    - Never automatically expands WorkOrder scope or authority.
    - Preserves empirical reason, target, and requested decision.
    - Can be converted directly to a formal ProgrammerEscalation when execution blocks.
    """
    candidate_id: str = field(default_factory=new_escalation_candidate_id)
    category: ProgrammerEscalationCategory = ProgrammerEscalationCategory.SCOPE
    reason: str = ""
    target: Optional[str] = None
    requested_decision: str = ""
    severity: ProgrammerBlockerSeverity = ProgrammerBlockerSeverity.HIGH
    suggested_options: list[str] = field(default_factory=list)
    trace: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if isinstance(self.category, str):
            try:
                self.category = ProgrammerEscalationCategory(self.category.upper())
            except (ValueError, TypeError):
                self.category = ProgrammerEscalationCategory.OTHER
        if isinstance(self.severity, str):
            try:
                self.severity = ProgrammerBlockerSeverity(self.severity.upper())
            except (ValueError, TypeError):
                self.severity = ProgrammerBlockerSeverity.HIGH
        self.validate()

    def validate(self) -> None:
        validate_escalation_candidate_id(self.candidate_id)
        if not self.reason or not self.reason.strip():
            raise ProgrammerValidationError("EscalationCandidate reason cannot be empty.", field_name="reason")
        if not self.requested_decision or not self.requested_decision.strip():
            raise ProgrammerValidationError(
                "EscalationCandidate requested_decision cannot be empty.",
                field_name="requested_decision",
            )

    def to_escalation(
        self,
        execution: ProgrammerExecution,
        work_order: ProgrammerWorkOrder,
        escalation_id: Optional[str] = None,
    ) -> ProgrammerEscalation:
        """Convert this planning candidate into a formal ProgrammerEscalation."""
        if execution.work_order_id != work_order.work_order_id:
            raise ProgrammerLineageError(
                f"Execution work_order_id '{execution.work_order_id}' does not match "
                f"WorkOrder work_order_id '{work_order.work_order_id}'."
            )

        observed = [self.reason]
        if self.target:
            observed.append(f"Target: {self.target}")

        current_scope = {
            "allowed_paths": list(work_order.allowed_paths),
            "writable_paths": list(work_order.writable_paths),
            "forbidden_paths": list(work_order.forbidden_paths),
        }

        esc = ProgrammerEscalation(
            escalation_id=escalation_id or new_escalation_id(),
            execution_id=execution.execution_id,
            work_order_id=work_order.work_order_id,
            category=self.category,
            requested_decision=self.requested_decision,
            severity=self.severity,
            observed_facts=observed,
            current_scope=current_scope,
            suggested_options=list(self.suggested_options),
            trace=dict(self.trace or {
                "candidate_id": self.candidate_id,
                "created_at": utc_now(),
            }),
            metadata=dict(self.metadata),
        )
        return esc

    def to_dict(self) -> dict[str, Any]:
        return {
            "candidate_id": self.candidate_id,
            "category": self.category.value,
            "reason": self.reason,
            "target": self.target,
            "requested_decision": self.requested_decision,
            "severity": self.severity.value,
            "suggested_options": list(self.suggested_options),
            "trace": dict(self.trace),
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> EscalationCandidate:
        cat_raw = data.get("category", ProgrammerEscalationCategory.SCOPE.value)
        try:
            category = ProgrammerEscalationCategory(str(cat_raw).upper())
        except (ValueError, TypeError):
            category = ProgrammerEscalationCategory.OTHER

        sev_raw = data.get("severity", ProgrammerBlockerSeverity.HIGH.value)
        try:
            severity = ProgrammerBlockerSeverity(str(sev_raw).upper())
        except (ValueError, TypeError):
            severity = ProgrammerBlockerSeverity.HIGH

        return cls(
            candidate_id=data.get("candidate_id", new_escalation_candidate_id()),
            category=category,
            reason=str(data.get("reason", "")),
            target=data.get("target"),
            requested_decision=str(data.get("requested_decision", "")),
            severity=severity,
            suggested_options=list(data.get("suggested_options", [])),
            trace=dict(data.get("trace", {})),
            metadata=dict(data.get("metadata", {})),
        )


@dataclass
class ImplementationStep:
    """
    Structured discrete step within an ImplementationPlan.

    Contains:
    - step_id: Unique deterministic identifier
    - description: Action to execute
    - target_files: Specific file paths to be modified or inspected
    - target_modules: High-level architectural modules involved
    - rationale: Why this step is required
    - dependencies: List of prerequisite step IDs that must be executed first
    - expected_result: Measurable expected outcome
    - verification: Explicit verification check or test command to run
    - acceptance_criteria_ids: Associated WorkOrder acceptance criteria addressed
    """
    step_id: str
    description: str
    target_files: list[str] = field(default_factory=list)
    target_modules: list[str] = field(default_factory=list)
    rationale: str = ""
    dependencies: list[str] = field(default_factory=list)
    expected_result: str = ""
    verification: str = ""
    acceptance_criteria_ids: list[str] = field(default_factory=list)
    action_type: str = "modify"  # "modify", "verify", "read"
    is_escalation_candidate: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.validate()

    def validate(self) -> None:
        validate_plan_step_id(self.step_id)
        if not self.description or not self.description.strip():
            raise ProgrammerValidationError(
                f"Step '{self.step_id}' description cannot be empty.",
                field_name="description",
            )
        if not self.verification or not self.verification.strip():
            raise ProgrammerValidationError(
                f"Step '{self.step_id}' verification cannot be empty.",
                field_name="verification",
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "step_id": self.step_id,
            "description": self.description,
            "target_files": list(self.target_files),
            "target_modules": list(self.target_modules),
            "rationale": self.rationale,
            "dependencies": list(self.dependencies),
            "expected_result": self.expected_result,
            "verification": self.verification,
            "acceptance_criteria_ids": list(self.acceptance_criteria_ids),
            "action_type": self.action_type,
            "is_escalation_candidate": self.is_escalation_candidate,
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ImplementationStep:
        return cls(
            step_id=data.get("step_id", new_plan_step_id()),
            description=str(data.get("description", "")),
            target_files=list(data.get("target_files", [])),
            target_modules=list(data.get("target_modules", [])),
            rationale=str(data.get("rationale", "")),
            dependencies=list(data.get("dependencies", [])),
            expected_result=str(data.get("expected_result", "")),
            verification=str(data.get("verification", "")),
            acceptance_criteria_ids=list(data.get("acceptance_criteria_ids", [])),
            action_type=str(data.get("action_type", "modify")),
            is_escalation_candidate=bool(data.get("is_escalation_candidate", False)),
            metadata=dict(data.get("metadata", {})),
        )


@dataclass
class ImplementationPlan:
    """
    Structured domain model representing Programmer's advisory execution plan
    prior to performing code modifications.

    Core Invariants:
    1. Advisory Only: Provides guidance for implementation sequencing; never modifies
       WorkOrder objectives, permissions, budgets, or filesystem boundaries.
    2. Authority Bounded: Cannot write to out-of-scope files without producing an EscalationCandidate.
    3. Deterministic Validation: Verified for acyclic step ordering, boundary compliance,
       and complete acceptance criteria coverage.
    """
    plan_id: str
    execution_id: str
    work_order_id: str
    project_id: str
    objective: str
    steps: list[ImplementationStep] = field(default_factory=list)
    affected_files: list[str] = field(default_factory=list)
    affected_modules: list[str] = field(default_factory=list)
    dependencies: list[str] = field(default_factory=list)
    required_checks: list[str] = field(default_factory=list)
    expected_changes: list[str] = field(default_factory=list)
    risks: list[str] = field(default_factory=list)
    assumptions: list[str] = field(default_factory=list)
    unknowns: list[str] = field(default_factory=list)
    escalation_points: list[EscalationCandidate] = field(default_factory=list)
    trace: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=utc_now)

    def __post_init__(self) -> None:
        normalized_steps: list[ImplementationStep] = []
        for s in self.steps:
            if isinstance(s, dict):
                normalized_steps.append(ImplementationStep.from_dict(s))
            else:
                normalized_steps.append(s)
        self.steps = normalized_steps

        normalized_esc: list[EscalationCandidate] = []
        for e in self.escalation_points:
            if isinstance(e, dict):
                normalized_esc.append(EscalationCandidate.from_dict(e))
            else:
                normalized_esc.append(e)
        self.escalation_points = normalized_esc

        self.validate()

    def validate(self) -> None:
        validate_plan_id(self.plan_id)
        validate_execution_id(self.execution_id)
        validate_work_order_id(self.work_order_id)
        if not self.project_id or not isinstance(self.project_id, str):
            raise ProgrammerValidationError("project_id must be a non-empty string.", field_name="project_id")
        if not self.objective or not isinstance(self.objective, str):
            raise ProgrammerValidationError("objective must be a non-empty string.", field_name="objective")

        # Step validation & acyclicity
        step_ids = set()
        for step in self.steps:
            if step.step_id in step_ids:
                raise ProgrammerValidationError(
                    f"Duplicate step_id '{step.step_id}' found in plan.",
                    field_name="steps",
                )
            step_ids.add(step.step_id)

        for step in self.steps:
            for dep in step.dependencies:
                if dep not in step_ids:
                    raise ProgrammerValidationError(
                        f"Step '{step.step_id}' references unknown dependency step '{dep}'.",
                        field_name="dependencies",
                    )

        # Check for cycles
        self.get_topological_order()

    def get_step(self, step_id: str) -> Optional[ImplementationStep]:
        """Retrieve a specific step by its unique ID."""
        for step in self.steps:
            if step.step_id == step_id:
                return step
        return None

    def get_topological_order(self) -> list[ImplementationStep]:
        """
        Compute and return the topologically sorted steps using Kahn's algorithm.
        Raises ProgrammerValidationError if a dependency cycle is detected.
        """
        in_degree: dict[str, int] = {step.step_id: 0 for step in self.steps}
        adj: dict[str, list[str]] = {step.step_id: [] for step in self.steps}

        for step in self.steps:
            for dep in step.dependencies:
                adj[dep].append(step.step_id)
                in_degree[step.step_id] += 1

        queue: deque[str] = deque([sid for sid, deg in in_degree.items() if deg == 0])
        ordered_ids: list[str] = []

        while queue:
            curr = queue.popleft()
            ordered_ids.append(curr)
            for neighbor in adj[curr]:
                in_degree[neighbor] -= 1
                if in_degree[neighbor] == 0:
                    queue.append(neighbor)

        if len(ordered_ids) != len(self.steps):
            cycle_steps = [sid for sid, deg in in_degree.items() if deg > 0]
            raise ProgrammerValidationError(
                f"Cyclic dependency detected among implementation steps: {cycle_steps}",
                field_name="steps",
            )

        step_map = {step.step_id: step for step in self.steps}
        return [step_map[sid] for sid in ordered_ids]

    def has_escalations(self) -> bool:
        """Check whether the plan identified any authority gaps or blockers requiring escalation."""
        return len(self.escalation_points) > 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "plan_id": self.plan_id,
            "execution_id": self.execution_id,
            "work_order_id": self.work_order_id,
            "project_id": self.project_id,
            "objective": self.objective,
            "steps": [s.to_dict() for s in self.steps],
            "affected_files": list(self.affected_files),
            "affected_modules": list(self.affected_modules),
            "dependencies": list(self.dependencies),
            "required_checks": list(self.required_checks),
            "expected_changes": list(self.expected_changes),
            "risks": list(self.risks),
            "assumptions": list(self.assumptions),
            "unknowns": list(self.unknowns),
            "escalation_points": [e.to_dict() for e in self.escalation_points],
            "trace": dict(self.trace),
            "metadata": dict(self.metadata),
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ImplementationPlan:
        return cls(
            plan_id=data["plan_id"],
            execution_id=data["execution_id"],
            work_order_id=data["work_order_id"],
            project_id=data["project_id"],
            objective=data.get("objective", ""),
            steps=[ImplementationStep.from_dict(s) for s in data.get("steps", [])],
            affected_files=list(data.get("affected_files", [])),
            affected_modules=list(data.get("affected_modules", [])),
            dependencies=list(data.get("dependencies", [])),
            required_checks=list(data.get("required_checks", [])),
            expected_changes=list(data.get("expected_changes", [])),
            risks=list(data.get("risks", [])),
            assumptions=list(data.get("assumptions", [])),
            unknowns=list(data.get("unknowns", [])),
            escalation_points=[EscalationCandidate.from_dict(e) for e in data.get("escalation_points", [])],
            trace=dict(data.get("trace", {})),
            metadata=dict(data.get("metadata", {})),
            created_at=data.get("created_at", utc_now()),
        )

    def to_json(self, indent: Optional[int] = None) -> str:
        return json.dumps(self.to_dict(), indent=indent)

    @classmethod
    def from_json(cls, json_str: str) -> ImplementationPlan:
        return cls.from_dict(json.loads(json_str))


class ImplementationPlanValidator:
    """
    Deterministic validator for ImplementationPlan domain contracts.

    Verifies:
    1. Lineage consistency with the WorkOrder.
    2. Step uniqueness and acyclic step dependencies.
    3. Topological ordering: prerequisites must precede dependents.
    4. Scope boundaries: steps modifying files outside writable scope or inside
       forbidden paths must be captured as escalation candidates.
    5. Acceptance criteria coverage: each acceptance criterion must be covered
       by at least one step or required check.
    6. Verification completeness: all steps must include explicit verification.
    """

    @classmethod
    def validate(
        cls,
        plan: ImplementationPlan,
        work_order: Optional[ProgrammerWorkOrder] = None,
    ) -> None:
        """Run complete deterministic validation suite against plan and optional work order."""
        plan.validate()

        # Step ordering validation: prerequisites must appear before dependent steps
        step_index_map = {step.step_id: idx for idx, step in enumerate(plan.steps)}
        for step in plan.steps:
            current_idx = step_index_map[step.step_id]
            for dep_id in step.dependencies:
                dep_idx = step_index_map[dep_id]
                if dep_idx >= current_idx:
                    raise ProgrammerValidationError(
                        f"Step '{step.step_id}' depends on '{dep_id}', but '{dep_id}' "
                        f"appears after it in execution order. Steps must be topologically ordered.",
                        field_name="steps",
                    )

        if work_order is None:
            return

        # 1. Lineage checks
        if plan.work_order_id != work_order.work_order_id:
            raise ProgrammerLineageError(
                f"Plan work_order_id '{plan.work_order_id}' does not match "
                f"WorkOrder work_order_id '{work_order.work_order_id}'."
            )
        if plan.project_id != work_order.project_id:
            raise ProgrammerLineageError(
                f"Plan project_id '{plan.project_id}' does not match "
                f"WorkOrder project_id '{work_order.project_id}'."
            )
        if plan.objective != work_order.objective:
            raise ProgrammerValidationError(
                f"Plan objective '{plan.objective}' does not match "
                f"WorkOrder objective '{work_order.objective}'.",
                field_name="objective",
            )

        # 2. Scope boundary enforcement
        escalated_targets = {e.target for e in plan.escalation_points if e.target}
        for step in plan.steps:
            for tf in step.target_files:
                # Check forbidden paths (forbidden for ALL actions)
                is_forbidden = any(is_subpath_or_equal(tf, f) for f in work_order.forbidden_paths)
                # Check allowed paths (must be readable for all actions)
                is_allowed = (
                    not work_order.allowed_paths
                    or any(is_subpath_or_equal(tf, a) for a in work_order.allowed_paths)
                    or any(is_subpath_or_equal(tf, w) for w in work_order.writable_paths)
                    or any(is_subpath_or_equal(tf, r) for r in work_order.read_only_paths)
                )
                # Check writable paths (required for modification actions)
                is_writable = any(is_subpath_or_equal(tf, w) for w in work_order.writable_paths)

                if is_forbidden or not is_allowed:
                    if not step.is_escalation_candidate and tf not in escalated_targets:
                        raise ProgrammerValidationError(
                            f"Plan step '{step.step_id}' targets file '{tf}' which is outside "
                            f"allowed scope or in forbidden paths without an escalation candidate.",
                            field_name="target_files",
                        )

                if step.action_type == "modify" and not is_writable:
                    if not step.is_escalation_candidate and tf not in escalated_targets:
                        raise ProgrammerValidationError(
                            f"Plan step '{step.step_id}' targets file '{tf}' for modification, which is outside "
                            f"writable scope without an escalation candidate.",
                            field_name="target_files",
                        )

        # 3. Acceptance criteria coverage
        if work_order.acceptance_criteria:
            covered_ac_ids = set()
            for step in plan.steps:
                covered_ac_ids.update(step.acceptance_criteria_ids)
                for ac in work_order.acceptance_criteria:
                    if ac.criterion_id in step.description or ac.criterion_id in step.verification:
                        covered_ac_ids.add(ac.criterion_id)
                    elif ac.description and ac.description.lower() in step.description.lower():
                        covered_ac_ids.add(ac.criterion_id)

            for rc in plan.required_checks:
                for ac in work_order.acceptance_criteria:
                    if ac.criterion_id in rc or (ac.description and ac.description.lower() in rc.lower()):
                        covered_ac_ids.add(ac.criterion_id)

            missing_ac = [
                ac.criterion_id for ac in work_order.acceptance_criteria
                if ac.is_mandatory and ac.criterion_id not in covered_ac_ids
            ]
            if missing_ac:
                raise ProgrammerValidationError(
                    f"Mandatory acceptance criteria not covered by implementation plan: {missing_ac}",
                    field_name="acceptance_criteria",
                )
