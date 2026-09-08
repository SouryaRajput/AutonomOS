from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import logging
import os
from typing import Any, Callable, Optional, Sequence, Union

from core.programmer.contracts.acceptance_criteria import AcceptanceCriterion
from core.programmer.contracts.acceptance_evaluator import (
    AcceptanceCriteriaEvaluator,
    AcceptanceEvaluationResult,
)
from core.programmer.contracts.blocker import ProgrammerBlocker
from core.programmer.contracts.coding_agent import (
    CodingAgentBackend,
    CodingAgentCancellationRequest,
    MockCodingAgentBackend,
)
from core.programmer.contracts.diff_verifier import (
    DiffScopeVerifier,
    DiffVerification,
    WorkspaceDiffInspector,
)
from core.programmer.contracts.execution import ProgrammerExecution
from core.programmer.contracts.execution_context import ProgrammerExecutionContext
from core.programmer.contracts.executor import (
    ControlledExecutionOutcome,
    ControlledProgrammerExecutor,
)
from core.programmer.contracts.identifiers import (
    new_iteration_id,
    new_result_id,
    validate_execution_id,
    validate_iteration_id,
    validate_work_order_id,
)
from core.programmer.contracts.result import ProgrammerResult
from core.programmer.contracts.verification import (
    AcceptanceResult,
    VerificationCheck,
    VerificationEvidence,
    VerificationSummary,
)
from core.programmer.contracts.evidence_aggregator import VerificationEvidenceAggregator
from core.programmer.contracts.verification_runner import (
    VerificationRunner,
    VerificationRunnerResult,
)
from core.programmer.contracts.command_record import CommandExecutionRecord
from core.programmer.contracts.acceptance_result import AcceptanceCriterionResult
from core.programmer.contracts.evidence import ProgrammerEvidence
from core.programmer.contracts.work_order import ProgrammerWorkOrder
from core.programmer.errors import (
    ProgrammerError,
    ProgrammerLineageError,
    ProgrammerValidationError,
)
from core.programmer.types import (
    AcceptanceStatus,
    ExecutionContextStatus,
    ProgrammerBlockerCategory,
    ProgrammerBlockerSeverity,
    ProgrammerEvidenceType,
    ProgrammerExecutionEventType,
    ProgrammerExecutionStatus,
    ProgrammerResultStatus,
    VerificationEvidenceSourceType,
    VerificationStatus,
    VerificationSummaryStatus,
    WorkspaceIsolationMode,
)

logger = logging.getLogger("AutonomOS.Programmer.CorrectionLoop")


def utc_now() -> str:
    """Generate ISO 8601 UTC timestamp."""
    return datetime.now(timezone.utc).isoformat()


# ==============================================================================
# 1. Correction Iteration Record
# ==============================================================================


@dataclass
class CorrectionIterationRecord:
    """
    Immutable structured record of an individual implementation or correction attempt
    within the bounded self-correction loop.
    
    Guarantees:
    - Unique iteration_id prefixed with 'piter-'.
    - Lineage tracing back to execution_id and work_order_id.
    - Captures attempt number (1, 2, ..., iteration_budget).
    - Stores the authoritative VerificationSummary and the factual feedback provided to agent.
    """
    iteration_id: str
    execution_id: str
    work_order_id: str
    attempt_number: int
    started_at: str
    completed_at: str
    verification_summary: VerificationSummary
    correction_reason: Optional[str] = None
    feedback_prompt: Optional[str] = None
    trace: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=utc_now)

    def __post_init__(self) -> None:
        validate_iteration_id(self.iteration_id)
        validate_execution_id(self.execution_id)
        validate_work_order_id(self.work_order_id)
        if self.attempt_number < 1:
            raise ProgrammerValidationError("attempt_number must be >= 1.")

    def to_dict(self) -> dict[str, Any]:
        return {
            "iteration_id": self.iteration_id,
            "execution_id": self.execution_id,
            "work_order_id": self.work_order_id,
            "attempt_number": self.attempt_number,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "verification_summary": self.verification_summary.to_dict(),
            "correction_reason": self.correction_reason,
            "feedback_prompt": self.feedback_prompt,
            "trace": dict(self.trace),
            "metadata": dict(self.metadata),
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CorrectionIterationRecord:
        summary_raw = data.get("verification_summary", {})
        summary = (
            VerificationSummary.from_dict(summary_raw)
            if isinstance(summary_raw, dict)
            else summary_raw
        )
        return cls(
            iteration_id=str(data.get("iteration_id", "")),
            execution_id=str(data.get("execution_id", "")),
            work_order_id=str(data.get("work_order_id", "")),
            attempt_number=int(data.get("attempt_number", 1)),
            started_at=str(data.get("started_at", utc_now())),
            completed_at=str(data.get("completed_at", utc_now())),
            verification_summary=summary,
            correction_reason=data.get("correction_reason"),
            feedback_prompt=data.get("feedback_prompt"),
            trace=dict(data.get("trace", {})),
            metadata=dict(data.get("metadata", {})),
            created_at=str(data.get("created_at", utc_now())),
        )


# ==============================================================================
# 2. Correction Loop Result
# ==============================================================================


@dataclass
class CorrectionLoopResult:
    """
    Comprehensive outcome contract of a bounded self-correction execution session.
    
    Guarantees:
    - Clear distinction between success (VERIFIED), budget exhaustion, scope blockage,
      and fatal backend crash.
    - Full causal iteration trace preserving all attempt records.
    - Final authoritative VerificationSummary.
    - Final ProgrammerResult for Manager consumption.
    """
    execution_id: str
    work_order_id: str
    final_status: VerificationSummaryStatus
    iterations: list[CorrectionIterationRecord] = field(default_factory=list)
    total_iterations: int = 0
    final_summary: Optional[VerificationSummary] = None
    result: Optional[ProgrammerResult] = None
    blockers: list[ProgrammerBlocker] = field(default_factory=list)
    error_message: Optional[str] = None
    metadata: dict[str, Any] = field(default_factory=dict)
    budget: int = 1

    def __post_init__(self) -> None:
        if isinstance(self.final_status, str):
            try:
                self.final_status = VerificationSummaryStatus(self.final_status.upper())
            except ValueError:
                self.final_status = VerificationSummaryStatus.UNVERIFIED
        if not self.total_iterations and self.iterations:
            self.total_iterations = len(self.iterations)

    @property
    def is_success(self) -> bool:
        """True if the loop achieved verified completion."""
        return self.final_status == VerificationSummaryStatus.VERIFIED

    @property
    def is_exhausted(self) -> bool:
        """True if the iteration budget was exhausted without reaching VERIFIED."""
        return self.total_iterations >= self.budget and not self.is_success and not self.is_blocked and not self.is_cancelled

    @property
    def is_blocked(self) -> bool:
        """True if execution was halted due to an out-of-scope or unverified blocker."""
        return bool(
            self.blockers
            or (self.result is not None and self.result.status == ProgrammerResultStatus.BLOCKED)
        )

    @property
    def is_cancelled(self) -> bool:
        """True if execution was cancelled."""
        return bool(
            self.result is not None and self.result.status == ProgrammerResultStatus.CANCELLED
        )

    @property
    def is_failed(self) -> bool:
        """True if execution failed due to agent fatal error or budget exhaustion."""
        return bool(
            self.result is not None and self.result.status == ProgrammerResultStatus.FAILED
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "execution_id": self.execution_id,
            "work_order_id": self.work_order_id,
            "final_status": self.final_status.value if isinstance(self.final_status, VerificationSummaryStatus) else str(self.final_status),
            "iterations": [i.to_dict() for i in self.iterations],
            "total_iterations": self.total_iterations,
            "final_summary": self.final_summary.to_dict() if self.final_summary else None,
            "result": self.result.to_dict() if self.result else None,
            "blockers": [b.to_dict() if hasattr(b, "to_dict") else str(b) for b in self.blockers],
            "error_message": self.error_message,
            "budget": self.budget,
            "is_success": self.is_success,
            "is_exhausted": self.is_exhausted,
            "is_blocked": self.is_blocked,
            "is_cancelled": self.is_cancelled,
            "is_failed": self.is_failed,
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CorrectionLoopResult:
        st_raw = data.get("final_status", VerificationSummaryStatus.UNVERIFIED.value)
        try:
            final_status = VerificationSummaryStatus(st_raw)
        except (ValueError, TypeError):
            final_status = VerificationSummaryStatus.UNVERIFIED

        final_summary_raw = data.get("final_summary")
        final_summary = (
            VerificationSummary.from_dict(final_summary_raw)
            if isinstance(final_summary_raw, dict)
            else None
        )

        res_raw = data.get("result")
        result = ProgrammerResult.from_dict(res_raw) if isinstance(res_raw, dict) else None

        iterations = [CorrectionIterationRecord.from_dict(i) for i in data.get("iterations", [])]
        raw_blockers = data.get("blockers", [])
        blockers: list[ProgrammerBlocker] = []
        for b in raw_blockers:
            if isinstance(b, dict):
                try:
                    blockers.append(ProgrammerBlocker.from_dict(b))
                except Exception:
                    pass
            elif isinstance(b, ProgrammerBlocker):
                blockers.append(b)

        return cls(
            execution_id=str(data.get("execution_id", "")),
            work_order_id=str(data.get("work_order_id", "")),
            final_status=final_status,
            iterations=iterations,
            total_iterations=int(data.get("total_iterations", len(iterations))),
            final_summary=final_summary,
            result=result,
            blockers=blockers,
            error_message=data.get("error_message"),
            budget=int(data.get("budget", 1)),
            metadata=dict(data.get("metadata", {})),
        )


# ==============================================================================
# 3. Correction Prompt Builder
# ==============================================================================


class CorrectionPromptBuilder:
    """
    Deterministic prompt builder formulating structured factual feedback for Cline.
    
    Principles:
    - Never fabricates explanations or reasons.
    - Never merely says "Fix it".
    - Presents observed facts (exit codes, error snippets, failed criteria, diff anomalies).
    - Explicitly reaffirms immutable boundary constraints.
    """

    @classmethod
    def build_correction_prompt(
        cls,
        work_order: ProgrammerWorkOrder,
        summary: VerificationSummary,
        attempt: int,
        max_attempts: int,
    ) -> str:
        """
        Synthesize observed verification failures into a structured, factual correction prompt.
        """
        lines: list[str] = []
        lines.append(f"# CORRECTION ATTEMPT {attempt + 1} OF {max_attempts}")
        lines.append("")
        lines.append(
            "AutonomOS observed verification failures during the preceding execution turn. "
            "Inspect the objective facts below, correct the implementation defects strictly "
            "within your authorized boundaries, and ensure all criteria pass."
        )
        lines.append("")

        # Section 1: Observed Verification Facts
        lines.append("## 1. OBSERVED VERIFICATION FACTS")
        failed_checks = [c for c in summary.verification_checks if c.status != VerificationStatus.PASS]
        if failed_checks:
            lines.append("### Failed Verification Checks:")
            for chk in failed_checks:
                lines.append(f"- **Check `{chk.check_id}`** ({chk.check_type.value}):")
                lines.append(f"  - Command: `{chk.command}`")
                lines.append(f"  - Status: `{chk.status.value}`")
                if chk.exit_code is not None:
                    lines.append(f"  - Exit Code: `{chk.exit_code}`")
                if chk.output_snippet:
                    lines.append(f"  - Observed Output:\n    ```\n    {chk.output_snippet.strip()}\n    ```")
        else:
            lines.append("### Verification Checks: None explicitly failed.")

        failed_criteria = [a for a in summary.acceptance_results if a.status != VerificationStatus.PASS]
        if failed_criteria:
            lines.append("")
            lines.append("### Failed Acceptance Criteria:")
            for crit in failed_criteria:
                lines.append(f"- **Criterion `{crit.criterion_id}`**:")
                lines.append(f"  - Status: `{crit.status.value}`")
                lines.append(f"  - Explanation: {crit.explanation}")
                if crit.evidence:
                    lines.append(f"  - Evidence: {crit.evidence}")

        # Diff Anomalies
        if summary.diff_verification and summary.diff_verification.unauthorized_changes:
            lines.append("")
            lines.append("### Unauthorized Scope Anomalies:")
            for u in summary.diff_verification.unauthorized_changes:
                lines.append(f"- [UNAUTHORIZED CHANGE] `{u.path}`: {u.reason}")

        lines.append("")

        # Section 2: Expected Behavior
        lines.append("## 2. EXPECTED BEHAVIOR")
        lines.append(f"- **Goal**: {work_order.objective}")
        if work_order.instructions:
            lines.append(f"- **Instructions**: {work_order.instructions}")
        lines.append("")

        # Section 3: Acceptance Criteria Catalog
        lines.append("## 3. ACCEPTANCE CRITERIA CATALOG")
        for crit in work_order.acceptance_criteria:
            lines.append(f"- [`{crit.criterion_id}`] {crit.description}")
        lines.append("")

        # Section 4: Boundary Constraints
        lines.append("## 4. BOUNDARY CONSTRAINTS (STRICTLY ENFORCED)")
        lines.append(
            "CRITICAL INVARIANT: You are operating inside the existing execution context. "
            "Your authority CANNOT expand during correction."
        )
        lines.append(f"- Authorized Writable Paths: {sorted(list(work_order.writable_paths))}")
        lines.append(f"- Read-Only Paths: {sorted(list(work_order.read_only_paths))}")
        lines.append(f"- Permitted Commands: {list(work_order.allowed_commands or [])}")
        if getattr(work_order, "constraints", None):
            lines.append(f"- Constraints: {list(work_order.constraints or [])}")
        lines.append("- Command Policy: All commands not explicitly listed in Permitted Commands are STRICTLY FORBIDDEN.")
        lines.append(
            "Do NOT attempt to modify files outside writable paths or execute unauthorized commands. "
            "Any out-of-scope modification will immediately halt execution."
        )
        lines.append("")
        lines.append(f"Proceed with correction attempt {attempt + 1}. Focus strictly on fixing the verified failures above.")

        return "\n".join(lines)


def _format_test_results_for_result(checks: list[VerificationCheck]) -> list[dict[str, Any]]:
    """Format VerificationCheck instances for ProgrammerResult.test_results."""
    formatted: list[dict[str, Any]] = []
    for c in checks:
        formatted.append({
            "command": c.command or "",
            "passed": c.status == VerificationStatus.PASS,
            "exit_code": c.exit_code if c.exit_code is not None else 0,
            "duration_ms": c.duration_ms if c.duration_ms is not None else 0.0,
            "failure_summary": c.output_snippet if c.status != VerificationStatus.PASS else None,
            "output_ref": c.output_reference,
            "metadata": dict(c.metadata),
        })
    return formatted


def _verification_evidence_to_programmer_evidence(
    ve: VerificationEvidence,
    execution_id: str,
    work_order_id: str,
) -> ProgrammerEvidence:
    """Convert a VerificationEvidence instance to a ProgrammerEvidence contract."""
    source_type_val = (
        ve.source_type.value if hasattr(ve.source_type, "value") else str(ve.source_type)
    )
    if "TEST" in source_type_val.upper():
        ev_type = ProgrammerEvidenceType.TEST_RESULT
    elif "COMMAND" in source_type_val.upper():
        ev_type = ProgrammerEvidenceType.COMMAND_EXECUTION
    elif "DIFF" in source_type_val.upper():
        ev_type = ProgrammerEvidenceType.DIFF
    elif "FILE" in source_type_val.upper():
        ev_type = ProgrammerEvidenceType.FILE_CHANGE
    else:
        ev_type = ProgrammerEvidenceType.CUSTOM

    return ProgrammerEvidence(
        evidence_id=ve.evidence_id,
        evidence_type=ev_type,
        source="AutonomOS.Verification",
        execution_id=ve.execution_id or execution_id,
        work_order_id=ve.work_order_id or work_order_id,
        summary=ve.description or f"Verification evidence ({source_type_val})",
        timestamp=ve.observed_at or utc_now(),
        artifact_ref=ve.source_reference,
        provenance={"data": ve.data, "is_agent_claim": ve.is_agent_claim},
        checksum=ve.checksum,
        metadata={"is_agent_claim": ve.is_agent_claim, "source_type": source_type_val},
    )


def _collect_all_commands(
    outcomes: list[ControlledExecutionOutcome],
    verification_checks: list[VerificationCheck],
) -> list[CommandExecutionRecord]:
    """Aggregate all commands observed from coding agent turns and verification check runs."""
    records: list[CommandExecutionRecord] = []
    seen: set[tuple[str, Any]] = set()

    for outcome in outcomes:
        if outcome and outcome.trace_collector:
            for ev in outcome.trace_collector.get_command_executions():
                cmd = ev.payload.get("command", "")
                st = "SUCCESS" if ev.payload.get("exit_code") == 0 else "FAILED"
                key = (cmd, ev.payload.get("started_at"))
                if key not in seen:
                    seen.add(key)
                    records.append(
                        CommandExecutionRecord(
                            command=cmd,
                            status=st,
                            exit_code=ev.payload.get("exit_code", 0),
                            duration_ms=float(ev.payload.get("duration_ms", 0.0) or 0.0),
                            output_snippet=ev.payload.get("output_snippet"),
                            output_ref=ev.payload.get("output_ref"),
                            timestamp=ev.timestamp or utc_now(),
                        )
                    )

    for vc in verification_checks:
        if vc.command:
            st = "SUCCESS" if vc.exit_code == 0 else "FAILED"
            key = (vc.command, vc.started_at)
            if key not in seen:
                seen.add(key)
                records.append(
                    CommandExecutionRecord(
                        command=vc.command,
                        status=st,
                        exit_code=vc.exit_code if vc.exit_code is not None else 0,
                        duration_ms=float(vc.duration_ms or 0.0),
                        output_snippet=vc.output_snippet,
                        output_ref=vc.output_reference,
                        timestamp=vc.started_at or utc_now(),
                    )
                )

    return records


def _acceptance_results_to_criterion_results(
    acceptance_results: list[AcceptanceResult],
    work_order: Optional[ProgrammerWorkOrder] = None,
) -> list[AcceptanceCriterionResult]:
    """Convert AcceptanceResult list to AcceptanceCriterionResult list with description lookup."""
    res: list[AcceptanceCriterionResult] = []
    desc_map: dict[str, str] = {}
    if work_order and getattr(work_order, "acceptance_criteria", None):
        for ac in work_order.acceptance_criteria:
            ac_id = ac.criterion_id if hasattr(ac, "criterion_id") else ac.get("criterion_id")
            desc = ac.description if hasattr(ac, "description") else ac.get("description", "")
            if ac_id:
                desc_map[ac_id] = desc

    for ar in acceptance_results:
        st_val = ar.status.value if hasattr(ar.status, "value") else str(ar.status)
        try:
            st = AcceptanceStatus(st_val.upper())
        except ValueError:
            st = AcceptanceStatus.NOT_VERIFIED

        desc = desc_map.get(ar.criterion_id, ar.explanation or f"Acceptance criterion {ar.criterion_id}")
        res.append(
            AcceptanceCriterionResult(
                criterion_id=ar.criterion_id,
                description=desc,
                status=st,
                evidence_ids=list(ar.evidence),
                message=ar.explanation,
                metadata=dict(ar.metadata),
            )
        )
    return res


def _build_final_programmer_result(
    work_order: ProgrammerWorkOrder,
    execution: ProgrammerExecution,
    status: ProgrammerResultStatus,
    summary_text: str,
    summary_for_manager: str,
    diff_verification: Optional[DiffVerification] = None,
    summary: Optional[VerificationSummary] = None,
    outcomes: Optional[list[ControlledExecutionOutcome]] = None,
    attempt: int = 1,
    budget: int = 1,
    blockers: Optional[list[str]] = None,
    escalations: Optional[list[str]] = None,
    metadata_extra: Optional[dict[str, Any]] = None,
) -> ProgrammerResult:
    """Build the final authoritative ProgrammerResult package for Manager consumption."""
    f_changed = list(diff_verification.files_changed) if diff_verification else []
    f_created = list(diff_verification.files_created) if diff_verification else []
    f_deleted = list(diff_verification.files_deleted) if diff_verification else []
    diff_sum = getattr(diff_verification, "diff_summary", "") if diff_verification else ""
    if not diff_sum and diff_verification and diff_verification.metadata:
        diff_sum = diff_verification.metadata.get("diff_summary", "")

    v_checks = summary.verification_checks if summary else []
    commands = _collect_all_commands(outcomes or [], v_checks)

    test_results = _format_test_results_for_result(v_checks)
    val_results = [
        {
            "check_id": vc.check_id,
            "check_type": vc.check_type.value if hasattr(vc.check_type, "value") else str(vc.check_type),
            "command": vc.command,
            "status": vc.status.value if hasattr(vc.status, "value") else str(vc.status),
            "exit_code": vc.exit_code,
            "duration_ms": vc.duration_ms,
        }
        for vc in v_checks
    ]

    raw_ac = summary.acceptance_results if summary else []
    ac_results = _acceptance_results_to_criterion_results(raw_ac, work_order)

    raw_ev = summary.evidence if summary else []
    evidence = [
        _verification_evidence_to_programmer_evidence(e, execution.execution_id, work_order.work_order_id)
        for e in raw_ev
    ]
    evidence_ids = [e.evidence_id for e in evidence]

    risks = list(summary.risks) if summary else []
    blks = list(blockers or [])
    escs = list(escalations or blks)

    meta = {
        "verification_status": summary.overall_status.value if summary else "UNVERIFIED",
        "total_attempts": attempt,
        "budget": budget,
        "checks_passed": len([c for c in v_checks if c.status == VerificationStatus.PASS]),
        "criteria_passed": len([a for a in ac_results if a.status == AcceptanceStatus.PASS]),
    }
    if metadata_extra:
        meta.update(metadata_extra)

    task_id = getattr(work_order, "task_id", "") or getattr(work_order, "manager_task_id", "")
    trace = {
        "execution_id": execution.execution_id,
        "work_order_id": work_order.work_order_id,
        "task_id": task_id,
        "project_id": work_order.project_id,
        "total_attempts": attempt,
        "budget": budget,
        "timestamps": {
            "created_at": execution.created_at or utc_now(),
            "completed_at": execution.completed_at or utc_now(),
        },
    }

    return ProgrammerResult(
        result_id=new_result_id(),
        execution_id=execution.execution_id,
        work_order_id=work_order.work_order_id,
        task_id=task_id,
        project_id=work_order.project_id,
        correlation_id=work_order.correlation_id,
        status=status,
        summary=summary_text,
        summary_for_manager=summary_for_manager,
        files_changed=f_changed,
        files_created=f_created,
        files_deleted=f_deleted,
        diff_summary=diff_sum,
        commands_executed=commands,
        test_results=test_results,
        validation_results=val_results,
        acceptance_results=ac_results,
        blockers=blks,
        material_blockers=blks,
        escalations=escs,
        risks=risks,
        artifacts=f_created,
        evidence=evidence,
        evidence_ids=evidence_ids,
        trace=trace,
        metadata=meta,
    )


# ==============================================================================
# 4. Bounded Correction Loop
# ==============================================================================


class BoundedCorrectionLoop:
    """
    Deterministic implementation → verification → correction loop owned and bounded
    by AutonomOS.
    
    Architectural Invariants:
    1. AutonomOS owns the loop: Cline NEVER decides when the loop ends.
    2. Strictly bounded by work_order.iteration_budget: never allows infinite retries.
    3. Authority cannot expand: all correction attempts execute within the existing
       ProgrammerExecutionContext with strictly immutable boundary policies.
    4. Out-of-scope escalation: if a correction requires out-of-scope files or commands,
       or produces unauthorized changes, the loop halts immediately in BLOCKED status.
    5. Factual feedback: Cline receives structured observed facts without fabrication.
    6. Non-retryable states: UNVERIFIED or fatal crashes halt without blind retries.
    7. Full lineage and attempt traceability: every iteration generates a CorrectionIterationRecord.
    """

    def __init__(
        self,
        executor: Optional[ControlledProgrammerExecutor] = None,
        verification_runner_factory: Optional[Callable[[ProgrammerExecutionContext], VerificationRunner]] = None,
        diff_verifier: Optional[DiffScopeVerifier] = None,
        acceptance_evaluator: Optional[AcceptanceCriteriaEvaluator] = None,
        evidence_aggregator: Optional[VerificationEvidenceAggregator] = None,
        prompt_builder: Optional[CorrectionPromptBuilder] = None,
    ) -> None:
        self.executor = executor or ControlledProgrammerExecutor()
        self.verification_runner_factory = verification_runner_factory
        self.diff_verifier = diff_verifier or DiffScopeVerifier()
        self.acceptance_evaluator = acceptance_evaluator or AcceptanceCriteriaEvaluator()
        self.evidence_aggregator = evidence_aggregator or VerificationEvidenceAggregator()
        self.prompt_builder = prompt_builder or CorrectionPromptBuilder()
        self._cancelled = False

    def cancel(self) -> None:
        """Signal cancellation of the correction loop."""
        self._cancelled = True

    def _resolve_verification_runner(self, context: ProgrammerExecutionContext) -> VerificationRunner:
        if self.verification_runner_factory:
            return self.verification_runner_factory(context)
        return VerificationRunner(context=context)

    def _ensure_verifying_state(self, execution: ProgrammerExecution, attempt: int) -> None:
        """Safely transition execution along canonical lifecycle into VERIFYING."""
        if execution.is_terminal or execution.status == ProgrammerExecutionStatus.VERIFYING:
            return
        if execution.status == ProgrammerExecutionStatus.REQUESTED:
            execution.transition_to(ProgrammerExecutionStatus.STARTING, reason="Starting execution")
        if execution.status == ProgrammerExecutionStatus.STARTING:
            execution.transition_to(ProgrammerExecutionStatus.RUNNING, reason="Running implementation")
        if execution.status in (ProgrammerExecutionStatus.RUNNING, ProgrammerExecutionStatus.COMPLETING):
            execution.transition_to(
                ProgrammerExecutionStatus.VERIFYING,
                reason=f"Running AutonomOS verification for attempt {attempt}",
            )

    def run(
        self,
        work_order: ProgrammerWorkOrder,
        execution: Optional[ProgrammerExecution] = None,
        context: Optional[ProgrammerExecutionContext] = None,
        initial_outcome: Optional[ControlledExecutionOutcome] = None,
        backend: Optional[CodingAgentBackend] = None,
        on_event: Optional[Callable[[Any], None]] = None,
        root_path_override: Optional[str] = None,
        isolation_mode: Optional[Union[WorkspaceIsolationMode, str]] = None,
        baseline_snapshot: Optional[dict[str, str]] = None,
    ) -> CorrectionLoopResult:
        """
        Execute the bounded implementation → verification → correction loop.
        
        Args:
            work_order: Validated ProgrammerWorkOrder authorizing the task and iteration budget.
            execution: Optional existing ProgrammerExecution session.
            context: Optional pre-provisioned ProgrammerExecutionContext.
            initial_outcome: Optional ControlledExecutionOutcome from an initial implementation turn.
            backend: Optional CodingAgentBackend override.
            on_event: Optional streaming event callback.
            root_path_override: Optional workspace root directory override.
            isolation_mode: Optional workspace isolation mode.
            baseline_snapshot: Optional baseline file hash snapshot for diff verification.
            
        Returns:
            CorrectionLoopResult encapsulating total iterations, final status, evidence, and result.
        """
        # Step 1: Validate WorkOrder
        if work_order is None:
            raise ProgrammerValidationError("ProgrammerWorkOrder cannot be None.")
        work_order.validate()

        budget = max(1, int(work_order.iteration_budget if work_order.iteration_budget is not None else 1))

        # Step 2: Establish or Verify Execution Session
        if execution is None:
            if initial_outcome is not None:
                execution = initial_outcome.execution
            else:
                from core.programmer.contracts.identifiers import new_execution_id
                execution = ProgrammerExecution(
                    execution_id=new_execution_id(),
                    work_order_id=work_order.work_order_id,
                    task_id=work_order.task_id,
                    project_id=work_order.project_id,
                    correlation_id=work_order.correlation_id,
                    status=ProgrammerExecutionStatus.REQUESTED,
                )
        else:
            if execution.work_order_id != work_order.work_order_id:
                raise ProgrammerLineageError(
                    f"Lineage mismatch: execution work_order_id '{execution.work_order_id}' "
                    f"does not match work order '{work_order.work_order_id}'."
                )

        current_context = context or (initial_outcome.context if initial_outcome else None)
        current_outcome = initial_outcome
        all_outcomes: list[ControlledExecutionOutcome] = [initial_outcome] if initial_outcome else []
        iterations: list[CorrectionIterationRecord] = []
        resolved_baseline = baseline_snapshot

        # Early cancellation check before starting execution
        if self._cancelled or (execution and execution.status == ProgrammerExecutionStatus.CANCELLED):
            if not execution.is_terminal:
                execution.cancel(requested_by="manager", reason="Execution cancelled prior to starting.")
            cancel_result = _build_final_programmer_result(
                work_order=work_order,
                execution=execution,
                status=ProgrammerResultStatus.CANCELLED,
                summary_text="Execution was cancelled.",
                summary_for_manager="Execution cancelled before starting.",
                diff_verification=None,
                summary=None,
                outcomes=all_outcomes,
                attempt=0,
                budget=budget,
            )
            return CorrectionLoopResult(
                execution_id=execution.execution_id,
                work_order_id=work_order.work_order_id,
                final_status=VerificationSummaryStatus.FAILED,
                iterations=[],
                total_iterations=0,
                result=cancel_result,
                budget=budget,
                error_message="Execution cancelled.",
            )

        # Step 3: Run Attempt 1 Implementation if not already supplied
        if current_outcome is None:
            iter_start = utc_now()
            current_outcome = self.executor.execute(
                work_order=work_order,
                execution=execution,
                backend=backend,
                on_event=on_event,
                root_path_override=root_path_override,
                isolation_mode=isolation_mode,
                context=current_context,
            )
            current_context = current_outcome.context
        if current_outcome is not None and current_outcome not in all_outcomes:
            all_outcomes.append(current_outcome)

        # Early termination checks on attempt 1 outcome
        if current_outcome.is_cancelled or self._cancelled:
            if not execution.is_terminal:
                execution.cancel(requested_by="manager", reason="Execution cancelled.")
            cancel_result = (
                current_outcome.result
                if (current_outcome.result and current_outcome.result.status == ProgrammerResultStatus.CANCELLED)
                else _build_final_programmer_result(
                    work_order=work_order,
                    execution=execution,
                    status=ProgrammerResultStatus.CANCELLED,
                    summary_text="Execution was cancelled.",
                    summary_for_manager="Execution cancelled before verification.",
                    diff_verification=None,
                    summary=None,
                    outcomes=all_outcomes,
                    attempt=0,
                    budget=budget,
                )
            )
            return CorrectionLoopResult(
                execution_id=execution.execution_id,
                work_order_id=work_order.work_order_id,
                final_status=VerificationSummaryStatus.FAILED,
                iterations=[],
                total_iterations=0,
                result=cancel_result,
                budget=budget,
                error_message="Execution cancelled.",
            )

        if current_outcome.is_failed:
            err = current_outcome.error_message or "Agent execution failed on initial attempt."
            return CorrectionLoopResult(
                execution_id=execution.execution_id,
                work_order_id=work_order.work_order_id,
                final_status=VerificationSummaryStatus.FAILED,
                iterations=[],
                total_iterations=1,
                result=current_outcome.result,
                budget=budget,
                error_message=err,
            )

        if current_context is None or current_context.workspace is None:
            err = "ExecutionContext or Workspace is unavailable after execution."
            if not execution.is_terminal:
                execution.transition_to(ProgrammerExecutionStatus.FAILED, reason=err)
            return CorrectionLoopResult(
                execution_id=execution.execution_id,
                work_order_id=work_order.work_order_id,
                final_status=VerificationSummaryStatus.FAILED,
                iterations=[],
                total_iterations=1,
                budget=budget,
                error_message=err,
            )

        # Resolve baseline snapshot for non-git workspaces if not supplied
        if resolved_baseline is None and current_context and current_context.workspace:
            root = getattr(current_context.workspace, "root_path", None)
            if root and not os.path.exists(os.path.join(root, ".git")):
                inspector = WorkspaceDiffInspector()
                resolved_baseline = inspector.capture_snapshot(current_context.workspace)

        # Step 4: Iterative Verification & Correction Loop
        for attempt in range(1, budget + 1):
            iter_start = utc_now()

            # Lifecycle: Safely transition to VERIFYING
            self._ensure_verifying_state(execution, attempt)

            # Check Cancellation
            if self._cancelled or (current_context.status == ExecutionContextStatus.FAILED and "cancel" in str(current_context.error_message).lower()):
                if not execution.is_terminal:
                    execution.cancel(requested_by="manager", reason="Execution cancelled.")
                return CorrectionLoopResult(
                    execution_id=execution.execution_id,
                    work_order_id=work_order.work_order_id,
                    final_status=VerificationSummaryStatus.FAILED,
                    iterations=iterations,
                    total_iterations=attempt,
                    budget=budget,
                    error_message="Execution cancelled during verification.",
                )

            # 4.1 Run Verification Checks
            runner = self._resolve_verification_runner(current_context)
            runner_result: VerificationRunnerResult = runner.run(checks=work_order.required_checks)

            # 4.2 Verify Diff & Scope Boundaries
            diff_verification: DiffVerification = self.diff_verifier.verify_workspace(
                workspace=current_context.workspace,
                baseline_snapshot=resolved_baseline,
                work_order=work_order,
                execution_id=execution.execution_id,
            )

            # 4.3 Combine Authoritative Evidence
            all_evidence: list[VerificationEvidence] = list(runner_result.evidence) + list(diff_verification.evidence)

            # 4.4 Evaluate Acceptance Criteria
            acceptance_eval_result: AcceptanceEvaluationResult = self.acceptance_evaluator.evaluate(
                work_order=work_order,
                execution_id=execution.execution_id,
                checks=runner_result.checks,
                evidence=all_evidence,
                files_changed=diff_verification.files_changed,
                files_created=diff_verification.files_created,
                files_deleted=diff_verification.files_deleted,
            )

            # 4.5 Aggregate into Authoritative VerificationSummary
            summary: VerificationSummary = self.evidence_aggregator.aggregate(
                work_order=work_order,
                execution_id=execution.execution_id,
                checks=runner_result.checks,
                acceptance_results=acceptance_eval_result.results,
                diff_verification=diff_verification,
                evidence=all_evidence,
                events=current_outcome.trace_collector.events if current_outcome.trace_collector else [],
                started_at=iter_start,
                completed_at=utc_now(),
            )

            # Record Attempt Iteration Record
            iter_record = CorrectionIterationRecord(
                iteration_id=new_iteration_id(),
                execution_id=execution.execution_id,
                work_order_id=work_order.work_order_id,
                attempt_number=attempt,
                started_at=iter_start,
                completed_at=utc_now(),
                verification_summary=summary,
                correction_reason=None if summary.is_verified else f"Verification status: {summary.overall_status.value}",
            )
            iterations.append(iter_record)

            # ==================================================================
            # 4.6 Evaluate Verification Verdict
            # ==================================================================

            # Case A: Complete Success (VERIFIED)
            if summary.is_verified:
                if not execution.is_terminal:
                    if execution.status == ProgrammerExecutionStatus.VERIFYING:
                        execution.transition_to(
                            ProgrammerExecutionStatus.COMPLETING,
                            reason=f"Verification succeeded on attempt {attempt}. Finalizing implementation.",
                        )
                    execution.transition_to(
                        ProgrammerExecutionStatus.COMPLETED,
                        reason=f"All verification checks and acceptance criteria passed on attempt {attempt}.",
                    )

                final_result = _build_final_programmer_result(
                    work_order=work_order,
                    execution=execution,
                    status=ProgrammerResultStatus.COMPLETED,
                    summary_text=f"Implementation verified successfully after {attempt} attempt(s).",
                    summary_for_manager=f"All verification checks and acceptance criteria passed ({attempt}/{budget} attempts).",
                    diff_verification=diff_verification,
                    summary=summary,
                    outcomes=all_outcomes,
                    attempt=attempt,
                    budget=budget,
                )
                return CorrectionLoopResult(
                    execution_id=execution.execution_id,
                    work_order_id=work_order.work_order_id,
                    final_status=VerificationSummaryStatus.VERIFIED,
                    iterations=iterations,
                    total_iterations=attempt,
                    final_summary=summary,
                    result=final_result,
                    budget=budget,
                )

            # Case B: Scope / Policy Escalation (BLOCKED)
            # Invariant: If changes outside writable scope occur, or out-of-scope permissions are needed,
            # halt and transition to BLOCKED immediately. Do not auto-expand scope.
            if diff_verification.has_unauthorized_changes:
                unauth_paths = [u.path for u in diff_verification.unauthorized_changes]
                blocker_desc = (
                    f"Unauthorized modifications detected outside writable paths: {unauth_paths}. "
                    "Programmer authority cannot expand autonomously."
                )
                blocker = execution.block(
                    reason=blocker_desc,
                    category=ProgrammerBlockerCategory.SCOPE,
                    severity=ProgrammerBlockerSeverity.HIGH,
                    required_decision="Manager decision required to expand scope or discard unauthorized changes.",
                )
                final_result = _build_final_programmer_result(
                    work_order=work_order,
                    execution=execution,
                    status=ProgrammerResultStatus.BLOCKED,
                    summary_text=blocker_desc,
                    summary_for_manager=blocker_desc,
                    diff_verification=diff_verification,
                    summary=summary,
                    outcomes=all_outcomes,
                    attempt=attempt,
                    budget=budget,
                    blockers=[blocker.description],
                    escalations=[blocker.description],
                    metadata_extra={"unauthorized_changes": unauth_paths},
                )
                return CorrectionLoopResult(
                    execution_id=execution.execution_id,
                    work_order_id=work_order.work_order_id,
                    final_status=summary.overall_status,
                    iterations=iterations,
                    total_iterations=attempt,
                    final_summary=summary,
                    result=final_result,
                    blockers=[blocker],
                    budget=budget,
                    error_message=blocker_desc,
                )

            # Case C: Inconclusive Verification (UNVERIFIED)
            # Invariant: Do not blindly retry when evidence cannot be safely obtained. Escalate to BLOCKED.
            if summary.is_unverified:
                blocker_desc = (
                    "Verification inconclusive (UNVERIFIED): missing authoritative verification checks "
                    "or executable evidence sources within current permissions."
                )
                blocker = execution.block(
                    reason=blocker_desc,
                    category=ProgrammerBlockerCategory.VERIFICATION,
                    severity=ProgrammerBlockerSeverity.HIGH,
                    required_decision="Manager clarification or authorized verification command specification required.",
                )
                final_result = _build_final_programmer_result(
                    work_order=work_order,
                    execution=execution,
                    status=ProgrammerResultStatus.BLOCKED,
                    summary_text=blocker_desc,
                    summary_for_manager=blocker_desc,
                    diff_verification=diff_verification,
                    summary=summary,
                    outcomes=all_outcomes,
                    attempt=attempt,
                    budget=budget,
                    blockers=[blocker.description],
                    escalations=[blocker.description],
                )
                return CorrectionLoopResult(
                    execution_id=execution.execution_id,
                    work_order_id=work_order.work_order_id,
                    final_status=VerificationSummaryStatus.UNVERIFIED,
                    iterations=iterations,
                    total_iterations=attempt,
                    final_summary=summary,
                    result=final_result,
                    blockers=[blocker],
                    budget=budget,
                    error_message=blocker_desc,
                )

            # Case D: Iteration Budget Exhausted
            if attempt >= budget:
                exhaust_msg = f"Verification failed and iteration budget exhausted ({attempt}/{budget} attempts)."
                if not execution.is_terminal:
                    if execution.status == ProgrammerExecutionStatus.VERIFYING:
                        execution.transition_to(
                            ProgrammerExecutionStatus.COMPLETING,
                            reason=f"Iteration budget exhausted on attempt {attempt}. Finalizing implementation.",
                        )
                    execution.transition_to(
                        ProgrammerExecutionStatus.FAILED,
                        reason=exhaust_msg,
                    )
                res_status = (
                    ProgrammerResultStatus.PARTIAL
                    if summary.is_partially_verified
                    else ProgrammerResultStatus.FAILED
                )
                final_result = _build_final_programmer_result(
                    work_order=work_order,
                    execution=execution,
                    status=res_status,
                    summary_text=exhaust_msg,
                    summary_for_manager=exhaust_msg,
                    diff_verification=diff_verification,
                    summary=summary,
                    outcomes=all_outcomes,
                    attempt=attempt,
                    budget=budget,
                    metadata_extra={"budget_exhausted": True},
                )
                return CorrectionLoopResult(
                    execution_id=execution.execution_id,
                    work_order_id=work_order.work_order_id,
                    final_status=summary.overall_status,
                    iterations=iterations,
                    total_iterations=attempt,
                    final_summary=summary,
                    result=final_result,
                    budget=budget,
                    error_message=exhaust_msg,
                )

            # Case E: Budget Remaining -> Execute Correction Turn (attempt + 1)
            correction_prompt = self.prompt_builder.build_correction_prompt(
                work_order=work_order,
                summary=summary,
                attempt=attempt,
                max_attempts=budget,
            )
            iter_record.feedback_prompt = correction_prompt

            current_outcome = self.executor.execute_correction(
                work_order=work_order,
                execution=execution,
                context=current_context,
                correction_prompt=correction_prompt,
                backend=backend,
                on_event=on_event,
                trace_collector=current_outcome.trace_collector,
                capability_binding=current_outcome.capability_binding,
            )
            all_outcomes.append(current_outcome)

            # If agent execution failed fatally during correction -> halt immediately
            if current_outcome.is_failed:
                fatal_msg = current_outcome.error_message or "Agent fatal error during correction."
                return CorrectionLoopResult(
                    execution_id=execution.execution_id,
                    work_order_id=work_order.work_order_id,
                    final_status=VerificationSummaryStatus.FAILED,
                    iterations=iterations,
                    total_iterations=attempt + 1,
                    final_summary=summary,
                    result=current_outcome.result,
                    budget=budget,
                    error_message=fatal_msg,
                )

            if current_outcome.is_cancelled:
                return CorrectionLoopResult(
                    execution_id=execution.execution_id,
                    work_order_id=work_order.work_order_id,
                    final_status=VerificationSummaryStatus.FAILED,
                    iterations=iterations,
                    total_iterations=attempt + 1,
                    final_summary=summary,
                    result=current_outcome.result,
                    budget=budget,
                    error_message="Execution cancelled during correction.",
                )

        # Fallback return (budget loop exit)
        return CorrectionLoopResult(
            execution_id=execution.execution_id,
            work_order_id=work_order.work_order_id,
            final_status=VerificationSummaryStatus.FAILED,
            iterations=iterations,
            total_iterations=len(iterations),
            budget=budget,
        )
