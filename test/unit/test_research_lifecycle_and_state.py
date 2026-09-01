from __future__ import annotations

import unittest

from core.research.contracts.request import ResearchRequest, ResearchScope
from core.research.errors import InvalidStateTransitionError
from core.research.state.lifecycle import ResearchStateMachine
from core.research.state.model import ResearchState
from core.research.types import (
    CrawlerCapability,
    EvidenceSufficiency,
    ResearchLifecycleState,
    ResearchMode,
    ResearchQuestionStatus,
)


class TestResearchLifecycleAndState(unittest.TestCase):
    """Unit tests for ResearchLifecycleState, ResearchStateMachine, and ResearchState operational container."""

    def setUp(self):
        self.request = ResearchRequest(
            request_id="req-test-01",
            project_id="proj-01",
            task_id="task-01",
            objective="Evaluate distributed consensus algorithms",
            mode=ResearchMode.STANDARD,
            questions=["What is Raft consensus?", "How does Paxos compare to Raft?"],
            scope=ResearchScope(max_crawlers=3, min_evidence_per_question=2),
        )
        self.state = ResearchState(request=self.request)

    def test_golden_path_lifecycle_transitions(self):
        """Verify the full 13-stage deterministic golden path transitions."""
        expected_sequence = [
            ResearchLifecycleState.UNDERSTANDING,
            ResearchLifecycleState.PLANNING,
            ResearchLifecycleState.CRAWLER_ALLOCATION,
            ResearchLifecycleState.CRAWLERS_RUNNING,
            ResearchLifecycleState.EVIDENCE_COLLECTION,
            ResearchLifecycleState.EVIDENCE_EVALUATION,
            ResearchLifecycleState.COVERAGE_CHECK,
            ResearchLifecycleState.CONTRADICTION_CHECK,
            ResearchLifecycleState.SYNTHESIS,
            ResearchLifecycleState.RESEARCH_VERIFIED,
            ResearchLifecycleState.RESULT_DELIVERED,
            ResearchLifecycleState.COMPLETE,
        ]

        self.assertEqual(self.state.current_state, ResearchLifecycleState.RESEARCH_REQUESTED)

        for next_state in expected_sequence:
            self.state.transition_to(next_state, reason=f"Advancing to {next_state.value}")
            self.assertEqual(self.state.current_state, next_state)

        self.assertTrue(self.state.is_terminal())
        self.assertEqual(len(self.state.state_history), 12)
        self.assertEqual(self.state.state_history[-1].to_state, ResearchLifecycleState.COMPLETE.value)

    def test_invalid_lifecycle_transition_raises_error(self):
        """Verify that skipping lifecycle states or invalid jumps raise InvalidStateTransitionError."""
        self.assertEqual(self.state.current_state, ResearchLifecycleState.RESEARCH_REQUESTED)
        
        # Jumping directly from RESEARCH_REQUESTED to SYNTHESIS or COMPLETE must fail
        with self.assertRaises(InvalidStateTransitionError) as ctx:
            self.state.transition_to(ResearchLifecycleState.SYNTHESIS)
        self.assertEqual(ctx.exception.current_state, ResearchLifecycleState.RESEARCH_REQUESTED.value)
        self.assertEqual(ctx.exception.target_state, ResearchLifecycleState.SYNTHESIS.value)

        # Transition to UNDERSTANDING is valid
        self.state.transition_to(ResearchLifecycleState.UNDERSTANDING)
        
        # Jumping from UNDERSTANDING to EVIDENCE_COLLECTION must fail
        with self.assertRaises(InvalidStateTransitionError):
            self.state.transition_to(ResearchLifecycleState.EVIDENCE_COLLECTION)

    def test_insufficient_evidence_recovery_branch(self):
        """Verify the lifecycle branch when coverage check discovers insufficient evidence."""
        self.state.transition_to(ResearchLifecycleState.UNDERSTANDING)
        self.state.transition_to(ResearchLifecycleState.PLANNING)
        self.state.transition_to(ResearchLifecycleState.CRAWLER_ALLOCATION)
        self.state.transition_to(ResearchLifecycleState.CRAWLERS_RUNNING)
        self.state.transition_to(ResearchLifecycleState.EVIDENCE_COLLECTION)
        self.state.transition_to(ResearchLifecycleState.EVIDENCE_EVALUATION)
        self.state.transition_to(ResearchLifecycleState.COVERAGE_CHECK)

        # Branch to RETRYING_CRAWLERS
        self.state.transition_to(ResearchLifecycleState.RETRYING_CRAWLERS, reason="Coverage check failed")
        self.assertEqual(self.state.current_state, ResearchLifecycleState.RETRYING_CRAWLERS)

        # From RETRYING_CRAWLERS, can proceed to INSUFFICIENT_EVIDENCE or CRAWLER_ALLOCATION
        self.state.transition_to(ResearchLifecycleState.INSUFFICIENT_EVIDENCE, reason="Retries exhausted")
        self.assertEqual(self.state.current_state, ResearchLifecycleState.INSUFFICIENT_EVIDENCE)

        # From INSUFFICIENT_EVIDENCE, proceed to SYNTHESIS to formulate partial results with knowledge gaps
        self.state.transition_to(ResearchLifecycleState.SYNTHESIS, reason="Synthesizing partial knowledge gaps")
        self.assertEqual(self.state.current_state, ResearchLifecycleState.SYNTHESIS)

    def test_has_sufficient_evidence_logic(self):
        """Verify the Researcher does NOT assume crawlers done = research complete."""
        from core.research.contracts.evidence import EvidenceItem, EvidenceProvenance
        from core.research.contracts.question import ResearchQuestion

        q1 = ResearchQuestion(question_id="q-1", question_text="What is Raft?")
        q2 = ResearchQuestion(question_id="q-2", question_text="What is Paxos?")
        self.state.add_question(q1)
        self.state.add_question(q2)

        # Scope requires 2 evidence items per question
        self.assertFalse(self.state.has_sufficient_evidence())

        # Add 1 evidence for q-1
        ev1 = EvidenceItem(
            evidence_id="ev-1",
            provenance=EvidenceProvenance(request_id="req-1", crawler_task_id="ct-1", crawler_id="c-1", question_id="q-1"),
            extracted_fact="Raft is a leader-based consensus algorithm.",
        )
        self.state.add_evidence(ev1)
        self.assertFalse(self.state.has_sufficient_evidence())

        # Add 2nd evidence for q-1 (q-1 now has 2, but q-2 has 0)
        ev2 = EvidenceItem(
            evidence_id="ev-2",
            provenance=EvidenceProvenance(request_id="req-1", crawler_task_id="ct-2", crawler_id="c-1", question_id="q-1"),
            extracted_fact="Raft decomposes consensus into leader election, log replication, and safety.",
        )
        self.state.add_evidence(ev2)
        self.assertFalse(self.state.has_sufficient_evidence())

        # Add 2 evidence items for q-2
        ev3 = EvidenceItem(
            evidence_id="ev-3",
            provenance=EvidenceProvenance(request_id="req-1", crawler_task_id="ct-3", crawler_id="c-2", question_id="q-2"),
            extracted_fact="Paxos is a consensus protocol invented by Leslie Lamport.",
        )
        ev4 = EvidenceItem(
            evidence_id="ev-4",
            provenance=EvidenceProvenance(request_id="req-1", crawler_task_id="ct-4", crawler_id="c-2", question_id="q-2"),
            extracted_fact="Multi-Paxos optimizes basic Paxos for replicated log state machines.",
        )
        self.state.add_evidence(ev3)
        self.state.add_evidence(ev4)

        # Now all questions have at least 2 evidence items
        self.assertTrue(self.state.has_sufficient_evidence())

    def test_state_serialization_roundtrip(self):
        """Verify ResearchState to_dict and from_dict roundtrip."""
        data = self.state.to_dict()
        self.assertEqual(data["current_state"], ResearchLifecycleState.RESEARCH_REQUESTED.value)
        self.assertEqual(data["request"]["request_id"], "req-test-01")

        restored = ResearchState.from_dict(data)
        self.assertEqual(restored.current_state, ResearchLifecycleState.RESEARCH_REQUESTED)
        self.assertEqual(restored.request.request_id, "req-test-01")
        self.assertEqual(restored.request.objective, "Evaluate distributed consensus algorithms")


if __name__ == "__main__":
    unittest.main()
