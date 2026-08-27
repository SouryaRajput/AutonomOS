from __future__ import annotations

from pathlib import Path
import subprocess
from typing import Optional

from core.models import Task
from core.safety.checkpoint import compute_file_checksum
from core.safety.model import ChangeRecord, Checkpoint, SafetyConfig, ScopeDeviation
from core.safety.types import CheckpointType, ScopeDeviationType


class ScopeTracker:
    """
    Tracks modifications and detects scope deviations when worker actions exceed
    explicit task boundaries or violate protection rules.
    """

    @classmethod
    def get_changed_files(cls, checkpoint: Checkpoint, project_root: str) -> list[ChangeRecord]:
        """Calculate list of files created, modified, or deleted since checkpoint."""
        root_path = Path(project_root).resolve()
        state_ref = checkpoint.state_reference
        changes: list[ChangeRecord] = []

        if checkpoint.checkpoint_type == CheckpointType.FILESYSTEM_SNAPSHOT:
            snapshot_map: dict[str, str] = state_ref.get("snapshot_map", {})
            current_files: dict[str, str] = {}

            for p in root_path.rglob("*"):
                if not p.is_file():
                    continue
                rel_str = str(p.relative_to(root_path))
                if rel_str.startswith(".autonomos") or rel_str.startswith(".git"):
                    continue
                current_files[rel_str] = compute_file_checksum(p)

            # Detect Created and Modified
            for rel_str, curr_chk in current_files.items():
                if rel_str not in snapshot_map:
                    changes.append(ChangeRecord(path=rel_str, change_type="created", after_hash=curr_chk))
                elif snapshot_map[rel_str] != curr_chk:
                    changes.append(ChangeRecord(path=rel_str, change_type="modified", before_hash=snapshot_map[rel_str], after_hash=curr_chk))

            # Detect Deleted
            for rel_str, orig_chk in snapshot_map.items():
                if rel_str not in current_files:
                    changes.append(ChangeRecord(path=rel_str, change_type="deleted", before_hash=orig_chk))

        elif checkpoint.checkpoint_type in (CheckpointType.GIT_WORKING_TREE, CheckpointType.GIT_COMMIT):
            try:
                res = subprocess.run(
                    ["git", "status", "--porcelain"],
                    cwd=str(root_path),
                    capture_output=True,
                    text=True,
                    check=False,
                )
                dirty_user_files = state_ref.get("dirty_user_files", {})
                if res.returncode == 0 and res.stdout.strip():
                    for line in res.stdout.splitlines():
                        code = line[:2].strip()
                        rel_str = line[3:].strip()
                        if rel_str.startswith(".autonomos"):
                            continue

                        # If this file was already dirty before task and checksum hasn't changed, skip
                        full_p = root_path / rel_str
                        if rel_str in dirty_user_files and full_p.is_file() and compute_file_checksum(full_p) == dirty_user_files[rel_str]:
                            continue

                        if code == "??" or code == "A":
                            changes.append(ChangeRecord(path=rel_str, change_type="created"))
                        elif code == "D":
                            changes.append(ChangeRecord(path=rel_str, change_type="deleted"))
                        else:
                            changes.append(ChangeRecord(path=rel_str, change_type="modified"))
            except Exception:
                pass

        return changes

    @classmethod
    def check_deviations(
        cls,
        checkpoint: Checkpoint,
        project_root: str,
        task: Task,
        config: Optional[SafetyConfig] = None,
    ) -> list[ScopeDeviation]:
        """
        Evaluate actual changes against task scope references and safety constraints.
        """
        cfg = config or SafetyConfig()
        deviations: list[ScopeDeviation] = []
        changes = cls.get_changed_files(checkpoint, project_root)

        # 1. Total changed files limit
        if len(changes) > cfg.max_changed_files:
            deviations.append(
                ScopeDeviation(
                    deviation_type=ScopeDeviationType.MAX_FILES_EXCEEDED,
                    target_path="*",
                    message=f"Total changed files ({len(changes)}) exceeds safety threshold ({cfg.max_changed_files}).",
                    details={"count": len(changes), "limit": cfg.max_changed_files},
                )
            )

        # 2. Extract allowed task paths
        allowed_paths: list[str] = []
        for ref in task.context_references:
            if isinstance(ref, dict):
                p = ref.get("path") or ref.get("target") or ref.get("file")
                if p:
                    allowed_paths.append(str(p).strip().lstrip("/\\"))
            elif isinstance(ref, str):
                allowed_paths.append(ref.strip().lstrip("/\\"))

        for change in changes:
            rel_clean = change.path.strip().lstrip("/\\")

            # Check protected paths
            for prot in cfg.protected_paths:
                prot_clean = prot.strip().lstrip("/\\")
                if rel_clean == prot_clean or rel_clean.startswith(f"{prot_clean}/") or rel_clean.startswith(f"{prot_clean}\\"):
                    deviations.append(
                        ScopeDeviation(
                            deviation_type=ScopeDeviationType.PROTECTED_PATH_MODIFIED,
                            target_path=change.path,
                            message=f"Protected path '{change.path}' was modified by worker.",
                            details={"change_type": change.change_type, "protected_rule": prot},
                        )
                    )

            # Check task scope confinement if task specified explicit references
            if allowed_paths and cfg.strict_scope_enforcement:
                is_scoped = any(
                    rel_clean == ap or rel_clean.startswith(f"{ap}/") or ap.startswith(f"{rel_clean}/")
                    for ap in allowed_paths
                )
                if not is_scoped:
                    dev_type = (
                        ScopeDeviationType.UNEXPECTED_FILE_DELETED
                        if change.change_type == "deleted"
                        else ScopeDeviationType.UNEXPECTED_FILE_MODIFIED
                    )
                    deviations.append(
                        ScopeDeviation(
                            deviation_type=dev_type,
                            target_path=change.path,
                            message=f"Modified file '{change.path}' is outside declared task scope ({allowed_paths}).",
                            details={"change_type": change.change_type, "allowed_paths": allowed_paths},
                        )
                    )

        return deviations
