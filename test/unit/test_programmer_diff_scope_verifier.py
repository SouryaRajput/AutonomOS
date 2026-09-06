import os
import shutil
import subprocess
import tempfile
import pytest

from core.enums import RiskLevel
from core.programmer.contracts.diff_verifier import (
    DiffScopeVerifier,
    DiffVerification,
    ObservedDiff,
    RenamedFile,
    UnauthorizedChange,
    WorkspaceDiffInspector,
)
from core.programmer.contracts.identifiers import (
    new_diff_verification_id,
    new_execution_id,
    new_verification_evidence_id,
    new_work_order_id,
    new_workspace_id,
    validate_diff_verification_id,
)
from core.programmer.contracts.verification import VerificationEvidence
from core.programmer.contracts.work_order import ProgrammerWorkOrder
from core.programmer.contracts.workspace import ProgrammerWorkspace
from core.programmer.errors import (
    ProgrammerLineageError,
    ProgrammerValidationError,
)
from core.programmer.types import (
    PathBoundaryScope,
    VerificationEvidenceSourceType,
    VerificationStatus,
)


@pytest.fixture
def temp_workspace_dir():
    """Create a clean temporary directory for workspace operations."""
    tmp = tempfile.mkdtemp(prefix="autonomos_diff_test_")
    yield tmp
    shutil.rmtree(tmp, ignore_errors=True)


@pytest.fixture
def base_workspace(temp_workspace_dir: str) -> ProgrammerWorkspace:
    """Create a validated ProgrammerWorkspace fixture."""
    # Create subdirectories
    os.makedirs(os.path.join(temp_workspace_dir, "src", "auth"), exist_ok=True)
    os.makedirs(os.path.join(temp_workspace_dir, "docs"), exist_ok=True)
    os.makedirs(os.path.join(temp_workspace_dir, "secrets"), exist_ok=True)

    return ProgrammerWorkspace(
        workspace_id=new_workspace_id(),
        project_id="prj-diff-test",
        work_order_id=new_work_order_id(),
        execution_id=new_execution_id(),
        root_path=temp_workspace_dir,
        allowed_paths=["src/auth", "docs"],
        writable_paths=["src/auth"],
        read_only_paths=["docs"],
        forbidden_paths=["secrets"],
    )


@pytest.fixture
def base_work_order(base_workspace: ProgrammerWorkspace) -> ProgrammerWorkOrder:
    """Create a validated ProgrammerWorkOrder fixture matching the workspace."""
    return ProgrammerWorkOrder(
        work_order_id=base_workspace.work_order_id,
        manager_task_id="tsk-diff-001",
        project_id=base_workspace.project_id,
        correlation_id="corr-diff-001",
        objective="Implement auth session management",
        allowed_paths=list(base_workspace.allowed_paths),
        writable_paths=list(base_workspace.writable_paths),
        read_only_paths=list(base_workspace.read_only_paths),
        forbidden_paths=list(base_workspace.forbidden_paths),
        time_budget=300,
        iteration_budget=5,
        risk_level=RiskLevel.LOW,
    )


class TestDiffScopeVerifierUnit:
    """Unit tests for Programmer V1 Phase 4.4 DiffScopeVerifier and DiffVerification."""

    def test_authorized_changes_only_produces_pass(
        self,
        base_workspace: ProgrammerWorkspace,
        base_work_order: ProgrammerWorkOrder,
    ):
        """Changes strictly inside writable_paths evaluate to PASS."""
        verifier = DiffScopeVerifier()
        diff = ObservedDiff(
            files_changed=["src/auth/session.py"],
            files_created=["src/auth/token.py"],
            files_deleted=["src/auth/deprecated.py"],
            files_renamed=[RenamedFile("src/auth/old_api.py", "src/auth/new_api.py")],
            inspection_method="SNAPSHOT",
        )

        result = verifier.verify_diff(
            workspace=base_workspace,
            observed_diff=diff,
            work_order=base_work_order,
        )

        assert result.scope_status == VerificationStatus.PASS
        assert result.is_authorized is True
        assert result.has_unauthorized_changes is False
        assert len(result.unauthorized_changes) == 0
        assert len(result.evidence) == 1
        assert result.evidence[0].is_authoritative() is True
        assert result.evidence[0].source_type == VerificationEvidenceSourceType.FILESYSTEM

    def test_clean_workspace_no_changes_produces_pass(
        self,
        base_workspace: ProgrammerWorkspace,
        base_work_order: ProgrammerWorkOrder,
    ):
        """Zero modifications in the workspace evaluate to PASS."""
        verifier = DiffScopeVerifier()
        diff = ObservedDiff(
            files_changed=[],
            files_created=[],
            files_deleted=[],
            files_renamed=[],
            inspection_method="SNAPSHOT",
        )

        result = verifier.verify_diff(
            workspace=base_workspace,
            observed_diff=diff,
            work_order=base_work_order,
        )

        assert result.scope_status == VerificationStatus.PASS
        assert result.is_authorized is True
        assert result.total_changes_count == 0

    def test_unauthorized_modification_produces_fail(
        self,
        base_workspace: ProgrammerWorkspace,
        base_work_order: ProgrammerWorkOrder,
    ):
        """Modifying a file outside writable_paths triggers FAIL with diagnostic details."""
        verifier = DiffScopeVerifier()
        diff = ObservedDiff(
            files_changed=["README.md"],
            inspection_method="SNAPSHOT",
        )

        result = verifier.verify_diff(
            workspace=base_workspace,
            observed_diff=diff,
            work_order=base_work_order,
        )

        assert result.scope_status == VerificationStatus.FAIL
        assert result.is_authorized is False
        assert result.has_unauthorized_changes is True
        assert len(result.unauthorized_changes) == 1
        unauth = result.unauthorized_changes[0]
        assert unauth.path == "README.md"
        assert unauth.change_type == "MODIFIED"
        assert unauth.scope == PathBoundaryScope.OUTSIDE_BOUNDARY

    def test_forbidden_file_modification_produces_fail(
        self,
        base_workspace: ProgrammerWorkspace,
        base_work_order: ProgrammerWorkOrder,
    ):
        """Modifying a path in forbidden_paths is strictly unauthorized."""
        verifier = DiffScopeVerifier()
        diff = ObservedDiff(
            files_changed=["secrets/keys.json"],
            inspection_method="SNAPSHOT",
        )

        result = verifier.verify_diff(
            workspace=base_workspace,
            observed_diff=diff,
            work_order=base_work_order,
        )

        assert result.scope_status == VerificationStatus.FAIL
        assert result.has_unauthorized_changes is True
        assert result.unauthorized_changes[0].scope == PathBoundaryScope.FORBIDDEN

    def test_read_only_file_modification_produces_fail(
        self,
        base_workspace: ProgrammerWorkspace,
        base_work_order: ProgrammerWorkOrder,
    ):
        """Modifying a path in read_only_paths is strictly unauthorized (mere readability does not grant write)."""
        verifier = DiffScopeVerifier()
        diff = ObservedDiff(
            files_changed=["docs/architecture.md"],
            inspection_method="SNAPSHOT",
        )

        result = verifier.verify_diff(
            workspace=base_workspace,
            observed_diff=diff,
            work_order=base_work_order,
        )

        assert result.scope_status == VerificationStatus.FAIL
        assert result.unauthorized_changes[0].scope == PathBoundaryScope.READ_ONLY

    def test_unauthorized_file_creation_produces_fail(
        self,
        base_workspace: ProgrammerWorkspace,
        base_work_order: ProgrammerWorkOrder,
    ):
        """Creating a new file outside writable_paths is unauthorized."""
        verifier = DiffScopeVerifier()
        diff = ObservedDiff(
            files_created=["scripts/hack.py"],
            inspection_method="SNAPSHOT",
        )

        result = verifier.verify_diff(
            workspace=base_workspace,
            observed_diff=diff,
            work_order=base_work_order,
        )

        assert result.scope_status == VerificationStatus.FAIL
        assert "scripts/hack.py" in result.unauthorized_paths
        assert result.unauthorized_changes[0].change_type == "CREATED"

    def test_unauthorized_file_deletion_produces_fail(
        self,
        base_workspace: ProgrammerWorkspace,
        base_work_order: ProgrammerWorkOrder,
    ):
        """Deleting a file outside writable_paths is unauthorized."""
        verifier = DiffScopeVerifier()
        diff = ObservedDiff(
            files_deleted=["docs/index.html"],
            inspection_method="SNAPSHOT",
        )

        result = verifier.verify_diff(
            workspace=base_workspace,
            observed_diff=diff,
            work_order=base_work_order,
        )

        assert result.scope_status == VerificationStatus.FAIL
        assert result.unauthorized_changes[0].change_type == "DELETED"
        assert result.unauthorized_changes[0].scope == PathBoundaryScope.READ_ONLY

    def test_rename_across_boundary_source_outside_scope(
        self,
        base_workspace: ProgrammerWorkspace,
        base_work_order: ProgrammerWorkOrder,
    ):
        """Renaming where the source file is outside writable scope is unauthorized."""
        verifier = DiffScopeVerifier()
        diff = ObservedDiff(
            files_renamed=[RenamedFile("docs/guide.md", "src/auth/guide.md")],
            inspection_method="SNAPSHOT",
        )

        result = verifier.verify_diff(
            workspace=base_workspace,
            observed_diff=diff,
            work_order=base_work_order,
        )

        assert result.scope_status == VerificationStatus.FAIL
        assert result.unauthorized_changes[0].change_type == "RENAMED_SRC"
        assert result.unauthorized_changes[0].path == "docs/guide.md"

    def test_rename_across_boundary_destination_forbidden(
        self,
        base_workspace: ProgrammerWorkspace,
        base_work_order: ProgrammerWorkOrder,
    ):
        """Renaming where the destination is in forbidden scope is unauthorized."""
        verifier = DiffScopeVerifier()
        diff = ObservedDiff(
            files_renamed=[RenamedFile("src/auth/token.py", "secrets/token.py")],
            inspection_method="SNAPSHOT",
        )

        result = verifier.verify_diff(
            workspace=base_workspace,
            observed_diff=diff,
            work_order=base_work_order,
        )

        assert result.scope_status == VerificationStatus.FAIL
        assert result.unauthorized_changes[0].change_type == "RENAMED_DST"
        assert result.unauthorized_changes[0].path == "secrets/token.py"
        assert result.unauthorized_changes[0].scope == PathBoundaryScope.FORBIDDEN

    def test_path_traversal_detection(
        self,
        base_workspace: ProgrammerWorkspace,
        base_work_order: ProgrammerWorkOrder,
    ):
        """Path traversal tricks like '../' navigating outside root are rejected as unauthorized."""
        verifier = DiffScopeVerifier()
        diff = ObservedDiff(
            files_changed=["../../etc/passwd", "src/auth/../../outside.py"],
            inspection_method="SNAPSHOT",
        )

        result = verifier.verify_diff(
            workspace=base_workspace,
            observed_diff=diff,
            work_order=base_work_order,
        )

        assert result.scope_status == VerificationStatus.FAIL
        assert len(result.unauthorized_changes) == 2
        for u in result.unauthorized_changes:
            assert u.scope == PathBoundaryScope.OUTSIDE_BOUNDARY

    def test_path_normalization_handling(
        self,
        base_workspace: ProgrammerWorkspace,
        base_work_order: ProgrammerWorkOrder,
    ):
        """Redundant slashes, dot segments, and backslashes are normalized to standard POSIX form."""
        verifier = DiffScopeVerifier()
        diff = ObservedDiff(
            files_changed=["src//auth/./helpers.py", "src/auth/nested/../sub.py"],
            inspection_method="SNAPSHOT",
        )

        result = verifier.verify_diff(
            workspace=base_workspace,
            observed_diff=diff,
            work_order=base_work_order,
        )

        assert result.scope_status == VerificationStatus.PASS
        assert result.files_changed == ["src/auth/helpers.py", "src/auth/sub.py"]

    def test_unreliable_repository_state_returns_error(
        self,
        base_workspace: ProgrammerWorkspace,
        base_work_order: ProgrammerWorkOrder,
    ):
        """When diff inspection fails or cannot be reliably determined, returns ERROR."""
        verifier = DiffScopeVerifier()
        invalid_diff = ObservedDiff(
            is_valid=False,
            error_message="Filesystem permission denied",
            inspection_method="SNAPSHOT",
        )

        result = verifier.verify_diff(
            workspace=base_workspace,
            observed_diff=invalid_diff,
            work_order=base_work_order,
        )

        assert result.scope_status == VerificationStatus.ERROR
        assert result.is_authorized is False
        assert "permission denied" in result.metadata["error"]

    def test_nonexistent_workspace_root_returns_error(
        self,
        base_work_order: ProgrammerWorkOrder,
    ):
        """Workspace whose root_path does not exist on disk returns ERROR without throwing unhandled exceptions."""
        workspace = ProgrammerWorkspace(
            workspace_id=new_workspace_id(),
            project_id="prj-missing",
            work_order_id=base_work_order.work_order_id,
            root_path="/path/that/definitely/does/not/exist/99999",
            writable_paths=["src"],
        )
        verifier = DiffScopeVerifier()
        result = verifier.verify_workspace(workspace=workspace, work_order=base_work_order)

        assert result.scope_status == VerificationStatus.ERROR
        assert result.is_authorized is False
        assert "does not exist" in result.metadata["error"]


class TestWorkspaceDiffInspector:
    """Tests for physical workspace inspection via snapshots and git status."""

    def test_snapshot_diff_detection(
        self,
        base_workspace: ProgrammerWorkspace,
        temp_workspace_dir: str,
    ):
        """Baseline snapshot comparison accurately detects modified, created, deleted, and renamed files."""
        inspector = WorkspaceDiffInspector()

        # Step 1: Create initial file structure
        file_a = os.path.join(temp_workspace_dir, "src", "auth", "session.py")
        file_b = os.path.join(temp_workspace_dir, "src", "auth", "to_delete.py")
        file_c = os.path.join(temp_workspace_dir, "src", "auth", "to_rename.py")

        with open(file_a, "w") as f:
            f.write("initial session code")
        with open(file_b, "w") as f:
            f.write("content to delete")
        with open(file_c, "w") as f:
            f.write("content to be renamed")

        # Step 2: Capture baseline snapshot
        baseline = inspector.capture_snapshot(base_workspace)
        assert "src/auth/session.py" in baseline
        assert "src/auth/to_delete.py" in baseline
        assert "src/auth/to_rename.py" in baseline

        # Step 3: Mutate filesystem
        # Modify file_a
        with open(file_a, "w") as f:
            f.write("modified session code")

        # Delete file_b
        os.remove(file_b)

        # Rename file_c to renamed.py
        file_c_new = os.path.join(temp_workspace_dir, "src", "auth", "renamed.py")
        os.rename(file_c, file_c_new)

        # Create new file_d
        file_d = os.path.join(temp_workspace_dir, "src", "auth", "brand_new.py")
        with open(file_d, "w") as f:
            f.write("brand new file content")

        # Step 4: Run inspect_filesystem_diff
        diff = inspector.inspect_filesystem_diff(base_workspace, baseline)

        assert diff.is_valid is True
        assert diff.files_changed == ["src/auth/session.py"]
        assert diff.files_created == ["src/auth/brand_new.py"]
        assert diff.files_deleted == ["src/auth/to_delete.py"]
        assert len(diff.files_renamed) == 1
        assert diff.files_renamed[0].old_path == "src/auth/to_rename.py"
        assert diff.files_renamed[0].new_path == "src/auth/renamed.py"

    def test_git_diff_inspection(
        self,
        base_workspace: ProgrammerWorkspace,
        temp_workspace_dir: str,
    ):
        """Git working tree inspection properly parses git status porcelain v1 tokens."""
        # Initialize a real git repo in the temp workspace
        subprocess.run(["git", "init"], cwd=temp_workspace_dir, check=True, capture_output=True)
        subprocess.run(["git", "config", "user.name", "AutonomOS Test"], cwd=temp_workspace_dir, check=True, capture_output=True)
        subprocess.run(["git", "config", "user.email", "test@autonomos.ai"], cwd=temp_workspace_dir, check=True, capture_output=True)

        initial_file = os.path.join(temp_workspace_dir, "src", "auth", "base.py")
        with open(initial_file, "w") as f:
            f.write("initial git tracked code")

        subprocess.run(["git", "add", "."], cwd=temp_workspace_dir, check=True, capture_output=True)
        subprocess.run(["git", "commit", "-m", "initial commit"], cwd=temp_workspace_dir, check=True, capture_output=True)

        # Now perform changes in working tree:
        # 1. Modify base.py
        with open(initial_file, "w") as f:
            f.write("modified git tracked code")

        # 2. Add untracked new file
        new_file = os.path.join(temp_workspace_dir, "src", "auth", "new.py")
        with open(new_file, "w") as f:
            f.write("untracked git code")

        inspector = WorkspaceDiffInspector()
        diff = inspector.inspect_git_diff(base_workspace)

        assert diff.is_valid is True
        assert diff.inspection_method == "GIT"
        assert "src/auth/base.py" in diff.files_changed
        assert "src/auth/new.py" in diff.files_created


class TestDiffVerificationContract:
    """Tests for DiffVerification contract invariants, serialization, and check conversion."""

    def test_diff_verification_id_generation_and_validation(self):
        v_id = new_diff_verification_id()
        assert v_id.startswith("vdiff-")
        validate_diff_verification_id(v_id)

    def test_diff_verification_pass_invariant_enforcement(self):
        """DiffVerification marked PASS cannot contain unauthorized changes."""
        ev = VerificationEvidence(
            execution_id=new_execution_id(),
            work_order_id=new_work_order_id(),
            source_type=VerificationEvidenceSourceType.FILESYSTEM,
            is_agent_claim=False,
        )
        unauth = UnauthorizedChange(
            path="forbidden.py",
            change_type="MODIFIED",
            scope=PathBoundaryScope.FORBIDDEN,
            reason="Forbidden",
        )

        dv = DiffVerification(
            execution_id=ev.execution_id,
            work_order_id=ev.work_order_id,
            unauthorized_changes=[unauth],
            scope_status=VerificationStatus.PASS,
            evidence=[ev],
        )

        with pytest.raises(ProgrammerValidationError) as exc:
            dv.validate()
        assert "cannot contain unauthorized changes" in str(exc.value)

    def test_diff_verification_serialization_roundtrip(self):
        exec_id = new_execution_id()
        wo_id = new_work_order_id()
        ev = VerificationEvidence(
            execution_id=exec_id,
            work_order_id=wo_id,
            source_type=VerificationEvidenceSourceType.FILESYSTEM,
            is_agent_claim=False,
            data={"test": 1},
        )
        unauth = UnauthorizedChange(
            path="secrets/key.pem",
            change_type="CREATED",
            scope=PathBoundaryScope.FORBIDDEN,
            reason="Forbidden write",
        )
        renamed = RenamedFile("src/old.py", "src/new.py")

        dv = DiffVerification(
            execution_id=exec_id,
            work_order_id=wo_id,
            files_changed=["src/a.py"],
            files_created=["src/b.py"],
            files_deleted=["src/c.py"],
            files_renamed=[renamed],
            unauthorized_changes=[unauth],
            scope_status=VerificationStatus.FAIL,
            evidence=[ev],
            metadata={"source": "test"},
        )

        data = dv.to_dict()
        rehydrated = DiffVerification.from_dict(data)

        assert rehydrated.verification_id == dv.verification_id
        assert rehydrated.execution_id == exec_id
        assert rehydrated.work_order_id == wo_id
        assert rehydrated.scope_status == VerificationStatus.FAIL
        assert rehydrated.files_changed == ["src/a.py"]
        assert len(rehydrated.files_renamed) == 1
        assert rehydrated.files_renamed[0].old_path == "src/old.py"
        assert len(rehydrated.unauthorized_changes) == 1
        assert rehydrated.unauthorized_changes[0].path == "secrets/key.pem"
        assert len(rehydrated.evidence) == 1
        assert rehydrated.evidence[0].evidence_id == ev.evidence_id

    def test_to_verification_check_conversion(self):
        exec_id = new_execution_id()
        wo_id = new_work_order_id()
        ev = VerificationEvidence(
            execution_id=exec_id,
            work_order_id=wo_id,
            source_type=VerificationEvidenceSourceType.FILESYSTEM,
            is_agent_claim=False,
        )
        dv = DiffVerification(
            execution_id=exec_id,
            work_order_id=wo_id,
            files_changed=["src/auth/login.py"],
            scope_status=VerificationStatus.PASS,
            evidence=[ev],
        )

        chk = dv.to_verification_check()
        assert chk.execution_id == exec_id
        assert chk.work_order_id == wo_id
        assert chk.status == VerificationStatus.PASS
        assert chk.exit_code == 0
        assert ev.evidence_id in chk.evidence
        assert "Scope Status: PASS" in chk.output_snippet
