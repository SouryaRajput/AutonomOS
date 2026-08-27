from __future__ import annotations

import json
import unittest

from workers.researcher.model import (
    ResearchQuestion,
    ResearchScope,
    ResearchTaskSpec,
    Source,
)
from workers.researcher.prompt import (
    build_research_synthesis_prompt,
    parse_research_synthesis,
)
from workers.researcher.types import (
    FactClassification,
    ResearchConfidence,
    ResearchMode,
    SourceType,
)


class TestResearcherPromptAndParser(unittest.TestCase):
    """Unit tests for prompt formatting, delimiter isolation, and synthesis parsing."""

    def test_build_prompt_isolates_untrusted_source_content(self):
        spec = ResearchTaskSpec(
            objective="Evaluate GraphQL libraries for Python",
            mode=ResearchMode.STANDARD,
            questions=[
                ResearchQuestion(question_id="q-1", question_text="What is the standard ASGI GraphQL library?")
            ],
        )
        sources = [
            Source(
                source_id="src-1",
                title="Strawberry GraphQL Documentation",
                url_or_ref="https://strawberry.rocks/docs",
                source_type=SourceType.OFFICIAL_DOCUMENTATION,
                content_snippet="Strawberry is a code-first Python GraphQL library based on dataclasses.",
            )
        ]

        messages = build_research_synthesis_prompt(task_spec=spec, sources=sources)
        self.assertEqual(len(messages), 2)
        self.assertEqual(messages[0].role, "system")
        self.assertIn("AutonomOS Specialist Researcher", messages[0].content)

        user_content = messages[1].content
        self.assertIn("[UNTRUSTED_SOURCE_DATA]", user_content)
        self.assertIn("Strawberry is a code-first Python GraphQL library", user_content)
        self.assertIn("[/UNTRUSTED_SOURCE_DATA]", user_content)

    def test_parse_valid_json_synthesis(self):
        spec = ResearchTaskSpec(
            objective="Evaluate Redis vs Memcached",
            questions=[ResearchQuestion(question_id="q-1", question_text="Which supports pub/sub?")]
        )
        sources = [Source(source_id="src-1", title="Redis Docs", url_or_ref="https://redis.io")]

        payload = {
            "reasoning_summary": "Redis provides built-in pub/sub while Memcached is pure key-value cache.",
            "questions_resolved": [{"question_id": "q-1", "status": "ANSWERED"}],
            "findings": [
                {
                    "finding_id": "f-1",
                    "claim": "Redis supports pub/sub channels natively.",
                    "classification": "FACT",
                    "confidence": "WELL_SUPPORTED",
                    "source_ids": ["src-1"],
                    "reasoning": "Official Redis commands documentation describes SUBSCRIBE and PUBLISH.",
                }
            ],
            "contradictions": [],
            "knowledge_gaps": [],
            "recommendations": [
                {
                    "action": "Use Redis for real-time messaging.",
                    "rationale": "Built-in pub/sub primitives satisfy project requirements.",
                }
            ],
            "manager_summary": "- Redis supports native pub/sub.\n- Recommended for messaging.",
        }

        raw_response = f"```json\n{json.dumps(payload)}\n```"
        findings, contradictions, gaps, recs, q_stats, summary = parse_research_synthesis(
            raw_content=raw_response,
            task_spec=spec,
            sources=sources,
        )

        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0].classification, FactClassification.FACT)
        self.assertEqual(findings[0].confidence, ResearchConfidence.WELL_SUPPORTED)
        self.assertEqual(len(recs), 1)
        self.assertEqual(q_stats.get("q-1"), "ANSWERED")
        self.assertIn("Redis supports native pub/sub", summary)


if __name__ == "__main__":
    unittest.main()
