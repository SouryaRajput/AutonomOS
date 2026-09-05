from __future__ import annotations

from core.research.question.model import (
    QuestionOption,
    QuestionProvenance,
    ResearchAnswer,
    ResearchDecisionQuestion,
    ResearchQuestion,
    new_id,
    utc_now,
)
from core.research.question.state import (
    ActiveQuestionExistsError,
    NoActiveQuestionError,
    QuestionLimitExceededError,
    QuestionTracker,
)
from core.research.question.types import (
    AnswerSource,
    AnswerType,
    DecisionType,
    QuestionImportance,
    QuestionLifecycleState,
    QuestionState,
)
from core.research.question.generator import (
    QuestionGenerationResult,
    QuestionGenerationStatus,
    QuestionGenerator,
    QuestionProposal,
)
from core.research.question.validator import (
    QuestionValidationError,
    QuestionValidator,
)
from core.research.question.ui_contract import (
    UIDecisionPrompt,
    UIOptionView,
)
from core.research.question.lifecycle import (
    AnswerIncorporationResult,
    DuplicateAnswerError,
    ExecutionPausedError,
    QuestionLifecycleCoordinator,
    StaleQuestionError,
)

__all__ = [
    # Types & Enums
    "DecisionType",
    "QuestionImportance",
    "QuestionState",
    "QuestionLifecycleState",
    "AnswerType",
    "AnswerSource",
    # Models
    "QuestionOption",
    "QuestionProvenance",
    "ResearchAnswer",
    "ResearchQuestion",
    "ResearchDecisionQuestion",
    "utc_now",
    "new_id",
    # State & Tracker
    "QuestionTracker",
    "ActiveQuestionExistsError",
    "QuestionLimitExceededError",
    "NoActiveQuestionError",
    # Validator
    "QuestionValidator",
    "QuestionValidationError",
    # Generator (Step 2.2.2)
    "QuestionGenerator",
    "QuestionProposal",
    "QuestionGenerationResult",
    "QuestionGenerationStatus",
    # UI Contracts (Step 2.2.3)
    "UIDecisionPrompt",
    "UIOptionView",
    # Lifecycle & Coordination (Step 2.2.3)
    "QuestionLifecycleCoordinator",
    "AnswerIncorporationResult",
    "StaleQuestionError",
    "DuplicateAnswerError",
    "ExecutionPausedError",
]
