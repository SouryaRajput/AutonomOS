from __future__ import annotations

import json
import os
import shutil
import tempfile
import unittest

from core.enums import RiskLevel
from core.programmer.contracts.acceptance_criteria import AcceptanceCriterion
from core.programmer.contracts.codebase_understanding import (
    CodebaseUnderstanding,
    UnderstandingInsight,
)
from core.programmer.contracts.command_scope import AllowedCommand
from core.programmer.contracts.execution import ProgrammerExecution
from core.programmer.contracts.execution_context import ProgrammerExecutionContext
from core.programmer.contracts.filesystem_resolver import FilesystemBoundaryResolver
from core.programmer.contracts.identifiers import (
    IMPACT_ANALYSIS_ID_PREFIX,
    new_codebase_understanding_id,
    new_execution_id,
    new_impact_analysis_id,
    new_work_order_id,
    new_workspace_id,
    validate_impact_analysis_id,
)
from core.programmer.contracts.impact_analysis import (
    ImpactAnalysis,
    ImpactItem,
)
from core.programmer.contracts.impact_analyzer import ImpactAnalyzer
from core.programmer.contracts.verification import VerificationEvidence
from core.programmer.contracts.work_order import ProgrammerWorkOrder
from core.programmer.contracts.workspace import ProgrammerWorkspace
from core.programmer.errors import ProgrammerValidationError
from core.programmer.types import (
    AcceptanceCriterionType,
    ImpactLevel,
    ProgrammerExecutionStatus,
    UnderstandingConfidence,
    VerificationEvidenceSourceType,
)


class TestProgrammerImpactAnalysis(unittest.TestCase):
    """
    Unit test suite for PROGRAMMER V1 — PHASE 7.2: Implementation Impact Analysis.
    Validates empirical dependency tracing, boundary enforcement, and impact categorization.
    """

    def setUp(self) -> None:
        self.temp_dir = tempfile.mkdtemp(prefix="prog_p72_test_")
        self.workspace_root = os.path.join(self.temp_dir, "repo")
        os.makedirs(self.workspace_root, exist_ok=True)

        self.project_id = "proj-p72-test"
        self.manager_task_id = "mtask-72"
        self.correlation_id = "corr-72"
        self.fs_resolver = FilesystemBoundaryResolver()
        self.analyzer = ImpactAnalyzer(fs_resolver=self.fs_resolver)

    def tearDown(self) -> None:
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def _create_work_order(
        self,
        objective: str = "Implement payment processing webhook",
        allowed_paths: Optional[list[str]] = None,
        writable_paths: Optional[list[str]] = None,
        forbidden_paths: Optional[list[str]] = None,
    ) -> ProgrammerWorkOrder:
        return ProgrammerWorkOrder(
            work_order_id=new_work_order_id(),
            manager_task_id=self.manager_task_id,
            project_id=self.project_id,
            correlation_id=self.correlation_id,
            objective=objective,
            allowed_paths=allowed_paths or ["."],
            writable_paths=writable_paths or ["."],
            forbidden_paths=forbidden_paths or [],
            allowed_commands=[AllowedCommand(command="pytest", description="pytest")],
            acceptance_criteria=[
                AcceptanceCriterion(
                    criterion_id="ac-1",
                    description="Webhook succeeds",
                    criterion_type=AcceptanceCriterionType.TEST_PASS,
                )
            ],
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

    def _create_understanding(
        self,
        work_order: ProgrammerWorkOrder,
        relevant_modules: Optional[list[str]] = None,
        configuration_files: Optional[list[str]] = None,
        dependency_manifests: Optional[list[str]] = None,
    ) -> CodebaseUnderstanding:
        return CodebaseUnderstanding(
            understanding_id=new_codebase_understanding_id(),
            execution_id=new_execution_id(),
            work_order_id=work_order.work_order_id,
            project_id=work_order.project_id,
            project_type="python_project",
            project_type_confidence=UnderstandingConfidence.OBSERVED,
            languages=["python"],
            frameworks=["fastapi"],
            relevant_modules=relevant_modules or [],
            configuration_files=configuration_files or [],
            dependency_manifests=dependency_manifests or [],
        )

    # -------------------------------------------------------------------------
    # Test 1: Direct Dependency
    # -------------------------------------------------------------------------
    def test_01_direct_dependency(self) -> None:
        """Target file and interface explicitly matching objective are identified as DIRECT."""
        src_dir = os.path.join(self.workspace_root, "src")
        os.makedirs(src_dir, exist_ok=True)

        payment_py = os.path.join(src_dir, "payment.py")
        with open(payment_py, "w") as f:
            f.write("def process_payment(amount: float) -> bool:\n    return True\n")

        wo = self._create_work_order(objective="Implement payment processing logic")
        ws = self._create_workspace(wo)
        und = self._create_understanding(wo, relevant_modules=["src/payment.py"])

        analysis = self.analyzer.analyze(workspace=ws, work_order=wo, understanding=und)

        self.assertIn("src/payment.py", analysis.directly_affected_files)
        self.assertIn("process_payment", analysis.affected_interfaces)
        direct_items = analysis.get_direct_impacts()
        self.assertTrue(any(i.target == "src/payment.py" and i.level == ImpactLevel.DIRECT for i in direct_items))

    # -------------------------------------------------------------------------
    # Test 2: Indirect Dependency
    # -------------------------------------------------------------------------
    def test_02_indirect_dependency(self) -> None:
        """Downstream files importing direct targets are classified as INDIRECT."""
        src_dir = os.path.join(self.workspace_root, "src")
        os.makedirs(src_dir, exist_ok=True)

        with open(os.path.join(src_dir, "payment.py"), "w") as f:
            f.write("def process_payment(): pass\n")

        with open(os.path.join(src_dir, "checkout.py"), "w") as f:
            f.write("from src.payment import process_payment\ndef checkout(): process_payment()\n")

        wo = self._create_work_order(objective="Update payment gateway")
        ws = self._create_workspace(wo)
        und = self._create_understanding(wo, relevant_modules=["src/payment.py"])

        analysis = self.analyzer.analyze(workspace=ws, work_order=wo, understanding=und)

        self.assertIn("src/payment.py", analysis.directly_affected_files)
        self.assertIn("src/checkout.py", analysis.indirectly_affected_files)
        indirect_items = analysis.get_indirect_impacts()
        self.assertTrue(any(i.target == "src/checkout.py" and i.level == ImpactLevel.INDIRECT for i in indirect_items))

    # -------------------------------------------------------------------------
    # Test 3: Missing Reference
    # -------------------------------------------------------------------------
    def test_03_missing_reference(self) -> None:
        """Import referencing missing/unresolvable target is logged as POTENTIAL unknown."""
        src_dir = os.path.join(self.workspace_root, "src")
        os.makedirs(src_dir, exist_ok=True)

        with open(os.path.join(src_dir, "payment.py"), "w") as f:
            f.write("from missing_unresolved_gateway import GatewayClient\ndef process(): pass\n")

        wo = self._create_work_order(objective="Refactor payment gateway")
        ws = self._create_workspace(wo)
        und = self._create_understanding(wo, relevant_modules=["src/payment.py"])

        analysis = self.analyzer.analyze(workspace=ws, work_order=wo, understanding=und)

        self.assertTrue(any("missing_unresolved_gateway" in u for u in analysis.unknowns))
        potential_items = analysis.get_potential_impacts()
        self.assertTrue(any("missing_unresolved_gateway" in i.target for i in potential_items))

    # -------------------------------------------------------------------------
    # Test 4: Cross-Module Dependency (Monorepo)
    # -------------------------------------------------------------------------
    def test_04_cross_module_dependency(self) -> None:
        """Cross-package import in a monorepo identifies downstream package as INDIRECT."""
        core_dir = os.path.join(self.workspace_root, "packages", "core")
        api_dir = os.path.join(self.workspace_root, "packages", "api")
        os.makedirs(core_dir, exist_ok=True)
        os.makedirs(api_dir, exist_ok=True)

        with open(os.path.join(core_dir, "models.ts"), "w") as f:
            f.write("export interface PaymentPayload { id: string; }\n")

        with open(os.path.join(api_dir, "routes.ts"), "w") as f:
            f.write("import { PaymentPayload } from '../core/models';\nexport function handle() {}\n")

        wo = self._create_work_order(objective="Update payment models in core")
        ws = self._create_workspace(wo)
        und = self._create_understanding(wo, relevant_modules=["packages/core/models.ts"])

        analysis = self.analyzer.analyze(workspace=ws, work_order=wo, understanding=und)

        self.assertIn("packages/core/models.ts", analysis.directly_affected_files)
        self.assertIn("packages/api/routes.ts", analysis.indirectly_affected_files)
        self.assertTrue(any("packages.api" in m for m in analysis.affected_modules))

    # -------------------------------------------------------------------------
    # Test 5: Frontend / Backend Relationship
    # -------------------------------------------------------------------------
    def test_05_frontend_backend_relationship(self) -> None:
        """Modifying backend route endpoint discovers client consumer referencing the route."""
        api_dir = os.path.join(self.workspace_root, "src", "api")
        client_dir = os.path.join(self.workspace_root, "client")
        os.makedirs(api_dir, exist_ok=True)
        os.makedirs(client_dir, exist_ok=True)

        with open(os.path.join(api_dir, "payment.py"), "w") as f:
            f.write("@app.post('/api/v1/payment/checkout')\ndef checkout(): pass\n")

        with open(os.path.join(client_dir, "api_client.ts"), "w") as f:
            f.write("export const pay = () => fetch('/api/v1/payment/checkout');\n")

        wo = self._create_work_order(objective="Modify payment checkout route")
        ws = self._create_workspace(wo)
        und = self._create_understanding(wo, relevant_modules=["src/api/payment.py"])

        analysis = self.analyzer.analyze(workspace=ws, work_order=wo, understanding=und)

        self.assertIn("src/api/payment.py", analysis.directly_affected_files)
        self.assertIn("/api/v1/payment/checkout", analysis.affected_interfaces)
        self.assertIn("client/api_client.ts", analysis.indirectly_affected_files)

    # -------------------------------------------------------------------------
    # Test 6: Restricted Files (Forbidden Paths / Boundary Safety)
    # -------------------------------------------------------------------------
    def test_06_restricted_files(self) -> None:
        """References to forbidden files (.secrets/) are marked UNKNOWN without reading them."""
        secrets_dir = os.path.join(self.workspace_root, ".secrets")
        src_dir = os.path.join(self.workspace_root, "src")
        os.makedirs(secrets_dir, exist_ok=True)
        os.makedirs(src_dir, exist_ok=True)

        with open(os.path.join(secrets_dir, "vault.py"), "w") as f:
            f.write("SECRET_KEY = 'super_secret'\n")

        with open(os.path.join(src_dir, "payment.py"), "w") as f:
            f.write("import .secrets.vault\ndef pay(): pass\n")

        wo = self._create_work_order(
            objective="Update payment encryption",
            allowed_paths=["src"],
            writable_paths=["src"],
            forbidden_paths=[".secrets"],
        )
        ws = self._create_workspace(wo)
        und = self._create_understanding(wo, relevant_modules=["src/payment.py"])

        analysis = self.analyzer.analyze(workspace=ws, work_order=wo, understanding=und)

        self.assertTrue(analysis.has_unknowns())
        self.assertTrue(any(".secrets" in u for u in analysis.unknowns))
        unknown_items = analysis.get_unknown_impacts()
        self.assertTrue(any(".secrets" in i.target for i in unknown_items))

    # -------------------------------------------------------------------------
    # Test 7: Unknown Impact
    # -------------------------------------------------------------------------
    def test_07_unknown_impact(self) -> None:
        """When references or callers cannot be determined, returns empty/unknown rather than guessing."""
        src_dir = os.path.join(self.workspace_root, "src")
        os.makedirs(src_dir, exist_ok=True)

        with open(os.path.join(src_dir, "isolated_tool.py"), "w") as f:
            f.write("def run_standalone(): pass\n")

        wo = self._create_work_order(objective="Audit isolated_tool execution")
        ws = self._create_workspace(wo)
        und = self._create_understanding(wo, relevant_modules=["src/isolated_tool.py"])

        analysis = self.analyzer.analyze(workspace=ws, work_order=wo, understanding=und)

        self.assertIn("src/isolated_tool.py", analysis.directly_affected_files)
        # No indirect callers or tests exist
        self.assertEqual(len(analysis.indirectly_affected_files), 0)
        self.assertEqual(len(analysis.affected_tests), 0)

    # -------------------------------------------------------------------------
    # Test 8: Evidence Lineage
    # -------------------------------------------------------------------------
    def test_08_evidence_lineage(self) -> None:
        """Every evidence item generated has valid prefix, execution_id, and non-agent claim status."""
        src_dir = os.path.join(self.workspace_root, "src")
        os.makedirs(src_dir, exist_ok=True)

        with open(os.path.join(src_dir, "payment.py"), "w") as f:
            f.write("def charge(): pass\n")

        wo = self._create_work_order(objective="Implement payment charge")
        ws = self._create_workspace(wo)
        und = self._create_understanding(wo, relevant_modules=["src/payment.py"])

        analysis = self.analyzer.analyze(
            workspace=ws,
            work_order=wo,
            understanding=und,
            execution_id="pexec-lineage-test-1",
        )

        self.assertGreater(len(analysis.evidence), 0)
        for ev in analysis.evidence:
            self.assertTrue(ev.evidence_id.startswith("vevid-"))
            self.assertEqual(ev.execution_id, "pexec-lineage-test-1")
            self.assertEqual(ev.work_order_id, wo.work_order_id)
            self.assertEqual(ev.source_type, VerificationEvidenceSourceType.IMPACT_ANALYSIS)
            self.assertFalse(ev.is_agent_claim)

    # -------------------------------------------------------------------------
    # Test 9: Affected Tests Detection
    # -------------------------------------------------------------------------
    def test_09_affected_tests_detection(self) -> None:
        """Test files importing or named after direct target are classified under affected_tests."""
        src_dir = os.path.join(self.workspace_root, "src")
        tests_dir = os.path.join(self.workspace_root, "tests")
        os.makedirs(src_dir, exist_ok=True)
        os.makedirs(tests_dir, exist_ok=True)

        with open(os.path.join(src_dir, "payment.py"), "w") as f:
            f.write("def process_payment(): pass\n")

        with open(os.path.join(tests_dir, "test_payment.py"), "w") as f:
            f.write("from src.payment import process_payment\ndef test_p(): pass\n")

        wo = self._create_work_order(objective="Implement payment processing")
        ws = self._create_workspace(wo)
        und = self._create_understanding(wo, relevant_modules=["src/payment.py"])

        analysis = self.analyzer.analyze(workspace=ws, work_order=wo, understanding=und)

        self.assertIn("tests/test_payment.py", analysis.affected_tests)
        self.assertNotIn("tests/test_payment.py", analysis.directly_affected_files)

    # -------------------------------------------------------------------------
    # Test 10: Affected Configuration & Manifests
    # -------------------------------------------------------------------------
    def test_10_affected_configuration_and_manifests(self) -> None:
        """Objectives mentioning dependencies or env variables discover config/manifest impacts."""
        with open(os.path.join(self.workspace_root, "pyproject.toml"), "w") as f:
            f.write("[project]\nname='app'\n")
        with open(os.path.join(self.workspace_root, ".env.example"), "w") as f:
            f.write("REDIS_HOST=localhost\n")

        wo = self._create_work_order(objective="Install redis dependency and configure redis env settings")
        ws = self._create_workspace(wo)
        und = self._create_understanding(
            wo,
            configuration_files=[".env.example"],
            dependency_manifests=["pyproject.toml"],
        )

        analysis = self.analyzer.analyze(workspace=ws, work_order=wo, understanding=und)

        self.assertIn("pyproject.toml", analysis.dependency_impacts)
        self.assertIn(".env.example", analysis.affected_configuration)

    # -------------------------------------------------------------------------
    # Test 11: Serialization Roundtrip
    # -------------------------------------------------------------------------
    def test_11_serialization_roundtrip(self) -> None:
        """to_dict() / from_dict() and JSON serialization roundtrip preserves all analysis fields."""
        anal_id = new_impact_analysis_id()
        exec_id = new_execution_id()
        wo_id = new_work_order_id()
        und_id = new_codebase_understanding_id()

        item = ImpactItem(
            target="src/payment.py",
            target_type="file",
            level=ImpactLevel.DIRECT,
            rationale="Primary target",
            source_reference="payment",
        )

        original = ImpactAnalysis(
            analysis_id=anal_id,
            execution_id=exec_id,
            work_order_id=wo_id,
            project_id=self.project_id,
            understanding_id=und_id,
            directly_affected_files=["src/payment.py"],
            indirectly_affected_files=["src/checkout.py"],
            affected_modules=["src.payment", "src.checkout"],
            affected_interfaces=["process_payment"],
            affected_tests=["tests/test_payment.py"],
            affected_configuration=[".env.example"],
            dependency_impacts=["pyproject.toml"],
            potential_side_effects=["Modifying payment may break checkout"],
            unknowns=["External webhook unresolvable"],
            impact_items=[item],
            confidence=UnderstandingConfidence.OBSERVED,
        )

        json_str = original.to_json()
        restored = ImpactAnalysis.from_json(json_str)

        self.assertEqual(restored.analysis_id, anal_id)
        self.assertEqual(restored.execution_id, exec_id)
        self.assertEqual(restored.work_order_id, wo_id)
        self.assertEqual(restored.understanding_id, und_id)
        self.assertEqual(restored.directly_affected_files, ["src/payment.py"])
        self.assertEqual(restored.indirectly_affected_files, ["src/checkout.py"])
        self.assertEqual(restored.confidence, UnderstandingConfidence.OBSERVED)
        self.assertEqual(len(restored.impact_items), 1)
        self.assertEqual(restored.impact_items[0].level, ImpactLevel.DIRECT)

    # -------------------------------------------------------------------------
    # Test 12: Context Convenience Entrypoint
    # -------------------------------------------------------------------------
    def test_12_context_convenience_entrypoint(self) -> None:
        """Direct analysis from ProgrammerExecutionContext."""
        os.makedirs(os.path.join(self.workspace_root, "src"), exist_ok=True)
        with open(os.path.join(self.workspace_root, "src", "payment.py"), "w") as f:
            f.write("def process(): pass\n")

        wo = self._create_work_order()
        ws = self._create_workspace(wo)
        und = self._create_understanding(wo, relevant_modules=["src/payment.py"])

        exec_session = ProgrammerExecution(
            execution_id=new_execution_id(),
            work_order_id=wo.work_order_id,
            task_id=wo.manager_task_id,
            project_id=wo.project_id,
            correlation_id=wo.correlation_id,
            status=ProgrammerExecutionStatus.RUNNING,
        )

        ctx = ProgrammerExecutionContext(
            execution_id=exec_session.execution_id,
            work_order_id=wo.work_order_id,
            workspace_id=ws.workspace_id,
            project_id=wo.project_id,
            correlation_id=wo.correlation_id,
            workspace=ws,
            work_order=wo,
            execution=exec_session,
        )

        analysis = self.analyzer.analyze_context(context=ctx, work_order=wo, understanding=und)

        self.assertEqual(analysis.execution_id, exec_session.execution_id)
        self.assertEqual(analysis.work_order_id, wo.work_order_id)
        self.assertTrue(analysis.analysis_id.startswith(IMPACT_ANALYSIS_ID_PREFIX))


if __name__ == "__main__":
    unittest.main()
