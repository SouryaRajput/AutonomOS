"""
Unit Tests for Programmer V1 Phase 6.4:
Change Set & Commit Management.

Validates:
1. Canonical ChangeSet identifier generation and validation (pcs-*).
2. Working tree change inspection (files changed, created, deleted, diff summary).
3. Empty change set detection (ChangeSetStatus.EMPTY).
4. Local isolated commit creation under permissive policy.
5. Multiple sequential commits tracking in commit_references.
6. Commit lineage preservation (Execution-ID, Work-Order-ID trailers).
7. Invalid repository state handling (ChangeSetStatus.INVALID).
8. Commit failure handling (ChangeSetStatus.FAILED).
9. Unauthorized commit rejection (conservative policy enforcement).
10. Protected branch mutation protection (cannot commit directly to main/master).
11. Commit is NOT approval invariant (no automatic acceptance side effects).
12. Serialization fidelity roundtrip for ChangeSet.
"""

from __future__ import annotations

import unittest

from core.programmer.contracts.git_change_set import (
    ChangeSet,
    GitChangeSetManager,
)
from core.programmer.contracts.git_model import (
    GitExecutionContext,
    GitRepository,
    GitRevision,
)
from core.programmer.contracts.git_policy import (
    GitOperationPolicy,
    GitPolicyEvaluator,
)
from core.programmer.contracts.git_worktree import (
    FakeGitOperations,
)
from core.programmer.contracts.identifiers import (
    CHANGESET_ID_PREFIX,
    new_change_set_id,
    new_execution_id,
    new_git_repository_id,
    new_git_revision_id,
    new_work_order_id,
    validate_change_set_id,
)
from core.programmer.contracts.work_order import ProgrammerWorkOrder
from core.programmer.errors import (
    InvalidProgrammerIdError,
    ProgrammerValidationError,
)
from core.programmer.types import (
    ChangeSetStatus,
    GitIsolationMode,
    GitRepositoryState,
)


class TestProgrammerGitChangeSet(unittest.TestCase):
    def setUp(self) -> None:
        self.project_id = "proj-changeset-test"
        self.repo_id = new_git_repository_id()
        self.work_order_id = new_work_order_id()
        self.execution_id = new_execution_id()
        self.commit_sha = "0123456789abcdef0123456789abcdef01234567"
        self.workspace_path = "/tmp/test_worktrees/gwt-cs-01"

        self.git_ops = FakeGitOperations()
        self.initial_rev = self.git_ops.add_repository(
            repo_path="/tmp/test_repos/alpha",
            default_branch="main",
            initial_commit_sha=self.commit_sha,
        )

        # Register workspace worktree in fake git
        self.git_ops.worktrees[self.workspace_path] = {
            "repo_path": "/tmp/test_repos/alpha",
            "branch": "feature/changeset-1",
            "base_commit": self.commit_sha,
        }

        self.base_revision = GitRevision(
            revision_id=new_git_revision_id(),
            commit_hash=self.commit_sha,
            branch="feature/changeset-1",
        )

        self.execution_context = GitExecutionContext(
            repository_id=self.repo_id,
            execution_id=self.execution_id,
            work_order_id=self.work_order_id,
            project_id=self.project_id,
            base_revision=self.base_revision,
            workspace_path=self.workspace_path,
            isolation_mode=GitIsolationMode.WORKTREE,
            branch="feature/changeset-1",
            reference="refs/heads/feature/changeset-1",
        )

        self.work_order = ProgrammerWorkOrder(
            work_order_id=self.work_order_id,
            manager_task_id="mtask-cs-1",
            project_id=self.project_id,
            correlation_id="corr-cs-1",
            objective="Manage Git Change Sets",
            allowed_paths=["src/", "test/"],
            forbidden_paths=["secrets.env"],
        )

        self.policy_evaluator = GitPolicyEvaluator()
        self.manager = GitChangeSetManager(
            git_ops=self.git_ops,
            policy_evaluator=self.policy_evaluator,
        )

    # -------------------------------------------------------------------------
    # 1. Identifier Validation
    # -------------------------------------------------------------------------

    def test_canonical_changeset_identifier(self) -> None:
        cs_id = new_change_set_id()
        self.assertTrue(cs_id.startswith(CHANGESET_ID_PREFIX))
        validate_change_set_id(cs_id)

        with self.assertRaises(InvalidProgrammerIdError):
            validate_change_set_id("invalid-cs-id")

        with self.assertRaises(InvalidProgrammerIdError):
            validate_change_set_id("")

    # -------------------------------------------------------------------------
    # 2. Empty Change Set Detection
    # -------------------------------------------------------------------------

    def test_empty_change_set_detection(self) -> None:
        # Clean workspace, no commits beyond base
        cs = self.manager.capture_change_set(self.execution_context, self.work_order)
        self.assertEqual(cs.status, ChangeSetStatus.EMPTY)
        self.assertTrue(cs.is_empty())
        self.assertEqual(cs.total_files_affected(), 0)
        self.assertEqual(cs.base_revision.commit_hash, self.commit_sha)
        self.assertIsNone(cs.resulting_revision)

    # -------------------------------------------------------------------------
    # 3. Changed Files Inspection
    # -------------------------------------------------------------------------

    def test_changed_files_captured_accurately(self) -> None:
        # Simulate local modifications in working tree
        self.git_ops.stage_change(self.workspace_path, "src/calculator.py", change_type="MODIFIED")
        self.git_ops.stage_change(self.workspace_path, "src/new_helper.py", change_type="CREATED")
        self.git_ops.stage_change(self.workspace_path, "src/deprecated.py", change_type="DELETED")

        cs = self.manager.capture_change_set(self.execution_context, self.work_order)

        self.assertEqual(cs.status, ChangeSetStatus.UNCOMMITTED)
        self.assertFalse(cs.is_empty())
        self.assertEqual(cs.total_files_affected(), 3)
        self.assertIn("src/calculator.py", cs.files_changed)
        self.assertIn("src/new_helper.py", cs.files_created)
        self.assertIn("src/deprecated.py", cs.files_deleted)
        self.assertEqual(cs.diff_summary["total_files"], 3)

    # -------------------------------------------------------------------------
    # 4. Local Commit Creation Under Permissive Policy
    # -------------------------------------------------------------------------

    def test_successful_local_commit(self) -> None:
        # Stage a modified file
        self.git_ops.stage_change(self.workspace_path, "src/core.py", change_type="MODIFIED")

        permissive_policy = GitOperationPolicy.allow_local_changes()

        cs = self.manager.create_local_commit(
            execution_context=self.execution_context,
            work_order=self.work_order,
            message="Add core implementation logic",
            policy=permissive_policy,
        )

        # Invariants verified:
        # 1. Status is COMMITTED
        self.assertEqual(cs.status, ChangeSetStatus.COMMITTED)
        # 2. resulting_revision is created and distinct from base_revision
        self.assertIsNotNone(cs.resulting_revision)
        self.assertNotEqual(cs.resulting_revision.commit_hash, self.commit_sha)
        self.assertEqual(cs.base_revision.commit_hash, self.commit_sha)
        # 3. execution_context is updated with resulting_revision
        self.assertEqual(self.execution_context.resulting_revision, cs.resulting_revision)
        # 4. commit_references contains the new commit
        self.assertEqual(len(cs.commit_references), 1)
        self.assertEqual(cs.commit_references[0].commit_hash, cs.resulting_revision.commit_hash)
        # 5. Working tree is clean post-commit
        post_status = self.git_ops.inspect_status(self.workspace_path)
        self.assertEqual(len(post_status["files_changed"]), 0)

    # -------------------------------------------------------------------------
    # 5. Multiple Sequential Commits
    # -------------------------------------------------------------------------

    def test_multiple_sequential_commits(self) -> None:
        permissive_policy = GitOperationPolicy.allow_local_changes()

        # Commit 1
        self.git_ops.stage_change(self.workspace_path, "src/part1.py", change_type="CREATED")
        cs1 = self.manager.create_local_commit(
            execution_context=self.execution_context,
            work_order=self.work_order,
            message="Step 1: Part 1 created",
            policy=permissive_policy,
        )
        self.assertEqual(cs1.status, ChangeSetStatus.COMMITTED)
        self.assertEqual(len(cs1.commit_references), 1)
        rev1 = cs1.resulting_revision

        # Commit 2
        self.git_ops.stage_change(self.workspace_path, "src/part2.py", change_type="CREATED")
        cs2 = self.manager.create_local_commit(
            execution_context=self.execution_context,
            work_order=self.work_order,
            message="Step 2: Part 2 created",
            policy=permissive_policy,
        )
        self.assertEqual(cs2.status, ChangeSetStatus.COMMITTED)
        self.assertEqual(len(cs2.commit_references), 2)
        rev2 = cs2.resulting_revision

        self.assertNotEqual(rev1.commit_hash, rev2.commit_hash)
        self.assertEqual(cs2.resulting_revision.commit_hash, rev2.commit_hash)
        self.assertEqual(cs2.commit_references[0].commit_hash, rev1.commit_hash)
        self.assertEqual(cs2.commit_references[1].commit_hash, rev2.commit_hash)

    # -------------------------------------------------------------------------
    # 6. Commit Lineage Preservation
    # -------------------------------------------------------------------------

    def test_commit_lineage_and_metadata(self) -> None:
        self.git_ops.stage_change(self.workspace_path, "src/feature.py", change_type="MODIFIED")

        cs = self.manager.create_local_commit(
            execution_context=self.execution_context,
            work_order=self.work_order,
            message="Fix parser edge case",
            policy=GitOperationPolicy.allow_local_changes(),
            author="Dev Agent <agent@autonomos.ai>",
        )
        self.assertEqual(cs.status, ChangeSetStatus.COMMITTED)

        # Check commit object in fake Git
        commit_obj = cs.resulting_revision
        self.assertEqual(commit_obj.metadata["execution_id"], self.execution_id)
        self.assertEqual(commit_obj.metadata["work_order_id"], self.work_order_id)
        self.assertEqual(commit_obj.metadata["project_id"], self.project_id)
        self.assertIn(f"Execution-ID: {self.execution_id}", commit_obj.trace["message"])
        self.assertIn(f"Work-Order-ID: {self.work_order_id}", commit_obj.trace["message"])

    # -------------------------------------------------------------------------
    # 7. Invalid Repository State Handling
    # -------------------------------------------------------------------------

    def test_invalid_repository_state_fails(self) -> None:
        broken_context = GitExecutionContext(
            repository_id=self.repo_id,
            execution_id=self.execution_id,
            work_order_id=self.work_order_id,
            project_id=self.project_id,
            base_revision=self.base_revision,
            workspace_path="/non/existent/broken_path",
        )
        cs = self.manager.capture_change_set(broken_context, self.work_order)
        self.assertEqual(cs.status, ChangeSetStatus.INVALID)
        self.assertTrue(cs.metadata.get("inspection_failed"))

    # -------------------------------------------------------------------------
    # 8. Commit Failure Handling
    # -------------------------------------------------------------------------

    def test_underlying_commit_failure_handled(self) -> None:
        self.git_ops.stage_change(self.workspace_path, "src/file.py", change_type="MODIFIED")
        self.git_ops.fail_commit = True
        self.git_ops.custom_error_message = "Disk full while writing Git tree"

        cs = self.manager.create_local_commit(
            execution_context=self.execution_context,
            work_order=self.work_order,
            message="Attempt commit",
            policy=GitOperationPolicy.allow_local_changes(),
        )
        self.assertEqual(cs.status, ChangeSetStatus.FAILED)
        self.assertIn("Underlying commit failed", cs.metadata.get("error", ""))

    # -------------------------------------------------------------------------
    # 9. Unauthorized Commit Under Default Policy
    # -------------------------------------------------------------------------

    def test_unauthorized_commit_rejected_by_default_policy(self) -> None:
        self.git_ops.stage_change(self.workspace_path, "src/file.py", change_type="MODIFIED")

        # Under conservative default: allow_commit is False
        conservative = GitOperationPolicy.conservative_default()

        cs = self.manager.create_local_commit(
            execution_context=self.execution_context,
            work_order=self.work_order,
            message="Unauthorized commit attempt",
            policy=conservative,
        )
        self.assertEqual(cs.status, ChangeSetStatus.FAILED)
        self.assertIn("Commit unauthorized by policy", cs.metadata.get("error", ""))
        self.assertEqual(
            cs.metadata["policy_decision"]["matched_policy"],
            "LOCAL_CHANGE_POLICY_REQUIRED",
        )

    # -------------------------------------------------------------------------
    # 10. Default / Protected Branch Mutation Protection
    # -------------------------------------------------------------------------

    def test_cannot_commit_directly_to_protected_branch(self) -> None:
        # Context set to 'main' branch
        protected_context = GitExecutionContext(
            repository_id=self.repo_id,
            execution_id=self.execution_id,
            work_order_id=self.work_order_id,
            project_id=self.project_id,
            base_revision=self.base_revision,
            workspace_path=self.workspace_path,
            branch="main",
        )

        self.git_ops.stage_change(self.workspace_path, "src/main.py", change_type="MODIFIED")

        cs = self.manager.create_local_commit(
            execution_context=protected_context,
            work_order=self.work_order,
            message="Direct commit to main",
            policy=GitOperationPolicy.allow_local_changes(),
        )
        self.assertEqual(cs.status, ChangeSetStatus.FAILED)
        self.assertIn("Commit unauthorized by policy", cs.metadata.get("error", ""))
        self.assertEqual(
            cs.metadata["policy_decision"]["matched_policy"],
            "PROTECTED_BRANCH_MUTATION_DENIED",
        )

    # -------------------------------------------------------------------------
    # 11. Commit Is NOT Approval Invariant
    # -------------------------------------------------------------------------

    def test_commit_is_not_approval(self) -> None:
        self.git_ops.stage_change(self.workspace_path, "src/feature.py", change_type="MODIFIED")

        cs = self.manager.create_local_commit(
            execution_context=self.execution_context,
            work_order=self.work_order,
            message="Feature implementation",
            policy=GitOperationPolicy.allow_local_changes(),
        )
        self.assertEqual(cs.status, ChangeSetStatus.COMMITTED)

        # Verify WorkOrder status and acceptance remain untouched
        self.assertNotEqual(self.work_order.status, "COMPLETED")
        self.assertEqual(self.execution_context.resulting_revision, cs.resulting_revision)

    # -------------------------------------------------------------------------
    # 12. Serialization Roundtrip
    # -------------------------------------------------------------------------

    def test_changeset_serialization_roundtrip(self) -> None:
        res_rev = GitRevision(
            revision_id=new_git_revision_id(),
            commit_hash="9999888877776666555544443333222211110000",
            branch="feature/changeset-1",
        )
        cs = ChangeSet(
            change_set_id=new_change_set_id(),
            execution_id=self.execution_id,
            work_order_id=self.work_order_id,
            base_revision=self.base_revision,
            resulting_revision=res_rev,
            files_changed=["src/a.py"],
            files_created=["src/b.py"],
            files_deleted=[],
            diff_summary={"total_files": 2},
            commit_references=[res_rev],
            status=ChangeSetStatus.COMMITTED,
            evidence=[{"type": "LOCAL_COMMIT", "sha": res_rev.commit_hash}],
            trace={"stage": "unit_test"},
            metadata={"source": "test"},
        )

        data = cs.to_dict()
        self.assertEqual(data["status"], "COMMITTED")
        self.assertEqual(data["files_changed"], ["src/a.py"])

        restored = ChangeSet.from_dict(data)
        self.assertEqual(restored.change_set_id, cs.change_set_id)
        self.assertEqual(restored.status, ChangeSetStatus.COMMITTED)
        self.assertEqual(restored.base_revision.commit_hash, self.commit_sha)
        self.assertEqual(restored.resulting_revision.commit_hash, res_rev.commit_hash)
        self.assertEqual(len(restored.commit_references), 1)
        self.assertEqual(restored.trace, cs.trace)

        json_str = cs.to_json()
        self.assertIn("COMMITTED", json_str)


if __name__ == "__main__":
    unittest.main()
