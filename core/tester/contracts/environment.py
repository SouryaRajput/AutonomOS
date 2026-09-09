from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

from core.tester.errors import TesterValidationError


@dataclass
class TestEnvironment:
    """
    Target product environment configuration for testing.
    Specifies deployment tier, base URLs, environment parameters, and fixtures.
    """
    __test__ = False
    env_name: str = "staging"
    base_url: Optional[str] = None
    variables: dict[str, str] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)

    def validate(self) -> None:
        if not self.env_name or not str(self.env_name).strip():
            raise TesterValidationError(
                "TestEnvironment must have a non-empty env_name.",
                field_name="test_environment.env_name",
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "env_name": self.env_name,
            "base_url": self.base_url,
            "variables": dict(self.variables),
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> TestEnvironment:
        return cls(
            env_name=str(data.get("env_name", "staging")),
            base_url=data.get("base_url"),
            variables=dict(data.get("variables", {})),
            metadata=dict(data.get("metadata", {})),
        )
