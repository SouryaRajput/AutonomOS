from __future__ import annotations

from pathlib import Path
import shutil
import subprocess
import time
from typing import Any, Optional
import uuid

from core.memory.manager import MemoryManager
from core.safety.checkpoint import compute_file_checksum
from core.safety.model import Checkpoint, RollbackResult, utc_now
from core.safety.types import CheckpointStatus, CheckpointType, RollbackStatus
from core.storage.base import Store


class RollbackManager:
    """
    Executes deterministic scoped rollbacks, guarantees pre-existing user change preservation,
    restores memory documents, and performs post-recovery verification.
    """

    @classmethod
    def rollback(
        self,
        checkpoint: Checkpoint,
        project_root: str,
        store: Store,
        memory_manager: MemoryManager,
    ) -> RollbackResult:
        start_time = time.perf_counter()
        rid = f"rb-{uuid.uuid4().hex[:8]}"
        root_path = Path(project_root).resolve()

        if checkpoint.status == CheckpointStatus.ROLLED_BACK:
            return RollbackResult(
                rollback_id=rid,
                checkpoint_id=checkpoint.id,
                status=RollbackStatus.SUCCESS,
                error_message="Checkpoint has already been rolled back.",
                verified=True,
                duration_ms=(time.perf_counter() - start_time) * 1000.0,
            )

        restored_files: list[str] = []
        deleted_files: list[str] = []
        preserved_user_files: list[str] = []

        try:
            if checkpoint.checkpoint_type in (CheckpointType.GIT_WORKING_TREE, CheckpointType.GIT_COMMIT):
                self._rollback_git_workspace(
                    checkpoint=checkpoint,
                    root_path=root_path,
                    restored_files=restored_files,
                    deleted_files=deleted_files,
                    preserved_user_files=preserved_user_files,
                )
            else:
                self._rollback_fs_workspace(
                    checkpoint=checkpoint,
                    root_path=root_path,
                    restored_files=restored_files,
                    deleted_files=deleted_files,
                )

            # Rollback Memory Documents
            self._rollback_memory_documents(
                checkpoint=checkpoint,
                store=store,
                memory_manager=memory_manager,
            )

            # Post-Rollback Verification
            is_verified = self._verify_restoration(checkpoint, root_path)

            status = RollbackStatus.SUCCESS if is_verified else RollbackStatus.FAILED
            checkpoint.status = CheckpointStatus.ROLLED_BACK if is_verified else CheckpointStatus.ROLLBACK_FAILED
            checkpoint.updated_at = utc_now()

            return RollbackResult(
                rollback_id=rid,
                checkpoint_id=checkpoint.id,
                status=status,
                restored_files=restored_files,
                deleted_files=deleted_files,
                preserved_user_files=preserved_user_files,
                verified=is_verified,
                duration_ms=(time.perf_counter() - start_time) * 1000.0,
            )

        except Exception as e:
            checkpoint.status = CheckpointStatus.ROLLBACK_FAILED
            checkpoint.updated_at = utc_now()
            return RollbackResult(
                rollback_id=rid,
                checkpoint_id=checkpoint.id,
                status=RollbackStatus.FAILED,
                error_message=f"Rollback failed with exception: {str(e)}",
                verified=False,
                duration_ms=(time.perf_counter() - start_time) * 1000.0,
            )

    @classmethod
    def _rollback_git_workspace(
        cls,
        checkpoint: Checkpoint,
        root_path: Path,
        restored_files: list[str],
        deleted_files: list[str],
        preserved_user_files: list[str],
    ) -> None:
        state_ref = checkpoint.state_reference
        backup_dir = Path(state_ref.get("backup_dir", ""))
        dirty_user_files = state_ref.get("dirty_user_files", {})

        # Step 1: Revert all tracked git changes using checkout
        subprocess.run(
            ["git", "checkout", "--", "."],
            cwd=str(root_path),
            capture_output=True,
            check=False,
        )

        # Step 2: Restore pre-existing dirty user files from backup
        if backup_dir.exists():
            for rel_p, orig_chk in dirty_user_files.items():
                src_bak = backup_dir / rel_p
                target = root_path / rel_p
                if src_bak.exists():
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(src_bak, target)
                    preserved_user_files.append(rel_p)

        # Step 3: Remove untracked files created by the worker
        status_res = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=str(root_path),
            capture_output=True,
            text=True,
            check=False,
        )
        if status_res.returncode == 0 and status_res.stdout.strip():
            for line in status_res.stdout.splitlines():
                if line.startswith("??"):
                    rel_p = line[3:].strip()
                    if rel_p.startswith(".autonomos"):
                        continue
                    # If this file was NOT in pre-existing dirty user files, it was created by worker -> delete
                    if rel_p not in dirty_user_files:
                        full_p = root_path / rel_p
                        if full_p.is_file():
                            full_p.unlink()
                            deleted_files.append(rel_p)
                        elif full_p.is_dir():
                            shutil.rmtree(full_p, ignore_errors=True)
                            deleted_files.append(rel_p)

    @classmethod
    def _rollback_fs_workspace(
        cls,
        checkpoint: Checkpoint,
        root_path: Path,
        restored_files: list[str],
        deleted_files: list[str],
    ) -> None:
        state_ref = checkpoint.state_reference
        snapshot_map: dict[str, str] = state_ref.get("snapshot_map", {})
        backup_dir = Path(state_ref.get("backup_dir", ""))

        # 1. Delete files created during task (not in snapshot)
        for p in list(root_path.rglob("*")):
            if not p.is_file():
                continue
            rel_str = str(p.relative_to(root_path))
            if rel_str.startswith(".autonomos") or rel_str.startswith(".git"):
                continue

            if rel_str not in snapshot_map:
                p.unlink()
                deleted_files.append(rel_str)

        # 2. Restore modified/deleted files from backup directory
        if backup_dir.exists():
            for rel_str, orig_chk in snapshot_map.items():
                src_bak = backup_dir / rel_str
                dest = root_path / rel_str
                if src_bak.exists():
                    # Check if destination missing or modified
                    if not dest.exists() or compute_file_checksum(dest) != orig_chk:
                        dest.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copy2(src_bak, dest)
                        restored_files.append(rel_str)

    @classmethod
    def _rollback_memory_documents(
        cls,
        checkpoint: Checkpoint,
        store: Store,
        memory_manager: MemoryManager,
    ) -> None:
        memory_ref = checkpoint.memory_reference  # doc_id -> version
        current_docs = store.list_memory_documents(project_id=checkpoint.project_id)

        for doc in current_docs:
            if doc.id not in memory_ref:
                # Document was created during task -> delete it
                try:
                    memory_manager.delete_memory(checkpoint.project_id, doc.id)
                except Exception:
                    pass

    @classmethod
    def _verify_restoration(cls, checkpoint: Checkpoint, root_path: Path) -> bool:
        """
        Verify that restored workspace matches checkpoint state.
        """
        state_ref = checkpoint.state_reference
        if checkpoint.checkpoint_type == CheckpointType.FILESYSTEM_SNAPSHOT:
            snapshot_map: dict[str, str] = state_ref.get("snapshot_map", {})
            for rel_p, expected_chk in snapshot_map.items():
                full_p = root_path / rel_p
                if not full_p.exists() or compute_file_checksum(full_p) != expected_chk:
                    return False
            return True

        elif checkpoint.checkpoint_type in (CheckpointType.GIT_WORKING_TREE, CheckpointType.GIT_COMMIT):
            dirty_user_files = state_ref.get("dirty_user_files", {})
            for rel_p, expected_chk in dirty_user_files.items():
                full_p = root_path / rel_p
                if not full_p.exists() or compute_file_checksum(full_p) != expected_chk:
                    return False
            return True

        return True
