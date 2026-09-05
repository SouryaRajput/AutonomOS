"""
Unit tests for Project VCS and Current Workspace State (Phase 1 / Part 8 / Step 7).
"""
import os
import shutil
import subprocess
import tempfile
import time
import pytest

from core.research.contracts.evidence import EvidenceProvenance
from core.research.errors import (
    ProjectCancelledError,
    ProjectTimeoutError,
)
from core.research.project.fake_provider import FakeProjectWorkspaceProvider
from core.research.project.local_provider import LocalProjectWorkspaceProvider
from core.research.project.models import (
    ProjectContext,
    ProjectIdentity,
    ProjectVCSContext,
    ProjectVCSState,
)
from core.research.project.vcs import (
    ProjectVCSInspector,
    ProjectVCSOptions,
)


@pytest.fixture
def temp_git_repo():
    """Create a clean local git repository fixture in a temporary directory."""
    tmp = tempfile.mkdtemp(prefix="test_vcs_repo_")
    try:
        # Initialize git repo
        subprocess.run(["git", "init"], cwd=tmp, check=True, capture_output=True)
        subprocess.run(["git", "config", "user.email", "test@autonomos.ai"], cwd=tmp, check=True, capture_output=True)
        subprocess.run(["git", "config", "user.name", "AutonomOS Test"], cwd=tmp, check=True, capture_output=True)

        # Initial commit
        initial_file = os.path.join(tmp, "README.md")
        with open(initial_file, "w", encoding="utf-8") as f:
            f.write("# Sample Project\n\nInitial content.")
        subprocess.run(["git", "add", "README.md"], cwd=tmp, check=True, capture_output=True)
        subprocess.run(["git", "commit", "-m", "Initial commit"], cwd=tmp, check=True, capture_output=True)

        yield tmp
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_clean_workspace_git_status(temp_git_repo):
    opts = ProjectVCSOptions(allow_vcs=True)
    vcs = ProjectVCSInspector.inspect(temp_git_repo, opts)

    assert vcs is not None
    assert vcs.working_tree_state == ProjectVCSState.CLEAN
    assert vcs.is_dirty is False
    assert vcs.modified_files == []
    assert vcs.deleted_files == []
    assert vcs.untracked_files == []
    assert vcs.staged_files == []
    assert vcs.unstaged_files == []
    assert vcs.revision is not None
    assert len(vcs.revision) == 40
    assert vcs.branch is not None


def test_modified_files_detection(temp_git_repo):
    # Modify tracked file
    readme = os.path.join(temp_git_repo, "README.md")
    with open(readme, "a", encoding="utf-8") as f:
        f.write("\nNew line modification.")

    opts = ProjectVCSOptions(allow_vcs=True)
    vcs = ProjectVCSInspector.inspect(temp_git_repo, opts)

    assert vcs is not None
    assert vcs.working_tree_state == ProjectVCSState.DIRTY
    assert vcs.is_dirty is True
    assert "README.md" in vcs.modified_files
    assert "README.md" in vcs.unstaged_files


def test_untracked_files_detection(temp_git_repo):
    # Create untracked file
    untracked = os.path.join(temp_git_repo, "new_file.py")
    with open(untracked, "w", encoding="utf-8") as f:
        f.write("print('untracked')")

    opts = ProjectVCSOptions(allow_vcs=True)
    vcs = ProjectVCSInspector.inspect(temp_git_repo, opts)

    assert vcs is not None
    assert "new_file.py" in vcs.untracked_files
    assert vcs.working_tree_state == ProjectVCSState.UNTRACKED


def test_deleted_files_detection(temp_git_repo):
    # Delete tracked file
    readme = os.path.join(temp_git_repo, "README.md")
    os.remove(readme)

    opts = ProjectVCSOptions(allow_vcs=True)
    vcs = ProjectVCSInspector.inspect(temp_git_repo, opts)

    assert vcs is not None
    assert vcs.working_tree_state == ProjectVCSState.DIRTY
    assert vcs.is_dirty is True
    assert "README.md" in vcs.deleted_files
    assert "README.md" in vcs.unstaged_files


def test_staged_unstaged_partitioning(temp_git_repo):
    # Create staged file
    staged = os.path.join(temp_git_repo, "staged.txt")
    with open(staged, "w", encoding="utf-8") as f:
        f.write("staged content")
    subprocess.run(["git", "add", "staged.txt"], cwd=temp_git_repo, check=True, capture_output=True)

    # Create unstaged modification
    readme = os.path.join(temp_git_repo, "README.md")
    with open(readme, "a", encoding="utf-8") as f:
        f.write("\nUnstaged mod")

    # Create untracked file
    untracked = os.path.join(temp_git_repo, "untracked.log")
    with open(untracked, "w", encoding="utf-8") as f:
        f.write("log")

    opts = ProjectVCSOptions(allow_vcs=True)
    vcs = ProjectVCSInspector.inspect(temp_git_repo, opts)

    assert vcs is not None
    assert "staged.txt" in vcs.staged_files
    assert "README.md" in vcs.unstaged_files
    assert "untracked.log" in vcs.untracked_files
    assert vcs.working_tree_state == ProjectVCSState.DIRTY


def test_branch_and_revision_retrieval(temp_git_repo):
    opts = ProjectVCSOptions(allow_vcs=True)
    vcs = ProjectVCSInspector.inspect(temp_git_repo, opts)

    assert vcs is not None
    assert vcs.branch in ("main", "master")
    assert len(vcs.revision) == 40
    assert vcs.author == "AutonomOS Test"
    assert vcs.commit_date is not None


def test_detached_head_handling(temp_git_repo):
    # Checkout detached HEAD
    res = subprocess.run(["git", "rev-parse", "HEAD"], cwd=temp_git_repo, check=True, capture_output=True, text=True)
    sha = res.stdout.strip()
    subprocess.run(["git", "checkout", "--detach", "HEAD"], cwd=temp_git_repo, check=True, capture_output=True)

    opts = ProjectVCSOptions(allow_vcs=True)
    vcs = ProjectVCSInspector.inspect(temp_git_repo, opts)

    assert vcs is not None
    assert vcs.revision == sha
    # Detached head should not be misreported as a named local branch
    assert vcs.branch is None


def test_remote_url_credential_sanitization(temp_git_repo):
    # Add remote with credentials
    raw_remote = "https://oauth2:secret_token_12345@github.com/my-org/my-project.git"
    subprocess.run(["git", "remote", "add", "origin", raw_remote], cwd=temp_git_repo, check=True, capture_output=True)

    opts = ProjectVCSOptions(allow_vcs=True)
    vcs = ProjectVCSInspector.inspect(temp_git_repo, opts)

    assert vcs is not None
    assert vcs.remote_url is not None
    assert "secret_token_12345" not in vcs.remote_url
    assert "github.com/my-org/my-project.git" in vcs.remote_url
    assert "[REDACTED]" in vcs.remote_url
    assert vcs.repository_id == "my-org/my-project"


def test_unavailable_vcs_when_not_allowed(temp_git_repo):
    # allow_vcs = False returns None
    opts = ProjectVCSOptions(allow_vcs=False)
    vcs = ProjectVCSInspector.inspect(temp_git_repo, opts)
    assert vcs is None

    # None options returns None
    assert ProjectVCSInspector.inspect(temp_git_repo, None) is None


def test_non_git_workspace():
    tmp = tempfile.mkdtemp(prefix="non_git_")
    try:
        opts = ProjectVCSOptions(allow_vcs=True)
        vcs = ProjectVCSInspector.inspect(tmp, opts)

        assert vcs is not None
        assert vcs.working_tree_state == ProjectVCSState.UNKNOWN
        assert vcs.vcs_type == "none"
        assert "Not a git repository" in vcs.metadata.get("reason", "")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_malformed_git_state_handling(temp_git_repo):
    # Corrupt the HEAD file
    head_file = os.path.join(temp_git_repo, ".git", "HEAD")
    with open(head_file, "w", encoding="utf-8") as f:
        f.write("corrupted garbage content")

    opts = ProjectVCSOptions(allow_vcs=True)
    vcs = ProjectVCSInspector.inspect(temp_git_repo, opts)

    # Should not crash; returns safe context with error
    assert vcs is not None
    assert vcs.working_tree_state == ProjectVCSState.UNKNOWN or vcs.branch is None


def test_project_root_containment(temp_git_repo):
    # Normalization should strip root escaping and keep only normalized relative paths
    raw_paths = ["src/../README.md", "a/b/../../README.md"]
    vcs = ProjectVCSContext(
        branch="main",
        working_tree_state=ProjectVCSState.DIRTY,
        modified_files=raw_paths,
    )
    assert vcs.modified_files == ["README.md"]


def test_max_changed_files_budget(temp_git_repo):
    # Create 10 untracked files
    for i in range(10):
        with open(os.path.join(temp_git_repo, f"file_{i}.txt"), "w") as f:
            f.write(str(i))

    opts = ProjectVCSOptions(allow_vcs=True, max_changed_files=4)
    vcs = ProjectVCSInspector.inspect(temp_git_repo, opts)

    assert vcs is not None
    assert len(vcs.untracked_files) == 4
    assert vcs.metadata.get("truncated") is True


def test_idempotent_repeated_execution(temp_git_repo):
    opts = ProjectVCSOptions(allow_vcs=True)
    vcs1 = ProjectVCSInspector.inspect(temp_git_repo, opts)
    vcs2 = ProjectVCSInspector.inspect(temp_git_repo, opts)

    assert vcs1 is not None and vcs2 is not None
    assert vcs1.branch == vcs2.branch
    assert vcs1.revision == vcs2.revision
    assert vcs1.working_tree_state == vcs2.working_tree_state
    assert vcs1.modified_files == vcs2.modified_files


def test_cooperative_cancellation(temp_git_repo):
    cancelled = True
    opts = ProjectVCSOptions(allow_vcs=True, is_cancelled=lambda: cancelled)
    with pytest.raises(ProjectCancelledError):
        ProjectVCSInspector.inspect(temp_git_repo, opts)


def test_timeout_handling(temp_git_repo):
    # Exceedingly small timeout
    opts = ProjectVCSOptions(allow_vcs=True, timeout_seconds=0.000001)
    with pytest.raises(ProjectTimeoutError):
        ProjectVCSInspector.inspect(temp_git_repo, opts)


def test_vcs_to_evidence_item():
    vcs = ProjectVCSContext(
        branch="feature/login",
        revision="1234567890abcdef1234567890abcdef12345678",
        working_tree_state=ProjectVCSState.DIRTY,
        repository_id="org/app",
        modified_files=["src/auth.py"],
        untracked_files=["tests/test_auth.py"],
    )
    ev = vcs.to_evidence_item(
        request_id="req-vcs-1",
        crawler_task_id="task-vcs-1",
        crawler_id="crawler.proj",
    )
    assert "ev-vcs-" in ev.evidence_id
    assert "feature/login" in ev.extracted_fact
    assert "12345678" in ev.extracted_fact
    assert ev.metadata["modified_files"] == ["src/auth.py"]
    assert ev.metadata["is_dirty"] is True


def test_vcs_serialization_round_trip():
    original = ProjectVCSContext(
        branch="main",
        revision="abcd1234efgh5678",
        working_tree_state=ProjectVCSState.DIRTY,
        repository_id="org/repo",
        modified_files=["app.py"],
        deleted_files=["old.py"],
        untracked_files=["new.py"],
        staged_files=["app.py"],
        unstaged_files=[],
        author="Dev <dev@example.com>",
        commit_date="2026-03-01T00:00:00Z",
        remote_url="https://github.com/org/repo.git",
        vcs_type="git",
    )
    data = original.to_dict()
    restored = ProjectVCSContext.from_dict(data)

    assert restored.branch == original.branch
    assert restored.revision == original.revision
    assert restored.working_tree_state == original.working_tree_state
    assert restored.repository_id == original.repository_id
    assert restored.modified_files == original.modified_files
    assert restored.deleted_files == original.deleted_files
    assert restored.untracked_files == original.untracked_files
    assert restored.staged_files == original.staged_files
    assert restored.is_dirty is True


def test_local_provider_vcs_delegation(temp_git_repo):
    provider = LocalProjectWorkspaceProvider(root_path=temp_git_repo)
    vcs = provider.get_vcs_metadata(allow_vcs=True)

    assert vcs is not None
    assert vcs.branch in ("main", "master")
    assert vcs.working_tree_state == ProjectVCSState.CLEAN

    # Not allowed returns None
    assert provider.get_vcs_metadata(allow_vcs=False) is None


def test_fake_provider_vcs_customization():
    fake = FakeProjectWorkspaceProvider()

    # Default VCS context
    default_vcs = fake.get_vcs_metadata(allow_vcs=True)
    assert default_vcs is not None
    assert default_vcs.branch == "main"
    assert default_vcs.working_tree_state == ProjectVCSState.CLEAN

    # Update with custom dirty state
    custom = ProjectVCSContext(
        branch="feature/custom",
        revision="999988887777",
        working_tree_state=ProjectVCSState.DIRTY,
        modified_files=["core/engine.py"],
    )
    fake.set_vcs_context(custom)
    retrieved = fake.get_vcs_metadata(allow_vcs=True)

    assert retrieved is not None
    assert retrieved.branch == "feature/custom"
    assert retrieved.working_tree_state == ProjectVCSState.DIRTY
    assert "core/engine.py" in retrieved.modified_files
