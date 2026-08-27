from __future__ import annotations

import re
from typing import Any, Optional
import uuid

from pkg.sdk.worker import WorkerRuntimeContext
from workers.tester.model import (
    Defect,
    FailureRecord,
    TestResult,
)
from workers.tester.types import (
    DefectSeverity,
    FailureClassification,
    RootCauseConfidence,
    TestExecutionStatus,
)


class DefectInvestigator:
    """
    Investigates failed test results, performs diagnostic reproduction passes,
    classifies failure types and severities, and formulates structured Defect cards.
    """

    def __init__(self, context: WorkerRuntimeContext):
        self.context = context

    def investigate_failure(
        self,
        failed_test: TestResult,
        affected_requirement: str = "Core requirement",
        affected_component: str = "Implementation",
        relevant_files: Optional[list[str]] = None,
    ) -> tuple[Defect, FailureRecord]:
        defect_id = f"def-{uuid.uuid4().hex[:8]}"
        failure_id = f"fail-{uuid.uuid4().hex[:8]}"

        classification = self._classify_failure(failed_test)
        severity = self._estimate_severity(failed_test, classification)
        root_cause_conf = RootCauseConfidence.LIKELY if failed_test.output_snippet else RootCauseConfidence.POSSIBLE

        # Extract suspected cause from error output
        suspected_cause = self._extract_suspected_cause(failed_test)

        # Build reproduction steps
        repro_steps = {
            "preconditions": "Repository is checked out at current implementation state.",
            "command": failed_test.command,
            "steps": [
                f"1. Set up project test environment.",
                f"2. Execute test command: `{failed_test.command}`",
                f"3. Observe exit code and output.",
            ],
            "expected_behavior": f"Command `{failed_test.command}` exits with status 0 and all assertions pass.",
            "actual_behavior": f"Command exited with code {failed_test.exit_code}. Error: {failed_test.error_message or failed_test.output_snippet[:200]}",
        }

        fix_guidance = (
            f"Inspect {', '.join(relevant_files or [affected_component])}. "
            f"Ensure {affected_requirement} is correctly handled and test `{failed_test.name}` passes."
        )

        title = f"Test Failure: {failed_test.name}"
        if failed_test.is_regression:
            title = f"Regression: {failed_test.name}"

        defect = Defect(
            defect_id=defect_id,
            title=title,
            severity=severity,
            classification=classification,
            affected_requirement=affected_requirement,
            affected_component=affected_component,
            reproduction_steps=repro_steps,
            expected_behavior=repro_steps["expected_behavior"],
            actual_behavior=repro_steps["actual_behavior"],
            suspected_cause=suspected_cause,
            root_cause_confidence=root_cause_conf,
            suggested_fix_guidance=fix_guidance,
            relevant_files=relevant_files or [],
        )

        record = FailureRecord(
            failure_id=failure_id,
            test_id=failed_test.test_id,
            classification=classification,
            severity=severity,
            reproducibility="REPRODUCIBLE",
            stack_trace=failed_test.output_snippet[:2000],
            notes=f"Investigated from test {failed_test.test_id}",
        )

        return defect, record

    def _classify_failure(self, test: TestResult) -> FailureClassification:
        if test.is_regression:
            return FailureClassification.REGRESSION

        out_lower = (test.output_snippet + " " + test.error_message).lower()

        if any(w in out_lower for w in ("modulenotfounderror", "importerror", "command not found", "no such file or directory", "environment")):
            return FailureClassification.ENVIRONMENT_FAILURE
        if any(w in out_lower for w in ("assertionerror", "failed assertion", "expected", "actual", "typeerror", "valueerror", "syntaxerror")):
            return FailureClassification.IMPLEMENTATION_BUG
        if any(w in out_lower for w in ("timeout", "timed out", "deadline exceeded")):
            return FailureClassification.TEST_BUG

        return FailureClassification.IMPLEMENTATION_BUG

    def _estimate_severity(self, test: TestResult, classification: FailureClassification) -> DefectSeverity:
        if classification == FailureClassification.REGRESSION:
            return DefectSeverity.HIGH
        if classification == FailureClassification.ENVIRONMENT_FAILURE:
            return DefectSeverity.MEDIUM

        out_lower = (test.output_snippet + " " + test.error_message).lower()
        if any(w in out_lower for w in ("critical", "crash", "segmentation fault", "security", "auth failure", "data loss")):
            return DefectSeverity.CRITICAL

        return DefectSeverity.HIGH if test.exit_code != 0 else DefectSeverity.MEDIUM

    def _extract_suspected_cause(self, test: TestResult) -> str:
        lines = [line.strip() for line in test.output_snippet.splitlines() if line.strip()]
        for line in reversed(lines):
            if any(term in line for term in ("Error:", "Exception:", "FAILED", "AssertionError", "SyntaxError", "TypeError", "ValueError")):
                return line[:200]
        return test.error_message[:200] if test.error_message else "Command returned non-zero exit status."
