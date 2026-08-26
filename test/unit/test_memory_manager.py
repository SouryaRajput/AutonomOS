import pathlib
import shutil
import tempfile
import unittest

from core.enums import IssueSeverity, IssueStatus, MemoryType
from core.errors import MemoryAlreadyExistsError, MemoryConflictError, MemoryNotFoundError
from core.memory.manager import MemoryManager
from core.models import Project, utc_now
from core.storage.sqlite_store import SQLiteStore


class TestMemoryManager(unittest.TestCase):

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.db_path = str(pathlib.Path(self.temp_dir) / "test_memory.db")
        self.project_workspace = str(pathlib.Path(self.temp_dir) / "workspace")
        pathlib.Path(self.project_workspace).mkdir(parents=True, exist_ok=True)

        self.store = SQLiteStore(self.db_path)
        self.project = Project(
            id="proj-mem-test",
            name="Memory Test App",
            description="Testing persistent project memory",
            root_path=self.project_workspace,
            created_at=utc_now(),
        )
        self.store.save_project(self.project)
        self.manager = MemoryManager(self.store)

    def tearDown(self):
        self.store.close()
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_scaffold_initialization(self):
        scaffold = self.manager.initialize_project_memory(
            project_id=self.project.id,
            project_name=self.project.name,
            description=self.project.description,
        )
        self.assertEqual(len(scaffold), 3)
        self.assertIn("project_map", scaffold)
        self.assertIn("architecture", scaffold)
        self.assertIn("current_state", scaffold)

        # Check physical files exist on disk
        map_path = pathlib.Path(self.project_workspace) / ".autonomos" / "memory" / "project-map.md"
        arch_path = pathlib.Path(self.project_workspace) / ".autonomos" / "memory" / "architecture.md"
        state_path = pathlib.Path(self.project_workspace) / ".autonomos" / "memory" / "current-state.md"

        self.assertTrue(map_path.exists())
        self.assertTrue(arch_path.exists())
        self.assertTrue(state_path.exists())

        # Check content is non-empty
        self.assertIn("Memory Test App", map_path.read_text())
        self.assertIn("Invariants", arch_path.read_text())

    def test_create_and_read_memory_document(self):
        doc = self.manager.create_memory(
            project_id=self.project.id,
            memory_type=MemoryType.NOTE,
            title="Design Notes",
            content="# Design Notes\nDiscussion on DB schema.",
            relative_path=".autonomos/notes/design.md",
            summary="DB design discussion",
            tags=["design", "db"],
        )
        self.assertEqual(doc.title, "Design Notes")

        # Read by ID
        fetched = self.manager.read_memory(self.project.id, doc.id)
        self.assertEqual(fetched.id, doc.id)
        self.assertEqual(fetched.content, "# Design Notes\nDiscussion on DB schema.")

        # Read by path
        fetched_path = self.manager.read_memory_by_path(self.project.id, ".autonomos/notes/design.md")
        self.assertEqual(fetched_path.id, doc.id)

    def test_update_memory_document_atomic_and_versioned(self):
        doc = self.manager.create_memory(
            project_id=self.project.id,
            memory_type=MemoryType.NOTE,
            title="Original Note",
            content="# Original Content",
            relative_path=".autonomos/notes/atomic.md",
        )
        self.assertEqual(doc.version, 1)

        updated = self.manager.update_memory(
            project_id=self.project.id,
            memory_id=doc.id,
            content="# Updated Content (Atomic)",
            expected_version=1,
        )
        self.assertEqual(updated.version, 2)

        file_on_disk = pathlib.Path(self.project_workspace) / ".autonomos" / "notes" / "atomic.md"
        self.assertEqual(file_on_disk.read_text(), "# Updated Content (Atomic)")

        # Conflict on stale expected_version
        with self.assertRaises(MemoryConflictError):
            self.manager.update_memory(
                project_id=self.project.id,
                memory_id=doc.id,
                content="# Conflicting Content",
                expected_version=1,  # Stale version! Current is 2
            )

    def test_delete_memory_removes_file_and_record(self):
        doc = self.manager.create_memory(
            project_id=self.project.id,
            memory_type=MemoryType.NOTE,
            title="Temporary Note",
            content="# Temporary",
            relative_path=".autonomos/notes/temp.md",
        )
        disk_file = pathlib.Path(self.project_workspace) / ".autonomos" / "notes" / "temp.md"
        self.assertTrue(disk_file.exists())

        deleted = self.manager.delete_memory(self.project.id, doc.id)
        self.assertTrue(deleted)
        self.assertFalse(disk_file.exists())
        self.assertFalse(self.manager.exists(self.project.id, doc.id))

    def test_record_decision(self):
        adr = self.manager.record_decision(
            project_id=self.project.id,
            title="Use SQLite WAL Mode",
            context="We need high-performance concurrency without lock contention.",
            decision="Enable PRAGMA journal_mode=WAL.",
            reasoning="WAL allows concurrent readers and single writer.",
            consequences="Requires proper connection handling.",
            decision_number=1,
            status="Accepted",
        )
        self.assertEqual(adr.memory_type, MemoryType.DECISION)
        self.assertIn("0001-use-sqlite-wal-mode.md", adr.relative_path)
        disk_file = pathlib.Path(self.project_workspace) / adr.relative_path
        self.assertTrue(disk_file.exists())
        self.assertIn("Status**: Accepted", disk_file.read_text())

    def test_record_report_and_task_memory(self):
        # Record report
        rep = self.manager.record_report(
            project_id=self.project.id,
            task_id="task-010",
            worker_id="worker.programmer",
            title="Task 010 Execution Report",
            summary="Refactored database queries",
            markdown_body="All 10 queries optimized with indices.",
        )
        self.assertEqual(rep.memory_type, MemoryType.REPORT)
        self.assertIn("task-010", rep.relative_path)

        # Record task memory
        task_mem = self.manager.record_task_memory(
            project_id=self.project.id,
            task_id="task-010",
            title="Optimize DB Queries",
            objective="Add indices to slow queries",
            relevant_files=["core/storage/sqlite_store.py"],
            findings="Indices reduced query latency by 80%.",
            outcome="COMPLETED",
        )
        self.assertEqual(task_mem.memory_type, MemoryType.TASK_MEMORY)
        self.assertEqual(task_mem.references, ["core/storage/sqlite_store.py"])

    def test_record_issue(self):
        issue = self.manager.record_issue(
            project_id=self.project.id,
            title="Memory leak in long-running worker",
            description="Process RSS grows after 1,000 tasks.",
            severity=IssueSeverity.HIGH,
            status=IssueStatus.OPEN,
            affected_area="WorkerHost",
        )
        self.assertEqual(issue.memory_type, MemoryType.ISSUE)
        self.assertIn("HIGH", issue.content)


if __name__ == "__main__":
    unittest.main()
