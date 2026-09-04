from __future__ import annotations

from enum import Enum


class ResearchLifecycleState(str, Enum):
    """
    Deterministic lifecycle states of the AutonomOS Research Subsystem.
    Governs the progression of a research request through understanding, planning,
    crawler allocation, execution, evidence collection, evaluation, verification,
    synthesis, and delivery.
    """
    RESEARCH_REQUESTED = "RESEARCH_REQUESTED"
    UNDERSTANDING = "UNDERSTANDING"
    PLANNING = "PLANNING"
    CRAWLER_ALLOCATION = "CRAWLER_ALLOCATION"
    CRAWLERS_RUNNING = "CRAWLERS_RUNNING"
    EVIDENCE_COLLECTION = "EVIDENCE_COLLECTION"
    EVIDENCE_EVALUATION = "EVIDENCE_EVALUATION"
    COVERAGE_CHECK = "COVERAGE_CHECK"
    CONTRADICTION_CHECK = "CONTRADICTION_CHECK"
    SYNTHESIS = "SYNTHESIS"
    RESEARCH_VERIFIED = "RESEARCH_VERIFIED"
    RESULT_DELIVERED = "RESULT_DELIVERED"
    COMPLETE = "COMPLETE"

    # Exceptional & Recovery States
    RETRYING_CRAWLERS = "RETRYING_CRAWLERS"
    INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"
    STAGNATED = "STAGNATED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class CrawlerCapability(str, Enum):
    """
    Taxonomy of discrete capabilities that crawlers can declare and execute.
    Decoupled from specific website brands or scraper libraries.
    """
    WEB_SEARCH = "WEB_SEARCH"
    WEB_FETCH = "WEB_FETCH"
    DOCUMENT_SCRAPING = "DOCUMENT_SCRAPING"
    DOCUMENTATION_CRAWL = "DOCUMENTATION_CRAWL"
    API_QUERY = "API_QUERY"
    REPOSITORY_INSPECTION = "REPOSITORY_INSPECTION"
    PROJECT_MEMORY_LOOKUP = "PROJECT_MEMORY_LOOKUP"
    STRUCTURED_DATA_EXTRACTION = "STRUCTURED_DATA_EXTRACTION"
    CUSTOM = "CUSTOM"


class CrawlerStatus(str, Enum):
    """
    Deterministic operational lifecycle status of an individual crawler worker instance.
    CREATED -> QUEUED -> RUNNING -> COMPLETED / FAILED / CANCELLED -> TERMINATED.
    """
    CREATED = "CREATED"
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"
    TERMINATED = "TERMINATED"


class CrawlerHealthStatus(str, Enum):
    """Health and responsiveness classification of a crawler."""
    HEALTHY = "HEALTHY"
    DEGRADED = "DEGRADED"
    UNRESPONSIVE = "UNRESPONSIVE"
    DEAD = "DEAD"


class CrawlerTaskStatus(str, Enum):
    """Execution status of an individual crawler task."""
    PENDING = "PENDING"
    ASSIGNED = "ASSIGNED"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"
    TIMED_OUT = "TIMED_OUT"


class CrawlerReportStatus(str, Enum):
    """Outcome status of a crawler execution report."""
    SUCCESS = "SUCCESS"
    PARTIAL = "PARTIAL"
    FAILED = "FAILED"
    TIMED_OUT = "TIMED_OUT"
    EMPTY = "EMPTY"


class ResearchMode(str, Enum):
    """Operational intensity and depth bounds for research execution."""
    QUICK = "QUICK"         # Low search depth, focused extraction, fast bounds
    STANDARD = "STANDARD"   # Balanced exploration, multi-source corroboration (default)
    DEEP = "DEEP"           # Exhaustive multi-crawler queries and deep cross-checking


class FactClassification(str, Enum):
    """
    Epistemic classification of research statements and evidence.
    Enforces strict distinction between directly observed facts, external claims,
    deductive inferences, recommendations, assumptions, and unknowns.
    """
    FACT = "FACT"                     # Directly observed ground truth from system or verified source
    SOURCE_CLAIM = "SOURCE_CLAIM"     # External statement attributed to a specific source
    INFERENCE = "INFERENCE"           # Deductive/inductive logical conclusion derived from evidence
    RECOMMENDATION = "RECOMMENDATION" # Actionable proposal explicitly separated from facts
    ASSUMPTION = "ASSUMPTION"         # Working premise taken as baseline without complete evidence
    UNKNOWN = "UNKNOWN"               # Explicit knowledge gap or unverified question


class ResearchConfidence(str, Enum):
    """
    Structured confidence ratings for research findings and evidence.
    Avoids arbitrary numeric probabilities in favor of evidence-backed certainty tiers.
    """
    WELL_SUPPORTED = "WELL_SUPPORTED"       # Multiple corroborating authoritative sources
    SUPPORTED = "SUPPORTED"                 # Credible primary or secondary source with no conflicts
    LIMITED_EVIDENCE = "LIMITED_EVIDENCE"   # Single low-authority source or incomplete data
    CONFLICTING = "CONFLICTING"             # Multiple credible sources contradict one another
    UNVERIFIED = "UNVERIFIED"               # Plausible hypothesis or claim lacking concrete evidence


class ResearchQuestionStatus(str, Enum):
    """Resolution status of an individual sub-question."""
    UNANSWERED = "UNANSWERED"
    IN_PROGRESS = "IN_PROGRESS"
    ANSWERED = "ANSWERED"
    PARTIALLY_ANSWERED = "PARTIALLY_ANSWERED"
    UNKNOWN = "UNKNOWN"
    CONFLICTING = "CONFLICTING"


class EvidenceSufficiency(str, Enum):
    """Outcome of coverage checks determining whether evidence is adequate."""
    SUFFICIENT = "SUFFICIENT"
    PARTIAL = "PARTIAL"
    INSUFFICIENT = "INSUFFICIENT"
    CONFLICTING = "CONFLICTING"


class ResearchResultStatus(str, Enum):
    """Final delivery status of a research result."""
    VERIFIED = "VERIFIED"                       # All questions resolved with verified evidence
    PARTIAL = "PARTIAL"                         # Some questions resolved, others marked unknown
    INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE" # Evidence pool too sparse to make claims
    CONFLICTING = "CONFLICTING"                 # Unresolvable contradictions found and preserved
    FAILED = "FAILED"                           # Execution failed due to runtime errors


class SourceType(str, Enum):
    """Classification of sources reflecting structural authority."""
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
