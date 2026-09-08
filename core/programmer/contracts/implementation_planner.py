from __future__ import annotations

from typing import Any, Optional, Sequence

from core.programmer.contracts.codebase_understanding import CodebaseUnderstanding
from core.programmer.contracts.escalation import (
    ProgrammerEscalationCategory,
)
from core.programmer.contracts.filesystem_resolver import (
    FilesystemBoundaryResolver,
    is_subpath_or_equal,
)
from core.programmer.contracts.identifiers import (
    new_escalation_candidate_id,
    new_plan_id,
    new_plan_step_id,
)
from core.programmer.contracts.impact_analysis import ImpactAnalysis
from core.programmer.contracts.implementation_plan import (
    EscalationCandidate,
    ImplementationPlan,
    ImplementationPlanValidator,
    ImplementationStep,
    utc_now,
)
from core.programmer.contracts.work_order import ProgrammerWorkOrder
from core.programmer.errors import (
    ProgrammerLineageError,
    ProgrammerValidationError,
)
from core.programmer.types import ProgrammerBlockerSeverity


class ImplementationPlanner:
    """
    Deterministic planning engine that synthesizes an advisory ImplementationPlan
    from a validated ProgrammerWorkOrder, CodebaseUnderstanding, and ImpactAnalysis.

    Core Invariants:
    1. Advisory Guidance Only: Never executes code, modifies WorkOrder objectives,
       alters acceptance criteria, or expands filesystem/command authority.
    2. Authority Bounded: If an implementation target is out-of-scope or forbidden,
       or an unresolvable dependency exists, an EscalationCandidate is created.
    3. Deterministic Ordering: Steps are topologically ordered with explicit prerequisites,
       guaranteeing that dependencies precede dependent steps.
    4. Comprehensive Verification: Every step has explicit verification, and all
       mandatory acceptance criteria are mapped to steps or required checks.
    """

    def __init__(self, fs_resolver: Optional[FilesystemBoundaryResolver] = None) -> None:
        self.fs_resolver = fs_resolver or FilesystemBoundaryResolver()

    def create_plan(
        self,
        work_order: ProgrammerWorkOrder,
        understanding: CodebaseUnderstanding,
        impact_analysis: ImpactAnalysis,
        execution_id: Optional[str] = None,
        custom_steps: Optional[list[ImplementationStep]] = None,
        trace: Optional[dict[str, Any]] = None,
    ) -> ImplementationPlan:
        """
        Synthesize, validate, and return an ImplementationPlan.
        """
        # Lineage validation
        if impact_analysis.work_order_id != work_order.work_order_id:
            raise ProgrammerLineageError(
                f"ImpactAnalysis work_order_id '{impact_analysis.work_order_id}' does not match "
                f"WorkOrder '{work_order.work_order_id}'."
            )
        if understanding.work_order_id != work_order.work_order_id:
            raise ProgrammerLineageError(
                f"CodebaseUnderstanding work_order_id '{understanding.work_order_id}' does not match "
                f"WorkOrder '{work_order.work_order_id}'."
            )
        if impact_analysis.project_id != work_order.project_id:
            raise ProgrammerLineageError(
                f"ImpactAnalysis project_id '{impact_analysis.project_id}' does not match "
                f"WorkOrder '{work_order.project_id}'."
            )

        resolved_execution_id = execution_id or impact_analysis.execution_id or understanding.execution_id
        plan_id = new_plan_id()

        # 1. Detect Escalation Candidates (Scope & Dependencies)
        escalation_candidates: list[EscalationCandidate] = []
        out_of_scope_targets: set[str] = set()

        # Check all directly and indirectly affected files against scope authority
        candidate_files = list(impact_analysis.directly_affected_files)
        if not candidate_files:
            candidate_files = [f for f in work_order.writable_paths if f not in (".", "*", "")]

        for file_path in candidate_files:
            is_forbidden = any(is_subpath_or_equal(file_path, f) for f in work_order.forbidden_paths)
            is_writable = any(is_subpath_or_equal(file_path, w) for w in work_order.writable_paths)
            if is_forbidden or not is_writable:
                out_of_scope_targets.add(file_path)
                esc_candidate = EscalationCandidate(
                    candidate_id=new_escalation_candidate_id(),
                    category=ProgrammerEscalationCategory.SCOPE,
                    reason=f"Target file '{file_path}' is outside authorized writable scope or forbidden.",
                    target=file_path,
                    requested_decision=f"Authorize modification permissions for '{file_path}' or reassign scope.",
                    severity=ProgrammerBlockerSeverity.HIGH,
                    suggested_options=[
                        f"Add '{file_path}' to work_order.writable_paths",
                        "Refactor implementation to avoid modifying this file",
                        "Cancel work order",
                    ],
                    trace={
                        "work_order_id": work_order.work_order_id,
                        "plan_id": plan_id,
                        "file_path": file_path,
                    },
                )
                escalation_candidates.append(esc_candidate)

        # Check unknowns in impact analysis
        for unknown in impact_analysis.unknowns:
            esc_candidate = EscalationCandidate(
                candidate_id=new_escalation_candidate_id(),
                category=ProgrammerEscalationCategory.DEPENDENCY,
                reason=f"Unknown or unresolvable dependency impact: '{unknown}'.",
                target=unknown,
                requested_decision=f"Provide clarification or access for '{unknown}'.",
                severity=ProgrammerBlockerSeverity.MEDIUM,
                suggested_options=[
                    f"Provide context or access for '{unknown}'",
                    "Proceed assuming default behavior",
                ],
                trace={
                    "work_order_id": work_order.work_order_id,
                    "plan_id": plan_id,
                    "unknown": unknown,
                },
            )
            escalation_candidates.append(esc_candidate)

        # 2. Build or Use Steps
        if custom_steps is not None:
            steps = list(custom_steps)
        else:
            steps = self._synthesize_steps(
                work_order=work_order,
                understanding=understanding,
                impact_analysis=impact_analysis,
                out_of_scope_targets=out_of_scope_targets,
            )

        # 3. Assemble Required Checks, Risks, Assumptions
        required_checks = list(work_order.required_checks)
        for test_file in impact_analysis.affected_tests:
            check_str = f"pytest {test_file}"
            if check_str not in required_checks:
                required_checks.append(check_str)
        if not required_checks:
            required_checks.append("Verify implementation and run relevant test suites")

        risks = list(impact_analysis.potential_side_effects)
        if work_order.risk_level in ("HIGH", "CRITICAL"):
            risks.append(f"Work order carries elevated risk level: {work_order.risk_level}")

        assumptions: list[str] = []
        if understanding.detected_conventions:
            for k, v in understanding.detected_conventions.items():
                assumptions.append(f"Convention {k}: {v}")
        if not assumptions:
            assumptions.append("Standard repository patterns and conventions apply.")

        unknowns = list(impact_analysis.unknowns) + [
            u for u in understanding.uncertainties if u not in impact_analysis.unknowns
        ]

        expected_changes = [f"Modify {f}" for f in impact_analysis.directly_affected_files]
        if not expected_changes:
            expected_changes = [f"Implement objective: {work_order.objective}"]

        plan_trace = dict(trace or {})
        plan_trace.update({
            "plan_id": plan_id,
            "work_order_id": work_order.work_order_id,
            "execution_id": resolved_execution_id,
            "project_id": work_order.project_id,
            "understanding_id": understanding.understanding_id,
            "analysis_id": impact_analysis.analysis_id,
            "created_at": utc_now(),
        })

        plan = ImplementationPlan(
            plan_id=plan_id,
            execution_id=resolved_execution_id,
            work_order_id=work_order.work_order_id,
            project_id=work_order.project_id,
            objective=work_order.objective,
            steps=steps,
            affected_files=list(impact_analysis.directly_affected_files + impact_analysis.indirectly_affected_files),
            affected_modules=list(impact_analysis.affected_modules),
            dependencies=list(work_order.dependencies + impact_analysis.dependency_impacts),
            required_checks=required_checks,
            expected_changes=expected_changes,
            risks=risks,
            assumptions=assumptions,
            unknowns=unknowns,
            escalation_points=escalation_candidates,
            trace=plan_trace,
        )

        # 4. Deterministic Validation
        ImplementationPlanValidator.validate(plan, work_order)

        return plan

    def _synthesize_steps(
        self,
        work_order: ProgrammerWorkOrder,
        understanding: CodebaseUnderstanding,
        impact_analysis: ImpactAnalysis,
        out_of_scope_targets: set[str],
    ) -> list[ImplementationStep]:
        """
        Synthesize ordered execution steps from analysis facts.
        Guarantees:
        - Setup/model before service logic
        - Service logic before consumer/route updates
        - Implementation before verification
        - Prerequisite step dependencies correctly tracked
        - Acceptance criteria mapped to steps
        """
        steps: list[ImplementationStep] = []
        step_counter = 1

        direct_files = list(impact_analysis.directly_affected_files)
        if not direct_files:
            direct_files = [p for p in work_order.writable_paths if p not in (".", "*", "")]
        if not direct_files:
            direct_files = ["src/main.py"]

        all_ac_ids = [ac.criterion_id for ac in work_order.acceptance_criteria]

        if getattr(work_order, "iteration_budget", None) == 1:
            test_files = list(impact_analysis.affected_tests)
            verification_desc = "Run unit and integration test suites to verify acceptance criteria."
            if test_files:
                verification_desc = f"Run tests in {', '.join(test_files)} and verify acceptance criteria."
            target_all = list(dict.fromkeys(direct_files + test_files))
            return [
                ImplementationStep(
                    step_id="pstep-001",
                    description=f"Implement and verify '{work_order.objective}' in {', '.join(target_all)}",
                    target_files=target_all,
                    target_modules=list(impact_analysis.affected_modules),
                    rationale="Execute atomic implementation and verification within authorized single-iteration budget.",
                    dependencies=[],
                    expected_result=f"Functionality for '{work_order.objective}' is implemented and verified.",
                    verification=verification_desc,
                    acceptance_criteria_ids=list(all_ac_ids),
                    action_type="modify",
                    is_escalation_candidate=any(f in out_of_scope_targets for f in target_all),
                )
            ]

        # Categorize direct files into: models/schemas (prerequisites) and services/endpoints (dependents)
        model_files = [f for f in direct_files if any(sub in f.lower() for sub in ("model", "schema", "type", "contract", "interface"))]
        logic_files = [f for f in direct_files if f not in model_files]

        model_step_ids: list[str] = []
        if model_files:
            step_id = f"pstep-{step_counter:03d}"
            step_counter += 1
            model_step_ids.append(step_id)
            is_esc = any(f in out_of_scope_targets for f in model_files)
            steps.append(
                ImplementationStep(
                    step_id=step_id,
                    description=f"Define or update data models and contracts in {', '.join(model_files)}",
                    target_files=model_files,
                    target_modules=[m for m in impact_analysis.affected_modules if "model" in m.lower()],
                    rationale="Prerequisite data structures and contracts must be established before business logic.",
                    dependencies=[],
                    expected_result="Data structures and interfaces are defined cleanly.",
                    verification="Validate model imports and run contract sanity checks.",
                    acceptance_criteria_ids=[],
                    is_escalation_candidate=is_esc,
                )
            )

        # Logic implementation steps
        logic_step_ids: list[str] = []
        target_logic = logic_files if logic_files else direct_files
        if target_logic:
            step_id = f"pstep-{step_counter:03d}"
            step_counter += 1
            logic_step_ids.append(step_id)
            is_esc = any(f in out_of_scope_targets for f in target_logic)
            steps.append(
                ImplementationStep(
                    step_id=step_id,
                    description=f"Implement core logic for '{work_order.objective}' in {', '.join(target_logic)}",
                    target_files=target_logic,
                    target_modules=list(impact_analysis.affected_modules),
                    rationale=f"Fulfills core technical requirements of the work order objective.",
                    dependencies=list(model_step_ids),
                    expected_result=f"Core functionality for '{work_order.objective}' is implemented.",
                    verification="Verify implementation syntax, imports, and functional logic.",
                    acceptance_criteria_ids=list(all_ac_ids),
                    is_escalation_candidate=is_esc,
                )
            )

        # Secondary / Indirect update step (e.g. routes, consumers, configuration)
        indirect_step_ids: list[str] = []
        if impact_analysis.indirectly_affected_files:
            step_id = f"pstep-{step_counter:03d}"
            step_counter += 1
            indirect_step_ids.append(step_id)
            all_ro = all(any(is_subpath_or_equal(f, r) for r in work_order.read_only_paths) for f in impact_analysis.indirectly_affected_files)
            action = "read" if all_ro else "modify"
            steps.append(
                ImplementationStep(
                    step_id=step_id,
                    description=f"{'Inspect' if all_ro else 'Update'} affected callers and routes in {', '.join(impact_analysis.indirectly_affected_files)}",
                    target_files=list(impact_analysis.indirectly_affected_files),
                    target_modules=list(impact_analysis.affected_modules),
                    rationale="Keep downstream consumers and interfaces synchronized with core changes." if not all_ro else "Inspect downstream consumers and interfaces for compatibility.",
                    dependencies=list(logic_step_ids or model_step_ids),
                    expected_result="Callers and interfaces integrate properly with updated functionality.",
                    verification="Verify caller integration and end-to-end references.",
                    acceptance_criteria_ids=[],
                    action_type=action,
                    is_escalation_candidate=False,
                )
            )

        # Verification step
        verify_step_id = f"pstep-{step_counter:03d}"
        prereq_step_ids = (indirect_step_ids or logic_step_ids or model_step_ids)
        test_files = list(impact_analysis.affected_tests)
        verification_desc = "Run unit and integration test suites to verify acceptance criteria."
        if test_files:
            verification_desc = f"Run tests in {', '.join(test_files)} and verify acceptance criteria."

        steps.append(
            ImplementationStep(
                step_id=verify_step_id,
                description=f"Verify implementation and validate acceptance criteria: {', '.join(all_ac_ids) if all_ac_ids else 'all criteria'}",
                target_files=test_files,
                target_modules=list(impact_analysis.affected_modules),
                rationale="Confirm that changes satisfy all WorkOrder acceptance criteria without regression.",
                dependencies=list(prereq_step_ids),
                expected_result="All tests pass and acceptance criteria are fully satisfied.",
                verification=verification_desc,
                acceptance_criteria_ids=list(all_ac_ids),
                action_type="verify",
                is_escalation_candidate=False,
            )
        )

        return steps
