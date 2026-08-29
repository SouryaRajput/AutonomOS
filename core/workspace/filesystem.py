from __future__ import annotations

import hashlib
import json
import logging
import os
from pathlib import Path
import shutil
import time
from typing import Any, Dict, List, Optional, Set, Tuple
import uuid

from core.errors import AutonomOSError

logger = logging.getLogger("AutonomOS.WorkspaceFS")


class WorkspaceSecurityError(AutonomOSError):
    """Raised when an operation attempts to escape or violate workspace confinement."""

    def __init__(self, message: str, details: Optional[Dict[str, Any]] = None):
        super().__init__(message=message, code="WORKSPACE_CONFINEMENT_VIOLATION", details=details)


class ControlledWorkspaceFS:
    """
    Security-enforced, confined filesystem runtime for AutonomOS workspaces.
    Guarantees that all read, write, move, rename, and delete operations remain
    strictly confined within the active workspace root.
    """

    PROTECTED_ROOT_PATHS = {".git"}

    def __init__(self, workspace_root: str | Path):
        self.workspace_root = Path(workspace_root).resolve()
        if not self.workspace_root.exists():
            try:
                self.workspace_root.mkdir(parents=True, exist_ok=True)
            except Exception as e:
                raise WorkspaceSecurityError(f"Cannot initialize workspace directory '{workspace_root}': {e}")

        # Metadata directory
        self.meta_dir = self.workspace_root / ".autonomos"
        self.meta_dir.mkdir(parents=True, exist_ok=True)

        self._audit_log_path = self.meta_dir / "fs_audit.jsonl"

    def resolve_safe_path(self, rel_path: str | Path) -> Path:
        """
        Resolves and validates that the target path is strictly within workspace_root.
        Prevents path traversal attacks (.. or symlink escapes).
        """
        if not rel_path:
            return self.workspace_root

        raw_str = str(rel_path).strip()
        # Disallow empty or null byte paths
        if "\0" in raw_str:
            raise WorkspaceSecurityError("Null bytes in path are forbidden.")

        # If absolute path is given, ensure it starts with workspace_root
        p = Path(raw_str)
        if p.is_absolute():
            resolved = p.resolve()
        else:
            resolved = (self.workspace_root / p).resolve()

        # Confinement check
        try:
            resolved.relative_to(self.workspace_root)
        except ValueError:
            raise WorkspaceSecurityError(
                f"Path traversal detected! Path '{rel_path}' resolves to '{resolved}', "
                f"which is outside workspace '{self.workspace_root}'."
            )

        return resolved

    def _record_audit(self, action: str, path: Path, status: str, details: Optional[Dict[str, Any]] = None):
        """Append filesystem operations to local security audit log."""
        try:
            rel = str(path.relative_to(self.workspace_root)) if path != self.workspace_root else "."
            entry = {
                "id": f"fs-{uuid.uuid4().hex[:8]}",
                "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "action": action,
                "path": rel,
                "status": status,
                "details": details or {},
            }
            with open(self._audit_log_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry) + "\n")
        except Exception as e:
            logger.warning(f"Failed to record FS audit entry: {e}")

    # --- Read Operations ---

    def exists(self, rel_path: str | Path) -> bool:
        target = self.resolve_safe_path(rel_path)
        return target.exists()

    def is_file(self, rel_path: str | Path) -> bool:
        target = self.resolve_safe_path(rel_path)
        return target.is_file()

    def is_dir(self, rel_path: str | Path) -> bool:
        target = self.resolve_safe_path(rel_path)
        return target.is_dir()

    def read_file(self, rel_path: str | Path, encoding: str = "utf-8", max_bytes: Optional[int] = None) -> str:
        target = self.resolve_safe_path(rel_path)
        if not target.exists():
            raise FileNotFoundError(f"File '{rel_path}' does not exist.")
        if target.is_dir():
            raise IsADirectoryError(f"Path '{rel_path}' is a directory, not a file.")

        with open(target, "r", encoding=encoding, errors="replace") as f:
            if max_bytes:
                content = f.read(max_bytes)
            else:
                content = f.read()

        self._record_audit("read_file", target, "SUCCESS", {"bytes": len(content)})
        return content

    def read_bytes(self, rel_path: str | Path) -> bytes:
        target = self.resolve_safe_path(rel_path)
        if not target.exists():
            raise FileNotFoundError(f"File '{rel_path}' does not exist.")
        return target.read_bytes()

    def compute_file_hash(self, rel_path: str | Path) -> str:
        target = self.resolve_safe_path(rel_path)
        if not target.exists() or target.is_dir():
            return ""
        hasher = hashlib.sha256()
        with open(target, "rb") as f:
            for chunk in iter(lambda: f.read(65536), b""):
                hasher.update(chunk)
        return hasher.hexdigest()

    def stat_file(self, rel_path: str | Path) -> Dict[str, Any]:
        target = self.resolve_safe_path(rel_path)
        if not target.exists():
            raise FileNotFoundError(f"Path '{rel_path}' does not exist.")
        st = target.stat()
        return {
            "path": str(target.relative_to(self.workspace_root)),
            "size": st.st_size,
            "mtime": st.st_mtime,
            "ctime": st.st_ctime,
            "is_dir": target.is_dir(),
            "is_file": target.is_file(),
        }

    def list_directory(self, rel_path: str | Path = "", recursive: bool = False) -> List[Dict[str, Any]]:
        target = self.resolve_safe_path(rel_path)
        if not target.exists():
            raise FileNotFoundError(f"Directory '{rel_path}' does not exist.")
        if not target.is_dir():
            raise NotADirectoryError(f"Path '{rel_path}' is not a directory.")

        results = []
        if recursive:
            for root, dirs, files in os.walk(target):
                root_p = Path(root)
                # Ignore .git and .autonomos/snapshots in standard listings
                dirs[:] = [d for d in dirs if d != ".git"]
                for d in dirs:
                    dp = root_p / d
                    results.append({
                        "path": str(dp.relative_to(self.workspace_root)),
                        "name": d,
                        "is_dir": True,
                    })
                for f in files:
                    fp = root_p / f
                    results.append({
                        "path": str(fp.relative_to(self.workspace_root)),
                        "name": f,
                        "is_dir": False,
                        "size": fp.stat().st_size if fp.exists() else 0,
                    })
        else:
            for entry in target.iterdir():
                if entry.name == ".git":
                    continue
                results.append({
                    "path": str(entry.relative_to(self.workspace_root)),
                    "name": entry.name,
                    "is_dir": entry.is_dir(),
                    "size": entry.stat().st_size if entry.is_file() else 0,
                })

        return sorted(results, key=lambda x: (not x["is_dir"], x["path"]))

    # --- Write / Mutation Operations ---

    def create_file(self, rel_path: str | Path, content: str = "", encoding: str = "utf-8") -> Path:
        target = self.resolve_safe_path(rel_path)
        if target.exists():
            raise FileExistsError(f"File '{rel_path}' already exists. Use overwrite_file instead.")

        target.parent.mkdir(parents=True, exist_ok=True)
        with open(target, "w", encoding=encoding) as f:
            f.write(content)

        self._record_audit("create_file", target, "SUCCESS", {"bytes": len(content)})
        return target

    def overwrite_file(self, rel_path: str | Path, content: str, encoding: str = "utf-8") -> Path:
        target = self.resolve_safe_path(rel_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        with open(target, "w", encoding=encoding) as f:
            f.write(content)

        self._record_audit("overwrite_file", target, "SUCCESS", {"bytes": len(content)})
        return target

    def edit_file(self, rel_path: str | Path, old_content: str, new_content: str, encoding: str = "utf-8") -> bool:
        target = self.resolve_safe_path(rel_path)
        if not target.exists():
            raise FileNotFoundError(f"File '{rel_path}' not found.")

        current = target.read_text(encoding=encoding)
        if old_content not in current:
            raise ValueError(f"Target snippet to replace was not found in '{rel_path}'.")

        updated = current.replace(old_content, new_content, 1)
        target.write_text(updated, encoding=encoding)

        self._record_audit("edit_file", target, "SUCCESS", {"replaced_length": len(old_content)})
        return True

    def create_directory(self, rel_path: str | Path) -> Path:
        target = self.resolve_safe_path(rel_path)
        target.mkdir(parents=True, exist_ok=True)
        self._record_audit("create_directory", target, "SUCCESS")
        return target

    def rename_or_move(self, src_rel: str | Path, dest_rel: str | Path) -> Path:
        src = self.resolve_safe_path(src_rel)
        dest = self.resolve_safe_path(dest_rel)

        if not src.exists():
            raise FileNotFoundError(f"Source path '{src_rel}' does not exist.")
        if dest.exists():
            raise FileExistsError(f"Destination path '{dest_rel}' already exists.")

        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(src), str(dest))

        self._record_audit("rename_or_move", dest, "SUCCESS", {"from": str(src_rel), "to": str(dest_rel)})
        return dest

    def rename_file(self, src_rel: str | Path, dest_rel: str | Path) -> Path:
        src = self.resolve_safe_path(src_rel)
        if not src.is_file():
            raise FileNotFoundError(f"Source file '{src_rel}' does not exist or is not a file.")
        return self.rename_or_move(src_rel, dest_rel)

    def move_file(self, src_rel: str | Path, dest_rel: str | Path) -> Path:
        src = self.resolve_safe_path(src_rel)
        if not src.is_file():
            raise FileNotFoundError(f"Source file '{src_rel}' does not exist or is not a file.")
        return self.rename_or_move(src_rel, dest_rel)

    def rename_directory(self, src_rel: str | Path, dest_rel: str | Path) -> Path:
        src = self.resolve_safe_path(src_rel)
        if not src.is_dir():
            raise NotADirectoryError(f"Source directory '{src_rel}' does not exist or is not a directory.")
        return self.rename_or_move(src_rel, dest_rel)

    def move_directory(self, src_rel: str | Path, dest_rel: str | Path) -> Path:
        src = self.resolve_safe_path(src_rel)
        if not src.is_dir():
            raise NotADirectoryError(f"Source directory '{src_rel}' does not exist or is not a directory.")
        return self.rename_or_move(src_rel, dest_rel)

    def delete_file(self, rel_path: str | Path) -> bool:
        target = self.resolve_safe_path(rel_path)
        if not target.exists():
            raise FileNotFoundError(f"File '{rel_path}' not found.")
        if target.is_dir():
            raise IsADirectoryError(f"Path '{rel_path}' is a directory. Use delete_directory.")

        rel_str = str(target.relative_to(self.workspace_root))
        if rel_str in self.PROTECTED_ROOT_PATHS:
            raise WorkspaceSecurityError(f"Cannot delete protected path '{rel_str}'.")

        target.unlink()
        self._record_audit("delete_file", target, "SUCCESS")
        return True

    def delete_directory(self, rel_path: str | Path, recursive: bool = False) -> bool:
        target = self.resolve_safe_path(rel_path)
        if not target.exists():
            raise FileNotFoundError(f"Directory '{rel_path}' not found.")
        if not target.is_dir():
            raise NotADirectoryError(f"Path '{rel_path}' is a file, not a directory.")

        rel_str = str(target.relative_to(self.workspace_root))
        if rel_str in self.PROTECTED_ROOT_PATHS or target == self.workspace_root:
            raise WorkspaceSecurityError(f"Cannot delete protected root path '{rel_str}'.")

        if recursive:
            shutil.rmtree(target)
        else:
            target.rmdir()

        self._record_audit("delete_directory", target, "SUCCESS", {"recursive": recursive})
        return True
