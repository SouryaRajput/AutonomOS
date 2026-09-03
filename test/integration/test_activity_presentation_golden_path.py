"""
End-to-end Golden Path Acceptance Test for Workforce Activity Presentation Layer.
Verifies the complete lifecycle described in Section 25 of the specification:
"Research what I can do to improve the UX of this app."
"""
from __future__ import annotations

import unittest
import uuid

from app.application import AutonomOSApp
from app.services.message_sanitizer import extract_user_facing_narrative
from core.activity.model import ActivityStatus
from core.events.types import EventSource, EventType


class TestActivityPresentationGoldenPath(unittest.TestCase):
    def setUp(self):
        self.app = AutonomOSApp.with_sqlite(":memory:")
        self.proj = self.app.projects.create_project(
            name="Portfolio App",
            root_path="/tmp/portfolio",
            description="Personal Portfolio",
        )
        self.project_id = self.proj["id"]
        self.task_id = "task-research-ux-1"
        self.corr_id = "corr-golden-path-1"

    def tearDown(self):
        self.app.close()

    def test_complete_research_activity_lifecycle(self):
        # 1. User Prompt & Conversational Channel
        user_prompt = "Research what I can do to improve the UX of this app."
        raw_assistant_reply = (
            "I'll inspect the current project first, then research UX improvements "
            "tailored to its architecture.\n"
            "```json\n"
            '{"action": "read_file", "path": "package.json"}\n'
            "```\n"
            "I've initialized the Researcher workforce."
        )

        # Verification 1: Channel Separation (No tool JSON in chat narrative)
        clean_narrative, tools = extract_user_facing_narrative(raw_assistant_reply)
        self.assertNotIn('{"action": "read_file"', clean_narrative)
        self.assertIn("I'll inspect the current project first", clean_narrative)
        self.assertEqual(len(tools), 1)

        # 2. Runtime Event Flow: Step 1 — Inspection
        self.app.runtime.log_event(
            event_type=EventType.RESEARCH_STARTED,
            source=EventSource.RUNTIME,
            project_id=self.project_id,
            task_id=self.task_id,
            correlation_id=self.corr_id,
            worker_id="worker.researcher",
            payload={"role": "Specialist Researcher"},
        )

        act = self.app.activity_projector.get_activity(self.corr_id)
        self.assertIsNotNone(act)
        self.assertEqual(act.status, ActivityStatus.RUNNING)
        self.assertTrue(act.is_live)
        self.assertIn("Inspecting", act.current_action)

        # Step 2 — File Inspection Tool Call
        self.app.runtime.log_event(
            event_type=EventType.TOOL_STARTED,
            source=EventSource.RUNTIME,
            project_id=self.project_id,
            task_id=self.task_id,
            correlation_id=self.corr_id,
            worker_id="worker.researcher",
            payload={"tool_id": "filesystem.read_file", "arguments": {"path": "package.json"}},
        )
        act = self.app.activity_projector.get_activity(self.corr_id)
        self.assertIn("package.json", act.files_read)

        # Step 3 — Plan Created
        self.app.runtime.log_event(
            event_type=EventType.RESEARCH_PLAN_CREATED,
            source=EventSource.RUNTIME,
            project_id=self.project_id,
            task_id=self.task_id,
            correlation_id=self.corr_id,
            worker_id="worker.researcher",
            payload={"questions_count": 6},
        )
        act = self.app.activity_projector.get_activity(self.corr_id)
        self.assertTrue(any("Identified 6 research questions" in a for a in act.completed_actions))
        self.assertIn("Allocating", act.current_action)

        # Step 4 — 3 Concurrent Crawlers Active
        crawlers = [
            ("crawler.1", "modern React animation libraries"),
            ("crawler.2", "WebGPU portfolio integration"),
            ("crawler.3", "repository structure inspection"),
        ]
        for c_id, query in crawlers:
            self.app.runtime.log_event(
                event_type=EventType.RESEARCH_SEARCH_PERFORMED,
                source=EventSource.RUNTIME,
                project_id=self.project_id,
                task_id=self.task_id,
                correlation_id=self.corr_id,
                worker_id=c_id,
                payload={"query": query, "result_count": 4, "crawler_id": c_id},
            )

        act = self.app.activity_projector.get_activity(self.corr_id)
        active_crawlers = [w for w in act.workers if "crawler" in w.worker_id.lower()]
        self.assertEqual(len(active_crawlers), 3)
        self.assertEqual(act.metrics["sources_collected"], 12)

        # Step 5 — Evidence findings evaluated (3/6 questions answered)
        for i in range(3):
            self.app.runtime.log_event(
                event_type=EventType.RESEARCH_FINDING_CREATED,
                source=EventSource.RUNTIME,
                project_id=self.project_id,
                task_id=self.task_id,
                correlation_id=self.corr_id,
                worker_id="worker.researcher",
                payload={"statement": f"Finding {i + 1}"},
            )

        act = self.app.activity_projector.get_activity(self.corr_id)
        self.assertEqual(act.metrics["evidence_items"], 3)
        self.assertEqual(act.metrics["questions_answered"], "3/6")

        # Step 6 — Crawlers finish
        for c_id, _ in crawlers:
            self.app.runtime.log_event(
                event_type=EventType.WORKER_FINISHED,
                source=EventSource.RUNTIME,
                project_id=self.project_id,
                task_id=self.task_id,
                correlation_id=self.corr_id,
                worker_id=c_id,
                payload={"worker_id": c_id},
            )

        # Step 7 — Research Completed
        self.app.runtime.log_event(
            event_type=EventType.RESEARCH_COMPLETED,
            source=EventSource.RUNTIME,
            project_id=self.project_id,
            task_id=self.task_id,
            correlation_id=self.corr_id,
            worker_id="worker.researcher",
            payload={"summary": "UX research synthesis complete"},
        )

        final_act = self.app.activity_projector.get_activity(self.corr_id)
        self.assertEqual(final_act.status, ActivityStatus.COMPLETED)
        self.assertFalse(final_act.is_live)
        self.assertIn("Research complete", final_act.current_action)
        self.assertTrue(any("Research completed" in a for a in final_act.completed_actions))


if __name__ == "__main__":
    unittest.main()
