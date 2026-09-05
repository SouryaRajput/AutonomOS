from __future__ import annotations

import unittest

from core.research.contracts.intent import TemporalScope, VersionScope
from core.research.decomposition.model import (
    AcceptanceCriteria,
    ExpectedEvidence,
    ResearchDecomposition,
    ResearchDependency,
    ResearchScope,
    ResearchSubQuestion,
    SubQuestionProvenance,
    new_id,
)
from core.research.decomposition.types import (
    ResearchDependencyType,
    ResearchPriority,
    SubQuestionPriority,
    SubQuestionStatus,
    SubQuestionType,
)
from core.research.decomposition.validator import (
    DecompositionValidationError,
    DecompositionValidator,
)
from core.research.question.model import QuestionOption, ResearchQuestion
from core.research.types import SourceType


class TestResearchDecompositionModel(unittest.TestCase):
    """
    Comprehensive test suite for Phase 2 Step 2.3.1: Research Decomposition Model.
    Validates:
    1. Root decomposition creation, defaults, and trace logging.
    2. Sub-question parent-child hierarchy and depth calculation.
    3. Multi-level nested decomposition and bounded depth enforcement.
    4. Explicit dependencies and graph topology queries.
    5. Dependency cycle detection and rejection.
    6. Self-dependency rejection.
    7. Invalid parent and parent-child loop detection.
    8. Priority, status, and sub-question type classifications.
    9. ResearchScope, ExpectedEvidence, and AcceptanceCriteria.
    10. Provenance lineage linking to ResearchRequest and ResearchIntent.
    11. Guard preventing confusion between internal sub-questions and user-facing HITL questions.
    12. Serialization / deserialization round-trip fidelity.
    13. End-to-end realistic migration decomposition ("REST to GraphQL").
    """

    def setUp(self):
        self.decomp_id = "decomp-test-100"
        self.req_id = "req-test-100"
        self.intent_id = "intent-test-100"

    # -------------------------------------------------------------------------
    # 1. Root Decomposition Creation & Defaults
    # -------------------------------------------------------------------------
    def test_root_decomposition_creation_and_defaults(self):
        """Verify decomposition creation with default values and validation success."""
        decomp = ResearchDecomposition(
            decomposition_id=self.decomp_id,
            research_request_id=self.req_id,
            research_intent_id=self.intent_id,
            objective="Evaluate GraphQL migration suitability",
            root_question="Should we migrate our backend from REST to GraphQL?",
        )

        self.assertEqual(decomp.decomposition_id, self.decomp_id)
        self.assertEqual(decomp.research_request_id, self.req_id)
        self.assertEqual(decomp.decomposition_confidence, 1.0)
        self.assertEqual(decomp.version, 1)
        self.assertEqual(decomp.max_depth_limit, 4)
        self.assertEqual(len(decomp.sub_questions), 0)
        self.assertEqual(len(decomp.dependencies), 0)

        # Validation must succeed
        errors = DecompositionValidator.validate(decomp)
        self.assertEqual(errors, [])

    # -------------------------------------------------------------------------
    # 2. Child Sub-Questions & Parent Linkage
    # -------------------------------------------------------------------------
    def test_child_sub_questions_and_parent_linkage(self):
        """Verify root and child sub-questions properly reflect parent-child relationships."""
        decomp = ResearchDecomposition(
            decomposition_id=self.decomp_id,
            research_request_id=self.req_id,
            objective="Evaluate GraphQL migration",
            root_question="Should we migrate?",
        )

        root_sq = ResearchSubQuestion(
            sub_question_id="sq-root-1",
            decomposition_id=self.decomp_id,
            question="What is the current backend API architecture?",
            objective="Document current REST endpoints and consumers",
            sub_question_type=SubQuestionType.ARCHITECTURAL,
            priority=SubQuestionPriority.HIGH,
            decomposition_depth=1,
        )

        child_sq = ResearchSubQuestion(
            sub_question_id="sq-child-1",
            decomposition_id=self.decomp_id,
            parent_id="sq-root-1",
            question="Which endpoints suffer from over-fetching?",
            objective="Identify specific endpoints with over-fetching bottlenecks",
            sub_question_type=SubQuestionType.PERFORMANCE_ANALYSIS,
            priority=SubQuestionPriority.MEDIUM,
            decomposition_depth=2,
        )

        decomp.add_sub_question(root_sq)
        decomp.add_sub_question(child_sq)

        self.assertEqual(len(decomp.sub_questions), 2)
        self.assertTrue(root_sq.is_root())
        self.assertFalse(child_sq.is_root())

        roots = decomp.get_root_sub_questions()
        self.assertEqual(len(roots), 1)
        self.assertEqual(roots[0].sub_question_id, "sq-root-1")

        children = decomp.get_children("sq-root-1")
        self.assertEqual(len(children), 1)
        self.assertEqual(children[0].sub_question_id, "sq-child-1")

        errors = DecompositionValidator.validate(decomp)
        self.assertEqual(errors, [])

    # -------------------------------------------------------------------------
    # 3. Nested Decomposition & Hierarchical Depth
    # -------------------------------------------------------------------------
    def test_nested_decomposition_hierarchical_depths(self):
        """Verify hierarchical nesting across multiple levels (depth 1 -> 2 -> 3)."""
        decomp = ResearchDecomposition(
            decomposition_id=self.decomp_id,
            research_request_id=self.req_id,
            objective="Evaluate database scaling options",
            root_question="How should we scale?",
            max_depth_limit=4,
        )

        sq1 = ResearchSubQuestion(
            sub_question_id="sq-1",
            decomposition_id=self.decomp_id,
            parent_id=None,
            question="What are our primary scaling bottlenecks?",
            objective="Identify bottlenecks",
            decomposition_depth=1,
        )
        sq2 = ResearchSubQuestion(
            sub_question_id="sq-2",
            decomposition_id=self.decomp_id,
            parent_id="sq-1",
            question="Is write throughput or read throughput the primary constraint?",
            objective="Analyze throughput profiles",
            decomposition_depth=2,
        )
        sq3 = ResearchSubQuestion(
            sub_question_id="sq-3",
            decomposition_id=self.decomp_id,
            parent_id="sq-2",
            question="What is the p99 write latency on the ingestion shard?",
            objective="Measure p99 write latency",
            decomposition_depth=3,
        )

        decomp.add_sub_question(sq1)
        decomp.add_sub_question(sq2)
        decomp.add_sub_question(sq3)

        self.assertEqual(decomp.max_depth(), 3)
        self.assertEqual(len(decomp.get_children("sq-1")), 1)
        self.assertEqual(len(decomp.get_children("sq-2")), 1)
        self.assertEqual(len(decomp.get_children("sq-3")), 0)

        errors = DecompositionValidator.validate(decomp)
        self.assertEqual(errors, [])

    # -------------------------------------------------------------------------
    # 4. Max Depth Limit Enforcement
    # -------------------------------------------------------------------------
    def test_max_depth_limit_enforcement(self):
        """Verify that sub-questions exceeding max_depth_limit are rejected."""
        decomp = ResearchDecomposition(
            decomposition_id=self.decomp_id,
            research_request_id=self.req_id,
            objective="Investigate",
            root_question="Root?",
            max_depth_limit=2,  # Max depth capped at 2
        )

        sq1 = ResearchSubQuestion(
            sub_question_id="sq-1",
            decomposition_id=self.decomp_id,
            question="Question 1",
            objective="Objective 1",
            decomposition_depth=1,
        )
        sq2 = ResearchSubQuestion(
            sub_question_id="sq-2",
            decomposition_id=self.decomp_id,
            parent_id="sq-1",
            question="Question 2",
            objective="Objective 2",
            decomposition_depth=2,
        )
        sq3 = ResearchSubQuestion(
            sub_question_id="sq-3",
            decomposition_id=self.decomp_id,
            parent_id="sq-2",
            question="Question 3",
            objective="Objective 3",
            decomposition_depth=3,  # Exceeds limit of 2
        )

        decomp.add_sub_question(sq1)
        decomp.add_sub_question(sq2)
        decomp.add_sub_question(sq3)

        with self.assertRaises(DecompositionValidationError) as ctx:
            DecompositionValidator.validate(decomp)
        self.assertIn("exceeds max_depth_limit", str(ctx.exception))

    # -------------------------------------------------------------------------
    # 5. Invalid Parent Rejection
    # -------------------------------------------------------------------------
    def test_invalid_parent_rejection(self):
        """Verify rejection of non-existent parent_id or self as parent."""
        decomp = ResearchDecomposition(
            decomposition_id=self.decomp_id,
            research_request_id=self.req_id,
            objective="Investigate",
            root_question="Root?",
        )

        # 1. Non-existent parent
        sq_bad_parent = ResearchSubQuestion(
            sub_question_id="sq-bad",
            decomposition_id=self.decomp_id,
            parent_id="sq-does-not-exist",
            question="Question",
            objective="Objective",
            decomposition_depth=2,
        )
        decomp.add_sub_question(sq_bad_parent)

        with self.assertRaises(DecompositionValidationError) as ctx:
            DecompositionValidator.validate(decomp)
        self.assertIn("references non-existent parent_id", str(ctx.exception))

        # 2. Self as parent
        decomp.sub_questions.clear()
        sq_self_parent = ResearchSubQuestion(
            sub_question_id="sq-self",
            decomposition_id=self.decomp_id,
            parent_id="sq-self",
            question="Question",
            objective="Objective",
            decomposition_depth=2,
        )
        decomp.add_sub_question(sq_self_parent)

        with self.assertRaises(DecompositionValidationError) as ctx:
            DecompositionValidator.validate(decomp)
        self.assertIn("cannot be its own parent", str(ctx.exception))

    # -------------------------------------------------------------------------
    # 6. Parent-Child Cycle Detection
    # -------------------------------------------------------------------------
    def test_parent_child_cycle_detection(self):
        """Verify detection and rejection of circular parent-child loops (A -> B -> A)."""
        decomp = ResearchDecomposition(
            decomposition_id=self.decomp_id,
            research_request_id=self.req_id,
            objective="Investigate",
            root_question="Root?",
        )

        sq_a = ResearchSubQuestion(
            sub_question_id="sq-a",
            decomposition_id=self.decomp_id,
            parent_id="sq-b",
            question="Question A",
            objective="Objective A",
            decomposition_depth=2,
        )
        sq_b = ResearchSubQuestion(
            sub_question_id="sq-b",
            decomposition_id=self.decomp_id,
            parent_id="sq-a",
            question="Question B",
            objective="Objective B",
            decomposition_depth=2,
        )
        decomp.add_sub_question(sq_a)
        decomp.add_sub_question(sq_b)

        with self.assertRaises(DecompositionValidationError) as ctx:
            DecompositionValidator.validate(decomp)
        self.assertIn("Circular parent-child hierarchy detected", str(ctx.exception))

    # -------------------------------------------------------------------------
    # 7. Depth Mismatch Rejection
    # -------------------------------------------------------------------------
    def test_depth_mismatch_rejection(self):
        """Verify sub-question depth must strictly equal parent.depth + 1 (or 1 for root)."""
        decomp = ResearchDecomposition(
            decomposition_id=self.decomp_id,
            research_request_id=self.req_id,
            objective="Investigate",
            root_question="Root?",
        )

        sq1 = ResearchSubQuestion(
            sub_question_id="sq-1",
            decomposition_id=self.decomp_id,
            parent_id=None,
            question="Question 1",
            objective="Objective 1",
            decomposition_depth=2,  # Root must be depth 1!
        )
        decomp.add_sub_question(sq1)

        with self.assertRaises(DecompositionValidationError) as ctx:
            DecompositionValidator.validate(decomp)
        self.assertIn("Root sub-question 'sq-1' must have decomposition_depth = 1", str(ctx.exception))

        # Fix root, break child
        sq1.decomposition_depth = 1
        sq2 = ResearchSubQuestion(
            sub_question_id="sq-2",
            decomposition_id=self.decomp_id,
            parent_id="sq-1",
            question="Question 2",
            objective="Objective 2",
            decomposition_depth=3,  # Expected 2, but set to 3!
        )
        decomp.add_sub_question(sq2)

        with self.assertRaises(DecompositionValidationError) as ctx:
            DecompositionValidator.validate(decomp)
        self.assertIn("does not match hierarchy", str(ctx.exception))

    # -------------------------------------------------------------------------
    # 8. Dependency References & Topology Lookups
    # -------------------------------------------------------------------------
    def test_dependency_references_and_lookups(self):
        """Verify explicit dependencies between sub-questions and helper lookups."""
        decomp = ResearchDecomposition(
            decomposition_id=self.decomp_id,
            research_request_id=self.req_id,
            objective="Investigate",
            root_question="Root?",
        )

        sq1 = ResearchSubQuestion(
            sub_question_id="sq-1",
            decomposition_id=self.decomp_id,
            question="Analyze REST API",
            objective="Document endpoints",
        )
        sq2 = ResearchSubQuestion(
            sub_question_id="sq-2",
            decomposition_id=self.decomp_id,
            question="Evaluate GraphQL Migration",
            objective="Plan migration",
        )
        decomp.add_sub_question(sq1)
        decomp.add_sub_question(sq2)

        dep = ResearchDependency(
            dependency_id="dep-1",
            prerequisite_id="sq-1",
            dependent_id="sq-2",
            dependency_type=ResearchDependencyType.PREREQUISITE,
            rationale="Cannot plan migration without understanding current REST endpoints.",
        )
        decomp.add_dependency(dep)

        self.assertEqual(len(decomp.dependencies), 1)
        self.assertIn("sq-1", sq2.dependencies)

        prereqs = decomp.get_prerequisites("sq-2")
        self.assertEqual(len(prereqs), 1)
        self.assertEqual(prereqs[0].sub_question_id, "sq-1")

        dependents = decomp.get_dependents("sq-1")
        self.assertEqual(len(dependents), 1)
        self.assertEqual(dependents[0].sub_question_id, "sq-2")

        deps_for_2 = decomp.get_dependencies_for("sq-2")
        self.assertEqual(len(deps_for_2), 1)
        self.assertEqual(deps_for_2[0].prerequisite_id, "sq-1")

        errors = DecompositionValidator.validate(decomp)
        self.assertEqual(errors, [])

    # -------------------------------------------------------------------------
    # 9. Dependency Cycle Detection
    # -------------------------------------------------------------------------
    def test_dependency_cycle_detection(self):
        """Verify directed cycle detection (Q1 -> Q2 -> Q3 -> Q1)."""
        decomp = ResearchDecomposition(
            decomposition_id=self.decomp_id,
            research_request_id=self.req_id,
            objective="Investigate",
            root_question="Root?",
        )

        sq1 = ResearchSubQuestion(sub_question_id="sq-1", decomposition_id=self.decomp_id, question="Q1", objective="Obj1")
        sq2 = ResearchSubQuestion(sub_question_id="sq-2", decomposition_id=self.decomp_id, question="Q2", objective="Obj2")
        sq3 = ResearchSubQuestion(sub_question_id="sq-3", decomposition_id=self.decomp_id, question="Q3", objective="Obj3")

        decomp.add_sub_question(sq1)
        decomp.add_sub_question(sq2)
        decomp.add_sub_question(sq3)

        # Create cycle: sq-2 depends on sq-1, sq-3 depends on sq-2, sq-1 depends on sq-3
        decomp.add_dependency(ResearchDependency(dependency_id="d1", prerequisite_id="sq-1", dependent_id="sq-2"))
        decomp.add_dependency(ResearchDependency(dependency_id="d2", prerequisite_id="sq-2", dependent_id="sq-3"))
        decomp.add_dependency(ResearchDependency(dependency_id="d3", prerequisite_id="sq-3", dependent_id="sq-1"))

        with self.assertRaises(DecompositionValidationError) as ctx:
            DecompositionValidator.validate(decomp)
        self.assertIn("Dependency cycle detected", str(ctx.exception))

    # -------------------------------------------------------------------------
    # 10. Self-Dependency Rejection
    # -------------------------------------------------------------------------
    def test_self_dependency_rejection(self):
        """Verify self-dependency is strictly rejected."""
        decomp = ResearchDecomposition(
            decomposition_id=self.decomp_id,
            research_request_id=self.req_id,
            objective="Investigate",
            root_question="Root?",
        )

        sq1 = ResearchSubQuestion(sub_question_id="sq-1", decomposition_id=self.decomp_id, question="Q1", objective="Obj1")
        decomp.add_sub_question(sq1)

        decomp.add_dependency(ResearchDependency(
            dependency_id="dep-self",
            prerequisite_id="sq-1",
            dependent_id="sq-1",
        ))

        with self.assertRaises(DecompositionValidationError) as ctx:
            DecompositionValidator.validate(decomp)
        self.assertIn("defines self-dependency", str(ctx.exception))

    # -------------------------------------------------------------------------
    # 11. Unresolved Dependency Reference Rejection
    # -------------------------------------------------------------------------
    def test_unresolved_dependency_reference_rejection(self):
        """Verify dependencies referencing non-existent sub-questions fail validation."""
        decomp = ResearchDecomposition(
            decomposition_id=self.decomp_id,
            research_request_id=self.req_id,
            objective="Investigate",
            root_question="Root?",
        )

        sq1 = ResearchSubQuestion(sub_question_id="sq-1", decomposition_id=self.decomp_id, question="Q1", objective="Obj1")
        decomp.add_sub_question(sq1)

        decomp.add_dependency(ResearchDependency(
            dependency_id="dep-invalid",
            prerequisite_id="sq-1",
            dependent_id="sq-ghost",  # Does not exist
        ))

        with self.assertRaises(DecompositionValidationError) as ctx:
            DecompositionValidator.validate(decomp)
        self.assertIn("references non-existent dependent_id", str(ctx.exception))

    # -------------------------------------------------------------------------
    # 12. Duplicate Sub-Question and Dependency IDs
    # -------------------------------------------------------------------------
    def test_duplicate_ids_rejection(self):
        """Verify duplicate sub_question_id or dependency_id is rejected."""
        decomp = ResearchDecomposition(
            decomposition_id=self.decomp_id,
            research_request_id=self.req_id,
            objective="Investigate",
            root_question="Root?",
        )

        sq1 = ResearchSubQuestion(sub_question_id="dup-id", decomposition_id=self.decomp_id, question="Q1", objective="Obj1")
        sq2 = ResearchSubQuestion(sub_question_id="dup-id", decomposition_id=self.decomp_id, question="Q2", objective="Obj2")
        decomp.sub_questions.append(sq1)
        decomp.sub_questions.append(sq2)

        with self.assertRaises(DecompositionValidationError) as ctx:
            DecompositionValidator.validate(decomp)
        self.assertIn("Duplicate sub_question_id detected: 'dup-id'", str(ctx.exception))

    # -------------------------------------------------------------------------
    # 13. Empty Objective or Question Text Rejection
    # -------------------------------------------------------------------------
    def test_empty_objective_or_question_rejection(self):
        """Verify empty objectives or questions fail validation."""
        decomp = ResearchDecomposition(
            decomposition_id=self.decomp_id,
            research_request_id=self.req_id,
            objective="",  # Empty decomposition objective
            root_question="Root?",
        )

        with self.assertRaises(DecompositionValidationError) as ctx:
            DecompositionValidator.validate(decomp)
        self.assertIn("Decomposition objective must not be empty", str(ctx.exception))

        decomp.objective = "Valid Objective"
        sq_empty = ResearchSubQuestion(
            sub_question_id="sq-empty",
            decomposition_id=self.decomp_id,
            question="",
            objective="   ",
        )
        decomp.add_sub_question(sq_empty)

        with self.assertRaises(DecompositionValidationError) as ctx:
            DecompositionValidator.validate(decomp)
        err_msg = str(ctx.exception)
        self.assertIn("has an empty question", err_msg)
        self.assertIn("has an empty objective", err_msg)

    # -------------------------------------------------------------------------
    # 14. Guard Preventing Confusion with User-Facing ResearchQuestion
    # -------------------------------------------------------------------------
    def test_user_facing_question_confusion_guard(self):
        """Verify that passing user-facing ResearchQuestion into decomposition fails validation."""
        decomp = ResearchDecomposition(
            decomposition_id=self.decomp_id,
            research_request_id=self.req_id,
            objective="Investigate",
            root_question="Root?",
        )

        user_facing_q = ResearchQuestion(
            question_id="rq-dec-1",
            research_request_id=self.req_id,
            question="Which database engine do you prefer?",
            options=[QuestionOption(option_id="opt-1", label="PostgreSQL")],
            custom_answer_allowed=True,
        )

        # Mistakenly added to decomposition sub_questions
        decomp.sub_questions.append(user_facing_q)  # type: ignore

        with self.assertRaises(DecompositionValidationError) as ctx:
            DecompositionValidator.validate(decomp)
        self.assertIn("user-facing ResearchQuestion", str(ctx.exception))
        self.assertIn("ResearchSubQuestion must be used for internal research planning", str(ctx.exception))

    # -------------------------------------------------------------------------
    # 15. Scope Model Fidelity & Serialization
    # -------------------------------------------------------------------------
    def test_scope_model_fidelity_and_serialization(self):
        """Verify ResearchScope serialization round-trip with TemporalScope and VersionScope."""
        scope = ResearchScope(
            entities=["PostgreSQL", "MongoDB"],
            domains=["postgresql.org", "mongodb.com"],
            geographic_scope="US",
            temporal_scope=TemporalScope(recency_days=30, description="Last 30 days releases"),
            version_scope=VersionScope(target_version="16.2", ecosystem="Python"),
            project_scope=["analytics-service", "data-pipeline"],
            source_expectations=[SourceType.OFFICIAL_DOCUMENTATION, SourceType.REPOSITORY],
            exclusions=["Cassandra", "DynamoDB"],
            constraints=["Must be deployable on Kubernetes"],
        )

        data = scope.to_dict()
        restored = ResearchScope.from_dict(data)

        self.assertEqual(restored.entities, ["PostgreSQL", "MongoDB"])
        self.assertEqual(restored.domains, ["postgresql.org", "mongodb.com"])
        self.assertIsNotNone(restored.temporal_scope)
        self.assertEqual(restored.temporal_scope.recency_days, 30)
        self.assertIsNotNone(restored.version_scope)
        self.assertEqual(restored.version_scope.target_version, "16.2")
        self.assertEqual(restored.source_expectations, [SourceType.OFFICIAL_DOCUMENTATION, SourceType.REPOSITORY])
        self.assertEqual(restored.exclusions, ["Cassandra", "DynamoDB"])

    # -------------------------------------------------------------------------
    # 16. Expected Evidence & Acceptance Criteria
    # -------------------------------------------------------------------------
    def test_expected_evidence_and_acceptance_criteria(self):
        """Verify ExpectedEvidence and AcceptanceCriteria serialization round-trips."""
        exp_ev = ExpectedEvidence(
            expectation_id="exp-1",
            description="Official benchmark results showing p99 latency",
            source_types=[SourceType.PRIMARY_SOURCE, SourceType.ACADEMIC],
            min_independent_sources=2,
            mandatory=True,
            confidence_requirement=0.85,
        )
        ev_dict = exp_ev.to_dict()
        restored_ev = ExpectedEvidence.from_dict(ev_dict)
        self.assertEqual(restored_ev.expectation_id, "exp-1")
        self.assertEqual(restored_ev.min_independent_sources, 2)
        self.assertEqual(restored_ev.confidence_requirement, 0.85)

        ac = AcceptanceCriteria(
            criteria_id="ac-1",
            description="Both PostgreSQL and MongoDB evaluated along write latency dimension",
            mandatory=True,
            verification_aspect="coverage",
            notes="Requires benchmark data under identical hardware specs",
        )
        ac_dict = ac.to_dict()
        restored_ac = AcceptanceCriteria.from_dict(ac_dict)
        self.assertEqual(restored_ac.criteria_id, "ac-1")
        self.assertEqual(restored_ac.verification_aspect, "coverage")
        self.assertTrue(restored_ac.mandatory)

    # -------------------------------------------------------------------------
    # 17. Provenance & Request Lineage
    # -------------------------------------------------------------------------
    def test_provenance_and_request_lineage(self):
        """Verify SubQuestionProvenance tracks research request ID and flags mismatch."""
        decomp = ResearchDecomposition(
            decomposition_id=self.decomp_id,
            research_request_id=self.req_id,
            objective="Investigate",
            root_question="Root?",
        )

        sq = ResearchSubQuestion(
            sub_question_id="sq-1",
            decomposition_id=self.decomp_id,
            question="Question 1",
            objective="Objective 1",
            provenance=SubQuestionProvenance(
                research_request_id="req-mismatched-999",  # Does not match decomp
                decomposition_id=self.decomp_id,
            ),
        )
        decomp.add_sub_question(sq)

        with self.assertRaises(DecompositionValidationError) as ctx:
            DecompositionValidator.validate(decomp)
        self.assertIn("provenance request ID", str(ctx.exception))

    # -------------------------------------------------------------------------
    # 18. Full Decomposition Serialization Round-Trip
    # -------------------------------------------------------------------------
    def test_full_decomposition_serialization_roundtrip(self):
        """Verify complete ResearchDecomposition to_dict and from_dict round-trip."""
        decomp = ResearchDecomposition(
            decomposition_id=self.decomp_id,
            research_request_id=self.req_id,
            research_intent_id=self.intent_id,
            objective="Evaluate relational vs document store",
            root_question="Which database best fits time-series workloads?",
            unresolved_decisions=["dec-scale-target"],
            coverage_requirements=["write throughput", "storage compression"],
            decomposition_confidence=0.95,
        )

        sq1 = ResearchSubQuestion(
            sub_question_id="sq-1",
            decomposition_id=self.decomp_id,
            question="What is the write throughput profile?",
            objective="Measure throughput",
            sub_question_type=SubQuestionType.PERFORMANCE_ANALYSIS,
            priority=SubQuestionPriority.HIGH,
            status=SubQuestionStatus.READY,
            expected_evidence=[
                ExpectedEvidence(description="Benchmark documentation", source_types=[SourceType.OFFICIAL_DOCUMENTATION])
            ],
            acceptance_criteria=[
                AcceptanceCriteria(description="Throughput in writes/sec verified")
            ],
        )
        decomp.add_sub_question(sq1)

        decomp.add_dependency(ResearchDependency(
            dependency_id="dep-1",
            prerequisite_id="sq-1",
            dependent_id="sq-1",  # just setting data
        ))
        decomp.dependencies.clear()  # keep valid

        data = decomp.to_dict()
        restored = ResearchDecomposition.from_dict(data)

        self.assertEqual(restored.decomposition_id, self.decomp_id)
        self.assertEqual(restored.research_request_id, self.req_id)
        self.assertEqual(restored.research_intent_id, self.intent_id)
        self.assertEqual(restored.objective, "Evaluate relational vs document store")
        self.assertEqual(restored.decomposition_confidence, 0.95)
        self.assertEqual(len(restored.sub_questions), 1)
        self.assertEqual(restored.sub_questions[0].sub_question_type, SubQuestionType.PERFORMANCE_ANALYSIS)
        self.assertEqual(restored.sub_questions[0].priority, SubQuestionPriority.HIGH)
        self.assertEqual(restored.sub_questions[0].status, SubQuestionStatus.READY)
        self.assertEqual(len(restored.sub_questions[0].expected_evidence), 1)
        self.assertEqual(len(restored.sub_questions[0].acceptance_criteria), 1)

    # -------------------------------------------------------------------------
    # 19. Realistic Multi-Question Decomposition: "REST to GraphQL"
    # -------------------------------------------------------------------------
    def test_realistic_rest_to_graphql_decomposition(self):
        """
        Verify an end-to-end realistic research decomposition based on prompt specification:
        'Should we migrate our backend from REST to GraphQL?'
        Decomposes into 7 interconnected analytical units with prerequisites and hierarchy.
        """
        decomp = ResearchDecomposition(
            decomposition_id="decomp-graphql-mig",
            research_request_id="req-mig-001",
            research_intent_id="intent-mig-001",
            objective="Evaluate architectural, performance, and risk tradeoffs of migrating backend to GraphQL",
            root_question="Should we migrate our backend from REST to GraphQL?",
            coverage_requirements=[
                "current_architecture",
                "graphql_suitability",
                "performance_tradeoffs",
                "migration_complexity",
                "operational_security",
            ],
            decomposition_confidence=0.92,
        )

        # 1. Understand current backend architecture
        sq1 = ResearchSubQuestion(
            sub_question_id="sq-arch",
            decomposition_id=decomp.decomposition_id,
            parent_id=None,
            question="What is the current backend architecture and API design?",
            objective="Map existing REST services, client types, and data sources",
            sub_question_type=SubQuestionType.ARCHITECTURAL,
            priority=SubQuestionPriority.HIGH,
            decomposition_depth=1,
            scope=ResearchScope(entities=["REST Services", "Microservices"]),
            expected_evidence=[
                ExpectedEvidence(description="Service architecture diagrams and schema repos", source_types=[SourceType.REPOSITORY])
            ],
            acceptance_criteria=[
                AcceptanceCriteria(description="All client consumer types cataloged")
            ],
        )

        # 2. Determine current REST usage
        sq2 = ResearchSubQuestion(
            sub_question_id="sq-rest-usage",
            decomposition_id=decomp.decomposition_id,
            parent_id="sq-arch",
            question="What are the current REST usage patterns and pain points?",
            objective="Identify over-fetching, under-fetching, and multiple round-trips in existing endpoints",
            sub_question_type=SubQuestionType.FACT_FINDING,
            priority=SubQuestionPriority.HIGH,
            decomposition_depth=2,
            dependencies=["sq-arch"],
        )

        # 3. Evaluate GraphQL suitability
        sq3 = ResearchSubQuestion(
            sub_question_id="sq-suitability",
            decomposition_id=decomp.decomposition_id,
            parent_id=None,
            question="How suitable is GraphQL for our frontend consumption requirements?",
            objective="Determine if client apps benefit from flexible field selection and schema stitching",
            sub_question_type=SubQuestionType.EVALUATION,
            priority=SubQuestionPriority.CRITICAL,
            decomposition_depth=1,
            dependencies=["sq-rest-usage"],
        )

        # 4. Compare performance implications
        sq4 = ResearchSubQuestion(
            sub_question_id="sq-perf",
            decomposition_id=decomp.decomposition_id,
            parent_id="sq-suitability",
            question="What are the performance and caching implications of GraphQL vs HTTP REST caching?",
            objective="Evaluate CDN caching difficulty, query parsing overhead, and N+1 query problem",
            sub_question_type=SubQuestionType.PERFORMANCE_ANALYSIS,
            priority=SubQuestionPriority.HIGH,
            decomposition_depth=2,
            dependencies=["sq-suitability"],
            expected_evidence=[
                ExpectedEvidence(
                    description="Verified benchmark comparisons of Apollo Server vs REST gateway",
                    source_types=[SourceType.OFFICIAL_DOCUMENTATION, SourceType.BENCHMARK if hasattr(SourceType, "BENCHMARK") else SourceType.PRIMARY_SOURCE],
                    min_independent_sources=2,
                )
            ],
        )

        # 5. Evaluate migration complexity
        sq5 = ResearchSubQuestion(
            sub_question_id="sq-migration-cost",
            decomposition_id=decomp.decomposition_id,
            parent_id="sq-suitability",
            question="What is the engineering effort and schema migration complexity?",
            objective="Estimate schema design, resolver development, backward compatibility, and client SDK updates",
            sub_question_type=SubQuestionType.COST_ANALYSIS,
            priority=SubQuestionPriority.HIGH,
            decomposition_depth=2,
            dependencies=["sq-rest-usage", "sq-suitability"],
        )

        # 6. Evaluate security / operational implications
        sq6 = ResearchSubQuestion(
            sub_question_id="sq-security",
            decomposition_id=decomp.decomposition_id,
            parent_id=None,
            question="What are the security and operational risks introduced by GraphQL?",
            objective="Analyze query depth limiting, cost analysis, authorization at resolver level, and DDoS risks",
            sub_question_type=SubQuestionType.SECURITY_ANALYSIS,
            priority=SubQuestionPriority.CRITICAL,
            decomposition_depth=1,
            dependencies=["sq-suitability"],
        )

        # 7. Produce final recommendation
        sq7 = ResearchSubQuestion(
            sub_question_id="sq-synthesis",
            decomposition_id=decomp.decomposition_id,
            parent_id=None,
            question="What is the definitive migration recommendation and rollout roadmap?",
            objective="Synthesize findings into an evidence-backed go/no-go recommendation with phased plan",
            sub_question_type=SubQuestionType.SYNTHESIS,
            priority=SubQuestionPriority.CRITICAL,
            decomposition_depth=1,
            dependencies=["sq-perf", "sq-migration-cost", "sq-security"],
        )

        # Register sub-questions
        for sq in [sq1, sq2, sq3, sq4, sq5, sq6, sq7]:
            decomp.add_sub_question(sq)

        # Register explicit dependencies
        decomp.add_dependency(ResearchDependency(dependency_id="dep-1", prerequisite_id="sq-arch", dependent_id="sq-rest-usage", rationale="Need architecture map before analyzing usage"))
        decomp.add_dependency(ResearchDependency(dependency_id="dep-2", prerequisite_id="sq-rest-usage", dependent_id="sq-suitability", rationale="Need REST pain points before evaluating GraphQL fit"))
        decomp.add_dependency(ResearchDependency(dependency_id="dep-3", prerequisite_id="sq-suitability", dependent_id="sq-perf", rationale="Evaluate performance for chosen GraphQL model"))
        decomp.add_dependency(ResearchDependency(dependency_id="dep-4", prerequisite_id="sq-rest-usage", dependent_id="sq-migration-cost", rationale="Migration cost depends on endpoint count"))
        decomp.add_dependency(ResearchDependency(dependency_id="dep-5", prerequisite_id="sq-suitability", dependent_id="sq-migration-cost", rationale="Migration scope depends on GraphQL design"))
        decomp.add_dependency(ResearchDependency(dependency_id="dep-6", prerequisite_id="sq-suitability", dependent_id="sq-security", rationale="Security analysis depends on GraphQL architecture"))
        decomp.add_dependency(ResearchDependency(dependency_id="dep-7", prerequisite_id="sq-perf", dependent_id="sq-synthesis", rationale="Synthesis requires performance findings"))
        decomp.add_dependency(ResearchDependency(dependency_id="dep-8", prerequisite_id="sq-migration-cost", dependent_id="sq-synthesis", rationale="Synthesis requires effort estimate"))
        decomp.add_dependency(ResearchDependency(dependency_id="dep-9", prerequisite_id="sq-security", dependent_id="sq-synthesis", rationale="Synthesis requires risk assessment"))

        # Verify complete graph properties
        self.assertEqual(len(decomp.sub_questions), 7)
        self.assertEqual(len(decomp.dependencies), 9)
        self.assertEqual(decomp.max_depth(), 2)

        # Root sub-questions (depth 1)
        root_questions = decomp.get_root_sub_questions()
        self.assertEqual(len(root_questions), 4)  # sq-arch, sq-suitability, sq-security, sq-synthesis

        # Children of sq-arch
        arch_children = decomp.get_children("sq-arch")
        self.assertEqual(len(arch_children), 1)
        self.assertEqual(arch_children[0].sub_question_id, "sq-rest-usage")

        # Prerequisites for synthesis
        synthesis_prereqs = decomp.get_prerequisites("sq-synthesis")
        synthesis_prereq_ids = {p.sub_question_id for p in synthesis_prereqs}
        self.assertEqual(synthesis_prereq_ids, {"sq-perf", "sq-migration-cost", "sq-security"})

        # Dependents of suitability
        suit_dependents = decomp.get_dependents("sq-suitability")
        suit_dep_ids = {d.sub_question_id for d in suit_dependents}
        self.assertEqual(suit_dep_ids, {"sq-perf", "sq-migration-cost", "sq-security"})

        # Full validation
        errors = DecompositionValidator.validate(decomp)
        self.assertEqual(errors, [])


if __name__ == "__main__":
    unittest.main()
