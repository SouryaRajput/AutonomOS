from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional, Sequence

from core.tester.contracts.blocker import TesterBlocker
from core.tester.contracts.boundary import TesterBoundaryGuard
from core.tester.contracts.finding import (
    AcceptanceCriterionResult,
    TesterDefect,
    TesterEvidence,
    TesterFinding,
)
from core.tester.contracts.identifiers import (
    new_blocker_id,
    new_defect_id,
    new_execution_id,
    new_finding_id,
    new_trace_id,
    validate_execution_id,
    validate_work_order_id,
)
from core.tester.contracts.lifecycle import TesterLifecycle
from core.tester.contracts.result import TesterResult
from core.tester.contracts.test_case import TestCaseResult
from core.tester.contracts.trace import TesterTrace
from core.tester.errors import (
    InvalidTesterTransitionError,
    TesterLineageError,
    TesterValidationError,
)
from core.tester.types import (
    AcceptanceCriterionStatus,
    DefectSeverity,
    DefectType,
    FindingCategory,
    ShipRecommendation,
    TesterActionType,
    TesterBlockerCategory,
    TesterBlockerSeverity,
    TesterExecutionPhase,
    TesterExecutionStatus,
    TesterResultStatus,
)


def utc_now() -> str:
    """Generate ISO 8601 UTC timestamp."""
    return datetime.now(timezone.utc).isoformat()


@dataclass
class TesterExecution:
    """
    Stateful execution session of a TesterWorkOrder.
    Tracks execution attempt lifecycle, operational transitions, generated traces,
    recorded defects, evaluative findings, test cases, and produces final TesterResult.
    """
    __test__ = False
    execution_id: str
    work_order_id: str
    task_id: str
    project_id: str
    correlation_id: str
    worker_id: str = "worker.tester"
    status: TesterExecutionStatus = TesterExecutionStatus.REQUESTED
    current_phase: TesterExecutionPhase = TesterExecutionPhase.PREPARING
    cancellation_reason: Optional[str] = None
    started_at: Optional[str] = None
    completed_at: Optional[str] = None
    traces: list[TesterTrace] = field(default_factory=list)
    transition_history: list[dict[str, Any]] = field(default_factory=list)
    test_cases: list[TestCaseResult] = field(default_factory=list)
    evidence: list[TesterEvidence] = field(default_factory=list)
    defects: list[TesterDefect] = field(default_factory=list)
    findings: list[TesterFinding] = field(default_factory=list)
    acceptance_results: list[AcceptanceCriterionResult] = field(default_factory=list)
    blockers: list[TesterBlocker] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=utc_now)

    def __post_init__(self) -> None:
        validate_execution_id(self.execution_id)
        validate_work_order_id(self.work_order_id)
        if not self.task_id:
            raise TesterLineageError("TesterExecution must have a valid non-empty task_id.")
        if not self.project_id:
            raise TesterLineageError("TesterExecution must have a valid non-empty project_id.")
        if not self.correlation_id:
            raise TesterLineageError("TesterExecution must have a valid non-empty correlation_id.")
        if not isinstance(self.status, TesterExecutionStatus):
            self.status = TesterLifecycle.to_status(self.status)
        if not isinstance(self.current_phase, TesterExecutionPhase):
            try:
                self.current_phase = TesterExecutionPhase(str(self.current_phase).upper())
            except (ValueError, KeyError):
                self.current_phase = TesterExecutionPhase.PREPARING

    @property
    def active_blockers(self) -> list[TesterBlocker]:
        """Return list of currently unresolved blockers."""
        return [b for b in self.blockers if not b.is_resolved]

    @property
    def is_terminal(self) -> bool:
        """Check whether execution is in a terminal state."""
        return TesterLifecycle.is_terminal(self.status)

    @property
    def is_active(self) -> bool:
        """Check whether execution is in an active non-terminal state."""
        return TesterLifecycle.is_active(self.status)

    def _validate_completion(self) -> None:
        """
        Validate preconditions before transitioning from REPORTING to COMPLETED.
        Enforces that execution finished legitimately and deterministically.
        Crucial invariant: Do NOT require all tests to pass! Execution success != Product acceptance.
        """
        current = self.status if isinstance(self.status, TesterExecutionStatus) else TesterLifecycle.to_status(self.status)
        if current != TesterExecutionStatus.REPORTING:
            raise InvalidTesterTransitionError(
                entity_id=self.execution_id,
                current_status=current.value,
                target_status=TesterExecutionStatus.COMPLETED.value,
                reason="Execution can only transition to COMPLETED from REPORTING.",
            )
        if not self.work_order_id or not self.task_id or not self.project_id:
            raise TesterLineageError(
                "Execution completion validation failed: missing valid lineage (work_order_id, task_id, or project_id)."
            )
        if self.active_blockers:
            blocker_ids = [b.blocker_id for b in self.active_blockers]
            raise TesterValidationError(
                f"Cannot complete execution with unresolved blockers: {blocker_ids}. "
                "All blockers must be resolved before completing."
            )
        # At least one activity or observation must have taken place
        has_activity = bool(
            self.traces
            or self.defects
            or self.findings
            or self.acceptance_results
            or self.test_cases
            or self.evidence
        )
        if not has_activity:
            raise TesterValidationError(
                "Cannot complete execution without any recorded activity, traces, test cases, findings, or evaluation."
            )

    def transition_to(
        self,
        target_status: TesterExecutionStatus,
        reason: str = "",
        blocker: Optional[TesterBlocker] = None,
    ) -> None:
        """
        Validate and apply a deterministic lifecycle state transition.
        Raises InvalidTesterTransitionError or TesterValidationError if the transition is illegal.
        """
        current = self.status if isinstance(self.status, TesterExecutionStatus) else TesterLifecycle.to_status(self.status)
        target = target_status if isinstance(target_status, TesterExecutionStatus) else TesterLifecycle.to_status(target_status)

        if current == target:
            return

        # Validate against lifecycle state machine
        TesterLifecycle.validate_transition(
            entity_id=self.execution_id,
            current_status=current,
            target_status=target,
            reason=reason,
        )

        # Pre-transition validation for BLOCKED
        if target == TesterExecutionStatus.BLOCKED:
            if not reason and blocker is None:
                raise TesterValidationError("Transition to BLOCKED requires a factual reason or blocker specification.")

        # Pre-transition completion validation if target is COMPLETED
        if target == TesterExecutionStatus.COMPLETED:
            self._validate_completion()

        now = utc_now()

        # Handle cancellation
        if target == TesterExecutionStatus.CANCELLED:
            self.cancellation_reason = reason or "Execution cancelled by manager"

        # Apply state transition
        self.status = target

        # Update operational phase
        if target == TesterExecutionStatus.STARTING:
            self.current_phase = TesterExecutionPhase.PREPARING
        elif target == TesterExecutionStatus.RUNNING:
            self.current_phase = TesterExecutionPhase.EXECUTING
        elif target == TesterExecutionStatus.EVALUATING:
            self.current_phase = TesterExecutionPhase.EVALUATING
        elif target == TesterExecutionStatus.REPORTING:
            self.current_phase = TesterExecutionPhase.REPORTING

        # Timestamps
        if target in {
            TesterExecutionStatus.RUNNING,
            TesterExecutionStatus.EVALUATING,
            TesterExecutionStatus.REPORTING,
        } and not self.started_at:
            self.started_at = now
        elif target in TesterLifecycle.TERMINAL_STATUSES:
            self.completed_at = now

        # Record transition history
        self.transition_history.append({
            "previous_status": current.value,
            "new_status": target.value,
            "timestamp": now,
            "reason": reason,
        })

        # Generate auditable lifecycle transition trace
        action_type = TesterActionType.INITIALIZE if target == TesterExecutionStatus.STARTING else TesterActionType.OBSERVE
        trace = TesterTrace(
            trace_id=new_trace_id(),
            execution_id=self.execution_id,
            work_order_id=self.work_order_id,
            task_id=self.task_id,
            project_id=self.project_id,
            correlation_id=self.correlation_id,
            action_type=action_type,
            action_details={
                "transition": f"{current.value} -> {target.value}",
                "previous_status": current.value,
                "new_status": target.value,
                "reason": reason,
            },
            timestamp=now,
        )
        self.traces.append(trace)

        # Record blocker if specified
        if blocker is not None:
            self.blockers.append(blocker)
        elif target == TesterExecutionStatus.BLOCKED and reason:
            blk = TesterBlocker(
                blocker_id=new_blocker_id(),
                work_order_id=self.work_order_id,
                category=TesterBlockerCategory.OTHER,
                description=reason,
                execution_id=self.execution_id,
                task_id=self.task_id,
            )
            self.blockers.append(blk)

    def block(
        self,
        reason: str,
        category: TesterBlockerCategory = TesterBlockerCategory.OTHER,
        severity: TesterBlockerSeverity = TesterBlockerSeverity.HIGH,
        required_decision: str = "",
        required_context: Optional[str] = None,
        blocker_id: Optional[str] = None,
    ) -> TesterBlocker:
        """
        Record an operational impediment, transition to BLOCKED, and record the blocker.
        BLOCKED indicates Tester requires Manager intervention, permissions, or context.
        """
        blk = TesterBlocker(
            blocker_id=blocker_id or new_blocker_id(),
            work_order_id=self.work_order_id,
            category=category,
            description=reason,
            severity=severity,
            required_decision=required_decision,
            required_context=required_context,
            execution_id=self.execution_id,
            task_id=self.task_id,
        )
        self.transition_to(
            TesterExecutionStatus.BLOCKED,
            reason=reason,
            blocker=blk,
        )
        return blk

    def unblock(
        self,
        resolution_notes: str,
        resolved_by: str = "manager",
        target_status: TesterExecutionStatus = TesterExecutionStatus.RUNNING,
    ) -> None:
        """
        Resolve all active blockers with provenance and transition to target status (STARTING or RUNNING).
        """
        if not resolution_notes or not resolution_notes.strip():
            raise TesterValidationError("Unblocking requires non-empty resolution notes.")
        for b in self.active_blockers:
            b.resolve(resolution_notes=resolution_notes, resolved_by=resolved_by)
        self.transition_to(target_status, reason=f"Unblocked: {resolution_notes}")

    def cancel(self, reason: str = "Execution cancelled by manager") -> None:
        """
        Explicitly cancel the execution session.
        Preserves all accumulated traces, defects, findings, test cases, and evidence
        without marking the execution as a runtime failure.
        """
        self.transition_to(TesterExecutionStatus.CANCELLED, reason=reason)

    def create_trace(
        self,
        action_type: TesterActionType,
        action_details: Optional[dict[str, Any]] = None,
    ) -> TesterTrace:
        """Create and record an auditable execution trace adhering to strict causal lineage."""
        current = self.status if isinstance(self.status, TesterExecutionStatus) else TesterLifecycle.to_status(self.status)
        if action_type in {TesterActionType.EXECUTE_TEST, TesterActionType.CAPTURE_EVIDENCE} and current in {
            TesterExecutionStatus.REPORTING,
            TesterExecutionStatus.COMPLETED,
            TesterExecutionStatus.FAILED,
            TesterExecutionStatus.CANCELLED,
        }:
            raise TesterValidationError(
                f"Cannot perform test execution action '{action_type.value}' while execution is in '{current.value}' state."
            )
        trace = TesterTrace(
            trace_id=new_trace_id(),
            execution_id=self.execution_id,
            work_order_id=self.work_order_id,
            task_id=self.task_id,
            project_id=self.project_id,
            correlation_id=self.correlation_id,
            action_type=action_type,
            action_details=dict(action_details or {}),
        )
        self.traces.append(trace)
        return trace

    def record_test_case(self, test_case: TestCaseResult) -> TestCaseResult:
        """
        Record an evaluated test case result.
        Enforces that tests cannot be executed while in REPORTING or terminal states.
        """
        current = self.status if isinstance(self.status, TesterExecutionStatus) else TesterLifecycle.to_status(self.status)
        if current in {
            TesterExecutionStatus.REPORTING,
            TesterExecutionStatus.COMPLETED,
            TesterExecutionStatus.FAILED,
            TesterExecutionStatus.CANCELLED,
        }:
            raise TesterValidationError(
                f"Cannot execute or record test cases while execution is in '{current.value}' state. "
                "Tests can only be executed during RUNNING or EVALUATING phases."
            )
        self.test_cases.append(test_case)
        return test_case

    def record_evidence(self, evidence: TesterEvidence) -> TesterEvidence:
        """
        Record a collected piece of evidence.
        Enforces that evidence cannot be collected while in REPORTING or terminal states.
        """
        current = self.status if isinstance(self.status, TesterExecutionStatus) else TesterLifecycle.to_status(self.status)
        if current in {
            TesterExecutionStatus.REPORTING,
            TesterExecutionStatus.COMPLETED,
            TesterExecutionStatus.FAILED,
            TesterExecutionStatus.CANCELLED,
        }:
            raise TesterValidationError(
                f"Cannot collect or record evidence while execution is in '{current.value}' state. "
                "Evidence can only be collected during RUNNING or EVALUATING phases."
            )
        self.evidence.append(evidence)
        return evidence

    def record_defect(
        self,
        title: str,
        description: str,
        severity: DefectSeverity = DefectSeverity.MEDIUM,
        defect_type: DefectType = DefectType.FUNCTIONAL,
        reproduction_steps: Optional[Sequence[str]] = None,
        expected_behavior: str = "",
        actual_behavior: str = "",
        evidence_ids: Optional[Sequence[str]] = None,
        affected_components: Optional[Sequence[str]] = None,
        is_regression: bool = False,
        defect_id: Optional[str] = None,
        metadata: Optional[dict[str, Any]] = None,
    ) -> TesterDefect:
        """Record an identified concrete defect bound to this execution."""
        defect = TesterDefect(
            defect_id=defect_id or new_defect_id(),
            work_order_id=self.work_order_id,
            execution_id=self.execution_id,
            title=title,
            description=description,
            severity=severity,
            defect_type=defect_type,
            reproduction_steps=list(reproduction_steps or []),
            expected_behavior=expected_behavior,
            actual_behavior=actual_behavior,
            evidence_ids=list(evidence_ids or []),
            affected_components=list(affected_components or []),
            is_regression=is_regression,
            metadata=dict(metadata or {}),
        )
        self.defects.append(defect)
        return defect

    def record_finding(
        self,
        category: FindingCategory,
        title: str,
        description: str,
        evidence_ids: Optional[Sequence[str]] = None,
        is_uncertain: bool = False,
        recommendation: Optional[str] = None,
        finding_id: Optional[str] = None,
        metadata: Optional[dict[str, Any]] = None,
    ) -> TesterFinding:
        """Record an evaluative finding (UX, performance, recommendation, or uncertainty)."""
        finding = TesterFinding(
            finding_id=finding_id or new_finding_id(),
            category=category,
            title=title,
            description=description,
            evidence_ids=list(evidence_ids or []),
            is_uncertain=is_uncertain,
            recommendation=recommendation,
            metadata=dict(metadata or {}),
        )
        self.findings.append(finding)
        return finding

    def record_acceptance_result(
        self,
        criterion_id: str,
        description: str,
        status: AcceptanceCriterionStatus,
        evidence_ids: Optional[Sequence[str]] = None,
        notes: str = "",
    ) -> AcceptanceCriterionResult:
        """Record an acceptance criterion verification outcome."""
        ac = AcceptanceCriterionResult(
            criterion_id=criterion_id,
            description=description,
            status=status,
            evidence_ids=list(evidence_ids or []),
            notes=notes,
        )
        self.acceptance_results.append(ac)
        return ac

    def validate_lineage(
        self,
        work_order: Optional[Any] = None,
        expected_project_id: Optional[str] = None,
        expected_task_id: Optional[str] = None,
    ) -> None:
        """
        Verify strict causal lineage against work order, project, and manager task.
        Raises TesterLineageError upon any discrepancy.
        """
        if work_order is not None:
            wo_id = getattr(work_order, "work_order_id", None)
            if wo_id and self.work_order_id != str(wo_id):
                raise TesterLineageError(
                    f"Lineage mismatch: execution work_order_id '{self.work_order_id}' does not match work order '{wo_id}'."
                )
            wo_proj = getattr(work_order, "project_id", None)
            if wo_proj and self.project_id != str(wo_proj):
                raise TesterLineageError(
                    f"Lineage mismatch: execution project_id '{self.project_id}' does not match work order project_id '{wo_proj}'."
                )
            wo_task = getattr(work_order, "manager_task_id", None) or getattr(work_order, "task_id", None)
            if wo_task and self.task_id != str(wo_task):
                raise TesterLineageError(
                    f"Lineage mismatch: execution task_id '{self.task_id}' does not match work order task_id '{wo_task}'."
                )
        if expected_project_id and self.project_id != expected_project_id:
            raise TesterLineageError(
                f"Project isolation violation: execution project_id '{self.project_id}' does not match expected '{expected_project_id}'."
            )
        if expected_task_id and self.task_id != expected_task_id:
            raise TesterLineageError(
                f"Lineage mismatch: execution task_id '{self.task_id}' does not match expected '{expected_task_id}'."
            )

    @classmethod
    def from_work_order(
        cls,
        work_order: Any,
        status: TesterExecutionStatus = TesterExecutionStatus.REQUESTED,
        worker_id: str = "worker.tester",
    ) -> TesterExecution:
        """
        Instantiate an authorized TesterExecution from a validated TesterWorkOrder.
        Enforces strict causal lineage and identity preservation.
        Rejects creation if WorkOrder is invalid or has missing/mismatched lineage.
        """
        wo_id = getattr(work_order, "work_order_id", None)
        if not wo_id:
            raise TesterLineageError("Cannot instantiate TesterExecution: work_order has no work_order_id.")
        task_id = getattr(work_order, "manager_task_id", None) or getattr(work_order, "task_id", None)
        if not task_id:
            raise TesterLineageError("Cannot instantiate TesterExecution: work_order has no manager_task_id/task_id.")
        project_id = getattr(work_order, "project_id", None)
        if not project_id:
            raise TesterLineageError("Cannot instantiate TesterExecution: work_order has no project_id.")
        correlation_id = getattr(work_order, "correlation_id", None)
        if not correlation_id:
            raise TesterLineageError("Cannot instantiate TesterExecution: work_order has no correlation_id.")

        # Ensure work order passes identity and causal lineage validation
        if hasattr(work_order, "validate_lineage"):
            work_order.validate_lineage()
        elif hasattr(work_order, "validate"):
            work_order.validate()

        return cls(
            execution_id=new_execution_id(),
            work_order_id=str(wo_id),
            task_id=str(task_id),
            project_id=str(project_id),
            correlation_id=str(correlation_id),
            worker_id=worker_id,
            status=status,
            created_at=utc_now(),
        )

    def create_result(
        self,
        status: Optional[TesterResultStatus] = None,
        summary_for_manager: str = "",
        recommendations: Optional[Sequence[Any]] = None,
        uncertainties: Optional[Sequence[str]] = None,
        evidence_ids: Optional[Sequence[str]] = None,
        artifacts: Optional[Sequence[str]] = None,
        metadata: Optional[dict[str, Any]] = None,
        ship_recommendation: Optional[ShipRecommendation] = None,
    ) -> TesterResult:
        """Construct the final authoritative TesterResult bound strictly to this execution."""
        if status is None:
            if self.status == TesterExecutionStatus.CANCELLED:
                status = TesterResultStatus.CANCELLED
            elif self.status == TesterExecutionStatus.FAILED:
                status = TesterResultStatus.FAILED
            elif self.status == TesterExecutionStatus.BLOCKED:
                status = TesterResultStatus.BLOCKED
            else:
                status = TesterResultStatus.COMPLETED

        all_ev_ids = set(evidence_ids or [])
        for t in self.traces:
            all_ev_ids.add(t.trace_id)
        for ev in self.evidence:
            all_ev_ids.add(ev.evidence_id)
        for tc in self.test_cases:
            all_ev_ids.update(tc.evidence_ids)
        for d in self.defects:
            all_ev_ids.update(d.evidence_ids)
        for f in self.findings:
            all_ev_ids.update(f.evidence_ids)
        for a in self.acceptance_results:
            all_ev_ids.update(a.evidence_ids)

        all_recs = list(recommendations or [])
        all_unc = list(uncertainties or [])
        for f in self.findings:
            if f.recommendation and f.recommendation not in all_recs:
                all_recs.append(f.recommendation)
            if f.is_uncertain and f.description not in all_unc:
                all_unc.append(f.description)

        blocker_descs = [b.description for b in self.active_blockers]

        res_id = (
            f"tres-{self.execution_id[len('texec-'):]}"
            if self.execution_id.startswith("texec-")
            else f"tres-{self.execution_id}"
        )

        return TesterResult(
            result_id=res_id,
            execution_id=self.execution_id,
            work_order_id=self.work_order_id,
            task_id=self.task_id,
            project_id=self.project_id,
            correlation_id=self.correlation_id,
            status=status,
            ship_recommendation=ship_recommendation or ShipRecommendation.NOT_VERIFIED,
            summary_for_manager=summary_for_manager,
            test_cases=list(self.test_cases),
            evidence=list(self.evidence),
            defects=list(self.defects),
            findings=list(self.findings),
            acceptance_results=list(self.acceptance_results),
            recommendations=all_recs,
            uncertainties=all_unc,
            blockers=blocker_descs,
            evidence_ids=sorted(list(all_ev_ids)),
            artifacts=list(artifacts or []),
            metadata=dict(metadata or {}),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "execution_id": self.execution_id,
            "work_order_id": self.work_order_id,
            "task_id": self.task_id,
            "project_id": self.project_id,
            "correlation_id": self.correlation_id,
            "worker_id": self.worker_id,
            "status": self.status.value if hasattr(self.status, "value") else str(self.status),
            "current_phase": self.current_phase.value if hasattr(self.current_phase, "value") else str(self.current_phase),
            "cancellation_reason": self.cancellation_reason,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "traces": [t.to_dict() for t in self.traces],
            "transition_history": list(self.transition_history),
            "test_cases": [tc.to_dict() for tc in self.test_cases],
            "evidence": [ev.to_dict() for ev in self.evidence],
            "defects": [d.to_dict() for d in self.defects],
            "findings": [f.to_dict() for f in self.findings],
            "acceptance_results": [a.to_dict() for a in self.acceptance_results],
            "blockers": [b.to_dict() for b in self.blockers],
            "metadata": dict(self.metadata),
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> TesterExecution:
        st_raw = data.get("status", TesterExecutionStatus.REQUESTED.value)
        status = TesterLifecycle.to_status(st_raw)

        phase_raw = data.get("current_phase", TesterExecutionPhase.PREPARING.value)
        try:
            current_phase = TesterExecutionPhase(phase_raw)
        except (ValueError, KeyError):
            current_phase = TesterExecutionPhase.PREPARING

        traces = [
            TesterTrace.from_dict(t) if isinstance(t, dict) else t
            for t in data.get("traces", [])
        ]
        test_cases = [
            TestCaseResult.from_dict(tc) if isinstance(tc, dict) else tc
            for tc in data.get("test_cases", [])
        ]
        evidence = [
            TesterEvidence.from_dict(ev) if isinstance(ev, dict) else ev
            for ev in data.get("evidence", [])
        ]
        defects = [
            TesterDefect.from_dict(d) if isinstance(d, dict) else d
            for d in data.get("defects", [])
        ]
        findings = [
            TesterFinding.from_dict(f) if isinstance(f, dict) else f
            for f in data.get("findings", [])
        ]
        acceptance_results = [
            AcceptanceCriterionResult.from_dict(a) if isinstance(a, dict) else a
            for a in data.get("acceptance_results", [])
        ]
        blockers = [
            TesterBlocker.from_dict(b) if isinstance(b, dict) else b
            for b in data.get("blockers", [])
        ]

        return cls(
            execution_id=data.get("execution_id", new_execution_id()),
            work_order_id=data.get("work_order_id", ""),
            task_id=data.get("task_id", ""),
            project_id=data.get("project_id", ""),
            correlation_id=data.get("correlation_id", ""),
            worker_id=str(data.get("worker_id", "worker.tester")),
            status=status,
            current_phase=current_phase,
            cancellation_reason=data.get("cancellation_reason"),
            started_at=data.get("started_at"),
            completed_at=data.get("completed_at"),
            traces=traces,
            transition_history=list(data.get("transition_history", [])),
            test_cases=test_cases,
            evidence=evidence,
            defects=defects,
            findings=findings,
            acceptance_results=acceptance_results,
            blockers=blockers,
            metadata=dict(data.get("metadata", {})),
            created_at=data.get("created_at", utc_now()),
        )

