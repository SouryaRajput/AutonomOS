"""AutonomOS Verification & Evidence System Package."""
from core.verification.adapters.artifact_adapter import ArtifactCheckAdapter
from core.verification.adapters.base import BaseCheckAdapter, CheckExecutionContext
from core.verification.adapters.command_adapter import CommandCheckAdapter
from core.verification.adapters.file_adapter import FileCheckAdapter, compute_file_sha256
from core.verification.adapters.git_adapter import GitCheckAdapter
from core.verification.engine import VerificationEngine
from core.verification.model import (
    SuccessCriterion,
    Verification,
    VerificationCheck,
    VerificationPlan,
    VerificationResult,
)
from core.verification.types import (
    CheckStatus,
    CheckType,
    VerificationStatus,
)

__all__ = [
    "CheckStatus",
    "CheckType",
    "VerificationStatus",
    "SuccessCriterion",
    "VerificationCheck",
    "VerificationPlan",
    "Verification",
    "VerificationResult",
    "BaseCheckAdapter",
    "CheckExecutionContext",
    "FileCheckAdapter",
    "CommandCheckAdapter",
    "GitCheckAdapter",
    "ArtifactCheckAdapter",
    "compute_file_sha256",
    "VerificationEngine",
]
