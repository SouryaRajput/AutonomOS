from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import logging
from typing import Any, Callable, Optional, Union

from core.programmer.contracts.cancellation import ProgrammerCancellation
from core.programmer.contracts.capability_binding import ClineCapabilityBinding
from core.programmer.contracts.coding_agent import (
    CodingAgentBackend,
    CodingAgentCancellationRequest,
    CodingAgentCancellationResult,
)
from core.programmer.contracts.execution import ProgrammerExecution
from core.programmer.contracts.failure import ProgrammerFailure
from core.programmer.contracts.verification_runner import VerificationRunner
from core.programmer.contracts.watchdog import ExecutionWatchdog
from core.programmer.errors import (
    InvalidProgrammerTransitionError,
    ProgrammerError,
    ProgrammerValidationError,
)
from core.programmer.types import (
    FailureSourceType,
    ProgrammerActionType,
    ProgrammerBlockerCategory,
    ProgrammerBlockerSeverity,
    ProgrammerExecutionStatus,
    ProgrammerFailureCategory,
    ProgrammerFailureSeverity,
    RecoveryDisposition,
)

logger = logging.getLogger("AutonomOS.Programmer.CancellationHandler")


def utc_now() -> str:
    """Generate ISO 8601 UTC timestamp."""
    return datetime.now(timezone.utc).isoformat()


class ProgrammerCancellationHandler:
    """
    Deterministic coordinator for cancellation and timeout handling across Programmer executions.
    
    Architectural Guarantees:
    - STRICT PROVENANCE: Preserves who requested, when, why, whether agent terminated,
      and whether cleanup completed.
    - NO SILENT CANCELLATION: If agent termination cannot be confirmed, records uncertainty/failure
      explicitly via blockers and metadata without falsely claiming clean cancellation.
    - ORDERED TEARDOWN: Follows deterministic pipeline:
      Programmer marked -> Cline cancellation -> active commands -> verification -> cleanup -> CANCELLED.
    - CANCELLATION IS NOT FAILURE: Normal cancellation transitions to CANCELLED without recording
      execution defect failure unless unconfirmed.
    - TIMEOUTS ARE FAILURES: Timeouts transition to FAILED with structured TIMEOUT failure candidate.
    - NO PROHIBITED ACTIONS: Never restarts Cline, expands scope, retries, or modifies acceptance.
    """

    def __init__(self) -> None:
        pass

    def cancel_execution(
        self,
        execution: ProgrammerExecution,
        requested_by: str,
        reason: str,
        backend: Optional[CodingAgentBackend] = None,
        capability_binding: Optional[ClineCapabilityBinding] = None,
        verification_runner: Optional[VerificationRunner] = None,
        watchdog: Optional[ExecutionWatchdog] = None,
        cleanup_callback: Optional[Callable[[], None]] = None,
    ) -> ProgrammerCancellation:
        """
        Execute deterministic cancellation flow for an active Programmer execution.
        
        Returns:
            ProgrammerCancellation record detailing provenance, termination, and confirmation.
        """
        if execution is None:
            raise ProgrammerValidationError("ProgrammerExecution cannot be None.")

        # ----------------------------------------------------------------------
        # Step 1: Terminal State & Race Checks
        # ----------------------------------------------------------------------
        if execution.status == ProgrammerExecutionStatus.CANCELLED:
            # Idempotent repeated cancellation
            logger.info(f"Execution '{execution.execution_id}' is already CANCELLED. Returning existing cancellation.")
            if execution.cancellation:
                return execution.cancellation
            # Reconstruct fallback cancellation record
            return ProgrammerCancellation(
                requested_by=requested_by,
                reason=reason,
                agent_terminated=True,
                cleanup_completed=True,
                confirmed=True,
            )

        if execution.status == ProgrammerExecutionStatus.COMPLETED:
            # Cancellation after completion / race with completion
            logger.warning(
                f"Cancellation requested for already COMPLETED execution '{execution.execution_id}'. "
                "Preserving completed state."
            )
            execution.metadata.setdefault("cancellation_attempts", []).append({
                "requested_by": requested_by,
                "reason": reason,
                "requested_at": utc_now(),
                "outcome": "REJECTED_ALREADY_COMPLETED",
            })
            return ProgrammerCancellation(
                requested_by=requested_by,
                reason=reason,
                agent_terminated=True,
                cleanup_completed=True,
                confirmed=False,
                confirmation_error="Execution has already completed successfully; cancellation rejected.",
            )

        # ----------------------------------------------------------------------
        # Step 2: Mark Cancellation Intent in Trace & Metadata
        # ----------------------------------------------------------------------
        execution.metadata["cancellation_requested"] = True
        execution.metadata["cancellation_request_time"] = utc_now()
        execution.create_trace(
            ProgrammerActionType.EXECUTE,
            {
                "cancellation_requested": True,
                "requested_by": requested_by,
                "reason": reason,
            },
        )

        agent_terminated = False
        confirmed = False
        confirmation_error: Optional[str] = None

        # ----------------------------------------------------------------------
        # Step 3: Signal Cline / Coding Agent Backend Cancellation
        # ----------------------------------------------------------------------
        if backend is not None:
            try:
                cancel_req = CodingAgentCancellationRequest(
                    execution_id=execution.execution_id,
                    work_order_id=execution.work_order_id,
                    reason=reason,
                )
                cancel_res: CodingAgentCancellationResult = backend.cancel(cancel_req)
                if cancel_res.cancelled:
                    agent_terminated = True
                    confirmed = True
                else:
                    agent_terminated = False
                    confirmed = False
                    confirmation_error = f"Backend declined cancellation: {cancel_res.reason or 'unknown reason'}"
                    logger.warning(
                        f"Backend could not confirm cancellation for '{execution.execution_id}': {confirmation_error}"
                    )
            except Exception as b_err:
                agent_terminated = False
                confirmed = False
                confirmation_error = f"Error during backend cancellation: {b_err}"
                logger.error(confirmation_error)
        else:
            # No backend active (e.g. cancelled while idle or preparing)
            agent_terminated = True
            confirmed = True

        # ----------------------------------------------------------------------
        # Step 4: Stop Active Commands & Verification Checks
        # ----------------------------------------------------------------------
        if capability_binding is not None:
            try:
                capability_binding.cancel_active_commands(reason=reason)
            except Exception as cmd_err:
                logger.warning(f"Error cancelling active commands for '{execution.execution_id}': {cmd_err}")

        if verification_runner is not None:
            try:
                verification_runner.cancel(reason=reason)
            except Exception as v_err:
                logger.warning(f"Error cancelling verification runner for '{execution.execution_id}': {v_err}")

        # ----------------------------------------------------------------------
        # Step 5: Execute Cleanup Callback & Stop Watchdog
        # ----------------------------------------------------------------------
        cleanup_completed = False
        try:
            if cleanup_callback is not None:
                cleanup_callback()
            if watchdog is not None:
                watchdog.stop_monitoring(execution.execution_id)
            cleanup_completed = True
        except Exception as cl_err:
            logger.error(f"Error during cleanup callback for '{execution.execution_id}': {cl_err}")
            cleanup_completed = False

        # ----------------------------------------------------------------------
        # Step 6: Assemble Provenance Record
        # ----------------------------------------------------------------------
        cancellation = ProgrammerCancellation(
            requested_by=requested_by,
            reason=reason,
            agent_terminated=agent_terminated,
            cleanup_completed=cleanup_completed,
            confirmed=confirmed,
            confirmation_error=confirmation_error,
            metadata={
                "execution_id": execution.execution_id,
                "work_order_id": execution.work_order_id,
            },
        )

        # ----------------------------------------------------------------------
        # Step 7: Transition Execution State
        # ----------------------------------------------------------------------
        if confirmed:
            # Confirmed clean cancellation: transition to CANCELLED
            if execution.status != ProgrammerExecutionStatus.CANCELLED:
                execution.cancel(
                    requested_by=requested_by,
                    reason=reason,
                    metadata=cancellation.to_dict(),
                    cancellation=cancellation,
                )
            else:
                execution.cancellation = cancellation
        else:
            # Failed cancellation: do NOT silently mark cancelled!
            # Record explicit blocker and metadata uncertainty
            execution.metadata["cancellation_unconfirmed"] = True
            execution.metadata["cancellation_error"] = confirmation_error
            execution.block(
                reason=f"Cancellation unconfirmed: {confirmation_error}",
                category=ProgrammerBlockerCategory.OTHER,
                severity=ProgrammerBlockerSeverity.CRITICAL,
                required_decision="Inspect and manually terminate rogue coding agent process.",
            )
            logger.error(
                f"Execution '{execution.execution_id}' cancellation could not be confirmed. "
                "Recorded explicit blocker instead of marking CANCELLED."
            )

        return cancellation

    def handle_timeout(
        self,
        execution: ProgrammerExecution,
        timeout_seconds: float,
        backend: Optional[CodingAgentBackend] = None,
        capability_binding: Optional[ClineCapabilityBinding] = None,
        verification_runner: Optional[VerificationRunner] = None,
        watchdog: Optional[ExecutionWatchdog] = None,
        cleanup_callback: Optional[Callable[[], None]] = None,
    ) -> ProgrammerFailure:
        """
        Execute deterministic timeout flow for an active Programmer execution.
        
        Follows timeout flow:
        TimeoutDetected -> Mark timed out -> Stop active work -> Cleanup -> FAILED
        
        Returns:
            Structured ProgrammerFailure with category TIMEOUT and disposition FAIL.
        """
        if execution is None:
            raise ProgrammerValidationError("ProgrammerExecution cannot be None.")

        reason = f"Execution exceeded configured timeout of {timeout_seconds}s."

        # Mark timeout in trace and metadata
        execution.metadata["timed_out"] = True
        execution.metadata["timeout_seconds"] = timeout_seconds
        execution.create_trace(
            ProgrammerActionType.EXECUTE,
            {
                "timed_out": True,
                "timeout_seconds": timeout_seconds,
            },
        )

        # 1. Stop backend if running
        if backend is not None:
            try:
                backend.cancel(
                    CodingAgentCancellationRequest(
                        execution_id=execution.execution_id,
                        work_order_id=execution.work_order_id,
                        reason=reason,
                    )
                )
            except Exception as b_err:
                logger.warning(f"Error signalling backend cancellation on timeout: {b_err}")

        # 2. Stop active commands & verification
        if capability_binding is not None:
            try:
                capability_binding.cancel_active_commands(reason=reason)
            except Exception as cmd_err:
                logger.warning(f"Error cancelling commands on timeout: {cmd_err}")

        if verification_runner is not None:
            try:
                verification_runner.cancel(reason=reason)
            except Exception as v_err:
                logger.warning(f"Error cancelling verification on timeout: {v_err}")

        # 3. Cleanup & Watchdog Teardown
        try:
            if cleanup_callback is not None:
                cleanup_callback()
            if watchdog is not None:
                watchdog.stop_monitoring(execution.execution_id)
        except Exception as cl_err:
            logger.error(f"Error during timeout cleanup: {cl_err}")

        # 4. Transition execution state to FAILED
        if not execution.is_terminal:
            try:
                execution.transition_to(
                    ProgrammerExecutionStatus.FAILED,
                    reason=reason,
                )
            except InvalidProgrammerTransitionError as trans_err:
                logger.warning(f"Could not transition execution to FAILED on timeout: {trans_err}")

        # 5. Produce structured failure record
        failure = ProgrammerFailure.create(
            execution_id=execution.execution_id,
            work_order_id=execution.work_order_id,
            category=ProgrammerFailureCategory.TIMEOUT,
            message=reason,
            severity=ProgrammerFailureSeverity.HIGH,
            source=FailureSourceType.SYSTEM,
            retryable=False,
            recovery_disposition=RecoveryDisposition.FAIL,
            evidence=[f"timeout_seconds:{timeout_seconds}"],
            trace={"timeout_seconds": timeout_seconds, "execution_id": execution.execution_id},
        )
        return failure
