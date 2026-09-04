"""
Deterministic Mock Repository Provider (Phase 1 / Part 5 / Step 2).

Provides an in-memory, hermetic repository provider for testing, simulation, and local development.
"""
from __future__ import annotations

import hashlib
import logging
from typing import Any, Callable, Optional

from core.research.errors import (
    RepositoryAuthenticationError,
    RepositoryCancelledError,
    RepositoryFileNotFoundError,
    RepositoryNotFoundError,
    RepositoryProviderError,
    RepositoryRateLimitError,
    RepositoryResourceLimitError,
    RepositoryRevisionNotFoundError,
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
    normalize_repo_path,
)
from core.research.repo.provider import (
    RepositoryFetchLimits,
    RepositoryProvider,
)

logger = logging.getLogger("AutonomOS.Research.MockRepositoryProvider")


class MockRepositoryProvider(RepositoryProvider):
    """
    Deterministic offline repository provider supporting predictable in-memory repositories,
    revision resolution, branch/tag mapping, tree queries, file slicing, and simulation hooks.
    """

    def __init__(
        self,
        provider_id: str = "mock-repo-provider",
        name: str = "Mock Repository Provider",
        limits: Optional[RepositoryFetchLimits] = None,
        simulate_error: Optional[Exception] = None,
        simulate_timeout: bool = False,
        simulate_timeout_ops: Optional[set[str]] = None,
        simulate_cancelled: bool = False,
        simulate_not_found: bool = False,
        simulate_auth_error: bool = False,
        simulate_rate_limit: bool = False,
        simulate_security_error: bool = False,
    ):
        super().__init__(
            provider_id=provider_id,
            provider_type=RepositoryProviderType.GENERIC_GIT,
            name=name,
            limits=limits or RepositoryFetchLimits(),
        )
        self.simulate_error = simulate_error
        self.simulate_timeout = simulate_timeout
        self.simulate_timeout_ops = simulate_timeout_ops or set()
        self.simulate_cancelled = simulate_cancelled
        self.simulate_not_found = simulate_not_found
        self.simulate_auth_error = simulate_auth_error
        self.simulate_rate_limit = simulate_rate_limit
        self.simulate_security_error = simulate_security_error

        # Internal repository store:
        # key -> repo_id or URL
        self._repositories: dict[str, RepositoryIdentity] = {}
        # (repo_key, branch_name) -> commit_sha
        self._branches: dict[tuple[str, str], str] = {}
        # (repo_key, tag_name) -> commit_sha
        self._tags: dict[tuple[str, str], str] = {}
        # (repo_key, commit_sha) -> RepositoryRevision
        self._revisions: dict[tuple[str, str], RepositoryRevision] = {}
        # (repo_key, commit_sha) -> RepositoryTree
        self._trees: dict[tuple[str, str], RepositoryTree] = {}
        # (repo_key, commit_sha, file_path) -> (content_str, raw_bytes, optional_language)
        self._files: dict[tuple[str, str, str], tuple[str, bytes, Optional[str]]] = {}

        # Call counters for inspection
        self.calls_count: int = 0
        self.history: list[dict[str, Any]] = []

    # -------------------------------------------------------------------------
    # Fixture Registration Helpers
    # -------------------------------------------------------------------------

    def register_repository(
        self,
        identity: RepositoryIdentity,
        default_branch: str = "main",
    ) -> None:
        """Register a repository identity in the in-memory store."""
        if not isinstance(identity, RepositoryIdentity):
            raise RepositoryValidationError("identity", "Must be a RepositoryIdentity instance.")
        key = identity.repo_id
        self._repositories[key] = identity
        self._repositories[identity.url] = identity
        if identity.full_name:
            self._repositories[identity.full_name] = identity

    def register_revision(
        self,
        repo_target: str,
        revision: RepositoryRevision,
        branch: Optional[str] = None,
        tag: Optional[str] = None,
    ) -> None:
        """Register a commit revision and optional branch/tag pointers."""
        repo_key = self._resolve_repo_key(repo_target)
        sha = revision.commit_sha or hashlib.sha256(f"{repo_key}:{branch or tag or 'main'}".encode()).hexdigest()[:40]
        if not revision.commit_sha:
            revision.commit_sha = sha
        if branch and not revision.branch:
            revision.branch = branch
        if tag and not revision.tag:
            revision.tag = tag

        self._revisions[(repo_key, sha)] = revision
        if branch:
            self._branches[(repo_key, branch)] = sha
        if tag:
            self._tags[(repo_key, tag)] = sha
        if revision.branch:
            self._branches[(repo_key, revision.branch)] = sha
        if revision.tag:
            self._tags[(repo_key, revision.tag)] = sha

    def register_file(
        self,
        repo_target: str,
        revision_str: str,
        file_path: str,
        content: str | bytes,
        language: Optional[str] = None,
    ) -> None:
        """Register file content at a specific revision and automatically index into the revision tree."""
        repo_key = self._resolve_repo_key(repo_target)
        norm_path = normalize_repo_path(file_path)
        sha = self._resolve_sha(repo_key, revision_str)
        if sha == "0000000000000000000000000000000000000000":
            sha = hashlib.sha256(f"{repo_key}:{revision_str or 'main'}".encode()).hexdigest()[:40]

        # Auto-register revision and branch if not already present
        if (repo_key, sha) not in self._revisions:
            rev_obj = RepositoryRevision(
                commit_sha=sha,
                branch=revision_str if revision_str and not revision_str.startswith("v") else "main",
                tag=revision_str if revision_str and revision_str.startswith("v") else None,
            )
            self.register_revision(repo_key, rev_obj, branch=rev_obj.branch, tag=rev_obj.tag)

        if isinstance(content, str):
            content_str = content
            raw_bytes = content.encode("utf-8")
        else:
            raw_bytes = content
            try:
                content_str = content.decode("utf-8")
            except UnicodeDecodeError:
                content_str = ""

        lang = language or detect_file_language(norm_path)
        self._files[(repo_key, sha, norm_path)] = (content_str, raw_bytes, lang)

        # Update or create tree for this revision
        if (repo_key, sha) not in self._trees:
            self._trees[(repo_key, sha)] = RepositoryTree()
        tree = self._trees[(repo_key, sha)]

        line_count = len(content_str.splitlines()) if content_str else 0
        repo_file = RepositoryFile(
            path=norm_path,
            filename=norm_path.split("/")[-1],
            extension=f".{norm_path.split('.')[-1]}" if "." in norm_path.split("/")[-1] else "",
            parent_path="/".join(norm_path.split("/")[:-1]) if "/" in norm_path else "",
            size_bytes=len(raw_bytes),
            line_count=line_count,
            is_binary=isinstance(content, bytes) and not content_str,
            language=lang,
            content_checksum=compute_sha256(raw_bytes),
        )
        tree.add_file(repo_file)

    def register_tree(
        self,
        repo_target: str,
        revision_str: str,
        tree: RepositoryTree,
    ) -> None:
        """Explicitly register a complete repository tree for a revision."""
        repo_key = self._resolve_repo_key(repo_target)
        sha = self._resolve_sha(repo_key, revision_str)
        if sha == "0000000000000000000000000000000000000000":
            sha = hashlib.sha256(f"{repo_key}:{revision_str or 'main'}".encode()).hexdigest()[:40]

        if (repo_key, sha) not in self._revisions:
            rev_obj = RepositoryRevision(
                commit_sha=sha,
                branch=revision_str if revision_str and not revision_str.startswith("v") else "main",
                tag=revision_str if revision_str and revision_str.startswith("v") else None,
            )
            self.register_revision(repo_key, rev_obj, branch=rev_obj.branch, tag=rev_obj.tag)

        self._trees[(repo_key, sha)] = tree

    # -------------------------------------------------------------------------
    # Internal Resolution Utilities
    # -------------------------------------------------------------------------

    def _resolve_repo_key(self, repo_target: RepositoryIdentity | str) -> str:
        if isinstance(repo_target, RepositoryIdentity):
            return repo_target.repo_id
        target = str(repo_target).strip()
        if target in self._repositories:
            return self._repositories[target].repo_id
        # Check by matching suffix or repo_id
        for k, ident in self._repositories.items():
            if target == ident.repo_id or target == ident.url or target == ident.full_name:
                return ident.repo_id
        return target

    def _resolve_sha(self, repo_key: str, revision_str: Optional[str]) -> str:
        if not revision_str:
            # Fallback to default branch
            ident = self._repositories.get(repo_key)
            default_branch = (ident.default_branch if ident else None) or "main"
            if (repo_key, default_branch) in self._branches:
                return self._branches[(repo_key, default_branch)]
            if (repo_key, "master") in self._branches:
                return self._branches[(repo_key, "master")]
            # Look for any revision
            for (rk, rev_sha) in self._revisions.keys():
                if rk == repo_key:
                    return rev_sha
            return "0000000000000000000000000000000000000000"

        # Check direct commit sha
        if (repo_key, revision_str) in self._revisions:
            return revision_str
        # Check branch
        if (repo_key, revision_str) in self._branches:
            return self._branches[(repo_key, revision_str)]
        # Check tag
        if (repo_key, revision_str) in self._tags:
            return self._tags[(repo_key, revision_str)]

        return revision_str

    def _evaluate_simulations(self, op: str, repo_target: str, timeout_seconds: Optional[float]) -> None:
        self.calls_count += 1
        self.history.append({"op": op, "repo_target": repo_target})

        if self.simulate_security_error:
            from core.research.errors import RepositorySecurityError
            raise RepositorySecurityError(repo_target, f"Simulated security violation in operation '{op}'.")

        if self.simulate_auth_error:
            raise RepositoryAuthenticationError(repo_target, "Simulated authentication credentials rejected.")

        if self.simulate_rate_limit:
            raise RepositoryRateLimitError(self.provider_id, retry_after_seconds=30.0)

        if self.simulate_cancelled:
            raise RepositoryCancelledError(repo_target=repo_target, operation=op)

        if self.simulate_timeout or (op in self.simulate_timeout_ops):
            effective_timeout = timeout_seconds if timeout_seconds is not None else self.limits.timeout_seconds
            raise RepositoryTimeoutError(repo_target=repo_target, operation=op, timeout_seconds=effective_timeout)

        if self.simulate_error:
            raise self.simulate_error

        if self.simulate_not_found:
            raise RepositoryNotFoundError(repo_target, "Simulated repository not found.")

    # -------------------------------------------------------------------------
    # RepositoryProvider Contract Implementation
    # -------------------------------------------------------------------------

    def resolve_identity(
        self,
        target: str,
        timeout_seconds: Optional[float] = None,
        is_cancelled: Optional[Callable[[], bool]] = None,
    ) -> RepositoryIdentity:
        target_str = self.extract_repo_target(target)
        self.check_cancellation(is_cancelled, target_str, "resolve_identity")
        self._evaluate_simulations("resolve_identity", target_str, timeout_seconds)

        repo_key = self._resolve_repo_key(target_str)
        if repo_key in self._repositories:
            return self._repositories[repo_key]

        # Try building from URL if plausible
        if target_str.startswith(("http://", "https://", "file://", "git@")):
            ident = RepositoryIdentity.from_url(target_str)
            return ident

        raise RepositoryNotFoundError(target_str, "Repository does not exist in mock store.")

    def get_metadata(
        self,
        repo: RepositoryIdentity | str,
        timeout_seconds: Optional[float] = None,
        is_cancelled: Optional[Callable[[], bool]] = None,
    ) -> RepositoryIdentity:
        target_str = self.extract_repo_target(repo)
        self.check_cancellation(is_cancelled, target_str, "get_metadata")
        self._evaluate_simulations("get_metadata", target_str, timeout_seconds)

        repo_key = self._resolve_repo_key(target_str)
        if repo_key in self._repositories:
            return self._repositories[repo_key]

        raise RepositoryNotFoundError(target_str, "Repository metadata not found.")

    def get_revision(
        self,
        repo: RepositoryIdentity | str,
        revision: Optional[str] = None,
        timeout_seconds: Optional[float] = None,
        is_cancelled: Optional[Callable[[], bool]] = None,
    ) -> RepositoryRevision:
        target_str = self.extract_repo_target(repo)
        self.check_cancellation(is_cancelled, target_str, "get_revision")
        self._evaluate_simulations("get_revision", target_str, timeout_seconds)

        repo_key = self._resolve_repo_key(target_str)
        if repo_key not in self._repositories and not any(k == repo_key for (k, _) in self._revisions.keys()):
            raise RepositoryNotFoundError(target_str)

        sha = self._resolve_sha(repo_key, revision)
        if (repo_key, sha) in self._revisions:
            return self._revisions[(repo_key, sha)]

        if revision and revision != sha and (repo_key, revision) in self._revisions:
            return self._revisions[(repo_key, revision)]

        if revision:
            raise RepositoryRevisionNotFoundError(target_str, revision)

        # Fallback synthesized revision for registered repository
        ident = self._repositories.get(repo_key)
        branch = ident.default_branch if ident else "main"
        return RepositoryRevision(
            commit_sha=sha,
            branch=branch,
            version_context=RepoVersionContext.from_branch(branch) if branch else RepoVersionContext.unknown(),
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
        self._evaluate_simulations("get_tree", target_str, timeout_seconds)

        repo_key = self._resolve_repo_key(target_str)
        if repo_key not in self._repositories and not any(k == repo_key for (k, _) in self._revisions.keys()):
            raise RepositoryNotFoundError(target_str)

        sha = self._resolve_sha(repo_key, revision)
        if revision and (repo_key, sha) not in self._revisions and (repo_key, sha) not in self._trees:
            raise RepositoryRevisionNotFoundError(target_str, revision)

        full_tree = self._trees.get((repo_key, sha), RepositoryTree())
        effective_tree = RepositoryTree()
        norm_sub = normalize_repo_path(subpath) if subpath else ""

        eff_max_depth = max_depth if max_depth is not None else self.limits.max_tree_depth
        eff_max_files = max_files if max_files is not None else self.limits.max_tree_files

        for dir_obj in full_tree.directories:
            dpath = dir_obj.path
            if norm_sub and not (dpath == norm_sub or dpath.startswith(norm_sub + "/")):
                continue
            depth = len(dpath.split("/")) if dpath else 0
            if depth > eff_max_depth:
                raise RepositoryResourceLimitError("tree_depth", depth, eff_max_depth)
            effective_tree.add_directory(dir_obj)

        for file_obj in full_tree.files:
            fpath = file_obj.path
            if norm_sub and not (fpath == norm_sub or fpath.startswith(norm_sub + "/")):
                continue
            depth = len(fpath.split("/")) if fpath else 0
            if depth > eff_max_depth:
                raise RepositoryResourceLimitError("tree_depth", depth, eff_max_depth)
            if len(effective_tree.files) >= eff_max_files:
                raise RepositoryResourceLimitError("tree_files", len(effective_tree.files) + 1, eff_max_files)
            effective_tree.add_file(file_obj)

        return effective_tree

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
        self._evaluate_simulations("get_file_content", target_str, timeout_seconds)

        repo_key = self._resolve_repo_key(target_str)
        if repo_key not in self._repositories and not any(k == repo_key for (k, _) in self._revisions.keys()):
            raise RepositoryNotFoundError(target_str)

        sha = self._resolve_sha(repo_key, revision)
        if revision and (repo_key, sha) not in self._revisions and (repo_key, sha) not in self._trees and (repo_key, sha, normalize_repo_path(file_path)) not in self._files:
            raise RepositoryRevisionNotFoundError(target_str, revision)

        norm_path = normalize_repo_path(file_path)
        file_tuple = self._files.get((repo_key, sha, norm_path))

        if not file_tuple:
            raise RepositoryFileNotFoundError(target_str, norm_path, revision=revision)

        content_str, raw_bytes, lang = file_tuple
        eff_max_bytes = max_bytes if max_bytes is not None else self.limits.max_file_bytes

        if len(raw_bytes) > eff_max_bytes:
            raise RepositoryResourceLimitError("file_bytes", len(raw_bytes), eff_max_bytes)

        # Apply LineRange slicing if specified
        final_content = content_str
        final_bytes = raw_bytes
        if line_range is not None:
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

        # Resolve identity & revision objects for provenance
        ident = self._repositories.get(repo_key) or RepositoryIdentity(
            repo_id=repo_key,
            url=target_str if target_str.startswith("http") else f"https://github.com/{repo_key}",
            provider_type=self.provider_type,
        )
        rev_obj = self._revisions.get((repo_key, sha)) or RepositoryRevision(
            commit_sha=sha,
            branch="main",
            version_context=RepoVersionContext.from_commit(sha),
        )

        snippet_id = RepositorySourceMaterial.generate_snippet_id(repo_key, rev_obj, norm_path, line_range)
        checksum = compute_sha256(final_bytes)

        return RepositorySourceMaterial(
            snippet_id=snippet_id,
            repository_identity=ident,
            revision=rev_obj,
            file_path=norm_path,
            line_range=line_range,
            content=final_content,
            raw_bytes=final_bytes,
            content_checksum=checksum,
            language=lang or detect_file_language(norm_path),
        )
