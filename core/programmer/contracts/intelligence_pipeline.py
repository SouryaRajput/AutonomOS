from __future__ import annotations

from dataclasses import dataclass, field
import logging
from typing import Any, Callable, Optional, Sequence

from core.programmer.contracts.codebase_explorer import CodebaseExplorer
from core.programmer.contracts.codebase_understanding import CodebaseUnderstanding
from core.programmer.contracts.engineering_risk import (
    EngineeringRisk,
    RiskAssessment,
)
from core.programmer.contracts.escalation import EscalationCoordinator
from core.programmer.contracts.execution_context import ProgrammerExecutionContext
from core.programmer.contracts.execution_supervisor import (
    ExecutionSupervisionRecord,
    ExecutionSupervisor,
)
from core.programmer.contracts.impact_analysis import ImpactAnalysis
from core.programmer.contracts.impact_analyzer import ImpactAnalyzer
from core.programmer.contracts.implementation_plan import ImplementationPlan
from core.programmer.contracts.implementation_planner import ImplementationPlanner
from core.programmer.contracts.plan_validator import (
    PlanValidationResult,
    PlanValidator,
)
from core.programmer.contracts.risk_analyzer import EngineeringRiskAnalyzer
from core.programmer.contracts.work_order import ProgrammerWorkOrder
from core.programmer.contracts.workspace import ProgrammerWorkspace
from core.programmer.errors import (
    ProgrammerLineageError,
    ProgrammerValidationError,
)
from core.programmer.types import PlanValidationStatus

logger = logging.getLogger("AutonomOS.Programmer.IntelligencePipeline")


@dataclass
class PreImplementationIntelligence:
    """
    Structured outcome of the pre-implementation intelligence phase.
    Preserves all 5 intelligence artifacts generated prior to code modification.
    """
    understanding: CodebaseUnderstanding
    impact_analysis: ImpactAnalysis
    plan: ImplementationPlan
    risk_assessment: RiskAssessment
    plan_validation: PlanValidationResult

    @property
    def is_valid(self) -> bool:
        return self.plan_validation.valid

    @property
    def requires_escalation(self) -> bool:
        return self.plan_validation.status == PlanValidationStatus.REQUIRES_ESCALATION

    def to_dict(self) -> dict[str, Any]:
        return {
            "understanding": self.understanding.to_dict(),
            "impact_analysis": self.impact_analysis.to_dict(),
            "plan": self.plan.to_dict(),
            "risk_assessment": self.risk_assessment.to_dict(),
            "plan_validation": self.plan_validation.to_dict(),
            "is_valid": self.is_valid,
            "requires_escalation": self.requires_escalation,
        }


class EngineeringIntelligencePipeline:
    """
    Coordinates the execution of Phase 7 engineering intelligence components:
    1. CodebaseUnderstanding (Phase 7.1)
    2. ImpactAnalysis (Phase 7.2)
    3. ImplementationPlanning (Phase 7.3)
    4. EngineeringRiskAnalysis (Phase 7.4)
    5. PlanValidation & Manager Escalation (Phase 7.5)
    6. ExecutionSupervision (Phase 7.6)
    """

    def __init__(
        self,
        explorer: Optional[CodebaseExplorer] = None,
        impact_analyzer: Optional[ImpactAnalyzer] = None,
        planner: Optional[ImplementationPlanner] = None,
        risk_analyzer: Optional[EngineeringRiskAnalyzer] = None,
        plan_validator: Optional[PlanValidator] = None,
    ) -> None:
        self.explorer = explorer or CodebaseExplorer()
        self.impact_analyzer = impact_analyzer or ImpactAnalyzer()
        self.planner = planner or ImplementationPlanner()
        self.risk_analyzer = risk_analyzer or EngineeringRiskAnalyzer()
        self.plan_validator = plan_validator or PlanValidator()

    def run_pre_implementation(
        self,
        work_order: ProgrammerWorkOrder,
        workspace: ProgrammerWorkspace,
        execution_id: str,
        execution_context: Optional[ProgrammerExecutionContext] = None,
    ) -> PreImplementationIntelligence:
        """
        Execute the complete pre-implementation intelligence pipeline deterministically.
        """
        if work_order is None:
            raise ProgrammerValidationError("ProgrammerWorkOrder cannot be None.")
        work_order.validate()

        # Step 1: Codebase Understanding
        understanding = self.explorer.explore(
            workspace=workspace,
            work_order=work_order,
            execution_id=execution_id,
        )

        # Step 2: Impact Analysis
        impact = self.impact_analyzer.analyze(
            workspace=workspace,
            work_order=work_order,
            understanding=understanding,
        )

        # Step 3: Implementation Planning
        plan = self.planner.create_plan(
            work_order=work_order,
            understanding=understanding,
            impact_analysis=impact,
        )

        # Step 4: Engineering Risk Detection
        risk_assessment = self.risk_analyzer.analyze(
            work_order=work_order,
            understanding=understanding,
            impact_analysis=impact,
            plan=plan,
            workspace=workspace,
        )

        # Step 5: Plan Validation
        plan_validation = self.plan_validator.validate_plan(
            plan=plan,
            work_order=work_order,
            execution_context=execution_context,
            impact_analysis=impact,
            risk_assessment=risk_assessment,
            workspace=workspace,
        )

        return PreImplementationIntelligence(
            understanding=understanding,
            impact_analysis=impact,
            plan=plan,
            risk_assessment=risk_assessment,
            plan_validation=plan_validation,
        )

    def create_supervisor(
        self,
        plan: ImplementationPlan,
        work_order: ProgrammerWorkOrder,
        execution_context: Optional[ProgrammerExecutionContext] = None,
    ) -> ExecutionSupervisor:
        """Instantiate an ExecutionSupervisor for the validated plan."""
        return ExecutionSupervisor(
            plan=plan,
            work_order=work_order,
            execution_context=execution_context,
        )
