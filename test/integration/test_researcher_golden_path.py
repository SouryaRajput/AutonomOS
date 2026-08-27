from __future__ import annotations

import json
import tempfile
import unittest
from core.enums import ArtifactType, TaskStatus
from core.events.types import EventType
from core.inference.provider import MockProvider
from core.runtime.workforce_runtime import WorkforceRuntime
from core.storage.sqlite_store import SQLiteStore
from core.tools.builtins.web import MockWebAdapter, WebTool
from workers.researcher.types import FactClassification, ResearchConfidence
from workers.researcher.worker import ResearcherWorker


class TestResearcherGoldenPath(unittest.TestCase):
    """End-to-end integration test for the Researcher specialist worker golden path."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.store = SQLiteStore(":memory:")
        self.runtime = WorkforceRuntime(self.store)
        self.project = self.runtime.create_project(
            name="Research Golden Project",
            root_path=self.temp_dir.name,
            description="Evaluate database options",
        )

        # Set up mock web adapter
        sqlite_results = [
            {
                "title": "SQLite WAL Mode Guide",
                "url": "https://www.sqlite.org/wal.html",
                "snippet": "Write-Ahead Logging allows concurrent readers and a writer.",
            }
        ]
        self.mock_web_data = {
            "sqlite concurrency wal mode": sqlite_results,
            "Does SQLite WAL mode allow concurrent reading and writing?": sqlite_results,
            "Does SQLite WAL mode allow concurrent reading and writing": sqlite_results,
            "Does SQLite WAL mode allow concurrent reading and writing? site:sqlite.org": sqlite_results,
            "Does SQLite WAL mode allow concurrent reading and writing site:sqlite.org": sqlite_results,
            "https://www.sqlite.org/wal.html": (
                "# Write-Ahead Logging\n\n"
                "WAL mode provides a massive concurrency boost. Readers do not block writers, "
                "and a writer does not block readers. Reading and writing can proceed concurrently."
            ),
        }
        mock_web_adapter = MockWebAdapter(self.mock_web_data)

        # Update runtime WebTool adapter
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
        self.researcher = ResearcherWorker("worker.researcher.golden")
        self.runtime.register_worker(self.researcher)

        # Configure Mock LLM provider canned response
        self.mock_provider = self.runtime.providers.get_provider("mock-provider")

    def tearDown(self):
        self.runtime.close()
        self.temp_dir.cleanup()

    def test_complete_researcher_golden_path(self):
        # 1. Prepare deterministic model synthesis response
        synthesis_json = {
            "reasoning_summary": "WAL mode documentation analyzed. Readers and writers proceed concurrently.",
            "questions_resolved": [
                {"question_id": "q-1", "status": "ANSWERED", "notes": "WAL mode concurrency verified"}
            ],
            "findings": [
                {
                    "finding_id": "f-1",
                    "claim": "SQLite WAL mode allows concurrent readers and a writer without blocking.",
                    "classification": "FACT",
                    "confidence": "WELL_SUPPORTED",
                    "source_ids": ["src-1"],
                    "reasoning": "Official SQLite WAL documentation explicitly states non-blocking reader/writer behavior.",
                    "project_implications": "Can be adopted for multi-threaded read-heavy local storage.",
                }
            ],
            "contradictions": [],
            "knowledge_gaps": [],
            "recommendations": [
                {
                    "action": "Enable PRAGMA journal_mode=WAL; for local project storage.",
                    "rationale": "Improves read concurrency while preserving transactional ACID integrity.",
                    "supporting_finding_ids": ["f-1"],
                    "risks": ["Requires shared memory (-shm) files in same directory."],
                    "tradeoffs": ["Slightly slower on pure read-only media."],
                }
            ],
            "manager_summary": "- SQLite WAL mode supports concurrent readers and writer.\n- Recommended: Enable WAL mode for high read concurrency.",
        }
        self.mock_provider.set_canned_response(json.dumps(synthesis_json))

        # 2. Create Task & Assign Researcher
        task = self.runtime.create_task(
            project_id=self.project.id,
            title="Research SQLite WAL Concurrency",
            objective="Analyze whether SQLite WAL mode allows concurrent readers and writers",
            metadata={
                "allowed_domains": ["sqlite.org"],
                "questions": ["Does SQLite WAL mode allow concurrent reading and writing?"],
            },
        )

        self.runtime.assign_task(task.id, "worker.researcher.golden")

        # 3. Execute Task
        output = self.runtime.run_task(task.id)
        self.assertTrue(output.success)

        # 4. Verify Task State
        completed_task = self.runtime.tasks.get_task(task.id)
        self.assertEqual(completed_task.status, TaskStatus.COMPLETED)

        # 5. Verify Artifact Created
        artifacts = self.runtime.artifacts.list_artifacts_for_task(task.id)
        self.assertTrue(len(artifacts) >= 1)
        report_art = artifacts[0]
        self.assertEqual(report_art.type, ArtifactType.REPORT)
        self.assertIn("Research report", report_art.description)

        events = self.runtime.get_events(project_id=self.project.id)
        event_types = [e.event_type for e in events]
        worker_events = [e.payload.get("event_type") for e in events if e.event_type == EventType.WORKER_PROGRESS_LOGGED]
        evidence_events = [e for e in events if e.event_type == EventType.EVIDENCE_RECORDED]

        # Verify Evidence Recorded
        self.assertTrue(len(evidence_events) >= 2)
        ev_types = [e.payload.get("evidence_type") for e in evidence_events]
        self.assertIn("RESEARCH_SOURCE_CITATION", ev_types)
        self.assertIn("RESEARCH_FINDING_VERIFICATION", ev_types)

        self.assertIn(EventType.RESEARCH_STARTED.value, worker_events)
        self.assertIn(EventType.RESEARCH_PLAN_CREATED.value, worker_events)
        self.assertIn(EventType.RESEARCH_SEARCH_PERFORMED.value, worker_events)
        self.assertIn(EventType.RESEARCH_SOURCE_FETCHED.value, worker_events)
        self.assertIn(EventType.RESEARCH_FINDING_CREATED.value, worker_events)
        self.assertIn(EventType.RESEARCH_COMPLETED.value, worker_events)
        self.assertIn(EventType.TASK_COMPLETED, event_types)


if __name__ == "__main__":
    unittest.main()
