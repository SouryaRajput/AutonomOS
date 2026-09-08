from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shlex
from typing import Any, Optional

from core.programmer.contracts.git_model import GitExecutionContext
from core.programmer.contracts.work_order import ProgrammerWorkOrder
from core.programmer.types import (
    GitOperationCategory,
    GitOperationType,
)


def utc_now() -> str:
    """Generate ISO 8601 UTC timestamp."""
    return datetime.now(timezone.utc).isoformat()


# Map concrete Git operations to their governance category
OPERATION_TO_CATEGORY: dict[GitOperationType, GitOperationCategory] = {
    GitOperationType.STATUS: GitOperationCategory.READ,
    GitOperationType.DIFF: GitOperationCategory.READ,
    GitOperationType.LOG: GitOperationCategory.READ,
    GitOperationType.SHOW: GitOperationCategory.READ,
    GitOperationType.BRANCH_INFO: GitOperationCategory.READ,

    GitOperationType.ADD: GitOperationCategory.LOCAL_CHANGE_MANAGEMENT,
    GitOperationType.COMMIT: GitOperationCategory.LOCAL_CHANGE_MANAGEMENT,
    GitOperationType.BRANCH_CREATE: GitOperationCategory.LOCAL_CHANGE_MANAGEMENT,

    GitOperationType.RESET: GitOperationCategory.DESTRUCTIVE,
    GitOperationType.CLEAN: GitOperationCategory.DESTRUCTIVE,
    GitOperationType.CHECKOUT_DISCARD: GitOperationCategory.DESTRUCTIVE,
    GitOperationType.BRANCH_DELETE: GitOperationCategory.DESTRUCTIVE,

    GitOperationType.FETCH: GitOperationCategory.REMOTE,
    GitOperationType.PUSH: GitOperationCategory.REMOTE,
    GitOperationType.PULL: GitOperationCategory.REMOTE,
}


@dataclass
class GitOperationRequest:
    """
    Deterministic domain model representing a requested Git operation.
    """
    operation: GitOperationType | str
    arguments: list[str] = field(default_factory=list)
    target_branch: Optional[str] = None
    target_path: Optional[str] = None
    remote: Optional[str] = None
    is_force: bool = False
    repository_id: str = ""
    execution_id: str = ""
    work_order_id: str = ""
    project_id: str = ""
    caller_role: str = "cline"
    trace: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if isinstance(self.operation, str):
            try:
                self.operation = GitOperationType(self.operation.lower())
            except (ValueError, TypeError):
                pass  # May remain string if unparseable/unknown

    @classmethod
    def from_command_string(
        cls,
        command_str: str,
        execution_id: str = "",
        work_order_id: str = "",
        repository_id: str = "",
        project_id: str = "",
        caller_role: str = "cline",
        trace: Optional[dict[str, Any]] = None,
    ) -> GitOperationRequest:
        """
        Parse a shell command string (e.g. 'git push origin main' or 'git commit -m "msg"')
        into a structured GitOperationRequest.
        """
        if not isinstance(command_str, str) or not command_str.strip():
            return cls(
                operation="malformed",
                execution_id=execution_id,
                work_order_id=work_order_id,
                repository_id=repository_id,
                project_id=project_id,
                caller_role=caller_role,
                trace=dict(trace or {}),
                metadata={"raw_command": command_str},
            )

        try:
            tokens = shlex.split(command_str.strip())
        except Exception:
            return cls(
                operation="malformed",
                execution_id=execution_id,
                work_order_id=work_order_id,
                repository_id=repository_id,
                project_id=project_id,
                caller_role=caller_role,
                trace=dict(trace or {}),
                metadata={"raw_command": command_str, "parse_error": "shlex_error"},
            )

        if not tokens:
            return cls(
                operation="malformed",
                execution_id=execution_id,
                work_order_id=work_order_id,
                repository_id=repository_id,
                project_id=project_id,
                caller_role=caller_role,
                trace=dict(trace or {}),
            )

        if tokens[0] == "git":
            tokens = tokens[1:]
        if not tokens:
            return cls(
                operation="malformed",
                execution_id=execution_id,
                work_order_id=work_order_id,
                repository_id=repository_id,
                project_id=project_id,
                caller_role=caller_role,
                trace=dict(trace or {}),
            )

        subcmd = tokens[0].lower()
        args = tokens[1:]

        is_force = False
        target_branch = None
        target_path = None
        remote = None
        op_type: GitOperationType | str = subcmd

        # Inspect flags and command shapes
        if subcmd == "status":
            op_type = GitOperationType.STATUS
        elif subcmd == "diff":
            op_type = GitOperationType.DIFF
        elif subcmd == "log":
            op_type = GitOperationType.LOG
        elif subcmd == "show":
            op_type = GitOperationType.SHOW
        elif subcmd == "branch":
            if any(a in ("-d", "-D", "--delete") for a in args):
                op_type = GitOperationType.BRANCH_DELETE
                for a in args:
                    if not a.startswith("-"):
                        target_branch = a
            elif any(a in ("-c", "-C", "-m", "-M") for a in args) or (len(args) >= 1 and not args[0].startswith("-")):
                op_type = GitOperationType.BRANCH_CREATE
                for a in args:
                    if not a.startswith("-"):
                        target_branch = a
            else:
                op_type = GitOperationType.BRANCH_INFO
        elif subcmd == "add":
            op_type = GitOperationType.ADD
            for a in args:
                if not a.startswith("-"):
                    target_path = a
                    break
        elif subcmd == "commit":
            op_type = GitOperationType.COMMIT
        elif subcmd == "reset":
            op_type = GitOperationType.RESET
        elif subcmd == "clean":
            op_type = GitOperationType.CLEAN
        elif subcmd == "checkout":
            if any(a == "--" for a in args) or any(a in ("-f", "--force") for a in args):
                op_type = GitOperationType.CHECKOUT_DISCARD
            elif any(a in ("-b", "-B") for a in args):
                op_type = GitOperationType.BRANCH_CREATE
                for i, a in enumerate(args):
                    if a in ("-b", "-B") and i + 1 < len(args):
                        target_branch = args[i + 1]
            else:
                # Checkout branch / commit or discard
                non_flags = [a for a in args if not a.startswith("-")]
                if non_flags:
                    target_branch = non_flags[0]
                op_type = GitOperationType.CHECKOUT_DISCARD if "." in args else GitOperationType.STATUS
        elif subcmd == "fetch":
            op_type = GitOperationType.FETCH
            if args:
                remote = args[0]
        elif subcmd == "push":
            op_type = GitOperationType.PUSH
            if any(a in ("--force", "-f", "--force-with-lease") for a in args) or any(a.startswith("+") for a in args):
                is_force = True
            non_flags = [a for a in args if not a.startswith("-")]
            if len(non_flags) >= 1:
                remote = non_flags[0]
            if len(non_flags) >= 2:
                target_branch = non_flags[1].lstrip("+")
        elif subcmd == "pull":
            op_type = GitOperationType.PULL
            if args:
                remote = args[0]

        return cls(
            operation=op_type,
            arguments=args,
            target_branch=target_branch,
            target_path=target_path,
            remote=remote,
            is_force=is_force,
            repository_id=repository_id,
            execution_id=execution_id,
            work_order_id=work_order_id,
            project_id=project_id,
            caller_role=caller_role,
            trace=dict(trace or {}),
            metadata={"raw_command": command_str},
        )


@dataclass
class GitDecision:
    """
    Deterministic authorization decision governing a requested Git operation.
    """
    allowed: bool
    operation: str
    reason: str
    matched_policy: str
    execution_id: str
    work_order_id: str
    trace: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=utc_now)

    def to_dict(self) -> dict[str, Any]:
        return {
            "allowed": self.allowed,
            "operation": self.operation,
            "reason": self.reason,
            "matched_policy": self.matched_policy,
            "execution_id": self.execution_id,
            "work_order_id": self.work_order_id,
            "trace": dict(self.trace),
            "metadata": dict(self.metadata),
            "created_at": self.created_at,
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=2)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> GitDecision:
        return cls(
            allowed=bool(data.get("allowed", False)),
            operation=str(data.get("operation", "")),
            reason=str(data.get("reason", "")),
            matched_policy=str(data.get("matched_policy", "")),
            execution_id=str(data.get("execution_id", "")),
            work_order_id=str(data.get("work_order_id", "")),
            trace=dict(data.get("trace", {})),
            metadata=dict(data.get("metadata", {})),
            created_at=str(data.get("created_at", utc_now())),
        )


@dataclass
class GitOperationPolicy:
    """
    Conservative policy specification governing permitted Git operations.
    """
    allow_read: bool = True
    allow_add: bool = False
    allow_commit: bool = False
    allow_branch_creation: bool = False
    allow_remote: bool = False
    allow_destructive: bool = False
    protected_branches: list[str] = field(
        default_factory=lambda: ["main", "master", "develop", "release", "prod", "production"]
    )
    allowed_remotes: list[str] = field(default_factory=list)

    @classmethod
    def conservative_default(cls) -> GitOperationPolicy:
        """Standard conservative default: read allowed, all modifications/remotes denied."""
        return cls(
            allow_read=True,
            allow_add=False,
            allow_commit=False,
            allow_branch_creation=False,
            allow_remote=False,
            allow_destructive=False,
        )

    @classmethod
    def allow_local_changes(cls) -> GitOperationPolicy:
        """Permissive local policy: allows add, commit, and branch creation within isolated worktrees."""
        return cls(
            allow_read=True,
            allow_add=True,
            allow_commit=True,
            allow_branch_creation=True,
            allow_remote=False,
            allow_destructive=False,
        )


class GitPolicyEvaluator:
    """
    Deterministic policy evaluator for Programmer and Cline Git operations.

    Core Invariants:
    1. Git policy must never override filesystem boundary, WorkOrder constraints, or Manager authority.
    2. Force push and remote deletion are permanently forbidden.
    3. Direct mutation of protected branches (main, master, etc.) is strictly forbidden.
    4. Remote operations are denied by default for Cline.
    5. Destructive operations (reset, clean, checkout discard) are denied by default.
    6. Cross-project, cross-execution, and foreign repository access are denied deterministically.
    """

    def __init__(self, default_policy: Optional[GitOperationPolicy] = None) -> None:
        self.default_policy = default_policy or GitOperationPolicy.conservative_default()

    def evaluate(
        self,
        request: GitOperationRequest,
        execution_context: Optional[GitExecutionContext] = None,
        work_order: Optional[ProgrammerWorkOrder] = None,
        policy: Optional[GitOperationPolicy] = None,
    ) -> GitDecision:
        """
        Evaluate a GitOperationRequest against the active policy, execution context, and work order.
        Returns a structured GitDecision.
        """
        pol = policy or self.default_policy
        tr = dict(request.trace)
        exec_id = request.execution_id or (execution_context.execution_id if execution_context else "")
        wo_id = request.work_order_id or (execution_context.work_order_id if execution_context else "")

        # ---------------------------------------------------------------------
        # Stage 1: Malformed Request Guard
        # ---------------------------------------------------------------------
        if (
            not request.operation
            or request.operation in ("malformed", GitOperationType.INVALID)
            or getattr(request.operation, "value", None) == "invalid"
        ):
            return GitDecision(
                allowed=False,
                operation=str(getattr(request.operation, "value", request.operation)),
                reason="Malformed Git request: missing, empty, or unparseable command.",
                matched_policy="MALFORMED_REQUEST",
                execution_id=exec_id,
                work_order_id=wo_id,
                trace=tr,
            )

        if not isinstance(request.operation, GitOperationType):
            try:
                op = GitOperationType(str(request.operation).lower())
            except (ValueError, TypeError):
                return GitDecision(
                    allowed=False,
                    operation=str(request.operation),
                    reason=f"Unrecognized or unsupported Git operation '{request.operation}'.",
                    matched_policy="UNSUPPORTED_GIT_OPERATION",
                    execution_id=exec_id,
                    work_order_id=wo_id,
                    trace=tr,
                )
        else:
            op = request.operation

        op_str = op.value

        # ---------------------------------------------------------------------
        # Stage 2: Lineage, Repository & Execution Boundary Checks
        # ---------------------------------------------------------------------
        if execution_context is not None:
            # Cross-execution access guard
            if request.execution_id and request.execution_id != execution_context.execution_id:
                return GitDecision(
                    allowed=False,
                    operation=op_str,
                    reason=(
                        f"Cross-execution access denied: request execution '{request.execution_id}' "
                        f"does not match execution context '{execution_context.execution_id}'."
                    ),
                    matched_policy="CROSS_EXECUTION_DENIED",
                    execution_id=exec_id,
                    work_order_id=wo_id,
                    trace=tr,
                )

            # Wrong repository guard
            if request.repository_id and request.repository_id != execution_context.repository_id:
                return GitDecision(
                    allowed=False,
                    operation=op_str,
                    reason=(
                        f"Execution from wrong repository: request repository '{request.repository_id}' "
                        f"does not match execution context repository '{execution_context.repository_id}'."
                    ),
                    matched_policy="WRONG_REPOSITORY_DENIED",
                    execution_id=exec_id,
                    work_order_id=wo_id,
                    trace=tr,
                )

            # Cross-project access guard
            if request.project_id and request.project_id != execution_context.project_id:
                return GitDecision(
                    allowed=False,
                    operation=op_str,
                    reason=(
                        f"Cross-project access denied: request project '{request.project_id}' "
                        f"does not match execution context project '{execution_context.project_id}'."
                    ),
                    matched_policy="CROSS_PROJECT_DENIED",
                    execution_id=exec_id,
                    work_order_id=wo_id,
                    trace=tr,
                )

            # WorkOrder lineage guard
            if request.work_order_id and request.work_order_id != execution_context.work_order_id:
                return GitDecision(
                    allowed=False,
                    operation=op_str,
                    reason=(
                        f"Work order lineage mismatch: request work order '{request.work_order_id}' "
                        f"does not match context work order '{execution_context.work_order_id}'."
                    ),
                    matched_policy="LINEAGE_MISMATCH_DENIED",
                    execution_id=exec_id,
                    work_order_id=wo_id,
                    trace=tr,
                )

        # ---------------------------------------------------------------------
        # Stage 3: Filesystem Boundary Invariant
        # Git policy must never override filesystem boundary
        # ---------------------------------------------------------------------
        if request.target_path:
            norm_target = os.path.normpath(request.target_path)
            
            if execution_context is not None and execution_context.workspace_path:
                ws_root = os.path.abspath(execution_context.workspace_path)
                abs_target = os.path.abspath(
                    norm_target if os.path.isabs(norm_target) else os.path.join(ws_root, norm_target)
                )
                try:
                    common = os.path.commonpath([ws_root, abs_target])
                    if common != ws_root:
                        return GitDecision(
                            allowed=False,
                            operation=op_str,
                            reason=f"Target path '{request.target_path}' escapes execution workspace '{ws_root}'.",
                            matched_policy="FILESYSTEM_BOUNDARY_VIOLATION",
                            execution_id=exec_id,
                            work_order_id=wo_id,
                            trace=tr,
                        )
                except ValueError:
                    return GitDecision(
                        allowed=False,
                        operation=op_str,
                        reason=f"Target path '{request.target_path}' is on a different drive or invalid.",
                        matched_policy="FILESYSTEM_BOUNDARY_VIOLATION",
                        execution_id=exec_id,
                        work_order_id=wo_id,
                        trace=tr,
                    )

            # WorkOrder boundary constraints
            if work_order is not None:
                # Check forbidden paths
                for f_path in work_order.forbidden_paths:
                    if norm_target == f_path or norm_target.startswith(f_path.rstrip("/") + "/"):
                        return GitDecision(
                            allowed=False,
                            operation=op_str,
                            reason=f"Target path '{request.target_path}' violates WorkOrder forbidden path '{f_path}'.",
                            matched_policy="WORKORDER_BOUNDARY_VIOLATION",
                            execution_id=exec_id,
                            work_order_id=wo_id,
                            trace=tr,
                        )

        # ---------------------------------------------------------------------
        # Stage 4: Permanent Hard Safety Denials
        # ---------------------------------------------------------------------

        # 1. Force push denial
        if op == GitOperationType.PUSH:
            if request.is_force or any(a in ("--force", "-f", "--force-with-lease") for a in request.arguments):
                return GitDecision(
                    allowed=False,
                    operation=op_str,
                    reason="Force push is strictly forbidden by policy.",
                    matched_policy="FORCE_PUSH_FORBIDDEN",
                    execution_id=exec_id,
                    work_order_id=wo_id,
                    trace=tr,
                )
            if any(a.startswith("+") for a in request.arguments):
                return GitDecision(
                    allowed=False,
                    operation=op_str,
                    reason="Force push via refspec '+' is strictly forbidden by policy.",
                    matched_policy="FORCE_PUSH_FORBIDDEN",
                    execution_id=exec_id,
                    work_order_id=wo_id,
                    trace=tr,
                )

        # 2. Remote deletion denial
        if op == GitOperationType.PUSH:
            if any(a in ("--delete", "-d") for a in request.arguments) or any(a.startswith(":") for a in request.arguments):
                return GitDecision(
                    allowed=False,
                    operation=op_str,
                    reason="Remote branch deletion via push is strictly forbidden by policy.",
                    matched_policy="REMOTE_DELETION_FORBIDDEN",
                    execution_id=exec_id,
                    work_order_id=wo_id,
                    trace=tr,
                )

        # 3. Protected branch mutation denial
        target_br = request.target_branch or (execution_context.branch if execution_context else None)
        if target_br and target_br in pol.protected_branches:
            if op in (
                GitOperationType.ADD,
                GitOperationType.COMMIT,
                GitOperationType.BRANCH_DELETE,
                GitOperationType.RESET,
                GitOperationType.CLEAN,
                GitOperationType.CHECKOUT_DISCARD,
                GitOperationType.PUSH,
            ):
                return GitDecision(
                    allowed=False,
                    operation=op_str,
                    reason=f"Direct mutation of protected branch '{target_br}' is strictly forbidden by policy.",
                    matched_policy="PROTECTED_BRANCH_MUTATION_DENIED",
                    execution_id=exec_id,
                    work_order_id=wo_id,
                    trace=tr,
                )

        # ---------------------------------------------------------------------
        # Stage 5: Category & Policy Specific Evaluation
        # ---------------------------------------------------------------------
        category = OPERATION_TO_CATEGORY.get(op, GitOperationCategory.READ)

        # Remote operations
        if category == GitOperationCategory.REMOTE:
            if request.caller_role.lower() == "cline":
                return GitDecision(
                    allowed=False,
                    operation=op_str,
                    reason=f"Remote Git operation '{op_str}' is not authorized for Cline.",
                    matched_policy="UNAUTHORIZED_REMOTE_OPERATION",
                    execution_id=exec_id,
                    work_order_id=wo_id,
                    trace=tr,
                )
            if not pol.allow_remote:
                return GitDecision(
                    allowed=False,
                    operation=op_str,
                    reason=f"Remote Git operation '{op_str}' is denied by default policy.",
                    matched_policy="REMOTE_OPERATION_DENIED",
                    execution_id=exec_id,
                    work_order_id=wo_id,
                    trace=tr,
                )
            return GitDecision(
                allowed=True,
                operation=op_str,
                reason=f"Remote Git operation '{op_str}' authorized by explicit policy.",
                matched_policy="REMOTE_OPERATION_PERMITTED",
                execution_id=exec_id,
                work_order_id=wo_id,
                trace=tr,
            )

        # Destructive operations
        if category == GitOperationCategory.DESTRUCTIVE:
            if not pol.allow_destructive:
                return GitDecision(
                    allowed=False,
                    operation=op_str,
                    reason=f"Destructive Git operation '{op_str}' is denied by default policy.",
                    matched_policy="DESTRUCTIVE_OPERATION_DENIED",
                    execution_id=exec_id,
                    work_order_id=wo_id,
                    trace=tr,
                )
            return GitDecision(
                allowed=True,
                operation=op_str,
                reason=f"Destructive Git operation '{op_str}' authorized by explicit policy.",
                matched_policy="DESTRUCTIVE_OPERATION_PERMITTED",
                execution_id=exec_id,
                work_order_id=wo_id,
                trace=tr,
            )

        # Local change management operations
        if category == GitOperationCategory.LOCAL_CHANGE_MANAGEMENT:
            if op == GitOperationType.ADD and not pol.allow_add:
                return GitDecision(
                    allowed=False,
                    operation=op_str,
                    reason="Local git add requires explicit Programmer policy authorization.",
                    matched_policy="LOCAL_CHANGE_POLICY_REQUIRED",
                    execution_id=exec_id,
                    work_order_id=wo_id,
                    trace=tr,
                )
            if op == GitOperationType.COMMIT and not pol.allow_commit:
                return GitDecision(
                    allowed=False,
                    operation=op_str,
                    reason="Local git commit requires explicit Programmer policy authorization.",
                    matched_policy="LOCAL_CHANGE_POLICY_REQUIRED",
                    execution_id=exec_id,
                    work_order_id=wo_id,
                    trace=tr,
                )
            if op == GitOperationType.BRANCH_CREATE and not pol.allow_branch_creation:
                return GitDecision(
                    allowed=False,
                    operation=op_str,
                    reason="Local branch creation requires explicit Programmer policy authorization.",
                    matched_policy="LOCAL_CHANGE_POLICY_REQUIRED",
                    execution_id=exec_id,
                    work_order_id=wo_id,
                    trace=tr,
                )
            return GitDecision(
                allowed=True,
                operation=op_str,
                reason=f"Local change management operation '{op_str}' authorized by Programmer policy.",
                matched_policy="LOCAL_CHANGE_POLICY_PERMITTED",
                execution_id=exec_id,
                work_order_id=wo_id,
                trace=tr,
            )

        # Read operations
        if category == GitOperationCategory.READ:
            if pol.allow_read:
                return GitDecision(
                    allowed=True,
                    operation=op_str,
                    reason=f"Read operation '{op_str}' allowed by default policy.",
                    matched_policy="DEFAULT_READ_ALLOWED",
                    execution_id=exec_id,
                    work_order_id=wo_id,
                    trace=tr,
                )
            return GitDecision(
                allowed=False,
                operation=op_str,
                reason=f"Read operation '{op_str}' disabled by policy.",
                matched_policy="READ_OPERATION_DENIED",
                execution_id=exec_id,
                work_order_id=wo_id,
                trace=tr,
            )

        return GitDecision(
            allowed=False,
            operation=op_str,
            reason=f"Unhandled Git operation '{op_str}'.",
            matched_policy="UNHANDLED_OPERATION_DENIED",
            execution_id=exec_id,
            work_order_id=wo_id,
            trace=tr,
        )
