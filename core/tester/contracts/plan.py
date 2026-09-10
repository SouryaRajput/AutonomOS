from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import logging
from typing import Any, Optional, Sequence

from core.tester.contracts.applicability import TestApplicabilityClassifier, TestApplicabilityReport
from core.tester.contracts.coverage import (
    CoverageGap,
    TestCoverageEvaluator,
    TestCoverageReport,
    TestPrioritizer,
)
from core.tester.contracts.identifiers import (
    new_plan_id,
    new_step_id,
    new_test_case_id,
    validate_execution_id,
    validate_plan_id,
    validate_test_case_id,
    validate_work_order_id,
)
from core.tester.errors import (
    TesterBoundaryViolationError,
    TesterLineageError,
    TesterValidationError,
)
from core.tester.types import (
    ApplicabilityLevel,
    ApplicableTestCategory,
    TestCaseStatus,
    TestCategory,
    TestPlanStatus,
    TestPriority,
    TestSurface,
    TestingCapability,
)

logger = logging.getLogger("AutonomOS.TestPlan")


def utc_now() -> str:
    """Generate ISO 8601 UTC timestamp."""
    return datetime.now(timezone.utc).isoformat()


@dataclass
class TestStep:
    """
    Deterministic step within a TestCase.
    Defines a discrete operational action, target, and expected observation.
    """
    __test__ = False
    step_number: int = 1
    description: str = ""
    action: Optional[str] = None
    target: Optional[str] = None
    expected: Optional[str] = None
    step_id: Optional[str] = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.step_number < 1:
            raise TesterValidationError("step_number must be a positive integer >= 1.", field_name="step_number")
        if not self.description or not self.description.strip():
            raise TesterValidationError("TestStep must have a non-empty description.", field_name="description")

    def to_dict(self) -> dict[str, Any]:
        res = {
            "step_number": self.step_number,
            "description": self.description,
            "action": self.action,
            "target": self.target,
            "expected": self.expected,
        }
        if self.step_id is not None:
            res["step_id"] = self.step_id
        if self.metadata:
            res["metadata"] = dict(self.metadata)
        return res

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> TestStep:
        return cls(
            step_number=int(data.get("step_number", 1)),
            description=str(data.get("description", "")),
            action=data.get("action"),
            target=data.get("target"),
            expected=data.get("expected"),
            step_id=data.get("step_id"),
            metadata=dict(data.get("metadata", {})),
        )


@dataclass
class TestCase:
    """
    Structured specification of an authorized test case within a TestPlan.
    Defines objective, prerequisites, sequence of steps, expected outcome,
    acceptance linkage, and evidence requirements.
    """
    __test__ = False
    test_case_id: str
    category: ApplicableTestCategory
    objective: str
    preconditions: list[str] = field(default_factory=list)
    steps: list[TestStep] = field(default_factory=list)
    expected_outcome: str = ""
    priority: TestPriority = TestPriority.MEDIUM
    acceptance_linkage: list[str] = field(default_factory=list)
    evidence_expectations: list[str] = field(default_factory=list)
    authorization_reference: str = ""
    status: TestCaseStatus = TestCaseStatus.NOT_RUN
    covered_surfaces: list[TestSurface] = field(default_factory=list)
    covered_categories: list[ApplicableTestCategory] = field(default_factory=list)
    dependencies: list[str] = field(default_factory=list)
    priority_score: int = 0
    priority_rationale: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def case_id(self) -> str:
        return self.test_case_id

    def __post_init__(self) -> None:
        validate_test_case_id(self.test_case_id)
        if not self.objective or not self.objective.strip():
            raise TesterValidationError("TestCase must have a non-empty objective.", field_name="objective")

        if isinstance(self.category, str):
            try:
                self.category = ApplicableTestCategory(self.category.upper())
            except (ValueError, KeyError):
                self.category = ApplicableTestCategory.FUNCTIONAL

        if isinstance(self.priority, str):
            try:
                self.priority = TestPriority(self.priority.upper())
            except (ValueError, KeyError):
                self.priority = TestPriority.MEDIUM

        if isinstance(self.status, str):
            try:
                self.status = TestCaseStatus(self.status.upper())
            except (ValueError, KeyError):
                self.status = TestCaseStatus.NOT_RUN

        self.preconditions = list(self.preconditions)
        self.acceptance_linkage = list(self.acceptance_linkage)
        self.evidence_expectations = list(self.evidence_expectations)
        self.covered_surfaces = [
            s if isinstance(s, TestSurface) else TestSurface(str(s).upper())
            for s in self.covered_surfaces
        ]
        self.covered_categories = [
            c if isinstance(c, ApplicableTestCategory) else ApplicableTestCategory(str(c).upper())
            for c in self.covered_categories
        ]
        self.dependencies = list(self.dependencies)

    def to_dict(self) -> dict[str, Any]:
        return {
            "test_case_id": self.test_case_id,
            "category": self.category.value,
            "objective": self.objective,
            "preconditions": list(self.preconditions),
            "steps": [s.to_dict() for s in self.steps],
            "expected_outcome": self.expected_outcome,
            "priority": self.priority.value,
            "acceptance_linkage": list(self.acceptance_linkage),
            "evidence_expectations": list(self.evidence_expectations),
            "authorization_reference": self.authorization_reference,
            "status": self.status.value if hasattr(self.status, "value") else str(self.status),
            "covered_surfaces": [s.value if hasattr(s, "value") else str(s) for s in self.covered_surfaces],
            "covered_categories": [c.value if hasattr(c, "value") else str(c) for c in self.covered_categories],
            "dependencies": list(self.dependencies),
            "priority_score": self.priority_score,
            "priority_rationale": self.priority_rationale,
        }
        if self.metadata:
            res["metadata"] = dict(self.metadata)
        return res

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> TestCase:
        steps = [TestStep.from_dict(s) if isinstance(s, dict) else s for s in data.get("steps", [])]
        raw_cat = data.get("category", ApplicableTestCategory.FUNCTIONAL.value)
        try:
            category = ApplicableTestCategory(str(raw_cat).upper())
        except (ValueError, KeyError):
            category = ApplicableTestCategory.FUNCTIONAL

        raw_prio = data.get("priority", TestPriority.MEDIUM.value)
        try:
            priority = TestPriority(str(raw_prio).upper())
        except (ValueError, KeyError):
            priority = TestPriority.MEDIUM

        raw_status = data.get("status", TestCaseStatus.NOT_RUN.value)
        try:
            status = TestCaseStatus(str(raw_status).upper())
        except (ValueError, KeyError):
            status = TestCaseStatus.NOT_RUN

        surfs = []
        for s in data.get("covered_surfaces", []):
            try:
                surfs.append(TestSurface(str(s).upper()))
            except (ValueError, KeyError):
                pass

        cats = []
        for c in data.get("covered_categories", []):
            try:
                cats.append(ApplicableTestCategory(str(c).upper()))
            except (ValueError, KeyError):
                pass

        return cls(
            test_case_id=data.get("test_case_id", new_test_case_id()),
            category=category,
            objective=str(data.get("objective", "")),
            preconditions=list(data.get("preconditions", [])),
            steps=steps,
            expected_outcome=str(data.get("expected_outcome", "")),
            priority=priority,
            acceptance_linkage=list(data.get("acceptance_linkage", [])),
            evidence_expectations=list(data.get("evidence_expectations", [])),
            authorization_reference=str(data.get("authorization_reference", "")),
            status=status,
            covered_surfaces=surfs,
            covered_categories=cats,
            dependencies=list(data.get("dependencies", [])),
            priority_score=int(data.get("priority_score", 0)),
            priority_rationale=str(data.get("priority_rationale", "")),
            metadata=dict(data.get("metadata", {})),
        )


@dataclass
class TestPlan:
    """
    Finite, immutable execution plan for a Tester execution session.
    Defines exactly what the Tester is authorized and expected to evaluate.
    
    Invariants:
    1. Finite Bounds: strictly capped by max_test_cases, time_budget, iteration_budget.
    2. Zero Filler Tests: contains only tests justified by criteria, changes, or authorized flows.
    3. Frozen Immutability: once FROZEN, test cases cannot be dynamically added or removed.
    4. Anti-Looping: prevents recursive test discovery loops.
    """
    __test__ = False
    plan_id: str
    work_order_id: str
    execution_id: str
    project_id: str
    context_revision: Optional[str] = None
    test_cases: list[TestCase] = field(default_factory=list)
    required_categories: list[ApplicableTestCategory] = field(default_factory=list)
    optional_categories: list[ApplicableTestCategory] = field(default_factory=list)
    not_applicable_categories: list[ApplicableTestCategory] = field(default_factory=list)
    max_test_cases: int = 25
    time_budget: int = 300
    iteration_budget: int = 5
    status: TestPlanStatus = TestPlanStatus.DRAFT
    estimated_execution_scope: str = ""
    coverage_report: Optional[TestCoverageReport] = None
    coverage_gaps: list[CoverageGap] = field(default_factory=list)
    provenance: dict[str, Any] = field(default_factory=dict)
    trace: dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=utc_now)

    def __post_init__(self) -> None:
        if isinstance(self.status, str):
            try:
                self.status = TestPlanStatus(self.status.upper())
            except (ValueError, KeyError):
                self.status = TestPlanStatus.DRAFT
        self.validate()

    def validate(self) -> None:
        validate_plan_id(self.plan_id)
        validate_work_order_id(self.work_order_id)
        validate_execution_id(self.execution_id)
        if not self.project_id or not self.project_id.strip():
            raise TesterLineageError("TestPlan requires a valid non-empty project_id.")

        if self.max_test_cases < 0:
            raise TesterValidationError("max_test_cases cannot be negative.", field_name="max_test_cases")
        if self.time_budget <= 0:
            raise TesterValidationError("time_budget must be a positive integer > 0.", field_name="time_budget")
        if self.iteration_budget <= 0:
            raise TesterValidationError("iteration_budget must be a positive integer > 0.", field_name="iteration_budget")

        if len(self.test_cases) > self.max_test_cases:
            raise TesterValidationError(
                f"TestPlan exceeds maximum allowed test cases: {len(self.test_cases)} > {self.max_test_cases}.",
                field_name="test_cases",
            )

    def validate_lineage(self, execution: Any = None, work_order: Any = None) -> None:
        """Validate strict lineage linkages with execution and work order."""
        if work_order is not None:
            wo_id = getattr(work_order, "work_order_id", None)
            if wo_id and self.work_order_id != wo_id:
                raise TesterLineageError(
                    f"Lineage mismatch: TestPlan belongs to work_order '{self.work_order_id}', "
                    f"cannot be used with work_order '{wo_id}'."
                )
            wo_proj = getattr(work_order, "project_id", None)
            if wo_proj and self.project_id != wo_proj:
                raise TesterLineageError(
                    f"Lineage mismatch: TestPlan belongs to project '{self.project_id}', "
                    f"cannot be used with work_order in project '{wo_proj}'."
                )
        if execution is not None:
            exec_id = getattr(execution, "execution_id", None)
            if exec_id and self.execution_id != exec_id:
                raise TesterLineageError(
                    f"Lineage mismatch: TestPlan belongs to execution '{self.execution_id}', "
                    f"cannot be used with execution '{exec_id}'."
                )

    @property
    def is_frozen(self) -> bool:
        return self.status == TestPlanStatus.FROZEN

    @property
    def is_validated(self) -> bool:
        return self.status in {TestPlanStatus.VALIDATED, TestPlanStatus.FROZEN}

    def mark_validated(self) -> None:
        """
        Transition TestPlan to VALIDATED state from DRAFT.
        """
        if self.is_frozen:
            raise TesterBoundaryViolationError(
                action="VALIDATE_TEST_PLAN",
                reason="Cannot alter validation status of a FROZEN TestPlan.",
            )
        if self.status == TestPlanStatus.INVALID:
            raise TesterValidationError("Cannot validate an INVALID TestPlan.")
        self.status = TestPlanStatus.VALIDATED

    def mark_invalid(self, reason: str = "") -> None:
        """
        Transition TestPlan to INVALID state from DRAFT.
        """
        if self.is_frozen:
            raise TesterBoundaryViolationError(
                action="INVALIDATE_TEST_PLAN",
                reason="Cannot alter status of a FROZEN TestPlan.",
            )
        self.status = TestPlanStatus.INVALID

    def freeze(self) -> None:
        """
        Transition TestPlan to FROZEN state.
        Guarantees that no further test cases can be added, removed, or mutated.
        """
        if self.status == TestPlanStatus.INVALID:
            raise TesterValidationError("Cannot freeze an INVALID TestPlan.")
        if len(self.test_cases) > self.max_test_cases:
            raise TesterValidationError(
                f"Cannot freeze TestPlan: test cases ({len(self.test_cases)}) exceed budget ({self.max_test_cases})."
            )
        self.status = TestPlanStatus.FROZEN

    def add_test_case(self, test_case: TestCase) -> None:
        """
        Add an authorized test case to the plan while in DRAFT/VALIDATED state.
        Raises TesterBoundaryViolationError if the plan is FROZEN.
        """
        if self.is_frozen:
            raise TesterBoundaryViolationError(
                action="ADD_TEST_CASE",
                reason="Cannot add test cases to a FROZEN TestPlan. A new Manager-authorized plan is required.",
            )
        if len(self.test_cases) >= self.max_test_cases:
            raise TesterValidationError(
                f"Cannot add test case: maximum budget of {self.max_test_cases} test cases reached.",
                field_name="test_cases",
            )
        if any(tc.test_case_id == test_case.test_case_id for tc in self.test_cases):
            raise TesterValidationError(
                f"Duplicate test_case_id '{test_case.test_case_id}' in TestPlan.",
                field_name="test_case_id",
            )
        self.test_cases.append(test_case)

    def remove_test_case(self, test_case_id: str) -> None:
        """
        Remove a test case from the plan while in DRAFT/VALIDATED state.
        Raises TesterBoundaryViolationError if the plan is FROZEN.
        """
        if self.is_frozen:
            raise TesterBoundaryViolationError(
                action="REMOVE_TEST_CASE",
                reason="Cannot remove test cases from a FROZEN TestPlan.",
            )
        self.test_cases = [tc for tc in self.test_cases if tc.test_case_id != test_case_id]

    def get_test_case(self, test_case_id: str) -> Optional[TestCase]:
        for tc in self.test_cases:
            if tc.test_case_id == test_case_id:
                return tc
        return None

    def attach_coverage_report(self, report: TestCoverageReport) -> None:
        """
        Attach an authoritative TestCoverageReport and its identified gaps to the test plan.
        Validates lineage alignment between plan and coverage report.
        """
        if self.is_frozen:
            raise TesterBoundaryViolationError(
                action="ATTACH_COVERAGE_REPORT",
                reason="Cannot attach coverage report to a FROZEN TestPlan.",
            )
        if report.plan_id and report.plan_id != self.plan_id:
            raise TesterLineageError(
                f"Cannot attach TestCoverageReport: plan_id mismatch ('{report.plan_id}' != '{self.plan_id}')."
            )
        self.coverage_report = report
        self.coverage_gaps = list(report.coverage_gaps)

    def to_dict(self) -> dict[str, Any]:
        return {
            "plan_id": self.plan_id,
            "work_order_id": self.work_order_id,
            "execution_id": self.execution_id,
            "project_id": self.project_id,
            "context_revision": self.context_revision,
            "test_cases": [tc.to_dict() for tc in self.test_cases],
            "required_categories": [c.value for c in sorted(self.required_categories, key=lambda x: x.value)],
            "optional_categories": [c.value for c in sorted(self.optional_categories, key=lambda x: x.value)],
            "not_applicable_categories": [c.value for c in sorted(self.not_applicable_categories, key=lambda x: x.value)],
            "max_test_cases": self.max_test_cases,
            "time_budget": self.time_budget,
            "iteration_budget": self.iteration_budget,
            "status": self.status.value,
            "estimated_execution_scope": self.estimated_execution_scope,
            "coverage_report": self.coverage_report.to_dict() if self.coverage_report else None,
            "coverage_gaps": [g.to_dict() for g in self.coverage_gaps],
            "provenance": dict(self.provenance),
            "trace": dict(self.trace),
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> TestPlan:
        cases = [TestCase.from_dict(tc) if isinstance(tc, dict) else tc for tc in data.get("test_cases", [])]
        req_cats = []
        for rc in data.get("required_categories", []):
            try:
                req_cats.append(ApplicableTestCategory(rc.upper()))
            except (ValueError, KeyError):
                pass
        opt_cats = []
        for oc in data.get("optional_categories", []):
            try:
                opt_cats.append(ApplicableTestCategory(oc.upper()))
            except (ValueError, KeyError):
                pass
        na_cats = []
        for nac in data.get("not_applicable_categories", []):
            try:
                na_cats.append(ApplicableTestCategory(nac.upper()))
            except (ValueError, KeyError):
                pass

        raw_status = data.get("status", TestPlanStatus.DRAFT.value)
        try:
            status = TestPlanStatus(str(raw_status).upper())
        except (ValueError, KeyError):
            status = TestPlanStatus.DRAFT

        raw_cov = data.get("coverage_report")
        coverage_report = TestCoverageReport.from_dict(raw_cov) if isinstance(raw_cov, dict) else None
        coverage_gaps = [CoverageGap.from_dict(g) if isinstance(g, dict) else g for g in data.get("coverage_gaps", [])]

        return cls(
            plan_id=str(data.get("plan_id", new_plan_id())),
            work_order_id=str(data.get("work_order_id", "")),
            execution_id=str(data.get("execution_id", "")),
            project_id=str(data.get("project_id", "")),
            context_revision=data.get("context_revision"),
            test_cases=cases,
            required_categories=req_cats,
            optional_categories=opt_cats,
            not_applicable_categories=na_cats,
            max_test_cases=int(data.get("max_test_cases", 25)),
            time_budget=int(data.get("time_budget", 300)),
            iteration_budget=int(data.get("iteration_budget", 5)),
            status=status,
            estimated_execution_scope=str(data.get("estimated_execution_scope", "")),
            coverage_report=coverage_report,
            coverage_gaps=coverage_gaps,
            provenance=dict(data.get("provenance", {})),
            trace=dict(data.get("trace", {})),
            created_at=str(data.get("created_at", utc_now())),
        )


class TestPlanGenerator:
    """
    Deterministic test plan generator for Tester V1 Phase 3.3.
    Produces a finite, bounded TestPlan from WorkOrder, TestContext, and ApplicabilityReport.
    """
    __test__ = False

    def __init__(self, classifier: Optional[TestApplicabilityClassifier] = None):
        self.classifier = classifier or TestApplicabilityClassifier()

    def generate(
        self,
        work_order: Any,
        test_context: Any,
        applicability_report: Optional[TestApplicabilityReport] = None,
        max_test_cases: Optional[int] = None,
        auto_freeze: bool = True,
    ) -> TestPlan:
        """
        Generate a finite, deterministic TestPlan and immediately freeze it.
        """
        proj_id = getattr(test_context, "project_id", "") or getattr(work_order, "project_id", "")
        wo_id = getattr(test_context, "work_order_id", "") or getattr(work_order, "work_order_id", "")
        exec_id = getattr(test_context, "execution_id", "") or getattr(work_order, "execution_id", "")

        # 1. Lineage Verification
        if hasattr(work_order, "project_id") and hasattr(test_context, "project_id"):
            if work_order.project_id != test_context.project_id:
                raise TesterLineageError(
                    f"Lineage mismatch between work_order ({work_order.project_id}) and test_context ({test_context.project_id})."
                )

        # 2. Resolve Applicability Report
        if applicability_report is None:
            applicability_report = self.classifier.classify(work_order=work_order, test_context=test_context)

        # 3. Resolve Finite Budgets
        time_budget = getattr(work_order, "time_budget", 300) or 300
        iteration_budget = getattr(work_order, "iteration_budget", 5) or 5

        # Maximum test case budget resolution (conservative default: 25)
        max_budget = 25
        if max_test_cases is not None and max_test_cases > 0:
            max_budget = int(max_test_cases)
        elif hasattr(work_order, "metadata") and isinstance(work_order.metadata, dict):
            if "max_test_cases" in work_order.metadata:
                max_budget = int(work_order.metadata["max_test_cases"])

        req_cats = set(applicability_report.required_categories)
        opt_cats = set(applicability_report.optional_categories)
        na_cats = set(applicability_report.not_applicable_categories)

        candidate_cases: list[TestCase] = []

        # 4. Empty Scope / Nothing Applicable check
        if not req_cats and not opt_cats:
            # Valid plan with 0 test cases
            plan = TestPlan(
                plan_id=new_plan_id(),
                work_order_id=wo_id,
                execution_id=exec_id,
                project_id=proj_id,
                context_revision=getattr(test_context, "source_revision", None),
                test_cases=[],
                required_categories=list(req_cats),
                optional_categories=list(opt_cats),
                not_applicable_categories=list(na_cats),
                max_test_cases=max_budget,
                time_budget=time_budget,
                iteration_budget=iteration_budget,
                status=TestPlanStatus.DRAFT,
                estimated_execution_scope="Zero executable test cases applicable.",
                provenance={
                    "work_order_id": wo_id,
                    "project_id": proj_id,
                    "execution_id": exec_id,
                    "source_revision": getattr(test_context, "source_revision", None),
                },
                trace={
                    "generator": "TestPlanGenerator",
                    "version": "1.0.0",
                    "phase": "3.5",
                },
                created_at=utc_now(),
            )
            evaluator = TestCoverageEvaluator(classifier=self.classifier)
            cov_report = evaluator.evaluate(
                work_order=work_order,
                test_context=test_context,
                test_plan=plan,
                applicability_report=applicability_report,
            )
            plan.attach_coverage_report(cov_report)
            plan.mark_validated()
            plan.freeze()
            return plan

        blocked_categories = set(getattr(applicability_report, "blocked_categories", []))
        runnable_cats = (req_cats | opt_cats) - na_cats - blocked_categories

        # A. Explicit Acceptance Criteria (Priority: CRITICAL)
        acceptance_criteria = getattr(work_order, "acceptance_criteria", []) or []
        for ac in acceptance_criteria:
            crit_id = getattr(ac, "criterion_id", "ac-general")
            crit_desc = getattr(ac, "description", "")
            cat = self._categorize_criterion(crit_desc, req_cats)
            if cat in na_cats or cat in blocked_categories or cat not in runnable_cats:
                continue

            evidence = ["TEST_OUTPUT", "OBSERVATION"]
            if cat in {ApplicableTestCategory.VISUAL, ApplicableTestCategory.RESPONSIVE}:
                evidence.append("SCREENSHOT")
            elif cat == ApplicableTestCategory.ANIMATION:
                evidence.append("VIDEO")

            surfs = self._surfaces_for_category(cat)

            candidate_cases.append(TestCase(
                test_case_id=new_test_case_id(),
                category=cat,
                objective=f"Evaluate acceptance criterion: {crit_desc}",
                preconditions=["Product environment initialized and reachable."],
                steps=[
                    TestStep(step_number=1, description=f"Execute verification for criterion '{crit_id}'", target=crit_id),
                    TestStep(step_number=2, description="Observe and record behavior against acceptance requirement", expected=crit_desc),
                ],
                expected_outcome=crit_desc,
                priority=TestPriority.CRITICAL,
                acceptance_linkage=[crit_id],
                evidence_expectations=evidence,
                authorization_reference=crit_id,
                covered_surfaces=surfs,
                covered_categories=[cat],
            ))

        # B. Explicit Required Flows (Priority: HIGH)
        required_flows = getattr(work_order, "required_flows", []) or []
        for flow in required_flows:
            cat = ApplicableTestCategory.UI_INTERACTION if ApplicableTestCategory.UI_INTERACTION in req_cats else ApplicableTestCategory.FUNCTIONAL
            if cat in na_cats:
                continue

            candidate_cases.append(TestCase(
                test_case_id=new_test_case_id(),
                category=cat,
                objective=f"Execute required user flow: {flow}",
                preconditions=["Session in initial state for flow."],
                steps=[
                    TestStep(step_number=1, description=f"Initialize flow sequence '{flow}'", target=flow),
                    TestStep(step_number=2, description=f"Traverse steps for '{flow}' to terminal state"),
                ],
                expected_outcome=f"Flow '{flow}' completes successfully without obstruction.",
                priority=TestPriority.HIGH,
                acceptance_linkage=[],
                evidence_expectations=["OBSERVATION", "TEST_OUTPUT"],
                authorization_reference=flow,
                covered_surfaces=[TestSurface.UI, TestSurface.USER_INTERACTION, TestSurface.NAVIGATION],
                covered_categories=[cat],
            ))

        # C. Navigation Routes Tests (Priority: HIGH)
        if ApplicableTestCategory.NAVIGATION in req_cats:
            routes = getattr(test_context, "changed_routes", []) or []
            if not routes and hasattr(work_order, "test_scope"):
                routes = getattr(work_order.test_scope, "routes", []) or []
            for route in sorted(routes):
                candidate_cases.append(TestCase(
                    test_case_id=new_test_case_id(),
                    category=ApplicableTestCategory.NAVIGATION,
                    objective=f"Verify navigation route accessibility: {route}",
                    preconditions=["Application running in target environment."],
                    steps=[
                        TestStep(step_number=1, description=f"Navigate to authorized route '{route}'", action="NAVIGATE", target=route),
                        TestStep(step_number=2, description="Verify route renders expected component view", expected=f"Route {route} status 200"),
                    ],
                    expected_outcome=f"Route '{route}' is navigable and renders without routing errors.",
                    priority=TestPriority.HIGH,
                    acceptance_linkage=[],
                    evidence_expectations=["OBSERVATION", "SCREENSHOT"],
                    authorization_reference=route,
                    covered_surfaces=[TestSurface.NAVIGATION],
                    covered_categories=[ApplicableTestCategory.NAVIGATION],
                ))

        # D. Frontend UI Interaction Tests (Priority: HIGH)
        if ApplicableTestCategory.UI_INTERACTION in req_cats:
            components = getattr(test_context, "changed_components", []) or []
            if not components and hasattr(work_order, "test_scope") and getattr(work_order.test_scope, "components", None):
                components = getattr(work_order.test_scope, "components", []) or []
            if not components:
                components = ["UI_Interaction_Target"]
            for comp in sorted(components):
                candidate_cases.append(TestCase(
                    test_case_id=new_test_case_id(),
                    category=ApplicableTestCategory.UI_INTERACTION,
                    objective=f"Verify user interaction on modified component: {comp}",
                    preconditions=[f"Component '{comp}' rendered on screen."],
                    steps=[
                        TestStep(step_number=1, description=f"Locate interactive controls in component '{comp}'", target=comp),
                        TestStep(step_number=2, description="Trigger click and input events on controls"),
                        TestStep(step_number=3, description="Observe interactive state transition"),
                    ],
                    expected_outcome=f"Component '{comp}' accepts user interactions and responds deterministically.",
                    priority=TestPriority.HIGH,
                    acceptance_linkage=[],
                    evidence_expectations=["OBSERVATION", "SCREENSHOT"],
                    authorization_reference=comp,
                    covered_surfaces=[TestSurface.UI, TestSurface.USER_INTERACTION],
                    covered_categories=[ApplicableTestCategory.UI_INTERACTION],
                ))

        # E. Visual Layout Tests (Priority: HIGH if CSS/visual, else OPTIONAL)
        if ApplicableTestCategory.VISUAL in req_cats:
            prio = TestPriority.HIGH
            candidate_cases.append(TestCase(
                test_case_id=new_test_case_id(),
                category=ApplicableTestCategory.VISUAL,
                objective="Verify visual presentation and layout integrity.",
                preconditions=["UI rendered at standard desktop viewport."],
                steps=[
                    TestStep(step_number=1, description="Inspect viewport visual rendering", action="SCREENSHOT"),
                    TestStep(step_number=2, description="Verify layout elements do not visually collide or overlap"),
                ],
                expected_outcome="Visual layout aligns properly with no clipping or misaligned presentation.",
                priority=prio,
                acceptance_linkage=[],
                evidence_expectations=["SCREENSHOT"],
                authorization_reference="visual_layout",
                covered_surfaces=[TestSurface.VISUAL_LAYOUT, TestSurface.UI],
                covered_categories=[ApplicableTestCategory.VISUAL],
            ))

        # F. Responsive Layout Tests (Priority: HIGH if responsive required)
        if ApplicableTestCategory.RESPONSIVE in req_cats:
            candidate_cases.append(TestCase(
                test_case_id=new_test_case_id(),
                category=ApplicableTestCategory.RESPONSIVE,
                objective="Verify responsive layout adaptation across viewports.",
                preconditions=["Viewport resizing supported in environment."],
                steps=[
                    TestStep(step_number=1, description="Inspect layout at mobile viewport (375x667)", action="RESIZE_VIEWPORT"),
                    TestStep(step_number=2, description="Inspect layout at desktop viewport (1280x800)", action="RESIZE_VIEWPORT"),
                ],
                expected_outcome="Layout fluidly adapts to mobile and desktop viewports without horizontal scroll breakage.",
                priority=TestPriority.HIGH,
                acceptance_linkage=[],
                evidence_expectations=["SCREENSHOT"],
                authorization_reference="responsive_layout",
                covered_surfaces=[TestSurface.RESPONSIVE_LAYOUT, TestSurface.VISUAL_LAYOUT],
                covered_categories=[ApplicableTestCategory.RESPONSIVE],
            ))

        # G. Animation Tests (Priority: HIGH if animation required)
        if ApplicableTestCategory.ANIMATION in req_cats:
            candidate_cases.append(TestCase(
                test_case_id=new_test_case_id(),
                category=ApplicableTestCategory.ANIMATION,
                objective="Verify animation and transition behavior.",
                preconditions=["Screen recording capability available."],
                steps=[
                    TestStep(step_number=1, description="Trigger animated component state transition"),
                    TestStep(step_number=2, description="Observe transition completion without jank or freeze"),
                ],
                expected_outcome="Animation transitions smoothly to final state.",
                priority=TestPriority.HIGH,
                acceptance_linkage=[],
                evidence_expectations=["VIDEO"],
                authorization_reference="animation",
                covered_surfaces=[TestSurface.ANIMATION],
                covered_categories=[ApplicableTestCategory.ANIMATION],
            ))

        # H. Backend Functional Tests (Priority: HIGH/MEDIUM)
        if ApplicableTestCategory.FUNCTIONAL in req_cats and not acceptance_criteria:
            modules = getattr(test_context, "changed_modules", []) or ["CoreFunctionality"]
            for mod in sorted(modules):
                candidate_cases.append(TestCase(
                    test_case_id=new_test_case_id(),
                    category=ApplicableTestCategory.FUNCTIONAL,
                    objective=f"Verify functional correctness of service module: {mod}",
                    preconditions=["Backend service dependencies initialized."],
                    steps=[
                        TestStep(step_number=1, description=f"Execute functional verification for module '{mod}'", target=mod),
                        TestStep(step_number=2, description="Assert functional output matches expected behavior"),
                    ],
                    expected_outcome=f"Module '{mod}' executes accurately and returns expected outputs.",
                    priority=TestPriority.HIGH,
                    acceptance_linkage=[],
                    evidence_expectations=["TEST_OUTPUT", "OBSERVATION"],
                    authorization_reference=mod,
                    covered_surfaces=[TestSurface.BUSINESS_LOGIC, TestSurface.API],
                    covered_categories=[ApplicableTestCategory.FUNCTIONAL],
                ))

        # I. Integration Tests (Priority: MEDIUM)
        if ApplicableTestCategory.INTEGRATION in req_cats:
            candidate_cases.append(TestCase(
                test_case_id=new_test_case_id(),
                category=ApplicableTestCategory.INTEGRATION,
                objective="Verify cross-layer integration between architectural components.",
                preconditions=["All connected architectural layers accessible."],
                steps=[
                    TestStep(step_number=1, description="Trigger cross-boundary request / invocation", action="INVOKE_INTEGRATION"),
                    TestStep(step_number=2, description="Verify data flow reaches underlying persistence / service layer"),
                ],
                expected_outcome="Cross-layer transaction completes with state integrity preserved.",
                priority=TestPriority.MEDIUM,
                acceptance_linkage=[],
                evidence_expectations=["LOG", "TEST_OUTPUT"],
                authorization_reference="cross_layer_integration",
                covered_surfaces=[TestSurface.INTEGRATION, TestSurface.DATA_FLOW],
                covered_categories=[ApplicableTestCategory.INTEGRATION],
            ))

        # J. Performance Tests (Priority: LOW / MEDIUM if explicitly requested)
        if ApplicableTestCategory.PERFORMANCE in req_cats:
            candidate_cases.append(TestCase(
                test_case_id=new_test_case_id(),
                category=ApplicableTestCategory.PERFORMANCE,
                objective="Evaluate response time and execution latency against performance bounds.",
                preconditions=["Performance measurement instrumentation active."],
                steps=[
                    TestStep(step_number=1, description="Sample execution latency across key operations", action="MEASURE_LATENCY"),
                    TestStep(step_number=2, description="Compare latency against threshold limits"),
                ],
                expected_outcome="Execution latency remains within bounded performance threshold.",
                priority=TestPriority.LOW,
                acceptance_linkage=[],
                evidence_expectations=["METRIC"],
                authorization_reference="performance",
                covered_surfaces=[TestSurface.PERFORMANCE, TestSurface.API],
                covered_categories=[ApplicableTestCategory.PERFORMANCE],
            ))

        # 6. Strictly Exclude Unauthorized, Blocked & Not-Applicable Categories
        authorized_cases = [tc for tc in candidate_cases if tc.category in runnable_cats]

        # 7. Priority Sorting & Budget Truncation using TestPrioritizer
        changed_components = getattr(test_context, "changed_components", []) or []
        changed_modules = getattr(test_context, "changed_modules", []) or []
        changed_routes = getattr(test_context, "changed_routes", []) or []

        ordered_cases = TestPrioritizer.order_test_cases(
            authorized_cases,
            changed_components=changed_components,
            changed_modules=changed_modules,
            changed_routes=changed_routes,
        )

        bounded_cases = ordered_cases[:max_budget]

        # 8. Create TestPlan in DRAFT status
        plan = TestPlan(
            plan_id=new_plan_id(),
            work_order_id=wo_id,
            execution_id=exec_id,
            project_id=proj_id,
            context_revision=getattr(test_context, "source_revision", None),
            test_cases=bounded_cases,
            required_categories=list(req_cats),
            optional_categories=list(opt_cats),
            not_applicable_categories=list(na_cats),
            max_test_cases=max_budget,
            time_budget=time_budget,
            iteration_budget=iteration_budget,
            status=TestPlanStatus.DRAFT,
            estimated_execution_scope=(
                "Zero executable test cases applicable."
                if not bounded_cases
                else f"Finite evaluation of {len(bounded_cases)} test cases across {[c.value for c in sorted(req_cats, key=lambda x: x.value)]}."
            ),
            provenance={
                "work_order_id": wo_id,
                "project_id": proj_id,
                "execution_id": exec_id,
                "source_revision": getattr(test_context, "source_revision", None),
            },
            trace={
                "generator": "TestPlanGenerator",
                "version": "1.0.0",
                "phase": "3.5",
            },
            created_at=utc_now(),
        )

        # 9. Evaluate Coverage and Attach Report
        evaluator = TestCoverageEvaluator(classifier=self.classifier)
        cov_report = evaluator.evaluate(
            work_order=work_order,
            test_context=test_context,
            test_plan=plan,
            applicability_report=applicability_report,
        )
        plan.attach_coverage_report(cov_report)

        # 10. Validate, Freeze and Return Plan
        if auto_freeze:
            plan.mark_validated()
            plan.freeze()
        return plan

    def _surfaces_for_category(self, cat: ApplicableTestCategory) -> list[TestSurface]:
        if cat == ApplicableTestCategory.VISUAL:
            return [TestSurface.VISUAL_LAYOUT, TestSurface.UI]
        if cat == ApplicableTestCategory.RESPONSIVE:
            return [TestSurface.RESPONSIVE_LAYOUT, TestSurface.VISUAL_LAYOUT]
        if cat == ApplicableTestCategory.ANIMATION:
            return [TestSurface.ANIMATION, TestSurface.UI]
        if cat == ApplicableTestCategory.NAVIGATION:
            return [TestSurface.NAVIGATION]
        if cat == ApplicableTestCategory.UI_INTERACTION:
            return [TestSurface.UI, TestSurface.USER_INTERACTION]
        if cat == ApplicableTestCategory.INTEGRATION:
            return [TestSurface.INTEGRATION, TestSurface.DATA_FLOW]
        if cat == ApplicableTestCategory.PERFORMANCE:
            return [TestSurface.PERFORMANCE, TestSurface.API]
        return [TestSurface.BUSINESS_LOGIC, TestSurface.API]

    def _categorize_criterion(self, desc: str, required_categories: set[ApplicableTestCategory]) -> ApplicableTestCategory:
        d_lower = desc.lower()
        if "responsive" in d_lower or "viewport" in d_lower:
            return ApplicableTestCategory.RESPONSIVE
        if "animat" in d_lower or "transition" in d_lower:
            return ApplicableTestCategory.ANIMATION
        if "visual" in d_lower or "layout" in d_lower or "render" in d_lower:
            return ApplicableTestCategory.VISUAL
        if "route" in d_lower or "navigate" in d_lower or "url" in d_lower:
            return ApplicableTestCategory.NAVIGATION
        if "click" in d_lower or "button" in d_lower or "type" in d_lower or "input" in d_lower:
            return ApplicableTestCategory.UI_INTERACTION
        if "integration" in d_lower or "database" in d_lower or "api" in d_lower:
            return ApplicableTestCategory.INTEGRATION
        if "performance" in d_lower or "latency" in d_lower:
            return ApplicableTestCategory.PERFORMANCE
        return ApplicableTestCategory.FUNCTIONAL
