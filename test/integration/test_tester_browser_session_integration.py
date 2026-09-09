from __future__ import annotations

import http.server
import socketserver
import threading
import time
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
from core.tester.contracts.scope import TestScope
from core.tester.contracts.session import HttpBrowserSession
from core.tester.contracts.work_order import TesterWorkOrder
from core.tester.errors import NavigationDeniedError, TesterError
from core.tester.types import (
    EnvironmentType,
    TestActionStatus,
    TestCategory,
    TestRuntimeStatus,
    TestingCapability,
)


class LocalTestAppHandler(http.server.BaseHTTPRequestHandler):
    """Minimal deterministic local HTTP application for integration testing."""

    def log_message(self, format, *args):
        # Silence HTTP server stdout logging during test runs
        pass

    def do_GET(self):
        if self.path in ("/", "/index.html"):
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(b"<html><head><title>Local App - Home</title></head><body><h1>Welcome</h1></body></html>")
        elif self.path == "/dashboard":
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(b"<html><head><title>Local App - Dashboard</title></head><body><h1>Dashboard</h1></body></html>")
        elif self.path == "/old-home":
            # 302 Redirect to /
            self.send_response(302)
            self.send_header("Location", "/")
            self.end_headers()
        elif self.path == "/error":
            self.send_response(500)
            self.send_header("Content-Type", "text/plain")
            self.end_headers()
        else:
            self.send_response(404)
            self.send_header("Content-Type", "text/plain")
            self.end_headers()
            self.wfile.write(b"Not Found")


def handle_local_app_request(path: str) -> tuple[int, dict[str, str], str]:
    """Deterministic local HTTP application route handler."""
    if path in ("/", "/index.html"):
        return 200, {"Content-Type": "text/html"}, "<html><head><title>Local App - Home</title></head><body><h1>Welcome</h1></body></html>"
    elif path == "/dashboard":
        return 200, {"Content-Type": "text/html"}, "<html><head><title>Local App - Dashboard</title></head><body><h1>Dashboard</h1></body></html>"
    elif path == "/old-home":
        return 302, {"Location": "/"}, ""
    elif path == "/error":
        return 500, {"Content-Type": "text/plain"}, "Internal Server Error"
    else:
        return 404, {"Content-Type": "text/plain"}, "Not Found"


class TestTesterBrowserSessionIntegration(unittest.TestCase):
    """
    Integration test verifying the end-to-end flow:
    TestEnvironment -> TestRuntime -> Browser Session -> configured local app -> successful navigation -> clean shutdown.
    """

    @classmethod
    def setUpClass(cls):
        # Start ephemeral local HTTP server
        cls.server = socketserver.TCPServer(("127.0.0.1", 0), LocalTestAppHandler)
        cls.server_port = cls.server.server_address[1]
        cls.server_url = f"http://127.0.0.1:{cls.server_port}"

        cls.server_thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.server_thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.server_thread.join(timeout=2.0)

    def setUp(self):
        self.project_id = "proj-browser-integration-01"
        self.work_order_id = new_work_order_id()
        self.execution_id = new_execution_id()
        self.emitted_events: List[Event] = []

    def _event_sink(self, event: Event) -> None:
        self.emitted_events.append(event)

    def _create_work_order(self) -> TesterWorkOrder:
        return TesterWorkOrder(
            work_order_id=self.work_order_id,
            manager_task_id="mtask-integ-01",
            project_id=self.project_id,
            correlation_id="corr-integ-01",
            objective="Evaluate local checkout and dashboard flows",
            instructions=["Navigate through local test app routes"],
            product_artifact="local-app:v1",
            test_scope=TestScope(routes=["/", "/dashboard"]),
            test_categories=[TestCategory.FUNCTIONAL, TestCategory.SMOKE],
            authorized_capabilities=[TestingCapability.NAVIGATE],
            acceptance_criteria=[
                AcceptanceCriterion(
                    criterion_id="ac-local-01",
                    description="Home page loads with valid HTML",
                )
            ],
        )

    def _create_execution(self) -> TesterExecution:
        return TesterExecution(
            execution_id=self.execution_id,
            work_order_id=self.work_order_id,
            task_id="mtask-integ-01",
            project_id=self.project_id,
            correlation_id="corr-integ-01",
        )

    def test_end_to_end_navigation_and_shutdown(self) -> None:
        """
        Demonstrate full integration flow:
        TestEnvironment -> TestRuntime -> Browser Session -> configured local app -> navigation -> clean shutdown.
        """
        wo = self._create_work_order()
        execution = self._create_execution()

        env = TestEnvironment(
            project_id=self.project_id,
            work_order_id=self.work_order_id,
            execution_id=self.execution_id,
            application_url=self.server_url,
            environment_type=EnvironmentType.BROWSER,
        )

        session = HttpBrowserSession(
            project_id=self.project_id,
            work_order_id=self.work_order_id,
            execution_id=self.execution_id,
            runtime_id=new_runtime_id(),
            initial_url=self.server_url,
            app_handler=handle_local_app_request,
        )

        runtime = BrowserTestRuntime(
            execution=execution,
            work_order=wo,
            environment=env,
            session=session,
            runtime_id=session.runtime_id,
            event_sink=self._event_sink,
        )

        # 1. Startup
        runtime.start()
        self.assertEqual(runtime.status, TestRuntimeStatus.READY)
        self.assertTrue(session.is_ready)

        # 2. Navigate to Home (/)
        action_home = runtime.navigate("/")
        self.assertEqual(runtime.status, TestRuntimeStatus.RUNNING)
        self.assertEqual(action_home.status, TestActionStatus.SUCCESS)
        self.assertEqual(action_home.resulting_state.get("status_code"), 200)
        self.assertEqual(action_home.resulting_state.get("title"), "Local App - Home")
        self.assertTrue(runtime.current_url.endswith("/"))

        # 3. Navigate to /dashboard
        action_dash = runtime.navigate("/dashboard")
        self.assertEqual(action_dash.status, TestActionStatus.SUCCESS)
        self.assertEqual(action_dash.resulting_state.get("status_code"), 200)
        self.assertEqual(action_dash.resulting_state.get("title"), "Local App - Dashboard")
        self.assertTrue(runtime.current_url.endswith("/dashboard"))

        # 4. Navigate to redirect (/old-home -> /)
        action_redir = runtime.navigate("/old-home")
        self.assertEqual(action_redir.status, TestActionStatus.SUCCESS)
        self.assertEqual(action_redir.resulting_state.get("status_code"), 200)
        self.assertTrue(runtime.current_url.endswith("/"))

        # 5. Verify action records & audit trail
        self.assertEqual(len(runtime.action_records), 3)
        for act in runtime.action_records:
            self.assertEqual(act.execution_id, self.execution_id)
            self.assertEqual(act.runtime_id, runtime.runtime_id)
            self.assertIsNotNone(act.duration_ms())
            self.assertGreater(act.duration_ms(), 0.0)

        # 6. Verify emitted domain events
        event_types = [e.event_type for e in self.emitted_events]
        self.assertIn(EventType.TEST_RUNTIME_CREATED, event_types)
        self.assertIn(EventType.TEST_RUNTIME_STARTING, event_types)
        self.assertIn(EventType.TEST_RUNTIME_READY, event_types)
        self.assertIn(EventType.TEST_NAVIGATION_STARTED, event_types)
        self.assertIn(EventType.TEST_NAVIGATION_COMPLETED, event_types)

        # 7. Clean shutdown
        runtime.stop(reason="Integration test completed successfully")
        self.assertEqual(runtime.status, TestRuntimeStatus.STOPPED)
        self.assertTrue(session.is_closed)
        self.assertFalse(session.is_ready)

    def test_boundary_enforcement_against_external_origin(self) -> None:
        """Verify navigation outside the local test app origin is strictly blocked."""
        wo = self._create_work_order()
        execution = self._create_execution()
        env = TestEnvironment(
            project_id=self.project_id,
            work_order_id=self.work_order_id,
            execution_id=self.execution_id,
            application_url=self.server_url,
        )

        session = HttpBrowserSession(
            project_id=self.project_id,
            work_order_id=self.work_order_id,
            execution_id=self.execution_id,
            runtime_id=new_runtime_id(),
            initial_url=self.server_url,
            app_handler=handle_local_app_request,
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

        # External navigation must be denied
        with self.assertRaises(NavigationDeniedError):
            runtime.navigate("https://external-unrelated-domain.com")

        runtime.stop()

    def test_error_endpoint_recording(self) -> None:
        """Verify navigating to an endpoint returning HTTP 500 raises TesterError and records failure."""
        wo = self._create_work_order()
        execution = self._create_execution()
        env = TestEnvironment(
            project_id=self.project_id,
            work_order_id=self.work_order_id,
            execution_id=self.execution_id,
            application_url=self.server_url,
        )

        session = HttpBrowserSession(
            project_id=self.project_id,
            work_order_id=self.work_order_id,
            execution_id=self.execution_id,
            runtime_id=new_runtime_id(),
            initial_url=self.server_url,
            app_handler=handle_local_app_request,
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

        with self.assertRaises(TesterError):
            runtime.navigate("/error")

        self.assertEqual(len(runtime.action_records), 1)
        record = runtime.action_records[0]
        self.assertEqual(record.status, TestActionStatus.FAILED)
        self.assertIn("500", str(record.error))

        runtime.stop()


if __name__ == "__main__":
    unittest.main()
