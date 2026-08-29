import json
import os
from pathlib import Path
import shutil
import tempfile
import unittest

from core.workspace.filesystem import ControlledWorkspaceFS
from core.workspace.incremental import IncrementalAuditEngine
from core.workspace.project_map import ProjectMapEngine
from core.workspace.scanner import RepositoryScanner


class TestIncrementalAuditing(unittest.TestCase):
    """
    Focused unit tests for the Manager's Incremental Project Auditing Engine.
    Proves targeted impact analysis, selective file re-auditing, and snapshot synchronization.
    """

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp(prefix="autonomos_test_inc_")
        self.workspace_root = Path(self.temp_dir) / "app"
        self.workspace_root.mkdir()
        self.fs = ControlledWorkspaceFS(self.workspace_root)
        self.scanner = RepositoryScanner(self.fs)
        self.map_engine = ProjectMapEngine(self.fs, scanner=self.scanner)
        self.incremental_engine = IncrementalAuditEngine(self.fs, map_engine=self.map_engine, scanner=self.scanner)

    def tearDown(self):
        if os.path.exists(self.temp_dir):
            shutil.rmtree(self.temp_dir)

    def _setup_multi_module_project(self):
        # 1. Manifest
        self.fs.create_file("package.json", json.dumps({
            "name": "my-app",
            "dependencies": {"react": "^18.0.0"}
        }))

        # 2. Auth Subsystem
        self.fs.create_file("src/auth/token_manager.ts", """
export class TokenManager {
  validateToken(token: string): boolean {
    return token.length > 10;
  }
}
""")

        # 3. Dependent file importing Auth
        self.fs.create_file("src/auth/auth_service.ts", """
import { TokenManager } from './token_manager';

export class AuthService {
  private manager = new TokenManager();
  login(): boolean { return true; }
}
""")

        # 4. Auth test
        self.fs.create_file("test/auth.test.ts", """
import { AuthService } from '../src/auth/auth_service';
describe('Auth', () => {});
""")

        # 5. Unrelated Billing Subsystem
        self.fs.create_file("src/billing/invoice.ts", """
export class InvoiceService {
  createInvoice(): string { return 'inv-001'; }
}
""")
        self.fs.create_file("README.md", "# My App Documentation")

    def test_1_initial_audit_scans_project(self):
        """Proves initial run triggers initial full project audit and creates project-map.md and snapshot."""
        self._setup_multi_module_project()

        self.assertFalse(self.map_engine.is_initialized())

        res = self.incremental_engine.check_and_update(trigger="SESSION_START")

        self.assertEqual(res["status"], "FULL_AUDIT_PERFORMED")
        self.assertTrue(self.map_engine.is_initialized())
        self.assertTrue((self.workspace_root / ".autonomos" / "project-map.md").exists())
        self.assertTrue((self.workspace_root / ".autonomos" / "last_snapshot.json").exists())
        self.assertEqual(res["files_audited"], 6)

    def test_2_second_run_with_no_changes_does_not_re_audit(self):
        """Proves second run with zero changes does NOT perform another full audit."""
        self._setup_multi_module_project()

        # Run 1: Initial audit
        self.incremental_engine.check_and_update(trigger="FIRST_SESSION")

        # Run 2: Without any changes
        res2 = self.incremental_engine.check_and_update(trigger="SECOND_SESSION")

        self.assertEqual(res2["status"], "UP_TO_DATE")
        self.assertFalse(res2["changes_detected"])
        self.assertEqual(res2["files_audited"], 0)
        self.assertEqual(res2["impacted_files"], 0)

    def test_3_and_4_changed_file_triggers_targeted_auditing_only(self):
        """
        Proves modifying src/auth/token_manager.ts re-audits ONLY that file
        and its direct dependents, without reprocessing unrelated billing files.
        """
        self._setup_multi_module_project()

        # Run 1: Initial audit
        self.incremental_engine.check_and_update(trigger="INITIAL")

        # Capture initial timestamp of billing file in map
        pmap1 = self.map_engine.load_project_map()
        billing_rec_1 = pmap1["files"]["src/billing/invoice.ts"]
        t1_billing = billing_rec_1["last_audited"]

        # Modify src/auth/token_manager.ts
        self.fs.overwrite_file("src/auth/token_manager.ts", """
export class TokenManager {
  validateToken(token: string): boolean { return true; }
  refreshToken(): string { return 'new-jwt'; }
}
""")

        # Run 2: Incremental audit
        res = self.incremental_engine.check_and_update(trigger="FILE_EDIT")

        self.assertEqual(res["status"], "INCREMENTAL_AUDIT_COMPLETED")
        self.assertTrue(res["changes_detected"])

        changed_files = res["changes"]["modified"]
        self.assertIn("src/auth/token_manager.ts", changed_files)
        self.assertNotIn("src/billing/invoice.ts", changed_files)

        # Verify only affected files were re-audited
        impact = res["impact"]
        self.assertIn("src/auth/token_manager.ts", impact["files_to_reanalyze"])
        self.assertNotIn("src/billing/invoice.ts", impact["files_to_reanalyze"])

        # Verify billing file in project map was untouched
        pmap2 = self.map_engine.load_project_map()
        billing_rec_2 = pmap2["files"]["src/billing/invoice.ts"]
        self.assertEqual(billing_rec_2["last_audited"], t1_billing)

    def test_5_project_map_updated_after_changes(self):
        """Proves project-map.md and project_map.json reflect new symbols and updated dependencies."""
        self._setup_multi_module_project()

        self.incremental_engine.check_and_update(trigger="INITIAL")

        # Add a new method in token_manager
        self.fs.overwrite_file("src/auth/token_manager.ts", """
export class TokenManager {
  validateToken(): boolean { return true; }
  revokeAllTokens(): void {}
}
""")

        self.incremental_engine.check_and_update(trigger="ADD_REVOKE_METHOD")

        # Check project_map.json
        pmap = self.map_engine.load_project_map()
        auth_symbols = pmap["files"]["src/auth/token_manager.ts"]["symbols"]
        self.assertTrue(any("revokeAllTokens" in s for s in auth_symbols))

        # Check project-map.md
        md_content = (self.workspace_root / ".autonomos" / "project-map.md").read_text(encoding="utf-8")
        self.assertIn("revokeAllTokens", md_content)

    def test_6_snapshot_and_audit_history_updated(self):
        """Proves last_snapshot.json and audit history are updated with trigger and impact metrics."""
        self._setup_multi_module_project()

        self.incremental_engine.check_and_update(trigger="INITIAL")

        # Add a new file
        self.fs.create_file("src/auth/oauth.ts", "export class OAuthService {}")

        self.incremental_engine.check_and_update(trigger="NEW_OAUTH_MODULE")

        # Verify snapshot updated
        snapshot = self.map_engine.snapshot_path.read_text(encoding="utf-8")
        self.assertIn("src/auth/oauth.ts", snapshot)

        # Verify audit history
        history = self.map_engine.get_audit_history()
        self.assertGreaterEqual(len(history), 2)
        latest = history[0]
        self.assertEqual(latest["trigger"], "NEW_OAUTH_MODULE")
        self.assertIn("src/auth/oauth.ts", latest["changed_files"])
        self.assertIn("snapshot", latest["updated_map_sections"])
        self.assertIn("snapshot_created", latest)


if __name__ == "__main__":
    unittest.main()
