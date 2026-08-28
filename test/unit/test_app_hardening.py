"""Unit tests for Stage 16 real-world hardening: search, storage, export/import, versioning, and crash recovery."""
import json
import os
import shutil
import tempfile
import unittest
import zipfile

from app.application import AutonomOSApp
from core.enums import ArtifactType, TaskStatus
from core.models import WorkerManifest


class TestAppHardening(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.temp_dir, "hardening.db")
        self.app = AutonomOSApp.with_sqlite(self.db_path)

        self.project_dict = self.app.projects.create_project(
            name="Hardening Project",
            root_path=self.temp_dir,
            description="Testing real-world resilience, export/import, search, and storage",
        )
        self.project_id = self.project_dict["id"]

        # Register standard workers
        for wid in ["worker.programmer", "worker.tester", "worker.researcher", "worker.manager"]:
            manifest = WorkerManifest(
                id=wid,
                name=wid.replace("worker.", "").title(),
                role="Specialist",
                description="Worker description",
                version="1.0.0",
                capabilities=["code", "test"],
            )
            self.app._runtime.store.save_worker(manifest)

    def tearDown(self):
        self.app.close()
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_global_project_search(self):
        # Create searchable tasks
        task1 = self.app.tasks.create_task(
            project_id=self.project_id,
            title="Implement OAuth authentication flow",
            objective="Add Google & GitHub login handlers",
        )
        task2 = self.app.tasks.create_task(
            project_id=self.project_id,
            title="Database migration script",
            objective="Migrate SQLite tables to version 16",
        )

        # Create searchable artifact
        self.app._runtime.artifacts.register_artifact(
            project_id=self.project_id,
            task_id=task1["id"],
            worker_id="worker.programmer",
            artifact_type=ArtifactType.REPORT,
            relative_path="docs/architecture_decision_oauth.md",
            description="Architecture decision report for OAuth 2.0 PKCE",
            content="# OAuth Decision\nSelected PKCE for single-page and mobile auth.",
            base_dir=self.temp_dir,
        )

        # Search for 'OAuth'
        results = self.app.search.search(self.project_id, "OAuth")
        self.assertGreaterEqual(len(results), 2)
        kinds = [r["kind"] for r in results]
        self.assertIn("TASK", kinds)
        self.assertIn("ARTIFACT", kinds)

        # Search for 'Migration'
        mig_results = self.app.search.search(self.project_id, "migration")
        self.assertEqual(len(mig_results), 1)
        self.assertEqual(mig_results[0]["title"], "Database migration script")

    def test_storage_inspection(self):
        task = self.app.tasks.create_task(
            project_id=self.project_id,
            title="Storage Test Task",
            objective="Write source code",
        )

        # Register an artifact with content to generate disk usage
        self.app._runtime.artifacts.register_artifact(
            project_id=self.project_id,
            task_id=task["id"],
            worker_id="worker.programmer",
            artifact_type=ArtifactType.FILE,
            relative_path="src/auth.py",
            description="Auth module",
            content="def authenticate(user, token):\n    return True\n" * 10,
            base_dir=self.temp_dir,
        )

        storage_info = self.app.storage.get_storage_info(self.project_id)
        self.assertEqual(storage_info["project_id"], self.project_id)
        self.assertEqual(os.path.realpath(storage_info["root_path"]), os.path.realpath(self.temp_dir))
        self.assertIn("artifacts_size_bytes", storage_info)
        self.assertGreater(storage_info["total_size_bytes"], 0)
        self.assertGreaterEqual(storage_info["artifact_count"], 1)

    def test_safe_project_bundle_export_and_import(self):
        # Create test task and artifact
        task = self.app.tasks.create_task(
            project_id=self.project_id,
            title="Exportable Task",
            objective="Verify portable packaging",
        )
        self.app._runtime.artifacts.register_artifact(
            project_id=self.project_id,
            task_id=task["id"],
            worker_id="worker.programmer",
            artifact_type=ArtifactType.REPORT,
            relative_path="reports/audit_report.md",
            description="Audit report",
            content="# Security Audit\nAll invariants passed.",
            base_dir=self.temp_dir,
        )

        # Set a secret key in secret store to verify secrets are NEVER exported
        self.app.providers.set_provider_key("openrouter", "sk-secret-key-12345")

        zip_export_path = os.path.join(self.temp_dir, "export_bundle.zip")
        export_res = self.app.export_import.export_project_bundle(self.project_id, zip_export_path)

        self.assertTrue(export_res["exported"])
        self.assertTrue(os.path.exists(zip_export_path))

        # Inspect zip contents
        with zipfile.ZipFile(zip_export_path, "r") as zf:
            manifest_raw = zf.read("manifest.json").decode("utf-8")
            manifest = json.loads(manifest_raw)

            # Validate no secrets in manifest
            self.assertFalse(manifest.get("has_secrets", True))
            self.assertNotIn("sk-secret-key-12345", manifest_raw)

            # Validate artifact inclusion
            self.assertIn("artifacts/reports/audit_report.md", zf.namelist())

        # Test Import into a fresh project directory
        import_dir = os.path.join(self.temp_dir, "imported_project")
        import_res = self.app.export_import.import_project_bundle(zip_export_path, import_dir)

        self.assertTrue(import_res["imported"])
        self.assertGreaterEqual(import_res["tasks_imported"], 1)
        self.assertGreaterEqual(import_res["artifacts_imported"], 1)

    def test_version_and_compatibility(self):
        version_info = self.app.version.get_system_version()
        self.assertEqual(version_info["application_version"], "1.0.0")
        self.assertEqual(version_info["api_version"], "1.0")
        self.assertEqual(version_info["schema_version"], 16)
        self.assertTrue(version_info["is_compatible"])

        # Test compatible client version
        comp = self.app.version.check_compatibility("1.0.2")
        self.assertTrue(comp["is_compatible"])

        # Test incompatible client major version
        incomp = self.app.version.check_compatibility("2.0.0")
        self.assertFalse(incomp["is_compatible"])
        self.assertIn("Incompatible client version", incomp["compatibility_message"])

    def test_backend_restart_and_persistence_recovery(self):
        """Simulate backend crash & restart: persisted SQLite state restored cleanly."""
        # 1. Create a task in first session
        task = self.app.tasks.create_task(
            project_id=self.project_id,
            title="Persistent Task",
            objective="Verify state survives backend crash",
        )
        task_id = task["id"]
        self.app.tasks.cancel_task(task_id, reason="Testing cancellation before restart")

        # 2. Close first app instance (simulating shutdown/crash)
        self.app.close()

        # 3. Reopen new app instance from same SQLite database file
        recovered_app = AutonomOSApp.with_sqlite(self.db_path)
        try:
            recovered_task = recovered_app.tasks.get_task(task_id)
            self.assertEqual(recovered_task["id"], task_id)
            self.assertEqual(recovered_task["status"], "CANCELLED")
            self.assertEqual(recovered_task["title"], "Persistent Task")
        finally:
            recovered_app.close()


if __name__ == "__main__":
    unittest.main()
