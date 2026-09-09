from __future__ import annotations

import math
import unittest
from unittest.mock import MagicMock

from core.tester import (
    BoundingBox,
    CoordinateSystem,
    EvidenceType,
    GeometryObservation,
    GeometryStatus,
    ObservationBinder,
    ObservationType,
    OCRResult,
    OCRStatus,
    Point,
    Rectangle,
    SpatialRelation,
    TesterBoundaryViolationError,
    TesterEvidence,
    TesterExecution,
    TesterExecutionStatus,
    TesterLineageError,
    TesterObservation,
    TesterValidationError,
    TextRegion,
    ViewportDimensions,
    VisibilityState,
    VisualGeometryObserver,
    new_evidence_id,
    new_execution_id,
    new_geometry_id,
    new_ocr_id,
    new_step_id,
    new_test_case_id,
    new_work_order_id,
    validate_geometry_id,
)


class TestTesterGeometry(unittest.TestCase):
    """
    Validation test suite for Tester V1 Phase 4.3: Visual Geometry & Layout Observation.
    Verifies all 12 mandatory scenarios:
    1. Rectangle & Point creation and geometric properties
    2. Coordinate normalization & denormalization
    3. Viewport metadata tracking
    4. OCR-derived geometry extraction
    5. Runtime-derived geometry extraction
    6. Missing geometry handling (zero coordinate fabrication)
    7. Invalid geometry rejection (negative dimensions, NaN, inf)
    8. Overlapping regions observed purely as factual measurements
    9. No false semantic interpretation or evaluative judgments
    10. Provenance & causal lineage tracking
    11. Serialization & deserialization round-trip
    12. Tenant isolation & execution boundary enforcement
    """

    def setUp(self) -> None:
        self.project_id = "proj-geometry-test"
        self.work_order_id = new_work_order_id()
        self.execution_id = new_execution_id()
        self.evidence_id = new_evidence_id()
        self.test_case_id = new_test_case_id()
        self.test_step_id = new_step_id()
        self.viewport = ViewportDimensions(width=1280.0, height=720.0, scale_factor=1.0)

    def _make_execution(self) -> TesterExecution:
        return TesterExecution(
            execution_id=self.execution_id,
            work_order_id=self.work_order_id,
            task_id="task-geom-01",
            project_id=self.project_id,
            correlation_id="corr-geom-01",
            status=TesterExecutionStatus.RUNNING,
        )

    def _make_evidence(self, **kwargs) -> TesterEvidence:
        return TesterEvidence(
            evidence_id=kwargs.pop("evidence_id", self.evidence_id),
            evidence_type=EvidenceType.SCREENSHOT,
            data="screenshot bytes placeholder",
            execution_id=kwargs.pop("execution_id", self.execution_id),
            artifact_reference=kwargs.pop("artifact_reference", "artifacts/screenshots/viewport.png"),
            metadata=kwargs.pop("metadata", {"project_id": self.project_id}),
            **kwargs,
        )

    def test_scenario_01_rectangle_and_point_creation(self) -> None:
        """Rectangle and Point calculate geometric properties correctly."""
        p1 = Point(x=10.0, y=20.0)
        p2 = Point(x=40.0, y=60.0)
        self.assertEqual(p1.x, 10.0)
        self.assertEqual(p1.y, 20.0)
        self.assertAlmostEqual(p1.distance_to(p2), 50.0)

        rect = Rectangle(x=100.0, y=50.0, width=200.0, height=80.0)
        self.assertEqual(rect.left, 100.0)
        self.assertEqual(rect.top, 50.0)
        self.assertEqual(rect.right, 300.0)
        self.assertEqual(rect.bottom, 130.0)
        self.assertEqual(rect.area, 16000.0)
        self.assertEqual(rect.center, Point(x=200.0, y=90.0))

        # Containment checks
        self.assertTrue(rect.contains_point(Point(x=150.0, y=90.0)))
        self.assertFalse(rect.contains_point(Point(x=50.0, y=90.0)))

        inner_rect = Rectangle(x=120.0, y=60.0, width=50.0, height=30.0)
        outer_rect = Rectangle(x=50.0, y=10.0, width=400.0, height=300.0)
        self.assertTrue(rect.contains_rect(inner_rect))
        self.assertFalse(rect.contains_rect(outer_rect))
        self.assertTrue(outer_rect.contains_rect(rect))

    def test_scenario_02_coordinate_normalization_and_denormalization(self) -> None:
        """Rectangle normalizes to [0.0, 1.0] and denormalizes back accurately."""
        rect = Rectangle(x=320.0, y=180.0, width=640.0, height=360.0)
        norm = rect.normalize(viewport_width=1280.0, viewport_height=720.0)

        self.assertAlmostEqual(norm.x, 0.25)
        self.assertAlmostEqual(norm.y, 0.25)
        self.assertAlmostEqual(norm.width, 0.5)
        self.assertAlmostEqual(norm.height, 0.5)

        denorm = norm.denormalize(viewport_width=1280.0, viewport_height=720.0)
        self.assertAlmostEqual(denorm.x, 320.0)
        self.assertAlmostEqual(denorm.y, 180.0)
        self.assertAlmostEqual(denorm.width, 640.0)
        self.assertAlmostEqual(denorm.height, 360.0)

        # Invalid viewport for normalization raises validation error
        with self.assertRaises(TesterValidationError):
            rect.normalize(viewport_width=0, viewport_height=720)
        with self.assertRaises(TesterValidationError):
            rect.normalize(viewport_width=1280, viewport_height=-50)

    def test_scenario_03_viewport_metadata(self) -> None:
        """ViewportDimensions models display properties and validates bounds."""
        vp = ViewportDimensions(width=1920.0, height=1080.0, scale_factor=2.0)
        self.assertEqual(vp.width, 1920.0)
        self.assertEqual(vp.height, 1080.0)
        self.assertEqual(vp.scale_factor, 2.0)

        d = vp.to_dict()
        restored = ViewportDimensions.from_dict(d)
        self.assertEqual(restored, vp)

        # Invalid viewport dimensions rejected
        with self.assertRaises(TesterValidationError):
            ViewportDimensions(width=-100, height=500)
        with self.assertRaises(TesterValidationError):
            ViewportDimensions(width=1000, height=0)
        with self.assertRaises(TesterValidationError):
            ViewportDimensions(width=1000, height=500, scale_factor=-1.0)

    def test_scenario_04_ocr_derived_geometry(self) -> None:
        """VisualGeometryObserver extracts accurate geometry from OCR results."""
        evidence = self._make_evidence()
        regions = [
            TextRegion(
                text="Login Header",
                bounding_box=BoundingBox(x=150.0, y=80.0, width=220.0, height=40.0),
                confidence=0.97,
            ),
            TextRegion(
                text="Submit Button",
                bounding_box=BoundingBox(x=150.0, y=200.0, width=120.0, height=35.0),
                confidence=0.99,
            ),
        ]
        ocr_res = OCRResult(
            ocr_id=new_ocr_id(),
            execution_id=self.execution_id,
            project_id=self.project_id,
            source_evidence_id=evidence.evidence_id,
            extracted_text="Login Header Submit Button",
            text_regions=regions,
            confidence=0.98,
        )

        obs_list = VisualGeometryObserver.observe_ocr_geometry(
            execution_id=self.execution_id,
            project_id=self.project_id,
            ocr_result=ocr_res,
            source_evidence=evidence,
            viewport=self.viewport,
        )

        self.assertEqual(len(obs_list), 2)

        # First region verification
        first = obs_list[0]
        self.assertEqual(first.status, GeometryStatus.AVAILABLE)
        self.assertIsNotNone(first.bounding_box)
        self.assertEqual(first.bounding_box.x, 150.0)
        self.assertEqual(first.bounding_box.y, 80.0)
        self.assertEqual(first.bounding_box.width, 220.0)
        self.assertEqual(first.bounding_box.height, 40.0)
        self.assertEqual(first.provenance["source"], "ocr.text_region")
        self.assertEqual(first.source_evidence_id, evidence.evidence_id)
        self.assertEqual(first.confidence, 0.97)

        # Second region verification
        second = obs_list[1]
        self.assertEqual(second.bounding_box.y, 200.0)
        self.assertEqual(second.confidence, 0.99)

    def test_scenario_05_runtime_derived_geometry(self) -> None:
        """VisualGeometryObserver extracts geometry from runtime DOM bounding rect."""
        evidence = self._make_evidence()
        bounds = {"x": 50.0, "y": 75.0, "width": 300.0, "height": 150.0}

        obs = VisualGeometryObserver.observe_element_geometry(
            execution_id=self.execution_id,
            project_id=self.project_id,
            element_id="#user-card",
            bounds=bounds,
            source_evidence=evidence,
            viewport=self.viewport,
            visibility_state=VisibilityState.VISIBLE,
            confidence=1.0,
        )

        validate_geometry_id(obs.geometry_id)
        self.assertEqual(obs.status, GeometryStatus.AVAILABLE)
        self.assertEqual(obs.element_id, "#user-card")
        self.assertEqual(obs.bounding_box.area, 45000.0)
        self.assertEqual(obs.visibility_state, VisibilityState.VISIBLE)
        self.assertEqual(obs.viewport.width, 1280.0)

        # Conversion to Phase 4.1 TesterObservation
        tester_obs = obs.to_observation()
        self.assertIsInstance(tester_obs, TesterObservation)
        self.assertEqual(tester_obs.observation_type, ObservationType.GEOMETRY)
        self.assertIn("user-card", tester_obs.description)
        self.assertEqual(tester_obs.observed_state["geometry_id"], obs.geometry_id)

    def test_scenario_06_missing_geometry_zero_fabrication(self) -> None:
        """Missing or unavailable geometry returns UNAVAILABLE status without fabricating coordinates."""
        evidence = self._make_evidence()

        # Pass None bounds (e.g. element not in DOM, or unmeasurable)
        obs = VisualGeometryObserver.observe_element_geometry(
            execution_id=self.execution_id,
            project_id=self.project_id,
            element_id="#nonexistent-modal",
            bounds=None,
            source_evidence=evidence,
            viewport=self.viewport,
        )

        self.assertEqual(obs.status, GeometryStatus.UNAVAILABLE)
        self.assertIsNone(obs.bounding_box)
        self.assertEqual(obs.confidence, 0.0)
        self.assertEqual(obs.visibility_state, VisibilityState.UNKNOWN)
        self.assertIn("not fabricated", obs.provenance["missing_reason"])

        # Conversion to TesterObservation reflects UNAVAILABLE factual state
        tester_obs = obs.to_observation()
        self.assertEqual(tester_obs.observed_state["status"], "UNAVAILABLE")
        self.assertIsNone(tester_obs.observed_state["bounding_box"])

    def test_scenario_07_invalid_geometry_rejection(self) -> None:
        """Invalid coordinates (negative dimensions, NaN, Inf, non-numbers) are rejected."""
        # Negative dimensions
        with self.assertRaises(TesterValidationError):
            Rectangle(x=10, y=10, width=-50, height=20)
        with self.assertRaises(TesterValidationError):
            Rectangle(x=10, y=10, width=50, height=-20)

        # NaN and Infinite coordinates
        with self.assertRaises(TesterValidationError):
            Rectangle(x=float("nan"), y=10, width=50, height=20)
        with self.assertRaises(TesterValidationError):
            Rectangle(x=10, y=float("inf"), width=50, height=20)
        with self.assertRaises(TesterValidationError):
            Point(x=10, y=float("nan"))

        # Non-numeric types
        with self.assertRaises(TesterValidationError):
            Rectangle(x="10", y=10, width=50, height=20)  # type: ignore
        with self.assertRaises(TesterValidationError):
            Point(x=True, y=10)  # bool rejected

    def test_scenario_08_overlapping_regions_as_observation_only(self) -> None:
        """Overlapping regions are measured factually without inferring semantic correctness or defects."""
        rect_button = Rectangle(x=100.0, y=100.0, width=150.0, height=50.0)
        rect_text = Rectangle(x=180.0, y=120.0, width=120.0, height=40.0)

        # Compute spatial relationship
        relation = VisualGeometryObserver.measure_spatial_relation(
            rect_a=rect_button,
            rect_b=rect_text,
            element_a="#btn-submit",
            element_b="#txt-label",
        )

        self.assertTrue(relation.overlaps)
        self.assertIsNotNone(relation.overlap_rect)
        self.assertEqual(relation.overlap_rect.x, 180.0)
        self.assertEqual(relation.overlap_rect.y, 120.0)
        self.assertEqual(relation.overlap_rect.width, 70.0)
        self.assertEqual(relation.overlap_rect.height, 30.0)
        self.assertEqual(relation.overlap_area, 2100.0)
        self.assertEqual(relation.distance, 0.0)

        # Description records spatial facts only
        desc = relation.description()
        self.assertIn("overlaps region '#txt-label' by 2100.0 area units", desc)

        # Container overflow measurement
        container = Rectangle(x=0.0, y=0.0, width=200.0, height=100.0)
        overflowing_text = Rectangle(x=50.0, y=10.0, width=180.0, height=30.0)  # right is 230, overflows by 30
        rel_overflow = VisualGeometryObserver.measure_spatial_relation(
            rect_a=overflowing_text,
            rect_b=container,
            element_a="#overflow-text",
            element_b="#card-container",
            is_b_container=True,
        )
        self.assertTrue(rel_overflow.a_overflows_b)
        self.assertEqual(rel_overflow.overflow_distance, 30.0)
        self.assertIn("extends 30.0 units beyond bounds", rel_overflow.description())

    def test_scenario_09_no_false_semantic_interpretation_or_evaluations(self) -> None:
        """Geometry layer strictly forbids defects, findings, UX scoring, or evaluative keys."""
        obs = GeometryObservation(
            geometry_id=new_geometry_id(),
            execution_id=self.execution_id,
            project_id=self.project_id,
            element_id="#overlapping-header",
            bounding_box=Rectangle(x=0, y=0, width=100, height=50),
        )

        # Evaluative APIs rejected
        with self.assertRaises(TesterBoundaryViolationError) as ctx_def:
            obs.to_defect()
        self.assertIn("cannot directly produce a TesterDefect", str(ctx_def.exception))

        with self.assertRaises(TesterBoundaryViolationError) as ctx_find:
            obs.to_finding()
        self.assertIn("cannot directly produce a TesterFinding", str(ctx_find.exception))

        with self.assertRaises(TesterBoundaryViolationError) as ctx_verd:
            obs.assert_verdict()
        self.assertIn("cannot assert test pass/fail verdicts", str(ctx_verd.exception))

        # Evaluative keys in metadata, measurements, or provenance are rejected
        for bad_key in ("is_defect", "verdict", "ux_score", "aesthetic_score", "defect_severity"):
            with self.subTest(bad_key=bad_key):
                with self.assertRaises(TesterBoundaryViolationError):
                    GeometryObservation(
                        geometry_id=new_geometry_id(),
                        execution_id=self.execution_id,
                        project_id=self.project_id,
                        measurements={bad_key: "bad layout"},
                    )

    def test_scenario_10_provenance_and_causal_lineage(self) -> None:
        """Geometry observation preserves complete causal lineage back to evidence and execution."""
        evidence = self._make_evidence()
        execution = self._make_execution()

        obs = VisualGeometryObserver.observe_element_geometry(
            execution_id=self.execution_id,
            project_id=self.project_id,
            element_id="input[name='email']",
            bounds={"x": 120, "y": 240, "width": 250, "height": 36},
            source_evidence=evidence,
            test_case_id=self.test_case_id,
            test_step_id=self.test_step_id,
            source="browser.dom.get_bounding_client_rect",
        )

        self.assertEqual(obs.execution_id, self.execution_id)
        self.assertEqual(obs.project_id, self.project_id)
        self.assertEqual(obs.test_case_id, self.test_case_id)
        self.assertEqual(obs.test_step_id, self.test_step_id)
        self.assertEqual(obs.source_evidence_id, evidence.evidence_id)
        self.assertEqual(obs.provenance["source"], "browser.dom.get_bounding_client_rect")

        # Binding to execution
        tester_obs = ObservationBinder.bind_geometry_observation(obs)
        execution.record_observation(tester_obs)

        self.assertEqual(len(execution.observations), 1)
        retrieved = execution.observations[0]
        self.assertEqual(retrieved.observation_type, ObservationType.GEOMETRY)
        self.assertEqual(retrieved.evidence_ids, [evidence.evidence_id])
        self.assertEqual(retrieved.provenance["geometry_id"], obs.geometry_id)

    def test_scenario_11_serialization_round_trip(self) -> None:
        """Point, Rectangle, ViewportDimensions, SpatialRelation, GeometryObservation round-trip cleanly."""
        pt = Point(x=45.5, y=90.25)
        self.assertEqual(Point.from_dict(pt.to_dict()), pt)

        rect = Rectangle(x=10.0, y=20.0, width=300.0, height=150.0)
        self.assertEqual(Rectangle.from_dict(rect.to_dict()), rect)

        vp = ViewportDimensions(width=1440.0, height=900.0, scale_factor=1.5)
        self.assertEqual(ViewportDimensions.from_dict(vp.to_dict()), vp)

        sr = SpatialRelation(
            element_a="alpha",
            element_b="beta",
            distance=15.5,
            overlaps=True,
            overlap_area=42.0,
            overlap_rect=Rectangle(x=10, y=10, width=6, height=7),
            a_contains_b=False,
            b_contains_a=False,
            a_overflows_b=True,
            overflow_distance=5.0,
        )
        self.assertEqual(SpatialRelation.from_dict(sr.to_dict()), sr)

        geom_obs = GeometryObservation(
            geometry_id=new_geometry_id(),
            execution_id=self.execution_id,
            project_id=self.project_id,
            source_evidence_id=self.evidence_id,
            test_case_id=self.test_case_id,
            element_id="button#save",
            bounding_box=rect,
            viewport=vp,
            coordinate_system=CoordinateSystem.VIEWPORT,
            visibility_state=VisibilityState.VISIBLE,
            status=GeometryStatus.AVAILABLE,
            confidence=0.99,
            spatial_relation=sr,
            measurements={"area": rect.area},
            provenance={"engine": "geometry_analyzer"},
        )

        d = geom_obs.to_dict()
        restored = GeometryObservation.from_dict(d)

        self.assertEqual(restored.geometry_id, geom_obs.geometry_id)
        self.assertEqual(restored.execution_id, geom_obs.execution_id)
        self.assertEqual(restored.project_id, geom_obs.project_id)
        self.assertEqual(restored.element_id, geom_obs.element_id)
        self.assertEqual(restored.bounding_box, geom_obs.bounding_box)
        self.assertEqual(restored.viewport, geom_obs.viewport)
        self.assertEqual(restored.coordinate_system, CoordinateSystem.VIEWPORT)
        self.assertEqual(restored.visibility_state, VisibilityState.VISIBLE)
        self.assertEqual(restored.spatial_relation, sr)
        self.assertEqual(restored.confidence, 0.99)

    def test_scenario_12_tenant_isolation_and_execution_boundaries(self) -> None:
        """Geometry observations strictly reject execution mismatches and cross-tenant project IDs."""
        # 1. Reject mismatched execution_id on evidence
        foreign_evidence = self._make_evidence(execution_id=new_execution_id())
        with self.assertRaises(TesterLineageError):
            VisualGeometryObserver.observe_element_geometry(
                execution_id=self.execution_id,
                project_id=self.project_id,
                element_id="#hero",
                bounds={"x": 0, "y": 0, "width": 100, "height": 100},
                source_evidence=foreign_evidence,
            )

        # 2. Reject cross-tenant project_id on evidence
        cross_tenant_evidence = self._make_evidence(
            metadata={"project_id": "different-tenant-project"}
        )
        with self.assertRaises(TesterBoundaryViolationError) as ctx_tenant:
            VisualGeometryObserver.observe_element_geometry(
                execution_id=self.execution_id,
                project_id=self.project_id,
                element_id="#hero",
                bounds={"x": 0, "y": 0, "width": 100, "height": 100},
                source_evidence=cross_tenant_evidence,
            )
        self.assertIn("project_id", str(ctx_tenant.exception))

        # 3. Empty project_id rejected
        with self.assertRaises(TesterLineageError):
            GeometryObservation(
                geometry_id=new_geometry_id(),
                execution_id=self.execution_id,
                project_id="",
            )


if __name__ == "__main__":
    unittest.main()
