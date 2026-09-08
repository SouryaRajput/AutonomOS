from __future__ import annotations

import json
import unittest

from core.enums import RiskLevel
from core.programmer.contracts.acceptance_criteria import AcceptanceCriterion
from core.programmer.contracts.codebase_understanding import (
    CodebaseUnderstanding,
    UnderstandingInsight,
)
from core.programmer.contracts.command_scope import AllowedCommand
from core.programmer.contracts.escalation import (
    ProgrammerEscalation,
    ProgrammerEscalationCategory,
)
from core.programmer.contracts.execution import ProgrammerExecution
from core.programmer.contracts.identifiers import (
    PLAN_ID_PREFIX,
    PLAN_STEP_ID_PREFIX,
    new_codebase_understanding_id,
    new_execution_id,
    new_impact_analysis_id,
    new_plan_id,
    new_plan_step_id,
    new_work_order_id,
    validate_plan_id,
    validate_plan_step_id,
)
from core.programmer.contracts.impact_analysis import (
    ImpactAnalysis,
    ImpactItem,
)
from core.programmer.contracts.implementation_plan import (
    EscalationCandidate,
    ImplementationPlan,
    ImplementationPlanValidator,
    ImplementationStep,
)
from core.programmer.contracts.implementation_planner import ImplementationPlanner
from core.programmer.errors import (
    ProgrammerLineageError,
    ProgrammerValidationError,
)
from core.programmer.types import (
    AcceptanceCriterionType,
    ImpactLevel,
    ProgrammerBlockerSeverity,
    UnderstandingConfidence,
)


class TestProgrammerImplementationPlanning(unittest.TestCase):
    """
    Unit test suite for PROGRAMMER V1 — PHASE 7.3: Implementation Planning.
    Verifies advisory execution plans, deterministic ordering, authority boundaries,
    acceptance criteria coverage, and escalation candidates.
    """

    def setUp(self) -> None:
        self.project_id = "proj-p73-test"
        self.manager_task_id = "mtask-73"
        self.correlation_id = "corr-73"
        self.planner = ImplementationPlanner()

    def _create_work_order(
        self,
        objective: str = "Implement Stripe payment webhook",
        allowed_paths: Optional[list[str]] = None,
        writable_paths: Optional[list[str]] = None,
        forbidden_paths: Optional[list[str]] = None,
        acceptance_criteria: Optional[list[AcceptanceCriterion]] = None,
        required_checks: Optional[list[str]] = None,
    ) -> ProgrammerWorkOrder:
        from core.programmer.contracts.work_order import ProgrammerWorkOrder

        ac_list = acceptance_criteria or [
            AcceptanceCriterion(
                criterion_id="ac-webhook-200",
                description="Webhook returns HTTP 200 on valid signature",
                criterion_type=AcceptanceCriterionType.TEST_PASS,
                is_mandatory=True,
            )
        ]

        return ProgrammerWorkOrder(
            work_order_id=new_work_order_id(),
            manager_task_id=self.manager_task_id,
            project_id=self.project_id,
            correlation_id=self.correlation_id,
            objective=objective,
            allowed_paths=allowed_paths or ["."],
            writable_paths=writable_paths or ["src/webhooks/stripe.py", "tests/test_stripe.py"],
            forbidden_paths=forbidden_paths or [".secrets/"],
            allowed_commands=[AllowedCommand(command="pytest", description="run tests")],
            acceptance_criteria=ac_list,
            required_checks=required_checks or ["pytest tests/test_stripe.py"],
        )

    def _create_understanding(self, work_order: ProgrammerWorkOrder) -> CodebaseUnderstanding:
        return CodebaseUnderstanding(
            understanding_id=new_codebase_understanding_id(),
            execution_id=new_execution_id(),
            work_order_id=work_order.work_order_id,
            project_id=work_order.project_id,
            project_type="python_fastapi",
            project_type_confidence=UnderstandingConfidence.OBSERVED,
            languages=["python"],
            frameworks=["fastapi"],
            entry_points=["src/main.py"],
            test_locations=["tests/"],
            detected_conventions={"code_style": "pep8", "test_framework": "pytest"},
            uncertainties=[],
        )

    def _create_impact_analysis(
        self,
        work_order: ProgrammerWorkOrder,
        understanding: CodebaseUnderstanding,
        directly_affected: Optional[list[str]] = None,
        indirectly_affected: Optional[list[str]] = None,
        affected_modules: Optional[list[str]] = None,
        affected_tests: Optional[list[str]] = None,
        unknowns: Optional[list[str]] = None,
    ) -> ImpactAnalysis:
        return ImpactAnalysis(
            analysis_id=new_impact_analysis_id(),
            execution_id=understanding.execution_id,
            work_order_id=work_order.work_order_id,
            project_id=work_order.project_id,
            understanding_id=understanding.understanding_id,
            directly_affected_files=directly_affected if directly_affected is not None else ["src/webhooks/stripe.py"],
            indirectly_affected_files=indirectly_affected or [],
            affected_modules=affected_modules or ["webhooks"],
            affected_tests=affected_tests if affected_tests is not None else ["tests/test_stripe.py"],
            unknowns=unknowns or [],
        )

    # -------------------------------------------------------------------------
    # Test Cases
    # -------------------------------------------------------------------------

    def test_simple_implementation(self) -> None:
        """Verify planner synthesizes a valid, concise plan for single-component task."""
        wo = self._create_work_order()
        und = self._create_understanding(wo)
        imp = self._create_impact_analysis(wo, und)

        plan = self.planner.create_plan(wo, und, imp)

        self.assertTrue(plan.plan_id.startswith(PLAN_ID_PREFIX))
        validate_plan_id(plan.plan_id)
        self.assertEqual(plan.objective, wo.objective)
        self.assertEqual(plan.work_order_id, wo.work_order_id)
        self.assertGreaterEqual(len(plan.steps), 2)  # Implementation + Verification
        self.assertIn("src/webhooks/stripe.py", plan.affected_files)
        self.assertFalse(plan.has_escalations())

        # Check step details
        for step in plan.steps:
            validate_plan_step_id(step.step_id)
            self.assertTrue(step.description)
            self.assertTrue(step.verification)

        # Verify acceptance criteria covered
        self.assertIn("ac-webhook-200", plan.steps[0].acceptance_criteria_ids + plan.steps[-1].acceptance_criteria_ids)

    def test_multi_step_implementation(self) -> None:
        """Verify multi-component task generates ordered steps (model -> logic -> callers -> verify)."""
        wo = self._create_work_order(
            writable_paths=[
                "src/models/payment_model.py",
                "src/services/payment_service.py",
                "src/routes/api.py",
                "tests/test_payment.py",
            ]
        )
        und = self._create_understanding(wo)
        imp = self._create_impact_analysis(
            wo,
            und,
            directly_affected=["src/models/payment_model.py", "src/services/payment_service.py"],
            indirectly_affected=["src/routes/api.py"],
            affected_modules=["models", "services", "routes"],
            affected_tests=["tests/test_payment.py"],
        )

        plan = self.planner.create_plan(wo, und, imp)

        # Steps should include:
        # 1. Model/contract step
        # 2. Service logic step
        # 3. Caller / route update step
        # 4. Verification step
        self.assertEqual(len(plan.steps), 4)

        step_models = plan.steps[0]
        step_service = plan.steps[1]
        step_routes = plan.steps[2]
        step_verify = plan.steps[3]

        self.assertIn("src/models/payment_model.py", step_models.target_files)
        self.assertIn("src/services/payment_service.py", step_service.target_files)
        self.assertIn("src/routes/api.py", step_routes.target_files)

        # Check dependencies
        self.assertEqual(step_models.dependencies, [])
        self.assertIn(step_models.step_id, step_service.dependencies)
        self.assertIn(step_service.step_id, step_routes.dependencies)
        self.assertIn(step_routes.step_id, step_verify.dependencies)

    def test_dependency_ordering(self) -> None:
        """Verify topological sorting guarantees prerequisites precede dependents."""
        wo = self._create_work_order()
        und = self._create_understanding(wo)
        imp = self._create_impact_analysis(wo, und)

        plan = self.planner.create_plan(wo, und, imp)
        ordered_steps = plan.get_topological_order()

        self.assertEqual(len(ordered_steps), len(plan.steps))
        step_indices = {s.step_id: idx for idx, s in enumerate(ordered_steps)}

        for s in ordered_steps:
            for dep in s.dependencies:
                self.assertLess(step_indices[dep], step_indices[s.step_id])

    def test_cyclic_dependency_rejected(self) -> None:
        """Verify cyclic step dependencies (A -> B -> A) are detected and rejected."""
        wo = self._create_work_order()

        step_a = ImplementationStep(
            step_id="pstep-001",
            description="Step A",
            target_files=["src/webhooks/stripe.py"],
            dependencies=["pstep-002"],  # Depends on B
            verification="Check A",
        )
        step_b = ImplementationStep(
            step_id="pstep-002",
            description="Step B",
            target_files=["src/webhooks/stripe.py"],
            dependencies=["pstep-001"],  # Depends on A -> Cycle!
            verification="Check B",
        )

        with self.assertRaises(ProgrammerValidationError) as ctx:
            ImplementationPlan(
                plan_id=new_plan_id(),
                execution_id=new_execution_id(),
                work_order_id=wo.work_order_id,
                project_id=wo.project_id,
                objective=wo.objective,
                steps=[step_a, step_b],
            )
        self.assertIn("Cyclic dependency", str(ctx.exception))

    def test_invalid_target(self) -> None:
        """Verify step targeting forbidden or non-writable path without escalation candidate is rejected."""
        wo = self._create_work_order(
            writable_paths=["src/"],
            forbidden_paths=[".secrets/"],
        )

        # Create step targeting forbidden path
        step = ImplementationStep(
            step_id="pstep-001",
            description="Tamper secrets",
            target_files=[".secrets/keys.json"],
            dependencies=[],
            verification="Verify tamper",
            is_escalation_candidate=False,
        )

        plan = ImplementationPlan(
            plan_id=new_plan_id(),
            execution_id=new_execution_id(),
            work_order_id=wo.work_order_id,
            project_id=wo.project_id,
            objective=wo.objective,
            steps=[step],
        )

        with self.assertRaises(ProgrammerValidationError) as ctx:
            ImplementationPlanValidator.validate(plan, wo)
        self.assertIn("in forbidden paths", str(ctx.exception))

    def test_plan_exceeding_writable_scope(self) -> None:
        """Verify planner generates EscalationCandidate when target file exceeds writable scope."""
        wo = self._create_work_order(
            writable_paths=["src/allowed.py"],
            forbidden_paths=[".secrets/"],
        )
        und = self._create_understanding(wo)
        # Directly affected file is outside writable paths
        imp = self._create_impact_analysis(
            wo,
            und,
            directly_affected=["config/production.json"],  # Not in writable_paths
        )

        plan = self.planner.create_plan(wo, und, imp)

        self.assertTrue(plan.has_escalations())
        self.assertEqual(len(plan.escalation_points), 1)

        candidate = plan.escalation_points[0]
        self.assertEqual(candidate.category, ProgrammerEscalationCategory.SCOPE)
        self.assertEqual(candidate.target, "config/production.json")
        self.assertIn("outside authorized writable scope", candidate.reason)

        # The step targeting this file must be marked as an escalation candidate
        model_or_logic_step = plan.steps[0]
        self.assertTrue(model_or_logic_step.is_escalation_candidate)

    def test_missing_acceptance_coverage(self) -> None:
        """Verify validator catches when a mandatory acceptance criterion is omitted from the plan."""
        wo = self._create_work_order(
            acceptance_criteria=[
                AcceptanceCriterion(
                    criterion_id="ac-required-audit",
                    description="Audit logs must record transaction id",
                    is_mandatory=True,
                )
            ]
        )

        # Plan steps do not mention ac-required-audit
        step = ImplementationStep(
            step_id="pstep-001",
            description="Implement simple logic",
            target_files=["src/webhooks/stripe.py"],
            dependencies=[],
            verification="pytest test",
            acceptance_criteria_ids=["ac-unrelated"],
        )

        plan = ImplementationPlan(
            plan_id=new_plan_id(),
            execution_id=new_execution_id(),
            work_order_id=wo.work_order_id,
            project_id=wo.project_id,
            objective=wo.objective,
            steps=[step],
            required_checks=["pytest"],
        )

        with self.assertRaises(ProgrammerValidationError) as ctx:
            ImplementationPlanValidator.validate(plan, wo)
        self.assertIn("Mandatory acceptance criteria not covered", str(ctx.exception))
        self.assertIn("ac-required-audit", str(ctx.exception))

    def test_unknown_dependency(self) -> None:
        """Verify unknown or unresolvable dependencies in impact analysis produce an escalation candidate."""
        wo = self._create_work_order()
        und = self._create_understanding(wo)
        imp = self._create_impact_analysis(
            wo,
            und,
            unknowns=["stripe_internal_billing_api_v3"],
        )

        plan = self.planner.create_plan(wo, und, imp)

        self.assertTrue(plan.has_escalations())
        dep_escalations = [e for e in plan.escalation_points if e.category == ProgrammerEscalationCategory.DEPENDENCY]
        self.assertEqual(len(dep_escalations), 1)
        self.assertEqual(dep_escalations[0].target, "stripe_internal_billing_api_v3")

    def test_escalation_candidate(self) -> None:
        """Verify EscalationCandidate captures details and converts to ProgrammerEscalation faithfully."""
        candidate = EscalationCandidate(
            category=ProgrammerEscalationCategory.SCOPE,
            reason="Path /etc/nginx/nginx.conf is outside writable scope.",
            target="/etc/nginx/nginx.conf",
            requested_decision="Authorize modifying nginx config.",
            severity=ProgrammerBlockerSeverity.CRITICAL,
            suggested_options=["Grant write access", "Use local proxy"],
        )

        self.assertEqual(candidate.category, ProgrammerEscalationCategory.SCOPE)
        self.assertEqual(candidate.severity, ProgrammerBlockerSeverity.CRITICAL)

        # Test conversion to ProgrammerEscalation
        wo = self._create_work_order()
        exec_id = new_execution_id()
        execution = ProgrammerExecution(
            execution_id=exec_id,
            work_order_id=wo.work_order_id,
            project_id=wo.project_id,
            task_id=wo.manager_task_id,
            correlation_id=wo.correlation_id,
        )

        escalation = candidate.to_escalation(execution, wo)
        self.assertIsInstance(escalation, ProgrammerEscalation)
        self.assertEqual(escalation.execution_id, execution.execution_id)
        self.assertEqual(escalation.work_order_id, wo.work_order_id)
        self.assertEqual(escalation.category, ProgrammerEscalationCategory.SCOPE)
        self.assertEqual(escalation.severity, ProgrammerBlockerSeverity.CRITICAL)
        self.assertEqual(escalation.requested_decision, candidate.requested_decision)
        self.assertIn("/etc/nginx/nginx.conf", escalation.observed_facts[1])

    def test_deterministic_validation(self) -> None:
        """Verify deterministic validation catches lineage mismatch, duplicate step IDs, and unknown dependencies."""
        wo = self._create_work_order()
        und = self._create_understanding(wo)
        imp = self._create_impact_analysis(wo, und)

        # 1. Lineage mismatch
        other_wo = self._create_work_order(objective="Different task")
        plan = self.planner.create_plan(wo, und, imp)

        with self.assertRaises(ProgrammerLineageError):
            ImplementationPlanValidator.validate(plan, other_wo)

        # 2. Duplicate step IDs
        step1 = ImplementationStep(
            step_id="pstep-duplicate",
            description="Step 1",
            target_files=["src/webhooks/stripe.py"],
            verification="check",
        )
        step2 = ImplementationStep(
            step_id="pstep-duplicate",
            description="Step 2",
            target_files=["src/webhooks/stripe.py"],
            verification="check",
        )

        with self.assertRaises(ProgrammerValidationError) as ctx:
            ImplementationPlan(
                plan_id=new_plan_id(),
                execution_id=new_execution_id(),
                work_order_id=wo.work_order_id,
                project_id=wo.project_id,
                objective=wo.objective,
                steps=[step1, step2],
            )
        self.assertIn("Duplicate step_id", str(ctx.exception))

        # 3. Unknown prerequisite step ID
        step_orphan = ImplementationStep(
            step_id="pstep-001",
            description="Step 1",
            target_files=["src/webhooks/stripe.py"],
            dependencies=["pstep-nonexistent"],
            verification="check",
        )
        with self.assertRaises(ProgrammerValidationError) as ctx:
            ImplementationPlan(
                plan_id=new_plan_id(),
                execution_id=new_execution_id(),
                work_order_id=wo.work_order_id,
                project_id=wo.project_id,
                objective=wo.objective,
                steps=[step_orphan],
            )
        self.assertIn("references unknown dependency step", str(ctx.exception))

    def test_serialization_roundtrip(self) -> None:
        """Verify complete to_dict/from_dict and JSON serialization roundtrip."""
        wo = self._create_work_order()
        und = self._create_understanding(wo)
        imp = self._create_impact_analysis(wo, und, unknowns=["unresolved_lib"])

        plan = self.planner.create_plan(wo, und, imp)
        plan_dict = plan.to_dict()
        plan_json = plan.to_json()

        restored_from_dict = ImplementationPlan.from_dict(plan_dict)
        restored_from_json = ImplementationPlan.from_json(plan_json)

        self.assertEqual(restored_from_dict.plan_id, plan.plan_id)
        self.assertEqual(restored_from_dict.objective, plan.objective)
        self.assertEqual(len(restored_from_dict.steps), len(plan.steps))
        self.assertEqual(len(restored_from_dict.escalation_points), len(plan.escalation_points))

        self.assertEqual(restored_from_json.plan_id, plan.plan_id)
        self.assertEqual(restored_from_json.steps[0].step_id, plan.steps[0].step_id)
        self.assertEqual(restored_from_json.escalation_points[0].target, plan.escalation_points[0].target)


if __name__ == "__main__":
    unittest.main()
