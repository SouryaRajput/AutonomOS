from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional

from core.programmer.contracts.blocker import ProgrammerBlocker
from core.programmer.contracts.cancellation import ProgrammerCancellation
from core.programmer.contracts.identifiers import (
    new_blocker_id,
    new_execution_id,
    validate_execution_id,
    validate_work_order_id,
)
from core.programmer.contracts.lifecycle import ProgrammerLifecycle
from core.programmer.contracts.result import ProgrammerResult
from core.programmer.contracts.trace import ProgrammerTrace, new_trace_id
from core.programmer.errors import (
    InvalidProgrammerTransitionError,
    ProgrammerLineageError,
)
from core.programmer.types import (
    ProgrammerActionType,
    ProgrammerBlockerCategory,
    ProgrammerBlockerSeverity,
    ProgrammerExecutionStatus,
    ProgrammerResultStatus,
)


def utc_now() -> str:
    """Generate ISO 8601 UTC timestamp."""
    return datetime.now(timezone.utc).isoformat()


@dataclass
class ProgrammerExecution:
    """
    Stateful execution session of a ProgrammerWorkOrder.
    Tracks execution attempt lifecycle, operational transitions, generated traces, and produces results.
    """
    execution_id: str
    work_order_id: str
    task_id: str
    project_id: str
    correlation_id: str
    worker_id: str = "worker.programmer"
    status: ProgrammerExecutionStatus = ProgrammerExecutionStatus.REQUESTED
    started_at: Optional[str] = None
    completed_at: Optional[str] = None
    traces: list[ProgrammerTrace] = field(default_factory=list)
    blockers: list[ProgrammerBlocker] = field(default_factory=list)
    cancellation: Optional[ProgrammerCancellation] = None
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=utc_now)

    # Reused deterministic transition table
    ALLOWED_TRANSITIONS = ProgrammerLifecycle.ALLOWED_TRANSITIONS

    def __post_init__(self) -> None:
        validate_execution_id(self.execution_id)
        validate_work_order_id(self.work_order_id)
        if not self.task_id:
            raise ProgrammerLineageError("ProgrammerExecution must have a valid non-empty task_id.")
        if not self.project_id:
            raise ProgrammerLineageError("ProgrammerExecution must have a valid non-empty project_id.")
        if not self.correlation_id:
            raise ProgrammerLineageError("ProgrammerExecution must have a valid non-empty correlation_id.")
        if not isinstance(self.status, ProgrammerExecutionStatus):
            self.status = ProgrammerLifecycle.to_status(self.status)

    @property
    def active_blockers(self) -> list[ProgrammerBlocker]:
        """Return list of currently unresolved blockers."""
        return [b for b in self.blockers if not b.is_resolved]

    @property
    def is_terminal(self) -> bool:
        """Check whether execution is in a terminal state (COMPLETED, FAILED, CANCELLED)."""
        return ProgrammerLifecycle.is_terminal(self.status)

    @property
    def is_active(self) -> bool:
        """Check whether execution is in an active non-terminal state."""
        return ProgrammerLifecycle.is_active(self.status)

    def transition_to(
        self,
        target_status: ProgrammerExecutionStatus,
        reason: str = "",
        blocker: Optional[ProgrammerBlocker] = None,
        cancellation: Optional[ProgrammerCancellation] = None,
    ) -> None:
        """
        Validate and apply a deterministic lifecycle state transition.
        Raises InvalidProgrammerTransitionError if the transition is illegal.
        """
        current = self.status if isinstance(self.status, ProgrammerExecutionStatus) else ProgrammerLifecycle.to_status(self.status)
        target = target_status if isinstance(target_status, ProgrammerExecutionStatus) else ProgrammerLifecycle.to_status(target_status)

        if current == target:
            return

        ProgrammerLifecycle.validate_transition(
            entity_id=self.execution_id,
            current_status=current,
            target_status=target,
            reason=reason,
        )

        self.status = target
        if target in {
            ProgrammerExecutionStatus.RUNNING,
            ProgrammerExecutionStatus.VERIFYING,
            ProgrammerExecutionStatus.COMPLETING,
        } and not self.started_at:
            self.started_at = utc_now()
        elif target in ProgrammerLifecycle.TERMINAL_STATUSES:
            self.completed_at = utc_now()

        if blocker is not None:
            self.blockers.append(blocker)
        elif target == ProgrammerExecutionStatus.BLOCKED and reason:
            # Create a lightweight blocker from the reason string if none supplied
            blk = ProgrammerBlocker(
                blocker_id=new_blocker_id(),
                work_order_id=self.work_order_id,
                category=ProgrammerBlockerCategory.OTHER,
                description=reason,
                execution_id=self.execution_id,
                task_id=self.task_id,
            )
            self.blockers.append(blk)

        if cancellation is not None:
            self.cancellation = cancellation

    def block(
        self,
        reason: str,
        category: ProgrammerBlockerCategory = ProgrammerBlockerCategory.OTHER,
        severity: ProgrammerBlockerSeverity = ProgrammerBlockerSeverity.HIGH,
        required_decision: str = "",
        required_context: Optional[str] = None,
        blocker_id: Optional[str] = None,
    ) -> ProgrammerBlocker:
        """
        Escalate an operational impediment, transition to BLOCKED, and record the blocker.
        BLOCKED indicates Programmer requires a decision, authorization, or missing context.
        """
        blk = ProgrammerBlocker(
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
            ProgrammerExecutionStatus.BLOCKED,
            reason=reason,
            blocker=blk,
        )
        return blk

    def unblock(
        self,
        resolution_notes: str,
        resolved_by: str = "manager",
        target_status: ProgrammerExecutionStatus = ProgrammerExecutionStatus.RUNNING,
    ) -> None:
        """
        Resolve all active blockers with provenance and transition to target status (default: RUNNING).
        Prevents completing from blocked without resolution.
        """
        for b in self.active_blockers:
            b.resolve(resolution_notes=resolution_notes, resolved_by=resolved_by)
        self.transition_to(target_status, reason=f"Unblocked: {resolution_notes}")

    def cancel(
        self,
        requested_by: str,
        reason: str,
        metadata: Optional[dict[str, Any]] = None,
        cancellation: Optional[ProgrammerCancellation] = None,
        confirmed: bool = True,
        agent_terminated: bool = True,
        cleanup_completed: bool = True,
    ) -> ProgrammerCancellation:
        """
        Cancel execution attempt with full cancellation provenance.
        Transitions status to CANCELLED.
        """
        if cancellation is None:
            cancellation = ProgrammerCancellation(
                requested_by=requested_by,
                reason=reason,
                confirmed=confirmed,
                agent_terminated=agent_terminated,
                cleanup_completed=cleanup_completed,
                metadata=dict(metadata or {}),
            )
        self.transition_to(
            ProgrammerExecutionStatus.CANCELLED,
            reason=reason,
            cancellation=cancellation,
        )
        return cancellation

    def create_trace(
        self,
        action_type: ProgrammerActionType,
        action_details: Optional[dict[str, Any]] = None,
    ) -> ProgrammerTrace:
        """Create and record an auditable execution trace adhering to strict causal lineage."""
        trace = ProgrammerTrace(
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

    def create_result(
        self,
        status: ProgrammerResultStatus = ProgrammerResultStatus.SUCCESS,
        summary_for_manager: str = "",
        evidence_ids: Optional[list[str]] = None,
        material_blockers: Optional[list[str]] = None,
        artifacts: Optional[list[str]] = None,
        metadata: Optional[dict[str, Any]] = None,
    ) -> ProgrammerResult:
        """Construct the final ProgrammerResult bound strictly to this execution and its lineage."""
        if material_blockers is not None:
            all_blockers = list(material_blockers)
        else:
            all_blockers = [b.description for b in self.active_blockers]

        return ProgrammerResult(
            result_id=f"pres-{self.execution_id[len('pexec-'):]}",
            execution_id=self.execution_id,
            work_order_id=self.work_order_id,
            task_id=self.task_id,
            project_id=self.project_id,
            correlation_id=self.correlation_id,
            status=status,
            summary_for_manager=summary_for_manager,
            evidence_ids=list(evidence_ids or []),
            material_blockers=all_blockers,
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
            "status": self.status.value if isinstance(self.status, ProgrammerExecutionStatus) else str(self.status),
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "traces": [t.to_dict() for t in self.traces],
            "blockers": [b.to_dict() for b in self.blockers],
            "cancellation": self.cancellation.to_dict() if self.cancellation else None,
            "metadata": dict(self.metadata),
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ProgrammerExecution:
        st_raw = data.get("status", ProgrammerExecutionStatus.REQUESTED.value)
        status = ProgrammerLifecycle.to_status(st_raw)

        traces = [
            ProgrammerTrace.from_dict(t) if isinstance(t, dict) else t
            for t in data.get("traces", [])
        ]
        blockers = [
            ProgrammerBlocker.from_dict(b) if isinstance(b, dict) else b
            for b in data.get("blockers", [])
        ]
        cancellation = None
        if data.get("cancellation"):
            cancellation = ProgrammerCancellation.from_dict(data["cancellation"])

        return cls(
            execution_id=data.get("execution_id", new_execution_id()),
            work_order_id=data.get("work_order_id", ""),
            task_id=data.get("task_id", ""),
            project_id=data.get("project_id", ""),
            correlation_id=data.get("correlation_id", ""),
            worker_id=str(data.get("worker_id", "worker.programmer")),
            status=status,
            started_at=data.get("started_at"),
            completed_at=data.get("completed_at"),
            traces=traces,
            blockers=blockers,
            cancellation=cancellation,
            metadata=dict(data.get("metadata", {})),
            created_at=data.get("created_at", utc_now()),
        )
