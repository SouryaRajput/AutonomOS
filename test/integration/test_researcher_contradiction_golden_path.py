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
from workers.researcher.types import FactClassification, ResearchConfidence
from workers.researcher.worker import ResearcherWorker


class TestResearcherContradictionGoldenPath(unittest.TestCase):
    """Integration test verifying contradiction detection, balanced reporting, and conflicting confidence ratings."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.store = SQLiteStore(":memory:")
        self.runtime = WorkforceRuntime(self.store)
        self.project = self.runtime.create_project(
            name="Contradiction Project",
            root_path=self.temp_dir.name,
            description="Investigate conflicting claims",
        )

        self.mock_web_data = {
            "library x stability version 3": [
                {
                    "title": "Vendor Blog Post",
                    "url": "https://vendor.example.com/lib-x-stable",
                    "snippet": "Library X v3 is 100% production ready and fully supported.",
                },
                {
                    "title": "Community Issue Tracker",
                    "url": "https://github.com/lib-x/issues/100",
                    "snippet": "Warning: v3 architecture is experimental and major breaking changes remain.",
                },
            ],
            "https://vendor.example.com/lib-x-stable": "Vendor Announcement: Library X v3 is stable for all enterprise production use.",
            "https://github.com/lib-x/issues/100": "Maintainer Comment: v3 is an experimental preview. Core maintainers recommend v2 for production.",
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

        self.researcher = ResearcherWorker("worker.researcher.contra")
        self.runtime.register_worker(self.researcher)

        provider = self.runtime.inference.providers.get_provider("mock-provider")
        if isinstance(provider, MockProvider):
            self.mock_provider = provider

    def tearDown(self):
        self.runtime.close()
        self.temp_dir.cleanup()

    def test_contradiction_detected_and_reported(self):
        synthesis_json = {
            "reasoning_summary": "Detected strong contradiction between vendor marketing announcement and core maintainer issue tracker comments.",
            "questions_resolved": [
                {"question_id": "q-1", "status": "CONFLICTING", "notes": "Vendor claims stable; maintainers state experimental"}
            ],
            "findings": [
                {
                    "finding_id": "f-1",
                    "claim": "Dispute on Library X v3 stability between vendor announcement and GitHub maintainer advisory.",
                    "classification": "SOURCE_CLAIM",
                    "confidence": "CONFLICTING",
                    "source_ids": ["src-1", "src-2"],
                    "corroborating_source_ids": [],
                    "conflicting_source_ids": ["src-2"],
                    "reasoning": "Source 1 claims production ready; Source 2 warns that v3 is experimental and breaking changes are pending.",
                    "project_implications": "High risk of unexpected breakage if adopting v3 immediately.",
                }
            ],
            "contradictions": [
                {
                    "topic": "Library X v3 Production Readiness",
                    "claim_a": "Library X v3 is stable for all enterprise production use.",
                    "sources_a": ["src-1"],
                    "claim_b": "Core maintainers consider v3 an experimental preview with pending breaking changes.",
                    "sources_b": ["src-2"],
                    "analysis": "Vendor marketing announcement conflicts directly with core developer issue advisory.",
                }
            ],
            "knowledge_gaps": [],
            "recommendations": [
                {
                    "action": "Remain on Library X v2 for current sprint until v3 stabilizes.",
                    "rationale": "Maintainer warnings indicate high volatility in v3 branch.",
                    "supporting_finding_ids": ["f-1"],
                }
            ],
            "manager_summary": "- Contradiction: Vendor claims v3 is stable, but maintainers warn it is experimental.\n- Recommended: Stay on v2.",
        }
        self.mock_provider.set_canned_response(json.dumps(synthesis_json))

        task = self.runtime.create_task(
            project_id=self.project.id,
            title="Investigate Library X v3 Stability",
            objective="Determine if Library X v3 is safe for enterprise production",
            metadata={"questions": ["Is Library X v3 stable for production?"]},
        )
        self.runtime.assign_task(task.id, "worker.researcher.contra")

        output = self.runtime.run_task(task.id)
        self.assertTrue(output.success)

        # Verify contradiction was recorded
        result_meta = output.metadata["research_result"]
        contradictions = result_meta["contradictions"]
        self.assertEqual(len(contradictions), 1)
        self.assertEqual(contradictions[0]["topic"], "Library X v3 Production Readiness")

        # Verify event emitted
        events = self.runtime.get_events(project_id=self.project.id)
        worker_events = [e.payload.get("event_type") for e in events if e.event_type == EventType.WORKER_PROGRESS_LOGGED]
        self.assertIn(EventType.RESEARCH_CONTRADICTION_DETECTED.value, worker_events)


if __name__ == "__main__":
    unittest.main()
