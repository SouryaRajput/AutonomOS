"""Conversation service — manages conversational UI sessions, message history, and Manager dispatch."""
from __future__ import annotations

import uuid
from typing import TYPE_CHECKING, Any, Optional

from app.dto.conversation import Conversation, ConversationMessage, MessageType
from app.dto.errors import AppError, AppException, ErrorCode, normalize_error
from app.services.message_sanitizer import extract_user_facing_narrative, is_internal_tool_payload
from core.models import utc_now

if TYPE_CHECKING:
    from core.runtime.workforce_runtime import WorkforceRuntime


class ConversationService:
    """Manages conversations and conversational dispatch to the Manager."""

    def __init__(self, runtime: WorkforceRuntime):
        self._runtime = runtime

    def get_or_create_active_conversation(self, project_id: str, title: str = "Workforce Chat") -> Conversation:
        """Retrieve the active conversation for a project, or create one if none exists."""
        try:
            convs = self.list_conversations(project_id)
            for c in convs:
                if c.is_active:
                    return self.get_conversation(c.id)
            # Create a new conversation
            return self.create_conversation(project_id=project_id, title=title)
        except Exception as e:
            raise AppException(normalize_error(e)) from e

    def create_conversation(self, project_id: str, title: str = "New Conversation") -> Conversation:
        """Create a new conversation session."""
        try:
            cid = f"conv-{uuid.uuid4().hex[:8]}"
            now = utc_now()
            if hasattr(self._runtime.store, "save_conversation"):
                self._runtime.store.save_conversation(
                    conversation_id=cid,
                    project_id=project_id,
                    title=title,
                    created_at=now,
                    updated_at=now,
                    is_active=True,
                )
            return Conversation(
                id=cid,
                project_id=project_id,
                title=title,
                messages=[],
                created_at=now,
                updated_at=now,
                is_active=True,
            )
        except Exception as e:
            raise AppException(normalize_error(e)) from e

    def get_conversation(self, conversation_id: str) -> Conversation:
        """Get a conversation with all its messages."""
        try:
            if hasattr(self._runtime.store, "get_conversation"):
                conv_data = self._runtime.store.get_conversation(conversation_id)
                if not conv_data:
                    raise AppException(AppError(
                        code=ErrorCode.NOT_FOUND,
                        message=f"Conversation {conversation_id} not found",
                        user_message="Conversation not found.",
                    ))
                raw_msgs = self._runtime.store.get_conversation_messages(conversation_id)
                messages = [ConversationMessage.from_dict(m) for m in raw_msgs]
                return Conversation(
                    id=conv_data["id"],
                    project_id=conv_data["project_id"],
                    title=conv_data["title"],
                    messages=messages,
                    created_at=conv_data["created_at"],
                    updated_at=conv_data["updated_at"],
                    is_active=conv_data["is_active"],
                )
            raise AppException(AppError(code=ErrorCode.NOT_FOUND, message="Store does not support conversations", user_message="Storage error."))
        except AppException:
            raise
        except Exception as e:
            raise AppException(normalize_error(e)) from e

    def list_conversations(self, project_id: str) -> list[Conversation]:
        """List all conversations for a project."""
        try:
            if hasattr(self._runtime.store, "list_conversations_for_project"):
                rows = self._runtime.store.list_conversations_for_project(project_id)
                result = []
                for r in rows:
                    result.append(Conversation(
                        id=r["id"],
                        project_id=r["project_id"],
                        title=r["title"],
                        messages=[],
                        created_at=r["created_at"],
                        updated_at=r["updated_at"],
                        is_active=r["is_active"],
                    ))
                return result
            return []
        except Exception as e:
            raise AppException(normalize_error(e)) from e

    def post_user_message(
        self,
        conversation_id: str,
        content: str,
        dispatch_manager: bool = True,
    ) -> list[ConversationMessage]:
        """
        Record a user message in the conversation and optionally trigger a Manager reasoning cycle.
        Returns the list of newly created messages (user message + manager responses/updates).
        """
        try:
            conv = self.get_conversation(conversation_id)
            user_msg_id = f"msg-{uuid.uuid4().hex[:8]}"
            now = utc_now()

            # Save user message
            user_msg = ConversationMessage(
                id=user_msg_id,
                conversation_id=conversation_id,
                message_type=MessageType.USER_MESSAGE,
                content=content,
                sender="user",
                timestamp=now,
            )
            if hasattr(self._runtime.store, "add_conversation_message"):
                self._runtime.store.add_conversation_message(
                    message_id=user_msg.id,
                    conversation_id=conversation_id,
                    message_type=user_msg.message_type.value,
                    content=user_msg.content,
                    sender=user_msg.sender,
                    timestamp=user_msg.timestamp,
                    metadata=user_msg.metadata,
                )

            produced_messages = [user_msg]

            if dispatch_manager:
                from core.manager.router import IntentRouter
                from core.manager.types import UserIntentType
                from core.workspace.filesystem import ControlledWorkspaceFS
                from core.workspace.project_map import ProjectMapEngine

                classification = IntentRouter.classify(content)
                project = self._runtime.projects.get_project(conv.project_id)

                if classification.intent == UserIntentType.QUESTION and project and project.root_path:
                    try:
                        fs = ControlledWorkspaceFS(project.root_path)
                        map_engine = ProjectMapEngine(fs)
                        answer_text = IntentRouter.answer_question(content, map_engine, project.name)
                    except Exception:
                        answer_text = f"Analyzing {project.name}. No formal tasks created."

                    mgr_msg_id = f"msg-{uuid.uuid4().hex[:8]}"
                    mgr_msg = ConversationMessage(
                        id=mgr_msg_id,
                        conversation_id=conversation_id,
                        message_type=MessageType.MANAGER_MESSAGE,
                        content=answer_text,
                        sender="Manager",
                        timestamp=utc_now(),
                        metadata={"intent": "QUESTION", "direct_answer": True},
                    )
                    if hasattr(self._runtime.store, "add_conversation_message"):
                        self._runtime.store.add_conversation_message(
                            message_id=mgr_msg.id,
                            conversation_id=conversation_id,
                            message_type=mgr_msg.message_type.value,
                            content=mgr_msg.content,
                            sender=mgr_msg.sender,
                            timestamp=mgr_msg.timestamp,
                            metadata=mgr_msg.metadata,
                        )
                    produced_messages.append(mgr_msg)
                    return produced_messages

                # Otherwise (EXECUTION_REQUEST, etc.):
                active_plan = self._runtime.get_active_plan(conv.project_id)
                if not active_plan and project and project.root_path:
                    from core.manager.planner import ManagerPlanner
                    from core.manager.model import Plan
                    from core.manager.types import PlanStatus
                    from core.events.types import EventSource, EventType

                    fs = ControlledWorkspaceFS(project.root_path)
                    map_engine = ProjectMapEngine(fs)
                    planner = ManagerPlanner(self._runtime, map_engine)
                    delegation_plan = planner.plan_and_delegate(
                        project_id=conv.project_id,
                        objective=content,
                    )

                    plan_obj = Plan(
                        id=delegation_plan.plan_id,
                        project_id=conv.project_id,
                        objective=content,
                        tasks=[t.to_dict() for t in delegation_plan.tasks],
                        milestones=delegation_plan.subsystems_involved,
                        status=PlanStatus.ACTIVE,
                        version=1,
                    )
                    if hasattr(self._runtime.store, "save_plan"):
                        self._runtime.store.save_plan(plan_obj)

                    self._runtime.log_event(
                        event_type=EventType.MANAGER_PLAN_CREATED,
                        payload={
                            "plan_id": delegation_plan.plan_id,
                            "tasks_count": len(delegation_plan.tasks),
                            "status": delegation_plan.status,
                            "objective": content,
                        },
                        project_id=conv.project_id,
                        source=EventSource.MANAGER,
                    )

                    # Automatically provision and activate required specialist workers
                    for t_contract in delegation_plan.tasks:
                        w_id = t_contract.worker_id or t_contract.worker_type
                        if w_id:
                            try:
                                self._runtime.activate_worker(w_id, project_id=conv.project_id, task_id=t_contract.task_id)
                            except Exception as act_err:
                                logger.warning(f"Could not pre-activate worker '{w_id}': {act_err}")

                    # Step manager cycle to formulate initial assignments
                    try:
                        cycle_result = self._runtime.step_manager(conv.project_id)
                    except Exception as step_err:
                        logger.warning(f"Initial Manager step cycle failed: {step_err}")
                        cycle_result = None

                    summary_text = (
                        cycle_result.status_summary
                        if cycle_result and cycle_result.status_summary and cycle_result.status_summary != "No cycles run."
                        else f"Work plan formulated with {len(delegation_plan.tasks)} tasks. Required specialists activated."
                    )
                    clean_summary, internal_tools = extract_user_facing_narrative(summary_text)
                    if not clean_summary:
                        clean_summary = f"Work plan formulated with {len(delegation_plan.tasks)} tasks. Required specialists activated."

                    mgr_msg_id = f"msg-{uuid.uuid4().hex[:8]}"
                    mgr_msg = ConversationMessage(
                        id=mgr_msg_id,
                        conversation_id=conversation_id,
                        message_type=MessageType.MANAGER_MESSAGE,
                        content=clean_summary,
                        sender="Manager",
                        timestamp=utc_now(),
                        metadata={
                            "plan_id": delegation_plan.plan_id,
                            "tasks_count": len(delegation_plan.tasks),
                            "status": delegation_plan.status,
                        },
                    )
                    if hasattr(self._runtime.store, "add_conversation_message"):
                        self._runtime.store.add_conversation_message(
                            message_id=mgr_msg.id,
                            conversation_id=conversation_id,
                            message_type=mgr_msg.message_type.value,
                            content=mgr_msg.content,
                            sender=mgr_msg.sender,
                            timestamp=mgr_msg.timestamp,
                            metadata=mgr_msg.metadata,
                        )
                    produced_messages.append(mgr_msg)
                    return produced_messages

                # If plan already exists, step the Manager cycle
                cycle_result = self._runtime.step_manager(
                    project_id=conv.project_id,
                    feedback=content,
                )

                # Format Manager response message
                summary = cycle_result.status_summary
                if cycle_result.decision:
                    summary = cycle_result.decision.reasoning_summary or summary

                clean_summary, internal_tools = extract_user_facing_narrative(summary)
                if not clean_summary:
                    clean_summary = "I've updated the project execution plan and active task status."

                if internal_tools:
                    from core.events.types import EventSource, EventType
                    for tool_call in internal_tools:
                        self._runtime.log_event(
                            event_type=EventType.TOOL_REQUESTED,
                            payload=tool_call,
                            project_id=conv.project_id,
                            source=EventSource.MANAGER,
                        )

                mgr_msg_id = f"msg-{uuid.uuid4().hex[:8]}"
                mgr_msg = ConversationMessage(
                    id=mgr_msg_id,
                    conversation_id=conversation_id,
                    message_type=MessageType.MANAGER_MESSAGE,
                    content=clean_summary,
                    sender="Manager",
                    timestamp=utc_now(),
                    metadata={
                        "cycle_id": cycle_result.cycle_id,
                        "progress_detected": cycle_result.progress_detected,
                        "completed": cycle_result.completed,
                        "waiting": cycle_result.waiting,
                        "actions_count": len(cycle_result.results),
                    },
                )
                if hasattr(self._runtime.store, "add_conversation_message"):
                    self._runtime.store.add_conversation_message(
                        message_id=mgr_msg.id,
                        conversation_id=conversation_id,
                        message_type=mgr_msg.message_type.value,
                        content=mgr_msg.content,
                        sender=mgr_msg.sender,
                        timestamp=mgr_msg.timestamp,
                        metadata=mgr_msg.metadata,
                    )
                produced_messages.append(mgr_msg)

                # Check if approval or user input was requested during cycle
                if cycle_result.user_input_required or cycle_result.waiting:
                    pending_approvals = self._runtime.autonomy.list_pending_approvals(conv.project_id)
                    for app in pending_approvals:
                        app_msg_id = f"msg-{uuid.uuid4().hex[:8]}"
                        app_msg = ConversationMessage(
                            id=app_msg_id,
                            conversation_id=conversation_id,
                            message_type=MessageType.APPROVAL_REQUEST,
                            content=f"Approval requested: {app.action} ({app.risk_level.value} risk). Reason: {app.reason}",
                            sender="AutonomyControl",
                            timestamp=utc_now(),
                            metadata=app.to_dict(),
                        )
                        if hasattr(self._runtime.store, "add_conversation_message"):
                            self._runtime.store.add_conversation_message(
                                message_id=app_msg.id,
                                conversation_id=conversation_id,
                                message_type=app_msg.message_type.value,
                                content=app_msg.content,
                                sender=app_msg.sender,
                                timestamp=app_msg.timestamp,
                                metadata=app_msg.metadata,
                            )
                        produced_messages.append(app_msg)

                if cycle_result.completed:
                    comp_msg_id = f"msg-{uuid.uuid4().hex[:8]}"
                    comp_msg = ConversationMessage(
                        id=comp_msg_id,
                        conversation_id=conversation_id,
                        message_type=MessageType.COMPLETION,
                        content=f"Objective completed: {cycle_result.status_summary}",
                        sender="Manager",
                        timestamp=utc_now(),
                        metadata={"completed": True},
                    )
                    if hasattr(self._runtime.store, "add_conversation_message"):
                        self._runtime.store.add_conversation_message(
                            message_id=comp_msg.id,
                            conversation_id=conversation_id,
                            message_type=comp_msg.message_type.value,
                            content=comp_msg.content,
                            sender=comp_msg.sender,
                            timestamp=comp_msg.timestamp,
                            metadata=comp_msg.metadata,
                        )
                    produced_messages.append(comp_msg)

            return produced_messages
        except AppException:
            raise
        except Exception as e:
            raise AppException(normalize_error(e)) from e

    def record_system_message(
        self,
        conversation_id: str,
        message_type: MessageType,
        content: str,
        sender: str = "System",
        metadata: Optional[dict[str, Any]] = None,
        related_event_id: Optional[str] = None,
    ) -> ConversationMessage:
        """Record an arbitrary system/worker/workflow update message."""
        try:
            clean_content, internal_tools = extract_user_facing_narrative(content)
            if not clean_content and message_type == MessageType.ERROR:
                clean_content = "An internal operation encountered an error."
            elif not clean_content:
                clean_content = "Specialist execution update recorded."

            if internal_tools:
                from core.events.types import EventSource, EventType
                conv = self.get_conversation(conversation_id)
                for tool_call in internal_tools:
                    self._runtime.log_event(
                        event_type=EventType.TOOL_REQUESTED,
                        payload=tool_call,
                        project_id=conv.project_id if hasattr(conv, "project_id") else "",
                        source=EventSource.WORKER,
                    )

            msg_id = f"msg-{uuid.uuid4().hex[:8]}"
            msg = ConversationMessage(
                id=msg_id,
                conversation_id=conversation_id,
                message_type=message_type,
                content=clean_content,
                sender=sender,
                timestamp=utc_now(),
                metadata=metadata or {},
                related_event_id=related_event_id,
            )
            if hasattr(self._runtime.store, "add_conversation_message"):
                self._runtime.store.add_conversation_message(
                    message_id=msg.id,
                    conversation_id=conversation_id,
                    message_type=msg.message_type.value,
                    content=msg.content,
                    sender=msg.sender,
                    timestamp=msg.timestamp,
                    metadata=msg.metadata,
                    related_event_id=msg.related_event_id,
                )
            return msg
        except Exception as e:
            raise AppException(normalize_error(e)) from e
