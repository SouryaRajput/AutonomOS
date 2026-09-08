from __future__ import annotations

import json
import unittest
from typing import Any, Optional

from core.enums import RiskLevel
from core.programmer.contracts.acceptance_criteria import AcceptanceCriterion
from core.programmer.contracts.command_scope import AllowedCommand
from core.programmer.contracts.engineering_risk import (
    EngineeringRisk,
    RiskAssessment,
)
from core.programmer.contracts.escalation import (
    EscalationCoordinator,
    EscalationStatus,
    ManagerEscalationResponse,
    ManagerEscalationResponseAction,
    ProgrammerEscalationCategory,
)
from core.programmer.contracts.execution import ProgrammerExecution
from core.programmer.contracts.execution_context import ProgrammerExecutionContext
from core.programmer.contracts.identifiers import (
    PLAN_VALIDATION_RESULT_ID_PREFIX,
    new_codebase_understanding_id,
    new_engineering_risk_id,
    new_execution_id,
    new_impact_analysis_id,
    new_plan_id,
    new_risk_assessment_id,
    new_work_order_id,
    new_workspace_id,
    validate_plan_validation_result_id,
)
from core.programmer.contracts.impact_analysis import ImpactAnalysis
from core.programmer.contracts.implementation_plan import (
    EscalationCandidate,
    ImplementationPlan,
    ImplementationStep,
)
from core.programmer.contracts.plan_validator import (
    PlanValidationResult,
    PlanValidator,
)
from core.programmer.contracts.verification import VerificationEvidence
from core.programmer.contracts.work_order import ProgrammerWorkOrder
from core.programmer.contracts.workspace import ProgrammerWorkspace
from core.programmer.errors import ProgrammerLineageError
from core.programmer.types import (
    EngineeringRiskCategory,
    PlanValidationStatus,
    ProgrammerBlockerSeverity,
    ProgrammerExecutionStatus,
    VerificationEvidenceSourceType,
)


class TestProgrammerPlanValidation(unittest.TestCase):
    """
    Unit test suite for PROGRAMMER V1 — PHASE 7.5: Plan Validation & Manager Escalation.
    Verifies 10-dimension evaluation, 4-status determination, deterministic escalation candidate
    generation, shell construct security, and integration with EscalationCoordinator.
    """

    def setUp(self) -> None:
        self.validator = PlanValidator()
        self.project_id = "proj-p75"
        self.manager_task_id = "mtask-p75"
        self.correlation_id = "corr-p75"

    def _create_work_order(
        self,
        objective: str = "Add helper utility function",
        allowed_paths: Optional[list[str]] = None,
        writable_paths: Optional[list[str]] = None,
        forbidden_paths: Optional[list[str]] = None,
        allowed_commands: Optional[list[Any]] = None,
        acceptance_criteria: Optional[list[AcceptanceCriterion]] = None,
        required_checks: Optional[list[str]] = None,
        iteration_budget: int = 10,
    ) -> ProgrammerWorkOrder:
        return ProgrammerWorkOrder(
            work_order_id=new_work_order_id(),
            manager_task_id=self.manager_task_id,
            project_id=self.project_id,
            correlation_id=self.correlation_id,
            objective=objective,
            allowed_paths=allowed_paths or ["src/", "tests/"],
            writable_paths=writable_paths or ["src/utils/"],
            forbidden_paths=forbidden_paths or [".secrets/", "config/prod.json"],
            allowed_commands=allowed_commands or [
                AllowedCommand(command="pytest tests/test_helpers.py", description="Run helper tests"),
                AllowedCommand(command="pytest", description="Run pytest test suite"),
            ],
            acceptance_criteria=acceptance_criteria or [
                AcceptanceCriterion(
                    criterion_id="ac-001",
                    description="Helper function returns correct string format",
                    is_mandatory=True,
                )
            ],
            required_checks=required_checks or ["pytest"],
            iteration_budget=iteration_budget,
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
        objective: Optional[str] = None,
        steps: Optional[list[ImplementationStep]] = None,
        affected_files: Optional[list[str]] = None,
        required_checks: Optional[list[str]] = None,
        unknowns: Optional[list[str]] = None,
        escalation_points: Optional[list[EscalationCandidate]] = None,
    ) -> ImplementationPlan:
        plan_steps = steps or [
            ImplementationStep(
                step_id="pstep-001",
                description="Implement helper function satisfying ac-001",
                action_type="modify",
                target_files=["src/utils/helpers.py"],
                dependencies=[],
                verification="pytest tests/test_helpers.py",
                acceptance_criteria_ids=["ac-001"],
            )
        ]
        return ImplementationPlan(
            plan_id=new_plan_id(),
            execution_id=execution_id or new_execution_id(),
            work_order_id=work_order.work_order_id,
            project_id=work_order.project_id,
            objective=objective if objective is not None else work_order.objective,
            steps=plan_steps,
            affected_files=affected_files or ["src/utils/helpers.py"],
            affected_modules=["utils"],
            required_checks=required_checks or ["pytest tests/test_helpers.py"],
            unknowns=unknowns or [],
            escalation_points=escalation_points or [],
        )

    def _create_impact_analysis(
        self,
        work_order: ProgrammerWorkOrder,
        execution_id: str,
        unknowns: Optional[list[str]] = None,
    ) -> ImpactAnalysis:
        return ImpactAnalysis(
            analysis_id=new_impact_analysis_id(),
            execution_id=execution_id,
            work_order_id=work_order.work_order_id,
            project_id=work_order.project_id,
            understanding_id=new_codebase_understanding_id(),
            directly_affected_files=["src/utils/helpers.py"],
            unknowns=unknowns or [],
        )

    def _create_risk_assessment(
        self,
        work_order: ProgrammerWorkOrder,
        execution_id: str,
        plan_id: Optional[str] = None,
        risks: Optional[list[EngineeringRisk]] = None,
    ) -> RiskAssessment:
        return RiskAssessment(
            assessment_id=new_risk_assessment_id(),
            execution_id=execution_id,
            work_order_id=work_order.work_order_id,
            project_id=work_order.project_id,
            plan_id=plan_id or new_plan_id(),
            overall_risk_level=RiskLevel.LOW,
            risks=risks or [],
        )

    # =========================================================================
    # 1. Clean Valid Plan (APPROVED)
    # =========================================================================

    def test_valid_plan_approved(self) -> None:
        wo = self._create_work_order()
        plan = self._create_plan(wo)
        impact = self._create_impact_analysis(wo, plan.execution_id)
        risks = self._create_risk_assessment(wo, plan.execution_id, plan_id=plan.plan_id)

        result = self.validator.validate_plan(
            plan=plan,
            work_order=wo,
            impact_analysis=impact,
            risk_assessment=risks,
        )

        validate_plan_validation_result_id(result.result_id)
        self.assertTrue(result.result_id.startswith(PLAN_VALIDATION_RESULT_ID_PREFIX))
        self.assertEqual(result.status, PlanValidationStatus.APPROVED)
        self.assertTrue(result.valid)
        self.assertEqual(len(result.warnings), 0)
        self.assertEqual(len(result.blocking_issues), 0)
        self.assertEqual(len(result.escalation_candidates), 0)
        self.assertFalse(result.has_escalations())
        self.assertEqual(len(result.evidence), 1)
        self.assertEqual(result.evidence[0].source_type, VerificationEvidenceSourceType.PLAN_VALIDATION)
        self.assertFalse(result.evidence[0].is_agent_claim)

    # =========================================================================
    # 2. Approved With Warnings (APPROVED_WITH_WARNINGS)
    # =========================================================================

    def test_approved_with_warnings(self) -> None:
        wo = self._create_work_order(required_checks=["pytest", "mypy"])
        plan = self._create_plan(wo, required_checks=["pytest tests/test_helpers.py"])
        # mypy not explicitly in plan required_checks -> triggers warning
        # Low severity risk with escalation_required=False -> triggers advisory warning
        advisory_risk = EngineeringRisk(
            risk_id=new_engineering_risk_id(),
            category=EngineeringRiskCategory.CONFIGURATION_CHANGE,
            severity=RiskLevel.LOW,
            description="Minor config comment adjustment",
            affected_area="src/utils/helpers.py",
            escalation_required=False,
        )
        risks = self._create_risk_assessment(wo, plan.execution_id, plan_id=plan.plan_id, risks=[advisory_risk])

        result = self.validator.validate_plan(
            plan=plan,
            work_order=wo,
            risk_assessment=risks,
        )

        self.assertEqual(result.status, PlanValidationStatus.APPROVED_WITH_WARNINGS)
        self.assertTrue(result.valid)
        self.assertGreater(len(result.warnings), 0)
        self.assertEqual(len(result.blocking_issues), 0)
        self.assertEqual(len(result.escalation_candidates), 0)

    # =========================================================================
    # 3. Objective Divergence (REQUIRES_ESCALATION)
    # =========================================================================

    def test_objective_divergence_escalates(self) -> None:
        wo = self._create_work_order(objective="Add helper utility function")
        plan = self._create_plan(wo, objective="Re-architect authentication subsystem")

        result = self.validator.validate_plan(plan=plan, work_order=wo)

        self.assertEqual(result.status, PlanValidationStatus.REQUIRES_ESCALATION)
        self.assertFalse(result.valid)
        self.assertTrue(result.has_escalations())
        self.assertIn("Plan objective", result.blocking_issues[0])

        categories = [e.category for e in result.escalation_candidates]
        self.assertIn(ProgrammerEscalationCategory.SCOPE, categories)

    # =========================================================================
    # 4. Mandatory Acceptance Criteria Coverage (REQUIRES_ESCALATION)
    # =========================================================================

    def test_missing_mandatory_acceptance_criteria_escalates(self) -> None:
        wo = self._create_work_order(
            acceptance_criteria=[
                AcceptanceCriterion(
                    criterion_id="ac-001",
                    description="Helper returns correct format",
                    is_mandatory=True,
                ),
                AcceptanceCriterion(
                    criterion_id="ac-002",
                    description="Helper handles negative numbers",
                    is_mandatory=True,
                ),
            ]
        )
        # Step only covers ac-001; ac-002 is uncovered
        step = ImplementationStep(
            step_id="pstep-001",
            description="Implement helper format",
            action_type="modify",
            target_files=["src/utils/helpers.py"],
            verification="pytest tests/test_helpers.py",
            acceptance_criteria_ids=["ac-001"],
        )
        plan = self._create_plan(wo, steps=[step])

        result = self.validator.validate_plan(plan=plan, work_order=wo)

        self.assertEqual(result.status, PlanValidationStatus.REQUIRES_ESCALATION)
        self.assertFalse(result.valid)
        self.assertTrue(any("ac-002" in issue for issue in result.blocking_issues))
        self.assertTrue(any("ac-002" in info for info in result.missing_information))
        self.assertTrue(any(e.category == ProgrammerEscalationCategory.VERIFICATION for e in result.escalation_candidates))

    # =========================================================================
    # 5. Filesystem Scope: Forbidden Path (REQUIRES_ESCALATION)
    # =========================================================================

    def test_forbidden_file_access_escalates(self) -> None:
        wo = self._create_work_order(
            forbidden_paths=[".secrets/", "config/prod.json"]
        )
        step = ImplementationStep(
            step_id="pstep-001",
            description="Read secret key",
            action_type="read",
            target_files=[".secrets/api_key.pem"],
            verification="pytest",
        )
        plan = self._create_plan(wo, steps=[step], affected_files=[".secrets/api_key.pem"])

        result = self.validator.validate_plan(plan=plan, work_order=wo)

        self.assertEqual(result.status, PlanValidationStatus.REQUIRES_ESCALATION)
        self.assertFalse(result.valid)
        self.assertTrue(any(".secrets/api_key.pem" in issue for issue in result.blocking_issues))
        self.assertTrue(any(e.category == ProgrammerEscalationCategory.SCOPE for e in result.escalation_candidates))

    # =========================================================================
    # 6. Filesystem Scope: Modifying Non-Writable File (REQUIRES_ESCALATION)
    # =========================================================================

    def test_non_writable_file_modification_escalates(self) -> None:
        wo = self._create_work_order(
            allowed_paths=["src/"],
            writable_paths=["src/utils/"],
        )
        # src/main.py is in allowed_paths but NOT in writable_paths
        step = ImplementationStep(
            step_id="pstep-001",
            description="Update entrypoint imports",
            action_type="modify",
            target_files=["src/main.py"],
            verification="pytest",
            acceptance_criteria_ids=["ac-001"],
        )
        plan = self._create_plan(wo, steps=[step], affected_files=["src/main.py"])

        result = self.validator.validate_plan(plan=plan, work_order=wo)

        self.assertEqual(result.status, PlanValidationStatus.REQUIRES_ESCALATION)
        self.assertFalse(result.valid)
        self.assertTrue(any("src/main.py" in issue and "writable_paths" in issue for issue in result.blocking_issues))
        scope_candidates = [e for e in result.escalation_candidates if e.category == ProgrammerEscalationCategory.SCOPE]
        self.assertGreater(len(scope_candidates), 0)
        self.assertEqual(scope_candidates[0].target, "src/main.py")

    # =========================================================================
    # 7. Command Scope: Unauthorized Verification Command (REQUIRES_ESCALATION)
    # =========================================================================

    def test_unauthorized_command_escalates(self) -> None:
        wo = self._create_work_order(
            allowed_commands=[AllowedCommand(command="pytest tests/test_helpers.py", description="Run helper tests")]
        )
        # Plan verification uses 'npm test' which is unlisted
        step = ImplementationStep(
            step_id="pstep-001",
            description="Implement helper",
            action_type="modify",
            target_files=["src/utils/helpers.py"],
            verification="npm test",
            acceptance_criteria_ids=["ac-001"],
        )
        plan = self._create_plan(wo, steps=[step])

        result = self.validator.validate_plan(plan=plan, work_order=wo)

        self.assertEqual(result.status, PlanValidationStatus.REQUIRES_ESCALATION)
        self.assertFalse(result.valid)
        perm_candidates = [e for e in result.escalation_candidates if e.category == ProgrammerEscalationCategory.PERMISSION]
        self.assertGreater(len(perm_candidates), 0)
        self.assertEqual(perm_candidates[0].target, "npm test")

    # =========================================================================
    # 8. Command Scope: Dangerous Shell Metacharacters (INVALID)
    # =========================================================================

    def test_dangerous_shell_command_invalidates_plan(self) -> None:
        wo = self._create_work_order()
        dangerous_commands = [
            "pytest && rm -rf /",
            "pytest; cat /etc/passwd",
            "pytest || echo fail",
            "pytest | grep pass",
            "pytest\ncat secrets",
        ]

        for bad_cmd in dangerous_commands:
            step = ImplementationStep(
                step_id="pstep-001",
                description="Dangerous check",
                action_type="modify",
                target_files=["src/utils/helpers.py"],
                verification=bad_cmd,
                acceptance_criteria_ids=["ac-001"],
            )
            plan = self._create_plan(wo, steps=[step])

            result = self.validator.validate_plan(plan=plan, work_order=wo)
            self.assertEqual(
                result.status,
                PlanValidationStatus.INVALID,
                f"Expected INVALID for dangerous command '{bad_cmd}', got {result.status.value}",
            )
            self.assertFalse(result.valid)
            self.assertTrue(any("Dangerous" in issue or "dangerous" in issue or "Unsupported" in issue for issue in result.blocking_issues))

    # =========================================================================
    # 9. Unknown Dependencies (REQUIRES_ESCALATION)
    # =========================================================================

    def test_unresolved_dependencies_escalates(self) -> None:
        wo = self._create_work_order()
        plan = self._create_plan(wo)
        impact = self._create_impact_analysis(wo, plan.execution_id, unknowns=["stripe-sdk-v3", "auth0-domain"])

        result = self.validator.validate_plan(plan=plan, work_order=wo, impact_analysis=impact)

        self.assertEqual(result.status, PlanValidationStatus.REQUIRES_ESCALATION)
        self.assertFalse(result.valid)
        self.assertIn("Unknown dependency: stripe-sdk-v3", result.missing_information)
        dep_candidates = [e for e in result.escalation_candidates if e.category == ProgrammerEscalationCategory.DEPENDENCY]
        self.assertEqual(len(dep_candidates), 2)

    # =========================================================================
    # 10. Material Engineering Risk (REQUIRES_ESCALATION)
    # =========================================================================

    def test_material_risk_escalates(self) -> None:
        wo = self._create_work_order()
        plan = self._create_plan(wo)
        risk = EngineeringRisk(
            risk_id=new_engineering_risk_id(),
            category=EngineeringRiskCategory.DATA_LOSS,
            severity=RiskLevel.HIGH,
            description="Migration script drops production table columns",
            affected_area="migrations/002_drop.sql",
            escalation_required=True,
        )
        risk_assessment = self._create_risk_assessment(wo, plan.execution_id, plan_id=plan.plan_id, risks=[risk])

        result = self.validator.validate_plan(plan=plan, work_order=wo, risk_assessment=risk_assessment)

        self.assertEqual(result.status, PlanValidationStatus.REQUIRES_ESCALATION)
        self.assertFalse(result.valid)
        self.assertTrue(any("DATA_LOSS" in issue for issue in result.blocking_issues))
        self.assertTrue(any(e.category == ProgrammerEscalationCategory.ARCHITECTURAL for e in result.escalation_candidates))

    # =========================================================================
    # 11. Hidden Architectural Change (REQUIRES_ESCALATION)
    # =========================================================================

    def test_architectural_change_escalates(self) -> None:
        wo = self._create_work_order()
        plan = self._create_plan(wo)
        arch_risk = EngineeringRisk(
            risk_id=new_engineering_risk_id(),
            category=EngineeringRiskCategory.ARCHITECTURAL_CHANGE,
            severity=RiskLevel.HIGH,
            description="Replaces monolith repository layout with microservices",
            affected_area="src/",
            escalation_required=True,
        )
        risk_assessment = self._create_risk_assessment(wo, plan.execution_id, plan_id=plan.plan_id, risks=[arch_risk])

        result = self.validator.validate_plan(plan=plan, work_order=wo, risk_assessment=risk_assessment)

        self.assertEqual(result.status, PlanValidationStatus.REQUIRES_ESCALATION)
        self.assertFalse(result.valid)
        arch_candidates = [e for e in result.escalation_candidates if e.category == ProgrammerEscalationCategory.ARCHITECTURAL]
        self.assertGreater(len(arch_candidates), 0)

    # =========================================================================
    # 12. Budget Compliance Overflow (REQUIRES_ESCALATION)
    # =========================================================================

    def test_budget_overflow_escalates(self) -> None:
        wo = self._create_work_order(iteration_budget=2)
        # Create plan with 3 steps, exceeding iteration_budget of 2
        steps = [
            ImplementationStep(
                step_id=f"pstep-{i:03d}",
                description=f"Step {i} satisfying ac-001",
                action_type="modify",
                target_files=["src/utils/helpers.py"],
                verification="pytest tests/test_helpers.py",
                acceptance_criteria_ids=["ac-001"],
            )
            for i in range(1, 4)
        ]
        plan = self._create_plan(wo, steps=steps)

        result = self.validator.validate_plan(plan=plan, work_order=wo)

        self.assertEqual(result.status, PlanValidationStatus.REQUIRES_ESCALATION)
        self.assertFalse(result.valid)
        self.assertTrue(any("iteration budget" in issue for issue in result.blocking_issues))
        resource_candidates = [e for e in result.escalation_candidates if e.category == ProgrammerEscalationCategory.RESOURCE]
        self.assertEqual(len(resource_candidates), 1)
        self.assertEqual(resource_candidates[0].target, "iteration_budget")

    # =========================================================================
    # 13. End-to-End Manager Escalation Integration Flow
    # =========================================================================

    def test_escalation_coordinator_flow_with_manager_approval(self) -> None:
        wo = self._create_work_order(writable_paths=["src/utils/"])
        execution = self._create_execution(wo)

        # Plan targets src/core/main.py which requires write expansion
        step = ImplementationStep(
            step_id="pstep-001",
            description="Modify core main",
            action_type="modify",
            target_files=["src/core/main.py"],
            verification="pytest tests/test_helpers.py",
            acceptance_criteria_ids=["ac-001"],
        )
        plan = self._create_plan(wo, execution_id=execution.execution_id, steps=[step], affected_files=["src/core/main.py"])

        result = self.validator.validate_plan(plan=plan, work_order=wo)
        self.assertEqual(result.status, PlanValidationStatus.REQUIRES_ESCALATION)
        self.assertFalse(result.valid)
        self.assertEqual(len(result.escalation_candidates), 1)

        candidate = result.escalation_candidates[0]
        self.assertEqual(candidate.category, ProgrammerEscalationCategory.SCOPE)

        # Escalate through EscalationCoordinator
        coordinator = EscalationCoordinator()
        escalation = coordinator.escalate_candidate(
            execution=execution,
            work_order=wo,
            candidate=candidate,
        )

        # Execution must be BLOCKED
        self.assertEqual(execution.status, ProgrammerExecutionStatus.BLOCKED)
        self.assertEqual(escalation.status, EscalationStatus.PENDING)
        self.assertEqual(escalation.work_order_id, wo.work_order_id)
        self.assertEqual(escalation.execution_id, execution.execution_id)

        # Manager resolves by modifying the WorkOrder to expand writable_paths
        response = ManagerEscalationResponse(
            response_id="mresp-plan-001",
            escalation_id=escalation.escalation_id,
            action=ManagerEscalationResponseAction.MODIFY_WORK_ORDER,
            responder="manager",
            reason="Approved expanding writable_paths to include src/core/",
            additional_context={
                "modifications": {
                    "writable_paths": ["src/utils/", "src/core/main.py"],
                }
            },
        )

        final_status, revised_wo = coordinator.apply_response(
            escalation=escalation,
            response=response,
            execution=execution,
            work_order=wo,
        )

        # Verify escalation resolved, execution unblocked, WorkOrder revised
        self.assertEqual(final_status, EscalationStatus.RESOLVED)
        self.assertEqual(execution.status, ProgrammerExecutionStatus.RUNNING)
        self.assertIsNotNone(revised_wo)
        self.assertIn("src/core/main.py", revised_wo.writable_paths)
        self.assertEqual(revised_wo.revision_number, wo.revision_number + 1)

        # Now re-validate the plan against the revised WorkOrder -> APPROVED!
        plan.work_order_id = revised_wo.work_order_id
        second_result = self.validator.validate_plan(plan=plan, work_order=revised_wo)
        self.assertEqual(second_result.status, PlanValidationStatus.APPROVED)
        self.assertTrue(second_result.valid)

    # =========================================================================
    # 14. Serialization Roundtrip
    # =========================================================================

    def test_serialization_roundtrip(self) -> None:
        wo = self._create_work_order()
        plan = self._create_plan(wo)
        result = self.validator.validate_plan(plan=plan, work_order=wo)

        as_dict = result.to_dict()
        as_json = result.to_json()

        deserialized_from_dict = PlanValidationResult.from_dict(as_dict)
        deserialized_from_json = PlanValidationResult.from_json(as_json)

        self.assertEqual(deserialized_from_dict.result_id, result.result_id)
        self.assertEqual(deserialized_from_dict.status, result.status)
        self.assertEqual(deserialized_from_dict.valid, result.valid)
        self.assertEqual(deserialized_from_dict.evidence[0].evidence_id, result.evidence[0].evidence_id)

        self.assertEqual(deserialized_from_json.result_id, result.result_id)
        self.assertEqual(deserialized_from_json.status, result.status)
        self.assertEqual(deserialized_from_json.valid, result.valid)

    # =========================================================================
    # 15. Lineage Verification
    # =========================================================================

    def test_lineage_mismatch_raises_error(self) -> None:
        wo = self._create_work_order()
        foreign_wo = self._create_work_order()
        plan = self._create_plan(foreign_wo)

        with self.assertRaises(ProgrammerLineageError):
            self.validator.validate_plan(plan=plan, work_order=wo)

    # =========================================================================
    # 16. Escalation Candidate Generation & Contract
    # =========================================================================

    def test_escalation_generation(self) -> None:
        wo = self._create_work_order(writable_paths=["src/utils/"])
        execution = self._create_execution(wo)
        step = ImplementationStep(
            step_id="pstep-001",
            description="Modify unlisted file",
            action_type="modify",
            target_files=["src/core/main.py"],
            verification="pytest tests/test_helpers.py",
            acceptance_criteria_ids=["ac-001"],
        )
        plan = self._create_plan(wo, execution_id=execution.execution_id, steps=[step], affected_files=["src/core/main.py"])
        result = self.validator.validate_plan(plan=plan, work_order=wo)

        self.assertEqual(result.status, PlanValidationStatus.REQUIRES_ESCALATION)
        self.assertFalse(result.valid)
        self.assertGreater(len(result.escalation_candidates), 0)

        candidate = result.escalation_candidates[0]
        self.assertIsNotNone(candidate.candidate_id)
        self.assertEqual(candidate.category, ProgrammerEscalationCategory.SCOPE)
        self.assertIn("src/core/main.py", candidate.target)
        self.assertTrue(candidate.requested_decision)
        self.assertTrue(candidate.suggested_options)

        # Convert to formal ProgrammerEscalation
        escalation = candidate.to_escalation(execution, wo)
        self.assertEqual(escalation.execution_id, execution.execution_id)
        self.assertEqual(escalation.work_order_id, wo.work_order_id)
        self.assertEqual(escalation.category, candidate.category)
        self.assertEqual(escalation.requested_decision, candidate.requested_decision)

    # Aliases matching explicit test requirements
    test_valid_plan = test_valid_plan_approved
    test_warning_only_plan = test_approved_with_warnings
    test_scope_expansion = test_non_writable_file_modification_escalates
    test_permission_expansion = test_unauthorized_command_escalates
    test_architecture_change = test_architectural_change_escalates
    test_missing_acceptance_coverage = test_missing_mandatory_acceptance_criteria_escalates
    test_invalid_command = test_dangerous_shell_command_invalidates_plan
    test_budget_overflow = test_budget_overflow_escalates
    test_manager_decision_flow = test_escalation_coordinator_flow_with_manager_approval


if __name__ == "__main__":
    unittest.main()
