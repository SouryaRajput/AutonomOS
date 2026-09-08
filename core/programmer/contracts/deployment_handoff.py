from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import json
from typing import Any, Optional, Sequence

from core.programmer.contracts.acceptance_result import AcceptanceCriterionResult
from core.programmer.contracts.blocker import ProgrammerBlocker
from core.programmer.contracts.build_packaging import BuildPackagingResult
from core.programmer.contracts.deployment_readiness import (
    DeploymentReadinessEvaluator,
    DeploymentReadinessResult,
)
from core.programmer.contracts.engineering_risk import EngineeringRisk, RiskAssessment
from core.programmer.contracts.environment_boundary import EnvironmentRequirement
from core.programmer.contracts.git_model import GitRevision
from core.programmer.contracts.identifiers import (
    DEPLOYMENT_HANDOFF_ID_PREFIX,
    new_deployment_handoff_id,
    validate_deployment_handoff_id,
    validate_execution_id,
    validate_work_order_id,
)
from core.programmer.contracts.product_artifact import ProductArtifact
from core.programmer.contracts.runtime_configuration import RuntimeConfigurationSchema
from core.programmer.contracts.verification import (
    VerificationEvidence,
    VerificationSummary,
)
from core.programmer.errors import (
    ArtifactLineageError,
    DeploymentHandoffError,
    PrematureDeploymentClaimError,
    ProgrammerValidationError,
    SourceRevisionMismatchError,
)
from core.programmer.types import (
    BuildPackagingStatus,
    DeploymentHandoffStatus,
    DeploymentReadinessStatus,
    DeploymentRecommendation,
    ProductLifecycleState,
    VerificationStatus,
    VerificationSummaryStatus,
)


def utc_now() -> str:
    """Generate ISO 8601 UTC timestamp."""
    return datetime.now(timezone.utc).isoformat()


@dataclass
class DeploymentHandoff:
    """
    Structured domain contract representing an authoritative engineering handoff from
    Programmer to the future deployment/operations layer under Manager authority.

    Contains complete lineage, artifact reference, source revision, build information,
    runtime/configuration requirements, verification evidence, risks, and blockers.

    Safety Boundary:
    - Never claims deployment has occurred (lifecycle_state CANNOT be DEPLOYED).
    - Programmer may advise recommendations (DEPLOY, FIX, INVESTIGATE, CONFIGURE),
      but Manager remains the sole authority deciding whether deployment should occur.
    - Programmer never directly triggers deployment, modifies DNS, alters production DBs,
      or manages production credentials.
    """
    handoff_id: str = field(default_factory=new_deployment_handoff_id)
    project_id: str = ""
    work_order_id: str = ""
    execution_id: str = ""
    product_artifact: Optional[ProductArtifact] = None
    source_revision: Optional[GitRevision] = None
    build_information: Optional[BuildPackagingResult | dict[str, Any]] = None
    runtime_requirements: dict[str, Any] = field(default_factory=dict)
    configuration_schema: Optional[RuntimeConfigurationSchema | dict[str, Any]] = None
    environment_requirements: list[EnvironmentRequirement | dict[str, Any]] = field(default_factory=list)
    verification_summary: Optional[VerificationSummary | dict[str, Any]] = None
    acceptance_results: list[AcceptanceCriterionResult | dict[str, Any]] = field(default_factory=list)
    known_risks: list[EngineeringRisk | dict[str, Any]] = field(default_factory=list)
    blockers: list[ProgrammerBlocker | dict[str, Any]] = field(default_factory=list)
    deployment_prerequisites: list[str] = field(default_factory=list)
    evidence: list[VerificationEvidence | dict[str, Any]] = field(default_factory=list)
    status: DeploymentHandoffStatus = DeploymentHandoffStatus.NOT_READY
    lifecycle_state: ProductLifecycleState = ProductLifecycleState.BUILD_COMPLETE
    recommendation: DeploymentRecommendation = DeploymentRecommendation.INVESTIGATE
    trace: dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=utc_now)

    def __post_init__(self) -> None:
        # Validate handoff_id format
        if not self.handoff_id:
            self.handoff_id = new_deployment_handoff_id()
        else:
            validate_deployment_handoff_id(self.handoff_id)

        # Normalize enum types
        if isinstance(self.status, str):
            try:
                self.status = DeploymentHandoffStatus(self.status.upper())
            except (ValueError, KeyError):
                self.status = DeploymentHandoffStatus.NOT_READY

        if isinstance(self.lifecycle_state, str):
            try:
                self.lifecycle_state = ProductLifecycleState(self.lifecycle_state.upper())
            except (ValueError, KeyError):
                self.lifecycle_state = ProductLifecycleState.BUILD_COMPLETE

        if isinstance(self.recommendation, str):
            try:
                self.recommendation = DeploymentRecommendation(self.recommendation.upper())
            except (ValueError, KeyError):
                self.recommendation = DeploymentRecommendation.INVESTIGATE

        # ---------------------------------------------------------------------
        # Critical Safety Invariant: Never claim deployment has occurred
        # ---------------------------------------------------------------------
        if self.lifecycle_state == ProductLifecycleState.DEPLOYED:
            raise PrematureDeploymentClaimError(
                "Programmer cannot claim artifact is DEPLOYED; deployment authority rests exclusively with future deployment/operations layer under Manager authority.",
                handoff_id=self.handoff_id,
            )

        # Ensure collections are mutable lists/dicts
        self.runtime_requirements = dict(self.runtime_requirements or {})
        self.environment_requirements = list(self.environment_requirements or [])
        self.acceptance_results = list(self.acceptance_results or [])
        self.known_risks = list(self.known_risks or [])
        self.blockers = list(self.blockers or [])
        self.deployment_prerequisites = [str(p) for p in (self.deployment_prerequisites or [])]
        self.evidence = list(self.evidence or [])
        self.trace = dict(self.trace or {})

        # Populate project_id/work_order_id/execution_id from product_artifact if missing
        if self.product_artifact is not None:
            if not self.project_id:
                self.project_id = self.product_artifact.project_id
            if not self.work_order_id:
                self.work_order_id = self.product_artifact.work_order_id
            if not self.execution_id:
                self.execution_id = self.product_artifact.execution_id

            # Verify Lineage Consistency
            if self.project_id and self.product_artifact.project_id != self.project_id:
                raise ArtifactLineageError(
                    f"DeploymentHandoff project_id '{self.project_id}' does not match product_artifact project_id '{self.product_artifact.project_id}'.",
                    details={"artifact_id": self.product_artifact.artifact_id},
                )
            if self.work_order_id and self.product_artifact.work_order_id != self.work_order_id:
                raise ArtifactLineageError(
                    f"DeploymentHandoff work_order_id '{self.work_order_id}' does not match product_artifact work_order_id '{self.product_artifact.work_order_id}'.",
                    details={"artifact_id": self.product_artifact.artifact_id},
                )
            if self.execution_id and self.product_artifact.execution_id != self.execution_id:
                raise ArtifactLineageError(
                    f"DeploymentHandoff execution_id '{self.execution_id}' does not match product_artifact execution_id '{self.product_artifact.execution_id}'.",
                    details={"artifact_id": self.product_artifact.artifact_id},
                )

        # Verify source revision consistency if both artifact and handoff specify it
        if self.source_revision is not None and self.product_artifact is not None and self.product_artifact.source_revision is not None:
            handoff_hash = getattr(self.source_revision, "commit_hash", "") or (self.source_revision.get("commit_hash") if isinstance(self.source_revision, dict) else "")
            art_hash = getattr(self.product_artifact.source_revision, "commit_hash", "") or (self.product_artifact.source_revision.get("commit_hash") if isinstance(self.product_artifact.source_revision, dict) else "")
            if handoff_hash and art_hash and handoff_hash != art_hash:
                raise SourceRevisionMismatchError(
                    f"DeploymentHandoff source_revision commit hash '{handoff_hash}' does not match ProductArtifact revision '{art_hash}'.",
                    commit_hash=handoff_hash,
                    expected_hash=art_hash,
                )

        # Safety Check: Cannot recommend DEPLOY if status is NOT_READY or BLOCKED
        if self.status in (DeploymentHandoffStatus.NOT_READY, DeploymentHandoffStatus.BLOCKED):
            if self.recommendation == DeploymentRecommendation.DEPLOY:
                raise DeploymentHandoffError(
                    f"Invalid handoff recommendation: Programmer cannot recommend DEPLOY when handoff status is '{self.status.value}'.",
                    handoff_id=self.handoff_id,
                    field_name="recommendation",
                )

    @property
    def is_ready(self) -> bool:
        """Return True if handoff is cleared for deployment review."""
        return self.status in (DeploymentHandoffStatus.READY, DeploymentHandoffStatus.READY_WITH_WARNINGS)

    @property
    def is_blocked(self) -> bool:
        """Return True if handoff is impeded by unresolved blockers."""
        return self.status == DeploymentHandoffStatus.BLOCKED

    def to_dict(self) -> dict[str, Any]:
        """Serialize DeploymentHandoff to a pure JSON-compatible dictionary."""
        return {
            "handoff_id": self.handoff_id,
            "project_id": self.project_id,
            "work_order_id": self.work_order_id,
            "execution_id": self.execution_id,
            "product_artifact": self.product_artifact.to_dict() if hasattr(self.product_artifact, "to_dict") else self.product_artifact,
            "source_revision": self.source_revision.to_dict() if hasattr(self.source_revision, "to_dict") else self.source_revision,
            "build_information": self.build_information.to_dict() if hasattr(self.build_information, "to_dict") else self.build_information,
            "runtime_requirements": dict(self.runtime_requirements),
            "configuration_schema": self.configuration_schema.to_dict() if hasattr(self.configuration_schema, "to_dict") else self.configuration_schema,
            "environment_requirements": [
                req.to_dict() if hasattr(req, "to_dict") else req for req in self.environment_requirements
            ],
            "verification_summary": self.verification_summary.to_dict() if hasattr(self.verification_summary, "to_dict") else self.verification_summary,
            "acceptance_results": [
                res.to_dict() if hasattr(res, "to_dict") else res for res in self.acceptance_results
            ],
            "known_risks": [
                risk.to_dict() if hasattr(risk, "to_dict") else risk for risk in self.known_risks
            ],
            "blockers": [
                b.to_dict() if hasattr(b, "to_dict") else b for b in self.blockers
            ],
            "deployment_prerequisites": list(self.deployment_prerequisites),
            "evidence": [
                e.to_dict() if hasattr(e, "to_dict") else e for e in self.evidence
            ],
            "status": self.status.value,
            "lifecycle_state": self.lifecycle_state.value,
            "recommendation": self.recommendation.value,
            "trace": dict(self.trace),
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> DeploymentHandoff:
        """Deserialize DeploymentHandoff from dictionary."""
        art_data = data.get("product_artifact")
        artifact = ProductArtifact.from_dict(art_data) if isinstance(art_data, dict) else art_data

        rev_data = data.get("source_revision")
        source_rev = GitRevision.from_dict(rev_data) if isinstance(rev_data, dict) and hasattr(GitRevision, "from_dict") else rev_data

        schema_data = data.get("configuration_schema")
        schema = RuntimeConfigurationSchema.from_dict(schema_data) if isinstance(schema_data, dict) else schema_data

        env_reqs = []
        for r in data.get("environment_requirements", []):
            if isinstance(r, dict):
                try:
                    env_reqs.append(EnvironmentRequirement.from_dict(r))
                except Exception:
                    env_reqs.append(r)
            else:
                env_reqs.append(r)

        evidence_list = []
        for e in data.get("evidence", []):
            if isinstance(e, dict) and hasattr(VerificationEvidence, "from_dict"):
                try:
                    evidence_list.append(VerificationEvidence.from_dict(e))
                except Exception:
                    evidence_list.append(e)
            else:
                evidence_list.append(e)

        return cls(
            handoff_id=str(data.get("handoff_id", "")),
            project_id=str(data.get("project_id", "")),
            work_order_id=str(data.get("work_order_id", "")),
            execution_id=str(data.get("execution_id", "")),
            product_artifact=artifact,
            source_revision=source_rev,
            build_information=data.get("build_information"),
            runtime_requirements=dict(data.get("runtime_requirements", {})),
            configuration_schema=schema,
            environment_requirements=env_reqs,
            verification_summary=data.get("verification_summary"),
            acceptance_results=list(data.get("acceptance_results", [])),
            known_risks=list(data.get("known_risks", [])),
            blockers=list(data.get("blockers", [])),
            deployment_prerequisites=list(data.get("deployment_prerequisites", [])),
            evidence=evidence_list,
            status=DeploymentHandoffStatus(data.get("status", DeploymentHandoffStatus.NOT_READY.value)),
            lifecycle_state=ProductLifecycleState(data.get("lifecycle_state", ProductLifecycleState.BUILD_COMPLETE.value)),
            recommendation=DeploymentRecommendation(data.get("recommendation", DeploymentRecommendation.INVESTIGATE.value)),
            trace=dict(data.get("trace", {})),
            created_at=str(data.get("created_at", utc_now())),
        )

    def to_json(self, indent: int = 2) -> str:
        """Serialize DeploymentHandoff to JSON string."""
        return json.dumps(self.to_dict(), indent=indent, sort_keys=True)

    @classmethod
    def from_json(cls, json_str: str) -> DeploymentHandoff:
        """Deserialize DeploymentHandoff from JSON string."""
        return cls.from_dict(json.loads(json_str))


class DeploymentHandoffBuilder:
    """
    Builder and synthesizer that creates an authoritative DeploymentHandoff from
    Programmer execution work products, verification runs, and readiness evaluations.
    """

    def __init__(self) -> None:
        self._artifact: Optional[ProductArtifact] = None
        self._project_id: str = ""
        self._work_order_id: str = ""
        self._execution_id: str = ""
        self._source_revision: Optional[GitRevision] = None
        self._build_result: Optional[BuildPackagingResult] = None
        self._runtime_requirements: dict[str, Any] = {}
        self._configuration_schema: Optional[RuntimeConfigurationSchema] = None
        self._environment_requirements: list[EnvironmentRequirement] = []
        self._verification_summary: Optional[VerificationSummary] = None
        self._acceptance_results: list[AcceptanceCriterionResult] = []
        self._risks: list[EngineeringRisk] = []
        self._blockers: list[ProgrammerBlocker] = []
        self._prerequisites: list[str] = []
        self._evidence: list[VerificationEvidence] = []
        self._readiness_result: Optional[DeploymentReadinessResult] = None
        self._trace: dict[str, Any] = {}

    def set_artifact(self, artifact: Optional[ProductArtifact]) -> DeploymentHandoffBuilder:
        self._artifact = artifact
        return self

    def set_project_id(self, project_id: str) -> DeploymentHandoffBuilder:
        self._project_id = str(project_id or "")
        return self

    def set_work_order_id(self, work_order_id: str) -> DeploymentHandoffBuilder:
        self._work_order_id = str(work_order_id or "")
        return self

    def set_execution_id(self, execution_id: str) -> DeploymentHandoffBuilder:
        self._execution_id = str(execution_id or "")
        return self

    def set_source_revision(self, revision: GitRevision) -> DeploymentHandoffBuilder:
        self._source_revision = revision
        return self

    def set_build_result(self, build_result: BuildPackagingResult) -> DeploymentHandoffBuilder:
        self._build_result = build_result
        return self

    def set_runtime_requirements(self, requirements: dict[str, Any]) -> DeploymentHandoffBuilder:
        self._runtime_requirements = dict(requirements or {})
        return self

    def set_configuration_schema(self, schema: RuntimeConfigurationSchema) -> DeploymentHandoffBuilder:
        self._configuration_schema = schema
        return self

    def set_environment_requirements(self, requirements: Sequence[EnvironmentRequirement]) -> DeploymentHandoffBuilder:
        self._environment_requirements = list(requirements or [])
        return self

    def set_verification_summary(self, summary: VerificationSummary) -> DeploymentHandoffBuilder:
        self._verification_summary = summary
        return self

    def set_acceptance_results(self, results: Sequence[AcceptanceCriterionResult]) -> DeploymentHandoffBuilder:
        self._acceptance_results = list(results or [])
        return self

    def set_risks(self, risks: Sequence[EngineeringRisk] | RiskAssessment) -> DeploymentHandoffBuilder:
        if hasattr(risks, "risks"):
            self._risks = list(getattr(risks, "risks", []))
        else:
            self._risks = list(risks or [])
        return self

    def set_blockers(self, blockers: Sequence[ProgrammerBlocker]) -> DeploymentHandoffBuilder:
        self._blockers = list(blockers or [])
        return self

    def set_prerequisites(self, prerequisites: Sequence[str]) -> DeploymentHandoffBuilder:
        self._prerequisites = list(prerequisites or [])
        return self

    def set_evidence(self, evidence: Sequence[VerificationEvidence]) -> DeploymentHandoffBuilder:
        self._evidence = list(evidence or [])
        return self

    def set_readiness_result(self, readiness: DeploymentReadinessResult) -> DeploymentHandoffBuilder:
        self._readiness_result = readiness
        return self

    def set_trace(self, trace: dict[str, Any]) -> DeploymentHandoffBuilder:
        self._trace = dict(trace or {})
        return self

    def build(self) -> DeploymentHandoff:
        """
        Synthesize all inputs into a validated DeploymentHandoff.
        """
        if self._artifact is None:
            if not self._project_id or not self._work_order_id:
                raise ProgrammerValidationError("DeploymentHandoff requires a ProductArtifact or explicit project_id and work_order_id.", field_name="product_artifact")
            proj_id = self._project_id
            wo_id = self._work_order_id
            exec_id = self._execution_id
            source_rev = self._source_revision
        else:
            proj_id = self._project_id or self._artifact.project_id
            wo_id = self._work_order_id or self._artifact.work_order_id
            exec_id = self._execution_id or self._artifact.execution_id
            source_rev = self._source_revision or self._artifact.source_revision

        # Compile evidence list from artifact and explicit additions
        all_evidence: list[VerificationEvidence] = list(self._evidence)
        if self._artifact and self._artifact.evidence:
            seen_ids = {getattr(e, "evidence_id", "") for e in all_evidence}
            for e in self._artifact.evidence:
                eid = getattr(e, "evidence_id", "")
                if eid not in seen_ids:
                    all_evidence.append(e)
                    seen_ids.add(eid)

        # Perform readiness evaluation if not provided
        readiness = self._readiness_result
        if readiness is None:
            evaluator = DeploymentReadinessEvaluator()
            readiness = evaluator.evaluate(
                artifact=self._artifact,
                build_result=self._build_result,
                verification_summary=self._verification_summary,
                acceptance_results=self._acceptance_results,
                blockers=self._blockers,
                risks=self._risks,
                configuration_schema=self._configuration_schema,
                environment_requirements=self._environment_requirements,
                trace=self._trace,
            )

        # Check for unresolved blockers
        unresolved_blockers = [b for b in self._blockers if not getattr(b, "is_resolved", False)]

        # Determine DeploymentHandoffStatus
        if len(unresolved_blockers) > 0:
            handoff_status = DeploymentHandoffStatus.BLOCKED
        elif readiness.status == DeploymentReadinessStatus.READY:
            handoff_status = DeploymentHandoffStatus.READY
        elif readiness.status == DeploymentReadinessStatus.READY_WITH_WARNINGS:
            handoff_status = DeploymentHandoffStatus.READY_WITH_WARNINGS
        else:
            handoff_status = DeploymentHandoffStatus.NOT_READY

        # Determine Lifecycle State (Programmer NEVER sets DEPLOYED)
        build_ok = False
        if self._build_result is not None:
            build_ok = getattr(self._build_result, "status", None) == BuildPackagingStatus.SUCCESS
        elif self._artifact and self._artifact.build_metadata:
            build_ok = self._artifact.build_metadata.get("status") in ("SUCCESS", BuildPackagingStatus.SUCCESS) or self._artifact.build_metadata.get("exit_code") == 0

        verified_ok = False
        if self._verification_summary is not None:
            v_st = getattr(self._verification_summary, "overall_status", getattr(self._verification_summary, "status", None))
            if v_st is not None:
                v_str = v_st.value if hasattr(v_st, "value") else str(v_st)
                verified_ok = v_str.upper() in ("PASS", "VERIFIED", "SUCCESS")

        if handoff_status in (DeploymentHandoffStatus.READY, DeploymentHandoffStatus.READY_WITH_WARNINGS):
            lifecycle_state = ProductLifecycleState.DEPLOYMENT_READY
        elif verified_ok:
            lifecycle_state = ProductLifecycleState.VERIFIED
        else:
            lifecycle_state = ProductLifecycleState.BUILD_COMPLETE

        # Synthesize Programmer Recommendation
        if handoff_status in (DeploymentHandoffStatus.READY, DeploymentHandoffStatus.READY_WITH_WARNINGS):
            recommendation = DeploymentRecommendation.DEPLOY
        elif handoff_status == DeploymentHandoffStatus.BLOCKED:
            recommendation = DeploymentRecommendation.INVESTIGATE
        else:
            # Analyze blocking issues from readiness to pick best advice
            blocking_strs = " ".join(readiness.blocking_issues).lower()
            if "configuration" in blocking_strs or "environment" in blocking_strs or "secret reference" in blocking_strs:
                recommendation = DeploymentRecommendation.CONFIGURE
            elif "verification" in blocking_strs or "test" in blocking_strs or "build" in blocking_strs or "exit code" in blocking_strs:
                recommendation = DeploymentRecommendation.FIX
            else:
                recommendation = DeploymentRecommendation.INVESTIGATE

        # Aggregate deployment prerequisites
        prereqs = list(self._prerequisites)
        for cf in readiness.required_configuration:
            item = f"Configure required runtime parameter '{cf}'"
            if item not in prereqs:
                prereqs.append(item)
        for ev in readiness.required_environment:
            item = f"Provide required environment variable '{ev}'"
            if item not in prereqs:
                prereqs.append(item)
        for req in self._environment_requirements:
            if getattr(req, "sensitive", False):
                item = f"Ensure secret reference is available for '{req.name}'"
                if item not in prereqs:
                    prereqs.append(item)

        return DeploymentHandoff(
            handoff_id=new_deployment_handoff_id(),
            project_id=proj_id,
            work_order_id=wo_id,
            execution_id=exec_id,
            product_artifact=self._artifact,
            source_revision=source_rev,
            build_information=self._build_result,
            runtime_requirements=dict(self._runtime_requirements or (self._artifact.runtime_requirements if self._artifact else {})),
            configuration_schema=self._configuration_schema or (self._artifact.configuration_schema if self._artifact else None),
            environment_requirements=list(self._environment_requirements),
            verification_summary=self._verification_summary or (self._artifact.verification_summary if self._artifact else None),
            acceptance_results=list(self._acceptance_results),
            known_risks=list(self._risks),
            blockers=list(self._blockers),
            deployment_prerequisites=prereqs,
            evidence=all_evidence,
            status=handoff_status,
            lifecycle_state=lifecycle_state,
            recommendation=recommendation,
            trace=dict(self._trace or {"builder": "DeploymentHandoffBuilder", "evaluated_at": utc_now()}),
            created_at=utc_now(),
        )
