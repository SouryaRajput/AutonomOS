from __future__ import annotations

from typing import Any, Optional

from pkg.sdk.worker import WorkerRuntimeContext


class ImplementationInspector:
    """
    Safely inspects project codebase, implementation reports, changed files,
    and existing test directories strictly mediated by Tool Runtime.
    """

    def __init__(self, context: WorkerRuntimeContext):
        self.context = context

    def list_files(self, path: str = "") -> list[str]:
        try:
            res = self.context.tools.execute(
                tool_id="filesystem.list",
                arguments={"path": path, "recursive": True},
            )
            if res and isinstance(res.output, list):
                return [str(f.get("path", f) if isinstance(f, dict) else f) for f in res.output]
            elif res and isinstance(res.output, dict) and "entries" in res.output:
                return [str(e.get("path", e) if isinstance(e, dict) else e) for e in res.output["entries"]]
            return []
        except Exception as e:
            self.context.log.warning(f"Failed to list files at '{path}': {e}")
            return []

    def read_file(self, path: str) -> Optional[str]:
        try:
            res = self.context.tools.execute(
                tool_id="filesystem.read",
                arguments={"path": path},
            )
            if res and res.output is not None:
                return str(res.output)
            return None
        except Exception as e:
            self.context.log.warning(f"Failed to read file '{path}': {e}")
            return None

    def inspect_changed_files(self, specified_files: list[str]) -> dict[str, str]:
        results: dict[str, str] = {}
        for fpath in specified_files:
            content = self.read_file(fpath)
            if content is not None:
                results[fpath] = content
        return results

    def discover_test_files(self) -> list[str]:
        all_files = self.list_files()
        test_files = [
            f for f in all_files 
            if ("test" in f.lower() or f.startswith("test_") or f.endswith("_test.py") or f.endswith(".test.js"))
            and not f.startswith(".git") and not f.startswith(".autonomos")
        ]
        return test_files
