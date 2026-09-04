"""
Unit tests for Web Fetch Crawler HTML Extraction and Metadata (Phase 1 / Part 3 / Step 4).

Verifies:
1. Normal HTML parsing and structure preservation (headings, paragraphs, lists, tables, links, code)
2. Malformed HTML resilience (unclosed tags, broken attributes)
3. Non-content stripping (script, style, noscript, nav, footer, form, svg, aside)
4. Comprehensive metadata extraction (title, description, author, language, published/modified date, OpenGraph)
5. Missing metadata remains None/unknown (no hallucination or inference)
6. Canonical URL extraction and distinct 3-URL preservation (requested, final, canonical)
7. Link extraction as structured metadata without crawling
8. SHA-256 content hashing and provenance preservation
9. Retrieval timestamp propagation
10. Empty HTML and non-HTML / unsupported content type handling
"""
from __future__ import annotations

import hashlib
import unittest

from core.research.contracts.crawler_task import CrawlerTask
from core.research.crawler.web_fetch import WebFetchCrawler
from core.research.fetch.config import FetchConfig
from core.research.fetch.extraction import ExtractedDocument, HtmlExtractor
from core.research.fetch.test_provider import TestFetchProvider
from core.research.types import CrawlerCapability, CrawlerReportStatus, FactClassification


class TestWebFetchHtmlExtraction(unittest.TestCase):

    def setUp(self):
        self.config = FetchConfig(provider_type="test", default_timeout_seconds=5.0)
        self.test_provider = TestFetchProvider(config=self.config)
        self.crawler = WebFetchCrawler(
            crawler_id="crawler.web_fetch.extractor_01",
            provider=self.test_provider,
            config=self.config,
        )

    # -------------------------------------------------------------
    # 1. Structure Preservation & Normal HTML
    # -------------------------------------------------------------

    def test_01_normal_html_structure_preservation(self):
        html = """
        <!DOCTYPE html>
        <html lang="en">
        <head>
            <title>WebAssembly SIMD Guide</title>
        </head>
        <body>
            <h1>WebAssembly SIMD</h1>
            <p>WebAssembly SIMD provides 128-bit vector operations for high-performance computing.</p>
            
            <h2>Key Operations</h2>
            <ul>
                <li>i32x4 arithmetic</li>
                <li>f32x4 matrix transforms</li>
                <li>v128 bitwise masks</li>
            </ul>

            <h2>Ordered Steps</h2>
            <ol>
                <li>Enable feature flag</li>
                <li>Compile with clang -msimd128</li>
                <li>Benchmark performance</li>
            </ol>

            <blockquote>Vectorization can yield a 2x to 4x speedup on modern CPUs.</blockquote>

            <pre><code>const result = wasmSimdFunc(buffer);</code></pre>

            <table>
                <thead>
                    <tr><th>Instruction</th><th>Throughput</th><th>Latency</th></tr>
                </thead>
                <tbody>
                    <tr><td>i32x4.add</td><td>1 cycle</td><td>1 cycle</td></tr>
                    <tr><td>f32x4.mul</td><td>1 cycle</td><td>3 cycles</td></tr>
                </tbody>
            </table>

            <p>Visit the <a href="https://v8.dev/docs">official docs</a> for further details.</p>
        </body>
        </html>
        """
        extracted = HtmlExtractor.extract(html, base_url="https://v8.dev/simd")

        self.assertEqual(extracted.title, "WebAssembly SIMD Guide")
        self.assertEqual(extracted.language, "en")
        self.assertIn("# WebAssembly SIMD", extracted.clean_text)
        self.assertIn("## Key Operations", extracted.clean_text)
        self.assertIn("* i32x4 arithmetic", extracted.clean_text)
        self.assertIn("1. Enable feature flag", extracted.clean_text)
        self.assertIn("> Vectorization can yield", extracted.clean_text)
        self.assertIn("```\nconst result = wasmSimdFunc(buffer);\n```", extracted.clean_text)
        self.assertIn("| Instruction | Throughput | Latency |", extracted.clean_text)
        self.assertIn("| i32x4.add | 1 cycle | 1 cycle |", extracted.clean_text)
        self.assertIn("[official docs](https://v8.dev/docs)", extracted.clean_text)

    # -------------------------------------------------------------
    # 2. Malformed HTML Handling
    # -------------------------------------------------------------

    def test_02_malformed_html_graceful_recovery(self):
        malformed_html = """
        <html>
        <head><title>Malformed Document
        <body>
        <h1>Unclosed Heading
        <p>First paragraph with broken <b attribute="broken>bold text</p>
        <div>Stray angle brackets < and > and >> in content</div>
        <ul><li>Item 1<li>Item 2 without closing tags
        <table><tr><td>Cell 1<td>Cell 2</tr>
        """
        extracted = HtmlExtractor.extract(malformed_html, base_url="https://example.com/broken")

        self.assertIn("Malformed Document", extracted.title or "")
        self.assertIn("Unclosed Heading", extracted.clean_text)
        self.assertIn("First paragraph", extracted.clean_text)
        self.assertIn("Item 1", extracted.clean_text)
        self.assertIn("Item 2", extracted.clean_text)
        self.assertIn("Cell 1", extracted.clean_text)

    # -------------------------------------------------------------
    # 3. Non-Content Stripping
    # -------------------------------------------------------------

    def test_03_strips_scripts_styles_and_boilerplate(self):
        html = """
        <html>
        <head>
            <style>body { font-size: 14px; } .hidden { display: none; }</style>
            <script>console.log("tracking pixel loaded"); window.__STATE__ = { token: "secret" };</script>
        </head>
        <body>
            <header><div class="logo">Company Logo</div></header>
            <nav><a href="/home">Home</a> | <a href="/about">About</a></nav>
            <aside><h3>Sidebar Ads</h3><p>Buy widgets now!</p></aside>
            <form action="/login"><input type="text" name="user" /><button>Submit</button></form>
            <noscript><p>JavaScript is disabled</p></noscript>
            <svg><path d="M10 10" /></svg>

            <main>
                <h1>Real Article Content</h1>
                <p>This is the actual informational text that must be extracted.</p>
            </main>

            <footer><p>Copyright 2026 Corporation. All rights reserved.</p></footer>
        </body>
        </html>
        """
        extracted = HtmlExtractor.extract(html, base_url="https://example.com/article")

        # Clean text must contain core content
        self.assertIn("Real Article Content", extracted.clean_text)
        self.assertIn("This is the actual informational text", extracted.clean_text)

        # Clean text must NOT contain stripped boilerplate
        self.assertNotIn("console.log", extracted.clean_text)
        self.assertNotIn("font-size: 14px", extracted.clean_text)
        self.assertNotIn("tracking pixel", extracted.clean_text)
        self.assertNotIn("Sidebar Ads", extracted.clean_text)
        self.assertNotIn("JavaScript is disabled", extracted.clean_text)
        self.assertNotIn("Copyright 2026 Corporation", extracted.clean_text)
        self.assertNotIn("Company Logo", extracted.clean_text)

    # -------------------------------------------------------------
    # 4. Metadata Extraction
    # -------------------------------------------------------------

    def test_04_comprehensive_metadata_extraction(self):
        html = """
        <!DOCTYPE html>
        <html lang="en-US">
        <head>
            <title>Architecture of High-Throughput Engines</title>
            <meta name="description" content="An in-depth analysis of high-throughput vector processing engines.">
            <meta name="author" content="Dr. Jane Doe">
            <meta property="article:published_time" content="2026-05-14T08:30:00Z">
            <meta property="article:modified_time" content="2026-05-16T12:00:00Z">
            <meta property="og:site_name" content="Tech Journal">
            <meta property="og:title" content="High-Throughput Vector Engines">
            <meta property="og:description" content="OG description text">
            <meta property="og:image" content="https://techjournal.org/cover.png">
            <meta property="og:type" content="article">
            <link rel="canonical" href="https://techjournal.org/papers/vector-engines">
        </head>
        <body>
            <p>Article body content.</p>
        </body>
        </html>
        """
        extracted = HtmlExtractor.extract(html, base_url="https://techjournal.org/papers/vector-engines?ref=newsletter")

        self.assertEqual(extracted.title, "Architecture of High-Throughput Engines")
        self.assertEqual(extracted.description, "An in-depth analysis of high-throughput vector processing engines.")
        self.assertEqual(extracted.author, "Dr. Jane Doe")
        self.assertEqual(extracted.language, "en-US")
        self.assertEqual(extracted.published_date, "2026-05-14T08:30:00Z")
        self.assertEqual(extracted.modified_date, "2026-05-16T12:00:00Z")
        self.assertEqual(extracted.canonical_url, "https://techjournal.org/papers/vector-engines")
        self.assertEqual(extracted.opengraph.get("og:site_name"), "Tech Journal")
        self.assertEqual(extracted.opengraph.get("og:image"), "https://techjournal.org/cover.png")
        self.assertEqual(extracted.opengraph.get("og:type"), "article")

    # -------------------------------------------------------------
    # 5. Missing Metadata Remains None (No Guessing)
    # -------------------------------------------------------------

    def test_05_missing_metadata_remains_none(self):
        html = """
        <html>
        <body>
            <p>Plain un-annotated document with no meta tags.</p>
        </body>
        </html>
        """
        extracted = HtmlExtractor.extract(html, base_url="https://example.com/raw")

        self.assertIsNone(extracted.title)
        self.assertIsNone(extracted.description)
        self.assertIsNone(extracted.author)
        self.assertIsNone(extracted.language)
        self.assertIsNone(extracted.published_date)
        self.assertIsNone(extracted.modified_date)
        self.assertIsNone(extracted.canonical_url)
        self.assertEqual(extracted.opengraph, {})

    # -------------------------------------------------------------
    # 6. Canonical URL & 3-URL Preservation
    # -------------------------------------------------------------

    def test_06_canonical_url_resolution_and_preservation(self):
        requested_url = "http://example.com/post-alias"
        final_url = "https://example.com/posts/item-42"
        canonical_href = "/articles/2026/item-42-canonical"
        expected_canonical = "https://example.com/articles/2026/item-42-canonical"

        html = f"""
        <html>
        <head>
            <title>Canonical URL Test</title>
            <link rel="canonical" href="{canonical_href}">
        </head>
        <body><p>Content</p></body>
        </html>
        """

        self.test_provider.set_fixture(
            url=requested_url,
            body_text=html,
            status_code=200,
            final_url=final_url,
            redirect_chain=[final_url],
        )

        task = CrawlerTask(
            task_id="ctask-canonical-test",
            request_id="req-1",
            plan_id="p-1",
            question_id="q-1",
            query_or_target=requested_url,
        )

        report = self.crawler.execute_crawler_task(task)

        # Verify all 3 URLs are distinctly preserved
        self.assertEqual(report.metadata["original_url"], requested_url)
        self.assertEqual(report.metadata["url"], final_url)
        self.assertEqual(report.metadata["canonical_url"], expected_canonical)

        # Evidence provenance points to canonical URL as primary reference
        self.assertEqual(report.extracted_evidence[0].provenance.source_ref, expected_canonical)
        self.assertEqual(report.raw_sources[0].url_or_ref, final_url)
        self.assertEqual(report.raw_sources[0].metadata["canonical_url"], expected_canonical)

    # -------------------------------------------------------------
    # 7. Link Extraction Metadata (No Recursive Crawling)
    # -------------------------------------------------------------

    def test_07_extracts_links_metadata_without_crawling(self):
        html = """
        <html>
        <body>
            <h1>Resources</h1>
            <p>Check these references:</p>
            <a href="/specs/v1.html">Specification v1</a>
            <a href="https://other.org/benchmarks">External Benchmarks</a>
            <a href="javascript:void(0)">Ignored JS Link</a>
            <a href="#section-2">Ignored Anchor Only</a>
        </body>
        </html>
        """
        extracted = HtmlExtractor.extract(html, base_url="https://standards.org/overview")

        self.assertEqual(len(extracted.links), 2)
        self.assertEqual(extracted.links[0]["url"], "https://standards.org/specs/v1.html")
        self.assertEqual(extracted.links[0]["text"], "Specification v1")
        self.assertEqual(extracted.links[1]["url"], "https://other.org/benchmarks")
        self.assertEqual(extracted.links[1]["text"], "External Benchmarks")

    # -------------------------------------------------------------
    # 8. Content Hashing & Lineage in CrawlerReport
    # -------------------------------------------------------------

    def test_08_content_hash_and_lineage_preservation(self):
        url = "https://example.org/spec"
        body = "<html><head><title>Spec V1</title></head><body><p>Deterministic Specification</p></body></html>"
        expected_hash = hashlib.sha256(body.encode("utf-8")).hexdigest()

        self.test_provider.set_fixture(url, body, status_code=200)

        task = CrawlerTask(
            task_id="ctask-lineage-spec",
            request_id="req-root-888",
            plan_id="plan-core-777",
            question_id="q-spec-1",
            correlation_id="corr-hash-test-555",
            query_or_target=url,
        )

        report = self.crawler.execute_crawler_task(task)

        # Checksum
        self.assertEqual(report.raw_sources[0].checksum, expected_hash)
        self.assertEqual(report.metadata["checksum"], expected_hash)

        # Lineage
        self.assertEqual(report.request_id, "req-root-888")
        self.assertEqual(report.plan_id, "plan-core-777")
        self.assertEqual(report.question_id, "q-spec-1")
        self.assertEqual(report.crawler_task_id, "ctask-lineage-spec")
        self.assertEqual(report.correlation_id, "corr-hash-test-555")
        self.assertEqual(report.crawler_id, self.crawler.crawler_id)

        # Evidence
        ev = report.extracted_evidence[0]
        self.assertEqual(ev.provenance.request_id, "req-root-888")
        self.assertEqual(ev.provenance.crawler_task_id, "ctask-lineage-spec")
        self.assertEqual(ev.classification, FactClassification.SOURCE_CLAIM)
        self.assertIn("Deterministic Specification", ev.content_snippet)

    # -------------------------------------------------------------
    # 9. Retrieval Timestamp
    # -------------------------------------------------------------

    def test_09_retrieval_timestamp_preserved(self):
        url = "https://example.org/timestamp-check"
        self.test_provider.set_fixture(url, "<p>Timestamp testing</p>", status_code=200)

        task = CrawlerTask(
            task_id="ctask-time-1",
            request_id="req-1",
            plan_id="p-1",
            question_id="q-1",
            query_or_target=url,
        )

        report = self.crawler.execute_crawler_task(task)
        self.assertTrue(report.raw_sources[0].fetched_at)
        self.assertTrue(report.extracted_evidence[0].provenance.captured_at)
        self.assertEqual(report.raw_sources[0].fetched_at, report.extracted_evidence[0].provenance.captured_at)

    # -------------------------------------------------------------
    # 10. Empty HTML
    # -------------------------------------------------------------

    def test_10_empty_html_handling(self):
        extracted = HtmlExtractor.extract("", base_url="https://example.com")
        self.assertEqual(extracted.clean_text, "")
        self.assertIsNone(extracted.title)
        self.assertEqual(len(extracted.links), 0)

        whitespace_extracted = HtmlExtractor.extract("   \n\t  ", base_url="https://example.com")
        self.assertEqual(whitespace_extracted.clean_text, "")

    # -------------------------------------------------------------
    # 11. Non-HTML & Unsupported Content Types
    # -------------------------------------------------------------

    def test_11_plain_text_and_json_content_types(self):
        json_content = '{"name": "autonomos", "version": "1.0.0"}'
        extracted_json = HtmlExtractor.extract(json_content, content_type="application/json")
        self.assertEqual(extracted_json.clean_text, json_content)
        self.assertFalse(extracted_json.is_html)

        plain_text = "Standard plain text documentation with no HTML tags."
        extracted_txt = HtmlExtractor.extract(plain_text, content_type="text/plain")
        self.assertEqual(extracted_txt.clean_text, plain_text)
        self.assertFalse(extracted_txt.is_html)

    def test_12_binary_unsupported_content_type(self):
        pdf_raw = "%PDF-1.7 binary content representation"
        extracted_pdf = HtmlExtractor.extract(pdf_raw, content_type="application/pdf")
        self.assertIn("Binary or unsupported content: application/pdf", extracted_pdf.clean_text)
        self.assertFalse(extracted_pdf.is_html)


if __name__ == "__main__":
    unittest.main()
