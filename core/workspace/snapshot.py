from __future__ import annotations

from dataclasses import asdict, dataclass, field
import hashlib
import json
import logging
import os
from pathlib import Path
import time
from typing import Any, Dict, List, Optional, Set, Tuple
import uuid

from core.workspace.filesystem import ControlledWorkspaceFS
from core.workspace.scanner import RepositoryScanner, RepositoryIndex

logger = logging.getLogger("AutonomOS.SnapshotEngine")


@dataclass
class SnapshotFileEntry:
    """Deterministic snapshot record for a single file."""
    path: str
    hash: str
    size: int
    mtime: float
    category: str = "source"

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> SnapshotFileEntry:
        return cls(
            path=data["path"],
            hash=data.get("hash", ""),
            size=data.get("size", 0),
            mtime=data.get("mtime", 0.0),
            category=data.get("category", "source"),
        )


@dataclass
class ProjectSnapshot:
    """
    Persistent snapshot containing deterministic SHA-256 hashes and metadata
    representing the workspace state at a point in time.
    """
    snapshot_id: str
    timestamp: str
    workspace_root: str
    project_map_version: str
    file_count: int
    files: Dict[str, SnapshotFileEntry] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "snapshot_id": self.snapshot_id,
            "timestamp": self.timestamp,
            "workspace_root": self.workspace_root,
            "project_map_version": self.project_map_version,
            "file_count": self.file_count,
            "files": {path: entry.to_dict() for path, entry in self.files.items()},
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> ProjectSnapshot:
        files_dict = {
            path: SnapshotFileEntry.from_dict(fentry if "path" in fentry else {"path": path, **fentry})
            for path, fentry in data.get("files", {}).items()
        }
        return cls(
            snapshot_id=data.get("snapshot_id", f"snap-{uuid.uuid4().hex[:8]}"),
            timestamp=data.get("timestamp", ""),
            workspace_root=data.get("workspace_root", ""),
            project_map_version=data.get("project_map_version", "1.0"),
            file_count=data.get("file_count", len(files_dict)),
            files=files_dict,
        )


@dataclass
class RenameOrMove:
    """Record of a reliably detected file rename or move."""
    from_path: str
    to_path: str
    hash: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "from": self.from_path,
            "to": self.to_path,
            "hash": self.hash,
        }


@dataclass
class WorkspaceChanges:
    """
    Structured diff between current workspace and the last known snapshot.
    Consumed by Manager for deterministic change comprehension without LLM overhead.
    """
    has_changes: bool
    added: List[str] = field(default_factory=list)
    modified: List[str] = field(default_factory=list)
    deleted: List[str] = field(default_factory=list)
    renamed: List[Dict[str, str]] = field(default_factory=list)
    unchanged_count: int = 0
    total_current_files: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "has_changes": self.has_changes,
            "added": sorted(self.added),
            "modified": sorted(self.modified),
            "deleted": sorted(self.deleted),
            "renamed": self.renamed,
            "unchanged_count": self.unchanged_count,
            "total_current_files": self.total_current_files,
        }


class SnapshotEngine:
    """
    Manages persistent workspace snapshots and deterministic change detection.
    Treats the actual filesystem as the source of truth.
    """

    def __init__(self, fs: ControlledWorkspaceFS, scanner: Optional[RepositoryScanner] = None):
        self.fs = fs
        self.scanner = scanner or RepositoryScanner(fs)
        self.meta_dir = self.fs.meta_dir
        self.snapshot_file = self.meta_dir / "last_snapshot.json"
        self.snapshots_history_dir = self.meta_dir / "snapshots"

    def capture_current_snapshot(self, project_map_version: str = "1.0") -> ProjectSnapshot:
        """
        Captures the current state of the workspace with SHA-256 hashes and metadata.
        """
        index: RepositoryIndex = self.scanner.scan_repository()
        now_iso = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        snapshot_id = f"snap-{uuid.uuid4().hex[:10]}"

        files_entries: Dict[str, SnapshotFileEntry] = {}
        for path, meta in index.files.items():
            files_entries[path] = SnapshotFileEntry(
                path=path,
                hash=meta.hash,
                size=meta.size,
                mtime=meta.mtime,
                category=meta.category,
            )

        return ProjectSnapshot(
            snapshot_id=snapshot_id,
            timestamp=now_iso,
            workspace_root=str(self.fs.workspace_root),
            project_map_version=project_map_version,
            file_count=len(files_entries),
            files=files_entries,
        )

    def load_last_snapshot(self) -> Optional[ProjectSnapshot]:
        """Loads the most recent persistent snapshot from disk."""
        if not self.snapshot_file.exists():
            return None

        try:
            with open(self.snapshot_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            return ProjectSnapshot.from_dict(data)
        except Exception as e:
            logger.warning(f"Failed to load snapshot from {self.snapshot_file}: {e}")
            return None

    def save_snapshot(self, snapshot: ProjectSnapshot) -> Path:
        """Persists the snapshot to last_snapshot.json and snapshot archive."""
        self.meta_dir.mkdir(parents=True, exist_ok=True)

        data = snapshot.to_dict()

        # 1. Save primary last_snapshot.json
        with open(self.snapshot_file, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)

        # 2. Archive to historical snapshots directory
        try:
            self.snapshots_history_dir.mkdir(parents=True, exist_ok=True)
            archive_path = self.snapshots_history_dir / f"{snapshot.snapshot_id}.json"
            with open(archive_path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
        except Exception:
            pass

        return self.snapshot_file

    def detect_changes(
        self,
        current_snapshot: Optional[ProjectSnapshot] = None,
        last_snapshot: Optional[ProjectSnapshot] = None,
    ) -> WorkspaceChanges:
        """
        Deterministically computes differences between CURRENT WORKSPACE and LAST SNAPSHOT.
        Detects added, modified, deleted, and renamed/moved files.
        """
        current = current_snapshot or self.capture_current_snapshot()
        last = last_snapshot or self.load_last_snapshot()

        # If no previous snapshot exists, all current files are treated as added
        if last is None:
            return WorkspaceChanges(
                has_changes=len(current.files) > 0,
                added=sorted(list(current.files.keys())),
                modified=[],
                deleted=[],
                renamed=[],
                unchanged_count=0,
                total_current_files=len(current.files),
            )

        current_paths = set(current.files.keys())
        last_paths = set(last.files.keys())

        raw_added = current_paths - last_paths
        raw_deleted = last_paths - current_paths
        common_paths = current_paths & last_paths

        modified_files: List[str] = []
        unchanged_count = 0

        # Check modified files among common paths
        for path in common_paths:
            cur_entry = current.files[path]
            last_entry = last.files[path]

            # Compare SHA-256 hash
            if cur_entry.hash and last_entry.hash:
                if cur_entry.hash != last_entry.hash:
                    modified_files.append(path)
                else:
                    unchanged_count += 1
            else:
                # Fallback to size and mtime
                if cur_entry.size != last_entry.size or abs(cur_entry.mtime - last_entry.mtime) > 0.001:
                    modified_files.append(path)
                else:
                    unchanged_count += 1

        # Detect Renamed & Moved files by matching identical SHA-256 hashes
        renamed_records: List[Dict[str, str]] = []
        final_added: List[str] = []
        final_deleted: Set[str] = set(raw_deleted)

        # Map deleted file hashes -> paths
        deleted_by_hash: Dict[str, List[str]] = {}
        for dpath in raw_deleted:
            dhash = last.files[dpath].hash
            if dhash:
                deleted_by_hash.setdefault(dhash, []).append(dpath)

        for apath in sorted(raw_added):
            ahash = current.files[apath].hash
            if ahash and ahash in deleted_by_hash and len(deleted_by_hash[ahash]) > 0:
                # Matched exact hash: this is a rename/move
                matched_old_path = deleted_by_hash[ahash].pop(0)
                final_deleted.remove(matched_old_path)
                renamed_records.append({
                    "from": matched_old_path,
                    "to": apath,
                    "hash": ahash,
                })
            else:
                final_added.append(apath)

        has_changes = bool(final_added or modified_files or final_deleted or renamed_records)

        return WorkspaceChanges(
            has_changes=has_changes,
            added=sorted(final_added),
            modified=sorted(modified_files),
            deleted=sorted(list(final_deleted)),
            renamed=renamed_records,
            unchanged_count=unchanged_count,
            total_current_files=len(current.files),
        )

    def sync_snapshot(self, project_map_version: str = "1.0") -> Tuple[ProjectSnapshot, WorkspaceChanges]:
        """
        Detects changes against last snapshot, saves fresh snapshot, and returns both.
        """
        last = self.load_last_snapshot()
        current = self.capture_current_snapshot(project_map_version=project_map_version)
        changes = self.detect_changes(current_snapshot=current, last_snapshot=last)
        self.save_snapshot(current)
        return current, changes
