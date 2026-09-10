from __future__ import annotations

import unittest
from typing import Any, Optional

from core.events.model import Event
from core.events.types import EventType
from core.tester import (
    AcceptanceCriterion,
    ObservationType,
    RuntimeEvaluator,
    RuntimeEventSeverity,
    RuntimeEventType,
    RuntimeObservation,
    TestCase,
    TestPlan,
    TestScope,
    TestStep,
    TesterActionType,
    TesterBoundaryViolationError,
    TesterExecution,
    TesterExecutionStatus,
    TesterLineageError,
    TesterObservation,
    TesterValidationError,
    TesterWorkOrder,
    TestingCapability,
    new_evidence_id,
    new_execution_id,
    new_plan_id,
    new_runtime_event_id,
    new_test_case_id,
    new_work_order_id,
)


class TestTesterRuntimeEvaluator(unittest.TestCase):
    """
    Unit test suite for Phase 5.3: Runtime & Request Evaluation.

    Verifies the 14 required scenarios:
    1. Successful request -> HTTP_RESPONSE, INFO
    2. 404 evaluation -> differentiated required vs optional
    3. 500 server error -> high severity CRITICAL / ERROR
    4. API failure -> API_FAILURE
    5. Console error -> CONSOLE_ERROR, ERROR
    6. Runtime exception -> PROCESS_ERROR, ERROR
    7. Navigation failure -> NAVIGATION_FAILURE, ERROR
    8. Application crash -> APPLICATION_CRASH, CRITICAL
    9. Irrelevant external error filtering -> noise marked non-application owned
    10. Required resource failure -> RESOURCE_FAILURE, ERROR
    11. Optional resource failure -> HTTP_RESPONSE, WARNING
    12. Provenance -> lineage, trace, evidence IDs preserved
    13. Execution isolation -> foreign execution/project rejected
    14. Deterministic behavior -> reproducible observations on identical inputs
    """

    def setUp(self) -> None:
        self.project_id = "proj-eval-unit"
        self.task_id = "task-eval-01"
        self.correlation_id = "corr-eval-01"
        self.work_order_id = new_work_order_id()
        self.execution_id = new_execution_id()

        self.work_order = TesterWorkOrder(
            work_order_id=self.work_order_id,
            manager_task_id=self.task_id,
            project_id=self.project_id,
            correlation_id=self.correlation_id,
            objective="Evaluate runtime application events and requests.",
            test_scope=TestScope(components=["App", "API"], routes=["/", "/api/v1"]),
            authorized_capabilities=[
                TestingCapability.TEST_EXECUTION,
                TestingCapability.BEHAVIOR_OBSERVATION,
            ],
            acceptance_criteria=[
                AcceptanceCriterion(criterion_id="ac-runtime-01", description="Application responds cleanly without 500 errors."),
            ],
        )

        self.execution = TesterExecution.from_work_order(self.work_order, status=TesterExecutionStatus.RUNNING)
        self.execution_id = self.execution.execution_id
        self.evaluator = RuntimeEvaluator(execution=self.execution, work_order=self.work_order)

    def test_01_successful_request(self) -> None:
        """Scenario 1: Normal 200 HTTP response evaluated with INFO severity."""
        obs = self.evaluator.evaluate_http(
            url="https://example.com/api/users",
            method="GET",
            status_code=200,
            timing_ms=45.2,
            is_required=True,
        )

        self.assertEqual(obs.event_type, RuntimeEventType.HTTP_RESPONSE)
        self.assertEqual(obs.severity, RuntimeEventSeverity.INFO)
        self.assertTrue(obs.is_info)
        self.assertFalse(obs.is_failure)
        self.assertEqual(obs.status_code, 200)
        self.assertEqual(obs.method, "GET")
        self.assertIn("HTTP 200", obs.message)
        self.assertEqual(len(self.evaluator.recorded_events), 1)

    def test_02_404_handling(self) -> None:
        """Scenario 2: Differentiates between required and optional 404s."""
        # Optional 404 -> WARNING severity
        opt_obs = self.evaluator.evaluate_http(
            url="https://example.com/optional-icon.png",
            method="GET",
            status_code=404,
            is_required=False,
        )
        self.assertEqual(opt_obs.event_type, RuntimeEventType.HTTP_RESPONSE)
        self.assertEqual(opt_obs.severity, RuntimeEventSeverity.WARNING)
        self.assertTrue(opt_obs.is_warning)
        self.assertFalse(opt_obs.is_failure)

        # Required 404 -> RESOURCE_FAILURE with ERROR severity
        req_obs = self.evaluator.evaluate_http(
            url="https://example.com/main.bundle.js",
            method="GET",
            status_code=404,
            is_required=True,
        )
        self.assertEqual(req_obs.event_type, RuntimeEventType.RESOURCE_FAILURE)
        self.assertEqual(req_obs.severity, RuntimeEventSeverity.ERROR)
        self.assertTrue(req_obs.is_failure)
        self.assertIn("Required resource not found", req_obs.message)

    def test_03_500_server_error(self) -> None:
        """Scenario 3: HTTP 500 server error evaluated with high technical severity (CRITICAL/ERROR)."""
        obs = self.evaluator.evaluate_http(
            url="https://example.com/checkout",
            method="POST",
            status_code=500,
            is_required=True,
        )

        self.assertTrue(obs.is_failure)
        self.assertIn(obs.severity, (RuntimeEventSeverity.ERROR, RuntimeEventSeverity.CRITICAL))
        self.assertIn("500", obs.message)

    def test_04_api_failure(self) -> None:
        """Scenario 4: API endpoint timeout, connection error, or 503 evaluated as API_FAILURE."""
        obs = self.evaluator.evaluate_api(
            endpoint="https://example.com/api/v1/orders",
            method="POST",
            status_code=503,
            error_message="Service Unavailable",
            is_required=True,
        )

        self.assertEqual(obs.event_type, RuntimeEventType.API_FAILURE)
        self.assertEqual(obs.severity, RuntimeEventSeverity.CRITICAL)
        self.assertTrue(obs.is_failure)
        self.assertTrue(obs.is_critical)
        self.assertIn("API server error (HTTP 503)", obs.message)

    def test_05_console_error(self) -> None:
        """Scenario 5: Application-owned console error evaluated as CONSOLE_ERROR (ERROR)."""
        obs = self.evaluator.evaluate_console(
            message="Uncaught TypeError: Cannot read properties of undefined (reading 'map')",
            level="error",
        )

        self.assertEqual(obs.event_type, RuntimeEventType.CONSOLE_ERROR)
        self.assertEqual(obs.severity, RuntimeEventSeverity.ERROR)
        self.assertTrue(obs.is_application_owned)
        self.assertTrue(obs.is_failure)

    def test_06_runtime_exception(self) -> None:
        """Scenario 6: Uncaught runtime exception evaluated as PROCESS_ERROR (ERROR)."""
        obs = self.evaluator.evaluate_process(
            message="ZeroDivisionError: division by zero in /app/handlers/metrics.py:42",
            is_crash=False,
        )

        self.assertEqual(obs.event_type, RuntimeEventType.PROCESS_ERROR)
        self.assertEqual(obs.severity, RuntimeEventSeverity.ERROR)
        self.assertTrue(obs.is_failure)
        self.assertIn("ZeroDivisionError", obs.message)

    def test_07_navigation_failure(self) -> None:
        """Scenario 7: Navigation to unreachable host evaluated as NAVIGATION_FAILURE (ERROR)."""
        obs = self.evaluator.evaluate_navigation(
            url="https://nonexistent.domain.local/dashboard",
            error_message="net::ERR_NAME_NOT_RESOLVED",
        )

        self.assertEqual(obs.event_type, RuntimeEventType.NAVIGATION_FAILURE)
        self.assertEqual(obs.severity, RuntimeEventSeverity.ERROR)
        self.assertTrue(obs.is_failure)
        self.assertIn("Navigation failed", obs.message)

    def test_08_application_crash(self) -> None:
        """Scenario 8: Process crash / exit code != 0 / SIGSEGV evaluated as APPLICATION_CRASH (CRITICAL)."""
        obs = self.evaluator.evaluate_process(
            message="Segmentation fault (core dumped)",
            exit_code=139,
            signal="SIGSEGV",
            is_crash=True,
        )

        self.assertEqual(obs.event_type, RuntimeEventType.APPLICATION_CRASH)
        self.assertEqual(obs.severity, RuntimeEventSeverity.CRITICAL)
        self.assertTrue(obs.is_critical)
        self.assertTrue(obs.is_failure)
        self.assertIn("Application crash detected", obs.message)
        self.assertEqual(obs.metadata.get("exit_code"), 139)
        self.assertEqual(obs.metadata.get("signal"), "SIGSEGV")

    def test_09_irrelevant_external_error_filtering(self) -> None:
        """Scenario 9: Ad blockers, third-party analytics, and extension errors filtered as non-app noise."""
        noise_messages = [
            "Failed to load resource: net::ERR_BLOCKED_BY_CLIENT (https://www.google-analytics.com/analytics.js)",
            "Unchecked runtime.lastError: Could not establish connection. Receiving end does not exist. (chrome-extension://abc)",
            "[Deprecation] Synchronous XMLHttpRequest on the main thread is deprecated.",
        ]

        for msg in noise_messages:
            obs = self.evaluator.evaluate_console(message=msg, level="error")
            self.assertFalse(obs.is_application_owned, f"Expected {msg} to be filtered out as external noise")
            self.assertEqual(obs.severity, RuntimeEventSeverity.INFO)
            self.assertFalse(obs.is_failure)
            self.assertIn("[External Noise Filtered]", obs.message)

        # Ensure failures list does not contain any of the filtered noise
        self.assertEqual(len(self.evaluator.failures), 0)

    def test_10_required_resource_failure(self) -> None:
        """Scenario 10: Missing required asset (CSS, core JS, required image) evaluated as RESOURCE_FAILURE (ERROR)."""
        obs = self.evaluator.evaluate_http(
            url="https://example.com/assets/app.css",
            method="GET",
            status_code=404,
            is_required=True,
        )

        self.assertEqual(obs.event_type, RuntimeEventType.RESOURCE_FAILURE)
        self.assertEqual(obs.severity, RuntimeEventSeverity.ERROR)
        self.assertTrue(obs.is_failure)
        self.assertIn(obs, self.evaluator.failures)

    def test_11_optional_resource_failure(self) -> None:
        """Scenario 11: Missing optional favicon or secondary asset does not trigger test failure."""
        obs = self.evaluator.evaluate_http(
            url="https://example.com/favicon.ico",
            method="GET",
            status_code=404,
            is_required=False,
        )

        self.assertEqual(obs.severity, RuntimeEventSeverity.WARNING)
        self.assertFalse(obs.is_failure)
        self.assertNotIn(obs, self.evaluator.failures)

    def test_12_provenance(self) -> None:
        """Scenario 12: Causal lineage, execution_id, test_case_id, trace, and evidence strictly preserved."""
        ev_id = new_evidence_id()
        tc_id = new_test_case_id()

        obs = self.evaluator.evaluate_http(
            url="https://example.com/api/status",
            method="GET",
            status_code=500,
            test_case_id=tc_id,
            evidence_ids=[ev_id],
        )

        self.assertEqual(obs.execution_id, self.execution_id)
        self.assertEqual(obs.project_id, self.project_id)
        self.assertEqual(obs.test_case_id, tc_id)
        self.assertIn(ev_id, obs.evidence_ids)
        self.assertIn("trace_id", obs.trace)
        self.assertEqual(obs.provenance.get("execution_id"), self.execution_id)
        self.assertEqual(obs.provenance.get("project_id"), self.project_id)

        # Convert to canonical TesterObservation
        canon_obs = obs.to_observation()
        self.assertEqual(canon_obs.execution_id, self.execution_id)
        self.assertEqual(canon_obs.project_id, self.project_id)
        self.assertEqual(canon_obs.test_case_id, tc_id)
        self.assertIn(ev_id, canon_obs.evidence_ids)
        self.assertEqual(canon_obs.observation_type, ObservationType.RUNTIME_STATE)

    def test_13_execution_isolation(self) -> None:
        """Scenario 13: Foreign execution or project IDs rejected with TesterLineageError."""
        foreign_exec_id = new_execution_id()
        foreign_proj_id = "proj-foreign-other"

        with self.assertRaises(TesterLineageError):
            self.evaluator.evaluate_http(
                url="https://example.com/",
                execution_id=foreign_exec_id,
            )

        with self.assertRaises(TesterLineageError):
            self.evaluator.evaluate_console(
                message="Error",
                project_id=foreign_proj_id,
            )

    def test_14_deterministic_behavior(self) -> None:
        """Scenario 14: Repeated evaluation over identical event streams yields identical results."""
        eval1 = RuntimeEvaluator(execution=self.execution, work_order=self.work_order)
        eval2 = RuntimeEvaluator(execution=self.execution, work_order=self.work_order)

        stream = [
            ("http", {"url": "/home", "method": "GET", "status_code": 200, "is_required": True}),
            ("http", {"url": "/assets/missing.css", "method": "GET", "status_code": 404, "is_required": True}),
            ("http", {"url": "/favicon.ico", "method": "GET", "status_code": 404, "is_required": False}),
            ("console", {"message": "TypeError: null", "level": "error"}),
            ("console", {"message": "net::ERR_BLOCKED_BY_CLIENT google-analytics", "level": "error"}),
            ("process", {"message": "Crash", "exit_code": 1, "is_crash": True}),
        ]

        def run_stream(evaluator: RuntimeEvaluator) -> list[RuntimeObservation]:
            out = []
            for kind, kwargs in stream:
                if kind == "http":
                    out.append(evaluator.evaluate_http(**kwargs))
                elif kind == "console":
                    out.append(evaluator.evaluate_console(**kwargs))
                elif kind == "process":
                    out.append(evaluator.evaluate_process(**kwargs))
            return out

        res1 = run_stream(eval1)
        res2 = run_stream(eval2)

        self.assertEqual(len(res1), len(res2))
        for o1, o2 in zip(res1, res2):
            self.assertEqual(o1.event_type, o2.event_type)
            self.assertEqual(o1.severity, o2.severity)
            self.assertEqual(o1.is_application_owned, o2.is_application_owned)
            self.assertEqual(o1.is_failure, o2.is_failure)
            self.assertEqual(o1.status_code, o2.status_code)
            self.assertEqual(o1.message, o2.message)

    def test_15_observation_boundary_defect_creation_rejection(self) -> None:
        """Scenario 15: Calling to_defect() or create_defect() on RuntimeObservation raises TesterBoundaryViolationError."""
        obs = self.evaluator.evaluate_http(
            url="/critical-error",
            method="GET",
            status_code=500,
        )

        with self.assertRaises(TesterBoundaryViolationError) as cm:
            obs.to_defect()
        self.assertEqual(cm.exception.action, "CREATE_DEFECT_FROM_OBSERVATION")

        with self.assertRaises(TesterBoundaryViolationError) as cm2:
            obs.create_defect()
        self.assertEqual(cm2.exception.action, "CREATE_DEFECT_FROM_OBSERVATION")


if __name__ == "__main__":
    unittest.main()
