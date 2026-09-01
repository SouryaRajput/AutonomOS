from __future__ import annotations

import unittest

from core.research.contracts.evidence import EvidenceItem, EvidenceProvenance
from core.research.contracts.question import ResearchQuestion
from core.research.contracts.request import ResearchRequest, ResearchScope
from core.research.contracts.result import ResearchFinding
from core.research.evidence.evaluator import EvidenceEvaluator
from core.research.state.model import ResearchState
from core.research.synthesis.synthesizer import ResearchSynthesizer
from core.research.types import (
    EvidenceSufficiency,
    FactClassification,
    ResearchConfidence,
    ResearchQuestionStatus,
    ResearchResultStatus,
    SourceType,
)


class TestEvidenceEvaluationAndSynthesis(unittest.TestCase):
    """Unit tests for EvidenceEvaluator coverage/contradiction checks and ResearchSynthesizer packaging."""

    def setUp(self):
        self.request = ResearchRequest(
            request_id="req-eval-1",
            project_id="proj-1",
            task_id="t-1",
            objective="Evaluate RocksDB Write Stall Triggers",
            scope=ResearchScope(min_evidence_per_question=2),
        )
        self.state = ResearchState(request=self.request)

    def test_evaluate_coverage_with_full_evidence(self):
        q1 = ResearchQuestion(question_id="q-1", question_text="What triggers L0 write stall in RocksDB?")
        self.state.add_question(q1)

        ev1 = EvidenceItem(
            evidence_id="ev-1",
            provenance=EvidenceProvenance(request_id="req-eval-1", crawler_task_id="ct-1", crawler_id="c-1", question_id="q-1"),
            extracted_fact="RocksDB slows writes when L0 file count reaches level0_slowdown_writes_trigger.",
        )
        ev2 = EvidenceItem(
            evidence_id="ev-2",
            provenance=EvidenceProvenance(request_id="req-eval-1", crawler_task_id="ct-2", crawler_id="c-1", question_id="q-1"),
            extracted_fact="RocksDB completely stops writes when L0 files reach level0_stop_writes_trigger.",
        )
        self.state.add_evidence(ev1)
        self.state.add_evidence(ev2)

        sufficiency, gaps = EvidenceEvaluator.evaluate_coverage(self.state)
        self.assertEqual(sufficiency, EvidenceSufficiency.SUFFICIENT)
        self.assertEqual(len(gaps), 0)
        self.assertEqual(q1.status, ResearchQuestionStatus.ANSWERED)
        self.assertEqual(q1.sufficiency, EvidenceSufficiency.SUFFICIENT)

    def test_evaluate_coverage_with_insufficient_evidence_and_knowledge_gaps(self):
        q1 = ResearchQuestion(question_id="q-1", question_text="What is the p99 latency in RocksDB under 100k writes/sec?")
        self.state.add_question(q1)

        sufficiency, gaps = EvidenceEvaluator.evaluate_coverage(self.state)
        self.assertEqual(sufficiency, EvidenceSufficiency.INSUFFICIENT)
        self.assertEqual(len(gaps), 1)
        self.assertEqual(gaps[0].gap_id, "gap-q-1")
        self.assertIn("No reliable evidence", gaps[0].reason)
        self.assertEqual(q1.status, ResearchQuestionStatus.UNKNOWN)
        self.assertEqual(q1.sufficiency, EvidenceSufficiency.INSUFFICIENT)

    def test_detect_contradictions_preserves_conflicting_claims(self):
        finding_conflicting = ResearchFinding(
            finding_id="f-contra",
            claim="SQLite WAL mode supports concurrent writers.",
            confidence=ResearchConfidence.CONFLICTING,
            source_ids=["src-blog-1"],
            conflicting_source_ids=["src-official-docs"],
            reasoning="Blog post claims concurrent writers exist, but official documentation confirms only 1 writer allowed.",
        )
        contradictions = EvidenceEvaluator.detect_contradictions([finding_conflicting])
        self.assertEqual(len(contradictions), 1)
        self.assertEqual(contradictions[0].contradiction_id, "c-f-contra")
        self.assertEqual(contradictions[0].sources_a, ["src-blog-1"])
        self.assertEqual(contradictions[0].sources_b, ["src-official-docs"])

    def test_synthesizer_produces_result_and_manager_summary(self):
        q1 = ResearchQuestion(question_id="q-1", question_text="Is RocksDB embeddable?", status=ResearchQuestionStatus.ANSWERED)
        self.state.add_question(q1)

        ev = EvidenceItem(
            evidence_id="ev-1",
            provenance=EvidenceProvenance(request_id="req-eval-1", crawler_task_id="ct-1", crawler_id="c-1", question_id="q-1"),
            extracted_fact="RocksDB is an embedded key-value storage engine library.",
        )
        self.state.add_evidence(ev)

        result = ResearchSynthesizer.synthesize_result(self.state, report_path="research/rocksdb.md")
        self.assertEqual(result.status, ResearchResultStatus.VERIFIED)
        self.assertEqual(result.request_id, "req-eval-1")
        self.assertEqual(result.task_id, "t-1")
        self.assertTrue(len(result.findings) >= 1)
        self.assertIn("Key Findings", result.summary_for_manager)

        # Generate markdown report
        md_report = ResearchSynthesizer.generate_markdown_report(result)
        self.assertIn("# Research Report", md_report)
        self.assertIn("RocksDB Write Stall", md_report)


if __name__ == "__main__":
    unittest.main()
