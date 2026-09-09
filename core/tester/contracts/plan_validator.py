from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import logging
from typing import Any, Optional, Sequence

from core.tester.contracts.boundary import (
    TESTER_ALLOWED_CAPABILITIES,
    TESTER_FORBIDDEN_ACTIONS,
    TesterBoundaryGuard,
)
from core.tester.contracts.identifiers import (
    new_validation_report_id,
    validate_execution_id,
    validate_plan_id,
    validate_validation_report_id,
    validate_work_order_id,
)
from core.tester.errors import TesterLineageError, TesterValidationError
from core.tester.types import (
    ApplicableTestCategory,
    PlanValidationCode,
    TestingCapability,
    TestPlanStatus,
    ValidationIssueSeverity,
)

logger = logging.getLogger("AutonomOS.TestPlanValidator")


def utc_now() -> str:
    """Generate ISO 8601 UTC timestamp."""
    return datetime.now(timezone.utc).isoformat()


@dataclass
class TestPlanValidationIssue:
    """
    Structured issue identified during TestPlan validation.
    Guarantees machine-parsable, deterministic failure reporting.
    """
    __test__ = False
    code: PlanValidationCode
    message: str
    target_id: Optional[str] = None
    severity: ValidationIssueSeverity = ValidationIssueSeverity.ERROR

    def __post_init__(self) -> None:
        if isinstance(self.code, str):
            try:
                self.code = PlanValidationCode(self.code.upper())
            except (ValueError, KeyError):
                self.code = PlanValidationCode.INVALID_TEST_CASE
        if isinstance(self.severity, str):
            try:
                self.severity = ValidationIssueSeverity(self.severity.upper())
            except (ValueError, KeyError):
                self.severity = ValidationIssueSeverity.ERROR

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code.value,
            "message": self.message,
            "target_id": self.target_id,
            "severity": self.severity.value,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> TestPlanValidationIssue:
        return cls(
            code=data.get("code", PlanValidationCode.INVALID_TEST_CASE.value),
            message=str(data.get("message", "")),
            target_id=data.get("target_id"),
            severity=data.get("severity", ValidationIssueSeverity.ERROR.value),
        )


@dataclass
class TestPlanValidationResult:
    """
    Immutable outcome of a TestPlan validation pass.
    Records validity, categorized issues, lineage, and summary.
    """
    __test__ = False
    report_id: str
    plan_id: str
    work_order_id: str
    execution_id: str
    project_id: str
    is_valid: bool
    issues: list[TestPlanValidationIssue] = field(default_factory=list)
    summary: str = ""
    provenance: dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=utc_now)

    def __post_init__(self) -> None:
        self.validate()

    def validate(self) -> None:
        validate_validation_report_id(self.report_id)
        validate_plan_id(self.plan_id)
        validate_work_order_id(self.work_order_id)
        validate_execution_id(self.execution_id)
        if not self.project_id or not self.project_id.strip():
            raise TesterLineageError("TestPlanValidationResult requires a valid non-empty project_id.")
        if self.provenance and isinstance(self.provenance, dict):
            prov_proj = self.provenance.get("project_id")
            if prov_proj and prov_proj != self.project_id:
                raise TesterLineageError(
                    f"Lineage mismatch between project_id ({self.project_id}) and provenance ({prov_proj})."
                )

    @property
    def errors(self) -> list[TestPlanValidationIssue]:
        return [i for i in self.issues if i.severity == ValidationIssueSeverity.ERROR]

    @property
    def warnings(self) -> list[TestPlanValidationIssue]:
        return [i for i in self.issues if i.severity == ValidationIssueSeverity.WARNING]

    @property
    def error_count(self) -> int:
        return len(self.errors)

    @property
    def warning_count(self) -> int:
        return len(self.warnings)

    def to_dict(self) -> dict[str, Any]:
        return {
            "report_id": self.report_id,
            "plan_id": self.plan_id,
            "work_order_id": self.work_order_id,
            "execution_id": self.execution_id,
            "project_id": self.project_id,
            "is_valid": self.is_valid,
            "error_count": self.error_count,
            "warning_count": self.warning_count,
            "issues": [i.to_dict() for i in self.issues],
            "summary": self.summary,
            "provenance": dict(self.provenance),
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> TestPlanValidationResult:
        issues = [
            TestPlanValidationIssue.from_dict(i) if isinstance(i, dict) else i
            for i in data.get("issues", [])
        ]
        return cls(
            report_id=str(data.get("report_id", new_validation_report_id())),
            plan_id=str(data.get("plan_id", "")),
            work_order_id=str(data.get("work_order_id", "")),
            execution_id=str(data.get("execution_id", "")),
            project_id=str(data.get("project_id", "")),
            is_valid=bool(data.get("is_valid", False)),
            issues=issues,
            summary=str(data.get("summary", "")),
            provenance=dict(data.get("provenance", {})),
            created_at=str(data.get("created_at", utc_now())),
        )


class TestPlanValidator:
    """
    Deterministic validator verifying a TestPlan before execution.
    Enforces the 3-way boundary:
    Global Tester Boundary ∩ WorkOrder Authorization ∩ TestPlan Scope
    """
    __test__ = False

    CATEGORY_REQUIRED_CAPABILITIES: dict[ApplicableTestCategory, list[TestingCapability]] = {
        ApplicableTestCategory.FUNCTIONAL: [TestingCapability.TEST_EXECUTION],
        ApplicableTestCategory.UI_INTERACTION: [TestingCapability.TEST_EXECUTION, TestingCapability.CLICK],
        ApplicableTestCategory.NAVIGATION: [TestingCapability.TEST_EXECUTION, TestingCapability.NAVIGATE],
        ApplicableTestCategory.VISUAL: [TestingCapability.TEST_EXECUTION, TestingCapability.SCREENSHOT],
        ApplicableTestCategory.RESPONSIVE: [TestingCapability.TEST_EXECUTION, TestingCapability.SCREENSHOT, TestingCapability.NAVIGATE],
        ApplicableTestCategory.ANIMATION: [TestingCapability.TEST_EXECUTION, TestingCapability.SCREEN_RECORDING],
        ApplicableTestCategory.OCR: [TestingCapability.TEST_EXECUTION, TestingCapability.OCR],
        ApplicableTestCategory.VIDEO: [TestingCapability.TEST_EXECUTION, TestingCapability.SCREEN_RECORDING],
        ApplicableTestCategory.PERFORMANCE: [TestingCapability.TEST_EXECUTION, TestingCapability.PERFORMANCE_MEASUREMENT],
        ApplicableTestCategory.INTEGRATION: [TestingCapability.TEST_EXECUTION],
    }

    VALID_EVIDENCE_TYPES: set[str] = {
        "SCREENSHOT",
        "VIDEO",
        "OBSERVATION",
        "TEST_OUTPUT",
        "LOG",
        "METRIC",
        "DIFF",
        "TRACE",
    }

    MAX_STEPS_PER_TEST_CASE: int = 50

    def validate(
        self,
        test_plan: Any,
        work_order: Any,
        test_context: Optional[Any] = None,
        environment: Optional[Any] = None,
        runtime_capabilities: Optional[Sequence[TestingCapability | str]] = None,
        test_environment: Optional[Any] = None,
    ) -> TestPlanValidationResult:
        """
        Validate a TestPlan deterministically against WorkOrder authorizations,
        Tester boundaries, context, and runtime availability.
        Side-effect free: does not execute tests or modify application state.
        """
        # Flexible argument resolution
        if test_environment is not None and environment is None:
            environment = test_environment
        elif environment is not None and isinstance(environment, (list, tuple, set)) and not hasattr(environment, "environment_id"):
            if runtime_capabilities is None:
                runtime_capabilities = environment
            environment = test_environment

        issues: list[TestPlanValidationIssue] = []

        plan_proj = getattr(test_plan, "project_id", "") or ""
        wo_proj = getattr(work_order, "project_id", "") or ""
        plan_wo = getattr(test_plan, "work_order_id", "") or ""
        wo_id = getattr(work_order, "work_order_id", "") or ""
        plan_exec = getattr(test_plan, "execution_id", "") or ""
        wo_exec = getattr(work_order, "execution_id", "") or ""

        # 1. WorkOrder Lineage & Execution Identity
        if not plan_proj or plan_proj != wo_proj:
            issues.append(TestPlanValidationIssue(
                code=PlanValidationCode.INVALID_LINEAGE,
                message=f"Project ID mismatch: test_plan ('{plan_proj}') != work_order ('{wo_proj}').",
                target_id="project_id",
            ))

        if not plan_wo or plan_wo != wo_id:
            issues.append(TestPlanValidationIssue(
                code=PlanValidationCode.INVALID_LINEAGE,
                message=f"WorkOrder ID mismatch: test_plan ('{plan_wo}') != work_order ('{wo_id}').",
                target_id="work_order_id",
            ))

        if wo_exec and plan_exec and plan_exec != wo_exec:
            issues.append(TestPlanValidationIssue(
                code=PlanValidationCode.INVALID_LINEAGE,
                message=f"Execution ID mismatch: test_plan ('{plan_exec}') != work_order ('{wo_exec}').",
                target_id="execution_id",
            ))

        if test_context is not None:
            ctx_proj = getattr(test_context, "project_id", "") or ""
            ctx_wo = getattr(test_context, "work_order_id", "") or ""
            ctx_exec = getattr(test_context, "execution_id", "") or ""

            if ctx_proj and plan_proj != ctx_proj:
                issues.append(TestPlanValidationIssue(
                    code=PlanValidationCode.INVALID_LINEAGE,
                    message=f"Project ID mismatch: test_plan ('{plan_proj}') != test_context ('{ctx_proj}').",
                    target_id="project_id",
                ))
            if ctx_wo and plan_wo != ctx_wo:
                issues.append(TestPlanValidationIssue(
                    code=PlanValidationCode.INVALID_LINEAGE,
                    message=f"WorkOrder ID mismatch: test_plan ('{plan_wo}') != test_context ('{ctx_wo}').",
                    target_id="work_order_id",
                ))
            if ctx_exec and plan_exec != ctx_exec:
                issues.append(TestPlanValidationIssue(
                    code=PlanValidationCode.INVALID_LINEAGE,
                    message=f"Execution ID mismatch: test_plan ('{plan_exec}') != test_context ('{ctx_exec}').",
                    target_id="execution_id",
                ))

        # Provenance Lineage Check
        provenance = getattr(test_plan, "provenance", {}) or {}
        if isinstance(provenance, dict):
            prov_proj = provenance.get("project_id")
            if prov_proj and prov_proj != plan_proj:
                issues.append(TestPlanValidationIssue(
                    code=PlanValidationCode.INVALID_LINEAGE,
                    message=f"Provenance project_id mismatch: '{prov_proj}' != test_plan '{plan_proj}'.",
                    target_id="project_id",
                ))

        # 2. Plan State Invariant
        plan_status = getattr(test_plan, "status", TestPlanStatus.DRAFT)
        if plan_status == TestPlanStatus.INVALID:
            issues.append(TestPlanValidationIssue(
                code=PlanValidationCode.PLAN_STATE_ERROR,
                message="TestPlan is marked INVALID and cannot be validated without revision.",
                target_id="status",
            ))

        # 3. Finite Budget Bounds & Unbounded Execution Checks
        time_budget = getattr(test_plan, "time_budget", 0)
        iter_budget = getattr(test_plan, "iteration_budget", 0)
        max_cases = getattr(test_plan, "max_test_cases", 0)
        wo_time = getattr(work_order, "time_budget", 300) or 300
        wo_iter = getattr(work_order, "iteration_budget", 5) or 5

        if time_budget <= 0:
            issues.append(TestPlanValidationIssue(
                code=PlanValidationCode.UNBOUNDED_PLAN,
                message=f"TestPlan time_budget must be positive (> 0), got {time_budget}.",
                target_id="time_budget",
            ))
        elif time_budget > wo_time:
            issues.append(TestPlanValidationIssue(
                code=PlanValidationCode.BUDGET_EXCEEDED,
                message=f"TestPlan time_budget ({time_budget}s) exceeds WorkOrder authorization ({wo_time}s).",
                target_id="time_budget",
            ))
        elif time_budget > 3600:
            issues.append(TestPlanValidationIssue(
                code=PlanValidationCode.UNBOUNDED_PLAN,
                message=f"TestPlan time_budget ({time_budget}s) exceeds maximum allowed ceiling (3600s).",
                target_id="time_budget",
            ))

        if iter_budget <= 0:
            issues.append(TestPlanValidationIssue(
                code=PlanValidationCode.UNBOUNDED_PLAN,
                message=f"TestPlan iteration_budget must be positive (> 0), got {iter_budget}.",
                target_id="iteration_budget",
            ))
        elif iter_budget > wo_iter:
            issues.append(TestPlanValidationIssue(
                code=PlanValidationCode.BUDGET_EXCEEDED,
                message=f"TestPlan iteration_budget ({iter_budget}) exceeds WorkOrder authorization ({wo_iter}).",
                target_id="iteration_budget",
            ))
        elif iter_budget > 20:
            issues.append(TestPlanValidationIssue(
                code=PlanValidationCode.UNBOUNDED_PLAN,
                message=f"TestPlan iteration_budget ({iter_budget}) exceeds maximum safety ceiling (20).",
                target_id="iteration_budget",
            ))

        if max_cases < 0:
            issues.append(TestPlanValidationIssue(
                code=PlanValidationCode.UNBOUNDED_PLAN,
                message=f"TestPlan max_test_cases cannot be negative, got {max_cases}.",
                target_id="max_test_cases",
            ))
        elif max_cases > 100:
            issues.append(TestPlanValidationIssue(
                code=PlanValidationCode.UNBOUNDED_PLAN,
                message=f"TestPlan max_test_cases ({max_cases}) exceeds maximum safety ceiling (100).",
                target_id="max_test_cases",
            ))

        test_cases = list(getattr(test_plan, "test_cases", []) or [])
        if len(test_cases) > max_cases:
            issues.append(TestPlanValidationIssue(
                code=PlanValidationCode.BUDGET_EXCEEDED,
                message=f"TestPlan contains {len(test_cases)} cases, exceeding max_test_cases budget ({max_cases}).",
                target_id="test_cases",
            ))

        # Check metadata budget cap if present in work order
        wo_meta = getattr(work_order, "metadata", {}) or {}
        if isinstance(wo_meta, dict) and "max_test_cases" in wo_meta:
            meta_max = int(wo_meta["max_test_cases"])
            if len(test_cases) > meta_max:
                issues.append(TestPlanValidationIssue(
                    code=PlanValidationCode.BUDGET_EXCEEDED,
                    message=f"TestPlan contains {len(test_cases)} cases, exceeding WorkOrder metadata budget ({meta_max}).",
                    target_id="test_cases",
                ))

        # 4. Zero-Test Plan Handling
        if len(test_cases) == 0:
            req_cats = getattr(test_plan, "required_categories", []) or []
            acceptance_crit = getattr(work_order, "acceptance_criteria", []) or []
            # Valid zero-test plan if no required categories or no applicable categories
            is_legit_zero = (
                not req_cats
                or (test_context is not None and getattr(test_context, "is_test_only", False))
                or all(c in getattr(test_plan, "not_applicable_categories", []) for c in req_cats)
            )
            if not is_legit_zero and acceptance_crit:
                issues.append(TestPlanValidationIssue(
                    code=PlanValidationCode.INVALID_TEST_CASE,
                    message="TestPlan contains 0 test cases despite active acceptance criteria and required categories.",
                    target_id="test_cases",
                ))

        # 5. WorkOrder Capability Authorizations & Global Tester Boundary
        wo_caps_raw = getattr(work_order, "authorized_capabilities", []) or []
        authorized_caps: set[TestingCapability] = set()
        for c in wo_caps_raw:
            if isinstance(c, TestingCapability):
                authorized_caps.add(c)
            elif isinstance(c, str):
                try:
                    authorized_caps.add(TestingCapability(c.upper()))
                except (ValueError, KeyError):
                    pass

        # Check for forbidden actions and unauthorized capabilities
        for tc in test_cases:
            tc_id = getattr(tc, "test_case_id", "unknown")
            cat = getattr(tc, "category", None)
            if isinstance(cat, str):
                try:
                    cat = ApplicableTestCategory(cat.upper())
                except (ValueError, KeyError):
                    cat = None

            # Category required capabilities check
            if cat is not None:
                req_caps = self.CATEGORY_REQUIRED_CAPABILITIES.get(cat, [])
                missing_caps = [c for c in req_caps if c not in authorized_caps]
                if missing_caps:
                    issues.append(TestPlanValidationIssue(
                        code=PlanValidationCode.UNAUTHORIZED_CATEGORY,
                        message=f"TestCase '{tc_id}' requires capabilities {[c.value for c in missing_caps]} not authorized in WorkOrder.",
                        target_id=tc_id,
                    ))

            # Not applicable category check
            na_cats = set(getattr(test_plan, "not_applicable_categories", []) or [])
            if cat in na_cats:
                issues.append(TestPlanValidationIssue(
                    code=PlanValidationCode.UNAUTHORIZED_CATEGORY,
                    message=f"TestCase '{tc_id}' belongs to NOT_APPLICABLE category '{cat.value}'.",
                    target_id=tc_id,
                ))

            # Evidence expectation capability check
            ev_list = getattr(tc, "evidence_expectations", []) or []
            if "SCREENSHOT" in ev_list and TestingCapability.SCREENSHOT not in authorized_caps:
                issues.append(TestPlanValidationIssue(
                    code=PlanValidationCode.UNAUTHORIZED_CATEGORY,
                    message=f"TestCase '{tc_id}' expects SCREENSHOT evidence, but SCREENSHOT capability is not authorized.",
                    target_id=tc_id,
                ))
            if "VIDEO" in ev_list and TestingCapability.SCREEN_RECORDING not in authorized_caps:
                issues.append(TestPlanValidationIssue(
                    code=PlanValidationCode.UNAUTHORIZED_CATEGORY,
                    message=f"TestCase '{tc_id}' expects VIDEO evidence, but SCREEN_RECORDING capability is not authorized.",
                    target_id=tc_id,
                ))

            # Steps forbidden action check
            for step in getattr(tc, "steps", []) or []:
                action = str(getattr(step, "action", "") or "").upper()
                for forbidden in TESTER_FORBIDDEN_ACTIONS:
                    if forbidden.value in action:
                        issues.append(TestPlanValidationIssue(
                            code=PlanValidationCode.FORBIDDEN_CAPABILITY,
                            message=f"TestCase '{tc_id}' step {getattr(step, 'step_number', '?')} contains forbidden action '{action}'.",
                            target_id=tc_id,
                        ))

        # 6. Acceptance Criterion References
        wo_criteria = {
            getattr(ac, "criterion_id", ""): ac
            for ac in (getattr(work_order, "acceptance_criteria", []) or [])
        }
        for tc in test_cases:
            tc_id = getattr(tc, "test_case_id", "unknown")
            linkage = getattr(tc, "acceptance_linkage", []) or []
            for crit_id in linkage:
                if crit_id not in wo_criteria:
                    issues.append(TestPlanValidationIssue(
                        code=PlanValidationCode.MISSING_ACCEPTANCE_REFERENCE,
                        message=f"TestCase '{tc_id}' references non-existent acceptance criterion '{crit_id}'.",
                        target_id=tc_id,
                    ))

        # 7. Test-Case Completeness & Valid Expected Outcomes
        for tc in test_cases:
            tc_id = getattr(tc, "test_case_id", "")
            if not tc_id or not tc_id.strip():
                issues.append(TestPlanValidationIssue(
                    code=PlanValidationCode.INVALID_TEST_CASE,
                    message="TestCase missing test_case_id.",
                    target_id="test_case_id",
                ))

            objective = getattr(tc, "objective", "")
            if not objective or not objective.strip():
                issues.append(TestPlanValidationIssue(
                    code=PlanValidationCode.EMPTY_TEST_OBJECTIVE,
                    message=f"TestCase '{tc_id}' has empty objective.",
                    target_id=tc_id,
                ))

            expected_outcome = getattr(tc, "expected_outcome", "")
            if not expected_outcome or not expected_outcome.strip():
                issues.append(TestPlanValidationIssue(
                    code=PlanValidationCode.EMPTY_EXPECTED_OUTCOME,
                    message=f"TestCase '{tc_id}' has empty expected_outcome.",
                    target_id=tc_id,
                ))

            steps = getattr(tc, "steps", []) or []
            if not steps:
                issues.append(TestPlanValidationIssue(
                    code=PlanValidationCode.INVALID_TEST_CASE,
                    message=f"TestCase '{tc_id}' contains no test steps.",
                    target_id=tc_id,
                ))
            elif len(steps) > self.MAX_STEPS_PER_TEST_CASE:
                issues.append(TestPlanValidationIssue(
                    code=PlanValidationCode.UNBOUNDED_PLAN,
                    message=f"TestCase '{tc_id}' exceeds maximum step limit ({self.MAX_STEPS_PER_TEST_CASE} steps).",
                    target_id=tc_id,
                ))
            else:
                for step in steps:
                    s_num = getattr(step, "step_number", 0)
                    s_desc = getattr(step, "description", "")
                    if s_num < 1:
                        issues.append(TestPlanValidationIssue(
                            code=PlanValidationCode.INVALID_TEST_CASE,
                            message=f"TestCase '{tc_id}' step_number must be >= 1, got {s_num}.",
                            target_id=tc_id,
                        ))
                    if not s_desc or not s_desc.strip():
                        issues.append(TestPlanValidationIssue(
                            code=PlanValidationCode.INVALID_TEST_CASE,
                            message=f"TestCase '{tc_id}' step {s_num} has empty description.",
                            target_id=tc_id,
                        ))

            # Evidence expectations valid vocabulary check
            for ev in getattr(tc, "evidence_expectations", []) or []:
                if str(ev).upper() not in self.VALID_EVIDENCE_TYPES:
                    issues.append(TestPlanValidationIssue(
                        code=PlanValidationCode.INVALID_EVIDENCE_REQUIREMENT,
                        message=f"TestCase '{tc_id}' specifies unrecognized evidence expectation '{ev}'.",
                        target_id=tc_id,
                    ))

        # 8. Scope Containment Check
        test_scope = getattr(work_order, "test_scope", None)
        if test_scope is not None:
            allowed_routes = set(getattr(test_scope, "routes", []) or [])
            allowed_components = set(getattr(test_scope, "components", []) or [])

            for tc in test_cases:
                tc_id = getattr(tc, "test_case_id", "unknown")
                cat = getattr(tc, "category", None)
                cat_val = cat.value if hasattr(cat, "value") else str(cat)

                # Navigation scope check
                if allowed_routes:
                    if cat_val == "NAVIGATION":
                        auth_ref = getattr(tc, "authorization_reference", "")
                        if auth_ref and auth_ref not in allowed_routes:
                            issues.append(TestPlanValidationIssue(
                                code=PlanValidationCode.OUT_OF_SCOPE,
                                message=f"TestCase '{tc_id}' targets route '{auth_ref}' outside authorized routes {sorted(allowed_routes)}.",
                                target_id=tc_id,
                            ))
                    for step in getattr(tc, "steps", []) or []:
                        s_act = getattr(step, "action", "")
                        s_tgt = getattr(step, "target", "")
                        if s_act == "NAVIGATE" and s_tgt and s_tgt not in allowed_routes:
                            issues.append(TestPlanValidationIssue(
                                code=PlanValidationCode.OUT_OF_SCOPE,
                                message=f"TestCase '{tc_id}' step navigates to unauthorized route '{s_tgt}'.",
                                target_id=tc_id,
                            ))

                # Component scope check
                auth_ref = getattr(tc, "authorization_reference", "")
                if allowed_components and auth_ref and not auth_ref.startswith("ac-") and cat_val in {"UI_INTERACTION", "VISUAL"}:
                    if auth_ref not in allowed_components and not any(c.lower() in auth_ref.lower() for c in allowed_components):
                        issues.append(TestPlanValidationIssue(
                            code=PlanValidationCode.OUT_OF_SCOPE,
                            message=f"TestCase '{tc_id}' targets component '{auth_ref}' outside authorized components {sorted(allowed_components)}.",
                            target_id=tc_id,
                        ))

        # 9. No Duplicate Meaningless Tests Check
        seen_ids: set[str] = set()
        seen_signatures: dict[tuple[str, str, str], str] = {}

        for tc in test_cases:
            tc_id = getattr(tc, "test_case_id", "")
            if tc_id in seen_ids:
                issues.append(TestPlanValidationIssue(
                    code=PlanValidationCode.DUPLICATE_TEST_CASE,
                    message=f"Duplicate test_case_id '{tc_id}' in TestPlan.",
                    target_id=tc_id,
                ))
            seen_ids.add(tc_id)

            cat_str = getattr(tc.category, "value", str(tc.category))
            sig = (cat_str, getattr(tc, "objective", "").strip().lower(), getattr(tc, "authorization_reference", ""))
            if sig in seen_signatures:
                prior_id = seen_signatures[sig]
                issues.append(TestPlanValidationIssue(
                    code=PlanValidationCode.DUPLICATE_TEST_CASE,
                    message=f"TestCase '{tc_id}' is a duplicate of '{prior_id}' with identical category, objective, and target.",
                    target_id=tc_id,
                ))
            else:
                seen_signatures[sig] = tc_id

        # 10. No Recursive Test Definitions / Dependency Cycles
        deps_map: dict[str, list[str]] = {}
        for tc in test_cases:
            tc_id = getattr(tc, "test_case_id", "")
            deps = list(getattr(tc, "dependencies", []) or [])
            deps_map[tc_id] = deps

            if tc_id in deps:
                issues.append(TestPlanValidationIssue(
                    code=PlanValidationCode.RECURSIVE_TEST_DEFINITION,
                    message=f"TestCase '{tc_id}' has self-referential dependency.",
                    target_id=tc_id,
                ))

            for d in deps:
                if d not in seen_ids:
                    issues.append(TestPlanValidationIssue(
                        code=PlanValidationCode.INVALID_TEST_CASE,
                        message=f"TestCase '{tc_id}' references non-existent dependency '{d}'.",
                        target_id=tc_id,
                    ))

        # Cycle detection
        visited: set[str] = set()
        recursion_stack: set[str] = set()

        def detect_cycle(node: str, path: list[str]) -> bool:
            visited.add(node)
            recursion_stack.add(node)
            path.append(node)

            for neighbor in deps_map.get(node, []):
                if neighbor not in visited:
                    if detect_cycle(neighbor, path):
                        return True
                elif neighbor in recursion_stack:
                    path.append(neighbor)
                    cycle_slice = path[path.index(neighbor):]
                    issues.append(TestPlanValidationIssue(
                        code=PlanValidationCode.RECURSIVE_TEST_DEFINITION,
                        message=f"Dependency cycle detected involving test cases {cycle_slice}.",
                        target_id=node,
                    ))
                    return True

            recursion_stack.remove(node)
            path.pop()
            return False

        for node in deps_map:
            if node not in visited:
                detect_cycle(node, [])

        # 11. Required Runtime Capabilities Availability Check
        avail_caps_set: Optional[set[TestingCapability]] = None
        if runtime_capabilities is not None:
            avail_caps_set = set()
            for rc in runtime_capabilities:
                if isinstance(rc, TestingCapability):
                    avail_caps_set.add(rc)
                elif isinstance(rc, str):
                    try:
                        avail_caps_set.add(TestingCapability(rc.upper()))
                    except (ValueError, KeyError):
                        pass

        if avail_caps_set is not None:
            for tc in test_cases:
                tc_id = getattr(tc, "test_case_id", "unknown")
                cat = getattr(tc, "category", None)
                if cat is not None:
                    req_caps = self.CATEGORY_REQUIRED_CAPABILITIES.get(cat, [])
                    missing_rt = [c for c in req_caps if c not in avail_caps_set]
                    if missing_rt:
                        issues.append(TestPlanValidationIssue(
                            code=PlanValidationCode.MISSING_RUNTIME_CAPABILITY,
                            message=f"TestCase '{tc_id}' requires runtime capability {[c.value for c in missing_rt]} which is unavailable in runtime.",
                            target_id=tc_id,
                        ))

                ev_list = getattr(tc, "evidence_expectations", []) or []
                if "SCREENSHOT" in ev_list and TestingCapability.SCREENSHOT not in avail_caps_set:
                    issues.append(TestPlanValidationIssue(
                        code=PlanValidationCode.MISSING_RUNTIME_CAPABILITY,
                        message=f"TestCase '{tc_id}' expects SCREENSHOT, but SCREENSHOT capability is unavailable in runtime.",
                        target_id=tc_id,
                    ))
                if "VIDEO" in ev_list and TestingCapability.SCREEN_RECORDING not in avail_caps_set:
                    issues.append(TestPlanValidationIssue(
                        code=PlanValidationCode.MISSING_RUNTIME_CAPABILITY,
                        message=f"TestCase '{tc_id}' expects VIDEO, but SCREEN_RECORDING capability is unavailable in runtime.",
                        target_id=tc_id,
                    ))

        # 12. Required Environment Availability & Reachability Check
        env = environment
        if env is None and test_context is not None:
            env = getattr(test_context, "target_environment", None)

        if len(test_cases) > 0:
            if env is None:
                issues.append(TestPlanValidationIssue(
                    code=PlanValidationCode.MISSING_ENVIRONMENT,
                    message="TestPlan has executable test cases but target environment is missing or unspecified.",
                    target_id="environment",
                ))
            else:
                is_avail = getattr(env, "is_available", True)
                is_reach = getattr(env, "is_reachable", True)
                if not is_avail or not is_reach:
                    issues.append(TestPlanValidationIssue(
                        code=PlanValidationCode.MISSING_ENVIRONMENT,
                        message=f"Target environment '{getattr(env, 'environment_id', 'unknown')}' is unavailable or unreachable.",
                        target_id="environment",
                    ))

                if test_scope is not None:
                    allowed_envs = set(getattr(test_scope, "environments", []) or [])
                    env_id = getattr(env, "environment_id", "")
                    env_name = getattr(env, "env_name", "")
                    env_type = getattr(env, "environment_type", "")
                    env_type_val = getattr(env_type, "value", str(env_type))
                    if allowed_envs and env_id not in allowed_envs and env_name not in allowed_envs and env_type not in allowed_envs and env_type_val not in allowed_envs:
                        issues.append(TestPlanValidationIssue(
                            code=PlanValidationCode.OUT_OF_SCOPE,
                            message=f"Target environment '{env_id or env_name or env_type}' is outside authorized environments {sorted(allowed_envs)}.",
                            target_id="environment",
                        ))

        # 13. Summarize Outcome
        error_issues = [i for i in issues if i.severity == ValidationIssueSeverity.ERROR]
        is_valid = len(error_issues) == 0

        p_id = getattr(test_plan, "plan_id", "tplan-unknown")
        rep_id = new_validation_report_id()

        if len(test_cases) == 0 and is_valid:
            summary = "Valid zero-test plan: NO_APPLICABLE_TESTS for current execution scope."
        elif is_valid:
            summary = f"TestPlan '{p_id}' validated successfully with 0 errors across {len(test_cases)} test cases."
        else:
            unique_codes = sorted(list({i.code.value for i in error_issues}))
            summary = f"TestPlan '{p_id}' validation failed with {len(error_issues)} errors: {unique_codes}."

        result = TestPlanValidationResult(
            report_id=rep_id,
            plan_id=p_id,
            work_order_id=wo_id,
            execution_id=plan_exec,
            project_id=plan_proj,
            is_valid=is_valid,
            issues=issues,
            summary=summary,
            provenance={
                "work_order_id": wo_id,
                "project_id": plan_proj,
                "execution_id": plan_exec,
                "plan_id": p_id,
            },
            created_at=utc_now(),
        )

        return result
