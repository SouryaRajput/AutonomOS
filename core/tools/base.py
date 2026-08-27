from abc import ABC, abstractmethod
from typing import Any

from core.errors import ToolArgumentValidationError
from core.tools.model import ToolDefinition, ToolExecutionContext, ToolResult


class BaseTool(ABC):
    """
    Universal abstract base class for all tools in AutonomOS.
    """

    @abstractmethod
    def get_definition(self) -> ToolDefinition:
        """Return the static metadata, input schema, risk level, and required permissions for this tool."""
        pass

    def validate_arguments(self, arguments: dict[str, Any]) -> None:
        """
        Validate input arguments against schema before authorization and execution.
        Subclasses can override for specific custom validation.
        """
        defn = self.get_definition()
        schema = defn.input_schema
        required_keys = schema.get("required", [])
        errors = []

        for key in required_keys:
            if key not in arguments or arguments[key] is None:
                errors.append(f"Missing required argument: '{key}'")

        if errors:
            raise ToolArgumentValidationError(defn.id, errors)

    @abstractmethod
    def execute(self, context: ToolExecutionContext, arguments: dict[str, Any]) -> ToolResult:
        """
        Execute the tool capability within the controlled ToolExecutionContext.
        """
        pass
