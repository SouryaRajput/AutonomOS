from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import json
import logging
import time
from typing import Any, Callable, Optional, Sequence, Union

from core.programmer.contracts.failure import ProgrammerFailure
from core.programmer.contracts.identifiers import (
    new_failure_id,
    new_programmer_event_id,
    new_watchdog_id,
    validate_execution_id,
    validate_watchdog_id,
    validate_work_order_id,
)
from core.programmer.errors import ProgrammerLineageError, ProgrammerValidationError
from core.programmer.types import (
    ExecutionMonitoringState,
    FailureSourceType,
    ProgrammerExecutionEventType,
    ProgrammerFailureCategory,
    ProgrammerFailureSeverity,
    RecoveryDisposition,
    WatchdogStatus,
)

logger = logging.getLogger("AutonomOS.Programmer.Watchdog")


def utc_now_iso(ts: Optional[float] = None) -> str:
    """Format Unix timestamp as ISO 8601 UTC string."""
    dt = datetime.fromtimestamp(ts or time.time(), tz=timezone.utc)
    return dt.isoformat()


# ==============================================================================
# 1. Clock Abstraction
# ==============================================================================


class SimulatedClock:
    """
    Deterministic simulated clock for testing time-dependent watchdog behavior.
    Allows exact advancement of virtual time without thread sleeps.
    """

    def __init__(self, initial_time: float = 1000.0) -> None:
        self._current_time = float(initial_time)

    def now(self) -> float:
        """Return current simulated time in seconds."""
        return self._current_time

    def advance(self, seconds: float) -> float:
        """Advance simulated time by given seconds and return new time."""
        if seconds < 0:
            raise ValueError(f"Cannot advance simulated clock by negative time ({seconds}s).")
        self._current_time += float(seconds)
        return self._current_time

    def set(self, timestamp: float) -> None:
        """Set absolute simulated time in seconds."""
        self._current_time = float(timestamp)

    def __call__(self) -> float:
        return self.now()


# ==============================================================================
# 2. Watchdog Configurations
# ==============================================================================


@dataclass
class WatchdogTimeoutConfig:
    """Configuration governing execution-level timeout thresholds."""
    execution_timeout_seconds: float = 600.0
    grace_period_seconds: float = 5.0

    def validate(self) -> None:
        if self.execution_timeout_seconds <= 0:
            raise ProgrammerValidationError(
                f"execution_timeout_seconds must be positive, got {self.execution_timeout_seconds}",
                field_name="execution_timeout_seconds",
            )
        if self.grace_period_seconds < 0:
            raise ProgrammerValidationError(
                f"grace_period_seconds must be non-negative, got {self.grace_period_seconds}",
                field_name="grace_period_seconds",
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "execution_timeout_seconds": self.execution_timeout_seconds,
            "grace_period_seconds": self.grace_period_seconds,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> WatchdogTimeoutConfig:
        return cls(
            execution_timeout_seconds=float(data.get("execution_timeout_seconds", 600.0)),
            grace_period_seconds=float(data.get("grace_period_seconds", 5.0)),
        )


@dataclass
class WatchdogHeartbeatConfig:
    """Configuration governing heartbeats, inactivity timers, and hung detection."""
    heartbeat_interval_seconds: float = 15.0
    inactivity_threshold_seconds: float = 60.0
    hung_threshold_seconds: float = 120.0
    deduplication_window_seconds: float = 0.1

    def validate(self) -> None:
        if self.heartbeat_interval_seconds <= 0:
            raise ProgrammerValidationError(
                f"heartbeat_interval_seconds must be positive, got {self.heartbeat_interval_seconds}",
                field_name="heartbeat_interval_seconds",
            )
        if self.inactivity_threshold_seconds <= 0:
            raise ProgrammerValidationError(
                f"inactivity_threshold_seconds must be positive, got {self.inactivity_threshold_seconds}",
                field_name="inactivity_threshold_seconds",
            )
        if self.hung_threshold_seconds <= self.inactivity_threshold_seconds:
            raise ProgrammerValidationError(
                f"hung_threshold_seconds ({self.hung_threshold_seconds}s) must be strictly greater than "
                f"inactivity_threshold_seconds ({self.inactivity_threshold_seconds}s).",
                field_name="hung_threshold_seconds",
            )
        if self.deduplication_window_seconds < 0:
            raise ProgrammerValidationError(
                f"deduplication_window_seconds must be non-negative, got {self.deduplication_window_seconds}",
                field_name="deduplication_window_seconds",
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "heartbeat_interval_seconds": self.heartbeat_interval_seconds,
            "inactivity_threshold_seconds": self.inactivity_threshold_seconds,
            "hung_threshold_seconds": self.hung_threshold_seconds,
            "deduplication_window_seconds": self.deduplication_window_seconds,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> WatchdogHeartbeatConfig:
        return cls(
            heartbeat_interval_seconds=float(data.get("heartbeat_interval_seconds", 15.0)),
            inactivity_threshold_seconds=float(data.get("inactivity_threshold_seconds", 60.0)),
            hung_threshold_seconds=float(data.get("hung_threshold_seconds", 120.0)),
            deduplication_window_seconds=float(data.get("deduplication_window_seconds", 0.1)),
        )


# ==============================================================================
# 3. Execution Monitoring Record
# ==============================================================================


@dataclass
class ExecutionMonitoringRecord:
    """
    Authoritative state model representing active execution monitoring for a Programmer attempt.
    
    Guarantees:
    - Maintains causal lineage to execution_id and work_order_id.
    - Captures timestamps for execution start, last activity, and last heartbeat.
    - Transitions strictly through: ACTIVE -> IDLE -> HUNG_SUSPECTED or terminal states.
    - Retains emitted failure candidates and trace telemetry.
    """
    record_id: str
    execution_id: str
    work_order_id: str
    execution_started_at: float
    last_activity_at: float
    timeout_configuration: WatchdogTimeoutConfig
    heartbeat_configuration: WatchdogHeartbeatConfig
    last_heartbeat_at: Optional[float] = None
    current_state: ExecutionMonitoringState = ExecutionMonitoringState.ACTIVE
    monitoring_status: WatchdogStatus = WatchdogStatus.RUNNING
    trace: dict[str, Any] = field(default_factory=dict)
    failure_candidates: list[ProgrammerFailure] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if isinstance(self.current_state, str):
            try:
                self.current_state = ExecutionMonitoringState(self.current_state.upper())
            except ValueError:
                self.current_state = ExecutionMonitoringState.ACTIVE

        if isinstance(self.monitoring_status, str):
            try:
                self.monitoring_status = WatchdogStatus(self.monitoring_status.upper())
            except ValueError:
                self.monitoring_status = WatchdogStatus.RUNNING

        if not self.trace:
            self.trace = {
                "record_id": self.record_id,
                "execution_id": self.execution_id,
                "work_order_id": self.work_order_id,
                "execution_started_at_iso": utc_now_iso(self.execution_started_at),
            }

    def validate(self) -> None:
        """Validate record identifiers, configurations, and state integrity."""
        validate_watchdog_id(self.record_id)
        validate_execution_id(self.execution_id)
        validate_work_order_id(self.work_order_id)
        self.timeout_configuration.validate()
        self.heartbeat_configuration.validate()

        if self.last_activity_at < self.execution_started_at:
            raise ProgrammerValidationError(
                f"last_activity_at ({self.last_activity_at}) cannot be earlier than execution_started_at ({self.execution_started_at}).",
                field_name="last_activity_at",
            )
        if self.last_heartbeat_at is not None and self.last_heartbeat_at < self.execution_started_at:
            raise ProgrammerValidationError(
                f"last_heartbeat_at ({self.last_heartbeat_at}) cannot be earlier than execution_started_at ({self.execution_started_at}).",
                field_name="last_heartbeat_at",
            )

    @property
    def is_terminal(self) -> bool:
        """True if the monitoring state is terminal."""
        return self.current_state in (
            ExecutionMonitoringState.TERMINATED,
            ExecutionMonitoringState.TIMED_OUT,
            ExecutionMonitoringState.CANCELLED,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "record_id": self.record_id,
            "execution_id": self.execution_id,
            "work_order_id": self.work_order_id,
            "execution_started_at": self.execution_started_at,
            "execution_started_at_iso": utc_now_iso(self.execution_started_at),
            "last_activity_at": self.last_activity_at,
            "last_activity_at_iso": utc_now_iso(self.last_activity_at),
            "last_heartbeat_at": self.last_heartbeat_at,
            "last_heartbeat_at_iso": utc_now_iso(self.last_heartbeat_at) if self.last_heartbeat_at else None,
            "current_state": self.current_state.value,
            "monitoring_status": self.monitoring_status.value,
            "timeout_configuration": self.timeout_configuration.to_dict(),
            "heartbeat_configuration": self.heartbeat_configuration.to_dict(),
            "failure_candidates": [f.to_dict() for f in self.failure_candidates],
            "trace": dict(self.trace),
            "metadata": dict(self.metadata),
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=2)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ExecutionMonitoringRecord:
        record = cls(
            record_id=str(data.get("record_id", new_watchdog_id())),
            execution_id=str(data["execution_id"]),
            work_order_id=str(data["work_order_id"]),
            execution_started_at=float(data["execution_started_at"]),
            last_activity_at=float(data["last_activity_at"]),
            timeout_configuration=WatchdogTimeoutConfig.from_dict(data.get("timeout_configuration", {})),
            heartbeat_configuration=WatchdogHeartbeatConfig.from_dict(data.get("heartbeat_configuration", {})),
            last_heartbeat_at=float(data["last_heartbeat_at"]) if data.get("last_heartbeat_at") is not None else None,
            current_state=ExecutionMonitoringState(str(data.get("current_state", "ACTIVE")).upper()),
            monitoring_status=WatchdogStatus(str(data.get("monitoring_status", "RUNNING")).upper()),
            failure_candidates=[ProgrammerFailure.from_dict(f) for f in data.get("failure_candidates", [])],
            trace=dict(data.get("trace", {})),
            metadata=dict(data.get("metadata", {})),
        )
        record.validate()
        return record


# ==============================================================================
# 4. Execution Watchdog Controller
# ==============================================================================


class ExecutionWatchdog:
    """
    Deterministic execution monitor and heartbeat supervisor for Programmer executions.
    
    Architectural Guarantees:
    - PASSIVE AND MONITOR-ONLY:
      * NEVER expands permissions.
      * NEVER restarts Cline by itself.
      * NEVER modifies repository files.
      * NEVER executes arbitrary shell commands.
      * NEVER alters acceptance criteria.
    - MULTI-STAGE SILENCE EVALUATION:
      * Does NOT immediately classify silence as failure.
      * Inactivity transitions ACTIVE -> IDLE (silence noted, no failure).
      * Prolonged silence exceeding hung_threshold transitions IDLE -> HUNG_SUSPECTED.
    - DETERMINISTIC TIME RESOLUTION:
      * Uses injected clock interface (e.g. SimulatedClock) for 100% reproducible tests.
    """

    def __init__(
        self,
        clock: Optional[Callable[[], float]] = None,
        on_state_change: Optional[Callable[[ExecutionMonitoringRecord, ExecutionMonitoringState], None]] = None,
        on_failure_candidate: Optional[Callable[[ProgrammerFailure], None]] = None,
    ) -> None:
        self._clock = clock or time.time
        self._on_state_change = on_state_change
        self._on_failure_candidate = on_failure_candidate
        self._records: dict[str, ExecutionMonitoringRecord] = {}
        self._seen_heartbeats: dict[str, set[str]] = {}

    def get_time(self) -> float:
        """Get current clock time in seconds."""
        return float(self._clock())

    def get_record(self, execution_id: str) -> Optional[ExecutionMonitoringRecord]:
        """Retrieve monitoring record for an execution if active or retained."""
        return self._records.get(execution_id)

    def start_monitoring(
        self,
        execution_id: str,
        work_order_id: str,
        timeout_config: Optional[WatchdogTimeoutConfig] = None,
        heartbeat_config: Optional[WatchdogHeartbeatConfig] = None,
        initial_state: ExecutionMonitoringState = ExecutionMonitoringState.ACTIVE,
        metadata: Optional[dict[str, Any]] = None,
    ) -> ExecutionMonitoringRecord:
        """
        Register and begin monitoring an execution session.
        """
        validate_execution_id(execution_id)
        validate_work_order_id(work_order_id)

        now = self.get_time()
        t_cfg = timeout_config or WatchdogTimeoutConfig()
        h_cfg = heartbeat_config or WatchdogHeartbeatConfig()
        t_cfg.validate()
        h_cfg.validate()

        record = ExecutionMonitoringRecord(
            record_id=new_watchdog_id(),
            execution_id=execution_id,
            work_order_id=work_order_id,
            execution_started_at=now,
            last_activity_at=now,
            last_heartbeat_at=None,
            timeout_configuration=t_cfg,
            heartbeat_configuration=h_cfg,
            current_state=initial_state,
            monitoring_status=WatchdogStatus.RUNNING,
            trace={
                "execution_id": execution_id,
                "work_order_id": work_order_id,
                "started_at_iso": utc_now_iso(now),
            },
            metadata=dict(metadata or {}),
        )
        record.validate()
        self._records[execution_id] = record
        self._seen_heartbeats[execution_id] = set()
        logger.info(f"Started monitoring execution '{execution_id}' (record_id={record.record_id})")
        return record

    def record_heartbeat(
        self,
        execution_id: str,
        heartbeat_id: Optional[str] = None,
        timestamp: Optional[float] = None,
        payload: Optional[dict[str, Any]] = None,
    ) -> bool:
        """
        Record a periodic heartbeat from coding agent or worker container.
        
        Returns:
            True if heartbeat was valid and accepted.
            False if monitoring not found, session terminal, stale, or duplicate.
        """
        record = self._records.get(execution_id)
        if not record:
            logger.warning(f"Heartbeat received for untracked execution '{execution_id}'")
            return False

        if record.is_terminal or record.monitoring_status != WatchdogStatus.RUNNING:
            logger.debug(f"Heartbeat ignored for terminal/stopped execution '{execution_id}'")
            return False

        now = self.get_time()
        hb_time = timestamp if timestamp is not None else now

        # 1. Stale Heartbeat Detection:
        # Heartbeat timestamp is strictly older than already recorded last_heartbeat_at
        if record.last_heartbeat_at is not None and hb_time < record.last_heartbeat_at:
            logger.warning(
                f"Stale heartbeat detected for '{execution_id}': "
                f"timestamp {hb_time} < last_heartbeat_at {record.last_heartbeat_at}"
            )
            record.trace.setdefault("stale_heartbeats", []).append({
                "heartbeat_id": heartbeat_id,
                "timestamp": hb_time,
                "recorded_at": now,
            })
            return False

        # 2. Duplicate Heartbeat Detection:
        seen = self._seen_heartbeats.setdefault(execution_id, set())
        if heartbeat_id and heartbeat_id in seen:
            logger.warning(f"Duplicate heartbeat_id '{heartbeat_id}' received for '{execution_id}'")
            record.trace.setdefault("duplicate_heartbeats", []).append({
                "heartbeat_id": heartbeat_id,
                "timestamp": hb_time,
                "recorded_at": now,
            })
            return False

        if record.last_heartbeat_at is not None and abs(hb_time - record.last_heartbeat_at) < record.heartbeat_configuration.deduplication_window_seconds:
            logger.debug(f"Heartbeat for '{execution_id}' arrived within duplicate window; acknowledged without duplicate alert.")

        if heartbeat_id:
            seen.add(heartbeat_id)

        # 3. Accept heartbeat and update activity:
        record.last_heartbeat_at = hb_time
        record.last_activity_at = max(record.last_activity_at, hb_time)

        # 4. If state was IDLE or HUNG_SUSPECTED, recover back to ACTIVE:
        if record.current_state in (ExecutionMonitoringState.IDLE, ExecutionMonitoringState.HUNG_SUSPECTED):
            self._transition_state(record, ExecutionMonitoringState.ACTIVE, reason="Heartbeat received")

        return True

    def record_progress(
        self,
        execution_id: str,
        activity_type: Optional[str] = None,
        timestamp: Optional[float] = None,
        payload: Optional[dict[str, Any]] = None,
    ) -> bool:
        """
        Record discrete execution progress (e.g. tool execution, thinking step, file change).
        Resets inactivity timer and recovers IDLE or HUNG_SUSPECTED back to ACTIVE.
        """
        record = self._records.get(execution_id)
        if not record:
            return False

        if record.is_terminal or record.monitoring_status != WatchdogStatus.RUNNING:
            return False

        now = self.get_time()
        prog_time = timestamp if timestamp is not None else now

        # Update last activity
        record.last_activity_at = max(record.last_activity_at, prog_time)

        # Recover to ACTIVE if currently IDLE or HUNG_SUSPECTED
        if record.current_state in (ExecutionMonitoringState.IDLE, ExecutionMonitoringState.HUNG_SUSPECTED):
            self._transition_state(
                record,
                ExecutionMonitoringState.ACTIVE,
                reason=f"Progress observed ({activity_type or 'activity'})",
            )

        return True

    def record_process_termination(
        self,
        execution_id: str,
        exit_code: int,
        timestamp: Optional[float] = None,
        reason: Optional[str] = None,
    ) -> Optional[ProgrammerFailure]:
        """
        Record external coding agent process/container termination.
        Immediately transitions to TERMINATED. If exit_code != 0, emits AGENT_CRASH candidate.
        """
        record = self._records.get(execution_id)
        if not record:
            return None

        now = self.get_time()
        term_time = timestamp if timestamp is not None else now
        record.last_activity_at = max(record.last_activity_at, term_time)

        self._transition_state(
            record,
            ExecutionMonitoringState.TERMINATED,
            reason=f"Process terminated with exit code {exit_code}: {reason or 'no details'}",
        )

        failure_candidate: Optional[ProgrammerFailure] = None
        if exit_code != 0:
            msg = reason or f"Coding agent process terminated abnormally with exit code {exit_code}."
            failure_candidate = ProgrammerFailure.create(
                execution_id=record.execution_id,
                work_order_id=record.work_order_id,
                category=ProgrammerFailureCategory.AGENT_CRASH,
                message=msg,
                severity=ProgrammerFailureSeverity.HIGH,
                source=FailureSourceType.AGENT,
                retryable=True,
                recovery_disposition=RecoveryDisposition.RETRY,
                evidence=[f"exit_code:{exit_code}"],
                trace={"exit_code": exit_code, "reason": reason, "record_id": record.record_id},
            )
            record.failure_candidates.append(failure_candidate)
            if self._on_failure_candidate:
                self._on_failure_candidate(failure_candidate)

        return failure_candidate

    def request_cancellation(
        self,
        execution_id: str,
        reason: Optional[str] = None,
        timestamp: Optional[float] = None,
    ) -> Optional[ProgrammerFailure]:
        """
        Handle a cancellation request from Manager or system.
        Immediately transitions to CANCELLED and emits a CANCELLATION failure candidate.
        """
        record = self._records.get(execution_id)
        if not record:
            return None

        now = self.get_time()
        canc_time = timestamp if timestamp is not None else now
        record.last_activity_at = max(record.last_activity_at, canc_time)

        self._transition_state(
            record,
            ExecutionMonitoringState.CANCELLED,
            reason=f"Cancellation requested: {reason or 'user requested'}",
        )

        failure_candidate = ProgrammerFailure.create(
            execution_id=record.execution_id,
            work_order_id=record.work_order_id,
            category=ProgrammerFailureCategory.CANCELLATION,
            message=reason or "Execution was cancelled.",
            severity=ProgrammerFailureSeverity.HIGH,
            source=FailureSourceType.SYSTEM,
            retryable=False,
            recovery_disposition=RecoveryDisposition.CANCEL,
            evidence=[f"cancelled_by:{reason or 'system'}"],
            trace={"reason": reason, "record_id": record.record_id},
        )
        record.failure_candidates.append(failure_candidate)
        if self._on_failure_candidate:
            self._on_failure_candidate(failure_candidate)

        return failure_candidate

    def check_health(
        self,
        execution_id: Optional[str] = None,
        current_time: Optional[float] = None,
    ) -> list[ProgrammerFailure]:
        """
        Deterministic tick / health check inspecting thresholds for tracked executions.
        
        Evaluates:
        1. Execution Timeout (total run time >= timeout_seconds).
        2. Inactivity Stage 1 (now - last_activity >= inactivity_threshold) -> IDLE.
        3. Inactivity Stage 2 (now - last_activity >= hung_threshold) -> HUNG_SUSPECTED.
        
        Returns:
            List of newly generated ProgrammerFailure candidates.
        """
        now = current_time if current_time is not None else self.get_time()
        targets = [self._records[execution_id]] if execution_id and execution_id in self._records else list(self._records.values())

        new_failures: list[ProgrammerFailure] = []

        for record in targets:
            if record.is_terminal or record.monitoring_status != WatchdogStatus.RUNNING:
                continue

            # ------------------------------------------------------------------
            # 1. Total Execution Timeout Check
            # ------------------------------------------------------------------
            elapsed_total = now - record.execution_started_at
            timeout_limit = record.timeout_configuration.execution_timeout_seconds

            if elapsed_total >= timeout_limit:
                self._transition_state(
                    record,
                    ExecutionMonitoringState.TIMED_OUT,
                    reason=f"Execution exceeded configured timeout ({elapsed_total:.1f}s >= {timeout_limit:.1f}s)",
                )
                fail = ProgrammerFailure.create(
                    execution_id=record.execution_id,
                    work_order_id=record.work_order_id,
                    category=ProgrammerFailureCategory.TIMEOUT,
                    message=f"Execution exceeded configured timeout of {timeout_limit}s (elapsed {elapsed_total:.1f}s).",
                    severity=ProgrammerFailureSeverity.HIGH,
                    source=FailureSourceType.SYSTEM,
                    retryable=False,
                    recovery_disposition=RecoveryDisposition.FAIL,
                    evidence=[f"elapsed_seconds:{elapsed_total:.1f}", f"timeout_limit:{timeout_limit:.1f}"],
                    trace={"elapsed_seconds": elapsed_total, "timeout_seconds": timeout_limit},
                )
                record.failure_candidates.append(fail)
                new_failures.append(fail)
                if self._on_failure_candidate:
                    self._on_failure_candidate(fail)
                continue  # Stop further checks for this record once timed out

            # ------------------------------------------------------------------
            # 2. Multi-Stage Inactivity / Hung Detection
            # ------------------------------------------------------------------
            silence_duration = now - record.last_activity_at
            inact_limit = record.heartbeat_configuration.inactivity_threshold_seconds
            hung_limit = record.heartbeat_configuration.hung_threshold_seconds

            # Stage 2: Hung Suspected (silence >= hung_threshold)
            if silence_duration >= hung_limit:
                if record.current_state != ExecutionMonitoringState.HUNG_SUSPECTED:
                    self._transition_state(
                        record,
                        ExecutionMonitoringState.HUNG_SUSPECTED,
                        reason=f"Coding agent silence exceeded hung threshold ({silence_duration:.1f}s >= {hung_limit:.1f}s)",
                    )
                    fail = ProgrammerFailure.create(
                        execution_id=record.execution_id,
                        work_order_id=record.work_order_id,
                        category=ProgrammerFailureCategory.AGENT_HUNG,
                        message=f"Coding agent silence exceeded hung threshold ({silence_duration:.1f}s >= {hung_limit:.1f}s).",
                        severity=ProgrammerFailureSeverity.HIGH,
                        source=FailureSourceType.AGENT,
                        retryable=True,
                        recovery_disposition=RecoveryDisposition.RETRY,
                        evidence=[f"silence_seconds:{silence_duration:.1f}", f"hung_threshold:{hung_limit:.1f}"],
                        trace={"silence_seconds": silence_duration, "hung_threshold": hung_limit},
                    )
                    record.failure_candidates.append(fail)
                    new_failures.append(fail)
                    if self._on_failure_candidate:
                        self._on_failure_candidate(fail)

            # Stage 1: Idle (silence >= inactivity_threshold, but < hung_threshold)
            elif silence_duration >= inact_limit:
                if record.current_state == ExecutionMonitoringState.ACTIVE:
                    self._transition_state(
                        record,
                        ExecutionMonitoringState.IDLE,
                        reason=f"Inactivity threshold exceeded ({silence_duration:.1f}s >= {inact_limit:.1f}s)",
                    )
                    # Notice: We do NOT emit a failure here. Silence is noted as IDLE.

        return new_failures

    def stop_monitoring(self, execution_id: str) -> Optional[ExecutionMonitoringRecord]:
        """
        Conclude and clean up monitoring for an execution session.
        Marks monitoring_status as STOPPED and frees active tracking.
        """
        record = self._records.get(execution_id)
        if not record:
            return None

        record.monitoring_status = WatchdogStatus.STOPPED
        self._seen_heartbeats.pop(execution_id, None)
        logger.info(f"Stopped monitoring execution '{execution_id}'")
        return record

    def _transition_state(
        self,
        record: ExecutionMonitoringRecord,
        target_state: ExecutionMonitoringState,
        reason: str,
    ) -> None:
        """Internal helper recording state transitions and invoking hook."""
        prev_state = record.current_state
        if prev_state == target_state:
            return

        record.current_state = target_state
        record.trace.setdefault("transitions", []).append({
            "from_state": prev_state.value,
            "to_state": target_state.value,
            "timestamp": self.get_time(),
            "reason": reason,
        })
        logger.info(f"Execution '{record.execution_id}' monitoring state: {prev_state.value} -> {target_state.value} ({reason})")

        if self._on_state_change:
            try:
                self._on_state_change(record, target_state)
            except Exception as err:
                logger.warning(f"Error in on_state_change callback: {err}")
