import threading
from typing import Optional

from core.errors import ToolNotFoundError
from core.models import WorkerManifest
from core.tools.base import BaseTool
from core.tools.model import ToolDefinition


class ToolRegistry:
    """
    Thread-safe registry of all available executable tools in AutonomOS.
    """

    def __init__(self):
        self._tools: dict[str, BaseTool] = {}
        self._lock = threading.RLock()

    def register_tool(self, tool: BaseTool) -> None:
        defn = tool.get_definition()
        with self._lock:
            self._tools[defn.id] = tool

    def unregister_tool(self, tool_id: str) -> bool:
        with self._lock:
            if tool_id in self._tools:
                del self._tools[tool_id]
                return True
            return False

    def get_tool(self, tool_id: str) -> BaseTool:
        with self._lock:
            tool = self._tools.get(tool_id)
            if not tool:
                raise ToolNotFoundError(tool_id)
            return tool

    def list_tools(self) -> list[ToolDefinition]:
        with self._lock:
            return [t.get_definition() for t in self._tools.values()]

    def list_tools_for_worker(self, worker_manifest: WorkerManifest) -> list[ToolDefinition]:
        """
        Discover tools that the worker is authorized to execute based on permissions.
        Supports wildcard permissions (e.g. '*' or 'filesystem.*').
        """
        worker_perms = set(worker_manifest.permissions)
        with self._lock:
            authorized_defs: list[ToolDefinition] = []
            for tool in self._tools.values():
                defn = tool.get_definition()
                if self._is_authorized_for_permissions(defn.permissions_required, worker_perms):
                    authorized_defs.append(defn)
            return authorized_defs

    def find_by_capability(self, capability: str) -> list[BaseTool]:
        with self._lock:
            return [
                tool for tool in self._tools.values()
                if capability in tool.get_definition().capabilities
            ]

    @staticmethod
    def _is_authorized_for_permissions(required: list[str], granted: set[str]) -> bool:
        if "*" in granted or "all" in granted:
            return True
        for req in required:
            matched = False
            if req in granted:
                matched = True
            else:
                # Check wildcard prefix e.g. "filesystem.*" matches "filesystem.read"
                for g in granted:
                    if g.endswith(".*") and req.startswith(g[:-2]):
                        matched = True
                        break
            if not matched:
                return False
        return True
