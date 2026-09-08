from __future__ import annotations

import json
import unittest
from typing import Any, Optional

from core.enums import RiskLevel
from core.programmer.contracts.acceptance_criteria import AcceptanceCriterion
from core.programmer.contracts.command_scope import AllowedCommand
from core.programmer.contracts.engineering_risk import (
    EngineeringRisk,
)
from core.programmer.contracts.escalation import (
    EscalationCoordinator,
    EscalationStatus,
    ManagerEscalationResponse,
    ManagerEscalationResponseAction,
    ProgrammerEscalationCategory,
)
from core.programmer.contracts.event_translation import ProgrammerExecutionEvent
from core.programmer.contracts.execution import ProgrammerExecution
from core.programmer.contracts.execution_context import ProgrammerExecutionContext
from core.programmer.contracts.execution_supervisor import (
    ExecutionSupervisionRecord,
    ExecutionSupervisor,
    PlanDeviation,
)
from core.programmer.contracts.identifiers import (
    PLAN_DEVIATION_ID_PREFIX,
    SUPERVISION_RECORD_ID_PREFIX,
    new_engineering_risk_id,
    new_execution_id,
    new_plan_deviation_id,
    new_plan_id,
    new_supervision_record_id,
    new_work_order_id,
    validate_plan_deviation_id,
    validate_supervision_record_id,
)
from core.programmer.contracts.implementation_plan import (
    ImplementationPlan,
    ImplementationStep,
)
from core.programmer.contracts.work_order import ProgrammerWorkOrder
from core.programmer.errors import ProgrammerLineageError
from core.programmer.types import (
    DeviationClassification,
    EngineeringRiskCategory,
    ExecutionSupervisionStatus,
    PlanDeviationCategory,
    ProgrammerBlockerSeverity,
    ProgrammerExecutionEventType,
    ProgrammerExecutionStatus,
)


class TestProgrammerExecutionSupervision(unittest.TestCase):
    """
    Unit test suite for PROGRAMMER V1 — PHASE 7.6: Intelligent Execution Supervision.
    Verifies real-time monitoring of execution behavior against planned expectations,
    four-level deviation classification, non-punitive evaluation, emerging risk detection,
    integration with EscalationCoordinator, context provision, and cancellation.
    """

    def setUp(self) -> None:
        self.project_id = "proj-p76"
        self.manager_task_id = "mtask-p76"
        self.correlation_id = "corr-p76"

    def _create_work_order(
        self,
        objective: str = "Implement authentication rate limiter",
        allowed_paths: Optional[list[str]] = None,
        writable_paths: Optional[list[str]] = None,
        forbidden_paths: Optional[list[str]] = None,
        allowed_commands: Optional[list[Any]] = None,
    ) -> ProgrammerWorkOrder:
        return ProgrammerWorkOrder(
            work_order_id=new_work_order_id(),
            manager_task_id=self.manager_task_id,
            project_id=self.project_id,
            correlation_id=self.correlation_id,
            objective=objective,
            allowed_paths=allowed_paths or ["src/", "tests/", "config/"],
            writable_paths=writable_paths or ["src/rate_limiter/", "tests/"],
            forbidden_paths=forbidden_paths or [".secrets/", "config/production.json"],
            allowed_commands=allowed_commands or [
                AllowedCommand(command="pytest tests/test_rate_limiter.py", description="Run limiter tests"),
                AllowedCommand(command="pytest", description="Run test suite"),
                AllowedCommand(command="git status", description="Check git status"),
            ],
            acceptance_criteria=[
                AcceptanceCriterion(
                    criterion_id="ac-001",
                    description="Rate limiter restricts burst traffic",
                    is_mandatory=True,
                )
            ],
            required_checks=["pytest tests/test_rate_limiter.py"],
            iteration_budget=10,
        )

    def _create_execution(self, work_order: ProgrammerWorkOrder) -> ProgrammerExecution:
        execution = ProgrammerExecution(
            execution_id=new_execution_id(),
            work_order_id=work_order.work_order_id,
            task_id=work_order.manager_task_id,
            project_id=work_order.project_id,
            correlation_id=work_order.correlation_id,
        )
        execution.status = ProgrammerExecutionStatus.RUNNING
        return execution

    def _create_plan(
        self,
        work_order: ProgrammerWorkOrder,
        execution_id: str = "",
        steps: Optional[list[ImplementationStep]] = None,
        affected_files: Optional[list[str]] = None,
    ) -> ImplementationPlan:
        plan_steps = steps or [
            ImplementationStep(
                step_id="pstep-001",
                description="Create rate limiter core interface",
                action_type="create",
                target_files=["src/rate_limiter/core.py"],
                dependencies=[],
                verification="pytest tests/test_rate_limiter.py",
                acceptance_criteria_ids=["ac-001"],
            ),
            ImplementationStep(
                step_id="pstep-002",
                description="Add unit tests for rate limiter",
                action_type="create",
                target_files=["tests/test_rate_limiter.py"],
                dependencies=["pstep-001"],
                verification="pytest tests/test_rate_limiter.py",
                acceptance_criteria_ids=["ac-001"],
            ),
        ]
        return ImplementationPlan(
            plan_id=new_plan_id(),
            execution_id=execution_id or new_execution_id(),
            work_order_id=work_order.work_order_id,
            project_id=work_order.project_id,
            objective=work_order.objective,
            steps=plan_steps,
            affected_files=affected_files or ["src/rate_limiter/core.py", "tests/test_rate_limiter.py"],
            affected_modules=["rate_limiter"],
            required_checks=["pytest tests/test_rate_limiter.py"],
        )

    def _create_supervisor(
        self,
        work_order: Optional[ProgrammerWorkOrder] = None,
        plan: Optional[ImplementationPlan] = None,
    ) -> tuple[ExecutionSupervisor, ProgrammerWorkOrder, ImplementationPlan]:
        wo = work_order or self._create_work_order()
        p = plan or self._create_plan(wo)
        supervisor = ExecutionSupervisor(plan=p, work_order=wo)
        return supervisor, wo, p

    # =========================================================================
    # 1. Normal Plan Execution
    # =========================================================================

    def test_normal_plan_execution(self) -> None:
        supervisor, wo, plan = self._create_supervisor()

        validate_supervision_record_id(supervisor.record.supervision_id)
        self.assertTrue(supervisor.record.supervision_id.startswith(SUPERVISION_RECORD_ID_PREFIX))
        self.assertEqual(supervisor.record.status, ExecutionSupervisionStatus.SUPERVISING)
        self.assertEqual(supervisor.record.progress_percentage, 0.0)

        # Step 1: Start
        dev1 = supervisor.record_step_start("pstep-001")
        self.assertIsNone(dev1)
        self.assertEqual(supervisor.record.current_step_id, "pstep-001")

        # Step 1: File operation matching plan
        evt1 = ProgrammerExecutionEvent(
            execution_id=supervisor.record.execution_id,
            work_order_id=wo.work_order_id,
            event_type=ProgrammerExecutionEventType.FILE_OPERATION,
            payload={"path": "src/rate_limiter/core.py", "operation": "write"},
        )
        dev_file = supervisor.observe_event(evt1)
        self.assertIsNone(dev_file)
        self.assertIn("src/rate_limiter/core.py", supervisor.record.files_modified)

        # Step 1: Complete with verification
        dev_comp1 = supervisor.record_step_completion("pstep-001", verification_passed=True, verification_output="OK")
        self.assertIsNone(dev_comp1)
        self.assertEqual(supervisor.record.progress_percentage, 50.0)
        self.assertIn("pstep-001", supervisor.record.completed_steps)

        # Step 2: Start (dependencies met)
        dev2 = supervisor.record_step_start("pstep-002")
        self.assertIsNone(dev2)

        # Step 2: Complete
        dev_comp2 = supervisor.record_step_completion("pstep-002", verification_passed=True, verification_output="OK")
        self.assertIsNone(dev_comp2)
        self.assertEqual(supervisor.record.progress_percentage, 100.0)
        self.assertEqual(supervisor.record.status, ExecutionSupervisionStatus.COMPLETED)
        self.assertFalse(supervisor.record.has_blocking_deviations())
        self.assertFalse(supervisor.record.has_material_deviations())

    # =========================================================================
    # 2. Expected Deviation (Allowed Autonomous)
    # =========================================================================

    def test_expected_deviation(self) -> None:
        supervisor, wo, plan = self._create_supervisor()

        # Reading an allowed auxiliary config file not explicitly in step targets
        evt = ProgrammerExecutionEvent(
            execution_id=supervisor.record.execution_id,
            work_order_id=wo.work_order_id,
            event_type=ProgrammerExecutionEventType.FILE_OPERATION,
            payload={"path": "config/settings.py", "operation": "read"},
        )
        deviation = supervisor.observe_event(evt)

        self.assertIsNotNone(deviation)
        validate_plan_deviation_id(deviation.deviation_id)
        self.assertTrue(deviation.deviation_id.startswith(PLAN_DEVIATION_ID_PREFIX))
        self.assertEqual(deviation.classification, DeviationClassification.EXPECTED)
        self.assertEqual(deviation.action_taken, "ALLOWED_AUTONOMOUS")
        self.assertEqual(supervisor.record.status, ExecutionSupervisionStatus.SUPERVISING)
        self.assertFalse(supervisor.record.has_blocking_deviations())
        self.assertFalse(supervisor.record.has_material_deviations())

    # =========================================================================
    # 3. Minor Deviation (Allowed Autonomous)
    # =========================================================================

    def test_minor_deviation(self) -> None:
        supervisor, wo, plan = self._create_supervisor()

        # Running an authorized command (git status) that was not in the plan verification
        evt = ProgrammerExecutionEvent(
            execution_id=supervisor.record.execution_id,
            work_order_id=wo.work_order_id,
            event_type=ProgrammerExecutionEventType.COMMAND_OPERATION,
            payload={"command": "git status"},
        )
        deviation = supervisor.observe_event(evt)

        self.assertIsNotNone(deviation)
        self.assertEqual(deviation.classification, DeviationClassification.MINOR)
        self.assertEqual(deviation.category, PlanDeviationCategory.UNEXPECTED_COMMAND)
        self.assertEqual(deviation.action_taken, "ALLOWED_AUTONOMOUS")
        self.assertEqual(supervisor.record.status, ExecutionSupervisionStatus.SUPERVISING)
        self.assertFalse(supervisor.record.has_blocking_deviations())

    # =========================================================================
    # 4. Material Deviation (Surfaced to Policy)
    # =========================================================================

    def test_material_deviation(self) -> None:
        supervisor, wo, plan = self._create_supervisor()

        # Modifying a file that is within writable_paths but unlisted in plan target_files
        evt = ProgrammerExecutionEvent(
            execution_id=supervisor.record.execution_id,
            work_order_id=wo.work_order_id,
            event_type=ProgrammerExecutionEventType.FILE_OPERATION,
            payload={"path": "src/rate_limiter/unplanned_module.py", "operation": "write"},
        )
        deviation = supervisor.observe_event(evt)

        self.assertIsNotNone(deviation)
        self.assertEqual(deviation.classification, DeviationClassification.MATERIAL)
        self.assertEqual(deviation.category, PlanDeviationCategory.UNEXPECTED_FILE)
        self.assertEqual(deviation.action_taken, "SURFACED_POLICY")
        self.assertTrue(supervisor.record.has_material_deviations())
        self.assertFalse(supervisor.record.has_blocking_deviations())
        # Supervision continues (not blocked, but surfaced to policy)
        self.assertEqual(supervisor.record.status, ExecutionSupervisionStatus.SUPERVISING)

    # =========================================================================
    # 5. Unexpected File: Blocking (Forbidden or Non-Writable)
    # =========================================================================

    def test_unexpected_file_blocking_forbidden(self) -> None:
        supervisor, wo, plan = self._create_supervisor()

        # Accessing forbidden path
        evt = ProgrammerExecutionEvent(
            execution_id=supervisor.record.execution_id,
            work_order_id=wo.work_order_id,
            event_type=ProgrammerExecutionEventType.FILE_OPERATION,
            payload={"path": ".secrets/keys.json", "operation": "read"},
        )
        deviation = supervisor.observe_event(evt)

        self.assertIsNotNone(deviation)
        self.assertEqual(deviation.classification, DeviationClassification.BLOCKING)
        self.assertEqual(deviation.category, PlanDeviationCategory.UNEXPECTED_FILE)
        self.assertEqual(supervisor.record.status, ExecutionSupervisionStatus.BLOCKED)
        self.assertTrue(supervisor.record.has_blocking_deviations())
        self.assertIsNotNone(deviation.escalation_candidate)
        self.assertEqual(deviation.escalation_candidate.category, ProgrammerEscalationCategory.SCOPE)

    def test_unexpected_file_blocking_non_writable(self) -> None:
        supervisor, wo, plan = self._create_supervisor()

        # Writing to config/ which is allowed for read, but NOT in writable_paths
        evt = ProgrammerExecutionEvent(
            execution_id=supervisor.record.execution_id,
            work_order_id=wo.work_order_id,
            event_type=ProgrammerExecutionEventType.FILE_OPERATION,
            payload={"path": "config/settings.py", "operation": "write"},
        )
        deviation = supervisor.observe_event(evt)

        self.assertIsNotNone(deviation)
        self.assertEqual(deviation.classification, DeviationClassification.BLOCKING)
        self.assertEqual(supervisor.record.status, ExecutionSupervisionStatus.BLOCKED)
        self.assertIsNotNone(deviation.escalation_candidate)
        self.assertEqual(deviation.escalation_candidate.category, ProgrammerEscalationCategory.SCOPE)
        self.assertIn("config/settings.py", deviation.escalation_candidate.target)

    # =========================================================================
    # 6. Unexpected Command: Blocking
    # =========================================================================

    def test_unexpected_command_blocking(self) -> None:
        supervisor, wo, plan = self._create_supervisor()

        # Executing an unauthorized command (e.g. npm install or pip install)
        evt = ProgrammerExecutionEvent(
            execution_id=supervisor.record.execution_id,
            work_order_id=wo.work_order_id,
            event_type=ProgrammerExecutionEventType.COMMAND_OPERATION,
            payload={"command": "npm install redis"},
        )
        deviation = supervisor.observe_event(evt)

        self.assertIsNotNone(deviation)
        self.assertEqual(deviation.classification, DeviationClassification.BLOCKING)
        self.assertEqual(deviation.category, PlanDeviationCategory.UNEXPECTED_COMMAND)
        self.assertEqual(supervisor.record.status, ExecutionSupervisionStatus.BLOCKED)
        self.assertIsNotNone(deviation.escalation_candidate)
        self.assertEqual(deviation.escalation_candidate.category, ProgrammerEscalationCategory.PERMISSION)

    def test_dangerous_shell_command_blocking(self) -> None:
        supervisor, wo, plan = self._create_supervisor()

        # Executing command with dangerous shell metacharacters
        evt = ProgrammerExecutionEvent(
            execution_id=supervisor.record.execution_id,
            work_order_id=wo.work_order_id,
            event_type=ProgrammerExecutionEventType.COMMAND_OPERATION,
            payload={"command": "pytest && rm -rf /"},
        )
        deviation = supervisor.observe_event(evt)

        self.assertIsNotNone(deviation)
        self.assertEqual(deviation.classification, DeviationClassification.BLOCKING)
        self.assertIn("Dangerous", deviation.escalation_candidate.reason)

    # =========================================================================
    # 7. Repeated Failure on Same Step
    # =========================================================================

    def test_repeated_failure(self) -> None:
        supervisor, wo, plan = self._create_supervisor()

        # First failure on pstep-001 (below threshold)
        dev1 = supervisor.record_step_failure("pstep-001", error_message="SyntaxError in core.py")
        self.assertIsNone(dev1)
        self.assertEqual(supervisor.record.step_failure_counts["pstep-001"], 1)

        # Second failure on pstep-001 (meets threshold of 2)
        dev2 = supervisor.record_step_failure("pstep-001", error_message="NameError in core.py")
        self.assertIsNotNone(dev2)
        self.assertEqual(dev2.classification, DeviationClassification.MATERIAL)
        self.assertEqual(dev2.category, PlanDeviationCategory.REPEATED_FAILURE)
        self.assertEqual(dev2.action_taken, "SURFACED_POLICY")
        self.assertTrue(supervisor.record.has_material_deviations())

    # =========================================================================
    # 8. Newly Discovered Engineering Risk
    # =========================================================================

    def test_newly_discovered_risk(self) -> None:
        supervisor, wo, plan = self._create_supervisor()

        # Newly discovered critical data loss risk
        risk = EngineeringRisk(
            risk_id=new_engineering_risk_id(),
            category=EngineeringRiskCategory.DATA_LOSS,
            severity=RiskLevel.CRITICAL,
            description="Unexpected DROP TABLE found in migration script",
            affected_area="migrations/003_drop.sql",
            escalation_required=True,
        )
        deviation = supervisor.record_emerging_risk(risk)

        self.assertIsNotNone(deviation)
        self.assertEqual(deviation.classification, DeviationClassification.BLOCKING)
        self.assertEqual(deviation.category, PlanDeviationCategory.EMERGING_RISK)
        self.assertEqual(supervisor.record.status, ExecutionSupervisionStatus.BLOCKED)
        self.assertIsNotNone(deviation.escalation_candidate)
        self.assertEqual(deviation.escalation_candidate.severity, ProgrammerBlockerSeverity.CRITICAL)

    # =========================================================================
    # 9. Step Dependency Violation
    # =========================================================================

    def test_step_dependency_violation(self) -> None:
        supervisor, wo, plan = self._create_supervisor()

        # pstep-002 requires pstep-001. Try starting pstep-002 before pstep-001 completes:
        deviation = supervisor.record_step_start("pstep-002")

        self.assertIsNotNone(deviation)
        self.assertEqual(deviation.classification, DeviationClassification.MATERIAL)
        self.assertEqual(deviation.category, PlanDeviationCategory.STEP_DEPENDENCY_VIOLATION)
        self.assertIn("pstep-001", deviation.description)
        self.assertTrue(supervisor.record.has_material_deviations())

    # =========================================================================
    # 10. Missing Required Verification
    # =========================================================================

    def test_missing_verification(self) -> None:
        supervisor, wo, plan = self._create_supervisor()

        # Completing step without passing verification
        dev = supervisor.record_step_completion(
            "pstep-001",
            verification_passed=False,
            verification_output="2 tests failed",
        )

        self.assertIsNotNone(dev)
        self.assertEqual(dev.classification, DeviationClassification.MATERIAL)
        self.assertEqual(dev.category, PlanDeviationCategory.MISSING_VERIFICATION)
        self.assertTrue(supervisor.record.has_material_deviations())

    # =========================================================================
    # 11. Significant Scope Expansion
    # =========================================================================

    def test_scope_expansion_detection(self) -> None:
        supervisor, wo, plan = self._create_supervisor()

        # Plan has 2 affected files. Simulate modifying 6 files within writable paths
        for i in range(1, 7):
            path = f"src/rate_limiter/sub_{i}.py"
            evt = ProgrammerExecutionEvent(
                execution_id=supervisor.record.execution_id,
                work_order_id=wo.work_order_id,
                event_type=ProgrammerExecutionEventType.FILE_OPERATION,
                payload={"path": path, "operation": "write"},
            )
            supervisor.observe_event(evt)

        expansion_dev = supervisor.check_scope_expansion()
        self.assertIsNotNone(expansion_dev)
        self.assertEqual(expansion_dev.classification, DeviationClassification.MATERIAL)
        self.assertEqual(expansion_dev.category, PlanDeviationCategory.SCOPE_EXPANSION)

    # =========================================================================
    # 12. Manager Escalation Integration Flow
    # =========================================================================

    def test_escalation_flow(self) -> None:
        supervisor, wo, plan = self._create_supervisor()
        execution = self._create_execution(wo)

        # Trigger blocking deviation: unauthorized command
        evt = ProgrammerExecutionEvent(
            execution_id=execution.execution_id,
            work_order_id=wo.work_order_id,
            event_type=ProgrammerExecutionEventType.COMMAND_OPERATION,
            payload={"command": "npm install redis"},
        )
        deviation = supervisor.observe_event(evt)
        self.assertIsNotNone(deviation)
        self.assertEqual(deviation.classification, DeviationClassification.BLOCKING)

        coordinator = EscalationCoordinator()
        escalation = supervisor.escalate_blocking(
            deviation=deviation,
            coordinator=coordinator,
            execution=execution,
        )

        self.assertIsNotNone(escalation)
        self.assertEqual(supervisor.record.status, ExecutionSupervisionStatus.ESCALATED)
        self.assertEqual(execution.status, ProgrammerExecutionStatus.BLOCKED)
        self.assertEqual(escalation.status, EscalationStatus.PENDING)

        # Manager resolves with APPROVE
        response = ManagerEscalationResponse(
            response_id="mresp-sup-001",
            escalation_id=escalation.escalation_id,
            action=ManagerEscalationResponseAction.APPROVE,
            responder="manager",
            reason="Approved npm install for this execution",
        )

        final_status, revised_wo = coordinator.apply_response(
            escalation=escalation,
            response=response,
            execution=execution,
            work_order=wo,
        )

        self.assertEqual(final_status, EscalationStatus.RESOLVED)
        self.assertEqual(execution.status, ProgrammerExecutionStatus.RUNNING)

    # =========================================================================
    # 13. Cancellation
    # =========================================================================

    def test_cancellation(self) -> None:
        supervisor, wo, plan = self._create_supervisor()
        supervisor.handle_cancellation(reason="User triggered abort")

        self.assertEqual(supervisor.record.status, ExecutionSupervisionStatus.CANCELLED)
        self.assertTrue(any("Cancelled: User triggered abort" in b for b in supervisor.record.blockers))

    # =========================================================================
    # 14. Structured Correction Context Provision
    # =========================================================================

    def test_correction_context_provision(self) -> None:
        supervisor, wo, plan = self._create_supervisor()
        supervisor.record_step_start("pstep-001")
        supervisor.record_step_completion("pstep-001", verification_passed=False, verification_output="Failure")

        context = supervisor.provide_correction_context()
        self.assertIn("supervision_id", context)
        self.assertIn("pstep-001", context["completed_steps"])
        self.assertIn("pstep-001", context["unverified_steps"])
        self.assertGreater(len(context["material_deviations"]), 0)

    # =========================================================================
    # 15. Serialization Roundtrip
    # =========================================================================

    def test_serialization_roundtrip(self) -> None:
        supervisor, wo, plan = self._create_supervisor()
        supervisor.record_step_start("pstep-001")
        supervisor.record_step_completion("pstep-001", verification_passed=True)

        as_dict = supervisor.record.to_dict()
        as_json = supervisor.record.to_json()

        from_dict_rec = ExecutionSupervisionRecord.from_dict(as_dict)
        from_json_rec = ExecutionSupervisionRecord.from_json(as_json)

        self.assertEqual(from_dict_rec.supervision_id, supervisor.record.supervision_id)
        self.assertEqual(from_dict_rec.status, supervisor.record.status)
        self.assertEqual(from_dict_rec.progress_percentage, supervisor.record.progress_percentage)

        self.assertEqual(from_json_rec.supervision_id, supervisor.record.supervision_id)
        self.assertEqual(from_json_rec.status, supervisor.record.status)
        self.assertEqual(from_json_rec.progress_percentage, supervisor.record.progress_percentage)

    # =========================================================================
    # 16. Lineage Verification
    # =========================================================================

    def test_lineage_mismatch_raises_error(self) -> None:
        wo = self._create_work_order()
        other_wo = self._create_work_order()
        plan = self._create_plan(other_wo)

        with self.assertRaises(ProgrammerLineageError):
            ExecutionSupervisor(plan=plan, work_order=wo)

    # Aliases matching explicit prompt requirements
    test_unexpected_file = test_unexpected_file_blocking_forbidden
    test_unexpected_command = test_unexpected_command_blocking
    test_escalation = test_escalation_flow


if __name__ == "__main__":
    unittest.main()
