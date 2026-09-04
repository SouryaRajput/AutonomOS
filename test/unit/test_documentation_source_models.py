"""
Unit tests for Documentation Source and Structural Domain Models (Phase 1 / Part 4 / Step 1).

Verifies:
1. DocumentationSource creation
2. DocumentationPage creation
3. DocumentationSection creation
4. Documentation hierarchy (Source -> Section -> Page -> Subsections)
5. Parent/child relationships
6. Page navigation relationships (prev, next, children, breadcrumbs)
7. Version context (latest, stable, explicit, preview, unknown)
8. Language context (known vs None)
9. Unknown metadata remains None/unknown without guessing
10. Provenance preservation (EvidenceProvenance attached across models)
11. Serialization/deserialization symmetry (to_dict / from_dict)
12. Malformed/invalid input validation
13. Isolation between documentation sources (no shared mutable state)
14. Compatibility with existing CrawlerReport structures (RawSourceReference, EvidenceItem)
15. Reuse of existing provenance and source type models without duplication
"""
from __future__ import annotations

import unittest

from core.research.contracts.crawler_report import CrawlerReport, RawSourceReference
from core.research.contracts.evidence import EvidenceItem, EvidenceProvenance
from core.research.docs.models import (
    DocVersionContext,
    DocumentationPage,
    DocumentationSection,
    DocumentationSource,
    PageNavigation,
    VersionCategory,
)
from core.research.errors import DocumentationValidationError
from core.research.types import CrawlerCapability, CrawlerReportStatus, FactClassification, SourceType


class TestDocumentationSourceModels(unittest.TestCase):

    def setUp(self):
        self.prov = EvidenceProvenance(
            request_id="req-doc-01",
            crawler_task_id="ctask-doc-01",
            crawler_id="crawler.doc.01",
            question_id="q-doc-01",
            source_ref="https://docs.python.org/3/",
            correlation_id="corr-doc-999",
        )

    # -------------------------------------------------------------
    # 1. DocumentationSource Creation
    # -------------------------------------------------------------

    def test_01_documentation_source_creation(self):
        source = DocumentationSource(
            source_id="docsrc-python",
            root_url="https://docs.python.org/3/",
            canonical_url="https://docs.python.org/3/",
            title="Python 3 Documentation",
            source_type=SourceType.OFFICIAL_DOCUMENTATION,
            version_context=DocVersionContext.stable("3.12"),
            language="en",
            discovery_metadata={"sitemap": "https://docs.python.org/3/sitemap.xml"},
            provenance=self.prov,
        )

        self.assertEqual(source.source_id, "docsrc-python")
        self.assertEqual(source.root_url, "https://docs.python.org/3/")
        self.assertEqual(source.canonical_url, "https://docs.python.org/3/")
        self.assertEqual(source.title, "Python 3 Documentation")
        self.assertEqual(source.source_type, SourceType.OFFICIAL_DOCUMENTATION)
        self.assertEqual(source.version_context.category, VersionCategory.STABLE)
        self.assertEqual(source.version_context.version_string, "3.12")
        self.assertEqual(source.language, "en")
        self.assertEqual(source.total_pages_count, 0)
        self.assertEqual(source.provenance.request_id, "req-doc-01")

    # -------------------------------------------------------------
    # 2. DocumentationPage Creation
    # -------------------------------------------------------------

    def test_02_documentation_page_creation(self):
        page = DocumentationPage(
            page_id="docpage-asyncio",
            url="https://docs.python.org/3/library/asyncio.html",
            final_url="https://docs.python.org/3/library/asyncio.html",
            canonical_url="https://docs.python.org/3/library/asyncio.html",
            title="asyncio — Asynchronous I/O",
            language="en",
            version_context=DocVersionContext.stable("3.12"),
            clean_text="asyncio is a library to write concurrent code using the async/await syntax.",
            bytes_fetched=45000,
            provenance=self.prov,
        )

        self.assertEqual(page.page_id, "docpage-asyncio")
        self.assertEqual(page.url, "https://docs.python.org/3/library/asyncio.html")
        self.assertEqual(page.title, "asyncio — Asynchronous I/O")
        self.assertTrue(page.content_checksum)
        self.assertEqual(page.bytes_fetched, 45000)
        self.assertEqual(len(page.sections), 0)

    # -------------------------------------------------------------
    # 3. DocumentationSection Creation
    # -------------------------------------------------------------

    def test_03_documentation_section_creation(self):
        sec = DocumentationSection(
            section_id="sec-runners",
            title="Event Loop Runners",
            level=2,
            order_index=1,
            content="asyncio.run(coro, *, debug=False) executes the coroutine coro and returns the result.",
            anchor="#asyncio.run",
            links=[{"url": "https://docs.python.org/3/library/asyncio-runner.html", "text": "Runner details"}],
            provenance=self.prov,
        )

        self.assertEqual(sec.section_id, "sec-runners")
        self.assertEqual(sec.title, "Event Loop Runners")
        self.assertEqual(sec.level, 2)
        self.assertEqual(sec.order_index, 1)
        self.assertEqual(sec.anchor, "#asyncio.run")
        self.assertEqual(len(sec.links), 1)
        self.assertEqual(len(sec.subsections), 0)

    # -------------------------------------------------------------
    # 4. Hierarchy: Source -> Section -> Page -> Subsections
    # -------------------------------------------------------------

    def test_04_documentation_hierarchy(self):
        source = DocumentationSource(
            source_id="docsrc-v8",
            root_url="https://v8.dev/docs",
            title="V8 Docs",
        )

        page = DocumentationPage(
            page_id="docpage-simd",
            url="https://v8.dev/features/simd",
            title="SIMD in V8",
        )

        h1_sec = DocumentationSection(
            section_id="sec-h1",
            title="SIMD Overview",
            level=1,
            order_index=0,
            content="Top level SIMD introduction",
        )

        h2_sec = DocumentationSection(
            section_id="sec-h2",
            title="128-bit Vector Ops",
            level=2,
            order_index=1,
            content="Detailed instructions for 128-bit vectors",
        )

        h3_sec = DocumentationSection(
            section_id="sec-h3",
            title="f32x4 Arithmetic",
            level=3,
            order_index=2,
            content="Floating point operations",
        )

        h2_sec.add_subsection(h3_sec)
        h1_sec.add_subsection(h2_sec)
        page.add_section(h1_sec)
        source.add_page(page)

        self.assertEqual(source.total_pages_count, 1)
        retrieved_page = source.get_page_by_url("https://v8.dev/features/simd")
        self.assertIsNotNone(retrieved_page)
        self.assertEqual(retrieved_page.page_id, "docpage-simd")

        all_secs = retrieved_page.get_all_sections()
        self.assertEqual(len(all_secs), 3)
        self.assertEqual([s.title for s in all_secs], ["SIMD Overview", "128-bit Vector Ops", "f32x4 Arithmetic"])

    # -------------------------------------------------------------
    # 5. Parent / Child Relationships
    # -------------------------------------------------------------

    def test_05_parent_child_relationships(self):
        parent_page = DocumentationPage(
            page_id="docpage-parent",
            url="https://docs.example.com/guide",
            title="User Guide",
        )

        child_page = DocumentationPage(
            page_id="docpage-child",
            url="https://docs.example.com/guide/quickstart",
            title="Quickstart",
            parent_page_id="docpage-parent",
        )

        self.assertEqual(child_page.parent_page_id, "docpage-parent")
        self.assertIsNone(parent_page.parent_page_id)

    # -------------------------------------------------------------
    # 6. Page Navigation Relationships
    # -------------------------------------------------------------

    def test_06_page_navigation_relationships(self):
        nav = PageNavigation(
            parent_section="Getting Started",
            previous_page_url="https://docs.example.com/intro",
            next_page_url="https://docs.example.com/advanced",
            child_page_urls=["https://docs.example.com/guide/sub1", "https://docs.example.com/guide/sub2"],
            breadcrumbs=["Home", "Documentation", "User Guide"],
            table_of_contents=["Installation", "Configuration", "Troubleshooting"],
        )

        page = DocumentationPage(
            page_id="docpage-nav",
            url="https://docs.example.com/guide",
            navigation=nav,
        )

        self.assertEqual(page.navigation.parent_section, "Getting Started")
        self.assertEqual(page.navigation.previous_page_url, "https://docs.example.com/intro")
        self.assertEqual(page.navigation.next_page_url, "https://docs.example.com/advanced")
        self.assertEqual(len(page.navigation.child_page_urls), 2)
        self.assertEqual(page.navigation.breadcrumbs, ["Home", "Documentation", "User Guide"])

    # -------------------------------------------------------------
    # 7. Version Context
    # -------------------------------------------------------------

    def test_07_version_context_variants(self):
        # 1. Unknown
        v_unk = DocVersionContext.unknown()
        self.assertEqual(v_unk.category, VersionCategory.UNKNOWN)
        self.assertIsNone(v_unk.version_string)

        # 2. Latest
        v_latest = DocVersionContext.latest()
        self.assertEqual(v_latest.category, VersionCategory.LATEST)
        self.assertEqual(v_latest.version_string, "latest")
        self.assertTrue(v_latest.is_default)

        # 3. Stable
        v_stable = DocVersionContext.stable("v1.8.0")
        self.assertEqual(v_stable.category, VersionCategory.STABLE)
        self.assertEqual(v_stable.version_string, "v1.8.0")

        # 4. Explicit
        v_exp = DocVersionContext.explicit("v3.12.2")
        self.assertEqual(v_exp.category, VersionCategory.EXPLICIT)
        self.assertEqual(v_exp.version_string, "v3.12.2")

        # 5. Preview
        v_prev = DocVersionContext.preview("2.0-canary")
        self.assertEqual(v_prev.category, VersionCategory.PREVIEW)
        self.assertEqual(v_prev.version_string, "2.0-canary")

    # -------------------------------------------------------------
    # 8. Language Context
    # -------------------------------------------------------------

    def test_08_language_context(self):
        page_en = DocumentationPage(page_id="p1", url="https://docs.org/en/guide", language="en")
        page_ja = DocumentationPage(page_id="p2", url="https://docs.org/ja/guide", language="ja")
        page_unk = DocumentationPage(page_id="p3", url="https://docs.org/guide", language=None)

        self.assertEqual(page_en.language, "en")
        self.assertEqual(page_ja.language, "ja")
        self.assertIsNone(page_unk.language)

    # -------------------------------------------------------------
    # 9. Unknown Metadata Remains None
    # -------------------------------------------------------------

    def test_09_unknown_metadata_remains_none(self):
        source = DocumentationSource(source_id="src-blank", root_url="https://example.com/docs")
        self.assertIsNone(source.canonical_url)
        self.assertIsNone(source.language)
        self.assertEqual(source.version_context.category, VersionCategory.UNKNOWN)
        self.assertIsNone(source.version_context.version_string)
        self.assertIsNone(source.provenance)

    # -------------------------------------------------------------
    # 10. Provenance Preservation
    # -------------------------------------------------------------

    def test_10_provenance_preservation(self):
        page = DocumentationPage(
            page_id="page-prov-1",
            url="https://docs.example.org/arch",
            title="Architecture",
            clean_text="Clean architectural design overview.",
            provenance=self.prov,
        )

        self.assertEqual(page.provenance.request_id, "req-doc-01")
        self.assertEqual(page.provenance.crawler_task_id, "ctask-doc-01")
        self.assertEqual(page.provenance.crawler_id, "crawler.doc.01")
        self.assertEqual(page.provenance.question_id, "q-doc-01")
        self.assertEqual(page.provenance.correlation_id, "corr-doc-999")

        # Check conversion to EvidenceItem
        ev_items = page.to_evidence_items(
            request_id="req-doc-01",
            crawler_task_id="ctask-doc-01",
            crawler_id="crawler.doc.01",
            question_id="q-doc-01",
        )
        self.assertEqual(len(ev_items), 1)
        ev = ev_items[0]
        self.assertEqual(ev.provenance.request_id, "req-doc-01")
        self.assertEqual(ev.provenance.source_ref, "https://docs.example.org/arch")
        self.assertEqual(ev.classification, FactClassification.SOURCE_CLAIM)

    # -------------------------------------------------------------
    # 11. Serialization / Deserialization Symmetry
    # -------------------------------------------------------------

    def test_11_serialization_symmetry(self):
        source = DocumentationSource(
            source_id="docsrc-full",
            root_url="https://fastapi.tiangolo.com/",
            canonical_url="https://fastapi.tiangolo.com/",
            title="FastAPI Documentation",
            source_type=SourceType.OFFICIAL_DOCUMENTATION,
            version_context=DocVersionContext.stable("0.110.0"),
            language="en",
            discovery_metadata={"entrypoint": "https://fastapi.tiangolo.com/"},
            provenance=self.prov,
        )

        page = DocumentationPage(
            page_id="page-tutorial",
            url="https://fastapi.tiangolo.com/tutorial/",
            title="Tutorial - User Guide",
            clean_text="FastAPI tutorial content.",
            navigation=PageNavigation(parent_section="Tutorial", next_page_url="https://fastapi.tiangolo.com/tutorial/first-steps/"),
            provenance=self.prov,
        )

        sec = DocumentationSection(
            section_id="sec-first-steps",
            title="First Steps",
            level=2,
            order_index=1,
            content="from fastapi import FastAPI\napp = FastAPI()",
            anchor="#first-steps",
            provenance=self.prov,
        )

        page.add_section(sec)
        source.add_page(page)

        # 1. Page serialization
        page_dict = page.to_dict()
        page_restored = DocumentationPage.from_dict(page_dict)
        self.assertEqual(page.page_id, page_restored.page_id)
        self.assertEqual(page.url, page_restored.url)
        self.assertEqual(page.title, page_restored.title)
        self.assertEqual(len(page_restored.sections), 1)
        self.assertEqual(page_restored.sections[0].title, "First Steps")

        # 2. Source serialization
        source_dict = source.to_dict()
        source_restored = DocumentationSource.from_dict(source_dict)
        self.assertEqual(source.source_id, source_restored.source_id)
        self.assertEqual(source.root_url, source_restored.root_url)
        self.assertEqual(source_restored.total_pages_count, 1)
        self.assertEqual(source_restored.pages[0].page_id, "page-tutorial")

    # -------------------------------------------------------------
    # 12. Malformed / Invalid Model Input Validation
    # -------------------------------------------------------------

    def test_12_malformed_input_validation(self):
        # Empty source_id or root_url
        with self.assertRaises(DocumentationValidationError):
            DocumentationSource(source_id="", root_url="https://docs.org")
        with self.assertRaises(DocumentationValidationError):
            DocumentationSource(source_id="src-1", root_url="")

        # Empty page_id or url
        with self.assertRaises(DocumentationValidationError):
            DocumentationPage(page_id="", url="https://docs.org/p1")
        with self.assertRaises(DocumentationValidationError):
            DocumentationPage(page_id="p1", url="   ")

        # Invalid section heading level (< 1 or > 6)
        with self.assertRaises(DocumentationValidationError):
            DocumentationSection(section_id="s1", title="Heading", level=0)
        with self.assertRaises(DocumentationValidationError):
            DocumentationSection(section_id="s1", title="Heading", level=7)

        # Empty explicit version string
        with self.assertRaises(DocumentationValidationError):
            DocVersionContext.explicit("")

    # -------------------------------------------------------------
    # 13. Isolation Between Documentation Sources
    # -------------------------------------------------------------

    def test_13_isolation_between_sources_no_shared_state(self):
        src1 = DocumentationSource(source_id="src-1", root_url="https://docs1.org")
        src2 = DocumentationSource(source_id="src-2", root_url="https://docs2.org")

        p1 = DocumentationPage(page_id="p1", url="https://docs1.org/p1")
        src1.add_page(p1)

        self.assertEqual(src1.total_pages_count, 1)
        self.assertEqual(src2.total_pages_count, 0)
        self.assertIsNot(src1.pages, src2.pages)

    # -------------------------------------------------------------
    # 14. Compatibility with Existing CrawlerReport Structures
    # -------------------------------------------------------------

    def test_14_crawler_report_compatibility(self):
        page = DocumentationPage(
            page_id="page-report-compat",
            url="https://rust-lang.github.io/async-book/",
            canonical_url="https://rust-lang.github.io/async-book/",
            title="Asynchronous Programming in Rust",
            clean_text="Async Rust enables high-performance concurrent systems with zero-cost futures.",
            bytes_fetched=32000,
        )

        raw_source = page.to_raw_source_reference()
        self.assertIsInstance(raw_source, RawSourceReference)
        self.assertEqual(raw_source.url_or_ref, "https://rust-lang.github.io/async-book/")
        self.assertEqual(raw_source.source_type, SourceType.OFFICIAL_DOCUMENTATION)
        self.assertEqual(raw_source.bytes_fetched, 32000)

        ev_items = page.to_evidence_items(
            request_id="req-rust-01",
            crawler_task_id="ctask-rust-01",
            crawler_id="crawler.doc.01",
        )
        self.assertTrue(len(ev_items) >= 1)
        self.assertIsInstance(ev_items[0], EvidenceItem)

        # Package into a standard CrawlerReport
        report = CrawlerReport(
            report_id="crep-doc-rust-01",
            crawler_task_id="ctask-rust-01",
            crawler_id="crawler.doc.01",
            request_id="req-rust-01",
            status=CrawlerReportStatus.SUCCESS,
            raw_sources=[raw_source],
            extracted_evidence=ev_items,
            summary="Fetched documentation page successfully.",
        )

        self.assertEqual(report.status, CrawlerReportStatus.SUCCESS)
        self.assertEqual(len(report.raw_sources), 1)
        self.assertEqual(report.raw_sources[0].source_type, SourceType.OFFICIAL_DOCUMENTATION)

    # -------------------------------------------------------------
    # 15. Reuse of Existing Provenance and SourceType Concepts
    # -------------------------------------------------------------

    def test_15_no_accidental_duplication_of_provenance(self):
        # Verify EvidenceProvenance from core.research.contracts.evidence is directly used
        prov = EvidenceProvenance(
            request_id="req-root",
            crawler_task_id="ctask-root",
            crawler_id="crawler-root",
        )
        page = DocumentationPage(
            page_id="page-prov-reuse",
            url="https://example.com/docs",
            provenance=prov,
        )
        self.assertIs(page.provenance, prov)
        self.assertEqual(page.provenance.request_id, "req-root")


if __name__ == "__main__":
    unittest.main()
