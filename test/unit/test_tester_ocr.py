from __future__ import annotations

import unittest
from unittest.mock import MagicMock

from core.events.types import EventType
from core.tester import (
    BoundingBox,
    EvidenceType,
    MockOCRProvider,
    ObservationBinder,
    ObservationType,
    OCRProvider,
    OCRResult,
    OCRStatus,
    TesterBoundaryViolationError,
    TesterEvidence,
    TesterExecution,
    TesterExecutionStatus,
    TesterLineageError,
    TesterObservation,
    TesterValidationError,
    TextRegion,
    new_evidence_id,
    new_execution_id,
    new_ocr_id,
    new_step_id,
    new_test_case_id,
    new_work_order_id,
    validate_ocr_id,
)


class TestTesterOCR(unittest.TestCase):
    """
    Validation test suite for Tester V1 Phase 4.2: OCR Engine.
    Verifies all 14 mandatory scenarios:
    1. Mock provider extracts text and regions deterministically
    2. Bounding box coordinates and region confidence are preserved
    3. OCRResult binds correctly to a TEXT observation
    4. Observation retains source_evidence_id and ocr_id lineage
    5. Low-confidence OCR preserves confidence score
    6. OCR failure produces clear failed result without crashing
    7. OCR unavailability produces UNAVAILABLE status
    8. OCR timeout produces TIMEOUT status
    9. Unsupported evidence type produces UNSUPPORTED status
    10. Unauthorized evidence is rejected
    11. OCR does NOT assert pass/fail verdicts or defects
    12. Multiple text regions are captured and structured correctly
    13. Bounded text limits are enforced (e.g., max regions/characters)
    14. Complete lineage from screenshot -> OCR -> observation is traceable
    15. Serialization and deserialization round-trip
    """

    def setUp(self) -> None:
        self.project_id = "proj-ocr-test"
        self.work_order_id = new_work_order_id()
        self.execution_id = new_execution_id()
        self.evidence_id = new_evidence_id()
        self.test_case_id = new_test_case_id()
        self.test_step_id = new_step_id()

    def _make_execution(self) -> TesterExecution:
        return TesterExecution(
            execution_id=self.execution_id,
            work_order_id=self.work_order_id,
            task_id="task-ocr-01",
            project_id=self.project_id,
            correlation_id="corr-ocr-01",
            status=TesterExecutionStatus.RUNNING,
        )

    def _make_screenshot_evidence(self, execution_id: Optional[str] = None, **kwargs) -> TesterEvidence:
        return TesterEvidence(
            evidence_id=kwargs.pop("evidence_id", self.evidence_id),
            evidence_type=EvidenceType.SCREENSHOT,
            data="screenshot bytes placeholder",
            execution_id=execution_id or self.execution_id,
            artifact_reference=kwargs.pop("artifact_reference", "artifacts/screenshots/shot_01.png"),
            metadata=kwargs.pop("metadata", {"project_id": self.project_id}),
            **kwargs,
        )

    def test_scenario_01_mock_provider_deterministic_extraction(self) -> None:
        """MockOCRProvider extracts text and regions deterministically."""
        provider = MockOCRProvider()
        evidence = self._make_screenshot_evidence()
        execution = self._make_execution()

        region = TextRegion(
            text="Welcome to Dashboard",
            bounding_box=BoundingBox(x=10.0, y=20.0, width=200.0, height=40.0),
            confidence=0.98,
        )
        provider.set_evidence_result(
            evidence_id=evidence.evidence_id,
            text="Welcome to Dashboard",
            regions=[region],
            confidence=0.98,
        )

        res1 = provider.extract_text(evidence, execution)
        res2 = provider.extract_text(evidence, execution)

        self.assertEqual(res1.status, OCRStatus.SUCCESS)
        self.assertEqual(res2.status, OCRStatus.SUCCESS)
        self.assertEqual(res1.extracted_text, "Welcome to Dashboard")
        self.assertEqual(res2.extracted_text, "Welcome to Dashboard")
        self.assertEqual(len(res1.text_regions), 1)
        self.assertEqual(res1.text_regions[0].text, "Welcome to Dashboard")
        self.assertAlmostEqual(res1.confidence, 0.98)

    def test_scenario_02_bounding_box_coordinates_and_confidence_preserved(self) -> None:
        """Bounding box coordinates and region confidence are faithfully preserved."""
        box = BoundingBox(x=105.5, y=250.25, width=320.0, height=45.75)
        self.assertEqual(box.x, 105.5)
        self.assertEqual(box.y, 250.25)
        self.assertEqual(box.width, 320.0)
        self.assertEqual(box.height, 45.75)

        region = TextRegion(
            text="Checkout Order Now",
            bounding_box=box,
            confidence=0.875,
        )
        self.assertEqual(region.text, "Checkout Order Now")
        self.assertEqual(region.bounding_box.x, 105.5)
        self.assertEqual(region.confidence, 0.875)

        # Invalid bounds validation
        with self.assertRaises(TesterValidationError):
            BoundingBox(x=0, y=0, width=-10, height=20)
        with self.assertRaises(TesterValidationError):
            BoundingBox(x=0, y=0, width=10, height=-5)
        with self.assertRaises(TesterValidationError):
            TextRegion(text="Invalid", bounding_box=box, confidence=1.5)

    def test_scenario_03_ocr_result_binds_to_text_observation(self) -> None:
        """OCRResult binds cleanly to an immutable Phase 4.1 TEXT observation."""
        evidence = self._make_screenshot_evidence()
        execution = self._make_execution()
        provider = MockOCRProvider()

        region = TextRegion(
            text="Invoice #10492",
            bounding_box=BoundingBox(x=50.0, y=50.0, width=150.0, height=25.0),
            confidence=0.96,
        )
        provider.set_evidence_result(
            evidence_id=evidence.evidence_id,
            text="Invoice #10492",
            regions=[region],
            confidence=0.96,
        )

        ocr_res = provider.extract_text(evidence, execution)
        obs = ocr_res.to_observation(
            test_case_id=self.test_case_id,
            test_step_id=self.test_step_id,
        )

        self.assertIsInstance(obs, TesterObservation)
        self.assertEqual(obs.observation_type, ObservationType.TEXT)
        self.assertEqual(obs.test_case_id, self.test_case_id)
        self.assertEqual(obs.test_step_id, self.test_step_id)
        self.assertEqual(obs.observed_state["extracted_text"], "Invoice #10492")
        self.assertEqual(obs.observed_state["ocr_id"], ocr_res.ocr_id)
        self.assertEqual(obs.confidence, 0.96)
        self.assertTrue(obs.is_uncertain)

        # Also via ObservationBinder.bind_ocr_result
        obs2 = ObservationBinder.bind_ocr_result(
            ocr_res,
            test_case_id=self.test_case_id,
            test_step_id=self.test_step_id,
        )
        self.assertEqual(obs2.observation_type, ObservationType.TEXT)
        self.assertEqual(obs2.observed_state["extracted_text"], "Invoice #10492")
        self.assertEqual(obs2.confidence, 0.96)
        self.assertTrue(obs2.is_uncertain)

    def test_scenario_04_observation_retains_evidence_and_ocr_lineage(self) -> None:
        """Observation derived from OCR retains source_evidence_id and ocr_id lineage."""
        evidence = self._make_screenshot_evidence()
        execution = self._make_execution()
        provider = MockOCRProvider(default_text="Settings Page")

        ocr_res = provider.extract_text(evidence, execution)
        obs = ocr_res.to_observation()

        self.assertEqual(obs.evidence_ids, [evidence.evidence_id])
        self.assertEqual(obs.provenance["source_evidence_id"], evidence.evidence_id)
        self.assertEqual(obs.provenance["ocr_id"], ocr_res.ocr_id)
        self.assertEqual(obs.observed_state["source_evidence_id"], evidence.evidence_id)
        self.assertEqual(obs.observed_state["ocr_id"], ocr_res.ocr_id)
        self.assertEqual(ocr_res.observation_id, obs.observation_id)

    def test_scenario_05_low_confidence_ocr_preserves_score(self) -> None:
        """Low-confidence OCR preserves score without inflating or pretending it is authoritative truth."""
        evidence = self._make_screenshot_evidence()
        execution = self._make_execution()
        provider = MockOCRProvider()

        region = TextRegion(
            text="blurred_watermark_89",
            bounding_box=BoundingBox(x=0, y=0, width=50, height=20),
            confidence=0.32,
        )
        provider.set_evidence_result(
            evidence_id=evidence.evidence_id,
            text="blurred_watermark_89",
            regions=[region],
            confidence=0.32,
        )

        ocr_res = provider.extract_text(evidence, execution)
        self.assertAlmostEqual(ocr_res.confidence, 0.32)

        obs = ocr_res.to_observation()
        self.assertAlmostEqual(obs.confidence, 0.32)
        self.assertTrue(obs.is_uncertain)

    def test_scenario_06_ocr_failure_produces_failed_status_without_crashing(self) -> None:
        """OCR failure produces clear FAILED status without crashing the Tester or marking test failure."""
        evidence = self._make_screenshot_evidence()
        execution = self._make_execution()
        provider = MockOCRProvider(
            simulate_failure=True,
            failure_error_message="Image corrupted or invalid pixel buffer format.",
        )

        # Must NOT raise exception or crash
        ocr_res = provider.extract_text(evidence, execution)

        self.assertEqual(ocr_res.status, OCRStatus.FAILED)
        self.assertIn("corrupted", ocr_res.error_message)
        self.assertEqual(ocr_res.extracted_text, "")
        self.assertEqual(ocr_res.text_regions, [])
        self.assertEqual(ocr_res.confidence, 0.0)

        # Execution remains in RUNNING status (OCR failure != test failure)
        self.assertEqual(execution.status, TesterExecutionStatus.RUNNING)

    def test_scenario_07_ocr_unavailability_produces_unavailable_status(self) -> None:
        """OCR unavailability produces UNAVAILABLE status gracefully."""
        evidence = self._make_screenshot_evidence()
        execution = self._make_execution()
        provider = MockOCRProvider()
        provider.set_unavailable(True)

        ocr_res = provider.extract_text(evidence, execution)

        self.assertEqual(ocr_res.status, OCRStatus.UNAVAILABLE)
        self.assertIn("unavailable", ocr_res.error_message.lower())
        self.assertEqual(ocr_res.confidence, 0.0)

    def test_scenario_08_ocr_timeout_produces_timeout_status(self) -> None:
        """OCR timeout produces TIMEOUT status gracefully."""
        evidence = self._make_screenshot_evidence()
        execution = self._make_execution()
        provider = MockOCRProvider()
        provider.set_timeout(True)

        ocr_res = provider.extract_text(evidence, execution)

        self.assertEqual(ocr_res.status, OCRStatus.TIMEOUT)
        self.assertIn("timed out", ocr_res.error_message.lower())
        self.assertEqual(ocr_res.confidence, 0.0)

    def test_scenario_09_unsupported_evidence_type_produces_unsupported_status(self) -> None:
        """Non-screenshot / non-visual evidence produces UNSUPPORTED status."""
        non_image_evidence = TesterEvidence(
            evidence_id=new_evidence_id(),
            evidence_type=EvidenceType.LOG,
            data="Server logs 2026-09-09",
            execution_id=self.execution_id,
            metadata={"project_id": self.project_id},
        )
        execution = self._make_execution()
        provider = MockOCRProvider()

        ocr_res = provider.extract_text(non_image_evidence, execution)

        self.assertEqual(ocr_res.status, OCRStatus.UNSUPPORTED)
        self.assertIn("not supported", ocr_res.error_message.lower())
        self.assertEqual(ocr_res.confidence, 0.0)

    def test_scenario_10_unauthorized_evidence_is_rejected(self) -> None:
        """Unauthorized evidence (lineage mismatch, cross-tenant project, directory traversal) is rejected."""
        provider = MockOCRProvider()
        execution = self._make_execution()

        # 1. Execution ID lineage mismatch
        foreign_evidence = self._make_screenshot_evidence(execution_id=new_execution_id())
        with self.assertRaises(TesterLineageError):
            provider.extract_text(foreign_evidence, execution)

        # 2. Project ID isolation mismatch
        cross_tenant_evidence = self._make_screenshot_evidence(
            metadata={"project_id": "other-tenant-project"}
        )
        with self.assertRaises(TesterBoundaryViolationError) as ctx_tenant:
            provider.extract_text(cross_tenant_evidence, execution)
        self.assertIn("project", str(ctx_tenant.exception).lower())

        # 3. Directory traversal / unauthorized arbitrary file access
        traversal_evidence = self._make_screenshot_evidence(
            artifact_reference="../../etc/shadow"
        )
        with self.assertRaises(TesterBoundaryViolationError) as ctx_traversal:
            provider.extract_text(traversal_evidence, execution)
        self.assertIn("unauthorized filesystem paths", str(ctx_traversal.exception))

        # 4. Explicitly unauthorized evidence
        unauth_evidence = self._make_screenshot_evidence(
            metadata={"project_id": self.project_id, "unauthorized": True}
        )
        with self.assertRaises(TesterBoundaryViolationError):
            provider.extract_text(unauth_evidence, execution)

    def test_scenario_11_ocr_does_not_assert_pass_fail_verdicts_or_defects(self) -> None:
        """OCR result strictly rejects asserting pass/fail verdicts or producing defects."""
        evidence = self._make_screenshot_evidence()
        execution = self._make_execution()
        provider = MockOCRProvider(default_text="Error: Connection refused")

        ocr_res = provider.extract_text(evidence, execution)

        # Boundary violation when trying to convert OCR directly into defects or findings
        with self.assertRaises(TesterBoundaryViolationError) as ctx_def:
            ocr_res.to_defect()
        self.assertIn("cannot directly produce a TesterDefect", str(ctx_def.exception))

        with self.assertRaises(TesterBoundaryViolationError) as ctx_find:
            ocr_res.to_finding()
        self.assertIn("cannot directly produce a TesterFinding", str(ctx_find.exception))

        with self.assertRaises(TesterBoundaryViolationError) as ctx_verd:
            ocr_res.assert_verdict()
        self.assertIn("cannot assert pass/fail verdicts", str(ctx_verd.exception))

        # Boundary violation if evaluative keys are injected into processing_metadata
        with self.assertRaises(TesterBoundaryViolationError):
            OCRResult(
                ocr_id=new_ocr_id(),
                execution_id=self.execution_id,
                project_id=self.project_id,
                source_evidence_id=self.evidence_id,
                processing_metadata={"is_defect": True},
            )

    def test_scenario_12_multiple_text_regions_structured_correctly(self) -> None:
        """Multiple recognized text regions are captured, structured, and ordered correctly."""
        evidence = self._make_screenshot_evidence()
        execution = self._make_execution()
        provider = MockOCRProvider()

        regions = [
            TextRegion(text="Header Title", bounding_box=BoundingBox(x=0, y=0, width=500, height=50), confidence=0.99),
            TextRegion(text="Subheading", bounding_box=BoundingBox(x=0, y=60, width=400, height=30), confidence=0.95),
            TextRegion(text="Paragraph body text", bounding_box=BoundingBox(x=0, y=100, width=600, height=200), confidence=0.91),
            TextRegion(text="Submit", bounding_box=BoundingBox(x=100, y=320, width=120, height=40), confidence=0.97),
        ]
        provider.set_evidence_result(
            evidence_id=evidence.evidence_id,
            text="Header Title Subheading Paragraph body text Submit",
            regions=regions,
            confidence=0.95,
        )

        ocr_res = provider.extract_text(evidence, execution)

        self.assertEqual(len(ocr_res.text_regions), 4)
        self.assertEqual(ocr_res.text_regions[0].text, "Header Title")
        self.assertEqual(ocr_res.text_regions[3].text, "Submit")
        self.assertEqual(ocr_res.processing_metadata["region_count"], 4)

    def test_scenario_13_bounded_resource_limits_enforced(self) -> None:
        """Bounded limits (character count and region limits) are enforced to prevent memory exhaustion."""
        evidence = self._make_screenshot_evidence()
        execution = self._make_execution()
        provider = MockOCRProvider(
            max_text_length=50,
            max_regions=2,
        )

        regions = [
            TextRegion(text=f"Region {i}", bounding_box=BoundingBox(x=0, y=i*20, width=100, height=18), confidence=0.9)
            for i in range(10)
        ]
        provider.set_evidence_result(
            evidence_id=evidence.evidence_id,
            text="This is a very long extracted text string that exceeds fifty characters by a lot.",
            regions=regions,
            confidence=0.9,
        )

        ocr_res = provider.extract_text(evidence, execution)

        # Text truncated to max 50 chars
        self.assertLessEqual(len(ocr_res.extracted_text), 50)
        self.assertTrue(ocr_res.processing_metadata["text_truncated"])

        # Regions truncated to max 2 regions
        self.assertEqual(len(ocr_res.text_regions), 2)
        self.assertTrue(ocr_res.processing_metadata["regions_truncated"])

    def test_scenario_14_complete_screenshot_ocr_observation_lineage(self) -> None:
        """Trace complete causal chain: Screenshot Evidence -> OCR Result -> Factual Observation."""
        execution = self._make_execution()
        provider = MockOCRProvider()

        # Step 1: Screenshot evidence captured
        evidence = self._make_screenshot_evidence(
            artifact_reference="artifacts/screenshots/checkout_view.png",
        )
        execution.evidence.append(evidence)

        # Step 2: OCR extraction executed
        region = TextRegion(
            text="Total: $49.99",
            bounding_box=BoundingBox(x=200, y=400, width=150, height=35),
            confidence=0.94,
        )
        provider.set_evidence_result(
            evidence_id=evidence.evidence_id,
            text="Total: $49.99",
            regions=[region],
            confidence=0.94,
        )
        ocr_res = provider.extract_text(evidence, execution)
        validate_ocr_id(ocr_res.ocr_id)
        self.assertEqual(ocr_res.source_evidence_id, evidence.evidence_id)

        # Step 3: OCR bound to Observation
        obs = ocr_res.to_observation(
            test_case_id=self.test_case_id,
            test_step_id=self.test_step_id,
        )
        execution.record_observation(obs)

        # Step 4: Verification of end-to-end lineage
        self.assertEqual(len(execution.observations), 1)
        retrieved_obs = execution.observations[0]
        self.assertEqual(retrieved_obs.observation_type, ObservationType.TEXT)
        self.assertEqual(retrieved_obs.evidence_ids, [evidence.evidence_id])
        self.assertEqual(retrieved_obs.provenance["ocr_id"], ocr_res.ocr_id)
        self.assertEqual(retrieved_obs.provenance["source_evidence_id"], evidence.evidence_id)
        self.assertEqual(ocr_res.observation_id, retrieved_obs.observation_id)

    def test_scenario_15_serialization_round_trip(self) -> None:
        """BoundingBox, TextRegion, and OCRResult serialize and deserialize without loss."""
        box = BoundingBox(x=12.0, y=34.0, width=56.0, height=78.0)
        box_dict = box.to_dict()
        restored_box = BoundingBox.from_dict(box_dict)
        self.assertEqual(restored_box, box)

        region = TextRegion(text="Test Region", bounding_box=box, confidence=0.88)
        region_dict = region.to_dict()
        restored_region = TextRegion.from_dict(region_dict)
        self.assertEqual(restored_region, region)

        ocr_res = OCRResult(
            ocr_id=new_ocr_id(),
            execution_id=self.execution_id,
            project_id=self.project_id,
            source_evidence_id=self.evidence_id,
            status=OCRStatus.SUCCESS,
            extracted_text="Order Confirmation",
            text_regions=[region],
            confidence=0.88,
            processing_metadata={"engine": "mock_ocr", "version": "1.0"},
            error_message=None,
        )
        res_dict = ocr_res.to_dict()
        restored_res = OCRResult.from_dict(res_dict)

        self.assertEqual(restored_res.ocr_id, ocr_res.ocr_id)
        self.assertEqual(restored_res.execution_id, ocr_res.execution_id)
        self.assertEqual(restored_res.project_id, ocr_res.project_id)
        self.assertEqual(restored_res.source_evidence_id, ocr_res.source_evidence_id)
        self.assertEqual(restored_res.status, OCRStatus.SUCCESS)
        self.assertEqual(restored_res.extracted_text, "Order Confirmation")
        self.assertEqual(len(restored_res.text_regions), 1)
        self.assertEqual(restored_res.text_regions[0].text, "Test Region")
        self.assertEqual(restored_res.confidence, 0.88)
        self.assertEqual(restored_res.processing_metadata["engine"], "mock_ocr")

    def test_event_emission_on_ocr_extraction(self) -> None:
        """Event bus receives TEST_OCR_EXTRACTED on success and TEST_OCR_FAILED on failure."""
        mock_event_bus = MagicMock()
        provider = MockOCRProvider(default_text="Sample", event_bus=mock_event_bus)
        evidence = self._make_screenshot_evidence()
        execution = self._make_execution()

        # Success event
        provider.extract_text(evidence, execution)
        mock_event_bus.emit.assert_called_once()
        self.assertEqual(
            mock_event_bus.emit.call_args[1]["event_type"],
            EventType.TEST_OCR_EXTRACTED,
        )

        # Failure event
        mock_event_bus.reset_mock()
        provider.set_failure(True)
        provider.extract_text(evidence, execution)
        mock_event_bus.emit.assert_called_once()
        self.assertEqual(
            mock_event_bus.emit.call_args[1]["event_type"],
            EventType.TEST_OCR_FAILED,
        )


if __name__ == "__main__":
    unittest.main()
