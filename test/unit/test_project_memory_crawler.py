"""
Unit tests for ProjectMemoryCrawler.

Tests use in-memory temp directories to avoid any filesystem side effects.
"""
import os
import tempfile
import unittest
from pathlib import Path

from core.research.contracts.crawler_task import CrawlerTask
from core.research.crawler.project_memory import (
    ProjectMemoryCrawler,
    _classify_memory_file,
    _extract_sections,
    _extract_title_from_markdown,
    _matches_query,
)
from core.research.types import (
    CrawlerCapability,
    CrawlerReportStatus,
    FactClassification,
    ResearchConfidence,
    SourceType,
)


def _make_task(
    query: str = "",
    params: dict = None,
    task_id: str = "task-001",
) -> CrawlerTask:
    return CrawlerTask(
        task_id=task_id,
        request_id="req-001",
        plan_id="plan-001",
        question_id="q-001",
        query_or_target=query,
        required_capability=CrawlerCapability.PROJECT_MEMORY_LOOKUP,
        parameters=params or {},
    )


class TestProjectMemoryCrawlerClassification(unittest.TestCase):
    """Unit tests for _classify_memory_file classification logic."""

    def test_architecture_is_fact_high(self):
        cls, conf, rel = _classify_memory_file("memory/architecture.md")
        self.assertEqual(cls, FactClassification.FACT)
        self.assertEqual(conf, ResearchConfidence.WELL_SUPPORTED)
        self.assertAlmostEqual(rel, 1.0)

    def test_project_map_is_fact(self):
        cls, conf, _ = _classify_memory_file("memory/project-map.md")
        self.assertEqual(cls, FactClassification.FACT)

    def test_decisions_is_source_claim(self):
        cls, conf, _ = _classify_memory_file("memory/decisions/001-use-sqlite.md")
        self.assertEqual(cls, FactClassification.SOURCE_CLAIM)

    def test_tech_debt_is_observation(self):
        cls, conf, _ = _classify_memory_file("memory/tech_debt/slow-queries.md")
        self.assertEqual(cls, FactClassification.INFERENCE)

    def test_current_state_is_observation(self):
        cls, conf, _ = _classify_memory_file("memory/current-state.md")
        self.assertEqual(cls, FactClassification.INFERENCE)

    def test_generic_file_is_observation_low(self):
        cls, conf, rel = _classify_memory_file("memory/notes.md")
        self.assertEqual(cls, FactClassification.SOURCE_CLAIM)
        self.assertEqual(conf, ResearchConfidence.LIMITED_EVIDENCE)


class TestHelperFunctions(unittest.TestCase):
    """Tests for Markdown parsing helpers."""

    def test_extract_title_from_h1(self):
        md = "# My Architecture\n\nSome content here."
        self.assertEqual(_extract_title_from_markdown(md, "fallback"), "My Architecture")

    def test_extract_title_fallback(self):
        md = "No heading here.\n\nJust text."
        self.assertEqual(_extract_title_from_markdown(md, "fallback-name"), "fallback-name")

    def test_extract_sections_splits_on_h2(self):
        md = "# Title\n\n## Section A\n\nContent A.\n\n## Section B\n\nContent B."
        sections = _extract_sections(md)
        headings = [s["heading"] for s in sections]
        self.assertIn("Section A", headings)
        self.assertIn("Section B", headings)

    def test_extract_sections_no_h2_returns_single(self):
        md = "# Title\n\nThis is all content with no sections."
        sections = _extract_sections(md)
        self.assertEqual(len(sections), 1)

    def test_matches_query_true(self):
        self.assertTrue(_matches_query("This contains SearchTerm here.", "title", "searchterm"))

    def test_matches_query_false(self):
        self.assertFalse(_matches_query("No match here.", "title", "xyz_missing"))

    def test_matches_empty_query_always_true(self):
        self.assertTrue(_matches_query("anything", "title", ""))


class TestProjectMemoryCrawlerExecution(unittest.TestCase):
    """Integration-style tests with actual temp filesystem."""

    def _setup_memory_dir(self, tmpdir: str) -> str:
        """Build a fake .autonomos/memory/ structure inside tmpdir."""
        root = Path(tmpdir)
        memory_dir = root / ".autonomos" / "memory"
        decisions_dir = memory_dir / "decisions"
        tech_debt_dir = memory_dir / "tech_debt"
        decisions_dir.mkdir(parents=True)
        tech_debt_dir.mkdir(parents=True)

        (memory_dir / "architecture.md").write_text(
            "# Project Architecture\n\n## Overview\n\nThis is a monorepo with Python backend.\n\n## Components\n\nWorkers, runtime, research.",
            encoding="utf-8",
        )
        (memory_dir / "current-state.md").write_text(
            "# Current State\n\n## Status\n\nInitial scaffolding done.",
            encoding="utf-8",
        )
        (decisions_dir / "001-database.md").write_text(
            "# Use SQLite\n\n## Context\n\nSQLite chosen for embedded use.",
            encoding="utf-8",
        )
        (tech_debt_dir / "slow-queries.md").write_text(
            "# Slow Query Debt\n\n## Issue\n\nQuery takes >500ms.",
            encoding="utf-8",
        )
        return str(root)

    def test_crawl_returns_success(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            project_root = self._setup_memory_dir(tmpdir)
            crawler = ProjectMemoryCrawler(project_root=project_root)
            task = _make_task(query="", params={"project_root": project_root})
            report = crawler.execute_crawler_task(task)
            self.assertEqual(report.status, CrawlerReportStatus.SUCCESS)
            self.assertGreater(len(report.extracted_evidence), 0)

    def test_evidence_has_internal_source_type(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            project_root = self._setup_memory_dir(tmpdir)
            crawler = ProjectMemoryCrawler(project_root=project_root)
            task = _make_task(params={"project_root": project_root})
            report = crawler.execute_crawler_task(task)
            for ev in report.extracted_evidence:
                self.assertEqual(ev.source_type, SourceType.PROJECT_MEMORY)

    def test_architecture_evidence_classified_as_fact(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            project_root = self._setup_memory_dir(tmpdir)
            crawler = ProjectMemoryCrawler(project_root=project_root)
            task = _make_task(query="architecture", params={"project_root": project_root})
            report = crawler.execute_crawler_task(task)
            arch_evidence = [
                ev for ev in report.extracted_evidence
                if "architecture" in ev.metadata.get("memory_file", "").lower()
            ]
            self.assertGreater(len(arch_evidence), 0)
            for ev in arch_evidence:
                self.assertEqual(ev.classification, FactClassification.FACT)
                self.assertEqual(ev.confidence, ResearchConfidence.WELL_SUPPORTED)

    def test_decision_evidence_classified_as_source_claim(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            project_root = self._setup_memory_dir(tmpdir)
            crawler = ProjectMemoryCrawler(project_root=project_root)
            task = _make_task(params={"project_root": project_root})
            report = crawler.execute_crawler_task(task)
            decision_evidence = [
                ev for ev in report.extracted_evidence
                if "decisions" in ev.metadata.get("memory_file", "").lower()
            ]
            self.assertGreater(len(decision_evidence), 0)
            for ev in decision_evidence:
                self.assertEqual(ev.classification, FactClassification.SOURCE_CLAIM)

    def test_query_filter_reduces_results(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            project_root = self._setup_memory_dir(tmpdir)
            crawler = ProjectMemoryCrawler(project_root=project_root)
            # Full scan
            task_full = _make_task(query="", params={"project_root": project_root})
            report_full = crawler.execute_crawler_task(task_full)
            # Narrow query
            task_narrow = _make_task(query="SQLite", params={"project_root": project_root})
            report_narrow = crawler.execute_crawler_task(task_narrow)
            self.assertLess(len(report_narrow.extracted_evidence), len(report_full.extracted_evidence))

    def test_empty_memory_dir_returns_partial(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / ".autonomos" / "memory").mkdir(parents=True)
            crawler = ProjectMemoryCrawler(project_root=str(root))
            task = _make_task(params={"project_root": str(root)})
            report = crawler.execute_crawler_task(task)
            self.assertIn(report.status, [CrawlerReportStatus.PARTIAL, CrawlerReportStatus.SUCCESS])

    def test_missing_memory_dir_returns_failed(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            crawler = ProjectMemoryCrawler()  # No project_root
            task = _make_task(
                query="search",
                params={"project_root": tmpdir},  # No .autonomos/memory inside
            )
            report = crawler.execute_crawler_task(task)
            self.assertEqual(report.status, CrawlerReportStatus.FAILED)
            self.assertIn("memory", report.error_message.lower())

    def test_capabilities_set_correctly(self):
        crawler = ProjectMemoryCrawler()
        caps = crawler._capabilities
        self.assertIn(CrawlerCapability.PROJECT_MEMORY_LOOKUP, caps)

    def test_evidence_checksums_are_populated(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            project_root = self._setup_memory_dir(tmpdir)
            crawler = ProjectMemoryCrawler(project_root=project_root)
            task = _make_task(params={"project_root": project_root})
            report = crawler.execute_crawler_task(task)
            for ev in report.extracted_evidence:
                self.assertNotEqual(ev.checksum, "", msg="EvidenceItem checksum should be set")


if __name__ == "__main__":
    unittest.main()
