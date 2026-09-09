from __future__ import annotations

import unittest
from typing import Any, List

from core.events.model import Event
from core.events.types import EventSource, EventType
from core.tester.contracts.action import TestActionRecord
from core.tester.contracts.boundary import TESTER_ALLOWED_CAPABILITIES
from core.tester.contracts.browser_runtime import (
    BrowserTestRuntime,
    LocalAppTestRuntime,
)
from core.tester.contracts.criteria import AcceptanceCriterion
from core.tester.contracts.environment import TestEnvironment
from core.tester.contracts.execution import TesterExecution
from core.tester.contracts.identifiers import (
    new_action_id,
    new_execution_id,
    new_runtime_id,
    new_session_id,
    new_work_order_id,
    validate_action_id,
    validate_session_id,
)
from core.tester.contracts.navigation_policy import NavigationPolicy
from core.tester.contracts.runtime_config import TestRuntimeConfig
from core.tester.contracts.scope import TestScope
from core.tester.contracts.session import (
    BrowserSession,
    MockBrowserSession,
    NavigationResult,
)
from core.tester.contracts.work_order import TesterWorkOrder
from core.tester.errors import (
    BrowserStartupError,
    EnvironmentMismatchError,
    InvalidUrlError,
    NavigationDeniedError,
    NavigationTimeoutError,
    SessionStoppedError,
    SessionUnavailableError,
    TesterBoundaryViolationError,
    TesterError,
    TesterLineageError,
    TesterValidationError,
    UnsupportedEnvironmentError,
)
from core.tester.types import (
    EnvironmentType,
    TestActionStatus,
    TestCategory,
    TestRuntimeStatus,
    TesterActionType,
    TestingCapability,
)


class TestBrowserSessionRuntime(unittest.TestCase):
    """
    Deterministic unit test suite for Tester V1 Phase 2.2:
    Concrete Browser/App Session Runtime.
    """

    def setUp(self) -> None:
        self.project_id = "proj-session-test-01"
        self.work_order_id = new_work_order_id()
        self.execution_id = new_execution_id()
        self.app_url = "http://localhost:3000"
        self.emitted_events: List[Event] = []

    def _event_sink(self, event: Event) -> None:
        self.emitted_events.append(event)

    def _create_sample_work_order(
        self,
        work_order_id: str | None = None,
        project_id: str | None = None,
        authorized_capabilities: list[TestingCapability | str] | None = None,
        app_url: str | None = None,
        allowed_origins: list[str] | None = None,
    ) -> TesterWorkOrder:
        meta = {}
        if allowed_origins:
            meta["allowed_origins"] = allowed_origins

        return TesterWorkOrder(
            work_order_id=work_order_id or self.work_order_id,
            manager_task_id="mtask-session-test-01",
            project_id=project_id or self.project_id,
            correlation_id="corr-session-test-01",
            objective="Evaluate product user onboarding flow",
            instructions=["Navigate through onboarding pages and inspect readiness"],
            product_artifact="web-frontend:2.1.0",
            test_scope=TestScope(
                routes=["/welcome", "/dashboard"],
                features=["onboarding"],
            ),
            test_categories=[TestCategory.FUNCTIONAL],
            authorized_capabilities=(
                authorized_capabilities
                if authorized_capabilities is not None
                else [TestingCapability.NAVIGATE]
            ),
            acceptance_criteria=[
                AcceptanceCriterion(
                    criterion_id="ac-onboarding-01",
                    description="Onboarding page loads successfully",
                )
            ],
            metadata=meta,
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
            task_id="mtask-session-test-01",
            project_id=project_id or self.project_id,
            correlation_id="corr-session-test-01",
        )

    def _create_runtime(
        self,
        work_order: TesterWorkOrder | None = None,
        session: BrowserSession | None = None,
        app_url: str | None = None,
        simulate_startup_failure: bool = False,
        simulate_timeout: bool = False,
        simulate_error: str | None = None,
        environment_type: EnvironmentType = EnvironmentType.BROWSER,
    ) -> tuple[BrowserTestRuntime, MockBrowserSession]:
        wo = work_order or self._create_sample_work_order()
        execution = self._create_sample_execution()
        target_url = app_url or self.app_url

        env = TestEnvironment(
            project_id=self.project_id,
            work_order_id=self.work_order_id,
            execution_id=self.execution_id,
            application_url=target_url,
            environment_type=environment_type,
        )

        mock_session = session or MockBrowserSession(
            project_id=self.project_id,
            work_order_id=self.work_order_id,
            execution_id=self.execution_id,
            runtime_id=new_runtime_id(),
            initial_url=target_url,
            simulate_startup_failure=simulate_startup_failure,
            simulate_timeout=simulate_timeout,
            simulate_error=simulate_error,
        )

        runtime = BrowserTestRuntime(
            execution=execution,
            work_order=wo,
            environment=env,
            session=mock_session,
            runtime_id=mock_session.runtime_id,
            event_sink=self._event_sink,
        )
        return runtime, mock_session

    # ----------------------------------------------------------------------
    # 1. Session Creation and Ownership Validation
    # ----------------------------------------------------------------------
    def test_session_creation_and_ownership_validation(self) -> None:
        """Verify BrowserSession assigns valid session_id and strictly validates ownership."""
        session = MockBrowserSession(
            project_id=self.project_id,
            work_order_id=self.work_order_id,
            execution_id=self.execution_id,
            runtime_id=new_runtime_id(),
            initial_url=self.app_url,
        )
        self.assertTrue(session.session_id.startswith("tsess-"))
        validate_session_id(session.session_id)
        self.assertEqual(session.current_url, self.app_url)
        self.assertFalse(session.is_ready)
        self.assertFalse(session.is_closed)

        # Correct ownership passes
        session.validate_ownership(
            project_id=self.project_id,
            work_order_id=self.work_order_id,
            execution_id=self.execution_id,
            runtime_id=session.runtime_id,
        )

        # Mismatched project
        with self.assertRaises(EnvironmentMismatchError):
            session.validate_ownership(
                project_id="proj-other",
                work_order_id=self.work_order_id,
                execution_id=self.execution_id,
                runtime_id=session.runtime_id,
            )

    # ----------------------------------------------------------------------
    # 2. Session Startup and Initial URL
    # ----------------------------------------------------------------------
    def test_session_startup_and_initial_url(self) -> None:
        """Verify session startup makes it ready and initializes current URL."""
        runtime, session = self._create_runtime()
        self.assertEqual(runtime.status, TestRuntimeStatus.CREATED)
        self.assertFalse(session.is_ready)

        runtime.start()
        self.assertEqual(runtime.status, TestRuntimeStatus.READY)
        self.assertTrue(session.is_ready)
        self.assertEqual(session.current_url, self.app_url)
        self.assertEqual(runtime.current_url, self.app_url)

    # ----------------------------------------------------------------------
    # 3. Session Readiness Check
    # ----------------------------------------------------------------------
    def test_session_readiness_check(self) -> None:
        """Verify navigation cannot occur prior to startup readiness."""
        runtime, session = self._create_runtime()
        # Before start(), runtime is CREATED, session is not ready
        self.assertFalse(session.is_ready)
        with self.assertRaises(SessionUnavailableError):
            runtime.navigate("http://localhost:3000/dashboard")

    # ----------------------------------------------------------------------
    # 4. Navigation Success and URL Tracking
    # ----------------------------------------------------------------------
    def test_navigation_success_and_url_tracking(self) -> None:
        """Verify successful navigation updates current URL and records history."""
        runtime, session = self._create_runtime()
        runtime.start()

        action = runtime.navigate("http://localhost:3000/welcome")
        self.assertEqual(action.status, TestActionStatus.SUCCESS)
        self.assertEqual(action.target, "http://localhost:3000/welcome")
        self.assertEqual(action.previous_state.get("url"), self.app_url)
        self.assertEqual(action.resulting_state.get("url"), "http://localhost:3000/welcome")
        self.assertEqual(action.resulting_state.get("status_code"), 200)
        self.assertEqual(session.current_url, "http://localhost:3000/welcome")
        self.assertEqual(runtime.current_url, "http://localhost:3000/welcome")

        # Relative route navigation
        action2 = runtime.navigate("/dashboard")
        self.assertEqual(action2.status, TestActionStatus.SUCCESS)
        self.assertEqual(action2.target, "http://localhost:3000/dashboard")
        self.assertEqual(action2.previous_state.get("url"), "http://localhost:3000/welcome")
        self.assertEqual(runtime.current_url, "http://localhost:3000/dashboard")

    # ----------------------------------------------------------------------
    # 5. Navigation Capability Denial
    # ----------------------------------------------------------------------
    def test_navigation_capability_denial(self) -> None:
        """Verify navigation is denied when NAVIGATE capability is not authorized by WorkOrder."""
        # WorkOrder authorizes BEHAVIOR_OBSERVATION, but NOT NAVIGATE
        wo_unauthorized = self._create_sample_work_order(
            authorized_capabilities=[TestingCapability.BEHAVIOR_OBSERVATION]
        )
        runtime, _ = self._create_runtime(work_order=wo_unauthorized)
        runtime.start()

        self.assertFalse(runtime.is_capability_available(TestingCapability.NAVIGATE))
        with self.assertRaises(TesterBoundaryViolationError) as ctx:
            runtime.navigate("http://localhost:3000/dashboard")
        self.assertIn("NAVIGATE", str(ctx.exception))

    # ----------------------------------------------------------------------
    # 6. Invalid URL and Unsupported Scheme Rejection
    # ----------------------------------------------------------------------
    def test_invalid_url_and_unsupported_scheme_rejection(self) -> None:
        """Verify malformed URLs, empty targets, and dangerous schemes are rejected."""
        runtime, _ = self._create_runtime()
        runtime.start()

        # Empty target
        with self.assertRaises(InvalidUrlError):
            runtime.navigate("")

        # Dangerous pseudo-schemes
        with self.assertRaises(InvalidUrlError):
            runtime.navigate("javascript:alert(1)")

        with self.assertRaises(InvalidUrlError):
            runtime.navigate("data:text/html,<h1>Test</h1>")

        # Unsupported protocol scheme (e.g. ftp, ssh)
        with self.assertRaises(InvalidUrlError):
            runtime.navigate("ftp://localhost:3000/files")

    # ----------------------------------------------------------------------
    # 7. Unauthorized Origin Boundary Denial
    # ----------------------------------------------------------------------
    def test_unauthorized_origin_boundary_denial(self) -> None:
        """Verify navigation to external unlisted origins is blocked by NavigationPolicy."""
        runtime, _ = self._create_runtime()
        runtime.start()

        # Primary origin is http://localhost:3000. External domain should be denied:
        with self.assertRaises(NavigationDeniedError) as ctx:
            runtime.navigate("https://unrelated-external-site.com/login")
        self.assertIn("Navigation denied", str(ctx.exception))

        # Loopback equivalence: http://127.0.0.1:3000 is allowed when http://localhost:3000 is primary
        action = runtime.navigate("http://127.0.0.1:3000/dashboard")
        self.assertEqual(action.status, TestActionStatus.SUCCESS)

        # WorkOrder with explicitly allowed extra origin
        wo_with_extra = self._create_sample_work_order(
            allowed_origins=["https://auth.company.example"]
        )
        runtime_extra, _ = self._create_runtime(work_order=wo_with_extra)
        runtime_extra.start()

        action_extra = runtime_extra.navigate("https://auth.company.example/oauth/token")
        self.assertEqual(action_extra.status, TestActionStatus.SUCCESS)

    # ----------------------------------------------------------------------
    # 8. Navigation Timeout Handling
    # ----------------------------------------------------------------------
    def test_navigation_timeout_handling(self) -> None:
        """Verify navigation timeout raises NavigationTimeoutError, records TIMED_OUT, and emits event."""
        runtime, _ = self._create_runtime(simulate_timeout=True)
        runtime.start()

        with self.assertRaises(NavigationTimeoutError) as ctx:
            runtime.navigate("http://localhost:3000/slow-page", timeout_seconds=2.0)

        self.assertIn("timed out", str(ctx.exception))
        # Check action record
        self.assertEqual(len(runtime.action_records), 1)
        record = runtime.action_records[0]
        self.assertEqual(record.status, TestActionStatus.TIMED_OUT)
        self.assertIn("timed out", str(record.error))

        # Check emitted event includes TEST_NAVIGATION_FAILED with timeout flag
        event_types = [e.event_type for e in self.emitted_events]
        self.assertIn(EventType.TEST_NAVIGATION_STARTED, event_types)
        self.assertIn(EventType.TEST_NAVIGATION_FAILED, event_types)
        failed_event = [e for e in self.emitted_events if e.event_type == EventType.TEST_NAVIGATION_FAILED][0]
        self.assertTrue(failed_event.payload.get("timeout"))

    # ----------------------------------------------------------------------
    # 9. Session Ownership Mismatch Rejection
    # ----------------------------------------------------------------------
    def test_session_ownership_mismatch_rejection(self) -> None:
        """Verify runtime rejects pre-injected session belonging to another execution or work order."""
        execution = self._create_sample_execution()
        wo = self._create_sample_work_order()
        env = TestEnvironment(
            project_id=self.project_id,
            work_order_id=self.work_order_id,
            execution_id=self.execution_id,
            application_url=self.app_url,
        )

        foreign_session = MockBrowserSession(
            project_id=self.project_id,
            work_order_id="two-foreign-work-order",  # mismatch
            execution_id=self.execution_id,
            runtime_id=new_runtime_id(),
        )

        with self.assertRaises(EnvironmentMismatchError):
            BrowserTestRuntime(
                execution=execution,
                work_order=wo,
                environment=env,
                session=foreign_session,
            )

    # ----------------------------------------------------------------------
    # 10. Runtime Lifecycle Transitions
    # ----------------------------------------------------------------------
    def test_runtime_lifecycle_during_session_operations(self) -> None:
        """Verify runtime transitions correctly from CREATED -> READY -> RUNNING -> STOPPED."""
        runtime, session = self._create_runtime()
        self.assertEqual(runtime.status, TestRuntimeStatus.CREATED)

        runtime.start()
        self.assertEqual(runtime.status, TestRuntimeStatus.READY)

        # First navigation automatically transitions from READY -> RUNNING
        runtime.navigate("/dashboard")
        self.assertEqual(runtime.status, TestRuntimeStatus.RUNNING)

        runtime.stop(reason="All testing steps completed")
        self.assertEqual(runtime.status, TestRuntimeStatus.STOPPED)
        self.assertTrue(session.is_closed)

        # Cannot navigate after STOPPED
        with self.assertRaises(SessionStoppedError):
            runtime.navigate("/profile")

    # ----------------------------------------------------------------------
    # 11. Clean Shutdown and Resource Release
    # ----------------------------------------------------------------------
    def test_clean_shutdown_and_resource_release(self) -> None:
        """Verify stop() closes session and releases resources cleanly."""
        runtime, session = self._create_runtime()
        runtime.start()
        self.assertTrue(session.is_ready)

        runtime.stop()
        self.assertEqual(runtime.status, TestRuntimeStatus.STOPPED)
        self.assertTrue(session.is_closed)
        self.assertFalse(session.is_ready)

        # Repeated stop is idempotent and harmless
        runtime.stop()
        self.assertEqual(runtime.status, TestRuntimeStatus.STOPPED)

    # ----------------------------------------------------------------------
    # 12. Shutdown After Failure
    # ----------------------------------------------------------------------
    def test_shutdown_after_startup_or_navigation_failure(self) -> None:
        """Verify failed startup cleans up resources and transitions to STOPPED on stop()."""
        runtime, session = self._create_runtime(simulate_startup_failure=True)
        with self.assertRaises(BrowserStartupError):
            runtime.start()

        self.assertEqual(runtime.status, TestRuntimeStatus.FAILED)
        runtime.stop(reason="Clean up after failure")
        self.assertEqual(runtime.status, TestRuntimeStatus.STOPPED)
        self.assertTrue(session.is_closed)

    # ----------------------------------------------------------------------
    # 13. Event Provenance For Navigation Events
    # ----------------------------------------------------------------------
    def test_event_provenance_for_navigation_events(self) -> None:
        """Verify navigation events preserve source=WORKER, project_id, work_order_id, execution_id, and action_id."""
        runtime, _ = self._create_runtime()
        runtime.start()
        action = runtime.navigate("/welcome")

        # Find navigation events
        nav_events = [
            e for e in self.emitted_events
            if e.event_type in (EventType.TEST_NAVIGATION_STARTED, EventType.TEST_NAVIGATION_COMPLETED)
        ]
        self.assertEqual(len(nav_events), 2)

        for e in nav_events:
            self.assertEqual(e.source, EventSource.WORKER)
            self.assertEqual(e.project_id, self.project_id)
            self.assertEqual(e.payload["action_id"], action.action_id)
            self.assertEqual(e.payload["runtime_id"], runtime.runtime_id)
            self.assertEqual(e.payload["work_order_id"], self.work_order_id)
            self.assertEqual(e.payload["execution_id"], self.execution_id)

    # ----------------------------------------------------------------------
    # 14. Action Record Creation and Audit Trail
    # ----------------------------------------------------------------------
    def test_action_record_creation_and_fields(self) -> None:
        """Verify TestActionRecord contains all required audit fields and computes duration."""
        runtime, _ = self._create_runtime()
        runtime.start()
        action = runtime.navigate("http://localhost:3000/dashboard")

        validate_action_id(action.action_id)
        self.assertEqual(action.execution_id, self.execution_id)
        self.assertEqual(action.runtime_id, runtime.runtime_id)
        self.assertEqual(action.action_type, TesterActionType.NAVIGATE)
        self.assertEqual(action.target, "http://localhost:3000/dashboard")
        self.assertEqual(action.status, TestActionStatus.SUCCESS)
        self.assertIsNotNone(action.started_at)
        self.assertIsNotNone(action.completed_at)
        self.assertIsNotNone(action.duration_ms())
        self.assertEqual(action.resulting_state.get("title"), "Page - http://localhost:3000/dashboard")

        # Verify serialization round-trip
        data = action.to_dict()
        reconstructed = TestActionRecord.from_dict(data)
        self.assertEqual(reconstructed.action_id, action.action_id)
        self.assertEqual(reconstructed.target, action.target)
        self.assertEqual(reconstructed.status, TestActionStatus.SUCCESS)

    # ----------------------------------------------------------------------
    # 15. No Secret Leakage In Navigation Or Records
    # ----------------------------------------------------------------------
    def test_no_secret_leakage_in_navigation_or_records(self) -> None:
        """Verify sensitive credentials in target URLs are strictly rejected."""
        runtime, _ = self._create_runtime()
        runtime.start()

        # Reject URL with embedded password
        with self.assertRaises(TesterValidationError) as ctx:
            runtime.navigate("http://admin:secretpassword@localhost:3000/dashboard")
        self.assertIn("Credentials embedded", str(ctx.exception))

        # Reject URL with sensitive query parameter
        with self.assertRaises(TesterValidationError) as ctx:
            runtime.navigate("http://localhost:3000/login?api_key=sk-12345")
        self.assertIn("Sensitive credential", str(ctx.exception))

        with self.assertRaises(TesterValidationError) as ctx:
            runtime.navigate("http://localhost:3000/reset?password=newpass")
        self.assertIn("Sensitive credential", str(ctx.exception))

    # ----------------------------------------------------------------------
    # 16. Unsupported LOCAL_APP Behavior
    # ----------------------------------------------------------------------
    def test_unsupported_local_app_environment_behavior(self) -> None:
        """Verify LOCAL_APP environment explicitly raises UnsupportedEnvironmentError without faking support."""
        # 1. BrowserTestRuntime with LOCAL_APP environment
        runtime, _ = self._create_runtime(environment_type=EnvironmentType.LOCAL_APP)
        with self.assertRaises(UnsupportedEnvironmentError) as ctx:
            runtime.start()
        self.assertIn("LOCAL_APP", str(ctx.exception))

        # 2. LocalAppTestRuntime adapter
        wo = self._create_sample_work_order()
        execution = self._create_sample_execution()
        local_app_runtime = LocalAppTestRuntime(execution=execution, work_order=wo)
        self.assertEqual(local_app_runtime.supported_capabilities(), set())

        with self.assertRaises(UnsupportedEnvironmentError) as ctx:
            local_app_runtime.start()
        self.assertIn("LOCAL_APP", str(ctx.exception))

    # ----------------------------------------------------------------------
    # 17. No False Capabilities Exposed
    # ----------------------------------------------------------------------
    def test_no_false_capabilities_exposed(self) -> None:
        """
        CRITICAL: The runtime MUST NOT report a capability as available unless
        it is genuinely implemented. Only NAVIGATE is supported in Phase 2.2.
        """
        runtime, _ = self._create_runtime()
        supported = runtime.supported_capabilities()

        # ONLY NAVIGATE is supported
        self.assertEqual(supported, {TestingCapability.NAVIGATE})

        # MUST NOT claim interaction or visual analysis capabilities
        forbidden_claims = [
            TestingCapability.CLICK,
            TestingCapability.TYPE,
            TestingCapability.SCROLL,
            TestingCapability.HOVER,
            TestingCapability.DRAG,
            TestingCapability.KEYBOARD_INPUT,
            TestingCapability.SCREENSHOT,
            TestingCapability.SCREEN_RECORDING,
            TestingCapability.OCR,
            TestingCapability.PERFORMANCE_MEASUREMENT,
        ]
        for cap in forbidden_claims:
            self.assertNotIn(cap, supported)
            self.assertFalse(runtime.is_capability_available(cap))


if __name__ == "__main__":
    unittest.main()
