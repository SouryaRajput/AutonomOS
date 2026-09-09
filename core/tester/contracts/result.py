from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional, Sequence

from core.models import WorkerOutput
from core.tester.contracts.finding import (
    AcceptanceCriterionResult,
    TesterDefect,
    TesterEvidence,
    TesterFinding,
)
from core.tester.contracts.identifiers import (
    new_result_id,
    validate_execution_id,
    validate_result_id,
    validate_work_order_id,
)
from core.tester.contracts.quality_summary import QualitySummary
from core.tester.contracts.recommendation import TesterRecommendation
from core.tester.contracts.result_validator import TesterResultValidator
from core.tester.contracts.test_case import TestCaseResult
from core.tester.errors import TesterLineageError
from core.tester.types import (
    AcceptanceCriterionStatus,
    DefectSeverity,
    ShipRecommendation,
    TestCaseStatus,
    TesterResultStatus,
)


def utc_now() -> str:
    """Generate ISO 8601 UTC timestamp."""
    return datetime.now(timezone.utc).isoformat()


@dataclass
class TesterResult:
    """
    Final authoritative evaluation result package produced by Tester and returned to Manager.
    
    Evidence-backed: details what was observed, what passed/failed, what defects were identified,
    what findings were discovered, what recommendations are proposed, and what evidence supports it.
    
    Invariants:
    - Strictly preserves causal lineage:
      ManagerTask -> TesterWorkOrder -> TesterExecution -> TesterResult
    - Completion != Product Acceptance: Tester completing its evaluation is separate from product acceptance.
    - Advisory Ship Recommendation: Tester provides an evaluative assessment, but Manager remains the sole authority.
    - Immutability: Represents a historical execution record.
    """
    __test__ = False
    result_id: str
    execution_id: str
    work_order_id: str
    task_id: str = ""
    project_id: str = ""
    correlation_id: str = ""
    status: TesterResultStatus = TesterResultStatus.COMPLETED
    ship_recommendation: ShipRecommendation = ShipRecommendation.NOT_VERIFIED
    summary: str = ""
    summary_for_manager: str = ""
    test_cases: list[TestCaseResult] = field(default_factory=list)
    defects: list[TesterDefect] = field(default_factory=list)
    findings: list[TesterFinding] = field(default_factory=list)
    acceptance_results: list[AcceptanceCriterionResult] = field(default_factory=list)
    evidence: list[TesterEvidence] = field(default_factory=list)
    metrics: dict[str, Any] = field(default_factory=dict)
    recommendations: list[TesterRecommendation] = field(default_factory=list)
    blockers: list[str] = field(default_factory=list)
    risks: list[str] = field(default_factory=list)
    uncertainties: list[str] = field(default_factory=list)
    quality_summary: Optional[QualitySummary] = None
    trace: dict[str, Any] = field(default_factory=dict)
    evidence_ids: list[str] = field(default_factory=list)
    artifacts: list[str] = field(default_factory=list)
    timestamps: dict[str, str] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=utc_now)
    completed_at: Optional[str] = None

    def __post_init__(self) -> None:
        validate_result_id(self.result_id)
        validate_execution_id(self.execution_id)
        validate_work_order_id(self.work_order_id)

        # Synchronize summary and summary_for_manager
        if not self.summary and self.summary_for_manager:
            self.summary = self.summary_for_manager
        elif not self.summary_for_manager and self.summary:
            self.summary_for_manager = self.summary

        # Normalize status enum
        if isinstance(self.status, str):
            try:
                self.status = TesterResultStatus(self.status.upper())
            except (ValueError, KeyError):
                self.status = TesterResultStatus.COMPLETED

        # Normalize ship_recommendation enum
        if isinstance(self.ship_recommendation, str):
            try:
                self.ship_recommendation = ShipRecommendation(self.ship_recommendation.upper())
            except (ValueError, KeyError):
                self.ship_recommendation = ShipRecommendation.NOT_VERIFIED

        # Normalize test_cases
        norm_tcs: list[TestCaseResult] = []
        for tc in self.test_cases:
            if isinstance(tc, TestCaseResult):
                norm_tcs.append(tc)
            elif isinstance(tc, dict):
                norm_tcs.append(TestCaseResult.from_dict(tc))
        self.test_cases = norm_tcs

        # Normalize defects
        norm_defects: list[TesterDefect] = []
        for d in self.defects:
            if isinstance(d, TesterDefect):
                norm_defects.append(d)
            elif isinstance(d, dict):
                norm_defects.append(TesterDefect.from_dict(d))
        self.defects = norm_defects

        # Normalize findings
        norm_findings: list[TesterFinding] = []
        for f in self.findings:
            if isinstance(f, TesterFinding):
                norm_findings.append(f)
            elif isinstance(f, dict):
                norm_findings.append(TesterFinding.from_dict(f))
        self.findings = norm_findings

        # Normalize acceptance_results
        norm_ac: list[AcceptanceCriterionResult] = []
        for a in self.acceptance_results:
            if isinstance(a, AcceptanceCriterionResult):
                norm_ac.append(a)
            elif isinstance(a, dict):
                norm_ac.append(AcceptanceCriterionResult.from_dict(a))
        self.acceptance_results = norm_ac

        # Normalize evidence
        norm_ev: list[TesterEvidence] = []
        for e in self.evidence:
            if isinstance(e, TesterEvidence):
                norm_ev.append(e)
            elif isinstance(e, dict):
                norm_ev.append(TesterEvidence.from_dict(e))
        self.evidence = norm_ev

        # Normalize recommendations (support both string and TesterRecommendation)
        norm_recs: list[TesterRecommendation | str] = []
        for r in self.recommendations:
            if isinstance(r, TesterRecommendation):
                norm_recs.append(r)
            elif isinstance(r, dict):
                norm_recs.append(TesterRecommendation.from_dict(r))
            elif isinstance(r, str):
                norm_recs.append(r)
        self.recommendations = norm_recs

        # Normalize quality_summary
        if isinstance(self.quality_summary, dict):
            self.quality_summary = QualitySummary.from_dict(self.quality_summary)
        elif self.quality_summary is None and (self.test_cases or self.defects or self.acceptance_results):
            # Compute default quality summary if components exist and none provided
            self.quality_summary = QualitySummary.compute(
                test_cases=self.test_cases,
                defects=self.defects,
                findings=self.findings,
                acceptance_results=self.acceptance_results,
            )

        # Collect evidence IDs from all components and merge with local evidence_ids
        all_ev_ids = set(self.evidence_ids)
        for tc in self.test_cases:
            all_ev_ids.update(tc.evidence_ids)
        for d in self.defects:
            all_ev_ids.update(d.evidence_ids)
        for f in self.findings:
            all_ev_ids.update(f.evidence_ids)
        for a in self.acceptance_results:
            all_ev_ids.update(a.evidence_ids)
        for r in self.recommendations:
            if hasattr(r, "evidence_ids"):
                all_ev_ids.update(r.evidence_ids)
        for e in self.evidence:
            all_ev_ids.add(e.evidence_id)
        self.evidence_ids = sorted(list(all_ev_ids))

        # Setup timestamps
        if not self.timestamps:
            self.timestamps = {"created_at": self.created_at}
        elif "created_at" not in self.timestamps:
            self.timestamps["created_at"] = self.created_at

        if self.completed_at:
            self.timestamps["completed_at"] = self.completed_at

        self.metrics = dict(self.metrics)
        self.risks = list(self.risks)
        self.trace = dict(self.trace)

    @property
    def tests_run(self) -> int:
        return len(self.test_cases)

    @property
    def tests_passed(self) -> int:
        return sum(1 for tc in self.test_cases if tc.is_pass)

    @property
    def tests_failed(self) -> int:
        return sum(1 for tc in self.test_cases if tc.is_fail)

    @property
    def tests_blocked(self) -> int:
        return sum(1 for tc in self.test_cases if tc.is_blocked)

    def validate(self) -> None:
        """Run complete deterministic validation on the result package."""
        TesterResultValidator.validate_all(self)

    def validate_lineage(self, execution: Any = None, work_order: Any = None) -> None:
        """
        Verify that this result strictly matches the lineage of the given execution and work order.
        Raises TesterLineageError if any identifier fails to match.
        """
        if execution is not None:
            if self.execution_id != execution.execution_id:
                raise TesterLineageError(
                    f"Result execution_id '{self.execution_id}' does not match execution.execution_id '{execution.execution_id}'"
                )
            if self.work_order_id != execution.work_order_id:
                raise TesterLineageError(
                    f"Result work_order_id '{self.work_order_id}' does not match execution.work_order_id '{execution.work_order_id}'"
                )
            if self.task_id and execution.task_id and self.task_id != execution.task_id:
                raise TesterLineageError(
                    f"Result task_id '{self.task_id}' does not match execution.task_id '{execution.task_id}'"
                )
            if self.project_id and execution.project_id and self.project_id != execution.project_id:
                raise TesterLineageError(
                    f"Result project_id '{self.project_id}' does not match execution.project_id '{execution.project_id}'"
                )
            if self.correlation_id and execution.correlation_id and self.correlation_id != execution.correlation_id:
                raise TesterLineageError(
                    f"Result correlation_id '{self.correlation_id}' does not match execution.correlation_id '{execution.correlation_id}'"
                )

        if work_order is not None:
            if self.work_order_id != work_order.work_order_id:
                raise TesterLineageError(
                    f"Result work_order_id '{self.work_order_id}' does not match work_order.work_order_id '{work_order.work_order_id}'"
                )
            wo_task_id = getattr(work_order, "manager_task_id", None) or getattr(work_order, "task_id", None)
            if self.task_id and wo_task_id and self.task_id != wo_task_id:
                raise TesterLineageError(
                    f"Result task_id '{self.task_id}' does not match work_order task_id '{wo_task_id}'"
                )
            if self.project_id and work_order.project_id and self.project_id != work_order.project_id:
                raise TesterLineageError(
                    f"Result project_id '{self.project_id}' does not match work_order.project_id '{work_order.project_id}'"
                )
            if self.correlation_id and work_order.correlation_id and self.correlation_id != work_order.correlation_id:
                raise TesterLineageError(
                    f"Result correlation_id '{self.correlation_id}' does not match work_order.correlation_id '{work_order.correlation_id}'"
                )

    def is_success(self) -> bool:
        """
        Returns True if the evaluation execution completed successfully without internal execution crashes.
        NOTE: This reflects Tester evaluation completion, NOT product acceptance!
        Product acceptance is determined by acceptance_results and Manager disposition.
        """
        return self.status in {TesterResultStatus.COMPLETED, TesterResultStatus.SUCCESS}

    def has_defects(self) -> bool:
        """Returns True if any concrete defects were identified."""
        return len(self.defects) > 0

    def has_critical_defects(self) -> bool:
        """Returns True if any critical or high severity defects were identified."""
        return any(
            d.severity in (DefectSeverity.CRITICAL, DefectSeverity.HIGH)
            for d in self.defects
        )

    def has_uncertainty(self) -> bool:
        """Returns True if any uncertainty was reported."""
        return len(self.uncertainties) > 0 or any(f.is_uncertain for f in self.findings)

    def is_acceptance_fully_verified(self) -> bool:
        """Returns True if at least one acceptance criterion exists and all have PASS status."""
        if not self.acceptance_results:
            return False
        return all(ac.status == AcceptanceCriterionStatus.PASS for ac in self.acceptance_results)

    def to_worker_output(self) -> WorkerOutput:
        """Convert TesterResult to standard AutonomOS WorkerOutput."""
        meta = dict(self.metadata)
        meta.update({
            "result_id": self.result_id,
            "execution_id": self.execution_id,
            "work_order_id": self.work_order_id,
            "task_id": self.task_id,
            "project_id": self.project_id,
            "correlation_id": self.correlation_id,
            "tester_status": self.status.value if hasattr(self.status, "value") else str(self.status),
            "ship_recommendation": (
                self.ship_recommendation.value
                if hasattr(self.ship_recommendation, "value")
                else str(self.ship_recommendation)
            ),
            "tests_run": self.tests_run,
            "tests_passed": self.tests_passed,
            "tests_failed": self.tests_failed,
            "tests_blocked": self.tests_blocked,
            "evidence_ids": list(self.evidence_ids),
            "defects_count": len(self.defects),
            "critical_defects_count": sum(1 for d in self.defects if d.severity == DefectSeverity.CRITICAL),
            "high_defects_count": sum(1 for d in self.defects if d.severity == DefectSeverity.HIGH),
            "findings_count": len(self.findings),
            "acceptance_count": len(self.acceptance_results),
            "acceptance_fully_verified": self.is_acceptance_fully_verified(),
            "recommendations": [
                r.title if hasattr(r, "title") else str(r)
                for r in self.recommendations
            ],
            "uncertainties": list(self.uncertainties),
            "blockers": list(self.blockers),
            "risks": list(self.risks),
            "artifacts": list(self.artifacts),
            "timestamps": dict(self.timestamps),
        })

        err_msg = "; ".join(self.blockers) if self.blockers else None

        return WorkerOutput(
            success=self.is_success(),
            summary=self.summary_for_manager or self.summary,
            error_message=err_msg,
            metadata=meta,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "result_id": self.result_id,
            "execution_id": self.execution_id,
            "work_order_id": self.work_order_id,
            "task_id": self.task_id,
            "project_id": self.project_id,
            "correlation_id": self.correlation_id,
            "status": self.status.value if hasattr(self.status, "value") else str(self.status),
            "ship_recommendation": (
                self.ship_recommendation.value
                if hasattr(self.ship_recommendation, "value")
                else str(self.ship_recommendation)
            ),
            "summary": self.summary,
            "summary_for_manager": self.summary_for_manager,
            "test_cases": [tc.to_dict() for tc in self.test_cases],
            "defects": [d.to_dict() for d in self.defects],
            "findings": [f.to_dict() for f in self.findings],
            "acceptance_results": [a.to_dict() for a in self.acceptance_results],
            "evidence": [e.to_dict() for e in self.evidence],
            "metrics": dict(self.metrics),
            "recommendations": [
                r.to_dict() if hasattr(r, "to_dict") else r
                for r in self.recommendations
            ],
            "blockers": list(self.blockers),
            "risks": list(self.risks),
            "uncertainties": list(self.uncertainties),
            "quality_summary": self.quality_summary.to_dict() if self.quality_summary else None,
            "trace": dict(self.trace),
            "evidence_ids": list(self.evidence_ids),
            "artifacts": list(self.artifacts),
            "timestamps": dict(self.timestamps),
            "metadata": dict(self.metadata),
            "created_at": self.created_at,
            "completed_at": self.completed_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> TesterResult:
        st_raw = data.get("status", TesterResultStatus.COMPLETED.value)
        try:
            status = TesterResultStatus(str(st_raw).upper())
        except (ValueError, KeyError):
            status = TesterResultStatus.COMPLETED

        ship_raw = data.get("ship_recommendation", ShipRecommendation.NOT_VERIFIED.value)
        try:
            ship_rec = ShipRecommendation(str(ship_raw).upper())
        except (ValueError, KeyError):
            ship_rec = ShipRecommendation.NOT_VERIFIED

        test_cases = [
            TestCaseResult.from_dict(tc) if isinstance(tc, dict) else tc
            for tc in data.get("test_cases", [])
        ]
        defects = [
            TesterDefect.from_dict(d) if isinstance(d, dict) else d
            for d in data.get("defects", [])
        ]
        findings = [
            TesterFinding.from_dict(f) if isinstance(f, dict) else f
            for f in data.get("findings", [])
        ]
        acceptance_results = [
            AcceptanceCriterionResult.from_dict(a) if isinstance(a, dict) else a
            for a in data.get("acceptance_results", [])
        ]
        evidence = [
            TesterEvidence.from_dict(e) if isinstance(e, dict) else e
            for e in data.get("evidence", [])
        ]
        recommendations = [
            TesterRecommendation.from_dict(r) if isinstance(r, dict) else r
            for r in data.get("recommendations", [])
        ]

        qs_raw = data.get("quality_summary")
        quality_summary = QualitySummary.from_dict(qs_raw) if isinstance(qs_raw, dict) else qs_raw

        summary = str(data.get("summary") or data.get("summary_for_manager", ""))

        return cls(
            result_id=str(data.get("result_id", new_result_id())),
            execution_id=str(data.get("execution_id", "")),
            work_order_id=str(data.get("work_order_id", "")),
            task_id=str(data.get("task_id", "")),
            project_id=str(data.get("project_id", "")),
            correlation_id=str(data.get("correlation_id", "")),
            status=status,
            ship_recommendation=ship_rec,
            summary=summary,
            summary_for_manager=summary,
            test_cases=test_cases,
            defects=defects,
            findings=findings,
            acceptance_results=acceptance_results,
            evidence=evidence,
            metrics=dict(data.get("metrics", {})),
            recommendations=recommendations,
            blockers=list(data.get("blockers", [])),
            risks=list(data.get("risks", [])),
            uncertainties=list(data.get("uncertainties", [])),
            quality_summary=quality_summary,
            trace=dict(data.get("trace", {})),
            evidence_ids=list(data.get("evidence_ids", [])),
            artifacts=list(data.get("artifacts", [])),
            timestamps=dict(data.get("timestamps", {})),
            metadata=dict(data.get("metadata", {})),
            created_at=str(data.get("created_at", utc_now())),
            completed_at=data.get("completed_at"),
        )
