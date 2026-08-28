from __future__ import annotations
from dataclasses import dataclass, field
from typing import Dict, Any, List, Optional
from enum import Enum

class MessageType(Enum):
    USER_MESSAGE = "USER_MESSAGE"
    MANAGER_MESSAGE = "MANAGER_MESSAGE"
    WORKFLOW_UPDATE = "WORKFLOW_UPDATE"
    WORKER_UPDATE = "WORKER_UPDATE"
    APPROVAL_REQUEST = "APPROVAL_REQUEST"
    USER_INPUT_REQUEST = "USER_INPUT_REQUEST"
    DECISION_REQUEST = "DECISION_REQUEST"
    ERROR = "ERROR"
    COMPLETION = "COMPLETION"

@dataclass
class ConversationMessage:
    id: str
    conversation_id: str
    message_type: MessageType
    content: str
    sender: str
    timestamp: str
    metadata: Dict[str, Any] = field(default_factory=dict)
    related_event_id: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "conversation_id": self.conversation_id,
            "message_type": self.message_type.value,
            "content": self.content,
            "sender": self.sender,
            "timestamp": self.timestamp,
            "metadata": self.metadata,
            "related_event_id": self.related_event_id
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> ConversationMessage:
        return cls(
            id=data["id"],
            conversation_id=data["conversation_id"],
            message_type=MessageType(data["message_type"]),
            content=data["content"],
            sender=data["sender"],
            timestamp=data["timestamp"],
            metadata=data.get("metadata", {}),
            related_event_id=data.get("related_event_id")
        )

@dataclass
class Conversation:
    id: str
    project_id: str
    title: str
    messages: List[ConversationMessage]
    created_at: str
    updated_at: str
    is_active: bool

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "project_id": self.project_id,
            "title": self.title,
            "messages": [m.to_dict() for m in self.messages],
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "is_active": self.is_active
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> Conversation:
        messages = [ConversationMessage.from_dict(m) for m in data.get("messages", [])]
        return cls(
            id=data["id"],
            project_id=data["project_id"],
            title=data["title"],
            messages=messages,
            created_at=data["created_at"],
            updated_at=data["updated_at"],
            is_active=data.get("is_active", True)
        )
