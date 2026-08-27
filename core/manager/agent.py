from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, Optional
import uuid

from core.context.model import ContextPackage
from core.events.model import Event
from core.inference.gateway import InferenceGateway
from core.inference.model import (
    Cost,
    InferenceMessage,
    InferenceRequest,
    InferenceResponse,
    ModelRequirement,
    Usage,
)
from core.inference.types import CostType, ModelCapability, RoutingProfile
from core.manager.model import ManagerConfig, ManagerDecision, ManagerState
from core.manager.prompt import build_manager_prompt, parse_manager_decision

logger = logging.getLogger("AutonomOS.ManagerAgent")


class ManagerAgent:
    """
    Intelligent reasoning brain for the Manager.
    Formulates structured prompts, queries InferenceGateway (OmniRoute), parses decisions,
    and tracks usage/costs. Operates as an unprivileged proposal generator.
    """

    def __init__(self, inference_gateway: InferenceGateway, config: Optional[ManagerConfig] = None):
        self.inference = inference_gateway
        self.config = config or ManagerConfig()
        self.total_tokens_used: int = 0
        self.total_cost_accumulated: float = 0.0

    def reason(
        self,
        state: ManagerState,
        cycle_id: str,
        context_package: Optional[ContextPackage] = None,
        trigger_event: Optional[Event] = None,
        feedback_message: Optional[str] = None,
        causation_id: Optional[str] = None,
    ) -> tuple[ManagerDecision, InferenceResponse]:
        """
        Execute a reasoning step using the Inference Gateway.
        Returns the parsed ManagerDecision and the raw InferenceResponse for auditability.
        """
        messages = build_manager_prompt(
            state=state,
            context_package=context_package,
            trigger_event=trigger_event,
            feedback_message=feedback_message,
        )

        req_id = f"req-mgr-{uuid.uuid4().hex[:8]}"
        requirements = self.config.model_requirements or ModelRequirement(
            required_capabilities={ModelCapability.REASONING, ModelCapability.STRUCTURED_OUTPUT},
            routing_profile=self.config.routing_profile,
        )
        req = InferenceRequest(
            request_id=req_id,
            project_id=state.project_id,
            task_id=f"mgr-{cycle_id}",
            worker_id="worker.manager.orchestrator",
            messages=messages,
            requirements=requirements,
            temperature=0.2,  # Low temperature for deterministic, structured planning
            metadata={"cycle_id": cycle_id, "manager_role": "orchestrator"},
        )

        response = self.inference.execute(req, causation_id=causation_id)

        # Track usage and cost
        if response.usage:
            self.total_tokens_used += response.usage.total_tokens
        if response.cost:
            self.total_cost_accumulated += response.cost.total_cost

        # Parse structured decision
        decision = parse_manager_decision(
            raw_text=response.content,
            project_id=state.project_id,
            cycle_id=cycle_id,
        )
        decision.metadata["model_used"] = response.model_used
        decision.metadata["provider_used"] = response.provider_used
        decision.metadata["cost"] = response.cost.total_cost if response.cost else 0.0

        return decision, response
