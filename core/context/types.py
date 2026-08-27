from enum import Enum


class ContextSourceType(str, Enum):
    """Origin sources for candidate context items."""
    TASK_OBJECTIVE = "TASK_OBJECTIVE"          # Authoritative task title, objective, constraints
    EXPLICIT_REFERENCE = "EXPLICIT_REFERENCE"  # Explicitly referenced files, ADRs, or docs
    PROJECT_MAP = "PROJECT_MAP"                # High-level component and navigation map
    ARCHITECTURE = "ARCHITECTURE"              # Stable architectural decisions and invariants
    CURRENT_STATE = "CURRENT_STATE"            # Active stage, milestones, active work
    DECISION = "DECISION"                      # Architectural Decision Records (ADRs)
    TASK_MEMORY = "TASK_MEMORY"                # Prior task summaries and findings
    REPORT = "REPORT"                          # Prior worker execution & research reports
    ISSUE = "ISSUE"                            # Known problems and limitations
    REPOSITORY_FILE = "REPOSITORY_FILE"        # Source code files on disk
    RECENT_EVENTS = "RECENT_EVENTS"            # Authoritative recent audit trail events
    ARTIFACT_METADATA = "ARTIFACT_METADATA"    # Metadata of produced artifacts


class ContextPriority(str, Enum):
    """Priority levels for context items."""
    MANDATORY = "MANDATORY"  # Protected from eviction; required for task understanding
    HIGH = "HIGH"            # Explicitly referenced or highly relevant items
    MEDIUM = "MEDIUM"        # Moderately relevant contextual background
    LOW = "LOW"              # Weakly relevant background
    OPTIONAL = "OPTIONAL"    # First to be dropped under tight context budget


class ContextWarningType(str, Enum):
    """Structured warning codes emitted during context assembly."""
    BUDGET_EXCEEDED = "BUDGET_EXCEEDED"                      # Items exceeded budget limit
    REQUIRED_CONTEXT_MISSING = "REQUIRED_CONTEXT_MISSING"    # A required item could not be found
    STALE_REFERENCE = "STALE_REFERENCE"                      # Referenced item has not been updated
    MISSING_FILE = "MISSING_FILE"                            # Referenced repository file not found on disk
    CONTRADICTORY_MEMORY = "CONTRADICTORY_MEMORY"            # Markdown memory contradicts authoritative state
    TRUNCATED_ITEM = "TRUNCATED_ITEM"                        # An item was safely truncated to fit budget
    DUPLICATE_REMOVED = "DUPLICATE_REMOVED"                  # Duplicate content was detected and deduplicated
