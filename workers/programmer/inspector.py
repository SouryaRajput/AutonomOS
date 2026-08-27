from __future__ import annotations

import logging
from typing import Any, Optional

from pkg.sdk.worker import WorkerRuntimeContext

logger = logging.getLogger("AutonomOS.Programmer.Inspector")


class RepositoryInspector:
    """
    Controlled inspection of repository files, structures, and version-control status
    strictly routed through the Tool Runtime.
    """

    @classmethod
    def list_files(
        cls,
        context: WorkerRuntimeContext,
        subpath: str = "",
        recursive: bool = True,
    ) -> list[str]:
        """List files in the workspace directory via Tool Runtime."""
        try:
            res = context.tools.execute(
                tool_id="filesystem.list_directory",
                arguments={"path": subpath or ".", "recursive": recursive, "action": "list_directory"},
            )
            if res.output:
                if isinstance(res.output, list):
                    return [str(f) for f in res.output]
                elif isinstance(res.output, dict) and "files" in res.output:
                    return [str(f) for f in res.output["files"]]
            return []
        except Exception as err:
            context.log.warning(f"Error listing files in '{subpath}': {err}")
            return []

    @classmethod
    def read_file(
        cls,
        context: WorkerRuntimeContext,
        file_path: str,
    ) -> Optional[str]:
        """Read text content of a file via Tool Runtime."""
        try:
            res = context.tools.execute(
                tool_id="filesystem.read_file",
                arguments={"path": file_path, "action": "read_file"},
            )
            if res.output is not None:
                return str(res.output)
            return None
        except Exception as err:
            context.log.warning(f"Error reading file '{file_path}': {err}")
            return None

    @classmethod
    def get_git_status(cls, context: WorkerRuntimeContext) -> dict[str, Any]:
        """Query Git status via Tool Runtime if git is available."""
        try:
            res = context.tools.execute(
                tool_id="git.status",
                arguments={"action": "status"},
            )
            if isinstance(res.output, dict):
                return res.output
            return {"raw": str(res.output)}
        except Exception:
            return {}

    @classmethod
    def get_git_diff(cls, context: WorkerRuntimeContext) -> str:
        """Query working tree git diff via Tool Runtime."""
        try:
            res = context.tools.execute(
                tool_id="git.diff",
                arguments={"action": "diff"},
            )
            return str(res.output or "")
        except Exception:
            return ""
