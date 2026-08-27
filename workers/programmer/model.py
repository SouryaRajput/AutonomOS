from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
from typing import Any, Optional
import uuid

from core.models import utc_now
from workers.programmer.types import (
    FileChangeType,
    ProgrammingMode,
    ProgrammingStatus,
    TestFailureType,
)


def compute_checksum(content: str | bytes) -> str:
    """Compute SHA-256 checksum of file content."""
    raw = content.encode("utf-8") if isinstance(content, str) else content
    return hashlib.sha256(raw).hexdigest()


@dataclass
class ProgrammingScope:
    """Explicit filesystem boundary for code changes."""
    allowed_paths: list[str] = field(default_factory=list)
    excluded_paths: list[str] = field(default_factory=list)
    max_files_modified: int = 15

    def to_dict(self) -> dict[str, Any]:
        return {
            "allowed_paths": self.allowed_paths,
            "excluded_paths": self.excluded_paths,
            "max_files_modified": self.max_files_modified,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ProgrammingScope:
        return cls(
            allowed_paths=list(data.get("allowed_paths", [])),
            excluded_paths=list(data.get("excluded_paths", [])),
            max_files_modified=int(data.get("max_files_modified", 15)),
        )


@dataclass
class ProgrammingTaskSpec:
    """Normalized specification of a programming assignment."""
    objective: str
    mode: ProgrammingMode = ProgrammingMode.FEATURE
    scope: ProgrammingScope = field(default_factory=ProgrammingScope)
    requirements: list[str] = field(default_factory=list)
    constraints: list[str] = field(default_factory=list)
    relevant_research: list[str] = field(default_factory=list)
    success_criteria: list[dict[str, Any]] = field(default_factory=list)
    test_expectations: list[str] = field(default_factory=list)
    raw_task_metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "objective": self.objective,
            "mode": self.mode.value,
            "scope": self.scope.to_dict(),
            "requirements": self.requirements,
            "constraints": self.constraints,
            "relevant_research": self.relevant_research,
            "success_criteria": self.success_criteria,
            "test_expectations": self.test_expectations,
            "raw_task_metadata": self.raw_task_metadata,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ProgrammingTaskSpec:
        mode_val = data.get("mode", "FEATURE")
        try:
            mode = ProgrammingMode(mode_val)
        except ValueError:
            mode = ProgrammingMode.FEATURE

        scope_data = data.get("scope", {})
        scope = ProgrammingScope.from_dict(scope_data) if isinstance(scope_data, dict) else ProgrammingScope()

        return cls(
            objective=data.get("objective", ""),
            mode=mode,
            scope=scope,
            requirements=list(data.get("requirements", [])),
            constraints=list(data.get("constraints", [])),
            relevant_research=list(data.get("relevant_research", [])),
            success_criteria=list(data.get("success_criteria", [])),
            test_expectations=list(data.get("test_expectations", [])),
            raw_task_metadata=dict(data.get("raw_task_metadata", {})),
        )


@dataclass
class ProgrammingPlan:
    """Scoped step-by-step plan for code modifications."""
    plan_id: str
    objective: str
    mode: ProgrammingMode
    planned_steps: list[str] = field(default_factory=list)
    affected_files: list[str] = field(default_factory=list)
    estimated_complexity: str = "MEDIUM"
    current_step_index: int = 0
    created_at: str = field(default_factory=utc_now)

    def to_dict(self) -> dict[str, Any]:
        return {
            "plan_id": self.plan_id,
            "objective": self.objective,
            "mode": self.mode.value,
            "planned_steps": self.planned_steps,
            "affected_files": self.affected_files,
            "estimated_complexity": self.estimated_complexity,
            "current_step_index": self.current_step_index,
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ProgrammingPlan:
        try:
            mode = ProgrammingMode(data.get("mode", "FEATURE"))
        except ValueError:
            mode = ProgrammingMode.FEATURE

        return cls(
            plan_id=data.get("plan_id", f"plan-{uuid.uuid4().hex[:6]}"),
            objective=data.get("objective", ""),
            mode=mode,
            planned_steps=list(data.get("planned_steps", [])),
            affected_files=list(data.get("affected_files", [])),
            estimated_complexity=data.get("estimated_complexity", "MEDIUM"),
            current_step_index=int(data.get("current_step_index", 0)),
            created_at=data.get("created_at", utc_now()),
        )


@dataclass
class FileChange:
    """Auditable record of a single file operation."""
    path: str
    change_type: FileChangeType
    original_checksum: Optional[str] = None
    new_checksum: Optional[str] = None
    diff: str = ""
    description: str = ""
    old_path: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "change_type": self.change_type.value,
            "original_checksum": self.original_checksum,
            "new_checksum": self.new_checksum,
            "diff": self.diff,
            "description": self.description,
            "old_path": self.old_path,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> FileChange:
        try:
            ct = FileChangeType(data.get("change_type", "MODIFY"))
        except ValueError:
            ct = FileChangeType.MODIFY

        return cls(
            path=data.get("path", ""),
            change_type=ct,
            original_checksum=data.get("original_checksum"),
            new_checksum=data.get("new_checksum"),
            diff=data.get("diff", ""),
            description=data.get("description", ""),
            old_path=data.get("old_path"),
        )


@dataclass
class TestExecutionResult:
    """Outcome of a test suite or check execution."""
    command: str
    exit_code: int
    duration_ms: float
    passed: bool
    output: str = ""
    failure_classification: Optional[TestFailureType] = None
    error_summary: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "command": self.command,
            "exit_code": self.exit_code,
            "duration_ms": self.duration_ms,
            "passed": self.passed,
            "output": self.output,
            "failure_classification": self.failure_classification.value if self.failure_classification else None,
            "error_summary": self.error_summary,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> TestExecutionResult:
        fc_val = data.get("failure_classification")
        fc = None
        if fc_val:
            try:
                fc = TestFailureType(fc_val)
            except ValueError:
                fc = TestFailureType.UNKNOWN

        return cls(
            command=data.get("command", ""),
            exit_code=int(data.get("exit_code", 0)),
            duration_ms=float(data.get("duration_ms", 0.0)),
            passed=bool(data.get("passed", False)),
            output=data.get("output", ""),
            failure_classification=fc,
            error_summary=data.get("error_summary"),
        )


@dataclass
class BuildExecutionResult:
    """Outcome of a project compilation or package build."""
    command: str
    exit_code: int
    duration_ms: float
    passed: bool
    output: str = ""
    error_summary: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "command": self.command,
            "exit_code": self.exit_code,
            "duration_ms": self.duration_ms,
            "passed": self.passed,
            "output": self.output,
            "error_summary": self.error_summary,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> BuildExecutionResult:
        return cls(
            command=data.get("command", ""),
            exit_code=int(data.get("exit_code", 0)),
            duration_ms=float(data.get("duration_ms", 0.0)),
            passed=bool(data.get("passed", False)),
            output=data.get("output", ""),
            error_summary=data.get("error_summary"),
        )


@dataclass
class SelfReviewResult:
    """Structured pre-completion audit by the Programmer."""
    checks_passed: bool = True
    modified_files_in_scope: bool = True
    tests_added_or_updated: bool = True
    regression_risk: str = "LOW"
    assumptions_verified: bool = True
    notes: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "checks_passed": self.checks_passed,
            "modified_files_in_scope": self.modified_files_in_scope,
            "tests_added_or_updated": self.tests_added_or_updated,
            "regression_risk": self.regression_risk,
            "assumptions_verified": self.assumptions_verified,
            "notes": self.notes,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SelfReviewResult:
        return cls(
            checks_passed=bool(data.get("checks_passed", True)),
            modified_files_in_scope=bool(data.get("modified_files_in_scope", True)),
            tests_added_or_updated=bool(data.get("tests_added_or_updated", True)),
            regression_risk=str(data.get("regression_risk", "LOW")),
            assumptions_verified=bool(data.get("assumptions_verified", True)),
            notes=str(data.get("notes", "")),
        )


@dataclass
class ChangeRecord:
    """Authoritative structural ledger of all changes performed."""
    task_id: str
    project_id: str
    files_created: list[str] = field(default_factory=list)
    files_modified: list[str] = field(default_factory=list)
    files_deleted: list[str] = field(default_factory=list)
    files_renamed: list[dict[str, str]] = field(default_factory=list)
    requirements_addressed: list[str] = field(default_factory=list)
    tests_run: list[TestExecutionResult] = field(default_factory=list)
    build_result: Optional[BuildExecutionResult] = None
    artifacts: list[str] = field(default_factory=list)
    notes: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "project_id": self.project_id,
            "files_created": self.files_created,
            "files_modified": self.files_modified,
            "files_deleted": self.files_deleted,
            "files_renamed": self.files_renamed,
            "requirements_addressed": self.requirements_addressed,
            "tests_run": [t.to_dict() for t in self.tests_run],
            "build_result": self.build_result.to_dict() if self.build_result else None,
            "artifacts": self.artifacts,
            "notes": self.notes,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ChangeRecord:
        tr_list = [TestExecutionResult.from_dict(t) if isinstance(t, dict) else t for t in data.get("tests_run", [])]
        br_data = data.get("build_result")
        br = BuildExecutionResult.from_dict(br_data) if isinstance(br_data, dict) else None

        return cls(
            task_id=data.get("task_id", ""),
            project_id=data.get("project_id", ""),
            files_created=list(data.get("files_created", [])),
            files_modified=list(data.get("files_modified", [])),
            files_deleted=list(data.get("files_deleted", [])),
            files_renamed=list(data.get("files_renamed", [])),
            requirements_addressed=list(data.get("requirements_addressed", [])),
            tests_run=tr_list,
            build_result=br,
            artifacts=list(data.get("artifacts", [])),
            notes=data.get("notes", ""),
        )


@dataclass
class ProgrammerResult:
    """Comprehensive output payload returned by the Programmer Worker."""
    task_id: str
    project_id: str
    objective: str
    mode: ProgrammingMode
    plan: Optional[ProgrammingPlan] = None
    change_record: Optional[ChangeRecord] = None
    changes: list[FileChange] = field(default_factory=list)
    test_results: list[TestExecutionResult] = field(default_factory=list)
    build_result: Optional[BuildExecutionResult] = None
    self_review: Optional[SelfReviewResult] = None
    evidence_ids: list[str] = field(default_factory=list)
    report_artifact_id: Optional[str] = None
    report_path: Optional[str] = None
    summary_for_manager: str = ""
    assumptions: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    unresolved_issues: list[str] = field(default_factory=list)
    status: ProgrammingStatus = ProgrammingStatus.SUCCESS
    cost_estimate: float = 0.0
    created_at: str = field(default_factory=utc_now)

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "project_id": self.project_id,
            "objective": self.objective,
            "mode": self.mode.value,
            "plan": self.plan.to_dict() if self.plan else None,
            "change_record": self.change_record.to_dict() if self.change_record else None,
            "changes": [c.to_dict() for c in self.changes],
            "test_results": [t.to_dict() for t in self.test_results],
            "build_result": self.build_result.to_dict() if self.build_result else None,
            "self_review": self.self_review.to_dict() if self.self_review else None,
            "evidence_ids": self.evidence_ids,
            "report_artifact_id": self.report_artifact_id,
            "report_path": self.report_path,
            "summary_for_manager": self.summary_for_manager,
            "assumptions": self.assumptions,
            "warnings": self.warnings,
            "unresolved_issues": self.unresolved_issues,
            "status": self.status.value,
            "cost_estimate": self.cost_estimate,
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ProgrammerResult:
        try:
            mode = ProgrammingMode(data.get("mode", "FEATURE"))
        except ValueError:
            mode = ProgrammingMode.FEATURE

        try:
            status = ProgrammingStatus(data.get("status", "SUCCESS"))
        except ValueError:
            status = ProgrammingStatus.SUCCESS

        plan_data = data.get("plan")
        plan = ProgrammingPlan.from_dict(plan_data) if isinstance(plan_data, dict) else None

        cr_data = data.get("change_record")
        change_record = ChangeRecord.from_dict(cr_data) if isinstance(cr_data, dict) else None

        changes = [FileChange.from_dict(c) if isinstance(c, dict) else c for c in data.get("changes", [])]
        tests = [TestExecutionResult.from_dict(t) if isinstance(t, dict) else t for t in data.get("test_results", [])]
        
        br_data = data.get("build_result")
        build_result = BuildExecutionResult.from_dict(br_data) if isinstance(br_data, dict) else None

        sr_data = data.get("self_review")
        self_review = SelfReviewResult.from_dict(sr_data) if isinstance(sr_data, dict) else None

        return cls(
            task_id=data.get("task_id", ""),
            project_id=data.get("project_id", ""),
            objective=data.get("objective", ""),
            mode=mode,
            plan=plan,
            change_record=change_record,
            changes=changes,
            test_results=tests,
            build_result=build_result,
            self_review=self_review,
            evidence_ids=list(data.get("evidence_ids", [])),
            report_artifact_id=data.get("report_artifact_id"),
            report_path=data.get("report_path"),
            summary_for_manager=data.get("summary_for_manager", ""),
            assumptions=list(data.get("assumptions", [])),
            warnings=list(data.get("warnings", [])),
            unresolved_issues=list(data.get("unresolved_issues", [])),
            status=status,
            cost_estimate=float(data.get("cost_estimate", 0.0)),
            created_at=data.get("created_at", utc_now()),
        )
