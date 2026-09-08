"""
Unit tests for PROGRAMMER V1 — PHASE 9.6: Product Deployment Handoff.

Validates:
1. Canonical identifier prefix ('pdhand-') and format validation.
2. Ready handoff: fully verified artifact and passing checks -> READY, recommendation DEPLOY.
3. Blocked handoff: unresolved blocker -> BLOCKED, recommendation INVESTIGATE.
4. Missing configuration: missing required configuration/schema -> NOT_READY, recommendation CONFIGURE.
5. Failed verification: failing test check -> NOT_READY, recommendation FIX.
6. Risk propagation: engineering risks correctly propagate into handoff.
7. Evidence linkage: verification evidence correctly linked.
8. Lineage validation: mismatched project_id, work_order_id, execution_id or revision raises ArtifactLineageError / SourceRevisionMismatchError.
9. Premature deployment claim rejection: attempting to set lifecycle_state to DEPLOYED raises PrematureDeploymentClaimError.
10. Lifecycle state distinction: BUILD_COMPLETE, VERIFIED, DEPLOYMENT_READY are clearly distinguished.
11. Programmer recommendations: advisory recommendations (DEPLOY, FIX, INVESTIGATE, CONFIGURE).
12. Serialization roundtrips: to_dict/from_dict and to_json/from_json fidelity.
"""

import json
import pytest

from core.programmer.contracts.acceptance_criteria import AcceptanceCriterion
from core.programmer.contracts.acceptance_result import AcceptanceCriterionResult
from core.programmer.contracts.blocker import ProgrammerBlocker
from core.programmer.contracts.build_packaging import BuildPackagingResult
from core.programmer.contracts.deployment_handoff import (
    DeploymentHandoff,
    DeploymentHandoffBuilder,
)
from core.programmer.contracts.deployment_readiness import DeploymentReadinessResult
from core.programmer.contracts.engineering_risk import EngineeringRisk, RiskAssessment
from core.programmer.contracts.environment_boundary import EnvironmentRequirement
from core.programmer.contracts.git_model import GitRevision
from core.programmer.contracts.identifiers import (
    DEPLOYMENT_HANDOFF_ID_PREFIX,
    new_deployment_handoff_id,
    validate_deployment_handoff_id,
)
from core.programmer.contracts.product_artifact import ProductArtifact
from core.programmer.contracts.runtime_configuration import (
    ConfigurationField,
    RuntimeConfiguration,
    RuntimeConfigurationSchema,
)
from core.programmer.contracts.verification import (
    VerificationEvidence,
    VerificationSummary,
)
from core.programmer.errors import (
    ArtifactLineageError,
    DeploymentHandoffError,
    InvalidProgrammerIdError,
    PrematureDeploymentClaimError,
    SourceRevisionMismatchError,
)
from core.programmer.types import (
    AcceptanceStatus,
    BuildPackagingStatus,
    ConfigurationFieldType,
    DeploymentHandoffStatus,
    DeploymentReadinessStatus,
    DeploymentRecommendation,
    EngineeringRiskCategory,
    EnvironmentRequirementType,
    ProductArtifactType,
    ProductLifecycleState,
    ProgrammerBlockerCategory,
    ProgrammerBlockerSeverity,
    VerificationEvidenceSourceType,
    VerificationStatus,
    VerificationSummaryStatus,
)


def _make_sample_revision(commit_hash: str = "abcdef0123456789") -> GitRevision:
    return GitRevision(
        revision_id="grev-test-12345678",
        commit_hash=commit_hash,
        branch="main",
    )


def _make_sample_evidence(
    evidence_id: str = "vevid-test-12345678",
    description: str = "Pytest passed with 100% assertions",
) -> VerificationEvidence:
    return VerificationEvidence(
        evidence_id=evidence_id,
        execution_id="pexec-test-1234",
        work_order_id="pwo-test-1234",
        source_type=VerificationEvidenceSourceType.COMMAND_OUTPUT,
        description=description,
        is_agent_claim=False,
    )


def _make_sample_artifact(
    artifact_id: str = "part-1234567890ab",
    project_id: str = "proj-test-1234",
    work_order_id: str = "pwo-test-1234",
    execution_id: str = "pexec-test-1234",
    commit_hash: str = "abcdef0123456789",
    evidence: list[VerificationEvidence] | None = None,
) -> ProductArtifact:
    rev = _make_sample_revision(commit_hash)
    return ProductArtifact(
        artifact_id=artifact_id,
        project_id=project_id,
        work_order_id=work_order_id,
        execution_id=execution_id,
        source_revision=rev,
        artifact_type=ProductArtifactType.BUILD,
        artifact_reference="dist/bundle.tar.gz",
        version="1.0.0",
        evidence=list(evidence or []),
        build_metadata={"status": "SUCCESS", "exit_code": 0},
    )


def test_deployment_handoff_identifiers():
    """Validates identifier prefix and validation logic."""
    hid = new_deployment_handoff_id()
    assert hid.startswith(DEPLOYMENT_HANDOFF_ID_PREFIX)
    validate_deployment_handoff_id(hid)  # Does not raise

    validate_deployment_handoff_id("pdhand-12345678")
    with pytest.raises(InvalidProgrammerIdError):
        validate_deployment_handoff_id("invalid-id")
    with pytest.raises(InvalidProgrammerIdError):
        validate_deployment_handoff_id("pread-12345678")
    with pytest.raises(InvalidProgrammerIdError):
        validate_deployment_handoff_id("")


def test_ready_deployment_handoff():
    """Fully verified artifact and passing checks produce status READY and recommendation DEPLOY."""
    evidence = _make_sample_evidence()
    artifact = _make_sample_artifact(evidence=[evidence])
    summary = VerificationSummary(
        summary_id="vsum-test-1234",
        execution_id="pexec-test-1234",
        work_order_id="pwo-test-1234",
        overall_status=VerificationSummaryStatus.PASS,
    )

    builder = DeploymentHandoffBuilder()
    builder.set_artifact(artifact)
    builder.set_verification_summary(summary)
    builder.set_evidence([evidence])

    handoff = builder.build()

    assert handoff.status == DeploymentHandoffStatus.READY
    assert handoff.lifecycle_state == ProductLifecycleState.DEPLOYMENT_READY
    assert handoff.recommendation == DeploymentRecommendation.DEPLOY
    assert handoff.is_ready is True
    assert handoff.is_blocked is False
    assert handoff.project_id == artifact.project_id
    assert handoff.work_order_id == artifact.work_order_id
    assert handoff.execution_id == artifact.execution_id


def test_blocked_deployment_handoff():
    """Unresolved blocker marks handoff as BLOCKED with recommendation INVESTIGATE."""
    evidence = _make_sample_evidence()
    artifact = _make_sample_artifact(evidence=[evidence])
    blocker = ProgrammerBlocker(
        blocker_id="pblk-test-12345678",
        execution_id="pexec-test-1234",
        work_order_id="pwo-test-1234",
        category=ProgrammerBlockerCategory.INFRASTRUCTURE_UNAVAILABLE,
        severity=ProgrammerBlockerSeverity.CRITICAL,
        description="Database migration runner host is down",
        is_resolved=False,
    )

    builder = DeploymentHandoffBuilder()
    builder.set_artifact(artifact)
    builder.set_blockers([blocker])
    builder.set_evidence([evidence])

    handoff = builder.build()

    assert handoff.status == DeploymentHandoffStatus.BLOCKED
    assert handoff.recommendation == DeploymentRecommendation.INVESTIGATE
    assert handoff.is_ready is False
    assert handoff.is_blocked is True
    assert len(handoff.blockers) == 1


def test_missing_configuration_deployment_handoff():
    """Missing required configuration flags NOT_READY with recommendation CONFIGURE."""
    evidence = _make_sample_evidence()
    artifact = _make_sample_artifact(evidence=[evidence])

    # Schema requiring DATABASE_URL with no default
    schema = RuntimeConfigurationSchema(
        schema_id="psch-test-12345678",
        product_id="proj-test-1234",
        fields=[
            ConfigurationField(
                name="DATABASE_URL",
                type=ConfigurationFieldType.STRING,
                required=True,
            )
        ],
    )

    builder = DeploymentHandoffBuilder()
    builder.set_artifact(artifact)
    builder.set_configuration_schema(schema)
    builder.set_evidence([evidence])

    handoff = builder.build()

    assert handoff.status == DeploymentHandoffStatus.NOT_READY
    assert handoff.recommendation == DeploymentRecommendation.CONFIGURE
    assert any("Configure required runtime parameter 'DATABASE_URL'" in p for p in handoff.deployment_prerequisites)


def test_failed_verification_deployment_handoff():
    """Failing verification checks flag NOT_READY with recommendation FIX."""
    evidence = _make_sample_evidence()
    artifact = _make_sample_artifact(evidence=[evidence])
    summary = VerificationSummary(
        summary_id="vsum-test-1234",
        execution_id="pexec-test-1234",
        work_order_id="pwo-test-1234",
        overall_status=VerificationSummaryStatus.FAIL,
    )

    builder = DeploymentHandoffBuilder()
    builder.set_artifact(artifact)
    builder.set_verification_summary(summary)
    builder.set_evidence([evidence])

    handoff = builder.build()

    assert handoff.status == DeploymentHandoffStatus.NOT_READY
    assert handoff.recommendation == DeploymentRecommendation.FIX


def test_risk_propagation_deployment_handoff():
    """Engineering risks correctly propagate into the handoff."""
    evidence = _make_sample_evidence()
    artifact = _make_sample_artifact(evidence=[evidence])
    risk = EngineeringRisk(
        risk_id="prisk-test-12345678",
        category=EngineeringRiskCategory.COMPATIBILITY,
        description="Upstream API will deprecate TLS 1.2 next month",
        mitigation="TLS 1.3 already supported in configuration",
    )

    builder = DeploymentHandoffBuilder()
    builder.set_artifact(artifact)
    builder.set_risks([risk])
    builder.set_evidence([evidence])

    handoff = builder.build()

    assert len(handoff.known_risks) == 1
    assert handoff.known_risks[0].risk_id == "prisk-test-12345678"


def test_evidence_linkage_deployment_handoff():
    """Empirical verification evidence is collected and linked in the handoff."""
    ev1 = _make_sample_evidence(evidence_id="vevid-test-00000001", description="Unit tests")
    ev2 = _make_sample_evidence(evidence_id="vevid-test-00000002", description="Integration tests")
    artifact = _make_sample_artifact(evidence=[ev1])

    builder = DeploymentHandoffBuilder()
    builder.set_artifact(artifact)
    builder.set_evidence([ev2])

    handoff = builder.build()

    # Both artifact evidence and builder evidence must be linked without duplicates
    eids = [getattr(e, "evidence_id", "") for e in handoff.evidence]
    assert "vevid-test-00000001" in eids
    assert "vevid-test-00000002" in eids
    assert len(handoff.evidence) == 2


def test_lineage_validation_deployment_handoff():
    """Mismatched project/work order/execution IDs or revision raise lineage errors."""
    artifact = _make_sample_artifact(project_id="proj-alpha", work_order_id="pwo-alpha")

    # Mismatched project_id
    with pytest.raises(ArtifactLineageError):
        DeploymentHandoff(
            product_artifact=artifact,
            project_id="proj-mismatched",
        )

    # Mismatched work_order_id
    with pytest.raises(ArtifactLineageError):
        DeploymentHandoff(
            product_artifact=artifact,
            work_order_id="pwo-mismatched",
        )

    # Mismatched source_revision commit hash
    mismatched_rev = GitRevision(
        revision_id="grev-other-1234",
        commit_hash="9999999999999999",
        branch="main",
    )
    with pytest.raises(SourceRevisionMismatchError):
        DeploymentHandoff(
            product_artifact=artifact,
            source_revision=mismatched_rev,
        )


def test_premature_deployment_claim_rejected():
    """Attempting to declare lifecycle_state == DEPLOYED raises PrematureDeploymentClaimError."""
    artifact = _make_sample_artifact()

    with pytest.raises(PrematureDeploymentClaimError) as exc:
        DeploymentHandoff(
            product_artifact=artifact,
            lifecycle_state=ProductLifecycleState.DEPLOYED,
        )
    assert "cannot claim artifact is DEPLOYED" in str(exc.value)


def test_cannot_recommend_deploy_when_not_ready_or_blocked():
    """Programmer cannot recommend DEPLOY when handoff is NOT_READY or BLOCKED."""
    artifact = _make_sample_artifact()

    with pytest.raises(DeploymentHandoffError) as exc:
        DeploymentHandoff(
            product_artifact=artifact,
            status=DeploymentHandoffStatus.NOT_READY,
            recommendation=DeploymentRecommendation.DEPLOY,
        )
    assert "Programmer cannot recommend DEPLOY" in str(exc.value)

    with pytest.raises(DeploymentHandoffError) as exc:
        DeploymentHandoff(
            product_artifact=artifact,
            status=DeploymentHandoffStatus.BLOCKED,
            recommendation=DeploymentRecommendation.DEPLOY,
        )
    assert "Programmer cannot recommend DEPLOY" in str(exc.value)


def test_lifecycle_state_distinction():
    """Validates clear distinction between BUILD_COMPLETE, VERIFIED, and DEPLOYMENT_READY."""
    evidence = _make_sample_evidence()
    artifact = _make_sample_artifact(evidence=[evidence])

    # Case 1: Build complete only (no verification summary)
    builder1 = DeploymentHandoffBuilder()
    builder1.set_artifact(artifact)
    handoff1 = builder1.build()
    assert handoff1.lifecycle_state == ProductLifecycleState.BUILD_COMPLETE

    # Case 2: Verified (verification summary passed, but missing config keeps it not deployment ready)
    schema = RuntimeConfigurationSchema(
        schema_id="psch-test-12345678",
        product_id="proj-test-1234",
        fields=[ConfigurationField(name="API_KEY", type=ConfigurationFieldType.STRING, required=True)],
    )
    summary = VerificationSummary(
        summary_id="vsum-test-1234",
        execution_id="pexec-test-1234",
        work_order_id="pwo-test-1234",
        overall_status=VerificationSummaryStatus.PASS,
    )
    builder2 = DeploymentHandoffBuilder()
    builder2.set_artifact(artifact)
    builder2.set_verification_summary(summary)
    builder2.set_configuration_schema(schema)
    handoff2 = builder2.build()
    assert handoff2.lifecycle_state == ProductLifecycleState.VERIFIED

    # Case 3: Deployment Ready (passing checks, no missing config)
    builder3 = DeploymentHandoffBuilder()
    builder3.set_artifact(artifact)
    builder3.set_verification_summary(summary)
    handoff3 = builder3.build()
    assert handoff3.lifecycle_state == ProductLifecycleState.DEPLOYMENT_READY


def test_serialization_roundtrips():
    """Validates dictionary and JSON round-trip serialization and deserialization."""
    evidence = _make_sample_evidence()
    artifact = _make_sample_artifact(evidence=[evidence])
    req = EnvironmentRequirement(
        name="PORT",
        type=EnvironmentRequirementType.INTEGER,
        required=True,
    )

    builder = DeploymentHandoffBuilder()
    builder.set_artifact(artifact)
    builder.set_environment_requirements([req])
    builder.set_evidence([evidence])

    handoff = builder.build()

    d = handoff.to_dict()
    assert d["handoff_id"].startswith(DEPLOYMENT_HANDOFF_ID_PREFIX)
    assert d["status"] == handoff.status.value
    assert d["recommendation"] == handoff.recommendation.value
    assert "product_artifact" in d

    # JSON roundtrip
    json_str = handoff.to_json()
    assert "pdhand-" in json_str

    handoff2 = DeploymentHandoff.from_json(json_str)
    assert handoff2.handoff_id == handoff.handoff_id
    assert handoff2.status == handoff.status
    assert handoff2.recommendation == handoff.recommendation
    assert handoff2.lifecycle_state == handoff.lifecycle_state
    assert len(handoff2.environment_requirements) == len(handoff.environment_requirements)
