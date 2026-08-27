from __future__ import annotations

import fnmatch
import os
from pathlib import Path
from typing import Optional

from workers.programmer.model import ProgrammingScope


class ScopeGuard:
    """
    Validates filesystem operations against authorized task scopes,
    preventing path traversal, system file modification, and unauthorized directory churn.
    """

    SYSTEM_PROTECTED_PREFIXES = (
        "/etc", "/usr", "/bin", "/sbin", "/var", "/dev", "/proc", "/sys", "/tmp",
        "c:\\windows", "c:\\program files", "/system", "/library",
    )

    @classmethod
    def is_system_path(cls, path: str) -> bool:
        """Check whether a path targets an absolute operating system location outside projects."""
        clean = (path or "").strip().lower()
        if not clean:
            return False
        return any(clean == p or clean.startswith(f"{p}/") or clean.startswith(f"{p}\\") for p in cls.SYSTEM_PROTECTED_PREFIXES)

    @classmethod
    def normalize_relative_path(cls, path: str) -> str:
        """Normalize a relative path within project workspace."""
        if not path:
            return ""
        clean = path.strip().replace("\\", "/")
        # Strip leading slashes
        clean = clean.lstrip("/")
        return os.path.normpath(clean).replace("\\", "/")

    @classmethod
    def validate_path(cls, path: str, scope: ProgrammingScope) -> tuple[bool, str]:
        """
        Validate whether a file path is permitted by the task's scope rules.
        """
        if not path:
            return False, "Empty path provided"

        raw_path = path.strip().replace("\\", "/")

        # 1. Block absolute system paths
        if cls.is_system_path(raw_path):
            return False, f"Access to system path '{path}' is strictly prohibited"

        norm = cls.normalize_relative_path(raw_path)

        # 2. Block path traversal escapes
        if norm.startswith("..") or "/../" in raw_path:
            return False, f"Path traversal attempt detected in '{path}'"

        # 3. Block protected infrastructure paths
        for exc in scope.excluded_paths:
            clean_exc = cls.normalize_relative_path(exc)
            if norm == clean_exc or norm.startswith(f"{clean_exc}/") or fnmatch.fnmatch(norm, exc):
                return False, f"Path '{norm}' matches excluded scope rule '{exc}'"

        # 4. If allowed_paths specified, ensure path matches at least one rule
        if scope.allowed_paths:
            matched = False
            for allow in scope.allowed_paths:
                clean_allow = cls.normalize_relative_path(allow)
                if norm == clean_allow or norm.startswith(f"{clean_allow}/") or fnmatch.fnmatch(norm, allow):
                    matched = True
                    break

            if not matched:
                return False, f"Path '{norm}' is outside allowed task scope: {scope.allowed_paths}"

        return True, "Path is within authorized scope"

    @classmethod
    def validate_change_count(cls, count: int, scope: ProgrammingScope) -> tuple[bool, str]:
        """Check that the number of files changed remains within configured bounds."""
        if count > scope.max_files_modified:
            return False, f"Modified files count ({count}) exceeds allowed limit of {scope.max_files_modified}"
        return True, "File change count within limits"
