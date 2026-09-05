from __future__ import annotations

import json
import unittest

from core.inference.provider import MockProvider
from core.research.contracts.intent import Ambiguity, ResearchIntent
from core.research.contracts.request import ResearchRequest
from core.research.question.generator import (
    QuestionGenerationResult,
    QuestionGenerationStatus,
    QuestionGenerator,
    QuestionProposal,
)
from core.research.question.model import (
    QuestionOption,
    ResearchAnswer,
    ResearchDecisionQuestion,
    ResearchQuestion,
)
from core.research.question.state import (
    ActiveQuestionExistsError,
    QuestionLimitExceededError,
    QuestionTracker,
)
from core.research.question.types import (
    DecisionType,
    QuestionImportance,
    QuestionState,
)


class TestIntelligentQuestionGenerator(unittest.TestCase):
    """
    Comprehensive test suite for Phase 2 Step 2.2.2: Intelligent Research Question Generation.
    Validates bounded intelligence, deterministic policy gating, one-at-a-time invariant,
    duplicate prevention, hard budget bounds, and fail-safe error handling.
    """

    def setUp(self):
        self.sample_request = ResearchRequest(
            request_id="req-gen-100",
            project_id="proj-test-1",
            task_id="task-test-1",
            objective="Compare PostgreSQL and MongoDB for our analytics application",
            questions=["Which database has better analytical query latency?"],
            constraints=["Must be deployable on Kubernetes"],
        )
        self.sample_intent = ResearchIntent(
            intent_id="intent-gen-100",
            objective="Compare PostgreSQL and MongoDB for our analytics application",
            subjects=["databases"],
            entities=["PostgreSQL", "MongoDB"],
            comparison_targets=["PostgreSQL", "MongoDB"],
            research_dimensions=["performance", "scalability"],
            explicit_constraints=["Must be deployable on Kubernetes"],
        )

    def _create_generator(self, custom_response: str) -> QuestionGenerator:
        provider = MockProvider(
            provider_id="mock-qgen-provider",
            custom_response=custom_response,
        )
        return QuestionGenerator(provider=provider, model_id="mock-qgen-model")

    # -------------------------------------------------------------------------
    # 1. No important ambiguity -> no question required
    # -------------------------------------------------------------------------
    def test_no_important_ambiguity_yields_no_question(self):
        """Verify that when no important ambiguity exists, generator returns NO_QUESTION_REQUIRED."""
        response_json = json.dumps({
            "has_unresolved_decision": False,
            "rationale": "The request is straightforward and explicit; no material decisions required."
        })
        generator = self._create_generator(response_json)
        result = generator.generate_question(request=self.sample_request, intent=self.sample_intent)

        self.assertEqual(result.status, QuestionGenerationStatus.NO_QUESTION_REQUIRED)
        self.assertIsNone(result.question)
        self.assertIn("straightforward and explicit", result.reason)

    # -------------------------------------------------------------------------
    # 2. Important architectural ambiguity -> one question
    # -------------------------------------------------------------------------
    def test_important_architectural_ambiguity_yields_one_question(self):
        """Verify that an unresolved architectural ambiguity yields exactly one ResearchQuestion."""
        response_json = json.dumps({
            "has_unresolved_decision": True,
            "unresolved_decision_id": "dec-db-workload",
            "decision_type": "ARCHITECTURAL_CONSTRAINT",
            "importance": "HIGH",
            "question": "What is the primary workload profile for this evaluation?",
            "context": "PostgreSQL and MongoDB exhibit vastly different performance under write-heavy vs read-heavy loads.",
            "impact": "Dictates benchmark configuration and index design evaluated in research.",
            "rationale": "Without workload profile, evaluation cannot produce actionable recommendation.",
            "options": [
                {
                    "option_id": "opt-1",
                    "label": "Read-Heavy Analytics",
                    "description": "90% analytical read queries with complex joins",
                    "rationale": "Favors relational indexing and columnar extensions"
                },
                {
                    "option_id": "opt-2",
                    "label": "Write-Heavy Ingestion",
                    "description": "High volume timeseries and event ingestion",
                    "rationale": "Favors document buffering and horizontal sharding"
                },
                {
                    "option_id": "opt-3",
                    "label": "Balanced OLTP + Reporting",
                    "description": "Hybrid operational and analytical reporting",
                    "rationale": "Evaluates HTAP capabilities"
                }
            ],
            "recommended_option_id": "opt-1",
            "recommendation_reason": "Analytics application was explicitly mentioned in objective"
        })
        generator = self._create_generator(response_json)
        result = generator.generate_question(request=self.sample_request, intent=self.sample_intent)

        self.assertEqual(result.status, QuestionGenerationStatus.QUESTION_GENERATED)
        self.assertIsNotNone(result.question)
        q = result.question
        self.assertEqual(q.decision_type, DecisionType.ARCHITECTURAL_CONSTRAINT)
        self.assertEqual(q.importance, QuestionImportance.HIGH)
        self.assertEqual(len(q.options), 3)
        self.assertEqual(q.sequence_number, 1)
        self.assertTrue(q.custom_answer_allowed)
        rec = q.get_recommended_option()
        self.assertIsNotNone(rec)
        self.assertEqual(rec.option_id, "opt-1")

    # -------------------------------------------------------------------------
    # 3. Trivial ambiguity -> no question
    # -------------------------------------------------------------------------
    def test_trivial_ambiguity_yields_no_question(self):
        """Verify that cosmetic/trivial ambiguities return NO_QUESTION_REQUIRED."""
        response_json = json.dumps({
            "has_unresolved_decision": False,
            "rationale": "Theme and report formatting preferences are trivial and do not alter research."
        })
        generator = self._create_generator(response_json)
        result = generator.generate_question(request=self.sample_request, intent=self.sample_intent)

        self.assertEqual(result.status, QuestionGenerationStatus.NO_QUESTION_REQUIRED)
        self.assertIsNone(result.question)

    # -------------------------------------------------------------------------
    # 4. HIGH importance -> question generated
    # -------------------------------------------------------------------------
    def test_high_importance_yields_question(self):
        """Verify that HIGH importance decision passes deterministic policy gating."""
        response_json = json.dumps({
            "has_unresolved_decision": True,
            "decision_type": "COMPARISON_CRITERIA",
            "importance": "HIGH",
            "question": "Which evaluation criterion takes precedence?",
            "context": "Tradeoff between raw query speed and operational maintenance costs.",
            "impact": "Determines primary ranking weight in comparison matrix.",
            "rationale": "Priorities significantly skew final recommendation.",
            "options": [
                {"option_id": "opt-perf", "label": "Performance", "description": "Prioritize latency", "rationale": "Fast responses"},
                {"option_id": "opt-cost", "label": "Cost", "description": "Prioritize low cloud spend", "rationale": "Budget efficiency"}
            ]
        })
        generator = self._create_generator(response_json)
        result = generator.generate_question(request=self.sample_request)

        self.assertEqual(result.status, QuestionGenerationStatus.QUESTION_GENERATED)
        self.assertEqual(result.question.importance, QuestionImportance.HIGH)

    # -------------------------------------------------------------------------
    # 5. LOW importance -> no question
    # -------------------------------------------------------------------------
    def test_low_importance_yields_no_question(self):
        """Verify that LOW importance proposal is intercepted by deterministic policy."""
        response_json = json.dumps({
            "has_unresolved_decision": True,
            "decision_type": "OUTPUT_EXPECTATION",
            "importance": "LOW",
            "question": "Do you prefer a table or bullet points for the summary?",
            "context": "Minor visual presentation choice.",
            "impact": "Formatting only.",
            "rationale": "User might prefer tables.",
            "options": [
                {"option_id": "opt-1", "label": "Table"},
                {"option_id": "opt-2", "label": "Bullets"}
            ]
        })
        generator = self._create_generator(response_json)
        result = generator.generate_question(request=self.sample_request)

        self.assertEqual(result.status, QuestionGenerationStatus.NO_QUESTION_REQUIRED)
        self.assertIsNone(result.question)
        self.assertIn("LOW importance", result.reason)

    # -------------------------------------------------------------------------
    # 6. Existing answer prevents duplicate question
    # -------------------------------------------------------------------------
    def test_existing_answer_prevents_duplicate_question(self):
        """Verify that a decision already answered by the user is not re-asked."""
        response_json = json.dumps({
            "has_unresolved_decision": True,
            "decision_type": "COMPARISON_CRITERIA",
            "importance": "HIGH",
            "question": "Which evaluation criterion takes precedence?",
            "context": "Comparing latency vs cost.",
            "impact": "Weighting.",
            "rationale": "Tradeoff choice.",
            "options": [
                {"option_id": "opt-perf", "label": "Performance"},
                {"option_id": "opt-cost", "label": "Cost"}
            ]
        })
        generator = self._create_generator(response_json)

        # Pass already answered decision
        result = generator.generate_question(
            request=self.sample_request,
            already_answered_decisions=["COMPARISON_CRITERIA:opt-perf"],
        )

        self.assertEqual(result.status, QuestionGenerationStatus.NO_QUESTION_REQUIRED)
        self.assertIsNone(result.question)
        self.assertIn("already been asked or answered", result.reason)

    # -------------------------------------------------------------------------
    # 7. Remaining budget = 0 -> no question
    # -------------------------------------------------------------------------
    def test_remaining_budget_zero_yields_no_question(self):
        """Verify that remaining budget of 0 immediately returns NO_QUESTION_REQUIRED without LLM call."""
        # Provider that would fail if called
        generator = QuestionGenerator(provider=None, gateway=None)
        result = generator.generate_question(request=self.sample_request, remaining_budget=0)

        self.assertEqual(result.status, QuestionGenerationStatus.NO_QUESTION_REQUIRED)
        self.assertIsNone(result.question)
        self.assertEqual(result.remaining_budget, 0)
        self.assertIn("Question budget exhausted", result.reason)

    # -------------------------------------------------------------------------
    # 8. Remaining budget = 1 -> at most one final question
    # -------------------------------------------------------------------------
    def test_remaining_budget_one_yields_at_most_one_final_question(self):
        """Verify that remaining budget of 1 allows exactly one question to be generated."""
        response_json = json.dumps({
            "has_unresolved_decision": True,
            "decision_type": "ENVIRONMENT",
            "importance": "CRITICAL",
            "question": "Which cloud provider environment should be benchmarked?",
            "context": "Pricing differs significantly across AWS, GCP, and Azure.",
            "impact": "Affects instance selection and cost calculations.",
            "rationale": "Cloud choice determines real deployment cost.",
            "options": [
                {"option_id": "aws", "label": "AWS"},
                {"option_id": "gcp", "label": "GCP"}
            ]
        })
        generator = self._create_generator(response_json)
        result = generator.generate_question(request=self.sample_request, remaining_budget=1)

        self.assertEqual(result.status, QuestionGenerationStatus.QUESTION_GENERATED)
        self.assertIsNotNone(result.question)
        self.assertEqual(result.remaining_budget, 1)

    # -------------------------------------------------------------------------
    # 9. Malformed LLM output handled safely
    # -------------------------------------------------------------------------
    def test_malformed_llm_output_handled_cleanly(self):
        """Verify malformed non-JSON output returns FAILED status without crashing."""
        malformed_text = "I think you should ask the user: What is your preferred database? ```"
        generator = self._create_generator(malformed_text)
        result = generator.generate_question(request=self.sample_request)

        self.assertEqual(result.status, QuestionGenerationStatus.FAILED)
        self.assertIsNone(result.question)
        self.assertIn("Malformed LLM output", result.reason)

    # -------------------------------------------------------------------------
    # 10. Too many options rejected
    # -------------------------------------------------------------------------
    def test_too_many_options_rejected(self):
        """Verify generator rejects proposals with more than 4 options (violating 2-4 option rule)."""
        response_json = json.dumps({
            "has_unresolved_decision": True,
            "decision_type": "SCOPE",
            "importance": "HIGH",
            "question": "Which specific database features should be prioritized?",
            "context": "Too many disparate options provided.",
            "impact": "Scope definition.",
            "rationale": "Feature selection.",
            "options": [
                {"option_id": "o1", "label": "Transactions"},
                {"option_id": "o2", "label": "Sharding"},
                {"option_id": "o3", "label": "Vector search"},
                {"option_id": "o4", "label": "Full text search"},
                {"option_id": "o5", "label": "Timeseries"}
            ]
        })
        generator = self._create_generator(response_json)
        result = generator.generate_question(request=self.sample_request)

        self.assertEqual(result.status, QuestionGenerationStatus.FAILED)
        self.assertIsNone(result.question)
        self.assertIn("exceeds maximum 4 options", result.reason)

    # -------------------------------------------------------------------------
    # 11. Duplicate options rejected
    # -------------------------------------------------------------------------
    def test_duplicate_options_rejected(self):
        """Verify proposals with duplicate option IDs or labels fail validation."""
        response_json = json.dumps({
            "has_unresolved_decision": True,
            "decision_type": "SCOPE",
            "importance": "HIGH",
            "question": "Which replication model is acceptable?",
            "context": "Replication model choices.",
            "impact": "Data loss tolerance.",
            "rationale": "HA configuration.",
            "options": [
                {"option_id": "opt-1", "label": "Synchronous Replication"},
                {"option_id": "opt-1", "label": "Synchronous Replication"}
            ]
        })
        generator = self._create_generator(response_json)
        result = generator.generate_question(request=self.sample_request)

        self.assertEqual(result.status, QuestionGenerationStatus.FAILED)
        self.assertIsNone(result.question)
        self.assertTrue(any("duplicate" in issue for issue in result.validation_issues))

    # -------------------------------------------------------------------------
    # 12. Invalid importance rejected
    # -------------------------------------------------------------------------
    def test_invalid_importance_rejected(self):
        """Verify invalid importance enum value fails cleanly."""
        response_json = json.dumps({
            "has_unresolved_decision": True,
            "decision_type": "SCOPE",
            "importance": "SUPER_MEGA_CRITICAL",
            "question": "Valid question text here?",
            "context": "Context text here.",
            "impact": "Impact text.",
            "rationale": "Rationale text.",
            "options": [
                {"option_id": "o1", "label": "Option 1"},
                {"option_id": "o2", "label": "Option 2"}
            ]
        })
        generator = self._create_generator(response_json)
        result = generator.generate_question(request=self.sample_request)

        self.assertEqual(result.status, QuestionGenerationStatus.FAILED)
        self.assertIsNone(result.question)
        self.assertIn("Invalid importance", result.reason)

    # -------------------------------------------------------------------------
    # 13. Recommendation handling
    # -------------------------------------------------------------------------
    def test_recommendation_handling_preserves_user_control(self):
        """Verify AI recommendation is structured advisory metadata and does not auto-answer."""
        response_json = json.dumps({
            "has_unresolved_decision": True,
            "decision_type": "TECHNICAL_DIRECTION",
            "importance": "HIGH",
            "question": "Which data modeling paradigm fits the system best?",
            "context": "Relational vs document data modeling.",
            "impact": "Schema design guidelines.",
            "rationale": "Foundational architectural choice.",
            "options": [
                {"option_id": "relational", "label": "Strict Relational"},
                {"option_id": "document", "label": "Document Hierarchy"}
            ],
            "recommended_option_id": "relational",
            "recommendation_reason": "Existing analytics pipelines use SQL"
        })
        generator = self._create_generator(response_json)
        result = generator.generate_question(request=self.sample_request)

        self.assertEqual(result.status, QuestionGenerationStatus.QUESTION_GENERATED)
        q = result.question
        self.assertIsNotNone(q)
        # Verify recommended option is tagged
        rec = q.get_recommended_option()
        self.assertIsNotNone(rec)
        self.assertEqual(rec.option_id, "relational")
        self.assertEqual(rec.recommendation_reason, "Existing analytics pipelines use SQL")
        # Verify question is NOT answered; user retains final control
        self.assertIsNone(q.answer)
        self.assertTrue(q.is_active())
        self.assertEqual(q.state, QuestionState.QUESTION_PENDING)

    # -------------------------------------------------------------------------
    # 14. Custom answer support
    # -------------------------------------------------------------------------
    def test_custom_answer_support(self):
        """Verify generated question allows custom answer input by default."""
        response_json = json.dumps({
            "has_unresolved_decision": True,
            "decision_type": "SECURITY_PRIORITY",
            "importance": "HIGH",
            "question": "What encryption compliance standard is mandatory?",
            "context": "Compliance standard affects key management.",
            "impact": "Architectural security boundaries.",
            "rationale": "Mandatory regulatory compliance.",
            "options": [
                {"option_id": "fips", "label": "FIPS 140-2"},
                {"option_id": "hipaa", "label": "HIPAA HITECH"}
            ]
        })
        generator = self._create_generator(response_json)
        result = generator.generate_question(request=self.sample_request)

        self.assertEqual(result.status, QuestionGenerationStatus.QUESTION_GENERATED)
        self.assertTrue(result.question.custom_answer_allowed)

    # -------------------------------------------------------------------------
    # 15. LLM provider failure handled cleanly
    # -------------------------------------------------------------------------
    def test_llm_provider_failure_handled_cleanly(self):
        """Verify that provider network/execution exception is caught and returned as FAILED."""
        class FailingProvider(MockProvider):
            def generate(self, request, model, secret=None):
                raise RuntimeError("503 Service Unavailable: Gateway Timeout")

        generator = QuestionGenerator(
            provider=FailingProvider(provider_id="failing-mock"),
            model_id="failing-model",
        )
        result = generator.generate_question(request=self.sample_request)

        self.assertEqual(result.status, QuestionGenerationStatus.FAILED)
        self.assertIsNone(result.question)
        self.assertIn("503 Service Unavailable", result.reason)

    # -------------------------------------------------------------------------
    # 16. Question generated only on demand (one at a time)
    # -------------------------------------------------------------------------
    def test_question_generated_only_on_demand(self):
        """Verify generator generates exactly ONE question, and enforces one-at-a-time invariant via tracker."""
        response_json = json.dumps({
            "has_unresolved_decision": True,
            "decision_type": "VERSION",
            "importance": "HIGH",
            "question": "Which version of PostgreSQL should be targeted?",
            "context": "PostgreSQL 16 has distinct optimization features over 14.",
            "impact": "Query planner capabilities evaluated.",
            "rationale": "Version features dictate performance.",
            "options": [
                {"option_id": "pg16", "label": "PostgreSQL 16"},
                {"option_id": "pg15", "label": "PostgreSQL 15"}
            ]
        })
        generator = self._create_generator(response_json)
        tracker = QuestionTracker(research_request_id=self.sample_request.request_id)

        # 1. First demand: generates exactly one question
        result = generator.generate_question(request=self.sample_request, tracker=tracker)
        self.assertEqual(result.status, QuestionGenerationStatus.QUESTION_GENERATED)
        self.assertIsNotNone(result.question)

        # Present the question to tracker
        tracker.present_question(result.question)
        self.assertTrue(tracker.has_active_question())

        # 2. Second demand while first is still pending: must return NO_QUESTION_REQUIRED
        second_result = generator.generate_question(request=self.sample_request, tracker=tracker)
        self.assertEqual(second_result.status, QuestionGenerationStatus.NO_QUESTION_REQUIRED)
        self.assertIsNone(second_result.question)
        self.assertIn("Active question is already pending", second_result.reason)

    # -------------------------------------------------------------------------
    # 17. Regression: A research task can NEVER receive more than 5 user questions
    # -------------------------------------------------------------------------
    def test_regression_never_more_than_five_questions(self):
        """
        Explicit regression test proving:
        A research task can NEVER receive more than 5 user-facing questions through this component.
        """
        tracker = QuestionTracker(research_request_id="req-five-cap", max_questions=5)

        for i in range(1, 6):
            response_json = json.dumps({
                "has_unresolved_decision": True,
                "unresolved_decision_id": f"dec-{i}",
                "decision_type": "SCOPE",
                "importance": "HIGH",
                "question": f"Important decision number {i} affecting research outcome?",
                "context": f"Context for decision {i}",
                "impact": f"Impact for decision {i}",
                "rationale": f"Rationale for decision {i}",
                "options": [
                    {"option_id": "opt-a", "label": "Option A"},
                    {"option_id": "opt-b", "label": "Option B"}
                ]
            })
            generator = self._create_generator(response_json)

            # Generate question
            result = generator.generate_question(request=self.sample_request, tracker=tracker)
            self.assertEqual(result.status, QuestionGenerationStatus.QUESTION_GENERATED)
            self.assertIsNotNone(result.question)
            self.assertEqual(result.question.sequence_number, i)

            # Present and answer question
            tracker.present_question(result.question)
            tracker.submit_answer(
                ResearchAnswer(
                    question_id=result.question.question_id,
                    research_request_id="req-five-cap",
                    selected_option_id="opt-a",
                )
            )

        # Tracker is now at 5 completed questions
        self.assertEqual(tracker.total_questions_count, 5)
        self.assertTrue(tracker.is_limit_reached())

        # Attempting a 6th question through generator
        response_6 = json.dumps({
            "has_unresolved_decision": True,
            "decision_type": "SCOPE",
            "importance": "CRITICAL",
            "question": "Sixth decision attempting to exceed the limit?",
            "context": "Context",
            "impact": "Impact",
            "rationale": "Rationale",
            "options": [
                {"option_id": "o1", "label": "O1"},
                {"option_id": "o2", "label": "O2"}
            ]
        })
        generator = self._create_generator(response_6)
        result_6 = generator.generate_question(request=self.sample_request, tracker=tracker)

        # MUST be blocked deterministically
        self.assertEqual(result_6.status, QuestionGenerationStatus.NO_QUESTION_REQUIRED)
        self.assertIsNone(result_6.question)
        self.assertEqual(result_6.remaining_budget, 0)
        self.assertIn("Question budget exhausted", result_6.reason)

        # Attempting direct tracker presentation of a 6th question also raises
        q_overflow = ResearchQuestion(
            question_id="rq-overflow",
            question="Overflow question exceeding max limit?",
            context="Context",
            decision_type=DecisionType.SCOPE,
            impact="Impact",
            rationale="Rationale",
            options=[
                QuestionOption(option_id="o1", label="O1"),
                QuestionOption(option_id="o2", label="O2"),
            ],
        )
        with self.assertRaises(QuestionLimitExceededError):
            tracker.present_question(q_overflow)


if __name__ == "__main__":
    unittest.main()
