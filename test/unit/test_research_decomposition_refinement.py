from __future__ import annotations

import json
import pytest

from core.research.contracts.intent import ResearchIntent
from core.research.contracts.request import ResearchRequest
from core.research.decomposition.generator import (
    DecompositionProposal,
    RawSubQuestionProposal,
)
from core.research.decomposition.model import (
    ResearchDecomposition,
    ResearchDependency,
    ResearchSubQuestion,
)
from core.research.decomposition.policy import DecompositionPolicy
from core.research.decomposition.refinement import (
    DecompositionRefiner,
    DecompositionValidationIssue,
    DecompositionValidationIssueCode,
    DecompositionValidationResult,
    DecompositionValidationStatus,
)
from core.research.decomposition.types import (
    SubQuestionPriority,
    SubQuestionStatus,
    SubQuestionType,
)


def make_test_intent(
    objective: str = "Compare GSAP and Motion for Next.js animation.",
    root_question: str = "Which animation library best fits Next.js?",
) -> ResearchIntent:
    intent = ResearchIntent(
        intent_id="intent-test-123",
        source_request_id="req-test-123",
        objective=objective,
        research_dimensions=["performance", "react_compatibility"],
    )
    intent.root_question = root_question
    return intent


class TestDecompositionRefinement:
    """Unit test suite for Step 2.3.5: Decomposition Validation & Refinement."""

    # -------------------------------------------------------------------------
    # Scenario 1: Perfect decomposition -> Accepted (VALID)
    # -------------------------------------------------------------------------
    def test_perfect_decomposition_accepted(self):
        intent = make_test_intent()
        proposal = DecompositionProposal(
            decomposition_rationale="Balanced 3-question breakdown.",
            sub_questions=[
                RawSubQuestionProposal(
                    temp_id="sq-1",
                    question="What animation capabilities does GSAP provide?",
                    objective="Assess GSAP animation features and timeline control.",
                    sub_question_type="FACT_FINDING",
                    priority="HIGH",
                    required=True,
                ),
                RawSubQuestionProposal(
                    temp_id="sq-2",
                    question="What animation capabilities does Motion provide?",
                    objective="Assess Motion features and declarative React integration.",
                    sub_question_type="FACT_FINDING",
                    priority="HIGH",
                    required=True,
                ),
                RawSubQuestionProposal(
                    temp_id="sq-3",
                    question="How do GSAP and Motion compare in Next.js performance benchmarks?",
                    objective="Benchmark bundle size, frame rate, and SSR hydration impact.",
                    sub_question_type="COMPARISON",
                    priority="CRITICAL",
                    required=True,
                    dependencies=["sq-1", "sq-2"],
                ),
            ],
        )

        res = DecompositionRefiner.validate_and_refine(intent, proposal)
        assert res.is_valid is True
        assert res.status == DecompositionValidationStatus.VALID
        assert res.decomposition is not None
        assert len(res.decomposition.sub_questions) == 3
        assert len(res.decomposition.dependencies) == 2
        assert len(res.issues) == 0
        assert res.confidence == 1.0

    # -------------------------------------------------------------------------
    # Scenario 2: Duplicate questions -> Rejected or safely normalized
    # -------------------------------------------------------------------------
    def test_duplicate_questions_rejected_when_dedup_forbidden(self):
        intent = make_test_intent()
        proposal = DecompositionProposal(
            sub_questions=[
                RawSubQuestionProposal(
                    temp_id="sq-1",
                    question="What animation capabilities does GSAP provide?",
                    objective="Assess GSAP features.",
                    priority="HIGH",
                ),
                RawSubQuestionProposal(
                    temp_id="sq-2",
                    question="What animation capabilities does GSAP provide?  ",  # canonical duplicate
                    objective="Assess GSAP features duplicate.",
                    priority="HIGH",
                ),
            ],
        )
        policy = DecompositionPolicy(allow_exact_deduplication=False)
        res = DecompositionRefiner.validate_and_refine(intent, proposal, policy=policy)
        assert res.is_valid is False
        assert res.status == DecompositionValidationStatus.RETRYABLE_INVALID
        assert any(i.code == DecompositionValidationIssueCode.DUPLICATE_QUESTION.value for i in res.issues)

    def test_duplicate_questions_safely_normalized_when_permitted(self):
        intent = make_test_intent()
        proposal = DecompositionProposal(
            sub_questions=[
                RawSubQuestionProposal(
                    temp_id="sq-1",
                    question="What animation capabilities does GSAP provide?",
                    objective="Assess GSAP features.",
                    priority="HIGH",
                ),
                RawSubQuestionProposal(
                    temp_id="sq-2",
                    question="What animation capabilities does GSAP provide?  ",
                    objective="Assess GSAP features identical.",
                    priority="HIGH",
                ),
            ],
        )
        policy = DecompositionPolicy(allow_exact_deduplication=True)
        res = DecompositionRefiner.validate_and_refine(intent, proposal, policy=policy)
        assert res.is_valid is True
        assert res.status == DecompositionValidationStatus.VALID
        assert len(res.decomposition.sub_questions) == 1
        assert any("safely_deduplicated_sub_question" in a for a in res.refinements_applied)

    # -------------------------------------------------------------------------
    # Scenario 3: 20 questions -> Rejected (TOO_MANY_QUESTIONS)
    # -------------------------------------------------------------------------
    def test_twenty_questions_rejected(self):
        intent = make_test_intent()
        sqs = [
            RawSubQuestionProposal(
                temp_id=f"sq-{i}",
                question=f"Detailed investigative inquiry number {i} regarding library capabilities?",
                objective=f"Analyze dimension {i} in exhaustive detail.",
                priority="MEDIUM",
            )
            for i in range(1, 21)
        ]
        proposal = DecompositionProposal(sub_questions=sqs)
        policy = DecompositionPolicy(allow_reduction_if_over_max=False)
        res = DecompositionRefiner.validate_and_refine(intent, proposal, policy=policy)
        assert res.is_valid is False
        assert res.status == DecompositionValidationStatus.RETRYABLE_INVALID
        assert any(i.code == DecompositionValidationIssueCode.TOO_MANY_QUESTIONS.value for i in res.issues)

    # -------------------------------------------------------------------------
    # Scenario 4: Missing required field -> Rejected
    # -------------------------------------------------------------------------
    def test_missing_required_field_rejected(self):
        intent = make_test_intent()
        proposal = DecompositionProposal(
            sub_questions=[
                RawSubQuestionProposal(
                    temp_id="sq-1",
                    question="",  # Empty question text
                    objective="Assess GSAP features.",
                    priority="HIGH",
                ),
            ],
        )
        res = DecompositionRefiner.validate_and_refine(intent, proposal)
        assert res.is_valid is False
        assert res.status == DecompositionValidationStatus.RETRYABLE_INVALID
        assert any(i.code == DecompositionValidationIssueCode.MALFORMED_QUESTION.value for i in res.issues)

    def test_empty_objective_rejected(self):
        intent = make_test_intent()
        proposal = DecompositionProposal(
            sub_questions=[
                RawSubQuestionProposal(
                    temp_id="sq-1",
                    question="What animation capabilities does GSAP provide?",
                    objective="   ",  # Whitespace only
                    priority="HIGH",
                ),
            ],
        )
        res = DecompositionRefiner.validate_and_refine(intent, proposal)
        assert res.is_valid is False
        assert res.status == DecompositionValidationStatus.RETRYABLE_INVALID
        assert any(i.code == DecompositionValidationIssueCode.EMPTY_OBJECTIVE.value for i in res.issues)

    # -------------------------------------------------------------------------
    # Scenario 5: Bad dependency -> Rejected
    # -------------------------------------------------------------------------
    def test_bad_dependency_rejected(self):
        intent = make_test_intent()
        proposal = DecompositionProposal(
            sub_questions=[
                RawSubQuestionProposal(
                    temp_id="sq-1",
                    question="What animation capabilities does GSAP provide?",
                    objective="Assess GSAP features.",
                    priority="HIGH",
                    dependencies=["sq-non-existent-99"],  # Invalid reference
                ),
            ],
        )
        res = DecompositionRefiner.validate_and_refine(intent, proposal)
        assert res.is_valid is False
        assert res.status == DecompositionValidationStatus.RETRYABLE_INVALID
        assert any(i.code == DecompositionValidationIssueCode.INVALID_DEPENDENCY.value for i in res.issues)

    def test_self_dependency_rejected(self):
        intent = make_test_intent()
        proposal = DecompositionProposal(
            sub_questions=[
                RawSubQuestionProposal(
                    temp_id="sq-1",
                    question="What animation capabilities does GSAP provide?",
                    objective="Assess GSAP features.",
                    priority="HIGH",
                    dependencies=["sq-1"],  # Self-dependency
                ),
            ],
        )
        res = DecompositionRefiner.validate_and_refine(intent, proposal)
        assert res.is_valid is False
        assert any(i.code == DecompositionValidationIssueCode.INVALID_DEPENDENCY.value for i in res.issues)

    # -------------------------------------------------------------------------
    # Scenario 6: Dependency cycle -> Rejected
    # -------------------------------------------------------------------------
    def test_dependency_cycle_rejected(self):
        intent = make_test_intent()
        proposal = DecompositionProposal(
            sub_questions=[
                RawSubQuestionProposal(
                    temp_id="sq-1",
                    question="What animation capabilities does GSAP provide?",
                    objective="Assess GSAP features.",
                    dependencies=["sq-2"],
                ),
                RawSubQuestionProposal(
                    temp_id="sq-2",
                    question="What animation capabilities does Motion provide?",
                    objective="Assess Motion features.",
                    dependencies=["sq-1"],  # Cycle: sq-1 -> sq-2 -> sq-1
                ),
            ],
        )
        res = DecompositionRefiner.validate_and_refine(intent, proposal)
        assert res.is_valid is False
        assert res.status == DecompositionValidationStatus.RETRYABLE_INVALID
        assert any(i.code == DecompositionValidationIssueCode.DEPENDENCY_CYCLE.value for i in res.issues)

    # -------------------------------------------------------------------------
    # Scenario 7: Valid proposal with optional questions -> Accepted
    # -------------------------------------------------------------------------
    def test_valid_proposal_with_optional_questions_accepted(self):
        intent = make_test_intent()
        proposal = DecompositionProposal(
            sub_questions=[
                RawSubQuestionProposal(
                    temp_id="sq-1",
                    question="What animation capabilities does GSAP provide?",
                    objective="Assess GSAP features.",
                    required=True,
                ),
                RawSubQuestionProposal(
                    temp_id="sq-2",
                    question="What community plugins are available for GSAP in React?",
                    objective="Investigate optional third-party integrations.",
                    required=False,  # Optional question
                ),
            ],
        )
        res = DecompositionRefiner.validate_and_refine(intent, proposal)
        assert res.is_valid is True
        assert res.status == DecompositionValidationStatus.VALID
        sq_map = {sq.question: sq for sq in res.decomposition.sub_questions}
        assert sq_map["What animation capabilities does GSAP provide?"].required is True
        assert sq_map["What community plugins are available for GSAP in React?"].required is False

    # -------------------------------------------------------------------------
    # Scenario 8: Malformed provider response -> Invalid
    # -------------------------------------------------------------------------
    def test_malformed_provider_response_invalid(self):
        intent = make_test_intent()
        malformed_strings = [
            "This is not a JSON object at all.",
            '{"sub_questions": "not a list"}',
            '{"unrelated_key": 123}',
            "",
            None,
        ]
        for bad_input in malformed_strings:
            res = DecompositionRefiner.validate_and_refine(intent, bad_input)
            assert res.is_valid is False
            assert res.status == DecompositionValidationStatus.INVALID
            assert any(i.code == DecompositionValidationIssueCode.MALFORMED_OUTPUT.value for i in res.issues)

    # -------------------------------------------------------------------------
    # Scenario 9: Safe normalization -> Stable output
    # -------------------------------------------------------------------------
    def test_safe_normalization_preserves_semantics(self):
        intent = make_test_intent()
        proposal = DecompositionProposal(
            sub_questions=[
                RawSubQuestionProposal(
                    temp_id="  sq-1  ",
                    question="  What   animation   capabilities does   GSAP   provide?  ",
                    objective="  Assess   GSAP   features.  ",
                    sub_question_type="fact_finding",  # Lowercase enum string
                    priority="high",                   # Lowercase enum string
                    rationale="   Baseline    understanding.   ",
                ),
            ],
        )
        res = DecompositionRefiner.validate_and_refine(intent, proposal)
        assert res.is_valid is True
        sq = res.decomposition.sub_questions[0]
        assert sq.question == "What animation capabilities does GSAP provide?"
        assert sq.objective == "Assess GSAP features."
        assert sq.rationale == "Baseline understanding."
        assert sq.sub_question_type == SubQuestionType.FACT_FINDING
        assert sq.priority == SubQuestionPriority.HIGH

    # -------------------------------------------------------------------------
    # Scenario 10: Same input -> Deterministic result
    # -------------------------------------------------------------------------
    def test_deterministic_output_across_runs(self):
        intent = make_test_intent()
        proposal_dict = {
            "sub_questions": [
                {
                    "temp_id": "sq-1",
                    "question": "What animation capabilities does GSAP provide?",
                    "objective": "Assess GSAP features and core capabilities.",
                    "priority": "HIGH",
                    "required": True,
                },
                {
                    "temp_id": "sq-2",
                    "question": "What animation capabilities does Motion provide?",
                    "objective": "Assess Motion features and core capabilities.",
                    "priority": "HIGH",
                    "required": True,
                },
            ]
        }

        res1 = DecompositionRefiner.validate_and_refine(intent, proposal_dict)
        res2 = DecompositionRefiner.validate_and_refine(intent, proposal_dict)

        assert res1.status == res2.status == DecompositionValidationStatus.VALID
        assert len(res1.decomposition.sub_questions) == len(res2.decomposition.sub_questions)
        # Questions texts and priorities identical
        for sq1, sq2 in zip(res1.decomposition.sub_questions, res2.decomposition.sub_questions):
            assert sq1.question == sq2.question
            assert sq1.objective == sq2.objective
            assert sq1.priority == sq2.priority

    # -------------------------------------------------------------------------
    # Additional Robustness: All Optional Questions Rejected
    # -------------------------------------------------------------------------
    def test_all_optional_questions_rejected(self):
        intent = make_test_intent()
        proposal = DecompositionProposal(
            sub_questions=[
                RawSubQuestionProposal(
                    temp_id="sq-1",
                    question="What animation capabilities does GSAP provide?",
                    objective="Assess GSAP features.",
                    required=False,
                ),
                RawSubQuestionProposal(
                    temp_id="sq-2",
                    question="What animation capabilities does Motion provide?",
                    objective="Assess Motion features.",
                    required=False,
                ),
            ],
        )
        res = DecompositionRefiner.validate_and_refine(intent, proposal)
        assert res.is_valid is False
        assert res.status == DecompositionValidationStatus.RETRYABLE_INVALID
        assert any(i.code == DecompositionValidationIssueCode.INCONSISTENT_REQUIRED_FIELDS.value for i in res.issues)

    # -------------------------------------------------------------------------
    # Additional Robustness: Redundant Root Question Rejected
    # -------------------------------------------------------------------------
    def test_redundant_root_question_rejected(self):
        intent = make_test_intent(
            objective="Compare GSAP and Motion.",
            root_question="Compare GSAP and Motion.",
        )
        proposal = DecompositionProposal(
            sub_questions=[
                RawSubQuestionProposal(
                    temp_id="sq-1",
                    question="Compare GSAP and Motion.",  # Verbatim duplicate of root question
                    objective="Compare both libraries.",
                ),
            ],
        )
        res = DecompositionRefiner.validate_and_refine(intent, proposal)
        assert res.is_valid is False
        assert any(i.code == DecompositionValidationIssueCode.REDUNDANT_ROOT_QUESTION.value for i in res.issues)

    # -------------------------------------------------------------------------
    # Additional Robustness: Serialization Roundtrips
    # -------------------------------------------------------------------------
    def test_serialization_roundtrips(self):
        issue = DecompositionValidationIssue(
            code=DecompositionValidationIssueCode.DEPENDENCY_CYCLE.value,
            message="Circular dependency detected",
            field="dependencies",
            sub_question_id="sq-1",
            retryable=True,
        )
        issue_dict = issue.to_dict()
        issue_restored = DecompositionValidationIssue.from_dict(issue_dict)
        assert issue == issue_restored

        res = DecompositionValidationResult(
            status=DecompositionValidationStatus.RETRYABLE_INVALID,
            issues=[issue],
            refinements_applied=["normalized_whitespace"],
            confidence=0.9,
            trace=["Trace message 1"],
        )
        res_dict = res.to_dict()
        res_restored = DecompositionValidationResult.from_dict(res_dict)
        assert res_restored.status == DecompositionValidationStatus.RETRYABLE_INVALID
        assert len(res_restored.issues) == 1
        assert res_restored.issues[0].code == issue.code
        assert res_restored.confidence == 0.9
