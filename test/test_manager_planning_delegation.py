import json
import os
from pathlib import Path
import shutil
import tempfile
import unittest

from core.enums import DependencyType, ProjectStatus, RiskLevel, TaskStatus
from core.manager.planner import ManagerPlanner, DelegationPlan, WorkerTaskContract
from core.models import Project
from core.runtime.workforce_runtime import WorkforceRuntime
from core.storage.sqlite_store import SQLiteStore
from core.workspace.filesystem import ControlledWorkspaceFS
from core.workspace.project_map import ProjectMapEngine


class TestManagerPlanningDelegation(unittest.TestCase):
    """
    Focused unit tests verifying Manager Task Decomposition, Worker Selection,
    Dependency Creation, Worker Prompt Generation, and Worker Inactivity (Disabled Mode).
    """

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp(prefix="autonomos_test_plan_")
        self.workspace_root = Path(self.temp_dir) / "project"
        self.workspace_root.mkdir()
        self.fs = ControlledWorkspaceFS(self.workspace_root)

        # Set up a sample project structure
        self.fs.create_file("src/components/SongPitchGraph.tsx", """
export function SongPitchGraph() {
  return <div>Pitch Graph</div>;
}
""")
        self.fs.create_file("src/styles/graph.css", ".graph { margin: 10px; }")
        self.fs.create_file("tests/components/SongPitchGraph.test.tsx", "test('renders', () => {});")

        # Initialize Project Map
        self.map_engine = ProjectMapEngine(self.fs)
        self.map_engine.perform_full_audit(trigger="TEST_INIT")

        # Initialize Runtime
        self.store = SQLiteStore(":memory:")
        self.runtime = WorkforceRuntime(self.store)
        self.project = self.runtime.create_project(
            name="AudioStudio",
            root_path=str(self.workspace_root),
            description="Audio waveform and pitch studio",
        )

        self.planner = ManagerPlanner(self.runtime, map_engine=self.map_engine)

    def tearDown(self):
        if os.path.exists(self.temp_dir):
            shutil.rmtree(self.temp_dir)

    def test_task_decomposition_and_worker_selection(self):
        """Verify Manager decomposes 'Improve pitch graph spacing' into Programmer and Tester tasks."""
        objective = "Improve the pitch graph spacing in SongPitchGraph component."

        plan: DelegationPlan = self.planner.plan_and_delegate(
            project_id=self.project.id,
            objective=objective,
        )

        self.assertIsInstance(plan, DelegationPlan)
        self.assertEqual(plan.project_id, self.project.id)
        self.assertEqual(plan.objective, objective)
        self.assertTrue(plan.workers_disabled)

        # Verify task breakdown
        worker_types = [t.worker_type for t in plan.tasks]
        self.assertIn("Programmer", worker_types)
        self.assertIn("Tester", worker_types)

        # Check Programmer Task
        prog_task = next(t for t in plan.tasks if t.worker_type == "Programmer")
        self.assertEqual(prog_task.worker_id, "worker.programmer.general")
        self.assertTrue(any("SongPitchGraph.tsx" in f for f in prog_task.relevant_files))

        # Check Tester Task
        test_task = next(t for t in plan.tasks if t.worker_type == "Tester")
        self.assertEqual(test_task.worker_id, "worker.tester.verification")

    def test_dependency_creation_in_runtime_task_engine(self):
        """Verify directed dependencies are properly wired in the runtime TaskEngine."""
        objective = "Improve the pitch graph spacing."

        plan = self.planner.plan_and_delegate(
            project_id=self.project.id,
            objective=objective,
        )

        prog_task = next(t for t in plan.tasks if t.worker_type == "Programmer")
        test_task = next(t for t in plan.tasks if t.worker_type == "Tester")

        # In the contract
        self.assertIn(prog_task.task_id, test_task.dependencies)

        # In the runtime TaskEngine
        stored_test_task = self.runtime.tasks.get_task(test_task.task_id)
        # Verify test task status is PENDING because it depends on the programmer task
        self.assertEqual(stored_test_task.status, TaskStatus.PENDING)

        # Verify dependency resolution
        satisfied, unsatisfied = self.runtime.tasks.dependency_resolver.are_dependencies_satisfied(test_task.task_id)
        self.assertFalse(satisfied)
        self.assertTrue(any(prog_task.task_id in u for u in unsatisfied))

    def test_scope_constraints_acceptance_criteria_and_evidence(self):
        """Verify contracts specify explicit constraints, criteria, and audit rules."""
        objective = "Improve the pitch graph spacing."
        user_constraint = "Do not modify backend APIs."

        plan = self.planner.plan_and_delegate(
            project_id=self.project.id,
            objective=objective,
            user_constraints=[user_constraint],
        )

        for task_contract in plan.tasks:
            self.assertGreater(len(task_contract.constraints), 0)
            self.assertTrue(any(user_constraint in c for c in task_contract.constraints))
            self.assertGreater(len(task_contract.acceptance_criteria), 0)
            self.assertGreater(len(task_contract.required_evidence), 0)
            self.assertGreater(len(task_contract.audit_criteria), 0)

    def test_worker_prompt_generation(self):
        """Verify self-contained instruction prompt generated for each worker."""
        objective = "Improve the pitch graph spacing."

        plan = self.planner.plan_and_delegate(
            project_id=self.project.id,
            objective=objective,
        )

        prog_task = next(t for t in plan.tasks if t.worker_type == "Programmer")
        prompt = prog_task.worker_prompt

        self.assertIn("WORKER TASK CONTRACT: PROGRAMMER", prompt)
        self.assertIn("Task Objective", prompt)
        self.assertIn("Acceptance Criteria", prompt)
        self.assertIn("Operational Constraints", prompt)
        self.assertIn("Required Evidence Output", prompt)

    def test_workers_remain_disabled(self):
        """
        Critical invariant: Tasks are created and registered in TaskEngine,
        but workers are NEVER executed.
        """
        objective = "Improve the pitch graph spacing."

        plan = self.planner.plan_and_delegate(
            project_id=self.project.id,
            objective=objective,
        )

        self.assertTrue(plan.workers_disabled)

        # Inspect all created tasks in runtime
        tasks_in_runtime = self.runtime.tasks.list_tasks(self.project.id)
        self.assertEqual(len(tasks_in_runtime), len(plan.tasks))

        for t in tasks_in_runtime:
            # Tasks must be PENDING or READY, never RUNNING or COMPLETED
            self.assertIn(t.status, (TaskStatus.READY, TaskStatus.PENDING))
            self.assertEqual(t.attempts, 0)
            self.assertIsNone(t.started_at)
            self.assertIsNone(t.completed_at)

    def test_researcher_task_inclusion_on_investigative_goals(self):
        """Verify Researcher worker is included when the goal is diagnostic/investigative."""
        objective = "Investigate memory leak and diagnose high CPU usage in audio renderer."

        plan = self.planner.plan_and_delegate(
            project_id=self.project.id,
            objective=objective,
        )

        worker_types = [t.worker_type for t in plan.tasks]
        self.assertIn("Researcher", worker_types)
        self.assertIn("Programmer", worker_types)
        self.assertIn("Tester", worker_types)

        research_task = next(t for t in plan.tasks if t.worker_type == "Researcher")
        prog_task = next(t for t in plan.tasks if t.worker_type == "Programmer")

        # Programmer depends on Researcher
        self.assertIn(research_task.task_id, prog_task.dependencies)


if __name__ == "__main__":
    unittest.main()
