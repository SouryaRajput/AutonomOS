from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import logging
from typing import Any, Optional, Sequence

from core.tester.contracts.identifiers import (
    validate_execution_id,
    validate_work_order_id,
)
from core.tester.errors import TesterLineageError, TesterValidationError
from core.tester.types import (
    ApplicabilityLevel,
    ApplicableTestCategory,
    ChangeCategory,
    PresenceStatus,
    TestCategory,
    TestSurface,
    TestingCapability,
)

logger = logging.getLogger("AutonomOS.TestApplicability")


def utc_now() -> str:
    """Generate ISO 8601 UTC timestamp."""
    return datetime.now(timezone.utc).isoformat()


@dataclass
class CategoryApplicability:
    """
    Applicability assessment for an individual testing category.
    Includes classification level, authority check, runtime capability check,
    manager request tracking, required testing capabilities, and audit rationale.
    """
    __test__ = False
    category: ApplicableTestCategory
    level: ApplicabilityLevel
    is_authorized: bool = True
    is_runtime_available: bool = True
    is_manager_requested: bool = False
    rationale: str = ""
    required_capabilities: list[TestingCapability] = field(default_factory=list)
    blocked_reason: Optional[str] = None

    def __post_init__(self) -> None:
        if isinstance(self.category, str):
            try:
                self.category = ApplicableTestCategory(self.category.upper())
            except (ValueError, KeyError):
                raise TesterValidationError(f"Invalid applicable test category: {self.category}", field_name="category")
        if isinstance(self.level, str):
            try:
                self.level = ApplicabilityLevel(self.level.upper())
            except (ValueError, KeyError):
                self.level = ApplicabilityLevel.UNKNOWN

    @property
    def is_required(self) -> bool:
        return self.level == ApplicabilityLevel.REQUIRED

    @property
    def is_optional(self) -> bool:
        return self.level == ApplicabilityLevel.OPTIONAL

    @property
    def is_not_applicable(self) -> bool:
        return self.level == ApplicabilityLevel.NOT_APPLICABLE

    @property
    def is_unknown(self) -> bool:
        return self.level == ApplicabilityLevel.UNKNOWN

    @property
    def is_blocked(self) -> bool:
        return bool(self.blocked_reason)

    def to_dict(self) -> dict[str, Any]:
        return {
            "category": self.category.value,
            "level": self.level.value,
            "is_authorized": self.is_authorized,
            "is_runtime_available": self.is_runtime_available,
            "is_manager_requested": self.is_manager_requested,
            "rationale": self.rationale,
            "required_capabilities": [c.value for c in self.required_capabilities],
            "blocked_reason": self.blocked_reason,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CategoryApplicability:
        raw_cat = data.get("category", ApplicableTestCategory.FUNCTIONAL.value)
        raw_level = data.get("level", ApplicabilityLevel.UNKNOWN.value)
        req_caps = []
        for rc in data.get("required_capabilities", []):
            try:
                req_caps.append(TestingCapability(rc.upper()))
            except (ValueError, KeyError):
                pass
        return cls(
            category=ApplicableTestCategory(raw_cat.upper()),
            level=ApplicabilityLevel(raw_level.upper()),
            is_authorized=bool(data.get("is_authorized", True)),
            is_runtime_available=bool(data.get("is_runtime_available", True)),
            is_manager_requested=bool(data.get("is_manager_requested", False)),
            rationale=str(data.get("rationale", "")),
            required_capabilities=req_caps,
            blocked_reason=data.get("blocked_reason"),
        )


@dataclass
class TestApplicabilityReport:
    """
    Authoritative evaluation of test applicability for a Tester execution session.
    Contains category-by-category applicability determinations, authority bounds,
    and runtime availability assertions.
    """
    __test__ = False
    project_id: str
    work_order_id: str
    execution_id: str
    classifications: dict[ApplicableTestCategory, CategoryApplicability] = field(default_factory=dict)
    provenance: dict[str, Any] = field(default_factory=dict)
    trace: dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=utc_now)

    def __post_init__(self) -> None:
        self.validate()

    def validate(self) -> None:
        validate_work_order_id(self.work_order_id)
        validate_execution_id(self.execution_id)
        if not self.project_id or not self.project_id.strip():
            raise TesterLineageError("TestApplicabilityReport requires a valid non-empty project_id.")

    def get(self, category: ApplicableTestCategory | str) -> Optional[CategoryApplicability]:
        cat_enum = category if isinstance(category, ApplicableTestCategory) else ApplicableTestCategory(str(category).upper())
        return self.classifications.get(cat_enum)

    def get_level(self, category: ApplicableTestCategory | str) -> ApplicabilityLevel:
        ca = self.get(category)
        return ca.level if ca else ApplicabilityLevel.UNKNOWN

    def is_required(self, category: ApplicableTestCategory | str) -> bool:
        return self.get_level(category) == ApplicabilityLevel.REQUIRED

    def is_optional(self, category: ApplicableTestCategory | str) -> bool:
        return self.get_level(category) == ApplicabilityLevel.OPTIONAL

    def is_not_applicable(self, category: ApplicableTestCategory | str) -> bool:
        return self.get_level(category) == ApplicabilityLevel.NOT_APPLICABLE

    def is_unknown(self, category: ApplicableTestCategory | str) -> bool:
        return self.get_level(category) == ApplicabilityLevel.UNKNOWN

    @property
    def required_categories(self) -> list[ApplicableTestCategory]:
        return sorted([cat for cat, ca in self.classifications.items() if ca.is_required], key=lambda x: x.value)

    @property
    def optional_categories(self) -> list[ApplicableTestCategory]:
        return sorted([cat for cat, ca in self.classifications.items() if ca.is_optional], key=lambda x: x.value)

    @property
    def not_applicable_categories(self) -> list[ApplicableTestCategory]:
        return sorted([cat for cat, ca in self.classifications.items() if ca.is_not_applicable], key=lambda x: x.value)

    @property
    def unknown_categories(self) -> list[ApplicableTestCategory]:
        return sorted([cat for cat, ca in self.classifications.items() if ca.is_unknown], key=lambda x: x.value)

    @property
    def blocked_categories(self) -> list[ApplicableTestCategory]:
        return sorted([cat for cat, ca in self.classifications.items() if ca.is_blocked], key=lambda x: x.value)

    def to_dict(self) -> dict[str, Any]:
        return {
            "project_id": self.project_id,
            "work_order_id": self.work_order_id,
            "execution_id": self.execution_id,
            "classifications": {cat.value: ca.to_dict() for cat, ca in sorted(self.classifications.items(), key=lambda x: x[0].value)},
            "required_categories": [c.value for c in sorted(self.required_categories, key=lambda x: x.value)],
            "optional_categories": [c.value for c in sorted(self.optional_categories, key=lambda x: x.value)],
            "not_applicable_categories": [c.value for c in sorted(self.not_applicable_categories, key=lambda x: x.value)],
            "unknown_categories": [c.value for c in sorted(self.unknown_categories, key=lambda x: x.value)],
            "blocked_categories": [c.value for c in sorted(self.blocked_categories, key=lambda x: x.value)],
            "provenance": dict(self.provenance),
            "trace": dict(self.trace),
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> TestApplicabilityReport:
        classes = {}
        for cat_str, ca_dict in data.get("classifications", {}).items():
            try:
                cat_enum = ApplicableTestCategory(cat_str.upper())
                classes[cat_enum] = CategoryApplicability.from_dict(ca_dict)
            except (ValueError, KeyError):
                pass
        return cls(
            project_id=str(data.get("project_id", "")),
            work_order_id=str(data.get("work_order_id", "")),
            execution_id=str(data.get("execution_id", "")),
            classifications=classes,
            provenance=dict(data.get("provenance", {})),
            trace=dict(data.get("trace", {})),
            created_at=str(data.get("created_at", utc_now())),
        )


class TestApplicabilityClassifier:
    """
    Deterministic test applicability classifier for Tester V1 Phase 3.2.

    Enforces 4-way authority intersection:
    Global Tester Boundary ∩ WorkOrder Authorization ∩ Runtime Availability ∩ Change Context
    """
    __test__ = False

    # Mapping of testing categories to the specific capabilities they require
    CATEGORY_CAPABILITY_REQUIREMENTS: dict[ApplicableTestCategory, list[TestingCapability]] = {
        ApplicableTestCategory.FUNCTIONAL: [],
        ApplicableTestCategory.UI_INTERACTION: [TestingCapability.CLICK, TestingCapability.TYPE],
        ApplicableTestCategory.NAVIGATION: [TestingCapability.NAVIGATE],
        ApplicableTestCategory.VISUAL: [TestingCapability.SCREENSHOT],
        ApplicableTestCategory.RESPONSIVE: [TestingCapability.SCREENSHOT, TestingCapability.NAVIGATE],
        ApplicableTestCategory.ANIMATION: [TestingCapability.SCREEN_RECORDING],
        ApplicableTestCategory.OCR: [TestingCapability.OCR],
        ApplicableTestCategory.VIDEO: [TestingCapability.SCREEN_RECORDING],
        ApplicableTestCategory.PERFORMANCE: [TestingCapability.PERFORMANCE_MEASUREMENT],
        ApplicableTestCategory.INTEGRATION: [],
    }

    # Taxonomy mapping from general TestCategory to ApplicableTestCategory
    TEST_CATEGORY_MAPPING: dict[str, list[ApplicableTestCategory]] = {
        "FUNCTIONAL": [ApplicableTestCategory.FUNCTIONAL],
        "INTEGRATION": [ApplicableTestCategory.INTEGRATION],
        "UI": [ApplicableTestCategory.UI_INTERACTION, ApplicableTestCategory.VISUAL],
        "UX": [ApplicableTestCategory.UI_INTERACTION, ApplicableTestCategory.VISUAL],
        "VISUAL": [ApplicableTestCategory.VISUAL],
        "RESPONSIVENESS": [ApplicableTestCategory.RESPONSIVE],
        "RESPONSIVE": [ApplicableTestCategory.RESPONSIVE],
        "ANIMATION": [ApplicableTestCategory.ANIMATION],
        "OCR": [ApplicableTestCategory.OCR],
        "VIDEO": [ApplicableTestCategory.VIDEO],
        "PERFORMANCE": [ApplicableTestCategory.PERFORMANCE],
        "NAVIGATION": [ApplicableTestCategory.NAVIGATION],
        "E2E": [ApplicableTestCategory.INTEGRATION, ApplicableTestCategory.FUNCTIONAL],
        "SMOKE": [ApplicableTestCategory.FUNCTIONAL],
        "REGRESSION": [ApplicableTestCategory.FUNCTIONAL],
    }

    def classify(
        self,
        work_order: Any,
        test_context: Any,
        runtime: Optional[Any] = None,
        runtime_capabilities: Optional[Sequence[TestingCapability | str]] = None,
        environment: Optional[Any] = None,
    ) -> TestApplicabilityReport:
        """
        Deterministically classify applicability of all 10 core V1 testing categories.
        """
        proj_id = getattr(test_context, "project_id", "") or getattr(work_order, "project_id", "")
        wo_id = getattr(test_context, "work_order_id", "") or getattr(work_order, "work_order_id", "")
        exec_id = getattr(test_context, "execution_id", "") or getattr(work_order, "execution_id", "")

        # 1. Resolve Authorized Capabilities from WorkOrder
        wo_caps_raw = getattr(work_order, "authorized_capabilities", None)
        authorized_caps_set: Optional[set[TestingCapability]] = None
        if wo_caps_raw:
            authorized_caps_set = set()
            for cap in wo_caps_raw:
                if isinstance(cap, TestingCapability):
                    authorized_caps_set.add(cap)
                elif isinstance(cap, str):
                    try:
                        authorized_caps_set.add(TestingCapability(cap.upper()))
                    except (ValueError, KeyError):
                        pass

        # 2. Resolve Available Runtime Capabilities
        available_caps_set: set[TestingCapability] = set(TestingCapability)
        if runtime_capabilities is not None:
            available_caps_set = set()
            for cap in runtime_capabilities:
                if isinstance(cap, TestingCapability):
                    available_caps_set.add(cap)
                elif isinstance(cap, str):
                    try:
                        available_caps_set.add(TestingCapability(cap.upper()))
                    except (ValueError, KeyError):
                        pass
        elif runtime is not None:
            rt_caps = getattr(runtime, "capabilities", None) or getattr(runtime, "supported_capabilities", None)
            if rt_caps is not None:
                available_caps_set = set()
                for cap in rt_caps:
                    if isinstance(cap, TestingCapability):
                        available_caps_set.add(cap)
                    elif isinstance(cap, str):
                        try:
                            available_caps_set.add(TestingCapability(cap.upper()))
                        except (ValueError, KeyError):
                            pass

        # 3. Resolve Manager-Requested Categories
        manager_requested: set[ApplicableTestCategory] = set()
        wo_cats = getattr(work_order, "test_categories", []) or []
        for c in wo_cats:
            c_name = c.value if hasattr(c, "value") else str(c).upper()
            mapped = self.TEST_CATEGORY_MAPPING.get(c_name, [])
            manager_requested.update(mapped)
            try:
                manager_requested.add(ApplicableTestCategory(c_name))
            except (ValueError, KeyError):
                pass

        # Also inspect acceptance criteria for explicit keyword requirements
        for ac in getattr(work_order, "acceptance_criteria", []) or []:
            desc = (getattr(ac, "description", "") or "").lower()
            if "responsive" in desc or "mobile layout" in desc or "viewport" in desc:
                manager_requested.add(ApplicableTestCategory.RESPONSIVE)
            if "animation" in desc or "transition" in desc:
                manager_requested.add(ApplicableTestCategory.ANIMATION)
            if "ocr" in desc:
                manager_requested.add(ApplicableTestCategory.OCR)
            if "record" in desc or "video" in desc:
                manager_requested.add(ApplicableTestCategory.VIDEO)
            if "performance" in desc or "latency" in desc or "load time" in desc:
                manager_requested.add(ApplicableTestCategory.PERFORMANCE)

        # 4. Contextual Presence & Change Signals
        frontend_pres = getattr(test_context, "frontend", None)
        is_fe_present = frontend_pres.is_present if frontend_pres else False
        is_fe_absent = frontend_pres.is_absent if frontend_pres else False
        is_fe_unknown = frontend_pres.is_unknown if frontend_pres else True

        backend_pres = getattr(test_context, "backend", None)
        is_be_present = backend_pres.is_present if backend_pres else False
        is_be_absent = backend_pres.is_absent if backend_pres else False

        change_cats = set(getattr(test_context, "change_categories", []))
        has_fe_change = test_context.has_frontend_changes if hasattr(test_context, "has_frontend_changes") else ChangeCategory.FRONTEND in change_cats
        has_be_change = test_context.has_backend_changes if hasattr(test_context, "has_backend_changes") else ChangeCategory.BACKEND in change_cats
        has_api_change = test_context.has_api_changes if hasattr(test_context, "has_api_changes") else ChangeCategory.API in change_cats
        has_cfg_change = test_context.has_configuration_changes if hasattr(test_context, "has_configuration_changes") else ChangeCategory.CONFIGURATION in change_cats
        is_test_only = test_context.is_test_only if hasattr(test_context, "is_test_only") else (change_cats == {ChangeCategory.TEST_ONLY})
        is_context_unknown = (change_cats == {ChangeCategory.UNKNOWN}) and is_fe_unknown

        changed_files = [f.lower() for f in getattr(test_context, "changed_files", [])]
        changed_routes = getattr(test_context, "changed_routes", [])

        is_css_layout_change = False
        if changed_files and all(f.endswith((".css", ".scss", ".sass", ".less")) for f in changed_files):
            is_css_layout_change = True

        is_animation_change = False
        if any("animat" in f or "transition" in f or "keyframes" in f for f in changed_files):
            is_animation_change = True
        if any(s.surface == TestSurface.ANIMATION and s.is_available for s in getattr(test_context, "test_surfaces", [])):
            is_animation_change = True

        is_navigation_change = bool(changed_routes) or any("route" in f or "nav" in f for f in changed_files)

        # 5. Evaluate Applicability for each of the 10 categories
        classifications: dict[ApplicableTestCategory, CategoryApplicability] = {}

        for category in ApplicableTestCategory:
            req_caps = self.CATEGORY_CAPABILITY_REQUIREMENTS.get(category, [])
            is_manager_req = category in manager_requested

            # A. Authorization Check
            is_authorized = True
            if authorized_caps_set is not None and req_caps:
                if not all(c in authorized_caps_set for c in req_caps):
                    is_authorized = False

            # B. Runtime Availability Check
            is_runtime_avail = True
            missing_rt_caps = [c for c in req_caps if c not in available_caps_set]
            if missing_rt_caps:
                is_runtime_avail = False

            # C. Incomplete Context Fallback
            if is_context_unknown and not is_manager_req:
                level = ApplicabilityLevel.UNKNOWN
                rationale = "Incomplete context: insufficient architectural or change data to determine applicability."
                classifications[category] = CategoryApplicability(
                    category=category,
                    level=level,
                    is_authorized=is_authorized,
                    is_runtime_available=is_runtime_avail,
                    is_manager_requested=is_manager_req,
                    rationale=rationale,
                    required_capabilities=req_caps,
                )
                continue

            # D. Contextual Baseline Determination
            level, rationale = self._determine_baseline_applicability(
                category=category,
                is_fe_present=is_fe_present,
                is_fe_absent=is_fe_absent,
                is_fe_unknown=is_fe_unknown,
                is_be_present=is_be_present,
                has_fe_change=has_fe_change,
                has_be_change=has_be_change,
                has_api_change=has_api_change,
                has_cfg_change=has_cfg_change,
                is_test_only=is_test_only,
                is_css_layout_change=is_css_layout_change,
                is_animation_change=is_animation_change,
                is_navigation_change=is_navigation_change,
            )

            # E. Manager Request Priority Override
            if is_manager_req:
                if is_fe_absent and category in {
                    ApplicableTestCategory.UI_INTERACTION,
                    ApplicableTestCategory.NAVIGATION,
                    ApplicableTestCategory.VISUAL,
                    ApplicableTestCategory.RESPONSIVE,
                    ApplicableTestCategory.ANIMATION,
                }:
                    level = ApplicabilityLevel.NOT_APPLICABLE
                    rationale = f"Category '{category.value}' requested by manager but frontend is absent."
                else:
                    level = ApplicabilityLevel.REQUIRED
                    rationale = f"Manager explicitly requested {category.value} testing."

            # F. Authority & Availability Enforcements
            blocked_reason: Optional[str] = None

            if not is_authorized:
                # Unauthorized category cannot be tested
                level = ApplicabilityLevel.NOT_APPLICABLE
                unauth_caps = [c.value for c in req_caps if authorized_caps_set and c not in authorized_caps_set]
                rationale = f"Category '{category.value}' requires capabilities {unauth_caps} not authorized by WorkOrder."
            elif not is_runtime_avail:
                # Runtime capability missing
                if level in {ApplicabilityLevel.REQUIRED, ApplicabilityLevel.OPTIONAL} or is_manager_req:
                    level = ApplicabilityLevel.UNKNOWN
                    blocked_reason = f"Required runtime capability {[c.value for c in missing_rt_caps]} is unavailable in current runtime."
                    rationale = f"Category '{category.value}' marked UNKNOWN/BLOCKED: {blocked_reason}"

            classifications[category] = CategoryApplicability(
                category=category,
                level=level,
                is_authorized=is_authorized,
                is_runtime_available=is_runtime_avail,
                is_manager_requested=is_manager_req,
                rationale=rationale,
                required_capabilities=req_caps,
                blocked_reason=blocked_reason,
            )

        return TestApplicabilityReport(
            project_id=proj_id,
            work_order_id=wo_id,
            execution_id=exec_id,
            classifications=classifications,
            provenance={
                "work_order_id": wo_id,
                "project_id": proj_id,
                "execution_id": exec_id,
            },
            trace={
                "classifier": "TestApplicabilityClassifier",
                "version": "1.0.0",
                "phase": "3.2",
            },
            created_at=utc_now(),
        )

    def _determine_baseline_applicability(
        self,
        category: ApplicableTestCategory,
        is_fe_present: bool,
        is_fe_absent: bool,
        is_fe_unknown: bool,
        is_be_present: bool,
        has_fe_change: bool,
        has_be_change: bool,
        has_api_change: bool,
        has_cfg_change: bool,
        is_test_only: bool,
        is_css_layout_change: bool,
        is_animation_change: bool,
        is_navigation_change: bool,
    ) -> tuple[ApplicabilityLevel, str]:
        """
        Pure deterministic baseline evaluation based on change category and architectural context.
        """
        # 1. FUNCTIONAL
        if category == ApplicableTestCategory.FUNCTIONAL:
            if is_css_layout_change:
                return ApplicabilityLevel.OPTIONAL, "CSS/layout changes primarily affect presentation; functional testing is optional."
            if has_fe_change or has_be_change or has_api_change or is_test_only:
                return ApplicabilityLevel.REQUIRED, "Code changes affect product functionality; functional testing is required."
            if has_cfg_change:
                return ApplicabilityLevel.REQUIRED, "Configuration changes may alter runtime behavior; functional testing required."
            return ApplicabilityLevel.OPTIONAL, "Functional testing is optional for non-functional or documentation changes."

        # 2. UI_INTERACTION
        if category == ApplicableTestCategory.UI_INTERACTION:
            if is_fe_absent:
                return ApplicabilityLevel.NOT_APPLICABLE, "No frontend present; UI interaction testing is not applicable."
            if is_fe_present:
                if is_css_layout_change:
                    return ApplicabilityLevel.OPTIONAL, "CSS/layout change: UI interaction testing is optional to verify click targets."
                if has_fe_change:
                    return ApplicabilityLevel.REQUIRED, "Frontend component changes directly affect user interaction."
                return ApplicabilityLevel.OPTIONAL, "Frontend is present; UI interaction testing is optional."
            return ApplicabilityLevel.UNKNOWN, "Frontend presence is unknown."

        # 3. NAVIGATION
        if category == ApplicableTestCategory.NAVIGATION:
            if is_fe_absent:
                return ApplicabilityLevel.NOT_APPLICABLE, "No frontend present; navigation testing is not applicable."
            if is_fe_present:
                if is_navigation_change:
                    return ApplicabilityLevel.REQUIRED, "Navigation routes or routing components changed; navigation testing required."
                if has_fe_change:
                    return ApplicabilityLevel.OPTIONAL, "Frontend changes may indirectly affect navigation flows."
                return ApplicabilityLevel.OPTIONAL, "Navigation testing is optional for unchanged frontend."
            return ApplicabilityLevel.UNKNOWN, "Frontend presence is unknown."

        # 4. VISUAL
        if category == ApplicableTestCategory.VISUAL:
            if is_fe_absent:
                return ApplicabilityLevel.NOT_APPLICABLE, "No frontend present; visual layout testing is not applicable."
            if is_fe_present:
                if is_css_layout_change or is_animation_change:
                    return ApplicabilityLevel.REQUIRED, "Visual/CSS/animation changes directly impact visual appearance."
                if has_fe_change:
                    return ApplicabilityLevel.OPTIONAL, "Frontend changes may affect layout; visual testing is optional."
                return ApplicabilityLevel.NOT_APPLICABLE, "No frontend changes detected; visual testing is not applicable."
            return ApplicabilityLevel.UNKNOWN, "Frontend presence is unknown."

        # 5. RESPONSIVE
        if category == ApplicableTestCategory.RESPONSIVE:
            if is_fe_absent:
                return ApplicabilityLevel.NOT_APPLICABLE, "No frontend present; responsive layout testing is not applicable."
            if is_fe_present:
                if is_css_layout_change:
                    return ApplicabilityLevel.REQUIRED, "CSS/layout changes directly affect responsive layout across viewports."
                if has_fe_change:
                    return ApplicabilityLevel.OPTIONAL, "Frontend changes may affect responsiveness; responsive testing is optional."
                return ApplicabilityLevel.NOT_APPLICABLE, "No frontend changes; responsive testing is not applicable."
            return ApplicabilityLevel.UNKNOWN, "Frontend presence is unknown."

        # 6. ANIMATION
        if category == ApplicableTestCategory.ANIMATION:
            if is_fe_absent:
                return ApplicabilityLevel.NOT_APPLICABLE, "No frontend present; animation testing is not applicable."
            if is_fe_present:
                if is_animation_change:
                    return ApplicabilityLevel.REQUIRED, "Animation or transition changes detected; animation testing required."
                return ApplicabilityLevel.NOT_APPLICABLE, "No animation behavior changed; animation testing is not applicable."
            return ApplicabilityLevel.UNKNOWN, "Frontend presence is unknown."

        # 7. OCR
        if category == ApplicableTestCategory.OCR:
            # OCR is specialized visual text extraction, NOT applicable unless explicitly requested
            return ApplicabilityLevel.NOT_APPLICABLE, "OCR testing is not applicable unless explicitly requested by Manager."

        # 8. VIDEO
        if category == ApplicableTestCategory.VIDEO:
            if is_fe_absent:
                return ApplicabilityLevel.NOT_APPLICABLE, "No frontend present; video recording is not applicable."
            if is_animation_change:
                return ApplicabilityLevel.OPTIONAL, "Animation changed; video recording is optional to capture dynamic transitions."
            if has_fe_change:
                return ApplicabilityLevel.OPTIONAL, "Frontend changed; video recording is optional."
            return ApplicabilityLevel.NOT_APPLICABLE, "No frontend changes; video recording is not applicable."

        # 9. PERFORMANCE
        if category == ApplicableTestCategory.PERFORMANCE:
            if has_be_change or has_api_change or has_fe_change:
                return ApplicabilityLevel.OPTIONAL, "Performance testing is optional for functional code changes."
            return ApplicabilityLevel.OPTIONAL, "Performance testing is optional."

        # 10. INTEGRATION
        if category == ApplicableTestCategory.INTEGRATION:
            if (has_fe_change and has_be_change) or (is_fe_present and is_be_present and (has_fe_change or has_be_change)):
                return ApplicabilityLevel.REQUIRED, "Full-stack changes require cross-layer integration testing."
            if has_api_change:
                return ApplicabilityLevel.REQUIRED, "API changes require client/server contract integration testing."
            if has_be_change:
                return ApplicabilityLevel.OPTIONAL, "Backend changes may warrant integration testing with data layers."
            if has_fe_change:
                return ApplicabilityLevel.OPTIONAL, "Frontend changes may warrant integration testing with service APIs."
            return ApplicabilityLevel.OPTIONAL, "Integration testing is optional."

        return ApplicabilityLevel.UNKNOWN, "Unknown category."
