import json
import os
from pathlib import Path
import shutil
import tempfile
import unittest

from core.workspace.auditor import ProjectAuditor
from core.workspace.filesystem import ControlledWorkspaceFS, WorkspaceSecurityError
from core.workspace.incremental import IncrementalAuditEngine
from core.workspace.project_map import ProjectMapEngine


class TestWorkspaceUnderstanding(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp(prefix="autonomos_ws_test_")
        self.ws_path = Path(self.temp_dir)
        self.fs = ControlledWorkspaceFS(self.ws_path)
        self.auditor = ProjectAuditor(self.fs)
        self.map_engine = ProjectMapEngine(self.fs, self.auditor)
        self.incremental_engine = IncrementalAuditEngine(self.fs, self.map_engine)

        # Create sample project structure
        self.fs.create_file("package.json", json.dumps({"name": "test-app", "dependencies": {"react": "^18.0.0"}}))
        self.fs.create_file("src/auth/token_manager.ts", "export class TokenManager { createToken() {} }")
        self.fs.create_file("src/auth/auth_service.ts", "import { TokenManager } from './token_manager'; export class AuthService {}")
        self.fs.create_file("src/api/routes.ts", "import { AuthService } from '../auth/auth_service'; export function setupRoutes() {}")
        self.fs.create_file("tests/auth.test.ts", "import { AuthService } from '../src/auth/auth_service';")

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_filesystem_confinement_and_security(self):
        # 1. Valid inside workspace
        safe = self.fs.resolve_safe_path("src/auth/token_manager.ts")
        self.assertTrue(safe.exists())

        # 2. Prevent path traversal escape
        with self.assertRaises(WorkspaceSecurityError):
            self.fs.resolve_safe_path("../../etc/passwd")

        with self.assertRaises(WorkspaceSecurityError):
            self.fs.resolve_safe_path("/tmp/outside_file.txt")

        # 3. Prevent null byte attacks
        with self.assertRaises(WorkspaceSecurityError):
            self.fs.resolve_safe_path("foo\0bar.txt")

    def test_filesystem_crud_operations(self):
        # Create
        created = self.fs.create_file("docs/readme.md", "# Test Project")
        self.assertTrue(self.fs.exists("docs/readme.md"))
        self.assertEqual(self.fs.read_file("docs/readme.md"), "# Test Project")

        # Overwrite
        self.fs.overwrite_file("docs/readme.md", "# Updated Project")
        self.assertEqual(self.fs.read_file("docs/readme.md"), "# Updated Project")

        # Edit
        self.fs.edit_file("docs/readme.md", "Updated", "Super")
        self.assertEqual(self.fs.read_file("docs/readme.md"), "# Super Project")

        # Stat
        st = self.fs.stat_file("docs/readme.md")
        self.assertGreater(st["size"], 0)

        # Move
        self.fs.rename_or_move("docs/readme.md", "docs/OVERVIEW.md")
        self.assertFalse(self.fs.exists("docs/readme.md"))
        self.assertTrue(self.fs.exists("docs/OVERVIEW.md"))

        # Delete
        self.fs.delete_file("docs/OVERVIEW.md")
        self.assertFalse(self.fs.exists("docs/OVERVIEW.md"))

    def test_initial_full_project_audit(self):
        pmap = self.map_engine.perform_full_audit(trigger="TEST_INIT")
        self.assertTrue(self.map_engine.is_initialized())
        self.assertEqual(pmap["total_files"], 5)

        # Check tech stack detection
        tech = pmap["tech_stack"]
        self.assertIn("React", tech["frameworks"])

        # Check dependency graph
        files = pmap["files"]
        self.assertIn("src/auth/token_manager.ts", files["src/auth/auth_service.ts"]["dependencies"])
        self.assertIn("src/auth/auth_service.ts", files["src/auth/token_manager.ts"]["dependents"])

        # Check Markdown generation
        self.assertTrue(self.map_engine.map_md_path.exists())
        md_text = self.map_engine.map_md_path.read_text()
        self.assertIn("# Project Map:", md_text)
        self.assertIn("src/auth/auth_service.ts", md_text)

    def test_incremental_change_detection_and_impact_analysis(self):
        # 1. Perform initial audit
        self.map_engine.perform_full_audit(trigger="TEST_INIT")

        # 2. Modify token_manager.ts
        self.fs.overwrite_file("src/auth/token_manager.ts", "export class TokenManager { createToken() {} verifyToken() {} }")

        # 3. Detect changes
        changes = self.incremental_engine.detect_changes()
        self.assertTrue(changes["has_changes"])
        self.assertIn("src/auth/token_manager.ts", changes["modified"])

        # 4. Analyze impact surface
        impact = self.incremental_engine.analyze_change_impact(changes)
        # auth_service depends on token_manager, so it should be impacted!
        self.assertIn("src/auth/auth_service.ts", impact["impacted_dependents"])

        # 5. Run incremental update
        res = self.incremental_engine.check_and_update(trigger="TEST_INCREMENTAL")
        self.assertEqual(res["status"], "INCREMENTAL_AUDIT_COMPLETED")

        # Check audit history
        history = self.map_engine.get_audit_history()
        self.assertGreaterEqual(len(history), 2)
        self.assertEqual(history[0]["trigger"], "TEST_INCREMENTAL")

    def test_targeted_query_context(self):
        self.map_engine.perform_full_audit(trigger="TEST_INIT")
        ctx = self.map_engine.query_relevant_context("authentication token creation")
        self.assertGreater(ctx["total_matches"], 0)
        top_file = ctx["relevant_files"][0]["path"]
        self.assertIn("auth", top_file)


if __name__ == "__main__":
    unittest.main()
