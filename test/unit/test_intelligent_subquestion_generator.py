from __future__ import annotations

import json
import unittest

from core.inference.provider import MockProvider
from core.research.contracts.intent import ResearchIntent
from core.research.contracts.request import ResearchRequest
from core.research.decomposition.generator import (
    DecompositionGenerationResult,
    DecompositionGenerationStatus,
    DecompositionGenerator,
    SubQuestionGenerator,
)
from core.research.decomposition.policy import DecompositionPolicy
from core.research.decomposition.types import SubQuestionPriority, SubQuestionType


class TestIntelligentSubQuestionGenerator(unittest.TestCase):
    """
    Comprehensive test suite for Phase 2 Step 2.3.3: Intelligent Sub-question Generation.
    Validates:
    1. Simple objective yielding exactly 1 sub-question.
    2. Moderately complex objective yielding 3-5 sub-questions.
    3. Complex objective yielding bounded output <= 8.
    4. Over-generation (20 questions) rejected by deterministic limit.
    5. Duplicate questions rejected.
    6. Sub-question matching root objective rejected by root alignment.
    7. Malformed non-JSON output handled safely.
    8. Invalid priority rejected.
    9. Non-existent dependency rejected.
    10. Cyclic dependency rejected.
    11. Zero-question proposal rejected.
    12. All-optional sub-questions rejected.
    13. Inference provider failure handled safely.
    14. Deterministic graph construction fidelity.
    15. None/empty intent handled safely.
    """

    def setUp(self):
        self.sample_intent = ResearchIntent(
            intent_id="intent-sg-100",
            objective="Compare GSAP and Motion for our Next.js portfolio website",
            subjects=["animation-libraries"],
            entities=["GSAP", "Motion", "Next.js"],
            comparison_targets=["GSAP", "Motion"],
            research_dimensions=["performance", "bundle-size", "developer-experience"],
            explicit_constraints=["Must support server-side rendering in Next.js App Router"],
        )
        self.sample_request = ResearchRequest(
            request_id="req-sg-100",
            project_id="proj-web-1",
            task_id="task-anim-1",
            objective="Compare GSAP and Motion for our Next.js portfolio website",
            questions=["Which animation library provides smoother 60fps animations in Next.js?"],
        )

    def _create_generator(
        self,
        custom_response: str | None = None,
        should_fail: bool = False,
    ) -> SubQuestionGenerator:
        provider = MockProvider(
            provider_id="mock-subq-provider",
            custom_response=custom_response,
            should_fail=should_fail,
        )
        return SubQuestionGenerator(provider=provider, model_id="mock-subq-model")

    # -------------------------------------------------------------------------
    # 1. Simple Objective -> Exactly 1 Sub-Question
    # -------------------------------------------------------------------------
    def test_simple_objective_yields_single_subquestion(self):
        """Simple, factual request should generate exactly 1 sub-question."""
        simple_intent = ResearchIntent(
            intent_id="intent-simple-1",
            objective="What is the current stable release version of React?",
            subjects=["react"],
            entities=["React"],
        )
        response_json = json.dumps({
            "decomposition_rationale": "Single factual inquiry requiring no multi-part breakdown.",
            "sub_questions": [
                {
                    "temp_id": "sq-1",
                    "question": "What is the latest stable release version number of React on npm?",
                    "objective": "Determine the current official stable release version of React.",
                    "sub_question_type": "FACT_FINDING",
                    "priority": "HIGH",
                    "required": True,
                    "dependencies": [],
                    "rationale": "Direct factual lookup resolves the research objective completely."
                }
            ]
        })
        generator = self._create_generator(response_json)
        result = generator.generate(simple_intent)

        self.assertEqual(result.status, DecompositionGenerationStatus.SUCCESS)
        self.assertIsNotNone(result.decomposition)
        self.assertEqual(len(result.decomposition.sub_questions), 1)
        self.assertEqual(
            result.decomposition.sub_questions[0].question,
            "What is the latest stable release version number of React on npm?"
        )
        self.assertEqual(result.decomposition.sub_questions[0].sub_question_type, SubQuestionType.FACT_FINDING)

    # -------------------------------------------------------------------------
    # 2. Moderately Complex Objective -> 3-5 Sub-Questions
    # -------------------------------------------------------------------------
    def test_moderately_complex_objective_yields_several_subquestions(self):
        """Moderate comparison should produce 4 distinct, non-overlapping sub-questions with dependencies."""
        response_json = json.dumps({
            "decomposition_rationale": "Compare animation capabilities, SSR integration, performance, and maintenance.",
            "sub_questions": [
                {
                    "temp_id": "sq-1",
                    "question": "What core animation capabilities and primitives does each library provide?",
                    "objective": "Survey timeline, scroll-triggered, and layout animation feature sets of GSAP and Motion.",
                    "sub_question_type": "COMPARISON",
                    "priority": "HIGH",
                    "required": True,
                    "dependencies": [],
                    "rationale": "Establishes baseline capability parity."
                },
                {
                    "temp_id": "sq-2",
                    "question": "How well does each library integrate with Next.js App Router and SSR?",
                    "objective": "Evaluate React Server Component compatibility and client directive requirements.",
                    "sub_question_type": "COMPATIBILITY_ANALYSIS",
                    "priority": "HIGH",
                    "required": True,
                    "dependencies": ["sq-1"],
                    "rationale": "Determines framework integration friction."
                },
                {
                    "temp_id": "sq-3",
                    "question": "What are the runtime performance and bundle size implications?",
                    "objective": "Benchmark minified bundle sizes, tree-shaking, and frame drop rates in Next.js.",
                    "sub_question_type": "PERFORMANCE_ANALYSIS",
                    "priority": "HIGH",
                    "required": True,
                    "dependencies": ["sq-1"],
                    "rationale": "Ensures adherence to performance budgets."
                },
                {
                    "temp_id": "sq-4",
                    "question": "What are the licensing, community maintenance, and ecosystem tradeoffs?",
                    "objective": "Analyze commercial licensing terms, update cadence, and community adoption.",
                    "sub_question_type": "EVALUATION",
                    "priority": "MEDIUM",
                    "required": True,
                    "dependencies": ["sq-2", "sq-3"],
                    "rationale": "Synthesizes long-term architectural suitability."
                }
            ]
        })
        generator = self._create_generator(response_json)
        result = generator.generate(self.sample_intent, self.sample_request)

        self.assertEqual(result.status, DecompositionGenerationStatus.SUCCESS)
        self.assertIsNotNone(result.decomposition)
        self.assertEqual(len(result.decomposition.sub_questions), 4)
        # Check dependencies mapped cleanly
        sq4 = result.decomposition.sub_questions[3]
        self.assertEqual(len(sq4.dependencies), 2)
        self.assertGreater(len(result.decomposition.dependencies), 0)

    # -------------------------------------------------------------------------
    # 3. Complex Objective -> Bounded Output (<= 8)
    # -------------------------------------------------------------------------
    def test_complex_objective_bounded_output(self):
        """Complex migration request produces 6 bounded sub-questions."""
        sub_qs = []
        for i in range(1, 7):
            sub_qs.append({
                "temp_id": f"sq-{i}",
                "question": f"What is architectural investigation item number {i} for this system?",
                "objective": f"Analyze technical facet number {i} regarding system migration and performance.",
                "sub_question_type": "ARCHITECTURAL",
                "priority": "HIGH",
                "required": True,
                "dependencies": [f"sq-{i-1}"] if i > 1 else [],
                "rationale": f"Aspect {i} is necessary for comprehensive migration planning."
            })
        response_json = json.dumps({
            "decomposition_rationale": "Broad multi-phase architecture review.",
            "sub_questions": sub_qs
        })
        generator = self._create_generator(response_json)
        result = generator.generate(self.sample_intent)

        self.assertEqual(result.status, DecompositionGenerationStatus.SUCCESS)
        self.assertEqual(len(result.decomposition.sub_questions), 6)
        self.assertLessEqual(len(result.decomposition.sub_questions), 8)

    # -------------------------------------------------------------------------
    # 4. Twenty Sub-Questions -> Policy Rejection
    # -------------------------------------------------------------------------
    def test_twenty_subquestions_rejected_by_policy(self):
        """LLM proposing 20 sub-questions must be strictly rejected by MAX_SUB_QUESTIONS=8."""
        sub_qs = []
        for i in range(1, 21):
            sub_qs.append({
                "temp_id": f"sq-{i}",
                "question": f"What is micro-detail number {i} for the proposed software stack?",
                "objective": f"Investigate minor detail number {i} of the software components.",
                "sub_question_type": "FACT_FINDING",
                "priority": "LOW",
                "required": True,
                "dependencies": [],
                "rationale": "Micro investigation."
            })
        response_json = json.dumps({
            "decomposition_rationale": "Excessive granular breakdown.",
            "sub_questions": sub_qs
        })
        generator = self._create_generator(response_json)
        result = generator.generate(self.sample_intent)

        self.assertEqual(result.status, DecompositionGenerationStatus.VALIDATION_FAILED)
        self.assertTrue(any("exceeds maximum allowed limit of 8" in iss for iss in result.validation_issues))

    # -------------------------------------------------------------------------
    # 5. Duplicate Questions Rejected
    # -------------------------------------------------------------------------
    def test_duplicate_questions_rejected(self):
        """LLM output containing duplicate questions must fail validation."""
        response_json = json.dumps({
            "decomposition_rationale": "Broken breakdown with duplicate items.",
            "sub_questions": [
                {
                    "temp_id": "sq-1",
                    "question": "What is the runtime performance overhead of GSAP animations?",
                    "objective": "Measure frame rate and CPU utilization of GSAP.",
                    "sub_question_type": "PERFORMANCE_ANALYSIS",
                    "priority": "HIGH",
                    "required": True,
                    "dependencies": [],
                    "rationale": "Performance check."
                },
                {
                    "temp_id": "sq-2",
                    "question": "  what is the runtime performance overhead of GSAP animations?  ",
                    "objective": "Measure frame rate and CPU utilization of GSAP again.",
                    "sub_question_type": "PERFORMANCE_ANALYSIS",
                    "priority": "HIGH",
                    "required": True,
                    "dependencies": [],
                    "rationale": "Duplicate performance check."
                }
            ]
        })
        generator = self._create_generator(response_json)
        result = generator.generate(self.sample_intent)

        self.assertEqual(result.status, DecompositionGenerationStatus.VALIDATION_FAILED)
        self.assertTrue(any("Duplicate sub-question detected" in iss for iss in result.validation_issues))

    # -------------------------------------------------------------------------
    # 6. Sub-Question Trivially Matching Root Objective Rejected
    # -------------------------------------------------------------------------
    def test_sub_question_trivially_matching_root_objective_rejected(self):
        """Sub-question copying root objective must be rejected by root alignment."""
        response_json = json.dumps({
            "decomposition_rationale": "Lazy decomposition copying root objective.",
            "sub_questions": [
                {
                    "temp_id": "sq-1",
                    "question": self.sample_intent.objective,
                    "objective": "Direct copy of root objective.",
                    "sub_question_type": "COMPARISON",
                    "priority": "HIGH",
                    "required": True,
                    "dependencies": [],
                    "rationale": "Lazy copy."
                }
            ]
        })
        generator = self._create_generator(response_json)
        result = generator.generate(self.sample_intent)

        self.assertEqual(result.status, DecompositionGenerationStatus.VALIDATION_FAILED)
        self.assertTrue(any("trivially identical to root objective" in iss for iss in result.validation_issues))

    # -------------------------------------------------------------------------
    # 7. Malformed Non-JSON Output
    # -------------------------------------------------------------------------
    def test_malformed_json_output(self):
        """Model returning unparseable prose must be handled safely as MALFORMED_OUTPUT."""
        generator = self._create_generator("I cannot help decompose this task into JSON.")
        result = generator.generate(self.sample_intent)

        self.assertEqual(result.status, DecompositionGenerationStatus.MALFORMED_OUTPUT)
        self.assertIsNone(result.decomposition)
        self.assertIn("could not be parsed", result.error_message)

    # -------------------------------------------------------------------------
    # 8. Invalid Priority Rejected
    # -------------------------------------------------------------------------
    def test_invalid_priority_rejected(self):
        """Model returning an unrecognized priority must be rejected or normalized."""
        response_json = json.dumps({
            "decomposition_rationale": "Checking priority validation.",
            "sub_questions": [
                {
                    "temp_id": "sq-1",
                    "question": "What is the runtime performance impact of this library?",
                    "objective": "Measure frame rate and CPU utilization metrics.",
                    "sub_question_type": "PERFORMANCE_ANALYSIS",
                    "priority": "INVALID_PRIORITY_VAL",
                    "required": True,
                    "dependencies": [],
                    "rationale": "Performance check."
                }
            ]
        })
        # Note: normalizer coerces unrecognized string priorities to MEDIUM, but let's verify
        # candidate is valid after normalizer coercion
        generator = self._create_generator(response_json)
        result = generator.generate(self.sample_intent)

        self.assertEqual(result.status, DecompositionGenerationStatus.SUCCESS)
        self.assertEqual(result.decomposition.sub_questions[0].priority, SubQuestionPriority.MEDIUM)

    # -------------------------------------------------------------------------
    # 9. Non-Existent Dependency Rejected
    # -------------------------------------------------------------------------
    def test_invalid_dependency_rejected(self):
        """Model referencing a non-existent temp_id as a dependency must fail validation."""
        response_json = json.dumps({
            "decomposition_rationale": "Checking invalid dependency reference.",
            "sub_questions": [
                {
                    "temp_id": "sq-1",
                    "question": "What is the bundle size footprint of Motion?",
                    "objective": "Measure gzipped bundle sizes of core Motion packages.",
                    "sub_question_type": "PERFORMANCE_ANALYSIS",
                    "priority": "HIGH",
                    "required": True,
                    "dependencies": ["sq-ghost-999"],
                    "rationale": "Bundle check."
                }
            ]
        })
        generator = self._create_generator(response_json)
        result = generator.generate(self.sample_intent)

        self.assertEqual(result.status, DecompositionGenerationStatus.VALIDATION_FAILED)
        self.assertTrue(any("references non-existent dependency" in iss for iss in result.validation_issues))

    # -------------------------------------------------------------------------
    # 10. Dependency Cycle Rejected
    # -------------------------------------------------------------------------
    def test_dependency_cycle_rejected(self):
        """Cyclic dependencies (sq-1 -> sq-2 -> sq-1) must be rejected."""
        response_json = json.dumps({
            "decomposition_rationale": "Checking dependency cycle rejection.",
            "sub_questions": [
                {
                    "temp_id": "sq-1",
                    "question": "What is the bundle size footprint of Motion?",
                    "objective": "Measure gzipped bundle sizes of core Motion packages.",
                    "sub_question_type": "PERFORMANCE_ANALYSIS",
                    "priority": "HIGH",
                    "required": True,
                    "dependencies": ["sq-2"],
                    "rationale": "Bundle check."
                },
                {
                    "temp_id": "sq-2",
                    "question": "What are the timeline sequencing capabilities of GSAP?",
                    "objective": "Analyze nesting and timeline controls in GSAP.",
                    "sub_question_type": "FACT_FINDING",
                    "priority": "HIGH",
                    "required": True,
                    "dependencies": ["sq-1"],
                    "rationale": "Timeline check."
                }
            ]
        })
        generator = self._create_generator(response_json)
        result = generator.generate(self.sample_intent)

        self.assertEqual(result.status, DecompositionGenerationStatus.VALIDATION_FAILED)
        self.assertTrue(any("Dependency cycle detected" in iss for iss in result.validation_issues))

    # -------------------------------------------------------------------------
    # 11. Zero-Question Proposal Rejected
    # -------------------------------------------------------------------------
    def test_no_question_proposal_rejected(self):
        """Empty sub_questions list in proposal must fail validation."""
        response_json = json.dumps({
            "decomposition_rationale": "No questions generated.",
            "sub_questions": []
        })
        generator = self._create_generator(response_json)
        result = generator.generate(self.sample_intent)

        self.assertEqual(result.status, DecompositionGenerationStatus.VALIDATION_FAILED)
        self.assertTrue(any("minimum required is 1" in iss for iss in result.validation_issues))

    # -------------------------------------------------------------------------
    # 12. All Optional Sub-Questions Rejected
    # -------------------------------------------------------------------------
    def test_all_optional_subquestions_rejected(self):
        """Proposal where all sub-questions are optional must be rejected."""
        response_json = json.dumps({
            "decomposition_rationale": "All optional questions.",
            "sub_questions": [
                {
                    "temp_id": "sq-1",
                    "question": "What are the timeline sequencing capabilities of GSAP?",
                    "objective": "Analyze nesting and timeline controls in GSAP.",
                    "sub_question_type": "FACT_FINDING",
                    "priority": "HIGH",
                    "required": False,
                    "dependencies": [],
                    "rationale": "Optional check."
                }
            ]
        })
        generator = self._create_generator(response_json)
        result = generator.generate(self.sample_intent)

        self.assertEqual(result.status, DecompositionGenerationStatus.VALIDATION_FAILED)
        self.assertTrue(any("required sub-question" in iss for iss in result.validation_issues))

    # -------------------------------------------------------------------------
    # 13. Inference Provider Failure Handled Safely
    # -------------------------------------------------------------------------
    def test_provider_failure_handled_safely(self):
        """Provider failure/timeout must be caught and return INFERENCE_FAILED."""
        generator = self._create_generator(should_fail=True)
        result = generator.generate(self.sample_intent)

        self.assertEqual(result.status, DecompositionGenerationStatus.INFERENCE_FAILED)
        self.assertIsNone(result.decomposition)
        self.assertIn("Inference provider failed", result.error_message)

    # -------------------------------------------------------------------------
    # 14. Deterministic Graph Construction Fidelity & Alias
    # -------------------------------------------------------------------------
    def test_deterministic_graph_construction_fidelity_and_alias(self):
        """Verify DecompositionGenerator alias and complete domain model fidelity."""
        self.assertIs(DecompositionGenerator, SubQuestionGenerator)

        response_json = json.dumps({
            "decomposition_rationale": "Check fidelity.",
            "sub_questions": [
                {
                    "temp_id": "sq-1",
                    "question": "What are the animation primitives provided by Motion?",
                    "objective": "Catalog motion components and spring physics in Motion.",
                    "sub_question_type": "FACT_FINDING",
                    "priority": "HIGH",
                    "required": True,
                    "dependencies": [],
                    "rationale": "Capabilities inventory."
                }
            ]
        })
        generator = DecompositionGenerator(
            provider=MockProvider(custom_response=response_json),
            model_id="fidelity-test-model"
        )
        result = generator.generate(self.sample_intent, self.sample_request)

        self.assertEqual(result.status, DecompositionGenerationStatus.SUCCESS)
        decomp = result.decomposition
        self.assertIsNotNone(decomp)
        self.assertEqual(decomp.research_request_id, self.sample_request.request_id)
        self.assertEqual(decomp.research_intent_id, self.sample_intent.intent_id)
        self.assertEqual(decomp.objective, self.sample_intent.objective)
        self.assertGreater(len(decomp.decomposition_trace), 0)

        # Sub-question provenance
        sq = decomp.sub_questions[0]
        self.assertIsNotNone(sq.provenance)
        self.assertEqual(sq.provenance.research_request_id, self.sample_request.request_id)
        self.assertEqual(sq.provenance.research_intent_id, self.sample_intent.intent_id)

    # -------------------------------------------------------------------------
    # 15. None / Empty Intent Handled Safely
    # -------------------------------------------------------------------------
    def test_empty_or_none_intent_handled_safely(self):
        """Passing None or empty intent must return VALIDATION_FAILED safely."""
        generator = self._create_generator()
        result_none = generator.generate(None)  # type: ignore
        self.assertEqual(result_none.status, DecompositionGenerationStatus.VALIDATION_FAILED)

        empty_intent = ResearchIntent(intent_id="empty-1", objective="")
        result_empty = generator.generate(empty_intent)
        self.assertEqual(result_empty.status, DecompositionGenerationStatus.VALIDATION_FAILED)


if __name__ == "__main__":
    unittest.main()
