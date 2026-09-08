from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import logging
import os
from pathlib import Path
import shlex
import subprocess
import time
from typing import Any, Callable, Optional, Sequence, Union

from core.programmer.contracts.command_resolver import (
    CommandBoundaryResolver,
    CommandDecision,
    CommandRequest,
)
from core.programmer.contracts.escalation import (
    new_escalation_id,
    ProgrammerEscalation,
    ProgrammerEscalationCategory,
)
from core.programmer.contracts.execution_context import ProgrammerExecutionContext
from core.programmer.contracts.git_model import (
    GitExecutionContext,
    GitRevision,
)
from core.programmer.contracts.identifiers import (
    new_product_artifact_id,
    new_verification_evidence_id,
    validate_execution_id,
    validate_work_order_id,
)
from core.programmer.contracts.product_artifact import ProductArtifact
from core.programmer.contracts.verification import (
    VerificationEvidence,
)
from core.programmer.contracts.work_order import ProgrammerWorkOrder
from core.programmer.errors import (
    ArtifactLineageError,
    BuildPackagingError,
    DependencyAuthorizationError,
    MissingArtifactError,
    MissingBuildCommandError,
    NonexistentRevisionError,
    ProgrammerLineageError,
    ProgrammerValidationError,
    SourceRevisionMismatchError,
    UnauthorizedBuildCommandError,
)
from core.programmer.types import (
    BuildPackagingStatus,
    CommandDecisionType,
    ProductArtifactType,
    VerificationEvidenceSourceType,
)

logger = logging.getLogger("AutonomOS.Programmer.BuildPackaging")


def utc_now() -> str:
    """Generate ISO 8601 UTC timestamp."""
    return datetime.now(timezone.utc).isoformat()


DEPENDENCY_INSTALL_COMMAND_INDICATORS: tuple[str, ...] = (
    "pip install",
    "npm install",
    "npm i ",
    "yarn add",
    "yarn install",
    "poetry add",
    "poetry install",
    "cargo add",
    "cargo install",
    "apt-get install",
    "brew install",
    "gem install",
    "go get",
    "go install",
)


@dataclass
class BuildPackagingRequest:
    """
    Request specification for executing a build and packaging operation.
    """
    build_command: Optional[str] = None
    artifact_reference: Optional[str] = None
    artifact_type: ProductArtifactType = ProductArtifactType.PACKAGE
    version: Optional[str] = None
    source_revision: Optional[GitRevision] = None
    target_directory: Optional[str] = None
    install_dependencies: bool = False
    timeout_seconds: int = 300
    runtime_requirements: dict[str, Any] = field(default_factory=dict)
    dependencies: list[str] = field(default_factory=list)
    configuration_schema: dict[str, Any] = field(default_factory=dict)
    environment_requirements: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if isinstance(self.artifact_type, str):
            try:
                self.artifact_type = ProductArtifactType(self.artifact_type.upper())
            except (ValueError, TypeError):
                self.artifact_type = ProductArtifactType.PACKAGE


@dataclass
class BuildPackagingResult:
    """
    Structured outcome contract returned by BuildPackagingRunner.
    """
    success: bool
    status: BuildPackagingStatus
    artifact: Optional[ProductArtifact] = None
    build_command: str = ""
    exit_code: Optional[int] = None
    duration_ms: float = 0.0
    output_reference: Optional[str] = None
    output_snippet: str = ""
    evidence: list[VerificationEvidence] = field(default_factory=list)
    escalation: Optional[ProgrammerEscalation] = None
    error_message: Optional[str] = None
    trace: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "success": self.success,
            "status": self.status.value,
            "artifact": self.artifact.to_dict() if self.artifact else None,
            "build_command": self.build_command,
            "exit_code": self.exit_code,
            "duration_ms": self.duration_ms,
            "output_reference": self.output_reference,
            "output_snippet": self.output_snippet,
            "evidence": [e.to_dict() for e in self.evidence],
            "escalation": self.escalation.to_dict() if self.escalation else None,
            "error_message": self.error_message,
            "trace": dict(self.trace),
            "metadata": dict(self.metadata),
        }


class BuildPackagingRunner:
    """
    Deterministic build and packaging runner for the AutonomOS Programmer subsystem.

    Architectural Flow:
        ProgrammerWorkOrder + GitExecutionContext
                    ↓
        Source Revision & Worktree Verification
                    ↓
        Dependency Installation Authorization Check
                    ↓
        CommandBoundaryResolver (Policy Enforcement)
                    ↓
        Authorized Subprocess Execution (Confined to Workspace)
                    ↓
        Empirical Verification of Generated Artifact on Disk
                    ↓
        ProductArtifact (Non-deployable by default)

    Core Invariants:
    1. Exact Source Revision Binding: Must bind to the actual GitRevision from GitExecutionContext.
    2. Zero Silent Dependency Installations: Unapproved dependency installations trigger escalation.
    3. Policy Enforcement: Build commands must strictly pass CommandBoundaryResolver.
    4. Empirical Output Verification: Build must exit 0 AND the declared artifact must exist on disk.
    5. Non-Deployability by Default: Output ProductArtifact always defaults to `is_deployable = False`.
    6. Non-Publishing: Never deploys, uploads, or pushes to production.
    """

    def __init__(
        self,
        work_order: ProgrammerWorkOrder,
        git_context: GitExecutionContext,
        execution_context: Optional[ProgrammerExecutionContext] = None,
        command_executor: Optional[Callable[[list[str], str, int], tuple[int, str, str]]] = None,
        default_timeout_seconds: int = 300,
    ):
        if work_order is None:
            raise ProgrammerValidationError("ProgrammerWorkOrder cannot be None.", field_name="work_order")
        if git_context is None:
            raise ProgrammerValidationError("GitExecutionContext cannot be None.", field_name="git_context")

        self.work_order = work_order
        self.git_context = git_context
        self.execution_context = execution_context
        self.command_executor = command_executor
        self.default_timeout_seconds = default_timeout_seconds
        self.command_resolver = CommandBoundaryResolver()

    def _execute_subprocess(
        self,
        command_tokens: list[str],
        cwd: str,
        timeout_seconds: int,
    ) -> tuple[int, str, str]:
        """Default process executor confined to cwd with timeout."""
        if self.command_executor is not None:
            return self.command_executor(command_tokens, cwd, timeout_seconds)

        try:
            res = subprocess.run(
                command_tokens,
                cwd=cwd,
                capture_output=True,
                text=True,
                timeout=timeout_seconds,
            )
            return res.returncode, res.stdout, res.stderr
        except subprocess.TimeoutExpired as exc:
            stdout = exc.stdout.decode() if isinstance(exc.stdout, bytes) else (exc.stdout or "")
            stderr = exc.stderr.decode() if isinstance(exc.stderr, bytes) else (exc.stderr or "")
            return -1, stdout, f"Build execution timed out after {timeout_seconds} seconds: {stderr}"
        except Exception as err:
            return -1, "", f"Failed to execute build process: {err}"

    def execute(
        self,
        request: Optional[BuildPackagingRequest] = None,
        raise_on_error: bool = True,
    ) -> BuildPackagingResult:
        """
        Execute build and packaging workflow.
        """
        req = request or BuildPackagingRequest()

        # ---------------------------------------------------------------------
        # Step 1: Verify Lineage and Exact Source Revision
        # ---------------------------------------------------------------------
        self.git_context.validate()
        self.work_order.validate()

        if self.git_context.project_id and self.git_context.project_id != self.work_order.project_id:
            msg = f"Project mismatch: git context project '{self.git_context.project_id}' does not match work order '{self.work_order.project_id}'."
            if raise_on_error:
                raise ArtifactLineageError(msg)
            return BuildPackagingResult(success=False, status=BuildPackagingStatus.MISMATCH, error_message=msg)

        if self.git_context.work_order_id != self.work_order.work_order_id:
            msg = f"Work order mismatch: git context work_order_id '{self.git_context.work_order_id}' does not match work order '{self.work_order.work_order_id}'."
            if raise_on_error:
                raise ArtifactLineageError(msg)
            return BuildPackagingResult(success=False, status=BuildPackagingStatus.MISMATCH, error_message=msg)

        # Determine effective source revision
        source_rev: GitRevision
        if req.source_revision is not None:
            req.source_revision.validate()
            valid_hashes: set[str] = set()
            if self.git_context.resulting_revision:
                valid_hashes.add(self.git_context.resulting_revision.commit_hash)
            if self.git_context.base_revision:
                valid_hashes.add(self.git_context.base_revision.commit_hash)

            if req.source_revision.commit_hash not in valid_hashes:
                expected = (
                    self.git_context.resulting_revision.commit_hash
                    if self.git_context.resulting_revision
                    else self.git_context.base_revision.commit_hash
                )
                msg = f"Source revision '{req.source_revision.commit_hash}' does not match git execution context revision '{expected}'."
                if raise_on_error:
                    raise SourceRevisionMismatchError(
                        message=msg,
                        commit_hash=req.source_revision.commit_hash,
                        expected_hash=expected,
                    )
                return BuildPackagingResult(success=False, status=BuildPackagingStatus.MISMATCH, error_message=msg)
            source_rev = req.source_revision
        else:
            if self.git_context.resulting_revision:
                source_rev = self.git_context.resulting_revision
            elif self.git_context.base_revision:
                source_rev = self.git_context.base_revision
            else:
                msg = "GitExecutionContext has neither resulting_revision nor base_revision."
                if raise_on_error:
                    raise NonexistentRevisionError(msg)
                return BuildPackagingResult(success=False, status=BuildPackagingStatus.MISMATCH, error_message=msg)

        # ---------------------------------------------------------------------
        # Step 2: Verify Workspace / Worktree Root Directory
        # ---------------------------------------------------------------------
        workspace_root = self.git_context.workspace_path
        if req.target_directory:
            working_dir = os.path.normpath(os.path.join(workspace_root, req.target_directory))
            if not working_dir.startswith(workspace_root):
                msg = f"Target directory '{req.target_directory}' escapes workspace root."
                if raise_on_error:
                    raise ProgrammerValidationError(msg, field_name="target_directory")
                return BuildPackagingResult(success=False, status=BuildPackagingStatus.UNAUTHORIZED, error_message=msg)
        else:
            working_dir = workspace_root

        # ---------------------------------------------------------------------
        # Step 3: Resolve Build Command
        # ---------------------------------------------------------------------
        build_command_str = req.build_command
        if not build_command_str:
            build_command_str = self.work_order.metadata.get("build_command")
        if not build_command_str and self.work_order.context:
            build_command_str = self.work_order.context.get("build_command")
        if not build_command_str:
            # Check required_checks
            for check in self.work_order.required_checks:
                if isinstance(check, str) and (check.startswith("build:") or "build" in check.lower().split()):
                    build_command_str = check.split("build:", 1)[-1].strip() if "build:" in check else check.strip()
                    break

        if not build_command_str or not build_command_str.strip():
            msg = "No build command specified in request or configured in the work order."
            if raise_on_error:
                raise MissingBuildCommandError(msg)
            return BuildPackagingResult(success=False, status=BuildPackagingStatus.MISSING_COMMAND, error_message=msg)

        build_command_str = build_command_str.strip()

        # ---------------------------------------------------------------------
        # Step 4: Check Dependency Installation Authorization & Escalation
        # ---------------------------------------------------------------------
        is_dep_install = req.install_dependencies
        if not is_dep_install:
            cmd_lower = build_command_str.lower()
            for indicator in DEPENDENCY_INSTALL_COMMAND_INDICATORS:
                if indicator in cmd_lower:
                    is_dep_install = True
                    break

        if is_dep_install:
            # Verify explicit authorization in work order
            allowed_meta = bool(
                self.work_order.metadata.get("allow_dependency_installation")
                or self.work_order.context.get("allow_dependency_installation")
            )
            # Or explicit match in allowed_commands
            allowed_in_cmds = any(
                (cmd.command in build_command_str or getattr(cmd, "executable", "") in build_command_str)
                and cmd.metadata.get("is_dependency_installer", False)
                for cmd in self.work_order.allowed_commands
            )

            if not allowed_meta and not allowed_in_cmds:
                # Escalation required
                escalation = ProgrammerEscalation(
                    escalation_id=new_escalation_id(),
                    execution_id=self.git_context.execution_id,
                    work_order_id=self.work_order.work_order_id,
                    category=ProgrammerEscalationCategory.DEPENDENCY,
                    requested_decision=f"Manager authorization required to install dependencies: '{build_command_str}'",
                    observed_facts=[
                        f"Build operation requested dependency installation: '{build_command_str}'.",
                        "Work order does not authorize dependency installation.",
                        "Silent dependency installation is prohibited by engineering policy.",
                    ],
                    attempted_actions=[f"Attempted build command: {build_command_str}"],
                )
                msg = f"Dependency installation requires explicit Manager authorization: '{build_command_str}'."
                if raise_on_error:
                    raise DependencyAuthorizationError(
                        message=msg,
                        dependency_command=build_command_str,
                        escalation_id=escalation.escalation_id,
                    )
                return BuildPackagingResult(
                    success=False,
                    status=BuildPackagingStatus.ESCALATED,
                    build_command=build_command_str,
                    escalation=escalation,
                    error_message=msg,
                )

        # ---------------------------------------------------------------------
        # Step 5: Validate Build Command Against CommandBoundaryResolver Policy
        # ---------------------------------------------------------------------
        decision = self.command_resolver.resolve(
            work_order=self.work_order,
            request=build_command_str,
            working_directory=working_dir,
        )

        if not decision.allowed:
            msg = f"Build command '{build_command_str}' unauthorized: {decision.reason}."
            if raise_on_error:
                raise UnauthorizedBuildCommandError(
                    message=msg,
                    build_command=build_command_str,
                )
            return BuildPackagingResult(
                success=False,
                status=BuildPackagingStatus.UNAUTHORIZED,
                build_command=build_command_str,
                error_message=msg,
            )

        # ---------------------------------------------------------------------
        # Step 6: Execute Authorized Build Command
        # ---------------------------------------------------------------------
        tokens = shlex.split(build_command_str)
        timeout = req.timeout_seconds or self.default_timeout_seconds

        start_time = time.perf_counter()
        exit_code, stdout, stderr = self._execute_subprocess(tokens, working_dir, timeout)
        duration_ms = (time.perf_counter() - start_time) * 1000.0

        output_snippet = (stdout.strip() or stderr.strip())[:1000]

        # ---------------------------------------------------------------------
        # Step 7: Create Authoritative VerificationEvidence
        # ---------------------------------------------------------------------
        evidence = VerificationEvidence(
            evidence_id=new_verification_evidence_id(),
            execution_id=self.git_context.execution_id,
            work_order_id=self.work_order.work_order_id,
            source_type=VerificationEvidenceSourceType.COMMAND_OUTPUT,
            source_reference=build_command_str,
            description=f"Build command execution: {build_command_str}",
            is_agent_claim=False,  # Authoritative empirical observation
            data={
                "build_command": build_command_str,
                "exit_code": exit_code,
                "duration_ms": duration_ms,
                "stdout_snippet": stdout[:1000],
                "stderr_snippet": stderr[:1000],
            },
        )

        # ---------------------------------------------------------------------
        # Step 8: Evaluate Outcome & Check Output Artifact on Disk
        # ---------------------------------------------------------------------
        if exit_code != 0:
            msg = f"Build command '{build_command_str}' failed with exit code {exit_code}: {stderr[:300] or stdout[:300]}"
            return BuildPackagingResult(
                success=False,
                status=BuildPackagingStatus.FAILED,
                build_command=build_command_str,
                exit_code=exit_code,
                duration_ms=duration_ms,
                output_snippet=output_snippet,
                evidence=[evidence],
                error_message=msg,
            )

        # Build succeeded with 0: verify generated artifact reference
        artifact_ref = req.artifact_reference or self.work_order.metadata.get("artifact_reference")
        if not artifact_ref:
            artifact_ref = "dist/package.tar.gz"

        artifact_full_path = (
            artifact_ref
            if os.path.isabs(artifact_ref)
            else os.path.normpath(os.path.join(workspace_root, artifact_ref))
        )

        if not os.path.exists(artifact_full_path):
            msg = f"Build command exited 0, but declared artifact was not found on disk at '{artifact_ref}'."
            if raise_on_error:
                raise MissingArtifactError(msg, artifact_reference=artifact_ref)
            return BuildPackagingResult(
                success=False,
                status=BuildPackagingStatus.MISSING_ARTIFACT,
                build_command=build_command_str,
                exit_code=exit_code,
                duration_ms=duration_ms,
                output_snippet=output_snippet,
                evidence=[evidence],
                error_message=msg,
            )

        # Construct ProductArtifact with is_deployable = False by default
        version = req.version or self.work_order.metadata.get("version", "0.1.0")
        dependencies = list(req.dependencies or self.work_order.dependencies)
        runtime_reqs = dict(req.runtime_requirements or self.work_order.metadata.get("runtime_requirements", {}))
        config_schema = dict(req.configuration_schema or self.work_order.metadata.get("configuration_schema", {}))
        env_reqs = dict(req.environment_requirements or self.work_order.metadata.get("environment_requirements", {}))

        artifact = ProductArtifact(
            artifact_id=new_product_artifact_id(),
            project_id=self.work_order.project_id,
            work_order_id=self.work_order.work_order_id,
            execution_id=self.git_context.execution_id,
            source_revision=source_rev,
            artifact_type=req.artifact_type,
            artifact_reference=artifact_ref,
            version=version,
            build_metadata={
                "build_command": build_command_str,
                "exit_code": exit_code,
                "duration_ms": duration_ms,
                "timestamp": utc_now(),
            },
            runtime_requirements=runtime_reqs,
            dependencies=dependencies,
            configuration_schema=config_schema,
            environment_requirements=env_reqs,
            evidence=[evidence],
            trace={
                "builder": "BuildPackagingRunner",
                "build_command": build_command_str,
                "source_revision": source_rev.commit_hash,
                "working_directory": working_dir,
            },
            is_deployable=False,  # INVARIANT: Successful build does NOT mean tested/secure/deployable
        )

        return BuildPackagingResult(
            success=True,
            status=BuildPackagingStatus.SUCCESS,
            artifact=artifact,
            build_command=build_command_str,
            exit_code=exit_code,
            duration_ms=duration_ms,
            output_snippet=output_snippet,
            evidence=[evidence],
            trace={
                "source_revision": source_rev.commit_hash,
                "artifact_id": artifact.artifact_id,
            },
        )
