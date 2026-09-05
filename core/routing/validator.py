from __future__ import annotations

import logging
from typing import Optional
import uuid

from core.routing.model import RoutingContext, RoutingDecision, RoutingProposal, utc_now
from core.routing.types import RouteDestination, RoutingConfidenceTier

logger = logging.getLogger("AutonomOS.RoutingValidator")


class RoutingValidator:
    """
    Deterministic validator gating untrusted routing proposals before acceptance.
    Enforces route validity, confidence bounds, target worker validity,
    incompatible combination prevention, and clarification consistency.
    """

    @classmethod
    def validate(
        cls,
        proposal: RoutingProposal,
        context: Optional[RoutingContext] = None,
        source_request_id: str = "",
    ) -> tuple[bool, list[str], Optional[RoutingDecision]]:
        """
        Validate a RoutingProposal and synthesize an authoritative RoutingDecision if valid.
        Returns: (is_valid, validation_errors, validated_decision)
        """
        errors: list[str] = []
        trace: list[str] = []

        if not isinstance(proposal, RoutingProposal):
            errors.append(f"Expected RoutingProposal instance, got {type(proposal).__name__}")
            return False, errors, None

        # 1. Validate route enum membership
        route_dest: Optional[RouteDestination] = None
        try:
            route_dest = RouteDestination(proposal.route.upper() if proposal.route else "")
        except (ValueError, KeyError):
            errors.append(f"Invalid route destination '{proposal.route}'. Must be one of {[r.value for r in RouteDestination]}")
            return False, errors, None

        # 2. Validate confidence bounds [0.0, 1.0]
        confidence = float(proposal.confidence)
        if confidence < 0.0 or confidence > 1.0:
            errors.append(f"Confidence score {confidence} is out of valid bounds [0.0, 1.0].")
            return False, errors, None

        # 3. Validate required fields
        if not (proposal.reason or "").strip():
            errors.append("Routing proposal must include a non-empty 'reason' explaining the routing decision.")

        # 4. Low-confidence demotion check
        clarification_required = proposal.clarification_required
        clarification_questions = list(proposal.clarification_questions)
        ambiguities = list(proposal.ambiguities)

        if confidence < 0.40 and route_dest != RouteDestination.CLARIFICATION:
            trace.append(
                f"Confidence {confidence:.2f} is below uncertainty threshold (0.40); "
                f"reconciling route '{route_dest.value}' to CLARIFICATION."
            )
            route_dest = RouteDestination.CLARIFICATION
            clarification_required = True
            if not clarification_questions:
                clarification_questions.append(
                    f"The intent of '{proposal.reason}' is uncertain. Please clarify what action or research is needed."
                )

        # 5. Incompatible combinations checks
        if proposal.requires_research_evidence and route_dest == RouteDestination.DIRECT_ACTION:
            errors.append("Incompatible routing: DIRECT_ACTION cannot require research evidence.")

        if route_dest == RouteDestination.SPECIALIZED_WORKER:
            if not proposal.target_worker:
                errors.append("SPECIALIZED_WORKER route requires a non-empty 'target_worker'.")
            elif context and context.available_workers:
                normalized_target = proposal.target_worker.lower()
                valid_worker = any(normalized_target in w.lower() for w in context.available_workers)
                if not valid_worker:
                    errors.append(
                        f"Target worker '{proposal.target_worker}' is not available in registered workers: {context.available_workers}"
                    )

        # 6. Clarification consistency
        if route_dest == RouteDestination.CLARIFICATION or clarification_required:
            clarification_required = True
            if not clarification_questions and not ambiguities:
                clarification_questions.append("Could you clarify the exact action or details for this request?")
                trace.append("Auto-populated default clarification question for CLARIFICATION route.")

        # 7. Multi-stage validation
        suggested_stages = list(proposal.suggested_stages)
        if route_dest == RouteDestination.MULTI_STAGE and not suggested_stages:
            suggested_stages = [
                {"stage": 1, "route": RouteDestination.RESEARCH.value, "description": "Information gathering"},
                {"stage": 2, "route": RouteDestination.SPECIALIZED_WORKER.value, "description": "Implementation"},
            ]
            trace.append("Auto-populated default multi-stage plan for MULTI_STAGE route.")

        if errors:
            return False, errors, None

        # Determine confidence tier
        if confidence >= 0.85:
            tier = RoutingConfidenceTier.HIGH
        elif confidence >= 0.60:
            tier = RoutingConfidenceTier.MEDIUM
        elif confidence >= 0.40:
            tier = RoutingConfidenceTier.LOW
        else:
            tier = RoutingConfidenceTier.UNCERTAIN

        decision = RoutingDecision(
            decision_id=f"route-dec-{uuid.uuid4().hex[:8]}",
            route=route_dest,
            confidence=confidence,
            confidence_tier=tier,
            reason=proposal.reason.strip(),
            requires_external_information=proposal.requires_external_information,
            requires_research_evidence=proposal.requires_research_evidence,
            requires_project_context=proposal.requires_project_context,
            requires_tool_execution=proposal.requires_tool_execution,
            target_worker=proposal.target_worker,
            suggested_stages=suggested_stages,
            ambiguities=ambiguities,
            clarification_required=clarification_required,
            clarification_questions=clarification_questions,
            routing_trace=trace,
            source_request_id=source_request_id,
            created_at=utc_now(),
        )

        return True, [], decision
