from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional
import uuid

from core.research.question.model import (
    QuestionOption,
    QuestionProvenance,
    ResearchAnswer,
    ResearchQuestion,
    new_id,
    utc_now,
)
from core.research.question.types import (
    DecisionType,
    QuestionImportance,
    QuestionState,
)
from core.research.question.validator import (
    QuestionValidationError,
    QuestionValidator,
)


class ActiveQuestionExistsError(RuntimeError):
    """Raised when attempting to activate a new question while an active question is still pending."""
    pass


class QuestionLimitExceededError(RuntimeError):
    """Raised when attempting to exceed the hard limit of 5 questions for a single research task."""
    pass


class NoActiveQuestionError(RuntimeError):
    """Raised when an answer or skip action is submitted but no question is currently pending."""
    pass


@dataclass
class QuestionTracker:
    """
    Deterministic state manager and lifecycle controller for Human-In-The-Loop research questions.
    Enforces:
    1. One-at-a-time presentation (only one QUESTION_PENDING question per research task).
    2. Hard limit of 1–5 questions maximum per research task.
    3. Strict state machine progression: QUESTION_PENDING -> USER_ANSWERED/USER_SKIPPED -> RESOLVED.
    4. Immutable historical audit log.
    """
    research_request_id: str
    max_questions: int = 5
    active_question: Optional[ResearchQuestion] = None
    history: list[ResearchQuestion] = field(default_factory=list)
    session_trace: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        if self.max_questions > 5:
            # Enforce hard ceiling of 5 per architectural specifications
            self.max_questions = 5

    @property
    def total_questions_count(self) -> int:
        """Total count of presented or completed questions."""
        return len(self.history) + (1 if self.active_question is not None else 0)

    def is_limit_reached(self) -> bool:
        """Check whether the task has reached the maximum permitted questions."""
        return self.total_questions_count >= self.max_questions

    def has_active_question(self) -> bool:
        """Check whether there is currently a question awaiting user response."""
        return self.active_question is not None and self.active_question.is_active()

    def present_question(
        self,
        question: ResearchQuestion,
        allow_medium: bool = False,
    ) -> ResearchQuestion:
        """
        Register and activate a candidate question for the research task.
        Guarantees:
        - Rejects candidate if an active question is already pending.
        - Rejects candidate if the maximum question bound (5) has been reached.
        - Assigns the next deterministic sequence number.
        - Validates the candidate against QuestionValidator.
        """
        # 1. Enforce one-at-a-time invariant
        if self.has_active_question():
            assert self.active_question is not None
            raise ActiveQuestionExistsError(
                f"Cannot present question: Active question '{self.active_question.question_id}' is still pending."
            )

        # 2. Enforce hard question limit
        if self.total_questions_count >= self.max_questions:
            raise QuestionLimitExceededError(
                f"Cannot present question: Maximum question limit ({self.max_questions}) reached for request '{self.research_request_id}'."
            )

        # 3. Synchronize request linkage and sequence number
        question.research_request_id = self.research_request_id
        if question.provenance and question.provenance.research_request_id != self.research_request_id:
            question.provenance.research_request_id = self.research_request_id
        question.sequence_number = len(self.history) + 1
        question.state = QuestionState.QUESTION_PENDING

        # 4. Deterministic validation
        QuestionValidator.validate_question(
            question=question,
            allow_medium=allow_medium,
            raise_on_error=True,
        )

        # 5. Activate question
        now = utc_now()
        question.add_trace(f"Presented to user as question {question.sequence_number}/{self.max_questions}")
        self.active_question = question
        self.session_trace.append(f"[{now}] Presented question {question.question_id} (seq {question.sequence_number})")

        return question

    def submit_answer(self, answer: ResearchAnswer) -> ResearchQuestion:
        """
        Record a user's answer (option or custom answer), transitioning the active question
        from QUESTION_PENDING -> USER_ANSWERED -> RESOLVED.
        """
        if self.active_question is None:
            raise NoActiveQuestionError("Cannot submit answer: No active question is currently pending.")

        active = self.active_question

        # Ensure answer links to the active question
        if not answer.research_request_id:
            answer.research_request_id = self.research_request_id
        if not answer.question_id:
            answer.question_id = active.question_id

        # Validate answer against active question
        QuestionValidator.validate_answer(
            question=active,
            answer=answer,
            raise_on_error=True,
        )

        # Record answer and update states
        now = utc_now()
        active.answer = answer

        if answer.skipped:
            active.state = QuestionState.USER_SKIPPED
            active.add_trace(f"User skipped question. Reason: {answer.skip_reason or 'No reason provided'}")
            # Transition to resolved after recording skip
            active.state = QuestionState.RESOLVED
            active.add_trace("Question lifecycle resolved via user skip")
            self.session_trace.append(f"[{now}] Question {active.question_id} skipped by user")
        else:
            active.state = QuestionState.USER_ANSWERED
            if answer.selected_option_id:
                active.add_trace(f"User selected option: {answer.selected_option_id}")
            if answer.custom_answer:
                active.add_trace(f"User provided custom answer: {answer.custom_answer}")
            # Transition to resolved
            active.state = QuestionState.RESOLVED
            active.add_trace("Question lifecycle resolved via user answer")
            self.session_trace.append(f"[{now}] Question {active.question_id} answered and resolved")

        # Move to history and clear active slot
        resolved_question = active
        self.history.append(resolved_question)
        self.active_question = None

        return resolved_question

    def skip_active_question(self, skip_reason: Optional[str] = None) -> ResearchQuestion:
        """Convenience method to explicitly skip the active pending question."""
        if self.active_question is None:
            raise NoActiveQuestionError("Cannot skip question: No active question is currently pending.")

        answer = ResearchAnswer(
            question_id=self.active_question.question_id,
            research_request_id=self.research_request_id,
            skipped=True,
            skip_reason=skip_reason,
        )
        return self.submit_answer(answer)

    def cancel_active_question(self, cancel_reason: str = "Cancelled by system") -> ResearchQuestion:
        """Cancel the active pending question (e.g. on task cancellation)."""
        if self.active_question is None:
            raise NoActiveQuestionError("Cannot cancel question: No active question is currently pending.")

        active = self.active_question
        now = utc_now()
        active.state = QuestionState.CANCELLED
        active.add_trace(f"Question cancelled: {cancel_reason}")
        self.session_trace.append(f"[{now}] Question {active.question_id} cancelled: {cancel_reason}")

        self.history.append(active)
        self.active_question = None
        return active

    def expire_active_question(self, expire_reason: str = "Question expired") -> ResearchQuestion:
        """Expire the active pending question when user interaction times out."""
        if self.active_question is None:
            raise NoActiveQuestionError("Cannot expire question: No active question is currently pending.")

        active = self.active_question
        now = utc_now()
        active.state = QuestionState.EXPIRED
        active.add_trace(f"Question expired: {expire_reason}")
        self.session_trace.append(f"[{now}] Question {active.question_id} expired: {expire_reason}")

        self.history.append(active)
        self.active_question = None
        return active

    def to_dict(self) -> dict[str, Any]:
        return {
            "research_request_id": self.research_request_id,
            "max_questions": self.max_questions,
            "total_questions_count": self.total_questions_count,
            "is_limit_reached": self.is_limit_reached(),
            "has_active_question": self.has_active_question(),
            "active_question": self.active_question.to_dict() if self.active_question else None,
            "history": [q.to_dict() for q in self.history],
            "session_trace": list(self.session_trace),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> QuestionTracker:
        active_raw = data.get("active_question")
        active = ResearchQuestion.from_dict(active_raw) if isinstance(active_raw, dict) else None

        hist_raw = data.get("history", [])
        history = [
            ResearchQuestion.from_dict(q) for q in hist_raw
            if isinstance(q, dict)
        ]

        tracker = cls(
            research_request_id=str(data.get("research_request_id", "")),
            max_questions=int(data.get("max_questions", 5)),
            active_question=active,
            history=history,
            session_trace=list(data.get("session_trace", [])),
        )
        return tracker
