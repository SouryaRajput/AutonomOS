from __future__ import annotations

from typing import List
import unittest

from core.events.model import Event
from core.events.types import EventSource, EventType
from core.tester.contracts.action import TestActionRecord
from core.tester.contracts.browser_runtime import BrowserTestRuntime
from core.tester.contracts.criteria import AcceptanceCriterion
from core.tester.contracts.environment import TestEnvironment
from core.tester.contracts.execution import TesterExecution
from core.tester.contracts.identifiers import (
    new_execution_id,
    new_runtime_id,
    new_work_order_id,
)
from core.tester.contracts.interaction import (
    InteractionResult,
    InteractionStatus,
    InteractionTarget,
)
from core.tester.contracts.scope import TestScope
from core.tester.contracts.session import HttpBrowserSession
from core.tester.contracts.work_order import TesterWorkOrder
from core.tester.types import (
    EnvironmentType,
    TestActionStatus,
    TestCategory,
    TestRuntimeStatus,
    TesterActionType,
    TesterExecutionStatus,
    TestingCapability,
)


def handle_interactive_app_request(path: str) -> tuple[int, dict[str, str], str]:
    """In-process mock web server for zero-socket deterministic interaction tests."""
    if path in ("/", "/login"):
        html = """
        <html>
            <head><title>Test App - Login</title></head>
            <body>
                <form id="login-form">
                    <input type="text" id="username" name="username" />
                    <input type="password" id="password" name="password" />
                    <button type="submit" id="login-btn">Sign In</button>
                </form>
            </body>
        </html>
        """
        return 200, {"Content-Type": "text/html"}, html.strip()
    elif path == "/dashboard":
        html = """
        <html>
            <head><title>Test App - Dashboard</title></head>
            <body>
                <h1>Welcome Alice</h1>
                <div id="content" style="height: 2000px;">Long Content</div>
                <button id="logout-btn">Log Out</button>
            </body>
        </html>
        """
        return 200, {"Content-Type": "text/html"}, html.strip()
    else:
        return 404, {"Content-Type": "text/plain"}, "Not Found"


class TestTesterInteractionIntegration(unittest.TestCase):
    """
    End-to-end integration test suite for Tester V1 Phase 2.3: Controlled Interaction Engine.
    
    Verifies full lifecycle with active session:
    1. TestEnvironment + TesterWorkOrder + TesterExecution setup
    2. Session initialization and runtime startup
    3. Multi-step interaction sequence:
       navigate -> type -> sensitive type -> press_key -> click -> scroll -> wait
    4. Audit history and state preservation
    5. Clean resource shutdown
    6. Non-crashing error semantics under authorization denial and unsupported operations
    """

    def setUp(self) -> None:
        self.project_id = "proj-interaction-integ-01"
        self.work_order_id = new_work_order_id()
        self.execution_id = new_execution_id()
        self.base_url = "http://127.0.0.1:8080"
        self.emitted_events: List[Event] = []

    def _event_sink(self, event: Event) -> None:
        self.emitted_events.append(event)

    def _create_work_order(
        self,
        authorized_capabilities: list[TestingCapability] | None = None,
    ) -> TesterWorkOrder:
        caps = authorized_capabilities or [
            TestingCapability.NAVIGATE,
            TestingCapability.CLICK,
            TestingCapability.TYPE,
            TestingCapability.KEYBOARD_INPUT,
            TestingCapability.SCROLL,
            TestingCapability.WAIT,
        ]
        return TesterWorkOrder(
            work_order_id=self.work_order_id,
            manager_task_id="mtask-interaction-integ-01",
            project_id=self.project_id,
            correlation_id="corr-interaction-integ-01",
            objective="Evaluate user authentication and dashboard navigation flows",
            instructions=[
                "Navigate to /login",
                "Enter credentials",
                "Click submit",
                "Scroll through dashboard",
            ],
            product_artifact="web-frontend:2.3.0",
            test_scope=TestScope(routes=["/login", "/dashboard"], features=["auth", "dashboard"]),
            test_categories=[TestCategory.FUNCTIONAL, TestCategory.INTEGRATION],
            authorized_capabilities=caps,
            acceptance_criteria=[
                AcceptanceCriterion(
                    criterion_id="ac-login-01",
                    description="User can enter credentials and sign in successfully",
                )
            ],
        )

    def _create_execution(self) -> TesterExecution:
        return TesterExecution(
            execution_id=self.execution_id,
            work_order_id=self.work_order_id,
            task_id="mtask-interaction-integ-01",
            project_id=self.project_id,
            correlation_id="corr-interaction-integ-01",
        )

    def test_end_to_end_interaction_flow(self) -> None:
        """
        Verify complete sequential interaction flow:
        start -> navigate -> type text -> type sensitive -> press key -> click -> scroll -> wait -> stop.
        """
        wo = self._create_work_order()
        execution = self._create_execution()

        env = TestEnvironment(
            project_id=self.project_id,
            work_order_id=self.work_order_id,
            execution_id=self.execution_id,
            application_url=self.base_url,
            environment_type=EnvironmentType.BROWSER,
        )

        session = HttpBrowserSession(
            project_id=self.project_id,
            work_order_id=self.work_order_id,
            execution_id=self.execution_id,
            runtime_id=new_runtime_id(),
            initial_url=self.base_url,
            app_handler=handle_interactive_app_request,
        )

        runtime = BrowserTestRuntime(
            execution=execution,
            work_order=wo,
            environment=env,
            session=session,
            runtime_id=session.runtime_id,
            event_sink=self._event_sink,
        )

        # 1. Start Runtime
        runtime.start()
        self.assertEqual(runtime.status, TestRuntimeStatus.READY)

        # 2. Navigate to /login
        nav_action = runtime.navigate("/login")
        self.assertEqual(runtime.status, TestRuntimeStatus.RUNNING)
        self.assertEqual(nav_action.status, TestActionStatus.SUCCESS)

        # 3. Enter Username (non-sensitive)
        res_user = runtime.type_text("#username", "alice_tester")
        self.assertEqual(res_user.status, InteractionStatus.SUCCESS)
        self.assertEqual(res_user.resulting_state.get("text_entered"), "alice_tester")
        self.assertEqual(session.form_state.get("#username"), "alice_tester")

        # 4. Enter Password (sensitive=True -> Redacted)
        secret = "P@ssw0rd!SuperSecret"
        res_pwd = runtime.type_text("#password", secret, sensitive=True)
        self.assertEqual(res_pwd.status, InteractionStatus.SUCCESS)
        self.assertTrue(res_pwd.sensitive)
        self.assertEqual(res_pwd.resulting_state.get("text_entered"), "[REDACTED]")
        self.assertEqual(session.form_state.get("#password"), "[REDACTED]")
        self.assertNotIn(secret, str(res_pwd.to_dict()))

        # 5. Press Enter Key
        res_key = runtime.press_key("Enter", target="#password")
        self.assertEqual(res_key.status, InteractionStatus.SUCCESS)
        self.assertEqual(res_key.action_type, TesterActionType.PRESS_KEY)

        # 6. Click Submit Button
        res_click = runtime.click("#login-btn")
        self.assertEqual(res_click.status, InteractionStatus.SUCCESS)
        self.assertEqual(res_click.action_type, TesterActionType.CLICK)

        # 7. Scroll Content
        res_scroll = runtime.scroll(direction="vertical", amount=250)
        self.assertEqual(res_scroll.status, InteractionStatus.SUCCESS)
        self.assertEqual(res_scroll.action_type, TesterActionType.SCROLL)
        self.assertEqual(session.scroll_offset, 250)

        # 8. Bounded Wait
        res_wait = runtime.wait(0.01)
        self.assertEqual(res_wait.status, InteractionStatus.SUCCESS)
        self.assertEqual(res_wait.action_type, TesterActionType.WAIT)

        # 9. Verify Audit History Count
        self.assertEqual(len(runtime.interaction_history), 6)

        # 10. Clean Stop
        runtime.stop()
        self.assertEqual(runtime.status, TestRuntimeStatus.STOPPED)
        self.assertTrue(session.is_closed)

    def test_unsupported_and_unauthorized_boundary_handling(self) -> None:
        """
        Verify that unsupported driver capabilities and unauthorized work order actions
        are deterministically rejected without aborting or crashing the runtime.
        """
        # WorkOrder authorizes only NAVIGATE and CLICK
        wo = self._create_work_order(
            authorized_capabilities=[TestingCapability.NAVIGATE, TestingCapability.CLICK]
        )
        execution = self._create_execution()

        env = TestEnvironment(
            project_id=self.project_id,
            work_order_id=self.work_order_id,
            execution_id=self.execution_id,
            application_url=self.base_url,
            environment_type=EnvironmentType.BROWSER,
        )

        session = HttpBrowserSession(
            project_id=self.project_id,
            work_order_id=self.work_order_id,
            execution_id=self.execution_id,
            runtime_id=new_runtime_id(),
            initial_url=self.base_url,
            app_handler=handle_interactive_app_request,
        )

        runtime = BrowserTestRuntime(
            execution=execution,
            work_order=wo,
            environment=env,
            session=session,
            runtime_id=session.runtime_id,
            event_sink=self._event_sink,
        )

        runtime.start()

        # 1. Unauthorized action (TYPE is not authorized by WorkOrder) -> DENIED
        res_unauthorized = runtime.type_text("#username", "unauthorized_input")
        self.assertEqual(res_unauthorized.status, InteractionStatus.DENIED)
        self.assertFalse(res_unauthorized.is_success)

        # 2. Unsupported driver action (MOVE_CURSOR is not supported by HttpBrowserSession) -> NOT_SUPPORTED
        # Note: If WO also didn't authorize it, WO check happens first. Let's create WO that authorizes MOVE_CURSOR
        # but HttpBrowserSession backend does not support it:
        wo_with_cursor = self._create_work_order(
            authorized_capabilities=[TestingCapability.NAVIGATE, TestingCapability.CLICK, TestingCapability.MOVE_CURSOR]
        )
        runtime.work_order = wo_with_cursor

        res_unsupported = runtime.move_cursor((100, 200))
        self.assertEqual(res_unsupported.status, InteractionStatus.NOT_SUPPORTED)
        self.assertFalse(res_unsupported.is_success)

        # 3. Authorized and supported action (CLICK) succeeds normally
        res_ok = runtime.click("#login-btn")
        self.assertEqual(res_ok.status, InteractionStatus.SUCCESS)

        # Execution is NOT failed despite the earlier denials
        self.assertNotEqual(execution.status, TesterExecutionStatus.FAILED)
        self.assertEqual(runtime.status, TestRuntimeStatus.RUNNING)

        runtime.stop()
        self.assertEqual(runtime.status, TestRuntimeStatus.STOPPED)


if __name__ == "__main__":
    unittest.main()
