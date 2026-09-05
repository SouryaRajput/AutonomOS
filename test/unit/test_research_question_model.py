from __future__ import annotations

import pytest

from core.research.question.model import (
    QuestionOption,
    QuestionProvenance,
    ResearchAnswer,
    ResearchDecisionQuestion,
    ResearchQuestion,
    new_id,
    utc_now,
)
from core.research.question.state import (
    ActiveQuestionExistsError,
    NoActiveQuestionError,
    QuestionLimitExceededError,
    QuestionTracker,
)
from core.research.question.types import (
    DecisionType,
    QuestionImportance,
    QuestionState,
)
from core.research.question.validator import (
    QuestionValidationError,
    QuestionValidator,
)


class TestResearchQuestionModelAndState:
    """Comprehensive test suite for Phase 2 Part 2 Step 2.2.1: Research Question Model & Decision Contract."""

    @pytest.fixture
    def sample_options(self) -> list[QuestionOption]:
        return [
            QuestionOption(
                option_id="opt-1",
                label="PostgreSQL with JSONB",
                description="Use relational schema with jsonb column for unstructured data",
                rationale="Best for strong transactional consistency with flexible query support",
                recommended=True,
                recommendation_reason="Balances ACID guarantees with JSON document flexibility",
            ),
            QuestionOption(
                option_id="opt-2",
                label="MongoDB Document Store",
                description="Use native document database",
                rationale="Best for rapid prototyping with nested document schemas",
                recommended=False,
            ),
            QuestionOption(
                option_id="opt-3",
                label="Hybrid PostgreSQL + Redis Cache",
                description="Primary SQL store with Redis caching layer",
                rationale="Best for high read-throughput low-latency requirements",
                recommended=False,
            ),
        ]

    @pytest.fixture
    def valid_question(self, sample_options: list[QuestionOption]) -> ResearchQuestion:
        return ResearchQuestion(
            question_id="rq-dec-001",
            research_request_id="req-test-100",
            sequence_number=1,
            question="Which database architecture should the research evaluate as primary?",
            context="The user request compares PostgreSQL and MongoDB but did not specify read/write patterns or ACID requirements.",
            decision_type=DecisionType.ARCHITECTURAL_CONSTRAINT,
            importance=QuestionImportance.HIGH,
            state=QuestionState.QUESTION_PENDING,
            options=sample_options,
            custom_answer_allowed=True,
            required=True,
            impact="Shapes the evaluation matrix and benchmarks investigated during research.",
            rationale="Database selection is an architectural constraint that dictates downstream recommendations.",
            provenance=QuestionProvenance(
                research_request_id="req-test-100",
                research_intent_id="intent-test-100",
                unresolved_decision_id="dec-db-choice",
                source_field="objective",
                rationale="Request mentions PostgreSQL and MongoDB without operational profile",
            ),
        )

    # --------------------------------------------------------------------------
    # 1. Valid Question Creation & Serialization
    # --------------------------------------------------------------------------

    def test_valid_question_creation_and_serialization(self, valid_question: ResearchQuestion) -> None:
        """Verify that a valid question passes validation and roundtrips cleanly to/from dict."""
        issues = QuestionValidator.validate_question(valid_question)
        assert issues == []

        data = valid_question.to_dict()
        assert data["question_id"] == "rq-dec-001"
        assert data["decision_type"] == "ARCHITECTURAL_CONSTRAINT"
        assert data["importance"] == "HIGH"
        assert data["state"] == "QUESTION_PENDING"
        assert len(data["options"]) == 3
        assert data["custom_answer_allowed"] is True
        assert data["provenance"]["research_intent_id"] == "intent-test-100"

        # Roundtrip reconstruction
        reconstructed = ResearchQuestion.from_dict(data)
        assert reconstructed.question_id == valid_question.question_id
        assert reconstructed.decision_type == DecisionType.ARCHITECTURAL_CONSTRAINT
        assert reconstructed.importance == QuestionImportance.HIGH
        assert reconstructed.state == QuestionState.QUESTION_PENDING
        assert len(reconstructed.options) == 3
        assert reconstructed.options[0].option_id == "opt-1"
        assert reconstructed.options[0].recommended is True
        assert reconstructed.provenance is not None
        assert reconstructed.provenance.unresolved_decision_id == "dec-db-choice"

        # Alias check
        assert ResearchDecisionQuestion is ResearchQuestion

    # --------------------------------------------------------------------------
    # 2. Options & Recommendations
    # --------------------------------------------------------------------------

    def test_options_and_recommended_option(self, valid_question: ResearchQuestion) -> None:
        """Verify get_recommended_option retrieves the single designated recommendation."""
        recommended = valid_question.get_recommended_option()
        assert recommended is not None
        assert recommended.option_id == "opt-1"
        assert recommended.label == "PostgreSQL with JSONB"
        assert "Balances ACID" in (recommended.recommendation_reason or "")

    def test_multiple_recommended_options_rejected(self, valid_question: ResearchQuestion) -> None:
        """Verify validator rejects a question with more than one recommended option."""
        valid_question.options[1].recommended = True
        valid_question.options[1].recommendation_reason = "Also great option"

        issues = QuestionValidator.validate_question(valid_question)
        assert any("at most 1 option may be recommended" in issue for issue in issues)

        with pytest.raises(QuestionValidationError, match="at most 1 option may be recommended"):
            QuestionValidator.validate_question(valid_question, raise_on_error=True)

    def test_recommended_option_missing_reason_rejected(self, valid_question: ResearchQuestion) -> None:
        """Verify recommended option must provide a non-empty recommendation_reason."""
        valid_question.options[0].recommendation_reason = ""
        issues = QuestionValidator.validate_question(valid_question)
        assert any("missing recommendation_reason" in issue for issue in issues)

    def test_duplicate_option_ids_rejected(self, valid_question: ResearchQuestion) -> None:
        """Verify validator rejects duplicate option identifiers."""
        valid_question.options[1].option_id = "opt-1"
        issues = QuestionValidator.validate_question(valid_question)
        assert any("duplicate option_id 'opt-1'" in issue for issue in issues)

    def test_duplicate_option_labels_rejected(self, valid_question: ResearchQuestion) -> None:
        """Verify validator rejects duplicate option labels case-insensitively."""
        valid_question.options[1].label = "postgresql with jsonb"
        issues = QuestionValidator.validate_question(valid_question)
        assert any("duplicate option label" in issue for issue in issues)

    def test_options_bounds_enforced(self, valid_question: ResearchQuestion) -> None:
        """Verify validator enforces bounded option count (2 to 6)."""
        # Less than 2 options
        valid_question.options = [valid_question.options[0]]
        issues = QuestionValidator.validate_question(valid_question)
        assert any("must be between 2 and 6" in issue for issue in issues)

        # More than 6 options
        valid_question.options = [
            QuestionOption(option_id=f"opt-{i}", label=f"Option {i}") for i in range(7)
        ]
        issues = QuestionValidator.validate_question(valid_question)
        assert any("must be between 2 and 6" in issue for issue in issues)

    # --------------------------------------------------------------------------
    # 3. Importance Levels & Policy Enforcement
    # --------------------------------------------------------------------------

    def test_importance_policy_high_and_critical(self, valid_question: ResearchQuestion) -> None:
        """Verify HIGH and CRITICAL questions pass user-facing policy."""
        valid_question.importance = QuestionImportance.HIGH
        assert QuestionValidator.validate_question(valid_question) == []

        valid_question.importance = QuestionImportance.CRITICAL
        assert QuestionValidator.validate_question(valid_question) == []

    def test_importance_policy_low_rejected(self, valid_question: ResearchQuestion) -> None:
        """Verify LOW importance questions are strictly rejected from user-facing presentation."""
        valid_question.importance = QuestionImportance.LOW
        issues = QuestionValidator.validate_question(valid_question)
        assert any("LOW importance questions must not become user-facing prompts" in issue for issue in issues)

    def test_importance_policy_medium_requires_flag(self, valid_question: ResearchQuestion) -> None:
        """Verify MEDIUM questions require explicit policy flag to become user-facing."""
        valid_question.importance = QuestionImportance.MEDIUM
        # Rejected by default
        issues_default = QuestionValidator.validate_question(valid_question, allow_medium=False)
        assert any("MEDIUM importance questions require explicit allow_medium" in issue for issue in issues_default)

        # Allowed with explicit policy flag
        issues_allowed = QuestionValidator.validate_question(valid_question, allow_medium=True)
        assert issues_allowed == []

    # --------------------------------------------------------------------------
    # 4. Malformed Question Validation & Length Bounds
    # --------------------------------------------------------------------------

    def test_malformed_question_empty_fields(self, valid_question: ResearchQuestion) -> None:
        """Verify missing or whitespace required fields fail validation cleanly."""
        valid_question.question = "   "
        valid_question.context = ""
        valid_question.impact = ""
        valid_question.rationale = ""
        issues = QuestionValidator.validate_question(valid_question)
        assert any("question text must not be empty" in issue for issue in issues)
        assert any("context must not be empty" in issue for issue in issues)
        assert any("impact description must not be empty" in issue for issue in issues)
        assert any("rationale must not be empty" in issue for issue in issues)

    def test_question_text_length_bounds(self, valid_question: ResearchQuestion) -> None:
        """Verify question text length limits (<10 or >500 chars) are enforced."""
        valid_question.question = "Short?"
        issues = QuestionValidator.validate_question(valid_question)
        assert any("question text too short" in issue for issue in issues)

        valid_question.question = "A" * 501
        issues = QuestionValidator.validate_question(valid_question)
        assert any("question text exceeds limit" in issue for issue in issues)

    def test_invalid_decision_type_rejected(self, valid_question: ResearchQuestion) -> None:
        """Verify invalid decision types fail validation."""
        valid_question.decision_type = "HYPER_SPECIFIC_TYPE"  # type: ignore[assignment]
        issues = QuestionValidator.validate_question(valid_question)
        assert any("invalid decision_type" in issue for issue in issues)

    def test_provenance_mismatch_rejected(self, valid_question: ResearchQuestion) -> None:
        """Verify mismatched provenance research_request_id fails validation."""
        assert valid_question.provenance is not None
        valid_question.provenance.research_request_id = "different-request-id"
        issues = QuestionValidator.validate_question(valid_question)
        assert any("provenance research_request_id mismatch" in issue for issue in issues)

    # --------------------------------------------------------------------------
    # 5. Question Tracker & State Machine Progression
    # --------------------------------------------------------------------------

    def test_tracker_presents_question_one_at_a_time(self, valid_question: ResearchQuestion) -> None:
        """Verify QuestionTracker enforces only ONE active question at a time."""
        tracker = QuestionTracker(research_request_id="req-test-100")
        assert not tracker.has_active_question()
        assert tracker.total_questions_count == 0

        # Present first question
        presented = tracker.present_question(valid_question)
        assert presented.is_active()
        assert presented.sequence_number == 1
        assert tracker.has_active_question()
        assert tracker.total_questions_count == 1

        # Attempting to present a second question while active raises ActiveQuestionExistsError
        second_q = ResearchQuestion(
            question_id="rq-dec-002",
            question="What is the secondary latency threshold for testing?",
            context="Latency target was not specified in request.",
            decision_type=DecisionType.PERFORMANCE_PRIORITY,
            impact="Determines load test duration.",
            rationale="Unclear benchmark target.",
            options=[
                QuestionOption(option_id="o1", label="Sub-50ms p99"),
                QuestionOption(option_id="o2", label="Sub-200ms p99"),
            ],
        )
        with pytest.raises(ActiveQuestionExistsError, match="Active question .* is still pending"):
            tracker.present_question(second_q)

    def test_user_answers_with_selected_option(self, valid_question: ResearchQuestion) -> None:
        """Verify user selecting an option resolves the question and updates history."""
        tracker = QuestionTracker(research_request_id="req-test-100")
        tracker.present_question(valid_question)

        answer = ResearchAnswer(
            question_id="rq-dec-001",
            research_request_id="req-test-100",
            selected_option_id="opt-1",
        )
        resolved = tracker.submit_answer(answer)

        assert resolved.is_resolved()
        assert resolved.is_answered()
        assert not resolved.is_skipped()
        assert resolved.state == QuestionState.RESOLVED
        assert resolved.get_selected_option() is not None
        assert resolved.get_selected_option().option_id == "opt-1"
        assert not tracker.has_active_question()
        assert len(tracker.history) == 1

    def test_user_answers_with_custom_answer(self, valid_question: ResearchQuestion) -> None:
        """Verify custom answer is treated as first-class input when custom_answer_allowed=True."""
        tracker = QuestionTracker(research_request_id="req-test-100")
        tracker.present_question(valid_question)

        answer = ResearchAnswer(
            question_id="rq-dec-001",
            research_request_id="req-test-100",
            custom_answer="We want SQLite with LiteFS for edge replication.",
        )
        resolved = tracker.submit_answer(answer)

        assert resolved.is_resolved()
        assert resolved.is_answered()
        assert resolved.answer is not None
        assert resolved.answer.custom_answer == "We want SQLite with LiteFS for edge replication."
        assert any("User provided custom answer" in entry for entry in resolved.question_trace)

    def test_custom_answer_disallowed_rejected(self, valid_question: ResearchQuestion) -> None:
        """Verify custom answer is rejected if custom_answer_allowed is False."""
        valid_question.custom_answer_allowed = False
        tracker = QuestionTracker(research_request_id="req-test-100")
        tracker.present_question(valid_question)

        answer = ResearchAnswer(
            question_id="rq-dec-001",
            research_request_id="req-test-100",
            custom_answer="Custom answer not permitted here",
        )
        with pytest.raises(QuestionValidationError, match="custom_answer_allowed is False"):
            tracker.submit_answer(answer)

    def test_invalid_option_id_rejected(self, valid_question: ResearchQuestion) -> None:
        """Verify selecting a non-existent option ID raises QuestionValidationError."""
        tracker = QuestionTracker(research_request_id="req-test-100")
        tracker.present_question(valid_question)

        answer = ResearchAnswer(
            question_id="rq-dec-001",
            research_request_id="req-test-100",
            selected_option_id="opt-999-invalid",
        )
        with pytest.raises(QuestionValidationError, match="selected_option_id 'opt-999-invalid' not found"):
            tracker.submit_answer(answer)

    def test_user_skips_active_question(self, valid_question: ResearchQuestion) -> None:
        """Verify user skip moves question through USER_SKIPPED -> RESOLVED."""
        tracker = QuestionTracker(research_request_id="req-test-100")
        tracker.present_question(valid_question)

        resolved = tracker.skip_active_question(skip_reason="User deferred to researcher default")
        assert resolved.is_resolved()
        assert resolved.is_skipped()
        assert resolved.answer is not None
        assert resolved.answer.skipped is True
        assert resolved.answer.skip_reason == "User deferred to researcher default"
        assert not tracker.has_active_question()
        assert len(tracker.history) == 1

    def test_cancel_active_question(self, valid_question: ResearchQuestion) -> None:
        """Verify cancellation marks question CANCELLED and clears active slot."""
        tracker = QuestionTracker(research_request_id="req-test-100")
        tracker.present_question(valid_question)

        cancelled = tracker.cancel_active_question(cancel_reason="Research request cancelled")
        assert cancelled.state == QuestionState.CANCELLED
        assert not tracker.has_active_question()
        assert len(tracker.history) == 1

    def test_no_active_question_actions_raise(self) -> None:
        """Verify answering or skipping when no question is active raises NoActiveQuestionError."""
        tracker = QuestionTracker(research_request_id="req-test-100")
        with pytest.raises(NoActiveQuestionError):
            tracker.skip_active_question()

        with pytest.raises(NoActiveQuestionError):
            tracker.submit_answer(ResearchAnswer(question_id="none", research_request_id="req"))

    # --------------------------------------------------------------------------
    # 6. Hard Limit of 1–5 Questions & Monotonic Sequencing
    # --------------------------------------------------------------------------

    def test_hard_limit_of_five_questions_enforced(self) -> None:
        """Verify task can present at most 5 questions; 6th question raises QuestionLimitExceededError."""
        tracker = QuestionTracker(research_request_id="req-limit-test", max_questions=5)

        for i in range(1, 6):
            q = ResearchQuestion(
                question_id=f"rq-{i}",
                question=f"Important decision question number {i} for research planning?",
                context=f"Context description for decision {i}.",
                decision_type=DecisionType.SCOPE,
                impact=f"Impact for decision {i}.",
                rationale=f"Rationale for decision {i}.",
                options=[
                    QuestionOption(option_id="o1", label="First Option"),
                    QuestionOption(option_id="o2", label="Second Option"),
                ],
            )
            presented = tracker.present_question(q)
            assert presented.sequence_number == i

            # Resolve it
            ans = ResearchAnswer(question_id=f"rq-{i}", research_request_id="req-limit-test", selected_option_id="o1")
            tracker.submit_answer(ans)

        assert tracker.total_questions_count == 5
        assert tracker.is_limit_reached()

        # 6th question must be rejected deterministically
        q6 = ResearchQuestion(
            question_id="rq-6",
            question="Sixth question exceeding the hard bound of five?",
            context="Context description for decision 6.",
            decision_type=DecisionType.SCOPE,
            impact="Impact 6.",
            rationale="Rationale 6.",
            options=[
                QuestionOption(option_id="o1", label="Option A"),
                QuestionOption(option_id="o2", label="Option B"),
            ],
        )
        with pytest.raises(QuestionLimitExceededError, match="Maximum question limit \\(5\\) reached"):
            tracker.present_question(q6)

    def test_max_questions_cannot_exceed_five_ceiling(self) -> None:
        """Verify tracker constructor clamps max_questions to 5."""
        tracker = QuestionTracker(research_request_id="req-ceiling", max_questions=10)
        assert tracker.max_questions == 5

    # --------------------------------------------------------------------------
    # 7. Tracker Serialization Roundtrip
    # --------------------------------------------------------------------------

    def test_tracker_serialization_roundtrip(self, valid_question: ResearchQuestion) -> None:
        """Verify QuestionTracker can serialize to dict and reconstruct accurately."""
        tracker = QuestionTracker(research_request_id="req-roundtrip")
        tracker.present_question(valid_question)

        # Answer active question
        ans = ResearchAnswer(
            question_id=valid_question.question_id,
            research_request_id="req-roundtrip",
            selected_option_id="opt-1",
        )
        tracker.submit_answer(ans)

        # Present a second question and leave it active
        q2 = ResearchQuestion(
            question_id="rq-active-2",
            question="Which cloud deployment target should be evaluated?",
            context="Deployment environment affects cost benchmarks.",
            decision_type=DecisionType.ENVIRONMENT,
            impact="Directly affects cost modeling.",
            rationale="AWS vs GCP vs on-prem.",
            options=[
                QuestionOption(option_id="aws", label="AWS EKS"),
                QuestionOption(option_id="gcp", label="GCP GKE"),
            ],
        )
        tracker.present_question(q2)

        data = tracker.to_dict()
        assert data["research_request_id"] == "req-roundtrip"
        assert data["total_questions_count"] == 2
        assert len(data["history"]) == 1
        assert data["active_question"] is not None
        assert data["active_question"]["question_id"] == "rq-active-2"

        reconstructed = QuestionTracker.from_dict(data)
        assert reconstructed.research_request_id == "req-roundtrip"
        assert reconstructed.has_active_question()
        assert reconstructed.active_question is not None
        assert reconstructed.active_question.question_id == "rq-active-2"
        assert len(reconstructed.history) == 1
        assert reconstructed.history[0].question_id == "rq-dec-001"
