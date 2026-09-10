from __future__ import annotations

import unittest

from core.tester.contracts.finding import TesterDefect, TesterFinding
from core.tester.contracts.geometry import (
    CoordinateSystem,
    GeometryObservation,
    GeometryStatus,
    Rectangle,
    ViewportDimensions,
)
from core.tester.contracts.identifiers import (
    new_evidence_id,
    new_execution_id,
    new_geometry_id,
    new_ocr_id,
    new_typography_evaluation_id,
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
from core.tester.evaluator.typography_evaluator import TypographyEvaluator
from core.tester.types import (
    DefectSeverity,
    DefectType,
    FindingCategory,
    OCRStatus,
    TypographyCheckType,
    TypographyEvaluationStatus,
    VisibilityState,
)


class TestTesterTypographyEvaluator(unittest.TestCase):
    """
    Comprehensive test suite for Phase 6.4 Typography & Text Presentation Evaluation.
    Covers all 10 required test scenarios + boundary invariants & serialization:
    1. visible required text
    2. missing required text
    3. clipped text
    4. overlapping text
    5. low OCR confidence
    6. intentional text wrapping
    7. severe required-content wrapping
    8. subjective font preference
    9. evidence lineage
    10. NOT_VERIFIED behavior
    11. zero fixing guard
    12. contract serialization
    """

    def setUp(self) -> None:
        self.exec_id = new_execution_id()
        self.project_id = "test-project-typo"
        self.work_order_id = "two-typo-001"
        self.evaluator = TypographyEvaluator(
            execution_id=self.exec_id,
            project_id=self.project_id,
            work_order_id=self.work_order_id,
            min_ocr_confidence=0.6,
        )

    def _make_ocr_result(
        self,
        text: str,
        regions: list[TextRegion] | None = None,
        confidence: float = 0.95,
        status: OCRStatus = OCRStatus.SUCCESS,
        evidence_id: str | None = None,
        project_id: str | None = None,
        execution_id: str | None = None,
    ) -> OCRResult:
        ev_id = evidence_id or new_evidence_id()
        regs = regions or [
            TextRegion(
                text=text,
                bounding_box=BoundingBox(x=100.0, y=50.0, width=300.0, height=40.0),
                confidence=confidence,
            )
        ]
        return OCRResult(
            ocr_id=new_ocr_id(),
            execution_id=execution_id or self.exec_id,
            project_id=project_id or self.project_id,
            source_evidence_id=ev_id,
            status=status,
            extracted_text=text,
            text_regions=regs,
            confidence=confidence,
        )

    def _make_container_obs(
        self,
        element_id: str,
        x: float,
        y: float,
        width: float,
        height: float,
        evidence_id: str | None = None,
        project_id: str | None = None,
        execution_id: str | None = None,
        status: GeometryStatus = GeometryStatus.AVAILABLE,
    ) -> GeometryObservation:
        ev_id = evidence_id or new_evidence_id()
        return GeometryObservation(
            geometry_id=new_geometry_id(),
            element_id=element_id,
            bounding_box=Rectangle(x=x, y=y, width=width, height=height) if status == GeometryStatus.AVAILABLE else None,
            status=status,
            visibility_state=VisibilityState.VISIBLE,
            viewport=ViewportDimensions(width=1280, height=800),
            execution_id=execution_id or self.exec_id,
            project_id=project_id or self.project_id,
            source_evidence_id=ev_id,
        )

    # ---------------------------------------------------------------------------
    # Test 1: Visible Required Text
    # ---------------------------------------------------------------------------

    def test_visible_required_text(self) -> None:
        """When required text is present with high OCR confidence, result is PASS."""
        ocr = self._make_ocr_result(
            text="Welcome to AutonomOS - Autonomous Software Engineering",
            confidence=0.95,
        )

        res = self.evaluator.evaluate_required_text(
            required_text="Welcome to AutonomOS",
            ocr_result=ocr,
            target_element_id="hero_heading",
        )

        self.assertEqual(res.status, TypographyEvaluationStatus.PASS)
        self.assertTrue(res.is_pass)
        self.assertIsNone(res.defect)
        self.assertIn("Welcome to AutonomOS", res.description)

    # ---------------------------------------------------------------------------
    # Test 2: Missing Required Text
    # ---------------------------------------------------------------------------

    def test_missing_required_text(self) -> None:
        """When required text is absent with high OCR confidence, result is FAIL with defect."""
        ocr = self._make_ocr_result(
            text="Dashboard Overview - No Welcome Message Here",
            confidence=0.92,
        )

        res = self.evaluator.evaluate_required_text(
            required_text="Welcome to AutonomOS",
            ocr_result=ocr,
            target_element_id="hero_heading",
        )

        self.assertEqual(res.status, TypographyEvaluationStatus.FAIL)
        self.assertTrue(res.is_fail)
        self.assertIsNotNone(res.defect)
        self.assertEqual(res.defect.defect_type, DefectType.TYPOGRAPHY)
        self.assertIn("Missing Required Text", res.defect.title)
        self.assertIn("Welcome to AutonomOS", res.defect.title)

    # ---------------------------------------------------------------------------
    # Test 3: Clipped Text
    # ---------------------------------------------------------------------------

    def test_clipped_text(self) -> None:
        """Text region extending beyond container boundary generates a text clipping defect."""
        # Container is 150px wide; text region is 220px wide (starts at same x)
        container = self._make_container_obs("btn_submit", x=50, y=100, width=150, height=40)
        text_reg = TextRegion(
            text="Confirm Order & Pay Now",
            bounding_box=BoundingBox(x=50, y=100, width=220, height=40),
            confidence=0.9,
        )

        res = self.evaluator.evaluate_text_clipping(
            text_region=text_reg,
            container_obs=container,
            tolerance_px=0.0,
        )

        self.assertEqual(res.status, TypographyEvaluationStatus.FAIL)
        self.assertTrue(res.is_fail)
        self.assertIsNotNone(res.defect)
        self.assertIn("Text Clipping", res.defect.title)
        self.assertEqual(res.defect.defect_type, DefectType.TYPOGRAPHY)
        self.assertIn("btn_submit", res.defect.affected_components)

    # ---------------------------------------------------------------------------
    # Test 4: Overlapping Text
    # ---------------------------------------------------------------------------

    def test_overlapping_text(self) -> None:
        """Two text regions colliding with non-trivial intersection generate an overlap defect."""
        reg_a = TextRegion(
            text="Header Title",
            bounding_box=BoundingBox(x=100, y=100, width=200, height=50),
            confidence=0.9,
        )
        # reg_b intersects reg_a (from y=120 to 150, x=110 to 300)
        reg_b = TextRegion(
            text="Subtitle Description",
            bounding_box=BoundingBox(x=110, y=120, width=200, height=50),
            confidence=0.9,
        )

        res = self.evaluator.evaluate_text_overlap(
            region_a=reg_a,
            region_b=reg_b,
        )

        self.assertEqual(res.status, TypographyEvaluationStatus.FAIL)
        self.assertTrue(res.is_fail)
        self.assertIsNotNone(res.defect)
        self.assertIn("Text Overlap", res.defect.title)
        self.assertEqual(res.defect.defect_type, DefectType.TYPOGRAPHY)

    # ---------------------------------------------------------------------------
    # Test 5: Low OCR Confidence
    # ---------------------------------------------------------------------------

    def test_low_ocr_confidence(self) -> None:
        """Low OCR confidence (< 0.6) yields NOT_VERIFIED instead of falsely failing or passing."""
        # Required text is not recognized, but OCR confidence is only 0.40
        ocr = self._make_ocr_result(
            text="some blurry garbled text",
            confidence=0.40,
        )

        res = self.evaluator.evaluate_required_text(
            required_text="Heading must be visible",
            ocr_result=ocr,
        )

        self.assertEqual(res.status, TypographyEvaluationStatus.NOT_VERIFIED)
        self.assertTrue(res.is_not_verified)
        self.assertIsNone(res.defect)
        self.assertIn("below reliable threshold", res.description)

    # ---------------------------------------------------------------------------
    # Test 6: Intentional Text Wrapping
    # ---------------------------------------------------------------------------

    def test_intentional_text_wrapping(self) -> None:
        """Normal multi-line paragraph text wrapping is intentional and evaluates to PASS."""
        lines = [
            TextRegion(text="This is the first line of the description,", bounding_box=BoundingBox(10, 10, 300, 20)),
            TextRegion(text="and this is the second line wrapping naturally,", bounding_box=BoundingBox(10, 35, 300, 20)),
            TextRegion(text="followed by the third line completing the paragraph.", bounding_box=BoundingBox(10, 60, 300, 20)),
        ]

        res = self.evaluator.evaluate_text_wrapping(
            text_content="This is the description paragraph.",
            line_regions=lines,
            max_allowed_lines=None,
            is_single_line_required=False,
        )

        self.assertEqual(res.status, TypographyEvaluationStatus.PASS)
        self.assertTrue(res.is_pass)
        self.assertIsNone(res.defect)

    # ---------------------------------------------------------------------------
    # Test 7: Severe Required-Content Wrapping
    # ---------------------------------------------------------------------------

    def test_severe_required_content_wrapping(self) -> None:
        """Text violating an explicit single-line or line-limit requirement generates a defect."""
        # Button label wrapping across 3 lines when single-line is required
        lines = [
            TextRegion(text="Click", bounding_box=BoundingBox(10, 10, 50, 20)),
            TextRegion(text="Here", bounding_box=BoundingBox(10, 35, 50, 20)),
            TextRegion(text="Now", bounding_box=BoundingBox(10, 60, 50, 20)),
        ]

        res = self.evaluator.evaluate_text_wrapping(
            text_content="Click Here Now",
            line_regions=lines,
            max_allowed_lines=1,
            is_single_line_required=True,
        )

        self.assertEqual(res.status, TypographyEvaluationStatus.FAIL)
        self.assertTrue(res.is_fail)
        self.assertIsNotNone(res.defect)
        self.assertIn("Severe Text Wrapping", res.defect.title)
        self.assertIn("wrapped onto 3 lines", res.defect.description)

    # ---------------------------------------------------------------------------
    # Test 8: Subjective Font Preference
    # ---------------------------------------------------------------------------

    def test_subjective_font_preference(self) -> None:
        """Subjective opinions ('font looks ugly', 'font is boring') are never converted to defects."""
        critiques = [
            "font looks ugly and dated",
            "font is boring",
            "font should be bigger",
            "spacing feels off to me",
            "I prefer another typeface like Roboto",
        ]

        for critique in critiques:
            is_subj, rec = self.evaluator.filter_subjective_aesthetic_input(critique)
            self.assertTrue(is_subj, f"Expected '{critique}' to be recognized as subjective.")
            if rec:
                self.assertEqual(rec.category, FindingCategory.RECOMMENDATION)
                self.assertNotEqual(rec.category, FindingCategory.DEFECT)

        # In aggregate evaluation, subjective critique assertions are SKIPPED, not failed
        subj_assertion = TypographyAssertion(
            assertion_id=new_typography_evaluation_id(),
            check_type=TypographyCheckType.SUBJECTIVE_AESTHETIC,
            required_text="font looks ugly",
        )

        eval_res = self.evaluator.evaluate_typography(
            assertions=[subj_assertion],
        )

        self.assertEqual(eval_res.status, TypographyEvaluationStatus.PASS)
        self.assertEqual(len(eval_res.defects), 0)
        self.assertEqual(len(eval_res.assertion_results), 1)
        self.assertEqual(eval_res.assertion_results[0].status, TypographyEvaluationStatus.SKIPPED)

    # ---------------------------------------------------------------------------
    # Test 9: Evidence Lineage
    # ---------------------------------------------------------------------------

    def test_evidence_lineage(self) -> None:
        """Evaluator rejects foreign project_id or execution_id and preserves evidence provenance."""
        # Project isolation
        foreign_ocr = self._make_ocr_result(
            text="Some Text",
            project_id="foreign-project-xyz",
        )

        with self.assertRaises(TesterBoundaryViolationError):
            self.evaluator.evaluate_required_text(
                required_text="Some Text",
                ocr_result=foreign_ocr,
            )

        # Execution lineage
        foreign_exec_ocr = self._make_ocr_result(
            text="Some Text",
            execution_id="texec-foreign-999",
        )

        with self.assertRaises(TesterLineageError):
            self.evaluator.evaluate_required_text(
                required_text="Some Text",
                ocr_result=foreign_exec_ocr,
            )

        # Evidence provenance on defect
        ev_id = "tevid-verified-001"
        valid_ocr = self._make_ocr_result(
            text="Only other text",
            evidence_id=ev_id,
        )
        res = self.evaluator.evaluate_required_text(
            required_text="Required text",
            ocr_result=valid_ocr,
        )
        self.assertIsNotNone(res.defect)
        self.assertIn(ev_id, res.defect.evidence_ids)

    # ---------------------------------------------------------------------------
    # Test 10: NOT_VERIFIED Behavior
    # ---------------------------------------------------------------------------

    def test_not_verified_behavior(self) -> None:
        """When evidence is missing or insufficient, status is NOT_VERIFIED without defects."""
        # A: OCR Result is None
        res_no_ocr = self.evaluator.evaluate_required_text(
            required_text="Heading must be visible",
            ocr_result=None,
        )
        self.assertEqual(res_no_ocr.status, TypographyEvaluationStatus.NOT_VERIFIED)
        self.assertTrue(res_no_ocr.is_not_verified)
        self.assertIsNone(res_no_ocr.defect)

        # B: OCR Status is FAILED
        failed_ocr = self._make_ocr_result(
            text="",
            status=OCRStatus.FAILED,
        )
        res_failed = self.evaluator.evaluate_required_text(
            required_text="Heading must be visible",
            ocr_result=failed_ocr,
        )
        self.assertEqual(res_failed.status, TypographyEvaluationStatus.NOT_VERIFIED)

        # C: Container geometry UNAVAILABLE for text clipping
        unavail_container = self._make_container_obs("header", 0, 0, 100, 100, status=GeometryStatus.UNAVAILABLE)
        text_reg = TextRegion("Title", BoundingBox(0, 0, 80, 20))
        res_clip_unavail = self.evaluator.evaluate_text_clipping(
            text_region=text_reg,
            container_obs=unavail_container,
        )
        self.assertEqual(res_clip_unavail.status, TypographyEvaluationStatus.NOT_VERIFIED)

    # ---------------------------------------------------------------------------
    # Test 11: Zero Fixing Guard
    # ---------------------------------------------------------------------------

    def test_zero_fixing_guard(self) -> None:
        """apply_fix and auto_fix methods raise TesterBoundaryViolationError."""
        with self.assertRaises(TesterBoundaryViolationError):
            self.evaluator.apply_fix()

        with self.assertRaises(TesterBoundaryViolationError):
            self.evaluator.auto_fix()

        res = TypographyEvaluationResult(
            evaluation_id=new_typography_evaluation_id(),
            status=TypographyEvaluationStatus.PASS,
        )

        with self.assertRaises(TesterBoundaryViolationError):
            res.apply_fix()

        with self.assertRaises(TesterBoundaryViolationError):
            res.auto_fix()

    # ---------------------------------------------------------------------------
    # Test 12: Contract Serialization
    # ---------------------------------------------------------------------------

    def test_contract_serialization(self) -> None:
        """TextRequirement, TypographyAssertion, and results roundtrip via dict."""
        req = TextRequirement(
            requirement_id="req-1",
            text_pattern="Welcome",
            is_exact_match=True,
            max_lines=1,
        )
        req_dict = req.to_dict()
        req_restored = TextRequirement.from_dict(req_dict)
        self.assertEqual(req_restored.requirement_id, "req-1")
        self.assertEqual(req_restored.max_lines, 1)

        assertion = TypographyAssertion(
            assertion_id=new_typography_evaluation_id(),
            check_type=TypographyCheckType.REQUIRED_TEXT_MISSING,
            required_text="Heading",
        )
        assert_dict = assertion.to_dict()
        assert_restored = TypographyAssertion.from_dict(assert_dict)
        self.assertEqual(assert_restored.assertion_id, assertion.assertion_id)
        self.assertEqual(assert_restored.check_type, TypographyCheckType.REQUIRED_TEXT_MISSING)

        assertion_res = TypographyAssertionResult(
            assertion_id=assertion.assertion_id,
            check_type=TypographyCheckType.REQUIRED_TEXT_MISSING,
            status=TypographyEvaluationStatus.PASS,
            description="Text verified",
        )
        ar_dict = assertion_res.to_dict()
        ar_restored = TypographyAssertionResult.from_dict(ar_dict)
        self.assertEqual(ar_restored.assertion_id, assertion.assertion_id)
        self.assertTrue(ar_restored.is_pass)

        eval_res = TypographyEvaluationResult(
            evaluation_id=new_typography_evaluation_id(),
            status=TypographyEvaluationStatus.PASS,
            assertion_results=[assertion_res],
        )
        eval_dict = eval_res.to_dict()
        eval_restored = TypographyEvaluationResult.from_dict(eval_dict)
        self.assertEqual(eval_restored.evaluation_id, eval_res.evaluation_id)
        self.assertTrue(eval_restored.is_pass)


if __name__ == "__main__":
    unittest.main()
