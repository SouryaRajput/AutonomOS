"""AutonomOS Application Container — Unified entry point for client integrations."""
from __future__ import annotations

import logging
from typing import Optional

from app.services.approval_service import ApprovalService
from app.services.artifact_service import ArtifactService
from app.services.conversation_service import ConversationService
from app.services.event_service import EventService
from app.services.policy_service import PolicyService
from app.services.project_service import ProjectService
from app.services.provider_service import ProviderService
from app.services.task_service import TaskService
from app.services.worker_service import WorkerService
from app.services.workflow_service import WorkflowService
from app.services.export_service import ExportImportService
from app.services.search_service import SearchService
from app.services.storage_service import StorageService
from app.services.version_service import VersionService
from app.services.workspace_service import WorkspaceService
from app.stream.event_stream import EventStreamManager
from core.runtime.workforce_runtime import WorkforceRuntime
from core.safety.model import SafetyConfig
from core.storage.sqlite_store import SQLiteStore

logger = logging.getLogger("AutonomOS.App")


class AutonomOSApp:
    """
    Unified Application Container for AutonomOS.
    Binds the authoritative WorkforceRuntime to the UI-facing application services,
    DTOs, and real-time event streaming.
    """

    def __init__(self, runtime: WorkforceRuntime):
        self._runtime = runtime

        # Initialize Application Services
        self.projects = ProjectService(runtime)
        self.tasks = TaskService(runtime)
        self.workers = WorkerService(runtime)
        self.workflows = WorkflowService(runtime)
        self.artifacts = ArtifactService(runtime)
        self.approvals = ApprovalService(runtime)
        self.policies = PolicyService(runtime)
        self.providers = ProviderService(runtime)
        self.events = EventService(runtime)
        self.conversations = ConversationService(runtime)
        self.search = SearchService(runtime)
        self.storage = StorageService(runtime)
        self.export_import = ExportImportService(runtime)
        self.version = VersionService(runtime)
        self.workspace = WorkspaceService(runtime)

        # Real-time event streaming
        self.stream = EventStreamManager(runtime)

    @classmethod
    def with_sqlite(cls, db_path: str = ":memory:", safety_config: Optional[SafetyConfig] = None) -> AutonomOSApp:
        """Create an AutonomOSApp backed by an embedded SQLite database."""
        store = SQLiteStore(db_path)
        runtime = WorkforceRuntime(store=store, safety_config=safety_config)
        return cls(runtime)

    @property
    def runtime(self) -> WorkforceRuntime:
        """Reference to the underlying authoritative runtime."""
        return self._runtime

    def close(self) -> None:
        """Orderly shutdown of the application container and database."""
        self._runtime.close()
