from __future__ import annotations

from workers.programmer.editor import CodeEditor, ScopeViolationError
from workers.programmer.inspector import RepositoryInspector
from workers.programmer.model import (
    BuildExecutionResult,
    ChangeRecord,
    FileChange,
    ProgrammerResult,
    ProgrammingPlan,
    ProgrammingScope,
    ProgrammingTaskSpec,
    SelfReviewResult,
    TestExecutionResult,
    compute_checksum,
)
from workers.programmer.planner import ProgrammingPlanner
from workers.programmer.prompt import (
    PROGRAMMER_SYSTEM_PROMPT,
    build_implementation_prompt,
    parse_implementation_decision,
)
from workers.programmer.report import ProgrammingReportGenerator
from workers.programmer.scope import ScopeGuard
from workers.programmer.tester import TestRunner
from workers.programmer.types import (
    FileChangeType,
    ProgrammingMode,
    ProgrammingStatus,
    TestFailureType,
)
from workers.programmer.worker import ProgrammerWorker

__all__ = [
    "ProgrammingMode",
    "FileChangeType",
    "TestFailureType",
    "ProgrammingStatus",
    "compute_checksum",
    "ProgrammingScope",
    "ProgrammingTaskSpec",
    "ProgrammingPlan",
    "FileChange",
    "TestExecutionResult",
    "BuildExecutionResult",
    "SelfReviewResult",
    "ChangeRecord",
    "ProgrammerResult",
    "ProgrammingPlanner",
    "ScopeGuard",
    "RepositoryInspector",
    "CodeEditor",
    "ScopeViolationError",
    "TestRunner",
    "PROGRAMMER_SYSTEM_PROMPT",
    "build_implementation_prompt",
    "parse_implementation_decision",
    "ProgrammingReportGenerator",
    "ProgrammerWorker",
]
