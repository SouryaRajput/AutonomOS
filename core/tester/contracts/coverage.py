from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import logging
from typing import Any, Optional, Sequence

from core.tester.contracts.applicability import TestApplicabilityClassifier, TestApplicabilityReport
from core.tester.contracts.identifiers import (
    new_coverage_report_id,
    new_gap_id,
    validate_coverage_report_id,
    validate_execution_id,
    validate_gap_id,
    validate_plan_id,
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
    CoverageGapReason,
    CoverageGapSeverity,
    CoverageState,
    TestCategory,
    TestPriority,
    TestSurface,
    TestingCapability,
)

logger = logging.getLogger("AutonomOS.TestCoverage")


def utc_now() -> str:
    """Generate ISO 8601 UTC timestamp."""
    return datetime.now(timezone.utc).isoformat()


@dataclass
class CoverageGap:
    """
    Explicit, auditable record of an uncovered acceptance criterion or changed surface.
    Identifies the target, severity, root cause reason, and provenance.

    IMPORTANT: A coverage gap is information, NOT permission to expand scope.
    """
    __test__ = False
    gap_id: str
    target_type: str  # "ACCEPTANCE_CRITERION" | "SURFACE" | "CATEGORY"
    target_id: str    # e.g., "ac-auth-01", "ANIMATION", "UI"
    reason: CoverageGapReason
    severity: CoverageGapSeverity
    description: str
    provenance: dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=utc_now)

    def __post_init__(self) -> None:
        validate_gap_id(self.gap_id)
        if not self.target_id or not self.target_id.strip():
            raise TesterValidationError("CoverageGap must have a valid non-empty target_id.", field_name="target_id")
        if not self.description or not self.description.strip():
            raise TesterValidationError("CoverageGap must have a non-empty description.", field_name="description")

        if isinstance(self.reason, str):
            try:
                self.reason = CoverageGapReason(self.reason.upper())
            except (ValueError, KeyError):
                self.reason = CoverageGapReason.MISSING_TEST

        if isinstance(self.severity, str):
            try:
                self.severity = CoverageGapSeverity(self.severity.upper())
            except (ValueError, KeyError):
                self.severity = CoverageGapSeverity.MEDIUM

    def to_dict(self) -> dict[str, Any]:
        return {
            "gap_id": self.gap_id,
            "target_type": self.target_type,
            "target_id": self.target_id,
            "reason": self.reason.value,
            "severity": self.severity.value,
            "description": self.description,
            "provenance": dict(self.provenance),
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CoverageGap:
        raw_reason = data.get("reason", CoverageGapReason.MISSING_TEST.value)
        try:
            reason = CoverageGapReason(str(raw_reason).upper())
        except (ValueError, KeyError):
            reason = CoverageGapReason.MISSING_TEST

        raw_sev = data.get("severity", CoverageGapSeverity.MEDIUM.value)
        try:
            severity = CoverageGapSeverity(str(raw_sev).upper())
        except (ValueError, KeyError):
            severity = CoverageGapSeverity.MEDIUM

        return cls(
            gap_id=str(data.get("gap_id", new_gap_id())),
            target_type=str(data.get("target_type", "ACCEPTANCE_CRITERION")),
            target_id=str(data.get("target_id", "")),
            reason=reason,
            severity=severity,
            description=str(data.get("description", "")),
            provenance=dict(data.get("provenance", {})),
            created_at=str(data.get("created_at", utc_now())),
        )


@dataclass
class CriterionCoverage:
    """
    Coverage status of an individual AcceptanceCriterion.
    """
    __test__ = False
    criterion_id: str
    description: str
    state: CoverageState
    covering_test_case_ids: list[str] = field(default_factory=list)
    rationale: str = ""
    gap: Optional[CoverageGap] = None

    def __post_init__(self) -> None:
        if isinstance(self.state, str):
            try:
                self.state = CoverageState(self.state.upper())
            except (ValueError, KeyError):
                self.state = CoverageState.UNKNOWN
        self.covering_test_case_ids = list(self.covering_test_case_ids)

    def to_dict(self) -> dict[str, Any]:
        return {
            "criterion_id": self.criterion_id,
            "description": self.description,
            "state": self.state.value,
            "covering_test_case_ids": list(self.covering_test_case_ids),
            "rationale": self.rationale,
            "gap": self.gap.to_dict() if self.gap else None,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CriterionCoverage:
        raw_state = data.get("state", CoverageState.UNKNOWN.value)
        try:
            state = CoverageState(str(raw_state).upper())
        except (ValueError, KeyError):
            state = CoverageState.UNKNOWN

        raw_gap = data.get("gap")
        gap = CoverageGap.from_dict(raw_gap) if isinstance(raw_gap, dict) else None

        return cls(
            criterion_id=str(data.get("criterion_id", "")),
            description=str(data.get("description", "")),
            state=state,
            covering_test_case_ids=list(data.get("covering_test_case_ids", [])),
            rationale=str(data.get("rationale", "")),
            gap=gap,
        )


@dataclass
class SurfaceCoverage:
    """
    Coverage status of an architectural product surface.
    """
    __test__ = False
    surface: TestSurface
    state: CoverageState
    covering_test_case_ids: list[str] = field(default_factory=list)
    rationale: str = ""
    gap: Optional[CoverageGap] = None

    def __post_init__(self) -> None:
        if isinstance(self.surface, str):
            try:
                self.surface = TestSurface(self.surface.upper())
            except (ValueError, KeyError):
                self.surface = TestSurface.UI
        if isinstance(self.state, str):
            try:
                self.state = CoverageState(self.state.upper())
            except (ValueError, KeyError):
                self.state = CoverageState.UNKNOWN
        self.covering_test_case_ids = list(self.covering_test_case_ids)

    def to_dict(self) -> dict[str, Any]:
        return {
            "surface": self.surface.value,
            "state": self.state.value,
            "covering_test_case_ids": list(self.covering_test_case_ids),
            "rationale": self.rationale,
            "gap": self.gap.to_dict() if self.gap else None,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SurfaceCoverage:
        raw_surf = data.get("surface", TestSurface.UI.value)
        try:
            surface = TestSurface(str(raw_surf).upper())
        except (ValueError, KeyError):
            surface = TestSurface.UI

        raw_state = data.get("state", CoverageState.UNKNOWN.value)
        try:
            state = CoverageState(str(raw_state).upper())
        except (ValueError, KeyError):
            state = CoverageState.UNKNOWN

        raw_gap = data.get("gap")
        gap = CoverageGap.from_dict(raw_gap) if isinstance(raw_gap, dict) else None

        return cls(
            surface=surface,
            state=state,
            covering_test_case_ids=list(data.get("covering_test_case_ids", [])),
            rationale=str(data.get("rationale", "")),
            gap=gap,
        )


@dataclass
class TestCoverageReport:
    """
    Authoritative evaluation of test coverage and gaps for a TestPlan.
    Contains criterion-by-criterion coverage, surface coverage, and explicit gaps.
    """
    __test__ = False
    report_id: str
    plan_id: str
    work_order_id: str
    execution_id: str
    project_id: str
    criteria_coverage: dict[str, CriterionCoverage] = field(default_factory=dict)
    surface_coverage: dict[TestSurface, SurfaceCoverage] = field(default_factory=dict)
    category_coverage: dict[str, CoverageState] = field(default_factory=dict)
    coverage_gaps: list[CoverageGap] = field(default_factory=list)
    provenance: dict[str, Any] = field(default_factory=dict)
    trace: dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=utc_now)

    def __post_init__(self) -> None:
        self.validate()

    def validate(self) -> None:
        validate_coverage_report_id(self.report_id)
        validate_plan_id(self.plan_id)
        validate_work_order_id(self.work_order_id)
        validate_execution_id(self.execution_id)
        if not self.project_id or not self.project_id.strip():
            raise TesterLineageError("TestCoverageReport requires a valid non-empty project_id.")
        if self.provenance and isinstance(self.provenance, dict):
            prov_proj = self.provenance.get("project_id")
            if prov_proj and prov_proj != self.project_id:
                raise TesterLineageError(
                    f"Lineage mismatch between project_id ({self.project_id}) and provenance ({prov_proj})."
                )

    @property
    def covered_criteria(self) -> list[str]:
        """List of criterion IDs that are fully covered."""
        return sorted([
            cid for cid, cov in self.criteria_coverage.items()
            if cov.state == CoverageState.COVERED
        ])

    @property
    def partially_covered_criteria(self) -> list[str]:
        """List of criterion IDs that are partially covered."""
        return sorted([
            cid for cid, cov in self.criteria_coverage.items()
            if cov.state == CoverageState.PARTIALLY_COVERED
        ])

    @property
    def uncovered_criteria(self) -> list[str]:
        """List of criterion IDs that are uncovered."""
        return sorted([
            cid for cid, cov in self.criteria_coverage.items()
            if cov.state == CoverageState.UNCOVERED
        ])

    @property
    def not_applicable_criteria(self) -> list[str]:
        """List of criterion IDs classified as not applicable."""
        return sorted([
            cid for cid, cov in self.criteria_coverage.items()
            if cov.state == CoverageState.NOT_APPLICABLE
        ])

    @property
    def critical_gaps(self) -> list[CoverageGap]:
        """List of coverage gaps with CRITICAL severity."""
        return [g for g in self.coverage_gaps if g.severity == CoverageGapSeverity.CRITICAL]

    @property
    def is_fully_covered(self) -> bool:
        """True if all criteria and surfaces are covered or not applicable, with no uncovered items."""
        for cov in self.criteria_coverage.values():
            if cov.state in {CoverageState.UNCOVERED, CoverageState.PARTIALLY_COVERED}:
                return False
        for scov in self.surface_coverage.values():
            if scov.state in {CoverageState.UNCOVERED, CoverageState.PARTIALLY_COVERED}:
                return False
        return True

    def get_criterion_coverage(self, criterion_id: str) -> Optional[CriterionCoverage]:
        return self.criteria_coverage.get(criterion_id)

    def get_surface_coverage(self, surface: TestSurface) -> Optional[SurfaceCoverage]:
        return self.surface_coverage.get(surface)

    def to_dict(self) -> dict[str, Any]:
        return {
            "report_id": self.report_id,
            "plan_id": self.plan_id,
            "work_order_id": self.work_order_id,
            "execution_id": self.execution_id,
            "project_id": self.project_id,
            "criteria_coverage": {cid: c.to_dict() for cid, c in sorted(self.criteria_coverage.items())},
            "surface_coverage": {s.value: c.to_dict() for s, c in sorted(self.surface_coverage.items(), key=lambda x: x[0].value)},
            "category_coverage": {cat: st.value if hasattr(st, "value") else str(st) for cat, st in sorted(self.category_coverage.items())},
            "coverage_gaps": [g.to_dict() for g in self.coverage_gaps],
            "provenance": dict(self.provenance),
            "trace": dict(self.trace),
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> TestCoverageReport:
        crit_cov = {}
        for cid, cd in data.get("criteria_coverage", {}).items():
            if isinstance(cd, dict):
                crit_cov[cid] = CriterionCoverage.from_dict(cd)

        surf_cov = {}
        for s_raw, sd in data.get("surface_coverage", {}).items():
            try:
                s_enum = TestSurface(str(s_raw).upper())
                if isinstance(sd, dict):
                    surf_cov[s_enum] = SurfaceCoverage.from_dict(sd)
            except (ValueError, KeyError):
                pass

        cat_cov = {}
        for cat, st_raw in data.get("category_coverage", {}).items():
            try:
                cat_cov[cat] = CoverageState(str(st_raw).upper())
            except (ValueError, KeyError):
                cat_cov[cat] = CoverageState.UNKNOWN

        gaps = [CoverageGap.from_dict(g) if isinstance(g, dict) else g for g in data.get("coverage_gaps", [])]

        return cls(
            report_id=str(data.get("report_id", new_coverage_report_id())),
            plan_id=str(data.get("plan_id", "")),
            work_order_id=str(data.get("work_order_id", "")),
            execution_id=str(data.get("execution_id", "")),
            project_id=str(data.get("project_id", "")),
            criteria_coverage=crit_cov,
            surface_coverage=surf_cov,
            category_coverage=cat_cov,
            coverage_gaps=gaps,
            provenance=dict(data.get("provenance", {})),
            trace=dict(data.get("trace", {})),
            created_at=str(data.get("created_at", utc_now())),
        )


class TestPrioritizer:
    """
    Deterministic, explainable prioritization and ordering engine for test cases.

    Prioritizes test cases based on:
    1. Acceptance criticality (+100 for CRITICAL, +60 for HIGH, +30 for MEDIUM, +10 for LOW)
    2. Direct relation to changed behavior (+40)
    3. Category risk level (+30 for Functional/Auth/Security, +25 for Integration, +15 for Visual, +10 for Animation)
    4. Explicit dependencies (topological sort ensures prerequisite tests precede dependent tests)
    5. Optional/secondary coverage penalties (-10)
    """

    @classmethod
    def calculate_priority_score(
        cls,
        test_case: Any,
        changed_components: Optional[Sequence[str]] = None,
        changed_modules: Optional[Sequence[str]] = None,
        changed_routes: Optional[Sequence[str]] = None,
    ) -> tuple[int, str]:
        """
        Compute an explainable, deterministic priority score and rationale for a TestCase.
        """
        score = 0
        reasons = []

        # 1. Acceptance Criticality
        prio = getattr(test_case, "priority", TestPriority.MEDIUM)
        if prio == TestPriority.CRITICAL or getattr(test_case, "acceptance_linkage", None):
            score += 100
            reasons.append(f"Critical Acceptance Linkage ({test_case.acceptance_linkage}) [+100]")
        elif prio == TestPriority.HIGH:
            score += 60
            reasons.append("High Priority Test Case [+60]")
        elif prio == TestPriority.MEDIUM:
            score += 30
            reasons.append("Medium Priority Test Case [+30]")
        else:
            score += 10
            reasons.append("Low Priority Test Case [+10]")

        # 2. Direct Relation to Changed Behavior
        changed_comps = set(changed_components or [])
        changed_mods = set(changed_modules or [])
        changed_rts = set(changed_routes or [])

        target = getattr(test_case, "authorization_reference", "") or ""
        obj = (getattr(test_case, "objective", "") or "").lower()

        is_direct_change = False
        if target and (target in changed_comps or target in changed_mods or target in changed_rts):
            is_direct_change = True
        elif any(c.lower() in obj for c in changed_comps) or any(r.lower() in obj for r in changed_rts):
            is_direct_change = True

        if is_direct_change:
            score += 40
            reasons.append("Directly Targets Changed Component/Route/Module [+40]")

        # 3. Category Risk Level
        cat = getattr(test_case, "category", ApplicableTestCategory.FUNCTIONAL)
        cat_enum = cat if isinstance(cat, ApplicableTestCategory) else ApplicableTestCategory(str(cat).upper())

        if cat_enum == ApplicableTestCategory.FUNCTIONAL:
            score += 30
            reasons.append("Functional Correctness Verification [+30]")
        elif cat_enum == ApplicableTestCategory.INTEGRATION:
            score += 25
            reasons.append("Cross-Layer Integration Flow [+25]")
        elif cat_enum == ApplicableTestCategory.UI_INTERACTION:
            score += 20
            reasons.append("Interactive User Control Flow [+20]")
        elif cat_enum in {ApplicableTestCategory.VISUAL, ApplicableTestCategory.RESPONSIVE}:
            score += 15
            reasons.append("Visual Presentation / Responsive Verification [+15]")
        elif cat_enum == ApplicableTestCategory.ANIMATION:
            score += 10
            reasons.append("Animation / Transition Verification [+10]")

        # 4. Secondary / Exploratory penalty
        if "exploratory" in obj or "secondary" in obj:
            score -= 10
            reasons.append("Secondary / Exploratory Check [-10]")

        rationale = " + ".join(reasons) + f" = Total {score}"
        return score, rationale

    @classmethod
    def order_test_cases(
        cls,
        test_cases: Sequence[Any],
        changed_components: Optional[Sequence[str]] = None,
        changed_modules: Optional[Sequence[str]] = None,
        changed_routes: Optional[Sequence[str]] = None,
    ) -> list[Any]:
        """
        Deterministically sort test cases respecting explicit dependencies (topological order)
        and falling back to priority score descending.
        """
        cases_by_id = {tc.test_case_id: tc for tc in test_cases}

        # Calculate scores if not already set
        for tc in test_cases:
            score, rationale = cls.calculate_priority_score(
                tc,
                changed_components=changed_components,
                changed_modules=changed_modules,
                changed_routes=changed_routes,
            )
            if hasattr(tc, "priority_score"):
                tc.priority_score = score
            if hasattr(tc, "priority_rationale"):
                tc.priority_rationale = rationale

        # Dependency-aware Topological Sort
        # Build dependency graph
        deps = {}
        for tc in test_cases:
            # Only count dependencies that actually exist in the candidate list
            valid_deps = [d for d in getattr(tc, "dependencies", []) if d in cases_by_id]
            deps[tc.test_case_id] = set(valid_deps)

        ordered: list[Any] = []
        visited = set()
        in_progress = set()

        # To ensure determinism, sort nodes before traversal by score descending, then objective, then id
        def sort_key(tc: Any):
            return (
                -getattr(tc, "priority_score", 0),
                getattr(tc, "objective", ""),
                tc.test_case_id,
            )

        sorted_cases = sorted(test_cases, key=sort_key)

        def visit(tc_id: str) -> None:
            if tc_id in visited:
                return
            if tc_id in in_progress:
                # Cycle detected: break cycle deterministically
                logger.warning(f"Circular dependency detected involving test case '{tc_id}'; breaking cycle.")
                return

            in_progress.add(tc_id)
            # Visit prerequisites first in sorted order
            prereqs = sorted([cases_by_id[d] for d in deps.get(tc_id, set()) if d in cases_by_id], key=sort_key)
            for prereq in prereqs:
                visit(prereq.test_case_id)

            in_progress.remove(tc_id)
            visited.add(tc_id)
            ordered.append(cases_by_id[tc_id])

        for tc in sorted_cases:
            if tc.test_case_id not in visited:
                visit(tc.test_case_id)

        return ordered


class TestCoverageEvaluator:
    """
    Deterministic evaluator that maps AcceptanceCriteria and ChangedSurfaces
    to TestCases in a TestPlan, identifying coverage states and explicit CoverageGaps.
    """

    SURFACE_CATEGORY_MAPPING: dict[TestSurface, set[ApplicableTestCategory]] = {
        TestSurface.UI: {ApplicableTestCategory.UI_INTERACTION, ApplicableTestCategory.VISUAL, ApplicableTestCategory.RESPONSIVE},
        TestSurface.USER_INTERACTION: {ApplicableTestCategory.UI_INTERACTION},
        TestSurface.NAVIGATION: {ApplicableTestCategory.NAVIGATION},
        TestSurface.VISUAL_LAYOUT: {ApplicableTestCategory.VISUAL, ApplicableTestCategory.RESPONSIVE},
        TestSurface.RESPONSIVE_LAYOUT: {ApplicableTestCategory.RESPONSIVE},
        TestSurface.ANIMATION: {ApplicableTestCategory.ANIMATION},
        TestSurface.API: {ApplicableTestCategory.FUNCTIONAL, ApplicableTestCategory.INTEGRATION},
        TestSurface.BUSINESS_LOGIC: {ApplicableTestCategory.FUNCTIONAL},
        TestSurface.INTEGRATION: {ApplicableTestCategory.INTEGRATION},
        TestSurface.DATA_FLOW: {ApplicableTestCategory.INTEGRATION, ApplicableTestCategory.FUNCTIONAL},
        TestSurface.PERFORMANCE: {ApplicableTestCategory.PERFORMANCE},
        TestSurface.VIDEO: {ApplicableTestCategory.VIDEO, ApplicableTestCategory.ANIMATION},
        TestSurface.OCR: {ApplicableTestCategory.OCR},
    }

    def __init__(self, classifier: Optional[TestApplicabilityClassifier] = None) -> None:
        self.classifier = classifier or TestApplicabilityClassifier()

    def evaluate(
        self,
        work_order: Any,
        test_context: Any,
        test_plan: Any,
        applicability_report: Optional[TestApplicabilityReport] = None,
    ) -> TestCoverageReport:
        """
        Evaluate coverage of a TestPlan against WorkOrder acceptance criteria and Context surfaces.
        Produces a complete TestCoverageReport with explicit CoverageGaps.
        """
        proj_id = getattr(test_plan, "project_id", "") or getattr(work_order, "project_id", "")
        wo_id = getattr(test_plan, "work_order_id", "") or getattr(work_order, "work_order_id", "")
        exec_id = getattr(test_plan, "execution_id", "") or getattr(work_order, "execution_id", "")

        # 1. Resolve applicability report
        if applicability_report is None:
            applicability_report = self.classifier.classify(work_order=work_order, test_context=test_context)

        req_cats = set(applicability_report.required_categories)
        opt_cats = set(applicability_report.optional_categories)
        na_cats = set(applicability_report.not_applicable_categories)
        classifications = getattr(applicability_report, "classifications", {}) or {}
        blocked_cats = {
            cat: (ca.blocked_reason or "runtime capability unavailable")
            for cat, ca in classifications.items()
            if getattr(ca, "is_blocked", False)
        }
        for b in getattr(applicability_report, "blocked_categories", []) or []:
            if isinstance(b, ApplicableTestCategory) and b not in blocked_cats:
                blocked_cats[b] = "runtime capability unavailable"

        authorized_caps = set(getattr(work_order, "authorized_capabilities", []) or [])

        plan_cases = list(getattr(test_plan, "test_cases", []) or [])

        criteria_cov: dict[str, CriterionCoverage] = {}
        surface_cov: dict[TestSurface, SurfaceCoverage] = {}
        category_cov: dict[str, CoverageState] = {}
        gaps: list[CoverageGap] = []

        # 2. Evaluate Acceptance Criteria Coverage
        criteria = getattr(work_order, "acceptance_criteria", []) or []
        for ac in criteria:
            crit_id = getattr(ac, "criterion_id", "ac-general")
            crit_desc = getattr(ac, "description", "")
            crit_lower = crit_desc.lower()

            # Find matching test cases in plan
            covering_cases = []
            for tc in plan_cases:
                # Linkage match
                if crit_id in getattr(tc, "acceptance_linkage", []):
                    covering_cases.append(tc)
                # Auth reference match
                elif getattr(tc, "authorization_reference", "") == crit_id:
                    covering_cases.append(tc)
                # Substring/objective match
                elif crit_desc and (crit_lower in tc.objective.lower() or tc.objective.lower() in crit_lower):
                    covering_cases.append(tc)

            if covering_cases:
                # Criterion is covered or partially covered
                # If test has steps verifying expectation, it's COVERED; if exploratory or 1 step, PARTIALLY_COVERED
                is_partial = any(len(getattr(tc, "steps", [])) < 2 for tc in covering_cases)
                state = CoverageState.PARTIALLY_COVERED if is_partial else CoverageState.COVERED
                rationale = f"Covered by test cases {[tc.test_case_id for tc in covering_cases]}"
                criteria_cov[crit_id] = CriterionCoverage(
                    criterion_id=crit_id,
                    description=crit_desc,
                    state=state,
                    covering_test_case_ids=[tc.test_case_id for tc in covering_cases],
                    rationale=rationale,
                    gap=None,
                )
            else:
                # Uncovered or Not Applicable
                # Determine WHY it is uncovered
                cat = self._infer_criterion_category(crit_desc)

                if self._is_unauthorized(cat, authorized_caps) or (cat in classifications and not classifications[cat].is_authorized):
                    state = CoverageState.UNCOVERED
                    reason = CoverageGapReason.AUTHORIZATION_LIMITATION
                    severity = CoverageGapSeverity.HIGH
                    desc = f"Criterion '{crit_id}' uncovered because required capability for '{cat.value}' is not authorized in WorkOrder."
                elif cat in blocked_cats:
                    state = CoverageState.UNCOVERED
                    reason = CoverageGapReason.UNAVAILABLE_CAPABILITY
                    severity = CoverageGapSeverity.HIGH
                    desc = f"Criterion '{crit_id}' uncovered because runtime capability '{blocked_cats[cat]}' is unavailable."
                elif cat in na_cats:
                    state = CoverageState.NOT_APPLICABLE
                    reason = CoverageGapReason.NOT_APPLICABLE
                    severity = CoverageGapSeverity.LOW
                    desc = f"Criterion '{crit_id}' corresponds to not applicable category '{cat.value}'."
                elif getattr(test_context, "is_context_unknown", False) if hasattr(test_context, "is_context_unknown") else False:
                    state = CoverageState.UNKNOWN
                    reason = CoverageGapReason.INSUFFICIENT_CONTEXT
                    severity = CoverageGapSeverity.MEDIUM
                    desc = f"Criterion '{crit_id}' coverage unknown due to insufficient architectural context."
                else:
                    state = CoverageState.UNCOVERED
                    reason = CoverageGapReason.MISSING_TEST
                    severity = CoverageGapSeverity.CRITICAL
                    desc = f"Criterion '{crit_id}' has no covering test case in TestPlan."
                c_suffix = crit_id[len("ac-"):] if crit_id.startswith("ac-") else crit_id
                gap = CoverageGap(
                    gap_id=f"tgap-{c_suffix}",
                    target_type="ACCEPTANCE_CRITERION",
                    target_id=crit_id,
                    reason=reason,
                    severity=severity,
                    description=desc,
                    provenance={
                        "work_order_id": wo_id,
                        "project_id": proj_id,
                        "execution_id": exec_id,
                        "criterion_id": crit_id,
                    },
                )
                gaps.append(gap)
                criteria_cov[crit_id] = CriterionCoverage(
                    criterion_id=crit_id,
                    description=crit_desc,
                    state=state,
                    covering_test_case_ids=[],
                    rationale=desc,
                    gap=gap,
                )

        # 3. Evaluate Changed Surface Coverage
        surfaces = getattr(test_context, "test_surfaces", []) or []
        # If no explicit test_surfaces, deduce from presence assessment
        surfaces_to_check: set[TestSurface] = set()
        for s in surfaces:
            if getattr(s, "is_available", False) or getattr(s, "status", None) == "PRESENT":
                surfaces_to_check.add(getattr(s, "surface", TestSurface.UI))

        # Always check UI if frontend is present
        if getattr(getattr(test_context, "frontend", None), "is_present", False):
            surfaces_to_check.add(TestSurface.UI)
            surfaces_to_check.add(TestSurface.USER_INTERACTION)
        # Always check API if API/backend is present and changed
        if getattr(getattr(test_context, "backend", None), "is_present", False):
            surfaces_to_check.add(TestSurface.BUSINESS_LOGIC)
        if getattr(test_context, "changed_routes", None):
            surfaces_to_check.add(TestSurface.NAVIGATION)

        for surf in sorted(surfaces_to_check, key=lambda x: x.value):
            matching_cats = self.SURFACE_CATEGORY_MAPPING.get(surf, set())
            covering_cases = []
            for tc in plan_cases:
                tc_surfs = set(getattr(tc, "covered_surfaces", []))
                if surf in tc_surfs:
                    covering_cases.append(tc)
                elif getattr(tc, "category", None) in matching_cats:
                    covering_cases.append(tc)

            if covering_cases:
                state = CoverageState.COVERED
                desc = f"Surface '{surf.value}' covered by test cases {[tc.test_case_id for tc in covering_cases]}"
                surface_cov[surf] = SurfaceCoverage(
                    surface=surf,
                    state=state,
                    covering_test_case_ids=[tc.test_case_id for tc in covering_cases],
                    rationale=desc,
                    gap=None,
                )
            else:
                # Check why surface is uncovered
                is_na = all(c in na_cats for c in matching_cats) if matching_cats else False
                if is_na:
                    state = CoverageState.NOT_APPLICABLE
                    reason = CoverageGapReason.NOT_APPLICABLE
                    severity = CoverageGapSeverity.LOW
                    desc = f"Surface '{surf.value}' categories are not applicable for this execution."
                elif any(self._is_unauthorized(c, authorized_caps) for c in matching_cats):
                    state = CoverageState.UNCOVERED
                    reason = CoverageGapReason.AUTHORIZATION_LIMITATION
                    severity = CoverageGapSeverity.HIGH
                    desc = f"Surface '{surf.value}' uncovered due to unauthorized capability in WorkOrder."
                elif any(c in blocked_cats for c in matching_cats):
                    state = CoverageState.UNCOVERED
                    reason = CoverageGapReason.UNAVAILABLE_CAPABILITY
                    severity = CoverageGapSeverity.HIGH
                    desc = f"Surface '{surf.value}' uncovered due to unavailable runtime capability."
                else:
                    state = CoverageState.UNCOVERED
                    reason = CoverageGapReason.MISSING_TEST
                    severity = CoverageGapSeverity.HIGH
                    desc = f"Surface '{surf.value}' has no covering test cases in TestPlan."

                gap = CoverageGap(
                    gap_id=f"tgap-surf-{surf.value.lower().replace('_', '-')}",
                    target_type="SURFACE",
                    target_id=surf.value,
                    reason=reason,
                    severity=severity,
                    description=desc,
                    provenance={
                        "work_order_id": wo_id,
                        "project_id": proj_id,
                        "execution_id": exec_id,
                        "surface": surf.value,
                    },
                )
                gaps.append(gap)
                surface_cov[surf] = SurfaceCoverage(
                    surface=surf,
                    state=state,
                    covering_test_case_ids=[],
                    rationale=desc,
                    gap=gap,
                )

        # 4. Category Coverage
        for cat in ApplicableTestCategory:
            cat_cases = [tc for tc in plan_cases if getattr(tc, "category", None) == cat]
            if cat_cases:
                category_cov[cat.value] = CoverageState.COVERED
            elif cat in na_cats:
                category_cov[cat.value] = CoverageState.NOT_APPLICABLE
            elif cat in req_cats:
                category_cov[cat.value] = CoverageState.UNCOVERED
            else:
                category_cov[cat.value] = CoverageState.NOT_APPLICABLE

        p_id = getattr(test_plan, "plan_id", "")
        rep_id = (
            f"tcov-{p_id[len('tplan-'):]}"
            if p_id.startswith("tplan-")
            else new_coverage_report_id()
        )
        plan_created_at = getattr(test_plan, "created_at", None) or utc_now()

        report = TestCoverageReport(
            report_id=rep_id,
            plan_id=p_id or "tplan-unknown",
            work_order_id=wo_id,
            execution_id=exec_id,
            project_id=proj_id,
            criteria_coverage=criteria_cov,
            surface_coverage=surface_cov,
            category_coverage=category_cov,
            coverage_gaps=gaps,
            provenance={
                "work_order_id": wo_id,
                "project_id": proj_id,
                "execution_id": exec_id,
                "plan_id": p_id,
            },
            trace={
                "evaluator": "TestCoverageEvaluator",
                "version": "1.0.0",
                "phase": "3.4",
            },
            created_at=plan_created_at,
        )

        return report

    def _infer_criterion_category(self, desc: str) -> ApplicableTestCategory:
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
        if "ocr" in d_lower:
            return ApplicableTestCategory.OCR
        if "video" in d_lower or "record" in d_lower:
            return ApplicableTestCategory.VIDEO
        return ApplicableTestCategory.FUNCTIONAL

    def _is_unauthorized(self, category: ApplicableTestCategory, authorized_caps: set[TestingCapability]) -> bool:
        req_caps = TestApplicabilityClassifier.CATEGORY_CAPABILITY_REQUIREMENTS.get(category, [])
        if not req_caps:
            return False
        return not all(c in authorized_caps for c in req_caps)
