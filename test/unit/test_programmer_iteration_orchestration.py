from __future__ import annotations

import copy
import shutil
import tempfile
import unittest

from core.events.types import EventSource, EventType
from core.programmer.contracts.acceptance_criteria import AcceptanceCriterion
from core.programmer.contracts.delivery import DeliveryPackage
from core.programmer.contracts.execution import ProgrammerExecution
from core.programmer.contracts.feedback import EngineeringFeedback
from core.programmer.contracts.identifiers import (
    new_delivery_id,
    new_feedback_id,
    new_work_order_id,
)
from core.programmer.contracts.iteration import (
    EngineeringIterationCoordinator,
    IterationOutcome,
    PriorEngineeringContext,
)
from core.programmer.contracts.manager_bridge import (
    FakeProgrammerWorker,
    ProgrammerManagerBridge,
)
from core.programmer.contracts.prompt_builder import (
    ProgrammerPromptBuilder,
)
from core.programmer.contracts.result import ProgrammerResult
from core.programmer.contracts.work_order import ProgrammerWorkOrder
from core.programmer.types import (
    AcceptanceCriterionType,
    AcceptanceStatus,
    FeedbackConfidence,
    FeedbackIssueType,
    FeedbackSeverity,
    ManagerIterationDecision,
    ProgrammerExecutionStatus,
    ProgrammerResultStatus,
)


class TestProgrammerIterationOrchestration(unittest.TestCase):
    """Unit tests for Phase 8.6: Manager-Orchestrated Engineering Iteration."""

    def setUp(self) -> None:
        self.tmp_dir = tempfile.mkdtemp()
        self.bridge = ProgrammerManagerBridge()
        self.project_id = "proj-payments-mesh"
        self.task_id = "mtask-pay-900"
        self.wo_id = new_work_order_id()
        self.base_wo_id = self.wo_id

        self.ac1 = AcceptanceCriterion(
            criterion_id="ac-charge-01",
            criterion_type=AcceptanceCriterionType.TEST_PASS,
            description="POST /api/v1/charge returns 200 on valid card token",
            is_mandatory=True,
        )
        self.ac2 = AcceptanceCriterion(
            criterion_id="ac-idemp-02",
            criterion_type=AcceptanceCriterionType.BEHAVIOR_DEMONSTRATED,
            description="Duplicate charges with same idempotency key are rejected with 409",
            is_mandatory=True,
        )

        self.base_work_order = ProgrammerWorkOrder(
            work_order_id=self.wo_id,
            manager_task_id=self.task_id,
            project_id=self.project_id,
            correlation_id="corr-pay-cycle",
            objective="Implement core credit card charging service with idempotency",
            revision_number=1,
            technical_requirements=[
                "Create POST /api/v1/charge endpoint",
                "Store idempotency keys in Redis with 24h TTL",
            ],
            constraints=[
                "Payment gateway calls must timeout within 2 seconds",
            ],
            acceptance_criteria=[self.ac1, self.ac2],
            allowed_paths=["src/payments/", "tests/payments/"],
            writable_paths=["src/payments/", "tests/payments/"],
            read_only_paths=["config/gateway.json"],
            forbidden_paths=["secrets/"],
            allowed_commands=["pytest tests/payments -v", "ruff check src/payments"],
        )

        self.worker = FakeProgrammerWorker(bridge=self.bridge)

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    # =========================================================================
    # Scenario 1: Programmer -> Tester -> Manager -> Programmer cycle
    # =========================================================================
    def test_01_programmer_tester_manager_programmer_cycle(self) -> None:
        """Scenario 1: Full multi-worker iteration cycle under Manager orchestration."""
        # 1. Programmer executes base work order (Revision 1)
        exec1, res1, out1 = self.worker.execute_work_order(self.base_work_order)
        self.assertEqual(res1.status, ProgrammerResultStatus.SUCCESS)

        # 2. Simulated Delivery to Tester
        delivery = DeliveryPackage(
            delivery_id=new_delivery_id(),
            execution_id=exec1.execution_id,
            work_order_id=self.base_work_order.work_order_id,
            acceptance_results=res1.acceptance_results,
            test_results=res1.test_results,
        )

        # 3. Tester runs independent integration tests and generates defect feedback
        fb_id = new_feedback_id()
        tester_feedback = EngineeringFeedback(
            feedback_id=fb_id,
            source_worker="Tester",
            source_task="ttask-load-901",
            target_project=self.project_id,
            related_work_order=self.base_work_order.work_order_id,
            issue="Race condition in idempotency check under 100 concurrent requests",
            issue_type=FeedbackIssueType.BUG,
            severity=FeedbackSeverity.HIGH,
            observed_behavior="Double billing occurred for 3 out of 100 concurrent requests",
            expected_behavior="Strict atomic lock prevents duplicate charges",
            suggested_direction="Use SETNX or Redlock algorithm",
        )

        # 4. Manager evaluates feedback and decides REQUEST_FIX
        outcome = self.bridge.orchestrate_iteration(
            base_work_order=self.base_work_order,
            decision=ManagerIterationDecision.REQUEST_FIX,
            feedback_items=[tester_feedback],
            previous_execution=exec1,
            previous_delivery=delivery,
            previous_result=res1,
            manager_notes="Fix confirmed race condition in idempotency check",
        )

        self.assertEqual(outcome.decision, ManagerIterationDecision.REQUEST_FIX)
        rev2_wo = outcome.revised_work_order
        self.assertIsNotNone(rev2_wo)
        self.assertEqual(rev2_wo.revision_number, 2)
        self.assertEqual(rev2_wo.parent_work_order_id, self.base_work_order.work_order_id)
        self.assertEqual(rev2_wo.project_id, self.project_id)

        # 5. Programmer executes revised work order (Revision 2)
        exec2, res2, out2 = self.worker.execute_work_order(rev2_wo)
        self.assertEqual(res2.status, ProgrammerResultStatus.SUCCESS)
        self.assertEqual(exec2.work_order_id, rev2_wo.work_order_id)

    # =========================================================================
    # Scenario 2: Revised WorkOrder structure and prior context comprehension
    # =========================================================================
    def test_02_revised_work_order_structure_and_context(self) -> None:
        """Scenario 2: Revised WorkOrder equips Programmer with structured prior context."""
        fb_id = new_feedback_id()
        fb = EngineeringFeedback(
            feedback_id=fb_id,
            source_worker="Tester",
            source_task="ttask-qa-02",
            target_project=self.project_id,
            related_work_order=self.base_wo_id,
            issue="Missing card brand in charge receipt",
            issue_type=FeedbackIssueType.API_CONTRACT,
            severity=FeedbackSeverity.MEDIUM,
            observed_behavior="brand field is None",
            expected_behavior="brand is Visa, Mastercard, etc.",
            suggested_direction="Extract brand from bin lookup",
        )

        outcome = EngineeringIterationCoordinator.orchestrate_iteration(
            base_work_order=self.base_work_order,
            decision=ManagerIterationDecision.REQUEST_FIX,
            feedback_items=[fb],
            new_technical_requirements=["Include card brand in receipt payload"],
            manager_notes="Prioritize receipt schema consistency",
        )

        rev_wo = outcome.revised_work_order
        self.assertIsNotNone(rev_wo)

        # Extract PriorEngineeringContext
        prior_ctx = PriorEngineeringContext.from_work_order(rev_wo)
        self.assertIsNotNone(prior_ctx)
        self.assertEqual(prior_ctx.previous_work_order_id, self.base_wo_id)
        self.assertEqual(prior_ctx.manager_decision, ManagerIterationDecision.REQUEST_FIX)
        self.assertTrue(len(prior_ctx.failures_observed) >= 1)
        self.assertTrue(len(prior_ctx.remaining_to_fix) >= 1)
        self.assertIn("Include card brand in receipt payload", prior_ctx.requirements_diff.get("added_requirements", []))

        # Check prompt builder injection of Dimension 17
        from core.programmer.contracts.provisioner import WorkspaceProvisioner
        provisioner = WorkspaceProvisioner(project_resolver={self.project_id: self.tmp_dir})
        exec_obj = rev_wo.create_execution()
        prov_res = provisioner.provision(rev_wo, exec_obj)
        self.assertTrue(prov_res.is_ready())
        ctx = prov_res.execution_context
        prompt = ProgrammerPromptBuilder.build_instruction_prompt(rev_wo, ctx)
        self.assertIn("# 17. PRIOR ENGINEERING ITERATION CONTEXT", prompt)
        self.assertIn("What Was Previously Implemented:", prompt)
        self.assertIn("What Failed (Observed Failures & Defects):", prompt)
        self.assertIn("What Changed in Requirements:", prompt)
        self.assertIn("What Remains to be Fixed:", prompt)

    # =========================================================================
    # Scenario 3: Unchanged WorkOrder (re-verification request)
    # =========================================================================
    def test_03_unchanged_work_order_verification_rerun(self) -> None:
        """Scenario 3: Manager requests iteration without changing requirements (e.g. flaky test rerun)."""
        outcome = EngineeringIterationCoordinator.orchestrate_iteration(
            base_work_order=self.base_work_order,
            decision=ManagerIterationDecision.REQUEST_FIX,
            feedback_items=[],  # No feedback defects
            manager_notes="CI network glitch suspected; re-run verification checks without code changes",
        )

        rev_wo = outcome.revised_work_order
        self.assertIsNotNone(rev_wo)
        self.assertEqual(rev_wo.revision_number, 2)
        # Requirements match base work order
        self.assertEqual(len(rev_wo.technical_requirements), len(self.base_work_order.technical_requirements))
        self.assertEqual(rev_wo.acceptance_criteria, self.base_work_order.acceptance_criteria)

    # =========================================================================
    # Scenario 4: Changed acceptance criteria
    # =========================================================================
    def test_04_changed_acceptance_criteria(self) -> None:
        """Scenario 4: Manager adds, modifies, and removes acceptance criteria between revisions."""
        new_ac = AcceptanceCriterion(
            criterion_id="ac-refund-03",
            criterion_type=AcceptanceCriterionType.TEST_PASS,
            description="POST /api/v1/refund processes partial refund",
            is_mandatory=True,
        )
        mod_ac = AcceptanceCriterion(
            criterion_id="ac-charge-01",  # modifying existing
            criterion_type=AcceptanceCriterionType.TEST_PASS,
            description="POST /api/v1/charge returns 200 and emits telemetry event",
            is_mandatory=True,
        )

        outcome = EngineeringIterationCoordinator.orchestrate_iteration(
            base_work_order=self.base_work_order,
            decision=ManagerIterationDecision.REQUEST_FIX,
            new_acceptance_criteria=[new_ac],
            modified_acceptance_criteria=[mod_ac],
            removed_acceptance_criteria_ids=["ac-idemp-02"],  # removing idemp-02
            manager_notes="Added refund support, updated charge criterion, removed deprecated idemp criterion",
        )

        rev_wo = outcome.revised_work_order
        self.assertIsNotNone(rev_wo)

        c_ids = [getattr(c, "criterion_id", None) or c.get("criterion_id") for c in rev_wo.acceptance_criteria]
        self.assertIn("ac-refund-03", c_ids)
        self.assertIn("ac-charge-01", c_ids)
        self.assertNotIn("ac-idemp-02", c_ids)

        # Verify modified description
        updated_charge = next(c for c in rev_wo.acceptance_criteria if getattr(c, "criterion_id", None) == "ac-charge-01")
        self.assertIn("emits telemetry event", updated_charge.description)

    # =========================================================================
    # Scenario 5: Regression fix
    # =========================================================================
    def test_05_regression_fix_cycle(self) -> None:
        """Scenario 5: Regression defect detected by Tester; Manager directs fix."""
        fb_reg = EngineeringFeedback(
            feedback_id=new_feedback_id(),
            source_worker="Tester",
            source_task="ttask-reg-55",
            target_project=self.project_id,
            related_work_order=self.base_wo_id,
            issue="Regression: Payment timeout handling now hangs indefinitely",
            issue_type=FeedbackIssueType.REGRESSION,
            severity=FeedbackSeverity.CRITICAL,
            observed_behavior="Request hangs after 30 seconds instead of timing out at 2 seconds",
            expected_behavior="GatewayTimeout error returned after 2 seconds",
            suggested_direction="Restore HTTP client timeout configuration",
        )

        outcome = self.bridge.orchestrate_iteration(
            base_work_order=self.base_work_order,
            decision=ManagerIterationDecision.REQUEST_FIX,
            feedback_items=[fb_reg],
            manager_notes="Critical regression must be addressed before deployment",
        )

        rev_wo = outcome.revised_work_order
        self.assertIsNotNone(rev_wo)
        self.assertTrue(any("REGRESSION" in req and "Payment timeout" in req for req in rev_wo.technical_requirements))
        self.assertEqual(rev_wo.context.get("max_feedback_severity"), FeedbackSeverity.CRITICAL.value)

    # =========================================================================
    # Scenario 6: Rejected feedback handling
    # =========================================================================
    def test_06_rejected_feedback(self) -> None:
        """Scenario 6: Manager filters out unwanted feedback; or rejects the iteration entirely."""
        fb_valid = EngineeringFeedback(
            feedback_id=new_feedback_id(),
            source_worker="Tester",
            source_task="ttask-1",
            target_project=self.project_id,
            related_work_order=self.base_wo_id,
            issue="Valid defect in currency formatting",
            observed_behavior="JPY has decimal places",
            expected_behavior="JPY has no decimal places",
        )
        fb_invalid = EngineeringFeedback(
            feedback_id=new_feedback_id(),
            source_worker="Designer",
            source_task="dtask-1",
            target_project=self.project_id,
            related_work_order=self.base_wo_id,
            issue="Out-of-scope color theme change",
            issue_type=FeedbackIssueType.UX_INTEGRATION,
            observed_behavior="Buttons are blue",
            expected_behavior="Buttons should be orange",
        )

        # 6a: Partial acceptance: only fb_valid accepted
        outcome_partial = EngineeringIterationCoordinator.orchestrate_iteration(
            base_work_order=self.base_work_order,
            decision=ManagerIterationDecision.REQUEST_FIX,
            feedback_items=[fb_valid, fb_invalid],
            accepted_feedback_ids=[fb_valid.feedback_id],
            manager_notes="Theme changes are out of scope for billing backend",
        )
        rev_wo = outcome_partial.revised_work_order
        self.assertIsNotNone(rev_wo)
        self.assertTrue(any("currency formatting" in r for r in rev_wo.technical_requirements))
        self.assertFalse(any("orange" in r for r in rev_wo.technical_requirements))

        # 6b: Total rejection by Manager
        outcome_reject = EngineeringIterationCoordinator.orchestrate_iteration(
            base_work_order=self.base_work_order,
            decision=ManagerIterationDecision.REJECT,
            manager_notes="All feedback rejected; task is closed as not planned",
        )
        self.assertEqual(outcome_reject.decision, ManagerIterationDecision.REJECT)
        self.assertIsNone(outcome_reject.revised_work_order)

    # =========================================================================
    # Scenario 7: Cancellation
    # =========================================================================
    def test_07_cancellation_lifecycle(self) -> None:
        """Scenario 7: Manager cancels the iteration or in-flight execution."""
        # 7a: Iteration decision CANCEL
        outcome = self.bridge.orchestrate_iteration(
            base_work_order=self.base_work_order,
            decision=ManagerIterationDecision.CANCEL,
            manager_notes="Project priorities changed; cancelling further work",
        )
        self.assertEqual(outcome.decision, ManagerIterationDecision.CANCEL)
        self.assertIsNone(outcome.revised_work_order)

        # 7b: In-flight execution cancellation via bridge
        execution = self.bridge.dispatch_work_order(self.base_work_order)
        cancellation = self.bridge.cancel_execution(
            execution=execution,
            requested_by="Manager",
            reason="Emergency stop requested by team lead",
        )
        self.assertEqual(execution.status, ProgrammerExecutionStatus.CANCELLED)
        self.assertEqual(cancellation.requested_by, "Manager")

    # =========================================================================
    # Scenario 8: Lineage preservation across multiple revisions
    # =========================================================================
    def test_08_lineage_preservation_multi_turn(self) -> None:
        """Scenario 8: Multi-revision chain (1 -> 2 -> 3) maintains unbroken audit trail and permission bounds."""
        # Turn 1 -> Turn 2
        outcome2 = EngineeringIterationCoordinator.orchestrate_iteration(
            base_work_order=self.base_work_order,
            decision=ManagerIterationDecision.REQUEST_FIX,
            new_technical_requirements=["Add audit log entry on charge attempt"],
        )
        rev2_wo = outcome2.revised_work_order
        self.assertIsNotNone(rev2_wo)
        self.assertEqual(rev2_wo.revision_number, 2)
        self.assertEqual(rev2_wo.parent_work_order_id, self.base_work_order.work_order_id)
        self.assertEqual(rev2_wo.allowed_paths, self.base_work_order.allowed_paths)
        self.assertEqual(rev2_wo.writable_paths, self.base_work_order.writable_paths)

        # Turn 2 -> Turn 3
        outcome3 = EngineeringIterationCoordinator.orchestrate_iteration(
            base_work_order=rev2_wo,
            decision=ManagerIterationDecision.REQUEST_FIX,
            new_technical_requirements=["Include IP address in audit log"],
        )
        rev3_wo = outcome3.revised_work_order
        self.assertIsNotNone(rev3_wo)
        self.assertEqual(rev3_wo.revision_number, 3)
        self.assertEqual(rev3_wo.parent_work_order_id, rev2_wo.work_order_id)
        self.assertEqual(rev3_wo.trace.get("parent_work_order_id"), rev2_wo.work_order_id)
        self.assertEqual(rev3_wo.allowed_paths, self.base_work_order.allowed_paths)
        self.assertEqual(rev3_wo.writable_paths, self.base_work_order.writable_paths)

    # =========================================================================
    # Scenario 9: Historical immutability
    # =========================================================================
    def test_09_historical_immutability(self) -> None:
        """Scenario 9: Previous work order is strictly immutable when revisions are created."""
        orig_wo_dict = copy.deepcopy(self.base_work_order.to_dict())

        outcome = EngineeringIterationCoordinator.orchestrate_iteration(
            base_work_order=self.base_work_order,
            decision=ManagerIterationDecision.REQUEST_FIX,
            new_technical_requirements=["Brand new requirement that did not exist in base"],
            new_acceptance_criteria=[
                AcceptanceCriterion(
                    criterion_id="ac-new-99",
                    criterion_type=AcceptanceCriterionType.BEHAVIOR_DEMONSTRATED,
                    description="New invariant",
                )
            ],
        )

        self.assertIsNotNone(outcome.revised_work_order)
        # Base work order must be 100% identical to its original state
        current_base_dict = self.base_work_order.to_dict()
        self.assertEqual(current_base_dict, orig_wo_dict)
        self.assertEqual(self.base_work_order.revision_number, 1)
        self.assertIsNone(self.base_work_order.parent_work_order_id)

    # =========================================================================
    # Scenario 10: Non-fix Manager decision routing
    # =========================================================================
    def test_10_manager_decision_routing_non_fix(self) -> None:
        """Scenario 10: Non-fix decisions (ACCEPT, REQUEST_DESIGN_CHANGE, REQUEST_RESEARCH, ESCALATE)."""
        # 10a: ACCEPT
        out_accept = EngineeringIterationCoordinator.orchestrate_iteration(
            base_work_order=self.base_work_order,
            decision=ManagerIterationDecision.ACCEPT,
            manager_notes="All acceptance criteria verified by Tester; approved.",
        )
        self.assertEqual(out_accept.decision, ManagerIterationDecision.ACCEPT)
        self.assertIsNone(out_accept.revised_work_order)

        # 10b: REQUEST_DESIGN_CHANGE
        out_design = EngineeringIterationCoordinator.orchestrate_iteration(
            base_work_order=self.base_work_order,
            decision=ManagerIterationDecision.REQUEST_DESIGN_CHANGE,
            manager_notes="UI review requested: form validation behavior ambiguous.",
        )
        self.assertEqual(out_design.decision, ManagerIterationDecision.REQUEST_DESIGN_CHANGE)
        self.assertEqual(out_design.routing_target, "Designer")
        self.assertIsNone(out_design.revised_work_order)

        # 10c: REQUEST_RESEARCH
        out_research = EngineeringIterationCoordinator.orchestrate_iteration(
            base_work_order=self.base_work_order,
            decision=ManagerIterationDecision.REQUEST_RESEARCH,
            manager_notes="Investigate alternative payment gateway latency benchmarks.",
        )
        self.assertEqual(out_research.decision, ManagerIterationDecision.REQUEST_RESEARCH)
        self.assertEqual(out_research.routing_target, "Researcher")
        self.assertIsNone(out_research.revised_work_order)

        # 10d: ESCALATE
        out_escalate = EngineeringIterationCoordinator.orchestrate_iteration(
            base_work_order=self.base_work_order,
            decision=ManagerIterationDecision.ESCALATE,
            manager_notes="Budget threshold exceeded; requires sponsor review.",
        )
        self.assertEqual(out_escalate.decision, ManagerIterationDecision.ESCALATE)
        self.assertEqual(out_escalate.routing_target, "ManagerEscalation")
        self.assertIsNone(out_escalate.revised_work_order)


if __name__ == "__main__":
    unittest.main()
