from __future__ import annotations

import unittest
from typing import Any

from core.tester.contracts.geometry import ViewportDimensions
from core.tester.contracts.identifiers import (
    NETWORK_PERFORMANCE_ID_PREFIX,
    NETWORK_REQUEST_ID_PREFIX,
    new_evidence_id,
    new_execution_id,
    new_network_performance_id,
    new_network_request_id,
    new_test_case_id,
    new_trace_id,
    new_work_order_id,
    validate_network_performance_id,
    validate_network_request_id,
)
from core.tester.contracts.network_performance import (
    NetworkPerformanceResult,
    NetworkPerformanceSpec,
    NetworkRequestRecord,
)
from core.tester.contracts.performance import (
    PerformanceEnvironment,
    PerformanceMeasurementBudget,
)
from core.tester.evaluator.network_performance_evaluator import (
    NetworkPerformanceEvaluator,
)
from core.tester.evaluator.performance_recorder import (
    PerformanceMeasurementRecorder,
)
from core.tester.errors import (
    InvalidTesterIdError,
    TesterBoundaryViolationError,
    TesterLineageError,
    TesterValidationError,
)
from core.tester.types import (
    NetworkPerformanceStatus,
    PerformanceMeasurementStatus,
    PerformanceMetricType,
    ResourceImportance,
    ResourceType,
)


class TestTesterNetworkPerformance(unittest.TestCase):
    """
    Unit test suite for Tester V1 — Phase 7.4: Network & Resource Performance.
    Tests request measurement, resource categories, 404 handling, slow requests,
    request counts, missing metrics, duplicate-defect prevention, provenance,
    execution isolation, and strict boundary guards.
    """

    def setUp(self) -> None:
        self.execution_id = new_execution_id()
        self.project_id = "proj-portal-app"
        self.work_order_id = new_work_order_id()
        self.test_case_id = new_test_case_id()
        self.evidence_id = new_evidence_id()
        self.trace_id = new_trace_id()

        self.environment = PerformanceEnvironment(
            browser="chromium-headless",
            viewport=ViewportDimensions(width=1280, height=800),
            network_configuration="unthrottled",
            application_revision="rev-7.4-rc1",
            test_execution=self.execution_id,
        )

        self.recorder = PerformanceMeasurementRecorder(
            execution_id=self.execution_id,
            project_id=self.project_id,
            work_order_id=self.work_order_id,
            environment=self.environment,
            budget=PerformanceMeasurementBudget(max_measurements=100, max_samples_per_metric=25),
        )

        self.evaluator = NetworkPerformanceEvaluator(
            execution_id=self.execution_id,
            project_id=self.project_id,
            work_order_id=self.work_order_id,
            recorder=self.recorder,
        )

    # -----------------------------------------------------------------------
    # Scenario 1: Successful Request
    # -----------------------------------------------------------------------
    def test_01_successful_request(self) -> None:
        """Scenario 1: Normal 200 request with valid duration and size evaluates to SUCCESS."""
        spec = NetworkPerformanceSpec(
            execution_id=self.execution_id,
            test_case_id=self.test_case_id,
        )

        req = NetworkRequestRecord(
            url="http://localhost:3000/app.js",
            method="GET",
            status_code=200,
            duration_ms=45.0,
            resource_type=ResourceType.SCRIPT,
            importance=ResourceImportance.REQUIRED,
            transfer_size_bytes=10240,
            execution_id=self.execution_id,
            test_case_id=self.test_case_id,
        )

        result = self.evaluator.evaluate_network_activity(spec, [req])

        self.assertTrue(result.is_success)
        self.assertEqual(result.status, NetworkPerformanceStatus.SUCCESS)
        self.assertEqual(result.total_requests, 1)
        self.assertEqual(result.failed_requests, 0)
        self.assertEqual(result.slow_requests, 0)
        self.assertEqual(result.total_transfer_size_bytes, 10240)
        self.assertEqual(result.avg_request_duration_ms, 45.0)

    # -----------------------------------------------------------------------
    # Scenario 2: Slow Request
    # -----------------------------------------------------------------------
    def test_02_slow_request(self) -> None:
        """Scenario 2: Request exceeding explicit threshold flagged as slow; status DEGRADED."""
        spec = NetworkPerformanceSpec(
            execution_id=self.execution_id,
            test_case_id=self.test_case_id,
            request_duration_threshold_ms=500.0,
        )

        req = NetworkRequestRecord(
            url="http://localhost:3000/api/heavy-data",
            method="GET",
            status_code=200,
            duration_ms=1250.0,
            resource_type=ResourceType.API,
            execution_id=self.execution_id,
            test_case_id=self.test_case_id,
        )

        result = self.evaluator.evaluate_network_activity(spec, [req])

        self.assertTrue(result.is_degraded)
        self.assertEqual(result.status, NetworkPerformanceStatus.DEGRADED)
        self.assertEqual(result.slow_requests, 1)
        self.assertIs(result.duration_threshold_met, False)

    # -----------------------------------------------------------------------
    # Scenario 3: Failed Request (Connection Dropped / Network Error)
    # -----------------------------------------------------------------------
    def test_03_failed_request(self) -> None:
        """Scenario 3: Connection drop / network error flagged as failed request."""
        spec = NetworkPerformanceSpec(
            execution_id=self.execution_id,
            test_case_id=self.test_case_id,
        )

        req = NetworkRequestRecord(
            url="http://localhost:3000/api/stream",
            method="GET",
            status_code=None,  # No response received
            is_success=False,
            error_message="net::ERR_CONNECTION_RESET",
            execution_id=self.execution_id,
            test_case_id=self.test_case_id,
        )

        result = self.evaluator.evaluate_network_activity(spec, [req])

        self.assertTrue(result.is_failed)
        self.assertEqual(result.status, NetworkPerformanceStatus.FAILED)
        self.assertEqual(result.failed_requests, 1)

    # -----------------------------------------------------------------------
    # Scenario 4: Required 404 Handling
    # -----------------------------------------------------------------------
    def test_04_required_404(self) -> None:
        """Scenario 4: Required application resource 404 linked to functional defect without duplicate defect."""
        spec = NetworkPerformanceSpec(
            execution_id=self.execution_id,
            test_case_id=self.test_case_id,
            critical_resources=["/images/dashboard-thumb.webp"],
        )

        req = NetworkRequestRecord(
            url="http://localhost:3000/images/dashboard-thumb.webp",
            method="GET",
            status_code=404,
            resource_type=ResourceType.IMAGE,
            importance=ResourceImportance.REQUIRED,
            execution_id=self.execution_id,
            test_case_id=self.test_case_id,
        )

        result = self.evaluator.evaluate_network_activity(spec, [req])

        self.assertEqual(result.status, NetworkPerformanceStatus.FAILED)
        self.assertEqual(result.failed_requests, 1)

        # Coordinate failure classification
        failure_info = result.resource_failures[0]
        classification = failure_info["classification"]
        self.assertEqual(classification["classification"], "FUNCTIONAL_RESOURCE_DEFECT")
        self.assertEqual(classification["recommended_defect_type"], "FUNCTIONAL")
        self.assertFalse(classification["is_performance_defect"])
        self.assertIn("Linked to functional evaluation", classification["reason"])

    # -----------------------------------------------------------------------
    # Scenario 5: Optional 404 Handling
    # -----------------------------------------------------------------------
    def test_05_optional_404(self) -> None:
        """Scenario 5: Optional analytics 404 recorded as warning; does not fail evaluation."""
        spec = NetworkPerformanceSpec(
            execution_id=self.execution_id,
            test_case_id=self.test_case_id,
            optional_resources=["/analytics/collect"],
        )

        req = NetworkRequestRecord(
            url="http://localhost:3000/analytics/collect",
            method="POST",
            status_code=404,
            resource_type=ResourceType.API,
            importance=ResourceImportance.OPTIONAL,
            execution_id=self.execution_id,
            test_case_id=self.test_case_id,
        )

        result = self.evaluator.evaluate_network_activity(spec, [req])

        # Optional 404 does NOT cause FAILED
        self.assertNotEqual(result.status, NetworkPerformanceStatus.FAILED)
        self.assertEqual(result.status, NetworkPerformanceStatus.DEGRADED)

        classification = self.evaluator.classify_resource_failure(req)
        self.assertEqual(classification["classification"], "OPTIONAL_RESOURCE_WARNING")
        self.assertIsNone(classification["recommended_defect_type"])
        self.assertFalse(classification["is_performance_defect"])

    # -----------------------------------------------------------------------
    # Scenario 6: API 500 Server Error
    # -----------------------------------------------------------------------
    def test_06_api_500(self) -> None:
        """Scenario 6: Critical API server error 500 detected and categorized."""
        spec = NetworkPerformanceSpec(
            execution_id=self.execution_id,
            test_case_id=self.test_case_id,
        )

        req = NetworkRequestRecord(
            url="http://localhost:3000/api/checkout",
            method="POST",
            status_code=500,
            resource_type=ResourceType.API,
            importance=ResourceImportance.REQUIRED,
            execution_id=self.execution_id,
            test_case_id=self.test_case_id,
        )

        result = self.evaluator.evaluate_network_activity(spec, [req])

        self.assertEqual(result.status, NetworkPerformanceStatus.FAILED)
        self.assertTrue(req.is_500)
        classification = self.evaluator.classify_resource_failure(req)
        self.assertEqual(classification["classification"], "API_SERVER_ERROR")
        self.assertEqual(classification["recommended_defect_type"], "FUNCTIONAL")

    # -----------------------------------------------------------------------
    # Scenario 7: Resource Timing Unavailable
    # -----------------------------------------------------------------------
    def test_07_resource_timing_unavailable(self) -> None:
        """Scenario 7: Missing timing API records None without synthesizing fallback numbers."""
        spec = NetworkPerformanceSpec(
            execution_id=self.execution_id,
            test_case_id=self.test_case_id,
        )

        req = NetworkRequestRecord(
            url="http://localhost:3000/style.css",
            method="GET",
            status_code=200,
            duration_ms=None,  # Timing unavailable
            resource_type=ResourceType.STYLESHEET,
            execution_id=self.execution_id,
            test_case_id=self.test_case_id,
        )

        result = self.evaluator.evaluate_network_activity(spec, [req])

        self.assertIsNone(req.duration_ms)
        self.assertIsNone(result.avg_request_duration_ms)
        self.assertIsNone(result.max_request_duration_ms)

    # -----------------------------------------------------------------------
    # Scenario 8: Transfer Size Unavailable
    # -----------------------------------------------------------------------
    def test_08_transfer_size_unavailable(self) -> None:
        """Scenario 8: Missing transfer size records None without synthesizing numbers."""
        spec = NetworkPerformanceSpec(
            execution_id=self.execution_id,
            test_case_id=self.test_case_id,
        )

        req = NetworkRequestRecord(
            url="http://localhost:3000/font.woff2",
            method="GET",
            status_code=200,
            transfer_size_bytes=None,  # Size unavailable (e.g. cross-origin opaque)
            resource_type=ResourceType.FONT,
            execution_id=self.execution_id,
            test_case_id=self.test_case_id,
        )

        result = self.evaluator.evaluate_network_activity(spec, [req])

        self.assertIsNone(req.transfer_size_bytes)
        self.assertIsNone(result.total_transfer_size_bytes)

    # -----------------------------------------------------------------------
    # Scenario 9: Explicit Request Threshold
    # -----------------------------------------------------------------------
    def test_09_request_threshold(self) -> None:
        """Scenario 9: Explicit latency threshold evaluates duration_threshold_met=True/False."""
        spec = NetworkPerformanceSpec(
            execution_id=self.execution_id,
            test_case_id=self.test_case_id,
            request_duration_threshold_ms=200.0,
        )

        # Case A: All requests under threshold (pass)
        reqs_pass = [
            NetworkRequestRecord(url="http://localhost:3000/a", duration_ms=80.0),
            NetworkRequestRecord(url="http://localhost:3000/b", duration_ms=150.0),
        ]
        res_pass = self.evaluator.evaluate_network_activity(spec, reqs_pass)
        self.assertIs(res_pass.duration_threshold_met, True)
        self.assertEqual(res_pass.slow_requests, 0)

        # Case B: Request exceeding threshold (fail)
        reqs_fail = [
            NetworkRequestRecord(url="http://localhost:3000/c", duration_ms=350.0),
        ]
        res_fail = self.evaluator.evaluate_network_activity(spec, reqs_fail)
        self.assertIs(res_fail.duration_threshold_met, False)
        self.assertEqual(res_fail.slow_requests, 1)

    # -----------------------------------------------------------------------
    # Scenario 10: No Threshold (Never Invent Benchmarks)
    # -----------------------------------------------------------------------
    def test_10_no_threshold(self) -> None:
        """Scenario 10: Without threshold, records duration only; duration_threshold_met is None."""
        spec = NetworkPerformanceSpec(
            execution_id=self.execution_id,
            test_case_id=self.test_case_id,
            request_duration_threshold_ms=None,
        )

        req = NetworkRequestRecord(
            url="http://localhost:3000/large-bundle.js",
            duration_ms=4500.0,
        )

        result = self.evaluator.evaluate_network_activity(spec, [req])

        self.assertIsNone(result.duration_threshold_met)
        self.assertEqual(result.avg_request_duration_ms, 4500.0)

    # -----------------------------------------------------------------------
    # Scenario 11: Request Count Measurement (Explicit Budget vs Unbounded)
    # -----------------------------------------------------------------------
    def test_11_request_count_measurement(self) -> None:
        """Scenario 11: Evaluates request budget if specified; records raw count as metric without judging."""
        # Case A: Within budget
        spec_budget = NetworkPerformanceSpec(
            execution_id=self.execution_id,
            test_case_id=self.test_case_id,
            max_requests_budget=5,
        )
        reqs_few = [NetworkRequestRecord(url=f"http://localhost:3000/{i}", status_code=200) for i in range(3)]
        res_few = self.evaluator.evaluate_network_activity(spec_budget, reqs_few)
        self.assertIs(res_few.request_budget_met, True)
        self.assertEqual(res_few.total_requests, 3)

        # Case B: Exceeding budget
        reqs_many = [NetworkRequestRecord(url=f"http://localhost:3000/{i}", status_code=200) for i in range(7)]
        res_many = self.evaluator.evaluate_network_activity(spec_budget, reqs_many)
        self.assertIs(res_many.request_budget_met, False)
        self.assertEqual(res_many.status, NetworkPerformanceStatus.EXCESSIVE_REQUESTS)

        # Case C: No budget configured (e.g. 42 requests is just an observation)
        spec_no_budget = NetworkPerformanceSpec(
            execution_id=self.execution_id,
            test_case_id=self.test_case_id,
            max_requests_budget=None,
        )
        reqs_42 = [NetworkRequestRecord(url=f"http://localhost:3000/item-{i}", status_code=200) for i in range(42)]
        res_42 = self.evaluator.evaluate_network_activity(spec_no_budget, reqs_42)
        self.assertEqual(res_42.total_requests, 42)
        self.assertIsNone(res_42.request_budget_met)
        self.assertEqual(res_42.status, NetworkPerformanceStatus.SUCCESS)

    # -----------------------------------------------------------------------
    # Scenario 12: Duplicate-Defect Prevention
    # -----------------------------------------------------------------------
    def test_12_duplicate_defect_prevention(self) -> None:
        """Scenario 12: Coordinates with functional evaluation avoiding duplicate defect generation."""
        req_required_404 = NetworkRequestRecord(
            url="http://localhost:3000/images/critical.png",
            status_code=404,
            importance=ResourceImportance.REQUIRED,
        )
        c1 = self.evaluator.classify_resource_failure(req_required_404)
        self.assertFalse(c1["is_performance_defect"])
        self.assertEqual(c1["target_layer"], "functional_acceptance")

        req_optional_404 = NetworkRequestRecord(
            url="http://localhost:3000/telemetry",
            status_code=404,
            importance=ResourceImportance.OPTIONAL,
        )
        c2 = self.evaluator.classify_resource_failure(req_optional_404)
        self.assertIsNone(c2["recommended_defect_type"])
        self.assertEqual(c2["target_layer"], "observation_only")

    # -----------------------------------------------------------------------
    # Scenario 13: Provenance & Identifiers
    # -----------------------------------------------------------------------
    def test_13_provenance(self) -> None:
        """Scenario 13: Validates tnetp- and treq- prefixes, execution ID, test case ID, and timestamps."""
        spec = NetworkPerformanceSpec(
            execution_id=self.execution_id,
            test_case_id=self.test_case_id,
        )
        req = NetworkRequestRecord(
            url="http://localhost:3000/home",
            execution_id=self.execution_id,
            test_case_id=self.test_case_id,
        )

        result = self.evaluator.evaluate_network_activity(spec, [req])

        self.assertTrue(result.network_performance_id.startswith(NETWORK_PERFORMANCE_ID_PREFIX))
        validate_network_performance_id(result.network_performance_id)

        self.assertTrue(req.request_id.startswith(NETWORK_REQUEST_ID_PREFIX))
        validate_network_request_id(req.request_id)

        self.assertEqual(result.execution_id, self.execution_id)
        self.assertEqual(result.spec.test_case_id, self.test_case_id)
        self.assertIn("started_at", result.timestamps)
        self.assertIn("completed_at", result.timestamps)

    # -----------------------------------------------------------------------
    # Scenario 14: Execution Isolation
    # -----------------------------------------------------------------------
    def test_14_execution_isolation(self) -> None:
        """Scenario 14: Rejects foreign execution IDs via TesterLineageError; validates ID constraints."""
        foreign_id = new_execution_id()
        foreign_spec = NetworkPerformanceSpec(
            execution_id=foreign_id,
            test_case_id=self.test_case_id,
        )

        with self.assertRaises(TesterLineageError):
            self.evaluator.evaluate_network_activity(foreign_spec, [])

        with self.assertRaises(InvalidTesterIdError):
            NetworkPerformanceResult(
                network_performance_id="invalid-net-id",
                execution_id=self.execution_id,
                spec=NetworkPerformanceSpec(execution_id=self.execution_id),
            )

    # -----------------------------------------------------------------------
    # Scenario 15: Invariant Boundary Guards
    # -----------------------------------------------------------------------
    def test_15_zero_fixing_and_defect_guards(self) -> None:
        """Scenario 15: Prohibits code modifications and defect generation."""
        req = NetworkRequestRecord(url="http://localhost:3000/api")
        spec = NetworkPerformanceSpec()
        result = self.evaluator.evaluate_network_activity(spec, [req])

        # Record guards
        with self.assertRaises(TesterBoundaryViolationError):
            req.apply_fix()
        with self.assertRaises(TesterBoundaryViolationError):
            req.create_defect()

        # Spec guards
        with self.assertRaises(TesterBoundaryViolationError):
            spec.apply_fix()
        with self.assertRaises(TesterBoundaryViolationError):
            spec.create_defect()

        # Evaluator guards
        with self.assertRaises(TesterBoundaryViolationError):
            self.evaluator.apply_fix()
        with self.assertRaises(TesterBoundaryViolationError):
            self.evaluator.create_defect()

        # Result guards
        with self.assertRaises(TesterBoundaryViolationError):
            result.apply_fix()
        with self.assertRaises(TesterBoundaryViolationError):
            result.create_defect()

    # -----------------------------------------------------------------------
    # Scenario 16: Serialization Roundtrip
    # -----------------------------------------------------------------------
    def test_16_serialization_roundtrip(self) -> None:
        """Scenario 16: NetworkRequestRecord, NetworkPerformanceSpec, and Result roundtrip through dict."""
        req = NetworkRequestRecord(
            url="http://localhost:3000/app.bundle.js",
            method="GET",
            status_code=200,
            duration_ms=120.0,
            resource_type=ResourceType.SCRIPT,
            importance=ResourceImportance.REQUIRED,
            transfer_size_bytes=54321,
            execution_id=self.execution_id,
        )
        req_loaded = NetworkRequestRecord.from_dict(req.to_dict())
        self.assertEqual(req_loaded.url, req.url)
        self.assertEqual(req_loaded.duration_ms, 120.0)
        self.assertEqual(req_loaded.resource_type, ResourceType.SCRIPT)
        self.assertEqual(req_loaded.transfer_size_bytes, 54321)

        spec = NetworkPerformanceSpec(
            max_requests_budget=20,
            request_duration_threshold_ms=300.0,
            critical_resources=["/main.js"],
            execution_id=self.execution_id,
        )
        spec_loaded = NetworkPerformanceSpec.from_dict(spec.to_dict())
        self.assertEqual(spec_loaded.max_requests_budget, 20)
        self.assertEqual(spec_loaded.request_duration_threshold_ms, 300.0)
        self.assertIn("/main.js", spec_loaded.critical_resources)

        result = self.evaluator.evaluate_network_activity(spec, [req])
        res_loaded = NetworkPerformanceResult.from_dict(result.to_dict())
        self.assertEqual(res_loaded.network_performance_id, result.network_performance_id)
        self.assertEqual(res_loaded.status, NetworkPerformanceStatus.SUCCESS)
        self.assertEqual(res_loaded.total_requests, 1)
        self.assertIs(res_loaded.request_budget_met, True)


if __name__ == "__main__":
    unittest.main()
