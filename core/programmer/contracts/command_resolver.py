from __future__ import annotations

from dataclasses import dataclass, field
import json
import os
from pathlib import Path
import shlex
from typing import Any, Optional, Union

from core.programmer.contracts.command_scope import AllowedCommand
from core.programmer.contracts.work_order import ProgrammerWorkOrder
from core.programmer.contracts.workspace import ProgrammerWorkspace
from core.programmer.errors import InvalidCommandScopeError
from core.programmer.types import CommandDecisionType


FORBIDDEN_SHELL_TOKENS: list[str] = [
    ";",
    "&&",
    "||",
    "|",
    "&",
    ">",
    "<",
    ">>",
    "<<",
    "$(",
    "`",
    "\n",
    "\r",
]


@dataclass
class CommandRequest:
    """
    Structured representation of a command execution request.
    
    Prevents arbitrary shell strings from being treated as implicitly safe
    by decoupling the executable, argument list, and execution directory.
    """
    executable: str
    arguments: list[str] = field(default_factory=list)
    working_directory: Optional[str] = None
    environment_policy: Optional[dict[str, str]] = None
    trace: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_str(self) -> str:
        """Render a normalized shell-escaped representation of the command."""
        tokens = [self.executable] + list(self.arguments)
        return shlex.join(tokens)

    def to_dict(self) -> dict[str, Any]:
        return {
            "executable": self.executable,
            "arguments": list(self.arguments),
            "working_directory": self.working_directory,
            "environment_policy": dict(self.environment_policy) if self.environment_policy else None,
            "trace": dict(self.trace),
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CommandRequest:
        return cls(
            executable=str(data.get("executable", "")),
            arguments=list(data.get("arguments", [])),
            working_directory=data.get("working_directory"),
            environment_policy=dict(data["environment_policy"]) if data.get("environment_policy") else None,
            trace=dict(data.get("trace", {})),
            metadata=dict(data.get("metadata", {})),
        )

    @classmethod
    def from_raw(
        cls,
        raw_command: str,
        working_directory: Optional[str] = None,
        metadata: Optional[dict[str, Any]] = None,
    ) -> CommandRequest:
        """
        Safely construct a CommandRequest from a raw command string.
        Raises InvalidCommandScopeError if the command contains null bytes or unbalanced quotes.
        """
        if not raw_command or not isinstance(raw_command, str):
            raise InvalidCommandScopeError("Command string must be a non-empty string.")

        clean = raw_command.strip()
        if not clean:
            raise InvalidCommandScopeError("Command string cannot be empty or whitespace only.")

        if "\0" in clean:
            raise InvalidCommandScopeError("Null bytes in command string are strictly forbidden.")

        try:
            tokens = shlex.split(clean)
        except ValueError as err:
            raise InvalidCommandScopeError(f"Malformed command syntax: {err}")

        if not tokens:
            raise InvalidCommandScopeError("Command string produced no tokens.")

        return cls(
            executable=tokens[0],
            arguments=tokens[1:],
            working_directory=working_directory,
            metadata=dict(metadata or {}),
        )


@dataclass
class CommandDecision:
    """
    Structured authorization decision returned by CommandBoundaryResolver.
    """
    allowed: bool
    decision: CommandDecisionType
    normalized_command: str
    reason: str
    matched_rule: Optional[str] = None
    executable: str = ""
    arguments: list[str] = field(default_factory=list)
    working_directory: str = ""
    trace: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if isinstance(self.decision, str):
            try:
                self.decision = CommandDecisionType(self.decision.upper())
            except (ValueError, TypeError):
                self.decision = CommandDecisionType.INVALID

    def to_dict(self) -> dict[str, Any]:
        return {
            "allowed": self.allowed,
            "decision": (
                self.decision.value
                if isinstance(self.decision, CommandDecisionType)
                else str(self.decision)
            ),
            "normalized_command": self.normalized_command,
            "reason": self.reason,
            "matched_rule": self.matched_rule,
            "executable": self.executable,
            "arguments": list(self.arguments),
            "working_directory": self.working_directory,
            "trace": dict(self.trace),
            "metadata": dict(self.metadata),
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=2)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CommandDecision:
        dec_raw = data.get("decision", CommandDecisionType.INVALID.value)
        try:
            decision = CommandDecisionType(str(dec_raw).upper())
        except (ValueError, TypeError):
            decision = CommandDecisionType.INVALID

        return cls(
            allowed=bool(data.get("allowed", False)),
            decision=decision,
            normalized_command=str(data.get("normalized_command", "")),
            reason=str(data.get("reason", "")),
            matched_rule=data.get("matched_rule"),
            executable=str(data.get("executable", "")),
            arguments=list(data.get("arguments", [])),
            working_directory=str(data.get("working_directory", "")),
            trace=dict(data.get("trace", {})),
            metadata=dict(data.get("metadata", {})),
        )


class CommandBoundaryResolver:
    """
    Deterministic policy resolver for Programmer command execution requests.
    
    Decides whether a requested command is authorized by the ProgrammerWorkOrder.
    
    Architectural Constraints:
    - This component is STRICT POLICY.
    - It does NOT execute any commands.
    - It does NOT invoke Cline.
    - Evaluates commands against declared WorkOrder allowed_commands.
    - Prevents command chaining (&&, ||, ;), redirection, pipes, command substitution,
      path traversal, and unauthorized argument expansion.
    """

    def resolve(
        self,
        work_order: ProgrammerWorkOrder,
        request: Union[CommandRequest, str],
        workspace: Optional[ProgrammerWorkspace] = None,
        working_directory: Optional[str] = None,
        metadata: Optional[dict[str, Any]] = None,
    ) -> CommandDecision:
        """
        Evaluate and decide authorization for a command request against the WorkOrder envelope.
        """
        meta = dict(metadata or {})

        # Step 1: Input ingestion and parsing
        raw_str = ""
        cmd_req: CommandRequest
        if isinstance(request, str):
            raw_str = request
            # Immediate raw string check for shell metacharacters before parsing
            for token in FORBIDDEN_SHELL_TOKENS:
                if token in raw_str:
                    return CommandDecision(
                        allowed=False,
                        decision=CommandDecisionType.INVALID,
                        normalized_command=raw_str.strip(),
                        reason=f"Unsupported shell construct detected in command: '{token}'.",
                        matched_rule="UNSUPPORTED_SHELL_CONSTRUCT",
                        trace={"raw_command": raw_str, "forbidden_token": token},
                        metadata=meta,
                    )
            try:
                cmd_req = CommandRequest.from_raw(raw_str, working_directory=working_directory)
            except InvalidCommandScopeError as err:
                return CommandDecision(
                    allowed=False,
                    decision=CommandDecisionType.INVALID,
                    normalized_command=raw_str.strip(),
                    reason=str(err),
                    matched_rule="MALFORMED_COMMAND",
                    trace={"raw_command": raw_str},
                    metadata=meta,
                )
            except Exception as err:
                return CommandDecision(
                    allowed=False,
                    decision=CommandDecisionType.INVALID,
                    normalized_command=raw_str.strip(),
                    reason=f"Failed parsing command: {err}",
                    matched_rule="MALFORMED_COMMAND",
                    trace={"raw_command": raw_str},
                    metadata=meta,
                )
        elif isinstance(request, CommandRequest):
            cmd_req = request
            if working_directory and not cmd_req.working_directory:
                cmd_req.working_directory = working_directory
        else:
            return CommandDecision(
                allowed=False,
                decision=CommandDecisionType.INVALID,
                normalized_command=str(request),
                reason=f"Invalid request type: expected CommandRequest or str, got {type(request).__name__}.",
                matched_rule="INVALID_REQUEST_TYPE",
                metadata=meta,
            )

        # Step 2: Validate structured command tokens
        if not cmd_req.executable or not cmd_req.executable.strip():
            return CommandDecision(
                allowed=False,
                decision=CommandDecisionType.INVALID,
                normalized_command="",
                reason="Executable cannot be empty.",
                matched_rule="EMPTY_EXECUTABLE",
                metadata=meta,
            )

        if "\0" in cmd_req.executable:
            return CommandDecision(
                allowed=False,
                decision=CommandDecisionType.INVALID,
                normalized_command=cmd_req.executable,
                reason="Null bytes in executable are strictly forbidden.",
                matched_rule="MALFORMED_EXECUTABLE",
                metadata=meta,
            )

        # Inspect executable and arguments for shell injection constructs
        all_tokens = [cmd_req.executable] + list(cmd_req.arguments)
        for token in all_tokens:
            if not isinstance(token, str):
                return CommandDecision(
                    allowed=False,
                    decision=CommandDecisionType.INVALID,
                    normalized_command=cmd_req.to_str(),
                    reason=f"Command token must be a string, got {type(token).__name__}.",
                    matched_rule="INVALID_TOKEN_TYPE",
                    metadata=meta,
                )
            if "\0" in token:
                return CommandDecision(
                    allowed=False,
                    decision=CommandDecisionType.INVALID,
                    normalized_command=cmd_req.to_str(),
                    reason="Null bytes in command tokens are strictly forbidden.",
                    matched_rule="MALFORMED_ARGUMENT",
                    metadata=meta,
                )
            for f_token in FORBIDDEN_SHELL_TOKENS:
                if f_token in token:
                    return CommandDecision(
                        allowed=False,
                        decision=CommandDecisionType.INVALID,
                        normalized_command=cmd_req.to_str(),
                        reason=f"Unsupported shell construct or injection operator detected: '{f_token}'.",
                        matched_rule="UNSUPPORTED_SHELL_CONSTRUCT",
                        executable=cmd_req.executable,
                        arguments=list(cmd_req.arguments),
                        trace={"token": token, "forbidden_token": f_token},
                        metadata=meta,
                    )

        norm_command = cmd_req.to_str()

        # Step 3: Working directory confinement
        effective_wd = cmd_req.working_directory or (workspace.root_path if workspace else ".")
        if workspace:
            ws_root = os.path.abspath(workspace.root_path)
            clean_wd = effective_wd.strip()
            if not clean_wd:
                effective_wd = ws_root
            elif os.path.isabs(clean_wd):
                abs_wd = os.path.abspath(clean_wd)
                if abs_wd != ws_root and not abs_wd.startswith(ws_root + os.sep):
                    return CommandDecision(
                        allowed=False,
                        decision=CommandDecisionType.INVALID,
                        normalized_command=norm_command,
                        reason=f"Working directory '{clean_wd}' escapes authorized workspace root '{ws_root}'.",
                        matched_rule="WORKING_DIRECTORY_OUTSIDE_WORKSPACE",
                        executable=cmd_req.executable,
                        arguments=list(cmd_req.arguments),
                        working_directory=clean_wd,
                        metadata=meta,
                    )
                effective_wd = abs_wd
            else:
                abs_wd = os.path.abspath(os.path.join(ws_root, clean_wd))
                if abs_wd != ws_root and not abs_wd.startswith(ws_root + os.sep):
                    return CommandDecision(
                        allowed=False,
                        decision=CommandDecisionType.INVALID,
                        normalized_command=norm_command,
                        reason=f"Working directory '{clean_wd}' escapes authorized workspace root via traversal.",
                        matched_rule="WORKING_DIRECTORY_OUTSIDE_WORKSPACE",
                        executable=cmd_req.executable,
                        arguments=list(cmd_req.arguments),
                        working_directory=clean_wd,
                        metadata=meta,
                    )
                effective_wd = abs_wd

        # Step 4: Argument path traversal check
        if workspace:
            ws_root = os.path.abspath(workspace.root_path)
            for arg in cmd_req.arguments:
                # Check arguments attempting to reference files outside workspace
                if arg.startswith("/") and not arg.startswith(ws_root + os.sep) and arg != ws_root:
                    # Ignore common CLI flag arguments starting with / (e.g. /?)
                    if not (len(arg) == 2 and arg[1] in "?h"):
                        return CommandDecision(
                            allowed=False,
                            decision=CommandDecisionType.DENY,
                            normalized_command=norm_command,
                            reason=f"Argument '{arg}' points to an absolute path outside workspace boundary.",
                            matched_rule="ARGUMENT_TRAVERSAL_DETECTED",
                            executable=cmd_req.executable,
                            arguments=list(cmd_req.arguments),
                            working_directory=effective_wd,
                            metadata=meta,
                        )
                if ".." in arg:
                    # Resolve relative to working directory
                    try:
                        resolved_arg = os.path.abspath(os.path.join(effective_wd, arg))
                        if resolved_arg != ws_root and not resolved_arg.startswith(ws_root + os.sep):
                            return CommandDecision(
                                allowed=False,
                                decision=CommandDecisionType.DENY,
                                normalized_command=norm_command,
                                reason=f"Argument '{arg}' resolves outside authorized workspace boundary.",
                                matched_rule="ARGUMENT_TRAVERSAL_DETECTED",
                                executable=cmd_req.executable,
                                arguments=list(cmd_req.arguments),
                                working_directory=effective_wd,
                                metadata=meta,
                            )
                    except Exception:
                        pass

        # Step 5: Evaluate against work_order.allowed_commands
        if not work_order.allowed_commands:
            return CommandDecision(
                allowed=False,
                decision=CommandDecisionType.DENY,
                normalized_command=norm_command,
                reason="No allowed commands are configured in the work order.",
                matched_rule="NO_ALLOWED_COMMANDS",
                executable=cmd_req.executable,
                arguments=list(cmd_req.arguments),
                working_directory=effective_wd,
                metadata=meta,
            )

        req_tokens = [cmd_req.executable] + list(cmd_req.arguments)
        executable_matched = False

        for rule in work_order.allowed_commands:
            rule_tokens = shlex.split(rule.command.strip())
            if not rule_tokens:
                continue

            rule_exec = rule_tokens[0]
            if cmd_req.executable == rule_exec:
                executable_matched = True

            # Check if requested command begins with rule prefix
            if len(req_tokens) >= len(rule_tokens) and req_tokens[: len(rule_tokens)] == rule_tokens:
                remaining_args = req_tokens[len(rule_tokens) :]

                # Check allowed subcommands if defined
                if rule.allowed_subcommands:
                    if not remaining_args:
                        return CommandDecision(
                            allowed=False,
                            decision=CommandDecisionType.DENY,
                            normalized_command=norm_command,
                            reason=f"Command '{rule.command}' requires a subcommand from {rule.allowed_subcommands}.",
                            matched_rule="MISSING_SUBCOMMAND",
                            executable=cmd_req.executable,
                            arguments=list(cmd_req.arguments),
                            working_directory=effective_wd,
                            metadata=meta,
                        )

                    subcmd = remaining_args[0]
                    if subcmd not in rule.allowed_subcommands:
                        return CommandDecision(
                            allowed=False,
                            decision=CommandDecisionType.DENY,
                            normalized_command=norm_command,
                            reason=f"Subcommand '{subcmd}' is not permitted for '{rule.command}'. Allowed: {rule.allowed_subcommands}.",
                            matched_rule="SUBCOMMAND_NOT_ALLOWED",
                            executable=cmd_req.executable,
                            arguments=list(cmd_req.arguments),
                            working_directory=effective_wd,
                            metadata=meta,
                        )

                    # Check argument allowance after subcommand
                    post_subcmd_args = remaining_args[1:]
                    if not rule.allow_args and post_subcmd_args:
                        return CommandDecision(
                            allowed=False,
                            decision=CommandDecisionType.DENY,
                            normalized_command=norm_command,
                            reason=f"Command '{rule.command} {subcmd}' does not allow additional arguments {post_subcmd_args}.",
                            matched_rule="ARGUMENTS_NOT_ALLOWED",
                            executable=cmd_req.executable,
                            arguments=list(cmd_req.arguments),
                            working_directory=effective_wd,
                            metadata=meta,
                        )

                    return CommandDecision(
                        allowed=True,
                        decision=CommandDecisionType.ALLOW,
                        normalized_command=norm_command,
                        reason=f"Command authorized by rule '{rule.command}' with subcommand '{subcmd}'.",
                        matched_rule=rule.command,
                        executable=cmd_req.executable,
                        arguments=list(cmd_req.arguments),
                        working_directory=effective_wd,
                        metadata=meta,
                    )

                # If no subcommands, check argument allowance
                if not rule.allow_args and remaining_args:
                    return CommandDecision(
                        allowed=False,
                        decision=CommandDecisionType.DENY,
                        normalized_command=norm_command,
                        reason=f"Command '{rule.command}' does not permit additional arguments {remaining_args}.",
                        matched_rule="ARGUMENTS_NOT_ALLOWED",
                        executable=cmd_req.executable,
                        arguments=list(cmd_req.arguments),
                        working_directory=effective_wd,
                        metadata=meta,
                    )

                # Approved!
                return CommandDecision(
                    allowed=True,
                    decision=CommandDecisionType.ALLOW,
                    normalized_command=norm_command,
                    reason=f"Command authorized by rule '{rule.command}'.",
                    matched_rule=rule.command,
                    executable=cmd_req.executable,
                    arguments=list(cmd_req.arguments),
                    working_directory=effective_wd,
                    metadata=meta,
                )

        # Rejection fallback: distinguish between unknown executable vs unauthorized subcommand/args
        if executable_matched:
            return CommandDecision(
                allowed=False,
                decision=CommandDecisionType.DENY,
                normalized_command=norm_command,
                reason=f"Command '{norm_command}' does not match any authorized rule for executable '{cmd_req.executable}'.",
                matched_rule="COMMAND_NOT_AUTHORIZED",
                executable=cmd_req.executable,
                arguments=list(cmd_req.arguments),
                working_directory=effective_wd,
                metadata=meta,
            )

        return CommandDecision(
            allowed=False,
            decision=CommandDecisionType.DENY,
            normalized_command=norm_command,
            reason=f"Executable '{cmd_req.executable}' is not in allowed_commands.",
            matched_rule="EXECUTABLE_NOT_ALLOWED",
            executable=cmd_req.executable,
            arguments=list(cmd_req.arguments),
            working_directory=effective_wd,
            metadata=meta,
        )
