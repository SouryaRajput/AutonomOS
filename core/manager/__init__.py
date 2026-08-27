"""Manager Agent & Workforce Orchestrator Package."""
from core.manager.agent import ManagerAgent
from core.manager.controller import ManagerController
from core.manager.model import (
    ActionResult,
    CycleResult,
    ManagerAction,
    ManagerConfig,
    ManagerDecision,
    ManagerState,
    ManagerStatus,
    Plan,
)
from core.manager.prompt import (
    MANAGER_SYSTEM_PROMPT,
    build_manager_prompt,
    parse_manager_decision,
)
from core.manager.types import (
    AutonomyLevel,
    ConfidenceLevel,
    ManagerActionType,
    ManagerExecutionMode,
    PlanStatus,
)

__all__ = [
    "ManagerAgent",
    "ManagerController",
    "ManagerAction",
    "ManagerDecision",
    "Plan",
    "ManagerState",
    "ManagerConfig",
    "ManagerStatus",
    "ActionResult",
    "CycleResult",
    "AutonomyLevel",
    "ManagerActionType",
    "ConfidenceLevel",
    "PlanStatus",
    "ManagerExecutionMode",
    "MANAGER_SYSTEM_PROMPT",
    "build_manager_prompt",
    "parse_manager_decision",
]
