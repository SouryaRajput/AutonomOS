from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import json
import os
from typing import Any, Optional

from core.programmer.contracts.git_model import (
    GitExecutionContext,
    GitRevision,
)
from core.programmer.contracts.git_policy import (
    GitDecision,
    GitOperationPolicy,
    GitOperationRequest,
    GitOperationType,
    GitPolicyEvaluator,
)
from core.programmer.contracts.git_worktree import (
    FakeGitOperations,
    GitOperationsProtocol,
)
from core.programmer.contracts.identifiers import (
    CHANGESET_ID_PREFIX,
    new_change_set_id,
    validate_change_set_id,
    validate_execution_id,
    validate_work_order_id,
)
from core.programmer.contracts.work_order import ProgrammerWorkOrder
from core.programmer.errors import (
    GitWorktreeError,
    ProgrammerValidationError,
)
from core.programmer.types import (
    ChangeSetStatus,
)


def utc_now() -> str:
    """Generate ISO 8601 UTC timestamp."""
    return datetime.now(timezone.utc).isoformat()


@dataclass
class ChangeSet:
    """
    Deterministic domain model representing a local Git change set for a Programmer execution.

    Invariants:
    1. Physical Git state is the sole authority; Cline self-reported claims are ignored.
    2. A commit is NOT equivalent to acceptance or approval.
    3. Preserves authoritative base_revision and resulting_revision.
    4. Tracks files changed, created, deleted, diff summary, and commit lineage references.
    """
    change_set_id: str
    execution_id: str
    work_order_id: str
    base_revision: GitRevision
    resulting_revision: Optional[GitRevision] = None
    files_changed: list[str] = field(default_factory=list)
    files_created: list[str] = field(default_factory=list)
    files_deleted: list[str] = field(default_factory=list)
    diff_summary: dict[str, Any] = field(default_factory=dict)
    commit_references: list[GitRevision] = field(default_factory=list)
    status: ChangeSetStatus = ChangeSetStatus.UNCOMMITTED
    evidence: list[dict[str, Any]] = field(default_factory=list)
    trace: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=utc_now)

    def __post_init__(self) -> None:
        if isinstance(self.status, str):
            try:
                self.status = ChangeSetStatus(self.status.upper())
            except (ValueError, TypeError):
                self.status = ChangeSetStatus.INVALID

        if isinstance(self.base_revision, dict):
            self.base_revision = GitRevision.from_dict(self.base_revision)
        if isinstance(self.resulting_revision, dict):
            self.resulting_revision = GitRevision.from_dict(self.resulting_revision)

        normalized_commits: list[GitRevision] = []
        for c in self.commit_references:
            if isinstance(c, dict):
                normalized_commits.append(GitRevision.from_dict(c))
            else:
                normalized_commits.append(c)
        self.commit_references = normalized_commits

        self.validate()

    def validate(self) -> None:
        validate_change_set_id(self.change_set_id)
        validate_execution_id(self.execution_id)
        validate_work_order_id(self.work_order_id)

        if self.base_revision is None or not isinstance(self.base_revision, GitRevision):
            raise ProgrammerValidationError("ChangeSet requires a valid base_revision GitRevision.", field_name="base_revision")
        self.base_revision.validate()

        if self.resulting_revision is not None:
            if not isinstance(self.resulting_revision, GitRevision):
                raise ProgrammerValidationError("resulting_revision must be a GitRevision instance.", field_name="resulting_revision")
            self.resulting_revision.validate()

    def is_empty(self) -> bool:
        """Check if change set contains no modifications and no new commits beyond base."""
        return (
            len(self.files_changed) == 0
            and len(self.files_created) == 0
            and len(self.files_deleted) == 0
            and len(self.commit_references) == 0
        )

    def total_files_affected(self) -> int:
        """Total distinct files modified, created, or deleted."""
        return len(self.files_changed) + len(self.files_created) + len(self.files_deleted)

    def to_dict(self) -> dict[str, Any]:
        return {
            "change_set_id": self.change_set_id,
            "execution_id": self.execution_id,
            "work_order_id": self.work_order_id,
            "base_revision": self.base_revision.to_dict(),
            "resulting_revision": self.resulting_revision.to_dict() if self.resulting_revision else None,
            "files_changed": list(self.files_changed),
            "files_created": list(self.files_created),
            "files_deleted": list(self.files_deleted),
            "diff_summary": dict(self.diff_summary),
            "commit_references": [c.to_dict() for c in self.commit_references],
            "status": self.status.value,
            "evidence": [dict(e) for e in self.evidence],
            "trace": dict(self.trace),
            "metadata": dict(self.metadata),
            "created_at": self.created_at,
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=2)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ChangeSet:
        st_raw = data.get("status", ChangeSetStatus.UNCOMMITTED.value)
        try:
            st = ChangeSetStatus(str(st_raw).upper())
        except (ValueError, TypeError):
            st = ChangeSetStatus.INVALID

        base_rev = GitRevision.from_dict(data["base_revision"])
        res_rev = GitRevision.from_dict(data["resulting_revision"]) if data.get("resulting_revision") else None
        commits = [GitRevision.from_dict(c) for c in data.get("commit_references", [])]

        return cls(
            change_set_id=data["change_set_id"],
            execution_id=data["execution_id"],
            work_order_id=data["work_order_id"],
            base_revision=base_rev,
            resulting_revision=res_rev,
            files_changed=list(data.get("files_changed", [])),
            files_created=list(data.get("files_created", [])),
            files_deleted=list(data.get("files_deleted", [])),
            diff_summary=dict(data.get("diff_summary", {})),
            commit_references=commits,
            status=st,
            evidence=[dict(e) for e in data.get("evidence", [])],
            trace=dict(data.get("trace", {})),
            metadata=dict(data.get("metadata", {})),
            created_at=data.get("created_at", utc_now()),
        )


class GitChangeSetManager:
    """
    Deterministic manager for capturing working-tree changes and producing local isolated commits.

    Core Invariants:
    1. Physical Git state is the sole authority; Cline self-reported claims are ignored.
    2. Commits are created ONLY within the isolated execution workspace on the execution branch.
    3. Commits to protected/default branches (main, master, etc.) are strictly forbidden.
    4. Commits are permitted ONLY when authorized by explicit policy.
    5. A commit is NOT equivalent to approval; verification and acceptance remain separate.
    6. Never executes push, merge, deploy, or touches unrelated repositories.
    """

    def __init__(
        self,
        git_ops: Optional[GitOperationsProtocol] = None,
        policy_evaluator: Optional[GitPolicyEvaluator] = None,
    ) -> None:
        self.git_ops = git_ops or FakeGitOperations()
        self.policy_evaluator = policy_evaluator or GitPolicyEvaluator()

    def capture_change_set(
        self,
        execution_context: GitExecutionContext,
        work_order: Optional[ProgrammerWorkOrder] = None,
        trace: Optional[dict[str, Any]] = None,
    ) -> ChangeSet:
        """
        Inspect physical repository working-tree state and recent commits to construct a ChangeSet.
        """
        tr = dict(trace or {})
        cs_id = new_change_set_id()

        try:
            status_data = self.git_ops.inspect_status(execution_context.workspace_path)
        except Exception as e:
            # Broken repository state or unreadable working tree
            return ChangeSet(
                change_set_id=cs_id,
                execution_id=execution_context.execution_id,
                work_order_id=execution_context.work_order_id,
                base_revision=execution_context.base_revision,
                resulting_revision=None,
                status=ChangeSetStatus.INVALID,
                metadata={"error": str(e), "inspection_failed": True},
                trace=tr,
            )

        # Retrieve commit history created during execution
        commits = self.git_ops.get_commit_history(
            execution_context.workspace_path,
            base_commit=execution_context.base_revision.commit_hash,
        )

        files_changed = list(status_data.get("files_changed", []))
        files_created = list(status_data.get("files_created", []))
        files_deleted = list(status_data.get("files_deleted", []))
        diff_summary = dict(status_data.get("diff_summary", {}))

        resulting_rev = commits[-1] if commits else execution_context.resulting_revision

        has_uncommitted = bool(files_changed or files_created or files_deleted)
        has_commits = bool(commits)

        if not has_uncommitted and not has_commits:
            st = ChangeSetStatus.EMPTY
        elif has_uncommitted:
            st = ChangeSetStatus.UNCOMMITTED
        else:
            st = ChangeSetStatus.COMMITTED

        return ChangeSet(
            change_set_id=cs_id,
            execution_id=execution_context.execution_id,
            work_order_id=execution_context.work_order_id,
            base_revision=execution_context.base_revision,
            resulting_revision=resulting_rev,
            files_changed=files_changed,
            files_created=files_created,
            files_deleted=files_deleted,
            diff_summary=diff_summary,
            commit_references=commits,
            status=st,
            trace=tr,
        )

    def create_local_commit(
        self,
        execution_context: GitExecutionContext,
        work_order: ProgrammerWorkOrder,
        message: str,
        policy: Optional[GitOperationPolicy] = None,
        author: Optional[str] = None,
        caller_role: str = "programmer",
        trace: Optional[dict[str, Any]] = None,
    ) -> ChangeSet:
        """
        Safely create a local commit inside the isolated execution workspace if authorized by policy.
        """
        tr = dict(trace or {})
        cs_id = new_change_set_id()

        # Step 1: Policy evaluation for commit operation
        req = GitOperationRequest(
            operation=GitOperationType.COMMIT,
            arguments=["-m", message],
            target_branch=execution_context.branch,
            repository_id=execution_context.repository_id,
            execution_id=execution_context.execution_id,
            work_order_id=execution_context.work_order_id,
            project_id=execution_context.project_id,
            caller_role=caller_role,
            trace=tr,
        )
        decision: GitDecision = self.policy_evaluator.evaluate(
            request=req,
            execution_context=execution_context,
            work_order=work_order,
            policy=policy,
        )

        if not decision.allowed:
            return ChangeSet(
                change_set_id=cs_id,
                execution_id=execution_context.execution_id,
                work_order_id=execution_context.work_order_id,
                base_revision=execution_context.base_revision,
                resulting_revision=execution_context.resulting_revision,
                status=ChangeSetStatus.FAILED,
                metadata={
                    "error": f"Commit unauthorized by policy: {decision.reason}",
                    "policy_decision": decision.to_dict(),
                },
                trace=tr,
            )

        # Step 2: Inspect current physical working tree state
        try:
            status_data = self.git_ops.inspect_status(execution_context.workspace_path)
        except Exception as e:
            return ChangeSet(
                change_set_id=cs_id,
                execution_id=execution_context.execution_id,
                work_order_id=execution_context.work_order_id,
                base_revision=execution_context.base_revision,
                status=ChangeSetStatus.FAILED,
                metadata={"error": f"Pre-commit status inspection failed: {e}"},
                trace=tr,
            )

        files_changed = list(status_data.get("files_changed", []))
        files_created = list(status_data.get("files_created", []))
        files_deleted = list(status_data.get("files_deleted", []))
        diff_summary = dict(status_data.get("diff_summary", {}))

        # Check for empty changes
        if not files_changed and not files_created and not files_deleted:
            commits = self.git_ops.get_commit_history(
                execution_context.workspace_path,
                base_commit=execution_context.base_revision.commit_hash,
            )
            return ChangeSet(
                change_set_id=cs_id,
                execution_id=execution_context.execution_id,
                work_order_id=execution_context.work_order_id,
                base_revision=execution_context.base_revision,
                resulting_revision=commits[-1] if commits else execution_context.resulting_revision,
                files_changed=[],
                files_created=[],
                files_deleted=[],
                diff_summary={"total_files": 0},
                commit_references=commits,
                status=ChangeSetStatus.EMPTY,
                metadata={"info": "Working tree is clean; no changes to commit."},
                trace=tr,
            )

        # Step 3: Format deterministic commit metadata and trailer provenance
        commit_author = author or "AutonomOS Programmer <programmer@autonomos.ai>"
        formatted_message = (
            f"{message.strip()}\n\n"
            f"Execution-ID: {execution_context.execution_id}\n"
            f"Work-Order-ID: {work_order.work_order_id}\n"
            f"Base-Revision: {execution_context.base_revision.commit_hash}"
        )
        commit_meta = {
            "execution_id": execution_context.execution_id,
            "work_order_id": work_order.work_order_id,
            "project_id": execution_context.project_id,
            "branch": execution_context.branch,
            "committed_at": utc_now(),
        }

        # Step 4: Perform commit via git_ops
        try:
            new_rev = self.git_ops.commit(
                repo_or_worktree_path=execution_context.workspace_path,
                message=formatted_message,
                author=commit_author,
                metadata=commit_meta,
            )
        except Exception as e:
            return ChangeSet(
                change_set_id=cs_id,
                execution_id=execution_context.execution_id,
                work_order_id=execution_context.work_order_id,
                base_revision=execution_context.base_revision,
                status=ChangeSetStatus.FAILED,
                metadata={"error": f"Underlying commit failed: {e}"},
                trace=tr,
            )

        # Step 5: Update execution_context with resulting_revision
        execution_context.resulting_revision = new_rev

        all_commits = self.git_ops.get_commit_history(
            execution_context.workspace_path,
            base_commit=execution_context.base_revision.commit_hash,
        )

        return ChangeSet(
            change_set_id=cs_id,
            execution_id=execution_context.execution_id,
            work_order_id=execution_context.work_order_id,
            base_revision=execution_context.base_revision,
            resulting_revision=new_rev,
            files_changed=files_changed,
            files_created=files_created,
            files_deleted=files_deleted,
            diff_summary=diff_summary,
            commit_references=all_commits,
            status=ChangeSetStatus.COMMITTED,
            evidence=[
                {
                    "type": "LOCAL_COMMIT",
                    "commit_hash": new_rev.commit_hash,
                    "branch": execution_context.branch,
                    "timestamp": new_rev.timestamp,
                }
            ],
            trace=tr,
        )
