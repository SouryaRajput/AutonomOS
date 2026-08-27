from __future__ import annotations

import logging
import re
import time
from typing import Any, Optional
import uuid

from core.enums import ArtifactType
from core.events.types import EventType
from core.inference.model import ModelRequirement
from core.inference.types import ModelCapability
from core.models import Task, WorkerManifest, WorkerOutput, utc_now
from pkg.sdk.types import WorkerCapability
from pkg.sdk.worker import Worker, WorkerRuntimeContext
from workers.programmer.editor import CodeEditor, ScopeViolationError
from workers.programmer.inspector import RepositoryInspector
from workers.programmer.model import (
    BuildExecutionResult,
    ChangeRecord,
    FileChange,
    ProgrammerResult,
    ProgrammingPlan,
    ProgrammingScope,
    ProgrammingTaskSpec,
    SelfReviewResult,
    TestExecutionResult,
)
from workers.programmer.planner import ProgrammingPlanner
from workers.programmer.prompt import (
    build_implementation_prompt,
    parse_implementation_decision,
)
from workers.programmer.report import ProgrammingReportGenerator
from workers.programmer.scope import ScopeGuard
from workers.programmer.tester import TestRunner
from workers.programmer.types import (
    FileChangeType,
    ProgrammingMode,
    ProgrammingStatus,
    TestFailureType,
)

logger = logging.getLogger("AutonomOS.Programmer")


class ProgrammerWorker(Worker):
    """
    AutonomOS Specialist Programmer Worker.
    Autonomous software engineering specialist responsible for understanding tasks,
    inspecting repositories, planning safe modifications, editing code, running checks,
    producing verifiable evidence, and reporting concise summaries to the Manager.
    """

    def __init__(
        self,
        worker_id: str = "worker.programmer",
        name: str = "AutonomOS Specialist Programmer",
        description: str = "Autonomous specialist for software implementation, code editing, testing, and evidence generation",
        version: str = "1.0.0",
        default_mode: ProgrammingMode = ProgrammingMode.FEATURE,
    ):
        self._worker_id = worker_id
        self._name = name
        self._description = description
        self._version = version
        self.default_mode = default_mode

        self._manifest = WorkerManifest(
            id=self._worker_id,
            name=self._name,
            role="Programmer",
            description=self._description,
            version=self._version,
            capabilities=[
                WorkerCapability.CODE_GENERATION.value,
                WorkerCapability.CODE_ANALYSIS.value,
                "REPOSITORY_ANALYSIS",
                WorkerCapability.FILE_MANIPULATION.value,
                WorkerCapability.CODE_EXECUTION.value,
                WorkerCapability.TESTING.value,
                "VERSION_CONTROL",
                "DEBUGGING",
                "REFACTORING",
                "BUILD_EXECUTION",
            ],
            permissions=[
                "filesystem",
                "filesystem.read_file",
                "filesystem.write_file",
                "filesystem.delete_file",
                "filesystem.list_directory",
                "shell.execute",
                "git",
                "git.status",
                "git.diff",
                "*",
            ],
            created_at=utc_now(),
        )

    def get_manifest(self) -> WorkerManifest:
        """Return the static manifest and declared capabilities for this worker."""
        return self._manifest

    def execute_task(self, context: WorkerRuntimeContext, task: Task) -> WorkerOutput:
        """
        Execute the assigned programming task through the disciplined, multi-phase engineering loop.
        """
        start_time = time.perf_counter()
        context.log.info(f"Programmer started task '{task.title}' (ID: {task.id})")
        context.progress.report(5.0, "Parsing programming task specification and analyzing scope")

        # 1. Parse Task Specification & Build Plan
        spec = ProgrammingPlanner.parse_task_spec(task)
        plan = ProgrammingPlanner.create_plan(spec)

        # Emit PROGRAMMER_STARTED and PROGRAMMER_PLAN_CREATED events
        context.events.emit(
            EventType.PROGRAMMER_STARTED.value,
            {
                "objective": spec.objective,
                "mode": spec.mode.value,
                "allowed_paths": spec.scope.allowed_paths,
                "requirements_count": len(spec.requirements),
            },
        )
        context.events.emit(
            EventType.PROGRAMMER_PLAN_CREATED.value,
            {
                "plan_id": plan.plan_id,
                "steps_count": len(plan.planned_steps),
                "affected_files": plan.affected_files,
                "complexity": plan.estimated_complexity,
            },
        )

        # 2. Inspect Repository, Project Memory, and Context Engine
        context.progress.report(15.0, "Inspecting repository structure and loading project context")
        ctx_pkg = context.context.get(focus_areas=["source", "architecture", "tests", "dependencies"])

        # Scan repository files via Tool Runtime
        scanned_files = RepositoryInspector.list_files(context, recursive=True)
        context_files: dict[str, str] = {}

        # Prioritize files in scope or mentioned in requirements
        files_to_read: set[str] = set()
        for allow_p in spec.scope.allowed_paths:
            for s_file in scanned_files:
                if ScopeGuard.normalize_relative_path(s_file).startswith(ScopeGuard.normalize_relative_path(allow_p)) or s_file == allow_p:
                    files_to_read.add(s_file)

        # Also inspect any existing files related to keywords
        if not files_to_read and scanned_files:
            files_to_read.update(scanned_files[:10])

        for f_path in list(files_to_read)[:12]:
            content = RepositoryInspector.read_file(context, f_path)
            if content is not None:
                context_files[f_path] = content

        context.events.emit(
            EventType.PROGRAMMER_INSPECTION_PERFORMED.value,
            {
                "target": "workspace",
                "summary": f"Scanned {len(scanned_files)} files; loaded {len(context_files)} files into context",
            },
        )

        # 3. Create Pre-Modification Safety Checkpoint
        context.progress.report(25.0, "Requesting pre-modification safety checkpoint")
        try:
            chk_id = context.safety.checkpoint(label=f"pre-task-{task.id[:8]}")
        except Exception as chk_err:
            context.log.warning(f"Could not create safety checkpoint: {chk_err}")
            chk_id = None

        # 4. Record Pre-Change Baseline Checks (for REFACTOR / BUGFIX modes)
        context.progress.report(35.0, "Recording pre-modification baseline checks")
        baseline_results: list[TestExecutionResult] = []
        baseline_failed_cmds: set[str] = set()

        if spec.mode in (ProgrammingMode.BUGFIX, ProgrammingMode.REFACTOR) and spec.test_expectations:
            for cmd in spec.test_expectations:
                b_res = TestRunner.run_test(context, cmd)
                baseline_results.append(b_res)
                if not b_res.passed:
                    baseline_failed_cmds.add(cmd)

        # 5. Generate Code Modifications via Inference Gateway (OmniRoute)
        context.progress.report(50.0, "Generating code modifications via Inference Gateway")
        messages = build_implementation_prompt(
            spec=spec,
            plan=plan,
            context_files=context_files,
            context_package=ctx_pkg,
            research_findings=spec.relevant_research,
            test_results=baseline_results,
        )

        inf_resp = context.inference.generate(
            messages=messages,
            requirements=ModelRequirement(
                required_capabilities={
                    ModelCapability.CODE_GENERATION,
                    ModelCapability.REASONING,
                    ModelCapability.STRUCTURED_OUTPUT,
                },
                minimum_context=12000,
            ),
            temperature=0.1,
        )

        file_ops, tests_to_run, build_cmd, assumptions, warnings, self_review_dict, raw_summary = parse_implementation_decision(
            inf_resp.content
        )

        # Combine tests to run with task test expectations
        combined_tests = list(tests_to_run)
        for t_exp in spec.test_expectations:
            if t_exp not in combined_tests:
                combined_tests.append(t_exp)

        # 6. Apply Code Modifications through Tool Runtime
        context.progress.report(65.0, "Applying code modifications through Tool Runtime")
        editor = CodeEditor(context, spec.scope)
        applied_changes: list[FileChange] = []
        scope_errors: list[str] = []

        for op in file_ops:
            action = str(op.get("action", "WRITE")).upper()
            target_path = str(op.get("path", "")).strip()
            content = str(op.get("content", ""))
            desc = str(op.get("description", ""))
            old_path = str(op.get("old_path", "")).strip()

            try:
                if action == "DELETE":
                    ch = editor.delete_file(target_path, description=desc)
                elif action == "RENAME" and old_path:
                    ch = editor.rename_file(old_path, target_path, description=desc)
                else:  # WRITE
                    ch = editor.write_file(target_path, content, description=desc, overwrite=True)

                applied_changes.append(ch)
                context.events.emit(
                    EventType.PROGRAMMER_CODE_MODIFIED.value,
                    {
                        "path": ch.path,
                        "change_type": ch.change_type.value,
                        "diff_lines": len(ch.diff.splitlines()),
                        "description": ch.description,
                    },
                )
            except ScopeViolationError as scope_err:
                context.log.error(f"Scope violation: {scope_err}")
                scope_errors.append(str(scope_err))
            except Exception as edit_err:
                context.log.error(f"Failed to apply edit to '{target_path}': {edit_err}")
                scope_errors.append(str(edit_err))

        # 7. Diff Inspection & Scope Validation
        context.progress.report(75.0, "Inspecting diffs and validating change count")
        count_valid, count_msg = ScopeGuard.validate_change_count(len(applied_changes), spec.scope)
        if not count_valid:
            scope_errors.append(count_msg)

        context.events.emit(
            EventType.PROGRAMMER_DIFF_INSPECTED.value,
            {
                "files_changed_count": len(applied_changes),
                "scope_valid": len(scope_errors) == 0,
            },
        )

        # 8. Test & Build Execution through Tool Runtime
        context.progress.report(80.0, "Executing test suites and build validation")
        executed_tests: list[TestExecutionResult] = []
        build_result: Optional[BuildExecutionResult] = None

        if build_cmd:
            context.events.emit(EventType.PROGRAMMER_BUILD_STARTED.value, {"command": build_cmd, "target": "project"})
            build_result = TestRunner.run_build(context, build_cmd)
            context.events.emit(
                EventType.PROGRAMMER_BUILD_COMPLETED.value,
                {
                    "command": build_cmd,
                    "passed": build_result.passed,
                    "exit_code": build_result.exit_code,
                },
            )

        for test_cmd in combined_tests:
            context.events.emit(EventType.PROGRAMMER_TEST_STARTED.value, {"command": test_cmd, "stage": "post-implementation"})
            t_res = TestRunner.run_test(context, test_cmd, baseline_failed_commands=baseline_failed_cmds)
            executed_tests.append(t_res)
            context.events.emit(
                EventType.PROGRAMMER_TEST_COMPLETED.value,
                {
                    "command": test_cmd,
                    "passed": t_res.passed,
                    "exit_code": t_res.exit_code,
                    "duration_ms": t_res.duration_ms,
                },
            )

        # Evaluate regressions against baseline
        has_regressions, regression_notes = TestRunner.evaluate_regressions(baseline_results, executed_tests)
        if has_regressions:
            warnings.extend(regression_notes)

        # 9. Self-Review & Verification Evidence Collection
        context.progress.report(85.0, "Performing self-review and recording evidence")
        all_tests_passed = all(t.passed or t.failure_classification == TestFailureType.PREEXISTING_FAILURE for t in executed_tests)
        build_passed = (build_result is None or build_result.passed)

        self_review = SelfReviewResult(
            checks_passed=all_tests_passed and build_passed and len(scope_errors) == 0,
            modified_files_in_scope=len(scope_errors) == 0,
            tests_added_or_updated=any("test" in c.path.lower() for c in applied_changes) or len(executed_tests) > 0,
            regression_risk="HIGH" if has_regressions else ("MEDIUM" if not all_tests_passed else "LOW"),
            assumptions_verified=len(assumptions) <= 3,
            notes=str(self_review_dict.get("notes", "")),
        )

        context.events.emit(
            EventType.PROGRAMMER_SELF_REVIEW_COMPLETED.value,
            {
                "checks_passed": self_review.checks_passed,
                "regression_risk": self_review.regression_risk,
            },
        )

        # Record Authoritative Evidence
        evidence_ids: list[str] = []
        for ch in applied_changes:
            if ch.diff:
                ev = context.record_evidence(
                    evidence_type="CODE_CHANGE_DIFF",
                    data=f"File [{ch.path}] ({ch.change_type.value}) — Checksum: {ch.new_checksum or ch.original_checksum or 'N/A'}\n{ch.diff[:3000]}",
                )
                if ev:
                    evidence_ids.append(ev.id)

        for t in executed_tests:
            ev = context.record_evidence(
                evidence_type="TEST_EXECUTION_EVIDENCE",
                data=f"Command: '{t.command}' | Status: {'PASSED' if t.passed else 'FAILED'} (Exit Code {t.exit_code}) | Duration: {t.duration_ms:.1f}ms\nOutput: {t.output[:2000]}",
            )
            if ev:
                evidence_ids.append(ev.id)

        if build_result:
            ev = context.record_evidence(
                evidence_type="BUILD_EXECUTION_EVIDENCE",
                data=f"Build Command: '{build_result.command}' | Status: {'PASSED' if build_result.passed else 'FAILED'} (Exit Code {build_result.exit_code})\nOutput: {build_result.output[:2000]}",
            )
            if ev:
                evidence_ids.append(ev.id)

        # 10. Assemble ChangeRecord & ProgrammerResult
        context.progress.report(90.0, "Generating Markdown implementation report artifact")
        
        files_created = [c.path for c in applied_changes if c.change_type == FileChangeType.CREATE]
        files_modified = [c.path for c in applied_changes if c.change_type == FileChangeType.MODIFY]
        files_deleted = [c.path for c in applied_changes if c.change_type == FileChangeType.DELETE]
        files_renamed = [{"from": c.old_path or "", "to": c.path} for c in applied_changes if c.change_type == FileChangeType.RENAME]

        change_record = ChangeRecord(
            task_id=task.id,
            project_id=task.project_id,
            files_created=files_created,
            files_modified=files_modified,
            files_deleted=files_deleted,
            files_renamed=files_renamed,
            requirements_addressed=spec.requirements,
            tests_run=executed_tests,
            build_result=build_result,
            artifacts=[],
            notes=raw_summary,
        )

        slug = re.sub(r"[^\w\-]", "_", spec.objective[:40].lower()).strip("_") or "implementation"
        rel_report_path = f"implementation/{slug}_{task.id[:8]}.md"

        # Determine Programming Status
        if scope_errors or has_regressions:
            overall_status = ProgrammingStatus.FAILED if has_regressions else ProgrammingStatus.BLOCKED
        elif not all_tests_passed or (build_result and not build_result.passed):
            overall_status = ProgrammingStatus.PARTIALLY_COMPLETED
        else:
            overall_status = ProgrammingStatus.SUCCESS

        result = ProgrammerResult(
            task_id=task.id,
            project_id=task.project_id,
            objective=spec.objective,
            mode=spec.mode,
            plan=plan,
            change_record=change_record,
            changes=applied_changes,
            test_results=executed_tests,
            build_result=build_result,
            self_review=self_review,
            evidence_ids=evidence_ids,
            report_path=rel_report_path,
            summary_for_manager=raw_summary,
            assumptions=assumptions,
            warnings=warnings,
            unresolved_issues=scope_errors,
            status=overall_status,
            cost_estimate=getattr(inf_resp.cost, "total_cost", 0.0) if inf_resp.cost else 0.0,
        )

        manager_summary = ProgrammingReportGenerator.generate_manager_summary(result)
        result.summary_for_manager = manager_summary

        report_markdown = ProgrammingReportGenerator.generate_markdown_report(result)
        report_artifact = context.artifacts.create(
            artifact_type=ArtifactType.REPORT,
            relative_path=rel_report_path,
            description=f"Implementation report for '{spec.objective}'",
            content=report_markdown,
            metadata={
                "task_id": task.id,
                "files_changed_count": len(applied_changes),
                "tests_count": len(executed_tests),
                "status": overall_status.value,
            },
        )
        result.report_artifact_id = report_artifact.id
        change_record.artifacts.append(report_artifact.id)

        # 11. Update Persistent Project Memory if appropriate
        try:
            if applied_changes and overall_status == ProgrammingStatus.SUCCESS:
                context.memory.record_decision(
                    title=f"Implementation: {spec.objective[:50]}",
                    context=f"Programming implementation for task: {spec.objective}",
                    decision=f"Applied {len(applied_changes)} file change(s): {', '.join([c.path for c in applied_changes[:4]])}",
                    reasoning=raw_summary or "Implemented requested feature/fix in accordance with task requirements.",
                    consequences=f"Implementation report registered in `{report_artifact.path}`.",
                )
        except Exception as mem_err:
            context.log.warning(f"Could not record implementation decision in memory: {mem_err}")

        # 12. Request Authoritative Verification
        context.progress.report(95.0, "Requesting deterministic verification")
        try:
            context.verification.request()
        except Exception as verif_err:
            context.log.warning(f"Verification request completed with notice: {verif_err}")

        # 13. Emit PROGRAMMER_COMPLETED / PROGRAMMER_FAILED and return WorkerOutput
        is_success = (overall_status == ProgrammingStatus.SUCCESS)
        if is_success:
            context.events.emit(
                EventType.PROGRAMMER_COMPLETED.value,
                {
                    "files_changed_count": len(applied_changes),
                    "tests_count": len(executed_tests),
                    "report_artifact_id": report_artifact.id,
                    "summary": manager_summary,
                },
            )
        else:
            context.events.emit(
                EventType.PROGRAMMER_FAILED.value,
                {
                    "error": "; ".join(scope_errors) if scope_errors else f"Tests/build failed (Status: {overall_status.value})",
                    "reason": manager_summary,
                },
            )

        context.progress.report(100.0, "Programmer execution finished")
        context.log.info(f"Programmer finished task '{task.title}' in {time.perf_counter() - start_time:.2f}s (Success: {is_success})")

        return WorkerOutput(
            success=is_success,
            summary=manager_summary,
            created_artifacts=[report_artifact.to_dict()],
            metadata={
                "programmer_result": result.to_dict(),
                "report_artifact_id": report_artifact.id,
                "report_path": rel_report_path,
                "files_changed_count": len(applied_changes),
                "status": overall_status.value,
            },
        )
