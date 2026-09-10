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
    new_typography_evaluation_id,
    validate_execution_id,
)
from core.tester.contracts.ocr import BoundingBox, OCRResult, TextRegion
from core.tester.contracts.typography import (
    MIN_OCR_CONFIDENCE,
    TextRequirement,
    TypographyAssertion,
    TypographyAssertionResult,
    TypographyEvaluationResult,
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
    OCRStatus,
    TestSurface,
    TypographyCheckType,
    TypographyEvaluationStatus,
    VisibilityState,
)

logger = logging.getLogger("AutonomOS.Tester.TypographyEvaluator")


def utc_now() -> str:
    """Generate ISO 8601 UTC timestamp."""
    return datetime.now(timezone.utc).isoformat()


# Subjective aesthetic patterns that must NEVER be classified as defects
SUBJECTIVE_TYPOGRAPHY_PATTERNS = [
    r"font\s+looks?\s+(?:ugly|cheap|boring|bad|dated|weird)",
    r"font\s+is\s+(?:boring|ugly|bad|too\s+plain)",
    r"font\s+should\s+be\s+bigger",
    r"spacing\s+feels?\s+(?:weird|off|awkward|bad)",
    r"prefer\s+another\s+(?:typeface|font)",
    r"aesthetic(?:s|\s+appeal)?",
    r"modern\s+look",
    r"visual\s+beauty",
]


class TypographyEvaluator:
    """
    Phase 6.4: Deterministic Typography & Text Presentation Evaluation Engine.

    Evaluates measurable text presentation problems using OCR, geometry observations,
    screenshots, and explicit TestCase expectations.

    Guarantees:
    1. Zero Subjective Defects: Subjective font preferences ('looks ugly', 'is boring')
       are strictly filtered and NEVER converted into defects.
    2. Non-Authoritative OCR Handling: Low OCR confidence (< 0.6) or missing OCR returns
       NOT_VERIFIED instead of falsely failing or passing.
    3. Concrete Measurable Checks: Evaluates required text visibility, text clipping,
       text overlap, placement outside expected region, and severe text wrapping.
    4. Intentional Text Wrapping: Multi-line wrapping in standard content flows is recognized
       as intentional and not penalized unless explicit single-line or line-limit constraints exist.
    5. Zero Automatic Fixing: Does not modify product source or attempt automated fixes.
    6. Strict Provenance & Lineage: Enforces project_id and execution_id isolation.
    """
    __test__ = False

    def __init__(
        self,
        execution_id: Optional[str] = None,
        project_id: Optional[str] = None,
        work_order_id: Optional[str] = None,
        min_ocr_confidence: float = MIN_OCR_CONFIDENCE,
    ) -> None:
        self.execution_id = execution_id
        self.project_id = project_id
        self.work_order_id = work_order_id
        self.min_ocr_confidence = float(min_ocr_confidence)

    # ---------------------------------------------------------------------------
    # Check 1: Required Text Presentation & Visibility
    # ---------------------------------------------------------------------------

    def evaluate_required_text(
        self,
        required_text: str,
        ocr_result: Optional[OCRResult] = None,
        target_element_id: Optional[str] = None,
        is_exact: bool = False,
        is_case_sensitive: bool = False,
        test_case_id: Optional[str] = None,
        test_step_id: Optional[str] = None,
        source_evidence_ids: Optional[Sequence[str]] = None,
    ) -> TypographyAssertionResult:
        """
        Evaluate whether an explicitly required text string is visible on screen.

        Rules:
        - If OCR is missing or failed: returns NOT_VERIFIED.
        - If OCR overall confidence is low (< min_ocr_confidence): returns NOT_VERIFIED.
        - If matching text is found with reliable confidence: returns PASS.
        - If matching text is absent with reliable OCR confidence: returns FAIL (defect).
        """
        assertion_id = new_typography_evaluation_id()
        elem_id = target_element_id or "text_element"
        ev_ids = self._collect_evidence_ids(ocr_result, source_evidence_ids)

        if not required_text or not str(required_text).strip():
            raise TesterValidationError("required_text must be a non-empty string.")

        # Lineage check
        if ocr_result is not None:
            self._validate_ocr_lineage(ocr_result)

        # 1. OCR Missing or Failed -> NOT_VERIFIED
        if ocr_result is None:
            return TypographyAssertionResult(
                assertion_id=assertion_id,
                check_type=TypographyCheckType.REQUIRED_TEXT_MISSING,
                status=TypographyEvaluationStatus.NOT_VERIFIED,
                target_element_id=elem_id,
                description=f"OCR result unavailable; cannot reliably verify presence of required text '{required_text}'.",
                evidence_ids=ev_ids,
                test_case_id=test_case_id,
                test_step_id=test_step_id,
            )

        if ocr_result.status != OCRStatus.SUCCESS:
            return TypographyAssertionResult(
                assertion_id=assertion_id,
                check_type=TypographyCheckType.REQUIRED_TEXT_MISSING,
                status=TypographyEvaluationStatus.NOT_VERIFIED,
                target_element_id=elem_id,
                ocr_confidence=ocr_result.confidence,
                description=(
                    f"OCR status is {ocr_result.status.value}; required text '{required_text}' "
                    "cannot be authoritatively verified."
                ),
                evidence_ids=ev_ids,
                test_case_id=test_case_id,
                test_step_id=test_step_id,
            )

        # 2. OCR Low Confidence Guard -> NOT_VERIFIED
        if ocr_result.confidence < self.min_ocr_confidence:
            return TypographyAssertionResult(
                assertion_id=assertion_id,
                check_type=TypographyCheckType.REQUIRED_TEXT_MISSING,
                status=TypographyEvaluationStatus.NOT_VERIFIED,
                target_element_id=elem_id,
                ocr_confidence=ocr_result.confidence,
                description=(
                    f"OCR confidence ({ocr_result.confidence:.2f}) is below reliable threshold "
                    f"({self.min_ocr_confidence:.2f}); cannot authoritatively verify presence or absence "
                    f"of '{required_text}'."
                ),
                evidence_ids=ev_ids,
                test_case_id=test_case_id,
                test_step_id=test_step_id,
            )

        # 3. Text Search in Recognized Regions and Extracted Text
        req_needle = required_text if is_case_sensitive else required_text.lower()
        full_haystack = ocr_result.extracted_text if is_case_sensitive else ocr_result.extracted_text.lower()

        found_match = False
        matching_region: Optional[TextRegion] = None

        # Check individual regions first for precise matching
        for region in ocr_result.text_regions:
            r_text = region.text if is_case_sensitive else region.text.lower()
            if is_exact:
                if req_needle == r_text.strip():
                    found_match = True
                    matching_region = region
                    break
            else:
                if req_needle in r_text:
                    found_match = True
                    matching_region = region
                    break

        # Fallback to full extracted text search
        if not found_match and (req_needle in full_haystack if not is_exact else req_needle == full_haystack.strip()):
            found_match = True

        # If found, verify matching region confidence if applicable
        if found_match:
            if matching_region is not None and matching_region.confidence < self.min_ocr_confidence:
                return TypographyAssertionResult(
                    assertion_id=assertion_id,
                    check_type=TypographyCheckType.REQUIRED_TEXT_MISSING,
                    status=TypographyEvaluationStatus.NOT_VERIFIED,
                    target_element_id=elem_id,
                    detected_text=matching_region.text,
                    ocr_confidence=matching_region.confidence,
                    description=(
                        f"Candidate match for '{required_text}' has low region confidence "
                        f"({matching_region.confidence:.2f}); marked NOT_VERIFIED."
                    ),
                    evidence_ids=ev_ids,
                    test_case_id=test_case_id,
                    test_step_id=test_step_id,
                )

            return TypographyAssertionResult(
                assertion_id=assertion_id,
                check_type=TypographyCheckType.REQUIRED_TEXT_MISSING,
                status=TypographyEvaluationStatus.PASS,
                target_element_id=elem_id,
                detected_text=matching_region.text if matching_region else required_text,
                ocr_confidence=matching_region.confidence if matching_region else ocr_result.confidence,
                measured_values={"required_text": required_text, "found": True},
                description=f"Required text '{required_text}' is clearly visible and verified by OCR.",
                evidence_ids=ev_ids,
                test_case_id=test_case_id,
                test_step_id=test_step_id,
            )

        # 4. Text definitively absent -> FAIL
        is_critical = any(kw in required_text.lower() or kw in elem_id.lower() for kw in ("heading", "title", "checkout", "submit", "order", "login"))
        severity = DefectSeverity.CRITICAL if is_critical else DefectSeverity.HIGH

        defect = self._create_typography_defect(
            title=f"Missing Required Text: '{required_text}' is not visible",
            description=(
                f"Explicitly required text '{required_text}' was not detected in OCR extraction "
                f"(overall OCR confidence: {ocr_result.confidence:.2f})."
            ),
            severity=severity,
            check_type=TypographyCheckType.REQUIRED_TEXT_MISSING,
            expected_behavior=f"Required text '{required_text}' should be prominently visible on the surface.",
            observed_behavior=f"Required text '{required_text}' was not detected in visual rendering.",
            reproduction_steps=[
                f"Inspect rendered interface for '{elem_id}'.",
                f"Search for text '{required_text}'.",
                "Observe text is completely missing from visual presentation.",
            ],
            affected_components=[elem_id],
            test_case_id=test_case_id,
            evidence_ids=ev_ids,
        )

        return TypographyAssertionResult(
            assertion_id=assertion_id,
            check_type=TypographyCheckType.REQUIRED_TEXT_MISSING,
            status=TypographyEvaluationStatus.FAIL,
            target_element_id=elem_id,
            measured_values={"required_text": required_text, "found": False, "ocr_confidence": ocr_result.confidence},
            ocr_confidence=ocr_result.confidence,
            description=f"Required text '{required_text}' is absent from visual rendering.",
            defect=defect,
            evidence_ids=ev_ids,
            test_case_id=test_case_id,
            test_step_id=test_step_id,
        )

    # ---------------------------------------------------------------------------
    # Check 2: Text Clipping Evaluation
    # ---------------------------------------------------------------------------

    def evaluate_text_clipping(
        self,
        text_region: TextRegion,
        container_obs: GeometryObservation,
        tolerance_px: float = 0.0,
        test_case_id: Optional[str] = None,
        test_step_id: Optional[str] = None,
        source_evidence_ids: Optional[Sequence[str]] = None,
    ) -> TypographyAssertionResult:
        """
        Evaluate whether rendered text extends outside its designated container bounding box.
        """
        assertion_id = new_typography_evaluation_id()
        elem_id = container_obs.element_id or "container"
        ev_ids = self._collect_evidence_ids(None, source_evidence_ids, [container_obs.source_evidence_id])

        self._validate_geometry_lineage(container_obs)

        if container_obs.status != GeometryStatus.AVAILABLE or container_obs.bounding_box is None:
            return TypographyAssertionResult(
                assertion_id=assertion_id,
                check_type=TypographyCheckType.TEXT_CLIPPING,
                status=TypographyEvaluationStatus.NOT_VERIFIED,
                target_element_id=elem_id,
                description=f"Container '{elem_id}' geometry is unavailable; cannot evaluate text clipping.",
                evidence_ids=ev_ids,
                test_case_id=test_case_id,
                test_step_id=test_step_id,
            )

        c_rect = container_obs.bounding_box
        t_bbox = text_region.bounding_box
        t_rect = Rectangle(x=t_bbox.x, y=t_bbox.y, width=t_bbox.width, height=t_bbox.height)

        over_left = max(0.0, c_rect.left - t_rect.left)
        over_top = max(0.0, c_rect.top - t_rect.top)
        over_right = max(0.0, t_rect.right - c_rect.right)
        over_bottom = max(0.0, t_rect.bottom - c_rect.bottom)
        max_overflow = max(over_left, over_top, over_right, over_bottom)

        measured = {
            "text": text_region.text,
            "overflow_distance": float(max_overflow),
            "overflow_left": float(over_left),
            "overflow_top": float(over_top),
            "overflow_right": float(over_right),
            "overflow_bottom": float(over_bottom),
            "text_bounds": t_rect.to_dict(),
            "container_bounds": c_rect.to_dict(),
        }

        if max_overflow <= tolerance_px:
            return TypographyAssertionResult(
                assertion_id=assertion_id,
                check_type=TypographyCheckType.TEXT_CLIPPING,
                status=TypographyEvaluationStatus.PASS,
                target_element_id=elem_id,
                detected_text=text_region.text,
                measured_values=measured,
                ocr_confidence=text_region.confidence,
                description=f"Text '{text_region.text}' is fully contained within '{elem_id}'.",
                evidence_ids=ev_ids,
                test_case_id=test_case_id,
                test_step_id=test_step_id,
            )

        severity = DefectSeverity.HIGH if max_overflow >= 10.0 else DefectSeverity.MEDIUM
        defect = self._create_typography_defect(
            title=f"Text Clipping: Text '{text_region.text}' clipped by container '{elem_id}'",
            description=(
                f"Text region '{text_region.text}' extends {max_overflow:.1f}px outside bounding box of '{elem_id}' "
                f"(left: {over_left:.1f}px, top: {over_top:.1f}px, right: {over_right:.1f}px, bottom: {over_bottom:.1f}px)."
            ),
            severity=severity,
            check_type=TypographyCheckType.TEXT_CLIPPING,
            expected_behavior=f"Text '{text_region.text}' should fit completely within container '{elem_id}'.",
            observed_behavior=f"Text clipped by {max_overflow:.1f}px outside container edges.",
            reproduction_steps=[
                f"Inspect container '{elem_id}' containing text '{text_region.text}'.",
                f"Observe text boundaries protruding {max_overflow:.1f}px beyond container edges.",
            ],
            affected_components=[elem_id],
            test_case_id=test_case_id,
            evidence_ids=ev_ids,
        )

        return TypographyAssertionResult(
            assertion_id=assertion_id,
            check_type=TypographyCheckType.TEXT_CLIPPING,
            status=TypographyEvaluationStatus.FAIL,
            target_element_id=elem_id,
            detected_text=text_region.text,
            measured_values=measured,
            ocr_confidence=text_region.confidence,
            description=f"Text '{text_region.text}' is clipped by {max_overflow:.1f}px.",
            defect=defect,
            evidence_ids=ev_ids,
            test_case_id=test_case_id,
            test_step_id=test_step_id,
        )

    # ---------------------------------------------------------------------------
    # Check 3: Text Overlap Evaluation
    # ---------------------------------------------------------------------------

    def evaluate_text_overlap(
        self,
        region_a: TextRegion,
        region_b: TextRegion,
        tolerance_px: float = 0.0,
        test_case_id: Optional[str] = None,
        test_step_id: Optional[str] = None,
        source_evidence_ids: Optional[Sequence[str]] = None,
    ) -> TypographyAssertionResult:
        """
        Evaluate whether two distinct recognized text regions overlap or collide.
        """
        assertion_id = new_typography_evaluation_id()
        ev_ids = list(source_evidence_ids or [])
        if not ev_ids:
            ev_ids = [new_evidence_id()]

        rect_a = Rectangle(x=region_a.bounding_box.x, y=region_a.bounding_box.y, width=region_a.bounding_box.width, height=region_a.bounding_box.height)
        rect_b = Rectangle(x=region_b.bounding_box.x, y=region_b.bounding_box.y, width=region_b.bounding_box.width, height=region_b.bounding_box.height)

        inter = rect_a.intersection(rect_b)
        overlap_area = inter.area if inter is not None else 0.0

        measured = {
            "text_a": region_a.text,
            "text_b": region_b.text,
            "overlap_area": float(overlap_area),
            "rect_a": rect_a.to_dict(),
            "rect_b": rect_b.to_dict(),
        }

        # Substantial collision check
        if inter is not None and overlap_area > 25.0 and inter.width > 2.0 and inter.height > 2.0:
            defect = self._create_typography_defect(
                title=f"Text Overlap: '{region_a.text}' collides with '{region_b.text}'",
                description=(
                    f"Text regions '{region_a.text}' and '{region_b.text}' visually overlap "
                    f"with collision area {overlap_area:.1f}px²."
                ),
                severity=DefectSeverity.HIGH,
                check_type=TypographyCheckType.TEXT_OVERLAP,
                expected_behavior=f"Text regions '{region_a.text}' and '{region_b.text}' should maintain distinct visual spacing.",
                observed_behavior=f"Text regions collide with {overlap_area:.1f}px² intersection area.",
                reproduction_steps=[
                    f"Inspect layout of '{region_a.text}' and '{region_b.text}'.",
                    f"Observe text collision ({overlap_area:.1f}px² overlap area).",
                ],
                affected_components=[region_a.text[:20], region_b.text[:20]],
                test_case_id=test_case_id,
                evidence_ids=ev_ids,
            )

            return TypographyAssertionResult(
                assertion_id=assertion_id,
                check_type=TypographyCheckType.TEXT_OVERLAP,
                status=TypographyEvaluationStatus.FAIL,
                target_element_id=region_a.text[:20],
                detected_text=f"{region_a.text} / {region_b.text}",
                measured_values=measured,
                description=f"Text regions '{region_a.text}' and '{region_b.text}' overlap by {overlap_area:.1f}px².",
                defect=defect,
                evidence_ids=ev_ids,
                test_case_id=test_case_id,
                test_step_id=test_step_id,
            )

        return TypographyAssertionResult(
            assertion_id=assertion_id,
            check_type=TypographyCheckType.TEXT_OVERLAP,
            status=TypographyEvaluationStatus.PASS,
            target_element_id=region_a.text[:20],
            detected_text=region_a.text,
            measured_values=measured,
            description=f"Text regions '{region_a.text}' and '{region_b.text}' maintain proper visual separation.",
            evidence_ids=ev_ids,
            test_case_id=test_case_id,
            test_step_id=test_step_id,
        )

    # ---------------------------------------------------------------------------
    # Check 4: Text Rendered Outside Expected Region
    # ---------------------------------------------------------------------------

    def evaluate_text_in_expected_region(
        self,
        text_region: TextRegion,
        expected_region: Rectangle,
        tolerance_px: float = 0.0,
        test_case_id: Optional[str] = None,
        test_step_id: Optional[str] = None,
        source_evidence_ids: Optional[Sequence[str]] = None,
    ) -> TypographyAssertionResult:
        """
        Evaluate whether text is rendered within its explicitly expected screen region.
        """
        assertion_id = new_typography_evaluation_id()
        ev_ids = list(source_evidence_ids or [])
        if not ev_ids:
            ev_ids = [new_evidence_id()]

        rect = Rectangle(x=text_region.bounding_box.x, y=text_region.bounding_box.y, width=text_region.bounding_box.width, height=text_region.bounding_box.height)

        over_left = max(0.0, expected_region.left - rect.left)
        over_top = max(0.0, expected_region.top - rect.top)
        over_right = max(0.0, rect.right - expected_region.right)
        over_bottom = max(0.0, rect.bottom - expected_region.bottom)
        max_displacement = max(over_left, over_top, over_right, over_bottom)

        measured = {
            "text": text_region.text,
            "displacement": float(max_displacement),
            "text_bounds": rect.to_dict(),
            "expected_region": expected_region.to_dict(),
        }

        if max_displacement <= tolerance_px:
            return TypographyAssertionResult(
                assertion_id=assertion_id,
                check_type=TypographyCheckType.TEXT_OUTSIDE_EXPECTED_REGION,
                status=TypographyEvaluationStatus.PASS,
                target_element_id=text_region.text[:20],
                detected_text=text_region.text,
                measured_values=measured,
                ocr_confidence=text_region.confidence,
                description=f"Text '{text_region.text}' is correctly positioned within expected region.",
                evidence_ids=ev_ids,
                test_case_id=test_case_id,
                test_step_id=test_step_id,
            )

        defect = self._create_typography_defect(
            title=f"Text Outside Expected Region: '{text_region.text}' displaced from target area",
            description=(
                f"Text '{text_region.text}' rendered {max_displacement:.1f}px outside expected target boundary "
                f"({expected_region.width:.0f}x{expected_region.height:.0f} at x={expected_region.x:.0f}, y={expected_region.y:.0f})."
            ),
            severity=DefectSeverity.HIGH if max_displacement >= 20.0 else DefectSeverity.MEDIUM,
            check_type=TypographyCheckType.TEXT_OUTSIDE_EXPECTED_REGION,
            expected_behavior=f"Text '{text_region.text}' should render within target region bounds.",
            observed_behavior=f"Text displaced by {max_displacement:.1f}px outside target region.",
            reproduction_steps=[
                f"Inspect rendered position of text '{text_region.text}'.",
                "Observe text located outside the authorized target coordinates.",
            ],
            affected_components=[text_region.text[:20]],
            test_case_id=test_case_id,
            evidence_ids=ev_ids,
        )

        return TypographyAssertionResult(
            assertion_id=assertion_id,
            check_type=TypographyCheckType.TEXT_OUTSIDE_EXPECTED_REGION,
            status=TypographyEvaluationStatus.FAIL,
            target_element_id=text_region.text[:20],
            detected_text=text_region.text,
            measured_values=measured,
            ocr_confidence=text_region.confidence,
            description=f"Text '{text_region.text}' is rendered outside expected region ({max_displacement:.1f}px displacement).",
            defect=defect,
            evidence_ids=ev_ids,
            test_case_id=test_case_id,
            test_step_id=test_step_id,
        )

    # ---------------------------------------------------------------------------
    # Check 5: Severe Wrapping vs Intentional Wrapping
    # ---------------------------------------------------------------------------

    def evaluate_text_wrapping(
        self,
        text_content: str,
        line_regions: Sequence[TextRegion],
        max_allowed_lines: Optional[int] = None,
        is_single_line_required: bool = False,
        test_case_id: Optional[str] = None,
        test_step_id: Optional[str] = None,
        source_evidence_ids: Optional[Sequence[str]] = None,
    ) -> TypographyAssertionResult:
        """
        Evaluate text wrapping behavior.

        Distinguishes:
        - Intentional text wrapping: Normal multi-line wrapping in standard paragraph/article flows (PASS).
        - Severe required wrapping: Content violating an explicit single-line or line-limit requirement (FAIL).
        """
        assertion_id = new_typography_evaluation_id()
        ev_ids = list(source_evidence_ids or [])
        if not ev_ids:
            ev_ids = [new_evidence_id()]

        num_lines = len(line_regions)
        max_lines = 1 if is_single_line_required else (max_allowed_lines or 0)

        measured = {
            "text": text_content,
            "observed_lines": num_lines,
            "max_allowed_lines": max_lines,
            "is_single_line_required": is_single_line_required,
        }

        # Case A: Explicit line limit or single-line requirement violated
        if max_lines > 0 and num_lines > max_lines:
            is_button = any(kw in text_content.lower() for kw in ("button", "btn", "submit", "checkout", "login", "order"))
            severity = DefectSeverity.HIGH if (is_button or num_lines >= max_lines + 2) else DefectSeverity.MEDIUM

            defect = self._create_typography_defect(
                title=f"Severe Text Wrapping: '{text_content}' wrapped onto {num_lines} lines (max expected: {max_lines})",
                description=(
                    f"Text '{text_content}' was required to occupy at most {max_lines} line(s), "
                    f"but rendered wrapped onto {num_lines} lines."
                ),
                severity=severity,
                check_type=TypographyCheckType.SEVERE_WRAPPING,
                expected_behavior=f"Text '{text_content}' should be rendered on at most {max_lines} line(s).",
                observed_behavior=f"Text wrapped into {num_lines} lines.",
                reproduction_steps=[
                    f"Inspect element rendering text '{text_content}'.",
                    f"Count visual line breaks; observe {num_lines} lines instead of maximum {max_lines}.",
                ],
                affected_components=[text_content[:20]],
                test_case_id=test_case_id,
                evidence_ids=ev_ids,
            )

            return TypographyAssertionResult(
                assertion_id=assertion_id,
                check_type=TypographyCheckType.SEVERE_WRAPPING,
                status=TypographyEvaluationStatus.FAIL,
                target_element_id=text_content[:20],
                detected_text=text_content,
                measured_values=measured,
                description=f"Severe wrapping: Text rendered across {num_lines} lines (max expected: {max_lines}).",
                defect=defect,
                evidence_ids=ev_ids,
                test_case_id=test_case_id,
                test_step_id=test_step_id,
            )

        # Case B: Standard intentional multi-line wrapping in paragraph or general content
        return TypographyAssertionResult(
            assertion_id=assertion_id,
            check_type=TypographyCheckType.SEVERE_WRAPPING,
            status=TypographyEvaluationStatus.PASS,
            target_element_id=text_content[:20],
            detected_text=text_content,
            measured_values=measured,
            description=f"Text '{text_content}' wrapping ({num_lines} lines) is intentional and within authorized limits.",
            evidence_ids=ev_ids,
            test_case_id=test_case_id,
            test_step_id=test_step_id,
        )

    # ---------------------------------------------------------------------------
    # Subjective Typography & Aesthetic Filter
    # ---------------------------------------------------------------------------

    def filter_subjective_aesthetic_input(
        self,
        critique_text: str,
        test_case_id: Optional[str] = None,
    ) -> tuple[bool, Optional[TesterFinding]]:
        """
        Check if an input critique represents subjective aesthetic preference.

        Returns (is_subjective, optional_recommendation).
        Guarantees: Subjective aesthetic critiques are NEVER converted to defects.
        """
        c_lower = critique_text.lower()
        is_subjective = any(re.search(pat, c_lower) for pat in SUBJECTIVE_TYPOGRAPHY_PATTERNS)

        if not is_subjective:
            return (False, None)

        # Build recommendation finding only if actionable context is provided
        finding = TesterFinding(
            finding_id=new_finding_id(),
            category=FindingCategory.RECOMMENDATION,
            title=f"Typography Suggestion: {critique_text[:60]}",
            description=f"Subjective aesthetic opinion noted: '{critique_text}'. Not classified as a functional defect.",
            affected_area="typography",
            confidence=0.5,
            metadata={
                "execution_id": self.execution_id or "texec-default",
                "evaluator": "TypographyEvaluator",
                "phase": "Phase 6.4",
                "is_subjective_aesthetic": True,
            },
        )
        return (True, finding)

    # ---------------------------------------------------------------------------
    # Full Evaluation Aggregator
    # ---------------------------------------------------------------------------

    def evaluate_typography(
        self,
        assertions: Sequence[TypographyAssertion],
        ocr_result: Optional[OCRResult] = None,
        geometry_observations: Optional[Sequence[GeometryObservation]] = None,
        screenshot_evidence_ids: Optional[Sequence[str]] = None,
        test_case_id: Optional[str] = None,
        test_step_id: Optional[str] = None,
    ) -> TypographyEvaluationResult:
        """
        Evaluate a sequence of typography assertions against OCR and geometry observations.
        """
        eval_id = new_typography_evaluation_id()
        results: list[TypographyAssertionResult] = []
        defects: list[TesterDefect] = []
        findings: list[TesterFinding] = []
        all_evidence = set(screenshot_evidence_ids or [])

        if ocr_result:
            all_evidence.add(ocr_result.source_evidence_id)

        # Geometry lookup by element_id
        geom_map: dict[str, GeometryObservation] = {}
        if geometry_observations:
            for g in geometry_observations:
                if g.element_id:
                    geom_map[g.element_id] = g

        for assertion in assertions:
            # Check for subjective aesthetic assertion
            is_subj, rec = self.filter_subjective_aesthetic_input(
                critique_text=assertion.required_text or assertion.metadata.get("critique", ""),
                test_case_id=test_case_id,
            )
            if is_subj:
                if rec:
                    findings.append(rec)
                results.append(TypographyAssertionResult(
                    assertion_id=assertion.assertion_id,
                    check_type=TypographyCheckType.SUBJECTIVE_AESTHETIC,
                    status=TypographyEvaluationStatus.SKIPPED,
                    target_element_id=assertion.target_element_id,
                    description=f"Subjective aesthetic critique skipped from defect classification.",
                    recommendation=rec,
                    test_case_id=test_case_id,
                ))
                continue

            if assertion.check_type == TypographyCheckType.REQUIRED_TEXT_MISSING:
                res = self.evaluate_required_text(
                    required_text=assertion.required_text or "",
                    ocr_result=ocr_result,
                    target_element_id=assertion.target_element_id,
                    test_case_id=test_case_id,
                    test_step_id=test_step_id,
                    source_evidence_ids=screenshot_evidence_ids,
                )
                results.append(res)
                if res.defect:
                    defects.append(res.defect)

            elif assertion.check_type == TypographyCheckType.TEXT_CLIPPING:
                container_id = assertion.expected_container_id or assertion.target_element_id
                c_obs = geom_map.get(container_id) if container_id else None
                if c_obs and ocr_result and ocr_result.text_regions:
                    t_region = next((r for r in ocr_result.text_regions if assertion.required_text in r.text), ocr_result.text_regions[0])
                    res = self.evaluate_text_clipping(
                        text_region=t_region,
                        container_obs=c_obs,
                        test_case_id=test_case_id,
                        test_step_id=test_step_id,
                        source_evidence_ids=screenshot_evidence_ids,
                    )
                    results.append(res)
                    if res.defect:
                        defects.append(res.defect)
                else:
                    results.append(TypographyAssertionResult(
                        assertion_id=assertion.assertion_id,
                        check_type=TypographyCheckType.TEXT_CLIPPING,
                        status=TypographyEvaluationStatus.NOT_VERIFIED,
                        target_element_id=assertion.target_element_id,
                        description="Insufficient geometry or OCR regions to evaluate text clipping.",
                        test_case_id=test_case_id,
                    ))

        has_fail = any(r.is_fail for r in results)
        has_not_verified = any(r.is_not_verified for r in results)

        if has_fail:
            overall_status = TypographyEvaluationStatus.FAIL
        elif has_not_verified:
            overall_status = TypographyEvaluationStatus.NOT_VERIFIED
        else:
            overall_status = TypographyEvaluationStatus.PASS

        return TypographyEvaluationResult(
            evaluation_id=eval_id,
            status=overall_status,
            assertion_results=results,
            defects=defects,
            findings=findings,
            evidence_ids=sorted(list(all_evidence)),
            test_case_id=test_case_id,
            test_step_id=test_step_id,
            evaluated_at=utc_now(),
            metadata={
                "project_id": self.project_id,
                "execution_id": self.execution_id,
                "work_order_id": self.work_order_id,
            },
        )

    # ---------------------------------------------------------------------------
    # Internal Helpers
    # ---------------------------------------------------------------------------

    def _create_typography_defect(
        self,
        title: str,
        description: str,
        severity: DefectSeverity,
        check_type: TypographyCheckType,
        expected_behavior: str,
        observed_behavior: str,
        reproduction_steps: list[str],
        affected_components: list[str],
        test_case_id: Optional[str] = None,
        evidence_ids: Optional[list[str]] = None,
    ) -> TesterDefect:
        """Construct an authoritative TesterDefect for typography/text failures."""
        ev_list = list(evidence_ids or [])
        if not ev_list:
            ev_list = [new_evidence_id()]

        return TesterDefect(
            defect_id=new_defect_id(),
            work_order_id=self.work_order_id or "two-default",
            title=title,
            description=description,
            severity=severity,
            defect_type=DefectType.TYPOGRAPHY,
            execution_id=self.execution_id,
            reproduction_steps=reproduction_steps,
            expected_behavior=expected_behavior,
            observed_behavior=observed_behavior,
            affected_components=affected_components,
            test_id=test_case_id,
            evidence_ids=ev_list,
            confidence=1.0,
            provenance={
                "evaluator": "TypographyEvaluator",
                "phase": "Phase 6.4",
                "check_type": check_type.value,
                "affected_surface": TestSurface.VISUAL_LAYOUT.value,
            },
            metadata={
                "check_type": check_type.value,
            },
        )

    def _collect_evidence_ids(
        self,
        ocr_result: Optional[OCRResult] = None,
        source_evidence_ids: Optional[Sequence[str]] = None,
        extra_ids: Optional[Sequence[Optional[str]]] = None,
    ) -> list[str]:
        """Aggregate unique evidence IDs supporting an evaluation."""
        ev_set = set()
        if source_evidence_ids:
            ev_set.update(source_evidence_ids)
        if ocr_result and ocr_result.source_evidence_id:
            ev_set.add(ocr_result.source_evidence_id)
        if extra_ids:
            for eid in extra_ids:
                if eid:
                    ev_set.add(eid)

        if not ev_set:
            ev_set.add(new_evidence_id())

        return sorted(list(ev_set))

    def _validate_ocr_lineage(self, ocr: OCRResult) -> None:
        """Validate execution_id and project_id lineage on OCRResult."""
        if self.execution_id and ocr.execution_id and ocr.execution_id != self.execution_id:
            raise TesterLineageError(
                f"OCRResult execution_id ('{ocr.execution_id}') does not match evaluator ('{self.execution_id}')."
            )
        if self.project_id and ocr.project_id and ocr.project_id != self.project_id:
            raise TesterBoundaryViolationError(
                action="TYPOGRAPHY_PROJECT_ISOLATION",
                reason=f"OCRResult project_id ('{ocr.project_id}') does not match evaluator ('{self.project_id}').",
            )

    def _validate_geometry_lineage(self, obs: GeometryObservation) -> None:
        """Validate execution_id and project_id lineage on GeometryObservation."""
        if self.execution_id and obs.execution_id and obs.execution_id != self.execution_id:
            raise TesterLineageError(
                f"Observation execution_id ('{obs.execution_id}') does not match evaluator ('{self.execution_id}')."
            )
        if self.project_id and obs.project_id and obs.project_id != self.project_id:
            raise TesterBoundaryViolationError(
                action="TYPOGRAPHY_PROJECT_ISOLATION",
                reason=f"Observation project_id ('{obs.project_id}') does not match evaluator ('{self.project_id}').",
            )

    def apply_fix(self, *args, **kwargs) -> Any:
        """Zero fixing guard: Typography evaluator does NOT modify product source or attempt automated fixes."""
        raise TesterBoundaryViolationError(
            action="TYPOGRAPHY_AUTO_FIX",
            reason=(
                "TypographyEvaluator is strictly an evaluation component. "
                "Tester does not perform automatic fixes on product or source code."
            ),
        )

    def auto_fix(self, *args, **kwargs) -> Any:
        return self.apply_fix(*args, **kwargs)
