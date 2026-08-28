"""Unit tests for Application Layer DTOs and Error Normalization."""
from __future__ import annotations

import unittest
from app.dto import (
    AppError,
    AppException,
    ErrorCode,
    normalize_error,
    ProjectDTO,
    ProjectSummaryDTO,
    ProjectCreateRequest,
    TaskDTO,
    TaskSummaryDTO,
    WorkerDTO,
    Conversation,
    ConversationMessage,
    MessageType,
    EventStreamEnvelope,
)
from core.errors import ProjectNotFoundError, WorkerBusyError, ToolPermissionDeniedError
from core.models import Project, Task, WorkerManifest, RiskLevel, TaskStatus, WorkerStatus


class TestAppDTO(unittest.TestCase):

    def test_error_normalization_autonomos_error(self):
        err = ProjectNotFoundError("proj-123")
        app_err = normalize_error(err)
        self.assertEqual(app_err.code, ErrorCode.NOT_FOUND)
        self.assertIn("not found", app_err.user_message.lower())

    def test_error_normalization_worker_busy(self):
        err = WorkerBusyError("worker-1", "task-99")
        app_err = normalize_error(err)
        self.assertEqual(app_err.code, ErrorCode.WORKER_BUSY)

    def test_error_normalization_generic_exception(self):
        err = ValueError("secret_key=xyz database password failed")
        app_err = normalize_error(err)
        self.assertEqual(app_err.code, ErrorCode.INTERNAL_ERROR)
        self.assertNotIn("secret_key", app_err.user_message)
        self.assertIn("unexpected error", app_err.user_message.lower())

    def test_project_dto_roundtrip(self):
        proj = Project(
            id="p-1",
            name="Test Proj",
            description="A test project",
            root_path="/tmp/test",
        )
        dto = ProjectDTO.from_domain(proj, task_count=3, worker_count=2)
        d = dto.to_dict()
        dto2 = ProjectDTO.from_dict(d)
        self.assertEqual(dto2.id, "p-1")
        self.assertEqual(dto2.task_count, 3)
        self.assertEqual(dto2.worker_count, 2)

    def test_conversation_dto_roundtrip(self):
        msg = ConversationMessage(
            id="m-1",
            conversation_id="c-1",
            message_type=MessageType.USER_MESSAGE,
            content="Build auth service",
            sender="user",
            timestamp="2026-08-27T10:00:00Z",
        )
        conv = Conversation(
            id="c-1",
            project_id="p-1",
            title="Chat",
            messages=[msg],
            created_at="2026-08-27T10:00:00Z",
            updated_at="2026-08-27T10:00:00Z",
            is_active=True,
        )
        d = conv.to_dict()
        conv2 = Conversation.from_dict(d)
        self.assertEqual(conv2.id, "c-1")
        self.assertEqual(len(conv2.messages), 1)
        self.assertEqual(conv2.messages[0].message_type, MessageType.USER_MESSAGE)

    def test_event_stream_envelope(self):
        env = EventStreamEnvelope(type="event", sequence_number=42, data={"key": "val"})
        d = env.to_dict()
        env2 = EventStreamEnvelope.from_dict(d)
        self.assertEqual(env2.type, "event")
        self.assertEqual(env2.sequence_number, 42)


if __name__ == "__main__":
    unittest.main()
