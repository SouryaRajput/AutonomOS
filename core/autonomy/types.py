from __future__ import annotations

from enum import Enum


class AutonomyLevel(str, Enum):
    """Configurable levels of autonomy for AutonomOS execution."""
    FULL_MANUAL = "FULL_MANUAL"        # All actions and tool calls require explicit user approval
    SUPERVISED = "SUPERVISED"          # Low risk reads allowed, all writes/executes require approval
    BALANCED = "BALANCED"              # Low & medium risk allowed; high & critical require approval (Default)
    HIGH_AUTONOMY = "HIGH_AUTONOMY"    # Low, medium, and high risk allowed within sandbox; critical/destructive requires approval
    FULL_AUTONOMY = "FULL_AUTONOMY"    # Autonomous execution bounded only by hard safety invariants (deny rules)


class ActionCategory(str, Enum):
    """Classification of consequential agent and tool actions."""
    READ = "READ"
    WRITE = "WRITE"
    MODIFY = "MODIFY"
    DELETE = "DELETE"
    EXECUTE = "EXECUTE"
    NETWORK = "NETWORK"
    EXTERNAL_SIDE_EFFECT = "EXTERNAL_SIDE_EFFECT"
    PUBLISH = "PUBLISH"
    DEPLOY = "DEPLOY"
    CREDENTIAL_ACCESS = "CREDENTIAL_ACCESS"
    PERMISSION_CHANGE = "PERMISSION_CHANGE"


class PolicyDecisionResult(str, Enum):
    """Outcome of a deterministic policy evaluation."""
    ALLOW = "ALLOW"
    APPROVAL_REQUIRED = "APPROVAL_REQUIRED"
    DENY = "DENY"


class ApprovalRequestStatus(str, Enum):
    """Lifecycle status of an approval request."""
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    EXPIRED = "EXPIRED"
    CANCELLED = "CANCELLED"


class UserInputStatus(str, Enum):
    """Status of a user clarification request."""
    PENDING = "PENDING"
    ANSWERED = "ANSWERED"
    CANCELLED = "CANCELLED"


class DecisionRequestStatus(str, Enum):
    """Status of an architectural or product choice request."""
    PENDING = "PENDING"
    DECIDED = "DECIDED"
    CANCELLED = "CANCELLED"
