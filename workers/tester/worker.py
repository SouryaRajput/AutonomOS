from __future__ import annotations

import re
import time
from typing import Any, Optional

from core.enums import ArtifactType
from core.events.types import EventType
from core.inference.model import ModelRequirement
from core.inference.types import ModelCapability
from core.models import Task, WorkerManifest
from pkg.sdk.worker import WorkerRuntimeContext
from pkg.sdk.types import WorkerCapability, WorkerConfig, WorkerRequirement
from pkg.sdk.worker import Worker, WorkerOutput
from workers.tester.executor import TestExecutor
from workers.tester.inspector import ImplementationInspector
from workers.tester.investigator import DefectInvestigator
from workers.tester.model import (
    Defect,
    RequirementTrace,
    TestResult,
    TestSuiteResult,
    TesterResult,
    TestingPlan,
    TestingTaskSpec,
)
from workers.tester.planner import TestingPlanner
from workers.tester.prompt import build_tester_evaluation_prompt, parse_tester_evaluation
from workers.tester.report import TestingReportGenerator
from workers.tester.types import (
    DefectSeverity,
    FailureClassification,
    RequirementVerificationStatus,
    TestCategory,
    TestExecutionStatus,
    TesterFinalStatus,
)
from workers.tester.ui_validator import UIValidator


class TesterWorker(Worker):
    """
    Autonomous Specialist QA & Evaluation Worker in AutonomOS.
    Independently inspects codebases, executes multi-category tests, isolates regressions,
    validates visual UI / OCR, investigates defects, records authoritative evidence,
    and produces structured reports for Manager handoffs.
    """

    def __init__(self, worker_id: str = "worker.tester.default", config: Optional[WorkerConfig] = None):
        super().__init__(worker_id=worker_id, config=config)

    def get_manifest(self) -> WorkerManifest:
        return WorkerManifest(
            id=self.worker_id,
            name="Specialist Tester Worker",
            role="Independent QA & Evaluation Engineer",
            description="Executes independent tests, verifies requirements, isolates regressions, investigates defects, and logs authoritative evidence.",
            version="1.0.0",
            capabilities=[
                WorkerCapability.TESTING,
                WorkerCapability.CODE_ANALYSIS,
                WorkerCapability.UI_ANALYSIS,
                WorkerCapability.CODE_EXECUTION,
                WorkerCapability.VISION,
                WorkerCapability.STRUCTURED_OUTPUT,
            ],
            requirements=WorkerRequirement(
                required_tools=["shell.execute", "filesystem.read", "filesystem.list"],
                preferred_inference_capabilities={ModelCapability.REASONING, ModelCapability.STRUCTURED_OUTPUT},
                required_context_categories=["code", "architecture", "requirements", "artifacts"],
                minimum_context_window=8000,
            ),
        )

    def execute_task(self, context: WorkerRuntimeContext) -> WorkerOutput:
        task = context.task
        start_time = time.perf_counter()

        # 1. Parse Task Specification & Decompose Requirements
        context.progress.report(5.0, "Parsing testing task specification and requirements")
        spec = TestingPlanner.parse_task_spec(task)

        context.events.emit(
            EventType.TESTER_STARTED.value,
            {
                "task_id": task.id,
                "objective": spec.objective,
                "requirements_count": len(spec.requirements),
                "categories": [c.value for c in spec.scope.allowed_categories],
            },
        )

        # 2. Inspect Codebase & Discovered Test Fixtures
        context.progress.report(15.0, "Inspecting repository files and test fixtures")
        inspector = ImplementationInspector(context)
        test_files = inspector.discover_test_files()
        inspected_code = inspector.inspect_changed_files(spec.implementation_references[:5])

        # 3. Formulate Testing Plan & Traceability Matrix
        context.progress.report(25.0, "Constructing testing plan and requirement traceability matrix")
        plan = TestingPlanner.create_plan(spec)

        context.events.emit(
            EventType.TEST_PLAN_CREATED.value,
            {
                "plan_id": plan.plan_id,
                "strategy_summary": plan.strategy_summary,
                "requirements_count": len(plan.requirement_traces),
                "categories": [c.value for c in plan.test_categories],
            },
        )

        # 4. Discover & Execute Test Suites
        context.progress.report(35.0, "Executing test suites via Tool Runtime")
        executor = TestExecutor(context, scope=spec.scope)
        
        # Determine test commands to execute
        commands_to_run: list[tuple[str, TestCategory]] = []
        
        # Check if custom test commands were specified in metadata
        custom_cmds = task.metadata.get("test_commands", []) if task.metadata else []
        if custom_cmds and isinstance(custom_cmds, list):
            for cmd in custom_cmds:
                if isinstance(cmd, str):
                    commands_to_run.append((cmd, TestCategory.UNIT))
                elif isinstance(cmd, dict):
                    commands_to_run.append((cmd.get("command", ""), TestCategory(cmd.get("category", "UNIT"))))
        
        if not commands_to_run:
            default_cmd = executor.discover_default_test_command(test_files)
            commands_to_run.append((default_cmd, TestCategory.UNIT))

        # Check for baseline comparisons
        baseline_results = task.metadata.get("baseline_test_results", {}) if task.metadata else {}
        baseline_map = {k: TestExecutionStatus(v) for k, v in baseline_results.items()} if baseline_results else None

        executed_suites: list[TestSuiteResult] = []
        for cmd, cat in commands_to_run:
            context.events.emit(
                EventType.TEST_STARTED.value,
                {"command": cmd, "category": cat.value, "task_id": task.id},
            )
            suite_res = executor.execute_test_suite(
                suite_name=f"{cat.value} Test Pass",
                commands=[(cmd, cat)],
                baseline_results=baseline_map,
            )
            executed_suites.append(suite_res)

            context.events.emit(
                EventType.TEST_COMPLETED.value,
                {
                    "command": cmd,
                    "passed": suite_res.failed == 0 and suite_res.errors == 0,
                    "passed_count": suite_res.passed,
                    "total_count": suite_res.total,
                    "exit_code": suite_res.test_results[0].exit_code if suite_res.test_results else 0,
                },
            )

        # 5. UI / OCR / Visual Validation (if applicable)
        if TestCategory.UI in plan.test_categories:
            context.progress.report(55.0, "Performing UI / OCR visual validation")
            context.events.emit(EventType.UI_TEST_STARTED.value, {"target": spec.objective})
            
            ui_val = UIValidator(context)
            ui_elements = task.metadata.get("expected_ui_elements", ["AutonomOS", "Verified"]) if task.metadata else ["AutonomOS"]
            ui_res = ui_val.validate_ui_layout_and_text(
                target_description=spec.objective,
                expected_elements=ui_elements,
            )
            ui_suite = TestSuiteResult(
                suite_id=f"suite-ui-{task.id[:6]}",
                suite_name="UI & OCR Validation Suite",
                total=1,
                passed=1 if ui_res.status == TestExecutionStatus.PASSED else 0,
                failed=1 if ui_res.status == TestExecutionStatus.FAILED else 0,
                test_results=[ui_res],
            )
            executed_suites.append(ui_suite)
            
            context.events.emit(
                EventType.UI_TEST_COMPLETED.value,
                {"passed": ui_res.status == TestExecutionStatus.PASSED, "findings": ui_res.output_snippet},
            )

        # 6. Failure Reproduction & Defect Investigation
        context.progress.report(70.0, "Investigating test failures and isolating regressions")
        investigator = DefectInvestigator(context)
        defects: list[Defect] = []
        regressions: list[TestResult] = []

        all_tests: list[TestResult] = []
        for s in executed_suites:
            all_tests.extend(s.test_results)

        for t in all_tests:
            if t.status in (TestExecutionStatus.FAILED, TestExecutionStatus.ERROR):
                context.events.emit(
                    EventType.TEST_FAILED.value,
                    {"test_name": t.name, "error_message": t.error_message},
                )
                defect, fail_record = investigator.investigate_failure(
                    failed_test=t,
                    affected_requirement=spec.requirements[0] if spec.requirements else "Core requirement",
                    affected_component=spec.affected_components[0] if spec.affected_components else "System",
                    relevant_files=spec.implementation_references,
                )
                defects.append(defect)

                context.events.emit(
                    EventType.DEFECT_DETECTED.value,
                    {
                        "defect_id": defect.defect_id,
                        "title": defect.title,
                        "severity": defect.severity.value,
                        "affected_requirement": defect.affected_requirement,
                        "suspected_cause": defect.suspected_cause,
                    },
                )

            if t.is_regression:
                regressions.append(t)
                context.events.emit(
                    EventType.REGRESSION_DETECTED.value,
                    {"test_name": t.name, "notes": t.output_snippet[:200]},
                )

        # 7. Model Evaluation & Synthesis via Inference Gateway
        context.progress.report(80.0, "Synthesizing evaluation results via Inference Gateway")
        messages = build_tester_evaluation_prompt(spec, executed_suites, inspected_code)

        try:
            inf_resp = context.inference.generate(
                messages=messages,
                requirements=ModelRequirement(
                    required_capabilities={ModelCapability.REASONING, ModelCapability.STRUCTURED_OUTPUT},
                    minimum_context=8000,
                ),
                temperature=0.1,
            )
            eval_data = parse_tester_evaluation(inf_resp.content)
        except Exception as e:
            context.log.warning(f"Inference evaluation fallback used: {e}")
            eval_data = {
                "overall_status": "FAILED" if defects else "VERIFIED",
                "requirement_statuses": [],
                "reasoning_summary": "Automated test execution evaluation.",
                "manager_summary": f"- Testing completed: {len(defects)} defects found.",
            }

        # 8. Update Requirement Traceability Statuses
        all_passed = len(defects) == 0 and len(regressions) == 0
        for trace in plan.requirement_traces:
            trace.test_ids = [t.test_id for t in all_tests]
            if all_passed:
                trace.status = RequirementVerificationStatus.VERIFIED
                trace.notes = "All test assertions passed cleanly."
            else:
                trace.status = RequirementVerificationStatus.FAILED
                trace.notes = f"Failed with {len(defects)} defect(s)."

        # Determine overall final status
        if defects:
            final_status = TesterFinalStatus.FAILED
        elif all_passed:
            final_status = TesterFinalStatus.VERIFIED
        else:
            final_status = TesterFinalStatus.PARTIALLY_VERIFIED

        # 9. Record Authoritative Evidence
        context.progress.report(88.0, "Recording authoritative test and defect evidence")
        evidence_ids: list[str] = []

        # Record test execution evidence
        for s in executed_suites:
            ev = context.record_evidence(
                evidence_type="TEST_EXECUTION_EVIDENCE",
                data=f"Suite [{s.suite_name}]: {s.passed}/{s.total} passed in {s.duration_ms:.0f}ms (exit_code: {s.test_results[0].exit_code if s.test_results else 0})",
            )
            if ev:
                evidence_ids.append(ev.id)

        # Record defect evidence
        for d in defects:
            ev = context.record_evidence(
                evidence_type="DEFECT_EVIDENCE",
                data=f"Defect [{d.defect_id}] ({d.severity.value}): '{d.title}' in component '{d.affected_component}' — Cause: {d.suspected_cause[:150]}",
            )
            if ev:
                evidence_ids.append(ev.id)
                d.evidence_ids.append(ev.id)

        # Record regression evidence
        for r in regressions:
            ev = context.record_evidence(
                evidence_type="REGRESSION_EVIDENCE",
                data=f"Regression [{r.test_id}]: Test '{r.name}' passed in baseline but failed in current execution.",
            )
            if ev:
                evidence_ids.append(ev.id)

        # 10. Generate Markdown Report Artifact
        context.progress.report(93.0, "Generating Markdown test report and artifact")
        slug = re.sub(r"[^\w\-]", "_", spec.objective[:40].lower()).strip("_") or "qa_eval"
        rel_report_path = f"testing/{slug}_report_{task.id[:8]}.md"

        tester_result = TesterResult(
            task_id=task.id,
            project_id=context.project_id,
            objective=spec.objective,
            plan=plan,
            suites=executed_suites,
            defects=defects,
            regressions=regressions,
            requirement_traces=plan.requirement_traces,
            evidence_ids=evidence_ids,
            environment_info={"os": "system", "total_tests": len(all_tests)},
            status=final_status,
            report_path=rel_report_path,
        )

        manager_summary = TestingReportGenerator.generate_manager_summary(tester_result)
        tester_result.summary_for_manager = manager_summary

        report_md = TestingReportGenerator.generate_markdown_report(tester_result)
        report_art = context.artifacts.create(
            artifact_type=ArtifactType.REPORT,
            relative_path=rel_report_path,
            description=f"QA Test Report for '{spec.objective}'",
            content=report_md,
            metadata={
                "task_id": task.id,
                "status": final_status.value,
                "defects_count": len(defects),
                "regressions_count": len(regressions),
                "total_tests": len(all_tests),
            },
        )
        if report_art:
            tester_result.artifacts.append(report_art.id)

        # 11. Request Deterministic Verification
        if all_passed:
            context.request_verification(
                target_type="TASK",
                target_id=task.id,
                evidence_ids=evidence_ids,
                notes=f"QA evaluation confirmed 0 defects and {len(all_tests)} passed tests.",
            )

        # 12. Final Event Emission & Output
        context.progress.report(100.0, "QA evaluation complete")
        context.events.emit(
            EventType.TESTER_COMPLETED.value if final_status == TesterFinalStatus.VERIFIED else EventType.TESTER_FAILED.value,
            {
                "status": final_status.value,
                "verified_count": sum(1 for r in plan.requirement_traces if r.status == RequirementVerificationStatus.VERIFIED),
                "total_requirements": len(plan.requirement_traces),
                "defects_count": len(defects),
                "regressions_count": len(regressions),
                "report_path": rel_report_path,
            },
        )

        duration = time.perf_counter() - start_time
        context.log.info(f"Tester completed evaluation in {duration:.2f}s with status {final_status.value}")

        return WorkerOutput(
            success=(final_status == TesterFinalStatus.VERIFIED),
            summary=manager_summary,
            artifacts=[rel_report_path],
            metadata={
                "tester_result": tester_result.to_dict(),
                "status": final_status.value,
                "defects_count": len(defects),
                "regressions_count": len(regressions),
                "evidence_ids": evidence_ids,
            },
        )
