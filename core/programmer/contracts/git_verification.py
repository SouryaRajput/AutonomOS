from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import json
import os
import re
from typing import Any, Optional

from core.programmer.contracts.diff_verifier import (
    DiffScopeVerifier,
    DiffVerification,
    ObservedDiff,
    RenamedFile,
    UnauthorizedChange,
)
from core.programmer.contracts.git_model import (
    GitExecutionContext,
    GitRepository,
    GitRevision,
)
from core.programmer.contracts.git_worktree import (
    FakeGitOperations,
    GitOperationsProtocol,
)
from core.programmer.contracts.identifiers import (
    REPO_STATE_VERIFICATION_ID_PREFIX,
    new_repo_state_verification_id,
    new_verification_evidence_id,
    new_workspace_id,
    validate_execution_id,
    validate_repo_state_verification_id,
    validate_work_order_id,
)
from core.programmer.contracts.verification import (
    VerificationCheck,
    VerificationEvidence,
)
from core.programmer.contracts.work_order import ProgrammerWorkOrder
from core.programmer.contracts.workspace import (
    ProgrammerWorkspace,
    normalize_workspace_path,
)
from core.programmer.errors import (
    ProgrammerLineageError,
    ProgrammerValidationError,
)
from core.programmer.types import (
    PathBoundaryScope,
    RepositoryAnomalyType,
    VerificationCheckType,
    VerificationEvidenceSourceType,
    VerificationStatus,
)


def utc_now() -> str:
    """Generate ISO 8601 UTC timestamp."""
    return datetime.now(timezone.utc).isoformat()


@dataclass
class RepositoryAnomaly:
    """
    Diagnostic anomaly record capturing suspicious, conflicting, or non-compliant repository state.
    """
    anomaly_type: RepositoryAnomalyType
    description: str
    severity: str = "ERROR"  # "ERROR", "WARNING"
    details: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if isinstance(self.anomaly_type, str):
            try:
                self.anomaly_type = RepositoryAnomalyType(self.anomaly_type.upper())
            except (ValueError, TypeError):
                self.anomaly_type = RepositoryAnomalyType.SUSPICIOUS_STATE

    def to_dict(self) -> dict[str, Any]:
        return {
            "anomaly_type": self.anomaly_type.value,
            "description": self.description,
            "severity": self.severity,
            "details": dict(self.details),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> RepositoryAnomaly:
        return cls(
            anomaly_type=data.get("anomaly_type", RepositoryAnomalyType.SUSPICIOUS_STATE.value),
            description=str(data.get("description", "")),
            severity=str(data.get("severity", "ERROR")),
            details=dict(data.get("details", {})),
        )


@dataclass
class RepositoryStateFacts:
    """
    Empirical facts extracted directly from the Git repository and working tree.
    """
    base_revision: GitRevision
    current_revision: Optional[GitRevision]
    workspace_path: str
    repository_id: str
    changed_files: list[str] = field(default_factory=list)
    created_files: list[str] = field(default_factory=list)
    deleted_files: list[str] = field(default_factory=list)
    renamed_files: list[RenamedFile] = field(default_factory=list)
    diff_statistics: dict[str, Any] = field(default_factory=dict)
    uncommitted_changes: bool = False
    uncommitted_files: list[str] = field(default_factory=list)
    commit_references: list[GitRevision] = field(default_factory=list)
    is_clean: bool = True
    is_valid: bool = True
    error_message: Optional[str] = None
    trace: dict[str, Any] = field(default_factory=dict)

    def total_files_affected(self) -> int:
        return (
            len(self.changed_files)
            + len(self.created_files)
            + len(self.deleted_files)
            + len(self.renamed_files)
        )

    def to_observed_diff(self) -> ObservedDiff:
        """Adapt raw Git facts into a Phase 4 ObservedDiff for scope verification."""
        return ObservedDiff(
            files_changed=sorted(self.changed_files),
            files_created=sorted(self.created_files),
            files_deleted=sorted(self.deleted_files),
            files_renamed=sorted(self.renamed_files, key=lambda r: (r.old_path, r.new_path)),
            inspection_method="GIT",
            is_valid=self.is_valid,
            error_message=self.error_message,
            metadata={
                "base_revision": self.base_revision.to_dict() if self.base_revision else None,
                "current_revision": self.current_revision.to_dict() if self.current_revision else None,
                "diff_statistics": dict(self.diff_statistics),
                "uncommitted_changes": self.uncommitted_changes,
                "is_clean": self.is_clean,
            },
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "base_revision": self.base_revision.to_dict(),
            "current_revision": self.current_revision.to_dict() if self.current_revision else None,
            "workspace_path": self.workspace_path,
            "repository_id": self.repository_id,
            "changed_files": list(self.changed_files),
            "created_files": list(self.created_files),
            "deleted_files": list(self.deleted_files),
            "renamed_files": [r.to_dict() for r in self.renamed_files],
            "diff_statistics": dict(self.diff_statistics),
            "uncommitted_changes": self.uncommitted_changes,
            "uncommitted_files": list(self.uncommitted_files),
            "commit_references": [c.to_dict() for c in self.commit_references],
            "is_clean": self.is_clean,
            "is_valid": self.is_valid,
            "error_message": self.error_message,
            "trace": dict(self.trace),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> RepositoryStateFacts:
        base_rev = GitRevision.from_dict(data["base_revision"])
        curr_rev = GitRevision.from_dict(data["current_revision"]) if data.get("current_revision") else None
        commits = [GitRevision.from_dict(c) for c in data.get("commit_references", [])]
        renamed = [RenamedFile.from_dict(r) for r in data.get("renamed_files", [])]

        return cls(
            base_revision=base_rev,
            current_revision=curr_rev,
            workspace_path=data["workspace_path"],
            repository_id=data["repository_id"],
            changed_files=list(data.get("changed_files", [])),
            created_files=list(data.get("created_files", [])),
            deleted_files=list(data.get("deleted_files", [])),
            renamed_files=renamed,
            diff_statistics=dict(data.get("diff_statistics", {})),
            uncommitted_changes=bool(data.get("uncommitted_changes", False)),
            uncommitted_files=list(data.get("uncommitted_files", [])),
            commit_references=commits,
            is_clean=bool(data.get("is_clean", True)),
            is_valid=bool(data.get("is_valid", True)),
            error_message=data.get("error_message"),
            trace=dict(data.get("trace", {})),
        )


@dataclass
class RepositoryStateVerification:
    """
    Structured outcome of verifying final repository state against WorkOrder scope,
    base revision lineage, and isolation invariants.
    """
    verification_id: str = field(default_factory=new_repo_state_verification_id)
    execution_id: str = ""
    work_order_id: str = ""
    repository_id: str = ""
    workspace_path: str = ""
    base_revision: Optional[GitRevision] = None
    current_revision: Optional[GitRevision] = None
    changed_files: list[str] = field(default_factory=list)
    created_files: list[str] = field(default_factory=list)
    deleted_files: list[str] = field(default_factory=list)
    renamed_files: list[RenamedFile] = field(default_factory=list)
    diff_statistics: dict[str, Any] = field(default_factory=dict)
    uncommitted_changes: bool = False
    commit_references: list[GitRevision] = field(default_factory=list)
    unauthorized_changes: list[UnauthorizedChange] = field(default_factory=list)
    is_clean: bool = True
    anomalies: list[RepositoryAnomaly] = field(default_factory=list)
    diff_verification: Optional[DiffVerification] = None
    verification_status: VerificationStatus = VerificationStatus.NOT_VERIFIED
    evidence: list[VerificationEvidence] = field(default_factory=list)
    trace: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=utc_now)

    def __post_init__(self) -> None:
        if isinstance(self.verification_status, str):
            try:
                self.verification_status = VerificationStatus(self.verification_status.upper())
            except (ValueError, TypeError):
                self.verification_status = VerificationStatus.NOT_VERIFIED

        if isinstance(self.base_revision, dict):
            self.base_revision = GitRevision.from_dict(self.base_revision)
        if isinstance(self.current_revision, dict):
            self.current_revision = GitRevision.from_dict(self.current_revision)

        normalized_renamed: list[RenamedFile] = []
        for r in self.renamed_files:
            if isinstance(r, dict):
                normalized_renamed.append(RenamedFile.from_dict(r))
            else:
                normalized_renamed.append(r)
        self.renamed_files = normalized_renamed

        normalized_commits: list[GitRevision] = []
        for c in self.commit_references:
            if isinstance(c, dict):
                normalized_commits.append(GitRevision.from_dict(c))
            else:
                normalized_commits.append(c)
        self.commit_references = normalized_commits

        normalized_unauth: list[UnauthorizedChange] = []
        for u in self.unauthorized_changes:
            if isinstance(u, dict):
                normalized_unauth.append(UnauthorizedChange.from_dict(u))
            else:
                normalized_unauth.append(u)
        self.unauthorized_changes = normalized_unauth

        normalized_anomalies: list[RepositoryAnomaly] = []
        for a in self.anomalies:
            if isinstance(a, dict):
                normalized_anomalies.append(RepositoryAnomaly.from_dict(a))
            else:
                normalized_anomalies.append(a)
        self.anomalies = normalized_anomalies

        if isinstance(self.diff_verification, dict):
            self.diff_verification = DiffVerification.from_dict(self.diff_verification)

        normalized_evidence: list[VerificationEvidence] = []
        for e in self.evidence:
            if isinstance(e, dict):
                normalized_evidence.append(VerificationEvidence.from_dict(e))
            else:
                normalized_evidence.append(e)
        self.evidence = normalized_evidence

        self.validate()

    @property
    def is_verified(self) -> bool:
        """True if status is PASS, zero unauthorized changes, and zero ERROR anomalies."""
        return (
            self.verification_status == VerificationStatus.PASS
            and len(self.unauthorized_changes) == 0
            and not any(a.severity == "ERROR" for a in self.anomalies)
        )

    @property
    def has_anomalies(self) -> bool:
        return len(self.anomalies) > 0

    @property
    def has_unauthorized_changes(self) -> bool:
        return len(self.unauthorized_changes) > 0

    @property
    def total_changes_count(self) -> int:
        return (
            len(self.changed_files)
            + len(self.created_files)
            + len(self.deleted_files)
            + len(self.renamed_files)
        )

    def validate(self) -> None:
        """Validate identifier syntax, lineage consistency, and status invariants."""
        validate_repo_state_verification_id(self.verification_id)
        if not self.execution_id:
            raise ProgrammerLineageError("RepositoryStateVerification requires a non-empty execution_id.")
        validate_execution_id(self.execution_id)

        if not self.work_order_id:
            raise ProgrammerLineageError("RepositoryStateVerification requires a non-empty work_order_id.")
        validate_work_order_id(self.work_order_id)

        if self.base_revision is not None:
            self.base_revision.validate()

        if self.current_revision is not None:
            self.current_revision.validate()

        # Invariant: PASS status cannot have unauthorized changes or ERROR anomalies
        if self.verification_status == VerificationStatus.PASS:
            if self.unauthorized_changes:
                raise ProgrammerValidationError(
                    f"RepositoryStateVerification marked PASS cannot have unauthorized changes: {self.unauthorized_changes}."
                )
            error_anomalies = [a for a in self.anomalies if a.severity == "ERROR"]
            if error_anomalies:
                raise ProgrammerValidationError(
                    f"RepositoryStateVerification marked PASS cannot have ERROR-level anomalies: {error_anomalies}."
                )

        # Validate evidence lineage
        for ev in self.evidence:
            ev.validate()
            if ev.execution_id != self.execution_id:
                raise ProgrammerLineageError(
                    f"VerificationEvidence '{ev.evidence_id}' execution_id '{ev.execution_id}' does not match '{self.execution_id}'."
                )
            if ev.work_order_id != self.work_order_id:
                raise ProgrammerLineageError(
                    f"VerificationEvidence '{ev.evidence_id}' work_order_id '{ev.work_order_id}' does not match '{self.work_order_id}'."
                )

    def to_verification_check(self) -> VerificationCheck:
        """Convert outcome into an authoritative VerificationCheck for runner integration."""
        output_snippet = (
            f"Repository Verification: {self.verification_status.value}. "
            f"Clean: {self.is_clean}, Total Changed: {self.total_changes_count}, "
            f"Unauthorized: {len(self.unauthorized_changes)}, Anomalies: {len(self.anomalies)}."
        )
        return VerificationCheck(
            execution_id=self.execution_id,
            work_order_id=self.work_order_id,
            check_type=VerificationCheckType.STATIC_ANALYSIS,
            command="repository_state_verifier",
            status=self.verification_status,
            exit_code=0 if self.verification_status == VerificationStatus.PASS else 1,
            output_snippet=output_snippet,
            evidence=[e.evidence_id for e in self.evidence],
            trace=self.trace,
            metadata={"verification_id": self.verification_id, **self.metadata},
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "verification_id": self.verification_id,
            "execution_id": self.execution_id,
            "work_order_id": self.work_order_id,
            "repository_id": self.repository_id,
            "workspace_path": self.workspace_path,
            "base_revision": self.base_revision.to_dict() if self.base_revision else None,
            "current_revision": self.current_revision.to_dict() if self.current_revision else None,
            "changed_files": list(self.changed_files),
            "created_files": list(self.created_files),
            "deleted_files": list(self.deleted_files),
            "renamed_files": [r.to_dict() for r in self.renamed_files],
            "diff_statistics": dict(self.diff_statistics),
            "uncommitted_changes": self.uncommitted_changes,
            "commit_references": [c.to_dict() for c in self.commit_references],
            "unauthorized_changes": [u.to_dict() for u in self.unauthorized_changes],
            "is_clean": self.is_clean,
            "anomalies": [a.to_dict() for a in self.anomalies],
            "diff_verification": self.diff_verification.to_dict() if self.diff_verification else None,
            "verification_status": self.verification_status.value,
            "evidence": [e.to_dict() for e in self.evidence],
            "trace": dict(self.trace),
            "metadata": dict(self.metadata),
            "created_at": self.created_at,
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=2)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> RepositoryStateVerification:
        st_raw = data.get("verification_status", VerificationStatus.NOT_VERIFIED.value)
        try:
            st = VerificationStatus(st_raw.upper())
        except (ValueError, TypeError):
            st = VerificationStatus.NOT_VERIFIED

        base_rev = GitRevision.from_dict(data["base_revision"]) if data.get("base_revision") else None
        curr_rev = GitRevision.from_dict(data["current_revision"]) if data.get("current_revision") else None
        commits = [GitRevision.from_dict(c) for c in data.get("commit_references", [])]
        renamed = [RenamedFile.from_dict(r) for r in data.get("renamed_files", [])]
        unauth = [UnauthorizedChange.from_dict(u) for u in data.get("unauthorized_changes", [])]
        anomalies = [RepositoryAnomaly.from_dict(a) for a in data.get("anomalies", [])]
        diff_ver = DiffVerification.from_dict(data["diff_verification"]) if data.get("diff_verification") else None
        evidence = [VerificationEvidence.from_dict(e) for e in data.get("evidence", [])]

        return cls(
            verification_id=data["verification_id"],
            execution_id=data["execution_id"],
            work_order_id=data["work_order_id"],
            repository_id=data.get("repository_id", ""),
            workspace_path=data.get("workspace_path", ""),
            base_revision=base_rev,
            current_revision=curr_rev,
            changed_files=list(data.get("changed_files", [])),
            created_files=list(data.get("created_files", [])),
            deleted_files=list(data.get("deleted_files", [])),
            renamed_files=renamed,
            diff_statistics=dict(data.get("diff_statistics", {})),
            uncommitted_changes=bool(data.get("uncommitted_changes", False)),
            commit_references=commits,
            unauthorized_changes=unauth,
            is_clean=bool(data.get("is_clean", True)),
            anomalies=anomalies,
            diff_verification=diff_ver,
            verification_status=st,
            evidence=evidence,
            trace=dict(data.get("trace", {})),
            metadata=dict(data.get("metadata", {})),
            created_at=data.get("created_at", utc_now()),
        )


class RepositoryStateVerifier:
    """
    Deterministic verifier verifying the final repository state of a Programmer execution.

    Invariants & Principles:
    1. The Git layer provides empirical facts; the verification layer decides compliance.
    2. Integrates with Phase 4 DiffScopeVerifier for scope boundary evaluation.
    3. Strictly READ-ONLY: Never executes git reset, clean, checkout, merge, or push.
    4. Detects base revision mismatches, uncommitted changes, vanished changes,
       and concurrent/cross-worktree contamination.
    5. Discrepancies are surfaced as structured RepositoryAnomaly and VerificationEvidence.
    """

    def __init__(
        self,
        git_ops: Optional[GitOperationsProtocol] = None,
        diff_scope_verifier: Optional[DiffScopeVerifier] = None,
    ) -> None:
        self.git_ops = git_ops or FakeGitOperations()
        self.diff_scope_verifier = diff_scope_verifier or DiffScopeVerifier()

    def _extract_facts(
        self,
        execution_context: GitExecutionContext,
    ) -> RepositoryStateFacts:
        """Query Git operations backend for physical repository facts."""
        workspace_path = execution_context.workspace_path
        repo_id = execution_context.repository_id
        base_rev = execution_context.base_revision

        try:
            status_data = self.git_ops.inspect_status(workspace_path)
        except Exception as e:
            return RepositoryStateFacts(
                base_revision=base_rev,
                current_revision=None,
                workspace_path=workspace_path,
                repository_id=repo_id,
                is_valid=False,
                error_message=f"Git status inspection failed: {e}",
            )

        # Retrieve commit history created during execution since base_commit
        commits = self.git_ops.get_commit_history(
            workspace_path,
            base_commit=base_rev.commit_hash,
        )

        uncommitted_changed = list(status_data.get("files_changed", []))
        uncommitted_created = list(status_data.get("files_created", []))
        uncommitted_deleted = list(status_data.get("files_deleted", []))

        # Parse uncommitted renames
        raw_renamed = status_data.get("files_renamed", [])
        uncommitted_renamed: list[RenamedFile] = []
        for r in raw_renamed:
            if isinstance(r, dict):
                uncommitted_renamed.append(RenamedFile.from_dict(r))
            elif isinstance(r, RenamedFile):
                uncommitted_renamed.append(r)

        uncommitted_files: list[str] = (
            uncommitted_changed
            + uncommitted_created
            + uncommitted_deleted
            + [f"{r.old_path}->{r.new_path}" for r in uncommitted_renamed]
        )
        has_uncommitted = len(uncommitted_files) > 0

        # Aggregate cumulative file changes across committed history and uncommitted state
        cum_changed: set[str] = set(uncommitted_changed)
        cum_created: set[str] = set(uncommitted_created)
        cum_deleted: set[str] = set(uncommitted_deleted)
        cum_renamed: list[RenamedFile] = list(uncommitted_renamed)

        for c in commits:
            cf = c.metadata.get("committed_files", {})
            for p in cf.get("files_changed", []):
                cum_changed.add(p)
            for p in cf.get("files_created", []):
                cum_created.add(p)
            for p in cf.get("files_deleted", []):
                cum_deleted.add(p)
            for r in cf.get("files_renamed", []):
                rf = RenamedFile.from_dict(r) if isinstance(r, dict) else r
                if rf not in cum_renamed:
                    cum_renamed.append(rf)

        # Resolve current revision
        current_rev: Optional[GitRevision] = None
        if commits:
            current_rev = commits[-1]
        else:
            try:
                current_rev = self.git_ops.resolve_revision(workspace_path)
            except Exception:
                current_rev = execution_context.resulting_revision or base_rev

        diff_summary = dict(status_data.get("diff_summary", {}))
        diff_stats = {
            "total_files": len(cum_changed) + len(cum_created) + len(cum_deleted) + len(cum_renamed),
            "changed_count": len(cum_changed),
            "created_count": len(cum_created),
            "deleted_count": len(cum_deleted),
            "renamed_count": len(cum_renamed),
            "uncommitted_count": len(uncommitted_files),
            **diff_summary,
        }

        return RepositoryStateFacts(
            base_revision=base_rev,
            current_revision=current_rev,
            workspace_path=workspace_path,
            repository_id=repo_id,
            changed_files=sorted(cum_changed),
            created_files=sorted(cum_created),
            deleted_files=sorted(cum_deleted),
            renamed_files=sorted(cum_renamed, key=lambda r: (r.old_path, r.new_path)),
            diff_statistics=diff_stats,
            uncommitted_changes=has_uncommitted,
            uncommitted_files=uncommitted_files,
            commit_references=commits,
            is_clean=not has_uncommitted,
            is_valid=True,
        )

    def verify(
        self,
        execution_context: GitExecutionContext,
        work_order: ProgrammerWorkOrder,
        workspace: Optional[ProgrammerWorkspace] = None,
        repository: Optional[GitRepository] = None,
        allow_uncommitted: bool = False,
        expect_changes: Optional[bool] = None,
        trace: Optional[dict[str, Any]] = None,
    ) -> RepositoryStateVerification:
        """
        Verify final repository state against WorkOrder constraints, base revision lineage,
        and execution isolation boundaries.
        """
        verification_id = new_repo_state_verification_id()
        exec_id = execution_context.execution_id
        wo_id = work_order.work_order_id
        repo_id = execution_context.repository_id
        workspace_path = execution_context.workspace_path
        tr = dict(trace or {})

        anomalies: list[RepositoryAnomaly] = []

        # Step 1: Validate Lineage and Workspace Boundaries
        if repository is not None:
            if repository.repository_id != execution_context.repository_id:
                anomalies.append(
                    RepositoryAnomaly(
                        anomaly_type=RepositoryAnomalyType.WORKSPACE_MISMATCH,
                        description=(
                            f"Repository ID mismatch: context has '{execution_context.repository_id}', "
                            f"but repository has '{repository.repository_id}'."
                        ),
                        severity="ERROR",
                        details={"expected_repository_id": execution_context.repository_id, "actual_repository_id": repository.repository_id},
                    )
                )
            if repository.project_id != execution_context.project_id:
                anomalies.append(
                    RepositoryAnomaly(
                        anomaly_type=RepositoryAnomalyType.WORKSPACE_MISMATCH,
                        description=(
                            f"Project ID mismatch: context has '{execution_context.project_id}', "
                            f"but repository has '{repository.project_id}'."
                        ),
                        severity="ERROR",
                        details={"expected_project_id": execution_context.project_id, "actual_project_id": repository.project_id},
                    )
                )

        if workspace is not None:
            norm_expected = os.path.abspath(execution_context.workspace_path)
            norm_actual = os.path.abspath(workspace.root_path)
            if norm_expected != norm_actual:
                anomalies.append(
                    RepositoryAnomaly(
                        anomaly_type=RepositoryAnomalyType.WORKSPACE_MISMATCH,
                        description=(
                            f"Workspace root mismatch: expected '{norm_expected}', "
                            f"got '{norm_actual}'."
                        ),
                        severity="ERROR",
                        details={"expected_path": norm_expected, "actual_path": norm_actual},
                    )
                )

        # Step 2: Extract Physical Repository Facts
        facts = self._extract_facts(execution_context)

        if not facts.is_valid:
            anomalies.append(
                RepositoryAnomaly(
                    anomaly_type=RepositoryAnomalyType.SUSPICIOUS_STATE,
                    description=facts.error_message or "Physical repository facts could not be determined.",
                    severity="ERROR",
                    details={"error": facts.error_message},
                )
            )
            ev = VerificationEvidence(
                execution_id=exec_id,
                work_order_id=wo_id,
                source_type=VerificationEvidenceSourceType.GIT,
                source_reference="repository_state_verifier:inspection",
                description=f"Repository inspection failure: {facts.error_message}",
                is_agent_claim=False,
                data={"error": facts.error_message},
            )
            return RepositoryStateVerification(
                verification_id=verification_id,
                execution_id=exec_id,
                work_order_id=wo_id,
                repository_id=repo_id,
                workspace_path=workspace_path,
                base_revision=execution_context.base_revision,
                current_revision=None,
                verification_status=VerificationStatus.ERROR,
                anomalies=anomalies,
                evidence=[ev],
                trace=tr,
            )

        # Step 3: Base Revision Lineage Check
        try:
            resolved_base = self.git_ops.resolve_revision(
                workspace_path,
                reference=execution_context.base_revision.commit_hash,
            )
            if resolved_base.commit_hash != execution_context.base_revision.commit_hash:
                anomalies.append(
                    RepositoryAnomaly(
                        anomaly_type=RepositoryAnomalyType.BASE_REVISION_MISMATCH,
                        description=(
                            f"Base revision hash mismatch: expected '{execution_context.base_revision.commit_hash}', "
                            f"resolved '{resolved_base.commit_hash}'."
                        ),
                        severity="ERROR",
                        details={
                            "expected_base": execution_context.base_revision.commit_hash,
                            "actual_base": resolved_base.commit_hash,
                        },
                    )
                )
        except Exception as e:
            anomalies.append(
                RepositoryAnomaly(
                    anomaly_type=RepositoryAnomalyType.BASE_REVISION_MISMATCH,
                    description=f"Failed resolving base revision '{execution_context.base_revision.commit_hash}': {e}",
                    severity="ERROR",
                    details={"error": str(e), "expected_base": execution_context.base_revision.commit_hash},
                )
            )

        # Step 4: Contamination Detection (Concurrent & Cross-Execution)
        for commit in facts.commit_references:
            commit_meta = commit.metadata or {}
            commit_exec = commit_meta.get("execution_id")
            commit_wo = commit_meta.get("work_order_id")

            # Check commit message trailers if metadata is absent
            msg = commit.trace.get("message", "") if isinstance(commit.trace, dict) else ""
            if not commit_exec and "Execution-ID:" in msg:
                match = re.search(r"Execution-ID:\s*([^\s]+)", msg)
                if match:
                    commit_exec = match.group(1).strip()
            if not commit_wo and "Work-Order-ID:" in msg:
                match = re.search(r"Work-Order-ID:\s*([^\s]+)", msg)
                if match:
                    commit_wo = match.group(1).strip()

            if commit_exec and commit_exec != exec_id:
                anomalies.append(
                    RepositoryAnomaly(
                        anomaly_type=RepositoryAnomalyType.CROSS_EXECUTION_CONTAMINATION,
                        description=(
                            f"Commit '{commit.commit_hash}' has execution_id '{commit_exec}', "
                            f"which does not match current execution '{exec_id}'."
                        ),
                        severity="ERROR",
                        details={
                            "commit_hash": commit.commit_hash,
                            "contaminating_execution_id": commit_exec,
                            "expected_execution_id": exec_id,
                        },
                    )
                )

            if commit_wo and commit_wo != wo_id:
                anomalies.append(
                    RepositoryAnomaly(
                        anomaly_type=RepositoryAnomalyType.CROSS_EXECUTION_CONTAMINATION,
                        description=(
                            f"Commit '{commit.commit_hash}' has work_order_id '{commit_wo}', "
                            f"which does not match current work order '{wo_id}'."
                        ),
                        severity="ERROR",
                        details={
                            "commit_hash": commit.commit_hash,
                            "contaminating_work_order_id": commit_wo,
                            "expected_work_order_id": wo_id,
                        },
                    )
                )

        # Step 5: Cleanliness & Expectation Check
        if facts.uncommitted_changes and not allow_uncommitted:
            anomalies.append(
                RepositoryAnomaly(
                    anomaly_type=RepositoryAnomalyType.UNEXPECTED_UNCOMMITTED_CHANGES,
                    description=(
                        f"Unexpected uncommitted changes detected in working tree: "
                        f"{facts.uncommitted_files}."
                    ),
                    severity="ERROR",
                    details={"uncommitted_files": facts.uncommitted_files},
                )
            )

        if expect_changes is True and facts.total_files_affected() == 0:
            anomalies.append(
                RepositoryAnomaly(
                    anomaly_type=RepositoryAnomalyType.CHANGES_DISAPPEARED,
                    description="WorkOrder expected repository modifications, but zero changes were observed.",
                    severity="ERROR",
                    details={"expected_changes": True, "observed_changes_count": 0},
                )
            )

        # Step 6: Phase 4 DiffScopeVerifier Integration (Scope & Boundary Checks)
        target_ws = workspace or ProgrammerWorkspace(
            workspace_id=new_workspace_id(),
            project_id=execution_context.project_id or getattr(work_order, "project_id", "proj-default"),
            execution_id=exec_id,
            work_order_id=wo_id,
            root_path=workspace_path,
            allowed_paths=list(getattr(work_order, "allowed_paths", [])),
            writable_paths=list(work_order.writable_paths),
            read_only_paths=list(work_order.read_only_paths),
            forbidden_paths=list(work_order.forbidden_paths),
        )

        observed_diff = facts.to_observed_diff()
        diff_ver = self.diff_scope_verifier.verify_diff(
            workspace=target_ws,
            observed_diff=observed_diff,
            work_order=work_order,
            execution_id=exec_id,
            trace=tr,
        )

        unauthorized_changes = list(diff_ver.unauthorized_changes)
        if unauthorized_changes:
            unauth_paths = [u.path for u in unauthorized_changes]
            anomalies.append(
                RepositoryAnomaly(
                    anomaly_type=RepositoryAnomalyType.UNAUTHORIZED_CHANGES,
                    description=f"Changes detected outside authorized scope: {unauth_paths}.",
                    severity="ERROR",
                    details={"unauthorized_paths": unauth_paths, "unauthorized_changes": [u.to_dict() for u in unauthorized_changes]},
                )
            )

        # Step 7: Determine Overall Verification Status
        has_errors = any(a.severity == "ERROR" for a in anomalies)
        if has_errors or diff_ver.scope_status != VerificationStatus.PASS:
            overall_status = VerificationStatus.FAIL
        else:
            overall_status = VerificationStatus.PASS

        # Step 8: Build Authoritative Verification Evidence
        evidence_data = {
            "verification_status": overall_status.value,
            "is_clean": facts.is_clean,
            "base_revision": facts.base_revision.commit_hash,
            "current_revision": facts.current_revision.commit_hash if facts.current_revision else None,
            "total_files_affected": facts.total_files_affected(),
            "changed_files": facts.changed_files,
            "created_files": facts.created_files,
            "deleted_files": facts.deleted_files,
            "renamed_files": [r.to_dict() for r in facts.renamed_files],
            "uncommitted_changes": facts.uncommitted_changes,
            "anomalies": [a.to_dict() for a in anomalies],
            "unauthorized_changes": [u.to_dict() for u in unauthorized_changes],
        }

        desc = (
            f"Repository state verification verdict: {overall_status.value}. "
            f"Clean: {facts.is_clean}, Affected files: {facts.total_files_affected()}, "
            f"Unauthorized: {len(unauthorized_changes)}, Anomalies: {len(anomalies)}."
        )

        ev = VerificationEvidence(
            execution_id=exec_id,
            work_order_id=wo_id,
            source_type=VerificationEvidenceSourceType.GIT,
            source_reference="repository_state_verifier:git_state",
            description=desc,
            is_agent_claim=False,
            data=evidence_data,
        )

        verification_result = RepositoryStateVerification(
            verification_id=verification_id,
            execution_id=exec_id,
            work_order_id=wo_id,
            repository_id=repo_id,
            workspace_path=workspace_path,
            base_revision=facts.base_revision,
            current_revision=facts.current_revision,
            changed_files=facts.changed_files,
            created_files=facts.created_files,
            deleted_files=facts.deleted_files,
            renamed_files=facts.renamed_files,
            diff_statistics=facts.diff_statistics,
            uncommitted_changes=facts.uncommitted_changes,
            commit_references=facts.commit_references,
            unauthorized_changes=unauthorized_changes,
            is_clean=facts.is_clean,
            anomalies=anomalies,
            diff_verification=diff_ver,
            verification_status=overall_status,
            evidence=[ev],
            trace=tr,
            metadata={
                "allow_uncommitted": allow_uncommitted,
                "expect_changes": expect_changes,
            },
        )
        return verification_result
