from __future__ import annotations

import unittest

from core.research.decomposition.model import (
    ResearchDecomposition,
    ResearchDependency,
    ResearchSubQuestion,
)
from core.research.decomposition.ordering import (
    DecompositionOrderPlan,
    DecompositionOrderPlanner,
    ExecutionStage,
    QuestionReadiness,
    SubQuestionOrderPlanner,
    get_priority_weight,
)
from core.research.decomposition.types import (
    ResearchDependencyType,
    SubQuestionPriority,
    SubQuestionStatus,
    SubQuestionType,
)
from core.research.decomposition.validator import DecompositionValidationError


class TestResearchDecompositionOrdering(unittest.TestCase):
    """
    Comprehensive test suite for Phase 2 Step 2.3.4: Basic Priority & Dependency Ordering.
    Validates:
    1. Independent questions ordered by priority and original index.
    2. Linear dependency chains preserving execution order regardless of priority.
    3. Dependencies taking strict precedence over priority.
    4. Multiple ready questions grouped into stages.
    5. Priority tie-breaking among ready questions.
    6. Multiple dependencies join condition.
    7. Cycle rejection raising structured validation error.
    8. Deterministic stable ordering.
    9. Exact prompt example ordering.
    10. Dynamic readiness query evaluation.
    11. Model integration via decomp.plan_execution_order().
    12. Serialization / deserialization round-trip.
    """

    def setUp(self):
        self.decomp_id = "decomp-ord-100"
        self.req_id = "req-ord-100"

    def _make_sub_question(
        self,
        sub_question_id: str,
        question: str,
        priority: SubQuestionPriority = SubQuestionPriority.HIGH,
        dependencies: list[str] | None = None,
    ) -> ResearchSubQuestion:
        return ResearchSubQuestion(
            sub_question_id=sub_question_id,
            decomposition_id=self.decomp_id,
            question=question,
            objective=f"Objective for {question}",
            sub_question_type=SubQuestionType.FACT_FINDING,
            priority=priority,
            status=SubQuestionStatus.PENDING,
            dependencies=dependencies or [],
        )

    # -------------------------------------------------------------------------
    # 1. Independent Questions (Priority & Index Tie-Breaking)
    # -------------------------------------------------------------------------
    def test_independent_questions_ordered_by_priority_and_index(self):
        """When sub-questions have no dependencies, order strictly by priority descending, then index."""
        decomp = ResearchDecomposition(
            decomposition_id=self.decomp_id,
            research_request_id=self.req_id,
            objective="Evaluate multiple independent options",
            root_question="What are the options?",
        )
        q1 = self._make_sub_question("sq-1", "Evaluate Low priority aspect", priority=SubQuestionPriority.LOW)
        q2 = self._make_sub_question("sq-2", "Evaluate High priority aspect 1", priority=SubQuestionPriority.HIGH)
        q3 = self._make_sub_question("sq-3", "Evaluate Critical priority aspect", priority=SubQuestionPriority.CRITICAL)
        q4 = self._make_sub_question("sq-4", "Evaluate High priority aspect 2", priority=SubQuestionPriority.HIGH)
        q5 = self._make_sub_question("sq-5", "Evaluate Medium priority aspect", priority=SubQuestionPriority.MEDIUM)

        decomp.sub_questions = [q1, q2, q3, q4, q5]

        plan = SubQuestionOrderPlanner.plan_order(decomp)

        # Expected total order:
        # 1. sq-3 (CRITICAL)
        # 2. sq-2 (HIGH, index 1)
        # 3. sq-4 (HIGH, index 3)
        # 4. sq-5 (MEDIUM)
        # 5. sq-1 (LOW)
        self.assertEqual(plan.linear_execution_ids, ["sq-3", "sq-2", "sq-4", "sq-5", "sq-1"])
        self.assertEqual(len(plan.stages), 1)
        self.assertEqual(plan.stages[0].stage_index, 0)
        self.assertEqual(plan.stages[0].sub_question_ids, ["sq-3", "sq-2", "sq-4", "sq-5", "sq-1"])

    # -------------------------------------------------------------------------
    # 2. Linear Dependency Chain
    # -------------------------------------------------------------------------
    def test_linear_dependency_chain(self):
        """A linear dependency chain (Q1 -> Q2 -> Q3) strictly preserves order regardless of priority."""
        decomp = ResearchDecomposition(
            decomposition_id=self.decomp_id,
            research_request_id=self.req_id,
            objective="Linear pipeline review",
            root_question="How to proceed?",
        )
        # Q1 is LOW priority, Q3 is CRITICAL
        q1 = self._make_sub_question("sq-1", "Step 1: Low priority foundation", priority=SubQuestionPriority.LOW)
        q2 = self._make_sub_question("sq-2", "Step 2: Medium priority midpoint", priority=SubQuestionPriority.MEDIUM, dependencies=["sq-1"])
        q3 = self._make_sub_question("sq-3", "Step 3: Critical priority outcome", priority=SubQuestionPriority.CRITICAL, dependencies=["sq-2"])

        decomp.sub_questions = [q1, q2, q3]
        decomp.dependencies = [
            ResearchDependency(dependency_id="d1", prerequisite_id="sq-1", dependent_id="sq-2"),
            ResearchDependency(dependency_id="d2", prerequisite_id="sq-2", dependent_id="sq-3"),
        ]

        plan = SubQuestionOrderPlanner.plan_order(decomp)

        # Dependencies strictly govern order
        self.assertEqual(plan.linear_execution_ids, ["sq-1", "sq-2", "sq-3"])
        # Three sequential stages
        self.assertEqual(len(plan.stages), 3)
        self.assertEqual(plan.stages[0].sub_question_ids, ["sq-1"])
        self.assertEqual(plan.stages[1].sub_question_ids, ["sq-2"])
        self.assertEqual(plan.stages[2].sub_question_ids, ["sq-3"])

    # -------------------------------------------------------------------------
    # 3. Dependency Precedence Over Priority
    # -------------------------------------------------------------------------
    def test_dependency_precedence_over_priority(self):
        """Dependencies always take absolute precedence over priority."""
        decomp = ResearchDecomposition(
            decomposition_id=self.decomp_id,
            research_request_id=self.req_id,
            objective="Precedence verification",
            root_question="Precedence?",
        )
        # sq-indep is CRITICAL and has no dependencies
        # sq-dep is CRITICAL but depends on sq-prereq (which is LOW)
        sq_prereq = self._make_sub_question("sq-prereq", "Prerequisite step", priority=SubQuestionPriority.LOW)
        sq_dep = self._make_sub_question("sq-dep", "Dependent step", priority=SubQuestionPriority.CRITICAL, dependencies=["sq-prereq"])
        sq_indep = self._make_sub_question("sq-indep", "Independent step", priority=SubQuestionPriority.CRITICAL)

        decomp.sub_questions = [sq_prereq, sq_dep, sq_indep]

        plan = SubQuestionOrderPlanner.plan_order(decomp)

        # sq-indep (CRITICAL) and sq-prereq (LOW) are ready at Stage 0.
        # sq-indep executes first because CRITICAL > LOW.
        # sq-prereq executes next.
        # sq-dep executes only after sq-prereq completes.
        self.assertEqual(plan.linear_execution_ids, ["sq-indep", "sq-prereq", "sq-dep"])

    # -------------------------------------------------------------------------
    # 4. Multiple Ready Questions & Stage Grouping
    # -------------------------------------------------------------------------
    def test_multiple_ready_questions_stage_grouping(self):
        """Verify multiple ready questions are grouped into stages with deterministic ordering."""
        decomp = ResearchDecomposition(
            decomposition_id=self.decomp_id,
            research_request_id=self.req_id,
            objective="Stage grouping test",
            root_question="Stage grouping?",
        )
        # Stage 0: Q1 (HIGH), Q2 (MEDIUM)
        q1 = self._make_sub_question("sq-1", "Stage 0 item A", priority=SubQuestionPriority.HIGH)
        q2 = self._make_sub_question("sq-2", "Stage 0 item B", priority=SubQuestionPriority.MEDIUM)
        # Stage 1: Q3 (depends on Q1, MEDIUM), Q4 (depends on Q2, HIGH)
        q3 = self._make_sub_question("sq-3", "Stage 1 item A", priority=SubQuestionPriority.MEDIUM, dependencies=["sq-1"])
        q4 = self._make_sub_question("sq-4", "Stage 1 item B", priority=SubQuestionPriority.HIGH, dependencies=["sq-2"])

        decomp.sub_questions = [q1, q2, q3, q4]

        plan = SubQuestionOrderPlanner.plan_order(decomp)

        self.assertEqual(len(plan.stages), 2)
        # Stage 0: Q1 (HIGH) before Q2 (MEDIUM)
        self.assertEqual(plan.stages[0].sub_question_ids, ["sq-1", "sq-2"])
        # Stage 1: Q4 (HIGH) before Q3 (MEDIUM)
        self.assertEqual(plan.stages[1].sub_question_ids, ["sq-4", "sq-3"])

    # -------------------------------------------------------------------------
    # 5. Multiple Dependencies Join Condition
    # -------------------------------------------------------------------------
    def test_multiple_dependencies_join(self):
        """A question depending on multiple prerequisites is blocked until ALL prerequisites resolve."""
        decomp = ResearchDecomposition(
            decomposition_id=self.decomp_id,
            research_request_id=self.req_id,
            objective="Join dependency test",
            root_question="Join?",
        )
        q1 = self._make_sub_question("sq-1", "Prereq 1", priority=SubQuestionPriority.HIGH)
        q2 = self._make_sub_question("sq-2", "Prereq 2", priority=SubQuestionPriority.HIGH)
        q3 = self._make_sub_question("sq-3", "Prereq 3", priority=SubQuestionPriority.MEDIUM)
        q4 = self._make_sub_question("sq-4", "Join dependent", priority=SubQuestionPriority.CRITICAL, dependencies=["sq-1", "sq-2", "sq-3"])

        decomp.sub_questions = [q1, q2, q3, q4]

        plan = SubQuestionOrderPlanner.plan_order(decomp)

        # Q4 must be in Stage 1 and execute last
        self.assertEqual(plan.linear_execution_ids[-1], "sq-4")
        self.assertEqual(plan.stages[1].sub_question_ids, ["sq-4"])

        # Dynamic readiness verification
        self.assertFalse(plan.is_ready("sq-4", completed_ids=set()))
        self.assertEqual(plan.get_blocking_dependencies("sq-4", completed_ids=set()), ["sq-1", "sq-2", "sq-3"])

        # Partially resolved: still blocked
        self.assertFalse(plan.is_ready("sq-4", completed_ids={"sq-1", "sq-2"}))
        self.assertEqual(plan.get_blocking_dependencies("sq-4", completed_ids={"sq-1", "sq-2"}), ["sq-3"])

        # Fully resolved: ready
        self.assertTrue(plan.is_ready("sq-4", completed_ids={"sq-1", "sq-2", "sq-3"}))
        self.assertEqual(plan.get_blocking_dependencies("sq-4", completed_ids={"sq-1", "sq-2", "sq-3"}), [])

    # -------------------------------------------------------------------------
    # 6. Cycle Rejection
    # -------------------------------------------------------------------------
    def test_cycle_rejection_raises_error(self):
        """Directed cycles (both direct and multi-hop) must be rejected with DecompositionValidationError."""
        # 2-node cycle: Q1 -> Q2 -> Q1
        decomp_2 = ResearchDecomposition(
            decomposition_id=self.decomp_id,
            research_request_id=self.req_id,
            objective="2-cycle test",
            root_question="2-cycle?",
        )
        q1 = self._make_sub_question("sq-1", "Cycle node 1", dependencies=["sq-2"])
        q2 = self._make_sub_question("sq-2", "Cycle node 2", dependencies=["sq-1"])
        decomp_2.sub_questions = [q1, q2]

        with self.assertRaises(DecompositionValidationError) as ctx:
            SubQuestionOrderPlanner.plan_order(decomp_2)
        self.assertIn("Dependency cycle detected", str(ctx.exception))

        # 3-node cycle: Q1 -> Q2 -> Q3 -> Q1
        decomp_3 = ResearchDecomposition(
            decomposition_id=self.decomp_id,
            research_request_id=self.req_id,
            objective="3-cycle test",
            root_question="3-cycle?",
        )
        qa = self._make_sub_question("sq-a", "Node A", dependencies=["sq-c"])
        qb = self._make_sub_question("sq-b", "Node B", dependencies=["sq-a"])
        qc = self._make_sub_question("sq-c", "Node C", dependencies=["sq-b"])
        decomp_3.sub_questions = [qa, qb, qc]

        with self.assertRaises(DecompositionValidationError) as ctx3:
            SubQuestionOrderPlanner.plan_order(decomp_3)
        self.assertIn("Dependency cycle detected", str(ctx3.exception))

    # -------------------------------------------------------------------------
    # 7. Stable Deterministic Ordering
    # -------------------------------------------------------------------------
    def test_stable_deterministic_ordering(self):
        """Multiple runs on the exact same decomposition produce identical execution plans."""
        decomp = ResearchDecomposition(
            decomposition_id=self.decomp_id,
            research_request_id=self.req_id,
            objective="Determinism test",
            root_question="Determinism?",
        )
        decomp.sub_questions = [
            self._make_sub_question("sq-1", "Item 1", priority=SubQuestionPriority.MEDIUM),
            self._make_sub_question("sq-2", "Item 2", priority=SubQuestionPriority.HIGH),
            self._make_sub_question("sq-3", "Item 3", priority=SubQuestionPriority.HIGH, dependencies=["sq-1"]),
            self._make_sub_question("sq-4", "Item 4", priority=SubQuestionPriority.LOW, dependencies=["sq-2"]),
        ]

        plan_1 = SubQuestionOrderPlanner.plan_order(decomp)
        plan_2 = SubQuestionOrderPlanner.plan_order(decomp)

        self.assertEqual(plan_1.linear_execution_ids, plan_2.linear_execution_ids)
        self.assertEqual(
            [s.sub_question_ids for s in plan_1.stages],
            [s.sub_question_ids for s in plan_2.stages],
        )
        plan_2.created_at = plan_1.created_at
        self.assertEqual(plan_1.to_dict(), plan_2.to_dict())

    # -------------------------------------------------------------------------
    # 8. Prompt Example Ordering
    # -------------------------------------------------------------------------
    def test_prompt_example_ordering(self):
        """
        Verify the exact example specified in the prompt:
        Q1 — Identify candidates — HIGH
        Q2 — Compare React compatibility — HIGH, depends on Q1
        Q3 — Compare performance — MEDIUM, depends on Q1
        Q4 — Recommendation — HIGH, depends on Q1/Q2/Q3

        Valid ordering:
        Q1
        Q2/Q3 (Q2 before Q3 by priority)
        Q4
        """
        decomp = ResearchDecomposition(
            decomposition_id=self.decomp_id,
            research_request_id=self.req_id,
            objective="Compare GSAP and Motion for our Next.js portfolio",
            root_question="Which animation library is best?",
        )
        q1 = self._make_sub_question("Q1", "Identify candidates", priority=SubQuestionPriority.HIGH)
        q2 = self._make_sub_question("Q2", "Compare React compatibility", priority=SubQuestionPriority.HIGH, dependencies=["Q1"])
        q3 = self._make_sub_question("Q3", "Compare performance", priority=SubQuestionPriority.MEDIUM, dependencies=["Q1"])
        q4 = self._make_sub_question("Q4", "Recommendation", priority=SubQuestionPriority.HIGH, dependencies=["Q1", "Q2", "Q3"])

        decomp.sub_questions = [q1, q2, q3, q4]

        plan = SubQuestionOrderPlanner.plan_order(decomp)

        # Verify linear order: Q1 -> Q2 -> Q3 -> Q4
        self.assertEqual(plan.linear_execution_ids, ["Q1", "Q2", "Q3", "Q4"])

        # Verify stages:
        # Stage 0: Q1
        # Stage 1: Q2, Q3 (Q2 before Q3 because HIGH > MEDIUM)
        # Stage 2: Q4
        self.assertEqual(len(plan.stages), 3)
        self.assertEqual(plan.stages[0].sub_question_ids, ["Q1"])
        self.assertEqual(plan.stages[1].sub_question_ids, ["Q2", "Q3"])
        self.assertEqual(plan.stages[2].sub_question_ids, ["Q4"])

    # -------------------------------------------------------------------------
    # 9. Dynamic Readiness Queries
    # -------------------------------------------------------------------------
    def test_dynamic_readiness_queries(self):
        """Verify dynamic readiness query helpers as completed items progress."""
        decomp = ResearchDecomposition(
            decomposition_id=self.decomp_id,
            research_request_id=self.req_id,
            objective="Dynamic readiness test",
            root_question="Readiness?",
        )
        q1 = self._make_sub_question("sq-1", "Step 1", priority=SubQuestionPriority.HIGH)
        q2 = self._make_sub_question("sq-2", "Step 2", priority=SubQuestionPriority.HIGH, dependencies=["sq-1"])
        q3 = self._make_sub_question("sq-3", "Step 3", priority=SubQuestionPriority.MEDIUM, dependencies=["sq-1"])
        decomp.sub_questions = [q1, q2, q3]

        plan = SubQuestionOrderPlanner.plan_order(decomp)

        # Initial state: only sq-1 is ready
        ready_0 = plan.get_ready_questions()
        self.assertEqual([sq.sub_question_id for sq in ready_0], ["sq-1"])

        # After sq-1 completes: sq-2 and sq-3 become ready, sorted by priority (sq-2 is HIGH, sq-3 is MEDIUM)
        ready_1 = plan.get_ready_questions(completed_ids={"sq-1"})
        self.assertEqual([sq.sub_question_id for sq in ready_1], ["sq-2", "sq-3"])

        # After sq-1 and sq-2 complete: only sq-3 remains ready
        ready_2 = plan.get_ready_questions(completed_ids={"sq-1", "sq-2"})
        self.assertEqual([sq.sub_question_id for sq in ready_2], ["sq-3"])

        # After all complete: none ready
        ready_3 = plan.get_ready_questions(completed_ids={"sq-1", "sq-2", "sq-3"})
        self.assertEqual(ready_3, [])

    # -------------------------------------------------------------------------
    # 10. Model Integration via plan_execution_order()
    # -------------------------------------------------------------------------
    def test_model_plan_execution_order_helper(self):
        """Verify ResearchDecomposition.plan_execution_order() method works seamlessly."""
        decomp = ResearchDecomposition(
            decomposition_id=self.decomp_id,
            research_request_id=self.req_id,
            objective="Helper test",
            root_question="Helper?",
        )
        q1 = self._make_sub_question("sq-1", "Single question", priority=SubQuestionPriority.HIGH)
        decomp.sub_questions = [q1]

        plan = decomp.plan_execution_order()
        self.assertIsInstance(plan, DecompositionOrderPlan)
        self.assertEqual(plan.linear_execution_ids, ["sq-1"])

    # -------------------------------------------------------------------------
    # 11. Serialization Round-Trip
    # -------------------------------------------------------------------------
    def test_order_plan_serialization_roundtrip(self):
        """Verify to_dict() and from_dict() round-trip fidelity for DecompositionOrderPlan."""
        decomp = ResearchDecomposition(
            decomposition_id=self.decomp_id,
            research_request_id=self.req_id,
            objective="Serialization test",
            root_question="Serialization?",
        )
        q1 = self._make_sub_question("sq-1", "Question 1", priority=SubQuestionPriority.HIGH)
        q2 = self._make_sub_question("sq-2", "Question 2", priority=SubQuestionPriority.MEDIUM, dependencies=["sq-1"])
        decomp.sub_questions = [q1, q2]

        plan = SubQuestionOrderPlanner.plan_order(decomp)
        plan_dict = plan.to_dict()
        restored = DecompositionOrderPlan.from_dict(plan_dict)

        self.assertEqual(restored.decomposition_id, plan.decomposition_id)
        self.assertEqual(restored.linear_execution_ids, plan.linear_execution_ids)
        self.assertEqual(len(restored.stages), len(plan.stages))
        self.assertEqual(restored.stages[0].stage_index, 0)
        self.assertEqual(restored.stages[1].stage_index, 1)
        self.assertEqual(restored.readiness_map["sq-1"].is_ready, True)
        self.assertEqual(restored.readiness_map["sq-2"].is_ready, False)

    # -------------------------------------------------------------------------
    # 12. Empty Decomposition
    # -------------------------------------------------------------------------
    def test_empty_decomposition_plan_order(self):
        """Empty decomposition yields an empty plan gracefully."""
        decomp = ResearchDecomposition(
            decomposition_id=self.decomp_id,
            research_request_id=self.req_id,
            objective="Empty test",
            root_question="Empty?",
        )
        plan = SubQuestionOrderPlanner.plan_order(decomp)
        self.assertEqual(plan.linear_execution_ids, [])
        self.assertEqual(plan.stages, [])


if __name__ == "__main__":
    unittest.main()
