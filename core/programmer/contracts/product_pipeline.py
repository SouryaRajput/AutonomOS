from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import json
import logging
import os
from typing import Any, Callable, Optional, Sequence

from core.programmer.contracts.acceptance_result import AcceptanceCriterionResult
from core.programmer.contracts.blocker import ProgrammerBlocker
from core.programmer.contracts.build_packaging import (
    BuildPackagingRequest,
    BuildPackagingResult,
    BuildPackagingRunner,
)
from core.programmer.contracts.command_resolver import CommandBoundaryResolver
from core.programmer.contracts.deployment_handoff import (
    DeploymentHandoff,
    DeploymentHandoffBuilder,
)
from core.programmer.contracts.deployment_readiness import (
    DeploymentReadinessEvaluator,
    DeploymentReadinessResult,
)
from core.programmer.contracts.engineering_risk import EngineeringRisk
from core.programmer.contracts.environment_boundary import (
    EnvironmentRequirement,
    SecretExposureDetector,
)
from core.programmer.contracts.git_model import GitExecutionContext
from core.programmer.contracts.product_artifact import ProductArtifact
from core.programmer.contracts.runtime_configuration import RuntimeConfigurationSchema
from core.programmer.contracts.verification import (
    VerificationEvidence,
    VerificationSummary,
)
from core.programmer.contracts.work_order import ProgrammerWorkOrder
from core.programmer.errors import (
    ArtifactLineageError,
    ProgrammerValidationError,
    SecretExposureError,
)
from core.programmer.types import (
    BuildPackagingStatus,
    DeploymentHandoffStatus,
    DeploymentReadinessStatus,
    DeploymentRecommendation,
    ProductLifecycleState,
)

logger = logging.getLogger("AutonomOS.Programmer.ProductPipeline")


def utc_now() -> str:
    """Generate ISO 8601 UTC timestamp."""
    return datetime.now(timezone.utc).isoformat()


@dataclass
class ProductPipelineRequest:
    """
    Request specification for executing the end-to-end Phase 9 product packaging
    and deployment readiness pipeline.
    """
    work_order: ProgrammerWorkOrder
    git_context: GitExecutionContext
    build_request: Optional[BuildPackagingRequest] = None
    build_command_executor: Optional[Callable[[list[str], str, int], tuple[int, str, str]]] = None
    configuration_schema: Optional[RuntimeConfigurationSchema] = None
    environment_requirements: list[EnvironmentRequirement] = field(default_factory=list)
    verification_summary: Optional[VerificationSummary] = None
    acceptance_results: list[AcceptanceCriterionResult] = field(default_factory=list)
    blockers: list[ProgrammerBlocker] = field(default_factory=list)
    risks: list[EngineeringRisk] = field(default_factory=list)
    available_secret_refs: set[str] = field(default_factory=set)
    secret_resolver: Optional[Callable[[str], bool]] = None
    file_exists_fn: Optional[Callable[[str], bool]] = None
    trace: dict[str, Any] = field(default_factory=dict)


@dataclass
class ProductPipelineResult:
    """
    Structured outcome contract returned by ProductPackagingReadinessPipeline.
    Encapsulates build results, emitted product artifact, schemas, evaluated
    readiness, and the finalized DeploymentHandoff for Manager delivery.
    """
    success: bool
    build_result: BuildPackagingResult
    artifact: Optional[ProductArtifact] = None
    configuration_schema: Optional[RuntimeConfigurationSchema] = None
    environment_requirements: list[EnvironmentRequirement] = field(default_factory=list)
    readiness_result: Optional[DeploymentReadinessResult] = None
    handoff: Optional[DeploymentHandoff] = None
    evidence: list[VerificationEvidence] = field(default_factory=list)
    trace: dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=utc_now)

    def to_dict(self) -> dict[str, Any]:
        """Serialize ProductPipelineResult to a JSON-compatible dictionary."""
        return {
            "success": self.success,
            "build_result": self.build_result.to_dict() if self.build_result else None,
            "artifact": self.artifact.to_dict() if self.artifact else None,
            "configuration_schema": (
                self.configuration_schema.to_dict()
                if self.configuration_schema and hasattr(self.configuration_schema, "to_dict")
                else self.configuration_schema
            ),
            "environment_requirements": [
                r.to_dict() if hasattr(r, "to_dict") else r for r in self.environment_requirements
            ],
            "readiness_result": self.readiness_result.to_dict() if self.readiness_result else None,
            "handoff": self.handoff.to_dict() if self.handoff else None,
            "evidence": [e.to_dict() if hasattr(e, "to_dict") else e for e in self.evidence],
            "trace": dict(self.trace),
            "created_at": self.created_at,
        }

    def to_json(self, indent: int = 2) -> str:
        """Serialize ProductPipelineResult to JSON string."""
        return json.dumps(self.to_dict(), indent=indent, sort_keys=True)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ProductPipelineResult:
        """Deserialize from dictionary."""
        build_res = (
            BuildPackagingResult(**data["build_result"])
            if data.get("build_result")
            else BuildPackagingResult(success=False, status=BuildPackagingStatus.FAILED)
        )
        art = ProductArtifact.from_dict(data["artifact"]) if data.get("artifact") else None
        schema = (
            RuntimeConfigurationSchema.from_dict(data["configuration_schema"])
            if data.get("configuration_schema")
            else None
        )
        env_reqs = [
            EnvironmentRequirement.from_dict(r) if isinstance(r, dict) else r
            for r in data.get("environment_requirements", [])
        ]
        readiness = (
            DeploymentReadinessResult.from_dict(data["readiness_result"])
            if data.get("readiness_result")
            else None
        )
        handoff = (
            DeploymentHandoff.from_dict(data["handoff"])
            if data.get("handoff")
            else None
        )
        evs = [
            VerificationEvidence.from_dict(e) if isinstance(e, dict) else e
            for e in data.get("evidence", [])
        ]
        return cls(
            success=bool(data.get("success", False)),
            build_result=build_res,
            artifact=art,
            configuration_schema=schema,
            environment_requirements=env_reqs,
            readiness_result=readiness,
            handoff=handoff,
            evidence=evs,
            trace=dict(data.get("trace", {})),
            created_at=str(data.get("created_at", utc_now())),
        )


class ProductPackagingReadinessPipeline:
    """
    End-to-End Orchestration Pipeline for Phase 9: Product Packaging & Deployment Readiness.

    Architectural Flow:
        Manager
           ↓
        ProgrammerWorkOrder + Isolated Git Execution
           ↓
        Lineage & Secret Boundary Inspection
           ↓
        Build & Packaging (BuildPackagingRunner)
           ↓
        ProductArtifact (Immutable Source Revision Binding, Non-deployable)
           ↓
        RuntimeConfigurationSchema & EnvironmentRequirements Attachment
           ↓
        Deployment Readiness Evaluation (Deterministic Evaluator)
           ↓
        Deployment Handoff Construction (DeploymentHandoffBuilder)
           ↓
        Manager (Retains Sole Authority Over Actual Deployment)

    Core Invariants:
    1. Source revision is immutable, verified, and strictly traceable.
    2. Build artifact corresponds strictly to the verified revision in GitExecutionContext.
    3. Configuration is data, not executable code; cannot silently execute commands or alter policies.
    4. Secrets are never exposed in ordinary Programmer artifacts, logs, or evidence.
    5. Deployment readiness is distinct from deployment; Programmer V1 never deploys.
    6. Manager remains the sole authority for organizational product delivery.
    """

    def __init__(
        self,
        command_resolver: Optional[CommandBoundaryResolver] = None,
        readiness_evaluator: Optional[DeploymentReadinessEvaluator] = None,
    ) -> None:
        self.command_resolver = command_resolver or CommandBoundaryResolver()
        self.readiness_evaluator = readiness_evaluator or DeploymentReadinessEvaluator()

    def execute(self, request: ProductPipelineRequest) -> ProductPipelineResult:
        """
        Execute the complete product packaging, configuration boundary, readiness evaluation,
        and deployment handoff pipeline.
        """
        if request is None:
            raise ProgrammerValidationError("ProductPipelineRequest cannot be None.", field_name="request")

        work_order = request.work_order
        git_context = request.git_context

        if work_order is None:
            raise ProgrammerValidationError("ProgrammerWorkOrder is required.", field_name="work_order")
        if git_context is None:
            raise ProgrammerValidationError("GitExecutionContext is required.", field_name="git_context")

        # ---------------------------------------------------------------------
        # Step 1: Validate Lineage and Repositories
        # ---------------------------------------------------------------------
        work_order.validate()
        git_context.validate()

        if git_context.project_id and git_context.project_id != work_order.project_id:
            raise ArtifactLineageError(
                f"Project mismatch: git context '{git_context.project_id}' does not match work order '{work_order.project_id}'."
            )
        if git_context.work_order_id != work_order.work_order_id:
            raise ArtifactLineageError(
                f"Work order mismatch: git context '{git_context.work_order_id}' does not match work order '{work_order.work_order_id}'."
            )

        # ---------------------------------------------------------------------
        # Step 2: Enforce Secret Exposure Boundary on Input WorkOrder & Metadata
        # ---------------------------------------------------------------------
        SecretExposureDetector.assert_no_secret_exposure(
            work_order.objective,
            location=f"work_order.{work_order.work_order_id}.objective",
        )
        if work_order.context:
            for k, v in work_order.context.items():
                if isinstance(v, str):
                    SecretExposureDetector.assert_no_secret_exposure(
                        v, location=f"work_order.{work_order.work_order_id}.context.{k}"
                    )

        # ---------------------------------------------------------------------
        # Step 3: Execute Build & Packaging
        # ---------------------------------------------------------------------
        build_runner = BuildPackagingRunner(
            work_order=work_order,
            git_context=git_context,
            command_executor=request.build_command_executor,
        )

        build_result = build_runner.execute(
            request=request.build_request,
            raise_on_error=False,
        )

        artifact: Optional[ProductArtifact] = build_result.artifact

        # If build was successful, enrich artifact with schemas and environment requirements
        if artifact is not None:
            if request.configuration_schema is not None and not artifact.configuration_schema:
                artifact.configuration_schema = request.configuration_schema.to_dict()
            if request.environment_requirements and not artifact.environment_requirements:
                artifact.environment_requirements = {
                    req.name: req.type.value if hasattr(req.type, "value") else str(req.type)
                    for req in request.environment_requirements
                }

        # ---------------------------------------------------------------------
        # Step 4: Evaluate Deployment Readiness
        # ---------------------------------------------------------------------
        readiness_result = self.readiness_evaluator.evaluate(
            artifact=artifact,
            build_result=build_result,
            verification_summary=request.verification_summary,
            acceptance_results=request.acceptance_results,
            blockers=request.blockers,
            risks=request.risks,
            configuration_schema=request.configuration_schema,
            environment_requirements=request.environment_requirements,
            available_secret_refs=request.available_secret_refs,
            secret_resolver=request.secret_resolver,
            file_exists_fn=request.file_exists_fn,
            trace={
                "pipeline": "ProductPackagingReadinessPipeline",
                "work_order_id": work_order.work_order_id,
                "project_id": work_order.project_id,
            },
        )

        # ---------------------------------------------------------------------
        # Step 5: Construct Deployment Handoff
        # ---------------------------------------------------------------------
        handoff_builder = DeploymentHandoffBuilder()
        handoff_builder.set_project_id(work_order.project_id)
        handoff_builder.set_work_order_id(work_order.work_order_id)
        handoff_builder.set_execution_id(git_context.execution_id)
        handoff_builder.set_source_revision(git_context.resulting_revision or git_context.base_revision)
        handoff_builder.set_build_result(build_result)
        handoff_builder.set_artifact(artifact)

        if request.configuration_schema is not None:
            handoff_builder.set_configuration_schema(request.configuration_schema)
        if request.environment_requirements:
            handoff_builder.set_environment_requirements(request.environment_requirements)
        if request.verification_summary is not None:
            handoff_builder.set_verification_summary(request.verification_summary)
        if request.acceptance_results:
            handoff_builder.set_acceptance_results(request.acceptance_results)
        if request.blockers:
            handoff_builder.set_blockers(request.blockers)
        if request.risks:
            handoff_builder.set_risks(request.risks)

        handoff_builder.set_readiness_result(readiness_result)

        # Aggregate evidence from build and caller
        all_evidence: list[VerificationEvidence] = list(build_result.evidence)
        handoff_builder.set_evidence(all_evidence)

        handoff_builder.set_trace({
            "pipeline": "ProductPackagingReadinessPipeline",
            "work_order_id": work_order.work_order_id,
            "project_id": work_order.project_id,
            "execution_id": git_context.execution_id,
            "evaluated_at": utc_now(),
        })

        handoff = handoff_builder.build()

        # ---------------------------------------------------------------------
        # Step 6: Assemble ProductPipelineResult
        # ---------------------------------------------------------------------
        pipeline_success = build_result.success and readiness_result.is_ready

        return ProductPipelineResult(
            success=pipeline_success,
            build_result=build_result,
            artifact=artifact,
            configuration_schema=request.configuration_schema,
            environment_requirements=list(request.environment_requirements),
            readiness_result=readiness_result,
            handoff=handoff,
            evidence=list(handoff.evidence),
            trace={
                "pipeline": "ProductPackagingReadinessPipeline",
                "pipeline_success": pipeline_success,
                "handoff_id": handoff.handoff_id,
                "readiness_status": readiness_result.status.value,
                "handoff_status": handoff.status.value,
                "recommendation": handoff.recommendation.value,
                "lifecycle_state": handoff.lifecycle_state.value,
            },
            created_at=utc_now(),
        )
