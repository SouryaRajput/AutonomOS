import pathlib
import shutil
import tempfile
import unittest

from core.context.model import ContextBudget, ContextPackage
from core.enums import ArtifactType, MemoryType, ProjectStatus, TaskStatus, ToolStatus, WorkerStatus
from core.errors import TaskAlreadyCompletedError, WorkerBusyError
from core.events.types import EventType
from core.inference.model import InferenceMessage, InferenceRequest, ModelRequirement
from core.memory.model import compute_checksum
from core.runtime.workforce_runtime import WorkforceRuntime
from workers.dummy_worker import DummyWorker


class TestEndToEndGoldenPath(unittest.TestCase):
    """
    Stage 5 Golden Path Integration Test.
    Proves the complete deterministic workflow:
    Create Project -> Auto-Init Memory Scaffold (project-map, architecture, current-state)
    -> Record ADR -> Register Worker -> Discover Tools -> Create Tasks with Explicit References
    -> Request & Assemble Bounded ContextPackage via Context Engine
    -> Explicit Runtime Tool Execution (filesystem.write_file)
    -> DummyWorker Receives ContextPackage & Executes Tool via Worker SDK -> Generates Artifact & Report
    -> Record Task Memory -> Update Current State -> Downstream DAG Resolution
    -> Audit References -> Events Stream (including CONTEXT_ASSEMBLED, TOOL_REQUESTED, TOOL_COMPLETED)
    -> Process Restart -> Reload Runtime, Memory, Context, and Tools on Restart.
    """

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.db_path = str(pathlib.Path(self.temp_dir) / "autonomos_state.db")
        self.project_workspace = str(pathlib.Path(self.temp_dir) / "workspace")

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_complete_golden_path_with_tools_context_memory_and_events(self):
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
                title="Tool Runtime Security & Confinement",
                context="AI models require secure, observable, workspace-confined tool execution.",
                decision="Implement universal Tool Runtime with strict pre-execution permission evaluation.",
                reasoning="Prevents unauthorized host access, directory escapes, and secret leaks.",
                consequences="All worker side effects must route through ToolRequest.",
                decision_number=1,
            )
            self.assertEqual(decision_doc.memory_type, MemoryType.DECISION)

            # 4. Register DummyWorker
            dummy_worker = DummyWorker(
                worker_id="worker.dummy.primary",
                name="Primary Dummy Worker",
                artifact_filename="hello.txt",
                artifact_content="Hello, Stage 5 Tool Runtime!\nThis artifact proves deterministic execution, events, memory, context, and tools.",
            )
            worker_manifest = runtime.register_worker(dummy_worker)
            self.assertEqual(worker_manifest.status, WorkerStatus.IDLE)

            # 5. Create Task 1 (Prerequisite with context reference)
            task_1 = runtime.create_task(
                project_id=project.id,
                title="Generate Hello Artifact via Tool",
                objective="Use Tool Runtime to write hello.txt and verify Context Engine assembly",
                context_references=[{"path": ".autonomos/memory/architecture.md"}],
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

            # 7. Explicit Standalone Context Request Verification (Stage 4)
            explicit_ctx = runtime.request_context(
                task_id=task_1.id,
                worker_id=dummy_worker.get_manifest().id,
                budget=ContextBudget(max_tokens=2500),
                focus_areas=[".autonomos/memory/architecture.md", "hello"],
            )
            self.assertIsInstance(explicit_ctx, ContextPackage)
            self.assertGreater(explicit_ctx.selected_count, 0)
            self.assertLessEqual(explicit_ctx.total_estimated_tokens, 2500)

            # 8. Explicit Standalone Tool Execution Verification (Stage 5)
            tool_res = runtime.execute_tool(
                project_id=project.id,
                task_id=task_1.id,
                worker_id=dummy_worker.get_manifest().id,
                tool_id="filesystem.write_file",
                arguments={"path": "setup_check.txt", "content": "Standalone Tool Execution Succeeded\n"},
            )
            self.assertEqual(tool_res.status, ToolStatus.SUCCESS)
            self.assertTrue((pathlib.Path(self.project_workspace) / "setup_check.txt").exists())

            # 9. Explicit Standalone Inference Gateway Execution (Stage 8 - OmniRoute)
            inf_req = InferenceRequest(
                request_id="req-gp-1",
                project_id=project.id,
                task_id=task_1.id,
                worker_id=dummy_worker.get_manifest().id,
                messages=[InferenceMessage(role="user", content="Plan architecture for hello task.")],
                requirements=ModelRequirement(),
            )
            inf_resp = runtime.request_inference(inf_req)
            self.assertIsNotNone(inf_resp)
            self.assertEqual(inf_resp.request_id, "req-gp-1")
            self.assertGreater(inf_resp.usage.total_tokens, 0)

            # 10. Assign Task 1 to DummyWorker
            assigned_task, assigned_worker = runtime.assign_task(task_1.id, dummy_worker.get_manifest().id)
            self.assertEqual(assigned_task.status, TaskStatus.ASSIGNED)
            self.assertEqual(assigned_worker.status, WorkerStatus.ASSIGNED)

            # 10. Run Task 1 (Worker internally requests context AND executes filesystem tool)
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

            # Verify Report document created in .autonomos/reports/
            report_file = pathlib.Path(self.project_workspace) / ".autonomos" / "reports" / "task-001" / "execution.md"
            self.assertTrue(report_file.exists())

            # Verify Task Memory document created in .autonomos/tasks/
            task_mem_file = pathlib.Path(self.project_workspace) / ".autonomos" / "tasks" / "task-001.md"
            self.assertTrue(task_mem_file.exists())

            # 11. Update Current State
            runtime.update_current_state(
                project_id=project.id,
                stage="Stage 5 Complete",
                completed_milestones=["Core Runtime", "Event System", "Project Memory", "Context Engine", "Tool Runtime"],
                active_work=["Safety & Rollback"],
                known_limitations=["LLM integrations scheduled for Stage 6"],
            )
            self.assertIn("Stage 5 Complete", state_file.read_text())

            # 12. Execute Task 2 to complete the full sequence
            runtime.assign_task(task_2.id, dummy_worker.get_manifest().id)
            output_2 = runtime.run_task(task_2.id)
            self.assertTrue(output_2.success)
            self.assertEqual(runtime.tasks.get_task(task_2.id).status, TaskStatus.COMPLETED)

            # 13. Audit memory references
            audit_reports = runtime.audit_project_memory(project.id)
            self.assertTrue(all(r.is_valid for r in audit_reports))

            # 14. Verify Events emitted (including Context, Tool, Safety & Verification events)
            events = runtime.get_events(project_id=project.id)
            types = [e.event_type for e in events]
            self.assertIn(EventType.PROJECT_CREATED, types)
            self.assertIn(EventType.MEMORY_CREATED, types)
            self.assertIn(EventType.DECISION_CREATED, types)
            self.assertIn(EventType.CONTEXT_REQUESTED, types)
            self.assertIn(EventType.CONTEXT_ASSEMBLED, types)
            self.assertIn(EventType.TOOL_REQUESTED, types)
            self.assertIn(EventType.TOOL_AUTHORIZED, types)
            self.assertIn(EventType.TOOL_COMPLETED, types)
            self.assertIn(EventType.SAFETY_CHECK_REQUESTED, types)
            self.assertIn(EventType.SAFETY_ALLOWED, types)
            self.assertIn(EventType.CHECKPOINT_CREATED, types)
            self.assertIn(EventType.CHECKPOINT_COMMITTED, types)
            self.assertIn(EventType.INFERENCE_REQUESTED, types)
            self.assertIn(EventType.INFERENCE_ROUTED, types)
            self.assertIn(EventType.INFERENCE_COMPLETED, types)
            self.assertIn(EventType.VERIFICATION_STARTED, types)
            self.assertIn(EventType.CHECK_STARTED, types)
            self.assertIn(EventType.CHECK_PASSED, types)
            self.assertIn(EventType.VERIFICATION_PASSED, types)
            self.assertIn(EventType.REPORT_CREATED, types)
            self.assertIn(EventType.TASK_COMPLETED, types)

        finally:
            runtime.close()

        # 15. Process Restart & Persistence Verification
        restarted_runtime = WorkforceRuntime.with_sqlite(self.db_path)
        restarted_runtime.register_worker_instance_only(dummy_worker)

        try:
            # Check Project & Tasks
            p = restarted_runtime.projects.get_project("proj-demo-1")
            self.assertEqual(p.name, "AutonomOS Demo Project")

            t1 = restarted_runtime.tasks.get_task("task-001")
            self.assertEqual(t1.status, TaskStatus.COMPLETED)

            # Re-run Tool Execution on restarted runtime
            post_restart_tool_res = restarted_runtime.execute_tool(
                project_id="proj-demo-1",
                task_id="task-001",
                worker_id="worker.dummy.primary",
                tool_id="filesystem.read_file",
                arguments={"path": "setup_check.txt"},
            )
            self.assertEqual(post_restart_tool_res.status, ToolStatus.SUCCESS)
            self.assertIn("Standalone Tool Execution Succeeded", post_restart_tool_res.output["content"])

        finally:
            restarted_runtime.close()


if __name__ == "__main__":
    unittest.main()
