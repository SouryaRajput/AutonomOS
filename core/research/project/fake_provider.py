"""
Deterministic Fake Project Workspace Provider (Phase 1 / Part 8 / Step 2).

Provides an in-memory, hermetic project workspace provider with realistic fixtures,
simulation hooks for failure scenarios, strict root containment, and zero-execution safety.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import logging
import posixpath
import time
from typing import Any, Callable, Optional, Union

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
)
from core.research.repo.models import (
    LineRange,
    compute_sha256,
    detect_file_language,
    is_known_binary_extension,
)
from core.research.search.security import sanitize_url

logger = logging.getLogger("AutonomOS.Research.FakeProjectWorkspaceProvider")


def _generate_default_fixtures() -> tuple[dict[str, Union[str, bytes]], dict[str, str], ProjectVCSContext]:
    """
    Generate realistic in-memory fixture files, symlinks, and VCS context.
    """
    # Large CSV content (approx 1.1 MB)
    large_csv_line = "1001,AutonomOS_Worker,active,2026-01-01T00:00:00Z,99.98,Production\n"
    large_csv_content = "id,name,status,timestamp,score,environment\n" + (large_csv_line * 16500)

    files: dict[str, Union[str, bytes]] = {
        # Source files
        "src/main.py": (
            '"""Main application entry point."""\n'
            "import sys\n"
            "from src.core.engine import run_engine\n\n"
            "class App:\n"
            '    def __init__(self, name: str = "AutonomOSApp"):\n'
            "        self.name = name\n\n"
            "    def run(self) -> int:\n"
            "        return run_engine(self.name)\n\n"
            'if __name__ == "__main__":\n'
            "    app = App()\n"
            "    sys.exit(app.run())\n"
        ),
        "src/utils/helpers.py": (
            '"""Utility helper functions."""\n\n'
            "def format_greeting(name: str) -> str:\n"
            '    return f"Hello, {name}!"\n\n'
            "def add_numbers(a: int, b: int) -> int:\n"
            "    return a + b\n"
        ),
        "src/core/engine.py": (
            '"""Core execution engine logic."""\n\n'
            "def run_engine(target: str) -> int:\n"
            '    print(f"Executing engine for {target}")\n'
            "    return 0\n"
        ),
        "src/core/subsystem/deep/module.py": (
            '"""Deeply nested subsystem module for hierarchy tests."""\n'
            "SUBSYSTEM_DEPTH = 4\n"
        ),
        # Tests
        "tests/test_main.py": (
            "import unittest\n"
            "from src.main import App\n\n"
            "class TestApp(unittest.TestCase):\n"
            "    def test_run(self):\n"
            "        app = App()\n"
            "        self.assertEqual(app.run(), 0)\n"
        ),
        "tests/unit/test_helpers.py": (
            "import unittest\n"
            "from src.utils.helpers import format_greeting, add_numbers\n\n"
            "class TestHelpers(unittest.TestCase):\n"
            "    def test_format_greeting(self):\n"
            '        self.assertEqual(format_greeting("World"), "Hello, World!")\n\n'
            "    def test_add_numbers(self):\n"
            "        self.assertEqual(add_numbers(2, 3), 5)\n"
        ),
        # Documentation
        "README.md": (
            "# Sample Project\n\n"
            "Sample repository workspace for AutonomOS research testing.\n\n"
            "## Architecture\n"
            "See `docs/architecture.md` for architecture details.\n"
        ),
        "docs/architecture.md": (
            "# Architecture\n\n"
            "The project contains:\n"
            "- `src/main.py`: Entrypoint\n"
            "- `src/core`: Engine logic\n"
            "- `src/utils`: Helper functions\n"
        ),
        # Configuration
        "config/settings.json": json.dumps({
            "app_name": "SampleProject",
            "version": "1.2.0",
            "environment": "production",
            "database": {
                "host": "localhost",
                "port": 5432,
                "user": "app_user",
                "password": "secret_db_password_xyz",
            },
            "api_key": "secret_api_token_12345",
        }, indent=2),
        "config/app.yaml": (
            "app:\n"
            "  debug: false\n"
            "  workers: 4\n"
            "  timeout: 30\n"
        ),
        # Manifests
        "pyproject.toml": (
            '[project]\n'
            'name = "sample-project"\n'
            'version = "1.2.0"\n'
            'description = "Sample project for research workspace inspection"\n'
            'dependencies = [\n'
            '    "pydantic>=2.0.0",\n'
            '    "httpx>=0.24.0",\n'
            ']\n\n'
            '[project.optional-dependencies]\n'
            'dev = [\n'
            '    "pytest>=8.0.0",\n'
            ']\n'
        ),
        "package.json": json.dumps({
            "name": "sample-project-web",
            "version": "1.2.0",
            "dependencies": {
                "react": "^18.2.0",
            },
        }, indent=2),
        # Binary files
        "assets/logo.png": (
            b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
            b"\x08\x06\x00\x00\x00\x1f\x15c4\x00\x00\x00\rIDATx\x9cc\xf8\xff\xff"
            b"?\x00\x05\xfe\x02\xfe\xdc\xccY\xe7\x00\x00\x00\x00IEND\xaeB`\x82"
        ),
        "bin/tool.wasm": b"\x00asm\x01\x00\x00\x00",
        # Large file
        "data/large_dataset.csv": large_csv_content,
        # Ignored / Sensitive files
        ".env": "SECRET_DATABASE_KEY=super_secret_env_value\nAWS_SECRET_ACCESS_KEY=abcd1234efgh\n",
        "node_modules/mock-lib/index.js": "module.exports = {};\n",
        "__pycache__/engine.cpython-312.pyc": b"fake_pyc_bytecode_data",
    }

    symlinks: dict[str, str] = {
        # Valid internal symlink
        "docs/readme_link.md": "../README.md",
        # Escaping symlink
        "escaped_link.txt": "../../etc/passwd",
        # Broken symlink
        "docs/broken_link.md": "docs/nonexistent.md",
    }

    vcs = ProjectVCSContext(
        branch="main",
        revision="a1b2c3d4e5f6789012345678901234567890abcd",
        working_tree_state=ProjectVCSState.CLEAN,
        repository_id="autonomos/sample-project",
        modified_files=[],
        deleted_files=[],
        untracked_files=[],
        staged_files=[],
        unstaged_files=[],
        author="Developer <dev@autonomos.ai>",
        commit_date="2026-01-01T12:00:00Z",
        remote_url="https://github.com/autonomos/sample-project.git",
        metadata={"commit_message": "Initial project release v1.2.0", "vcs_type": "git"},
    )

    return files, symlinks, vcs


class FakeProjectWorkspaceProvider(ProjectWorkspaceProvider):
    """
    In-memory, deterministic fake workspace provider.

    Supports:
    - Complete suite of realistic files (sources, tests, docs, configs, manifests, binaries, large files)
    - Full simulation hooks (errors, timeouts, cancellations, missing files, access errors, malformations)
    - Project-root containment and symlink escape verification
    - Bounded execution with depth, file-count, and byte ceilings
    """

    def __init__(
        self,
        provider_id: str = "fake-project-provider",
        name: str = "Fake Project Workspace Provider",
        root_path: str = "/fake/workspace/sample-project",
        limits: Optional[ProjectFetchLimits] = None,
        custom_files: Optional[dict[str, Union[str, bytes]]] = None,
        custom_symlinks: Optional[dict[str, str]] = None,
        custom_vcs: Optional[ProjectVCSContext] = None,
        inaccessible_paths: Optional[set[str]] = None,
        malformed_paths: Optional[set[str]] = None,
        simulate_error: Optional[Exception] = None,
        simulate_timeout: bool = False,
        simulate_timeout_ops: Optional[set[str]] = None,
        simulate_cancelled: bool = False,
    ):
        super().__init__(
            provider_id=provider_id,
            name=name,
            root_path=root_path,
            limits=limits or ProjectFetchLimits(),
        )
        def_files, def_symlinks, def_vcs = _generate_default_fixtures()
        self._files: dict[str, Union[str, bytes]] = dict(def_files)
        if custom_files:
            self._files.update(custom_files)

        self._symlinks: dict[str, str] = dict(def_symlinks)
        if custom_symlinks:
            self._symlinks.update(custom_symlinks)

        self._vcs: Optional[ProjectVCSContext] = custom_vcs or def_vcs

        self.inaccessible_paths: set[str] = set(inaccessible_paths or [])
        self.malformed_paths: set[str] = set(malformed_paths or [])
        self.simulate_error = simulate_error
        self.simulate_timeout = simulate_timeout
        self.simulate_timeout_ops = set(simulate_timeout_ops or [])
        self.simulate_cancelled = simulate_cancelled

        # Call tracking
        self.call_history: list[dict[str, Any]] = []

    def _record_call(self, operation: str, **kwargs) -> None:
        self.call_history.append({"operation": operation, "time": time.time(), **kwargs})

    def _trigger_simulation_hooks(
        self,
        operation: str,
        target: str = "",
        timeout_seconds: Optional[float] = None,
        is_cancelled: Optional[Callable[[], bool]] = None,
    ) -> None:
        """
        Evaluate simulation flags, cancellation, and timeouts.
        """
        self.check_cancellation(is_cancelled, operation, target)

        if self.simulate_cancelled:
            raise ProjectCancelledError(operation=operation, target=target, message="Simulated cancellation.")

        if self.simulate_error is not None:
            if isinstance(self.simulate_error, ProjectProviderError):
                raise self.simulate_error
            raise ProjectProviderError(
                provider_id=self.provider_id,
                message=str(self.simulate_error),
                details={"simulated": True},
            )

        if self.simulate_timeout or operation in self.simulate_timeout_ops:
            eff_timeout = timeout_seconds if timeout_seconds is not None else self._limits.timeout_seconds
            raise ProjectTimeoutError(operation=operation, timeout_seconds=eff_timeout, target=target)

    def _resolve_symlink_or_path(self, rel_path: str) -> tuple[str, bool]:
        """
        Resolve a relative path, traversing symlinks if applicable.
        Checks for escaping symlinks and raises ProjectSecurityError if escaping root.
        Returns: (target_path, is_symlink)
        """
        current = rel_path
        visited = set()
        is_sym = False

        while current in self._symlinks:
            is_sym = True
            if current in visited:
                raise ProjectSecurityError(current, f"Symlink cycle detected: '{rel_path}'")
            visited.add(current)
            target = self._symlinks[current]

            # Check if target escapes root
            norm_target = target.replace("\\", "/")
            if norm_target.startswith("/") or "/../" in f"/{norm_target}/" or norm_target.startswith("../") or norm_target == "..":
                # Check resolved posixpath
                base_dir = posixpath.dirname(current)
                combined = posixpath.normpath(posixpath.join(base_dir, norm_target))
                if combined.startswith("../") or combined == "..":
                    raise ProjectSecurityError(
                        current,
                        f"Symlink targets location outside project root: '{current}' -> '{target}'",
                    )
                current = normalize_project_path(combined)
            else:
                base_dir = posixpath.dirname(current)
                combined = posixpath.normpath(posixpath.join(base_dir, norm_target))
                current = normalize_project_path(combined)

        return current, is_sym

    # -------------------------------------------------------------------------
    # ProjectWorkspaceProvider Implementation
    # -------------------------------------------------------------------------

    def identify_root(
        self,
        timeout_seconds: Optional[float] = None,
        is_cancelled: Optional[Callable[[], bool]] = None,
    ) -> ProjectIdentity:
        self._record_call("identify_root")
        self._trigger_simulation_hooks("identify_root", self.root_path, timeout_seconds, is_cancelled)

        return ProjectIdentity(
            project_id=f"proj-{hashlib.sha256(self.root_path.encode()).hexdigest()[:8]}",
            project_root=self.root_path,
            name=posixpath.basename(self.root_path.rstrip("/")) or "sample-project",
            project_type=ProjectType.PYTHON,
            languages=["Python"],
            version="1.2.0",
            metadata={"simulated": True, "fixture_files_count": len(self._files)},
        )

    def list_dir(
        self,
        subpath: str = "",
        max_depth: Optional[int] = None,
        max_files: Optional[int] = None,
        timeout_seconds: Optional[float] = None,
        is_cancelled: Optional[Callable[[], bool]] = None,
    ) -> ProjectStructure:
        self._record_call("list_dir", subpath=subpath)
        norm_sub = self.validate_relative_path(subpath) if subpath else ""
        self._trigger_simulation_hooks("list_dir", norm_sub, timeout_seconds, is_cancelled)

        if norm_sub in self.inaccessible_paths:
            raise ProjectAccessError(norm_sub, "Directory is inaccessible.")

        eff_max_depth = max_depth if max_depth is not None else self._limits.max_tree_depth
        eff_max_files = max_files if max_files is not None else self._limits.max_tree_files

        directories: dict[str, ProjectDirectory] = {}
        files: dict[str, ProjectFile] = {}
        file_count = 0

        # Combine files and symlinks
        all_candidate_paths = sorted(set(list(self._files.keys()) + list(self._symlinks.keys())))

        for p in all_candidate_paths:
            self.check_cancellation(is_cancelled, "list_dir", p)

            # Skip ignored directories & sensitive files
            parts = p.split("/")
            if any(part in IGNORED_PROJECT_NAMES for part in parts):
                continue
            if is_sensitive_project_path(p):
                continue

            if norm_sub and not (p == norm_sub or p.startswith(norm_sub + "/")):
                continue

            # Check for escaping symlink
            if p in self._symlinks:
                try:
                    resolved_p, _ = self._resolve_symlink_or_path(p)
                except ProjectSecurityError:
                    continue  # Safely ignore escaping symlink during directory walk

            # Calculate relative path from subpath
            rel_from_sub = posixpath.relpath(p, norm_sub) if norm_sub else p
            depth = len(rel_from_sub.split("/")) if rel_from_sub != "." else 0

            if depth > eff_max_depth:
                continue

            # Populate directory hierarchy
            dir_parts = p.split("/")[:-1]
            cur_dir = ""
            for d in dir_parts:
                parent = cur_dir
                cur_dir = f"{cur_dir}/{d}".lstrip("/")
                if cur_dir not in directories:
                    directories[cur_dir] = ProjectDirectory(
                        path=cur_dir,
                        name=d,
                        parent_path=parent,
                    )

            # Add file metadata
            fname = posixpath.basename(p)
            parent_dir = posixpath.dirname(p)
            raw_content = self._files.get(p)
            if raw_content is None and p in self._symlinks:
                resolved_target, _ = self._resolve_symlink_or_path(p)
                raw_content = self._files.get(resolved_target, "")

            if isinstance(raw_content, str):
                content_bytes = raw_content.encode("utf-8")
                is_bin = is_known_binary_extension(fname)
                line_count = len(raw_content.splitlines())
            elif isinstance(raw_content, bytes):
                content_bytes = raw_content
                is_bin = True
                line_count = 0
            else:
                content_bytes = b""
                is_bin = False
                line_count = 0

            file_node = ProjectFile(
                relative_path=p,
                filename=fname,
                extension=posixpath.splitext(fname)[1],
                parent_path=parent_dir,
                size_bytes=len(content_bytes),
                line_count=line_count,
                is_binary=is_bin,
                language=detect_file_language(fname),
                content_hash=compute_sha256(content_bytes),
            )
            files[p] = file_node
            file_count += 1

            if file_count > eff_max_files:
                raise ProjectResourceLimitError("tree_files", file_count, eff_max_files)

        return ProjectStructure(
            root_path=self.root_path,
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
        self._record_call("get_file_metadata", file_path=file_path)
        norm_path = self.validate_relative_path(file_path)
        self._trigger_simulation_hooks("get_file_metadata", norm_path, timeout_seconds, is_cancelled)

        if is_sensitive_project_path(norm_path):
            raise ProjectSecurityError(norm_path, "Access to credential/secret file is forbidden.")

        if norm_path in self.inaccessible_paths:
            raise ProjectAccessError(norm_path, "File access permission denied.")

        resolved_path, _ = self._resolve_symlink_or_path(norm_path)

        if resolved_path not in self._files:
            raise ProjectFileNotFoundError(norm_path, project_id=self.provider_id)

        fname = posixpath.basename(norm_path)
        parent_dir = posixpath.dirname(norm_path)
        raw_val = self._files[resolved_path]

        if isinstance(raw_val, str):
            raw_bytes = raw_val.encode("utf-8")
            is_bin = is_known_binary_extension(fname)
            line_cnt = len(raw_val.splitlines())
        else:
            raw_bytes = raw_val
            is_bin = True
            line_cnt = 0

        return ProjectFile(
            relative_path=norm_path,
            filename=fname,
            extension=posixpath.splitext(fname)[1],
            parent_path=parent_dir,
            size_bytes=len(raw_bytes),
            line_count=line_cnt,
            is_binary=is_bin,
            language=detect_file_language(fname),
            content_hash=compute_sha256(raw_bytes),
        )

    def get_file_content(
        self,
        file_path: str,
        line_range: Optional[LineRange] = None,
        max_bytes: Optional[int] = None,
        timeout_seconds: Optional[float] = None,
        is_cancelled: Optional[Callable[[], bool]] = None,
    ) -> ProjectSourceMaterial:
        self._record_call("get_file_content", file_path=file_path, line_range=line_range)
        norm_path = self.validate_relative_path(file_path)
        self._trigger_simulation_hooks("get_file_content", norm_path, timeout_seconds, is_cancelled)

        if is_sensitive_project_path(norm_path):
            raise ProjectSecurityError(norm_path, "Access to credential/secret file is forbidden.")

        if norm_path in self.inaccessible_paths:
            raise ProjectAccessError(norm_path, "File access permission denied.")

        if norm_path in self.malformed_paths:
            raise ProjectMalformedFileError(norm_path, "File content corrupted or invalid UTF-8 encoding.")

        resolved_path, _ = self._resolve_symlink_or_path(norm_path)

        if resolved_path not in self._files:
            raise ProjectFileNotFoundError(norm_path, project_id=self.provider_id)

        fname = posixpath.basename(norm_path)
        raw_val = self._files[resolved_path]

        if isinstance(raw_val, str):
            raw_bytes = raw_val.encode("utf-8")
            is_bin = is_known_binary_extension(fname)
        else:
            raw_bytes = raw_val
            is_bin = True

        total_size = len(raw_bytes)

        # Check file size limit
        if total_size > self._limits.max_file_size:
            raise ProjectResourceLimitError("max_file_size", total_size, self._limits.max_file_size)

        eff_max_bytes = max_bytes if max_bytes is not None else self._limits.max_file_bytes

        # Binary file handling
        if is_bin:
            if total_size > eff_max_bytes:
                raise ProjectResourceLimitError("file_bytes", total_size, eff_max_bytes)
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

        # Text file handling & line range slicing
        text_content = raw_bytes.decode("utf-8", errors="replace")
        lines = text_content.splitlines(keepends=True)

        if line_range is not None:
            total_lines = len(lines)
            start_idx = max(0, line_range.start_line - 1)
            end_idx = min(total_lines, line_range.end_line)
            selected_lines = lines[start_idx:end_idx]
            sliced_text = "".join(selected_lines)
            eff_line_range = line_range
        else:
            sliced_text = text_content
            eff_line_range = None

        sliced_text = sanitize_content_secrets(sliced_text)
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
        self._record_call("get_project_metadata")
        self._trigger_simulation_hooks("get_project_metadata", self.root_path, timeout_seconds, is_cancelled)

        # Build raw metadata from fixtures and config
        settings_raw: dict[str, Any] = {}
        if "config/settings.json" in self._files:
            try:
                settings_raw = json.loads(str(self._files["config/settings.json"]))
            except Exception:
                pass

        # Mask sensitive keys and passwords
        masked_settings = mask_sensitive_config(settings_raw)

        return {
            "project_name": posixpath.basename(self.root_path.rstrip("/")) or "sample-project",
            "root_path": self.root_path,
            "project_type": ProjectType.PYTHON.value,
            "version": "1.2.0",
            "has_manifest": "pyproject.toml" in self._files,
            "settings": masked_settings,
        }

    def set_vcs_context(self, vcs: Optional[ProjectVCSContext]) -> None:
        """Set or update the in-memory VCS context fixture."""
        self._vcs = vcs

    def get_vcs_metadata(
        self,
        allow_vcs: bool = False,
        timeout_seconds: Optional[float] = None,
        is_cancelled: Optional[Callable[[], bool]] = None,
    ) -> Optional[ProjectVCSContext]:
        self._record_call("get_vcs_metadata", allow_vcs=allow_vcs)
        self._trigger_simulation_hooks("get_vcs_metadata", self.root_path, timeout_seconds, is_cancelled)

        if not allow_vcs or self._vcs is None:
            return None

        # Sanitize remote URL
        clean_url = sanitize_url(self._vcs.remote_url) if self._vcs.remote_url else None
        return ProjectVCSContext(
            branch=self._vcs.branch,
            revision=self._vcs.revision,
            working_tree_state=self._vcs.working_tree_state,
            repository_id=self._vcs.repository_id,
            modified_files=list(self._vcs.modified_files),
            deleted_files=list(self._vcs.deleted_files),
            untracked_files=list(self._vcs.untracked_files),
            staged_files=list(self._vcs.staged_files),
            unstaged_files=list(self._vcs.unstaged_files),
            tag=self._vcs.tag,
            commit_date=self._vcs.commit_date,
            author=self._vcs.author,
            remote_url=clean_url,
            vcs_type=self._vcs.vcs_type,
            retrieved_at=self._vcs.retrieved_at,
            provenance=self._vcs.provenance,
            metadata=dict(self._vcs.metadata),
        )
