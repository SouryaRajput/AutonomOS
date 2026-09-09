from __future__ import annotations

from dataclasses import dataclass, field
import unittest
from unittest.mock import MagicMock, patch
from typing import Any, Optional

from core.tester import (
    ChangeCategory,
    FactStatus,
    PresenceAssessment,
    PresenceStatus,
    SurfaceAssessment,
    TestContext,
    TestContextBuilder,
    TestEnvironment,
    TestScope,
    TestSurface,
    TesterExecution,
    TesterExecutionStatus,
    TesterLineageError,
    TesterValidationError,
    TesterWorkOrder,
    new_execution_id,
    new_work_order_id,
)
from core.tester.types import EnvironmentType


class TestTesterContext(unittest.TestCase):
    """
    Validation test suite for Tester V1 Phase 3.1: Test Context & Change Understanding.
    Validates:
    1. Backend-only change classification
    2. Frontend-only change classification
    3. Full-stack change classification
    4. API change classification
    5. Configuration-only change classification
    6. Test-only change classification
    7. Mixed multi-category change classification
    8. Unknown / incomplete change information handling
    9. ProgrammerResult unavailable handling
    10. ProductArtifact metadata incomplete handling
    11. Causal lineage preservation and mismatch rejection
    12. Deterministic output and sort stability
    13. Strict no-execution invariant
    14. No-mutation invariant of input artifacts
    """

    def setUp(self) -> None:
        self.builder = TestContextBuilder()
        self.project_id = "proj-context-test"
        self.task_id = "task-context-test-01"
        self.correlation_id = "corr-context-test-01"
        self.work_order_id = new_work_order_id()
        self.execution_id = new_execution_id()

    def _make_work_order(self, **kwargs) -> TesterWorkOrder:
        defaults = {
            "work_order_id": self.work_order_id,
            "manager_task_id": self.task_id,
            "project_id": self.project_id,
            "correlation_id": self.correlation_id,
            "objective": "Verify change context assembly.",
            "test_scope": TestScope(
                components=["Service"],
                routes=["/test"],
                environments=["staging"],
            ),
            "source_revision": "rev-test-12345",
        }
        defaults.update(kwargs)
        return TesterWorkOrder(**defaults)

    def _make_execution(self, work_order: TesterWorkOrder, **kwargs) -> TesterExecution:
        defaults = {
            "execution_id": self.execution_id,
            "work_order_id": work_order.work_order_id,
            "task_id": work_order.manager_task_id,
            "project_id": work_order.project_id,
            "correlation_id": work_order.correlation_id,
        }
        defaults.update(kwargs)
        return TesterExecution(**defaults)

    def test_backend_only_change(self) -> None:
        """Test backend files classification and absence inference for frontend."""
        wo = self._make_work_order(test_scope=TestScope(components=["BackendService"]))
        exec_session = self._make_execution(wo)

        mock_prog_result = MagicMock()
        mock_prog_result.files_changed = ["src/backend/service.py", "backend/calculator.py"]
        mock_prog_result.files_created = []
        mock_prog_result.files_deleted = []
        mock_prog_result.metadata = {}

        ctx = self.builder.build(
            work_order=wo,
            execution=exec_session,
            programmer_result=mock_prog_result,
        )

        self.assertIn(ChangeCategory.BACKEND, ctx.change_categories)
        self.assertNotIn(ChangeCategory.FRONTEND, ctx.change_categories)
        self.assertTrue(ctx.has_backend_changes)
        self.assertFalse(ctx.has_frontend_changes)
        self.assertEqual(ctx.backend.status, PresenceStatus.PRESENT)
        self.assertEqual(ctx.backend.confidence, FactStatus.INFERENCE)
        self.assertEqual(ctx.frontend.status, PresenceStatus.ABSENT)
        self.assertEqual(ctx.frontend.confidence, FactStatus.INFERENCE)
        self.assertEqual(ctx.project_type, "BACKEND")

        logic_surface = ctx.get_surface_assessment(TestSurface.BUSINESS_LOGIC)
        self.assertIsNotNone(logic_surface)
        self.assertEqual(logic_surface.status, PresenceStatus.PRESENT)

        ui_surface = ctx.get_surface_assessment(TestSurface.UI)
        self.assertIsNotNone(ui_surface)
        self.assertEqual(ui_surface.status, PresenceStatus.ABSENT)

    def test_frontend_only_change(self) -> None:
        """Test frontend files classification and presence of UI surfaces."""
        wo = self._make_work_order(test_scope=TestScope(components=["Navigation"]))
        exec_session = self._make_execution(wo)

        mock_prog_result = MagicMock()
        mock_prog_result.files_changed = ["src/ui/Button.tsx", "src/styles/app.css"]
        mock_prog_result.files_created = ["src/components/Header.vue"]
        mock_prog_result.files_deleted = []
        mock_prog_result.metadata = {}

        ctx = self.builder.build(
            work_order=wo,
            execution=exec_session,
            programmer_result=mock_prog_result,
        )

        self.assertIn(ChangeCategory.FRONTEND, ctx.change_categories)
        self.assertNotIn(ChangeCategory.BACKEND, ctx.change_categories)
        self.assertTrue(ctx.has_frontend_changes)
        self.assertFalse(ctx.has_backend_changes)
        self.assertEqual(ctx.frontend.status, PresenceStatus.PRESENT)
        self.assertEqual(ctx.frontend.confidence, FactStatus.INFERENCE)
        self.assertEqual(ctx.backend.status, PresenceStatus.ABSENT)
        self.assertEqual(ctx.backend.confidence, FactStatus.INFERENCE)
        self.assertEqual(ctx.project_type, "FRONTEND")

        ui_surface = ctx.get_surface_assessment(TestSurface.UI)
        self.assertIsNotNone(ui_surface)
        self.assertEqual(ui_surface.status, PresenceStatus.PRESENT)
        self.assertTrue(ui_surface.is_available)

    def test_full_stack_change(self) -> None:
        """Test co-existing frontend and backend changes indicating full stack project."""
        wo = self._make_work_order(test_scope=TestScope(components=["FullApp"]))
        exec_session = self._make_execution(wo)

        mock_prog_result = MagicMock()
        mock_prog_result.files_changed = ["client/App.tsx", "server/main.py"]
        mock_prog_result.files_created = []
        mock_prog_result.files_deleted = []
        mock_prog_result.metadata = {}

        ctx = self.builder.build(
            work_order=wo,
            execution=exec_session,
            programmer_result=mock_prog_result,
        )

        self.assertIn(ChangeCategory.FRONTEND, ctx.change_categories)
        self.assertIn(ChangeCategory.BACKEND, ctx.change_categories)
        self.assertTrue(ctx.has_frontend_changes)
        self.assertTrue(ctx.has_backend_changes)
        self.assertEqual(ctx.project_type, "FULL_STACK")

        integration_surface = ctx.get_surface_assessment(TestSurface.INTEGRATION)
        self.assertIsNotNone(integration_surface)
        self.assertEqual(integration_surface.status, PresenceStatus.PRESENT)

    def test_api_change(self) -> None:
        """Test API classification and API test surface presence."""
        wo = self._make_work_order(test_scope=TestScope(routes=["/api/v1/users", "/api/v1/orders"]))
        exec_session = self._make_execution(wo)

        mock_prog_result = MagicMock()
        mock_prog_result.files_changed = ["api/routes/users.py"]
        mock_prog_result.files_created = []
        mock_prog_result.files_deleted = []
        mock_prog_result.metadata = {}

        ctx = self.builder.build(
            work_order=wo,
            execution=exec_session,
            programmer_result=mock_prog_result,
        )

        self.assertIn(ChangeCategory.API, ctx.change_categories)
        self.assertIn(ChangeCategory.BACKEND, ctx.change_categories)
        self.assertTrue(ctx.has_api_changes)
        self.assertEqual(ctx.api.status, PresenceStatus.PRESENT)
        # Explicit /api routes in work order scope elevate confidence to FACT
        self.assertEqual(ctx.api.confidence, FactStatus.FACT)

        api_surface = ctx.get_surface_assessment(TestSurface.API)
        self.assertIsNotNone(api_surface)
        self.assertEqual(api_surface.status, PresenceStatus.PRESENT)

    def test_configuration_only_change(self) -> None:
        """Test configuration files classification without code changes."""
        wo = self._make_work_order(test_scope=TestScope(components=["Config"]))
        exec_session = self._make_execution(wo)

        mock_prog_result = MagicMock()
        mock_prog_result.files_changed = ["config.yaml", ".env.staging"]
        mock_prog_result.files_created = []
        mock_prog_result.files_deleted = []
        mock_prog_result.metadata = {}

        ctx = self.builder.build(
            work_order=wo,
            execution=exec_session,
            programmer_result=mock_prog_result,
        )

        self.assertIn(ChangeCategory.CONFIGURATION, ctx.change_categories)
        self.assertTrue(ctx.has_configuration_changes)
        self.assertNotIn(ChangeCategory.FRONTEND, ctx.change_categories)
        self.assertNotIn(ChangeCategory.BACKEND, ctx.change_categories)

    def test_test_only_change(self) -> None:
        """Test detection of changes exclusively affecting test files."""
        wo = self._make_work_order(test_scope=TestScope(components=["Tests"]))
        exec_session = self._make_execution(wo)

        mock_prog_result = MagicMock()
        mock_prog_result.files_changed = ["test/unit/test_auth.py", "tests/unit/test_calc.py"]
        mock_prog_result.files_created = ["src/test_helper.spec.ts"]
        mock_prog_result.files_deleted = []
        mock_prog_result.metadata = {}

        ctx = self.builder.build(
            work_order=wo,
            execution=exec_session,
            programmer_result=mock_prog_result,
        )

        self.assertTrue(ctx.is_test_only)
        self.assertEqual(ctx.change_categories, [ChangeCategory.TEST_ONLY])

    def test_mixed_change(self) -> None:
        """Test multi-category detection across complex heterogeneous changes."""
        wo = self._make_work_order()
        exec_session = self._make_execution(wo)

        mock_prog_result = MagicMock()
        mock_prog_result.files_changed = [
            "src/components/Modal.tsx",       # Frontend
            "api/controllers/auth.py",        # API & Backend
            "db/migrations/001_init.sql",     # Database
            "package.json",                   # Dependency & Build
            "docs/ARCHITECTURE.md",           # Documentation
        ]
        mock_prog_result.files_created = []
        mock_prog_result.files_deleted = []
        mock_prog_result.metadata = {}

        ctx = self.builder.build(
            work_order=wo,
            execution=exec_session,
            programmer_result=mock_prog_result,
        )

        self.assertIn(ChangeCategory.FRONTEND, ctx.change_categories)
        self.assertIn(ChangeCategory.BACKEND, ctx.change_categories)
        self.assertIn(ChangeCategory.API, ctx.change_categories)
        self.assertIn(ChangeCategory.DATABASE, ctx.change_categories)
        self.assertIn(ChangeCategory.DEPENDENCY, ctx.change_categories)
        self.assertIn(ChangeCategory.BUILD, ctx.change_categories)
        self.assertIn(ChangeCategory.DOCUMENTATION, ctx.change_categories)

    def test_unknown_incomplete_change_information(self) -> None:
        """Test handling when change information is omitted or unknown."""
        wo = TesterWorkOrder(
            work_order_id=self.work_order_id,
            manager_task_id=self.task_id,
            project_id=self.project_id,
            correlation_id=self.correlation_id,
            objective="Minimal testing order",
            product_artifact=None,
            source_revision=None,
            test_scope=TestScope(components=["UnknownComponent"]),
        )
        exec_session = self._make_execution(wo)

        ctx = self.builder.build(
            work_order=wo,
            execution=exec_session,
        )

        self.assertIn(ChangeCategory.UNKNOWN, ctx.change_categories)
        self.assertEqual(ctx.frontend.status, PresenceStatus.UNKNOWN)
        self.assertEqual(ctx.backend.status, PresenceStatus.UNKNOWN)
        self.assertEqual(ctx.frontend.confidence, FactStatus.UNKNOWN)
        self.assertTrue(len(ctx.unknowns) > 0)
        self.assertTrue(any("source_revision" in u for u in ctx.unknowns))
        self.assertTrue(any("product_artifact" in u for u in ctx.unknowns))
        self.assertTrue(any("changed_files" in u for u in ctx.unknowns))

    def test_programmer_result_unavailable(self) -> None:
        """Test building context when programmer_result is None but WorkOrder metadata provides files."""
        wo = self._make_work_order(
            product_artifact="art-direct-wo-001",
            metadata={"changed_files": ["services/billing.py"]},
        )
        exec_session = self._make_execution(wo)

        ctx = self.builder.build(
            work_order=wo,
            execution=exec_session,
            programmer_result=None,
        )

        self.assertEqual(ctx.product_artifact_id, "art-direct-wo-001")
        self.assertEqual(ctx.changed_files, ["services/billing.py"])
        self.assertIn(ChangeCategory.BACKEND, ctx.change_categories)
        self.assertEqual(ctx.provenance.get("changed_files"), "work_order.metadata")

    def test_product_artifact_metadata_incomplete(self) -> None:
        """Test graceful construction when ProductArtifact metadata is minimal or absent."""
        wo = self._make_work_order()
        exec_session = self._make_execution(wo)

        minimal_artifact = {
            "artifact_id": "art-minimal-001",
            "build_metadata": {},
        }

        ctx = self.builder.build(
            work_order=wo,
            execution=exec_session,
            product_artifact=minimal_artifact,
        )

        self.assertEqual(ctx.product_artifact_id, "art-minimal-001")
        self.assertIsNotNone(ctx.database)
        self.assertFalse(any("product_artifact is unspecified" in u for u in ctx.unknowns))

    def test_lineage_preservation(self) -> None:
        """Test lineage preservation and rejection of mismatched entities."""
        wo = self._make_work_order()
        exec_session = self._make_execution(wo)

        ctx = self.builder.build(
            work_order=wo,
            execution=exec_session,
        )

        # Lineage verified
        self.assertEqual(ctx.project_id, wo.project_id)
        self.assertEqual(ctx.work_order_id, wo.work_order_id)
        self.assertEqual(ctx.execution_id, exec_session.execution_id)

        # Attach to execution
        exec_session.attach_test_context(ctx)
        self.assertIs(exec_session.test_context, ctx)

        # Mismatched execution rejected during build
        mismatched_exec = TesterExecution(
            execution_id=new_execution_id(),
            work_order_id=new_work_order_id(),
            task_id="task-different",
            project_id="proj-different",
            correlation_id="corr-different",
        )
        with self.assertRaises(TesterLineageError):
            self.builder.build(work_order=wo, execution=mismatched_exec)

        # Mismatched context attachment rejected
        foreign_ctx = TestContext(
            project_id=wo.project_id,
            work_order_id=wo.work_order_id,
            execution_id=new_execution_id(),
        )
        with self.assertRaises(TesterLineageError):
            exec_session.attach_test_context(foreign_ctx)

    def test_deterministic_output(self) -> None:
        """Test that multiple builds on identical inputs produce byte-identical dictionaries."""
        wo = self._make_work_order(
            constraints=["time_budget=30s"],
            product_artifact="art-det-1",
        )
        exec_session = self._make_execution(wo)

        change_dict = {
            "files_changed": ["src/z_service.py", "src/a_service.py", "src/components/Nav.tsx"],
            "files_created": ["docs/index.md"],
            "files_deleted": [],
        }
        mock_prog = MagicMock()
        mock_prog.files_changed = change_dict["files_changed"]
        mock_prog.files_created = change_dict["files_created"]
        mock_prog.files_deleted = change_dict["files_deleted"]
        mock_prog.metadata = {}

        with patch("core.tester.contracts.context.utc_now", return_value="2026-09-09T12:00:00Z"):
            ctx1 = self.builder.build(
                work_order=wo,
                execution=exec_session,
                programmer_result=mock_prog,
            )
            ctx2 = self.builder.build(
                work_order=wo,
                execution=exec_session,
                programmer_result=mock_prog,
            )

        dict1 = ctx1.to_dict()
        dict2 = ctx2.to_dict()
        self.assertEqual(dict1, dict2)

        # Check sorting stability
        self.assertEqual(dict1["changed_files"], sorted(dict1["changed_files"]))

    def test_no_execution_performed(self) -> None:
        """Strict invariant: TestContextBuilder never runs tests, browsers, or interactions."""
        wo = self._make_work_order()
        exec_session = self._make_execution(wo)

        with patch("core.tester.contracts.session.PlaywrightBrowserSession") as mock_browser, \
             patch("core.tester.contracts.interaction.InteractionEngine") as mock_interaction, \
             patch("core.tester.contracts.screenshot.ScreenshotCaptureService") as mock_ss, \
             patch("core.tester.contracts.recording.ScreenRecordingService") as mock_rec:

            ctx = self.builder.build(
                work_order=wo,
                execution=exec_session,
            )

            self.assertIsNotNone(ctx)
            mock_browser.assert_not_called()
            mock_interaction.assert_not_called()
            mock_ss.assert_not_called()
            mock_rec.assert_not_called()

    def test_no_mutation_of_product_or_source(self) -> None:
        """Invariant: Context building does not mutate input WorkOrder or ProgrammerResult."""
        wo = self._make_work_order()
        original_wo_dict = wo.to_dict()

        mock_prog = MagicMock()
        mock_prog.files_changed = ["src/app.py"]
        mock_prog.files_created = []
        mock_prog.files_deleted = []
        mock_prog.metadata = {"key": "value"}

        ctx = self.builder.build(
            work_order=wo,
            programmer_result=mock_prog,
        )

        self.assertEqual(wo.to_dict(), original_wo_dict)
        self.assertEqual(mock_prog.files_changed, ["src/app.py"])
        self.assertEqual(mock_prog.metadata, {"key": "value"})


if __name__ == "__main__":
    unittest.main()
