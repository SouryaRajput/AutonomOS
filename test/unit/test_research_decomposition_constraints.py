from __future__ import annotations

import unittest

from core.research.decomposition.constraints import (
    DecompositionConstraintError,
    DecompositionConstraintValidator,
)
from core.research.decomposition.model import (
    AcceptanceCriteria,
    ExpectedEvidence,
    ResearchDecomposition,
    ResearchDependency,
    ResearchScope,
    ResearchSubQuestion,
    SubQuestionProvenance,
)
from core.research.decomposition.normalizer import (
    DecompositionNormalizer,
    canonicalize_text,
)
from core.research.decomposition.policy import (
    DEFAULT_MAX_OBJECTIVE_LEN,
    DEFAULT_MAX_PAYLOAD_BYTES,
    DEFAULT_MAX_QUESTION_LEN,
    DEFAULT_MIN_OBJECTIVE_LEN,
    DEFAULT_MIN_QUESTION_LEN,
    DEFAULT_MIN_SUB_QUESTIONS,
    DEFAULT_TARGET_MAX_SUB_QUESTIONS,
    DEFAULT_TARGET_MIN_SUB_QUESTIONS,
    DEFAULT_TARGET_SUB_QUESTIONS,
    MAX_SUB_QUESTIONS,
    DecompositionPolicy,
)
from core.research.decomposition.types import (
    ResearchDependencyType,
    SubQuestionPriority,
    SubQuestionStatus,
    SubQuestionType,
)
from core.research.decomposition.validator import (
    DecompositionValidationError,
    DecompositionValidator,
)


class TestResearchDecompositionConstraints(unittest.TestCase):
    """
    Comprehensive test suite for Phase 2 Step 2.3.2: Deterministic Decomposition Constraints.
    Validates:
    1. Quantity constraints (min, max=8, target 3-5).
    2. Quality constraints (meaningful question, objective, priority, status).
    3. Deterministic duplicate detection (exact and canonical).
    4. Root alignment checks (not trivially identical to root question or objective).
    5. Dependency safety (unknown refs, self-dependencies, directed cycles).
    6. Practical length and payload bounds.
    7. Deterministic normalization and idempotence.
    8. Validation idempotence.
    9. Deterministic reduction policy when enabled.
    10. Integration with DecompositionValidator.
    """

    def setUp(self):
        self.decomp_id = "decomp-c-100"
        self.req_id = "req-c-100"
        self.root_objective = "Evaluate PostgreSQL to DynamoDB migration suitability"
        self.root_question = "Should we migrate our primary database from PostgreSQL to DynamoDB?"

    def _make_sub_question(
        self,
        sub_question_id: str,
        question: str,
        objective: str,
        priority: SubQuestionPriority = SubQuestionPriority.HIGH,
        status: SubQuestionStatus = SubQuestionStatus.PENDING,
        dependencies: list[str] | None = None,
        depth: int = 1,
        parent_id: str | None = None,
    ) -> ResearchSubQuestion:
        return ResearchSubQuestion(
            sub_question_id=sub_question_id,
            decomposition_id=self.decomp_id,
            parent_id=parent_id,
            question=question,
            objective=objective,
            sub_question_type=SubQuestionType.ARCHITECTURAL,
            priority=priority,
            status=status,
            dependencies=dependencies or [],
            decomposition_depth=depth,
        )

    def _make_valid_decomposition(self) -> ResearchDecomposition:
        """Create a valid baseline decomposition with 3 sub-questions."""
        decomp = ResearchDecomposition(
            decomposition_id=self.decomp_id,
            research_request_id=self.req_id,
            objective=self.root_objective,
            root_question=self.root_question,
        )
        sq1 = self._make_sub_question(
            "sq-1",
            "What is our current relational access pattern in PostgreSQL?",
            "Analyze table joins, secondary indexes, and query throughput in PostgreSQL.",
            priority=SubQuestionPriority.HIGH,
        )
        sq2 = self._make_sub_question(
            "sq-2",
            "How does DynamoDB single-table design map to our schema?",
            "Model entities into partition and sort keys for DynamoDB access patterns.",
            priority=SubQuestionPriority.HIGH,
            dependencies=["sq-1"],
        )
        sq3 = self._make_sub_question(
            "sq-3",
            "What are the estimated operational costs and latency impacts?",
            "Calculate read/write capacity unit pricing and cross-region latency in DynamoDB.",
            priority=SubQuestionPriority.MEDIUM,
            dependencies=["sq-2"],
        )
        decomp.sub_questions = [sq1, sq2, sq3]
        decomp.dependencies = [
            ResearchDependency(
                dependency_id="dep-1",
                prerequisite_id="sq-1",
                dependent_id="sq-2",
                dependency_type=ResearchDependencyType.PREREQUISITE,
            ),
            ResearchDependency(
                dependency_id="dep-2",
                prerequisite_id="sq-2",
                dependent_id="sq-3",
                dependency_type=ResearchDependencyType.INFORMS,
            ),
        ]
        return decomp

    # -------------------------------------------------------------------------
    # 1. Quantity Constraints
    # -------------------------------------------------------------------------
    def test_policy_defaults_and_target_range(self):
        """Verify standard policy constants and defaults."""
        self.assertEqual(DEFAULT_MIN_SUB_QUESTIONS, 1)
        self.assertEqual(DEFAULT_TARGET_MIN_SUB_QUESTIONS, 3)
        self.assertEqual(DEFAULT_TARGET_MAX_SUB_QUESTIONS, 5)
        self.assertEqual(DEFAULT_TARGET_SUB_QUESTIONS, (3, 5))
        self.assertEqual(MAX_SUB_QUESTIONS, 8)

        policy = DecompositionPolicy()
        self.assertEqual(policy.min_sub_questions, 1)
        self.assertEqual(policy.target_sub_questions, (3, 5))
        self.assertEqual(policy.max_sub_questions, 8)
        self.assertFalse(policy.allow_reduction_if_over_max)

    def test_zero_sub_questions_rejected(self):
        """Decomposition with zero sub-questions must be rejected."""
        decomp = ResearchDecomposition(
            decomposition_id=self.decomp_id,
            research_request_id=self.req_id,
            objective=self.root_objective,
            root_question=self.root_question,
            sub_questions=[],
        )
        with self.assertRaises(DecompositionConstraintError) as ctx:
            DecompositionConstraintValidator.validate(decomp)

        self.assertTrue(any("minimum required is 1" in issue for issue in ctx.exception.issues))

    def test_too_many_sub_questions_rejected(self):
        """Decomposition with 9 sub-questions (> MAX_SUB_QUESTIONS=8) must be rejected."""
        decomp = self._make_valid_decomposition()
        # Add sub-questions to reach 9
        for i in range(4, 10):
            decomp.sub_questions.append(
                self._make_sub_question(
                    f"sq-{i}",
                    f"What is analytical consideration number {i} for database migration?",
                    f"Investigate architectural aspect number {i} regarding database performance.",
                )
            )
        self.assertEqual(len(decomp.sub_questions), 9)

        with self.assertRaises(DecompositionConstraintError) as ctx:
            DecompositionConstraintValidator.validate(decomp)

        self.assertTrue(any("exceeds maximum allowed limit of 8" in issue for issue in ctx.exception.issues))

    # -------------------------------------------------------------------------
    # 2. Duplicate Detection (Exact & Canonical)
    # -------------------------------------------------------------------------
    def test_exact_duplicate_questions_rejected(self):
        """Sub-questions with exact identical questions must be rejected."""
        decomp = self._make_valid_decomposition()
        decomp.sub_questions[1].question = decomp.sub_questions[0].question

        with self.assertRaises(DecompositionConstraintError) as ctx:
            DecompositionConstraintValidator.validate(decomp)

        self.assertTrue(any("Duplicate sub-question detected" in issue for issue in ctx.exception.issues))

    def test_canonical_duplicate_questions_rejected(self):
        """Sub-questions with questions differing only in casing, punctuation, or spaces must be rejected."""
        decomp = self._make_valid_decomposition()
        # sq-1: "What is our current relational access pattern in PostgreSQL?"
        # sq-2: "  what is our CURRENT relational access pattern in PostgreSQL?  "
        decomp.sub_questions[1].question = "  what is our CURRENT relational access pattern in PostgreSQL?  "

        with self.assertRaises(DecompositionConstraintError) as ctx:
            DecompositionConstraintValidator.validate(decomp)

        self.assertTrue(any("Duplicate sub-question detected" in issue for issue in ctx.exception.issues))

    def test_exact_and_canonical_duplicate_objectives_rejected(self):
        """Sub-questions with canonical duplicate objectives must be rejected."""
        decomp = self._make_valid_decomposition()
        decomp.sub_questions[1].objective = (
            "  analyze table joins, secondary indexes, and query throughput in PostgreSQL.  "
        )

        with self.assertRaises(DecompositionConstraintError) as ctx:
            DecompositionConstraintValidator.validate(decomp)

        self.assertTrue(any("Duplicate sub-question objective detected" in issue for issue in ctx.exception.issues))

    # -------------------------------------------------------------------------
    # 3. Root Alignment
    # -------------------------------------------------------------------------
    def test_sub_question_trivially_identical_to_root_objective_rejected(self):
        """Sub-question question or objective matching root objective must be rejected."""
        decomp = self._make_valid_decomposition()
        decomp.sub_questions[0].question = self.root_objective

        with self.assertRaises(DecompositionConstraintError) as ctx:
            DecompositionConstraintValidator.validate(decomp)

        self.assertTrue(any("trivially identical to root objective" in issue for issue in ctx.exception.issues))

    def test_sub_question_trivially_identical_to_root_question_rejected(self):
        """Sub-question question or objective matching root question must be rejected."""
        decomp = self._make_valid_decomposition()
        decomp.sub_questions[0].question = self.root_question

        with self.assertRaises(DecompositionConstraintError) as ctx:
            DecompositionConstraintValidator.validate(decomp)

        self.assertTrue(any("trivially identical to root question" in issue for issue in ctx.exception.issues))

    # -------------------------------------------------------------------------
    # 4. Dependency Safety
    # -------------------------------------------------------------------------
    def test_self_dependency_rejected(self):
        """Sub-question depending on itself must be rejected."""
        decomp = self._make_valid_decomposition()
        decomp.sub_questions[0].dependencies = ["sq-1"]

        with self.assertRaises(DecompositionConstraintError) as ctx:
            DecompositionConstraintValidator.validate(decomp)

        self.assertTrue(any("cannot depend on itself" in issue for issue in ctx.exception.issues))

    def test_unknown_dependency_rejected(self):
        """Dependency referencing non-existent sub-question must be rejected."""
        decomp = self._make_valid_decomposition()
        decomp.sub_questions[0].dependencies = ["sq-nonexistent"]

        with self.assertRaises(DecompositionConstraintError) as ctx:
            DecompositionConstraintValidator.validate(decomp)

        self.assertTrue(any("references non-existent dependency" in issue for issue in ctx.exception.issues))

    def test_dependency_cycle_rejected(self):
        """Directed dependency cycle (sq-1 -> sq-2 -> sq-3 -> sq-1) must be detected and rejected."""
        decomp = self._make_valid_decomposition()
        # Make sq-1 depend on sq-3, creating sq-1 -> sq-2 -> sq-3 -> sq-1
        decomp.sub_questions[0].dependencies = ["sq-3"]
        decomp.dependencies.append(
            ResearchDependency(
                dependency_id="dep-cycle",
                prerequisite_id="sq-3",
                dependent_id="sq-1",
            )
        )

        with self.assertRaises(DecompositionConstraintError) as ctx:
            DecompositionConstraintValidator.validate(decomp)

        self.assertTrue(any("Dependency cycle detected" in issue for issue in ctx.exception.issues))

    # -------------------------------------------------------------------------
    # 5. Practical Length and Payload Bounds
    # -------------------------------------------------------------------------
    def test_oversized_question_rejected(self):
        """Question exceeding max_question_len (500 chars) must be rejected."""
        decomp = self._make_valid_decomposition()
        decomp.sub_questions[0].question = "What is " + ("very long question " * 40)

        with self.assertRaises(DecompositionConstraintError) as ctx:
            DecompositionConstraintValidator.validate(decomp)

        self.assertTrue(any("question length" in issue and "exceeds maximum limit" in issue for issue in ctx.exception.issues))

    def test_undersized_or_empty_question_rejected(self):
        """Question < min_question_len (10 chars) or punctuation-only must be rejected."""
        decomp = self._make_valid_decomposition()
        decomp.sub_questions[0].question = "Why???"

        with self.assertRaises(DecompositionConstraintError) as ctx:
            DecompositionConstraintValidator.validate(decomp)

        self.assertTrue(any("below minimum" in issue for issue in ctx.exception.issues))

        # Punctuation-only
        decomp.sub_questions[0].question = "???????????"
        with self.assertRaises(DecompositionConstraintError) as ctx2:
            DecompositionConstraintValidator.validate(decomp)

        self.assertTrue(any("lacks substantive alphanumeric content" in issue for issue in ctx2.exception.issues))

    def test_oversized_objective_rejected(self):
        """Objective exceeding max_objective_len (1000 chars) must be rejected."""
        decomp = self._make_valid_decomposition()
        decomp.sub_questions[0].objective = "Analyze " + ("detailed background requirements " * 50)

        with self.assertRaises(DecompositionConstraintError) as ctx:
            DecompositionConstraintValidator.validate(decomp)

        self.assertTrue(any("objective length" in issue and "exceeds maximum limit" in issue for issue in ctx.exception.issues))

    def test_undersized_or_empty_objective_rejected(self):
        """Objective < min_objective_len (10 chars) must be rejected."""
        decomp = self._make_valid_decomposition()
        decomp.sub_questions[0].objective = "Short."

        with self.assertRaises(DecompositionConstraintError) as ctx:
            DecompositionConstraintValidator.validate(decomp)

        self.assertTrue(any("objective length" in issue and "below minimum" in issue for issue in ctx.exception.issues))

    def test_too_many_dependencies_per_question(self):
        """Sub-question with dependencies exceeding max_dependencies_per_question must be rejected."""
        decomp = self._make_valid_decomposition()
        # Set max_dependencies_per_question = 1 in policy
        strict_policy = DecompositionPolicy(max_dependencies_per_question=1)
        decomp.sub_questions[2].dependencies = ["sq-1", "sq-2"]

        with self.assertRaises(DecompositionConstraintError) as ctx:
            DecompositionConstraintValidator.validate(decomp, policy=strict_policy)

        self.assertTrue(any("dependencies; exceeds limit" in issue for issue in ctx.exception.issues))

    def test_total_payload_size_limit_exceeded(self):
        """Decomposition whose payload exceeds max_payload_bytes must be rejected."""
        decomp = self._make_valid_decomposition()
        # Set a tiny max_payload_bytes for testing
        tiny_policy = DecompositionPolicy(max_payload_bytes=200)

        with self.assertRaises(DecompositionConstraintError) as ctx:
            DecompositionConstraintValidator.validate(decomp, policy=tiny_policy)

        self.assertTrue(any("payload size" in issue and "exceeds maximum allowed limit" in issue for issue in ctx.exception.issues))

    # -------------------------------------------------------------------------
    # 6. Quality, Priority, Status & Identity Checks
    # -------------------------------------------------------------------------
    def test_invalid_priority_and_status_rejected(self):
        """Sub-question with invalid priority or status must be rejected."""
        decomp = self._make_valid_decomposition()
        decomp.sub_questions[0].priority = "SUPER_URGENT"  # type: ignore

        with self.assertRaises(DecompositionConstraintError) as ctx:
            DecompositionConstraintValidator.validate(decomp)

        self.assertTrue(any("invalid priority" in issue for issue in ctx.exception.issues))

    def test_decomposition_id_mismatch_rejected(self):
        """Sub-question with mismatched decomposition_id must be rejected."""
        decomp = self._make_valid_decomposition()
        decomp.sub_questions[0].decomposition_id = "mismatched-decomp-id"

        with self.assertRaises(DecompositionConstraintError) as ctx:
            DecompositionConstraintValidator.validate(decomp)

        self.assertTrue(any("does not match parent decomposition" in issue for issue in ctx.exception.issues))

    # -------------------------------------------------------------------------
    # 7. Valid Decomposition & Validation Idempotence
    # -------------------------------------------------------------------------
    def test_valid_decomposition_passes_all_constraints(self):
        """A well-formed decomposition with 3 sub-questions passes validation with 0 issues."""
        decomp = self._make_valid_decomposition()
        issues = DecompositionConstraintValidator.validate(decomp, raise_on_error=True)
        self.assertEqual(issues, [])

    def test_validation_idempotence(self):
        """Multiple validation runs produce identical issues with zero mutation or side-effects."""
        decomp = self._make_valid_decomposition()
        issues_1 = DecompositionConstraintValidator.validate(decomp, raise_on_error=False)
        issues_2 = DecompositionConstraintValidator.validate(decomp, raise_on_error=False)
        self.assertEqual(issues_1, issues_2)
        self.assertEqual(issues_1, [])

    # -------------------------------------------------------------------------
    # 8. Deterministic Normalization & Idempotence
    # -------------------------------------------------------------------------
    def test_deterministic_normalization_and_idempotence(self):
        """Verify normalizer cleans whitespace, deduplicates lists, coerces enums, and is idempotent."""
        decomp = self._make_valid_decomposition()
        decomp.objective = "  Evaluate   PostgreSQL to DynamoDB migration  suitability  "
        decomp.sub_questions[0].question = " What is our current   relational access pattern? "
        decomp.sub_questions[0].dependencies = ["sq-2", "sq-2", "  sq-3  ", "sq-1"]  # includes self and duplicate
        decomp.sub_questions[0].priority = "critical"  # string enum

        normalized_1 = DecompositionNormalizer.normalize(decomp)
        self.assertEqual(normalized_1.objective, "Evaluate PostgreSQL to DynamoDB migration suitability")
        self.assertEqual(normalized_1.sub_questions[0].question, "What is our current relational access pattern?")
        self.assertEqual(normalized_1.sub_questions[0].priority, SubQuestionPriority.CRITICAL)
        # Self-dependency dropped, duplicates dropped, whitespace trimmed
        self.assertEqual(normalized_1.sub_questions[0].dependencies, ["sq-2", "sq-3"])

        # Idempotence: normalize(normalize(d)) == normalize(d)
        normalized_2 = DecompositionNormalizer.normalize(normalized_1)
        self.assertEqual(normalized_1.to_dict(), normalized_2.to_dict())

    def test_canonicalize_text_helper(self):
        """Verify canonicalize_text trims punctuation, lowercases, and collapses whitespace."""
        self.assertEqual(
            canonicalize_text("  What is DynamoDB single-table design???  "),
            "what is dynamodb single-table design",
        )
        self.assertEqual(
            canonicalize_text("What is DynamoDB single-table design!"),
            "what is dynamodb single-table design",
        )
        self.assertEqual(canonicalize_text(""), "")
        self.assertEqual(canonicalize_text(None), "")

    # -------------------------------------------------------------------------
    # 9. Deterministic Reduction Policy
    # -------------------------------------------------------------------------
    def test_deterministic_reduction_policy(self):
        """When allow_reduction_if_over_max is True, excess sub-questions are pruned deterministically by priority."""
        decomp = self._make_valid_decomposition()
        # Add 7 more sub-questions to reach 10 (exceeding limit of 8)
        for i in range(4, 11):
            decomp.sub_questions.append(
                self._make_sub_question(
                    f"sq-{i}",
                    f"What is analytical consideration number {i} for database migration?",
                    f"Investigate architectural aspect number {i} regarding database performance.",
                    priority=SubQuestionPriority.LOW if i > 8 else SubQuestionPriority.HIGH,
                )
            )
        self.assertEqual(len(decomp.sub_questions), 10)

        policy = DecompositionPolicy(max_sub_questions=8, allow_reduction_if_over_max=True)
        issues = DecompositionConstraintValidator.validate(decomp, policy=policy, raise_on_error=True)
        self.assertEqual(issues, [])
        self.assertEqual(len(decomp.sub_questions), 8)
        # The 2 LOW priority sub-questions (sq-9 and sq-10) should have been pruned
        remaining_ids = {sq.sub_question_id for sq in decomp.sub_questions}
        self.assertNotIn("sq-9", remaining_ids)
        self.assertNotIn("sq-10", remaining_ids)

    # -------------------------------------------------------------------------
    # 10. Integration with DecompositionValidator
    # -------------------------------------------------------------------------
    def test_decomposition_validator_integration(self):
        """Verify DecompositionValidator integrates constraints via validate_constraints and validate(..., policy=...)."""
        decomp = self._make_valid_decomposition()

        # 1. validate_constraints succeeds on valid decomposition
        issues = DecompositionValidator.validate_constraints(decomp)
        self.assertEqual(issues, [])

        # 2. validate(decomp, policy=policy) runs both structural and constraint validation
        policy = DecompositionPolicy()
        errors = DecompositionValidator.validate(decomp, policy=policy)
        self.assertEqual(errors, [])

        # 3. validate(decomp, policy=policy) fails on constraint violations
        decomp.sub_questions[1].question = decomp.sub_questions[0].question  # duplicate
        with self.assertRaises(DecompositionValidationError):
            DecompositionValidator.validate(decomp, policy=policy)


if __name__ == "__main__":
    unittest.main()
