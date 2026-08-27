from __future__ import annotations

import json
import unittest

from core.enums import ToolStatus
from core.models import Task
from core.tools.model import ToolResult
from pkg.sdk.harness import WorkerTestHarness
from workers.researcher.types import FactClassification, ResearchConfidence, ResearchQuestionStatus
from workers.researcher.worker import ResearcherWorker


class TestResearcherHallucinationAndUncertainty(unittest.TestCase):
    """Unit tests ensuring missing facts are classified as UNKNOWN and never hallucinated."""

    def test_unsupported_claim_marked_unknown_with_knowledge_gap(self):
        harness = WorkerTestHarness(project_id="proj-hallucination")
        worker = ResearcherWorker("worker.researcher.honest")

        # Mock source that mentions product X, but has no pricing data
        harness.mock_tool(
            "web.search",
            ToolResult(
                result_id="res-s",
                request_id="req-s",
                tool_id="web.search",
                status=ToolStatus.SUCCESS,
                output=[
                    {
                        "title": "Tool X Architecture Overview",
                        "url": "https://example.org/tool-x",
                        "snippet": "Tool X is a distributed queue system.",
                    }
                ],
            ),
        )
        harness.mock_tool(
            "web.fetch",
            ToolResult(
                result_id="res-f",
                request_id="req-f",
                tool_id="web.fetch",
                status=ToolStatus.SUCCESS,
                output="Tool X is written in Rust. Architecture details: uses Raft consensus.",
            ),
        )

        # Synthesis honestly reports that enterprise pricing is UNKNOWN
        synthesis_json = {
            "reasoning_summary": "Architecture found, but enterprise pricing information is not published in public documentation.",
            "questions_resolved": [
                {"question_id": "q-1", "status": "ANSWERED", "notes": "Architecture is Raft-based"},
                {"question_id": "q-2", "status": "UNKNOWN", "notes": "No enterprise pricing mentioned"},
            ],
            "findings": [
                {
                    "finding_id": "f-1",
                    "claim": "Tool X uses Raft consensus for state replication.",
                    "classification": "FACT",
                    "confidence": "WELL_SUPPORTED",
                    "source_ids": ["src-1"],
                    "reasoning": "Explicitly stated in architecture overview.",
                },
                {
                    "finding_id": "f-2",
                    "claim": "Enterprise pricing tier details are undisclosed.",
                    "classification": "UNKNOWN",
                    "confidence": "LIMITED_EVIDENCE",
                    "source_ids": ["src-1"],
                    "reasoning": "Pricing page requires contacting sales.",
                }
            ],
            "contradictions": [],
            "knowledge_gaps": [
                {
                    "topic": "Tool X Pricing",
                    "question": "What is the annual enterprise license cost for Tool X?",
                    "reason": "Requires direct sales inquiry; not publicly listed.",
                    "impact": "Budget calculations cannot be completed without vendor quote.",
                }
            ],
            "recommendations": [
                {
                    "action": "Contact Tool X sales team for custom licensing quote.",
                    "rationale": "Pricing information is not publicly documented.",
                }
            ],
            "manager_summary": "- Tool X uses Raft.\n- Enterprise pricing is UNKNOWN (knowledge gap created).",
        }
        harness.mock_inference(json.dumps(synthesis_json))

        task = Task(
            id="t-honest-01",
            project_id="proj-hallucination",
            title="Tool X Evaluation",
            objective="Evaluate Tool X architecture and enterprise license cost",
            metadata={
                "questions": [
                    "What consensus protocol does Tool X use?",
                    "What is the annual enterprise license cost for Tool X?",
                ]
            },
        )
        harness.task = task

        output = harness.run(worker)
        self.assertTrue(output.success)

        res_meta = output.metadata["research_result"]
        findings = res_meta["findings"]
        gaps = res_meta["knowledge_gaps"]

        # Check that unknown was captured
        self.assertEqual(len(gaps), 1)
        self.assertEqual(gaps[0]["topic"], "Tool X Pricing")
        self.assertEqual(findings[1]["classification"], "UNKNOWN")
        self.assertIn("Enterprise pricing is UNKNOWN", output.summary)


if __name__ == "__main__":
    unittest.main()
