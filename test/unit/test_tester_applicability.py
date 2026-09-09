from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch
from typing import Any, Optional

from core.tester import (
    ApplicabilityLevel,
    ApplicableTestCategory,
    CategoryApplicability,
    ChangeCategory,
    PresenceAssessment,
    PresenceStatus,
    SurfaceAssessment,
    TestApplicabilityClassifier,
    TestApplicabilityReport,
    TestCategory,
    TestContext,
    TestScope,
    TestSurface,
    TestingCapability,
    TesterExecution,
    TesterLineageError,
    TesterValidationError,
    TesterWorkOrder,
    new_execution_id,
    new_work_order_id,
)


class TestTesterApplicability(unittest.TestCase):
    """
    Validation test suite for Tester V1 Phase 3.2: Test Applicability Classification.
    Validates:
    1. Backend-only change classification
    2. Frontend-only change classification
    3. Full-stack change classification
    4. API-only change classification
    5. CSS/layout change classification
    6. Animation change classification
    7. Navigation change classification
    8. Mixed changes classification
    9. Explicit Manager-required category preservation
    10. Unauthorized category boundary enforcement
    11. Unavailable runtime capability handling (BLOCKED/UNKNOWN)
    12. Incomplete context fallback
    13. Epistemic UNKNOWN classification
    14. Deterministic output and sort stability
    15. NOT_APPLICABLE as a legitimate terminal classification
    """

    def setUp(self) -> None:
        self.classifier = TestApplicabilityClassifier()
        self.project_id = "proj-applicability-test"
        self.task_id = "task-applicability-test-01"
        self.correlation_id = "corr-applicability-test-01"
        self.work_order_id = new_work_order_id()
        self.execution_id = new_execution_id()

    def _make_work_order(self, **kwargs) -> TesterWorkOrder:
        defaults = {
            "work_order_id": self.work_order_id,
            "manager_task_id": self.task_id,
            "project_id": self.project_id,
            "correlation_id": self.correlation_id,
            "objective": "Verify applicability classification.",
            "test_scope": TestScope(
                components=["CoreService"],
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

    def _make_context(self, **kwargs) -> TestContext:
        defaults = {
            "project_id": self.project_id,
            "work_order_id": self.work_order_id,
            "execution_id": self.execution_id,
            "source_revision": "rev-test-12345",
            "frontend": PresenceAssessment(status=PresenceStatus.PRESENT),
            "backend": PresenceAssessment(status=PresenceStatus.PRESENT),
            "change_categories": [ChangeCategory.FRONTEND, ChangeCategory.BACKEND],
            "changed_files": ["src/app.py"],
        }
        defaults.update(kwargs)
        return TestContext(**defaults)

    def test_backend_only_change(self) -> None:
        """Test backend-only changes result in NOT_APPLICABLE for UI/visual/animation categories."""
        wo = self._make_work_order()
        ctx = self._make_context(
            frontend=PresenceAssessment(status=PresenceStatus.ABSENT),
            backend=PresenceAssessment(status=PresenceStatus.PRESENT),
            change_categories=[ChangeCategory.BACKEND],
            changed_files=["src/backend/service.py", "backend/calculator.py"],
        )

        report = self.classifier.classify(work_order=wo, test_context=ctx)

        self.assertEqual(report.get_level(ApplicableTestCategory.FUNCTIONAL), ApplicabilityLevel.REQUIRED)
        self.assertEqual(report.get_level(ApplicableTestCategory.UI_INTERACTION), ApplicabilityLevel.NOT_APPLICABLE)
        self.assertEqual(report.get_level(ApplicableTestCategory.NAVIGATION), ApplicabilityLevel.NOT_APPLICABLE)
        self.assertEqual(report.get_level(ApplicableTestCategory.VISUAL), ApplicabilityLevel.NOT_APPLICABLE)
        self.assertEqual(report.get_level(ApplicableTestCategory.RESPONSIVE), ApplicabilityLevel.NOT_APPLICABLE)
        self.assertEqual(report.get_level(ApplicableTestCategory.ANIMATION), ApplicabilityLevel.NOT_APPLICABLE)
        self.assertEqual(report.get_level(ApplicableTestCategory.OCR), ApplicabilityLevel.NOT_APPLICABLE)
        self.assertEqual(report.get_level(ApplicableTestCategory.VIDEO), ApplicabilityLevel.NOT_APPLICABLE)
        self.assertEqual(report.get_level(ApplicableTestCategory.PERFORMANCE), ApplicabilityLevel.OPTIONAL)
        self.assertIn(report.get_level(ApplicableTestCategory.INTEGRATION), {ApplicabilityLevel.OPTIONAL, ApplicabilityLevel.REQUIRED})

    def test_frontend_only_change(self) -> None:
        """Test frontend button/interaction changes make UI_INTERACTION and FUNCTIONAL required."""
        wo = self._make_work_order()
        ctx = self._make_context(
            frontend=PresenceAssessment(status=PresenceStatus.PRESENT),
            backend=PresenceAssessment(status=PresenceStatus.ABSENT),
            change_categories=[ChangeCategory.FRONTEND],
            changed_files=["src/ui/Button.tsx", "src/components/Modal.tsx"],
            changed_components=["Button", "Modal"],
        )

        report = self.classifier.classify(work_order=wo, test_context=ctx)

        self.assertEqual(report.get_level(ApplicableTestCategory.FUNCTIONAL), ApplicabilityLevel.REQUIRED)
        self.assertEqual(report.get_level(ApplicableTestCategory.UI_INTERACTION), ApplicabilityLevel.REQUIRED)
        self.assertEqual(report.get_level(ApplicableTestCategory.VISUAL), ApplicabilityLevel.OPTIONAL)
        self.assertEqual(report.get_level(ApplicableTestCategory.NAVIGATION), ApplicabilityLevel.OPTIONAL)
        self.assertEqual(report.get_level(ApplicableTestCategory.ANIMATION), ApplicabilityLevel.NOT_APPLICABLE)
        self.assertEqual(report.get_level(ApplicableTestCategory.OCR), ApplicabilityLevel.NOT_APPLICABLE)

    def test_full_stack_change(self) -> None:
        """Test full-stack changes make FUNCTIONAL, UI_INTERACTION, and INTEGRATION required."""
        wo = self._make_work_order()
        ctx = self._make_context(
            frontend=PresenceAssessment(status=PresenceStatus.PRESENT),
            backend=PresenceAssessment(status=PresenceStatus.PRESENT),
            change_categories=[ChangeCategory.FRONTEND, ChangeCategory.BACKEND],
            changed_files=["client/App.tsx", "server/main.py"],
            project_type="FULL_STACK",
        )

        report = self.classifier.classify(work_order=wo, test_context=ctx)

        self.assertEqual(report.get_level(ApplicableTestCategory.FUNCTIONAL), ApplicabilityLevel.REQUIRED)
        self.assertEqual(report.get_level(ApplicableTestCategory.UI_INTERACTION), ApplicabilityLevel.REQUIRED)
        self.assertEqual(report.get_level(ApplicableTestCategory.INTEGRATION), ApplicabilityLevel.REQUIRED)
        self.assertEqual(report.get_level(ApplicableTestCategory.VISUAL), ApplicabilityLevel.OPTIONAL)
        self.assertEqual(report.get_level(ApplicableTestCategory.RESPONSIVE), ApplicabilityLevel.OPTIONAL)

    def test_api_only_change(self) -> None:
        """Test API-only changes make FUNCTIONAL and INTEGRATION required, UI categories not applicable."""
        wo = self._make_work_order()
        ctx = self._make_context(
            frontend=PresenceAssessment(status=PresenceStatus.ABSENT),
            backend=PresenceAssessment(status=PresenceStatus.PRESENT),
            api=PresenceAssessment(status=PresenceStatus.PRESENT),
            change_categories=[ChangeCategory.API, ChangeCategory.BACKEND],
            changed_files=["api/v1/routes.py"],
            changed_apis=["routes"],
        )

        report = self.classifier.classify(work_order=wo, test_context=ctx)

        self.assertEqual(report.get_level(ApplicableTestCategory.FUNCTIONAL), ApplicabilityLevel.REQUIRED)
        self.assertEqual(report.get_level(ApplicableTestCategory.INTEGRATION), ApplicabilityLevel.REQUIRED)
        self.assertEqual(report.get_level(ApplicableTestCategory.UI_INTERACTION), ApplicabilityLevel.NOT_APPLICABLE)
        self.assertEqual(report.get_level(ApplicableTestCategory.NAVIGATION), ApplicabilityLevel.NOT_APPLICABLE)
        self.assertEqual(report.get_level(ApplicableTestCategory.VISUAL), ApplicabilityLevel.NOT_APPLICABLE)
        self.assertEqual(report.get_level(ApplicableTestCategory.RESPONSIVE), ApplicabilityLevel.NOT_APPLICABLE)
        self.assertEqual(report.get_level(ApplicableTestCategory.ANIMATION), ApplicabilityLevel.NOT_APPLICABLE)

    def test_css_layout_change(self) -> None:
        """Test CSS/layout changes make VISUAL and RESPONSIVE required, UI_INTERACTION optional."""
        wo = self._make_work_order()
        ctx = self._make_context(
            frontend=PresenceAssessment(status=PresenceStatus.PRESENT),
            backend=PresenceAssessment(status=PresenceStatus.ABSENT),
            change_categories=[ChangeCategory.FRONTEND],
            changed_files=["styles/theme.css", "styles/layout.scss"],
        )

        report = self.classifier.classify(work_order=wo, test_context=ctx)

        self.assertEqual(report.get_level(ApplicableTestCategory.VISUAL), ApplicabilityLevel.REQUIRED)
        self.assertEqual(report.get_level(ApplicableTestCategory.RESPONSIVE), ApplicabilityLevel.REQUIRED)
        self.assertEqual(report.get_level(ApplicableTestCategory.UI_INTERACTION), ApplicabilityLevel.OPTIONAL)
        self.assertEqual(report.get_level(ApplicableTestCategory.ANIMATION), ApplicabilityLevel.NOT_APPLICABLE)
        self.assertEqual(report.get_level(ApplicableTestCategory.FUNCTIONAL), ApplicabilityLevel.OPTIONAL)

    def test_animation_change(self) -> None:
        """Test animation/transition changes make ANIMATION and VISUAL required."""
        wo = self._make_work_order()
        ctx = self._make_context(
            frontend=PresenceAssessment(status=PresenceStatus.PRESENT),
            backend=PresenceAssessment(status=PresenceStatus.ABSENT),
            change_categories=[ChangeCategory.FRONTEND],
            changed_files=["styles/animations.css", "src/components/fade_transition.tsx"],
            test_surfaces=[
                SurfaceAssessment(surface=TestSurface.ANIMATION, status=PresenceStatus.PRESENT),
            ],
        )

        report = self.classifier.classify(work_order=wo, test_context=ctx)

        self.assertEqual(report.get_level(ApplicableTestCategory.ANIMATION), ApplicabilityLevel.REQUIRED)
        self.assertEqual(report.get_level(ApplicableTestCategory.VISUAL), ApplicabilityLevel.REQUIRED)
        self.assertEqual(report.get_level(ApplicableTestCategory.FUNCTIONAL), ApplicabilityLevel.REQUIRED)

    def test_navigation_change(self) -> None:
        """Test route additions or navigation changes make NAVIGATION required."""
        wo = self._make_work_order()
        ctx = self._make_context(
            frontend=PresenceAssessment(status=PresenceStatus.PRESENT),
            backend=PresenceAssessment(status=PresenceStatus.ABSENT),
            change_categories=[ChangeCategory.FRONTEND],
            changed_files=["src/router/index.ts"],
            changed_routes=["/login", "/dashboard", "/settings"],
        )

        report = self.classifier.classify(work_order=wo, test_context=ctx)

        self.assertEqual(report.get_level(ApplicableTestCategory.NAVIGATION), ApplicabilityLevel.REQUIRED)
        self.assertEqual(report.get_level(ApplicableTestCategory.FUNCTIONAL), ApplicabilityLevel.REQUIRED)

    def test_mixed_changes(self) -> None:
        """Test mixed changes classify each category with high precision."""
        wo = self._make_work_order()
        ctx = self._make_context(
            frontend=PresenceAssessment(status=PresenceStatus.PRESENT),
            backend=PresenceAssessment(status=PresenceStatus.PRESENT),
            api=PresenceAssessment(status=PresenceStatus.PRESENT),
            change_categories=[ChangeCategory.FRONTEND, ChangeCategory.BACKEND, ChangeCategory.API],
            changed_files=["src/ui/Form.tsx", "server/api/routes.py", "db/schema.sql"],
        )

        report = self.classifier.classify(work_order=wo, test_context=ctx)

        self.assertEqual(report.get_level(ApplicableTestCategory.FUNCTIONAL), ApplicabilityLevel.REQUIRED)
        self.assertEqual(report.get_level(ApplicableTestCategory.UI_INTERACTION), ApplicabilityLevel.REQUIRED)
        self.assertEqual(report.get_level(ApplicableTestCategory.INTEGRATION), ApplicabilityLevel.REQUIRED)
        self.assertEqual(report.get_level(ApplicableTestCategory.VISUAL), ApplicabilityLevel.OPTIONAL)

    def test_explicit_manager_required_category(self) -> None:
        """Test Manager-specified categories in work_order are preserved as REQUIRED."""
        wo = self._make_work_order(
            test_categories=[TestCategory.PERFORMANCE, TestCategory.VIDEO],
        )
        ctx = self._make_context(
            frontend=PresenceAssessment(status=PresenceStatus.PRESENT),
            backend=PresenceAssessment(status=PresenceStatus.PRESENT),
            change_categories=[ChangeCategory.BACKEND],
            changed_files=["src/backend/calc.py"],
        )

        report = self.classifier.classify(work_order=wo, test_context=ctx)

        self.assertEqual(report.get_level(ApplicableTestCategory.PERFORMANCE), ApplicabilityLevel.REQUIRED)
        self.assertEqual(report.get_level(ApplicableTestCategory.VIDEO), ApplicabilityLevel.REQUIRED)
        self.assertTrue(report.get(ApplicableTestCategory.PERFORMANCE).is_manager_requested)
        self.assertTrue(report.get(ApplicableTestCategory.VIDEO).is_manager_requested)

    def test_unauthorized_category(self) -> None:
        """Test unauthorized categories are constrained to NOT_APPLICABLE and is_authorized=False."""
        # Only authorize NAVIGATE; omit CLICK, TYPE, SCREENSHOT, SCREEN_RECORDING
        wo = self._make_work_order(
            authorized_capabilities=[TestingCapability.NAVIGATE],
        )
        ctx = self._make_context(
            frontend=PresenceAssessment(status=PresenceStatus.PRESENT),
            change_categories=[ChangeCategory.FRONTEND],
            changed_files=["src/ui/Button.tsx"],
        )

        report = self.classifier.classify(work_order=wo, test_context=ctx)

        ui_app = report.get(ApplicableTestCategory.UI_INTERACTION)
        self.assertFalse(ui_app.is_authorized)
        self.assertEqual(ui_app.level, ApplicabilityLevel.NOT_APPLICABLE)

        vis_app = report.get(ApplicableTestCategory.VISUAL)
        self.assertFalse(vis_app.is_authorized)
        self.assertEqual(vis_app.level, ApplicabilityLevel.NOT_APPLICABLE)

        # NAVIGATION was authorized
        nav_app = report.get(ApplicableTestCategory.NAVIGATION)
        self.assertTrue(nav_app.is_authorized)

    def test_unavailable_runtime_capability(self) -> None:
        """Test category requiring missing runtime capability is marked UNKNOWN with blocked reason."""
        wo = self._make_work_order(
            test_categories=[TestCategory.VIDEO],
        )
        ctx = self._make_context(
            frontend=PresenceAssessment(status=PresenceStatus.PRESENT),
            change_categories=[ChangeCategory.FRONTEND],
            changed_files=["src/ui/App.tsx"],
        )

        # Runtime does NOT have SCREEN_RECORDING
        limited_runtime_caps = [TestingCapability.NAVIGATE, TestingCapability.CLICK]

        report = self.classifier.classify(
            work_order=wo,
            test_context=ctx,
            runtime_capabilities=limited_runtime_caps,
        )

        video_app = report.get(ApplicableTestCategory.VIDEO)
        self.assertFalse(video_app.is_runtime_available)
        self.assertEqual(video_app.level, ApplicabilityLevel.UNKNOWN)
        self.assertTrue(video_app.is_blocked)
        self.assertIn("SCREEN_RECORDING", video_app.blocked_reason)

    def test_incomplete_context(self) -> None:
        """Test incomplete or unknown context falls back to UNKNOWN without crashing."""
        wo = self._make_work_order()
        ctx = self._make_context(
            frontend=PresenceAssessment(status=PresenceStatus.UNKNOWN),
            backend=PresenceAssessment(status=PresenceStatus.UNKNOWN),
            change_categories=[ChangeCategory.UNKNOWN],
            changed_files=[],
        )

        report = self.classifier.classify(work_order=wo, test_context=ctx)

        # Unknown context cannot determine UI_INTERACTION or VISUAL
        self.assertEqual(report.get_level(ApplicableTestCategory.UI_INTERACTION), ApplicabilityLevel.UNKNOWN)
        self.assertEqual(report.get_level(ApplicableTestCategory.VISUAL), ApplicabilityLevel.UNKNOWN)
        self.assertEqual(report.get_level(ApplicableTestCategory.NAVIGATION), ApplicabilityLevel.UNKNOWN)

    def test_unknown_classification(self) -> None:
        """Test epistemic UNKNOWN is explicitly tracked in unknown_categories collection."""
        wo = self._make_work_order()
        ctx = self._make_context(
            frontend=PresenceAssessment(status=PresenceStatus.UNKNOWN),
            backend=PresenceAssessment(status=PresenceStatus.UNKNOWN),
            change_categories=[ChangeCategory.UNKNOWN],
            changed_files=[],
        )

        report = self.classifier.classify(work_order=wo, test_context=ctx)

        self.assertIn(ApplicableTestCategory.UI_INTERACTION, report.unknown_categories)
        self.assertIn(ApplicableTestCategory.VISUAL, report.unknown_categories)
        self.assertTrue(report.is_unknown(ApplicableTestCategory.UI_INTERACTION))

    def test_deterministic_classification(self) -> None:
        """Test repeated classification runs on identical inputs yield byte-identical outputs."""
        wo = self._make_work_order(
            test_categories=[TestCategory.FUNCTIONAL, TestCategory.PERFORMANCE],
        )
        ctx = self._make_context(
            frontend=PresenceAssessment(status=PresenceStatus.PRESENT),
            backend=PresenceAssessment(status=PresenceStatus.PRESENT),
            change_categories=[ChangeCategory.FRONTEND, ChangeCategory.BACKEND],
            changed_files=["src/app.py", "src/ui/Header.tsx"],
        )

        with patch("core.tester.contracts.applicability.utc_now", return_value="2026-09-09T12:00:00Z"):
            report1 = self.classifier.classify(work_order=wo, test_context=ctx)
            report2 = self.classifier.classify(work_order=wo, test_context=ctx)

        dict1 = report1.to_dict()
        dict2 = report2.to_dict()
        self.assertEqual(dict1, dict2)

    def test_not_applicable_categories(self) -> None:
        """Test NOT_APPLICABLE is a legitimate terminal classification that excludes categories."""
        wo = self._make_work_order()
        ctx = self._make_context(
            frontend=PresenceAssessment(status=PresenceStatus.ABSENT),
            backend=PresenceAssessment(status=PresenceStatus.PRESENT),
            change_categories=[ChangeCategory.BACKEND],
            changed_files=["src/service.py"],
        )

        report = self.classifier.classify(work_order=wo, test_context=ctx)

        # Assert not_applicable_categories contains expected items
        self.assertIn(ApplicableTestCategory.UI_INTERACTION, report.not_applicable_categories)
        self.assertIn(ApplicableTestCategory.VISUAL, report.not_applicable_categories)
        self.assertIn(ApplicableTestCategory.ANIMATION, report.not_applicable_categories)
        self.assertIn(ApplicableTestCategory.OCR, report.not_applicable_categories)

        # Ensure they are not marked required
        self.assertNotIn(ApplicableTestCategory.UI_INTERACTION, report.required_categories)
        self.assertNotIn(ApplicableTestCategory.VISUAL, report.required_categories)
        self.assertNotIn(ApplicableTestCategory.ANIMATION, report.required_categories)

    def test_execution_attachment_and_lineage(self) -> None:
        """Test attaching TestApplicabilityReport to TesterExecution with lineage validation."""
        wo = self._make_work_order()
        exec_session = self._make_execution(wo)
        ctx = self._make_context()

        report = self.classifier.classify(work_order=wo, test_context=ctx)
        exec_session.attach_test_applicability(report)
        self.assertIs(exec_session.test_applicability, report)

        # Serialization round-trip
        exec_dict = exec_session.to_dict()
        self.assertIn("test_applicability", exec_dict)
        self.assertIsNotNone(exec_dict["test_applicability"])

        restored_exec = TesterExecution.from_dict(exec_dict)
        self.assertIsNotNone(restored_exec.test_applicability)
        self.assertEqual(
            restored_exec.test_applicability.required_categories,
            report.required_categories,
        )

        # Mismatched execution rejection
        mismatched_report = TestApplicabilityReport(
            project_id=wo.project_id,
            work_order_id=wo.work_order_id,
            execution_id=new_execution_id(),
        )
        with self.assertRaises(TesterLineageError):
            exec_session.attach_test_applicability(mismatched_report)


if __name__ == "__main__":
    unittest.main()
