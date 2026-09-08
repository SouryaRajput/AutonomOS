"""
Unit Tests for Programmer V1 Phase 5.3:
Timeout & Cancellation Handling.

Validates deterministic timeout and cancellation handling for Programmer executions:
1. Cancellation while idle (no active backend).
2. Cancellation while Cline/backend is active.
3. Cancellation during command execution (active process termination).
4. Timeout handling (execution marked FAILED, TIMEOUT failure candidate emitted).
5. Cancellation race with completion (preserves COMPLETED state).
6. Repeated cancellation (idempotent behavior).
7. Cancellation after completion (rejected, does not overwrite COMPLETED).
8. Failed cancellation (unconfirmed agent termination creates explicit blocker, not CANCELLED).
9. Cleanup after cancellation (cleanup callback invoked, cleanup_completed recorded).
10. ControlledProgrammerExecutor integration.
"""

from __future__ import annotations

import subprocess
import time
import unittest
from unittest.mock import MagicMock

from core.programmer.contracts.cancellation import ProgrammerCancellation
from core.programmer.contracts.cancellation_handler import ProgrammerCancellationHandler
from core.programmer.contracts.capability_binding import ClineCapabilityBinding
from core.programmer.contracts.coding_agent import (
    CodingAgentBackendType,
    CodingAgentCancellationRequest,
    CodingAgentCancellationResult,
    MockCodingAgentBackend,
)
from core.programmer.contracts.execution import ProgrammerExecution
from core.programmer.contracts.execution_context import ProgrammerExecutionContext
from core.programmer.contracts.executor import ControlledProgrammerExecutor
from core.programmer.contracts.identifiers import (
    new_execution_id,
    new_work_order_id,
)
from core.programmer.contracts.watchdog import ExecutionWatchdog, SimulatedClock
from core.programmer.contracts.work_order import ProgrammerWorkOrder
from core.programmer.contracts.workspace import ProgrammerWorkspace
from core.programmer.errors import ProgrammerValidationError
from core.programmer.types import (
    ExecutionContextStatus,
    ProgrammerExecutionStatus,
    ProgrammerFailureCategory,
    RecoveryDisposition,
)


class TestProgrammerTimeoutAndCancellation(unittest.TestCase):
    def setUp(self) -> None:
        self.handler = ProgrammerCancellationHandler()
        self.exec_id = new_execution_id()
        self.wo_id = new_work_order_id()
        self.execution = ProgrammerExecution(
            execution_id=self.exec_id,
            work_order_id=self.wo_id,
            task_id="task-123",
            project_id="proj-456",
            correlation_id="corr-789",
            status=ProgrammerExecutionStatus.STARTING,
        )

    # -------------------------------------------------------------------------
    # 1. Cancellation While Idle
    # -------------------------------------------------------------------------

    def test_cancellation_while_idle(self) -> None:
        cancellation = self.handler.cancel_execution(
            execution=self.execution,
            requested_by="manager",
            reason="Task superseded by new user goal.",
        )
        self.assertEqual(self.execution.status, ProgrammerExecutionStatus.CANCELLED)
        self.assertTrue(cancellation.confirmed)
        self.assertTrue(cancellation.agent_terminated)
        self.assertTrue(cancellation.cleanup_completed)
        self.assertEqual(cancellation.requested_by, "manager")
        self.assertEqual(cancellation.reason, "Task superseded by new user goal.")
        self.assertIsNone(cancellation.confirmation_error)

    # -------------------------------------------------------------------------
    # 2. Cancellation While Cline Is Active
    # -------------------------------------------------------------------------

    def test_cancellation_while_cline_is_active(self) -> None:
        self.execution.transition_to(
            ProgrammerExecutionStatus.RUNNING,
            reason="Cline execution started",
        )
        mock_backend = MockCodingAgentBackend()

        cancellation = self.handler.cancel_execution(
            execution=self.execution,
            requested_by="user",
            reason="User clicked cancel in UI.",
            backend=mock_backend,
        )

        self.assertEqual(self.execution.status, ProgrammerExecutionStatus.CANCELLED)
        self.assertTrue(cancellation.confirmed)
        self.assertTrue(cancellation.agent_terminated)
        self.assertTrue(cancellation.cleanup_completed)
        self.assertFalse(cancellation.has_error)

    # -------------------------------------------------------------------------
    # 3. Cancellation During Command Execution
    # -------------------------------------------------------------------------

    def test_cancellation_during_command_execution(self) -> None:
        self.execution.transition_to(
            ProgrammerExecutionStatus.RUNNING,
            reason="Running commands",
        )
        # Create a mock capability binding with an active process
        mock_binding = MagicMock(spec=ClineCapabilityBinding)
        mock_binding.cancel_active_commands.return_value = 1

        cancellation = self.handler.cancel_execution(
            execution=self.execution,
            requested_by="manager",
            reason="Command taking too long.",
            capability_binding=mock_binding,
        )

        mock_binding.cancel_active_commands.assert_called_once_with(reason="Command taking too long.")
        self.assertEqual(self.execution.status, ProgrammerExecutionStatus.CANCELLED)
        self.assertTrue(cancellation.confirmed)

    # -------------------------------------------------------------------------
    # 4. Timeout Handling
    # -------------------------------------------------------------------------

    def test_timeout_handling(self) -> None:
        self.execution.transition_to(
            ProgrammerExecutionStatus.RUNNING,
            reason="Agent running",
        )
        mock_backend = MockCodingAgentBackend()
        cleanup_called = False

        def cleanup_hook():
            nonlocal cleanup_called
            cleanup_called = True

        clock = SimulatedClock()
        watchdog = ExecutionWatchdog(clock=clock)
        watchdog.start_monitoring(execution_id=self.exec_id, work_order_id=self.wo_id)

        failure = self.handler.handle_timeout(
            execution=self.execution,
            timeout_seconds=300.0,
            backend=mock_backend,
            watchdog=watchdog,
            cleanup_callback=cleanup_hook,
        )

        self.assertEqual(self.execution.status, ProgrammerExecutionStatus.FAILED)
        self.assertTrue(cleanup_called)
        self.assertEqual(failure.category, ProgrammerFailureCategory.TIMEOUT)
        self.assertEqual(failure.recovery_disposition, RecoveryDisposition.FAIL)
        self.assertFalse(failure.retryable)
        self.assertIn("300.0s", failure.message)
        self.assertEqual(watchdog.get_record(self.exec_id).monitoring_status.value, "STOPPED")

    # -------------------------------------------------------------------------
    # 5. Cancellation Race With Completion
    # -------------------------------------------------------------------------

    def test_cancellation_race_with_completion(self) -> None:
        # Execution is already completed
        self.execution.status = ProgrammerExecutionStatus.COMPLETED

        cancellation = self.handler.cancel_execution(
            execution=self.execution,
            requested_by="manager",
            reason="Late cancellation arrival.",
        )

        # Must preserve COMPLETED state and NOT overwrite with CANCELLED
        self.assertEqual(self.execution.status, ProgrammerExecutionStatus.COMPLETED)
        self.assertFalse(cancellation.confirmed)
        self.assertIn("already completed", cancellation.confirmation_error or "")
        self.assertIn("cancellation_attempts", self.execution.metadata)

    # -------------------------------------------------------------------------
    # 6. Repeated Cancellation (Idempotent)
    # -------------------------------------------------------------------------

    def test_repeated_cancellation(self) -> None:
        first = self.handler.cancel_execution(
            execution=self.execution,
            requested_by="manager",
            reason="Initial cancel.",
        )
        self.assertEqual(self.execution.status, ProgrammerExecutionStatus.CANCELLED)
        self.assertTrue(first.confirmed)

        # Second cancel call
        second = self.handler.cancel_execution(
            execution=self.execution,
            requested_by="manager",
            reason="Second cancel call.",
        )
        self.assertEqual(self.execution.status, ProgrammerExecutionStatus.CANCELLED)
        self.assertTrue(second.confirmed)
        self.assertEqual(second.reason, first.reason)  # Idempotent return

    # -------------------------------------------------------------------------
    # 7. Cancellation After Completion
    # -------------------------------------------------------------------------

    def test_cancellation_after_completion(self) -> None:
        self.execution.status = ProgrammerExecutionStatus.COMPLETED
        cancellation = self.handler.cancel_execution(
            execution=self.execution,
            requested_by="user",
            reason="Stop",
        )
        self.assertEqual(self.execution.status, ProgrammerExecutionStatus.COMPLETED)
        self.assertFalse(cancellation.confirmed)

    # -------------------------------------------------------------------------
    # 8. Failed Cancellation (Agent Refused / Unconfirmed)
    # -------------------------------------------------------------------------

    def test_failed_cancellation_records_blocker_not_cancelled(self) -> None:
        self.execution.transition_to(
            ProgrammerExecutionStatus.RUNNING,
            reason="Agent running",
        )
        # Mock backend that fails cancellation
        failing_backend = MagicMock(spec=MockCodingAgentBackend)
        failing_backend.cancel.return_value = CodingAgentCancellationResult(
            execution_id=self.exec_id,
            work_order_id=self.wo_id,
            cancelled=False,
            reason="Process hung in uninterruptible sleep.",
        )

        cancellation = self.handler.cancel_execution(
            execution=self.execution,
            requested_by="manager",
            reason="Abort task",
            backend=failing_backend,
        )

        # Invariant: Do NOT silently mark an execution cancelled while process is active
        self.assertNotEqual(self.execution.status, ProgrammerExecutionStatus.CANCELLED)
        self.assertEqual(self.execution.status, ProgrammerExecutionStatus.BLOCKED)
        self.assertFalse(cancellation.confirmed)
        self.assertFalse(cancellation.agent_terminated)
        self.assertTrue(cancellation.has_error)
        self.assertIn("Process hung in uninterruptible sleep", cancellation.confirmation_error or "")
        self.assertTrue(self.execution.metadata.get("cancellation_unconfirmed"))
        self.assertEqual(len(self.execution.blockers), 1)

    # -------------------------------------------------------------------------
    # 9. Cleanup After Cancellation
    # -------------------------------------------------------------------------

    def test_cleanup_after_cancellation(self) -> None:
        cleanup_called = False

        def cleanup_hook():
            nonlocal cleanup_called
            cleanup_called = True

        clock = SimulatedClock()
        watchdog = ExecutionWatchdog(clock=clock)
        watchdog.start_monitoring(execution_id=self.exec_id, work_order_id=self.wo_id)

        cancellation = self.handler.cancel_execution(
            execution=self.execution,
            requested_by="manager",
            reason="Cancel with cleanup",
            watchdog=watchdog,
            cleanup_callback=cleanup_hook,
        )

        self.assertTrue(cleanup_called)
        self.assertTrue(cancellation.cleanup_completed)
        self.assertEqual(watchdog.get_record(self.exec_id).monitoring_status.value, "STOPPED")

    # -------------------------------------------------------------------------
    # 10. ControlledProgrammerExecutor Integration
    # -------------------------------------------------------------------------

    def test_controlled_executor_cancel_integration(self) -> None:
        executor = ControlledProgrammerExecutor()
        executor._active_executions[self.exec_id] = self.execution
        mock_backend = MockCodingAgentBackend()
        executor._active_backends[self.exec_id] = mock_backend

        success = executor.cancel(
            execution_id=self.exec_id,
            reason="User cancelled via Executor facade.",
            requested_by="user",
        )

        self.assertTrue(success)
        self.assertEqual(self.execution.status, ProgrammerExecutionStatus.CANCELLED)
        self.assertIsNotNone(self.execution.cancellation)
        assert self.execution.cancellation is not None
        self.assertTrue(self.execution.cancellation.confirmed)
        self.assertTrue(self.execution.cancellation.agent_terminated)


if __name__ == "__main__":
    unittest.main()
