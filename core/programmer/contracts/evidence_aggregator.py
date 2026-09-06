from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional, Sequence

from core.programmer.contracts.identifiers import (
    validate_execution_id,
    validate_work_order_id,
)
from core.programmer.contracts.verification import (
    AcceptanceResult,
    VerificationCheck,
    VerificationEvidence,
    VerificationSummary,
)
from core.programmer.contracts.work_order import ProgrammerWorkOrder
from core.programmer.errors import (
    ProgrammerLineageError,
    ProgrammerValidationError,
)
from core.programmer.types import (
    VerificationEvidenceSourceType,
    VerificationStatus,
    VerificationSummaryStatus,
)


def utc_now() -> str:
    """Generate ISO 8601 UTC timestamp."""
    return datetime.now(timezone.utc).isoformat()


class VerificationEvidenceAggregator:
    """
    Deterministic aggregator that synthesizes actual Programmer execution evidence
    into an authoritative VerificationSummary.
    
    Inputs:
    - VerificationCheck[]
    - AcceptanceResult[]
    - DiffVerification
    - VerificationEvidence[]
    - Execution events and observations
    
    Invariants:
    1. Agent narration (is_agent_claim=True) is NEVER treated as authoritative verification proof.
    2. Deterministic 4-state status calculation:
       - VERIFIED: All required checks passed, all criteria passed with authoritative evidence,
                   scope verified clean with zero unauthorized changes.
       - PARTIALLY_VERIFIED: Zero failures detected, some checks/criteria passed with authoritative
                             proof, but some remain NOT_VERIFIED or unrun.
       - FAILED: Any required check failed, or any criterion failed, or unauthorized changes occurred.
       - UNVERIFIED: Insufficient authoritative evidence exists to reach a reliable determination.
    3. Preserves full provenance: All checks and acceptance results point to supporting evidence.
    4. Explicitly surfaces risks and limitations without hiding uncertainty.
    """

    def aggregate(
        self,
        work_order: ProgrammerWorkOrder | str,
        execution_id: str,
        checks: Optional[Sequence[VerificationCheck]] = None,
        acceptance_results: Optional[Sequence[AcceptanceResult]] = None,
        diff_verification: Optional[Any] = None,
        evidence: Optional[Sequence[VerificationEvidence]] = None,
        events: Optional[Sequence[Any]] = None,
        workspace_observations: Optional[dict[str, Any]] = None,
        started_at: Optional[str] = None,
        completed_at: Optional[str] = None,
        trace: Optional[Any] = None,
        metadata: Optional[dict[str, Any]] = None,
    ) -> VerificationSummary:
        """
        Synthesize all available execution and verification facts into a validated VerificationSummary.
        """
        wo_id = getattr(work_order, "work_order_id", None) or (work_order if isinstance(work_order, str) else "")
        if not wo_id:
            raise ProgrammerLineageError("Verification aggregation requires a valid non-empty work_order_id.")
        validate_work_order_id(wo_id)

        if not execution_id:
            raise ProgrammerLineageError("Verification aggregation requires a valid non-empty execution_id.")
        validate_execution_id(execution_id)

        resolved_checks = list(checks or [])
        resolved_acceptance = list(acceptance_results or [])
        resolved_evidence = list(evidence or [])
        risks: list[str] = []
        limitations: list[str] = []

        # 1. Lineage validation
        for chk in resolved_checks:
            if chk.execution_id != execution_id:
                raise ProgrammerLineageError(
                    f"VerificationCheck '{chk.check_id}' execution_id '{chk.execution_id}' does not match '{execution_id}'."
                )
            if chk.work_order_id != wo_id:
                raise ProgrammerLineageError(
                    f"VerificationCheck '{chk.check_id}' work_order_id '{chk.work_order_id}' does not match '{wo_id}'."
                )

        for res in resolved_acceptance:
            if res.execution_id and res.execution_id != execution_id:
                raise ProgrammerLineageError(
                    f"AcceptanceResult for '{res.criterion_id}' execution_id '{res.execution_id}' does not match '{execution_id}'."
                )
            if res.work_order_id and res.work_order_id != wo_id:
                raise ProgrammerLineageError(
                    f"AcceptanceResult for '{res.criterion_id}' work_order_id '{res.work_order_id}' does not match '{wo_id}'."
                )

        if diff_verification is not None:
            dv_exec = getattr(diff_verification, "execution_id", None)
            dv_wo = getattr(diff_verification, "work_order_id", None)
            if dv_exec and dv_exec != execution_id:
                raise ProgrammerLineageError(
                    f"DiffVerification execution_id '{dv_exec}' does not match '{execution_id}'."
                )
            if dv_wo and dv_wo != wo_id:
                raise ProgrammerLineageError(
                    f"DiffVerification work_order_id '{dv_wo}' does not match '{wo_id}'."
                )

        for ev in resolved_evidence:
            if ev.execution_id != execution_id:
                raise ProgrammerLineageError(
                    f"VerificationEvidence '{ev.evidence_id}' execution_id '{ev.execution_id}' does not match '{execution_id}'."
                )
            if ev.work_order_id != wo_id:
                raise ProgrammerLineageError(
                    f"VerificationEvidence '{ev.evidence_id}' work_order_id '{ev.work_order_id}' does not match '{wo_id}'."
                )

        # 2. Collect and Index Evidence
        # Include evidence embedded in diff_verification if present
        all_evidence: list[VerificationEvidence] = list(resolved_evidence)
        seen_evidence_ids: set[str] = {e.evidence_id for e in all_evidence}

        if diff_verification is not None and hasattr(diff_verification, "evidence"):
            for dv_ev in diff_verification.evidence:
                if isinstance(dv_ev, VerificationEvidence) and dv_ev.evidence_id not in seen_evidence_ids:
                    all_evidence.append(dv_ev)
                    seen_evidence_ids.add(dv_ev.evidence_id)

        evidence_map: dict[str, VerificationEvidence] = {e.evidence_id: e for e in all_evidence}
        authoritative_evidence: list[VerificationEvidence] = [e for e in all_evidence if e.is_authoritative()]
        agent_claims: list[VerificationEvidence] = [e for e in all_evidence if not e.is_authoritative()]

        # 3. Evaluate Verification Checks
        has_failed_check = False
        passed_checks_count = 0
        unrun_checks_count = 0

        for chk in resolved_checks:
            if chk.status in (VerificationStatus.FAIL, VerificationStatus.ERROR):
                has_failed_check = True
                cmd_info = f" ({chk.command})" if chk.command else ""
                exit_info = f" (exit code {chk.exit_code})" if chk.exit_code is not None else ""
                risks.append(f"Verification check '{chk.check_id}'{cmd_info} failed{exit_info}.")
            elif chk.status == VerificationStatus.PASS:
                passed_checks_count += 1
            elif chk.status in (VerificationStatus.NOT_RUN, VerificationStatus.NOT_VERIFIED):
                unrun_checks_count += 1
                limitations.append(f"Verification check '{chk.check_id}' was not executed.")

        # Check for missing required checks declared on work order
        if isinstance(work_order, ProgrammerWorkOrder) and work_order.required_checks:
            executed_commands = {chk.command for chk in resolved_checks if chk.command}
            for req in work_order.required_checks:
                req_cmd = req.command if hasattr(req, "command") else str(req)
                if req_cmd not in executed_commands:
                    limitations.append(f"Required check '{req_cmd}' declared on WorkOrder was not executed.")

        # 4. Evaluate Diff & Scope Verification
        has_unauthorized_diff = False
        if diff_verification is None:
            limitations.append("Repository diff verification was not performed.")
        else:
            if hasattr(diff_verification, "has_unauthorized_changes") and diff_verification.has_unauthorized_changes:
                has_unauthorized_diff = True
                for u in diff_verification.unauthorized_changes:
                    risks.append(f"Unauthorized modification in {u.scope}: '{u.path}' ({u.reason}).")
            elif hasattr(diff_verification, "scope_status") and diff_verification.scope_status in (VerificationStatus.FAIL, VerificationStatus.ERROR):
                has_unauthorized_diff = True
                risks.append(f"Repository scope verification failed with status {diff_verification.scope_status}.")
            elif hasattr(diff_verification, "scope_status") and diff_verification.scope_status == VerificationStatus.NOT_VERIFIED:
                err_text = diff_verification.metadata.get("error", "unreliable repository state")
                limitations.append(f"Repository diff scope could not be verified: {err_text}.")

        # 5. Evaluate Acceptance Criteria Results & Agent Claim Discounting
        has_failed_acceptance = False
        passed_criteria_count = 0
        unverified_criteria_count = 0
        sanitized_acceptance: list[AcceptanceResult] = []

        for res in resolved_acceptance:
            if res.status in (VerificationStatus.FAIL, VerificationStatus.ERROR):
                has_failed_acceptance = True
                risks.append(f"Acceptance criterion '{res.criterion_id}' failed: {res.explanation or 'verification failed'}.")
                sanitized_acceptance.append(res)
            elif res.status in (VerificationStatus.PASS, "PASS"):
                # Check authoritative evidence backing:
                # Agent narration alone CAN NEVER satisfy PASS / VERIFIED
                criterion_authoritative = [
                    evidence_map[ev_id]
                    for ev_id in res.evidence
                    if ev_id in evidence_map and evidence_map[ev_id].is_authoritative()
                ]
                if not criterion_authoritative:
                    unverified_criteria_count += 1
                    limitations.append(
                        f"Acceptance criterion '{res.criterion_id}' claimed PASS without authoritative proof (agent narration discounted)."
                    )
                    # Downgrade to NOT_VERIFIED to uphold domain invariant
                    sanitized_acceptance.append(
                        AcceptanceResult(
                            criterion_id=res.criterion_id,
                            status=VerificationStatus.NOT_VERIFIED,
                            explanation=f"{res.explanation or 'Claimed PASS'} (discounted: lack of authoritative evidence)",
                            evidence=list(res.evidence),
                            execution_id=res.execution_id or execution_id,
                            work_order_id=res.work_order_id or wo_id,
                            trace=res.trace,
                            metadata=dict(res.metadata),
                        )
                    )
                else:
                    passed_criteria_count += 1
                    sanitized_acceptance.append(res)
            else:
                unverified_criteria_count += 1
                limitations.append(
                    f"Acceptance criterion '{res.criterion_id}' could not be verified: {res.explanation or 'no supporting evidence'}."
                )
                sanitized_acceptance.append(res)

        # 6. Deterministic Overall Status Calculation
        # Principle: Never hide uncertainty. Strictly enforce authority.
        if has_failed_check or has_failed_acceptance or has_unauthorized_diff:
            overall_status = VerificationSummaryStatus.FAILED
        elif len(authoritative_evidence) == 0:
            overall_status = VerificationSummaryStatus.UNVERIFIED
            limitations.append("Insufficient authoritative evidence exists to reach a reliable verification determination.")
        elif (passed_checks_count == 0 and passed_criteria_count == 0) or (len(resolved_checks) == 0 and len(resolved_acceptance) == 0):
            overall_status = VerificationSummaryStatus.UNVERIFIED
            limitations.append("No verification checks or acceptance criteria were verified.")
        elif unverified_criteria_count > 0 or unrun_checks_count > 0 or diff_verification is None or getattr(diff_verification, "scope_status", None) == VerificationStatus.NOT_VERIFIED:
            overall_status = VerificationSummaryStatus.PARTIALLY_VERIFIED
        else:
            overall_status = VerificationSummaryStatus.VERIFIED

        # 7. Timestamps
        now_str = utc_now()
        timestamps = {
            "verified_at": now_str,
        }
        if started_at:
            timestamps["started_at"] = started_at
        if completed_at:
            timestamps["completed_at"] = completed_at

        # 8. Synthesis Summary Text
        diff_status_str = getattr(diff_verification, "scope_status", "NOT_PERFORMED")
        if hasattr(diff_status_str, "value"):
            diff_status_str = diff_status_str.value

        summary_text = (
            f"Verification aggregated with status: {overall_status.value}. "
            f"Checks: {passed_checks_count}/{len(resolved_checks)} passed. "
            f"Criteria: {passed_criteria_count}/{len(resolved_acceptance)} verified. "
            f"Diff scope: {diff_status_str}. "
            f"Authoritative evidence: {len(authoritative_evidence)} items. "
            f"Identified {len(risks)} risks and {len(limitations)} limitations."
        )

        meta = dict(metadata or {})
        meta.update({
            "passed_checks_count": passed_checks_count,
            "total_checks_count": len(resolved_checks),
            "passed_criteria_count": passed_criteria_count,
            "total_criteria_count": len(resolved_acceptance),
            "authoritative_evidence_count": len(authoritative_evidence),
            "agent_claims_count": len(agent_claims),
            "risks_count": len(risks),
            "limitations_count": len(limitations),
        })

        summary = VerificationSummary(
            overall_status=overall_status,
            execution_id=execution_id,
            work_order_id=wo_id,
            checks=resolved_checks,
            verification_checks=resolved_checks,
            acceptance_results=sanitized_acceptance,
            diff_verification=diff_verification,
            evidence=all_evidence,
            risks=sorted(list(set(risks))),
            limitations=sorted(list(set(limitations))),
            verified_at=now_str,
            timestamps=timestamps,
            summary_text=summary_text,
            trace=trace,
            metadata=meta,
        )

        summary.validate()
        return summary
