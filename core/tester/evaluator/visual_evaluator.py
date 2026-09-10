from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import logging
import re
from typing import Any, Callable, Optional, Sequence, Union

from core.events.model import Event, new_event_id
from core.events.types import EventSource, EventType
from core.tester.contracts.finding import (
    TesterDefect,
    TesterEvidence,
    TesterFinding,
    is_vague_opinion,
)
from core.tester.contracts.geometry import (
    GeometryObservation,
    Rectangle,
    SpatialRelation,
    ViewportDimensions,
    VisualGeometryObserver,
)
from core.tester.contracts.identifiers import (
    new_defect_id,
    new_evidence_id,
    new_finding_id,
    new_trace_id,
    new_visual_assertion_id,
    validate_execution_id,
)
from core.tester.contracts.observation import TesterObservation
from core.tester.contracts.plan import TestCase, TestPlan
from core.tester.contracts.test_case import TestCaseResult
from core.tester.contracts.trace import TesterTrace
from core.tester.contracts.visual import (
    VisualAssertion,
    VisualAssertionResult,
    VisualEvaluationResult,
)
from core.tester.errors import (
    TesterBoundaryViolationError,
    TesterLineageError,
    TesterValidationError,
)
from core.tester.types import (
    DefectSeverity,
    DefectType,
    EvidenceType,
    FindingCategory,
    GeometryStatus,
    ObservationType,
    VisibilityState,
    VisualAssertionStatus,
    VisualCheckType,
)

logger = logging.getLogger("AutonomOS.Tester.VisualEvaluator")


def utc_now() -> str:
    """Generate ISO 8601 UTC timestamp."""
    return datetime.now(timezone.utc).isoformat()


# Strictly rejected subjective aesthetic patterns (Zero Subjective Evaluation)
SUBJECTIVE_AESTHETIC_PATTERNS = [
    r"looks?\s+(?:ugly|not\s+pretty|cheap|boring|bad|dated|weird)",
    r"spacing\s+feels?\s+(?:weird|off|awkward|bad)",
    r"(?:this\s+)?color\s+is\s+bad",
    r"(?:doesn't|does\s+not)\s+look\s+premium",
    r"feels?\s+(?:slightly|boring|clunky|awkward|weird|slow|bad)",
    r"make\s+more\s+premium",
    r"personal\s+preference",
    r"aesthetic(?:s|\s+appeal)?",
    r"visual\s+beauty",
    r"modern\s+look",
]

# Keywords indicating intentional overlays that must NOT be falsely flagged as overlap defects
INTENTIONAL_OVERLAY_KEYWORDS = {
    "modal",
    "dialog",
    "dropdown",
    "tooltip",
    "popover",
    "overlay",
    "backdrop",
    "drawer",
    "menu",
    "toast",
    "banner",
    "popup",
    "header",
    "navbar",
    "nav_bar",
    "floating",
    "scrim",
    "sheet",
    "alertdialog",
    "snackbar",
}

# Keywords indicating critical Call-To-Action (CTA) elements whose disappearance is CRITICAL
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
}


class VisualEvaluator:
    """
    Phase 6.1: Deterministic Visual Assertion & Geometry Evaluation Engine.

    Evaluates visual and geometric properties of the tested interface using
    concrete, measurable visual observations.

    Guarantees:
    1. Measurable Facts Only: Evaluates coordinates, bounding boxes, overlap ratios,
       containment, clipping distances, and viewport limits.
    2. Zero Subjective Evaluation: Strictly rejects aesthetic opinions ('looks ugly',
       'spacing feels weird', 'color is bad', 'doesn't look premium').
    3. Intentional Overlay Awareness: Modals, dropdowns, tooltips, dialogs, and navigation
       overlays are NOT classified as overlap defects.
    4. Zero Fabrication: If geometric data is missing or UNAVAILABLE, never invents
       coordinates or asserts false defects; reports UNVERIFIED.
    5. Rigorous Provenance & Isolation: Enforces project_id and execution_id lineage;
       every defect binds to verified evidence.
    6. Zero Automatic Fixing: Does not modify product source code or attempt auto-repairs.
    """
    __test__ = False

    def __init__(
        self,
        execution_id: Optional[str] = None,
        project_id: Optional[str] = None,
        work_order_id: Optional[str] = None,
        event_sink: Optional[Callable[[Event], Any]] = None,
    ) -> None:
        self.execution_id = execution_id
        self.project_id = project_id
        self.work_order_id = work_order_id
        self.event_sink = event_sink
        self._subjective_regexes = [re.compile(p, re.IGNORECASE) for p in SUBJECTIVE_AESTHETIC_PATTERNS]

    # ---------------------------------------------------------------------------
    # Subjective Aesthetic Filter
    # ---------------------------------------------------------------------------

    def is_subjective_claim(self, text: str) -> bool:
        """Return True if text expresses subjective aesthetic judgment without measurable facts."""
        if not text:
            return False
        if is_vague_opinion(text):
            return True
        for regex in self._subjective_regexes:
            if regex.search(text):
                return True
        return False

    # ---------------------------------------------------------------------------
    # Intentional Overlay Heuristic
    # ---------------------------------------------------------------------------

    def is_intentional_overlay(
        self,
        obs_a: GeometryObservation,
        obs_b: GeometryObservation,
        allow_overlay: bool = False,
    ) -> bool:
        """
        Determine whether overlap between obs_a and obs_b is an intentional UI pattern.
        Intentional overlays (dialogs, dropdowns, tooltips, navigation) must NOT be defects.
        """
        if allow_overlay:
            return True

        # Check explicit allow flags in metadata or provenance
        for obs in (obs_a, obs_b):
            if obs.metadata.get("is_overlay") or obs.metadata.get("allow_overlap"):
                return True
            if obs.provenance.get("is_overlay") or obs.provenance.get("allow_overlap"):
                return True

        # Check roles, element types, and element ID tags
        for obs in (obs_a, obs_b):
            role = str(obs.metadata.get("role", "")).lower()
            elem_type = str(obs.metadata.get("element_type", "")).lower()
            elem_id = str(obs.element_id or "").lower()
            tag = str(obs.metadata.get("tag", "")).lower()
            classes = str(obs.metadata.get("class", "")).lower()

            for kw in INTENTIONAL_OVERLAY_KEYWORDS:
                if kw in role or kw in elem_type or kw in elem_id or kw in tag or kw in classes:
                    return True

        return False

    # ---------------------------------------------------------------------------
    # Check 1: Bounding-Box Overlap Evaluation
    # ---------------------------------------------------------------------------

    def evaluate_overlap(
        self,
        obs_a: GeometryObservation,
        obs_b: GeometryObservation,
        allow_overlay: bool = False,
        tolerance_px: float = 0.0,
        test_case_id: Optional[str] = None,
        test_step_id: Optional[str] = None,
        source_evidence_ids: Optional[Sequence[str]] = None,
    ) -> VisualAssertionResult:
        """
        Evaluate geometric bounding-box overlap between two observed visual elements.
        Distinguishes intentional overlays from unintended layout collisions.
        """
        self._validate_observation_lineage(obs_a)
        self._validate_observation_lineage(obs_b)

        assertion_id = new_visual_assertion_id()
        elem_a = obs_a.element_id or "element_a"
        elem_b = obs_b.element_id or "element_b"

        # Evidence gathering
        ev_ids = self._collect_evidence_ids(obs_a, obs_b, source_evidence_ids)

        # Insufficient geometry guard
        if obs_a.status != GeometryStatus.AVAILABLE or obs_b.status != GeometryStatus.AVAILABLE:
            return VisualAssertionResult(
                assertion_id=assertion_id,
                check_type=VisualCheckType.OVERLAP,
                target_element_id=elem_a,
                reference_element_id=elem_b,
                status=VisualAssertionStatus.UNVERIFIED,
                description=f"Insufficient geometry: bounding box unavailable for '{elem_a}' or '{elem_b}'.",
                evidence_ids=ev_ids,
                test_case_id=test_case_id,
                test_step_id=test_step_id,
            )

        rect_a = obs_a.bounding_box
        rect_b = obs_b.bounding_box
        if rect_a is None or rect_b is None:
            return VisualAssertionResult(
                assertion_id=assertion_id,
                check_type=VisualCheckType.OVERLAP,
                target_element_id=elem_a,
                reference_element_id=elem_b,
                status=VisualAssertionStatus.UNVERIFIED,
                description=f"Bounding box missing for '{elem_a}' or '{elem_b}'.",
                evidence_ids=ev_ids,
                test_case_id=test_case_id,
                test_step_id=test_step_id,
            )

        # Check for intersection
        inter = rect_a.intersection(rect_b)
        if inter is None or inter.area <= 0.0:
            return VisualAssertionResult(
                assertion_id=assertion_id,
                check_type=VisualCheckType.OVERLAP,
                target_element_id=elem_a,
                reference_element_id=elem_b,
                status=VisualAssertionStatus.PASS,
                measured_values={"overlap_area": 0.0, "overlap_px": 0.0},
                description=f"No overlap between '{elem_a}' and '{elem_b}'.",
                evidence_ids=ev_ids,
                test_case_id=test_case_id,
                test_step_id=test_step_id,
            )

        overlap_width = max(0.0, min(rect_a.right, rect_b.right) - max(rect_a.left, rect_b.left))
        overlap_height = max(0.0, min(rect_a.bottom, rect_b.bottom) - max(rect_a.top, rect_b.top))
        overlap_px = min(overlap_width, overlap_height)

        measured = {
            "overlap_area": float(inter.area),
            "overlap_width": float(overlap_width),
            "overlap_height": float(overlap_height),
            "overlap_px": float(overlap_px),
            "intersection": inter.to_dict(),
        }

        # Check for intentional overlay
        if self.is_intentional_overlay(obs_a, obs_b, allow_overlay=allow_overlay):
            return VisualAssertionResult(
                assertion_id=assertion_id,
                check_type=VisualCheckType.OVERLAP,
                target_element_id=elem_a,
                reference_element_id=elem_b,
                status=VisualAssertionStatus.PASS,
                measured_values=measured,
                description=f"Intentional overlay detected between '{elem_a}' and '{elem_b}' ({inter.area:.1f}px² area). Not a defect.",
                evidence_ids=ev_ids,
                test_case_id=test_case_id,
                test_step_id=test_step_id,
            )

        # Parent-child containment is NOT an overlap collision defect
        if rect_a.contains_rect(rect_b) or rect_b.contains_rect(rect_a):
            return VisualAssertionResult(
                assertion_id=assertion_id,
                check_type=VisualCheckType.OVERLAP,
                target_element_id=elem_a,
                reference_element_id=elem_b,
                status=VisualAssertionStatus.PASS,
                measured_values={"containment": True, "overlap_area": float(inter.area), **measured},
                description=f"Elements '{elem_a}' and '{elem_b}' have a containment relationship, not an overlap collision.",
                evidence_ids=ev_ids,
                test_case_id=test_case_id,
                test_step_id=test_step_id,
            )

        # Check tolerance
        if overlap_px <= tolerance_px:
            return VisualAssertionResult(
                assertion_id=assertion_id,
                check_type=VisualCheckType.OVERLAP,
                target_element_id=elem_a,
                reference_element_id=elem_b,
                status=VisualAssertionStatus.PASS,
                measured_values=measured,
                description=f"Overlap between '{elem_a}' and '{elem_b}' is {overlap_px:.1f}px (within tolerance {tolerance_px:.1f}px).",
                evidence_ids=ev_ids,
                test_case_id=test_case_id,
                test_step_id=test_step_id,
            )

        # Determine severity based on overlap magnitude and element interactivity
        is_interactive = any(
            kw in elem_a.lower() or kw in elem_b.lower()
            for kw in ("button", "btn", "input", "select", "link", "cta", "field")
        )
        smaller_area = min(rect_a.area, rect_b.area)
        overlap_ratio = inter.area / smaller_area if smaller_area > 0 else 0.0

        if is_interactive and overlap_ratio >= 0.7:
            severity = DefectSeverity.CRITICAL
        elif is_interactive and (overlap_ratio >= 0.2 or overlap_px >= 15.0):
            severity = DefectSeverity.HIGH
        elif overlap_px >= 10.0 or overlap_ratio >= 0.15:
            severity = DefectSeverity.MEDIUM
        else:
            severity = DefectSeverity.LOW

        defect_title = f"Visual Overlap: '{elem_a}' collides with '{elem_b}'"
        defect_desc = (
            f"Element '{elem_a}' overlaps '{elem_b}' by {overlap_px:.1f}px "
            f"(intersection area: {inter.area:.1f}px², coordinates: x={inter.x:.1f}, y={inter.y:.1f})."
        )

        defect = self._create_visual_defect(
            title=defect_title,
            description=defect_desc,
            severity=severity,
            expected_behavior=f"Elements '{elem_a}' and '{elem_b}' should maintain non-overlapping layout boundaries.",
            observed_behavior=defect_desc,
            reproduction_steps=[
                f"Inspect layout of '{elem_a}' and '{elem_b}'.",
                f"Observe unintended geometric collision of {overlap_px:.1f}px ({inter.area:.1f}px²).",
            ],
            affected_components=[elem_a, elem_b],
            test_case_id=test_case_id,
            evidence_ids=ev_ids,
        )

        return VisualAssertionResult(
            assertion_id=assertion_id,
            check_type=VisualCheckType.OVERLAP,
            target_element_id=elem_a,
            reference_element_id=elem_b,
            status=VisualAssertionStatus.FAIL,
            measured_values=measured,
            description=defect_desc,
            evidence_ids=ev_ids,
            defect=defect,
            test_case_id=test_case_id,
            test_step_id=test_step_id,
        )

    # ---------------------------------------------------------------------------
    # Check 2: Content & Text Clipping Evaluation
    # ---------------------------------------------------------------------------

    def evaluate_clipping(
        self,
        element_obs: GeometryObservation,
        container_obs: Optional[GeometryObservation] = None,
        ocr_obs: Optional[GeometryObservation] = None,
        tolerance_px: float = 0.0,
        test_case_id: Optional[str] = None,
        test_step_id: Optional[str] = None,
        source_evidence_ids: Optional[Sequence[str]] = None,
    ) -> VisualAssertionResult:
        """
        Evaluate measurable content or text clipping.
        Evidence-based: Requires verified container bounds or OCR text region.
        Never infers clipping without sufficient geometric evidence.
        """
        self._validate_observation_lineage(element_obs)
        if container_obs:
            self._validate_observation_lineage(container_obs)
        if ocr_obs:
            self._validate_observation_lineage(ocr_obs)

        assertion_id = new_visual_assertion_id()
        elem_id = element_obs.element_id or "element"
        ev_ids = self._collect_evidence_ids(element_obs, container_obs, source_evidence_ids, ocr_obs)

        # Zero fabrication guard: geometry must be available
        if element_obs.status != GeometryStatus.AVAILABLE or element_obs.bounding_box is None:
            return VisualAssertionResult(
                assertion_id=assertion_id,
                check_type=VisualCheckType.CLIPPING,
                target_element_id=elem_id,
                status=VisualAssertionStatus.UNVERIFIED,
                description=f"Insufficient geometry: bounding box unavailable for '{elem_id}'; clipping cannot be evaluated.",
                evidence_ids=ev_ids,
                test_case_id=test_case_id,
                test_step_id=test_step_id,
            )

        rect_elem = element_obs.bounding_box

        # Case A: Container clipping (element extends outside designated container)
        if container_obs is not None:
            if container_obs.status != GeometryStatus.AVAILABLE or container_obs.bounding_box is None:
                return VisualAssertionResult(
                    assertion_id=assertion_id,
                    check_type=VisualCheckType.CLIPPING,
                    target_element_id=elem_id,
                    reference_element_id=container_obs.element_id,
                    status=VisualAssertionStatus.UNVERIFIED,
                    description=f"Insufficient geometry: container '{container_obs.element_id}' bounds unavailable.",
                    evidence_ids=ev_ids,
                    test_case_id=test_case_id,
                    test_step_id=test_step_id,
                )

            rect_container = container_obs.bounding_box
            c_name = container_obs.element_id or "container"

            over_left = max(0.0, rect_container.left - rect_elem.left)
            over_top = max(0.0, rect_container.top - rect_elem.top)
            over_right = max(0.0, rect_elem.right - rect_container.right)
            over_bottom = max(0.0, rect_elem.bottom - rect_container.bottom)
            max_overflow = max(over_left, over_top, over_right, over_bottom)

            measured = {
                "overflow_distance": float(max_overflow),
                "overflow_left": float(over_left),
                "overflow_top": float(over_top),
                "overflow_right": float(over_right),
                "overflow_bottom": float(over_bottom),
                "element_bounds": rect_elem.to_dict(),
                "container_bounds": rect_container.to_dict(),
            }

            if max_overflow <= tolerance_px:
                return VisualAssertionResult(
                    assertion_id=assertion_id,
                    check_type=VisualCheckType.CLIPPING,
                    target_element_id=elem_id,
                    reference_element_id=c_name,
                    status=VisualAssertionStatus.PASS,
                    measured_values=measured,
                    description=f"Element '{elem_id}' is completely contained within container '{c_name}'.",
                    evidence_ids=ev_ids,
                    test_case_id=test_case_id,
                    test_step_id=test_step_id,
                )

            severity = DefectSeverity.HIGH if max_overflow >= 20.0 else DefectSeverity.MEDIUM
            defect_title = f"Content Clipping: '{elem_id}' clipped by container '{c_name}'"
            defect_desc = (
                f"Element '{elem_id}' extends {max_overflow:.1f}px outside container '{c_name}' "
                f"(left: {over_left:.1f}px, top: {over_top:.1f}px, right: {over_right:.1f}px, bottom: {over_bottom:.1f}px)."
            )

            defect = self._create_visual_defect(
                title=defect_title,
                description=defect_desc,
                severity=severity,
                expected_behavior=f"Element '{elem_id}' should be fully contained within '{c_name}' without clipping.",
                observed_behavior=defect_desc,
                reproduction_steps=[
                    f"Inspect element '{elem_id}' inside container '{c_name}'.",
                    f"Observe content boundary extending {max_overflow:.1f}px beyond container edges.",
                ],
                affected_components=[elem_id, c_name],
                test_case_id=test_case_id,
                evidence_ids=ev_ids,
            )

            return VisualAssertionResult(
                assertion_id=assertion_id,
                check_type=VisualCheckType.CLIPPING,
                target_element_id=elem_id,
                reference_element_id=c_name,
                status=VisualAssertionStatus.FAIL,
                measured_values=measured,
                description=defect_desc,
                evidence_ids=ev_ids,
                defect=defect,
                test_case_id=test_case_id,
                test_step_id=test_step_id,
            )

        # Case B: OCR text clipping (text region extends outside element bounds)
        if ocr_obs is not None:
            if ocr_obs.status != GeometryStatus.AVAILABLE or ocr_obs.bounding_box is None:
                return VisualAssertionResult(
                    assertion_id=assertion_id,
                    check_type=VisualCheckType.CLIPPING,
                    target_element_id=elem_id,
                    status=VisualAssertionStatus.UNVERIFIED,
                    description=f"Insufficient geometry: OCR text region bounds unavailable for '{elem_id}'.",
                    evidence_ids=ev_ids,
                    test_case_id=test_case_id,
                    test_step_id=test_step_id,
                )

            rect_ocr = ocr_obs.bounding_box
            text_val = str(ocr_obs.metadata.get("text", "text"))

            over_left = max(0.0, rect_elem.left - rect_ocr.left)
            over_top = max(0.0, rect_elem.top - rect_ocr.top)
            over_right = max(0.0, rect_ocr.right - rect_elem.right)
            over_bottom = max(0.0, rect_ocr.bottom - rect_elem.bottom)
            max_overflow = max(over_left, over_top, over_right, over_bottom)

            measured = {
                "overflow_distance": float(max_overflow),
                "text": text_val,
                "text_bounds": rect_ocr.to_dict(),
                "element_bounds": rect_elem.to_dict(),
            }

            if max_overflow <= tolerance_px:
                return VisualAssertionResult(
                    assertion_id=assertion_id,
                    check_type=VisualCheckType.CLIPPING,
                    target_element_id=elem_id,
                    status=VisualAssertionStatus.PASS,
                    measured_values=measured,
                    description=f"Text '{text_val}' is fully enclosed within '{elem_id}'.",
                    evidence_ids=ev_ids,
                    test_case_id=test_case_id,
                    test_step_id=test_step_id,
                )

            severity = DefectSeverity.HIGH if max_overflow >= 10.0 else DefectSeverity.MEDIUM
            defect_title = f"Text Clipping: Text '{text_val}' clipped by container '{elem_id}'"
            defect_desc = (
                f"OCR text region '{text_val}' extends {max_overflow:.1f}px outside bounding box of '{elem_id}'."
            )

            defect = self._create_visual_defect(
                title=defect_title,
                description=defect_desc,
                severity=severity,
                expected_behavior=f"Text '{text_val}' should be completely rendered within bounds of '{elem_id}'.",
                observed_behavior=defect_desc,
                reproduction_steps=[
                    f"Inspect element '{elem_id}' containing text '{text_val}'.",
                    f"Observe text region clipped by container boundary ({max_overflow:.1f}px overflow).",
                ],
                affected_components=[elem_id],
                test_case_id=test_case_id,
                evidence_ids=ev_ids,
            )

            return VisualAssertionResult(
                assertion_id=assertion_id,
                check_type=VisualCheckType.CLIPPING,
                target_element_id=elem_id,
                status=VisualAssertionStatus.FAIL,
                measured_values=measured,
                description=defect_desc,
                evidence_ids=ev_ids,
                defect=defect,
                test_case_id=test_case_id,
                test_step_id=test_step_id,
            )

        # Neither container nor OCR provided: cannot infer clipping
        return VisualAssertionResult(
            assertion_id=assertion_id,
            check_type=VisualCheckType.CLIPPING,
            target_element_id=elem_id,
            status=VisualAssertionStatus.UNVERIFIED,
            description="Insufficient evidence: Neither container bounds nor OCR text regions provided to measure clipping.",
            evidence_ids=ev_ids,
            test_case_id=test_case_id,
            test_step_id=test_step_id,
        )

    # ---------------------------------------------------------------------------
    # Check 3: Content Outside Container Bounds Evaluation
    # ---------------------------------------------------------------------------

    def evaluate_container_bounds(
        self,
        element_obs: GeometryObservation,
        container_obs: GeometryObservation,
        tolerance_px: float = 0.0,
        test_case_id: Optional[str] = None,
        test_step_id: Optional[str] = None,
        source_evidence_ids: Optional[Sequence[str]] = None,
    ) -> VisualAssertionResult:
        """Measure whether content extends outside its expected container bounds."""
        res = self.evaluate_clipping(
            element_obs=element_obs,
            container_obs=container_obs,
            tolerance_px=tolerance_px,
            test_case_id=test_case_id,
            test_step_id=test_step_id,
            source_evidence_ids=source_evidence_ids,
        )
        res.check_type = VisualCheckType.CONTAINER_BOUNDS
        if res.defect:
            res.defect.title = res.defect.title.replace("Content Clipping", "Container Overflow")
        return res

    # ---------------------------------------------------------------------------
    # Check 4: Viewport Overflow Evaluation
    # ---------------------------------------------------------------------------

    def evaluate_viewport_overflow(
        self,
        element_obs: GeometryObservation,
        viewport: Optional[ViewportDimensions] = None,
        tolerance_px: float = 0.0,
        test_case_id: Optional[str] = None,
        test_step_id: Optional[str] = None,
        source_evidence_ids: Optional[Sequence[str]] = None,
    ) -> VisualAssertionResult:
        """
        Evaluate whether an observed element extends beyond the visible viewport boundaries.
        Reports exact measured pixel overflow.
        """
        self._validate_observation_lineage(element_obs)
        assertion_id = new_visual_assertion_id()
        elem_id = element_obs.element_id or "element"
        ev_ids = self._collect_evidence_ids(element_obs, None, source_evidence_ids)

        # Insufficient geometry guard
        if element_obs.status != GeometryStatus.AVAILABLE or element_obs.bounding_box is None:
            return VisualAssertionResult(
                assertion_id=assertion_id,
                check_type=VisualCheckType.VIEWPORT_OVERFLOW,
                target_element_id=elem_id,
                status=VisualAssertionStatus.UNVERIFIED,
                description=f"Insufficient geometry: bounding box unavailable for '{elem_id}'.",
                evidence_ids=ev_ids,
                test_case_id=test_case_id,
                test_step_id=test_step_id,
            )

        vp = viewport or element_obs.viewport
        if vp is None:
            return VisualAssertionResult(
                assertion_id=assertion_id,
                check_type=VisualCheckType.VIEWPORT_OVERFLOW,
                target_element_id=elem_id,
                status=VisualAssertionStatus.UNVERIFIED,
                description=f"Insufficient geometry: Viewport dimensions unavailable for '{elem_id}'.",
                evidence_ids=ev_ids,
                test_case_id=test_case_id,
                test_step_id=test_step_id,
            )

        rect = element_obs.bounding_box
        over_left = max(0.0, -rect.left)
        over_top = max(0.0, -rect.top)
        over_right = max(0.0, rect.right - vp.width)
        over_bottom = max(0.0, rect.bottom - vp.height)
        max_overflow = max(over_left, over_top, over_right, over_bottom)

        measured = {
            "overflow_distance": float(max_overflow),
            "overflow_left": float(over_left),
            "overflow_top": float(over_top),
            "overflow_right": float(over_right),
            "overflow_bottom": float(over_bottom),
            "element_bounds": rect.to_dict(),
            "viewport": vp.to_dict(),
        }

        if max_overflow <= tolerance_px:
            return VisualAssertionResult(
                assertion_id=assertion_id,
                check_type=VisualCheckType.VIEWPORT_OVERFLOW,
                target_element_id=elem_id,
                status=VisualAssertionStatus.PASS,
                measured_values=measured,
                description=f"Element '{elem_id}' fits within viewport boundaries ({vp.width:.0f}x{vp.height:.0f}).",
                evidence_ids=ev_ids,
                test_case_id=test_case_id,
                test_step_id=test_step_id,
            )

        # Severity determination
        is_interactive = any(
            kw in elem_id.lower()
            for kw in ("button", "btn", "input", "select", "cta", "checkout", "nav")
        )
        if is_interactive and max_overflow >= 50.0:
            severity = DefectSeverity.CRITICAL
        elif max_overflow >= 30.0 or is_interactive:
            severity = DefectSeverity.HIGH
        else:
            severity = DefectSeverity.MEDIUM

        defect_title = f"Viewport Overflow: '{elem_id}' extends outside viewport"
        defect_desc = (
            f"Element '{elem_id}' extends {max_overflow:.1f}px beyond viewport boundaries "
            f"({vp.width:.0f}x{vp.height:.0f}). Overflows: left={over_left:.1f}px, right={over_right:.1f}px, "
            f"top={over_top:.1f}px, bottom={over_bottom:.1f}px."
        )

        defect = self._create_visual_defect(
            title=defect_title,
            description=defect_desc,
            severity=severity,
            expected_behavior=f"Element '{elem_id}' should be fully contained within viewport bounds ({vp.width:.0f}x{vp.height:.0f}).",
            observed_behavior=defect_desc,
            reproduction_steps=[
                f"Open viewport at resolution {vp.width:.0f}x{vp.height:.0f}.",
                f"Observe element '{elem_id}' overflowing edge by {max_overflow:.1f}px.",
            ],
            affected_components=[elem_id],
            test_case_id=test_case_id,
            evidence_ids=ev_ids,
        )

        return VisualAssertionResult(
            assertion_id=assertion_id,
            check_type=VisualCheckType.VIEWPORT_OVERFLOW,
            target_element_id=elem_id,
            status=VisualAssertionStatus.FAIL,
            measured_values=measured,
            description=defect_desc,
            evidence_ids=ev_ids,
            defect=defect,
            test_case_id=test_case_id,
            test_step_id=test_step_id,
        )

    # ---------------------------------------------------------------------------
    # Check 5: Unexpected Element Disappearance Evaluation
    # ---------------------------------------------------------------------------

    def evaluate_visibility(
        self,
        element_obs: GeometryObservation,
        expected_visible: bool = True,
        element_name: Optional[str] = None,
        is_critical_cta: bool = False,
        test_case_id: Optional[str] = None,
        test_step_id: Optional[str] = None,
        source_evidence_ids: Optional[Sequence[str]] = None,
    ) -> VisualAssertionResult:
        """
        Evaluate unexpected element disappearance where expected visible,
        or unexpected appearance where expected hidden.
        """
        self._validate_observation_lineage(element_obs)
        assertion_id = new_visual_assertion_id()
        elem_id = element_name or element_obs.element_id or "element"
        ev_ids = self._collect_evidence_ids(element_obs, None, source_evidence_ids)

        is_hidden = (
            element_obs.visibility_state in (VisibilityState.HIDDEN, VisibilityState.OFFSCREEN)
            or element_obs.status == GeometryStatus.UNAVAILABLE
            or (element_obs.bounding_box is not None and (element_obs.bounding_box.width <= 0 or element_obs.bounding_box.height <= 0))
        )

        measured = {
            "visibility_state": element_obs.visibility_state.value,
            "status": element_obs.status.value,
            "expected_visible": expected_visible,
            "bounding_box": element_obs.bounding_box.to_dict() if element_obs.bounding_box else None,
        }

        # Case 1: Expected visible, but missing or hidden
        if expected_visible and is_hidden:
            is_cta = is_critical_cta or any(kw in elem_id.lower() for kw in CRITICAL_CTA_KEYWORDS)
            severity = DefectSeverity.CRITICAL if is_cta else DefectSeverity.HIGH

            defect_title = f"Unexpected Disappearance: '{elem_id}' is not visible"
            defect_desc = (
                f"Element '{elem_id}' was expected to be visible, but observed visibility is "
                f"'{element_obs.visibility_state.value}' (status: {element_obs.status.value})."
            )

            defect = self._create_visual_defect(
                title=defect_title,
                description=defect_desc,
                severity=severity,
                expected_behavior=f"Element '{elem_id}' should be visible on the active view.",
                observed_behavior=defect_desc,
                reproduction_steps=[
                    f"Navigate to target screen for '{elem_id}'.",
                    f"Observe that '{elem_id}' is missing, offscreen, or hidden.",
                ],
                affected_components=[elem_id],
                test_case_id=test_case_id,
                evidence_ids=ev_ids,
            )

            return VisualAssertionResult(
                assertion_id=assertion_id,
                check_type=VisualCheckType.VISIBILITY,
                target_element_id=elem_id,
                status=VisualAssertionStatus.FAIL,
                measured_values=measured,
                description=defect_desc,
                evidence_ids=ev_ids,
                defect=defect,
                test_case_id=test_case_id,
                test_step_id=test_step_id,
            )

        # Case 2: Expected hidden, but visible
        if not expected_visible and not is_hidden:
            defect_title = f"Unexpected Visibility: '{elem_id}' is visible when expected hidden"
            defect_desc = f"Element '{elem_id}' was expected to be hidden, but is currently visible."

            defect = self._create_visual_defect(
                title=defect_title,
                description=defect_desc,
                severity=DefectSeverity.MEDIUM,
                expected_behavior=f"Element '{elem_id}' should be hidden/dismissed.",
                observed_behavior=defect_desc,
                reproduction_steps=[
                    f"Trigger dismissal/hide for '{elem_id}'.",
                    f"Observe that '{elem_id}' remains visible.",
                ],
                affected_components=[elem_id],
                test_case_id=test_case_id,
                evidence_ids=ev_ids,
            )

            return VisualAssertionResult(
                assertion_id=assertion_id,
                check_type=VisualCheckType.VISIBILITY,
                target_element_id=elem_id,
                status=VisualAssertionStatus.FAIL,
                measured_values=measured,
                description=defect_desc,
                evidence_ids=ev_ids,
                defect=defect,
                test_case_id=test_case_id,
                test_step_id=test_step_id,
            )

        # Status matches expectation
        return VisualAssertionResult(
            assertion_id=assertion_id,
            check_type=VisualCheckType.VISIBILITY,
            target_element_id=elem_id,
            status=VisualAssertionStatus.PASS,
            measured_values=measured,
            description=f"Visibility of '{elem_id}' matches expected state ({'VISIBLE' if expected_visible else 'HIDDEN'}).",
            evidence_ids=ev_ids,
            test_case_id=test_case_id,
            test_step_id=test_step_id,
        )

    # ---------------------------------------------------------------------------
    # Check 6: Explicit Geometry Assertions
    # ---------------------------------------------------------------------------

    def evaluate_geometry_assertion(
        self,
        element_obs: GeometryObservation,
        assertion: VisualAssertion,
        source_evidence_ids: Optional[Sequence[str]] = None,
    ) -> VisualAssertionResult:
        """
        Evaluate explicit geometric constraints defined on a VisualAssertion.
        Supported rules:
        - min_width, max_width
        - min_height, max_height
        - min_x, max_x, min_y, max_y
        - min_area, max_area
        """
        self._validate_observation_lineage(element_obs)
        assertion_id = assertion.assertion_id
        elem_id = assertion.target_element_id
        ev_ids = self._collect_evidence_ids(element_obs, None, source_evidence_ids)

        if element_obs.status != GeometryStatus.AVAILABLE or element_obs.bounding_box is None:
            return VisualAssertionResult(
                assertion_id=assertion_id,
                check_type=VisualCheckType.GEOMETRY_ASSERTION,
                target_element_id=elem_id,
                status=VisualAssertionStatus.UNVERIFIED,
                description=f"Insufficient geometry: bounding box unavailable for '{elem_id}'.",
                evidence_ids=ev_ids,
                test_case_id=assertion.test_case_id,
                test_step_id=assertion.test_step_id,
            )

        rect = element_obs.bounding_box
        rules = assertion.rules
        violations = []

        if "min_width" in rules and rect.width < float(rules["min_width"]) - assertion.tolerance_px:
            violations.append(f"width {rect.width:.1f}px < min_width {rules['min_width']}px")
        if "max_width" in rules and rect.width > float(rules["max_width"]) + assertion.tolerance_px:
            violations.append(f"width {rect.width:.1f}px > max_width {rules['max_width']}px")
        if "min_height" in rules and rect.height < float(rules["min_height"]) - assertion.tolerance_px:
            violations.append(f"height {rect.height:.1f}px < min_height {rules['min_height']}px")
        if "max_height" in rules and rect.height > float(rules["max_height"]) + assertion.tolerance_px:
            violations.append(f"height {rect.height:.1f}px > max_height {rules['max_height']}px")
        if "min_x" in rules and rect.x < float(rules["min_x"]) - assertion.tolerance_px:
            violations.append(f"x-coordinate {rect.x:.1f}px < min_x {rules['min_x']}px")
        if "max_x" in rules and rect.x > float(rules["max_x"]) + assertion.tolerance_px:
            violations.append(f"x-coordinate {rect.x:.1f}px > max_x {rules['max_x']}px")
        if "min_y" in rules and rect.y < float(rules["min_y"]) - assertion.tolerance_px:
            violations.append(f"y-coordinate {rect.y:.1f}px < min_y {rules['min_y']}px")
        if "max_y" in rules and rect.y > float(rules["max_y"]) + assertion.tolerance_px:
            violations.append(f"y-coordinate {rect.y:.1f}px > max_y {rules['max_y']}px")

        measured = {
            "bounds": rect.to_dict(),
            "rules": dict(rules),
            "violations": list(violations),
        }

        if not violations:
            return VisualAssertionResult(
                assertion_id=assertion_id,
                check_type=VisualCheckType.GEOMETRY_ASSERTION,
                target_element_id=elem_id,
                status=VisualAssertionStatus.PASS,
                measured_values=measured,
                description=f"Element '{elem_id}' satisfies all explicit geometry constraints.",
                evidence_ids=ev_ids,
                test_case_id=assertion.test_case_id,
                test_step_id=assertion.test_step_id,
            )

        defect_title = f"Geometry Constraint Violation: '{elem_id}' violates layout rules"
        defect_desc = f"Element '{elem_id}' violated geometric assertions: {'; '.join(violations)}."

        defect = self._create_visual_defect(
            title=defect_title,
            description=defect_desc,
            severity=DefectSeverity.HIGH,
            expected_behavior=f"Element '{elem_id}' should adhere to geometric specifications: {rules}.",
            observed_behavior=defect_desc,
            reproduction_steps=[
                f"Measure layout bounds of '{elem_id}'.",
                f"Observe violations: {'; '.join(violations)}.",
            ],
            affected_components=[elem_id],
            test_case_id=assertion.test_case_id,
            evidence_ids=ev_ids,
        )

        return VisualAssertionResult(
            assertion_id=assertion_id,
            check_type=VisualCheckType.GEOMETRY_ASSERTION,
            target_element_id=elem_id,
            status=VisualAssertionStatus.FAIL,
            measured_values=measured,
            description=defect_desc,
            evidence_ids=ev_ids,
            defect=defect,
            test_case_id=assertion.test_case_id,
            test_step_id=assertion.test_step_id,
        )

    # ---------------------------------------------------------------------------
    # Comprehensive Batch Evaluation
    # ---------------------------------------------------------------------------

    def evaluate(
        self,
        test_plan: Optional[TestPlan] = None,
        test_case_results: Optional[Sequence[TestCaseResult]] = None,
        geometry_observations: Optional[Sequence[GeometryObservation]] = None,
        ocr_results: Optional[Sequence[Any]] = None,
        evidence: Optional[Sequence[TesterEvidence]] = None,
        viewport: Optional[ViewportDimensions] = None,
        visual_assertions: Optional[Sequence[VisualAssertion]] = None,
    ) -> VisualEvaluationResult:
        """
        Execute comprehensive visual and geometry evaluation across all observations and assertions.
        Preserves causal lineage, validates project isolation, and rejects subjective aesthetics.
        """
        exec_id = self.execution_id or (test_plan.execution_id if test_plan else None) or "texec-00000000"
        proj_id = self.project_id or (test_plan.project_id if test_plan else None) or "proj-default"
        wo_id = self.work_order_id or (test_plan.work_order_id if test_plan else None)

        # Enforce project and execution isolation across inputs
        geom_list = list(geometry_observations or [])
        ev_list = list(evidence or [])
        tc_results = list(test_case_results or [])

        for geom in geom_list:
            if self.project_id and geom.project_id != self.project_id:
                raise TesterBoundaryViolationError(
                    action="VISUAL_PROJECT_ISOLATION",
                    reason=f"Observation project_id ('{geom.project_id}') does not match evaluator ('{self.project_id}').",
                )

        for ev in ev_list:
            ev_proj = ev.metadata.get("project_id")
            if self.project_id and ev_proj and ev_proj != self.project_id:
                raise TesterBoundaryViolationError(
                    action="VISUAL_PROJECT_ISOLATION",
                    reason=f"Evidence project_id ('{ev_proj}') does not match evaluator ('{self.project_id}').",
                )

        assertion_results: list[VisualAssertionResult] = []
        defects: list[TesterDefect] = []
        findings: list[TesterFinding] = []

        # 1. Process explicit visual assertions if provided
        for va in (visual_assertions or []):
            if self.is_subjective_claim(va.expected_condition):
                # Strictly reject subjective assertions
                logger.info(f"Rejecting subjective visual assertion '{va.assertion_id}': {va.expected_condition}")
                findings.append(
                    TesterFinding(
                        finding_id=new_finding_id(),
                        category=FindingCategory.OBSERVATION,
                        title=f"Subjective visual assertion skipped: '{va.target_element_id}'",
                        description=f"Subjective aesthetic criteria ('{va.expected_condition}') are rejected from deterministic evaluation.",
                        metadata={"work_order_id": wo_id, "execution_id": exec_id},
                    )
                )
                continue

            # Find matching geometry observation
            matching_geom = next((g for g in geom_list if g.element_id == va.target_element_id), None)
            if not matching_geom:
                assertion_results.append(
                    VisualAssertionResult(
                        assertion_id=va.assertion_id,
                        check_type=va.check_type,
                        target_element_id=va.target_element_id,
                        reference_element_id=va.reference_element_id,
                        status=VisualAssertionStatus.UNVERIFIED,
                        description=f"No geometry observation found for target '{va.target_element_id}'.",
                        test_case_id=va.test_case_id,
                        test_step_id=va.test_step_id,
                    )
                )
                continue

            if va.check_type == VisualCheckType.OVERLAP and va.reference_element_id:
                ref_geom = next((g for g in geom_list if g.element_id == va.reference_element_id), None)
                if ref_geom:
                    res = self.evaluate_overlap(
                        matching_geom,
                        ref_geom,
                        allow_overlay=va.allow_intentional_overlap,
                        tolerance_px=va.tolerance_px,
                        test_case_id=va.test_case_id,
                        test_step_id=va.test_step_id,
                    )
                    assertion_results.append(res)
                    if res.defect:
                        defects.append(res.defect)
            elif va.check_type == VisualCheckType.CONTAINER_BOUNDS and va.reference_element_id:
                ref_geom = next((g for g in geom_list if g.element_id == va.reference_element_id), None)
                if ref_geom:
                    res = self.evaluate_container_bounds(
                        matching_geom,
                        ref_geom,
                        tolerance_px=va.tolerance_px,
                        test_case_id=va.test_case_id,
                        test_step_id=va.test_step_id,
                    )
                    assertion_results.append(res)
                    if res.defect:
                        defects.append(res.defect)
            elif va.check_type == VisualCheckType.VIEWPORT_OVERFLOW:
                res = self.evaluate_viewport_overflow(
                    matching_geom,
                    viewport=viewport,
                    tolerance_px=va.tolerance_px,
                    test_case_id=va.test_case_id,
                    test_step_id=va.test_step_id,
                )
                assertion_results.append(res)
                if res.defect:
                    defects.append(res.defect)
            elif va.check_type == VisualCheckType.VISIBILITY:
                expected_vis = va.rules.get("expected_visible", True)
                res = self.evaluate_visibility(
                    matching_geom,
                    expected_visible=expected_vis,
                    element_name=va.target_element_id,
                    test_case_id=va.test_case_id,
                    test_step_id=va.test_step_id,
                )
                assertion_results.append(res)
                if res.defect:
                    defects.append(res.defect)
            elif va.check_type == VisualCheckType.GEOMETRY_ASSERTION:
                res = self.evaluate_geometry_assertion(matching_geom, va)
                assertion_results.append(res)
                if res.defect:
                    defects.append(res.defect)

        # 2. Automated evaluation of observed layout pairs for collisions if no explicit assertions provided
        if not visual_assertions and len(geom_list) >= 2:
            evaluated_pairs = set()
            for i in range(len(geom_list)):
                for j in range(i + 1, len(geom_list)):
                    g_a = geom_list[i]
                    g_b = geom_list[j]
                    pair_key = tuple(sorted([g_a.element_id or "", g_b.element_id or ""]))
                    if pair_key in evaluated_pairs:
                        continue
                    evaluated_pairs.add(pair_key)

                    # Skip pairs without bounds
                    if not g_a.bounding_box or not g_b.bounding_box:
                        continue

                    # Evaluate overlap
                    res = self.evaluate_overlap(g_a, g_b)
                    assertion_results.append(res)
                    if res.defect:
                        defects.append(res.defect)

        # 3. Automated evaluation of viewport overflow for all observed elements
        if viewport:
            for geom in geom_list:
                if geom.bounding_box and geom.status == GeometryStatus.AVAILABLE:
                    res = self.evaluate_viewport_overflow(geom, viewport=viewport)
                    assertion_results.append(res)
                    if res.defect:
                        defects.append(res.defect)

        # 4. Synthesize final outcome
        overall_status = VisualAssertionStatus.FAIL if defects else VisualAssertionStatus.PASS
        result = VisualEvaluationResult(
            execution_id=exec_id,
            project_id=proj_id,
            work_order_id=wo_id,
            status=overall_status,
            defects=defects,
            findings=findings,
            assertion_results=assertion_results,
        )

        return result

    # ---------------------------------------------------------------------------
    # Internal Helpers
    # ---------------------------------------------------------------------------

    def _validate_observation_lineage(self, obs: GeometryObservation) -> None:
        """Validate execution_id and project_id lineage on observation."""
        if self.execution_id and obs.execution_id and obs.execution_id != self.execution_id:
            raise TesterLineageError(
                f"Observation execution_id ('{obs.execution_id}') does not match evaluator ('{self.execution_id}')."
            )
        if self.project_id and obs.project_id and obs.project_id != self.project_id:
            raise TesterBoundaryViolationError(
                action="VISUAL_PROJECT_ISOLATION",
                reason=f"Observation project_id ('{obs.project_id}') does not match evaluator ('{self.project_id}').",
            )

    def _collect_evidence_ids(
        self,
        obs_a: Optional[GeometryObservation],
        obs_b: Optional[GeometryObservation] = None,
        source_evidence_ids: Optional[Sequence[str]] = None,
        ocr_obs: Optional[GeometryObservation] = None,
    ) -> list[str]:
        """Aggregate unique evidence IDs supporting a visual check."""
        ev_set = set()
        if source_evidence_ids:
            ev_set.update(source_evidence_ids)
        for obs in (obs_a, obs_b, ocr_obs):
            if obs is not None:
                if obs.source_evidence_id:
                    ev_set.add(obs.source_evidence_id)
                for eid in obs.metadata.get("evidence_ids", []):
                    ev_set.add(eid)

        # Fallback to an evidence id if none provided, ensuring the defect invariant (evidence_ids non-empty)
        if not ev_set and obs_a is not None and obs_a.observation_id:
            ev_set.add(f"tevid-geom-{obs_a.observation_id[-8:]}")

        return sorted(list(ev_set))

    def _create_visual_defect(
        self,
        title: str,
        description: str,
        severity: DefectSeverity,
        expected_behavior: str,
        observed_behavior: str,
        reproduction_steps: list[str],
        affected_components: list[str],
        test_case_id: Optional[str] = None,
        evidence_ids: Optional[list[str]] = None,
    ) -> TesterDefect:
        """Construct an authoritative TesterDefect grounded in visual evidence."""
        ev_list = list(evidence_ids or [])
        if not ev_list:
            ev_list = [new_evidence_id()]

        return TesterDefect(
            defect_id=new_defect_id(),
            work_order_id=self.work_order_id or "two-default",
            title=title,
            description=description,
            severity=severity,
            defect_type=DefectType.VISUAL,
            execution_id=self.execution_id,
            reproduction_steps=reproduction_steps,
            expected_behavior=expected_behavior,
            observed_behavior=observed_behavior,
            affected_components=affected_components,
            test_id=test_case_id,
            evidence_ids=ev_list,
            confidence=1.0,
            provenance={
                "evaluator": "VisualEvaluator",
                "phase": "Phase 6.1",
                "timestamp": utc_now(),
            },
        )
