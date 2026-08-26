import pathlib
import shutil
import tempfile
import unittest

from core.enums import ArtifactType, DependencyType, ProjectStatus, RiskLevel, TaskStatus, WorkerStatus
from core.models import Artifact, Dependency, Project, Task, WorkerManifest
from core.storage.sqlite_store import SQLiteStore


class TestSQLitePersistence(unittest.TestCase):

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.db_path = str(pathlib.Path(self.temp_dir) / "test_state.db")
        self.store = SQLiteStore(self.db_path)

    def tearDown(self):
        self.store.close()
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_persistence_and_process_restart(self):
        # 1. Populate initial state in DB
        project = Project(
            id="proj-alpha",
            name="Alpha Platform",
            description="Autonomous OS project",
            root_path=self.temp_dir,
            status=ProjectStatus.ACTIVE,
            configuration={"max_workers": 5},
        )
        self.store.save_project(project)

        worker = WorkerManifest(
            id="worker-coder",
            name="Coder Worker",
            role="Programming",
            description="Writes code",
            capabilities=["code_gen", "ast_edit"],
            permissions=["filesystem.write"],
            tools=["filesystem.write"],
            model_policy={"model": "deepseek-coder"},
            status=WorkerStatus.IDLE,
        )
        self.store.save_worker(worker)

        task_1 = Task(
            id="task-100",
            project_id="proj-alpha",
            title="Setup Scaffold",
            objective="Initialize workspace",
            status=TaskStatus.COMPLETED,
            priority=2,
            risk=RiskLevel.LOW,
            assigned_worker="worker-coder",
        )
        task_2 = Task(
            id="task-200",
            project_id="proj-alpha",
            title="Build Runtime",
            objective="Implement task engine",
            status=TaskStatus.READY,
            priority=1,
            risk=RiskLevel.MEDIUM,
            assigned_worker="worker-coder",
        )
        self.store.save_task(task_1)
        self.store.save_task(task_2)

        dep = Dependency(
            id="dep-1",
            dependent_task_id="task-200",
            prerequisite_task_id="task-100",
            dependency_type=DependencyType.STRICT_SUCCESS,
        )
        self.store.add_dependency(dep)

        art = Artifact(
            id="art-1",
            project_id="proj-alpha",
            task_id="task-100",
            worker_id="worker-coder",
            type=ArtifactType.FILE,
            path="scaffold.json",
            description="Generated project scaffold",
            checksum="abc123sha",
            metadata={"lines": 42},
        )
        self.store.save_artifact(art)

        # 2. Simulate Process Restart: Close current connection
        self.store.close()

        # 3. Open a brand new store connection pointing to the same file
        restarted_store = SQLiteStore(self.db_path)
        try:
            # Verify Project
            p = restarted_store.get_project("proj-alpha")
            self.assertIsNotNone(p)
            self.assertEqual(p.name, "Alpha Platform")
            self.assertEqual(p.configuration["max_workers"], 5)

            # Verify Worker
            w = restarted_store.get_worker("worker-coder")
            self.assertIsNotNone(w)
            self.assertEqual(w.capabilities, ["code_gen", "ast_edit"])
            self.assertEqual(w.status, WorkerStatus.IDLE)

            # Verify Tasks
            t1 = restarted_store.get_task("task-100")
            self.assertIsNotNone(t1)
            self.assertEqual(t1.status, TaskStatus.COMPLETED)

            t2 = restarted_store.get_task("task-200")
            self.assertIsNotNone(t2)
            self.assertEqual(t2.status, TaskStatus.READY)
            self.assertEqual(t2.risk, RiskLevel.MEDIUM)

            # Verify Dependencies
            deps = restarted_store.get_dependencies_for_task("task-200")
            self.assertEqual(len(deps), 1)
            self.assertEqual(deps[0].prerequisite_task_id, "task-100")

            # Verify Artifacts
            artifacts = restarted_store.list_artifacts_for_task("task-100")
            self.assertEqual(len(artifacts), 1)
            self.assertEqual(artifacts[0].checksum, "abc123sha")
            self.assertEqual(artifacts[0].metadata["lines"], 42)

        finally:
            restarted_store.close()


if __name__ == "__main__":
    unittest.main()
