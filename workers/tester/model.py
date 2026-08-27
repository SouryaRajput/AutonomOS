from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import hashlib
import json
from typing import Any, Optional

from workers.tester.types import (
    DefectSeverity,
    FailureClassification,
    RequirementVerificationStatus,
    RootCauseConfidence,
    TestCategory,
    TestExecutionStatus,
    TesterFinalStatus,
)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def compute_checksum(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


@dataclass
class TestingScope:
    """Explicit boundaries and budget for the test execution."""
    test_paths: list[str] = field(default_factory=list)
    target_components: list[str] = field(default_factory=list)
    excluded_paths: list[str] = field(default_factory=lambda: [".git", ".autonomos/backups"])
    max_test_commands: int = 20
    max_test_duration_s: int = 120
    cost_limit: float = 2.0
    allowed_categories: list[TestCategory] = field(
        default_factory=lambda: [TestCategory.UNIT, TestCategory.INTEGRATION, TestCategory.REGRESSION]
    )

    def to_dict(self) -> dict[str, Any]:
        return {
            "test_paths": self.test_paths,
            "target_components": self.target_components,
            "excluded_paths": self.excluded_paths,
            "max_test_commands": self.max_test_commands,
            "max_test_duration_s": self.max_test_duration_s,
            "cost_limit": self.cost_limit,
            "allowed_categories": [c.value if isinstance(c, TestCategory) else c for c in self.allowed_categories],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> TestingScope:
        cats = [TestCategory(c) for c in data.get("allowed_categories", [])]
        return cls(
            test_paths=list(data.get("test_paths", [])),
            target_components=list(data.get("target_components", [])),
            excluded_paths=list(data.get("excluded_paths", [".git", ".autonomos/backups"])),
            max_test_commands=int(data.get("max_test_commands", 20)),
            max_test_duration_s=int(data.get("max_test_duration_s", 120)),
            cost_limit=float(data.get("cost_limit", 2.0)),
            allowed_categories=cats or [TestCategory.UNIT, TestCategory.INTEGRATION, TestCategory.REGRESSION],
        )


@dataclass
class RequirementTrace:
    """Tracks verification status and supporting evidence for an explicit requirement."""
    requirement_id: str
    description: str
    status: RequirementVerificationStatus = RequirementVerificationStatus.NOT_TESTED
    test_ids: list[str] = field(default_factory=list)
    evidence_ids: list[str] = field(default_factory=list)
    notes: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "requirement_id": self.requirement_id,
            "description": self.description,
            "status": self.status.value if isinstance(self.status, RequirementVerificationStatus) else self.status,
            "test_ids": self.test_ids,
            "evidence_ids": self.evidence_ids,
            "notes": self.notes,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> RequirementTrace:
        return cls(
            requirement_id=data["requirement_id"],
            description=data["description"],
            status=RequirementVerificationStatus(data.get("status", "NOT_TESTED")),
            test_ids=list(data.get("test_ids", [])),
            evidence_ids=list(data.get("evidence_ids", [])),
            notes=data.get("notes", ""),
        )


@dataclass
class TestingTaskSpec:
    """Normalized specification of a testing and evaluation task."""
    objective: str
    requirements: list[str] = field(default_factory=list)
    success_criteria: list[dict[str, Any]] = field(default_factory=list)
    affected_components: list[str] = field(default_factory=list)
    implementation_references: list[str] = field(default_factory=list)
    relevant_artifacts: list[str] = field(default_factory=list)
    known_risks: list[str] = field(default_factory=list)
    scope: TestingScope = field(default_factory=TestingScope)
    raw_task_metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "objective": self.objective,
            "requirements": self.requirements,
            "success_criteria": self.success_criteria,
            "affected_components": self.affected_components,
            "implementation_references": self.implementation_references,
            "relevant_artifacts": self.relevant_artifacts,
            "known_risks": self.known_risks,
            "scope": self.scope.to_dict(),
            "raw_task_metadata": self.raw_task_metadata,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> TestingTaskSpec:
        return cls(
            objective=data.get("objective", ""),
            requirements=list(data.get("requirements", [])),
            success_criteria=list(data.get("success_criteria", [])),
            affected_components=list(data.get("affected_components", [])),
            implementation_references=list(data.get("implementation_references", [])),
            relevant_artifacts=list(data.get("relevant_artifacts", [])),
            known_risks=list(data.get("known_risks", [])),
            scope=TestingScope.from_dict(data.get("scope", {})),
            raw_task_metadata=dict(data.get("raw_task_metadata", {})),
        )


@dataclass
class TestingPlan:
    """Structured QA strategy outlining planned testing phases and requirements."""
    plan_id: str
    objective: str
    strategy_summary: str
    planned_phases: list[str] = field(default_factory=list)
    test_categories: list[TestCategory] = field(default_factory=list)
    requirement_traces: list[RequirementTrace] = field(default_factory=list)
    current_phase_index: int = 0
    created_at: str = field(default_factory=utc_now)

    def to_dict(self) -> dict[str, Any]:
        return {
            "plan_id": self.plan_id,
            "objective": self.objective,
            "strategy_summary": self.strategy_summary,
            "planned_phases": self.planned_phases,
            "test_categories": [c.value if isinstance(c, TestCategory) else c for c in self.test_categories],
            "requirement_traces": [r.to_dict() for r in self.requirement_traces],
            "current_phase_index": self.current_phase_index,
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> TestingPlan:
        cats = [TestCategory(c) for c in data.get("test_categories", [])]
        traces = [RequirementTrace.from_dict(r) for r in data.get("requirement_traces", [])]
        return cls(
            plan_id=data["plan_id"],
            objective=data["objective"],
            strategy_summary=data.get("strategy_summary", ""),
            planned_phases=list(data.get("planned_phases", [])),
            test_categories=cats,
            requirement_traces=traces,
            current_phase_index=int(data.get("current_phase_index", 0)),
            created_at=data.get("created_at", utc_now()),
        )


@dataclass
class TestResult:
    """Normalized execution record of an individual test or test runner command."""
    test_id: str
    name: str
    category: TestCategory
    status: TestExecutionStatus
    duration_ms: float = 0.0
    exit_code: int = 0
    command: str = ""
    output_snippet: str = ""
    error_message: str = ""
    is_baseline: bool = False
    is_regression: bool = False
    evidence_ids: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "test_id": self.test_id,
            "name": self.name,
            "category": self.category.value if isinstance(self.category, TestCategory) else self.category,
            "status": self.status.value if isinstance(self.status, TestExecutionStatus) else self.status,
            "duration_ms": self.duration_ms,
            "exit_code": self.exit_code,
            "command": self.command,
            "output_snippet": self.output_snippet,
            "error_message": self.error_message,
            "is_baseline": self.is_baseline,
            "is_regression": self.is_regression,
            "evidence_ids": self.evidence_ids,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> TestResult:
        return cls(
            test_id=data["test_id"],
            name=data.get("name", ""),
            category=TestCategory(data.get("category", "UNIT")),
            status=TestExecutionStatus(data.get("status", "PASSED")),
            duration_ms=float(data.get("duration_ms", 0.0)),
            exit_code=int(data.get("exit_code", 0)),
            command=data.get("command", ""),
            output_snippet=data.get("output_snippet", ""),
            error_message=data.get("error_message", ""),
            is_baseline=bool(data.get("is_baseline", False)),
            is_regression=bool(data.get("is_regression", False)),
            evidence_ids=list(data.get("evidence_ids", [])),
            metadata=dict(data.get("metadata", {})),
        )


@dataclass
class TestSuiteResult:
    """Aggregated outcome of a test suite execution pass."""
    suite_id: str
    suite_name: str
    total: int = 0
    passed: int = 0
    failed: int = 0
    skipped: int = 0
    errors: int = 0
    duration_ms: float = 0.0
    environment_info: dict[str, Any] = field(default_factory=dict)
    test_results: list[TestResult] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "suite_id": self.suite_id,
            "suite_name": self.suite_name,
            "total": self.total,
            "passed": self.passed,
            "failed": self.failed,
            "skipped": self.skipped,
            "errors": self.errors,
            "duration_ms": self.duration_ms,
            "environment_info": self.environment_info,
            "test_results": [t.to_dict() for t in self.test_results],
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> TestSuiteResult:
        tests = [TestResult.from_dict(t) for t in data.get("test_results", [])]
        return cls(
            suite_id=data["suite_id"],
            suite_name=data.get("suite_name", ""),
            total=int(data.get("total", 0)),
            passed=int(data.get("passed", 0)),
            failed=int(data.get("failed", 0)),
            skipped=int(data.get("skipped", 0)),
            errors=int(data.get("errors", 0)),
            duration_ms=float(data.get("duration_ms", 0.0)),
            environment_info=dict(data.get("environment_info", {})),
            test_results=tests,
            metadata=dict(data.get("metadata", {})),
        )


@dataclass
class Defect:
    """Structured defect report ready for Manager interpretation and Programmer handoff."""
    defect_id: str
    title: str
    severity: DefectSeverity
    classification: FailureClassification
    affected_requirement: str
    affected_component: str
    reproduction_steps: dict[str, Any] = field(default_factory=dict)
    expected_behavior: str = ""
    actual_behavior: str = ""
    evidence_ids: list[str] = field(default_factory=list)
    suspected_cause: str = ""
    root_cause_confidence: RootCauseConfidence = RootCauseConfidence.POSSIBLE
    suggested_fix_guidance: str = ""
    relevant_files: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "defect_id": self.defect_id,
            "title": self.title,
            "severity": self.severity.value if isinstance(self.severity, DefectSeverity) else self.severity,
            "classification": (
                self.classification.value if isinstance(self.classification, FailureClassification) else self.classification
            ),
            "affected_requirement": self.affected_requirement,
            "affected_component": self.affected_component,
            "reproduction_steps": self.reproduction_steps,
            "expected_behavior": self.expected_behavior,
            "actual_behavior": self.actual_behavior,
            "evidence_ids": self.evidence_ids,
            "suspected_cause": self.suspected_cause,
            "root_cause_confidence": (
                self.root_cause_confidence.value
                if isinstance(self.root_cause_confidence, RootCauseConfidence)
                else self.root_cause_confidence
            ),
            "suggested_fix_guidance": self.suggested_fix_guidance,
            "relevant_files": self.relevant_files,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Defect:
        return cls(
            defect_id=data["defect_id"],
            title=data.get("title", ""),
            severity=DefectSeverity(data.get("severity", "MEDIUM")),
            classification=FailureClassification(data.get("classification", "IMPLEMENTATION_BUG")),
            affected_requirement=data.get("affected_requirement", ""),
            affected_component=data.get("affected_component", ""),
            reproduction_steps=dict(data.get("reproduction_steps", {})),
            expected_behavior=data.get("expected_behavior", ""),
            actual_behavior=data.get("actual_behavior", ""),
            evidence_ids=list(data.get("evidence_ids", [])),
            suspected_cause=data.get("suspected_cause", ""),
            root_cause_confidence=RootCauseConfidence(data.get("root_cause_confidence", "POSSIBLE")),
            suggested_fix_guidance=data.get("suggested_fix_guidance", ""),
            relevant_files=list(data.get("relevant_files", [])),
            metadata=dict(data.get("metadata", {})),
        )


@dataclass
class FailureRecord:
    """Detailed diagnostic breakdown of a test failure with reproduction notes."""
    failure_id: str
    test_id: str
    classification: FailureClassification
    severity: DefectSeverity
    reproducibility: str = "REPRODUCIBLE"  # REPRODUCIBLE, INTERMITTENT, NOT_REPRODUCIBLE
    stack_trace: str = ""
    evidence_ids: list[str] = field(default_factory=list)
    notes: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "failure_id": self.failure_id,
            "test_id": self.test_id,
            "classification": (
                self.classification.value if isinstance(self.classification, FailureClassification) else self.classification
            ),
            "severity": self.severity.value if isinstance(self.severity, DefectSeverity) else self.severity,
            "reproducibility": self.reproducibility,
            "stack_trace": self.stack_trace,
            "evidence_ids": self.evidence_ids,
            "notes": self.notes,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> FailureRecord:
        return cls(
            failure_id=data["failure_id"],
            test_id=data["test_id"],
            classification=FailureClassification(data.get("classification", "UNKNOWN")),
            severity=DefectSeverity(data.get("severity", "MEDIUM")),
            reproducibility=data.get("reproducibility", "REPRODUCIBLE"),
            stack_trace=data.get("stack_trace", ""),
            evidence_ids=list(data.get("evidence_ids", [])),
            notes=data.get("notes", ""),
        )


@dataclass
class TesterResult:
    """Comprehensive QA evaluation result package returned by the Tester."""
    task_id: str
    project_id: str
    objective: str
    plan: TestingPlan
    suites: list[TestSuiteResult] = field(default_factory=list)
    defects: list[Defect] = field(default_factory=list)
    regressions: list[TestResult] = field(default_factory=list)
    requirement_traces: list[RequirementTrace] = field(default_factory=list)
    evidence_ids: list[str] = field(default_factory=list)
    artifacts: list[str] = field(default_factory=list)
    environment_info: dict[str, Any] = field(default_factory=dict)
    status: TesterFinalStatus = TesterFinalStatus.VERIFIED
    summary_for_manager: str = ""
    report_path: str = ""
    cost_estimate: float = 0.0
    created_at: str = field(default_factory=utc_now)

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "project_id": self.project_id,
            "objective": self.objective,
            "plan": self.plan.to_dict(),
            "suites": [s.to_dict() for s in self.suites],
            "defects": [d.to_dict() for d in self.defects],
            "regressions": [r.to_dict() for r in self.regressions],
            "requirement_traces": [rt.to_dict() for rt in self.requirement_traces],
            "evidence_ids": self.evidence_ids,
            "artifacts": self.artifacts,
            "environment_info": self.environment_info,
            "status": self.status.value if isinstance(self.status, TesterFinalStatus) else self.status,
            "summary_for_manager": self.summary_for_manager,
            "report_path": self.report_path,
            "cost_estimate": self.cost_estimate,
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> TesterResult:
        return cls(
            task_id=data["task_id"],
            project_id=data["project_id"],
            objective=data["objective"],
            plan=TestingPlan.from_dict(data["plan"]),
            suites=[TestSuiteResult.from_dict(s) for s in data.get("suites", [])],
            defects=[Defect.from_dict(d) for d in data.get("defects", [])],
            regressions=[TestResult.from_dict(r) for r in data.get("regressions", [])],
            requirement_traces=[RequirementTrace.from_dict(rt) for rt in data.get("requirement_traces", [])],
            evidence_ids=list(data.get("evidence_ids", [])),
            artifacts=list(data.get("artifacts", [])),
            environment_info=dict(data.get("environment_info", {})),
            status=TesterFinalStatus(data.get("status", "VERIFIED")),
            summary_for_manager=data.get("summary_for_manager", ""),
            report_path=data.get("report_path", ""),
            cost_estimate=float(data.get("cost_estimate", 0.0)),
            created_at=data.get("created_at", utc_now()),
        )
