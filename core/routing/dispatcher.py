from __future__ import annotations

from dataclasses import dataclass, field
import logging
from typing import Any, Callable, Optional
import uuid

from core.research.contracts.request import ResearchRequest
from core.routing.model import RoutingDecision
from core.routing.types import RouteDestination

logger = logging.getLogger("AutonomOS.RequestDispatcher")


@dataclass
class DispatchResult:
    """Outcome of dispatching a validated RoutingDecision to its target execution path."""
    decision_id: str
    route: RouteDestination
    bypassed_researcher: bool
    research_request_created: bool
    target_worker: Optional[str] = None
    output: Any = None
    details: dict[str, Any] = field(default_factory=dict)


class RequestDispatcher:
    """
    Executes or hands off validated RoutingDecisions to their appropriate subsystem.
    Enforces that non-research routes genuinely BYPASS the Researcher, ensuring:
    - researcher_activation_count == 0
    - research_request_count == 0
    - crawler_allocation_count == 0
    """

    def __init__(
        self,
        researcher: Optional[Any] = None,
        direct_action_handler: Optional[Callable[[str, dict[str, Any]], Any]] = None,
        worker_dispatcher: Optional[Callable[[str, str, dict[str, Any]], Any]] = None,
    ):
        self.researcher = researcher
        self.direct_action_handler = direct_action_handler
        self.worker_dispatcher = worker_dispatcher

        # Audit counters
        self.researcher_activation_count: int = 0
        self.research_request_count: int = 0
        self.crawler_allocation_count: int = 0
        self.direct_action_count: int = 0
        self.specialized_worker_count: int = 0
        self.clarification_count: int = 0
        self.manager_reasoning_count: int = 0
        self.multi_stage_count: int = 0

    def dispatch(
        self,
        decision: RoutingDecision,
        request_text: str,
        project_id: str = "default-project",
        task_id: str = "default-task",
    ) -> DispatchResult:
        """
        Dispatch the request based on the validated RoutingDecision.
        """
        route = decision.route

        # ---------------------------------------------------------------------
        # 1. RESEARCH Route: Hand off to existing Researcher boundary
        # ---------------------------------------------------------------------
        if route == RouteDestination.RESEARCH:
            self.research_request_count += 1
            research_req = ResearchRequest(
                request_id=f"req-res-{uuid.uuid4().hex[:8]}",
                project_id=project_id,
                task_id=task_id,
                objective=request_text,
                metadata={"source_request_id": decision.source_request_id},
            )

            research_output = None
            if self.researcher is not None:
                self.researcher_activation_count += 1
                logger.info(f"Activating Researcher for objective: '{request_text}'")
                if hasattr(self.researcher, "execute_research"):
                    research_output = self.researcher.execute_research(research_req)
                elif hasattr(self.researcher, "understand"):
                    research_output = self.researcher.understand(research_req)
                elif callable(self.researcher):
                    research_output = self.researcher(research_req)

            return DispatchResult(
                decision_id=decision.decision_id,
                route=RouteDestination.RESEARCH,
                bypassed_researcher=False,
                research_request_created=True,
                output=research_output,
                details={"research_request_id": research_req.request_id},
            )

        # ---------------------------------------------------------------------
        # 2. DIRECT_ACTION Route: Completely bypass Researcher
        # ---------------------------------------------------------------------
        if route == RouteDestination.DIRECT_ACTION:
            self.direct_action_count += 1
            logger.info(f"Direct action route selected. Bypassing Researcher for: '{request_text}'")
            action_output = None
            if self.direct_action_handler is not None:
                action_output = self.direct_action_handler(request_text, decision.metadata)
            else:
                action_output = {"status": "DIRECT_ACTION_QUEUED", "action": request_text}

            return DispatchResult(
                decision_id=decision.decision_id,
                route=RouteDestination.DIRECT_ACTION,
                bypassed_researcher=True,
                research_request_created=False,
                output=action_output,
                details={"reason": decision.reason},
            )

        # ---------------------------------------------------------------------
        # 3. SPECIALIZED_WORKER Route: Direct handoff to specialist
        # ---------------------------------------------------------------------
        if route == RouteDestination.SPECIALIZED_WORKER:
            self.specialized_worker_count += 1
            target = decision.target_worker or "worker.programmer"
            logger.info(f"Specialized worker route selected ({target}). Bypassing Researcher.")
            worker_output = None
            if self.worker_dispatcher is not None:
                worker_output = self.worker_dispatcher(target, request_text, decision.metadata)
            else:
                worker_output = {"status": "ASSIGNED_TO_WORKER", "worker": target, "task": request_text}

            return DispatchResult(
                decision_id=decision.decision_id,
                route=RouteDestination.SPECIALIZED_WORKER,
                bypassed_researcher=True,
                research_request_created=False,
                target_worker=target,
                output=worker_output,
                details={"target_worker": target},
            )

        # ---------------------------------------------------------------------
        # 4. CLARIFICATION Route: Prompt user for missing information
        # ---------------------------------------------------------------------
        if route == RouteDestination.CLARIFICATION:
            self.clarification_count += 1
            return DispatchResult(
                decision_id=decision.decision_id,
                route=RouteDestination.CLARIFICATION,
                bypassed_researcher=True,
                research_request_created=False,
                output={
                    "status": "CLARIFICATION_REQUIRED",
                    "questions": decision.clarification_questions,
                    "ambiguities": decision.ambiguities,
                },
                details={"clarification_questions": decision.clarification_questions},
            )

        # ---------------------------------------------------------------------
        # 5. MANAGER_REASONING Route: Direct to reasoning loop
        # ---------------------------------------------------------------------
        if route == RouteDestination.MANAGER_REASONING:
            self.manager_reasoning_count += 1
            return DispatchResult(
                decision_id=decision.decision_id,
                route=RouteDestination.MANAGER_REASONING,
                bypassed_researcher=True,
                research_request_created=False,
                output={"status": "MANAGER_REASONING_ENGAGED", "topic": request_text},
                details={"reason": decision.reason},
            )

        # ---------------------------------------------------------------------
        # 6. MULTI_STAGE Route: Return staged plan requirements
        # ---------------------------------------------------------------------
        if route == RouteDestination.MULTI_STAGE:
            self.multi_stage_count += 1
            return DispatchResult(
                decision_id=decision.decision_id,
                route=RouteDestination.MULTI_STAGE,
                bypassed_researcher=True,  # Does not immediately activate researcher in routing phase
                research_request_created=False,
                output={"status": "MULTI_STAGE_PLAN_REQUIRED", "stages": decision.suggested_stages},
                details={"stages": decision.suggested_stages},
            )

        # Fallback / Failed
        return DispatchResult(
            decision_id=decision.decision_id,
            route=RouteDestination.FAILED,
            bypassed_researcher=True,
            research_request_created=False,
            output={"status": "FAILED", "reason": decision.reason},
            details={"reason": decision.reason},
        )
