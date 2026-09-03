"""
Workforce Execution Activity & Presentation Layer.
Provides a sanitized, structured projection of technical runtime events for human visibility.
"""
from core.activity.model import (
    ActivityStatus,
    CommandLineItem,
    ExecutionActivity,
    FileActivityItem,
    FileOperationType,
    WorkerActivityItem,
)
from core.activity.projector import WorkforceActivityProjector

__all__ = [
    "ActivityStatus",
    "FileOperationType",
    "CommandLineItem",
    "FileActivityItem",
    "WorkerActivityItem",
    "ExecutionActivity",
    "WorkforceActivityProjector",
]
