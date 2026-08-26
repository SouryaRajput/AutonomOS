import pathlib
import shutil
import tempfile
import unittest

from core.enums import ArtifactType
from core.errors import ArtifactNotFoundError
from core.models import Project, Task
from core.runtime.artifact_registry import ArtifactRegistry
from core.storage.memory_store import MemoryStore


class TestArtifactRegistry(unittest.TestCase):

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.store = MemoryStore()
        self.registry = ArtifactRegistry(self.store)

        self.project = Project(id="p-1", name="Project 1", description="", root_path=self.temp_dir)
        self.store.save_project(self.project)

        self.task = Task(id="t-1", project_id="p-1", title="Task 1", objective="")
        self.store.save_task(self.task)

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_register_and_get_artifact_with_file_write(self):
        content = "Hello, AutonomOS Artifact!"
        artifact = self.registry.register_artifact(
            project_id="p-1",
            task_id="t-1",
            worker_id="w-1",
            artifact_type=ArtifactType.FILE,
            relative_path="artifacts/output.txt",
            description="Sample output file",
            content=content,
            base_dir=self.temp_dir,
            metadata={"format": "plain/text"},
        )
        self.assertIsNotNone(artifact.id)
        self.assertIsNotNone(artifact.checksum)

        # Verify file exists on disk
        disk_file = pathlib.Path(self.temp_dir) / "artifacts/output.txt"
        self.assertTrue(disk_file.exists())
        self.assertEqual(disk_file.read_text(), content)

        # Verify retrieval from registry
        fetched = self.registry.get_artifact(artifact.id)
        self.assertEqual(fetched.id, artifact.id)
        self.assertEqual(fetched.description, "Sample output file")
        self.assertEqual(fetched.metadata["format"], "plain/text")

        # Verify task artifact list updated
        task_artifacts = self.registry.list_artifacts_for_task("t-1")
        self.assertEqual(len(task_artifacts), 1)
        self.assertEqual(task_artifacts[0].id, artifact.id)

    def test_get_nonexistent_artifact_fails(self):
        with self.assertRaises(ArtifactNotFoundError):
            self.registry.get_artifact("ghost-artifact")


if __name__ == "__main__":
    unittest.main()
