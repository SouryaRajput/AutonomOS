from __future__ import annotations

import json
import tempfile
import unittest

from core.enums import TaskStatus
from core.events.types import EventType
from core.inference.provider import MockProvider
from core.runtime.workforce_runtime import WorkforceRuntime
from core.storage.sqlite_store import SQLiteStore
from core.tools.builtins.web import MockWebAdapter, WebTool
from workers.researcher.worker import ResearcherWorker


class TestResearcherManagerIntegration(unittest.TestCase):
    """Integration test verifying Manager autonomous orchestration with the Researcher specialist worker."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.store = SQLiteStore(":memory:")
        self.runtime = WorkforceRuntime(self.store)
        self.project = self.runtime.create_project(
            name="Vector DB Selection",
            root_path=self.temp_dir.name,
            description="Select and deploy vector database for semantic search",
        )

        self.mock_web_data = {
            "qdrant vector database python": [
                {
                    "title": "Qdrant Vector Database Documentation",
                    "url": "https://qdrant.tech/documentation/",
                    "snippet": "Qdrant is an open-source vector database written in Rust with official Python SDK.",
                }
            ],
            "https://qdrant.tech/documentation/": "Qdrant provides HNSW indexing, payload filtering, and high throughput Python SDK.",
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

        # Register Researcher Worker
        self.researcher = ResearcherWorker("worker.researcher.spec")
        self.runtime.register_worker(self.researcher)

        provider = self.runtime.inference.providers.get_provider("mock-provider")
        if isinstance(provider, MockProvider):
            self.mock_provider = provider

    def tearDown(self):
        self.runtime.close()
        self.temp_dir.cleanup()

    def test_manager_dispatches_researcher_and_acts_on_findings(self):
        # 1. Manager Cycle 1: Formulate Plan and Dispatch Research Task
        mgr_cycle_1_decision = {
            "reasoning_summary": "Initial project kickoff. Decomposing objective into research task.",
            "plan_update": {
                "objective": "Select and integrate vector database",
                "milestones": ["Research Vector DB Options", "Deploy Chosen DB"],
                "reason": "Initial decomposition",
            },
            "actions": [
                {
                    "action_type": "CREATE_TASK",
                    "parameters": {
                        "title": "Research Vector DB Options",
                        "objective": "Evaluate Qdrant capabilities and Python SDK support",
                        "priority": 90,
                        "risk": "LOW",
                    },
                    "rationale": "Gather evidence before architectural commitment",
                },
                {
                    "action_type": "ASSIGN_TASK",
                    "parameters": {
                        "task_id": "task-will-be-resolved",  # Dynamic
                        "worker_id": "worker.researcher.spec",
                    },
                    "rationale": "Assign to specialist researcher",
                },
            ],
        }

        # First, let Manager create the task
        self.mock_provider.set_canned_response(
            json.dumps({
                "reasoning_summary": "Create research task.",
                "plan_update": {
                    "objective": "Select and integrate vector database",
                    "milestones": ["Research Vector DB Options", "Deploy Chosen DB"],
                    "reason": "Initial decomposition",
                },
                "actions": [
                    {
                        "action_type": "CREATE_TASK",
                        "parameters": {
                            "title": "Research Vector DB Options",
                            "objective": "Evaluate Qdrant capabilities and Python SDK support",
                            "priority": 90,
                            "risk": "LOW",
                        },
                        "rationale": "Gather evidence before architectural commitment",
                    }
                ],
            })
        )
        res_m1 = self.runtime.step_manager(self.project.id)
        self.assertTrue(res_m1.progress_detected)

        tasks = self.runtime.tasks.list_tasks(self.project.id)
        self.assertEqual(len(tasks), 1)
        research_task = tasks[0]

        # Manager assigns task
        self.mock_provider.set_canned_response(
            json.dumps({
                "reasoning_summary": "Assign task to researcher.",
                "actions": [
                    {
                        "action_type": "ASSIGN_TASK",
                        "parameters": {
                            "task_id": research_task.id,
                            "worker_id": "worker.researcher.spec",
                        },
                        "rationale": "Assign to specialist researcher",
                    }
                ],
            })
        )
        res_m2 = self.runtime.step_manager(self.project.id)
        self.assertTrue(res_m2.progress_detected)

        # 2. Researcher executes task
        researcher_synthesis = {
            "reasoning_summary": "Qdrant evaluated. Fast Rust core, official Python SDK, HNSW indexing.",
            "questions_resolved": [
                {"question_id": "q-1", "status": "ANSWERED", "notes": "Python SDK available"}
            ],
            "findings": [
                {
                    "finding_id": "f-1",
                    "claim": "Qdrant provides an official asynchronous Python SDK with HNSW indexing.",
                    "classification": "FACT",
                    "confidence": "WELL_SUPPORTED",
                    "source_ids": ["src-1"],
                    "reasoning": "Official Qdrant docs confirm async client support.",
                }
            ],
            "contradictions": [],
            "knowledge_gaps": [],
            "recommendations": [
                {
                    "action": "Adopt Qdrant as primary vector database.",
                    "rationale": "Strong Python SDK and HNSW performance.",
                    "supporting_finding_ids": ["f-1"],
                }
            ],
            "manager_summary": "- Qdrant official Python SDK verified.\n- Recommended: Adopt Qdrant.",
        }
        self.mock_provider.set_canned_response(json.dumps(researcher_synthesis))

        worker_out = self.runtime.run_task(research_task.id)
        self.assertTrue(worker_out.success)

        task_completed = self.runtime.tasks.get_task(research_task.id)
        self.assertEqual(task_completed.status, TaskStatus.COMPLETED)

        # 3. Manager Cycle 3: Consumes Research and Marks Project/Milestone
        self.mock_provider.set_canned_response(
            json.dumps({
                "reasoning_summary": "Research task completed. Adopting Qdrant based on findings.",
                "actions": [
                    {
                        "action_type": "UPDATE_MEMORY",
                        "parameters": {
                            "memory_type": "ARCHITECTURE",
                            "title": "ADR: Adopt Qdrant Vector DB",
                            "content": "Decision: Adopt Qdrant as recommended by Researcher.",
                        },
                        "rationale": "Record architectural decision",
                    },
                    {
                        "action_type": "COMPLETE_PROJECT",
                        "parameters": {
                            "summary": "Vector database evaluation successfully completed with Qdrant selection."
                        },
                        "rationale": "Project objective accomplished",
                    },
                ],
            })
        )
        res_m3 = self.runtime.step_manager(self.project.id)
        self.assertTrue(res_m3.progress_detected)

        # Verify ADR recorded in memory
        memories = self.runtime.memory.list_memories(self.project.id)
        adr_mems = [m for m in memories if "Qdrant" in m.title]
        self.assertTrue(len(adr_mems) >= 1)


if __name__ == "__main__":
    unittest.main()
