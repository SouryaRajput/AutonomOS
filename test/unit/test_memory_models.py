import unittest

from core.enums import MemoryType
from core.memory.model import MemoryDocument, ValidationReport, compute_checksum


class TestMemoryModels(unittest.TestCase):

    def test_memory_document_creation_and_checksum(self):
        doc = MemoryDocument(
            id="mem-1",
            project_id="proj-1",
            memory_type=MemoryType.ARCHITECTURE,
            title="System Architecture",
            relative_path=".autonomos/memory/architecture.md",
            content="# System Architecture\nInvariants here.",
        )
        self.assertEqual(doc.version, 1)
        self.assertIsNotNone(doc.checksum)
        self.assertEqual(doc.checksum, compute_checksum(doc.content))

    def test_update_content_increments_version_and_updates_checksum(self):
        doc = MemoryDocument(
            id="mem-1",
            project_id="proj-1",
            memory_type=MemoryType.PROJECT_MAP,
            title="Project Map",
            relative_path=".autonomos/memory/project-map.md",
            content="Version 1 content",
        )
        initial_checksum = doc.checksum
        initial_version = doc.version

        doc.update_content("Version 2 new content", summary="Updated map summary")
        self.assertEqual(doc.version, initial_version + 1)
        self.assertNotEqual(doc.checksum, initial_checksum)
        self.assertEqual(doc.summary, "Updated map summary")

    def test_serialization_and_deserialization(self):
        doc = MemoryDocument(
            id="mem-100",
            project_id="proj-1",
            memory_type=MemoryType.DECISION,
            title="ADR 0001",
            relative_path=".autonomos/decisions/0001-use-sql.md",
            content="# ADR 0001\nWe use SQL.",
            summary="Use SQL",
            tags=["adr", "sql"],
            references=["core/storage/sqlite_store.py"],
            related_task_id="task-1",
            related_worker_id="worker.programmer",
            version=3,
        )
        d = doc.to_dict()
        self.assertEqual(d["id"], "mem-100")
        self.assertEqual(d["memory_type"], "DECISION")
        self.assertEqual(d["tags"], ["adr", "sql"])

        restored = MemoryDocument.from_dict(d)
        self.assertEqual(restored.id, doc.id)
        self.assertEqual(restored.memory_type, MemoryType.DECISION)
        self.assertEqual(restored.version, 3)
        self.assertEqual(restored.references, ["core/storage/sqlite_store.py"])

    def test_validation_report_issues_count(self):
        report = ValidationReport(
            memory_id="mem-1",
            relative_path=".autonomos/memory/architecture.md",
            is_valid=False,
            broken_file_references=["nonexistent/file.py"],
            broken_task_references=["task-999"],
            broken_worker_references=[],
        )
        self.assertFalse(report.is_valid)
        self.assertEqual(report.total_issues_count, 2)


if __name__ == "__main__":
    unittest.main()
