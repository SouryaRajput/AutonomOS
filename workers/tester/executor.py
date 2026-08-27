from __future__ import annotations

import re
import time
from typing import Any, Optional
import uuid

from pkg.sdk.worker import WorkerRuntimeContext
from workers.tester.model import (
    TestResult,
    TestSuiteResult,
    TestingScope,
)
from workers.tester.types import TestCategory, TestExecutionStatus


class TestExecutor:
    """
    Executes test commands through Tool Runtime, captures outputs,
    evaluates baseline regressions, and parses structured test results.
    """

    def __init__(self, context: WorkerRuntimeContext, scope: Optional[TestingScope] = None):
        self.context = context
        self.scope = scope or TestingScope()
        self.command_count = 0

    def discover_default_test_command(self, test_files: list[str]) -> str:
        # Detect framework based on files
        if any(f.endswith(".py") for f in test_files):
            return "python3 -m unittest discover -s test -p 'test_*.py'"
        if any(f.endswith(".dart") for f in test_files) or any("flutter" in f for f in test_files):
            return "flutter test"
        if any(f.endswith(".js") or f.endswith(".ts") for f in test_files):
            return "npm test"
        if any(f.endswith(".rs") for f in test_files):
            return "cargo test"
        return "python3 -m unittest"

    def run_command(
        self,
        command: str,
        category: TestCategory = TestCategory.UNIT,
        timeout_seconds: Optional[int] = None,
        is_baseline: bool = False,
    ) -> TestResult:
        test_id = f"t-{uuid.uuid4().hex[:8]}"
        self.command_count += 1

        timeout = timeout_seconds or min(self.scope.max_test_duration_s, 60)
        start_t = time.perf_counter()

        self.context.log.info(f"Executing test command [{category.value}]: {command}")
        
        try:
            tool_res = self.context.tools.execute(
                tool_id="shell.execute",
                arguments={"command": command, "timeout_seconds": timeout},
            )
            duration_ms = (time.perf_counter() - start_t) * 1000.0

            exit_code = 0
            stdout_text = ""
            stderr_text = ""

            if tool_res and isinstance(tool_res.output, dict):
                exit_code = int(tool_res.output.get("exit_code", 0))
                stdout_text = str(tool_res.output.get("stdout", ""))
                stderr_text = str(tool_res.output.get("stderr", ""))
            elif tool_res:
                stdout_text = str(tool_res.output or "")

            combined_output = (stdout_text + "\n" + stderr_text).strip()
            status = TestExecutionStatus.PASSED if exit_code == 0 else TestExecutionStatus.FAILED

            err_msg = ""
            if status == TestExecutionStatus.FAILED:
                err_msg = stderr_text.strip() or stdout_text.strip()[:300]

            return TestResult(
                test_id=test_id,
                name=f"Command: {command[:50]}",
                category=category,
                status=status,
                duration_ms=duration_ms,
                exit_code=exit_code,
                command=command,
                output_snippet=combined_output[:4000],
                error_message=err_msg[:500],
                is_baseline=is_baseline,
            )
        except Exception as e:
            duration_ms = (time.perf_counter() - start_t) * 1000.0
            return TestResult(
                test_id=test_id,
                name=f"Command: {command[:50]}",
                category=category,
                status=TestExecutionStatus.ERROR,
                duration_ms=duration_ms,
                exit_code=1,
                command=command,
                output_snippet=f"Execution exception: {e}",
                error_message=str(e),
                is_baseline=is_baseline,
            )

    def execute_test_suite(
        self,
        suite_name: str,
        commands: list[tuple[str, TestCategory]],
        baseline_results: Optional[dict[str, TestExecutionStatus]] = None,
    ) -> TestSuiteResult:
        suite_id = f"suite-{uuid.uuid4().hex[:8]}"
        test_results: list[TestResult] = []
        
        suite_start = time.perf_counter()

        for cmd, cat in commands:
            if self.command_count >= self.scope.max_test_commands:
                self.context.log.warning(f"Test budget limit reached ({self.scope.max_test_commands} commands).")
                break

            result = self.run_command(cmd, category=cat)
            
            # Check baseline regression
            if baseline_results and cmd in baseline_results:
                base_status = baseline_results[cmd]
                if base_status == TestExecutionStatus.PASSED and result.status == TestExecutionStatus.FAILED:
                    result.is_regression = True

            test_results.append(result)

        total_duration = (time.perf_counter() - suite_start) * 1000.0

        passed = sum(1 for t in test_results if t.status == TestExecutionStatus.PASSED)
        failed = sum(1 for t in test_results if t.status == TestExecutionStatus.FAILED)
        errors = sum(1 for t in test_results if t.status == TestExecutionStatus.ERROR)
        skipped = sum(1 for t in test_results if t.status == TestExecutionStatus.SKIPPED)

        return TestSuiteResult(
            suite_id=suite_id,
            suite_name=suite_name,
            total=len(test_results),
            passed=passed,
            failed=failed,
            skipped=skipped,
            errors=errors,
            duration_ms=total_duration,
            test_results=test_results,
            environment_info={"os": "system", "command_count": len(test_results)},
        )
