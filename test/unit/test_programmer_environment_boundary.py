"""
Unit tests for PROGRAMMER V1 — PHASE 9.5: Environment & Secret Boundary.

Validates:
1. Normal environment requirements (non-sensitive: PORT, API_BASE_URL).
2. Sensitive environment requirements (require secret reference, reject raw plaintext).
3. Missing secret reference in deployment readiness check -> NOT_READY.
4. Accidental secret logging and evidence detection (AWS keys, private keys, DB passwords, API keys).
5. Configuration leakage rejection in prompts, results, and evidence (SecretLeakageError).
6. Serialization without secret values (to_dict/to_json preserves refs, never leaks raw).
7. Missing required environment requirement in deployment readiness check -> NOT_READY.
8. Secret sanitization masks raw credentials.
9. EnvironmentRequirement validation rules (regex, numeric range).
10. Validates identifiers and prefix rules for environment requirements.
"""

import json
import pytest

from core.programmer.contracts.deployment_readiness import (
    DeploymentReadinessEvaluator,
    DeploymentReadinessResult,
)
from core.programmer.contracts.environment_boundary import (
    EnvironmentRequirement,
    SecretExposureDetector,
    SecretExposureFinding,
)
from core.programmer.contracts.identifiers import (
    ENVIRONMENT_REQUIREMENT_ID_PREFIX,
    new_environment_requirement_id,
    validate_environment_requirement_id,
)
from core.programmer.contracts.product_artifact import ProductArtifact
from core.programmer.contracts.runtime_configuration import RuntimeConfiguration
from core.programmer.contracts.verification import VerificationEvidence
from core.programmer.errors import (
    EnvironmentRequirementError,
    InvalidProgrammerIdError,
    SecretLeakageError,
)
from core.programmer.types import (
    DeploymentReadinessStatus,
    EnvironmentRequirementSource,
    EnvironmentRequirementType,
    ProductArtifactType,
    VerificationEvidenceSourceType,
)


def _make_dummy_artifact(
    artifact_id: str = "part-1234567890ab",
    project_id: str = "proj-test-1234",
    artifact_ref: str = "dist/app.tar.gz",
    commit_hash: str = "abcdef0123456789",
    evidence: list[VerificationEvidence] | None = None,
) -> ProductArtifact:
    """Helper to construct a valid ProductArtifact."""
    from core.programmer.contracts.git_model import GitRevision

    rev = GitRevision(
        revision_id="grev-test-12345678",
        commit_hash=commit_hash,
        branch="main",
    )
    return ProductArtifact(
        artifact_id=artifact_id,
        project_id=project_id,
        work_order_id="pwo-test-1234",
        execution_id="pexec-test-1234",
        source_revision=rev,
        artifact_type=ProductArtifactType.BUILD,
        artifact_reference=artifact_ref,
        version="1.0.0",
        evidence=list(evidence or []),
    )


def test_environment_requirement_identifiers():
    """Validates identifier prefix and validation helpers."""
    req_id = new_environment_requirement_id()
    assert req_id.startswith(ENVIRONMENT_REQUIREMENT_ID_PREFIX)
    validate_environment_requirement_id(req_id)  # Does not raise

    validate_environment_requirement_id("penv-12345678")
    with pytest.raises(InvalidProgrammerIdError):
        validate_environment_requirement_id("invalid-id")
    with pytest.raises(InvalidProgrammerIdError):
        validate_environment_requirement_id("")


def test_normal_environment_requirement():
    """Validates non-sensitive environment requirements (PORT, API_BASE_URL)."""
    port_req = EnvironmentRequirement(
        name="PORT",
        type=EnvironmentRequirementType.INTEGER,
        required=True,
        sensitive=False,
        description="Application listening port",
        validation_rules={"min": 1024, "max": 65535},
        source=EnvironmentRequirementSource.DECLARED,
    )
    assert port_req.name == "PORT"
    assert port_req.sensitive is False
    assert port_req.validate_value(8080) is None
    assert port_req.validate_value(80) is not None  # Below min 1024
    assert port_req.validate_value("invalid_int") is not None

    url_req = EnvironmentRequirement(
        name="API_BASE_URL",
        type=EnvironmentRequirementType.URL,
        required=True,
        sensitive=False,
        description="External API URL endpoint",
    )
    assert url_req.validate_value("https://api.example.com/v1") is None
    assert url_req.validate_value("not_a_valid_url") is not None


def test_sensitive_environment_requirement():
    """Validates sensitive requirements requiring secret reference placeholders."""
    secret_req = EnvironmentRequirement(
        name="SECRET_KEY",
        type=EnvironmentRequirementType.SECRET_REF,
        required=True,
        sensitive=True,
        description="Application signing secret",
        source=EnvironmentRequirementSource.MANIFEST,
    )
    assert secret_req.sensitive is True

    # Valid secret reference placeholders
    assert secret_req.validate_value("secret_ref:app_secret_key") is None
    assert secret_req.validate_value("env:SECRET_KEY") is None
    assert secret_req.validate_value("vault:secret/app#key") is None
    assert secret_req.validate_value("${SECRET_KEY}") is None
    assert secret_req.validate_value("[REDACTED_SECRET]") is None

    # Raw plaintext secret values must fail validation
    err = secret_req.validate_value("super_secret_raw_passphrase_12345")
    assert err is not None
    assert "must be a secret reference placeholder" in err


def test_missing_secret_reference_readiness_check():
    """Evaluates readiness when a required secret reference is missing/unavailable -> NOT_READY."""
    evaluator = DeploymentReadinessEvaluator()
    artifact = _make_dummy_artifact()

    # Required sensitive environment requirement
    req = EnvironmentRequirement(
        name="DATABASE_URL",
        type=EnvironmentRequirementType.SECRET_REF,
        required=True,
        sensitive=True,
        description="Production database secret connection string",
    )

    # Secret reference 'secret_ref:DATABASE_URL' is NOT available in available_secret_refs
    res = evaluator.evaluate(
        artifact=artifact,
        environment_requirements=[req],
        available_secret_refs={"secret_ref:OTHER_KEY"},
        file_exists_fn=lambda _: True,
    )
    assert res.status == DeploymentReadinessStatus.NOT_READY
    assert any("Secret reference unavailable" in b and "DATABASE_URL" in b for b in res.blocking_issues)


def test_secret_reference_available_readiness_check():
    """Evaluates readiness when a required secret reference is available -> READY (with evidence)."""
    evaluator = DeploymentReadinessEvaluator()
    evidence = VerificationEvidence(
        evidence_id="vevid-test-12345678",
        execution_id="pexec-test-1234",
        work_order_id="pwo-test-1234",
        source_type=VerificationEvidenceSourceType.COMMAND_OUTPUT,
        description="Test suite passed 100%",
        is_agent_claim=False,
    )
    artifact = _make_dummy_artifact(evidence=[evidence])

    req = EnvironmentRequirement(
        name="DATABASE_URL",
        type=EnvironmentRequirementType.SECRET_REF,
        required=True,
        sensitive=True,
        description="Production database secret connection string",
    )

    # Secret reference 'secret_ref:DATABASE_URL' IS available
    res = evaluator.evaluate(
        artifact=artifact,
        environment_requirements=[req],
        available_secret_refs={"secret_ref:DATABASE_URL"},
        file_exists_fn=lambda _: True,
    )
    assert res.status == DeploymentReadinessStatus.READY
    assert "DATABASE_URL" in res.required_environment


def test_accidental_secret_logging_detection():
    """Detects raw private keys, API keys, and passwords in text."""
    detector = SecretExposureDetector()

    # Private key
    rsa_key = "-----BEGIN RSA PRIVATE KEY-----\nMIIEowIBAAKCAQEA0...\n-----END RSA PRIVATE KEY-----"
    findings = detector.scan_text(rsa_key, location="build.log")
    assert len(findings) > 0
    assert findings[0].secret_type == "PRIVATE_KEY"

    # AWS Key
    aws_key = "Access credentials: AKIAIOSFODNN7EXAMPLE"
    findings = detector.scan_text(aws_key, location="stdout")
    assert len(findings) > 0
    assert findings[0].secret_type == "AWS_KEY"

    # GitHub PAT
    gh_token = "Deploy token: ghp_1234567890abcdefghijklmnopqrstuvwxyzAB"
    findings = detector.scan_text(gh_token, location="config")
    assert len(findings) > 0
    assert findings[0].secret_type == "GITHUB_TOKEN"

    # DB URI with password
    db_uri = "postgres://admin:supersecretpassword123@db.prod.internal:5432/main"
    findings = detector.scan_text(db_uri, location="evidence")
    assert len(findings) > 0
    assert findings[0].secret_type == "DATABASE_PASSWORD_URI"

    # Clean text with authorized placeholder must NOT trigger
    clean_text = "DATABASE_URL=secret_ref:DATABASE_URL and port=8080"
    assert len(detector.scan_text(clean_text)) == 0


def test_configuration_leakage_rejection():
    """Validates that prompts, results, and evidence containing raw secrets raise SecretLeakageError."""
    detector = SecretExposureDetector()

    # Prompt safety
    dirty_prompt = "Here is the production key: AKIAIOSFODNN7EXAMPLE. Please refactor DB."
    with pytest.raises(SecretLeakageError) as exc_info:
        detector.validate_prompt_safety(dirty_prompt)
    assert exc_info.value.secret_type == "AWS_KEY"

    # Clean prompt passes
    detector.validate_prompt_safety("Use the environment variable defined by secret_ref:DB_KEY.")

    # Result safety
    dirty_result = {
        "summary": "Task complete",
        "output": "-----BEGIN RSA PRIVATE KEY-----\nkey\n-----END RSA PRIVATE KEY-----",
    }
    with pytest.raises(SecretLeakageError) as exc_info:
        detector.validate_result_safety(dirty_result)
    assert exc_info.value.secret_type == "PRIVATE_KEY"

    # Evidence safety
    dirty_evidence = VerificationEvidence(
        evidence_id="vevid-test-12345678",
        execution_id="pexec-test-1234",
        work_order_id="pwo-test-1234",
        source_type=VerificationEvidenceSourceType.COMMAND_OUTPUT,
        description="Ran curl with token ghp_1234567890abcdefghijklmnopqrstuvwxyzAB",
    )
    with pytest.raises(SecretLeakageError) as exc_info:
        detector.validate_evidence_safety(dirty_evidence)
    assert exc_info.value.secret_type == "GITHUB_TOKEN"


def test_serialization_without_secret_values():
    """Ensures serialization of EnvironmentRequirement preserves references and never contains raw secrets."""
    req = EnvironmentRequirement(
        name="OAUTH_CLIENT_SECRET",
        type=EnvironmentRequirementType.SECRET_REF,
        required=True,
        sensitive=True,
        description="OAuth 2.0 client secret",
        source=EnvironmentRequirementSource.DECLARED,
    )

    d = req.to_dict()
    assert d["name"] == "OAUTH_CLIENT_SECRET"
    assert d["sensitive"] is True
    assert d["type"] == "SECRET_REF"
    assert "secret_value" not in d  # No secret values stored

    # JSON serialization
    json_str = req.to_json()
    assert "OAUTH_CLIENT_SECRET" in json_str

    # Deserialization round-trip
    req2 = EnvironmentRequirement.from_json(json_str)
    assert req2.name == req.name
    assert req2.type == req.type
    assert req2.sensitive == req.sensitive


def test_missing_required_environment_requirement_readiness_check():
    """Readiness evaluation flags missing mandatory environment variables as blocking issues."""
    evaluator = DeploymentReadinessEvaluator()
    artifact = _make_dummy_artifact()

    req = EnvironmentRequirement(
        name="MANDATORY_TENANT_ID",
        type=EnvironmentRequirementType.STRING,
        required=True,
        sensitive=False,
        description="Required tenant identifier",
    )

    # No configuration providing MANDATORY_TENANT_ID
    res = evaluator.evaluate(
        artifact=artifact,
        environment_requirements=[req],
        file_exists_fn=lambda _: True,
    )
    assert res.status == DeploymentReadinessStatus.NOT_READY
    assert any("Missing required environment requirement: 'MANDATORY_TENANT_ID'" in b for b in res.blocking_issues)


def test_sanitization_masks_credentials():
    """Confirms SecretExposureDetector.sanitize_text redacts credentials safely."""
    detector = SecretExposureDetector()
    raw = "Deploy failed using AKIAIOSFODNN7EXAMPLE and token ghp_1234567890abcdefghijklmnopqrstuvwxyzAB on host."
    sanitized = detector.sanitize_text(raw)
    assert "AKIAIOSFODNN7EXAMPLE" not in sanitized
    assert "ghp_1234567890" not in sanitized
    assert "[REDACTED_SECRET]" in sanitized
    assert "Deploy failed using" in sanitized


def test_deployment_readiness_secret_exposure_in_evidence_blocks():
    """Evaluation flags accidental secret exposure inside evidence as a blocking issue."""
    evaluator = DeploymentReadinessEvaluator()
    dirty_evidence = VerificationEvidence(
        evidence_id="vevid-test-12345678",
        execution_id="pexec-test-1234",
        work_order_id="pwo-test-1234",
        source_type=VerificationEvidenceSourceType.COMMAND_OUTPUT,
        description="curl https://api.com -H 'Authorization: Bearer ghp_1234567890abcdefghijklmnopqrstuvwxyzAB'",
        is_agent_claim=False,
    )
    artifact = _make_dummy_artifact(evidence=[dirty_evidence])

    res = evaluator.evaluate(
        artifact=artifact,
        file_exists_fn=lambda _: True,
    )
    assert res.status == DeploymentReadinessStatus.NOT_READY
    assert any("Accidental secret exposure detected in evidence" in b for b in res.blocking_issues)


def test_deployment_readiness_plaintext_sensitive_value_blocks():
    """Evaluation flags sensitive requirement with raw plaintext value in runtime config."""
    evaluator = DeploymentReadinessEvaluator()
    evidence = VerificationEvidence(
        evidence_id="vevid-test-12345678",
        execution_id="pexec-test-1234",
        work_order_id="pwo-test-1234",
        source_type=VerificationEvidenceSourceType.COMMAND_OUTPUT,
        description="Clean verified test evidence",
        is_agent_claim=False,
    )
    artifact = _make_dummy_artifact(evidence=[evidence])

    req = EnvironmentRequirement(
        name="API_SECRET",
        type=EnvironmentRequirementType.SECRET_REF,
        required=True,
        sensitive=True,
        description="Production API secret",
    )

    # Runtime configuration with raw plaintext secret instead of placeholder
    config = RuntimeConfiguration(
        values={"API_SECRET": "raw_plaintext_password_12345"}
    )

    res = evaluator.evaluate(
        artifact=artifact,
        environment_requirements=[req],
        runtime_configuration=config,
        file_exists_fn=lambda _: True,
    )
    assert res.status == DeploymentReadinessStatus.NOT_READY
    assert any("cannot contain plaintext raw secret" in b for b in res.blocking_issues)


def test_git_commit_and_diff_safety():
    """Validates that secrets in commit messages or diffs trigger SecretLeakageError."""
    detector = SecretExposureDetector()

    # Commit message with secret
    with pytest.raises(SecretLeakageError) as exc:
        detector.validate_git_safety("Fix bug with key AKIAIOSFODNN7EXAMPLE")
    assert "GitCommitMessage" in str(exc.value)

    # Clean commit message passes
    detector.validate_git_safety("Fix bug using secret_ref:MY_KEY")

    # Diff with secret
    diff = "+ DB_PASS = 'supersecretpassword123'\n+ host = 'localhost'"
    # Plain assignment of sensitive key
    with pytest.raises(SecretLeakageError) as exc:
        detector.validate_git_safety("Clean commit", diff_text="+ api_key = \"AKIAIOSFODNN7EXAMPLE\"")
    assert "GitDiff" in str(exc.value)


def test_secret_resolver_callable_integration():
    """Evaluates readiness using custom secret_resolver callable."""
    evaluator = DeploymentReadinessEvaluator()
    evidence = VerificationEvidence(
        evidence_id="vevid-test-12345678",
        execution_id="pexec-test-1234",
        work_order_id="pwo-test-1234",
        source_type=VerificationEvidenceSourceType.COMMAND_OUTPUT,
        description="Clean verified test evidence",
        is_agent_claim=False,
    )
    artifact = _make_dummy_artifact(evidence=[evidence])

    req = EnvironmentRequirement(
        name="VAULT_KEY",
        type=EnvironmentRequirementType.SECRET_REF,
        required=True,
        sensitive=True,
    )

    # Resolver that knows VAULT_KEY exists
    def mock_resolver(ref: str) -> bool:
        return "VAULT_KEY" in ref

    res = evaluator.evaluate(
        artifact=artifact,
        environment_requirements=[req],
        secret_resolver=mock_resolver,
        file_exists_fn=lambda _: True,
    )
    assert res.status == DeploymentReadinessStatus.READY
