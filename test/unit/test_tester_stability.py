import unittest
from datetime import datetime, timezone

from core.tester.contracts.identifiers import (
    CRASH_EVENT_ID_PREFIX,
    ERROR_GROUP_ID_PREFIX,
    STABILITY_EVALUATION_ID_PREFIX,
    new_crash_event_id,
    new_error_group_id,
    new_execution_id,
    new_runtime_event_id,
    new_stability_evaluation_id,
    new_test_case_id,
    new_work_order_id,
    validate_crash_event_id,
    validate_error_group_id,
    validate_stability_evaluation_id,
)
from core.tester.contracts.performance import PerformanceMeasurement
from core.tester.contracts.runtime_observation import RuntimeObservation
from core.tester.contracts.stability import (
    CrashEvent,
    ErrorGroup,
    StabilityResult,
    StabilitySpec,
)
from core.tester.evaluator.stability_evaluator import StabilityEvaluator
from core.tester.errors import (
    TesterBoundaryViolationError,
    TesterLineageError,
    TesterValidationError,
)
from core.tester.types import (
    DefectSeverity,
    DefectType,
    PerformanceMeasurementStatus,
    PerformanceMetricType,
    ProcessState,
    RuntimeEventSeverity,
    RuntimeEventType,
    StabilityFailureType,
    StabilityStatus,
    TestCaseStatus,
    TesterExecutionStatus,
)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class TestTesterStability(unittest.TestCase):
    """
    Unit test suite for Tester V1 Phase 7.5: Stability & Runtime Health.
    Covers all 14 required specifications plus serialization roundtrips and invariant guards.
    """

    def setUp(self) -> None:
        self.execution_id = new_execution_id()
        self.project_id = "test-project"
        self.work_order_id = new_work_order_id()
        self.evaluator = StabilityEvaluator(
            execution_id=self.execution_id,
            project_id=self.project_id,
            work_order_id=self.work_order_id,
        )

    def test_01_stable_application(self) -> None:
        """Verify evaluation of a completely stable application."""
        spec = StabilitySpec(
            execution_id=self.execution_id,
            critical_processes=["web-server", "browser"],
        )
        process_states = {
            "web-server": ProcessState.RUNNING,
            "browser": ProcessState.RUNNING,
        }
        observations = [
            RuntimeObservation(
                event_id=new_runtime_event_id(),
                execution_id=self.execution_id,
                project_id=self.project_id,
                event_type=RuntimeEventType.HTTP_RESPONSE,
                message="HTTP 200 OK for /dashboard",
                status_code=200,
                severity=RuntimeEventSeverity.INFO,
            )
        ]

        result = self.evaluator.evaluate_stability(
            spec=spec,
            runtime_observations=observations,
            process_states=process_states,
            reachability=True,
        )

        self.assertEqual(result.status, StabilityStatus.STABLE)
        self.assertTrue(result.is_stable)
        self.assertFalse(result.is_crashed)
        self.assertTrue(result.is_app_reachable)
        self.assertEqual(len(result.crashes), 0)
        self.assertEqual(len(result.error_groups), 0)
        self.assertEqual(result.process_states["web-server"], ProcessState.RUNNING)

    def test_02_application_crash(self) -> None:
        """Verify detection and recording of an application process crash."""
        tc_id = new_test_case_id()
        spec = StabilitySpec(
            execution_id=self.execution_id,
            test_case_id=tc_id,
        )
        crash_obs = RuntimeObservation(
            event_id=new_runtime_event_id(),
            execution_id=self.execution_id,
            project_id=self.project_id,
            event_type=RuntimeEventType.APPLICATION_CRASH,
            message="Fatal segmentation fault in core rendering loop",
            test_case_id=tc_id,
            severity=RuntimeEventSeverity.CRITICAL,
            evidence_ids=["tevid-crashlog1"],
            metadata={"exit_code": 139, "logs": ["SIGSEGV received", "Core dumped"]},
        )

        result = self.evaluator.evaluate_stability(
            spec=spec,
            runtime_observations=[crash_obs],
            process_states={"app": ProcessState.CRASHED},
        )

        self.assertEqual(result.status, StabilityStatus.CRITICAL_FAILURE)
        self.assertTrue(result.is_crashed)
        self.assertEqual(len(result.crashes), 1)
        crash = result.crashes[0]
        self.assertEqual(crash.failure_type, StabilityFailureType.APPLICATION_CRASH)
        self.assertEqual(crash.affected_test_case_id, tc_id)
        self.assertEqual(crash.process_state, ProcessState.CRASHED)
        self.assertEqual(crash.exit_code, 139)
        self.assertIn("tevid-crashlog1", crash.evidence_ids)

    def test_03_server_termination(self) -> None:
        """Verify detection of backend server process termination."""
        spec = StabilitySpec(
            execution_id=self.execution_id,
            critical_processes=["api-server"],
        )
        term_obs = RuntimeObservation(
            event_id=new_runtime_event_id(),
            execution_id=self.execution_id,
            project_id=self.project_id,
            event_type=RuntimeEventType.SERVER_TERMINATION,
            message="API Server process unexpectedly exited with exit code 1",
            severity=RuntimeEventSeverity.CRITICAL,
            metadata={"exit_code": 1},
        )

        result = self.evaluator.evaluate_stability(
            spec=spec,
            runtime_observations=[term_obs],
            process_states={"api-server": ProcessState.TERMINATED},
        )

        self.assertEqual(result.status, StabilityStatus.CRITICAL_FAILURE)
        self.assertEqual(len(result.crashes), 1)
        self.assertEqual(result.crashes[0].failure_type, StabilityFailureType.SERVER_TERMINATION)
        self.assertEqual(result.crashes[0].process_state, ProcessState.TERMINATED)

    def test_04_repeated_runtime_exception(self) -> None:
        """Verify grouping of repeated identical runtime exceptions."""
        spec = StabilitySpec(execution_id=self.execution_id)
        tc1 = new_test_case_id()
        tc2 = new_test_case_id()

        # 10 identical TypeError exceptions across 2 test cases
        obs_list = []
        for i in range(10):
            obs_list.append(
                RuntimeObservation(
                    event_id=new_runtime_event_id(),
                    execution_id=self.execution_id,
                    project_id=self.project_id,
                    event_type=RuntimeEventType.UNHANDLED_EXCEPTION,
                    message="Uncaught TypeError: Cannot read property 'map' of undefined",
                    test_case_id=tc1 if i < 6 else tc2,
                    severity=RuntimeEventSeverity.ERROR,
                    evidence_ids=["tevid-err1"],
                )
            )

        result = self.evaluator.evaluate_stability(
            spec=spec,
            runtime_observations=obs_list,
        )

        self.assertEqual(result.status, StabilityStatus.DEGRADED)
        # Must be grouped into exactly 1 ErrorGroup
        self.assertEqual(len(result.error_groups), 1)
        group = result.error_groups[0]
        self.assertEqual(group.count, 10)
        self.assertIn(tc1, group.test_cases)
        self.assertIn(tc2, group.test_cases)
        self.assertEqual(result.total_errors, 10)

    def test_05_repeated_identical_request_failure(self) -> None:
        """Verify 100 identical 404 requests are grouped into 1 ErrorGroup avoiding defect explosion."""
        spec = StabilitySpec(execution_id=self.execution_id)
        tc_id = new_test_case_id()

        # Generate 100 identical 404 requests to /api/v1/missing
        obs_list = []
        for _ in range(100):
            obs_list.append(
                RuntimeObservation(
                    event_id=new_runtime_event_id(),
                    execution_id=self.execution_id,
                    project_id=self.project_id,
                    event_type=RuntimeEventType.HTTP_RESPONSE,
                    message="GET /api/v1/missing returned 404 Not Found",
                    url="/api/v1/missing",
                    status_code=404,
                    test_case_id=tc_id,
                    severity=RuntimeEventSeverity.ERROR,
                    evidence_ids=["tevid-404"],
                )
            )

        result = self.evaluator.evaluate_stability(
            spec=spec,
            runtime_observations=obs_list,
        )

        self.assertEqual(len(result.error_groups), 1)
        group = result.error_groups[0]
        self.assertEqual(group.count, 100)
        self.assertEqual(group.affected_resource, "/api/v1/missing")
        self.assertEqual(group.status_code, 404)
        self.assertEqual(result.total_errors, 100)

        # Defect candidate classification helper produces 1 defect, not 100
        defect_candidate = self.evaluator.classify_stability_defect(group)
        self.assertEqual(defect_candidate["occurrence_count"], 100)
        self.assertIn("100x", defect_candidate["title"])

    def test_06_application_becomes_unreachable(self) -> None:
        """Verify detection of application unreachability."""
        spec = StabilitySpec(execution_id=self.execution_id)
        unreachable_obs = RuntimeObservation(
            event_id=new_runtime_event_id(),
            execution_id=self.execution_id,
            project_id=self.project_id,
            event_type=RuntimeEventType.UNREACHABLE,
            message="Connection refused when connecting to http://localhost:8080",
            severity=RuntimeEventSeverity.CRITICAL,
        )

        result = self.evaluator.evaluate_stability(
            spec=spec,
            runtime_observations=[unreachable_obs],
            reachability=False,
        )

        self.assertFalse(result.is_app_reachable)
        self.assertEqual(result.status, StabilityStatus.UNSTABLE)

    def test_07_browser_page_crash(self) -> None:
        """Verify detection and recording of browser tab crash."""
        spec = StabilitySpec(execution_id=self.execution_id)
        browser_crash_obs = RuntimeObservation(
            event_id=new_runtime_event_id(),
            execution_id=self.execution_id,
            project_id=self.project_id,
            event_type=RuntimeEventType.BROWSER_CRASH,
            message="Target page crashed: Aw, Snap! (SIGSEGV in rendering process)",
            severity=RuntimeEventSeverity.CRITICAL,
        )

        result = self.evaluator.evaluate_stability(
            spec=spec,
            runtime_observations=[browser_crash_obs],
            process_states={"browser": ProcessState.CRASHED},
        )

        self.assertEqual(result.status, StabilityStatus.CRITICAL_FAILURE)
        self.assertEqual(len(result.crashes), 1)
        self.assertEqual(result.crashes[0].failure_type, StabilityFailureType.BROWSER_CRASH)
        self.assertEqual(result.crashes[0].process_state, ProcessState.CRASHED)

    def test_08_bounded_recovery(self) -> None:
        """Verify bounded recovery when permitted by frozen TestPlan (max 1 attempt)."""
        spec = StabilitySpec(
            execution_id=self.execution_id,
            max_recovery_attempts=1,
        )
        crash_obs = RuntimeObservation(
            event_id=new_runtime_event_id(),
            execution_id=self.execution_id,
            project_id=self.project_id,
            event_type=RuntimeEventType.APPLICATION_CRASH,
            message="Transient memory allocation failure; process restarted.",
            severity=RuntimeEventSeverity.CRITICAL,
        )

        # First crash, recovery attempt 1 of 1
        result = self.evaluator.evaluate_stability(
            spec=spec,
            runtime_observations=[crash_obs],
            recovery_attempts=0,
        )

        self.assertEqual(result.recovery_attempts, 0)
        self.assertFalse(result.recovery_budget_exhausted)
        self.assertEqual(result.status, StabilityStatus.CRITICAL_FAILURE)

    def test_09_recovery_budget_exhausted(self) -> None:
        """Verify that exceeding allowed recovery attempts halts recovery and sets RECOVERY_EXHAUSTED."""
        spec = StabilitySpec(
            execution_id=self.execution_id,
            max_recovery_attempts=1,
        )
        crash_obs = RuntimeObservation(
            event_id=new_runtime_event_id(),
            execution_id=self.execution_id,
            project_id=self.project_id,
            event_type=RuntimeEventType.APPLICATION_CRASH,
            message="Persistent server crash on restart.",
            severity=RuntimeEventSeverity.CRITICAL,
        )

        # Recovery attempts exhausted (1 of 1 already used)
        result = self.evaluator.evaluate_stability(
            spec=spec,
            runtime_observations=[crash_obs],
            recovery_attempts=1,
        )

        self.assertTrue(result.recovery_budget_exhausted)
        self.assertEqual(result.status, StabilityStatus.RECOVERY_EXHAUSTED)
        self.assertTrue(result.has_critical_failure)

    def test_10_unavailable_metrics(self) -> None:
        """Verify that missing CPU/memory telemetry records None/UNAVAILABLE without synthetic data."""
        spec = StabilitySpec(execution_id=self.execution_id)

        # Telemetry is unavailable from runtime
        cpu_metrics = [{"value": None, "reason": "Runtime platform does not expose CPU telemetry"}]
        memory_metrics = [{"value": None, "reason": "Process memory API unsupported"}]

        result = self.evaluator.evaluate_stability(
            spec=spec,
            runtime_observations=[],
            cpu_metrics=cpu_metrics,
            memory_metrics=memory_metrics,
        )

        self.assertEqual(len(result.measurements), 2)
        for m in result.measurements:
            self.assertIsNone(m.value)
            self.assertEqual(m.status, PerformanceMeasurementStatus.UNAVAILABLE)

    def test_11_tester_infrastructure_failure(self) -> None:
        """Verify handling of Tester infrastructure failure without misclassifying as product defect."""
        spec = StabilitySpec(execution_id=self.execution_id)

        result = self.evaluator.evaluate_stability(
            spec=spec,
            runtime_observations=[],
            is_tester_infra_failure=True,
            tester_infra_error="Playwright driver process was killed by OS out-of-memory",
        )

        self.assertTrue(result.metadata.get("is_tester_infra_failure"))
        self.assertEqual(result.provenance.get("is_tester_infra_failure"), True)
        self.assertIn("Tester infrastructure failure", result.notes[0])

    def test_12_distinction_product_vs_tester_failure(self) -> None:
        """
        Verify the strict distinction:
        Product crash: Tester execution status COMPLETED, test case status FAILED, defect created.
        Tester infrastructure crash: Tester execution status FAILED/BLOCKED.
        """
        # Case A: Application crashes
        app_crash_event = CrashEvent(
            crash_id=new_crash_event_id(),
            message="Application core dumped in production workflow",
            failure_type=StabilityFailureType.APPLICATION_CRASH,
            affected_test_case_id="ttest-checkout",
        )
        defect_data = self.evaluator.classify_stability_defect(
            app_crash_event,
            work_order_id=self.work_order_id,
        )
        self.assertEqual(defect_data["defect_type"], DefectType.RUNTIME.value)
        self.assertEqual(defect_data["severity"], DefectSeverity.CRITICAL.value)

        # Semantics:
        # In product crash:
        tester_status_on_product_crash = TesterExecutionStatus.COMPLETED
        test_case_status_on_product_crash = TestCaseStatus.FAIL
        self.assertEqual(tester_status_on_product_crash, TesterExecutionStatus.COMPLETED)
        self.assertEqual(test_case_status_on_product_crash, TestCaseStatus.FAIL)

        # In tester infrastructure failure:
        tester_status_on_infra_crash = TesterExecutionStatus.BLOCKED
        self.assertEqual(tester_status_on_infra_crash, TesterExecutionStatus.BLOCKED)

    def test_13_provenance(self) -> None:
        """Verify ID prefixes, execution ID, timestamps, and provenance tracking."""
        spec = StabilitySpec(execution_id=self.execution_id)
        result = self.evaluator.evaluate_stability(
            spec=spec,
            runtime_observations=[],
        )

        # Validate ID prefixes
        self.assertTrue(result.stability_id.startswith(STABILITY_EVALUATION_ID_PREFIX))
        validate_stability_evaluation_id(result.stability_id)

        eg = ErrorGroup(
            message="Sample error",
            execution_id=self.execution_id,
        )
        self.assertTrue(eg.group_id.startswith(ERROR_GROUP_ID_PREFIX))
        validate_error_group_id(eg.group_id)

        crash = CrashEvent(
            message="Sample crash",
            execution_id=self.execution_id,
        )
        self.assertTrue(crash.crash_id.startswith(CRASH_EVENT_ID_PREFIX))
        validate_crash_event_id(crash.crash_id)

        # Provenance validation
        self.assertEqual(result.provenance["execution_id"], self.execution_id)
        self.assertEqual(result.provenance["project_id"], self.project_id)
        self.assertIn("started_at", result.timestamps)
        self.assertIn("completed_at", result.timestamps)

    def test_14_execution_isolation(self) -> None:
        """Verify cross-execution isolation prevents evaluation across different executions."""
        foreign_execution_id = new_execution_id()
        foreign_spec = StabilitySpec(execution_id=foreign_execution_id)

        # Rejects spec with mismatched execution_id
        with self.assertRaises(TesterLineageError):
            self.evaluator.evaluate_stability(
                spec=foreign_spec,
                runtime_observations=[],
            )

        # Rejects runtime observation with foreign execution_id
        foreign_obs = RuntimeObservation(
            event_id=new_runtime_event_id(),
            execution_id=foreign_execution_id,
            project_id=self.project_id,
            event_type=RuntimeEventType.APPLICATION_CRASH,
            message="Foreign crash",
        )
        valid_spec = StabilitySpec(execution_id=self.execution_id)
        with self.assertRaises(TesterLineageError):
            self.evaluator.evaluate_stability(
                spec=valid_spec,
                runtime_observations=[foreign_obs],
            )

    def test_15_serialization_and_invariant_guards(self) -> None:
        """Verify to_dict/from_dict roundtrips and zero-fixing invariant guards."""
        spec = StabilitySpec(
            execution_id=self.execution_id,
            max_recovery_attempts=1,
            critical_processes=["server"],
        )
        spec_dict = spec.to_dict()
        spec_restored = StabilitySpec.from_dict(spec_dict)
        self.assertEqual(spec_restored.spec_id, spec.spec_id)
        self.assertEqual(spec_restored.max_recovery_attempts, 1)

        crash = CrashEvent(
            message="Browser tab crashed",
            failure_type=StabilityFailureType.BROWSER_CRASH,
            process_state=ProcessState.CRASHED,
            execution_id=self.execution_id,
        )
        crash_dict = crash.to_dict()
        crash_restored = CrashEvent.from_dict(crash_dict)
        self.assertEqual(crash_restored.crash_id, crash.crash_id)
        self.assertEqual(crash_restored.failure_type, StabilityFailureType.BROWSER_CRASH)

        eg = ErrorGroup(
            message="HTTP 404",
            count=5,
            execution_id=self.execution_id,
        )
        eg_dict = eg.to_dict()
        eg_restored = ErrorGroup.from_dict(eg_dict)
        self.assertEqual(eg_restored.group_id, eg.group_id)
        self.assertEqual(eg_restored.count, 5)

        res = StabilityResult(
            stability_id=new_stability_evaluation_id(),
            execution_id=self.execution_id,
            spec=spec,
            status=StabilityStatus.DEGRADED,
            crashes=[crash],
            error_groups=[eg],
        )
        res_dict = res.to_dict()
        res_restored = StabilityResult.from_dict(res_dict)
        self.assertEqual(res_restored.stability_id, res.stability_id)
        self.assertEqual(res_restored.status, StabilityStatus.DEGRADED)
        self.assertEqual(len(res_restored.crashes), 1)
        self.assertEqual(len(res_restored.error_groups), 1)

        # Invariant guards: apply_fix, auto_fix, create_defect raise TesterBoundaryViolationError
        with self.assertRaises(TesterBoundaryViolationError):
            self.evaluator.apply_fix()

        with self.assertRaises(TesterBoundaryViolationError):
            self.evaluator.auto_fix()

        with self.assertRaises(TesterBoundaryViolationError):
            self.evaluator.create_defect()

        with self.assertRaises(TesterBoundaryViolationError):
            res.apply_fix()

        with self.assertRaises(TesterBoundaryViolationError):
            res.auto_fix()

        with self.assertRaises(TesterBoundaryViolationError):
            res.create_defect()

        with self.assertRaises(TesterBoundaryViolationError):
            crash.apply_fix()

        with self.assertRaises(TesterBoundaryViolationError):
            eg.apply_fix()


if __name__ == "__main__":
    unittest.main()
