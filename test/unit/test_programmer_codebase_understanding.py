from __future__ import annotations

import json
import os
import shutil
import tempfile
import unittest

from core.enums import RiskLevel
from core.programmer.contracts.acceptance_criteria import AcceptanceCriterion
from core.programmer.contracts.codebase_explorer import CodebaseExplorer
from core.programmer.contracts.codebase_understanding import (
    CodebaseUnderstanding,
    UnderstandingInsight,
)
from core.programmer.contracts.command_resolver import (
    CommandBoundaryResolver,
    CommandRequest,
)
from core.programmer.contracts.command_scope import AllowedCommand
from core.programmer.contracts.execution import ProgrammerExecution
from core.programmer.contracts.execution_context import ProgrammerExecutionContext
from core.programmer.contracts.filesystem_resolver import FilesystemBoundaryResolver
from core.programmer.contracts.identifiers import (
    UNDERSTANDING_ID_PREFIX,
    new_codebase_understanding_id,
    new_execution_id,
    new_work_order_id,
    new_workspace_id,
    validate_codebase_understanding_id,
)
from core.programmer.contracts.verification import VerificationEvidence
from core.programmer.contracts.work_order import ProgrammerWorkOrder
from core.programmer.contracts.workspace import ProgrammerWorkspace
from core.programmer.errors import InvalidProgrammerIdError, ProgrammerValidationError
from core.programmer.types import (
    AcceptanceCriterionType,
    FilesystemOperation,
    ProgrammerExecutionStatus,
    UnderstandingConfidence,
    VerificationEvidenceSourceType,
)


class TestProgrammerCodebaseUnderstanding(unittest.TestCase):
    """
    Unit test suite for PROGRAMMER V1 — PHASE 7.1: Codebase Understanding.
    Validates focused, bounded, and epistemically-calibrated repository exploration.
    """

    def setUp(self) -> None:
        self.temp_dir = tempfile.mkdtemp(prefix="prog_p71_test_")
        self.workspace_root = os.path.join(self.temp_dir, "repo")
        os.makedirs(self.workspace_root, exist_ok=True)

        self.project_id = "proj-p71-test"
        self.manager_task_id = "mtask-71"
        self.correlation_id = "corr-71"
        self.fs_resolver = FilesystemBoundaryResolver()
        self.cmd_resolver = CommandBoundaryResolver()
        self.explorer = CodebaseExplorer(
            fs_resolver=self.fs_resolver,
            command_resolver=self.cmd_resolver,
        )

    def tearDown(self) -> None:
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def _create_work_order(
        self,
        objective: str = "Implement payment webhook processing service",
        allowed_paths: Optional[list[str]] = None,
        writable_paths: Optional[list[str]] = None,
        forbidden_paths: Optional[list[str]] = None,
        allowed_commands: Optional[list[AllowedCommand]] = None,
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
            allowed_commands=allowed_commands or [
                AllowedCommand(command="pytest", description="Run pytest test suite")
            ],
            acceptance_criteria=[
                AcceptanceCriterion(
                    criterion_id="ac-1",
                    description="Unit tests pass",
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

    # -------------------------------------------------------------------------
    # Test 1: Simple Repository (Python / FastAPI)
    # -------------------------------------------------------------------------
    def test_01_simple_repository(self) -> None:
        """Explores standard Python repository with pyproject.toml, src, tests, entry points."""
        os.makedirs(os.path.join(self.workspace_root, "src"), exist_ok=True)
        os.makedirs(os.path.join(self.workspace_root, "tests"), exist_ok=True)

        pyproject_content = """[project]
name = "payment-svc"
version = "0.1.0"
dependencies = [
    "fastapi>=0.100.0",
    "uvicorn>=0.23.0"
]
"""
        with open(os.path.join(self.workspace_root, "pyproject.toml"), "w") as f:
            f.write(pyproject_content)

        with open(os.path.join(self.workspace_root, "src", "app.py"), "w") as f:
            f.write("def create_app():\n    return None\n")

        with open(os.path.join(self.workspace_root, "src", "payment.py"), "w") as f:
            f.write("def process_payment(amount: float) -> bool:\n    return True\n")

        with open(os.path.join(self.workspace_root, "tests", "test_payment.py"), "w") as f:
            f.write("def test_payment():\n    assert True\n")

        with open(os.path.join(self.workspace_root, "pytest.ini"), "w") as f:
            f.write("[pytest]\npython_files = test_*.py\n")

        wo = self._create_work_order(objective="Implement payment processing webhook")
        ws = self._create_workspace(wo)

        understanding = self.explorer.explore(workspace=ws, work_order=wo)

        # Core assertions
        self.assertEqual(understanding.project_type, "python_project")
        self.assertEqual(understanding.project_type_confidence, UnderstandingConfidence.OBSERVED)
        self.assertIn("python", understanding.languages)
        self.assertIn("fastapi", understanding.frameworks)
        self.assertIn("pyproject.toml", understanding.dependency_manifests)
        self.assertIn("src", understanding.important_directories)
        self.assertIn("tests", understanding.important_directories)
        self.assertTrue(any("app.py" in ep for ep in understanding.entry_points))
        self.assertIn("pytest.ini", understanding.configuration_files)
        self.assertIn("tests", understanding.test_locations)
        # Relevant module discovered from 'payment' keyword in objective
        self.assertTrue(any("payment.py" in m for m in understanding.relevant_modules))

        # Check observed vs inferred
        self.assertTrue(understanding.is_observed("manifest", "pyproject.toml"))
        self.assertTrue(understanding.is_observed("framework", "fastapi"))
        self.assertTrue(understanding.is_observed("language", "python"))

    # -------------------------------------------------------------------------
    # Test 2: Multi-Module Repository (Monorepo)
    # -------------------------------------------------------------------------
    def test_02_multi_module_repository(self) -> None:
        """Explores multi-package monorepo layout (packages/core, packages/api)."""
        pkg_core = os.path.join(self.workspace_root, "packages", "core")
        pkg_api = os.path.join(self.workspace_root, "packages", "api")
        os.makedirs(pkg_core, exist_ok=True)
        os.makedirs(pkg_api, exist_ok=True)

        root_pkg = {"name": "monorepo-root", "workspaces": ["packages/*"]}
        with open(os.path.join(self.workspace_root, "package.json"), "w") as f:
            json.dump(root_pkg, f)

        core_pkg = {"name": "@repo/core", "dependencies": {"react": "^18.0.0"}}
        with open(os.path.join(pkg_core, "package.json"), "w") as f:
            json.dump(core_pkg, f)

        api_pkg = {"name": "@repo/api", "dependencies": {"express": "^4.18.0"}, "main": "server.js"}
        with open(os.path.join(pkg_api, "package.json"), "w") as f:
            json.dump(api_pkg, f)

        wo = self._create_work_order(objective="Refactor packages API routes")
        ws = self._create_workspace(wo)

        understanding = self.explorer.explore(workspace=ws, work_order=wo)

        self.assertEqual(understanding.repository_structure.get("type"), "monorepo")
        self.assertTrue(understanding.is_observed("structure", "monorepo"))
        self.assertIn("packages", understanding.important_directories)
        self.assertIn("package.json", understanding.dependency_manifests)
        self.assertEqual(understanding.project_type, "node_project")

    # -------------------------------------------------------------------------
    # Test 3: Missing Metadata (Inferred from source extensions)
    # -------------------------------------------------------------------------
    def test_03_missing_metadata(self) -> None:
        """Repository with source code but no manifests or configuration; validates INFERRED tag."""
        src_dir = os.path.join(self.workspace_root, "src")
        os.makedirs(src_dir, exist_ok=True)

        with open(os.path.join(src_dir, "calc.py"), "w") as f:
            f.write("def add(a, b):\n    return a + b\n")

        wo = self._create_work_order(objective="Optimize calculator addition logic")
        ws = self._create_workspace(wo)

        understanding = self.explorer.explore(workspace=ws, work_order=wo)

        # Inferred, not observed, because no manifest exists
        self.assertEqual(understanding.project_type, "python_project")
        self.assertEqual(understanding.project_type_confidence, UnderstandingConfidence.INFERRED)
        self.assertIn("python", understanding.languages)
        self.assertTrue(understanding.is_inferred("language", "python"))
        self.assertFalse(understanding.is_observed("language", "python"))
        self.assertEqual(len(understanding.dependency_manifests), 0)
        self.assertTrue(understanding.is_unknown("framework", "none_detected"))
        self.assertTrue(any("Missing project metadata" in u for u in understanding.uncertainties))

    # -------------------------------------------------------------------------
    # Test 4: Unknown Framework
    # -------------------------------------------------------------------------
    def test_04_unknown_framework(self) -> None:
        """Dependencies contain custom/internal libraries; framework remains UNKNOWN."""
        pkg = {"name": "custom-app", "dependencies": {"my-internal-lib": "1.0.0"}}
        with open(os.path.join(self.workspace_root, "package.json"), "w") as f:
            json.dump(pkg, f)

        wo = self._create_work_order(objective="Inspect custom application")
        ws = self._create_workspace(wo)

        understanding = self.explorer.explore(workspace=ws, work_order=wo)

        self.assertEqual(understanding.project_type, "node_project")
        self.assertTrue(understanding.is_unknown("framework", "none_detected"))
        self.assertEqual(len(understanding.frameworks), 0)

    # -------------------------------------------------------------------------
    # Test 5: Incomplete Structure (Sparse / Minimal Repository)
    # -------------------------------------------------------------------------
    def test_05_incomplete_structure(self) -> None:
        """Near-empty repository with only a README; handles gracefully without crashing."""
        with open(os.path.join(self.workspace_root, "README.md"), "w") as f:
            f.write("# Empty Project\n")

        wo = self._create_work_order(objective="Bootstrap new repository")
        ws = self._create_workspace(wo)

        understanding = self.explorer.explore(workspace=ws, work_order=wo)

        self.assertEqual(understanding.project_type, "unknown")
        self.assertEqual(understanding.project_type_confidence, UnderstandingConfidence.UNKNOWN)
        self.assertTrue(len(understanding.uncertainties) > 0)
        self.assertTrue(any("entry points could not be" in u for u in understanding.uncertainties))
        self.assertTrue(any("No existing test" in u for u in understanding.uncertainties))

    # -------------------------------------------------------------------------
    # Test 6: Scope-Restricted Exploration
    # -------------------------------------------------------------------------
    def test_06_scope_restricted_exploration(self) -> None:
        """Exploration is strictly confined to allowed_paths; unpermitted directories are blocked."""
        auth_dir = os.path.join(self.workspace_root, "src", "auth")
        billing_dir = os.path.join(self.workspace_root, "src", "billing")
        os.makedirs(auth_dir, exist_ok=True)
        os.makedirs(billing_dir, exist_ok=True)

        with open(os.path.join(auth_dir, "token.py"), "w") as f:
            f.write("def create_token(): return 'jwt'\n")

        with open(os.path.join(billing_dir, "charge.py"), "w") as f:
            f.write("def charge(): pass\n")

        # Allowed paths strictly restricted to src/auth
        wo = self._create_work_order(
            objective="Update auth token expiration",
            allowed_paths=["src/auth"],
            writable_paths=["src/auth"],
        )
        ws = self._create_workspace(wo)

        understanding = self.explorer.explore(workspace=ws, work_order=wo)

        # src/auth is explored
        self.assertTrue(any("token.py" in m for m in understanding.relevant_modules))
        # src/billing/charge.py is blocked and never explored
        self.assertFalse(any("charge.py" in m for m in understanding.relevant_modules))

    # -------------------------------------------------------------------------
    # Test 7: Forbidden Path
    # -------------------------------------------------------------------------
    def test_07_forbidden_path(self) -> None:
        """Forbidden paths (.secrets, deploy) are strictly denied and never accessed."""
        secrets_dir = os.path.join(self.workspace_root, ".secrets")
        src_dir = os.path.join(self.workspace_root, "src")
        os.makedirs(secrets_dir, exist_ok=True)
        os.makedirs(src_dir, exist_ok=True)

        with open(os.path.join(secrets_dir, "api_keys.json"), "w") as f:
            f.write('{"api_key": "top_secret"}\n')

        with open(os.path.join(src_dir, "main.py"), "w") as f:
            f.write("print('hello')\n")

        wo = self._create_work_order(
            objective="Inspect application",
            allowed_paths=["src"],
            writable_paths=["src"],
            forbidden_paths=[".secrets"],
        )
        ws = self._create_workspace(wo)

        understanding = self.explorer.explore(workspace=ws, work_order=wo)

        # .secrets is not in configuration files or anywhere in understanding
        self.assertNotIn(".secrets", understanding.important_directories)
        self.assertFalse(any("api_keys" in c for c in understanding.configuration_files))

    # -------------------------------------------------------------------------
    # Test 8: Command Denial
    # -------------------------------------------------------------------------
    def test_08_command_denial(self) -> None:
        """Arbitrary shell commands requested during exploration are rejected by CommandBoundaryResolver."""
        wo = self._create_work_order(
            objective="Run tests",
            allowed_commands=[AllowedCommand(command="pytest", description="pytest suite")],
        )
        ws = self._create_workspace(wo)

        # Attempt forbidden arbitrary command
        req = CommandRequest(executable="bash", arguments=["-c", "rm -rf /"])
        decision = self.cmd_resolver.resolve(
            request=req,
            workspace=ws,
            work_order=wo,
        )

        self.assertFalse(decision.allowed)
        self.assertIn("not in allowed_commands", decision.reason)

    # -------------------------------------------------------------------------
    # Test 9: Evidence Lineage
    # -------------------------------------------------------------------------
    def test_09_evidence_lineage(self) -> None:
        """All emitted evidence records have valid IDs, non-agent claim status, and valid lineage."""
        with open(os.path.join(self.workspace_root, "requirements.txt"), "w") as f:
            f.write("flask>=2.0.0\n")

        wo = self._create_work_order(objective="Audit dependencies")
        ws = self._create_workspace(wo)

        understanding = self.explorer.explore(
            workspace=ws,
            work_order=wo,
            execution_id="pexec-evidence-lineage-1",
        )

        self.assertGreater(len(understanding.evidence), 0)
        for ev in understanding.evidence:
            self.assertTrue(ev.evidence_id.startswith("vevid-"))
            self.assertEqual(ev.execution_id, "pexec-evidence-lineage-1")
            self.assertEqual(ev.work_order_id, wo.work_order_id)
            self.assertEqual(ev.source_type, VerificationEvidenceSourceType.CODEBASE_EXPLORATION)
            self.assertFalse(ev.is_agent_claim)

    # -------------------------------------------------------------------------
    # Test 10: Deterministic Behavior
    # -------------------------------------------------------------------------
    def test_10_deterministic_behavior(self) -> None:
        """Multiple exploration runs on the identical repository yield consistent understanding."""
        os.makedirs(os.path.join(self.workspace_root, "src"), exist_ok=True)
        with open(os.path.join(self.workspace_root, "pyproject.toml"), "w") as f:
            f.write("[project]\nname='det'\nversion='1.0'\ndependencies=['django']\n")
        with open(os.path.join(self.workspace_root, "src", "app.py"), "w") as f:
            f.write("def main(): pass\n")

        wo = self._create_work_order(objective="Implement django views")
        ws = self._create_workspace(wo)

        u1 = self.explorer.explore(workspace=ws, work_order=wo, execution_id="pexec-det-1")
        u2 = self.explorer.explore(workspace=ws, work_order=wo, execution_id="pexec-det-1")

        self.assertEqual(u1.project_type, u2.project_type)
        self.assertEqual(u1.project_type_confidence, u2.project_type_confidence)
        self.assertEqual(u1.languages, u2.languages)
        self.assertEqual(u1.frameworks, u2.frameworks)
        self.assertEqual(u1.dependency_manifests, u2.dependency_manifests)
        self.assertEqual(u1.entry_points, u2.entry_points)
        self.assertEqual(u1.important_directories, u2.important_directories)

    # -------------------------------------------------------------------------
    # Test 11: Epistemic Calibration (Never Represent Inference as Fact)
    # -------------------------------------------------------------------------
    def test_11_epistemic_calibration(self) -> None:
        """Validates that OBSERVED, INFERRED, and UNKNOWN facts are strictly separated."""
        insight_obs = UnderstandingInsight(
            category="manifest",
            key="Cargo.toml",
            confidence=UnderstandingConfidence.OBSERVED,
            value="Cargo.toml",
            source_path="Cargo.toml",
            rationale="File exists on disk",
        )
        insight_inf = UnderstandingInsight(
            category="convention",
            key="naming",
            confidence=UnderstandingConfidence.INFERRED,
            value="snake_case",
            rationale="Inferred from pattern of 10 functions",
        )
        insight_unk = UnderstandingInsight(
            category="framework",
            key="none_detected",
            confidence=UnderstandingConfidence.UNKNOWN,
            value="unknown",
            rationale="No known signatures",
        )

        understanding = CodebaseUnderstanding(
            understanding_id=new_codebase_understanding_id(),
            execution_id=new_execution_id(),
            work_order_id=new_work_order_id(),
            project_id=self.project_id,
            insights=[insight_obs, insight_inf, insight_unk],
        )

        self.assertTrue(understanding.is_observed("manifest", "Cargo.toml"))
        self.assertFalse(understanding.is_inferred("manifest", "Cargo.toml"))

        self.assertTrue(understanding.is_inferred("convention", "naming"))
        self.assertFalse(understanding.is_observed("convention", "naming"))

        self.assertTrue(understanding.is_unknown("framework", "none_detected"))
        self.assertFalse(understanding.is_observed("framework", "none_detected"))

        obs_list = understanding.get_observed_insights()
        inf_list = understanding.get_inferred_insights()
        unk_list = understanding.get_unknown_insights()

        self.assertEqual(len(obs_list), 1)
        self.assertEqual(len(inf_list), 1)
        self.assertEqual(len(unk_list), 1)

    # -------------------------------------------------------------------------
    # Test 12: Objective-Driven Module Discovery
    # -------------------------------------------------------------------------
    def test_12_objective_driven_module_discovery(self) -> None:
        """Relevance ranking isolates modules specifically matching WorkOrder objective tokens."""
        src_dir = os.path.join(self.workspace_root, "src")
        os.makedirs(src_dir, exist_ok=True)

        with open(os.path.join(src_dir, "billing_service.py"), "w") as f:
            f.write("class BillingService: pass\n")
        with open(os.path.join(src_dir, "analytics_worker.py"), "w") as f:
            f.write("class AnalyticsWorker: pass\n")
        with open(os.path.join(src_dir, "inventory_tracker.py"), "w") as f:
            f.write("class InventoryTracker: pass\n")

        wo = self._create_work_order(objective="Fix credit card timeout in billing_service")
        ws = self._create_workspace(wo)

        understanding = self.explorer.explore(workspace=ws, work_order=wo)

        # billing_service.py is prioritized
        self.assertTrue(any("billing_service.py" in m for m in understanding.relevant_modules))
        # analytics_worker is not in relevant_modules
        self.assertFalse(any("analytics_worker.py" in m for m in understanding.relevant_modules))

    # -------------------------------------------------------------------------
    # Test 13: Serialization Roundtrip
    # -------------------------------------------------------------------------
    def test_13_serialization_roundtrip(self) -> None:
        """to_dict() / from_dict() and JSON serialization preserve all understanding data."""
        und_id = new_codebase_understanding_id()
        exec_id = new_execution_id()
        wo_id = new_work_order_id()

        insight = UnderstandingInsight(
            category="language",
            key="rust",
            confidence=UnderstandingConfidence.OBSERVED,
            value="rust",
            source_path="Cargo.toml",
            rationale="Directly observed Cargo.toml",
        )

        original = CodebaseUnderstanding(
            understanding_id=und_id,
            execution_id=exec_id,
            work_order_id=wo_id,
            project_id=self.project_id,
            repository_id="grepo-test",
            project_type="rust_crate",
            project_type_confidence=UnderstandingConfidence.OBSERVED,
            languages=["rust"],
            frameworks=["tokio"],
            package_managers=["cargo"],
            important_directories=["src"],
            entry_points=["src/main.rs"],
            configuration_files=["Cargo.toml"],
            test_locations=["tests"],
            dependency_manifests=["Cargo.toml"],
            relevant_modules=["src/main.rs"],
            detected_conventions={"naming": "snake_case"},
            uncertainties=["No integration tests found"],
            insights=[insight],
        )

        # JSON Roundtrip
        json_str = original.to_json()
        restored = CodebaseUnderstanding.from_json(json_str)

        self.assertEqual(restored.understanding_id, und_id)
        self.assertEqual(restored.execution_id, exec_id)
        self.assertEqual(restored.work_order_id, wo_id)
        self.assertEqual(restored.project_type, "rust_crate")
        self.assertEqual(restored.project_type_confidence, UnderstandingConfidence.OBSERVED)
        self.assertEqual(restored.languages, ["rust"])
        self.assertEqual(restored.frameworks, ["tokio"])
        self.assertEqual(restored.package_managers, ["cargo"])
        self.assertEqual(restored.entry_points, ["src/main.rs"])
        self.assertEqual(len(restored.insights), 1)
        self.assertEqual(restored.insights[0].confidence, UnderstandingConfidence.OBSERVED)

    # -------------------------------------------------------------------------
    # Test 14: ExecutionContext Convenience Entrypoint
    # -------------------------------------------------------------------------
    def test_14_context_convenience_entrypoint(self) -> None:
        """Explores from a ProgrammerExecutionContext directly."""
        with open(os.path.join(self.workspace_root, "app.py"), "w") as f:
            f.write("print('ok')\n")

        wo = self._create_work_order()
        ws = self._create_workspace(wo)

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

        understanding = self.explorer.explore_context(context=ctx, work_order=wo)

        self.assertEqual(understanding.execution_id, exec_session.execution_id)
        self.assertEqual(understanding.work_order_id, wo.work_order_id)
        self.assertTrue(understanding.understanding_id.startswith(UNDERSTANDING_ID_PREFIX))


if __name__ == "__main__":
    unittest.main()
