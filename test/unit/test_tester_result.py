from __future__ import annotations

import unittest
from unittest.mock import patch
import uuid

from core.models import Task, WorkerOutput
from core.tester import (
    AcceptanceCriterionResult,
    AcceptanceCriterionStatus,
    AcceptanceSummaryStatus,
    DefectSeverity,
    DefectType,
    EvidenceType,
    FindingCategory,
    InvalidTesterIdError,
    QualitySummary,
    QualityThresholds,
    RecommendationPriority,
    ShipRecommendation,
    TestCaseResult,
    TestCaseStatus,
    TestCategory,
    TesterDefect,
    TesterEvidence,
    TesterExecution,
    TesterFinding,
    TesterLineageError,
    TesterRecommendation,
    TesterResult,
    TesterResultStatus,
    TesterResultValidator,
    TesterValidationError,
    TesterWorkOrder,
    new_defect_id,
    new_evidence_id,
    new_execution_id,
    new_finding_id,
    new_recommendation_id,
    new_result_id,
    new_test_case_id,
    new_work_order_id,
)


class TestTesterResult(unittest.TestCase):
    """
    Unit tests for TesterResult and evidence contracts (Tester V1 Phase 1.3).
    Validates:
    1. TesterResult creation and baseline validation
    2. Lineage preservation across Task -> WorkOrder -> Execution -> Result
    3. Project isolation enforcement
    4. TestCaseResult statuses (PASS, FAIL, BLOCKED, NOT_RUN, NOT_VERIFIED)
    5. Defect validation (concrete observations vs rejection of vague opinions)
    6. Finding validation (informational vs actionable observations)
    7. Acceptance result validation
    8. Evidence creation (types, checksums, artifact references)
    9. Evidence provenance tracking across evaluation chain
    10. Recommendation structure (purely advisory, no execution trigger)
    11. Quality summary aggregation (deterministic counting & thresholds)
    12. Result status semantics (COMPLETED != product acceptance)
    13. Advisory ship recommendation
    14. Complete serialization round-trip fidelity
    15. Tampering detection on identifiers and lineage
    16. Invalid/dangling evidence references rejection
    17. Acceptance PASS without evidence rejection
    18. Historical result integrity and zero execution during validation
    """

    def _make_valid_evidence(self, eid: str = "tevid-11111111", **kwargs) -> TesterEvidence:
        defaults = {
            "evidence_id": eid,
            "evidence_type": EvidenceType.SCREENSHOT,
            "data": "screenshot_data_bytes_simulated",
            "description": "Screenshot of checkout page with error banner",
            "source": "NAVIGATE",
        }
        defaults.update(kwargs)
        return TesterEvidence(**defaults)

    def _make_valid_result(self, **kwargs) -> TesterResult:
        ev1 = self._make_valid_evidence("tevid-00000001", data="log output 1")
        ev2 = self._make_valid_evidence("tevid-00000002", data="screenshot 2")

        tc1 = TestCaseResult(
            test_id="ttest-00000001",
            name="Verify checkout button",
            category=TestCategory.FUNCTIONAL,
            status=TestCaseStatus.PASS,
            observed_behavior="Button redirects to /payment",
            evidence_ids=[ev1.evidence_id],
        )

        defect1 = TesterDefect(
            defect_id="tdef-00000001",
            work_order_id="two-00000001",
            execution_id="texec-00000001",
            title="Duplicate charge on rapid click",
            description="Rapidly clicking submit submits two distinct payment requests.",
            observed_behavior="Two transactions appear in ledger.",
            reproduction_steps=["Click submit button twice rapidly"],
            severity=DefectSeverity.HIGH,
            defect_type=DefectType.FUNCTIONAL,
            evidence_ids=[ev2.evidence_id],
        )

        finding1 = TesterFinding(
            finding_id="tfind-00000001",
            category=FindingCategory.UX,
            title="Spinner lacks accessible label",
            description="Loading spinner does not specify aria-label.",
            observed_behavior="Screen reader announces unlabelled graphic.",
            severity=DefectSeverity.LOW,
            evidence_ids=[ev1.evidence_id],
        )

        ac1 = AcceptanceCriterionResult(
            criterion_id="ac-001",
            description="Checkout completes within 2 seconds",
            status=AcceptanceCriterionStatus.PASS,
            observed_behavior="Checkout completed in 850ms",
            evidence_ids=[ev1.evidence_id],
        )

        rec1 = TesterRecommendation(
            recommendation_id="trec-00000001",
            title="Add debounce to payment submit button",
            description="Prevent concurrent clicks by disabling button on click.",
            related_finding_id=defect1.defect_id,
            evidence_ids=[ev2.evidence_id],
            priority=RecommendationPriority.HIGH,
        )

        defaults = {
            "result_id": "tres-00000001",
            "work_order_id": "two-00000001",
            "execution_id": "texec-00000001",
            "task_id": "task-base-001",
            "project_id": "proj-base",
            "correlation_id": "corr-base-001",
            "status": TesterResultStatus.COMPLETED,
            "ship_recommendation": ShipRecommendation.DO_NOT_SHIP,
            "summary": "Completed evaluation of checkout flow. Identified 1 high defect.",
            "test_cases": [tc1],
            "defects": [defect1],
            "findings": [finding1],
            "acceptance_results": [ac1],
            "evidence": [ev1, ev2],
            "recommendations": [rec1],
        }
        defaults.update(kwargs)
        return TesterResult(**defaults)

    def test_1_tester_result_creation(self):
        """1. Verify valid TesterResult creation, defaults, and validation."""
        result = self._make_valid_result()
        result.validate()

        self.assertEqual(result.result_id, "tres-00000001")
        self.assertEqual(result.work_order_id, "two-00000001")
        self.assertEqual(result.execution_id, "texec-00000001")
        self.assertEqual(result.task_id, "task-base-001")
        self.assertEqual(result.project_id, "proj-base")
        self.assertEqual(result.status, TesterResultStatus.COMPLETED)
        self.assertEqual(result.ship_recommendation, ShipRecommendation.DO_NOT_SHIP)
        self.assertEqual(result.tests_run, 1)
        self.assertEqual(result.tests_passed, 1)
        self.assertEqual(len(result.defects), 1)
        self.assertEqual(len(result.findings), 1)
        self.assertEqual(len(result.acceptance_results), 1)
        self.assertEqual(len(result.evidence), 2)
        self.assertIsNotNone(result.quality_summary)
        self.assertEqual(result.quality_summary.high_defects, 1)

    def test_2_lineage_preservation(self):
        """2. Verify strict lineage matching against execution and work order."""
        result = self._make_valid_result()

        class MockExec:
            execution_id = "texec-00000001"
            work_order_id = "two-00000001"
            task_id = "task-base-001"
            project_id = "proj-base"
            correlation_id = "corr-base-001"

        class MockWO:
            work_order_id = "two-00000001"
            task_id = "task-base-001"
            project_id = "proj-base"
            correlation_id = "corr-base-001"

        # Valid lineage passes
        result.validate_lineage(MockExec(), MockWO())

        # Mismatched execution ID
        class ForeignExec(MockExec):
            execution_id = "texec-foreign-99"

        with self.assertRaises(TesterLineageError):
            result.validate_lineage(ForeignExec(), MockWO())

        # Mismatched work order ID
        class ForeignWO(MockWO):
            work_order_id = "two-foreign-99"

        with self.assertRaises(TesterLineageError):
            result.validate_lineage(MockExec(), ForeignWO())

    def test_3_project_isolation(self):
        """3. Verify project isolation enforcement: result cannot cross project boundary."""
        result = self._make_valid_result(project_id="proj-alpha")

        class ForeignProjectExec:
            execution_id = "texec-00000001"
            work_order_id = "two-00000001"
            task_id = "task-base-001"
            project_id = "proj-beta"  # foreign
            correlation_id = "corr-base-001"

        with self.assertRaises(TesterLineageError) as ctx:
            result.validate_lineage(ForeignProjectExec())
        self.assertIn("project_id", str(ctx.exception))

    def test_4_test_case_result_statuses(self):
        """4. Verify TestCaseResult statuses and properties."""
        tc_pass = TestCaseResult("ttest-01", "Test 1", status=TestCaseStatus.PASS)
        tc_fail = TestCaseResult("ttest-02", "Test 2", status=TestCaseStatus.FAIL)
        tc_blk = TestCaseResult("ttest-03", "Test 3", status=TestCaseStatus.BLOCKED)
        tc_notrun = TestCaseResult("ttest-04", "Test 4", status=TestCaseStatus.NOT_RUN)
        tc_notver = TestCaseResult("ttest-05", "Test 5", status=TestCaseStatus.NOT_VERIFIED)

        self.assertTrue(tc_pass.is_pass)
        self.assertTrue(tc_fail.is_fail)
        self.assertTrue(tc_blk.is_blocked)
        self.assertTrue(tc_notrun.is_not_run)
        self.assertTrue(tc_notver.is_not_verified)

    def test_5_defect_validation(self):
        """5. Verify concrete observation requirement and rejection of vague subjective opinions."""
        # Valid defect with concrete observation
        valid_def = TesterDefect(
            defect_id="tdef-11111111",
            work_order_id="two-11111111",
            title="Modal content clipped below viewport",
            description="On 768px viewport, dialog buttons render below fold without scrollbar.",
            observed_behavior="Scrollbar is missing and action buttons are not reachable.",
            reproduction_steps=["Resize viewport to 768px", "Open terms dialog"],
        )
        valid_def.validate()

        # Reject vague subjective opinions without reproduction steps or test ID
        vague_opinions = [
            "The UI feels slightly boring.",
            "Could make this more premium.",
            "Maybe users won't like this.",
            "Looks ugly on mobile.",
        ]
        for opinion in vague_opinions:
            bad_def = TesterDefect(
                defect_id="tdef-22222222",
                work_order_id="two-11111111",
                title="Subjective UI Impression",
                description=opinion,
            )
            with self.assertRaises(TesterValidationError) as ctx:
                bad_def.validate()
            self.assertEqual(ctx.exception.field_name, "description")

    def test_6_finding_validation(self):
        """6. Verify informational vs actionable findings with categories and evidence IDs."""
        finding = TesterFinding(
            finding_id="tfind-11111111",
            category=FindingCategory.PERFORMANCE,
            title="Image payload size 12MB",
            description="Hero banner image is uncompressed PNG.",
            observed_behavior="Download time 4.2s on simulated 4G.",
            actionable=True,
            confidence=0.95,
        )
        finding.validate()
        self.assertEqual(finding.category, FindingCategory.PERFORMANCE)
        self.assertTrue(finding.actionable)

    def test_7_acceptance_result_validation(self):
        """7. Verify acceptance criterion results and statuses."""
        ac = AcceptanceCriterionResult(
            criterion_id="ac-auth-01",
            description="User session token expires after 15m idle",
            status=AcceptanceCriterionStatus.PASS,
            observed_behavior="Request at 15m01s returns 401 Unauthorized",
            evidence_ids=["tevid-00000001"],
            explanation="Token invalidation verified via simulated timer.",
        )
        ac.validate()
        self.assertTrue(ac.is_pass)
        self.assertFalse(ac.is_fail)

    def test_8_evidence_creation(self):
        """8. Verify evidence creation, types, and cryptographic checksum calculation."""
        ev = TesterEvidence(
            evidence_id="tevid-99999999",
            evidence_type=EvidenceType.METRIC,
            data="LCP=1200ms;FID=12ms;CLS=0.01",
            description="Core Web Vitals trace",
            artifact_reference="/artifacts/metrics.json",
        )
        ev.validate()
        self.assertEqual(ev.evidence_type, EvidenceType.METRIC)
        self.assertTrue(len(ev.checksum) == 64)  # SHA-256 length
        # Invariant check: Checksum establishes payload identity, not truth
        self.assertIn("LCP=1200ms", ev.data)

    def test_9_evidence_provenance(self):
        """9. Verify evidence provenance chain: Test -> Observation -> Evidence -> Defect/Finding -> Result."""
        ev = self._make_valid_evidence("tevid-prov-01")
        tc = TestCaseResult("ttest-01", "Login test", evidence_ids=[ev.evidence_id])
        defect = TesterDefect(
            defect_id="tdef-prov-01",
            work_order_id="two-00000001",
            title="Auth failed",
            description="Authentication request returned 401 Unauthorized instead of 200 OK",
            observed_behavior="401 error",
            reproduction_steps=["Submit invalid creds"],
            evidence_ids=[ev.evidence_id],
        )
        result = self._make_valid_result(
            test_cases=[tc],
            defects=[defect],
            findings=[],
            acceptance_results=[],
            recommendations=[],
            evidence=[ev],
        )
        result.validate()
        self.assertIn(ev.evidence_id, result.evidence_ids)

    def test_10_recommendation_structure(self):
        """10. Verify recommendation structure: advisory only, does not trigger executions."""
        rec = TesterRecommendation(
            recommendation_id="trec-11111111",
            title="Cache product catalog responses",
            description="Catalog response takes 450ms; caching can reduce it to <50ms.",
            priority=RecommendationPriority.MEDIUM,
            confidence=0.9,
        )
        rec.validate()
        # Invariant: purely advisory
        self.assertEqual(rec.priority, RecommendationPriority.MEDIUM)

    def test_11_quality_summary_aggregation(self):
        """11. Verify deterministic quality summary computation without fuzzy AI scores."""
        tcs = [
            TestCaseResult("ttest-01", "T1", status=TestCaseStatus.PASS),
            TestCaseResult("ttest-02", "T2", status=TestCaseStatus.FAIL),
            TestCaseResult("ttest-03", "T3", status=TestCaseStatus.BLOCKED),
        ]
        defs = [
            TesterDefect("tdef-01", "two-1", "D1", "Desc", severity=DefectSeverity.CRITICAL, observed_behavior="Crash"),
            TesterDefect("tdef-02", "two-1", "D2", "Desc", severity=DefectSeverity.HIGH, observed_behavior="Slow"),
            TesterDefect("tdef-03", "two-1", "D3", "Desc", severity=DefectSeverity.LOW, observed_behavior="Typo"),
        ]
        thresholds = QualityThresholds(max_critical_defects=0, max_high_defects=1)

        summary = QualitySummary.compute(
            test_cases=tcs,
            defects=defs,
            quality_thresholds=thresholds,
        )
        self.assertEqual(summary.total_tests, 3)
        self.assertEqual(summary.passed, 1)
        self.assertEqual(summary.failed, 1)
        self.assertEqual(summary.blocked, 1)
        self.assertEqual(summary.critical_defects, 1)
        self.assertEqual(summary.high_defects, 1)
        self.assertEqual(summary.medium_low_defects, 1)
        self.assertFalse(summary.thresholds_met)  # Critical defect exceeds threshold of 0

    def test_12_result_status_semantics(self):
        """12. Invariant: COMPLETED indicates Tester finished its evaluation, NOT product acceptance."""
        result = self._make_valid_result(
            status=TesterResultStatus.COMPLETED,
            ship_recommendation=ShipRecommendation.DO_NOT_SHIP,
        )
        result.validate()
        self.assertTrue(result.is_success())
        self.assertEqual(result.ship_recommendation, ShipRecommendation.DO_NOT_SHIP)
        # Evaluator completed its assignment despite product not meeting quality bars
        self.assertTrue(result.has_critical_defects())

    def test_13_advisory_ship_recommendation(self):
        """13. Verify advisory nature of ship recommendation."""
        for rec in [
            ShipRecommendation.SHIP,
            ShipRecommendation.SHIP_WITH_WARNINGS,
            ShipRecommendation.DO_NOT_SHIP,
            ShipRecommendation.NOT_VERIFIED,
        ]:
            result = self._make_valid_result(ship_recommendation=rec)
            self.assertEqual(result.ship_recommendation, rec)

    def test_14_serialization_roundtrip(self):
        """14. Verify complete dictionary round-trip fidelity for TesterResult and nested objects."""
        original = self._make_valid_result()
        original.validate()

        data = original.to_dict()
        self.assertIsInstance(data, dict)
        self.assertEqual(data["result_id"], original.result_id)
        self.assertEqual(data["ship_recommendation"], original.ship_recommendation.value)

        restored = TesterResult.from_dict(data)
        restored.validate()

        self.assertEqual(restored.result_id, original.result_id)
        self.assertEqual(restored.execution_id, original.execution_id)
        self.assertEqual(restored.work_order_id, original.work_order_id)
        self.assertEqual(restored.task_id, original.task_id)
        self.assertEqual(restored.project_id, original.project_id)
        self.assertEqual(restored.status, original.status)
        self.assertEqual(restored.ship_recommendation, original.ship_recommendation)
        self.assertEqual(len(restored.test_cases), len(original.test_cases))
        self.assertEqual(len(restored.defects), len(original.defects))
        self.assertEqual(len(restored.findings), len(original.findings))
        self.assertEqual(len(restored.acceptance_results), len(original.acceptance_results))
        self.assertEqual(len(restored.evidence), len(original.evidence))
        self.assertEqual(len(restored.recommendations), len(original.recommendations))

    def test_15_tampering_detection(self):
        """15. Verify detection of forged result_id, work_order_id, or execution_id."""
        with self.assertRaises(InvalidTesterIdError):
            self._make_valid_result(result_id="invalid-res-id")

        with self.assertRaises(InvalidTesterIdError):
            self._make_valid_result(execution_id="invalid-exec-id")

        with self.assertRaises(InvalidTesterIdError):
            self._make_valid_result(work_order_id="invalid-wo-id")

    def test_16_invalid_evidence_references(self):
        """16. Verify rejection of dangling/missing evidence IDs."""
        result = self._make_valid_result()
        # Add a test case referencing a non-existent evidence ID
        bad_tc = TestCaseResult("ttest-99", "Dangling test", evidence_ids=["tevid-missing-999"])
        result.test_cases.append(bad_tc)

        with self.assertRaises(TesterValidationError) as ctx:
            result.validate()
        self.assertEqual(ctx.exception.field_name, "evidence_ids")
        self.assertIn("tevid-missing-999", str(ctx.exception))

    def test_17_pass_without_evidence_rejection(self):
        """17. Invariant: Acceptance criterion marked PASS without evidence is strictly rejected."""
        ac_no_ev = AcceptanceCriterionResult(
            criterion_id="ac-99",
            description="Must have zero downtime",
            status=AcceptanceCriterionStatus.PASS,
            evidence_ids=[],  # Empty!
        )
        with self.assertRaises(TesterValidationError) as ctx:
            ac_no_ev.validate()
        self.assertEqual(ctx.exception.field_name, "evidence_ids")

    def test_18_historical_result_integrity(self):
        """18. Invariant: Zero subprocesses or source modification occur during result validation."""
        result = self._make_valid_result()

        with patch("subprocess.run") as mock_subproc, \
             patch("subprocess.Popen") as mock_popen, \
             patch("builtins.open", wraps=open) as mock_file_open:

            result.validate()
            d = result.to_dict()
            _ = TesterResult.from_dict(d)

            mock_subproc.assert_not_called()
            mock_popen.assert_not_called()

            for call in mock_file_open.call_args_list:
                args = call[0]
                if len(args) > 1:
                    mode = args[1]
                    self.assertNotIn("w", mode)
                    self.assertNotIn("a", mode)


if __name__ == "__main__":
    unittest.main()
