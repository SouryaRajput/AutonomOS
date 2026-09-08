from __future__ import annotations

import json
import pytest

from core.programmer.contracts.identifiers import (
    CONFIGURATION_ID_PREFIX,
    SCHEMA_ID_PREFIX,
    new_configuration_id,
    new_git_revision_id,
    new_schema_id,
    validate_configuration_id,
    validate_schema_id,
)
from core.programmer.contracts.runtime_configuration import (
    ConfigurationField,
    ConfigurationValidationRule,
    RuntimeConfiguration,
    RuntimeConfigurationSchema,
    ValidationReport,
    assert_safe_configuration_value,
    is_secret_reference,
)
from core.programmer.contracts.git_model import GitRevision
from core.programmer.contracts.product_artifact import (
    ProductArtifact,
    ProductArtifactType,
)
from core.programmer.errors import (
    ArtifactLineageError,
    ConfigurationError,
    ConfigurationValidationError,
    FieldDependencyViolationError,
    ImmutableConfigurationModificationError,
    InvalidConfigurationTypeError,
    InvalidConfigurationValueError,
    MaliciousConfigurationError,
    MissingRequiredConfigurationError,
    PlaintextSensitiveValueError,
    SchemaVersionMismatchError,
)
from core.programmer.types import (
    ConfigurationFieldType,
    ConfigurationValidationStatus,
)


def test_schema_and_configuration_identifiers():
    """Verify ID generation and prefix validation for schemas and configurations."""
    sid = new_schema_id()
    assert sid.startswith(SCHEMA_ID_PREFIX)
    validate_schema_id(sid)

    cid = new_configuration_id()
    assert cid.startswith(CONFIGURATION_ID_PREFIX)
    validate_configuration_id(cid)

    with pytest.raises(Exception):
        validate_schema_id("invalid-schema-id")

    with pytest.raises(Exception):
        validate_configuration_id("invalid-config-id")


def test_valid_configuration_and_defaults_population():
    """Verify valid configuration creation with schema defaults applied."""
    schema = RuntimeConfigurationSchema(
        product_id="prod-autonomos-backend",
        version="1.0.0",
        fields=[
            ConfigurationField(
                name="service_name",
                field_type=ConfigurationFieldType.STRING,
                required=True,
            ),
            ConfigurationField(
                name="port",
                field_type=ConfigurationFieldType.INTEGER,
                required=False,
                default=8080,
                min_value=1024,
                max_value=65535,
            ),
            ConfigurationField(
                name="log_level",
                field_type=ConfigurationFieldType.ENUM,
                required=False,
                default="INFO",
                allowed_values=["DEBUG", "INFO", "WARN", "ERROR"],
            ),
            ConfigurationField(
                name="features_enabled",
                field_type=ConfigurationFieldType.LIST,
                required=False,
                default=["metrics", "healthcheck"],
            ),
            ConfigurationField(
                name="debug_mode",
                field_type=ConfigurationFieldType.BOOLEAN,
                required=False,
                default=False,
            ),
        ],
    )

    assert "service_name" in schema.required_fields
    assert "port" in schema.defaults
    assert schema.defaults["port"] == 8080

    # Create configuration specifying only the required field
    config = schema.create_configuration(
        values={"service_name": "payment-service"}
    )

    assert config.configuration_id.startswith(CONFIGURATION_ID_PREFIX)
    assert config.product_id == "prod-autonomos-backend"
    assert config.schema_version == "1.0.0"
    assert config.validation_status == ConfigurationValidationStatus.VALID
    assert config.get("service_name") == "payment-service"
    assert config.get("port") == 8080
    assert config.get("log_level") == "INFO"
    assert config.get("debug_mode") is False
    assert config.get("features_enabled") == ["metrics", "healthcheck"]


def test_missing_required_field_raises():
    """Verify validation detects missing required configuration fields."""
    schema = RuntimeConfigurationSchema(
        product_id="prod-app",
        version="1.0.0",
        fields=[
            ConfigurationField(name="api_key_ref", required=True),
            ConfigurationField(name="app_env", required=True),
        ],
    )

    # 1. Raising on error
    with pytest.raises(MissingRequiredConfigurationError) as exc_info:
        schema.validate_configuration({"app_env": "production"}, raise_on_error=True)
    assert "api_key_ref" in str(exc_info.value)
    assert exc_info.value.field_name == "api_key_ref"

    # 2. Non-raising validation report
    report = schema.validate_configuration({"app_env": "production"}, raise_on_error=False)
    assert not report.is_valid
    assert "api_key_ref" in report.missing_fields
    assert len(report.errors) >= 1


def test_invalid_configuration_types():
    """Verify type checking catches type mismatches."""
    schema = RuntimeConfigurationSchema(
        product_id="prod-app",
        fields=[
            ConfigurationField(name="port", field_type=ConfigurationFieldType.INTEGER),
            ConfigurationField(name="is_active", field_type=ConfigurationFieldType.BOOLEAN),
            ConfigurationField(name="tags", field_type=ConfigurationFieldType.LIST),
            ConfigurationField(name="meta", field_type=ConfigurationFieldType.DICT),
            ConfigurationField(name="rate_limit", field_type=ConfigurationFieldType.FLOAT),
        ],
    )

    # String passed for INTEGER
    with pytest.raises(InvalidConfigurationTypeError) as exc:
        schema.validate_configuration({"port": "not-an-int"})
    assert exc.value.expected_type == "INTEGER"
    assert exc.value.field_name == "port"

    # Boolean passed for INTEGER (in Python, bool is an int subclass, but must be rejected)
    with pytest.raises(InvalidConfigurationTypeError) as exc:
        schema.validate_configuration({"port": True})
    assert exc.value.expected_type == "INTEGER"

    # Integer passed for BOOLEAN
    with pytest.raises(InvalidConfigurationTypeError) as exc:
        schema.validate_configuration({"is_active": 1})
    assert exc.value.expected_type == "BOOLEAN"

    # String passed for LIST
    with pytest.raises(InvalidConfigurationTypeError) as exc:
        schema.validate_configuration({"tags": "not-a-list"})
    assert exc.value.expected_type == "LIST"

    # List passed for DICT
    with pytest.raises(InvalidConfigurationTypeError) as exc:
        schema.validate_configuration({"meta": ["item"]})
    assert exc.value.expected_type == "DICT"


def test_invalid_configuration_value_constraints():
    """Verify constraint checks: min_value, max_value, min_length, max_length, pattern, enum."""
    schema = RuntimeConfigurationSchema(
        product_id="prod-app",
        fields=[
            ConfigurationField(
                name="concurrency",
                field_type=ConfigurationFieldType.INTEGER,
                min_value=1,
                max_value=100,
            ),
            ConfigurationField(
                name="cluster_name",
                field_type=ConfigurationFieldType.STRING,
                min_length=3,
                max_length=10,
                pattern=r"^[a-z0-9\-]+$",
            ),
            ConfigurationField(
                name="protocol",
                field_type=ConfigurationFieldType.ENUM,
                allowed_values=["HTTP", "HTTPS", "GRPC"],
            ),
        ],
    )

    # Below min_value
    with pytest.raises(InvalidConfigurationValueError) as exc:
        schema.validate_configuration({"concurrency": 0})
    assert "less than minimum" in str(exc.value)

    # Above max_value
    with pytest.raises(InvalidConfigurationValueError) as exc:
        schema.validate_configuration({"concurrency": 101})
    assert "exceeds maximum" in str(exc.value)

    # Shorter than min_length
    with pytest.raises(InvalidConfigurationValueError) as exc:
        schema.validate_configuration({"cluster_name": "ab"})
    assert "less than minimum length" in str(exc.value)

    # Longer than max_length
    with pytest.raises(InvalidConfigurationValueError) as exc:
        schema.validate_configuration({"cluster_name": "very-long-cluster-name"})
    assert "exceeds maximum length" in str(exc.value)

    # Pattern mismatch (uppercase not allowed)
    with pytest.raises(InvalidConfigurationValueError) as exc:
        schema.validate_configuration({"cluster_name": "Cluster-1"})
    assert "required pattern" in str(exc.value)

    # Disallowed enum value
    with pytest.raises(InvalidConfigurationValueError) as exc:
        schema.validate_configuration({"protocol": "FTP"})
    assert "not in allowed values" in str(exc.value)


def test_schema_version_mismatch():
    """Verify incompatible schema versions are detected and rejected."""
    schema = RuntimeConfigurationSchema(
        product_id="prod-app",
        version="2.0.0",
        fields=[ConfigurationField(name="timeout", default=30)],
    )

    config_v1 = RuntimeConfiguration(
        product_id="prod-app",
        schema_version="1.0.0",
        values={"timeout": 30},
    )

    with pytest.raises(SchemaVersionMismatchError) as exc:
        schema.validate_configuration(config_v1)
    assert exc.value.expected_version == "2.0.0"
    assert exc.value.actual_version == "1.0.0"


def test_sensitive_field_handling_and_masking():
    """Verify sensitive fields reject plaintext, accept valid placeholders, and mask safely."""
    schema = RuntimeConfigurationSchema(
        product_id="prod-app",
        fields=[
            ConfigurationField(
                name="db_password",
                field_type=ConfigurationFieldType.STRING,
                sensitive=True,
            ),
            ConfigurationField(
                name="api_token",
                field_type=ConfigurationFieldType.SECRET_REF,
            ),
            ConfigurationField(
                name="server_host",
                field_type=ConfigurationFieldType.STRING,
                sensitive=False,
            ),
        ],
    )

    # Plaintext in sensitive field rejected
    with pytest.raises(PlaintextSensitiveValueError) as exc:
        schema.validate_configuration({"db_password": "my_plaintext_password_123"})
    assert "db_password" in str(exc.value)

    # Plaintext in SECRET_REF rejected
    with pytest.raises(PlaintextSensitiveValueError) as exc:
        schema.validate_configuration({"api_token": "sk-1234567890abcdef"})
    assert "api_token" in str(exc.value)

    # Valid secret placeholders accepted
    valid_values = {
        "db_password": "secret_ref:databases/primary/password",
        "api_token": "env:EXTERNAL_API_TOKEN",
        "server_host": "db.internal.lan",
    }
    report = schema.validate_configuration(valid_values)
    assert report.is_valid

    # Vault and ${...} placeholders also accepted
    valid_values_2 = {
        "db_password": "vault:secret/data/db#password",
        "api_token": "${API_TOKEN_ENV}",
        "server_host": "db.internal.lan",
    }
    report_2 = schema.validate_configuration(valid_values_2)
    assert report_2.is_valid

    # Verify masking
    config = schema.create_configuration(values=valid_values)
    masked = config.masked_values(schema=schema)
    assert masked["db_password"] == "***"
    assert masked["api_token"] == "***"
    assert masked["server_host"] == "db.internal.lan"


def test_malicious_configuration_input_rejected():
    """Verify malicious payloads (command injection, eval, path traversal) are rejected."""
    schema = RuntimeConfigurationSchema(
        product_id="prod-app",
        fields=[ConfigurationField(name="data_dir", field_type=ConfigurationFieldType.STRING)],
    )

    # Command chaining / semicolon
    with pytest.raises(MaliciousConfigurationError) as exc:
        schema.validate_configuration({"data_dir": "/var/data; rm -rf /"})
    assert "Malicious configuration pattern" in str(exc.value)

    # Shell subshell $()
    with pytest.raises(MaliciousConfigurationError):
        schema.validate_configuration({"data_dir": "echo $(id)"})

    # Command substitution `...`
    with pytest.raises(MaliciousConfigurationError):
        schema.validate_configuration({"data_dir": "`cat /etc/passwd`"})

    # Dynamic code evaluation eval()
    with pytest.raises(MaliciousConfigurationError):
        schema.validate_configuration({"data_dir": "eval('import os')"})

    # Subprocess call
    with pytest.raises(MaliciousConfigurationError):
        schema.validate_configuration({"data_dir": "subprocess.run(['ls'])"})

    # Path traversal ../
    with pytest.raises(MaliciousConfigurationError):
        schema.validate_configuration({"data_dir": "../../../etc/shadow"})

    # Script tag
    with pytest.raises(MaliciousConfigurationError):
        schema.validate_configuration({"data_dir": "<script>alert(1)</script>"})

    # Direct callable/executable object
    with pytest.raises(MaliciousConfigurationError):
        schema.validate_configuration({"data_dir": lambda: "malicious_code"})


def test_field_dependency_and_mutual_exclusion_rules():
    """Verify conditional inter-field dependency rules and mutual exclusion."""
    schema = RuntimeConfigurationSchema(
        product_id="prod-db-service",
        fields=[
            ConfigurationField(name="storage_backend", allowed_values=["LOCAL", "S3", "GCS"]),
            ConfigurationField(name="s3_bucket_name", required=False),
            ConfigurationField(name="use_tls", field_type=ConfigurationFieldType.BOOLEAN),
            ConfigurationField(name="tls_cert_ref", required=False),
            ConfigurationField(name="insecure_no_auth", field_type=ConfigurationFieldType.BOOLEAN),
            ConfigurationField(name="auth_provider_ref", required=False),
        ],
        validation_rules=[
            # Conditional requires: if storage_backend == "S3", s3_bucket_name required
            ConfigurationValidationRule(
                rule_id="rule-s3-bucket",
                rule_type="REQUIRES_FIELD",
                source_field="storage_backend",
                target_field="s3_bucket_name",
                condition={"equals": "S3"},
                message="S3 storage requires s3_bucket_name.",
            ),
            # Requires field: if use_tls is True, tls_cert_ref required
            ConfigurationValidationRule(
                rule_id="rule-tls-cert",
                rule_type="REQUIRES_FIELD",
                source_field="use_tls",
                target_field="tls_cert_ref",
                condition={"equals": True},
                message="TLS enabled requires tls_cert_ref.",
            ),
            # Mutual exclusion: cannot specify both insecure_no_auth and auth_provider_ref
            ConfigurationValidationRule(
                rule_id="rule-auth-mut-excl",
                rule_type="MUTUALLY_EXCLUSIVE",
                source_field="insecure_no_auth",
                target_field="auth_provider_ref",
                message="Cannot enable insecure_no_auth while configuring auth_provider_ref.",
            ),
        ],
    )

    # Violate S3 requirement
    with pytest.raises(FieldDependencyViolationError) as exc:
        schema.validate_configuration({"storage_backend": "S3"})
    assert "s3_bucket_name" in str(exc.value)

    # Satisfy S3 requirement
    report = schema.validate_configuration({"storage_backend": "S3", "s3_bucket_name": "my-bucket"})
    assert report.is_valid

    # Violate TLS requirement
    with pytest.raises(FieldDependencyViolationError) as exc:
        schema.validate_configuration({"use_tls": True})
    assert "tls_cert_ref" in str(exc.value)

    # Violate mutual exclusion
    with pytest.raises(FieldDependencyViolationError) as exc:
        schema.validate_configuration({
            "insecure_no_auth": True,
            "auth_provider_ref": "secret_ref:auth/oidc",
        })
    assert "Cannot enable insecure_no_auth" in str(exc.value)


def test_immutability_and_updates():
    """Verify immutable fields cannot be modified post-creation."""
    schema = RuntimeConfigurationSchema(
        product_id="prod-app",
        fields=[
            ConfigurationField(
                name="cluster_id",
                field_type=ConfigurationFieldType.STRING,
                immutable=True,
            ),
            ConfigurationField(
                name="worker_count",
                field_type=ConfigurationFieldType.INTEGER,
                immutable=False,
            ),
        ],
    )

    config = schema.create_configuration(
        values={"cluster_id": "us-east-1a", "worker_count": 5}
    )

    # Updating mutable field succeeds
    updated = config.with_updates({"worker_count": 10}, schema=schema)
    assert updated.get("worker_count") == 10
    assert updated.get("cluster_id") == "us-east-1a"
    assert updated.updated_at >= config.created_at

    # Attempting to change immutable field raises
    with pytest.raises(ImmutableConfigurationModificationError) as exc:
        config.with_updates({"cluster_id": "eu-west-1a"}, schema=schema)
    assert exc.value.field_name == "cluster_id"


def test_lineage_and_product_id_consistency():
    """Verify configurations and schemas must share product_id."""
    schema = RuntimeConfigurationSchema(
        product_id="product-alpha",
        fields=[ConfigurationField(name="host", default="localhost")],
    )

    config_beta = RuntimeConfiguration(
        product_id="product-beta",
        schema_version="1.0.0",
        values={"host": "localhost"},
    )

    with pytest.raises(ArtifactLineageError) as exc:
        schema.validate_configuration(config_beta)
    assert "lineage mismatch" in str(exc.value)


def test_serialization_and_deserialization_roundtrips():
    """Verify JSON and dict serialization/deserialization."""
    schema = RuntimeConfigurationSchema(
        product_id="prod-core",
        version="1.2.0",
        fields=[
            ConfigurationField(name="database_url", required=True),
            ConfigurationField(name="pool_size", field_type=ConfigurationFieldType.INTEGER, default=10),
            ConfigurationField(name="secret_key", field_type=ConfigurationFieldType.SECRET_REF),
        ],
    )

    # Schema serialization roundtrip
    s_dict = schema.to_dict()
    schema_rehydrated = RuntimeConfigurationSchema.from_dict(s_dict)
    assert schema_rehydrated.schema_id == schema.schema_id
    assert schema_rehydrated.product_id == schema.product_id
    assert schema_rehydrated.version == "1.2.0"
    assert len(schema_rehydrated.fields) == 3

    s_json = schema.to_json()
    schema_from_json = RuntimeConfigurationSchema.from_json(s_json)
    assert schema_from_json.product_id == schema.product_id

    # Configuration serialization roundtrip
    config = schema.create_configuration(
        values={
            "database_url": "postgres://localhost:5432/mydb",
            "secret_key": "secret_ref:app/prod/master_key",
        }
    )

    c_dict = config.to_dict()
    config_rehydrated = RuntimeConfiguration.from_dict(c_dict)
    assert config_rehydrated.configuration_id == config.configuration_id
    assert config_rehydrated.product_id == config.product_id
    assert config_rehydrated.values["database_url"] == "postgres://localhost:5432/mydb"
    assert config_rehydrated.validation_status == ConfigurationValidationStatus.VALID

    c_json = config.to_json()
    config_from_json = RuntimeConfiguration.from_json(c_json)
    assert config_from_json.configuration_id == config.configuration_id

    # ProductArtifact integration
    artifact = ProductArtifact(
        project_id="proj-01",
        work_order_id="pwo-test-01",
        execution_id="pexec-test-01",
        source_revision=GitRevision(revision_id=new_git_revision_id(), commit_hash="a" * 40),
        artifact_type=ProductArtifactType.BUILD,
        artifact_reference="/dist/app.tar.gz",
        configuration_schema=schema.to_dict(),
    )
    assert artifact.configuration_schema["product_id"] == "prod-core"
    assert artifact.configuration_schema["version"] == "1.2.0"
