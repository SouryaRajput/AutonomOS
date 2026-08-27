from __future__ import annotations

import json
import unittest

from core.models import Task, utc_now
from core.tools.model import ToolResult
from core.enums import ToolStatus
from pkg.sdk.harness import WorkerTestHarness
from workers.researcher.worker import ResearcherWorker


class TestResearcherSearchBudgetAndStagnation(unittest.TestCase):
    """Unit tests verifying search budgets, fetch limits, and stagnation termination."""

    def test_search_and_fetch_budgets_enforced(self):
        harness = WorkerTestHarness(project_id="proj-budget")
        worker = ResearcherWorker("worker.researcher.budget")

        search_calls = []
        fetch_calls = []

        def mock_web_search(args):
            search_calls.append(args.get("query"))
            return ToolResult(
                result_id="res-search",
                request_id="req-1",
                tool_id="web.search",
                status=ToolStatus.SUCCESS,
                output=[
                    {"title": f"Result {i}", "url": f"https://example.com/page{i}", "snippet": f"Snippet {i}"}
                    for i in range(1, 15)  # 14 results
                ],
            )

        def mock_web_fetch(args):
            fetch_calls.append(args.get("url"))
            return ToolResult(
                result_id="res-fetch",
                request_id="req-2",
                tool_id="web.fetch",
                status=ToolStatus.SUCCESS,
                output="Fetched page content sample",
            )

        harness.mock_tool("web.search", mock_web_search)
        harness.mock_tool("web.fetch", mock_web_fetch)

        # Mock inference synthesis response
        synthesis_json = {
            "reasoning_summary": "Synthesized results within budget bounds.",
            "questions_resolved": [{"question_id": "q-1", "status": "ANSWERED"}],
            "findings": [
                {
                    "finding_id": "f-1",
                    "claim": "Budget-constrained finding.",
                    "classification": "SOURCE_CLAIM",
                    "confidence": "SUPPORTED",
                    "source_ids": ["src-1"],
                    "reasoning": "Observed in fetched page.",
                }
            ],
            "contradictions": [],
            "knowledge_gaps": [],
            "recommendations": [],
            "manager_summary": "- Bounded research executed.",
        }
        harness.mock_inference(json.dumps(synthesis_json))

        task = Task(
            id="t-budget-01",
            project_id="proj-budget",
            title="Search Budget Test",
            objective="Evaluate maximum limits",
            metadata={
                "max_searches": 2,
                "max_fetches": 3,
                "questions": ["Q1: Test limit 1", "Q2: Test limit 2", "Q3: Test limit 3", "Q4: Test limit 4"],
            },
        )
        harness.task = task

        output = harness.run(worker)
        self.assertTrue(output.success)

        # Verify search budget respected
        self.assertLessEqual(len(search_calls), 2)
        # Verify fetch budget respected
        self.assertLessEqual(len(fetch_calls), 3)

    def test_empty_search_results_terminates_with_knowledge_gap(self):
        harness = WorkerTestHarness(project_id="proj-empty")
        worker = ResearcherWorker("worker.researcher.empty")

        # Mock search returning empty list
        harness.mock_tool(
            "web.search",
            ToolResult(
                result_id="res-empty",
                request_id="req-empty",
                tool_id="web.search",
                status=ToolStatus.SUCCESS,
                output=[],
            ),
        )

        synthesis_json = {
            "reasoning_summary": "No sources found for the query.",
            "questions_resolved": [{"question_id": "q-1", "status": "UNKNOWN", "notes": "No literature found"}],
            "findings": [],
            "contradictions": [],
            "knowledge_gaps": [
                {
                    "topic": "Obscure Query",
                    "question": "What is the secret undocumented flag?",
                    "reason": "No public sources returned.",
                    "impact": "Flag cannot be verified.",
                }
            ],
            "recommendations": [],
            "manager_summary": "- No sources found. Uncertainty reported.",
        }
        harness.mock_inference(json.dumps(synthesis_json))

        task = Task(
            id="t-empty-01",
            project_id="proj-empty",
            title="Search for Non-Existent Protocol",
            objective="Find documentation for imaginary protocol XYZ-9999",
        )
        harness.task = task

        output = harness.run(worker)
        self.assertTrue(output.success)
        res_meta = output.metadata.get("research_result", {})
        self.assertEqual(len(res_meta.get("sources", [])), 0)
        self.assertIn("knowledge gap(s) identified", output.summary)


if __name__ == "__main__":
    unittest.main()
