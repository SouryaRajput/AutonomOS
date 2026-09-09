from __future__ import annotations

from typing import Any, Optional

from core.errors import AutonomOSError


class TesterError(AutonomOSError):
    """Base exception for all Tester subsystem errors."""
    __test__ = False

    def __init__(
        self,
        message: str,
        code: str = "TESTER_ERROR",
        details: Optional[dict[str, Any]] = None,
    ):
        super().__init__(message=message, code=code, details=details)


class InvalidTesterIdError(TesterError):
    """Raised when an identifier does not conform to the Tester subsystem prefix or format."""
    __test__ = False

    def __init__(self, identifier_type: str, identifier_value: str, expected_prefix: str):
        super().__init__(
            message=f"Invalid {identifier_type} identifier '{identifier_value}'. Expected prefix '{expected_prefix}'.",
            code="INVALID_TESTER_ID",
            details={
                "identifier_type": identifier_type,
                "identifier_value": identifier_value,
                "expected_prefix": expected_prefix,
            },
        )
        self.identifier_type = identifier_type
        self.identifier_value = identifier_value
        self.expected_prefix = expected_prefix


class TesterLineageError(TesterError):
    """Raised when an entity's parent lineage or correlation reference is missing, broken, or tampered with."""
    __test__ = False

    def __init__(self, message: str, details: Optional[dict[str, Any]] = None):
        super().__init__(
            message=message,
            code="TESTER_LINEAGE_ERROR",
            details=details,
        )


class InvalidTesterTransitionError(TesterError):
    """Raised when an illegal lifecycle transition is attempted on a Tester execution or work order."""
    __test__ = False

    def __init__(
        self,
        entity_id: str,
        current_status: str,
        target_status: str,
        reason: str = "",
    ):
        msg = f"Cannot transition '{entity_id}' from '{current_status}' to '{target_status}'"
        if reason:
            msg += f": {reason}"
        super().__init__(
            message=msg,
            code="INVALID_TESTER_TRANSITION",
            details={
                "entity_id": entity_id,
                "current_status": current_status,
                "target_status": target_status,
                "reason": reason,
            },
        )
        self.entity_id = entity_id
        self.current_status = current_status
        self.target_status = target_status


class TesterValidationError(TesterError):
    """Raised when a Tester contract fails schema, boundary, or sanity validation."""
    __test__ = False

    def __init__(
        self,
        message: str,
        field_name: Optional[str] = None,
        code: str = "TESTER_VALIDATION_ERROR",
        details: Optional[dict[str, Any]] = None,
    ):
        d = dict(details or {})
        if field_name:
            d["field_name"] = field_name
        super().__init__(message=message, code=code, details=d)
        self.field_name = field_name


class TesterBoundaryViolationError(TesterError):
    """
    Raised when a requested action, scope expansion, or modification violates
    the fundamental Tester responsibility boundary (Tester MUST NOT).
    """
    __test__ = False

    def __init__(
        self,
        action: str,
        reason: str,
        details: Optional[dict[str, Any]] = None,
    ):
        d = dict(details or {})
        d.update({"action": action, "reason": reason})
        super().__init__(
            message=f"Tester boundary violation: cannot perform '{action}'. {reason}",
            code="TESTER_BOUNDARY_VIOLATION",
            details=d,
        )
        self.action = action
        self.reason = reason


class TesterBlockerError(TesterError):
    """Raised when a material blocker prevents Tester execution from proceeding, requiring Manager escalation."""
    __test__ = False

    def __init__(self, work_order_id: str, blocker: str, details: Optional[dict[str, Any]] = None):
        d = {"work_order_id": work_order_id, "blocker": blocker}
        if details:
            d.update(details)
        super().__init__(
            message=f"Execution blocked for work order '{work_order_id}': {blocker}",
            code="TESTER_MATERIAL_BLOCKER",
            details=d,
        )
        self.work_order_id = work_order_id
        self.blocker = blocker
