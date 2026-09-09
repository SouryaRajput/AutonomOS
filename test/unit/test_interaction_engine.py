from __future__ import annotations

import unittest
from typing import Any, List

from core.events.model import Event
from core.events.types import EventSource, EventType
from core.tester.contracts.action import TestActionRecord
from core.tester.contracts.boundary import TESTER_ALLOWED_CAPABILITIES
from core.tester.contracts.browser_runtime import BrowserTestRuntime
from core.tester.contracts.criteria import AcceptanceCriterion
from core.tester.contracts.environment import TestEnvironment
from core.tester.contracts.execution import TesterExecution
from core.tester.contracts.identifiers import (
    new_execution_id,
    new_runtime_id,
    new_work_order_id,
    validate_action_id,
)
from core.tester.contracts.interaction import (
    InteractionEngine,
    InteractionResult,
    InteractionStatus,
    InteractionTarget,
    normalize_target,
)
from core.tester.contracts.scope import TestScope
from core.tester.contracts.session import MockBrowserSession
from core.tester.contracts.work_order import TesterWorkOrder
from core.tester.errors import (
    SessionStoppedError,
    SessionUnavailableError,
    TesterValidationError,
)
from core.tester.types import (
    EnvironmentType,
    TestCategory,
    TestRuntimeStatus,
    TesterActionType,
    TesterExecutionStatus,
    TestingCapability,
)


class TestInteractionEngine(unittest.TestCase):
    """
    Comprehensive unit test suite for Tester V1 Phase 2.3: Controlled Interaction Engine.
    
    Validates:
    - All 9 interaction actions (move_cursor, click, double_click, type_text, press_key, scroll, hover, drag, wait)
    - Strict 3-way capability intersection enforcement
    - Active runtime state validation (no silent auto-starts on illegal states)
    - Deterministic target validation (CSS, coordinates, text)
    - Timeout handling
    - Action audit tracing and timing
    - Sensitive text input redaction
    - Action failure isolation (does not crash TesterExecution)
    - Zero automatic retries
    - Project/execution ownership and lineage
    - Domain event emission with complete causal provenance
    """

    def setUp(self) -> None:
        self.project_id = "proj-interaction-test-01"
        self.work_order_id = new_work_order_id()
        self.execution_id = new_execution_id()
        self.runtime_id = new_runtime_id()
        self.app_url = "https://app.autonomos.internal/dashboard"
        self.emitted_events: List[Event] = []

    def _event_sink(self, event: Event) -> None:
        self.emitted_events.append(event)

    def _create_sample_work_order(
        self,
        authorized_capabilities: list[TestingCapability] | None = None,
    ) -> TesterWorkOrder:
        all_caps = [
            TestingCapability.NAVIGATE,
            TestingCapability.MOVE_CURSOR,
            TestingCapability.CLICK,
            TestingCapability.TYPE,
            TestingCapability.KEYBOARD_INPUT,
            TestingCapability.SCROLL,
            TestingCapability.HOVER,
            TestingCapability.DRAG,
            TestingCapability.WAIT,
        ]
        return TesterWorkOrder(
            work_order_id=self.work_order_id,
            manager_task_id="mtask-interaction-01",
            project_id=self.project_id,
            correlation_id="corr-interaction-01",
            objective="Evaluate controlled interaction engine",
            instructions=["Interact with input fields, buttons, and drag elements"],
            product_artifact="web-frontend:2.3.0",
            test_scope=TestScope(
                routes=["/dashboard"],
                features=["interaction"],
            ),
            test_categories=[TestCategory.FUNCTIONAL],
            authorized_capabilities=authorized_capabilities if authorized_capabilities is not None else all_caps,
            acceptance_criteria=[
                AcceptanceCriterion(
                    criterion_id="ac-interaction-01",
                    description="Interaction operations succeed reliably",
                )
            ],
        )

    def _create_sample_execution(self) -> TesterExecution:
        return TesterExecution(
            execution_id=self.execution_id,
            work_order_id=self.work_order_id,
            task_id="mtask-interaction-01",
            project_id=self.project_id,
            correlation_id="corr-interaction-01",
        )

    def _create_runtime(
        self,
        work_order: TesterWorkOrder | None = None,
        session: MockBrowserSession | None = None,
        unsupported_capabilities: set[TestingCapability] | None = None,
        simulate_action_failure: dict[str, str] | None = None,
        simulate_timeout_actions: set[str] | None = None,
    ) -> tuple[BrowserTestRuntime, MockBrowserSession]:
        wo = work_order or self._create_sample_work_order()
        execution = self._create_sample_execution()

        env = TestEnvironment(
            project_id=self.project_id,
            work_order_id=self.work_order_id,
            execution_id=self.execution_id,
            application_url=self.app_url,
            environment_type=EnvironmentType.BROWSER,
        )

        mock_session = session or MockBrowserSession(
            project_id=self.project_id,
            work_order_id=self.work_order_id,
            execution_id=self.execution_id,
            runtime_id=self.runtime_id,
            initial_url=self.app_url,
            unsupported_capabilities=unsupported_capabilities,
            simulate_action_failure=simulate_action_failure,
            simulate_timeout_actions=simulate_timeout_actions,
        )

        runtime = BrowserTestRuntime(
            execution=execution,
            work_order=wo,
            environment=env,
            session=mock_session,
            runtime_id=self.runtime_id,
            event_sink=self._event_sink,
        )
        return runtime, mock_session

    # ----------------------------------------------------------------------
    # 1. Cursor Movement
    # ----------------------------------------------------------------------
    def test_cursor_movement(self) -> None:
        """Verify controlled cursor movement with valid coordinates."""
        runtime, session = self._create_runtime()
        runtime.start()

        result = runtime.move_cursor((150.0, 300.0))

        self.assertEqual(result.status, InteractionStatus.SUCCESS)
        self.assertTrue(result.is_success)
        self.assertEqual(result.action_type, TesterActionType.MOVE_CURSOR)
        self.assertEqual(result.target, {"x": 150.0, "y": 300.0})

        # Verify underlying session recorded movement
        self.assertEqual(len(session.recorded_interactions), 1)
        rec = session.recorded_interactions[0]
        self.assertEqual(rec["action"], "move_cursor")
        self.assertEqual(rec["x"], 150.0)
        self.assertEqual(rec["y"], 300.0)

    # ----------------------------------------------------------------------
    # 2. Click (Single Click)
    # ----------------------------------------------------------------------
    def test_click(self) -> None:
        """Verify single click on target element selector."""
        runtime, session = self._create_runtime()
        runtime.start()

        result = runtime.click("#submit-btn")

        self.assertEqual(result.status, InteractionStatus.SUCCESS)
        self.assertTrue(result.is_success)
        self.assertEqual(result.action_type, TesterActionType.CLICK)
        self.assertEqual(result.target, {"selector": "#submit-btn"})

        self.assertEqual(len(session.recorded_interactions), 1)
        rec = session.recorded_interactions[0]
        self.assertEqual(rec["action"], "click")
        self.assertEqual(rec["selector"], "#submit-btn")
        self.assertFalse(rec["double"])

    # ----------------------------------------------------------------------
    # 3. Double Click
    # ----------------------------------------------------------------------
    def test_double_click(self) -> None:
        """Verify double click on target element."""
        runtime, session = self._create_runtime()
        runtime.start()

        result = runtime.double_click(".file-row")

        self.assertEqual(result.status, InteractionStatus.SUCCESS)
        self.assertTrue(result.is_success)
        self.assertEqual(result.action_type, TesterActionType.DOUBLE_CLICK)

        self.assertEqual(len(session.recorded_interactions), 1)
        rec = session.recorded_interactions[0]
        self.assertEqual(rec["action"], "double_click")
        self.assertEqual(rec["selector"], ".file-row")
        self.assertTrue(rec["double"])

    # ----------------------------------------------------------------------
    # 4. Type Text
    # ----------------------------------------------------------------------
    def test_type_text(self) -> None:
        """Verify text typing into target input field."""
        runtime, session = self._create_runtime()
        runtime.start()

        result = runtime.type_text("#username", "tester_alice")

        self.assertEqual(result.status, InteractionStatus.SUCCESS)
        self.assertTrue(result.is_success)
        self.assertEqual(result.action_type, TesterActionType.TYPE)
        self.assertEqual(result.resulting_state.get("text_entered"), "tester_alice")
        self.assertFalse(result.sensitive)

        self.assertEqual(len(session.recorded_interactions), 1)
        rec = session.recorded_interactions[0]
        self.assertEqual(rec["action"], "type_text")
        self.assertEqual(rec["selector"], "#username")
        self.assertEqual(rec["text"], "tester_alice")

    # ----------------------------------------------------------------------
    # 5. Keyboard Input
    # ----------------------------------------------------------------------
    def test_keyboard_input(self) -> None:
        """Verify discrete keyboard key presses."""
        runtime, session = self._create_runtime()
        runtime.start()

        result_enter = runtime.press_key("Enter", target="#search-input")
        self.assertEqual(result_enter.status, InteractionStatus.SUCCESS)
        self.assertEqual(result_enter.action_type, TesterActionType.PRESS_KEY)

        result_tab = runtime.press_key("Tab")
        self.assertEqual(result_tab.status, InteractionStatus.SUCCESS)

        self.assertEqual(len(session.recorded_interactions), 2)
        self.assertEqual(session.recorded_interactions[0]["key"], "Enter")
        self.assertEqual(session.recorded_interactions[1]["key"], "Tab")

    # ----------------------------------------------------------------------
    # 6. Scroll
    # ----------------------------------------------------------------------
    def test_scroll(self) -> None:
        """Verify vertical and horizontal scrolling."""
        runtime, session = self._create_runtime()
        runtime.start()

        result_v = runtime.scroll(direction="vertical", amount=350)
        self.assertEqual(result_v.status, InteractionStatus.SUCCESS)
        self.assertEqual(result_v.action_type, TesterActionType.SCROLL)

        result_h = runtime.scroll(direction="horizontal", amount=120)
        self.assertEqual(result_h.status, InteractionStatus.SUCCESS)

        self.assertEqual(len(session.recorded_interactions), 2)
        self.assertEqual(session.recorded_interactions[0]["direction"], "vertical")
        self.assertEqual(session.recorded_interactions[0]["amount"], 350)
        self.assertEqual(session.recorded_interactions[1]["direction"], "horizontal")
        self.assertEqual(session.recorded_interactions[1]["amount"], 120)

    # ----------------------------------------------------------------------
    # 7. Hover
    # ----------------------------------------------------------------------
    def test_hover(self) -> None:
        """Verify cursor hover over element."""
        runtime, session = self._create_runtime()
        runtime.start()

        result = runtime.hover(".menu-dropdown")

        self.assertEqual(result.status, InteractionStatus.SUCCESS)
        self.assertEqual(result.action_type, TesterActionType.HOVER)

        self.assertEqual(len(session.recorded_interactions), 1)
        rec = session.recorded_interactions[0]
        self.assertEqual(rec["action"], "hover")
        self.assertEqual(rec["selector"], ".menu-dropdown")

    # ----------------------------------------------------------------------
    # 8. Drag
    # ----------------------------------------------------------------------
    def test_drag(self) -> None:
        """Verify drag and drop from source to destination."""
        runtime, session = self._create_runtime()
        runtime.start()

        result = runtime.drag("#item-draggable", "#dropzone")

        self.assertEqual(result.status, InteractionStatus.SUCCESS)
        self.assertEqual(result.action_type, TesterActionType.DRAG)

        self.assertEqual(len(session.recorded_interactions), 1)
        rec = session.recorded_interactions[0]
        self.assertEqual(rec["action"], "drag")
        self.assertEqual(rec["source"]["selector"], "#item-draggable")
        self.assertEqual(rec["destination"]["selector"], "#dropzone")

    # ----------------------------------------------------------------------
    # 9. Wait (Bounded Wait)
    # ----------------------------------------------------------------------
    def test_wait(self) -> None:
        """Verify deterministic bounded wait and rejection of invalid durations."""
        runtime, session = self._create_runtime()
        runtime.start()

        # Valid wait
        result = runtime.wait(0.01)
        self.assertEqual(result.status, InteractionStatus.SUCCESS)
        self.assertEqual(result.action_type, TesterActionType.WAIT)

        # Negative wait rejected
        with self.assertRaises(TesterValidationError):
            runtime.wait(-1.0)

        # Excessive wait (> 60s) rejected
        with self.assertRaises(TesterValidationError):
            runtime.wait(65.0)

    # ----------------------------------------------------------------------
    # 10. Authorization Denial
    # ----------------------------------------------------------------------
    def test_authorization_denial(self) -> None:
        """Verify action is strictly DENIED when capability is not in WorkOrder."""
        # WorkOrder authorizes only NAVIGATE and CLICK (no TYPE)
        wo = self._create_sample_work_order(
            authorized_capabilities=[TestingCapability.NAVIGATE, TestingCapability.CLICK]
        )
        runtime, session = self._create_runtime(work_order=wo)
        runtime.start()

        # Try to type (requires TYPE capability)
        result = runtime.type_text("#input", "unauthorized input")

        self.assertEqual(result.status, InteractionStatus.DENIED)
        self.assertFalse(result.is_success)
        self.assertIn("not authorized", result.error or "")

        # Underlying session was NEVER invoked
        self.assertEqual(len(session.recorded_interactions), 0)

        # TEST_ACTION_DENIED event emitted
        denied_events = [e for e in self.emitted_events if e.event_type == EventType.TEST_ACTION_DENIED]
        self.assertEqual(len(denied_events), 1)
        self.assertEqual(denied_events[0].payload.get("action_type"), "TYPE")

    # ----------------------------------------------------------------------
    # 11. Runtime State Denial
    # ----------------------------------------------------------------------
    def test_runtime_state_denial(self) -> None:
        """Verify interactions are rejected when runtime is CREATED, STOPPED, or FAILED."""
        runtime, session = self._create_runtime()

        # 1. State: CREATED (not started)
        res_created = runtime.click("#btn")
        self.assertEqual(res_created.status, InteractionStatus.FAILED)
        self.assertIn("not ready", res_created.error or "")
        self.assertEqual(runtime.status, TestRuntimeStatus.CREATED)

        # Start runtime
        runtime.start()
        self.assertEqual(runtime.status, TestRuntimeStatus.READY)

        # 2. State: STOPPED
        runtime.stop()
        self.assertEqual(runtime.status, TestRuntimeStatus.STOPPED)
        res_stopped = runtime.click("#btn")
        self.assertEqual(res_stopped.status, InteractionStatus.FAILED)
        self.assertIn("stopped runtime", res_stopped.error or "")

        # Session was never called
        self.assertEqual(len(session.recorded_interactions), 0)

    # ----------------------------------------------------------------------
    # 12. Unsupported Capability
    # ----------------------------------------------------------------------
    def test_unsupported_capability(self) -> None:
        """Verify NOT_SUPPORTED status when underlying backend lacks the capability."""
        # Session does not support DRAG
        runtime, session = self._create_runtime(
            unsupported_capabilities={TestingCapability.DRAG}
        )
        runtime.start()

        result = runtime.drag("#item", "#zone")

        self.assertEqual(result.status, InteractionStatus.NOT_SUPPORTED)
        self.assertFalse(result.is_success)
        self.assertIn("not supported", (result.error or "").lower())
        self.assertEqual(len(session.recorded_interactions), 0)

    # ----------------------------------------------------------------------
    # 13. Invalid Target Handling
    # ----------------------------------------------------------------------
    def test_invalid_target(self) -> None:
        """Verify malformed or empty targets are rejected with clear validation error."""
        runtime, session = self._create_runtime()
        runtime.start()

        # Empty string target
        res_empty = runtime.click("")
        self.assertEqual(res_empty.status, InteractionStatus.FAILED)
        self.assertIn("Target validation error", res_empty.error or "")

        # Negative coordinate target
        res_neg = runtime.move_cursor((-10.0, 50.0))
        self.assertEqual(res_neg.status, InteractionStatus.FAILED)
        self.assertIn("negative", res_neg.error or "")

        # Target normalizing helper tests
        with self.assertRaises(TesterValidationError):
            normalize_target("")

        with self.assertRaises(TesterValidationError):
            normalize_target((-5, -5))

        with self.assertRaises(TesterValidationError):
            normalize_target(12345)

        # Text prefix target parsing
        text_tgt = normalize_target("text=Sign In")
        self.assertEqual(text_tgt.text, "Sign In")

    # ----------------------------------------------------------------------
    # 14. Timeout Handling
    # ----------------------------------------------------------------------
    def test_timeout_handling(self) -> None:
        """Verify driver timeouts return TIMEOUT status without crashing runtime."""
        runtime, session = self._create_runtime(
            simulate_timeout_actions={"click"}
        )
        runtime.start()

        result = runtime.click("#slow-button", timeout_seconds=1.0)

        self.assertEqual(result.status, InteractionStatus.TIMEOUT)
        self.assertFalse(result.is_success)
        self.assertIn("timed out", (result.error or "").lower())

        # Runtime is still RUNNING and healthy
        self.assertEqual(runtime.status, TestRuntimeStatus.RUNNING)

    # ----------------------------------------------------------------------
    # 15. Action Audit Trace
    # ----------------------------------------------------------------------
    def test_action_trace(self) -> None:
        """Verify complete auditable trace with valid IDs, timestamps, and duration."""
        runtime, session = self._create_runtime()
        runtime.start()

        result = runtime.click("#profile-link")

        self.assertEqual(result.status, InteractionStatus.SUCCESS)
        validate_action_id(result.action_id)
        self.assertEqual(result.execution_id, self.execution_id)
        self.assertEqual(result.runtime_id, self.runtime_id)
        self.assertIsNotNone(result.started_at)
        self.assertIsNotNone(result.completed_at)
        self.assertGreaterEqual(result.duration_ms or 0.0, 0.0)

        # Round-trip serialization
        data = result.to_dict()
        restored = InteractionResult.from_dict(data)
        self.assertEqual(restored.action_id, result.action_id)
        self.assertEqual(restored.status, result.status)
        self.assertEqual(restored.action_type, result.action_type)
        self.assertEqual(restored.duration_ms, result.duration_ms)

        # In runtime history
        self.assertEqual(len(runtime.interaction_history), 1)
        self.assertEqual(runtime.interaction_history[0].action_id, result.action_id)

    # ----------------------------------------------------------------------
    # 16. Sensitive Input Redaction
    # ----------------------------------------------------------------------
    def test_sensitive_input_redaction(self) -> None:
        """Verify sensitive=True strictly redacts secret strings across records, traces, and events."""
        runtime, session = self._create_runtime()
        runtime.start()

        secret_password = "SuperSecretPassword123!"
        result = runtime.type_text("#password", secret_password, sensitive=True)

        self.assertEqual(result.status, InteractionStatus.SUCCESS)
        self.assertTrue(result.sensitive)

        # Result state is redacted
        self.assertEqual(result.resulting_state.get("text_entered"), "[REDACTED]")
        self.assertNotIn(secret_password, str(result.to_dict()))

        # Session record is redacted
        rec = session.recorded_interactions[0]
        self.assertEqual(rec["text"], "[REDACTED]")
        self.assertTrue(rec["sensitive"])
        self.assertNotIn(secret_password, str(rec))

        # Event payload does not contain secret
        comp_events = [e for e in self.emitted_events if e.event_type == EventType.TEST_ACTION_COMPLETED]
        self.assertEqual(len(comp_events), 1)
        self.assertNotIn(secret_password, str(comp_events[0].payload))

    # ----------------------------------------------------------------------
    # 17. Action Failure Separation from Execution Failure
    # ----------------------------------------------------------------------
    def test_action_failure_without_execution_failure(self) -> None:
        """Verify a failed interaction marks the action as FAILED without failing TesterExecution."""
        runtime, session = self._create_runtime(
            simulate_action_failure={"click": "Element '#nonexistent' not found in DOM"}
        )
        runtime.start()

        result = runtime.click("#nonexistent")

        self.assertEqual(result.status, InteractionStatus.FAILED)
        self.assertFalse(result.is_success)
        self.assertIn("not found in DOM", result.error or "")

        # TesterExecution is NOT failed - it remains in its state
        self.assertNotEqual(runtime.execution.status, TesterExecutionStatus.FAILED)
        self.assertEqual(runtime.status, TestRuntimeStatus.RUNNING)

    # ----------------------------------------------------------------------
    # 18. No Automatic Retries
    # ----------------------------------------------------------------------
    def test_no_automatic_retry(self) -> None:
        """Verify failed interactions are executed exactly once without automatic retry loops."""
        runtime, session = self._create_runtime(
            simulate_action_failure={"click": "Transient click interception"}
        )
        runtime.start()

        result = runtime.click("#flaky-button")

        self.assertEqual(result.status, InteractionStatus.FAILED)

        # Underlying driver was invoked exactly once
        self.assertEqual(len(session.recorded_interactions), 1)

    # ----------------------------------------------------------------------
    # 19. Project and Execution Ownership Validation
    # ----------------------------------------------------------------------
    def test_project_execution_ownership(self) -> None:
        """Verify session ownership matches runtime and execution lineage."""
        runtime, session = self._create_runtime()

        self.assertEqual(runtime.project_id, self.project_id)
        self.assertEqual(runtime.work_order_id, self.work_order_id)
        self.assertEqual(runtime.execution_id, self.execution_id)

        # Validate that mismatched session is rejected on ownership check
        foreign_session = MockBrowserSession(
            project_id="other-project",
            work_order_id=self.work_order_id,
            execution_id=self.execution_id,
            runtime_id=self.runtime_id,
        )
        with self.assertRaises(Exception):
            foreign_session.validate_ownership(
                project_id=self.project_id,
                work_order_id=self.work_order_id,
                execution_id=self.execution_id,
                runtime_id=self.runtime_id,
            )

    # ----------------------------------------------------------------------
    # 20. Event Provenance
    # ----------------------------------------------------------------------
    def test_event_provenance(self) -> None:
        """Verify TEST_ACTION_* events carry full causal provenance and IDs."""
        runtime, session = self._create_runtime()
        runtime.start()

        result = runtime.click("#nav-home")

        # Find TEST_ACTION_STARTED and TEST_ACTION_COMPLETED events
        started = [e for e in self.emitted_events if e.event_type == EventType.TEST_ACTION_STARTED]
        completed = [e for e in self.emitted_events if e.event_type == EventType.TEST_ACTION_COMPLETED]

        self.assertEqual(len(started), 1)
        self.assertEqual(len(completed), 1)

        st_event = started[0]
        self.assertEqual(st_event.source, EventSource.WORKER)
        self.assertEqual(st_event.project_id, self.project_id)
        self.assertEqual(st_event.payload.get("execution_id"), self.execution_id)
        self.assertEqual(st_event.payload.get("work_order_id"), self.work_order_id)
        self.assertEqual(st_event.payload.get("action_id"), result.action_id)
        self.assertEqual(st_event.payload.get("action_type"), "CLICK")

        cmp_event = completed[0]
        self.assertEqual(cmp_event.source, EventSource.WORKER)
        self.assertEqual(cmp_event.payload.get("action_id"), result.action_id)


if __name__ == "__main__":
    unittest.main()
