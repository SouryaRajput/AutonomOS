from __future__ import annotations

import unittest

from core.research.contracts.intent import (
    ResearchIntent,
    TemporalScope,
    VersionScope,
)
from core.research.contracts.request import ResearchRequest, ResearchScope
from core.research.planning.extractor import (
    DeterministicRequestExtractor,
    ExtractedRequestElements,
    ExtractionProvenance,
    ExtractionUncertainty,
)
from core.research.types import (
    DesiredOutput,
    FreshnessRequirement,
    IntentType,
    SourceType,
)


class TestDeterministicRequestExtractor(unittest.TestCase):
    """
    Unit test suite verifying deterministic, bounded extraction of explicit elements
    from ResearchRequest instances without hallucinating inferred values.
    """

    def setUp(self):
        self.extractor = DeterministicRequestExtractor()

    def test_explicit_entities_and_standards(self):
        """Verify extraction of formal RFC/PEP standards, quoted tokens, PascalCase names, and known software."""
        req = ResearchRequest(
            request_id="req-1",
            project_id="proj-1",
            task_id="task-1",
            objective="Evaluate 'FastAPI' compliance with RFC 7519 and PEP 684 for PostgreSQL microservices.",
            questions=["Does FastAPI support OAuth2 tokens per RFC 6749?"],
            constraints=["Must integrate with Redis cache."],
        )
        res = self.extractor.extract(req)

        self.assertIn("RFC 7519", res.entities)
        self.assertIn("PEP 684", res.entities)
        self.assertIn("RFC 6749", res.entities)
        self.assertIn("FastAPI", res.entities)
        self.assertIn("PostgreSQL", res.entities)
        self.assertIn("OAuth2", res.entities)
        self.assertIn("Redis", res.entities)

        # Verify provenance tracks each entity
        fastapi_prov = [p for p in res.provenance if p.value == "FastAPI"]
        self.assertTrue(len(fastapi_prov) > 0)
        self.assertEqual(fastapi_prov[0].source_field, "objective")

    def test_subjects_extraction_from_patterns_and_metadata(self):
        """Verify high-level subject phrases are extracted from actionable framing and metadata."""
        req = ResearchRequest(
            request_id="req-2",
            project_id="proj-1",
            task_id="task-1",
            objective="How do I implement distributed worker queues using Redis?",
            metadata={"subjects": ["asynchronous job processing"]},
        )
        res = self.extractor.extract(req)

        self.assertIn("asynchronous job processing", res.subjects)
        self.assertTrue(any("distributed worker queues" in s for s in res.subjects))

    def test_comparison_targets_extraction(self):
        """Verify comparison targets extracted from 'X vs Y', 'compare X with Y', and choice options."""
        req1 = ResearchRequest(
            request_id="req-3a",
            project_id="proj-1",
            task_id="task-1",
            objective="Compare PostgreSQL with MySQL for time-series workloads.",
        )
        res1 = self.extractor.extract(req1)
        self.assertIn("PostgreSQL", res1.comparison_targets)
        self.assertIn("MySQL", res1.comparison_targets)

        req2 = ResearchRequest(
            request_id="req-3b",
            project_id="proj-1",
            task_id="task-1",
            objective="Kafka vs RabbitMQ latency under high load.",
        )
        res2 = self.extractor.extract(req2)
        self.assertIn("Kafka", res2.comparison_targets)
        self.assertIn("RabbitMQ", res2.comparison_targets)

        req3 = ResearchRequest(
            request_id="req-3c",
            project_id="proj-1",
            task_id="task-1",
            objective="Which should we choose: Celery or Airflow for batch DAGs?",
        )
        res3 = self.extractor.extract(req3)
        self.assertIn("Celery", res3.comparison_targets)
        self.assertIn("Airflow", res3.comparison_targets)

    def test_ambiguity_in_incomplete_comparison(self):
        """Verify comparative phrasing with fewer than 2 targets triggers ambiguity uncertainty."""
        req = ResearchRequest(
            request_id="req-4",
            project_id="proj-1",
            task_id="task-1",
            objective="Compare PostgreSQL performance against competitors.",
        )
        res = self.extractor.extract(req)
        # Should record an ambiguity for comparison_targets
        target_uncs = res.get_uncertainties("comparison_targets")
        self.assertTrue(len(target_uncs) > 0)
        self.assertTrue(target_uncs[0].is_ambiguous)

    def test_research_dimensions_extraction(self):
        """Verify standard dimensions (performance, security, cost, scalability) extracted from text & lists."""
        req = ResearchRequest(
            request_id="req-5",
            project_id="proj-1",
            task_id="task-1",
            objective="Evaluate RabbitMQ clustering. Dimensions: performance, security, and scalability.",
            constraints=["Must optimize for low latency and high throughput."],
        )
        res = self.extractor.extract(req)
        self.assertIn("performance", res.research_dimensions)
        self.assertIn("security", res.research_dimensions)
        self.assertIn("scalability", res.research_dimensions)

    def test_explicit_constraints_extraction(self):
        """Verify request constraints, domain scope, and imperative directives are captured as explicit constraints."""
        req = ResearchRequest(
            request_id="req-6",
            project_id="proj-1",
            task_id="task-1",
            objective="Find libraries for JWT token parsing. You must not use deprecated PyJWT 1.x.",
            scope=ResearchScope(
                allowed_domains=["github.com", "python.org"],
                excluded_domains=["untrusted.io"],
                timeout_seconds=120,
            ),
            constraints=["Memory footprint under 50MB", "Zero native C extensions"],
        )
        res = self.extractor.extract(req)

        self.assertIn("Memory footprint under 50MB", res.explicit_constraints)
        self.assertIn("Zero native C extensions", res.explicit_constraints)
        self.assertTrue(any("allowed_domains" in c for c in res.explicit_constraints))
        self.assertTrue(any("excluded_domains" in c for c in res.explicit_constraints))
        self.assertTrue(any("timeout_limit: 120s" in c for c in res.explicit_constraints))
        self.assertTrue(any("must not use deprecated PyJWT 1.x" in c for c in res.explicit_constraints))

    def test_temporal_scope_date_ranges_and_recency(self):
        """Verify ISO date ranges, relative windows, and recency days are mapped to TemporalScope."""
        # Case A: Scope recency_days
        req_a = ResearchRequest(
            request_id="req-7a",
            project_id="p1",
            task_id="t1",
            objective="Check latest Python CVEs.",
            scope=ResearchScope(recency_days=14),
        )
        res_a = self.extractor.extract(req_a)
        self.assertIsNotNone(res_a.temporal_scope)
        self.assertEqual(res_a.temporal_scope.recency_days, 14)

        # Case B: ISO date range
        req_b = ResearchRequest(
            request_id="req-7b",
            project_id="p1",
            task_id="t1",
            objective="Security advisories from 2024-01-01 to 2024-06-30.",
        )
        res_b = self.extractor.extract(req_b)
        self.assertIsNotNone(res_b.temporal_scope)
        self.assertEqual(res_b.temporal_scope.start_date, "2024-01-01")
        self.assertEqual(res_b.temporal_scope.end_date, "2024-06-30")

        # Case C: Relative window in text
        req_c = ResearchRequest(
            request_id="req-7c",
            project_id="p1",
            task_id="t1",
            objective="Review Python package updates in the past 30 days.",
        )
        res_c = self.extractor.extract(req_c)
        self.assertIsNotNone(res_c.temporal_scope)
        self.assertEqual(res_c.temporal_scope.recency_days, 30)

    def test_version_scope_ecosystem_and_semver(self):
        """Verify package/ecosystem version patterns are extracted into typed VersionScope."""
        # Case A: Python 3.12+
        req_a = ResearchRequest(
            request_id="req-8a",
            project_id="p1",
            task_id="t1",
            objective="Verify subinterpreter support in Python 3.12+.",
        )
        res_a = self.extractor.extract(req_a)
        self.assertIsNotNone(res_a.version_scope)
        self.assertEqual(res_a.version_scope.ecosystem, "Python")
        self.assertEqual(res_a.version_scope.target_version, "3.12+")

        # Case B: Python >= 3.11
        req_b = ResearchRequest(
            request_id="req-8b",
            project_id="p1",
            task_id="t1",
            objective="Libraries requiring Python >= 3.11.",
        )
        res_b = self.extractor.extract(req_b)
        self.assertIsNotNone(res_b.version_scope)
        self.assertEqual(res_b.version_scope.ecosystem, "Python")
        self.assertEqual(res_b.version_scope.version_specifier, ">= 3.11")

        # Case C: Standalone semver
        req_c = ResearchRequest(
            request_id="req-8c",
            project_id="p1",
            task_id="t1",
            objective="Changelog for v2.4.0 release.",
        )
        res_c = self.extractor.extract(req_c)
        self.assertIsNotNone(res_c.version_scope)
        self.assertEqual(res_c.version_scope.target_version, "2.4.0")

    def test_geographic_scope_tokens_and_regulations(self):
        """Verify geographic regions and jurisdiction-tied frameworks (GDPR, CCPA, HIPAA) are extracted."""
        req1 = ResearchRequest(
            request_id="req-9a",
            project_id="p1",
            task_id="t1",
            objective="Data residency compliance under GDPR.",
        )
        res1 = self.extractor.extract(req1)
        self.assertEqual(res1.geographic_scope, "EU (GDPR)")

        req2 = ResearchRequest(
            request_id="req-9b",
            project_id="p1",
            task_id="t1",
            objective="Cloud latency benchmarks across APAC regions.",
        )
        res2 = self.extractor.extract(req2)
        self.assertEqual(res2.geographic_scope, "APAC")

        req3 = ResearchRequest(
            request_id="req-9c",
            project_id="p1",
            task_id="t1",
            objective="Global CDN deployment strategies.",
        )
        res3 = self.extractor.extract(req3)
        self.assertEqual(res3.geographic_scope, "GLOBAL")

    def test_freshness_requirement_extraction(self):
        """Verify freshness requirement extracted from recency days, metadata, and lexical cues."""
        req1 = ResearchRequest(
            request_id="req-10a",
            project_id="p1",
            task_id="t1",
            objective="Breaking real-time updates on security vulnerability.",
        )
        res1 = self.extractor.extract(req1)
        self.assertEqual(res1.freshness_requirement, FreshnessRequirement.CURRENT)

        req2 = ResearchRequest(
            request_id="req-10b",
            project_id="p1",
            task_id="t1",
            objective="History of Python concurrency model over time.",
        )
        res2 = self.extractor.extract(req2)
        self.assertEqual(res2.freshness_requirement, FreshnessRequirement.TIME_RANGE)

        req3 = ResearchRequest(
            request_id="req-10c",
            project_id="p1",
            task_id="t1",
            objective="Recent updates in Django ORM.",
            scope=ResearchScope(recency_days=20),
        )
        res3 = self.extractor.extract(req3)
        self.assertEqual(res3.freshness_requirement, FreshnessRequirement.RECENT)

    def test_desired_output_extraction(self):
        """Verify desired output format extracted from required_output_format and text directives."""
        req1 = ResearchRequest(
            request_id="req-11a",
            project_id="p1",
            task_id="t1",
            objective="Provide a step-by-step guide for setting up Celery.",
        )
        res1 = self.extractor.extract(req1)
        self.assertEqual(res1.desired_output, DesiredOutput.IMPLEMENTATION_GUIDANCE)

        req2 = ResearchRequest(
            request_id="req-11b",
            project_id="p1",
            task_id="t1",
            objective="Compare RabbitMQ and Kafka.",
            required_output_format="comparison_matrix",
        )
        res2 = self.extractor.extract(req2)
        self.assertEqual(res2.desired_output, DesiredOutput.COMPARISON)

        req3 = ResearchRequest(
            request_id="req-11c",
            project_id="p1",
            task_id="t1",
            objective="Architectural decision brief for cloud database migration.",
        )
        res3 = self.extractor.extract(req3)
        self.assertEqual(res3.desired_output, DesiredOutput.DECISION_BRIEF)

    def test_evidence_requirements_extraction(self):
        """Verify preferred source types and text requirements map to EvidenceRequirement."""
        req = ResearchRequest(
            request_id="req-12",
            project_id="p1",
            task_id="t1",
            objective="Official documentation analysis of Docker networking.",
            scope=ResearchScope(
                preferred_source_types=[SourceType.OFFICIAL_DOCUMENTATION, SourceType.REPOSITORY],
                min_evidence_per_question=2,
            ),
        )
        res = self.extractor.extract(req)
        self.assertTrue(len(res.evidence_requirements) > 0)
        req_item = res.evidence_requirements[0]
        self.assertIn(SourceType.OFFICIAL_DOCUMENTATION, req_item.source_types)
        self.assertEqual(req_item.min_independent_sources, 2)

    def test_preservation_of_uncertainty_for_missing_fields(self):
        """Verify that absent fields remain None without hallucination, and record ExtractionUncertainty."""
        # Highly minimal request
        req = ResearchRequest(
            request_id="req-13",
            project_id="p1",
            task_id="t1",
            objective="Explain garbage collection algorithms.",
        )
        res = self.extractor.extract(req)

        # None of these were stated:
        self.assertIsNone(res.temporal_scope)
        self.assertIsNone(res.geographic_scope)
        self.assertIsNone(res.version_scope)
        self.assertEqual(res.comparison_targets, [])

        # Uncertainty records exist
        self.assertTrue(res.has_uncertainty("temporal_scope"))
        self.assertTrue(res.has_uncertainty("geographic_scope"))
        self.assertTrue(res.has_uncertainty("version_scope"))

        # None of these unstated fields are marked as ambiguous (they are simply unconstrained)
        temp_unc = res.get_uncertainties("temporal_scope")[0]
        self.assertFalse(temp_unc.is_ambiguous)

    def test_non_semantic_confidence_bounds(self):
        """Verify all deterministic extraction confidence scores are strictly <= 0.95."""
        req = ResearchRequest(
            request_id="req-14",
            project_id="p1",
            task_id="t1",
            objective="Compare PostgreSQL with MySQL under GDPR in Python 3.12.",
            scope=ResearchScope(recency_days=7),
            required_output_format="comparison_matrix",
            metadata={"entities": ["PostgreSQL", "MySQL"]},
        )
        res = self.extractor.extract(req)

        for dim_name, score in res.confidence.items():
            self.assertLessEqual(score, 0.95, f"Confidence for {dim_name} exceeded 0.95: {score}")

        for prov in res.provenance:
            self.assertLessEqual(prov.confidence, 0.95, f"Provenance confidence exceeded 0.95: {prov.confidence}")

    def test_composability_with_research_intent(self):
        """Verify apply_to_intent composes extracted elements into ResearchIntent safely."""
        req = ResearchRequest(
            request_id="req-15",
            project_id="p1",
            task_id="t1",
            objective="Evaluate Redis vs Memcached for session storage under HIPAA.",
            required_output_format="comparison_matrix",
            constraints=["Must be in-memory"],
        )
        res = self.extractor.extract(req)

        intent = ResearchIntent(
            objective=req.objective,
            intent_types=[IntentType.COMPARATIVE, IntentType.EVALUATIVE],
        )

        res.apply_to_intent(intent)

        self.assertIn("Redis", intent.entities)
        self.assertIn("Memcached", intent.entities)
        self.assertIn("Redis", intent.comparison_targets)
        self.assertIn("Memcached", intent.comparison_targets)
        self.assertIn("Must be in-memory", intent.explicit_constraints)
        self.assertEqual(intent.geographic_scope, "US (HIPAA)")
        self.assertEqual(intent.desired_output, DesiredOutput.COMPARISON)
        # Verify inferred_constraints was untouched
        self.assertEqual(intent.inferred_constraints, [])

    def test_blocking_ambiguity_propagation_to_intent(self):
        """Verify blocking uncertainties in extraction propagate to intent.ambiguities and set clarification_required."""
        req = ResearchRequest(
            request_id="req-16",
            project_id="p1",
            task_id="t1",
            objective="Compare FastAPI with alternatives.",
        )
        res = self.extractor.extract(req)
        # Should have ambiguous comparison_targets uncertainty
        self.assertTrue(any(u.is_ambiguous for u in res.uncertainties))

        intent = ResearchIntent(objective=req.objective)
        res.apply_to_intent(intent)

        self.assertTrue(intent.clarification_required)
        self.assertTrue(len(intent.ambiguities) > 0)
        self.assertTrue(any("comparison_targets" in a.affected_fields for a in intent.ambiguities))

    def test_serialization_roundtrip(self):
        """Verify bidirectional serialization of ExtractedRequestElements, Provenance, and Uncertainty."""
        req = ResearchRequest(
            request_id="req-17",
            project_id="p1",
            task_id="t1",
            objective="Compare FastAPI with Flask in Python 3.12.",
            scope=ResearchScope(recency_days=30),
            required_output_format="comparison_matrix",
        )
        res = self.extractor.extract(req)
        res_dict = res.to_dict()

        restored = ExtractedRequestElements.from_dict(res_dict)

        self.assertEqual(restored.source_request_id, res.source_request_id)
        self.assertEqual(restored.comparison_targets, res.comparison_targets)
        self.assertEqual(restored.entities, res.entities)
        self.assertEqual(restored.temporal_scope.recency_days, res.temporal_scope.recency_days)
        self.assertEqual(restored.version_scope.target_version, res.version_scope.target_version)
        self.assertEqual(restored.desired_output, res.desired_output)
        self.assertEqual(len(restored.provenance), len(res.provenance))
        self.assertEqual(len(restored.uncertainties), len(res.uncertainties))

    def test_idempotent_extraction(self):
        """Verify repeated extraction calls on the same request return identical results."""
        req = ResearchRequest(
            request_id="req-18",
            project_id="p1",
            task_id="t1",
            objective="Evaluate MongoDB vs PostgreSQL for IoT telemetry in EU.",
            constraints=["Encryption at rest mandatory"],
        )

        res1 = self.extractor.extract(req)
        res2 = self.extractor.extract(req)

        self.assertEqual(res1.entities, res2.entities)
        self.assertEqual(res1.comparison_targets, res2.comparison_targets)
        self.assertEqual(res1.research_dimensions, res2.research_dimensions)
        self.assertEqual(res1.explicit_constraints, res2.explicit_constraints)
        self.assertEqual(res1.geographic_scope, res2.geographic_scope)
        self.assertEqual(len(res1.provenance), len(res2.provenance))
        self.assertEqual(len(res1.uncertainties), len(res2.uncertainties))

    def test_invalid_input_type_raises(self):
        """Verify TypeError when non-ResearchRequest passed."""
        with self.assertRaises(TypeError):
            self.extractor.extract("not a request")  # type: ignore


if __name__ == "__main__":
    unittest.main()
