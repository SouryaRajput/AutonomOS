"""
Unit tests for Structured Data Source and Structural Domain Models (Phase 1 / Part 7 / Step 1).

Covers:
- Model validation and invalid state rejection
- SourceLocation path parsing, formatting, traversal, and exact resolution
- Resolution errors and edge cases (out of bounds, missing keys, type mismatches)
- Schema inference and representation (objects, arrays, primitives, nullability, optionality)
- Deterministic hashing via canonical JSON (key ordering invariance)
- StructuredRecord, StructuredDataResponse, StructuredDataRequest, StructuredDataSource
- Serialization and deserialization round-trips (to_dict / from_dict)
- Provenance integrity
"""
from __future__ import annotations

import json
import unittest

from core.research.contracts.evidence import EvidenceProvenance
from core.research.errors import (
    StructuredDataLimitError,
    StructuredDataLocationError,
    StructuredDataValidationError,
)
from core.research.structured.models import (
    FieldSegment,
    HttpMethod,
    IndexSegment,
    PaginationConfig,
    PaginationMetadata,
    PaginationType,
    SchemaField,
    SchemaStructureType,
    SourceLocation,
    StructuredContentType,
    StructuredDataLimits,
    StructuredDataRequest,
    StructuredDataResponse,
    StructuredDataSource,
    StructuredDataType,
    StructuredRecord,
    StructuredSchema,
    StructuredSourceType,
    canonical_json_dumps,
    compute_structured_hash,
)
from core.research.types import SourceType


def _make_provenance(source_ref: str = "https://api.example.com/data") -> EvidenceProvenance:
    return EvidenceProvenance(
        request_id="req-test-123",
        crawler_task_id="task-test-456",
        crawler_id="crawler-structured-001",
        question_id="q-test-789",
        source_ref=source_ref,
        correlation_id="corr-test-abc",
    )


class TestSourceLocationParsingAndFormatting(unittest.TestCase):
    """Tests for exact path parsing, formatting, and segment representations."""

    def test_parse_root_locations(self):
        loc1 = SourceLocation.parse("root")
        self.assertEqual(loc1.path, "root")
        self.assertEqual(len(loc1.segments), 0)

        loc2 = SourceLocation.parse("")
        self.assertEqual(loc2.path, "root")

        loc3 = SourceLocation.parse("$")
        self.assertEqual(loc3.path, "root")

    def test_parse_simple_dot_fields(self):
        loc = SourceLocation.parse("data.items")
        self.assertEqual(loc.path, "data.items")
        self.assertEqual(len(loc.segments), 2)
        self.assertIsInstance(loc.segments[0], FieldSegment)
        self.assertEqual(loc.segments[0].name, "data")
        self.assertIsInstance(loc.segments[1], FieldSegment)
        self.assertEqual(loc.segments[1].name, "items")

    def test_parse_exact_complex_path(self):
        # Explicitly required in instructions: data.items[3].name
        loc = SourceLocation.parse("data.items[3].name")
        self.assertEqual(loc.path, "data.items[3].name")
        self.assertEqual(len(loc.segments), 4)
        self.assertEqual(loc.segments[0], FieldSegment("data"))
        self.assertEqual(loc.segments[1], FieldSegment("items"))
        self.assertEqual(loc.segments[2], IndexSegment(3))
        self.assertEqual(loc.segments[3], FieldSegment("name"))

    def test_parse_bracket_field_notation(self):
        loc = SourceLocation.parse('response["status-code"][0]')
        self.assertEqual(loc.path, "response.status-code[0]")
        self.assertEqual(loc.segments[0], FieldSegment("response"))
        self.assertEqual(loc.segments[1], FieldSegment("status-code"))
        self.assertEqual(loc.segments[2], IndexSegment(0))

    def test_parse_leading_index(self):
        loc = SourceLocation.parse("[5].title")
        self.assertEqual(loc.path, "[5].title")
        self.assertEqual(loc.segments[0], IndexSegment(5))
        self.assertEqual(loc.segments[1], FieldSegment("title"))

    def test_parse_nested_indices(self):
        loc = SourceLocation.parse("matrix[1][2]")
        self.assertEqual(loc.path, "matrix[1][2]")
        self.assertEqual(loc.segments[0], FieldSegment("matrix"))
        self.assertEqual(loc.segments[1], IndexSegment(1))
        self.assertEqual(loc.segments[2], IndexSegment(2))

    def test_parse_invalid_syntax_raises_location_error(self):
        with self.assertRaises(StructuredDataLocationError):
            SourceLocation.parse("data.[invalid")

        with self.assertRaises(StructuredDataLocationError):
            SourceLocation.parse(12345)  # non-string non-location

    def test_child_field_and_child_index_builders(self):
        base = SourceLocation.parse("data")
        child1 = base.child_field("records")
        self.assertEqual(child1.path, "data.records")
        child2 = child1.child_index(4)
        self.assertEqual(child2.path, "data.records[4]")

    def test_child_field_invalid_args(self):
        base = SourceLocation.parse("root")
        with self.assertRaises(StructuredDataLocationError):
            base.child_field("")
        with self.assertRaises(StructuredDataLocationError):
            base.child_index(-1)


class TestSourceLocationResolution(unittest.TestCase):
    """Tests for resolving paths against diverse Python payloads."""

    def setUp(self):
        self.payload = {
            "metadata": {"version": 2, "generated_at": "2026-09-05T00:00:00Z"},
            "data": {
                "items": [
                    {"id": 101, "name": "Alpha", "tags": ["red", "fast"]},
                    {"id": 102, "name": "Beta", "tags": []},
                    {"id": 103, "name": "Gamma", "active": True},
                    {"id": 104, "name": "Delta", "extra": {"code": "XYZ-99"}},
                ]
            },
            "flags": [True, False, True],
            "count": 4,
            "nullable_field": None,
        }

    def test_resolve_root(self):
        loc = SourceLocation.parse("root")
        val = loc.resolve(self.payload)
        self.assertIs(val, self.payload)

    def test_resolve_exact_user_spec_path(self):
        # Exact path from user requirement: data.items[3].name
        loc = SourceLocation.parse("data.items[3].name")
        self.assertEqual(loc.resolve(self.payload), "Delta")

    def test_resolve_nested_primitive_and_null(self):
        self.assertEqual(SourceLocation.parse("metadata.version").resolve(self.payload), 2)
        self.assertIsNone(SourceLocation.parse("nullable_field").resolve(self.payload))
        self.assertEqual(SourceLocation.parse("data.items[0].tags[1]").resolve(self.payload), "fast")
        self.assertEqual(SourceLocation.parse("data.items[3].extra.code").resolve(self.payload), "XYZ-99")

    def test_resolve_missing_dict_key_raises_location_error(self):
        loc = SourceLocation.parse("data.items[0].non_existent")
        with self.assertRaises(StructuredDataLocationError) as ctx:
            loc.resolve(self.payload)
        self.assertIn("non_existent", str(ctx.exception))

    def test_resolve_out_of_bounds_index_raises_location_error(self):
        loc = SourceLocation.parse("data.items[99].name")
        with self.assertRaises(StructuredDataLocationError) as ctx:
            loc.resolve(self.payload)
        self.assertIn("out of range", str(ctx.exception))

    def test_resolve_wrong_type_traversal_raises_location_error(self):
        loc = SourceLocation.parse("count.subfield")
        with self.assertRaises(StructuredDataLocationError) as ctx:
            loc.resolve(self.payload)
        self.assertIn("expected dict", str(ctx.exception))

    def test_exists_check(self):
        self.assertTrue(SourceLocation.parse("data.items[2].name").exists(self.payload))
        self.assertFalse(SourceLocation.parse("data.items[10].name").exists(self.payload))
        self.assertFalse(SourceLocation.parse("data.missing").exists(self.payload))


class TestStructuredSchemaAndInference(unittest.TestCase):
    """Tests for structural schema representation and deterministic inference."""

    def test_infer_from_empty_and_primitives(self):
        s_none = StructuredSchema.infer_from_value(None)
        self.assertEqual(s_none.structure_type, SchemaStructureType.EMPTY)

        s_int = StructuredSchema.infer_from_value(42)
        self.assertEqual(s_int.structure_type, SchemaStructureType.PRIMITIVE)
        self.assertEqual(s_int.fields[0].data_type, StructuredDataType.INTEGER)

        s_str = StructuredSchema.infer_from_value("hello")
        self.assertEqual(s_str.fields[0].data_type, StructuredDataType.STRING)

        s_dt = StructuredSchema.infer_from_value("2026-09-05T10:30:00Z")
        self.assertEqual(s_dt.fields[0].data_type, StructuredDataType.DATETIME)

        s_bool = StructuredSchema.infer_from_value(True)
        self.assertEqual(s_bool.fields[0].data_type, StructuredDataType.BOOLEAN)

    def test_infer_from_flat_dict(self):
        data = {"id": 1, "name": "Widget", "price": 19.99, "available": True}
        schema = StructuredSchema.infer_from_value(data)
        self.assertEqual(schema.structure_type, SchemaStructureType.OBJECT)
        self.assertEqual(len(schema.fields), 4)
        field_types = {f.path: f.data_type for f in schema.fields}
        self.assertEqual(field_types["id"], StructuredDataType.INTEGER)
        self.assertEqual(field_types["name"], StructuredDataType.STRING)
        self.assertEqual(field_types["price"], StructuredDataType.FLOAT)
        self.assertEqual(field_types["available"], StructuredDataType.BOOLEAN)

    def test_infer_from_nested_dict(self):
        data = {
            "user": {
                "id": 100,
                "profile": {"handle": "alice", "active": True},
            }
        }
        schema = StructuredSchema.infer_from_value(data)
        self.assertEqual(schema.structure_type, SchemaStructureType.OBJECT)
        self.assertIn("user", schema.field_paths)
        user_field = next(f for f in schema.fields if f.path == "user")
        self.assertEqual(user_field.data_type, StructuredDataType.OBJECT)
        self.assertIsNotNone(user_field.nested_schema)

    def test_infer_from_array_of_objects(self):
        items = [
            {"id": 1, "sku": "A1", "discount": None},
            {"id": 2, "sku": "B2", "discount": 0.15},
            {"id": 3, "sku": "C3", "notes": "special item"},
        ]
        schema = StructuredSchema.infer_from_value(items)
        self.assertEqual(schema.structure_type, SchemaStructureType.ARRAY)
        self.assertIn("[].id", schema.field_paths)
        self.assertIn("[].sku", schema.field_paths)
        self.assertIn("[].discount", schema.field_paths)
        self.assertIn("[].notes", schema.field_paths)

        # Check nullable and optional detection
        nested_fields = schema.fields[0].nested_schema.fields
        discount_f = next(f for f in nested_fields if f.path == "[].discount")
        self.assertTrue(discount_f.nullable)

        notes_f = next(f for f in nested_fields if f.path == "[].notes")
        self.assertTrue(notes_f.optional)  # missing in items 0 and 1

    def test_schema_serialization_roundtrip(self):
        data = {"count": 10, "items": [{"id": "x1"}]}
        orig = StructuredSchema.infer_from_value(data)
        serialized = orig.to_dict()
        restored = StructuredSchema.from_dict(serialized)
        self.assertEqual(restored.structure_type, orig.structure_type)
        self.assertEqual(restored.field_paths, orig.field_paths)


class TestDeterministicHashing(unittest.TestCase):
    """Tests that hashing is strictly deterministic and invariant under key ordering."""

    def test_canonical_json_dumps_sorts_keys(self):
        d1 = {"z": 1, "a": 2, "m": {"b": 3, "a": 4}}
        d2 = {"a": 2, "z": 1, "m": {"a": 4, "b": 3}}
        self.assertEqual(canonical_json_dumps(d1), canonical_json_dumps(d2))

    def test_compute_structured_hash_consistency(self):
        obj1 = {"status": "ok", "records": [{"id": 1, "name": "foo"}, {"id": 2, "name": "bar"}]}
        obj2 = {"records": [{"name": "foo", "id": 1}, {"id": 2, "name": "bar"}], "status": "ok"}
        self.assertEqual(compute_structured_hash(obj1), compute_structured_hash(obj2))
        self.assertEqual(len(compute_structured_hash(obj1)), 64)  # SHA-256 length


class TestStructuredDataSource(unittest.TestCase):
    """Tests for StructuredDataSource model validation and serialization."""

    def setUp(self):
        self.prov = _make_provenance("https://api.github.com/repos/org/repo")

    def test_valid_source_creation(self):
        src = StructuredDataSource(
            source_id="src-gh-api",
            provider="github_api",
            endpoint_url="https://api.github.com/repos/org/repo/issues",
            source_type=StructuredSourceType.REST_API,
            content_type=StructuredContentType.JSON,
            provenance=self.prov,
        )
        self.assertEqual(src.source_id, "src-gh-api")
        self.assertEqual(src.provider, "github_api")
        self.assertEqual(src.source_type, StructuredSourceType.REST_API)
        self.assertEqual(src.content_type, StructuredContentType.JSON)

    def test_source_validation_empty_fields_raise(self):
        with self.assertRaises(StructuredDataValidationError):
            StructuredDataSource(source_id="", provider="test", endpoint_url="https://example.com")

        with self.assertRaises(StructuredDataValidationError):
            StructuredDataSource(source_id="id", provider="", endpoint_url="https://example.com")

        with self.assertRaises(StructuredDataValidationError):
            StructuredDataSource(source_id="id", provider="test", endpoint_url="")

    def test_source_validation_unsupported_scheme_raises(self):
        with self.assertRaises(StructuredDataValidationError):
            StructuredDataSource(source_id="id", provider="test", endpoint_url="ftp://example.com/data")

    def test_source_serialization_roundtrip(self):
        src = StructuredDataSource(
            source_id="src-001",
            provider="census",
            endpoint_url="https://api.census.gov/data",
            source_type=StructuredSourceType.JSON_ENDPOINT,
            content_type=StructuredContentType.JSON,
            provenance=self.prov,
            metadata={"vintage": 2020},
        )
        d = src.to_dict()
        restored = StructuredDataSource.from_dict(d)
        self.assertEqual(restored.source_id, src.source_id)
        self.assertEqual(restored.endpoint_url, src.endpoint_url)
        self.assertEqual(restored.metadata["vintage"], 2020)
        self.assertIsNotNone(restored.provenance)


class TestStructuredDataRequest(unittest.TestCase):
    """Tests for StructuredDataRequest configuration, limits, and validation."""

    def test_valid_request(self):
        req = StructuredDataRequest(
            request_id="req-001",
            endpoint_url="https://api.example.com/v1/users",
            method=HttpMethod.GET,
            query_params={"limit": 50, "status": "active"},
            requested_fields=["id", "username", "email"],
            limits=StructuredDataLimits(max_records=500, timeout_seconds=15.0),
        )
        self.assertEqual(req.request_id, "req-001")
        self.assertEqual(req.method, HttpMethod.GET)
        self.assertEqual(req.limits.max_records, 500)

    def test_request_validation_empty_id_raises(self):
        with self.assertRaises(StructuredDataValidationError):
            StructuredDataRequest(request_id="", endpoint_url="https://example.com")

    def test_request_limits_validation(self):
        with self.assertRaises(StructuredDataLimitError):
            StructuredDataLimits(max_bytes=0)
        with self.assertRaises(StructuredDataLimitError):
            StructuredDataLimits(max_records=-1)
        with self.assertRaises(StructuredDataLimitError):
            StructuredDataLimits(timeout_seconds=0.0)

    def test_pagination_config_validation(self):
        with self.assertRaises(StructuredDataValidationError):
            PaginationConfig(max_pages=0)
        with self.assertRaises(StructuredDataValidationError):
            PaginationConfig(page=-1)
        with self.assertRaises(StructuredDataValidationError):
            PaginationConfig(limit=0)

    def test_request_serialization_roundtrip(self):
        req = StructuredDataRequest(
            request_id="req-roundtrip",
            endpoint_url="https://api.example.com/items",
            method=HttpMethod.POST,
            headers={"Authorization": "Bearer secret"},
            body='{"filter": "active"}',
            pagination=PaginationConfig(pagination_type=PaginationType.PAGE_NUMBER, page=1, page_size=25),
        )
        d = req.to_dict()
        restored = StructuredDataRequest.from_dict(d)
        self.assertEqual(restored.request_id, req.request_id)
        self.assertEqual(restored.method, HttpMethod.POST)
        self.assertEqual(restored.pagination.page, 1)


class TestStructuredRecord(unittest.TestCase):
    """Tests for StructuredRecord domain entity, hashing, and RawSourceReference conversion."""

    def setUp(self):
        self.prov = _make_provenance("https://api.example.com/v1/products")

    def test_record_creation_and_auto_hash(self):
        rec = StructuredRecord(
            record_id="rec-001",
            value={"id": "prod-10", "name": "Gadget", "price": 49.95},
            source_location="data.items[0]",
            provenance=self.prov,
        )
        self.assertEqual(rec.record_id, "rec-001")
        self.assertEqual(rec.source_location.path, "data.items[0]")
        self.assertNotEqual(rec.content_hash, "")
        self.assertEqual(len(rec.content_hash), 64)

    def test_record_validation(self):
        with self.assertRaises(StructuredDataValidationError):
            StructuredRecord(
                record_id="",
                value="test",
                source_location="root",
                provenance=self.prov,
            )

        with self.assertRaises(StructuredDataValidationError):
            StructuredRecord(
                record_id="rec-1",
                value="test",
                source_location="root",
                provenance=None,
            )

    def test_to_raw_source_reference_conversion(self):
        rec = StructuredRecord(
            record_id="rec-42",
            value={"title": "Structured Fact", "score": 98},
            source_location="items[42]",
            provenance=self.prov,
        )
        ref = rec.to_raw_source_reference("https://api.example.com/v1/products")
        self.assertEqual(ref.url_or_ref, "https://api.example.com/v1/products#items[42]")
        self.assertEqual(ref.source_type, SourceType.PRIMARY_SOURCE)
        self.assertEqual(ref.checksum, rec.content_hash)
        self.assertIn("Structured Fact", ref.content_snippet)

    def test_record_serialization_roundtrip(self):
        rec = StructuredRecord(
            record_id="rec-rt",
            value={"alpha": 1, "beta": 2},
            source_location="data[3]",
            provenance=self.prov,
            metadata={"extracted_by": "unit_test"},
        )
        d = rec.to_dict()
        restored = StructuredRecord.from_dict(d)
        self.assertEqual(restored.record_id, rec.record_id)
        self.assertEqual(restored.source_location.path, "data[3]")
        self.assertEqual(restored.content_hash, rec.content_hash)
        self.assertEqual(restored.metadata["extracted_by"], "unit_test")


class TestStructuredDataResponse(unittest.TestCase):
    """Tests for StructuredDataResponse, record extraction, schema inference, and status."""

    def setUp(self):
        self.prov = _make_provenance("https://api.example.com/catalog")
        self.payload = {
            "status": "success",
            "count": 3,
            "results": [
                {"id": 1, "name": "Item 1", "active": True},
                {"id": 2, "name": "Item 2", "active": False},
                {"id": 3, "name": "Item 3", "active": True},
            ],
        }

    def test_response_creation_and_auto_properties(self):
        resp = StructuredDataResponse(
            response_id="resp-001",
            request_id="req-001",
            status_code=200,
            payload=self.payload,
            provenance=self.prov,
        )
        self.assertTrue(resp.is_success)
        self.assertNotEqual(resp.raw_payload_hash, "")
        self.assertGreater(resp.response_bytes, 0)
        self.assertIsNotNone(resp.schema_info)
        self.assertEqual(resp.schema_info.structure_type, SchemaStructureType.OBJECT)

    def test_response_validation_invalid_status(self):
        with self.assertRaises(StructuredDataValidationError):
            StructuredDataResponse(
                response_id="resp-err",
                request_id="req-err",
                status_code=0,
                provenance=self.prov,
            )

    def test_extract_records_from_array_location(self):
        resp = StructuredDataResponse(
            response_id="resp-extract",
            request_id="req-extract",
            status_code=200,
            payload=self.payload,
            provenance=self.prov,
        )
        records = resp.extract_records("results")
        self.assertEqual(len(records), 3)
        self.assertEqual(records[0].source_location.path, "results[0]")
        self.assertEqual(records[0].value["id"], 1)
        self.assertEqual(records[1].source_location.path, "results[1]")
        self.assertEqual(records[2].source_location.path, "results[2]")

    def test_extract_records_from_single_object_location(self):
        resp = StructuredDataResponse(
            response_id="resp-extract-single",
            request_id="req-extract-single",
            status_code=200,
            payload=self.payload,
            provenance=self.prov,
        )
        records = resp.extract_records("results[0]")
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].source_location.path, "results[0]")
        self.assertEqual(records[0].value["id"], 1)

    def test_extract_records_missing_path_raises_location_error(self):
        resp = StructuredDataResponse(
            response_id="resp-fail",
            request_id="req-fail",
            status_code=200,
            payload=self.payload,
            provenance=self.prov,
        )
        with self.assertRaises(StructuredDataLocationError):
            resp.extract_records("non_existent_key")

    def test_response_serialization_roundtrip(self):
        resp = StructuredDataResponse(
            response_id="resp-roundtrip",
            request_id="req-roundtrip",
            status_code=200,
            status_message="OK",
            content_type="application/json; charset=utf-8",
            payload=self.payload,
            provenance=self.prov,
            pagination_info=PaginationMetadata(has_more=True, next_page=2, total_records=100),
        )
        d = resp.to_dict()
        restored = StructuredDataResponse.from_dict(d)
        self.assertEqual(restored.response_id, resp.response_id)
        self.assertEqual(restored.status_code, 200)
        self.assertEqual(restored.normalized_content_type, StructuredContentType.JSON)
        self.assertTrue(restored.pagination_info.has_more)
        self.assertEqual(restored.pagination_info.next_page, 2)


if __name__ == "__main__":
    unittest.main()
