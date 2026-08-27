from __future__ import annotations

import logging
from pathlib import Path
import time
from typing import TYPE_CHECKING, Any, Callable, Optional
import uuid

from core.enums import ArtifactType
from core.events.model import Event
from core.events.types import EventSource, EventType
from core.models import Project, Task
from core.verification.adapters.artifact_adapter import ArtifactCheckAdapter
from core.verification.adapters.base import BaseCheckAdapter, CheckExecutionContext
from core.verification.adapters.command_adapter import CommandCheckAdapter
from core.verification.adapters.file_adapter import FileCheckAdapter
from core.verification.adapters.git_adapter import GitCheckAdapter
from core.verification.model import (
    SuccessCriterion,
    Verification,
    VerificationCheck,
    VerificationPlan,
    VerificationResult,
    utc_now,
)
from core.verification.types import CheckStatus, CheckType, VerificationStatus

if TYPE_CHECKING:
    from core.runtime.artifact_registry import ArtifactRegistry
    from core.storage.base import Store
    from core.tools.runtime import ToolRuntime

logger = logging.getLogger("AutonomOS.VerificationEngine")


class VerificationEngine:
    """
    Central Deterministic Verification Engine.
    Executes verification plans, evaluates evidence, aggregates outcomes,
    generates verification reports, and prevents false claims of success.
    """

    def __init__(
        self,
        tool_runtime: ToolRuntime,
        artifact_registry: ArtifactRegistry,
        store: Store,
        event_logger: Optional[Callable[..., Event]] = None,
        custom_adapters: Optional[list[BaseCheckAdapter]] = None,
    ):
        self.tool_runtime = tool_runtime
        self.artifact_registry = artifact_registry
        self.store = store
        self.event_logger = event_logger

        # Register standard default adapters
        self.adapters: list[BaseCheckAdapter] = [
            FileCheckAdapter(),
            CommandCheckAdapter(),
            GitCheckAdapter(),
            ArtifactCheckAdapter(),
        ]
        if custom_adapters:
            self.adapters.extend(custom_adapters)

    def register_adapter(self, adapter: BaseCheckAdapter) -> None:
        self.adapters.insert(0, adapter)

    def verify_task(
        self,
        project: Project,
        task: Task,
        worker_id: Optional[str] = None,
        checkpoint_id: Optional[str] = None,
        plan: Optional[VerificationPlan] = None,
        causation_id: Optional[str] = None,
    ) -> Verification:
        """
        Execute deterministic verification on completed task work against explicit success criteria.
        """
        start_time = time.perf_counter()
        vid = f"verif-{uuid.uuid4().hex[:10]}"

        # 1. Resolve or Build Verification Plan
        resolved_plan = plan or self._resolve_plan(task)

        # 2. Emit VERIFICATION_STARTED event
        self._emit_event(
            event_type=EventType.VERIFICATION_STARTED,
            payload={
                "verification_id": vid,
                "task_id": task.id,
                "criteria_count": len(resolved_plan.criteria),
                "checkpoint_id": checkpoint_id,
            },
            project_id=project.id,
            task_id=task.id,
            worker_id=worker_id,
            correlation_id=task.id,
            causation_id=causation_id,
        )

        exec_context = CheckExecutionContext(
            project_id=project.id,
            workspace_root=project.root_path,
            task_id=task.id,
            worker_id=worker_id,
            tool_runtime=self.tool_runtime,
            artifact_registry=self.artifact_registry,
            store=self.store,
        )

        checks: list[VerificationCheck] = []
        all_evidence_ids: list[str] = []

        # 3. Execute Individual Checks
        for criterion in resolved_plan.criteria:
            adapter = self._find_adapter(criterion.check_type)
            chk_start_evt = self._emit_event(
                event_type=EventType.CHECK_STARTED,
                payload={
                    "verification_id": vid,
                    "check_type": criterion.check_type.value,
                    "description": criterion.description,
                    "required": criterion.required,
                },
                project_id=project.id,
                task_id=task.id,
                worker_id=worker_id,
                correlation_id=task.id,
                causation_id=causation_id,
            )

            if not adapter:
                check = VerificationCheck(
                    id=f"chk-{uuid.uuid4().hex[:8]}",
                    verification_id=vid,
                    criterion_id=criterion.id,
                    check_type=criterion.check_type,
                    description=criterion.description,
                    status=CheckStatus.ERROR,
                    required=criterion.required,
                    error_message=f"No verification adapter registered for check type '{criterion.check_type.value}'.",
                )
            else:
                check = adapter.execute(exec_context, criterion, vid)

            checks.append(check)
            all_evidence_ids.extend(check.evidence_ids)

            # Emit outcome event for check
            outcome_event_type = EventType.CHECK_PASSED
            if check.status == CheckStatus.FAILED:
                outcome_event_type = EventType.CHECK_FAILED
            elif check.status in (CheckStatus.UNCERTAIN, CheckStatus.ERROR):
                outcome_event_type = EventType.CHECK_UNCERTAIN if check.status == CheckStatus.UNCERTAIN else EventType.CHECK_ERROR

            self._emit_event(
                event_type=outcome_event_type,
                payload={
                    "verification_id": vid,
                    "check_id": check.id,
                    "check_type": check.check_type.value,
                    "status": check.status.value,
                    "duration_ms": check.duration_ms,
                    "error": check.error_message,
                },
                project_id=project.id,
                task_id=task.id,
                worker_id=worker_id,
                correlation_id=task.id,
                causation_id=chk_start_evt.event_id if chk_start_evt else causation_id,
            )

        # 4. Deterministic Aggregation
        passed_checks = [c.id for c in checks if c.status == CheckStatus.PASSED]
        failed_checks = [c.id for c in checks if c.status == CheckStatus.FAILED]
        uncertain_checks = [c.id for c in checks if c.status in (CheckStatus.UNCERTAIN, CheckStatus.ERROR)]

        failed_required = [c for c in checks if c.required and c.status == CheckStatus.FAILED]
        uncertain_required = [c for c in checks if c.required and c.status in (CheckStatus.UNCERTAIN, CheckStatus.ERROR)]

        if failed_required:
            status = VerificationStatus.FAILED
            summary = f"Verification FAILED: {len(failed_required)} required check(s) failed."
            final_event_type = EventType.VERIFICATION_FAILED
        elif uncertain_required:
            status = VerificationStatus.UNCERTAIN
            summary = f"Verification UNCERTAIN: {len(uncertain_required)} required check(s) inconclusive."
            final_event_type = EventType.VERIFICATION_UNCERTAIN
        else:
            status = VerificationStatus.PASSED
            summary = f"Verification PASSED: {len(passed_checks)}/{len(checks)} check(s) satisfied criteria."
            final_event_type = EventType.VERIFICATION_PASSED

        duration_ms = (time.perf_counter() - start_time) * 1000.0

        # 5. Generate Human-Readable Markdown Report
        report_md = self._generate_report_markdown(
            task=task,
            status=status,
            summary=summary,
            checks=checks,
            evidence_ids=all_evidence_ids,
            duration_ms=duration_ms,
        )

        verification = Verification(
            id=vid,
            project_id=project.id,
            task_id=task.id,
            checkpoint_id=checkpoint_id,
            status=status,
            checks=checks,
            evidence_ids=all_evidence_ids,
            summary=summary,
            started_at=utc_now(),
            completed_at=utc_now(),
            duration_ms=duration_ms,
            report_markdown=report_md,
            metadata={"plan_id": resolved_plan.id, "checks_count": len(checks)},
        )

        # 6. Save Report File & Register Artifact
        if self.artifact_registry:
            try:
                self.artifact_registry.register_artifact(
                    project_id=project.id,
                    task_id=task.id,
                    worker_id=worker_id or "verifier",
                    artifact_type=ArtifactType.REPORT,
                    relative_path=f".autonomos/reports/{task.id}/verification.md",
                    description=f"Authoritative Verification Report for task '{task.title}'",
                    content=report_md,
                )
            except Exception:
                pass

        # 7. Emit Final Verification Event
        self._emit_event(
            event_type=final_event_type,
            payload={
                "verification_id": vid,
                "status": status.value,
                "summary": summary,
                "passed_count": len(passed_checks),
                "failed_count": len(failed_checks),
                "uncertain_count": len(uncertain_checks),
                "duration_ms": duration_ms,
            },
            project_id=project.id,
            task_id=task.id,
            worker_id=worker_id,
            correlation_id=task.id,
            causation_id=causation_id,
        )

        return verification

    def _resolve_plan(self, task: Task) -> VerificationPlan:
        """Resolve or extract a deterministic VerificationPlan for the task."""
        # 1. Explicit metadata verification plan
        meta = getattr(task, "metadata", {}) or {}
        if "verification_plan" in meta and isinstance(meta["verification_plan"], dict):
            return VerificationPlan.from_dict(meta["verification_plan"])

        # 2. Explicit success criteria list (from task.success_criteria or task.metadata["success_criteria"])
        raw_criteria = getattr(task, "success_criteria", None) or meta.get("success_criteria") or []
        if raw_criteria:
            criteria = []
            for item in raw_criteria:
                if isinstance(item, dict):
                    criteria.append(SuccessCriterion.from_dict(item))
                elif isinstance(item, SuccessCriterion):
                    criteria.append(item)
            if criteria:
                return VerificationPlan(id=f"plan-{task.id}", task_id=task.id, criteria=criteria)

        # 3. Default fallback: verify task artifacts if any declared
        default_criteria: list[SuccessCriterion] = []
        if getattr(task, "artifacts", None) and self.artifact_registry:
            for art_id in task.artifacts:
                art = self.artifact_registry.get_artifact(art_id)
                if art:
                    default_criteria.append(
                        SuccessCriterion(
                            id=f"crit-art-{art.id}",
                            description=f"Verify generated artifact '{art.path}' exists.",
                            check_type=CheckType.ARTIFACT_EXISTS,
                            parameters={"artifact_id": art.id, "path": art.path},
                            required=True,
                        )
                    )

        if not default_criteria:
            # Basic sanity check: verify task workspace exists
            default_criteria.append(
                SuccessCriterion(
                    id=f"crit-sanity-{task.id}",
                    description=f"Sanity verification for task '{task.title}'.",
                    check_type=CheckType.FILE_EXISTS,
                    parameters={"path": "."},
                    required=True,
                )
            )

        return VerificationPlan(id=f"plan-{task.id}", task_id=task.id, criteria=default_criteria)

    def _find_adapter(self, check_type: CheckType) -> Optional[BaseCheckAdapter]:
        for adapter in self.adapters:
            if adapter.supports(check_type):
                return adapter
        return None

    def _generate_report_markdown(
        self,
        task: Task,
        status: VerificationStatus,
        summary: str,
        checks: list[VerificationCheck],
        evidence_ids: list[str],
        duration_ms: float,
    ) -> str:
        lines = [
            f"# Verification Report: {task.title}",
            "",
            f"- **Task ID**: `{task.id}`",
            f"- **Status**: **`{status.value}`**",
            f"- **Summary**: {summary}",
            f"- **Duration**: {duration_ms:.2f}ms",
            f"- **Evidence Records**: {len(evidence_ids)} collected",
            "",
            "## Verification Checks",
            "",
            "| Status | Type | Description | Required | Duration |",
            "| :--- | :--- | :--- | :--- | :--- |",
        ]

        for c in checks:
            status_icon = "✓ PASSED" if c.status == CheckStatus.PASSED else f"✗ {c.status.value}"
            req_str = "Yes" if c.required else "Optional"
            lines.append(f"| {status_icon} | `{c.check_type.value}` | {c.description} | {req_str} | {c.duration_ms:.1f}ms |")

        failed_checks = [c for c in checks if c.status in (CheckStatus.FAILED, CheckStatus.ERROR, CheckStatus.UNCERTAIN)]
        if failed_checks:
            lines.extend(["", "## Failure & Diagnostic Details", ""])
            for fc in failed_checks:
                lines.append(f"### `{fc.check_type.value}`: {fc.description}")
                lines.append(f"- **Status**: `{fc.status.value}`")
                lines.append(f"- **Expected**: `{fc.expected_result}`")
                lines.append(f"- **Actual**: `{fc.actual_result}`")
                if fc.error_message:
                    lines.append(f"- **Error**: {fc.error_message}")
                lines.append("")

        return "\n".join(lines)

    def _emit_event(
        self,
        event_type: EventType,
        payload: dict[str, Any],
        project_id: Optional[str] = None,
        task_id: Optional[str] = None,
        worker_id: Optional[str] = None,
        correlation_id: Optional[str] = None,
        causation_id: Optional[str] = None,
    ) -> Optional[Event]:
        if self.event_logger:
            return self.event_logger(
                event_type=event_type,
                payload=payload,
                source=EventSource.RUNTIME,
                project_id=project_id,
                task_id=task_id,
                worker_id=worker_id,
                correlation_id=correlation_id,
                causation_id=causation_id,
            )
        return None
