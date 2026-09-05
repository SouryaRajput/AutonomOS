"""
Project Source and Contract Extractor (Phase 1 / Part 8 / Step 5).

Provides lightweight, deterministic structural extraction from selected project source files:
- Classes, functions, methods, constants, imports, exports, and approximate line ranges
- Contract classification (interface, protocol, typed_model, lifecycle_enum, config_schema, event_contract, registry_definition)
- Graceful fallback for unsupported languages (CodeParsingStatus.FALLBACK_UNSUPPORTED, is_supported=False)
- Resilient malformed syntax handling (CodeParsingStatus.MALFORMED_SYNTAX / PARTIAL)
- Prompt injection isolation (isolated text snippets, zero eval/exec)
- Resource bounds (max_parse_bytes, max_symbols_per_file, cancellation checks)

Reuses established CodeStructureExtractor infrastructure from core.research.repo.structure.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import logging
import re
from typing import Any, Callable, Optional, Sequence

from core.research.contracts.evidence import EvidenceProvenance
from core.research.errors import (
    ProjectCancelledError,
    ProjectResourceLimitError,
    ProjectValidationError,
)
from core.research.project.models import (
    LineRange,
    ProjectContext,
    ProjectContract,
    ProjectContractType,
    ProjectSourceMaterial,
    ProjectSymbol,
    compute_sha256,
    detect_file_language,
    is_known_binary_extension,
    normalize_project_path,
    utc_now,
)
from core.research.repo.structure import (
    CodeBlock,
    CodeClass,
    CodeConstant,
    CodeExport,
    CodeFunction,
    CodeImport,
    CodeParsingStatus,
    CodeStructureExtractor,
    StructuredCodeFile,
    SymbolKind,
)

logger = logging.getLogger("AutonomOS.Research.ProjectExtractor")


# -----------------------------------------------------------------------------
# Options & Extraction Models
# -----------------------------------------------------------------------------

@dataclass
class ProjectExtractionOptions:
    """Options and resource bounds for project code structure extraction."""
    max_parse_bytes: int = 500_000
    max_symbols_per_file: int = 200
    extract_contracts: bool = True
    extract_imports: bool = True
    extract_exports: bool = True
    extract_code_blocks: bool = True
    is_cancelled: Optional[Callable[[], bool]] = None


@dataclass
class ExtractedProjectFile:
    """
    Result of deterministic structural extraction on a single project source file.
    """
    file_path: str
    language: Optional[str] = None
    parsing_status: CodeParsingStatus = CodeParsingStatus.PARSED
    is_supported: bool = True
    symbols: list[ProjectSymbol] = field(default_factory=list)
    contracts: list[ProjectContract] = field(default_factory=list)
    imports: list[CodeImport] = field(default_factory=list)
    exports: list[CodeExport] = field(default_factory=list)
    code_blocks: list[CodeBlock] = field(default_factory=list)
    line_count: int = 0
    size_bytes: int = 0
    content_hash: str = ""
    error_message: Optional[str] = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "file_path": self.file_path,
            "language": self.language,
            "parsing_status": self.parsing_status.value if isinstance(self.parsing_status, CodeParsingStatus) else str(self.parsing_status),
            "is_supported": self.is_supported,
            "symbols": [s.to_dict() for s in self.symbols],
            "contracts": [c.to_dict() for c in self.contracts],
            "imports": [i.to_dict() for i in self.imports],
            "exports": [e.to_dict() for e in self.exports],
            "code_blocks": [b.to_dict() for b in self.code_blocks],
            "line_count": self.line_count,
            "size_bytes": self.size_bytes,
            "content_hash": self.content_hash,
            "error_message": self.error_message,
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ExtractedProjectFile:
        raw_status = data.get("parsing_status", CodeParsingStatus.PARSED.value)
        try:
            status = CodeParsingStatus(raw_status)
        except ValueError:
            status = CodeParsingStatus.PARSED

        return cls(
            file_path=data.get("file_path", ""),
            language=data.get("language"),
            parsing_status=status,
            is_supported=bool(data.get("is_supported", True)),
            symbols=[ProjectSymbol.from_dict(s) for s in data.get("symbols", [])],
            contracts=[ProjectContract.from_dict(c) for c in data.get("contracts", [])],
            imports=[CodeImport.from_dict(i) for i in data.get("imports", [])],
            exports=[CodeExport.from_dict(e) for e in data.get("exports", [])],
            code_blocks=[CodeBlock.from_dict(b) for b in data.get("code_blocks", [])],
            line_count=int(data.get("line_count", 0)),
            size_bytes=int(data.get("size_bytes", 0)),
            content_hash=data.get("content_hash", ""),
            error_message=data.get("error_message"),
            metadata=dict(data.get("metadata", {})),
        )


# -----------------------------------------------------------------------------
# Contract Classification
# -----------------------------------------------------------------------------

class ProjectContractClassifier:
    """
    Deterministic structural classifier for explicit code contracts:
    - Protocol definitions (typing.Protocol, Protocol bases)
    - Interface definitions (ABC, abstract methods, TypeScript/Go/Java interfaces)
    - Typed models (BaseModel, dataclass, TypedDict, structs)
    - Lifecycle enums (Enum, IntEnum, StrEnum, Status/State enums)
    - Configuration schemas (Settings, Config, Options schemas)
    - Event contracts (Event, Message, Command, Payload contracts)
    - Registry definitions (Registry, Catalog, Factory classes)
    """

    _INTERFACE_NAME_RE = re.compile(r"^I[A-Z][a-zA-Z0-9_]*$")
    _REGISTRY_RE = re.compile(r"(Registry|Catalog|Factory|ProviderRegistry)$", re.IGNORECASE)
    _EVENT_RE = re.compile(r"(Event|Message|Notification|Command|Payload)$", re.IGNORECASE)
    _CONFIG_RE = re.compile(r"(Config|Configuration|Settings|Options|Spec)$", re.IGNORECASE)
    _MODEL_RE = re.compile(r"(Model|Schema|Dto|Entity|Record)$", re.IGNORECASE)
    _LIFECYCLE_RE = re.compile(r"(State|Status|Phase|Mode|Lifecycle|Stage|Kind|Type)$", re.IGNORECASE)

    @classmethod
    def classify_class(
        cls,
        code_class: CodeClass,
        file_path: str,
        lines: list[str],
        provenance: Optional[EvidenceProvenance] = None,
    ) -> Optional[ProjectContract]:
        """
        Classify a CodeClass and return a ProjectContract if it represents an explicit contract.
        """
        contract_type = cls.determine_contract_type(code_class)
        if contract_type is None:
            return None

        name = code_class.name
        norm_path = normalize_project_path(file_path)
        contract_id = f"contract-{norm_path.replace('/', '_')}-{name}"

        # Extract members
        members = [m.name for m in code_class.methods]

        # Extract snippet bounded
        content_snippet = ""
        if code_class.line_range and lines:
            start = max(1, code_class.line_range.start_line)
            end = min(len(lines), code_class.line_range.end_line)
            if start <= end:
                raw_snippet = "\n".join(lines[start - 1:end])
                content_snippet = raw_snippet[:2000]

        signature = f"class {name}"
        if code_class.bases:
            signature += f"({', '.join(code_class.bases)})"

        # Content hash from snippet or signature
        snippet_seed = content_snippet or signature
        content_hash = compute_sha256(snippet_seed)

        return ProjectContract(
            contract_id=contract_id,
            name=name,
            contract_type=contract_type,
            file_path=norm_path,
            line_range=code_class.line_range,
            members=members,
            bases=list(code_class.bases),
            signature=signature,
            docstring=code_class.docstring,
            content_snippet=content_snippet,
            content_hash=content_hash,
            provenance=provenance,
            metadata={
                "kind": code_class.kind.value if hasattr(code_class.kind, "value") else str(code_class.kind),
                "decorators": list(code_class.decorators),
                "method_count": len(code_class.methods),
            },
        )

    @classmethod
    def determine_contract_type(cls, code_class: CodeClass) -> Optional[ProjectContractType]:
        """Determine explicit contract type from class attributes."""
        bases_lower = [b.lower() for b in code_class.bases]
        name = code_class.name
        decorators_lower = [d.lower() for d in code_class.decorators]

        # 1. Protocol
        if any("protocol" in b for b in bases_lower) or any("runtime_checkable" in d for d in decorators_lower):
            return ProjectContractType.PROTOCOL

        # 2. Interface
        if (
            code_class.kind == SymbolKind.INTERFACE
            or any(b in ("abc", "abstract", "interface") for b in bases_lower)
            or cls._INTERFACE_NAME_RE.match(name)
            or any(any("abstractmethod" in d.lower() for d in m.decorators) for m in code_class.methods)
        ):
            return ProjectContractType.INTERFACE

        # 3. Lifecycle Enum
        if (
            code_class.kind == SymbolKind.ENUM
            or any(b in ("enum", "intenum", "strenum") for b in bases_lower)
            or (cls._LIFECYCLE_RE.search(name) and any("enum" in b for b in bases_lower))
        ):
            return ProjectContractType.LIFECYCLE_ENUM

        # 4. Registry Definition
        if cls._REGISTRY_RE.search(name):
            return ProjectContractType.REGISTRY_DEFINITION

        # 5. Event Contract
        if cls._EVENT_RE.search(name):
            return ProjectContractType.EVENT_CONTRACT

        # 6. Configuration Schema
        if (
            cls._CONFIG_RE.search(name)
            or any(b in ("basesettings", "config", "configuration") for b in bases_lower)
        ):
            return ProjectContractType.CONFIG_SCHEMA

        # 7. Typed Model
        if (
            code_class.kind == SymbolKind.STRUCT
            or any(b in ("basemodel", "typeddict", "namedtuple", "schema") for b in bases_lower)
            or any("dataclass" in d for d in decorators_lower)
            or cls._MODEL_RE.search(name)
        ):
            return ProjectContractType.TYPED_MODEL

        return None


# -----------------------------------------------------------------------------
# Main Extractor: ProjectSourceExtractor
# -----------------------------------------------------------------------------

class ProjectSourceExtractor:
    """
    Deterministic structural extractor for project workspace source files.
    Dispatches to language parsers via CodeStructureExtractor, classifies explicit contracts,
    isolates docstrings, and strictly respects cancellation and parse byte bounds.
    """

    @classmethod
    def extract_file(
        cls,
        file_path: str,
        content: str,
        language: Optional[str] = None,
        provenance: Optional[EvidenceProvenance] = None,
        options: Optional[ProjectExtractionOptions] = None,
    ) -> ExtractedProjectFile:
        """
        Extract code structure, symbols, and explicit contracts from a single source file.
        """
        opts = options or ProjectExtractionOptions()

        # Cancellation check
        if opts.is_cancelled and opts.is_cancelled():
            raise ProjectCancelledError(
                operation="extract_source_structure",
                target=file_path,
                message="Source extraction cancelled before processing file.",
            )

        norm_path = normalize_project_path(file_path)
        content_str = content or ""
        raw_bytes = content_str.encode("utf-8")
        size_bytes = len(raw_bytes)
        content_hash = compute_sha256(raw_bytes)
        lines = content_str.splitlines()
        line_count = len(lines)
        detected_lang = language or detect_file_language(norm_path)

        # 1. Check binary or empty or null bytes
        if is_known_binary_extension(norm_path) or "\x00" in content_str:
            return ExtractedProjectFile(
                file_path=norm_path,
                language=detected_lang,
                parsing_status=CodeParsingStatus.FALLBACK_UNSUPPORTED,
                is_supported=False,
                line_count=line_count,
                size_bytes=size_bytes,
                content_hash=content_hash,
                error_message="Binary file or null bytes detected; structural extraction skipped.",
            )

        # 2. Check byte budget limit
        if size_bytes > opts.max_parse_bytes:
            logger.info(f"File '{norm_path}' ({size_bytes}B) exceeds max_parse_bytes ({opts.max_parse_bytes}B).")
            return ExtractedProjectFile(
                file_path=norm_path,
                language=detected_lang,
                parsing_status=CodeParsingStatus.TRUNCATED,
                is_supported=True,
                line_count=line_count,
                size_bytes=size_bytes,
                content_hash=content_hash,
                error_message=f"File size {size_bytes} bytes exceeds maximum parse limit {opts.max_parse_bytes} bytes.",
            )

        # 3. Call repo CodeStructureExtractor
        repo_canceller = opts.is_cancelled
        try:
            structured = CodeStructureExtractor.extract_structure(
                file_path=norm_path,
                content=content_str,
                language=detected_lang,
                max_parse_bytes=opts.max_parse_bytes,
                provenance=provenance,
                is_cancelled=repo_canceller,
            )
        except Exception as e:
            if opts.is_cancelled and opts.is_cancelled():
                raise ProjectCancelledError(
                    operation="extract_source_structure",
                    target=norm_path,
                    message="Source extraction cancelled.",
                )
            logger.warning(f"Unexpected parsing failure for '{norm_path}': {e}")
            return ExtractedProjectFile(
                file_path=norm_path,
                language=detected_lang,
                parsing_status=CodeParsingStatus.MALFORMED_SYNTAX,
                is_supported=True,
                line_count=line_count,
                size_bytes=size_bytes,
                content_hash=content_hash,
                error_message=f"Syntax parsing error: {str(e)}",
            )

        # 4. Handle unsupported language fallback
        is_supported = structured.parsing_status != CodeParsingStatus.FALLBACK_UNSUPPORTED
        if not is_supported:
            return ExtractedProjectFile(
                file_path=norm_path,
                language=detected_lang,
                parsing_status=CodeParsingStatus.FALLBACK_UNSUPPORTED,
                is_supported=False,
                line_count=line_count,
                size_bytes=size_bytes,
                content_hash=content_hash,
                error_message=f"Language '{detected_lang}' not supported for AST extraction; falling back to source text.",
            )

        # 5. Build ProjectSymbol items
        symbols: list[ProjectSymbol] = []
        for c in structured.classes:
            symbols.append(
                ProjectSymbol(
                    name=c.name,
                    symbol_type=c.kind,
                    file_path=norm_path,
                    line_range=c.line_range,
                    structural_relationships=[f"base:{b}" for b in c.bases],
                    signature=f"class {c.name}({', '.join(c.bases)})" if c.bases else f"class {c.name}",
                    docstring=c.docstring,
                    visibility="public" if not c.name.startswith("_") else "private",
                    metadata={"kind": c.kind.value if hasattr(c.kind, "value") else str(c.kind)},
                )
            )
            for m in c.methods:
                symbols.append(
                    ProjectSymbol(
                        name=m.name,
                        symbol_type=SymbolKind.METHOD,
                        file_path=norm_path,
                        line_range=m.line_range,
                        parent_symbol=c.name,
                        signature=m.signature,
                        docstring=m.docstring,
                        visibility="public" if not m.name.startswith("_") else "private",
                        metadata={"is_async": m.is_async, "parent_class": c.name},
                    )
                )

        for f in structured.functions:
            symbols.append(
                ProjectSymbol(
                    name=f.name,
                    symbol_type=SymbolKind.ASYNC_FUNCTION if f.is_async else SymbolKind.FUNCTION,
                    file_path=norm_path,
                    line_range=f.line_range,
                    signature=f.signature,
                    docstring=f.docstring,
                    visibility="public" if not f.name.startswith("_") else "private",
                    metadata={"is_async": f.is_async},
                )
            )

        for const in structured.constants:
            symbols.append(
                ProjectSymbol(
                    name=const.name,
                    symbol_type=SymbolKind.CONSTANT,
                    file_path=norm_path,
                    line_range=const.line_range,
                    signature=const.type_annotation or const.value_preview,
                    visibility="public" if not const.name.startswith("_") else "private",
                    metadata={"value_preview": const.value_preview},
                )
            )

        # Enforce max_symbols_per_file budget
        if len(symbols) > opts.max_symbols_per_file:
            symbols = symbols[:opts.max_symbols_per_file]

        # 6. Extract explicit contracts
        contracts: list[ProjectContract] = []
        if opts.extract_contracts:
            for c in structured.classes:
                contract = ProjectContractClassifier.classify_class(
                    code_class=c,
                    file_path=norm_path,
                    lines=lines,
                    provenance=provenance,
                )
                if contract is not None:
                    contracts.append(contract)

        # 7. Collect structural artifacts according to options
        imports = structured.imports if opts.extract_imports else []
        exports = structured.exports if opts.extract_exports else []
        code_blocks = structured.code_blocks if opts.extract_code_blocks else []

        return ExtractedProjectFile(
            file_path=norm_path,
            language=detected_lang,
            parsing_status=structured.parsing_status,
            is_supported=True,
            symbols=symbols,
            contracts=contracts,
            imports=imports,
            exports=exports,
            code_blocks=code_blocks,
            line_count=line_count,
            size_bytes=size_bytes,
            content_hash=content_hash,
            error_message=structured.error_message,
        )

    @classmethod
    def extract_materials(
        cls,
        materials: list[ProjectSourceMaterial],
        options: Optional[ProjectExtractionOptions] = None,
    ) -> list[ExtractedProjectFile]:
        """
        Extract code structure from a list of ProjectSourceMaterial items.
        """
        results: list[ExtractedProjectFile] = []
        opts = options or ProjectExtractionOptions()

        for mat in materials:
            if opts.is_cancelled and opts.is_cancelled():
                raise ProjectCancelledError(
                    operation="extract_materials",
                    target=mat.file_path,
                    message="Extraction cancelled during batch processing.",
                )
            extracted = cls.extract_file(
                file_path=mat.file_path,
                content=mat.content,
                language=mat.language,
                provenance=mat.provenance,
                options=opts,
            )
            results.append(extracted)

        return results

    @classmethod
    def populate_context(
        cls,
        project_context: ProjectContext,
        extracted_files: Optional[list[ExtractedProjectFile]] = None,
        options: Optional[ProjectExtractionOptions] = None,
    ) -> None:
        """
        Populate extracted symbols and contracts into a ProjectContext container.
        If extracted_files is not provided, extracts them from project_context.source_materials.
        """
        if extracted_files is None:
            extracted_files = cls.extract_materials(project_context.source_materials, options=options)
        for ef in extracted_files:
            for s in ef.symbols:
                project_context.add_symbol(s)
            for c in ef.contracts:
                project_context.add_contract(c)
