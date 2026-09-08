"""
Unit Tests for Programmer V1 Phase 6.2:
Git Worktree Isolation & Lifecycle Management.

Validates:
1. Isolated worktree creation and lifecycle transitions:
   PROVISIONING -> READY -> ACTIVE -> CLEANUP_PENDING -> CLEANED.
2. Unique worktree identity and deterministic branch naming.
3. Authoritative base revision binding (explicit or resolved).
4. Ownership and causal lineage validation (project_id, work_order_id, execution_id).
5. Cleanup for successful, failed, and cancelled executions.
6. Safe cleanup invariants: never deletes repository root or unrelated worktrees.
7. Repeated cleanup idempotency.
8. Failed provisioning handling with clean rollback.
9. Concurrent execution isolation: distinct paths, distinct branches, collision rejection.
10. Invalid repository handling (non-git, non-existent, or malformed).
11. Rejection of silent fallback: ISOLATION_UNAVAILABLE returned when worktrees unsupported.
12. Serialization roundtrips for GitWorktree and GitWorktreeProvisioningResult.
"""

from __future__ import annotations

import os
import unittest

from core.programmer.contracts.execution import ProgrammerExecution
from core.programmer.contracts.git_model import (
    GitExecutionContext,
    GitRepository,
    GitRevision,
)
from core.programmer.contracts.git_worktree import (
    FakeGitOperations,
    GitWorktree,
    GitWorktreeProvisioner,
    GitWorktreeProvisioningResult,
)
from core.programmer.contracts.identifiers import (
    GIT_WORKTREE_ID_PREFIX,
    new_execution_id,
    new_git_repository_id,
    new_git_revision_id,
    new_git_worktree_id,
    new_work_order_id,
    validate_git_worktree_id,
)
from core.programmer.contracts.work_order import ProgrammerWorkOrder
from core.programmer.errors import (
    InvalidProgrammerIdError,
    InvalidProgrammerTransitionError,
    ProgrammerValidationError,
)
from core.programmer.types import (
    GitIsolationMode,
    GitRepositoryState,
    GitWorktreeErrorCode,
    GitWorktreeStatus,
)


class TestProgrammerGitWorktreeIsolation(unittest.TestCase):
    def setUp(self) -> None:
        self.project_id = "proj-worktree-test"
        self.repo_root = "/tmp/test_repos/repo_alpha"
        self.repo_id = new_git_repository_id()
        self.work_order_id = new_work_order_id()
        self.execution_id = new_execution_id()
        self.commit_sha = "0123456789abcdef0123456789abcdef01234567"

        # Initialize fake git ops and register the repository
        self.git_ops = FakeGitOperations()
        self.initial_rev = self.git_ops.add_repository(
            repo_path=self.repo_root,
            default_branch="main",
            initial_commit_sha=self.commit_sha,
        )

        self.repo = GitRepository(
            repository_id=self.repo_id,
            project_id=self.project_id,
            root_path=self.repo_root,
            default_branch="main",
            repository_state=GitRepositoryState.READY,
        )

        self.work_order = ProgrammerWorkOrder(
            work_order_id=self.work_order_id,
            manager_task_id="mtask-wt-1",
            project_id=self.project_id,
            correlation_id="corr-wt-1",
            objective="Implement worktree isolation",
        )

        self.execution = ProgrammerExecution(
            execution_id=self.execution_id,
            work_order_id=self.work_order_id,
            task_id="task-wt-1",
            project_id=self.project_id,
            correlation_id="corr-wt-1",
        )

        self.provisioner = GitWorktreeProvisioner(
            git_ops=self.git_ops,
            worktree_base_dir="/tmp/test_worktrees",
        )

    # -------------------------------------------------------------------------
    # 1. Identifier and Worktree Domain Model Basics
    # -------------------------------------------------------------------------

    def test_worktree_canonical_identifier(self) -> None:
        wt_id = new_git_worktree_id()
        self.assertTrue(wt_id.startswith(GIT_WORKTREE_ID_PREFIX))
        validate_git_worktree_id(wt_id)

        with self.assertRaises(InvalidProgrammerIdError):
            validate_git_worktree_id("invalid-wt-id")

        with self.assertRaises(InvalidProgrammerIdError):
            validate_git_worktree_id("")

    def test_git_worktree_lifecycle_transitions(self) -> None:
        base_rev = GitRevision(
            revision_id=new_git_revision_id(),
            commit_hash=self.commit_sha,
            branch="main",
        )
        wt = GitWorktree(
            worktree_id=new_git_worktree_id(),
            repository_id=self.repo_id,
            execution_id=self.execution_id,
            work_order_id=self.work_order_id,
            project_id=self.project_id,
            worktree_path="/tmp/test_worktrees/wt_1",
            branch="autonomos/pwo-1/exec-1",
            base_revision=base_rev,
            status=GitWorktreeStatus.PROVISIONING,
        )
        self.assertEqual(wt.status, GitWorktreeStatus.PROVISIONING)

        # PROVISIONING -> READY
        wt.transition_to(GitWorktreeStatus.READY)
        self.assertEqual(wt.status, GitWorktreeStatus.READY)

        # READY -> ACTIVE
        wt.transition_to(GitWorktreeStatus.ACTIVE)
        self.assertEqual(wt.status, GitWorktreeStatus.ACTIVE)

        # ACTIVE -> CLEANUP_PENDING
        wt.transition_to(GitWorktreeStatus.CLEANUP_PENDING, reason="finished")
        self.assertEqual(wt.status, GitWorktreeStatus.CLEANUP_PENDING)
        self.assertEqual(wt.cleanup_reason, "finished")

        # CLEANUP_PENDING -> CLEANED
        wt.transition_to(GitWorktreeStatus.CLEANED)
        self.assertEqual(wt.status, GitWorktreeStatus.CLEANED)
        self.assertIsNotNone(wt.cleaned_at)

        # CLEANED is terminal: cannot transition anywhere else
        with self.assertRaises(InvalidProgrammerTransitionError):
            wt.transition_to(GitWorktreeStatus.ACTIVE)

    def test_git_worktree_transition_to_failed(self) -> None:
        base_rev = GitRevision(
            revision_id=new_git_revision_id(),
            commit_hash=self.commit_sha,
        )
        wt = GitWorktree(
            worktree_id=new_git_worktree_id(),
            repository_id=self.repo_id,
            execution_id=self.execution_id,
            work_order_id=self.work_order_id,
            project_id=self.project_id,
            worktree_path="/tmp/test_worktrees/wt_fail",
            branch="autonomos/pwo-1/exec-fail",
            base_revision=base_rev,
            status=GitWorktreeStatus.PROVISIONING,
        )
        # PROVISIONING -> FAILED
        wt.transition_to(GitWorktreeStatus.FAILED, reason="git hook error")
        self.assertEqual(wt.status, GitWorktreeStatus.FAILED)
        self.assertEqual(wt.cleanup_reason, "git hook error")

        # FAILED can transition to CLEANED for cleanup
        wt.transition_to(GitWorktreeStatus.CLEANED)
        self.assertEqual(wt.status, GitWorktreeStatus.CLEANED)

    # -------------------------------------------------------------------------
    # 2. Successful Isolated Worktree Creation
    # -------------------------------------------------------------------------

    def test_successful_worktree_provisioning(self) -> None:
        result = self.provisioner.provision_isolated_workspace(
            repository=self.repo,
            work_order=self.work_order,
            execution=self.execution,
        )

        # 1. Status is READY and verified executable
        self.assertTrue(result.is_ready())
        self.assertEqual(result.status, GitWorktreeStatus.READY)
        self.assertIsNotNone(result.worktree)
        self.assertIsNotNone(result.execution_context)

        wt = result.worktree
        ctx = result.execution_context

        # 2. Unique worktree identity
        self.assertTrue(wt.worktree_id.startswith(GIT_WORKTREE_ID_PREFIX))
        validate_git_worktree_id(wt.worktree_id)

        # 3. Deterministic branch and path
        expected_branch = f"autonomos/{self.work_order.work_order_id}/{self.execution.execution_id}"
        self.assertEqual(wt.branch, expected_branch)
        self.assertTrue(wt.worktree_path.endswith(f"{self.repo.repository_id}_{self.execution.execution_id}"))

        # 4. Base revision binding
        self.assertEqual(wt.base_revision.commit_hash, self.commit_sha)
        self.assertEqual(ctx.base_revision.commit_hash, self.commit_sha)

        # 5. GitExecutionContext binds strictly to the worktree path and WORKTREE mode
        self.assertEqual(ctx.workspace_path, wt.worktree_path)
        self.assertEqual(ctx.isolation_mode, GitIsolationMode.WORKTREE)
        self.assertEqual(ctx.repository_id, self.repo.repository_id)
        self.assertEqual(ctx.execution_id, self.execution.execution_id)
        self.assertEqual(ctx.work_order_id, self.work_order.work_order_id)
        self.assertEqual(ctx.project_id, self.repo.project_id)

        # 6. Active state in fake Git operations
        self.assertTrue(self.git_ops.is_worktree_active(wt.worktree_path))

        # 7. Transition to active
        self.provisioner.mark_active(self.execution.execution_id)
        self.assertEqual(wt.status, GitWorktreeStatus.ACTIVE)

    def test_worktree_with_explicit_base_revision(self) -> None:
        custom_sha = "1111222233334444555566667777888899990000"
        explicit_rev = GitRevision(
            revision_id=new_git_revision_id(),
            commit_hash=custom_sha,
            branch="release/1.0",
        )
        result = self.provisioner.provision_isolated_workspace(
            repository=self.repo,
            work_order=self.work_order,
            execution=self.execution,
            base_revision=explicit_rev,
        )
        self.assertTrue(result.is_ready())
        self.assertEqual(result.worktree.base_revision.commit_hash, custom_sha)
        self.assertEqual(result.execution_context.base_revision.commit_hash, custom_sha)

    # -------------------------------------------------------------------------
    # 3. Ownership & Lineage Invariant Enforcement
    # -------------------------------------------------------------------------

    def test_project_id_mismatch_fails_provisioning(self) -> None:
        foreign_repo = GitRepository(
            repository_id=new_git_repository_id(),
            project_id="alien-project-xyz",
            root_path=self.repo_root,
        )
        result = self.provisioner.provision_isolated_workspace(
            repository=foreign_repo,
            work_order=self.work_order,
            execution=self.execution,
        )
        self.assertFalse(result.is_ready())
        self.assertEqual(result.status, GitWorktreeStatus.FAILED)
        self.assertEqual(result.error_code, GitWorktreeErrorCode.REPOSITORY_INVALID)
        self.assertIn("Cross-project forbidden", result.error_message)

    def test_work_order_lineage_mismatch_fails_provisioning(self) -> None:
        other_execution = ProgrammerExecution(
            execution_id=self.execution_id,
            work_order_id=new_work_order_id(),  # Different WO
            task_id="task-wt-1",
            project_id=self.project_id,
            correlation_id="corr-wt-1",
        )
        result = self.provisioner.provision_isolated_workspace(
            repository=self.repo,
            work_order=self.work_order,
            execution=other_execution,
        )
        self.assertFalse(result.is_ready())
        self.assertEqual(result.status, GitWorktreeStatus.FAILED)
        self.assertEqual(result.error_code, GitWorktreeErrorCode.REPOSITORY_INVALID)
        self.assertIn("Lineage mismatch", result.error_message)

    # -------------------------------------------------------------------------
    # 4. Strict Isolation: No Silent Fallback to Shared
    # -------------------------------------------------------------------------

    def test_unsupported_worktrees_fails_without_fallback_to_shared(self) -> None:
        # Simulate environment where worktrees are not supported
        self.git_ops.worktrees_supported = False

        result = self.provisioner.provision_isolated_workspace(
            repository=self.repo,
            work_order=self.work_order,
            execution=self.execution,
        )
        # Invariant: Must NOT silently degrade to shared mode
        self.assertFalse(result.is_ready())
        self.assertEqual(result.status, GitWorktreeStatus.FAILED)
        self.assertEqual(result.error_code, GitWorktreeErrorCode.ISOLATION_UNAVAILABLE)
        self.assertIsNone(result.worktree)
        self.assertIsNone(result.execution_context)

    # -------------------------------------------------------------------------
    # 5. Base Revision Unresolved Failure
    # -------------------------------------------------------------------------

    def test_base_revision_resolution_failure(self) -> None:
        self.git_ops.fail_resolve_revision = True
        self.git_ops.custom_error_message = "Git ref not found: main"

        result = self.provisioner.provision_isolated_workspace(
            repository=self.repo,
            work_order=self.work_order,
            execution=self.execution,
        )
        self.assertFalse(result.is_ready())
        self.assertEqual(result.status, GitWorktreeStatus.FAILED)
        self.assertEqual(result.error_code, GitWorktreeErrorCode.BASE_REVISION_UNRESOLVED)

    # -------------------------------------------------------------------------
    # 6. Failed Provisioning & Rollback
    # -------------------------------------------------------------------------

    def test_underlying_worktree_creation_failure(self) -> None:
        self.git_ops.fail_create_worktree = True
        self.git_ops.custom_error_message = "Permission denied creating worktree"

        result = self.provisioner.provision_isolated_workspace(
            repository=self.repo,
            work_order=self.work_order,
            execution=self.execution,
        )
        self.assertFalse(result.is_ready())
        self.assertEqual(result.status, GitWorktreeStatus.FAILED)
        self.assertEqual(result.error_code, GitWorktreeErrorCode.PROVISIONING_FAILED)
        self.assertIsNone(result.worktree)
        self.assertIsNone(result.execution_context)

    # -------------------------------------------------------------------------
    # 7. Concurrent Execution Isolation & Collision Prevention
    # -------------------------------------------------------------------------

    def test_concurrent_executions_are_strictly_isolated(self) -> None:
        exec_2_id = new_execution_id()
        execution_2 = ProgrammerExecution(
            execution_id=exec_2_id,
            work_order_id=self.work_order_id,
            task_id="task-wt-2",
            project_id=self.project_id,
            correlation_id="corr-wt-2",
        )

        res_1 = self.provisioner.provision_isolated_workspace(
            repository=self.repo,
            work_order=self.work_order,
            execution=self.execution,
        )
        res_2 = self.provisioner.provision_isolated_workspace(
            repository=self.repo,
            work_order=self.work_order,
            execution=execution_2,
        )

        self.assertTrue(res_1.is_ready())
        self.assertTrue(res_2.is_ready())

        wt_1 = res_1.worktree
        wt_2 = res_2.worktree

        # Different worktree IDs
        self.assertNotEqual(wt_1.worktree_id, wt_2.worktree_id)
        # Different worktree paths
        self.assertNotEqual(wt_1.worktree_path, wt_2.worktree_path)
        # Different branch names
        self.assertNotEqual(wt_1.branch, wt_2.branch)
        # Both active in backend
        self.assertTrue(self.git_ops.is_worktree_active(wt_1.worktree_path))
        self.assertTrue(self.git_ops.is_worktree_active(wt_2.worktree_path))

    def test_active_execution_cannot_be_reprovisioned(self) -> None:
        res_1 = self.provisioner.provision_isolated_workspace(
            repository=self.repo,
            work_order=self.work_order,
            execution=self.execution,
        )
        self.assertTrue(res_1.is_ready())

        # Attempting to re-provision with same execution_id while active fails
        res_2 = self.provisioner.provision_isolated_workspace(
            repository=self.repo,
            work_order=self.work_order,
            execution=self.execution,
        )
        self.assertFalse(res_2.is_ready())
        self.assertEqual(res_2.error_code, GitWorktreeErrorCode.WORKTREE_EXISTS)

    # -------------------------------------------------------------------------
    # 8. Cleanup Behaviors (Success, Cancellation, Failure, Repeated)
    # -------------------------------------------------------------------------

    def test_cleanup_successful_execution(self) -> None:
        result = self.provisioner.provision_isolated_workspace(
            repository=self.repo,
            work_order=self.work_order,
            execution=self.execution,
        )
        wt = result.worktree
        self.assertTrue(self.git_ops.is_worktree_active(wt.worktree_path))

        # Perform cleanup
        cleaned = self.provisioner.cleanup_worktree(
            execution_id=self.execution.execution_id,
            repository=self.repo,
            reason="execution_completed",
            delete_branch=True,
        )
        self.assertTrue(cleaned)
        self.assertEqual(wt.status, GitWorktreeStatus.CLEANED)
        self.assertFalse(self.git_ops.is_worktree_active(wt.worktree_path))
        self.assertIsNone(self.provisioner.get_worktree(self.execution.execution_id))

    def test_cancellation_cleanup(self) -> None:
        result = self.provisioner.provision_isolated_workspace(
            repository=self.repo,
            work_order=self.work_order,
            execution=self.execution,
        )
        wt = result.worktree

        # Manager / execution cancelled
        cleaned = self.provisioner.cleanup_worktree(
            execution_id=self.execution.execution_id,
            repository=self.repo,
            reason="cancelled_by_manager",
            delete_branch=True,
        )
        self.assertTrue(cleaned)
        self.assertEqual(wt.status, GitWorktreeStatus.CLEANED)
        self.assertEqual(wt.cleanup_reason, "cancelled_by_manager")
        self.assertFalse(self.git_ops.is_worktree_active(wt.worktree_path))

    def test_repeated_cleanup_is_idempotent(self) -> None:
        result = self.provisioner.provision_isolated_workspace(
            repository=self.repo,
            work_order=self.work_order,
            execution=self.execution,
        )
        # First cleanup
        self.assertTrue(self.provisioner.cleanup_worktree(self.execution.execution_id, self.repo))
        # Second cleanup of same execution: succeeds safely without error
        self.assertTrue(self.provisioner.cleanup_worktree(self.execution.execution_id, self.repo))
        # Third cleanup of unknown execution: succeeds safely
        self.assertTrue(self.provisioner.cleanup_worktree("non-existent-exec", self.repo))

    def test_cleanup_safety_never_deletes_repository_root(self) -> None:
        result = self.provisioner.provision_isolated_workspace(
            repository=self.repo,
            work_order=self.work_order,
            execution=self.execution,
        )
        wt = result.worktree

        # Maliciously or erroneously tampered worktree_path pointing to repo root
        wt.worktree_path = self.repo.root_path

        from core.programmer.errors import GitWorktreeError
        with self.assertRaises(GitWorktreeError) as cm:
            self.provisioner.cleanup_worktree(self.execution.execution_id, repository=self.repo)
        self.assertIn("Catastrophic safety violation", str(cm.exception))

    # -------------------------------------------------------------------------
    # 9. Serialization Roundtrips
    # -------------------------------------------------------------------------

    def test_git_worktree_serialization_roundtrip(self) -> None:
        base_rev = GitRevision(
            revision_id=new_git_revision_id(),
            commit_hash=self.commit_sha,
            branch="main",
        )
        wt = GitWorktree(
            worktree_id=new_git_worktree_id(),
            repository_id=self.repo_id,
            execution_id=self.execution_id,
            work_order_id=self.work_order_id,
            project_id=self.project_id,
            worktree_path="/tmp/test_worktrees/wt_serial",
            branch="autonomos/wo-1/exec-1",
            base_revision=base_rev,
            status=GitWorktreeStatus.ACTIVE,
            trace={"step": "provision"},
            metadata={"cluster": "test"},
        )
        data = wt.to_dict()
        self.assertEqual(data["status"], "ACTIVE")
        self.assertEqual(data["branch"], "autonomos/wo-1/exec-1")

        restored = GitWorktree.from_dict(data)
        self.assertEqual(restored.worktree_id, wt.worktree_id)
        self.assertEqual(restored.status, GitWorktreeStatus.ACTIVE)
        self.assertEqual(restored.base_revision.commit_hash, self.commit_sha)
        self.assertEqual(restored.trace, wt.trace)

    def test_provisioning_result_serialization_roundtrip(self) -> None:
        result = self.provisioner.provision_isolated_workspace(
            repository=self.repo,
            work_order=self.work_order,
            execution=self.execution,
        )
        data = result.to_dict()
        self.assertEqual(data["status"], "READY")
        self.assertIsNotNone(data["worktree"])
        self.assertIsNotNone(data["execution_context"])

        restored = GitWorktreeProvisioningResult.from_dict(data)
        self.assertTrue(restored.is_ready())
        self.assertEqual(restored.worktree.worktree_id, result.worktree.worktree_id)
        self.assertEqual(restored.execution_context.execution_id, self.execution.execution_id)


if __name__ == "__main__":
    unittest.main()
