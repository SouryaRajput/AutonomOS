from __future__ import annotations

from typing import Optional

from core.research.question.model import (
    QuestionOption,
    ResearchAnswer,
    ResearchQuestion,
)
from core.research.question.types import (
    DecisionType,
    QuestionImportance,
    QuestionState,
)


class QuestionValidationError(ValueError):
    """Raised when a research question or answer violates validation constraints."""
    def __init__(self, message: str, issues: Optional[list[str]] = None):
        super().__init__(message)
        self.issues = issues or [message]


class QuestionValidator:
    """
    Deterministic validation engine for untrusted question proposals and user answers.
    Enforces security, schema correctness, option uniqueness, length bounds,
    importance policies, and answer integrity.
    """

    # Bounds
    MIN_OPTIONS = 2
    MAX_OPTIONS = 6
    MIN_QUESTION_LEN = 10
    MAX_QUESTION_LEN = 500
    MAX_CONTEXT_LEN = 2000
    MAX_LABEL_LEN = 120
    MAX_DESCRIPTION_LEN = 1000
    MAX_RATIONALE_LEN = 1000
    MAX_IMPACT_LEN = 1000
    MAX_CUSTOM_ANSWER_LEN = 2000
    MAX_SEQUENCE_NUMBER = 5

    @classmethod
    def validate_question(
        cls,
        question: ResearchQuestion,
        allow_medium: bool = False,
        raise_on_error: bool = False,
    ) -> list[str]:
        """
        Validate a ResearchQuestion. Returns a list of validation error strings.
        If raise_on_error is True, raises QuestionValidationError on failure.
        """
        issues: list[str] = []

        # 1. Required identifier & text fields
        if not question.question_id or not question.question_id.strip():
            issues.append("question_id must not be empty")

        if not question.research_request_id or not question.research_request_id.strip():
            issues.append("research_request_id must not be empty")

        q_text = question.question.strip() if question.question else ""
        if not q_text:
            issues.append("question text must not be empty")
        elif len(q_text) < cls.MIN_QUESTION_LEN:
            issues.append(f"question text too short ({len(q_text)} < {cls.MIN_QUESTION_LEN} chars)")
        elif len(q_text) > cls.MAX_QUESTION_LEN:
            issues.append(f"question text exceeds limit ({len(q_text)} > {cls.MAX_QUESTION_LEN} chars)")

        ctx_text = question.context.strip() if question.context else ""
        if not ctx_text:
            issues.append("context must not be empty")
        elif len(ctx_text) > cls.MAX_CONTEXT_LEN:
            issues.append(f"context exceeds limit ({len(ctx_text)} > {cls.MAX_CONTEXT_LEN} chars)")

        impact_text = question.impact.strip() if question.impact else ""
        if not impact_text:
            issues.append("impact description must not be empty")
        elif len(impact_text) > cls.MAX_IMPACT_LEN:
            issues.append(f"impact exceeds limit ({len(impact_text)} > {cls.MAX_IMPACT_LEN} chars)")

        rationale_text = question.rationale.strip() if question.rationale else ""
        if not rationale_text:
            issues.append("rationale must not be empty")
        elif len(rationale_text) > cls.MAX_RATIONALE_LEN:
            issues.append(f"rationale exceeds limit ({len(rationale_text)} > {cls.MAX_RATIONALE_LEN} chars)")

        # 2. Sequence number bounds
        if question.sequence_number < 1 or question.sequence_number > cls.MAX_SEQUENCE_NUMBER:
            issues.append(
                f"sequence_number {question.sequence_number} out of valid bounds (1..{cls.MAX_SEQUENCE_NUMBER})"
            )

        # 3. Decision type validation
        if not isinstance(question.decision_type, DecisionType):
            issues.append(f"invalid decision_type: {question.decision_type}")

        # 4. Importance validation and user-facing policy
        if not isinstance(question.importance, QuestionImportance):
            issues.append(f"invalid importance: {question.importance}")
        else:
            if question.importance == QuestionImportance.LOW:
                issues.append("LOW importance questions must not become user-facing prompts")
            elif question.importance == QuestionImportance.MEDIUM and not allow_medium:
                issues.append("MEDIUM importance questions require explicit allow_medium configuration")

        # 5. Options validation
        if not isinstance(question.options, list):
            issues.append("options must be a list")
        else:
            opt_count = len(question.options)
            if opt_count < cls.MIN_OPTIONS or opt_count > cls.MAX_OPTIONS:
                issues.append(
                    f"options count ({opt_count}) must be between {cls.MIN_OPTIONS} and {cls.MAX_OPTIONS}"
                )

            seen_ids: set[str] = set()
            seen_labels: set[str] = set()
            recommended_count = 0

            for idx, opt in enumerate(question.options):
                if not isinstance(opt, QuestionOption):
                    issues.append(f"option at index {idx} is not a QuestionOption instance")
                    continue

                # Option ID
                oid = opt.option_id.strip() if opt.option_id else ""
                if not oid:
                    issues.append(f"option at index {idx} has empty option_id")
                elif oid in seen_ids:
                    issues.append(f"duplicate option_id '{oid}' at index {idx}")
                else:
                    seen_ids.add(oid)

                # Option Label
                label = opt.label.strip() if opt.label else ""
                if not label:
                    issues.append(f"option at index {idx} has empty label")
                elif len(label) > cls.MAX_LABEL_LEN:
                    issues.append(f"option '{oid}' label exceeds {cls.MAX_LABEL_LEN} chars")
                elif label.lower() in seen_labels:
                    issues.append(f"duplicate option label '{label}' at index {idx}")
                else:
                    seen_labels.add(label.lower())

                # Option Description & Rationale
                if opt.description and len(opt.description) > cls.MAX_DESCRIPTION_LEN:
                    issues.append(f"option '{oid}' description exceeds {cls.MAX_DESCRIPTION_LEN} chars")
                if opt.rationale and len(opt.rationale) > cls.MAX_RATIONALE_LEN:
                    issues.append(f"option '{oid}' rationale exceeds {cls.MAX_RATIONALE_LEN} chars")

                # Recommendation checks
                if opt.recommended:
                    recommended_count += 1
                    if not opt.recommendation_reason or not opt.recommendation_reason.strip():
                        issues.append(f"recommended option '{oid}' missing recommendation_reason")

            if recommended_count > 1:
                issues.append(f"at most 1 option may be recommended; found {recommended_count}")

        # 6. Provenance sanity check if present
        if question.provenance is not None:
            if not question.provenance.research_request_id:
                issues.append("provenance missing research_request_id")
            elif question.provenance.research_request_id != question.research_request_id:
                issues.append(
                    f"provenance research_request_id mismatch: "
                    f"'{question.provenance.research_request_id}' != '{question.research_request_id}'"
                )

        if raise_on_error and issues:
            raise QuestionValidationError(f"ResearchQuestion validation failed with {len(issues)} issues: {'; '.join(issues)}", issues)

        return issues

    @classmethod
    def validate_answer(
        cls,
        question: ResearchQuestion,
        answer: ResearchAnswer,
        raise_on_error: bool = False,
    ) -> list[str]:
        """
        Validate a user's ResearchAnswer against the corresponding ResearchQuestion.
        Ensures options match, custom answer constraints are respected, and inputs are sanitized.
        """
        issues: list[str] = []

        # 1. Matching linkage
        if answer.question_id != question.question_id:
            issues.append(
                f"answer question_id '{answer.question_id}' does not match question '{question.question_id}'"
            )

        if answer.research_request_id != question.research_request_id:
            issues.append(
                f"answer research_request_id '{answer.research_request_id}' "
                f"does not match question '{question.research_request_id}'"
            )

        # 2. Skip branch
        if answer.skipped:
            if raise_on_error and issues:
                raise QuestionValidationError(f"ResearchAnswer validation failed with {len(issues)} issues: {'; '.join(issues)}", issues)
            return issues

        # 3. Response selection / custom answer
        has_option = bool(answer.selected_option_id and answer.selected_option_id.strip())
        has_custom = bool(answer.custom_answer and answer.custom_answer.strip())

        if not has_option and not has_custom:
            issues.append("answer must specify selected_option_id, custom_answer, or skipped=True")

        if has_option:
            valid_option_ids = {opt.option_id for opt in question.options}
            if answer.selected_option_id not in valid_option_ids:
                issues.append(
                    f"selected_option_id '{answer.selected_option_id}' not found in question options {sorted(valid_option_ids)}"
                )

        if has_custom:
            if not question.custom_answer_allowed:
                issues.append("custom answer provided but custom_answer_allowed is False for this question")
            elif len(answer.custom_answer.strip()) > cls.MAX_CUSTOM_ANSWER_LEN:
                issues.append(
                    f"custom_answer exceeds maximum length ({len(answer.custom_answer)} > {cls.MAX_CUSTOM_ANSWER_LEN})"
                )

        if raise_on_error and issues:
            raise QuestionValidationError(f"ResearchAnswer validation failed with {len(issues)} issues: {'; '.join(issues)}", issues)

        return issues
