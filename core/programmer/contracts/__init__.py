from __future__ import annotations

from core.programmer.contracts.acceptance_criteria import AcceptanceCriterion
from core.programmer.contracts.acceptance_result import AcceptanceCriterionResult
from core.programmer.contracts.blocker import ProgrammerBlocker
from core.programmer.contracts.cancellation import ProgrammerCancellation
from core.programmer.contracts.coding_agent import (
    CodingAgentBackend,
    CodingAgentCancellationRequest,
    CodingAgentCancellationResult,
    CodingAgentEvent,
    CodingAgentRequest,
    CodingAgentResult,
    MockCodingAgentBackend,
)
from core.programmer.contracts.command_record import CommandExecutionRecord
from core.programmer.contracts.command_resolver import (
    CommandBoundaryResolver,
    CommandDecision,
    CommandRequest,
)
from core.programmer.contracts.command_scope import AllowedCommand
from core.programmer.contracts.evidence import ProgrammerEvidence
from core.programmer.contracts.execution import ProgrammerExecution
from core.programmer.contracts.execution_context import ProgrammerExecutionContext
from core.programmer.contracts.filesystem_resolver import (
    FilesystemBoundaryResolver,
    FilesystemDecision,
    is_subpath_or_equal,
)
from core.programmer.contracts.identifiers import (
    BACKEND_EVENT_ID_PREFIX,
    BACKEND_REQUEST_ID_PREFIX,
    BACKEND_RESULT_ID_PREFIX,
    BLOCKER_ID_PREFIX,
    EXECUTION_ID_PREFIX,
    RESULT_ID_PREFIX,
    TRACE_ID_PREFIX,
    WORKSPACE_ID_PREFIX,
    WORK_ORDER_ID_PREFIX,
    is_programmer_id,
    new_backend_event_id,
    new_backend_request_id,
    new_backend_result_id,
    new_blocker_id,
    new_execution_id,
    new_result_id,
    new_trace_id,
    new_work_order_id,
    new_workspace_id,
    validate_backend_request_id,
    validate_blocker_id,
    validate_execution_id,
    validate_result_id,
    validate_trace_id,
    validate_work_order_id,
    validate_workspace_id,
)
from core.programmer.contracts.lifecycle import ProgrammerLifecycle
from core.programmer.contracts.manager_bridge import (
    FakeProgrammerWorker,
    ProgrammerManagerBridge,
)
from core.programmer.contracts.provisioner import (
    WorkspaceProvisioner,
    WorkspaceProvisioningResult,
)
from core.programmer.contracts.research_reference import ResearchEvidenceReference
from core.programmer.contracts.result import ProgrammerResult
from core.programmer.contracts.test_record import TestResultRecord
from core.programmer.contracts.trace import ProgrammerTrace, compute_sha256
from core.programmer.contracts.validator import ProgrammerWorkOrderValidator
from core.programmer.contracts.work_order import ProgrammerWorkOrder
from core.programmer.contracts.workspace import (
    ProgrammerWorkspace,
    Workspace,
    normalize_workspace_path,
)

__all__ = [
    "CommandBoundaryResolver",
    "CommandRequest",
    "CommandDecision",
    "FilesystemBoundaryResolver",
    "FilesystemDecision",
    "is_subpath_or_equal",
    "WorkspaceProvisioner",
    "WorkspaceProvisioningResult",
    "ProgrammerWorkspace",
    "Workspace",
    "ProgrammerExecutionContext",
    "normalize_workspace_path",
    "ProgrammerManagerBridge",
    "FakeProgrammerWorker",
    "ProgrammerWorkOrder",
    "ProgrammerWorkOrderValidator",
    "ProgrammerExecution",
    "ProgrammerTrace",
    "ProgrammerResult",
    "ProgrammerLifecycle",
    "ProgrammerBlocker",
    "ProgrammerCancellation",
    "AcceptanceCriterion",
    "AcceptanceCriterionResult",
    "AllowedCommand",
    "CommandExecutionRecord",
    "TestResultRecord",
    "ProgrammerEvidence",
    "ResearchEvidenceReference",
    "WORK_ORDER_ID_PREFIX",
    "EXECUTION_ID_PREFIX",
    "TRACE_ID_PREFIX",
    "RESULT_ID_PREFIX",
    "BLOCKER_ID_PREFIX",
    "WORKSPACE_ID_PREFIX",
    "new_work_order_id",
    "new_execution_id",
    "new_trace_id",
    "new_result_id",
    "new_blocker_id",
    "new_workspace_id",
    "validate_work_order_id",
    "validate_execution_id",
    "validate_trace_id",
    "validate_result_id",
    "CodingAgentBackend",
    "MockCodingAgentBackend",
    "CodingAgentRequest",
    "CodingAgentResult",
    "CodingAgentEvent",
    "CodingAgentCancellationRequest",
    "CodingAgentCancellationResult",
    "BACKEND_REQUEST_ID_PREFIX",
    "BACKEND_EVENT_ID_PREFIX",
    "BACKEND_RESULT_ID_PREFIX",
    "new_backend_request_id",
    "new_backend_event_id",
    "new_backend_result_id",
    "validate_backend_request_id",
    "is_programmer_id",
    "compute_sha256",
]
