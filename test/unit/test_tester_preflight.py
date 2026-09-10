from __future__ import annotations

import unittest
from typing import Any, Optional

from core.events.model import Event
from core.events.types import EventType
from core.tester import (
    AcceptanceCriterion,
    ApplicationHealthStatus,
    BuildStatus,
    MockBrowserSession,
    MockTestRuntime,
    NavigationResult,
    PreflightDecision,
    PreflightStatus,
    ResourceCheckResult,
    RouteCheckResult,
    TestEnvironment,
    TestPlan,
    TestPreflightEvaluator,
    TestPreflightResult,
    TestRuntimeStatus,
    TestScope,
    TestCase,
    TestStep,
    TesterActionType,
    TesterBlocker,
    TesterBlockerCategory,
    TesterBlockerSeverity,
    TesterBoundaryViolationError,
    TesterExecution,
    TesterExecutionStatus,
    TesterLineageError,
    TesterValidationError,
    TesterWorkOrder,
    TestingCapability,
    new_environment_id,
    new_execution_id,
    new_plan_id,
    new_preflight_id,
    new_test_case_id,
    new_work_order_id,
)


class TestTesterPreflight(unittest.TestCase):
    """
    Unit test suite for Phase 5.1: Test Preflight & Application Health.
    
    Verifies 17 deterministic preflight scenarios:
    1. Healthy application -> READY, PASS, HEALTHY
    2. Startup failure -> FAILED, STARTUP_FAILED
    3. Build failure -> FAILED, BuildStatus.FAILED
    4. Compilation error in terminal output -> FAILED
    5. Runtime exception in terminal output -> FAILED, UNHEALTHY
    6. Repeated 404s on required resources -> FAILED
    7. HTTP 500 error response on route check -> FAILED, UNHEALTHY (Product failure)
    8. Unreachable application / connection refused -> FAILED, UNREACHABLE
    9. Valid expected 404 -> PASS, READY
    10. Warnings only -> READY_WITH_WARNINGS, WARNINGS
    11. Blocked environment / credentials -> BLOCKED
    12. Timeout / hang detection -> FAILED
    13. Terminal output isolation (scoped to active session)
    14. Command authorization enforcement (unauthorized commands rejected)
    15. Project and execution lineage isolation
    16. Zero-fixing guard (TesterBoundaryViolationError on fixes)
    17. Evidence, trace lineage, and serialization round-trip
    """

    def setUp(self) -> None:
        self.project_id = "proj-preflight-unit"
        self.task_id = "task-preflight-01"
        self.correlation_id = "corr-preflight-01"
        self.work_order_id = new_work_order_id()
        self.execution_id = new_execution_id()
        self.evaluator = TestPreflightEvaluator()

    def _make_work_order(self, **kwargs) -> TesterWorkOrder:
        defaults = {
            "work_order_id": self.work_order_id,
            "manager_task_id": self.task_id,
            "project_id": self.project_id,
            "correlation_id": self.correlation_id,
            "objective": "Verify application health and readiness prior to Phase 5 execution.",
            "test_scope": TestScope(
                components=["AppRoot"],
                routes=["/", "/health"],
                environments=["staging"],
            ),
            "authorized_capabilities": [
                TestingCapability.TEST_EXECUTION,
                TestingCapability.NAVIGATE,
                TestingCapability.SCREENSHOT,
            ],
            "acceptance_criteria": [
                AcceptanceCriterion(criterion_id="ac-health-01", description="Application root responds with 200."),
            ],
        }
        defaults.update(kwargs)
        return TesterWorkOrder(**defaults)

    def _make_execution(self, work_order: TesterWorkOrder) -> TesterExecution:
        return TesterExecution.from_work_order(work_order, status=TesterExecutionStatus.RUNNING)

    def test_01_healthy_application_preflight_ready(self) -> None:
        """Scenario 1: Clean application with reachable base URL and 200 routes is READY."""
        wo = self._make_work_order()
        execution = self._make_execution(wo)

        result = self.evaluator.evaluate_preflight(
            execution=execution,
            work_order=wo,
            routes=[{"route": "/", "status_code": 200, "is_reachable": True}],
        )

        self.assertEqual(result.status, PreflightStatus.PASS)
        self.assertEqual(result.decision, PreflightDecision.READY)
        self.assertEqual(result.application_status, ApplicationHealthStatus.HEALTHY)
        self.assertTrue(result.is_ready)
        self.assertFalse(result.is_ready_with_warnings)
        self.assertFalse(result.is_failed)
        self.assertFalse(result.is_blocked)
        self.assertEqual(execution.preflight_result, result)
        self.assertTrue(len(execution.traces) > 0)

    def test_02_startup_failure_detection(self) -> None:
        """Scenario 2: Runtime/process startup failure produces STARTUP_FAILED and decision FAILED."""
        wo = self._make_work_order()
        execution = self._make_execution(wo)
        runtime = MockTestRuntime(
            execution=execution,
            work_order=wo,
            simulate_startup_failure=True,
            simulate_failure_reason="WebDriver connection refused on port 9222",
        )

        result = self.evaluator.evaluate_preflight(
            execution=execution,
            work_order=wo,
            runtime=runtime,
        )

        self.assertEqual(result.status, PreflightStatus.FAILED)
        self.assertEqual(result.decision, PreflightDecision.FAILED)
        self.assertEqual(result.application_status, ApplicationHealthStatus.STARTUP_FAILED)
        self.assertTrue(result.is_failed)
        self.assertFalse(result.is_ready)
        self.assertTrue(any("startup failed" in o.description.lower() for o in result.error_observations))

    def test_03_build_failure_detection(self) -> None:
        """Scenario 3: Authorized build command failure produces BuildStatus.FAILED and decision FAILED."""
        wo = self._make_work_order(metadata={"authorized_commands": ["npm run build"]})
        execution = self._make_execution(wo)

        def mock_failing_builder(cmd: str) -> tuple[int, str, str]:
            return 1, "", "Error: Cannot find module '@app/theme'\nBuild failed with exit code 1"

        result = self.evaluator.evaluate_preflight(
            execution=execution,
            work_order=wo,
            build_command="npm run build",
            build_runner=mock_failing_builder,
        )

        self.assertEqual(result.build_status, BuildStatus.FAILED)
        self.assertEqual(result.status, PreflightStatus.FAILED)
        self.assertEqual(result.decision, PreflightDecision.FAILED)
        self.assertTrue(result.is_failed)
        self.assertTrue(len(result.error_observations) > 0)

    def test_04_compilation_error_in_terminal_output(self) -> None:
        """Scenario 4: Compilation errors detected in terminal logs trigger FAILED status."""
        wo = self._make_work_order()
        execution = self._make_execution(wo)
        terminal_logs = (
            "[webpack] Compiling...\n"
            "TypeScript error TS2304: Cannot find name 'UserProfile'.\n"
            "src/components/User.tsx(14,22): error TS2304: Cannot find name 'UserProfile'."
        )

        result = self.evaluator.evaluate_preflight(
            execution=execution,
            work_order=wo,
            terminal_output=terminal_logs,
        )

        self.assertEqual(result.build_status, BuildStatus.FAILED)
        self.assertEqual(result.status, PreflightStatus.FAILED)
        self.assertEqual(result.decision, PreflightDecision.FAILED)
        self.assertTrue(any("compilation error" in o.description.lower() for o in result.error_observations))

    def test_05_runtime_exception_in_terminal_output(self) -> None:
        """Scenario 5: Uncaught exception in process output triggers UNHEALTHY and FAILED."""
        wo = self._make_work_order()
        execution = self._make_execution(wo)
        terminal_logs = (
            "Server listening on port 3000\n"
            "Traceback (most recent call last):\n"
            "  File 'server.py', line 120, in handle_request\n"
            "    db.connect()\n"
            "ConnectionError: Failed to connect to primary cluster"
        )

        result = self.evaluator.evaluate_preflight(
            execution=execution,
            work_order=wo,
            terminal_output=terminal_logs,
        )

        self.assertEqual(result.application_status, ApplicationHealthStatus.UNHEALTHY)
        self.assertEqual(result.status, PreflightStatus.FAILED)
        self.assertEqual(result.decision, PreflightDecision.FAILED)

    def test_06_repeated_404_on_required_resource(self) -> None:
        """Scenario 6: Repeated 404 failures on critical assets fail preflight."""
        wo = self._make_work_order()
        execution = self._make_execution(wo)
        resources = [
            ResourceCheckResult(
                resource_url="/dist/bundle.main.js",
                resource_type="script",
                status_code=404,
                is_available=False,
                is_required=True,
                failure_count=2,
            )
        ]

        result = self.evaluator.evaluate_preflight(
            execution=execution,
            work_order=wo,
            resources=resources,
        )

        self.assertEqual(result.status, PreflightStatus.FAILED)
        self.assertEqual(result.decision, PreflightDecision.FAILED)
        self.assertEqual(result.application_status, ApplicationHealthStatus.UNHEALTHY)

    def test_07_http_500_response_on_route(self) -> None:
        """Scenario 7: HTTP 500 error on required route discovers product failure and fails preflight."""
        wo = self._make_work_order()
        execution = self._make_execution(wo)

        def mock_app(path: str) -> dict[str, Any]:
            if path == "/health":
                return {"status_code": 500, "is_reachable": True, "error_message": "Internal Server Error"}
            return {"status_code": 200, "is_reachable": True}

        result = self.evaluator.evaluate_preflight(
            execution=execution,
            work_order=wo,
            routes=["/", "/health"],
            mock_app_checker=mock_app,
        )

        self.assertEqual(result.status, PreflightStatus.FAILED)
        self.assertEqual(result.decision, PreflightDecision.FAILED)
        self.assertEqual(result.application_status, ApplicationHealthStatus.UNHEALTHY)
        self.assertTrue(any(not r.is_healthy for r in result.route_checks))

    def test_08_unreachable_application_connection_refused(self) -> None:
        """Scenario 8: Unreachable application / connection refused marks UNREACHABLE."""
        wo = self._make_work_order()
        execution = self._make_execution(wo)

        def mock_app(path: str) -> dict[str, Any]:
            return {"is_reachable": False, "error_message": "Connection refused (ECONNREFUSED)"}

        result = self.evaluator.evaluate_preflight(
            execution=execution,
            work_order=wo,
            routes=["/"],
            mock_app_checker=mock_app,
        )

        self.assertEqual(result.status, PreflightStatus.FAILED)
        self.assertEqual(result.decision, PreflightDecision.FAILED)
        self.assertEqual(result.application_status, ApplicationHealthStatus.UNREACHABLE)

    def test_09_valid_expected_404_route(self) -> None:
        """Scenario 9: Route with expected 404 status does NOT fail preflight."""
        wo = self._make_work_order()
        execution = self._make_execution(wo)

        routes = [
            RouteCheckResult(
                route="/nonexistent-endpoint",
                status_code=404,
                is_reachable=True,
                is_healthy=True,
                expected_status_code=404,
                is_required=False,
            )
        ]

        result = self.evaluator.evaluate_preflight(
            execution=execution,
            work_order=wo,
            routes=routes,
        )

        self.assertEqual(result.status, PreflightStatus.PASS)
        self.assertEqual(result.decision, PreflightDecision.READY)
        self.assertTrue(result.route_checks[0].is_healthy)

    def test_10_warnings_only_preflight_ready_with_warnings(self) -> None:
        """Scenario 10: Non-fatal warnings and single non-critical 404 produce READY_WITH_WARNINGS."""
        wo = self._make_work_order()
        execution = self._make_execution(wo)
        terminal_logs = "Warning: DeprecationWarning: react-router v5 is deprecated\n[WARN] High memory usage detected"
        resources = [
            ResourceCheckResult(
                resource_url="/favicon.ico",
                is_available=False,
                status_code=404,
                failure_count=1,
                is_required=False,
            )
        ]

        result = self.evaluator.evaluate_preflight(
            execution=execution,
            work_order=wo,
            routes=[{"route": "/", "status_code": 200, "is_reachable": True}],
            resources=resources,
            terminal_output=terminal_logs,
        )

        self.assertEqual(result.status, PreflightStatus.WARNINGS)
        self.assertEqual(result.decision, PreflightDecision.READY_WITH_WARNINGS)
        self.assertTrue(result.is_ready)
        self.assertTrue(result.is_ready_with_warnings)
        self.assertFalse(result.is_failed)
        self.assertTrue(len(result.warnings) > 0)

    def test_11_blocked_environment(self) -> None:
        """Scenario 11: Missing mandatory credentials or active blocker results in BLOCKED decision."""
        wo = self._make_work_order()
        execution = self._make_execution(wo)
        execution.block(
            reason="Missing staging authentication database credentials",
            category=TesterBlockerCategory.ENVIRONMENT,
            severity=TesterBlockerSeverity.HIGH,
        )

        result = self.evaluator.evaluate_preflight(
            execution=execution,
            work_order=wo,
        )

        self.assertEqual(result.status, PreflightStatus.BLOCKED)
        self.assertEqual(result.decision, PreflightDecision.BLOCKED)
        self.assertTrue(result.is_blocked)
        self.assertFalse(result.is_ready)
        self.assertTrue(len(result.blockers) > 0)

    def test_12_timeout_or_hang_detection(self) -> None:
        """Scenario 12: Route check or server hang produces FAILED decision."""
        wo = self._make_work_order()
        execution = self._make_execution(wo)

        def hanging_mock(path: str) -> dict[str, Any]:
            return {"is_reachable": False, "error_message": "Navigation timeout exceeded: 30000ms"}

        result = self.evaluator.evaluate_preflight(
            execution=execution,
            work_order=wo,
            routes=["/login"],
            mock_app_checker=hanging_mock,
        )

        self.assertEqual(result.status, PreflightStatus.FAILED)
        self.assertEqual(result.decision, PreflightDecision.FAILED)
        self.assertEqual(result.application_status, ApplicationHealthStatus.UNREACHABLE)

    def test_13_terminal_output_isolation(self) -> None:
        """Scenario 13: Terminal inspection evaluates strictly isolated buffer from active session."""
        wo = self._make_work_order()
        execution = self._make_execution(wo)

        # Buffer with clean logs
        clean_buffer = "Application initialized on port 8080\nReady for connections"
        result = self.evaluator.evaluate_preflight(
            execution=execution,
            work_order=wo,
            routes=[{"route": "/", "status_code": 200, "is_reachable": True}],
            terminal_output=clean_buffer,
        )
        self.assertEqual(result.status, PreflightStatus.PASS)
        self.assertEqual(len(result.error_observations), 0)

    def test_14_command_authorization_rejection(self) -> None:
        """Scenario 14: Unauthorized build commands are strictly rejected with TesterBoundaryViolationError."""
        wo = self._make_work_order(metadata={"authorized_commands": ["npm run build"]})
        execution = self._make_execution(wo)

        with self.assertRaises(TesterBoundaryViolationError):
            self.evaluator.evaluate_preflight(
                execution=execution,
                work_order=wo,
                build_command="sudo rm -rf / && touch hacked.txt",
            )

    def test_15_project_and_execution_lineage_isolation(self) -> None:
        """Scenario 15: Cross-project or mismatched lineage raises TesterLineageError."""
        wo = self._make_work_order(project_id="proj-alpha")
        execution = self._make_execution(wo)
        execution.project_id = "proj-bravo"  # Tampered lineage

        with self.assertRaises(TesterLineageError):
            self.evaluator.evaluate_preflight(execution=execution, work_order=wo)

        # Mismatched runtime lineage
        wo2 = self._make_work_order()
        exec2 = self._make_execution(wo2)
        exec3 = self._make_execution(wo2)
        mismatched_runtime = MockTestRuntime(execution=exec3, work_order=wo2)

        with self.assertRaises(TesterLineageError):
            self.evaluator.evaluate_preflight(execution=exec2, work_order=wo2, runtime=mismatched_runtime)

    def test_16_zero_fixing_invariant(self) -> None:
        """Scenario 16: Zero-fixing guard: TestPreflightResult strictly forbids automated fixes."""
        wo = self._make_work_order()
        execution = self._make_execution(wo)

        result = self.evaluator.evaluate_preflight(
            execution=execution,
            work_order=wo,
            routes=[{"route": "/", "status_code": 200, "is_reachable": True}],
        )

        with self.assertRaises(TesterBoundaryViolationError):
            result.apply_fix()

        with self.assertRaises(TesterBoundaryViolationError):
            result.repair_source()

        with self.assertRaises(TesterBoundaryViolationError):
            result.install_dependency()

        with self.assertRaises(TesterBoundaryViolationError):
            result.repair_assets()

    def test_17_evidence_trace_and_serialization(self) -> None:
        """Scenario 17: Trace generation, execution attachment, and dictionary serialization round-trip."""
        wo = self._make_work_order()
        execution = self._make_execution(wo)

        events_emitted: list[Event] = []
        def sink(ev: Event) -> None:
            events_emitted.append(ev)

        result = self.evaluator.evaluate_preflight(
            execution=execution,
            work_order=wo,
            routes=[{"route": "/", "status_code": 200, "is_reachable": True}],
            event_sink=sink,
        )

        # Trace and attachment
        self.assertIsNotNone(execution.preflight_result)
        self.assertEqual(execution.preflight_result.preflight_id, result.preflight_id)
        self.assertTrue(len(result.evidence_ids) > 0)
        self.assertTrue(any(t.action_type == TesterActionType.PREFLIGHT_CHECK for t in execution.traces))

        # Event emission sequence
        event_types = [e.event_type for e in events_emitted]
        self.assertIn(EventType.TEST_PREFLIGHT_STARTED, event_types)
        self.assertIn(EventType.TEST_PREFLIGHT_COMPLETED, event_types)

        # Preflight serialization round-trip
        data = result.to_dict()
        restored = TestPreflightResult.from_dict(data)
        self.assertEqual(restored.preflight_id, result.preflight_id)
        self.assertEqual(restored.decision, result.decision)
        self.assertEqual(restored.status, result.status)
        self.assertEqual(restored.application_status, result.application_status)

        # Execution serialization round-trip
        exec_data = execution.to_dict()
        self.assertIn("preflight_result", exec_data)
        restored_exec = TesterExecution.from_dict(exec_data)
        self.assertIsNotNone(restored_exec.preflight_result)
        self.assertEqual(restored_exec.preflight_result.preflight_id, result.preflight_id)


if __name__ == "__main__":
    unittest.main()
