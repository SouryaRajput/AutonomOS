from abc import ABC, abstractmethod
import time
from typing import Any, Optional
import urllib.request
import uuid

from core.enums import RiskLevel, ToolStatus
from core.tools.base import BaseTool
from core.tools.model import ToolDefinition, ToolExecutionContext, ToolResult
from core.tools.types import ToolCategory


class WebAdapter(ABC):
    """Abstract adapter for provider-independent web search and fetch operations."""

    @abstractmethod
    def search(self, query: str, limit: int = 5) -> list[dict[str, Any]]:
        pass

    @abstractmethod
    def fetch(self, url: str, max_bytes: int = 100000) -> str:
        pass


class MockWebAdapter(WebAdapter):
    """Deterministic offline adapter for unit testing and offline development."""

    def __init__(self, mock_data: Optional[dict[str, Any]] = None):
        self.mock_data = mock_data or {}

    def search(self, query: str, limit: int = 5) -> list[dict[str, Any]]:
        if query in self.mock_data:
            return self.mock_data[query][:limit]
        return [
            {
                "title": f"Result for '{query}' - Documentation",
                "url": f"https://docs.example.org/search?q={query}",
                "snippet": f"Official documentation and API reference for {query}.",
            },
            {
                "title": f"Community Guide: {query}",
                "url": f"https://community.example.org/{query}",
                "snippet": f"Best practices and troubleshooting guide for {query}.",
            },
        ][:limit]

    def fetch(self, url: str, max_bytes: int = 100000) -> str:
        if url in self.mock_data:
            return str(self.mock_data[url])[:max_bytes]
        return f"# Web Content from {url}\n\nThis is simulated web page content fetched offline."


class WebTool(BaseTool):
    """
    Controlled Web Access Tool for AutonomOS.
    Executes search queries and web fetches through pluggable adapters with response limits.
    """

    def __init__(self, tool_id: str = "web", adapter: Optional[WebAdapter] = None):
        self._tool_id = tool_id
        self._adapter = adapter or MockWebAdapter()

    def set_adapter(self, adapter: WebAdapter) -> None:
        self._adapter = adapter

    def get_definition(self) -> ToolDefinition:
        return ToolDefinition(
            id=self._tool_id,
            name="Web Search and Fetch Tool",
            description="Provider-independent web search and content retrieval.",
            version="1.0.0",
            category=ToolCategory.WEB,
            capabilities=["web.search", "web.fetch"],
            input_schema={
                "type": "object",
                "properties": {
                    "action": {"type": "string", "enum": ["search", "fetch"]},
                    "query": {"type": "string"},
                    "url": {"type": "string"},
                    "limit": {"type": "integer", "default": 5},
                },
                "required": ["action"],
            },
            risk_level=RiskLevel.LOW,
            permissions_required=["web.search" if "search" in self._tool_id else "web.fetch"],
            timeout_seconds=15,
            output_limit_bytes=100000,
        )

    def execute(self, context: ToolExecutionContext, arguments: dict[str, Any]) -> ToolResult:
        start_time = time.perf_counter()
        req_id = arguments.get("request_id", f"req-{uuid.uuid4().hex[:8]}")
        action = arguments.get("action", "search")

        try:
            if action == "search":
                query = arguments.get("query")
                if not query:
                    return ToolResult(
                        result_id=f"res-{uuid.uuid4().hex[:8]}",
                        request_id=req_id,
                        tool_id=self._tool_id,
                        status=ToolStatus.VALIDATION_ERROR,
                        error_message="Query argument is required for web search.",
                        duration_ms=(time.perf_counter() - start_time) * 1000.0,
                    )
                limit = int(arguments.get("limit", 5))
                results = self._adapter.search(query, limit=limit)
                return ToolResult(
                    result_id=f"res-{uuid.uuid4().hex[:8]}",
                    request_id=req_id,
                    tool_id=self._tool_id,
                    status=ToolStatus.SUCCESS,
                    output={"query": query, "results": results},
                    duration_ms=(time.perf_counter() - start_time) * 1000.0,
                )

            elif action == "fetch":
                url = arguments.get("url")
                if not url:
                    return ToolResult(
                        result_id=f"res-{uuid.uuid4().hex[:8]}",
                        request_id=req_id,
                        tool_id=self._tool_id,
                        status=ToolStatus.VALIDATION_ERROR,
                        error_message="URL argument is required for web fetch.",
                        duration_ms=(time.perf_counter() - start_time) * 1000.0,
                    )
                content = self._adapter.fetch(url, max_bytes=context.output_limit_bytes)
                is_truncated = len(content) >= context.output_limit_bytes
                return ToolResult(
                    result_id=f"res-{uuid.uuid4().hex[:8]}",
                    request_id=req_id,
                    tool_id=self._tool_id,
                    status=ToolStatus.SUCCESS,
                    output={"url": url, "content": content},
                    is_truncated=is_truncated,
                    duration_ms=(time.perf_counter() - start_time) * 1000.0,
                )
            else:
                return ToolResult(
                    result_id=f"res-{uuid.uuid4().hex[:8]}",
                    request_id=req_id,
                    tool_id=self._tool_id,
                    status=ToolStatus.VALIDATION_ERROR,
                    error_message=f"Unknown web action '{action}'",
                    duration_ms=(time.perf_counter() - start_time) * 1000.0,
                )

        except Exception as e:
            return ToolResult(
                result_id=f"res-{uuid.uuid4().hex[:8]}",
                request_id=req_id,
                tool_id=self._tool_id,
                status=ToolStatus.FAILED,
                error_message=str(e),
                duration_ms=(time.perf_counter() - start_time) * 1000.0,
            )
