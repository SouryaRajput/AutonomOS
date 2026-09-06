from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional
import uuid

from core.enums import RiskLevel
from core.programmer.contracts.acceptance_criteria import AcceptanceCriterion
from core.programmer.contracts.command_scope import AllowedCommand
from core.programmer.contracts.execution import ProgrammerExecution
from core.programmer.contracts.identifiers import (
    new_execution_id,
    new_work_order_id,
    validate_work_order_id,
)
from core.programmer.contracts.research_reference import ResearchEvidenceReference
from core.programmer.contracts.validator import (
    ProgrammerWorkOrderValidator,
    is_path_in_scope,
)
from core.programmer.errors import (
    InvalidBudgetError,
    InvalidPathScopeError,
    ProgrammerLineageError,
    ProgrammerValidationError,
)
from core.programmer.types import (
    AcceptanceCriterionType,
    ProgrammerExecutionStatus,
    ProgrammerWorkOrderStatus,
)


def utc_now() -> str:
    """Generate ISO 8601 UTC timestamp."""
    return datetime.now(timezone.utc).isoformat()


class ProgrammerWorkOrder:
    """
    Authorized assignment contract delegated from Manager to Programmer.
    Represents the formal root of Programmer-side execution and establishes causal lineage
    from ManagerTask down through ProgrammerExecution and ProgrammerResult.
    
    The work order defines:
    - WHAT to do: objective, technical_requirements
    - WHY it is needed: instructions, context, research_evidence
    - WHAT context is relevant: context, research_evidence, dependencies
    - WHAT is allowed: allowed_paths, writable_paths, read_only_paths, allowed_commands
    - WHAT is forbidden: forbidden_paths, constraints
    - HOW success will be measured: acceptance_criteria, required_checks
    """

    def __init__(
        self,
        work_order_id: str,
        manager_task_id: Optional[str] = None,
        project_id: str = "",
        correlation_id: str = "",
        objective: str = "",
        task_id: Optional[str] = None,
        instructions: Optional[list[str]] = None,
        context: Optional[dict[str, Any]] = None,
        allowed_paths: Optional[list[str]] = None,
        writable_paths: Optional[list[str]] = None,
        read_only_paths: Optional[list[str]] = None,
        forbidden_paths: Optional[list[str]] = None,
        allowed_commands: Optional[list[Any]] = None,
        constraints: Optional[list[str]] = None,
        technical_requirements: Optional[list[str]] = None,
        acceptance_criteria: Optional[list[Any]] = None,
        required_checks: Optional[list[str]] = None,
        research_evidence: Optional[list[Any]] = None,
        dependencies: Optional[list[str]] = None,
        iteration_budget: int = 10,
        time_budget: int = 600,
        risk_level: RiskLevel | str = RiskLevel.LOW,
        status: ProgrammerWorkOrderStatus = ProgrammerWorkOrderStatus.CREATED,
        trace: Optional[dict[str, Any]] = None,
        metadata: Optional[dict[str, Any]] = None,
        created_at: Optional[str] = None,
    ):
        self.work_order_id = work_order_id

        # Resolve task lineage accepting both manager_task_id and task_id
        if manager_task_id and task_id and manager_task_id != task_id:
            raise ProgrammerLineageError(
                f"Task ID mismatch: work order belongs to manager task '{manager_task_id}' but task_id is '{task_id}'."
            )
        resolved_task_id = manager_task_id or task_id or ""
        self.manager_task_id = resolved_task_id
        self.task_id = resolved_task_id

        self.project_id = project_id
        self.correlation_id = correlation_id
        self.objective = objective

        self.instructions = list(instructions or [])
        self.context = dict(context or {})
        self.allowed_paths = list(allowed_paths or [])
        self.writable_paths = list(writable_paths or [])
        self.read_only_paths = list(read_only_paths or [])
        self.forbidden_paths = list(forbidden_paths or [])

        # Normalize allowed_commands
        self.allowed_commands: list[AllowedCommand] = []
        for cmd in (allowed_commands or []):
            if isinstance(cmd, AllowedCommand):
                self.allowed_commands.append(cmd)
            elif isinstance(cmd, dict):
                self.allowed_commands.append(AllowedCommand.from_dict(cmd))
            elif isinstance(cmd, str):
                self.allowed_commands.append(AllowedCommand.from_str(cmd))
            else:
                self.allowed_commands.append(cmd)

        self.constraints = list(constraints or [])
        self.technical_requirements = list(technical_requirements or [])

        # Normalize acceptance_criteria
        self.acceptance_criteria: list[AcceptanceCriterion] = []
        for ac in (acceptance_criteria or []):
            if isinstance(ac, AcceptanceCriterion):
                self.acceptance_criteria.append(ac)
            elif isinstance(ac, dict):
                self.acceptance_criteria.append(AcceptanceCriterion.from_dict(ac))
            elif isinstance(ac, str):
                self.acceptance_criteria.append(AcceptanceCriterion.from_str(ac))
            else:
                self.acceptance_criteria.append(ac)

        self.required_checks = list(required_checks or [])

        # Normalize research_evidence
        self.research_evidence: list[ResearchEvidenceReference] = []
        for re in (research_evidence or []):
            if isinstance(re, ResearchEvidenceReference):
                self.research_evidence.append(re)
            elif isinstance(re, dict):
                self.research_evidence.append(ResearchEvidenceReference.from_dict(re))
            else:
                self.research_evidence.append(ResearchEvidenceReference.from_evidence(re))

        self.dependencies = list(dependencies or [])
        self.iteration_budget = iteration_budget
        self.time_budget = time_budget

        # Normalize risk_level
        if isinstance(risk_level, str):
            try:
                self.risk_level = RiskLevel(risk_level.upper())
            except ValueError:
                self.risk_level = risk_level  # Let validate() handle invalid risk
        else:
            self.risk_level = risk_level

        self.status = status
        self.trace = trace
        self.metadata = dict(metadata or {})
        self.created_at = created_at or utc_now()

        # Identifier and lineage baseline verification
        validate_work_order_id(self.work_order_id)
        if not self.manager_task_id:
            raise ProgrammerLineageError("ProgrammerWorkOrder must have a valid non-empty manager_task_id (ManagerTask link).")
        if not self.project_id:
            raise ProgrammerLineageError("ProgrammerWorkOrder must have a valid non-empty project_id.")
        if not self.correlation_id:
            raise ProgrammerLineageError("ProgrammerWorkOrder must have a valid non-empty correlation_id.")

    def validate(self, require_acceptance_criteria: bool = False) -> None:
        """
        Validate completeness and internal consistency of the work order.
        Raises ProgrammerValidationError or its subclasses if validation fails.
        """
        ProgrammerWorkOrderValidator.validate_all(
            self,
            require_acceptance_criteria=require_acceptance_criteria,
        )

    def validate_lineage(self, manager_task: Optional[Any] = None) -> None:
        """
        Verify that this work order strictly matches the authorized Manager task.
        Prevents a work order from belonging to or being executed under another Manager task.
        """
        ProgrammerWorkOrderValidator.validate_identity_and_lineage(self)
        if manager_task is not None:
            expected_id = getattr(manager_task, "id", None) or getattr(manager_task, "task_id", None)
            if isinstance(manager_task, str):
                expected_id = manager_task
            if expected_id and self.manager_task_id != str(expected_id):
                raise ProgrammerLineageError(
                    f"Lineage mismatch: work order belongs to task '{self.manager_task_id}', cannot be associated with task '{expected_id}'."
                )
            expected_project = getattr(manager_task, "project_id", None)
            if expected_project and self.project_id != str(expected_project):
                raise ProgrammerLineageError(
                    f"Project mismatch: work order belongs to project '{self.project_id}', but task is in '{expected_project}'."
                )

    def is_path_allowed(self, path: str) -> bool:
        """Check if a path falls within the allowed scope."""
        if not self.allowed_paths:
            return True
        return any(is_path_in_scope(path, a) for a in self.allowed_paths)

    def is_path_writable(self, path: str) -> bool:
        """Check if a path is authorized for writing."""
        if self.is_path_forbidden(path):
            return False
        if not self.writable_paths:
            return False
        return any(is_path_in_scope(path, w) for w in self.writable_paths)

    def is_path_read_only(self, path: str) -> bool:
        """Check if a path is read-only."""
        return any(is_path_in_scope(path, r) for r in self.read_only_paths)

    def is_path_forbidden(self, path: str) -> bool:
        """Check if a path is forbidden."""
        return any(is_path_in_scope(path, f) for f in self.forbidden_paths)

    @classmethod
    def from_task(cls, task: Any) -> ProgrammerWorkOrder:
        """
        Construct a strongly-typed ProgrammerWorkOrder from a runtime Manager Task model.
        Guarantees strict lineage preservation.
        """
        task_id = getattr(task, "id", None)
        if not task_id:
            raise ProgrammerLineageError("Cannot create ProgrammerWorkOrder from task without an id.")

        project_id = getattr(task, "project_id", None)
        if not project_id:
            raise ProgrammerLineageError(f"Cannot create ProgrammerWorkOrder from task '{task_id}' without a project_id.")

        meta = dict(getattr(task, "metadata", {}) or {})
        correlation_id = meta.get("correlation_id") or task_id
        objective = getattr(task, "objective", "") or getattr(task, "title", "")

        # Extract path scopes from metadata overrides
        allowed_paths = list(meta.get("allowed_paths", []))
        writable_paths = list(meta.get("writable_paths", []))
        read_only_paths = list(meta.get("read_only_paths", []))
        forbidden_paths = list(meta.get("forbidden_paths", []))

        # Extract commands
        cmds_raw = meta.get("allowed_commands", [])

        # Extract acceptance criteria
        ac_raw = meta.get("acceptance_criteria", []) or getattr(task, "success_criteria", [])

        # Extract research evidence
        ev_raw = meta.get("research_evidence", [])

        # Risk level
        risk_raw = getattr(task, "risk", None) or meta.get("risk_level", RiskLevel.LOW.value)
        if isinstance(risk_raw, RiskLevel):
            risk_level = risk_raw
        else:
            try:
                risk_level = RiskLevel(str(risk_raw).upper())
            except ValueError:
                risk_level = RiskLevel.LOW

        work_order_id = f"pwo-{uuid.uuid4().hex[:8]}"

        return cls(
            work_order_id=work_order_id,
            manager_task_id=task_id,
            project_id=project_id,
            correlation_id=correlation_id,
            objective=objective,
            instructions=list(meta.get("instructions", [])),
            context=dict(meta.get("context", {})),
            allowed_paths=allowed_paths,
            writable_paths=writable_paths,
            read_only_paths=read_only_paths,
            forbidden_paths=forbidden_paths,
            allowed_commands=cmds_raw,
            constraints=list(meta.get("constraints", [])),
            technical_requirements=list(meta.get("technical_requirements", [])),
            acceptance_criteria=ac_raw,
            required_checks=list(meta.get("required_checks", [])),
            research_evidence=ev_raw,
            dependencies=list(getattr(task, "dependencies", []) or meta.get("dependencies", [])),
            iteration_budget=int(meta.get("iteration_budget", 10)),
            time_budget=int(meta.get("time_budget", 600)),
            risk_level=risk_level,
            status=ProgrammerWorkOrderStatus.ASSIGNED,
            trace=meta.get("trace"),
            metadata=meta,
        )

    def create_execution(self, worker_id: str = "worker.programmer") -> ProgrammerExecution:
        """
        Spawn an active execution attempt for this work order.
        Validates authorization first to ensure malformed authorization cannot reach execution.
        Strictly preserves work_order_id, task_id, project_id, and correlation_id.
        """
        self.validate()
        return ProgrammerExecution(
            execution_id=new_execution_id(),
            work_order_id=self.work_order_id,
            task_id=self.manager_task_id,
            project_id=self.project_id,
            correlation_id=self.correlation_id,
            worker_id=worker_id,
            status=ProgrammerExecutionStatus.INITIALIZED,
            created_at=utc_now(),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "work_order_id": self.work_order_id,
            "manager_task_id": self.manager_task_id,
            "task_id": self.task_id,
            "project_id": self.project_id,
            "correlation_id": self.correlation_id,
            "objective": self.objective,
            "instructions": list(self.instructions),
            "context": dict(self.context),
            "allowed_paths": list(self.allowed_paths),
            "writable_paths": list(self.writable_paths),
            "read_only_paths": list(self.read_only_paths),
            "forbidden_paths": list(self.forbidden_paths),
            "allowed_commands": [
                c.to_dict() if isinstance(c, AllowedCommand) else c for c in self.allowed_commands
            ],
            "constraints": list(self.constraints),
            "technical_requirements": list(self.technical_requirements),
            "acceptance_criteria": [
                a.to_dict() if isinstance(a, AcceptanceCriterion) else a for a in self.acceptance_criteria
            ],
            "required_checks": list(self.required_checks),
            "research_evidence": [
                r.to_dict() if isinstance(r, ResearchEvidenceReference) else r for r in self.research_evidence
            ],
            "dependencies": list(self.dependencies),
            "iteration_budget": self.iteration_budget,
            "time_budget": self.time_budget,
            "risk_level": self.risk_level.value if isinstance(self.risk_level, RiskLevel) else str(self.risk_level),
            "status": self.status.value if isinstance(self.status, ProgrammerWorkOrderStatus) else str(self.status),
            "trace": self.trace,
            "metadata": dict(self.metadata),
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ProgrammerWorkOrder:
        st_raw = data.get("status", ProgrammerWorkOrderStatus.CREATED.value)
        try:
            status = ProgrammerWorkOrderStatus(st_raw)
        except (ValueError, TypeError):
            status = ProgrammerWorkOrderStatus.CREATED

        risk_raw = data.get("risk_level", RiskLevel.LOW.value)
        try:
            risk_level = RiskLevel(str(risk_raw).upper())
        except (ValueError, TypeError):
            risk_level = risk_raw  # Let validate catch invalid risk

        task_id = data.get("manager_task_id") or data.get("task_id", "")

        return cls(
            work_order_id=data.get("work_order_id", new_work_order_id()),
            manager_task_id=task_id,
            task_id=task_id,
            project_id=data.get("project_id", ""),
            correlation_id=data.get("correlation_id", ""),
            objective=str(data.get("objective", "")),
            instructions=list(data.get("instructions", [])),
            context=dict(data.get("context", {})),
            allowed_paths=list(data.get("allowed_paths", [])),
            writable_paths=list(data.get("writable_paths", [])),
            read_only_paths=list(data.get("read_only_paths", [])),
            forbidden_paths=list(data.get("forbidden_paths", [])),
            allowed_commands=data.get("allowed_commands", []),
            constraints=list(data.get("constraints", [])),
            technical_requirements=list(data.get("technical_requirements", [])),
            acceptance_criteria=data.get("acceptance_criteria", []),
            required_checks=list(data.get("required_checks", [])),
            research_evidence=data.get("research_evidence", []),
            dependencies=list(data.get("dependencies", [])),
            iteration_budget=int(data.get("iteration_budget", 10)),
            time_budget=int(data.get("time_budget", 600)),
            risk_level=risk_level,
            status=status,
            trace=data.get("trace"),
            metadata=dict(data.get("metadata", {})),
            created_at=data.get("created_at", utc_now()),
        )
