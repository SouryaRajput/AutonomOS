from __future__ import annotations

from typing import Optional


class ResearchError(Exception):
    """Base exception for all research subsystem errors."""
    def __init__(self, message: str, details: Optional[dict] = None):
        super().__init__(message)
        self.message = message
        self.details = details or {}


class InvalidStateTransitionError(ResearchError):
    """Raised when an invalid research lifecycle state transition is attempted."""
    def __init__(self, current_state: str, target_state: str, reason: str = ""):
        msg = f"Cannot transition research state from '{current_state}' to '{target_state}'"
        if reason:
            msg += f": {reason}"
        super().__init__(msg, {"current_state": current_state, "target_state": target_state, "reason": reason})
        self.current_state = current_state
        self.target_state = target_state


class InsufficientEvidenceError(ResearchError):
    """Raised when coverage check determines evidence is insufficient to answer required questions."""
    def __init__(self, question_ids: list[str], reason: str = ""):
        msg = f"Insufficient evidence for questions: {', '.join(question_ids)}"
        if reason:
            msg += f" ({reason})"
        super().__init__(msg, {"question_ids": question_ids, "reason": reason})
        self.question_ids = question_ids


class CrawlerExecutionError(ResearchError):
    """Raised when a crawler task fails or encounters an unhandled runtime exception."""
    def __init__(self, crawler_id: str, task_id: str, error_message: str):
        msg = f"Crawler '{crawler_id}' failed executing task '{task_id}': {error_message}"
        super().__init__(msg, {"crawler_id": crawler_id, "task_id": task_id, "error": error_message})
        self.crawler_id = crawler_id
        self.task_id = task_id


class CrawlerNotFoundError(ResearchError):
    """Raised when a requested crawler ID or capability matching crawler is not found."""
    def __init__(self, identifier: str):
        super().__init__(f"Crawler matching '{identifier}' not found.", {"identifier": identifier})


class CrawlerTaskNotFoundError(ResearchError):
    """Raised when a referenced crawler task cannot be found."""
    def __init__(self, task_id: str):
        super().__init__(f"Crawler task with ID '{task_id}' not found.", {"task_id": task_id})


class ProvenanceError(ResearchError):
    """Raised when evidence or report provenance verification fails."""
    def __init__(self, item_id: str, expected: str, actual: str):
        msg = f"Provenance mismatch for item '{item_id}': expected '{expected}', found '{actual}'"
        super().__init__(msg, {"item_id": item_id, "expected": expected, "actual": actual})
