from __future__ import annotations

import re
from typing import Optional
import uuid

from core.programmer.errors import InvalidProgrammerIdError

# Canonical Programmer domain ID prefixes
WORK_ORDER_ID_PREFIX = "pwo-"
EXECUTION_ID_PREFIX = "pexec-"
TRACE_ID_PREFIX = "ptrace-"
RESULT_ID_PREFIX = "pres-"
BLOCKER_ID_PREFIX = "pblk-"
WORKSPACE_ID_PREFIX = "pws-"
PROGRAMMER_EVENT_ID_PREFIX = "pevt-"
BACKEND_REQUEST_ID_PREFIX = "cbreq-"
BACKEND_EVENT_ID_PREFIX = "cbevt-"
BACKEND_RESULT_ID_PREFIX = "cbres-"
VERIFICATION_CHECK_ID_PREFIX = "vchk-"
VERIFICATION_EVIDENCE_ID_PREFIX = "vevid-"
DIFF_VERIFICATION_ID_PREFIX = "vdiff-"
ITERATION_ID_PREFIX = "piter-"

ALL_PROGRAMMER_PREFIXES = (
    WORK_ORDER_ID_PREFIX,
    EXECUTION_ID_PREFIX,
    TRACE_ID_PREFIX,
    RESULT_ID_PREFIX,
    BLOCKER_ID_PREFIX,
    WORKSPACE_ID_PREFIX,
    PROGRAMMER_EVENT_ID_PREFIX,
    BACKEND_REQUEST_ID_PREFIX,
    BACKEND_EVENT_ID_PREFIX,
    BACKEND_RESULT_ID_PREFIX,
    VERIFICATION_CHECK_ID_PREFIX,
    VERIFICATION_EVIDENCE_ID_PREFIX,
    DIFF_VERIFICATION_ID_PREFIX,
    ITERATION_ID_PREFIX,
)

# Regex patterns: prefix followed by non-empty alphanumeric / hyphen string
_ID_PATTERN = re.compile(r"^[a-zA-Z0-9_\-]+$")


def new_backend_request_id() -> str:
    """Generate unique identifier for a CodingAgentRequest."""
    return f"{BACKEND_REQUEST_ID_PREFIX}{uuid.uuid4().hex[:8]}"


def new_backend_event_id() -> str:
    """Generate unique identifier for a CodingAgentEvent."""
    return f"{BACKEND_EVENT_ID_PREFIX}{uuid.uuid4().hex[:8]}"


def new_backend_result_id() -> str:
    """Generate unique identifier for a CodingAgentResult."""
    return f"{BACKEND_RESULT_ID_PREFIX}{uuid.uuid4().hex[:8]}"


def new_work_order_id() -> str:
    """Generate unique identifier for a ProgrammerWorkOrder."""
    return f"{WORK_ORDER_ID_PREFIX}{uuid.uuid4().hex[:8]}"


def new_execution_id() -> str:
    """Generate unique identifier for a ProgrammerExecution."""
    return f"{EXECUTION_ID_PREFIX}{uuid.uuid4().hex[:8]}"


def new_trace_id() -> str:
    """Generate unique identifier for a ProgrammerTrace."""
    return f"{TRACE_ID_PREFIX}{uuid.uuid4().hex[:8]}"


def new_result_id() -> str:
    """Generate unique identifier for a ProgrammerResult."""
    return f"{RESULT_ID_PREFIX}{uuid.uuid4().hex[:8]}"


def new_blocker_id() -> str:
    """Generate unique identifier for a ProgrammerBlocker."""
    return f"{BLOCKER_ID_PREFIX}{uuid.uuid4().hex[:8]}"


def new_workspace_id() -> str:
    """Generate unique identifier for a ProgrammerWorkspace."""
    return f"{WORKSPACE_ID_PREFIX}{uuid.uuid4().hex[:8]}"


def new_programmer_event_id() -> str:
    """Generate unique identifier for a ProgrammerExecutionEvent."""
    return f"{PROGRAMMER_EVENT_ID_PREFIX}{uuid.uuid4().hex[:8]}"


def validate_programmer_event_id(event_id: str) -> None:
    """
    Validate that event_id starts with 'pevt-' and conforms to identifier constraints.
    Raises InvalidProgrammerIdError if invalid.
    """
    if not isinstance(event_id, str) or not event_id.startswith(PROGRAMMER_EVENT_ID_PREFIX) or len(event_id) <= len(PROGRAMMER_EVENT_ID_PREFIX):
        raise InvalidProgrammerIdError(
            identifier_type="event_id",
            identifier_value=str(event_id),
            expected_prefix=PROGRAMMER_EVENT_ID_PREFIX,
        )
    suffix = event_id[len(PROGRAMMER_EVENT_ID_PREFIX):]
    if not _ID_PATTERN.match(suffix):
        raise InvalidProgrammerIdError(
            identifier_type="event_id",
            identifier_value=str(event_id),
            expected_prefix=PROGRAMMER_EVENT_ID_PREFIX,
        )



def validate_blocker_id(blocker_id: str) -> None:
    """
    Validate that blocker_id starts with 'pblk-' and conforms to identifier constraints.
    Raises InvalidProgrammerIdError if invalid.
    """
    if not isinstance(blocker_id, str) or not blocker_id.startswith(BLOCKER_ID_PREFIX) or len(blocker_id) <= len(BLOCKER_ID_PREFIX):
        raise InvalidProgrammerIdError(
            identifier_type="blocker_id",
            identifier_value=str(blocker_id),
            expected_prefix=BLOCKER_ID_PREFIX,
        )
    suffix = blocker_id[len(BLOCKER_ID_PREFIX):]
    if not _ID_PATTERN.match(suffix):
        raise InvalidProgrammerIdError(
            identifier_type="blocker_id",
            identifier_value=str(blocker_id),
            expected_prefix=BLOCKER_ID_PREFIX,
        )


def validate_workspace_id(workspace_id: str) -> None:
    """
    Validate that workspace_id starts with 'pws-' and conforms to identifier constraints.
    Raises InvalidProgrammerIdError if invalid.
    """
    if not isinstance(workspace_id, str) or not workspace_id.startswith(WORKSPACE_ID_PREFIX) or len(workspace_id) <= len(WORKSPACE_ID_PREFIX):
        raise InvalidProgrammerIdError(
            identifier_type="workspace_id",
            identifier_value=str(workspace_id),
            expected_prefix=WORKSPACE_ID_PREFIX,
        )
    suffix = workspace_id[len(WORKSPACE_ID_PREFIX):]
    if not _ID_PATTERN.match(suffix):
        raise InvalidProgrammerIdError(
            identifier_type="workspace_id",
            identifier_value=str(workspace_id),
            expected_prefix=WORKSPACE_ID_PREFIX,
        )



def validate_work_order_id(work_order_id: str) -> None:
    """
    Validate that work_order_id starts with 'pwo-' and conforms to identifier constraints.
    Raises InvalidProgrammerIdError if invalid.
    """
    if not isinstance(work_order_id, str) or not work_order_id.startswith(WORK_ORDER_ID_PREFIX) or len(work_order_id) <= len(WORK_ORDER_ID_PREFIX):
        raise InvalidProgrammerIdError(
            identifier_type="work_order_id",
            identifier_value=str(work_order_id),
            expected_prefix=WORK_ORDER_ID_PREFIX,
        )
    suffix = work_order_id[len(WORK_ORDER_ID_PREFIX):]
    if not _ID_PATTERN.match(suffix):
        raise InvalidProgrammerIdError(
            identifier_type="work_order_id",
            identifier_value=str(work_order_id),
            expected_prefix=WORK_ORDER_ID_PREFIX,
        )


def validate_execution_id(execution_id: str) -> None:
    """
    Validate that execution_id starts with 'pexec-' and conforms to identifier constraints.
    Raises InvalidProgrammerIdError if invalid.
    """
    if not isinstance(execution_id, str) or not execution_id.startswith(EXECUTION_ID_PREFIX) or len(execution_id) <= len(EXECUTION_ID_PREFIX):
        raise InvalidProgrammerIdError(
            identifier_type="execution_id",
            identifier_value=str(execution_id),
            expected_prefix=EXECUTION_ID_PREFIX,
        )
    suffix = execution_id[len(EXECUTION_ID_PREFIX):]
    if not _ID_PATTERN.match(suffix):
        raise InvalidProgrammerIdError(
            identifier_type="execution_id",
            identifier_value=str(execution_id),
            expected_prefix=EXECUTION_ID_PREFIX,
        )


def validate_trace_id(trace_id: str) -> None:
    """
    Validate that trace_id starts with 'ptrace-' and conforms to identifier constraints.
    Raises InvalidProgrammerIdError if invalid.
    """
    if not isinstance(trace_id, str) or not trace_id.startswith(TRACE_ID_PREFIX) or len(trace_id) <= len(TRACE_ID_PREFIX):
        raise InvalidProgrammerIdError(
            identifier_type="trace_id",
            identifier_value=str(trace_id),
            expected_prefix=TRACE_ID_PREFIX,
        )
    suffix = trace_id[len(TRACE_ID_PREFIX):]
    if not _ID_PATTERN.match(suffix):
        raise InvalidProgrammerIdError(
            identifier_type="trace_id",
            identifier_value=str(trace_id),
            expected_prefix=TRACE_ID_PREFIX,
        )


def validate_result_id(result_id: str) -> None:
    """
    Validate that result_id starts with 'pres-' and conforms to identifier constraints.
    Raises InvalidProgrammerIdError if invalid.
    """
    if not isinstance(result_id, str) or not result_id.startswith(RESULT_ID_PREFIX) or len(result_id) <= len(RESULT_ID_PREFIX):
        raise InvalidProgrammerIdError(
            identifier_type="result_id",
            identifier_value=str(result_id),
            expected_prefix=RESULT_ID_PREFIX,
        )
    suffix = result_id[len(RESULT_ID_PREFIX):]
    if not _ID_PATTERN.match(suffix):
        raise InvalidProgrammerIdError(
            identifier_type="result_id",
            identifier_value=str(result_id),
            expected_prefix=RESULT_ID_PREFIX,
        )


def validate_backend_request_id(request_id: str) -> None:
    """
    Validate that request_id starts with 'cbreq-' and conforms to identifier constraints.
    Raises InvalidProgrammerIdError if invalid.
    """
    if not isinstance(request_id, str) or not request_id.startswith(BACKEND_REQUEST_ID_PREFIX) or len(request_id) <= len(BACKEND_REQUEST_ID_PREFIX):
        raise InvalidProgrammerIdError(
            identifier_type="request_id",
            identifier_value=str(request_id),
            expected_prefix=BACKEND_REQUEST_ID_PREFIX,
        )
    suffix = request_id[len(BACKEND_REQUEST_ID_PREFIX):]
    if not _ID_PATTERN.match(suffix):
        raise InvalidProgrammerIdError(
            identifier_type="request_id",
            identifier_value=str(request_id),
            expected_prefix=BACKEND_REQUEST_ID_PREFIX,
        )


def new_verification_check_id() -> str:
    """Generate unique identifier for a VerificationCheck."""
    return f"{VERIFICATION_CHECK_ID_PREFIX}{uuid.uuid4().hex[:8]}"


def new_verification_evidence_id() -> str:
    """Generate unique identifier for a VerificationEvidence."""
    return f"{VERIFICATION_EVIDENCE_ID_PREFIX}{uuid.uuid4().hex[:8]}"


def validate_verification_check_id(check_id: str) -> None:
    """
    Validate that check_id starts with 'vchk-' and conforms to identifier constraints.
    Raises InvalidProgrammerIdError if invalid.
    """
    if not isinstance(check_id, str) or not check_id.startswith(VERIFICATION_CHECK_ID_PREFIX) or len(check_id) <= len(VERIFICATION_CHECK_ID_PREFIX):
        raise InvalidProgrammerIdError(
            identifier_type="check_id",
            identifier_value=str(check_id),
            expected_prefix=VERIFICATION_CHECK_ID_PREFIX,
        )
    suffix = check_id[len(VERIFICATION_CHECK_ID_PREFIX):]
    if not _ID_PATTERN.match(suffix):
        raise InvalidProgrammerIdError(
            identifier_type="check_id",
            identifier_value=str(check_id),
            expected_prefix=VERIFICATION_CHECK_ID_PREFIX,
        )


def validate_verification_evidence_id(evidence_id: str) -> None:
    """
    Validate that evidence_id starts with 'vevid-' and conforms to identifier constraints.
    Raises InvalidProgrammerIdError if invalid.
    """
    if not isinstance(evidence_id, str) or not evidence_id.startswith(VERIFICATION_EVIDENCE_ID_PREFIX) or len(evidence_id) <= len(VERIFICATION_EVIDENCE_ID_PREFIX):
        raise InvalidProgrammerIdError(
            identifier_type="evidence_id",
            identifier_value=str(evidence_id),
            expected_prefix=VERIFICATION_EVIDENCE_ID_PREFIX,
        )
    suffix = evidence_id[len(VERIFICATION_EVIDENCE_ID_PREFIX):]
    if not _ID_PATTERN.match(suffix):
        raise InvalidProgrammerIdError(
            identifier_type="evidence_id",
            identifier_value=str(evidence_id),
            expected_prefix=VERIFICATION_EVIDENCE_ID_PREFIX,
        )


def new_diff_verification_id() -> str:
    """Generate unique identifier for a DiffVerification."""
    return f"{DIFF_VERIFICATION_ID_PREFIX}{uuid.uuid4().hex[:8]}"


def validate_diff_verification_id(verification_id: str) -> None:
    """
    Validate that verification_id starts with 'vdiff-' and conforms to identifier constraints.
    Raises InvalidProgrammerIdError if invalid.
    """
    if not isinstance(verification_id, str) or not verification_id.startswith(DIFF_VERIFICATION_ID_PREFIX) or len(verification_id) <= len(DIFF_VERIFICATION_ID_PREFIX):
        raise InvalidProgrammerIdError(
            identifier_type="verification_id",
            identifier_value=str(verification_id),
            expected_prefix=DIFF_VERIFICATION_ID_PREFIX,
        )
    suffix = verification_id[len(DIFF_VERIFICATION_ID_PREFIX):]
    if not _ID_PATTERN.match(suffix):
        raise InvalidProgrammerIdError(
            identifier_type="verification_id",
            identifier_value=str(verification_id),
            expected_prefix=DIFF_VERIFICATION_ID_PREFIX,
        )


def new_iteration_id() -> str:
    """Generate unique identifier for a CorrectionIterationRecord."""
    return f"{ITERATION_ID_PREFIX}{uuid.uuid4().hex[:8]}"


def validate_iteration_id(iteration_id: str) -> None:
    """
    Validate that iteration_id starts with 'piter-' and conforms to identifier constraints.
    Raises InvalidProgrammerIdError if invalid.
    """
    if not isinstance(iteration_id, str) or not iteration_id.startswith(ITERATION_ID_PREFIX) or len(iteration_id) <= len(ITERATION_ID_PREFIX):
        raise InvalidProgrammerIdError(
            identifier_type="iteration_id",
            identifier_value=str(iteration_id),
            expected_prefix=ITERATION_ID_PREFIX,
        )
    suffix = iteration_id[len(ITERATION_ID_PREFIX):]
    if not _ID_PATTERN.match(suffix):
        raise InvalidProgrammerIdError(
            identifier_type="iteration_id",
            identifier_value=str(iteration_id),
            expected_prefix=ITERATION_ID_PREFIX,
        )


def is_programmer_id(identifier: str) -> bool:
    """Check whether a given identifier belongs to the Programmer subsystem domain."""
    if not isinstance(identifier, str):
        return False
    return any(identifier.startswith(prefix) for prefix in ALL_PROGRAMMER_PREFIXES)

