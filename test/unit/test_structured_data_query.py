"""
Comprehensive Unit Tests for Deterministic Structured Query and Filtering (Phase 1 / Part 7 / Step 6).

Covers:
- Equality & comparison filters
- Membership & basic text matching
- Field selection / projection
- Deterministic multi-key ordering and tie-breaking
- Record limits and slicing
- Invalid fields, invalid operators, and security bounds
- Nested field paths (dots & array indices)
- Empty results & malformed data
- Cancellation & resource limits
- Remote query mapping, pushdown, and security validation
"""
from __future__ import annotations

import pytest

from core.research.contracts.evidence import EvidenceProvenance
from core.research.errors import (
    StructuredDataCancelledError,
    StructuredDataInvalidFieldError,
    StructuredDataInvalidFilterError,
    StructuredDataInvalidOperatorError,
    StructuredDataLimitError,
    StructuredDataValidationError,
)
from core.research.structured.models import (
    HttpMethod,
    SourceLocation,
    StructuredDataLimits,
    StructuredDataRequest,
    StructuredRecord,
)
from core.research.structured.policy import StructuredDataSecurityPolicy
from core.research.structured.query import (
    FilterOperator,
    LocalStructuredQueryEngine,
    OrderDirection,
    QueryFilter,
    QueryOrder,
    QueryResult,
    RemoteQueryContract,
    RemoteQueryMapper,
    StructuredQuery,
    evaluate_filter,
    extract_field_value,
    project_record,
    validate_field_path,
)


def _make_record(record_id: str, value: dict, path: str = "root") -> StructuredRecord:
    prov = EvidenceProvenance(
        request_id="req-test",
        crawler_task_id="task-test",
        crawler_id="crawler-test",
        source_ref="https://api.example.com/data",
    )
    return StructuredRecord(
        record_id=record_id,
        value=value,
        source_location=SourceLocation.parse(path),
        provenance=prov,
    )


# -----------------------------------------------------------------------------
# 1. Model Validation & Sanitization Tests
# -----------------------------------------------------------------------------

class TestQueryModelValidation:
    def test_filter_operator_aliases(self):
        assert FilterOperator.from_string("==") == FilterOperator.EQUALS
        assert FilterOperator.from_string("=") == FilterOperator.EQUALS
        assert FilterOperator.from_string("eq") == FilterOperator.EQUALS
        assert FilterOperator.from_string("!=") == FilterOperator.NOT_EQUALS
        assert FilterOperator.from_string("ne") == FilterOperator.NOT_EQUALS
        assert FilterOperator.from_string(">") == FilterOperator.GREATER_THAN
        assert FilterOperator.from_string(">=") == FilterOperator.GREATER_THAN_OR_EQUAL
        assert FilterOperator.from_string("<") == FilterOperator.LESS_THAN
        assert FilterOperator.from_string("<=") == FilterOperator.LESS_THAN_OR_EQUAL
        assert FilterOperator.from_string("in") == FilterOperator.IN
        assert FilterOperator.from_string("contains") == FilterOperator.CONTAINS
        assert FilterOperator.from_string("STARTSWITH") == FilterOperator.STARTS_WITH
        assert FilterOperator.from_string("endswith") == FilterOperator.ENDS_WITH
        assert FilterOperator.from_string("is_null") == FilterOperator.IS_NULL

    def test_invalid_operator_raises_error(self):
        with pytest.raises(StructuredDataInvalidOperatorError) as exc_info:
            FilterOperator.from_string("regex_match")
        assert "Unsupported filter operator" in str(exc_info.value)

    def test_order_direction_parsing(self):
        assert OrderDirection.from_string("asc") == OrderDirection.ASC
        assert OrderDirection.from_string("DESC") == OrderDirection.DESC
        assert OrderDirection.from_string("ascending") == OrderDirection.ASC
        assert OrderDirection.from_string("descending") == OrderDirection.DESC

        with pytest.raises(StructuredDataValidationError):
            OrderDirection.from_string("random")

    def test_field_path_validation_valid_cases(self):
        assert validate_field_path("status") == "status"
        assert validate_field_path("user.name") == "user.name"
        assert validate_field_path("items[0].id") == "items[0].id"
        assert validate_field_path("catalog.item[1].details.price") == "catalog.item[1].details.price"

    def test_field_path_validation_invalid_cases(self):
        with pytest.raises(StructuredDataInvalidFieldError):
            validate_field_path("")
        with pytest.raises(StructuredDataInvalidFieldError):
            validate_field_path("   ")
        with pytest.raises(StructuredDataInvalidFieldError):
            validate_field_path("user;DROP TABLE users")
        with pytest.raises(StructuredDataInvalidFieldError):
            validate_field_path("__proto__")
        with pytest.raises(StructuredDataInvalidFieldError):
            validate_field_path("user.eval(cmd)")
        with pytest.raises(StructuredDataInvalidFieldError):
            validate_field_path("a.b.c.d.e.f.g.h.i.j.k.l")  # depth > 10
        with pytest.raises(StructuredDataInvalidFieldError):
            validate_field_path("x" * 300)  # len > 256

    def test_query_filter_in_requires_iterable(self):
        with pytest.raises(StructuredDataInvalidFilterError):
            QueryFilter(field="status", operator=FilterOperator.IN, value="active")  # string not collection

        qf = QueryFilter(field="status", operator=FilterOperator.IN, value=["active", "pending"])
        assert qf.value == ("active", "pending")

    def test_structured_query_complexity_bounds(self):
        # max_filters > 20
        too_many_filters = [QueryFilter(field=f"f_{i}", operator=FilterOperator.EQUALS, value=i) for i in range(21)]
        with pytest.raises(StructuredDataLimitError) as exc_info:
            StructuredQuery(filters=tuple(too_many_filters))
        assert "max_filters" in str(exc_info.value)

        # max_order_by > 5
        too_many_orders = [QueryOrder(field=f"o_{i}") for i in range(6)]
        with pytest.raises(StructuredDataLimitError) as exc_info:
            StructuredQuery(order_by=tuple(too_many_orders))
        assert "max_order_by" in str(exc_info.value)

        # max_select_fields > 50
        too_many_selects = tuple(f"s_{i}" for i in range(51))
        with pytest.raises(StructuredDataLimitError) as exc_info:
            StructuredQuery(select_fields=too_many_selects)
        assert "max_select_fields" in str(exc_info.value)

    def test_query_serialization_roundtrip(self):
        q = (
            StructuredQuery()
            .where("user.age", ">=", 21)
            .where("status", "in", ["active", "pending"])
            .select("user.name", "user.age")
            .order("user.age", OrderDirection.DESC)
            .limit_to(10, 5)
        )
        d = q.to_dict()
        q2 = StructuredQuery.from_dict(d)
        assert q2.limit == 10
        assert q2.offset == 5
        assert len(q2.filters) == 2
        assert q2.filters[0].operator == FilterOperator.GREATER_THAN_OR_EQUAL
        assert q2.filters[1].operator == FilterOperator.IN
        assert q2.order_by[0].direction == OrderDirection.DESC
        assert q2.select_fields == ("user.name", "user.age")


# -----------------------------------------------------------------------------
# 2. Equality & Comparison Filter Tests
# -----------------------------------------------------------------------------

class TestEqualityAndComparisonFilters:
    def test_equality_and_not_equals(self):
        r1 = _make_record("r1", {"status": "active", "code": 200, "flag": True})
        r2 = _make_record("r2", {"status": "inactive", "code": 404, "flag": False})

        assert evaluate_filter(QueryFilter("status", FilterOperator.EQUALS, "active"), r1) is True
        assert evaluate_filter(QueryFilter("status", FilterOperator.EQUALS, "inactive"), r1) is False
        assert evaluate_filter(QueryFilter("status", FilterOperator.NOT_EQUALS, "inactive"), r1) is True

        assert evaluate_filter(QueryFilter("code", FilterOperator.EQUALS, 200), r1) is True
        assert evaluate_filter(QueryFilter("code", FilterOperator.EQUALS, 200.0), r1) is True  # numeric equality
        assert evaluate_filter(QueryFilter("code", FilterOperator.EQUALS, 404), r1) is False

        assert evaluate_filter(QueryFilter("flag", FilterOperator.EQUALS, True), r1) is True
        assert evaluate_filter(QueryFilter("flag", FilterOperator.EQUALS, False), r1) is False

    def test_comparison_operators(self):
        r = _make_record("r", {"score": 85, "rank": 2, "rating": 4.5, "tag": "beta"})

        assert evaluate_filter(QueryFilter("score", FilterOperator.GREATER_THAN, 80), r) is True
        assert evaluate_filter(QueryFilter("score", FilterOperator.GREATER_THAN, 85), r) is False
        assert evaluate_filter(QueryFilter("score", FilterOperator.GREATER_THAN_OR_EQUAL, 85), r) is True
        assert evaluate_filter(QueryFilter("score", FilterOperator.LESS_THAN, 90), r) is True
        assert evaluate_filter(QueryFilter("score", FilterOperator.LESS_THAN, 85), r) is False
        assert evaluate_filter(QueryFilter("score", FilterOperator.LESS_THAN_OR_EQUAL, 85), r) is True

        # Float to int comparisons
        assert evaluate_filter(QueryFilter("rating", FilterOperator.GREATER_THAN, 4), r) is True
        assert evaluate_filter(QueryFilter("rating", FilterOperator.LESS_THAN, 5), r) is True

        # Lexicographic comparison
        assert evaluate_filter(QueryFilter("tag", FilterOperator.GREATER_THAN, "alpha"), r) is True
        assert evaluate_filter(QueryFilter("tag", FilterOperator.LESS_THAN, "charlie"), r) is True

    def test_incompatible_types_safe_evaluation(self):
        r = _make_record("r", {"val": "hello"})

        # Comparing string to int with > should safely return False without TypeError
        assert evaluate_filter(QueryFilter("val", FilterOperator.GREATER_THAN, 100), r) is False
        assert evaluate_filter(QueryFilter("val", FilterOperator.LESS_THAN, 100), r) is False


# -----------------------------------------------------------------------------
# 3. Membership & Text Matching Tests
# -----------------------------------------------------------------------------

class TestMembershipAndTextMatching:
    def test_in_and_not_in(self):
        r = _make_record("r", {"category": "electronics", "tier": 1})

        assert evaluate_filter(QueryFilter("category", FilterOperator.IN, ["books", "electronics"]), r) is True
        assert evaluate_filter(QueryFilter("category", FilterOperator.IN, ["books", "clothing"]), r) is False
        assert evaluate_filter(QueryFilter("category", FilterOperator.NOT_IN, ["books", "clothing"]), r) is True
        assert evaluate_filter(QueryFilter("tier", FilterOperator.IN, [1, 2, 3]), r) is True

    def test_text_matching_contains_starts_ends(self):
        r = _make_record("r", {"filename": "report_2026_q3.pdf", "tags": ["finance", "audit"]})

        # Substring on string
        assert evaluate_filter(QueryFilter("filename", FilterOperator.CONTAINS, "2026"), r) is True
        assert evaluate_filter(QueryFilter("filename", FilterOperator.CONTAINS, "missing"), r) is False

        # Prefix
        assert evaluate_filter(QueryFilter("filename", FilterOperator.STARTS_WITH, "report"), r) is True
        assert evaluate_filter(QueryFilter("filename", FilterOperator.STARTS_WITH, "2026"), r) is False

        # Suffix
        assert evaluate_filter(QueryFilter("filename", FilterOperator.ENDS_WITH, ".pdf"), r) is True
        assert evaluate_filter(QueryFilter("filename", FilterOperator.ENDS_WITH, ".csv"), r) is False

        # Contains on array
        assert evaluate_filter(QueryFilter("tags", FilterOperator.CONTAINS, "audit"), r) is True
        assert evaluate_filter(QueryFilter("tags", FilterOperator.CONTAINS, "marketing"), r) is False

    def test_exists_and_nullity(self):
        r = _make_record("r", {"present": "yes", "empty_val": None})

        assert evaluate_filter(QueryFilter("present", FilterOperator.EXISTS), r) is True
        assert evaluate_filter(QueryFilter("non_existent", FilterOperator.EXISTS), r) is False

        assert evaluate_filter(QueryFilter("empty_val", FilterOperator.IS_NULL), r) is True
        assert evaluate_filter(QueryFilter("non_existent", FilterOperator.IS_NULL), r) is True
        assert evaluate_filter(QueryFilter("present", FilterOperator.IS_NULL), r) is False

        assert evaluate_filter(QueryFilter("present", FilterOperator.IS_NOT_NULL), r) is True
        assert evaluate_filter(QueryFilter("empty_val", FilterOperator.IS_NOT_NULL), r) is False
        assert evaluate_filter(QueryFilter("non_existent", FilterOperator.IS_NOT_NULL), r) is False


# -----------------------------------------------------------------------------
# 4. Nested Paths & Extraction Tests
# -----------------------------------------------------------------------------

class TestNestedFieldPaths:
    def test_deeply_nested_dictionary(self):
        payload = {
            "user": {
                "profile": {
                    "contact": {
                        "email": "alice@example.com",
                        "verified": True,
                    }
                }
            }
        }
        r = _make_record("r_nest", payload)

        assert evaluate_filter(QueryFilter("user.profile.contact.email", FilterOperator.EQUALS, "alice@example.com"), r) is True
        assert evaluate_filter(QueryFilter("user.profile.contact.verified", FilterOperator.EQUALS, True), r) is True
        assert evaluate_filter(QueryFilter("user.profile.contact.phone", FilterOperator.EXISTS), r) is False

    def test_array_indexing_paths(self):
        payload = {
            "items": [
                {"id": 101, "name": "widget"},
                {"id": 102, "name": "gadget"},
            ]
        }
        r = _make_record("r_arr", payload)

        assert evaluate_filter(QueryFilter("items[0].id", FilterOperator.EQUALS, 101), r) is True
        assert evaluate_filter(QueryFilter("items[1].name", FilterOperator.EQUALS, "gadget"), r) is True
        assert evaluate_filter(QueryFilter("items[5].id", FilterOperator.EXISTS), r) is False


# -----------------------------------------------------------------------------
# 5. Field Selection / Projection Tests
# -----------------------------------------------------------------------------

class TestFieldSelectionAndProjection:
    def test_project_record_leaves_unselected_fields_out(self):
        r = _make_record("r_proj", {
            "id": 42,
            "secret_token": "shhh",
            "user": {
                "name": "Alice",
                "ssn": "000-00-0000",
                "details": {"role": "admin", "salary": 100000},
            }
        })

        projected = project_record(r, select_fields=("id", "user.name", "user.details.role"))

        val = projected.value
        assert "id" in val and val["id"] == 42
        assert "secret_token" not in val
        assert val["user"]["name"] == "Alice"
        assert "ssn" not in val["user"]
        assert val["user"]["details"]["role"] == "admin"
        assert "salary" not in val["user"]["details"]

        # Verifies record provenance and hash
        assert projected.content_hash != ""
        assert projected.metadata["original_record_id"] == "r_proj"
        assert projected.record_id == "r_proj#projected"

    def test_engine_projection_flag(self):
        engine = LocalStructuredQueryEngine()
        records = [
            _make_record("r1", {"a": 1, "b": 2, "c": 3}),
            _make_record("r2", {"a": 4, "b": 5, "c": 6}),
        ]
        q = StructuredQuery().select("a", "b")
        res = engine.execute_query(q, records)

        assert res.is_projected is True
        assert len(res.records) == 2
        assert res.records[0].value == {"a": 1, "b": 2}
        assert "c" not in res.records[0].value


# -----------------------------------------------------------------------------
# 6. Ordering & Deterministic Results Tests
# -----------------------------------------------------------------------------

class TestOrderingAndDeterminism:
    def test_single_key_ordering_asc_and_desc(self):
        engine = LocalStructuredQueryEngine()
        records = [
            _make_record("r1", {"score": 30}),
            _make_record("r2", {"score": 10}),
            _make_record("r3", {"score": 20}),
        ]

        # ASC
        res_asc = engine.execute_query(StructuredQuery().order("score", OrderDirection.ASC), records)
        assert [r.record_id for r in res_asc.records] == ["r2", "r3", "r1"]

        # DESC
        res_desc = engine.execute_query(StructuredQuery().order("score", OrderDirection.DESC), records)
        assert [r.record_id for r in res_desc.records] == ["r1", "r3", "r2"]

    def test_multi_key_ordering_with_tie_breaking(self):
        engine = LocalStructuredQueryEngine()
        records = [
            _make_record("r_c", {"dept": "eng", "salary": 100}),
            _make_record("r_a", {"dept": "eng", "salary": 200}),
            _make_record("r_b", {"dept": "sales", "salary": 150}),
            _make_record("r_d", {"dept": "eng", "salary": 100}),
        ]

        # Primary: dept ASC ("eng" before "sales"), Secondary: salary DESC (200 before 100)
        # Ties between r_c and r_d: broken deterministically by record_id ("r_c" before "r_d")
        q = StructuredQuery().order("dept", OrderDirection.ASC).order("salary", OrderDirection.DESC)
        res = engine.execute_query(q, records)

        ids = [r.record_id for r in res.records]
        assert ids == ["r_a", "r_c", "r_d", "r_b"]

    def test_ordering_with_missing_and_null_values(self):
        engine = LocalStructuredQueryEngine()
        records = [
            _make_record("r_val", {"rank": 5}),
            _make_record("r_none", {"rank": None}),
            _make_record("r_missing", {"other": 1}),
            _make_record("r_first", {"rank": 1}),
        ]

        # In ASC, None/missing sorts last
        res = engine.execute_query(StructuredQuery().order("rank", OrderDirection.ASC), records)
        ids = [r.record_id for r in res.records]
        assert ids[0] == "r_first"
        assert ids[1] == "r_val"
        # The last two are r_none and r_missing (sorted deterministically by ID)
        assert set(ids[2:]) == {"r_none", "r_missing"}


# -----------------------------------------------------------------------------
# 7. Limits, Offsets & Slicing Tests
# -----------------------------------------------------------------------------

class TestLimitsAndOffsets:
    def test_limit_and_offset_slicing(self):
        engine = LocalStructuredQueryEngine()
        records = [_make_record(f"r_{i}", {"val": i}) for i in range(10)]

        # Limit 3
        res = engine.execute_query(StructuredQuery().limit_to(3), records)
        assert len(res.records) == 3
        assert res.total_matched == 10
        assert res.total_returned == 3
        assert [r.record_id for r in res.records] == ["r_0", "r_1", "r_2"]

        # Limit 3, Offset 4
        res_page2 = engine.execute_query(StructuredQuery().limit_to(3, offset=4), records)
        assert len(res_page2.records) == 3
        assert [r.record_id for r in res_page2.records] == ["r_4", "r_5", "r_6"]

    def test_query_filter_conjunction_and_empty_results(self):
        engine = LocalStructuredQueryEngine()
        records = [
            _make_record("r1", {"status": "active", "age": 25}),
            _make_record("r2", {"status": "active", "age": 40}),
            _make_record("r3", {"status": "inactive", "age": 30}),
        ]

        # Conjunction matches only r2
        q = StructuredQuery().where("status", "equals", "active").where("age", ">", 30)
        res = engine.execute_query(q, records)
        assert len(res.records) == 1
        assert res.records[0].record_id == "r2"

        # No match returns empty results cleanly
        q_empty = StructuredQuery().where("status", "equals", "deleted")
        res_empty = engine.execute_query(q_empty, records)
        assert len(res_empty.records) == 0
        assert res_empty.total_matched == 0


# -----------------------------------------------------------------------------
# 8. Malformed Data, Safety & Cancellation Tests
# -----------------------------------------------------------------------------

class TestSafetyAndCancellation:
    def test_malformed_records_handled_gracefully(self):
        engine = LocalStructuredQueryEngine()
        records = [
            _make_record("r_dict", {"num": 10}),
            _make_record("r_primitive", "not a dict"),  # primitive value
            _make_record("r_none", None),               # None value
        ]
        q = StructuredQuery().where("num", ">", 5)
        res = engine.execute_query(q, records)

        assert len(res.records) == 1
        assert res.records[0].record_id == "r_dict"

    def test_cancellation_callback_raises_cancelled_error(self):
        engine = LocalStructuredQueryEngine()
        records = [_make_record(f"r_{i}", {"val": i}) for i in range(10)]

        cancelled = True
        with pytest.raises(StructuredDataCancelledError) as exc_info:
            engine.execute_query(StructuredQuery(), records, is_cancelled=lambda: cancelled)
        assert "cancelled" in str(exc_info.value).lower()


# -----------------------------------------------------------------------------
# 9. Remote Query Mapping & Policy Enforcement Tests
# -----------------------------------------------------------------------------

class TestRemoteQueryMappingAndSecurity:
    def test_map_supported_filters_to_request_query_params(self):
        policy = StructuredDataSecurityPolicy(allowed_domains={"api.example.com"})
        contract = RemoteQueryContract(
            supported_filter_fields={"status": "status", "category": "cat"},
            supported_operators={
                "status": (FilterOperator.EQUALS,),
                "category": (FilterOperator.IN,),
            },
            limit_param_name="limit",
        )
        mapper = RemoteQueryMapper(policy)

        base_req = StructuredDataRequest(
            request_id="req-base",
            endpoint_url="https://api.example.com/items",
            method=HttpMethod.GET,
            query_params={"existing": "1"},
        )

        query = (
            StructuredQuery()
            .where("status", "equals", "published")
            .where("category", "in", ["tech", "science"])
            .where("local_only_field", ">", 100)  # unsupported remotely
            .limit_to(25)
        )

        updated_req, local_remainder = mapper.map_query_to_request(query, base_req, contract)

        # Verify remote parameters were safely injected
        assert updated_req.query_params["existing"] == "1"
        assert updated_req.query_params["status"] == "published"
        assert updated_req.query_params["cat"] == "tech,science"
        assert updated_req.query_params["limit"] == 25

        # Verify remote filters tracked in metadata
        assert "applied_remote_filters" in updated_req.metadata
        assert len(updated_req.metadata["applied_remote_filters"]) == 2

        # Verify local remainder holds the unmapped filter
        assert len(local_remainder.filters) == 1
        assert local_remainder.filters[0].field == "local_only_field"

    def test_remote_mapping_strict_contract_rejection(self):
        policy = StructuredDataSecurityPolicy(allowed_domains={"api.example.com"})
        contract = RemoteQueryContract(
            supported_filter_fields={"status": "status"},
            allow_unmapped_filters_locally=False,  # Disallow fallback
        )
        mapper = RemoteQueryMapper(policy)
        base_req = StructuredDataRequest(request_id="req-1", endpoint_url="https://api.example.com/v1")

        query = StructuredQuery().where("unsupported_field", "equals", 123)

        with pytest.raises(StructuredDataInvalidFilterError) as exc_info:
            mapper.map_query_to_request(query, base_req, contract)
        assert "not supported by endpoint contract" in str(exc_info.value)
