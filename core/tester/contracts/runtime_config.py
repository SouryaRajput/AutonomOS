from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

from core.tester.contracts.environment import assert_no_sensitive_values
from core.tester.errors import TesterValidationError


@dataclass
class TestRuntimeConfig:
    """
    Explicit, sanitized configuration for an active TestRuntime instance.
    
    Guarantees:
    - Strictly validates viewport limits, timeouts, and URLs.
    - Zero secret storage: rejects passwords, access keys, or bearer tokens.
    - Sanitized logging and serial representation.
    """
    __test__ = False
    application_url: Optional[str] = None
    viewport_width: int = 1280
    viewport_height: int = 720
    timeout_seconds: float = 30.0
    browser_type: str = "chromium"
    headless: bool = True
    parameters: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.viewport_width = int(self.viewport_width)
        self.viewport_height = int(self.viewport_height)
        self.timeout_seconds = float(self.timeout_seconds)
        self.parameters = dict(self.parameters)

    def validate(self) -> None:
        """Validate configuration values and ensure no secrets are stored."""
        if self.viewport_width < 320 or self.viewport_height < 240:
            raise TesterValidationError(
                f"Viewport dimensions ({self.viewport_width}x{self.viewport_height}) too small. Minimum is 320x240.",
                field_name="viewport",
            )
        if self.viewport_width > 7680 or self.viewport_height > 4320:
            raise TesterValidationError(
                f"Viewport dimensions ({self.viewport_width}x{self.viewport_height}) exceed maximum allowed 7680x4320.",
                field_name="viewport",
            )

        if self.timeout_seconds <= 0:
            raise TesterValidationError(
                f"timeout_seconds must be strictly positive, got {self.timeout_seconds}",
                field_name="timeout_seconds",
            )
        if self.timeout_seconds > 600.0:
            raise TesterValidationError(
                f"timeout_seconds cannot exceed 600.0s (10 minutes), got {self.timeout_seconds}",
                field_name="timeout_seconds",
            )

        # Prohibit secrets in parameters
        assert_no_sensitive_values(self.parameters, "parameters")

        # Validate URL format if provided
        if self.application_url:
            if "://" not in self.application_url and not self.application_url.startswith(("/", "http", "ws")):
                raise TesterValidationError(
                    f"Invalid application URL format: '{self.application_url}'",
                    field_name="application_url",
                )

    def to_dict(self) -> dict[str, Any]:
        """Return safe, non-sensitive dictionary representation."""
        return {
            "application_url": self.application_url,
            "viewport_width": self.viewport_width,
            "viewport_height": self.viewport_height,
            "timeout_seconds": self.timeout_seconds,
            "browser_type": self.browser_type,
            "headless": self.headless,
            "parameters": dict(self.parameters),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> TestRuntimeConfig:
        return cls(
            application_url=data.get("application_url"),
            viewport_width=int(data.get("viewport_width", 1280)),
            viewport_height=int(data.get("viewport_height", 720)),
            timeout_seconds=float(data.get("timeout_seconds", 30.0)),
            browser_type=str(data.get("browser_type", "chromium")),
            headless=bool(data.get("headless", True)),
            parameters=dict(data.get("parameters", {})),
        )
