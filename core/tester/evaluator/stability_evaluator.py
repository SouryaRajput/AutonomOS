from __future__ import annotations

from datetime import datetime, timezone
import logging
import re
from typing import Any, Optional, Sequence, Union

from core.tester.contracts.identifiers import (
    new_crash_event_id,
    new_error_group_id,
    new_stability_evaluation_id,
    validate_execution_id,
)
from core.tester.contracts.performance import PerformanceMeasurement
from core.tester.contracts.runtime_observation import RuntimeObservation
from core.tester.contracts.stability import (
    CrashEvent,
    ErrorGroup,
    StabilityResult,
    StabilitySpec,
)
from core.tester.evaluator.performance_recorder import (
    PerformanceMeasurementRecorder,
)
from core.tester.errors import (
    TesterBoundaryViolationError,
    TesterLineageError,
    TesterValidationError,
)
from core.tester.types import (
    DefectSeverity,
    DefectType,
    PerformanceMeasurementStatus,
    PerformanceMetricType,
    PerformanceMetricUnit,
    ProcessState,
    RuntimeEventSeverity,
    RuntimeEventType,
    StabilityFailureType,
    StabilityStatus,
)

logger = logging.getLogger("AutonomOS.Tester.StabilityEvaluator")


def utc_now() -> str:
    """Generate ISO 8601 UTC timestamp."""
    return datetime.now(timezone.utc).isoformat()


class StabilityEvaluator:
    """
    Authoritative evaluator measuring application stability and runtime health.
    Evaluates process state, reachability, crashes, repeated errors, and bounded recovery
    without stress/endurance generation, product code modification, or defect spam.
    """
    __test__ = False

    def __init__(
        self,
        execution_id: str,
        project_id: str,
        work_order_id: Optional[str] = None,
        recorder: Optional[PerformanceMeasurementRecorder] = None,
    ) -> None:
        validate_execution_id(execution_id)
        if not project_id or not str(project_id).strip():
            raise TesterLineageError("StabilityEvaluator requires a valid non-empty project_id.")

        self.execution_id = execution_id
        self.project_id = str(project_id).strip()
        self.work_order_id = work_order_id

        if recorder is not None:
            if recorder.execution_id != execution_id:
                raise TesterLineageError(
                    f"Recorder execution_id '{recorder.execution_id}' does not match evaluator execution_id '{execution_id}'."
                )
            self.recorder = recorder
        else:
            self.recorder = PerformanceMeasurementRecorder(
                execution_id=self.execution_id,
                project_id=self.project_id,
                work_order_id=self.work_order_id,
            )

    def evaluate_stability(
        self,
        spec: StabilitySpec,
        runtime_observations: Sequence[Union[RuntimeObservation, dict[str, Any]]],
        process_states: Optional[dict[str, Union[ProcessState, str]]] = None,
        reachability: bool = True,
        recovery_attempts: int = 0,
        cpu_metrics: Optional[Sequence[dict[str, Any]]] = None,
        memory_metrics: Optional[Sequence[dict[str, Any]]] = None,
        is_tester_infra_failure: bool = False,
        tester_infra_error: Optional[str] = None,
    ) -> StabilityResult:
        """
        Evaluate application stability and runtime health during bounded TestPlan execution.
        Detects crashes, unreachability, process termination, and groups repeated identical errors.
        """
        # Execution isolation check
        if spec.execution_id and spec.execution_id != self.execution_id:
            raise TesterLineageError(
                f"Spec execution_id '{spec.execution_id}' does not match evaluator execution_id '{self.execution_id}'."
            )

        stability_id = new_stability_evaluation_id()
        started_at = utc_now()
        notes: list[str] = []
        crashes: list[CrashEvent] = []
        error_groups_map: dict[str, ErrorGroup] = {}
        all_evidence_ids: list[str] = []

        # 1. Normalize process states
        norm_process_states: dict[str, ProcessState] = {}
        if process_states:
            for proc, st in process_states.items():
                if isinstance(st, str):
                    norm_process_states[proc] = ProcessState.from_str(st)
                elif isinstance(st, ProcessState):
                    norm_process_states[proc] = st
                else:
                    norm_process_states[proc] = ProcessState.UNKNOWN

        # 2. Normalize and evaluate runtime observations
        is_app_reachable = bool(reachability)

        for item in runtime_observations:
            if isinstance(item, RuntimeObservation):
                obs = item
            elif isinstance(item, dict):
                obs = RuntimeObservation.from_dict(item)
            else:
                continue

            # Lineage validation
            if obs.execution_id and obs.execution_id != self.execution_id:
                raise TesterLineageError(
                    f"Runtime observation execution_id '{obs.execution_id}' does not match evaluator execution_id '{self.execution_id}'."
                )

            # Accumulate evidence
            for eid in obs.evidence_ids:
                if eid and eid not in all_evidence_ids:
                    all_evidence_ids.append(eid)

            # Check unreachability
            if obs.event_type == RuntimeEventType.UNREACHABLE:
                is_app_reachable = False
                notes.append(f"Application became unreachable: {obs.message}")

            # Check for crashes and terminations
            if obs.event_type in (
                RuntimeEventType.APPLICATION_CRASH,
                RuntimeEventType.SERVER_TERMINATION,
                RuntimeEventType.BROWSER_CRASH,
            ) or (obs.event_type == RuntimeEventType.PROCESS_ERROR and "crash" in obs.message.lower()):
                if obs.event_type == RuntimeEventType.SERVER_TERMINATION:
                    ft = StabilityFailureType.SERVER_TERMINATION
                    pst = ProcessState.TERMINATED
                elif obs.event_type == RuntimeEventType.BROWSER_CRASH:
                    ft = StabilityFailureType.BROWSER_CRASH
                    pst = ProcessState.CRASHED
                else:
                    ft = StabilityFailureType.APPLICATION_CRASH
                    pst = ProcessState.CRASHED

                crash_event = CrashEvent(
                    crash_id=new_crash_event_id(),
                    message=obs.message,
                    failure_type=ft,
                    affected_test_case_id=obs.test_case_id or spec.test_case_id,
                    process_state=pst,
                    exit_code=obs.metadata.get("exit_code"),
                    stack_trace=obs.trace.get("stack_trace") or obs.metadata.get("stack_trace"),
                    logs=list(obs.metadata.get("logs", [obs.message])),
                    evidence_ids=list(obs.evidence_ids),
                    timestamp=obs.timestamp or utc_now(),
                    execution_id=self.execution_id,
                    provenance={
                        "evaluator": "StabilityEvaluator",
                        "source_event_id": obs.event_id,
                        "project_id": self.project_id,
                    },
                    metadata=dict(obs.metadata),
                )
                crashes.append(crash_event)
                notes.append(f"Detected crash event: {ft.value} - {obs.message}")
                continue

            # Group errors & exceptions (repetition grouping)
            if obs.is_failure or obs.severity in (RuntimeEventSeverity.ERROR, RuntimeEventSeverity.CRITICAL) or (obs.status_code and obs.status_code >= 400):
                sig = self._calculate_error_signature(obs)
                if sig in error_groups_map:
                    error_groups_map[sig].add_occurrence(
                        timestamp=obs.timestamp,
                        test_case_id=obs.test_case_id,
                        evidence_id=obs.evidence_ids[0] if obs.evidence_ids else None,
                    )
                else:
                    affected_res = obs.url or obs.metadata.get("target") or obs.source or None
                    eg = ErrorGroup(
                        group_id=new_error_group_id(),
                        message=obs.message,
                        error_type=obs.event_type,
                        affected_resource=affected_res,
                        count=1,
                        first_occurrence=obs.timestamp or utc_now(),
                        last_occurrence=obs.timestamp or utc_now(),
                        test_cases=[obs.test_case_id] if obs.test_case_id else [],
                        evidence_ids=list(obs.evidence_ids),
                        status_code=obs.status_code,
                        execution_id=self.execution_id,
                        metadata=dict(obs.metadata),
                        provenance={
                            "evaluator": "StabilityEvaluator",
                            "source_event_id": obs.event_id,
                            "project_id": self.project_id,
                        },
                    )
                    error_groups_map[sig] = eg

        # 3. Check process states for crash/termination
        for proc, st in norm_process_states.items():
            if st == ProcessState.CRASHED and not any(c.failure_type in (StabilityFailureType.APPLICATION_CRASH, StabilityFailureType.BROWSER_CRASH) for c in crashes):
                crashes.append(
                    CrashEvent(
                        crash_id=new_crash_event_id(),
                        message=f"Process '{proc}' crashed unexpectedly.",
                        failure_type=StabilityFailureType.APPLICATION_CRASH,
                        affected_test_case_id=spec.test_case_id,
                        process_state=ProcessState.CRASHED,
                        execution_id=self.execution_id,
                        provenance={"evaluator": "StabilityEvaluator", "project_id": self.project_id},
                    )
                )
                notes.append(f"Process '{proc}' observed in CRASHED state.")
            elif st == ProcessState.TERMINATED and not any(c.failure_type == StabilityFailureType.SERVER_TERMINATION for c in crashes):
                crashes.append(
                    CrashEvent(
                        crash_id=new_crash_event_id(),
                        message=f"Process '{proc}' terminated unexpectedly.",
                        failure_type=StabilityFailureType.SERVER_TERMINATION,
                        affected_test_case_id=spec.test_case_id,
                        process_state=ProcessState.TERMINATED,
                        execution_id=self.execution_id,
                        provenance={"evaluator": "StabilityEvaluator", "project_id": self.project_id},
                    )
                )
                notes.append(f"Process '{proc}' observed in TERMINATED state.")

        # 4. Check reachability
        if not is_app_reachable:
            if not any(c.failure_type == StabilityFailureType.UNREACHABLE for c in crashes):
                notes.append("Application reachability check failed.")

        # 5. Bounded recovery evaluation
        effective_recovery_attempts = max(0, recovery_attempts)
        recovery_budget_exhausted = False
        if len(crashes) > 0:
            if spec.max_recovery_attempts > 0 and effective_recovery_attempts >= spec.max_recovery_attempts:
                recovery_budget_exhausted = True
                notes.append(
                    f"Recovery budget exhausted: {effective_recovery_attempts} / {spec.max_recovery_attempts} attempts used."
                )
            elif spec.max_recovery_attempts > 0:
                notes.append(
                    f"Bounded recovery attempted: {effective_recovery_attempts} / {spec.max_recovery_attempts} attempts."
                )

        # 6. Status determination
        if is_tester_infra_failure:
            status = StabilityStatus.CRITICAL_FAILURE
            notes.append(f"Tester infrastructure failure detected: {tester_infra_error or 'Internal harness error'}")
        elif recovery_budget_exhausted:
            status = StabilityStatus.RECOVERY_EXHAUSTED
        elif len(crashes) > 0:
            status = StabilityStatus.CRITICAL_FAILURE
        elif not is_app_reachable:
            status = StabilityStatus.UNSTABLE
        elif len(error_groups_map) > 0:
            # Check for severe repeated errors or unhandled exceptions
            has_unhandled = any(
                eg.error_type in (RuntimeEventType.UNHANDLED_EXCEPTION, "UNHANDLED_EXCEPTION")
                or "exception" in eg.message.lower()
                for eg in error_groups_map.values()
            )
            total_err_count = sum(eg.count for eg in error_groups_map.values())
            if has_unhandled or total_err_count >= 5:
                status = StabilityStatus.DEGRADED
            else:
                status = StabilityStatus.STABLE
        else:
            status = StabilityStatus.STABLE

        # 7. Reliable telemetry recording (CPU / Memory)
        # Strictly record reliable measurements only; never invent fallback data.
        measurements: list[PerformanceMeasurement] = []
        if cpu_metrics is not None:
            for item in cpu_metrics:
                val = item.get("value")
                if val is not None:
                    m = self.recorder.record_metric(
                        metric=PerformanceMetricType.CPU_USAGE,
                        value=float(val),
                        unit=PerformanceMetricUnit.PERCENTAGE,
                        test_case_id=spec.test_case_id,
                        source=item.get("source", "runtime_telemetry"),
                        sample_info=item.get("sample_info"),
                    )
                    measurements.append(m)
                else:
                    m = self.recorder.record_unavailable(
                        metric=PerformanceMetricType.CPU_USAGE,
                        reason=item.get("reason", "CPU telemetry unavailable from runtime"),
                        test_case_id=spec.test_case_id,
                        source=item.get("source", "runtime_telemetry"),
                    )
                    measurements.append(m)

        if memory_metrics is not None:
            for item in memory_metrics:
                val = item.get("value")
                if val is not None:
                    m = self.recorder.record_metric(
                        metric=PerformanceMetricType.MEMORY_USAGE,
                        value=float(val),
                        unit=PerformanceMetricUnit.BYTES,
                        test_case_id=spec.test_case_id,
                        source=item.get("source", "runtime_telemetry"),
                        sample_info=item.get("sample_info"),
                    )
                    measurements.append(m)
                else:
                    m = self.recorder.record_unavailable(
                        metric=PerformanceMetricType.MEMORY_USAGE,
                        reason=item.get("reason", "Memory telemetry unavailable from runtime"),
                        test_case_id=spec.test_case_id,
                        source=item.get("source", "runtime_telemetry"),
                    )
                    measurements.append(m)

        completed_at = utc_now()
        error_groups_list = list(error_groups_map.values())
        total_error_count = sum(eg.count for eg in error_groups_list)

        return StabilityResult(
            stability_id=stability_id,
            execution_id=self.execution_id,
            spec=spec,
            status=status,
            process_states=norm_process_states,
            is_app_reachable=is_app_reachable,
            crashes=crashes,
            error_groups=error_groups_list,
            total_errors=total_error_count,
            recovery_attempts=effective_recovery_attempts,
            recovery_budget_exhausted=recovery_budget_exhausted,
            measurements=measurements,
            evidence_ids=all_evidence_ids,
            provenance={
                "evaluator": "StabilityEvaluator",
                "execution_id": self.execution_id,
                "project_id": self.project_id,
                "work_order_id": self.work_order_id,
                "is_tester_infra_failure": is_tester_infra_failure,
            },
            timestamps={"started_at": started_at, "completed_at": completed_at},
            notes=notes,
            metadata={
                "is_tester_infra_failure": is_tester_infra_failure,
                "tester_infra_error": tester_infra_error,
            },
        )

    def classify_stability_defect(
        self,
        failure_item: Union[CrashEvent, ErrorGroup],
        work_order_id: Optional[str] = None,
        test_case_id: Optional[str] = None,
    ) -> dict[str, Any]:
        """
        Helper classification mapping stability failures into candidate defect records.
        Coordinates with DefectClassifier to ensure application crashes and error groups
        produce well-grounded, deduplicated defects with complete provenance.
        """
        wo_id = work_order_id or self.work_order_id or "two-stability"

        if isinstance(failure_item, CrashEvent):
            return {
                "work_order_id": wo_id,
                "execution_id": self.execution_id,
                "test_case_id": failure_item.affected_test_case_id or test_case_id,
                "title": f"Application Stability Failure: {failure_item.failure_type.value}",
                "description": failure_item.message,
                "severity": DefectSeverity.CRITICAL.value,
                "defect_type": DefectType.RUNTIME.value,
                "expected_behavior": "Application processes remain operational and stable without crashing.",
                "observed_behavior": failure_item.message,
                "reproduction_steps": [f"Execute test workflow leading to {failure_item.failure_type.value}: {failure_item.message}"],
                "evidence_ids": list(failure_item.evidence_ids),
                "affected_area": "Runtime / Stability",
                "is_regression": False,
                "provenance": {
                    "evaluator": "StabilityEvaluator",
                    "crash_id": failure_item.crash_id,
                    "failure_type": failure_item.failure_type.value,
                },
            }
        elif isinstance(failure_item, ErrorGroup):
            # Repeated identical error grouped into 1 defect candidate
            sev = (
                DefectSeverity.HIGH.value
                if failure_item.count >= 5 or (failure_item.status_code and failure_item.status_code >= 500)
                else DefectSeverity.MEDIUM.value
            )
            return {
                "work_order_id": wo_id,
                "execution_id": self.execution_id,
                "test_case_id": failure_item.test_cases[0] if failure_item.test_cases else test_case_id,
                "title": f"Repeated Runtime Error ({failure_item.count}x): {failure_item.message[:60]}",
                "description": f"Identical runtime error occurred {failure_item.count} times. First occurrence: {failure_item.first_occurrence}. Message: {failure_item.message}",
                "severity": sev,
                "defect_type": DefectType.RUNTIME.value,
                "expected_behavior": "Runtime interactions execute cleanly without repeated errors.",
                "observed_behavior": f"Error occurred {failure_item.count} times: {failure_item.message}",
                "reproduction_steps": [f"Trigger repeated requests/actions to: {failure_item.affected_resource or failure_item.message}"],
                "evidence_ids": list(failure_item.evidence_ids),
                "affected_area": failure_item.affected_resource or "Runtime",
                "is_regression": False,
                "occurrence_count": failure_item.count,
                "first_occurrence": failure_item.first_occurrence,
                "provenance": {
                    "evaluator": "StabilityEvaluator",
                    "group_id": failure_item.group_id,
                    "occurrence_count": failure_item.count,
                },
            }
        else:
            raise TesterValidationError(f"Cannot classify stability defect for {type(failure_item).__name__}.")

    def _calculate_error_signature(self, obs: RuntimeObservation) -> str:
        """Calculate deterministic grouping signature for identical errors."""
        et = obs.event_type.value if hasattr(obs.event_type, "value") else str(obs.event_type)
        status = str(obs.status_code) if obs.status_code is not None else "no_status"
        norm_url = str(obs.url or "").strip().lower()

        # Clean variable numbers/UUIDs from message to match identical root causes
        clean_msg = re.sub(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", "<uuid>", obs.message.lower())
        clean_msg = re.sub(r"\b\d{10,}\b", "<timestamp>", clean_msg)
        clean_msg = re.sub(r"\s+", " ", clean_msg).strip()

        return f"{et}|{status}|{norm_url}|{clean_msg}"

    # Invariant guards
    def apply_fix(self, *args: Any, **kwargs: Any) -> None:
        raise TesterBoundaryViolationError(
            action="STABILITY_AUTO_FIX",
            reason="StabilityEvaluator cannot modify application code or apply fixes.",
        )

    def auto_fix(self, *args: Any, **kwargs: Any) -> None:
        raise TesterBoundaryViolationError(
            action="STABILITY_AUTO_FIX",
            reason="StabilityEvaluator cannot modify application code or apply fixes.",
        )

    def create_defect(self, *args: Any, **kwargs: Any) -> None:
        raise TesterBoundaryViolationError(
            action="STABILITY_DEFECT_CLASSIFICATION",
            reason="StabilityEvaluator does not directly create defects; classification occurs separately.",
        )
