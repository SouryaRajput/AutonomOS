from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import json
import re
from typing import Any, Optional, Sequence

from core.programmer.contracts.identifiers import (
    ENVIRONMENT_REQUIREMENT_ID_PREFIX,
    new_environment_requirement_id,
    validate_environment_requirement_id,
)
from core.programmer.contracts.runtime_configuration import is_secret_reference
from core.programmer.errors import (
    MissingEnvironmentRequirementError,
    ProgrammerValidationError,
    SecretLeakageError,
)
from core.programmer.types import (
    EnvironmentRequirementSource,
    EnvironmentRequirementType,
)


def utc_now() -> str:
    """Generate ISO 8601 UTC timestamp."""
    return datetime.now(timezone.utc).isoformat()


@dataclass
class SecretExposureFinding:
    """Diagnostic detail of an identified credential or raw secret in text/data."""
    pattern_name: str
    description: str
    snippet: str
    location: Optional[str] = None

    @property
    def secret_type(self) -> str:
        return self.pattern_name

    def to_dict(self) -> dict[str, Any]:
        return {
            "pattern_name": self.pattern_name,
            "description": self.description,
            "snippet": self.snippet,
            "location": self.location,
        }


# High-confidence credential patterns for accidental exposure detection
_SECRET_PATTERNS: list[tuple[str, re.Pattern, str]] = [
    (
        "PRIVATE_KEY",
        re.compile(r"-----BEGIN (?:[A-Z0-9 ]+ )?PRIVATE KEY-----", re.IGNORECASE),
        "Asymmetric private key block (RSA/DSA/EC/OPENSSH)",
    ),
    (
        "AWS_KEY",
        re.compile(r"\b(AKIA|ABIA|ACCA|ASIA)[0-9A-Z]{16}\b"),
        "AWS Access Key ID",
    ),
    (
        "GITHUB_TOKEN",
        re.compile(r"\b(?:ghp|gho|ghu|ghs|ghr)_[0-9a-zA-Z]{30,50}\b"),
        "GitHub Personal Access Token / App Token",
    ),
    (
        "DATABASE_PASSWORD_URI",
        re.compile(r"(?i)\b(?:postgres(?:ql)?|mysql|mongodb(?:\+srv)?|redis)://[^:]+:([^@/\s]+)@"),
        "Plaintext password embedded in database connection URI",
    ),
    (
        "API_KEY_ASSIGNMENT",
        re.compile(
            r"(?i)\b(?:api[_-]?key|secret[_-]?key|private[_-]?key|auth[_-]?token|access[_-]?token|master[_-]?key)\s*[:=]\s*['\"]([a-zA-Z0-9_\-\.]{16,})['\"]"
        ),
        "Hardcoded plaintext API key or token assignment",
    ),
    (
        "SLACK_STRIPE_TOKEN",
        re.compile(r"\b(?:xox[p|b|o|a]-[0-9]{10,}-[0-9]{10,}-[a-zA-Z0-9]{24}|sk_live_[0-9a-zA-Z]{24})\b"),
        "Live SaaS credential token (Slack / Stripe API key)",
    ),
]


class SecretExposureDetector:
    """
    Automated scanner and safety enforcement boundary that prevents raw credentials
    and production secrets from being placed in prompts, Git commits, ProgrammerResults,
    ordinary execution logs, or verification evidence.
    """

    @classmethod
    def scan_text(cls, text: str, location: Optional[str] = None) -> list[SecretExposureFinding]:
        """
        Scan text for obvious accidental secret exposures.
        Recognizes and permits authorized secret references (secret_ref:..., env:..., ${...}).
        """
        if not text or not isinstance(text, str):
            return []

        findings: list[SecretExposureFinding] = []

        for name, pattern, desc in _SECRET_PATTERNS:
            for match in pattern.finditer(text):
                full_match = match.group(0)

                # Ignore authorized secret references / placeholders
                if is_secret_reference(full_match.strip()):
                    continue

                # For key-value assignments, check if assigned value is a reference or already redacted
                if name == "API_KEY_ASSIGNMENT" and match.lastindex and match.lastindex >= 1:
                    assigned_val = match.group(match.lastindex)
                    if (
                        is_secret_reference(assigned_val)
                        or assigned_val.startswith("***")
                        or "[REDACTED" in assigned_val
                        or "placeholder" in assigned_val.lower()
                    ):
                        continue

                # For database URI, check if password group is a placeholder
                if name == "DATABASE_PASSWORD_URI" and match.lastindex and match.lastindex >= 1:
                    pw = match.group(match.lastindex)
                    if (
                        is_secret_reference(pw)
                        or pw.startswith("***")
                        or "[REDACTED" in pw
                        or pw in ("${DB_PASS}", "${DATABASE_PASSWORD}", "password", "secret")
                    ):
                        continue

                # Redact snippet for safe reporting
                snippet = full_match[:6] + "***" + (full_match[-3:] if len(full_match) > 10 else "")
                findings.append(
                    SecretExposureFinding(
                        pattern_name=name,
                        description=desc,
                        snippet=snippet,
                        location=location,
                    )
                )

        return findings

    @classmethod
    def assert_no_secret_exposure(cls, text: str, location: Optional[str] = None) -> None:
        """
        Assert that text contains no raw secrets.
        Raises SecretLeakageError if exposure is detected.
        """
        findings = cls.scan_text(text, location=location)
        if findings:
            first = findings[0]
            loc_str = f" in {first.location}" if first.location else ""
            raise SecretLeakageError(
                f"Accidental raw secret exposure detected{loc_str}: {first.description} (snippet: {first.snippet}).",
                secret_type=first.pattern_name,
                location=first.location,
            )

    @classmethod
    def sanitize_text(cls, text: str) -> str:
        """
        Replace detected raw secrets and credentials with safe [REDACTED_SECRET] markers.
        """
        if not text or not isinstance(text, str):
            return text

        sanitized = text
        for name, pattern, _ in _SECRET_PATTERNS:
            if name == "DATABASE_PASSWORD_URI":
                # Redact only password part in URI
                def replace_uri(m: re.Match) -> str:
                    whole = m.group(0)
                    pw = m.group(1)
                    if is_secret_reference(pw) or pw.startswith("***"):
                        return whole
                    return whole.replace(f":{pw}@", ":[REDACTED_SECRET]@")
                sanitized = pattern.sub(replace_uri, sanitized)
            elif name == "API_KEY_ASSIGNMENT":
                def replace_key(m: re.Match) -> str:
                    whole = m.group(0)
                    val = m.group(1)
                    if is_secret_reference(val) or val.startswith("***"):
                        return whole
                    return whole.replace(val, "[REDACTED_SECRET]")
                sanitized = pattern.sub(replace_key, sanitized)
            else:
                sanitized = pattern.sub("[REDACTED_SECRET]", sanitized)

        return sanitized

    @classmethod
    def validate_prompt_safety(cls, prompt_text: str) -> None:
        """Enforce the rule: Never place secrets in Programmer prompts."""
        cls.assert_no_secret_exposure(prompt_text, location="ProgrammerPrompt")

    @classmethod
    def validate_result_safety(cls, result_data: Any) -> None:
        """Enforce the rule: Never place secrets in ProgrammerResult."""
        if isinstance(result_data, str):
            cls.assert_no_secret_exposure(result_data, location="ProgrammerResult")
        elif isinstance(result_data, dict):
            for k, v in result_data.items():
                cls.validate_result_safety(v)
        elif isinstance(result_data, list):
            for item in result_data:
                cls.validate_result_safety(item)
        elif hasattr(result_data, "to_dict"):
            cls.validate_result_safety(result_data.to_dict())

    @classmethod
    def validate_evidence_safety(cls, evidence: Any) -> None:
        """Enforce the rule: Never echo secret values into verification evidence."""
        desc = getattr(evidence, "description", "")
        if desc:
            cls.assert_no_secret_exposure(desc, location="VerificationEvidence.description")
        data = getattr(evidence, "data", {})
        if isinstance(data, dict):
            for k, v in data.items():
                if isinstance(v, str):
                    cls.assert_no_secret_exposure(v, location=f"VerificationEvidence.data.{k}")

    @classmethod
    def validate_git_safety(cls, commit_message: str, diff_text: Optional[str] = None) -> None:
        """Enforce the rule: Never place secrets in Git commits or diffs."""
        if commit_message:
            cls.assert_no_secret_exposure(commit_message, location="GitCommitMessage")
        if diff_text:
            cls.assert_no_secret_exposure(diff_text, location="GitDiff")


def is_authorized_secret_placeholder(value: Any) -> bool:
    """Check if value is an authorized secret reference placeholder or safe redaction token."""
    if not isinstance(value, str):
        return False
    val = value.strip()
    if val in ("[REDACTED_SECRET]", "***", "[REDACTED]"):
        return True
    return is_secret_reference(val)


@dataclass
class EnvironmentRequirement:
    """
    Structured domain model defining an environment variable or secret reference requirement.
    Programmer identifies required variables and configuration, but never obtains or generates
    raw production secrets.
    """
    requirement_id: str = field(default_factory=new_environment_requirement_id)
    product_id: str = ""
    name: str = ""
    type: EnvironmentRequirementType = EnvironmentRequirementType.STRING
    required: bool = True
    sensitive: bool = False
    description: str = ""
    validation_rules: list[str] | dict[str, Any] = field(default_factory=list)
    source: EnvironmentRequirementSource = EnvironmentRequirementSource.DECLARED
    trace: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.requirement_id:
            self.requirement_id = new_environment_requirement_id()
        else:
            validate_environment_requirement_id(self.requirement_id)

        if not self.name or not self.name.strip():
            raise ProgrammerValidationError("EnvironmentRequirement requires a non-empty name.", field_name="name")

        if isinstance(self.type, str):
            try:
                self.type = EnvironmentRequirementType(self.type.upper())
            except (ValueError, TypeError):
                self.type = EnvironmentRequirementType.STRING

        if isinstance(self.source, str):
            try:
                self.source = EnvironmentRequirementSource(self.source.upper())
            except (ValueError, TypeError):
                self.source = EnvironmentRequirementSource.DECLARED

        # SECRET_REF type is inherently sensitive
        if self.type == EnvironmentRequirementType.SECRET_REF:
            self.sensitive = True

        # Support list or dict validation_rules
        if isinstance(self.validation_rules, dict):
            self.validation_rules = dict(self.validation_rules)
        elif isinstance(self.validation_rules, (list, tuple, set)):
            self.validation_rules = list(self.validation_rules)
        else:
            self.validation_rules = []

    def validate_value(self, value: Any, raise_on_error: bool = False) -> Optional[str]:
        """
        Validate a provided environment value against this requirement's constraints
        and sensitivity bounds.
        Returns None if valid; returns an error string if invalid (or raises if raise_on_error=True).
        """
        err_msg: Optional[str] = None
        exc: Optional[Exception] = None

        if value is None:
            if self.required:
                err_msg = f"Required environment requirement '{self.name}' is missing."
                exc = MissingEnvironmentRequirementError(err_msg, requirement_name=self.name)
            if err_msg:
                if raise_on_error and exc:
                    raise exc
                return err_msg
            return None

        # Sensitivity check: sensitive environment values must be secret references, not raw plaintext
        if self.sensitive:
            if not is_authorized_secret_placeholder(value):
                # Also check secret exposure detector
                if isinstance(value, str):
                    findings = SecretExposureDetector.scan_text(value, location=self.name)
                    if findings:
                        f = findings[0]
                        err_msg = f"Accidental raw secret exposure in '{self.name}': {f.description}"
                        exc = SecretLeakageError(err_msg, secret_type=f.pattern_name, location=self.name)
                if not err_msg:
                    err_msg = (
                        f"Sensitive environment requirement '{self.name}' cannot contain plaintext raw secret; "
                        f"must be a secret reference placeholder ('secret_ref:...', 'env:...', '${{...}}')."
                    )
                    exc = SecretLeakageError(err_msg, secret_type="PLAINTEXT_CREDENTIAL", location=self.name)
                if raise_on_error and exc:
                    raise exc
                return err_msg

        # Type checks
        if self.type == EnvironmentRequirementType.INTEGER:
            if isinstance(value, bool) or not isinstance(value, int):
                if isinstance(value, str) and value.isdigit():
                    value = int(value)
                else:
                    err_msg = f"Environment requirement '{self.name}' expected INTEGER, got {type(value).__name__}."
                    exc = ProgrammerValidationError(err_msg, field_name=self.name)
        elif self.type == EnvironmentRequirementType.BOOLEAN:
            if not isinstance(value, bool):
                if isinstance(value, str) and value.lower() in ("true", "false"):
                    pass
                else:
                    err_msg = f"Environment requirement '{self.name}' expected BOOLEAN, got {type(value).__name__}."
                    exc = ProgrammerValidationError(err_msg, field_name=self.name)
        elif self.type == EnvironmentRequirementType.URL:
            if not isinstance(value, str) or not (value.startswith("http://") or value.startswith("https://")):
                err_msg = f"Environment requirement '{self.name}' expected valid URL (http:// or https://), got '{value}'."
                exc = ProgrammerValidationError(err_msg, field_name=self.name)

        # Validation rules evaluation
        if err_msg is None and isinstance(self.validation_rules, dict):
            min_val = self.validation_rules.get("min")
            if min_val is not None and isinstance(value, (int, float)) and value < min_val:
                err_msg = f"Environment requirement '{self.name}' value {value} is below minimum {min_val}."
                exc = ProgrammerValidationError(err_msg, field_name=self.name)
            max_val = self.validation_rules.get("max")
            if max_val is not None and isinstance(value, (int, float)) and value > max_val:
                err_msg = f"Environment requirement '{self.name}' value {value} exceeds maximum {max_val}."
                exc = ProgrammerValidationError(err_msg, field_name=self.name)
            regex_pat = self.validation_rules.get("regex") or self.validation_rules.get("pattern")
            if regex_pat is not None and isinstance(value, str) and not re.search(regex_pat, value):
                err_msg = f"Environment requirement '{self.name}' does not match pattern '{regex_pat}'."
                exc = ProgrammerValidationError(err_msg, field_name=self.name)

        if err_msg is not None:
            if raise_on_error and exc:
                raise exc
            return err_msg

        return None

    def to_dict(self) -> dict[str, Any]:
        """
        Serialize EnvironmentRequirement safely without any raw secret values.
        """
        rules_val: Any = self.validation_rules
        if isinstance(self.validation_rules, dict):
            rules_val = dict(self.validation_rules)
        elif isinstance(self.validation_rules, list):
            rules_val = list(self.validation_rules)

        return {
            "requirement_id": self.requirement_id,
            "product_id": self.product_id,
            "name": self.name,
            "type": self.type.value,
            "required": self.required,
            "sensitive": self.sensitive,
            "description": self.description,
            "validation_rules": rules_val,
            "source": self.source.value,
            "trace": dict(self.trace),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> EnvironmentRequirement:
        raw_rules = data.get("validation_rules", [])
        if isinstance(raw_rules, dict):
            parsed_rules = dict(raw_rules)
        elif isinstance(raw_rules, list):
            parsed_rules = list(raw_rules)
        else:
            parsed_rules = []

        return cls(
            requirement_id=str(data.get("requirement_id", "")),
            product_id=str(data.get("product_id", "")),
            name=str(data.get("name", "")),
            type=EnvironmentRequirementType(data.get("type", EnvironmentRequirementType.STRING.value)),
            required=bool(data.get("required", True)),
            sensitive=bool(data.get("sensitive", False)),
            description=str(data.get("description", "")),
            validation_rules=parsed_rules,
            source=EnvironmentRequirementSource(data.get("source", EnvironmentRequirementSource.DECLARED.value)),
            trace=dict(data.get("trace", {})),
        )

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent, sort_keys=True)

    @classmethod
    def from_json(cls, json_str: str) -> EnvironmentRequirement:
        return cls.from_dict(json.loads(json_str))
