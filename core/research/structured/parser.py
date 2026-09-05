from __future__ import annotations

import csv
from dataclasses import dataclass, field
import io
import json
import logging
import re
from typing import Any, Callable, Optional, Union
import xml.etree.ElementTree as ET

from core.research.errors import (
    StructuredDataCancelledError,
    StructuredDataLimitError,
    StructuredDataMalformedResponseError,
)
from core.research.structured.models import (
    StructuredContentType,
    StructuredDataLimits,
    StructuredDataResponse,
    StructuredSchema,
    canonical_json_dumps,
    compute_structured_hash,
)

logger = logging.getLogger("AutonomOS.Research.Structured.Parser")

# Security patterns preventing XML External Entity (XXE) and entity expansion bombs
_XXE_FORBIDDEN_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"<!DOCTYPE", re.IGNORECASE),
    re.compile(r"<!ENTITY", re.IGNORECASE),
    re.compile(r"SYSTEM\s+[\"']", re.IGNORECASE),
    re.compile(r"PUBLIC\s+[\"']", re.IGNORECASE),
)


@dataclass(frozen=True)
class ParsedStructuredData:
    """
    Deterministic result of structured response parsing and normalization.
    Preserves exact source data, inferred schema, cryptographic hash, and metadata.
    """
    payload: Any
    content_type: str
    normalized_content_type: StructuredContentType
    schema_info: StructuredSchema
    content_hash: str
    record_count: int
    raw_bytes: int
    metadata: dict[str, Any] = field(default_factory=dict)


class StructuredResponseParser:
    """
    Deterministic parser and structural normalizer for machine-readable data formats:
    JSON, JSONL / NDJSON, XML, and CSV.
    
    Enforces strict safety envelope limits (depth, records, fields, field sizes, total bytes)
    and neutralizes XXE and injection attacks. Never performs semantic field interpretation.
    """

    @classmethod
    def parse_response(
        cls,
        response: StructuredDataResponse,
        limits: Optional[StructuredDataLimits] = None,
        is_cancelled: Optional[Callable[[], bool]] = None,
    ) -> StructuredDataResponse:
        """
        Parse and structurally normalize an incoming StructuredDataResponse.
        Populates payload, schema_info, and hashes while preserving response lineage.
        """
        effective_limits = limits or StructuredDataLimits()

        # If payload is already parsed into structured collections and schema exists, return as-is
        if (
            response.payload is not None
            and isinstance(response.payload, (dict, list))
            and response.schema_info is not None
            and response.normalized_content_type != StructuredContentType.UNKNOWN
        ):
            return response

        raw_content = response.payload
        content_type = response.content_type or response.normalized_content_type.value
        target = response.metadata.get("endpoint_url", response.response_id)

        parsed = cls.parse_content(
            content=raw_content if raw_content is not None else b"",
            content_type=content_type,
            limits=effective_limits,
            target=target,
            encoding=response.encoding or "utf-8",
            is_cancelled=is_cancelled,
            status_code=response.status_code,
        )

        return StructuredDataResponse(
            response_id=response.response_id,
            request_id=response.request_id,
            status_code=response.status_code,
            provenance=response.provenance,
            status_message=response.status_message,
            content_type=parsed.content_type,
            normalized_content_type=parsed.normalized_content_type,
            encoding=response.encoding or "utf-8",
            response_bytes=parsed.raw_bytes or response.response_bytes,
            retrieved_at=response.retrieved_at,
            raw_payload_hash=parsed.content_hash,
            payload=parsed.payload,
            schema_info=parsed.schema_info,
            pagination_info=response.pagination_info,
            headers=dict(response.headers),
            metadata={**response.metadata, **parsed.metadata},
        )

    @classmethod
    def parse_content(
        cls,
        content: Union[bytes, str, dict[str, Any], list[Any]],
        content_type: Union[str, StructuredContentType] = StructuredContentType.UNKNOWN,
        limits: Optional[StructuredDataLimits] = None,
        target: str = "",
        encoding: str = "utf-8",
        is_cancelled: Optional[Callable[[], bool]] = None,
        status_code: int = 200,
    ) -> ParsedStructuredData:
        """
        Parse and validate raw content into normalized Python structures.
        """
        effective_limits = limits or StructuredDataLimits()

        if is_cancelled and is_cancelled():
            raise StructuredDataCancelledError(
                target=target,
                operation="parse",
                message="Parsing cancelled by caller.",
            )

        # 1. Handle already parsed python dict/list structures
        if isinstance(content, (dict, list)):
            field_count = [0]
            cls._validate_structure_limits(content, effective_limits, 1, is_cancelled, field_count)
            schema = StructuredSchema.infer_from_value(content)
            c_hash = compute_structured_hash(content)
            rec_count = len(content) if isinstance(content, list) else (
                len(content.get("rows", [])) if "rows" in content and isinstance(content["rows"], list) else (
                    len(content.get("items", [])) if "items" in content and isinstance(content["items"], list) else 1
                )
            )
            raw_bytes = len(canonical_json_dumps(content).encode("utf-8"))
            n_type = (
                content_type
                if isinstance(content_type, StructuredContentType)
                else StructuredContentType.from_mime_type(content_type)
            )
            if n_type == StructuredContentType.UNKNOWN:
                n_type = StructuredContentType.JSON

            return ParsedStructuredData(
                payload=content,
                content_type=n_type.value,
                normalized_content_type=n_type,
                schema_info=schema,
                content_hash=c_hash,
                record_count=rec_count,
                raw_bytes=raw_bytes,
                metadata={"encoding": "utf-8"},
            )

        # 2. Decode raw bytes or strings
        content_str, content_bytes = cls._decode_content(
            content=content,
            declared_encoding=encoding,
            limits=effective_limits,
            target=target,
        )

        # 3. Resolve normalized content type
        normalized_ct = cls.detect_content_type(content_str, content_type)
        ct_str = content_type if isinstance(content_type, str) and content_type else normalized_ct.value

        # 4. Dispatch to format-specific parser
        if normalized_ct == StructuredContentType.JSON:
            payload = cls._parse_json(
                content_str=content_str,
                limits=effective_limits,
                target=target,
                is_cancelled=is_cancelled,
                status_code=status_code,
            )
        elif normalized_ct == StructuredContentType.JSONL:
            payload = cls._parse_jsonl(
                content_str=content_str,
                limits=effective_limits,
                target=target,
                is_cancelled=is_cancelled,
            )
        elif normalized_ct == StructuredContentType.XML:
            payload = cls._parse_xml(
                content_str=content_str,
                limits=effective_limits,
                target=target,
                is_cancelled=is_cancelled,
                status_code=status_code,
            )
        elif normalized_ct == StructuredContentType.CSV:
            payload = cls._parse_csv(
                content_str=content_str,
                limits=effective_limits,
                target=target,
                is_cancelled=is_cancelled,
                status_code=status_code,
            )
        else:
            # Attempt JSON first, then CSV
            try:
                payload = cls._parse_json(content_str, effective_limits, target, is_cancelled, status_code)
                normalized_ct = StructuredContentType.JSON
            except StructuredDataMalformedResponseError:
                try:
                    payload = cls._parse_csv(content_str, effective_limits, target, is_cancelled, status_code)
                    normalized_ct = StructuredContentType.CSV
                except StructuredDataMalformedResponseError:
                    raise StructuredDataMalformedResponseError(
                        target=target,
                        content_type=str(content_type),
                        reason="Unable to determine or parse structured data format.",
                    )

        # 5. Extract structural schema
        schema = StructuredSchema.infer_from_value(payload) if payload is not None else StructuredSchema(
            structure_type=StructuredSchema.from_dict({}).structure_type if hasattr(StructuredSchema, "EMPTY") else "empty"  # type: ignore
        )

        # 6. Compute deterministic content hash
        c_hash = compute_structured_hash(payload) if payload is not None else compute_structured_hash("")

        # 7. Calculate discrete record count
        if isinstance(payload, list):
            rec_count = len(payload)
        elif isinstance(payload, dict) and "rows" in payload and isinstance(payload["rows"], list):
            rec_count = len(payload["rows"])
        elif isinstance(payload, dict) and "items" in payload and isinstance(payload["items"], list):
            rec_count = len(payload["items"])
        elif payload is not None:
            rec_count = 1
        else:
            rec_count = 0

        return ParsedStructuredData(
            payload=payload,
            content_type=ct_str,
            normalized_content_type=normalized_ct,
            schema_info=schema,
            content_hash=c_hash,
            record_count=rec_count,
            raw_bytes=len(content_bytes),
            metadata={"encoding": encoding},
        )

    # -------------------------------------------------------------------------
    # Format-Specific Parsers
    # -------------------------------------------------------------------------

    @classmethod
    def _parse_json(
        cls,
        content_str: str,
        limits: StructuredDataLimits,
        target: str,
        is_cancelled: Optional[Callable[[], bool]],
        status_code: int = 200,
    ) -> Any:
        """Parse JSON document with depth, size, and field bounding."""
        stripped = content_str.strip()
        if not stripped:
            if status_code == 204:
                return None
            raise StructuredDataMalformedResponseError(
                target=target,
                content_type="application/json",
                reason="Empty JSON payload received.",
            )

        try:
            val = json.loads(stripped)
        except (json.JSONDecodeError, ValueError) as e:
            raise StructuredDataMalformedResponseError(
                target=target,
                content_type="application/json",
                reason=f"JSON decode error: {e}",
            ) from e

        # Validate limits and bounds
        field_count = [0]
        cls._validate_structure_limits(val, limits, 1, is_cancelled, field_count)

        if isinstance(val, list) and len(val) > limits.max_records:
            raise StructuredDataLimitError("max_records", len(val), limits.max_records)

        return val

    @classmethod
    def _parse_jsonl(
        cls,
        content_str: str,
        limits: StructuredDataLimits,
        target: str,
        is_cancelled: Optional[Callable[[], bool]],
    ) -> list[Any]:
        """Parse streaming newline-delimited JSON (NDJSON/JSONL)."""
        lines = content_str.splitlines()
        records: list[Any] = []
        field_count = [0]

        for line_idx, raw_line in enumerate(lines):
            if is_cancelled and is_cancelled():
                raise StructuredDataCancelledError(target=target, operation="parse", message="Cancelled during JSONL parsing.")

            line = raw_line.strip()
            if not line:
                continue

            if len(records) >= limits.max_records:
                raise StructuredDataLimitError("max_records", len(records) + 1, limits.max_records)

            try:
                record = json.loads(line)
            except (json.JSONDecodeError, ValueError) as e:
                raise StructuredDataMalformedResponseError(
                    target=target,
                    content_type="application/x-ndjson",
                    reason=f"JSONL parse error on line {line_idx + 1}: {e}",
                    details={"line_number": line_idx + 1, "line_content": line[:200]},
                ) from e

            cls._validate_structure_limits(record, limits, 1, is_cancelled, field_count)
            records.append(record)

        return records

    @classmethod
    def _parse_xml(
        cls,
        content_str: str,
        limits: StructuredDataLimits,
        target: str,
        is_cancelled: Optional[Callable[[], bool]],
        status_code: int = 200,
    ) -> Optional[dict[str, Any]]:
        """
        Parse XML safely with complete XXE and entity expansion bomb prevention.
        Converts XML hierarchy into normalized dictionary with @attributes and repeated tags.
        """
        stripped = content_str.strip()
        if not stripped:
            if status_code == 204:
                return None
            raise StructuredDataMalformedResponseError(
                target=target,
                content_type="application/xml",
                reason="Empty XML document received.",
            )

        # Strict security scan for DOCTYPE, ENTITY, and SYSTEM references
        for pat in _XXE_FORBIDDEN_PATTERNS:
            if pat.search(stripped):
                raise StructuredDataMalformedResponseError(
                    target=target,
                    content_type="application/xml",
                    reason="XML contains prohibited DTD, ENTITY, or external reference declaration (XXE/XML bomb prevention).",
                )

        try:
            root = ET.fromstring(stripped)
        except ET.ParseError as e:
            raise StructuredDataMalformedResponseError(
                target=target,
                content_type="application/xml",
                reason=f"XML parse error: {e}",
            ) from e

        field_count = [0]
        root_val = cls._convert_xml_node(root, 1, limits, is_cancelled, field_count)
        root_tag = root.tag.split("}")[-1]
        return {root_tag: root_val}

    @classmethod
    def _convert_xml_node(
        cls,
        elem: ET.Element,
        depth: int,
        limits: StructuredDataLimits,
        is_cancelled: Optional[Callable[[], bool]],
        field_count: list[int],
    ) -> Any:
        """Recursively transform an XML ElementTree into a normalized Python dictionary."""
        if is_cancelled and is_cancelled():
            raise StructuredDataCancelledError(operation="parse", message="Cancelled during XML conversion.")

        if depth > limits.max_depth:
            raise StructuredDataLimitError("max_depth", depth, limits.max_depth)

        field_count[0] += 1
        if field_count[0] > limits.max_fields:
            raise StructuredDataLimitError("max_fields", field_count[0], limits.max_fields)

        has_children = len(elem) > 0
        has_attribs = bool(elem.attrib)
        text = (elem.text or "").strip()

        if text and len(text.encode("utf-8")) > limits.max_field_size:
            raise StructuredDataLimitError("max_field_size", len(text.encode("utf-8")), limits.max_field_size)

        # Leaf node without attributes
        if not has_children and not has_attribs:
            return text

        node: dict[str, Any] = {}

        # Preserve attributes prefixed with @
        for k, v in elem.attrib.items():
            attr_name = k.split("}")[-1]
            if len(v.encode("utf-8")) > limits.max_field_size:
                raise StructuredDataLimitError("max_field_size", len(v.encode("utf-8")), limits.max_field_size)
            node[f"@{attr_name}"] = v
            field_count[0] += 1

        # Preserve mixed text content under #text
        if text:
            node["#text"] = text

        # Group children by clean tag name to represent repeated sibling tags as lists
        child_groups: dict[str, list[Any]] = {}
        for child in elem:
            child_tag = child.tag.split("}")[-1]
            child_val = cls._convert_xml_node(child, depth + 1, limits, is_cancelled, field_count)
            child_groups.setdefault(child_tag, []).append(child_val)

        for child_tag, items in child_groups.items():
            if len(items) == 1:
                node[child_tag] = items[0]
            else:
                node[child_tag] = items

        return node

    @classmethod
    def _parse_csv(
        cls,
        content_str: str,
        limits: StructuredDataLimits,
        target: str,
        is_cancelled: Optional[Callable[[], bool]],
        status_code: int = 200,
    ) -> Optional[dict[str, Any]]:
        """
        Parse CSV document into normalized rows and columns with deterministic paths.
        Structure: {"columns": [...], "rows": [{"col": val}, ...], "total_rows": int}
        """
        stripped = content_str.strip()
        if not stripped:
            if status_code == 204:
                return None
            return {"columns": [], "rows": [], "total_rows": 0}

        # Sniff delimiter safely from sample
        sample = stripped[:4096]
        try:
            dialect = csv.Sniffer().sniff(sample, delimiters=",\t;|")
            delimiter = dialect.delimiter
        except Exception:
            delimiter = ","

        try:
            f = io.StringIO(stripped)
            reader = csv.reader(f, delimiter=delimiter)
            try:
                header = next(reader)
            except StopIteration:
                return {"columns": [], "rows": [], "total_rows": 0}
        except Exception as e:
            raise StructuredDataMalformedResponseError(
                target=target,
                content_type="text/csv",
                reason=f"CSV read error: {e}",
            ) from e

        # Clean and disambiguate column names
        columns: list[str] = []
        seen_cols: dict[str, int] = {}
        for idx, c in enumerate(header):
            clean_c = c.strip() or f"col_{idx + 1}"
            if clean_c in seen_cols:
                seen_cols[clean_c] += 1
                unique_c = f"{clean_c}_{seen_cols[clean_c]}"
            else:
                seen_cols[clean_c] = 1
                unique_c = clean_c
            columns.append(unique_c)

        rows: list[dict[str, Any]] = []
        field_count = len(columns)

        for row_idx, row in enumerate(reader):
            if is_cancelled and is_cancelled():
                raise StructuredDataCancelledError(target=target, operation="parse", message="Cancelled during CSV parsing.")

            # Skip empty lines
            if not row or not any(field.strip() for field in row):
                continue

            if len(rows) >= limits.max_records:
                raise StructuredDataLimitError("max_records", len(rows) + 1, limits.max_records)

            row_dict: dict[str, Any] = {}
            for col_idx, col_name in enumerate(columns):
                val = row[col_idx].strip() if col_idx < len(row) else ""
                size = len(val.encode("utf-8"))
                if size > limits.max_field_size:
                    raise StructuredDataLimitError("max_field_size", size, limits.max_field_size)
                row_dict[col_name] = val
                field_count += 1

            if field_count > limits.max_fields:
                raise StructuredDataLimitError("max_fields", field_count, limits.max_fields)

            rows.append(row_dict)

        return {
            "columns": columns,
            "rows": rows,
            "total_rows": len(rows),
        }

    # -------------------------------------------------------------------------
    # Helper Utilities & Envelopes
    # -------------------------------------------------------------------------

    @classmethod
    def detect_content_type(
        cls,
        content: Union[bytes, str],
        declared_type: Union[str, StructuredContentType] = StructuredContentType.UNKNOWN,
    ) -> StructuredContentType:
        """
        Determine canonical structured content type from declared MIME or raw syntax.
        """
        if isinstance(declared_type, StructuredContentType) and declared_type != StructuredContentType.UNKNOWN:
            return declared_type

        if isinstance(declared_type, str) and declared_type:
            n_type = StructuredContentType.from_mime_type(declared_type)
            if n_type != StructuredContentType.UNKNOWN:
                return n_type

        # Sniff content syntax
        text = (content.decode("utf-8", errors="ignore") if isinstance(content, bytes) else str(content)).strip()
        if not text:
            return StructuredContentType.JSON

        if text.startswith("<?xml") or (text.startswith("<") and text.endswith(">")):
            return StructuredContentType.XML

        if text.startswith("{") or text.startswith("["):
            lines = [line.strip() for line in text.splitlines() if line.strip()]
            if len(lines) > 1 and all(l.startswith("{") and l.endswith("}") for l in lines[:5]):
                return StructuredContentType.JSONL
            return StructuredContentType.JSON

        # Check CSV features
        first_line = text.splitlines()[0] if text.splitlines() else ""
        if any(d in first_line for d in (",", "\t", ";", "|")):
            return StructuredContentType.CSV

        return StructuredContentType.UNKNOWN

    @classmethod
    def _decode_content(
        cls,
        content: Union[bytes, str],
        declared_encoding: str,
        limits: StructuredDataLimits,
        target: str,
    ) -> tuple[str, bytes]:
        """Decode raw input bytes or strings, checking total byte bounds."""
        if isinstance(content, str):
            content_bytes = content.encode(declared_encoding, errors="replace")
            content_str = content
        elif isinstance(content, bytes):
            content_bytes = content
            encodings_to_try = [declared_encoding]
            if declared_encoding and declared_encoding.lower().replace("-", "") == "utf8":
                encodings_to_try.append("utf-8-sig")
            decoded: Optional[str] = None
            for enc in encodings_to_try:
                try:
                    decoded = content.decode(enc)
                    break
                except (UnicodeDecodeError, LookupError):
                    continue
            if decoded is None:
                raise StructuredDataMalformedResponseError(
                    target=target,
                    content_type="bytes",
                    reason=f"Failed to decode byte payload with declared encoding '{declared_encoding}'.",
                )
            content_str = decoded
        else:
            raise StructuredDataMalformedResponseError(
                target=target,
                content_type=type(content).__name__,
                reason=f"Unsupported raw content type: {type(content).__name__}",
            )

        if len(content_bytes) > limits.max_bytes:
            raise StructuredDataLimitError("max_bytes", len(content_bytes), limits.max_bytes)

        return content_str, content_bytes

    @classmethod
    def _validate_structure_limits(
        cls,
        val: Any,
        limits: StructuredDataLimits,
        current_depth: int,
        is_cancelled: Optional[Callable[[], bool]],
        field_count: list[int],
    ) -> None:
        """Recursively enforce nesting depth, total fields count, and field string size limits."""
        if is_cancelled and is_cancelled():
            raise StructuredDataCancelledError(operation="parse", message="Parsing cancelled by caller.")

        if current_depth > limits.max_depth:
            raise StructuredDataLimitError("max_depth", current_depth, limits.max_depth)

        if isinstance(val, dict):
            field_count[0] += len(val)
            if field_count[0] > limits.max_fields:
                raise StructuredDataLimitError("max_fields", field_count[0], limits.max_fields)

            for k, v in val.items():
                if isinstance(k, str) and len(k.encode("utf-8")) > limits.max_field_size:
                    raise StructuredDataLimitError("max_field_size", len(k.encode("utf-8")), limits.max_field_size)
                cls._validate_structure_limits(v, limits, current_depth + 1, is_cancelled, field_count)

        elif isinstance(val, (list, tuple)):
            field_count[0] += len(val)
            if field_count[0] > limits.max_fields:
                raise StructuredDataLimitError("max_fields", field_count[0], limits.max_fields)

            for item in val:
                cls._validate_structure_limits(item, limits, current_depth + 1, is_cancelled, field_count)

        elif isinstance(val, str):
            size = len(val.encode("utf-8"))
            if size > limits.max_field_size:
                raise StructuredDataLimitError("max_field_size", size, limits.max_field_size)
