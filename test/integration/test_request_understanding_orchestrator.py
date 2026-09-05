from __future__ import annotations

import json
import unittest

from core.inference.provider import MockProvider
from core.research.contracts.request import ResearchRequest, ResearchScope
from core.research.crawler.registry import CrawlerRegistry
from core.research.planning.extractor import DeterministicRequestExtractor
from core.research.planning.intent_classifier import DeterministicIntentClassifier
from core.research.planning.normalizer import ResearchRequestNormalizer
from core.research.planning.understanding_engine import (
    LLMUnderstandingEngine,
    ProposalValidator,
)
from core.research.planning.understanding_orchestrator import (
    RequestUnderstandingOrchestrator,
    RequestUnderstandingResult,
    UnderstandingProvenance,
)
from core.research.types import IntentType, UnderstandingStatus


class TestRequestUnderstandingOrchestrator(unittest.TestCase):
    """
    Integration test suite verifying the complete RequestUnderstandingOrchestrator:
    - Bounded orchestration across Normalization, Signals, Extraction, LLM Understanding, and Validation.
    - Status determination: RESOLVED, RESOLVABLE_WITH_INFERENCE, AMBIGUOUS, INSUFFICIENT, CONFLICTING, FAILED.
    - Safety: zero crawler execution, search queries, or tool invocations.
    - Complete provenance and trace preservation.
    """

    def setUp(self):
        self.normalizer = ResearchRequestNormalizer()
        self.classifier = DeterministicIntentClassifier()
        self.extractor = DeterministicRequestExtractor()
        self.validator = ProposalValidator()

    def _create_orchestrator(self, custom_llm_response: str) -> RequestUnderstandingOrchestrator:
        """Create an orchestrator wired to a deterministic MockProvider analyst."""
        provider = MockProvider(
            provider_id="mock-analyst-provider",
            custom_response=custom_llm_response,
        )
        llm_engine = LLMUnderstandingEngine(
            provider=provider,
            validator=self.validator,
            model_id="mock-analyst-model",
        )
        return RequestUnderstandingOrchestrator(
            normalizer=self.normalizer,
            classifier=self.classifier,
            extractor=self.extractor,
            llm_engine=llm_engine,
            validator=self.validator,
        )

    # -------------------------------------------------------------------------
    # Scenario 1: Straightforward request -> RESOLVED
    # -------------------------------------------------------------------------
    def test_straightforward_request_resolved(self):
        """Verify request with complete explicit information resolves cleanly without inference."""
        llm_proposal = {
            "objective": "Compare PostgreSQL and MySQL for high-throughput write workloads.",
            "intent_types": ["COMPARATIVE", "TECHNICAL"],
            "subjects": ["high-throughput write workloads"],
            "entities": ["PostgreSQL", "MySQL"],
            "comparison_targets": ["MySQL"],
            "research_dimensions": ["performance", "scalability"],
            "explicit_constraints": ["must use official documentation"],
            "inferred_constraints": [],
            "freshness_requirement": "CURRENT",
            "desired_output": "COMPARISON",
            "evidence_requirements": [],
            "assumptions": [],
            "ambiguities": [],
            "clarification_questions": [],
            "confidence": 0.95,
        }
        orchestrator = self._create_orchestrator(json.dumps(llm_proposal))

        req = ResearchRequest(
            request_id="req-clean-101",
            project_id="proj-alpha",
            task_id="task-01",
            objective="Compare PostgreSQL and MySQL for high-throughput write workloads.",
            constraints=["must use official documentation"],
        )

        result = orchestrator.understand(req)

        self.assertEqual(result.status, UnderstandingStatus.RESOLVED)
        self.assertFalse(result.clarification_required)
        self.assertEqual(len(result.clarification_questions), 0)
        self.assertIsNotNone(result.intent)
        self.assertEqual(result.intent.objective, req.objective)
        self.assertTrue(result.intent.has_intent_type(IntentType.COMPARATIVE))
        self.assertEqual(result.intent.inferred_constraints, [])
        self.assertEqual(result.intent.assumptions, [])
        self.assertIsNotNone(result.provenance)
        self.assertEqual(result.provenance.request_id, "req-clean-101")
        self.assertTrue(result.provenance.normalizer_applied)
        self.assertTrue(result.provenance.classifier_applied)
        self.assertTrue(result.provenance.extractor_applied)
        self.assertTrue(result.provenance.llm_applied)
        self.assertTrue(result.provenance.validator_applied)

    # -------------------------------------------------------------------------
    # Scenario 2: Request requiring reasonable inference -> RESOLVABLE_WITH_INFERENCE
    # -------------------------------------------------------------------------
    def test_request_requiring_reasonable_inference(self):
        """Verify request with explicit inferences or assumptions yields RESOLVABLE_WITH_INFERENCE."""
        llm_proposal = {
            "objective": "What are best practices for database connection pooling?",
            "intent_types": ["DESCRIPTIVE", "TECHNICAL"],
            "subjects": ["database connection pooling"],
            "entities": ["PgBouncer", "HikariCP"],
            "comparison_targets": [],
            "research_dimensions": ["performance", "architecture"],
            "explicit_constraints": [],
            "inferred_constraints": ["Assuming relational SQL databases rather than NoSQL"],
            "freshness_requirement": "RECENT",
            "desired_output": "IMPLEMENTATION_GUIDANCE",
            "evidence_requirements": [],
            "assumptions": ["Assuming standard Linux containerized application deployments"],
            "ambiguities": [
                {
                    "description": "Specific database technology was unstated; defaulting to common relational engines.",
                    "impact": "Recommendations might require slight tuning if NoSQL is used.",
                    "blocking": False,
                }
            ],
            "clarification_questions": [],
            "confidence": 0.85,
        }
        orchestrator = self._create_orchestrator(json.dumps(llm_proposal))

        req = ResearchRequest(
            request_id="req-inf-102",
            project_id="proj-alpha",
            task_id="task-02",
            objective="What are best practices for database connection pooling?",
        )

        result = orchestrator.understand(req)

        self.assertEqual(result.status, UnderstandingStatus.RESOLVABLE_WITH_INFERENCE)
        self.assertFalse(result.clarification_required)
        self.assertIsNotNone(result.intent)
        self.assertIn("Assuming relational SQL databases rather than NoSQL", result.intent.inferred_constraints)
        self.assertIn("Assuming standard Linux containerized application deployments", result.intent.assumptions)
        self.assertFalse(result.intent.has_blocking_ambiguity())

    # -------------------------------------------------------------------------
    # Scenario 3: Materially ambiguous request -> AMBIGUOUS
    # -------------------------------------------------------------------------
    def test_materially_ambiguous_request(self):
        """Verify request with blocking ambiguity and clarification required yields AMBIGUOUS."""
        llm_proposal = {
            "objective": "Migrate the legacy authentication service to the new modern standard.",
            "intent_types": ["IMPLEMENTATION"],
            "subjects": ["authentication service migration"],
            "entities": [],
            "comparison_targets": [],
            "research_dimensions": ["security"],
            "explicit_constraints": [],
            "inferred_constraints": [],
            "freshness_requirement": "CURRENT",
            "desired_output": "IMPLEMENTATION_GUIDANCE",
            "evidence_requirements": [],
            "assumptions": [],
            "ambiguities": [
                {
                    "description": "Neither the source authentication system nor the target standard (e.g., OAuth 2.1, OIDC, SAML) is specified.",
                    "impact": "Cannot plan migration research without knowing target protocol.",
                    "blocking": True,
                }
            ],
            "clarification_questions": [
                "Which target authentication standard should be adopted (e.g. OAuth 2.1, OIDC, or SAML 2.0)?"
            ],
            "confidence": 0.35,
        }
        orchestrator = self._create_orchestrator(json.dumps(llm_proposal))

        req = ResearchRequest(
            request_id="req-amb-103",
            project_id="proj-alpha",
            task_id="task-03",
            objective="Migrate the legacy authentication service to the new modern standard.",
        )

        result = orchestrator.understand(req)

        self.assertEqual(result.status, UnderstandingStatus.AMBIGUOUS)
        self.assertTrue(result.clarification_required)
        self.assertGreaterEqual(len(result.clarification_questions), 1)
        self.assertIn("Which target authentication standard", result.clarification_questions[0].question_text)
        self.assertIsNotNone(result.intent)
        self.assertTrue(result.intent.has_blocking_ambiguity())

    # -------------------------------------------------------------------------
    # Scenario 4: Insufficient request -> INSUFFICIENT
    # -------------------------------------------------------------------------
    def test_insufficient_request_empty_or_trivial_objective(self):
        """Verify empty or trivial non-specific request halts early as INSUFFICIENT."""
        orchestrator = self._create_orchestrator("{}")

        # 4a: Empty objective
        req_empty = ResearchRequest(
            request_id="req-insuf-empty",
            project_id="proj-alpha",
            task_id="task-04a",
            objective="   ",
        )
        res_empty = orchestrator.understand(req_empty)
        self.assertEqual(res_empty.status, UnderstandingStatus.INSUFFICIENT)
        self.assertTrue(res_empty.clarification_required)
        self.assertIsNone(res_empty.intent)
        self.assertIn("empty", res_empty.validation_issues[0])

        # 4b: Vague single-word objective without questions
        req_vague = ResearchRequest(
            request_id="req-insuf-vague",
            project_id="proj-alpha",
            task_id="task-04b",
            objective="research",
        )
        res_vague = orchestrator.understand(req_vague)
        self.assertEqual(res_vague.status, UnderstandingStatus.INSUFFICIENT)
        self.assertTrue(res_vague.clarification_required)
        self.assertIsNone(res_vague.intent)
        self.assertIn("too brief", res_vague.validation_issues[0])

    # -------------------------------------------------------------------------
    # Scenario 5: Conflicting requirements -> CONFLICTING
    # -------------------------------------------------------------------------
    def test_conflicting_requirements_detected(self):
        """Verify contradictory domain scopes, negation constraints, or objective conflicts yield CONFLICTING."""
        orchestrator = self._create_orchestrator("{}")

        # 5a: Contradictory domain scope (allowed and excluded overlap)
        req_domain_conflict = ResearchRequest(
            request_id="req-conf-domain",
            project_id="proj-alpha",
            task_id="task-05a",
            objective="Investigate FastAPI async performance.",
            scope=ResearchScope(
                allowed_domains=["fastapi.tiangolo.com"],
                excluded_domains=["fastapi.tiangolo.com"],
            ),
        )
        res_domain = orchestrator.understand(req_domain_conflict)
        self.assertEqual(res_domain.status, UnderstandingStatus.CONFLICTING)
        self.assertTrue(res_domain.clarification_required)
        self.assertIsNotNone(res_domain.intent)
        self.assertTrue(res_domain.intent.has_blocking_ambiguity())
        self.assertTrue(any("Domain" in issue for issue in res_domain.validation_issues))

        # 5b: Contradictory constraints
        req_constraint_conflict = ResearchRequest(
            request_id="req-conf-constraint",
            project_id="proj-alpha",
            task_id="task-05b",
            objective="Evaluate caching backends.",
            constraints=["must use Redis", "must not use Redis"],
        )
        res_constraint = orchestrator.understand(req_constraint_conflict)
        self.assertEqual(res_constraint.status, UnderstandingStatus.CONFLICTING)
        self.assertTrue(res_constraint.clarification_required)
        self.assertTrue(any("Contradictory constraints" in issue for issue in res_constraint.validation_issues))

        # 5c: Objective vs constraint conflict (objective targets MySQL vs constraint prohibits it)
        req_obj_conflict = ResearchRequest(
            request_id="req-conf-obj",
            project_id="proj-alpha",
            task_id="task-05c",
            objective="Compare PostgreSQL vs MySQL for relational storage.",
            constraints=["do not use MySQL"],
        )
        res_obj = orchestrator.understand(req_obj_conflict)
        self.assertEqual(res_obj.status, UnderstandingStatus.CONFLICTING)
        self.assertTrue(res_obj.clarification_required)

    # -------------------------------------------------------------------------
    # Scenario 6: LLM failure -> FAILED
    # -------------------------------------------------------------------------
    def test_llm_failure_yields_failed_status(self):
        """Verify inference provider exception gracefully marks pipeline as FAILED."""
        failing_provider = MockProvider(
            provider_id="failing-mock",
            should_fail=True,
        )
        failing_engine = LLMUnderstandingEngine(
            provider=failing_provider,
            validator=self.validator,
        )
        orchestrator = RequestUnderstandingOrchestrator(
            normalizer=self.normalizer,
            classifier=self.classifier,
            extractor=self.extractor,
            llm_engine=failing_engine,
            validator=self.validator,
        )

        req = ResearchRequest(
            request_id="req-fail-106",
            project_id="proj-alpha",
            task_id="task-06",
            objective="Research Kafka vs RabbitMQ message brokers.",
        )

        result = orchestrator.understand(req)

        self.assertEqual(result.status, UnderstandingStatus.FAILED)
        self.assertFalse(result.clarification_required)
        self.assertIsNone(result.intent)
        self.assertTrue(len(result.validation_issues) > 0)
        self.assertTrue(any("failed" in issue.lower() for issue in result.validation_issues))

    # -------------------------------------------------------------------------
    # Scenario 7: Malformed LLM output -> FAILED
    # -------------------------------------------------------------------------
    def test_malformed_llm_output_yields_failed_status(self):
        """Verify unparseable JSON from LLM is rejected and marks status as FAILED."""
        orchestrator = self._create_orchestrator("```json\n{ this is corrupted json! \n```")

        req = ResearchRequest(
            request_id="req-malformed-107",
            project_id="proj-alpha",
            task_id="task-07",
            objective="Research indexing best practices.",
        )

        result = orchestrator.understand(req)

        self.assertEqual(result.status, UnderstandingStatus.FAILED)
        self.assertFalse(result.clarification_required)
        self.assertIsNone(result.intent)
        self.assertTrue(len(result.validation_issues) > 0)

    # -------------------------------------------------------------------------
    # Scenario 8: Deterministic fake LLM producing valid understanding
    # -------------------------------------------------------------------------
    def test_deterministic_fake_llm_repeatability(self):
        """Verify deterministic MockProvider generates repeatable, identical understanding results."""
        valid_response = json.dumps({
            "objective": "Determine recommended TLS versions for internal services.",
            "intent_types": ["TECHNICAL", "EVALUATIVE"],
            "subjects": ["TLS configuration"],
            "entities": ["TLS 1.3", "TLS 1.2"],
            "comparison_targets": [],
            "research_dimensions": ["security", "compatibility"],
            "explicit_constraints": ["must follow NIST guidelines"],
            "inferred_constraints": [],
            "freshness_requirement": "CURRENT",
            "desired_output": "FACTUAL_ANSWER",
            "evidence_requirements": [],
            "assumptions": [],
            "ambiguities": [],
            "clarification_questions": [],
            "confidence": 0.96,
        })
        orchestrator = self._create_orchestrator(valid_response)

        req = ResearchRequest(
            request_id="req-det-108",
            project_id="proj-alpha",
            task_id="task-08",
            objective="Determine recommended TLS versions for internal services.",
            constraints=["must follow NIST guidelines"],
        )

        result1 = orchestrator.understand(req)
        result2 = orchestrator.understand(req)

        self.assertEqual(result1.status, UnderstandingStatus.RESOLVED)
        self.assertEqual(result2.status, UnderstandingStatus.RESOLVED)
        self.assertEqual(result1.intent.objective, result2.intent.objective)
        self.assertEqual(result1.intent.entities, result2.intent.entities)
        self.assertEqual(result1.intent.confidence.overall, result2.intent.confidence.overall)

    # -------------------------------------------------------------------------
    # Scenario 9: Provenance and trace preservation
    # -------------------------------------------------------------------------
    def test_provenance_and_trace_preservation_and_serialization(self):
        """Verify complete provenance linkage and bidirectional serialization."""
        llm_proposal = {
            "objective": "Survey distributed lock implementations using Redis and ZooKeeper.",
            "intent_types": ["COMPARATIVE", "TECHNICAL"],
            "subjects": ["distributed lock implementations"],
            "entities": ["Redis", "ZooKeeper"],
            "comparison_targets": ["ZooKeeper"],
            "research_dimensions": ["reliability", "scalability"],
            "explicit_constraints": [],
            "inferred_constraints": [],
            "freshness_requirement": "RECENT",
            "desired_output": "COMPARISON",
            "evidence_requirements": [],
            "assumptions": [],
            "ambiguities": [],
            "clarification_questions": [],
            "confidence": 0.90,
        }
        orchestrator = self._create_orchestrator(json.dumps(llm_proposal))

        req = ResearchRequest(
            request_id="req-prov-109",
            project_id="proj-beta",
            task_id="task-09",
            correlation_id="corr-trace-999",
            objective="Survey distributed lock implementations using Redis and ZooKeeper.",
        )

        result = orchestrator.understand(req)

        self.assertIsNotNone(result.provenance)
        self.assertEqual(result.provenance.request_id, "req-prov-109")
        self.assertEqual(result.provenance.project_id, "proj-beta")
        self.assertEqual(result.provenance.task_id, "task-09")
        self.assertEqual(result.provenance.correlation_id, "corr-trace-999")
        self.assertTrue(result.provenance.normalizer_applied)
        self.assertTrue(result.provenance.classifier_applied)
        self.assertTrue(result.provenance.extractor_applied)
        self.assertTrue(result.provenance.llm_applied)
        self.assertTrue(result.provenance.validator_applied)
        self.assertEqual(result.provenance.provider_used, "mock-analyst-provider")

        # Trace checks
        self.assertTrue(len(result.trace) >= 5)
        self.assertTrue(any("Started Request Understanding" in t for t in result.trace))
        self.assertTrue(any("normalization" in t.lower() for t in result.trace))
        self.assertTrue(any("classification" in t.lower() for t in result.trace))
        self.assertTrue(any("extraction" in t.lower() for t in result.trace))
        self.assertTrue(any("completed with status" in t.lower() for t in result.trace))

        # Serialization roundtrip
        res_dict = result.to_dict()
        self.assertIsInstance(res_dict, dict)
        restored_res = RequestUnderstandingResult.from_dict(res_dict)
        self.assertEqual(restored_res.result_id, result.result_id)
        self.assertEqual(restored_res.status, result.status)
        self.assertEqual(restored_res.provenance.request_id, result.provenance.request_id)
        self.assertEqual(restored_res.intent.objective, result.intent.objective)

    # -------------------------------------------------------------------------
    # Scenario 10: Safety - zero crawler or tool execution occurs
    # -------------------------------------------------------------------------
    def test_safety_zero_crawler_or_tool_execution(self):
        """Verify no crawler workers, search queries, or tool tasks run during understanding."""
        registry = CrawlerRegistry()
        initial_crawler_count = len(registry.list_active_crawlers())

        valid_response = json.dumps({
            "objective": "Extract OAuth2 specifications and token rotation rules.",
            "intent_types": ["TECHNICAL", "IMPLEMENTATION"],
            "subjects": ["OAuth2 token rotation"],
            "entities": ["OAuth 2.1", "RFC 6749"],
            "comparison_targets": [],
            "research_dimensions": ["security"],
            "explicit_constraints": [],
            "inferred_constraints": [],
            "freshness_requirement": "STATIC",
            "desired_output": "IMPLEMENTATION_GUIDANCE",
            "evidence_requirements": [],
            "assumptions": [],
            "ambiguities": [],
            "clarification_questions": [],
            "confidence": 0.92,
        })
        orchestrator = self._create_orchestrator(valid_response)

        req = ResearchRequest(
            request_id="req-safe-110",
            project_id="proj-safe",
            task_id="task-10",
            objective="Extract OAuth2 specifications and token rotation rules.",
        )

        result = orchestrator.understand(req)

        self.assertEqual(result.status, UnderstandingStatus.RESOLVED)

        # Confirm registry was untouched
        self.assertEqual(len(registry.list_active_crawlers()), initial_crawler_count)

        # Confirm intent does NOT contain crawler tasks, research plans, or evidence
        self.assertFalse(hasattr(result.intent, "crawlers"))
        self.assertFalse(hasattr(result.intent, "crawler_tasks"))
        self.assertFalse(hasattr(result.intent, "evidence"))
        self.assertFalse(hasattr(result.intent, "plan"))
        self.assertIsInstance(result.intent.evidence_requirements, list)
