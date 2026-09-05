"""
Project Documentation, Configuration, and Dependency Context Extractor (Phase 1 / Part 8 / Step 6).

Provides safe, deterministic contextual extraction for:
1. Documentation:
   - README files, architecture documentation, ADRs, guides, project docs
   - Markdown sections with heading hierarchy (h1-h6), heading paths, line ranges, code blocks
   - Inert text isolation (zero execution of code blocks)
2. Configuration:
   - File types: JSON, YAML, TOML, INI/CFG
   - Secret safety: blocks sensitive files (.env, *.key, *.pem, id_rsa, credentials.json)
   - Value redaction: recursive masking of passwords/tokens/keys via mask_sensitive_config
3. Dependencies:
   - Static manifest inspection ONLY (pyproject.toml, package.json, Cargo.toml, go.mod, requirements.txt, setup.cfg)
   - Zero execution (NO pip, npm, cargo, go)
   - Zero network/registry queries
   - Strongly typed models: ProjectDependencyMetadata with name, version_spec, type, ecosystem

Integrates directly with ProjectContext and CrawlerReport evidence pipeline.
"""
from __future__ import annotations

import configparser
from dataclasses import dataclass, field
import json
import logging
import os
import posixpath
import re
from typing import Any, Callable, Optional, Sequence
import tomllib
import yaml

from core.research.contracts.evidence import EvidenceProvenance
from core.research.errors import (
    ProjectCancelledError,
    ProjectSecurityError,
    ProjectValidationError,
)
from core.research.project.models import (
    LineRange,
    ProjectConfigType,
    ProjectConfigurationMetadata,
    ProjectContext,
    ProjectDependencyMetadata,
    ProjectDependencyType,
    ProjectDocSection,
    ProjectDocumentationMetadata,
    compute_sha256,
    mask_sensitive_config,
    normalize_project_path,
    utc_now,
)
from core.research.project.provider import is_sensitive_project_path, sanitize_content_secrets
from core.research.search.security import sanitize_error

logger = logging.getLogger("AutonomOS.Research.ProjectContextExtractor")


# -----------------------------------------------------------------------------
# 1. ProjectDocumentationExtractor
# -----------------------------------------------------------------------------

class ProjectDocumentationExtractor:
    """
    Deterministic structural extractor for Markdown and text documentation files:
    - Heading hierarchy (h1-h6) and breadcrumb paths
    - Code blocks with language detection
    - Document classification (README, Architecture, ADR, Guide, General)
    - Inert claim isolation (doc content is passive text, never executed)
    """

    _HEADING_RE = re.compile(r"^(#{1,6})\s+(.+)$")
    _FENCED_CODE_START_RE = re.compile(r"^```\s*([a-zA-Z0-9_+#.-]*)\s*$")
    _ADR_FILENAME_RE = re.compile(r"^\d{4}-.*\.md$", re.IGNORECASE)

    @classmethod
    def extract_documentation(
        cls,
        file_path: str,
        content: str,
        provenance: Optional[EvidenceProvenance] = None,
    ) -> ProjectDocumentationMetadata:
        """
        Parse Markdown/text documentation file into structured sections and metadata.
        """
        norm_path = normalize_project_path(file_path)
        content_str = content or ""
        lines = content_str.splitlines()
        content_hash = compute_sha256(content_str)
        doc_type = cls.classify_doc_type(norm_path)

        sections: list[ProjectDocSection] = []
        heading_stack: list[tuple[int, str]] = []  # (level, heading_text)

        # Extraction state
        current_heading = "Overview"
        current_level = 1
        current_lines: list[str] = []
        current_start_line = 1
        current_code_blocks: list[dict[str, str]] = []

        in_code_block = False
        code_block_lang = ""
        code_block_lines: list[str] = []

        first_title = ""

        for idx, line in enumerate(lines, start=1):
            stripped = line.strip()

            # Handle code block fence
            if stripped.startswith("```"):
                if in_code_block:
                    # Closing fence
                    in_code_block = False
                    block_content = "\n".join(code_block_lines)
                    current_code_blocks.append({
                        "language": code_block_lang,
                        "code": block_content,
                    })
                    code_block_lines = []
                    code_block_lang = ""
                    current_lines.append(line)
                    continue
                else:
                    # Opening fence
                    fence_match = cls._FENCED_CODE_START_RE.match(stripped)
                    in_code_block = True
                    code_block_lang = fence_match.group(1) if fence_match else ""
                    code_block_lines = []
                    current_lines.append(line)
                    continue

            if in_code_block:
                code_block_lines.append(line)
                current_lines.append(line)
                continue

            # Check heading outside code blocks
            heading_match = cls._HEADING_RE.match(stripped)
            if heading_match:
                level = len(heading_match.group(1))
                heading_text = heading_match.group(2).strip()

                if not first_title and level == 1:
                    first_title = heading_text

                # Flush previous section if it has content or was a named section
                if current_lines or current_heading != "Overview":
                    prev_content = sanitize_content_secrets("\n".join(current_lines).strip())
                    heading_path = [h[1] for h in heading_stack] or [current_heading]
                    sections.append(
                        ProjectDocSection(
                            heading=current_heading,
                            level=current_level,
                            heading_path=heading_path,
                            content=prev_content,
                            line_range=LineRange(start_line=current_start_line, end_line=max(current_start_line, idx - 1)),
                            code_blocks=list(current_code_blocks),
                        )
                    )

                # Update heading stack for hierarchy
                while heading_stack and heading_stack[-1][0] >= level:
                    heading_stack.pop()
                heading_stack.append((level, heading_text))

                current_heading = heading_text
                current_level = level
                current_lines = []
                current_start_line = idx
                current_code_blocks = []
                continue

            current_lines.append(line)

        # Flush final section
        if current_lines or not sections:
            final_content = sanitize_content_secrets("\n".join(current_lines).strip())
            heading_path = [h[1] for h in heading_stack] or [current_heading]
            sections.append(
                ProjectDocSection(
                    heading=current_heading,
                    level=current_level,
                    heading_path=heading_path,
                    content=final_content,
                    line_range=LineRange(start_line=current_start_line, end_line=len(lines) or 1),
                    code_blocks=list(current_code_blocks),
                )
            )

        # Determine document title
        title = first_title
        if not title:
            base = posixpath.basename(norm_path)
            title = posixpath.splitext(base)[0]

        return ProjectDocumentationMetadata(
            file_path=norm_path,
            doc_type=doc_type,
            title=title,
            sections=sections,
            content_hash=content_hash,
            provenance=provenance,
            retrieved_at=utc_now(),
            metadata={
                "line_count": len(lines),
                "section_count": len(sections),
            },
        )

    @classmethod
    def classify_doc_type(cls, file_path: str) -> str:
        """Classify documentation type based on path and filename heuristics."""
        norm = normalize_project_path(file_path).lower()
        base = posixpath.basename(norm)

        # 1. README
        if base.startswith("readme"):
            return "readme"

        # 2. Architecture / Design
        if any(p in norm for p in ("architecture", "system_design", "design_doc", "/arch/")):
            return "architecture"

        # 3. ADR (Architecture Decision Records)
        if any(p in norm for p in ("/adr/", "/decisions/", "adr-")) or cls._ADR_FILENAME_RE.match(base):
            return "adr"

        # 4. Guide / Tutorial
        if any(p in norm for p in ("guide", "tutorial", "howto", "getting-started", "manual", "walkthrough")):
            return "guide"

        # 5. General project documentation
        return "general"


# -----------------------------------------------------------------------------
# 2. ProjectConfigurationExtractor
# -----------------------------------------------------------------------------

class ProjectConfigurationExtractor:
    """
    Safe structural parser for project configuration and manifest files:
    - Supported formats: JSON, YAML, TOML, INI
    - Secret safety: blocks sensitive credential files (.env, *.key, *.pem, id_rsa)
    - Secret masking: passes all dictionaries through recursive mask_sensitive_config
    - Identifies ProjectConfigType taxonomy
    """

    _CONFIG_TYPE_MAPPING = [
        (re.compile(r"^pyproject\.toml$", re.I), ProjectConfigType.PYTHON_PYPROJECT),
        (re.compile(r"^setup\.(cfg|py)$", re.I), ProjectConfigType.PYTHON_SETUPTOOLS),
        (re.compile(r"^requirements.*\.txt$", re.I), ProjectConfigType.PYTHON_REQUIREMENTS),
        (re.compile(r"^package\.json$", re.I), ProjectConfigType.NPM_PACKAGE),
        (re.compile(r"^tsconfig.*\.json$", re.I), ProjectConfigType.TYPESCRIPT_TSCONFIG),
        (re.compile(r"^cargo\.toml$", re.I), ProjectConfigType.RUST_CARGO),
        (re.compile(r"^go\.mod$", re.I), ProjectConfigType.GO_MOD),
        (re.compile(r"^dockerfile.*$", re.I), ProjectConfigType.DOCKERFILE),
        (re.compile(r"^docker-compose.*\.ya?ml$", re.I), ProjectConfigType.DOCKER_COMPOSE),
        (re.compile(r"^\.github/workflows/.*\.ya?ml$", re.I), ProjectConfigType.GITHUB_ACTIONS),
        (re.compile(r"^\.git/config$", re.I), ProjectConfigType.GIT_CONFIG),
    ]

    @classmethod
    def extract_configuration(
        cls,
        file_path: str,
        content: str,
        provenance: Optional[EvidenceProvenance] = None,
    ) -> ProjectConfigurationMetadata:
        """
        Extract safe configuration structure from file content.
        Enforces secret safety and recursive key masking.
        """
        norm_path = normalize_project_path(file_path)

        # 1. Secret safety: block sensitive credential files
        if is_sensitive_project_path(norm_path):
            logger.warning(f"Blocked extraction of sensitive credential file: '{norm_path}'")
            return ProjectConfigurationMetadata(
                config_path=norm_path,
                config_type=ProjectConfigType.ENV,
                safe_metadata={
                    "status": "blocked_sensitive_file",
                    "reason": "File is classified as a sensitive credential or key file.",
                },
                content_hash=compute_sha256(content or ""),
                provenance=provenance,
                metadata={"blocked": True},
            )

        content_str = content or ""
        content_hash = compute_sha256(content_str)
        config_type = cls.determine_config_type(norm_path)

        # 2. Parse file according to extension/type
        parsed_data: dict[str, Any] = {}
        ext = posixpath.splitext(norm_path)[1].lower()
        base = posixpath.basename(norm_path).lower()

        try:
            if ext == ".json":
                loaded = json.loads(content_str)
                parsed_data = loaded if isinstance(loaded, dict) else {"items": loaded}

            elif ext in (".yaml", ".yml"):
                loaded = yaml.safe_load(content_str)
                parsed_data = loaded if isinstance(loaded, dict) else {"items": loaded} if isinstance(loaded, list) else {"value": loaded}

            elif ext == ".toml" or base in ("cargo.toml", "pyproject.toml"):
                parsed_data = tomllib.loads(content_str)

            elif ext in (".ini", ".cfg", ".conf") or base == "setup.cfg":
                cp = configparser.ConfigParser()
                cp.read_string(content_str)
                parsed_data = {s: dict(cp.items(s)) for s in cp.sections()}

            else:
                # Fallback simple key-value parser for flat config files
                lines = content_str.splitlines()
                kv: dict[str, Any] = {}
                for line in lines:
                    line_s = line.strip()
                    if line_s and not line_s.startswith(("#", "//", ";")) and "=" in line_s:
                        k, _, v = line_s.partition("=")
                        kv[k.strip()] = v.strip().strip("'\"")
                parsed_data = kv or {"raw_lines_count": len(lines)}

        except Exception as err:
            logger.warning(f"Failed to parse config file '{norm_path}': {err}")
            parsed_data = {
                "parse_error": sanitize_error(err),
                "raw_preview": content_str[:200],
            }

        # 3. Recursive secret masking
        safe_meta = mask_sensitive_config(parsed_data)

        return ProjectConfigurationMetadata(
            config_path=norm_path,
            config_type=config_type,
            safe_metadata=safe_meta,
            content_hash=content_hash,
            provenance=provenance,
            retrieved_at=utc_now(),
            metadata={"format": ext.lstrip(".") or "text"},
        )

    @classmethod
    def determine_config_type(cls, file_path: str) -> ProjectConfigType:
        """Classify configuration type from file path."""
        norm = normalize_project_path(file_path)
        base = posixpath.basename(norm)

        for pattern, cfg_type in cls._CONFIG_TYPE_MAPPING:
            if pattern.search(base) or pattern.search(norm):
                return cfg_type

        return ProjectConfigType.GENERIC


# -----------------------------------------------------------------------------
# 3. ProjectDependencyExtractor
# -----------------------------------------------------------------------------

class ProjectDependencyExtractor:
    """
    Purely static declared dependency extractor:
    - Reads manifests ONLY: pyproject.toml, requirements.txt, setup.cfg, package.json, Cargo.toml, go.mod
    - ZERO execution of package managers (pip, npm, cargo, go)
    - ZERO network or package registry requests
    - Typed extraction of package name, version spec, scope, and ecosystem
    """

    _REQ_LINE_RE = re.compile(r"^([a-zA-Z0-9_.-]+(?:\[[^\]]*\])?)\s*([<>=~!].*)?$")
    _GO_REQ_RE = re.compile(r"^([a-zA-Z0-9_./-]+)\s+([v0-9a-zA-Z._+-]+)")

    @classmethod
    def extract_dependencies(
        cls,
        file_path: str,
        content: str,
        provenance: Optional[EvidenceProvenance] = None,
    ) -> list[ProjectDependencyMetadata]:
        """
        Extract declared dependencies from a project manifest file.
        """
        norm_path = normalize_project_path(file_path)
        base = posixpath.basename(norm_path).lower()
        content_str = content or ""

        if base == "pyproject.toml":
            return cls._parse_pyproject(norm_path, content_str, provenance)
        elif base.startswith("requirements") and base.endswith(".txt"):
            return cls._parse_requirements_txt(norm_path, content_str, provenance)
        elif base == "setup.cfg":
            return cls._parse_setup_cfg(norm_path, content_str, provenance)
        elif base == "package.json":
            return cls._parse_package_json(norm_path, content_str, provenance)
        elif base == "cargo.toml":
            return cls._parse_cargo_toml(norm_path, content_str, provenance)
        elif base == "go.mod":
            return cls._parse_go_mod(norm_path, content_str, provenance)

        return []

    @classmethod
    def _parse_pyproject(
        cls,
        manifest_path: str,
        content: str,
        provenance: Optional[EvidenceProvenance],
    ) -> list[ProjectDependencyMetadata]:
        deps: list[ProjectDependencyMetadata] = []
        try:
            data = tomllib.loads(content)
        except Exception as e:
            logger.warning(f"Error parsing '{manifest_path}': {e}")
            return deps

        # 1. PEP 621: project.dependencies
        project_table = data.get("project", {})
        for req_str in project_table.get("dependencies", []):
            dep = cls._parse_pep508_string(req_str, manifest_path, ProjectDependencyType.RUNTIME, provenance)
            if dep:
                deps.append(dep)

        # PEP 621: project.optional-dependencies
        optional_deps = project_table.get("optional-dependencies", {})
        if isinstance(optional_deps, dict):
            for group, req_list in optional_deps.items():
                if isinstance(req_list, list):
                    for req_str in req_list:
                        dep = cls._parse_pep508_string(req_str, manifest_path, ProjectDependencyType.OPTIONAL, provenance, group=group)
                        if dep:
                            deps.append(dep)

        # 2. Poetry: tool.poetry.dependencies
        poetry_table = data.get("tool", {}).get("poetry", {})
        for name, spec in poetry_table.get("dependencies", {}).items():
            if name.lower() == "python":
                continue
            ver = spec if isinstance(spec, str) else spec.get("version") if isinstance(spec, dict) else None
            deps.append(
                ProjectDependencyMetadata(
                    name=name,
                    version_spec=ver,
                    manifest_source=manifest_path,
                    dependency_type=ProjectDependencyType.RUNTIME,
                    ecosystem="pypi",
                    provenance=provenance,
                )
            )

        # Poetry dev-dependencies
        dev_deps = poetry_table.get("dev-dependencies", {})
        for name, spec in dev_deps.items():
            ver = spec if isinstance(spec, str) else spec.get("version") if isinstance(spec, dict) else None
            deps.append(
                ProjectDependencyMetadata(
                    name=name,
                    version_spec=ver,
                    manifest_source=manifest_path,
                    dependency_type=ProjectDependencyType.DEV,
                    is_dev=True,
                    ecosystem="pypi",
                    provenance=provenance,
                )
            )

        # Poetry 1.2+ groups
        for grp_name, grp_data in poetry_table.get("group", {}).items():
            is_dev_group = "dev" in grp_name.lower() or "test" in grp_name.lower()
            dtype = ProjectDependencyType.DEV if is_dev_group else ProjectDependencyType.OPTIONAL
            for name, spec in grp_data.get("dependencies", {}).items():
                ver = spec if isinstance(spec, str) else spec.get("version") if isinstance(spec, dict) else None
                deps.append(
                    ProjectDependencyMetadata(
                        name=name,
                        version_spec=ver,
                        manifest_source=manifest_path,
                        dependency_type=dtype,
                        is_dev=is_dev_group,
                        ecosystem="pypi",
                        provenance=provenance,
                        metadata={"group": grp_name},
                    )
                )

        return deps

    @classmethod
    def _parse_pep508_string(
        cls,
        req_str: str,
        manifest_path: str,
        dep_type: ProjectDependencyType,
        provenance: Optional[EvidenceProvenance],
        group: str = "",
    ) -> Optional[ProjectDependencyMetadata]:
        if not isinstance(req_str, str) or not req_str.strip():
            return None
        line = req_str.strip()
        # Strip environment markers (; python_version >= ...)
        marker = ""
        if ";" in line:
            line, _, marker = line.partition(";")
            line = line.strip()

        match = cls._REQ_LINE_RE.match(line)
        if not match:
            return None

        raw_name = match.group(1).strip()
        ver = match.group(2).strip() if match.group(2) else None

        # Clean extras if any: requests[security] -> requests
        name = raw_name.split("[")[0].strip()
        extras = raw_name[len(name):].strip("[]") if "[" in raw_name else ""

        meta: dict[str, Any] = {}
        if extras:
            meta["extras"] = extras
        if marker:
            meta["marker"] = marker.strip()
        if group:
            meta["group"] = group

        return ProjectDependencyMetadata(
            name=name,
            version_spec=ver,
            manifest_source=manifest_path,
            dependency_type=dep_type,
            is_dev=(dep_type == ProjectDependencyType.DEV),
            ecosystem="pypi",
            provenance=provenance,
            metadata=meta,
        )

    @classmethod
    def _parse_requirements_txt(
        cls,
        manifest_path: str,
        content: str,
        provenance: Optional[EvidenceProvenance],
    ) -> list[ProjectDependencyMetadata]:
        deps: list[ProjectDependencyMetadata] = []
        is_dev = any(k in manifest_path.lower() for k in ("dev", "test", "lint"))
        dtype = ProjectDependencyType.DEV if is_dev else ProjectDependencyType.RUNTIME

        for line in content.splitlines():
            line_s = line.strip()
            if not line_s or line_s.startswith(("#", "--", "-r", "-i", "-f", "-c")):
                continue
            dep = cls._parse_pep508_string(line_s, manifest_path, dtype, provenance)
            if dep:
                deps.append(dep)

        return deps

    @classmethod
    def _parse_setup_cfg(
        cls,
        manifest_path: str,
        content: str,
        provenance: Optional[EvidenceProvenance],
    ) -> list[ProjectDependencyMetadata]:
        deps: list[ProjectDependencyMetadata] = []
        cp = configparser.ConfigParser()
        try:
            cp.read_string(content)
        except Exception as e:
            logger.warning(f"Error reading setup.cfg '{manifest_path}': {e}")
            return deps

        # options.install_requires
        if cp.has_section("options") and cp.has_option("options", "install_requires"):
            reqs = cp.get("options", "install_requires").splitlines()
            for r in reqs:
                dep = cls._parse_pep508_string(r, manifest_path, ProjectDependencyType.RUNTIME, provenance)
                if dep:
                    deps.append(dep)

        # options.extras_require
        if cp.has_section("options.extras_require"):
            for extra, val in cp.items("options.extras_require"):
                is_dev = any(k in extra.lower() for k in ("dev", "test", "doc"))
                dtype = ProjectDependencyType.DEV if is_dev else ProjectDependencyType.OPTIONAL
                for r in val.splitlines():
                    dep = cls._parse_pep508_string(r, manifest_path, dtype, provenance, group=extra)
                    if dep:
                        deps.append(dep)

        return deps

    @classmethod
    def _parse_package_json(
        cls,
        manifest_path: str,
        content: str,
        provenance: Optional[EvidenceProvenance],
    ) -> list[ProjectDependencyMetadata]:
        deps: list[ProjectDependencyMetadata] = []
        try:
            data = json.loads(content)
        except Exception as e:
            logger.warning(f"Error parsing package.json '{manifest_path}': {e}")
            return deps

        if not isinstance(data, dict):
            return deps

        type_map = [
            ("dependencies", ProjectDependencyType.RUNTIME, False),
            ("devDependencies", ProjectDependencyType.DEV, True),
            ("peerDependencies", ProjectDependencyType.PEER, False),
            ("optionalDependencies", ProjectDependencyType.OPTIONAL, False),
        ]

        for key, dtype, is_dev in type_map:
            table = data.get(key, {})
            if isinstance(table, dict):
                for name, ver in table.items():
                    deps.append(
                        ProjectDependencyMetadata(
                            name=str(name),
                            version_spec=str(ver) if ver is not None else None,
                            manifest_source=manifest_path,
                            dependency_type=dtype,
                            is_dev=is_dev,
                            ecosystem="npm",
                            provenance=provenance,
                        )
                    )

        return deps

    @classmethod
    def _parse_cargo_toml(
        cls,
        manifest_path: str,
        content: str,
        provenance: Optional[EvidenceProvenance],
    ) -> list[ProjectDependencyMetadata]:
        deps: list[ProjectDependencyMetadata] = []
        try:
            data = tomllib.loads(content)
        except Exception as e:
            logger.warning(f"Error parsing Cargo.toml '{manifest_path}': {e}")
            return deps

        sections = [
            ("dependencies", ProjectDependencyType.RUNTIME, False),
            ("dev-dependencies", ProjectDependencyType.DEV, True),
            ("build-dependencies", ProjectDependencyType.BUILD, False),
        ]

        for sec_name, dtype, is_dev in sections:
            table = data.get(sec_name, {})
            if isinstance(table, dict):
                for name, val in table.items():
                    ver_spec: Optional[str] = None
                    features: list[str] = []
                    if isinstance(val, str):
                        ver_spec = val
                    elif isinstance(val, dict):
                        ver_spec = val.get("version")
                        features = val.get("features", [])

                    meta: dict[str, Any] = {}
                    if features:
                        meta["features"] = features

                    deps.append(
                        ProjectDependencyMetadata(
                            name=str(name),
                            version_spec=ver_spec,
                            manifest_source=manifest_path,
                            dependency_type=dtype,
                            is_dev=is_dev,
                            ecosystem="cargo",
                            provenance=provenance,
                            metadata=meta,
                        )
                    )

        return deps

    @classmethod
    def _parse_go_mod(
        cls,
        manifest_path: str,
        content: str,
        provenance: Optional[EvidenceProvenance],
    ) -> list[ProjectDependencyMetadata]:
        deps: list[ProjectDependencyMetadata] = []
        in_require_block = False

        for line in content.splitlines():
            line_s = line.strip()
            if not line_s or line_s.startswith("//"):
                continue

            if line_s.startswith("require ("):
                in_require_block = True
                continue
            elif in_require_block and line_s == ")":
                in_require_block = False
                continue

            if in_require_block:
                m = cls._GO_REQ_RE.match(line_s)
                if m:
                    pkg_name = m.group(1)
                    ver = m.group(2)
                    is_indirect = "// indirect" in line_s
                    deps.append(
                        ProjectDependencyMetadata(
                            name=pkg_name,
                            version_spec=ver,
                            manifest_source=manifest_path,
                            dependency_type=ProjectDependencyType.RUNTIME,
                            ecosystem="go",
                            provenance=provenance,
                            metadata={"indirect": is_indirect},
                        )
                    )
            elif line_s.startswith("require "):
                rest = line_s[len("require "):].strip()
                m = cls._GO_REQ_RE.match(rest)
                if m:
                    pkg_name = m.group(1)
                    ver = m.group(2)
                    is_indirect = "// indirect" in rest
                    deps.append(
                        ProjectDependencyMetadata(
                            name=pkg_name,
                            version_spec=ver,
                            manifest_source=manifest_path,
                            dependency_type=ProjectDependencyType.RUNTIME,
                            ecosystem="go",
                            provenance=provenance,
                            metadata={"indirect": is_indirect},
                        )
                    )

        return deps


# -----------------------------------------------------------------------------
# 4. Context Coordinator: ProjectContextExtractor
# -----------------------------------------------------------------------------

class ProjectContextExtractor:
    """
    Unified extractor coordinating documentation, configuration, and dependency
    extraction for a project workspace snapshot.
    """

    _DOC_EXTENSIONS = {".md", ".markdown", ".rst", ".txt", ".adoc"}
    _CONFIG_FILENAMES = {
        "pyproject.toml",
        "package.json",
        "cargo.toml",
        "go.mod",
        "setup.cfg",
        "setup.py",
        "tsconfig.json",
        "dockerfile",
        "docker-compose.yml",
        "docker-compose.yaml",
    }
    _CONFIG_EXTENSIONS = {".json", ".yaml", ".yml", ".toml", ".ini", ".cfg", ".conf"}

    def __init__(self, provider: Optional[Any] = None) -> None:
        self.provider = provider

    @classmethod
    def extract_file(
        cls,
        file_path: str,
        content: str,
        project_context: Optional[ProjectContext] = None,
        provenance: Optional[EvidenceProvenance] = None,
    ) -> dict[str, Any]:
        """
        Extract documentation, configuration, or dependency context based on file characteristics.
        Optionally attaches extracted objects directly to a ProjectContext container.
        """
        norm_path = normalize_project_path(file_path)
        ext = posixpath.splitext(norm_path)[1].lower()
        base = posixpath.basename(norm_path).lower()

        result: dict[str, Any] = {
            "documentation": None,
            "configuration": None,
            "dependencies": [],
        }

        # 1. Documentation files
        if ext in cls._DOC_EXTENSIONS or base.startswith("readme"):
            doc = ProjectDocumentationExtractor.extract_documentation(norm_path, content, provenance)
            result["documentation"] = doc
            if project_context:
                project_context.add_documentation(doc)

        # 2. Configuration & Manifests
        is_config = (
            base in cls._CONFIG_FILENAMES
            or ext in cls._CONFIG_EXTENSIONS
            or base.startswith("requirements")
            or is_sensitive_project_path(norm_path)
        )

        if is_config:
            cfg = ProjectConfigurationExtractor.extract_configuration(norm_path, content, provenance)
            result["configuration"] = cfg
            if project_context:
                project_context.add_configuration(cfg)

            # Dependencies from manifest
            deps = ProjectDependencyExtractor.extract_dependencies(norm_path, content, provenance)
            result["dependencies"] = deps
            if project_context:
                for d in deps:
                    project_context.add_dependency(d)

        return result

    @classmethod
    def extract_all(
        cls,
        files: list[tuple[str, str]],
        project_context: ProjectContext,
        provenance: Optional[EvidenceProvenance] = None,
    ) -> None:
        """
        Batch extract documentation, configuration, and dependencies into a ProjectContext container.
        """
        for path, content in files:
            cls.extract_file(
                file_path=path,
                content=content,
                project_context=project_context,
                provenance=provenance,
            )
