from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
import json
import logging
import re
from typing import Any, Optional, Union

from core.research.contracts.intent import ResearchIntent
from core.research.contracts.request import ResearchRequest
from core.research.decomposition.constraints import DecompositionConstraintValidator
from core.research.decomposition.generator import (
    DecompositionProposal,
    RawSubQuestionProposal,
)
from core.research.decomposition.model import (
    ResearchDecomposition,
    ResearchDependency,
    ResearchSubQuestion,
    SubQuestionProvenance,
    new_id,
    utc_now,
)
from core.research.decomposition.normalizer import (
    DecompositionNormalizer,
    canonicalize_text,
    normalize_whitespace,
)
from core.research.decomposition.policy import DecompositionPolicy
from core.research.decomposition.types import (
    ResearchDependencyType,
    SubQuestionPriority,
    SubQuestionStatus,
    SubQuestionType,
)
from core.research.decomposition.validator import DecompositionValidator

logger = logging.getLogger("AutonomOS.Research.DecompositionRefinement")


class DecompositionValidationStatus(str, Enum):
    """
    Structured outcome status for decomposition proposal validation.
    - VALID: Proposal satisfies all structural and policy constraints after safe refinement.
    - INVALID: Proposal or input has fatal, non-retryable defects (e.g. malformed JSON, empty ResearchIntent, massive payload).
    - RETRYABLE_INVALID: Proposal has defects that can be corrected by an LLM retry with feedback (e.g. count violation, cycle, duplicates, missing required fields).
    """
    VALID = "VALID"
    INVALID = "INVALID"
    RETRYABLE_INVALID = "RETRYABLE_INVALID"


class DecompositionValidationIssueCode(str, Enum):
    """Normalized error codes for decomposition validation failures."""
    TOO_MANY_QUESTIONS = "TOO_MANY_QUESTIONS"
    ZERO_QUESTIONS = "ZERO_QUESTIONS"
    DUPLICATE_QUESTION = "DUPLICATE_QUESTION"
    MALFORMED_QUESTION = "MALFORMED_QUESTION"
    INVALID_PRIORITY = "INVALID_PRIORITY"
    INVALID_STATUS = "INVALID_STATUS"
    INVALID_DEPENDENCY = "INVALID_DEPENDENCY"
    DEPENDENCY_CYCLE = "DEPENDENCY_CYCLE"
    EMPTY_OBJECTIVE = "EMPTY_OBJECTIVE"
    OVERSIZED_PAYLOAD = "OVERSIZED_PAYLOAD"
    REDUNDANT_ROOT_QUESTION = "REDUNDANT_ROOT_QUESTION"
    INVALID_ID = "INVALID_ID"
    INCONSISTENT_REQUIRED_FIELDS = "INCONSISTENT_REQUIRED_FIELDS"
    MALFORMED_OUTPUT = "MALFORMED_OUTPUT"
    INVALID_INTENT = "INVALID_INTENT"
    TEXT_TOO_SHORT = "TEXT_TOO_SHORT"
    TEXT_TOO_LONG = "TEXT_TOO_LONG"


@dataclass
class DecompositionValidationIssue:
    """Structured issue identifying a specific validation defect and its retryability."""
    code: str
    message: str
    field: Optional[str] = None
    sub_question_id: Optional[str] = None
    retryable: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "message": self.message,
            "field": self.field,
            "sub_question_id": self.sub_question_id,
            "retryable": self.retryable,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> DecompositionValidationIssue:
        return cls(
            code=str(data.get("code", "")),
            message=str(data.get("message", "")),
            field=data.get("field"),
            sub_question_id=data.get("sub_question_id"),
            retryable=bool(data.get("retryable", True)),
        )


@dataclass
class DecompositionValidationResult:
    """
    Structured outcome of the decomposition proposal validation and refinement process.
    Never exposes raw unverified LLM reasoning as trusted state.
    """
    status: DecompositionValidationStatus
    decomposition: Optional[ResearchDecomposition] = None
    proposal: Optional[DecompositionProposal] = None
    issues: list[DecompositionValidationIssue] = field(default_factory=list)
    refinements_applied: list[str] = field(default_factory=list)
    confidence: float = 1.0
    trace: list[str] = field(default_factory=list)
    raw_response: Optional[str] = None

    @property
    def is_valid(self) -> bool:
        return self.status == DecompositionValidationStatus.VALID

    @property
    def is_retryable(self) -> bool:
        return self.status == DecompositionValidationStatus.RETRYABLE_INVALID

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status.value if isinstance(self.status, DecompositionValidationStatus) else str(self.status),
            "decomposition": self.decomposition.to_dict() if self.decomposition else None,
            "proposal": self.proposal.to_dict() if self.proposal else None,
            "issues": [issue.to_dict() for issue in self.issues],
            "refinements_applied": list(self.refinements_applied),
            "confidence": self.confidence,
            "trace": list(self.trace),
            "raw_response": self.raw_response,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> DecompositionValidationResult:
        st_raw = data.get("status", DecompositionValidationStatus.INVALID.value)
        try:
            status = DecompositionValidationStatus(st_raw)
        except (ValueError, TypeError):
            status = DecompositionValidationStatus.INVALID

        d_raw = data.get("decomposition")
        decomp = ResearchDecomposition.from_dict(d_raw) if isinstance(d_raw, dict) else None

        p_raw = data.get("proposal")
        prop = DecompositionProposal.from_dict(p_raw) if isinstance(p_raw, dict) else None

        issues = [
            DecompositionValidationIssue.from_dict(i)
            for i in data.get("issues", [])
            if isinstance(i, dict)
        ]

        return cls(
            status=status,
            decomposition=decomp,
            proposal=prop,
            issues=issues,
            refinements_applied=list(data.get("refinements_applied", [])),
            confidence=float(data.get("confidence", 1.0)),
            trace=list(data.get("trace", [])),
            raw_response=data.get("raw_response"),
        )


class DecompositionRefiner:
    """
    Deterministic validation and refinement engine for research decomposition proposals.
    
    Safe deterministic refinements performed:
    - Canonical whitespace normalization across all textual elements.
    - Deterministic, consistent ID generation linking temp_ids to persistent sub_question_ids.
    - Normalization of priority and type enum strings to canonical enum instances.
    - Defaulting safe optional fields (e.g. required=True, dependencies=[], rationale=\"\").
    - Exact canonical duplicate removal ONLY if explicitly allowed by policy.
    - Deterministic stable ordering preservation.

    Strict Prohibitions:
    - No semantic rewriting.
    - No embeddings or semantic similarity guessing.
    - No inventing missing research questions.
    - No silently changing the analytical meaning of an LLM proposal.
    - Inadequate semantic quality or structural integrity results in rejection (INVALID or RETRYABLE_INVALID).
    """

    @classmethod
    def validate_and_refine(
        cls,
        intent: ResearchIntent,
        proposal: Union[DecompositionProposal, dict[str, Any], str, ResearchDecomposition, None],
        request: Optional[ResearchRequest] = None,
        policy: Optional[DecompositionPolicy] = None,
    ) -> DecompositionValidationResult:
        """
        Validate and deterministically refine a decomposition proposal against a ResearchIntent.
        Returns a structured DecompositionValidationResult.
        """
        trace: list[str] = [f"[{utc_now()}] Initiating decomposition proposal validation & refinement"]
        active_policy = policy or DecompositionPolicy()
        refinements: list[str] = []
        issues: list[DecompositionValidationIssue] = []

        # 1. Validate Input ResearchIntent (Fatal if defective)
        if intent is None or not isinstance(intent, ResearchIntent):
            trace.append("Fatal defect: missing or invalid ResearchIntent")
            issues.append(
                DecompositionValidationIssue(
                    code=DecompositionValidationIssueCode.INVALID_INTENT.value,
                    message="A valid ResearchIntent instance is required for decomposition.",
                    field="intent",
                    retryable=False,
                )
            )
            return DecompositionValidationResult(
                status=DecompositionValidationStatus.INVALID,
                issues=issues,
                trace=trace,
            )

        if not intent.objective or not intent.objective.strip():
            trace.append("Fatal defect: ResearchIntent objective is empty")
            issues.append(
                DecompositionValidationIssue(
                    code=DecompositionValidationIssueCode.EMPTY_OBJECTIVE.value,
                    message="ResearchIntent objective cannot be empty or whitespace.",
                    field="intent.objective",
                    retryable=False,
                )
            )
            return DecompositionValidationResult(
                status=DecompositionValidationStatus.INVALID,
                issues=issues,
                trace=trace,
            )

        # 2. Parse / Normalize Input Proposal
        decomp_prop: Optional[DecompositionProposal] = None
        raw_response: Optional[str] = None
        candidate_decomp: Optional[ResearchDecomposition] = None

        if proposal is None:
            trace.append("Proposal is None; fatal malformed input")
            issues.append(
                DecompositionValidationIssue(
                    code=DecompositionValidationIssueCode.MALFORMED_OUTPUT.value,
                    message="Proposal is None; language model output missing or empty.",
                    retryable=False,
                )
            )
            return DecompositionValidationResult(
                status=DecompositionValidationStatus.INVALID,
                issues=issues,
                trace=trace,
            )

        if isinstance(proposal, str):
            raw_response = proposal
            decomp_prop = cls._parse_raw_string(proposal)
            if decomp_prop is None:
                trace.append("Failed to parse raw response string into JSON structure")
                issues.append(
                    DecompositionValidationIssue(
                        code=DecompositionValidationIssueCode.MALFORMED_OUTPUT.value,
                        message="Raw model output could not be parsed into a valid JSON decomposition structure.",
                        retryable=False,
                    )
                )
                return DecompositionValidationResult(
                    status=DecompositionValidationStatus.INVALID,
                    raw_response=raw_response,
                    issues=issues,
                    trace=trace,
                )
        elif isinstance(proposal, dict):
            try:
                decomp_prop = DecompositionProposal.from_dict(proposal)
                raw_response = str(proposal)
            except Exception as ex:
                trace.append(f"Failed to parse proposal dictionary: {ex}")
                issues.append(
                    DecompositionValidationIssue(
                        code=DecompositionValidationIssueCode.MALFORMED_OUTPUT.value,
                        message=f"Proposal dictionary is malformed: {ex}",
                        retryable=False,
                    )
                )
                return DecompositionValidationResult(
                    status=DecompositionValidationStatus.INVALID,
                    issues=issues,
                    trace=trace,
                )
        elif isinstance(proposal, DecompositionProposal):
            decomp_prop = proposal
            raw_response = proposal.raw_response or None
        elif isinstance(proposal, ResearchDecomposition):
            candidate_decomp = proposal
        else:
            trace.append(f"Unknown proposal type: {type(proposal).__name__}")
            issues.append(
                DecompositionValidationIssue(
                    code=DecompositionValidationIssueCode.MALFORMED_OUTPUT.value,
                    message=f"Unsupported proposal object type '{type(proposal).__name__}'.",
                    retryable=False,
                )
            )
            return DecompositionValidationResult(
                status=DecompositionValidationStatus.INVALID,
                issues=issues,
                trace=trace,
            )

        # 3. Payload Size Check
        if raw_response:
            payload_len = len(raw_response.encode("utf-8"))
            if payload_len > active_policy.max_payload_bytes:
                trace.append(f"Oversized payload: {payload_len} > {active_policy.max_payload_bytes}")
                issues.append(
                    DecompositionValidationIssue(
                        code=DecompositionValidationIssueCode.OVERSIZED_PAYLOAD.value,
                        message=f"Payload size ({payload_len} bytes) exceeds maximum limit ({active_policy.max_payload_bytes} bytes).",
                        retryable=False,
                    )
                )
                return DecompositionValidationResult(
                    status=DecompositionValidationStatus.INVALID,
                    issues=issues,
                    trace=trace,
                    raw_response=raw_response,
                )

        # 4. Convert Proposal to Candidate Decomposition if needed
        if candidate_decomp is None and decomp_prop is not None:
            # Check question count bounds on raw proposal first
            raw_count = len(decomp_prop.sub_questions)
            if raw_count == 0:
                trace.append("Proposal contains zero sub-questions (retryable)")
                issues.append(
                    DecompositionValidationIssue(
                        code=DecompositionValidationIssueCode.ZERO_QUESTIONS.value,
                        message="Proposal contains zero sub-questions; minimum required is 1.",
                        field="sub_questions",
                        retryable=True,
                    )
                )
                return DecompositionValidationResult(
                    status=DecompositionValidationStatus.RETRYABLE_INVALID,
                    proposal=decomp_prop,
                    issues=issues,
                    trace=trace,
                    raw_response=raw_response,
                )

            # Build candidate decomposition
            candidate_decomp = cls._build_candidate_decomposition(
                proposal=decomp_prop,
                intent=intent,
                request=request,
                refinements=refinements,
            )

        if candidate_decomp is None:
            issues.append(
                DecompositionValidationIssue(
                    code=DecompositionValidationIssueCode.MALFORMED_OUTPUT.value,
                    message="Failed to assemble candidate decomposition from proposal.",
                    retryable=False,
                )
            )
            return DecompositionValidationResult(
                status=DecompositionValidationStatus.INVALID,
                issues=issues,
                trace=trace,
            )

        # 5. Safe Deterministic Refinement
        candidate_decomp, ref_actions = cls.refine(candidate_decomp, policy=active_policy)
        refinements.extend(ref_actions)
        trace.extend(ref_actions)

        # 6. Deterministic Invariant & Constraint Validation
        val_status, val_issues = cls.validate(
            candidate=candidate_decomp,
            intent=intent,
            policy=active_policy,
        )
        issues.extend(val_issues)

        # Determine overall validation status
        if not issues:
            trace.append(f"Decomposition successfully validated with {len(candidate_decomp.sub_questions)} sub-questions")
            return DecompositionValidationResult(
                status=DecompositionValidationStatus.VALID,
                decomposition=candidate_decomp,
                proposal=decomp_prop,
                issues=[],
                refinements_applied=refinements,
                confidence=candidate_decomp.decomposition_confidence,
                trace=trace,
                raw_response=raw_response,
            )

        # Any non-retryable issue makes the overall status INVALID; otherwise RETRYABLE_INVALID
        has_fatal = any(not i.retryable for i in issues)
        final_status = DecompositionValidationStatus.INVALID if has_fatal else DecompositionValidationStatus.RETRYABLE_INVALID
        trace.append(f"Validation failed with status {final_status.value} ({len(issues)} issue(s))")

        return DecompositionValidationResult(
            status=final_status,
            decomposition=candidate_decomp,
            proposal=decomp_prop,
            issues=issues,
            refinements_applied=refinements,
            confidence=candidate_decomp.decomposition_confidence,
            trace=trace,
            raw_response=raw_response,
        )

    # -------------------------------------------------------------------------
    # Safe Deterministic Refinement
    # -------------------------------------------------------------------------
    @classmethod
    def refine(
        cls,
        candidate: ResearchDecomposition,
        policy: Optional[DecompositionPolicy] = None,
    ) -> tuple[ResearchDecomposition, list[str]]:
        actions: list[str] = []
        active_policy = policy or DecompositionPolicy()

        # 1. Normalizer pass (whitespace, string lists, enum types)
        norm_obj = normalize_whitespace(candidate.objective)
        if norm_obj != candidate.objective:
            actions.append("normalized_root_objective_whitespace")
            candidate.objective = norm_obj

        norm_root_q = normalize_whitespace(candidate.root_question)
        if norm_root_q != candidate.root_question:
            actions.append("normalized_root_question_whitespace")
            candidate.root_question = norm_root_q

        candidate.coverage_requirements = DecompositionNormalizer._clean_string_list(
            candidate.coverage_requirements, actions, "coverage_requirements"
        )
        candidate.unresolved_decisions = DecompositionNormalizer._clean_string_list(
            candidate.unresolved_decisions, actions, "unresolved_decisions"
        )

        for sq in candidate.sub_questions:
            DecompositionNormalizer._normalize_sub_question(sq, candidate.decomposition_id, actions)

        candidate.dependencies = DecompositionNormalizer._normalize_dependencies(
            candidate.dependencies, actions
        )

        # 2. Exact Duplicate Removal (ONLY IF PERMITTED BY POLICY)
        if getattr(active_policy, "allow_exact_deduplication", False):
            seen_canonical: set[str] = set()
            kept_sqs: list[ResearchSubQuestion] = []
            dropped_ids: set[str] = set()

            for sq in candidate.sub_questions:
                c_text = canonicalize_text(sq.question)
                if c_text in seen_canonical:
                    dropped_ids.add(sq.sub_question_id)
                    actions.append(f"safely_deduplicated_sub_question:{sq.sub_question_id}")
                else:
                    seen_canonical.add(c_text)
                    kept_sqs.append(sq)

            if dropped_ids:
                candidate.sub_questions = kept_sqs
                kept_ids = {sq.sub_question_id for sq in kept_sqs}
                # Prune dropped dependencies
                for sq in candidate.sub_questions:
                    sq.dependencies = [d for d in sq.dependencies if d not in dropped_ids]
                candidate.dependencies = [
                    dep for dep in candidate.dependencies
                    if dep.prerequisite_id in kept_ids and dep.dependent_id in kept_ids
                ]

        # 3. Reduction if over max (ONLY IF PERMITTED BY POLICY)
        if active_policy.allow_reduction_if_over_max and len(candidate.sub_questions) > active_policy.max_sub_questions:
            candidate, red_actions = active_policy.reduce_to_limit(candidate)
            actions.extend(red_actions)

        # 4. Confidence Clamping
        if candidate.decomposition_confidence < 0.0:
            candidate.decomposition_confidence = 0.0
            actions.append("clamped_confidence_to_min_0.0")
        elif candidate.decomposition_confidence > 1.0:
            candidate.decomposition_confidence = 1.0
            actions.append("clamped_confidence_to_max_1.0")

        return candidate, actions

    # -------------------------------------------------------------------------
    # Invariant & Constraint Validation
    # -------------------------------------------------------------------------
    @classmethod
    def validate(
        cls,
        candidate: ResearchDecomposition,
        intent: ResearchIntent,
        policy: Optional[DecompositionPolicy] = None,
    ) -> tuple[DecompositionValidationStatus, list[DecompositionValidationIssue]]:
        active_policy = policy or DecompositionPolicy()
        issues: list[DecompositionValidationIssue] = []

        # 1. Basic structural checks
        if not candidate.decomposition_id or not candidate.decomposition_id.strip():
            issues.append(
                DecompositionValidationIssue(
                    code=DecompositionValidationIssueCode.INVALID_ID.value,
                    message="Decomposition ID must not be empty.",
                    field="decomposition_id",
                    retryable=False,
                )
            )

        if not candidate.objective or not candidate.objective.strip():
            issues.append(
                DecompositionValidationIssue(
                    code=DecompositionValidationIssueCode.EMPTY_OBJECTIVE.value,
                    message="Decomposition objective must not be empty.",
                    field="objective",
                    retryable=False,
                )
            )

        # 2. Sub-question count bounds
        sq_count = len(candidate.sub_questions)
        if sq_count < active_policy.min_sub_questions:
            issues.append(
                DecompositionValidationIssue(
                    code=DecompositionValidationIssueCode.ZERO_QUESTIONS.value if sq_count == 0 else DecompositionValidationIssueCode.TOO_MANY_QUESTIONS.value,
                    message=f"Decomposition contains {sq_count} sub-questions; minimum required is {active_policy.min_sub_questions}.",
                    field="sub_questions",
                    retryable=True,
                )
            )
        elif sq_count > active_policy.max_sub_questions:
            issues.append(
                DecompositionValidationIssue(
                    code=DecompositionValidationIssueCode.TOO_MANY_QUESTIONS.value,
                    message=f"Decomposition contains {sq_count} sub-questions; exceeds maximum allowed limit of {active_policy.max_sub_questions}.",
                    field="sub_questions",
                    retryable=True,
                )
            )

        # 3. Required questions consistency
        required_count = sum(1 for sq in candidate.sub_questions if getattr(sq, "required", True))
        if sq_count >= active_policy.min_sub_questions and required_count < active_policy.min_sub_questions:
            issues.append(
                DecompositionValidationIssue(
                    code=DecompositionValidationIssueCode.INCONSISTENT_REQUIRED_FIELDS.value,
                    message=f"Decomposition has {required_count} required sub-questions; at least {active_policy.min_sub_questions} required sub-question(s) must be present.",
                    field="sub_questions.required",
                    retryable=True,
                )
            )

        # 4. Check each sub-question
        sq_map: dict[str, ResearchSubQuestion] = {}
        canonical_questions: dict[str, str] = {}
        canonical_objectives: dict[str, str] = {}
        canon_root_q = canonicalize_text(candidate.root_question)
        canon_root_obj = canonicalize_text(candidate.objective)

        for idx, sq in enumerate(candidate.sub_questions):
            # Guard against user-facing ResearchQuestion
            if hasattr(sq, "options") and hasattr(sq, "custom_answer_allowed"):
                issues.append(
                    DecompositionValidationIssue(
                        code=DecompositionValidationIssueCode.MALFORMED_QUESTION.value,
                        message=f"Sub-question at index {idx} appears to be a user-facing ResearchQuestion, not an internal ResearchSubQuestion.",
                        sub_question_id=getattr(sq, "sub_question_id", str(idx)),
                        retryable=True,
                    )
                )
                continue

            if not isinstance(sq, ResearchSubQuestion):
                issues.append(
                    DecompositionValidationIssue(
                        code=DecompositionValidationIssueCode.MALFORMED_QUESTION.value,
                        message=f"Sub-question at index {idx} is not an instance of ResearchSubQuestion.",
                        retryable=True,
                    )
                )
                continue

            sq_id = sq.sub_question_id.strip() if sq.sub_question_id else ""
            if not sq_id:
                issues.append(
                    DecompositionValidationIssue(
                        code=DecompositionValidationIssueCode.INVALID_ID.value,
                        message=f"Sub-question at index {idx} has an empty sub_question_id.",
                        retryable=True,
                    )
                )
                continue

            if sq_id in sq_map:
                issues.append(
                    DecompositionValidationIssue(
                        code=DecompositionValidationIssueCode.DUPLICATE_QUESTION.value,
                        message=f"Duplicate sub_question_id detected: '{sq_id}'.",
                        sub_question_id=sq_id,
                        retryable=True,
                    )
                )
            else:
                sq_map[sq_id] = sq

            # Question text quality
            q_text = sq.question.strip() if sq.question else ""
            if not q_text:
                issues.append(
                    DecompositionValidationIssue(
                        code=DecompositionValidationIssueCode.MALFORMED_QUESTION.value,
                        message=f"Sub-question '{sq_id}' has empty question text.",
                        sub_question_id=sq_id,
                        field="question",
                        retryable=True,
                    )
                )
            else:
                if not re.search(r"[a-zA-Z0-9]", q_text):
                    issues.append(
                        DecompositionValidationIssue(
                            code=DecompositionValidationIssueCode.MALFORMED_QUESTION.value,
                            message=f"Sub-question '{sq_id}' question lacks substantive alphanumeric content.",
                            sub_question_id=sq_id,
                            field="question",
                            retryable=True,
                        )
                    )
                elif len(q_text) < active_policy.min_question_len:
                    issues.append(
                        DecompositionValidationIssue(
                            code=DecompositionValidationIssueCode.TEXT_TOO_SHORT.value,
                            message=f"Sub-question '{sq_id}' question length ({len(q_text)}) is below minimum ({active_policy.min_question_len} chars).",
                            sub_question_id=sq_id,
                            field="question",
                            retryable=True,
                        )
                    )
                elif len(q_text) > active_policy.max_question_len:
                    issues.append(
                        DecompositionValidationIssue(
                            code=DecompositionValidationIssueCode.TEXT_TOO_LONG.value,
                            message=f"Sub-question '{sq_id}' question length ({len(q_text)}) exceeds limit ({active_policy.max_question_len} chars).",
                            sub_question_id=sq_id,
                            field="question",
                            retryable=True,
                        )
                    )

            # Objective quality
            obj_text = sq.objective.strip() if sq.objective else ""
            if not obj_text:
                issues.append(
                    DecompositionValidationIssue(
                        code=DecompositionValidationIssueCode.EMPTY_OBJECTIVE.value,
                        message=f"Sub-question '{sq_id}' has empty objective.",
                        sub_question_id=sq_id,
                        field="objective",
                        retryable=True,
                    )
                )
            else:
                if not re.search(r"[a-zA-Z0-9]", obj_text):
                    issues.append(
                        DecompositionValidationIssue(
                            code=DecompositionValidationIssueCode.MALFORMED_QUESTION.value,
                            message=f"Sub-question '{sq_id}' objective lacks substantive alphanumeric content.",
                            sub_question_id=sq_id,
                            field="objective",
                            retryable=True,
                        )
                    )
                elif len(obj_text) < active_policy.min_objective_len:
                    issues.append(
                        DecompositionValidationIssue(
                            code=DecompositionValidationIssueCode.TEXT_TOO_SHORT.value,
                            message=f"Sub-question '{sq_id}' objective length ({len(obj_text)}) is below minimum ({active_policy.min_objective_len} chars).",
                            sub_question_id=sq_id,
                            field="objective",
                            retryable=True,
                        )
                    )
                elif len(obj_text) > active_policy.max_objective_len:
                    issues.append(
                        DecompositionValidationIssue(
                            code=DecompositionValidationIssueCode.TEXT_TOO_LONG.value,
                            message=f"Sub-question '{sq_id}' objective length ({len(obj_text)}) exceeds limit ({active_policy.max_objective_len} chars).",
                            sub_question_id=sq_id,
                            field="objective",
                            retryable=True,
                        )
                    )

            # Priority & Status
            if not isinstance(sq.priority, SubQuestionPriority):
                try:
                    SubQuestionPriority(str(sq.priority).upper())
                except ValueError:
                    issues.append(
                        DecompositionValidationIssue(
                            code=DecompositionValidationIssueCode.INVALID_PRIORITY.value,
                            message=f"Sub-question '{sq_id}' has invalid priority '{sq.priority}'.",
                            sub_question_id=sq_id,
                            field="priority",
                            retryable=True,
                        )
                    )

            if not isinstance(sq.status, SubQuestionStatus):
                try:
                    SubQuestionStatus(str(sq.status).upper())
                except ValueError:
                    issues.append(
                        DecompositionValidationIssue(
                            code=DecompositionValidationIssueCode.INVALID_STATUS.value,
                            message=f"Sub-question '{sq_id}' has invalid status '{sq.status}'.",
                            sub_question_id=sq_id,
                            field="status",
                            retryable=True,
                        )
                    )

            # Duplicate question detection
            canon_q = canonicalize_text(q_text)
            if canon_q:
                if canon_q in canonical_questions:
                    other_id = canonical_questions[canon_q]
                    issues.append(
                        DecompositionValidationIssue(
                            code=DecompositionValidationIssueCode.DUPLICATE_QUESTION.value,
                            message=f"Sub-question '{sq_id}' question text is canonically identical to '{other_id}'.",
                            sub_question_id=sq_id,
                            field="question",
                            retryable=True,
                        )
                    )
                else:
                    canonical_questions[canon_q] = sq_id

            canon_o = canonicalize_text(obj_text)
            if canon_o:
                if canon_o in canonical_objectives:
                    other_id = canonical_objectives[canon_o]
                    issues.append(
                        DecompositionValidationIssue(
                            code=DecompositionValidationIssueCode.DUPLICATE_QUESTION.value,
                            message=f"Sub-question '{sq_id}' objective is canonically identical to '{other_id}'.",
                            sub_question_id=sq_id,
                            field="objective",
                            retryable=True,
                        )
                    )
                else:
                    canonical_objectives[canon_o] = sq_id

            # Redundant root question / objective
            if canon_q and canon_root_q and canon_q == canon_root_q:
                issues.append(
                    DecompositionValidationIssue(
                        code=DecompositionValidationIssueCode.REDUNDANT_ROOT_QUESTION.value,
                        message=f"Sub-question '{sq_id}' trivially duplicates root question.",
                        sub_question_id=sq_id,
                        field="question",
                        retryable=True,
                    )
                )

            if canon_q and canon_root_obj and canon_q == canon_root_obj:
                issues.append(
                    DecompositionValidationIssue(
                        code=DecompositionValidationIssueCode.REDUNDANT_ROOT_QUESTION.value,
                        message=f"Sub-question '{sq_id}' trivially duplicates root objective.",
                        sub_question_id=sq_id,
                        field="question",
                        retryable=True,
                    )
                )

        # 5. Dependency Resolution & Cycle Check
        dep_graph: dict[str, set[str]] = {sq_id: set() for sq_id in sq_map}
        for sq_id, sq in sq_map.items():
            for prereq_id in sq.dependencies:
                if prereq_id == sq_id:
                    issues.append(
                        DecompositionValidationIssue(
                            code=DecompositionValidationIssueCode.INVALID_DEPENDENCY.value,
                            message=f"Sub-question '{sq_id}' cannot depend on itself.",
                            sub_question_id=sq_id,
                            field="dependencies",
                            retryable=True,
                        )
                    )
                elif prereq_id not in sq_map:
                    issues.append(
                        DecompositionValidationIssue(
                            code=DecompositionValidationIssueCode.INVALID_DEPENDENCY.value,
                            message=f"Sub-question '{sq_id}' references non-existent dependency '{prereq_id}'.",
                            sub_question_id=sq_id,
                            field="dependencies",
                            retryable=True,
                        )
                    )
                else:
                    dep_graph[sq_id].add(prereq_id)

        for dep in candidate.dependencies:
            if not isinstance(dep, ResearchDependency):
                continue
            if dep.prerequisite_id == dep.dependent_id:
                issues.append(
                    DecompositionValidationIssue(
                        code=DecompositionValidationIssueCode.INVALID_DEPENDENCY.value,
                        message=f"Dependency '{dep.dependency_id}' defines self-dependency on '{dep.dependent_id}'.",
                        field="dependencies",
                        retryable=True,
                    )
                )
            if dep.prerequisite_id not in sq_map:
                issues.append(
                    DecompositionValidationIssue(
                        code=DecompositionValidationIssueCode.INVALID_DEPENDENCY.value,
                        message=f"Dependency '{dep.dependency_id}' references non-existent prerequisite '{dep.prerequisite_id}'.",
                        field="dependencies",
                        retryable=True,
                    )
                )
            if dep.dependent_id not in sq_map:
                issues.append(
                    DecompositionValidationIssue(
                        code=DecompositionValidationIssueCode.INVALID_DEPENDENCY.value,
                        message=f"Dependency '{dep.dependency_id}' references non-existent dependent '{dep.dependent_id}'.",
                        field="dependencies",
                        retryable=True,
                    )
                )

        # Cycle check using 3-color DFS
        cycle_issues: list[str] = []
        DecompositionValidator._check_dependency_cycles(dep_graph, cycle_issues)
        for c_msg in cycle_issues:
            issues.append(
                DecompositionValidationIssue(
                    code=DecompositionValidationIssueCode.DEPENDENCY_CYCLE.value,
                    message=c_msg,
                    field="dependencies",
                    retryable=True,
                )
            )

        # Status determination
        if not issues:
            return DecompositionValidationStatus.VALID, []

        has_fatal = any(not i.retryable for i in issues)
        status = DecompositionValidationStatus.INVALID if has_fatal else DecompositionValidationStatus.RETRYABLE_INVALID
        return status, issues

    # -------------------------------------------------------------------------
    # Helper: Parsing Raw Strings
    # -------------------------------------------------------------------------
    @classmethod
    def _parse_raw_string(cls, text: str) -> Optional[DecompositionProposal]:
        if not text or not text.strip():
            return None

        cleaned = text.strip()
        if cleaned.startswith("```"):
            cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
            cleaned = re.sub(r"\s*```$", "", cleaned)
            cleaned = cleaned.strip()

        try:
            data = json.loads(cleaned)
        except json.JSONDecodeError:
            match = re.search(r"(\{.*\})", cleaned, re.DOTALL)
            if match:
                try:
                    data = json.loads(match.group(1))
                except json.JSONDecodeError:
                    return None
            else:
                return None

        if not isinstance(data, dict):
            return None

        if "sub_questions" not in data or not isinstance(data["sub_questions"], list):
            return None

        proposal = DecompositionProposal.from_dict(data)
        proposal.raw_response = text
        return proposal

    # -------------------------------------------------------------------------
    # Helper: Build Candidate Decomposition
    # -------------------------------------------------------------------------
    @classmethod
    def _build_candidate_decomposition(
        cls,
        proposal: DecompositionProposal,
        intent: ResearchIntent,
        request: Optional[ResearchRequest],
        refinements: list[str],
    ) -> ResearchDecomposition:
        decomp_id = new_id("decomp")
        req_id = (
            getattr(request, "request_id", "")
            if request and getattr(request, "request_id", None)
            else (getattr(intent, "source_request_id", "") or getattr(intent, "research_request_id", "") or new_id("req"))
        )

        root_q = getattr(intent, "root_question", "")
        if not root_q and request and request.questions:
            root_q = request.questions[0]
        if not root_q:
            root_q = intent.objective

        decomposition = ResearchDecomposition(
            decomposition_id=decomp_id,
            research_request_id=req_id,
            research_intent_id=getattr(intent, "intent_id", None),
            objective=intent.objective,
            root_question=root_q,
            decomposition_confidence=1.0,
            coverage_requirements=list(getattr(intent, "research_dimensions", [])),
        )

        # Deterministic temporary ID mapping
        id_map: dict[str, str] = {}
        for idx, raw_sq in enumerate(proposal.sub_questions):
            persistent_id = new_id("subq")
            tid = raw_sq.temp_id.strip() if raw_sq.temp_id else f"sq-{idx+1}"
            id_map[tid] = persistent_id

        # Instantiate ResearchSubQuestions
        for idx, raw_sq in enumerate(proposal.sub_questions):
            tid = raw_sq.temp_id.strip() if raw_sq.temp_id else f"sq-{idx+1}"
            persistent_id = id_map.get(tid, new_id("subq"))

            # Enum coercion
            sq_type_val = raw_sq.sub_question_type.upper() if raw_sq.sub_question_type else "FACT_FINDING"
            try:
                sq_type = SubQuestionType(sq_type_val)
            except ValueError:
                sq_type = SubQuestionType.FACT_FINDING
                refinements.append(f"defaulted_sq_type_for_{persistent_id}")

            prio_val = raw_sq.priority.upper() if raw_sq.priority else "MEDIUM"
            try:
                priority = SubQuestionPriority(prio_val)
            except ValueError:
                priority = SubQuestionPriority.MEDIUM
                refinements.append(f"defaulted_priority_for_{persistent_id}")

            # Remap dependencies
            mapped_deps: list[str] = []
            for dep_ref in raw_sq.dependencies:
                dep_ref_clean = str(dep_ref).strip()
                mapped_dep = id_map.get(dep_ref_clean, dep_ref_clean)
                if mapped_dep and mapped_dep not in mapped_deps:
                    mapped_deps.append(mapped_dep)

            provenance = SubQuestionProvenance(
                research_request_id=req_id,
                decomposition_id=decomp_id,
                research_intent_id=getattr(intent, "intent_id", None),
                originating_requirement=intent.objective,
            )

            sub_q = ResearchSubQuestion(
                sub_question_id=persistent_id,
                decomposition_id=decomp_id,
                parent_id=None,
                question=raw_sq.question,
                objective=raw_sq.objective,
                sub_question_type=sq_type,
                rationale=raw_sq.rationale,
                priority=priority,
                required=raw_sq.required,
                status=SubQuestionStatus.PENDING,
                dependencies=mapped_deps,
                assumptions=list(raw_sq.assumptions),
                decomposition_depth=1,
                provenance=provenance,
            )
            decomposition.sub_questions.append(sub_q)

        # Build explicit dependency objects
        seen_edges: set[tuple[str, str]] = set()
        for sq in decomposition.sub_questions:
            for prereq_id in sq.dependencies:
                edge = (prereq_id, sq.sub_question_id)
                if edge not in seen_edges:
                    seen_edges.add(edge)
                    dep = ResearchDependency(
                        dependency_id=new_id("dep"),
                        prerequisite_id=prereq_id,
                        dependent_id=sq.sub_question_id,
                        dependency_type=ResearchDependencyType.PREREQUISITE,
                    )
                    decomposition.dependencies.append(dep)

        return decomposition


# Alias for explicit pipeline terminology
DecompositionValidationPipeline = DecompositionRefiner
