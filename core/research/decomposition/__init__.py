from __future__ import annotations

from core.research.decomposition.constraints import (
    DecompositionConstraintError,
    DecompositionConstraintValidator,
)
from core.research.decomposition.generator import (
    DecompositionGenerationResult,
    DecompositionGenerationStatus,
    DecompositionGenerator,
    DecompositionProposal,
    RawSubQuestionProposal,
    SubQuestionGenerator,
)
from core.research.decomposition.model import (
    AcceptanceCriteria,
    ExpectedEvidence,
    ResearchDecomposition,
    ResearchDependency,
    ResearchScope,
    ResearchSubQuestion,
    SubQuestionProvenance,
    new_id,
    utc_now,
)
from core.research.decomposition.normalizer import (
    DecompositionNormalizer,
    canonicalize_text,
)
from core.research.decomposition.ordering import (
    DecompositionOrderPlan,
    DecompositionOrderPlanner,
    ExecutionStage,
    QuestionReadiness,
    SubQuestionOrderPlanner,
    get_priority_weight,
)
from core.research.decomposition.policy import (
    DEFAULT_MAX_DEPENDENCIES_PER_QUESTION,
    DEFAULT_MAX_OBJECTIVE_LEN,
    DEFAULT_MAX_PAYLOAD_BYTES,
    DEFAULT_MAX_QUESTION_LEN,
    DEFAULT_MAX_RATIONALE_LEN,
    DEFAULT_MAX_TOTAL_DEPENDENCIES,
    DEFAULT_MIN_OBJECTIVE_LEN,
    DEFAULT_MIN_QUESTION_LEN,
    DEFAULT_MIN_SUB_QUESTIONS,
    DEFAULT_TARGET_MAX_SUB_QUESTIONS,
    DEFAULT_TARGET_MIN_SUB_QUESTIONS,
    DEFAULT_TARGET_SUB_QUESTIONS,
    MAX_SUB_QUESTIONS,
    DecompositionPolicy,
)
from core.research.decomposition.types import (
    ResearchDependencyType,
    ResearchPriority,
    SubQuestionPriority,
    SubQuestionStatus,
    SubQuestionType,
)
from core.research.decomposition.refinement import (
    DecompositionRefiner,
    DecompositionValidationIssue,
    DecompositionValidationIssueCode,
    DecompositionValidationPipeline,
    DecompositionValidationResult,
    DecompositionValidationStatus,
)
from core.research.decomposition.validator import (
    DecompositionValidationError,
    DecompositionValidator,
)

__all__ = [
    # Types & Enums
    "SubQuestionType",
    "SubQuestionPriority",
    "ResearchPriority",
    "SubQuestionStatus",
    "ResearchDependencyType",
    # Domain Models
    "ResearchScope",
    "ExpectedEvidence",
    "AcceptanceCriteria",
    "ResearchDependency",
    "SubQuestionProvenance",
    "ResearchSubQuestion",
    "ResearchDecomposition",
    "new_id",
    "utc_now",
    # Policy & Bounds
    "DecompositionPolicy",
    "DEFAULT_MIN_SUB_QUESTIONS",
    "DEFAULT_TARGET_MIN_SUB_QUESTIONS",
    "DEFAULT_TARGET_MAX_SUB_QUESTIONS",
    "DEFAULT_TARGET_SUB_QUESTIONS",
    "MAX_SUB_QUESTIONS",
    "DEFAULT_MIN_QUESTION_LEN",
    "DEFAULT_MAX_QUESTION_LEN",
    "DEFAULT_MIN_OBJECTIVE_LEN",
    "DEFAULT_MAX_OBJECTIVE_LEN",
    "DEFAULT_MAX_RATIONALE_LEN",
    "DEFAULT_MAX_DEPENDENCIES_PER_QUESTION",
    "DEFAULT_MAX_TOTAL_DEPENDENCIES",
    "DEFAULT_MAX_PAYLOAD_BYTES",
    # Normalizer
    "DecompositionNormalizer",
    "canonicalize_text",
    # Validators
    "DecompositionValidationError",
    "DecompositionValidator",
    "DecompositionConstraintError",
    "DecompositionConstraintValidator",
    # Generator
    "SubQuestionGenerator",
    "DecompositionGenerator",
    "DecompositionGenerationResult",
    "DecompositionGenerationStatus",
    "DecompositionProposal",
    "RawSubQuestionProposal",
    # Ordering & Readiness
    "SubQuestionOrderPlanner",
    "DecompositionOrderPlanner",
    "DecompositionOrderPlan",
    "ExecutionStage",
    "QuestionReadiness",
    "get_priority_weight",
    # Validation & Refinement
    "DecompositionValidationStatus",
    "DecompositionValidationIssueCode",
    "DecompositionValidationIssue",
    "DecompositionValidationResult",
    "DecompositionRefiner",
    "DecompositionValidationPipeline",
]
