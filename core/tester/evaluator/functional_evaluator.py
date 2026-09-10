from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import logging
import re
from typing import Any, Callable, Optional, Sequence, Union

from core.events.model import Event, new_event_id
from core.events.types import EventSource, EventType
from core.tester.contracts.criteria import AcceptanceCriterion
from core.tester.contracts.execution import TesterExecution
from core.tester.contracts.finding import AcceptanceCriterionResult, TesterEvidence
from core.tester.contracts.identifiers import new_trace_id
from core.tester.contracts.observation import TesterObservation
from core.tester.contracts.plan import TestCase, TestPlan
from core.tester.contracts.runtime_observation import RuntimeObservation
from core.tester.contracts.test_case import TestCaseResult, TestStepResult
from core.tester.contracts.trace import TesterTrace
from core.tester.contracts.work_order import TesterWorkOrder
from core.tester.errors import (
    TesterBoundaryViolationError,
    TesterLineageError,
    TesterValidationError,
)
from core.tester.types import (
    AcceptanceCriterionStatus,
    RuntimeEventSeverity,
    TestCaseStatus,
    TesterActionType,
)

logger = logging.getLogger("AutonomOS.Tester.FunctionalEvaluator")


def utc_now() -> str:
    """Generate ISO 8601 UTC timestamp."""
    return datetime.now(timezone.utc).isoformat()


# Phrases and keywords indicating subjective aesthetic or visual quality judgments
# which are explicitly forbidden from functional acceptance evaluation
SUBJECTIVE_UX_PATTERNS = [
    r"looks?\s+(?:premium|sleek|modern|clean|pretty|beautiful|stunning|ugly|bad|good)",
    r"feels?\s+(?:sleek|smooth|premium|intuitive|great|clunky)",
    r"aesthetics?\s+(?:is|are|look|appeal)",
    r"visually\s+(?:appealing|pleasing|stunning|impressive)",
    r"(?:delightful|great|amazing|poor|sublime)\s+ux",
    r"color\s+palette\s+looks",
]


class FunctionalAcceptanceEvaluator:
    """
    Phase 5.4 Functional Acceptance Evaluator.
    
    Evaluates executed TestCases against explicit expected outcomes and
    WorkOrder acceptance criteria.
    
    Invariants & Execution Rules:
    1. Objective Behavior Only:
       Evaluates objective, testable functional behavior. Subjective visual/UX
       claims are rejected or flagged as NOT_APPLICABLE to functional acceptance.
    2. Sufficient Evidence Guard:
       NEVER marks PASS without sufficient evidence. Unverified state transitions
       or empty evidence collections produce NOT_VERIFIED.
    3. Frozen Plan Adherence:
       Evaluates strictly against existing test cases in the FROZEN TestPlan.
       Never dynamically generates or mutates test cases.
    4. Zero Fixing Guard:
       Never modifies product source or asks Programmer to fix automatically.
    5. Causal Lineage Preservation:
       Enforces execution, work order, and plan identity isolation.
    6. Determinism:
       Identical inputs produce identical evaluation outcomes.
    """
    __test__ = False

    def __init__(
        self,
        execution: Optional[TesterExecution] = None,
        work_order: Optional[TesterWorkOrder] = None,
        test_plan: Optional[TestPlan] = None,
        event_sink: Optional[Callable[[Event], Any]] = None,
    ) -> None:
        self.execution = execution
        self.work_order = work_order
        self.test_plan = test_plan
        self.event_sink = event_sink
        self._subjective_regexes = [re.compile(p, re.IGNORECASE) for p in SUBJECTIVE_UX_PATTERNS]

    def is_subjective_criterion(self, text: str) -> bool:
        """Check whether criterion description represents a subjective aesthetic/UX claim."""
        if not text:
            return False
        for regex in self._subjective_regexes:
            if regex.search(text):
                return True
        return False

    def evaluate(
        self,
        execution: Optional[TesterExecution] = None,
        work_order: Optional[TesterWorkOrder] = None,
        test_plan: Optional[TestPlan] = None,
        test_case_results: Optional[Sequence[TestCaseResult]] = None,
        runtime_observations: Optional[Sequence[RuntimeObservation]] = None,
        observations: Optional[Sequence[TesterObservation]] = None,
        evidence: Optional[Sequence[TesterEvidence]] = None,
    ) -> list[AcceptanceCriterionResult]:
        """
        Evaluate all acceptance criteria authorized by the WorkOrder.
        """
        eff_exec = execution or self.execution
        eff_wo = work_order or self.work_order or (getattr(eff_exec, "work_order", None) if eff_exec else None)
        eff_plan = test_plan or self.test_plan or (getattr(eff_exec, "test_plan", None) if eff_exec else None)

        if eff_wo is None:
            raise TesterValidationError("Cannot evaluate functional acceptance: no TesterWorkOrder provided.")
        if eff_exec is None:
            raise TesterValidationError("Cannot evaluate functional acceptance: no TesterExecution provided.")
        if eff_plan is None:
            raise TesterValidationError("Cannot evaluate functional acceptance: no TestPlan provided.")

        # 1. Enforce Plan Freezing
        if not getattr(eff_plan, "is_frozen", False):
            raise TesterBoundaryViolationError(
                action="EVALUATE_UNFROZEN_PLAN",
                reason=f"Cannot evaluate functional acceptance: TestPlan '{eff_plan.plan_id}' is not FROZEN.",
            )

        # 2. Enforce Lineage & Project Isolation
        if eff_exec.work_order_id != eff_wo.work_order_id:
            raise TesterLineageError(
                f"Lineage mismatch: execution work_order_id '{eff_exec.work_order_id}' != work_order '{eff_wo.work_order_id}'."
            )
        if eff_exec.project_id != eff_wo.project_id:
            raise TesterLineageError(
                f"Project isolation mismatch: execution project_id '{eff_exec.project_id}' != work_order '{eff_wo.project_id}'."
            )
        if eff_plan.execution_id != eff_exec.execution_id:
            raise TesterLineageError(
                f"Lineage mismatch: test_plan execution_id '{eff_plan.execution_id}' != execution '{eff_exec.execution_id}'."
            )
        if eff_plan.project_id != eff_exec.project_id:
            raise TesterLineageError(
                f"Project isolation mismatch: test_plan project_id '{eff_plan.project_id}' != execution '{eff_exec.project_id}'."
            )

        # Gather inputs
        all_tc_results: list[TestCaseResult] = list(test_case_results or eff_exec.test_cases)
        results_by_id = {r.test_id: r for r in all_tc_results}
        all_runtime_obs = list(runtime_observations or getattr(eff_exec, "runtime_observations", []))
        all_obs = list(observations or getattr(eff_exec, "observations", []))

        # Trace creation
        trace_record = eff_exec.create_trace(
            action_type=TesterActionType.EVALUATE_CRITERIA,
            action_details={
                "criteria_count": len(eff_wo.acceptance_criteria),
                "test_cases_count": len(all_tc_results),
            },
        )

        evaluated_results: list[AcceptanceCriterionResult] = []

        for crit in eff_wo.acceptance_criteria:
            crit_res = self._evaluate_criterion(
                criterion=crit,
                test_plan=eff_plan,
                results_by_id=results_by_id,
                all_runtime_obs=all_runtime_obs,
                all_obs=all_obs,
                execution=eff_exec,
                work_order=eff_wo,
                trace_id=trace_record.trace_id,
            )
            evaluated_results.append(crit_res)

            # Record on execution
            if hasattr(eff_exec, "acceptance_results"):
                # Replace existing record for same criterion if present
                eff_exec.acceptance_results = [
                    ar for ar in eff_exec.acceptance_results if ar.criterion_id != crit.criterion_id
                ]
                eff_exec.acceptance_results.append(crit_res)

            # Emit criterion evaluated event
            if self.event_sink is not None:
                evt = Event(
                    event_id=new_event_id(),
                    event_type=EventType.TEST_CRITERION_EVALUATED,
                    timestamp=utc_now(),
                    source=EventSource.WORKER,
                    project_id=eff_exec.project_id,
                    correlation_id=eff_exec.correlation_id,
                    payload=crit_res.to_dict(),
                )
                self.event_sink(evt)

        # Emit acceptance completed event
        if self.event_sink is not None:
            evt_all = Event(
                event_id=new_event_id(),
                event_type=EventType.TEST_ACCEPTANCE_EVALUATED,
                timestamp=utc_now(),
                source=EventSource.WORKER,
                project_id=eff_exec.project_id,
                correlation_id=eff_exec.correlation_id,
                payload={
                    "total": len(evaluated_results),
                    "passed": sum(1 for r in evaluated_results if r.is_pass),
                    "failed": sum(1 for r in evaluated_results if r.is_fail),
                    "blocked": sum(1 for r in evaluated_results if r.is_blocked),
                    "not_verified": sum(1 for r in evaluated_results if r.is_not_verified),
                    "not_applicable": sum(1 for r in evaluated_results if r.is_not_applicable),
                },
            )
            self.event_sink(evt_all)

        return evaluated_results

    def _evaluate_criterion(
        self,
        criterion: AcceptanceCriterion,
        test_plan: TestPlan,
        results_by_id: dict[str, TestCaseResult],
        all_runtime_obs: list[RuntimeObservation],
        all_obs: list[TesterObservation],
        execution: TesterExecution,
        work_order: TesterWorkOrder,
        trace_id: str,
    ) -> AcceptanceCriterionResult:
        """Evaluate a single acceptance criterion."""
        cid = criterion.criterion_id
        desc = criterion.description

        provenance = {
            "execution_id": execution.execution_id,
            "project_id": execution.project_id,
            "work_order_id": work_order.work_order_id,
            "criterion_id": cid,
        }
        trace_data = {"trace_id": trace_id, "action_type": TesterActionType.EVALUATE_CRITERIA.value}

        # 1. Subjective UX / Visual Aesthetics Boundary Check
        if self.is_subjective_criterion(desc):
            return AcceptanceCriterionResult(
                criterion_id=cid,
                description=desc,
                status=AcceptanceCriterionStatus.NOT_APPLICABLE,
                observed_behavior="Subjective aesthetic/UX statement detected.",
                reason="Subjective visual/UX judgments cannot be evaluated as functional acceptance criteria.",
                confidence=1.0,
                provenance=provenance,
                trace=trace_data,
            )

        # 2. Check if criterion category is marked NOT_APPLICABLE
        not_applicable_cats = getattr(test_plan, "not_applicable_categories", [])
        if criterion.category is not None and criterion.category in not_applicable_cats:
            return AcceptanceCriterionResult(
                criterion_id=cid,
                description=desc,
                status=AcceptanceCriterionStatus.NOT_APPLICABLE,
                observed_behavior=f"Category '{criterion.category.value if hasattr(criterion.category, 'value') else criterion.category}' is marked NOT_APPLICABLE.",
                reason="Criterion category is marked NOT_APPLICABLE in frozen TestPlan.",
                confidence=1.0,
                provenance=provenance,
                trace=trace_data,
            )

        # 3. Resolve Linked Test Cases
        matching_tcs: list[TestCase] = []
        for tc in test_plan.test_cases:
            if cid in tc.acceptance_linkage or cid == tc.test_case_id:
                matching_tcs.append(tc)

        # Fallback: link by description match if no explicit linkage was tagged
        if not matching_tcs:
            for tc in test_plan.test_cases:
                if desc.lower() in tc.objective.lower() or tc.objective.lower() in desc.lower():
                    matching_tcs.append(tc)

        if not matching_tcs:
            return AcceptanceCriterionResult(
                criterion_id=cid,
                description=desc,
                status=AcceptanceCriterionStatus.NOT_VERIFIED,
                observed_behavior="No test cases were executed or planned for this criterion.",
                reason="No test case in frozen TestPlan was linked to or covered this acceptance criterion.",
                confidence=1.0,
                provenance=provenance,
                trace=trace_data,
            )

        supporting_tc_ids = [tc.test_case_id for tc in matching_tcs]
        supporting_results: list[TestCaseResult] = [
            results_by_id[tcid] for tcid in supporting_tc_ids if tcid in results_by_id
        ]

        if not supporting_results:
            return AcceptanceCriterionResult(
                criterion_id=cid,
                description=desc,
                status=AcceptanceCriterionStatus.NOT_VERIFIED,
                supporting_test_cases=supporting_tc_ids,
                observed_behavior="Planned test cases were not executed.",
                reason="Supporting test cases in frozen plan have no execution results.",
                confidence=1.0,
                provenance=provenance,
                trace=trace_data,
            )

        # Gather evidence IDs and observations across supporting results
        collected_evidence_ids: list[str] = []
        collected_observations: list[Any] = []

        for r in supporting_results:
            for eid in r.evidence_ids:
                if eid not in collected_evidence_ids:
                    collected_evidence_ids.append(eid)
            for obs in r.observations:
                if obs not in collected_observations:
                    collected_observations.append(obs)

        # 4. Check for Runtime Errors impacting the criterion
        # (e.g. required API returned 500, process crashed)
        runtime_failures = [
            ro for ro in all_runtime_obs
            if ro.is_failure and ro.is_application_owned and (ro.test_case_id in supporting_tc_ids or ro.test_case_id is None)
        ]
        if runtime_failures:
            first_fail = runtime_failures[0]
            for eid in first_fail.evidence_ids:
                if eid not in collected_evidence_ids:
                    collected_evidence_ids.append(eid)
            return AcceptanceCriterionResult(
                criterion_id=cid,
                description=desc,
                status=AcceptanceCriterionStatus.FAIL,
                supporting_test_cases=supporting_tc_ids,
                evidence_ids=collected_evidence_ids,
                observations=collected_observations,
                observed_behavior=f"Runtime failure observed: {first_fail.message}",
                reason=f"Runtime error occurred during verification of this criterion: {first_fail.message}",
                confidence=1.0,
                provenance=provenance,
                trace=trace_data,
            )

        # 5. Check Test Case Statuses
        # Rule: Any BLOCKED -> BLOCKED
        blocked_cases = [r for r in supporting_results if r.status == TestCaseStatus.BLOCKED]
        if blocked_cases:
            first_blocked = blocked_cases[0]
            reason_txt = (first_blocked.failure_information or {}).get("reason", "Supporting test case was blocked.")
            return AcceptanceCriterionResult(
                criterion_id=cid,
                description=desc,
                status=AcceptanceCriterionStatus.BLOCKED,
                supporting_test_cases=supporting_tc_ids,
                evidence_ids=collected_evidence_ids,
                observations=collected_observations,
                observed_behavior=f"Test case '{first_blocked.test_id}' was BLOCKED.",
                reason=f"Verification blocked: {reason_txt}",
                confidence=1.0,
                provenance=provenance,
                trace=trace_data,
            )

        # Rule: Any FAIL -> FAIL
        failed_cases = [r for r in supporting_results if r.status == TestCaseStatus.FAIL]
        if failed_cases:
            first_fail = failed_cases[0]
            reason_txt = (first_fail.failure_information or {}).get(
                "error",
                (first_fail.failure_information or {}).get("reason", first_fail.observed_behavior or "Test step failed expectation.")
            )
            return AcceptanceCriterionResult(
                criterion_id=cid,
                description=desc,
                status=AcceptanceCriterionStatus.FAIL,
                supporting_test_cases=supporting_tc_ids,
                evidence_ids=collected_evidence_ids,
                observations=collected_observations,
                observed_behavior=first_fail.observed_behavior or "Observed behavior failed functional expectation.",
                reason=f"Functional verification failed: {reason_txt}",
                confidence=1.0,
                provenance=provenance,
                trace=trace_data,
            )

        # Rule: Any NOT_VERIFIED or NOT_RUN -> NOT_VERIFIED
        unverified_cases = [
            r for r in supporting_results
            if r.status in (TestCaseStatus.NOT_VERIFIED, TestCaseStatus.NOT_RUN)
        ]
        if unverified_cases:
            return AcceptanceCriterionResult(
                criterion_id=cid,
                description=desc,
                status=AcceptanceCriterionStatus.NOT_VERIFIED,
                supporting_test_cases=supporting_tc_ids,
                evidence_ids=collected_evidence_ids,
                observations=collected_observations,
                observed_behavior="One or more supporting test cases were not fully verified.",
                reason="Supporting test cases could not verify expected resulting state.",
                confidence=0.5,
                provenance=provenance,
                trace=trace_data,
            )

        # Rule: All supporting cases passed -> Check Sufficient Evidence
        passed_cases = [r for r in supporting_results if r.status == TestCaseStatus.PASS]
        if len(passed_cases) == len(supporting_results) and len(supporting_results) > 0:
            # S1: Never mark PASS without sufficient evidence
            # Must have non-empty evidence_ids
            if not collected_evidence_ids:
                return AcceptanceCriterionResult(
                    criterion_id=cid,
                    description=desc,
                    status=AcceptanceCriterionStatus.NOT_VERIFIED,
                    supporting_test_cases=supporting_tc_ids,
                    evidence_ids=[],
                    observations=collected_observations,
                    observed_behavior="Test passed but zero evidence was gathered to prove the outcome.",
                    reason="Never mark PASS without sufficient evidence: supporting test passed but evidence collection is empty.",
                    confidence=0.5,
                    provenance=provenance,
                    trace=trace_data,
                )

            # S2: Check if any step explicitly reports unverified state
            has_unverified_step = False
            for r in passed_cases:
                for step_res in r.step_results:
                    if step_res.metadata.get("state_verified") is False:
                        has_unverified_step = True
                        break
                if has_unverified_step:
                    break

            if has_unverified_step:
                return AcceptanceCriterionResult(
                    criterion_id=cid,
                    description=desc,
                    status=AcceptanceCriterionStatus.NOT_VERIFIED,
                    supporting_test_cases=supporting_tc_ids,
                    evidence_ids=collected_evidence_ids,
                    observations=collected_observations,
                    observed_behavior="Interaction action succeeded but resulting state could not be verified.",
                    reason="Action succeeded but resulting application state cannot be verified (insufficient evidence).",
                    confidence=0.5,
                    provenance=provenance,
                    trace=trace_data,
                )

            # S3: Verified PASS
            return AcceptanceCriterionResult(
                criterion_id=cid,
                description=desc,
                status=AcceptanceCriterionStatus.PASS,
                supporting_test_cases=supporting_tc_ids,
                evidence_ids=collected_evidence_ids,
                observations=collected_observations,
                observed_behavior="Observed behavior strictly satisfies expected acceptance criterion.",
                reason="Observed behavior satisfies authorized functional requirement with verified supporting evidence.",
                confidence=1.0,
                provenance=provenance,
                trace=trace_data,
            )

        # Fallback default
        return AcceptanceCriterionResult(
            criterion_id=cid,
            description=desc,
            status=AcceptanceCriterionStatus.NOT_VERIFIED,
            supporting_test_cases=supporting_tc_ids,
            evidence_ids=collected_evidence_ids,
            observations=collected_observations,
            observed_behavior="Criterion status could not be determined from test outcomes.",
            reason="Inconclusive test execution outcomes.",
            confidence=0.5,
            provenance=provenance,
            trace=trace_data,
        )

    def apply_fix(self, *args, **kwargs) -> Any:
        """Zero fixing guard: Functional evaluation never performs automatic fixes."""
        raise TesterBoundaryViolationError(
            action="AUTOMATIC_FIX",
            reason="FunctionalAcceptanceEvaluator does not modify product or source code.",
        )

    def auto_fix(self, *args, **kwargs) -> Any:
        return self.apply_fix(*args, **kwargs)
