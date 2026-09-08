from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import json
import logging
from typing import Any, Optional, Sequence

from core.enums import RiskLevel
from core.programmer.contracts.command_resolver import (
    CommandBoundaryResolver,
    FORBIDDEN_SHELL_TOKENS,
)
from core.programmer.contracts.engineering_risk import (
    EngineeringRisk,
)
from core.programmer.contracts.escalation import (
    EscalationCoordinator,
    ProgrammerEscalation,
    ProgrammerEscalationCategory,
)
from core.programmer.contracts.event_translation import ProgrammerExecutionEvent
from core.programmer.contracts.execution import ProgrammerExecution
from core.programmer.contracts.execution_context import ProgrammerExecutionContext
from core.programmer.contracts.filesystem_resolver import (
    FilesystemBoundaryResolver,
    is_subpath_or_equal,
)
from core.programmer.contracts.identifiers import (
    new_escalation_candidate_id,
    new_plan_deviation_id,
    new_supervision_record_id,
    new_verification_evidence_id,
    validate_execution_id,
    validate_plan_deviation_id,
    validate_plan_id,
    validate_supervision_record_id,
    validate_work_order_id,
)
from core.programmer.contracts.implementation_plan import (
    EscalationCandidate,
    ImplementationPlan,
    ImplementationStep,
    utc_now,
)
from core.programmer.contracts.verification import VerificationEvidence
from core.programmer.contracts.work_order import ProgrammerWorkOrder
from core.programmer.errors import (
    ProgrammerLineageError,
    ProgrammerValidationError,
)
from core.programmer.types import (
    DeviationClassification,
    EngineeringRiskCategory,
    ExecutionSupervisionStatus,
    PlanDeviationCategory,
    ProgrammerBlockerSeverity,
    ProgrammerExecutionEventType,
    VerificationEvidenceSourceType,
)

logger = logging.getLogger("AutonomOS.Programmer.ExecutionSupervision")


@dataclass
class PlanDeviation:
    """
    Structured record of an observed execution divergence from planned expectations.

    Guarantees:
    - Classified into EXPECTED, MINOR, MATERIAL, or BLOCKING.
    - Preserves causal provenance (deviation_id, step_id, timestamp).
    - Encapsulates prospective EscalationCandidate when authority expansion is required.
    """
    deviation_id: str = field(default_factory=new_plan_deviation_id)
    classification: DeviationClassification = DeviationClassification.MINOR
    category: PlanDeviationCategory = PlanDeviationCategory.OTHER
    step_id: Optional[str] = None
    description: str = ""
    observed_fact: str = ""
    expected_behavior: str = ""
    action_taken: str = ""
    escalation_candidate: Optional[EscalationCandidate] = None
    evidence: Optional[VerificationEvidence] = None
    trace: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)
    timestamp: str = field(default_factory=utc_now)

    def __post_init__(self) -> None:
        if isinstance(self.classification, str):
            try:
                self.classification = DeviationClassification(self.classification.upper())
            except (ValueError, TypeError):
                self.classification = DeviationClassification.MINOR

        if isinstance(self.category, str):
            try:
                self.category = PlanDeviationCategory(self.category.upper())
            except (ValueError, TypeError):
                self.category = PlanDeviationCategory.OTHER

        if isinstance(self.escalation_candidate, dict):
            self.escalation_candidate = EscalationCandidate.from_dict(self.escalation_candidate)

        if isinstance(self.evidence, dict):
            self.evidence = VerificationEvidence.from_dict(self.evidence)

        self.validate()

    def validate(self) -> None:
        validate_plan_deviation_id(self.deviation_id)
        if not self.description:
            raise ProgrammerValidationError("PlanDeviation description cannot be empty.", field_name="description")

    def to_dict(self) -> dict[str, Any]:
        return {
            "deviation_id": self.deviation_id,
            "classification": self.classification.value,
            "category": self.category.value,
            "step_id": self.step_id,
            "description": self.description,
            "observed_fact": self.observed_fact,
            "expected_behavior": self.expected_behavior,
            "action_taken": self.action_taken,
            "escalation_candidate": self.escalation_candidate.to_dict() if self.escalation_candidate else None,
            "evidence": self.evidence.to_dict() if self.evidence else None,
            "trace": dict(self.trace),
            "metadata": dict(self.metadata),
            "timestamp": self.timestamp,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> PlanDeviation:
        esc_data = data.get("escalation_candidate")
        esc = EscalationCandidate.from_dict(esc_data) if esc_data else None

        ev_data = data.get("evidence")
        ev = VerificationEvidence.from_dict(ev_data) if ev_data else None

        return cls(
            deviation_id=data["deviation_id"],
            classification=DeviationClassification(str(data.get("classification", "MINOR")).upper()),
            category=PlanDeviationCategory(str(data.get("category", "OTHER")).upper()),
            step_id=data.get("step_id"),
            description=data.get("description", ""),
            observed_fact=data.get("observed_fact", ""),
            expected_behavior=data.get("expected_behavior", ""),
            action_taken=data.get("action_taken", ""),
            escalation_candidate=esc,
            evidence=ev,
            trace=dict(data.get("trace", {})),
            metadata=dict(data.get("metadata", {})),
            timestamp=data.get("timestamp", utc_now()),
        )


@dataclass
class ExecutionSupervisionRecord:
    """
    Comprehensive record tracking execution behavior against the validated plan.

    Guarantees:
    - Tracks step progress, files touched/modified, commands run, and verification results.
    - Records all deviations with four-level classification.
    - Aggregates emerging risks and blocker states.
    - Fully serializable with strict lineage validation.
    """
    supervision_id: str = field(default_factory=new_supervision_record_id)
    execution_id: str = ""
    work_order_id: str = ""
    plan_id: str = ""
    current_step_id: Optional[str] = None
    completed_steps: list[str] = field(default_factory=list)
    pending_steps: list[str] = field(default_factory=list)
    files_touched: list[str] = field(default_factory=list)
    files_modified: list[str] = field(default_factory=list)
    commands_executed: list[str] = field(default_factory=list)
    verification_results: list[dict[str, Any]] = field(default_factory=list)
    deviations: list[PlanDeviation] = field(default_factory=list)
    emerging_risks: list[EngineeringRisk] = field(default_factory=list)
    blockers: list[str] = field(default_factory=list)
    step_failure_counts: dict[str, int] = field(default_factory=dict)
    progress_percentage: float = 0.0
    status: ExecutionSupervisionStatus = ExecutionSupervisionStatus.SUPERVISING
    trace: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=utc_now)
    updated_at: str = field(default_factory=utc_now)

    def __post_init__(self) -> None:
        if isinstance(self.status, str):
            try:
                self.status = ExecutionSupervisionStatus(self.status.upper())
            except (ValueError, TypeError):
                self.status = ExecutionSupervisionStatus.SUPERVISING

        norm_devs: list[PlanDeviation] = []
        for d in self.deviations:
            if isinstance(d, dict):
                norm_devs.append(PlanDeviation.from_dict(d))
            else:
                norm_devs.append(d)
        self.deviations = norm_devs

        norm_risks: list[EngineeringRisk] = []
        for r in self.emerging_risks:
            if isinstance(r, dict):
                norm_risks.append(EngineeringRisk.from_dict(r))
            else:
                norm_risks.append(r)
        self.emerging_risks = norm_risks

        self.validate()

    def validate(self) -> None:
        validate_supervision_record_id(self.supervision_id)
        validate_execution_id(self.execution_id)
        validate_work_order_id(self.work_order_id)
        validate_plan_id(self.plan_id)

    def has_blocking_deviations(self) -> bool:
        return any(d.classification == DeviationClassification.BLOCKING for d in self.deviations)

    def has_material_deviations(self) -> bool:
        return any(d.classification in (DeviationClassification.MATERIAL, DeviationClassification.BLOCKING) for d in self.deviations)

    def get_deviations_by_classification(self, classification: DeviationClassification | str) -> list[PlanDeviation]:
        if isinstance(classification, str):
            classification = DeviationClassification(classification.upper())
        return [d for d in self.deviations if d.classification == classification]

    def to_dict(self) -> dict[str, Any]:
        return {
            "supervision_id": self.supervision_id,
            "execution_id": self.execution_id,
            "work_order_id": self.work_order_id,
            "plan_id": self.plan_id,
            "current_step_id": self.current_step_id,
            "completed_steps": list(self.completed_steps),
            "pending_steps": list(self.pending_steps),
            "files_touched": list(self.files_touched),
            "files_modified": list(self.files_modified),
            "commands_executed": list(self.commands_executed),
            "verification_results": [dict(r) for r in self.verification_results],
            "deviations": [d.to_dict() for d in self.deviations],
            "emerging_risks": [r.to_dict() for r in self.emerging_risks],
            "blockers": list(self.blockers),
            "step_failure_counts": dict(self.step_failure_counts),
            "progress_percentage": round(self.progress_percentage, 2),
            "status": self.status.value,
            "trace": dict(self.trace),
            "metadata": dict(self.metadata),
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ExecutionSupervisionRecord:
        return cls(
            supervision_id=data["supervision_id"],
            execution_id=data["execution_id"],
            work_order_id=data["work_order_id"],
            plan_id=data["plan_id"],
            current_step_id=data.get("current_step_id"),
            completed_steps=list(data.get("completed_steps", [])),
            pending_steps=list(data.get("pending_steps", [])),
            files_touched=list(data.get("files_touched", [])),
            files_modified=list(data.get("files_modified", [])),
            commands_executed=list(data.get("commands_executed", [])),
            verification_results=[dict(r) for r in data.get("verification_results", [])],
            deviations=[PlanDeviation.from_dict(d) for d in data.get("deviations", [])],
            emerging_risks=[EngineeringRisk.from_dict(r) for r in data.get("emerging_risks", [])],
            blockers=list(data.get("blockers", [])),
            step_failure_counts=dict(data.get("step_failure_counts", {})),
            progress_percentage=float(data.get("progress_percentage", 0.0)),
            status=ExecutionSupervisionStatus(str(data.get("status", "SUPERVISING")).upper()),
            trace=dict(data.get("trace", {})),
            metadata=dict(data.get("metadata", {})),
            created_at=data.get("created_at", utc_now()),
            updated_at=data.get("updated_at", utc_now()),
        )

    def to_json(self, indent: Optional[int] = None) -> str:
        return json.dumps(self.to_dict(), indent=indent)

    @classmethod
    def from_json(cls, json_str: str) -> ExecutionSupervisionRecord:
        return cls.from_dict(json.loads(json_str))


class ExecutionSupervisor:
    """
    Intelligent execution supervisor monitoring Cline execution against the validated ImplementationPlan.

    Core Invariants:
    - PASSIVE: Never executes implementation code, never modifies files, never expands permissions.
    - ACCURATE: Evaluates actions against planned expectations and WorkOrder constraints.
    - NON-PUNITIVE: Does NOT treat every deviation as a failure; classifies into EXPECTED, MINOR, MATERIAL, BLOCKING.
    - INTEGRATED: Connects with Phase 4 verification, Phase 5 recovery, and Phase 6 change tracking.
    - AUTHORITATIVE: Escalates blocking deviations through existing EscalationCoordinator.
    """

    def __init__(
        self,
        plan: ImplementationPlan,
        work_order: ProgrammerWorkOrder,
        execution_context: Optional[ProgrammerExecutionContext] = None,
        command_resolver: Optional[CommandBoundaryResolver] = None,
        fs_resolver: Optional[FilesystemBoundaryResolver] = None,
        repeat_failure_threshold: int = 2,
        scope_expansion_multiplier: float = 2.0,
    ) -> None:
        if plan.work_order_id != work_order.work_order_id:
            raise ProgrammerLineageError(
                f"ImplementationPlan work_order_id '{plan.work_order_id}' does not match "
                f"WorkOrder '{work_order.work_order_id}'."
            )
        if execution_context and execution_context.work_order_id != work_order.work_order_id:
            raise ProgrammerLineageError(
                f"ExecutionContext work_order_id '{execution_context.work_order_id}' does not match "
                f"WorkOrder '{work_order.work_order_id}'."
            )

        self.plan = plan
        self.work_order = work_order
        self.execution_context = execution_context
        self.command_resolver = command_resolver or CommandBoundaryResolver()
        self.fs_resolver = fs_resolver or FilesystemBoundaryResolver()
        self.repeat_failure_threshold = repeat_failure_threshold
        self.scope_expansion_multiplier = scope_expansion_multiplier

        # Build step lookup
        self._step_by_id: dict[str, ImplementationStep] = {s.step_id: s for s in plan.steps}

        execution_id = plan.execution_id or (execution_context.execution_id if execution_context else "")

        self.record = ExecutionSupervisionRecord(
            supervision_id=new_supervision_record_id(),
            execution_id=execution_id,
            work_order_id=work_order.work_order_id,
            plan_id=plan.plan_id,
            current_step_id=plan.steps[0].step_id if plan.steps else None,
            completed_steps=[],
            pending_steps=[s.step_id for s in plan.steps],
            files_touched=[],
            files_modified=[],
            commands_executed=[],
            deviations=[],
            emerging_risks=[],
            blockers=[],
            step_failure_counts={},
            progress_percentage=0.0,
            status=ExecutionSupervisionStatus.SUPERVISING,
            trace={
                "plan_id": plan.plan_id,
                "work_order_id": work_order.work_order_id,
                "execution_id": execution_id,
                "started_at": utc_now(),
            },
        )

    # -------------------------------------------------------------------------
    # Event Observation
    # -------------------------------------------------------------------------

    def observe_event(self, event: ProgrammerExecutionEvent) -> Optional[PlanDeviation]:
        """
        Observe a normalized execution event and compare against plan expectations.
        Returns a PlanDeviation if a meaningful divergence is detected.
        """
        self.record.updated_at = utc_now()
        payload = event.payload or {}
        deviation: Optional[PlanDeviation] = None

        # 1. File Operations
        if event.event_type == ProgrammerExecutionEventType.FILE_OPERATION:
            path = payload.get("path", "")
            operation = payload.get("operation", "read").lower()
            if path:
                if path not in self.record.files_touched:
                    self.record.files_touched.append(path)

                is_write = operation in ("write", "modify", "edit", "create", "delete", "append")
                if is_write and path not in self.record.files_modified:
                    self.record.files_modified.append(path)

                deviation = self._evaluate_file_operation(path=path, is_write=is_write)

        # 2. Command Operations
        elif event.event_type == ProgrammerExecutionEventType.COMMAND_OPERATION:
            command = payload.get("command", "")
            if command:
                if command not in self.record.commands_executed:
                    self.record.commands_executed.append(command)
                deviation = self._evaluate_command_operation(command=command)

        # 3. Warning / Blocker Events
        elif event.event_type == ProgrammerExecutionEventType.WARNING:
            warn_msg = payload.get("message", "Execution warning")
            if warn_msg not in self.record.blockers:
                self.record.blockers.append(warn_msg)

        # 4. Error Events
        elif event.event_type == ProgrammerExecutionEventType.ERROR:
            err_msg = payload.get("message", "Execution error")
            if err_msg not in self.record.blockers:
                self.record.blockers.append(err_msg)

        if deviation:
            self._record_deviation(deviation)

        return deviation

    def _evaluate_file_operation(self, path: str, is_write: bool) -> Optional[PlanDeviation]:
        """Compare file access or modification against plan and WorkOrder scope."""
        # Check forbidden paths
        is_forbidden = any(is_subpath_or_equal(path, fp) for fp in self.work_order.forbidden_paths)
        if is_forbidden:
            candidate = EscalationCandidate(
                candidate_id=new_escalation_candidate_id(),
                category=ProgrammerEscalationCategory.SCOPE,
                reason=f"Execution attempted access to forbidden path '{path}'.",
                target=path,
                requested_decision=f"Manager authority required to access forbidden path '{path}'.",
                severity=ProgrammerBlockerSeverity.CRITICAL,
                suggested_options=["Terminate execution", "Adjust forbidden_paths in revised WorkOrder"],
            )
            return PlanDeviation(
                deviation_id=new_plan_deviation_id(),
                classification=DeviationClassification.BLOCKING,
                category=PlanDeviationCategory.UNEXPECTED_FILE,
                step_id=self.record.current_step_id,
                description=f"Access to forbidden path: '{path}'.",
                observed_fact=f"Attempted {'write' if is_write else 'read'} to forbidden path '{path}'.",
                expected_behavior=f"Path '{path}' is strictly forbidden by WorkOrder.",
                action_taken="BLOCKED",
                escalation_candidate=candidate,
            )

        # If write operation
        if is_write:
            # Check writable paths
            is_writable = any(is_subpath_or_equal(path, wp) for wp in self.work_order.writable_paths)
            if not is_writable:
                candidate = EscalationCandidate(
                    candidate_id=new_escalation_candidate_id(),
                    category=ProgrammerEscalationCategory.SCOPE,
                    reason=f"Execution modified file '{path}' which is outside authorized writable_paths.",
                    target=path,
                    requested_decision=f"Grant write permissions for '{path}' via revised WorkOrder.",
                    severity=ProgrammerBlockerSeverity.HIGH,
                    suggested_options=[f"Add '{path}' to writable_paths", "Revert changes to '{path}'"],
                )
                return PlanDeviation(
                    deviation_id=new_plan_deviation_id(),
                    classification=DeviationClassification.BLOCKING,
                    category=PlanDeviationCategory.UNEXPECTED_FILE,
                    step_id=self.record.current_step_id,
                    description=f"Write to non-writable path: '{path}'.",
                    observed_fact=f"File '{path}' modified outside writable_paths.",
                    expected_behavior=f"Path '{path}' must be in authorized writable_paths.",
                    action_taken="BLOCKED",
                    escalation_candidate=candidate,
                )

            # Within writable paths: check if expected in current step or overall plan
            current_step = self._step_by_id.get(self.record.current_step_id or "")
            step_targets = current_step.target_files if current_step else []
            is_step_target = any(is_subpath_or_equal(path, st) for st in step_targets)
            is_plan_target = any(is_subpath_or_equal(path, af) for af in self.plan.affected_files)

            if not is_step_target and not is_plan_target:
                # Modifying an unexpected file within authorized scope -> MATERIAL deviation
                return PlanDeviation(
                    deviation_id=new_plan_deviation_id(),
                    classification=DeviationClassification.MATERIAL,
                    category=PlanDeviationCategory.UNEXPECTED_FILE,
                    step_id=self.record.current_step_id,
                    description=f"Unexpected file modified: '{path}' was not listed in plan targets.",
                    observed_fact=f"Modified file '{path}' not in step {self.record.current_step_id} or plan affected_files.",
                    expected_behavior=f"Modifications should target planned files: {self.plan.affected_files}.",
                    action_taken="SURFACED_POLICY",
                )

        else:
            # Read operation
            is_allowed = any(is_subpath_or_equal(path, ap) for ap in self.work_order.allowed_paths)
            if not is_allowed:
                candidate = EscalationCandidate(
                    candidate_id=new_escalation_candidate_id(),
                    category=ProgrammerEscalationCategory.SCOPE,
                    reason=f"Execution read file '{path}' which is outside authorized allowed_paths.",
                    target=path,
                    requested_decision=f"Grant read access for '{path}' via revised WorkOrder.",
                    severity=ProgrammerBlockerSeverity.MEDIUM,
                    suggested_options=[f"Add '{path}' to allowed_paths", "Avoid inspecting '{path}'"],
                )
                return PlanDeviation(
                    deviation_id=new_plan_deviation_id(),
                    classification=DeviationClassification.BLOCKING,
                    category=PlanDeviationCategory.UNEXPECTED_FILE,
                    step_id=self.record.current_step_id,
                    description=f"Read outside allowed paths: '{path}'.",
                    observed_fact=f"Inspected file '{path}' outside allowed_paths.",
                    expected_behavior=f"Reads must remain within {self.work_order.allowed_paths}.",
                    action_taken="BLOCKED",
                    escalation_candidate=candidate,
                )

            # Allowed read: check if unlisted in plan
            current_step = self._step_by_id.get(self.record.current_step_id or "")
            step_targets = current_step.target_files if current_step else []
            if not any(is_subpath_or_equal(path, st) for st in step_targets):
                return PlanDeviation(
                    deviation_id=new_plan_deviation_id(),
                    classification=DeviationClassification.EXPECTED,
                    category=PlanDeviationCategory.UNEXPECTED_FILE,
                    step_id=self.record.current_step_id,
                    description=f"Read auxiliary file '{path}' within allowed scope.",
                    observed_fact=f"Inspected '{path}' outside current step targets.",
                    expected_behavior="Routine auxiliary inspection within allowed paths.",
                    action_taken="ALLOWED_AUTONOMOUS",
                )

        return None

    def _evaluate_command_operation(self, command: str) -> Optional[PlanDeviation]:
        """Validate executed command against WorkOrder authorization and plan expectations."""
        # 1. Shell metacharacter check
        for token in FORBIDDEN_SHELL_TOKENS:
            if token in command:
                candidate = EscalationCandidate(
                    candidate_id=new_escalation_candidate_id(),
                    category=ProgrammerEscalationCategory.PERMISSION,
                    reason=f"Dangerous shell chaining construct '{token}' detected in command '{command}'.",
                    target=command,
                    requested_decision="Forbidden shell construct cannot be executed.",
                    severity=ProgrammerBlockerSeverity.CRITICAL,
                    suggested_options=["Refactor command into single discrete command", "Terminate execution"],
                )
                return PlanDeviation(
                    deviation_id=new_plan_deviation_id(),
                    classification=DeviationClassification.BLOCKING,
                    category=PlanDeviationCategory.UNEXPECTED_COMMAND,
                    step_id=self.record.current_step_id,
                    description=f"Forbidden shell token '{token}' in command: '{command}'.",
                    observed_fact=f"Executed command with chaining token '{token}'.",
                    expected_behavior="Commands must not contain shell chaining or redirection tokens.",
                    action_taken="BLOCKED",
                    escalation_candidate=candidate,
                )

        # 2. Authorization check
        resolver = self.execution_context.command_policy if self.execution_context else self.command_resolver
        decision = resolver.resolve(work_order=self.work_order, request=command)
        if not decision.allowed:
            candidate = EscalationCandidate(
                candidate_id=new_escalation_candidate_id(),
                category=ProgrammerEscalationCategory.PERMISSION,
                reason=f"Command '{command}' is not authorized by WorkOrder: {decision.reason}",
                target=command,
                requested_decision=f"Authorize command '{command}' in WorkOrder allowed_commands.",
                severity=ProgrammerBlockerSeverity.HIGH,
                suggested_options=[f"Add '{command}' to allowed_commands", "Use authorized alternative"],
            )
            return PlanDeviation(
                deviation_id=new_plan_deviation_id(),
                classification=DeviationClassification.BLOCKING,
                category=PlanDeviationCategory.UNEXPECTED_COMMAND,
                step_id=self.record.current_step_id,
                description=f"Unauthorized command: '{command}'.",
                observed_fact=f"Attempted execution of unauthorized command: {decision.reason}",
                expected_behavior=f"Commands must be explicitly authorized in WorkOrder allowed_commands.",
                action_taken="BLOCKED",
                escalation_candidate=candidate,
            )

        # 3. Check if command was expected in plan
        current_step = self._step_by_id.get(self.record.current_step_id or "")
        step_verification = current_step.verification if current_step else ""
        plan_checks = self.plan.required_checks

        is_expected = (
            (step_verification and command in step_verification)
            or any(command in chk or chk in command for chk in plan_checks)
        )
        if not is_expected:
            return PlanDeviation(
                deviation_id=new_plan_deviation_id(),
                classification=DeviationClassification.MINOR,
                category=PlanDeviationCategory.UNEXPECTED_COMMAND,
                step_id=self.record.current_step_id,
                description=f"Authorized auxiliary command executed: '{command}'.",
                observed_fact=f"Command '{command}' is authorized but not listed in step verification.",
                expected_behavior="Expected planned verification commands.",
                action_taken="ALLOWED_AUTONOMOUS",
            )

        return None

    # -------------------------------------------------------------------------
    # Step Lifecycle Tracking
    # -------------------------------------------------------------------------

    def record_step_start(self, step_id: str) -> Optional[PlanDeviation]:
        """
        Record start of a planned step, validating prerequisite dependencies.
        Returns a PlanDeviation if dependency order is violated.
        """
        self.record.updated_at = utc_now()
        step = self._step_by_id.get(step_id)
        if not step:
            dev = PlanDeviation(
                deviation_id=new_plan_deviation_id(),
                classification=DeviationClassification.MATERIAL,
                category=PlanDeviationCategory.STEP_DEPENDENCY_VIOLATION,
                step_id=step_id,
                description=f"Unrecognized step_id '{step_id}' started.",
                observed_fact=f"Step '{step_id}' is not in plan steps.",
                expected_behavior=f"Steps must be one of: {list(self._step_by_id.keys())}.",
                action_taken="SURFACED_POLICY",
            )
            self._record_deviation(dev)
            return dev

        # Check dependencies
        missing_deps = [dep for dep in step.dependencies if dep not in self.record.completed_steps]
        if missing_deps:
            dev = PlanDeviation(
                deviation_id=new_plan_deviation_id(),
                classification=DeviationClassification.MATERIAL,
                category=PlanDeviationCategory.STEP_DEPENDENCY_VIOLATION,
                step_id=step_id,
                description=f"Step '{step_id}' started before prerequisites completed: {missing_deps}.",
                observed_fact=f"Prerequisite steps {missing_deps} are not in completed_steps.",
                expected_behavior=f"Prerequisites {step.dependencies} must be completed before '{step_id}'.",
                action_taken="SURFACED_POLICY",
            )
            self._record_deviation(dev)
            self.record.current_step_id = step_id
            return dev

        self.record.current_step_id = step_id
        return None

    def record_step_completion(
        self,
        step_id: str,
        verification_passed: bool = True,
        verification_output: str = "",
    ) -> Optional[PlanDeviation]:
        """
        Record completion of a planned step with verification outcome.
        Returns a PlanDeviation if required verification was missing or failed.
        """
        self.record.updated_at = utc_now()
        step = self._step_by_id.get(step_id)
        deviation: Optional[PlanDeviation] = None

        if step and step.verification and not verification_passed:
            deviation = PlanDeviation(
                deviation_id=new_plan_deviation_id(),
                classification=DeviationClassification.MATERIAL,
                category=PlanDeviationCategory.MISSING_VERIFICATION,
                step_id=step_id,
                description=f"Verification failed or unverified for step '{step_id}'.",
                observed_fact=f"Step '{step_id}' completed but verification did not pass: {verification_output}",
                expected_behavior=f"Verification '{step.verification}' must pass.",
                action_taken="SURFACED_POLICY",
            )
            self._record_deviation(deviation)

        # Update completed/pending lists
        if step_id not in self.record.completed_steps:
            self.record.completed_steps.append(step_id)
        if step_id in self.record.pending_steps:
            self.record.pending_steps.remove(step_id)

        # Record verification outcome
        self.record.verification_results.append({
            "step_id": step_id,
            "passed": verification_passed,
            "output": verification_output,
            "timestamp": utc_now(),
        })

        # Recalculate progress
        total_steps = len(self.plan.steps)
        if total_steps > 0:
            self.record.progress_percentage = (len(self.record.completed_steps) / total_steps) * 100.0

        if len(self.record.completed_steps) == total_steps and not self.record.has_blocking_deviations():
            self.record.status = ExecutionSupervisionStatus.COMPLETED

        return deviation

    def record_step_failure(self, step_id: str, error_message: str = "") -> Optional[PlanDeviation]:
        """
        Record failure of a step, detecting repeated failures.
        Returns a PlanDeviation if repeated failures exceed threshold.
        """
        self.record.updated_at = utc_now()
        count = self.record.step_failure_counts.get(step_id, 0) + 1
        self.record.step_failure_counts[step_id] = count

        if count >= self.repeat_failure_threshold:
            dev = PlanDeviation(
                deviation_id=new_plan_deviation_id(),
                classification=DeviationClassification.MATERIAL,
                category=PlanDeviationCategory.REPEATED_FAILURE,
                step_id=step_id,
                description=f"Repeated failure on step '{step_id}' ({count} attempts): {error_message}",
                observed_fact=f"Step '{step_id}' failed {count} times consecutively.",
                expected_behavior=f"Step '{step_id}' should succeed within {self.repeat_failure_threshold} attempts.",
                action_taken="SURFACED_POLICY",
            )
            self._record_deviation(dev)
            return dev

        return None

    # -------------------------------------------------------------------------
    # Risk & Scope Dynamics
    # -------------------------------------------------------------------------

    def record_emerging_risk(self, risk: EngineeringRisk) -> Optional[PlanDeviation]:
        """
        Record an emerging engineering risk discovered during execution.
        If the risk requires escalation or has HIGH/CRITICAL severity, creates a BLOCKING deviation.
        """
        self.record.updated_at = utc_now()
        self.record.emerging_risks.append(risk)

        is_material = risk.escalation_required or risk.severity in (RiskLevel.HIGH, RiskLevel.CRITICAL)
        if is_material:
            cand = risk.to_escalation_candidate()
            dev = PlanDeviation(
                deviation_id=new_plan_deviation_id(),
                classification=DeviationClassification.BLOCKING,
                category=PlanDeviationCategory.EMERGING_RISK,
                step_id=self.record.current_step_id,
                description=f"Emerging material risk [{risk.category.value}]: {risk.description}",
                observed_fact=f"Discovered {risk.severity.value} risk in {risk.affected_area}: {risk.description}",
                expected_behavior="Execution must not introduce unauthorized material engineering risks.",
                action_taken="BLOCKED",
                escalation_candidate=cand,
            )
            self._record_deviation(dev)
            self.record.status = ExecutionSupervisionStatus.BLOCKED
            return dev
        else:
            dev = PlanDeviation(
                deviation_id=new_plan_deviation_id(),
                classification=DeviationClassification.MINOR,
                category=PlanDeviationCategory.EMERGING_RISK,
                step_id=self.record.current_step_id,
                description=f"Advisory emerging risk [{risk.category.value}]: {risk.description}",
                observed_fact=f"Observed low/medium risk: {risk.description}",
                expected_behavior="Low impact risk noted for trace.",
                action_taken="ALLOWED_AUTONOMOUS",
            )
            self._record_deviation(dev)
            return dev

    def check_scope_expansion(self) -> Optional[PlanDeviation]:
        """
        Evaluate whether the number of modified files significantly exceeds planned expectations.
        Returns a MATERIAL PlanDeviation if scope expanded significantly beyond plan.
        """
        self.record.updated_at = utc_now()
        planned_count = max(1, len(self.plan.affected_files))
        modified_count = len(self.record.files_modified)

        if modified_count > max(4, int(planned_count * self.scope_expansion_multiplier)):
            dev = PlanDeviation(
                deviation_id=new_plan_deviation_id(),
                classification=DeviationClassification.MATERIAL,
                category=PlanDeviationCategory.SCOPE_EXPANSION,
                step_id=self.record.current_step_id,
                description=f"Scope expansion detected: {modified_count} files modified (planned: {planned_count}).",
                observed_fact=f"Modified {modified_count} files exceeding planned count {planned_count} by {self.scope_expansion_multiplier}x.",
                expected_behavior=f"Implementation should remain near planned scope ({self.plan.affected_files}).",
                action_taken="SURFACED_POLICY",
            )
            self._record_deviation(dev)
            return dev

        return None

    # -------------------------------------------------------------------------
    # Context Provision & Manager Escalation
    # -------------------------------------------------------------------------

    def provide_correction_context(self) -> dict[str, Any]:
        """
        Generate structured execution context for Phase 4 BoundedCorrectionLoop
        and Phase 5 FailureRecoveryController.
        """
        unverified = [
            s.step_id for s in self.plan.steps
            if s.step_id in self.record.completed_steps
            and any(
                r["step_id"] == s.step_id and not r["passed"]
                for r in self.record.verification_results
            )
        ]

        return {
            "supervision_id": self.record.supervision_id,
            "plan_id": self.plan.plan_id,
            "current_step_id": self.record.current_step_id,
            "completed_steps": list(self.record.completed_steps),
            "pending_steps": list(self.record.pending_steps),
            "progress_percentage": self.record.progress_percentage,
            "files_touched": list(self.record.files_touched),
            "files_modified": list(self.record.files_modified),
            "step_failure_counts": dict(self.record.step_failure_counts),
            "unverified_steps": unverified,
            "active_deviations_count": len(self.record.deviations),
            "material_deviations": [d.to_dict() for d in self.record.get_deviations_by_classification(DeviationClassification.MATERIAL)],
            "blocking_deviations": [d.to_dict() for d in self.record.get_deviations_by_classification(DeviationClassification.BLOCKING)],
            "emerging_risks": [r.to_dict() for r in self.record.emerging_risks],
            "blockers": list(self.record.blockers),
            "status": self.record.status.value,
        }

    def escalate_blocking(
        self,
        deviation: PlanDeviation,
        coordinator: EscalationCoordinator,
        execution: ProgrammerExecution,
    ) -> Optional[ProgrammerEscalation]:
        """
        Forward a blocking deviation to Manager authority via the existing Phase 5 EscalationCoordinator.
        """
        if deviation.classification != DeviationClassification.BLOCKING:
            return None

        if not deviation.escalation_candidate:
            deviation.escalation_candidate = EscalationCandidate(
                candidate_id=new_escalation_candidate_id(),
                category=ProgrammerEscalationCategory.SCOPE,
                reason=deviation.description,
                target=deviation.observed_fact,
                requested_decision=f"Manager authority required for blocking deviation: {deviation.description}",
                severity=ProgrammerBlockerSeverity.HIGH,
                suggested_options=["Revise WorkOrder to authorize action", "Reject action and abort execution"],
            )

        escalation = coordinator.escalate_candidate(
            execution=execution,
            work_order=self.work_order,
            candidate=deviation.escalation_candidate,
        )

        self.record.status = ExecutionSupervisionStatus.ESCALATED
        self.record.updated_at = utc_now()
        return escalation

    def handle_cancellation(self, reason: str = "Execution cancelled") -> None:
        """Record execution cancellation in supervision state."""
        self.record.status = ExecutionSupervisionStatus.CANCELLED
        self.record.blockers.append(f"Cancelled: {reason}")
        self.record.updated_at = utc_now()

    def _record_deviation(self, deviation: PlanDeviation) -> None:
        """Internal helper to append deviation and update supervision status."""
        self.record.deviations.append(deviation)
        if deviation.classification == DeviationClassification.BLOCKING:
            self.record.status = ExecutionSupervisionStatus.BLOCKED
            if deviation.description not in self.record.blockers:
                self.record.blockers.append(deviation.description)

    def to_record(self) -> ExecutionSupervisionRecord:
        """Return the underlying ExecutionSupervisionRecord."""
        return self.record

    def record_event(self, event: Any) -> Optional[PlanDeviation]:
        """Alias for observe_event for event recording."""
        return self.observe_event(event)

    @property
    def deviations(self) -> list[PlanDeviation]:
        """Return deviations recorded by the supervisor."""
        return self.record.deviations

    @property
    def blockers(self) -> list[str]:
        """Return blockers recorded by the supervisor."""
        return self.record.blockers

    def has_blocking_deviations(self) -> bool:
        """Check if any blocking deviations have been recorded."""
        return self.record.has_blocking_deviations()
