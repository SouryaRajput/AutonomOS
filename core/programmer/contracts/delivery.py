from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import json
from typing import Any, Optional, Sequence

from core.programmer.contracts.acceptance_result import AcceptanceCriterionResult
from core.programmer.contracts.diff_verifier import RenamedFile, UnauthorizedChange
from core.programmer.contracts.git_change_set import ChangeSet
from core.programmer.contracts.git_model import GitExecutionContext, GitRevision
from core.programmer.contracts.git_verification import (
    RepositoryAnomaly,
    RepositoryStateVerification,
)
from core.programmer.contracts.identifiers import (
    DELIVERY_ID_PREFIX,
    new_delivery_id,
    validate_delivery_id,
    validate_execution_id,
    validate_work_order_id,
)
from core.programmer.contracts.result import ProgrammerResult
from core.programmer.contracts.test_record import TestResultRecord
from core.programmer.contracts.verification import (
    VerificationEvidence,
    VerificationSummary,
)
from core.programmer.contracts.work_order import ProgrammerWorkOrder
from core.programmer.errors import (
    ProgrammerLineageError,
    ProgrammerValidationError,
)
from core.programmer.types import (
    AcceptanceStatus,
    ManagerDisposition,
    ProgrammerResultStatus,
    VerificationStatus,
    VerificationSummaryStatus,
)


def utc_now() -> str:
    """Generate ISO 8601 UTC timestamp."""
    return datetime.now(timezone.utc).isoformat()


@dataclass
class DeliveryPackage:
    """
    Authoritative final delivery package returned from Programmer to Manager after implementation
    and verification.

    Core Invariants:
    1. Answers the 10 core audit questions objectively using empirical facts.
    2. Programmer may recommend a disposition, but Manager has authoritative decision power.
    3. Strictly non-publishing: Never automatically merges, pushes, or deploys.
    4. Evidence and lineage are immutable and cryptographically traceable.
    """
    delivery_id: str = field(default_factory=new_delivery_id)
    execution_id: str = ""
    work_order_id: str = ""
    repository_id: str = ""
    base_revision: Optional[GitRevision] = None
    resulting_revision: Optional[GitRevision] = None
    branch_or_reference: Optional[str] = None
    change_set: Optional[ChangeSet] = None
    verification_summary: Optional[Any] = None
    acceptance_results: list[AcceptanceCriterionResult] = field(default_factory=list)
    test_results: list[TestResultRecord] = field(default_factory=list)
    risk_summary: dict[str, Any] = field(default_factory=dict)
    blockers: list[str] = field(default_factory=list)
    evidence: list[VerificationEvidence] = field(default_factory=list)
    trace: dict[str, Any] = field(default_factory=dict)
    recommended_disposition: ManagerDisposition = ManagerDisposition.REQUEST_CHANGES
    disposition_rationale: str = ""
    manager_disposition: Optional[ManagerDisposition] = None
    manager_notes: Optional[str] = None
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=utc_now)

    def __post_init__(self) -> None:
        if isinstance(self.recommended_disposition, str):
            try:
                self.recommended_disposition = ManagerDisposition(self.recommended_disposition.upper())
            except (ValueError, TypeError):
                self.recommended_disposition = ManagerDisposition.REJECT

        if isinstance(self.manager_disposition, str):
            try:
                self.manager_disposition = ManagerDisposition(self.manager_disposition.upper())
            except (ValueError, TypeError):
                self.manager_disposition = None

        if isinstance(self.base_revision, dict):
            self.base_revision = GitRevision.from_dict(self.base_revision)
        if isinstance(self.resulting_revision, dict):
            self.resulting_revision = GitRevision.from_dict(self.resulting_revision)

        if isinstance(self.change_set, dict):
            self.change_set = ChangeSet.from_dict(self.change_set)

        if isinstance(self.verification_summary, dict):
            # Attempt rehydration as RepositoryStateVerification or VerificationSummary
            if "diff_statistics" in self.verification_summary:
                self.verification_summary = RepositoryStateVerification.from_dict(self.verification_summary)
            else:
                self.verification_summary = VerificationSummary.from_dict(self.verification_summary)

        normalized_ac: list[AcceptanceCriterionResult] = []
        for a in self.acceptance_results:
            if isinstance(a, dict):
                normalized_ac.append(AcceptanceCriterionResult.from_dict(a))
            else:
                normalized_ac.append(a)
        self.acceptance_results = normalized_ac

        normalized_tests: list[TestResultRecord] = []
        for t in self.test_results:
            if isinstance(t, dict):
                normalized_tests.append(TestResultRecord.from_dict(t))
            else:
                normalized_tests.append(t)
        self.test_results = normalized_tests

        normalized_ev: list[VerificationEvidence] = []
        for e in self.evidence:
            if isinstance(e, dict):
                normalized_ev.append(VerificationEvidence.from_dict(e))
            else:
                normalized_ev.append(e)
        self.evidence = normalized_ev

        self.validate()

    # -------------------------------------------------------------------------
    # The 10 Core Audit Answers
    # -------------------------------------------------------------------------

    def what_was_requested(self) -> str:
        """1. What was requested?"""
        return str(self.metadata.get("requested_objective") or self.metadata.get("objective") or "")

    def what_changed(self) -> dict[str, Any]:
        """2. What changed?"""
        if self.change_set is not None:
            return {
                "files_changed": list(self.change_set.files_changed),
                "files_created": list(self.change_set.files_created),
                "files_deleted": list(self.change_set.files_deleted),
                "diff_summary": dict(self.change_set.diff_summary),
                "status": self.change_set.status.value,
            }
        if isinstance(self.verification_summary, RepositoryStateVerification):
            return {
                "files_changed": list(self.verification_summary.changed_files),
                "files_created": list(self.verification_summary.created_files),
                "files_deleted": list(self.verification_summary.deleted_files),
                "renamed_files": [r.to_dict() for r in self.verification_summary.renamed_files],
                "diff_summary": dict(self.verification_summary.diff_statistics),
                "is_clean": self.verification_summary.is_clean,
            }
        return {"files_changed": [], "files_created": [], "files_deleted": [], "diff_summary": {}}

    def start_revision(self) -> Optional[str]:
        """3. What revision did we start from?"""
        return self.base_revision.commit_hash if self.base_revision else None

    def result_revision(self) -> Optional[str]:
        """4. What revision contains the result?"""
        if self.resulting_revision:
            return self.resulting_revision.commit_hash
        if self.base_revision:
            return self.base_revision.commit_hash
        return None

    def tests_run_summary(self) -> dict[str, Any]:
        """5. What tests were run?"""
        passed = sum(t.tests_passed for t in self.test_results)
        failed = sum(t.tests_failed for t in self.test_results)
        skipped = sum(t.tests_skipped for t in self.test_results)
        total = sum(t.total_tests for t in self.test_results)
        return {
            "total_suites": len(self.test_results),
            "total_tests": total,
            "tests_passed": passed,
            "tests_failed": failed,
            "tests_skipped": skipped,
            "all_passed": all(t.passed for t in self.test_results) if self.test_results else True,
            "suite_commands": [t.command for t in self.test_results],
        }

    def acceptance_criteria_passed(self) -> list[str]:
        """6. What acceptance criteria passed?"""
        return [
            a.description or a.criterion_id
            for a in self.acceptance_results
            if a.status == AcceptanceStatus.PASS
        ]

    def what_remains_unverified(self) -> list[str]:
        """7. What remains unverified?"""
        unverified: list[str] = []
        for a in self.acceptance_results:
            if a.status == AcceptanceStatus.NOT_VERIFIED:
                unverified.append(f"Acceptance Criterion: {a.description or a.criterion_id}")
            elif a.status == AcceptanceStatus.FAIL:
                unverified.append(f"Failed Criterion: {a.description or a.criterion_id}")

        for t in self.test_results:
            if not t.passed:
                unverified.append(f"Failing Test Suite: {t.command}")

        # Check uncommitted changes in change set or repo verification
        if self.change_set and self.change_set.status.value == "UNCOMMITTED":
            unverified.append("Uncommitted working tree changes exist")

        return unverified

    def has_unauthorized_changes(self) -> bool:
        """8. Are there unauthorized changes?"""
        if isinstance(self.verification_summary, RepositoryStateVerification):
            return self.verification_summary.has_unauthorized_changes
        if hasattr(self.verification_summary, "diff_verification") and self.verification_summary.diff_verification:
            dv = self.verification_summary.diff_verification
            return getattr(dv, "has_unauthorized_changes", False)
        return False

    def unauthorized_changes_details(self) -> list[dict[str, Any]]:
        """Detailed list of unauthorized changes if any."""
        if isinstance(self.verification_summary, RepositoryStateVerification):
            return [u.to_dict() for u in self.verification_summary.unauthorized_changes]
        if hasattr(self.verification_summary, "diff_verification") and self.verification_summary.diff_verification:
            dv = self.verification_summary.diff_verification
            if hasattr(dv, "unauthorized_changes"):
                return [u.to_dict() if hasattr(u, "to_dict") else dict(u) for u in dv.unauthorized_changes]
        return []

    def known_risks(self) -> list[str]:
        """9. Are there known risks?"""
        risks: list[str] = list(self.risk_summary.get("items", [])) if "items" in self.risk_summary else []
        for b in self.blockers:
            if b not in risks:
                risks.append(f"Blocker: {b}")
        if self.has_unauthorized_changes():
            risks.append("Unauthorized scope modifications detected")
        return risks

    def recommended_action(self) -> dict[str, Any]:
        """10. What action should Manager take next?"""
        return {
            "disposition": self.recommended_disposition.value,
            "rationale": self.disposition_rationale,
        }

    # -------------------------------------------------------------------------
    # Manager Disposition
    # -------------------------------------------------------------------------

    def record_manager_disposition(
        self,
        disposition: ManagerDisposition,
        notes: Optional[str] = None,
    ) -> None:
        """
        Record Manager's authoritative review decision.
        Does not automatically merge, push, deploy, or publish.
        """
        if isinstance(disposition, str):
            disposition = ManagerDisposition(disposition.upper())
        self.manager_disposition = disposition
        self.manager_notes = notes

    # -------------------------------------------------------------------------
    # Validation & Invariants
    # -------------------------------------------------------------------------

    def validate(self) -> None:
        """Validate package identifiers, lineage, and disposition invariants."""
        validate_delivery_id(self.delivery_id)
        if not self.execution_id:
            raise ProgrammerLineageError("DeliveryPackage requires a non-empty execution_id.")
        validate_execution_id(self.execution_id)

        if not self.work_order_id:
            raise ProgrammerLineageError("DeliveryPackage requires a non-empty work_order_id.")
        validate_work_order_id(self.work_order_id)

        if self.base_revision is not None:
            self.base_revision.validate()

        if self.resulting_revision is not None:
            self.resulting_revision.validate()

        # Lineage check with change_set
        if self.change_set is not None:
            if self.change_set.execution_id != self.execution_id:
                raise ProgrammerLineageError(
                    f"ChangeSet execution_id '{self.change_set.execution_id}' does not match '{self.execution_id}'."
                )
            if self.change_set.work_order_id != self.work_order_id:
                raise ProgrammerLineageError(
                    f"ChangeSet work_order_id '{self.change_set.work_order_id}' does not match '{self.work_order_id}'."
                )

        # Lineage check with verification_summary
        if self.verification_summary is not None:
            if hasattr(self.verification_summary, "execution_id") and self.verification_summary.execution_id:
                if self.verification_summary.execution_id != self.execution_id:
                    raise ProgrammerLineageError(
                        f"Verification summary execution_id '{self.verification_summary.execution_id}' does not match '{self.execution_id}'."
                    )
            if hasattr(self.verification_summary, "work_order_id") and self.verification_summary.work_order_id:
                if self.verification_summary.work_order_id != self.work_order_id:
                    raise ProgrammerLineageError(
                        f"Verification summary work_order_id '{self.verification_summary.work_order_id}' does not match '{self.work_order_id}'."
                    )

        # Lineage check with evidence
        for ev in self.evidence:
            ev.validate()
            if ev.execution_id != self.execution_id:
                raise ProgrammerLineageError(
                    f"VerificationEvidence execution_id '{ev.execution_id}' does not match '{self.execution_id}'."
                )
            if ev.work_order_id != self.work_order_id:
                raise ProgrammerLineageError(
                    f"VerificationEvidence work_order_id '{ev.work_order_id}' does not match '{self.work_order_id}'."
                )

        # Invariant: If ACCEPT is recommended:
        # 1. Zero unauthorized changes allowed
        # 2. At least one authoritative evidence item must be present
        if self.recommended_disposition == ManagerDisposition.ACCEPT:
            if self.has_unauthorized_changes():
                raise ProgrammerValidationError(
                    "DeliveryPackage cannot recommend ACCEPT when unauthorized changes exist."
                )
            authoritative_evidence = [e for e in self.evidence if e.is_authoritative()]
            if not authoritative_evidence:
                raise ProgrammerValidationError(
                    "DeliveryPackage cannot recommend ACCEPT without authoritative verification evidence."
                )

    # -------------------------------------------------------------------------
    # Conversions & Serialization
    # -------------------------------------------------------------------------

    def to_programmer_result(self) -> ProgrammerResult:
        """Convert DeliveryPackage to standard ProgrammerResult for AutonomOS compatibility."""
        if self.recommended_disposition == ManagerDisposition.ACCEPT:
            st = ProgrammerResultStatus.SUCCESS
        elif self.recommended_disposition == ManagerDisposition.CANCEL:
            st = ProgrammerResultStatus.CANCELLED
        elif self.recommended_disposition == ManagerDisposition.ESCALATE:
            st = ProgrammerResultStatus.BLOCKED
        elif self.recommended_disposition == ManagerDisposition.REQUEST_CHANGES:
            st = ProgrammerResultStatus.PARTIAL
        else:
            st = ProgrammerResultStatus.FAILED

        changes = self.what_changed()
        return ProgrammerResult(
            result_id=f"pres-{self.delivery_id[-8:]}",
            execution_id=self.execution_id,
            work_order_id=self.work_order_id,
            project_id=self.metadata.get("project_id", ""),
            status=st,
            summary=self.disposition_rationale or f"Delivery Package {self.delivery_id}",
            summary_for_manager=self.disposition_rationale,
            files_changed=list(changes.get("files_changed", [])),
            files_created=list(changes.get("files_created", [])),
            files_deleted=list(changes.get("files_deleted", [])),
            test_results=list(self.test_results),
            acceptance_results=list(self.acceptance_results),
            blockers=list(self.blockers),
            risks=self.known_risks(),
            evidence_ids=[e.evidence_id for e in self.evidence],
            trace=self.trace,
            metadata={"delivery_id": self.delivery_id, **self.metadata},
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "delivery_id": self.delivery_id,
            "execution_id": self.execution_id,
            "work_order_id": self.work_order_id,
            "repository_id": self.repository_id,
            "base_revision": self.base_revision.to_dict() if self.base_revision else None,
            "resulting_revision": self.resulting_revision.to_dict() if self.resulting_revision else None,
            "branch_or_reference": self.branch_or_reference,
            "change_set": self.change_set.to_dict() if self.change_set else None,
            "verification_summary": self.verification_summary.to_dict() if hasattr(self.verification_summary, "to_dict") else self.verification_summary,
            "acceptance_results": [a.to_dict() for a in self.acceptance_results],
            "test_results": [t.to_dict() for t in self.test_results],
            "risk_summary": dict(self.risk_summary),
            "blockers": list(self.blockers),
            "evidence": [e.to_dict() for e in self.evidence],
            "trace": dict(self.trace),
            "recommended_disposition": self.recommended_disposition.value,
            "disposition_rationale": self.disposition_rationale,
            "manager_disposition": self.manager_disposition.value if self.manager_disposition else None,
            "manager_notes": self.manager_notes,
            "metadata": dict(self.metadata),
            "created_at": self.created_at,
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=2)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> DeliveryPackage:
        rec_disp_raw = data.get("recommended_disposition", ManagerDisposition.REQUEST_CHANGES.value)
        try:
            rec_disp = ManagerDisposition(str(rec_disp_raw).upper())
        except (ValueError, TypeError):
            rec_disp = ManagerDisposition.REJECT

        mgr_disp_raw = data.get("manager_disposition")
        mgr_disp = None
        if mgr_disp_raw:
            try:
                mgr_disp = ManagerDisposition(str(mgr_disp_raw).upper())
            except (ValueError, TypeError):
                mgr_disp = None

        base_rev = GitRevision.from_dict(data["base_revision"]) if data.get("base_revision") else None
        res_rev = GitRevision.from_dict(data["resulting_revision"]) if data.get("resulting_revision") else None
        cs = ChangeSet.from_dict(data["change_set"]) if data.get("change_set") else None

        ver_sum = data.get("verification_summary")
        if isinstance(ver_sum, dict):
            if "diff_statistics" in ver_sum:
                ver_sum = RepositoryStateVerification.from_dict(ver_sum)
            else:
                ver_sum = VerificationSummary.from_dict(ver_sum)

        ac_list = [AcceptanceCriterionResult.from_dict(a) for a in data.get("acceptance_results", [])]
        tests_list = [TestResultRecord.from_dict(t) for t in data.get("test_results", [])]
        ev_list = [VerificationEvidence.from_dict(e) for e in data.get("evidence", [])]

        return cls(
            delivery_id=data["delivery_id"],
            execution_id=data["execution_id"],
            work_order_id=data["work_order_id"],
            repository_id=data.get("repository_id", ""),
            base_revision=base_rev,
            resulting_revision=res_rev,
            branch_or_reference=data.get("branch_or_reference"),
            change_set=cs,
            verification_summary=ver_sum,
            acceptance_results=ac_list,
            test_results=tests_list,
            risk_summary=dict(data.get("risk_summary", {})),
            blockers=list(data.get("blockers", [])),
            evidence=ev_list,
            trace=dict(data.get("trace", {})),
            recommended_disposition=rec_disp,
            disposition_rationale=data.get("disposition_rationale", ""),
            manager_disposition=mgr_disp,
            manager_notes=data.get("manager_notes"),
            metadata=dict(data.get("metadata", {})),
            created_at=data.get("created_at", utc_now()),
        )


class DeliveryPreparer:
    """
    Deterministic builder compiling verified implementation state into an authoritative DeliveryPackage.
    """

    def prepare(
        self,
        execution_context: GitExecutionContext,
        work_order: ProgrammerWorkOrder,
        change_set: Optional[ChangeSet] = None,
        repository_verification: Optional[RepositoryStateVerification] = None,
        verification_summary: Optional[VerificationSummary] = None,
        test_results: Optional[Sequence[TestResultRecord]] = None,
        acceptance_results: Optional[Sequence[AcceptanceCriterionResult]] = None,
        blockers: Optional[Sequence[str]] = None,
        risks: Optional[Sequence[str]] = None,
        evidence: Optional[Sequence[VerificationEvidence]] = None,
        trace: Optional[dict[str, Any]] = None,
    ) -> DeliveryPackage:
        """
        Build an immutable, traceable DeliveryPackage evaluating compliance to recommend Manager disposition.
        """
        # Step 1: Lineage Validation
        if execution_context.work_order_id != work_order.work_order_id:
            raise ProgrammerLineageError(
                f"Execution context work_order_id '{execution_context.work_order_id}' "
                f"does not match WorkOrder '{work_order.work_order_id}'."
            )
        if execution_context.project_id and work_order.project_id:
            if execution_context.project_id != work_order.project_id:
                raise ProgrammerLineageError(
                    f"Execution context project_id '{execution_context.project_id}' "
                    f"does not match WorkOrder '{work_order.project_id}'."
                )

        exec_id = execution_context.execution_id
        wo_id = work_order.work_order_id
        repo_id = execution_context.repository_id
        tr = dict(trace or {})

        # Step 2: Evidence Aggregation
        combined_evidence: list[VerificationEvidence] = []
        seen_ev_ids: set[str] = set()

        if evidence:
            for e in evidence:
                if e.evidence_id not in seen_ev_ids:
                    combined_evidence.append(e)
                    seen_ev_ids.add(e.evidence_id)

        if repository_verification and repository_verification.evidence:
            for e in repository_verification.evidence:
                if e.evidence_id not in seen_ev_ids:
                    combined_evidence.append(e)
                    seen_ev_ids.add(e.evidence_id)

        if verification_summary and verification_summary.evidence:
            for e in verification_summary.evidence:
                if e.evidence_id not in seen_ev_ids:
                    combined_evidence.append(e)
                    seen_ev_ids.add(e.evidence_id)

        # Step 3: Normalize Tests and Acceptance Results
        tests = list(test_results or [])
        ac_results = list(acceptance_results or [])
        blist = list(blockers or [])
        rlist = list(risks or [])

        # Step 4: Determine Primary Verification Summary
        primary_ver = repository_verification or verification_summary

        # Step 5: Check Scope Violations & Uncommitted Changes
        has_unauthorized = False
        if repository_verification and repository_verification.has_unauthorized_changes:
            has_unauthorized = True
        elif verification_summary and hasattr(verification_summary, "diff_verification"):
            dv = verification_summary.diff_verification
            if hasattr(dv, "has_unauthorized_changes") and dv.has_unauthorized_changes:
                has_unauthorized = True

        has_failing_tests = any(not t.passed for t in tests)
        has_failed_ac = any(a.status == AcceptanceStatus.FAIL for a in ac_results)
        has_unverified_ac = any(a.status == AcceptanceStatus.NOT_VERIFIED for a in ac_results)

        ver_passed = False
        if repository_verification:
            ver_passed = repository_verification.verification_status == VerificationStatus.PASS
        elif verification_summary:
            ver_passed = verification_summary.overall_status in (
                VerificationStatus.PASS,
                VerificationSummaryStatus.VERIFIED,
                "PASS",
                "VERIFIED",
            )
        else:
            ver_passed = not has_failing_tests and not has_failed_ac and not has_unverified_ac

        # Step 6: Derive Recommended Manager Disposition
        if blist:
            recommended = ManagerDisposition.ESCALATE
            rationale = f"Execution has {len(blist)} active blocker(s) requiring Manager intervention: {blist[:2]}."
        elif has_unauthorized:
            recommended = ManagerDisposition.REJECT
            rationale = "Execution modified files outside authorized scope boundaries."
        elif has_failing_tests or has_failed_ac:
            recommended = ManagerDisposition.REJECT
            rationale = "Verification failed due to failing tests or failed acceptance criteria."
        elif has_unverified_ac or not ver_passed:
            recommended = ManagerDisposition.REQUEST_CHANGES
            rationale = "Implementation is partially complete with unverified criteria or pending checks."
        elif not combined_evidence:
            recommended = ManagerDisposition.REQUEST_CHANGES
            rationale = "Implementation claimed complete but lacks authoritative verification evidence."
        else:
            recommended = ManagerDisposition.ACCEPT
            rationale = "Implementation satisfied all acceptance criteria and verification requirements within authorized scope."

        risk_summary_data = {
            "items": rlist,
            "has_unauthorized_changes": has_unauthorized,
            "has_failing_tests": has_failing_tests,
            "total_blockers": len(blist),
        }

        metadata = {
            "project_id": execution_context.project_id,
            "requested_objective": getattr(work_order, "objective", ""),
            "branch": execution_context.branch,
        }

        pkg = DeliveryPackage(
            delivery_id=new_delivery_id(),
            execution_id=exec_id,
            work_order_id=wo_id,
            repository_id=repo_id,
            base_revision=execution_context.base_revision,
            resulting_revision=execution_context.resulting_revision,
            branch_or_reference=execution_context.branch or execution_context.reference,
            change_set=change_set,
            verification_summary=primary_ver,
            acceptance_results=ac_results,
            test_results=tests,
            risk_summary=risk_summary_data,
            blockers=blist,
            evidence=combined_evidence,
            trace=tr,
            recommended_disposition=recommended,
            disposition_rationale=rationale,
            metadata=metadata,
        )
        return pkg
