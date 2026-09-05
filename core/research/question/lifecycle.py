from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import logging
from typing import Any, Optional
import uuid

from core.research.contracts.intent import ResearchIntent, VersionScope
from core.research.contracts.request import ResearchRequest
from core.research.question.generator import (
    QuestionGenerationResult,
    QuestionGenerationStatus,
    QuestionGenerator,
)
from core.research.question.model import (
    QuestionOption,
    ResearchAnswer,
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
from core.research.question.ui_contract import UIDecisionPrompt

logger = logging.getLogger("AutonomOS.Research.QuestionLifecycle")


class StaleQuestionError(RuntimeError):
    """Raised when an answer or action references a question that is no longer pending or is not active."""
    pass


class DuplicateAnswerError(RuntimeError):
    """Raised when attempting to answer an already answered question or submitting multiple answers."""
    pass


class ExecutionPausedError(RuntimeError):
    """Raised when an execution action (such as spawning crawlers) is attempted while a decision question is pending."""
    pass


@dataclass
class AnswerIncorporationResult:
    """
    Structured outcome of incorporating a user's answer into the ResearchIntent.
    Maintains explicit audit traceability of all changes made to constraints,
    dimensions, and ambiguities.
    """
    applied_constraints: list[str] = field(default_factory=list)
    added_dimensions: list[str] = field(default_factory=list)
    resolved_ambiguities: list[str] = field(default_factory=list)
    updated_assumptions: list[str] = field(default_factory=list)
    summary: str = ""
    timestamp: str = field(default_factory=utc_now)

    def to_dict(self) -> dict[str, Any]:
        return {
            "applied_constraints": list(self.applied_constraints),
            "added_dimensions": list(self.added_dimensions),
            "resolved_ambiguities": list(self.resolved_ambiguities),
            "updated_assumptions": list(self.updated_assumptions),
            "summary": self.summary,
            "timestamp": self.timestamp,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> AnswerIncorporationResult:
        return cls(
            applied_constraints=list(data.get("applied_constraints", [])),
            added_dimensions=list(data.get("added_dimensions", [])),
            resolved_ambiguities=list(data.get("resolved_ambiguities", [])),
            updated_assumptions=list(data.get("updated_assumptions", [])),
            summary=str(data.get("summary", "")),
            timestamp=str(data.get("timestamp", utc_now())),
        )


@dataclass
class QuestionLifecycleCoordinator:
    """
    Orchestrates the Human-In-The-Loop research decision question lifecycle.

    Guarantees:
    1. Execution Pause: Researcher execution is strictly paused while a decision question is pending.
    2. Crawler Prohibition: Crawlers cannot be spawned while execution is paused.
    3. One-at-a-time Presentation: Exactly one question pending at any moment.
    4. Hard Limit Enforced: At most 5 questions per research task.
    5. Authoritative Incorporation: User choices (option or custom answer) authoritatively
       update ResearchIntent without silent overwriting.
    6. Traceable State Transitions: All states and answer traces are preserved.
    """
    research_request_id: str
    tracker: QuestionTracker = field(default_factory=lambda: QuestionTracker(research_request_id=""))
    generator: Optional[QuestionGenerator] = None
    current_state: QuestionLifecycleState = QuestionLifecycleState.NO_QUESTION
    session_trace: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        if not self.tracker.research_request_id:
            self.tracker.research_request_id = self.research_request_id
        if self.tracker.has_active_question():
            self.current_state = QuestionLifecycleState.QUESTION_PENDING
        else:
            self.current_state = QuestionLifecycleState.NO_QUESTION

    def _log_trace(self, message: str) -> None:
        entry = f"[{utc_now()}] {message}"
        self.session_trace.append(entry)
        logger.info(entry)

    def is_execution_paused(self) -> bool:
        """
        Return True if research execution is paused awaiting a user decision.
        While paused, planning cannot advance and crawlers must not be spawned.
        """
        return (
            self.current_state == QuestionLifecycleState.QUESTION_PENDING
            or self.tracker.has_active_question()
        )

    def can_spawn_crawlers(self) -> bool:
        """
        Return True if crawlers can be safely spawned.
        Guarantees crawlers cannot be spawned while a question is pending.
        """
        return not self.is_execution_paused()

    def assert_can_execute(self) -> None:
        """Raise ExecutionPausedError if execution is currently paused awaiting a decision."""
        if self.is_execution_paused():
            active_id = self.tracker.active_question.question_id if self.tracker.active_question else "unknown"
            raise ExecutionPausedError(
                f"Research execution is paused: awaiting answer for question '{active_id}'. "
                "No further execution or crawler spawning is permitted until resolved."
            )

    def get_active_prompt(self) -> Optional[UIDecisionPrompt]:
        """
        Return the UIDecisionPrompt contract for the active question if pending.
        """
        if self.tracker.has_active_question() and self.tracker.active_question:
            return UIDecisionPrompt.from_question(
                self.tracker.active_question,
                max_questions=self.tracker.max_questions,
            )
        return None

    def request_decision(
        self,
        request: ResearchRequest,
        intent: Optional[ResearchIntent] = None,
        allow_medium: bool = False,
    ) -> Optional[UIDecisionPrompt]:
        """
        Evaluate whether an unresolved high-impact decision exists and, if so,
        generate and activate exactly ONE question, pausing execution.

        Returns UIDecisionPrompt if a question is generated and activated.
        Returns None if no question is required or budget is exhausted.

        Raises:
            ActiveQuestionExistsError: If a question is already pending.
            RuntimeError: If generator is not configured.
        """
        if self.is_execution_paused():
            active_id = self.tracker.active_question.question_id if self.tracker.active_question else "active"
            raise ActiveQuestionExistsError(
                f"Cannot request new decision: Question '{active_id}' is already pending."
            )

        if self.tracker.is_limit_reached():
            self._log_trace(
                f"Question limit reached ({self.tracker.total_questions_count}/{self.tracker.max_questions}); skipping generation"
            )
            self.current_state = QuestionLifecycleState.NO_QUESTION
            return None

        if self.generator is None:
            raise RuntimeError("QuestionGenerator is not configured on QuestionLifecycleCoordinator.")

        result = self.generator.generate_question(
            request=request,
            intent=intent,
            tracker=self.tracker,
            allow_medium=allow_medium,
        )

        if result.status == QuestionGenerationStatus.QUESTION_GENERATED and result.question:
            presented = self.tracker.present_question(result.question, allow_medium=allow_medium)
            self.current_state = QuestionLifecycleState.QUESTION_PENDING
            self._log_trace(f"Question {presented.question_id} presented. Execution paused.")
            return UIDecisionPrompt.from_question(presented, max_questions=self.tracker.max_questions)
        else:
            self.current_state = QuestionLifecycleState.NO_QUESTION
            self._log_trace(f"No question required: {result.reason}")
            return None

    def submit_answer(
        self,
        answer: ResearchAnswer,
        intent: Optional[ResearchIntent] = None,
    ) -> tuple[ResearchQuestion, AnswerIncorporationResult]:
        """
        Submit an answer to the pending question, incorporating the decision
        into the ResearchIntent and unpausing execution.
        """
        if not self.tracker.has_active_question() or self.tracker.active_question is None:
            for q in self.tracker.history:
                if q.question_id == answer.question_id:
                    raise DuplicateAnswerError(f"Question '{answer.question_id}' has already been answered and resolved.")
            raise StaleQuestionError(
                f"No active question pending to answer (received question_id: '{answer.question_id}')."
            )

        active = self.tracker.active_question
        if active.question_id != answer.question_id:
            for q in self.tracker.history:
                if q.question_id == answer.question_id:
                    raise DuplicateAnswerError(f"Question '{answer.question_id}' has already been answered.")
            raise StaleQuestionError(
                f"Answer references question '{answer.question_id}', but currently active question is '{active.question_id}'."
            )

        # Record answer in tracker
        resolved_q = self.tracker.submit_answer(answer)

        # Determine temporary state before returning to NO_QUESTION
        if answer.skipped:
            self.current_state = QuestionLifecycleState.SKIPPED
        else:
            self.current_state = QuestionLifecycleState.ANSWERED

        # Incorporate into intent
        inc_result = AnswerIncorporationResult()
        if intent is not None:
            inc_result = self._incorporate_answer_into_intent(resolved_q, answer, intent)

        # Unpause execution
        self.current_state = QuestionLifecycleState.NO_QUESTION
        self._log_trace(
            f"Question {resolved_q.question_id} resolved ({'SKIPPED' if answer.skipped else 'ANSWERED'}). "
            f"Execution resumed. {inc_result.summary}"
        )

        return (resolved_q, inc_result)

    def _incorporate_answer_into_intent(
        self,
        question: ResearchQuestion,
        answer: ResearchAnswer,
        intent: ResearchIntent,
    ) -> AnswerIncorporationResult:
        """
        Incorporate the answer authoritatively into the ResearchIntent according to Invariant 10:
        - Preserves custom user inputs as primary decisions.
        - Maps architectural, scope, security, version, environment to explicit constraints.
        - Maps comparison criteria, performance, cost to research dimensions.
        - Resolves blocking ambiguities tied to unresolved_decision_id.
        - Re-evaluates clarification_required.
        - Adds audit entry to understanding_trace.
        """
        inc = AnswerIncorporationResult()
        if answer.skipped:
            msg = f"User explicitly skipped decision on '{question.question}' (DecisionType: {question.decision_type.value})."
            if answer.skip_reason:
                msg += f" Reason: {answer.skip_reason}"
            intent.understanding_trace.append(f"[{utc_now()}] {msg}")
            inc.summary = msg
            return inc

        effective_answer = answer.get_effective_answer_text(question)
        if not effective_answer:
            inc.summary = "Empty answer text provided; no intent updates applied."
            return inc

        dt = question.decision_type

        # 1. Constraints-oriented decision types
        if dt in (
            DecisionType.ARCHITECTURAL_CONSTRAINT,
            DecisionType.SCOPE,
            DecisionType.SECURITY_PRIORITY,
            DecisionType.ENVIRONMENT,
            DecisionType.TECHNICAL_DIRECTION,
            DecisionType.OTHER_HIGH_IMPACT,
        ):
            constraint_text = f"User Decision [{dt.value}]: {effective_answer}"
            if constraint_text not in intent.explicit_constraints:
                intent.explicit_constraints.append(constraint_text)
                inc.applied_constraints.append(constraint_text)

        elif dt == DecisionType.VERSION:
            constraint_text = f"User Version Specification: {effective_answer}"
            if constraint_text not in intent.explicit_constraints:
                intent.explicit_constraints.append(constraint_text)
                inc.applied_constraints.append(constraint_text)
            if intent.version_scope is None:
                intent.version_scope = VersionScope(description=effective_answer)
            else:
                if intent.version_scope.description:
                    intent.version_scope.description = f"{intent.version_scope.description}; {effective_answer}"
                else:
                    intent.version_scope.description = effective_answer

        elif dt == DecisionType.FRESHNESS:
            constraint_text = f"User Freshness Requirement: {effective_answer}"
            if constraint_text not in intent.explicit_constraints:
                intent.explicit_constraints.append(constraint_text)
                inc.applied_constraints.append(constraint_text)

        # 2. Dimensions-oriented decision types
        elif dt in (
            DecisionType.COMPARISON_CRITERIA,
            DecisionType.PERFORMANCE_PRIORITY,
            DecisionType.COST_PRIORITY,
            DecisionType.OUTPUT_EXPECTATION,
        ):
            dimension_text = f"User Priority [{dt.value}]: {effective_answer}"
            if dimension_text not in intent.research_dimensions:
                intent.research_dimensions.append(dimension_text)
                inc.added_dimensions.append(dimension_text)

        else:
            # Default fallback: add to explicit constraints
            constraint_text = f"User Decision [{dt.value}]: {effective_answer}"
            if constraint_text not in intent.explicit_constraints:
                intent.explicit_constraints.append(constraint_text)
                inc.applied_constraints.append(constraint_text)

        # 3. Ambiguity resolution
        decision_id = (
            question.provenance.unresolved_decision_id
            if question.provenance and question.provenance.unresolved_decision_id
            else None
        )
        if decision_id:
            for amb in intent.ambiguities:
                if amb.ambiguity_id == decision_id:
                    amb.blocking = False
                    amb.suggested_interpretation = f"Resolved by user decision: {effective_answer}"
                    inc.resolved_ambiguities.append(amb.ambiguity_id)

            # Remove resolved clarification questions matching this ambiguity
            intent.clarification_questions = [
                cq for cq in intent.clarification_questions
                if cq.target_ambiguity_id != decision_id
            ]

        # Update clarification_required status
        intent.clarification_required = intent.has_blocking_ambiguity()

        # 4. Record trace on intent
        trace_msg = (
            f"Incorporated user answer for decision '{question.question_id}' ({dt.value}): '{effective_answer}'. "
            f"Constraints added: {len(inc.applied_constraints)}, Dimensions added: {len(inc.added_dimensions)}, "
            f"Ambiguities resolved: {len(inc.resolved_ambiguities)}."
        )
        intent.understanding_trace.append(f"[{utc_now()}] {trace_msg}")
        inc.summary = trace_msg

        return inc

    def skip_active_question(
        self,
        skip_reason: Optional[str] = None,
        intent: Optional[ResearchIntent] = None,
    ) -> tuple[ResearchQuestion, AnswerIncorporationResult]:
        """Explicitly skip the active question and resume execution."""
        if not self.tracker.has_active_question() or self.tracker.active_question is None:
            raise StaleQuestionError("Cannot skip question: No active question is currently pending.")

        active = self.tracker.active_question
        answer = ResearchAnswer(
            question_id=active.question_id,
            research_request_id=self.research_request_id,
            skipped=True,
            skip_reason=skip_reason,
            answer_source=AnswerSource.USER,
            answer_type=AnswerType.SKIPPED,
        )
        return self.submit_answer(answer, intent=intent)

    def expire_active_question(
        self,
        expire_reason: str = "Question expired",
        use_default_recommendation: bool = False,
        intent: Optional[ResearchIntent] = None,
    ) -> tuple[ResearchQuestion, Optional[AnswerIncorporationResult]]:
        """
        Expire the currently pending question.
        If use_default_recommendation is True and the question has a recommended option,
        applies that option with AnswerSource.DEFAULT_RECOMMENDATION.
        Otherwise marks the question as EXPIRED and resumes execution.
        """
        if not self.tracker.has_active_question() or self.tracker.active_question is None:
            raise StaleQuestionError("Cannot expire question: No active question is currently pending.")

        active = self.tracker.active_question
        if use_default_recommendation:
            rec = active.get_recommended_option()
            if rec is not None:
                answer = ResearchAnswer(
                    question_id=active.question_id,
                    research_request_id=self.research_request_id,
                    selected_option_id=rec.option_id,
                    answer_source=AnswerSource.DEFAULT_RECOMMENDATION,
                    answer_type=AnswerType.OPTION_SELECTED,
                    metadata={"fallback_to_recommended": True, "expire_reason": expire_reason},
                )
                return self.submit_answer(answer, intent=intent)

        # Pure expiration without recommendation fallback
        expired_q = self.tracker.expire_active_question(expire_reason=expire_reason)
        self.current_state = QuestionLifecycleState.NO_QUESTION
        self._log_trace(f"Question {expired_q.question_id} expired ({expire_reason}). Execution resumed.")
        return (expired_q, None)

    def cancel_active_question(
        self,
        cancel_reason: str = "Question cancelled by system",
    ) -> ResearchQuestion:
        """Cancel the active pending question and resume execution."""
        if not self.tracker.has_active_question() or self.tracker.active_question is None:
            raise StaleQuestionError("Cannot cancel question: No active question is currently pending.")

        cancelled_q = self.tracker.cancel_active_question(cancel_reason=cancel_reason)
        self.current_state = QuestionLifecycleState.NO_QUESTION
        self._log_trace(f"Question {cancelled_q.question_id} cancelled ({cancel_reason}). Execution resumed.")
        return cancelled_q

    def to_dict(self) -> dict[str, Any]:
        return {
            "research_request_id": self.research_request_id,
            "tracker": self.tracker.to_dict(),
            "current_state": self.current_state.value,
            "session_trace": list(self.session_trace),
        }

    @classmethod
    def from_dict(
        cls,
        data: dict[str, Any],
        generator: Optional[QuestionGenerator] = None,
    ) -> QuestionLifecycleCoordinator:
        tracker_data = data.get("tracker", {})
        tracker = QuestionTracker.from_dict(tracker_data)

        st_raw = data.get("current_state", QuestionLifecycleState.NO_QUESTION.value)
        try:
            current_state = QuestionLifecycleState(st_raw)
        except (ValueError, TypeError):
            current_state = QuestionLifecycleState.NO_QUESTION

        return cls(
            research_request_id=str(data.get("research_request_id", "")),
            tracker=tracker,
            generator=generator,
            current_state=current_state,
            session_trace=list(data.get("session_trace", [])),
        )
