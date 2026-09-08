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
FAILURE_ID_PREFIX = "pfail-"
WATCHDOG_ID_PREFIX = "pwdog-"
RECOVERY_DECISION_ID_PREFIX = "prec-"
RETRY_ATTEMPT_ID_PREFIX = "pretry-"
ESCALATION_ID_PREFIX = "pesc-"
GIT_REPOSITORY_ID_PREFIX = "grepo-"
GIT_REVISION_ID_PREFIX = "grev-"
GIT_WORKTREE_ID_PREFIX = "gwt-"
CHANGESET_ID_PREFIX = "pcs-"
REPO_STATE_VERIFICATION_ID_PREFIX = "vrepo-"
DELIVERY_ID_PREFIX = "pdel-"
UNDERSTANDING_ID_PREFIX = "pund-"
IMPACT_ANALYSIS_ID_PREFIX = "pimp-"
PLAN_ID_PREFIX = "ppln-"
PLAN_STEP_ID_PREFIX = "pstep-"
ESCALATION_CANDIDATE_ID_PREFIX = "pescand-"
ENGINEERING_RISK_ID_PREFIX = "prisk-"
RISK_ASSESSMENT_ID_PREFIX = "prasm-"
PLAN_VALIDATION_RESULT_ID_PREFIX = "pvr-"
SUPERVISION_RECORD_ID_PREFIX = "psup-"
PLAN_DEVIATION_ID_PREFIX = "pdev-"

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
    FAILURE_ID_PREFIX,
    WATCHDOG_ID_PREFIX,
    RECOVERY_DECISION_ID_PREFIX,
    RETRY_ATTEMPT_ID_PREFIX,
    ESCALATION_ID_PREFIX,
    GIT_REPOSITORY_ID_PREFIX,
    GIT_REVISION_ID_PREFIX,
    GIT_WORKTREE_ID_PREFIX,
    CHANGESET_ID_PREFIX,
    REPO_STATE_VERIFICATION_ID_PREFIX,
    DELIVERY_ID_PREFIX,
    UNDERSTANDING_ID_PREFIX,
    IMPACT_ANALYSIS_ID_PREFIX,
    PLAN_ID_PREFIX,
    PLAN_STEP_ID_PREFIX,
    ESCALATION_CANDIDATE_ID_PREFIX,
    ENGINEERING_RISK_ID_PREFIX,
    RISK_ASSESSMENT_ID_PREFIX,
    PLAN_VALIDATION_RESULT_ID_PREFIX,
    SUPERVISION_RECORD_ID_PREFIX,
    PLAN_DEVIATION_ID_PREFIX,
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


def new_failure_id() -> str:
    """Generate unique identifier for a ProgrammerFailure."""
    return f"{FAILURE_ID_PREFIX}{uuid.uuid4().hex[:8]}"


def validate_failure_id(failure_id: str) -> None:
    """
    Validate that failure_id starts with 'pfail-' and conforms to identifier constraints.
    Raises InvalidProgrammerIdError if invalid.
    """
    if not isinstance(failure_id, str) or not failure_id.startswith(FAILURE_ID_PREFIX) or len(failure_id) <= len(FAILURE_ID_PREFIX):
        raise InvalidProgrammerIdError(
            identifier_type="failure_id",
            identifier_value=str(failure_id),
            expected_prefix=FAILURE_ID_PREFIX,
        )
    suffix = failure_id[len(FAILURE_ID_PREFIX):]
    if not _ID_PATTERN.match(suffix):
        raise InvalidProgrammerIdError(
            identifier_type="failure_id",
            identifier_value=str(failure_id),
            expected_prefix=FAILURE_ID_PREFIX,
        )


def new_watchdog_id() -> str:
    """Generate unique identifier for an ExecutionWatchdog record."""
    return f"{WATCHDOG_ID_PREFIX}{uuid.uuid4().hex[:8]}"


def validate_watchdog_id(watchdog_id: str) -> None:
    """
    Validate that watchdog_id starts with 'pwdog-' and conforms to identifier constraints.
    Raises InvalidProgrammerIdError if invalid.
    """
    if not isinstance(watchdog_id, str) or not watchdog_id.startswith(WATCHDOG_ID_PREFIX) or len(watchdog_id) <= len(WATCHDOG_ID_PREFIX):
        raise InvalidProgrammerIdError(
            identifier_type="watchdog_id",
            identifier_value=str(watchdog_id),
            expected_prefix=WATCHDOG_ID_PREFIX,
        )
    suffix = watchdog_id[len(WATCHDOG_ID_PREFIX):]
    if not _ID_PATTERN.match(suffix):
        raise InvalidProgrammerIdError(
            identifier_type="watchdog_id",
            identifier_value=str(watchdog_id),
            expected_prefix=WATCHDOG_ID_PREFIX,
        )


def new_recovery_decision_id() -> str:
    """Generate unique identifier for a RecoveryDecision record."""
    return f"{RECOVERY_DECISION_ID_PREFIX}{uuid.uuid4().hex[:8]}"


def validate_recovery_decision_id(decision_id: str) -> None:
    """
    Validate that decision_id starts with 'prec-' and conforms to identifier constraints.
    Raises InvalidProgrammerIdError if invalid.
    """
    if not isinstance(decision_id, str) or not decision_id.startswith(RECOVERY_DECISION_ID_PREFIX) or len(decision_id) <= len(RECOVERY_DECISION_ID_PREFIX):
        raise InvalidProgrammerIdError(
            identifier_type="decision_id",
            identifier_value=str(decision_id),
            expected_prefix=RECOVERY_DECISION_ID_PREFIX,
        )
    suffix = decision_id[len(RECOVERY_DECISION_ID_PREFIX):]
    if not _ID_PATTERN.match(suffix):
        raise InvalidProgrammerIdError(
            identifier_type="decision_id",
            identifier_value=str(decision_id),
            expected_prefix=RECOVERY_DECISION_ID_PREFIX,
        )


def new_retry_attempt_id() -> str:
    """Generate unique identifier for a RetryAttemptRecord."""
    return f"{RETRY_ATTEMPT_ID_PREFIX}{uuid.uuid4().hex[:8]}"


def validate_retry_attempt_id(attempt_id: str) -> None:
    """
    Validate that attempt_id starts with 'pretry-' and conforms to identifier constraints.
    Raises InvalidProgrammerIdError if invalid.
    """
    if not isinstance(attempt_id, str) or not attempt_id.startswith(RETRY_ATTEMPT_ID_PREFIX) or len(attempt_id) <= len(RETRY_ATTEMPT_ID_PREFIX):
        raise InvalidProgrammerIdError(
            identifier_type="attempt_id",
            identifier_value=str(attempt_id),
            expected_prefix=RETRY_ATTEMPT_ID_PREFIX,
        )
    suffix = attempt_id[len(RETRY_ATTEMPT_ID_PREFIX):]
    if not _ID_PATTERN.match(suffix):
        raise InvalidProgrammerIdError(
            identifier_type="attempt_id",
            identifier_value=str(attempt_id),
            expected_prefix=RETRY_ATTEMPT_ID_PREFIX,
        )


def new_escalation_id() -> str:
    """Generate unique identifier for a ProgrammerEscalation record."""
    return f"{ESCALATION_ID_PREFIX}{uuid.uuid4().hex[:8]}"


def validate_escalation_id(escalation_id: str) -> None:
    """
    Validate that escalation_id starts with 'pesc-' and conforms to identifier constraints.
    Raises InvalidProgrammerIdError if invalid.
    """
    if not isinstance(escalation_id, str) or not escalation_id.startswith(ESCALATION_ID_PREFIX) or len(escalation_id) <= len(ESCALATION_ID_PREFIX):
        raise InvalidProgrammerIdError(
            identifier_type="escalation_id",
            identifier_value=str(escalation_id),
            expected_prefix=ESCALATION_ID_PREFIX,
        )
    suffix = escalation_id[len(ESCALATION_ID_PREFIX):]
    if not _ID_PATTERN.match(suffix):
        raise InvalidProgrammerIdError(
            identifier_type="escalation_id",
            identifier_value=str(escalation_id),
            expected_prefix=ESCALATION_ID_PREFIX,
        )


def new_git_repository_id() -> str:
    """Generate unique identifier for a GitRepository."""
    return f"{GIT_REPOSITORY_ID_PREFIX}{uuid.uuid4().hex[:8]}"


def validate_git_repository_id(repository_id: str) -> None:
    """
    Validate that repository_id starts with 'grepo-' and conforms to identifier constraints.
    Raises InvalidProgrammerIdError if invalid.
    """
    if not isinstance(repository_id, str) or not repository_id.startswith(GIT_REPOSITORY_ID_PREFIX) or len(repository_id) <= len(GIT_REPOSITORY_ID_PREFIX):
        raise InvalidProgrammerIdError(
            identifier_type="repository_id",
            identifier_value=str(repository_id),
            expected_prefix=GIT_REPOSITORY_ID_PREFIX,
        )
    suffix = repository_id[len(GIT_REPOSITORY_ID_PREFIX):]
    if not _ID_PATTERN.match(suffix):
        raise InvalidProgrammerIdError(
            identifier_type="repository_id",
            identifier_value=str(repository_id),
            expected_prefix=GIT_REPOSITORY_ID_PREFIX,
        )


def new_git_revision_id() -> str:
    """Generate unique identifier for a GitRevision."""
    return f"{GIT_REVISION_ID_PREFIX}{uuid.uuid4().hex[:8]}"


def validate_git_revision_id(revision_id: str) -> None:
    """
    Validate that revision_id starts with 'grev-' and conforms to identifier constraints.
    Raises InvalidProgrammerIdError if invalid.
    """
    if not isinstance(revision_id, str) or not revision_id.startswith(GIT_REVISION_ID_PREFIX) or len(revision_id) <= len(GIT_REVISION_ID_PREFIX):
        raise InvalidProgrammerIdError(
            identifier_type="revision_id",
            identifier_value=str(revision_id),
            expected_prefix=GIT_REVISION_ID_PREFIX,
        )
    suffix = revision_id[len(GIT_REVISION_ID_PREFIX):]
    if not _ID_PATTERN.match(suffix):
        raise InvalidProgrammerIdError(
            identifier_type="revision_id",
            identifier_value=str(revision_id),
            expected_prefix=GIT_REVISION_ID_PREFIX,
        )


def new_git_worktree_id() -> str:
    """Generate unique identifier for an isolated GitWorktree."""
    return f"{GIT_WORKTREE_ID_PREFIX}{uuid.uuid4().hex[:8]}"


def validate_git_worktree_id(worktree_id: str) -> None:
    """
    Validate that worktree_id starts with 'gwt-' and conforms to identifier constraints.
    Raises InvalidProgrammerIdError if invalid.
    """
    if not isinstance(worktree_id, str) or not worktree_id.startswith(GIT_WORKTREE_ID_PREFIX) or len(worktree_id) <= len(GIT_WORKTREE_ID_PREFIX):
        raise InvalidProgrammerIdError(
            identifier_type="worktree_id",
            identifier_value=str(worktree_id),
            expected_prefix=GIT_WORKTREE_ID_PREFIX,
        )
    suffix = worktree_id[len(GIT_WORKTREE_ID_PREFIX):]
    if not _ID_PATTERN.match(suffix):
        raise InvalidProgrammerIdError(
            identifier_type="worktree_id",
            identifier_value=str(worktree_id),
            expected_prefix=GIT_WORKTREE_ID_PREFIX,
        )


def new_change_set_id() -> str:
    """Generate unique identifier for a ChangeSet."""
    return f"{CHANGESET_ID_PREFIX}{uuid.uuid4().hex[:8]}"


def validate_change_set_id(change_set_id: str) -> None:
    """
    Validate that change_set_id starts with 'pcs-' and conforms to identifier constraints.
    Raises InvalidProgrammerIdError if invalid.
    """
    if not isinstance(change_set_id, str) or not change_set_id.startswith(CHANGESET_ID_PREFIX) or len(change_set_id) <= len(CHANGESET_ID_PREFIX):
        raise InvalidProgrammerIdError(
            identifier_type="change_set_id",
            identifier_value=str(change_set_id),
            expected_prefix=CHANGESET_ID_PREFIX,
        )
    suffix = change_set_id[len(CHANGESET_ID_PREFIX):]
    if not _ID_PATTERN.match(suffix):
        raise InvalidProgrammerIdError(
            identifier_type="change_set_id",
            identifier_value=str(change_set_id),
            expected_prefix=CHANGESET_ID_PREFIX,
        )


def new_repo_state_verification_id() -> str:
    """Generate unique identifier for a RepositoryStateVerification."""
    return f"{REPO_STATE_VERIFICATION_ID_PREFIX}{uuid.uuid4().hex[:8]}"


def validate_repo_state_verification_id(verification_id: str) -> None:
    """
    Validate that verification_id starts with 'vrepo-' and conforms to identifier constraints.
    Raises InvalidProgrammerIdError if invalid.
    """
    if not isinstance(verification_id, str) or not verification_id.startswith(REPO_STATE_VERIFICATION_ID_PREFIX) or len(verification_id) <= len(REPO_STATE_VERIFICATION_ID_PREFIX):
        raise InvalidProgrammerIdError(
            identifier_type="verification_id",
            identifier_value=str(verification_id),
            expected_prefix=REPO_STATE_VERIFICATION_ID_PREFIX,
        )
    suffix = verification_id[len(REPO_STATE_VERIFICATION_ID_PREFIX):]
    if not _ID_PATTERN.match(suffix):
        raise InvalidProgrammerIdError(
            identifier_type="verification_id",
            identifier_value=str(verification_id),
            expected_prefix=REPO_STATE_VERIFICATION_ID_PREFIX,
        )


def new_delivery_id() -> str:
    """Generate unique identifier for a DeliveryPackage."""
    return f"{DELIVERY_ID_PREFIX}{uuid.uuid4().hex[:8]}"


def validate_delivery_id(delivery_id: str) -> None:
    """
    Validate that delivery_id starts with 'pdel-' and conforms to identifier constraints.
    Raises InvalidProgrammerIdError if invalid.
    """
    if not isinstance(delivery_id, str) or not delivery_id.startswith(DELIVERY_ID_PREFIX) or len(delivery_id) <= len(DELIVERY_ID_PREFIX):
        raise InvalidProgrammerIdError(
            identifier_type="delivery_id",
            identifier_value=str(delivery_id),
            expected_prefix=DELIVERY_ID_PREFIX,
        )
    suffix = delivery_id[len(DELIVERY_ID_PREFIX):]
    if not _ID_PATTERN.match(suffix):
        raise InvalidProgrammerIdError(
            identifier_type="delivery_id",
            identifier_value=str(delivery_id),
            expected_prefix=DELIVERY_ID_PREFIX,
        )


def new_codebase_understanding_id() -> str:
    """Generate unique identifier for a CodebaseUnderstanding."""
    return f"{UNDERSTANDING_ID_PREFIX}{uuid.uuid4().hex[:8]}"


def validate_codebase_understanding_id(understanding_id: str) -> None:
    """
    Validate that understanding_id starts with 'pund-' and conforms to identifier constraints.
    Raises InvalidProgrammerIdError if invalid.
    """
    if not isinstance(understanding_id, str) or not understanding_id.startswith(UNDERSTANDING_ID_PREFIX) or len(understanding_id) <= len(UNDERSTANDING_ID_PREFIX):
        raise InvalidProgrammerIdError(
            identifier_type="understanding_id",
            identifier_value=str(understanding_id),
            expected_prefix=UNDERSTANDING_ID_PREFIX,
        )
    suffix = understanding_id[len(UNDERSTANDING_ID_PREFIX):]
    if not _ID_PATTERN.match(suffix):
        raise InvalidProgrammerIdError(
            identifier_type="understanding_id",
            identifier_value=str(understanding_id),
            expected_prefix=UNDERSTANDING_ID_PREFIX,
        )


def new_impact_analysis_id() -> str:
    """Generate unique identifier for an ImpactAnalysis."""
    return f"{IMPACT_ANALYSIS_ID_PREFIX}{uuid.uuid4().hex[:8]}"


def validate_impact_analysis_id(analysis_id: str) -> None:
    """
    Validate that analysis_id starts with 'pimp-' and conforms to identifier constraints.
    Raises InvalidProgrammerIdError if invalid.
    """
    if not isinstance(analysis_id, str) or not analysis_id.startswith(IMPACT_ANALYSIS_ID_PREFIX) or len(analysis_id) <= len(IMPACT_ANALYSIS_ID_PREFIX):
        raise InvalidProgrammerIdError(
            identifier_type="analysis_id",
            identifier_value=str(analysis_id),
            expected_prefix=IMPACT_ANALYSIS_ID_PREFIX,
        )
    suffix = analysis_id[len(IMPACT_ANALYSIS_ID_PREFIX):]
    if not _ID_PATTERN.match(suffix):
        raise InvalidProgrammerIdError(
            identifier_type="analysis_id",
            identifier_value=str(analysis_id),
            expected_prefix=IMPACT_ANALYSIS_ID_PREFIX,
        )


def new_plan_id() -> str:
    """Generate unique identifier for an ImplementationPlan."""
    return f"{PLAN_ID_PREFIX}{uuid.uuid4().hex[:8]}"


def validate_plan_id(plan_id: str) -> None:
    """
    Validate that plan_id starts with 'ppln-' and conforms to identifier constraints.
    Raises InvalidProgrammerIdError if invalid.
    """
    if not isinstance(plan_id, str) or not plan_id.startswith(PLAN_ID_PREFIX) or len(plan_id) <= len(PLAN_ID_PREFIX):
        raise InvalidProgrammerIdError(
            identifier_type="plan_id",
            identifier_value=str(plan_id),
            expected_prefix=PLAN_ID_PREFIX,
        )
    suffix = plan_id[len(PLAN_ID_PREFIX):]
    if not _ID_PATTERN.match(suffix):
        raise InvalidProgrammerIdError(
            identifier_type="plan_id",
            identifier_value=str(plan_id),
            expected_prefix=PLAN_ID_PREFIX,
        )


def new_plan_step_id() -> str:
    """Generate unique identifier for an ImplementationStep."""
    return f"{PLAN_STEP_ID_PREFIX}{uuid.uuid4().hex[:8]}"


def validate_plan_step_id(step_id: str) -> None:
    """
    Validate that step_id starts with 'pstep-' and conforms to identifier constraints.
    Raises InvalidProgrammerIdError if invalid.
    """
    if not isinstance(step_id, str) or not step_id.startswith(PLAN_STEP_ID_PREFIX) or len(step_id) <= len(PLAN_STEP_ID_PREFIX):
        raise InvalidProgrammerIdError(
            identifier_type="step_id",
            identifier_value=str(step_id),
            expected_prefix=PLAN_STEP_ID_PREFIX,
        )
    suffix = step_id[len(PLAN_STEP_ID_PREFIX):]
    if not _ID_PATTERN.match(suffix):
        raise InvalidProgrammerIdError(
            identifier_type="step_id",
            identifier_value=str(step_id),
            expected_prefix=PLAN_STEP_ID_PREFIX,
        )


def new_escalation_candidate_id() -> str:
    """Generate unique identifier for an EscalationCandidate."""
    return f"{ESCALATION_CANDIDATE_ID_PREFIX}{uuid.uuid4().hex[:8]}"


def validate_escalation_candidate_id(candidate_id: str) -> None:
    """
    Validate that candidate_id starts with 'pescand-' and conforms to identifier constraints.
    Raises InvalidProgrammerIdError if invalid.
    """
    if not isinstance(candidate_id, str) or not candidate_id.startswith(ESCALATION_CANDIDATE_ID_PREFIX) or len(candidate_id) <= len(ESCALATION_CANDIDATE_ID_PREFIX):
        raise InvalidProgrammerIdError(
            identifier_type="candidate_id",
            identifier_value=str(candidate_id),
            expected_prefix=ESCALATION_CANDIDATE_ID_PREFIX,
        )
    suffix = candidate_id[len(ESCALATION_CANDIDATE_ID_PREFIX):]
    if not _ID_PATTERN.match(suffix):
        raise InvalidProgrammerIdError(
            identifier_type="candidate_id",
            identifier_value=str(candidate_id),
            expected_prefix=ESCALATION_CANDIDATE_ID_PREFIX,
        )


def new_engineering_risk_id() -> str:
    """Generate unique identifier for an EngineeringRisk."""
    return f"{ENGINEERING_RISK_ID_PREFIX}{uuid.uuid4().hex[:8]}"


def validate_engineering_risk_id(risk_id: str) -> None:
    """
    Validate that risk_id starts with 'prisk-' and conforms to identifier constraints.
    Raises InvalidProgrammerIdError if invalid.
    """
    if not isinstance(risk_id, str) or not risk_id.startswith(ENGINEERING_RISK_ID_PREFIX) or len(risk_id) <= len(ENGINEERING_RISK_ID_PREFIX):
        raise InvalidProgrammerIdError(
            identifier_type="risk_id",
            identifier_value=str(risk_id),
            expected_prefix=ENGINEERING_RISK_ID_PREFIX,
        )
    suffix = risk_id[len(ENGINEERING_RISK_ID_PREFIX):]
    if not _ID_PATTERN.match(suffix):
        raise InvalidProgrammerIdError(
            identifier_type="risk_id",
            identifier_value=str(risk_id),
            expected_prefix=ENGINEERING_RISK_ID_PREFIX,
        )


def new_risk_assessment_id() -> str:
    """Generate unique identifier for a RiskAssessment."""
    return f"{RISK_ASSESSMENT_ID_PREFIX}{uuid.uuid4().hex[:8]}"


def validate_risk_assessment_id(assessment_id: str) -> None:
    """
    Validate that assessment_id starts with 'prasm-' and conforms to identifier constraints.
    Raises InvalidProgrammerIdError if invalid.
    """
    if not isinstance(assessment_id, str) or not assessment_id.startswith(RISK_ASSESSMENT_ID_PREFIX) or len(assessment_id) <= len(RISK_ASSESSMENT_ID_PREFIX):
        raise InvalidProgrammerIdError(
            identifier_type="assessment_id",
            identifier_value=str(assessment_id),
            expected_prefix=RISK_ASSESSMENT_ID_PREFIX,
        )
    suffix = assessment_id[len(RISK_ASSESSMENT_ID_PREFIX):]
    if not _ID_PATTERN.match(suffix):
        raise InvalidProgrammerIdError(
            identifier_type="assessment_id",
            identifier_value=str(assessment_id),
            expected_prefix=RISK_ASSESSMENT_ID_PREFIX,
        )


def new_plan_validation_result_id() -> str:
    """Generate unique identifier for a PlanValidationResult."""
    return f"{PLAN_VALIDATION_RESULT_ID_PREFIX}{uuid.uuid4().hex[:8]}"


def validate_plan_validation_result_id(result_id: str) -> None:
    """
    Validate that result_id starts with 'pvr-' and conforms to identifier constraints.
    Raises InvalidProgrammerIdError if invalid.
    """
    if not isinstance(result_id, str) or not result_id.startswith(PLAN_VALIDATION_RESULT_ID_PREFIX) or len(result_id) <= len(PLAN_VALIDATION_RESULT_ID_PREFIX):
        raise InvalidProgrammerIdError(
            identifier_type="result_id",
            identifier_value=str(result_id),
            expected_prefix=PLAN_VALIDATION_RESULT_ID_PREFIX,
        )
    suffix = result_id[len(PLAN_VALIDATION_RESULT_ID_PREFIX):]
    if not _ID_PATTERN.match(suffix):
        raise InvalidProgrammerIdError(
            identifier_type="result_id",
            identifier_value=str(result_id),
            expected_prefix=PLAN_VALIDATION_RESULT_ID_PREFIX,
        )


def new_supervision_record_id() -> str:
    """Generate unique identifier for an ExecutionSupervisionRecord."""
    return f"{SUPERVISION_RECORD_ID_PREFIX}{uuid.uuid4().hex[:8]}"


def validate_supervision_record_id(supervision_id: str) -> None:
    """
    Validate that supervision_id starts with 'psup-' and conforms to identifier constraints.
    Raises InvalidProgrammerIdError if invalid.
    """
    if not isinstance(supervision_id, str) or not supervision_id.startswith(SUPERVISION_RECORD_ID_PREFIX) or len(supervision_id) <= len(SUPERVISION_RECORD_ID_PREFIX):
        raise InvalidProgrammerIdError(
            identifier_type="supervision_id",
            identifier_value=str(supervision_id),
            expected_prefix=SUPERVISION_RECORD_ID_PREFIX,
        )
    suffix = supervision_id[len(SUPERVISION_RECORD_ID_PREFIX):]
    if not _ID_PATTERN.match(suffix):
        raise InvalidProgrammerIdError(
            identifier_type="supervision_id",
            identifier_value=str(supervision_id),
            expected_prefix=SUPERVISION_RECORD_ID_PREFIX,
        )


def new_plan_deviation_id() -> str:
    """Generate unique identifier for a PlanDeviation."""
    return f"{PLAN_DEVIATION_ID_PREFIX}{uuid.uuid4().hex[:8]}"


def validate_plan_deviation_id(deviation_id: str) -> None:
    """
    Validate that deviation_id starts with 'pdev-' and conforms to identifier constraints.
    Raises InvalidProgrammerIdError if invalid.
    """
    if not isinstance(deviation_id, str) or not deviation_id.startswith(PLAN_DEVIATION_ID_PREFIX) or len(deviation_id) <= len(PLAN_DEVIATION_ID_PREFIX):
        raise InvalidProgrammerIdError(
            identifier_type="deviation_id",
            identifier_value=str(deviation_id),
            expected_prefix=PLAN_DEVIATION_ID_PREFIX,
        )
    suffix = deviation_id[len(PLAN_DEVIATION_ID_PREFIX):]
    if not _ID_PATTERN.match(suffix):
        raise InvalidProgrammerIdError(
            identifier_type="deviation_id",
            identifier_value=str(deviation_id),
            expected_prefix=PLAN_DEVIATION_ID_PREFIX,
        )


def is_programmer_id(identifier: str) -> bool:
    """Check whether a given identifier belongs to the Programmer subsystem domain."""
    if not isinstance(identifier, str):
        return False
    return any(identifier.startswith(prefix) for prefix in ALL_PROGRAMMER_PREFIXES)



