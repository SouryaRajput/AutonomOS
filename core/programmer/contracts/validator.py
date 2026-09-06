from __future__ import annotations

import posixpath
from typing import Any, Optional

from core.enums import RiskLevel
from core.programmer.contracts.acceptance_criteria import AcceptanceCriterion
from core.programmer.contracts.command_scope import AllowedCommand
from core.programmer.contracts.identifiers import validate_work_order_id
from core.programmer.contracts.research_reference import ResearchEvidenceReference
from core.programmer.errors import (
    InvalidBudgetError,
    InvalidCommandScopeError,
    InvalidPathScopeError,
    ProgrammerLineageError,
    ProgrammerValidationError,
)

MAX_OBJECTIVE_LENGTH = 10_000


def normalize_scope_path(p: str) -> str:
    """Normalize a filesystem path for scope comparison."""
    norm = p.replace("\\", "/").strip()
    return posixpath.normpath(norm)


def is_path_in_scope(subpath: str, parent_scope: str) -> bool:
    """Check if subpath is within or equal to parent_scope."""
    sub = normalize_scope_path(subpath)
    parent = normalize_scope_path(parent_scope)
    if parent in ("*", ".", ""):
        return True
    if sub == parent:
        return True
    return sub.startswith(parent.rstrip("/") + "/")


class ProgrammerWorkOrderValidator:
    """
    Deterministic validator for ProgrammerWorkOrder authorization contracts.
    Enforces that the Manager-authorized work order is structurally sound,
    internally consistent, bounded, and cannot be expanded by the Programmer worker.
    """

    @classmethod
    def validate_all(cls, work_order: Any, require_acceptance_criteria: bool = False) -> None:
        """Run complete validation suite against the given work order."""
        cls.validate_identity_and_lineage(work_order)
        cls.validate_objective(work_order)
        cls.validate_path_scope(work_order)
        cls.validate_commands(work_order)
        cls.validate_acceptance_criteria(work_order, require_acceptance_criteria=require_acceptance_criteria)
        cls.validate_budgets(work_order)
        cls.validate_risk_level(work_order)
        cls.validate_dependencies(work_order)
        cls.validate_research_evidence(work_order)

    @classmethod
    def validate_identity_and_lineage(cls, work_order: Any) -> None:
        """Validate work order ID and manager task lineage invariants."""
        validate_work_order_id(work_order.work_order_id)

        # Manager task ID
        if not work_order.manager_task_id or not str(work_order.manager_task_id).strip():
            raise ProgrammerLineageError("Work order must have a valid non-empty manager_task_id.")

        # Project ID
        if not work_order.project_id or not str(work_order.project_id).strip():
            raise ProgrammerLineageError("Work order must have a valid non-empty project_id.")

        # Correlation ID
        if not work_order.correlation_id or not str(work_order.correlation_id).strip():
            raise ProgrammerLineageError("Work order must have a valid non-empty correlation_id.")

        # Invariant 4: a work order cannot belong to another Manager task
        task_id = getattr(work_order, "task_id", None)
        if task_id and work_order.manager_task_id and task_id != work_order.manager_task_id:
            raise ProgrammerLineageError(
                f"Task ID mismatch: work order belongs to manager task '{work_order.manager_task_id}' but task_id is '{task_id}'."
            )

    @classmethod
    def validate_objective(cls, work_order: Any) -> None:
        """Validate non-empty objective and reasonable length constraints."""
        obj = work_order.objective
        if not obj or not str(obj).strip():
            raise ProgrammerValidationError(
                message="Work order objective must be a non-empty string.",
                field_name="objective",
            )
        if len(str(obj)) > MAX_OBJECTIVE_LENGTH:
            raise ProgrammerValidationError(
                message=f"Work order objective exceeds maximum allowed length of {MAX_OBJECTIVE_LENGTH} characters.",
                field_name="objective",
            )

    @classmethod
    def validate_path_scope(cls, work_order: Any) -> None:
        """
        Validate path scopes and enforce critical path invariants:
        1. Writable scope cannot exceed allowed scope.
        2. Forbidden paths cannot simultaneously be writable.
        3. Read-only paths cannot simultaneously be writable.
        """
        all_scope_lists = [
            ("allowed_paths", work_order.allowed_paths),
            ("writable_paths", work_order.writable_paths),
            ("read_only_paths", work_order.read_only_paths),
            ("forbidden_paths", work_order.forbidden_paths),
        ]
        for name, paths in all_scope_lists:
            for p in paths:
                if not isinstance(p, str) or not p.strip():
                    raise InvalidPathScopeError(
                        message=f"Path in {name} cannot be empty or whitespace.",
                        path=str(p),
                    )
                if "\0" in p:
                    raise InvalidPathScopeError(
                        message=f"Path in {name} cannot contain null bytes: '{p}'",
                        path=p,
                    )

        allowed_paths = work_order.allowed_paths
        writable_paths = work_order.writable_paths
        read_only_paths = work_order.read_only_paths
        forbidden_paths = work_order.forbidden_paths

        # Invariant 1: Writable scope cannot exceed allowed scope (if allowed_paths is explicitly specified)
        if allowed_paths:
            for w in writable_paths:
                if not any(is_path_in_scope(w, a) for a in allowed_paths):
                    raise InvalidPathScopeError(
                        message=f"Writable path '{w}' exceeds allowed scope {allowed_paths}: not within any allowed path.",
                        path=w,
                    )

        # Invariant 2: Forbidden paths cannot simultaneously be writable (direct match or subpath overlap)
        for w in writable_paths:
            for f in forbidden_paths:
                if is_path_in_scope(w, f) or is_path_in_scope(f, w):
                    raise InvalidPathScopeError(
                        message=f"Path '{w}' cannot be both forbidden and writable (overlaps with '{f}').",
                        path=w,
                    )

        # Invariant 3: Read-only paths cannot simultaneously be writable (direct match or subpath overlap)
        for w in writable_paths:
            for r in read_only_paths:
                if is_path_in_scope(w, r) or is_path_in_scope(r, w):
                    raise InvalidPathScopeError(
                        message=f"Path '{w}' cannot be both writable and read_only (overlaps with '{r}').",
                        path=w,
                    )

        # Forbidden and read-only contradiction
        for r in read_only_paths:
            for f in forbidden_paths:
                if is_path_in_scope(r, f) or is_path_in_scope(f, r):
                    raise InvalidPathScopeError(
                        message=f"Path '{r}' cannot be both forbidden and read_only (overlaps with '{f}').",
                        path=r,
                    )

    @classmethod
    def validate_commands(cls, work_order: Any) -> None:
        """Validate command representations and check for contradictory authorizations."""
        seen_commands: dict[str, AllowedCommand] = {}
        for cmd in work_order.allowed_commands:
            if isinstance(cmd, AllowedCommand):
                cmd_obj = cmd
            elif isinstance(cmd, str):
                cmd_obj = AllowedCommand.from_str(cmd)
            else:
                raise ProgrammerValidationError(
                    message=f"Allowed command must be an AllowedCommand instance or string, got {type(cmd)}.",
                    field_name="allowed_commands",
                )
            cmd_obj.validate()

            base_cmd = cmd_obj.command.strip()
            if base_cmd in seen_commands:
                prev = seen_commands[base_cmd]
                if prev.allow_args != cmd_obj.allow_args:
                    raise ProgrammerValidationError(
                        message=f"Contradictory command authorization for '{base_cmd}': conflicting allow_args specifications.",
                        field_name="allowed_commands",
                    )
            seen_commands[base_cmd] = cmd_obj

    @classmethod
    def validate_acceptance_criteria(cls, work_order: Any, require_acceptance_criteria: bool = False) -> None:
        """Validate presence, individual validity, and unique IDs of acceptance criteria."""
        criteria = work_order.acceptance_criteria
        if require_acceptance_criteria and not criteria:
            raise ProgrammerValidationError(
                message="Work order requires at least one acceptance criterion.",
                field_name="acceptance_criteria",
            )

        seen_criterion_ids: set[str] = set()
        for ac in criteria:
            if not isinstance(ac, AcceptanceCriterion):
                if isinstance(ac, dict):
                    ac_obj = AcceptanceCriterion.from_dict(ac)
                elif isinstance(ac, str):
                    ac_obj = AcceptanceCriterion.from_str(ac)
                else:
                    raise ProgrammerValidationError(
                        message=f"Invalid acceptance criterion type: {type(ac)}.",
                        field_name="acceptance_criteria",
                    )
            else:
                ac_obj = ac

            if not ac_obj.description or not ac_obj.description.strip():
                raise ProgrammerValidationError(
                    message="Acceptance criterion description must not be empty.",
                    field_name="acceptance_criteria",
                )

            # Check for duplicate criterion IDs
            if ac_obj.criterion_id and ac_obj.criterion_id.strip():
                cid = ac_obj.criterion_id.strip()
                if cid in seen_criterion_ids:
                    raise ProgrammerValidationError(
                        message=f"Duplicate acceptance criterion ID '{cid}'.",
                        field_name="acceptance_criteria",
                    )
                seen_criterion_ids.add(cid)

    @classmethod
    def validate_budgets(cls, work_order: Any) -> None:
        """Validate non-negative and positive execution budgets."""
        # Iteration budget
        if not isinstance(work_order.iteration_budget, int) or work_order.iteration_budget <= 0:
            raise InvalidBudgetError(
                message=f"iteration_budget must be a positive integer, got {work_order.iteration_budget}.",
                budget_type="iteration_budget",
                value=work_order.iteration_budget,
            )

        # Time budget
        if not isinstance(work_order.time_budget, (int, float)) or work_order.time_budget <= 0:
            raise InvalidBudgetError(
                message=f"time_budget must be a positive number of seconds, got {work_order.time_budget}.",
                budget_type="time_budget",
                value=work_order.time_budget,
            )

    @classmethod
    def validate_risk_level(cls, work_order: Any) -> None:
        """Validate that risk level is a valid RiskLevel enum value."""
        if not isinstance(work_order.risk_level, RiskLevel):
            raise ProgrammerValidationError(
                message=f"Invalid risk_level '{work_order.risk_level}'. Expected one of {[r.value for r in RiskLevel]}.",
                field_name="risk_level",
            )

    @classmethod
    def validate_dependencies(cls, work_order: Any) -> None:
        """Validate task dependencies are non-empty references."""
        for dep in work_order.dependencies:
            if not isinstance(dep, str) or not dep.strip():
                raise ProgrammerValidationError(
                    message="Dependency reference must be a non-empty string.",
                    field_name="dependencies",
                )

    @classmethod
    def validate_research_evidence(cls, work_order: Any) -> None:
        """Validate research evidence references and provenance structures."""
        for r in work_order.research_evidence:
            if isinstance(r, dict):
                r_obj = ResearchEvidenceReference.from_dict(r)
            elif isinstance(r, ResearchEvidenceReference):
                r_obj = r
            else:
                raise ProgrammerValidationError(
                    message=f"Research evidence must be a ResearchEvidenceReference or dict, got {type(r)}.",
                    field_name="research_evidence",
                )
            if not r_obj.evidence_id or not str(r_obj.evidence_id).strip():
                raise ProgrammerValidationError(
                    message="Research evidence reference must have a non-empty evidence_id.",
                    field_name="research_evidence",
                )
            if not r_obj.claim_or_fact or not str(r_obj.claim_or_fact).strip():
                raise ProgrammerValidationError(
                    message="Research evidence reference must have a non-empty claim_or_fact.",
                    field_name="research_evidence",
                )
