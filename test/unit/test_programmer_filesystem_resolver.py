"""
Adversarial Unit Tests for Programmer V1 Phase 2.3: Filesystem Boundary Resolver.

Verifies deterministic path normalization, scope classification, and operation authorization:
1. Path traversal (../) rejection
2. Sibling directory prefix attacks (e.g. src/auth_evil vs src/auth)
3. Absolute paths (both outside and within workspace root)
4. Nested forbidden path priority (absolute veto across all operations)
5. Nested read-only paths (allowed for READ, denied for mutation)
6. Nested writable paths (allowed for mutation)
7. Rename operations across boundaries (source and destination independent write verification)
8. Delete restrictions (only explicitly writable paths can be deleted)
9. Normalized equivalent paths (redundant slashes, backslashes, relative dots)
10. Empty, whitespace-only, and null-byte malformed paths
11. Symlink escape detection and runtime limitation disclosure
12. Serialization and deserialization fidelity
"""

import os
from pathlib import Path
import pytest

from core.programmer.contracts.filesystem_resolver import (
    SYMLINK_SECURITY_LIMITATION,
    FilesystemBoundaryResolver,
    FilesystemDecision,
    is_subpath_or_equal,
)
from core.programmer.contracts.identifiers import new_work_order_id, new_workspace_id
from core.programmer.contracts.workspace import ProgrammerWorkspace
from core.programmer.types import (
    FilesystemOperation,
    PathBoundaryScope,
    WorkspaceIsolationMode,
)


@pytest.fixture
def workspace_dir(tmp_path: Path) -> Path:
    ws = tmp_path / "workspace"
    ws.mkdir(parents=True, exist_ok=True)
    (ws / "src").mkdir(parents=True, exist_ok=True)
    (ws / "src" / "auth").mkdir(parents=True, exist_ok=True)
    (ws / "tests").mkdir(parents=True, exist_ok=True)
    return ws


@pytest.fixture
def bounded_workspace(workspace_dir: Path) -> ProgrammerWorkspace:
    """
    Standard bounded workspace for policy testing:
    - allowed: "src", "tests"
    - writable: "src/auth/session.py", "tests/test_session.py", "src/writable_dir"
    - read_only: "src/auth/config.json", "src/auth/keys/public.pem"
    - forbidden: "src/secrets.env", "src/auth/keys/private.pem", "config"
    """
    return ProgrammerWorkspace(
        workspace_id=new_workspace_id(),
        project_id="prj-test",
        work_order_id=new_work_order_id(),
        root_path=str(workspace_dir),
        allowed_paths=["src", "tests"],
        writable_paths=[
            "src/auth/session.py",
            "tests/test_session.py",
            "src/writable_dir",
        ],
        read_only_paths=[
            "src/auth/config.json",
            "src/auth/keys/public.pem",
        ],
        forbidden_paths=[
            "src/secrets.env",
            "src/auth/keys/private.pem",
            "config",
        ],
        isolation_mode=WorkspaceIsolationMode.SHARED,
    )


@pytest.fixture
def resolver() -> FilesystemBoundaryResolver:
    return FilesystemBoundaryResolver()


# ==============================================================================
# 1. Path Traversal Attacks (../)
# ==============================================================================


def test_path_traversal_rejection(
    resolver: FilesystemBoundaryResolver,
    bounded_workspace: ProgrammerWorkspace,
) -> None:
    """Traversal escaping workspace root must be strictly rejected."""
    traversal_attacks = [
        "../escape.txt",
        "../../etc/passwd",
        "src/../../outside.txt",
        "src/auth/../../../etc/shadow",
        "..",
        "./../secret",
        "src/auth/../../../../../../root.txt",
    ]

    for attack in traversal_attacks:
        for op in [FilesystemOperation.READ, FilesystemOperation.WRITE, FilesystemOperation.DELETE]:
            decision = resolver.resolve(bounded_workspace, attack, op)
            assert decision.allowed is False, f"Expected denial for '{attack}' with op '{op}'"
            assert decision.scope == PathBoundaryScope.OUTSIDE_BOUNDARY
            assert decision.policy_rule == "PATH_TRAVERSAL_OR_INVALID"
            assert "traversal" in decision.reason.lower() or "outside" in decision.reason.lower()


# ==============================================================================
# 2. Sibling Directory Prefix Confusion Attacks
# ==============================================================================


def test_sibling_directories_with_similar_names(
    resolver: FilesystemBoundaryResolver,
    workspace_dir: Path,
) -> None:
    """
    Ensure string prefix checks do not confuse sibling directories with authorized prefixes.
    e.g., 'src/auth_evil' must NOT match allowed/writable 'src/auth'
    """
    ws = ProgrammerWorkspace(
        workspace_id=new_workspace_id(),
        project_id="prj-test",
        work_order_id=new_work_order_id(),
        root_path=str(workspace_dir),
        allowed_paths=["src/auth", "src/writable_dir", "tests"],
        writable_paths=["src/auth/session.py", "src/writable_dir"],
    )

    sibling_attacks = [
        "src/auth_evil/session.py",
        "src/auth2/session.py",
        "src/authentic/session.py",
        "src/auth.backup/session.py",
        "src/writable_directory_evil/file.py",
        "tests_evil/test_session.py",
    ]

    for attack in sibling_attacks:
        for op in [FilesystemOperation.READ, FilesystemOperation.WRITE]:
            decision = resolver.resolve(ws, attack, op)
            assert decision.allowed is False, f"Expected denial for sibling path '{attack}' with op '{op}'"
            assert decision.scope == PathBoundaryScope.OUTSIDE_BOUNDARY


def test_is_subpath_or_equal_exact_boundaries() -> None:
    """Verify segment-aware matching logic directly."""
    assert is_subpath_or_equal("src/auth/session.py", "src/auth") is True
    assert is_subpath_or_equal("src/auth", "src/auth") is True
    assert is_subpath_or_equal("src/auth_evil/session.py", "src/auth") is False
    assert is_subpath_or_equal("src/auth2", "src/auth") is False
    assert is_subpath_or_equal("src", "src/auth") is False


# ==============================================================================
# 3. Absolute Paths Handling
# ==============================================================================


def test_absolute_paths_outside_root(
    resolver: FilesystemBoundaryResolver,
    bounded_workspace: ProgrammerWorkspace,
) -> None:
    """Absolute paths outside workspace root must be denied as OUTSIDE_BOUNDARY."""
    outside_abs = [
        "/etc/passwd",
        "/var/log/system.log",
        "/tmp/malicious.sh",
        os.path.abspath(os.path.join(bounded_workspace.root_path, "..", "outside.txt")),
    ]

    for path in outside_abs:
        decision = resolver.resolve(bounded_workspace, path, FilesystemOperation.READ)
        assert decision.allowed is False
        assert decision.scope == PathBoundaryScope.OUTSIDE_BOUNDARY
        assert decision.policy_rule == "PATH_TRAVERSAL_OR_INVALID"
        assert "escapes workspace root" in decision.reason


def test_absolute_paths_within_root(
    resolver: FilesystemBoundaryResolver,
    bounded_workspace: ProgrammerWorkspace,
) -> None:
    """Absolute paths within workspace root must be normalized and authorized correctly."""
    abs_writable = os.path.join(bounded_workspace.root_path, "src", "auth", "session.py")
    decision = resolver.resolve(bounded_workspace, abs_writable, FilesystemOperation.WRITE)

    assert decision.allowed is True
    assert decision.normalized_path == "src/auth/session.py"
    assert decision.scope == PathBoundaryScope.WRITABLE
    assert decision.policy_rule == "WRITE_PERMITTED"


# ==============================================================================
# 4. Nested Forbidden Paths (Absolute Veto)
# ==============================================================================


def test_forbidden_nested_paths_veto_all_operations(
    resolver: FilesystemBoundaryResolver,
    bounded_workspace: ProgrammerWorkspace,
) -> None:
    """Forbidden paths must be denied across READ, WRITE, CREATE, DELETE, and RENAME."""
    forbidden_targets = [
        "src/secrets.env",
        "src/auth/keys/private.pem",
        "config/system.conf",
    ]

    for target in forbidden_targets:
        for op in [
            FilesystemOperation.READ,
            FilesystemOperation.WRITE,
            FilesystemOperation.CREATE,
            FilesystemOperation.DELETE,
        ]:
            decision = resolver.resolve(bounded_workspace, target, op)
            assert decision.allowed is False, f"Expected forbidden rejection for '{target}' with '{op}'"
            assert decision.scope == PathBoundaryScope.FORBIDDEN
            assert "FORBIDDEN" in decision.policy_rule


# ==============================================================================
# 5. Nested Read-Only Paths
# ==============================================================================


def test_read_only_nested_paths(
    resolver: FilesystemBoundaryResolver,
    bounded_workspace: ProgrammerWorkspace,
) -> None:
    """Read-only paths permit READ, but strictly deny WRITE, CREATE, DELETE, RENAME."""
    ro_target = "src/auth/config.json"

    # READ must succeed
    read_decision = resolver.resolve(bounded_workspace, ro_target, FilesystemOperation.READ)
    assert read_decision.allowed is True
    assert read_decision.scope == PathBoundaryScope.READ_ONLY
    assert read_decision.policy_rule == "READ_PERMITTED"

    # Mutation operations must fail
    for op in [FilesystemOperation.WRITE, FilesystemOperation.CREATE, FilesystemOperation.DELETE]:
        mut_decision = resolver.resolve(bounded_workspace, ro_target, op)
        assert mut_decision.allowed is False
        assert mut_decision.scope == PathBoundaryScope.READ_ONLY
        assert "DENIED_READ_ONLY" in mut_decision.policy_rule


# ==============================================================================
# 6. Writable vs Allowed-Only Non-Writable Paths
# ==============================================================================


def test_writable_nested_paths(
    resolver: FilesystemBoundaryResolver,
    bounded_workspace: ProgrammerWorkspace,
) -> None:
    """Paths explicitly in writable_paths permit both READ and mutations."""
    writable_target = "src/auth/session.py"

    for op in [
        FilesystemOperation.READ,
        FilesystemOperation.WRITE,
        FilesystemOperation.CREATE,
        FilesystemOperation.DELETE,
    ]:
        decision = resolver.resolve(bounded_workspace, writable_target, op)
        assert decision.allowed is True, f"Operation '{op}' should be permitted on writable target"
        assert decision.scope == PathBoundaryScope.WRITABLE


def test_allowed_scope_non_writable_paths(
    resolver: FilesystemBoundaryResolver,
    bounded_workspace: ProgrammerWorkspace,
) -> None:
    """Paths in allowed_paths but not writable permit READ, but deny mutations."""
    allowed_non_writable = "src/auth/helper.py"

    read_dec = resolver.resolve(bounded_workspace, allowed_non_writable, FilesystemOperation.READ)
    assert read_dec.allowed is True
    assert read_dec.scope == PathBoundaryScope.READ_ONLY

    write_dec = resolver.resolve(bounded_workspace, allowed_non_writable, FilesystemOperation.WRITE)
    assert write_dec.allowed is False
    assert write_dec.scope == PathBoundaryScope.READ_ONLY
    assert "DENIED_READ_ONLY" in write_dec.policy_rule

    del_dec = resolver.resolve(bounded_workspace, allowed_non_writable, FilesystemOperation.DELETE)
    assert del_dec.allowed is False
    assert "must be explicitly WRITABLE" in del_dec.reason


# ==============================================================================
# 7. Rename Operations Across Boundaries
# ==============================================================================


def test_rename_across_boundaries(
    resolver: FilesystemBoundaryResolver,
    bounded_workspace: ProgrammerWorkspace,
) -> None:
    """Both source and destination must be independently verified as WRITABLE."""
    writable_src = "src/auth/session.py"
    writable_dst = "src/writable_dir/session_new.py"
    read_only_target = "src/auth/config.json"
    forbidden_target = "src/secrets.env"
    outside_target = "outside_of_allowed.py"

    # Case A: Valid rename (both writable)
    res_valid = resolver.resolve(
        bounded_workspace,
        writable_src,
        FilesystemOperation.RENAME,
        destination_path=writable_dst,
    )
    assert res_valid.allowed is True
    assert res_valid.policy_rule == "RENAME_PERMITTED"
    assert res_valid.normalized_destination_path == "src/writable_dir/session_new.py"

    # Case B: Destination is forbidden
    res_dst_forbidden = resolver.resolve(
        bounded_workspace,
        writable_src,
        FilesystemOperation.RENAME,
        destination_path=forbidden_target,
    )
    assert res_dst_forbidden.allowed is False
    assert res_dst_forbidden.policy_rule == "RENAME_DESTINATION_DENIED"

    # Case C: Destination is read-only
    res_dst_ro = resolver.resolve(
        bounded_workspace,
        writable_src,
        FilesystemOperation.RENAME,
        destination_path=read_only_target,
    )
    assert res_dst_ro.allowed is False
    assert res_dst_ro.policy_rule == "RENAME_DESTINATION_DENIED"

    # Case D: Destination is outside boundary
    res_dst_outside = resolver.resolve(
        bounded_workspace,
        writable_src,
        FilesystemOperation.RENAME,
        destination_path=outside_target,
    )
    assert res_dst_outside.allowed is False
    assert res_dst_outside.policy_rule == "RENAME_DESTINATION_DENIED"

    # Case E: Source is read-only
    res_src_ro = resolver.resolve(
        bounded_workspace,
        read_only_target,
        FilesystemOperation.RENAME,
        destination_path=writable_dst,
    )
    assert res_src_ro.allowed is False
    assert res_src_ro.policy_rule == "RENAME_SOURCE_DENIED"

    # Case F: Source is forbidden
    res_src_forbid = resolver.resolve(
        bounded_workspace,
        forbidden_target,
        FilesystemOperation.RENAME,
        destination_path=writable_dst,
    )
    assert res_src_forbid.allowed is False
    assert res_src_forbid.policy_rule == "RENAME_SOURCE_DENIED"

    # Case G: Missing destination path
    res_no_dst = resolver.resolve(
        bounded_workspace,
        writable_src,
        FilesystemOperation.RENAME,
        destination_path=None,
    )
    assert res_no_dst.allowed is False
    assert res_no_dst.policy_rule == "MISSING_RENAME_DESTINATION"


# ==============================================================================
# 8. Delete Operations Outside Boundary and Non-Writable
# ==============================================================================


def test_delete_boundary_rules(
    resolver: FilesystemBoundaryResolver,
    bounded_workspace: ProgrammerWorkspace,
) -> None:
    """DELETE requires explicit WRITABLE scope; forbidden, read-only, and outside are denied."""
    # Delete writable -> ALLOWED
    d1 = resolver.resolve(bounded_workspace, "src/auth/session.py", FilesystemOperation.DELETE)
    assert d1.allowed is True
    assert d1.policy_rule == "DELETE_PERMITTED"

    # Delete read-only -> DENIED
    d2 = resolver.resolve(bounded_workspace, "src/auth/config.json", FilesystemOperation.DELETE)
    assert d2.allowed is False
    assert d2.policy_rule == "DELETE_DENIED_READ_ONLY"

    # Delete forbidden -> DENIED
    d3 = resolver.resolve(bounded_workspace, "src/secrets.env", FilesystemOperation.DELETE)
    assert d3.allowed is False
    assert d3.policy_rule == "DELETE_DENIED_FORBIDDEN"

    # Delete outside allowed -> DENIED
    d4 = resolver.resolve(bounded_workspace, "other_dir/file.txt", FilesystemOperation.DELETE)
    assert d4.allowed is False
    assert d4.policy_rule == "DELETE_DENIED_OUTSIDE_BOUNDARY"


# ==============================================================================
# 9. Normalized Equivalent Paths
# ==============================================================================


def test_normalized_equivalent_paths(
    resolver: FilesystemBoundaryResolver,
    bounded_workspace: ProgrammerWorkspace,
) -> None:
    """Various equivalent syntaxes for the same file must normalize identically."""
    variants = [
        "src/auth/session.py",
        "./src/auth/session.py",
        "src/auth/./session.py",
        "src//auth///session.py",
        "src/auth/extra/../session.py",
        "src\\auth\\session.py",
    ]

    for variant in variants:
        decision = resolver.resolve(bounded_workspace, variant, FilesystemOperation.WRITE)
        assert decision.allowed is True
        assert decision.normalized_path == "src/auth/session.py"
        assert decision.scope == PathBoundaryScope.WRITABLE


# ==============================================================================
# 10. Empty, Whitespace-only, and Malformed Paths
# ==============================================================================


def test_empty_and_malformed_paths(
    resolver: FilesystemBoundaryResolver,
    bounded_workspace: ProgrammerWorkspace,
) -> None:
    """Empty, whitespace-only, and null-byte paths must be safely rejected."""
    malformed = [
        "",
        "   ",
        "\t\n",
        "src/auth/\0session.py",
        None,  # type: ignore
        12345,  # type: ignore
    ]

    for m in malformed:
        decision = resolver.resolve(bounded_workspace, m, FilesystemOperation.READ)
        assert decision.allowed is False
        assert decision.scope == PathBoundaryScope.OUTSIDE_BOUNDARY
        assert decision.policy_rule == "PATH_TRAVERSAL_OR_INVALID"


# ==============================================================================
# 11. Symlink Boundary Escape & Limitation Disclosure
# ==============================================================================


def test_symlink_boundary_escape_and_limitation_disclosure(
    resolver: FilesystemBoundaryResolver,
    workspace_dir: Path,
    tmp_path: Path,
) -> None:
    """
    When a symlink inside workspace resolves outside workspace root:
    - It must be rejected with policy_rule='SYMLINK_ESCAPES_WORKSPACE'
    - Every decision must carry explicit limitation disclosure.
    """
    # Create an outside secret file
    outside_file = tmp_path / "outside_secret.txt"
    outside_file.write_text("TOP_SECRET")

    # Create a symlink inside the workspace pointing to outside
    symlink_path = workspace_dir / "src" / "symlink_escape.txt"
    os.symlink(str(outside_file), str(symlink_path))

    ws = ProgrammerWorkspace(
        workspace_id=new_workspace_id(),
        project_id="prj-test",
        work_order_id=new_work_order_id(),
        root_path=str(workspace_dir),
        allowed_paths=["src"],
        writable_paths=["src/symlink_escape.txt"],
        isolation_mode=WorkspaceIsolationMode.SHARED,
    )

    decision = resolver.resolve(ws, "src/symlink_escape.txt", FilesystemOperation.READ)

    assert decision.allowed is False
    assert decision.policy_rule == "SYMLINK_ESCAPES_WORKSPACE"
    assert decision.symlink_status == "ESCAPES_BOUNDARY"
    assert decision.scope == PathBoundaryScope.OUTSIDE_BOUNDARY
    assert "symlink" in decision.reason.lower()

    # Limitation disclosure must be present
    assert decision.symlink_limitation == SYMLINK_SECURITY_LIMITATION
    assert "Runtime TOCTOU" in decision.symlink_limitation


# ==============================================================================
# 12. Serialization & Deserialization Fidelity
# ==============================================================================


def test_decision_serialization_roundtrip(
    resolver: FilesystemBoundaryResolver,
    bounded_workspace: ProgrammerWorkspace,
) -> None:
    """Verify to_dict(), to_json(), and from_dict() roundtrip preservation."""
    # Test RENAME decision
    dec = resolver.resolve(
        bounded_workspace,
        "src/auth/session.py",
        FilesystemOperation.RENAME,
        destination_path="src/writable_dir/session2.py",
    )

    d_dict = dec.to_dict()
    d_json = dec.to_json()

    restored = FilesystemDecision.from_dict(d_dict)
    assert restored.allowed is True
    assert restored.normalized_path == "src/auth/session.py"
    assert restored.operation == FilesystemOperation.RENAME
    assert restored.scope == PathBoundaryScope.WRITABLE
    assert restored.destination_path == "src/writable_dir/session2.py"
    assert restored.normalized_destination_path == "src/writable_dir/session2.py"
    assert restored.destination_scope == PathBoundaryScope.WRITABLE
    assert restored.policy_rule == "RENAME_PERMITTED"
    assert restored.symlink_limitation == SYMLINK_SECURITY_LIMITATION
