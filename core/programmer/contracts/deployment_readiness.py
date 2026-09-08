from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import json
import os
from typing import Any, Callable, Optional, Sequence

from core.programmer.contracts.acceptance_criteria import AcceptanceCriterion
from core.programmer.contracts.acceptance_result import AcceptanceCriterionResult
from core.programmer.contracts.blocker import ProgrammerBlocker
from core.programmer.contracts.build_packaging import BuildPackagingResult
from core.programmer.contracts.engineering_risk import EngineeringRisk, RiskAssessment
from core.programmer.contracts.environment_boundary import (
    EnvironmentRequirement,
    SecretExposureDetector,
)
from core.programmer.contracts.identifiers import (
    DEPLOYMENT_READINESS_ID_PREFIX,
    new_deployment_readiness_id,
    validate_deployment_readiness_id,
)
from core.programmer.contracts.product_artifact import ProductArtifact
from core.programmer.contracts.runtime_configuration import (
    RuntimeConfiguration,
    RuntimeConfigurationSchema,
)
from core.programmer.contracts.verification import (
    VerificationEvidence,
    VerificationSummary,
)
from core.programmer.types import (
    AcceptanceStatus,
    BuildPackagingStatus,
    DeploymentReadinessStatus,
    EnvironmentRequirementType,
    VerificationStatus,
    VerificationSummaryStatus,
)


def utc_now() -> str:
    """Generate ISO 8601 UTC timestamp."""
    return datetime.now(timezone.utc).isoformat()


@dataclass
class DeploymentReadinessResult:
    """
    Authoritative evaluation result of whether a ProductArtifact is eligible and ready
    for deployment.
    """
    readiness_id: str = field(default_factory=new_deployment_readiness_id)
    artifact_id: str = ""
    project_id: str = ""
    status: DeploymentReadinessStatus = DeploymentReadinessStatus.UNKNOWN
    blocking_issues: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    required_configuration: list[str] = field(default_factory=list)
    required_environment: list[str] = field(default_factory=list)
    evidence: list[VerificationEvidence] = field(default_factory=list)
    trace: dict[str, Any] = field(default_factory=dict)
    evaluated_at: str = field(default_factory=utc_now)

    def __post_init__(self) -> None:
        if not self.readiness_id:
            self.readiness_id = new_deployment_readiness_id()
        else:
            validate_deployment_readiness_id(self.readiness_id)

        if isinstance(self.status, str):
            try:
                self.status = DeploymentReadinessStatus(self.status.upper())
            except (ValueError, TypeError):
                self.status = DeploymentReadinessStatus.UNKNOWN

        # Rehydrate evidence if dicts
        normalized_ev: list[VerificationEvidence] = []
        for e in self.evidence:
            if isinstance(e, dict):
                normalized_ev.append(VerificationEvidence.from_dict(e))
            else:
                normalized_ev.append(e)
        self.evidence = normalized_ev

    @property
    def is_ready(self) -> bool:
        """True if readiness is READY or READY_WITH_WARNINGS."""
        return self.status in (
            DeploymentReadinessStatus.READY,
            DeploymentReadinessStatus.READY_WITH_WARNINGS,
        )

    @property
    def has_blocking_issues(self) -> bool:
        """True if there are any blocking issues preventing deployment."""
        return len(self.blocking_issues) > 0 or self.status == DeploymentReadinessStatus.NOT_READY

    @property
    def is_unknown(self) -> bool:
        """True if readiness cannot be determined due to insufficient evidence."""
        return self.status == DeploymentReadinessStatus.UNKNOWN

    def to_dict(self) -> dict[str, Any]:
        return {
            "readiness_id": self.readiness_id,
            "artifact_id": self.artifact_id,
            "project_id": self.project_id,
            "status": self.status.value,
            "blocking_issues": list(self.blocking_issues),
            "warnings": list(self.warnings),
            "required_configuration": list(self.required_configuration),
            "required_environment": list(self.required_environment),
            "evidence": [e.to_dict() if hasattr(e, "to_dict") else e for e in self.evidence],
            "trace": dict(self.trace),
            "evaluated_at": self.evaluated_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> DeploymentReadinessResult:
        return cls(
            readiness_id=str(data.get("readiness_id", "")),
            artifact_id=str(data.get("artifact_id", "")),
            project_id=str(data.get("project_id", "")),
            status=DeploymentReadinessStatus(data.get("status", DeploymentReadinessStatus.UNKNOWN.value)),
            blocking_issues=list(data.get("blocking_issues", [])),
            warnings=list(data.get("warnings", [])),
            required_configuration=list(data.get("required_configuration", [])),
            required_environment=list(data.get("required_environment", [])),
            evidence=[
                VerificationEvidence.from_dict(e) if isinstance(e, dict) else e
                for e in data.get("evidence", [])
            ],
            trace=dict(data.get("trace", {})),
            evaluated_at=str(data.get("evaluated_at", utc_now())),
        )

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent, sort_keys=True)

    @classmethod
    def from_json(cls, json_str: str) -> DeploymentReadinessResult:
        return cls.from_dict(json.loads(json_str))


class DeploymentReadinessEvaluator:
    """
    Deterministic evaluator for assessing whether a ProductArtifact is ready for deployment.
    Never deploys, never contacts external infrastructure, ignores unverified agent claims,
    and never automatically resolves blocking issues.
    """

    def evaluate(
        self,
        artifact: Optional[ProductArtifact] = None,
        build_result: Optional[BuildPackagingResult] = None,
        verification_summary: Optional[VerificationSummary] = None,
        acceptance_criteria: Optional[Sequence[AcceptanceCriterion]] = None,
        acceptance_results: Optional[Sequence[AcceptanceCriterionResult]] = None,
        blockers: Optional[Sequence[ProgrammerBlocker]] = None,
        risks: Optional[Sequence[EngineeringRisk]] = None,
        risk_assessment: Optional[RiskAssessment] = None,
        configuration_schema: Optional[RuntimeConfigurationSchema] = None,
        runtime_configuration: Optional[RuntimeConfiguration] = None,
        environment_requirements: Optional[Sequence[EnvironmentRequirement]] = None,
        available_secret_refs: Optional[Sequence[str] | set[str]] = None,
        secret_resolver: Optional[Callable[[str], bool]] = None,
        file_exists_fn: Optional[Callable[[str], bool]] = None,
        trace: Optional[dict[str, Any]] = None,
    ) -> DeploymentReadinessResult:
        """
        Perform exhaustive readiness inspection and produce a deterministic DeploymentReadinessResult.
        """
        blocking_issues: list[str] = []
        warnings: list[str] = []
        required_config: list[str] = []
        required_env: list[str] = []
        authoritative_evidence: list[VerificationEvidence] = []
        insufficient_evidence_reasons: list[str] = []

        # ---------------------------------------------------------------------
        # 1. Inspect Artifact Identity & Lineage
        # ---------------------------------------------------------------------
        if artifact is None:
            blocking_issues.append("No ProductArtifact provided for deployment readiness evaluation.")
            insufficient_evidence_reasons.append("Artifact is missing or build failed to produce a product artifact.")
        else:
            if not artifact.artifact_id:
                blocking_issues.append("Artifact has no artifact_id.")
            if not artifact.project_id:
                blocking_issues.append("Artifact has no project_id.")

            # Source revision inspection
            if artifact.source_revision is None:
                insufficient_evidence_reasons.append("Artifact source revision is missing or unspecified.")
            else:
                commit_hash = getattr(artifact.source_revision, "commit_hash", "")
                if not commit_hash or len(commit_hash.strip()) < 7:
                    insufficient_evidence_reasons.append("Artifact source revision has missing or invalid commit hash.")

            # ---------------------------------------------------------------------
            # 2. Inspect Artifact Existence on Disk
            # ---------------------------------------------------------------------
            if not artifact.artifact_reference or not artifact.artifact_reference.strip():
                blocking_issues.append("ProductArtifact has empty or unspecified artifact_reference.")
            else:
                ref = artifact.artifact_reference.strip()
                if file_exists_fn is not None:
                    if not file_exists_fn(ref):
                        blocking_issues.append(f"Artifact file does not exist at reference: '{ref}'.")
                else:
                    # Default filesystem check if absolute or relative file path
                    if ref.startswith("/") or ref.startswith("./"):
                        if not os.path.exists(ref):
                            blocking_issues.append(f"Artifact file not found on disk at '{ref}'.")

        # ---------------------------------------------------------------------
        # 3. Inspect Build Status & Build Metadata
        # ---------------------------------------------------------------------
        if build_result is not None:
            if hasattr(build_result, "status") and build_result.status != BuildPackagingStatus.SUCCESS:
                st_str = build_result.status.value if hasattr(build_result.status, "value") else str(build_result.status)
                err_msg = getattr(build_result, "error_message", "") or "Build failure"
                blocking_issues.append(f"Build operation failed with status '{st_str}': {err_msg}.")
            if hasattr(build_result, "exit_code") and build_result.exit_code is not None and build_result.exit_code != 0:
                blocking_issues.append(f"Build process returned non-zero exit code: {build_result.exit_code}.")

        if artifact is not None and artifact.build_metadata:
            exit_code = artifact.build_metadata.get("exit_code")
            if exit_code is not None and exit_code != 0:
                blocking_issues.append(f"Artifact build metadata indicates build failure with exit code {exit_code}.")
            build_status = artifact.build_metadata.get("status")
            if build_status is not None:
                st_str = str(build_status).upper()
                if st_str in ("FAILED", "ERROR", "UNAUTHORIZED", "MISSING_ARTIFACT"):
                    blocking_issues.append(f"Artifact build metadata recorded failed status: '{st_str}'.")

        # ---------------------------------------------------------------------
        # 4. Inspect Verification Status & Evidence (Ignoring Agent Claims)
        # ---------------------------------------------------------------------
        # Collect candidate evidence from artifact and arguments
        candidate_evidence: list[VerificationEvidence] = list(artifact.evidence) if artifact is not None else []

        # Filter out Cline's claims ("Do not treat Cline's claims as evidence.")
        for ev in candidate_evidence:
            if ev.is_agent_claim:
                warnings.append(f"Ignored unverified agent claim in evidence '{ev.evidence_id}': {ev.description}")
            else:
                authoritative_evidence.append(ev)

        summary = verification_summary or (artifact.verification_summary if artifact is not None else None)
        if summary is not None:
            if hasattr(summary, "is_failed") and summary.is_failed:
                blocking_issues.append("Verification summary indicates failing verification checks.")
            else:
                st = getattr(summary, "overall_status", getattr(summary, "status", None))
                if st is not None:
                    st_val = st.value if hasattr(st, "value") else str(st)
                    if st_val in ("FAIL", "FAILED", "ERROR"):
                        blocking_issues.append(f"Verification summary indicates failure status: '{st_val}'.")
                    elif st_val in ("NOT_RUN", "NOT_VERIFIED", "UNVERIFIED"):
                        insufficient_evidence_reasons.append(f"Verification summary status is unverified: '{st_val}'.")

            # Inspect individual checks inside summary
            summary_checks = getattr(summary, "checks", getattr(summary, "verification_checks", []))
            for chk in summary_checks:
                chk_st = getattr(chk, "status", None)
                chk_st_str = chk_st.value if hasattr(chk_st, "value") else str(chk_st)
                chk_id = getattr(chk, "check_id", "chk")
                chk_desc = getattr(chk, "description", getattr(chk, "command", ""))
                if chk_st_str in ("FAIL", "ERROR"):
                    blocking_issues.append(f"Verification check '{chk_id}' failed: {chk_desc}.")
                elif chk_st_str in ("NOT_RUN", "NOT_VERIFIED", "UNVERIFIED"):
                    insufficient_evidence_reasons.append(f"Verification check '{chk_id}' was not verified ({chk_st_str}): {chk_desc}.")

        # Check if authoritative empirical evidence exists
        if not authoritative_evidence and (summary is None or getattr(summary, "status", None) is None):
            insufficient_evidence_reasons.append("No authoritative verification evidence available (agent claims cannot substantiate readiness).")

        # ---------------------------------------------------------------------
        # 5. Inspect Acceptance Criteria & Results
        # ---------------------------------------------------------------------
        ac_map: dict[str, AcceptanceCriterion] = {}
        if acceptance_criteria:
            for ac in acceptance_criteria:
                ac_map[ac.criterion_id] = ac

        if acceptance_results:
            for r in acceptance_results:
                r_status = r.status if isinstance(r.status, AcceptanceStatus) else AcceptanceStatus(str(r.status))
                cid = getattr(r, "criterion_id", "")
                desc = getattr(r, "description", "") or cid
                ac_item = ac_map.get(cid)
                is_mandatory = ac_item.is_mandatory if ac_item is not None else True

                if r_status == AcceptanceStatus.FAIL:
                    blocking_issues.append(f"Acceptance criterion '{desc}' failed: {r.message or 'Test assertion failure'}.")
                elif r_status == AcceptanceStatus.NOT_VERIFIED or str(r_status) in ("SKIPPED", "NOT_RUN"):
                    if is_mandatory:
                        insufficient_evidence_reasons.append(f"Mandatory acceptance criterion '{desc}' is unverified ({getattr(r_status, 'value', r_status)}).")
                    else:
                        warnings.append(f"Optional acceptance criterion '{desc}' is unverified ({getattr(r_status, 'value', r_status)}).")
        elif acceptance_criteria:
            # Criteria specified but no evaluation results provided
            for ac in acceptance_criteria:
                if ac.is_mandatory:
                    insufficient_evidence_reasons.append(f"Mandatory acceptance criterion '{ac.description}' has no verification result.")
                else:
                    warnings.append(f"Optional acceptance criterion '{ac.description}' has no verification result.")

        # ---------------------------------------------------------------------
        # 6. Inspect Configuration Schema & Runtime Configuration
        # ---------------------------------------------------------------------
        target_schema = configuration_schema
        if target_schema is None and artifact is not None and artifact.configuration_schema:
            if isinstance(artifact.configuration_schema, RuntimeConfigurationSchema):
                target_schema = artifact.configuration_schema
            elif isinstance(artifact.configuration_schema, dict) and artifact.configuration_schema:
                try:
                    target_schema = RuntimeConfigurationSchema.from_dict(artifact.configuration_schema)
                except Exception:
                    target_schema = None

        if target_schema is not None:
            # Extract required configuration fields
            req_fields = list(getattr(target_schema, "required_fields", []))
            defaults = dict(getattr(target_schema, "defaults", {}))
            for rf in req_fields:
                if rf not in required_config:
                    required_config.append(rf)

            if runtime_configuration is not None:
                rep = target_schema.validate_configuration(runtime_configuration, raise_on_error=False)
                if not rep.is_valid:
                    for err in rep.errors:
                        blocking_issues.append(f"Runtime configuration error: {err}")
            else:
                # If no runtime configuration provided, verify that all required fields have defaults
                missing_without_defaults = [f for f in req_fields if f not in defaults or defaults[f] is None]
                if missing_without_defaults:
                    blocking_issues.append(
                        f"Missing required configuration fields with no default values: {missing_without_defaults}."
                    )
        elif artifact is not None and artifact.is_configuration():
            blocking_issues.append("Configuration artifact lacks a valid RuntimeConfigurationSchema.")

        # ---------------------------------------------------------------------
        # 7. Inspect Runtime Requirements & Environment
        # ---------------------------------------------------------------------
        if artifact is not None and artifact.environment_requirements:
            env_vars = artifact.environment_requirements.get("env_vars", [])
            if isinstance(env_vars, list):
                for ev in env_vars:
                    if ev not in required_env:
                        required_env.append(ev)
            elif isinstance(env_vars, dict):
                for ev in env_vars.keys():
                    if ev not in required_env:
                        required_env.append(ev)

        # Process structured EnvironmentRequirement definitions
        if environment_requirements:
            for req in environment_requirements:
                if req.name not in required_env:
                    required_env.append(req.name)

                # Determine if requirement value is provided
                provided_val = None
                is_provided = False
                if runtime_configuration is not None and req.name in runtime_configuration.values:
                    provided_val = runtime_configuration.values[req.name]
                    is_provided = True
                elif artifact is not None and artifact.environment_requirements and isinstance(artifact.environment_requirements, dict):
                    if req.name in artifact.environment_requirements:
                        provided_val = artifact.environment_requirements[req.name]
                        is_provided = True
                    elif "values" in artifact.environment_requirements and isinstance(artifact.environment_requirements["values"], dict):
                        if req.name in artifact.environment_requirements["values"]:
                            provided_val = artifact.environment_requirements["values"][req.name]
                            is_provided = True

                # Check secret reference availability if sensitive or SECRET_REF
                if req.sensitive or req.type == EnvironmentRequirementType.SECRET_REF:
                    target_ref = str(provided_val).strip() if is_provided and provided_val is not None else f"secret_ref:{req.name}"
                    if available_secret_refs is not None or secret_resolver is not None:
                        ref_found = False
                        if available_secret_refs is not None:
                            avail_set = set(available_secret_refs)
                            unprefixed = target_ref.split(":", 1)[1] if ":" in target_ref else target_ref
                            if target_ref in avail_set or unprefixed in avail_set or req.name in avail_set:
                                ref_found = True
                        if secret_resolver is not None:
                            if secret_resolver(target_ref) or secret_resolver(req.name):
                                ref_found = True
                        if not ref_found:
                            blocking_issues.append(f"Secret reference unavailable: '{target_ref}' for requirement '{req.name}'.")
                        else:
                            is_provided = True

                # Check value validity if provided
                if is_provided and provided_val is not None:
                    val_err = req.validate_value(provided_val)
                    if val_err is not None:
                        blocking_issues.append(f"Invalid environment requirement '{req.name}': {val_err}")

                # Check missing required environment requirement
                if req.required and not is_provided:
                    blocking_issues.append(f"Missing required environment requirement: '{req.name}'.")

        if artifact is not None and artifact.runtime_requirements:
            # E.g. python version, node version, memory
            memory = artifact.runtime_requirements.get("min_memory_mb")
            if memory is not None and isinstance(memory, (int, float)) and memory > 65536:
                warnings.append(f"High memory requirement detected: {memory}MB.")

        # ---------------------------------------------------------------------
        # 7b. Scan for Accidental Secret Exposure in Artifact & Evidence
        # ---------------------------------------------------------------------
        # Scan artifact reference
        if artifact is not None and artifact.artifact_reference:
            for finding in SecretExposureDetector.scan_text(artifact.artifact_reference, location="artifact.artifact_reference"):
                blocking_issues.append(f"Accidental secret exposure detected in {finding.location}: {finding.secret_type} pattern matched.")

        # Scan artifact build metadata
        if artifact is not None and artifact.build_metadata:
            for k, v in artifact.build_metadata.items():
                if isinstance(v, str):
                    for finding in SecretExposureDetector.scan_text(v, location=f"artifact.build_metadata.{k}"):
                        blocking_issues.append(f"Accidental secret exposure detected in {finding.location}: {finding.secret_type} pattern matched.")

        # Scan evidence descriptions and data
        for ev in candidate_evidence:
            for finding in SecretExposureDetector.scan_text(ev.description, location=f"evidence.{ev.evidence_id}.description"):
                blocking_issues.append(f"Accidental secret exposure detected in {finding.location}: {finding.secret_type} pattern matched.")
            if ev.data:
                for k, v in ev.data.items():
                    if isinstance(v, str):
                        for finding in SecretExposureDetector.scan_text(v, location=f"evidence.{ev.evidence_id}.data.{k}"):
                            blocking_issues.append(f"Accidental secret exposure detected in {finding.location}: {finding.secret_type} pattern matched.")

        # Scan runtime configuration values
        if runtime_configuration is not None:
            for k, v in runtime_configuration.values.items():
                if isinstance(v, str):
                    for finding in SecretExposureDetector.scan_text(v, location=f"runtime_configuration.{k}"):
                        blocking_issues.append(f"Accidental secret exposure detected in {finding.location}: {finding.secret_type} pattern matched.")

        # ---------------------------------------------------------------------
        # 8. Inspect Known Blockers (Never auto-resolve)
        # ---------------------------------------------------------------------
        if blockers:
            for b in blockers:
                if not b.is_resolved:
                    cat_str = b.category.value if hasattr(b.category, "value") else str(b.category)
                    blocking_issues.append(f"Unresolved blocker [{cat_str}]: {b.description}")
                else:
                    warnings.append(f"Noting historical resolved blocker [{b.blocker_id}]: {b.description}")

        # ---------------------------------------------------------------------
        # 9. Inspect Engineering Risks
        # ---------------------------------------------------------------------
        all_risks: list[EngineeringRisk] = list(risks or [])
        if risk_assessment is not None:
            all_risks.extend(risk_assessment.risks)
            all_risks.extend(risk_assessment.material_risks)

        # Deduplicate risks by risk_id
        seen_risk_ids: set[str] = set()
        for r in all_risks:
            if r.risk_id in seen_risk_ids:
                continue
            seen_risk_ids.add(r.risk_id)

            sev_str = r.severity.value if hasattr(r.severity, "value") else str(r.severity)
            cat_str = r.category.value if hasattr(r.category, "value") else str(r.category)
            if sev_str in ("HIGH", "CRITICAL"):
                if r.escalation_required or not r.mitigation:
                    blocking_issues.append(
                        f"Unresolved material {sev_str} risk [{cat_str}]: {r.description}"
                    )
                else:
                    warnings.append(
                        f"Mitigated {sev_str} risk [{cat_str}]: {r.description} (Mitigation: {r.mitigation})"
                    )
            elif sev_str in ("LOW", "MEDIUM"):
                warnings.append(f"Known {sev_str.lower()} risk [{cat_str}]: {r.description}")

        # ---------------------------------------------------------------------
        # 10. Synthesize Status
        # ---------------------------------------------------------------------
        if len(blocking_issues) > 0:
            status = DeploymentReadinessStatus.NOT_READY
        elif len(insufficient_evidence_reasons) > 0:
            status = DeploymentReadinessStatus.UNKNOWN
            warnings.extend(insufficient_evidence_reasons)
        elif len(warnings) > 0:
            status = DeploymentReadinessStatus.READY_WITH_WARNINGS
        else:
            status = DeploymentReadinessStatus.READY

        res_id = new_deployment_readiness_id()
        art_id = artifact.artifact_id if artifact is not None else ""
        proj_id = (artifact.project_id if artifact is not None else "") or (
            build_result.artifact.project_id if build_result and getattr(build_result, "artifact", None) else ""
        )
        return DeploymentReadinessResult(
            readiness_id=res_id,
            artifact_id=art_id,
            project_id=proj_id,
            status=status,
            blocking_issues=blocking_issues,
            warnings=warnings,
            required_configuration=required_config,
            required_environment=required_env,
            evidence=authoritative_evidence,
            trace=dict(trace or {"evaluated_at": utc_now()}),
            evaluated_at=utc_now(),
        )
