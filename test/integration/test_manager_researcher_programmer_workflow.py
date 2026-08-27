from __future__ import annotations

import json
import tempfile
import unittest

from core.enums import ProjectStatus, TaskStatus
from core.events.types import EventType
from core.inference.provider import MockProvider
from core.manager.types import PlanStatus
from core.runtime.workforce_runtime import WorkforceRuntime
from core.storage.sqlite_store import SQLiteStore
from core.tools.builtins.web import MockWebAdapter, WebTool
from workers.programmer.worker import ProgrammerWorker
from workers.researcher.worker import ResearcherWorker


class TestManagerResearcherProgrammerWorkflow(unittest.TestCase):
    """
    End-to-end multi-agent orchestration workflow test:
    Manager Orchestrator -> Researcher Specialist -> Research Artifact -> Manager -> Programmer Specialist -> Implementation -> Verification -> Project Completion.
    """

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.store = SQLiteStore(":memory:")
        self.runtime = WorkforceRuntime(self.store)
        self.project = self.runtime.create_project(
            name="Autonomous Auth Workforce",
            root_path=self.temp_dir.name,
            description="Autonomous multi-worker authentication module construction",
        )

        # Web mock data for Researcher
        self.mock_web_data = {
            "password hashing algorithm comparison": [
                {
                    "title": "OWASP Password Storage Cheat Sheet",
                    "url": "https://cheatsheetseries.owasp.org/cheatsheets/Password_Storage_Cheat_Sheet.html",
                    "snippet": "Argon2id is the recommended algorithm for password hashing.",
                }
            ],
            "https://cheatsheetseries.owasp.org/cheatsheets/Password_Storage_Cheat_Sheet.html": (
                "Argon2id provides memory-hard hashing resistant to GPU cracking."
            ),
        }
        mock_web_adapter = MockWebAdapter(self.mock_web_data)

        web_tool = self.runtime.tools.registry.get_tool("web")
        if isinstance(web_tool, WebTool):
            web_tool.set_adapter(mock_web_adapter)
        web_search = self.runtime.tools.registry.get_tool("web.search")
        if isinstance(web_search, WebTool):
            web_search.set_adapter(mock_web_adapter)
        web_fetch = self.runtime.tools.registry.get_tool("web.fetch")
        if isinstance(web_fetch, WebTool):
            web_fetch.set_adapter(mock_web_adapter)

        # Register Specialist Workers
        self.researcher = ResearcherWorker("worker.researcher.flow")
        self.programmer = ProgrammerWorker("worker.programmer.flow")
        self.runtime.register_worker(self.researcher)
        self.runtime.register_worker(self.programmer)

        provider = self.runtime.inference.providers.get_provider("mock-provider")
        if isinstance(provider, MockProvider):
            self.mock_provider = provider

    def tearDown(self):
        self.runtime.close()
        self.temp_dir.cleanup()

    def test_full_manager_researcher_programmer_coordination(self):
        # -------------------------------------------------------------
        # STEP 1: Manager Cycle 1 — Plan Creation & Researcher Task Dispatch
        # -------------------------------------------------------------
        # 1a. Manager creates research task
        self.mock_provider.set_canned_response(
            json.dumps({
                "reasoning_summary": "Creating project plan and dispatching research task to determine hashing algorithm.",
                "plan_update": {
                    "objective": "Deliver verified authentication module",
                    "milestones": ["Research & Selection", "Implementation & Verification"],
                    "reason": "Initial project breakdown",
                },
                "actions": [
                    {
                        "action_type": "CREATE_TASK",
                        "parameters": {
                            "title": "Research recommended password hashing algorithms",
                            "objective": "Investigate recommended password hashing algorithm for web service",
                            "priority": 90,
                            "risk": "LOW",
                            "metadata": {"mode": "STANDARD"},
                        },
                        "rationale": "Gather evidence before implementing cryptographic code.",
                    }
                ],
            })
        )
        res_m1 = self.runtime.step_manager(self.project.id)
        self.assertTrue(res_m1.progress_detected)

        tasks = self.runtime.tasks.list_tasks(self.project.id)
        self.assertEqual(len(tasks), 1)
        research_task = tasks[0]

        # 1b. Manager assigns research task
        self.mock_provider.set_canned_response(
            json.dumps({
                "reasoning_summary": "Assign task to researcher specialist.",
                "actions": [
                    {
                        "action_type": "ASSIGN_TASK",
                        "parameters": {
                            "task_id": research_task.id,
                            "worker_id": "worker.researcher.flow",
                        },
                        "rationale": "Assign to researcher",
                    }
                ],
            })
        )
        res_m2 = self.runtime.step_manager(self.project.id)
        self.assertTrue(res_m2.progress_detected)

        # -------------------------------------------------------------
        # STEP 2: Researcher Executes Research Task
        # -------------------------------------------------------------
        r_synthesis_response = {
            "reasoning_summary": "OWASP explicitly recommends Argon2id for password hashing.",
            "questions": [
                {
                    "id": "q1",
                    "question": "What is the recommended password hashing algorithm?",
                    "status": "ANSWERED",
                    "priority": 1,
                    "finding_ids": ["f1"],
                }
            ],
            "findings": [
                {
                    "id": "f1",
                    "claim": "Argon2id is the primary OWASP recommendation for password storage.",
                    "classification": "FACT",
                    "confidence": "WELL_SUPPORTED",
                    "source_ids": ["src_1"],
                    "reasoning": "Standard security baseline recommendation from official OWASP documentation.",
                }
            ],
            "contradictions": [],
            "knowledge_gaps": [],
            "recommendations": [
                {
                    "id": "rec1",
                    "proposal": "Implement Argon2id / hashlib fallback with salt and iterations.",
                    "rationale": "High resistance against offline brute-force attacks.",
                    "finding_ids": ["f1"],
                }
            ],
            "manager_summary": "OWASP recommends Argon2id as the state of the art.",
        }

        self.mock_provider.set_canned_response(json.dumps(r_synthesis_response))
        r_output = self.runtime.run_task(research_task.id)
        self.assertTrue(r_output.success)

        # -------------------------------------------------------------
        # STEP 3: Manager Cycle 2 — Creates & Assigns Programming Task
        # -------------------------------------------------------------
        # 3a. Manager creates programming task
        self.mock_provider.set_canned_response(
            json.dumps({
                "reasoning_summary": "Research completed. Creating programming task with PBKDF2/Argon2 salt recommendation.",
                "actions": [
                    {
                        "action_type": "CREATE_TASK",
                        "parameters": {
                            "title": "Implement PasswordHasher service",
                            "objective": "Implement PasswordHasher with secure hashing and salting",
                            "priority": 85,
                            "risk": "MEDIUM",
                            "metadata": {
                                "mode": "FEATURE",
                                "allowed_paths": ["src/hasher.py", "tests/test_hasher.py"],
                                "relevant_research": ["Argon2id/SHA-256 with salt recommended by OWASP."],
                            },
                        },
                        "rationale": "Implement code based on researcher evidence.",
                    }
                ],
            })
        )
        res_m3 = self.runtime.step_manager(self.project.id)
        self.assertTrue(res_m3.progress_detected)

        all_tasks = self.runtime.tasks.list_tasks(self.project.id)
        prog_task = next(t for t in all_tasks if t.id != research_task.id)

        # 3b. Manager assigns programming task
        self.mock_provider.set_canned_response(
            json.dumps({
                "reasoning_summary": "Assign programming task to programmer specialist.",
                "actions": [
                    {
                        "action_type": "ASSIGN_TASK",
                        "parameters": {
                            "task_id": prog_task.id,
                            "worker_id": "worker.programmer.flow",
                        },
                        "rationale": "Assign to programmer",
                    }
                ],
            })
        )
        res_m4 = self.runtime.step_manager(self.project.id)
        self.assertTrue(res_m4.progress_detected)

        # -------------------------------------------------------------
        # STEP 4: Programmer Executes Implementation Task
        # -------------------------------------------------------------
        p_synthesis_response = {
            "reasoning_summary": "Implemented PasswordHasher using hashlib and salt per research guidance.",
            "file_operations": [
                {
                    "action": "WRITE",
                    "path": "src/hasher.py",
                    "content": (
                        "import hashlib\nimport os\n\n"
                        "class PasswordHasher:\n"
                        "    def hash_password(self, password: str, salt: bytes | None = None) -> tuple[str, str]:\n"
                        "        salt_bytes = salt or os.urandom(16)\n"
                        "        dk = hashlib.pbkdf2_hmac('sha256', password.encode(), salt_bytes, 100000)\n"
                        "        return dk.hex(), salt_bytes.hex()\n"
                    ),
                    "description": "PasswordHasher implementation with PBKDF2-HMAC-SHA256 and salt",
                },
                {
                    "action": "WRITE",
                    "path": "tests/test_hasher.py",
                    "content": (
                        "import unittest\n"
                        "from src.hasher import PasswordHasher\n\n"
                        "class TestPasswordHasher(unittest.TestCase):\n"
                        "    def test_hash_password(self):\n"
                        "        h = PasswordHasher()\n"
                        "        hash_val, salt = h.hash_password('secret')\n"
                        "        self.assertEqual(len(hash_val), 64)\n"
                    ),
                    "description": "Unit tests for PasswordHasher",
                },
            ],
            "tests_to_run": [
                "echo 'Running password hasher tests... 1 passed'",
            ],
            "assumptions": ["PBKDF2-HMAC-SHA256 standard library compliance"],
            "warnings": [],
            "self_review": {
                "checks_passed": True,
                "modified_files_in_scope": True,
                "tests_added_or_updated": True,
                "regression_risk": "LOW",
            },
            "manager_summary": "Implemented PasswordHasher with salting and PBKDF2. Tests passed.",
        }

        self.mock_provider.set_canned_response(json.dumps(p_synthesis_response))
        p_output = self.runtime.run_task(prog_task.id)
        self.assertTrue(p_output.success)

        # -------------------------------------------------------------
        # STEP 5: Manager Cycle 3 — Verification & Project Completion
        # -------------------------------------------------------------
        self.mock_provider.set_canned_response(
            json.dumps({
                "reasoning_summary": "All tasks completed and verified. Marking project complete.",
                "actions": [
                    {
                        "action_type": "COMPLETE_PROJECT",
                        "parameters": {"project_id": self.project.id},
                        "rationale": "All milestone criteria satisfied with concrete evidence.",
                    }
                ],
            })
        )
        res_m5 = self.runtime.step_manager(self.project.id)
        self.assertTrue(res_m5.completed)

        plan = self.runtime.manager.get_active_plan(self.project.id)
        self.assertIsNotNone(plan)
        self.assertEqual(plan.status, PlanStatus.COMPLETED)


if __name__ == "__main__":
    unittest.main()
