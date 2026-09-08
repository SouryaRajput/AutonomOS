from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import json
import re
from typing import Any, Optional, Sequence

from core.enums import RiskLevel
from core.programmer.contracts.command_resolver import (
    CommandBoundaryResolver,
    FORBIDDEN_SHELL_TOKENS,
)
from core.programmer.contracts.engineering_risk import (
    EngineeringRisk,
    RiskAssessment,
)
from core.programmer.contracts.escalation import (
    ProgrammerEscalation,
    ProgrammerEscalationCategory,
)
from core.programmer.contracts.execution_context import ProgrammerExecutionContext
from core.programmer.contracts.filesystem_resolver import (
    FilesystemBoundaryResolver,
    is_subpath_or_equal,
)
from core.programmer.contracts.identifiers import (
    new_escalation_candidate_id,
    new_plan_validation_result_id,
    new_verification_evidence_id,
    validate_execution_id,
    validate_plan_id,
    validate_plan_validation_result_id,
    validate_work_order_id,
)
from core.programmer.contracts.impact_analysis import ImpactAnalysis
from core.programmer.contracts.implementation_plan import (
    EscalationCandidate,
    ImplementationPlan,
    ImplementationStep,
    utc_now,
)
from core.programmer.contracts.verification import VerificationEvidence
from core.programmer.contracts.work_order import ProgrammerWorkOrder
from core.programmer.contracts.workspace import ProgrammerWorkspace
from core.programmer.errors import (
    ProgrammerLineageError,
    ProgrammerValidationError,
)
from core.programmer.types import (
    EngineeringRiskCategory,
    PlanValidationStatus,
    ProgrammerBlockerSeverity,
    VerificationEvidenceSourceType,
)


@dataclass
class PlanValidationResult:
    """
    Structured outcome of validating an ImplementationPlan against the WorkOrder,
    ExecutionContext, ImpactAnalysis, and EngineeringRiskAnalysis.

    Guarantees:
    - Status is one of: APPROVED, APPROVED_WITH_WARNINGS, REQUIRES_ESCALATION, INVALID.
    - valid is True ONLY if APPROVED or APPROVED_WITH_WARNINGS.
    - Programmer may proceed autonomously only when valid is True.
    - Material scope/permission/architectural/budget issues require Manager escalation.
    - Fully serializable with immutable evidence provenance.
    """
    result_id: str = field(default_factory=new_plan_validation_result_id)
    execution_id: str = ""
    work_order_id: str = ""
    plan_id: str = ""
    status: PlanValidationStatus = PlanValidationStatus.INVALID
    valid: bool = False
    warnings: list[str] = field(default_factory=list)
    blocking_issues: list[str] = field(default_factory=list)
    escalation_candidates: list[EscalationCandidate] = field(default_factory=list)
    missing_information: list[str] = field(default_factory=list)
    evidence: list[VerificationEvidence] = field(default_factory=list)
    trace: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=utc_now)

    def __post_init__(self) -> None:
        if isinstance(self.status, str):
            try:
                self.status = PlanValidationStatus(self.status.upper())
            except (ValueError, TypeError):
                self.status = PlanValidationStatus.INVALID

        self.valid = self.status in (PlanValidationStatus.APPROVED, PlanValidationStatus.APPROVED_WITH_WARNINGS)

        normalized_esc: list[EscalationCandidate] = []
        for e in self.escalation_candidates:
            if isinstance(e, dict):
                normalized_esc.append(EscalationCandidate.from_dict(e))
            else:
                normalized_esc.append(e)
        self.escalation_candidates = normalized_esc

        normalized_ev: list[VerificationEvidence] = []
        for ev in self.evidence:
            if isinstance(ev, dict):
                normalized_ev.append(VerificationEvidence.from_dict(ev))
            else:
                normalized_ev.append(ev)
        self.evidence = normalized_ev

        self.validate()

    def validate(self) -> None:
        validate_plan_validation_result_id(self.result_id)
        validate_execution_id(self.execution_id)
        validate_work_order_id(self.work_order_id)
        validate_plan_id(self.plan_id)

    def has_escalations(self) -> bool:
        """Return True if the validation result contains prospective escalations."""
        return len(self.escalation_candidates) > 0 or self.status == PlanValidationStatus.REQUIRES_ESCALATION

    def to_dict(self) -> dict[str, Any]:
        return {
            "result_id": self.result_id,
            "execution_id": self.execution_id,
            "work_order_id": self.work_order_id,
            "plan_id": self.plan_id,
            "status": self.status.value,
            "valid": self.valid,
            "warnings": list(self.warnings),
            "blocking_issues": list(self.blocking_issues),
            "escalation_candidates": [e.to_dict() for e in self.escalation_candidates],
            "missing_information": list(self.missing_information),
            "evidence": [e.to_dict() if hasattr(e, "to_dict") else e for e in self.evidence],
            "trace": dict(self.trace),
            "metadata": dict(self.metadata),
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> PlanValidationResult:
        status_raw = data.get("status", PlanValidationStatus.INVALID.value)
        try:
            status = PlanValidationStatus(str(status_raw).upper())
        except (ValueError, TypeError):
            status = PlanValidationStatus.INVALID

        return cls(
            result_id=data["result_id"],
            execution_id=data["execution_id"],
            work_order_id=data["work_order_id"],
            plan_id=data["plan_id"],
            status=status,
            valid=bool(data.get("valid", False)),
            warnings=list(data.get("warnings", [])),
            blocking_issues=list(data.get("blocking_issues", [])),
            escalation_candidates=[EscalationCandidate.from_dict(e) for e in data.get("escalation_candidates", [])],
            missing_information=list(data.get("missing_information", [])),
            evidence=[VerificationEvidence.from_dict(e) for e in data.get("evidence", [])],
            trace=dict(data.get("trace", {})),
            metadata=dict(data.get("metadata", {})),
            created_at=data.get("created_at", utc_now()),
        )

    def to_json(self, indent: Optional[int] = None) -> str:
        return json.dumps(self.to_dict(), indent=indent)

    @classmethod
    def from_json(cls, json_str: str) -> PlanValidationResult:
        return cls.from_dict(json.loads(json_str))


class PlanValidator:
    """
    Deterministic validator evaluating an ImplementationPlan across 10 engineering dimensions:
    1. Objective alignment
    2. Acceptance criteria coverage
    3. Filesystem scope boundaries (allowed, writable, forbidden)
    4. Command authorization and construct safety
    5. Required dependencies are known
    6. Required checks exist
    7. Risks are acknowledged and mitigated
    8. No unauthorized architectural changes hidden in plan
    9. Budget compliance (iteration, time, resources)
    10. Material unknowns explicitly represented

    Produces a definitive PlanValidationResult with structured EscalationCandidates
    when Manager authority is required.
    """

    def __init__(
        self,
        command_resolver: Optional[CommandBoundaryResolver] = None,
        fs_resolver: Optional[FilesystemBoundaryResolver] = None,
    ) -> None:
        self.command_resolver = command_resolver or CommandBoundaryResolver()
        self.fs_resolver = fs_resolver or FilesystemBoundaryResolver()

    def validate_plan(
        self,
        plan: ImplementationPlan,
        work_order: ProgrammerWorkOrder,
        execution_context: Optional[ProgrammerExecutionContext] = None,
        impact_analysis: Optional[ImpactAnalysis] = None,
        risk_assessment: Optional[RiskAssessment] = None,
        workspace: Optional[ProgrammerWorkspace] = None,
        trace: Optional[dict[str, Any]] = None,
    ) -> PlanValidationResult:
        """
        Evaluate implementation plan against all constraints, generating warnings,
        blocking issues, or escalation candidates as appropriate.
        """
        # Lineage verification
        if plan.work_order_id != work_order.work_order_id:
            raise ProgrammerLineageError(
                f"ImplementationPlan work_order_id '{plan.work_order_id}' does not match "
                f"WorkOrder '{work_order.work_order_id}'."
            )
        if execution_context and execution_context.work_order_id != work_order.work_order_id:
            raise ProgrammerLineageError(
                f"ExecutionContext work_order_id '{execution_context.work_order_id}' does not match "
                f"WorkOrder '{work_order.work_order_id}'."
            )

        execution_id = plan.execution_id or (execution_context.execution_id if execution_context else "")
        result_id = new_plan_validation_result_id()

        warnings: list[str] = []
        blocking_issues: list[str] = []
        escalation_candidates: list[EscalationCandidate] = []
        missing_information: list[str] = []
        is_invalid = False

        # Propagate existing plan escalation points if any
        escalation_candidates.extend(plan.escalation_points)

        # ---------------------------------------------------------------------
        # 0. Plan Structural Integrity & Dependency Acyclicity
        # ---------------------------------------------------------------------
        try:
            plan.validate()
        except ProgrammerValidationError as e:
            blocking_issues.append(f"Plan structural validation failed: {str(e)}")
            is_invalid = True

        # ---------------------------------------------------------------------
        # 1. Objective Alignment
        # ---------------------------------------------------------------------
        if plan.objective.strip() != work_order.objective.strip():
            blocking_issues.append(
                f"Plan objective '{plan.objective}' does not match authorized WorkOrder objective '{work_order.objective}'."
            )
            esc = EscalationCandidate(
                candidate_id=new_escalation_candidate_id(),
                category=ProgrammerEscalationCategory.SCOPE,
                reason=f"Plan objective diverges from authorized WorkOrder objective.",
                target=plan.objective,
                requested_decision=f"Authorize modification of objective from '{work_order.objective}' to '{plan.objective}'.",
                severity=ProgrammerBlockerSeverity.HIGH,
                suggested_options=["Revise plan objective to match WorkOrder", "Issue revised WorkOrder with new objective"],
            )
            escalation_candidates.append(esc)

        # ---------------------------------------------------------------------
        # 2. Acceptance Criteria Coverage
        # ---------------------------------------------------------------------
        if work_order.acceptance_criteria:
            covered_ac_ids = set()
            for step in plan.steps:
                covered_ac_ids.update(step.acceptance_criteria_ids)
                for ac in work_order.acceptance_criteria:
                    if ac.criterion_id in step.description or ac.criterion_id in step.verification:
                        covered_ac_ids.add(ac.criterion_id)
                    elif ac.description and ac.description.lower() in step.description.lower():
                        covered_ac_ids.add(ac.criterion_id)

            for rc in plan.required_checks:
                for ac in work_order.acceptance_criteria:
                    if ac.criterion_id in rc or (ac.description and ac.description.lower() in rc.lower()):
                        covered_ac_ids.add(ac.criterion_id)

            missing_ac = [
                ac for ac in work_order.acceptance_criteria
                if ac.is_mandatory and ac.criterion_id not in covered_ac_ids
            ]
            if missing_ac:
                missing_ids = [ac.criterion_id for ac in missing_ac]
                blocking_issues.append(
                    f"Implementation plan does not cover mandatory acceptance criteria: {missing_ids}"
                )
                missing_information.extend([f"Coverage for criterion {ac.criterion_id}: {ac.description}" for ac in missing_ac])
                esc = EscalationCandidate(
                    candidate_id=new_escalation_candidate_id(),
                    category=ProgrammerEscalationCategory.VERIFICATION,
                    reason=f"Plan fails to cover mandatory acceptance criteria: {missing_ids}.",
                    target=", ".join(missing_ids),
                    requested_decision=f"Authorize omitting acceptance criteria {missing_ids} or require plan revision.",
                    severity=ProgrammerBlockerSeverity.HIGH,
                    suggested_options=["Add steps covering missing criteria", "Modify WorkOrder acceptance criteria"],
                )
                escalation_candidates.append(esc)

        # ---------------------------------------------------------------------
        # 3. Filesystem Scope Boundaries (Allowed, Writable, Forbidden)
        # ---------------------------------------------------------------------
        all_plan_files = set(plan.affected_files)
        for step in plan.steps:
            all_plan_files.update(step.target_files)

        for f in all_plan_files:
            is_forbidden = any(is_subpath_or_equal(f, p) for p in work_order.forbidden_paths)
            is_allowed = (
                not work_order.allowed_paths
                or any(is_subpath_or_equal(f, p) for p in work_order.allowed_paths)
                or any(is_subpath_or_equal(f, w) for w in work_order.writable_paths)
                or any(is_subpath_or_equal(f, r) for r in work_order.read_only_paths)
            )
            if is_forbidden or not is_allowed:
                blocking_issues.append(f"Plan targets file '{f}' which is forbidden or outside allowed_paths.")
                esc = EscalationCandidate(
                    candidate_id=new_escalation_candidate_id(),
                    category=ProgrammerEscalationCategory.SCOPE,
                    reason=f"File '{f}' is outside allowed paths or within forbidden scope.",
                    target=f,
                    requested_decision=f"Authorize access to '{f}' or adjust WorkOrder scope boundaries.",
                    severity=ProgrammerBlockerSeverity.HIGH,
                    suggested_options=[f"Add '{f}' to allowed_paths", "Avoid touching '{f}'"],
                )
                escalation_candidates.append(esc)

        for step in plan.steps:
            if step.action_type == "modify":
                for tf in step.target_files:
                    is_writable = any(is_subpath_or_equal(tf, w) for w in work_order.writable_paths)
                    if not is_writable:
                        blocking_issues.append(
                            f"Step '{step.step_id}' modifies file '{tf}' which is outside authorized writable_paths."
                        )
                        esc = EscalationCandidate(
                            candidate_id=new_escalation_candidate_id(),
                            category=ProgrammerEscalationCategory.SCOPE,
                            reason=f"Target file '{tf}' requires write permissions but is not in writable_paths.",
                            target=tf,
                            requested_decision=f"Grant write permission for '{tf}' in WorkOrder.",
                            severity=ProgrammerBlockerSeverity.HIGH,
                            suggested_options=[f"Add '{tf}' to writable_paths", "Refactor step to avoid writing to '{tf}'"],
                        )
                        escalation_candidates.append(esc)

        # ---------------------------------------------------------------------
        # 4. Command Scope Authorization & Construct Safety
        # ---------------------------------------------------------------------
        cmd_candidates: list[str] = list(plan.required_checks)
        for step in plan.steps:
            if step.verification:
                cmd_candidates.append(step.verification)

        resolver = execution_context.command_policy if execution_context else self.command_resolver
        for cmd_raw in cmd_candidates:
            # Check for forbidden shell metacharacters
            for token in FORBIDDEN_SHELL_TOKENS:
                if token in cmd_raw:
                    blocking_issues.append(
                        f"Unsupported or dangerous shell construct '{token}' in command: '{cmd_raw}'."
                    )
                    is_invalid = True
                    break

            # If it's formatted like a CLI command (e.g. "pytest ...", "npm ...", "python ...")
            tokens = cmd_raw.strip().split()
            if tokens and tokens[0].lower() in ("pytest", "npm", "python", "python3", "node", "cargo", "go", "mvn", "gradle", "rm", "sudo", "chmod"):
                decision = resolver.resolve(work_order=work_order, request=cmd_raw)
                if not decision.allowed:
                    blocking_issues.append(f"Command '{cmd_raw}' is not authorized: {decision.reason}")
                    esc = EscalationCandidate(
                        candidate_id=new_escalation_candidate_id(),
                        category=ProgrammerEscalationCategory.PERMISSION,
                        reason=f"Execution of command '{cmd_raw}' is not authorized by WorkOrder: {decision.reason}",
                        target=cmd_raw,
                        requested_decision=f"Authorize command '{cmd_raw}' in WorkOrder allowed_commands.",
                        severity=ProgrammerBlockerSeverity.HIGH,
                        suggested_options=[f"Add '{cmd_raw}' to allowed_commands", "Use authorized verification command"],
                    )
                    escalation_candidates.append(esc)

        # ---------------------------------------------------------------------
        # 5. Required Dependencies Are Known
        # ---------------------------------------------------------------------
        if impact_analysis and impact_analysis.unknowns:
            for unknown in impact_analysis.unknowns:
                missing_information.append(f"Unknown dependency: {unknown}")
                blocking_issues.append(f"Implementation depends on unresolved element '{unknown}'.")
                esc = EscalationCandidate(
                    candidate_id=new_escalation_candidate_id(),
                    category=ProgrammerEscalationCategory.DEPENDENCY,
                    reason=f"Plan blocked by unresolved dependency impact: '{unknown}'.",
                    target=unknown,
                    requested_decision=f"Provide clarification or credentials/access for '{unknown}'.",
                    severity=ProgrammerBlockerSeverity.HIGH,
                    suggested_options=["Provide required dependency", "Modify requirements to remove dependency"],
                )
                escalation_candidates.append(esc)

        # ---------------------------------------------------------------------
        # 6. Required Checks Exist
        # ---------------------------------------------------------------------
        for req_chk in work_order.required_checks:
            matched = any(
                req_chk.lower() in p_chk.lower() for p_chk in plan.required_checks
            ) or any(
                req_chk.lower() in step.verification.lower() for step in plan.steps
            )
            if not matched:
                warnings.append(f"WorkOrder required check '{req_chk}' is not explicitly listed in plan required_checks.")

        # ---------------------------------------------------------------------
        # 7. Risks Are Acknowledged & Mitigated
        # ---------------------------------------------------------------------
        if risk_assessment:
            for risk in risk_assessment.material_risks:
                # Add risk escalation candidates
                if risk.escalation_required:
                    blocking_issues.append(f"Material engineering risk requires Manager authority: [{risk.category.value}] {risk.description}")
                    escalation_candidates.append(risk.to_escalation_candidate())
                else:
                    warnings.append(f"Engineering risk noted [{risk.category.value}]: {risk.description}")

            for risk in risk_assessment.risks:
                if risk.severity in (RiskLevel.LOW, RiskLevel.MEDIUM) and not risk.escalation_required:
                    warnings.append(f"Advisory risk [{risk.category.value}]: {risk.description}")

        # ---------------------------------------------------------------------
        # 8. No Hidden Unauthorized Architecture Changes
        # ---------------------------------------------------------------------
        if risk_assessment:
            arch_risks = risk_assessment.get_risks_by_category(EngineeringRiskCategory.ARCHITECTURAL_CHANGE)
            if arch_risks:
                for ar in arch_risks:
                    blocking_issues.append(f"Unauthorized architectural change detected: {ar.description}")
                    escalation_candidates.append(ar.to_escalation_candidate())

        # ---------------------------------------------------------------------
        # 9. Budget Compliance (Iterations & Time)
        # ---------------------------------------------------------------------
        iteration_budget = getattr(work_order, "iteration_budget", 10)
        if len(plan.steps) > iteration_budget:
            blocking_issues.append(
                f"Plan step count ({len(plan.steps)}) exceeds WorkOrder iteration budget ({iteration_budget})."
            )
            esc = EscalationCandidate(
                candidate_id=new_escalation_candidate_id(),
                category=ProgrammerEscalationCategory.RESOURCE,
                reason=f"Plan requires {len(plan.steps)} steps, exceeding authorized iteration_budget of {iteration_budget}.",
                target="iteration_budget",
                requested_decision=f"Increase iteration budget to at least {len(plan.steps)}.",
                severity=ProgrammerBlockerSeverity.MEDIUM,
                suggested_options=[f"Increase iteration_budget to {len(plan.steps)}", "Consolidate plan into fewer steps"],
            )
            escalation_candidates.append(esc)

        # ---------------------------------------------------------------------
        # 10. Material Unknowns Represented
        # ---------------------------------------------------------------------
        for u in plan.unknowns:
            if u not in missing_information:
                missing_information.append(u)

        # ---------------------------------------------------------------------
        # Final Decision Synthesis
        # ---------------------------------------------------------------------
        # Deduplicate escalation candidates by (category, target)
        unique_escalations: list[EscalationCandidate] = []
        seen_targets = set()
        for ec in escalation_candidates:
            key = (ec.category.value if hasattr(ec.category, "value") else str(ec.category), ec.target or ec.reason)
            if key not in seen_targets:
                seen_targets.add(key)
                unique_escalations.append(ec)
        escalation_candidates = unique_escalations

        if is_invalid:
            status = PlanValidationStatus.INVALID
        elif escalation_candidates or blocking_issues:
            status = PlanValidationStatus.REQUIRES_ESCALATION
        elif warnings:
            status = PlanValidationStatus.APPROVED_WITH_WARNINGS
        else:
            status = PlanValidationStatus.APPROVED

        # Evidence generation
        ev = VerificationEvidence(
            evidence_id=new_verification_evidence_id(),
            execution_id=execution_id,
            work_order_id=work_order.work_order_id,
            source_type=VerificationEvidenceSourceType.PLAN_VALIDATION,
            source_reference=result_id,
            description=f"ImplementationPlan validation result: {status.value}",
            data={
                "status": status.value,
                "valid": status in (PlanValidationStatus.APPROVED, PlanValidationStatus.APPROVED_WITH_WARNINGS),
                "warnings_count": len(warnings),
                "blocking_count": len(blocking_issues),
                "escalations_count": len(escalation_candidates),
                "missing_information_count": len(missing_information),
            },
            is_agent_claim=False,
        )

        res_trace = dict(trace or {})
        res_trace.update({
            "result_id": result_id,
            "plan_id": plan.plan_id,
            "work_order_id": work_order.work_order_id,
            "execution_id": execution_id,
            "status": status.value,
            "created_at": utc_now(),
        })

        return PlanValidationResult(
            result_id=result_id,
            execution_id=execution_id,
            work_order_id=work_order.work_order_id,
            plan_id=plan.plan_id,
            status=status,
            valid=(status in (PlanValidationStatus.APPROVED, PlanValidationStatus.APPROVED_WITH_WARNINGS)),
            warnings=warnings,
            blocking_issues=blocking_issues,
            escalation_candidates=escalation_candidates,
            missing_information=missing_information,
            evidence=[ev],
            trace=res_trace,
        )
