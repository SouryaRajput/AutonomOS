"""
Code Structure Extraction Models & Deterministic Parsers (Phase 1 / Part 5 / Step 5).

Provides lightweight, deterministic structural extraction from retrieved repository source files:
- Language detection & confirmation
- Functions & asynchronous functions (with parameters, return types, docstrings, line ranges)
- Classes, interfaces, structs, traits, and enums (with methods, bases, docstrings, line ranges)
- Methods (associated with parent classes and line ranges)
- Constants and global variable declarations (with type hints and value previews)
- Module imports (ESM, CommonJS, Python, Go, Rust, C-family)
- Symbol exports (explicit exports, __all__, visibility modifiers)
- Top-level symbol indexing
- Extractable code blocks with 1-indexed LineRanges and SHA-256 checksums
- Safe error handling, size limits, cancellation support, and prompt injection isolation

Operates deterministically without external compiler binaries or non-deterministic LLM analysis.
"""
from __future__ import annotations

import ast
from dataclasses import dataclass, field
from enum import Enum
import hashlib
import logging
import os
import posixpath
import re
from typing import Any, Callable, Optional, Sequence

from core.research.contracts.evidence import EvidenceItem, EvidenceProvenance
from core.research.errors import (
    RepositoryCancelledError,
    RepositoryError,
    RepositoryResourceLimitError,
)
from core.research.repo.models import (
    LineRange,
    RepositoryIdentity,
    RepositoryRevision,
    compute_sha256,
    detect_file_language,
    is_known_binary_extension,
    normalize_repo_path,
    utc_now,
)
from core.research.search.security import sanitize_error
from core.research.types import FactClassification, ResearchConfidence, SourceType

logger = logging.getLogger("AutonomOS.Research.CodeStructure")


# -----------------------------------------------------------------------------
# Enums
# -----------------------------------------------------------------------------

class SymbolKind(str, Enum):
    """Taxonomy of extractable code symbol definitions."""
    FUNCTION = "function"
    ASYNC_FUNCTION = "async_function"
    METHOD = "method"
    CLASS = "class"
    INTERFACE = "interface"
    STRUCT = "struct"
    TRAIT = "trait"
    ENUM = "enum"
    TYPE_ALIAS = "type_alias"
    CONSTANT = "constant"
    VARIABLE = "variable"
    MODULE = "module"
    UNKNOWN = "unknown"


class CodeParsingStatus(str, Enum):
    """Status indicating the result and fidelity of code structure extraction."""
    PARSED = "parsed"
    PARTIAL = "partial"
    FALLBACK_UNSUPPORTED = "fallback_unsupported"
    MALFORMED_SYNTAX = "malformed_syntax"
    TRUNCATED = "truncated"


# -----------------------------------------------------------------------------
# Structural Data Models
# -----------------------------------------------------------------------------

@dataclass
class CodeFunction:
    """
    Structured representation of a function or method definition.
    """
    name: str
    line_range: Optional[LineRange] = None
    signature: str = ""
    docstring: Optional[str] = None
    parent_class: Optional[str] = None
    is_async: bool = False
    is_method: bool = False
    parameters: list[str] = field(default_factory=list)
    return_type: Optional[str] = None
    decorators: list[str] = field(default_factory=list)
    is_exported: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "line_range": self.line_range.to_dict() if self.line_range else None,
            "signature": self.signature,
            "docstring": self.docstring,
            "parent_class": self.parent_class,
            "is_async": self.is_async,
            "is_method": self.is_method,
            "parameters": list(self.parameters),
            "return_type": self.return_type,
            "decorators": list(self.decorators),
            "is_exported": self.is_exported,
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CodeFunction:
        lr_data = data.get("line_range")
        line_range = LineRange.from_dict(lr_data) if isinstance(lr_data, dict) else None
        return cls(
            name=data.get("name", ""),
            line_range=line_range,
            signature=data.get("signature", ""),
            docstring=data.get("docstring"),
            parent_class=data.get("parent_class"),
            is_async=bool(data.get("is_async", False)),
            is_method=bool(data.get("is_method", False)),
            parameters=list(data.get("parameters", [])),
            return_type=data.get("return_type"),
            decorators=list(data.get("decorators", [])),
            is_exported=bool(data.get("is_exported", False)),
            metadata=dict(data.get("metadata", {})),
        )


@dataclass
class CodeClass:
    """
    Structured representation of a class, interface, struct, trait, or enum definition.
    """
    name: str
    line_range: Optional[LineRange] = None
    bases: list[str] = field(default_factory=list)
    methods: list[CodeFunction] = field(default_factory=list)
    docstring: Optional[str] = None
    decorators: list[str] = field(default_factory=list)
    kind: SymbolKind = SymbolKind.CLASS
    is_exported: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "line_range": self.line_range.to_dict() if self.line_range else None,
            "bases": list(self.bases),
            "methods": [m.to_dict() for m in self.methods],
            "docstring": self.docstring,
            "decorators": list(self.decorators),
            "kind": self.kind.value if isinstance(self.kind, SymbolKind) else str(self.kind),
            "is_exported": self.is_exported,
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CodeClass:
        lr_data = data.get("line_range")
        line_range = LineRange.from_dict(lr_data) if isinstance(lr_data, dict) else None
        methods = [CodeFunction.from_dict(m) for m in data.get("methods", [])]
        raw_kind = data.get("kind", SymbolKind.CLASS.value)
        try:
            kind = SymbolKind(raw_kind)
        except ValueError:
            kind = SymbolKind.CLASS

        return cls(
            name=data.get("name", ""),
            line_range=line_range,
            bases=list(data.get("bases", [])),
            methods=methods,
            docstring=data.get("docstring"),
            decorators=list(data.get("decorators", [])),
            kind=kind,
            is_exported=bool(data.get("is_exported", False)),
            metadata=dict(data.get("metadata", {})),
        )


@dataclass
class CodeConstant:
    """
    Structured representation of a top-level constant or variable definition.
    """
    name: str
    line_range: Optional[LineRange] = None
    value_preview: str = ""
    type_annotation: Optional[str] = None
    is_exported: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "line_range": self.line_range.to_dict() if self.line_range else None,
            "value_preview": self.value_preview,
            "type_annotation": self.type_annotation,
            "is_exported": self.is_exported,
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CodeConstant:
        lr_data = data.get("line_range")
        line_range = LineRange.from_dict(lr_data) if isinstance(lr_data, dict) else None
        return cls(
            name=data.get("name", ""),
            line_range=line_range,
            value_preview=data.get("value_preview", ""),
            type_annotation=data.get("type_annotation"),
            is_exported=bool(data.get("is_exported", False)),
            metadata=dict(data.get("metadata", {})),
        )


@dataclass
class CodeImport:
    """
    Structured representation of a module or symbol import statement.
    """
    module: str
    names: list[str] = field(default_factory=list)
    alias: Optional[str] = None
    is_type_only: bool = False
    line_range: Optional[LineRange] = None
    raw_statement: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "module": self.module,
            "names": list(self.names),
            "alias": self.alias,
            "is_type_only": self.is_type_only,
            "line_range": self.line_range.to_dict() if self.line_range else None,
            "raw_statement": self.raw_statement,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CodeImport:
        lr_data = data.get("line_range")
        line_range = LineRange.from_dict(lr_data) if isinstance(lr_data, dict) else None
        return cls(
            module=data.get("module", ""),
            names=list(data.get("names", [])),
            alias=data.get("alias"),
            is_type_only=bool(data.get("is_type_only", False)),
            line_range=line_range,
            raw_statement=data.get("raw_statement", ""),
        )


@dataclass
class CodeExport:
    """
    Structured representation of an exported identifier or module export statement.
    """
    name: str
    kind: SymbolKind = SymbolKind.UNKNOWN
    is_default: bool = False
    line_range: Optional[LineRange] = None
    raw_statement: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "kind": self.kind.value if isinstance(self.kind, SymbolKind) else str(self.kind),
            "is_default": self.is_default,
            "line_range": self.line_range.to_dict() if self.line_range else None,
            "raw_statement": self.raw_statement,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CodeExport:
        lr_data = data.get("line_range")
        line_range = LineRange.from_dict(lr_data) if isinstance(lr_data, dict) else None
        raw_kind = data.get("kind", SymbolKind.UNKNOWN.value)
        try:
            kind = SymbolKind(raw_kind)
        except ValueError:
            kind = SymbolKind.UNKNOWN

        return cls(
            name=data.get("name", ""),
            kind=kind,
            is_default=bool(data.get("is_default", False)),
            line_range=line_range,
            raw_statement=data.get("raw_statement", ""),
        )


@dataclass
class CodeBlock:
    """
    Extracted code block snippet with bounded line range and content hash.
    """
    block_id: str
    block_type: str  # "function", "class", "module_header", "declaration", "raw_snippet"
    name: str = ""
    line_range: Optional[LineRange] = None
    content: str = ""
    content_checksum: str = ""

    def __post_init__(self):
        if not self.content_checksum and self.content:
            self.content_checksum = compute_sha256(self.content)

    def to_dict(self) -> dict[str, Any]:
        return {
            "block_id": self.block_id,
            "block_type": self.block_type,
            "name": self.name,
            "line_range": self.line_range.to_dict() if self.line_range else None,
            "content": self.content,
            "content_checksum": self.content_checksum,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CodeBlock:
        lr_data = data.get("line_range")
        line_range = LineRange.from_dict(lr_data) if isinstance(lr_data, dict) else None
        return cls(
            block_id=data.get("block_id", ""),
            block_type=data.get("block_type", "raw_snippet"),
            name=data.get("name", ""),
            line_range=line_range,
            content=data.get("content", ""),
            content_checksum=data.get("content_checksum", ""),
        )


# -----------------------------------------------------------------------------
# Structured Code File Aggregate
# -----------------------------------------------------------------------------

@dataclass
class StructuredCodeFile:
    """
    Rich structured model representing extracted syntax and symbols from a repository source file.
    """
    file_path: str
    language: Optional[str] = None
    parsing_status: CodeParsingStatus = CodeParsingStatus.PARSED
    content_checksum: str = ""
    line_count: int = 0
    size_bytes: int = 0
    functions: list[CodeFunction] = field(default_factory=list)
    classes: list[CodeClass] = field(default_factory=list)
    constants: list[CodeConstant] = field(default_factory=list)
    imports: list[CodeImport] = field(default_factory=list)
    exports: list[CodeExport] = field(default_factory=list)
    top_level_symbols: list[str] = field(default_factory=list)
    code_blocks: list[CodeBlock] = field(default_factory=list)
    repository_id: str = ""
    revision: str = ""
    provenance: Optional[EvidenceProvenance] = None
    error_message: Optional[str] = None
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def total_functions_count(self) -> int:
        """Total functions including class methods."""
        method_count = sum(len(c.methods) for c in self.classes)
        return len(self.functions) + method_count

    @property
    def total_classes_count(self) -> int:
        return len(self.classes)

    @property
    def total_symbols_count(self) -> int:
        return len(self.top_level_symbols)

    def get_all_methods(self) -> list[CodeFunction]:
        """Flatten and return all methods defined across all classes."""
        methods: list[CodeFunction] = []
        for c in self.classes:
            methods.extend(c.methods)
        return methods

    def find_function(self, name: str) -> Optional[CodeFunction]:
        """Find a top-level function or class method by exact name."""
        for fn in self.functions:
            if fn.name == name:
                return fn
        for c in self.classes:
            for m in c.methods:
                if m.name == name:
                    return m
        return None

    def find_class(self, name: str) -> Optional[CodeClass]:
        """Find a class definition by exact name."""
        for c in self.classes:
            if c.name == name:
                return c
        return None

    def find_constant(self, name: str) -> Optional[CodeConstant]:
        """Find a constant declaration by exact name."""
        for const in self.constants:
            if const.name == name:
                return const
        return None

    def to_evidence_items(
        self,
        request_id: str,
        crawler_task_id: str,
        crawler_id: str,
        question_id: str = "",
        correlation_id: str = "",
        repo_url: str = "",
    ) -> list[EvidenceItem]:
        """
        Generate high-value structured EvidenceItems for classes and functions
        with explicit line ranges, revision context, and provenance.
        """
        evidence_items: list[EvidenceItem] = []
        base_source = repo_url.rstrip("/") if repo_url else f"repo://{self.repository_id or 'default'}"
        if base_source.endswith(".git"):
            base_source = base_source[:-4]
        rev = self.revision or "HEAD"
        rev_tag = rev[:8] if rev else "head"
        repo_tag = self.repository_id or "repo"

        # 1. Classes
        for cls_item in self.classes:
            line_str = f"#L{cls_item.line_range.start_line}-L{cls_item.line_range.end_line}" if cls_item.line_range else ""
            if base_source.startswith("file://") or "://" not in base_source:
                source_ref = f"{base_source}@{rev}/{self.file_path}{line_str}"
            else:
                source_ref = f"{base_source}/blob/{rev}/{self.file_path}{line_str}"

            prov = EvidenceProvenance(
                request_id=request_id,
                crawler_task_id=crawler_task_id,
                crawler_id=crawler_id,
                question_id=question_id,
                source_ref=source_ref,
                correlation_id=correlation_id,
            )
            methods_summary = f" with {len(cls_item.methods)} methods" if cls_item.methods else ""
            fact = f"{cls_item.kind.value.capitalize()} '{cls_item.name}' defined in {self.file_path}{methods_summary}"
            if cls_item.bases:
                fact += f" extending {', '.join(cls_item.bases)}"

            snippet = cls_item.docstring or fact
            evidence_items.append(
                EvidenceItem(
                    evidence_id=f"ev-code-{repo_tag}-{rev_tag}-{self.file_path.replace('/', '_')}-{cls_item.name}",
                    provenance=prov,
                    extracted_fact=fact,
                    content_snippet=snippet[:1000],
                    classification=FactClassification.SOURCE_CLAIM,
                    confidence=ResearchConfidence.SUPPORTED,
                    reliability_score=0.9,
                    source_type=SourceType.REPOSITORY,
                    checksum=self.content_checksum,
                    metadata={
                        "symbol_name": cls_item.name,
                        "symbol_kind": cls_item.kind.value,
                        "file_path": self.file_path,
                        "repository_id": self.repository_id,
                        "revision": self.revision,
                        "line_range": cls_item.line_range.to_dict() if cls_item.line_range else None,
                        "bases": cls_item.bases,
                    },
                )
            )

        # 2. Top-level functions
        for fn_item in self.functions:
            line_str = f"#L{fn_item.line_range.start_line}-L{fn_item.line_range.end_line}" if fn_item.line_range else ""
            if base_source.startswith("file://") or "://" not in base_source:
                source_ref = f"{base_source}@{rev}/{self.file_path}{line_str}"
            else:
                source_ref = f"{base_source}/blob/{rev}/{self.file_path}{line_str}"

            prov = EvidenceProvenance(
                request_id=request_id,
                crawler_task_id=crawler_task_id,
                crawler_id=crawler_id,
                question_id=question_id,
                source_ref=source_ref,
                correlation_id=correlation_id,
            )
            async_prefix = "Async function" if fn_item.is_async else "Function"
            fact = f"{async_prefix} '{fn_item.name}' ({fn_item.signature or '()'}) defined in {self.file_path}"
            snippet = fn_item.docstring or fn_item.signature or fact
            evidence_items.append(
                EvidenceItem(
                    evidence_id=f"ev-code-{repo_tag}-{rev_tag}-{self.file_path.replace('/', '_')}-{fn_item.name}",
                    provenance=prov,
                    extracted_fact=fact,
                    content_snippet=snippet[:1000],
                    classification=FactClassification.SOURCE_CLAIM,
                    confidence=ResearchConfidence.SUPPORTED,
                    reliability_score=0.9,
                    source_type=SourceType.REPOSITORY,
                    checksum=self.content_checksum,
                    metadata={
                        "symbol_name": fn_item.name,
                        "symbol_kind": SymbolKind.ASYNC_FUNCTION.value if fn_item.is_async else SymbolKind.FUNCTION.value,
                        "file_path": self.file_path,
                        "repository_id": self.repository_id,
                        "revision": self.revision,
                        "line_range": fn_item.line_range.to_dict() if fn_item.line_range else None,
                        "signature": fn_item.signature,
                    },
                )
            )

        return evidence_items

    def to_dict(self) -> dict[str, Any]:
        return {
            "file_path": self.file_path,
            "language": self.language,
            "parsing_status": self.parsing_status.value if isinstance(self.parsing_status, CodeParsingStatus) else str(self.parsing_status),
            "content_checksum": self.content_checksum,
            "line_count": self.line_count,
            "size_bytes": self.size_bytes,
            "functions": [f.to_dict() for f in self.functions],
            "classes": [c.to_dict() for c in self.classes],
            "constants": [c.to_dict() for c in self.constants],
            "imports": [i.to_dict() for i in self.imports],
            "exports": [e.to_dict() for e in self.exports],
            "top_level_symbols": list(self.top_level_symbols),
            "code_blocks": [b.to_dict() for b in self.code_blocks],
            "repository_id": self.repository_id,
            "revision": self.revision,
            "provenance": self.provenance.to_dict() if self.provenance else None,
            "error_message": self.error_message,
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> StructuredCodeFile:
        raw_status = data.get("parsing_status", CodeParsingStatus.PARSED.value)
        try:
            status = CodeParsingStatus(raw_status)
        except ValueError:
            status = CodeParsingStatus.PARSED

        prov_data = data.get("provenance")
        provenance = EvidenceProvenance.from_dict(prov_data) if isinstance(prov_data, dict) else None

        return cls(
            file_path=data.get("file_path", ""),
            language=data.get("language"),
            parsing_status=status,
            content_checksum=data.get("content_checksum", ""),
            line_count=int(data.get("line_count", 0)),
            size_bytes=int(data.get("size_bytes", 0)),
            functions=[CodeFunction.from_dict(f) for f in data.get("functions", [])],
            classes=[CodeClass.from_dict(c) for c in data.get("classes", [])],
            constants=[CodeConstant.from_dict(c) for c in data.get("constants", [])],
            imports=[CodeImport.from_dict(i) for i in data.get("imports", [])],
            exports=[CodeExport.from_dict(e) for e in data.get("exports", [])],
            top_level_symbols=list(data.get("top_level_symbols", [])),
            code_blocks=[CodeBlock.from_dict(b) for b in data.get("code_blocks", [])],
            repository_id=data.get("repository_id", ""),
            revision=data.get("revision", ""),
            provenance=provenance,
            error_message=data.get("error_message"),
            metadata=dict(data.get("metadata", {})),
        )


# -----------------------------------------------------------------------------
# Language-Specific Extractors
# -----------------------------------------------------------------------------

class PythonStructureExtractor:
    """
    Deterministic structural extractor for Python source files using standard library `ast`.
    Falls back to defensive regex scanning on syntax errors.
    """

    @classmethod
    def extract(
        cls,
        file_path: str,
        content: str,
        lines: list[str],
        size_bytes: int,
        content_checksum: str,
    ) -> tuple[CodeParsingStatus, list[CodeFunction], list[CodeClass], list[CodeConstant], list[CodeImport], list[CodeExport], list[str], list[CodeBlock], Optional[str]]:
        functions: list[CodeFunction] = []
        classes: list[CodeClass] = []
        constants: list[CodeConstant] = []
        imports: list[CodeImport] = []
        exports: list[CodeExport] = []
        top_level_symbols: list[str] = []
        code_blocks: list[CodeBlock] = []
        parsing_status = CodeParsingStatus.PARSED
        error_msg: Optional[str] = None

        try:
            tree = ast.parse(content, filename=file_path)
        except SyntaxError as syn_err:
            logger.debug(f"Python AST parse syntax error for '{file_path}': {syn_err}. Attempting regex fallback.")
            parsing_status = CodeParsingStatus.PARTIAL
            error_msg = f"SyntaxError in Python source: {syn_err.msg} at line {syn_err.lineno}"
            return cls._extract_regex_fallback(file_path, content, lines, size_bytes, content_checksum, error_msg)
        except Exception as e:
            logger.debug(f"Python AST unexpected parse exception for '{file_path}': {e}.")
            parsing_status = CodeParsingStatus.PARTIAL
            error_msg = f"AST parse error: {sanitize_error(e)}"
            return cls._extract_regex_fallback(file_path, content, lines, size_bytes, content_checksum, error_msg)

        all_export_names: set[str] = set()

        for node in tree.body:
            if isinstance(node, ast.ClassDef):
                cls_obj = cls._parse_class(node, lines)
                classes.append(cls_obj)
                top_level_symbols.append(node.name)
                start_l = node.lineno
                end_l = getattr(node, "end_lineno", start_l)
                cls_block_text = "\n".join(lines[start_l - 1:end_l])
                code_blocks.append(
                    CodeBlock(
                        block_id=f"block-{file_path}:{start_l}-{end_l}",
                        block_type="class",
                        name=node.name,
                        line_range=LineRange(start_line=start_l, end_line=end_l),
                        content=cls_block_text,
                    )
                )

            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                fn_obj = cls._parse_function(node, lines, parent_class=None)
                functions.append(fn_obj)
                top_level_symbols.append(node.name)
                start_l = node.lineno
                end_l = getattr(node, "end_lineno", start_l)
                fn_block_text = "\n".join(lines[start_l - 1:end_l])
                code_blocks.append(
                    CodeBlock(
                        block_id=f"block-{file_path}:{start_l}-{end_l}",
                        block_type="function",
                        name=node.name,
                        line_range=LineRange(start_line=start_l, end_line=end_l),
                        content=fn_block_text,
                    )
                )

            elif isinstance(node, ast.Import):
                raw_stmt = lines[node.lineno - 1] if 0 < node.lineno <= len(lines) else ""
                for alias in node.names:
                    imports.append(
                        CodeImport(
                            module=alias.name,
                            names=[alias.name],
                            alias=alias.asname,
                            line_range=LineRange(start_line=node.lineno, end_line=getattr(node, "end_lineno", node.lineno)),
                            raw_statement=raw_stmt.strip(),
                        )
                    )

            elif isinstance(node, ast.ImportFrom):
                raw_stmt = lines[node.lineno - 1] if 0 < node.lineno <= len(lines) else ""
                mod_name = node.module or ""
                names = [alias.name for alias in node.names]
                imports.append(
                    CodeImport(
                        module=mod_name,
                        names=names,
                        line_range=LineRange(start_line=node.lineno, end_line=getattr(node, "end_lineno", node.lineno)),
                        raw_statement=raw_stmt.strip(),
                    )
                )

            elif isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name):
                        name = target.id
                        if name == "__all__" and isinstance(node.value, (ast.List, ast.Tuple, ast.Set)):
                            for elt in node.value.elts:
                                if isinstance(elt, ast.Constant) and isinstance(elt.value, str):
                                    all_export_names.add(elt.value)
                        val_preview = cls._preview_ast_value(node.value)
                        is_const = name.isupper() or not name.startswith("_")
                        if is_const:
                            start_l = node.lineno
                            end_l = getattr(node, "end_lineno", start_l)
                            constants.append(
                                CodeConstant(
                                    name=name,
                                    line_range=LineRange(start_line=start_l, end_line=end_l),
                                    value_preview=val_preview,
                                    is_exported=name in all_export_names or not name.startswith("_"),
                                )
                            )
                            if name not in top_level_symbols:
                                top_level_symbols.append(name)

            elif isinstance(node, ast.AnnAssign):
                if isinstance(node.target, ast.Name):
                    name = node.target.id
                    val_preview = cls._preview_ast_value(node.value) if node.value else ""
                    type_annot = cls._preview_ast_value(node.annotation)
                    start_l = node.lineno
                    end_l = getattr(node, "end_lineno", start_l)
                    constants.append(
                        CodeConstant(
                            name=name,
                            line_range=LineRange(start_line=start_l, end_line=end_l),
                            value_preview=val_preview,
                            type_annotation=type_annot,
                            is_exported=name in all_export_names or not name.startswith("_"),
                        )
                    )
                    if name not in top_level_symbols:
                        top_level_symbols.append(name)

        if all_export_names:
            for exp_name in sorted(all_export_names):
                exports.append(
                    CodeExport(
                        name=exp_name,
                        kind=SymbolKind.UNKNOWN,
                    )
                )
        else:
            for sym in top_level_symbols:
                if not sym.startswith("_") and sym != "__all__":
                    exports.append(
                        CodeExport(
                            name=sym,
                            kind=SymbolKind.CLASS if any(c.name == sym for c in classes) else SymbolKind.FUNCTION,
                        )
                    )

        classes.sort(key=lambda c: (c.line_range.start_line if c.line_range else 0, c.name))
        functions.sort(key=lambda f: (f.line_range.start_line if f.line_range else 0, f.name))
        constants.sort(key=lambda c: (c.line_range.start_line if c.line_range else 0, c.name))
        imports.sort(key=lambda i: (i.line_range.start_line if i.line_range else 0, i.module))
        exports.sort(key=lambda e: e.name)

        return (
            parsing_status,
            functions,
            classes,
            constants,
            imports,
            exports,
            top_level_symbols,
            code_blocks,
            error_msg,
        )

    @classmethod
    def _parse_class(cls, node: ast.ClassDef, lines: list[str]) -> CodeClass:
        start_l = node.lineno
        end_l = getattr(node, "end_lineno", start_l)
        bases = [cls._preview_ast_value(b) for b in node.bases]
        decorators = [cls._preview_ast_value(d) for d in node.decorator_list]
        docstring = ast.get_docstring(node)

        methods: list[CodeFunction] = []
        for body_item in node.body:
            if isinstance(body_item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                method_obj = cls._parse_function(body_item, lines, parent_class=node.name)
                methods.append(method_obj)

        methods.sort(key=lambda m: (m.line_range.start_line if m.line_range else 0, m.name))

        return CodeClass(
            name=node.name,
            line_range=LineRange(start_line=start_l, end_line=end_l),
            bases=bases,
            methods=methods,
            docstring=docstring,
            decorators=decorators,
            kind=SymbolKind.CLASS,
            is_exported=not node.name.startswith("_"),
        )

    @classmethod
    def _parse_function(
        cls,
        node: ast.FunctionDef | ast.AsyncFunctionDef,
        lines: list[str],
        parent_class: Optional[str] = None,
    ) -> CodeFunction:
        start_l = node.lineno
        end_l = getattr(node, "end_lineno", start_l)
        is_async = isinstance(node, ast.AsyncFunctionDef)
        is_method = parent_class is not None
        docstring = ast.get_docstring(node)
        decorators = [cls._preview_ast_value(d) for d in node.decorator_list]
        return_type = cls._preview_ast_value(node.returns) if node.returns else None

        params: list[str] = []
        for arg in node.args.posonlyargs + node.args.args:
            params.append(arg.arg)
        if node.args.vararg:
            params.append(f"*{node.args.vararg.arg}")
        for arg in node.args.kwonlyargs:
            params.append(arg.arg)
        if node.args.kwarg:
            params.append(f"**{node.args.kwarg.arg}")

        sig = f"def {node.name}({', '.join(params)})"
        if is_async:
            sig = f"async {sig}"
        if return_type:
            sig += f" -> {return_type}"

        return CodeFunction(
            name=node.name,
            line_range=LineRange(start_line=start_l, end_line=end_l),
            signature=sig,
            docstring=docstring,
            parent_class=parent_class,
            is_async=is_async,
            is_method=is_method,
            parameters=params,
            return_type=return_type,
            decorators=decorators,
            is_exported=not node.name.startswith("_"),
        )

    @classmethod
    def _preview_ast_value(cls, node: Optional[ast.AST]) -> str:
        if node is None:
            return ""
        if hasattr(ast, "unparse"):
            try:
                return ast.unparse(node)
            except Exception:
                pass
        if isinstance(node, ast.Name):
            return node.id
        if isinstance(node, ast.Constant):
            return repr(node.value)
        if isinstance(node, ast.Attribute):
            return f"{cls._preview_ast_value(node.value)}.{node.attr}"
        return type(node).__name__

    @classmethod
    def _extract_regex_fallback(
        cls,
        file_path: str,
        content: str,
        lines: list[str],
        size_bytes: int,
        content_checksum: str,
        error_msg: str,
    ) -> tuple[CodeParsingStatus, list[CodeFunction], list[CodeClass], list[CodeConstant], list[CodeImport], list[CodeExport], list[str], list[CodeBlock], Optional[str]]:
        functions: list[CodeFunction] = []
        classes: list[CodeClass] = []
        constants: list[CodeConstant] = []
        imports: list[CodeImport] = []
        exports: list[CodeExport] = []
        top_level_symbols: list[str] = []
        code_blocks: list[CodeBlock] = []

        for idx, line in enumerate(lines, start=1):
            sline = line.strip()
            cls_match = re.match(r"^\s*class\s+([A-Za-z0-9_]+)(?:\(([^)]*)\))?:", sline)
            if cls_match:
                cname = cls_match.group(1)
                bases_raw = cls_match.group(2)
                bases = [b.strip() for b in bases_raw.split(",") if b.strip()] if bases_raw else []
                classes.append(
                    CodeClass(
                        name=cname,
                        line_range=LineRange(start_line=idx, end_line=idx),
                        bases=bases,
                        is_exported=not cname.startswith("_"),
                    )
                )
                if cname not in top_level_symbols:
                    top_level_symbols.append(cname)
                continue

            fn_match = re.match(r"^\s*(async\s+)?def\s+([A-Za-z0-9_]+)(?:\s*\(([^)]*)\)?)?", sline)
            if fn_match:
                is_async = bool(fn_match.group(1))
                fname = fn_match.group(2)
                params_raw = fn_match.group(3) or ""
                params = [p.strip() for p in params_raw.split(",") if p.strip()]
                is_indented = line.startswith((" ", "\t"))
                if is_indented and classes:
                    classes[-1].methods.append(
                        CodeFunction(
                            name=fname,
                            line_range=LineRange(start_line=idx, end_line=idx),
                            signature=sline,
                            parent_class=classes[-1].name,
                            is_async=is_async,
                            is_method=True,
                            parameters=params,
                            is_exported=not fname.startswith("_"),
                        )
                    )
                else:
                    functions.append(
                        CodeFunction(
                            name=fname,
                            line_range=LineRange(start_line=idx, end_line=idx),
                            signature=sline,
                            is_async=is_async,
                            is_method=False,
                            parameters=params,
                            is_exported=not fname.startswith("_"),
                        )
                    )
                    if fname not in top_level_symbols:
                        top_level_symbols.append(fname)
                continue

            if sline.startswith("import "):
                imports.append(
                    CodeImport(
                        module=sline[7:].strip(),
                        line_range=LineRange(start_line=idx, end_line=idx),
                        raw_statement=sline,
                    )
                )
            elif sline.startswith("from "):
                m = re.match(r"^from\s+([A-Za-z0-9_.]+)\s+import\s+(.*)", sline)
                if m:
                    imports.append(
                        CodeImport(
                            module=m.group(1),
                            names=[n.strip() for n in m.group(2).split(",") if n.strip()],
                            line_range=LineRange(start_line=idx, end_line=idx),
                            raw_statement=sline,
                        )
                    )

        return (
            CodeParsingStatus.PARTIAL,
            functions,
            classes,
            constants,
            imports,
            exports,
            top_level_symbols,
            code_blocks,
            error_msg,
        )


class TypeScriptJavaScriptExtractor:
    """
    Deterministic regex/pattern extractor for TypeScript, JavaScript, TSX, and JSX source files.
    Extracts classes, interfaces, types, enums, functions, arrow functions, constants, imports, and exports.
    """

    @classmethod
    def extract(
        cls,
        file_path: str,
        content: str,
        lines: list[str],
        size_bytes: int,
        content_checksum: str,
    ) -> tuple[CodeParsingStatus, list[CodeFunction], list[CodeClass], list[CodeConstant], list[CodeImport], list[CodeExport], list[str], list[CodeBlock], Optional[str]]:
        functions: list[CodeFunction] = []
        classes: list[CodeClass] = []
        constants: list[CodeConstant] = []
        imports: list[CodeImport] = []
        exports: list[CodeExport] = []
        top_level_symbols: list[str] = []
        code_blocks: list[CodeBlock] = []

        for idx, line in enumerate(lines, start=1):
            sline = line.strip()
            esm_m = re.match(r"^import\s+(?:type\s+)?(?:([\w*\s{},]+)\s+from\s+)?['\"]([^'\"]+)['\"]", sline)
            if esm_m:
                names_part = esm_m.group(1) or ""
                mod_name = esm_m.group(2)
                is_type = "type " in sline[:12]
                names = [n.strip() for n in re.findall(r"[\w]+", names_part) if n.strip() not in ("from", "type", "as")]
                imports.append(
                    CodeImport(
                        module=mod_name,
                        names=names,
                        is_type_only=is_type,
                        line_range=LineRange(start_line=idx, end_line=idx),
                        raw_statement=sline,
                    )
                )
                continue

            cjs_m = re.match(r"^(?:const|let|var)\s+([\w\s{},]+)\s*=\s*require\(['\"]([^'\"]+)['\"]\)", sline)
            if cjs_m:
                names_part = cjs_m.group(1)
                mod_name = cjs_m.group(2)
                names = [n.strip() for n in re.findall(r"[\w]+", names_part) if n.strip()]
                imports.append(
                    CodeImport(
                        module=mod_name,
                        names=names,
                        line_range=LineRange(start_line=idx, end_line=idx),
                        raw_statement=sline,
                    )
                )
                continue

            type_m = re.match(r"^(?:export\s+)?(?:default\s+)?(?:abstract\s+)?(class|interface|enum|type)\s+([A-Za-z0-9_]+)(?:\s+extends\s+([^{]+))?(?:\s+implements\s+([^{]+))?", sline)
            if type_m:
                kw = type_m.group(1)
                cname = type_m.group(2)
                ext_part = type_m.group(3)
                impl_part = type_m.group(4)
                bases: list[str] = []
                if ext_part:
                    bases.extend([b.strip() for b in ext_part.split(",") if b.strip()])
                if impl_part:
                    bases.extend([b.strip() for b in impl_part.split(",") if b.strip()])

                kind = SymbolKind.INTERFACE if kw == "interface" else SymbolKind.ENUM if kw == "enum" else SymbolKind.TYPE_ALIAS if kw == "type" else SymbolKind.CLASS
                is_exported = "export" in sline[:10]

                classes.append(
                    CodeClass(
                        name=cname,
                        line_range=LineRange(start_line=idx, end_line=idx),
                        bases=bases,
                        kind=kind,
                        is_exported=is_exported,
                    )
                )
                if cname not in top_level_symbols:
                    top_level_symbols.append(cname)
                if is_exported:
                    exports.append(
                        CodeExport(
                            name=cname,
                            kind=kind,
                            is_default="default" in sline[:15],
                            line_range=LineRange(start_line=idx, end_line=idx),
                            raw_statement=sline,
                        )
                    )
                continue

            fn_m = re.match(r"^(?:export\s+)?(?:default\s+)?(async\s+)?function\s*([A-Za-z0-9_]*)\s*\(([^)]*)\)(?:\s*:\s*([^{]+))?", sline)
            if fn_m:
                is_async = bool(fn_m.group(1))
                fname = fn_m.group(2) or "anonymous"
                params_raw = fn_m.group(3)
                ret_type = fn_m.group(4).strip() if fn_m.group(4) else None
                params = [p.strip() for p in params_raw.split(",") if p.strip()]
                is_exported = "export" in sline[:10]

                functions.append(
                    CodeFunction(
                        name=fname,
                        line_range=LineRange(start_line=idx, end_line=idx),
                        signature=sline,
                        is_async=is_async,
                        parameters=params,
                        return_type=ret_type,
                        is_exported=is_exported,
                    )
                )
                if fname not in top_level_symbols:
                    top_level_symbols.append(fname)
                if is_exported:
                    exports.append(
                        CodeExport(
                            name=fname,
                            kind=SymbolKind.FUNCTION,
                            is_default="default" in sline[:15],
                            line_range=LineRange(start_line=idx, end_line=idx),
                            raw_statement=sline,
                        )
                    )
                continue

            arrow_m = re.match(
                r"^(?:export\s+)?(?:const|let|var)\s+([A-Za-z0-9_]+)(?:\s*:\s*[^=]+)?\s*=\s*(async\s+)?(?:\(([^)]*)\)|([A-Za-z0-9_]+))(?:\s*:\s*([^{=]+?))?\s*=>",
                sline,
            )
            if arrow_m:
                fname = arrow_m.group(1)
                is_async = bool(arrow_m.group(2))
                params_raw = arrow_m.group(3) or arrow_m.group(4) or ""
                ret_type = arrow_m.group(5).strip() if arrow_m.group(5) else None
                params = [p.strip() for p in params_raw.split(",") if p.strip()]
                is_exported = "export" in sline[:10]

                functions.append(
                    CodeFunction(
                        name=fname,
                        line_range=LineRange(start_line=idx, end_line=idx),
                        signature=sline,
                        is_async=is_async,
                        parameters=params,
                        return_type=ret_type,
                        is_exported=is_exported,
                    )
                )
                if fname not in top_level_symbols:
                    top_level_symbols.append(fname)
                if is_exported:
                    exports.append(
                        CodeExport(
                            name=fname,
                            kind=SymbolKind.FUNCTION,
                            line_range=LineRange(start_line=idx, end_line=idx),
                            raw_statement=sline,
                        )
                    )
                continue

            const_m = re.match(r"^(?:export\s+)?const\s+([A-Za-z0-9_]+)(?:\s*:\s*([^=]+))?\s*=\s*(.*)", sline)
            if const_m:
                cname = const_m.group(1)
                type_annot = const_m.group(2).strip() if const_m.group(2) else None
                val_preview = const_m.group(3).strip()[:60]
                is_exported = "export" in sline[:10]

                constants.append(
                    CodeConstant(
                        name=cname,
                        line_range=LineRange(start_line=idx, end_line=idx),
                        value_preview=val_preview,
                        type_annotation=type_annot,
                        is_exported=is_exported,
                    )
                )
                if cname not in top_level_symbols:
                    top_level_symbols.append(cname)
                if is_exported:
                    exports.append(
                        CodeExport(
                            name=cname,
                            kind=SymbolKind.CONSTANT,
                            line_range=LineRange(start_line=idx, end_line=idx),
                            raw_statement=sline,
                        )
                    )
                continue

            export_named_m = re.match(r"^export\s+\{([^}]+)\}", sline)
            if export_named_m:
                raw_names = export_named_m.group(1)
                for item in raw_names.split(","):
                    clean = item.strip()
                    if clean:
                        exp_name = clean.split(" as ")[-1].strip() if " as " in clean else clean
                        exports.append(
                            CodeExport(
                                name=exp_name,
                                kind=SymbolKind.UNKNOWN,
                                line_range=LineRange(start_line=idx, end_line=idx),
                                raw_statement=sline,
                            )
                        )

        classes.sort(key=lambda c: (c.line_range.start_line if c.line_range else 0, c.name))
        functions.sort(key=lambda f: (f.line_range.start_line if f.line_range else 0, f.name))
        constants.sort(key=lambda c: (c.line_range.start_line if c.line_range else 0, c.name))
        imports.sort(key=lambda i: (i.line_range.start_line if i.line_range else 0, i.module))
        exports.sort(key=lambda e: (e.line_range.start_line if e.line_range else 0, e.name))

        return (
            CodeParsingStatus.PARSED,
            functions,
            classes,
            constants,
            imports,
            exports,
            top_level_symbols,
            code_blocks,
            None,
        )


class GoStructureExtractor:
    """
    Deterministic regex/pattern extractor for Go source files.
    Extracts package, imports, structs, interfaces, functions, receiver methods, and constants.
    """

    @classmethod
    def extract(
        cls,
        file_path: str,
        content: str,
        lines: list[str],
        size_bytes: int,
        content_checksum: str,
    ) -> tuple[CodeParsingStatus, list[CodeFunction], list[CodeClass], list[CodeConstant], list[CodeImport], list[CodeExport], list[str], list[CodeBlock], Optional[str]]:
        functions: list[CodeFunction] = []
        classes: list[CodeClass] = []
        constants: list[CodeConstant] = []
        imports: list[CodeImport] = []
        exports: list[CodeExport] = []
        top_level_symbols: list[str] = []
        code_blocks: list[CodeBlock] = []

        in_import_block = False

        for idx, line in enumerate(lines, start=1):
            sline = line.strip()

            if sline == "import (":
                in_import_block = True
                continue
            if in_import_block:
                if sline == ")":
                    in_import_block = False
                else:
                    mod_m = re.match(r"^(?:([A-Za-z0-9_]+)\s+)?['\"]([^'\"]+)['\"]", sline)
                    if mod_m:
                        alias = mod_m.group(1)
                        mod_name = mod_m.group(2)
                        imports.append(
                            CodeImport(
                                module=mod_name,
                                alias=alias,
                                line_range=LineRange(start_line=idx, end_line=idx),
                                raw_statement=sline,
                            )
                        )
                continue

            single_imp = re.match(r"^import\s+(?:([A-Za-z0-9_]+)\s+)?['\"]([^'\"]+)['\"]", sline)
            if single_imp:
                alias = single_imp.group(1)
                mod_name = single_imp.group(2)
                imports.append(
                    CodeImport(
                        module=mod_name,
                        alias=alias,
                        line_range=LineRange(start_line=idx, end_line=idx),
                        raw_statement=sline,
                    )
                )
                continue

            type_m = re.match(r"^type\s+([A-Za-z0-9_]+)\s+(struct|interface)", sline)
            if type_m:
                tname = type_m.group(1)
                kind_str = type_m.group(2)
                kind = SymbolKind.STRUCT if kind_str == "struct" else SymbolKind.INTERFACE
                is_exp = tname[0].isupper()
                classes.append(
                    CodeClass(
                        name=tname,
                        line_range=LineRange(start_line=idx, end_line=idx),
                        kind=kind,
                        is_exported=is_exp,
                    )
                )
                if tname not in top_level_symbols:
                    top_level_symbols.append(tname)
                if is_exp:
                    exports.append(
                        CodeExport(
                            name=tname,
                            kind=kind,
                            line_range=LineRange(start_line=idx, end_line=idx),
                            raw_statement=sline,
                        )
                    )
                continue

            method_m = re.match(r"^func\s+\(([^)]+)\)\s*([A-Za-z0-9_]+)\s*\(([^)]*)\)(?:\s*(.*))?", sline)
            if method_m:
                receiver_raw = method_m.group(1)
                mname = method_m.group(2)
                params_raw = method_m.group(3)
                ret_part = method_m.group(4) or ""
                parent = receiver_raw.split()[-1].lstrip("*") if receiver_raw else None
                is_exp = mname[0].isupper()
                params = [p.strip() for p in params_raw.split(",") if p.strip()]

                fn_obj = CodeFunction(
                    name=mname,
                    line_range=LineRange(start_line=idx, end_line=idx),
                    signature=sline,
                    parent_class=parent,
                    is_method=True,
                    parameters=params,
                    return_type=ret_part.strip() or None,
                    is_exported=is_exp,
                )
                matched_cls = False
                for c in classes:
                    if c.name == parent:
                        c.methods.append(fn_obj)
                        matched_cls = True
                        break
                if not matched_cls:
                    functions.append(fn_obj)

                if is_exp:
                    exports.append(
                        CodeExport(
                            name=mname,
                            kind=SymbolKind.METHOD,
                            line_range=LineRange(start_line=idx, end_line=idx),
                            raw_statement=sline,
                        )
                    )
                continue

            fn_m = re.match(r"^func\s+([A-Za-z0-9_]+)\s*\(([^)]*)\)(?:\s*(.*))?", sline)
            if fn_m:
                fname = fn_m.group(1)
                params_raw = fn_m.group(2)
                ret_part = fn_m.group(3) or ""
                is_exp = fname[0].isupper()
                params = [p.strip() for p in params_raw.split(",") if p.strip()]

                functions.append(
                    CodeFunction(
                        name=fname,
                        line_range=LineRange(start_line=idx, end_line=idx),
                        signature=sline,
                        parameters=params,
                        return_type=ret_part.strip() or None,
                        is_exported=is_exp,
                    )
                )
                if fname not in top_level_symbols:
                    top_level_symbols.append(fname)
                if is_exp:
                    exports.append(
                        CodeExport(
                            name=fname,
                            kind=SymbolKind.FUNCTION,
                            line_range=LineRange(start_line=idx, end_line=idx),
                            raw_statement=sline,
                        )
                    )
                continue

            const_m = re.match(r"^const\s+([A-Za-z0-9_]+)(?:\s+([A-Za-z0-9_]+))?\s*=\s*(.*)", sline)
            if const_m:
                cname = const_m.group(1)
                t_annot = const_m.group(2)
                val_prev = const_m.group(3)[:60]
                is_exp = cname[0].isupper()

                constants.append(
                    CodeConstant(
                        name=cname,
                        line_range=LineRange(start_line=idx, end_line=idx),
                        value_preview=val_prev,
                        type_annotation=t_annot,
                        is_exported=is_exp,
                    )
                )
                if cname not in top_level_symbols:
                    top_level_symbols.append(cname)
                if is_exp:
                    exports.append(
                        CodeExport(
                            name=cname,
                            kind=SymbolKind.CONSTANT,
                            line_range=LineRange(start_line=idx, end_line=idx),
                            raw_statement=sline,
                        )
                    )
                continue

        classes.sort(key=lambda c: (c.line_range.start_line if c.line_range else 0, c.name))
        functions.sort(key=lambda f: (f.line_range.start_line if f.line_range else 0, f.name))
        constants.sort(key=lambda c: (c.line_range.start_line if c.line_range else 0, c.name))
        imports.sort(key=lambda i: (i.line_range.start_line if i.line_range else 0, i.module))
        exports.sort(key=lambda e: (e.line_range.start_line if e.line_range else 0, e.name))

        return (
            CodeParsingStatus.PARSED,
            functions,
            classes,
            constants,
            imports,
            exports,
            top_level_symbols,
            code_blocks,
            None,
        )


class RustStructureExtractor:
    """
    Deterministic regex/pattern extractor for Rust source files.
    Extracts use statements, structs, enums, traits, impl blocks, functions, and constants.
    """

    @classmethod
    def extract(
        cls,
        file_path: str,
        content: str,
        lines: list[str],
        size_bytes: int,
        content_checksum: str,
    ) -> tuple[CodeParsingStatus, list[CodeFunction], list[CodeClass], list[CodeConstant], list[CodeImport], list[CodeExport], list[str], list[CodeBlock], Optional[str]]:
        functions: list[CodeFunction] = []
        classes: list[CodeClass] = []
        constants: list[CodeConstant] = []
        imports: list[CodeImport] = []
        exports: list[CodeExport] = []
        top_level_symbols: list[str] = []
        code_blocks: list[CodeBlock] = []

        current_impl: Optional[str] = None

        for idx, line in enumerate(lines, start=1):
            sline = line.strip()

            use_m = re.match(r"^(pub\s+)?use\s+([^;]+);", sline)
            if use_m:
                is_pub = bool(use_m.group(1))
                use_path = use_m.group(2).strip()
                imports.append(
                    CodeImport(
                        module=use_path,
                        line_range=LineRange(start_line=idx, end_line=idx),
                        raw_statement=sline,
                    )
                )
                if is_pub:
                    exports.append(
                        CodeExport(
                            name=use_path.split("::")[-1],
                            kind=SymbolKind.UNKNOWN,
                            line_range=LineRange(start_line=idx, end_line=idx),
                            raw_statement=sline,
                        )
                    )
                continue

            type_m = re.match(r"^(pub\s+)?(struct|enum|trait)\s+([A-Za-z0-9_]+)", sline)
            if type_m:
                is_pub = bool(type_m.group(1))
                kw = type_m.group(2)
                tname = type_m.group(3)
                kind = SymbolKind.STRUCT if kw == "struct" else SymbolKind.ENUM if kw == "enum" else SymbolKind.TRAIT

                classes.append(
                    CodeClass(
                        name=tname,
                        line_range=LineRange(start_line=idx, end_line=idx),
                        kind=kind,
                        is_exported=is_pub,
                    )
                )
                if tname not in top_level_symbols:
                    top_level_symbols.append(tname)
                if is_pub:
                    exports.append(
                        CodeExport(
                            name=tname,
                            kind=kind,
                            line_range=LineRange(start_line=idx, end_line=idx),
                            raw_statement=sline,
                        )
                    )
                continue

            impl_m = re.match(r"^impl(?:\s+<[^>]+>)?\s+(?:([A-Za-z0-9_]+)\s+for\s+)?([A-Za-z0-9_]+)", sline)
            if impl_m:
                struct_name = impl_m.group(2)
                current_impl = struct_name
                continue
            if sline == "}":
                current_impl = None

            fn_m = re.match(r"^(pub(?:\([^)]+\))?\s+)?(async\s+)?fn\s+([A-Za-z0-9_]+)\s*(?:<[^>]+>)?\s*\(([^)]*)\)(?:\s*->\s*([^{]+))?", sline)
            if fn_m:
                is_pub = bool(fn_m.group(1))
                is_async = bool(fn_m.group(2))
                fname = fn_m.group(3)
                params_raw = fn_m.group(4)
                ret_type = fn_m.group(5).strip() if fn_m.group(5) else None
                params = [p.strip() for p in params_raw.split(",") if p.strip()]

                fn_obj = CodeFunction(
                    name=fname,
                    line_range=LineRange(start_line=idx, end_line=idx),
                    signature=sline,
                    parent_class=current_impl,
                    is_method=current_impl is not None,
                    is_async=is_async,
                    parameters=params,
                    return_type=ret_type,
                    is_exported=is_pub,
                )

                if current_impl:
                    for c in classes:
                        if c.name == current_impl:
                            c.methods.append(fn_obj)
                            break
                    else:
                        functions.append(fn_obj)
                else:
                    functions.append(fn_obj)
                    if fname not in top_level_symbols:
                        top_level_symbols.append(fname)

                if is_pub:
                    exports.append(
                        CodeExport(
                            name=fname,
                            kind=SymbolKind.FUNCTION,
                            line_range=LineRange(start_line=idx, end_line=idx),
                            raw_statement=sline,
                        )
                    )
                continue

            const_m = re.match(r"^(pub\s+)?const\s+([A-Za-z0-9_]+)\s*:\s*([^=]+)\s*=\s*(.*);", sline)
            if const_m:
                is_pub = bool(const_m.group(1))
                cname = const_m.group(2)
                type_annot = const_m.group(3).strip()
                val_prev = const_m.group(4).strip()[:60]

                constants.append(
                    CodeConstant(
                        name=cname,
                        line_range=LineRange(start_line=idx, end_line=idx),
                        value_preview=val_prev,
                        type_annotation=type_annot,
                        is_exported=is_pub,
                    )
                )
                if cname not in top_level_symbols:
                    top_level_symbols.append(cname)
                if is_pub:
                    exports.append(
                        CodeExport(
                            name=cname,
                            kind=SymbolKind.CONSTANT,
                            line_range=LineRange(start_line=idx, end_line=idx),
                            raw_statement=sline,
                        )
                    )
                continue

        classes.sort(key=lambda c: (c.line_range.start_line if c.line_range else 0, c.name))
        functions.sort(key=lambda f: (f.line_range.start_line if f.line_range else 0, f.name))
        constants.sort(key=lambda c: (c.line_range.start_line if c.line_range else 0, c.name))
        imports.sort(key=lambda i: (i.line_range.start_line if i.line_range else 0, i.module))
        exports.sort(key=lambda e: (e.line_range.start_line if e.line_range else 0, e.name))

        return (
            CodeParsingStatus.PARSED,
            functions,
            classes,
            constants,
            imports,
            exports,
            top_level_symbols,
            code_blocks,
            None,
        )


class GenericCStyleExtractor:
    """
    Deterministic regex extractor for C, C++, Java, C#, Kotlin, PHP, and other C-family languages.
    """

    @classmethod
    def extract(
        cls,
        file_path: str,
        content: str,
        lines: list[str],
        size_bytes: int,
        content_checksum: str,
    ) -> tuple[CodeParsingStatus, list[CodeFunction], list[CodeClass], list[CodeConstant], list[CodeImport], list[CodeExport], list[str], list[CodeBlock], Optional[str]]:
        functions: list[CodeFunction] = []
        classes: list[CodeClass] = []
        constants: list[CodeConstant] = []
        imports: list[CodeImport] = []
        exports: list[CodeExport] = []
        top_level_symbols: list[str] = []
        code_blocks: list[CodeBlock] = []

        current_class: Optional[str] = None

        for idx, line in enumerate(lines, start=1):
            sline = line.strip()

            inc_m = re.match(r"^(?:#include|import|using|package)\s+([<\"A-Za-z0-9_./*>]+)", sline)
            if inc_m:
                mod_name = inc_m.group(1).strip("<>\"'; ")
                imports.append(
                    CodeImport(
                        module=mod_name,
                        line_range=LineRange(start_line=idx, end_line=idx),
                        raw_statement=sline,
                    )
                )
                continue

            type_m = re.match(r"^(?:public\s+|protected\s+|private\s+|static\s+|abstract\s+|final\s+)*(class|interface|struct|enum)\s+([A-Za-z0-9_]+)(?:\s*(?:extends|implements|:)\s*([^{]+))?", sline)
            if type_m:
                kw = type_m.group(1)
                cname = type_m.group(2)
                bases_raw = type_m.group(3)
                bases = [b.strip() for b in bases_raw.split(",") if b.strip()] if bases_raw else []
                kind = SymbolKind.INTERFACE if kw == "interface" else SymbolKind.ENUM if kw == "enum" else SymbolKind.STRUCT if kw == "struct" else SymbolKind.CLASS
                is_pub = "public" in sline[:10]
                current_class = cname

                classes.append(
                    CodeClass(
                        name=cname,
                        line_range=LineRange(start_line=idx, end_line=idx),
                        bases=bases,
                        kind=kind,
                        is_exported=is_pub,
                    )
                )
                if cname not in top_level_symbols:
                    top_level_symbols.append(cname)
                if is_pub:
                    exports.append(
                        CodeExport(
                            name=cname,
                            kind=kind,
                            line_range=LineRange(start_line=idx, end_line=idx),
                            raw_statement=sline,
                        )
                    )
                continue

            fn_m = re.match(r"^(?:public\s+|protected\s+|private\s+|static\s+|final\s+|async\s+)*(?:(?:fun|def|void|[A-Za-z0-9_<>[\]]+)\s+)+([A-Za-z0-9_]+)\s*\(([^)]*)\)", sline)
            if fn_m:
                fname = fn_m.group(1)
                if fname not in ("if", "for", "while", "switch", "catch", "return", "class", "interface"):
                    params_raw = fn_m.group(2)
                    params = [p.strip() for p in params_raw.split(",") if p.strip()]
                    is_pub = "public" in sline[:10]

                    fn_obj = CodeFunction(
                        name=fname,
                        line_range=LineRange(start_line=idx, end_line=idx),
                        signature=sline,
                        parent_class=current_class,
                        is_method=current_class is not None,
                        parameters=params,
                        is_exported=is_pub,
                    )

                    if current_class:
                        for c in classes:
                            if c.name == current_class:
                                c.methods.append(fn_obj)
                                break
                        else:
                            functions.append(fn_obj)
                    else:
                        functions.append(fn_obj)
                        if fname not in top_level_symbols:
                            top_level_symbols.append(fname)

                    if is_pub:
                        exports.append(
                            CodeExport(
                                name=fname,
                                kind=SymbolKind.FUNCTION,
                                line_range=LineRange(start_line=idx, end_line=idx),
                                raw_statement=sline,
                            )
                        )
                    continue

            const_m = re.match(r"^(?:#define\s+([A-Za-z0-9_]+)\s+(.*)|(?:public\s+|private\s+|static\s+|final\s+|const\s+)+[A-Za-z0-9_<>[\]]+\s+([A-Za-z0-9_]+)\s*=\s*(.*);)", sline)
            if const_m:
                cname = const_m.group(1) or const_m.group(3)
                val_prev = (const_m.group(2) or const_m.group(4) or "").strip()[:60]
                if cname:
                    constants.append(
                        CodeConstant(
                            name=cname,
                            line_range=LineRange(start_line=idx, end_line=idx),
                            value_preview=val_prev,
                            is_exported="public" in sline[:10],
                        )
                    )
                    if cname not in top_level_symbols:
                        top_level_symbols.append(cname)
                    continue

        classes.sort(key=lambda c: (c.line_range.start_line if c.line_range else 0, c.name))
        functions.sort(key=lambda f: (f.line_range.start_line if f.line_range else 0, f.name))
        constants.sort(key=lambda c: (c.line_range.start_line if c.line_range else 0, c.name))
        imports.sort(key=lambda i: (i.line_range.start_line if i.line_range else 0, i.module))
        exports.sort(key=lambda e: (e.line_range.start_line if e.line_range else 0, e.name))

        return (
            CodeParsingStatus.PARSED,
            functions,
            classes,
            constants,
            imports,
            exports,
            top_level_symbols,
            code_blocks,
            None,
        )


# -----------------------------------------------------------------------------
# Unified Code Structure Extractor Engine
# -----------------------------------------------------------------------------

class CodeStructureExtractor:
    """
    Main entry point for extracting deterministic code structure from repository source files.
    Dispatches to language-specific extractors or graceful fallback.
    """

    DEFAULT_MAX_PARSE_BYTES = 500_000

    @classmethod
    def extract_structure(
        cls,
        file_path: str,
        content: str,
        language: Optional[str] = None,
        max_parse_bytes: int = DEFAULT_MAX_PARSE_BYTES,
        repository_id: str = "",
        revision: str = "",
        provenance: Optional[EvidenceProvenance] = None,
        is_cancelled: Optional[Callable[[], bool]] = None,
    ) -> StructuredCodeFile:
        """
        Extract code structure from the given source file content.
        Enforces cancellation checks, size limits, and language-specific parsers.
        """
        if is_cancelled and is_cancelled():
            raise RepositoryCancelledError(
                repo_target=repository_id or file_path,
                operation="extract_structure",
                message="Code structure extraction cancelled.",
            )

        norm_path = normalize_repo_path(file_path)
        content_str = content or ""
        raw_bytes = content_str.encode("utf-8")
        size_bytes = len(raw_bytes)
        content_checksum = compute_sha256(raw_bytes)
        lines = content_str.splitlines()
        line_count = len(lines)

        detected_lang = language or detect_file_language(norm_path)

        if is_known_binary_extension(norm_path) or not content_str.strip() or "\x00" in content_str:
            return StructuredCodeFile(
                file_path=norm_path,
                language=detected_lang,
                parsing_status=CodeParsingStatus.FALLBACK_UNSUPPORTED,
                content_checksum=content_checksum,
                line_count=line_count,
                size_bytes=size_bytes,
                repository_id=repository_id,
                revision=revision,
                provenance=provenance,
            )

        if size_bytes > max_parse_bytes:
            logger.info(f"File '{norm_path}' ({size_bytes} bytes) exceeds max_parse_bytes ({max_parse_bytes}); truncating structure.")
            return StructuredCodeFile(
                file_path=norm_path,
                language=detected_lang,
                parsing_status=CodeParsingStatus.TRUNCATED,
                content_checksum=content_checksum,
                line_count=line_count,
                size_bytes=size_bytes,
                repository_id=repository_id,
                revision=revision,
                provenance=provenance,
                error_message=f"File size {size_bytes} exceeds limit {max_parse_bytes} bytes.",
            )

        lang_key = (detected_lang or "").lower().strip()

        try:
            if lang_key == "python":
                (
                    status,
                    functions,
                    classes,
                    constants,
                    imports,
                    exports,
                    top_level_symbols,
                    code_blocks,
                    err_msg,
                ) = PythonStructureExtractor.extract(
                    norm_path, content_str, lines, size_bytes, content_checksum
                )

            elif lang_key in ("typescript", "javascript", "tsx", "jsx", "ts", "js"):
                (
                    status,
                    functions,
                    classes,
                    constants,
                    imports,
                    exports,
                    top_level_symbols,
                    code_blocks,
                    err_msg,
                ) = TypeScriptJavaScriptExtractor.extract(
                    norm_path, content_str, lines, size_bytes, content_checksum
                )

            elif lang_key in ("go", "golang"):
                (
                    status,
                    functions,
                    classes,
                    constants,
                    imports,
                    exports,
                    top_level_symbols,
                    code_blocks,
                    err_msg,
                ) = GoStructureExtractor.extract(
                    norm_path, content_str, lines, size_bytes, content_checksum
                )

            elif lang_key in ("rust", "rs"):
                (
                    status,
                    functions,
                    classes,
                    constants,
                    imports,
                    exports,
                    top_level_symbols,
                    code_blocks,
                    err_msg,
                ) = RustStructureExtractor.extract(
                    norm_path, content_str, lines, size_bytes, content_checksum
                )

            elif lang_key in ("java", "c", "cpp", "c++", "csharp", "c#", "kotlin", "php", "swift", "scala", "dart"):
                (
                    status,
                    functions,
                    classes,
                    constants,
                    imports,
                    exports,
                    top_level_symbols,
                    code_blocks,
                    err_msg,
                ) = GenericCStyleExtractor.extract(
                    norm_path, content_str, lines, size_bytes, content_checksum
                )

            else:
                return StructuredCodeFile(
                    file_path=norm_path,
                    language=detected_lang,
                    parsing_status=CodeParsingStatus.FALLBACK_UNSUPPORTED,
                    content_checksum=content_checksum,
                    line_count=line_count,
                    size_bytes=size_bytes,
                    repository_id=repository_id,
                    revision=revision,
                    provenance=provenance,
                )

            return StructuredCodeFile(
                file_path=norm_path,
                language=detected_lang,
                parsing_status=status,
                content_checksum=content_checksum,
                line_count=line_count,
                size_bytes=size_bytes,
                functions=functions,
                classes=classes,
                constants=constants,
                imports=imports,
                exports=exports,
                top_level_symbols=top_level_symbols,
                code_blocks=code_blocks,
                repository_id=repository_id,
                revision=revision,
                provenance=provenance,
                error_message=err_msg,
            )

        except RepositoryCancelledError:
            raise
        except Exception as unhandled_err:
            logger.warning(f"Unexpected error extracting structure from '{norm_path}': {unhandled_err}")
            return StructuredCodeFile(
                file_path=norm_path,
                language=detected_lang,
                parsing_status=CodeParsingStatus.PARTIAL,
                content_checksum=content_checksum,
                line_count=line_count,
                size_bytes=size_bytes,
                repository_id=repository_id,
                revision=revision,
                provenance=provenance,
                error_message=sanitize_error(unhandled_err),
            )
