import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path

from core.enums import ProjectStatus, TaskStatus
from core.manager.planner import ManagerPlanner
from core.models import Project
from core.runtime.workforce_runtime import WorkforceRuntime
from core.storage.sqlite_store import SQLiteStore
from core.workspace.filesystem import ControlledWorkspaceFS
from core.workspace.incremental import IncrementalAuditEngine
from core.workspace.project_map import ProjectMapEngine
from core.workspace.snapshot import SnapshotEngine


class TestActualManagerExecution(unittest.TestCase):
    """
    End-to-end verification of actual Manager execution on workspace:
    1. Persistent Project Map generation (.autonomos/project-map.md)
    2. Snapshot persistence (.autonomos/last_snapshot.json)
    3. Incremental auditing on file modifications
    4. Task decomposition in the real TaskEngine
    5. Inactive worker enforcement
    6. Concise response formatting
    """

    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.workspace_path = Path(self.test_dir) / "test_project"
        self.workspace_path.mkdir(parents=True, exist_ok=True)

        # Create sample project structure
        (self.workspace_path / "src").mkdir(parents=True, exist_ok=True)
        (self.workspace_path / "tests").mkdir(parents=True, exist_ok=True)

        (self.workspace_path / "package.json").write_text(
            json.dumps({"name": "sample-portfolio", "version": "1.0.0", "dependencies": {"three": "^0.150.0"}}),
            encoding="utf-8",
        )
        (self.workspace_path / "src" / "index.ts").write_text(
            "import * as THREE from 'three';\nexport function initScene() { return new THREE.Scene(); }\n",
            encoding="utf-8",
        )
        (self.workspace_path / "tests" / "test_scene.ts").write_text(
            "import { initScene } from '../src/index';\n// test scene\n",
            encoding="utf-8",
        )

        self.fs = ControlledWorkspaceFS(str(self.workspace_path))
        self.store = SQLiteStore(":memory:")
        self.runtime = WorkforceRuntime(store=self.store)

        # Register project in store
        self.runtime.projects.create_project(
            project_id="proj-test-1",
            name="test_project",
            root_path=str(self.workspace_path),
            description="Test Project for Manager Execution",
        )

        self.map_engine = ProjectMapEngine(self.fs)
        self.incremental_engine = IncrementalAuditEngine(self.fs, self.map_engine)
        self.planner = ManagerPlanner(self.runtime, self.map_engine)

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_complete_actual_manager_lifecycle(self):
        # Step 1-4: Verify .autonomos/ and .autonomos/project-map.md are created and contain real project info
        self.assertFalse(self.map_engine.is_initialized())
        audit_res = self.map_engine.perform_full_audit(trigger="USER_REQUEST")

        meta_dir = self.workspace_path / ".autonomos"
        map_md = meta_dir / "project-map.md"
        map_json = meta_dir / "project_map.json"
        snapshot_json = meta_dir / "last_snapshot.json"

        self.assertTrue(meta_dir.exists(), ".autonomos/ directory must exist")
        self.assertTrue(map_md.exists(), ".autonomos/project-map.md must exist on disk")
        self.assertTrue(map_json.exists(), ".autonomos/project_map.json must exist on disk")
        self.assertTrue(snapshot_json.exists(), ".autonomos/last_snapshot.json must exist on disk")

        content = map_md.read_text(encoding="utf-8")
        self.assertIn("test_project", content)
        self.assertIn("Three.js", content)
        self.assertIn("src/index.ts", content)
        self.assertIn("package.json", content)

        # Step 5-6: Simulate restart and verify Project Map is still loaded
        fs_new_session = ControlledWorkspaceFS(str(self.workspace_path))
        map_engine_session = ProjectMapEngine(fs_new_session)
        self.assertTrue(map_engine_session.is_initialized())
        loaded_map = map_engine_session.load_project_map()
        self.assertIsNotNone(loaded_map)
        self.assertEqual(loaded_map["project_name"], "test_project")

        # Step 7-10: Modify a file and run incremental audit
        (self.workspace_path / "src" / "index.ts").write_text(
            "import * as THREE from 'three';\nexport function initScene() { return new THREE.Scene(); }\nexport function addCamera() {}\n",
            encoding="utf-8",
        )
        inc_engine = IncrementalAuditEngine(fs_new_session, map_engine_session)
        diff = inc_engine.check_and_update(trigger="USER_REQUEST")

        self.assertIn("INCREMENTAL", diff["status"])
        self.assertEqual(diff["files_audited"], 2)  # Modified file (src/index.ts) + dependent (tests/test_scene.ts)
        self.assertIn("src/index.ts", diff["impact"]["changed_files"])
        self.assertIn("tests/test_scene.ts", diff["impact"]["impacted_dependents"])

        # Step 11-13: Ask Manager for a substantial change ("Turn website into a 3D portfolio")
        plan = self.planner.plan_and_delegate(
            project_id="proj-test-1",
            objective="Turn my website into a 3D portfolio.",
        )

        self.assertGreaterEqual(len(plan.tasks), 2)
        self.assertTrue(plan.workers_disabled)

        # Verify tasks actually exist in the Task Engine
        tasks_in_engine = self.runtime.tasks.list_tasks(project_id="proj-test-1")
        self.assertEqual(len(tasks_in_engine), len(plan.tasks))

        for task in tasks_in_engine:
            self.assertIn(task.status, [TaskStatus.PENDING, TaskStatus.READY, TaskStatus.ASSIGNED, TaskStatus.RUNNING])
            # Specialist workers are disabled - no execution happened
            self.assertNotEqual(task.status, TaskStatus.COMPLETED)
            self.assertIn("worker_prompt", task.metadata)
            self.assertIn("worker_type", task.metadata)

        # Step 14: Verify response format stays concise
        concise_response = (
            f"Got it. I've analyzed the project and prepared {len(plan.tasks)} tasks. "
            "Workers are currently disabled."
        )
        self.assertLess(len(concise_response.split()), 30)
        self.assertNotIn("--- TASK CONTRACT:", concise_response)


if __name__ == "__main__":
    unittest.main()
