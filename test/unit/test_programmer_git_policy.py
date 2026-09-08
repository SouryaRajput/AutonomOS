"""
Unit Tests for Programmer V1 Phase 6.3:
Git Operation Policy & Governance.

Adversarial & Safety Tests:
1. Allowed read operations (status, diff, log, show, branch inspection).
2. Denied destructive operations (reset, clean, checkout discard, branch delete).
3. Denied force push (flag-based --force, -f, or refspec +).
4. Denied protected branch mutation (direct commits, branch deletion, or push to main/master).
5. Unauthorized remote operations for Cline (fetch, push, pull).
6. Malformed Git requests (empty, unparseable, missing operation).
7. Execution from wrong repository (repository_id mismatch).
8. Cross-execution access (execution_id mismatch).
9. Filesystem boundary preservation (target path outside workspace).
10. WorkOrder constraint preservation (target path in forbidden_paths).
11. Permissive local change policy (add, commit, branch_create allowed when authorized).
12. Serialization fidelity for GitDecision.
"""

from __future__ import annotations

import unittest

from core.programmer.contracts.git_model import (
    GitExecutionContext,
    GitRevision,
)
from core.programmer.contracts.git_policy import (
    GitDecision,
    GitOperationCategory,
    GitOperationPolicy,
    GitOperationRequest,
    GitOperationType,
    GitPolicyEvaluator,
    OPERATION_TO_CATEGORY,
)
from core.programmer.contracts.identifiers import (
    new_execution_id,
    new_git_repository_id,
    new_git_revision_id,
    new_work_order_id,
)
from core.programmer.contracts.work_order import ProgrammerWorkOrder
from core.programmer.types import (
    GitIsolationMode,
)


class TestProgrammerGitOperationPolicy(unittest.TestCase):
    def setUp(self) -> None:
        self.project_id = "proj-git-policy-test"
        self.repo_id = new_git_repository_id()
        self.work_order_id = new_work_order_id()
        self.execution_id = new_execution_id()
        self.commit_sha = "0123456789abcdef0123456789abcdef01234567"
        self.workspace_path = "/tmp/test_worktrees/gwt-ws-01"

        self.base_revision = GitRevision(
            revision_id=new_git_revision_id(),
            commit_hash=self.commit_sha,
            branch="feature/test-1",
        )

        self.execution_context = GitExecutionContext(
            repository_id=self.repo_id,
            execution_id=self.execution_id,
            work_order_id=self.work_order_id,
            project_id=self.project_id,
            base_revision=self.base_revision,
            workspace_path=self.workspace_path,
            isolation_mode=GitIsolationMode.WORKTREE,
            branch="feature/test-1",
            reference="refs/heads/feature/test-1",
        )

        self.work_order = ProgrammerWorkOrder(
            work_order_id=self.work_order_id,
            manager_task_id="mtask-policy-1",
            project_id=self.project_id,
            correlation_id="corr-policy-1",
            objective="Enforce Git operation policies",
            allowed_paths=["src/", "test/"],
            forbidden_paths=["secrets.env", "config/credentials.json"],
        )

        self.evaluator = GitPolicyEvaluator()

    # -------------------------------------------------------------------------
    # 1. Operation Categorization
    # -------------------------------------------------------------------------

    def test_operation_category_mapping(self) -> None:
        # READ
        self.assertEqual(OPERATION_TO_CATEGORY[GitOperationType.STATUS], GitOperationCategory.READ)
        self.assertEqual(OPERATION_TO_CATEGORY[GitOperationType.DIFF], GitOperationCategory.READ)
        self.assertEqual(OPERATION_TO_CATEGORY[GitOperationType.LOG], GitOperationCategory.READ)
        self.assertEqual(OPERATION_TO_CATEGORY[GitOperationType.SHOW], GitOperationCategory.READ)
        self.assertEqual(OPERATION_TO_CATEGORY[GitOperationType.BRANCH_INFO], GitOperationCategory.READ)

        # LOCAL CHANGE MANAGEMENT
        self.assertEqual(OPERATION_TO_CATEGORY[GitOperationType.ADD], GitOperationCategory.LOCAL_CHANGE_MANAGEMENT)
        self.assertEqual(OPERATION_TO_CATEGORY[GitOperationType.COMMIT], GitOperationCategory.LOCAL_CHANGE_MANAGEMENT)
        self.assertEqual(OPERATION_TO_CATEGORY[GitOperationType.BRANCH_CREATE], GitOperationCategory.LOCAL_CHANGE_MANAGEMENT)

        # DESTRUCTIVE
        self.assertEqual(OPERATION_TO_CATEGORY[GitOperationType.RESET], GitOperationCategory.DESTRUCTIVE)
        self.assertEqual(OPERATION_TO_CATEGORY[GitOperationType.CLEAN], GitOperationCategory.DESTRUCTIVE)
        self.assertEqual(OPERATION_TO_CATEGORY[GitOperationType.CHECKOUT_DISCARD], GitOperationCategory.DESTRUCTIVE)
        self.assertEqual(OPERATION_TO_CATEGORY[GitOperationType.BRANCH_DELETE], GitOperationCategory.DESTRUCTIVE)

        # REMOTE
        self.assertEqual(OPERATION_TO_CATEGORY[GitOperationType.FETCH], GitOperationCategory.REMOTE)
        self.assertEqual(OPERATION_TO_CATEGORY[GitOperationType.PUSH], GitOperationCategory.REMOTE)
        self.assertEqual(OPERATION_TO_CATEGORY[GitOperationType.PULL], GitOperationCategory.REMOTE)

    # -------------------------------------------------------------------------
    # 2. Allowed Read Operations (Conservative Default)
    # -------------------------------------------------------------------------

    def test_allowed_read_operations(self) -> None:
        read_commands = [
            "git status",
            "git status --short",
            "git diff",
            "git diff HEAD~1",
            "git log -n 5",
            "git show HEAD",
            "git branch",
            "git branch --list",
        ]
        for cmd in read_commands:
            req = GitOperationRequest.from_command_string(
                cmd,
                execution_id=self.execution_id,
                work_order_id=self.work_order_id,
                repository_id=self.repo_id,
                project_id=self.project_id,
            )
            decision = self.evaluator.evaluate(req, self.execution_context, self.work_order)
            self.assertTrue(decision.allowed, f"Expected read command '{cmd}' to be allowed.")
            self.assertEqual(decision.matched_policy, "DEFAULT_READ_ALLOWED")

    # -------------------------------------------------------------------------
    # 3. Denied Destructive Operations (Conservative Default)
    # -------------------------------------------------------------------------

    def test_denied_destructive_operations(self) -> None:
        destructive_commands = [
            "git reset --hard HEAD~1",
            "git reset --soft HEAD~1",
            "git clean -fd",
            "git checkout -- .",
            "git branch -D feature/old",
            "git branch -d feature/old",
        ]
        for cmd in destructive_commands:
            req = GitOperationRequest.from_command_string(
                cmd,
                execution_id=self.execution_id,
                work_order_id=self.work_order_id,
                repository_id=self.repo_id,
                project_id=self.project_id,
            )
            decision = self.evaluator.evaluate(req, self.execution_context, self.work_order)
            self.assertFalse(decision.allowed, f"Expected destructive command '{cmd}' to be denied.")
            self.assertEqual(decision.matched_policy, "DESTRUCTIVE_OPERATION_DENIED")

    # -------------------------------------------------------------------------
    # 4. Hard Safety Denials: Force Push
    # -------------------------------------------------------------------------

    def test_denied_force_push_under_all_policies(self) -> None:
        permissive_policy = GitOperationPolicy(
            allow_read=True,
            allow_add=True,
            allow_commit=True,
            allow_branch_creation=True,
            allow_remote=True,
            allow_destructive=True,
        )

        force_push_commands = [
            "git push --force origin feature/test-1",
            "git push -f origin feature/test-1",
            "git push --force-with-lease origin feature/test-1",
            "git push origin +feature/test-1",
        ]
        for cmd in force_push_commands:
            req = GitOperationRequest.from_command_string(
                cmd,
                execution_id=self.execution_id,
                work_order_id=self.work_order_id,
                repository_id=self.repo_id,
                project_id=self.project_id,
                caller_role="programmer",
            )
            decision = self.evaluator.evaluate(
                req,
                self.execution_context,
                self.work_order,
                policy=permissive_policy,
            )
            self.assertFalse(decision.allowed, f"Expected force push '{cmd}' to be strictly denied.")
            self.assertEqual(decision.matched_policy, "FORCE_PUSH_FORBIDDEN")

    # -------------------------------------------------------------------------
    # 5. Hard Safety Denials: Remote Deletion
    # -------------------------------------------------------------------------

    def test_denied_remote_branch_deletion(self) -> None:
        remote_delete_commands = [
            "git push origin --delete feature/test-1",
            "git push origin -d feature/test-1",
            "git push origin :feature/test-1",
        ]
        for cmd in remote_delete_commands:
            req = GitOperationRequest.from_command_string(
                cmd,
                execution_id=self.execution_id,
                work_order_id=self.work_order_id,
                repository_id=self.repo_id,
                project_id=self.project_id,
                caller_role="programmer",
            )
            decision = self.evaluator.evaluate(req, self.execution_context, self.work_order)
            self.assertFalse(decision.allowed, f"Expected remote deletion '{cmd}' to be strictly denied.")
            self.assertEqual(decision.matched_policy, "REMOTE_DELETION_FORBIDDEN")

    # -------------------------------------------------------------------------
    # 6. Hard Safety Denials: Protected Branch Mutation
    # -------------------------------------------------------------------------

    def test_denied_protected_branch_mutation(self) -> None:
        # Context set to protected 'main' branch
        protected_context = GitExecutionContext(
            repository_id=self.repo_id,
            execution_id=self.execution_id,
            work_order_id=self.work_order_id,
            project_id=self.project_id,
            base_revision=self.base_revision,
            workspace_path=self.workspace_path,
            branch="main",
        )

        mutation_commands = [
            "git commit -m 'Direct commit to main'",
            "git push origin main",
            "git branch -D main",
            "git reset --hard HEAD~1",
        ]
        for cmd in mutation_commands:
            req = GitOperationRequest.from_command_string(
                cmd,
                execution_id=self.execution_id,
                work_order_id=self.work_order_id,
                repository_id=self.repo_id,
                project_id=self.project_id,
            )
            decision = self.evaluator.evaluate(
                req,
                protected_context,
                self.work_order,
                policy=GitOperationPolicy.allow_local_changes(),
            )
            self.assertFalse(decision.allowed, f"Expected protected branch mutation '{cmd}' to be denied.")
            self.assertEqual(decision.matched_policy, "PROTECTED_BRANCH_MUTATION_DENIED")

    # -------------------------------------------------------------------------
    # 7. Unauthorized Remote Operations for Cline
    # -------------------------------------------------------------------------

    def test_unauthorized_remote_operation_for_cline(self) -> None:
        remote_commands = [
            "git fetch origin",
            "git pull origin main",
            "git push origin feature/test-1",
        ]
        for cmd in remote_commands:
            req = GitOperationRequest.from_command_string(
                cmd,
                execution_id=self.execution_id,
                work_order_id=self.work_order_id,
                repository_id=self.repo_id,
                project_id=self.project_id,
                caller_role="cline",
            )
            decision = self.evaluator.evaluate(req, self.execution_context, self.work_order)
            self.assertFalse(decision.allowed, f"Expected remote op '{cmd}' to be denied for Cline.")
            self.assertEqual(decision.matched_policy, "UNAUTHORIZED_REMOTE_OPERATION")

    # -------------------------------------------------------------------------
    # 8. Malformed Git Request
    # -------------------------------------------------------------------------

    def test_malformed_git_requests(self) -> None:
        malformed_inputs = [
            "",
            "   ",
            "git",
            "git 'unclosed quote",
        ]
        for cmd in malformed_inputs:
            req = GitOperationRequest.from_command_string(
                cmd,
                execution_id=self.execution_id,
                work_order_id=self.work_order_id,
            )
            decision = self.evaluator.evaluate(req, self.execution_context, self.work_order)
            self.assertFalse(decision.allowed)
            self.assertEqual(decision.matched_policy, "MALFORMED_REQUEST")

    # -------------------------------------------------------------------------
    # 9. Execution From Wrong Repository & Cross-Execution Access
    # -------------------------------------------------------------------------

    def test_execution_from_wrong_repository_denied(self) -> None:
        req = GitOperationRequest.from_command_string(
            "git status",
            execution_id=self.execution_id,
            work_order_id=self.work_order_id,
            repository_id="grepo-alien-repo",  # Wrong repository
            project_id=self.project_id,
        )
        decision = self.evaluator.evaluate(req, self.execution_context, self.work_order)
        self.assertFalse(decision.allowed)
        self.assertEqual(decision.matched_policy, "WRONG_REPOSITORY_DENIED")

    def test_cross_execution_access_denied(self) -> None:
        req = GitOperationRequest.from_command_string(
            "git status",
            execution_id="pexec-foreign-id",  # Wrong execution
            work_order_id=self.work_order_id,
            repository_id=self.repo_id,
            project_id=self.project_id,
        )
        decision = self.evaluator.evaluate(req, self.execution_context, self.work_order)
        self.assertFalse(decision.allowed)
        self.assertEqual(decision.matched_policy, "CROSS_EXECUTION_DENIED")

    def test_cross_project_access_denied(self) -> None:
        req = GitOperationRequest.from_command_string(
            "git status",
            execution_id=self.execution_id,
            work_order_id=self.work_order_id,
            repository_id=self.repo_id,
            project_id="proj-alien-org",  # Wrong project
        )
        decision = self.evaluator.evaluate(req, self.execution_context, self.work_order)
        self.assertFalse(decision.allowed)
        self.assertEqual(decision.matched_policy, "CROSS_PROJECT_DENIED")

    # -------------------------------------------------------------------------
    # 10. Filesystem Boundary & WorkOrder Constraint Preservation
    # -------------------------------------------------------------------------

    def test_filesystem_boundary_violation_denied(self) -> None:
        # Request with path escaping workspace root
        req = GitOperationRequest(
            operation=GitOperationType.ADD,
            target_path="/etc/passwd",
            execution_id=self.execution_id,
            work_order_id=self.work_order_id,
            repository_id=self.repo_id,
            project_id=self.project_id,
        )
        decision = self.evaluator.evaluate(
            req,
            self.execution_context,
            self.work_order,
            policy=GitOperationPolicy.allow_local_changes(),
        )
        self.assertFalse(decision.allowed)
        self.assertEqual(decision.matched_policy, "FILESYSTEM_BOUNDARY_VIOLATION")

    def test_workorder_forbidden_path_denied(self) -> None:
        # Request attempting to add forbidden file 'secrets.env'
        req = GitOperationRequest(
            operation=GitOperationType.ADD,
            target_path="secrets.env",
            execution_id=self.execution_id,
            work_order_id=self.work_order_id,
            repository_id=self.repo_id,
            project_id=self.project_id,
        )
        decision = self.evaluator.evaluate(
            req,
            self.execution_context,
            self.work_order,
            policy=GitOperationPolicy.allow_local_changes(),
        )
        self.assertFalse(decision.allowed)
        self.assertEqual(decision.matched_policy, "WORKORDER_BOUNDARY_VIOLATION")

    # -------------------------------------------------------------------------
    # 11. Permissive Local Change Policy
    # -------------------------------------------------------------------------

    def test_local_change_management_under_permissive_policy(self) -> None:
        permissive = GitOperationPolicy.allow_local_changes()

        # Add valid file inside workspace
        req_add = GitOperationRequest(
            operation=GitOperationType.ADD,
            target_path="src/main.py",
            execution_id=self.execution_id,
            work_order_id=self.work_order_id,
            repository_id=self.repo_id,
            project_id=self.project_id,
        )
        dec_add = self.evaluator.evaluate(req_add, self.execution_context, self.work_order, policy=permissive)
        self.assertTrue(dec_add.allowed)
        self.assertEqual(dec_add.matched_policy, "LOCAL_CHANGE_POLICY_PERMITTED")

        # Commit on feature branch
        req_commit = GitOperationRequest.from_command_string(
            "git commit -m 'Implement feature'",
            execution_id=self.execution_id,
            work_order_id=self.work_order_id,
            repository_id=self.repo_id,
            project_id=self.project_id,
        )
        dec_commit = self.evaluator.evaluate(req_commit, self.execution_context, self.work_order, policy=permissive)
        self.assertTrue(dec_commit.allowed)
        self.assertEqual(dec_commit.matched_policy, "LOCAL_CHANGE_POLICY_PERMITTED")

        # Create branch
        req_branch = GitOperationRequest(
            operation=GitOperationType.BRANCH_CREATE,
            target_branch="feature/subtask-2",
            execution_id=self.execution_id,
            work_order_id=self.work_order_id,
            repository_id=self.repo_id,
            project_id=self.project_id,
        )
        dec_branch = self.evaluator.evaluate(req_branch, self.execution_context, self.work_order, policy=permissive)
        self.assertTrue(dec_branch.allowed)
        self.assertEqual(dec_branch.matched_policy, "LOCAL_CHANGE_POLICY_PERMITTED")

    def test_local_changes_denied_under_conservative_default(self) -> None:
        conservative = GitOperationPolicy.conservative_default()

        req_add = GitOperationRequest(
            operation=GitOperationType.ADD,
            target_path="src/main.py",
            execution_id=self.execution_id,
            work_order_id=self.work_order_id,
            repository_id=self.repo_id,
            project_id=self.project_id,
        )
        dec_add = self.evaluator.evaluate(req_add, self.execution_context, self.work_order, policy=conservative)
        self.assertFalse(dec_add.allowed)
        self.assertEqual(dec_add.matched_policy, "LOCAL_CHANGE_POLICY_REQUIRED")

        req_commit = GitOperationRequest(
            operation=GitOperationType.COMMIT,
            execution_id=self.execution_id,
            work_order_id=self.work_order_id,
            repository_id=self.repo_id,
            project_id=self.project_id,
        )
        dec_commit = self.evaluator.evaluate(req_commit, self.execution_context, self.work_order, policy=conservative)
        self.assertFalse(dec_commit.allowed)
        self.assertEqual(dec_commit.matched_policy, "LOCAL_CHANGE_POLICY_REQUIRED")

    # -------------------------------------------------------------------------
    # 12. Serialization Roundtrip
    # -------------------------------------------------------------------------

    def test_git_decision_serialization_roundtrip(self) -> None:
        decision = GitDecision(
            allowed=False,
            operation="push",
            reason="Force push is strictly forbidden.",
            matched_policy="FORCE_PUSH_FORBIDDEN",
            execution_id=self.execution_id,
            work_order_id=self.work_order_id,
            trace={"evaluator": "v1", "depth": 2},
            metadata={"source": "cli"},
        )
        data = decision.to_dict()
        self.assertFalse(data["allowed"])
        self.assertEqual(data["matched_policy"], "FORCE_PUSH_FORBIDDEN")
        self.assertEqual(data["trace"]["depth"], 2)

        restored = GitDecision.from_dict(data)
        self.assertEqual(restored.allowed, decision.allowed)
        self.assertEqual(restored.operation, decision.operation)
        self.assertEqual(restored.reason, decision.reason)
        self.assertEqual(restored.matched_policy, decision.matched_policy)
        self.assertEqual(restored.execution_id, decision.execution_id)
        self.assertEqual(restored.work_order_id, decision.work_order_id)
        self.assertEqual(restored.trace, decision.trace)
        self.assertEqual(restored.metadata, decision.metadata)

        # to_json test
        json_str = decision.to_json()
        self.assertIn("FORCE_PUSH_FORBIDDEN", json_str)


if __name__ == "__main__":
    unittest.main()
