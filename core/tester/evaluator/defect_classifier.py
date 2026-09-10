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
from core.tester.contracts.finding import (
    AcceptanceCriterionResult,
    TesterDefect,
    TesterEvidence,
    TesterFinding,
    is_vague_opinion,
)
from core.tester.contracts.identifiers import (
    new_defect_id,
    new_finding_id,
    new_trace_id,
)
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
    DefectSeverity,
    DefectType,
    FindingCategory,
    RuntimeEventSeverity,
    RuntimeEventType,
    TestCaseStatus,
    TesterActionType,
)

logger = logging.getLogger("AutonomOS.Tester.DefectClassifier")


def utc_now() -> str:
    """Generate ISO 8601 UTC timestamp."""
    return datetime.now(timezone.utc).isoformat()


SUBJECTIVE_PATTERNS = [
    r"could\s+(?:perhaps|maybe)\s+be\s+improved",
    r"feels?\s+(?:slightly|boring|clunky|awkward|weird|slow|bad)",
    r"looks?\s+(?:ugly|not\s+pretty|bad|cheap|boring)",
    r"spacing\s+could",
    r"make\s+more\s+premium",
    r"personal\s+preference",
    r"aesthetic\s+appeal",
]


@dataclass
class ClassificationResult:
    """Consolidated classification output containing defects and non-defect findings."""
    defects: list[TesterDefect] = field(default_factory=list)
    findings: list[TesterFinding] = field(default_factory=list)
    execution_id: Optional[str] = None
    work_order_id: Optional[str] = None
    created_at: str = field(default_factory=utc_now)

    @property
    def total_defects(self) -> int:
        return len(self.defects)

    @property
    def total_findings(self) -> int:
        return len(self.findings)

    @property
    def critical_defects(self) -> int:
        return sum(1 for d in self.defects if d.severity == DefectSeverity.CRITICAL)

    @property
    def high_defects(self) -> int:
        return sum(1 for d in self.defects if d.severity == DefectSeverity.HIGH)

    @property
    def medium_defects(self) -> int:
        return sum(1 for d in self.defects if d.severity == DefectSeverity.MEDIUM)

    @property
    def low_defects(self) -> int:
        return sum(1 for d in self.defects if d.severity == DefectSeverity.LOW)


class DefectClassifier:
    """
    Phase 5.5: Defect & Finding Classification.

    Converts sufficiently supported evaluation failures into structured TesterDefects.
    Maintains the conceptual progression:
        Observation -> Evaluation -> Finding / Defect -> TesterResult

    Invariants & Boundaries:
    1. Evidence Requirement:
       A defect MUST be supported by sufficient evidence (evidence_ids non-empty).
       Failures without evidence do not become product defects.
    2. Finding vs Defect Separation:
       Observations without violated expectations (e.g. "Animation takes 400ms")
       are NOT defects unless an explicit criterion was violated.
       Subjective opinions ("The spacing could perhaps be improved") are NEVER defects.
       Warnings only (warnings without application failure) are NOT defects.
    3. Severity Determination:
       Evidence-based, deterministic, explainable. Never marks every defect CRITICAL.
       - CRITICAL: Crashes, process terminations, core blocker failures.
       - HIGH: Primary acceptance criteria failures, required API 500 errors, navigation failures.
       - MEDIUM: Required resource 404s, secondary functional test failures.
       - LOW: Minor cosmetic text mismatches or edge cases with known workarounds.
    4. Deduplication:
       Consolidates multiple failures arising from the same root cause into a single defect,
       aggregating evidence and reproduction steps.
    5. Zero-Fixing & Zero-Dynamic-Test Boundary:
       Never performs automatic fixes or generates new tests dynamically.
    6. Causal Lineage:
       Preserves execution_id, work_order_id, test_case_id, trace, and acceptance_criterion_id.
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
        self._subjective_regexes = [re.compile(p, re.IGNORECASE) for p in SUBJECTIVE_PATTERNS]

    def is_subjective(self, text: str) -> bool:
        """Check whether text represents a vague subjective opinion."""
        if not text:
            return False
        if is_vague_opinion(text):
            return True
        for regex in self._subjective_regexes:
            if regex.search(text):
                return True
        return False

    def classify(
        self,
        execution: Optional[TesterExecution] = None,
        work_order: Optional[TesterWorkOrder] = None,
        test_plan: Optional[TestPlan] = None,
        test_case_results: Optional[Sequence[TestCaseResult]] = None,
        runtime_observations: Optional[Sequence[RuntimeObservation]] = None,
        acceptance_results: Optional[Sequence[AcceptanceCriterionResult]] = None,
        observations: Optional[Sequence[TesterObservation]] = None,
        evidence: Optional[Sequence[TesterEvidence]] = None,
    ) -> ClassificationResult:
        """
        Classify evaluation outcomes into structured TesterDefects and TesterFindings.
        """
        eff_exec = execution or self.execution
        eff_wo = work_order or self.work_order or (getattr(eff_exec, "work_order", None) if eff_exec else None)
        eff_plan = test_plan or self.test_plan or (getattr(eff_exec, "test_plan", None) if eff_exec else None)

        if eff_wo is None:
            raise TesterValidationError("Cannot classify defects: no TesterWorkOrder provided.")
        if eff_exec is None:
            raise TesterValidationError("Cannot classify defects: no TesterExecution provided.")

        # Lineage & Isolation check
        if eff_exec.work_order_id != eff_wo.work_order_id:
            raise TesterLineageError(
                f"Lineage mismatch: execution work_order_id '{eff_exec.work_order_id}' != work_order '{eff_wo.work_order_id}'."
            )
        if eff_exec.project_id != eff_wo.project_id:
            raise TesterLineageError(
                f"Project isolation mismatch: execution project_id '{eff_exec.project_id}' != work_order '{eff_wo.project_id}'."
            )

        # Gather inputs
        all_tc_results: list[TestCaseResult] = list(test_case_results or getattr(eff_exec, "test_cases", []))
        all_runtime_obs: list[RuntimeObservation] = list(runtime_observations or getattr(eff_exec, "runtime_observations", []))
        all_acceptance_results: list[AcceptanceCriterionResult] = list(acceptance_results or getattr(eff_exec, "acceptance_results", []))
        all_obs: list[TesterObservation] = list(observations or getattr(eff_exec, "observations", []))

        # Trace creation
        trace_record = eff_exec.create_trace(
            action_type=TesterActionType.IDENTIFY_DEFECT,
            action_details={
                "test_case_results_count": len(all_tc_results),
                "runtime_observations_count": len(all_runtime_obs),
                "acceptance_results_count": len(all_acceptance_results),
            },
        )
        trace_data = {"trace_id": trace_record.trace_id, "action_type": TesterActionType.IDENTIFY_DEFECT.value}

        classified_defects: list[TesterDefect] = []
        classified_findings: list[TesterFinding] = []

        # Track mapped acceptance criteria to tests
        tc_to_ac_map: dict[str, str] = {}
        if eff_plan:
            for tc in eff_plan.test_cases:
                if tc.acceptance_linkage:
                    tc_to_ac_map[tc.test_case_id] = tc.acceptance_linkage[0]
        for ar in all_acceptance_results:
            for stc in ar.supporting_test_cases:
                tc_to_ac_map[stc] = ar.criterion_id

        # 1. Classify Runtime Failures (Crash, 500 Server Error, Navigation, Required Resource 404)
        for ro in all_runtime_obs:
            self._process_runtime_observation(
                obs=ro,
                execution=eff_exec,
                work_order=eff_wo,
                trace_data=trace_data,
                tc_to_ac_map=tc_to_ac_map,
                defects=classified_defects,
                findings=classified_findings,
            )

        # 2. Classify Functional Test Case Failures
        for tcr in all_tc_results:
            self._process_test_case_result(
                result=tcr,
                test_plan=eff_plan,
                execution=eff_exec,
                work_order=eff_wo,
                trace_data=trace_data,
                tc_to_ac_map=tc_to_ac_map,
                all_runtime_obs=all_runtime_obs,
                defects=classified_defects,
                findings=classified_findings,
            )

        # 3. Classify Explicit Acceptance Criteria Failures (if not already represented)
        for ar in all_acceptance_results:
            self._process_acceptance_result(
                acceptance_res=ar,
                execution=eff_exec,
                work_order=eff_wo,
                trace_data=trace_data,
                defects=classified_defects,
                findings=classified_findings,
            )

        # 4. Deduplicate Defects sharing root cause
        deduplicated_defects = self._deduplicate_defects(classified_defects)

        # Record defects and findings onto execution and emit events
        for defect in deduplicated_defects:
            if hasattr(eff_exec, "defects") and defect not in eff_exec.defects:
                eff_exec.defects.append(defect)
            if self.event_sink is not None:
                evt = Event(
                    event_id=new_event_id(),
                    event_type=EventType.DEFECT_DETECTED,
                    timestamp=utc_now(),
                    source=EventSource.WORKER,
                    project_id=eff_exec.project_id,
                    correlation_id=eff_exec.correlation_id,
                    payload=defect.to_dict(),
                )
                self.event_sink(evt)

        for finding in classified_findings:
            if hasattr(eff_exec, "findings") and finding not in eff_exec.findings:
                eff_exec.findings.append(finding)

        return ClassificationResult(
            defects=deduplicated_defects,
            findings=classified_findings,
            execution_id=eff_exec.execution_id,
            work_order_id=eff_wo.work_order_id,
        )

    def _process_runtime_observation(
        self,
        obs: RuntimeObservation,
        execution: TesterExecution,
        work_order: TesterWorkOrder,
        trace_data: dict[str, Any],
        tc_to_ac_map: dict[str, str],
        defects: list[TesterDefect],
        findings: list[TesterFinding],
    ) -> None:
        """Process an individual runtime observation."""
        # Noise filtering: irrelevant non-application owned errors are discarded
        if not obs.is_application_owned:
            return

        # Subjective check
        if self.is_subjective(obs.message):
            findings.append(
                TesterFinding(
                    finding_id=new_finding_id(),
                    category=FindingCategory.OBSERVATION,
                    title="Subjective Observation Filtered",
                    description=obs.message,
                    severity=DefectSeverity.LOW,
                    evidence_ids=list(obs.evidence_ids),
                    actionable=False,
                    trace=trace_data,
                )
            )
            return

        # Warning-only handling: warnings are NOT defects
        if obs.severity == RuntimeEventSeverity.WARNING or (not obs.is_failure and obs.status_code and obs.status_code < 400):
            if obs.severity == RuntimeEventSeverity.WARNING:
                findings.append(
                    TesterFinding(
                        finding_id=new_finding_id(),
                        category=FindingCategory.OBSERVATION,
                        title=f"Runtime Warning: {obs.message}",
                        description=obs.message,
                        severity=DefectSeverity.LOW,
                        evidence_ids=list(obs.evidence_ids),
                        actionable=False,
                        trace=trace_data,
                    )
                )
            return

        # Irrelevant 404: optional resource (is_required_resource == False) is not a defect
        if obs.status_code == 404 and obs.is_required_resource is False:
            findings.append(
                TesterFinding(
                    finding_id=new_finding_id(),
                    category=FindingCategory.OBSERVATION,
                    title=f"Optional Resource 404: {obs.url or obs.message}",
                    description=f"Optional resource not found: {obs.url}",
                    severity=DefectSeverity.LOW,
                    evidence_ids=list(obs.evidence_ids),
                    actionable=False,
                    trace=trace_data,
                )
            )
            return

        # Only process genuine failures
        if not obs.is_failure:
            return

        # Evidence requirement guard: Defect MUST have evidence
        if not obs.evidence_ids:
            findings.append(
                TesterFinding(
                    finding_id=new_finding_id(),
                    category=FindingCategory.UNCERTAINTY,
                    title=f"Unverified Runtime Failure: {obs.message}",
                    description=f"Runtime failure observed without supporting evidence: {obs.message}",
                    severity=DefectSeverity.LOW,
                    is_uncertain=True,
                    trace=trace_data,
                )
            )
            return

        # Classify based on event type and status
        ac_id = tc_to_ac_map.get(obs.test_case_id or "")
        provenance = {
            "execution_id": execution.execution_id,
            "project_id": execution.project_id,
            "work_order_id": work_order.work_order_id,
            "source_event_id": obs.event_id,
            "runtime_event_type": obs.event_type.value,
        }

        # 1. Application Crash
        if obs.event_type == RuntimeEventType.APPLICATION_CRASH:
            defects.append(
                TesterDefect(
                    defect_id=new_defect_id(),
                    work_order_id=work_order.work_order_id,
                    execution_id=execution.execution_id,
                    title=f"Application Crash: {obs.message}",
                    description=f"Application terminated abnormally: {obs.message}",
                    severity=DefectSeverity.CRITICAL,
                    defect_type=DefectType.RUNTIME,
                    test_id=obs.test_case_id,
                    expected_behavior="Application runs continuously without fatal crashes.",
                    observed_behavior=obs.message,
                    reproduction_steps=[f"Trigger interaction: {obs.message}"],
                    evidence_ids=list(obs.evidence_ids),
                    affected_area=obs.url or "Application Process",
                    acceptance_criterion_id=ac_id,
                    provenance=provenance,
                    trace=trace_data,
                )
            )
            return

        # 2. 500 Server Error or API Failure
        if obs.status_code and obs.status_code >= 500 or obs.event_type == RuntimeEventType.API_FAILURE:
            defects.append(
                TesterDefect(
                    defect_id=new_defect_id(),
                    work_order_id=work_order.work_order_id,
                    execution_id=execution.execution_id,
                    title=f"API Server Error: {obs.method or 'HTTP'} {obs.url or obs.message} returned {obs.status_code or 500}",
                    description=f"Application API endpoint returned server error {obs.status_code or 500}: {obs.message}",
                    severity=DefectSeverity.HIGH,
                    defect_type=DefectType.API,
                    test_id=obs.test_case_id,
                    expected_behavior="API returns successful 2xx response without 5xx server errors.",
                    observed_behavior=obs.message,
                    reproduction_steps=[f"Send {obs.method or 'GET'} request to {obs.url}"],
                    evidence_ids=list(obs.evidence_ids),
                    affected_area=obs.url or "API",
                    acceptance_criterion_id=ac_id,
                    provenance=provenance,
                    trace=trace_data,
                )
            )
            return

        # 3. Navigation Failure
        if obs.event_type == RuntimeEventType.NAVIGATION_FAILURE:
            defects.append(
                TesterDefect(
                    defect_id=new_defect_id(),
                    work_order_id=work_order.work_order_id,
                    execution_id=execution.execution_id,
                    title=f"Navigation Failure: Unable to reach {obs.url or obs.message}",
                    description=f"Navigation to authorized route failed: {obs.message}",
                    severity=DefectSeverity.HIGH,
                    defect_type=DefectType.NAVIGATION,
                    test_id=obs.test_case_id,
                    expected_behavior="Browser successfully navigates to authorized application route.",
                    observed_behavior=obs.message,
                    reproduction_steps=[f"Navigate to {obs.url}"],
                    evidence_ids=list(obs.evidence_ids),
                    affected_area=obs.url or "Router",
                    acceptance_criterion_id=ac_id,
                    provenance=provenance,
                    trace=trace_data,
                )
            )
            return

        # 4. Required Resource 404
        if obs.status_code == 404 and obs.is_required_resource is True:
            defects.append(
                TesterDefect(
                    defect_id=new_defect_id(),
                    work_order_id=work_order.work_order_id,
                    execution_id=execution.execution_id,
                    title=f"Missing Required Resource: {obs.url or obs.message} (404 Not Found)",
                    description=f"Required application resource failed to load with 404 Not Found: {obs.url}",
                    severity=DefectSeverity.MEDIUM,
                    defect_type=DefectType.RESOURCE,
                    test_id=obs.test_case_id,
                    expected_behavior="Required application assets and resources load with HTTP 200.",
                    observed_behavior=f"Resource 404 Not Found: {obs.url}",
                    reproduction_steps=[f"Load resource at {obs.url}"],
                    evidence_ids=list(obs.evidence_ids),
                    affected_area=obs.url or "Static Assets",
                    acceptance_criterion_id=ac_id,
                    provenance=provenance,
                    trace=trace_data,
                )
            )
            return

        # 5. Generic Process or Runtime Error
        defects.append(
            TesterDefect(
                defect_id=new_defect_id(),
                work_order_id=work_order.work_order_id,
                execution_id=execution.execution_id,
                title=f"Runtime Error: {obs.message}",
                description=obs.message,
                severity=DefectSeverity.HIGH if obs.severity == RuntimeEventSeverity.CRITICAL else DefectSeverity.MEDIUM,
                defect_type=DefectType.RUNTIME,
                test_id=obs.test_case_id,
                expected_behavior="Application executes cleanly without runtime errors.",
                observed_behavior=obs.message,
                reproduction_steps=[f"Execute interaction resulting in: {obs.message}"],
                evidence_ids=list(obs.evidence_ids),
                affected_area=obs.url or "Runtime",
                acceptance_criterion_id=ac_id,
                provenance=provenance,
                trace=trace_data,
            )
        )

    def _process_test_case_result(
        self,
        result: TestCaseResult,
        test_plan: Optional[TestPlan],
        execution: TesterExecution,
        work_order: TesterWorkOrder,
        trace_data: dict[str, Any],
        tc_to_ac_map: dict[str, str],
        all_runtime_obs: list[RuntimeObservation],
        defects: list[TesterDefect],
        findings: list[TesterFinding],
    ) -> None:
        """Process an individual test case result."""
        # Only evaluate failed test cases
        if result.status != TestCaseStatus.FAIL:
            return

        # Anti-opinion / subjective filter
        combined_text = f"{result.name} {result.description} {result.observed_behavior}"
        if self.is_subjective(combined_text):
            findings.append(
                TesterFinding(
                    finding_id=new_finding_id(),
                    category=FindingCategory.OBSERVATION,
                    title=f"Subjective Test Failure Filtered: {result.name}",
                    description=result.observed_behavior,
                    severity=DefectSeverity.LOW,
                    evidence_ids=list(result.evidence_ids),
                    actionable=False,
                    trace=trace_data,
                )
            )
            return

        # Sufficient evidence guard: Defect MUST have evidence
        if not result.evidence_ids:
            findings.append(
                TesterFinding(
                    finding_id=new_finding_id(),
                    category=FindingCategory.UNCERTAINTY,
                    title=f"Unverified Test Failure: {result.name}",
                    description=f"Test '{result.test_id}' failed but zero evidence was gathered.",
                    severity=DefectSeverity.LOW,
                    is_uncertain=True,
                    trace=trace_data,
                )
            )
            return

        # Extract reproduction steps up to the failure
        repro_steps: list[str] = []
        failed_step_expected = result.expected_behavior
        failed_step_actual = result.observed_behavior

        if result.step_results:
            for s in result.step_results:
                line = f"Step {s.step_number}: {s.action or 'EXEC'} {s.target or ''} -> Expected: {s.expected or 'N/A'}"
                repro_steps.append(line)
                if s.status == TestCaseStatus.FAIL:
                    if s.expected:
                        failed_step_expected = s.expected
                    if s.actual or s.error:
                        step_detail = s.error or s.actual or ""
                        if result.observed_behavior:
                            if step_detail and step_detail not in result.observed_behavior:
                                failed_step_actual = f"{result.observed_behavior} ({step_detail})"
                            else:
                                failed_step_actual = result.observed_behavior
                        else:
                            failed_step_actual = step_detail
                    break
        elif test_plan:
            matching_tc = next((t for t in test_plan.test_cases if t.test_case_id == result.test_id), None)
            if matching_tc and matching_tc.steps:
                for step in matching_tc.steps:
                    repro_steps.append(f"Step {step.step_number}: {step.action} {step.target} -> {step.description}")

        if not repro_steps:
            repro_steps = [f"Execute test case '{result.name}'"]

        ac_id = tc_to_ac_map.get(result.test_id)
        is_primary_ac = ac_id is not None

        # Severity determination:
        # High if tied to acceptance criteria or severe failure, Low if minor text mismatch, Medium default
        fail_info = result.failure_information or {}
        error_msg = str(fail_info.get("error", fail_info.get("reason", ""))).lower()

        if is_primary_ac or "crash" in error_msg or "fatal" in error_msg:
            severity = DefectSeverity.HIGH
        elif "minor" in error_msg or "cosmetic" in error_msg or "label" in error_msg:
            severity = DefectSeverity.LOW
        else:
            severity = DefectSeverity.MEDIUM

        provenance = {
            "execution_id": execution.execution_id,
            "project_id": execution.project_id,
            "work_order_id": work_order.work_order_id,
            "test_case_id": result.test_id,
        }

        meta = getattr(result, "metadata", None) or (result.failure_information or {})
        is_regr = bool(meta.get("is_regression", False))
        aff_area = str(meta.get("affected_area", "Functional Workflow"))

        defects.append(
            TesterDefect(
                defect_id=new_defect_id(),
                work_order_id=work_order.work_order_id,
                execution_id=execution.execution_id,
                title=f"Functional Failure: {result.name}",
                description=f"Test case '{result.name}' failed functional verification: {failed_step_actual}",
                severity=severity,
                defect_type=DefectType.REGRESSION if is_regr else DefectType.FUNCTIONAL,
                test_id=result.test_id,
                expected_behavior=failed_step_expected or "Expected interaction state satisfies assertions.",
                observed_behavior=failed_step_actual or result.observed_behavior,
                reproduction_steps=repro_steps,
                evidence_ids=list(result.evidence_ids),
                affected_area=aff_area,
                acceptance_criterion_id=ac_id,
                confidence=1.0,
                is_regression=is_regr,
                provenance=provenance,
                trace=trace_data,
            )
        )

    def _process_acceptance_result(
        self,
        acceptance_res: AcceptanceCriterionResult,
        execution: TesterExecution,
        work_order: TesterWorkOrder,
        trace_data: dict[str, Any],
        defects: list[TesterDefect],
        findings: list[TesterFinding],
    ) -> None:
        """Process an acceptance criterion failure, enriching existing defect or adding one if not covered."""
        if acceptance_res.status != AcceptanceCriterionStatus.FAIL:
            return

        # Check if already covered by an existing defect
        already_covered = False
        for d in defects:
            if d.acceptance_criterion_id == acceptance_res.criterion_id:
                # Associate evidence
                for eid in acceptance_res.evidence_ids:
                    if eid not in d.evidence_ids:
                        d.evidence_ids.append(eid)
                already_covered = True
                break
            if d.test_id and d.test_id in acceptance_res.supporting_test_cases:
                d.acceptance_criterion_id = acceptance_res.criterion_id
                for eid in acceptance_res.evidence_ids:
                    if eid not in d.evidence_ids:
                        d.evidence_ids.append(eid)
                already_covered = True
                break

        if already_covered:
            return

        # Evidence requirement guard
        if not acceptance_res.evidence_ids:
            findings.append(
                TesterFinding(
                    finding_id=new_finding_id(),
                    category=FindingCategory.UNCERTAINTY,
                    title=f"Unverified Acceptance Failure: {acceptance_res.criterion_id}",
                    description=f"Acceptance criterion '{acceptance_res.criterion_id}' failed without concrete evidence.",
                    severity=DefectSeverity.LOW,
                    is_uncertain=True,
                    trace=trace_data,
                )
            )
            return

        provenance = {
            "execution_id": execution.execution_id,
            "project_id": execution.project_id,
            "work_order_id": work_order.work_order_id,
            "criterion_id": acceptance_res.criterion_id,
        }

        defects.append(
            TesterDefect(
                defect_id=new_defect_id(),
                work_order_id=work_order.work_order_id,
                execution_id=execution.execution_id,
                title=f"Acceptance Criterion Violation: {acceptance_res.criterion_id}",
                description=acceptance_res.reason or f"Acceptance criterion failed: {acceptance_res.description}",
                severity=DefectSeverity.HIGH,
                defect_type=DefectType.SPEC_VIOLATION,
                expected_behavior=acceptance_res.description,
                observed_behavior=acceptance_res.observed_behavior,
                reproduction_steps=[f"Verify acceptance criterion: {acceptance_res.description}"],
                evidence_ids=list(acceptance_res.evidence_ids),
                acceptance_criterion_id=acceptance_res.criterion_id,
                provenance=provenance,
                trace=trace_data,
            )
        )

    def _are_same_root_cause(self, d1: TesterDefect, d2: TesterDefect) -> bool:
        """Check if two defects represent the same root cause."""
        # 1. Exact same test case and similar description
        if d1.test_id and d2.test_id and d1.test_id == d2.test_id:
            return True

        # 2. Check URL / endpoint and HTTP status matching
        text1 = f"{d1.affected_area} {d1.observed_behavior} {d1.title} {d1.description} {' '.join(d1.reproduction_steps)}"
        text2 = f"{d2.affected_area} {d2.observed_behavior} {d2.title} {d2.description} {' '.join(d2.reproduction_steps)}"
        urls1 = set(re.findall(r"https?://[^\s\"']+|/[a-zA-Z0-9_\-\.]+(?:/[a-zA-Z0-9_\-\.]+)*", text1))
        urls2 = set(re.findall(r"https?://[^\s\"']+|/[a-zA-Z0-9_\-\.]+(?:/[a-zA-Z0-9_\-\.]+)*", text2))
        urls1 = {u.rstrip("/") for u in urls1 if len(u) > 1}
        urls2 = {u.rstrip("/") for u in urls2 if len(u) > 1}

        if urls1 and urls2 and (urls1 & urls2):
            statuses1 = set(re.findall(r"\b(?:4\d\d|5\d\d)\b", text1))
            statuses2 = set(re.findall(r"\b(?:4\d\d|5\d\d)\b", text2))
            if statuses1 and statuses2 and (statuses1 & statuses2):
                return True
            if not statuses1 and not statuses2:
                return True

        # 3. Check normalized observed behavior containment
        clean1 = re.sub(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", "", d1.observed_behavior.lower())
        clean1 = re.sub(r"\s+", " ", clean1).strip()
        clean2 = re.sub(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", "", d2.observed_behavior.lower())
        clean2 = re.sub(r"\s+", " ", clean2).strip()

        if clean1 and clean2 and len(clean1) > 10 and len(clean2) > 10:
            if clean1 in clean2 or clean2 in clean1:
                return True

        return False

    def _deduplicate_defects(self, defects: list[TesterDefect]) -> list[TesterDefect]:
        """Deduplicate defects with matching root cause signatures, consolidating evidence."""
        unique_defects: list[TesterDefect] = []

        for d in defects:
            matched_existing: Optional[TesterDefect] = None
            for existing in unique_defects:
                if self._are_same_root_cause(d, existing):
                    matched_existing = existing
                    break

            if matched_existing is not None:
                # Consolidate evidence
                for eid in d.evidence_ids:
                    if eid not in matched_existing.evidence_ids:
                        matched_existing.evidence_ids.append(eid)
                # Link acceptance criterion if existing didn't have one
                if not matched_existing.acceptance_criterion_id and d.acceptance_criterion_id:
                    matched_existing.acceptance_criterion_id = d.acceptance_criterion_id
                # Escalate severity if incoming is more severe
                if d.severity == DefectSeverity.CRITICAL:
                    matched_existing.severity = DefectSeverity.CRITICAL
                elif d.severity == DefectSeverity.HIGH and matched_existing.severity in (DefectSeverity.MEDIUM, DefectSeverity.LOW):
                    matched_existing.severity = DefectSeverity.HIGH
                # Consolidate reproduction steps
                for step in d.reproduction_steps:
                    if step not in matched_existing.reproduction_steps:
                        matched_existing.reproduction_steps.append(step)
                # If existing was generic functional and incoming is specific API/runtime, prefer more specific defect_type
                if matched_existing.defect_type == DefectType.FUNCTIONAL and d.defect_type in (DefectType.API, DefectType.RUNTIME, DefectType.CRASH, DefectType.RESOURCE):
                    matched_existing.defect_type = d.defect_type
            else:
                unique_defects.append(d)

        return unique_defects

    def apply_fix(self, *args, **kwargs) -> Any:
        """Boundary guard: Defect classification strictly forbids automatic fixing."""
        raise TesterBoundaryViolationError(
            action="AUTOMATIC_FIX",
            reason="DefectClassifier does not modify product source code or apply fixes.",
        )

    def auto_fix(self, *args, **kwargs) -> Any:
        return self.apply_fix(*args, **kwargs)

    def create_tests(self, *args, **kwargs) -> Any:
        """Boundary guard: Defect classification strictly forbids dynamic test creation."""
        raise TesterBoundaryViolationError(
            action="GENERATE_TESTS",
            reason="DefectClassifier cannot dynamically generate new test cases.",
        )

    def generate_tests(self, *args, **kwargs) -> Any:
        return self.create_tests(*args, **kwargs)
