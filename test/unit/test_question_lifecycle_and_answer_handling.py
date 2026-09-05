from __future__ import annotations

import json
import unittest

from core.inference.provider import MockProvider
from core.research.contracts.intent import Ambiguity, ClarificationQuestion, ResearchIntent, VersionScope
from core.research.contracts.request import ResearchRequest
from core.research.question.generator import (
    QuestionGenerationResult,
    QuestionGenerationStatus,
    QuestionGenerator,
)
from core.research.question.lifecycle import (
    AnswerIncorporationResult,
    DuplicateAnswerError,
    ExecutionPausedError,
    QuestionLifecycleCoordinator,
    StaleQuestionError,
)
from core.research.question.model import (
    QuestionOption,
    QuestionProvenance,
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
    AnswerSource,
    AnswerType,
    DecisionType,
    QuestionImportance,
    QuestionLifecycleState,
    QuestionState,
)
from core.research.question.ui_contract import UIDecisionPrompt, UIOptionView


class TestQuestionLifecycleAndAnswerHandling(unittest.TestCase):
    """
    Comprehensive test suite for Phase 2 Step 2.2.3: Research Question Lifecycle & Answer Handling.
    Validates:
    1. Paused execution invariant: while question pending, execution is paused, crawlers cannot spawn.
    2. Resumed execution invariant: upon answer/skip/expiration/cancellation, execution resumes.
    3. One-at-a-time presentation invariant: parallel questions strictly rejected.
    4. Custom answers as first-class input: never overwritten by recommendations.
    5. Hard limit bound: at most 5 questions per research task.
    6. Authoritative intent incorporation: constraints, dimensions, and ambiguity resolution.
    7. Timeout and default recommendation fallback.
    8. Robust error handling (StaleQuestionError, DuplicateAnswerError, ExecutionPausedError).
    9. UI contract fidelity and serialization.
    """

    def setUp(self):
        self.request_id = "req-lifecycle-100"
        self.sample_request = ResearchRequest(
            request_id=self.request_id,
            project_id="proj-test-1",
            task_id="task-test-1",
            objective="Evaluate relational vs document store for time-series analytics",
            questions=["What is the write throughput for time-series data?"],
            constraints=["Deployable on self-hosted Kubernetes"],
        )
        self.sample_intent = ResearchIntent(
            intent_id="intent-lifecycle-100",
            objective="Evaluate relational vs document store for time-series analytics",
            subjects=["databases"],
            entities=["PostgreSQL", "MongoDB"],
            comparison_targets=["PostgreSQL", "MongoDB"],
            research_dimensions=["write throughput", "storage efficiency"],
            explicit_constraints=["Deployable on self-hosted Kubernetes"],
            ambiguities=[
                Ambiguity(
                    ambiguity_id="amb-storage-scale",
                    description="Expected data volume per day is unspecified",
                    impact="Affects database partitioning strategy",
                    blocking=True,
                )
            ],
            clarification_questions=[
                ClarificationQuestion(
                    question_id="cq-vol",
                    question_text="What is your estimated daily ingest volume?",
                    target_ambiguity_id="amb-storage-scale",
                )
            ],
            clarification_required=True,
        )

    def _create_generator(self, custom_response: str) -> QuestionGenerator:
        provider = MockProvider(
            provider_id="mock-lifecycle-provider",
            custom_response=custom_response,
        )
        return QuestionGenerator(provider=provider, model_id="mock-lifecycle-model")

    def _build_question_json(
        self,
        decision_type: str = "ARCHITECTURAL_CONSTRAINT",
        importance: str = "HIGH",
        question: str = "What is your target daily data ingest volume?",
        decision_id: str = "amb-storage-scale",
    ) -> str:
        return json.dumps({
            "has_unresolved_decision": True,
            "unresolved_decision_id": decision_id,
            "decision_type": decision_type,
            "importance": importance,
            "question": question,
            "context": "Ingest scale dramatically impacts database selection.",
            "impact": "Sharding vs single-node architecture.",
            "rationale": "High throughput requires clustered architectures.",
            "options": [
                {
                    "option_id": "opt-low",
                    "label": "Under 10 GB / day",
                    "description": "Modest ingestion volume.",
                    "rationale": "Single node PostgreSQL is fully sufficient.",
                    "recommended": False,
                },
                {
                    "option_id": "opt-high",
                    "label": "Over 500 GB / day",
                    "description": "High ingestion volume requiring clustering.",
                    "rationale": "Requires TimescaleDB hypertable or MongoDB sharding.",
                    "recommended": True,
                    "recommendation_reason": "Default for modern enterprise analytics.",
                },
            ],
            "recommended_option_id": "opt-high",
            "recommendation_reason": "Default for modern enterprise analytics.",
        })

    # -------------------------------------------------------------------------
    # 1. Initial State & Execution Permissions
    # -------------------------------------------------------------------------
    def test_initial_coordinator_state(self):
        """Verify coordinator initializes with NO_QUESTION and unpaused execution."""
        coordinator = QuestionLifecycleCoordinator(research_request_id=self.request_id)

        self.assertEqual(coordinator.current_state, QuestionLifecycleState.NO_QUESTION)
        self.assertFalse(coordinator.is_execution_paused())
        self.assertTrue(coordinator.can_spawn_crawlers())
        self.assertIsNone(coordinator.get_active_prompt())
        # Should not raise
        coordinator.assert_can_execute()

    # -------------------------------------------------------------------------
    # 2. Question Presentation Pauses Execution & Blocks Crawlers
    # -------------------------------------------------------------------------
    def test_question_presentation_pauses_execution_and_blocks_crawlers(self):
        """Verify requesting a decision transitions to QUESTION_PENDING, pauses execution, and blocks crawlers."""
        generator = self._create_generator(self._build_question_json())
        coordinator = QuestionLifecycleCoordinator(
            research_request_id=self.request_id,
            generator=generator,
        )

        prompt = coordinator.request_decision(request=self.sample_request, intent=self.sample_intent)

        self.assertIsNotNone(prompt)
        self.assertIsInstance(prompt, UIDecisionPrompt)
        self.assertEqual(coordinator.current_state, QuestionLifecycleState.QUESTION_PENDING)
        self.assertTrue(coordinator.is_execution_paused())
        self.assertFalse(coordinator.can_spawn_crawlers())
        self.assertEqual(prompt.sequence_number, 1)
        self.assertEqual(prompt.max_questions, 5)
        self.assertEqual(prompt.progress_label, "Question 1 of 5")
        self.assertEqual(prompt.recommended_option_id, "opt-high")

        # Invariant check: assert_can_execute MUST raise ExecutionPausedError
        with self.assertRaises(ExecutionPausedError) as ctx:
            coordinator.assert_can_execute()
        self.assertIn("Research execution is paused", str(ctx.exception))

        # Check get_active_prompt
        active_prompt = coordinator.get_active_prompt()
        self.assertIsNotNone(active_prompt)
        self.assertEqual(active_prompt.question_id, prompt.question_id)

    # -------------------------------------------------------------------------
    # 3. One-At-A-Time Presentation Invariant
    # -------------------------------------------------------------------------
    def test_one_at_a_time_presentation_invariant(self):
        """Verify cannot present multiple questions simultaneously."""
        generator = self._create_generator(self._build_question_json())
        coordinator = QuestionLifecycleCoordinator(
            research_request_id=self.request_id,
            generator=generator,
        )

        coordinator.request_decision(request=self.sample_request, intent=self.sample_intent)
        self.assertTrue(coordinator.is_execution_paused())

        # Attempting second question while active question is pending must raise ActiveQuestionExistsError
        with self.assertRaises(ActiveQuestionExistsError):
            coordinator.request_decision(request=self.sample_request, intent=self.sample_intent)

    # -------------------------------------------------------------------------
    # 4. User Selects Option -> Unpauses Execution & Updates Intent
    # -------------------------------------------------------------------------
    def test_submit_answer_option_selected(self):
        """Verify user selecting an option resolves question, unpauses execution, and updates intent."""
        generator = self._create_generator(self._build_question_json())
        coordinator = QuestionLifecycleCoordinator(
            research_request_id=self.request_id,
            generator=generator,
        )

        prompt = coordinator.request_decision(request=self.sample_request, intent=self.sample_intent)
        self.assertIsNotNone(prompt)

        answer = ResearchAnswer(
            question_id=prompt.question_id,
            research_request_id=self.request_id,
            selected_option_id="opt-high",
            answer_source=AnswerSource.USER,
        )

        resolved_q, inc_result = coordinator.submit_answer(answer, intent=self.sample_intent)

        # Invariant checks
        self.assertEqual(coordinator.current_state, QuestionLifecycleState.NO_QUESTION)
        self.assertFalse(coordinator.is_execution_paused())
        self.assertTrue(coordinator.can_spawn_crawlers())
        coordinator.assert_can_execute()
        self.assertIsNone(coordinator.get_active_prompt())

        # Question state checks
        self.assertEqual(resolved_q.state, QuestionState.RESOLVED)
        self.assertTrue(resolved_q.is_answered())
        self.assertFalse(resolved_q.is_skipped())
        self.assertEqual(resolved_q.answer.selected_option_id, "opt-high")
        self.assertEqual(resolved_q.answer.answer_type, AnswerType.OPTION_SELECTED)
        self.assertEqual(resolved_q.answer.answer_source, AnswerSource.USER)

        # Intent incorporation checks (Invariant 10)
        self.assertTrue(any("Over 500 GB / day" in c for c in self.sample_intent.explicit_constraints))
        self.assertIn("amb-storage-scale", inc_result.resolved_ambiguities)

        # Ambiguity is now resolved (not blocking)
        amb = next(a for a in self.sample_intent.ambiguities if a.ambiguity_id == "amb-storage-scale")
        self.assertFalse(amb.blocking)
        self.assertIn("Resolved by user decision", amb.suggested_interpretation)
        self.assertFalse(self.sample_intent.clarification_required)
        self.assertEqual(len(self.sample_intent.clarification_questions), 0)

    # -------------------------------------------------------------------------
    # 5. Custom Answer as First-Class Input
    # -------------------------------------------------------------------------
    def test_submit_custom_answer(self):
        """Verify custom answer is treated as first-class input and not overwritten."""
        generator = self._create_generator(self._build_question_json())
        coordinator = QuestionLifecycleCoordinator(
            research_request_id=self.request_id,
            generator=generator,
        )

        prompt = coordinator.request_decision(request=self.sample_request, intent=self.sample_intent)

        custom_text = "Exactly 250 GB/day with 90-day cold retention in Parquet format"
        answer = ResearchAnswer(
            question_id=prompt.question_id,
            research_request_id=self.request_id,
            custom_answer=custom_text,
            answer_source=AnswerSource.USER,
        )

        resolved_q, inc_result = coordinator.submit_answer(answer, intent=self.sample_intent)

        self.assertFalse(coordinator.is_execution_paused())
        self.assertTrue(coordinator.can_spawn_crawlers())
        self.assertTrue(resolved_q.answer.is_custom())
        self.assertFalse(resolved_q.answer.is_option())
        self.assertEqual(resolved_q.answer.get_effective_answer_text(), custom_text)

        # Intent constraint matches custom answer verbatim
        self.assertTrue(any(custom_text in c for c in self.sample_intent.explicit_constraints))

    # -------------------------------------------------------------------------
    # 6. Skip Active Question
    # -------------------------------------------------------------------------
    def test_skip_active_question(self):
        """Verify skipping an active question unpauses execution without adding constraints."""
        generator = self._create_generator(self._build_question_json())
        coordinator = QuestionLifecycleCoordinator(
            research_request_id=self.request_id,
            generator=generator,
        )

        prompt = coordinator.request_decision(request=self.sample_request, intent=self.sample_intent)
        constraints_before = list(self.sample_intent.explicit_constraints)

        resolved_q, inc_result = coordinator.skip_active_question(
            skip_reason="User lacks information on volume at this stage",
            intent=self.sample_intent,
        )

        self.assertFalse(coordinator.is_execution_paused())
        self.assertTrue(coordinator.can_spawn_crawlers())
        self.assertTrue(resolved_q.is_skipped())
        self.assertEqual(resolved_q.answer.answer_type, AnswerType.SKIPPED)

        # No new constraints added to intent
        self.assertEqual(self.sample_intent.explicit_constraints, constraints_before)
        # Trace should reflect user skip
        self.assertTrue(any("explicitly skipped" in t for t in self.sample_intent.understanding_trace))

    # -------------------------------------------------------------------------
    # 7. Sequential Two-Question Flow
    # -------------------------------------------------------------------------
    def test_sequential_question_progression(self):
        """Verify sequential Q1 -> Answer -> Q2 -> Answer flow."""
        # 1. Setup generator for Q1
        gen1 = self._create_generator(self._build_question_json(
            decision_type="ARCHITECTURAL_CONSTRAINT",
            question="What is your target daily data ingest volume?",
            decision_id="amb-storage-scale",
        ))
        coordinator = QuestionLifecycleCoordinator(
            research_request_id=self.request_id,
            generator=gen1,
        )

        prompt1 = coordinator.request_decision(request=self.sample_request, intent=self.sample_intent)
        self.assertEqual(prompt1.sequence_number, 1)
        self.assertTrue(coordinator.is_execution_paused())

        # Answer Q1
        coordinator.submit_answer(ResearchAnswer(
            question_id=prompt1.question_id,
            research_request_id=self.request_id,
            selected_option_id="opt-high",
        ), intent=self.sample_intent)

        self.assertFalse(coordinator.is_execution_paused())
        self.assertEqual(coordinator.tracker.total_questions_count, 1)

        # 2. Setup generator for Q2 (e.g. Scope / Criteria)
        coordinator.generator = self._create_generator(self._build_question_json(
            decision_type="COMPARISON_CRITERIA",
            question="Which metric should be prioritized in the benchmark?",
            decision_id="dec-crit-bench",
        ))

        prompt2 = coordinator.request_decision(request=self.sample_request, intent=self.sample_intent)
        self.assertIsNotNone(prompt2)
        self.assertEqual(prompt2.sequence_number, 2)
        self.assertEqual(prompt2.progress_label, "Question 2 of 5")
        self.assertTrue(coordinator.is_execution_paused())

        # Answer Q2
        coordinator.submit_answer(ResearchAnswer(
            question_id=prompt2.question_id,
            research_request_id=self.request_id,
            custom_answer="Write latency under p99 SLA of 10ms",
        ), intent=self.sample_intent)

        self.assertFalse(coordinator.is_execution_paused())
        self.assertEqual(coordinator.tracker.total_questions_count, 2)
        self.assertEqual(len(coordinator.tracker.history), 2)
        # Added to research_dimensions
        self.assertTrue(any("p99 SLA of 10ms" in d for d in self.sample_intent.research_dimensions))

    # -------------------------------------------------------------------------
    # 8. Hard Bound of 5 Questions
    # -------------------------------------------------------------------------
    def test_hard_bound_of_five_questions(self):
        """Verify hard limit of 5 questions is strictly enforced and 6th attempt returns None."""
        coordinator = QuestionLifecycleCoordinator(
            research_request_id=self.request_id,
        )

        distinct_decisions = [
            ("SCOPE", "What is the primary scope of the research?", "dec-scope-1"),
            ("TECHNICAL_DIRECTION", "What is the desired technical direction?", "dec-tech-2"),
            ("ARCHITECTURAL_CONSTRAINT", "What is the target daily data ingest volume?", "dec-arch-3"),
            ("COMPARISON_CRITERIA", "Which metric should be prioritized in the benchmark?", "dec-crit-4"),
            ("SECURITY_PRIORITY", "What level of encryption is required?", "dec-sec-5"),
        ]

        for i, (dtype, qtext, dec_id) in enumerate(distinct_decisions, start=1):
            coordinator.generator = self._create_generator(self._build_question_json(
                decision_type=dtype,
                question=qtext,
                decision_id=dec_id,
            ))
            prompt = coordinator.request_decision(request=self.sample_request, intent=self.sample_intent)
            self.assertIsNotNone(prompt, f"Question {i} should be generated")
            self.assertEqual(prompt.sequence_number, i)
            coordinator.submit_answer(ResearchAnswer(
                question_id=prompt.question_id,
                research_request_id=self.request_id,
                selected_option_id="opt-high",
            ))

        self.assertEqual(coordinator.tracker.total_questions_count, 5)
        self.assertTrue(coordinator.tracker.is_limit_reached())

        # 6th attempt must return None without raising
        coordinator.generator = self._create_generator(self._build_question_json(
            decision_type="COST_PRIORITY",
            question="What is your budget limit?",
            decision_id="dec-cost-6",
        ))
        prompt6 = coordinator.request_decision(request=self.sample_request, intent=self.sample_intent)
        self.assertIsNone(prompt6)
        self.assertFalse(coordinator.is_execution_paused())

    # -------------------------------------------------------------------------
    # 9. Stale Question Error Handling
    # -------------------------------------------------------------------------
    def test_stale_question_error_handling(self):
        """Verify StaleQuestionError when answering with no active question or mismatched ID."""
        coordinator = QuestionLifecycleCoordinator(research_request_id=self.request_id)

        # No question active
        with self.assertRaises(StaleQuestionError):
            coordinator.submit_answer(ResearchAnswer(
                question_id="rq-dec-unknown",
                research_request_id=self.request_id,
                selected_option_id="opt-1",
            ))

        with self.assertRaises(StaleQuestionError):
            coordinator.skip_active_question()

        with self.assertRaises(StaleQuestionError):
            coordinator.expire_active_question()

        with self.assertRaises(StaleQuestionError):
            coordinator.cancel_active_question()

        # Mismatched ID while a question is active
        generator = self._create_generator(self._build_question_json())
        coordinator.generator = generator
        prompt = coordinator.request_decision(request=self.sample_request)

        with self.assertRaises(StaleQuestionError):
            coordinator.submit_answer(ResearchAnswer(
                question_id="rq-dec-different-id",
                research_request_id=self.request_id,
                selected_option_id="opt-1",
            ))

    # -------------------------------------------------------------------------
    # 10. Duplicate Answer Error Handling
    # -------------------------------------------------------------------------
    def test_duplicate_answer_error_handling(self):
        """Verify DuplicateAnswerError when attempting to answer a previously resolved question."""
        generator = self._create_generator(self._build_question_json())
        coordinator = QuestionLifecycleCoordinator(
            research_request_id=self.request_id,
            generator=generator,
        )

        prompt = coordinator.request_decision(request=self.sample_request)
        answer = ResearchAnswer(
            question_id=prompt.question_id,
            research_request_id=self.request_id,
            selected_option_id="opt-high",
        )
        coordinator.submit_answer(answer)

        # Submitting again to the same question_id must raise DuplicateAnswerError
        with self.assertRaises(DuplicateAnswerError):
            coordinator.submit_answer(answer)

    # -------------------------------------------------------------------------
    # 11. Expire Question Without Recommendation
    # -------------------------------------------------------------------------
    def test_expire_active_question_without_recommendation(self):
        """Verify expiring a question marks it EXPIRED and unpauses execution."""
        generator = self._create_generator(self._build_question_json())
        coordinator = QuestionLifecycleCoordinator(
            research_request_id=self.request_id,
            generator=generator,
        )

        prompt = coordinator.request_decision(request=self.sample_request)
        self.assertTrue(coordinator.is_execution_paused())

        expired_q, inc_res = coordinator.expire_active_question(
            expire_reason="User prompt timed out after 300s",
            use_default_recommendation=False,
        )

        self.assertFalse(coordinator.is_execution_paused())
        self.assertTrue(coordinator.can_spawn_crawlers())
        self.assertIsNone(inc_res)
        self.assertEqual(expired_q.state, QuestionState.EXPIRED)
        self.assertEqual(len(coordinator.tracker.history), 1)

    # -------------------------------------------------------------------------
    # 12. Expire Question With Default Recommendation Fallback
    # -------------------------------------------------------------------------
    def test_expire_active_question_with_default_recommendation(self):
        """Verify expiring with recommendation fallback applies recommended option with AnswerSource.DEFAULT_RECOMMENDATION."""
        generator = self._create_generator(self._build_question_json())
        coordinator = QuestionLifecycleCoordinator(
            research_request_id=self.request_id,
            generator=generator,
        )

        prompt = coordinator.request_decision(request=self.sample_request, intent=self.sample_intent)

        resolved_q, inc_res = coordinator.expire_active_question(
            expire_reason="User timed out, adopting system recommendation",
            use_default_recommendation=True,
            intent=self.sample_intent,
        )

        self.assertFalse(coordinator.is_execution_paused())
        self.assertTrue(coordinator.can_spawn_crawlers())
        self.assertIsNotNone(inc_res)
        self.assertEqual(resolved_q.state, QuestionState.RESOLVED)
        self.assertEqual(resolved_q.answer.answer_source, AnswerSource.DEFAULT_RECOMMENDATION)
        self.assertEqual(resolved_q.answer.selected_option_id, "opt-high")
        self.assertTrue(any("Over 500 GB / day" in c for c in self.sample_intent.explicit_constraints))

    # -------------------------------------------------------------------------
    # 13. Cancel Active Question
    # -------------------------------------------------------------------------
    def test_cancel_active_question(self):
        """Verify cancelling active question sets CANCELLED and unpauses execution."""
        generator = self._create_generator(self._build_question_json())
        coordinator = QuestionLifecycleCoordinator(
            research_request_id=self.request_id,
            generator=generator,
        )

        prompt = coordinator.request_decision(request=self.sample_request)
        self.assertTrue(coordinator.is_execution_paused())

        cancelled_q = coordinator.cancel_active_question("User stopped research")

        self.assertFalse(coordinator.is_execution_paused())
        self.assertTrue(coordinator.can_spawn_crawlers())
        self.assertEqual(cancelled_q.state, QuestionState.CANCELLED)
        self.assertEqual(len(coordinator.tracker.history), 1)

    # -------------------------------------------------------------------------
    # 14. Intent Incorporation for Version Scope
    # -------------------------------------------------------------------------
    def test_intent_incorporation_version_scope(self):
        """Verify version decision updates intent.version_scope and constraints."""
        generator = self._create_generator(self._build_question_json(
            decision_type="VERSION",
            question="Which version of PostgreSQL are you evaluating?",
            decision_id="dec-pg-ver",
        ))
        coordinator = QuestionLifecycleCoordinator(
            research_request_id=self.request_id,
            generator=generator,
        )

        prompt = coordinator.request_decision(request=self.sample_request, intent=self.sample_intent)

        answer = ResearchAnswer(
            question_id=prompt.question_id,
            research_request_id=self.request_id,
            custom_answer="PostgreSQL 16.2 on Alpine Linux",
        )

        resolved_q, inc_res = coordinator.submit_answer(answer, intent=self.sample_intent)

        self.assertIsNotNone(self.sample_intent.version_scope)
        self.assertIn("PostgreSQL 16.2", self.sample_intent.version_scope.description)
        self.assertTrue(any("PostgreSQL 16.2" in c for c in self.sample_intent.explicit_constraints))

    # -------------------------------------------------------------------------
    # 15. UI Contract Serialization Round-Trip
    # -------------------------------------------------------------------------
    def test_ui_decision_prompt_serialization(self):
        """Verify UIDecisionPrompt to_dict and from_dict fidelity."""
        generator = self._create_generator(self._build_question_json())
        coordinator = QuestionLifecycleCoordinator(
            research_request_id=self.request_id,
            generator=generator,
        )

        prompt = coordinator.request_decision(request=self.sample_request)
        data = prompt.to_dict()
        restored = UIDecisionPrompt.from_dict(data)

        self.assertEqual(prompt.prompt_id, restored.prompt_id)
        self.assertEqual(prompt.question_id, restored.question_id)
        self.assertEqual(prompt.decision_type, restored.decision_type)
        self.assertEqual(prompt.recommended_option_id, restored.recommended_option_id)
        self.assertEqual(len(prompt.options), len(restored.options))
        self.assertEqual(prompt.options[0].label, restored.options[0].label)

    # -------------------------------------------------------------------------
    # 16. Coordinator State Serialization Round-Trip
    # -------------------------------------------------------------------------
    def test_coordinator_serialization(self):
        """Verify QuestionLifecycleCoordinator can be serialized and deserialized preserving history."""
        generator = self._create_generator(self._build_question_json())
        coordinator = QuestionLifecycleCoordinator(
            research_request_id=self.request_id,
            generator=generator,
        )

        # Present and answer one question
        prompt = coordinator.request_decision(request=self.sample_request)
        coordinator.submit_answer(ResearchAnswer(
            question_id=prompt.question_id,
            research_request_id=self.request_id,
            selected_option_id="opt-high",
        ))

        # Present a second question with distinct decision (now pending)
        coordinator.generator = self._create_generator(self._build_question_json(
            decision_type="COMPARISON_CRITERIA",
            question="Which metric should be prioritized?",
            decision_id="dec-crit-2",
        ))
        prompt2 = coordinator.request_decision(request=self.sample_request)
        self.assertIsNotNone(prompt2)

        # Serialize
        serialized = coordinator.to_dict()

        # Restore
        restored = QuestionLifecycleCoordinator.from_dict(serialized, generator=generator)

        self.assertEqual(restored.research_request_id, self.request_id)
        self.assertEqual(restored.current_state, QuestionLifecycleState.QUESTION_PENDING)
        self.assertTrue(restored.is_execution_paused())
        self.assertEqual(restored.tracker.total_questions_count, 2)
        self.assertEqual(len(restored.tracker.history), 1)
        self.assertIsNotNone(restored.tracker.active_question)
        self.assertEqual(restored.tracker.active_question.question_id, prompt2.question_id)


if __name__ == "__main__":
    unittest.main()
