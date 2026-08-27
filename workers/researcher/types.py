from __future__ import annotations

from enum import Enum


class SourceType(str, Enum):
    """Classification of research sources reflecting their structural authority."""
    OFFICIAL_DOCUMENTATION = "OFFICIAL_DOCUMENTATION"
    OFFICIAL_ANNOUNCEMENT = "OFFICIAL_ANNOUNCEMENT"
    PRIMARY_SOURCE = "PRIMARY_SOURCE"
    ACADEMIC = "ACADEMIC"
    NEWS = "NEWS"
    COMMUNITY = "COMMUNITY"
    BLOG = "BLOG"
    FORUM = "FORUM"
    REPOSITORY = "REPOSITORY"
    PROJECT_MEMORY = "PROJECT_MEMORY"
    OTHER = "OTHER"


class FactClassification(str, Enum):
    """
    Epistemic classification of research statements.
    Enforces strict distinction between observed facts, attributed claims,
    logical inferences, assumptions, and actionable recommendations.
    """
    FACT = "FACT"                     # Directly observed or verified ground truth from project/system
    SOURCE_CLAIM = "SOURCE_CLAIM"     # External statement attributed to a specific source
    INFERENCE = "INFERENCE"           # Deductive or inductive conclusion drawn from evidence
    RECOMMENDATION = "RECOMMENDATION" # Actionable advice derived from findings + project goals
    ASSUMPTION = "ASSUMPTION"         # Working premise taken as baseline without complete evidence
    UNKNOWN = "UNKNOWN"               # Explicit knowledge gap or unverified question


class ResearchConfidence(str, Enum):
    """
    Structured confidence rating for research findings.
    Avoids arbitrary numeric probabilities in favor of evidence-grounded certainty levels.
    """
    WELL_SUPPORTED = "WELL_SUPPORTED"       # Multiple corroborating authoritative sources
    SUPPORTED = "SUPPORTED"                 # Credible primary or secondary source with no conflicts
    LIMITED_EVIDENCE = "LIMITED_EVIDENCE"   # Single low-authority source or incomplete data
    CONFLICTING = "CONFLICTING"             # Multiple credible sources contradict one another
    UNVERIFIED = "UNVERIFIED"               # Plausible hypothesis or claim lacking concrete evidence


class ResearchMode(str, Enum):
    """Operational mode controlling search depth, budget bounds, and cross-checking intensity."""
    QUICK = "QUICK"         # Low search depth, fast extraction, strictly focused
    STANDARD = "STANDARD"   # Balanced search and cross-checking (default)
    DEEP = "DEEP"           # Comprehensive multi-query exploration and thorough corroboration


class ResearchQuestionStatus(str, Enum):
    """Status of an individual sub-question being tracked by the Researcher."""
    UNANSWERED = "UNANSWERED"
    IN_PROGRESS = "IN_PROGRESS"
    ANSWERED = "ANSWERED"
    PARTIALLY_ANSWERED = "PARTIALLY_ANSWERED"
    UNKNOWN = "UNKNOWN"
    CONFLICTING = "CONFLICTING"
