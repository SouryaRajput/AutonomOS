"""
Unit Tests for Programmer V1 Phase 6.1:
Git Repository Model & Lineage Contracts.

Validates:
1. Canonical Git identifier generation and validation (grepo-*, grev-*).
2. Commit hash format validation (7 to 40 hex characters).
3. GitRevision domain contract, immutability, and serialization.
4. GitRepository domain contract, repository state handling, and serialization.
5. GitExecutionContext domain contract, lineage preservation, and workspace isolation.
6. Strict cross-project and cross-execution lineage invariants.
7. Clear distinction between repository identity, workspace path, base revision, and resulting revision.
"""

from __future__ import annotations

import unittest

from core.programmer.contracts.git_model import (
    GitExecutionContext,
    GitRepository,
    GitRevision,
    validate_commit_hash,
)
from core.programmer.contracts.identifiers import (
    GIT_REPOSITORY_ID_PREFIX,
    GIT_REVISION_ID_PREFIX,
    new_execution_id,
    new_git_repository_id,
    new_git_revision_id,
    new_work_order_id,
    validate_git_repository_id,
    validate_git_revision_id,
)
from core.programmer.contracts.work_order import ProgrammerWorkOrder
from core.programmer.contracts.execution import ProgrammerExecution
from core.programmer.errors import (
    InvalidProgrammerIdError,
    ProgrammerLineageError,
    ProgrammerValidationError,
)
from core.programmer.types import (
    GitIsolationMode,
    GitRepositoryState,
)


class TestProgrammerGitModel(unittest.TestCase):
    def setUp(self) -> None:
        self.project_id = "proj-autonomos-alpha"
        self.work_order_id = new_work_order_id()
        self.execution_id = new_execution_id()
        self.repo_id = new_git_repository_id()
        self.rev_id = new_git_revision_id()
        self.commit_sha = "a1b2c3d4e5f60718293a4b5c6d7e8f9012345678"

    # -------------------------------------------------------------------------
    # 1. Canonical Identifiers & Commit Hash Validation
    # -------------------------------------------------------------------------

    def test_canonical_git_identifiers(self) -> None:
        repo_id = new_git_repository_id()
        self.assertTrue(repo_id.startswith(GIT_REPOSITORY_ID_PREFIX))
        validate_git_repository_id(repo_id)

        rev_id = new_git_revision_id()
        self.assertTrue(rev_id.startswith(GIT_REVISION_ID_PREFIX))
        validate_git_revision_id(rev_id)

        with self.assertRaises(InvalidProgrammerIdError):
            validate_git_repository_id("invalid-repo-id")

        with self.assertRaises(InvalidProgrammerIdError):
            validate_git_revision_id("invalid-rev-id")

        with self.assertRaises(InvalidProgrammerIdError):
            validate_git_repository_id("")

        with self.assertRaises(InvalidProgrammerIdError):
            validate_git_revision_id("")

    def test_validate_commit_hash(self) -> None:
        # Valid 40-char full SHA
        validate_commit_hash("0123456789abcdef0123456789abcdef01234567")
        validate_commit_hash("ABCDEF0123456789abcdef0123456789abcdef01")

        # Valid 7-char short SHA
        validate_commit_hash("a1b2c3d")
        validate_commit_hash("7f8e9d0")

        # Valid intermediate length SHA
        validate_commit_hash("1234567890ab")

        # Invalid: non-hex
        with self.assertRaises(ProgrammerValidationError):
            validate_commit_hash("g1234567")
        with self.assertRaises(ProgrammerValidationError):
            validate_commit_hash("not-a-sha-hash!")

        # Invalid: too short (< 7)
        with self.assertRaises(ProgrammerValidationError):
            validate_commit_hash("123456")

        # Invalid: too long (> 40)
        with self.assertRaises(ProgrammerValidationError):
            validate_commit_hash("0123456789abcdef0123456789abcdef0123456789a")

        # Invalid: empty or non-string
        with self.assertRaises(ProgrammerValidationError):
            validate_commit_hash("")
        with self.assertRaises(ProgrammerValidationError):
            validate_commit_hash("   ")

    # -------------------------------------------------------------------------
    # 2. GitRevision Domain Contract
    # -------------------------------------------------------------------------

    def test_git_revision_creation_and_validation(self) -> None:
        rev = GitRevision(
            revision_id=self.rev_id,
            commit_hash=self.commit_sha,
            branch="feature/phase-6.1",
            trace={"source": "upstream/main", "correlation_id": "corr-1"},
            metadata={"author": "programmer"},
        )
        self.assertEqual(rev.revision_id, self.rev_id)
        self.assertEqual(rev.commit_hash, self.commit_sha)
        self.assertEqual(rev.branch, "feature/phase-6.1")
        self.assertIsNotNone(rev.timestamp)
        self.assertEqual(rev.trace["correlation_id"], "corr-1")

        # Validation errors
        with self.assertRaises(ProgrammerValidationError):
            GitRevision(revision_id="", commit_hash=self.commit_sha)

        with self.assertRaises(InvalidProgrammerIdError):
            GitRevision(revision_id="wo-12345678", commit_hash=self.commit_sha)

        with self.assertRaises(ProgrammerValidationError):
            GitRevision(revision_id=self.rev_id, commit_hash="invalid-hash")

        with self.assertRaises(ProgrammerValidationError):
            GitRevision(revision_id=self.rev_id, commit_hash=self.commit_sha, timestamp="")

    def test_git_revision_serialization_roundtrip(self) -> None:
        rev = GitRevision(
            revision_id=self.rev_id,
            commit_hash=self.commit_sha,
            branch="main",
            trace={"event": "commit", "depth": 1},
            metadata={"committer": "robot"},
        )
        data = rev.to_dict()
        self.assertEqual(data["revision_id"], self.rev_id)
        self.assertEqual(data["commit_hash"], self.commit_sha)
        self.assertEqual(data["branch"], "main")
        self.assertEqual(data["trace"]["depth"], 1)

        restored = GitRevision.from_dict(data)
        self.assertEqual(restored.revision_id, rev.revision_id)
        self.assertEqual(restored.commit_hash, rev.commit_hash)
        self.assertEqual(restored.branch, rev.branch)
        self.assertEqual(restored.timestamp, rev.timestamp)
        self.assertEqual(restored.trace, rev.trace)
        self.assertEqual(restored.metadata, rev.metadata)

    # -------------------------------------------------------------------------
    # 3. GitRepository Domain Contract
    # -------------------------------------------------------------------------

    def test_git_repository_creation_and_validation(self) -> None:
        repo = GitRepository(
            repository_id=self.repo_id,
            project_id=self.project_id,
            root_path="/path/to/project/repo",
            default_branch="master",
            remote_reference="git@github.com:org/repo.git",
            repository_state=GitRepositoryState.CLEAN,
            trace={"origin": "manager_spec"},
        )
        self.assertEqual(repo.repository_id, self.repo_id)
        self.assertEqual(repo.project_id, self.project_id)
        self.assertEqual(repo.root_path, "/path/to/project/repo")
        self.assertEqual(repo.default_branch, "master")
        self.assertEqual(repo.remote_reference, "git@github.com:org/repo.git")
        self.assertEqual(repo.repository_state, GitRepositoryState.CLEAN)

        # String enum parsing
        repo_str = GitRepository(
            repository_id=new_git_repository_id(),
            project_id=self.project_id,
            root_path="/repo",
            repository_state="dirty",
        )
        self.assertEqual(repo_str.repository_state, GitRepositoryState.DIRTY)

        # Fallback to UNKNOWN on unparseable state
        repo_unknown = GitRepository(
            repository_id=new_git_repository_id(),
            project_id=self.project_id,
            root_path="/repo",
            repository_state="some_weird_state",
        )
        self.assertEqual(repo_unknown.repository_state, GitRepositoryState.UNKNOWN)

        # Validation errors
        with self.assertRaises(ProgrammerValidationError):
            GitRepository(repository_id="", project_id=self.project_id, root_path="/repo")

        with self.assertRaises(InvalidProgrammerIdError):
            GitRepository(repository_id="invalid-id", project_id=self.project_id, root_path="/repo")

        with self.assertRaises(ProgrammerValidationError):
            GitRepository(repository_id=new_git_repository_id(), project_id="", root_path="/repo")

        with self.assertRaises(ProgrammerValidationError):
            GitRepository(repository_id=new_git_repository_id(), project_id=self.project_id, root_path="")

        with self.assertRaises(ProgrammerValidationError):
            GitRepository(repository_id=new_git_repository_id(), project_id=self.project_id, root_path="/repo", default_branch="")

    def test_git_repository_serialization_roundtrip(self) -> None:
        repo = GitRepository(
            repository_id=self.repo_id,
            project_id=self.project_id,
            root_path="/workspace/repo",
            default_branch="main",
            remote_reference="https://github.com/project/core.git",
            repository_state=GitRepositoryState.READY,
            trace={"created_by": "provisioner"},
            metadata={"cluster": "us-east"},
        )
        data = repo.to_dict()
        self.assertEqual(data["repository_id"], self.repo_id)
        self.assertEqual(data["repository_state"], "READY")

        restored = GitRepository.from_dict(data)
        self.assertEqual(restored.repository_id, repo.repository_id)
        self.assertEqual(restored.project_id, repo.project_id)
        self.assertEqual(restored.root_path, repo.root_path)
        self.assertEqual(restored.default_branch, repo.default_branch)
        self.assertEqual(restored.remote_reference, repo.remote_reference)
        self.assertEqual(restored.repository_state, GitRepositoryState.READY)
        self.assertEqual(restored.trace, repo.trace)
        self.assertEqual(restored.metadata, repo.metadata)

    # -------------------------------------------------------------------------
    # 4. GitExecutionContext Domain Contract & Differentiation
    # -------------------------------------------------------------------------

    def test_git_execution_context_differentiation(self) -> None:
        base_rev = GitRevision(
            revision_id=self.rev_id,
            commit_hash=self.commit_sha,
            branch="main",
        )
        res_rev = GitRevision(
            revision_id=new_git_revision_id(),
            commit_hash="b2c3d4e5f6a10718293a4b5c6d7e8f9012345679",
            branch="feature/wo-exec-1",
        )
        ctx = GitExecutionContext(
            repository_id=self.repo_id,
            execution_id=self.execution_id,
            work_order_id=self.work_order_id,
            project_id=self.project_id,
            base_revision=base_rev,
            resulting_revision=res_rev,
            workspace_path="/tmp/workspaces/exec-1",
            isolation_mode=GitIsolationMode.WORKTREE,
            branch="feature/wo-exec-1",
            reference="refs/heads/feature/wo-exec-1",
        )

        # Clearly distinguishes:
        # 1. Repository identity vs workspace path
        self.assertEqual(ctx.repository_id, self.repo_id)
        self.assertEqual(ctx.workspace_path, "/tmp/workspaces/exec-1")
        self.assertNotEqual(ctx.repository_id, ctx.workspace_path)

        # 2. Base revision vs resulting revision
        self.assertEqual(ctx.base_revision.commit_hash, self.commit_sha)
        self.assertEqual(ctx.resulting_revision.commit_hash, "b2c3d4e5f6a10718293a4b5c6d7e8f9012345679")
        self.assertNotEqual(ctx.base_revision.revision_id, ctx.resulting_revision.revision_id)
        self.assertNotEqual(ctx.base_revision.commit_hash, ctx.resulting_revision.commit_hash)

        # 3. Isolation mode
        self.assertEqual(ctx.isolation_mode, GitIsolationMode.WORKTREE)

    def test_git_execution_context_validation_failures(self) -> None:
        base_rev = GitRevision(
            revision_id=self.rev_id,
            commit_hash=self.commit_sha,
        )

        # Missing repository_id
        with self.assertRaises(ProgrammerLineageError):
            GitExecutionContext(
                repository_id="",
                execution_id=self.execution_id,
                work_order_id=self.work_order_id,
                base_revision=base_rev,
                workspace_path="/workspace",
            )

        # Missing execution_id
        with self.assertRaises(ProgrammerLineageError):
            GitExecutionContext(
                repository_id=self.repo_id,
                execution_id="",
                work_order_id=self.work_order_id,
                base_revision=base_rev,
                workspace_path="/workspace",
            )

        # Missing work_order_id
        with self.assertRaises(ProgrammerLineageError):
            GitExecutionContext(
                repository_id=self.repo_id,
                execution_id=self.execution_id,
                work_order_id="",
                base_revision=base_rev,
                workspace_path="/workspace",
            )

        # Missing workspace_path
        with self.assertRaises(ProgrammerValidationError):
            GitExecutionContext(
                repository_id=self.repo_id,
                execution_id=self.execution_id,
                work_order_id=self.work_order_id,
                base_revision=base_rev,
                workspace_path="",
            )

        # Missing base_revision
        with self.assertRaises(ProgrammerValidationError):
            GitExecutionContext(
                repository_id=self.repo_id,
                execution_id=self.execution_id,
                work_order_id=self.work_order_id,
                base_revision=None,  # type: ignore
                workspace_path="/workspace",
            )

    # -------------------------------------------------------------------------
    # 5. Lineage & Cross-Project / Cross-Execution Invariant Enforcement
    # -------------------------------------------------------------------------

    def test_cross_project_and_cross_execution_invariants(self) -> None:
        base_rev = GitRevision(
            revision_id=self.rev_id,
            commit_hash=self.commit_sha,
        )
        ctx = GitExecutionContext(
            repository_id=self.repo_id,
            execution_id=self.execution_id,
            work_order_id=self.work_order_id,
            project_id=self.project_id,
            base_revision=base_rev,
            workspace_path="/workspace/exec-1",
        )

        repo = GitRepository(
            repository_id=self.repo_id,
            project_id=self.project_id,
            root_path="/repo/root",
        )
        work_order = ProgrammerWorkOrder(
            work_order_id=self.work_order_id,
            manager_task_id="mtask-100",
            project_id=self.project_id,
            correlation_id="corr-100",
            objective="Deliver Phase 6.1",
        )
        execution = ProgrammerExecution(
            execution_id=self.execution_id,
            work_order_id=self.work_order_id,
            task_id="task-100",
            project_id=self.project_id,
            correlation_id="corr-100",
        )

        # Valid lineage should pass with zero errors
        ctx.validate(repository=repo, execution=execution, work_order=work_order)

        # 1. Cross-Project Rejection: Repo belongs to a different project
        alien_repo = GitRepository(
            repository_id=self.repo_id,
            project_id="alien-project-xyz",
            root_path="/alien/repo",
        )
        with self.assertRaises(ProgrammerLineageError) as cm:
            ctx.validate(repository=alien_repo)
        self.assertIn("Cross-project forbidden", str(cm.exception))

        # 2. Cross-Project Rejection: Execution belongs to a different project
        alien_execution = ProgrammerExecution(
            execution_id=self.execution_id,
            work_order_id=self.work_order_id,
            task_id="task-alien",
            project_id="alien-project-xyz",
            correlation_id="corr-alien",
        )
        with self.assertRaises(ProgrammerLineageError) as cm:
            ctx.validate(execution=alien_execution)
        self.assertIn("Cross-project forbidden", str(cm.exception))

        # 3. Cross-Project Rejection: WorkOrder belongs to a different project
        alien_wo = ProgrammerWorkOrder(
            work_order_id=self.work_order_id,
            manager_task_id="mtask-100",
            project_id="alien-project-xyz",
            correlation_id="corr-alien",
            objective="Alien objective",
        )
        with self.assertRaises(ProgrammerLineageError) as cm:
            ctx.validate(work_order=alien_wo)
        self.assertIn("Cross-project forbidden", str(cm.exception))

        # 4. Cross-Execution Rejection: Execution ID mismatch
        other_execution = ProgrammerExecution(
            execution_id=new_execution_id(),
            work_order_id=self.work_order_id,
            task_id="task-100",
            project_id=self.project_id,
            correlation_id="corr-100",
        )
        with self.assertRaises(ProgrammerLineageError) as cm:
            ctx.validate(execution=other_execution)
        self.assertIn("Lineage mismatch", str(cm.exception))

        # 5. WorkOrder Mismatch: Different WorkOrder ID
        other_wo = ProgrammerWorkOrder(
            work_order_id=new_work_order_id(),
            manager_task_id="mtask-100",
            project_id=self.project_id,
            correlation_id="corr-100",
            objective="Other objective",
        )
        with self.assertRaises(ProgrammerLineageError) as cm:
            ctx.validate(work_order=other_wo)
        self.assertIn("Lineage mismatch", str(cm.exception))

        # 6. Repository Mismatch: Different Repository ID
        other_repo = GitRepository(
            repository_id=new_git_repository_id(),
            project_id=self.project_id,
            root_path="/repo/other",
        )
        with self.assertRaises(ProgrammerLineageError) as cm:
            ctx.validate(repository=other_repo)
        self.assertIn("Lineage mismatch", str(cm.exception))

    def test_git_execution_context_serialization_roundtrip(self) -> None:
        base_rev = GitRevision(
            revision_id=self.rev_id,
            commit_hash=self.commit_sha,
            branch="develop",
        )
        res_rev = GitRevision(
            revision_id=new_git_revision_id(),
            commit_hash="c3d4e5f6a1b20718293a4b5c6d7e8f9012345670",
            branch="develop",
        )
        ctx = GitExecutionContext(
            repository_id=self.repo_id,
            execution_id=self.execution_id,
            work_order_id=self.work_order_id,
            project_id=self.project_id,
            base_revision=base_rev,
            resulting_revision=res_rev,
            workspace_path="/opt/workspaces/ws-01",
            isolation_mode=GitIsolationMode.ISOLATED_CLONE,
            branch="develop",
            reference="refs/heads/develop",
            trace={"parent_task": "task-42"},
            metadata={"env": "ci"},
        )

        data = ctx.to_dict()
        self.assertEqual(data["repository_id"], self.repo_id)
        self.assertEqual(data["isolation_mode"], "ISOLATED_CLONE")
        self.assertEqual(data["base_revision"]["commit_hash"], self.commit_sha)
        self.assertEqual(data["resulting_revision"]["commit_hash"], "c3d4e5f6a1b20718293a4b5c6d7e8f9012345670")

        restored = GitExecutionContext.from_dict(data)
        self.assertEqual(restored.repository_id, ctx.repository_id)
        self.assertEqual(restored.execution_id, ctx.execution_id)
        self.assertEqual(restored.work_order_id, ctx.work_order_id)
        self.assertEqual(restored.project_id, ctx.project_id)
        self.assertEqual(restored.workspace_path, ctx.workspace_path)
        self.assertEqual(restored.isolation_mode, GitIsolationMode.ISOLATED_CLONE)
        self.assertEqual(restored.branch, ctx.branch)
        self.assertEqual(restored.reference, ctx.reference)
        self.assertEqual(restored.base_revision.commit_hash, ctx.base_revision.commit_hash)
        self.assertEqual(restored.resulting_revision.commit_hash, ctx.resulting_revision.commit_hash)
        self.assertEqual(restored.trace, ctx.trace)
        self.assertEqual(restored.metadata, ctx.metadata)


if __name__ == "__main__":
    unittest.main()
