from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional
import uuid

from core.research.question.types import (
    AnswerSource,
    AnswerType,
    DecisionType,
    QuestionImportance,
    QuestionState,
)


def utc_now() -> str:
    """Generate ISO 8601 UTC timestamp."""
    return datetime.now(timezone.utc).isoformat()


def new_id(prefix: str = "rq-dec") -> str:
    """Generate unique identifier with prefix."""
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


@dataclass
class QuestionOption:
    """
    Structured AI-generated option for UI popup/modal rendering.
    Allows users to choose a predefined course of action with explicit rationale.
    """
    option_id: str
    label: str
    description: str = ""
    rationale: str = ""
    recommended: bool = False
    recommendation_reason: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "option_id": self.option_id,
            "label": self.label,
            "description": self.description,
            "rationale": self.rationale,
            "recommended": self.recommended,
            "recommendation_reason": self.recommendation_reason,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> QuestionOption:
        return cls(
            option_id=str(data.get("option_id", "")),
            label=str(data.get("label", "")),
            description=str(data.get("description", "")),
            rationale=str(data.get("rationale", "")),
            recommended=bool(data.get("recommended", False)),
            recommendation_reason=str(data["recommendation_reason"]) if data.get("recommendation_reason") is not None else None,
        )


@dataclass
class QuestionProvenance:
    """
    Audit provenance linking a question back to the originating research request,
    inferred intent, and specific unresolved decision.
    """
    research_request_id: str
    research_intent_id: Optional[str] = None
    unresolved_decision_id: Optional[str] = None
    source_field: Optional[str] = None
    rationale: str = ""
    created_at: str = field(default_factory=utc_now)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "research_request_id": self.research_request_id,
            "research_intent_id": self.research_intent_id,
            "unresolved_decision_id": self.unresolved_decision_id,
            "source_field": self.source_field,
            "rationale": self.rationale,
            "created_at": self.created_at,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> QuestionProvenance:
        return cls(
            research_request_id=str(data.get("research_request_id", "")),
            research_intent_id=str(data["research_intent_id"]) if data.get("research_intent_id") is not None else None,
            unresolved_decision_id=str(data["unresolved_decision_id"]) if data.get("unresolved_decision_id") is not None else None,
            source_field=str(data["source_field"]) if data.get("source_field") is not None else None,
            rationale=str(data.get("rationale", "")),
            created_at=str(data.get("created_at", utc_now())),
            metadata=dict(data.get("metadata", {})),
        )


@dataclass
class ResearchAnswer:
    """
    Typed user response to a research decision question.
    Treats custom answers as first-class input alongside selected options.
    Preserves answer source, answer type, and detailed trace history.
    """
    question_id: str
    research_request_id: str
    answer_id: str = field(default_factory=lambda: new_id("ans"))
    selected_option_id: Optional[str] = None
    custom_answer: Optional[str] = None
    skipped: bool = False
    skip_reason: Optional[str] = None
    answered_at: str = field(default_factory=utc_now)
    answer_source: AnswerSource = AnswerSource.USER
    answer_type: Optional[AnswerType] = None
    answer_trace: list[str] = field(default_factory=list)
    user_id: Optional[str] = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.answer_type is None:
            if self.skipped:
                self.answer_type = AnswerType.SKIPPED
            elif self.custom_answer and self.custom_answer.strip():
                self.answer_type = AnswerType.CUSTOM_ANSWER
            elif self.selected_option_id and self.selected_option_id.strip():
                self.answer_type = AnswerType.OPTION_SELECTED
            else:
                self.answer_type = AnswerType.SKIPPED if self.skipped else AnswerType.OPTION_SELECTED

    def is_custom(self) -> bool:
        return self.answer_type == AnswerType.CUSTOM_ANSWER or bool(self.custom_answer and self.custom_answer.strip())

    def is_option(self) -> bool:
        return self.answer_type == AnswerType.OPTION_SELECTED or bool(self.selected_option_id and self.selected_option_id.strip())

    def is_skip(self) -> bool:
        return self.answer_type == AnswerType.SKIPPED or self.skipped

    def get_effective_answer_text(self, question: Optional[ResearchQuestion] = None) -> str:
        """Return human-readable representation of user decision."""
        if self.is_custom() and self.custom_answer:
            return self.custom_answer.strip()
        if self.is_option() and self.selected_option_id:
            if question is not None:
                for opt in question.options:
                    if opt.option_id == self.selected_option_id:
                        return opt.label
            return self.selected_option_id
        if self.is_skip():
            return f"SKIPPED: {self.skip_reason or 'No reason provided'}"
        return ""

    def add_trace(self, entry: str) -> None:
        self.answer_trace.append(f"[{utc_now()}] {entry}")

    def to_dict(self) -> dict[str, Any]:
        return {
            "answer_id": self.answer_id,
            "question_id": self.question_id,
            "research_request_id": self.research_request_id,
            "selected_option_id": self.selected_option_id,
            "custom_answer": self.custom_answer,
            "skipped": self.skipped,
            "skip_reason": self.skip_reason,
            "answered_at": self.answered_at,
            "answer_source": self.answer_source.value if isinstance(self.answer_source, AnswerSource) else str(self.answer_source),
            "answer_type": self.answer_type.value if isinstance(self.answer_type, AnswerType) else str(self.answer_type) if self.answer_type else None,
            "answer_trace": list(self.answer_trace),
            "user_id": self.user_id,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ResearchAnswer:
        src_raw = data.get("answer_source", AnswerSource.USER.value)
        try:
            answer_source = AnswerSource(src_raw)
        except (ValueError, TypeError):
            answer_source = AnswerSource.USER

        type_raw = data.get("answer_type")
        answer_type = None
        if type_raw:
            try:
                answer_type = AnswerType(type_raw)
            except (ValueError, TypeError):
                answer_type = None

        return cls(
            answer_id=str(data.get("answer_id", new_id("ans"))),
            question_id=str(data.get("question_id", "")),
            research_request_id=str(data.get("research_request_id", "")),
            selected_option_id=str(data["selected_option_id"]) if data.get("selected_option_id") is not None else None,
            custom_answer=str(data["custom_answer"]) if data.get("custom_answer") is not None else None,
            skipped=bool(data.get("skipped", False)),
            skip_reason=str(data["skip_reason"]) if data.get("skip_reason") is not None else None,
            answered_at=str(data.get("answered_at", utc_now())),
            answer_source=answer_source,
            answer_type=answer_type,
            answer_trace=list(data.get("answer_trace", [])),
            user_id=str(data["user_id"]) if data.get("user_id") is not None else None,
            metadata=dict(data.get("metadata", {})),
        )


@dataclass
class ResearchQuestion:
    """
    Domain contract for a Human-In-The-Loop research decision question.
    Presents an unresolved decision one at a time for user input/selection.
    """
    question_id: str = field(default_factory=lambda: new_id("rq-dec"))
    research_request_id: str = ""
    sequence_number: int = 1
    question: str = ""
    context: str = ""
    decision_type: DecisionType = DecisionType.SCOPE
    importance: QuestionImportance = QuestionImportance.HIGH
    state: QuestionState = QuestionState.QUESTION_PENDING
    options: list[QuestionOption] = field(default_factory=list)
    custom_answer_allowed: bool = True
    required: bool = True
    impact: str = ""
    rationale: str = ""
    created_at: str = field(default_factory=utc_now)
    expires_at: Optional[str] = None
    question_trace: list[str] = field(default_factory=list)
    provenance: Optional[QuestionProvenance] = None
    answer: Optional[ResearchAnswer] = None

    def is_active(self) -> bool:
        """Check if question is pending user action."""
        return self.state == QuestionState.QUESTION_PENDING

    def is_answered(self) -> bool:
        """Check if question was answered by user with option or custom answer."""
        if self.state == QuestionState.USER_ANSWERED:
            return True
        if self.state == QuestionState.RESOLVED and self.answer is not None and not self.answer.skipped:
            return True
        return False

    def is_skipped(self) -> bool:
        """Check if question was explicitly skipped."""
        if self.state == QuestionState.USER_SKIPPED:
            return True
        if self.answer is not None and self.answer.skipped:
            return True
        return False

    def is_resolved(self) -> bool:
        """Check if question has completed its lifecycle."""
        return self.state == QuestionState.RESOLVED

    def get_recommended_option(self) -> Optional[QuestionOption]:
        """Return the option designated as recommended, if any."""
        for opt in self.options:
            if opt.recommended:
                return opt
        return None

    def get_selected_option(self) -> Optional[QuestionOption]:
        """Return the option chosen in the answer, if an option was selected."""
        if self.answer is None or not self.answer.selected_option_id:
            return None
        for opt in self.options:
            if opt.option_id == self.answer.selected_option_id:
                return opt
        return None

    def add_trace(self, entry: str) -> None:
        """Append a timestamped step to the question trace."""
        self.question_trace.append(f"[{utc_now()}] {entry}")

    def to_dict(self) -> dict[str, Any]:
        return {
            "question_id": self.question_id,
            "research_request_id": self.research_request_id,
            "sequence_number": self.sequence_number,
            "question": self.question,
            "context": self.context,
            "decision_type": self.decision_type.value if isinstance(self.decision_type, DecisionType) else str(self.decision_type),
            "importance": self.importance.value if isinstance(self.importance, QuestionImportance) else str(self.importance),
            "state": self.state.value if isinstance(self.state, QuestionState) else str(self.state),
            "options": [opt.to_dict() for opt in self.options],
            "custom_answer_allowed": self.custom_answer_allowed,
            "required": self.required,
            "impact": self.impact,
            "rationale": self.rationale,
            "created_at": self.created_at,
            "expires_at": self.expires_at,
            "question_trace": list(self.question_trace),
            "provenance": self.provenance.to_dict() if self.provenance else None,
            "answer": self.answer.to_dict() if self.answer else None,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ResearchQuestion:
        # Resolve decision_type enum
        dt_raw = data.get("decision_type", DecisionType.SCOPE.value)
        try:
            decision_type = DecisionType(dt_raw)
        except (ValueError, TypeError):
            decision_type = DecisionType.SCOPE

        # Resolve importance enum
        imp_raw = data.get("importance", QuestionImportance.HIGH.value)
        try:
            importance = QuestionImportance(imp_raw)
        except (ValueError, TypeError):
            importance = QuestionImportance.HIGH

        # Resolve state enum
        st_raw = data.get("state", QuestionState.QUESTION_PENDING.value)
        try:
            state = QuestionState(st_raw)
        except (ValueError, TypeError):
            state = QuestionState.QUESTION_PENDING

        # Resolve options
        options = [
            QuestionOption.from_dict(opt) for opt in data.get("options", [])
            if isinstance(opt, dict)
        ]

        # Resolve provenance
        prov_data = data.get("provenance")
        provenance = QuestionProvenance.from_dict(prov_data) if isinstance(prov_data, dict) else None

        # Resolve answer
        ans_data = data.get("answer")
        answer = ResearchAnswer.from_dict(ans_data) if isinstance(ans_data, dict) else None

        return cls(
            question_id=str(data.get("question_id", new_id("rq-dec"))),
            research_request_id=str(data.get("research_request_id", "")),
            sequence_number=int(data.get("sequence_number", 1)),
            question=str(data.get("question", "")),
            context=str(data.get("context", "")),
            decision_type=decision_type,
            importance=importance,
            state=state,
            options=options,
            custom_answer_allowed=bool(data.get("custom_answer_allowed", True)),
            required=bool(data.get("required", True)),
            impact=str(data.get("impact", "")),
            rationale=str(data.get("rationale", "")),
            created_at=str(data.get("created_at", utc_now())),
            expires_at=str(data["expires_at"]) if data.get("expires_at") is not None else None,
            question_trace=list(data.get("question_trace", [])),
            provenance=provenance,
            answer=answer,
        )


# Alias for explicit distinction from Phase 1 internal crawler questions
ResearchDecisionQuestion = ResearchQuestion
