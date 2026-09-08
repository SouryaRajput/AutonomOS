"""
Unit Tests for Programmer V1 Phase 5.2:
Execution Watchdog & Heartbeats.

Validates deterministic monitoring of Programmer/Cline executions:
1. Normal heartbeat updates timestamps and maintains ACTIVE state.
2. Progress resets inactivity timer and recovers from IDLE/HUNG_SUSPECTED.
3. Inactivity threshold transitions ACTIVE -> IDLE without premature failure.
4. Hung threshold transitions IDLE -> HUNG_SUSPECTED and emits AGENT_HUNG candidate.
5. Execution timeout transitions to TIMED_OUT and emits TIMEOUT candidate.
6. Process termination: normal (exit 0) vs abnormal (exit != 0, AGENT_CRASH).
7. Cancellation transitions to CANCELLED and emits CANCELLATION candidate.
8. Duplicate heartbeat detection.
9. Stale heartbeat detection without timestamp regression.
10. Monitoring cleanup and resource teardown.
11. Record serialization (to_dict, from_dict, to_json).
12. Callback invocations and fake clock determinism.
"""

from __future__ import annotations

import json
import unittest

from core.programmer.contracts.identifiers import (
    WATCHDOG_ID_PREFIX,
    new_execution_id,
    new_watchdog_id,
    new_work_order_id,
    validate_watchdog_id,
)
from core.programmer.contracts.watchdog import (
    ExecutionMonitoringRecord,
    ExecutionWatchdog,
    SimulatedClock,
    WatchdogHeartbeatConfig,
    WatchdogTimeoutConfig,
)
from core.programmer.errors import ProgrammerValidationError
from core.programmer.types import (
    ExecutionMonitoringState,
    ProgrammerFailureCategory,
    RecoveryDisposition,
    WatchdogStatus,
)


class TestProgrammerExecutionWatchdog(unittest.TestCase):
    def setUp(self) -> None:
        self.clock = SimulatedClock(initial_time=1000.0)
        self.watchdog = ExecutionWatchdog(clock=self.clock)
        self.exec_id = new_execution_id()
        self.wo_id = new_work_order_id()
        self.timeout_config = WatchdogTimeoutConfig(execution_timeout_seconds=300.0)
        self.heartbeat_config = WatchdogHeartbeatConfig(
            heartbeat_interval_seconds=15.0,
            inactivity_threshold_seconds=60.0,
            hung_threshold_seconds=120.0,
        )

    # -------------------------------------------------------------------------
    # 1. Normal Heartbeat
    # -------------------------------------------------------------------------

    def test_normal_heartbeat_updates_timestamps_and_state(self) -> None:
        record = self.watchdog.start_monitoring(
            execution_id=self.exec_id,
            work_order_id=self.wo_id,
            timeout_config=self.timeout_config,
            heartbeat_config=self.heartbeat_config,
        )
        self.assertEqual(record.current_state, ExecutionMonitoringState.ACTIVE)
        self.assertEqual(record.execution_started_at, 1000.0)
        self.assertEqual(record.last_activity_at, 1000.0)
        self.assertIsNone(record.last_heartbeat_at)

        # Advance time by 15s and record heartbeat
        self.clock.advance(15.0)
        accepted = self.watchdog.record_heartbeat(self.exec_id, heartbeat_id="hb-1")
        self.assertTrue(accepted)
        self.assertEqual(record.last_heartbeat_at, 1015.0)
        self.assertEqual(record.last_activity_at, 1015.0)
        self.assertEqual(record.current_state, ExecutionMonitoringState.ACTIVE)

    # -------------------------------------------------------------------------
    # 2. Progress Resets Inactivity Timer
    # -------------------------------------------------------------------------

    def test_progress_resets_inactivity_timer(self) -> None:
        record = self.watchdog.start_monitoring(
            execution_id=self.exec_id,
            work_order_id=self.wo_id,
            timeout_config=self.timeout_config,
            heartbeat_config=self.heartbeat_config,
        )
        self.clock.advance(40.0)  # t=1040
        accepted = self.watchdog.record_progress(
            self.exec_id,
            activity_type="TOOL_CALL",
            payload={"tool": "view_file"},
        )
        self.assertTrue(accepted)
        self.assertEqual(record.last_activity_at, 1040.0)
        self.assertIsNone(record.last_heartbeat_at)  # Heartbeat wasn't touched
        self.assertEqual(record.current_state, ExecutionMonitoringState.ACTIVE)

    # -------------------------------------------------------------------------
    # 3. Inactivity Threshold (Multi-Stage Silence: ACTIVE -> IDLE)
    # -------------------------------------------------------------------------

    def test_inactivity_threshold_transitions_to_idle_without_failure(self) -> None:
        record = self.watchdog.start_monitoring(
            execution_id=self.exec_id,
            work_order_id=self.wo_id,
            timeout_config=self.timeout_config,
            heartbeat_config=self.heartbeat_config,
        )

        # Inactivity threshold is 60s. Advance by 65s (< hung_threshold 120s)
        self.clock.advance(65.0)
        new_failures = self.watchdog.check_health(self.exec_id)

        # Guarantees: Does NOT immediately classify silence as failure
        self.assertEqual(len(new_failures), 0)
        self.assertEqual(record.current_state, ExecutionMonitoringState.IDLE)
        self.assertEqual(len(record.failure_candidates), 0)

        # Progress recovers IDLE state back to ACTIVE
        self.clock.advance(5.0)
        self.watchdog.record_progress(self.exec_id, activity_type="THINKING")
        self.assertEqual(record.current_state, ExecutionMonitoringState.ACTIVE)

    # -------------------------------------------------------------------------
    # 4. Hung Detection (IDLE -> HUNG_SUSPECTED with failure candidate)
    # -------------------------------------------------------------------------

    def test_hung_threshold_transitions_to_hung_suspected_and_emits_failure(self) -> None:
        record = self.watchdog.start_monitoring(
            execution_id=self.exec_id,
            work_order_id=self.wo_id,
            timeout_config=self.timeout_config,
            heartbeat_config=self.heartbeat_config,
        )

        # Advance beyond hung_threshold (120s) -> 125s
        self.clock.advance(125.0)
        new_failures = self.watchdog.check_health(self.exec_id)

        self.assertEqual(len(new_failures), 1)
        fail = new_failures[0]
        self.assertEqual(fail.category, ProgrammerFailureCategory.AGENT_HUNG)
        self.assertEqual(fail.recovery_disposition, RecoveryDisposition.RETRY)
        self.assertTrue(fail.retryable)
        self.assertEqual(record.current_state, ExecutionMonitoringState.HUNG_SUSPECTED)
        self.assertIn(fail, record.failure_candidates)

        # Heartbeat recovers HUNG_SUSPECTED back to ACTIVE
        self.clock.advance(2.0)
        accepted = self.watchdog.record_heartbeat(self.exec_id, heartbeat_id="recovery-hb")
        self.assertTrue(accepted)
        self.assertEqual(record.current_state, ExecutionMonitoringState.ACTIVE)

    # -------------------------------------------------------------------------
    # 5. Execution Timeout
    # -------------------------------------------------------------------------

    def test_execution_timeout_transitions_to_timed_out_and_emits_failure(self) -> None:
        record = self.watchdog.start_monitoring(
            execution_id=self.exec_id,
            work_order_id=self.wo_id,
            timeout_config=self.timeout_config,  # 300s
            heartbeat_config=self.heartbeat_config,
        )

        # Keep active with heartbeats until t=290
        for _ in range(19):
            self.clock.advance(15.0)
            self.watchdog.record_heartbeat(self.exec_id)
        self.assertEqual(record.current_state, ExecutionMonitoringState.ACTIVE)

        # Now advance past total execution timeout (t=305s from start)
        self.clock.advance(20.0)
        new_failures = self.watchdog.check_health(self.exec_id)

        self.assertEqual(len(new_failures), 1)
        fail = new_failures[0]
        self.assertEqual(fail.category, ProgrammerFailureCategory.TIMEOUT)
        self.assertEqual(fail.recovery_disposition, RecoveryDisposition.FAIL)
        self.assertFalse(fail.retryable)
        self.assertEqual(record.current_state, ExecutionMonitoringState.TIMED_OUT)
        self.assertTrue(record.is_terminal)

        # Subsequent heartbeats are ignored on terminal state
        rejected = self.watchdog.record_heartbeat(self.exec_id)
        self.assertFalse(rejected)

    # -------------------------------------------------------------------------
    # 6. Process Termination
    # -------------------------------------------------------------------------

    def test_normal_process_termination(self) -> None:
        record = self.watchdog.start_monitoring(
            execution_id=self.exec_id,
            work_order_id=self.wo_id,
        )
        self.clock.advance(30.0)
        fail = self.watchdog.record_process_termination(self.exec_id, exit_code=0)
        self.assertIsNone(fail)
        self.assertEqual(record.current_state, ExecutionMonitoringState.TERMINATED)
        self.assertTrue(record.is_terminal)

    def test_abnormal_process_termination_emits_crash_failure(self) -> None:
        record = self.watchdog.start_monitoring(
            execution_id=self.exec_id,
            work_order_id=self.wo_id,
        )
        self.clock.advance(30.0)
        fail = self.watchdog.record_process_termination(
            self.exec_id,
            exit_code=137,
            reason="Container killed by OOM killer.",
        )
        self.assertIsNotNone(fail)
        assert fail is not None
        self.assertEqual(fail.category, ProgrammerFailureCategory.AGENT_CRASH)
        self.assertEqual(fail.recovery_disposition, RecoveryDisposition.RETRY)
        self.assertTrue(fail.retryable)
        self.assertEqual(record.current_state, ExecutionMonitoringState.TERMINATED)
        self.assertIn(fail, record.failure_candidates)

    # -------------------------------------------------------------------------
    # 7. Cancellation
    # -------------------------------------------------------------------------

    def test_cancellation_transitions_to_cancelled_and_emits_failure(self) -> None:
        record = self.watchdog.start_monitoring(
            execution_id=self.exec_id,
            work_order_id=self.wo_id,
        )
        self.clock.advance(10.0)
        fail = self.watchdog.request_cancellation(self.exec_id, reason="Manager aborted task.")
        self.assertIsNotNone(fail)
        assert fail is not None
        self.assertEqual(fail.category, ProgrammerFailureCategory.CANCELLATION)
        self.assertEqual(fail.recovery_disposition, RecoveryDisposition.CANCEL)
        self.assertFalse(fail.retryable)
        self.assertEqual(record.current_state, ExecutionMonitoringState.CANCELLED)
        self.assertTrue(record.is_terminal)

    # -------------------------------------------------------------------------
    # 8. Duplicate & Stale Heartbeats
    # -------------------------------------------------------------------------

    def test_duplicate_heartbeat_detection(self) -> None:
        record = self.watchdog.start_monitoring(
            execution_id=self.exec_id,
            work_order_id=self.wo_id,
        )
        self.clock.advance(10.0)
        first = self.watchdog.record_heartbeat(self.exec_id, heartbeat_id="hb-unique-1")
        self.assertTrue(first)

        # Same heartbeat_id sent again
        duplicate = self.watchdog.record_heartbeat(self.exec_id, heartbeat_id="hb-unique-1")
        self.assertFalse(duplicate)
        self.assertIn("duplicate_heartbeats", record.trace)
        self.assertEqual(len(record.trace["duplicate_heartbeats"]), 1)

    def test_stale_heartbeat_detection(self) -> None:
        record = self.watchdog.start_monitoring(
            execution_id=self.exec_id,
            work_order_id=self.wo_id,
        )
        self.clock.advance(50.0)  # t=1050
        self.watchdog.record_heartbeat(self.exec_id, timestamp=1050.0)
        self.assertEqual(record.last_heartbeat_at, 1050.0)

        # Stale heartbeat with earlier timestamp (1030.0 < 1050.0)
        stale = self.watchdog.record_heartbeat(self.exec_id, timestamp=1030.0)
        self.assertFalse(stale)
        # Timestamp must not regress
        self.assertEqual(record.last_heartbeat_at, 1050.0)
        self.assertIn("stale_heartbeats", record.trace)
        self.assertEqual(len(record.trace["stale_heartbeats"]), 1)

    # -------------------------------------------------------------------------
    # 9. Monitoring Cleanup
    # -------------------------------------------------------------------------

    def test_monitoring_cleanup(self) -> None:
        record = self.watchdog.start_monitoring(
            execution_id=self.exec_id,
            work_order_id=self.wo_id,
        )
        self.assertEqual(record.monitoring_status, WatchdogStatus.RUNNING)

        stopped_record = self.watchdog.stop_monitoring(self.exec_id)
        self.assertIsNotNone(stopped_record)
        assert stopped_record is not None
        self.assertEqual(stopped_record.monitoring_status, WatchdogStatus.STOPPED)

        # Further operations on stopped execution return False/None
        self.assertFalse(self.watchdog.record_heartbeat(self.exec_id))
        self.assertFalse(self.watchdog.record_progress(self.exec_id))

    # -------------------------------------------------------------------------
    # 10. Record Serialization Roundtrip
    # -------------------------------------------------------------------------

    def test_monitoring_record_serialization_roundtrip(self) -> None:
        record = self.watchdog.start_monitoring(
            execution_id=self.exec_id,
            work_order_id=self.wo_id,
            timeout_config=self.timeout_config,
            heartbeat_config=self.heartbeat_config,
        )
        self.clock.advance(15.0)
        self.watchdog.record_heartbeat(self.exec_id, heartbeat_id="hb-1")

        data = record.to_dict()
        self.assertTrue(data["record_id"].startswith(WATCHDOG_ID_PREFIX))
        validate_watchdog_id(data["record_id"])
        self.assertEqual(data["execution_id"], self.exec_id)
        self.assertEqual(data["current_state"], "ACTIVE")
        self.assertEqual(data["monitoring_status"], "RUNNING")

        reconstructed = ExecutionMonitoringRecord.from_dict(data)
        self.assertEqual(reconstructed.record_id, record.record_id)
        self.assertEqual(reconstructed.execution_id, record.execution_id)
        self.assertEqual(reconstructed.work_order_id, record.work_order_id)
        self.assertEqual(reconstructed.current_state, record.current_state)
        self.assertEqual(reconstructed.last_heartbeat_at, record.last_heartbeat_at)
        self.assertEqual(reconstructed.last_activity_at, record.last_activity_at)

        json_str = record.to_json()
        parsed = json.loads(json_str)
        self.assertEqual(parsed["record_id"], record.record_id)

    # -------------------------------------------------------------------------
    # 11. Configuration Validation
    # -------------------------------------------------------------------------

    def test_invalid_configurations_raise_validation_error(self) -> None:
        # Invalid timeout
        bad_timeout = WatchdogTimeoutConfig(execution_timeout_seconds=-10.0)
        with self.assertRaises(ProgrammerValidationError):
            bad_timeout.validate()

        # Invalid hung threshold (hung_threshold <= inactivity_threshold)
        bad_hb = WatchdogHeartbeatConfig(
            inactivity_threshold_seconds=60.0,
            hung_threshold_seconds=50.0,  # invalid!
        )
        with self.assertRaises(ProgrammerValidationError):
            bad_hb.validate()

    # -------------------------------------------------------------------------
    # 12. State Change and Failure Candidate Callbacks
    # -------------------------------------------------------------------------

    def test_state_change_and_failure_callbacks(self) -> None:
        state_changes: list[tuple[str, str]] = []
        failures: list[str] = []

        def on_change(rec: ExecutionMonitoringRecord, st: ExecutionMonitoringState) -> None:
            state_changes.append((rec.execution_id, st.value))

        def on_fail(f) -> None:
            failures.append(f.category.value)

        custom_watchdog = ExecutionWatchdog(
            clock=self.clock,
            on_state_change=on_change,
            on_failure_candidate=on_fail,
        )

        custom_watchdog.start_monitoring(
            execution_id=self.exec_id,
            work_order_id=self.wo_id,
            heartbeat_config=self.heartbeat_config,
        )

        # Advance to idle
        self.clock.advance(65.0)
        custom_watchdog.check_health(self.exec_id)
        self.assertIn((self.exec_id, "IDLE"), state_changes)

        # Advance to hung
        self.clock.advance(60.0)  # total 125s
        custom_watchdog.check_health(self.exec_id)
        self.assertIn((self.exec_id, "HUNG_SUSPECTED"), state_changes)
        self.assertIn("AGENT_HUNG", failures)


if __name__ == "__main__":
    unittest.main()
