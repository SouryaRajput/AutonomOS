"""
Unit and regression tests for Message Sanitizer and Channel Separation.

Verifies strict separation between:
- USER-FACING CONVERSATION CHANNEL
- INTERNAL EXECUTION / EVENT CHANNEL
"""
from __future__ import annotations

import json
import os
import tempfile
import unittest

from app.application import AutonomOSApp
from app.dto.conversation import ConversationMessage, MessageType
from app.services.message_sanitizer import (
    extract_user_facing_narrative,
    is_internal_tool_payload,
    is_json_tool_string,
)
from core.events.types import EventSource, EventType


class TestMessageSanitizerAndChannelSeparation(unittest.TestCase):

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.temp_dir.name, "test_sanitizer.db")
        self.app = AutonomOSApp.with_sqlite(self.db_path)

    def tearDown(self):
        self.app.close()
        self.temp_dir.cleanup()

    # 1. Exact Bug Reproduction: Natural language + list_directory JSON
    def test_exact_bug_reproduction_scenario(self):
        raw_model_output = (
            "I'll start by exploring your current Portfolio project to understand the existing tech stack, "
            "then research modern animation and 3D technologies that would integrate\n\n"
            '{\n  "action": "list_directory",\n  "path": "/Users/shirsh/Downloads/Programming/Portfolio"\n}'
        )

        narrative, extracted_tools = extract_user_facing_narrative(raw_model_output)

        # 1. Natural language assistant message is preserved
        self.assertIn("I'll start by exploring your current Portfolio project", narrative)
        self.assertIn("research modern animation and 3D technologies", narrative)

        # 2. JSON tool call is extracted for the internal execution channel
        self.assertEqual(len(extracted_tools), 1)
        self.assertEqual(extracted_tools[0]["action"], "list_directory")
        self.assertEqual(extracted_tools[0]["path"], "/Users/shirsh/Downloads/Programming/Portfolio")

        # 3. JSON does NOT appear in user-facing narrative
        self.assertNotIn('{\n  "action": "list_directory"', narrative)
        self.assertNotIn('"path": "/Users/shirsh/Downloads/Programming/Portfolio"', narrative)
        self.assertNotIn("list_directory", narrative)

    # 2. Tool invocation does not create a visible chat message
    def test_pure_tool_invocation_produces_no_chat_pollution(self):
        raw_tool_json = '{\n  "action": "read_file",\n  "path": "package.json"\n}'
        narrative, extracted_tools = extract_user_facing_narrative(raw_tool_json)

        self.assertEqual(narrative, "")
        self.assertEqual(len(extracted_tools), 1)
        self.assertEqual(extracted_tools[0]["action"], "read_file")

    # 3. Tool arguments/JSON do not appear in conversation transcript
    def test_tool_arguments_hidden_from_transcript(self):
        raw_text = (
            "Inspecting the active repository.\n\n"
            '```json\n{\n  "tool": "filesystem.read_file",\n  "arguments": {"path": "src/index.ts"}\n}\n```'
        )
        narrative, extracted_tools = extract_user_facing_narrative(raw_text)

        self.assertEqual(narrative, "Inspecting the active repository.")
        self.assertEqual(len(extracted_tools), 1)
        self.assertNotIn("filesystem.read_file", narrative)
        self.assertNotIn("src/index.ts", narrative)

    # 4. Tool results do not automatically become chat messages
    def test_tool_results_filtered_from_chat(self):
        raw_result_payload = '{"status": "success", "action": "list_directory", "files": ["app.py", "models.py"]}'
        narrative, extracted_tools = extract_user_facing_narrative(raw_result_payload)
        self.assertEqual(narrative, "")
        self.assertEqual(len(extracted_tools), 1)

    # 5. Explicit assistant/user-facing progress messages still render normally
    def test_user_facing_progress_narrative_preserved(self):
        progress_text = (
            "I've inspected the project. I'll now research the technologies that best fit its current stack.\n\n"
            "Key architectural requirements identified:\n"
            "- React 18 frontend with TypeScript\n"
            "- Vite build tooling\n"
            "- Tailwind CSS styling"
        )
        narrative, extracted_tools = extract_user_facing_narrative(progress_text)
        self.assertEqual(narrative, progress_text)
        self.assertEqual(len(extracted_tools), 0)

    # 6. Internal Manager execution events remain internal
    def test_manager_internal_events_remain_internal(self):
        raw_mgr_output = (
            "Work plan prepared for distributed consensus investigation.\n\n"
            '{"action": "create_task", "title": "Inspect leader election", "priority": 10}'
        )
        narrative, tools = extract_user_facing_narrative(raw_mgr_output)
        self.assertEqual(narrative, "Work plan prepared for distributed consensus investigation.")
        self.assertEqual(len(tools), 1)
        self.assertEqual(tools[0]["action"], "create_task")

    # 7. Internal Researcher events remain internal
    def test_researcher_internal_events_remain_internal(self):
        raw_researcher_output = (
            "Synthesizing findings across gathered sources.\n\n"
            '{"action": "evidence_evaluation", "question_id": "q-1", "sufficiency": "SUFFICIENT"}'
        )
        narrative, tools = extract_user_facing_narrative(raw_researcher_output)
        self.assertEqual(narrative, "Synthesizing findings across gathered sources.")
        self.assertEqual(len(tools), 1)
        self.assertEqual(tools[0]["action"], "evidence_evaluation")

    # 8. Internal crawler events remain internal
    def test_crawler_internal_events_remain_internal(self):
        raw_crawler_output = (
            "Crawling official documentation.\n\n"
            '{"action": "web_fetch", "url": "https://docs.autonomos.ai/api", "timeout": 30}'
        )
        narrative, tools = extract_user_facing_narrative(raw_crawler_output)
        self.assertEqual(narrative, "Crawling official documentation.")
        self.assertEqual(len(tools), 1)
        self.assertEqual(tools[0]["action"], "web_fetch")

    # 9. Multiple consecutive tool calls do not leak into transcript
    def test_multiple_consecutive_tool_calls_extracted(self):
        raw_multi_tool = (
            "I will check the configuration and source files.\n\n"
            '{"action": "read_file", "path": "package.json"}\n\n'
            '{"action": "list_directory", "path": "src"}\n\n'
            '{"action": "stat_file", "path": "tsconfig.json"}'
        )
        narrative, tools = extract_user_facing_narrative(raw_multi_tool)
        self.assertEqual(narrative, "I will check the configuration and source files.")
        self.assertEqual(len(tools), 3)
        self.assertEqual(tools[0]["action"], "read_file")
        self.assertEqual(tools[1]["action"], "list_directory")
        self.assertEqual(tools[2]["action"], "stat_file")

    # 10. Streaming tool execution fragments / partial JSON are hidden
    def test_openai_function_call_payloads_hidden(self):
        raw_function_call = (
            "Beginning workspace audit.\n\n"
            '{"type": "function", "function": {"name": "list_directory", "arguments": "{\\"path\\": \\"/workspace\\"}"}}'
        )
        narrative, tools = extract_user_facing_narrative(raw_function_call)
        self.assertEqual(narrative, "Beginning workspace audit.")
        self.assertEqual(len(tools), 1)

    # 11. Tool execution failures do not expose raw internal payloads
    def test_tool_failure_payloads_hidden(self):
        raw_fail = '{"status": "error", "error": "500 Internal Server Error", "tool": "web_fetch"}'
        narrative, tools = extract_user_facing_narrative(raw_fail)
        self.assertEqual(narrative, "")
        self.assertEqual(len(tools), 1)

    # 12. Normal educational markdown code examples (non-tool JSON) are preserved
    def test_educational_json_code_example_preserved(self):
        example_text = (
            "Here is how you configure your project settings:\n\n"
            "```json\n"
            "{\n"
            '  "projectName": "MyPortfolio",\n'
            '  "theme": "dark",\n'
            '  "enable3D": true\n'
            "}\n"
            "```"
        )
        narrative, tools = extract_user_facing_narrative(example_text)
        self.assertIn("MyPortfolio", narrative)
        self.assertIn("enable3D", narrative)
        self.assertEqual(len(tools), 0)

    # 13. End-to-end ConversationService: chat message clean, runtime event bus receives tool event
    def test_conversation_service_preserves_internal_event_stream(self):
        proj = self.app.projects.create_project(name="Sanitizer Proj", root_path=self.temp_dir.name)
        conv = self.app.conversations.get_or_create_active_conversation(proj["id"])

        # Record a system message with embedded tool call
        embedded_content = (
            "Investigating tech stack.\n\n"
            '{\n  "action": "list_directory",\n  "path": "/workspace/src"\n}'
        )
        msg = self.app.conversations.record_system_message(
            conversation_id=conv.id,
            message_type=MessageType.WORKER_UPDATE,
            content=embedded_content,
            sender="Specialist Researcher",
        )

        # 1. Chat message content is clean prose
        self.assertEqual(msg.content, "Investigating tech stack.")
        self.assertNotIn("list_directory", msg.content)

        # 2. Verify persistence in DB
        reloaded = self.app.conversations.get_conversation(conv.id)
        saved_msg = reloaded.messages[-1]
        self.assertEqual(saved_msg.content, "Investigating tech stack.")
        self.assertNotIn("list_directory", saved_msg.content)

        # 3. Verify internal execution channel received the event on runtime bus
        events = self.app.events.get_events(project_id=proj["id"])
        tool_events = [e for e in events if e.get("event_type") == EventType.TOOL_REQUESTED.value]
        self.assertTrue(len(tool_events) >= 1)
        self.assertEqual(tool_events[-1]["payload"]["action"], "list_directory")
        self.assertEqual(tool_events[-1]["payload"]["path"], "/workspace/src")


if __name__ == "__main__":
    unittest.main()
