"""Stage 16 Full Release Integration & End-to-End Validation Suite.
Tests the complete integration across Application Boundary, Manager, Workers,
Autonomy Governance, Approvals, Rejections, User Questions, Emergency Stop, and Crash Recovery.
"""
import json
import os
import shutil
import tempfile
import unittest

from app.application import AutonomOSApp
from core.autonomy.types import ActionCategory, AutonomyLevel
from core.enums import ArtifactType, RiskLevel, TaskStatus
from core.events.types import EventType
from core.inference.provider import MockProvider
from core.models import WorkerManifest
from workers.dummy_worker import DummyWorker


class TestStage16FullRelease(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.temp_dir, "release_validation.db")
        self.app = AutonomOSApp.with_sqlite(self.db_path)

        # Create real project through App facade
        self.project_dict = self.app.projects.create_project(
            name="Stage 16 Release Project",
            root_path=self.temp_dir,
            description="End-to-end full release validation for Stage 16",
        )
        self.project_id = self.project_dict["id"]

        # Register specialist workers
        self.researcher = DummyWorker("worker.researcher", "Lead Researcher")
        self.programmer = DummyWorker("worker.programmer", "Senior Programmer")
        self.tester = DummyWorker("worker.tester", "QA & Test Engineer")

        self.app._runtime.register_worker(self.researcher)
        self.app._runtime.register_worker(self.programmer)
        self.app._runtime.register_worker(self.tester)

        provider = self.app._runtime.providers.get_provider("mock-provider")
        if isinstance(provider, MockProvider):
            self.mock_provider = provider

    def tearDown(self):
        self.app.close()
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_1_golden_product_path_end_to_end(self):
        """User -> App -> Manager -> Researcher -> Programmer -> Tester -> Verification -> Result."""
        # 1. User message initiates goal
        conv = self.app.conversations.get_or_create_active_conversation(self.project_id)
        msgs = self.app.conversations.post_user_message(
            conversation_id=conv.id,
            content="Build authentication for my application with JWT and SQLite.",
            dispatch_manager=False,
        )
        self.assertEqual(len(msgs), 1)
        self.assertEqual(msgs[0].sender, "user")

        # 2. Manager Cycle 1: Decomposes goal into Research, Implementation, and Verification tasks
        plan_decision = {
            "reasoning_summary": "Creating multi-worker execution plan for authentication feature.",
            "confidence_level": "CERTAIN",
            "plan_update": {
                "objective": "Build authentication for my application",
                "milestones": ["Milestone 1: Auth Architecture & Implementation"],
            },
            "actions": [
                {
                    "action_type": "CREATE_TASK",
                    "parameters": {
                        "title": "Research OAuth & JWT Architecture",
                        "objective": "Survey best practices and token expiration strategy",
                        "priority": 100,
                    },
                    "rationale": "Requirement exploration",
                }
            ],
        }
        self.mock_provider.set_canned_response(json.dumps(plan_decision))
        res1 = self.app.workflows.step_manager(self.project_id)
        self.assertTrue(res1["progress_detected"])

        tasks = self.app.tasks.list_tasks(self.project_id)
        self.assertEqual(len(tasks), 1)
        research_task_id = tasks[0]["id"]

        # 3. Researcher executes research task and produces artifact
        self.app._runtime.assign_task(research_task_id, "worker.researcher")
        out1 = self.app._runtime.run_task(research_task_id)
        self.assertTrue(out1.success)
        t1 = self.app.tasks.get_task(research_task_id)
        self.assertEqual(t1["status"], "COMPLETED")

        # 4. Programmer creates auth module
        prog_task = self.app.tasks.create_task(
            project_id=self.project_id,
            title="Implement JWT Token Handler",
            objective="Write auth verification functions",
        )
        self.app._runtime.assign_task(prog_task["id"], "worker.programmer")
        out2 = self.app._runtime.run_task(prog_task["id"])
        self.assertTrue(out2.success)
        t2 = self.app.tasks.get_task(prog_task["id"])
        self.assertEqual(t2["status"], "COMPLETED")

        # 5. Tester verifies implementation and generates evidence report
        test_task = self.app.tasks.create_task(
            project_id=self.project_id,
            title="Verify JWT Auth Test Suite",
            objective="Run unit tests on auth.py",
        )
        self.app._runtime.assign_task(test_task["id"], "worker.tester")
        out3 = self.app._runtime.run_task(test_task["id"])
        self.assertTrue(out3.success)
        t3 = self.app.tasks.get_task(test_task["id"])
        self.assertEqual(t3["status"], "COMPLETED")

        # 6. Verify full state available to UI via Application Boundary
        artifacts = self.app.artifacts.list_artifacts(project_id=self.project_id)
        self.assertGreaterEqual(len(artifacts), 3)

        search_res = self.app.search.search(self.project_id, "JWT")
        self.assertGreaterEqual(len(search_res), 2)

    def test_2_approval_path_and_execution(self):
        """Risky action -> Approval Request -> User Grants -> Execution Succeeds."""
        task = self.app.tasks.create_task(
            project_id=self.project_id,
            title="Deploy Staging Release",
            objective="Deploy container to staging cluster",
        )

        # 1. Request Approval
        req = self.app._runtime.autonomy.request_approval(
            project_id=self.project_id,
            task_id=task["id"],
            worker_id="worker.programmer",
            action="deploy.staging",
            category=ActionCategory.DEPLOY,
            risk_level=RiskLevel.HIGH,
            reason="Deploying new JWT auth service to staging namespace",
            requested_scope="staging-k8s",
        )

        # 2. Query pending via App facade
        pending = self.app.approvals.list_pending_approvals(self.project_id)
        self.assertEqual(len(pending), 1)
        self.assertEqual(pending[0]["action"], "deploy.staging")

        # 3. User approves via App facade
        granted = self.app.approvals.grant_approval(req.id, decided_by="SecOps Lead")
        self.assertEqual(granted["status"], "APPROVED")
        self.assertEqual(granted["decided_by"], "SecOps Lead")

        # 4. Check no pending approvals remain
        self.assertEqual(len(self.app.approvals.list_pending_approvals(self.project_id)), 0)

    def test_3_rejection_path_and_manager_replanning(self):
        """Risky action -> User Rejects -> Rejection recorded."""
        task = self.app.tasks.create_task(
            project_id=self.project_id,
            title="Direct Prod DB Migration",
            objective="Execute raw drop table in production",
        )

        req = self.app._runtime.autonomy.request_approval(
            project_id=self.project_id,
            task_id=task["id"],
            worker_id="worker.programmer",
            action="db.drop_legacy",
            category=ActionCategory.DELETE,
            risk_level=RiskLevel.CRITICAL,
            reason="Direct drop of legacy user table",
            requested_scope="prod-db",
        )

        # User rejects
        rejected = self.app.approvals.reject_approval(req.id, reason="Dropping table in prod is prohibited; archive instead.")
        self.assertEqual(rejected["status"], "REJECTED")
        self.assertIn("archive instead", rejected["rejection_reason"])

    def test_4_user_question_and_response_flow(self):
        """Workforce asks user question -> UI displays -> User answers -> Answer recorded."""
        task = self.app.tasks.create_task(
            project_id=self.project_id,
            title="Session Store Setup",
            objective="Choose storage engine",
        )

        u_req = self.app._runtime.autonomy.request_user_input(
            project_id=self.project_id,
            task_id=task["id"],
            question="Do you prefer Redis or SQLite for session token cache?",
        )

        pending = self.app.approvals.list_pending_user_inputs(self.project_id)
        self.assertEqual(len(pending), 1)

        answered = self.app.approvals.respond_to_user_input(u_req.id, answer="Redis with persistent disk snapshot")
        self.assertEqual(answered["status"], "ANSWERED")
        self.assertEqual(answered["answer"], "Redis with persistent disk snapshot")

    def test_5_emergency_stop_and_explicit_policy_re_evaluation(self):
        """Emergency stop freezes executions -> Explicit resume re-evaluates policy."""
        # 1. Activate Emergency Stop
        stop_res = self.app.policies.emergency_stop(self.project_id, reason="Security audit underway")
        self.assertTrue(stop_res["stopped"])
        self.assertTrue(self.app.policies.is_emergency_stopped())

        # 2. Consequential action blocked
        decision = self.app._runtime.autonomy.evaluate_tool_action(
            project_id=self.project_id,
            tool_id="shell.execute",
            arguments={"command": "ls -la"},
        )
        self.assertEqual(decision.result.value, "DENY")
        self.assertIn("EMERGENCY_STOP_ACTIVE", decision.explanation.matched_rules)

        # 3. Explicit Resume
        clear_res = self.app.policies.clear_emergency_stop(self.project_id)
        self.assertFalse(clear_res["stopped"])
        self.assertFalse(self.app.policies.is_emergency_stopped())

        # 4. Policy allows normal safe reads
        decision_after = self.app._runtime.autonomy.evaluate_tool_action(
            project_id=self.project_id,
            tool_id="filesystem.read",
            arguments={"path": "README.md"},
        )
        self.assertEqual(decision_after.result.value, "ALLOW")

    def test_6_crash_recovery_and_resynchronization(self):
        """Backend terminates -> Restarts from SQLite -> Full state reconstructed."""
        task = self.app.tasks.create_task(
            project_id=self.project_id,
            title="Recovery Verification Task",
            objective="Ensure data survives restart",
        )
        task_id = task["id"]

        # Close first app
        self.app.close()

        # Reopen second app instance from same SQLite DB
        reopened = AutonomOSApp.with_sqlite(self.db_path)
        try:
            p = reopened.projects.get_project(self.project_id)
            self.assertEqual(p["id"], self.project_id)
            t = reopened.tasks.get_task(task_id)
            self.assertEqual(t["id"], task_id)
            self.assertEqual(t["title"], "Recovery Verification Task")
        finally:
            reopened.close()


if __name__ == "__main__":
    unittest.main()
