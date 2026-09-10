from __future__ import annotations

import unittest

from core.tester.contracts.finding import TesterDefect, TesterEvidence
from core.tester.contracts.geometry import (
    CoordinateSystem,
    GeometryObservation,
    GeometryStatus,
    Point,
    Rectangle,
    SpatialRelation,
    ViewportDimensions,
    VisualGeometryObserver,
)
from core.tester.contracts.identifiers import (
    new_defect_id,
    new_evidence_id,
    new_execution_id,
    new_geometry_id,
    new_observation_id,
    new_visual_assertion_id,
)
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
from core.tester.evaluator.visual_evaluator import VisualEvaluator
from core.tester.types import (
    DefectSeverity,
    DefectType,
    EvidenceType,
    VisibilityState,
    VisualAssertionStatus,
    VisualCheckType,
)


class TestTesterVisualEvaluator(unittest.TestCase):
    """
    Comprehensive test suite for Phase 6.1 Visual Assertion & Geometry Evaluation.
    Covers all 12 required test scenarios:
    1. actual overlap
    2. intentional overlap
    3. clipping
    4. viewport overflow
    5. missing expected element
    6. valid layout
    7. insufficient geometry
    8. subjective visual difference
    9. evidence provenance
    10. defect severity
    11. project isolation
    12. no false defect generation
    """

    def setUp(self) -> None:
        self.execution_id = new_execution_id()
        self.project_id = "proj-acme-corp"
        self.work_order_id = "two-visual-601"
        self.evaluator = VisualEvaluator(
            execution_id=self.execution_id,
            project_id=self.project_id,
            work_order_id=self.work_order_id,
        )
        self.viewport = ViewportDimensions(width=1024.0, height=768.0)
        self.evidence = TesterEvidence(
            evidence_id=new_evidence_id(),
            evidence_type=EvidenceType.SCREENSHOT,
            data="screenshot-payload",
            execution_id=self.execution_id,
            metadata={"project_id": self.project_id},
        )

    # ---------------------------------------------------------------------------
    # Test 1: Actual Overlap Generates Defect
    # ---------------------------------------------------------------------------

    def test_01_actual_overlap_generates_defect(self) -> None:
        """Geometric overlap between two regular sibling elements produces a structured defect."""
        obs_button = VisualGeometryObserver.observe_element_geometry(
            execution_id=self.execution_id,
            project_id=self.project_id,
            element_id="button_submit",
            bounds=Rectangle(x=100.0, y=100.0, width=150.0, height=40.0),
            source_evidence=self.evidence,
            viewport=self.viewport,
            visibility_state=VisibilityState.VISIBLE,
        )
        obs_input = VisualGeometryObserver.observe_element_geometry(
            execution_id=self.execution_id,
            project_id=self.project_id,
            element_id="input_email",
            bounds=Rectangle(x=180.0, y=120.0, width=200.0, height=40.0),
            source_evidence=self.evidence,
            viewport=self.viewport,
            visibility_state=VisibilityState.VISIBLE,
        )

        result = self.evaluator.evaluate_overlap(obs_button, obs_input)

        self.assertEqual(result.status, VisualAssertionStatus.FAIL)
        self.assertIsNotNone(result.defect)
        self.assertIn("Visual Overlap", result.defect.title)
        self.assertIn("button_submit", result.defect.title)
        self.assertIn("input_email", result.defect.title)
        self.assertIn("overlaps 'input_email'", result.defect.description)
        self.assertGreater(result.measured_values["overlap_area"], 0.0)
        self.assertEqual(result.defect.defect_type, DefectType.VISUAL)
        self.assertIn(self.evidence.evidence_id, result.defect.evidence_ids)

    # ---------------------------------------------------------------------------
    # Test 2: Intentional Overlap (Modal / Dropdown / Tooltip) Does Not Generate Defect
    # ---------------------------------------------------------------------------

    def test_02_intentional_overlap_does_not_generate_defect(self) -> None:
        """Modals, dropdowns, and tooltips are recognized as intentional overlays and do NOT generate defects."""
        obs_page = VisualGeometryObserver.observe_element_geometry(
            execution_id=self.execution_id,
            project_id=self.project_id,
            element_id="page_container",
            bounds=Rectangle(x=0.0, y=0.0, width=1024.0, height=768.0),
            source_evidence=self.evidence,
        )
        obs_modal = VisualGeometryObserver.observe_element_geometry(
            execution_id=self.execution_id,
            project_id=self.project_id,
            element_id="modal_dialog",
            bounds=Rectangle(x=200.0, y=150.0, width=500.0, height=400.0),
            source_evidence=self.evidence,
            metadata={"role": "dialog", "is_overlay": True},
        )
        obs_tooltip = VisualGeometryObserver.observe_element_geometry(
            execution_id=self.execution_id,
            project_id=self.project_id,
            element_id="tooltip_help",
            bounds=Rectangle(x=120.0, y=80.0, width=100.0, height=30.0),
            source_evidence=self.evidence,
            metadata={"role": "tooltip"},
        )
        obs_dropdown = VisualGeometryObserver.observe_element_geometry(
            execution_id=self.execution_id,
            project_id=self.project_id,
            element_id="dropdown_menu",
            bounds=Rectangle(x=100.0, y=120.0, width=150.0, height=200.0),
            source_evidence=self.evidence,
            metadata={"role": "menu"},
        )

        # Modal over page
        res_modal = self.evaluator.evaluate_overlap(obs_page, obs_modal)
        self.assertEqual(res_modal.status, VisualAssertionStatus.PASS)
        self.assertIsNone(res_modal.defect)
        self.assertIn("Intentional overlay detected", res_modal.description)

        # Tooltip over content
        res_tooltip = self.evaluator.evaluate_overlap(obs_page, obs_tooltip)
        self.assertEqual(res_tooltip.status, VisualAssertionStatus.PASS)
        self.assertIsNone(res_tooltip.defect)

        # Dropdown menu
        res_dropdown = self.evaluator.evaluate_overlap(obs_page, obs_dropdown)
        self.assertEqual(res_dropdown.status, VisualAssertionStatus.PASS)
        self.assertIsNone(res_dropdown.defect)

        # Explicit allow_overlay parameter
        obs_a = VisualGeometryObserver.observe_element_geometry(
            execution_id=self.execution_id,
            project_id=self.project_id,
            element_id="element_a",
            bounds=Rectangle(x=50.0, y=50.0, width=100.0, height=100.0),
            source_evidence=self.evidence,
        )
        obs_b = VisualGeometryObserver.observe_element_geometry(
            execution_id=self.execution_id,
            project_id=self.project_id,
            element_id="element_b",
            bounds=Rectangle(x=80.0, y=80.0, width=100.0, height=100.0),
            source_evidence=self.evidence,
        )
        res_allowed = self.evaluator.evaluate_overlap(obs_a, obs_b, allow_overlay=True)
        self.assertEqual(res_allowed.status, VisualAssertionStatus.PASS)
        self.assertIsNone(res_allowed.defect)

    # ---------------------------------------------------------------------------
    # Test 3: Clipping Generates Defect When Evidence Exists
    # ---------------------------------------------------------------------------

    def test_03_clipping_generates_defect_when_evidence_exists(self) -> None:
        """Measurable content clipping against container bounds or OCR text produces defect."""
        # Container clipping: element extends beyond container
        obs_card = VisualGeometryObserver.observe_element_geometry(
            execution_id=self.execution_id,
            project_id=self.project_id,
            element_id="card_container",
            bounds=Rectangle(x=100.0, y=100.0, width=300.0, height=200.0),
            source_evidence=self.evidence,
        )
        obs_image = VisualGeometryObserver.observe_element_geometry(
            execution_id=self.execution_id,
            project_id=self.project_id,
            element_id="card_banner_image",
            bounds=Rectangle(x=100.0, y=100.0, width=350.0, height=250.0),  # extends 50px right, 50px bottom
            source_evidence=self.evidence,
        )

        res_container = self.evaluator.evaluate_clipping(
            element_obs=obs_image,
            container_obs=obs_card,
        )
        self.assertEqual(res_container.status, VisualAssertionStatus.FAIL)
        self.assertIsNotNone(res_container.defect)
        self.assertIn("Content Clipping", res_container.defect.title)
        self.assertEqual(res_container.measured_values["overflow_distance"], 50.0)

        # OCR text clipping: text region extends outside button bounding box
        obs_btn = VisualGeometryObserver.observe_element_geometry(
            execution_id=self.execution_id,
            project_id=self.project_id,
            element_id="btn_confirm",
            bounds=Rectangle(x=100.0, y=100.0, width=100.0, height=40.0),
            source_evidence=self.evidence,
        )
        obs_ocr = VisualGeometryObserver.observe_element_geometry(
            execution_id=self.execution_id,
            project_id=self.project_id,
            element_id="ocr_text:Confirm and Complete Order",
            bounds=Rectangle(x=100.0, y=100.0, width=140.0, height=20.0),  # text is 140px wide, button is 100px
            source_evidence=self.evidence,
            metadata={"text": "Confirm and Complete Order"},
        )

        res_ocr = self.evaluator.evaluate_clipping(
            element_obs=obs_btn,
            ocr_obs=obs_ocr,
        )
        self.assertEqual(res_ocr.status, VisualAssertionStatus.FAIL)
        self.assertIsNotNone(res_ocr.defect)
        self.assertIn("Text Clipping", res_ocr.defect.title)
        self.assertEqual(res_ocr.measured_values["overflow_distance"], 40.0)

    # ---------------------------------------------------------------------------
    # Test 4: Viewport Overflow Generates Defect
    # ---------------------------------------------------------------------------

    def test_04_viewport_overflow_generates_defect(self) -> None:
        """Content extending beyond viewport boundary produces viewport overflow defect."""
        obs_overflow = VisualGeometryObserver.observe_element_geometry(
            execution_id=self.execution_id,
            project_id=self.project_id,
            element_id="pricing_table",
            bounds=Rectangle(x=200.0, y=100.0, width=900.0, height=400.0),  # right=1100 > viewport 1024
            source_evidence=self.evidence,
            viewport=self.viewport,
        )

        res = self.evaluator.evaluate_viewport_overflow(
            element_obs=obs_overflow,
            viewport=self.viewport,
        )

        self.assertEqual(res.status, VisualAssertionStatus.FAIL)
        self.assertIsNotNone(res.defect)
        self.assertIn("Viewport Overflow", res.defect.title)
        self.assertEqual(res.measured_values["overflow_right"], 76.0)
        self.assertEqual(res.measured_values["overflow_distance"], 76.0)
        self.assertIn("extends 76.0px beyond viewport", res.defect.description)

    # ---------------------------------------------------------------------------
    # Test 5: Missing Expected Element Generates Defect (Severity scaling)
    # ---------------------------------------------------------------------------

    def test_05_missing_expected_element_generates_defect(self) -> None:
        """Expected element that is hidden or missing generates unexpected disappearance defect."""
        obs_missing = VisualGeometryObserver.observe_element_geometry(
            execution_id=self.execution_id,
            project_id=self.project_id,
            element_id="btn_checkout",
            bounds=Rectangle(x=0.0, y=0.0, width=0.0, height=0.0),
            source_evidence=self.evidence,
            visibility_state=VisibilityState.HIDDEN,
        )

        res = self.evaluator.evaluate_visibility(
            element_obs=obs_missing,
            expected_visible=True,
            is_critical_cta=True,
        )

        self.assertEqual(res.status, VisualAssertionStatus.FAIL)
        self.assertIsNotNone(res.defect)
        self.assertIn("Unexpected Disappearance", res.defect.title)
        self.assertEqual(res.defect.severity, DefectSeverity.CRITICAL)

        # Non-critical missing element has HIGH severity
        obs_secondary = VisualGeometryObserver.observe_element_geometry(
            execution_id=self.execution_id,
            project_id=self.project_id,
            element_id="footer_disclaimer",
            bounds=None,
            source_evidence=self.evidence,
            visibility_state=VisibilityState.OFFSCREEN,
        )
        res_sec = self.evaluator.evaluate_visibility(
            element_obs=obs_secondary,
            expected_visible=True,
            is_critical_cta=False,
        )
        self.assertEqual(res_sec.status, VisualAssertionStatus.FAIL)
        self.assertEqual(res_sec.defect.severity, DefectSeverity.HIGH)

    # ---------------------------------------------------------------------------
    # Test 6: Valid Layout Generates No Visual Defects
    # ---------------------------------------------------------------------------

    def test_06_valid_layout_generates_no_visual_defects(self) -> None:
        """Fully compliant, properly spaced, non-overlapping layout produces zero defects."""
        obs_header = VisualGeometryObserver.observe_element_geometry(
            execution_id=self.execution_id,
            project_id=self.project_id,
            element_id="nav_header",
            bounds=Rectangle(x=0.0, y=0.0, width=1024.0, height=60.0),
            source_evidence=self.evidence,
            viewport=self.viewport,
            visibility_state=VisibilityState.VISIBLE,
        )
        obs_main = VisualGeometryObserver.observe_element_geometry(
            execution_id=self.execution_id,
            project_id=self.project_id,
            element_id="main_content",
            bounds=Rectangle(x=50.0, y=80.0, width=924.0, height=500.0),
            source_evidence=self.evidence,
            viewport=self.viewport,
            visibility_state=VisibilityState.VISIBLE,
        )
        obs_footer = VisualGeometryObserver.observe_element_geometry(
            execution_id=self.execution_id,
            project_id=self.project_id,
            element_id="site_footer",
            bounds=Rectangle(x=0.0, y=600.0, width=1024.0, height=100.0),
            source_evidence=self.evidence,
            viewport=self.viewport,
            visibility_state=VisibilityState.VISIBLE,
        )

        res = self.evaluator.evaluate(
            geometry_observations=[obs_header, obs_main, obs_footer],
            viewport=self.viewport,
        )

        self.assertEqual(res.status, VisualAssertionStatus.PASS)
        self.assertEqual(len(res.defects), 0)
        self.assertTrue(res.is_pass)
        self.assertGreater(res.total_checks, 0)
        self.assertEqual(res.failed_checks, 0)

    # ---------------------------------------------------------------------------
    # Test 7: Insufficient Geometry Does NOT Fabricate Defects
    # ---------------------------------------------------------------------------

    def test_07_insufficient_geometry_does_not_fabricate_defects(self) -> None:
        """When geometry coordinates are unavailable, evaluator returns UNVERIFIED and fabricates NO defects."""
        obs_unavail = VisualGeometryObserver.observe_element_geometry(
            execution_id=self.execution_id,
            project_id=self.project_id,
            element_id="shadow_element",
            bounds=None,
            source_evidence=self.evidence,
        )
        self.assertEqual(obs_unavail.status, GeometryStatus.UNAVAILABLE)

        # Overlap with unavailable geometry
        obs_other = VisualGeometryObserver.observe_element_geometry(
            execution_id=self.execution_id,
            project_id=self.project_id,
            element_id="normal_element",
            bounds=Rectangle(x=10.0, y=10.0, width=50.0, height=50.0),
            source_evidence=self.evidence,
        )
        res_overlap = self.evaluator.evaluate_overlap(obs_unavail, obs_other)
        self.assertEqual(res_overlap.status, VisualAssertionStatus.UNVERIFIED)
        self.assertIsNone(res_overlap.defect)

        # Clipping with unavailable geometry
        res_clip = self.evaluator.evaluate_clipping(obs_unavail, obs_other)
        self.assertEqual(res_clip.status, VisualAssertionStatus.UNVERIFIED)
        self.assertIsNone(res_clip.defect)

        # Viewport overflow with unavailable geometry
        res_vp = self.evaluator.evaluate_viewport_overflow(obs_unavail, viewport=self.viewport)
        self.assertEqual(res_vp.status, VisualAssertionStatus.UNVERIFIED)
        self.assertIsNone(res_vp.defect)

    # ---------------------------------------------------------------------------
    # Test 8: Subjective Visual Difference Does NOT Generate Defect
    # ---------------------------------------------------------------------------

    def test_08_subjective_visual_difference_does_not_generate_defect(self) -> None:
        """Subjective opinions ('looks ugly', 'spacing feels weird', 'color is bad') are rejected."""
        subjective_statements = [
            "The button looks ugly and outdated.",
            "The spacing feels weird between cards.",
            "This color is bad for our branding.",
            "The screen doesn't look premium enough.",
            "Personal preference: make it more modern.",
            "Users might think this feels boring.",
        ]

        for stmt in subjective_statements:
            self.assertTrue(
                self.evaluator.is_subjective_claim(stmt),
                f"Expected '{stmt}' to be detected as subjective.",
            )

        # When evaluated via VisualAssertion, subjective assertions produce no defects
        obs = VisualGeometryObserver.observe_element_geometry(
            execution_id=self.execution_id,
            project_id=self.project_id,
            element_id="landing_hero",
            bounds=Rectangle(x=0.0, y=0.0, width=1024.0, height=400.0),
            source_evidence=self.evidence,
        )
        va = VisualAssertion(
            assertion_id=new_visual_assertion_id(),
            check_type=VisualCheckType.GEOMETRY_ASSERTION,
            target_element_id="landing_hero",
            expected_condition="Hero section looks ugly and doesn't look premium.",
        )

        res = self.evaluator.evaluate(
            geometry_observations=[obs],
            visual_assertions=[va],
        )

        self.assertEqual(len(res.defects), 0)
        self.assertEqual(res.status, VisualAssertionStatus.PASS)
        self.assertGreater(len(res.findings), 0)
        self.assertIn("Subjective visual assertion skipped", res.findings[0].title)

    # ---------------------------------------------------------------------------
    # Test 9: Evidence Provenance Is Preserved
    # ---------------------------------------------------------------------------

    def test_09_evidence_provenance_is_preserved(self) -> None:
        """Every defect accurately links to its source evidence IDs and includes causal reproduction steps."""
        obs_a = VisualGeometryObserver.observe_element_geometry(
            execution_id=self.execution_id,
            project_id=self.project_id,
            element_id="btn_primary",
            bounds=Rectangle(x=100.0, y=100.0, width=120.0, height=50.0),
            source_evidence=self.evidence,
        )
        obs_b = VisualGeometryObserver.observe_element_geometry(
            execution_id=self.execution_id,
            project_id=self.project_id,
            element_id="btn_secondary",
            bounds=Rectangle(x=150.0, y=120.0, width=120.0, height=50.0),
            source_evidence=self.evidence,
        )

        res = self.evaluator.evaluate_overlap(obs_a, obs_b)
        self.assertIsNotNone(res.defect)
        defect = res.defect

        self.assertIn(self.evidence.evidence_id, defect.evidence_ids)
        self.assertEqual(defect.execution_id, self.execution_id)
        self.assertGreater(len(defect.reproduction_steps), 0)
        self.assertIn("VisualEvaluator", defect.provenance.get("evaluator", ""))

    # ---------------------------------------------------------------------------
    # Test 10: Defect Severity Corresponds to Severity of Problem
    # ---------------------------------------------------------------------------

    def test_10_defect_severity_corresponds_to_severity_of_problem(self) -> None:
        """Severity properly scales: CRITICAL for unusable interface / CTA, HIGH for major clipping, LOW for minor."""
        # 1. Critical: Main CTA completely missing
        obs_cta = VisualGeometryObserver.observe_element_geometry(
            execution_id=self.execution_id,
            project_id=self.project_id,
            element_id="checkout_button",
            bounds=None,
            source_evidence=self.evidence,
            visibility_state=VisibilityState.HIDDEN,
        )
        res_cta = self.evaluator.evaluate_visibility(obs_cta, expected_visible=True)
        self.assertEqual(res_cta.defect.severity, DefectSeverity.CRITICAL)

        # 2. Critical: Interactive elements colliding with massive overlap ratio (>70%)
        obs_btn1 = VisualGeometryObserver.observe_element_geometry(
            execution_id=self.execution_id,
            project_id=self.project_id,
            element_id="button_one",
            bounds=Rectangle(x=100.0, y=100.0, width=100.0, height=40.0),
            source_evidence=self.evidence,
        )
        obs_btn2 = VisualGeometryObserver.observe_element_geometry(
            execution_id=self.execution_id,
            project_id=self.project_id,
            element_id="button_two",
            bounds=Rectangle(x=105.0, y=102.0, width=100.0, height=40.0),  # almost completely superimposed
            source_evidence=self.evidence,
        )
        res_severe_overlap = self.evaluator.evaluate_overlap(obs_btn1, obs_btn2)
        self.assertEqual(res_severe_overlap.defect.severity, DefectSeverity.CRITICAL)

        # 3. High: Content clipping with large overflow (>= 20px)
        obs_container = VisualGeometryObserver.observe_element_geometry(
            execution_id=self.execution_id,
            project_id=self.project_id,
            element_id="container",
            bounds=Rectangle(x=0.0, y=0.0, width=200.0, height=200.0),
            source_evidence=self.evidence,
        )
        obs_large_content = VisualGeometryObserver.observe_element_geometry(
            execution_id=self.execution_id,
            project_id=self.project_id,
            element_id="large_content",
            bounds=Rectangle(x=0.0, y=0.0, width=250.0, height=200.0),  # 50px overflow
            source_evidence=self.evidence,
        )
        res_clip_high = self.evaluator.evaluate_clipping(obs_large_content, obs_container)
        self.assertEqual(res_clip_high.defect.severity, DefectSeverity.HIGH)

        # 4. Low: Minor non-interactive overlap (<= 5px)
        obs_div1 = VisualGeometryObserver.observe_element_geometry(
            execution_id=self.execution_id,
            project_id=self.project_id,
            element_id="static_box_1",
            bounds=Rectangle(x=10.0, y=10.0, width=100.0, height=100.0),
            source_evidence=self.evidence,
        )
        obs_div2 = VisualGeometryObserver.observe_element_geometry(
            execution_id=self.execution_id,
            project_id=self.project_id,
            element_id="static_box_2",
            bounds=Rectangle(x=108.0, y=10.0, width=100.0, height=100.0),  # 2px overlap
            source_evidence=self.evidence,
        )
        res_overlap_low = self.evaluator.evaluate_overlap(obs_div1, obs_div2)
        self.assertEqual(res_overlap_low.defect.severity, DefectSeverity.LOW)

    # ---------------------------------------------------------------------------
    # Test 11: Project Isolation Is Respected
    # ---------------------------------------------------------------------------

    def test_11_project_isolation_is_respected(self) -> None:
        """Mismatched project_id in observations or evidence raises TesterBoundaryViolationError."""
        obs_foreign = VisualGeometryObserver.observe_element_geometry(
            execution_id=self.execution_id,
            project_id="proj-foreign-tenant",
            element_id="alien_button",
            bounds=Rectangle(x=10.0, y=10.0, width=50.0, height=50.0),
        )

        with self.assertRaises(TesterBoundaryViolationError) as ctx:
            self.evaluator.evaluate(geometry_observations=[obs_foreign])
        self.assertEqual(ctx.exception.action, "VISUAL_PROJECT_ISOLATION")

        # Foreign evidence isolation
        foreign_evidence = TesterEvidence(
            evidence_id=new_evidence_id(),
            evidence_type=EvidenceType.SCREENSHOT,
            data="foreign-data",
            execution_id=self.execution_id,
            metadata={"project_id": "proj-other"},
        )
        with self.assertRaises(TesterBoundaryViolationError) as ctx:
            self.evaluator.evaluate(evidence=[foreign_evidence])
        self.assertEqual(ctx.exception.action, "VISUAL_PROJECT_ISOLATION")

    # ---------------------------------------------------------------------------
    # Test 12: No False Defect Generation on Compliant Layouts
    # ---------------------------------------------------------------------------

    def test_12_no_false_defect_generation(self) -> None:
        """Adjacent non-overlapping elements (edge-to-edge) and valid containers produce NO false defects."""
        # Edge-to-edge buttons (distance == 0.0, but overlap == 0.0)
        obs_left = VisualGeometryObserver.observe_element_geometry(
            execution_id=self.execution_id,
            project_id=self.project_id,
            element_id="btn_prev",
            bounds=Rectangle(x=100.0, y=100.0, width=50.0, height=30.0),
            source_evidence=self.evidence,
        )
        obs_right = VisualGeometryObserver.observe_element_geometry(
            execution_id=self.execution_id,
            project_id=self.project_id,
            element_id="btn_next",
            bounds=Rectangle(x=150.0, y=100.0, width=50.0, height=30.0),  # exactly touches at x=150
            source_evidence=self.evidence,
        )

        res_overlap = self.evaluator.evaluate_overlap(obs_left, obs_right)
        self.assertEqual(res_overlap.status, VisualAssertionStatus.PASS)
        self.assertIsNone(res_overlap.defect)

        # Parent containing child exactly (width/height matches)
        obs_parent = VisualGeometryObserver.observe_element_geometry(
            execution_id=self.execution_id,
            project_id=self.project_id,
            element_id="wrapper",
            bounds=Rectangle(x=50.0, y=50.0, width=200.0, height=200.0),
            source_evidence=self.evidence,
        )
        obs_child = VisualGeometryObserver.observe_element_geometry(
            execution_id=self.execution_id,
            project_id=self.project_id,
            element_id="inner_content",
            bounds=Rectangle(x=60.0, y=60.0, width=180.0, height=180.0),
            source_evidence=self.evidence,
        )

        res_containment = self.evaluator.evaluate_container_bounds(obs_child, obs_parent)
        self.assertEqual(res_containment.status, VisualAssertionStatus.PASS)
        self.assertIsNone(res_containment.defect)

    # ---------------------------------------------------------------------------
    # Additional Invariant Checks: Zero-Fixing Guard and Serialization
    # ---------------------------------------------------------------------------

    def test_zero_fixing_guard_raises_boundary_violation(self) -> None:
        """Visual evaluation objects must NEVER perform auto fixes."""
        ar = VisualAssertionResult(
            assertion_id=new_visual_assertion_id(),
            check_type=VisualCheckType.OVERLAP,
            target_element_id="button_a",
        )
        with self.assertRaises(TesterBoundaryViolationError) as ctx:
            ar.apply_fix()
        self.assertEqual(ctx.exception.action, "VISUAL_AUTO_FIX")

        with self.assertRaises(TesterBoundaryViolationError):
            ar.auto_fix()

        vr = VisualEvaluationResult(
            execution_id=self.execution_id,
            project_id=self.project_id,
        )
        with self.assertRaises(TesterBoundaryViolationError) as ctx:
            vr.apply_fix()
        self.assertEqual(ctx.exception.action, "VISUAL_AUTO_FIX")

    def test_contracts_serialization_roundtrip(self) -> None:
        """Visual contracts must serialize and deserialize without loss."""
        va = VisualAssertion(
            assertion_id=new_visual_assertion_id(),
            check_type=VisualCheckType.OVERLAP,
            target_element_id="btn_ok",
            reference_element_id="btn_cancel",
            tolerance_px=2.0,
            rules={"min_width": 50},
        )
        va_dict = va.to_dict()
        va_restored = VisualAssertion.from_dict(va_dict)
        self.assertEqual(va.assertion_id, va_restored.assertion_id)
        self.assertEqual(va.tolerance_px, va_restored.tolerance_px)
        self.assertEqual(va.check_type, va_restored.check_type)

        defect = TesterDefect(
            defect_id=new_defect_id(),
            work_order_id=self.work_order_id,
            title="Visual Overlap",
            description="Overlap detected",
            severity=DefectSeverity.HIGH,
            evidence_ids=[self.evidence.evidence_id],
        )
        ar = VisualAssertionResult(
            assertion_id=new_visual_assertion_id(),
            check_type=VisualCheckType.OVERLAP,
            target_element_id="btn_ok",
            status=VisualAssertionStatus.FAIL,
            measured_values={"overlap_px": 12.0},
            evidence_ids=[self.evidence.evidence_id],
            defect=defect,
        )
        ar_dict = ar.to_dict()
        ar_restored = VisualAssertionResult.from_dict(ar_dict)
        self.assertEqual(ar.assertion_id, ar_restored.assertion_id)
        self.assertEqual(ar.status, ar_restored.status)
        self.assertIsNotNone(ar_restored.defect)
        self.assertEqual(ar_restored.defect.defect_id, defect.defect_id)

        vr = VisualEvaluationResult(
            execution_id=self.execution_id,
            project_id=self.project_id,
            assertion_results=[ar],
            defects=[defect],
        )
        vr_dict = vr.to_dict()
        vr_restored = VisualEvaluationResult.from_dict(vr_dict)
        self.assertEqual(vr.execution_id, vr_restored.execution_id)
        self.assertEqual(vr.total_defects, 1)
        self.assertEqual(vr.status, VisualAssertionStatus.FAIL)


if __name__ == "__main__":
    unittest.main()
