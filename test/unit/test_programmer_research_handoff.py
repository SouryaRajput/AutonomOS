from __future__ import annotations

import copy
import unittest

from core.programmer.contracts.handoff import EngineeringHandoff
from core.programmer.contracts.research_adapter import (
    EpistemicContextItem,
    ResearchToWorkOrderAdapter,
)
from core.programmer.contracts.work_order import ProgrammerWorkOrder
from core.programmer.errors import (
    ConflictingResearchError,
    MissingResearchEvidenceError,
    ProgrammerValidationError,
)
from core.programmer.types import (
    EngineeringHandoffType,
    EpistemicContextType,
    HandoffPriority,
)
from core.research.contracts.evidence import EvidenceItem, EvidenceProvenance
from core.research.contracts.result import (
    ResearchContradiction,
    ResearchFinding,
    ResearchRecommendation,
    ResearchResult,
)
from core.research.types import FactClassification, ResearchConfidence, SourceType


class TestProgrammerResearchHandoff(unittest.TestCase):
    """Unit tests for Phase 8.2: Research -> Programmer Handoff Adapter."""

    def setUp(self) -> None:
        self.prov_1 = EvidenceProvenance(
            request_id="req-res-001",
            crawler_task_id="ctask-101",
            crawler_id="crawl-web-1",
            source_ref="https://docs.example.com/api",
        )
        self.evidence_1 = EvidenceItem(
            evidence_id="ev-001",
            provenance=self.prov_1,
            extracted_fact="Endpoints support gzip and brotli compression",
            content_snippet="gzip, brotli supported",
            classification=FactClassification.FACT,
            confidence=ResearchConfidence.WELL_SUPPORTED,
            source_type=SourceType.OFFICIAL_DOCUMENTATION,
        )
        self.evidence_2 = EvidenceItem(
            evidence_id="ev-002",
            provenance=self.prov_1,
            extracted_fact="Rate limit is 100 requests per minute per IP",
            content_snippet="100 req/min limit",
            classification=FactClassification.FACT,
            confidence=ResearchConfidence.SUPPORTED,
            source_type=SourceType.OFFICIAL_DOCUMENTATION,
        )
        self.evidence_3 = EvidenceItem(
            evidence_id="ev-003",
            provenance=self.prov_1,
            extracted_fact="Authentication uses HMAC-SHA256 headers",
            content_snippet="HMAC headers required",
            classification=FactClassification.FACT,
            confidence=ResearchConfidence.WELL_SUPPORTED,
            source_type=SourceType.OFFICIAL_DOCUMENTATION,
        )

        self.finding_1 = ResearchFinding(
            finding_id="f-001",
            claim="Redis clustering requires minimal 3 master nodes",
            classification=FactClassification.FACT,
            confidence=ResearchConfidence.WELL_SUPPORTED,
            evidence_ids=["ev-001"],
        )

        self.recommendation_1 = ResearchRecommendation(
            recommendation_id="rec-001",
            action="Use Redis connection pooling with keepalive=30s",
            rationale="Reduces TCP handshake overhead under high concurrency",
        )
        self.recommendation_2 = ResearchRecommendation(
            recommendation_id="rec-002",
            action="Migrate all endpoints to GraphQL",
            rationale="Reduces over-fetching",
        )

        self.research_result = ResearchResult(
            task_id="rtask-500",
            request_id="req-res-001",
            project_id="proj-e2e",
            objective="Evaluate external API integration and caching architecture",
            evidence=[self.evidence_1, self.evidence_2, self.evidence_3],
            findings=[self.finding_1],
            recommendations=[self.recommendation_1, self.recommendation_2],
            contradictions=[],
        )

    # =========================================================================
    # Scenario 1: Research-backed WorkOrder construction and lineage preservation
    # =========================================================================
    def test_01_research_backed_work_order(self) -> None:
        """Scenario 1: Manager transforms ResearchResult into bounded ProgrammerWorkOrder with complete lineage."""
        wo = ResearchToWorkOrderAdapter.transform(
            research_result=self.research_result,
            manager_task_id="mtask-eng-900",
            objective="Implement client API adapter for external service",
            selected_evidence_ids=["ev-001"],
            manager_requirements=["Support response decompression"],
            allowed_paths=["src/client/"],
            writable_paths=["src/client/"],
        )

        self.assertIsInstance(wo, ProgrammerWorkOrder)
        self.assertEqual(wo.manager_task_id, "mtask-eng-900")
        self.assertEqual(wo.project_id, "proj-e2e")
        self.assertEqual(wo.objective, "Implement client API adapter for external service")

        # Verify lineage in metadata and trace
        self.assertEqual(wo.metadata["source_research_request_id"], "req-res-001")
        self.assertEqual(wo.metadata["source_research_task_id"], "rtask-500")
        self.assertEqual(wo.trace["manager_task_id"], "mtask-eng-900")
        self.assertEqual(wo.trace["source_research_request_id"], "req-res-001")

    # =========================================================================
    # Scenario 2: Evidence linkage
    # =========================================================================
    def test_02_evidence_linkage(self) -> None:
        """Scenario 2: Attached research evidence preserves IDs, facts, provenance, and confidence."""
        wo = ResearchToWorkOrderAdapter.transform(
            research_result=self.research_result,
            manager_task_id="mtask-901",
            objective="Implement auth header signing",
            selected_evidence_ids=["ev-003"],
        )

        self.assertEqual(len(wo.research_evidence), 1)
        ref = wo.research_evidence[0]
        self.assertEqual(ref.evidence_id, "ev-003")
        self.assertEqual(ref.claim_or_fact, "Authentication uses HMAC-SHA256 headers")
        self.assertEqual(ref.source_ref, "https://docs.example.com/api")
        self.assertEqual(ref.confidence, "WELL_SUPPORTED")
        self.assertEqual(ref.provenance["crawler_id"], "crawl-web-1")

    # =========================================================================
    # Scenario 3: Selected vs unselected evidence
    # =========================================================================
    def test_03_selected_vs_unselected_evidence(self) -> None:
        """Scenario 3: Only evidence explicitly selected by Manager is attached; unselected evidence is excluded."""
        wo = ResearchToWorkOrderAdapter.transform(
            research_result=self.research_result,
            manager_task_id="mtask-902",
            objective="Implement rate limiting handler",
            selected_evidence_ids=["ev-002"],
        )

        attached_ids = [e.evidence_id for e in wo.research_evidence]
        self.assertEqual(attached_ids, ["ev-002"])
        self.assertNotIn("ev-001", attached_ids)
        self.assertNotIn("ev-003", attached_ids)

        # Unselected count verified
        self.assertEqual(wo.metadata["unselected_evidence_count"], 3)  # ev-001, ev-003, f-001

        # No selection attaches zero evidence
        wo_empty = ResearchToWorkOrderAdapter.transform(
            research_result=self.research_result,
            manager_task_id="mtask-902",
            objective="Clean implementation without direct evidence",
            selected_evidence_ids=[],
        )
        self.assertEqual(len(wo_empty.research_evidence), 0)

    # =========================================================================
    # Scenario 4: Recommendation vs requirement distinction
    # =========================================================================
    def test_04_recommendation_vs_requirement_distinction(self) -> None:
        """Scenario 4: Recommendations are NEVER silently converted to requirements unless explicitly accepted by Manager."""
        # Case A: Manager does NOT accept any recommendations
        wo_unaccepted = ResearchToWorkOrderAdapter.transform(
            research_result=self.research_result,
            manager_task_id="mtask-903",
            objective="Build caching layer",
            manager_requirements=["Cache TTL must be 60s"],
            accepted_recommendation_ids=[],
        )

        # Neither rec-001 nor rec-002 should appear in technical_requirements
        self.assertEqual(wo_unaccepted.technical_requirements, ["Cache TTL must be 60s"])
        # Both appear in advisory context only
        advisory_ids = [r["recommendation_id"] for r in wo_unaccepted.context["advisory_recommendations"]]
        self.assertIn("rec-001", advisory_ids)
        self.assertIn("rec-002", advisory_ids)

        # Case B: Manager explicitly accepts rec-001
        wo_accepted = ResearchToWorkOrderAdapter.transform(
            research_result=self.research_result,
            manager_task_id="mtask-903",
            objective="Build caching layer with connection pool",
            manager_requirements=["Cache TTL must be 60s"],
            accepted_recommendation_ids=["rec-001"],
        )

        self.assertEqual(len(wo_accepted.technical_requirements), 2)
        self.assertEqual(wo_accepted.technical_requirements[0], "Cache TTL must be 60s")
        self.assertIn("rec-001", wo_accepted.technical_requirements[1])
        # rec-002 remains unaccepted
        advisory_ids_b = [r["recommendation_id"] for r in wo_accepted.context["advisory_recommendations"]]
        self.assertNotIn("rec-001", advisory_ids_b)
        self.assertIn("rec-002", advisory_ids_b)

    # =========================================================================
    # Scenario 5: Missing research evidence
    # =========================================================================
    def test_05_missing_research_evidence(self) -> None:
        """Scenario 5: Requesting non-existent research evidence raises MissingResearchEvidenceError."""
        with self.assertRaises(MissingResearchEvidenceError) as ctx:
            ResearchToWorkOrderAdapter.transform(
                research_result=self.research_result,
                manager_task_id="mtask-904",
                objective="Implement missing component",
                selected_evidence_ids=["ev-nonexistent-999"],
            )
        self.assertEqual(ctx.exception.evidence_id, "ev-nonexistent-999")

    # =========================================================================
    # Scenario 6: Conflicting research findings
    # =========================================================================
    def test_06_conflicting_research_findings(self) -> None:
        """Scenario 6: Unresolved contradictions raise ConflictingResearchError; resolved contradictions are recorded."""
        contradiction = ResearchContradiction(
            contradiction_id="c-001",
            topic="Database Concurrency Locking",
            claim_a="SQLite WAL mode supports concurrent writers",
            sources_a=["https://sqlite.org/wal"],
            claim_b="SQLite WAL mode restricts writes to a single thread",
            sources_b=["https://sqlite.org/locking"],
            analysis="WAL allows one writer and multiple concurrent readers.",
        )

        conflicted_result = copy.deepcopy(self.research_result)
        conflicted_result.contradictions = [contradiction]

        # Case A: Without resolution -> raises ConflictingResearchError
        with self.assertRaises(ConflictingResearchError) as ctx:
            ResearchToWorkOrderAdapter.transform(
                research_result=conflicted_result,
                manager_task_id="mtask-905",
                objective="Design database locking architecture",
            )
        self.assertEqual(ctx.exception.contradiction_id, "c-001")
        self.assertIn("Manager resolution required", str(ctx.exception))

        # Case B: With explicit Manager resolution -> succeeds
        wo = ResearchToWorkOrderAdapter.transform(
            research_result=conflicted_result,
            manager_task_id="mtask-905",
            objective="Design database locking architecture",
            conflict_resolutions={"c-001": "Assume single-writer with multi-reader concurrency"},
        )
        self.assertEqual(
            wo.context["conflict_resolutions"]["c-001"],
            "Assume single-writer with multi-reader concurrency",
        )

    # =========================================================================
    # Scenario 7: Epistemic categorization
    # =========================================================================
    def test_07_epistemic_categorization(self) -> None:
        """Scenario 7: Accurately categorizes observed evidence, inferences, manager mandates, and assumptions."""
        wo = ResearchToWorkOrderAdapter.transform(
            research_result=self.research_result,
            manager_task_id="mtask-906",
            objective="Implement resilient networking client",
            selected_evidence_ids=["ev-001"],
            manager_requirements=["Mandate exponential backoff retry"],
            inferred_requirements=["Payload sizes likely exceed 10MB based on compression finding"],
            engineering_assumptions=["Use httpx as underlying HTTP client"],
            accepted_recommendation_ids=["rec-001"],
        )

        epistemic_items = wo.context["epistemic_context"]
        types_present = {item["epistemic_type"] for item in epistemic_items}

        self.assertIn(EpistemicContextType.OBSERVED_RESEARCH_EVIDENCE.value, types_present)
        self.assertIn(EpistemicContextType.INFERRED_REQUIREMENTS.value, types_present)
        self.assertIn(EpistemicContextType.MANAGER_DEFINED_REQUIREMENTS.value, types_present)
        self.assertIn(EpistemicContextType.PROGRAMMER_ENGINEERING_ASSUMPTIONS.value, types_present)

    # =========================================================================
    # Scenario 8: Source immutability and handoff creation
    # =========================================================================
    def test_08_source_immutability_and_handoff_creation(self) -> None:
        """Scenario 8: ResearchResult remains unmodified and create_research_handoff builds valid EngineeringHandoff."""
        original_ev_count = len(self.research_result.evidence)
        original_rec_count = len(self.research_result.recommendations)

        # Transformation should not mutate source
        _ = ResearchToWorkOrderAdapter.transform(
            research_result=self.research_result,
            manager_task_id="mtask-907",
            objective="Verify non-mutation",
            selected_evidence_ids=["ev-001"],
            accepted_recommendation_ids=["rec-001"],
        )
        self.assertEqual(len(self.research_result.evidence), original_ev_count)
        self.assertEqual(len(self.research_result.recommendations), original_rec_count)

        # Build EngineeringHandoff directly
        handoff = ResearchToWorkOrderAdapter.create_research_handoff(
            research_result=self.research_result,
            manager_task_id="mtask-907",
            objective="Build caching module",
            selected_evidence_ids=["ev-001", "ev-002"],
            priority=HandoffPriority.HIGH,
        )
        self.assertIsInstance(handoff, EngineeringHandoff)
        self.assertEqual(handoff.handoff_type, EngineeringHandoffType.RESEARCH_TO_PROGRAMMER)
        self.assertEqual(handoff.source_worker_id, "worker-researcher")
        self.assertEqual(handoff.target_worker_id, "worker-programmer")
        self.assertEqual(handoff.priority, HandoffPriority.HIGH)
        self.assertEqual(len(handoff.evidence), 2)


if __name__ == "__main__":
    unittest.main()
