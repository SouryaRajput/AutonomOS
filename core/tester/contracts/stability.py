from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import logging
from typing import Any, Optional, Sequence

from core.tester.contracts.identifiers import (
    new_crash_event_id,
    new_error_group_id,
    new_stability_evaluation_id,
    validate_crash_event_id,
    validate_error_group_id,
    validate_evidence_id,
    validate_execution_id,
    validate_stability_evaluation_id,
    validate_test_case_id,
)
from core.tester.contracts.performance import PerformanceMeasurement
from core.tester.errors import (
    TesterBoundaryViolationError,
    TesterLineageError,
    TesterValidationError,
)
from core.tester.types import (
    ProcessState,
    RuntimeEventType,
    StabilityFailureType,
    StabilityStatus,
)

logger = logging.getLogger("AutonomOS.Tester.StabilityContracts")


def utc_now() -> str:
    """Generate ISO 8601 UTC timestamp."""
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# Error Group (Repetition Grouping)
# ---------------------------------------------------------------------------

@dataclass
class ErrorGroup:
    """
    Authoritative record grouping repeated identical runtime errors.
    Prevents defect explosion (e.g. 100 identical 404s become 1 grouped finding).
    Captures first occurrence, occurrence count, affected resource, test cases, and evidence.
    """
    __test__ = False
    message: str
    group_id: str = field(default_factory=new_error_group_id)
    error_type: RuntimeEventType | str = RuntimeEventType.CONSOLE_ERROR
    affected_resource: Optional[str] = None
    count: int = 1
    first_occurrence: str = field(default_factory=utc_now)
    last_occurrence: Optional[str] = None
    test_cases: list[str] = field(default_factory=list)
    evidence_ids: list[str] = field(default_factory=list)
    status_code: Optional[int] = None
    execution_id: Optional[str] = None
    metadata: dict[str, Any] = field(default_factory=dict)
    provenance: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.validate()

    def validate(self) -> None:
        validate_error_group_id(self.group_id)

        if not self.message or not str(self.message).strip():
            raise TesterValidationError("ErrorGroup requires a valid non-empty message.", field_name="message")

        if isinstance(self.error_type, str):
            try:
                self.error_type = RuntimeEventType(self.error_type.upper().strip())
            except (ValueError, KeyError):
                pass

        if self.count < 1:
            raise TesterValidationError("ErrorGroup count must be >= 1.", field_name="count")

        if self.execution_id:
            validate_execution_id(self.execution_id)

        valid_tcs: list[str] = []
        for tcid in self.test_cases:
            if tcid:
                validate_test_case_id(str(tcid))
                if str(tcid) not in valid_tcs:
                    valid_tcs.append(str(tcid))
        self.test_cases = valid_tcs

        valid_evs: list[str] = []
        for eid in self.evidence_ids:
            if eid:
                validate_evidence_id(str(eid))
                if str(eid) not in valid_evs:
                    valid_evs.append(str(eid))
        self.evidence_ids = valid_evs

        self.metadata = dict(self.metadata)
        self.provenance = dict(self.provenance)

    def add_occurrence(
        self,
        timestamp: Optional[str] = None,
        test_case_id: Optional[str] = None,
        evidence_id: Optional[str] = None,
    ) -> None:
        """Increment error count and track occurrence metadata."""
        self.count += 1
        ts = timestamp or utc_now()
        self.last_occurrence = ts

        if test_case_id:
            validate_test_case_id(test_case_id)
            if test_case_id not in self.test_cases:
                self.test_cases.append(test_case_id)

        if evidence_id:
            validate_evidence_id(evidence_id)
            if evidence_id not in self.evidence_ids:
                self.evidence_ids.append(evidence_id)

    def to_dict(self) -> dict[str, Any]:
        return {
            "group_id": self.group_id,
            "message": self.message,
            "error_type": self.error_type.value if hasattr(self.error_type, "value") else str(self.error_type),
            "affected_resource": self.affected_resource,
            "count": self.count,
            "first_occurrence": self.first_occurrence,
            "last_occurrence": self.last_occurrence,
            "test_cases": list(self.test_cases),
            "evidence_ids": list(self.evidence_ids),
            "status_code": self.status_code,
            "execution_id": self.execution_id,
            "metadata": dict(self.metadata),
            "provenance": dict(self.provenance),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ErrorGroup:
        raw_et = data.get("error_type", RuntimeEventType.CONSOLE_ERROR.value)
        try:
            et = RuntimeEventType(str(raw_et).upper().strip())
        except (ValueError, KeyError):
            et = str(raw_et)

        return cls(
            group_id=str(data.get("group_id") or new_error_group_id()),
            message=str(data.get("message", "")),
            error_type=et,
            affected_resource=data.get("affected_resource"),
            count=int(data.get("count", 1)),
            first_occurrence=str(data.get("first_occurrence") or utc_now()),
            last_occurrence=data.get("last_occurrence"),
            test_cases=list(data.get("test_cases", [])),
            evidence_ids=list(data.get("evidence_ids", [])),
            status_code=data.get("status_code"),
            execution_id=data.get("execution_id"),
            metadata=dict(data.get("metadata", {})),
            provenance=dict(data.get("provenance", {})),
        )

    # Invariant guards
    def apply_fix(self, *args: Any, **kwargs: Any) -> None:
        raise TesterBoundaryViolationError(
            action="STABILITY_AUTO_FIX",
            reason="ErrorGroup is an observation record; cannot modify application code or apply fixes.",
        )

    def auto_fix(self, *args: Any, **kwargs: Any) -> None:
        raise TesterBoundaryViolationError(
            action="STABILITY_AUTO_FIX",
            reason="ErrorGroup is an observation record; cannot modify application code or apply fixes.",
        )

    def create_defect(self, *args: Any, **kwargs: Any) -> None:
        raise TesterBoundaryViolationError(
            action="STABILITY_DEFECT_CREATION",
            reason="ErrorGroup does not directly create defects; classification occurs separately.",
        )


# ---------------------------------------------------------------------------
# Crash Event
# ---------------------------------------------------------------------------

@dataclass
class CrashEvent:
    """
    Authoritative record of an application, server, or browser crash.
    Captures failure type, affected test case, process state, logs, and evidence.
    """
    __test__ = False
    message: str
    crash_id: str = field(default_factory=new_crash_event_id)
    failure_type: StabilityFailureType = StabilityFailureType.APPLICATION_CRASH
    affected_test_case_id: Optional[str] = None
    process_state: ProcessState = ProcessState.CRASHED
    exit_code: Optional[int] = None
    stack_trace: Optional[str] = None
    logs: list[str] = field(default_factory=list)
    evidence_ids: list[str] = field(default_factory=list)
    timestamp: str = field(default_factory=utc_now)
    execution_id: Optional[str] = None
    provenance: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.validate()

    def validate(self) -> None:
        validate_crash_event_id(self.crash_id)

        if not self.message or not str(self.message).strip():
            raise TesterValidationError("CrashEvent requires a valid non-empty message.", field_name="message")

        if isinstance(self.failure_type, str):
            try:
                self.failure_type = StabilityFailureType(self.failure_type.upper().strip())
            except ValueError:
                self.failure_type = StabilityFailureType.APPLICATION_CRASH

        if isinstance(self.process_state, str):
            try:
                self.process_state = ProcessState(self.process_state.upper().strip())
            except ValueError:
                self.process_state = ProcessState.CRASHED

        if self.execution_id:
            validate_execution_id(self.execution_id)

        if self.affected_test_case_id:
            validate_test_case_id(self.affected_test_case_id)

        valid_evs: list[str] = []
        for eid in self.evidence_ids:
            if eid:
                validate_evidence_id(str(eid))
                if str(eid) not in valid_evs:
                    valid_evs.append(str(eid))
        self.evidence_ids = valid_evs

        self.logs = list(self.logs)
        self.provenance = dict(self.provenance)
        self.metadata = dict(self.metadata)

    def to_dict(self) -> dict[str, Any]:
        return {
            "crash_id": self.crash_id,
            "message": self.message,
            "failure_type": self.failure_type.value,
            "affected_test_case_id": self.affected_test_case_id,
            "process_state": self.process_state.value,
            "exit_code": self.exit_code,
            "stack_trace": self.stack_trace,
            "logs": list(self.logs),
            "evidence_ids": list(self.evidence_ids),
            "timestamp": self.timestamp,
            "execution_id": self.execution_id,
            "provenance": dict(self.provenance),
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CrashEvent:
        raw_ft = data.get("failure_type", StabilityFailureType.APPLICATION_CRASH.value)
        try:
            ft = StabilityFailureType(str(raw_ft).upper().strip())
        except ValueError:
            ft = StabilityFailureType.APPLICATION_CRASH

        raw_ps = data.get("process_state", ProcessState.CRASHED.value)
        try:
            ps = ProcessState(str(raw_ps).upper().strip())
        except ValueError:
            ps = ProcessState.CRASHED

        return cls(
            crash_id=str(data.get("crash_id") or new_crash_event_id()),
            message=str(data.get("message", "")),
            failure_type=ft,
            affected_test_case_id=data.get("affected_test_case_id"),
            process_state=ps,
            exit_code=data.get("exit_code"),
            stack_trace=data.get("stack_trace"),
            logs=list(data.get("logs", [])),
            evidence_ids=list(data.get("evidence_ids", [])),
            timestamp=str(data.get("timestamp") or utc_now()),
            execution_id=data.get("execution_id"),
            provenance=dict(data.get("provenance", {})),
            metadata=dict(data.get("metadata", {})),
        )

    # Invariant guards
    def apply_fix(self, *args: Any, **kwargs: Any) -> None:
        raise TesterBoundaryViolationError(
            action="STABILITY_AUTO_FIX",
            reason="CrashEvent is an observation record; cannot modify application code or apply fixes.",
        )

    def auto_fix(self, *args: Any, **kwargs: Any) -> None:
        raise TesterBoundaryViolationError(
            action="STABILITY_AUTO_FIX",
            reason="CrashEvent is an observation record; cannot modify application code or apply fixes.",
        )

    def create_defect(self, *args: Any, **kwargs: Any) -> None:
        raise TesterBoundaryViolationError(
            action="STABILITY_DEFECT_CREATION",
            reason="CrashEvent does not directly create defects; classification occurs separately.",
        )


# ---------------------------------------------------------------------------
# Stability Specification
# ---------------------------------------------------------------------------

@dataclass
class StabilitySpec:
    """
    Authoritative specification of stability bounds and expectations.
    Governs bounded recovery limits, critical endpoints, and duration limits.
    """
    __test__ = False
    spec_id: str = field(default_factory=new_stability_evaluation_id)
    execution_id: Optional[str] = None
    test_case_id: Optional[str] = None
    max_recovery_attempts: int = 0
    bounded_duration_seconds: Optional[float] = None
    critical_processes: list[str] = field(default_factory=list)
    critical_endpoints: list[str] = field(default_factory=list)
    max_consecutive_errors: Optional[int] = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.validate()

    def validate(self) -> None:
        validate_stability_evaluation_id(self.spec_id)

        if self.execution_id:
            validate_execution_id(self.execution_id)

        if self.test_case_id:
            validate_test_case_id(self.test_case_id)

        if self.max_recovery_attempts < 0:
            raise TesterValidationError("max_recovery_attempts must be >= 0.", field_name="max_recovery_attempts")

        if self.bounded_duration_seconds is not None and self.bounded_duration_seconds <= 0:
            raise TesterValidationError("bounded_duration_seconds must be > 0.", field_name="bounded_duration_seconds")

        self.critical_processes = list(self.critical_processes)
        self.critical_endpoints = list(self.critical_endpoints)
        self.metadata = dict(self.metadata)

    def to_dict(self) -> dict[str, Any]:
        return {
            "spec_id": self.spec_id,
            "execution_id": self.execution_id,
            "test_case_id": self.test_case_id,
            "max_recovery_attempts": self.max_recovery_attempts,
            "bounded_duration_seconds": self.bounded_duration_seconds,
            "critical_processes": list(self.critical_processes),
            "critical_endpoints": list(self.critical_endpoints),
            "max_consecutive_errors": self.max_consecutive_errors,
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> StabilitySpec:
        return cls(
            spec_id=str(data.get("spec_id") or new_stability_evaluation_id()),
            execution_id=data.get("execution_id"),
            test_case_id=data.get("test_case_id"),
            max_recovery_attempts=int(data.get("max_recovery_attempts", 0)),
            bounded_duration_seconds=(
                float(data["bounded_duration_seconds"])
                if data.get("bounded_duration_seconds") is not None
                else None
            ),
            critical_processes=list(data.get("critical_processes", [])),
            critical_endpoints=list(data.get("critical_endpoints", [])),
            max_consecutive_errors=(
                int(data["max_consecutive_errors"])
                if data.get("max_consecutive_errors") is not None
                else None
            ),
            metadata=dict(data.get("metadata", {})),
        )

    # Invariant guards
    def apply_fix(self, *args: Any, **kwargs: Any) -> None:
        raise TesterBoundaryViolationError(
            action="STABILITY_AUTO_FIX",
            reason="StabilitySpec cannot modify application code or apply fixes.",
        )

    def auto_fix(self, *args: Any, **kwargs: Any) -> None:
        raise TesterBoundaryViolationError(
            action="STABILITY_AUTO_FIX",
            reason="StabilitySpec cannot modify application code or apply fixes.",
        )

    def create_defect(self, *args: Any, **kwargs: Any) -> None:
        raise TesterBoundaryViolationError(
            action="STABILITY_DEFECT_CREATION",
            reason="StabilitySpec cannot create defects.",
        )


# ---------------------------------------------------------------------------
# Stability Result
# ---------------------------------------------------------------------------

@dataclass
class StabilityResult:
    """
    Authoritative evaluation result for application stability and runtime health.
    Summarizes process states, reachability, crashes, grouped errors, and bounded recovery.
    """
    __test__ = False
    stability_id: str
    execution_id: str
    spec: StabilitySpec
    status: StabilityStatus = StabilityStatus.STABLE
    process_states: dict[str, ProcessState] = field(default_factory=dict)
    is_app_reachable: bool = True
    crashes: list[CrashEvent] = field(default_factory=list)
    error_groups: list[ErrorGroup] = field(default_factory=list)
    total_errors: int = 0
    recovery_attempts: int = 0
    recovery_budget_exhausted: bool = False
    measurements: list[PerformanceMeasurement] = field(default_factory=list)
    evidence_ids: list[str] = field(default_factory=list)
    provenance: dict[str, Any] = field(default_factory=dict)
    timestamps: dict[str, str] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.validate()

    def validate(self) -> None:
        validate_stability_evaluation_id(self.stability_id)
        validate_execution_id(self.execution_id)

        if isinstance(self.status, str):
            try:
                self.status = StabilityStatus(self.status.upper().strip())
            except ValueError:
                self.status = StabilityStatus.STABLE

        # Normalize process states
        norm_ps: dict[str, ProcessState] = {}
        for proc, st in self.process_states.items():
            if isinstance(st, str):
                try:
                    norm_ps[proc] = ProcessState(st.upper().strip())
                except ValueError:
                    norm_ps[proc] = ProcessState.UNKNOWN
            else:
                norm_ps[proc] = st
        self.process_states = norm_ps

        self.crashes = list(self.crashes)
        self.error_groups = list(self.error_groups)
        self.measurements = list(self.measurements)

        valid_evs: list[str] = []
        for eid in self.evidence_ids:
            if eid:
                validate_evidence_id(str(eid))
                if str(eid) not in valid_evs:
                    valid_evs.append(str(eid))
        self.evidence_ids = valid_evs

        self.provenance = dict(self.provenance)
        self.timestamps = dict(self.timestamps)
        self.notes = list(self.notes)
        self.metadata = dict(self.metadata)

    @property
    def is_stable(self) -> bool:
        return self.status == StabilityStatus.STABLE

    @property
    def is_crashed(self) -> bool:
        return len(self.crashes) > 0 or any(
            st in (ProcessState.CRASHED, ProcessState.TERMINATED)
            for st in self.process_states.values()
        )

    @property
    def has_critical_failure(self) -> bool:
        return self.status in (
            StabilityStatus.CRITICAL_FAILURE,
            StabilityStatus.RECOVERY_EXHAUSTED,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "stability_id": self.stability_id,
            "execution_id": self.execution_id,
            "spec": self.spec.to_dict(),
            "status": self.status.value,
            "process_states": {k: v.value for k, v in self.process_states.items()},
            "is_app_reachable": self.is_app_reachable,
            "crashes": [c.to_dict() for c in self.crashes],
            "error_groups": [eg.to_dict() for eg in self.error_groups],
            "total_errors": self.total_errors,
            "recovery_attempts": self.recovery_attempts,
            "recovery_budget_exhausted": self.recovery_budget_exhausted,
            "measurements": [m.to_dict() for m in self.measurements],
            "evidence_ids": list(self.evidence_ids),
            "provenance": dict(self.provenance),
            "timestamps": dict(self.timestamps),
            "notes": list(self.notes),
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> StabilityResult:
        raw_st = data.get("status", StabilityStatus.STABLE.value)
        try:
            st = StabilityStatus(str(raw_st).upper().strip())
        except ValueError:
            st = StabilityStatus.STABLE

        ps_dict: dict[str, ProcessState] = {}
        for k, v in data.get("process_states", {}).items():
            try:
                ps_dict[k] = ProcessState(str(v).upper().strip())
            except ValueError:
                ps_dict[k] = ProcessState.UNKNOWN

        spec_data = data.get("spec", {})
        spec = StabilitySpec.from_dict(spec_data) if isinstance(spec_data, dict) else spec_data

        return cls(
            stability_id=str(data.get("stability_id") or new_stability_evaluation_id()),
            execution_id=str(data.get("execution_id", "")),
            spec=spec,
            status=st,
            process_states=ps_dict,
            is_app_reachable=bool(data.get("is_app_reachable", True)),
            crashes=[CrashEvent.from_dict(c) for c in data.get("crashes", [])],
            error_groups=[ErrorGroup.from_dict(eg) for eg in data.get("error_groups", [])],
            total_errors=int(data.get("total_errors", 0)),
            recovery_attempts=int(data.get("recovery_attempts", 0)),
            recovery_budget_exhausted=bool(data.get("recovery_budget_exhausted", False)),
            measurements=[
                PerformanceMeasurement.from_dict(m) for m in data.get("measurements", [])
            ],
            evidence_ids=list(data.get("evidence_ids", [])),
            provenance=dict(data.get("provenance", {})),
            timestamps=dict(data.get("timestamps", {})),
            notes=list(data.get("notes", [])),
            metadata=dict(data.get("metadata", {})),
        )

    # Invariant guards
    def apply_fix(self, *args: Any, **kwargs: Any) -> None:
        raise TesterBoundaryViolationError(
            action="STABILITY_AUTO_FIX",
            reason="StabilityResult cannot modify application code or apply fixes.",
        )

    def auto_fix(self, *args: Any, **kwargs: Any) -> None:
        raise TesterBoundaryViolationError(
            action="STABILITY_AUTO_FIX",
            reason="StabilityResult cannot modify application code or apply fixes.",
        )

    def create_defect(self, *args: Any, **kwargs: Any) -> None:
        raise TesterBoundaryViolationError(
            action="STABILITY_DEFECT_CREATION",
            reason="StabilityResult does not directly create defects; classification occurs separately.",
        )
