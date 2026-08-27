from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import threading
import time
from typing import Any, Optional
import uuid

from core.errors import ProjectNotFoundError
from core.safety.model import Checkpoint, utc_now
from core.safety.types import CheckpointStatus, CheckpointType
from core.storage.base import Store


def compute_file_checksum(path: Path) -> str:
    if not path.is_file():
        return ""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(65536):
            h.update(chunk)
    return h.hexdigest()


class CheckpointManager:
    """
    Manages deterministic project checkpoint creation, snapshot persistence,
    and pre-existing user work preservation.
    """

    def __init__(self, store: Store):
        self.store = store
        self._checkpoints: dict[str, Checkpoint] = {}
        self._lock = threading.RLock()

    def create_checkpoint(
        self,
        project_id: str,
        task_id: str,
        worker_id: str,
        checkpoint_id: Optional[str] = None,
    ) -> Checkpoint:
        """
        Capture a recoverable checkpoint of the project state before task execution.
        """
        with self._lock:
            project = self.store.get_project(project_id)
            if not project:
                raise ProjectNotFoundError(project_id)

            cid = checkpoint_id or f"chk-{uuid.uuid4().hex[:10]}"
            root_path = Path(project.root_path).resolve()
            checkpoint_dir = root_path / ".autonomos" / "checkpoints" / cid
            checkpoint_dir.mkdir(parents=True, exist_ok=True)

            is_git_repo = (root_path / ".git").is_dir()

            state_ref: dict[str, Any] = {}
            if is_git_repo:
                ckpt_type = CheckpointType.GIT_WORKING_TREE
                state_ref = self._capture_git_state(root_path, checkpoint_dir)
            else:
                ckpt_type = CheckpointType.FILESYSTEM_SNAPSHOT
                state_ref = self._capture_fs_state(root_path, checkpoint_dir)

            # Capture memory document versions
            memory_ref: dict[str, int] = {}
            memory_docs = self.store.list_memory_documents(project_id=project_id)
            for doc in memory_docs:
                memory_ref[doc.id] = doc.version

            checkpoint = Checkpoint(
                id=cid,
                project_id=project_id,
                task_id=task_id,
                worker_id=worker_id,
                checkpoint_type=ckpt_type,
                status=CheckpointStatus.ACTIVE,
                state_reference=state_ref,
                memory_reference=memory_ref,
                created_at=utc_now(),
                metadata={"is_git": is_git_repo, "checkpoint_dir": str(checkpoint_dir)},
            )

            # Persist metadata JSON in checkpoint directory
            meta_file = checkpoint_dir / "checkpoint.json"
            meta_file.write_text(json.dumps(checkpoint.to_dict(), indent=2))

            self._checkpoints[cid] = checkpoint
            return checkpoint

    def get_checkpoint(self, checkpoint_id: str) -> Optional[Checkpoint]:
        with self._lock:
            return self._checkpoints.get(checkpoint_id)

    def get_active_checkpoint_for_task(self, task_id: str) -> Optional[Checkpoint]:
        with self._lock:
            for ckpt in self._checkpoints.values():
                if ckpt.task_id == task_id and ckpt.status == CheckpointStatus.ACTIVE:
                    return ckpt
            return None

    def commit_checkpoint(self, checkpoint_id: str) -> None:
        with self._lock:
            ckpt = self._checkpoints.get(checkpoint_id)
            if ckpt and ckpt.status == CheckpointStatus.ACTIVE:
                ckpt.status = CheckpointStatus.COMMITTED
                ckpt.updated_at = utc_now()

    def _capture_git_state(self, root_path: Path, checkpoint_dir: Path) -> dict[str, Any]:
        """
        Capture Git commit reference and back up any pre-existing dirty/uncommitted user files.
        """
        head_commit = "UNCOMMITTED"
        try:
            res = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                cwd=str(root_path),
                capture_output=True,
                text=True,
                check=False,
            )
            if res.returncode == 0:
                head_commit = res.stdout.strip()
        except Exception:
            pass

        # Capture pre-existing dirty status
        dirty_user_files: dict[str, str] = {}
        backup_dir = checkpoint_dir / "user_dirty_backup"
        backup_dir.mkdir(parents=True, exist_ok=True)

        try:
            status_res = subprocess.run(
                ["git", "status", "--porcelain"],
                cwd=str(root_path),
                capture_output=True,
                text=True,
                check=False,
            )
            if status_res.returncode == 0 and status_res.stdout.strip():
                for line in status_res.stdout.splitlines():
                    rel_path_str = line[3:].strip()
                    # Skip .autonomos folder
                    if rel_path_str.startswith(".autonomos"):
                        continue
                    full_p = root_path / rel_path_str
                    if full_p.is_file():
                        chk = compute_file_checksum(full_p)
                        dirty_user_files[rel_path_str] = chk

                        # Copy backup of user file
                        dest = backup_dir / rel_path_str
                        dest.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copy2(full_p, dest)
        except Exception:
            pass

        return {
            "head_commit": head_commit,
            "dirty_user_files": dirty_user_files,
            "backup_dir": str(backup_dir),
        }

    def _capture_fs_state(self, root_path: Path, checkpoint_dir: Path) -> dict[str, Any]:
        """
        Capture complete filesystem snapshot and backup copies for non-Git projects.
        """
        snapshot_map: dict[str, str] = {}
        fs_backup_dir = checkpoint_dir / "fs_snapshot"
        fs_backup_dir.mkdir(parents=True, exist_ok=True)

        for p in root_path.rglob("*"):
            if not p.is_file():
                continue
            rel_str = str(p.relative_to(root_path))
            if rel_str.startswith(".autonomos") or rel_str.startswith(".git"):
                continue

            chk = compute_file_checksum(p)
            snapshot_map[rel_str] = chk

            dest = fs_backup_dir / rel_str
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(p, dest)

        return {
            "snapshot_map": snapshot_map,
            "backup_dir": str(fs_backup_dir),
        }
