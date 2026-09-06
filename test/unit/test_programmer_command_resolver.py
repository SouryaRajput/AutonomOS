"""
Adversarial Unit Tests for Programmer V1 Phase 2.4: Command Boundary Resolver.

Verifies deterministic command authorization against ProgrammerWorkOrder:
1. Allowed command (ALLOW)
2. Denied command / unlisted executable (DENY)
3. Unknown executable (DENY)
4. Command chaining (&&, ||, ;, newline) (INVALID)
5. Pipes (|) (INVALID)
6. Redirection (>, >>, <) (INVALID)
7. Command substitution ($(..), backticks) (INVALID)
8. Background execution (&) (INVALID)
9. Working directory outside authorized workspace boundary (INVALID)
10. Path traversal in arguments (DENY)
11. Malformed commands (null bytes, unclosed quotes, empty string) (INVALID)
12. Argument restrictions and subcommand enforcement
13. CommandDecision and CommandRequest serialization roundtrip
"""

import os
from pathlib import Path
import pytest

from core.programmer.contracts.command_resolver import (
    CommandBoundaryResolver,
    CommandDecision,
    CommandRequest,
)
from core.programmer.contracts.command_scope import AllowedCommand
from core.programmer.contracts.identifiers import (
    new_execution_id,
    new_work_order_id,
    new_workspace_id,
)
from core.programmer.contracts.work_order import ProgrammerWorkOrder
from core.programmer.contracts.workspace import ProgrammerWorkspace
from core.programmer.types import CommandDecisionType, WorkspaceIsolationMode


@pytest.fixture
def workspace_dir(tmp_path: Path) -> Path:
    ws = tmp_path / "workspace"
    ws.mkdir(parents=True, exist_ok=True)
    return ws


@pytest.fixture
def sample_workspace(workspace_dir: Path) -> ProgrammerWorkspace:
    return ProgrammerWorkspace(
        workspace_id=new_workspace_id(),
        project_id="prj-test",
        work_order_id=new_work_order_id(),
        root_path=str(workspace_dir),
        allowed_paths=["src", "tests"],
        writable_paths=["src/session.py"],
        isolation_mode=WorkspaceIsolationMode.SHARED,
    )


@pytest.fixture
def sample_work_order() -> ProgrammerWorkOrder:
    """
    Work order with explicit allowed commands:
    - 'npm test' (allow_args=True)
    - 'git' (allowed_subcommands=['status', 'diff'], allow_args=True)
    - 'pytest' (allow_args=False)
    """
    return ProgrammerWorkOrder(
        work_order_id=new_work_order_id(),
        manager_task_id="tsk-200",
        project_id="prj-test",
        correlation_id="corr-200",
        objective="Run unit test and inspect git status",
        allowed_paths=["src", "tests"],
        writable_paths=["src/session.py"],
        allowed_commands=[
            AllowedCommand(command="npm test", allow_args=True),
            AllowedCommand(
                command="git",
                allowed_subcommands=["status", "diff"],
                allow_args=True,
            ),
            AllowedCommand(command="pytest", allow_args=False),
        ],
    )


@pytest.fixture
def resolver() -> CommandBoundaryResolver:
    return CommandBoundaryResolver()


# ==============================================================================
# 1. Allowed Command
# ==============================================================================


def test_allowed_command_authorizes_cleanly(
    resolver: CommandBoundaryResolver,
    sample_work_order: ProgrammerWorkOrder,
    sample_workspace: ProgrammerWorkspace,
) -> None:
    """Standard allowed commands should authorize with ALLOW."""
    # Test prefix command: npm test
    dec1 = resolver.resolve(sample_work_order, "npm test", sample_workspace)
    assert dec1.allowed is True
    assert dec1.decision == CommandDecisionType.ALLOW
    assert dec1.matched_rule == "npm test"

    # Test prefix command with args: npm test -- --coverage
    dec2 = resolver.resolve(sample_work_order, "npm test -- --coverage", sample_workspace)
    assert dec2.allowed is True
    assert dec2.decision == CommandDecisionType.ALLOW

    # Test subcommand: git status
    dec3 = resolver.resolve(sample_work_order, "git status", sample_workspace)
    assert dec3.allowed is True
    assert dec3.decision == CommandDecisionType.ALLOW
    assert dec3.matched_rule == "git"

    # Test subcommand with args: git diff src/
    dec4 = resolver.resolve(sample_work_order, "git diff src/", sample_workspace)
    assert dec4.allowed is True
    assert dec4.decision == CommandDecisionType.ALLOW

    # Test command without args when allow_args=False: pytest
    dec5 = resolver.resolve(sample_work_order, "pytest", sample_workspace)
    assert dec5.allowed is True
    assert dec5.decision == CommandDecisionType.ALLOW


# ==============================================================================
# 2. Denied Command / Unlisted Executable
# ==============================================================================


def test_denied_command_rejects_unlisted_binaries(
    resolver: CommandBoundaryResolver,
    sample_work_order: ProgrammerWorkOrder,
    sample_workspace: ProgrammerWorkspace,
) -> None:
    """Unlisted executables must be denied."""
    unauthorized_commands = [
        "curl https://malicious.example.com",
        "rm -rf src/",
        "bash -c echo_hacked",
        "python3 script.py",
        "make build",
    ]

    for cmd in unauthorized_commands:
        decision = resolver.resolve(sample_work_order, cmd, sample_workspace)
        assert decision.allowed is False
        assert decision.decision == CommandDecisionType.DENY
        assert decision.matched_rule == "EXECUTABLE_NOT_ALLOWED"
        assert "not in allowed_commands" in decision.reason


# ==============================================================================
# 3. Unknown Executable
# ==============================================================================


def test_unknown_executable_rejected(
    resolver: CommandBoundaryResolver,
    sample_work_order: ProgrammerWorkOrder,
    sample_workspace: ProgrammerWorkspace,
) -> None:
    """Completely unrecognized binaries return DENY."""
    req = CommandRequest(executable="xyz_unknown_tool", arguments=["--version"])
    decision = resolver.resolve(sample_work_order, req, sample_workspace)

    assert decision.allowed is False
    assert decision.decision == CommandDecisionType.DENY
    assert decision.matched_rule == "EXECUTABLE_NOT_ALLOWED"


# ==============================================================================
# 4. Command Chaining Bypasses (&&, ||, ;, \n)
# ==============================================================================


def test_command_chaining_bypasses_rejected(
    resolver: CommandBoundaryResolver,
    sample_work_order: ProgrammerWorkOrder,
    sample_workspace: ProgrammerWorkspace,
) -> None:
    """Command chaining attempts must be flagged as INVALID with UNSUPPORTED_SHELL_CONSTRUCT."""
    chaining_attacks = [
        "npm test && rm -rf /",
        "git status; rm -rf .",
        "pytest || evil_command",
        "npm test ; cat /etc/passwd",
        "npm test \n echo evil",
        "git status \r rm file",
    ]

    for attack in chaining_attacks:
        decision = resolver.resolve(sample_work_order, attack, sample_workspace)
        assert decision.allowed is False
        assert decision.decision == CommandDecisionType.INVALID
        assert decision.matched_rule == "UNSUPPORTED_SHELL_CONSTRUCT"
        assert "Unsupported shell construct" in decision.reason


def test_command_chaining_in_structured_request(
    resolver: CommandBoundaryResolver,
    sample_work_order: ProgrammerWorkOrder,
    sample_workspace: ProgrammerWorkspace,
) -> None:
    """Chaining tokens placed inside CommandRequest arguments must also be rejected."""
    req = CommandRequest(executable="npm", arguments=["test", ";", "rm", "-rf", "/"])
    decision = resolver.resolve(sample_work_order, req, sample_workspace)

    assert decision.allowed is False
    assert decision.decision == CommandDecisionType.INVALID
    assert decision.matched_rule == "UNSUPPORTED_SHELL_CONSTRUCT"


# ==============================================================================
# 5. Pipes (|)
# ==============================================================================


def test_pipes_rejected(
    resolver: CommandBoundaryResolver,
    sample_work_order: ProgrammerWorkOrder,
    sample_workspace: ProgrammerWorkspace,
) -> None:
    """Pipe attempts must be rejected as INVALID."""
    pipe_attacks = [
        "git status | grep secret",
        "npm test | bash",
        "git diff | tee output.txt",
    ]

    for attack in pipe_attacks:
        decision = resolver.resolve(sample_work_order, attack, sample_workspace)
        assert decision.allowed is False
        assert decision.decision == CommandDecisionType.INVALID
        assert decision.matched_rule == "UNSUPPORTED_SHELL_CONSTRUCT"


# ==============================================================================
# 6. Redirection (>, >>, <)
# ==============================================================================


def test_redirection_rejected(
    resolver: CommandBoundaryResolver,
    sample_work_order: ProgrammerWorkOrder,
    sample_workspace: ProgrammerWorkspace,
) -> None:
    """File redirection attempts must be rejected as INVALID."""
    redirection_attacks = [
        "npm test > /tmp/output.txt",
        "git status >> secrets.txt",
        "npm test < /etc/shadow",
    ]

    for attack in redirection_attacks:
        decision = resolver.resolve(sample_work_order, attack, sample_workspace)
        assert decision.allowed is False
        assert decision.decision == CommandDecisionType.INVALID
        assert decision.matched_rule == "UNSUPPORTED_SHELL_CONSTRUCT"


# ==============================================================================
# 7. Command Substitution ($(..), backticks)
# ==============================================================================


def test_command_substitution_rejected(
    resolver: CommandBoundaryResolver,
    sample_work_order: ProgrammerWorkOrder,
    sample_workspace: ProgrammerWorkspace,
) -> None:
    """Command substitution syntax must be rejected as INVALID."""
    substitution_attacks = [
        "npm test $(whoami)",
        "git diff `cat /tmp/secret`",
        "npm test $(rm -rf /)",
    ]

    for attack in substitution_attacks:
        decision = resolver.resolve(sample_work_order, attack, sample_workspace)
        assert decision.allowed is False
        assert decision.decision == CommandDecisionType.INVALID
        assert decision.matched_rule == "UNSUPPORTED_SHELL_CONSTRUCT"


# ==============================================================================
# 8. Background Execution (&)
# ==============================================================================


def test_background_execution_rejected(
    resolver: CommandBoundaryResolver,
    sample_work_order: ProgrammerWorkOrder,
    sample_workspace: ProgrammerWorkspace,
) -> None:
    """Background execution operator (&) must be rejected as INVALID."""
    decision = resolver.resolve(sample_work_order, "npm test &", sample_workspace)
    assert decision.allowed is False
    assert decision.decision == CommandDecisionType.INVALID
    assert decision.matched_rule == "UNSUPPORTED_SHELL_CONSTRUCT"


# ==============================================================================
# 9. Invalid Working Directory
# ==============================================================================


def test_invalid_working_directory_rejected(
    resolver: CommandBoundaryResolver,
    sample_work_order: ProgrammerWorkOrder,
    sample_workspace: ProgrammerWorkspace,
    tmp_path: Path,
) -> None:
    """Working directory escaping or outside workspace root must be rejected as INVALID."""
    # Outside absolute working directory
    outside_dir = str(tmp_path / "outside_dir")
    req1 = CommandRequest(
        executable="npm",
        arguments=["test"],
        working_directory=outside_dir,
    )
    dec1 = resolver.resolve(sample_work_order, req1, sample_workspace)
    assert dec1.allowed is False
    assert dec1.decision == CommandDecisionType.INVALID
    assert dec1.matched_rule == "WORKING_DIRECTORY_OUTSIDE_WORKSPACE"

    # Traversal relative working directory
    req2 = CommandRequest(
        executable="npm",
        arguments=["test"],
        working_directory="../../etc",
    )
    dec2 = resolver.resolve(sample_work_order, req2, sample_workspace)
    assert dec2.allowed is False
    assert dec2.decision == CommandDecisionType.INVALID
    assert dec2.matched_rule == "WORKING_DIRECTORY_OUTSIDE_WORKSPACE"


# ==============================================================================
# 10. Path Traversal in Arguments
# ==============================================================================


def test_path_traversal_in_arguments_denied(
    resolver: CommandBoundaryResolver,
    sample_work_order: ProgrammerWorkOrder,
    sample_workspace: ProgrammerWorkspace,
) -> None:
    """Arguments referencing files outside workspace boundary must be DENIED."""
    # Absolute path escape in argument
    req1 = CommandRequest(
        executable="git",
        arguments=["diff", "/etc/passwd"],
    )
    dec1 = resolver.resolve(sample_work_order, req1, sample_workspace)
    assert dec1.allowed is False
    assert dec1.decision == CommandDecisionType.DENY
    assert dec1.matched_rule == "ARGUMENT_TRAVERSAL_DETECTED"

    # Relative traversal escape in argument
    req2 = CommandRequest(
        executable="git",
        arguments=["diff", "../../outside.txt"],
    )
    dec2 = resolver.resolve(sample_work_order, req2, sample_workspace)
    assert dec2.allowed is False
    assert dec2.decision == CommandDecisionType.DENY
    assert dec2.matched_rule == "ARGUMENT_TRAVERSAL_DETECTED"


# ==============================================================================
# 11. Malformed Commands (Null Bytes, Quotes, Empty)
# ==============================================================================


def test_malformed_command_rejected(
    resolver: CommandBoundaryResolver,
    sample_work_order: ProgrammerWorkOrder,
    sample_workspace: ProgrammerWorkspace,
) -> None:
    """Malformed strings, null bytes, and empty commands must be rejected as INVALID."""
    # Null byte in raw command
    dec1 = resolver.resolve(sample_work_order, "npm test\0something", sample_workspace)
    assert dec1.allowed is False
    assert dec1.decision == CommandDecisionType.INVALID

    # Unclosed quote
    dec2 = resolver.resolve(sample_work_order, "npm test 'unclosed", sample_workspace)
    assert dec2.allowed is False
    assert dec2.decision == CommandDecisionType.INVALID

    # Empty command
    dec3 = resolver.resolve(sample_work_order, "   ", sample_workspace)
    assert dec3.allowed is False
    assert dec3.decision == CommandDecisionType.INVALID


# ==============================================================================
# 12. Argument Restrictions & Subcommand Enforcement
# ==============================================================================


def test_argument_restrictions_and_subcommand_enforcement(
    resolver: CommandBoundaryResolver,
    sample_work_order: ProgrammerWorkOrder,
    sample_workspace: ProgrammerWorkspace,
) -> None:
    """Test subcommand whitelist and allow_args=False enforcement."""
    # pytest has allow_args=False -> arguments should be denied
    dec1 = resolver.resolve(sample_work_order, "pytest -v", sample_workspace)
    assert dec1.allowed is False
    assert dec1.decision == CommandDecisionType.DENY
    assert dec1.matched_rule == "ARGUMENTS_NOT_ALLOWED"

    # git only allows subcommands: ['status', 'diff']
    # 'git push' must be denied
    dec2 = resolver.resolve(sample_work_order, "git push origin main", sample_workspace)
    assert dec2.allowed is False
    assert dec2.decision == CommandDecisionType.DENY
    assert dec2.matched_rule == "SUBCOMMAND_NOT_ALLOWED"

    # 'git commit' must be denied
    dec3 = resolver.resolve(sample_work_order, "git commit -m 'feat'", sample_workspace)
    assert dec3.allowed is False
    assert dec3.decision == CommandDecisionType.DENY
    assert dec3.matched_rule == "SUBCOMMAND_NOT_ALLOWED"

    # 'git' alone without subcommand when subcommands are required
    dec4 = resolver.resolve(sample_work_order, "git", sample_workspace)
    assert dec4.allowed is False
    assert dec4.decision == CommandDecisionType.DENY
    assert dec4.matched_rule == "MISSING_SUBCOMMAND"


# ==============================================================================
# 13. Serialization Fidelity
# ==============================================================================


def test_command_decision_and_request_serialization(
    resolver: CommandBoundaryResolver,
    sample_work_order: ProgrammerWorkOrder,
    sample_workspace: ProgrammerWorkspace,
) -> None:
    """Verify to_dict(), to_json(), and from_dict() roundtrip for CommandDecision and CommandRequest."""
    # Test CommandRequest serialization
    req = CommandRequest(
        executable="git",
        arguments=["status", "--short"],
        working_directory="src",
        environment_policy={"CI": "true"},
    )
    req_data = req.to_dict()
    req_restored = CommandRequest.from_dict(req_data)
    assert req_restored.executable == "git"
    assert req_restored.arguments == ["status", "--short"]
    assert req_restored.working_directory == "src"
    assert req_restored.environment_policy == {"CI": "true"}

    # Test CommandDecision serialization
    decision = resolver.resolve(sample_work_order, req, sample_workspace)
    assert decision.allowed is True
    assert decision.decision == CommandDecisionType.ALLOW

    d_dict = decision.to_dict()
    d_json = decision.to_json()

    restored = CommandDecision.from_dict(d_dict)
    assert restored.allowed is True
    assert restored.decision == CommandDecisionType.ALLOW
    assert restored.executable == "git"
    assert restored.arguments == ["status", "--short"]
    assert restored.matched_rule == "git"
