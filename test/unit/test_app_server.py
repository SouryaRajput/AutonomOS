"""In-process and unit tests for AutonomOS Production HTTP & SSE Server."""
import io
import json
import os
import shutil
import tempfile
import unittest
from unittest.mock import MagicMock

from app.application import AutonomOSApp
from app.server import AutonomOSRequestHandler


class FakeSocket:
    """Mock socket for in-memory HTTP handler testing."""
    def __init__(self, data: bytes):
        self._file = io.BytesIO(data)

    def makefile(self, mode: str = "r", bufsize: int = -1):
        return self._file


class TestAppServer(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.temp_dir, "server_test.db")
        self.app = AutonomOSApp.with_sqlite(self.db_path)
        self.app._runtime.register_default_specialist_workers()

    def tearDown(self):
        self.app.close()
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def _execute_request(self, method: str, path: str, body: dict = None) -> tuple[int, dict]:
        """Execute request in-memory through AutonomOSRequestHandler without socket binds."""
        body_bytes = json.dumps(body).encode("utf-8") if body else b""

        rfile = io.BytesIO(body_bytes)
        wfile = io.BytesIO()

        # Build mock server & handler
        mock_server = MagicMock()
        mock_server.app = self.app

        handler = AutonomOSRequestHandler.__new__(AutonomOSRequestHandler)
        handler.rfile = rfile
        handler.wfile = wfile
        handler.server = mock_server
        handler.headers = {
            "Content-Length": str(len(body_bytes)),
            "Content-Type": "application/json",
        }
        handler.path = path
        handler.command = method
        handler.request_version = "HTTP/1.1"
        handler.close_connection = True

        # Intercept status code
        status_box = [200]
        def send_response(code, message=None):
            status_box[0] = code
        handler.send_response = send_response
        handler.send_header = MagicMock()
        handler.end_headers = MagicMock()

        if method == "GET":
            handler.do_GET()
        elif method == "POST":
            handler.do_POST()
        elif method == "OPTIONS":
            handler.do_OPTIONS()

        wfile.seek(0)
        output_bytes = wfile.getvalue()
        try:
            resp_data = json.loads(output_bytes.decode("utf-8")) if output_bytes else {}
        except Exception:
            resp_data = {"raw": output_bytes.decode("utf-8", errors="replace")}

        return status_box[0], resp_data

    def test_1_health_and_version(self):
        status, health = self._execute_request("GET", "/health")
        self.assertEqual(status, 200)
        self.assertEqual(health["status"], "ok")

        status, version = self._execute_request("GET", "/api/version")
        self.assertEqual(status, 200)
        self.assertEqual(version["application_version"], "1.0.0")

    def test_2_projects_crud(self):
        # Create Project
        status, proj = self._execute_request("POST", "/api/projects", {
            "name": "Server API Project",
            "root_path": self.temp_dir,
            "description": "Testing live HTTP server",
        })
        self.assertEqual(status, 201)
        project_id = proj["id"]

        # List Projects
        status, projects = self._execute_request("GET", "/api/projects")
        self.assertEqual(status, 200)
        self.assertGreaterEqual(len(projects), 1)

        # Get Project by ID
        status, fetched = self._execute_request("GET", f"/api/projects/{project_id}")
        self.assertEqual(status, 200)
        self.assertEqual(fetched["name"], "Server API Project")

    def test_3_tasks_and_specialist_workers(self):
        # Create Project
        status, proj = self._execute_request("POST", "/api/projects", {
            "name": "Task Test Proj",
            "root_path": self.temp_dir,
        })
        pid = proj["id"]

        # Create Task
        status, task = self._execute_request("POST", "/api/tasks", {
            "project_id": pid,
            "title": "HTTP Task",
            "objective": "Testing HTTP Task Dispatch",
        })
        self.assertEqual(status, 201)

        # List Tasks
        status, tasks = self._execute_request("GET", f"/api/tasks?project_id={pid}")
        self.assertEqual(status, 200)
        self.assertEqual(len(tasks), 1)

        # Check Specialists Workers Registered
        status, workers = self._execute_request("GET", "/api/workers")
        self.assertEqual(status, 200)
        worker_ids = [w["id"] for w in workers]
        self.assertIn("worker.programmer", worker_ids)
        self.assertIn("worker.researcher", worker_ids)
        self.assertIn("worker.tester", worker_ids)

    def test_4_conversations_and_messages(self):
        status, proj = self._execute_request("POST", "/api/projects", {
            "name": "Chat Proj",
            "root_path": self.temp_dir,
        })
        pid = proj["id"]

        # Get or create active conversation
        status, conv = self._execute_request("GET", f"/api/conversations/active?project_id={pid}")
        self.assertEqual(status, 200)
        conv_id = conv["id"]

        # Post Message
        status, msgs = self._execute_request("POST", f"/api/conversations/{conv_id}/messages", {
            "content": "Hello AutonomOS Server",
            "dispatch_manager": False,
        })
        self.assertEqual(status, 200)
        self.assertGreaterEqual(len(msgs), 1)
        self.assertEqual(msgs[0]["content"], "Hello AutonomOS Server")

    def test_5_emergency_stop_and_clear(self):
        status, proj = self._execute_request("POST", "/api/projects", {
            "name": "Safety Proj",
            "root_path": self.temp_dir,
        })
        pid = proj["id"]

        # Trigger Stop
        status, stop_res = self._execute_request("POST", f"/api/policies/{pid}/emergency_stop", {
            "reason": "Test safety stop",
        })
        self.assertEqual(status, 200)
        self.assertTrue(stop_res["stopped"])

        # Clear Stop
        status, clear_res = self._execute_request("POST", f"/api/policies/{pid}/clear_emergency_stop", {})
        self.assertEqual(status, 200)
        self.assertFalse(clear_res["stopped"])


if __name__ == "__main__":
    unittest.main()
