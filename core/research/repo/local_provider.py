"""
Local Filesystem / Fixture-backed Repository Provider (Phase 1 / Part 5 / Step 2).

Enables hermetic, fixture-driven repository data access against local file trees and test directories.
"""
from __future__ import annotations

import hashlib
import logging
import os
import posixpath
import re
import time
from typing import Any, Callable, Optional

from core.research.errors import (
    RepositoryCancelledError,
    RepositoryFileNotFoundError,
    RepositoryNotFoundError,
    RepositoryProviderError,
    RepositoryResourceLimitError,
    RepositoryRevisionNotFoundError,
    RepositorySecurityError,
    RepositoryTimeoutError,
    RepositoryValidationError,
)
from core.research.repo.models import (
    LineRange,
    RepoVersionCategory,
    RepoVersionContext,
    RepositoryDirectory,
    RepositoryFile,
    RepositoryIdentity,
    RepositoryProviderType,
    RepositoryRevision,
    RepositorySourceMaterial,
    RepositoryTree,
    compute_sha256,
    detect_file_language,
    is_known_binary_extension,
    normalize_repo_path,
)
from core.research.repo.provider import (
    RepositoryFetchLimits,
    RepositoryProvider,
)

logger = logging.getLogger("AutonomOS.Research.LocalRepositoryProvider")

IGNORED_NAMES = {
    ".git",
    ".hg",
    ".svn",
    ".DS_Store",
    "__pycache__",
    ".pytest_cache",
    "node_modules",
    ".venv",
    "venv",
    ".env",
}


class LocalRepositoryProvider(RepositoryProvider):
    """
    Local filesystem repository provider reading fixture folders and local directories.
    Provides bounded directory traversal, path traversal security, and line range slicing.
    """

    def __init__(
        self,
        provider_id: str = "local-repo-provider",
        name: str = "Local Filesystem Repository Provider",
        base_directory: Optional[str] = None,
        limits: Optional[RepositoryFetchLimits] = None,
    ):
        super().__init__(
            provider_id=provider_id,
            provider_type=RepositoryProviderType.LOCAL_GIT,
            name=name,
            limits=limits or RepositoryFetchLimits(),
        )
        self._base_directory = os.path.abspath(base_directory) if base_directory else None

    # -------------------------------------------------------------------------
    # Path & Security Resolution
    # -------------------------------------------------------------------------

    def _resolve_root_path(self, repo_target: RepositoryIdentity | str) -> str:
        """
        Resolve repository target to a canonical absolute local directory path.
        """
        raw_path: str
        if isinstance(repo_target, RepositoryIdentity):
            raw_path = repo_target.url
        elif isinstance(repo_target, str) and repo_target.strip():
            raw_path = repo_target.strip()
        else:
            raise RepositoryValidationError("repo", "Repository identity or path is required.")

        # Strip file:// URI prefix if present
        if raw_path.startswith("file://"):
            raw_path = raw_path[7:]

        # If base_directory configured and relative path given
        if not os.path.isabs(raw_path) and self._base_directory:
            resolved = os.path.abspath(os.path.join(self._base_directory, raw_path))
        else:
            resolved = os.path.abspath(raw_path)

        if not os.path.exists(resolved) or not os.path.isdir(resolved):
            raise RepositoryNotFoundError(raw_path, f"Directory does not exist: '{resolved}'")

        # If base directory is configured, guard against escaping base_directory
        if self._base_directory:
            common = os.path.commonpath([self._base_directory, resolved])
            if common != self._base_directory:
                raise RepositorySecurityError(raw_path, f"Path escapes base directory '{self._base_directory}'")

        return resolved

    def _resolve_file_path(self, root_dir: str, rel_path: str) -> str:
        """
        Resolve and validate relative file path inside root directory, preventing directory traversal
        and symlink escapes outside the repository root.
        """
        norm = normalize_repo_path(rel_path)
        if not norm:
            raise RepositoryValidationError("file_path", "File path cannot be empty or root.")

        abs_file_path = os.path.abspath(os.path.join(root_dir, norm))
        common = os.path.commonpath([root_dir, abs_file_path])
        if common != root_dir:
            raise RepositorySecurityError(rel_path, f"File path escapes repository root: '{rel_path}'")

        # Symlink containment check: if path exists, ensure resolved realpath is inside real root
        if os.path.lexists(abs_file_path):
            real_file_path = os.path.realpath(abs_file_path)
            real_root = os.path.realpath(root_dir)
            if os.path.commonpath([real_root, real_file_path]) != real_root:
                raise RepositorySecurityError(rel_path, f"Symlink targets location outside repository root: '{rel_path}' -> '{real_file_path}'")

        return abs_file_path

    # -------------------------------------------------------------------------
    # RepositoryProvider Contract Implementation
    # -------------------------------------------------------------------------

    def resolve_identity(
        self,
        target: str,
        timeout_seconds: Optional[float] = None,
        is_cancelled: Optional[Callable[[], bool]] = None,
    ) -> RepositoryIdentity:
        self.check_cancellation(is_cancelled, target, "resolve_identity")
        root_dir = self._resolve_root_path(target)
        dir_name = os.path.basename(root_dir) or "root"
        repo_id = f"local-{hashlib.sha256(root_dir.encode()).hexdigest()[:8]}"

        return RepositoryIdentity(
            repo_id=repo_id,
            url=f"file://{root_dir}",
            provider_type=RepositoryProviderType.LOCAL_GIT,
            name=dir_name,
            full_name=dir_name,
            default_branch="main",
            metadata={"local_root": root_dir},
        )

    def get_metadata(
        self,
        repo: RepositoryIdentity | str,
        timeout_seconds: Optional[float] = None,
        is_cancelled: Optional[Callable[[], bool]] = None,
    ) -> RepositoryIdentity:
        target_str = self.extract_repo_target(repo)
        self.check_cancellation(is_cancelled, target_str, "get_metadata")
        return self.resolve_identity(target_str, timeout_seconds=timeout_seconds, is_cancelled=is_cancelled)

    def get_revision(
        self,
        repo: RepositoryIdentity | str,
        revision: Optional[str] = None,
        timeout_seconds: Optional[float] = None,
        is_cancelled: Optional[Callable[[], bool]] = None,
    ) -> RepositoryRevision:
        target_str = self.extract_repo_target(repo)
        self.check_cancellation(is_cancelled, target_str, "get_revision")
        root_dir = self._resolve_root_path(repo)

        # Deterministic revision from root path and revision parameter
        if revision:
            sha = hashlib.sha256(f"{root_dir}:{revision}".encode()).hexdigest()[:40]
            is_tag = revision.startswith("v") or bool(re.match(r"^v?\d+\.\d+", revision))
            return RepositoryRevision(
                commit_sha=sha,
                branch=revision if not is_tag else None,
                tag=revision if is_tag else None,
                version_context=RepoVersionContext.tag(revision) if is_tag else RepoVersionContext.branch(revision),
            )

        # Default local HEAD revision
        sha = hashlib.sha256(f"{root_dir}:HEAD".encode()).hexdigest()[:40]
        return RepositoryRevision(
            commit_sha=sha,
            branch="main",
            version_context=RepoVersionContext.from_branch("main"),
        )

    def get_tree(
        self,
        repo: RepositoryIdentity | str,
        revision: Optional[str] = None,
        subpath: Optional[str] = None,
        max_depth: Optional[int] = None,
        max_files: Optional[int] = None,
        timeout_seconds: Optional[float] = None,
        is_cancelled: Optional[Callable[[], bool]] = None,
    ) -> RepositoryTree:
        target_str = self.extract_repo_target(repo)
        self.check_cancellation(is_cancelled, target_str, "get_tree")
        root_dir = self._resolve_root_path(repo)
        real_root = os.path.realpath(root_dir)

        scan_root = root_dir
        if subpath:
            norm_sub = normalize_repo_path(subpath)
            scan_root = self._resolve_file_path(root_dir, norm_sub)
            if not os.path.exists(scan_root) or not os.path.isdir(scan_root):
                raise RepositoryFileNotFoundError(target_str, norm_sub, revision=revision)

        eff_max_depth = max_depth if max_depth is not None else self.limits.max_tree_depth
        eff_max_files = max_files if max_files is not None else self.limits.max_tree_files

        tree = RepositoryTree()
        file_count = 0

        for current_root, dirs, files in os.walk(scan_root, topdown=True, followlinks=False):
            self.check_cancellation(is_cancelled, target_str, "get_tree")

            # Prune ignored directory names in-place and reject symlinked directories escaping root
            valid_dirs = []
            for d in dirs:
                if d in IGNORED_NAMES:
                    continue
                d_full = os.path.join(current_root, d)
                if os.path.islink(d_full):
                    try:
                        d_real = os.path.realpath(d_full)
                        if os.path.commonpath([real_root, d_real]) != real_root:
                            continue  # Ignore escaping symlink directory
                    except Exception:
                        continue
                valid_dirs.append(d)
            dirs[:] = valid_dirs

            rel_dir = os.path.relpath(current_root, root_dir).replace("\\", "/")
            if rel_dir == ".":
                rel_dir = ""

            current_depth = len(rel_dir.split("/")) if rel_dir else 0
            if current_depth > eff_max_depth:
                dirs.clear()  # Do not recurse deeper
                continue

            # Add directory node
            if rel_dir:
                parent_dir = "/".join(rel_dir.split("/")[:-1]) if "/" in rel_dir else ""
                tree.add_directory(
                    RepositoryDirectory(
                        path=rel_dir,
                        name=os.path.basename(current_root),
                        parent_path=parent_dir,
                    )
                )

            for fname in sorted(files):
                self.check_cancellation(is_cancelled, target_str, "get_tree")

                if fname in IGNORED_NAMES:
                    continue

                full_file_path = os.path.join(current_root, fname)

                # Skip symlinks that escape repository root or are broken
                if os.path.islink(full_file_path):
                    try:
                        real_f = os.path.realpath(full_file_path)
                        if os.path.commonpath([real_root, real_f]) != real_root:
                            continue
                    except Exception:
                        continue

                rel_file_path = os.path.relpath(full_file_path, root_dir).replace("\\", "/")
                try:
                    norm_file_path = normalize_repo_path(rel_file_path)
                except RepositoryValidationError:
                    continue

                file_size = 0
                try:
                    file_size = os.path.getsize(full_file_path)
                except OSError:
                    pass

                is_bin = is_known_binary_extension(fname)
                lang = detect_file_language(fname)
                line_count = 0
                checksum = ""

                if not is_bin and file_size < 100_000:
                    try:
                        with open(full_file_path, "rb") as f:
                            raw_b = f.read()
                            checksum = compute_sha256(raw_b)
                            try:
                                text_c = raw_b.decode("utf-8")
                                line_count = len(text_c.splitlines())
                            except UnicodeDecodeError:
                                is_bin = True
                    except OSError:
                        pass

                tree.add_file(
                    RepositoryFile(
                        path=norm_file_path,
                        filename=fname,
                        extension=f".{fname.split('.')[-1]}" if "." in fname else "",
                        parent_path=rel_dir,
                        size_bytes=file_size,
                        line_count=line_count,
                        is_binary=is_bin,
                        language=lang,
                        content_checksum=checksum,
                    )
                )
                file_count += 1
                if file_count > eff_max_files:
                    raise RepositoryResourceLimitError("tree_files", file_count, eff_max_files)

        return tree

    def get_file_content(
        self,
        repo: RepositoryIdentity | str,
        file_path: str,
        revision: Optional[str] = None,
        line_range: Optional[LineRange] = None,
        max_bytes: Optional[int] = None,
        timeout_seconds: Optional[float] = None,
        is_cancelled: Optional[Callable[[], bool]] = None,
    ) -> RepositorySourceMaterial:
        target_str = self.extract_repo_target(repo)
        self.check_cancellation(is_cancelled, target_str, "get_file_content")
        root_dir = self._resolve_root_path(repo)

        norm_path = normalize_repo_path(file_path)
        abs_path = self._resolve_file_path(root_dir, norm_path)

        if not os.path.exists(abs_path) or os.path.isdir(abs_path):
            raise RepositoryFileNotFoundError(target_str, norm_path, revision=revision)

        eff_max_bytes = max_bytes if max_bytes is not None else self.limits.max_file_bytes
        try:
            file_size = os.path.getsize(abs_path)
        except OSError as e:
            raise RepositoryProviderError(f"Error checking file size for '{abs_path}': {e}") from e

        if file_size > eff_max_bytes:
            raise RepositoryResourceLimitError("file_bytes", file_size, eff_max_bytes)

        try:
            with open(abs_path, "rb") as f:
                raw_bytes = f.read(eff_max_bytes + 1)
        except OSError as e:
            raise RepositoryProviderError(f"Error reading file '{abs_path}': {e}") from e

        if len(raw_bytes) > eff_max_bytes:
            raise RepositoryResourceLimitError("file_bytes", len(raw_bytes), eff_max_bytes)

        try:
            content_str = raw_bytes.decode("utf-8")
        except UnicodeDecodeError:
            content_str = ""

        final_content = content_str
        final_bytes = raw_bytes

        if line_range is not None and content_str:
            lines = content_str.splitlines(keepends=True)
            total_lines = len(lines)
            if line_range.start_line > max(total_lines, 1):
                final_content = ""
                final_bytes = b""
            else:
                start_idx = max(0, line_range.start_line - 1)
                end_idx = min(total_lines, line_range.end_line)
                sliced_lines = lines[start_idx:end_idx]
                final_content = "".join(sliced_lines)
                final_bytes = final_content.encode("utf-8")

        ident = self.resolve_identity(root_dir)
        rev_obj = self.get_revision(root_dir, revision=revision)
        snippet_id = RepositorySourceMaterial.generate_snippet_id(ident.repo_id, rev_obj, norm_path, line_range)
        checksum = compute_sha256(final_bytes)
        lang = detect_file_language(norm_path)

        return RepositorySourceMaterial(
            snippet_id=snippet_id,
            repository_identity=ident,
            revision=rev_obj,
            file_path=norm_path,
            line_range=line_range,
            content=final_content,
            raw_bytes=final_bytes,
            content_checksum=checksum,
            language=lang,
        )
