from __future__ import annotations

import json
import unittest

from core.inference.provider import MockProvider
from core.research.contracts.request import ResearchRequest
from core.research.crawler.registry import CrawlerRegistry
from core.routing import (
    DeterministicRoutingSignals,
    DispatchResult,
    LLMRoutingClassifier,
    RequestDispatcher,
    RequestRouter,
    RouteDestination,
    RoutingConfidenceTier,
    RoutingContext,
    RoutingDecision,
    RoutingValidator,
)


class TestRequestRouter(unittest.TestCase):
    """
    Unit test suite verifying the Request Routing subsystem:
    - Accurate routing to DIRECT_ACTION, RESEARCH, SPECIALIZED_WORKER,
      MANAGER_REASONING, CLARIFICATION, and MULTI_STAGE.
    - Strict Researcher bypass for non-research requests.
    - Deterministic validation around untrusted LLM proposals.
    - Complete provenance, trace, and serialization fidelity.
    """

    def setUp(self):
        self.validator = RoutingValidator()

    def _create_router_with_mock(self, custom_response: str) -> RequestRouter:
        """Create a RequestRouter backed by a deterministic MockProvider."""
        provider = MockProvider(
            provider_id="mock-routing-provider",
            custom_response=custom_response,
        )
        classifier = LLMRoutingClassifier(
            provider=provider,
            model_id="mock-routing-model",
        )
        return RequestRouter(classifier=classifier, validator=self.validator)

    # -------------------------------------------------------------------------
    # 1. Direct action request ("Move that card to center.")
    # -------------------------------------------------------------------------
    def test_direct_action_request_move_card_to_center(self):
        """Verify 'Move that card to center.' routes to DIRECT_ACTION and bypasses Researcher."""
        llm_payload = json.dumps({
            "route": "DIRECT_ACTION",
            "confidence": 0.95,
            "reason": "Direct UI manipulation command requiring no external research.",
            "requires_external_information": False,
            "requires_research_evidence": False,
            "requires_project_context": False,
            "requires_tool_execution": True,
        })
        router = self._create_router_with_mock(llm_payload)
        dispatcher = RequestDispatcher()

        text = "Move that card to center."
        decision = router.route(text, request_id="req-ui-001")

        self.assertEqual(decision.route, RouteDestination.DIRECT_ACTION)
        self.assertTrue(decision.can_bypass_researcher())
        self.assertFalse(decision.is_research_required())
        self.assertEqual(decision.confidence_tier, RoutingConfidenceTier.HIGH)
        self.assertTrue(decision.requires_tool_execution)
        self.assertFalse(decision.requires_research_evidence)

        # Dispatch and verify strict bypass
        result = dispatcher.dispatch(decision, text)
        self.assertTrue(result.bypassed_researcher)
        self.assertFalse(result.research_request_created)
        self.assertEqual(dispatcher.researcher_activation_count, 0)
        self.assertEqual(dispatcher.research_request_count, 0)
        self.assertEqual(dispatcher.crawler_allocation_count, 0)
        self.assertEqual(dispatcher.direct_action_count, 1)

    # -------------------------------------------------------------------------
    # 2. Research request ("Compare PostgreSQL and MongoDB for our application.")
    # -------------------------------------------------------------------------
    def test_research_request_compare_databases(self):
        """Verify comparison query routes to RESEARCH and requires research evidence."""
        llm_payload = json.dumps({
            "route": "RESEARCH",
            "confidence": 0.92,
            "reason": "Comparative database evaluation requiring technical documentation and benchmark evidence.",
            "requires_external_information": True,
            "requires_research_evidence": True,
            "requires_project_context": True,
            "requires_tool_execution": False,
        })
        router = self._create_router_with_mock(llm_payload)

        text = "Compare PostgreSQL and MongoDB for our application."
        decision = router.route(text, request_id="req-res-002")

        self.assertEqual(decision.route, RouteDestination.RESEARCH)
        self.assertTrue(decision.is_research_required())
        self.assertFalse(decision.can_bypass_researcher())
        self.assertTrue(decision.requires_research_evidence)
        self.assertTrue(decision.requires_external_information)

    # -------------------------------------------------------------------------
    # 3. Specialized worker request ("Run the test suite.")
    # -------------------------------------------------------------------------
    def test_specialized_worker_request_run_test_suite(self):
        """Verify testing command routes to SPECIALIZED_WORKER with target_worker='worker.tester'."""
        llm_payload = json.dumps({
            "route": "SPECIALIZED_WORKER",
            "confidence": 0.96,
            "reason": "Request asks to execute the test suite, delegating to the QA/tester worker.",
            "requires_external_information": False,
            "requires_research_evidence": False,
            "requires_project_context": False,
            "requires_tool_execution": True,
            "target_worker": "worker.tester",
        })
        router = self._create_router_with_mock(llm_payload)
        dispatcher = RequestDispatcher()

        text = "Run the test suite."
        decision = router.route(text, request_id="req-test-003")

        self.assertEqual(decision.route, RouteDestination.SPECIALIZED_WORKER)
        self.assertTrue(decision.can_bypass_researcher())
        self.assertEqual(decision.target_worker, "worker.tester")

        # Dispatch and verify worker handoff
        result = dispatcher.dispatch(decision, text)
        self.assertTrue(result.bypassed_researcher)
        self.assertFalse(result.research_request_created)
        self.assertEqual(dispatcher.specialized_worker_count, 1)
        self.assertEqual(dispatcher.researcher_activation_count, 0)

    # -------------------------------------------------------------------------
    # 4. Manager reasoning request ("Which of these two approaches is cleaner?")
    # -------------------------------------------------------------------------
    def test_manager_reasoning_request(self):
        """Verify architectural tradeoff query routes to MANAGER_REASONING."""
        llm_payload = json.dumps({
            "route": "MANAGER_REASONING",
            "confidence": 0.88,
            "reason": "Evaluation of existing design approaches within project context, requiring no web research.",
            "requires_external_information": False,
            "requires_research_evidence": False,
            "requires_project_context": True,
            "requires_tool_execution": False,
        })
        router = self._create_router_with_mock(llm_payload)
        dispatcher = RequestDispatcher()

        text = "Which of these two approaches is cleaner?"
        decision = router.route(text, request_id="req-mgr-004")

        self.assertEqual(decision.route, RouteDestination.MANAGER_REASONING)
        self.assertTrue(decision.can_bypass_researcher())
        self.assertTrue(decision.requires_project_context)

        result = dispatcher.dispatch(decision, text)
        self.assertEqual(dispatcher.manager_reasoning_count, 1)
        self.assertEqual(dispatcher.researcher_activation_count, 0)

    # -------------------------------------------------------------------------
    # 5. Clarification-required request ("Move that there.")
    # -------------------------------------------------------------------------
    def test_clarification_required_deictic_ambiguity(self):
        """Verify unresolvable deictic references trigger CLARIFICATION with structured questions."""
        llm_payload = json.dumps({
            "route": "CLARIFICATION",
            "confidence": 0.95,
            "reason": "Deictic pronouns 'that' and 'there' cannot be resolved without antecedent context.",
            "requires_external_information": False,
            "requires_research_evidence": False,
            "requires_project_context": False,
            "requires_tool_execution": False,
            "clarification_required": True,
            "clarification_questions": ["What specific element should be moved, and to which location?"],
        })
        router = self._create_router_with_mock(llm_payload)
        dispatcher = RequestDispatcher()

        text = "Move that there."
        decision = router.route(text, request_id="req-clar-005")

        self.assertEqual(decision.route, RouteDestination.CLARIFICATION)
        self.assertTrue(decision.clarification_required)
        self.assertGreaterEqual(len(decision.clarification_questions), 1)
        self.assertIn("location", decision.clarification_questions[0])

        result = dispatcher.dispatch(decision, text)
        self.assertEqual(dispatcher.clarification_count, 1)
        self.assertEqual(dispatcher.researcher_activation_count, 0)

    # -------------------------------------------------------------------------
    # 6. Multi-stage request ("Research best database then update project.")
    # -------------------------------------------------------------------------
    def test_multi_stage_request(self):
        """Verify compound research + implementation request routes to MULTI_STAGE with suggested phases."""
        llm_payload = json.dumps({
            "route": "MULTI_STAGE",
            "confidence": 0.91,
            "reason": "Compound instruction requiring research discovery phase followed by engineering implementation.",
            "requires_external_information": True,
            "requires_research_evidence": True,
            "requires_project_context": True,
            "requires_tool_execution": True,
            "suggested_stages": [
                {"stage": 1, "route": "RESEARCH", "description": "Investigate vector database options"},
                {"stage": 2, "route": "SPECIALIZED_WORKER", "description": "Implement client integration"},
            ],
        })
        router = self._create_router_with_mock(llm_payload)
        dispatcher = RequestDispatcher()

        text = "Research the best database for our app and then update the project."
        decision = router.route(text, request_id="req-multi-006")

        self.assertEqual(decision.route, RouteDestination.MULTI_STAGE)
        self.assertEqual(len(decision.suggested_stages), 2)
        self.assertEqual(decision.suggested_stages[0]["route"], "RESEARCH")

        result = dispatcher.dispatch(decision, text)
        self.assertEqual(dispatcher.multi_stage_count, 1)
        self.assertEqual(dispatcher.researcher_activation_count, 0)

    # -------------------------------------------------------------------------
    # 7. Ambiguous request with low confidence demotion
    # -------------------------------------------------------------------------
    def test_ambiguous_request_demoted_to_clarification(self):
        """Verify low-confidence (< 0.40) analytical proposals are deterministically demoted to CLARIFICATION."""
        llm_payload = json.dumps({
            "route": "DIRECT_ACTION",
            "confidence": 0.25,
            "reason": "Completely unsure what user intends by this phrase.",
            "requires_external_information": False,
            "requires_research_evidence": False,
            "requires_project_context": False,
            "requires_tool_execution": False,
        })
        router = self._create_router_with_mock(llm_payload)

        text = "Maybe later do something with the thing."
        decision = router.route(text, request_id="req-amb-007")

        self.assertEqual(decision.route, RouteDestination.CLARIFICATION)
        self.assertTrue(decision.clarification_required)
        self.assertEqual(decision.confidence_tier, RoutingConfidenceTier.UNCERTAIN)
        self.assertGreaterEqual(len(decision.clarification_questions), 1)

    # -------------------------------------------------------------------------
    # 8. Malformed LLM output
    # -------------------------------------------------------------------------
    def test_malformed_llm_output_handled_safely(self):
        """Verify malformed JSON from LLM falls back safely without unhandled crashes."""
        router = self._create_router_with_mock("```json\n{ not valid json syntax ...\n")

        text = "Check the server health."
        decision = router.route(text, request_id="req-malformed-008")

        self.assertIsInstance(decision, RoutingDecision)
        self.assertIn(decision.route, [RouteDestination.CLARIFICATION, RouteDestination.DIRECT_ACTION])
        self.assertFalse(decision.is_research_required())

    # -------------------------------------------------------------------------
    # 9. Invalid route proposed by LLM
    # -------------------------------------------------------------------------
    def test_invalid_route_rejected_by_validator(self):
        """Verify unrecognized route strings in proposals are rejected and handled safely."""
        llm_payload = json.dumps({
            "route": "MAGIC_TELEPORT",
            "confidence": 0.99,
            "reason": "Invented route destination.",
        })
        router = self._create_router_with_mock(llm_payload)

        text = "Teleport the system to production."
        decision = router.route(text, request_id="req-invalid-009")

        self.assertEqual(decision.route, RouteDestination.CLARIFICATION)
        self.assertTrue(decision.clarification_required)

    # -------------------------------------------------------------------------
    # 10. Low-confidence routing handling
    # -------------------------------------------------------------------------
    def test_low_confidence_routing_bounds(self):
        """Verify confidence tier computation and validation bounds."""
        llm_payload = json.dumps({
            "route": "DIRECT_ACTION",
            "confidence": 0.45,
            "reason": "Marginal match for direct action.",
        })
        router = self._create_router_with_mock(llm_payload)

        text = "Minimize the active editor window."
        decision = router.route(text, request_id="req-lowconf-010")

        self.assertEqual(decision.confidence_tier, RoutingConfidenceTier.LOW)
        self.assertEqual(decision.confidence, 0.45)

    # -------------------------------------------------------------------------
    # 11. Researcher bypass verification
    # -------------------------------------------------------------------------
    def test_researcher_bypass_multiple_actions(self):
        """Verify diverse direct action requests consistently bypass Researcher."""
        direct_requests = [
            "Move that card to center.",
            "Click the submit button.",
            "Toggle dark mode switch.",
            "Scroll down to footer.",
        ]

        llm_payload = json.dumps({
            "route": "DIRECT_ACTION",
            "confidence": 0.95,
            "reason": "Direct action command.",
            "requires_tool_execution": True,
        })
        router = self._create_router_with_mock(llm_payload)
        dispatcher = RequestDispatcher()

        for req in direct_requests:
            dec = router.route(req)
            self.assertTrue(dec.can_bypass_researcher(), f"Failed for request: {req}")
            res = dispatcher.dispatch(dec, req)
            self.assertTrue(res.bypassed_researcher)

        self.assertEqual(dispatcher.researcher_activation_count, 0)
        self.assertEqual(dispatcher.research_request_count, 0)
        self.assertEqual(dispatcher.crawler_allocation_count, 0)
        self.assertEqual(dispatcher.direct_action_count, len(direct_requests))

    # -------------------------------------------------------------------------
    # 12. Research routing into existing Researcher boundary
    # -------------------------------------------------------------------------
    def test_research_routing_into_researcher_boundary(self):
        """Verify RESEARCH decision hands off into Researcher execution boundary."""
        class MockResearcherSpecialist:
            def __init__(self):
                self.calls = []

            def execute_research(self, req: ResearchRequest):
                self.calls.append(req)
                return {"result": "success", "objective": req.objective}

        mock_researcher = MockResearcherSpecialist()
        dispatcher = RequestDispatcher(researcher=mock_researcher)

        decision = RoutingDecision(
            decision_id="dec-res-012",
            route=RouteDestination.RESEARCH,
            confidence=0.95,
            confidence_tier=RoutingConfidenceTier.HIGH,
            reason="Broad technology exploration.",
            requires_research_evidence=True,
            requires_external_information=True,
            source_request_id="src-012",
        )

        result = dispatcher.dispatch(decision, "Compare Kafka and RabbitMQ architectures.")

        self.assertFalse(result.bypassed_researcher)
        self.assertTrue(result.research_request_created)
        self.assertEqual(dispatcher.researcher_activation_count, 1)
        self.assertEqual(dispatcher.research_request_count, 1)
        self.assertEqual(len(mock_researcher.calls), 1)
        self.assertEqual(mock_researcher.calls[0].objective, "Compare Kafka and RabbitMQ architectures.")

    # -------------------------------------------------------------------------
    # 13. No crawler activation for direct actions
    # -------------------------------------------------------------------------
    def test_no_crawler_activation_for_direct_actions(self):
        """Verify CrawlerRegistry active count is completely unchanged during direct action."""
        registry = CrawlerRegistry()
        initial_crawlers = len(registry.list_active_crawlers())

        router = RequestRouter()  # Fast-path deterministic router
        dispatcher = RequestDispatcher()

        decision = router.route("Move that card to center.")
        result = dispatcher.dispatch(decision, "Move that card to center.")

        # Ensure no crawlers were registered or spawned
        self.assertEqual(len(registry.list_active_crawlers()), initial_crawlers)
        self.assertEqual(dispatcher.crawler_allocation_count, 0)

    # -------------------------------------------------------------------------
    # 14. No ResearchRequest creation for direct actions
    # -------------------------------------------------------------------------
    def test_no_research_request_creation_for_direct_actions(self):
        """Verify no ResearchRequest contract instance is created for direct action routes."""
        router = RequestRouter()
        dispatcher = RequestDispatcher()

        decision = router.route("Click the cancel button on the dialog.")
        result = dispatcher.dispatch(decision, "Click the cancel button on the dialog.")

        self.assertFalse(result.research_request_created)
        self.assertEqual(dispatcher.research_request_count, 0)

    # -------------------------------------------------------------------------
    # 15. Provenance and trace preservation
    # -------------------------------------------------------------------------
    def test_provenance_and_trace_preservation(self):
        """Verify routing trace and source request id are preserved with serialization roundtrip."""
        llm_payload = json.dumps({
            "route": "SPECIALIZED_WORKER",
            "confidence": 0.90,
            "reason": "Implementation request.",
            "target_worker": "worker.programmer",
        })
        router = self._create_router_with_mock(llm_payload)

        context = RoutingContext(
            project_id="proj-trace-015",
            available_workers=["worker.researcher", "worker.programmer", "worker.tester"],
        )
        decision = router.route("Refactor user auth handler function.", request_id="req-trace-015", context=context)

        self.assertEqual(decision.source_request_id, "req-trace-015")
        self.assertTrue(len(decision.routing_trace) >= 3)
        self.assertTrue(any("Routing request" in t for t in decision.routing_trace))
        self.assertTrue(any("validated" in t.lower() or "finalized" in t.lower() for t in decision.routing_trace))

        # Serialization roundtrip
        dec_dict = decision.to_dict()
        self.assertIsInstance(dec_dict, dict)
        restored = RoutingDecision.from_dict(dec_dict)
        self.assertEqual(restored.decision_id, decision.decision_id)
        self.assertEqual(restored.route, decision.route)
        self.assertEqual(restored.target_worker, decision.target_worker)
        self.assertEqual(restored.source_request_id, decision.source_request_id)

    # -------------------------------------------------------------------------
    # 16. Cancellation and empty request handling
    # -------------------------------------------------------------------------
    def test_cancellation_and_empty_request_handling(self):
        """Verify cancelled context yields FAILED and empty request yields CLARIFICATION."""
        router = RequestRouter()

        # 16a: Cancelled request
        cancelled_ctx = RoutingContext(task_metadata={"cancelled": True})
        dec_cancelled = router.route("Do something.", context=cancelled_ctx)
        self.assertEqual(dec_cancelled.route, RouteDestination.FAILED)
        self.assertIn("cancelled", dec_cancelled.reason.lower())

        # 16b: Empty request
        dec_empty = router.route("   ")
        self.assertEqual(dec_empty.route, RouteDestination.CLARIFICATION)
        self.assertTrue(dec_empty.clarification_required)
        self.assertIn("empty", dec_empty.reason.lower())
