from __future__ import annotations

import json
import os
import shutil
import tempfile
import unittest

from core.enums import RiskLevel
from core.programmer.contracts.acceptance_criteria import AcceptanceCriterion
from core.programmer.contracts.codebase_understanding import CodebaseUnderstanding
from core.programmer.contracts.command_scope import AllowedCommand
from core.programmer.contracts.engineering_risk import (
    EngineeringRisk,
    RiskAssessment,
)
from core.programmer.contracts.escalation import ProgrammerEscalationCategory
from core.programmer.contracts.identifiers import (
    ENGINEERING_RISK_ID_PREFIX,
    RISK_ASSESSMENT_ID_PREFIX,
    new_codebase_understanding_id,
    new_execution_id,
    new_impact_analysis_id,
    new_plan_id,
    new_work_order_id,
    new_workspace_id,
    validate_engineering_risk_id,
    validate_risk_assessment_id,
)
from core.programmer.contracts.impact_analysis import ImpactAnalysis
from core.programmer.contracts.implementation_plan import (
    ImplementationPlan,
    ImplementationStep,
)
from core.programmer.contracts.risk_analyzer import EngineeringRiskAnalyzer
from core.programmer.contracts.verification import VerificationEvidence
from core.programmer.contracts.work_order import ProgrammerWorkOrder
from core.programmer.contracts.workspace import ProgrammerWorkspace
from core.programmer.errors import ProgrammerLineageError
from core.programmer.types import (
    EngineeringRiskCategory,
    ProgrammerBlockerSeverity,
    UnderstandingConfidence,
    VerificationEvidenceSourceType,
)


class TestProgrammerEngineeringRisk(unittest.TestCase):
    """
    Unit test suite for PROGRAMMER V1 — PHASE 7.4: Engineering Risk Detection.
    Verifies deterministic risk categorization, false-positive resistance,
    escalation candidate generation, and evidence lineage.
    """

    def setUp(self) -> None:
        self.temp_dir = tempfile.mkdtemp(prefix="prog_p74_test_")
        self.workspace_root = os.path.join(self.temp_dir, "repo")
        os.makedirs(self.workspace_root, exist_ok=True)

        self.project_id = "proj-p74-test"
        self.manager_task_id = "mtask-74"
        self.correlation_id = "corr-74"
        self.analyzer = EngineeringRiskAnalyzer()

    def tearDown(self) -> None:
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def _create_work_order(
        self,
        objective: str = "Add helper utility function",
        allowed_paths: Optional[list[str]] = None,
        writable_paths: Optional[list[str]] = None,
        allowed_commands: Optional[list[Any]] = None,
    ) -> ProgrammerWorkOrder:
        return ProgrammerWorkOrder(
            work_order_id=new_work_order_id(),
            manager_task_id=self.manager_task_id,
            project_id=self.project_id,
            correlation_id=self.correlation_id,
            objective=objective,
            allowed_paths=allowed_paths or ["src/", "tests/", "pyproject.toml", "migrations/"],
            writable_paths=writable_paths or ["src/", "tests/", "pyproject.toml", "migrations/"],
            forbidden_paths=[".secrets/"],
            allowed_commands=allowed_commands or [AllowedCommand(command="pytest", description="run tests")],
        )

    def _create_workspace(self, work_order: ProgrammerWorkOrder) -> ProgrammerWorkspace:
        return ProgrammerWorkspace(
            workspace_id=new_workspace_id(),
            project_id=work_order.project_id,
            work_order_id=work_order.work_order_id,
            root_path=self.workspace_root,
            allowed_paths=list(work_order.allowed_paths),
            writable_paths=list(work_order.writable_paths),
            forbidden_paths=list(work_order.forbidden_paths),
        )

    def _create_understanding(self, work_order: ProgrammerWorkOrder) -> CodebaseUnderstanding:
        return CodebaseUnderstanding(
            understanding_id=new_codebase_understanding_id(),
            execution_id=new_execution_id(),
            work_order_id=work_order.work_order_id,
            project_id=work_order.project_id,
            project_type="python_fastapi",
            languages=["python"],
            frameworks=["fastapi"],
            entry_points=["src/main.py"],
            test_locations=["tests/"],
        )

    def _create_impact_analysis(
        self,
        work_order: ProgrammerWorkOrder,
        understanding: CodebaseUnderstanding,
        directly_affected: Optional[list[str]] = None,
        indirectly_affected: Optional[list[str]] = None,
        affected_modules: Optional[list[str]] = None,
        affected_tests: Optional[list[str]] = None,
        dependency_impacts: Optional[list[str]] = None,
        unknowns: Optional[list[str]] = None,
    ) -> ImpactAnalysis:
        return ImpactAnalysis(
            analysis_id=new_impact_analysis_id(),
            execution_id=understanding.execution_id,
            work_order_id=work_order.work_order_id,
            project_id=work_order.project_id,
            understanding_id=understanding.understanding_id,
            directly_affected_files=directly_affected or ["src/utils/helpers.py"],
            indirectly_affected_files=indirectly_affected or [],
            affected_modules=affected_modules or ["utils"],
            affected_tests=affected_tests if affected_tests is not None else ["tests/test_helpers.py"],
            dependency_impacts=dependency_impacts or [],
            unknowns=unknowns or [],
        )

    def _create_plan(
        self,
        work_order: ProgrammerWorkOrder,
        impact: ImpactAnalysis,
        steps: Optional[list[ImplementationStep]] = None,
    ) -> ImplementationPlan:
        plan_steps = steps or [
            ImplementationStep(
                step_id="pstep-001",
                description=f"Implement changes for {work_order.objective}",
                target_files=list(impact.directly_affected_files),
                dependencies=[],
                verification="Run pytest tests/test_helpers.py",
            )
        ]
        return ImplementationPlan(
            plan_id=new_plan_id(),
            execution_id=impact.execution_id,
            work_order_id=work_order.work_order_id,
            project_id=work_order.project_id,
            objective=work_order.objective,
            steps=plan_steps,
            affected_files=list(impact.directly_affected_files + impact.indirectly_affected_files),
            affected_modules=list(impact.affected_modules),
            required_checks=["pytest"],
        )

    # -------------------------------------------------------------------------
    # Test Cases
    # -------------------------------------------------------------------------

    def test_dependency_change(self) -> None:
        """Verify modification to dependency manifest produces DEPENDENCY_CHANGE risk and escalation candidate."""
        wo = self._create_work_order(objective="Upgrade requests and add stripe client library")
        und = self._create_understanding(wo)
        imp = self._create_impact_analysis(
            wo,
            und,
            directly_affected=["pyproject.toml", "src/client.py"],
            dependency_impacts=["requests", "stripe"],
        )
        plan = self._create_plan(wo, imp)

        assessment = self.analyzer.analyze(wo, und, imp, plan)

        self.assertTrue(assessment.assessment_id.startswith(RISK_ASSESSMENT_ID_PREFIX))
        validate_risk_assessment_id(assessment.assessment_id)
        dep_risks = assessment.get_risks_by_category(EngineeringRiskCategory.DEPENDENCY_CHANGE)
        self.assertEqual(len(dep_risks), 1)

        risk = dep_risks[0]
        validate_engineering_risk_id(risk.risk_id)
        self.assertEqual(risk.severity, RiskLevel.HIGH)
        self.assertEqual(risk.confidence, UnderstandingConfidence.OBSERVED)
        self.assertTrue(risk.escalation_required)
        self.assertIn("pyproject.toml", risk.affected_area)

        # Verify material risk & escalation candidate generated
        self.assertTrue(assessment.has_material_risks())
        self.assertEqual(len(assessment.escalation_candidates), 1)
        self.assertEqual(assessment.escalation_candidates[0].category, ProgrammerEscalationCategory.DEPENDENCY)

    def test_api_change(self) -> None:
        """Verify modifications to public API routes trigger PUBLIC_API_CHANGE and BREAKING_CHANGE if breaking."""
        wo = self._create_work_order(objective="Deprecate and remove /api/v1/checkout endpoint in breaking update")
        und = self._create_understanding(wo)
        imp = self._create_impact_analysis(
            wo,
            und,
            directly_affected=["src/api/routes/checkout.py"],
            indirectly_affected=["src/api/client.py", "src/frontend/bridge.py"],
            affected_modules=["api"],
        )
        plan = self._create_plan(wo, imp)

        assessment = self.analyzer.analyze(wo, und, imp, plan)

        api_risks = assessment.get_risks_by_category(EngineeringRiskCategory.PUBLIC_API_CHANGE)
        self.assertEqual(len(api_risks), 1)
        self.assertEqual(api_risks[0].severity, RiskLevel.HIGH)
        self.assertTrue(api_risks[0].escalation_required)

        breaking_risks = assessment.get_risks_by_category(EngineeringRiskCategory.BREAKING_CHANGE)
        self.assertEqual(len(breaking_risks), 1)
        self.assertTrue(breaking_risks[0].escalation_required)

    def test_database_migration_and_data_loss(self) -> None:
        """Verify schema migrations and destructive DDL trigger DATABASE_MIGRATION and DATA_LOSS risks."""
        wo = self._create_work_order(
            objective="Drop table legacy_users and remove old columns via alembic migration",
        )
        und = self._create_understanding(wo)
        imp = self._create_impact_analysis(
            wo,
            und,
            directly_affected=["migrations/versions/002_drop_legacy_users.py"],
            affected_modules=["database"],
        )
        plan = self._create_plan(wo, imp)

        assessment = self.analyzer.analyze(wo, und, imp, plan)

        mig_risks = assessment.get_risks_by_category(EngineeringRiskCategory.DATABASE_MIGRATION)
        self.assertEqual(len(mig_risks), 1)
        self.assertEqual(mig_risks[0].severity, RiskLevel.HIGH)

        data_loss_risks = assessment.get_risks_by_category(EngineeringRiskCategory.DATA_LOSS)
        self.assertEqual(len(data_loss_risks), 1)
        self.assertEqual(data_loss_risks[0].severity, RiskLevel.CRITICAL)
        self.assertEqual(assessment.overall_risk_level, RiskLevel.CRITICAL)

        # Escalation candidates should cover architectural / data loss
        self.assertGreaterEqual(len(assessment.escalation_candidates), 1)

    def test_authentication_change(self) -> None:
        """Verify changes to security authentication/authorization modules are detected."""
        wo = self._create_work_order(objective="Update JWT session token verification and RBAC permissions")
        und = self._create_understanding(wo)
        imp = self._create_impact_analysis(
            wo,
            und,
            directly_affected=["src/auth/jwt_handler.py", "src/auth/rbac.py"],
            affected_modules=["auth"],
        )
        plan = self._create_plan(wo, imp)

        assessment = self.analyzer.analyze(wo, und, imp, plan)

        auth_risks = assessment.get_risks_by_category(EngineeringRiskCategory.AUTHENTICATION) + \
                     assessment.get_risks_by_category(EngineeringRiskCategory.AUTHORIZATION)
        self.assertGreaterEqual(len(auth_risks), 1)
        self.assertEqual(auth_risks[0].severity, RiskLevel.HIGH)
        self.assertTrue(auth_risks[0].escalation_required)
        self.assertEqual(assessment.escalation_candidates[0].category, ProgrammerEscalationCategory.PERMISSION)

    def test_large_scope(self) -> None:
        """Verify implementation plans with extensive file or module footprints are flagged as LARGE_SCOPE."""
        wo = self._create_work_order(objective="Refactor system across all components")
        und = self._create_understanding(wo)

        many_files = [f"packages/pkg_{i}/file_{i}.py" for i in range(12)]
        imp = self._create_impact_analysis(
            wo,
            und,
            directly_affected=many_files[:6],
            indirectly_affected=many_files[6:],
            affected_modules=[f"pkg_{i}" for i in range(12)],
        )

        steps = [
            ImplementationStep(
                step_id=f"pstep-{i:03d}",
                description=f"Refactor step {i}",
                target_files=[many_files[i]],
                dependencies=[] if i == 0 else [f"pstep-{(i-1):03d}"],
                verification=f"Verify step {i}",
            )
            for i in range(6)
        ]
        plan = self._create_plan(wo, imp, steps=steps)

        assessment = self.analyzer.analyze(wo, und, imp, plan)

        scope_risks = assessment.get_risks_by_category(EngineeringRiskCategory.LARGE_SCOPE)
        self.assertEqual(len(scope_risks), 1)
        self.assertEqual(scope_risks[0].severity, RiskLevel.HIGH)
        self.assertTrue(scope_risks[0].escalation_required)
        self.assertEqual(assessment.escalation_candidates[0].category, ProgrammerEscalationCategory.SCOPE)

    def test_ordinary_low_risk_task(self) -> None:
        """Verify routine low-risk tasks trigger zero material risks and no escalation candidates."""
        wo = self._create_work_order(objective="Add string formatting helper function")
        und = self._create_understanding(wo)
        imp = self._create_impact_analysis(
            wo,
            und,
            directly_affected=["src/utils/formatters.py"],
            affected_tests=["tests/test_formatters.py"],
        )
        plan = self._create_plan(wo, imp)

        assessment = self.analyzer.analyze(wo, und, imp, plan)

        self.assertFalse(assessment.has_material_risks())
        self.assertEqual(len(assessment.material_risks), 0)
        self.assertEqual(len(assessment.escalation_candidates), 0)
        self.assertEqual(assessment.overall_risk_level, RiskLevel.LOW)

    def test_unknown_risk(self) -> None:
        """Verify unresolvable dependencies in impact analysis produce UNKNOWN risk with escalation."""
        wo = self._create_work_order(objective="Integrate with external partner webhook")
        und = self._create_understanding(wo)
        imp = self._create_impact_analysis(
            wo,
            und,
            unknowns=["external_partner_encryption_key_v2"],
        )
        plan = self._create_plan(wo, imp)

        assessment = self.analyzer.analyze(wo, und, imp, plan)

        unknown_risks = assessment.get_risks_by_category(EngineeringRiskCategory.UNKNOWN)
        self.assertEqual(len(unknown_risks), 1)
        self.assertEqual(unknown_risks[0].confidence, UnderstandingConfidence.UNKNOWN)
        self.assertEqual(unknown_risks[0].affected_area, "external_partner_encryption_key_v2")
        self.assertTrue(unknown_risks[0].escalation_required)

    def test_false_positive_resistance(self) -> None:
        """Verify scary file names without empirical risk signals do not trigger false alarms."""
        # Create a file named 'danger_zone.py' with benign content
        danger_file = os.path.join(self.workspace_root, "src", "utils", "danger_zone.py")
        os.makedirs(os.path.dirname(danger_file), exist_ok=True)
        with open(danger_file, "w", encoding="utf-8") as f:
            f.write("def calculate_sum(a: int, b: int) -> int:\n    return a + b\n")

        wo = self._create_work_order(objective="Add addition helper in danger_zone.py")
        ws = self._create_workspace(wo)
        und = self._create_understanding(wo)
        imp = self._create_impact_analysis(
            wo,
            und,
            directly_affected=["src/utils/danger_zone.py"],
            affected_tests=["tests/test_danger_zone.py"],
        )
        plan = self._create_plan(wo, imp)

        assessment = self.analyzer.analyze(wo, und, imp, plan, workspace=ws)

        # Must not falsely flag SECURITY, DATA_LOSS, or DATABASE_MIGRATION
        self.assertEqual(len(assessment.get_risks_by_category(EngineeringRiskCategory.SECURITY)), 0)
        self.assertEqual(len(assessment.get_risks_by_category(EngineeringRiskCategory.DATA_LOSS)), 0)
        self.assertEqual(len(assessment.get_risks_by_category(EngineeringRiskCategory.DATABASE_MIGRATION)), 0)
        self.assertFalse(assessment.has_material_risks())
        self.assertEqual(assessment.overall_risk_level, RiskLevel.LOW)

    def test_evidence_lineage(self) -> None:
        """Verify all detected risks carry immutable VerificationEvidence with lineage."""
        wo = self._create_work_order(objective="Update dependencies in pyproject.toml")
        und = self._create_understanding(wo)
        imp = self._create_impact_analysis(wo, und, directly_affected=["pyproject.toml"])
        plan = self._create_plan(wo, imp)

        assessment = self.analyzer.analyze(wo, und, imp, plan)
        self.assertGreater(len(assessment.risks), 0)

        for risk in assessment.risks:
            self.assertGreater(len(risk.evidence), 0)
            for ev in risk.evidence:
                self.assertIsInstance(ev, VerificationEvidence)
                self.assertEqual(ev.source_type, VerificationEvidenceSourceType.RISK_ASSESSMENT)
                self.assertFalse(ev.is_agent_claim)
                self.assertEqual(ev.execution_id, plan.execution_id)

    def test_serialization_roundtrip(self) -> None:
        """Verify full to_dict/from_dict and JSON roundtrip serialization."""
        wo = self._create_work_order(objective="Upgrade dependencies in pyproject.toml")
        und = self._create_understanding(wo)
        imp = self._create_impact_analysis(wo, und, directly_affected=["pyproject.toml"])
        plan = self._create_plan(wo, imp)

        assessment = self.analyzer.analyze(wo, und, imp, plan)
        data = assessment.to_dict()
        json_str = assessment.to_json()

        restored_from_dict = RiskAssessment.from_dict(data)
        restored_from_json = RiskAssessment.from_json(json_str)

        self.assertEqual(restored_from_dict.assessment_id, assessment.assessment_id)
        self.assertEqual(restored_from_dict.overall_risk_level, assessment.overall_risk_level)
        self.assertEqual(len(restored_from_dict.risks), len(assessment.risks))
        self.assertEqual(len(restored_from_dict.material_risks), len(assessment.material_risks))

        self.assertEqual(restored_from_json.assessment_id, assessment.assessment_id)
        self.assertEqual(len(restored_from_json.escalation_candidates), len(assessment.escalation_candidates))


if __name__ == "__main__":
    unittest.main()
