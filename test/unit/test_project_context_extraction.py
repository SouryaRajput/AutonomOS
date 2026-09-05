"""
Unit tests for Project Documentation, Configuration, and Dependency Context (Phase 1 / Part 8 / Step 6).
"""
import pytest

from core.research.contracts.evidence import EvidenceProvenance
from core.research.project.context_extractor import (
    ProjectConfigurationExtractor,
    ProjectContextExtractor,
    ProjectDependencyExtractor,
    ProjectDocumentationExtractor,
)
from core.research.project.models import (
    ProjectConfigType,
    ProjectConfigurationMetadata,
    ProjectContext,
    ProjectDependencyMetadata,
    ProjectDependencyType,
    ProjectDocSection,
    ProjectDocumentationMetadata,
    ProjectIdentity,
)
from core.research.search.security import REDACTED_STR


def _make_provenance() -> EvidenceProvenance:
    return EvidenceProvenance(
        request_id="req-test-ctx",
        crawler_task_id="task-test-ctx",
        crawler_id="crawler.project.test",
        question_id="q-test-ctx",
    )


# -----------------------------------------------------------------------------
# Documentation Tests
# -----------------------------------------------------------------------------

def test_documentation_markdown_section_hierarchy():
    markdown = """# AutonomOS Architecture

High-level architecture documentation for the AutonomOS autonomous platform.

## Subsystems

AutonomOS contains modular subsystems.

### Researcher

The Researcher conducts autonomous evidence gathering.

### Planner

The Planner coordinates goals and tasks.

## Deployment

Deploy using Docker Compose or Kubernetes.
"""
    doc = ProjectDocumentationExtractor.extract_documentation(
        file_path="docs/architecture.md",
        content=markdown,
        provenance=_make_provenance(),
    )

    assert doc.doc_type == "architecture"
    assert doc.title == "AutonomOS Architecture"
    assert len(doc.sections) >= 4

    # Check section paths
    sec_map = {s.heading: s for s in doc.sections}
    assert "AutonomOS Architecture" in sec_map
    assert "Subsystems" in sec_map
    assert "Researcher" in sec_map
    assert "Planner" in sec_map
    assert "Deployment" in sec_map

    # Check hierarchy path for nested subsections
    researcher_sec = sec_map["Researcher"]
    assert researcher_sec.level == 3
    assert researcher_sec.heading_path == ["AutonomOS Architecture", "Subsystems", "Researcher"]
    assert "conducts autonomous evidence gathering" in researcher_sec.content


def test_documentation_code_blocks_extraction():
    markdown = """# Installation Guide

Run the following commands to install:

```bash
pip install autonomos
autonomos init
```

Configure your workspace settings:

```json
{
  "project_name": "demo",
  "version": "1.0.0"
}
```
"""
    doc = ProjectDocumentationExtractor.extract_documentation(
        file_path="docs/getting-started.md",
        content=markdown,
    )

    assert doc.doc_type == "guide"
    all_code_blocks = [cb for s in doc.sections for cb in s.code_blocks]
    assert len(all_code_blocks) == 2
    assert all_code_blocks[0]["language"] == "bash"
    assert "pip install autonomos" in all_code_blocks[0]["code"]
    assert all_code_blocks[1]["language"] == "json"
    assert '"project_name": "demo"' in all_code_blocks[1]["code"]


def test_documentation_classification():
    assert ProjectDocumentationExtractor.classify_doc_type("README.md") == "readme"
    assert ProjectDocumentationExtractor.classify_doc_type("README.rst") == "readme"
    assert ProjectDocumentationExtractor.classify_doc_type("docs/architecture/overview.md") == "architecture"
    assert ProjectDocumentationExtractor.classify_doc_type("docs/adr/0001-use-fastapi.md") == "adr"
    assert ProjectDocumentationExtractor.classify_doc_type("docs/decisions/0002-database.md") == "adr"
    assert ProjectDocumentationExtractor.classify_doc_type("guides/tutorial.md") == "guide"
    assert ProjectDocumentationExtractor.classify_doc_type("notes/misc.txt") == "general"


def test_documentation_to_evidence_items():
    doc = ProjectDocumentationExtractor.extract_documentation(
        file_path="README.md",
        content="# AutonomOS\n\nAutonomous enterprise operating system.",
        provenance=_make_provenance(),
    )
    evs = doc.to_evidence_items(
        request_id="req-1",
        crawler_task_id="task-1",
        crawler_id="crawler.proj",
    )
    assert len(evs) == 1
    ev = evs[0]
    assert ev.evidence_id == "ev-doc-README.md"
    assert "Documentation (readme)" in ev.extracted_fact
    assert ev.metadata["doc_type"] == "readme"


# -----------------------------------------------------------------------------
# Configuration Tests
# -----------------------------------------------------------------------------

def test_configuration_json_safe_parsing():
    content = """{
      "name": "autonomos",
      "version": "1.0.0",
      "private": true,
      "scripts": {
        "build": "tsc",
        "test": "jest"
      }
    }"""
    cfg = ProjectConfigurationExtractor.extract_configuration(
        file_path="package.json",
        content=content,
    )
    assert cfg.config_type == ProjectConfigType.NPM_PACKAGE
    assert cfg.safe_metadata["name"] == "autonomos"
    assert cfg.safe_metadata["scripts"]["build"] == "tsc"


def test_configuration_yaml_safe_parsing():
    content = """
version: '3.8'
services:
  app:
    image: autonomos:latest
    ports:
      - "8000:8000"
    environment:
      - LOG_LEVEL=info
"""
    cfg = ProjectConfigurationExtractor.extract_configuration(
        file_path="docker-compose.yml",
        content=content,
    )
    assert cfg.config_type == ProjectConfigType.DOCKER_COMPOSE
    assert "services" in cfg.safe_metadata
    assert cfg.safe_metadata["services"]["app"]["image"] == "autonomos:latest"


def test_configuration_toml_safe_parsing():
    content = """
[package]
name = "autonomos-core"
version = "0.1.0"
edition = "2021"

[dependencies]
tokio = "1.0"
"""
    cfg = ProjectConfigurationExtractor.extract_configuration(
        file_path="Cargo.toml",
        content=content,
    )
    assert cfg.config_type == ProjectConfigType.RUST_CARGO
    assert cfg.safe_metadata["package"]["name"] == "autonomos-core"


def test_configuration_ini_safe_parsing():
    content = """
[metadata]
name = autonomos
version = 0.5.0

[options]
packages = find:
"""
    cfg = ProjectConfigurationExtractor.extract_configuration(
        file_path="setup.cfg",
        content=content,
    )
    assert cfg.config_type == ProjectConfigType.PYTHON_SETUPTOOLS
    assert cfg.safe_metadata["metadata"]["name"] == "autonomos"


def test_configuration_secret_blocking():
    sensitive_paths = [
        ".env",
        ".env.production",
        "secrets/id_rsa",
        "certs/server.key",
        "config/credentials.json",
    ]
    for sp in sensitive_paths:
        cfg = ProjectConfigurationExtractor.extract_configuration(
            file_path=sp,
            content="SUPER_SECRET_TOKEN=xyz123abc456\nPASSWORD=hunter2",
        )
        assert cfg.metadata.get("blocked") is True
        assert cfg.safe_metadata.get("status") == "blocked_sensitive_file"
        # Verify secret content was never stored in safe_metadata
        assert "xyz123abc456" not in str(cfg.safe_metadata)
        assert "hunter2" not in str(cfg.safe_metadata)


def test_configuration_secret_masking():
    content = """{
      "app_name": "AutonomOS",
      "port": 8080,
      "api_key": "live_key_999999999",
      "database": {
        "user": "postgres",
        "password": "super_secret_db_password",
        "url": "postgres://admin:pass1234@db.prod.internal:5432/main"
      }
    }"""
    cfg = ProjectConfigurationExtractor.extract_configuration(
        file_path="config/app.json",
        content=content,
    )
    assert cfg.safe_metadata["app_name"] == "AutonomOS"
    assert cfg.safe_metadata["port"] == 8080
    assert cfg.safe_metadata["api_key"] == REDACTED_STR
    assert cfg.safe_metadata["database"]["password"] == REDACTED_STR
    assert "super_secret_db_password" not in str(cfg.safe_metadata)
    assert "pass1234" not in str(cfg.safe_metadata)


# -----------------------------------------------------------------------------
# Dependency Tests
# -----------------------------------------------------------------------------

def test_dependencies_pyproject_pep621():
    content = """
[project]
name = "my-service"
version = "0.1.0"
dependencies = [
    "fastapi>=0.100.0",
    "uvicorn[standard]>=0.20.0",
    "pydantic",
]

[project.optional-dependencies]
dev = [
    "pytest>=7.0.0",
    "black",
]
"""
    deps = ProjectDependencyExtractor.extract_dependencies(
        file_path="pyproject.toml",
        content=content,
        provenance=_make_provenance(),
    )
    names = {d.name: d for d in deps}
    assert "fastapi" in names
    assert names["fastapi"].version_spec == ">=0.100.0"
    assert names["fastapi"].dependency_type == ProjectDependencyType.RUNTIME
    assert names["fastapi"].ecosystem == "pypi"

    assert "uvicorn" in names
    assert names["uvicorn"].metadata.get("extras") == "standard"

    assert "pytest" in names
    assert names["pytest"].dependency_type == ProjectDependencyType.OPTIONAL
    assert names["pytest"].metadata.get("group") == "dev"


def test_dependencies_pyproject_poetry():
    content = """
[tool.poetry]
name = "autonomos-worker"
version = "0.1.0"

[tool.poetry.dependencies]
python = "^3.11"
httpx = "^0.24.0"
pydantic = "^2.0"

[tool.poetry.dev-dependencies]
pytest = "^7.4.0"
mypy = "^1.4"
"""
    deps = ProjectDependencyExtractor.extract_dependencies(
        file_path="pyproject.toml",
        content=content,
    )
    names = {d.name: d for d in deps}
    assert "httpx" in names
    assert names["httpx"].dependency_type == ProjectDependencyType.RUNTIME
    assert names["httpx"].version_spec == "^0.24.0"

    assert "pytest" in names
    assert names["pytest"].dependency_type == ProjectDependencyType.DEV
    assert names["pytest"].is_dev is True


def test_dependencies_requirements_txt():
    content = """
# Production requirements
requests>=2.28.0
sqlalchemy[asyncio]>=2.0.0
# Comment line
pydantic~=2.0
--extra-index-url https://example.com/pypi
"""
    deps = ProjectDependencyExtractor.extract_dependencies(
        file_path="requirements.txt",
        content=content,
    )
    names = {d.name: d for d in deps}
    assert "requests" in names
    assert names["requests"].version_spec == ">=2.28.0"
    assert names["requests"].dependency_type == ProjectDependencyType.RUNTIME

    assert "sqlalchemy" in names
    assert names["sqlalchemy"].metadata.get("extras") == "asyncio"

    # requirements-dev.txt should be recognized as dev dependencies
    dev_content = "pytest>=7.0.0\nflake8"
    dev_deps = ProjectDependencyExtractor.extract_dependencies(
        file_path="requirements-dev.txt",
        content=dev_content,
    )
    assert all(d.is_dev for d in dev_deps)
    assert all(d.dependency_type == ProjectDependencyType.DEV for d in dev_deps)


def test_dependencies_package_json():
    content = """{
      "dependencies": {
        "express": "^4.18.2",
        "cors": "~2.8.5"
      },
      "devDependencies": {
        "typescript": "^5.0.0",
        "@types/node": "^20.0.0"
      },
      "peerDependencies": {
        "react": ">=18.0.0"
      }
    }"""
    deps = ProjectDependencyExtractor.extract_dependencies(
        file_path="package.json",
        content=content,
    )
    dep_map = {d.name: d for d in deps}
    assert "express" in dep_map
    assert dep_map["express"].dependency_type == ProjectDependencyType.RUNTIME
    assert dep_map["express"].version_spec == "^4.18.2"
    assert dep_map["express"].ecosystem == "npm"

    assert "typescript" in dep_map
    assert dep_map["typescript"].dependency_type == ProjectDependencyType.DEV
    assert dep_map["typescript"].is_dev is True

    assert "react" in dep_map
    assert dep_map["react"].dependency_type == ProjectDependencyType.PEER


def test_dependencies_cargo_toml():
    content = """
[dependencies]
serde = "1.0"
tokio = { version = "1.28", features = ["full"] }

[dev-dependencies]
tempfile = "3.5"

[build-dependencies]
cc = "1.0"
"""
    deps = ProjectDependencyExtractor.extract_dependencies(
        file_path="Cargo.toml",
        content=content,
    )
    dep_map = {d.name: d for d in deps}
    assert "serde" in dep_map
    assert dep_map["serde"].dependency_type == ProjectDependencyType.RUNTIME
    assert dep_map["serde"].version_spec == "1.0"
    assert dep_map["serde"].ecosystem == "cargo"

    assert "tokio" in dep_map
    assert dep_map["tokio"].version_spec == "1.28"
    assert dep_map["tokio"].metadata.get("features") == ["full"]

    assert "tempfile" in dep_map
    assert dep_map["tempfile"].dependency_type == ProjectDependencyType.DEV
    assert dep_map["tempfile"].is_dev is True

    assert "cc" in dep_map
    assert dep_map["cc"].dependency_type == ProjectDependencyType.BUILD


def test_dependencies_go_mod():
    content = """
module github.com/autonomos/agent

go 1.21

require (
    github.com/gin-gonic/gin v1.9.1
    github.com/google/uuid v1.3.0 // indirect
)

require golang.org/x/sync v0.3.0
"""
    deps = ProjectDependencyExtractor.extract_dependencies(
        file_path="go.mod",
        content=content,
    )
    dep_map = {d.name: d for d in deps}
    assert "github.com/gin-gonic/gin" in dep_map
    assert dep_map["github.com/gin-gonic/gin"].version_spec == "v1.9.1"
    assert dep_map["github.com/gin-gonic/gin"].ecosystem == "go"

    assert "github.com/google/uuid" in dep_map
    assert dep_map["github.com/google/uuid"].metadata.get("indirect") is True

    assert "golang.org/x/sync" in dep_map
    assert dep_map["golang.org/x/sync"].version_spec == "v0.3.0"


# -----------------------------------------------------------------------------
# Coordinator Tests
# -----------------------------------------------------------------------------

def test_context_coordinator_extract_file():
    context = ProjectContext(
        identity=ProjectIdentity(project_id="p-test", project_root="/app"),
    )

    # 1. Doc file
    ProjectContextExtractor.extract_file(
        file_path="README.md",
        content="# Welcome to Project",
        project_context=context,
    )
    assert len(context.documentation) == 1
    assert context.documentation[0].title == "Welcome to Project"

    # 2. Config & Manifest file
    pkg_json = '{"dependencies": {"lodash": "^4.17.21"}}'
    ProjectContextExtractor.extract_file(
        file_path="package.json",
        content=pkg_json,
        project_context=context,
    )
    assert len(context.configurations) == 1
    assert len(context.dependencies) == 1
    assert context.dependencies[0].name == "lodash"


def test_context_coordinator_extract_all():
    context = ProjectContext(
        identity=ProjectIdentity(project_id="p-test", project_root="/app"),
    )

    files = [
        ("README.md", "# Test Project\n\nDocs."),
        ("pyproject.toml", '[project]\nname = "test"\ndependencies = ["click>=8.0"]'),
        (".env", "SECRET=123"),
    ]

    ProjectContextExtractor.extract_all(files, context)

    assert len(context.documentation) == 1
    assert len(context.configurations) == 2  # pyproject.toml and blocked .env
    assert len(context.dependencies) == 1
    assert context.dependencies[0].name == "click"

    # Verify blocked file in configurations
    env_cfg = next(c for c in context.configurations if c.config_path == ".env")
    assert env_cfg.safe_metadata.get("status") == "blocked_sensitive_file"
