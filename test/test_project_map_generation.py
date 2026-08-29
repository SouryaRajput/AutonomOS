import json
import os
from pathlib import Path
import shutil
import tempfile
import unittest

from core.workspace.filesystem import ControlledWorkspaceFS
from core.workspace.project_map import ProjectMapEngine
from core.workspace.scanner import RepositoryScanner


class TestProjectMapGeneration(unittest.TestCase):
    """
    Focused test suite verifying persistent Project Map generation in .autonomos/project-map.md.
    """

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp(prefix="autonomos_test_pmap_")
        self.workspace_root = Path(self.temp_dir) / "sample_project"
        self.workspace_root.mkdir()
        self.fs = ControlledWorkspaceFS(self.workspace_root)
        self.engine = ProjectMapEngine(self.fs)

    def tearDown(self):
        if os.path.exists(self.temp_dir):
            shutil.rmtree(self.temp_dir)

    def _setup_mock_project(self):
        # 1. Manifest
        self.fs.create_file("package.json", json.dumps({
            "name": "sample-project",
            "main": "src/index.ts",
            "dependencies": {"react": "^18.0.0", "next": "^14.0.0"}
        }))

        # 2. Source Code
        self.fs.create_file("src/index.ts", """
import React from 'react';

export class AppRoot {
  render() {
    return 'root';
  }
}
""")

        self.fs.create_file("src/utils/math.ts", """
export function add(a: number, b: number): number {
  return a + b;
}
""")

        # 3. Tests
        self.fs.create_file("test/math.test.ts", "describe('add', () => {});")

        # 4. Docs
        self.fs.create_file("README.md", "# Sample Project\nA demo project.")

    def test_creates_autonomos_meta_dir_and_project_map(self):
        """Verify initialization creates .autonomos/ and .autonomos/project-map.md."""
        self._setup_mock_project()

        meta_dir = self.workspace_root / ".autonomos"
        map_md_path = meta_dir / "project-map.md"
        map_json_path = meta_dir / "project_map.json"
        snapshot_path = meta_dir / "last_snapshot.json"

        # Initially not initialized
        self.assertFalse(self.engine.is_initialized())

        # Perform audit / generation
        pmap = self.engine.perform_full_audit(trigger="INITIAL_AUDIT")

        # Verify .autonomos/ exists
        self.assertTrue(meta_dir.exists())
        self.assertTrue(meta_dir.is_dir())

        # Verify project-map.md exists on disk
        self.assertTrue(map_md_path.exists())
        self.assertTrue(self.engine.is_initialized())

        # Verify metadata files exist
        self.assertTrue(map_json_path.exists())
        self.assertTrue(snapshot_path.exists())

    def test_map_contains_real_repository_information(self):
        """Verify project-map.md contains actual project metadata, tech stack, and structure."""
        self._setup_mock_project()

        self.engine.perform_full_audit()

        map_md_path = self.workspace_root / ".autonomos" / "project-map.md"
        content = map_md_path.read_text(encoding="utf-8")

        # Project name and metadata
        self.assertIn("Project Map: sample_project", content)
        self.assertIn("Root Path", content)

        # Tech stack
        self.assertIn("React", content)
        self.assertIn("Next.js", content)
        self.assertIn("Node.js / TypeScript / JavaScript", content)

        # Structure & Entry points
        self.assertIn("`src/index.ts`", content)
        self.assertIn("`package.json`", content)
        self.assertIn("Source Directories", content)

        # Subsystems & symbols
        self.assertIn("AppRoot", content)
        self.assertIn("add", content)

    def test_source_code_not_blindly_copied(self):
        """Verify that full function bodies / source code lines are NOT dumped into the map."""
        self._setup_mock_project()

        self.engine.perform_full_audit()

        map_md_path = self.workspace_root / ".autonomos" / "project-map.md"
        content = map_md_path.read_text(encoding="utf-8")

        # Verify raw code lines are absent from map
        self.assertNotIn("return a + b;", content)
        self.assertNotIn("return 'root';", content)

    def test_safe_repeatable_initialization(self):
        """Verify that running initialization repeatedly is safe, idempotent, and updates properly."""
        self._setup_mock_project()

        # Run 1
        pmap1 = self.engine.perform_full_audit(trigger="FIRST_RUN")
        self.assertTrue(self.engine.is_initialized())

        # Add a new file
        self.fs.create_file("src/service.ts", "export class AuthService {}")

        # Run 2
        pmap2 = self.engine.perform_full_audit(trigger="SECOND_RUN")
        self.assertTrue(self.engine.is_initialized())

        map_md_path = self.workspace_root / ".autonomos" / "project-map.md"
        content = map_md_path.read_text(encoding="utf-8")
        self.assertIn("AuthService", content)
        self.assertGreater(pmap2["total_files"], pmap1["total_files"])


if __name__ == "__main__":
    unittest.main()
