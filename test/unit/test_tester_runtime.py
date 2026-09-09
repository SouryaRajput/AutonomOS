from __future__ import annotations

import unittest
from typing import Any, List

from core.events.model import Event
from core.events.types import EventSource, EventType
from core.tester.contracts.boundary import TESTER_ALLOWED_CAPABILITIES
from core.tester.contracts.criteria import AcceptanceCriterion
from core.tester.contracts.environment import TestEnvironment
from core.tester.contracts.execution import TesterExecution
from core.tester.contracts.identifiers import (
    new_environment_id,
    new_execution_id,
    new_runtime_id,
    new_work_order_id,
    validate_environment_id,
    validate_runtime_id,
)
from core.tester.contracts.runtime import MockTestRuntime, TestRuntime
from core.tester.contracts.runtime_config import TestRuntimeConfig
from core.tester.contracts.runtime_lifecycle import TestRuntimeLifecycle
from core.tester.contracts.scope import TestScope
from core.tester.contracts.work_order import TesterWorkOrder
from core.tester.errors import (
    InvalidTesterTransitionError,
    TesterBoundaryViolationError,
    TesterError,
    TesterLineageError,
    TesterValidationError,
)
from core.tester.types import (
    EnvironmentType,
    TestCategory,
    TestRuntimeStatus,
    TestingCapability,
)


class TestTesterRuntime(unittest.TestCase):
    """
    Deterministic unit test suite for Tester V1 Phase 2.1:
    Controlled Test Runtime Foundation.
    """

    def setUp(self) -> None:
        self.project_id = "proj-test-rt-01"
        self.work_order_id = new_work_order_id()
        self.execution_id = new_execution_id()
        self.emitted_events: List[Event] = []

    def _event_sink(self, event: Event) -> None:
        self.emitted_events.append(event)

    def _create_sample_work_order(
        self,
        work_order_id: str | None = None,
        project_id: str | None = None,
        authorized_capabilities: list[TestingCapability | str] | None = None,
    ) -> TesterWorkOrder:
        return TesterWorkOrder(
            work_order_id=work_order_id or self.work_order_id,
            manager_task_id="mtask-test-rt-01",
            project_id=project_id or self.project_id,
            correlation_id="corr-test-rt-01",
            objective="Verify checkout flow accessibility and behavior",
            instructions=["Navigate to checkout and assert elements"],
            product_artifact="checkout-service:1.0.0",
            test_scope=TestScope(routes=["/checkout"], features=["payment"]),
            test_categories=[TestCategory.FUNCTIONAL],
            authorized_capabilities=(
                authorized_capabilities
                if authorized_capabilities is not None
                else [
                    TestingCapability.NAVIGATE,
                    TestingCapability.CLICK,
                    TestingCapability.TYPE,
                    TestingCapability.SCROLL,
                ]
            ),
            acceptance_criteria=[
                AcceptanceCriterion(
                    criterion_id="ac-test-rt-01",
                    description="Checkout page loads and elements are functional",
                )
            ],
        )

    def _create_sample_execution(
        self,
        execution_id: str | None = None,
        work_order_id: str | None = None,
        project_id: str | None = None,
    ) -> TesterExecution:
        return TesterExecution(
            execution_id=execution_id or self.execution_id,
            work_order_id=work_order_id or self.work_order_id,
            task_id="mtask-test-rt-01",
            project_id=project_id or self.project_id,
            correlation_id="corr-test-rt-01",
        )

    # ----------------------------------------------------------------------
    # 1. Environment Creation and Validation
    # ----------------------------------------------------------------------
    def test_test_environment_creation_and_validation(self) -> None:
        """Test environment initialization, default values, custom overrides, and validation."""
        # Defaults
        env = TestEnvironment(
            project_id=self.project_id,
            work_order_id=self.work_order_id,
            execution_id=self.execution_id,
        )
        self.assertTrue(env.environment_id.startswith("tenv-"))
        validate_environment_id(env.environment_id)
        self.assertEqual(env.environment_type, EnvironmentType.BROWSER)
        self.assertEqual(env.viewport["width"], 1280)
        self.assertEqual(env.viewport["height"], 720)
        self.assertEqual(env.env_name, "staging")
        env.validate()

        # Custom config
        custom_env = TestEnvironment(
            environment_id="tenv-custom-01",
            project_id=self.project_id,
            work_order_id=self.work_order_id,
            execution_id=self.execution_id,
            environment_type=EnvironmentType.LOCAL_APP,
            application_url="http://localhost:8080",
            viewport={"width": 1920, "height": 1080},
            env_name="local_dev",
            configuration={"browser_locale": "en-US"},
        )
        custom_env.validate()
        self.assertEqual(custom_env.environment_id, "tenv-custom-01")
        self.assertEqual(custom_env.application_url, "http://localhost:8080")
        self.assertEqual(custom_env.viewport["width"], 1920)

        # Invalid viewport width
        invalid_viewport_env = TestEnvironment(
            project_id=self.project_id,
            work_order_id=self.work_order_id,
            execution_id=self.execution_id,
            viewport={"width": 100, "height": 720},  # < 320
        )
        with self.assertRaises(TesterValidationError):
            invalid_viewport_env.validate()

        # Secret rejection in variables
        secret_env = TestEnvironment(
            project_id=self.project_id,
            work_order_id=self.work_order_id,
            execution_id=self.execution_id,
            variables={"admin_password": "supersecretpassword123"},
        )
        with self.assertRaises(TesterValidationError):
            secret_env.validate()

        # Secret rejection in configuration
        secret_config_env = TestEnvironment(
            project_id=self.project_id,
            work_order_id=self.work_order_id,
            execution_id=self.execution_id,
            configuration={"auth_token": "bearer-xyz123"},
        )
        with self.assertRaises(TesterValidationError):
            secret_config_env.validate()

    # ----------------------------------------------------------------------
    # 2. Runtime Creation and Initial State
    # ----------------------------------------------------------------------
    def test_runtime_creation_and_initial_state(self) -> None:
        """Verify TestRuntime creates in CREATED status, validates lineage, and emits event."""
        wo = self._create_sample_work_order()
        execution = self._create_sample_execution()

        runtime = MockTestRuntime(
            execution=execution,
            work_order=wo,
            event_sink=self._event_sink,
        )

        self.assertEqual(runtime.status, TestRuntimeStatus.CREATED)
        self.assertTrue(runtime.runtime_id.startswith("trun-"))
        validate_runtime_id(runtime.runtime_id)
        self.assertEqual(runtime.project_id, self.project_id)
        self.assertEqual(runtime.work_order_id, self.work_order_id)
        self.assertEqual(runtime.execution_id, self.execution_id)
        self.assertFalse(runtime.is_started)
        self.assertFalse(runtime.is_stopped)

        # Check CREATED event was emitted
        self.assertEqual(len(self.emitted_events), 1)
        created_event = self.emitted_events[0]
        self.assertEqual(created_event.event_type, EventType.TEST_RUNTIME_CREATED)
        self.assertEqual(created_event.payload["runtime_id"], runtime.runtime_id)
        self.assertEqual(created_event.payload["status"], TestRuntimeStatus.CREATED.value)

    # ----------------------------------------------------------------------
    # 3. Normal Lifecycle Progression
    # ----------------------------------------------------------------------
    def test_runtime_lifecycle_normal_progression(self) -> None:
        """Verify CREATED -> STARTING -> READY -> RUNNING -> STOPPING -> STOPPED."""
        wo = self._create_sample_work_order()
        execution = self._create_sample_execution()

        runtime = MockTestRuntime(
            execution=execution,
            work_order=wo,
            event_sink=self._event_sink,
        )

        # 1. Start: CREATED -> STARTING -> READY
        runtime.start()
        self.assertEqual(runtime.status, TestRuntimeStatus.READY)
        self.assertTrue(runtime.is_started)
        self.assertIsNotNone(runtime.started_at)
        self.assertIsNone(runtime.stopped_at)

        # 2. Run: READY -> RUNNING
        runtime.run()
        self.assertEqual(runtime.status, TestRuntimeStatus.RUNNING)

        # 3. Stop: RUNNING -> STOPPING -> STOPPED
        runtime.stop(reason="Test execution finished successfully")
        self.assertEqual(runtime.status, TestRuntimeStatus.STOPPED)
        self.assertTrue(runtime.is_stopped)
        self.assertIsNotNone(runtime.stopped_at)

        # Verify event sequence
        event_types = [e.event_type for e in self.emitted_events]
        self.assertEqual(
            event_types,
            [
                EventType.TEST_RUNTIME_CREATED,
                EventType.TEST_RUNTIME_STARTING,
                EventType.TEST_RUNTIME_READY,
                EventType.TEST_RUNTIME_STARTED,
                EventType.TEST_RUNTIME_STOPPING,
                EventType.TEST_RUNTIME_STOPPED,
            ],
        )

    # ----------------------------------------------------------------------
    # 4. Invalid Lifecycle Transitions Rejected
    # ----------------------------------------------------------------------
    def test_invalid_lifecycle_transitions_rejected(self) -> None:
        """Verify that skipping states or invalid backward transitions raise InvalidTesterTransitionError."""
        wo = self._create_sample_work_order()
        execution = self._create_sample_execution()

        runtime = MockTestRuntime(execution=execution, work_order=wo)

        # Cannot skip directly from CREATED to RUNNING
        with self.assertRaises(InvalidTesterTransitionError):
            runtime.transition_to(TestRuntimeStatus.RUNNING)

        # Cannot skip directly from CREATED to STOPPING
        with self.assertRaises(InvalidTesterTransitionError):
            runtime.transition_to(TestRuntimeStatus.STOPPING)

        # Start to READY
        runtime.start()
        self.assertEqual(runtime.status, TestRuntimeStatus.READY)

        # Cannot go back from READY to STARTING
        with self.assertRaises(InvalidTesterTransitionError):
            runtime.transition_to(TestRuntimeStatus.STARTING)

        # Cannot go back from READY to CREATED
        with self.assertRaises(InvalidTesterTransitionError):
            runtime.transition_to(TestRuntimeStatus.CREATED)

    # ----------------------------------------------------------------------
    # 5. Work Order and Execution Lineage
    # ----------------------------------------------------------------------
    def test_work_order_and_execution_lineage(self) -> None:
        """Verify mismatched work_order_id or execution_id raises TesterLineageError."""
        wo = self._create_sample_work_order()
        mismatched_execution = TesterExecution(
            execution_id=new_execution_id(),
            work_order_id="two-other-order-99",  # mismatch
            task_id="mtask-test-rt-01",
            project_id=self.project_id,
            correlation_id="corr-test-rt-01",
        )

        with self.assertRaises(TesterLineageError):
            MockTestRuntime(execution=mismatched_execution, work_order=wo)

        # Mismatched environment work_order_id
        valid_execution = self._create_sample_execution()
        mismatched_env = TestEnvironment(
            project_id=self.project_id,
            work_order_id="two-different-env-wo",
            execution_id=valid_execution.execution_id,
        )
        with self.assertRaises(TesterLineageError):
            MockTestRuntime(
                execution=valid_execution,
                work_order=wo,
                environment=mismatched_env,
            )

        # Mismatched environment execution_id
        mismatched_exec_env = TestEnvironment(
            project_id=self.project_id,
            work_order_id=wo.work_order_id,
            execution_id="texec-different-exec",
        )
        with self.assertRaises(TesterLineageError):
            MockTestRuntime(
                execution=valid_execution,
                work_order=wo,
                environment=mismatched_exec_env,
            )

    # ----------------------------------------------------------------------
    # 6. Project Isolation Enforcement
    # ----------------------------------------------------------------------
    def test_project_isolation_enforcement(self) -> None:
        """Verify that cross-project execution or work orders are strictly rejected."""
        wo = self._create_sample_work_order(project_id="proj-alpha")
        exec_beta = self._create_sample_execution(project_id="proj-beta")

        with self.assertRaises(TesterLineageError):
            MockTestRuntime(execution=exec_beta, work_order=wo)

        # Mismatched environment project_id
        exec_alpha = self._create_sample_execution(project_id="proj-alpha")
        mismatched_env = TestEnvironment(
            project_id="proj-gamma",
            work_order_id=wo.work_order_id,
            execution_id=exec_alpha.execution_id,
        )
        with self.assertRaises(TesterLineageError):
            MockTestRuntime(
                execution=exec_alpha,
                work_order=wo,
                environment=mismatched_env,
            )

    # ----------------------------------------------------------------------
    # 7. Effective Capability 3-Way Intersection
    # ----------------------------------------------------------------------
    def test_effective_capability_three_way_intersection(self) -> None:
        """
        Verify:
        Effective = Global Tester Boundary ∩ WorkOrder Authorized Capabilities ∩ Runtime Supported Capabilities
        """
        # Case A: Full intersection within boundary
        wo = self._create_sample_work_order(
            authorized_capabilities=[
                TestingCapability.NAVIGATE,
                TestingCapability.CLICK,
                TestingCapability.TYPE,
            ]
        )
        execution = self._create_sample_execution()
        runtime = MockTestRuntime(
            execution=execution,
            work_order=wo,
            mock_supported_capabilities={
                TestingCapability.NAVIGATE,
                TestingCapability.CLICK,
                TestingCapability.TYPE,
                TestingCapability.SCREENSHOT,
            },
        )
        # Runtime supports SCREENSHOT, but WorkOrder didn't authorize it
        self.assertEqual(
            runtime.capabilities(),
            {
                TestingCapability.NAVIGATE,
                TestingCapability.CLICK,
                TestingCapability.TYPE,
            },
        )

        # Case B: Runtime only supports a subset of WorkOrder authorized
        runtime_limited = MockTestRuntime(
            execution=execution,
            work_order=wo,
            mock_supported_capabilities={
                TestingCapability.NAVIGATE,
            },
        )
        self.assertEqual(
            runtime_limited.capabilities(),
            {
                TestingCapability.NAVIGATE,
            },
        )

        # Case C: WorkOrder authorises a subset of Runtime supported
        wo_restricted = self._create_sample_work_order(
            authorized_capabilities=[
                TestingCapability.NAVIGATE,
            ]
        )
        runtime_broad = MockTestRuntime(
            execution=execution,
            work_order=wo_restricted,
            mock_supported_capabilities=set(TESTER_ALLOWED_CAPABILITIES),
        )
        self.assertEqual(
            runtime_broad.capabilities(),
            {
                TestingCapability.NAVIGATE,
            },
        )

        # Case D: Outside global tester boundary is filtered out
        # Even if WorkOrder lists an unallowed capability (e.g. EXECUTE_CODE if coerced),
        # global allowed boundary removes it
        wo_dangerous = self._create_sample_work_order(
            authorized_capabilities=[
                TestingCapability.NAVIGATE,
                "EXECUTE_CODE",  # forbidden
            ]
        )
        runtime_safe = MockTestRuntime(
            execution=execution,
            work_order=wo_dangerous,
            mock_supported_capabilities=set(TESTER_ALLOWED_CAPABILITIES),
        )
        self.assertEqual(runtime_safe.capabilities(), {TestingCapability.NAVIGATE})

    # ----------------------------------------------------------------------
    # 8. Unauthorized Capability Rejection at Runtime
    # ----------------------------------------------------------------------
    def test_unauthorized_capability_rejection_at_runtime(self) -> None:
        """Verify assert_capability_available raises TesterBoundaryViolationError."""
        wo = self._create_sample_work_order(
            authorized_capabilities=[TestingCapability.NAVIGATE]
        )
        execution = self._create_sample_execution()
        runtime = MockTestRuntime(
            execution=execution,
            work_order=wo,
            mock_supported_capabilities={
                TestingCapability.NAVIGATE,
                TestingCapability.CLICK,
            },
        )

        # NAVIGATE is authorized and available
        self.assertTrue(runtime.is_capability_available(TestingCapability.NAVIGATE))
        runtime.assert_capability_available(TestingCapability.NAVIGATE)

        # CLICK is supported by runtime, but NOT authorized by WorkOrder
        self.assertFalse(runtime.is_capability_available(TestingCapability.CLICK))
        with self.assertRaises(TesterBoundaryViolationError) as ctx:
            runtime.assert_capability_available(TestingCapability.CLICK)
        self.assertIn("CLICK", str(ctx.exception))

    # ----------------------------------------------------------------------
    # 9. Runtime Configuration Validation and Secret Rejection
    # ----------------------------------------------------------------------
    def test_runtime_configuration_validation_and_secret_rejection(self) -> None:
        """Verify TestRuntimeConfig boundary constraints and zero-secrets enforcement."""
        # Valid config
        valid_cfg = TestRuntimeConfig(
            viewport_width=1280,
            viewport_height=720,
            timeout_seconds=30,
            headless=True,
            application_url="https://app.example.com/checkout",
        )
        valid_cfg.validate()

        # Invalid viewport dimensions
        with self.assertRaises(TesterValidationError):
            TestRuntimeConfig(viewport_width=100, viewport_height=720).validate()
        with self.assertRaises(TesterValidationError):
            TestRuntimeConfig(viewport_width=1280, viewport_height=100).validate()

        # Invalid timeout
        with self.assertRaises(TesterValidationError):
            TestRuntimeConfig(timeout_seconds=-5).validate()

        # Secret rejection in parameters
        with self.assertRaises(TesterValidationError):
            TestRuntimeConfig(parameters={"api_secret": "sk-test-12345"}).validate()
        with self.assertRaises(TesterValidationError):
            TestRuntimeConfig(parameters={"db_password": "p@ssword!"}).validate()

    # ----------------------------------------------------------------------
    # 10. Runtime Event Emission and Provenance
    # ----------------------------------------------------------------------
    def test_runtime_event_emission_and_provenance(self) -> None:
        """Verify events carry full lineage, proper source, correlation_id, and worker_id."""
        wo = self._create_sample_work_order()
        execution = self._create_sample_execution()

        runtime = MockTestRuntime(
            execution=execution,
            work_order=wo,
            event_sink=self._event_sink,
        )
        runtime.start()
        runtime.run()
        runtime.stop(reason="Verification complete")

        self.assertEqual(len(self.emitted_events), 6)
        for event in self.emitted_events:
            self.assertEqual(event.source, EventSource.WORKER)
            self.assertEqual(event.project_id, self.project_id)
            self.assertEqual(event.task_id, wo.manager_task_id)
            self.assertEqual(event.correlation_id, wo.correlation_id)
            self.assertEqual(event.payload["runtime_id"], runtime.runtime_id)
            self.assertEqual(event.payload["environment_id"], runtime.environment_id)
            self.assertEqual(event.payload["work_order_id"], self.work_order_id)
            self.assertEqual(event.payload["execution_id"], self.execution_id)

    # ----------------------------------------------------------------------
    # 11. Failed Startup Transition and Error Capture
    # ----------------------------------------------------------------------
    def test_failed_startup_transition_and_error_capture(self) -> None:
        """Verify simulated startup failure transitions runtime to FAILED and emits event."""
        wo = self._create_sample_work_order()
        execution = self._create_sample_execution()

        runtime = MockTestRuntime(
            execution=execution,
            work_order=wo,
            event_sink=self._event_sink,
            simulate_startup_failure=True,
            simulate_failure_reason="Failed to bind to test browser driver port 9222",
        )

        with self.assertRaises(TesterError) as ctx:
            runtime.start()

        self.assertIn("Failed to bind", str(ctx.exception))
        self.assertEqual(runtime.status, TestRuntimeStatus.FAILED)
        self.assertIn("Failed to bind", str(runtime.failure_reason))

        # Check emitted event sequence includes TEST_RUNTIME_FAILED
        event_types = [e.event_type for e in self.emitted_events]
        self.assertIn(EventType.TEST_RUNTIME_STARTING, event_types)
        self.assertIn(EventType.TEST_RUNTIME_FAILED, event_types)
        self.assertNotIn(EventType.TEST_RUNTIME_READY, event_types)

    # ----------------------------------------------------------------------
    # 12. Clean Shutdown From Various States
    # ----------------------------------------------------------------------
    def test_runtime_clean_shutdown_from_various_states(self) -> None:
        """Verify clean teardown is supported from READY, RUNNING, and FAILED states."""
        wo = self._create_sample_work_order()
        execution = self._create_sample_execution()

        # 1. Stop from READY
        rt_ready = MockTestRuntime(execution=execution, work_order=wo)
        rt_ready.start()
        self.assertEqual(rt_ready.status, TestRuntimeStatus.READY)
        rt_ready.stop(reason="Stopped before running")
        self.assertEqual(rt_ready.status, TestRuntimeStatus.STOPPED)
        self.assertTrue(rt_ready.is_stopped)

        # 2. Stop from RUNNING
        rt_running = MockTestRuntime(execution=execution, work_order=wo)
        rt_running.start()
        rt_running.run()
        self.assertEqual(rt_running.status, TestRuntimeStatus.RUNNING)
        rt_running.stop(reason="Stopped after running")
        self.assertEqual(rt_running.status, TestRuntimeStatus.STOPPED)
        self.assertTrue(rt_running.is_stopped)

        # 3. Stop from FAILED
        rt_failed = MockTestRuntime(
            execution=execution,
            work_order=wo,
            simulate_startup_failure=True,
        )
        try:
            rt_failed.start()
        except TesterError:
            pass
        self.assertEqual(rt_failed.status, TestRuntimeStatus.FAILED)
        rt_failed.stop(reason="Cleanup after failure")
        self.assertEqual(rt_failed.status, TestRuntimeStatus.STOPPED)
        self.assertTrue(rt_failed.is_stopped)

    # ----------------------------------------------------------------------
    # 13. Terminal State Protection
    # ----------------------------------------------------------------------
    def test_terminal_state_protection(self) -> None:
        """Verify STOPPED is terminal and cannot transition or restart, and stop() is idempotent."""
        wo = self._create_sample_work_order()
        execution = self._create_sample_execution()

        runtime = MockTestRuntime(
            execution=execution,
            work_order=wo,
            event_sink=self._event_sink,
        )
        runtime.start()
        runtime.stop()
        self.assertEqual(runtime.status, TestRuntimeStatus.STOPPED)

        # Cannot transition to STARTING
        with self.assertRaises(InvalidTesterTransitionError):
            runtime.transition_to(TestRuntimeStatus.STARTING)

        # Cannot transition to READY
        with self.assertRaises(InvalidTesterTransitionError):
            runtime.transition_to(TestRuntimeStatus.READY)

        # Cannot transition to RUNNING
        with self.assertRaises(InvalidTesterTransitionError):
            runtime.transition_to(TestRuntimeStatus.RUNNING)

        # Cannot transition to FAILED
        with self.assertRaises(InvalidTesterTransitionError):
            runtime.transition_to(TestRuntimeStatus.FAILED)

        # Calling stop() again is idempotent and harmless
        event_count = len(self.emitted_events)
        runtime.stop()
        self.assertEqual(runtime.status, TestRuntimeStatus.STOPPED)
        # No extra events emitted on idempotent stop
        self.assertEqual(len(self.emitted_events), event_count)


if __name__ == "__main__":
    unittest.main()
