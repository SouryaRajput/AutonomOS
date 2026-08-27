import os
from pathlib import Path
from typing import Optional

from core.errors import ToolPermissionDeniedError, ToolWorkspaceViolationError


class PermissionPolicy:
    """
    Security and authorization engine for the Tool Runtime.
    Enforces permission checks, workspace path confinement, and environment secret redaction.
    """

    SENSITIVE_ENV_PATTERNS = {
        "SECRET", "PASSWORD", "PASS", "TOKEN", "API_KEY", "APIKEY",
        "AUTH", "PRIVATE", "CREDENTIAL", "AWS_", "SSH_", "GEMINI_",
        "OPENAI_", "ANTHROPIC_", "GITHUB_TOKEN", "GH_TOKEN",
    }

    SAFE_ENV_VARS = {
        "PATH", "HOME", "USER", "LANG", "LC_ALL", "TMPDIR",
        "SHELL", "TERM", "PYTHONPATH", "SYSTEMROOT", "WINDIR",
    }

    @classmethod
    def evaluate_permissions(
        cls,
        worker_permissions: list[str],
        required_permissions: list[str],
        worker_id: str,
        tool_id: str,
    ) -> None:
        """
        Authorize worker permissions before tool invocation.
        Raises ToolPermissionDeniedError if unauthorized.
        """
        granted = set(worker_permissions)
        if "*" in granted or "all" in granted:
            return

        missing = []
        for req in required_permissions:
            matched = False
            if req in granted:
                matched = True
            else:
                for g in granted:
                    if g.endswith(".*") and req.startswith(g[:-2]):
                        matched = True
                        break
            if not matched:
                missing.append(req)

        if missing:
            raise ToolPermissionDeniedError(worker_id, tool_id, required_permissions)

    @classmethod
    def validate_path_confinement(cls, path_str: str, workspace_root: str) -> Path:
        """
        Validate and resolve a path to ensure it is strictly confined within the authorized workspace.
        Protects against directory traversal (../../) and absolute system escapes (/etc/...).
        Raises ToolWorkspaceViolationError on violation.
        """
        canonical_workspace = Path(workspace_root).resolve()
        
        target_path = Path(path_str)
        if not target_path.is_absolute():
            resolved_target = (canonical_workspace / target_path).resolve()
        else:
            resolved_target = target_path.resolve()

        # Enforce boundary: resolved_target must be equal to or a subpath of canonical_workspace
        try:
            resolved_target.relative_to(canonical_workspace)
        except ValueError:
            raise ToolWorkspaceViolationError(path_str, str(canonical_workspace))

        return resolved_target

    @classmethod
    def sanitize_environment(
        cls,
        custom_env: Optional[dict[str, str]] = None,
        include_host_safe: bool = True,
    ) -> dict[str, str]:
        """
        Construct a safe, sanitized environment dictionary for tool processes.
        Filters out sensitive API keys, secrets, tokens, and credentials.
        """
        env: dict[str, str] = {}

        if include_host_safe:
            for k, v in os.environ.items():
                k_upper = k.upper()
                # Check if matches sensitive pattern
                is_sensitive = any(pattern in k_upper for pattern in cls.SENSITIVE_ENV_PATTERNS)
                if not is_sensitive or k in cls.SAFE_ENV_VARS:
                    env[k] = v

        if custom_env:
            for k, v in custom_env.items():
                env[k] = str(v)

        return env
