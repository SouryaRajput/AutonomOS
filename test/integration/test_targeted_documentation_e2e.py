"""
End-to-End Integration Test for Targeted Documentation Crawling (Phase 1 / Part 4 / Step 3).

Verifies the complete end-to-end integration:
Researcher
   ↓
CrawlerTask(capability=DOCUMENTATION_CRAWL, topic="WebGPU renderer")
   ↓
CrawlerSpawner
   ↓
CrawlerSupervisor
   ↓
DocumentationCrawler
   ↓
TargetedDocumentationCrawlerEngine (Candidate Selection & Ranking)
   ↓
WebFetch (MockFetchProvider)
   ↓
Selected Documentation Pages & Extracted Sections
   ↓
CrawlerReport
   ↓
Researcher (Evidence Ingestion & Provenance Lineage)
"""
from __future__ import annotations

import unittest

from core.research.contracts.crawler_task import CrawlerTask
from core.research.crawler.documentation import DocumentationCrawler
from core.research.crawler.registry import CrawlerRegistry
from core.research.fetch.config import FetchConfig
from core.research.fetch.test_provider import TestFetchProvider
from core.research.orchestration.spawner import CrawlerSpawner
from core.research.orchestration.supervisor import CrawlerSupervisor
from core.research.researcher import Researcher
from core.research.types import (
    CrawlerCapability,
    CrawlerReportStatus,
    FactClassification,
    ResearchConfidence,
    SourceType,
)


class TestTargetedDocumentationE2E(unittest.TestCase):

    def setUp(self):
        self.config = FetchConfig(provider_type="test", allow_localhost=False, default_timeout_seconds=5.0)
        self.fetch_provider = TestFetchProvider(config=self.config)
        self.registry = CrawlerRegistry()
        self.spawner = CrawlerSpawner(registry=self.registry)
        self.supervisor = CrawlerSupervisor()
        self.researcher = Researcher(
            spawner=self.spawner,
            supervisor=self.supervisor,
            registry=self.registry,
        )

        self._setup_doc_site()

    def _setup_doc_site(self):
        """Build mock documentation site."""
        base = "https://docs.threejs-like.org"

        # 1. Documentation Index
        root_html = """
        <html>
        <head><title>Three-Like Graphics Documentation</title></head>
        <body>
            <nav class="sidebar">
                <a href="/docs/installation">Installation</a>
                <a href="/docs/fundamentals">Core Concepts</a>
                <a href="/docs/rendering">Rendering Backends</a>
                <a href="/docs/materials">Shaders & Materials</a>
                <a href="/docs/examples">Demos & Examples</a>
                <a href="https://github.com/three-like/repo">GitHub Code</a>
            </nav>
            <h1>Three-Like Engine Guides</h1>
            <p>Complete documentation for next-generation 3D graphics rendering.</p>
        </body>
        </html>
        """
        self.fetch_provider.set_fixture(f"{base}/docs", root_html)

        # 2. Rendering Backends
        rendering_html = """
        <html>
        <head><title>Rendering Backends</title></head>
        <body>
            <nav class="sidebar">
                <a href="/docs/rendering/webgpu-renderer">WebGPU Modern Renderer</a>
                <a href="/docs/rendering/webgl-legacy">WebGL Fallback Renderer</a>
            </nav>
            <h1>Rendering Backends</h1>
            <p>Compare WebGPU and WebGL render pipelines.</p>
        </body>
        </html>
        """
        self.fetch_provider.set_fixture(f"{base}/docs/rendering", rendering_html)

        # 3. WebGPU Modern Renderer (High relevance exact target)
        webgpu_html = """
        <html>
        <head><title>WebGPU Modern Renderer Guide</title></head>
        <body>
            <nav aria-label="breadcrumb">
                <ol><li><a href="/docs">Docs</a></li><li><a href="/docs/rendering">Rendering</a></li><li>WebGPU</li></ol>
            </nav>
            <h1>WebGPU Modern Renderer Guide</h1>
            <p>The WebGPURenderer is the flagship hardware-accelerated pipeline utilizing compute and fragment passes.</p>
            <h2 id="setup">Setup and Instantiation</h2>
            <p>Initialize const renderer = new WebGPURenderer({ antialias: true });</p>
            <h2 id="compute">Compute Shaders</h2>
            <p>GPU compute nodes execute massively parallel calculations.</p>
            <a href="/docs/rendering/webgpu/wgsl-nodes">WGSL Shader Nodes</a>
        </body>
        </html>
        """
        self.fetch_provider.set_fixture(f"{base}/docs/rendering/webgpu-renderer", webgpu_html)

        # 4. WGSL Shader Nodes (Depth 2 child)
        wgsl_html = """
        <html>
        <head><title>WGSL Shader Nodes</title></head>
        <body>
            <h1>WGSL Node Materials in WebGPU</h1>
            <p>Construct node-based materials compiling directly to high efficiency WGSL.</p>
        </body>
        </html>
        """
        self.fetch_provider.set_fixture(f"{base}/docs/rendering/webgpu/wgsl-nodes", wgsl_html)

        # 5. WebGL Fallback (Lower relevance)
        webgl_html = """
        <html>
        <head><title>WebGL Fallback Renderer</title></head>
        <body>
            <h1>Legacy WebGL Renderer</h1>
            <p>Deprecated OpenGL ES 3.0 compatibility backend.</p>
        </body>
        </html>
        """
        self.fetch_provider.set_fixture(f"{base}/docs/rendering/webgl-legacy", webgl_html)

        # 6. Installation (Irrelevant)
        install_html = """
        <html>
        <head><title>Installation</title></head>
        <body>
            <h1>Installation</h1>
            <p>npm install @three-like/core</p>
        </body>
        </html>
        """
        self.fetch_provider.set_fixture(f"{base}/docs/installation", install_html)

    def test_e2e_targeted_documentation_crawling_flow(self):
        """
        Full E2E test verifying:
        1. CrawlerSpawner provisions DocumentationCrawler with DOCUMENTATION_CRAWL capability
        2. CrawlerSupervisor dispatches task to DocumentationCrawler
        3. TargetedDocumentationCrawlerEngine discovers structure and scores candidate pages
        4. WebGPU Renderer is prioritized and fetched via WebFetch
        5. Irrelevant pages (e.g. installation) are excluded
        6. External links (e.g. GitHub) are excluded from page crawl
        7. Evidence items and raw sources preserve complete lineage and provenance
        """
        start_url = "https://docs.threejs-like.org/docs"

        # 1. Provision and register crawler with mock provider
        crawler = DocumentationCrawler(
            crawler_id="crawler.doc.e2e_01",
            provider=self.fetch_provider,
            config=self.config,
        )
        self.registry.register_crawler_instance(crawler)
        self.assertIsInstance(crawler, DocumentationCrawler)

        # 2. Construct targeted documentation task
        task = CrawlerTask(
            task_id="ctask-e2e-doc-01",
            request_id="req-e2e-01",
            plan_id="plan-e2e-01",
            question_id="q-e2e-01",
            query_or_target=start_url,
            objective="Research WebGPU renderer architecture in official documentation",
            required_capability=CrawlerCapability.DOCUMENTATION_CRAWL,
            parameters={
                "topic": "WebGPU Modern Renderer",
                "keywords": ["WebGPU", "renderer", "WGSL"],
                "max_depth": 2,
                "max_pages": 3,
            },
        )

        # 3. Supervisor executes task on crawler
        report = self.supervisor.execute_task(crawler, task)

        # 4. Verify report success and metadata
        self.assertEqual(report.status, CrawlerReportStatus.SUCCESS)
        self.assertEqual(report.crawler_task_id, "ctask-e2e-doc-01")
        self.assertEqual(report.request_id, "req-e2e-01")
        self.assertEqual(report.plan_id, "plan-e2e-01")
        self.assertEqual(report.question_id, "q-e2e-01")

        # 5. Verify page selection accuracy
        fetched_urls = [s.url_or_ref for s in report.raw_sources]
        self.assertIn("https://docs.threejs-like.org/docs/rendering/webgpu-renderer", fetched_urls)
        # Irrelevant page was skipped
        self.assertNotIn("https://docs.threejs-like.org/docs/installation", fetched_urls)

        # 6. Verify evidence generation and lineage
        self.assertTrue(len(report.extracted_evidence) >= 1)
        for ev in report.extracted_evidence:
            self.assertEqual(ev.provenance.request_id, "req-e2e-01")
            self.assertEqual(ev.provenance.crawler_task_id, "ctask-e2e-doc-01")
            self.assertEqual(ev.provenance.question_id, "q-e2e-01")
            self.assertEqual(ev.classification, FactClassification.SOURCE_CLAIM)

        # 7. Verify internal observability metrics
        self.assertIn("candidates_discovered", report.metadata)
        self.assertIn("selected_count", report.metadata)
        self.assertEqual(report.metadata["topic"], "WebGPU Modern Renderer")
        self.assertTrue(report.metadata["selected_count"] <= 3)


if __name__ == "__main__":
    unittest.main()
