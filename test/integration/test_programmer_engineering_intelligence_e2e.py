from __future__ import annotations

import json
import os
import shutil
import tempfile
import unittest
from unittest.mock import MagicMock

from core.programmer.contracts.acceptance_result import AcceptanceCriterionResult
from core.programmer.contracts.coding_agent import (
    CodingAgentBackend,
    CodingAgentEvent,
    CodingAgentExecutionStatus,
    CodingAgentRequest,
    CodingAgentResult,
    MockCodingAgentBackend,
)
from core.programmer.contracts.delivery import (
    DeliveryPackage,
    DeliveryPreparer,
)
from core.programmer.contracts.diff_verifier import (
    DiffScopeVerifier,
    DiffVerification,
    UnauthorizedChange,
)
from core.programmer.contracts.engineering_risk import EngineeringRisk
from core.programmer.contracts.escalation import (
    EscalationCoordinator,
    EscalationStatus,
    ManagerEscalationResponse,
    ManagerEscalationResponseAction,
    ProgrammerEscalation,
    ProgrammerEscalationCategory,
)
from core.programmer.contracts.execution import ProgrammerExecution
from core.programmer.contracts.execution_context import ProgrammerExecutionContext
from core.programmer.contracts.execution_supervisor import (
    ExecutionSupervisor,
    PlanDeviation,
)
from core.programmer.contracts.git_model import (
    GitExecutionContext,
    GitRevision,
)
from core.programmer.contracts.identifiers import (
    new_diff_verification_id,
    new_escalation_candidate_id,
    new_execution_id,
    new_git_repository_id,
    new_git_revision_id,
    new_verification_check_id,
    new_verification_evidence_id,
    new_work_order_id,
)
from core.programmer.contracts.implementation_plan import (
    EscalationCandidate,
    ImplementationPlan,
    ImplementationStep,
)
from core.programmer.contracts.intelligence_pipeline import (
    EngineeringIntelligencePipeline,
    PreImplementationIntelligence,
)
from core.programmer.contracts.manager_bridge import ProgrammerManagerBridge
from core.programmer.contracts.programmer import Programmer
from core.programmer.contracts.provisioner import WorkspaceProvisioner
from core.programmer.contracts.result import ProgrammerResult
from core.programmer.contracts.verification import (
    VerificationCheck,
    VerificationEvidence,
    VerificationSummary,
)
from core.programmer.contracts.verification_runner import (
    VerificationRunner,
    VerificationRunnerResult,
)
from core.programmer.contracts.work_order import (
    AcceptanceCriterion,
    ProgrammerWorkOrder,
)
from core.programmer.contracts.workspace import ProgrammerWorkspace
from core.enums import RiskLevel
from core.programmer.types import (
    AcceptanceCriterionType,
    AcceptanceStatus,
    DeviationClassification,
    EngineeringRiskCategory,
    GitIsolationMode,
    ManagerDisposition,
    PathBoundaryScope,
    PlanDeviationCategory,
    PlanValidationStatus,
    ProgrammerBlockerCategory,
    ProgrammerBlockerSeverity,
    ProgrammerExecutionEventType,
    ProgrammerExecutionStatus,
    ProgrammerResultStatus,
    VerificationCheckType,
    VerificationEvidenceSourceType,
    VerificationStatus,
    VerificationSummaryStatus,
)


class TestProgrammerEngineeringIntelligenceE2E(unittest.TestCase):
    """
    End-to-End Integration Test Suite for PROGRAMMER V1 — PHASE 7.7
    Validates complete lifecycle:
    WorkOrder -> CodebaseUnderstanding -> ImpactAnalysis -> ImplementationPlan ->
    RiskAnalysis -> PlanValidation -> Escalation -> Supervision -> Verification -> Delivery.
    """

    def setUp(self) -> None:
        self.temp_dir = tempfile.mkdtemp(prefix="prog_p77_e2e_")
        self.workspace_root = os.path.join(self.temp_dir, "workspace")
        os.makedirs(os.path.join(self.workspace_root, "src", "calc"), exist_ok=True)
        os.makedirs(os.path.join(self.workspace_root, "src", "common"), exist_ok=True)
        os.makedirs(os.path.join(self.workspace_root, "tests"), exist_ok=True)
        os.makedirs(os.path.join(self.workspace_root, "config"), exist_ok=True)
        os.makedirs(os.path.join(self.workspace_root, "secrets"), exist_ok=True)

        # Baseline code files
        with open(os.path.join(self.workspace_root, "src", "calc", "calculator.py"), "w") as f:
            f.write("# Calculator Service\ndef calculate(a, b): return a + b\n")

        with open(os.path.join(self.workspace_root, "src", "calc", "models.py"), "w") as f:
            f.write("# Calc Models\nclass CalcContext: pass\n")

        with open(os.path.join(self.workspace_root, "src", "common", "formatter.py"), "w") as f:
            f.write("# Common formatter utils\ndef format_result(v): return str(v)\n")

        with open(os.path.join(self.workspace_root, "tests", "test_calc.py"), "w") as f:
            f.write("def test_calculate(): assert True\n")

        with open(os.path.join(self.workspace_root, "config", "app.json"), "w") as f:
            f.write('{"env": "development", "version": "1.0.0"}\n')

        with open(os.path.join(self.workspace_root, "secrets", "keys.json"), "w") as f:
            f.write('{"secret": "xyz"}\n')

        self.project_id = "proj-intel-e2e"
        self.bridge = ProgrammerManagerBridge()

    def tearDown(self) -> None:
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def _create_work_order(
        self,
        objective: str = "Implement calculator operations",
        allowed_paths: list[str] | None = None,
        writable_paths: list[str] | None = None,
        read_only_paths: list[str] | None = None,
        forbidden_paths: list[str] | None = None,
        allowed_commands: list[str] | None = None,
        iteration_budget: int = 3,
        dependencies: list[str] | None = None,
        acceptance_criteria: list[dict] | None = None,
    ) -> ProgrammerWorkOrder:
        wo_id = new_work_order_id()
        ac_list = []
        if acceptance_criteria is not None:
            for ac in acceptance_criteria:
                ac_list.append(AcceptanceCriterion(
                    criterion_id=ac.get("criterion_id", "ac-1"),
                    description=ac.get("description", "Tests pass"),
                    criterion_type=AcceptanceCriterionType.TEST_PASS,
                    target=ac.get("target", "tests/test_calc.py"),
                ))
        else:
            ac_list.append(AcceptanceCriterion(
                criterion_id="ac-calc-pass",
                description="Calculator tests pass with exit code 0",
                criterion_type=AcceptanceCriterionType.TEST_PASS,
                target="tests/test_calc.py",
            ))

        effective_forbidden = forbidden_paths if forbidden_paths is not None else [".env", ".git"]
        if read_only_paths is None:
            effective_read_only = [] if any("config" in f for f in effective_forbidden) else ["config/"]
        else:
            effective_read_only = read_only_paths

        return ProgrammerWorkOrder(
            work_order_id=wo_id,
            manager_task_id="mtask-p77-test",
            project_id=self.project_id,
            correlation_id="corr-p77-test",
            objective=objective,
            allowed_paths=allowed_paths if allowed_paths is not None else ["src/", "tests/", "config/"],
            writable_paths=writable_paths if writable_paths is not None else ["src/calc/", "tests/"],
            read_only_paths=effective_read_only,
            forbidden_paths=effective_forbidden,
            allowed_commands=allowed_commands or ["pytest", "pytest tests/test_calc.py -v"],
            acceptance_criteria=ac_list,
            iteration_budget=iteration_budget,
            dependencies=dependencies or [],
        )

    def _make_passing_backend(self, output: str = "Implementation successful"):
        class PassingBackend(MockCodingAgentBackend):
            def execute(self, request, event_handler=None):
                return CodingAgentResult(
                    execution_id=request.execution_id,
                    work_order_id=request.work_order_id,
                    status=CodingAgentExecutionStatus.COMPLETED,
                    output_text=output,
                )
        return PassingBackend()

    def _make_mock_diff_verifier(
        self,
        files_changed: list[str] | None = None,
        scope_status: VerificationStatus = VerificationStatus.PASS,
    ):
        mock = MagicMock()
        def _verify_workspace(workspace, baseline_snapshot=None, work_order=None, execution_id=""):
            wo_id = getattr(work_order, "work_order_id", "") or str(work_order or "")
            return DiffVerification(
                verification_id=new_diff_verification_id(),
                execution_id=execution_id or "pexec-default",
                work_order_id=wo_id,
                files_changed=list(files_changed if files_changed is not None else ["src/calc/calculator.py"]),
                files_created=[],
                files_deleted=[],
                unauthorized_changes=[],
                scope_status=scope_status,
            )
        mock.verify_workspace.side_effect = _verify_workspace
        return mock

    def _make_passing_runner_factory(self):
        def _factory(ctx: ProgrammerExecutionContext):
            mock_runner = MagicMock()
            ev_id = new_verification_evidence_id()
            test_cmd = "pytest tests/test_calc.py tests/test_auth.py -v"
            ev = VerificationEvidence(
                evidence_id=ev_id,
                execution_id=ctx.execution_id,
                work_order_id=ctx.work_order_id,
                source_type=VerificationEvidenceSourceType.TEST_RUNNER,
                description="Pytest passed with code 0",
                data={"command": test_cmd, "exit_code": 0},
                is_agent_claim=False,
            )
            chk = VerificationCheck(
                check_id=new_verification_check_id(),
                execution_id=ctx.execution_id,
                work_order_id=ctx.work_order_id,
                check_type=VerificationCheckType.TEST,
                command=test_cmd,
                status=VerificationStatus.PASS,
                exit_code=0,
                duration_ms=50.0,
                evidence=[ev_id],
            )
            mock_runner.run.return_value = VerificationRunnerResult(
                execution_id=ctx.execution_id,
                work_order_id=ctx.work_order_id,
                checks=[chk],
                evidence=[ev],
            )
            return mock_runner
        return _factory

    def _make_programmer(
        self,
        backend=None,
        verification_runner_factory=None,
        diff_verifier=None,
        intelligence_pipeline=None,
    ) -> Programmer:
        return Programmer(
            backend=backend or self._make_passing_backend(),
            verification_runner_factory=verification_runner_factory or self._make_passing_runner_factory(),
            diff_verifier=diff_verifier if diff_verifier is not None else self._make_mock_diff_verifier(),
            intelligence_pipeline=intelligence_pipeline,
        )

    # =========================================================================
    # Scenario 1: Simple task requiring minimal exploration
    # =========================================================================
    def test_01_simple_task_minimal_exploration(self) -> None:
        """Scenario 1: Simple task produces minimal exploration, valid plan, passes verification."""
        wo = self._create_work_order(
            objective="Fix typo in calculator docstring",
            writable_paths=["src/calc/calculator.py"],
        )
        programmer = self._make_programmer()

        result = programmer.execute(work_order=wo, root_path_override=self.workspace_root)
        self.assertEqual(result.status, ProgrammerResultStatus.COMPLETED)
        self.assertIn("understanding", result.metadata)
        self.assertIn("plan", result.metadata)
        self.assertIn("supervision_record", result.metadata)
        self.assertIn(result.metadata["plan_validation"]["status"], ["APPROVED", "APPROVED_WITH_WARNINGS"])

    # =========================================================================
    # Scenario 2: Multi-file feature
    # =========================================================================
    def test_02_multi_file_feature(self) -> None:
        """Scenario 2: Multi-file feature properly maps models, logic, tests in plan order."""
        wo = self._create_work_order(
            objective="Implement CalcContext model and calculation logic",
            writable_paths=["src/calc/models.py", "src/calc/calculator.py", "tests/test_calc.py"],
        )
        programmer = self._make_programmer()

        result = programmer.execute(work_order=wo, root_path_override=self.workspace_root)
        self.assertEqual(result.status, ProgrammerResultStatus.COMPLETED)
        plan_dict = result.metadata["plan"]
        self.assertTrue(len(plan_dict["steps"]) >= 2)
        step_descriptions = [s["description"].lower() for s in plan_dict["steps"]]
        self.assertTrue(any("model" in desc for desc in step_descriptions))

    # =========================================================================
    # Scenario 3: Existing architecture with reusable components
    # =========================================================================
    def test_03_existing_architecture_reusable_components(self) -> None:
        """Scenario 3: Explorer identifies reusable formatter utils in codebase context."""
        wo = self._create_work_order(
            objective="Format calculation output using formatter helpers",
            read_only_paths=["config/", "src/common/"],
        )
        programmer = self._make_programmer()

        result = programmer.execute(work_order=wo, root_path_override=self.workspace_root)
        self.assertEqual(result.status, ProgrammerResultStatus.COMPLETED)
        understanding = result.metadata["understanding"]
        self.assertEqual(understanding["project_type"], "python")
        self.assertIn("src", understanding["important_directories"])

    # =========================================================================
    # Scenario 4: Task requiring impact analysis
    # =========================================================================
    def test_04_task_requiring_impact_analysis(self) -> None:
        """Scenario 4: Impact analysis correctly correlates calculation modification with test impact."""
        wo = self._create_work_order(objective="Refactor calculator calculation interface")
        programmer = self._make_programmer()

        result = programmer.execute(work_order=wo, root_path_override=self.workspace_root)
        self.assertEqual(result.status, ProgrammerResultStatus.COMPLETED)
        impact = result.metadata["impact_analysis"]
        self.assertIn("src/calc/calculator.py", impact["directly_affected_files"])

    # =========================================================================
    # Scenario 5: Task requiring an architectural decision
    # =========================================================================
    def test_05_task_requiring_architectural_decision(self) -> None:
        """Scenario 5: Plan with architectural risk triggers Manager escalation candidates."""
        wo = self._create_work_order(objective="Re-architect storage to introduce sqlite database migration")
        programmer = self._make_programmer()

        # Execute without manager approval -> blocks
        result = programmer.execute(work_order=wo, root_path_override=self.workspace_root)
        self.assertEqual(result.status, ProgrammerResultStatus.BLOCKED)
        self.assertTrue(len(result.blockers) > 0)
        self.assertIn("escalations", result.metadata)

    # =========================================================================
    # Scenario 6: Task requiring new dependency
    # =========================================================================
    def test_06_task_requiring_new_dependency(self) -> None:
        """Scenario 6: New dependency requirement is captured in risk analysis and plan."""
        wo = self._create_work_order(
            objective="Add redis cache for calculation results",
            dependencies=["redis>=5.0.0"],
        )
        programmer = self._make_programmer()

        result = programmer.execute(work_order=wo, root_path_override=self.workspace_root)
        self.assertIn("plan", result.metadata)
        self.assertIn("redis>=5.0.0", result.metadata["plan"]["dependencies"])

    # =========================================================================
    # Scenario 7: Out-of-scope files detected & escalated
    # =========================================================================
    def test_07_task_requiring_out_of_scope_files_detected_and_escalated(self) -> None:
        """Scenario 7: Targeting forbidden or out-of-scope files escalates to Manager without auto-expansion."""
        wo = self._create_work_order(
            objective="Modify config and secret files",
            allowed_paths=["src/calc/"],
            writable_paths=["src/calc/calculator.py"],
            read_only_paths=[],
            forbidden_paths=["secrets/keys.json", ".env"],
        )
        pipeline = EngineeringIntelligencePipeline()
        real_planner = pipeline.planner

        class ScopeExpandingPlanner:
            def create_plan(self, work_order, understanding, impact_analysis, trace=None):
                plan = real_planner.create_plan(work_order, understanding, impact_analysis, trace=trace)
                cand = EscalationCandidate(
                    candidate_id=new_escalation_candidate_id(),
                    category=ProgrammerEscalationCategory.SCOPE,
                    reason="Needs access to secrets/keys.json",
                    target="secrets/keys.json",
                    requested_decision="Authorize secrets/keys.json",
                    severity=ProgrammerBlockerSeverity.HIGH,
                )
                plan.escalation_points.append(cand)
                plan.steps.append(ImplementationStep(
                    step_id="pstep-unauth",
                    description="Edit secrets",
                    target_files=["secrets/keys.json"],
                    target_modules=[],
                    rationale="Secret update",
                    dependencies=[],
                    expected_result="Updated",
                    verification="None",
                    is_escalation_candidate=True,
                ))
                return plan

        pipeline.planner = ScopeExpandingPlanner()
        programmer = self._make_programmer(intelligence_pipeline=pipeline)

        result = programmer.execute(work_order=wo, root_path_override=self.workspace_root)
        self.assertEqual(result.status, ProgrammerResultStatus.BLOCKED)
        self.assertTrue(len(result.escalations) >= 1)

    # =========================================================================
    # Scenario 8: Implementation deviates from plan
    # =========================================================================
    def test_08_task_where_implementation_deviates_from_plan(self) -> None:
        """Scenario 8: Supervisor records deviations during execution events."""
        wo = self._create_work_order(objective="Implement calculator service")

        class DeviatingBackend(MockCodingAgentBackend):
            def execute(self, request, event_handler=None):
                if event_handler:
                    # Emit file modification outside planned files
                    evt = CodingAgentEvent(
                        event_id="evt-dev-1",
                        execution_id=request.execution_id,
                        work_order_id=request.work_order_id,
                        event_type=ProgrammerExecutionEventType.FILE_OPERATION,
                        payload={"path": "src/unplanned.py", "operation": "write"},
                    )
                    event_handler(evt)
                return CodingAgentResult(
                    execution_id=request.execution_id,
                    work_order_id=request.work_order_id,
                    status=CodingAgentExecutionStatus.COMPLETED,
                    output_text="Done",
                )

        programmer = self._make_programmer(backend=DeviatingBackend())

        result = programmer.execute(work_order=wo, root_path_override=self.workspace_root)
        sup_rec = result.metadata.get("supervision_record")
        self.assertIsNotNone(sup_rec)
        self.assertTrue(len(sup_rec["deviations"]) >= 1)

    # =========================================================================
    # Scenario 9: Supervisor detects new material risk
    # =========================================================================
    def test_09_task_where_supervisor_detects_new_material_risk(self) -> None:
        """Scenario 9: Execution supervisor records emerging risks."""
        wo = self._create_work_order(objective="Implement calculator service")

        class RiskEmergingBackend(MockCodingAgentBackend):
            def execute(self, request, event_handler=None):
                if event_handler:
                    # Emit destructive command event
                    evt = CodingAgentEvent(
                        event_id="evt-risk-1",
                        execution_id=request.execution_id,
                        work_order_id=request.work_order_id,
                        event_type=ProgrammerExecutionEventType.COMMAND_OPERATION,
                        payload={"command": "rm -rf /tmp/test"},
                    )
                    event_handler(evt)
                return CodingAgentResult(
                    execution_id=request.execution_id,
                    work_order_id=request.work_order_id,
                    status=CodingAgentExecutionStatus.COMPLETED,
                    output_text="Executed",
                )

        programmer = self._make_programmer(backend=RiskEmergingBackend())

        result = programmer.execute(work_order=wo, root_path_override=self.workspace_root)
        sup_rec = result.metadata["supervision_record"]
        self.assertTrue(len(sup_rec["deviations"]) >= 1)

    # =========================================================================
    # Scenario 10: Verification fails and Phase 4 correction fixes it
    # =========================================================================
    def test_10_task_verification_fails_and_correction_fixes_it(self) -> None:
        """Scenario 10: Attempt 1 fails verification, correction loop re-executes and passes on attempt 2."""
        wo = self._create_work_order(iteration_budget=3)
        turn_counter = {"turn": 0}

        def dynamic_runner_factory(ctx: ProgrammerExecutionContext):
            mock_runner = MagicMock()
            turn_counter["turn"] += 1
            ev_id = new_verification_evidence_id()
            test_cmd = "pytest tests/test_calc.py"
            if turn_counter["turn"] == 1:
                chk = VerificationCheck(
                    check_id="vchk-1",
                    execution_id=ctx.execution_id,
                    work_order_id=ctx.work_order_id,
                    check_type=VerificationCheckType.TEST,
                    command=test_cmd,
                    status=VerificationStatus.FAIL,
                    exit_code=1,
                    output_snippet="AssertionError: calc mismatch",
                )
                ev = VerificationEvidence(
                    evidence_id=ev_id,
                    execution_id=ctx.execution_id,
                    work_order_id=ctx.work_order_id,
                    source_type=VerificationEvidenceSourceType.TEST_RUNNER,
                    description="Pytest failed with code 1",
                    data={"command": test_cmd, "exit_code": 1},
                    is_agent_claim=False,
                )
            else:
                chk = VerificationCheck(
                    check_id="vchk-2",
                    execution_id=ctx.execution_id,
                    work_order_id=ctx.work_order_id,
                    check_type=VerificationCheckType.TEST,
                    command=test_cmd,
                    status=VerificationStatus.PASS,
                    exit_code=0,
                )
                ev = VerificationEvidence(
                    evidence_id=ev_id,
                    execution_id=ctx.execution_id,
                    work_order_id=ctx.work_order_id,
                    source_type=VerificationEvidenceSourceType.TEST_RUNNER,
                    description="Pytest passed with code 0",
                    data={"command": test_cmd, "exit_code": 0},
                    is_agent_claim=False,
                )
            mock_runner.run.return_value = VerificationRunnerResult(
                execution_id=ctx.execution_id,
                work_order_id=ctx.work_order_id,
                checks=[chk],
                evidence=[ev],
            )
            return mock_runner

        programmer = self._make_programmer(verification_runner_factory=dynamic_runner_factory)

        result = programmer.execute(work_order=wo, root_path_override=self.workspace_root)
        self.assertEqual(result.status, ProgrammerResultStatus.COMPLETED)
        self.assertEqual(turn_counter["turn"], 2)

    # =========================================================================
    # Scenario 11: Verification fails repeatedly and reaches Phase 5 recovery
    # =========================================================================
    def test_11_task_verification_fails_repeatedly_and_reaches_recovery(self) -> None:
        """Scenario 11: Repeated verification failure exhausts budget and records recovery metadata."""
        wo = self._create_work_order(iteration_budget=2)

        def failing_runner_factory(ctx: ProgrammerExecutionContext):
            mock_runner = MagicMock()
            chk = VerificationCheck(
                check_id="vchk-fail",
                execution_id=ctx.execution_id,
                work_order_id=ctx.work_order_id,
                check_type=VerificationCheckType.TEST,
                command="pytest",
                status=VerificationStatus.FAIL,
                exit_code=1,
                output_snippet="Failure",
            )
            mock_runner.run.return_value = VerificationRunnerResult(
                execution_id=ctx.execution_id,
                work_order_id=ctx.work_order_id,
                checks=[chk],
                evidence=[],
            )
            return mock_runner

        programmer = self._make_programmer(verification_runner_factory=failing_runner_factory)

        result = programmer.execute(work_order=wo, root_path_override=self.workspace_root)
        self.assertEqual(result.status, ProgrammerResultStatus.FAILED)
        self.assertTrue(result.metadata.get("budget_exhausted", False))

    # =========================================================================
    # Scenario 12: Successful Phase 6 Git/delivery package
    # =========================================================================
    def test_12_task_reaching_successful_delivery_package(self) -> None:
        """Scenario 12: With Git context, completed task prepares valid DeliveryPackage."""
        wo = self._create_work_order()
        repo_id = new_git_repository_id()
        base_rev = GitRevision(revision_id=new_git_revision_id(), commit_hash="1111111111111111111111111111111111111111", branch="main")
        res_rev = GitRevision(revision_id=new_git_revision_id(), commit_hash="2222222222222222222222222222222222222222", branch="feature")

        git_ctx = GitExecutionContext(
            repository_id=repo_id,
            execution_id="pexec-git-1",
            work_order_id=wo.work_order_id,
            project_id=wo.project_id,
            base_revision=base_rev,
            resulting_revision=res_rev,
            workspace_path=self.workspace_root,
            isolation_mode=GitIsolationMode.WORKTREE,
            branch="feature",
        )

        programmer = self._make_programmer()

        result = programmer.execute(
            work_order=wo,
            root_path_override=self.workspace_root,
            git_context=git_ctx,
        )

        self.assertEqual(result.status, ProgrammerResultStatus.COMPLETED)
        self.assertIn("delivery_package", result.metadata)
        delivery_pkg = result.metadata["delivery_package"]
        self.assertEqual(delivery_pkg["repository_id"], repo_id)
        self.assertIn(delivery_pkg["recommended_disposition"], ["ACCEPT", "NEEDS_REVIEW", "REQUEST_CHANGES"])

    # =========================================================================
    # Scenario 13: Manager escalation approved and execution continues
    # =========================================================================
    def test_13_task_manager_escalation_approved_and_execution_continues(self) -> None:
        """Scenario 13: Manager approval of an escalation unblocks execution and completes successfully."""
        wo = self._create_work_order(
            objective="Re-architect storage to introduce sqlite database migration",
        )

        def approve_escalation(escalation: ProgrammerEscalation) -> ManagerEscalationResponse:
            return ManagerEscalationResponse(
                response_id="mresp-approve-1",
                escalation_id=escalation.escalation_id,
                action=ManagerEscalationResponseAction.APPROVE,
                responder="manager-lead",
                reason="Architecture change approved for V1 rollout.",
            )

        programmer = self._make_programmer()

        result = programmer.execute(
            work_order=wo,
            root_path_override=self.workspace_root,
            manager_response=approve_escalation,
        )

        self.assertEqual(result.status, ProgrammerResultStatus.COMPLETED)

    # =========================================================================
    # Scenario 14: Manager escalation denied and execution terminates safely
    # =========================================================================
    def test_14_task_manager_escalation_denied_and_terminates_safely(self) -> None:
        """Scenario 14: Manager denial of an escalation halts execution safely with status BLOCKED."""
        wo = self._create_work_order(
            objective="Re-architect storage to introduce sqlite database migration without manager approval",
        )

        def deny_escalation(escalation: ProgrammerEscalation) -> ManagerEscalationResponse:
            return ManagerEscalationResponse(
                response_id="mresp-deny-1",
                escalation_id=escalation.escalation_id,
                action=ManagerEscalationResponseAction.DENY,
                responder="manager-lead",
                reason="Architecture change rejected.",
            )

        programmer = self._make_programmer()

        result = programmer.execute(
            work_order=wo,
            root_path_override=self.workspace_root,
            manager_response=deny_escalation,
        )

        self.assertEqual(result.status, ProgrammerResultStatus.BLOCKED)
        self.assertIn("denied", result.summary.lower())

    # =========================================================================
    # Scenario 15: Material unknowns explicitly preserved throughout lifecycle
    # =========================================================================
    def test_15_task_with_material_unknowns_preserved_throughout_lifecycle(self) -> None:
        """Scenario 15: Inaccessible paths or unknowns are preserved in final result metadata."""
        wo = self._create_work_order(
            objective="Update calculation routines",
            allowed_paths=["src/calc/"],
            writable_paths=["src/calc/calculator.py"],
            read_only_paths=[],
            forbidden_paths=["secrets/"],
        )

        programmer = self._make_programmer()

        result = programmer.execute(work_order=wo, root_path_override=self.workspace_root)
        self.assertEqual(result.status, ProgrammerResultStatus.COMPLETED)
        self.assertIn("known_unknowns", result.metadata)
        # Verify unknowns list contains recorded uncertainties
        self.assertTrue(isinstance(result.metadata["known_unknowns"], list))


if __name__ == "__main__":
    unittest.main()
