from __future__ import annotations

import re
import time
from typing import Any, Optional
import uuid

from core.enums import ArtifactType, ToolStatus
from core.models import Evidence, utc_now
from core.tools.model import ToolRequest
from core.verification.adapters.base import BaseCheckAdapter, CheckExecutionContext
from core.verification.model import SuccessCriterion, VerificationCheck
from core.verification.types import CheckStatus, CheckType


class CommandCheckAdapter(BaseCheckAdapter):
    """
    Adapter for deterministic command-based verification checks (Tests, Builds, Compiles, Lints, Static Analysis).
    Executes commands securely through the Tool Runtime.
    """

    SUPPORTED = {
        CheckType.BUILD,
        CheckType.COMPILE,
        CheckType.TEST_SUITE,
        CheckType.TEST_CASE,
        CheckType.LINT,
        CheckType.TYPE_CHECK,
        CheckType.COMMAND_EXIT_CODE,
        CheckType.COMMAND_OUTPUT_MATCH,
    }

    def supports(self, check_type: CheckType) -> bool:
        return check_type in self.SUPPORTED

    def execute(
        self,
        context: CheckExecutionContext,
        criterion: SuccessCriterion,
        verification_id: str,
    ) -> VerificationCheck:
        start_time = time.perf_counter()
        check_id = f"chk-{uuid.uuid4().hex[:8]}"

        command = str(criterion.parameters.get("command") or "")
        args = list(criterion.parameters.get("args") or [])
        expected_exit_code = int(criterion.parameters.get("expected_exit_code", 0))
        output_pattern = criterion.parameters.get("pattern") or criterion.parameters.get("expected_output")
        timeout_seconds = criterion.parameters.get("timeout_seconds")

        if not command and args:
            command = " ".join(args)

        status = CheckStatus.FAILED
        expected = {"exit_code": expected_exit_code}
        if output_pattern:
            expected["output_pattern"] = str(output_pattern)

        actual: dict[str, Any] = {}
        error_msg: Optional[str] = None
        evidence_ids: list[str] = []

        if not command:
            return VerificationCheck(
                id=check_id,
                verification_id=verification_id,
                criterion_id=criterion.id,
                check_type=criterion.check_type,
                description=criterion.description or "Empty command verification check",
                expected_result=expected,
                actual_result="MISSING_COMMAND",
                status=CheckStatus.ERROR,
                required=criterion.required,
                error_message="No command string provided for command verification check.",
                duration_ms=(time.perf_counter() - start_time) * 1000.0,
            )

        # Execute through Tool Runtime
        req = ToolRequest(
            project_id=context.project_id,
            task_id=context.task_id,
            worker_id=context.worker_id or "verifier",
            tool_id="shell.execute",
            arguments={"command": command, "timeout_seconds": timeout_seconds},
        )

        tool_result = context.tool_runtime.execute_request(req)

        if tool_result.status == ToolStatus.TIMEOUT:
            status = CheckStatus.UNCERTAIN
            error_msg = f"Command timed out after {timeout_seconds or 30}s: '{command}'"
            actual = {"status": "TIMEOUT", "error": tool_result.error_message}

        elif tool_result.status == ToolStatus.DENIED:
            status = CheckStatus.ERROR
            error_msg = f"Command execution denied by safety/permissions: {tool_result.error_message}"
            actual = {"status": "DENIED", "error": tool_result.error_message}

        elif tool_result.output and isinstance(tool_result.output, dict):
            exit_code = tool_result.output.get("exit_code", -1)
            stdout = tool_result.output.get("stdout", "")
            stderr = tool_result.output.get("stderr", "")
            combined_output = f"{stdout}\n{stderr}".strip()

            actual = {"exit_code": exit_code, "output_snippet": combined_output[:300]}

            # Validate Exit Code
            code_matches = (exit_code == expected_exit_code)

            # Validate Output Pattern if requested
            pattern_matches = True
            if output_pattern:
                is_regex = bool(criterion.parameters.get("is_regex", False))
                pattern_matches = bool(re.search(str(output_pattern), combined_output)) if is_regex else str(output_pattern) in combined_output

            if code_matches and pattern_matches:
                status = CheckStatus.PASSED
            else:
                status = CheckStatus.FAILED
                reasons = []
                if not code_matches:
                    reasons.append(f"exit_code {exit_code} != expected {expected_exit_code}")
                if not pattern_matches:
                    reasons.append(f"output did not match '{output_pattern}'")
                error_msg = f"Command verification failed ({'; '.join(reasons)}). Output:\n{combined_output[:400]}"

            # Save full output as an artifact if lengthy
            if len(combined_output) > 200 and context.artifact_registry:
                try:
                    artifact = context.artifact_registry.register_artifact(
                        project_id=context.project_id,
                        task_id=context.task_id,
                        worker_id=context.worker_id or "verifier",
                        artifact_type=ArtifactType.LOG,
                        relative_path=f".autonomos/evidence/cmd_{check_id}.log",
                        description=f"Command check log for {criterion.check_type.value}: {command[:50]}",
                        content=combined_output,
                    )
                except Exception:
                    pass

        else:
            status = CheckStatus.ERROR
            error_msg = f"Unexpected tool execution result: {tool_result.error_message or 'No output returned'}"
            actual = {"error": tool_result.error_message}

        # Create Evidence record
        evidence = Evidence(
            id=f"ev-cmd-{uuid.uuid4().hex[:8]}",
            task_id=context.task_id,
            evidence_type="COMMAND_VERIFICATION",
            data=f"Command: '{command[:80]}' | Status: {status.value} | Result: {actual}",
            created_at=utc_now(),
        )
        evidence_ids.append(evidence.id)

        duration_ms = (time.perf_counter() - start_time) * 1000.0

        return VerificationCheck(
            id=check_id,
            verification_id=verification_id,
            criterion_id=criterion.id,
            check_type=criterion.check_type,
            description=criterion.description or f"{criterion.check_type.value}: `{command[:60]}`",
            expected_result=expected,
            actual_result=actual,
            status=status,
            required=criterion.required,
            evidence_ids=evidence_ids,
            error_message=error_msg,
            duration_ms=duration_ms,
            metadata={"command": command},
        )
