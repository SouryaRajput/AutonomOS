from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import re
from typing import Any, Optional

from core.tester.contracts.identifiers import (
    new_environment_id,
    validate_environment_id,
    validate_execution_id,
    validate_work_order_id,
)
from core.tester.errors import TesterValidationError
from core.tester.types import EnvironmentType


def utc_now() -> str:
    """Generate ISO 8601 UTC timestamp."""
    return datetime.now(timezone.utc).isoformat()


_SUSPICIOUS_SECRET_KEYS = frozenset({
    "password", "secret", "token", "apikey", "api_key", "access_key", "private_key"
})


def assert_no_sensitive_values(data: dict[str, Any], path_prefix: str = "configuration") -> None:
    """Ensure sensitive credentials or tokens are not stored in runtime/environment models."""
    for key, value in data.items():
        k_norm = str(key).strip().lower()
        for sk in _SUSPICIOUS_SECRET_KEYS:
            if sk in k_norm:
                raise TesterValidationError(
                    f"Sensitive credential key '{key}' detected in {path_prefix}. Storing secrets in environment models is prohibited.",
                    field_name=f"{path_prefix}.{key}",
                )
        if isinstance(value, dict):
            assert_no_sensitive_values(value, f"{path_prefix}.{key}")


@dataclass
class TestEnvironment:
    """
    Target product environment configuration where testing occurs.
    
    Subordinate to TesterExecution: specifies the concrete target endpoint,
    environment type, viewport, operating context, and configuration.
    
    Guarantees:
    - Bounded to project, work order, and execution lineage.
    - Strictly rejects embedded secrets or credentials.
    - Retains backward compatibility with Phase 1 env_name and base_url fields.
    """
    __test__ = False
    environment_id: str = field(default_factory=new_environment_id)
    project_id: str = ""
    work_order_id: str = ""
    execution_id: str = ""
    application_url: Optional[str] = None
    application_reference: Optional[str] = None
    environment_type: EnvironmentType = EnvironmentType.BROWSER
    viewport: dict[str, int] = field(default_factory=lambda: {"width": 1280, "height": 720})
    operating_context: dict[str, Any] = field(default_factory=dict)
    configuration: dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=utc_now)
    trace: dict[str, Any] = field(default_factory=dict)

    # Backward compatibility fields (Phase 1)
    env_name: str = "staging"
    base_url: Optional[str] = None
    variables: dict[str, str] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.environment_id:
            self.environment_id = new_environment_id()
        else:
            validate_environment_id(self.environment_id)

        # Synchronize application_url and base_url
        if not self.application_url and self.base_url:
            self.application_url = self.base_url
        elif not self.base_url and self.application_url:
            self.base_url = self.application_url

        # Normalize environment_type
        if isinstance(self.environment_type, str):
            try:
                self.environment_type = EnvironmentType(self.environment_type.upper())
            except (ValueError, KeyError):
                self.environment_type = EnvironmentType.BROWSER

        # Normalize viewport
        if not isinstance(self.viewport, dict):
            self.viewport = {"width": 1280, "height": 720}
        else:
            w = int(self.viewport.get("width", 1280))
            h = int(self.viewport.get("height", 720))
            self.viewport = {"width": w, "height": h}

        self.operating_context = dict(self.operating_context)
        self.configuration = dict(self.configuration)
        self.variables = dict(self.variables)
        self.metadata = dict(self.metadata)
        self.trace = dict(self.trace)

    def validate(self) -> None:
        """Validate internal consistency, lineage, viewport, and secret absence."""
        validate_environment_id(self.environment_id)

        if not self.env_name or not str(self.env_name).strip():
            raise TesterValidationError(
                "TestEnvironment must have a non-empty env_name.",
                field_name="test_environment.env_name",
            )

        if self.work_order_id:
            validate_work_order_id(self.work_order_id)
        if self.execution_id:
            validate_execution_id(self.execution_id)

        # Viewport validation
        w = self.viewport.get("width", 0)
        h = self.viewport.get("height", 0)
        if w < 320 or h < 240:
            raise TesterValidationError(
                f"Viewport dimensions ({w}x{h}) are too small; minimum is 320x240.",
                field_name="viewport",
            )
        if w > 7680 or h > 4320:
            raise TesterValidationError(
                f"Viewport dimensions ({w}x{h}) exceed maximum allowed 7680x4320.",
                field_name="viewport",
            )

        # Secret protection checks
        assert_no_sensitive_values(self.configuration, "configuration")
        assert_no_sensitive_values(self.variables, "variables")
        assert_no_sensitive_values(self.metadata, "metadata")

        # URL validation if present
        target_url = self.application_url or self.base_url
        if target_url:
            if "://" not in target_url and not target_url.startswith(("/", "http", "ws")):
                raise TesterValidationError(
                    f"Invalid application URL format: '{target_url}'. Must specify a valid protocol or path.",
                    field_name="application_url",
                )

    def to_dict(self) -> dict[str, Any]:
        return {
            "environment_id": self.environment_id,
            "project_id": self.project_id,
            "work_order_id": self.work_order_id,
            "execution_id": self.execution_id,
            "application_url": self.application_url,
            "application_reference": self.application_reference,
            "environment_type": self.environment_type.value,
            "viewport": dict(self.viewport),
            "operating_context": dict(self.operating_context),
            "configuration": dict(self.configuration),
            "created_at": self.created_at,
            "trace": dict(self.trace),
            "env_name": self.env_name,
            "base_url": self.base_url,
            "variables": dict(self.variables),
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> TestEnvironment:
        env_type_raw = data.get("environment_type", EnvironmentType.BROWSER.value)
        try:
            env_type = EnvironmentType(str(env_type_raw).upper())
        except (ValueError, KeyError):
            env_type = EnvironmentType.BROWSER

        return cls(
            environment_id=data.get("environment_id", ""),
            project_id=data.get("project_id", ""),
            work_order_id=data.get("work_order_id", ""),
            execution_id=data.get("execution_id", ""),
            application_url=data.get("application_url") or data.get("base_url"),
            application_reference=data.get("application_reference"),
            environment_type=env_type,
            viewport=dict(data.get("viewport", {"width": 1280, "height": 720})),
            operating_context=dict(data.get("operating_context", {})),
            configuration=dict(data.get("configuration", {})),
            created_at=data.get("created_at", utc_now()),
            trace=dict(data.get("trace", {})),
            env_name=str(data.get("env_name", "staging")),
            base_url=data.get("base_url") or data.get("application_url"),
            variables=dict(data.get("variables", {})),
            metadata=dict(data.get("metadata", {})),
        )
