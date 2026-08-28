"""AutonomOS Production HTTP & SSE Server.
Provides REST APIs and Server-Sent Events (SSE) streaming for Flutter UI and external clients.
Uses Python standard library (http.server, socketserver, threading) for zero external dependencies.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import threading
import time
from typing import Any, Optional
from http.server import HTTPServer, BaseHTTPRequestHandler
from socketserver import ThreadingMixIn
import urllib.parse

from app.application import AutonomOSApp
from app.dto.errors import AppException

logger = logging.getLogger("AutonomOS.Server")


class ThreadedHTTPServer(ThreadingMixIn, HTTPServer):
    """Multi-threaded HTTP Server handling concurrent REST requests and SSE streams."""
    daemon_threads = True
    allow_reuse_address = True


class AutonomOSRequestHandler(BaseHTTPRequestHandler):
    """HTTP Request Handler mapping REST routes and SSE streams to AutonomOSApp."""

    server_version = "AutonomOS-Server/1.0"

    def _send_cors_headers(self) -> None:
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, PUT, DELETE, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization, X-Requested-With")

    def do_OPTIONS(self) -> None:
        self.send_response(204)
        self._send_cors_headers()
        self.end_headers()

    def _send_json(self, status_code: int, data: Any) -> None:
        body = json.dumps(data, default=str).encode("utf-8")
        self.send_response(status_code)
        self._send_cors_headers()
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_error(self, status_code: int, error_code: str, message: str, user_message: str = "") -> None:
        self._send_json(status_code, {
            "error": {
                "code": error_code,
                "message": message,
                "user_message": user_message or message,
            }
        })

    def _parse_json_body(self) -> dict[str, Any]:
        content_length = int(self.headers.get("Content-Length", 0))
        if content_length > 0:
            raw = self.rfile.read(content_length).decode("utf-8")
            try:
                return json.loads(raw)
            except json.JSONDecodeError:
                return {}
        return {}

    @property
    def app(self) -> AutonomOSApp:
        return self.server.app  # type: ignore

    def do_GET(self) -> None:
        parsed_url = urllib.parse.urlparse(self.path)
        path = parsed_url.path
        query = urllib.parse.parse_qs(parsed_url.query)

        def q(key: str, default: str = "") -> str:
            val = query.get(key, [default])
            return val[0] if val else default

        try:
            # 1. Health & System Info
            if path == "/health" or path == "/":
                return self._send_json(200, {"status": "ok", "service": "AutonomOS", "version": "1.0.0"})

            if path == "/api/version":
                return self._send_json(200, self.app.version.get_system_version())

            # 2. SSE Event Stream
            if path == "/api/events/stream":
                return self._handle_sse_stream(query)

            # 3. Project Routes
            if path == "/api/projects":
                projects = self.app.projects.list_projects()
                return self._send_json(200, projects)

            if path.startswith("/api/projects/"):
                pid = path.split("/")[3]
                proj = self.app.projects.get_project(pid)
                return self._send_json(200, proj)

            # 4. Task Routes
            if path == "/api/tasks":
                pid = q("project_id") or None
                status = q("status") or None
                tasks = self.app.tasks.list_tasks(project_id=pid, status=status)
                return self._send_json(200, tasks)

            if path.startswith("/api/tasks/") and path.endswith("/timeline"):
                tid = path.split("/")[3]
                timeline = self.app.tasks.get_task_timeline(tid)
                return self._send_json(200, timeline)

            if path.startswith("/api/tasks/"):
                tid = path.split("/")[3]
                task = self.app.tasks.get_task(tid)
                return self._send_json(200, task)

            # 5. Worker Routes
            if path == "/api/workers":
                workers = self.app.workers.list_workers()
                return self._send_json(200, workers)

            if path.startswith("/api/workers/"):
                wid = path.split("/")[3]
                worker = self.app.workers.get_worker(wid)
                return self._send_json(200, worker)

            # 6. Conversation Routes
            if path == "/api/conversations/active":
                pid = q("project_id")
                conv = self.app.conversations.get_or_create_active_conversation(pid)
                return self._send_json(200, conv.to_dict())

            if path == "/api/conversations":
                pid = q("project_id")
                convs = self.app.conversations.list_conversations(pid)
                return self._send_json(200, [c.to_dict() for c in convs])

            if path.startswith("/api/conversations/"):
                cid = path.split("/")[3]
                conv = self.app.conversations.get_conversation(cid)
                return self._send_json(200, conv.to_dict())

            # 7. Workflow & Manager Routes
            if path == "/api/manager/status":
                pid = q("project_id")
                status = self.app.workflows.get_manager_status(pid)
                return self._send_json(200, status)

            if path == "/api/manager/plan":
                pid = q("project_id")
                plan = self.app.workflows.get_active_plan(pid)
                return self._send_json(200, plan)

            if path == "/api/workflows":
                pid = q("project_id")
                workflows = self.app.workflows.list_workflows(pid)
                return self._send_json(200, workflows)

            # 8. Approvals & Autonomy
            if path == "/api/approvals":
                pid = q("project_id")
                approvals = self.app.approvals.list_pending_approvals(pid)
                return self._send_json(200, approvals)

            if path == "/api/user_inputs":
                pid = q("project_id")
                inputs = self.app.approvals.list_pending_user_inputs(pid)
                return self._send_json(200, inputs)

            if path == "/api/decisions":
                pid = q("project_id")
                decisions = self.app.approvals.list_pending_decisions(pid)
                return self._send_json(200, decisions)

            if path.startswith("/api/policies/"):
                pid = path.split("/")[3]
                policy = self.app.policies.get_policy(pid)
                return self._send_json(200, policy)

            # 9. Providers & Telemetry
            if path == "/api/providers":
                providers = self.app.providers.list_providers()
                return self._send_json(200, providers)

            # 10. Artifacts & Evidence
            if path == "/api/artifacts":
                pid = q("project_id") or None
                tid = q("task_id") or None
                artifacts = self.app.artifacts.list_artifacts(project_id=pid, task_id=tid)
                return self._send_json(200, artifacts)

            if path.startswith("/api/artifacts/") and path.endswith("/content"):
                aid = path.split("/")[3]
                content = self.app.artifacts.get_artifact_content(aid)
                return self._send_json(200, {"artifact_id": aid, "content": content})

            if path.startswith("/api/artifacts/"):
                aid = path.split("/")[3]
                art = self.app.artifacts.get_artifact(aid)
                return self._send_json(200, art)

            # 11. Search & Storage
            if path == "/api/search":
                pid = q("project_id")
                query_str = q("q")
                results = self.app.search.search(pid, query_str)
                return self._send_json(200, results)

            if path == "/api/storage":
                pid = q("project_id")
                info = self.app.storage.get_storage_info(pid)
                return self._send_json(200, info)

            # 12. Activity Feed
            if path == "/api/activity":
                pid = q("project_id")
                limit = int(q("limit", "50"))
                if pid:
                    activity = self.app.events.get_project_activity(pid, limit=limit)
                else:
                    activity = self.app.events.get_activity_feed(limit=limit)
                return self._send_json(200, activity)

            return self._send_error(404, "NOT_FOUND", f"Route GET {path} not found.")

        except AppException as e:
            return self._send_error(400, e.error.code.value if hasattr(e.error.code, "value") else str(e.error.code), e.error.message, e.error.user_message)
        except Exception as e:
            logger.exception("GET Error handling %s", path)
            return self._send_error(500, "INTERNAL_ERROR", str(e))

    def do_POST(self) -> None:
        parsed_url = urllib.parse.urlparse(self.path)
        path = parsed_url.path
        body = self._parse_json_body()

        try:
            # 1. Project Operations
            if path == "/api/projects":
                proj = self.app.projects.create_project(
                    name=body.get("name", "New Project"),
                    root_path=body.get("root_path", os.getcwd()),
                    description=body.get("description", ""),
                )
                return self._send_json(201, proj)

            # 2. Task Operations
            if path == "/api/tasks":
                task = self.app.tasks.create_task(
                    project_id=body["project_id"],
                    title=body["title"],
                    objective=body.get("objective", ""),
                    priority=body.get("priority", 1),
                    risk=body.get("risk", "LOW"),
                )
                return self._send_json(201, task)

            if path.startswith("/api/tasks/") and path.endswith("/cancel"):
                tid = path.split("/")[3]
                reason = body.get("reason", "User cancelled")
                cancelled = self.app.tasks.cancel_task(tid, reason=reason)
                return self._send_json(200, cancelled)

            # 3. Conversation Messages
            if path.startswith("/api/conversations/") and path.endswith("/messages"):
                cid = path.split("/")[3]
                content = body.get("content", "")
                dispatch_mgr = body.get("dispatch_manager", True)
                msgs = self.app.conversations.post_user_message(
                    conversation_id=cid,
                    content=content,
                    dispatch_manager=dispatch_mgr,
                )
                return self._send_json(200, [m.to_dict() for m in msgs])

            # 4. Manager Stepper & Orchestration
            if path == "/api/manager/step":
                pid = body["project_id"]
                feedback = body.get("feedback")
                res = self.app.workflows.step_manager(pid, feedback=feedback)
                return self._send_json(200, res)

            if path == "/api/manager/orchestrate":
                pid = body["project_id"]
                max_cycles = body.get("max_cycles", 15)
                res = self.app.workflows.run_orchestration(pid, max_cycles=max_cycles)
                return self._send_json(200, res)

            # 5. Approvals & Autonomy
            if path.startswith("/api/approvals/") and path.endswith("/grant"):
                aid = path.split("/")[3]
                decided_by = body.get("decided_by", "User")
                granted = self.app.approvals.grant_approval(aid, decided_by=decided_by)
                return self._send_json(200, granted)

            if path.startswith("/api/approvals/") and path.endswith("/reject"):
                aid = path.split("/")[3]
                reason = body.get("reason", "")
                decided_by = body.get("decided_by", "User")
                rejected = self.app.approvals.reject_approval(aid, reason=reason, decided_by=decided_by)
                return self._send_json(200, rejected)

            if path.startswith("/api/user_inputs/") and path.endswith("/respond"):
                uid = path.split("/")[3]
                ans = body.get("answer", "")
                resp = self.app.approvals.respond_to_user_input(uid, answer=ans)
                return self._send_json(200, resp)

            if path.startswith("/api/decisions/") and path.endswith("/respond"):
                did = path.split("/")[3]
                chosen = body.get("chosen_option", "")
                rationale = body.get("rationale", "")
                resp = self.app.approvals.respond_to_decision(did, chosen_option=chosen, rationale=rationale)
                return self._send_json(200, resp)

            if path.startswith("/api/policies/") and path.endswith("/emergency_stop"):
                pid = path.split("/")[3]
                reason = body.get("reason", "Operator emergency stop")
                halt = self.app.policies.emergency_stop(pid, reason=reason)
                return self._send_json(200, halt)

            if path.startswith("/api/policies/") and path.endswith("/clear_emergency_stop"):
                pid = path.split("/")[3]
                res = self.app.policies.clear_emergency_stop(pid)
                return self._send_json(200, res)

            # 6. Provider Key Configuration
            if path.startswith("/api/providers/") and path.endswith("/key"):
                prov_id = path.split("/")[3]
                key_val = body.get("key", "")
                res = self.app.providers.set_provider_key(prov_id, key_val)
                return self._send_json(200, res)

            return self._send_error(404, "NOT_FOUND", f"Route POST {path} not found.")

        except AppException as e:
            return self._send_error(400, e.error.code.value if hasattr(e.error.code, "value") else str(e.error.code), e.error.message, e.error.user_message)
        except Exception as e:
            logger.exception("POST Error handling %s", path)
            return self._send_error(500, "INTERNAL_ERROR", str(e))

    def _handle_sse_stream(self, query: dict[str, list[str]]) -> None:
        """Handle real-time Server-Sent Events (SSE) stream subscription."""
        self.send_response(200)
        self._send_cors_headers()
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.end_headers()

        project_id = query.get("project_id", [None])[0]
        event_queue: list[dict[str, Any]] = []
        lock = threading.Lock()
        active = True

        def on_event(evt) -> None:
            if not active:
                return
            e_dict = evt.to_dict() if hasattr(evt, "to_dict") else dict(evt)
            if not project_id or e_dict.get("project_id") == project_id:
                with lock:
                    event_queue.append(e_dict)

        self.app._runtime.subscribe_events(on_event)

        try:
            # Send initial connected heartbeat
            self.wfile.write(b"event: connected\ndata: {\"status\": \"streaming\"}\n\n")
            self.wfile.flush()

            while active:
                to_send = []
                with lock:
                    if event_queue:
                        to_send = list(event_queue)
                        event_queue.clear()

                for ev in to_send:
                    msg = f"event: autonomos_event\ndata: {json.dumps(ev, default=str)}\n\n".encode("utf-8")
                    self.wfile.write(msg)

                # Send heartbeat every 15s to keep connection open
                self.wfile.write(b": heartbeat\n\n")
                self.wfile.flush()
                time.sleep(1.0)

        except (BrokenPipeError, ConnectionResetError):
            pass
        finally:
            active = False


def create_server(app: AutonomOSApp, host: str = "127.0.0.1", port: int = 8000) -> ThreadedHTTPServer:
    server = ThreadedHTTPServer((host, port), AutonomOSRequestHandler)
    server.app = app  # type: ignore
    return server


def run_server(db_path: str = "autonomos.db", host: str = "127.0.0.1", port: int = 8000) -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    app = AutonomOSApp.with_sqlite(db_path)
    # Register real specialist AI workforce
    app._runtime.register_default_specialist_workers()

    server = create_server(app, host=host, port=port)
    print(f"🚀 AutonomOS Backend Server listening at http://{host}:{port}")
    print(f"📦 Embedded Database: {os.path.abspath(db_path)}")
    print(f"🌐 Active Providers: {[p.provider_id for p in app.providers.list_providers()]}")
    print(f"👥 Active Workforce: {[w['name'] for w in app.workers.list_workers()]}")

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping AutonomOS Server...")
    finally:
        server.server_close()
        app.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="AutonomOS Backend HTTP & SSE Server")
    parser.add_argument("--db", default="autonomos.db", help="Path to SQLite database")
    parser.add_argument("--host", default="127.0.0.1", help="Host address (default: 127.0.0.1)")
    parser.add_argument("--port", type=int, default=8000, help="Port (default: 8000)")
    args = parser.parse_args()

    run_server(db_path=args.db, host=args.host, port=args.port)
