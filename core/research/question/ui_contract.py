from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional
import uuid

from core.research.question.model import QuestionOption, ResearchQuestion, new_id, utc_now


@dataclass
class UIOptionView:
    """Read-only presentation projection of an option for UI modal rendering."""
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
    def from_dict(cls, data: dict[str, Any]) -> UIOptionView:
        return cls(
            option_id=str(data.get("option_id", "")),
            label=str(data.get("label", "")),
            description=str(data.get("description", "")),
            rationale=str(data.get("rationale", "")),
            recommended=bool(data.get("recommended", False)),
            recommendation_reason=str(data["recommendation_reason"]) if data.get("recommendation_reason") is not None else None,
        )


@dataclass
class UIDecisionPrompt:
    """
    Authoritative backend-generated UI contract for rendering a decision modal/popup.
    The UI renders directly from these structured fields without interpreting free-form LLM output.
    """
    prompt_id: str
    question_id: str
    research_request_id: str
    sequence_number: int
    max_questions: int
    progress_label: str
    question: str
    context: str
    decision_type: str
    importance: str
    options: list[UIOptionView]
    recommended_option_id: Optional[str] = None
    recommendation_reason: Optional[str] = None
    custom_answer_allowed: bool = True
    can_skip: bool = True
    impact: str = ""
    rationale: str = ""
    created_at: str = field(default_factory=utc_now)
    expires_at: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "prompt_id": self.prompt_id,
            "question_id": self.question_id,
            "research_request_id": self.research_request_id,
            "sequence_number": self.sequence_number,
            "max_questions": self.max_questions,
            "progress_label": self.progress_label,
            "question": self.question,
            "context": self.context,
            "decision_type": self.decision_type,
            "importance": self.importance,
            "options": [opt.to_dict() for opt in self.options],
            "recommended_option_id": self.recommended_option_id,
            "recommendation_reason": self.recommendation_reason,
            "custom_answer_allowed": self.custom_answer_allowed,
            "can_skip": self.can_skip,
            "impact": self.impact,
            "rationale": self.rationale,
            "created_at": self.created_at,
            "expires_at": self.expires_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> UIDecisionPrompt:
        options = [
            UIOptionView.from_dict(opt) for opt in data.get("options", [])
            if isinstance(opt, dict)
        ]
        return cls(
            prompt_id=str(data.get("prompt_id", new_id("prompt"))),
            question_id=str(data.get("question_id", "")),
            research_request_id=str(data.get("research_request_id", "")),
            sequence_number=int(data.get("sequence_number", 1)),
            max_questions=int(data.get("max_questions", 5)),
            progress_label=str(data.get("progress_label", "Question 1 of 5")),
            question=str(data.get("question", "")),
            context=str(data.get("context", "")),
            decision_type=str(data.get("decision_type", "")),
            importance=str(data.get("importance", "")),
            options=options,
            recommended_option_id=str(data["recommended_option_id"]) if data.get("recommended_option_id") is not None else None,
            recommendation_reason=str(data["recommendation_reason"]) if data.get("recommendation_reason") is not None else None,
            custom_answer_allowed=bool(data.get("custom_answer_allowed", True)),
            can_skip=bool(data.get("can_skip", True)),
            impact=str(data.get("impact", "")),
            rationale=str(data.get("rationale", "")),
            created_at=str(data.get("created_at", utc_now())),
            expires_at=str(data["expires_at"]) if data.get("expires_at") is not None else None,
        )

    @classmethod
    def from_question(
        cls,
        question: ResearchQuestion,
        max_questions: int = 5,
    ) -> UIDecisionPrompt:
        """Create a UIDecisionPrompt directly from an authoritative ResearchQuestion."""
        rec_opt = question.get_recommended_option()
        option_views = [
            UIOptionView(
                option_id=opt.option_id,
                label=opt.label,
                description=opt.description,
                rationale=opt.rationale,
                recommended=opt.recommended,
                recommendation_reason=opt.recommendation_reason,
            )
            for opt in question.options
        ]

        progress = f"Question {question.sequence_number} of {max_questions}"

        return cls(
            prompt_id=f"prompt-{question.question_id}",
            question_id=question.question_id,
            research_request_id=question.research_request_id,
            sequence_number=question.sequence_number,
            max_questions=max_questions,
            progress_label=progress,
            question=question.question,
            context=question.context,
            decision_type=question.decision_type.value if hasattr(question.decision_type, "value") else str(question.decision_type),
            importance=question.importance.value if hasattr(question.importance, "value") else str(question.importance),
            options=option_views,
            recommended_option_id=rec_opt.option_id if rec_opt else None,
            recommendation_reason=rec_opt.recommendation_reason if rec_opt else None,
            custom_answer_allowed=question.custom_answer_allowed,
            can_skip=True,
            impact=question.impact,
            rationale=question.rationale,
            created_at=question.created_at,
            expires_at=question.expires_at,
        )
