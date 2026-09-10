from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import logging
import re
from typing import Any, Callable, Optional, Sequence, Union

from core.tester.contracts.finding import TesterDefect, TesterFinding
from core.tester.contracts.geometry import (
    GeometryObservation,
    GeometryStatus,
    Rectangle,
    ViewportDimensions,
)
from core.tester.contracts.identifiers import (
    new_defect_id,
    new_evidence_id,
    new_finding_id,
    new_responsive_evaluation_id,
    validate_execution_id,
)
from core.tester.contracts.plan import TestCase, TestPlan
from core.tester.contracts.responsive import (
    DEFAULT_DESKTOP_PROFILE,
    DEFAULT_DESKTOP_VIEWPORT,
    DEFAULT_MOBILE_PROFILE,
    DEFAULT_MOBILE_VIEWPORT,
    DEFAULT_TABLET_PROFILE,
    DEFAULT_TABLET_VIEWPORT,
    DEFAULT_VIEWPORT_PROFILES,
    MAX_VIEWPORT_COUNT,
    ResponsiveCheck,
    ResponsiveEvaluationResult,
    ResponsiveViewportResult,
    ViewportProfile,
)
from core.tester.errors import (
    TesterBoundaryViolationError,
    TesterLineageError,
    TesterValidationError,
)
from core.tester.types import (
    DefectSeverity,
    DefectType,
    DeviceCategory,
    FindingCategory,
    ResponsiveCheckType,
    ResponsiveEvaluationStatus,
    TestSurface,
    VisibilityState,
)

logger = logging.getLogger("AutonomOS.Tester.ResponsiveEvaluator")


def utc_now() -> str:
    """Generate ISO 8601 UTC timestamp."""
    return datetime.now(timezone.utc).isoformat()


# Keywords indicating mobile navigation triggers / hamburger menu
MOBILE_NAV_TRIGGER_KEYWORDS = {
    "hamburger",
    "hamburger_button",
    "hamburger_btn",
    "hamburger_menu",
    "nav_toggle",
    "nav_button",
    "mobile_nav",
    "mobile_menu",
    "menu_toggle",
    "menu_button",
    "menu_btn",
    "drawer_button",
    "drawer_toggle",
}

# Keywords indicating desktop navigation elements
DESKTOP_NAV_KEYWORDS = {
    "nav",
    "navbar",
    "nav_link",
    "nav_links",
    "nav_item",
    "nav_menu",
    "header_nav",
    "menu_bar",
    "desktop_nav",
    "tab_nav",
}

# Keywords indicating intentionally horizontally scrollable containers
INTENTIONAL_HORIZONTAL_KEYWORDS = {
    "carousel",
    "table",
    "data_table",
    "code",
    "pre",
    "tabs",
    "tab_list",
    "horizontal_scroll",
    "slider",
    "gallery",
    "strip",
    "scroll_row",
}

# Keywords indicating intentional overlays that must not be flagged as overlap collisions
INTENTIONAL_OVERLAY_KEYWORDS = {
    "modal",
    "dialog",
    "dropdown",
    "tooltip",
    "popover",
    "overlay",
    "backdrop",
    "drawer",
    "toast",
    "banner",
    "popup",
    "sheet",
    "scrim",
    "header",
    "navbar",
    "floating",
    "sticky",
}

# Keywords indicating critical Call-To-Action (CTA) elements
CRITICAL_CTA_KEYWORDS = {
    "submit",
    "checkout",
    "buy",
    "purchase",
    "pay",
    "login",
    "sign_in",
    "signup",
    "sign_up",
    "confirm",
    "order",
    "cta",
}

# Interactive control element keywords
INTERACTIVE_KEYWORDS = {
    "button",
    "btn",
    "input",
    "select",
    "textarea",
    "link",
    "checkbox",
    "radio",
    "toggle",
}


class ResponsiveEvaluator:
    """
    Phase 6.3: Deterministic Responsive Layout Evaluation Engine.

    Evaluates whether the application remains usable across explicitly required viewport sizes:
    1. Bounded Viewport Resolution: Resolves viewports strictly from TestPlan or configuration,
       falling back to bounded defaults, clamped to MAX_VIEWPORT_COUNT (10).
    2. Measurable Visual Checks: Evaluates content clipping, unexpected horizontal overflow,
       inaccessible controls, overlapping elements, and layout collapse.
    3. Intentional Differences Awareness: Distinguishes intentional responsive design adaptations
       (e.g., hamburger menu replacing desktop navbar, multi-column cards stacking vertically)
       from actual defects.
    4. Viewport-Isolated Defects: Binds exact viewport name, dimensions, and evidence IDs to every defect.
    5. Zero Automatic Fixing: Does not modify product source or attempt automated fixes.
    6. Strict Provenance & Isolation: Enforces project_id and execution_id lineage.
    """
    __test__ = False

    def __init__(
        self,
        execution_id: Optional[str] = None,
        project_id: Optional[str] = None,
        work_order_id: Optional[str] = None,
    ) -> None:
        self.execution_id = execution_id
        self.project_id = project_id
        self.work_order_id = work_order_id

    # ---------------------------------------------------------------------------
    # Viewport Resolution
    # ---------------------------------------------------------------------------

    def resolve_viewports(
        self,
        test_plan: Optional[TestPlan] = None,
        configured_viewports: Optional[Sequence[Union[ViewportProfile, dict, ViewportDimensions]]] = None,
    ) -> list[ViewportProfile]:
        """
        Resolve bounded list of viewport profiles for evaluation.

        Priority order:
        1. Explicit configured_viewports (validated and clamped).
        2. Viewports explicitly extracted from test_plan step descriptions/metadata.
        3. Bounded V1 defaults (Mobile 375x667 and Desktop 1280x800).

        Strictly clamped to MAX_VIEWPORT_COUNT (10).
        """
        resolved: list[ViewportProfile] = []

        if configured_viewports is not None:
            if not configured_viewports:
                raise TesterValidationError("configured_viewports cannot be empty when explicitly provided.")

            for item in configured_viewports:
                if isinstance(item, ViewportProfile):
                    profile = item
                elif isinstance(item, ViewportDimensions):
                    profile = self._profile_from_dimensions(item)
                elif isinstance(item, dict):
                    if "width" in item and "height" in item and "name" not in item:
                        dims = ViewportDimensions.from_dict(item)
                        profile = self._profile_from_dimensions(dims)
                    else:
                        profile = ViewportProfile.from_dict(item)
                else:
                    raise TesterValidationError(
                        f"Unsupported viewport configuration item type: {type(item).__name__}."
                    )
                resolved.append(profile)

        elif test_plan is not None:
            extracted = self._extract_viewports_from_plan(test_plan)
            if extracted:
                resolved.extend(extracted)
            else:
                resolved = list(DEFAULT_VIEWPORT_PROFILES)
        else:
            resolved = list(DEFAULT_VIEWPORT_PROFILES)

        # Deduplicate by (width, height)
        unique_profiles: list[ViewportProfile] = []
        seen_dims: set[tuple[float, float]] = set()
        for p in resolved:
            dim_key = (float(p.dimensions.width), float(p.dimensions.height))
            if dim_key not in seen_dims:
                seen_dims.add(dim_key)
                unique_profiles.append(p)

        # Enforce finite bounded count (MAX_VIEWPORT_COUNT)
        if len(unique_profiles) > MAX_VIEWPORT_COUNT:
            logger.warning(
                f"Configured viewports count ({len(unique_profiles)}) exceeds MAX_VIEWPORT_COUNT ({MAX_VIEWPORT_COUNT}). "
                f"Clamping to first {MAX_VIEWPORT_COUNT} viewports."
            )
            unique_profiles = unique_profiles[:MAX_VIEWPORT_COUNT]

        return unique_profiles

    def _profile_from_dimensions(self, dims: ViewportDimensions) -> ViewportProfile:
        """Construct a ViewportProfile from raw dimensions using standard breakpoints."""
        w = dims.width
        if w < 600.0:
            return ViewportProfile(
                name=f"Mobile ({int(dims.width)}x{int(dims.height)})",
                category=DeviceCategory.MOBILE,
                dimensions=dims,
                is_touch_enabled=True,
            )
        elif w < 1024.0:
            return ViewportProfile(
                name=f"Tablet ({int(dims.width)}x{int(dims.height)})",
                category=DeviceCategory.TABLET,
                dimensions=dims,
                is_touch_enabled=True,
            )
        else:
            return ViewportProfile(
                name=f"Desktop ({int(dims.width)}x{int(dims.height)})",
                category=DeviceCategory.DESKTOP,
                dimensions=dims,
                is_touch_enabled=False,
            )

    def _extract_viewports_from_plan(self, test_plan: TestPlan) -> list[ViewportProfile]:
        """Extract explicit viewports declared in TestPlan test cases or steps."""
        extracted: list[ViewportProfile] = []
        pattern = re.compile(r"\(?(\d{3,4})\s*[xX*]\s*(\d{3,4})\)?")

        for tc in test_plan.test_cases:
            texts_to_check = [tc.objective, tc.expected_outcome]
            for step in tc.steps:
                texts_to_check.append(step.description)
                if step.metadata:
                    texts_to_check.append(str(step.metadata))

            for text in texts_to_check:
                if not text:
                    continue
                matches = pattern.findall(text)
                for w_str, h_str in matches:
                    w, h = float(w_str), float(h_str)
                    if 200.0 <= w <= 5000.0 and 200.0 <= h <= 5000.0:
                        dims = ViewportDimensions(width=w, height=h)
                        extracted.append(self._profile_from_dimensions(dims))

        return extracted

    # ---------------------------------------------------------------------------
    # Single Viewport Layout Evaluation
    # ---------------------------------------------------------------------------

    def evaluate_viewport_layout(
        self,
        profile: ViewportProfile,
        observations: Sequence[GeometryObservation],
        screenshot_evidence_ids: Optional[Sequence[str]] = None,
        test_case_id: Optional[str] = None,
        test_step_id: Optional[str] = None,
    ) -> ResponsiveViewportResult:
        """
        Evaluate layout usability for a single viewport profile against geometry observations.

        Checks:
        1. Layout Collapse: Elements collapsing to 0 width/height/area.
        2. Inaccessible Controls: Interactive controls becoming hidden/off-screen without mobile equivalent.
        3. Content Clipping / Horizontal Overflow: Content unexpectedly exceeding viewport width.
        4. Overlapping Elements: Collisions between non-container elements on this viewport.
        """
        # 1. Lineage & isolation validation
        for obs in observations:
            self._validate_observation_lineage(obs)

        vp_evidence_ids = self._collect_viewport_evidence_ids(observations, screenshot_evidence_ids)
        defects: list[TesterDefect] = []
        checks_evaluated = 0
        checks_passed = 0
        checks_failed = 0

        vp_width = profile.dimensions.width
        vp_height = profile.dimensions.height

        # Pre-scan: Identify presence of mobile navigation toggle on this viewport
        has_mobile_nav_toggle = any(
            self._is_mobile_nav_trigger(obs) and obs.status == GeometryStatus.AVAILABLE
            for obs in observations
        )

        # Pre-scan: Build map of available bounding boxes
        valid_observations = [
            obs for obs in observations
            if obs.status == GeometryStatus.AVAILABLE and obs.bounding_box is not None
        ]

        # -----------------------------------------------------------------------
        # Check 1: Layout Collapse & Inaccessible Controls & Overflow
        # -----------------------------------------------------------------------
        for obs in observations:
            elem_id = obs.element_id or "element"
            checks_evaluated += 1

            # A. Layout Collapse check
            if obs.status == GeometryStatus.AVAILABLE and obs.bounding_box is not None:
                rect = obs.bounding_box
                obs_vis = getattr(obs, "visibility_state", getattr(obs, "visibility", None))
                is_hidden = obs_vis == VisibilityState.HIDDEN
                # If element has 0 dimensions and is not intentionally hidden
                if (rect.width <= 0 or rect.height <= 0 or rect.area <= 0) and not is_hidden:
                    checks_failed += 1
                    defect = self._create_responsive_defect(
                        profile=profile,
                        title=f"Layout Collapse: '{elem_id}' collapsed to 0 area on {profile.name} ({int(vp_width)}x{int(vp_height)})",
                        description=(
                            f"Element '{elem_id}' has collapsed layout dimensions ({rect.width:.1f}x{rect.height:.1f}, "
                            f"area: {rect.area:.1f}px²) on {profile.name} viewport."
                        ),
                        severity=DefectSeverity.HIGH if self._is_critical_cta(elem_id) else DefectSeverity.MEDIUM,
                        check_type=ResponsiveCheckType.LAYOUT_COLLAPSE,
                        expected_behavior=f"Element '{elem_id}' should maintain non-zero layout dimensions on {profile.name}.",
                        observed_behavior=f"Element '{elem_id}' collapsed to {rect.width:.1f}x{rect.height:.1f}px.",
                        reproduction_steps=[
                            f"Switch viewport to {profile.name} ({int(vp_width)}x{int(vp_height)}).",
                            f"Inspect bounding box of '{elem_id}'.",
                            f"Observe collapsed area of {rect.area:.1f}px².",
                        ],
                        affected_components=[elem_id],
                        test_case_id=test_case_id,
                        evidence_ids=vp_evidence_ids,
                    )
                    defects.append(defect)
                    continue

            # B. Inaccessible Controls check
            is_control = self._is_interactive_control(elem_id)
            if is_control:
                obs_vis = getattr(obs, "visibility_state", getattr(obs, "visibility", None))
                is_hidden = (
                    obs.status == GeometryStatus.UNAVAILABLE or
                    obs_vis == VisibilityState.HIDDEN or
                    (obs.bounding_box is not None and (
                        obs.bounding_box.right <= 0 or
                        obs.bounding_box.left >= vp_width or
                        obs.bounding_box.bottom <= 0
                    ))
                )

                if is_hidden:
                    # Check if this is an intentional responsive adaptation (e.g. desktop nav -> mobile hamburger)
                    if self._is_desktop_nav_item(elem_id) and has_mobile_nav_toggle:
                        # Intentional navigation adaptation: mobile toggle is available!
                        checks_passed += 1
                        continue

                    # Real inaccessible control defect!
                    checks_failed += 1
                    is_critical = self._is_critical_cta(elem_id)
                    severity = DefectSeverity.CRITICAL if is_critical else DefectSeverity.HIGH
                    defect = self._create_responsive_defect(
                        profile=profile,
                        title=f"Inaccessible Control: '{elem_id}' inaccessible on {profile.name} ({int(vp_width)}x{int(vp_height)})",
                        description=(
                            f"Interactive control '{elem_id}' is hidden or off-screen on {profile.name} viewport "
                            f"without an accessible responsive replacement."
                        ),
                        severity=severity,
                        check_type=ResponsiveCheckType.INACCESSIBLE_CONTROL,
                        expected_behavior=f"Interactive control '{elem_id}' must remain visible and accessible on {profile.name}.",
                        observed_behavior=f"Control '{elem_id}' is not accessible on {profile.name} ({int(vp_width)}x{int(vp_height)}).",
                        reproduction_steps=[
                            f"Switch viewport to {profile.name} ({int(vp_width)}x{int(vp_height)}).",
                            f"Attempt to locate control '{elem_id}'.",
                            f"Observe control is missing, hidden, or located off-screen.",
                        ],
                        affected_components=[elem_id],
                        test_case_id=test_case_id,
                        evidence_ids=vp_evidence_ids,
                    )
                    defects.append(defect)
                    continue

            # C. Horizontal Overflow & Clipping check
            if obs.status == GeometryStatus.AVAILABLE and obs.bounding_box is not None:
                rect = obs.bounding_box
                # Check horizontal clipping beyond viewport bounds
                over_left = max(0.0, -rect.left)
                over_right = max(0.0, rect.right - vp_width)
                max_h_overflow = max(over_left, over_right)

                if max_h_overflow > 1.0:
                    # Check if container is intentionally horizontally scrollable
                    if self._is_intentional_horizontal(elem_id) or obs.metadata.get("is_intentional_overflow"):
                        checks_passed += 1
                        continue

                    # Unintended horizontal overflow / clipping
                    checks_failed += 1
                    is_mobile = profile.category == DeviceCategory.MOBILE
                    severity = DefectSeverity.HIGH if (max_h_overflow >= 20.0 or is_control) else DefectSeverity.MEDIUM
                    prefix = "Mobile Clipping" if is_mobile else f"{profile.name} Overflow"
                    defect = self._create_responsive_defect(
                        profile=profile,
                        title=f"{prefix}: '{elem_id}' extends beyond viewport on {profile.name} ({int(vp_width)}x{int(vp_height)})",
                        description=(
                            f"Element '{elem_id}' exceeds horizontal viewport boundary by {max_h_overflow:.1f}px "
                            f"(rect.right={rect.right:.1f}px, viewport width={vp_width:.1f}px) on {profile.name}."
                        ),
                        severity=severity,
                        check_type=ResponsiveCheckType.HORIZONTAL_OVERFLOW if not is_mobile else ResponsiveCheckType.CLIPPING,
                        expected_behavior=f"Element '{elem_id}' should fit within horizontal boundaries of {profile.name} ({int(vp_width)}px).",
                        observed_behavior=f"Element '{elem_id}' overflows horizontally by {max_h_overflow:.1f}px.",
                        reproduction_steps=[
                            f"Switch viewport to {profile.name} ({int(vp_width)}x{int(vp_height)}).",
                            f"Inspect element '{elem_id}'.",
                            f"Observe horizontal boundary exceeding viewport width by {max_h_overflow:.1f}px.",
                        ],
                        affected_components=[elem_id],
                        test_case_id=test_case_id,
                        evidence_ids=vp_evidence_ids,
                    )
                    defects.append(defect)
                    continue

            checks_passed += 1

        # -----------------------------------------------------------------------
        # Check 2: Overlapping Elements (Pairwise)
        # -----------------------------------------------------------------------
        n_elems = len(valid_observations)
        for i in range(n_elems):
            obs_a = valid_observations[i]
            elem_a = obs_a.element_id or f"elem_{i}"
            rect_a = obs_a.bounding_box
            if rect_a is None:
                continue

            for j in range(i + 1, n_elems):
                obs_b = valid_observations[j]
                elem_b = obs_b.element_id or f"elem_{j}"
                rect_b = obs_b.bounding_box
                if rect_b is None:
                    continue

                checks_evaluated += 1

                # Skip intentional overlays
                if self._is_intentional_overlay(elem_a) or self._is_intentional_overlay(elem_b):
                    checks_passed += 1
                    continue

                # Skip containment (parent-child)
                if rect_a.contains_rect(rect_b) or rect_b.contains_rect(rect_a):
                    checks_passed += 1
                    continue

                # Check intersection
                inter = rect_a.intersection(rect_b)
                if inter is not None and inter.area > 50.0:
                    overlap_width = inter.width
                    overlap_height = inter.height
                    # Notice: Vertical stacking (column stack) where elements touch or have 0-1px boundary is not overlap
                    if overlap_width > 2.0 and overlap_height > 2.0:
                        checks_failed += 1
                        is_interactive = self._is_interactive_control(elem_a) or self._is_interactive_control(elem_b)
                        severity = DefectSeverity.HIGH if is_interactive else DefectSeverity.MEDIUM

                        defect = self._create_responsive_defect(
                            profile=profile,
                            title=f"Responsive Overlap: '{elem_a}' overlaps '{elem_b}' on {profile.name} ({int(vp_width)}x{int(vp_height)})",
                            description=(
                                f"Elements '{elem_a}' and '{elem_b}' collide on {profile.name} "
                                f"with intersection area {inter.area:.1f}px² (overlap: {overlap_width:.1f}x{overlap_height:.1f}px)."
                            ),
                            severity=severity,
                            check_type=ResponsiveCheckType.OVERLAP,
                            expected_behavior=f"Elements '{elem_a}' and '{elem_b}' should maintain non-overlapping boundaries on {profile.name}.",
                            observed_behavior=f"Elements collide with {inter.area:.1f}px² intersection area.",
                            reproduction_steps=[
                                f"Switch viewport to {profile.name} ({int(vp_width)}x{int(vp_height)}).",
                                f"Inspect layout of '{elem_a}' and '{elem_b}'.",
                                f"Observe unintended visual collision ({inter.area:.1f}px²).",
                            ],
                            affected_components=[elem_a, elem_b],
                            test_case_id=test_case_id,
                            evidence_ids=vp_evidence_ids,
                        )
                        defects.append(defect)
                        continue

                checks_passed += 1

        vp_status = ResponsiveEvaluationStatus.FAIL if defects else ResponsiveEvaluationStatus.PASS

        return ResponsiveViewportResult(
            profile=profile,
            status=vp_status,
            checks_evaluated=checks_evaluated,
            checks_passed=checks_passed,
            checks_failed=checks_failed,
            defects=defects,
            observations=list(observations),
            evidence_ids=vp_evidence_ids,
            trace={
                "viewport_name": profile.name,
                "category": profile.category.value,
                "width": vp_width,
                "height": vp_height,
                "evaluated_at": utc_now(),
            },
        )

    # ---------------------------------------------------------------------------
    # Cross-Viewport Layout Evaluation
    # ---------------------------------------------------------------------------

    def evaluate_cross_viewport(
        self,
        viewport_observations: dict[str, Sequence[GeometryObservation]],
        profiles: Optional[Sequence[ViewportProfile]] = None,
        screenshot_evidence_map: Optional[dict[str, Sequence[str]]] = None,
        test_case_id: Optional[str] = None,
        test_step_id: Optional[str] = None,
    ) -> ResponsiveEvaluationResult:
        """
        Evaluate layout usability across multiple viewports for a test case.

        Performs individual viewport layout evaluations and cross-viewport comparative analysis.
        Tags defects as viewport-specific when appropriate and aggregates authoritative results.
        """
        evaluation_id = new_responsive_evaluation_id()
        evidence_map = screenshot_evidence_map or {}

        # Resolve or align profiles
        if profiles:
            resolved_profiles = list(profiles)
        else:
            resolved_profiles = []
            for name in viewport_observations.keys():
                dims = self._find_dimensions_for_viewport(name, viewport_observations[name])
                resolved_profiles.append(self._profile_from_dimensions(dims))

        # Clamp to MAX_VIEWPORT_COUNT
        resolved_profiles = resolved_profiles[:MAX_VIEWPORT_COUNT]

        profile_map = {p.name.lower(): p for p in resolved_profiles}
        # Also map by category name
        for p in resolved_profiles:
            profile_map[p.category.value.lower()] = p

        viewport_results: list[ResponsiveViewportResult] = []
        all_defects: list[TesterDefect] = []
        all_evidence_ids: list[str] = []

        for vp_key, obs_list in viewport_observations.items():
            # Find matching profile
            matched_profile = profile_map.get(vp_key.lower())
            if not matched_profile:
                dims = self._find_dimensions_for_viewport(vp_key, obs_list)
                matched_profile = self._profile_from_dimensions(dims)

            vp_ev_ids = evidence_map.get(vp_key, [])
            vp_res = self.evaluate_viewport_layout(
                profile=matched_profile,
                observations=obs_list,
                screenshot_evidence_ids=vp_ev_ids,
                test_case_id=test_case_id,
                test_step_id=test_step_id,
            )
            viewport_results.append(vp_res)
            all_defects.extend(vp_res.defects)
            all_evidence_ids.extend(vp_res.evidence_ids)

        # Cross-viewport analysis: Mark whether defects are viewport-specific
        defects_by_elem: dict[str, list[TesterDefect]] = {}
        for d in all_defects:
            elem = d.affected_components[0] if d.affected_components else "unknown"
            defects_by_elem.setdefault(elem, []).append(d)

        num_viewports = len(viewport_results)
        for elem, defect_list in defects_by_elem.items():
            # If defect affects fewer than all viewports, it's viewport-specific
            is_specific = len(defect_list) < num_viewports
            for d in defect_list:
                if d.metadata is not None:
                    d.metadata["is_viewport_specific"] = is_specific

        overall_status = ResponsiveEvaluationStatus.FAIL if all_defects else ResponsiveEvaluationStatus.PASS

        return ResponsiveEvaluationResult(
            evaluation_id=evaluation_id,
            status=overall_status,
            viewport_results=viewport_results,
            defects=all_defects,
            findings=[],
            total_viewports_evaluated=len(viewport_results),
            evidence_ids=sorted(list(set(all_evidence_ids))),
            test_case_id=test_case_id,
            test_step_id=test_step_id,
            evaluated_at=utc_now(),
            metadata={
                "project_id": self.project_id,
                "execution_id": self.execution_id,
                "work_order_id": self.work_order_id,
                "total_defects": len(all_defects),
            },
        )

    def _find_dimensions_for_viewport(
        self,
        name: str,
        observations: Sequence[GeometryObservation],
    ) -> ViewportDimensions:
        """Infer ViewportDimensions from name or observations."""
        for obs in observations:
            if obs.viewport is not None:
                return obs.viewport

        name_lower = name.lower()
        if "mobile" in name_lower or "phone" in name_lower:
            return DEFAULT_MOBILE_VIEWPORT
        elif "tablet" in name_lower or "ipad" in name_lower:
            return DEFAULT_TABLET_VIEWPORT
        else:
            return DEFAULT_DESKTOP_VIEWPORT

    # ---------------------------------------------------------------------------
    # Defect Factory & Evidence Helpers
    # ---------------------------------------------------------------------------

    def _create_responsive_defect(
        self,
        profile: ViewportProfile,
        title: str,
        description: str,
        severity: DefectSeverity,
        check_type: ResponsiveCheckType,
        expected_behavior: str,
        observed_behavior: str,
        reproduction_steps: list[str],
        affected_components: list[str],
        test_case_id: Optional[str] = None,
        evidence_ids: Optional[list[str]] = None,
    ) -> TesterDefect:
        """Construct an authoritative TesterDefect grounded in viewport layout observations."""
        ev_list = list(evidence_ids or [])
        if not ev_list:
            ev_list = [new_evidence_id()]

        return TesterDefect(
            defect_id=new_defect_id(),
            work_order_id=self.work_order_id or "two-default",
            title=title,
            description=description,
            severity=severity,
            defect_type=DefectType.RESPONSIVE,
            execution_id=self.execution_id,
            reproduction_steps=reproduction_steps,
            expected_behavior=expected_behavior,
            observed_behavior=observed_behavior,
            affected_components=affected_components,
            test_id=test_case_id,
            evidence_ids=ev_list,
            confidence=1.0,
            provenance={
                "evaluator": "ResponsiveEvaluator",
                "phase": "Phase 6.3",
                "check_type": check_type.value,
                "viewport_profile": profile.name,
                "viewport_width": profile.dimensions.width,
                "viewport_height": profile.dimensions.height,
                "device_category": profile.category.value,
                "affected_surface": TestSurface.RESPONSIVE_LAYOUT.value,
            },
            metadata={
                "viewport_profile": profile.name,
                "viewport_width": profile.dimensions.width,
                "viewport_height": profile.dimensions.height,
                "device_category": profile.category.value,
                "is_viewport_specific": True,
            },
        )

    def _collect_viewport_evidence_ids(
        self,
        observations: Sequence[GeometryObservation],
        screenshot_evidence_ids: Optional[Sequence[str]] = None,
    ) -> list[str]:
        """Collect all evidence IDs strictly associated with a specific viewport."""
        ev_set = set()
        if screenshot_evidence_ids:
            ev_set.update(screenshot_evidence_ids)

        for obs in observations:
            if obs.source_evidence_id:
                ev_set.add(obs.source_evidence_id)
            for eid in obs.metadata.get("evidence_ids", []):
                ev_set.add(eid)

        if not ev_set and observations:
            first_obs = observations[0]
            if first_obs.observation_id:
                ev_set.add(f"tevid-resp-{first_obs.observation_id[-8:]}")

        return sorted(list(ev_set))

    # ---------------------------------------------------------------------------
    # Pattern & Heuristic Classifiers
    # ---------------------------------------------------------------------------

    def _is_mobile_nav_trigger(self, obs: GeometryObservation) -> bool:
        """Return True if element is a mobile navigation trigger (e.g. hamburger menu)."""
        elem_id = (obs.element_id or "").lower()
        metadata_str = str(obs.metadata).lower()
        return any(kw in elem_id or kw in metadata_str for kw in MOBILE_NAV_TRIGGER_KEYWORDS)

    def _is_desktop_nav_item(self, elem_id: str) -> bool:
        """Return True if element name suggests a desktop navigation bar item."""
        e_lower = elem_id.lower()
        return any(kw in e_lower for kw in DESKTOP_NAV_KEYWORDS)

    def _is_intentional_horizontal(self, elem_id: str) -> bool:
        """Return True if container is intentionally horizontally scrollable."""
        e_lower = elem_id.lower()
        return any(kw in e_lower for kw in INTENTIONAL_HORIZONTAL_KEYWORDS)

    def _is_intentional_overlay(self, elem_id: str) -> bool:
        """Return True if element is an intentional overlay/dropdown/modal."""
        e_lower = elem_id.lower()
        return any(kw in e_lower for kw in INTENTIONAL_OVERLAY_KEYWORDS)

    def _is_critical_cta(self, elem_id: str) -> bool:
        """Return True if element is a critical conversion CTA."""
        e_lower = elem_id.lower()
        return any(kw in e_lower for kw in CRITICAL_CTA_KEYWORDS)

    def _is_interactive_control(self, elem_id: str) -> bool:
        """Return True if element is an interactive button/input/link/CTA."""
        e_lower = elem_id.lower()
        return (
            any(kw in e_lower for kw in INTERACTIVE_KEYWORDS) or
            self._is_critical_cta(elem_id) or
            self._is_desktop_nav_item(elem_id) or
            any(kw in e_lower for kw in MOBILE_NAV_TRIGGER_KEYWORDS)
        )

    # ---------------------------------------------------------------------------
    # Lineage & Boundary Guards
    # ---------------------------------------------------------------------------

    def _validate_observation_lineage(self, obs: GeometryObservation) -> None:
        """Validate execution_id and project_id lineage on observation."""
        if self.execution_id and obs.execution_id and obs.execution_id != self.execution_id:
            raise TesterLineageError(
                f"Observation execution_id ('{obs.execution_id}') does not match evaluator ('{self.execution_id}')."
            )
        if self.project_id and obs.project_id and obs.project_id != self.project_id:
            raise TesterBoundaryViolationError(
                action="RESPONSIVE_PROJECT_ISOLATION",
                reason=f"Observation project_id ('{obs.project_id}') does not match evaluator ('{self.project_id}').",
            )

    def apply_fix(self, *args, **kwargs) -> Any:
        """Zero fixing guard: Responsive evaluator does NOT modify product source or attempt automated fixes."""
        raise TesterBoundaryViolationError(
            action="RESPONSIVE_AUTO_FIX",
            reason=(
                "ResponsiveEvaluator is strictly an evaluation component. "
                "Tester does not perform automatic fixes on product or source code."
            ),
        )

    def auto_fix(self, *args, **kwargs) -> Any:
        return self.apply_fix(*args, **kwargs)
