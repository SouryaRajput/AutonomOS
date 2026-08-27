from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Optional

from core.verification.model import SuccessCriterion, VerificationCheck
from core.verification.types import CheckType

if TYPE_CHECKING:
    from core.runtime.artifact_registry import ArtifactRegistry
    from core.storage.base import Store
    from core.tools.runtime import ToolRuntime


@dataclass
class CheckExecutionContext:
    """Execution context provided to check adapters."""
    project_id: str
    workspace_root: str
    task_id: str
    worker_id: Optional[str]
    tool_runtime: ToolRuntime
    artifact_registry: ArtifactRegistry
    store: Store


class BaseCheckAdapter(ABC):
    """Abstract interface for pluggable verification check adapters."""

    @abstractmethod
    def supports(self, check_type: CheckType) -> bool:
        """Return True if adapter supports this check type."""
        pass

    @abstractmethod
    def execute(
        self,
        context: CheckExecutionContext,
        criterion: SuccessCriterion,
        verification_id: str,
    ) -> VerificationCheck:
        """Execute the check deterministically and return a VerificationCheck record."""
        pass
