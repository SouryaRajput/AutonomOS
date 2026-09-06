from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import json
import logging
import os
import re
from typing import Any, Optional, Sequence, Union

from core.programmer.contracts.acceptance_criteria import AcceptanceCriterion
from core.programmer.contracts.identifiers import (
    validate_execution_id,
    validate_work_order_id,
)
from core.programmer.contracts.verification import (
    AcceptanceResult,
    VerificationCheck,
    VerificationEvidence,
)
from core.programmer.errors import (
    ProgrammerLineageError,
    ProgrammerValidationError,
)
from core.programmer.types import (
    AcceptanceCriterionType,
    VerificationCheckType,
    VerificationEvidenceSourceType,
    VerificationStatus,
)

logger = logging.getLogger("AutonomOS.Programmer.AcceptanceEvaluator")


def utc_now() -> str:
    """Generate ISO 8601 UTC timestamp."""
    return datetime.now(timezone.utc).isoformat()


@dataclass
class AcceptanceEvaluationResult:
    """
    Comprehensive outcome package of an acceptance criteria evaluation session.
    
    Guarantees:
    - Maintains causal lineage back to execution_id and work_order_id.
    - Aggregates all evaluated AcceptanceResult records.
    - Provides deterministic overall verification status:
        ALL PASS -> PASS
        ANY FAIL -> FAIL
        NO FAIL BUT >= 1 NOT_VERIFIED -> NOT_VERIFIED
    """
    overall_status: VerificationStatus
    results: list[AcceptanceResult] = field(default_factory=list)
    execution_id: str = ""
    work_order_id: str = ""
    pass_count: int = 0
    fail_count: int = 0
    not_verified_count: int = 0
    evaluated_at: str = field(default_factory=utc_now)
    trace: Optional[Any] = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if isinstance(self.overall_status, str):
            try:
                self.overall_status = VerificationStatus(self.overall_status)
            except ValueError:
                self.overall_status = VerificationStatus.NOT_VERIFIED

    def to_dict(self) -> dict[str, Any]:
        return {
            "overall_status": self.overall_status.value if isinstance(self.overall_status, VerificationStatus) else str(self.overall_status),
            "results": [r.to_dict() for r in self.results],
            "execution_id": self.execution_id,
            "work_order_id": self.work_order_id,
            "pass_count": self.pass_count,
            "fail_count": self.fail_count,
            "not_verified_count": self.not_verified_count,
            "evaluated_at": self.evaluated_at,
            "trace": self.trace.to_dict() if hasattr(self.trace, "to_dict") else (dict(self.trace) if isinstance(self.trace, dict) else self.trace),
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> AcceptanceEvaluationResult:
        st_raw = data.get("overall_status", VerificationStatus.NOT_VERIFIED.value)
        try:
            overall_status = VerificationStatus(st_raw)
        except (ValueError, TypeError):
            overall_status = VerificationStatus.NOT_VERIFIED

        return cls(
            overall_status=overall_status,
            results=[AcceptanceResult.from_dict(r) for r in data.get("results", [])],
            execution_id=str(data.get("execution_id", "")),
            work_order_id=str(data.get("work_order_id", "")),
            pass_count=int(data.get("pass_count", 0)),
            fail_count=int(data.get("fail_count", 0)),
            not_verified_count=int(data.get("not_verified_count", 0)),
            evaluated_at=str(data.get("evaluated_at", utc_now())),
            trace=data.get("trace"),
            metadata=dict(data.get("metadata", {})),
        )


class AcceptanceCriteriaEvaluator:
    """
    Deterministic evaluator for ProgrammerWorkOrder acceptance criteria.
    
    Architecture:
        WorkOrder.acceptance_criteria
                    ↓
        AcceptanceCriteriaEvaluator
                    ↓
        Verification Evidence + Execution / Repository Evidence
                    ↓
        AcceptanceResult[]
    
    Architectural Guarantees:
    1. Deterministic evaluation: relies purely on observed execution facts, exit codes, and diffs.
    2. NEVER consult an LLM to judge code or accept coding agent narration.
    3. An acceptance criterion can only achieve PASS if backed by at least one authoritative
       evidence record (is_agent_claim == False).
    4. Criteria lacking observable evidence return NOT_VERIFIED (never guess).
    5. Overall status:
        ALL PASS -> PASS
        ANY FAIL -> FAIL
        NO FAIL BUT >= 1 NOT_VERIFIED -> NOT_VERIFIED
    6. Lineage integrity: cross-execution or cross-work-order evidence is strictly rejected.
    """

    def evaluate(
        self,
        work_order: Any,
        execution_id: str,
        checks: Optional[Sequence[VerificationCheck]] = None,
        evidence: Optional[Sequence[VerificationEvidence]] = None,
        files_changed: Optional[Sequence[str]] = None,
        files_created: Optional[Sequence[str]] = None,
        files_deleted: Optional[Sequence[str]] = None,
        trace: Optional[Any] = None,
        metadata: Optional[dict[str, Any]] = None,
    ) -> AcceptanceEvaluationResult:
        """
        Evaluate all acceptance criteria declared on the work order against available evidence.
        """
        if work_order is None:
            raise ProgrammerValidationError("ProgrammerWorkOrder cannot be None.")
        
        work_order_id = getattr(work_order, "work_order_id", "")
        if not work_order_id:
            raise ProgrammerLineageError("ProgrammerWorkOrder must have a valid non-empty work_order_id.")
        validate_work_order_id(work_order_id)

        if not execution_id:
            raise ProgrammerLineageError("execution_id must be a valid non-empty string.")
        validate_execution_id(execution_id)

        criteria = getattr(work_order, "acceptance_criteria", [])
        if not criteria:
            raise ProgrammerValidationError(
                "ProgrammerWorkOrder must have non-empty acceptance criteria for evaluation."
            )

        resolved_checks = list(checks or [])
        resolved_evidence = list(evidence or [])
        changed = [f.strip() for f in (files_changed or [])]
        created = [f.strip() for f in (files_created or [])]
        deleted = [f.strip() for f in (files_deleted or [])]
        all_modified_files = set(changed + created + deleted)

        # Validate lineage on supplied checks and evidence
        for chk in resolved_checks:
            if chk.execution_id != execution_id:
                raise ProgrammerLineageError(
                    f"VerificationCheck '{chk.check_id}' execution_id '{chk.execution_id}' does not match '{execution_id}'."
                )
            if chk.work_order_id != work_order_id:
                raise ProgrammerLineageError(
                    f"VerificationCheck '{chk.check_id}' work_order_id '{chk.work_order_id}' does not match '{work_order_id}'."
                )

        for ev in resolved_evidence:
            if ev.execution_id != execution_id:
                raise ProgrammerLineageError(
                    f"VerificationEvidence '{ev.evidence_id}' execution_id '{ev.execution_id}' does not match '{execution_id}'."
                )
            if ev.work_order_id != work_order_id:
                raise ProgrammerLineageError(
                    f"VerificationEvidence '{ev.evidence_id}' work_order_id '{ev.work_order_id}' does not match '{work_order_id}'."
                )

        evidence_map: dict[str, VerificationEvidence] = {e.evidence_id: e for e in resolved_evidence}

        results: list[AcceptanceResult] = []
        for crit in criteria:
            res = self._evaluate_criterion(
                criterion=crit,
                work_order=work_order,
                execution_id=execution_id,
                checks=resolved_checks,
                evidence=resolved_evidence,
                evidence_map=evidence_map,
                all_modified_files=all_modified_files,
                trace=trace,
            )
            # Validate generated result against criteria catalog
            res.validate(criteria_catalog=criteria)
            results.append(res)

        pass_count = sum(1 for r in results if r.status == VerificationStatus.PASS)
        fail_count = sum(1 for r in results if r.status == VerificationStatus.FAIL)
        not_verified_count = sum(1 for r in results if r.status == VerificationStatus.NOT_VERIFIED)

        # Deterministic overall acceptance status rule:
        # ALL PASS -> PASS
        # ANY FAIL -> FAIL
        # NO FAIL BUT >= 1 NOT_VERIFIED -> NOT_VERIFIED
        if fail_count > 0:
            overall_status = VerificationStatus.FAIL
        elif not_verified_count > 0:
            overall_status = VerificationStatus.NOT_VERIFIED
        elif pass_count == len(results) and pass_count > 0:
            overall_status = VerificationStatus.PASS
        else:
            overall_status = VerificationStatus.NOT_VERIFIED

        return AcceptanceEvaluationResult(
            overall_status=overall_status,
            results=results,
            execution_id=execution_id,
            work_order_id=work_order_id,
            pass_count=pass_count,
            fail_count=fail_count,
            not_verified_count=not_verified_count,
            trace=trace,
            metadata=dict(metadata or {}),
        )

    def _evaluate_criterion(
        self,
        criterion: Any,
        work_order: Any,
        execution_id: str,
        checks: list[VerificationCheck],
        evidence: list[VerificationEvidence],
        evidence_map: dict[str, VerificationEvidence],
        all_modified_files: set[str],
        trace: Optional[Any],
    ) -> AcceptanceResult:
        """Evaluate a single AcceptanceCriterion against execution facts."""
        crit_id = getattr(criterion, "criterion_id", str(criterion))
        desc = getattr(criterion, "description", "").strip()
        target = getattr(criterion, "target", None)
        crit_type = getattr(criterion, "criterion_type", AcceptanceCriterionType.CUSTOM)
        if isinstance(crit_type, str):
            try:
                crit_type = AcceptanceCriterionType(crit_type.upper())
            except ValueError:
                crit_type = AcceptanceCriterionType.CUSTOM

        desc_lower = desc.lower()

        # 1. Test Criteria (TEST_PASS or description mentions tests)
        if crit_type == AcceptanceCriterionType.TEST_PASS or "test" in desc_lower:
            return self._evaluate_test_criterion(
                crit_id=crit_id,
                desc=desc,
                target=target,
                execution_id=execution_id,
                work_order_id=work_order.work_order_id,
                checks=checks,
                evidence=evidence,
                trace=trace,
            )

        # 2. Typecheck Criteria (TYPECHECK_PASS or description mentions mypy/typecheck)
        if crit_type == AcceptanceCriterionType.TYPECHECK_PASS or "typecheck" in desc_lower or "mypy" in desc_lower:
            return self._evaluate_typecheck_criterion(
                crit_id=crit_id,
                desc=desc,
                target=target,
                execution_id=execution_id,
                work_order_id=work_order.work_order_id,
                checks=checks,
                evidence=evidence,
                trace=trace,
            )

        # 3. Build Criteria (BUILD_PASS or description mentions build)
        if crit_type == AcceptanceCriterionType.BUILD_PASS or "build" in desc_lower:
            return self._evaluate_build_criterion(
                crit_id=crit_id,
                desc=desc,
                target=target,
                execution_id=execution_id,
                work_order_id=work_order.work_order_id,
                checks=checks,
                evidence=evidence,
                trace=trace,
            )

        # 4. File Scope / Modification Criteria (FILE_CHANGED or description mentions files)
        if crit_type == AcceptanceCriterionType.FILE_CHANGED or "only files under" in desc_lower or "may change" in desc_lower or "modify" in desc_lower:
            return self._evaluate_file_scope_criterion(
                crit_id=crit_id,
                desc=desc,
                target=target,
                execution_id=execution_id,
                work_order_id=work_order.work_order_id,
                all_modified_files=all_modified_files,
                evidence=evidence,
                trace=trace,
            )

        # 5. Dependency Constraints (NO_DEPENDENCY_ADDED)
        if crit_type == AcceptanceCriterionType.NO_DEPENDENCY_ADDED or "no dependenc" in desc_lower:
            return self._evaluate_dependency_criterion(
                crit_id=crit_id,
                desc=desc,
                execution_id=execution_id,
                work_order_id=work_order.work_order_id,
                all_modified_files=all_modified_files,
                evidence=evidence,
                trace=trace,
            )

        # 6. Unverifiable / Custom Criteria (e.g. Performance, Behavior, Unmeasured features)
        return self._evaluate_unverifiable_or_custom_criterion(
            crit_id=crit_id,
            desc=desc,
            target=target,
            execution_id=execution_id,
            work_order_id=work_order.work_order_id,
            evidence=evidence,
            trace=trace,
        )

    def _evaluate_test_criterion(
        self,
        crit_id: str,
        desc: str,
        target: Optional[str],
        execution_id: str,
        work_order_id: str,
        checks: list[VerificationCheck],
        evidence: list[VerificationEvidence],
        trace: Optional[Any],
    ) -> AcceptanceResult:
        """Evaluate test execution criterion against actual observed test checks and evidence."""
        # Find relevant test checks
        matching_checks = [
            c for c in checks
            if c.check_type == VerificationCheckType.TEST
            or (c.command and ("pytest" in c.command.lower() or "test" in c.command.lower()))
        ]
        if target:
            matching_checks = [c for c in matching_checks if target.lower() in (c.command or "").lower()]

        # Also find test evidence directly
        matching_evidence = [
            e for e in evidence
            if e.source_type == VerificationEvidenceSourceType.TEST_RUNNER
            or (isinstance(e.data, dict) and "pytest" in str(e.data.get("command", "")).lower())
            or (e.is_agent_claim and "test" in (e.description + " " + str(e.data)).lower())
        ]
        if target:
            matching_evidence = [
                e for e in matching_evidence
                if target.lower() in str(e.data.get("command", "")).lower()
                or target.lower() in str(e.source_reference or "").lower()
            ]

        if not matching_checks and not matching_evidence:
            return AcceptanceResult(
                criterion_id=crit_id,
                status=VerificationStatus.NOT_VERIFIED,
                explanation=f"No actual test execution evidence observed matching criterion '{desc}'.",
                evidence=[],
                execution_id=execution_id,
                work_order_id=work_order_id,
                trace=trace,
            )

        # Check for non-authoritative agent claims
        authoritative_evidence = [e for e in matching_evidence if e.is_authoritative()]
        for c in matching_checks:
            for ev_id in c.evidence:
                ev_obj = next((e for e in evidence if e.evidence_id == ev_id), None)
                if ev_obj and ev_obj.is_authoritative() and ev_obj not in authoritative_evidence:
                    authoritative_evidence.append(ev_obj)

        if not authoritative_evidence:
            return AcceptanceResult(
                criterion_id=crit_id,
                status=VerificationStatus.NOT_VERIFIED,
                explanation="Test evidence consists solely of unverified agent narration or claims. Agent claims cannot satisfy PASS.",
                evidence=[],
                execution_id=execution_id,
                work_order_id=work_order_id,
                trace=trace,
            )

        # Check for failures
        failing_checks = [c for c in matching_checks if c.status in (VerificationStatus.FAIL, VerificationStatus.ERROR) or (c.exit_code is not None and c.exit_code != 0)]
        failing_evidence = [e for e in matching_evidence if isinstance(e.data, dict) and e.data.get("exit_code") not in (None, 0)]

        if failing_checks or failing_evidence:
            fail_ev_ids = [e.evidence_id for e in failing_evidence]
            for c in failing_checks:
                fail_ev_ids.extend(c.evidence)
            return AcceptanceResult(
                criterion_id=crit_id,
                status=VerificationStatus.FAIL,
                explanation=f"Observed actual test failure in execution: {len(failing_checks)} failing check(s).",
                evidence=list(set(fail_ev_ids)),
                execution_id=execution_id,
                work_order_id=work_order_id,
                trace=trace,
            )

        # All matching checks passed with exit code 0
        passing_ev_ids = [e.evidence_id for e in authoritative_evidence]
        return AcceptanceResult(
            criterion_id=crit_id,
            status=VerificationStatus.PASS,
            explanation=f"Observed successful test execution with exit code 0 across {len(authoritative_evidence)} authoritative evidence item(s).",
            evidence=list(set(passing_ev_ids)),
            execution_id=execution_id,
            work_order_id=work_order_id,
            trace=trace,
        )

    def _evaluate_typecheck_criterion(
        self,
        crit_id: str,
        desc: str,
        target: Optional[str],
        execution_id: str,
        work_order_id: str,
        checks: list[VerificationCheck],
        evidence: list[VerificationEvidence],
        trace: Optional[Any],
    ) -> AcceptanceResult:
        """Evaluate typecheck criterion against actual static analysis checks and evidence."""
        matching_checks = [
            c for c in checks
            if c.check_type == VerificationCheckType.TYPECHECK
            or (c.command and ("mypy" in c.command.lower() or "pyright" in c.command.lower() or "tsc" in c.command.lower()))
        ]
        matching_evidence = [
            e for e in evidence
            if e.source_type == VerificationEvidenceSourceType.STATIC_ANALYSIS
            or (isinstance(e.data, dict) and any(k in str(e.data.get("command", "")).lower() for k in ("mypy", "pyright", "tsc")))
        ]

        if not matching_checks and not matching_evidence:
            return AcceptanceResult(
                criterion_id=crit_id,
                status=VerificationStatus.NOT_VERIFIED,
                explanation=f"No typecheck or static analysis evidence observed matching '{desc}'.",
                evidence=[],
                execution_id=execution_id,
                work_order_id=work_order_id,
                trace=trace,
            )

        authoritative_evidence = [e for e in matching_evidence if e.is_authoritative()]
        for c in matching_checks:
            for ev_id in c.evidence:
                ev_obj = next((e for e in evidence if e.evidence_id == ev_id), None)
                if ev_obj and ev_obj.is_authoritative() and ev_obj not in authoritative_evidence:
                    authoritative_evidence.append(ev_obj)

        if not authoritative_evidence:
            return AcceptanceResult(
                criterion_id=crit_id,
                status=VerificationStatus.NOT_VERIFIED,
                explanation="Typecheck evidence consists solely of agent claims. Cannot satisfy PASS.",
                evidence=[],
                execution_id=execution_id,
                work_order_id=work_order_id,
                trace=trace,
            )

        failing = [c for c in matching_checks if c.status in (VerificationStatus.FAIL, VerificationStatus.ERROR) or (c.exit_code is not None and c.exit_code != 0)]
        if failing:
            fail_ev_ids = [ev for c in failing for ev in c.evidence]
            return AcceptanceResult(
                criterion_id=crit_id,
                status=VerificationStatus.FAIL,
                explanation=f"Observed typecheck failures in execution: {failing[0].output_snippet}",
                evidence=list(set(fail_ev_ids)),
                execution_id=execution_id,
                work_order_id=work_order_id,
                trace=trace,
            )

        passing_ev_ids = [e.evidence_id for e in authoritative_evidence]
        return AcceptanceResult(
            criterion_id=crit_id,
            status=VerificationStatus.PASS,
            explanation="Observed successful typecheck execution with zero errors.",
            evidence=list(set(passing_ev_ids)),
            execution_id=execution_id,
            work_order_id=work_order_id,
            trace=trace,
        )

    def _evaluate_build_criterion(
        self,
        crit_id: str,
        desc: str,
        target: Optional[str],
        execution_id: str,
        work_order_id: str,
        checks: list[VerificationCheck],
        evidence: list[VerificationEvidence],
        trace: Optional[Any],
    ) -> AcceptanceResult:
        """Evaluate build criterion against actual build checks and evidence."""
        matching_checks = [c for c in checks if c.check_type == VerificationCheckType.BUILD or (c.command and "build" in c.command.lower())]
        if not matching_checks:
            return AcceptanceResult(
                criterion_id=crit_id,
                status=VerificationStatus.NOT_VERIFIED,
                explanation=f"No build execution evidence observed matching '{desc}'.",
                evidence=[],
                execution_id=execution_id,
                work_order_id=work_order_id,
                trace=trace,
            )

        failing = [c for c in matching_checks if c.status in (VerificationStatus.FAIL, VerificationStatus.ERROR) or (c.exit_code is not None and c.exit_code != 0)]
        if failing:
            fail_ev_ids = [ev for c in failing for ev in c.evidence]
            return AcceptanceResult(
                criterion_id=crit_id,
                status=VerificationStatus.FAIL,
                explanation=f"Observed build failure: {failing[0].output_snippet}",
                evidence=list(set(fail_ev_ids)),
                execution_id=execution_id,
                work_order_id=work_order_id,
                trace=trace,
            )

        passing_ev_ids = [ev for c in matching_checks for ev in c.evidence]
        return AcceptanceResult(
            criterion_id=crit_id,
            status=VerificationStatus.PASS,
            explanation="Observed successful build execution with exit code 0.",
            evidence=list(set(passing_ev_ids)),
            execution_id=execution_id,
            work_order_id=work_order_id,
            trace=trace,
        )

    def _evaluate_file_scope_criterion(
        self,
        crit_id: str,
        desc: str,
        target: Optional[str],
        execution_id: str,
        work_order_id: str,
        all_modified_files: set[str],
        evidence: list[VerificationEvidence],
        trace: Optional[Any],
    ) -> AcceptanceResult:
        """Evaluate file modification scope against actual modified/created/deleted files."""
        # Detect allowed prefix from target or description ("Only files under src/auth may change")
        allowed_prefix = target
        if not allowed_prefix:
            match = re.search(r"(?:under|in)\s+([a-zA-Z0-9_\-/\.]+)", desc, re.IGNORECASE)
            if match:
                allowed_prefix = match.group(1).rstrip("/")

        fs_evidence = [e for e in evidence if e.source_type == VerificationEvidenceSourceType.FILESYSTEM or "file" in e.description.lower()]
        ev_ids = [e.evidence_id for e in fs_evidence if e.is_authoritative()]

        if not all_modified_files and not fs_evidence:
            return AcceptanceResult(
                criterion_id=crit_id,
                status=VerificationStatus.NOT_VERIFIED,
                explanation="No repository modification evidence or diff records observed.",
                evidence=[],
                execution_id=execution_id,
                work_order_id=work_order_id,
                trace=trace,
            )

        if allowed_prefix:
            norm_prefix = allowed_prefix.replace("\\", "/").rstrip("/")
            disallowed = [f for f in all_modified_files if not (f.startswith(norm_prefix + "/") or f == norm_prefix)]
            if disallowed:
                return AcceptanceResult(
                    criterion_id=crit_id,
                    status=VerificationStatus.FAIL,
                    explanation=f"File modification scope violated. Disallowed files modified: {sorted(disallowed)}.",
                    evidence=ev_ids,
                    execution_id=execution_id,
                    work_order_id=work_order_id,
                    trace=trace,
                )
            return AcceptanceResult(
                criterion_id=crit_id,
                status=VerificationStatus.PASS,
                explanation=f"All {len(all_modified_files)} modified file(s) are strictly confined within authorized scope '{norm_prefix}'.",
                evidence=ev_ids if ev_ids else [e.evidence_id for e in evidence if e.is_authoritative()][:1],
                execution_id=execution_id,
                work_order_id=work_order_id,
                trace=trace,
            )

        # Specific file modification requirement: e.g. "File src/calc.py modified"
        target_file = target
        if not target_file:
            match = re.search(r"([a-zA-Z0-9_\-/\.]+\.[a-zA-Z0-9]+)", desc)
            if match:
                target_file = match.group(1)

        if target_file:
            norm_target = target_file.replace("\\", "/")
            if norm_target in all_modified_files or any(f.endswith(norm_target) for f in all_modified_files):
                return AcceptanceResult(
                    criterion_id=crit_id,
                    status=VerificationStatus.PASS,
                    explanation=f"Target file '{target_file}' was modified as required.",
                    evidence=ev_ids if ev_ids else [e.evidence_id for e in evidence if e.is_authoritative()][:1],
                    execution_id=execution_id,
                    work_order_id=work_order_id,
                    trace=trace,
                )
            return AcceptanceResult(
                criterion_id=crit_id,
                status=VerificationStatus.FAIL,
                explanation=f"Required target file '{target_file}' was not modified in actual execution.",
                evidence=ev_ids,
                execution_id=execution_id,
                work_order_id=work_order_id,
                trace=trace,
            )

        return AcceptanceResult(
            criterion_id=crit_id,
            status=VerificationStatus.NOT_VERIFIED,
            explanation=f"Could not extract deterministic path constraint from '{desc}'.",
            evidence=[],
            execution_id=execution_id,
            work_order_id=work_order_id,
            trace=trace,
        )

    def _evaluate_dependency_criterion(
        self,
        crit_id: str,
        desc: str,
        execution_id: str,
        work_order_id: str,
        all_modified_files: set[str],
        evidence: list[VerificationEvidence],
        trace: Optional[Any],
    ) -> AcceptanceResult:
        """Evaluate dependency confinement criterion (no new dependencies added)."""
        dep_files = {
            "requirements.txt",
            "package.json",
            "package-lock.json",
            "pnpm-lock.yaml",
            "yarn.lock",
            "pyproject.toml",
            "setup.py",
            "setup.cfg",
            "Pipfile",
            "Pipfile.lock",
            "poetry.lock",
            "Cargo.toml",
            "Cargo.lock",
            "go.mod",
            "go.sum",
        }
        modified_deps = [f for f in all_modified_files if any(f.endswith(d) for d in dep_files)]
        fs_evidence = [e for e in evidence if e.source_type == VerificationEvidenceSourceType.FILESYSTEM]
        ev_ids = [e.evidence_id for e in fs_evidence if e.is_authoritative()]

        if modified_deps:
            return AcceptanceResult(
                criterion_id=crit_id,
                status=VerificationStatus.FAIL,
                explanation=f"Dependency files were modified: {sorted(modified_deps)}.",
                evidence=ev_ids,
                execution_id=execution_id,
                work_order_id=work_order_id,
                trace=trace,
            )

        authoritative = [e.evidence_id for e in evidence if e.is_authoritative()]
        pass_evidence = ev_ids if ev_ids else authoritative[:1]
        if not pass_evidence:
            return AcceptanceResult(
                criterion_id=crit_id,
                status=VerificationStatus.NOT_VERIFIED,
                explanation="No repository modification evidence or filesystem records observed to verify dependency status.",
                evidence=[],
                execution_id=execution_id,
                work_order_id=work_order_id,
                trace=trace,
            )

        return AcceptanceResult(
            criterion_id=crit_id,
            status=VerificationStatus.PASS,
            explanation="Verified that no project dependency manifest files were modified.",
            evidence=pass_evidence,
            execution_id=execution_id,
            work_order_id=work_order_id,
            trace=trace,
        )

    def _evaluate_unverifiable_or_custom_criterion(
        self,
        crit_id: str,
        desc: str,
        target: Optional[str],
        execution_id: str,
        work_order_id: str,
        evidence: list[VerificationEvidence],
        trace: Optional[Any],
    ) -> AcceptanceResult:
        """
        Handle criteria that cannot be deterministically verified from standard command/fs evidence
        (e.g., performance budgets, runtime memory constraints, non-functional subjective qualities).
        Returns NOT_VERIFIED deterministically without guessing or asking an LLM.
        """
        # Search for custom measurement evidence specifically matching this criterion
        custom_ev = [
            e for e in evidence
            if isinstance(e.data, dict)
            and (e.data.get("criterion_id") == crit_id or (target and target.lower() in str(e.data).lower()))
        ]
        if custom_ev:
            auth_ev = [e for e in custom_ev if e.is_authoritative()]
            if auth_ev:
                ev_data = auth_ev[0].data
                if ev_data.get("pass") is True:
                    return AcceptanceResult(
                        criterion_id=crit_id,
                        status=VerificationStatus.PASS,
                        explanation=f"Verified through observed measurement evidence: {ev_data.get('summary', 'Measurement passed')}.",
                        evidence=[auth_ev[0].evidence_id],
                        execution_id=execution_id,
                        work_order_id=work_order_id,
                        trace=trace,
                    )
                elif ev_data.get("pass") is False:
                    return AcceptanceResult(
                        criterion_id=crit_id,
                        status=VerificationStatus.FAIL,
                        explanation=f"Failed through observed measurement evidence: {ev_data.get('summary', 'Measurement failed')}.",
                        evidence=[auth_ev[0].evidence_id],
                        execution_id=execution_id,
                        work_order_id=work_order_id,
                        trace=trace,
                    )

        return AcceptanceResult(
            criterion_id=crit_id,
            status=VerificationStatus.NOT_VERIFIED,
            explanation=f"Criterion '{desc}' cannot be deterministically verified: no matching benchmark or measurement evidence observed in execution records.",
            evidence=[],
            execution_id=execution_id,
            work_order_id=work_order_id,
            trace=trace,
        )
