"""
Project Version Control & Workspace State Inspector (Phase 1 / Part 8 / Step 7).

Provides safe, explicitly scoped, read-only inspection of version-control/project state:
- Current branch
- Current revision / commit hash
- Repository identity
- Modified files
- Deleted files
- Untracked files
- Staged vs unstaged state
- Clean / dirty / untracked status

Strict Safety Invariants:
- Strictly read-only operations (git status --porcelain=v1, git rev-parse, git config, git log -1).
- ZERO modification of workspace files or VCS state (no add, commit, checkout, pull, push, reset).
- Zero execution of arbitrary VCS commands from project content.
- Explicit containment within project root directory.
- Sensitive remote URL sanitization (credentials/tokens stripped via sanitize_url).
- Fallback to safe direct file inspection (.git/HEAD, .git/refs, .git/config) if git CLI is unavailable or fails.
- Cooperative cancellation and timeout enforcement.
"""
from __future__ import annotations

import configparser
from dataclasses import dataclass, field
import logging
import os
import posixpath
import re
import subprocess
import time
from typing import Any, Callable, Optional, Sequence

from core.research.contracts.evidence import EvidenceProvenance
from core.research.errors import (
    ProjectCancelledError,
    ProjectSecurityError,
    ProjectTimeoutError,
    ProjectValidationError,
)
from core.research.project.models import (
    ProjectVCSContext,
    ProjectVCSState,
    normalize_project_path,
    utc_now,
)
from core.research.search.security import sanitize_error, sanitize_url

logger = logging.getLogger("AutonomOS.Research.ProjectVCS")


@dataclass
class ProjectVCSOptions:
    """Configuration options and resource bounds for project VCS inspection."""
    allow_vcs: bool = False
    timeout_seconds: float = 10.0
    max_changed_files: int = 500
    is_cancelled: Optional[Callable[[], bool]] = None


class ProjectVCSInspector:
    """
    Deterministic read-only inspector for project version control state.
    Scopes all queries strictly to project root, enforces timeouts and cancellation,
    and provides fallback to passive file parsing when git CLI is unavailable.
    """

    @classmethod
    def inspect(
        cls,
        project_root: str,
        options: Optional[ProjectVCSOptions] = None,
        provenance: Optional[EvidenceProvenance] = None,
    ) -> Optional[ProjectVCSContext]:
        """
        Inspect the version control and current working tree state of a project workspace.
        Returns None if allow_vcs is False.
        """
        opts = options or ProjectVCSOptions()
        if not opts.allow_vcs:
            return None

        # 1. Cancellation check
        if opts.is_cancelled and opts.is_cancelled():
            raise ProjectCancelledError(
                operation="inspect_vcs",
                target=project_root,
                message="VCS inspection cancelled before execution.",
            )

        if not project_root or not isinstance(project_root, str) or not os.path.isdir(project_root):
            return ProjectVCSContext(
                working_tree_state=ProjectVCSState.UNKNOWN,
                vcs_type="none",
                provenance=provenance,
                metadata={"error": f"Invalid project root directory: '{project_root}'"},
            )

        abs_root = os.path.abspath(project_root)
        git_dir = os.path.join(abs_root, ".git")

        if not os.path.exists(git_dir):
            return ProjectVCSContext(
                working_tree_state=ProjectVCSState.UNKNOWN,
                vcs_type="none",
                provenance=provenance,
                metadata={"reason": "Not a git repository (no .git directory)."},
            )

        start_time = time.perf_counter()

        # 2. Attempt git CLI inspection first
        try:
            return cls._inspect_via_git_cli(
                project_root=abs_root,
                options=opts,
                start_time=start_time,
                provenance=provenance,
            )
        except (subprocess.SubprocessError, FileNotFoundError, PermissionError) as e:
            logger.debug(f"Git CLI inspection unavailable for '{abs_root}': {e}. Falling back to direct file inspection.")
            return cls._inspect_via_file_fallback(
                project_root=abs_root,
                git_dir=git_dir,
                options=opts,
                provenance=provenance,
            )
        except ProjectTimeoutError:
            raise
        except ProjectCancelledError:
            raise
        except Exception as err:
            logger.warning(f"Unexpected error inspecting VCS for '{abs_root}': {err}")
            return cls._inspect_via_file_fallback(
                project_root=abs_root,
                git_dir=git_dir,
                options=opts,
                provenance=provenance,
                error_context=str(err),
            )

    @classmethod
    def _inspect_via_git_cli(
        cls,
        project_root: str,
        options: ProjectVCSOptions,
        start_time: float,
        provenance: Optional[EvidenceProvenance],
    ) -> ProjectVCSContext:
        """
        Perform read-only git command queries strictly within project_root.
        """
        def check_limits():
            if options.is_cancelled and options.is_cancelled():
                raise ProjectCancelledError(
                    operation="inspect_vcs",
                    target=project_root,
                    message="VCS inspection cancelled during command execution.",
                )
            elapsed = time.perf_counter() - start_time
            if elapsed > options.timeout_seconds:
                raise ProjectTimeoutError(
                    operation="inspect_vcs",
                    timeout_seconds=options.timeout_seconds,
                    target=project_root,
                )

        check_limits()
        remaining_time = max(0.1, options.timeout_seconds - (time.perf_counter() - start_time))

        def run_cmd(args: list[str]) -> tuple[int, str, str]:
            check_limits()
            rem = max(0.1, options.timeout_seconds - (time.perf_counter() - start_time))
            try:
                res = subprocess.run(
                    args,
                    cwd=project_root,
                    capture_output=True,
                    text=True,
                    timeout=rem,
                    check=False,
                    shell=False,
                )
                return res.returncode, res.stdout, res.stderr.strip()
            except subprocess.TimeoutExpired:
                raise ProjectTimeoutError(
                    operation=f"git {' '.join(args[1:])}",
                    timeout_seconds=options.timeout_seconds,
                    target=project_root,
                )

        # 1. Branch
        code, out, _ = run_cmd(["git", "rev-parse", "--abbrev-ref", "HEAD"])
        branch: Optional[str] = None
        out_s = out.strip()
        if code == 0 and out_s:
            branch = out_s if out_s != "HEAD" else None

        # 2. Revision
        code, out, _ = run_cmd(["git", "rev-parse", "HEAD"])
        revision: Optional[str] = None
        out_s = out.strip()
        if code == 0 and out_s and len(out_s) == 40:
            revision = out_s

        # 3. Remote URL
        code, out, _ = run_cmd(["git", "config", "--get", "remote.origin.url"])
        remote_url: Optional[str] = None
        repository_id: Optional[str] = None
        out_s = out.strip()
        if code == 0 and out_s:
            remote_url = sanitize_url(out_s)
            repository_id = cls._derive_repository_id(remote_url, project_root)
        else:
            repository_id = os.path.basename(project_root)

        # 4. Commit Date and Author
        code, out, _ = run_cmd(["git", "log", "-1", "--format=%cI|%an"])
        commit_date: Optional[str] = None
        author: Optional[str] = None
        out_s = out.strip()
        if code == 0 and out_s and "|" in out_s:
            parts = out_s.split("|", 1)
            commit_date = parts[0].strip() or None
            author = parts[1].strip() or None

        # 5. Status: modified, deleted, untracked, staged, unstaged
        code, out, err = run_cmd(["git", "status", "--porcelain=v1", "--untracked-files=all"])
        if code != 0:
            logger.warning(f"git status returned non-zero code {code}: {err}")
            return cls._inspect_via_file_fallback(
                project_root=project_root,
                git_dir=os.path.join(project_root, ".git"),
                options=options,
                provenance=provenance,
                error_context=err,
            )

        modified_files: list[str] = []
        deleted_files: list[str] = []
        untracked_files: list[str] = []
        staged_files: list[str] = []
        unstaged_files: list[str] = []

        total_files_counted = 0
        is_truncated = False

        for line in out.splitlines():
            if not line or len(line) < 3:
                continue

            x = line[0]
            y = line[1]
            raw_path = line[2:].lstrip()

            # Handle rename (PATH1 -> PATH2)
            if " -> " in raw_path:
                raw_path = raw_path.split(" -> ")[1].strip()

            # Strip possible surrounding quotes
            raw_path = raw_path.strip("\"'")

            # Project root containment verification
            try:
                norm_rel = normalize_project_path(raw_path)
                full_p = os.path.abspath(os.path.join(project_root, norm_rel))
                if not (full_p == project_root or full_p.startswith(project_root + os.sep)):
                    logger.warning(f"Rejected out-of-root VCS path: '{raw_path}'")
                    continue
            except Exception:
                continue

            if total_files_counted >= options.max_changed_files:
                is_truncated = True
                break

            total_files_counted += 1

            # Untracked files
            if x == "?" and y == "?":
                untracked_files.append(norm_rel)
                continue

            # Staged files (index state)
            if x in ("M", "A", "D", "R", "C"):
                staged_files.append(norm_rel)

            # Unstaged files (worktree state)
            if y in ("M", "D"):
                unstaged_files.append(norm_rel)

            # Modified files
            if x == "M" or y == "M" or x == "A":
                modified_files.append(norm_rel)

            # Deleted files
            if x == "D" or y == "D":
                deleted_files.append(norm_rel)

        # Determine overall working tree state
        if modified_files or deleted_files or staged_files:
            working_tree_state = ProjectVCSState.DIRTY
        elif untracked_files:
            working_tree_state = ProjectVCSState.UNTRACKED
        else:
            working_tree_state = ProjectVCSState.CLEAN

        return ProjectVCSContext(
            branch=branch,
            revision=revision,
            working_tree_state=working_tree_state,
            repository_id=repository_id,
            modified_files=modified_files,
            deleted_files=deleted_files,
            untracked_files=untracked_files,
            staged_files=staged_files,
            unstaged_files=unstaged_files,
            commit_date=commit_date,
            author=author,
            remote_url=remote_url,
            vcs_type="git",
            retrieved_at=utc_now(),
            provenance=provenance,
            metadata={
                "truncated": is_truncated,
                "total_changed_count": total_files_counted,
            },
        )

    @classmethod
    def _inspect_via_file_fallback(
        cls,
        project_root: str,
        git_dir: str,
        options: ProjectVCSOptions,
        provenance: Optional[EvidenceProvenance],
        error_context: Optional[str] = None,
    ) -> ProjectVCSContext:
        """
        Passive fallback inspecting git metadata directly from .git directory files.
        Guarantees zero shell/subprocess execution.
        """
        branch: Optional[str] = None
        revision: Optional[str] = None
        remote_url: Optional[str] = None
        repository_id: Optional[str] = os.path.basename(project_root)
        working_state = ProjectVCSState.UNKNOWN
        metadata: dict[str, Any] = {"file_fallback": True}

        if error_context:
            metadata["cli_error"] = error_context

        # 1. Read .git/HEAD
        head_file = os.path.join(git_dir, "HEAD")
        if os.path.isfile(head_file):
            try:
                with open(head_file, "r", encoding="utf-8") as f:
                    head_line = f.read().strip()
                if head_line.startswith("ref: refs/heads/"):
                    branch = head_line[len("ref: refs/heads/"):]
                    ref_file = os.path.join(git_dir, "refs", "heads", branch)
                    if os.path.isfile(ref_file):
                        with open(ref_file, "r", encoding="utf-8") as rf:
                            revision = rf.read().strip()
                elif len(head_line) == 40:
                    revision = head_line
            except Exception as e:
                metadata["head_read_error"] = str(e)

        # 2. Read .git/config
        config_file = os.path.join(git_dir, "config")
        if os.path.isfile(config_file):
            try:
                cp = configparser.ConfigParser()
                cp.read(config_file)
                for sec in cp.sections():
                    if sec.startswith('remote "') or sec == "remote":
                        raw_url = cp.get(sec, "url", fallback="")
                        if raw_url:
                            remote_url = sanitize_url(raw_url)
                            repository_id = cls._derive_repository_id(remote_url, project_root)
                            break
            except Exception as e:
                metadata["config_read_error"] = str(e)

        return ProjectVCSContext(
            branch=branch,
            revision=revision,
            working_tree_state=working_state,
            repository_id=repository_id,
            remote_url=remote_url,
            vcs_type="git",
            retrieved_at=utc_now(),
            provenance=provenance,
            metadata=metadata,
        )

    @classmethod
    def _derive_repository_id(cls, remote_url: Optional[str], project_root: str) -> str:
        """Derive repository identifier from remote URL or fallback to project root folder name."""
        if remote_url:
            clean = remote_url.rstrip("/")
            if clean.endswith(".git"):
                clean = clean[:-4]
            # Match domain.com/org/repo -> org/repo
            match = re.search(r"[:/]([a-zA-Z0-9_.-]+/[a-zA-Z0-9_.-]+)$", clean)
            if match:
                return match.group(1)
            parts = clean.split("/")
            if parts and parts[-1]:
                return parts[-1]
        return os.path.basename(project_root) or "project"
