import shutil
import tempfile
import unittest

from core.enums import ProjectStatus
from core.errors import ProjectAlreadyExistsError, ProjectNotFoundError
from core.runtime.project_registry import ProjectRegistry
from core.storage.memory_store import MemoryStore
from core.storage.sqlite_store import SQLiteStore


class TestProjectRegistry(unittest.TestCase):

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.store = MemoryStore()
        self.registry = ProjectRegistry(self.store)

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_create_and_get_project(self):
        project = self.registry.create_project(
            name="Test Alpha",
            root_path=self.temp_dir,
            description="A test workspace",
            configuration={"budget_usd": 10.0},
        )
        self.assertIsNotNone(project.id)
        self.assertEqual(project.name, "Test Alpha")
        self.assertEqual(project.status, ProjectStatus.ACTIVE)

        fetched = self.registry.get_project(project.id)
        self.assertEqual(fetched.id, project.id)
        self.assertEqual(fetched.name, "Test Alpha")
        self.assertEqual(fetched.configuration["budget_usd"], 10.0)

    def test_create_duplicate_project_fails(self):
        self.registry.create_project(
            name="Test Alpha",
            root_path=self.temp_dir,
            project_id="proj-123",
        )
        with self.assertRaises(ProjectAlreadyExistsError):
            self.registry.create_project(
                name="Test Beta",
                root_path=self.temp_dir,
                project_id="proj-123",
            )

    def test_get_nonexistent_project_fails(self):
        with self.assertRaises(ProjectNotFoundError):
            self.registry.get_project("does-not-exist")

    def test_list_projects(self):
        self.registry.create_project(name="P1", root_path=self.temp_dir)
        self.registry.create_project(name="P2", root_path=self.temp_dir)
        projects = self.registry.list_projects()
        self.assertEqual(len(projects), 2)

    def test_update_project(self):
        p = self.registry.create_project(name="Old Name", root_path=self.temp_dir)
        updated = self.registry.update_project(
            project_id=p.id,
            name="New Name",
            status=ProjectStatus.PAUSED,
        )
        self.assertEqual(updated.name, "New Name")
        self.assertEqual(updated.status, ProjectStatus.PAUSED)

    def test_delete_project(self):
        p = self.registry.create_project(name="To Delete", root_path=self.temp_dir)
        deleted = self.registry.delete_project(p.id)
        self.assertTrue(deleted)
        with self.assertRaises(ProjectNotFoundError):
            self.registry.get_project(p.id)


if __name__ == "__main__":
    unittest.main()
