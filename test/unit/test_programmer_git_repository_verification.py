from __future__ import annotations

import os
import pytest

from core.programmer.contracts.diff_verifier import (
    DiffScopeVerifier,
    RenamedFile,
    UnauthorizedChange,
)
from core.programmer.contracts.git_change_set import (
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
from core.programmer.contracts.git_verification import (
    RepositoryAnomaly,
    RepositoryStateFacts,
    RepositoryStateVerification,
    RepositoryStateVerifier,
)
from core.programmer.contracts.git_worktree import (
    FakeGitOperations,
    GitWorktree,
)
from core.programmer.contracts.identifiers import (
    REPO_STATE_VERIFICATION_ID_PREFIX,
    new_execution_id,
    new_git_repository_id,
    new_git_revision_id,
    new_repo_state_verification_id,
    new_work_order_id,
    validate_repo_state_verification_id,
)
from core.programmer.contracts.work_order import ProgrammerWorkOrder
from core.programmer.contracts.workspace import ProgrammerWorkspace
from core.programmer.errors import (
    InvalidProgrammerIdError,
    ProgrammerLineageError,
    ProgrammerValidationError,
)
from core.programmer.types import (
    GitIsolationMode,
    GitRepositoryState,
    PathBoundaryScope,
    RepositoryAnomalyType,
    VerificationEvidenceSourceType,
    VerificationStatus,
)


class TestProgrammerGitRepositoryVerification:
    """
    Test suite for Programmer V1 — Phase 6.5: Repository State Verification.
    Validates empirical Git facts extraction, scope evaluation via Phase 4 integration,
    anomaly detection, and isolation invariants.
    """

    @pytest.fixture
    def setup_env(self, tmp_path):
        git_ops = FakeGitOperations()
        policy_evaluator = GitPolicyEvaluator()
        changeset_mgr = GitChangeSetManager(git_ops=git_ops, policy_evaluator=policy_evaluator)
        diff_verifier = DiffScopeVerifier()
        verifier = RepositoryStateVerifier(git_ops=git_ops, diff_scope_verifier=diff_verifier)

        repo_root = str(tmp_path / "repo")
        os.makedirs(repo_root, exist_ok=True)
        base_rev = git_ops.add_repository(repo_root, default_branch="main", initial_commit_sha="1111111111111111111111111111111111111111")

        repo = GitRepository(
            repository_id=new_git_repository_id(),
            project_id="proj-alpha",
            root_path=repo_root,
            default_branch="main",
            repository_state=GitRepositoryState.CLEAN,
        )

        exec_id = new_execution_id()
        wo_id = new_work_order_id()
        wt_path = str(tmp_path / "worktrees" / f"wt-{exec_id[-8:]}")
        os.makedirs(wt_path, exist_ok=True)
        branch_name = f"workorder/{wo_id}/exec-{exec_id[-8:]}"

        git_ops.create_worktree(
            repo_path=repo_root,
            worktree_path=wt_path,
            branch=branch_name,
            base_commit=base_rev.commit_hash,
        )

        context = GitExecutionContext(
            repository_id=repo.repository_id,
            execution_id=exec_id,
            work_order_id=wo_id,
            base_revision=base_rev,
            workspace_path=wt_path,
            isolation_mode=GitIsolationMode.WORKTREE,
            branch=branch_name,
            project_id=repo.project_id,
        )

        work_order = ProgrammerWorkOrder(
            work_order_id=wo_id,
            manager_task_id="mtask-alpha",
            project_id=repo.project_id,
            correlation_id="corr-alpha",
            objective="Implement feature inside src/",
            allowed_paths=["src/"],
            writable_paths=["src/"],
            read_only_paths=["config/"],
            forbidden_paths=[".env", "secrets/"],
        )

        return {
            "git_ops": git_ops,
            "verifier": verifier,
            "changeset_mgr": changeset_mgr,
            "repo": repo,
            "context": context,
            "work_order": work_order,
            "base_rev": base_rev,
        }

    def test_canonical_identifier_generation_and_validation(self):
        """Verify canonical vrepo- prefix generation and validation."""
        v_id = new_repo_state_verification_id()
        assert v_id.startswith(REPO_STATE_VERIFICATION_ID_PREFIX)
        validate_repo_state_verification_id(v_id)

        with pytest.raises(InvalidProgrammerIdError):
            validate_repo_state_verification_id("invalid-id")
        with pytest.raises(InvalidProgrammerIdError):
            validate_repo_state_verification_id("pcs-12345678")

    def test_clean_repository(self, setup_env):
        """Verify that a clean repository without changes verifies successfully."""
        verifier: RepositoryStateVerifier = setup_env["verifier"]
        context: GitExecutionContext = setup_env["context"]
        wo: ProgrammerWorkOrder = setup_env["work_order"]
        repo: GitRepository = setup_env["repo"]

        result = verifier.verify(
            execution_context=context,
            work_order=wo,
            repository=repo,
            allow_uncommitted=False,
        )

        assert result.verification_status == VerificationStatus.PASS
        assert result.is_clean is True
        assert result.uncommitted_changes is False
        assert result.total_changes_count == 0
        assert len(result.anomalies) == 0
        assert len(result.unauthorized_changes) == 0
        assert result.is_verified is True

    def test_changes_disappeared_when_changes_expected(self, setup_env):
        """Verify that a clean repository flags CHANGES_DISAPPEARED if changes were expected."""
        verifier: RepositoryStateVerifier = setup_env["verifier"]
        context: GitExecutionContext = setup_env["context"]
        wo: ProgrammerWorkOrder = setup_env["work_order"]

        result = verifier.verify(
            execution_context=context,
            work_order=wo,
            expect_changes=True,
        )

        assert result.verification_status == VerificationStatus.FAIL
        assert result.is_clean is True
        assert len(result.anomalies) == 1
        assert result.anomalies[0].anomaly_type == RepositoryAnomalyType.CHANGES_DISAPPEARED

    def test_expected_changes_committed(self, setup_env):
        """Verify expected authorized changes committed to local branch pass verification."""
        git_ops: FakeGitOperations = setup_env["git_ops"]
        verifier: RepositoryStateVerifier = setup_env["verifier"]
        changeset_mgr: GitChangeSetManager = setup_env["changeset_mgr"]
        context: GitExecutionContext = setup_env["context"]
        wo: ProgrammerWorkOrder = setup_env["work_order"]

        # Stage changes in writable path
        git_ops.stage_change(context.workspace_path, "src/core.py", "MODIFIED")
        git_ops.stage_change(context.workspace_path, "src/utils.py", "CREATED")

        # Create authorized commit
        cs = changeset_mgr.create_local_commit(
            execution_context=context,
            work_order=wo,
            message="Add core feature and utils",
            policy=GitOperationPolicy.allow_local_changes(),
        )
        assert cs.resulting_revision is not None

        result = verifier.verify(
            execution_context=context,
            work_order=wo,
            allow_uncommitted=False,
        )

        assert result.verification_status == VerificationStatus.PASS
        assert result.is_clean is True
        assert result.uncommitted_changes is False
        assert "src/core.py" in result.changed_files
        assert "src/utils.py" in result.created_files
        assert len(result.unauthorized_changes) == 0
        assert len(result.anomalies) == 0
        assert len(result.commit_references) == 1
        assert result.current_revision == cs.resulting_revision

    def test_unauthorized_changes_detected(self, setup_env):
        """Verify changes touching forbidden or read-only paths trigger UNAUTHORIZED_CHANGES."""
        git_ops: FakeGitOperations = setup_env["git_ops"]
        verifier: RepositoryStateVerifier = setup_env["verifier"]
        context: GitExecutionContext = setup_env["context"]
        wo: ProgrammerWorkOrder = setup_env["work_order"]

        # Modify read-only file and create forbidden file
        git_ops.stage_change(context.workspace_path, "config/settings.json", "MODIFIED")
        git_ops.stage_change(context.workspace_path, ".env", "CREATED")

        result = verifier.verify(
            execution_context=context,
            work_order=wo,
            allow_uncommitted=True,
        )

        assert result.verification_status == VerificationStatus.FAIL
        assert result.has_unauthorized_changes is True
        assert len(result.unauthorized_changes) == 2

        unauth_paths = [u.path for u in result.unauthorized_changes]
        assert "config/settings.json" in unauth_paths
        assert ".env" in unauth_paths

        anomaly_types = [a.anomaly_type for a in result.anomalies]
        assert RepositoryAnomalyType.UNAUTHORIZED_CHANGES in anomaly_types

    def test_base_revision_mismatch(self, setup_env):
        """Verify that diverged base revision triggers BASE_REVISION_MISMATCH anomaly."""
        git_ops: FakeGitOperations = setup_env["git_ops"]
        verifier: RepositoryStateVerifier = setup_env["verifier"]
        context: GitExecutionContext = setup_env["context"]
        wo: ProgrammerWorkOrder = setup_env["work_order"]

        # Tamper context to expect a non-existent/different base commit
        fake_base = GitRevision(
            revision_id=new_git_revision_id(),
            commit_hash="9999999999999999999999999999999999999999",
            branch="main",
        )
        tampered_context = GitExecutionContext(
            repository_id=context.repository_id,
            execution_id=context.execution_id,
            work_order_id=context.work_order_id,
            base_revision=fake_base,
            workspace_path=context.workspace_path,
            isolation_mode=context.isolation_mode,
            branch=context.branch,
            project_id=context.project_id,
        )

        result = verifier.verify(
            execution_context=tampered_context,
            work_order=wo,
        )

        assert result.verification_status == VerificationStatus.FAIL
        anomaly_types = [a.anomaly_type for a in result.anomalies]
        assert RepositoryAnomalyType.BASE_REVISION_MISMATCH in anomaly_types

    def test_uncommitted_changes_detected(self, setup_env):
        """Verify uncommitted changes flag UNEXPECTED_UNCOMMITTED_CHANGES when not allowed."""
        git_ops: FakeGitOperations = setup_env["git_ops"]
        verifier: RepositoryStateVerifier = setup_env["verifier"]
        context: GitExecutionContext = setup_env["context"]
        wo: ProgrammerWorkOrder = setup_env["work_order"]

        git_ops.stage_change(context.workspace_path, "src/worker.py", "CREATED")

        result = verifier.verify(
            execution_context=context,
            work_order=wo,
            allow_uncommitted=False,  # Uncommitted changes forbidden
        )

        assert result.verification_status == VerificationStatus.FAIL
        assert result.is_clean is False
        assert result.uncommitted_changes is True
        anomaly_types = [a.anomaly_type for a in result.anomalies]
        assert RepositoryAnomalyType.UNEXPECTED_UNCOMMITTED_CHANGES in anomaly_types

    def test_deleted_files_in_writable_and_forbidden_scopes(self, setup_env):
        """Verify deleted files are verified against authorized writable scope."""
        git_ops: FakeGitOperations = setup_env["git_ops"]
        verifier: RepositoryStateVerifier = setup_env["verifier"]
        context: GitExecutionContext = setup_env["context"]
        wo: ProgrammerWorkOrder = setup_env["work_order"]

        # Case 1: Deletion in writable path is allowed
        git_ops.stage_change(context.workspace_path, "src/old_module.py", "DELETED")
        res1 = verifier.verify(
            execution_context=context,
            work_order=wo,
            allow_uncommitted=True,
        )
        assert res1.verification_status == VerificationStatus.PASS
        assert "src/old_module.py" in res1.deleted_files
        assert len(res1.unauthorized_changes) == 0

        # Case 2: Deletion in forbidden path is blocked
        git_ops.stage_change(context.workspace_path, "secrets/key.pem", "DELETED")
        res2 = verifier.verify(
            execution_context=context,
            work_order=wo,
            allow_uncommitted=True,
        )
        assert res2.verification_status == VerificationStatus.FAIL
        assert res2.has_unauthorized_changes is True
        unauth_paths = [u.path for u in res2.unauthorized_changes]
        assert "secrets/key.pem" in unauth_paths

    def test_renamed_files_boundary_checks(self, setup_env):
        """Verify renamed files evaluate both source and destination scopes."""
        git_ops: FakeGitOperations = setup_env["git_ops"]
        verifier: RepositoryStateVerifier = setup_env["verifier"]
        context: GitExecutionContext = setup_env["context"]
        wo: ProgrammerWorkOrder = setup_env["work_order"]

        # Case 1: Rename within writable scope (src/a.py -> src/b.py)
        git_ops.stage_rename(context.workspace_path, "src/a.py", "src/b.py")
        res1 = verifier.verify(
            execution_context=context,
            work_order=wo,
            allow_uncommitted=True,
        )
        assert res1.verification_status == VerificationStatus.PASS
        assert len(res1.renamed_files) == 1
        assert res1.renamed_files[0].old_path == "src/a.py"
        assert res1.renamed_files[0].new_path == "src/b.py"

        # Case 2: Rename destination into forbidden scope (src/c.py -> secrets/c.py)
        git_ops.stage_rename(context.workspace_path, "src/c.py", "secrets/c.py")
        res2 = verifier.verify(
            execution_context=context,
            work_order=wo,
            allow_uncommitted=True,
        )
        assert res2.verification_status == VerificationStatus.FAIL
        assert res2.has_unauthorized_changes is True

    def test_concurrent_execution_contamination(self, setup_env):
        """Verify commits with a foreign Execution-ID trailer/metadata trigger contamination anomaly."""
        git_ops: FakeGitOperations = setup_env["git_ops"]
        verifier: RepositoryStateVerifier = setup_env["verifier"]
        context: GitExecutionContext = setup_env["context"]
        wo: ProgrammerWorkOrder = setup_env["work_order"]

        # Simulate foreign execution commit
        foreign_exec_id = new_execution_id()
        foreign_msg = (
            f"Commit by concurrent execution\n\n"
            f"Execution-ID: {foreign_exec_id}\n"
            f"Work-Order-ID: {wo.work_order_id}\n"
            f"Base-Revision: {context.base_revision.commit_hash}"
        )
        git_ops.commit(
            repo_or_worktree_path=context.workspace_path,
            message=foreign_msg,
            metadata={"execution_id": foreign_exec_id, "work_order_id": wo.work_order_id},
        )

        result = verifier.verify(
            execution_context=context,
            work_order=wo,
            allow_uncommitted=False,
        )

        assert result.verification_status == VerificationStatus.FAIL
        anomaly_types = [a.anomaly_type for a in result.anomalies]
        assert RepositoryAnomalyType.CROSS_EXECUTION_CONTAMINATION in anomaly_types
        contamination_anomaly = next(a for a in result.anomalies if a.anomaly_type == RepositoryAnomalyType.CROSS_EXECUTION_CONTAMINATION)
        assert contamination_anomaly.details["contaminating_execution_id"] == foreign_exec_id

    def test_cross_worktree_and_workspace_mismatch(self, setup_env):
        """Verify repository and workspace root mismatch trigger WORKSPACE_MISMATCH anomaly."""
        verifier: RepositoryStateVerifier = setup_env["verifier"]
        context: GitExecutionContext = setup_env["context"]
        wo: ProgrammerWorkOrder = setup_env["work_order"]
        repo: GitRepository = setup_env["repo"]

        # Create a mismatched workspace pointing to a different folder
        mismatched_ws = ProgrammerWorkspace(
            workspace_id="pws-other",
            project_id=context.project_id,
            execution_id=context.execution_id,
            work_order_id=wo.work_order_id,
            root_path="/tmp/unrelated_workspace_path",
        )

        result = verifier.verify(
            execution_context=context,
            work_order=wo,
            workspace=mismatched_ws,
            repository=repo,
        )

        assert result.verification_status == VerificationStatus.FAIL
        anomaly_types = [a.anomaly_type for a in result.anomalies]
        assert RepositoryAnomalyType.WORKSPACE_MISMATCH in anomaly_types

    def test_to_verification_check_and_evidence(self, setup_env):
        """Verify conversion to authoritative VerificationCheck and VerificationEvidence."""
        verifier: RepositoryStateVerifier = setup_env["verifier"]
        context: GitExecutionContext = setup_env["context"]
        wo: ProgrammerWorkOrder = setup_env["work_order"]

        result = verifier.verify(
            execution_context=context,
            work_order=wo,
            allow_uncommitted=False,
        )

        check = result.to_verification_check()
        assert check.status == VerificationStatus.PASS
        assert check.exit_code == 0
        assert check.execution_id == context.execution_id
        assert check.work_order_id == wo.work_order_id
        assert check.command == "repository_state_verifier"

        assert len(result.evidence) == 1
        ev = result.evidence[0]
        assert ev.source_type == VerificationEvidenceSourceType.GIT
        assert ev.is_authoritative() is True
        assert ev.execution_id == context.execution_id

    def test_serialization_roundtrip(self, setup_env):
        """Verify full to_dict, from_dict, and to_json fidelity."""
        verifier: RepositoryStateVerifier = setup_env["verifier"]
        context: GitExecutionContext = setup_env["context"]
        wo: ProgrammerWorkOrder = setup_env["work_order"]

        result = verifier.verify(
            execution_context=context,
            work_order=wo,
            allow_uncommitted=False,
        )

        d = result.to_dict()
        rehydrated = RepositoryStateVerification.from_dict(d)

        assert rehydrated.verification_id == result.verification_id
        assert rehydrated.execution_id == result.execution_id
        assert rehydrated.work_order_id == result.work_order_id
        assert rehydrated.verification_status == result.verification_status
        assert rehydrated.is_clean == result.is_clean

        json_str = result.to_json()
        assert result.verification_id in json_str

    def test_strict_non_modifying_invariant(self, setup_env):
        """Verify verifier never executes destructive mutations (no reset, clean, checkout)."""
        git_ops: FakeGitOperations = setup_env["git_ops"]
        verifier: RepositoryStateVerifier = setup_env["verifier"]
        context: GitExecutionContext = setup_env["context"]
        wo: ProgrammerWorkOrder = setup_env["work_order"]

        # Stage some changes
        git_ops.stage_change(context.workspace_path, "src/temp.py", "CREATED")

        # Run verification
        verifier.verify(
            execution_context=context,
            work_order=wo,
            allow_uncommitted=True,
        )

        # Confirm working tree changes still exist and were NOT wiped or reset
        status_after = git_ops.inspect_status(context.workspace_path)
        assert "src/temp.py" in status_after["files_created"]
