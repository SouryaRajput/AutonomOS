"""
Deterministic Structured Query and Filtering Engine (Phase 1 / Part 7 / Step 6).

Provides explicit, bounded query specification and execution for structured data:
- Bounded StructuredQuery model (field selection, equality, comparison, membership, text matching, ordering, limits).
- Strictly deterministic evaluation without arbitrary execution (no eval/exec, no code generation, no LLM queries).
- Local filtering engine for in-memory StructuredRecord evaluation with path traversal and cycle protection.
- Remote API query mapping contract for safe provider parameter pushdown and provenance tracking.
- Guarantees: Never infer semantic data meanings; preserve original queries and applied filters in audit metadata.
"""
from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from enum import Enum
import re
from typing import Any, Callable, Optional, Union

from core.research.contracts.evidence import EvidenceProvenance
from core.research.errors import (
    StructuredDataCancelledError,
    StructuredDataInvalidFieldError,
    StructuredDataInvalidFilterError,
    StructuredDataInvalidOperatorError,
    StructuredDataLimitError,
    StructuredDataValidationError,
)
from core.research.structured.builder import StructuredDataRequestBuilder
from core.research.structured.models import (
    SourceLocation,
    StructuredDataLimits,
    StructuredDataRequest,
    StructuredRecord,
    compute_structured_hash,
)
from core.research.structured.policy import StructuredDataSecurityPolicy


# -----------------------------------------------------------------------------
# Operator & Direction Enumerations
# -----------------------------------------------------------------------------

class FilterOperator(str, Enum):
    """Supported deterministic filter comparison and membership operators."""
    EQUALS = "equals"
    NOT_EQUALS = "not_equals"
    GREATER_THAN = "greater_than"
    GREATER_THAN_OR_EQUAL = "greater_than_or_equal"
    LESS_THAN = "less_than"
    LESS_THAN_OR_EQUAL = "less_than_or_equal"
    IN = "in"
    NOT_IN = "not_in"
    CONTAINS = "contains"
    STARTS_WITH = "starts_with"
    ENDS_WITH = "ends_with"
    EXISTS = "exists"
    IS_NULL = "is_null"
    IS_NOT_NULL = "is_not_null"

    @classmethod
    def from_string(cls, value: str | FilterOperator) -> FilterOperator:
        """Parse operator from canonical name or common symbolic alias."""
        if isinstance(value, cls):
            return value
        if not isinstance(value, str):
            raise StructuredDataInvalidOperatorError(
                str(value), "Operator must be a string or FilterOperator enum."
            )
        val = value.strip().lower()
        mapping: dict[str, FilterOperator] = {
            "equals": cls.EQUALS,
            "==": cls.EQUALS,
            "=": cls.EQUALS,
            "eq": cls.EQUALS,
            "not_equals": cls.NOT_EQUALS,
            "!=": cls.NOT_EQUALS,
            "<>": cls.NOT_EQUALS,
            "ne": cls.NOT_EQUALS,
            "greater_than": cls.GREATER_THAN,
            ">": cls.GREATER_THAN,
            "gt": cls.GREATER_THAN,
            "greater_than_or_equal": cls.GREATER_THAN_OR_EQUAL,
            ">=": cls.GREATER_THAN_OR_EQUAL,
            "gte": cls.GREATER_THAN_OR_EQUAL,
            "less_than": cls.LESS_THAN,
            "<": cls.LESS_THAN,
            "lt": cls.LESS_THAN,
            "less_than_or_equal": cls.LESS_THAN_OR_EQUAL,
            "<=": cls.LESS_THAN_OR_EQUAL,
            "lte": cls.LESS_THAN_OR_EQUAL,
            "in": cls.IN,
            "not_in": cls.NOT_IN,
            "contains": cls.CONTAINS,
            "starts_with": cls.STARTS_WITH,
            "startswith": cls.STARTS_WITH,
            "ends_with": cls.ENDS_WITH,
            "endswith": cls.ENDS_WITH,
            "exists": cls.EXISTS,
            "is_null": cls.IS_NULL,
            "isnull": cls.IS_NULL,
            "is_not_null": cls.IS_NOT_NULL,
            "isnotnull": cls.IS_NOT_NULL,
        }
        if val in mapping:
            return mapping[val]
        raise StructuredDataInvalidOperatorError(
            value, f"Unsupported filter operator '{value}'. Supported: {', '.join(cls.__members__.keys())}"
        )


class OrderDirection(str, Enum):
    """Direction for deterministic record sorting."""
    ASC = "asc"
    DESC = "desc"

    @classmethod
    def from_string(cls, value: str | OrderDirection) -> OrderDirection:
        """Parse order direction case-insensitively."""
        if isinstance(value, cls):
            return value
        if not isinstance(value, str):
            raise StructuredDataValidationError("direction", f"OrderDirection must be a string, got {type(value)}")
        val = value.strip().lower()
        if val in ("asc", "ascending", "1"):
            return cls.ASC
        if val in ("desc", "descending", "-1"):
            return cls.DESC
        raise StructuredDataValidationError("direction", f"Invalid order direction '{value}'. Must be 'asc' or 'desc'.")


# -----------------------------------------------------------------------------
# Field Path Validation
# -----------------------------------------------------------------------------

_FIELD_PATH_PATTERN = re.compile(r"^[a-zA-Z0-9_@]+(\[[0-9]+\])*(\.[a-zA-Z0-9_@]+(\[[0-9]+\])*)*$")
_DANGEROUS_SUBSTRINGS = {"__", ";", "\n", "\r", "eval", "exec", "import", "globals", "locals"}


def validate_field_path(field_name: str, max_depth: int = 10, max_len: int = 256) -> str:
    """
    Sanitize and validate a dotted/indexed field expression.
    Rejects injection syntax, unapproved characters, and excessive nesting.
    """
    if not field_name or not isinstance(field_name, str):
        raise StructuredDataInvalidFieldError(str(field_name), "Field name cannot be empty and must be a string.")
    cleaned = field_name.strip()
    if not cleaned:
        raise StructuredDataInvalidFieldError(field_name, "Field name cannot be empty whitespace.")
    if len(cleaned) > max_len:
        raise StructuredDataInvalidFieldError(
            field_name, f"Field path exceeds maximum length ({len(cleaned)} > {max_len})."
        )
    for danger in _DANGEROUS_SUBSTRINGS:
        if danger in cleaned.lower():
            raise StructuredDataInvalidFieldError(field_name, f"Field path contains forbidden keyword '{danger}'.")

    # Reject wildcard or arbitrary characters
    if not _FIELD_PATH_PATTERN.match(cleaned):
        raise StructuredDataInvalidFieldError(
            field_name, f"Field path '{field_name}' contains invalid characters or syntax."
        )

    # Check nesting depth
    segments_count = cleaned.count(".") + cleaned.count("[")
    if segments_count > max_depth:
        raise StructuredDataInvalidFieldError(
            field_name, f"Field path depth ({segments_count}) exceeds maximum allowed depth of {max_depth}."
        )

    return cleaned


# -----------------------------------------------------------------------------
# Query Models
# -----------------------------------------------------------------------------

@dataclass(frozen=True)
class QueryFilter:
    """
    Explicit, single-clause filter condition.
    Binds a validated field path, comparison operator, and comparison value.
    """
    field: str
    operator: FilterOperator
    value: Any = None

    def __post_init__(self) -> None:
        validated_field = validate_field_path(self.field)
        object.__setattr__(self, "field", validated_field)

        op = FilterOperator.from_string(self.operator)
        object.__setattr__(self, "operator", op)

        # Validate value compatibility according to operator
        if op in (FilterOperator.IN, FilterOperator.NOT_IN):
            if not isinstance(self.value, (list, tuple, set, frozenset)):
                raise StructuredDataInvalidFilterError(
                    f"Operator '{op.value}' requires an iterable (list/set) value, got {type(self.value).__name__}."
                )
            if len(self.value) > 1000:
                raise StructuredDataLimitError("in_clause_items", len(self.value), 1000)
            # Store as tuple for frozen dataclass immutability
            object.__setattr__(self, "value", tuple(self.value))

        elif op in (FilterOperator.CONTAINS, FilterOperator.STARTS_WITH, FilterOperator.ENDS_WITH):
            if self.value is None or not isinstance(self.value, (str, int, float, bool)):
                raise StructuredDataInvalidFilterError(
                    f"Operator '{op.value}' requires a scalar string/numeric value, got {type(self.value).__name__}."
                )

        elif op in (FilterOperator.EXISTS, FilterOperator.IS_NULL, FilterOperator.IS_NOT_NULL):
            # Value is disregarded for nullity / existence checks
            object.__setattr__(self, "value", None)

    def to_dict(self) -> dict[str, Any]:
        return {
            "field": self.field,
            "operator": self.operator.value,
            "value": list(self.value) if isinstance(self.value, (tuple, set, frozenset)) else self.value,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> QueryFilter:
        if not isinstance(data, dict):
            raise StructuredDataInvalidFilterError(f"Filter data must be a dictionary, got {type(data).__name__}.")
        fld = data.get("field")
        op = data.get("operator")
        val = data.get("value")
        if fld is None or op is None:
            raise StructuredDataInvalidFilterError("Filter specification must include 'field' and 'operator'.")
        return cls(field=str(fld), operator=FilterOperator.from_string(op), value=val)


@dataclass(frozen=True)
class QueryOrder:
    """Explicit ordering specification for a single field."""
    field: str
    direction: OrderDirection = OrderDirection.ASC

    def __post_init__(self) -> None:
        validated_field = validate_field_path(self.field)
        object.__setattr__(self, "field", validated_field)
        dir_val = OrderDirection.from_string(self.direction)
        object.__setattr__(self, "direction", dir_val)

    def to_dict(self) -> dict[str, Any]:
        return {
            "field": self.field,
            "direction": self.direction.value,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> QueryOrder:
        if not isinstance(data, dict):
            raise StructuredDataValidationError("order", f"Order data must be a dict, got {type(data).__name__}")
        fld = data.get("field")
        if not fld:
            raise StructuredDataInvalidFieldError("", "QueryOrder requires a non-empty 'field'.")
        direction = data.get("direction", OrderDirection.ASC)
        return cls(field=str(fld), direction=OrderDirection.from_string(direction))


@dataclass(frozen=True)
class StructuredQuery:
    """
    Bounded, explicit structured query specification.
    Coordinates projections (select_fields), filters, ordering, and record bounds.
    """
    select_fields: Optional[tuple[str, ...]] = None
    filters: tuple[QueryFilter, ...] = field(default_factory=tuple)
    order_by: tuple[QueryOrder, ...] = field(default_factory=tuple)
    limit: Optional[int] = None
    offset: int = 0
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        # Validate complexity bounds
        if len(self.filters) > 20:
            raise StructuredDataLimitError("max_filters", len(self.filters), 20)
        if len(self.order_by) > 5:
            raise StructuredDataLimitError("max_order_by", len(self.order_by), 5)

        # Validate select_fields
        if self.select_fields is not None:
            if len(self.select_fields) > 50:
                raise StructuredDataLimitError("max_select_fields", len(self.select_fields), 50)
            validated_select = tuple(validate_field_path(f) for f in self.select_fields)
            object.__setattr__(self, "select_fields", validated_select)

        # Validate filters conversion if passed as list
        if not isinstance(self.filters, tuple):
            object.__setattr__(self, "filters", tuple(self.filters))

        # Validate order_by conversion if passed as list
        if not isinstance(self.order_by, tuple):
            object.__setattr__(self, "order_by", tuple(self.order_by))

        # Validate limit and offset
        if self.limit is not None:
            if not isinstance(self.limit, int) or self.limit <= 0:
                raise StructuredDataValidationError("limit", f"Query limit must be positive integer, got {self.limit}.")
            if self.limit > 10_000:
                raise StructuredDataLimitError("query_limit", self.limit, 10_000)

        if not isinstance(self.offset, int) or self.offset < 0:
            raise StructuredDataValidationError("offset", f"Query offset must be non-negative integer, got {self.offset}.")

    # Fluent Builder Methods
    def where(self, field: str, operator: str | FilterOperator, value: Any = None) -> StructuredQuery:
        """Append a filter clause immutably."""
        qf = QueryFilter(field=field, operator=FilterOperator.from_string(operator), value=value)
        return StructuredQuery(
            select_fields=self.select_fields,
            filters=self.filters + (qf,),
            order_by=self.order_by,
            limit=self.limit,
            offset=self.offset,
            metadata=dict(self.metadata),
        )

    def select(self, *fields: str) -> StructuredQuery:
        """Specify projection fields immutably."""
        return StructuredQuery(
            select_fields=fields,
            filters=self.filters,
            order_by=self.order_by,
            limit=self.limit,
            offset=self.offset,
            metadata=dict(self.metadata),
        )

    def order(self, field: str, direction: str | OrderDirection = OrderDirection.ASC) -> StructuredQuery:
        """Append an order specification immutably."""
        qo = QueryOrder(field=field, direction=OrderDirection.from_string(direction))
        return StructuredQuery(
            select_fields=self.select_fields,
            filters=self.filters,
            order_by=self.order_by + (qo,),
            limit=self.limit,
            offset=self.offset,
            metadata=dict(self.metadata),
        )

    def limit_to(self, limit: Optional[int], offset: int = 0) -> StructuredQuery:
        """Configure result slice bounds immutably."""
        return StructuredQuery(
            select_fields=self.select_fields,
            filters=self.filters,
            order_by=self.order_by,
            limit=limit,
            offset=offset,
            metadata=dict(self.metadata),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "select_fields": list(self.select_fields) if self.select_fields is not None else None,
            "filters": [f.to_dict() for f in self.filters],
            "order_by": [o.to_dict() for o in self.order_by],
            "limit": self.limit,
            "offset": self.offset,
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> StructuredQuery:
        if not isinstance(data, dict):
            raise StructuredDataValidationError("query", f"Query data must be a dict, got {type(data).__name__}")
        selects = data.get("select_fields")
        select_tuple = tuple(str(s) for s in selects) if selects is not None else None
        filters_data = data.get("filters", [])
        filters = tuple(QueryFilter.from_dict(f) for f in filters_data)
        order_data = data.get("order_by", [])
        order_by = tuple(QueryOrder.from_dict(o) for o in order_data)
        return cls(
            select_fields=select_tuple,
            filters=filters,
            order_by=order_by,
            limit=data.get("limit"),
            offset=data.get("offset", 0),
            metadata=data.get("metadata", {}),
        )


@dataclass(frozen=True)
class QueryResult:
    """Encapsulates the deterministic outcome of a StructuredQuery execution."""
    records: list[StructuredRecord]
    total_matched: int
    total_returned: int
    applied_filters_count: int
    is_projected: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "records": [r.to_dict() for r in self.records],
            "total_matched": self.total_matched,
            "total_returned": self.total_returned,
            "applied_filters_count": self.applied_filters_count,
            "is_projected": self.is_projected,
            "metadata": dict(self.metadata),
        }


# -----------------------------------------------------------------------------
# Local Field Extraction & Evaluation Helpers
# -----------------------------------------------------------------------------

def extract_field_value(container: Any, field_path: str) -> tuple[bool, Any]:
    """
    Safely navigate a nested dictionary, array, or object without raising exceptions.
    Returns (exists: bool, resolved_value: Any).
    """
    # If the container is a StructuredRecord, extract its payload value
    curr = container.value if isinstance(container, StructuredRecord) else container

    if curr is None:
        return False, None

    try:
        loc = SourceLocation.parse(field_path)
        val = loc.resolve(curr)
        return True, val
    except Exception:
        # Fallback to direct key check if path has no dots/indices
        if isinstance(curr, dict) and field_path in curr:
            return True, curr[field_path]
        return False, None


def evaluate_filter(filter_spec: QueryFilter, record: StructuredRecord | dict[str, Any]) -> bool:
    """
    Evaluate a single QueryFilter condition against a record deterministically.
    Never throws unhandled exceptions on missing keys, type mismatches, or malformed values.
    """
    exists, val = extract_field_value(record, filter_spec.field)
    op = filter_spec.operator
    target_val = filter_spec.value

    # 1. Existence / Nullity operators
    if op == FilterOperator.EXISTS:
        return exists

    if op == FilterOperator.IS_NULL:
        return (not exists) or (val is None)

    if op == FilterOperator.IS_NOT_NULL:
        return exists and (val is not None)

    # If field does not exist on record, it cannot satisfy equality, comparison, or text operators
    if not exists:
        return False

    # 2. Equality
    if op == FilterOperator.EQUALS:
        if val is None or target_val is None:
            return val is target_val
        if isinstance(val, (int, float)) and isinstance(target_val, (int, float)):
            return float(val) == float(target_val)
        return val == target_val

    if op == FilterOperator.NOT_EQUALS:
        if val is None or target_val is None:
            return val is not target_val
        if isinstance(val, (int, float)) and isinstance(target_val, (int, float)):
            return float(val) != float(target_val)
        return val != target_val

    # 3. Numeric & Lexicographic Comparisons
    if op in (
        FilterOperator.GREATER_THAN,
        FilterOperator.GREATER_THAN_OR_EQUAL,
        FilterOperator.LESS_THAN,
        FilterOperator.LESS_THAN_OR_EQUAL,
    ):
        if val is None or target_val is None:
            return False
        try:
            # Handle numeric conversions cleanly
            if isinstance(val, (int, float)) and isinstance(target_val, (int, float)):
                v_num, t_num = float(val), float(target_val)
                if op == FilterOperator.GREATER_THAN:
                    return v_num > t_num
                if op == FilterOperator.GREATER_THAN_OR_EQUAL:
                    return v_num >= t_num
                if op == FilterOperator.LESS_THAN:
                    return v_num < t_num
                if op == FilterOperator.LESS_THAN_OR_EQUAL:
                    return v_num <= t_num

            # Direct comparison for strings / dates / same-type values
            if type(val) is type(target_val):
                if op == FilterOperator.GREATER_THAN:
                    return val > target_val
                if op == FilterOperator.GREATER_THAN_OR_EQUAL:
                    return val >= target_val
                if op == FilterOperator.LESS_THAN:
                    return val < target_val
                if op == FilterOperator.LESS_THAN_OR_EQUAL:
                    return val <= target_val
        except TypeError:
            return False
        return False

    # 4. Membership Operators
    if op == FilterOperator.IN:
        if not isinstance(target_val, (list, tuple, set, frozenset)):
            return False
        try:
            return val in target_val
        except TypeError:
            return False

    if op == FilterOperator.NOT_IN:
        if not isinstance(target_val, (list, tuple, set, frozenset)):
            return False
        try:
            return val not in target_val
        except TypeError:
            return False

    # 5. Basic Text Matching Operators
    if op == FilterOperator.CONTAINS:
        if isinstance(val, (list, tuple, set)):
            return target_val in val
        if isinstance(val, str):
            return str(target_val) in val
        return False

    if op == FilterOperator.STARTS_WITH:
        if isinstance(val, str):
            return val.startswith(str(target_val))
        return False

    if op == FilterOperator.ENDS_WITH:
        if isinstance(val, str):
            return val.endswith(str(target_val))
        return False

    return False


def project_record(
    record: StructuredRecord,
    select_fields: tuple[str, ...],
    query: Optional[StructuredQuery] = None,
) -> StructuredRecord:
    """
    Project a StructuredRecord to contain only the requested fields.
    Constructs a new StructuredRecord with an updated cryptographic hash
    and audit metadata preserving provenance.
    """
    orig_val = record.value
    projected: dict[str, Any] = {}

    for path in select_fields:
        exists, val = extract_field_value(orig_val, path)
        if exists:
            # Build nested dictionary matching the path hierarchy
            parts = [p.strip() for p in path.split(".") if p.strip()]
            curr_target = projected
            for p in parts[:-1]:
                if p not in curr_target or not isinstance(curr_target[p], dict):
                    curr_target[p] = {}
                curr_target = curr_target[p]
            curr_target[parts[-1]] = val

    prov_meta = dict(record.provenance.to_dict())
    prov = EvidenceProvenance.from_dict({
        **prov_meta,
        "source_ref": record.provenance.source_ref,
    })

    return StructuredRecord(
        record_id=f"{record.record_id}#projected",
        value=projected,
        source_location=record.source_location,
        provenance=prov,
        content_hash=compute_structured_hash(projected),
        metadata={
            **record.metadata,
            "original_record_id": record.record_id,
            "projected_fields": list(select_fields),
        },
    )


# -----------------------------------------------------------------------------
# Local Structured Query Engine
# -----------------------------------------------------------------------------

class LocalStructuredQueryEngine:
    """
    Deterministic query and filtering engine operating on local records.
    Enforces resource limits, handles nested paths, provides multi-key sorting,
    and applies field projections without dynamic code execution.
    """

    def __init__(self, default_limits: Optional[StructuredDataLimits] = None):
        self.default_limits = default_limits or StructuredDataLimits()

    def execute_query(
        self,
        query: StructuredQuery,
        records: list[StructuredRecord],
        limits: Optional[StructuredDataLimits] = None,
        is_cancelled: Optional[Callable[[], bool]] = None,
    ) -> QueryResult:
        """
        Execute a StructuredQuery against a sequence of StructuredRecord instances.
        Guarantees deterministic ordering and strict resource boundedness.
        """
        active_limits = limits or self.default_limits

        # 1. Check cancellation upfront
        if is_cancelled and is_cancelled():
            raise StructuredDataCancelledError(
                target="local_query_engine",
                operation="execute_query",
                message="Query execution cancelled by caller.",
            )

        # 2. Enforce input record count limit
        if len(records) > active_limits.max_records * 2:
            raise StructuredDataLimitError("records_to_filter", len(records), active_limits.max_records * 2)

        # 3. Apply Filters (conjunction / AND)
        filtered_records: list[StructuredRecord] = []
        for i, rec in enumerate(records):
            if i % 100 == 0 and is_cancelled and is_cancelled():
                raise StructuredDataCancelledError(
                    target="local_query_engine",
                    operation="execute_query",
                    message="Query execution cancelled during record filtering.",
                )

            matches_all = True
            for qf in query.filters:
                if not evaluate_filter(qf, rec):
                    matches_all = False
                    break

            if matches_all:
                filtered_records.append(rec)

        total_matched = len(filtered_records)

        # 4. Multi-Key Deterministic Ordering
        if query.order_by:
            def sort_key(rec: StructuredRecord) -> tuple[Any, ...]:
                keys: list[Any] = []
                for qo in query.order_by:
                    exists, val = extract_field_value(rec, qo.field)
                    if not exists or val is None:
                        # Missing/null values sort last in ASC (flag 1), first in DESC (flag 0)
                        sort_flag = 1 if qo.direction == OrderDirection.ASC else 0
                        keys.append((sort_flag, None))
                    else:
                        sort_flag = 0 if qo.direction == OrderDirection.ASC else 1
                        # For DESC, invert numbers or wrap
                        if isinstance(val, (int, float)):
                            comp_val = val if qo.direction == OrderDirection.ASC else -val
                        elif isinstance(val, str):
                            comp_val = val if qo.direction == OrderDirection.ASC else [-ord(c) for c in val]
                        else:
                            comp_val = str(val)
                        keys.append((sort_flag, comp_val))

                # Deterministic tie-breaker: record_id
                keys.append(rec.record_id)
                return tuple(keys)

            filtered_records.sort(key=sort_key)

        # 5. Slicing (Offset and Limit)
        offset = query.offset
        limit = query.limit

        if limit is not None:
            sliced_records = filtered_records[offset : offset + limit]
        else:
            sliced_records = filtered_records[offset:]

        # 6. Field Projection (Select Fields)
        is_projected = False
        final_records: list[StructuredRecord] = []
        if query.select_fields is not None:
            is_projected = True
            for rec in sliced_records:
                final_records.append(project_record(rec, query.select_fields, query=query))
        else:
            final_records = sliced_records

        return QueryResult(
            records=final_records,
            total_matched=total_matched,
            total_returned=len(final_records),
            applied_filters_count=len(query.filters),
            is_projected=is_projected,
            metadata={
                **query.metadata,
                "input_records_count": len(records),
                "total_matched": total_matched,
            },
        )


# -----------------------------------------------------------------------------
# Remote Query Contract & Parameter Mapping
# -----------------------------------------------------------------------------

@dataclass(frozen=True)
class RemoteQueryContract:
    """
    Declarative capabilities contract for remote provider query pushdown.
    Specifies which fields, operators, and parameters an external API endpoint supports.
    """
    supported_filter_fields: dict[str, str] = field(default_factory=dict)
    supported_operators: dict[str, tuple[FilterOperator, ...]] = field(default_factory=dict)
    supported_order_fields: dict[str, str] = field(default_factory=dict)
    limit_param_name: Optional[str] = "limit"
    offset_param_name: Optional[str] = "offset"
    allow_unmapped_filters_locally: bool = True

    def is_filter_supported(self, qf: QueryFilter) -> bool:
        """Check whether a filter can be pushed down directly to the remote API."""
        if qf.field not in self.supported_filter_fields:
            return False
        allowed_ops = self.supported_operators.get(qf.field, (FilterOperator.EQUALS,))
        return qf.operator in allowed_ops


class RemoteQueryMapper:
    """
    Translates a StructuredQuery into valid HTTP query parameters according to
    a RemoteQueryContract, while maintaining strict security policy validation.
    """

    def __init__(self, policy: Optional[StructuredDataSecurityPolicy] = None):
        self.policy = policy or StructuredDataSecurityPolicy()

    def map_query_to_request(
        self,
        query: StructuredQuery,
        base_request: StructuredDataRequest,
        contract: RemoteQueryContract,
    ) -> tuple[StructuredDataRequest, StructuredQuery]:
        """
        Translate supported filters into query parameters and return:
        (updated_request, remaining_local_query)
        """
        applied_remote_params: dict[str, Any] = dict(base_request.query_params)
        applied_remote_filters: list[QueryFilter] = []
        remaining_local_filters: list[QueryFilter] = []

        # 1. Process filters
        for qf in query.filters:
            if contract.is_filter_supported(qf):
                param_name = contract.supported_filter_fields[qf.field]
                # Format value safely
                if qf.operator in (FilterOperator.IN, FilterOperator.NOT_IN):
                    formatted_val = ",".join(str(v) for v in qf.value)
                elif qf.operator in (FilterOperator.EXISTS, FilterOperator.IS_NOT_NULL):
                    formatted_val = "true"
                elif qf.operator == FilterOperator.IS_NULL:
                    formatted_val = "null"
                else:
                    formatted_val = str(qf.value)

                applied_remote_params[param_name] = formatted_val
                applied_remote_filters.append(qf)
            else:
                if not contract.allow_unmapped_filters_locally:
                    raise StructuredDataInvalidFilterError(
                        f"Filter on '{qf.field}' with operator '{qf.operator.value}' is not supported by endpoint contract."
                    )
                remaining_local_filters.append(qf)

        # 2. Process limits and offsets
        if query.limit is not None and contract.limit_param_name:
            applied_remote_params[contract.limit_param_name] = query.limit

        if query.offset > 0 and contract.offset_param_name:
            applied_remote_params[contract.offset_param_name] = query.offset

        # 3. Process ordering if remote contract supports it
        for qo in query.order_by:
            if qo.field in contract.supported_order_fields:
                param_name = contract.supported_order_fields[qo.field]
                order_prefix = "-" if qo.direction == OrderDirection.DESC else ""
                applied_remote_params[param_name] = f"{order_prefix}{qo.field}"

        # 4. Construct updated StructuredDataRequest via builder to validate security policy
        builder = (
            StructuredDataRequestBuilder(self.policy)
            .with_request_id(base_request.request_id)
            .with_endpoint(base_request.endpoint_url)
            .with_method(base_request.method)
            .with_headers(dict(base_request.headers))
            .with_query_params(applied_remote_params)
            .with_limits(base_request.limits)
            .with_metadata({
                **base_request.metadata,
                "applied_remote_filters": [f.to_dict() for f in applied_remote_filters],
                "original_query": query.to_dict(),
            })
        )
        if base_request.body is not None:
            builder.with_body(base_request.body)
        if base_request.pagination is not None:
            builder.with_pagination(base_request.pagination)

        updated_request = builder.build()

        # 5. Build remainder query for local evaluation
        remaining_local_query = StructuredQuery(
            select_fields=query.select_fields,
            filters=tuple(remaining_local_filters),
            order_by=query.order_by,
            limit=query.limit,
            offset=query.offset,
            metadata={
                **query.metadata,
                "remote_filters_applied_count": len(applied_remote_filters),
            },
        )

        return updated_request, remaining_local_query
