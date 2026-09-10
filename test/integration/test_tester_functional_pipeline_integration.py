from __future__ import annotations

import unittest
from typing import Any, Optional

from core.events.model import Event
from core.events.types import EventSource, EventType
from core.models import Task, WorkerOutput
from core.tester.contracts.blocker import TesterBlocker
from core.tester.contracts.boundary import TesterBoundaryGuard
from core.tester.contracts.criteria import AcceptanceCriterion
from core.tester.contracts.environment import TestEnvironment
from core.tester.contracts.execution import TesterExecution
from core.tester.contracts.finding import (
    AcceptanceCriterionResult,
    TesterDefect,
    TesterEvidence,
    TesterFinding,
)
from core.tester.contracts.functional_pipeline import (
    FunctionalEvaluationPipeline,
    TesterExecutionPipeline,
)
from core.tester.contracts.identifiers import (
    new_blocker_id,
    new_defect_id,
    new_evidence_id,
    new_execution_id,
    new_result_id,
    new_test_case_id,
    new_work_order_id,
)
from core.tester.contracts.manager_bridge import (
    FakeTesterWorker,
    TesterManagerBridge,
)
from core.tester.contracts.observation import TesterObservation
from core.tester.contracts.plan import TestCase, TestPlan, TestStep
from core.tester.contracts.preflight import (
    ResourceCheckResult,
    RouteCheckResult,
    TestPreflightResult,
)
from core.tester.contracts.result import TesterResult
from core.tester.contracts.result_validator import TesterResultValidator
from core.tester.contracts.runtime import MockTestRuntime, TestRuntime
from core.tester.contracts.runtime_observation import RuntimeObservation
from core.tester.contracts.scope import TestScope
from core.tester.contracts.test_case import TestCaseResult, TestStepResult
from core.tester.contracts.work_order import TesterWorkOrder
from core.tester.errors import (
    TesterBlockerError,
    TesterBoundaryViolationError,
    TesterLineageError,
    TesterValidationError,
)
from core.tester.evaluator.defect_classifier import DefectClassifier
from core.tester.evaluator.functional_evaluator import FunctionalAcceptanceEvaluator
from core.tester.evaluator.preflight_evaluator import TestPreflightEvaluator
from core.tester.evaluator.runtime_evaluator import RuntimeEvaluator
from core.tester.executor.test_case_executor import TestCaseExecutor
from core.tester.types import (
    AcceptanceCriterionStatus,
    ApplicationHealthStatus,
    ApplicableTestCategory,
    DefectSeverity,
    DefectType,
    EvidenceType,
    ObservationType,
    PreflightDecision,
    PreflightStatus,
    RuntimeEventSeverity,
    RuntimeEventType,
    ShipRecommendation,
    TestCaseStatus,
    TestCategory,
    TestPlanStatus,
    TestSurface,
    TesterActionType,
    TesterBlockerCategory,
    TesterBlockerSeverity,
    TesterExecutionStatus,
    TesterResultStatus,
    TestingCapability,
)
from core.tester.worker import TesterWorker


class TestTesterFunctionalPipelineIntegration(unittest.TestCase):
    """
    Phase 5.6 Integration Test Suite.
    
    Verifies the end-to-end functional evaluation pipeline:
    - 15-step flow with deliberate runtime failure
    - Healthy application flow
    - Project types: backend-only, frontend, full-stack
    - No applicable UI tests handling
    - Runtime unavailable (Tester Failure)
    - Browser unavailable (Tester Failure)
    - Cancellation mid-evaluation
    - Timeout mid-evaluation
    - Unauthorized action attempted
    - False-success prevention guards
    """

    def setUp(self) -> None:
        self.recorded_events: list[Event] = []

        def _sink(ev: Event) -> None:
            self.recorded_events.append(ev)

        self.event_sink = _sink
        self.bridge = TesterManagerBridge(event_sink=self.event_sink)
        self.pipeline = FunctionalEvaluationPipeline(
            bridge=self.bridge,
            event_sink=self.event_sink,
        )

    # -----------------------------------------------------------------------
    # Verification 1: End-to-end 15 Required Steps with Deliberate Runtime Problem
    # -----------------------------------------------------------------------
    def test_e2e_15_steps_deliberate_runtime_failure(self) -> None:
        """
        Scenario 1: Full 15-step end-to-end integration flow with a deliberate runtime failure
        (e.g., required API 500 error and required resource 404).
        """
        # Step 1: Manager creates Task
        task = Task(
            id="task-e2e-checkout",
            project_id="proj-e2e",
            title="Implement checkout processing",
            objective="Verify checkout submission and confirmation flow",
            success_criteria=[
                "Payment submission returns HTTP 200 and confirmation receipt",
                "Confirmation badge renders without 404 assets",
            ],
            metadata={
                "correlation_id": "corr-e2e-checkout",
                "acceptance_criteria": [
                    AcceptanceCriterion(
                        criterion_id="ac-e2e-pay",
                        description="Payment API endpoint returns HTTP 200",
                    ),
                    AcceptanceCriterion(
                        criterion_id="ac-e2e-receipt",
                        description="Receipt image loads with HTTP 200",
                    ),
                ],
                "test_scope": ["checkout.payment", "checkout.receipt"],
            },
        )

        # Step 2: TesterWorkOrder issued
        work_order = self.bridge.issue_work_order(task)
        self.assertIsInstance(work_order, TesterWorkOrder)
        req_events = [e for e in self.recorded_events if e.event_type == EventType.TESTER_REQUESTED]
        self.assertEqual(len(req_events), 1)

        # Step 3: Tester actually activated (not just registered)
        # We invoke the real FunctionalEvaluationPipeline
        # We configure a deliberate runtime problem: required API endpoint returns 500
        deliberate_runtime_events = [
            {
                "event_type": "HTTP_RESPONSE",
                "url": "http://localhost:8080/api/checkout/pay",
                "method": "POST",
                "status_code": 500,
                "is_required_resource": True,
                "is_api": True,
                "error_message": "Internal Server Error in Payment Gateway",
                "test_case_id": "ttest-checkout-pay",
            },
            {
                "event_type": "HTTP_RESPONSE",
                "url": "http://localhost:8080/static/receipt-badge.png",
                "method": "GET",
                "status_code": 404,
                "is_required_resource": True,
                "is_api": False,
                "error_message": "Not Found",
                "test_case_id": "ttest-checkout-badge",
            },
        ]

        # Provide a frozen test plan with matching test cases
        tc1 = TestCase(
            test_case_id="ttest-checkout-pay",
            category=ApplicableTestCategory.FUNCTIONAL,
            objective="Verify payment submission",
            steps=[
                TestStep(step_number=1, description="Post payment", action="POST /api/checkout/pay", expected="HTTP 200 OK"),
            ],
            expected_outcome="Payment successful with 200",
            acceptance_linkage=["ac-e2e-pay"],
            covered_surfaces=[TestSurface.API],
        )
        tc2 = TestCase(
            test_case_id="ttest-checkout-badge",
            category=ApplicableTestCategory.FUNCTIONAL,
            objective="Verify receipt asset loading",
            steps=[
                TestStep(step_number=1, description="Load receipt badge", action="GET /static/receipt-badge.png", expected="HTTP 200 OK"),
            ],
            expected_outcome="Receipt image loaded",
            acceptance_linkage=["ac-e2e-receipt"],
            covered_surfaces=[TestSurface.UI],
        )
        frozen_plan = TestPlan(
            plan_id="tplan-e2e-checkout",
            execution_id="texec-e2e-checkout",
            work_order_id=work_order.work_order_id,
            project_id=work_order.project_id,
            test_cases=[tc1, tc2],
            status=TestPlanStatus.FROZEN,
        )

        execution = TesterExecution(
            execution_id="texec-e2e-checkout",
            work_order_id=work_order.work_order_id,
            task_id=task.id,
            project_id=task.project_id,
            correlation_id=task.metadata["correlation_id"],
            status=TesterExecutionStatus.STARTING,
        )

        # Run pipeline
        executed_execution, result, worker_output = self.pipeline.execute(
            work_order=work_order,
            execution=execution,
            test_plan=frozen_plan,
            runtime_events=deliberate_runtime_events,
        )

        # Step 4: TestContext established
        # In this flow, execution is bound and traced
        self.assertEqual(executed_execution.execution_id, "texec-e2e-checkout")

        # Step 5: Frozen TestPlan loaded
        self.assertIsNotNone(executed_execution.test_plan)
        self.assertTrue(executed_execution.test_plan.is_frozen)

        # Step 6: Preflight evaluated
        self.assertIsNotNone(executed_execution.preflight_result)
        self.assertIn(
            executed_execution.preflight_result.status,
            (PreflightStatus.PASS, PreflightStatus.WARNINGS),
        )

        # Step 7: Test execution executed
        self.assertGreater(len(executed_execution.test_cases), 0)

        # Step 8: Runtime observations collected
        self.assertGreater(len(executed_execution.runtime_observations), 0)

        # Step 9: Runtime failure captured
        rt_failures = [ro for ro in executed_execution.runtime_observations if ro.is_failure]
        self.assertGreater(len(rt_failures), 0)
        has_500 = any(ro.status_code == 500 for ro in rt_failures)
        self.assertTrue(has_500)

        # Step 10: Functional evaluation executed
        self.assertGreater(len(executed_execution.acceptance_results), 0)
        # At least one acceptance criterion failed
        has_failed_ac = any(ac.status == AcceptanceCriterionStatus.FAIL for ac in executed_execution.acceptance_results)
        self.assertTrue(has_failed_ac)

        # Step 11: Defect created and classified
        self.assertGreater(len(executed_execution.defects), 0)
        defect_types = [d.defect_type for d in executed_execution.defects]
        self.assertTrue(
            DefectType.API in defect_types
            or DefectType.RESOURCE in defect_types
            or DefectType.FUNCTIONAL in defect_types
            or DefectType.SPEC_VIOLATION in defect_types
        )
        # Evidence backing checked: each defect must have evidence_ids
        for d in executed_execution.defects:
            self.assertGreater(len(d.evidence_ids), 0)

        # Step 12: Structured TesterResult constructed
        self.assertIsInstance(result, TesterResult)
        self.assertEqual(result.execution_id, executed_execution.execution_id)
        self.assertEqual(result.work_order_id, work_order.work_order_id)
        self.assertEqual(result.ship_recommendation, ShipRecommendation.DO_NOT_SHIP)
        self.assertFalse(result.is_pass)
        self.assertFalse(result.is_acceptance_fully_verified())

        # Step 13: Result returned to Manager
        self.assertIsInstance(worker_output, WorkerOutput)

        # Step 14: Manager receives result via bridge
        completed_events = [e for e in self.recorded_events if e.event_type == EventType.TESTER_COMPLETED]
        self.assertEqual(len(completed_events), 1)
        self.assertEqual(executed_execution.status, TesterExecutionStatus.COMPLETED)

        # Step 15: WorkerOutput reflects actual failure
        self.assertEqual(worker_output.metadata["ship_recommendation"], "DO_NOT_SHIP")
        self.assertGreater(worker_output.metadata["defects_count"], 0)
        self.assertFalse(worker_output.metadata["acceptance_fully_verified"])
        self.assertFalse(worker_output.metadata["is_pass"])
        self.assertIsNotNone(worker_output.error_message)
        self.assertIn("DO_NOT_SHIP", worker_output.error_message)

    # -----------------------------------------------------------------------
    # Verification 2: Healthy Application Flow
    # -----------------------------------------------------------------------
    def test_healthy_application_flow(self) -> None:
        """
        Scenario 2: Healthy application flow:
        - Application healthy, tests pass, acceptance criteria pass
        - Defect-free, SHIP recommendation, evidence backed
        """
        task = Task(
            id="task-healthy-flow",
            project_id="proj-healthy",
            title="Healthy user profile display",
            objective="Verify user profile renders correctly",
            success_criteria=["User profile displays username and avatar"],
            metadata={
                "correlation_id": "corr-healthy",
                "acceptance_criteria": [
                    AcceptanceCriterion(
                        criterion_id="ac-healthy-1",
                        description="User profile displays username",
                    ),
                ],
                "test_scope": ["profile.display"],
            },
        )
        work_order = self.bridge.issue_work_order(task)

        # Custom step handler simulating healthy step execution
        def _step_handler(step: TestStep, *args: Any, **kwargs: Any) -> tuple[bool, str, list[str]]:
            ev_id = new_evidence_id()
            return True, "Profile loaded cleanly with user data", [ev_id]

        tc = TestCase(
            test_case_id="ttest-healthy-1",
            category=ApplicableTestCategory.FUNCTIONAL,
            objective="Verify profile page rendering",
            steps=[
                TestStep(step_number=1, description="Get profile page", action="GET /profile", expected="HTTP 200 and profile HTML"),
            ],
            expected_outcome="Profile displayed",
            acceptance_linkage=["ac-healthy-1"],
            covered_surfaces=[TestSurface.UI],
        )
        plan = TestPlan(
            plan_id="tplan-healthy-1",
            execution_id="texec-healthy-1",
            work_order_id=work_order.work_order_id,
            project_id=work_order.project_id,
            test_cases=[tc],
            status=TestPlanStatus.FROZEN,
        )

        execution = TesterExecution(
            execution_id="texec-healthy-1",
            work_order_id=work_order.work_order_id,
            task_id=task.id,
            project_id=task.project_id,
            correlation_id=task.metadata["correlation_id"],
            status=TesterExecutionStatus.STARTING,
        )

        # Benign HTTP 200 runtime observation
        healthy_events = [
            {
                "event_type": "HTTP_RESPONSE",
                "url": "http://localhost:3000/profile",
                "method": "GET",
                "status_code": 200,
                "is_required_resource": True,
                "test_case_id": "ttest-healthy-1",
            }
        ]

        executed_execution, result, output = self.pipeline.execute(
            work_order=work_order,
            execution=execution,
            test_plan=plan,
            step_handler=_step_handler,
            runtime_events=healthy_events,
        )

        # Verify defect-free, SHIP recommendation, evidence backed
        self.assertEqual(len(result.defects), 0)
        self.assertEqual(result.ship_recommendation, ShipRecommendation.SHIP)
        self.assertTrue(result.is_pass)
        self.assertTrue(result.is_acceptance_fully_verified())
        self.assertGreater(len(result.evidence_ids), 0)

        # Verify WorkerOutput
        self.assertTrue(output.success)
        self.assertEqual(output.metadata["ship_recommendation"], "SHIP")
        self.assertEqual(output.metadata["defects_count"], 0)
        self.assertTrue(output.metadata["is_pass"])

    # -----------------------------------------------------------------------
    # Verification 3: Test Across Project Types (Backend, Frontend, Fullstack)
    # -----------------------------------------------------------------------
    def test_project_types_evaluation(self) -> None:
        """
        Scenario 3: Verify execution across backend-only, frontend-only, and full-stack projects.
        """
        # A. Backend-only project
        task_backend = Task(
            id="task-proj-backend",
            project_id="proj-backend",
            title="Backend user service",
            objective="Verify REST API user endpoint",
            success_criteria=["User API responds with valid JSON"],
            metadata={
                "correlation_id": "corr-backend",
                "test_scope": ["api.users"],
            },
        )
        wo_backend = self.bridge.issue_work_order(task_backend)
        exec_be, res_be, out_be = self.pipeline.execute(
            work_order=wo_backend,
            files_manifest=["backend/server.py", "backend/models/user.py", "backend/routes/api.py"],
        )
        self.assertEqual(exec_be.status, TesterExecutionStatus.COMPLETED)
        self.assertIsInstance(res_be, TesterResult)

        # B. Frontend-only project
        task_frontend = Task(
            id="task-proj-frontend",
            project_id="proj-frontend",
            title="Frontend dashboard component",
            objective="Verify dashboard layout rendering",
            success_criteria=["Dashboard component mounts"],
            metadata={
                "correlation_id": "corr-frontend",
                "test_scope": ["App", "Dashboard"],
            },
        )
        wo_frontend = self.bridge.issue_work_order(task_frontend)
        exec_fe, res_fe, out_fe = self.pipeline.execute(
            work_order=wo_frontend,
            files_manifest=["frontend/src/App.tsx", "frontend/src/components/Dashboard.tsx"],
        )
        self.assertEqual(exec_fe.status, TesterExecutionStatus.COMPLETED)
        self.assertIsInstance(res_fe, TesterResult)

        # C. Full-stack project
        task_fullstack = Task(
            id="task-proj-fullstack",
            project_id="proj-fullstack",
            title="Full stack application",
            objective="Verify end-to-end integration",
            success_criteria=["Frontend and backend integrate"],
            metadata={
                "correlation_id": "corr-fullstack",
                "test_scope": ["App", "api"],
            },
        )
        wo_fullstack = self.bridge.issue_work_order(task_fullstack)
        exec_fs, res_fs, out_fs = self.pipeline.execute(
            work_order=wo_fullstack,
            files_manifest=["backend/api.py", "frontend/src/App.tsx", "shared/types.ts"],
        )
        self.assertEqual(exec_fs.status, TesterExecutionStatus.COMPLETED)
        self.assertIsInstance(res_fs, TesterResult)

    # -----------------------------------------------------------------------
    # Verification 4: Project With No Applicable UI Tests
    # -----------------------------------------------------------------------
    def test_project_no_applicable_ui_tests(self) -> None:
        """
        Scenario 4: Verify proper handling when changes contain no applicable executable tests.
        Does not falsely fail or fabricate defects.
        """
        task = Task(
            id="task-no-applicable",
            project_id="proj-docs",
            title="Update architecture documentation",
            objective="Update documentation and README notes",
            success_criteria=["User can click interactive demo button"],
            metadata={
                "correlation_id": "corr-docs",
                "test_scope": ["docs.readme"],
                "test_categories": [],
                "authorized_capabilities": ["test_execution"],
            },
        )
        work_order = self.bridge.issue_work_order(task)

        exec_res, res, output = self.pipeline.execute(
            work_order=work_order,
            files_manifest=["README.md", "docs/architecture.md", "LICENSE"],
        )

        self.assertEqual(exec_res.status, TesterExecutionStatus.COMPLETED)
        self.assertIsInstance(res, TesterResult)
        self.assertEqual(len(res.defects), 0)
        self.assertIn("NO_APPLICABLE_TESTS", res.summary_for_manager or res.summary)

    # -----------------------------------------------------------------------
    # Verification 5: Execution When Runtime Unavailable (Tester Failure)
    # -----------------------------------------------------------------------
    def test_execution_runtime_unavailable(self) -> None:
        """
        Scenario 5: Runtime is unavailable -> Tester reports BLOCKED / FAILED, not success.
        """
        task = Task(
            id="task-no-runtime",
            project_id="proj-no-runtime",
            title="Backend test with broken runtime",
            objective="Run tests against missing runtime",
            success_criteria=["Runtime active"],
            metadata={
                "correlation_id": "corr-no-runtime",
                "test_scope": ["backend.runtime"],
            },
        )
        work_order = self.bridge.issue_work_order(task)

        exec_res, res, output = self.pipeline.execute(
            work_order=work_order,
            runtime_available=False,
        )

        self.assertEqual(exec_res.status, TesterExecutionStatus.BLOCKED)
        self.assertEqual(res.status, TesterResultStatus.BLOCKED)
        self.assertEqual(res.ship_recommendation, ShipRecommendation.NOT_VERIFIED)
        self.assertFalse(output.success)
        self.assertIn("Tester Failure", res.summary_for_manager)

    # -----------------------------------------------------------------------
    # Verification 6: Execution When Browser Unavailable (Tester Failure)
    # -----------------------------------------------------------------------
    def test_execution_browser_unavailable(self) -> None:
        """
        Scenario 6: Browser required but unavailable -> reports BLOCKED / FAILED.
        """
        task = Task(
            id="task-no-browser",
            project_id="proj-no-browser",
            title="UI test with missing browser",
            objective="Run UI verification requiring headless chrome",
            success_criteria=["Browser renders page"],
            metadata={
                "correlation_id": "corr-no-browser",
                "test_scope": ["frontend.browser"],
            },
        )
        work_order = self.bridge.issue_work_order(task)

        exec_res, res, output = self.pipeline.execute(
            work_order=work_order,
            require_browser=True,
            browser_available=False,
        )

        self.assertEqual(exec_res.status, TesterExecutionStatus.BLOCKED)
        self.assertEqual(res.status, TesterResultStatus.BLOCKED)
        self.assertEqual(res.ship_recommendation, ShipRecommendation.NOT_VERIFIED)
        self.assertFalse(output.success)

    # -----------------------------------------------------------------------
    # Verification 7: Test Cancellation Mid-Evaluation
    # -----------------------------------------------------------------------
    def test_cancellation_mid_evaluation(self) -> None:
        """
        Scenario 7: Cancellation cleanly transitions to CANCELLED with trace and lineage.
        """
        task = Task(
            id="task-cancel",
            project_id="proj-cancel",
            title="Cancelled test run",
            objective="Verify cancellation handling",
            success_criteria=["Clean cancellation"],
            metadata={
                "correlation_id": "corr-cancel",
                "test_scope": ["cancel.evaluation"],
            },
        )
        work_order = self.bridge.issue_work_order(task)
        execution = self.bridge.dispatch_work_order(work_order)

        # Cancel execution
        self.bridge.cancel_execution(
            execution=execution,
            requested_by="manager",
            reason="User cancelled evaluation to modify scope",
            work_order=work_order,
        )

        exec_res, res, output = self.pipeline.execute(
            work_order=work_order,
            execution=execution,
        )

        self.assertEqual(exec_res.status, TesterExecutionStatus.CANCELLED)
        self.assertEqual(res.status, TesterResultStatus.CANCELLED)
        self.assertFalse(output.success)

        # Verify domain event emitted
        cancel_events = [e for e in self.recorded_events if e.event_type == EventType.TESTER_CANCELLED]
        self.assertGreaterEqual(len(cancel_events), 1)

    # -----------------------------------------------------------------------
    # Verification 8: Test Timeout Mid-Evaluation
    # -----------------------------------------------------------------------
    def test_timeout_mid_evaluation(self) -> None:
        """
        Scenario 8: Bounded execution halts when timeout occurs.
        """
        task = Task(
            id="task-timeout",
            project_id="proj-timeout",
            title="Test run with timeout",
            objective="Verify timeout halts execution gracefully",
            success_criteria=["Timeout handled"],
            metadata={
                "correlation_id": "corr-timeout",
                "time_budget": 1,
                "test_scope": ["timeout.evaluation"],
            },
        )
        work_order = self.bridge.issue_work_order(task)

        # Timeout runner that raises TimeoutError
        def _slow_build_runner(cmd: str) -> tuple[int, str, str]:
            raise TimeoutError("Command timed out after 1000ms")

        exec_res, res, output = self.pipeline.execute(
            work_order=work_order,
            build_command="npm run build",
            build_runner=_slow_build_runner,
        )

        # Preflight detects build error and product evaluation fails cleanly
        self.assertEqual(res.ship_recommendation, ShipRecommendation.DO_NOT_SHIP)
        self.assertFalse(res.is_pass)

    # -----------------------------------------------------------------------
    # Verification 9: Unauthorized Action Attempted During Evaluation
    # -----------------------------------------------------------------------
    def test_unauthorized_action_attempted(self) -> None:
        """
        Scenario 9: Boundary guard blocks unauthorized action (expanding scope or modifying source).
        """
        task = Task(
            id="task-unauthorized",
            project_id="proj-boundary",
            title="Boundary enforcement test",
            objective="Verify boundaries cannot be crossed",
            success_criteria=["Boundary preserved"],
            metadata={
                "correlation_id": "corr-boundary",
                "test_scope": ["auth.login"],
            },
        )
        work_order = self.bridge.issue_work_order(task)
        execution = self.bridge.dispatch_work_order(work_order)

        # Attempt to record a test case outside authorized scope
        tc_out_of_scope = TestCaseResult(
            test_id=new_test_case_id(),
            name="payments.unauthorized_scope_expansion",
            status=TestCaseStatus.PASS,
            evidence_ids=[new_evidence_id()],
        )
        execution.record_test_case(tc_out_of_scope)

        # Bridge assert_authority_boundary must reject unauthorized scope
        with self.assertRaises(TesterBoundaryViolationError):
            self.bridge.assert_authority_boundary(work_order, execution)

    # -----------------------------------------------------------------------
    # Verification 10: False-Success Prevention
    # -----------------------------------------------------------------------
    def test_false_success_prevention(self) -> None:
        """
        Scenario 10:
        - PASS without evidence is strictly rejected.
        - COMPLETED != PASS.
        - Tester registered != Tester executed.
        - Preflight failure cannot be marked as passing product.
        """
        # Subtest A: PASS without evidence rejected by TesterResultValidator
        res_no_evidence = TesterResult(
            result_id=new_result_id(),
            execution_id=new_execution_id(),
            work_order_id=new_work_order_id(),
            task_id="task-no-ev",
            project_id="proj-no-ev",
            correlation_id="corr-no-ev",
            status=TesterResultStatus.COMPLETED,
            ship_recommendation=ShipRecommendation.SHIP,
            acceptance_results=[
                AcceptanceCriterionResult(
                    criterion_id="ac-fake-pass",
                    description="Fake pass without proof",
                    status=AcceptanceCriterionStatus.PASS,
                    evidence_ids=[],  # Empty!
                )
            ],
        )
        with self.assertRaises(TesterValidationError):
            res_no_evidence.validate()

        # Subtest B: COMPLETED does not mean PASS
        ev_crash_id = new_evidence_id()
        res_completed_with_defect = TesterResult(
            result_id=new_result_id(),
            execution_id=new_execution_id(),
            work_order_id=new_work_order_id(),
            task_id="task-comp",
            project_id="proj-comp",
            correlation_id="corr-comp",
            status=TesterResultStatus.COMPLETED,
            ship_recommendation=ShipRecommendation.DO_NOT_SHIP,
            defects=[
                TesterDefect(
                    defect_id=new_defect_id(),
                    work_order_id=new_work_order_id(),
                    title="Critical crash",
                    description="Null pointer in checkout",
                    severity=DefectSeverity.CRITICAL,
                    defect_type=DefectType.CRASH,
                    evidence_ids=[ev_crash_id],
                )
            ],
            evidence=[
                TesterEvidence(
                    evidence_id=ev_crash_id,
                    evidence_type=EvidenceType.TEST_OUTPUT,
                    description="Crash traceback",
                )
            ],
        )
        self.assertTrue(res_completed_with_defect.is_success())  # Evaluation completed
        self.assertFalse(res_completed_with_defect.is_pass)  # Product did NOT pass!
        self.assertEqual(res_completed_with_defect.ship_recommendation, ShipRecommendation.DO_NOT_SHIP)

        # Subtest C: Tester worker registration != Tester executed
        worker = TesterWorker(bridge=self.bridge, pipeline=self.pipeline)
        manifest = worker.get_manifest()
        self.assertEqual(manifest.id, "worker.tester")
        # Merely instantiating or registering worker did NOT execute anything
        completed_events = [e for e in self.recorded_events if e.event_type == EventType.TESTER_COMPLETED]
        self.assertEqual(len(completed_events), 0)

        # Now actually execute via worker
        task = Task(
            id="task-worker-run",
            project_id="proj-worker",
            title="Execute via worker",
            objective="Verify worker actually runs",
            success_criteria=["Runs cleanly"],
            metadata={
                "correlation_id": "corr-worker",
                "test_scope": ["worker.run"],
            },
        )
        output = worker.execute_task(task=task)
        self.assertIsInstance(output, WorkerOutput)
        # Now execution actually ran and emitted TESTER_COMPLETED!
        completed_after = [e for e in self.recorded_events if e.event_type == EventType.TESTER_COMPLETED]
        self.assertEqual(len(completed_after), 1)

        # Subtest D: Preflight failure cannot be marked as passing
        task_pf_fail = Task(
            id="task-pf-fail",
            project_id="proj-pf-fail",
            title="Preflight failure task",
            objective="Verify preflight failure produces DO_NOT_SHIP",
            success_criteria=["Must fail preflight"],
            metadata={
                "correlation_id": "corr-pf-fail",
                "authorized_commands": ["npm run build"],
                "test_scope": ["build.health"],
            },
        )
        wo_pf_fail = self.bridge.issue_work_order(task_pf_fail)

        def _failing_build_runner(cmd: str) -> tuple[int, str, str]:
            return 1, "", "SyntaxError: Unexpected token in main.ts"

        tc_pf = TestCase(
            test_case_id="ttest-pf-1",
            category=ApplicableTestCategory.FUNCTIONAL,
            objective="Build app",
            steps=[TestStep(step_number=1, description="Run build", action="build", expected="exit 0")],
            expected_outcome="Build pass",
            covered_surfaces=[TestSurface.BUSINESS_LOGIC],
        )
        plan_pf = TestPlan(
            plan_id="tplan-pf-fail",
            execution_id="texec-pf-fail",
            work_order_id=wo_pf_fail.work_order_id,
            project_id=wo_pf_fail.project_id,
            test_cases=[tc_pf],
            status=TestPlanStatus.FROZEN,
        )
        exec_pf = TesterExecution(
            execution_id="texec-pf-fail",
            work_order_id=wo_pf_fail.work_order_id,
            task_id=task_pf_fail.id,
            project_id=task_pf_fail.project_id,
            correlation_id="corr-pf-fail",
            status=TesterExecutionStatus.STARTING,
        )

        _, res_pf, out_pf = self.pipeline.execute(
            work_order=wo_pf_fail,
            execution=exec_pf,
            test_plan=plan_pf,
            build_command="npm run build",
            build_runner=_failing_build_runner,
        )

        self.assertEqual(res_pf.ship_recommendation, ShipRecommendation.DO_NOT_SHIP)
        self.assertFalse(res_pf.is_pass)
        self.assertEqual(out_pf.metadata["ship_recommendation"], "DO_NOT_SHIP")


if __name__ == "__main__":
    unittest.main()
