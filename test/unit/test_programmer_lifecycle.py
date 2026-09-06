import unittest

from core.programmer.contracts import (
    BLOCKER_ID_PREFIX,
    ProgrammerBlocker,
    ProgrammerCancellation,
    ProgrammerExecution,
    ProgrammerLifecycle,
    ProgrammerResult,
    ProgrammerWorkOrder,
    new_blocker_id,
    new_execution_id,
    new_work_order_id,
    validate_blocker_id,
)
from core.programmer.errors import (
    InvalidProgrammerIdError,
    InvalidProgrammerTransitionError,
    ProgrammerValidationError,
)
from core.programmer.types import (
    ProgrammerBlockerCategory,
    ProgrammerBlockerSeverity,
    ProgrammerExecutionStatus,
    ProgrammerResultStatus,
)


class TestProgrammerLifecycle(unittest.TestCase):
    """
    Unit tests for Programmer execution lifecycle and failure states under Programmer V1 Phase 1 (Step 1.4).
    Validates deterministic lifecycle transitions, terminal states, blocker structures, and cancellation provenance.
    """

    def setUp(self):
        self.work_order_id = new_work_order_id()
        self.task_id = "task-oauth-01"
        self.project_id = "proj-autonomos"
        self.correlation_id = "corr-oauth-01"

    def _create_execution(self, status: ProgrammerExecutionStatus = ProgrammerExecutionStatus.REQUESTED) -> ProgrammerExecution:
        return ProgrammerExecution(
            execution_id=new_execution_id(),
            work_order_id=self.work_order_id,
            task_id=self.task_id,
            project_id=self.project_id,
            correlation_id=self.correlation_id,
            status=status,
        )

    # -------------------------------------------------------------------------
    # 1. Valid Transitions & Golden Path
    # -------------------------------------------------------------------------

    def test_valid_full_lifecycle_path(self):
        """
        Verify the canonical execution lifecycle:
        REQUESTED -> STARTING -> RUNNING -> VERIFYING -> COMPLETING -> COMPLETED
        """
        execution = self._create_execution(status=ProgrammerExecutionStatus.REQUESTED)
        self.assertEqual(execution.status, ProgrammerExecutionStatus.REQUESTED)
        self.assertFalse(execution.is_terminal)
        self.assertFalse(execution.is_active)  # REQUESTED is pre-active

        # REQUESTED -> STARTING
        execution.transition_to(ProgrammerExecutionStatus.STARTING)
        self.assertEqual(execution.status, ProgrammerExecutionStatus.STARTING)
        self.assertTrue(execution.is_active)
        self.assertFalse(execution.is_terminal)

        # STARTING -> RUNNING
        execution.transition_to(ProgrammerExecutionStatus.RUNNING)
        self.assertEqual(execution.status, ProgrammerExecutionStatus.RUNNING)
        self.assertTrue(execution.is_active)
        self.assertIsNotNone(execution.started_at)
        self.assertIsNone(execution.completed_at)

        # RUNNING -> VERIFYING
        execution.transition_to(ProgrammerExecutionStatus.VERIFYING)
        self.assertEqual(execution.status, ProgrammerExecutionStatus.VERIFYING)
        self.assertTrue(execution.is_active)

        # VERIFYING -> COMPLETING
        execution.transition_to(ProgrammerExecutionStatus.COMPLETING)
        self.assertEqual(execution.status, ProgrammerExecutionStatus.COMPLETING)
        self.assertTrue(execution.is_active)

        # COMPLETING -> COMPLETED
        execution.transition_to(ProgrammerExecutionStatus.COMPLETED)
        self.assertEqual(execution.status, ProgrammerExecutionStatus.COMPLETED)
        self.assertTrue(execution.is_terminal)
        self.assertFalse(execution.is_active)
        self.assertIsNotNone(execution.completed_at)

    def test_iterative_verification_loop(self):
        """
        Verify iterative loop when verification identifies defects:
        RUNNING -> VERIFYING -> RUNNING -> VERIFYING -> COMPLETING -> COMPLETED
        """
        execution = self._create_execution(status=ProgrammerExecutionStatus.RUNNING)

        # 1st verification attempt fails some checks
        execution.transition_to(ProgrammerExecutionStatus.VERIFYING)
        self.assertEqual(execution.status, ProgrammerExecutionStatus.VERIFYING)

        # Loop back to RUNNING to apply fix
        execution.transition_to(ProgrammerExecutionStatus.RUNNING, reason="Tests failed: patching parser bug")
        self.assertEqual(execution.status, ProgrammerExecutionStatus.RUNNING)

        # 2nd verification attempt passes
        execution.transition_to(ProgrammerExecutionStatus.VERIFYING)
        self.assertEqual(execution.status, ProgrammerExecutionStatus.VERIFYING)

        # Proceed to completion
        execution.transition_to(ProgrammerExecutionStatus.COMPLETING)
        execution.transition_to(ProgrammerExecutionStatus.COMPLETED)
        self.assertEqual(execution.status, ProgrammerExecutionStatus.COMPLETED)

    # -------------------------------------------------------------------------
    # 2. Blocking and Unblocking Flow
    # -------------------------------------------------------------------------

    def test_blocking_and_resolution_flow(self):
        """
        Verify that execution can be blocked on missing context/permission and safely unblocked:
        RUNNING -> BLOCKED -> (Manager resolves) -> RUNNING -> VERIFYING -> COMPLETING -> COMPLETED
        """
        execution = self._create_execution(status=ProgrammerExecutionStatus.RUNNING)

        # Hit a blocker requiring architectural decision
        blocker = execution.block(
            reason="Ambiguous OAuth redirect URL scheme for mobile clients",
            category=ProgrammerBlockerCategory.ARCHITECTURAL,
            severity=ProgrammerBlockerSeverity.HIGH,
            required_decision="Confirm whether custom scheme 'autonomos://' or universal links should be used.",
        )
        self.assertEqual(execution.status, ProgrammerExecutionStatus.BLOCKED)
        self.assertTrue(execution.is_active)  # BLOCKED is active (suspended, not terminal)
        self.assertFalse(execution.is_terminal)
        self.assertEqual(len(execution.active_blockers), 1)
        self.assertEqual(execution.active_blockers[0].category, ProgrammerBlockerCategory.ARCHITECTURAL)
        self.assertFalse(blocker.is_resolved)

        # Manager unblocks with decision
        execution.unblock(
            resolution_notes="Use custom URI scheme 'autonomos://oauth/callback'",
            resolved_by="manager",
            target_status=ProgrammerExecutionStatus.RUNNING,
        )
        self.assertEqual(execution.status, ProgrammerExecutionStatus.RUNNING)
        self.assertEqual(len(execution.active_blockers), 0)
        self.assertTrue(blocker.is_resolved)
        self.assertEqual(blocker.resolved_by, "manager")
        self.assertIn("custom URI scheme", blocker.resolution_notes)

        # Can now continue to completion
        execution.transition_to(ProgrammerExecutionStatus.VERIFYING)
        execution.transition_to(ProgrammerExecutionStatus.COMPLETING)
        execution.transition_to(ProgrammerExecutionStatus.COMPLETED)
        self.assertEqual(execution.status, ProgrammerExecutionStatus.COMPLETED)

    # -------------------------------------------------------------------------
    # 3. Failure States
    # -------------------------------------------------------------------------

    def test_failure_states_from_active_phases(self):
        """Verify that FAILED is reachable from STARTING, RUNNING, VERIFYING, COMPLETING, and BLOCKED."""
        for phase in [
            ProgrammerExecutionStatus.STARTING,
            ProgrammerExecutionStatus.RUNNING,
            ProgrammerExecutionStatus.VERIFYING,
            ProgrammerExecutionStatus.COMPLETING,
            ProgrammerExecutionStatus.BLOCKED,
        ]:
            exec_obj = self._create_execution(status=phase)
            exec_obj.transition_to(ProgrammerExecutionStatus.FAILED, reason=f"Failure during {phase.value}")
            self.assertEqual(exec_obj.status, ProgrammerExecutionStatus.FAILED)
            self.assertTrue(exec_obj.is_terminal)
            self.assertIsNotNone(exec_obj.completed_at)

    def test_failure_vs_blocked_distinction(self):
        """
        Verify fundamental semantic distinction:
        - BLOCKED: Programmer needs decision/authorization/context, can resume.
        - FAILED: Attempted work suffered fatal/unrecoverable failure, terminal.
        """
        # BLOCKED can resume
        blocked_exec = self._create_execution(status=ProgrammerExecutionStatus.RUNNING)
        blocked_exec.block(reason="Need GitHub Client ID", category=ProgrammerBlockerCategory.PERMISSION)
        self.assertEqual(blocked_exec.status, ProgrammerExecutionStatus.BLOCKED)
        self.assertFalse(blocked_exec.is_terminal)
        self.assertTrue(ProgrammerLifecycle.can_transition(ProgrammerExecutionStatus.BLOCKED, ProgrammerExecutionStatus.RUNNING))

        # FAILED is terminal and cannot resume
        failed_exec = self._create_execution(status=ProgrammerExecutionStatus.RUNNING)
        failed_exec.transition_to(ProgrammerExecutionStatus.FAILED, reason="Compilation failed with syntax error")
        self.assertEqual(failed_exec.status, ProgrammerExecutionStatus.FAILED)
        self.assertTrue(failed_exec.is_terminal)
        self.assertFalse(ProgrammerLifecycle.can_transition(ProgrammerExecutionStatus.FAILED, ProgrammerExecutionStatus.RUNNING))

    # -------------------------------------------------------------------------
    # 4. Cancellation Provenance
    # -------------------------------------------------------------------------

    def test_cancellation_provenance_and_terminal_state(self):
        """Verify cancellation provenance (requested_by, reason, timestamp) and terminal status."""
        for phase in [
            ProgrammerExecutionStatus.REQUESTED,
            ProgrammerExecutionStatus.STARTING,
            ProgrammerExecutionStatus.RUNNING,
            ProgrammerExecutionStatus.VERIFYING,
            ProgrammerExecutionStatus.BLOCKED,
        ]:
            execution = self._create_execution(status=phase)
            cancellation = execution.cancel(
                requested_by="manager",
                reason=f"Priority reprioritization during {phase.value}",
                metadata={"priority_shift": "critical_hotfix"},
            )
            self.assertEqual(execution.status, ProgrammerExecutionStatus.CANCELLED)
            self.assertTrue(execution.is_terminal)
            self.assertIsNotNone(execution.completed_at)
            self.assertIsNotNone(execution.cancellation)
            self.assertEqual(execution.cancellation.requested_by, "manager")
            self.assertIn("Priority reprioritization", execution.cancellation.reason)
            self.assertIsNotNone(execution.cancellation.requested_at)
            self.assertEqual(execution.cancellation.metadata["priority_shift"], "critical_hotfix")

    def test_cancellation_validation(self):
        """Verify that ProgrammerCancellation requires non-empty requested_by and reason."""
        with self.assertRaises(ProgrammerValidationError):
            ProgrammerCancellation(requested_by="", reason="Some reason")

        with self.assertRaises(ProgrammerValidationError):
            ProgrammerCancellation(requested_by="manager", reason="   ")

    # -------------------------------------------------------------------------
    # 5. Invalid Transitions & Terminal State Guards
    # -------------------------------------------------------------------------

    def test_terminal_states_cannot_transition(self):
        """
        At minimum prevent:
        - COMPLETED -> RUNNING
        - CANCELLED -> RUNNING
        - FAILED -> RUNNING without explicit retry semantics
        """
        # COMPLETED cannot transition anywhere
        comp_exec = self._create_execution(status=ProgrammerExecutionStatus.COMPLETED)
        for target in [
            ProgrammerExecutionStatus.RUNNING,
            ProgrammerExecutionStatus.STARTING,
            ProgrammerExecutionStatus.VERIFYING,
            ProgrammerExecutionStatus.FAILED,
        ]:
            with self.assertRaises(InvalidProgrammerTransitionError) as ctx:
                comp_exec.transition_to(target)
            self.assertEqual(ctx.exception.current_status, "COMPLETED")
            self.assertEqual(ctx.exception.target_status, target.value)

        # CANCELLED cannot transition anywhere
        canc_exec = self._create_execution(status=ProgrammerExecutionStatus.CANCELLED)
        with self.assertRaises(InvalidProgrammerTransitionError):
            canc_exec.transition_to(ProgrammerExecutionStatus.RUNNING)

        # FAILED cannot transition anywhere without new execution cycle
        fail_exec = self._create_execution(status=ProgrammerExecutionStatus.FAILED)
        with self.assertRaises(InvalidProgrammerTransitionError):
            fail_exec.transition_to(ProgrammerExecutionStatus.RUNNING)

    def test_blocked_cannot_transition_to_completed_without_resolution(self):
        """Verify that BLOCKED cannot transition directly to COMPLETED or COMPLETING."""
        blocked_exec = self._create_execution(status=ProgrammerExecutionStatus.BLOCKED)

        with self.assertRaises(InvalidProgrammerTransitionError) as ctx:
            blocked_exec.transition_to(ProgrammerExecutionStatus.COMPLETED)
        self.assertEqual(ctx.exception.current_status, "BLOCKED")
        self.assertEqual(ctx.exception.target_status, "COMPLETED")

        with self.assertRaises(InvalidProgrammerTransitionError):
            blocked_exec.transition_to(ProgrammerExecutionStatus.COMPLETING)

    def test_arbitrary_state_jumps_rejected(self):
        """Verify that arbitrary illegal jumps between non-adjacent phases are rejected."""
        # REQUESTED cannot jump directly to RUNNING or VERIFYING or COMPLETED
        req_exec = self._create_execution(status=ProgrammerExecutionStatus.REQUESTED)
        with self.assertRaises(InvalidProgrammerTransitionError):
            req_exec.transition_to(ProgrammerExecutionStatus.RUNNING)
        with self.assertRaises(InvalidProgrammerTransitionError):
            req_exec.transition_to(ProgrammerExecutionStatus.VERIFYING)
        with self.assertRaises(InvalidProgrammerTransitionError):
            req_exec.transition_to(ProgrammerExecutionStatus.COMPLETED)

        # STARTING cannot jump directly to VERIFYING or COMPLETED
        start_exec = self._create_execution(status=ProgrammerExecutionStatus.STARTING)
        with self.assertRaises(InvalidProgrammerTransitionError):
            start_exec.transition_to(ProgrammerExecutionStatus.VERIFYING)
        with self.assertRaises(InvalidProgrammerTransitionError):
            start_exec.transition_to(ProgrammerExecutionStatus.COMPLETED)

    # -------------------------------------------------------------------------
    # 6. Blocker Structure & Categories
    # -------------------------------------------------------------------------

    def test_blocker_structure_and_categories(self):
        """Verify ProgrammerBlocker structure, ID validation, all 8 categories, and all severities."""
        all_categories = [
            ProgrammerBlockerCategory.SCOPE,
            ProgrammerBlockerCategory.PERMISSION,
            ProgrammerBlockerCategory.MISSING_CONTEXT,
            ProgrammerBlockerCategory.ARCHITECTURAL,
            ProgrammerBlockerCategory.DEPENDENCY,
            ProgrammerBlockerCategory.VERIFICATION,
            ProgrammerBlockerCategory.RESOURCE,
            ProgrammerBlockerCategory.OTHER,
        ]

        for cat in all_categories:
            blk_id = new_blocker_id()
            self.assertTrue(blk_id.startswith(BLOCKER_ID_PREFIX))
            validate_blocker_id(blk_id)

            blocker = ProgrammerBlocker(
                blocker_id=blk_id,
                work_order_id=self.work_order_id,
                category=cat,
                description=f"Blocker under {cat.value}",
                severity=ProgrammerBlockerSeverity.CRITICAL,
                required_decision="Manager approval needed",
                required_context="File missing in /docs",
            )
            self.assertEqual(blocker.category, cat)
            self.assertEqual(blocker.severity, ProgrammerBlockerSeverity.CRITICAL)
            self.assertFalse(blocker.is_resolved)

            # Test round-trip serialization
            d = blocker.to_dict()
            restored = ProgrammerBlocker.from_dict(d)
            self.assertEqual(restored.blocker_id, blocker.blocker_id)
            self.assertEqual(restored.category, blocker.category)
            self.assertEqual(restored.severity, blocker.severity)
            self.assertEqual(restored.description, blocker.description)
            self.assertEqual(restored.required_decision, blocker.required_decision)

    def test_invalid_blocker_rejections(self):
        """Verify validation errors for malformed blockers."""
        # Wrong prefix
        with self.assertRaises(InvalidProgrammerIdError):
            ProgrammerBlocker(
                blocker_id="block-12345",
                work_order_id=self.work_order_id,
                category=ProgrammerBlockerCategory.OTHER,
                description="Invalid ID blocker",
            )

        # Empty description
        with self.assertRaises(ProgrammerValidationError):
            ProgrammerBlocker(
                blocker_id=new_blocker_id(),
                work_order_id=self.work_order_id,
                category=ProgrammerBlockerCategory.OTHER,
                description="    ",
            )

        # Empty resolution notes on resolve
        blk = ProgrammerBlocker(
            blocker_id=new_blocker_id(),
            work_order_id=self.work_order_id,
            category=ProgrammerBlockerCategory.OTHER,
            description="Valid blocker",
        )
        with self.assertRaises(ProgrammerValidationError):
            blk.resolve(resolution_notes="   ")

    # -------------------------------------------------------------------------
    # 7. Execution Serialization Fidelity
    # -------------------------------------------------------------------------

    def test_execution_serialization_fidelity(self):
        """Verify full round-trip serialization for ProgrammerExecution including blockers and cancellation."""
        execution = self._create_execution(status=ProgrammerExecutionStatus.RUNNING)
        execution.block(
            reason="Missing payment webhook secret",
            category=ProgrammerBlockerCategory.PERMISSION,
            severity=ProgrammerBlockerSeverity.HIGH,
            required_decision="Provide WEBHOOK_SECRET in environment",
        )
        execution.unblock(resolution_notes="Secret injected into test vault")
        execution.transition_to(ProgrammerExecutionStatus.VERIFYING)

        d = execution.to_dict()
        restored = ProgrammerExecution.from_dict(d)

        self.assertEqual(restored.execution_id, execution.execution_id)
        self.assertEqual(restored.work_order_id, execution.work_order_id)
        self.assertEqual(restored.task_id, execution.task_id)
        self.assertEqual(restored.status, ProgrammerExecutionStatus.VERIFYING)
        self.assertEqual(len(restored.blockers), 1)
        self.assertEqual(restored.blockers[0].category, ProgrammerBlockerCategory.PERMISSION)
        self.assertTrue(restored.blockers[0].is_resolved)
        self.assertEqual(len(restored.active_blockers), 0)


if __name__ == "__main__":
    unittest.main()
