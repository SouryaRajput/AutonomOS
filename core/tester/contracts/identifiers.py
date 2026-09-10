from __future__ import annotations

import re
from typing import Optional
import uuid

from core.tester.errors import InvalidTesterIdError

# Canonical Tester domain ID prefixes
WORK_ORDER_ID_PREFIX = "two-"
EXECUTION_ID_PREFIX = "texec-"
TRACE_ID_PREFIX = "ttrace-"
RESULT_ID_PREFIX = "tres-"
DEFECT_ID_PREFIX = "tdef-"
FINDING_ID_PREFIX = "tfind-"
EVIDENCE_ID_PREFIX = "tevid-"
BLOCKER_ID_PREFIX = "tblk-"
TEST_CASE_ID_PREFIX = "ttest-"
RECOMMENDATION_ID_PREFIX = "trec-"
ENVIRONMENT_ID_PREFIX = "tenv-"
RUNTIME_ID_PREFIX = "trun-"
ACTION_ID_PREFIX = "tact-"
SESSION_ID_PREFIX = "tsess-"
PLAN_ID_PREFIX = "tplan-"
STEP_ID_PREFIX = "tstep-"
GAP_ID_PREFIX = "tgap-"
COVERAGE_REPORT_ID_PREFIX = "tcov-"
VALIDATION_REPORT_ID_PREFIX = "tval-"
OBSERVATION_ID_PREFIX = "tobs-"
OCR_ID_PREFIX = "tocr-"
GEOMETRY_ID_PREFIX = "tgeom-"
FRAME_OBSERVATION_ID_PREFIX = "tframe-"
OBSERVATION_SET_ID_PREFIX = "tobset-"
PREFLIGHT_ID_PREFIX = "tpref-"
RUNTIME_EVENT_ID_PREFIX = "trtevt-"
VISUAL_ASSERTION_ID_PREFIX = "tvis-"
SCROLL_EVALUATION_ID_PREFIX = "tscrl-"
RESPONSIVE_EVALUATION_ID_PREFIX = "tresv-"
TYPOGRAPHY_EVALUATION_ID_PREFIX = "ttypo-"
ANIMATION_EVALUATION_ID_PREFIX = "tanim-"
UX_EVALUATION_ID_PREFIX = "tux-"
PERFORMANCE_MEASUREMENT_ID_PREFIX = "tperf-"
LOAD_PERFORMANCE_ID_PREFIX = "tloadp-"
INTERACTION_PERFORMANCE_ID_PREFIX = "tiperf-"
NETWORK_PERFORMANCE_ID_PREFIX = "tnetp-"
NETWORK_REQUEST_ID_PREFIX = "treq-"
STABILITY_EVALUATION_ID_PREFIX = "tstab-"
ERROR_GROUP_ID_PREFIX = "terrgrp-"
CRASH_EVENT_ID_PREFIX = "tcrash-"

ALL_TESTER_PREFIXES = (
    WORK_ORDER_ID_PREFIX,
    EXECUTION_ID_PREFIX,
    TRACE_ID_PREFIX,
    RESULT_ID_PREFIX,
    DEFECT_ID_PREFIX,
    FINDING_ID_PREFIX,
    EVIDENCE_ID_PREFIX,
    BLOCKER_ID_PREFIX,
    TEST_CASE_ID_PREFIX,
    RECOMMENDATION_ID_PREFIX,
    ENVIRONMENT_ID_PREFIX,
    RUNTIME_ID_PREFIX,
    ACTION_ID_PREFIX,
    SESSION_ID_PREFIX,
    PLAN_ID_PREFIX,
    STEP_ID_PREFIX,
    GAP_ID_PREFIX,
    COVERAGE_REPORT_ID_PREFIX,
    VALIDATION_REPORT_ID_PREFIX,
    OBSERVATION_ID_PREFIX,
    OCR_ID_PREFIX,
    GEOMETRY_ID_PREFIX,
    FRAME_OBSERVATION_ID_PREFIX,
    OBSERVATION_SET_ID_PREFIX,
    PREFLIGHT_ID_PREFIX,
    RUNTIME_EVENT_ID_PREFIX,
    VISUAL_ASSERTION_ID_PREFIX,
    SCROLL_EVALUATION_ID_PREFIX,
    RESPONSIVE_EVALUATION_ID_PREFIX,
    TYPOGRAPHY_EVALUATION_ID_PREFIX,
    ANIMATION_EVALUATION_ID_PREFIX,
    UX_EVALUATION_ID_PREFIX,
    PERFORMANCE_MEASUREMENT_ID_PREFIX,
    LOAD_PERFORMANCE_ID_PREFIX,
    INTERACTION_PERFORMANCE_ID_PREFIX,
    NETWORK_PERFORMANCE_ID_PREFIX,
    NETWORK_REQUEST_ID_PREFIX,
    STABILITY_EVALUATION_ID_PREFIX,
    ERROR_GROUP_ID_PREFIX,
    CRASH_EVENT_ID_PREFIX,
)

# Regex pattern: non-empty alphanumeric / hyphen / underscore string
_ID_PATTERN = re.compile(r"^[a-zA-Z0-9_\-]+$")


def new_work_order_id() -> str:
    """Generate unique identifier for a TesterWorkOrder."""
    return f"{WORK_ORDER_ID_PREFIX}{uuid.uuid4().hex[:8]}"


def new_execution_id() -> str:
    """Generate unique identifier for a TesterExecution."""
    return f"{EXECUTION_ID_PREFIX}{uuid.uuid4().hex[:8]}"


def new_trace_id() -> str:
    """Generate unique identifier for a TesterTrace."""
    return f"{TRACE_ID_PREFIX}{uuid.uuid4().hex[:8]}"


def new_result_id() -> str:
    """Generate unique identifier for a TesterResult."""
    return f"{RESULT_ID_PREFIX}{uuid.uuid4().hex[:8]}"


def new_defect_id() -> str:
    """Generate unique identifier for a TesterDefect."""
    return f"{DEFECT_ID_PREFIX}{uuid.uuid4().hex[:8]}"


def new_finding_id() -> str:
    """Generate unique identifier for a TesterFinding."""
    return f"{FINDING_ID_PREFIX}{uuid.uuid4().hex[:8]}"


def new_evidence_id() -> str:
    """Generate unique identifier for a TesterEvidence."""
    return f"{EVIDENCE_ID_PREFIX}{uuid.uuid4().hex[:8]}"


def new_blocker_id() -> str:
    """Generate unique identifier for a TesterBlocker."""
    return f"{BLOCKER_ID_PREFIX}{uuid.uuid4().hex[:8]}"


def new_test_case_id() -> str:
    """Generate unique identifier for a TestCaseResult."""
    return f"{TEST_CASE_ID_PREFIX}{uuid.uuid4().hex[:8]}"


def new_recommendation_id() -> str:
    """Generate unique identifier for a TesterRecommendation."""
    return f"{RECOMMENDATION_ID_PREFIX}{uuid.uuid4().hex[:8]}"


def new_environment_id() -> str:
    """Generate unique identifier for a TestEnvironment."""
    return f"{ENVIRONMENT_ID_PREFIX}{uuid.uuid4().hex[:8]}"


def new_runtime_id() -> str:
    """Generate unique identifier for a TestRuntime."""
    return f"{RUNTIME_ID_PREFIX}{uuid.uuid4().hex[:8]}"


def new_action_id() -> str:
    """Generate unique identifier for a TestActionRecord."""
    return f"{ACTION_ID_PREFIX}{uuid.uuid4().hex[:8]}"


def new_session_id() -> str:
    """Generate unique identifier for a BrowserSession."""
    return f"{SESSION_ID_PREFIX}{uuid.uuid4().hex[:8]}"


def new_plan_id() -> str:
    """Generate unique identifier for a TestPlan."""
    return f"{PLAN_ID_PREFIX}{uuid.uuid4().hex[:8]}"


def new_step_id() -> str:
    """Generate unique identifier for a TestStep."""
    return f"{STEP_ID_PREFIX}{uuid.uuid4().hex[:8]}"


def new_gap_id() -> str:
    """Generate unique identifier for a CoverageGap."""
    return f"{GAP_ID_PREFIX}{uuid.uuid4().hex[:8]}"


def new_coverage_report_id() -> str:
    """Generate unique identifier for a TestCoverageReport."""
    return f"{COVERAGE_REPORT_ID_PREFIX}{uuid.uuid4().hex[:8]}"


def new_validation_report_id() -> str:
    """Generate unique identifier for a TestPlanValidationResult."""
    return f"{VALIDATION_REPORT_ID_PREFIX}{uuid.uuid4().hex[:8]}"


def new_observation_id() -> str:
    """Generate unique identifier for a TesterObservation."""
    return f"{OBSERVATION_ID_PREFIX}{uuid.uuid4().hex[:8]}"


def new_ocr_id() -> str:
    """Generate unique identifier for an OCRResult."""
    return f"{OCR_ID_PREFIX}{uuid.uuid4().hex[:8]}"


def new_geometry_id() -> str:
    """Generate unique identifier for a GeometryObservation."""
    return f"{GEOMETRY_ID_PREFIX}{uuid.uuid4().hex[:8]}"


def new_frame_observation_id() -> str:
    """Generate unique identifier for a VideoFrameObservation."""
    return f"{FRAME_OBSERVATION_ID_PREFIX}{uuid.uuid4().hex[:8]}"


def new_observation_set_id() -> str:
    """Generate unique identifier for an ObservationSet."""
    return f"{OBSERVATION_SET_ID_PREFIX}{uuid.uuid4().hex[:8]}"


def new_preflight_id() -> str:
    """Generate unique identifier for a TestPreflightResult."""
    return f"{PREFLIGHT_ID_PREFIX}{uuid.uuid4().hex[:8]}"


def new_runtime_event_id() -> str:
    """Generate unique identifier for a RuntimeObservation event."""
    return f"{RUNTIME_EVENT_ID_PREFIX}{uuid.uuid4().hex[:8]}"


def new_visual_assertion_id() -> str:
    """Generate unique identifier for a VisualAssertion."""
    return f"{VISUAL_ASSERTION_ID_PREFIX}{uuid.uuid4().hex[:8]}"


def new_scroll_evaluation_id() -> str:
    """Generate unique identifier for a ScrollEvaluation."""
    return f"{SCROLL_EVALUATION_ID_PREFIX}{uuid.uuid4().hex[:8]}"


def new_responsive_evaluation_id() -> str:
    """Generate unique identifier for a ResponsiveEvaluation."""
    return f"{RESPONSIVE_EVALUATION_ID_PREFIX}{uuid.uuid4().hex[:8]}"


def new_typography_evaluation_id() -> str:
    """Generate unique identifier for a TypographyEvaluation."""
    return f"{TYPOGRAPHY_EVALUATION_ID_PREFIX}{uuid.uuid4().hex[:8]}"


def new_animation_evaluation_id() -> str:
    """Generate unique identifier for an AnimationEvaluation."""
    return f"{ANIMATION_EVALUATION_ID_PREFIX}{uuid.uuid4().hex[:8]}"


def new_ux_evaluation_id() -> str:
    """Generate unique identifier for a UXEvaluation."""
    return f"{UX_EVALUATION_ID_PREFIX}{uuid.uuid4().hex[:8]}"


def new_performance_measurement_id() -> str:
    """Generate unique identifier for a PerformanceMeasurement."""
    return f"{PERFORMANCE_MEASUREMENT_ID_PREFIX}{uuid.uuid4().hex[:8]}"


def new_load_performance_id() -> str:
    """Generate unique identifier for a LoadPerformanceResult."""
    return f"{LOAD_PERFORMANCE_ID_PREFIX}{uuid.uuid4().hex[:8]}"


def new_interaction_performance_id() -> str:
    """Generate unique identifier for an InteractionPerformanceResult."""
    return f"{INTERACTION_PERFORMANCE_ID_PREFIX}{uuid.uuid4().hex[:8]}"


def new_network_performance_id() -> str:
    """Generate unique identifier for a NetworkPerformanceResult."""
    return f"{NETWORK_PERFORMANCE_ID_PREFIX}{uuid.uuid4().hex[:8]}"


def new_network_request_id() -> str:
    """Generate unique identifier for a NetworkRequestRecord."""
    return f"{NETWORK_REQUEST_ID_PREFIX}{uuid.uuid4().hex[:8]}"


def new_stability_evaluation_id() -> str:
    """Generate unique identifier for a StabilityResult."""
    return f"{STABILITY_EVALUATION_ID_PREFIX}{uuid.uuid4().hex[:8]}"


def new_error_group_id() -> str:
    """Generate unique identifier for an ErrorGroup."""
    return f"{ERROR_GROUP_ID_PREFIX}{uuid.uuid4().hex[:8]}"


def new_crash_event_id() -> str:
    """Generate unique identifier for a CrashEvent."""
    return f"{CRASH_EVENT_ID_PREFIX}{uuid.uuid4().hex[:8]}"






def is_tester_id(identifier: Optional[str]) -> bool:
    """
    Return True if the given identifier starts with a valid Tester subsystem prefix
    and has a valid format.
    """
    if not identifier or not isinstance(identifier, str):
        return False
    for prefix in ALL_TESTER_PREFIXES:
        if identifier.startswith(prefix) and len(identifier) > len(prefix):
            suffix = identifier[len(prefix):]
            if _ID_PATTERN.match(suffix):
                return True
    return False


def _validate_id(identifier: str, prefix: str, id_type: str) -> None:
    if not isinstance(identifier, str) or not identifier.startswith(prefix) or len(identifier) <= len(prefix):
        raise InvalidTesterIdError(
            identifier_type=id_type,
            identifier_value=str(identifier),
            expected_prefix=prefix,
        )
    suffix = identifier[len(prefix):]
    if not _ID_PATTERN.match(suffix):
        raise InvalidTesterIdError(
            identifier_type=id_type,
            identifier_value=str(identifier),
            expected_prefix=prefix,
        )


def validate_work_order_id(work_order_id: str) -> None:
    """Validate that work_order_id starts with 'two-' and conforms to identifier constraints."""
    _validate_id(work_order_id, WORK_ORDER_ID_PREFIX, "work_order_id")


def validate_execution_id(execution_id: str) -> None:
    """Validate that execution_id starts with 'texec-' and conforms to identifier constraints."""
    _validate_id(execution_id, EXECUTION_ID_PREFIX, "execution_id")


def validate_trace_id(trace_id: str) -> None:
    """Validate that trace_id starts with 'ttrace-' and conforms to identifier constraints."""
    _validate_id(trace_id, TRACE_ID_PREFIX, "trace_id")


def validate_result_id(result_id: str) -> None:
    """Validate that result_id starts with 'tres-' and conforms to identifier constraints."""
    _validate_id(result_id, RESULT_ID_PREFIX, "result_id")


def validate_defect_id(defect_id: str) -> None:
    """Validate that defect_id starts with 'tdef-' and conforms to identifier constraints."""
    _validate_id(defect_id, DEFECT_ID_PREFIX, "defect_id")


def validate_finding_id(finding_id: str) -> None:
    """Validate that finding_id starts with 'tfind-' and conforms to identifier constraints."""
    _validate_id(finding_id, FINDING_ID_PREFIX, "finding_id")


def validate_evidence_id(evidence_id: str) -> None:
    """Validate that evidence_id starts with 'tevid-' and conforms to identifier constraints."""
    _validate_id(evidence_id, EVIDENCE_ID_PREFIX, "evidence_id")


def validate_blocker_id(blocker_id: str) -> None:
    """Validate that blocker_id starts with 'tblk-' and conforms to identifier constraints."""
    _validate_id(blocker_id, BLOCKER_ID_PREFIX, "blocker_id")


def validate_test_case_id(test_id: str) -> None:
    """Validate that test_id starts with 'ttest-' and conforms to identifier constraints."""
    _validate_id(test_id, TEST_CASE_ID_PREFIX, "test_id")


def validate_recommendation_id(recommendation_id: str) -> None:
    """Validate that recommendation_id starts with 'trec-' and conforms to identifier constraints."""
    _validate_id(recommendation_id, RECOMMENDATION_ID_PREFIX, "recommendation_id")


def validate_environment_id(environment_id: str) -> None:
    """Validate that environment_id starts with 'tenv-' and conforms to identifier constraints."""
    _validate_id(environment_id, ENVIRONMENT_ID_PREFIX, "environment_id")


def validate_runtime_id(runtime_id: str) -> None:
    """Validate that runtime_id starts with 'trun-' and conforms to identifier constraints."""
    _validate_id(runtime_id, RUNTIME_ID_PREFIX, "runtime_id")


def validate_action_id(action_id: str) -> None:
    """Validate that action_id starts with 'tact-' and conforms to identifier constraints."""
    _validate_id(action_id, ACTION_ID_PREFIX, "action_id")


def validate_session_id(session_id: str) -> None:
    """Validate that session_id starts with 'tsess-' and conforms to identifier constraints."""
    _validate_id(session_id, SESSION_ID_PREFIX, "session_id")


def validate_plan_id(plan_id: str) -> None:
    """Validate that plan_id starts with 'tplan-' and conforms to identifier constraints."""
    _validate_id(plan_id, PLAN_ID_PREFIX, "plan_id")


def validate_step_id(step_id: str) -> None:
    """Validate that step_id starts with 'tstep-' and conforms to identifier constraints."""
    _validate_id(step_id, STEP_ID_PREFIX, "step_id")


def validate_gap_id(gap_id: str) -> None:
    """Validate that gap_id starts with 'tgap-' and conforms to identifier constraints."""
    _validate_id(gap_id, GAP_ID_PREFIX, "gap_id")


def validate_coverage_report_id(report_id: str) -> None:
    """Validate that report_id starts with 'tcov-' and conforms to identifier constraints."""
    _validate_id(report_id, COVERAGE_REPORT_ID_PREFIX, "report_id")


def validate_validation_report_id(report_id: str) -> None:
    """Validate that report_id starts with 'tval-' and conforms to identifier constraints."""
    _validate_id(report_id, VALIDATION_REPORT_ID_PREFIX, "report_id")


def validate_observation_id(observation_id: str) -> None:
    """Validate that observation_id starts with 'tobs-' and conforms to identifier constraints."""
    _validate_id(observation_id, OBSERVATION_ID_PREFIX, "observation_id")


def validate_ocr_id(ocr_id: str) -> None:
    """Validate that ocr_id starts with 'tocr-' and conforms to identifier constraints."""
    _validate_id(ocr_id, OCR_ID_PREFIX, "ocr_id")


def validate_geometry_id(geometry_id: str) -> None:
    """Validate that geometry_id starts with 'tgeom-' and conforms to identifier constraints."""
    _validate_id(geometry_id, GEOMETRY_ID_PREFIX, "geometry_id")


def validate_frame_observation_id(frame_observation_id: str) -> None:
    """Validate that frame_observation_id starts with 'tframe-' and conforms to identifier constraints."""
    _validate_id(frame_observation_id, FRAME_OBSERVATION_ID_PREFIX, "frame_observation_id")


def validate_observation_set_id(observation_set_id: str) -> None:
    """Validate that observation_set_id starts with 'tobset-' and conforms to identifier constraints."""
    _validate_id(observation_set_id, OBSERVATION_SET_ID_PREFIX, "observation_set_id")


def validate_preflight_id(preflight_id: str) -> None:
    """Validate that preflight_id starts with 'tpref-' and conforms to identifier constraints."""
    _validate_id(preflight_id, PREFLIGHT_ID_PREFIX, "preflight_id")


def validate_runtime_event_id(event_id: str) -> None:
    """Validate that event_id starts with 'trtevt-' and conforms to identifier constraints."""
    _validate_id(event_id, RUNTIME_EVENT_ID_PREFIX, "event_id")


def validate_visual_assertion_id(assertion_id: str) -> None:
    """Validate that assertion_id starts with 'tvis-' and conforms to identifier constraints."""
    _validate_id(assertion_id, VISUAL_ASSERTION_ID_PREFIX, "assertion_id")


def validate_scroll_evaluation_id(evaluation_id: str) -> None:
    """Validate that evaluation_id starts with 'tscrl-' and conforms to identifier constraints."""
    _validate_id(evaluation_id, SCROLL_EVALUATION_ID_PREFIX, "evaluation_id")


def validate_responsive_evaluation_id(evaluation_id: str) -> None:
    """Validate that evaluation_id starts with 'tresv-' and conforms to identifier constraints."""
    _validate_id(evaluation_id, RESPONSIVE_EVALUATION_ID_PREFIX, "evaluation_id")


def validate_typography_evaluation_id(evaluation_id: str) -> None:
    """Validate that evaluation_id starts with 'ttypo-' and conforms to identifier constraints."""
    _validate_id(evaluation_id, TYPOGRAPHY_EVALUATION_ID_PREFIX, "evaluation_id")


def validate_animation_evaluation_id(evaluation_id: str) -> None:
    """Validate that evaluation_id starts with 'tanim-' and conforms to identifier constraints."""
    _validate_id(evaluation_id, ANIMATION_EVALUATION_ID_PREFIX, "evaluation_id")


def validate_ux_evaluation_id(evaluation_id: str) -> None:
    """Validate that evaluation_id starts with 'tux-' and conforms to identifier constraints."""
    _validate_id(evaluation_id, UX_EVALUATION_ID_PREFIX, "evaluation_id")


def validate_performance_measurement_id(measurement_id: str) -> None:
    """Validate that measurement_id starts with 'tperf-' and conforms to identifier constraints."""
    _validate_id(measurement_id, PERFORMANCE_MEASUREMENT_ID_PREFIX, "measurement_id")


def validate_load_performance_id(evaluation_id: str) -> None:
    """Validate that evaluation_id starts with 'tloadp-' and conforms to identifier constraints."""
    _validate_id(evaluation_id, LOAD_PERFORMANCE_ID_PREFIX, "evaluation_id")


def validate_interaction_performance_id(evaluation_id: str) -> None:
    """Validate that evaluation_id starts with 'tiperf-' and conforms to identifier constraints."""
    _validate_id(evaluation_id, INTERACTION_PERFORMANCE_ID_PREFIX, "evaluation_id")


def validate_network_performance_id(evaluation_id: str) -> None:
    """Validate that evaluation_id starts with 'tnetp-' and conforms to identifier constraints."""
    _validate_id(evaluation_id, NETWORK_PERFORMANCE_ID_PREFIX, "evaluation_id")


def validate_network_request_id(request_id: str) -> None:
    """Validate that request_id starts with 'treq-' and conforms to identifier constraints."""
    _validate_id(request_id, NETWORK_REQUEST_ID_PREFIX, "request_id")


def validate_stability_evaluation_id(evaluation_id: str) -> None:
    """Validate that evaluation_id starts with 'tstab-' and conforms to identifier constraints."""
    _validate_id(evaluation_id, STABILITY_EVALUATION_ID_PREFIX, "evaluation_id")


def validate_error_group_id(group_id: str) -> None:
    """Validate that group_id starts with 'terrgrp-' and conforms to identifier constraints."""
    _validate_id(group_id, ERROR_GROUP_ID_PREFIX, "group_id")


def validate_crash_event_id(crash_id: str) -> None:
    """Validate that crash_id starts with 'tcrash-' and conforms to identifier constraints."""
    _validate_id(crash_id, CRASH_EVENT_ID_PREFIX, "crash_id")





