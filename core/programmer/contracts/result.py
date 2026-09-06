from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional

from core.models import WorkerOutput
from core.programmer.contracts.acceptance_result import AcceptanceCriterionResult
from core.programmer.contracts.command_record import CommandExecutionRecord
from core.programmer.contracts.evidence import ProgrammerEvidence
from core.programmer.contracts.identifiers import (
    new_result_id,
    validate_execution_id,
    validate_result_id,
    validate_work_order_id,
)
from core.programmer.contracts.test_record import TestResultRecord
from core.programmer.errors import ProgrammerLineageError
from core.programmer.types import AcceptanceStatus, ProgrammerResultStatus


def utc_now() -> str:
    """Generate ISO 8601 UTC timestamp."""
    return datetime.now(timezone.utc).isoformat()


@dataclass
class ProgrammerResult:
    """
    Final authoritative result package produced by the Programmer and returned to the Manager.
    Evidence-oriented: details what changed, what was executed, what passed, what failed,
    and what remains uncertain or unverified.
    """
    result_id: str
    execution_id: str
    work_order_id: str
    task_id: str = ""
    project_id: str = ""
    correlation_id: str = ""
    status: ProgrammerResultStatus = ProgrammerResultStatus.COMPLETED
    summary: str = ""
    summary_for_manager: str = ""
    files_changed: list[str] = field(default_factory=list)
    files_created: list[str] = field(default_factory=list)
    files_deleted: list[str] = field(default_factory=list)
    diff_summary: str = ""
    commands_executed: list[CommandExecutionRecord] = field(default_factory=list)
    test_results: list[TestResultRecord] = field(default_factory=list)
    validation_results: list[dict[str, Any]] = field(default_factory=list)
    acceptance_results: list[AcceptanceCriterionResult] = field(default_factory=list)
    blockers: list[str] = field(default_factory=list)
    material_blockers: list[str] = field(default_factory=list)
    escalations: list[str] = field(default_factory=list)
    risks: list[str] = field(default_factory=list)
    artifacts: list[str] = field(default_factory=list)
    evidence: list[ProgrammerEvidence] = field(default_factory=list)
    evidence_ids: list[str] = field(default_factory=list)
    trace: Optional[dict[str, Any]] = None
    timestamps: dict[str, str] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=utc_now)

    def __post_init__(self) -> None:
        validate_result_id(self.result_id)
        validate_execution_id(self.execution_id)
        validate_work_order_id(self.work_order_id)

        # Synchronize summary and summary_for_manager
        if not self.summary and self.summary_for_manager:
            self.summary = self.summary_for_manager
        elif not self.summary_for_manager and self.summary:
            self.summary_for_manager = self.summary

        # Synchronize blockers and material_blockers
        if not self.blockers and self.material_blockers:
            self.blockers = list(self.material_blockers)
        elif not self.material_blockers and self.blockers:
            self.material_blockers = list(self.blockers)

        # Normalize status enum
        if isinstance(self.status, str):
            try:
                self.status = ProgrammerResultStatus(self.status.upper())
            except ValueError:
                pass

        # Normalize commands_executed
        norm_cmds: list[CommandExecutionRecord] = []
        for c in self.commands_executed:
            if isinstance(c, CommandExecutionRecord):
                norm_cmds.append(c)
            elif isinstance(c, dict):
                norm_cmds.append(CommandExecutionRecord.from_dict(c))
        self.commands_executed = norm_cmds

        # Normalize test_results
        norm_tests: list[TestResultRecord] = []
        for t in self.test_results:
            if isinstance(t, TestResultRecord):
                norm_tests.append(t)
            elif isinstance(t, dict):
                norm_tests.append(TestResultRecord.from_dict(t))
        self.test_results = norm_tests

        # Normalize acceptance_results
        norm_ac: list[AcceptanceCriterionResult] = []
        for a in self.acceptance_results:
            if isinstance(a, AcceptanceCriterionResult):
                norm_ac.append(a)
            elif isinstance(a, dict):
                norm_ac.append(AcceptanceCriterionResult.from_dict(a))
        self.acceptance_results = norm_ac

        # Normalize evidence
        norm_ev: list[ProgrammerEvidence] = []
        for e in self.evidence:
            if isinstance(e, ProgrammerEvidence):
                norm_ev.append(e)
            elif isinstance(e, dict):
                norm_ev.append(ProgrammerEvidence.from_dict(e))
        self.evidence = norm_ev

        # Collect evidence IDs
        collected_ev_ids = set(self.evidence_ids)
        for ev in self.evidence:
            collected_ev_ids.add(ev.evidence_id)
        self.evidence_ids = sorted(list(collected_ev_ids))

        # Setup timestamps
        if not self.timestamps:
            self.timestamps = {"created_at": self.created_at}
        elif "created_at" not in self.timestamps:
            self.timestamps["created_at"] = self.created_at

    def validate_lineage(self, execution: Any = None, work_order: Any = None) -> None:
        """
        Verify that this result strictly matches the lineage of the given execution and work order.
        Raises ProgrammerLineageError if any identifier fails to match.
        """
        if execution is not None:
            if self.execution_id != execution.execution_id:
                raise ProgrammerLineageError(
                    f"Result execution_id '{self.execution_id}' does not match execution.execution_id '{execution.execution_id}'"
                )
            if self.work_order_id != execution.work_order_id:
                raise ProgrammerLineageError(
                    f"Result work_order_id '{self.work_order_id}' does not match execution.work_order_id '{execution.work_order_id}'"
                )
            if self.task_id and execution.task_id and self.task_id != execution.task_id:
                raise ProgrammerLineageError(
                    f"Result task_id '{self.task_id}' does not match execution.task_id '{execution.task_id}'"
                )
            if self.project_id and execution.project_id and self.project_id != execution.project_id:
                raise ProgrammerLineageError(
                    f"Result project_id '{self.project_id}' does not match execution.project_id '{execution.project_id}'"
                )
            if self.correlation_id and execution.correlation_id and self.correlation_id != execution.correlation_id:
                raise ProgrammerLineageError(
                    f"Result correlation_id '{self.correlation_id}' does not match execution.correlation_id '{execution.correlation_id}'"
                )

        if work_order is not None:
            if self.work_order_id != work_order.work_order_id:
                raise ProgrammerLineageError(
                    f"Result work_order_id '{self.work_order_id}' does not match work_order.work_order_id '{work_order.work_order_id}'"
                )
            wo_task_id = getattr(work_order, "manager_task_id", None) or getattr(work_order, "task_id", None)
            if self.task_id and wo_task_id and self.task_id != wo_task_id:
                raise ProgrammerLineageError(
                    f"Result task_id '{self.task_id}' does not match work_order task_id '{wo_task_id}'"
                )
            if self.project_id and work_order.project_id and self.project_id != work_order.project_id:
                raise ProgrammerLineageError(
                    f"Result project_id '{self.project_id}' does not match work_order.project_id '{work_order.project_id}'"
                )
            if self.correlation_id and work_order.correlation_id and self.correlation_id != work_order.correlation_id:
                raise ProgrammerLineageError(
                    f"Result correlation_id '{self.correlation_id}' does not match work_order.correlation_id '{work_order.correlation_id}'"
                )

    def is_success(self) -> bool:
        """Returns True if the result represents an operational success or completion."""
        return self.status in {ProgrammerResultStatus.COMPLETED, ProgrammerResultStatus.SUCCESS}

    def is_partial(self) -> bool:
        """Returns True if the result represents partial completion."""
        return self.status in {ProgrammerResultStatus.PARTIAL, ProgrammerResultStatus.PARTIALLY_COMPLETED}

    def is_blocked(self) -> bool:
        """Returns True if execution was blocked by an external or environmental blocker."""
        return self.status == ProgrammerResultStatus.BLOCKED

    def is_failed(self) -> bool:
        """Returns True if execution encountered an unrecoverable failure or regression."""
        return self.status == ProgrammerResultStatus.FAILED

    def is_acceptance_fully_verified(self) -> bool:
        """
        Returns True if and only if there is at least one acceptance criterion result
        and ALL criteria results have status PASS.
        """
        if not self.acceptance_results:
            return False
        return all(ac.status == AcceptanceStatus.PASS for ac in self.acceptance_results)

    def to_worker_output(self) -> WorkerOutput:
        """Convert ProgrammerResult to standard AutonomOS WorkerOutput."""
        meta = dict(self.metadata)
        meta.update({
            "result_id": self.result_id,
            "execution_id": self.execution_id,
            "work_order_id": self.work_order_id,
            "task_id": self.task_id,
            "project_id": self.project_id,
            "correlation_id": self.correlation_id,
            "programmer_status": self.status.value,
            "evidence_ids": list(self.evidence_ids),
            "files_changed": list(self.files_changed),
            "files_created": list(self.files_created),
            "files_deleted": list(self.files_deleted),
            "diff_summary": self.diff_summary,
            "commands_count": len(self.commands_executed),
            "tests_count": len(self.test_results),
            "acceptance_count": len(self.acceptance_results),
            "acceptance_fully_verified": self.is_acceptance_fully_verified(),
            "blockers": list(self.blockers or self.material_blockers),
            "material_blockers": list(self.material_blockers or self.blockers),
            "escalations": list(self.escalations),
            "risks": list(self.risks),
            "artifacts": list(self.artifacts),
            "timestamps": dict(self.timestamps),
        })

        err_msg = "; ".join(self.blockers or self.material_blockers) if (self.blockers or self.material_blockers) else None

        return WorkerOutput(
            success=self.is_success(),
            summary=self.summary,
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
            "status": self.status.value if isinstance(self.status, ProgrammerResultStatus) else str(self.status),
            "summary": self.summary,
            "summary_for_manager": self.summary_for_manager,
            "files_changed": list(self.files_changed),
            "files_created": list(self.files_created),
            "files_deleted": list(self.files_deleted),
            "diff_summary": self.diff_summary,
            "commands_executed": [c.to_dict() for c in self.commands_executed],
            "test_results": [t.to_dict() for t in self.test_results],
            "validation_results": list(self.validation_results),
            "acceptance_results": [a.to_dict() for a in self.acceptance_results],
            "blockers": list(self.blockers),
            "material_blockers": list(self.material_blockers),
            "escalations": list(self.escalations),
            "risks": list(self.risks),
            "artifacts": list(self.artifacts),
            "evidence": [e.to_dict() for e in self.evidence],
            "evidence_ids": list(self.evidence_ids),
            "trace": self.trace,
            "timestamps": dict(self.timestamps),
            "metadata": dict(self.metadata),
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ProgrammerResult:
        st_raw = data.get("status", ProgrammerResultStatus.COMPLETED.value)
        try:
            status = ProgrammerResultStatus(st_raw)
        except (ValueError, TypeError):
            status = ProgrammerResultStatus.COMPLETED

        commands = [
            CommandExecutionRecord.from_dict(c) if isinstance(c, dict) else c
            for c in data.get("commands_executed", [])
        ]
        test_results = [
            TestResultRecord.from_dict(t) if isinstance(t, dict) else t
            for t in data.get("test_results", [])
        ]
        acceptance_results = [
            AcceptanceCriterionResult.from_dict(a) if isinstance(a, dict) else a
            for a in data.get("acceptance_results", [])
        ]
        evidence = [
            ProgrammerEvidence.from_dict(e) if isinstance(e, dict) else e
            for e in data.get("evidence", [])
        ]

        summary = str(data.get("summary") or data.get("summary_for_manager", ""))
        blockers = list(data.get("blockers") or data.get("material_blockers", []))

        return cls(
            result_id=str(data.get("result_id", new_result_id())),
            execution_id=str(data.get("execution_id", "")),
            work_order_id=str(data.get("work_order_id", "")),
            task_id=str(data.get("task_id", "")),
            project_id=str(data.get("project_id", "")),
            correlation_id=str(data.get("correlation_id", "")),
            status=status,
            summary=summary,
            summary_for_manager=summary,
            files_changed=list(data.get("files_changed", [])),
            files_created=list(data.get("files_created", [])),
            files_deleted=list(data.get("files_deleted", [])),
            diff_summary=str(data.get("diff_summary", "")),
            commands_executed=commands,
            test_results=test_results,
            validation_results=list(data.get("validation_results", [])),
            acceptance_results=acceptance_results,
            blockers=blockers,
            material_blockers=blockers,
            escalations=list(data.get("escalations", [])),
            risks=list(data.get("risks", [])),
            artifacts=list(data.get("artifacts", [])),
            evidence=evidence,
            evidence_ids=list(data.get("evidence_ids", [])),
            trace=data.get("trace"),
            timestamps=dict(data.get("timestamps", {})),
            metadata=dict(data.get("metadata", {})),
            created_at=str(data.get("created_at", utc_now())),
        )
