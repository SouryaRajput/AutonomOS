"""
Structured Data Source and Structural Domain Models (Phase 1 / Part 7 / Step 1).

Establishes provider-neutral, strongly typed domain representations for:
- Source Location & exact path addressing (e.g. data.items[3].name, root, array indices)
- Structured Data Source (provider, endpoint URL, protocol/type, content type, provenance)
- Structured Data Request (endpoint, method, params, headers, requested fields, filters, pagination, resource limits)
- Structured Data Response (HTTP/status metadata, content type, encoding, response bytes, payload hash, schema, pagination, provenance)
- Structured Record (record ID, value, source location, content hash, provenance)
- Structured Schema & Fields (structure type, field paths, basic types, nullability, optionality)

Architectural Invariants:
- Zero external package dependencies (stdlib only: json, dataclasses, enum, re, hashlib).
- Pure structural representation: strictly NO semantic guessing or inferences from field names.
- Deterministic hashing via canonical JSON serialization.
- Preservation of raw vs normalized distinctions.
- Compatibility with existing AutonomOS provenance and CrawlerReport contracts.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
import hashlib
import json
import re
from typing import Any, Optional, Sequence, Union
import urllib.parse
import uuid

from core.research.contracts.crawler_report import RawSourceReference
from core.research.contracts.evidence import EvidenceProvenance
from core.research.errors import (
    StructuredDataLimitError,
    StructuredDataLocationError,
    StructuredDataValidationError,
)
from core.research.structured.auth import CredentialReference
from core.research.types import SourceType


def utc_now() -> str:
    """Generate ISO 8601 UTC timestamp."""
    return datetime.now(timezone.utc).isoformat()


def compute_sha256(content: str | bytes) -> str:
    """Compute SHA-256 hex digest for cryptographic integrity."""
    raw = content.encode("utf-8") if isinstance(content, str) else content
    return hashlib.sha256(raw).hexdigest()


def canonical_json_dumps(obj: Any) -> str:
    """
    Produce deterministic, canonical JSON string representation.
    Ensures sorted keys, consistent compact separators, and UTF-8 encoding stability.
    """
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=True, default=str)


def compute_structured_hash(value: Any) -> str:
    """
    Deterministically hash any Python data structure (dict, list, primitive, bytes).
    """
    if isinstance(value, bytes):
        return compute_sha256(value)
    if isinstance(value, (dict, list)):
        return compute_sha256(canonical_json_dumps(value))
    return compute_sha256(str(value))


# -----------------------------------------------------------------------------
# Enums & Classifications
# -----------------------------------------------------------------------------

class StructuredSourceType(str, Enum):
    """Protocol and structural classification of the data endpoint."""
    REST_API = "rest_api"
    GRAPHQL = "graphql"
    JSON_ENDPOINT = "json_endpoint"
    JSONL_FEED = "jsonl_feed"
    XML_FEED = "xml_feed"
    CSV_ENDPOINT = "csv_endpoint"
    DATASET = "dataset"
    GENERIC = "generic"

    @classmethod
    def from_string(cls, val: str | StructuredSourceType) -> StructuredSourceType:
        if isinstance(val, cls):
            return val
        if not val or not isinstance(val, str):
            return cls.GENERIC
        normalized = val.strip().lower().replace("-", "_").replace(" ", "_")
        for member in cls:
            if member.value == normalized:
                return member
        return cls.GENERIC


class StructuredContentType(str, Enum):
    """Normalized structured content MIME classification."""
    JSON = "json"
    JSONL = "jsonl"
    XML = "xml"
    CSV = "csv"
    YAML = "yaml"
    BINARY = "binary"
    UNKNOWN = "unknown"

    @classmethod
    def from_mime_type(cls, mime: Optional[str]) -> StructuredContentType:
        if not mime or not isinstance(mime, str):
            return cls.UNKNOWN
        clean = mime.split(";")[0].strip().lower()
        if clean in ("application/json", "text/json", "application/vnd.api+json"):
            return cls.JSON
        if clean in ("application/x-ndjson", "application/jsonl", "application/x-jsonlines"):
            return cls.JSONL
        if clean in ("application/xml", "text/xml", "application/atom+xml", "application/rss+xml"):
            return cls.XML
        if clean in ("text/csv", "application/csv"):
            return cls.CSV
        if clean in ("application/x-yaml", "text/yaml", "text/x-yaml"):
            return cls.YAML
        if clean in ("application/octet-stream", "application/zip", "application/gzip"):
            return cls.BINARY
        # Substring heuristics
        if "json" in clean:
            return cls.JSON
        if "xml" in clean:
            return cls.XML
        if "csv" in clean:
            return cls.CSV
        return cls.UNKNOWN


class HttpMethod(str, Enum):
    """HTTP methods applicable for REST and API endpoints."""
    GET = "GET"
    POST = "POST"
    PUT = "PUT"
    PATCH = "PATCH"
    DELETE = "DELETE"
    HEAD = "HEAD"
    OPTIONS = "OPTIONS"

    @classmethod
    def from_string(cls, val: str | HttpMethod) -> HttpMethod:
        if isinstance(val, cls):
            return val
        if not val or not isinstance(val, str):
            return cls.GET
        try:
            return cls(val.strip().upper())
        except ValueError:
            return cls.GET


class PaginationType(str, Enum):
    """Pagination mechanism supported by structured data endpoints."""
    NONE = "none"
    PAGE_NUMBER = "page_number"
    OFFSET_LIMIT = "offset_limit"
    CURSOR = "cursor"
    LINK_HEADER = "link_header"
    NEXT_URL = "next_url"


class StructuredDataType(str, Enum):
    """Basic data types for fields in a structured payload."""
    STRING = "string"
    INTEGER = "integer"
    FLOAT = "float"
    BOOLEAN = "boolean"
    ARRAY = "array"
    OBJECT = "object"
    NULL = "null"
    DATETIME = "datetime"
    BYTES = "bytes"
    ANY = "any"


class SchemaStructureType(str, Enum):
    """Root structural shape of a structured schema."""
    OBJECT = "object"
    ARRAY = "array"
    PRIMITIVE = "primitive"
    UNION = "union"
    EMPTY = "empty"


# -----------------------------------------------------------------------------
# Source Location & Exact Path Addressing
# -----------------------------------------------------------------------------

class PathSegment:
    """Base class for parsed location path segments."""
    pass


@dataclass(frozen=True)
class FieldSegment(PathSegment):
    """Represents object key / field lookup."""
    name: str

    def __str__(self) -> str:
        return self.name


@dataclass(frozen=True)
class IndexSegment(PathSegment):
    """Represents list / array index lookup."""
    index: int

    def __str__(self) -> str:
        return f"[{self.index}]"


@dataclass(frozen=True)
class WildcardSegment(PathSegment):
    """Represents array iteration / wildcard path."""
    def __str__(self) -> str:
        return "[]"


@dataclass(frozen=True)
class SourceLocation:
    """
    Exact address of a structured value or record within a response tree.
    Preserves exact syntactic paths such as 'data.items[3].name' or 'root'.
    Provides deterministic traversal and resolution without semantic alteration.
    """
    path: str
    segments: tuple[PathSegment, ...] = field(default_factory=tuple)

    @classmethod
    def parse(cls, raw_path: Union[str, SourceLocation]) -> SourceLocation:
        """
        Parse a dot/bracket notation path string into structured PathSegments.
        Examples:
            'root' -> ()
            'items[0].name' -> (FieldSegment('items'), IndexSegment(0), FieldSegment('name'))
            'data.items[3].name' -> (FieldSegment('data'), FieldSegment('items'), IndexSegment(3), FieldSegment('name'))
            '[2]' -> (IndexSegment(2),)
            'items[].id' -> (FieldSegment('items'), WildcardSegment(), FieldSegment('id'))
        """
        if isinstance(raw_path, SourceLocation):
            return raw_path
        if not isinstance(raw_path, str):
            raise StructuredDataLocationError(str(raw_path), "Source path must be a string or SourceLocation.")

        s = raw_path.strip()
        if not s or s == "root" or s == "$":
            return cls(path="root", segments=())

        # Regex tokenizer for dot access, bracket index, and bracket field access
        # Matches: .name | ["name"] | ['name'] | [123] | []
        token_re = re.compile(
            r"""
            (?:\.([a-zA-Z0-9_\-]+))           # 1: dot field: .foo
            | (?:\[['"]([^'"]+)['"]\])        # 2: quoted bracket field: ["foo"]
            | (?:\[(\d+)\])                   # 3: array index: [0]
            | (\[\])                          # 4: array wildcard: []
            | ([a-zA-Z0-9_\-]+)               # 5: initial bare field: foo
            """,
            re.VERBOSE,
        )

        segments: list[PathSegment] = []
        pos = 0
        n = len(s)

        while pos < n:
            m = token_re.match(s, pos)
            if not m:
                # Malformed token at position
                raise StructuredDataLocationError(
                    raw_path,
                    f"Invalid path syntax at position {pos} in '{raw_path}'",
                )
            dot_field, bracket_field, index_str, wildcard, bare_field = m.groups()

            if dot_field is not None:
                segments.append(FieldSegment(dot_field))
            elif bracket_field is not None:
                segments.append(FieldSegment(bracket_field))
            elif index_str is not None:
                segments.append(IndexSegment(int(index_str)))
            elif wildcard is not None:
                segments.append(WildcardSegment())
            elif bare_field is not None:
                segments.append(FieldSegment(bare_field))

            pos = m.end()

        canonical_path = cls._format_segments(segments)
        return cls(path=canonical_path, segments=tuple(segments))

    @staticmethod
    def _format_segments(segments: Sequence[PathSegment]) -> str:
        """Format segment sequence into canonical string representation."""
        if not segments:
            return "root"
        parts: list[str] = []
        for i, seg in enumerate(segments):
            if isinstance(seg, IndexSegment):
                parts.append(f"[{seg.index}]")
            elif isinstance(seg, WildcardSegment):
                parts.append("[]")
            elif isinstance(seg, FieldSegment):
                if i > 0:
                    parts.append(f".{seg.name}")
                else:
                    parts.append(seg.name)
        return "".join(parts)

    def child_field(self, field_name: str) -> SourceLocation:
        """Create a child location pointing to a named field."""
        if not field_name or not isinstance(field_name, str):
            raise StructuredDataLocationError(self.path, "Child field name cannot be empty.")
        new_segments = self.segments + (FieldSegment(field_name),)
        return SourceLocation(path=self._format_segments(new_segments), segments=new_segments)

    def child_index(self, index: int) -> SourceLocation:
        """Create a child location pointing to an array index."""
        if not isinstance(index, int) or index < 0:
            raise StructuredDataLocationError(self.path, f"Child index must be non-negative integer, got {index}.")
        new_segments = self.segments + (IndexSegment(index),)
        return SourceLocation(path=self._format_segments(new_segments), segments=new_segments)

    def resolve(self, payload: Any) -> Any:
        """
        Traverse the payload according to this location's segments.
        Raises StructuredDataLocationError if a key is missing or index out of bounds.
        """
        curr = payload
        traversed: list[PathSegment] = []

        for seg in self.segments:
            traversed.append(seg)
            curr_path = self._format_segments(traversed)

            if isinstance(seg, FieldSegment):
                if not isinstance(curr, dict):
                    raise StructuredDataLocationError(
                        self.path,
                        f"Cannot access field '{seg.name}' at '{curr_path}': container is {type(curr).__name__}, expected dict",
                    )
                if seg.name not in curr:
                    raise StructuredDataLocationError(
                        self.path,
                        f"Field '{seg.name}' not found at '{curr_path}'",
                    )
                curr = curr[seg.name]

            elif isinstance(seg, IndexSegment):
                if not isinstance(curr, (list, tuple)):
                    raise StructuredDataLocationError(
                        self.path,
                        f"Cannot access index [{seg.index}] at '{curr_path}': container is {type(curr).__name__}, expected list",
                    )
                if seg.index < 0 or seg.index >= len(curr):
                    raise StructuredDataLocationError(
                        self.path,
                        f"Array index [{seg.index}] out of range (length {len(curr)}) at '{curr_path}'",
                    )
                curr = curr[seg.index]

            elif isinstance(seg, WildcardSegment):
                raise StructuredDataLocationError(
                    self.path,
                    f"Wildcard segment '[]' cannot be resolved to a single value at '{curr_path}'",
                )

        return curr

    def exists(self, payload: Any) -> bool:
        """Check whether this location exists within the payload without raising."""
        try:
            self.resolve(payload)
            return True
        except StructuredDataLocationError:
            return False

    def __str__(self) -> str:
        return self.path

    def to_dict(self) -> dict[str, Any]:
        return {"path": self.path}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SourceLocation:
        return cls.parse(data.get("path", "root"))


# -----------------------------------------------------------------------------
# Request & Resource Configuration
# -----------------------------------------------------------------------------

@dataclass(frozen=True)
class StructuredDataLimits:
    """Explicit resource and safety envelope bounds for structured data retrieval."""
    max_bytes: int = 10_000_000      # 10 MB default max response size
    max_records: int = 10_000        # Max structured records to extract per request
    max_depth: int = 20              # Max nesting depth for objects/arrays
    timeout_seconds: float = 30.0    # Default per-operation network timeout
    max_fields: int = 50_000         # Max total fields/keys across document
    max_field_size: int = 1_000_000  # 1 MB max size for any individual field string

    def __post_init__(self) -> None:
        if self.max_bytes <= 0:
            raise StructuredDataLimitError("max_bytes", self.max_bytes, 1)
        if self.max_records <= 0:
            raise StructuredDataLimitError("max_records", self.max_records, 1)
        if self.max_depth <= 0:
            raise StructuredDataLimitError("max_depth", self.max_depth, 1)
        if self.timeout_seconds <= 0:
            raise StructuredDataLimitError("timeout_seconds", self.timeout_seconds, 0.1)
        if self.max_fields <= 0:
            raise StructuredDataLimitError("max_fields", self.max_fields, 1)
        if self.max_field_size <= 0:
            raise StructuredDataLimitError("max_field_size", self.max_field_size, 1)

    def to_dict(self) -> dict[str, Any]:
        return {
            "max_bytes": self.max_bytes,
            "max_records": self.max_records,
            "max_depth": self.max_depth,
            "timeout_seconds": self.timeout_seconds,
            "max_fields": self.max_fields,
            "max_field_size": self.max_field_size,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> StructuredDataLimits:
        return cls(
            max_bytes=int(data.get("max_bytes", 10_000_000)),
            max_records=int(data.get("max_records", 10_000)),
            max_depth=int(data.get("max_depth", 20)),
            timeout_seconds=float(data.get("timeout_seconds", 30.0)),
            max_fields=int(data.get("max_fields", 50_000)),
            max_field_size=int(data.get("max_field_size", 1_000_000)),
        )


@dataclass(frozen=True)
class PaginationConfig:
    """Configuration for paginated endpoint navigation."""
    pagination_type: PaginationType = PaginationType.NONE
    page: Optional[int] = None
    page_size: Optional[int] = None
    offset: Optional[int] = None
    limit: Optional[int] = None
    cursor: Optional[str] = None
    max_pages: int = 1
    page_param_name: str = "page"
    page_size_param_name: str = "per_page"
    cursor_param_name: str = "cursor"
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.max_pages < 1:
            raise StructuredDataValidationError("max_pages", "max_pages must be >= 1.")
        if self.page is not None and self.page < 0:
            raise StructuredDataValidationError("page", "page must be non-negative.")
        if self.page_size is not None and self.page_size <= 0:
            raise StructuredDataValidationError("page_size", "page_size must be > 0.")
        if self.offset is not None and self.offset < 0:
            raise StructuredDataValidationError("offset", "offset must be non-negative.")
        if self.limit is not None and self.limit <= 0:
            raise StructuredDataValidationError("limit", "limit must be > 0.")

    def to_dict(self) -> dict[str, Any]:
        return {
            "pagination_type": self.pagination_type.value if isinstance(self.pagination_type, PaginationType) else str(self.pagination_type),
            "page": self.page,
            "page_size": self.page_size,
            "offset": self.offset,
            "limit": self.limit,
            "cursor": self.cursor,
            "max_pages": self.max_pages,
            "page_param_name": self.page_param_name,
            "page_size_param_name": self.page_size_param_name,
            "cursor_param_name": self.cursor_param_name,
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> PaginationConfig:
        pt_raw = data.get("pagination_type", PaginationType.NONE.value)
        try:
            pt = PaginationType(pt_raw)
        except (ValueError, TypeError):
            pt = PaginationType.NONE

        return cls(
            pagination_type=pt,
            page=data.get("page"),
            page_size=data.get("page_size"),
            offset=data.get("offset"),
            limit=data.get("limit"),
            cursor=data.get("cursor"),
            max_pages=int(data.get("max_pages", 1)),
            page_param_name=data.get("page_param_name", "page"),
            page_size_param_name=data.get("page_size_param_name", "per_page"),
            cursor_param_name=data.get("cursor_param_name", "cursor"),
            metadata=dict(data.get("metadata", {})),
        )


@dataclass(frozen=True)
class PaginationMetadata:
    """Pagination state extracted from response headers or payload."""
    has_more: bool = False
    next_page: Optional[int] = None
    next_cursor: Optional[str] = None
    next_url: Optional[str] = None
    total_records: Optional[int] = None
    total_pages: Optional[int] = None
    current_page: Optional[int] = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "has_more": self.has_more,
            "next_page": self.next_page,
            "next_cursor": self.next_cursor,
            "next_url": self.next_url,
            "total_records": self.total_records,
            "total_pages": self.total_pages,
            "current_page": self.current_page,
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> PaginationMetadata:
        return cls(
            has_more=bool(data.get("has_more", False)),
            next_page=data.get("next_page"),
            next_cursor=data.get("next_cursor"),
            next_url=data.get("next_url"),
            total_records=data.get("total_records"),
            total_pages=data.get("total_pages"),
            current_page=data.get("current_page"),
            metadata=dict(data.get("metadata", {})),
        )


# -----------------------------------------------------------------------------
# Schema Representation
# -----------------------------------------------------------------------------

@dataclass
class SchemaField:
    """Individual field descriptor in a structured schema."""
    path: str
    data_type: StructuredDataType
    nullable: bool = False
    optional: bool = False
    nested_schema: Optional[StructuredSchema] = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "path": self.path,
            "data_type": self.data_type.value if isinstance(self.data_type, StructuredDataType) else str(self.data_type),
            "nullable": self.nullable,
            "optional": self.optional,
            "metadata": dict(self.metadata),
        }
        if self.nested_schema is not None:
            d["nested_schema"] = self.nested_schema.to_dict()
        return d

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SchemaField:
        dt_raw = data.get("data_type", StructuredDataType.ANY.value)
        try:
            dt = StructuredDataType(dt_raw)
        except (ValueError, TypeError):
            dt = StructuredDataType.ANY

        nested = None
        if "nested_schema" in data and isinstance(data["nested_schema"], dict):
            nested = StructuredSchema.from_dict(data["nested_schema"])

        return cls(
            path=data.get("path", ""),
            data_type=dt,
            nullable=bool(data.get("nullable", False)),
            optional=bool(data.get("optional", False)),
            nested_schema=nested,
            metadata=dict(data.get("metadata", {})),
        )


@dataclass
class StructuredSchema:
    """
    Representation of structural layout, data types, and nesting hierarchy.
    Strictly describes data shape; does not guess semantic business meanings.
    """
    structure_type: SchemaStructureType
    fields: list[SchemaField] = field(default_factory=list)
    field_paths: list[str] = field(default_factory=list)
    inferred: bool = True
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.field_paths and self.fields:
            self.field_paths = [f.path for f in self.fields]

    def to_dict(self) -> dict[str, Any]:
        return {
            "structure_type": self.structure_type.value if isinstance(self.structure_type, SchemaStructureType) else str(self.structure_type),
            "fields": [f.to_dict() for f in self.fields],
            "field_paths": list(self.field_paths),
            "inferred": self.inferred,
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> StructuredSchema:
        st_raw = data.get("structure_type", SchemaStructureType.OBJECT.value)
        try:
            st = SchemaStructureType(st_raw)
        except (ValueError, TypeError):
            st = SchemaStructureType.OBJECT

        fields = [SchemaField.from_dict(f) for f in data.get("fields", []) if isinstance(f, dict)]
        return cls(
            structure_type=st,
            fields=fields,
            field_paths=list(data.get("field_paths", [f.path for f in fields])),
            inferred=bool(data.get("inferred", True)),
            metadata=dict(data.get("metadata", {})),
        )

    @classmethod
    def infer_from_value(cls, value: Any, max_depth: int = 15, base_path: str = "") -> StructuredSchema:
        """
        Deterministically infer basic structural schema from any Python data structure.
        Strictly observes types without semantic interpretation.
        """
        if value is None:
            return cls(
                structure_type=SchemaStructureType.EMPTY,
                fields=[SchemaField(path=base_path or "root", data_type=StructuredDataType.NULL, nullable=True)],
                field_paths=[base_path or "root"],
            )

        if isinstance(value, dict):
            fields: list[SchemaField] = []
            for k in sorted(value.keys()):
                v = value[k]
                p = f"{base_path}.{k}" if base_path else k
                f_type, nullable = cls._detect_data_type(v)
                nested = None
                if max_depth > 1 and isinstance(v, (dict, list)):
                    nested = cls.infer_from_value(v, max_depth=max_depth - 1, base_path=p)

                fields.append(
                    SchemaField(
                        path=p,
                        data_type=f_type,
                        nullable=nullable,
                        optional=False,
                        nested_schema=nested,
                    )
                )
            return cls(
                structure_type=SchemaStructureType.OBJECT,
                fields=fields,
                field_paths=[f.path for f in fields],
            )

        if isinstance(value, (list, tuple)):
            if not value:
                return cls(
                    structure_type=SchemaStructureType.ARRAY,
                    fields=[],
                    field_paths=[f"{base_path}[]" if base_path else "[]"],
                )
            # Sample elements up to 20 for schema inference
            sample_size = min(len(value), 20)
            seen_types: set[StructuredDataType] = set()
            nullable = False
            item_fields: dict[str, list[tuple[StructuredDataType, bool]]] = {}

            for elem in value[:sample_size]:
                dt, is_null = cls._detect_data_type(elem)
                if is_null:
                    nullable = True
                else:
                    seen_types.add(dt)
                if isinstance(elem, dict):
                    for ek, ev in elem.items():
                        edt, enull = cls._detect_data_type(ev)
                        item_fields.setdefault(ek, []).append((edt, enull))

            nested_fields: list[SchemaField] = []
            array_path = f"{base_path}[]" if base_path else "[]"
            for k in sorted(item_fields.keys()):
                types_for_k = item_fields[k]
                k_type = types_for_k[0][0] if len({t[0] for t in types_for_k}) == 1 else StructuredDataType.ANY
                k_null = any(t[1] for t in types_for_k)
                k_optional = len(types_for_k) < sample_size
                nested_fields.append(
                    SchemaField(
                        path=f"{array_path}.{k}",
                        data_type=k_type,
                        nullable=k_null,
                        optional=k_optional,
                    )
                )

            nested_schema = None
            if nested_fields:
                nested_schema = cls(
                    structure_type=SchemaStructureType.OBJECT,
                    fields=nested_fields,
                    field_paths=[f.path for f in nested_fields],
                )

            root_item_type = next(iter(seen_types)) if len(seen_types) == 1 else (StructuredDataType.ANY if seen_types else StructuredDataType.NULL)
            return cls(
                structure_type=SchemaStructureType.ARRAY,
                fields=[
                    SchemaField(
                        path=array_path,
                        data_type=root_item_type,
                        nullable=nullable,
                        nested_schema=nested_schema,
                    )
                ],
                field_paths=[array_path] + [f.path for f in nested_fields],
            )

        # Primitive value
        dt, is_null = cls._detect_data_type(value)
        p = base_path or "root"
        return cls(
            structure_type=SchemaStructureType.PRIMITIVE,
            fields=[SchemaField(path=p, data_type=dt, nullable=is_null)],
            field_paths=[p],
        )

    @staticmethod
    def _detect_data_type(val: Any) -> tuple[StructuredDataType, bool]:
        """Detect basic data type and nullability without semantic assumptions."""
        if val is None:
            return StructuredDataType.NULL, True
        if isinstance(val, bool):
            return StructuredDataType.BOOLEAN, False
        if isinstance(val, int):
            return StructuredDataType.INTEGER, False
        if isinstance(val, float):
            return StructuredDataType.FLOAT, False
        if isinstance(val, str):
            # Check ISO datetime string pattern
            if re.match(r"^\d{4}-\d{2}-\d{2}(?:T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:?\d{2})?)?$", val):
                return StructuredDataType.DATETIME, False
            return StructuredDataType.STRING, False
        if isinstance(val, (dict,)):
            return StructuredDataType.OBJECT, False
        if isinstance(val, (list, tuple, set)):
            return StructuredDataType.ARRAY, False
        if isinstance(val, (bytes, bytearray)):
            return StructuredDataType.BYTES, False
        return StructuredDataType.ANY, False


# -----------------------------------------------------------------------------
# Core Structured Data Domain Models
# -----------------------------------------------------------------------------

@dataclass
class StructuredDataSource:
    """
    Domain descriptor representing a structured data provider and endpoint.
    Maintains provenance, content type, and protocol invariants.
    """
    source_id: str
    provider: str
    endpoint_url: str
    source_type: StructuredSourceType = StructuredSourceType.GENERIC
    content_type: StructuredContentType = StructuredContentType.UNKNOWN
    retrieved_at: str = field(default_factory=utc_now)
    provenance: Optional[EvidenceProvenance] = None
    credential_ref: Optional[CredentialReference] = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.source_id or not isinstance(self.source_id, str) or not self.source_id.strip():
            raise StructuredDataValidationError("source_id", "source_id cannot be empty.")
        if not self.provider or not isinstance(self.provider, str) or not self.provider.strip():
            raise StructuredDataValidationError("provider", "provider identifier cannot be empty.")
        if not self.endpoint_url or not isinstance(self.endpoint_url, str) or not self.endpoint_url.strip():
            raise StructuredDataValidationError("endpoint_url", "endpoint_url cannot be empty.")

        self.source_id = self.source_id.strip()
        self.provider = self.provider.strip()
        self.endpoint_url = self.endpoint_url.strip()

        parsed = urllib.parse.urlparse(self.endpoint_url)
        if parsed.scheme not in ("http", "https", "file", "mock"):
            raise StructuredDataValidationError(
                "endpoint_url",
                f"Unsupported URL scheme '{parsed.scheme}'. Must be http, https, file, or mock.",
            )

        if not isinstance(self.source_type, StructuredSourceType):
            self.source_type = StructuredSourceType.from_string(self.source_type)
        if not isinstance(self.content_type, StructuredContentType):
            self.content_type = StructuredContentType.from_mime_type(str(self.content_type))

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_id": self.source_id,
            "provider": self.provider,
            "endpoint_url": self.endpoint_url,
            "source_type": self.source_type.value,
            "content_type": self.content_type.value,
            "retrieved_at": self.retrieved_at,
            "provenance": self.provenance.to_dict() if self.provenance else None,
            "credential_ref": self.credential_ref.to_dict() if self.credential_ref else None,
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> StructuredDataSource:
        st_raw = data.get("source_type", StructuredSourceType.GENERIC.value)
        ct_raw = data.get("content_type", StructuredContentType.UNKNOWN.value)
        prov_data = data.get("provenance")
        prov = EvidenceProvenance.from_dict(prov_data) if isinstance(prov_data, dict) else None
        c_data = data.get("credential_ref")
        cred_ref = CredentialReference.from_dict(c_data) if isinstance(c_data, dict) else None

        return cls(
            source_id=data.get("source_id", ""),
            provider=data.get("provider", ""),
            endpoint_url=data.get("endpoint_url", ""),
            source_type=StructuredSourceType.from_string(st_raw),
            content_type=StructuredContentType.from_mime_type(ct_raw) if "/" in ct_raw else StructuredContentType(ct_raw),
            retrieved_at=data.get("retrieved_at", utc_now()),
            provenance=prov,
            credential_ref=cred_ref,
            metadata=dict(data.get("metadata", {})),
        )


@dataclass
class StructuredDataRequest:
    """
    Declarative specification for structured data acquisition.
    Enforces bounded resource limits, explicit projections, and HTTP parameters.
    """
    request_id: str
    endpoint_url: str
    method: HttpMethod = HttpMethod.GET
    query_params: dict[str, Any] = field(default_factory=dict)
    headers: dict[str, str] = field(default_factory=dict)
    body: Optional[Union[str, bytes, dict[str, Any]]] = None
    requested_fields: list[str] = field(default_factory=list)
    filters: dict[str, Any] = field(default_factory=dict)
    pagination: Optional[PaginationConfig] = None
    limits: StructuredDataLimits = field(default_factory=StructuredDataLimits)
    credential_ref: Optional[CredentialReference] = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.request_id or not isinstance(self.request_id, str) or not self.request_id.strip():
            raise StructuredDataValidationError("request_id", "request_id cannot be empty.")
        if not self.endpoint_url or not isinstance(self.endpoint_url, str) or not self.endpoint_url.strip():
            raise StructuredDataValidationError("endpoint_url", "endpoint_url cannot be empty.")

        self.request_id = self.request_id.strip()
        self.endpoint_url = self.endpoint_url.strip()

        if not isinstance(self.method, HttpMethod):
            self.method = HttpMethod.from_string(self.method)

    def to_dict(self) -> dict[str, Any]:
        return {
            "request_id": self.request_id,
            "endpoint_url": self.endpoint_url,
            "method": self.method.value,
            "query_params": dict(self.query_params),
            "headers": dict(self.headers),
            "body": self.body if not isinstance(self.body, bytes) else self.body.decode("utf-8", errors="replace"),
            "requested_fields": list(self.requested_fields),
            "filters": dict(self.filters),
            "pagination": self.pagination.to_dict() if self.pagination else None,
            "limits": self.limits.to_dict(),
            "credential_ref": self.credential_ref.to_dict() if self.credential_ref else None,
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> StructuredDataRequest:
        p_data = data.get("pagination")
        p_cfg = PaginationConfig.from_dict(p_data) if isinstance(p_data, dict) else None
        l_data = data.get("limits")
        limits = StructuredDataLimits.from_dict(l_data) if isinstance(l_data, dict) else StructuredDataLimits()
        c_data = data.get("credential_ref")
        cred_ref = CredentialReference.from_dict(c_data) if isinstance(c_data, dict) else None

        return cls(
            request_id=data.get("request_id", ""),
            endpoint_url=data.get("endpoint_url", ""),
            method=HttpMethod.from_string(data.get("method", "GET")),
            query_params=dict(data.get("query_params", {})),
            headers=dict(data.get("headers", {})),
            body=data.get("body"),
            requested_fields=list(data.get("requested_fields", [])),
            filters=dict(data.get("filters", {})),
            pagination=p_cfg,
            limits=limits,
            credential_ref=cred_ref,
            metadata=dict(data.get("metadata", {})),
        )


@dataclass
class StructuredRecord:
    """
    Individual discrete entity extracted from a structured data payload.
    Binds value, exact source location, cryptographic checksum, and audit provenance.
    """
    record_id: str
    value: Any
    source_location: SourceLocation
    provenance: EvidenceProvenance
    content_hash: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.record_id or not isinstance(self.record_id, str) or not self.record_id.strip():
            raise StructuredDataValidationError("record_id", "record_id cannot be empty.")
        if self.source_location is None:
            raise StructuredDataValidationError("source_location", "source_location cannot be None.")
        if self.provenance is None or not isinstance(self.provenance, EvidenceProvenance):
            raise StructuredDataValidationError("provenance", "provenance must be a valid EvidenceProvenance.")

        if isinstance(self.source_location, str):
            self.source_location = SourceLocation.parse(self.source_location)

        self.record_id = self.record_id.strip()

        if not self.content_hash:
            self.content_hash = compute_structured_hash(self.value)

    def to_raw_source_reference(self, endpoint_url: str = "") -> RawSourceReference:
        """
        Convert to existing RawSourceReference contract for standard CrawlerReport inclusion.
        """
        snippet = canonical_json_dumps(self.value)[:500] if isinstance(self.value, (dict, list)) else str(self.value)[:500]
        url_ref = f"{endpoint_url}#{self.source_location.path}" if endpoint_url else self.source_location.path
        return RawSourceReference(
            url_or_ref=url_ref,
            title=f"Structured Record [{self.record_id}]",
            publisher=self.provenance.source_ref or "Structured Data Source",
            source_type=SourceType.PRIMARY_SOURCE,
            checksum=self.content_hash,
            bytes_fetched=len(snippet.encode("utf-8")),
            content_snippet=snippet,
            metadata={
                "record_id": self.record_id,
                "source_location": self.source_location.path,
                **self.metadata,
            },
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "record_id": self.record_id,
            "value": self.value,
            "source_location": self.source_location.to_dict(),
            "content_hash": self.content_hash,
            "provenance": self.provenance.to_dict(),
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> StructuredRecord:
        loc_data = data.get("source_location", {})
        loc = SourceLocation.from_dict(loc_data) if isinstance(loc_data, dict) else SourceLocation.parse(str(loc_data))
        prov_data = data.get("provenance", {})
        prov = EvidenceProvenance.from_dict(prov_data) if isinstance(prov_data, dict) else prov_data

        return cls(
            record_id=data.get("record_id", ""),
            value=data.get("value"),
            source_location=loc,
            provenance=prov,
            content_hash=data.get("content_hash", ""),
            metadata=dict(data.get("metadata", {})),
        )


@dataclass
class StructuredDataResponse:
    """
    Immutable normalized response container for structured data retrieval.
    Retains status metadata, exact payload, checksums, and schema information.
    """
    response_id: str
    request_id: str
    status_code: int
    provenance: EvidenceProvenance
    status_message: str = "OK"
    content_type: str = "application/json"
    normalized_content_type: StructuredContentType = StructuredContentType.JSON
    encoding: str = "utf-8"
    response_bytes: int = 0
    retrieved_at: str = field(default_factory=utc_now)
    raw_payload_hash: str = ""
    payload: Any = None
    schema_info: Optional[StructuredSchema] = None
    pagination_info: Optional[PaginationMetadata] = None
    headers: dict[str, str] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.response_id or not isinstance(self.response_id, str) or not self.response_id.strip():
            raise StructuredDataValidationError("response_id", "response_id cannot be empty.")
        if not self.request_id or not isinstance(self.request_id, str) or not self.request_id.strip():
            raise StructuredDataValidationError("request_id", "request_id cannot be empty.")
        if not isinstance(self.status_code, int) or self.status_code <= 0:
            raise StructuredDataValidationError("status_code", f"status_code must be positive integer, got {self.status_code}.")
        if self.provenance is None or not isinstance(self.provenance, EvidenceProvenance):
            raise StructuredDataValidationError("provenance", "provenance must be a valid EvidenceProvenance.")

        self.response_id = self.response_id.strip()
        self.request_id = self.request_id.strip()

        if not isinstance(self.normalized_content_type, StructuredContentType):
            self.normalized_content_type = StructuredContentType.from_mime_type(self.content_type)

        if not self.raw_payload_hash and self.payload is not None:
            self.raw_payload_hash = compute_structured_hash(self.payload)

        # Infer schema automatically if not provided and payload is non-empty
        if self.schema_info is None and self.payload is not None:
            self.schema_info = StructuredSchema.infer_from_value(self.payload)

        if self.response_bytes == 0 and self.payload is not None:
            if isinstance(self.payload, bytes):
                self.response_bytes = len(self.payload)
            elif isinstance(self.payload, str):
                self.response_bytes = len(self.payload.encode(self.encoding, errors="replace"))
            elif isinstance(self.payload, (dict, list)):
                self.response_bytes = len(canonical_json_dumps(self.payload).encode("utf-8"))

    @property
    def is_success(self) -> bool:
        """Return True if status code is in 2xx range."""
        return 200 <= self.status_code < 300

    def extract_records(self, base_location: Union[str, SourceLocation] = "root") -> list[StructuredRecord]:
        """
        Extract discrete records from payload at the specified base location.
        If the resolved value is a list, returns one StructuredRecord per element.
        If the resolved value is a dict or primitive, returns a single StructuredRecord.
        """
        if self.status_code == 204 or self.payload is None:
            return []

        loc = SourceLocation.parse(base_location) if isinstance(base_location, str) else base_location
        target_value = loc.resolve(self.payload)

        records: list[StructuredRecord] = []
        if isinstance(target_value, (list, tuple)):
            for idx, item in enumerate(target_value):
                item_loc = loc.child_index(idx)
                rec_id = f"{self.response_id}-{item_loc.path}"
                records.append(
                    StructuredRecord(
                        record_id=rec_id,
                        value=item,
                        source_location=item_loc,
                        provenance=self.provenance,
                        metadata={"parent_response_id": self.response_id},
                    )
                )
        else:
            rec_id = f"{self.response_id}-{loc.path}"
            records.append(
                StructuredRecord(
                    record_id=rec_id,
                    value=target_value,
                    source_location=loc,
                    provenance=self.provenance,
                    metadata={"parent_response_id": self.response_id},
                )
            )

        return records

    def to_dict(self) -> dict[str, Any]:
        return {
            "response_id": self.response_id,
            "request_id": self.request_id,
            "status_code": self.status_code,
            "status_message": self.status_message,
            "content_type": self.content_type,
            "normalized_content_type": self.normalized_content_type.value,
            "encoding": self.encoding,
            "response_bytes": self.response_bytes,
            "retrieved_at": self.retrieved_at,
            "raw_payload_hash": self.raw_payload_hash,
            "payload": self.payload,
            "schema_info": self.schema_info.to_dict() if self.schema_info else None,
            "pagination_info": self.pagination_info.to_dict() if self.pagination_info else None,
            "headers": dict(self.headers),
            "provenance": self.provenance.to_dict(),
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> StructuredDataResponse:
        nct_raw = data.get("normalized_content_type", StructuredContentType.UNKNOWN.value)
        try:
            nct = StructuredContentType(nct_raw)
        except (ValueError, TypeError):
            nct = StructuredContentType.UNKNOWN

        schema_data = data.get("schema_info")
        schema = StructuredSchema.from_dict(schema_data) if isinstance(schema_data, dict) else None

        pag_data = data.get("pagination_info")
        pag = PaginationMetadata.from_dict(pag_data) if isinstance(pag_data, dict) else None

        prov_data = data.get("provenance", {})
        prov = EvidenceProvenance.from_dict(prov_data) if isinstance(prov_data, dict) else prov_data

        return cls(
            response_id=data.get("response_id", ""),
            request_id=data.get("request_id", ""),
            status_code=int(data.get("status_code", 200)),
            provenance=prov,
            status_message=data.get("status_message", "OK"),
            content_type=data.get("content_type", "application/json"),
            normalized_content_type=nct,
            encoding=data.get("encoding", "utf-8"),
            response_bytes=int(data.get("response_bytes", 0)),
            retrieved_at=data.get("retrieved_at", utc_now()),
            raw_payload_hash=data.get("raw_payload_hash", ""),
            payload=data.get("payload"),
            schema_info=schema,
            pagination_info=pag,
            headers=dict(data.get("headers", {})),
            metadata=dict(data.get("metadata", {})),
        )
