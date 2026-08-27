from __future__ import annotations

import logging
import re
import time
from typing import Optional

from pkg.sdk.worker import WorkerRuntimeContext
from workers.programmer.model import (
    BuildExecutionResult,
    TestExecutionResult,
)
from workers.programmer.types import TestFailureType

logger = logging.getLogger("AutonomOS.Programmer.Tester")


class TestRunner:
    """
    Executes tests and build checks through the Tool Runtime,
    classifies failures, and tracks regressions against established baselines.
    """

    @classmethod
    def classify_failure(
        cls,
        command: str,
        output: str,
        exit_code: int,
        is_preexisting: bool = False,
    ) -> TestFailureType:
        """Deterministically categorize a test or check failure."""
        if exit_code == 0:
            return TestFailureType.UNKNOWN

        if is_preexisting:
            return TestFailureType.PREEXISTING_FAILURE

        low_out = (output or "").lower()

        if any(e in low_out for e in ("command not found", "not recognized as an internal", "cannot find binary", "no such file or directory: 'pytest'", "sh: 1:")):
            return TestFailureType.ENVIRONMENT_FAILURE

        if any(e in low_out for e in ("modulenotfounderror: no module named", "importerror: cannot import name", "package not found")):
            return TestFailureType.DEPENDENCY_FAILURE

        if any(e in low_out for e in ("syntaxerror", "indentationerror", "cannot import test module", "failed to collect tests")):
            return TestFailureType.TEST_FAILURE

        if any(e in low_out for e in ("assertionerror", "failed (failures=", "failed (errors=", "test failed", "assert ")):
            return TestFailureType.IMPLEMENTATION_FAILURE

        return TestFailureType.UNKNOWN

    @classmethod
    def run_test(
        cls,
        context: WorkerRuntimeContext,
        command: str,
        timeout_seconds: int = 60,
        baseline_failed_commands: Optional[set[str]] = None,
    ) -> TestExecutionResult:
        """Run a test command via Tool Runtime."""
        start = time.perf_counter()
        exit_code = 0
        output_str = ""

        try:
            context.log.info(f"Executing test command: '{command}'")
            res = context.tools.execute(
                tool_id="shell.execute",
                arguments={"command": command, "action": "execute"},
                timeout_seconds=timeout_seconds,
            )
            # Inspect output format
            if isinstance(res.output, dict):
                exit_code = int(res.output.get("exit_code", 0))
                output_str = str(res.output.get("stdout", "") or res.output.get("output", ""))
                stderr_str = str(res.output.get("stderr", ""))
                if stderr_str:
                    output_str = f"{output_str}\n{stderr_str}".strip()
            elif isinstance(res.output, str):
                output_str = res.output
                exit_code = 0 if res.is_success else 1
            else:
                output_str = str(res.output or "")
                exit_code = 0 if res.is_success else 1
        except Exception as err:
            output_str = f"Execution exception: {err}"
            exit_code = 1

        duration_ms = (time.perf_counter() - start) * 1000.0
        passed = (exit_code == 0)

        is_preexisting = bool(baseline_failed_commands and command in baseline_failed_commands)
        failure_class = None
        error_summary = None

        if not passed:
            failure_class = cls.classify_failure(command, output_str, exit_code, is_preexisting)
            # Extract first 3 non-empty error lines
            err_lines = [line.strip() for line in output_str.splitlines() if "error" in line.lower() or "fail" in line.lower()]
            error_summary = "; ".join(err_lines[:3]) if err_lines else output_str[:200]

        return TestExecutionResult(
            command=command,
            exit_code=exit_code,
            duration_ms=duration_ms,
            passed=passed,
            output=output_str[:5000],  # bounded output
            failure_classification=failure_class,
            error_summary=error_summary,
        )

    @classmethod
    def run_build(
        cls,
        context: WorkerRuntimeContext,
        command: str,
        timeout_seconds: int = 120,
    ) -> BuildExecutionResult:
        """Run a build validation command via Tool Runtime."""
        start = time.perf_counter()
        exit_code = 0
        output_str = ""

        try:
            context.log.info(f"Executing build command: '{command}'")
            res = context.tools.execute(
                tool_id="shell.execute",
                arguments={"command": command, "action": "execute"},
                timeout_seconds=timeout_seconds,
            )
            if isinstance(res.output, dict):
                exit_code = int(res.output.get("exit_code", 0))
                output_str = str(res.output.get("stdout", "") or res.output.get("output", ""))
            else:
                output_str = str(res.output or "")
                exit_code = 0 if res.is_success else 1
        except Exception as err:
            output_str = f"Build error: {err}"
            exit_code = 1

        duration_ms = (time.perf_counter() - start) * 1000.0
        passed = (exit_code == 0)

        return BuildExecutionResult(
            command=command,
            exit_code=exit_code,
            duration_ms=duration_ms,
            passed=passed,
            output=output_str[:5000],
            error_summary=None if passed else output_str[:300],
        )

    @classmethod
    def evaluate_regressions(
        cls,
        baseline_results: list[TestExecutionResult],
        current_results: list[TestExecutionResult],
    ) -> tuple[bool, list[str]]:
        """
        Compare baseline check outcomes with post-implementation outcomes.
        Returns (has_regressions, regression_descriptions).
        """
        baseline_passed_cmds = {b.command for b in baseline_results if b.passed}
        regressions: list[str] = []

        for curr in current_results:
            if not curr.passed and curr.command in baseline_passed_cmds:
                regressions.append(
                    f"Test regression detected on command '{curr.command}': previously passed in baseline, now failed with exit code {curr.exit_code}."
                )

        return len(regressions) > 0, regressions
