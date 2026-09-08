from __future__ import annotations

import os
from pathlib import Path
import tempfile
from typing import Any

import pytest

from core.enums import RiskLevel
from core.programmer.contracts.build_packaging import (
    BuildPackagingRequest,
    BuildPackagingResult,
    BuildPackagingRunner,
)
from core.programmer.contracts.command_scope import AllowedCommand
from core.programmer.contracts.escalation import (
    ProgrammerEscalation,
    ProgrammerEscalationCategory,
)
from core.programmer.contracts.git_model import (
    GitExecutionContext,
    GitRevision,
)
from core.programmer.contracts.identifiers import (
    new_execution_id,
    new_git_repository_id,
    new_git_revision_id,
    new_work_order_id,
)
from core.programmer.contracts.product_artifact import ProductArtifact
from core.programmer.contracts.work_order import ProgrammerWorkOrder
from core.programmer.errors import (
    ArtifactLineageError,
    DependencyAuthorizationError,
    MissingArtifactError,
    MissingBuildCommandError,
    ProgrammerValidationError,
    SourceRevisionMismatchError,
    UnauthorizedBuildCommandError,
)
from core.programmer.types import (
    BuildPackagingStatus,
    ProductArtifactType,
)


def make_work_order(
    project_id: str = "proj-build-test",
    work_order_id: str | None = None,
    allowed_commands: list[Any] | None = None,
    metadata: dict[str, Any] | None = None,
    dependencies: list[str] | None = None,
) -> ProgrammerWorkOrder:
    """Helper to create a valid ProgrammerWorkOrder for build testing."""
    wo_id = work_order_id or new_work_order_id()
    cmds = allowed_commands or [
        AllowedCommand(command="python", allow_args=True),
        AllowedCommand(command="cargo", allow_args=True),
        AllowedCommand(command="pip", allow_args=True),
    ]
    return ProgrammerWorkOrder(
        work_order_id=wo_id,
        manager_task_id="mtask-001",
        project_id=project_id,
        correlation_id="corr-build-001",
        objective="Build and package verified release",
        allowed_commands=cmds,
        metadata=dict(metadata or {}),
        dependencies=dependencies or ["fastapi>=0.100.0"],
    )


def make_git_context(
    work_order: ProgrammerWorkOrder,
    workspace_path: str,
    base_hash: str = "1" * 40,
    resulting_hash: str = "2" * 40,
    execution_id: str | None = None,
) -> GitExecutionContext:
    """Helper to create a valid GitExecutionContext."""
    exec_id = execution_id or new_execution_id()
    base_rev = GitRevision(revision_id=new_git_revision_id(), commit_hash=base_hash)
    res_rev = GitRevision(revision_id=new_git_revision_id(), commit_hash=resulting_hash)
    return GitExecutionContext(
        repository_id=new_git_repository_id(),
        execution_id=exec_id,
        work_order_id=work_order.work_order_id,
        project_id=work_order.project_id,
        base_revision=base_rev,
        resulting_revision=res_rev,
        workspace_path=workspace_path,
    )


class TestProgrammerBuildPackaging:
    """
    Comprehensive test suite for Phase 9.2 Build & Packaging.
    Verifies policy enforcement, revision linkage, missing artifact detection,
    non-silent dependency escalation, and non-deployability defaults.
    """

    def test_01_successful_build_produces_product_artifact(self) -> None:
        """Verify successful build creates an authoritative ProductArtifact with is_deployable = False."""
        with tempfile.TemporaryDirectory() as tmpdir:
            wo = make_work_order()
            git_ctx = make_git_context(wo, workspace_path=tmpdir)

            # Define fake command executor that creates the output artifact on disk
            artifact_rel_path = "dist/app-1.0.0.whl"

            def fake_executor(tokens: list[str], cwd: str, timeout: int) -> tuple[int, str, str]:
                full_path = os.path.join(cwd, artifact_rel_path)
                os.makedirs(os.path.dirname(full_path), exist_ok=True)
                with open(full_path, "wb") as f:
                    f.write(b"PK\x03\x04fake-wheel-content")
                return 0, "Successfully built app-1.0.0.whl", ""

            runner = BuildPackagingRunner(
                work_order=wo,
                git_context=git_ctx,
                command_executor=fake_executor,
            )

            req = BuildPackagingRequest(
                build_command="python -m build",
                artifact_reference=artifact_rel_path,
                artifact_type=ProductArtifactType.PACKAGE,
                version="1.0.0",
                runtime_requirements={"python": ">=3.12"},
            )

            result: BuildPackagingResult = runner.execute(req)

            assert result.success is True
            assert result.status == BuildPackagingStatus.SUCCESS
            assert result.exit_code == 0
            assert result.duration_ms >= 0
            assert len(result.evidence) == 1
            assert result.evidence[0].is_authoritative() is True

            # Verify ProductArtifact properties
            artifact = result.artifact
            assert artifact is not None
            assert artifact.artifact_id.startswith("part-")
            assert artifact.project_id == wo.project_id
            assert artifact.work_order_id == wo.work_order_id
            assert artifact.execution_id == git_ctx.execution_id
            assert artifact.source_revision.commit_hash == git_ctx.resulting_revision.commit_hash
            assert artifact.artifact_type == ProductArtifactType.PACKAGE
            assert artifact.artifact_reference == artifact_rel_path
            assert artifact.version == "1.0.0"
            assert artifact.is_deployable is False  # Core Invariant: non-deployable by default
            assert artifact.build_metadata["build_command"] == "python -m build"

    def test_02_failed_build_returns_failed_result_without_artifact(self) -> None:
        """Verify non-zero exit code captures failure evidence and does not emit an artifact."""
        with tempfile.TemporaryDirectory() as tmpdir:
            wo = make_work_order()
            git_ctx = make_git_context(wo, workspace_path=tmpdir)

            def failing_executor(tokens: list[str], cwd: str, timeout: int) -> tuple[int, str, str]:
                return 1, "Compiling...", "Error: syntax error in main.py line 42"

            runner = BuildPackagingRunner(
                work_order=wo,
                git_context=git_ctx,
                command_executor=failing_executor,
            )

            result: BuildPackagingResult = runner.execute(
                BuildPackagingRequest(
                    build_command="python -m build",
                    artifact_reference="dist/app.whl",
                )
            )

            assert result.success is False
            assert result.status == BuildPackagingStatus.FAILED
            assert result.exit_code == 1
            assert result.artifact is None
            assert len(result.evidence) == 1
            assert result.evidence[0].data["exit_code"] == 1
            assert "syntax error" in result.error_message

    def test_03_unauthorized_build_command_rejected_by_policy(self) -> None:
        """Verify build command not in allowed_commands or containing forbidden shell tokens is rejected."""
        with tempfile.TemporaryDirectory() as tmpdir:
            wo = make_work_order()
            git_ctx = make_git_context(wo, workspace_path=tmpdir)

            executed = []

            def tracked_executor(tokens: list[str], cwd: str, timeout: int) -> tuple[int, str, str]:
                executed.append(tokens)
                return 0, "ok", ""

            runner = BuildPackagingRunner(
                work_order=wo,
                git_context=git_ctx,
                command_executor=tracked_executor,
            )

            # Command not in work order allowed_commands
            with pytest.raises(UnauthorizedBuildCommandError) as exc_info:
                runner.execute(
                    BuildPackagingRequest(build_command="make build-all"),
                    raise_on_error=True,
                )
            assert "unauthorized" in str(exc_info.value).lower()
            assert len(executed) == 0  # Command was never executed

            # Test raise_on_error = False returns UNAUTHORIZED status
            res = runner.execute(
                BuildPackagingRequest(build_command="make build-all"),
                raise_on_error=False,
            )
            assert res.success is False
            assert res.status == BuildPackagingStatus.UNAUTHORIZED

    def test_04_missing_build_command_handled(self) -> None:
        """Verify runner fails deterministically when no build command is provided or configured."""
        with tempfile.TemporaryDirectory() as tmpdir:
            wo = make_work_order()
            git_ctx = make_git_context(wo, workspace_path=tmpdir)

            runner = BuildPackagingRunner(work_order=wo, git_context=git_ctx)

            # With raise_on_error=True
            with pytest.raises(MissingBuildCommandError):
                runner.execute(BuildPackagingRequest(build_command=None), raise_on_error=True)

            # With raise_on_error=False
            res = runner.execute(BuildPackagingRequest(build_command=None), raise_on_error=False)
            assert res.success is False
            assert res.status == BuildPackagingStatus.MISSING_COMMAND

    def test_05_source_revision_mismatch_rejected(self) -> None:
        """Verify runner rejects a build request claiming a revision not in the git execution context."""
        with tempfile.TemporaryDirectory() as tmpdir:
            wo = make_work_order()
            git_ctx = make_git_context(wo, workspace_path=tmpdir, resulting_hash="2" * 40)

            foreign_rev = GitRevision(revision_id=new_git_revision_id(), commit_hash="9" * 40)

            runner = BuildPackagingRunner(work_order=wo, git_context=git_ctx)

            with pytest.raises(SourceRevisionMismatchError) as exc_info:
                runner.execute(
                    BuildPackagingRequest(
                        build_command="python -m build",
                        source_revision=foreign_rev,
                    ),
                    raise_on_error=True,
                )
            assert "does not match git execution context" in str(exc_info.value)
            assert exc_info.value.commit_hash == "9" * 40
            assert exc_info.value.expected_hash == "2" * 40

            # With raise_on_error=False
            res = runner.execute(
                BuildPackagingRequest(
                    build_command="python -m build",
                    source_revision=foreign_rev,
                ),
                raise_on_error=False,
            )
            assert res.success is False
            assert res.status == BuildPackagingStatus.MISMATCH

    def test_06_missing_artifact_on_zero_exit_code(self) -> None:
        """Verify runner detects when exit code is 0 but declared artifact file was not created."""
        with tempfile.TemporaryDirectory() as tmpdir:
            wo = make_work_order()
            git_ctx = make_git_context(wo, workspace_path=tmpdir)

            # Executor returns 0 but does NOT create the file
            def ghost_executor(tokens: list[str], cwd: str, timeout: int) -> tuple[int, str, str]:
                return 0, "Build finished successfully (no file created)", ""

            runner = BuildPackagingRunner(
                work_order=wo,
                git_context=git_ctx,
                command_executor=ghost_executor,
            )

            artifact_path = "dist/nonexistent.whl"

            with pytest.raises(MissingArtifactError) as exc_info:
                runner.execute(
                    BuildPackagingRequest(
                        build_command="python -m build",
                        artifact_reference=artifact_path,
                    ),
                    raise_on_error=True,
                )
            assert "declared artifact was not found on disk" in str(exc_info.value)
            assert exc_info.value.artifact_reference == artifact_path

            # With raise_on_error=False
            res = runner.execute(
                BuildPackagingRequest(
                    build_command="python -m build",
                    artifact_reference=artifact_path,
                ),
                raise_on_error=False,
            )
            assert res.success is False
            assert res.status == BuildPackagingStatus.MISSING_ARTIFACT

    def test_07_build_output_and_metadata_capture(self) -> None:
        """Verify duration, stdout/stderr snippets, and build metadata are captured in evidence."""
        with tempfile.TemporaryDirectory() as tmpdir:
            wo = make_work_order()
            git_ctx = make_git_context(wo, workspace_path=tmpdir)

            artifact_path = "dist/app.whl"

            def output_executor(tokens: list[str], cwd: str, timeout: int) -> tuple[int, str, str]:
                p = os.path.join(cwd, artifact_path)
                os.makedirs(os.path.dirname(p), exist_ok=True)
                with open(p, "w") as f:
                    f.write("content")
                return 0, "Step 1: compiling\nStep 2: linking\nDone!", "Warning: unused var"

            runner = BuildPackagingRunner(
                work_order=wo,
                git_context=git_ctx,
                command_executor=output_executor,
            )

            res = runner.execute(
                BuildPackagingRequest(
                    build_command="python -m build",
                    artifact_reference=artifact_path,
                )
            )

            assert res.success is True
            assert "Step 1: compiling" in res.output_snippet
            assert len(res.evidence) == 1
            ev_data = res.evidence[0].data
            assert ev_data["exit_code"] == 0
            assert "linking" in ev_data["stdout_snippet"]
            assert "unused var" in ev_data["stderr_snippet"]
            assert res.duration_ms >= 0

    def test_08_dependency_requirement_and_escalation(self) -> None:
        """Verify silent dependency installation is prohibited and triggers formal escalation."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # 1. Unauthorized dependency installation attempted
            wo = make_work_order()  # Does NOT have allow_dependency_installation metadata
            git_ctx = make_git_context(wo, workspace_path=tmpdir)

            runner = BuildPackagingRunner(work_order=wo, git_context=git_ctx)

            dep_cmd = "pip install -r requirements.txt"

            # raise_on_error = True raises DependencyAuthorizationError
            with pytest.raises(DependencyAuthorizationError) as exc_info:
                runner.execute(
                    BuildPackagingRequest(build_command=dep_cmd),
                    raise_on_error=True,
                )
            assert "requires explicit Manager authorization" in str(exc_info.value)
            assert exc_info.value.dependency_command == dep_cmd

            # raise_on_error = False produces ESCALATED result with ProgrammerEscalation
            res = runner.execute(
                BuildPackagingRequest(build_command=dep_cmd),
                raise_on_error=False,
            )
            assert res.success is False
            assert res.status == BuildPackagingStatus.ESCALATED
            assert res.escalation is not None
            assert res.escalation.category == ProgrammerEscalationCategory.DEPENDENCY
            assert res.escalation.work_order_id == wo.work_order_id
            assert res.escalation.execution_id == git_ctx.execution_id

            # 2. Authorized dependency installation: explicitly authorized by Manager in metadata
            wo_authorized = make_work_order(
                metadata={
                    "allow_dependency_installation": True,
                    "build_command": "pip install -r requirements.txt",
                    "artifact_reference": "dist/reqs.stamp",
                }
            )
            git_ctx_auth = make_git_context(wo_authorized, workspace_path=tmpdir)

            def dep_executor(tokens: list[str], cwd: str, timeout: int) -> tuple[int, str, str]:
                p = os.path.join(cwd, "dist/reqs.stamp")
                os.makedirs(os.path.dirname(p), exist_ok=True)
                with open(p, "w") as f:
                    f.write("installed")
                return 0, "Successfully installed dependencies", ""

            runner_auth = BuildPackagingRunner(
                work_order=wo_authorized,
                git_context=git_ctx_auth,
                command_executor=dep_executor,
            )
            res_auth = runner_auth.execute(
                BuildPackagingRequest(
                    build_command="pip install -r requirements.txt",
                    artifact_reference="dist/reqs.stamp",
                )
            )
            assert res_auth.success is True
            assert res_auth.status == BuildPackagingStatus.SUCCESS

    def test_09_lineage_preservation_and_mismatch_rejection(self) -> None:
        """Verify cross-project and cross-work-order mismatches are caught and rejected."""
        with tempfile.TemporaryDirectory() as tmpdir:
            wo = make_work_order(project_id="proj-alpha")

            # Mismatched project ID
            bad_proj_ctx = GitExecutionContext(
                repository_id=new_git_repository_id(),
                execution_id=new_execution_id(),
                work_order_id=wo.work_order_id,
                project_id="proj-beta",  # mismatch!
                base_revision=GitRevision(revision_id=new_git_revision_id(), commit_hash="1" * 40),
                workspace_path=tmpdir,
            )

            runner = BuildPackagingRunner(work_order=wo, git_context=bad_proj_ctx)
            with pytest.raises(ArtifactLineageError) as exc_info:
                runner.execute(BuildPackagingRequest(build_command="python -m build"))
            assert "Project mismatch" in str(exc_info.value)

            # Mismatched work order ID
            bad_wo_ctx = GitExecutionContext(
                repository_id=new_git_repository_id(),
                execution_id=new_execution_id(),
                work_order_id=new_work_order_id(),  # mismatch!
                project_id="proj-alpha",
                base_revision=GitRevision(revision_id=new_git_revision_id(), commit_hash="1" * 40),
                workspace_path=tmpdir,
            )

            runner_wo = BuildPackagingRunner(work_order=wo, git_context=bad_wo_ctx)
            with pytest.raises(ArtifactLineageError) as exc_info:
                runner_wo.execute(BuildPackagingRequest(build_command="python -m build"))
            assert "Work order mismatch" in str(exc_info.value)

    def test_10_target_directory_confinement(self) -> None:
        """Verify target directory cannot escape workspace root boundary."""
        with tempfile.TemporaryDirectory() as tmpdir:
            wo = make_work_order()
            git_ctx = make_git_context(wo, workspace_path=tmpdir)

            runner = BuildPackagingRunner(work_order=wo, git_context=git_ctx)

            # Path traversal attempt
            with pytest.raises(ProgrammerValidationError) as exc_info:
                runner.execute(
                    BuildPackagingRequest(
                        build_command="python -m build",
                        target_directory="../../escape",
                    )
                )
            assert "escapes workspace root" in str(exc_info.value)
