"""Unit & concurrency tests for Stage 16 Human-in-the-Loop, Autonomy, and Safety."""
import concurrent.futures
import os
import shutil
import tempfile
import unittest

from app.application import AutonomOSApp
from core.autonomy.types import ActionCategory
from core.enums import RiskLevel, TaskStatus
from core.models import WorkerManifest
from core.workflow.types import WorkflowStatus


class TestAppHitlAndSafety(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.temp_dir, "hitl_safety.db")
        self.app = AutonomOSApp.with_sqlite(self.db_path)
        self.project_dict = self.app.projects.create_project(
            name="HITL Safety Project",
            root_path=self.temp_dir,
            description="Testing human-in-the-loop and safety controls",
        )
        self.project_id = self.project_dict["id"]

        # Register workers
        for wid in ["worker.programmer", "worker.tester", "worker.researcher", "worker.manager"]:
            manifest = WorkerManifest(
                id=wid,
                name=wid.replace("worker.", "").title(),
                role="Specialist",
                description="Worker description",
                version="1.0.0",
                capabilities=[],
            )
            self.app._runtime.store.save_worker(manifest)

    def tearDown(self):
        self.app.close()
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_approval_grant_and_rejection_flows(self):
        task = self.app.tasks.create_task(
            project_id=self.project_id,
            title="Destructive Action Task",
            objective="Delete legacy configuration cache",
        )

        # 1. Request Approval
        req = self.app._runtime.autonomy.request_approval(
            project_id=self.project_id,
            task_id=task["id"],
            worker_id="worker.programmer",
            action="shell.rm_rf",
            category=ActionCategory.DELETE,
            risk_level=RiskLevel.CRITICAL,
            reason="Clear deprecated legacy cache folder",
            requested_scope="/cache/legacy",
        )

        # 2. List pending
        pending = self.app.approvals.list_pending_approvals(self.project_id)
        self.assertEqual(len(pending), 1)
        self.assertEqual(pending[0]["id"], req.id)
        self.assertEqual(pending[0]["action"], "shell.rm_rf")
        self.assertEqual(pending[0]["risk_level"], "CRITICAL")

        # 3. Grant approval
        granted = self.app.approvals.grant_approval(req.id, decided_by="Admin")
        self.assertEqual(granted["status"], "APPROVED")
        self.assertEqual(granted["decided_by"], "Admin")

        # 4. Check no longer pending
        pending_after = self.app.approvals.list_pending_approvals(self.project_id)
        self.assertEqual(len(pending_after), 0)

    def test_concurrent_approval_resolution_race_condition(self):
        """Test two clients attempting to resolve the same approval simultaneously."""
        task = self.app.tasks.create_task(
            project_id=self.project_id,
            title="Concurrent Approval Task",
            objective="Test race conditions",
        )

        req = self.app._runtime.autonomy.request_approval(
            project_id=self.project_id,
            task_id=task["id"],
            worker_id="worker.programmer",
            action="git.push_main",
            category=ActionCategory.EXTERNAL_SIDE_EFFECT,
            risk_level=RiskLevel.HIGH,
            reason="Deploy release tag",
            requested_scope="main branch",
        )

        results = []
        errors = []

        def client_a_approve():
            try:
                res = self.app.approvals.grant_approval(req.id, decided_by="ClientA")
                results.append(("ClientA", res))
            except Exception as e:
                errors.append(("ClientA", e))

        def client_b_reject():
            try:
                res = self.app.approvals.reject_approval(req.id, reason="Operator reject", decided_by="ClientB")
                results.append(("ClientB", res))
            except Exception as e:
                errors.append(("ClientB", e))

        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
            fut1 = executor.submit(client_a_approve)
            fut2 = executor.submit(client_b_reject)
            fut1.result()
            fut2.result()

        # Exactly 1 operation must succeed and exactly 1 must receive a validation error
        self.assertEqual(len(results), 1, f"Expected exactly 1 successful resolution, got {len(results)}")
        self.assertEqual(len(errors), 1, f"Expected exactly 1 rejection error, got {len(errors)}")

    def test_user_input_and_decision_requests(self):
        task = self.app.tasks.create_task(
            project_id=self.project_id,
            title="Database Selection Task",
            objective="Select database architecture",
        )

        # 1. User Input Request
        u_req = self.app._runtime.autonomy.request_user_input(
            project_id=self.project_id,
            task_id=task["id"],
            question="Which database engine should be used for user session storage?",
        )
        self.assertEqual(u_req.status.value, "PENDING")

        inputs = self.app.approvals.list_pending_user_inputs(self.project_id)
        self.assertEqual(len(inputs), 1)

        answered = self.app.approvals.respond_to_user_input(u_req.id, "PostgreSQL")
        self.assertEqual(answered["status"], "ANSWERED")
        self.assertEqual(answered["answer"], "PostgreSQL")

        # 2. Decision Request
        d_req = self.app._runtime.autonomy.request_decision(
            project_id=self.project_id,
            task_id=task["id"],
            title="Select Authentication Framework",
            options=["OAuth 2.0 PKCE", "JWT Bearer", "Session Cookies"],
        )

        decisions = self.app.approvals.list_pending_decisions(self.project_id)
        self.assertEqual(len(decisions), 1)

        decided = self.app.approvals.respond_to_decision(d_req.id, chosen_option="OAuth 2.0 PKCE")
        self.assertEqual(decided["status"], "DECIDED")
        self.assertEqual(decided["chosen_option"], "OAuth 2.0 PKCE")

    def test_emergency_stop_and_policy_re_evaluation(self):
        # 1. Check initial state
        self.assertFalse(self.app.policies.is_emergency_stopped())

        # 2. Activate emergency stop
        stop_res = self.app.policies.emergency_stop(self.project_id, reason="Security audit in progress")
        self.assertTrue(stop_res["stopped"])
        self.assertTrue(self.app.policies.is_emergency_stopped())

        # 3. Verify that action evaluation is DENIED while emergency stop is active
        decision = self.app._runtime.autonomy.evaluate_tool_action(
            project_id=self.project_id,
            tool_id="filesystem.read",
            arguments={"path": "README.md"},
        )
        self.assertEqual(decision.result.value, "DENY")
        self.assertIn("EMERGENCY_STOP_ACTIVE", decision.explanation.matched_rules)

        # 4. Clear emergency stop
        clear_res = self.app.policies.clear_emergency_stop(self.project_id)
        self.assertFalse(clear_res["stopped"])
        self.assertFalse(self.app.policies.is_emergency_stopped())

        # 5. Policy re-evaluation now permits safe read
        decision_after = self.app._runtime.autonomy.evaluate_tool_action(
            project_id=self.project_id,
            tool_id="filesystem.read",
            arguments={"path": "README.md"},
        )
        self.assertEqual(decision_after.result.value, "ALLOW")

    def test_workflow_pause_resume_cancel(self):
        task = self.app.tasks.create_task(
            project_id=self.project_id,
            title="Workflow Task",
            objective="Workflow test",
        )

        wf = self.app._runtime.workflows.create_workflow(
            project_id=self.project_id,
            title="Deploy Workflow",
            objective="Deploy release tasks",
        )
        self.assertEqual(wf.status, WorkflowStatus.CREATED)

        # Pause
        paused = self.app.workflows.pause_workflow(wf.id, reason="Operator paused for review")
        self.assertEqual(paused["status"], "PAUSED")

        # Resume
        resumed = self.app.workflows.resume_workflow(wf.id, reason="Operator resumed")
        self.assertEqual(resumed["status"], "RUNNING")

        # Cancel
        cancelled = self.app.workflows.cancel_workflow(wf.id, reason="Operator cancelled")
        self.assertEqual(cancelled["status"], "CANCELLED")

    def test_provider_configuration_and_secure_keys(self):
        # Configure API key
        key_res = self.app.providers.set_provider_key("openrouter", "sk-or-v1-abcdef1234567890")
        self.assertTrue(key_res["configured"])
        self.assertEqual(key_res["masked_key"], "sk-••••••••7890")

        # Telemetry
        omni = self.app.providers.get_omniroute_status()
        self.assertIn("total_providers", omni)
        self.assertIn("total_models", omni)


if __name__ == "__main__":
    unittest.main()
