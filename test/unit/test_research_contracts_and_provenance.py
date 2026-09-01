from __future__ import annotations

import unittest
import uuid

from core.models import Task
from core.research.contracts.crawler_report import CrawlerReport, RawSourceReference
from core.research.contracts.crawler_task import CrawlerTask
from core.research.contracts.evidence import (
    EvidenceItem,
    EvidenceProvenance,
    Source,
    compute_sha256,
)
from core.research.contracts.plan import ResearchPlan
from core.research.contracts.question import ResearchQuestion
from core.research.contracts.request import ResearchRequest, ResearchScope
from core.research.contracts.result import (
    ResearchContradiction,
    ResearchFinding,
    ResearchKnowledgeGap,
    ResearchRecommendation,
    ResearchResult,
)
from core.research.types import (
    CrawlerCapability,
    CrawlerReportStatus,
    CrawlerTaskStatus,
    EvidenceSufficiency,
    FactClassification,
    ResearchConfidence,
    ResearchMode,
    ResearchQuestionStatus,
    ResearchResultStatus,
    SourceType,
)


class TestResearchContractsAndProvenance(unittest.TestCase):
    """Unit tests for research contracts, lineage tracking, and cryptographic provenance."""

    def test_research_request_from_runtime_task(self):
        task = Task(
            id="t-001",
            project_id="proj-001",
            title="Investigate Arrow Flight SQL",
            objective="Evaluate Arrow Flight SQL for columnar transfer",
            metadata={
                "mode": "DEEP",
                "allowed_domains": ["arrow.apache.org"],
                "questions": ["Does Flight SQL support prepared statements?"],
                "scope": {"max_crawlers": 4},
            },
        )
        req = ResearchRequest.from_task(task)
        self.assertEqual(req.task_id, "t-001")
        self.assertEqual(req.project_id, "proj-001")
        self.assertEqual(req.mode, ResearchMode.DEEP)
        self.assertEqual(req.scope.max_crawlers, 4)
        self.assertEqual(req.scope.allowed_domains, ["arrow.apache.org"])
        self.assertEqual(req.questions, ["Does Flight SQL support prepared statements?"])

        data = req.to_dict()
        restored = ResearchRequest.from_dict(data)
        self.assertEqual(restored.request_id, req.request_id)
        self.assertEqual(restored.mode, ResearchMode.DEEP)

    def test_crawler_task_lineage_to_request_and_question(self):
        task = CrawlerTask(
            task_id="ctask-q1-web-1",
            request_id="req-123",
            plan_id="plan-456",
            question_id="q-1",
            query_or_target="arrow flight sql prepared statements",
            required_capability=CrawlerCapability.WEB_SEARCH,
            correlation_id="corr-789",
        )
        data = task.to_dict()
        self.assertEqual(data["request_id"], "req-123")
        self.assertEqual(data["question_id"], "q-1")
        self.assertEqual(data["required_capability"], "WEB_SEARCH")

        restored = CrawlerTask.from_dict(data)
        self.assertEqual(restored.task_id, "ctask-q1-web-1")
        self.assertEqual(restored.request_id, "req-123")
        self.assertEqual(restored.plan_id, "plan-456")
        self.assertEqual(restored.required_capability, CrawlerCapability.WEB_SEARCH)

    def test_evidence_item_cryptographic_checksum_and_provenance(self):
        prov = EvidenceProvenance(
            request_id="req-100",
            crawler_task_id="ctask-q1-1",
            crawler_id="crawler.web.1",
            question_id="q-1",
            source_ref="https://arrow.apache.org/docs/flight_sql.html",
            correlation_id="corr-100",
        )
        fact_text = "Apache Arrow Flight SQL protocol supports parameterized prepared statements."
        snippet_text = "Clients can prepare statements using CommandPreparedStatementQuery."

        evidence = EvidenceItem(
            evidence_id="ev-1001",
            provenance=prov,
            extracted_fact=fact_text,
            content_snippet=snippet_text,
            classification=FactClassification.FACT,
            confidence=ResearchConfidence.WELL_SUPPORTED,
            source_type=SourceType.OFFICIAL_DOCUMENTATION,
        )

        expected_checksum = compute_sha256(f"{fact_text}|{snippet_text}")
        self.assertEqual(evidence.checksum, expected_checksum)

        # Verify serialization roundtrip
        data = evidence.to_dict()
        self.assertEqual(data["provenance"]["crawler_id"], "crawler.web.1")
        self.assertEqual(data["checksum"], expected_checksum)

        restored = EvidenceItem.from_dict(data)
        self.assertEqual(restored.evidence_id, "ev-1001")
        self.assertEqual(restored.provenance.crawler_task_id, "ctask-q1-1")
        self.assertEqual(restored.checksum, expected_checksum)

        # Test conversion to standard runtime Evidence dataclass
        runtime_ev = evidence.to_runtime_evidence("t-001")
        self.assertEqual(runtime_ev.id, "ev-1001")
        self.assertEqual(runtime_ev.task_id, "t-001")
        self.assertEqual(runtime_ev.checksum, expected_checksum)
        self.assertIn("OFFICIAL_DOCUMENTATION", runtime_ev.data)

    def test_crawler_report_traceability_and_failure_states(self):
        # Successful report with evidence
        prov = EvidenceProvenance(request_id="req-1", crawler_task_id="ct-1", crawler_id="c-1")
        ev = EvidenceItem(evidence_id="ev-1", provenance=prov, extracted_fact="Fact 1")
        raw_src = RawSourceReference(url_or_ref="https://sqlite.org", title="SQLite Docs", bytes_fetched=1024)

        report = CrawlerReport(
            report_id="crep-1",
            crawler_task_id="ct-1",
            crawler_id="c-1",
            request_id="req-1",
            status=CrawlerReportStatus.SUCCESS,
            raw_sources=[raw_src],
            extracted_evidence=[ev],
            summary="Fetched 1 source and extracted 1 evidence item",
            execution_time_seconds=0.45,
        )

        data = report.to_dict()
        self.assertEqual(data["status"], "SUCCESS")
        self.assertEqual(len(data["extracted_evidence"]), 1)

        restored = CrawlerReport.from_dict(data)
        self.assertEqual(restored.report_id, "crep-1")
        self.assertEqual(restored.status, CrawlerReportStatus.SUCCESS)

        # Failed report representation
        failed_report = CrawlerReport(
            report_id="crep-err-1",
            crawler_task_id="ct-2",
            crawler_id="c-2",
            request_id="req-1",
            status=CrawlerReportStatus.FAILED,
            error_message="Connection timed out after 30s",
        )
        self.assertEqual(failed_report.status, CrawlerReportStatus.FAILED)
        self.assertIn("Connection timed out", failed_report.error_message)

    def test_research_result_distinguishes_verified_vs_partial_vs_insufficient(self):
        # 1. Verified Result
        res_verified = ResearchResult(
            task_id="t-1",
            request_id="req-1",
            project_id="proj-1",
            objective="Evaluate Arrow",
            status=ResearchResultStatus.VERIFIED,
            findings=[ResearchFinding(finding_id="f-1", claim="Verified statement")],
        )
        self.assertEqual(res_verified.status, ResearchResultStatus.VERIFIED)

        # 2. Partial Result
        res_partial = ResearchResult(
            task_id="t-2",
            request_id="req-2",
            project_id="proj-1",
            objective="Evaluate Framework X",
            status=ResearchResultStatus.PARTIAL,
            knowledge_gaps=[ResearchKnowledgeGap(gap_id="g-1", topic="Benchmarks", question="Latency?", reason="No data")],
        )
        self.assertEqual(res_partial.status, ResearchResultStatus.PARTIAL)
        self.assertEqual(len(res_partial.knowledge_gaps), 1)

        # 3. Insufficient Evidence Result
        res_insufficient = ResearchResult(
            task_id="t-3",
            request_id="req-3",
            project_id="proj-1",
            objective="Evaluate Secret Feature",
            status=ResearchResultStatus.INSUFFICIENT_EVIDENCE,
            summary_for_manager="Insufficient evidence to make verified claims.",
        )
        self.assertEqual(res_insufficient.status, ResearchResultStatus.INSUFFICIENT_EVIDENCE)


if __name__ == "__main__":
    unittest.main()
