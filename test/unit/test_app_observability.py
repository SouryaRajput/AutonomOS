"""Unit tests for Stage 16 Deep Observability Application Services."""
import os
import shutil
import tempfile
import unittest

from app.application import AutonomOSApp
from app.dto.artifact import ArtifactDTO, EvidenceDTO
from core.enums import ArtifactType
from core.models import Artifact, Evidence, WorkerManifest
from core.verification.model import SuccessCriterion, VerificationPlan
from core.verification.types import CheckType, VerificationStatus


class TestAppObservability(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.temp_dir, "observability.db")
        self.app = AutonomOSApp.with_sqlite(self.db_path)
        self.project_dict = self.app.projects.create_project(
            name="Observability Test Project",
            root_path=self.temp_dir,
            description="Testing deep observability layer",
        )
        self.project_id = self.project_dict["id"]

        # Register workers
        for wid in ["worker.programmer", "worker.tester", "worker.researcher"]:
            manifest = WorkerManifest(
                id=wid,
                name=wid.replace("worker.", "").title(),
                role="Specialist",
                description="Worker description",
                version="1.0.0",
                capabilities=[],
            )
            self.app._runtime.store.save_worker(manifest)

    def tearDown(self):
        self.app.close()
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_artifact_registration_and_retrieval(self):
        task = self.app.tasks.create_task(
            project_id=self.project_id,
            title="Generate Auth Diff",
            objective="Add JWT verifier middleware",
        )

        # Register artifact via runtime
        art = self.app._runtime.artifacts.register_artifact(
            project_id=self.project_id,
            task_id=task["id"],
            worker_id="worker.programmer",
            artifact_type=ArtifactType.PATCH,
            relative_path="diffs/auth.diff",
            description="Auth diff patch",
            content="--- a/auth.py\n+++ b/auth.py\n@@ -1,1 +1,2 @@\n+import jwt\n",
            base_dir=self.temp_dir,
        )

        artifacts = self.app.artifacts.list_artifacts(project_id=self.project_id)
        self.assertEqual(len(artifacts), 1)
        self.assertEqual(artifacts[0]["id"], art.id)
        self.assertEqual(artifacts[0]["type"], "PATCH")
        self.assertEqual(artifacts[0]["path"], str(os.path.join(self.temp_dir, "diffs/auth.diff")))

        # Fetch artifact by ID
        fetched = self.app.artifacts.get_artifact(art.id)
        self.assertEqual(fetched["id"], art.id)
        self.assertEqual(fetched["description"], "Auth diff patch")

    def test_evidence_dto_projection(self):
        ev = Evidence(
            id="ev-101",
            task_id="t-101",
            evidence_type="UNIT_TEST_LOG",
            data="Ran 15 tests in 0.05s. ALL PASSED.",
            created_at="2026-08-27T12:00:00Z",
        )
        dto = EvidenceDTO.from_domain(ev)
        self.assertEqual(dto.id, "ev-101")
        self.assertEqual(dto.evidence_type, "UNIT_TEST_LOG")
        self.assertIn("Ran 15 tests", dto.data_preview)

        # Roundtrip
        d = dto.to_dict()
        restored = EvidenceDTO.from_dict(d)
        self.assertEqual(restored.id, dto.id)
        self.assertEqual(restored.data_preview, dto.data_preview)

    def test_deterministic_verification_engine_flow(self):
        task = self.app.tasks.create_task(
            project_id=self.project_id,
            title="Verify File Creation",
            objective="Ensure output file exists and has content",
        )

        target_file = os.path.join(self.temp_dir, "output.txt")
        with open(target_file, "w") as f:
            f.write("Deterministic Verification Content")

        criterion = SuccessCriterion(
            id="crit-file-1",
            description="output.txt must exist on disk",
            check_type=CheckType.FILE_EXISTS,
            parameters={"path": "output.txt"},
            required=True,
        )

        plan = VerificationPlan(
            id="vplan-1",
            task_id=task["id"],
            criteria=[criterion],
        )

        # Execute verification
        ver_session = self.app._runtime.verification.verify_task(
            project=self.app._runtime.projects.get_project(self.project_id),
            task=self.app._runtime.tasks.get_task(task["id"]),
            worker_id="worker.tester",
            plan=plan,
        )

        self.assertEqual(ver_session.status, VerificationStatus.PASSED)
        self.assertEqual(len(ver_session.checks), 1)
        self.assertEqual(ver_session.checks[0].check_type, CheckType.FILE_EXISTS)
        self.assertEqual(ver_session.checks[0].status.value, "PASSED")


if __name__ == "__main__":
    unittest.main()
