"""AutonomOS Safety, Checkpoints & Rollback Package."""
from core.safety.checkpoint import CheckpointManager, compute_file_checksum
from core.safety.evaluator import SafetyEvaluator
from core.safety.model import (
    ChangeRecord,
    Checkpoint,
    RollbackResult,
    SafetyConfig,
    SafetyDecision,
    ScopeDeviation,
)
from core.safety.rollback import RollbackManager
from core.safety.scope import ScopeTracker
from core.safety.types import (
    CheckpointStatus,
    CheckpointType,
    RollbackStatus,
    SafetyAction,
    ScopeDeviationType,
)

__all__ = [
    "CheckpointManager",
    "compute_file_checksum",
    "SafetyEvaluator",
    "RollbackManager",
    "ScopeTracker",
    "SafetyAction",
    "CheckpointType",
    "CheckpointStatus",
    "RollbackStatus",
    "ScopeDeviationType",
    "SafetyConfig",
    "SafetyDecision",
    "Checkpoint",
    "ChangeRecord",
    "ScopeDeviation",
    "RollbackResult",
]
