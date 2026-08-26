import pathlib
import shutil
import tempfile
import unittest

from core.enums import ArtifactType, MemoryType, ProjectStatus, TaskStatus, WorkerStatus
from core.errors import TaskAlreadyCompletedError, WorkerBusyError
from core.events.types import EventType
from core.memory.model import compute_checksum
from core.runtime.workforce_runtime import WorkforceRuntime
from workers.dummy_worker import DummyWorker


class TestEndToEndGoldenPath(unittest.TestCase):
    """
    Stage 3 Golden Path Integration Test.
    Proves the complete deterministic workflow AND the persistent project memory system:
    Create Project -> Auto-Init Memory Scaffold (project-map, architecture, current-state)
    -> Record ADR -> Register Worker -> Create Tasks -> Assign -> Run DummyWorker
    -> Generate Artifact & Worker Execution Report -> Record Task Memory -> Update Current State
    -> Downstream DAG Resolution -> Validate References -> Event Stream -> Process Restart & Verification.
    """

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.db_path = str(pathlib.Path(self.temp_dir) / "autonomos_state.db")
        self.project_workspace = str(pathlib.Path(self.temp_dir) / "workspace")

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_complete_golden_path_with_memory_and_events(self):
        # 1. Initialize Runtime with SQLite
        runtime = WorkforceRuntime.with_sqlite(self.db_path)

        try:
            # 2. Create Project (auto-initializes persistent memory scaffold)
            project = runtime.create_project(
                name="AutonomOS Demo Project",
                root_path=self.project_workspace,
                description="End-to-end integration test workspace",
                project_id="proj-demo-1",
            )
            self.assertEqual(project.status, ProjectStatus.ACTIVE)
            self.assertTrue(pathlib.Path(self.project_workspace).exists())

            # Verify memory scaffold files created on disk in .autonomos/
            map_file = pathlib.Path(self.project_workspace) / ".autonomos" / "memory" / "project-map.md"
            arch_file = pathlib.Path(self.project_workspace) / ".autonomos" / "memory" / "architecture.md"
            state_file = pathlib.Path(self.project_workspace) / ".autonomos" / "memory" / "current-state.md"

            self.assertTrue(map_file.exists())
            self.assertTrue(arch_file.exists())
            self.assertTrue(state_file.exists())

            # 3. Record an Architectural Decision (ADR)
            decision_doc = runtime.record_decision(
                project_id=project.id,
                title="Markdown First Persistent Memory",
                context="Future AI workers need external persistent project knowledge.",
                decision="Implement .autonomos/ markdown directory with SQLite index.",
                reasoning="Human inspectability and version control without LLM vendor lock-in.",
                consequences="Requires atomic file writes and reference validation.",
                decision_number=1,
            )
            self.assertEqual(decision_doc.memory_type, MemoryType.DECISION)
            adr_file = pathlib.Path(self.project_workspace) / decision_doc.relative_path
            self.assertTrue(adr_file.exists())

            # 4. Register DummyWorker
            dummy_worker = DummyWorker(
                worker_id="worker.dummy.primary",
                name="Primary Dummy Worker",
                artifact_filename="hello.txt",
                artifact_content="Hello, Stage 3 Runtime!\nThis artifact proves deterministic execution, event timeline, and persistent memory.",
            )
            worker_manifest = runtime.register_worker(dummy_worker)
            self.assertEqual(worker_manifest.status, WorkerStatus.IDLE)

            # 5. Create Task 1 (Prerequisite)
            task_1 = runtime.create_task(
                project_id=project.id,
                title="Generate Hello Artifact",
                objective="Create a hello.txt artifact file and record execution report",
                task_id="task-001",
            )
            self.assertEqual(task_1.status, TaskStatus.READY)

            # 6. Create Task 2 (Dependent on Task 1)
            task_2 = runtime.create_task(
                project_id=project.id,
                title="Process Hello Artifact",
                objective="Verify downstream dependency resolution and task memory",
                dependencies=[task_1.id],
                task_id="task-002",
            )
            self.assertEqual(task_2.status, TaskStatus.PENDING)

            # 7. Assign Task 1 to DummyWorker
            assigned_task, assigned_worker = runtime.assign_task(task_1.id, dummy_worker.get_manifest().id)
            self.assertEqual(assigned_task.status, TaskStatus.ASSIGNED)
            self.assertEqual(assigned_worker.status, WorkerStatus.ASSIGNED)

            # 8. Run Task 1
            output = runtime.run_task(task_1.id)
            self.assertTrue(output.success)
            self.assertIn("successfully executed", output.summary)

            # Verify Task 1 is COMPLETED
            completed_task_1 = runtime.tasks.get_task(task_1.id)
            self.assertEqual(completed_task_1.status, TaskStatus.COMPLETED)
            self.assertEqual(completed_task_1.attempts, 1)

            # Verify Worker is back to IDLE
            idle_worker = runtime.workers.get_worker(dummy_worker.get_manifest().id)
            self.assertEqual(idle_worker.status, WorkerStatus.IDLE)

            # Verify Artifact generated on disk and indexed
            artifact_file = pathlib.Path(self.project_workspace).resolve() / "hello.txt"
            self.assertTrue(artifact_file.exists())
            self.assertIn("Hello, Stage 3 Runtime!", artifact_file.read_text())

            # Verify Report document created in .autonomos/reports/
            report_file = pathlib.Path(self.project_workspace) / ".autonomos" / "reports" / "task-001" / "execution.md"
            self.assertTrue(report_file.exists())
            self.assertIn("Task Execution Report", report_file.read_text())

            # Verify Task Memory document created in .autonomos/tasks/
            task_mem_file = pathlib.Path(self.project_workspace) / ".autonomos" / "tasks" / "task-001.md"
            self.assertTrue(task_mem_file.exists())
            self.assertIn("Generate Hello Artifact", task_mem_file.read_text())

            # 9. Update Current State
            runtime.update_current_state(
                project_id=project.id,
                stage="Stage 3 Complete",
                completed_milestones=["Core Runtime", "Event System", "Project Memory"],
                active_work=["Tool Runtime"],
                known_limitations=["Context Engine pending Stage 4"],
            )
            self.assertIn("Stage 3 Complete", state_file.read_text())

            # 10. Verify Downstream Dependency: Task 2 is now automatically promoted from PENDING to READY
            updated_task_2 = runtime.tasks.get_task(task_2.id)
            self.assertEqual(updated_task_2.status, TaskStatus.READY)

            # 11. Execute Task 2 to complete the full sequence
            runtime.assign_task(task_2.id, dummy_worker.get_manifest().id)
            output_2 = runtime.run_task(task_2.id)
            self.assertTrue(output_2.success)
            self.assertEqual(runtime.tasks.get_task(task_2.id).status, TaskStatus.COMPLETED)

            # 12. Audit memory references
            audit_reports = runtime.audit_project_memory(project.id)
            self.assertTrue(all(r.is_valid for r in audit_reports))

            # 13. Verify Events emitted
            events = runtime.get_events(project_id=project.id)
            types = [e.event_type for e in events]
            self.assertIn(EventType.PROJECT_CREATED, types)
            self.assertIn(EventType.MEMORY_CREATED, types)
            self.assertIn(EventType.DECISION_CREATED, types)
            self.assertIn(EventType.REPORT_CREATED, types)
            self.assertIn(EventType.TASK_COMPLETED, types)

        finally:
            runtime.close()

        # 14. Process Restart & State/Memory Verification (Rule 9 & Stage 3 persistence)
        restarted_runtime = WorkforceRuntime.with_sqlite(self.db_path)
        restarted_runtime.register_worker_instance_only(dummy_worker)

        try:
            # Check Project
            p = restarted_runtime.projects.get_project("proj-demo-1")
            self.assertEqual(p.name, "AutonomOS Demo Project")

            # Check Tasks
            t1 = restarted_runtime.tasks.get_task("task-001")
            self.assertEqual(t1.status, TaskStatus.COMPLETED)

            # Check Memory Documents reloaded from SQLite
            mem_docs = restarted_runtime.memory.list_memory("proj-demo-1")
            self.assertGreaterEqual(len(mem_docs), 6)  # map, arch, state, decision, 2 task reports/memories

            # Verify files still exist on disk and match checksums
            for doc in mem_docs:
                full_path = pathlib.Path(self.project_workspace) / doc.relative_path
                self.assertTrue(full_path.exists())
                self.assertEqual(doc.checksum, compute_checksum(pathlib.Path(full_path).read_text()))

            # Check events intact
            reloaded_events = restarted_runtime.get_events()
            self.assertGreaterEqual(len(reloaded_events), 15)

        finally:
            restarted_runtime.close()


if __name__ == "__main__":
    unittest.main()
