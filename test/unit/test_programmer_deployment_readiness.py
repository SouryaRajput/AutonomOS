from __future__ import annotations

import json
import pytest

from core.enums import RiskLevel
from core.programmer.contracts.acceptance_criteria import AcceptanceCriterion
from core.programmer.contracts.acceptance_result import AcceptanceCriterionResult
from core.programmer.contracts.blocker import ProgrammerBlocker
from core.programmer.contracts.build_packaging import BuildPackagingResult
from core.programmer.contracts.deployment_readiness import (
    DeploymentReadinessEvaluator,
    DeploymentReadinessResult,
)
from core.programmer.contracts.engineering_risk import EngineeringRisk, RiskAssessment
from core.programmer.contracts.git_model import GitRevision
from core.programmer.contracts.identifiers import (
    DEPLOYMENT_READINESS_ID_PREFIX,
    new_blocker_id,
    new_deployment_readiness_id,
    new_engineering_risk_id,
    new_git_revision_id,
    new_risk_assessment_id,
    new_verification_evidence_id,
    validate_deployment_readiness_id,
)
from core.programmer.contracts.product_artifact import (
    ProductArtifact,
    ProductArtifactType,
)
from core.programmer.contracts.runtime_configuration import (
    ConfigurationField,
    RuntimeConfiguration,
    RuntimeConfigurationSchema,
)
from core.programmer.contracts.verification import (
    VerificationCheck,
    VerificationEvidence,
    VerificationSummary,
)
from core.programmer.types import (
    AcceptanceCriterionType,
    AcceptanceStatus,
    BuildPackagingStatus,
    DeploymentReadinessStatus,
    EngineeringRiskCategory,
    ProgrammerBlockerCategory,
    ProgrammerBlockerSeverity,
    VerificationCheckType,
    VerificationEvidenceSourceType,
    VerificationStatus,
    VerificationSummaryStatus,
)


def make_valid_revision(commit_hash: str = "a" * 40) -> GitRevision:
    return GitRevision(
        revision_id=new_git_revision_id(),
        commit_hash=commit_hash,
        branch="main",
    )


def make_valid_evidence(
    work_order_id: str = "pwo-test-01",
    is_agent_claim: bool = False,
    desc: str = "Integration test suite passed (120 tests)",
) -> VerificationEvidence:
    return VerificationEvidence(
        evidence_id=new_verification_evidence_id(),
        execution_id="pexec-test-01",
        work_order_id=work_order_id,
        is_agent_claim=is_agent_claim,
        description=desc,
        source_type=VerificationEvidenceSourceType.COMMAND_OUTPUT,
    )


def make_valid_artifact(
    artifact_reference: str = "/dist/app.tar.gz",
    with_schema: bool = True,
    build_exit_code: int = 0,
    is_agent_claim: bool = False,
) -> ProductArtifact:
    schema_dict = {}
    if with_schema:
        schema = RuntimeConfigurationSchema(
            product_id="prod-service",
            version="1.0.0",
            fields=[
                ConfigurationField(name="port", required=True, default=8080),
                ConfigurationField(name="env", default="production"),
            ],
        )
        schema_dict = schema.to_dict()

    return ProductArtifact(
        project_id="proj-service",
        work_order_id="pwo-test-01",
        execution_id="pexec-test-01",
        source_revision=make_valid_revision(),
        artifact_type=ProductArtifactType.PACKAGE,
        artifact_reference=artifact_reference,
        configuration_schema=schema_dict,
        environment_requirements={"env_vars": ["APP_PORT", "APP_ENV"]},
        evidence=[make_valid_evidence(is_agent_claim=is_agent_claim)],
        build_metadata={"exit_code": build_exit_code, "status": "SUCCESS"},
    )


def test_deployment_readiness_identifiers():
    """Verify ID generation and prefix validation."""
    rid = new_deployment_readiness_id()
    assert rid.startswith(DEPLOYMENT_READINESS_ID_PREFIX)
    validate_deployment_readiness_id(rid)

    with pytest.raises(Exception):
        validate_deployment_readiness_id("invalid-readiness-id")


def test_fully_ready_product():
    """Verify fully ready product with passing build, tests, and criteria produces READY."""
    evaluator = DeploymentReadinessEvaluator()
    artifact = make_valid_artifact(artifact_reference="/app/dist.zip")

    # Authoritative verification summary with passing checks
    summary = VerificationSummary(
        overall_status=VerificationSummaryStatus.VERIFIED,
        execution_id="pexec-test-01",
        work_order_id="pwo-test-01",
        checks=[
            VerificationCheck(
                check_id="vchk-01",
                check_type=VerificationCheckType.TEST,
                command="pytest test/unit",
                status=VerificationStatus.PASS,
            )
        ],
    )

    # Passing acceptance criteria results
    ac_results = [
        AcceptanceCriterionResult(
            criterion_id="ac-01",
            description="All API routes return 200",
            status=AcceptanceStatus.PASS,
        )
    ]

    result = evaluator.evaluate(
        artifact=artifact,
        verification_summary=summary,
        acceptance_results=ac_results,
        file_exists_fn=lambda path: True,
    )

    assert result.status == DeploymentReadinessStatus.READY
    assert result.is_ready is True
    assert result.has_blocking_issues is False
    assert len(result.blocking_issues) == 0
    assert len(result.warnings) == 0
    assert "port" in result.required_configuration
    assert "APP_PORT" in result.required_environment
    assert len(result.evidence) >= 1


def test_ready_with_warnings():
    """Verify non-blocking warnings (mitigated risk, optional criterion skipped) produce READY_WITH_WARNINGS."""
    evaluator = DeploymentReadinessEvaluator()
    artifact = make_valid_artifact()

    # Mitigated high-severity risk
    risk = EngineeringRisk(
        risk_id=new_engineering_risk_id(),
        category=EngineeringRiskCategory.DATABASE_MIGRATION,
        severity=RiskLevel.HIGH,
        description="Database column migration",
        affected_area="database/models",
        mitigation="Backward-compatible additive migration applied",
        escalation_required=False,
    )

    # Non-mandatory criterion skipped
    ac = AcceptanceCriterion(
        criterion_id="ac-opt",
        description="Optional stress test",
        is_mandatory=False,
    )
    ac_result = AcceptanceCriterionResult(
        criterion_id="ac-opt",
        description="Optional stress test",
        status=AcceptanceStatus.NOT_VERIFIED,
    )

    result = evaluator.evaluate(
        artifact=artifact,
        risks=[risk],
        acceptance_criteria=[ac],
        acceptance_results=[ac_result],
        file_exists_fn=lambda path: True,
    )

    assert result.status == DeploymentReadinessStatus.READY_WITH_WARNINGS
    assert result.is_ready is True
    assert result.has_blocking_issues is False
    assert len(result.blocking_issues) == 0
    assert len(result.warnings) >= 1


def test_failed_build_blocks_readiness():
    """Verify failed build metadata or BuildPackagingResult produces NOT_READY."""
    evaluator = DeploymentReadinessEvaluator()

    # Case A: artifact build_metadata has non-zero exit code
    artifact_failed_meta = make_valid_artifact(build_exit_code=1)
    result_a = evaluator.evaluate(
        artifact=artifact_failed_meta,
        file_exists_fn=lambda p: True,
    )
    assert result_a.status == DeploymentReadinessStatus.NOT_READY
    assert result_a.has_blocking_issues is True
    assert any("build" in issue.lower() for issue in result_a.blocking_issues)

    # Case B: explicit BuildPackagingResult failed
    artifact_ok = make_valid_artifact(build_exit_code=0)
    failed_build = BuildPackagingResult(
        success=False,
        status=BuildPackagingStatus.FAILED,
        exit_code=2,
        error_message="Compilation error: syntax error in main.go:45",
    )
    result_b = evaluator.evaluate(
        artifact=artifact_ok,
        build_result=failed_build,
        file_exists_fn=lambda p: True,
    )
    assert result_b.status == DeploymentReadinessStatus.NOT_READY
    assert any("Compilation error" in issue for issue in result_b.blocking_issues)


def test_failed_verification_checks_blocks_readiness():
    """Verify failing verification checks produce NOT_READY."""
    evaluator = DeploymentReadinessEvaluator()
    artifact = make_valid_artifact()

    summary = VerificationSummary(
        overall_status=VerificationSummaryStatus.FAILED,
        execution_id="pexec-test-01",
        work_order_id="pwo-test-01",
        checks=[
            VerificationCheck(
                check_id="vchk-unit",
                check_type=VerificationCheckType.TEST,
                command="pytest test/unit/test_db.py",
                status=VerificationStatus.FAIL,
            )
        ],
    )

    result = evaluator.evaluate(
        artifact=artifact,
        verification_summary=summary,
        file_exists_fn=lambda p: True,
    )

    assert result.status == DeploymentReadinessStatus.NOT_READY
    assert result.has_blocking_issues is True
    assert any("failed" in issue.lower() for issue in result.blocking_issues)


def test_missing_configuration_blocks_readiness():
    """Verify required configuration field without default or runtime value produces NOT_READY."""
    evaluator = DeploymentReadinessEvaluator()

    # Schema with required field that has NO default
    schema = RuntimeConfigurationSchema(
        product_id="prod-service",
        fields=[
            ConfigurationField(
                name="database_password",
                required=True,
                default=None,  # No default
            )
        ],
    )

    artifact = make_valid_artifact(with_schema=False)
    artifact.configuration_schema = schema.to_dict()

    # Evaluate without supplying a runtime configuration
    result = evaluator.evaluate(
        artifact=artifact,
        file_exists_fn=lambda p: True,
    )

    assert result.status == DeploymentReadinessStatus.NOT_READY
    assert any("database_password" in issue for issue in result.blocking_issues)

    # When valid runtime configuration provided, blocking issue clears
    runtime_config = RuntimeConfiguration(
        product_id="prod-service",
        values={"database_password": "secret_ref:db/app_password"},
    )
    result_with_cfg = evaluator.evaluate(
        artifact=artifact,
        runtime_configuration=runtime_config,
        file_exists_fn=lambda p: True,
    )
    assert not any("database_password" in issue for issue in result_with_cfg.blocking_issues)


def test_missing_artifact_blocks_readiness():
    """Verify non-existent artifact on disk produces NOT_READY."""
    evaluator = DeploymentReadinessEvaluator()
    artifact = make_valid_artifact(artifact_reference="/nonexistent/path/binary.bin")

    result = evaluator.evaluate(
        artifact=artifact,
        file_exists_fn=lambda path: False,  # File does not exist
    )

    assert result.status == DeploymentReadinessStatus.NOT_READY
    assert any("does not exist" in issue.lower() for issue in result.blocking_issues)


def test_unresolved_acceptance_criterion_blocks_readiness():
    """Verify failing mandatory acceptance criterion produces NOT_READY."""
    evaluator = DeploymentReadinessEvaluator()
    artifact = make_valid_artifact()

    ac_results = [
        AcceptanceCriterionResult(
            criterion_id="ac-mandatory",
            description="Mandatory security audit",
            status=AcceptanceStatus.FAIL,
            message="Found CVE in dependency",
        )
    ]

    result = evaluator.evaluate(
        artifact=artifact,
        acceptance_results=ac_results,
        file_exists_fn=lambda p: True,
    )

    assert result.status == DeploymentReadinessStatus.NOT_READY
    assert any("Mandatory security audit" in issue for issue in result.blocking_issues)


def test_known_unresolved_blocker_blocks_readiness():
    """Verify known unresolved blocker produces NOT_READY and is not auto-resolved."""
    evaluator = DeploymentReadinessEvaluator()
    artifact = make_valid_artifact()

    blocker = ProgrammerBlocker(
        blocker_id=new_blocker_id(),
        work_order_id="pwo-test-01",
        category=ProgrammerBlockerCategory.PERMISSION,
        severity=ProgrammerBlockerSeverity.HIGH,
        description="Missing production deployment key authorization",
    )

    result = evaluator.evaluate(
        artifact=artifact,
        blockers=[blocker],
        file_exists_fn=lambda p: True,
    )

    assert result.status == DeploymentReadinessStatus.NOT_READY
    assert blocker.is_resolved is False  # Never auto-resolved
    assert any("Missing production deployment key" in issue for issue in result.blocking_issues)


def test_unresolved_material_risk_blocks_readiness():
    """Verify unmitigated or escalation-required high risk produces NOT_READY."""
    evaluator = DeploymentReadinessEvaluator()
    artifact = make_valid_artifact()

    risk = EngineeringRisk(
        risk_id=new_engineering_risk_id(),
        category=EngineeringRiskCategory.DATA_LOSS,
        severity=RiskLevel.CRITICAL,
        description="High-volume concurrent write transaction race condition",
        affected_area="storage/engine",
        escalation_required=True,
        mitigation="",  # Unmitigated
    )

    result = evaluator.evaluate(
        artifact=artifact,
        risks=[risk],
        file_exists_fn=lambda p: True,
    )

    assert result.status == DeploymentReadinessStatus.NOT_READY
    assert any("race condition" in issue for issue in result.blocking_issues)


def test_unknown_state_when_insufficient_evidence():
    """Verify lack of authoritative verification evidence produces UNKNOWN."""
    evaluator = DeploymentReadinessEvaluator()

    # Artifact with empty evidence and no verification summary
    artifact = ProductArtifact(
        project_id="proj-01",
        work_order_id="pwo-test-01",
        execution_id="pexec-test-01",
        source_revision=make_valid_revision(),
        artifact_type=ProductArtifactType.PACKAGE,
        artifact_reference="/dist/app.tar.gz",
        evidence=[],  # No evidence
        build_metadata={"exit_code": 0, "status": "SUCCESS"},
    )

    result = evaluator.evaluate(
        artifact=artifact,
        file_exists_fn=lambda p: True,
    )

    assert result.status == DeploymentReadinessStatus.UNKNOWN
    assert result.is_unknown is True
    assert any("authoritative verification evidence" in w.lower() for w in result.warnings)


def test_cline_agent_claim_rejected_as_evidence():
    """Verify Cline agent claims are rejected as verification proof ('Do not treat Cline's claims as evidence')."""
    evaluator = DeploymentReadinessEvaluator()

    # Artifact containing only an agent claim
    agent_claim_evidence = make_valid_evidence(
        is_agent_claim=True,
        desc="Cline agent claims that all 50 tests passed in subshell",
    )

    artifact = ProductArtifact(
        project_id="proj-01",
        work_order_id="pwo-test-01",
        execution_id="pexec-test-01",
        source_revision=make_valid_revision(),
        artifact_type=ProductArtifactType.PACKAGE,
        artifact_reference="/dist/app.tar.gz",
        evidence=[agent_claim_evidence],
        build_metadata={"exit_code": 0, "status": "SUCCESS"},
    )

    result = evaluator.evaluate(
        artifact=artifact,
        file_exists_fn=lambda p: True,
    )

    # Since the only evidence was an agent claim, authoritative evidence is empty -> UNKNOWN
    assert result.status == DeploymentReadinessStatus.UNKNOWN
    assert len(result.evidence) == 0  # Agent claim excluded from authoritative evidence
    assert any("Ignored unverified agent claim" in w for w in result.warnings)


def test_serialization_roundtrips():
    """Verify JSON and dict serialization of DeploymentReadinessResult."""
    result = DeploymentReadinessResult(
        readiness_id=new_deployment_readiness_id(),
        artifact_id="part-test-01",
        project_id="proj-01",
        status=DeploymentReadinessStatus.READY_WITH_WARNINGS,
        blocking_issues=[],
        warnings=["Non-critical dependency deprecated"],
        required_configuration=["port", "db_host"],
        required_environment=["NODE_ENV"],
        evidence=[make_valid_evidence()],
    )

    # Dict roundtrip
    d = result.to_dict()
    rehydrated_d = DeploymentReadinessResult.from_dict(d)
    assert rehydrated_d.readiness_id == result.readiness_id
    assert rehydrated_d.status == DeploymentReadinessStatus.READY_WITH_WARNINGS
    assert rehydrated_d.warnings == ["Non-critical dependency deprecated"]
    assert len(rehydrated_d.evidence) == 1

    # JSON roundtrip
    j = result.to_json()
    rehydrated_j = DeploymentReadinessResult.from_json(j)
    assert rehydrated_j.readiness_id == result.readiness_id
    assert rehydrated_j.is_ready is True
