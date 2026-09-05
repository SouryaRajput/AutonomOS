from __future__ import annotations

import logging
from typing import Optional
import uuid

from core.routing.classifier import LLMRoutingClassifier, RoutingClassificationError
from core.routing.model import RoutingContext, RoutingDecision, RoutingProposal, utc_now
from core.routing.signals import DeterministicRoutingSignals, DeterministicSignalResult
from core.routing.types import RouteDestination, RoutingConfidenceTier
from core.routing.validator import RoutingValidator

logger = logging.getLogger("AutonomOS.RequestRouter")


class RequestRouter:
    """
    Intelligent Request Router for AutonomOS.
    Determines whether an incoming request requires DIRECT_ACTION, MANAGER_REASONING,
    RESEARCH, SPECIALIZED_WORKER, CLARIFICATION, or MULTI_STAGE execution.
    Researcher is ONE possible route, not the default.
    """

    def __init__(
        self,
        classifier: Optional[LLMRoutingClassifier] = None,
        validator: Optional[RoutingValidator] = None,
        prefer_deterministic_fast_path: bool = True,
    ):
        self.classifier = classifier
        self.validator = validator or RoutingValidator()
        self.prefer_deterministic_fast_path = prefer_deterministic_fast_path

    def route(
        self,
        request_text: str,
        request_id: Optional[str] = None,
        context: Optional[RoutingContext] = None,
    ) -> RoutingDecision:
        """
        Classifies request_text into an authoritative RoutingDecision.
        Completely bypasses Researcher if the route does not require research.
        """
        req_id = request_id or f"req-{uuid.uuid4().hex[:8]}"
        trace: list[str] = [f"Routing request '{req_id}': \"{request_text}\""]

        clean_text = (request_text or "").strip()

        # Step 0: Check for cancellation or empty input
        if context and context.task_metadata.get("cancelled", False):
            trace.append("Request is marked cancelled; returning FAILED.")
            return RoutingDecision(
                decision_id=f"route-dec-{uuid.uuid4().hex[:8]}",
                route=RouteDestination.FAILED,
                confidence=1.0,
                confidence_tier=RoutingConfidenceTier.HIGH,
                reason="Request was cancelled prior to routing.",
                routing_trace=trace,
                source_request_id=req_id,
            )

        if not clean_text:
            trace.append("Request text is empty; returning CLARIFICATION.")
            return RoutingDecision(
                decision_id=f"route-dec-{uuid.uuid4().hex[:8]}",
                route=RouteDestination.CLARIFICATION,
                confidence=1.0,
                confidence_tier=RoutingConfidenceTier.HIGH,
                reason="Empty request text received.",
                clarification_required=True,
                clarification_questions=["The request is empty. What task or research would you like to perform?"],
                routing_trace=trace,
                source_request_id=req_id,
            )

        # Step 1: Deterministic Signal Extraction
        trace.append("Analyzing deterministic routing signals.")
        signals = DeterministicRoutingSignals.analyze(clean_text, context)

        # Step 2: High-confidence fast path (e.g., direct UI actions or obvious deictic ambiguities without LLM)
        if (
            self.prefer_deterministic_fast_path
            and self.classifier is None
            and signals.suggested_route is not None
        ):
            trace.append(f"Applying deterministic route without LLM: {signals.suggested_route.value}")
            return self._build_deterministic_decision(signals, req_id, trace, context)

        # Step 3: LLM Classification (Untrusted Proposal)
        proposal: Optional[RoutingProposal] = None
        if self.classifier is not None:
            trace.append("Querying LLMRoutingClassifier for untrusted routing proposal.")
            try:
                proposal = self.classifier.classify(clean_text, context, signals)
                trace.append(
                    f"LLM proposed route '{proposal.route}' (confidence: {proposal.confidence:.2f}, "
                    f"model: {proposal.model_used})"
                )
            except RoutingClassificationError as e:
                trace.append(f"Routing classifier failed: {e.message}")
                logger.warning(f"Routing classifier failed for request '{req_id}': {e}")
            except Exception as e:
                trace.append(f"Unexpected routing classifier error: {e}")
                logger.exception(f"Unexpected error during routing for request '{req_id}': {e}")

        # Step 4: Fallback to deterministic signals if LLM failed or produced no proposal
        if proposal is None or not proposal.route:
            if signals.suggested_route is not None:
                trace.append(f"Falling back to deterministic signal route: {signals.suggested_route.value}")
                return self._build_deterministic_decision(signals, req_id, trace, context)
            else:
                trace.append("No valid proposal or deterministic route; failing safely to CLARIFICATION.")
                return RoutingDecision(
                    decision_id=f"route-dec-{uuid.uuid4().hex[:8]}",
                    route=RouteDestination.CLARIFICATION,
                    confidence=0.5,
                    confidence_tier=RoutingConfidenceTier.LOW,
                    reason="Could not determine a definitive execution route; clarification required.",
                    clarification_required=True,
                    clarification_questions=["Could you please provide more details on the requested task or question?"],
                    routing_trace=trace,
                    source_request_id=req_id,
                )

        # Step 5: Deterministic Validation of Proposal
        trace.append("Validating LLM proposal with RoutingValidator.")
        is_valid, validation_errors, validated_decision = self.validator.validate(
            proposal=proposal,
            context=context,
            source_request_id=req_id,
        )

        if not is_valid or validated_decision is None:
            trace.append(f"Proposal validation rejected proposal: {validation_errors}")
            # Fall back safely
            if signals.suggested_route is not None:
                trace.append(f"Recovering with deterministic signal route: {signals.suggested_route.value}")
                return self._build_deterministic_decision(signals, req_id, trace, context)

            trace.append("Defaulting to safe CLARIFICATION due to invalid proposal.")
            return RoutingDecision(
                decision_id=f"route-dec-{uuid.uuid4().hex[:8]}",
                route=RouteDestination.CLARIFICATION,
                confidence=0.3,
                confidence_tier=RoutingConfidenceTier.UNCERTAIN,
                reason=f"Routing proposal was invalid ({', '.join(validation_errors)}). Clarification required.",
                clarification_required=True,
                clarification_questions=["Could you clarify the specific instructions or goals for this request?"],
                routing_trace=trace,
                source_request_id=req_id,
            )

        # Step 6: Finalize validated decision
        trace.append(f"Routing finalized: {validated_decision.route.value} (Confidence: {validated_decision.confidence:.2f})")
        validated_decision.routing_trace = list(trace) + list(validated_decision.routing_trace)
        return validated_decision

    def _build_deterministic_decision(
        self,
        signals: DeterministicSignalResult,
        request_id: str,
        trace: list[str],
        context: Optional[RoutingContext],
    ) -> RoutingDecision:
        """Construct an authoritative decision directly from deterministic signals."""
        route = signals.suggested_route or RouteDestination.CLARIFICATION
        tier = (
            RoutingConfidenceTier.HIGH
            if signals.confidence >= 0.85
            else RoutingConfidenceTier.MEDIUM
        )

        clarification_required = route == RouteDestination.CLARIFICATION
        questions = list(signals.clarification_questions)
        if clarification_required and not questions:
            questions.append("Could you clarify the specific requirements for this request?")

        return RoutingDecision(
            decision_id=f"route-dec-{uuid.uuid4().hex[:8]}",
            route=route,
            confidence=signals.confidence,
            confidence_tier=tier,
            reason=f"Matched deterministic routing pattern(s): {', '.join(signals.matched_rules)}",
            requires_external_information=signals.requires_external_information,
            requires_research_evidence=signals.requires_research_evidence,
            requires_project_context=signals.requires_project_context,
            requires_tool_execution=signals.requires_tool_execution,
            target_worker=signals.target_worker,
            clarification_required=clarification_required,
            clarification_questions=questions,
            routing_trace=trace,
            source_request_id=request_id,
            created_at=utc_now(),
        )
