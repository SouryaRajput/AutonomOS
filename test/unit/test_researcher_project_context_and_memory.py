from __future__ import annotations

import json
import unittest

from core.context.model import ContextBudget, ContextItem, ContextPackage
from core.context.types import ContextSourceType
from core.enums import ToolStatus
from core.memory.model import MemoryDocument
from core.models import Task
from core.tools.model import ToolResult
from pkg.sdk.harness import WorkerTestHarness
from workers.researcher.worker import ResearcherWorker


class TestResearcherProjectContextAndMemory(unittest.TestCase):
    """Unit tests ensuring Researcher reads and incorporates project memory and local context."""

    def test_researcher_uses_existing_project_memory(self):
        harness = WorkerTestHarness(project_id="proj-memory")
        worker = ResearcherWorker("worker.researcher.mem")

        # Mock project memory documents
        arch_doc = MemoryDocument(
            id="mem-arch-01",
            project_id="proj-memory",
            memory_type="ARCHITECTURE",
            title="System Architecture",
            relative_path=".autonomos/memory/architecture.md",
            content="## Architecture\n- Core database: PostgreSQL 15\n- Message broker: Redis 7",
            summary="System uses PostgreSQL and Redis.",
        )
        harness.mock_memory_document(arch_doc)

        # Mock context package
        ctx_pkg = ContextPackage(
            request_id="ctx-req-01",
            project_id="proj-memory",
            task_id="t-mem-01",
            worker_id="worker.researcher.mem",
            items=[
                ContextItem(
                    id="ctx-item-1",
                    source_type=ContextSourceType.PROJECT_MAP,
                    source_id="pm-1",
                    title="project-map.md",
                    content="Service A communicates with Service B over gRPC.",
                )
            ],
            total_estimated_tokens=50,
            total_characters=200,
            budget=ContextBudget(),
        )
        harness.mock_context(ctx_pkg)

        # Mock search & fetch tools
        harness.mock_tool(
            "web.search",
            ToolResult(
                result_id="res-s",
                request_id="req-s",
                tool_id="web.search",
                status=ToolStatus.SUCCESS,
                output=[{"title": "Redis PubSub Scaling", "url": "https://redis.io/topics/pubsub", "snippet": "Redis pubsub scaling guide"}],
            ),
        )
        harness.mock_tool(
            "web.fetch",
            ToolResult(
                result_id="res-f",
                request_id="req-f",
                tool_id="web.fetch",
                status=ToolStatus.SUCCESS,
                output="Redis PubSub delivers messages to active subscribers at sub-millisecond latencies.",
            ),
        )

        captured_messages = []

        def mock_inference_handler(req):
            captured_messages.extend(req.messages)
            synthesis_json = {
                "reasoning_summary": "Combined existing project architecture (Redis 7) with Redis PubSub capabilities.",
                "questions_resolved": [{"question_id": "q-1", "status": "ANSWERED"}],
                "findings": [
                    {
                        "finding_id": "f-1",
                        "claim": "Project already operates Redis 7, which supports sub-millisecond PubSub.",
                        "classification": "FACT",
                        "confidence": "WELL_SUPPORTED",
                        "source_ids": ["src-1"],
                        "reasoning": "Project memory confirms Redis 7 is deployed; external docs verify PubSub latency.",
                        "project_implications": "Zero new infrastructure required to support real-time notifications.",
                    }
                ],
                "contradictions": [],
                "knowledge_gaps": [],
                "recommendations": [
                    {
                        "action": "Use deployed Redis 7 instance for real-time notification broadcasting.",
                        "rationale": "Avoids introducing a new message broker.",
                    }
                ],
                "manager_summary": "- Project Redis 7 can handle notification pub/sub without new infrastructure.",
            }
            return json.dumps(synthesis_json)

        harness.mock_inference(mock_inference_handler)

        task = Task(
            id="t-mem-01",
            project_id="proj-memory",
            title="Real-Time Notifications Architecture",
            objective="Evaluate message broker options considering existing project dependencies",
        )
        harness.task = task

        output = harness.run(worker)
        self.assertTrue(output.success)

        # Verify prompt included project memory content
        self.assertTrue(len(captured_messages) >= 2)
        user_msg = captured_messages[1].content
        self.assertIn("Core database: PostgreSQL 15", user_msg)
        self.assertIn("Message broker: Redis 7", user_msg)
        self.assertIn("Service A communicates with Service B over gRPC", user_msg)


if __name__ == "__main__":
    unittest.main()
