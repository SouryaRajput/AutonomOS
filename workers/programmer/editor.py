from __future__ import annotations

import difflib
import logging
from typing import Optional

from pkg.sdk.worker import WorkerRuntimeContext
from workers.programmer.model import (
    FileChange,
    ProgrammingScope,
    compute_checksum,
)
from workers.programmer.scope import ScopeGuard
from workers.programmer.types import FileChangeType

logger = logging.getLogger("AutonomOS.Programmer.Editor")


class ScopeViolationError(Exception):
    """Raised when an operation attempts to access an out-of-scope path."""
    pass


class CodeEditor:
    """
    Applies controlled filesystem modifications strictly through the Tool Runtime
    while generating unified diffs, tracking checksums, and enforcing scope boundaries.
    """

    def __init__(self, context: WorkerRuntimeContext, scope: ProgrammingScope):
        self.context = context
        self.scope = scope
        self.changes: list[FileChange] = []

    def _generate_diff(self, path: str, old_content: str, new_content: str) -> str:
        """Generate unified diff between original and modified text."""
        old_lines = old_content.splitlines(keepends=True)
        new_lines = new_content.splitlines(keepends=True)
        diff_lines = list(
            difflib.unified_diff(
                old_lines,
                new_lines,
                fromfile=f"a/{path}",
                tofile=f"b/{path}",
            )
        )
        return "".join(diff_lines)

    def write_file(
        self,
        path: str,
        content: str,
        description: str = "",
        overwrite: bool = True,
    ) -> FileChange:
        """Create or overwrite a file through Tool Runtime."""
        valid, reason = ScopeGuard.validate_path(path, self.scope)
        if not valid:
            raise ScopeViolationError(f"Scope violation writing to '{path}': {reason}")

        # Check existing content for diff and checksum
        old_content = ""
        old_checksum: Optional[str] = None
        try:
            read_res = self.context.tools.execute(
                tool_id="filesystem.read_file",
                arguments={"path": path, "action": "read_file"},
            )
            if read_res.output is not None:
                old_content = str(read_res.output)
                old_checksum = compute_checksum(old_content)
        except Exception:
            old_content = ""

        # Execute write via Tool Runtime
        self.context.tools.execute(
            tool_id="filesystem.write_file",
            arguments={"path": path, "content": content, "overwrite": overwrite, "action": "write_file"},
        )

        new_checksum = compute_checksum(content)
        change_type = FileChangeType.MODIFY if old_checksum else FileChangeType.CREATE
        diff = self._generate_diff(path, old_content, content)

        change = FileChange(
            path=path,
            change_type=change_type,
            original_checksum=old_checksum,
            new_checksum=new_checksum,
            diff=diff,
            description=description or f"{'Updated' if old_checksum else 'Created'} {path}",
        )
        self.changes.append(change)
        return change

    def delete_file(self, path: str, description: str = "") -> FileChange:
        """Delete a file through Tool Runtime."""
        valid, reason = ScopeGuard.validate_path(path, self.scope)
        if not valid:
            raise ScopeViolationError(f"Scope violation deleting '{path}': {reason}")

        old_content = ""
        old_checksum: Optional[str] = None
        try:
            read_res = self.context.tools.execute(
                tool_id="filesystem.read_file",
                arguments={"path": path, "action": "read_file"},
            )
            if read_res.output is not None:
                old_content = str(read_res.output)
                old_checksum = compute_checksum(old_content)
        except Exception:
            pass

        self.context.tools.execute(
            tool_id="filesystem.delete_file",
            arguments={"path": path, "action": "delete_file"},
        )

        diff = self._generate_diff(path, old_content, "")
        change = FileChange(
            path=path,
            change_type=FileChangeType.DELETE,
            original_checksum=old_checksum,
            new_checksum=None,
            diff=diff,
            description=description or f"Deleted {path}",
        )
        self.changes.append(change)
        return change

    def rename_file(self, source_path: str, destination_path: str, description: str = "") -> FileChange:
        """Rename or move a file through Tool Runtime."""
        valid_src, reason_src = ScopeGuard.validate_path(source_path, self.scope)
        if not valid_src:
            raise ScopeViolationError(f"Scope violation on rename source '{source_path}': {reason_src}")

        valid_dst, reason_dst = ScopeGuard.validate_path(destination_path, self.scope)
        if not valid_dst:
            raise ScopeViolationError(f"Scope violation on rename destination '{destination_path}': {reason_dst}")

        old_content = ""
        try:
            read_res = self.context.tools.execute(
                tool_id="filesystem.read_file",
                arguments={"path": source_path, "action": "read_file"},
            )
            if read_res.output is not None:
                old_content = str(read_res.output)
        except Exception:
            pass

        # Write to destination and delete source via Tool Runtime
        self.context.tools.execute(
            tool_id="filesystem.write_file",
            arguments={"path": destination_path, "content": old_content, "overwrite": True, "action": "write_file"},
        )
        self.context.tools.execute(
            tool_id="filesystem.delete_file",
            arguments={"path": source_path, "action": "delete_file"},
        )

        checksum = compute_checksum(old_content)
        change = FileChange(
            path=destination_path,
            change_type=FileChangeType.RENAME,
            original_checksum=checksum,
            new_checksum=checksum,
            diff=f"Renamed {source_path} -> {destination_path}",
            description=description or f"Renamed {source_path} to {destination_path}",
            old_path=source_path,
        )
        self.changes.append(change)
        return change
