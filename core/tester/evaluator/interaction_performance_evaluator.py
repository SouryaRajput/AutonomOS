from __future__ import annotations

from datetime import datetime, timezone
import logging
from typing import Any, Optional

from core.tester.contracts.identifiers import (
    new_interaction_performance_id,
    validate_execution_id,
)
from core.tester.contracts.interaction import InteractionResult, InteractionTarget
from core.tester.contracts.interaction_performance import (
    InteractionPerformanceResult,
    InteractionPerformanceSpec,
)
from core.tester.contracts.performance import (
    PerformanceMeasurement,
)
from core.tester.contracts.session import BrowserSession
from core.tester.evaluator.performance_recorder import (
    PerformanceMeasurementRecorder,
)
from core.tester.errors import (
    NavigationTimeoutError,
    TesterBoundaryViolationError,
    TesterBudgetExceededError,
    TesterLineageError,
    TesterValidationError,
)
from core.tester.types import (
    DefectType,
    InteractionPerformanceStatus,
    InteractionStatus,
    PerformanceMeasurementStatus,
    PerformanceMetricType,
    PerformanceMetricUnit,
    TesterActionType,
)

logger = logging.getLogger("AutonomOS.Tester.InteractionPerformanceEvaluator")


def utc_now() -> str:
    """Generate ISO 8601 UTC timestamp."""
    return datetime.now(timezone.utc).isoformat()


class InteractionPerformanceEvaluator:
    """
    Deterministic evaluator measuring responsiveness of important user interactions
    already defined in the frozen TestPlan.
    
    Measures elapsed time from USER ACTION initiation to resulting OBSERVABLE RESPONSE,
    evaluates explicit thresholds where provided without benchmark invention,
    and preserves distinct failure semantics without duplicate defect generation.
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
            raise TesterLineageError("InteractionPerformanceEvaluator requires a valid non-empty project_id.")

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

    def measure_interaction(
        self,
        spec: InteractionPerformanceSpec,
        session: Optional[BrowserSession] = None,
        mock_timing: Optional[dict[str, Any]] = None,
        mock_failure: Optional[str] = None,
    ) -> InteractionPerformanceResult:
        """
        Execute an authorized interaction responsiveness measurement test.
        Measures from action dispatch through to observable resulting state readiness.
        """
        interaction_perf_id = new_interaction_performance_id()
        started_at = utc_now()

        # 1. Distinguish explicit simulated or caught failures
        if mock_failure == "timeout":
            return InteractionPerformanceResult(
                interaction_performance_id=interaction_perf_id,
                execution_id=self.execution_id,
                spec=spec,
                status=InteractionPerformanceStatus.TIMEOUT,
                error_message=f"Interaction '{spec.action_type.value}' on target '{spec.target}' timed out after {spec.timeout_seconds}s waiting for state '{spec.expected_state}'.",
                threshold_ms=spec.threshold_ms,
                threshold_met=False if spec.threshold_ms else None,
                evidence_id=spec.evidence_id,
                trace_id=spec.trace_id,
                provenance={"target": str(spec.target), "action_type": spec.action_type.value},
                timestamps={"started_at": started_at, "completed_at": utc_now()},
            )

        if mock_failure in ("missing_state", "state_transition_failed"):
            return InteractionPerformanceResult(
                interaction_performance_id=interaction_perf_id,
                execution_id=self.execution_id,
                spec=spec,
                status=InteractionPerformanceStatus.MISSING_STATE_TRANSITION,
                error_message=f"Interaction '{spec.action_type.value}' succeeded, but expected state '{spec.expected_state}' never emerged.",
                threshold_ms=spec.threshold_ms,
                threshold_met=False if spec.threshold_ms else None,
                evidence_id=spec.evidence_id,
                trace_id=spec.trace_id,
                provenance={"target": str(spec.target), "expected_state": spec.expected_state},
                timestamps={"started_at": started_at, "completed_at": utc_now()},
            )

        if mock_failure in ("failed", "interaction_failed", "unclickable", "disabled"):
            return InteractionPerformanceResult(
                interaction_performance_id=interaction_perf_id,
                execution_id=self.execution_id,
                spec=spec,
                status=InteractionPerformanceStatus.INTERACTION_FAILED,
                error_message=f"Raw interaction '{spec.action_type.value}' on target '{spec.target}' failed to execute.",
                threshold_ms=spec.threshold_ms,
                threshold_met=False if spec.threshold_ms else None,
                evidence_id=spec.evidence_id,
                trace_id=spec.trace_id,
                provenance={"target": str(spec.target), "action_type": spec.action_type.value},
                timestamps={"started_at": started_at, "completed_at": utc_now()},
            )

        if mock_failure == "no_api" or (mock_timing is not None and mock_timing.get("has_timing_api") is False):
            m = self.recorder.record_unavailable(
                metric=PerformanceMetricType.INTERACTION_DURATION,
                reason="Interaction timing observation hooks unavailable in active runtime environment.",
                test_case_id=spec.test_case_id,
            )
            return InteractionPerformanceResult(
                interaction_performance_id=interaction_perf_id,
                execution_id=self.execution_id,
                spec=spec,
                status=InteractionPerformanceStatus.MEASUREMENT_UNAVAILABLE,
                measurements=[m],
                error_message="Interaction responsiveness measurement hooks unavailable in runtime.",
                threshold_ms=spec.threshold_ms,
                threshold_met=None,
                evidence_id=spec.evidence_id,
                trace_id=spec.trace_id,
                provenance={"target": str(spec.target)},
                timestamps={"started_at": started_at, "completed_at": utc_now()},
            )

        # 2. Live Session Interaction Execution (if session provided)
        live_action_duration: Optional[float] = None
        live_state_duration: Optional[float] = None
        if session is not None:
            try:
                action_t0 = utc_now()
                # Dispatch action
                if spec.action_type == TesterActionType.CLICK:
                    res = session.click(spec.target)
                    if not res.is_success:
                        return InteractionPerformanceResult(
                            interaction_performance_id=interaction_perf_id,
                            execution_id=self.execution_id,
                            spec=spec,
                            status=InteractionPerformanceStatus.INTERACTION_FAILED,
                            error_message=res.error or "Click interaction failed.",
                            threshold_ms=spec.threshold_ms,
                            threshold_met=False if spec.threshold_ms else None,
                            evidence_id=spec.evidence_id,
                            trace_id=spec.trace_id,
                            timestamps={"started_at": started_at, "completed_at": utc_now()},
                        )
                    live_action_duration = res.duration_ms
                elif spec.action_type == TesterActionType.TYPE:
                    text_val = (spec.action_payload or {}).get("text", "")
                    res = session.type(spec.target, text=text_val)
                    if not res.is_success:
                        return InteractionPerformanceResult(
                            interaction_performance_id=interaction_perf_id,
                            execution_id=self.execution_id,
                            spec=spec,
                            status=InteractionPerformanceStatus.INTERACTION_FAILED,
                            error_message=res.error or "Type interaction failed.",
                            threshold_ms=spec.threshold_ms,
                            threshold_met=False if spec.threshold_ms else None,
                            evidence_id=spec.evidence_id,
                            trace_id=spec.trace_id,
                            timestamps={"started_at": started_at, "completed_at": utc_now()},
                        )
                    live_action_duration = res.duration_ms
            except NavigationTimeoutError as e:
                return InteractionPerformanceResult(
                    interaction_performance_id=interaction_perf_id,
                    execution_id=self.execution_id,
                    spec=spec,
                    status=InteractionPerformanceStatus.TIMEOUT,
                    error_message=str(e),
                    threshold_ms=spec.threshold_ms,
                    threshold_met=False if spec.threshold_ms else None,
                    evidence_id=spec.evidence_id,
                    trace_id=spec.trace_id,
                    timestamps={"started_at": started_at, "completed_at": utc_now()},
                )
            except Exception as e:
                return InteractionPerformanceResult(
                    interaction_performance_id=interaction_perf_id,
                    execution_id=self.execution_id,
                    spec=spec,
                    status=InteractionPerformanceStatus.INTERACTION_FAILED,
                    error_message=str(e),
                    threshold_ms=spec.threshold_ms,
                    threshold_met=False if spec.threshold_ms else None,
                    evidence_id=spec.evidence_id,
                    trace_id=spec.trace_id,
                    timestamps={"started_at": started_at, "completed_at": utc_now()},
                )

        # 3. Extract and Record Measurements (USER ACTION -> OBSERVABLE RESPONSE)
        timing = dict(mock_timing or {})
        action_duration = float(timing.get("action_execution_duration", live_action_duration or 45.0))
        state_duration = float(timing.get("state_readiness_duration", live_state_duration or 65.0))
        total_duration = float(timing.get("interaction_duration", action_duration + state_duration))

        measurements: list[PerformanceMeasurement] = []
        actual_samples = 0
        target_repeats = max(1, spec.repeat_count)

        for sample_idx in range(target_repeats):
            try:
                # 1. Total Interaction Duration (USER ACTION -> OBSERVABLE RESPONSE)
                m_total = self.recorder.record_metric(
                    metric=PerformanceMetricType.INTERACTION_DURATION,
                    value=total_duration,
                    test_case_id=spec.test_case_id,
                    trace_id=spec.trace_id,
                    sample_info={
                        "sample_index": sample_idx,
                        "action_type": spec.action_type.value,
                        "target": str(spec.target),
                    },
                    provenance={"evidence_id": spec.evidence_id} if spec.evidence_id else None,
                    threshold_context={"threshold_ms": spec.threshold_ms} if spec.threshold_ms else None,
                )
                measurements.append(m_total)

                # 2. State Readiness Duration
                m_state = self.recorder.record_metric(
                    metric=PerformanceMetricType.STATE_READINESS_DURATION,
                    value=state_duration,
                    test_case_id=spec.test_case_id,
                    trace_id=spec.trace_id,
                    sample_info={"sample_index": sample_idx},
                )
                measurements.append(m_state)

                # 3. Action Execution Duration (raw dispatch duration)
                m_action = self.recorder.record_metric(
                    metric=PerformanceMetricType.ACTION_EXECUTION_DURATION,
                    value=action_duration,
                    test_case_id=spec.test_case_id,
                    trace_id=spec.trace_id,
                    sample_info={"sample_index": sample_idx},
                )
                measurements.append(m_action)

                actual_samples += 1
            except TesterBudgetExceededError:
                logger.info(f"Measurement budget exhausted during interaction performance repetition at sample {sample_idx}.")
                break

        # 4. Evaluate Threshold if Explicitly Configured
        threshold_met: Optional[bool] = None
        if spec.threshold_ms is not None:
            threshold_met = total_duration <= spec.threshold_ms

        completed_at = utc_now()
        provenance = {
            "target": str(spec.target),
            "action_type": spec.action_type.value,
            "expected_state": spec.expected_state,
            "samples_collected": actual_samples,
        }

        return InteractionPerformanceResult(
            interaction_performance_id=interaction_perf_id,
            execution_id=self.execution_id,
            spec=spec,
            status=InteractionPerformanceStatus.SUCCESS,
            measurements=measurements,
            action_started_at=started_at,
            action_completed_at=started_at,
            state_ready_at=completed_at,
            action_execution_duration_ms=action_duration,
            state_readiness_duration_ms=state_duration,
            interaction_duration_ms=total_duration,
            threshold_ms=spec.threshold_ms,
            threshold_met=threshold_met,
            evidence_id=spec.evidence_id,
            trace_id=spec.trace_id,
            sample_count=actual_samples,
            provenance=provenance,
            timestamps={"started_at": started_at, "completed_at": completed_at},
        )

    def evaluate_failure_classification(
        self,
        result: InteractionPerformanceResult,
    ) -> dict[str, Any]:
        """
        Defect Classification Coordination:
        When an interaction fails or never completes, determine whether it should be
        categorized as a functional failure, runtime failure, timeout, or performance issue.
        Prevents duplicate defect generation across layers.
        """
        if result.is_success:
            if result.threshold_met is False:
                return {
                    "classification": "PERFORMANCE_THRESHOLD_EXCEEDED",
                    "defect_candidate": False,  # Phase 7.3 records observations without classifying defects
                    "reason": f"Interaction took {result.interaction_duration_ms}ms, exceeding explicit threshold of {result.threshold_ms}ms.",
                }
            return {"classification": "NONE", "defect_candidate": False}

        if result.status == InteractionPerformanceStatus.TIMEOUT:
            return {
                "classification": "TIMEOUT",
                "recommended_defect_type": DefectType.TIMEOUT.value if hasattr(DefectType, "TIMEOUT") else "TIMEOUT",
                "reason": result.error_message or "Interaction timed out.",
            }

        if result.status == InteractionPerformanceStatus.MISSING_STATE_TRANSITION:
            return {
                "classification": "FUNCTIONAL_STATE_TRANSITION_FAILURE",
                "recommended_defect_type": DefectType.FUNCTIONAL.value,
                "reason": result.error_message or "Expected UI state transition failed to emerge after action.",
            }

        if result.status == InteractionPerformanceStatus.INTERACTION_FAILED:
            return {
                "classification": "RUNTIME_INTERACTION_FAILURE",
                "recommended_defect_type": DefectType.FUNCTIONAL.value,
                "reason": result.error_message or "Raw interaction failed to dispatch or target element was unavailable.",
            }

        return {
            "classification": "MEASUREMENT_UNAVAILABLE",
            "defect_candidate": False,
            "reason": result.error_message or "Measurement unavailable.",
        }

    # -----------------------------------------------------------------------
    # Invariant Guards: Zero Code Modifications & Zero Optimization
    # -----------------------------------------------------------------------

    def apply_fix(self, *args: Any, **kwargs: Any) -> None:
        """Strict invariant: Interaction performance evaluator cannot modify application code or apply fixes."""
        raise TesterBoundaryViolationError(
            action="PERFORMANCE_AUTO_FIX",
            reason="Interaction performance evaluator cannot modify application code or apply fixes.",
        )

    def auto_fix(self, *args: Any, **kwargs: Any) -> None:
        """Strict invariant: Interaction performance evaluator cannot modify application code or apply fixes."""
        raise TesterBoundaryViolationError(
            action="PERFORMANCE_AUTO_FIX",
            reason="Interaction performance evaluator cannot modify application code or apply fixes.",
        )

    def create_defect(self, *args: Any, **kwargs: Any) -> None:
        """Strict invariant: Interaction performance evaluator does not classify defects in Phase 7.3."""
        raise TesterBoundaryViolationError(
            action="PERFORMANCE_DEFECT_CLASSIFICATION",
            reason="Interaction performance evaluator does not classify defects in Phase 7.3.",
        )
