from __future__ import annotations

from core.programmer.contracts.acceptance_criteria import AcceptanceCriterion
from core.programmer.contracts.acceptance_result import AcceptanceCriterionResult
from core.programmer.contracts.blocker import ProgrammerBlocker
from core.programmer.contracts.cancellation import ProgrammerCancellation
from core.programmer.contracts.command_record import CommandExecutionRecord
from core.programmer.contracts.command_scope import AllowedCommand
from core.programmer.contracts.evidence import ProgrammerEvidence
from core.programmer.contracts.execution import ProgrammerExecution
from core.programmer.contracts.identifiers import (
    BLOCKER_ID_PREFIX,
    EXECUTION_ID_PREFIX,
    RESULT_ID_PREFIX,
    TRACE_ID_PREFIX,
    WORK_ORDER_ID_PREFIX,
    is_programmer_id,
    new_blocker_id,
    new_execution_id,
    new_result_id,
    new_trace_id,
    new_work_order_id,
    validate_blocker_id,
    validate_execution_id,
    validate_result_id,
    validate_trace_id,
    validate_work_order_id,
)
from core.programmer.contracts.lifecycle import ProgrammerLifecycle
from core.programmer.contracts.manager_bridge import (
    FakeProgrammerWorker,
    ProgrammerManagerBridge,
)
from core.programmer.contracts.research_reference import ResearchEvidenceReference
from core.programmer.contracts.result import ProgrammerResult
from core.programmer.contracts.test_record import TestResultRecord
from core.programmer.contracts.trace import ProgrammerTrace, compute_sha256
from core.programmer.contracts.validator import ProgrammerWorkOrderValidator
from core.programmer.contracts.work_order import ProgrammerWorkOrder

__all__ = [
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
    "new_work_order_id",
    "new_execution_id",
    "new_trace_id",
    "new_result_id",
    "new_blocker_id",
    "validate_work_order_id",
    "validate_execution_id",
    "validate_trace_id",
    "validate_result_id",
    "validate_blocker_id",
    "is_programmer_id",
    "compute_sha256",
]
