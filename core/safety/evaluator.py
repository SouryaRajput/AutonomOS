from __future__ import annotations

import os
from pathlib import Path
import re
from typing import TYPE_CHECKING, Any, Optional

from core.enums import RiskLevel
from core.models import Project, Task, WorkerManifest
from core.safety.model import SafetyConfig, SafetyDecision
from core.safety.types import SafetyAction

if TYPE_CHECKING:
    from core.tools.model import ToolDefinition, ToolRequest


class SafetyEvaluator:
    """
    Deterministic Safety & Contextual Risk Evaluation Engine.
    Evaluates requested tool operations in the context of the project, task, worker permissions,
    target paths, protected resources, and potential destructive consequences.
    """

    DANGEROUS_SHELL_PATTERNS = [
        r"\brm\s+-[rRfF]+\s+(/|\*|~|\.\.)",
        r"\bmkfs\b",
        r"\bdd\s+if=",
        r"\bshutdown\b",
        r"\breboot\b",
        r"\bkillall\b",
        r"\bgit\s+reset\s+--hard\b",
        r"\bgit\s+clean\s+-[fdx]+\b",
        r"\bgit\s+push\s+--force\b",
        r"\bchmod\s+-R\s+777\b",
    ]

    @classmethod
    def evaluate_request(
        cls,
        request: ToolRequest,
        tool_def: ToolDefinition,
        project: Project,
        worker: WorkerManifest,
        task: Task,
        config: Optional[SafetyConfig] = None,
    ) -> SafetyDecision:
        """
        Evaluate a ToolRequest and return a deterministic SafetyDecision.
        """
        cfg = config or SafetyConfig()
        reasons: list[str] = []
        target_path_str = str(request.arguments.get("path") or request.arguments.get("image_path") or "")
        clean_path = target_path_str.strip().lstrip("/\\")

        # 0. Workspace Boundary Escape Check (Path Traversal Protection)
        if target_path_str and project and project.root_path:
            root_resolved = Path(project.root_path).resolve()
            try:
                target_resolved = (root_resolved / target_path_str).resolve()
                if not (target_resolved == root_resolved or target_resolved.is_relative_to(root_resolved)):
                    reasons.append(f"Target path '{target_path_str}' escapes workspace boundary ('{project.root_path}'). Operation denied.")
                    return SafetyDecision(
                        decision=SafetyAction.DENY,
                        risk_level=RiskLevel.CRITICAL,
                        reasons=reasons,
                        required_approval=False,
                        metadata={"target_path": target_path_str, "project_root": project.root_path},
                    )
            except Exception as path_err:
                reasons.append(f"Invalid target path structure '{target_path_str}': {path_err}")
                return SafetyDecision(
                    decision=SafetyAction.DENY,
                    risk_level=RiskLevel.CRITICAL,
                    reasons=reasons,
                    required_approval=False,
                    metadata={"target_path": target_path_str, "error": str(path_err)},
                )

        # 1. Protected Paths Check (Critical Risk)
        for protected in cfg.protected_paths:
            clean_prot = protected.strip().lstrip("/\\")
            if clean_path and (clean_path == clean_prot or clean_path.startswith(f"{clean_prot}/") or clean_path.startswith(f"{clean_prot}\\")):
                if "delete" in tool_def.id or "write" in tool_def.id:
                    reasons.append(f"Target path '{clean_path}' is a protected resource ('{protected}'). Destructive modification is restricted.")
                    return SafetyDecision(
                        decision=SafetyAction.REQUIRES_APPROVAL if cfg.allow_high_risk else SafetyAction.DENY,
                        risk_level=RiskLevel.CRITICAL,
                        reasons=reasons,
                        required_approval=True,
                        metadata={"target_path": clean_path, "protected_rule": protected},
                    )

        # 2. Dangerous Shell Command Inspection
        if tool_def.id == "shell.execute":
            cmd = str(request.arguments.get("command") or " ".join(request.arguments.get("args") or []))
            for pattern in cls.DANGEROUS_SHELL_PATTERNS:
                if re.search(pattern, cmd, re.IGNORECASE):
                    reasons.append(f"Command '{cmd[:60]}' matches dangerous execution pattern '{pattern}'.")
                    return SafetyDecision(
                        decision=SafetyAction.REQUIRES_APPROVAL,
                        risk_level=RiskLevel.CRITICAL,
                        reasons=reasons,
                        required_approval=True,
                        metadata={"command": cmd, "matched_pattern": pattern},
                    )

        # 3. Contextual Risk Classification
        risk_level = tool_def.risk_level
        req_checkpoint = False

        if "delete" in tool_def.id or request.arguments.get("action") == "delete_file":
            risk_level = RiskLevel.HIGH
            req_checkpoint = True
            reasons.append("Destructive filesystem deletion operation detected (High Risk).")

        elif "write" in tool_def.id or request.arguments.get("action") in ("write_file", "create_file"):
            risk_level = RiskLevel.MEDIUM
            req_checkpoint = True
            reasons.append("Filesystem modification operation detected (Medium Risk).")

        elif tool_def.id == "shell.execute":
            risk_level = RiskLevel.HIGH
            req_checkpoint = True
            reasons.append("Shell execution capability carries environmental mutation potential (High Risk).")

        elif tool_def.id.startswith("git") and request.arguments.get("action") in ("commit", "add"):
            risk_level = RiskLevel.MEDIUM
            req_checkpoint = True
            reasons.append("Version control modification operation detected (Medium Risk).")

        else:
            risk_level = RiskLevel.LOW
            reasons.append(f"Read-only or idempotent inspection operation ({tool_def.id}).")

        # 4. Scope Execution Window Check
        allowed_paths: list[str] = []
        for ref in task.context_references:
            if isinstance(ref, dict):
                p = ref.get("path") or ref.get("target") or ref.get("file")
                if p:
                    allowed_paths.append(str(p).strip().lstrip("/\\"))
            elif isinstance(ref, str):
                allowed_paths.append(ref.strip().lstrip("/\\"))

        if clean_path and allowed_paths:
            is_in_declared_scope = any(
                clean_path == ap or clean_path.startswith(f"{ap}/") or ap.startswith(f"{clean_path}/")
                for ap in allowed_paths
            )
            if not is_in_declared_scope and ("write" in tool_def.id or "delete" in tool_def.id):
                reasons.append(f"Target path '{clean_path}' is outside explicitly declared task scope ({allowed_paths}).")
                if cfg.strict_scope_enforcement and risk_level == RiskLevel.HIGH:
                    risk_level = RiskLevel.CRITICAL
                    return SafetyDecision(
                        decision=SafetyAction.REQUIRES_APPROVAL,
                        risk_level=risk_level,
                        reasons=reasons,
                        required_approval=True,
                        allowed_paths=allowed_paths,
                    )

        # 5. Determine Final Decision
        if risk_level == RiskLevel.CRITICAL:
            decision = SafetyAction.REQUIRES_APPROVAL if cfg.allow_high_risk else SafetyAction.DENY
        elif risk_level in (RiskLevel.HIGH, RiskLevel.MEDIUM):
            decision = SafetyAction.ALLOW_WITH_CHECKPOINT if cfg.auto_checkpoint else SafetyAction.ALLOW
        else:
            decision = SafetyAction.ALLOW

        return SafetyDecision(
            decision=decision,
            risk_level=risk_level,
            reasons=reasons,
            required_checkpoint=req_checkpoint and cfg.auto_checkpoint,
            required_approval=risk_level == RiskLevel.CRITICAL,
            allowed_paths=allowed_paths,
            metadata={"tool_id": tool_def.id, "target_path": clean_path},
        )
