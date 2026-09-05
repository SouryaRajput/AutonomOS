"""
Unit tests for Structured Response Parsing (Phase 1 / Part 7.4).

Tests verify that:
- JSON, JSONL, XML, and CSV formats are parsed deterministically and safely.
- Original source structure and field names are preserved without semantic mutation.
- XML External Entity (XXE) and entity expansion bombs are rejected unconditionally.
- Deterministic source paths resolve exactly against parsed payloads.
- Structural schema and content hashes are automatically extracted.
- Safety envelope limits (depth, records, fields, field sizes, bytes) and cancellation are enforced.
- Malformed inputs produce structured errors rather than false successful results.
"""
from __future__ import annotations

import json
import pytest

from core.research.contracts.evidence import EvidenceProvenance
from core.research.errors import (
    StructuredDataCancelledError,
    StructuredDataLimitError,
    StructuredDataMalformedResponseError,
)
from core.research.structured import (
    ParsedStructuredData,
    SchemaStructureType,
    SourceLocation,
    StructuredContentType,
    StructuredDataLimits,
    StructuredDataResponse,
    StructuredResponseParser,
)


def _make_provenance() -> EvidenceProvenance:
    return EvidenceProvenance(
        request_id="req-test-1",
        question_id="q-test-1",
        crawler_task_id="task-test-1",
        crawler_id="test_crawler",
        source_ref="https://api.example.com/test",
    )


# -----------------------------------------------------------------------------
# 1. JSON Parsing Tests
# -----------------------------------------------------------------------------

class TestJsonParsing:
    """Verify JSON parsing for objects, nested structures, arrays, and primitives."""

    def test_parse_simple_json_object(self):
        raw = '{"status": "ok", "count": 42, "enabled": true}'
        parsed = StructuredResponseParser.parse_content(raw, content_type="application/json")

        assert isinstance(parsed, ParsedStructuredData)
        assert parsed.normalized_content_type == StructuredContentType.JSON
        assert parsed.payload == {"status": "ok", "count": 42, "enabled": True}
        assert parsed.record_count == 1
        assert parsed.schema_info.structure_type == SchemaStructureType.OBJECT
        assert len(parsed.content_hash) == 64

    def test_parse_nested_json_object_and_source_paths(self):
        raw = """
        {
            "data": {
                "items": [
                    {"id": 101, "name": "Alpha"},
                    {"id": 102, "name": "Beta"}
                ],
                "total": 2
            }
        }
        """
        parsed = StructuredResponseParser.parse_content(raw, content_type="application/json")

        # Source paths resolution
        loc_id = SourceLocation.parse("data.items[0].id")
        loc_name = SourceLocation.parse("data.items[0].name")
        loc_total = SourceLocation.parse("data.total")

        assert loc_id.resolve(parsed.payload) == 101
        assert loc_name.resolve(parsed.payload) == "Alpha"
        assert loc_total.resolve(parsed.payload) == 2

    def test_parse_json_array_at_root(self):
        raw = '[{"id": 1}, {"id": 2}, {"id": 3}]'
        parsed = StructuredResponseParser.parse_content(raw, content_type="application/json")

        assert isinstance(parsed.payload, list)
        assert len(parsed.payload) == 3
        assert parsed.record_count == 3
        assert parsed.schema_info.structure_type == SchemaStructureType.ARRAY
        assert SourceLocation.parse("[1].id").resolve(parsed.payload) == 2

    def test_parse_json_with_unicode(self):
        raw = '{"greeting": "こんにちは", "emoji": "🚀", "accents": "résumé"}'
        parsed = StructuredResponseParser.parse_content(raw.encode("utf-8"), content_type="application/json")

        assert parsed.payload["greeting"] == "こんにちは"
        assert parsed.payload["emoji"] == "🚀"
        assert parsed.payload["accents"] == "résumé"

    def test_malformed_json_raises_malformed_error(self):
        malformed = '{"key": "value", unquoted_key: 123}'
        with pytest.raises(StructuredDataMalformedResponseError) as exc_info:
            StructuredResponseParser.parse_content(malformed, content_type="application/json")
        assert "JSON decode error" in str(exc_info.value)

    def test_empty_json_handling(self):
        # Empty string with status 204 returns None
        empty_204 = StructuredResponseParser.parse_content("", content_type="application/json", status_code=204)
        assert empty_204.payload is None

        # Empty string with status 200 raises StructuredDataMalformedResponseError
        with pytest.raises(StructuredDataMalformedResponseError) as exc:
            StructuredResponseParser.parse_content("", content_type="application/json", status_code=200)
        assert "Empty JSON payload" in str(exc.value)


# -----------------------------------------------------------------------------
# 2. JSONL / NDJSON Parsing Tests
# -----------------------------------------------------------------------------

class TestJsonlParsing:
    """Verify streaming NDJSON/JSONL parsing, blank line skipping, and line failure reporting."""

    def test_parse_valid_jsonl(self):
        raw = """
        {"id": 1, "action": "login"}
        {"id": 2, "action": "view"}

        {"id": 3, "action": "logout"}
        """
        parsed = StructuredResponseParser.parse_content(raw, content_type="application/x-ndjson")

        assert parsed.normalized_content_type == StructuredContentType.JSONL
        assert isinstance(parsed.payload, list)
        assert len(parsed.payload) == 3
        assert parsed.record_count == 3
        assert parsed.payload[0] == {"id": 1, "action": "login"}
        assert parsed.payload[2] == {"id": 3, "action": "logout"}
        assert SourceLocation.parse("[0].action").resolve(parsed.payload) == "login"

    def test_jsonl_malformed_line_reports_line_number(self):
        raw = '{"id": 1}\n{not valid json}\n{"id": 3}'
        with pytest.raises(StructuredDataMalformedResponseError) as exc_info:
            StructuredResponseParser.parse_content(raw, content_type="application/x-ndjson")
        assert "JSONL parse error on line 2" in str(exc_info.value)
        assert exc_info.value.details["line_number"] == 2


# -----------------------------------------------------------------------------
# 3. XML Parsing & Security (XXE) Tests
# -----------------------------------------------------------------------------

class TestXmlParsingAndSecurity:
    """Verify XML parsing, element hierarchy, attributes, repeated tags, and XXE prevention."""

    def test_parse_valid_xml_hierarchy(self):
        raw = """<?xml version="1.0" encoding="UTF-8"?>
        <catalog name="Main Catalog">
            <item id="101" in_stock="true">
                <name>Widget</name>
                <price>19.99</price>
            </item>
            <item id="102" in_stock="false">
                <name>Gadget</name>
                <price>29.99</price>
            </item>
        </catalog>
        """
        parsed = StructuredResponseParser.parse_content(raw, content_type="application/xml")

        assert parsed.normalized_content_type == StructuredContentType.XML
        payload = parsed.payload
        assert "catalog" in payload
        catalog = payload["catalog"]
        assert catalog["@name"] == "Main Catalog"
        assert isinstance(catalog["item"], list)
        assert len(catalog["item"]) == 2

        # Verify attributes and child elements
        item1 = catalog["item"][0]
        assert item1["@id"] == "101"
        assert item1["@in_stock"] == "true"
        assert item1["name"] == "Widget"
        assert item1["price"] == "19.99"

        # Verify source location path access
        assert SourceLocation.parse("catalog.item[0].name").resolve(payload) == "Widget"
        assert SourceLocation.parse("catalog.item[1].name").resolve(payload) == "Gadget"
        assert SourceLocation.parse('catalog.item[0]["@id"]').resolve(payload) == "101"

    def test_parse_xml_leaf_node_with_text(self):
        raw = "<response><status>OK</status><code id='code-1'>200</code></response>"
        parsed = StructuredResponseParser.parse_content(raw, content_type="application/xml")

        # Leaf without attributes is direct text
        assert parsed.payload["response"]["status"] == "OK"
        # Leaf with attribute retains @id and #text
        assert parsed.payload["response"]["code"]["@id"] == "code-1"
        assert parsed.payload["response"]["code"]["#text"] == "200"

    def test_xxe_doctype_declaration_blocked(self):
        xxe_payload = """<?xml version="1.0"?>
        <!DOCTYPE foo [
            <!ENTITY xxe SYSTEM "file:///etc/passwd">
        ]>
        <data>&xxe;</data>
        """
        with pytest.raises(StructuredDataMalformedResponseError) as exc_info:
            StructuredResponseParser.parse_content(xxe_payload, content_type="application/xml")
        assert "prohibited DTD, ENTITY, or external reference" in str(exc_info.value)

    def test_xxe_entity_bomb_blocked(self):
        bomb = """<?xml version="1.0"?>
        <!DOCTYPE lolz [
            <!ENTITY lol "lol">
            <!ENTITY lol2 "&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;">
        ]>
        <lolz>&lol2;</lolz>
        """
        with pytest.raises(StructuredDataMalformedResponseError) as exc_info:
            StructuredResponseParser.parse_content(bomb, content_type="application/xml")
        assert "prohibited DTD, ENTITY, or external reference" in str(exc_info.value)

    def test_malformed_xml_syntax_error(self):
        malformed = "<root><unclosed>text</root>"
        with pytest.raises(StructuredDataMalformedResponseError) as exc:
            StructuredResponseParser.parse_content(malformed, content_type="application/xml")
        assert "XML parse error" in str(exc.value)


# -----------------------------------------------------------------------------
# 4. CSV Parsing Tests
# -----------------------------------------------------------------------------

class TestCsvParsing:
    """Verify CSV parsing, header preservation, column disambiguation, and row mapping."""

    def test_parse_standard_csv(self):
        raw = "id,name,role,department\n1,Alice,Engineer,Core\n2,Bob,Scientist,AI\n3,Charlie,Architect,Infra\n"
        parsed = StructuredResponseParser.parse_content(raw, content_type="text/csv")

        assert parsed.normalized_content_type == StructuredContentType.CSV
        assert parsed.payload["columns"] == ["id", "name", "role", "department"]
        assert parsed.record_count == 3
        assert len(parsed.payload["rows"]) == 3

        # Row source locations
        assert SourceLocation.parse("rows[0].name").resolve(parsed.payload) == "Alice"
        assert SourceLocation.parse("rows[1].role").resolve(parsed.payload) == "Scientist"
        assert SourceLocation.parse("rows[2].department").resolve(parsed.payload) == "Infra"
        assert SourceLocation.parse("columns[0]").resolve(parsed.payload) == "id"
        assert SourceLocation.parse("total_rows").resolve(parsed.payload) == 3

    def test_parse_csv_duplicate_column_disambiguation(self):
        raw = "name,score,name\nAlpha,95,Beta\n"
        parsed = StructuredResponseParser.parse_content(raw, content_type="text/csv")

        # Second 'name' column disambiguated to 'name_2'
        assert parsed.payload["columns"] == ["name", "score", "name_2"]
        assert parsed.payload["rows"][0]["name"] == "Alpha"
        assert parsed.payload["rows"][0]["name_2"] == "Beta"

    def test_parse_tsv_or_semicolon_delimited(self):
        tsv = "id\tlabel\tactive\n1\tFirst\ttrue\n2\tSecond\tfalse\n"
        parsed = StructuredResponseParser.parse_content(tsv, content_type="text/csv")

        assert parsed.payload["columns"] == ["id", "label", "active"]
        assert len(parsed.payload["rows"]) == 2
        assert parsed.payload["rows"][0]["label"] == "First"

    def test_empty_csv_handling(self):
        parsed = StructuredResponseParser.parse_content("", content_type="text/csv")
        assert parsed.payload["columns"] == []
        assert parsed.payload["rows"] == []
        assert parsed.payload["total_rows"] == 0


# -----------------------------------------------------------------------------
# 5. Safety Envelope & Resource Limits Tests
# -----------------------------------------------------------------------------

class TestSafetyEnvelopeAndLimits:
    """Verify maximum nesting depth, records, fields, field sizes, and cancellation."""

    def test_max_depth_exceeded_raises_limit_error(self):
        # Construct deeply nested JSON beyond limit
        nested = {"level1": {"level2": {"level3": {"level4": {"level5": "deep"}}}}}
        limits = StructuredDataLimits(max_depth=3)

        with pytest.raises(StructuredDataLimitError) as exc_info:
            StructuredResponseParser.parse_content(nested, limits=limits)
        assert exc_info.value.limit_name == "max_depth"
        assert exc_info.value.actual_value > 3

    def test_max_records_exceeded_raises_limit_error(self):
        raw = json.dumps([{"id": i} for i in range(10)])
        limits = StructuredDataLimits(max_records=5)

        with pytest.raises(StructuredDataLimitError) as exc:
            StructuredResponseParser.parse_content(raw, content_type="application/json", limits=limits)
        assert exc.value.limit_name == "max_records"

    def test_max_fields_exceeded_raises_limit_error(self):
        # Object with 20 fields when limit is 10
        raw = json.dumps({f"k_{i}": i for i in range(20)})
        limits = StructuredDataLimits(max_fields=10)

        with pytest.raises(StructuredDataLimitError) as exc:
            StructuredResponseParser.parse_content(raw, content_type="application/json", limits=limits)
        assert exc.value.limit_name == "max_fields"

    def test_max_field_size_exceeded_raises_limit_error(self):
        large_val = "x" * 1000
        raw = json.dumps({"key": large_val})
        limits = StructuredDataLimits(max_field_size=500)

        with pytest.raises(StructuredDataLimitError) as exc:
            StructuredResponseParser.parse_content(raw, content_type="application/json", limits=limits)
        assert exc.value.limit_name == "max_field_size"

    def test_max_bytes_exceeded_raises_limit_error(self):
        raw = "a" * 5000
        limits = StructuredDataLimits(max_bytes=1000)

        with pytest.raises(StructuredDataLimitError) as exc:
            StructuredResponseParser.parse_content(raw, content_type="application/json", limits=limits)
        assert exc.value.limit_name == "max_bytes"

    def test_cancellation_check_aborts_parsing(self):
        raw = '{"id": 1, "items": [1, 2, 3]}'
        cancelled = True

        with pytest.raises(StructuredDataCancelledError):
            StructuredResponseParser.parse_content(
                raw,
                content_type="application/json",
                is_cancelled=lambda: cancelled,
            )


# -----------------------------------------------------------------------------
# 6. StructuredDataResponse Integration & Record Extraction Tests
# -----------------------------------------------------------------------------

class TestResponseIntegrationAndExtraction:
    """Verify integration with StructuredDataResponse and StructuredRecord extraction."""

    def test_parse_response_object(self):
        prov = _make_provenance()
        resp = StructuredDataResponse(
            response_id="resp-001",
            request_id="req-001",
            status_code=200,
            provenance=prov,
            content_type="application/json",
            payload='{"user": {"id": 99, "role": "admin"}}',
        )

        parsed_resp = StructuredResponseParser.parse_response(resp)
        assert isinstance(parsed_resp, StructuredDataResponse)
        assert parsed_resp.payload == {"user": {"id": 99, "role": "admin"}}
        assert parsed_resp.schema_info is not None
        assert len(parsed_resp.raw_payload_hash) == 64

        # Extract record at 'user'
        records = parsed_resp.extract_records("user")
        assert len(records) == 1
        rec = records[0]
        assert rec.value == {"id": 99, "role": "admin"}
        assert rec.source_location.path == "user"
        assert rec.provenance.crawler_id == "test_crawler"

        # Check raw source reference conversion
        raw_ref = rec.to_raw_source_reference(endpoint_url="https://api.example.com/user")
        assert raw_ref.url_or_ref == "https://api.example.com/user#user"
        assert raw_ref.checksum == rec.content_hash

    def test_extract_records_from_parsed_csv(self):
        prov = _make_provenance()
        csv_data = "sku,title,qty\nSKU-1,Widget,10\nSKU-2,Gadget,20\n"
        resp = StructuredDataResponse(
            response_id="resp-csv-002",
            request_id="req-002",
            status_code=200,
            provenance=prov,
            content_type="text/csv",
            payload=csv_data,
        )

        parsed_resp = StructuredResponseParser.parse_response(resp)
        assert parsed_resp.normalized_content_type == StructuredContentType.CSV

        # Extract rows
        records = parsed_resp.extract_records("rows")
        assert len(records) == 2
        assert records[0].source_location.path == "rows[0]"
        assert records[0].value == {"sku": "SKU-1", "title": "Widget", "qty": "10"}
        assert records[1].source_location.path == "rows[1]"
        assert records[1].value == {"sku": "SKU-2", "title": "Gadget", "qty": "20"}

    def test_no_semantic_mutation_invariant(self):
        """Ensure field names like created_at, price, stars are NEVER modified."""
        raw = '{"created_at": "2026-09-05", "price": 49.99, "stars": 5}'
        parsed = StructuredResponseParser.parse_content(raw, content_type="application/json")

        assert "created_at" in parsed.payload
        assert "publication_date" not in parsed.payload
        assert "price" in parsed.payload
        assert "monetary_value" not in parsed.payload
        assert "stars" in parsed.payload
        assert "popularity" not in parsed.payload

    def test_xml_namespace_stripping_and_repeated_elements(self):
        xml_with_ns = """<?xml version="1.0" encoding="UTF-8"?>
        <feed xmlns="http://www.w3.org/2005/Atom">
            <title>Project Updates</title>
            <entry>
                <id>tag:1</id>
                <title>First Entry</title>
            </entry>
            <entry>
                <id>tag:2</id>
                <title>Second Entry</title>
            </entry>
        </feed>
        """
        parsed = StructuredResponseParser.parse_content(xml_with_ns, content_type="application/atom+xml")
        assert "feed" in parsed.payload
        feed = parsed.payload["feed"]
        assert feed["title"] == "Project Updates"
        assert isinstance(feed["entry"], list)
        assert len(feed["entry"]) == 2
        assert feed["entry"][0]["id"] == "tag:1"
        assert SourceLocation.parse("feed.entry[0].title").resolve(parsed.payload) == "First Entry"

    def test_parse_already_parsed_dict(self):
        dict_payload = {"key": "value", "items": [1, 2]}
        parsed = StructuredResponseParser.parse_content(dict_payload)
        assert parsed.payload == dict_payload
        assert parsed.normalized_content_type == StructuredContentType.JSON
        assert parsed.record_count == 2

    def test_detect_content_type(self):
        assert StructuredResponseParser.detect_content_type("<root><a/></root>") == StructuredContentType.XML
        assert StructuredResponseParser.detect_content_type('{"key": 1}') == StructuredContentType.JSON
        assert StructuredResponseParser.detect_content_type('{"id": 1}\n{"id": 2}') == StructuredContentType.JSONL
        assert StructuredResponseParser.detect_content_type("a,b,c\n1,2,3") == StructuredContentType.CSV

    def test_invalid_byte_sequence_handling(self):
        # Corrupt byte payload that cannot be decoded as utf-8
        corrupt_bytes = b"\xff\xfe\xff\xff\x80\x81"
        with pytest.raises(StructuredDataMalformedResponseError):
            StructuredResponseParser.parse_content(corrupt_bytes, encoding="utf-8")

        # Unknown/invalid encoding
        with pytest.raises(StructuredDataMalformedResponseError):
            StructuredResponseParser.parse_content(b"hello", encoding="non_existent_encoding_xyz")
