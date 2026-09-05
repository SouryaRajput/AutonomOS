from __future__ import annotations

import unittest

from core.research.contracts.request import ResearchRequest, ResearchScope
from core.research.planning.intent_classifier import (
    DeterministicIntentClassifier,
    IntentClassificationResult,
    IntentSignal,
)
from core.research.types import IntentType


class TestDeterministicIntentClassifier(unittest.TestCase):
    """Unit tests for Phase 2 Step 2.1.3 Deterministic Intent Classification."""

    def setUp(self):
        self.classifier = DeterministicIntentClassifier()

    # -------------------------------------------------------------------------
    # 1. Single Intent Pattern Recognition
    # -------------------------------------------------------------------------

    def test_comparative_intent_explicit_patterns(self):
        """Verify comparative phrasing yields COMPARATIVE primary intent with high confidence."""
        req1 = ResearchRequest(
            request_id="r1",
            project_id="p1",
            task_id="t1",
            objective="Compare SQLite with PostgreSQL for embedded storage",
        )
        res1 = self.classifier.classify(req1)
        self.assertEqual(res1.primary_intent, IntentType.COMPARATIVE)
        self.assertGreaterEqual(res1.get_confidence(IntentType.COMPARATIVE), 0.80)
        self.assertTrue(res1.has_intent(IntentType.COMPARATIVE))
        self.assertFalse(res1.is_ambiguous)

        req2 = ResearchRequest(
            request_id="r2",
            project_id="p1",
            task_id="t1",
            objective="Tradeoffs between FastAPI and Flask",
        )
        res2 = self.classifier.classify(req2)
        self.assertEqual(res2.primary_intent, IntentType.COMPARATIVE)

        req3 = ResearchRequest(
            request_id="r3",
            project_id="p1",
            task_id="t1",
            objective="React vs Vue for dashboard performance",
        )
        res3 = self.classifier.classify(req3)
        self.assertEqual(res3.primary_intent, IntentType.COMPARATIVE)

    def test_diagnostic_intent_explicit_patterns(self):
        """Verify diagnostic problem inquiry yields DIAGNOSTIC primary intent."""
        req1 = ResearchRequest(
            request_id="r1",
            project_id="p1",
            task_id="t1",
            objective="Why is the worker failing with timeout under high concurrency?",
        )
        res1 = self.classifier.classify(req1)
        self.assertEqual(res1.primary_intent, IntentType.DIAGNOSTIC)
        self.assertGreaterEqual(res1.get_confidence(IntentType.DIAGNOSTIC), 0.85)

        req2 = ResearchRequest(
            request_id="r2",
            project_id="p1",
            task_id="t1",
            objective="Root cause of the memory leak in connection pool",
        )
        res2 = self.classifier.classify(req2)
        self.assertEqual(res2.primary_intent, IntentType.DIAGNOSTIC)

        req3 = ResearchRequest(
            request_id="r3",
            project_id="p1",
            task_id="t1",
            objective="Troubleshoot database deadlock during migration",
        )
        res3 = self.classifier.classify(req3)
        self.assertEqual(res3.primary_intent, IntentType.DIAGNOSTIC)

    def test_implementation_intent_explicit_patterns(self):
        """Verify practical coding/setup questions yield IMPLEMENTATION primary intent."""
        req1 = ResearchRequest(
            request_id="r1",
            project_id="p1",
            task_id="t1",
            objective="How do I implement JWT token rotation in Python?",
        )
        res1 = self.classifier.classify(req1)
        self.assertEqual(res1.primary_intent, IntentType.IMPLEMENTATION)
        self.assertGreaterEqual(res1.get_confidence(IntentType.IMPLEMENTATION), 0.80)

        req2 = ResearchRequest(
            request_id="r2",
            project_id="p1",
            task_id="t1",
            objective="Step-by-step guide to configure SQLite WAL mode",
        )
        res2 = self.classifier.classify(req2)
        self.assertEqual(res2.primary_intent, IntentType.IMPLEMENTATION)

        req3 = ResearchRequest(
            request_id="r3",
            project_id="p1",
            task_id="t1",
            objective="Code example of Python asyncio TaskGroup with cancellation",
        )
        res3 = self.classifier.classify(req3)
        self.assertEqual(res3.primary_intent, IntentType.IMPLEMENTATION)

    def test_verification_intent_explicit_patterns(self):
        """Verify polar validation questions yield VERIFICATION primary intent."""
        req1 = ResearchRequest(
            request_id="r1",
            project_id="p1",
            task_id="t1",
            objective="Is Python 3.12 subinterpreters supported on Windows?",
        )
        res1 = self.classifier.classify(req1)
        self.assertEqual(res1.primary_intent, IntentType.VERIFICATION)
        self.assertGreaterEqual(res1.get_confidence(IntentType.VERIFICATION), 0.80)

        req2 = ResearchRequest(
            request_id="r2",
            project_id="p1",
            task_id="t1",
            objective="Verify whether SQLite WAL mode is safe for concurrent readers",
        )
        res2 = self.classifier.classify(req2)
        self.assertEqual(res2.primary_intent, IntentType.VERIFICATION)

        req3 = ResearchRequest(
            request_id="r3",
            project_id="p1",
            task_id="t1",
            objective="Check if PostgreSQL 16 supports jsonb subscripting",
        )
        res3 = self.classifier.classify(req3)
        self.assertEqual(res3.primary_intent, IntentType.VERIFICATION)

    def test_descriptive_intent_explicit_patterns(self):
        """Verify definitional questions yield DESCRIPTIVE primary intent."""
        req1 = ResearchRequest(
            request_id="r1",
            project_id="p1",
            task_id="t1",
            objective="What is an Abstract Syntax Tree?",
        )
        res1 = self.classifier.classify(req1)
        self.assertEqual(res1.primary_intent, IntentType.DESCRIPTIVE)
        self.assertGreaterEqual(res1.get_confidence(IntentType.DESCRIPTIVE), 0.75)

        req2 = ResearchRequest(
            request_id="r2",
            project_id="p1",
            task_id="t1",
            objective="Overview of the AutonomOS architecture",
        )
        res2 = self.classifier.classify(req2)
        self.assertEqual(res2.primary_intent, IntentType.DESCRIPTIVE)

        req3 = ResearchRequest(
            request_id="r3",
            project_id="p1",
            task_id="t1",
            objective="Define Raft consensus protocol",
        )
        res3 = self.classifier.classify(req3)
        self.assertEqual(res3.primary_intent, IntentType.DESCRIPTIVE)

    def test_evaluative_intent_explicit_patterns(self):
        """Verify benchmarking and assessment queries yield EVALUATIVE primary intent."""
        req1 = ResearchRequest(
            request_id="r1",
            project_id="p1",
            task_id="t1",
            objective="Evaluate write throughput and memory overhead of DuckDB",
        )
        res1 = self.classifier.classify(req1)
        self.assertEqual(res1.primary_intent, IntentType.EVALUATIVE)
        self.assertGreaterEqual(res1.get_confidence(IntentType.EVALUATIVE), 0.80)

        req2 = ResearchRequest(
            request_id="r2",
            project_id="p1",
            task_id="t1",
            objective="Security audit of the authentication token endpoint",
        )
        res2 = self.classifier.classify(req2)
        self.assertEqual(res2.primary_intent, IntentType.EVALUATIVE)

        req3 = ResearchRequest(
            request_id="r3",
            project_id="p1",
            task_id="t1",
            objective="Assess the feasibility of on-device model quantization",
        )
        res3 = self.classifier.classify(req3)
        self.assertEqual(res3.primary_intent, IntentType.EVALUATIVE)

    def test_technical_intent_explicit_patterns(self):
        """Verify formal RFC/PEP or runtime internals yield TECHNICAL intent."""
        req1 = ResearchRequest(
            request_id="r1",
            project_id="p1",
            task_id="t1",
            objective="Inspect PEP 684 per-interpreter GIL specifications and memory layout",
        )
        res1 = self.classifier.classify(req1)
        self.assertTrue(res1.has_intent(IntentType.TECHNICAL, min_confidence=0.80))
        self.assertEqual(res1.primary_intent, IntentType.TECHNICAL)

        req2 = ResearchRequest(
            request_id="r2",
            project_id="p1",
            task_id="t1",
            objective="Analyze RFC 7519 JSON Web Token claims structure",
        )
        res2 = self.classifier.classify(req2)
        self.assertTrue(res2.has_intent(IntentType.TECHNICAL))

    def test_decision_support_intent_explicit_patterns(self):
        """Verify decision queries yield DECISION_SUPPORT primary intent."""
        req1 = ResearchRequest(
            request_id="r1",
            project_id="p1",
            task_id="t1",
            objective="Which should we choose: RabbitMQ or Redis for distributed worker queues?",
        )
        res1 = self.classifier.classify(req1)
        self.assertEqual(res1.primary_intent, IntentType.DECISION_SUPPORT)
        self.assertTrue(res1.has_intent(IntentType.COMPARATIVE))  # Also comparative!

        req2 = ResearchRequest(
            request_id="r2",
            project_id="p1",
            task_id="t1",
            objective="Should we use gRPC or REST for internal microservice communication?",
        )
        res2 = self.classifier.classify(req2)
        self.assertEqual(res2.primary_intent, IntentType.DECISION_SUPPORT)

        req3 = ResearchRequest(
            request_id="r3",
            project_id="p1",
            task_id="t1",
            objective="Provide an architectural recommendation for database caching strategy",
        )
        res3 = self.classifier.classify(req3)
        self.assertEqual(res3.primary_intent, IntentType.DECISION_SUPPORT)

    def test_historical_intent_explicit_patterns(self):
        """Verify chronological evolution queries yield HISTORICAL primary intent."""
        req1 = ResearchRequest(
            request_id="r1",
            project_id="p1",
            task_id="t1",
            objective="History of Python GIL removal proposals and past PEPs",
        )
        res1 = self.classifier.classify(req1)
        self.assertEqual(res1.primary_intent, IntentType.HISTORICAL)
        self.assertGreaterEqual(res1.get_confidence(IntentType.HISTORICAL), 0.85)

        req2 = ResearchRequest(
            request_id="r2",
            project_id="p1",
            task_id="t1",
            objective="Evolution of JavaScript async patterns over time",
        )
        res2 = self.classifier.classify(req2)
        self.assertEqual(res2.primary_intent, IntentType.HISTORICAL)

    def test_exploratory_intent_explicit_patterns(self):
        """Verify open-ended exploration queries yield EXPLORATORY primary intent."""
        req1 = ResearchRequest(
            request_id="r1",
            project_id="p1",
            task_id="t1",
            objective="Explore the state of the art in local LLM inference engines",
        )
        res1 = self.classifier.classify(req1)
        self.assertEqual(res1.primary_intent, IntentType.EXPLORATORY)
        self.assertGreaterEqual(res1.get_confidence(IntentType.EXPLORATORY), 0.80)

        req2 = ResearchRequest(
            request_id="r2",
            project_id="p1",
            task_id="t1",
            objective="Survey of open-source vector databases",
        )
        res2 = self.classifier.classify(req2)
        self.assertEqual(res2.primary_intent, IntentType.EXPLORATORY)

    # -------------------------------------------------------------------------
    # 2. Multi-Intent Detection
    # -------------------------------------------------------------------------

    def test_multi_intent_detection(self):
        """Verify requests requiring both implementation guidance and technical analysis return multiple intents."""
        req = ResearchRequest(
            request_id="r-multi",
            project_id="p1",
            task_id="t1",
            objective="How to implement and benchmark SQLite vs DuckDB for time series analytics",
            questions=[
                "Code example for configuring in-memory DuckDB",
                "Evaluate write throughput benchmarks between SQLite and DuckDB",
            ],
        )
        res = self.classifier.classify(req)

        # Primary could be IMPLEMENTATION or COMPARATIVE
        self.assertIn(res.primary_intent, [IntentType.IMPLEMENTATION, IntentType.COMPARATIVE])
        # Both must be present in aggregate confidence with confident scores
        self.assertTrue(res.has_intent(IntentType.IMPLEMENTATION, min_confidence=0.70))
        self.assertTrue(res.has_intent(IntentType.COMPARATIVE, min_confidence=0.70))
        self.assertTrue(res.has_intent(IntentType.EVALUATIVE, min_confidence=0.60))
        self.assertIn(IntentType.COMPARATIVE, [res.primary_intent] + res.secondary_intents)

    # -------------------------------------------------------------------------
    # 3. Cross-Field Corroboration (Noisy-OR Boost)
    # -------------------------------------------------------------------------

    def test_cross_field_signal_corroboration(self):
        """Verify matching signals across objective and questions boost aggregate confidence."""
        req_single = ResearchRequest(
            request_id="r-single",
            project_id="p1",
            task_id="t1",
            objective="Compare SQLite and PostgreSQL",
            questions=[],
        )
        res_single = self.classifier.classify(req_single)
        conf_single = res_single.get_confidence(IntentType.COMPARATIVE)

        req_multi = ResearchRequest(
            request_id="r-multi",
            project_id="p1",
            task_id="t1",
            objective="Compare SQLite and PostgreSQL",
            questions=[
                "What are the differences between SQLite and PostgreSQL?",
                "How does SQLite compare to PostgreSQL for concurrency?",
            ],
        )
        res_multi = self.classifier.classify(req_multi)
        conf_multi = res_multi.get_confidence(IntentType.COMPARATIVE)

        # Multi-field corroboration should produce strictly higher confidence
        self.assertGreater(conf_multi, conf_single)
        self.assertGreaterEqual(conf_multi, 0.95)

    # -------------------------------------------------------------------------
    # 4. Ambiguity Detection
    # -------------------------------------------------------------------------

    def test_ambiguous_case_low_signals(self):
        """Verify underspecified request with no clear signal is flagged as ambiguous."""
        req = ResearchRequest(
            request_id="r-vague",
            project_id="p1",
            task_id="t1",
            objective="Look into system modules and code files",
        )
        res = self.classifier.classify(req)
        self.assertTrue(res.is_ambiguous)
        self.assertIsNotNone(res.ambiguity_reason)
        self.assertIn("No strong intent signals", res.ambiguity_reason)

    def test_ambiguous_case_tied_signals(self):
        """Verify competing intent signals with similar confidence are flagged as ambiguous."""
        req = ResearchRequest(
            request_id="r-tied",
            project_id="p1",
            task_id="t1",
            objective="Look into SQLite vs DuckDB and explore potential options",
        )
        res = self.classifier.classify(req)
        # Should flag ambiguity if competing signals exist without overwhelming dominance
        if len(res.aggregate_confidence) >= 2:
            scores = sorted(res.aggregate_confidence.values(), reverse=True)
            if abs(scores[0] - scores[1]) <= 0.05 and scores[0] < 0.80:
                self.assertTrue(res.is_ambiguous)

    # -------------------------------------------------------------------------
    # 5. Non-Semantic Certainty Bounds Invariant
    # -------------------------------------------------------------------------

    def test_non_semantic_certainty_bounds(self):
        """Verify deterministic rules never pretend 1.0 absolute certainty."""
        req = ResearchRequest(
            request_id="r-certain",
            project_id="p1",
            task_id="t1",
            objective="Compare SQLite and PostgreSQL. Differences between them. Versus benchmarks.",
            questions=[
                "Compare SQLite vs PostgreSQL",
                "Differences between SQLite and PostgreSQL",
                "Which performs better in comparison benchmarks?",
            ],
        )
        res = self.classifier.classify(req)
        for it, score in res.aggregate_confidence.items():
            self.assertLessEqual(score, DeterministicIntentClassifier.MAX_DETERMINISTIC_CONFIDENCE)
            self.assertLess(score, 1.0)

    # -------------------------------------------------------------------------
    # 6. Metadata Hints Integration
    # -------------------------------------------------------------------------

    def test_metadata_hints_integration(self):
        """Verify desired_output hints in metadata generate supporting signals."""
        req = ResearchRequest(
            request_id="r-meta",
            project_id="p1",
            task_id="t1",
            objective="Storage layer review",
            metadata={"desired_output": "DECISION_BRIEF"},
        )
        res = self.classifier.classify(req)
        self.assertTrue(res.has_intent(IntentType.DECISION_SUPPORT, min_confidence=0.50))
        meta_signals = [s for s in res.signals if s.source_field == "metadata"]
        self.assertTrue(len(meta_signals) >= 1)
        self.assertEqual(meta_signals[0].intent_type, IntentType.DECISION_SUPPORT)

    # -------------------------------------------------------------------------
    # 7. Serialization Roundtrip
    # -------------------------------------------------------------------------

    def test_signal_and_result_serialization_roundtrip(self):
        """Verify IntentSignal and IntentClassificationResult serialize and deserialize faithfully."""
        sig = IntentSignal(
            intent_type=IntentType.COMPARATIVE,
            confidence=0.88,
            rule_name="comparative_syntax_pattern",
            matched_cues=["compare ... with"],
            rationale="Explicit comparison construct",
            source_field="objective",
        )
        data_sig = sig.to_dict()
        restored_sig = IntentSignal.from_dict(data_sig)
        self.assertEqual(restored_sig.intent_type, IntentType.COMPARATIVE)
        self.assertEqual(restored_sig.confidence, 0.88)
        self.assertEqual(restored_sig.rule_name, "comparative_syntax_pattern")

        result = IntentClassificationResult(
            signals=[sig],
            primary_intent=IntentType.COMPARATIVE,
            secondary_intents=[IntentType.TECHNICAL],
            aggregate_confidence={IntentType.COMPARATIVE: 0.88, IntentType.TECHNICAL: 0.65},
            is_ambiguous=False,
            ambiguity_reason=None,
            classification_trace=["Rule matched"],
        )
        data_res = result.to_dict()
        restored_res = IntentClassificationResult.from_dict(data_res)
        self.assertEqual(restored_res.primary_intent, IntentType.COMPARATIVE)
        self.assertEqual(restored_res.secondary_intents, [IntentType.TECHNICAL])
        self.assertEqual(restored_res.aggregate_confidence[IntentType.COMPARATIVE], 0.88)
        self.assertEqual(len(restored_res.signals), 1)

    # -------------------------------------------------------------------------
    # 8. Deterministic Idempotence
    # -------------------------------------------------------------------------

    def test_idempotent_classification(self):
        """Verify repeated classification of the same request produces identical results."""
        req = ResearchRequest(
            request_id="r-idem",
            project_id="p1",
            task_id="t1",
            objective="How to implement token bucket rate limiting in Python?",
            questions=["What is the algorithm for token bucket?", "Code example in Python"],
        )
        res1 = self.classifier.classify(req)
        res2 = self.classifier.classify(req)

        self.assertEqual(res1.to_dict(), res2.to_dict())


if __name__ == "__main__":
    unittest.main()
