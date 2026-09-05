"""
Local Filesystem Project Workspace Provider (Phase 1 / Part 8 / Step 2).

Provides controlled, strictly READ-ONLY access to an actual workspace directory on the local filesystem.
Guarantees:
- Zero execution: no subprocess calls, no shell commands, no script runs.
- Strict project-root containment: rejects directory traversals and symlink escapes.
- Credential isolation: blocks access to .env and private key files; masks config and VCS URLs.
- Resource limits: bounds directory traversal depth, file counts, and file byte sizes.
"""
from __future__ import annotations

import configparser
import hashlib
import json
import logging
import os
import posixpath
import re
import stat
import time
from typing import Any, Callable, Optional

from core.research.errors import (
    ProjectAccessError,
    ProjectCancelledError,
    ProjectFileNotFoundError,
    ProjectMalformedFileError,
    ProjectProviderError,
    ProjectResourceLimitError,
    ProjectSecurityError,
    ProjectTimeoutError,
    ProjectValidationError,
)
from core.research.project.models import (
    ProjectConfigType,
    ProjectDirectory,
    ProjectFile,
    ProjectIdentity,
    ProjectSourceMaterial,
    ProjectStructure,
    ProjectType,
    ProjectVCSContext,
    ProjectVCSState,
    mask_sensitive_config,
    normalize_project_path,
)
from core.research.project.provider import (
    IGNORED_PROJECT_NAMES,
    ProjectFetchLimits,
    ProjectWorkspaceProvider,
    is_sensitive_project_path,
    sanitize_content_secrets,
    sanitize_project_error,
)
from core.research.repo.models import (
    LineRange,
    compute_sha256,
    detect_file_language,
    is_known_binary_extension,
    utc_now,
)
from core.research.project.vcs import (
    ProjectVCSInspector,
    ProjectVCSOptions,
)
from core.research.search.security import sanitize_url

logger = logging.getLogger("AutonomOS.Research.LocalProjectWorkspaceProvider")


WINDOWS_RESERVED_NAMES = {
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}


class LocalProjectWorkspaceProvider(ProjectWorkspaceProvider):
    """
    Local filesystem workspace provider for real AutonomOS project directories.

    Enforces strict read-only access, root containment, and secret shielding.
    """

    def __init__(
        self,
        root_path: str,
        provider_id: str = "local-project-provider",
        name: str = "Local Project Workspace Provider",
        limits: Optional[ProjectFetchLimits] = None,
        ignored_names: Optional[set[str]] = None,
    ):
        if not root_path or not root_path.strip():
            raise ProjectValidationError("root_path", "Root path cannot be empty.")

        resolved_root = os.path.realpath(os.path.abspath(root_path.strip()))
        if not os.path.exists(resolved_root) or not os.path.isdir(resolved_root):
            raise ProjectFileNotFoundError(resolved_root, reason=f"Project root directory does not exist: '{resolved_root}'")

        super().__init__(
            provider_id=provider_id,
            name=name,
            root_path=resolved_root,
            limits=limits or ProjectFetchLimits(),
        )
        self._real_root = resolved_root
        self._ignored_names = set(ignored_names or IGNORED_PROJECT_NAMES)

    def _resolve_safe_path(self, rel_path: str) -> str:
        """
        Resolve a relative project path against the root directory, verifying strict containment
        and rejecting traversal, device names, or symlink escapes.
        """
        norm = self.validate_relative_path(rel_path)
        if not norm:
            return self._real_root

        # Check for Windows reserved device names
        for segment in norm.split("/"):
            stem = posixpath.splitext(segment)[0].upper()
            if stem in WINDOWS_RESERVED_NAMES:
                raise ProjectSecurityError(norm, f"Path contains reserved device name: '{segment}'")

        # Check for sensitive path
        if is_sensitive_project_path(norm):
            raise ProjectSecurityError(norm, "Access to credential/secret file is forbidden.")

        candidate = os.path.abspath(os.path.join(self._root_path, norm))

        # Check containment before dereferencing symlinks
        try:
            common = os.path.commonpath([self._root_path, candidate])
        except ValueError:
            raise ProjectSecurityError(rel_path, f"Path escapes project root: '{rel_path}'")

        if common != self._root_path:
            raise ProjectSecurityError(rel_path, f"Path escapes project root: '{rel_path}'")

        # If the file or link exists, check realpath containment to prevent symlink escapes
        if os.path.lexists(candidate) or os.path.islink(candidate):
            real_candidate = os.path.realpath(candidate)
            try:
                common_real = os.path.commonpath([self._real_root, real_candidate])
            except ValueError:
                raise ProjectSecurityError(rel_path, f"Symlink targets location outside project root: '{rel_path}' -> '{real_candidate}'")
            if common_real != self._real_root:
                raise ProjectSecurityError(rel_path, f"Symlink targets location outside project root: '{rel_path}' -> '{real_candidate}'")

        return candidate

    # -------------------------------------------------------------------------
    # ProjectWorkspaceProvider Operations
    # -------------------------------------------------------------------------

    def identify_root(
        self,
        timeout_seconds: Optional[float] = None,
        is_cancelled: Optional[Callable[[], bool]] = None,
    ) -> ProjectIdentity:
        start_time = time.time()
        self.check_cancellation(is_cancelled, "identify_root", self.root_path)
        self.check_timeout(start_time, timeout_seconds, "identify_root", self.root_path)

        dir_name = os.path.basename(self._root_path) or "root"
        proj_id = f"proj-{hashlib.sha256(self._real_root.encode()).hexdigest()[:8]}"

        # Infer project type and languages from manifests present
        detected_type = ProjectType.UNKNOWN
        detected_langs: list[str] = []
        detected_version: Optional[str] = None

        if os.path.isfile(os.path.join(self._root_path, "pyproject.toml")) or os.path.isfile(os.path.join(self._root_path, "setup.py")):
            detected_type = ProjectType.PYTHON
            detected_langs.append("Python")
        elif os.path.isfile(os.path.join(self._root_path, "package.json")):
            detected_type = ProjectType.TYPESCRIPT if os.path.isfile(os.path.join(self._root_path, "tsconfig.json")) else ProjectType.JAVASCRIPT
            detected_langs.append("TypeScript" if detected_type == ProjectType.TYPESCRIPT else "JavaScript")
        elif os.path.isfile(os.path.join(self._root_path, "Cargo.toml")):
            detected_type = ProjectType.RUST
            detected_langs.append("Rust")
        elif os.path.isfile(os.path.join(self._root_path, "go.mod")):
            detected_type = ProjectType.GO
            detected_langs.append("Go")

        # Quick safe extraction of version from pyproject.toml or package.json if present
        pyproj_path = os.path.join(self._root_path, "pyproject.toml")
        pkg_json_path = os.path.join(self._root_path, "package.json")
        if os.path.isfile(pyproj_path):
            try:
                with open(pyproj_path, "r", encoding="utf-8") as f:
                    for line in f:
                        m = re.match(r'^\s*version\s*=\s*["\']([^"\']+)["\']', line)
                        if m:
                            detected_version = m.group(1)
                            break
            except Exception:
                pass
        elif os.path.isfile(pkg_json_path):
            try:
                with open(pkg_json_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    if isinstance(data, dict) and "version" in data:
                        detected_version = str(data["version"])
            except Exception:
                pass

        return ProjectIdentity(
            project_id=proj_id,
            project_root=self._root_path,
            name=dir_name,
            project_type=detected_type,
            languages=detected_langs,
            version=detected_version,
            metadata={"local_root": self._root_path, "real_root": self._real_root},
        )

    def list_dir(
        self,
        subpath: str = "",
        max_depth: Optional[int] = None,
        max_files: Optional[int] = None,
        timeout_seconds: Optional[float] = None,
        is_cancelled: Optional[Callable[[], bool]] = None,
    ) -> ProjectStructure:
        start_time = time.time()
        self.check_cancellation(is_cancelled, "list_dir", subpath)
        self.check_timeout(start_time, timeout_seconds, "list_dir", subpath)

        scan_abs = self._resolve_safe_path(subpath)
        if not os.path.exists(scan_abs) or not os.path.isdir(scan_abs):
            raise ProjectFileNotFoundError(subpath or "/", project_id=self.provider_id)

        eff_max_depth = max_depth if max_depth is not None else self._limits.max_tree_depth
        eff_max_files = max_files if max_files is not None else self._limits.max_tree_files

        directories: dict[str, ProjectDirectory] = {}
        files: dict[str, ProjectFile] = {}
        file_count = 0

        for current_root, dirs, filenames in os.walk(scan_abs, topdown=True, followlinks=False):
            self.check_cancellation(is_cancelled, "list_dir", current_root)
            self.check_timeout(start_time, timeout_seconds, "list_dir", current_root)

            # Prune ignored directory names and symlinked directories escaping root
            valid_dirs = []
            for d in dirs:
                if d in self._ignored_names:
                    continue
                d_abs = os.path.join(current_root, d)
                if os.path.islink(d_abs):
                    try:
                        d_real = os.path.realpath(d_abs)
                        if os.path.commonpath([self._real_root, d_real]) != self._real_root:
                            continue
                    except Exception:
                        continue
                valid_dirs.append(d)
            dirs[:] = sorted(valid_dirs)

            rel_dir = os.path.relpath(current_root, self._root_path).replace("\\", "/")
            if rel_dir == ".":
                rel_dir = ""

            current_depth = len(rel_dir.split("/")) if rel_dir else 0
            if current_depth > eff_max_depth:
                dirs.clear()
                continue

            if rel_dir:
                parent_dir = "/".join(rel_dir.split("/")[:-1]) if "/" in rel_dir else ""
                directories[rel_dir] = ProjectDirectory(
                    path=rel_dir,
                    name=os.path.basename(current_root),
                    parent_path=parent_dir,
                )

            for fname in sorted(filenames):
                self.check_cancellation(is_cancelled, "list_dir", fname)

                if fname in self._ignored_names:
                    continue

                full_file_path = os.path.join(current_root, fname)
                rel_file_path = os.path.relpath(full_file_path, self._root_path).replace("\\", "/")

                if is_sensitive_project_path(rel_file_path):
                    continue

                # Symlink containment check
                if os.path.islink(full_file_path):
                    try:
                        real_f = os.path.realpath(full_file_path)
                        if os.path.commonpath([self._real_root, real_f]) != self._real_root:
                            continue
                    except Exception:
                        continue

                try:
                    norm_path = normalize_project_path(rel_file_path)
                except ProjectValidationError:
                    continue

                # Retrieve file stat
                try:
                    st = os.stat(full_file_path)
                    f_size = st.st_size
                except OSError:
                    continue

                is_bin = is_known_binary_extension(fname)
                lang = detect_file_language(fname)
                line_count = 0
                c_hash = ""

                if not is_bin and f_size <= 100_000:
                    try:
                        with open(full_file_path, "rb") as f:
                            chunk = f.read()
                            c_hash = compute_sha256(chunk)
                            try:
                                text_str = chunk.decode("utf-8")
                                line_count = len(text_str.splitlines())
                            except UnicodeDecodeError:
                                is_bin = True
                    except (OSError, PermissionError):
                        pass

                files[norm_path] = ProjectFile(
                    relative_path=norm_path,
                    filename=fname,
                    extension=posixpath.splitext(fname)[1],
                    parent_path=rel_dir,
                    size_bytes=f_size,
                    line_count=line_count,
                    is_binary=is_bin,
                    language=lang,
                    content_hash=c_hash,
                )
                file_count += 1

                if file_count > eff_max_files:
                    raise ProjectResourceLimitError("tree_files", file_count, eff_max_files)

        return ProjectStructure(
            root_path=self._root_path,
            directories=list(directories.values()),
            files=list(files.values()),
            max_depth_reached=eff_max_depth,
            is_truncated=False,
            total_files=len(files),
            total_directories=len(directories),
        )

    def get_file_metadata(
        self,
        file_path: str,
        timeout_seconds: Optional[float] = None,
        is_cancelled: Optional[Callable[[], bool]] = None,
    ) -> ProjectFile:
        start_time = time.time()
        self.check_cancellation(is_cancelled, "get_file_metadata", file_path)
        self.check_timeout(start_time, timeout_seconds, "get_file_metadata", file_path)

        abs_path = self._resolve_safe_path(file_path)
        norm_path = self.validate_relative_path(file_path)

        if not os.path.exists(abs_path) or os.path.isdir(abs_path):
            raise ProjectFileNotFoundError(norm_path, project_id=self.provider_id)

        try:
            st = os.stat(abs_path)
            f_size = st.st_size
        except PermissionError as e:
            raise ProjectAccessError(norm_path, reason=str(e))
        except OSError as e:
            raise ProjectProviderError(provider_id=self.provider_id, message=str(e))

        fname = os.path.basename(abs_path)
        parent_dir = posixpath.dirname(norm_path)
        is_bin = is_known_binary_extension(fname)
        lang = detect_file_language(fname)
        line_count = 0
        c_hash = ""

        if not is_bin and f_size <= 100_000:
            try:
                with open(abs_path, "rb") as f:
                    data = f.read()
                    c_hash = compute_sha256(data)
                    try:
                        line_count = len(data.decode("utf-8").splitlines())
                    except UnicodeDecodeError:
                        is_bin = True
            except PermissionError as e:
                raise ProjectAccessError(norm_path, reason=str(e))
            except OSError:
                pass

        return ProjectFile(
            relative_path=norm_path,
            filename=fname,
            extension=posixpath.splitext(fname)[1],
            parent_path=parent_dir,
            size_bytes=f_size,
            line_count=line_count,
            is_binary=is_bin,
            language=lang,
            content_hash=c_hash,
        )

    def get_file_content(
        self,
        file_path: str,
        line_range: Optional[LineRange] = None,
        max_bytes: Optional[int] = None,
        timeout_seconds: Optional[float] = None,
        is_cancelled: Optional[Callable[[], bool]] = None,
    ) -> ProjectSourceMaterial:
        start_time = time.time()
        self.check_cancellation(is_cancelled, "get_file_content", file_path)
        self.check_timeout(start_time, timeout_seconds, "get_file_content", file_path)

        abs_path = self._resolve_safe_path(file_path)
        norm_path = self.validate_relative_path(file_path)

        if not os.path.exists(abs_path) or os.path.isdir(abs_path):
            raise ProjectFileNotFoundError(norm_path, project_id=self.provider_id)

        try:
            st = os.stat(abs_path)
            total_size = st.st_size
            if not stat.S_ISREG(st.st_mode):
                raise ProjectSecurityError(norm_path, f"Non-regular file access (device, socket, fifo) is forbidden: '{norm_path}'")
        except PermissionError as e:
            raise ProjectAccessError(norm_path, reason=str(e))
        except (ProjectSecurityError, ProjectAccessError):
            raise
        except OSError as e:
            raise ProjectProviderError(provider_id=self.provider_id, message=str(e))

        if total_size > self._limits.max_file_size:
            raise ProjectResourceLimitError("max_file_size", total_size, self._limits.max_file_size)

        eff_max_bytes = max_bytes if max_bytes is not None else self._limits.max_file_bytes
        fname = os.path.basename(abs_path)
        is_bin = is_known_binary_extension(fname)

        try:
            with open(abs_path, "rb") as f:
                raw_bytes = f.read(total_size)
        except PermissionError as e:
            raise ProjectAccessError(norm_path, reason=str(e))
        except OSError as e:
            raise ProjectProviderError(provider_id=self.provider_id, message=str(e))

        if is_bin:
            if len(raw_bytes) > eff_max_bytes:
                raise ProjectResourceLimitError("file_bytes", len(raw_bytes), eff_max_bytes)
            mat_id = ProjectSourceMaterial.generate_material_id(self.provider_id, norm_path)
            return ProjectSourceMaterial(
                material_id=mat_id,
                file_path=norm_path,
                content="",
                raw_bytes=raw_bytes,
                line_range=None,
                language=detect_file_language(fname),
                is_binary=True,
                content_hash=compute_sha256(raw_bytes),
            )

        try:
            text_content = raw_bytes.decode("utf-8")
        except UnicodeDecodeError:
            # Reclassify as binary or malformed if text expected
            is_bin = True
            mat_id = ProjectSourceMaterial.generate_material_id(self.provider_id, norm_path)
            return ProjectSourceMaterial(
                material_id=mat_id,
                file_path=norm_path,
                content="",
                raw_bytes=raw_bytes,
                line_range=None,
                language=detect_file_language(fname),
                is_binary=True,
                content_hash=compute_sha256(raw_bytes),
            )

        # Scrub inline secrets from text content
        text_content = sanitize_content_secrets(text_content)

        lines = text_content.splitlines(keepends=True)
        if line_range is not None:
            total_lines = len(lines)
            start_idx = max(0, line_range.start_line - 1)
            end_idx = min(total_lines, line_range.end_line)
            sliced_text = "".join(lines[start_idx:end_idx])
            eff_line_range = line_range
        else:
            sliced_text = text_content
            eff_line_range = None

        sliced_bytes = sliced_text.encode("utf-8")
        if len(sliced_bytes) > eff_max_bytes:
            raise ProjectResourceLimitError("file_bytes", len(sliced_bytes), eff_max_bytes)

        mat_id = ProjectSourceMaterial.generate_material_id(self.provider_id, norm_path, eff_line_range)
        return ProjectSourceMaterial(
            material_id=mat_id,
            file_path=norm_path,
            content=sliced_text,
            raw_bytes=sliced_bytes,
            line_range=eff_line_range,
            language=detect_file_language(fname),
            is_binary=False,
            content_hash=compute_sha256(sliced_bytes),
        )

    def get_project_metadata(
        self,
        timeout_seconds: Optional[float] = None,
        is_cancelled: Optional[Callable[[], bool]] = None,
    ) -> dict[str, Any]:
        start_time = time.time()
        self.check_cancellation(is_cancelled, "get_project_metadata", self.root_path)
        self.check_timeout(start_time, timeout_seconds, "get_project_metadata", self.root_path)

        raw_meta: dict[str, Any] = {
            "root_path": self._root_path,
            "project_name": os.path.basename(self._root_path) or "root",
        }

        # Check for config files
        cfg_path = os.path.join(self._root_path, "config.json")
        if os.path.isfile(cfg_path):
            try:
                with open(cfg_path, "r", encoding="utf-8") as f:
                    raw_meta["config"] = mask_sensitive_config(json.load(f))
            except Exception:
                pass

        return raw_meta

    def get_vcs_metadata(
        self,
        allow_vcs: bool = False,
        timeout_seconds: Optional[float] = None,
        is_cancelled: Optional[Callable[[], bool]] = None,
    ) -> Optional[ProjectVCSContext]:
        if not allow_vcs:
            return None

        effective_timeout = timeout_seconds if timeout_seconds is not None else self._limits.timeout_seconds
        options = ProjectVCSOptions(
            allow_vcs=True,
            timeout_seconds=effective_timeout,
            is_cancelled=is_cancelled,
        )
        return ProjectVCSInspector.inspect(
            project_root=self._root_path,
            options=options,
        )
