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
from core.tester.contracts.preflight import TestPreflightResult
from core.tester.contracts.quality_summary import QualitySummary
from core.tester.contracts.recommendation import TesterRecommendation
from core.tester.contracts.result_validator import TesterResultValidator
from core.tester.contracts.runtime_observation import RuntimeObservation
from core.tester.contracts.test_case import TestCaseResult
from core.tester.errors import TesterLineageError
from core.tester.types import (
    AcceptanceCriterionStatus,
    DefectSeverity,
    DefectType,
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
    preflight_result: Optional[TestPreflightResult] = None
    runtime_observations: list[RuntimeObservation] = field(default_factory=list)
    metrics: dict[str, Any] = field(default_factory=dict)
    recommendations: list[TesterRecommendation] = field(default_factory=list)
    blockers: list[str] = field(default_factory=list)
    blocked_items: list[str] = field(default_factory=list)
    unverified_items: list[str] = field(default_factory=list)
    risks: list[str] = field(default_factory=list)
    uncertainties: list[str] = field(default_factory=list)
    quality_summary: Optional[QualitySummary] = None
    visual_results: list[Any] = field(default_factory=list)
    responsive_results: list[Any] = field(default_factory=list)
    typography_results: list[Any] = field(default_factory=list)
    animation_results: list[Any] = field(default_factory=list)
    ux_results: list[Any] = field(default_factory=list)
    visual_ux_summary: dict[str, Any] = field(default_factory=dict)
    performance_measurements: list[Any] = field(default_factory=list)
    performance_results: list[Any] = field(default_factory=list)
    stability_results: list[Any] = field(default_factory=list)
    performance_stability_summary: dict[str, Any] = field(default_factory=dict)
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

        # Normalize preflight_result
        if isinstance(self.preflight_result, dict):
            self.preflight_result = TestPreflightResult.from_dict(self.preflight_result)

        # Normalize runtime_observations
        norm_ro: list[RuntimeObservation] = []
        for ro in self.runtime_observations:
            if isinstance(ro, RuntimeObservation):
                norm_ro.append(ro)
            elif isinstance(ro, dict):
                norm_ro.append(RuntimeObservation.from_dict(ro))
        self.runtime_observations = norm_ro

        # Auto-populate blocked_items
        if not self.blocked_items:
            blocked_set = set(self.blockers)
            for tc in self.test_cases:
                if tc.is_blocked:
                    blocked_set.add(f"Test '{tc.name or tc.test_id}' is blocked: {tc.observed_behavior or 'Blocked'}")
            self.blocked_items = sorted(list(blocked_set))
        else:
            self.blocked_items = list(self.blocked_items)

        # Auto-populate unverified_items
        if not self.unverified_items:
            unverified_set = set()
            for ac in self.acceptance_results:
                if ac.status == AcceptanceCriterionStatus.NOT_VERIFIED:
                    unverified_set.add(f"Acceptance criterion '{ac.criterion_id}' is not verified: {ac.notes or ac.description}")
            for tc in self.test_cases:
                if tc.status in (TestCaseStatus.NOT_RUN, TestCaseStatus.NOT_VERIFIED):
                    unverified_set.add(f"Test '{tc.name or tc.test_id}' not verified")
            self.unverified_items = sorted(list(unverified_set))
        else:
            self.unverified_items = list(self.unverified_items)

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
        if self.preflight_result and hasattr(self.preflight_result, "evidence_ids"):
            all_ev_ids.update(self.preflight_result.evidence_ids)
        for ro in self.runtime_observations:
            all_ev_ids.update(ro.evidence_ids)
        for r_list in (self.performance_results, self.stability_results):
            for item in r_list:
                if hasattr(item, "evidence_ids"):
                    all_ev_ids.update(item.evidence_ids)
                elif isinstance(item, dict) and "evidence_ids" in item:
                    all_ev_ids.update(item["evidence_ids"])
        self.evidence_ids = sorted(list(all_ev_ids))

        # Setup timestamps
        if not self.timestamps:
            self.timestamps = {"created_at": self.created_at}
        elif "created_at" not in self.timestamps:
            self.timestamps["created_at"] = self.created_at

        if self.completed_at:
            self.timestamps["completed_at"] = self.completed_at

        self.visual_results = list(self.visual_results)
        self.responsive_results = list(self.responsive_results)
        self.typography_results = list(self.typography_results)
        self.animation_results = list(self.animation_results)
        self.ux_results = list(self.ux_results)
        self.performance_measurements = list(self.performance_measurements)
        self.performance_results = list(self.performance_results)
        self.stability_results = list(self.stability_results)

        # Populate visual_ux_summary if empty
        if not self.visual_ux_summary:
            vis_defect_types = {
                DefectType.VISUAL,
                DefectType.LAYOUT,
                DefectType.SCROLL,
                DefectType.OVERFLOW,
                DefectType.RESPONSIVE,
                DefectType.TYPOGRAPHY,
                DefectType.ANIMATION,
            }
            vis_defects_count = sum(1 for d in self.defects if d.defect_type in vis_defect_types)
            ux_defects_count = sum(1 for d in self.defects if d.defect_type == DefectType.UX)
            vis_tests_count = len(self.visual_results) + len(self.responsive_results) + len(self.typography_results) + len(self.animation_results)
            ux_tests_count = len(self.ux_results)
            if vis_tests_count == 0 and ux_tests_count == 0:
                # Count from test_cases
                for tc in self.test_cases:
                    name_lower = (tc.name or "").lower()
                    if any(w in name_lower for w in ("visual", "layout", "responsive", "typography", "animation", "scroll")):
                        vis_tests_count += 1
                    elif any(w in name_lower for w in ("ux", "onboarding", "flow", "usability")):
                        ux_tests_count += 1

            vis_ux_evidence: set[str] = set()
            for r_list in (self.visual_results, self.responsive_results, self.typography_results, self.animation_results, self.ux_results):
                for item in r_list:
                    if hasattr(item, "evidence_ids"):
                        vis_ux_evidence.update(item.evidence_ids)
                    elif isinstance(item, dict) and "evidence_ids" in item:
                        vis_ux_evidence.update(item["evidence_ids"])

            self.visual_ux_summary = {
                "visual_tests_executed": vis_tests_count,
                "ux_tests_executed": ux_tests_count,
                "visual_defects": vis_defects_count,
                "ux_defects": ux_defects_count,
                "findings": len(self.findings),
                "recommendations": len(self.recommendations),
                "blocked_evaluations": len(self.blocked_items),
                "not_verified_evaluations": len(self.unverified_items),
                "evidence_references": sorted(list(vis_ux_evidence)),
            }
        else:
            self.visual_ux_summary = dict(self.visual_ux_summary)

        # Populate performance_stability_summary if empty
        if not self.performance_stability_summary:
            perf_defects_count = sum(1 for d in self.defects if d.defect_type == DefectType.PERFORMANCE)
            resource_defects_count = sum(1 for d in self.defects if d.defect_type == DefectType.RESOURCE)
            stability_defects_count = sum(
                1 for d in self.defects
                if d.defect_type in (DefectType.RUNTIME, DefectType.CRASH, DefectType.STABILITY)
            )

            load_tests_count = 0
            interaction_tests_count = 0
            network_requests_count = 0
            for r in self.performance_results:
                r_type = getattr(r, "result_type", None) or (r.get("result_type") if isinstance(r, dict) else "")
                r_type_str = str(r_type).upper()
                if "LOAD" in r_type_str or "NAVIGATION" in r_type_str or hasattr(r, "startup_duration_ms") or (isinstance(r, dict) and "startup_duration_ms" in r):
                    load_tests_count += 1
                elif "INTERACTION" in r_type_str or hasattr(r, "interaction_duration_ms") or (isinstance(r, dict) and "interaction_duration_ms" in r):
                    interaction_tests_count += 1
                elif "NETWORK" in r_type_str or hasattr(r, "total_requests") or (isinstance(r, dict) and "total_requests" in r):
                    req_count = getattr(r, "total_requests", None) or (r.get("total_requests") if isinstance(r, dict) else 1)
                    network_requests_count += req_count
                else:
                    load_tests_count += 1

            stab_status = "HEALTHY"
            if any(d.severity == DefectSeverity.CRITICAL for d in self.defects if d.defect_type in (DefectType.CRASH, DefectType.RUNTIME, DefectType.STABILITY)):
                stab_status = "CRITICAL"
            elif any(d.defect_type in (DefectType.CRASH, DefectType.RUNTIME, DefectType.STABILITY) for d in self.defects):
                stab_status = "DEGRADED"

            for sr in self.stability_results:
                s_stat = getattr(sr, "stability_status", None) or (sr.get("stability_status") if isinstance(sr, dict) else None)
                if s_stat:
                    s_stat_str = str(s_stat.value if hasattr(s_stat, "value") else s_stat).upper()
                    if s_stat_str in ("CRASHED", "CRITICAL", "UNSTABLE", "FAILED"):
                        stab_status = "CRITICAL"
                        break
                    elif s_stat_str in ("DEGRADED", "RECOVERED") and stab_status != "CRITICAL":
                        stab_status = "DEGRADED"

            perf_stab_evidence: set[str] = set()
            for r_list in (self.performance_results, self.stability_results):
                for item in r_list:
                    if hasattr(item, "evidence_ids"):
                        perf_stab_evidence.update(item.evidence_ids)
                    elif isinstance(item, dict) and "evidence_ids" in item:
                        perf_stab_evidence.update(item["evidence_ids"])

            self.performance_stability_summary = {
                "load_tests_executed": load_tests_count,
                "interaction_tests_executed": interaction_tests_count,
                "network_requests_evaluated": network_requests_count,
                "performance_defects": perf_defects_count,
                "resource_defects": resource_defects_count,
                "stability_defects": stability_defects_count,
                "stability_status": stab_status,
                "evidence_references": sorted(list(perf_stab_evidence)),
            }
        else:
            self.performance_stability_summary = dict(self.performance_stability_summary)

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

    @property
    def runtime_errors(self) -> list[RuntimeObservation]:
        """Return all actionable application runtime error observations."""
        return [ro for ro in self.runtime_observations if ro.is_failure and ro.is_application_owned]

    @property
    def has_runtime_failures(self) -> bool:
        """Returns True if any runtime failure observations exist."""
        return len(self.runtime_errors) > 0

    @property
    def is_pass(self) -> bool:
        """
        Returns True if the product actually PASSED evaluation.
        Distinguishes product acceptance from evaluation completion!
        Requires:
        - evaluation completed successfully (is_success)
        - no critical defects
        - all acceptance criteria verified PASS (is_acceptance_fully_verified)
        - advisory recommendation is SHIP or SHIP_WITH_WARNINGS
        - preflight succeeded if preflight occurred
        - no runtime failures
        """
        if not self.is_success():
            return False
        if self.has_critical_defects():
            return False
        if not self.is_acceptance_fully_verified():
            return False
        if self.ship_recommendation not in (ShipRecommendation.SHIP, ShipRecommendation.SHIP_WITH_WARNINGS):
            return False
        if self.preflight_result and hasattr(self.preflight_result, "decision"):
            from core.tester.types import PreflightDecision
            if self.preflight_result.decision in (PreflightDecision.FAILED, PreflightDecision.BLOCKED):
                return False
        if self.has_runtime_failures:
            return False
        for sr in self.stability_results:
            s_stat = getattr(sr, "stability_status", None) or (sr.get("stability_status") if isinstance(sr, dict) else None)
            if s_stat:
                s_stat_str = str(s_stat.value if hasattr(s_stat, "value") else s_stat).upper()
                if s_stat_str in ("CRASHED", "CRITICAL", "FAILED"):
                    return False
        return True

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
            "preflight_status": (
                self.preflight_result.status.value
                if self.preflight_result and hasattr(self.preflight_result.status, "value")
                else (str(self.preflight_result.status) if self.preflight_result else "NONE")
            ),
            "preflight_decision": (
                self.preflight_result.decision.value
                if self.preflight_result and hasattr(self.preflight_result.decision, "value")
                else (str(self.preflight_result.decision) if self.preflight_result else "NONE")
            ),
            "runtime_observations_count": len(self.runtime_observations),
            "runtime_failures_count": len(self.runtime_errors),
            "is_pass": self.is_pass,
            "blocked_items": list(self.blocked_items),
            "unverified_items": list(self.unverified_items),
            "recommendations": [
                r.title if hasattr(r, "title") else str(r)
                for r in self.recommendations
            ],
            "uncertainties": list(self.uncertainties),
            "blockers": list(self.blockers),
            "risks": list(self.risks),
            "artifacts": list(self.artifacts),
            "timestamps": dict(self.timestamps),
            "visual_ux_summary": dict(self.visual_ux_summary),
            "performance_stability_summary": dict(self.performance_stability_summary),
        })

        err_msg = "; ".join(self.blockers) if self.blockers else None
        if not err_msg and self.ship_recommendation == ShipRecommendation.DO_NOT_SHIP:
            err_msg = f"Evaluation completed with recommendation DO_NOT_SHIP: {len(self.defects)} defect(s) detected."
        elif not err_msg and not self.is_success():
            err_msg = self.summary or "Tester execution failed."

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
            "preflight_result": self.preflight_result.to_dict() if self.preflight_result else None,
            "runtime_observations": [ro.to_dict() for ro in self.runtime_observations],
            "metrics": dict(self.metrics),
            "recommendations": [
                r.to_dict() if hasattr(r, "to_dict") else r
                for r in self.recommendations
            ],
            "blockers": list(self.blockers),
            "blocked_items": list(self.blocked_items),
            "unverified_items": list(self.unverified_items),
            "risks": list(self.risks),
            "uncertainties": list(self.uncertainties),
            "quality_summary": self.quality_summary.to_dict() if self.quality_summary else None,
            "visual_results": [vr.to_dict() if hasattr(vr, "to_dict") else vr for vr in self.visual_results],
            "responsive_results": [rr.to_dict() if hasattr(rr, "to_dict") else rr for rr in self.responsive_results],
            "typography_results": [tr.to_dict() if hasattr(tr, "to_dict") else tr for tr in self.typography_results],
            "animation_results": [ar.to_dict() if hasattr(ar, "to_dict") else ar for ar in self.animation_results],
            "ux_results": [ux.to_dict() if hasattr(ux, "to_dict") else ux for ux in self.ux_results],
            "visual_ux_summary": dict(self.visual_ux_summary),
            "performance_measurements": [
                pm.to_dict() if hasattr(pm, "to_dict") else pm
                for pm in self.performance_measurements
            ],
            "performance_results": [
                pr.to_dict() if hasattr(pr, "to_dict") else pr
                for pr in self.performance_results
            ],
            "stability_results": [
                sr.to_dict() if hasattr(sr, "to_dict") else sr
                for sr in self.stability_results
            ],
            "performance_stability_summary": dict(self.performance_stability_summary),
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

        pf_raw = data.get("preflight_result")
        preflight_result = TestPreflightResult.from_dict(pf_raw) if isinstance(pf_raw, dict) else pf_raw

        runtime_observations = [
            RuntimeObservation.from_dict(ro) if isinstance(ro, dict) else ro
            for ro in data.get("runtime_observations", [])
        ]

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
            preflight_result=preflight_result,
            runtime_observations=runtime_observations,
            metrics=dict(data.get("metrics", {})),
            recommendations=recommendations,
            blockers=list(data.get("blockers", [])),
            blocked_items=list(data.get("blocked_items", [])),
            unverified_items=list(data.get("unverified_items", [])),
            risks=list(data.get("risks", [])),
            uncertainties=list(data.get("uncertainties", [])),
            quality_summary=quality_summary,
            visual_results=list(data.get("visual_results", [])),
            responsive_results=list(data.get("responsive_results", [])),
            typography_results=list(data.get("typography_results", [])),
            animation_results=list(data.get("animation_results", [])),
            ux_results=list(data.get("ux_results", [])),
            visual_ux_summary=dict(data.get("visual_ux_summary", {})),
            performance_measurements=list(data.get("performance_measurements", [])),
            performance_results=list(data.get("performance_results", [])),
            stability_results=list(data.get("stability_results", [])),
            performance_stability_summary=dict(data.get("performance_stability_summary", {})),
            trace=dict(data.get("trace", {})),
            evidence_ids=list(data.get("evidence_ids", [])),
            artifacts=list(data.get("artifacts", [])),
            timestamps=dict(data.get("timestamps", {})),
            metadata=dict(data.get("metadata", {})),
            created_at=str(data.get("created_at", utc_now())),
            completed_at=data.get("completed_at"),
        )
