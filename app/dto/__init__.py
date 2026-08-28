"""AutonomOS Application DTOs — Stable, serializable data transfer objects for the UI."""
from __future__ import annotations

from app.dto.approval import (
    ApprovalRequestDTO,
    ApprovalResponse,
    DecisionRequestDTO,
    UserInputRequestDTO,
)
from app.dto.artifact import ArtifactDTO, EvidenceDTO
from app.dto.conversation import Conversation, ConversationMessage, MessageType
from app.dto.errors import AppError, AppException, ErrorCode, normalize_error
from app.dto.event import ActivityItemDTO, EventDTO, EventStreamEnvelope
from app.dto.manager import CycleResultDTO, ManagerStatusDTO, PlanDTO
from app.dto.project import ProjectCreateRequest, ProjectDTO, ProjectSummaryDTO
from app.dto.provider import ModelDTO, ProviderDTO
from app.dto.task import TaskCreateRequest, TaskDTO, TaskSummaryDTO
from app.dto.worker import WorkerDTO, WorkerSummaryDTO
from app.dto.workflow import HandoffDTO, WorkflowDTO

__all__ = [
    "ApprovalRequestDTO",
    "ApprovalResponse",
    "DecisionRequestDTO",
    "UserInputRequestDTO",
    "ArtifactDTO",
    "EvidenceDTO",
    "Conversation",
    "ConversationMessage",
    "MessageType",
    "AppError",
    "AppException",
    "ErrorCode",
    "normalize_error",
    "ActivityItemDTO",
    "EventDTO",
    "EventStreamEnvelope",
    "CycleResultDTO",
    "ManagerStatusDTO",
    "PlanDTO",
    "ProjectCreateRequest",
    "ProjectDTO",
    "ProjectSummaryDTO",
    "ModelDTO",
    "ProviderDTO",
    "TaskCreateRequest",
    "TaskDTO",
    "TaskSummaryDTO",
    "WorkerDTO",
    "WorkerSummaryDTO",
    "HandoffDTO",
    "WorkflowDTO",
]
