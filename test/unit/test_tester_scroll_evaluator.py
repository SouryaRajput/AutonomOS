from __future__ import annotations

import unittest

from core.tester.contracts.finding import TesterDefect, TesterEvidence
from core.tester.contracts.geometry import (
    GeometryObservation,
    GeometryStatus,
    Rectangle,
    ViewportDimensions,
    VisualGeometryObserver,
)
from core.tester.contracts.identifiers import (
    new_defect_id,
    new_evidence_id,
    new_execution_id,
    new_scroll_evaluation_id,
)
from core.tester.contracts.scroll import (
    ScrollAssertion,
    ScrollBudget,
    ScrollEvaluationResult,
    ScrollPosition,
)
from core.tester.errors import (
    TesterBoundaryViolationError,
    TesterLineageError,
    TesterValidationError,
)
from core.tester.evaluator.scroll_evaluator import ScrollEvaluator
from core.tester.types import (
    DefectSeverity,
    DefectType,
    EvidenceType,
    ScrollDirection,
    ScrollEvaluationStatus,
    VisibilityState,
)


class TestTesterScrollEvaluator(unittest.TestCase):
    """
    Comprehensive test suite for Phase 6.2 Scrolling & Overflow Evaluation.
    Covers all 11 required scenarios:
    1. successful vertical scroll
    2. failed scroll
    3. content reachable after scrolling
    4. inaccessible content
    5. intentional horizontal scroll
    6. unexpected horizontal overflow
    7. scroll limit
    8. runtime failure
    9. missing geometry/evidence
    10. provenance
    11. no infinite scrolling
    """

    def setUp(self) -> None:
        self.execution_id = new_execution_id()
        self.project_id = "proj-scroll-tenant"
        self.work_order_id = "two-scroll-602"
        self.evaluator = ScrollEvaluator(
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
    # Test 1: Successful Vertical Scroll
    # ---------------------------------------------------------------------------

    def test_01_successful_vertical_scroll(self) -> None:
        """Vertical scroll changes position as expected and produces PASS."""
        pos_init = ScrollPosition(scroll_x=0.0, scroll_y=0.0, max_scroll_y=2000.0)
        pos_post = ScrollPosition(scroll_x=0.0, scroll_y=450.0, max_scroll_y=2000.0)

        res = self.evaluator.evaluate_vertical_scroll(
            initial_position=pos_init,
            post_position=pos_post,
            expected_scrollable=True,
            source_evidence_ids=[self.evidence.evidence_id],
        )

        self.assertEqual(res.status, ScrollEvaluationStatus.PASS)
        self.assertEqual(res.delta_y, 450.0)
        self.assertIsNone(res.defect)
        self.assertIn("Vertical scroll succeeded by 450px", res.description)

    # ---------------------------------------------------------------------------
    # Test 2: Failed Scroll (Scroll Locked / Unresponsive)
    # ---------------------------------------------------------------------------

    def test_02_failed_scroll(self) -> None:
        """When vertical scroll is expected but position does not change, produces defect."""
        pos_init = ScrollPosition(scroll_x=0.0, scroll_y=0.0, max_scroll_y=1500.0)
        pos_post = ScrollPosition(scroll_x=0.0, scroll_y=0.0, max_scroll_y=1500.0)  # 0px delta

        res = self.evaluator.evaluate_vertical_scroll(
            initial_position=pos_init,
            post_position=pos_post,
            expected_scrollable=True,
            source_evidence_ids=[self.evidence.evidence_id],
        )

        self.assertEqual(res.status, ScrollEvaluationStatus.FAIL)
        self.assertIsNotNone(res.defect)
        self.assertIn("Scroll Failure", res.defect.title)
        self.assertEqual(res.defect.defect_type, DefectType.SCROLL)
        self.assertEqual(res.defect.severity, DefectSeverity.HIGH)
        self.assertIn("scroll position did not change", res.defect.description)

    # ---------------------------------------------------------------------------
    # Test 3: Content Reachable After Scrolling
    # ---------------------------------------------------------------------------

    def test_03_content_reachable_after_scrolling(self) -> None:
        """Target content that becomes visible in viewport after scrolling produces PASS."""
        pos_init = ScrollPosition(scroll_x=0.0, scroll_y=0.0)
        pos_post = ScrollPosition(scroll_x=0.0, scroll_y=800.0)

        # Target element is now inside the 768px viewport
        obs_target = VisualGeometryObserver.observe_element_geometry(
            execution_id=self.execution_id,
            project_id=self.project_id,
            element_id="pricing_section",
            bounds=Rectangle(x=100.0, y=150.0, width=800.0, height=300.0),
            source_evidence=self.evidence,
            viewport=self.viewport,
            visibility_state=VisibilityState.VISIBLE,
        )

        res = self.evaluator.evaluate_vertical_scroll(
            initial_position=pos_init,
            post_position=pos_post,
            expected_scrollable=True,
            target_obs=obs_target,
            viewport=self.viewport,
            source_evidence_ids=[self.evidence.evidence_id],
        )

        self.assertEqual(res.status, ScrollEvaluationStatus.PASS)
        self.assertTrue(res.target_reached)
        self.assertIsNone(res.defect)
        self.assertIn("Target 'pricing_section' reached", res.description)

    # ---------------------------------------------------------------------------
    # Test 4: Inaccessible Content
    # ---------------------------------------------------------------------------

    def test_04_inaccessible_content(self) -> None:
        """Target content remains offscreen/unreachable after scrolling produces defect."""
        pos_init = ScrollPosition(scroll_x=0.0, scroll_y=0.0)
        pos_post = ScrollPosition(scroll_x=0.0, scroll_y=500.0)

        # Target element is still far below viewport (top=1800 > viewport height 768)
        obs_target = VisualGeometryObserver.observe_element_geometry(
            execution_id=self.execution_id,
            project_id=self.project_id,
            element_id="btn_submit_order",
            bounds=Rectangle(x=100.0, y=1800.0, width=200.0, height=50.0),
            source_evidence=self.evidence,
            viewport=self.viewport,
            visibility_state=VisibilityState.VISIBLE,
        )

        res = self.evaluator.evaluate_vertical_scroll(
            initial_position=pos_init,
            post_position=pos_post,
            expected_scrollable=True,
            target_obs=obs_target,
            viewport=self.viewport,
            source_evidence_ids=[self.evidence.evidence_id],
        )

        self.assertEqual(res.status, ScrollEvaluationStatus.FAIL)
        self.assertFalse(res.target_reached)
        self.assertIsNotNone(res.defect)
        self.assertIn("Inaccessible Content", res.defect.title)
        self.assertEqual(res.defect.severity, DefectSeverity.CRITICAL)  # submit CTA
        self.assertIn("remains outside the visible viewport", res.defect.description)

    # ---------------------------------------------------------------------------
    # Test 5: Intentional Horizontal Scroll (Table / Carousel / Code block)
    # ---------------------------------------------------------------------------

    def test_05_intentional_horizontal_scroll(self) -> None:
        """Horizontally scrollable table or carousel does NOT generate overflow defect."""
        pos_init = ScrollPosition(scroll_x=0.0, scroll_y=0.0)
        pos_post = ScrollPosition(scroll_x=300.0, scroll_y=0.0)

        obs_carousel = VisualGeometryObserver.observe_element_geometry(
            execution_id=self.execution_id,
            project_id=self.project_id,
            element_id="product_carousel_track",
            bounds=Rectangle(x=50.0, y=200.0, width=900.0, height=250.0),
            source_evidence=self.evidence,
            metadata={"role": "carousel", "is_carousel": True},
        )

        res = self.evaluator.evaluate_horizontal_scroll(
            initial_position=pos_init,
            post_position=pos_post,
            container_obs=obs_carousel,
            is_intentional=True,
            source_evidence_ids=[self.evidence.evidence_id],
        )

        self.assertEqual(res.status, ScrollEvaluationStatus.PASS)
        self.assertFalse(res.has_unexpected_overflow)
        self.assertIsNone(res.defect)
        self.assertIn("Intentional horizontal scroll succeeded", res.description)

    # ---------------------------------------------------------------------------
    # Test 6: Unexpected Horizontal Overflow
    # ---------------------------------------------------------------------------

    def test_06_unexpected_horizontal_overflow(self) -> None:
        """Unexpected page-level horizontal overflow or scroll produces a defect."""
        # A: Via evaluate_horizontal_scroll on un-designated page container
        pos_init = ScrollPosition(scroll_x=0.0, scroll_y=0.0)
        pos_post = ScrollPosition(scroll_x=120.0, scroll_y=0.0, max_scroll_x=150.0)

        res = self.evaluator.evaluate_horizontal_scroll(
            initial_position=pos_init,
            post_position=pos_post,
            is_intentional=False,
            source_evidence_ids=[self.evidence.evidence_id],
        )

        self.assertEqual(res.status, ScrollEvaluationStatus.FAIL)
        self.assertTrue(res.has_unexpected_overflow)
        self.assertIsNotNone(res.defect)
        self.assertIn("Unexpected Horizontal Overflow", res.defect.title)

        # B: Via evaluate_unexpected_horizontal_overflow
        res_doc = self.evaluator.evaluate_unexpected_horizontal_overflow(
            page_width=1150.0,
            viewport_width=1024.0,
            has_horizontal_scrollbar=True,
            source_evidence_ids=[self.evidence.evidence_id],
        )
        self.assertEqual(res_doc.status, ScrollEvaluationStatus.FAIL)
        self.assertTrue(res_doc.has_unexpected_overflow)
        self.assertEqual(res_doc.delta_x, 126.0)

    # ---------------------------------------------------------------------------
    # Test 7: Scroll Limit (Budget Capping)
    # ---------------------------------------------------------------------------

    def test_07_scroll_limit(self) -> None:
        """Scrolling loop strictly halts when finite budget action limit is reached."""
        call_count = 0

        def mock_get_geometry() -> GeometryObservation:
            # Target never enters viewport
            return VisualGeometryObserver.observe_element_geometry(
                execution_id=self.execution_id,
                project_id=self.project_id,
                element_id="footer_link",
                bounds=Rectangle(x=100.0, y=3000.0, width=100.0, height=30.0),
            )

        scroll_y = [0.0]

        def mock_get_pos() -> ScrollPosition:
            return ScrollPosition(scroll_x=0.0, scroll_y=scroll_y[0])

        def mock_scroll(direction: str, amount: int) -> tuple[bool, None]:
            nonlocal call_count
            call_count += 1
            scroll_y[0] += amount
            return True, None

        budget = ScrollBudget(max_actions=3, max_distance_px=2000.0, step_size_px=200.0)

        res = self.evaluator.evaluate_scroll_to_element(
            target_element_id="footer_link",
            get_element_geometry_fn=mock_get_geometry,
            get_scroll_position_fn=mock_get_pos,
            scroll_fn=mock_scroll,
            budget=budget,
            source_evidence_ids=[self.evidence.evidence_id],
        )

        self.assertEqual(call_count, 3)
        self.assertEqual(res.actions_performed, 3)
        self.assertEqual(res.status, ScrollEvaluationStatus.FAIL)
        self.assertFalse(res.target_reached)
        self.assertIsNotNone(res.defect)

    # ---------------------------------------------------------------------------
    # Test 8: Runtime Failure Handled Gracefully
    # ---------------------------------------------------------------------------

    def test_08_runtime_failure(self) -> None:
        """Runtime interaction errors during scrolling return BLOCKED without unhandled crash."""
        def mock_get_geometry():
            return None

        def mock_get_pos():
            return ScrollPosition()

        def mock_failing_scroll(direction: str, amount: int) -> tuple[bool, str]:
            return False, "BrowserSession stopped unexpectedly"

        res = self.evaluator.evaluate_scroll_to_element(
            target_element_id="any_element",
            get_element_geometry_fn=mock_get_geometry,
            get_scroll_position_fn=mock_get_pos,
            scroll_fn=mock_failing_scroll,
        )

        self.assertEqual(res.status, ScrollEvaluationStatus.BLOCKED)
        self.assertIn("BrowserSession stopped unexpectedly", res.description)

    # ---------------------------------------------------------------------------
    # Test 9: Missing Geometry / Evidence Does NOT Fabricate Defects
    # ---------------------------------------------------------------------------

    def test_09_missing_geometry_evidence(self) -> None:
        """Target element with UNAVAILABLE geometry returns UNVERIFIED and does not fabricate defects."""
        pos_init = ScrollPosition(scroll_x=0.0, scroll_y=0.0)
        pos_post = ScrollPosition(scroll_x=0.0, scroll_y=300.0)

        obs_unavail = VisualGeometryObserver.observe_element_geometry(
            execution_id=self.execution_id,
            project_id=self.project_id,
            element_id="missing_section",
            bounds=None,
        )

        res = self.evaluator.evaluate_vertical_scroll(
            initial_position=pos_init,
            post_position=pos_post,
            expected_scrollable=True,
            target_obs=obs_unavail,
        )

        self.assertEqual(res.status, ScrollEvaluationStatus.UNVERIFIED)
        self.assertIsNone(res.defect)
        self.assertIn("Insufficient geometry", res.description)

    # ---------------------------------------------------------------------------
    # Test 10: Provenance Is Preserved
    # ---------------------------------------------------------------------------

    def test_10_provenance(self) -> None:
        """Scroll defects preserve non-empty evidence IDs, execution lineage, and reproduction steps."""
        pos_init = ScrollPosition(scroll_x=0.0, scroll_y=0.0)
        pos_post = ScrollPosition(scroll_x=0.0, scroll_y=0.0)  # locked

        res = self.evaluator.evaluate_vertical_scroll(
            initial_position=pos_init,
            post_position=pos_post,
            expected_scrollable=True,
            source_evidence_ids=[self.evidence.evidence_id],
        )

        defect = res.defect
        self.assertIsNotNone(defect)
        self.assertIn(self.evidence.evidence_id, defect.evidence_ids)
        self.assertEqual(defect.execution_id, self.execution_id)
        self.assertGreater(len(defect.reproduction_steps), 0)
        self.assertEqual(defect.provenance.get("evaluator"), "ScrollEvaluator")

    # ---------------------------------------------------------------------------
    # Test 11: No Infinite Scrolling (Stalled Delta Halts Loop)
    # ---------------------------------------------------------------------------

    def test_11_no_infinite_scrolling(self) -> None:
        """Stalled scroll delta (e.g. at bottom of page) halts the loop immediately."""
        call_count = 0

        def mock_get_geometry():
            return VisualGeometryObserver.observe_element_geometry(
                execution_id=self.execution_id,
                project_id=self.project_id,
                element_id="unreachable_phantom",
                bounds=Rectangle(x=10.0, y=5000.0, width=50.0, height=50.0),
            )

        def mock_get_pos():
            # Scroll position never changes (stalled at end of page)
            return ScrollPosition(scroll_x=0.0, scroll_y=1000.0, max_scroll_y=1000.0)

        def mock_scroll(direction: str, amount: int) -> tuple[bool, None]:
            nonlocal call_count
            call_count += 1
            return True, None

        budget = ScrollBudget(max_actions=20, max_distance_px=10000.0)

        res = self.evaluator.evaluate_scroll_to_element(
            target_element_id="unreachable_phantom",
            get_element_geometry_fn=mock_get_geometry,
            get_scroll_position_fn=mock_get_pos,
            scroll_fn=mock_scroll,
            budget=budget,
        )

        # Loop must stop after 1st attempt because delta was 0px
        self.assertEqual(call_count, 1)
        self.assertEqual(res.actions_performed, 1)
        self.assertEqual(res.status, ScrollEvaluationStatus.FAIL)

    # ---------------------------------------------------------------------------
    # Invariant Checks: Zero Fixing & Serialization
    # ---------------------------------------------------------------------------

    def test_zero_fixing_guard_raises_boundary_violation(self) -> None:
        """Scroll evaluation objects must NEVER attempt automatic fixes."""
        res = ScrollEvaluationResult(
            evaluation_id=new_scroll_evaluation_id(),
            status=ScrollEvaluationStatus.FAIL,
        )
        with self.assertRaises(TesterBoundaryViolationError) as ctx:
            res.apply_fix()
        self.assertEqual(ctx.exception.action, "SCROLL_AUTO_FIX")

        with self.assertRaises(TesterBoundaryViolationError):
            res.auto_fix()

    def test_contracts_serialization_roundtrip(self) -> None:
        """ScrollPosition, ScrollBudget, ScrollAssertion, and ScrollEvaluationResult roundtrip correctly."""
        sp = ScrollPosition(scroll_x=10.0, scroll_y=250.0, max_scroll_y=1000.0)
        sp_dict = sp.to_dict()
        sp_restored = ScrollPosition.from_dict(sp_dict)
        self.assertEqual(sp.scroll_x, sp_restored.scroll_x)
        self.assertEqual(sp.scroll_y, sp_restored.scroll_y)

        sb = ScrollBudget(max_actions=5, max_distance_px=2500.0, step_size_px=250.0)
        sb_dict = sb.to_dict()
        sb_restored = ScrollBudget.from_dict(sb_dict)
        self.assertEqual(sb.max_actions, sb_restored.max_actions)

        sa = ScrollAssertion(
            check_id=new_scroll_evaluation_id(),
            direction=ScrollDirection.VERTICAL,
            target_element_id="cta_footer",
            budget=sb,
        )
        sa_dict = sa.to_dict()
        sa_restored = ScrollAssertion.from_dict(sa_dict)
        self.assertEqual(sa.check_id, sa_restored.check_id)
        self.assertEqual(sa.target_element_id, sa_restored.target_element_id)

        defect = TesterDefect(
            defect_id=new_defect_id(),
            work_order_id=self.work_order_id,
            title="Scroll Failure",
            description="Locked",
            defect_type=DefectType.SCROLL,
            evidence_ids=[self.evidence.evidence_id],
        )
        sr = ScrollEvaluationResult(
            evaluation_id=new_scroll_evaluation_id(),
            status=ScrollEvaluationStatus.FAIL,
            initial_position=sp,
            final_position=sp,
            defect=defect,
            evidence_ids=[self.evidence.evidence_id],
        )
        sr_dict = sr.to_dict()
        sr_restored = ScrollEvaluationResult.from_dict(sr_dict)
        self.assertEqual(sr.evaluation_id, sr_restored.evaluation_id)
        self.assertEqual(sr.status, sr_restored.status)
        self.assertIsNotNone(sr_restored.defect)
        self.assertEqual(sr_restored.defect.defect_id, defect.defect_id)


if __name__ == "__main__":
    unittest.main()
