from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import json
import re
from typing import Any, Optional, Sequence

from core.programmer.contracts.identifiers import (
    new_configuration_id,
    new_schema_id,
    validate_configuration_id,
    validate_schema_id,
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


def utc_now() -> str:
    """Generate ISO 8601 UTC timestamp."""
    return datetime.now(timezone.utc).isoformat()


# Secret reference patterns for sensitive configuration fields
_SECRET_REF_PATTERNS = [
    re.compile(r"^secret_ref:[a-zA-Z0-9_\-\./]+$"),
    re.compile(r"^env:[A-Za-z0-9_]+$"),
    re.compile(r"^vault:[a-zA-Z0-9_\-\./#]+$"),
    re.compile(r"^\$\{[A-Za-z0-9_]+\}$"),
]

# Malicious patterns: command injection, shell metacharacters, code evaluation, path traversal
_MALICIOUS_PATTERNS: list[tuple[re.Pattern, str]] = [
    (re.compile(r";\s*[a-zA-Z_/]"), "command separator ';' followed by command"),
    (re.compile(r"&&"), "command chaining '&&'"),
    (re.compile(r"\|\|"), "command chaining '||'"),
    (re.compile(r"\|\s*[a-zA-Z]"), "pipe operator '|'"),
    (re.compile(r"\$\([^\)]*\)"), "subshell command execution '$()'"),
    (re.compile(r"`[^`]+`"), "backtick command substitution '`'"),
    (re.compile(r"\b(eval|exec)\s*\("), "dynamic code evaluation 'eval()/exec()'"),
    (re.compile(r"\b(os\.system|subprocess\.)"), "system execution call"),
    (re.compile(r"<script[\s>]", re.IGNORECASE), "embedded script tag '<script>'"),
    (re.compile(r"\.\./|\.\.\\"), "path traversal sequence '../'"),
    (re.compile(r"\brm\s+-rf\b"), "destructive file system command 'rm -rf'"),
]


def is_secret_reference(value: Any) -> bool:
    """Check if a string represents an authorized secret reference placeholder."""
    if not isinstance(value, str):
        return False
    val = value.strip()
    return any(pattern.match(val) for pattern in _SECRET_REF_PATTERNS)


def assert_safe_configuration_value(key: str, value: Any) -> None:
    """
    Ensure configuration value contains no malicious code or command injection payloads.
    Configuration is data; it must never become arbitrary executable code.
    """
    if isinstance(value, str):
        # If it is an authorized secret reference, verify it doesn't contain injection payloads
        for pattern, desc in _MALICIOUS_PATTERNS:
            if pattern.search(value):
                raise MaliciousConfigurationError(
                    f"Malicious configuration pattern detected in field '{key}': {desc}",
                    field_name=key,
                    detected_pattern=desc,
                )
    elif isinstance(value, list):
        for idx, item in enumerate(value):
            assert_safe_configuration_value(f"{key}[{idx}]", item)
    elif isinstance(value, dict):
        for sub_key, sub_val in value.items():
            assert_safe_configuration_value(f"{key}.{sub_key}", sub_val)
    elif callable(value):
        raise MaliciousConfigurationError(
            f"Configuration field '{key}' must be pure data, but received callable/executable code.",
            field_name=key,
            detected_pattern="callable/executable code",
        )


@dataclass
class ConfigurationValidationRule:
    """
    Inter-field validation rule or conditional requirement.
    """
    rule_id: str
    rule_type: str  # e.g., "REQUIRES_FIELD", "MUTUALLY_EXCLUSIVE", "CONDITIONAL_VALUE"
    source_field: str
    target_field: Optional[str] = None
    condition: Optional[dict[str, Any]] = None
    message: str = ""

    def evaluate(self, values: dict[str, Any]) -> None:
        """Evaluate the rule against configuration values; raise if violated."""
        s_val = values.get(self.source_field)

        if self.rule_type == "REQUIRES_FIELD":
            # If source_field is present/truthy (or matches condition), target_field must be present
            applies = True
            if self.condition and "equals" in self.condition:
                applies = (s_val == self.condition["equals"])
            elif s_val is None:
                applies = False

            if applies:
                if self.target_field not in values or values.get(self.target_field) is None:
                    msg = self.message or f"Field '{self.source_field}' requires '{self.target_field}' to be configured."
                    raise FieldDependencyViolationError(
                        msg,
                        source_field=self.source_field,
                        target_field=self.target_field,
                    )

        elif self.rule_type == "MUTUALLY_EXCLUSIVE":
            if self.source_field in values and values[self.source_field] is not None:
                if self.target_field and self.target_field in values and values[self.target_field] is not None:
                    msg = self.message or f"Fields '{self.source_field}' and '{self.target_field}' are mutually exclusive."
                    raise FieldDependencyViolationError(
                        msg,
                        source_field=self.source_field,
                        target_field=self.target_field,
                    )

    def to_dict(self) -> dict[str, Any]:
        return {
            "rule_id": self.rule_id,
            "rule_type": self.rule_type,
            "source_field": self.source_field,
            "target_field": self.target_field,
            "condition": self.condition,
            "message": self.message,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ConfigurationValidationRule:
        return cls(
            rule_id=str(data.get("rule_id", "")),
            rule_type=str(data.get("rule_type", "")),
            source_field=str(data.get("source_field", "")),
            target_field=data.get("target_field"),
            condition=data.get("condition"),
            message=str(data.get("message", "")),
        )


@dataclass
class ConfigurationField:
    """
    Specification and constraints for an individual configuration field.
    """
    name: str
    field_type: ConfigurationFieldType = ConfigurationFieldType.STRING
    required: bool = False
    default: Any = None
    sensitive: bool = False
    immutable: bool = False
    allowed_values: Optional[list[Any]] = None
    min_value: Optional[float] = None
    max_value: Optional[float] = None
    min_length: Optional[int] = None
    max_length: Optional[int] = None
    pattern: Optional[str] = None
    description: str = ""

    def __post_init__(self) -> None:
        if isinstance(self.field_type, str):
            try:
                self.field_type = ConfigurationFieldType(self.field_type.upper())
            except (ValueError, TypeError):
                self.field_type = ConfigurationFieldType.STRING

        # If field_type is SECRET_REF, sensitive is automatically True
        if self.field_type == ConfigurationFieldType.SECRET_REF:
            self.sensitive = True

    def validate_value(self, value: Any) -> None:
        """Validate a single value against field specifications and constraints."""
        if value is None:
            if self.required:
                raise MissingRequiredConfigurationError(
                    f"Required configuration field '{self.name}' is missing or None.",
                    field_name=self.name,
                )
            return

        # 1. Type validation
        if self.field_type == ConfigurationFieldType.STRING:
            if not isinstance(value, str):
                raise InvalidConfigurationTypeError(
                    f"Field '{self.name}' expected STRING, got {type(value).__name__}.",
                    field_name=self.name,
                    expected_type="STRING",
                    actual_type=type(value).__name__,
                )
        elif self.field_type == ConfigurationFieldType.INTEGER:
            # Note: in Python bool is subclass of int, so exclude bool
            if not isinstance(value, int) or isinstance(value, bool):
                raise InvalidConfigurationTypeError(
                    f"Field '{self.name}' expected INTEGER, got {type(value).__name__}.",
                    field_name=self.name,
                    expected_type="INTEGER",
                    actual_type=type(value).__name__,
                )
        elif self.field_type == ConfigurationFieldType.FLOAT:
            if (not isinstance(value, (int, float))) or isinstance(value, bool):
                raise InvalidConfigurationTypeError(
                    f"Field '{self.name}' expected FLOAT, got {type(value).__name__}.",
                    field_name=self.name,
                    expected_type="FLOAT",
                    actual_type=type(value).__name__,
                )
        elif self.field_type == ConfigurationFieldType.BOOLEAN:
            if not isinstance(value, bool):
                raise InvalidConfigurationTypeError(
                    f"Field '{self.name}' expected BOOLEAN, got {type(value).__name__}.",
                    field_name=self.name,
                    expected_type="BOOLEAN",
                    actual_type=type(value).__name__,
                )
        elif self.field_type == ConfigurationFieldType.LIST:
            if not isinstance(value, list):
                raise InvalidConfigurationTypeError(
                    f"Field '{self.name}' expected LIST, got {type(value).__name__}.",
                    field_name=self.name,
                    expected_type="LIST",
                    actual_type=type(value).__name__,
                )
        elif self.field_type == ConfigurationFieldType.DICT:
            if not isinstance(value, dict):
                raise InvalidConfigurationTypeError(
                    f"Field '{self.name}' expected DICT, got {type(value).__name__}.",
                    field_name=self.name,
                    expected_type="DICT",
                    actual_type=type(value).__name__,
                )
        elif self.field_type == ConfigurationFieldType.ENUM:
            if self.allowed_values is not None and value not in self.allowed_values:
                raise InvalidConfigurationValueError(
                    f"Field '{self.name}' value '{value}' not in allowed values: {self.allowed_values}.",
                    field_name=self.name,
                    value=value,
                    constraint=f"allowed_values={self.allowed_values}",
                )
        elif self.field_type == ConfigurationFieldType.SECRET_REF:
            if not is_secret_reference(value):
                raise PlaintextSensitiveValueError(
                    f"Field '{self.name}' must be a secret reference placeholder ('secret_ref:...', 'env:...', '${{...}}'), but received plaintext value.",
                    field_name=self.name,
                )

        # 2. Sensitive handling (applies to all types if sensitive is True)
        if self.sensitive:
            if not is_secret_reference(value):
                raise PlaintextSensitiveValueError(
                    f"Sensitive field '{self.name}' must contain a secret reference placeholder ('secret_ref:...', 'env:...', '${{...}}'), but received plaintext.",
                    field_name=self.name,
                )

        # 3. Numeric bounds
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            if self.min_value is not None and value < self.min_value:
                raise InvalidConfigurationValueError(
                    f"Field '{self.name}' value {value} is less than minimum {self.min_value}.",
                    field_name=self.name,
                    value=value,
                    constraint=f"min_value={self.min_value}",
                )
            if self.max_value is not None and value > self.max_value:
                raise InvalidConfigurationValueError(
                    f"Field '{self.name}' value {value} exceeds maximum {self.max_value}.",
                    field_name=self.name,
                    value=value,
                    constraint=f"max_value={self.max_value}",
                )

        # 4. Length constraints
        if isinstance(value, (str, list)):
            if self.min_length is not None and len(value) < self.min_length:
                raise InvalidConfigurationValueError(
                    f"Field '{self.name}' length {len(value)} is less than minimum length {self.min_length}.",
                    field_name=self.name,
                    value=value,
                    constraint=f"min_length={self.min_length}",
                )
            if self.max_length is not None and len(value) > self.max_length:
                raise InvalidConfigurationValueError(
                    f"Field '{self.name}' length {len(value)} exceeds maximum length {self.max_length}.",
                    field_name=self.name,
                    value=value,
                    constraint=f"max_length={self.max_length}",
                )

        # 5. Pattern constraint
        if isinstance(value, str) and self.pattern:
            if not re.search(self.pattern, value):
                raise InvalidConfigurationValueError(
                    f"Field '{self.name}' value does not match required pattern '{self.pattern}'.",
                    field_name=self.name,
                    value=value,
                    constraint=f"pattern={self.pattern}",
                )

        # 6. Enum allowed values constraint (if allowed_values set for any type)
        if self.allowed_values is not None and value not in self.allowed_values:
            raise InvalidConfigurationValueError(
                f"Field '{self.name}' value '{value}' not in allowed values: {self.allowed_values}.",
                field_name=self.name,
                value=value,
                constraint=f"allowed_values={self.allowed_values}",
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "field_type": self.field_type.value,
            "required": self.required,
            "default": self.default,
            "sensitive": self.sensitive,
            "immutable": self.immutable,
            "allowed_values": self.allowed_values,
            "min_value": self.min_value,
            "max_value": self.max_value,
            "min_length": self.min_length,
            "max_length": self.max_length,
            "pattern": self.pattern,
            "description": self.description,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ConfigurationField:
        ft = data.get("field_type", ConfigurationFieldType.STRING)
        if isinstance(ft, str):
            try:
                ft = ConfigurationFieldType(ft.upper())
            except (ValueError, TypeError):
                ft = ConfigurationFieldType.STRING

        return cls(
            name=str(data.get("name", "")),
            field_type=ft,
            required=bool(data.get("required", False)),
            default=data.get("default"),
            sensitive=bool(data.get("sensitive", False)),
            immutable=bool(data.get("immutable", False)),
            allowed_values=data.get("allowed_values"),
            min_value=data.get("min_value"),
            max_value=data.get("max_value"),
            min_length=data.get("min_length"),
            max_length=data.get("max_length"),
            pattern=data.get("pattern"),
            description=str(data.get("description", "")),
        )


@dataclass
class ValidationReport:
    """Outcome of validating a configuration against a schema."""
    is_valid: bool
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    missing_fields: list[str] = field(default_factory=list)
    invalid_fields: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "is_valid": self.is_valid,
            "errors": list(self.errors),
            "warnings": list(self.warnings),
            "missing_fields": list(self.missing_fields),
            "invalid_fields": list(self.invalid_fields),
        }


@dataclass
class RuntimeConfigurationSchema:
    """
    Safe, structured schema defining expected configuration parameters, constraints,
    types, and sensitivity bounds for a product.
    """
    schema_id: str = field(default_factory=new_schema_id)
    product_id: str = ""
    version: str = "1.0.0"
    fields: list[ConfigurationField] = field(default_factory=list)
    required_fields: list[str] = field(default_factory=list)
    optional_fields: list[str] = field(default_factory=list)
    defaults: dict[str, Any] = field(default_factory=dict)
    validation_rules: list[ConfigurationValidationRule] = field(default_factory=list)
    sensitive_fields: list[str] = field(default_factory=list)
    immutable_fields: list[str] = field(default_factory=list)
    trace: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.schema_id:
            self.schema_id = new_schema_id()
        else:
            validate_schema_id(self.schema_id)

        # Normalize fields
        normalized_fields: list[ConfigurationField] = []
        for f in self.fields:
            if isinstance(f, dict):
                normalized_fields.append(ConfigurationField.from_dict(f))
            else:
                normalized_fields.append(f)
        self.fields = normalized_fields

        # Normalize validation_rules
        normalized_rules: list[ConfigurationValidationRule] = []
        for r in self.validation_rules:
            if isinstance(r, dict):
                normalized_rules.append(ConfigurationValidationRule.from_dict(r))
            else:
                normalized_rules.append(r)
        self.validation_rules = normalized_rules

        # Populate / merge derived lists if not explicitly provided
        field_map = {f.name: f for f in self.fields}

        # Required fields
        req_from_fields = [f.name for f in self.fields if f.required]
        if not self.required_fields:
            self.required_fields = req_from_fields
        else:
            self.required_fields = list(dict.fromkeys(self.required_fields + req_from_fields))

        # Optional fields
        opt_from_fields = [f.name for f in self.fields if not f.required]
        if not self.optional_fields:
            self.optional_fields = opt_from_fields
        else:
            self.optional_fields = list(dict.fromkeys(self.optional_fields + opt_from_fields))

        # Defaults
        for f in self.fields:
            if f.default is not None and f.name not in self.defaults:
                self.defaults[f.name] = f.default

        # Sensitive fields
        sens_from_fields = [
            f.name for f in self.fields
            if f.sensitive or f.field_type == ConfigurationFieldType.SECRET_REF
        ]
        if not self.sensitive_fields:
            self.sensitive_fields = sens_from_fields
        else:
            self.sensitive_fields = list(dict.fromkeys(self.sensitive_fields + sens_from_fields))

        # Immutable fields
        imm_from_fields = [f.name for f in self.fields if f.immutable]
        if not self.immutable_fields:
            self.immutable_fields = imm_from_fields
        else:
            self.immutable_fields = list(dict.fromkeys(self.immutable_fields + imm_from_fields))

    def get_field(self, name: str) -> Optional[ConfigurationField]:
        """Lookup a field definition by name."""
        for f in self.fields:
            if f.name == name:
                return f
        return None

    def validate_configuration(
        self,
        configuration: Any,
        raise_on_error: bool = True,
    ) -> ValidationReport:
        """
        Validate a RuntimeConfiguration instance or dictionary of configuration values
        against this schema.
        """
        errors: list[str] = []
        warnings: list[str] = []
        missing_fields: list[str] = []
        invalid_fields: list[str] = []

        # Check instance attributes if it's a RuntimeConfiguration
        if isinstance(configuration, RuntimeConfiguration):
            # Lineage check: product_id must match if both are specified
            if self.product_id and configuration.product_id and self.product_id != configuration.product_id:
                err = f"Product ID lineage mismatch: schema product '{self.product_id}' != config product '{configuration.product_id}'."
                if raise_on_error:
                    raise ArtifactLineageError(err)
                errors.append(err)

            # Schema version compatibility check
            if configuration.schema_version != self.version:
                # Compare major versions
                s_major = self.version.split(".")[0]
                c_major = configuration.schema_version.split(".")[0]
                if s_major != c_major:
                    err = f"Schema version mismatch: schema is '{self.version}', configuration requires '{configuration.schema_version}'."
                    if raise_on_error:
                        raise SchemaVersionMismatchError(
                            err,
                            expected_version=self.version,
                            actual_version=configuration.schema_version,
                        )
                    errors.append(err)

            values = dict(configuration.values)
        elif isinstance(configuration, dict):
            values = dict(configuration)
        else:
            err = f"Configuration must be RuntimeConfiguration or dict, got {type(configuration).__name__}."
            if raise_on_error:
                raise InvalidConfigurationTypeError(err)
            return ValidationReport(is_valid=False, errors=[err])

        # 1. Malicious input check (pure data invariant)
        for k, v in values.items():
            try:
                assert_safe_configuration_value(k, v)
            except MaliciousConfigurationError as e:
                if raise_on_error:
                    raise
                errors.append(str(e))
                invalid_fields.append(k)

        # 2. Check required fields
        for req in self.required_fields:
            if req not in values or values[req] is None:
                # Check if schema default exists
                if req not in self.defaults or self.defaults[req] is None:
                    err = f"Required configuration field '{req}' is missing."
                    if raise_on_error:
                        raise MissingRequiredConfigurationError(err, field_name=req)
                    errors.append(err)
                    missing_fields.append(req)

        # 3. Check field definitions and constraints
        for k, v in values.items():
            f_def = self.get_field(k)
            if f_def is not None:
                try:
                    f_def.validate_value(v)
                except ConfigurationValidationError as e:
                    if raise_on_error:
                        raise
                    errors.append(str(e))
                    invalid_fields.append(k)
            else:
                # Field not in schema
                warnings.append(f"Field '{k}' is not defined in schema '{self.schema_id}'.")
                # Even if not in schema, if field name sounds sensitive, verify it's not raw plaintext
                if any(sec_term in k.lower() for sec_term in ["secret", "password", "token", "credential"]):
                    if isinstance(v, str) and not is_secret_reference(v):
                        err = f"Sensitive undeclared field '{k}' contains plaintext rather than a secret reference placeholder."
                        if raise_on_error:
                            raise PlaintextSensitiveValueError(err, field_name=k)
                        errors.append(err)
                        invalid_fields.append(k)

        # 4. Check explicit sensitive_fields
        for s_name in self.sensitive_fields:
            if s_name in values and values[s_name] is not None:
                val = values[s_name]
                if not is_secret_reference(val):
                    err = f"Sensitive field '{s_name}' must be a secret reference, but received plaintext."
                    if raise_on_error:
                        raise PlaintextSensitiveValueError(err, field_name=s_name)
                    errors.append(err)
                    invalid_fields.append(s_name)

        # 5. Evaluate inter-field validation rules
        for rule in self.validation_rules:
            try:
                rule.evaluate(values)
            except FieldDependencyViolationError as e:
                if raise_on_error:
                    raise
                errors.append(str(e))
                if rule.source_field:
                    invalid_fields.append(rule.source_field)

        is_valid = (len(errors) == 0)
        return ValidationReport(
            is_valid=is_valid,
            errors=errors,
            warnings=warnings,
            missing_fields=list(set(missing_fields)),
            invalid_fields=list(set(invalid_fields)),
        )

    def create_configuration(
        self,
        values: Optional[dict[str, Any]] = None,
        configuration_id: Optional[str] = None,
        trace: Optional[dict[str, Any]] = None,
    ) -> RuntimeConfiguration:
        """
        Create and validate a new RuntimeConfiguration using this schema.
        Applies defaults for any missing fields.
        """
        raw_vals = dict(values or {})

        # Apply defaults
        merged_vals: dict[str, Any] = {}
        for k, default_val in self.defaults.items():
            merged_vals[k] = default_val
        merged_vals.update(raw_vals)

        # Pre-validate values
        self.validate_configuration(merged_vals, raise_on_error=True)

        cid = configuration_id or new_configuration_id()
        return RuntimeConfiguration(
            configuration_id=cid,
            product_id=self.product_id,
            schema_version=self.version,
            values=merged_vals,
            validation_status=ConfigurationValidationStatus.VALID,
            trace=dict(trace or {}),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_id": self.schema_id,
            "product_id": self.product_id,
            "version": self.version,
            "fields": [f.to_dict() for f in self.fields],
            "required_fields": list(self.required_fields),
            "optional_fields": list(self.optional_fields),
            "defaults": dict(self.defaults),
            "validation_rules": [r.to_dict() for r in self.validation_rules],
            "sensitive_fields": list(self.sensitive_fields),
            "immutable_fields": list(self.immutable_fields),
            "trace": dict(self.trace),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> RuntimeConfigurationSchema:
        return cls(
            schema_id=str(data.get("schema_id", "")),
            product_id=str(data.get("product_id", "")),
            version=str(data.get("version", "1.0.0")),
            fields=[ConfigurationField.from_dict(f) for f in data.get("fields", [])],
            required_fields=list(data.get("required_fields", [])),
            optional_fields=list(data.get("optional_fields", [])),
            defaults=dict(data.get("defaults", {})),
            validation_rules=[
                ConfigurationValidationRule.from_dict(r)
                for r in data.get("validation_rules", [])
            ],
            sensitive_fields=list(data.get("sensitive_fields", [])),
            immutable_fields=list(data.get("immutable_fields", [])),
            trace=dict(data.get("trace", {})),
        )

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent, sort_keys=True)

    @classmethod
    def from_json(cls, json_str: str) -> RuntimeConfigurationSchema:
        return cls.from_dict(json.loads(json_str))


@dataclass
class RuntimeConfiguration:
    """
    Concrete instance of software configuration.
    Configuration is data. It cannot execute commands, alter security policies,
    modify files, or store raw secrets.
    """
    configuration_id: str = field(default_factory=new_configuration_id)
    product_id: str = ""
    schema_version: str = "1.0.0"
    values: dict[str, Any] = field(default_factory=dict)
    validation_status: ConfigurationValidationStatus = ConfigurationValidationStatus.PENDING
    created_at: str = field(default_factory=utc_now)
    updated_at: str = field(default_factory=utc_now)
    trace: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.configuration_id:
            self.configuration_id = new_configuration_id()
        else:
            validate_configuration_id(self.configuration_id)

        # Coerce validation_status
        if isinstance(self.validation_status, str):
            try:
                self.validation_status = ConfigurationValidationStatus(self.validation_status.upper())
            except (ValueError, TypeError):
                self.validation_status = ConfigurationValidationStatus.PENDING

        # Invariant: Ensure pure data and reject malicious payloads immediately
        for k, v in self.values.items():
            assert_safe_configuration_value(k, v)

    def get(self, key: str, default: Any = None) -> Any:
        """Safe retrieval of a configuration value."""
        return self.values.get(key, default)

    def masked_values(self, schema: Optional[RuntimeConfigurationSchema] = None) -> dict[str, Any]:
        """
        Return a sanitized dictionary of configuration values where all sensitive
        fields are masked for safe display, logging, or debugging.
        """
        sensitive_keys: set[str] = set()

        if schema is not None:
            sensitive_keys.update(schema.sensitive_fields)
            for f in schema.fields:
                if f.sensitive or f.field_type == ConfigurationFieldType.SECRET_REF:
                    sensitive_keys.add(f.name)

        # Also detect sensitive keywords in key names
        keywords = ("password", "secret", "token", "credential", "private_key", "api_key")

        masked: dict[str, Any] = {}
        for k, v in self.values.items():
            is_sens = (k in sensitive_keys) or any(kw in k.lower() for kw in keywords)
            if is_sens:
                masked[k] = "***"
            elif isinstance(v, dict):
                masked[k] = self._mask_sub_dict(v, keywords)
            else:
                masked[k] = v

        return masked

    def _mask_sub_dict(self, d: dict[str, Any], keywords: tuple[str, ...]) -> dict[str, Any]:
        res: dict[str, Any] = {}
        for k, v in d.items():
            if any(kw in str(k).lower() for kw in keywords):
                res[k] = "***"
            elif isinstance(v, dict):
                res[k] = self._mask_sub_dict(v, keywords)
            else:
                res[k] = v
        return res

    def with_updates(
        self,
        new_values: dict[str, Any],
        schema: Optional[RuntimeConfigurationSchema] = None,
    ) -> RuntimeConfiguration:
        """
        Create a new RuntimeConfiguration reflecting updated values.
        Enforces immutability constraints when a schema is provided.
        """
        # 1. Check immutability
        if schema is not None:
            for imm in schema.immutable_fields:
                if imm in new_values and new_values[imm] != self.values.get(imm):
                    raise ImmutableConfigurationModificationError(
                        f"Cannot modify immutable configuration field '{imm}'.",
                        field_name=imm,
                    )

        # 2. Merge values
        merged = dict(self.values)
        merged.update(new_values)

        # 3. Validate against schema if provided
        status = self.validation_status
        if schema is not None:
            schema.validate_configuration(merged, raise_on_error=True)
            status = ConfigurationValidationStatus.VALID

        return RuntimeConfiguration(
            configuration_id=self.configuration_id,
            product_id=self.product_id,
            schema_version=self.schema_version,
            values=merged,
            validation_status=status,
            created_at=self.created_at,
            updated_at=utc_now(),
            trace=dict(self.trace),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "configuration_id": self.configuration_id,
            "product_id": self.product_id,
            "schema_version": self.schema_version,
            "values": dict(self.values),
            "validation_status": self.validation_status.value,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "trace": dict(self.trace),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> RuntimeConfiguration:
        st = data.get("validation_status", ConfigurationValidationStatus.PENDING)
        if isinstance(st, str):
            try:
                st = ConfigurationValidationStatus(st.upper())
            except (ValueError, TypeError):
                st = ConfigurationValidationStatus.PENDING

        return cls(
            configuration_id=str(data.get("configuration_id", "")),
            product_id=str(data.get("product_id", "")),
            schema_version=str(data.get("schema_version", "1.0.0")),
            values=dict(data.get("values", {})),
            validation_status=st,
            created_at=str(data.get("created_at", "")),
            updated_at=str(data.get("updated_at", "")),
            trace=dict(data.get("trace", {})),
        )

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent, sort_keys=True)

    @classmethod
    def from_json(cls, json_str: str) -> RuntimeConfiguration:
        return cls.from_dict(json.loads(json_str))
