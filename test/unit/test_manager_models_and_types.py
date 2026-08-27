import unittest

from core.context.model import ContextBudget
from core.enums import RiskLevel, TaskStatus
from core.inference.types import RoutingProfile
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
from core.manager.types import (
    AutonomyLevel,
    ConfidenceLevel,
    ManagerActionType,
    PlanStatus,
)
from core.models import Task, WorkerManifest, utc_now


class TestManagerModelsAndTypes(unittest.TestCase):

    def test_manager_action_serialization(self):
        action = ManagerAction(
            action_type=ManagerActionType.CREATE_TASK,
            parameters={"title": "Implement API", "priority": 80, "risk": "MEDIUM"},
            rationale="Backend foundation needed",
        )
        d = action.to_dict()
        self.assertEqual(d["action_type"], "CREATE_TASK")
        self.assertEqual(d["parameters"]["title"], "Implement API")

        restored = ManagerAction.from_dict(d)
        self.assertEqual(restored.action_type, ManagerActionType.CREATE_TASK)
        self.assertEqual(restored.parameters["priority"], 80)
        self.assertEqual(restored.rationale, "Backend foundation needed")

    def test_manager_decision_serialization(self):
        decision = ManagerDecision(
            decision_id="dec-123",
            cycle_id="cycle-456",
            project_id="proj-789",
            reasoning_summary="Tasks are ready to assign.",
            actions=[
                ManagerAction(action_type=ManagerActionType.ASSIGN_TASK, parameters={"task_id": "t1", "worker_id": "w1"}),
            ],
            assumptions=["PostgreSQL is configured"],
            risks=["API schema may change"],
            confidence_level=ConfidenceLevel.LOW_UNCERTAINTY,
            plan_update={"objective": "Build REST API", "milestones": ["M1", "M2"]},
        )
        d = decision.to_dict()
        self.assertEqual(d["decision_id"], "dec-123")
        self.assertEqual(d["confidence_level"], "LOW_UNCERTAINTY")
        self.assertEqual(len(d["actions"]), 1)

        restored = ManagerDecision.from_dict(d)
        self.assertEqual(restored.decision_id, "dec-123")
        self.assertEqual(restored.confidence_level, ConfidenceLevel.LOW_UNCERTAINTY)
        self.assertEqual(restored.actions[0].action_type, ManagerActionType.ASSIGN_TASK)
        self.assertEqual(restored.assumptions, ["PostgreSQL is configured"])

    def test_plan_serialization_and_versioning(self):
        plan = Plan(
            id="plan-1",
            project_id="proj-1",
            objective="Develop E-commerce platform",
            tasks=[{"title": "Setup DB"}, {"title": "Build Auth"}],
            milestones=["Phase 1: Auth", "Phase 2: Payments"],
            dependencies={"task-2": ["task-1"]},
            status=PlanStatus.ACTIVE,
            version=1,
        )
        d = plan.to_dict()
        self.assertEqual(d["version"], 1)
        self.assertEqual(d["status"], "ACTIVE")

        restored = Plan.from_dict(d)
        self.assertEqual(restored.id, "plan-1")
        self.assertEqual(restored.version, 1)
        self.assertEqual(restored.status, PlanStatus.ACTIVE)
        self.assertEqual(len(restored.milestones), 2)

    def test_manager_config_defaults(self):
        cfg = ManagerConfig(
            autonomy_level=AutonomyLevel.BALANCED,
            max_actions_per_cycle=5,
            max_cycles_without_progress=3,
            max_cost_per_project=25.0,
        )
        d = cfg.to_dict()
        self.assertEqual(d["autonomy_level"], "BALANCED")
        self.assertEqual(d["max_actions_per_cycle"], 5)
        self.assertEqual(d["max_cost_per_project"], 25.0)


if __name__ == "__main__":
    unittest.main()
