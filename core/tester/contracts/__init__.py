from __future__ import annotations

from core.tester.contracts.blocker import TesterBlocker
from core.tester.contracts.boundary import (
    TESTER_ALLOWED_CAPABILITIES,
    TESTER_FORBIDDEN_ACTIONS,
    TesterBoundaryGuard,
)
from core.tester.contracts.criteria import AcceptanceCriterion
from core.tester.contracts.environment import TestEnvironment
from core.tester.contracts.execution import TesterExecution
from core.tester.contracts.finding import (
    AcceptanceCriterionResult,
    TesterDefect,
    TesterEvidence,
    TesterFinding,
)
from core.tester.contracts.identifiers import (
    ALL_TESTER_PREFIXES,
    BLOCKER_ID_PREFIX,
    DEFECT_ID_PREFIX,
    EVIDENCE_ID_PREFIX,
    EXECUTION_ID_PREFIX,
    FINDING_ID_PREFIX,
    RECOMMENDATION_ID_PREFIX,
    RESULT_ID_PREFIX,
    TEST_CASE_ID_PREFIX,
    TRACE_ID_PREFIX,
    WORK_ORDER_ID_PREFIX,
    is_tester_id,
    new_blocker_id,
    new_defect_id,
    new_evidence_id,
    new_execution_id,
    new_finding_id,
    new_recommendation_id,
    new_result_id,
    new_test_case_id,
    new_trace_id,
    new_work_order_id,
    validate_blocker_id,
    validate_defect_id,
    validate_evidence_id,
    validate_execution_id,
    validate_finding_id,
    validate_recommendation_id,
    validate_result_id,
    validate_test_case_id,
    validate_trace_id,
    validate_work_order_id,
)
from core.tester.contracts.lifecycle import TesterLifecycle
from core.tester.contracts.manager_bridge import (
    FakeTesterWorker,
    TesterManagerBridge,
)
from core.tester.contracts.quality_summary import QualitySummary
from core.tester.contracts.recommendation import TesterRecommendation
from core.tester.contracts.result import TesterResult
from core.tester.contracts.result_validator import TesterResultValidator
from core.tester.contracts.scope import TestScope
from core.tester.contracts.test_case import TestCaseResult
from core.tester.contracts.thresholds import QualityThresholds
from core.tester.contracts.trace import TesterTrace
from core.tester.contracts.validator import TesterWorkOrderValidator
from core.tester.contracts.work_order import TesterWorkOrder

__all__ = [
    "AcceptanceCriterion",
    "AcceptanceCriterionResult",
    "FakeTesterWorker",
    "QualitySummary",
    "QualityThresholds",
    "TestCaseResult",
    "TestEnvironment",
    "TestScope",
    "TesterBlocker",
    "TesterBoundaryGuard",
    "TESTER_ALLOWED_CAPABILITIES",
    "TESTER_FORBIDDEN_ACTIONS",
    "TesterDefect",
    "TesterEvidence",
    "TesterExecution",
    "TesterFinding",
    "TesterLifecycle",
    "TesterManagerBridge",
    "TesterRecommendation",
    "TesterResult",
    "TesterResultValidator",
    "TesterTrace",
    "TesterWorkOrder",
    "TesterWorkOrderValidator",
    "ALL_TESTER_PREFIXES",
    "BLOCKER_ID_PREFIX",
    "DEFECT_ID_PREFIX",
    "EVIDENCE_ID_PREFIX",
    "EXECUTION_ID_PREFIX",
    "FINDING_ID_PREFIX",
    "RECOMMENDATION_ID_PREFIX",
    "RESULT_ID_PREFIX",
    "TEST_CASE_ID_PREFIX",
    "TRACE_ID_PREFIX",
    "WORK_ORDER_ID_PREFIX",
    "is_tester_id",
    "new_blocker_id",
    "new_defect_id",
    "new_evidence_id",
    "new_execution_id",
    "new_finding_id",
    "new_recommendation_id",
    "new_result_id",
    "new_test_case_id",
    "new_trace_id",
    "new_work_order_id",
    "validate_blocker_id",
    "validate_defect_id",
    "validate_evidence_id",
    "validate_execution_id",
    "validate_finding_id",
    "validate_recommendation_id",
    "validate_result_id",
    "validate_test_case_id",
    "validate_trace_id",
    "validate_work_order_id",
]
