from __future__ import annotations

import unittest

from core.tester.contracts.finding import TesterDefect, TesterEvidence
from core.tester.contracts.geometry import (
    GeometryObservation,
    GeometryStatus,
    Rectangle,
    ViewportDimensions,
)
from core.tester.contracts.identifiers import (
    new_evidence_id,
    new_execution_id,
    new_geometry_id,
    new_responsive_evaluation_id,
)
from core.tester.contracts.plan import TestCase, TestPlan, TestStep
from core.tester.contracts.responsive import (
    DEFAULT_DESKTOP_PROFILE,
    DEFAULT_DESKTOP_VIEWPORT,
    DEFAULT_MOBILE_PROFILE,
    DEFAULT_MOBILE_VIEWPORT,
    DEFAULT_TABLET_PROFILE,
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
from core.tester.evaluator.responsive_evaluator import ResponsiveEvaluator
from core.tester.types import (
    ApplicableTestCategory,
    DefectSeverity,
    DefectType,
    DeviceCategory,
    ResponsiveCheckType,
    ResponsiveEvaluationStatus,
    TestPriority,
    TestSurface,
    VisibilityState,
)


class TestTesterResponsiveEvaluator(unittest.TestCase):
    """
    Comprehensive test suite for Phase 6.3 Responsive Layout Evaluation.
    Covers all 10 required test scenarios + boundary invariants & serialization:
    1. valid responsive layout
    2. mobile clipping
    3. desktop overflow
    4. overlapping elements
    5. inaccessible control
    6. intentionally different mobile layout
    7. missing viewport configuration
    8. viewport isolation
    9. evidence provenance
    10. bounded viewport count
    11. zero fixing guard
    12. contract serialization
    """

    def setUp(self) -> None:
        self.exec_id = new_execution_id()
        self.project_id = "test-project-resp"
        self.work_order_id = "two-resp-001"
        self.evaluator = ResponsiveEvaluator(
            execution_id=self.exec_id,
            project_id=self.project_id,
            work_order_id=self.work_order_id,
        )

    def _make_geom_obs(
        self,
        element_id: str,
        x: float,
        y: float,
        width: float,
        height: float,
        viewport: ViewportDimensions,
        visibility: VisibilityState = VisibilityState.VISIBLE,
        status: GeometryStatus = GeometryStatus.AVAILABLE,
        evidence_id: str | None = None,
        metadata: dict | None = None,
    ) -> GeometryObservation:
        ev_id = evidence_id or new_evidence_id()
        meta = dict(metadata or {})
        meta["evidence_ids"] = [ev_id]
        return GeometryObservation(
            geometry_id=new_geometry_id(),
            element_id=element_id,
            bounding_box=Rectangle(x=x, y=y, width=width, height=height),
            status=status,
            visibility_state=visibility,
            viewport=viewport,
            execution_id=self.exec_id,
            project_id=self.project_id,
            source_evidence_id=ev_id,
            metadata=meta,
        )

    # ---------------------------------------------------------------------------
    # Test 1: Valid Responsive Layout
    # ---------------------------------------------------------------------------

    def test_valid_responsive_layout(self) -> None:
        """Valid mobile and desktop layouts evaluate to PASS with zero defects."""
        # Desktop (1280x800): 3 columns side-by-side
        desktop_obs = [
            self._make_geom_obs("nav_header", 0, 0, 1280, 60, DEFAULT_DESKTOP_VIEWPORT),
            self._make_geom_obs("col_1", 50, 100, 350, 400, DEFAULT_DESKTOP_VIEWPORT),
            self._make_geom_obs("col_2", 450, 100, 350, 400, DEFAULT_DESKTOP_VIEWPORT),
            self._make_geom_obs("col_3", 850, 100, 350, 400, DEFAULT_DESKTOP_VIEWPORT),
            self._make_geom_obs("submit_btn", 500, 600, 200, 50, DEFAULT_DESKTOP_VIEWPORT),
        ]

        # Mobile (375x667): 3 columns stacked vertically within 375px width
        mobile_obs = [
            self._make_geom_obs("nav_header", 0, 0, 375, 50, DEFAULT_MOBILE_VIEWPORT),
            self._make_geom_obs("col_1", 20, 70, 335, 150, DEFAULT_MOBILE_VIEWPORT),
            self._make_geom_obs("col_2", 20, 240, 335, 150, DEFAULT_MOBILE_VIEWPORT),
            self._make_geom_obs("col_3", 20, 410, 335, 150, DEFAULT_MOBILE_VIEWPORT),
            self._make_geom_obs("submit_btn", 20, 580, 335, 45, DEFAULT_MOBILE_VIEWPORT),
        ]

        result = self.evaluator.evaluate_cross_viewport(
            viewport_observations={
                "desktop": desktop_obs,
                "mobile": mobile_obs,
            },
            profiles=[DEFAULT_DESKTOP_PROFILE, DEFAULT_MOBILE_PROFILE],
        )

        self.assertEqual(result.status, ResponsiveEvaluationStatus.PASS)
        self.assertTrue(result.is_pass)
        self.assertEqual(len(result.defects), 0)
        self.assertEqual(result.total_viewports_evaluated, 2)

    # ---------------------------------------------------------------------------
    # Test 2: Mobile Clipping
    # ---------------------------------------------------------------------------

    def test_mobile_clipping(self) -> None:
        """Element extending horizontally beyond mobile viewport width generates a Mobile Clipping defect."""
        # Mobile viewport width is 375. Card starts at x=20 and width=400 -> right=420 > 375
        mobile_obs = [
            self._make_geom_obs("pricing_card", 20, 100, 400, 300, DEFAULT_MOBILE_VIEWPORT),
        ]

        vp_result = self.evaluator.evaluate_viewport_layout(
            profile=DEFAULT_MOBILE_PROFILE,
            observations=mobile_obs,
        )

        self.assertEqual(vp_result.status, ResponsiveEvaluationStatus.FAIL)
        self.assertTrue(vp_result.is_fail)
        self.assertEqual(len(vp_result.defects), 1)

        defect = vp_result.defects[0]
        self.assertIn("Mobile Clipping", defect.title)
        self.assertIn("375x667", defect.title)
        self.assertEqual(defect.defect_type, DefectType.RESPONSIVE)
        self.assertEqual(defect.severity, DefectSeverity.HIGH)
        self.assertIn("pricing_card", defect.affected_components)
        self.assertTrue(defect.metadata.get("is_viewport_specific"))

    # ---------------------------------------------------------------------------
    # Test 3: Desktop Overflow
    # ---------------------------------------------------------------------------

    def test_desktop_overflow(self) -> None:
        """Element exceeding desktop width generates a Desktop Overflow defect."""
        # Desktop viewport width is 1280. Wide banner width=1450 -> right=1450 > 1280
        desktop_obs = [
            self._make_geom_obs("hero_banner_wide", 0, 0, 1450, 400, DEFAULT_DESKTOP_VIEWPORT),
        ]

        vp_result = self.evaluator.evaluate_viewport_layout(
            profile=DEFAULT_DESKTOP_PROFILE,
            observations=desktop_obs,
        )

        self.assertEqual(vp_result.status, ResponsiveEvaluationStatus.FAIL)
        self.assertEqual(len(vp_result.defects), 1)

        defect = vp_result.defects[0]
        self.assertIn("Desktop Overflow", defect.title)
        self.assertIn("1280x800", defect.title)
        self.assertEqual(defect.defect_type, DefectType.RESPONSIVE)
        self.assertIn("hero_banner_wide", defect.description)

    # ---------------------------------------------------------------------------
    # Test 4: Overlapping Elements
    # ---------------------------------------------------------------------------

    def test_overlapping_elements(self) -> None:
        """Colliding stacked elements on a responsive viewport generate an overlap defect."""
        # Two elements colliding on mobile: card_a and card_b
        mobile_obs = [
            self._make_geom_obs("card_pricing_a", 10, 100, 300, 200, DEFAULT_MOBILE_VIEWPORT),
            # card_pricing_b starts at y=150, overlapping card_pricing_a (100 to 300)
            self._make_geom_obs("card_pricing_b", 10, 150, 300, 200, DEFAULT_MOBILE_VIEWPORT),
        ]

        vp_result = self.evaluator.evaluate_viewport_layout(
            profile=DEFAULT_MOBILE_PROFILE,
            observations=mobile_obs,
        )

        self.assertEqual(vp_result.status, ResponsiveEvaluationStatus.FAIL)
        self.assertEqual(len(vp_result.defects), 1)

        defect = vp_result.defects[0]
        self.assertIn("Responsive Overlap", defect.title)
        self.assertIn("Mobile (375x667)", defect.title)
        self.assertIn("card_pricing_a", defect.affected_components)
        self.assertIn("card_pricing_b", defect.affected_components)

    # ---------------------------------------------------------------------------
    # Test 5: Inaccessible Control
    # ---------------------------------------------------------------------------

    def test_inaccessible_control(self) -> None:
        """Hidden or off-screen critical CTA on mobile generates a CRITICAL defect."""
        mobile_obs = [
            self._make_geom_obs(
                element_id="checkout_button",
                x=-200,
                y=500,
                width=150,
                height=50,
                viewport=DEFAULT_MOBILE_VIEWPORT,
                visibility=VisibilityState.HIDDEN,
            ),
        ]

        vp_result = self.evaluator.evaluate_viewport_layout(
            profile=DEFAULT_MOBILE_PROFILE,
            observations=mobile_obs,
        )

        self.assertEqual(vp_result.status, ResponsiveEvaluationStatus.FAIL)
        self.assertEqual(len(vp_result.defects), 1)

        defect = vp_result.defects[0]
        self.assertIn("Inaccessible Control", defect.title)
        self.assertEqual(defect.severity, DefectSeverity.CRITICAL)
        self.assertIn("checkout_button", defect.affected_components)

    # ---------------------------------------------------------------------------
    # Test 6: Intentionally Different Mobile Layout
    # ---------------------------------------------------------------------------

    def test_intentionally_different_mobile_layout(self) -> None:
        """
        Desktop navigation links replaced by a mobile hamburger menu toggle,
        and side-by-side columns stacking vertically, is intentional and NOT a defect.
        """
        # On Desktop: 3 nav items visible
        desktop_obs = [
            self._make_geom_obs("nav_home", 200, 10, 80, 40, DEFAULT_DESKTOP_VIEWPORT),
            self._make_geom_obs("nav_pricing", 300, 10, 80, 40, DEFAULT_DESKTOP_VIEWPORT),
            self._make_geom_obs("nav_contact", 400, 10, 80, 40, DEFAULT_DESKTOP_VIEWPORT),
            self._make_geom_obs("card_1", 50, 100, 300, 200, DEFAULT_DESKTOP_VIEWPORT),
            self._make_geom_obs("card_2", 400, 100, 300, 200, DEFAULT_DESKTOP_VIEWPORT),
        ]

        # On Mobile: Desktop nav items hidden, but hamburger button is available
        mobile_obs = [
            self._make_geom_obs("hamburger_menu_btn", 320, 10, 40, 40, DEFAULT_MOBILE_VIEWPORT),
            self._make_geom_obs("nav_home", 0, 0, 0, 0, DEFAULT_MOBILE_VIEWPORT, visibility=VisibilityState.HIDDEN),
            self._make_geom_obs("nav_pricing", 0, 0, 0, 0, DEFAULT_MOBILE_VIEWPORT, visibility=VisibilityState.HIDDEN),
            self._make_geom_obs("nav_contact", 0, 0, 0, 0, DEFAULT_MOBILE_VIEWPORT, visibility=VisibilityState.HIDDEN),
            # Cards stacked cleanly vertically
            self._make_geom_obs("card_1", 20, 80, 335, 180, DEFAULT_MOBILE_VIEWPORT),
            self._make_geom_obs("card_2", 20, 280, 335, 180, DEFAULT_MOBILE_VIEWPORT),
        ]

        result = self.evaluator.evaluate_cross_viewport(
            viewport_observations={
                "desktop": desktop_obs,
                "mobile": mobile_obs,
            },
            profiles=[DEFAULT_DESKTOP_PROFILE, DEFAULT_MOBILE_PROFILE],
        )

        self.assertEqual(result.status, ResponsiveEvaluationStatus.PASS)
        self.assertEqual(len(result.defects), 0)

    # ---------------------------------------------------------------------------
    # Test 7: Missing Viewport Configuration
    # ---------------------------------------------------------------------------

    def test_missing_viewport_configuration(self) -> None:
        """Passing empty viewport config raises ValidationError; omitting config uses bounded defaults."""
        # Empty list explicitly provided -> error
        with self.assertRaises(TesterValidationError):
            self.evaluator.resolve_viewports(configured_viewports=[])

        # None provided -> returns bounded defaults (Mobile and Desktop)
        defaults = self.evaluator.resolve_viewports(configured_viewports=None, test_plan=None)
        self.assertEqual(len(defaults), 2)
        self.assertEqual(defaults[0].category, DeviceCategory.MOBILE)
        self.assertEqual(defaults[1].category, DeviceCategory.DESKTOP)

    # ---------------------------------------------------------------------------
    # Test 8: Viewport Isolation (Lineage & Boundary Guards)
    # ---------------------------------------------------------------------------

    def test_viewport_isolation(self) -> None:
        """Evaluator rejects observations from a foreign project or execution lineage."""
        # Project ID mismatch raises TesterBoundaryViolationError
        foreign_obs = self._make_geom_obs("elem", 0, 0, 100, 100, DEFAULT_MOBILE_VIEWPORT)
        object.__setattr__(foreign_obs, "project_id", "foreign-project-xyz")

        with self.assertRaises(TesterBoundaryViolationError):
            self.evaluator.evaluate_viewport_layout(
                profile=DEFAULT_MOBILE_PROFILE,
                observations=[foreign_obs],
            )

        # Execution ID mismatch raises TesterLineageError
        foreign_exec_obs = self._make_geom_obs("elem2", 0, 0, 100, 100, DEFAULT_MOBILE_VIEWPORT)
        object.__setattr__(foreign_exec_obs, "execution_id", "texec-foreign-999")

        with self.assertRaises(TesterLineageError):
            self.evaluator.evaluate_viewport_layout(
                profile=DEFAULT_MOBILE_PROFILE,
                observations=[foreign_exec_obs],
            )

    # ---------------------------------------------------------------------------
    # Test 9: Evidence Provenance
    # ---------------------------------------------------------------------------

    def test_evidence_provenance(self) -> None:
        """Mobile defects contain mobile evidence IDs, desktop defects contain desktop evidence IDs."""
        mobile_ev_id = "tevid-mob-001"
        desktop_ev_id = "tevid-desk-002"

        mobile_obs = [
            self._make_geom_obs("clipped_card", 10, 10, 500, 100, DEFAULT_MOBILE_VIEWPORT, evidence_id=mobile_ev_id),
        ]
        desktop_obs = [
            self._make_geom_obs("overflow_banner", 0, 0, 1600, 200, DEFAULT_DESKTOP_VIEWPORT, evidence_id=desktop_ev_id),
        ]

        result = self.evaluator.evaluate_cross_viewport(
            viewport_observations={
                "mobile": mobile_obs,
                "desktop": desktop_obs,
            },
            screenshot_evidence_map={
                "mobile": [mobile_ev_id],
                "desktop": [desktop_ev_id],
            },
            profiles=[DEFAULT_MOBILE_PROFILE, DEFAULT_DESKTOP_PROFILE],
        )

        self.assertEqual(len(result.defects), 2)
        mob_defect = next(d for d in result.defects if "Mobile Clipping" in d.title)
        desk_defect = next(d for d in result.defects if "Desktop Overflow" in d.title)

        self.assertIn(mobile_ev_id, mob_defect.evidence_ids)
        self.assertNotIn(desktop_ev_id, mob_defect.evidence_ids)

        self.assertIn(desktop_ev_id, desk_defect.evidence_ids)
        self.assertNotIn(mobile_ev_id, desk_defect.evidence_ids)

    def test_intentional_horizontal_container_not_defect(self) -> None:
        """Carousels, data tables, and code snippets extending beyond viewport width are not defects."""
        mobile_obs = [
            self._make_geom_obs("product_carousel", 0, 100, 800, 250, DEFAULT_MOBILE_VIEWPORT),
            self._make_geom_obs("metrics_data_table", 0, 380, 700, 300, DEFAULT_MOBILE_VIEWPORT),
            self._make_geom_obs("sample_code_snippet", 0, 700, 600, 200, DEFAULT_MOBILE_VIEWPORT),
        ]

        vp_result = self.evaluator.evaluate_viewport_layout(
            profile=DEFAULT_MOBILE_PROFILE,
            observations=mobile_obs,
        )

        self.assertEqual(vp_result.status, ResponsiveEvaluationStatus.PASS)
        self.assertEqual(len(vp_result.defects), 0)


    # ---------------------------------------------------------------------------
    # Test 10: Bounded Viewport Count
    # ---------------------------------------------------------------------------

    def test_bounded_viewport_count(self) -> None:
        """Arbitrary 25 configured viewports are strictly clamped to MAX_VIEWPORT_COUNT (10)."""
        many_viewports = [
            ViewportProfile(
                name=f"vp_{i}",
                category=DeviceCategory.CUSTOM,
                dimensions=ViewportDimensions(width=300.0 + i * 50.0, height=600.0 + i * 20.0),
            )
            for i in range(25)
        ]

        resolved = self.evaluator.resolve_viewports(configured_viewports=many_viewports)
        self.assertEqual(len(resolved), MAX_VIEWPORT_COUNT)
        self.assertEqual(len(resolved), 10)

    # ---------------------------------------------------------------------------
    # Test 11: Zero Fixing Guard
    # ---------------------------------------------------------------------------

    def test_zero_fixing_guard(self) -> None:
        """apply_fix and auto_fix methods raise TesterBoundaryViolationError."""
        with self.assertRaises(TesterBoundaryViolationError):
            self.evaluator.apply_fix()

        with self.assertRaises(TesterBoundaryViolationError):
            self.evaluator.auto_fix()

        res = ResponsiveEvaluationResult(
            evaluation_id=new_responsive_evaluation_id(),
            status=ResponsiveEvaluationStatus.PASS,
        )

        with self.assertRaises(TesterBoundaryViolationError):
            res.apply_fix()

        with self.assertRaises(TesterBoundaryViolationError):
            res.auto_fix()

    # ---------------------------------------------------------------------------
    # Test 12: Contract Serialization
    # ---------------------------------------------------------------------------

    def test_contract_serialization(self) -> None:
        """ViewportProfile, ResponsiveCheck, ResponsiveViewportResult, and ResponsiveEvaluationResult roundtrip."""
        profile = DEFAULT_MOBILE_PROFILE
        prof_dict = profile.to_dict()
        prof_restored = ViewportProfile.from_dict(prof_dict)
        self.assertEqual(prof_restored.name, profile.name)
        self.assertEqual(prof_restored.category, profile.category)
        self.assertEqual(prof_restored.dimensions.width, profile.dimensions.width)

        check = ResponsiveCheck(
            check_id=new_responsive_evaluation_id(),
            check_type=ResponsiveCheckType.CLIPPING,
            viewport_profile=profile,
            target_element_id="card",
            expected_behavior="Card fits in viewport",
        )
        check_dict = check.to_dict()
        check_restored = ResponsiveCheck.from_dict(check_dict)
        self.assertEqual(check_restored.check_id, check.check_id)
        self.assertEqual(check_restored.check_type, ResponsiveCheckType.CLIPPING)

        vp_res = ResponsiveViewportResult(
            profile=profile,
            status=ResponsiveEvaluationStatus.PASS,
            checks_evaluated=5,
            checks_passed=5,
            checks_failed=0,
        )
        vp_dict = vp_res.to_dict()
        vp_restored = ResponsiveViewportResult.from_dict(vp_dict)
        self.assertEqual(vp_restored.checks_evaluated, 5)
        self.assertTrue(vp_restored.is_pass)

        eval_res = ResponsiveEvaluationResult(
            evaluation_id=new_responsive_evaluation_id(),
            status=ResponsiveEvaluationStatus.PASS,
            viewport_results=[vp_res],
            total_viewports_evaluated=1,
        )
        eval_dict = eval_res.to_dict()
        eval_restored = ResponsiveEvaluationResult.from_dict(eval_dict)
        self.assertEqual(eval_restored.evaluation_id, eval_res.evaluation_id)
        self.assertEqual(eval_restored.total_viewports_evaluated, 1)
        self.assertTrue(eval_restored.is_pass)


if __name__ == "__main__":
    unittest.main()
