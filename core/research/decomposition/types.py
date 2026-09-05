from __future__ import annotations

from enum import Enum


class SubQuestionType(str, Enum):
    """
    Bounded classification taxonomy for internal research sub-questions.
    Reflects the distinct analytical or investigative intent of each unit of research.
    """
    FACT_FINDING = "FACT_FINDING"
    COMPARISON = "COMPARISON"
    EVALUATION = "EVALUATION"
    VERIFICATION = "VERIFICATION"
    DIAGNOSTIC = "DIAGNOSTIC"
    ARCHITECTURAL = "ARCHITECTURAL"
    IMPLEMENTATION = "IMPLEMENTATION"
    CONSTRAINT_ANALYSIS = "CONSTRAINT_ANALYSIS"
    RISK_ANALYSIS = "RISK_ANALYSIS"
    COST_ANALYSIS = "COST_ANALYSIS"
    PERFORMANCE_ANALYSIS = "PERFORMANCE_ANALYSIS"
    SECURITY_ANALYSIS = "SECURITY_ANALYSIS"
    COMPATIBILITY_ANALYSIS = "COMPATIBILITY_ANALYSIS"
    HISTORICAL = "HISTORICAL"
    SYNTHESIS = "SYNTHESIS"


class SubQuestionPriority(str, Enum):
    """
    Deterministic priority tiers describing the analytical importance of a sub-question
    to the parent research objective.
    NOTE: Priority indicates investigative criticality, NOT crawler urgency.
    """
    CRITICAL = "CRITICAL"
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"


# Alias for explicit domain clarity
ResearchPriority = SubQuestionPriority


class SubQuestionStatus(str, Enum):
    """
    Deterministic planning lifecycle status of an individual research sub-question.
    PENDING -> READY (dependencies satisfied) -> IN_PROGRESS -> COMPLETE / FAILED / SKIPPED.
    Or BLOCKED when pending prerequisites.
    """
    PENDING = "PENDING"
    READY = "READY"
    IN_PROGRESS = "IN_PROGRESS"
    BLOCKED = "BLOCKED"
    COMPLETE = "COMPLETE"
    FAILED = "FAILED"
    SKIPPED = "SKIPPED"


class ResearchDependencyType(str, Enum):
    """
    Classification of directed relationships between research sub-questions.
    prerequisite_id -> dependent_id
    """
    PREREQUISITE = "PREREQUISITE"  # Prerequisite must be resolved before dependent begins
    INFORMS = "INFORMS"            # Prerequisite findings provide context or dimensions
    BLOCKS = "BLOCKS"              # Dependent is strictly blocked until prerequisite completes
    EXTENDS = "EXTENDS"            # Dependent builds directly on findings of prerequisite
