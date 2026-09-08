from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import json
import os
from pathlib import Path, PurePosixPath
import re
from typing import Any, Optional, Sequence, Union

from core.programmer.contracts.codebase_understanding import (
    CodebaseUnderstanding,
    UnderstandingInsight,
)
from core.programmer.contracts.command_record import CommandExecutionRecord
from core.programmer.contracts.command_resolver import (
    CommandBoundaryResolver,
    CommandRequest,
)
from core.programmer.contracts.execution_context import ProgrammerExecutionContext
from core.programmer.contracts.filesystem_resolver import (
    FilesystemBoundaryResolver,
    FilesystemDecision,
)
from core.programmer.contracts.git_model import GitExecutionContext
from core.programmer.contracts.identifiers import (
    new_codebase_understanding_id,
    new_verification_evidence_id,
)
from core.programmer.contracts.verification import VerificationEvidence
from core.programmer.contracts.work_order import ProgrammerWorkOrder
from core.programmer.contracts.workspace import ProgrammerWorkspace
from core.programmer.types import (
    FilesystemOperation,
    UnderstandingConfidence,
    VerificationEvidenceSourceType,
)


def utc_now() -> str:
    """Generate ISO 8601 UTC timestamp."""
    return datetime.now(timezone.utc).isoformat()


# Common noise directories to skip during bounded directory walking
IGNORE_DIRECTORIES = {
    ".git",
    "node_modules",
    "__pycache__",
    ".venv",
    "venv",
    "dist",
    "build",
    ".tox",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    "coverage",
    ".idea",
    ".vscode",
    ".egg-info",
}

# Known framework signatures mapped from dependency names or imports
FRAMEWORK_SIGNATURES: dict[str, str] = {
    "fastapi": "fastapi",
    "flask": "flask",
    "django": "django",
    "express": "express",
    "react": "react",
    "next": "nextjs",
    "vue": "vue",
    "angular": "angular",
    "gin": "gin",
    "fiber": "fiber",
    "actix-web": "actix-web",
    "axum": "axum",
    "tokio": "tokio",
    "spring": "spring",
    "rails": "rails",
}

# Stop words to filter from objective for relevant module extraction
OBJECTIVE_STOPWORDS = {
    "a", "an", "the", "and", "or", "in", "on", "at", "to", "for", "with",
    "by", "from", "as", "is", "are", "was", "were", "be", "been", "being",
    "have", "has", "had", "do", "does", "did", "implement", "create", "add",
    "update", "fix", "modify", "change", "refactor", "support", "ensure",
    "verify", "test", "should", "must", "can", "will", "all", "each", "every",
}


class CodebaseExplorer:
    """
    Focused, bounded engineering-intelligence explorer for Programmer V1 (Phase 7.1).
    
    Explores an unfamiliar repository to construct a strongly-typed, epistemically-calibrated
    CodebaseUnderstanding model prior to implementation.
    
    Core Invariants:
    1. Bounded strictly by Programmer ExecutionContext and authorized filesystem boundaries.
    2. Read-only: strictly zero filesystem mutations or deletions.
    3. Epistemic calibration:
       - OBSERVED: directly found on disk.
       - INFERRED: reasonable deduction from observed facts.
       - UNKNOWN: insufficient evidence.
       Never represents inference as fact.
    4. Objective-driven: focuses on modules relevant to the WorkOrder objective.
    5. Command safety: command queries must be authorized by CommandBoundaryResolver.
    """

    def __init__(
        self,
        fs_resolver: Optional[FilesystemBoundaryResolver] = None,
        command_resolver: Optional[CommandBoundaryResolver] = None,
        max_depth: int = 3,
        max_files_inspected: int = 100,
    ) -> None:
        self.fs_resolver = fs_resolver or FilesystemBoundaryResolver()
        self.command_resolver = command_resolver or CommandBoundaryResolver()
        self.max_depth = max_depth
        self.max_files_inspected = max_files_inspected

    def explore(
        self,
        workspace: ProgrammerWorkspace,
        work_order: ProgrammerWorkOrder,
        execution_id: Optional[str] = None,
        repository_id: Optional[str] = None,
        trace: Optional[dict[str, Any]] = None,
    ) -> CodebaseUnderstanding:
        """
        Execute focused exploration of workspace driven by work_order constraints and objective.
        """
        tr = dict(trace or {})
        exec_id = execution_id or "pexec-exploration-default"
        wo_id = work_order.work_order_id
        proj_id = work_order.project_id
        repo_root = os.path.abspath(workspace.root_path)

        insights: list[UnderstandingInsight] = []
        evidence_list: list[VerificationEvidence] = []
        uncertainties: list[str] = []
        important_dirs: list[str] = []
        entry_points: list[str] = []
        config_files: list[str] = []
        test_locations: list[str] = []
        manifests: list[str] = []
        relevant_modules: list[str] = []
        conventions: dict[str, Any] = {}
        languages: list[str] = []
        frameworks: list[str] = []
        package_managers: list[str] = []
        structure_dict: dict[str, Any] = {}
        project_type: str = "unknown"
        project_type_confidence: UnderstandingConfidence = UnderstandingConfidence.UNKNOWN

        # Helper to check if reading a relative path is allowed
        def is_read_allowed(rel_path: str) -> tuple[bool, str]:
            norm_rel, err = self.fs_resolver.normalize_path(workspace, rel_path)
            if err or norm_rel is None:
                return False, err or "Normalization failed"
            decision: FilesystemDecision = self.fs_resolver.resolve(
                workspace=workspace,
                requested_path=norm_rel,
                operation=FilesystemOperation.READ,
            )
            return decision.allowed, decision.reason

        # Helper to record evidence
        def add_evidence(desc: str, data: dict[str, Any]) -> str:
            ev_id = new_verification_evidence_id()
            ev = VerificationEvidence(
                evidence_id=ev_id,
                execution_id=exec_id,
                work_order_id=wo_id,
                source_type=VerificationEvidenceSourceType.CODEBASE_EXPLORATION,
                description=desc,
                data=data,
                is_agent_claim=False,
            )
            evidence_list.append(ev)
            return ev_id

        # ---------------------------------------------------------------------
        # Stage 1: Repository Structure
        # ---------------------------------------------------------------------
        top_dirs: list[str] = []
        top_files: list[str] = []
        root_allowed, root_reason = is_read_allowed(".")
        if not root_allowed:
            uncertainties.append(f"Repository root read blocked by policy: {root_reason}")
            structure_dict["status"] = "ACCESS_DENIED"
            for ap in work_order.allowed_paths:
                if ap not in (".", "*", ""):
                    ap_allowed, _ = is_read_allowed(ap)
                    if ap_allowed and os.path.exists(os.path.join(repo_root, ap)):
                        important_dirs.append(ap)
        else:
            try:
                top_entries = sorted(os.listdir(repo_root))
            except Exception as e:
                top_entries = []
                uncertainties.append(f"Failed to list repository root: {e}")

            top_dirs = [e for e in top_entries if os.path.isdir(os.path.join(repo_root, e)) and e not in IGNORE_DIRECTORIES]
            top_files = [e for e in top_entries if os.path.isfile(os.path.join(repo_root, e))]

            # Identify monorepo vs single package
            has_packages_dir = "packages" in top_dirs or "apps" in top_dirs or "modules" in top_dirs
            if has_packages_dir:
                structure_type = "monorepo"
                insights.append(UnderstandingInsight(
                    category="structure",
                    key="monorepo",
                    confidence=UnderstandingConfidence.OBSERVED,
                    value="monorepo",
                    source_path=".",
                    rationale="Observed packages/ or apps/ multi-package directory layout at repository root",
                ))
            else:
                structure_type = "single_package"
                insights.append(UnderstandingInsight(
                    category="structure",
                    key="single_package",
                    confidence=UnderstandingConfidence.OBSERVED,
                    value="single_package",
                    source_path=".",
                    rationale="Standard single package repository structure",
                ))

            structure_dict["type"] = structure_type
            structure_dict["top_level_directories"] = top_dirs
            structure_dict["top_level_files"] = top_files

            # Important directory identification
            candidate_important = ["src", "lib", "app", "tests", "test", "spec", "packages", "apps", "cmd", "internal", "api", "core"]
            for d in candidate_important:
                if d in top_dirs:
                    d_allowed, _ = is_read_allowed(d)
                    if d_allowed:
                        important_dirs.append(d)
                        ev_id = add_evidence(f"Observed important directory '{d}'", {"directory": d})
                        insights.append(UnderstandingInsight(
                            category="directory",
                            key=d,
                            confidence=UnderstandingConfidence.OBSERVED,
                            value=d,
                            source_path=d,
                            rationale=f"Observed key source/test directory '{d}' on disk",
                            evidence_id=ev_id,
                        ))
                    else:
                        uncertainties.append(f"Important directory '{d}' exists but is restricted by policy")

        # ---------------------------------------------------------------------
        # Stage 2 & 3: Project Metadata & Dependency Manifests
        # ---------------------------------------------------------------------
        metadata_manifest_candidates = [
            ("package.json", "node"),
            ("pyproject.toml", "python"),
            ("setup.py", "python"),
            ("setup.cfg", "python"),
            ("requirements.txt", "python"),
            ("Pipfile", "python"),
            ("poetry.lock", "python"),
            ("Cargo.toml", "rust"),
            ("Cargo.lock", "rust"),
            ("go.mod", "go"),
            ("go.sum", "go"),
            ("pom.xml", "java"),
            ("build.gradle", "java"),
            ("Gemfile", "ruby"),
            ("composer.json", "php"),
        ]

        found_manifests: list[str] = []
        for filename, lang in metadata_manifest_candidates:
            cand_path = os.path.join(repo_root, filename)
            if os.path.exists(cand_path):
                allowed, reason = is_read_allowed(filename)
                if not allowed:
                    uncertainties.append(f"Manifest '{filename}' exists but is forbidden by policy: {reason}")
                    continue

                manifests.append(filename)
                found_manifests.append(filename)
                ev_id = add_evidence(f"Discovered manifest '{filename}'", {"path": filename, "language": lang})
                insights.append(UnderstandingInsight(
                    category="manifest",
                    key=filename,
                    confidence=UnderstandingConfidence.OBSERVED,
                    value=filename,
                    source_path=filename,
                    rationale=f"Directly observed manifest file '{filename}' on disk",
                    evidence_id=ev_id,
                ))

                # Language observation
                if lang not in languages:
                    languages.append(lang)
                    insights.append(UnderstandingInsight(
                        category="language",
                        key=lang,
                        confidence=UnderstandingConfidence.OBSERVED,
                        value=lang,
                        source_path=filename,
                        rationale=f"Directly verified language '{lang}' from presence of manifest '{filename}'",
                        evidence_id=ev_id,
                    ))

                # Deep parse of manifest content where practical
                try:
                    with open(cand_path, "r", encoding="utf-8", errors="replace") as mf:
                        content = mf.read()

                    # Package manager detection
                    if filename == "package.json":
                        try:
                            pkg_data = json.loads(content)
                            if "npm" not in package_managers:
                                package_managers.append("npm")
                            pkg_deps = {**pkg_data.get("dependencies", {}), **pkg_data.get("devDependencies", {})}
                            for dep_name in pkg_deps:
                                for sig_dep, sig_fw in FRAMEWORK_SIGNATURES.items():
                                    if sig_dep == dep_name or dep_name.startswith(f"@{sig_dep}/"):
                                        if sig_fw not in frameworks:
                                            frameworks.append(sig_fw)
                                            insights.append(UnderstandingInsight(
                                                category="framework",
                                                key=sig_fw,
                                                confidence=UnderstandingConfidence.OBSERVED,
                                                value=sig_fw,
                                                source_path=filename,
                                                rationale=f"Directly declared dependency '{dep_name}' in package.json",
                                            ))
                            # Check scripts for entry points or test commands
                            if "main" in pkg_data and pkg_data["main"]:
                                main_entry = pkg_data["main"]
                                if main_entry not in entry_points:
                                    entry_points.append(main_entry)
                        except Exception:
                            pass

                    elif filename == "pyproject.toml":
                        # Detect poetry
                        if "tool.poetry" in content:
                            if "poetry" not in package_managers:
                                package_managers.append("poetry")
                        elif "pip" not in package_managers:
                            package_managers.append("pip")

                        # Search framework dependencies
                        for sig_dep, sig_fw in FRAMEWORK_SIGNATURES.items():
                            if re.search(rf"\b{re.escape(sig_dep)}\b", content, re.IGNORECASE):
                                if sig_fw not in frameworks:
                                    frameworks.append(sig_fw)
                                    insights.append(UnderstandingInsight(
                                        category="framework",
                                        key=sig_fw,
                                        confidence=UnderstandingConfidence.OBSERVED,
                                        value=sig_fw,
                                        source_path=filename,
                                        rationale=f"Directly declared dependency '{sig_dep}' in pyproject.toml",
                                    ))

                    elif filename == "requirements.txt":
                        if "pip" not in package_managers:
                            package_managers.append("pip")
                        for sig_dep, sig_fw in FRAMEWORK_SIGNATURES.items():
                            if re.search(rf"^{re.escape(sig_dep)}(?:[=<>!~]|\s*$)", content, re.IGNORECASE | re.MULTILINE):
                                if sig_fw not in frameworks:
                                    frameworks.append(sig_fw)
                                    insights.append(UnderstandingInsight(
                                        category="framework",
                                        key=sig_fw,
                                        confidence=UnderstandingConfidence.OBSERVED,
                                        value=sig_fw,
                                        source_path=filename,
                                        rationale=f"Directly declared dependency '{sig_dep}' in requirements.txt",
                                    ))

                    elif filename == "Cargo.toml":
                        if "cargo" not in package_managers:
                            package_managers.append("cargo")
                        for sig_dep, sig_fw in FRAMEWORK_SIGNATURES.items():
                            if re.search(rf"\b{re.escape(sig_dep)}\b", content):
                                if sig_fw not in frameworks:
                                    frameworks.append(sig_fw)
                                    insights.append(UnderstandingInsight(
                                        category="framework",
                                        key=sig_fw,
                                        confidence=UnderstandingConfidence.OBSERVED,
                                        value=sig_fw,
                                        source_path=filename,
                                        rationale=f"Directly declared dependency '{sig_dep}' in Cargo.toml",
                                    ))

                    elif filename == "go.mod":
                        if "go" not in package_managers:
                            package_managers.append("go")
                        for sig_dep, sig_fw in FRAMEWORK_SIGNATURES.items():
                            if re.search(rf"\b{re.escape(sig_dep)}\b", content):
                                if sig_fw not in frameworks:
                                    frameworks.append(sig_fw)
                                    insights.append(UnderstandingInsight(
                                        category="framework",
                                        key=sig_fw,
                                        confidence=UnderstandingConfidence.OBSERVED,
                                        value=sig_fw,
                                        source_path=filename,
                                        rationale=f"Directly declared module requirement '{sig_dep}' in go.mod",
                                    ))

                except Exception as read_err:
                    uncertainties.append(f"Failed reading manifest '{filename}': {read_err}")

        # Check secondary lockfiles for package managers
        if "package-lock.json" in top_files and "npm" not in package_managers:
            package_managers.append("npm")
        if "yarn.lock" in top_files and "yarn" not in package_managers:
            package_managers.append("yarn")
        if "pnpm-lock.yaml" in top_files and "pnpm" not in package_managers:
            package_managers.append("pnpm")
        if "poetry.lock" in top_files and "poetry" not in package_managers:
            package_managers.append("poetry")
        if "Pipfile.lock" in top_files and "pipenv" not in package_managers:
            package_managers.append("pipenv")

        # Project type derivation from metadata
        if "package.json" in found_manifests:
            project_type = "node_project"
            project_type_confidence = UnderstandingConfidence.OBSERVED
        elif "pyproject.toml" in found_manifests or "setup.py" in found_manifests:
            project_type = "python_project"
            project_type_confidence = UnderstandingConfidence.OBSERVED
        elif "Cargo.toml" in found_manifests:
            project_type = "rust_crate"
            project_type_confidence = UnderstandingConfidence.OBSERVED
        elif "go.mod" in found_manifests:
            project_type = "go_module"
            project_type_confidence = UnderstandingConfidence.OBSERVED
        elif not found_manifests:
            # Fallback: scan file extensions to INFER language
            observed_exts: set[str] = set()
            try:
                for root, dirs, files in os.walk(repo_root):
                    # Filter ignored directories in-place
                    dirs[:] = [d for d in dirs if d not in IGNORE_DIRECTORIES]
                    rel_r = os.path.relpath(root, repo_root)
                    if rel_r != ".":
                        r_allowed, _ = is_read_allowed(rel_r)
                        if not r_allowed:
                            dirs[:] = []
                            continue
                    for f in files:
                        _, ext = os.path.splitext(f)
                        if ext:
                            observed_exts.add(ext.lower())
            except Exception:
                pass

            uncertainties.append("Missing project metadata manifests; language and project type inferred from source files.")

            if ".py" in observed_exts:
                languages.append("python")
                insights.append(UnderstandingInsight(
                    category="language",
                    key="python",
                    confidence=UnderstandingConfidence.INFERRED,
                    value="python",
                    rationale="Inferred from observed .py file extensions; no manifest was found on disk",
                ))
                project_type = "python_project"
                project_type_confidence = UnderstandingConfidence.INFERRED
            elif ".ts" in observed_exts or ".js" in observed_exts:
                languages.append("typescript" if ".ts" in observed_exts else "javascript")
                insights.append(UnderstandingInsight(
                    category="language",
                    key=languages[-1],
                    confidence=UnderstandingConfidence.INFERRED,
                    value=languages[-1],
                    rationale=f"Inferred from observed {observed_exts} file extensions; no manifest found",
                ))
                project_type = "node_project"
                project_type_confidence = UnderstandingConfidence.INFERRED
            elif ".rs" in observed_exts:
                languages.append("rust")
                project_type = "rust_crate"
                project_type_confidence = UnderstandingConfidence.INFERRED
            elif ".go" in observed_exts:
                languages.append("go")
                project_type = "go_module"
                project_type_confidence = UnderstandingConfidence.INFERRED
            else:
                project_type = "unknown"
                project_type_confidence = UnderstandingConfidence.UNKNOWN
                uncertainties.append("Missing project metadata manifests and recognizable source files.")

        # If frameworks empty, record unknown
        if not frameworks:
            insights.append(UnderstandingInsight(
                category="framework",
                key="none_detected",
                confidence=UnderstandingConfidence.UNKNOWN,
                value="unknown",
                rationale="No known framework signatures observed in manifests or source code",
            ))

        # ---------------------------------------------------------------------
        # Stage 4: Application Entry Points
        # ---------------------------------------------------------------------
        entry_point_candidates = [
            "main.py",
            "app.py",
            "cli.py",
            "server.py",
            "__main__.py",
            "src/main.py",
            "src/app.py",
            "src/cli.py",
            "index.ts",
            "index.js",
            "src/index.ts",
            "src/index.js",
            "server.js",
            "src/server.ts",
            "cmd/main.go",
            "main.go",
            "src/main.rs",
            "src/lib.rs",
        ]

        for ep in entry_point_candidates:
            ep_path = os.path.join(repo_root, ep)
            if os.path.exists(ep_path) and os.path.isfile(ep_path):
                allowed, reason = is_read_allowed(ep)
                if allowed:
                    if ep not in entry_points:
                        entry_points.append(ep)
                        ev_id = add_evidence(f"Observed application entry point '{ep}'", {"entry_point": ep})
                        insights.append(UnderstandingInsight(
                            category="entry_point",
                            key=ep,
                            confidence=UnderstandingConfidence.OBSERVED,
                            value=ep,
                            source_path=ep,
                            rationale=f"Standard application entry point '{ep}' exists on disk",
                            evidence_id=ev_id,
                        ))
                else:
                    uncertainties.append(f"Candidate entry point '{ep}' exists but reading is restricted: {reason}")

        if not entry_points:
            uncertainties.append("Application entry points could not be definitively observed.")

        # ---------------------------------------------------------------------
        # Stage 5: Relevant Modules (Objective-Driven)
        # ---------------------------------------------------------------------
        # Tokenize objective to extract search keywords
        raw_words = re.findall(r"[A-Za-z0-9_-]+", work_order.objective.lower())
        objective_keywords = [
            w for w in raw_words
            if len(w) >= 3 and w not in OBJECTIVE_STOPWORDS
        ]

        discovered_source_files: list[str] = []
        files_inspected = 0

        # Scan candidate directories: important_dirs or root
        search_dirs = [d for d in important_dirs if d not in ("tests", "test", "spec", "docs")]
        if not search_dirs:
            if root_allowed:
                search_dirs = ["."]
            else:
                search_dirs = [ap for ap in work_order.allowed_paths if ap not in (".", "*", "")]

        for s_dir in search_dirs:
            dir_full = os.path.join(repo_root, s_dir) if s_dir != "." else repo_root
            if not os.path.exists(dir_full):
                continue
            for root, dirs, files in os.walk(dir_full):
                dirs[:] = [d for d in dirs if d not in IGNORE_DIRECTORIES]
                rel_root = os.path.relpath(root, repo_root)
                if rel_root != ".":
                    allowed, _ = is_read_allowed(rel_root)
                    if not allowed:
                        dirs[:] = []
                        continue

                for f in sorted(files):
                    if files_inspected >= self.max_files_inspected:
                        break
                    files_inspected += 1
                    rel_f = os.path.relpath(os.path.join(root, f), repo_root)
                    allowed, _ = is_read_allowed(rel_f)
                    if allowed:
                        discovered_source_files.append(rel_f)

        # Match objective keywords against discovered files
        matched_modules: list[tuple[str, str]] = []  # (path, keyword)
        for f_path in discovered_source_files:
            lower_path = f_path.lower()
            for kw in objective_keywords:
                if kw in lower_path:
                    matched_modules.append((f_path, kw))
                    break

        if matched_modules:
            for m_path, kw in matched_modules:
                if m_path not in relevant_modules:
                    relevant_modules.append(m_path)
                    ev_id = add_evidence(f"Relevant module '{m_path}' matched objective keyword '{kw}'", {"path": m_path, "keyword": kw})
                    insights.append(UnderstandingInsight(
                        category="relevant_module",
                        key=m_path,
                        confidence=UnderstandingConfidence.INFERRED,
                        value=m_path,
                        source_path=m_path,
                        rationale=f"Module path '{m_path}' matches WorkOrder objective keyword '{kw}'",
                        evidence_id=ev_id,
                    ))
        else:
            # Fallback: designate primary source directory as inferred relevant module
            if important_dirs:
                primary = important_dirs[0]
                relevant_modules.append(primary)
                insights.append(UnderstandingInsight(
                    category="relevant_module",
                    key=primary,
                    confidence=UnderstandingConfidence.INFERRED,
                    value=primary,
                    source_path=primary,
                    rationale=f"No keyword match; inferred primary source directory '{primary}' as relevant module",
                ))
            else:
                uncertainties.append("No specific relevant modules matched the WorkOrder objective.")

        # ---------------------------------------------------------------------
        # Stage 6: Existing Tests
        # ---------------------------------------------------------------------
        test_dir_candidates = ["tests", "test", "spec", "__tests__"]
        for td in test_dir_candidates:
            td_path = os.path.join(repo_root, td)
            if os.path.exists(td_path) and os.path.isdir(td_path):
                allowed, reason = is_read_allowed(td)
                if allowed:
                    if td not in test_locations:
                        test_locations.append(td)
                        ev_id = add_evidence(f"Observed test directory '{td}'", {"path": td})
                        insights.append(UnderstandingInsight(
                            category="test_location",
                            key=td,
                            confidence=UnderstandingConfidence.OBSERVED,
                            value=td,
                            source_path=td,
                            rationale=f"Observed test directory '{td}' on disk",
                            evidence_id=ev_id,
                        ))
                else:
                    uncertainties.append(f"Test directory '{td}' exists but read was blocked: {reason}")

        # Scan for test files
        for f_path in discovered_source_files:
            bname = os.path.basename(f_path)
            if (
                bname.startswith("test_")
                or bname.endswith("_test.py")
                or bname.endswith(".test.ts")
                or bname.endswith(".spec.ts")
                or bname.endswith(".test.js")
                or bname.endswith(".spec.js")
                or bname.endswith("_test.go")
            ):
                if f_path not in test_locations:
                    test_locations.append(f_path)

        if not test_locations:
            uncertainties.append("No existing test locations or test files observed.")

        # ---------------------------------------------------------------------
        # Stage 7: Configuration Files
        # ---------------------------------------------------------------------
        config_candidates = [
            ".env.example",
            ".env.template",
            "docker-compose.yml",
            "Dockerfile",
            "Makefile",
            "tsconfig.json",
            "pytest.ini",
            "setup.cfg",
            "tox.ini",
            ".flake8",
            "ruff.toml",
            ".eslintrc.json",
            ".eslintrc.js",
            ".prettierrc",
        ]

        for cfg in config_candidates:
            cfg_path = os.path.join(repo_root, cfg)
            if os.path.exists(cfg_path) and os.path.isfile(cfg_path):
                allowed, reason = is_read_allowed(cfg)
                if allowed:
                    config_files.append(cfg)
                    ev_id = add_evidence(f"Discovered configuration file '{cfg}'", {"path": cfg})
                    insights.append(UnderstandingInsight(
                        category="configuration",
                        key=cfg,
                        confidence=UnderstandingConfidence.OBSERVED,
                        value=cfg,
                        source_path=cfg,
                        rationale=f"Observed configuration file '{cfg}' on disk",
                        evidence_id=ev_id,
                    ))
                else:
                    uncertainties.append(f"Configuration file '{cfg}' exists but read was restricted: {reason}")

        # ---------------------------------------------------------------------
        # Stage 8: Conventions Relevant to Requested Task
        # ---------------------------------------------------------------------
        # Inspect 1-2 discovered source files to infer naming, formatting conventions
        if discovered_source_files:
            sample_files = [f for f in discovered_source_files if not f.startswith("tests")][:2]
            for sf in sample_files:
                sf_path = os.path.join(repo_root, sf)
                try:
                    with open(sf_path, "r", encoding="utf-8", errors="replace") as f_obj:
                        lines = [f_obj.readline() for _ in range(50)]

                    # Indentation convention
                    space_indented = sum(1 for line in lines if line.startswith("    "))
                    two_space_indented = sum(1 for line in lines if line.startswith("  ") and not line.startswith("    "))
                    tab_indented = sum(1 for line in lines if line.startswith("\t"))

                    if space_indented > two_space_indented and space_indented > tab_indented:
                        conventions["indentation"] = "4_spaces"
                    elif two_space_indented > space_indented and two_space_indented > tab_indented:
                        conventions["indentation"] = "2_spaces"
                    elif tab_indented > space_indented:
                        conventions["indentation"] = "tabs"

                    # Naming convention (functions/methods)
                    full_text = "".join(lines)
                    snake_matches = len(re.findall(r"def [a-z0-9_]+\(", full_text))
                    camel_matches = len(re.findall(r"(?:function|def) [a-z]+[A-Z][a-zA-Z0-9]*\(", full_text))
                    if snake_matches > camel_matches:
                        conventions["function_naming"] = "snake_case"
                    elif camel_matches > snake_matches:
                        conventions["function_naming"] = "camelCase"

                except Exception:
                    pass

        # Test location convention
        if any(tl.startswith("tests") or tl.startswith("test") for tl in test_locations):
            conventions["test_location"] = "separate_test_directory"
        elif any("__tests__" in tl for tl in test_locations):
            conventions["test_location"] = "colocated_tests"

        for conv_key, conv_val in conventions.items():
            insights.append(UnderstandingInsight(
                category="convention",
                key=conv_key,
                confidence=UnderstandingConfidence.INFERRED,
                value=conv_val,
                rationale=f"Inferred convention '{conv_key}={conv_val}' from codebase patterns",
            ))

        # Build understanding result
        und_id = new_codebase_understanding_id()
        understanding = CodebaseUnderstanding(
            understanding_id=und_id,
            execution_id=exec_id,
            work_order_id=wo_id,
            project_id=proj_id,
            repository_id=repository_id,
            project_type=project_type,
            project_type_confidence=project_type_confidence,
            languages=languages,
            frameworks=frameworks,
            package_managers=package_managers,
            repository_structure=structure_dict,
            important_directories=important_dirs,
            entry_points=entry_points,
            configuration_files=config_files,
            test_locations=test_locations,
            dependency_manifests=manifests,
            relevant_modules=relevant_modules,
            detected_conventions=conventions,
            uncertainties=uncertainties,
            insights=insights,
            evidence=evidence_list,
            trace=tr,
        )

        return understanding

    def explore_context(
        self,
        context: Union[ProgrammerExecutionContext, GitExecutionContext],
        work_order: ProgrammerWorkOrder,
        trace: Optional[dict[str, Any]] = None,
    ) -> CodebaseUnderstanding:
        """
        Convenience entrypoint to explore from either a ProgrammerExecutionContext or GitExecutionContext.
        """
        ws_path = getattr(context, "workspace_path", None)
        if ws_path is None:
            ws = getattr(context, "workspace", None)
            ws_path = getattr(ws, "root_path", "")

        from core.programmer.contracts.identifiers import new_workspace_id
        workspace = ProgrammerWorkspace(
            workspace_id=new_workspace_id(),
            project_id=work_order.project_id,
            work_order_id=work_order.work_order_id,
            root_path=str(ws_path),
            allowed_paths=list(work_order.allowed_paths),
            writable_paths=list(work_order.writable_paths),
            forbidden_paths=list(work_order.forbidden_paths),
        )

        repo_id = getattr(context, "repository_id", None)

        return self.explore(
            workspace=workspace,
            work_order=work_order,
            execution_id=context.execution_id,
            repository_id=repo_id,
            trace=trace,
        )
