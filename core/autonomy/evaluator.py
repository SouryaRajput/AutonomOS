from __future__ import annotations

import re
from typing import Any, Optional
import uuid

from core.enums import RiskLevel
from core.autonomy.model import (
    AutonomyPolicy,
    PolicyDecision,
    PolicyExplanation,
)
from core.autonomy.types import (
    ActionCategory,
    AutonomyLevel,
    PolicyDecisionResult,
)


class AutonomyEvaluator:
    """
    Deterministic action classifier and risk evaluator for autonomous workforce operations.
    Enforces deny-always-wins precedence, autonomy level thresholds, and scope constraints.
    """

    @classmethod
    def classify_action(cls, tool_id: str, arguments: dict[str, Any]) -> tuple[ActionCategory, RiskLevel]:
        tool_lower = tool_id.lower()

        # Filesystem operations
        if "filesystem.read" in tool_lower or "filesystem.list" in tool_lower:
            return ActionCategory.READ, RiskLevel.LOW
        if "filesystem.write" in tool_lower:
            return ActionCategory.WRITE, RiskLevel.MEDIUM
        if "filesystem.delete" in tool_lower:
            path = str(arguments.get("path", "")).lower()
            if any(p in path for p in (".git", "package.json", "setup.py", "pyproject.toml", "core/")):
                return ActionCategory.DELETE, RiskLevel.HIGH
            return ActionCategory.DELETE, RiskLevel.MEDIUM

        # Shell and execution
        if "shell.execute" in tool_lower:
            cmd = str(arguments.get("command", "")).lower()
            if any(p in cmd for p in ("rm -rf", "drop table", "format", "mkfs", "dd if=")):
                return ActionCategory.EXECUTE, RiskLevel.CRITICAL
            if any(p in cmd for p in ("pip install", "npm install", "cargo add", "git commit", "git push")):
                return ActionCategory.EXECUTE, RiskLevel.HIGH
            return ActionCategory.EXECUTE, RiskLevel.LOW

        # Web operations
        if "web.search" in tool_lower or "web.fetch" in tool_lower or "web" in tool_lower:
            return ActionCategory.NETWORK, RiskLevel.LOW

        # OCR and Vision
        if "ocr" in tool_lower or "screenshot" in tool_lower:
            return ActionCategory.READ, RiskLevel.LOW

        # Credential and Secrets
        if "secret" in tool_lower or "credential" in tool_lower:
            return ActionCategory.CREDENTIAL_ACCESS, RiskLevel.CRITICAL

        # Publishing and deployment
        if any(w in tool_lower for w in ("publish", "deploy", "release")):
            return ActionCategory.PUBLISH, RiskLevel.CRITICAL

        return ActionCategory.EXECUTE, RiskLevel.MEDIUM

    @classmethod
    def evaluate_action(
        cls,
        policy: AutonomyPolicy,
        tool_id: str,
        arguments: dict[str, Any],
        is_emergency_stopped: bool = False,
    ) -> PolicyDecision:
        decision_id = f"dec-{uuid.uuid4().hex[:8]}"
        category, risk = cls.classify_action(tool_id, arguments)
        matched_rules: list[str] = []

        # 1. Emergency Stop Check (Highest Precedence)
        if is_emergency_stopped:
            explanation = PolicyExplanation(
                decision=PolicyDecisionResult.DENY,
                risk_level=RiskLevel.CRITICAL,
                matched_rules=["EMERGENCY_STOP_ACTIVE"],
                summary="Action denied because emergency stop is currently active.",
            )
            return PolicyDecision(
                id=decision_id,
                action=f"{tool_id}({arguments})",
                category=category,
                result=PolicyDecisionResult.DENY,
                risk_level=RiskLevel.CRITICAL,
                policy_id=policy.id,
                reason="Emergency stop is active.",
                explanation=explanation,
            )

        # 2. Hard Deny Rules
        if tool_id in policy.denied_tools:
            matched_rules.append(f"DENIED_TOOL_{tool_id}")
            explanation = PolicyExplanation(
                decision=PolicyDecisionResult.DENY,
                risk_level=RiskLevel.CRITICAL,
                matched_rules=matched_rules,
                summary=f"Tool '{tool_id}' is explicitly denied by project autonomy policy.",
            )
            return PolicyDecision(
                id=decision_id,
                action=tool_id,
                category=category,
                result=PolicyDecisionResult.DENY,
                risk_level=RiskLevel.CRITICAL,
                policy_id=policy.id,
                reason=f"Tool '{tool_id}' is explicitly denied.",
                explanation=explanation,
            )

        # Check for prohibited paths (like /etc, .git, etc.)
        arg_str = str(arguments)
        if any(p in arg_str for p in ("/etc/", "/usr/", "C:\\Windows", "../")):
            matched_rules.append("PATH_CONFINEMENT_VIOLATION")
            explanation = PolicyExplanation(
                decision=PolicyDecisionResult.DENY,
                risk_level=RiskLevel.CRITICAL,
                matched_rules=matched_rules,
                summary="Access to system paths or directory traversal outside workspace is forbidden.",
            )
            return PolicyDecision(
                id=decision_id,
                action=tool_id,
                category=category,
                result=PolicyDecisionResult.DENY,
                risk_level=RiskLevel.CRITICAL,
                policy_id=policy.id,
                reason="Workspace path confinement violation.",
                explanation=explanation,
            )

        # 3. Explicit Approval Required Actions
        if category in policy.approval_required_actions:
            matched_rules.append(f"APPROVAL_REQUIRED_FOR_{category.value}")
            explanation = PolicyExplanation(
                decision=PolicyDecisionResult.APPROVAL_REQUIRED,
                risk_level=risk,
                matched_rules=matched_rules,
                required_approval_scope=f"Authorize action '{tool_id}' in category '{category.value}'",
                summary=f"Category '{category.value}' requires explicit human approval under policy.",
            )
            return PolicyDecision(
                id=decision_id,
                action=tool_id,
                category=category,
                result=PolicyDecisionResult.APPROVAL_REQUIRED,
                risk_level=risk,
                policy_id=policy.id,
                reason=f"Action category '{category.value}' requires approval.",
                explanation=explanation,
            )

        # 4. Autonomy Level Evaluation
        level = policy.autonomy_level

        if level == AutonomyLevel.FULL_MANUAL:
            matched_rules.append("FULL_MANUAL_LEVEL")
            explanation = PolicyExplanation(
                decision=PolicyDecisionResult.APPROVAL_REQUIRED,
                risk_level=risk,
                matched_rules=matched_rules,
                required_approval_scope=f"Authorize '{tool_id}'",
                summary="Full manual autonomy level requires human approval for all actions.",
            )
            return PolicyDecision(
                id=decision_id,
                action=tool_id,
                category=category,
                result=PolicyDecisionResult.APPROVAL_REQUIRED,
                risk_level=risk,
                policy_id=policy.id,
                reason="Full manual level requires human approval.",
                explanation=explanation,
            )

        if level == AutonomyLevel.SUPERVISED:
            if category != ActionCategory.READ:
                matched_rules.append("SUPERVISED_NON_READ_ACTION")
                explanation = PolicyExplanation(
                    decision=PolicyDecisionResult.APPROVAL_REQUIRED,
                    risk_level=risk,
                    matched_rules=matched_rules,
                    required_approval_scope=f"Authorize non-read action '{tool_id}'",
                    summary="Supervised autonomy level requires approval for non-read actions.",
                )
                return PolicyDecision(
                    id=decision_id,
                    action=tool_id,
                    category=category,
                    result=PolicyDecisionResult.APPROVAL_REQUIRED,
                    risk_level=risk,
                    policy_id=policy.id,
                    reason="Supervised level requires approval for writes and executions.",
                    explanation=explanation,
                )

        if level == AutonomyLevel.BALANCED:
            if risk in (RiskLevel.HIGH, RiskLevel.CRITICAL):
                matched_rules.append("BALANCED_HIGH_OR_CRITICAL_RISK")
                explanation = PolicyExplanation(
                    decision=PolicyDecisionResult.APPROVAL_REQUIRED,
                    risk_level=risk,
                    matched_rules=matched_rules,
                    required_approval_scope=f"Authorize high-risk action '{tool_id}'",
                    summary="Balanced autonomy level requires approval for HIGH and CRITICAL risk actions.",
                )
                return PolicyDecision(
                    id=decision_id,
                    action=tool_id,
                    category=category,
                    result=PolicyDecisionResult.APPROVAL_REQUIRED,
                    risk_level=risk,
                    policy_id=policy.id,
                    reason=f"Risk level {risk.value} requires approval under Balanced autonomy.",
                    explanation=explanation,
                )

        if level == AutonomyLevel.HIGH_AUTONOMY:
            if risk == RiskLevel.CRITICAL:
                matched_rules.append("HIGH_AUTONOMY_CRITICAL_RISK")
                explanation = PolicyExplanation(
                    decision=PolicyDecisionResult.APPROVAL_REQUIRED,
                    risk_level=risk,
                    matched_rules=matched_rules,
                    required_approval_scope=f"Authorize critical action '{tool_id}'",
                    summary="High autonomy level requires approval only for CRITICAL risk actions.",
                )
                return PolicyDecision(
                    id=decision_id,
                    action=tool_id,
                    category=category,
                    result=PolicyDecisionResult.APPROVAL_REQUIRED,
                    risk_level=risk,
                    policy_id=policy.id,
                    reason="Critical risk requires approval under High Autonomy.",
                    explanation=explanation,
                )

        # 5. Default Allow
        matched_rules.append("POLICY_ALLOW_DEFAULT")
        explanation = PolicyExplanation(
            decision=PolicyDecisionResult.ALLOW,
            risk_level=risk,
            matched_rules=matched_rules,
            summary=f"Action '{tool_id}' is permitted under autonomy level '{level.value}'.",
        )
        return PolicyDecision(
            id=decision_id,
            action=tool_id,
            category=category,
            result=PolicyDecisionResult.ALLOW,
            risk_level=risk,
            policy_id=policy.id,
            reason=f"Action permitted under {level.value} level.",
            explanation=explanation,
        )
