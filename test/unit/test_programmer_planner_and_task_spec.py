from __future__ import annotations

import unittest

from core.models import Task
from workers.programmer.planner import ProgrammingPlanner
from workers.programmer.types import ProgrammingMode


class TestProgrammerPlannerAndTaskSpec(unittest.TestCase):
    """Unit tests for task decomposition and planning across programming modes."""

    def test_implicit_mode_detection_from_objective(self):
        task_fix = Task(id="t1", project_id="p1", title="Fix error", objective="Fix null pointer error in auth token parser")
        spec_fix = ProgrammingPlanner.parse_task_spec(task_fix)
        self.assertEqual(spec_fix.mode, ProgrammingMode.BUGFIX)

        task_refactor = Task(id="t2", project_id="p1", title="Refactor", objective="Refactor repository access classes")
        spec_refactor = ProgrammingPlanner.parse_task_spec(task_refactor)
        self.assertEqual(spec_refactor.mode, ProgrammingMode.REFACTOR)

        task_patch = Task(id="t3", project_id="p1", title="Patch", objective="Patch version number in setup.cfg")
        spec_patch = ProgrammingPlanner.parse_task_spec(task_patch)
        self.assertEqual(spec_patch.mode, ProgrammingMode.PATCH)

        task_feat = Task(id="t4", project_id="p1", title="Implement", objective="Implement Stripe webhook handler")
        spec_feat = ProgrammingPlanner.parse_task_spec(task_feat)
        self.assertEqual(spec_feat.mode, ProgrammingMode.FEATURE)

    def test_explicit_mode_override_in_metadata(self):
        task = Task(
            id="t1",
            project_id="p1",
            title="Clean up auth",
            objective="Clean up auth",
            metadata={"mode": "FEATURE", "allowed_paths": ["src/auth.py"]},
        )
        spec = ProgrammingPlanner.parse_task_spec(task)
        self.assertEqual(spec.mode, ProgrammingMode.FEATURE)
        self.assertEqual(spec.scope.allowed_paths, ["src/auth.py"])

    def test_plan_generation_for_all_modes(self):
        for mode in (ProgrammingMode.FEATURE, ProgrammingMode.BUGFIX, ProgrammingMode.REFACTOR, ProgrammingMode.PATCH):
            task = Task(id="t1", project_id="p1", title=f"Test {mode.value}", objective=f"Objective for {mode.value}", metadata={"mode": mode.value})
            spec = ProgrammingPlanner.parse_task_spec(task)
            plan = ProgrammingPlanner.create_plan(spec)
            self.assertEqual(plan.mode, mode)
            self.assertGreater(len(plan.planned_steps), 3)


if __name__ == "__main__":
    unittest.main()
