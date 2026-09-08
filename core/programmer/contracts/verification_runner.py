from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import json
import logging
import os
from pathlib import Path
import shlex
import subprocess
import time
from typing import Any, Callable, Optional, Sequence, Union

from core.programmer.contracts.command_resolver import CommandDecision, CommandRequest
from core.programmer.contracts.execution_context import ProgrammerExecutionContext
from core.programmer.contracts.identifiers import (
    new_verification_check_id,
    new_verification_evidence_id,
    validate_execution_id,
    validate_work_order_id,
)
from core.programmer.contracts.verification import (
    VerificationCheck,
    VerificationEvidence,
)
from core.programmer.errors import (
    InvalidCommandScopeError,
    ProgrammerError,
    ProgrammerLineageError,
    ProgrammerValidationError,
)
from core.programmer.types import (
    CommandDecisionType,
    ExecutionContextStatus,
    VerificationCheckType,
    VerificationEvidenceSourceType,
    VerificationStatus,
)

logger = logging.getLogger("AutonomOS.Programmer.VerificationRunner")


def utc_now() -> str:
    """Generate ISO 8601 UTC timestamp."""
    return datetime.now(timezone.utc).isoformat()


@dataclass
class VerificationRunnerResult:
    """
    Structured outcome contract returned by VerificationRunner.
    
    Guarantees:
    - Maintains causal lineage back to execution_id and work_order_id.
    - Aggregates all produced VerificationCheck and VerificationEvidence records.
    - Accurately reports duration, completion, cancellation, and budget status.
    """
    checks: list[VerificationCheck] = field(default_factory=list)
    evidence: list[VerificationEvidence] = field(default_factory=list)
    execution_id: str = ""
    work_order_id: str = ""
    total_duration_ms: float = 0.0
    completed: bool = True
    cancelled: bool = False
    budget_exhausted: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "checks": [c.to_dict() for c in self.checks],
            "evidence": [e.to_dict() for e in self.evidence],
            "execution_id": self.execution_id,
            "work_order_id": self.work_order_id,
            "total_duration_ms": self.total_duration_ms,
            "completed": self.completed,
            "cancelled": self.cancelled,
            "budget_exhausted": self.budget_exhausted,
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> VerificationRunnerResult:
        return cls(
            checks=[VerificationCheck.from_dict(c) for c in data.get("checks", [])],
            evidence=[VerificationEvidence.from_dict(e) for e in data.get("evidence", [])],
            execution_id=str(data.get("execution_id", "")),
            work_order_id=str(data.get("work_order_id", "")),
            total_duration_ms=float(data.get("total_duration_ms", 0.0)),
            completed=bool(data.get("completed", True)),
            cancelled=bool(data.get("cancelled", False)),
            budget_exhausted=bool(data.get("budget_exhausted", False)),
            metadata=dict(data.get("metadata", {})),
        )


class VerificationRunner:
    """
    Deterministic verification runner for the AutonomOS Programmer subsystem.
    
    Architecture:
        Programmer
            ↓
        Required Checks (from ProgrammerWorkOrder)
            ↓
        VerificationRunner
            ↓
        Command Boundary (CommandBoundaryResolver)
            ↓
        Command Executor (Confined subprocess inside workspace)
            ↓
        Actual Result
            ↓
        VerificationCheck + Authoritative VerificationEvidence
    
    Architectural Guarantees:
    1. NEVER trust coding agent (Cline) narration or self-confidence scores.
    2. Verification commands strictly obey CommandBoundaryResolver, WorkOrder allowed_commands,
       ExecutionContext boundaries, workspace root confinement, and execution budgets.
    3. Unauthorized commands are NEVER executed and produce NOT_RUN / ERROR records with reasons.
    4. Malformed check definitions fail deterministically without process execution.
    5. All observed outcomes create immutable, authoritative VerificationEvidence records.
    6. Does NOT build a CI/CD engine, install dependencies, expand permissions, or evaluate acceptance.
    """

    def __init__(
        self,
        context: ProgrammerExecutionContext,
        command_executor: Optional[Callable[[list[str], str, int], Any]] = None,
        default_timeout_seconds: int = 60,
    ):
        if context is None:
            raise ProgrammerValidationError("ProgrammerExecutionContext cannot be None.")
        self.context = context
        self.command_executor = command_executor
        self.default_timeout_seconds = default_timeout_seconds
        self._cancelled = False
        self._active_processes: dict[str, Any] = {}

    @property
    def execution_id(self) -> str:
        return self.context.execution_id

    @property
    def work_order_id(self) -> str:
        return self.context.work_order_id

    def cancel(self, reason: str = "Verification cancelled") -> None:
        """Signal cancellation to the verification runner and terminate any active check processes."""
        self._cancelled = True
        for chk_id, proc in list(self._active_processes.items()):
            try:
                if hasattr(proc, "poll") and proc.poll() is None:
                    proc.terminate()
                    try:
                        proc.wait(timeout=1.0)
                    except Exception:
                        if hasattr(proc, "kill"):
                            proc.kill()
                            proc.wait(timeout=1.0)
            except Exception as err:
                logger.warning(f"Error terminating active verification process {chk_id}: {err}")
            finally:
                self._active_processes.pop(chk_id, None)

    def is_cancelled(self) -> bool:
        """Check whether verification has been cancelled."""
        if self._cancelled:
            return True
        if self.context.status == ExecutionContextStatus.FAILED:
            msg = str(self.context.error_message or "").lower()
            if "cancel" in msg:
                return True
        return False

    def _infer_check_type(self, command_str: str, explicit_type: Optional[str] = None) -> VerificationCheckType:
        """Infer VerificationCheckType from explicit input or command tokens."""
        if explicit_type:
            try:
                return VerificationCheckType(str(explicit_type).upper())
            except (ValueError, TypeError):
                pass

        cmd_lower = command_str.lower().strip()
        tokens = cmd_lower.split()
        first_token = tokens[0] if tokens else ""

        if "pytest" in first_token or "test" in first_token:
            return VerificationCheckType.TEST
        if "mypy" in first_token or "pyright" in first_token or "tsc" in first_token or "typecheck" in cmd_lower:
            return VerificationCheckType.TYPECHECK
        if "ruff" in first_token or "flake8" in first_token or "eslint" in first_token or "lint" in cmd_lower:
            return VerificationCheckType.LINT
        if "build" in first_token or ("build" in tokens and tokens[0] in ("cargo", "go", "npm", "yarn", "make")):
            return VerificationCheckType.BUILD

        return VerificationCheckType.COMMAND

    def _parse_check_definition(
        self,
        check_def: Any,
    ) -> tuple[str, VerificationCheckType, int, Optional[str], Optional[str]]:
        """
        Validate and parse a check definition into (command_str, check_type, timeout, working_dir, error_msg).
        Returns error_msg if malformed.
        """
        if isinstance(check_def, str):
            clean = check_def.strip()
            if not clean:
                return "", VerificationCheckType.CUSTOM, self.default_timeout_seconds, None, "Check command string cannot be empty."
            if "\0" in clean:
                return "", VerificationCheckType.CUSTOM, self.default_timeout_seconds, None, "Null bytes forbidden in check command."
            try:
                tokens = shlex.split(clean)
                if not tokens:
                    return "", VerificationCheckType.CUSTOM, self.default_timeout_seconds, None, "Check command produced no tokens."
            except ValueError as err:
                return "", VerificationCheckType.CUSTOM, self.default_timeout_seconds, None, f"Malformed command syntax: {err}"

            check_type = self._infer_check_type(clean)
            return clean, check_type, self.default_timeout_seconds, None, None

        elif isinstance(check_def, dict):
            raw_cmd = check_def.get("command") or check_def.get("cmd")
            if not raw_cmd or not isinstance(raw_cmd, str) or not raw_cmd.strip():
                return "", VerificationCheckType.CUSTOM, self.default_timeout_seconds, None, "Check definition dict must contain a non-empty 'command'."
            clean = raw_cmd.strip()
            if "\0" in clean:
                return "", VerificationCheckType.CUSTOM, self.default_timeout_seconds, None, "Null bytes forbidden in check command."
            try:
                tokens = shlex.split(clean)
                if not tokens:
                    return "", VerificationCheckType.CUSTOM, self.default_timeout_seconds, None, "Check command produced no tokens."
            except ValueError as err:
                return "", VerificationCheckType.CUSTOM, self.default_timeout_seconds, None, f"Malformed command syntax: {err}"

            check_type = self._infer_check_type(clean, check_def.get("type") or check_def.get("check_type"))
            timeout = int(check_def.get("timeout_seconds") or self.default_timeout_seconds)
            working_dir = check_def.get("working_directory")
            return clean, check_type, timeout, working_dir, None

        elif hasattr(check_def, "command"):
            clean = str(check_def.command).strip()
            if not clean:
                return "", VerificationCheckType.CUSTOM, self.default_timeout_seconds, None, "Check definition command cannot be empty."
            check_type = getattr(check_def, "check_type", None) or self._infer_check_type(clean)
            timeout = getattr(check_def, "timeout_seconds", self.default_timeout_seconds)
            working_dir = getattr(check_def, "working_directory", None)
            return clean, check_type, timeout, working_dir, None

        else:
            return "", VerificationCheckType.CUSTOM, self.default_timeout_seconds, None, f"Unsupported check definition type: {type(check_def).__name__}"

    def run_checks(
        self,
        checks: Optional[Sequence[Any]] = None,
    ) -> VerificationRunnerResult:
        """
        Execute required verification checks in sequence and record observed outcomes.
        
        Args:
            checks: Optional explicit list of check definitions. If None, uses
                    `self.context.work_order.required_checks`.
        
        Returns:
            VerificationRunnerResult containing all VerificationCheck and VerificationEvidence records.
        """
        # Resolve checks from work order if not explicitly supplied
        resolved_checks: list[Any] = []
        if checks is not None:
            resolved_checks = list(checks)
        elif self.context.work_order and hasattr(self.context.work_order, "required_checks"):
            resolved_checks = list(self.context.work_order.required_checks)

        result = VerificationRunnerResult(
            execution_id=self.execution_id,
            work_order_id=self.work_order_id,
        )

        if not resolved_checks:
            return result

        # Extract budget constraints from context
        time_budget = None
        check_budget = None
        if self.context.budgets:
            time_budget = self.context.budgets.get("time_budget")
            check_budget = (
                self.context.budgets.get("verification_budget")
                or self.context.budgets.get("max_checks")
                or self.context.budgets.get("check_budget")
            )
        elif self.context.work_order and hasattr(self.context.work_order, "time_budget"):
            time_budget = self.context.work_order.time_budget

        total_start_time = time.monotonic()
        executed_checks_count = 0

        for check_def in resolved_checks:
            check_id = new_verification_check_id()
            elapsed_seconds = time.monotonic() - total_start_time

            # 1. Check Cancellation
            if self.is_cancelled():
                result.cancelled = True
                result.completed = False
                chk = VerificationCheck(
                    check_id=check_id,
                    execution_id=self.execution_id,
                    work_order_id=self.work_order_id,
                    status=VerificationStatus.NOT_RUN,
                    output_snippet="Check not run: verification cancelled.",
                    metadata={"reason": "CANCELLED"},
                )
                result.checks.append(chk)
                continue

            # 2. Check Time Budget Exhaustion
            if time_budget is not None and elapsed_seconds >= float(time_budget):
                result.budget_exhausted = True
                result.completed = False
                chk = VerificationCheck(
                    check_id=check_id,
                    execution_id=self.execution_id,
                    work_order_id=self.work_order_id,
                    status=VerificationStatus.NOT_RUN,
                    output_snippet="Check not run: verification time budget exhausted.",
                    metadata={"reason": "BUDGET_EXHAUSTED", "budget_type": "time_budget"},
                )
                result.checks.append(chk)
                continue

            # 3. Check Count Budget Exhaustion
            if check_budget is not None and executed_checks_count >= int(check_budget):
                result.budget_exhausted = True
                result.completed = False
                chk = VerificationCheck(
                    check_id=check_id,
                    execution_id=self.execution_id,
                    work_order_id=self.work_order_id,
                    status=VerificationStatus.NOT_RUN,
                    output_snippet="Check not run: verification check budget exhausted.",
                    metadata={"reason": "BUDGET_EXHAUSTED", "budget_type": "check_budget"},
                )
                result.checks.append(chk)
                continue

            # 4. Validate check definition
            cmd_str, check_type, timeout, working_dir, parse_err = self._parse_check_definition(check_def)
            if parse_err:
                chk = VerificationCheck(
                    check_id=check_id,
                    execution_id=self.execution_id,
                    work_order_id=self.work_order_id,
                    check_type=check_type,
                    command=str(check_def) if isinstance(check_def, str) else None,
                    status=VerificationStatus.ERROR,
                    output_snippet=f"Malformed check definition: {parse_err}",
                    metadata={"error": parse_err, "malformed": True},
                )
                result.checks.append(chk)
                executed_checks_count += 1
                continue

            # 5. Confirm permission via CommandBoundaryResolver
            decision: CommandDecision = self.context.may_execute_command(
                request=cmd_str,
                working_directory=working_dir,
            )

            if not decision.allowed:
                # Unauthorized command: DO NOT EXECUTE
                chk = VerificationCheck(
                    check_id=check_id,
                    execution_id=self.execution_id,
                    work_order_id=self.work_order_id,
                    check_type=check_type,
                    command=cmd_str,
                    status=VerificationStatus.NOT_RUN,
                    output_snippet=f"Command denied by policy: {decision.reason}",
                    metadata={
                        "denial_reason": decision.reason,
                        "matched_rule": decision.matched_rule,
                        "decision": decision.decision.value,
                    },
                    trace=decision.trace,
                )
                result.checks.append(chk)
                executed_checks_count += 1
                continue

            # 6. Execute permitted command
            executed_checks_count += 1
            chk, ev = self._execute_check(
                check_id=check_id,
                cmd_str=cmd_str,
                check_type=check_type,
                working_directory=working_dir,
                timeout_seconds=timeout,
                trace=decision.trace,
            )
            result.checks.append(chk)
            if ev is not None:
                result.evidence.append(ev)

        result.total_duration_ms = float(int((time.monotonic() - total_start_time) * 1000))
        return result

    def _execute_check(
        self,
        check_id: str,
        cmd_str: str,
        check_type: VerificationCheckType,
        working_directory: Optional[str],
        timeout_seconds: int,
        trace: dict[str, Any],
    ) -> tuple[VerificationCheck, Optional[VerificationEvidence]]:
        """Execute an authorized verification command and capture observed results."""
        started_at = utc_now()
        start_t = time.monotonic()

        try:
            cwd = self.context.resolve_working_directory(working_directory)
        except Exception as cwd_err:
            duration_ms = float(int((time.monotonic() - start_t) * 1000))
            chk = VerificationCheck(
                check_id=check_id,
                execution_id=self.execution_id,
                work_order_id=self.work_order_id,
                check_type=check_type,
                command=cmd_str,
                status=VerificationStatus.ERROR,
                duration_ms=duration_ms,
                started_at=started_at,
                completed_at=utc_now(),
                output_snippet=f"Failed resolving working directory: {cwd_err}",
                metadata={"error": str(cwd_err)},
                trace=trace,
            )
            return chk, None

        tokens = shlex.split(cmd_str)
        exit_code: Optional[int] = None
        stdout = ""
        stderr = ""
        is_timeout = False
        exec_error: Optional[str] = None

        try:
            if self.command_executor is not None:
                exec_res = self.command_executor(tokens, cwd, timeout_seconds)
                if isinstance(exec_res, tuple) and len(exec_res) >= 3:
                    exit_code, stdout, stderr = exec_res[0], str(exec_res[1]), str(exec_res[2])
                elif isinstance(exec_res, dict):
                    exit_code = exec_res.get("exit_code", 0)
                    stdout = str(exec_res.get("stdout", ""))
                    stderr = str(exec_res.get("stderr", ""))
                elif hasattr(exec_res, "returncode"):
                    exit_code = exec_res.returncode
                    stdout = str(exec_res.stdout or "")
                    stderr = str(exec_res.stderr or "")
                else:
                    exit_code = 0
            else:
                proc = subprocess.Popen(
                    tokens,
                    cwd=cwd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                )
                self._active_processes[check_id] = proc
                try:
                    stdout, stderr = proc.communicate(timeout=timeout_seconds)
                    exit_code = proc.returncode
                finally:
                    self._active_processes.pop(check_id, None)
        except (subprocess.TimeoutExpired, TimeoutError) as timeout_err:
            is_timeout = True
            exec_error = f"Command timed out after {timeout_seconds} seconds: {timeout_err}"
        except Exception as err:
            exec_error = f"Execution error: {err}"

        duration_ms = float(int((time.monotonic() - start_t) * 1000))
        completed_at = utc_now()

        # Determine verification status
        if is_timeout:
            status = VerificationStatus.ERROR
            output_snippet = exec_error or "Command timed out."
        elif exec_error:
            status = VerificationStatus.ERROR
            output_snippet = exec_error
        elif exit_code == 0:
            status = VerificationStatus.PASS
            output_snippet = (stdout.strip() or stderr.strip())[:500]
        else:
            status = VerificationStatus.FAIL
            output_snippet = (stderr.strip() or stdout.strip())[:500]

        # Construct authoritative VerificationEvidence
        evidence_source = (
            VerificationEvidenceSourceType.TEST_RUNNER
            if check_type == VerificationCheckType.TEST
            else (
                VerificationEvidenceSourceType.STATIC_ANALYSIS
                if check_type in (VerificationCheckType.TYPECHECK, VerificationCheckType.LINT)
                else VerificationEvidenceSourceType.COMMAND_OUTPUT
            )
        )

        ev = VerificationEvidence(
            evidence_id=new_verification_evidence_id(),
            execution_id=self.execution_id,
            work_order_id=self.work_order_id,
            source_type=evidence_source,
            source_reference=cmd_str,
            description=f"Observed output of check '{check_id}' ({cmd_str})",
            is_agent_claim=False,  # CRITICAL: actual execution observation, never an agent narration
            data={
                "command": cmd_str,
                "exit_code": exit_code,
                "duration_ms": duration_ms,
                "stdout": stdout,
                "stderr": stderr,
                "is_timeout": is_timeout,
                "error": exec_error,
            },
            metadata={"check_id": check_id, "check_type": check_type.value},
        )
        ev.validate()

        chk = VerificationCheck(
            check_id=check_id,
            execution_id=self.execution_id,
            work_order_id=self.work_order_id,
            check_type=check_type,
            command=cmd_str,
            status=status,
            exit_code=exit_code,
            duration_ms=duration_ms,
            output_reference=f"evidence:{ev.evidence_id}",
            output_snippet=output_snippet,
            started_at=started_at,
            completed_at=completed_at,
            evidence=[ev.evidence_id],
            trace=trace,
            metadata={"timeout_seconds": timeout_seconds, "working_directory": cwd},
        )
        chk.validate()

        return chk, ev
