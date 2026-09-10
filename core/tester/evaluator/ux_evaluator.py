from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import logging
import re
from typing import Any, Callable, Optional, Sequence, Union

from core.tester.contracts.finding import (
    TesterDefect,
    TesterEvidence,
    TesterFinding,
)
from core.tester.contracts.identifiers import (
    new_defect_id,
    new_evidence_id,
    new_finding_id,
    new_ux_evaluation_id,
    validate_execution_id,
)
from core.tester.contracts.test_case import TestCaseResult
from core.tester.contracts.ux import (
    UXAssertion,
    UXAssertionResult,
    UXEvaluationResult,
    UXFlowExpectation,
)
from core.tester.errors import (
    TesterBoundaryViolationError,
    TesterLineageError,
    TesterValidationError,
)
from core.tester.types import (
    DefectSeverity,
    DefectType,
    FindingCategory,
    TestSurface,
    UXCheckType,
    UXEvaluationStatus,
)

logger = logging.getLogger("AutonomOS.Tester.UXEvaluator")


def utc_now() -> str:
    """Generate ISO 8601 UTC timestamp."""
    return datetime.now(timezone.utc).isoformat()


# Anti-noise budget: maximum findings/recommendations generated per flow evaluation
MAX_UX_FINDINGS_PER_FLOW = 5

# Subjective aesthetic patterns that must be DISCARDED (never defects or findings)
SUBJECTIVE_AESTHETIC_PATTERNS = [
    r"i\s+personally\s+(?:dislike|hate|prefer|like)",
    r"i\s+(?:dislike|prefer|feel)\s+(?:this|the)\s+(?:layout|design|color|theme)",
    r"fewer\s+clicks\s+(?:are\s+always|is\s+always)\s+better",
    r"animations?\s+(?:are\s+always|is\s+always)\s+desirable",
    r"a\s+familiar\s+design\s+pattern\s+is\s+required",
    r"(?:looks?|feels?)\s+(?:ugly|cheap|boring|not\s+modern|uninspired)",
    r"color\s+(?:is\s+bad|should\s+be\s+different|looks\s+off)",
]

# Vague non-actionable suggestions that lack concrete guidance and must be DISCARDED
VAGUE_SUGGESTION_PATTERNS = [
    r"^the\s+ux\s+could\s+(?:potentially\s+)?be\s+(?:better|improved|enhanced)\.?$",
    r"^make\s+(?:the\s+)?ui\s+(?:cleaner|nicer|better)\.?$",
    r"^improve\s+(?:the\s+)?usability\.?$",
    r"^rethink\s+(?:the\s+)?design\.?$",
]


class UXFlowEvaluator:
    """
    Phase 6.6: Deterministic UX Flow & Usability Evaluation Engine.

    Evaluates measurable, actionable usability barriers encountered during authorized execution.

    Invariants:
    1. Actionability Filter: Only concrete, actionable observations survive.
       - Subjective opinions ('I personally dislike this layout') -> DISCARDED.
       - Vague critiques ('UX could be better') -> DISCARDED.
       - Actionable suggestions ('Onboarding could be shorter') -> RECOMMENDATION finding.
       - Concrete barriers ('Continue button is inaccessible') -> DEFECT.
    2. Zero Design Speculation: Does not assume designer intent or mandate arbitrary layouts/colors.
    3. Grounded in Explicit TestCase Objectives: Evaluates task completion against explicit requirements.
    4. Anti-Infinite Critique Guard: Caps findings per flow to prevent endless aesthetic nitpicking.
    5. Zero Automatic Fixing: Does not modify product source or execute fixes.
    6. Strict Provenance & Lineage: Preserves execution_id and project_id isolation.
    """
    __test__ = False

    def __init__(
        self,
        execution_id: Optional[str] = None,
        project_id: Optional[str] = None,
        work_order_id: Optional[str] = None,
        max_findings_per_flow: int = MAX_UX_FINDINGS_PER_FLOW,
    ) -> None:
        self.execution_id = execution_id
        self.project_id = project_id
        self.work_order_id = work_order_id
        self.max_findings_per_flow = max(1, int(max_findings_per_flow))
        self._total_findings_count = 0

    # ---------------------------------------------------------------------------
    # Actionability & Filter Utilities
    # ---------------------------------------------------------------------------

    def is_subjective_preference(self, text: str) -> bool:
        """Check if critique text represents subjective opinion or aesthetic preference."""
        t_lower = text.strip().lower()
        return any(re.search(pat, t_lower) for pat in SUBJECTIVE_AESTHETIC_PATTERNS)

    def is_vague_critique(self, text: str) -> bool:
        """Check if critique text lacks concrete actionable guidance."""
        t_lower = text.strip().lower()
        if len(t_lower) < 15 and ("ux" in t_lower or "ui" in t_lower):
            return True
        return any(re.search(pat, t_lower) for pat in VAGUE_SUGGESTION_PATTERNS)

    # ---------------------------------------------------------------------------
    # Concrete UX Flow Evaluation
    # ---------------------------------------------------------------------------

    def evaluate_flow(
        self,
        expectation: UXFlowExpectation,
        test_case_result: Optional[TestCaseResult] = None,
        step_results: Optional[Sequence[Any]] = None,
        runtime_failures: Optional[Sequence[str]] = None,
        missing_feedback: bool = False,
        inaccessible_controls: Optional[Sequence[str]] = None,
        unrecoverable_error: Optional[str] = None,
        source_evidence_ids: Optional[Sequence[str]] = None,
        test_case_id: Optional[str] = None,
    ) -> UXAssertionResult:
        """
        Evaluate a single user task or flow against explicit expectations.
        """
        assertion_id = new_ux_evaluation_id()
        flow_name = expectation.flow_name or "user_flow"
        ev_ids = list(source_evidence_ids or [])
        if not ev_ids:
            ev_ids = [new_evidence_id()]

        # Check 1: Inaccessible Control (e.g. Continue button inaccessible)
        if inaccessible_controls:
            ctrl_list = ", ".join(inaccessible_controls)
            defect = self._create_ux_defect(
                title=f"Inaccessible Control in '{flow_name}': {ctrl_list}",
                description=(
                    f"The '{flow_name}' task cannot be completed because required control(s) [{ctrl_list}] "
                    f"are inaccessible or not interactable."
                ),
                severity=DefectSeverity.HIGH,
                check_type=UXCheckType.INACCESSIBLE_CONTROL,
                expected_behavior=f"Required control(s) [{ctrl_list}] should be accessible and interactive.",
                observed_behavior=f"Control(s) [{ctrl_list}] could not be reached or activated.",
                reproduction_steps=[
                    f"Start workflow '{flow_name}'.",
                    f"Attempt to interact with control [{ctrl_list}].",
                    "Observe interaction cannot be performed.",
                ],
                affected_components=list(inaccessible_controls),
                test_case_id=test_case_id,
                evidence_ids=ev_ids,
            )
            return UXAssertionResult(
                assertion_id=assertion_id,
                check_type=UXCheckType.INACCESSIBLE_CONTROL,
                status=UXEvaluationStatus.FAIL,
                target_flow=flow_name,
                target_element_id=inaccessible_controls[0] if inaccessible_controls else None,
                description=f"Inaccessible control(s) [{ctrl_list}] prevented task completion.",
                defect=defect,
                evidence_ids=ev_ids,
                test_case_id=test_case_id,
            )

        # Check 2: Unrecoverable Error State
        if unrecoverable_error:
            defect = self._create_ux_defect(
                title=f"Unrecoverable Error in '{flow_name}'",
                description=(
                    f"Workflow '{flow_name}' entered an error state ('{unrecoverable_error}') "
                    "with no available recovery action or path back to valid state."
                ),
                severity=DefectSeverity.HIGH,
                check_type=UXCheckType.UNRECOVERABLE_ERROR,
                expected_behavior="Application error states should provide clear recovery actions or back navigation.",
                observed_behavior=f"Error state occurred ('{unrecoverable_error}') with no recovery path.",
                reproduction_steps=[
                    f"Execute workflow '{flow_name}'.",
                    f"Trigger condition leading to error: '{unrecoverable_error}'.",
                    "Observe absence of recovery controls (e.g. retry, cancel, or back).",
                ],
                affected_components=[flow_name],
                test_case_id=test_case_id,
                evidence_ids=ev_ids,
            )
            return UXAssertionResult(
                assertion_id=assertion_id,
                check_type=UXCheckType.UNRECOVERABLE_ERROR,
                status=UXEvaluationStatus.FAIL,
                target_flow=flow_name,
                description=f"Unrecoverable error state: {unrecoverable_error}",
                defect=defect,
                evidence_ids=ev_ids,
                test_case_id=test_case_id,
            )

        # Check 3: Blocked / Failed Flow via TestCaseResult
        if test_case_result is not None and test_case_result.is_fail:
            obj_desc = expectation.objective or (test_case_result.objective if hasattr(test_case_result, "objective") else "Task objective")
            fail_reason = test_case_result.error_message or "Execution steps failed"
            check_type = UXCheckType.ONBOARDING_FAILURE if "onboarding" in flow_name.lower() else UXCheckType.REQUIRED_ACTION_BLOCKED

            defect = self._create_ux_defect(
                title=f"Flow Failure: '{flow_name}' could not be completed",
                description=(
                    f"Objective '{obj_desc}' failed during execution: {fail_reason}."
                ),
                severity=DefectSeverity.HIGH,
                check_type=check_type,
                expected_behavior=f"User should successfully complete flow '{flow_name}' to satisfy: '{obj_desc}'.",
                observed_behavior=f"Flow failed with error: {fail_reason}.",
                reproduction_steps=[
                    f"Attempt flow '{flow_name}'.",
                    f"Observe failure: {fail_reason}.",
                ],
                affected_components=[flow_name],
                test_case_id=test_case_id,
                evidence_ids=ev_ids,
            )
            return UXAssertionResult(
                assertion_id=assertion_id,
                check_type=check_type,
                status=UXEvaluationStatus.FAIL,
                target_flow=flow_name,
                description=f"Flow '{flow_name}' failed to achieve objective: {fail_reason}.",
                defect=defect,
                evidence_ids=ev_ids,
                test_case_id=test_case_id,
            )

        # Check 4: Missing Critical Feedback
        if missing_feedback:
            defect = self._create_ux_defect(
                title=f"Missing Critical Feedback in '{flow_name}'",
                description=(
                    f"Action was executed in '{flow_name}', but important feedback "
                    f"(expected '{expectation.expected_feedback or 'confirmation / progress'}') was absent, "
                    "leaving the user without confirmation of whether the action succeeded."
                ),
                severity=DefectSeverity.MEDIUM,
                check_type=UXCheckType.MISSING_FEEDBACK,
                expected_behavior=f"Action in '{flow_name}' should provide clear visual feedback: '{expectation.expected_feedback or 'status indicator'}'.",
                observed_behavior="Action completed with zero visual feedback or status change.",
                reproduction_steps=[
                    f"Perform action in '{flow_name}'.",
                    "Observe absence of confirmation or status feedback.",
                ],
                affected_components=[flow_name],
                test_case_id=test_case_id,
                evidence_ids=ev_ids,
            )
            return UXAssertionResult(
                assertion_id=assertion_id,
                check_type=UXCheckType.MISSING_FEEDBACK,
                status=UXEvaluationStatus.FAIL,
                target_flow=flow_name,
                description=f"Missing expected feedback for action in '{flow_name}'.",
                defect=defect,
                evidence_ids=ev_ids,
                test_case_id=test_case_id,
            )

        # Check 5: Clean Pass
        return UXAssertionResult(
            assertion_id=assertion_id,
            check_type=UXCheckType.REQUIRED_ACTION_BLOCKED,
            status=UXEvaluationStatus.PASS,
            target_flow=flow_name,
            description=f"Flow '{flow_name}' completed successfully satisfying objective: '{expectation.objective or 'completed'}'.",
            evidence_ids=ev_ids,
            test_case_id=test_case_id,
        )

    # ---------------------------------------------------------------------------
    # Actionable UX Suggestion Evaluator (Recommendation vs Discard)
    # ---------------------------------------------------------------------------

    def evaluate_suggestion(
        self,
        suggestion_text: str,
        target_flow: str = "general_ux",
        test_case_id: Optional[str] = None,
        source_evidence_ids: Optional[Sequence[str]] = None,
    ) -> UXAssertionResult:
        """
        Evaluate a candidate UX observation or suggestion through the actionability filter.
        """
        assertion_id = new_ux_evaluation_id()
        ev_ids = list(source_evidence_ids or [])
        if not ev_ids:
            ev_ids = [new_evidence_id()]

        # 1. Subjective aesthetic filter -> DISCARD
        if self.is_subjective_preference(suggestion_text):
            return UXAssertionResult(
                assertion_id=assertion_id,
                check_type=UXCheckType.SUBJECTIVE_PREFERENCE,
                status=UXEvaluationStatus.DISCARDED,
                target_flow=target_flow,
                description=f"Discarded subjective opinion: '{suggestion_text}'. Not an actionable UX defect.",
                evidence_ids=ev_ids,
                test_case_id=test_case_id,
            )

        # 2. Vague critique filter -> DISCARD
        if self.is_vague_critique(suggestion_text):
            return UXAssertionResult(
                assertion_id=assertion_id,
                check_type=UXCheckType.VAGUE_CRITIQUE,
                status=UXEvaluationStatus.DISCARDED,
                target_flow=target_flow,
                description=f"Discarded vague critique without concrete guidance: '{suggestion_text}'.",
                evidence_ids=ev_ids,
                test_case_id=test_case_id,
            )

        # 3. Actionable recommendation -> RECOMMENDATION (Finding, NOT defect)
        rec = TesterFinding(
            finding_id=new_finding_id(),
            category=FindingCategory.RECOMMENDATION,
            title=f"UX Suggestion for '{target_flow}'",
            description=suggestion_text,
            affected_area=target_flow,
            confidence=0.8,
            recommendation=suggestion_text,
            evidence_ids=ev_ids,
            metadata={
                "execution_id": self.execution_id or "texec-default",
                "evaluator": "UXFlowEvaluator",
                "phase": "Phase 6.6",
                "is_actionable_recommendation": True,
            },
        )
        return UXAssertionResult(
            assertion_id=assertion_id,
            check_type=UXCheckType.UNEXPECTED_STEP,
            status=UXEvaluationStatus.PASS,
            target_flow=target_flow,
            description=f"Actionable suggestion recorded as recommendation: '{suggestion_text}'.",
            recommendation=rec,
            evidence_ids=ev_ids,
            test_case_id=test_case_id,
        )

    # ---------------------------------------------------------------------------
    # Assertion Evaluator
    # ---------------------------------------------------------------------------

    def evaluate_assertion(
        self,
        assertion: UXAssertion,
        test_case_result: Optional[TestCaseResult] = None,
        source_evidence_ids: Optional[Sequence[str]] = None,
    ) -> UXAssertionResult:
        """Evaluate a single UX assertion."""
        assertion_id = assertion.assertion_id or new_ux_evaluation_id()
        flow_name = assertion.target_flow or assertion.flow_name or "user_flow"
        ev_ids = list(assertion.evidence_ids) if assertion.evidence_ids else list(source_evidence_ids or [])
        if not ev_ids:
            ev_ids = [new_evidence_id()]

        # 1. Subjective aesthetic filter -> DISCARD
        if (
            assertion.is_subjective_preference
            or assertion.check_type == UXCheckType.SUBJECTIVE_PREFERENCE
            or self.is_subjective_preference(assertion.description)
        ):
            return UXAssertionResult(
                assertion_id=assertion_id,
                check_type=UXCheckType.SUBJECTIVE_PREFERENCE,
                status=UXEvaluationStatus.DISCARDED,
                target_flow=flow_name,
                description=f"Discarded subjective opinion: '{assertion.description}'. Not an actionable UX defect.",
                evidence_ids=ev_ids,
                test_case_id=assertion.test_case_id,
            )

        # 2. Vague critique filter -> DISCARD
        if (
            assertion.is_vague_critique
            or assertion.check_type == UXCheckType.VAGUE_CRITIQUE
            or self.is_vague_critique(assertion.description)
        ):
            return UXAssertionResult(
                assertion_id=assertion_id,
                check_type=UXCheckType.VAGUE_CRITIQUE,
                status=UXEvaluationStatus.DISCARDED,
                target_flow=flow_name,
                description=f"Discarded vague critique without concrete guidance: '{assertion.description}'.",
                evidence_ids=ev_ids,
                test_case_id=assertion.test_case_id,
            )

        # 3. Check for explicit expectations in assertion.expectations
        failed_expectations = [e for e in assertion.expectations if not e.is_completed]
        if failed_expectations:
            failed_exp = failed_expectations[0]
            reason = failed_exp.blocker_reason or failed_exp.actual_outcome or "Step failed"
            step_name = failed_exp.step_name or flow_name

            if assertion.check_type == UXCheckType.INACCESSIBLE_CONTROL:
                title = f"Inaccessible UI control in '{flow_name}': {step_name}"
                desc = f"Inaccessible UI control in '{flow_name}': {failed_exp.actual_outcome or reason}"
                defect_type = UXCheckType.INACCESSIBLE_CONTROL
                severity = DefectSeverity.HIGH
            elif assertion.check_type == UXCheckType.MISSING_FEEDBACK:
                title = f"Missing user feedback in '{flow_name}': {step_name}"
                desc = f"Missing user feedback in '{flow_name}': {failed_exp.actual_outcome or reason}"
                defect_type = UXCheckType.MISSING_FEEDBACK
                severity = DefectSeverity.MEDIUM
            elif assertion.check_type == UXCheckType.UNRECOVERABLE_ERROR:
                title = f"Unrecoverable error in '{flow_name}': {step_name}"
                desc = f"Unrecoverable error state in '{flow_name}': {failed_exp.actual_outcome or reason}"
                defect_type = UXCheckType.UNRECOVERABLE_ERROR
                severity = DefectSeverity.HIGH
            elif assertion.check_type == UXCheckType.ONBOARDING_FAILURE:
                title = f"Onboarding failure in '{flow_name}': {step_name}"
                desc = f"Onboarding failure in '{flow_name}': {failed_exp.actual_outcome or reason}"
                defect_type = UXCheckType.ONBOARDING_FAILURE
                severity = DefectSeverity.HIGH
            else:
                title = f"Blocked UX flow in '{flow_name}': {step_name}"
                desc = f"Blocked UX flow in '{flow_name}': {step_name} could not proceed. Reason: {reason}"
                defect_type = UXCheckType.REQUIRED_ACTION_BLOCKED
                severity = DefectSeverity.HIGH

            defect = self._create_ux_defect(
                title=title,
                description=desc,
                severity=severity,
                check_type=defect_type,
                expected_behavior=failed_exp.expected_outcome or f"Successfully complete {step_name}",
                observed_behavior=failed_exp.actual_outcome or reason,
                reproduction_steps=[
                    f"Start user flow '{flow_name}'.",
                    f"Perform step '{step_name}'.",
                    f"Observe failure: {reason}",
                ],
                affected_components=[flow_name],
                test_case_id=assertion.test_case_id,
                evidence_ids=ev_ids,
                execution_id=assertion.execution_id or self.execution_id,
            )
            return UXAssertionResult(
                assertion_id=assertion_id,
                check_type=defect_type,
                status=UXEvaluationStatus.FAIL,
                target_flow=flow_name,
                description=desc,
                defect=defect,
                evidence_ids=ev_ids,
                test_case_id=assertion.test_case_id,
            )

        # 4. Actionable recommendation (suggestion)
        if assertion.actionable_suggestion:
            rec = TesterFinding(
                finding_id=new_finding_id(),
                category=FindingCategory.RECOMMENDATION,
                title=f"UX Suggestion for '{flow_name}'",
                description=assertion.actionable_suggestion,
                affected_area=flow_name,
                confidence=0.8,
                recommendation=assertion.actionable_suggestion,
                evidence_ids=ev_ids,
                metadata={
                    "execution_id": assertion.execution_id or self.execution_id or "texec-default",
                    "evaluator": "UXFlowEvaluator",
                    "phase": "Phase 6.6",
                    "is_actionable_recommendation": True,
                },
            )
            return UXAssertionResult(
                assertion_id=assertion_id,
                check_type=assertion.check_type,
                status=UXEvaluationStatus.PASS,
                target_flow=flow_name,
                description=f"Actionable suggestion recorded as recommendation: '{assertion.actionable_suggestion}'.",
                recommendation=rec,
                evidence_ids=ev_ids,
                test_case_id=assertion.test_case_id,
            )

        # 5. If expectations list given and all completed
        if assertion.expectations and all(e.is_completed for e in assertion.expectations):
            return UXAssertionResult(
                assertion_id=assertion_id,
                check_type=assertion.check_type,
                status=UXEvaluationStatus.PASS,
                target_flow=flow_name,
                description=f"Flow '{flow_name}' completed successfully with all {len(assertion.expectations)} steps satisfied.",
                evidence_ids=ev_ids,
                test_case_id=assertion.test_case_id,
            )

        # 6. Fallback to evaluate_flow
        return self.evaluate_flow(
            expectation=assertion.expectation,
            test_case_result=test_case_result,
            inaccessible_controls=assertion.metadata.get("inaccessible_controls"),
            missing_feedback=bool(assertion.metadata.get("missing_feedback", False)),
            unrecoverable_error=assertion.metadata.get("unrecoverable_error"),
            source_evidence_ids=ev_ids,
            test_case_id=assertion.test_case_id,
        )

    # ---------------------------------------------------------------------------
    # Aggregate UX Evaluation Runner
    # ---------------------------------------------------------------------------

    def evaluate_ux(
        self,
        assertions: Sequence[UXAssertion],
        test_case_results: Optional[Sequence[TestCaseResult]] = None,
        source_evidence_ids: Optional[Sequence[str]] = None,
        test_case_id: Optional[str] = None,
    ) -> UXEvaluationResult:
        """
        Evaluate a series of UX assertions, respecting finite budget and discarding noise.
        """
        eval_id = new_ux_evaluation_id()
        results: list[UXAssertionResult] = []
        defects: list[TesterDefect] = []
        findings: list[TesterFinding] = []
        recs: list[TesterFinding] = []
        discarded_count = 0
        all_ev = set(source_evidence_ids or [])

        # Infer execution_id from evaluator or first assertion
        exec_id = self.execution_id
        for a in assertions:
            if not exec_id and a.execution_id:
                exec_id = a.execution_id
                break

        # Map test_case_results by test_case_id if available
        tc_map: dict[str, TestCaseResult] = {}
        if test_case_results:
            for tc in test_case_results:
                tc_map[tc.test_case_id] = tc

        for assertion in assertions:
            tc_res = tc_map.get(assertion.test_case_id or "")
            res = self.evaluate_assertion(
                assertion=assertion,
                test_case_result=tc_res,
                source_evidence_ids=source_evidence_ids,
            )

            results.append(res)
            all_ev.update(res.evidence_ids)

            if res.is_discarded:
                discarded_count += 1
            elif res.defect:
                defects.append(res.defect)
                self._total_findings_count += 1
            elif res.recommendation:
                if self._total_findings_count < self.max_findings_per_flow:
                    findings.append(res.recommendation)
                    recs.append(res.recommendation)
                    self._total_findings_count += 1
                else:
                    logger.info("Budget cap reached, dropping excess recommendation")

        has_fail = any(r.is_fail for r in results)
        has_blocked = any(r.is_blocked for r in results)
        has_not_verified = any(r.is_not_verified for r in results)
        all_discarded = bool(results and all(r.is_discarded for r in results))

        if has_fail:
            overall_status = UXEvaluationStatus.FAIL
        elif all_discarded:
            overall_status = UXEvaluationStatus.DISCARDED
        elif has_blocked:
            overall_status = UXEvaluationStatus.BLOCKED
        elif has_not_verified:
            overall_status = UXEvaluationStatus.NOT_VERIFIED
        else:
            overall_status = UXEvaluationStatus.PASS

        return UXEvaluationResult(
            evaluation_id=eval_id,
            execution_id=exec_id,
            status=overall_status,
            assertion_results=results,
            defects=defects,
            findings=findings,
            recommendations=recs,
            discarded_count=discarded_count,
            evidence_ids=sorted(list(all_ev)),
            test_case_id=test_case_id,
            evaluated_at=utc_now(),
            metadata={
                "project_id": self.project_id,
                "execution_id": exec_id,
                "work_order_id": self.work_order_id,
            },
        )

    def evaluate(
        self,
        assertion: Union[UXAssertion, Sequence[UXAssertion]],
        test_case_results: Optional[Sequence[TestCaseResult]] = None,
        source_evidence_ids: Optional[Sequence[str]] = None,
        test_case_id: Optional[str] = None,
    ) -> UXEvaluationResult:
        """Evaluate a single UXAssertion or a sequence of assertions."""
        if isinstance(assertion, (list, tuple)):
            return self.evaluate_ux(
                assertions=assertion,
                test_case_results=test_case_results,
                source_evidence_ids=source_evidence_ids,
                test_case_id=test_case_id,
            )
        return self.evaluate_ux(
            assertions=[assertion],
            test_case_results=test_case_results,
            source_evidence_ids=source_evidence_ids,
            test_case_id=test_case_id,
        )

    def evaluate_batch(
        self,
        assertions: Sequence[UXAssertion],
        test_case_results: Optional[Sequence[TestCaseResult]] = None,
        source_evidence_ids: Optional[Sequence[str]] = None,
    ) -> list[UXEvaluationResult]:
        """Evaluate a batch of UX assertions individually."""
        return [
            self.evaluate(
                assertion=a,
                test_case_results=test_case_results,
                source_evidence_ids=source_evidence_ids,
            )
            for a in assertions
        ]

    # ---------------------------------------------------------------------------
    # Defect Factory Helper
    # ---------------------------------------------------------------------------

    def _create_ux_defect(
        self,
        title: str,
        description: str,
        severity: DefectSeverity,
        check_type: UXCheckType,
        expected_behavior: str,
        observed_behavior: str,
        reproduction_steps: list[str],
        affected_components: list[str],
        test_case_id: Optional[str] = None,
        evidence_ids: Optional[list[str]] = None,
        execution_id: Optional[str] = None,
    ) -> TesterDefect:
        """Construct an authoritative TesterDefect for concrete UX/usability failures."""
        ev_list = list(evidence_ids or [])
        if not ev_list:
            ev_list = [new_evidence_id()]

        return TesterDefect(
            defect_id=new_defect_id(),
            work_order_id=self.work_order_id or "two-default",
            title=title,
            description=description,
            severity=severity,
            defect_type=DefectType.UX,
            execution_id=execution_id or self.execution_id,
            reproduction_steps=reproduction_steps,
            expected_behavior=expected_behavior,
            observed_behavior=observed_behavior,
            affected_components=affected_components,
            test_id=test_case_id,
            evidence_ids=ev_list,
            confidence=1.0,
            provenance={
                "evaluator": "UXFlowEvaluator",
                "phase": "Phase 6.6",
                "check_type": check_type.value,
                "affected_surface": TestSurface.USER_INTERACTION.value,
            },
            metadata={
                "check_type": check_type.value,
            },
        )

    def apply_fix(self, *args, **kwargs) -> Any:
        """Zero fixing guard: UX evaluator does NOT modify product source or execute auto repairs."""
        raise TesterBoundaryViolationError(
            action="UX_AUTO_FIX",
            reason=(
                "UXFlowEvaluator is strictly an evaluation component. "
                "Tester does not perform automatic fixes on product or source code."
            ),
        )

    def auto_fix(self, *args, **kwargs) -> Any:
        return self.apply_fix(*args, **kwargs)
