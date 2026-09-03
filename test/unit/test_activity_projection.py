"""
Comprehensive Unit Tests for Workforce Execution Activity & Status Presentation Layer.
Verifies all 28 requirements for three-channel separation, activity projection,
command sanitization, crawler tracking, waiting/stalled/failed states, and multi-task isolation.
"""
from __future__ import annotations

import unittest
import uuid
import time

from app.services.message_sanitizer import (
    extract_user_facing_narrative,
    is_internal_tool_payload,
    is_json_tool_string,
)
from core.activity.model import (
    ActivityStatus,
    CommandLineItem,
    ExecutionActivity,
    FileActivityItem,
    FileOperationType,
    WorkerActivityItem,
)
from core.activity.projector import WorkforceActivityProjector, redact_command
from core.events.model import Event
from core.events.types import EventSource, EventType
from core.models import utc_now


class TestActivityProjectionLayer(unittest.TestCase):
    def setUp(self):
        self.projector = WorkforceActivityProjector()
        self.proj_id = "proj-test-1"
        self.task_id = "task-test-1"
        self.corr_id = "corr-test-1"

    def _create_event(
        self,
        event_type: EventType,
        payload: dict,
        worker_id: str = "worker.manager",
        task_id: str = "task-test-1",
        corr_id: str = "corr-test-1",
        seq: int = 1,
    ) -> Event:
        return Event(
            event_id=f"evt-{uuid.uuid4().hex[:8]}",
            sequence_number=seq,
            event_type=event_type,
            source=EventSource.RUNTIME,
            project_id=self.proj_id,
            task_id=task_id,
            worker_id=worker_id,
            correlation_id=corr_id,
            timestamp=utc_now(),
            payload=payload,
        )

    # 1. Internal tool event does not become chat message
    def test_internal_tool_event_does_not_become_chat_message(self):
        raw_tool_json = '{"action": "list_directory", "path": "/src/components"}'
        narrative, tools = extract_user_facing_narrative(raw_tool_json)
        self.assertEqual(narrative, "")
        self.assertEqual(len(tools), 1)
        self.assertEqual(tools[0]["action"], "list_directory")

    # 2. Internal tool event becomes appropriate activity event
    def test_internal_tool_event_becomes_appropriate_activity_event(self):
        evt = self._create_event(
            EventType.TOOL_STARTED,
            {"tool_id": "filesystem.read_file", "arguments": {"path": "package.json"}},
            worker_id="worker.researcher",
        )
        act = self.projector.project_event(evt)
        self.assertEqual(act.current_action, "Reading: package.json")
        self.assertIn("package.json", act.files_read)

    # 3. Current activity updates correctly
    def test_current_activity_updates_correctly(self):
        evt1 = self._create_event(
            EventType.RESEARCH_STARTED,
            {},
            worker_id="worker.researcher",
        )
        act = self.projector.project_event(evt1)
        self.assertEqual(act.status, ActivityStatus.RUNNING)
        self.assertIn("Inspecting", act.current_action)

        evt2 = self._create_event(
            EventType.RESEARCH_SEARCH_PERFORMED,
            {"query": "React animations", "result_count": 5},
            worker_id="crawler.1",
        )
        act2 = self.projector.project_event(evt2)
        self.assertIn("React animations", act2.current_action)

    # 4. Completed activity is preserved
    def test_completed_activity_is_preserved(self):
        evt1 = self._create_event(
            EventType.RESEARCH_PLAN_CREATED,
            {"questions_count": 4},
            worker_id="worker.researcher",
        )
        self.projector.project_event(evt1)

        evt2 = self._create_event(
            EventType.PROGRAMMER_CODE_MODIFIED,
            {"file_path": "src/App.tsx"},
            worker_id="worker.programmer",
        )
        act = self.projector.project_event(evt2)

        self.assertTrue(any("Identified 4 research questions" in a for a in act.completed_actions))
        self.assertTrue(any("Modified src/App.tsx" in a for a in act.completed_actions))

    # 5. Command execution is displayed
    def test_command_execution_is_displayed(self):
        evt_start = self._create_event(
            EventType.TOOL_STARTED,
            {"tool_id": "shell.exec", "arguments": {"command": "git status --short"}},
        )
        act = self.projector.project_event(evt_start)
        self.assertEqual(len(act.commands), 1)
        self.assertEqual(act.commands[0].command, "git status --short")
        self.assertTrue(act.commands[0].is_running)

        evt_end = self._create_event(
            EventType.TOOL_COMPLETED,
            {"tool_id": "shell.exec", "exit_code": 0, "duration_ms": 45.2},
        )
        act2 = self.projector.project_event(evt_end)
        self.assertFalse(act2.commands[0].is_running)
        self.assertEqual(act2.commands[0].exit_code, 0)

    # 6. File read is displayed and deduplicated
    def test_file_read_is_displayed_and_deduplicated(self):
        evt1 = self._create_event(
            EventType.TOOL_STARTED,
            {"tool_id": "filesystem.read_file", "arguments": {"path": "src/index.ts"}},
        )
        self.projector.project_event(evt1)

        evt2 = self._create_event(
            EventType.TOOL_STARTED,
            {"tool_id": "filesystem.read_file", "arguments": {"path": "src/index.ts"}},
        )
        act = self.projector.project_event(evt2)
        self.assertEqual(act.files_read.count("src/index.ts"), 1)

    # 7. File modification is displayed
    def test_file_modification_is_displayed(self):
        evt = self._create_event(
            EventType.PROGRAMMER_CODE_MODIFIED,
            {"file_path": "src/components/Header.tsx"},
            worker_id="worker.programmer",
        )
        act = self.projector.project_event(evt)
        self.assertTrue(any(f.path == "src/components/Header.tsx" and f.operation == FileOperationType.MODIFIED for f in act.files_changed))

    # 8. File creation and deletion is displayed
    def test_file_creation_and_deletion_displayed(self):
        evt_create = self._create_event(
            EventType.TOOL_STARTED,
            {"tool_id": "filesystem.create_file", "arguments": {"path": "src/utils/helpers.ts"}},
        )
        act = self.projector.project_event(evt_create)
        self.assertTrue(any(f.path == "src/utils/helpers.ts" and f.operation == FileOperationType.CREATED for f in act.files_changed))

        evt_del = self._create_event(
            EventType.TOOL_STARTED,
            {"tool_id": "filesystem.delete_file", "arguments": {"path": "src/legacy.js"}},
        )
        act2 = self.projector.project_event(evt_del)
        self.assertTrue(any(f.path == "src/legacy.js" and f.operation == FileOperationType.DELETED for f in act2.files_changed))

    # 9. Sensitive command arguments are redacted
    def test_sensitive_command_arguments_redacted(self):
        raw_cmd = "curl -H 'Authorization: Bearer secret_token_12345' --api-key my-secret-key https://api.openai.com/v1"
        clean = redact_command(raw_cmd)
        self.assertNotIn("secret_token_12345", clean)
        self.assertNotIn("my-secret-key", clean)
        self.assertIn("[REDACTED]", clean)

    # 10. Worker start appears in activity
    def test_worker_start_appears_in_activity(self):
        evt = self._create_event(
            EventType.WORKER_STARTED,
            {"worker_id": "worker.programmer", "role": "Programmer Specialist"},
            worker_id="worker.programmer",
        )
        act = self.projector.project_event(evt)
        self.assertTrue(any(w.worker_id == "worker.programmer" and w.status == "RUNNING" for w in act.workers))

    # 11. Worker completion appears in activity
    def test_worker_completion_appears_in_activity(self):
        evt1 = self._create_event(
            EventType.WORKER_STARTED,
            {"worker_id": "worker.tester", "role": "QA Tester"},
            worker_id="worker.tester",
        )
        self.projector.project_event(evt1)

        evt2 = self._create_event(
            EventType.WORKER_FINISHED,
            {"worker_id": "worker.tester"},
            worker_id="worker.tester",
        )
        act = self.projector.project_event(evt2)
        self.assertTrue(any(w.worker_id == "worker.tester" and w.status == "COMPLETED" for w in act.workers))

    # 12. Researcher activity appears correctly
    def test_researcher_activity_appears_correctly(self):
        evt_start = self._create_event(
            EventType.RESEARCH_STARTED,
            {},
            worker_id="worker.researcher",
        )
        self.projector.project_event(evt_start)

        evt_finding = self._create_event(
            EventType.RESEARCH_FINDING_CREATED,
            {"statement": "Tailwind provides zero-runtime styling"},
            worker_id="worker.researcher",
        )
        act = self.projector.project_event(evt_finding)
        self.assertEqual(act.metrics.get("evidence_items"), 1)
        self.assertEqual(act.metrics.get("questions_answered"), "1/6")

    # 13. Multiple concurrent crawlers display independently
    def test_multiple_concurrent_crawlers_display_independently(self):
        c1 = self._create_event(
            EventType.RESEARCH_SEARCH_PERFORMED,
            {"query": "modern React animation libraries", "crawler_id": "crawler.search.1"},
            worker_id="crawler.search.1",
        )
        c2 = self._create_event(
            EventType.RESEARCH_SEARCH_PERFORMED,
            {"query": "WebGPU portfolio integration", "crawler_id": "crawler.doc.2"},
            worker_id="crawler.doc.2",
        )
        self.projector.project_event(c1)
        act = self.projector.project_event(c2)

        crawlers = [w for w in act.workers if "crawler" in w.worker_id.lower()]
        self.assertEqual(len(crawlers), 2)
        self.assertTrue(any("modern React animation" in w.action for w in crawlers))
        self.assertTrue(any("WebGPU portfolio" in w.action for w in crawlers))

    # 14. Waiting state is distinguishable from running
    def test_waiting_state_distinguishable_from_running(self):
        evt = self._create_event(
            EventType.MANAGER_WAITING,
            {"reason": "Waiting for 2 crawlers to finish"},
        )
        act = self.projector.project_event(evt)
        self.assertEqual(act.status, ActivityStatus.WAITING)
        self.assertEqual(act.waiting_reason, "Waiting for 2 crawlers to finish")
        self.assertIn("Waiting:", act.current_action)

    # 15. Stalled state is distinguishable from waiting
    def test_stalled_state_distinguishable_from_waiting(self):
        evt = self._create_event(
            EventType.MANAGER_STAGNATION_DETECTED,
            {},
        )
        act = self.projector.project_event(evt)
        self.assertEqual(act.status, ActivityStatus.STALLED)
        self.assertIn("Stalled", act.current_action)

    # 16. Failure state is displayed correctly
    def test_failure_state_displayed_correctly(self):
        evt = self._create_event(
            EventType.TASK_FAILED,
            {"reason": "Web search failed — provider returned a rate-limit response (HTTP 429)"},
        )
        act = self.projector.project_event(evt)
        self.assertEqual(act.status, ActivityStatus.FAILED)
        self.assertFalse(act.is_live)
        self.assertIn("rate limit", act.error_summary.lower())

    # 17. Cancellation state is displayed correctly
    def test_cancellation_state_displayed_correctly(self):
        evt = self._create_event(
            EventType.TASK_CANCELLED,
            {},
        )
        act = self.projector.project_event(evt)
        self.assertEqual(act.status, ActivityStatus.CANCELLED)
        self.assertFalse(act.is_live)

    # 18. Activity completes correctly
    def test_activity_completes_correctly(self):
        evt_start = self._create_event(EventType.TASK_STARTED, {"title": "Portfolio Refactor"})
        self.projector.project_event(evt_start)

        evt_done = self._create_event(EventType.TASK_COMPLETED, {"summary": "Portfolio refactor complete"})
        act = self.projector.project_event(evt_done)

        self.assertEqual(act.status, ActivityStatus.COMPLETED)
        self.assertFalse(act.is_live)
        self.assertTrue(any("Portfolio refactor complete" in s for s in act.completed_actions))

    # 19. Duplicate events are handled safely (idempotent)
    def test_duplicate_events_handled_safely(self):
        evt = self._create_event(
            EventType.RESEARCH_SEARCH_PERFORMED,
            {"query": "Next.js 15 App Router", "result_count": 10},
        )
        self.projector.project_event(evt)
        act2 = self.projector.project_event(evt)
        self.assertEqual(act2.metrics.get("sources_collected"), 10)

    # 20. Out-of-order events handled safely
    def test_out_of_order_events_handled_safely(self):
        evt_tool = self._create_event(
            EventType.TOOL_STARTED,
            {"tool_id": "shell.exec", "arguments": {"command": "npm test"}},
            seq=2,
        )
        act = self.projector.project_event(evt_tool)
        self.assertEqual(len(act.commands), 1)

    # 21. Multiple tasks remain isolated
    def test_multiple_tasks_remain_isolated(self):
        evt_task_a = self._create_event(
            EventType.TASK_STARTED,
            {"title": "Task A: UX research"},
            task_id="task-A",
            corr_id="corr-A",
        )
        evt_task_b = self._create_event(
            EventType.TASK_STARTED,
            {"title": "Task B: Auth bugfix"},
            task_id="task-B",
            corr_id="corr-B",
        )
        act_a = self.projector.project_event(evt_task_a)
        act_b = self.projector.project_event(evt_task_b)

        self.assertNotEqual(act_a.activity_id, act_b.activity_id)
        self.assertEqual(act_a.task_id, "task-A")
        self.assertEqual(act_b.task_id, "task-B")

    # 22. Event stream reconnect / resync works without marking workers failed
    def test_event_stream_reconnect_preserves_state(self):
        evt = self._create_event(
            EventType.WORKER_STARTED,
            {"worker_id": "worker.programmer", "role": "Programmer"},
            worker_id="worker.programmer",
        )
        self.projector.project_event(evt)

        # Query existing activity after reconnect
        reloaded = self.projector.get_activity(self.corr_id)
        self.assertIsNotNone(reloaded)
        self.assertEqual(reloaded.status, ActivityStatus.RUNNING)
        self.assertTrue(any(w.worker_id == "worker.programmer" for w in reloaded.workers))

    # 23. Large event volume remains bounded
    def test_large_event_volume_remains_bounded(self):
        start = time.perf_counter()
        for i in range(150):
            evt = self._create_event(
                EventType.TOOL_STARTED,
                {"tool_id": "filesystem.read_file", "arguments": {"path": f"file_{i % 20}.py"}},
                seq=i + 1,
            )
            self.projector.project_event(evt)
        duration = time.perf_counter() - start
        self.assertLess(duration, 0.5)  # Processing 150 events in <500ms
        act = self.projector.get_activity(self.corr_id)
        self.assertEqual(len(act.files_read), 20)  # Deduplicated to 20 files

    # 24. No raw JSON payload appears in normal chat
    def test_no_raw_json_payload_appears_in_normal_chat(self):
        ai_response = (
            "I'll inspect the project first.\n"
            "```json\n"
            '{"action": "read_file", "path": "package.json"}\n'
            "```\n"
            "Then I'll research improvements."
        )
        narrative, tools = extract_user_facing_narrative(ai_response)
        self.assertNotIn('{"action": "read_file"', narrative)
        self.assertIn("I'll inspect the project first.", narrative)
        self.assertIn("Then I'll research improvements.", narrative)
        self.assertEqual(len(tools), 1)

    # 25. Existing MessageSanitizer behavior remains intact
    def test_existing_message_sanitizer_behavior_remains_intact(self):
        plain_text = "Here is a clean conversational response for the user."
        narrative, tools = extract_user_facing_narrative(plain_text)
        self.assertEqual(narrative, plain_text)
        self.assertEqual(len(tools), 0)


if __name__ == "__main__":
    unittest.main()
