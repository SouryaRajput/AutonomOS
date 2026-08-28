"""Version service — application version, API version, and schema compatibility."""
from __future__ import annotations

from typing import TYPE_CHECKING, Any, Optional

from app.dto.errors import AppException, normalize_error
from app.dto.version import SystemVersionDTO

if TYPE_CHECKING:
    from core.runtime.workforce_runtime import WorkforceRuntime


class VersionService:
    """Thin facade for system version and compatibility verification."""

    APP_VERSION = "1.0.0"
    API_VERSION = "1.0"
    SCHEMA_VERSION = 16
    MIN_SUPPORTED_CLIENT_VERSION = "1.0.0"

    def __init__(self, runtime: WorkforceRuntime):
        self._runtime = runtime

    def get_system_version(self) -> dict[str, Any]:
        """Return runtime versions and schema level."""
        try:
            dto = SystemVersionDTO(
                application_version=self.APP_VERSION,
                api_version=self.API_VERSION,
                schema_version=self.SCHEMA_VERSION,
                min_supported_client_version=self.MIN_SUPPORTED_CLIENT_VERSION,
                is_compatible=True,
                compatibility_message="System version 1.0.0 (API v1.0, Schema v16) is fully operational.",
            )
            return dto.to_dict()
        except Exception as e:
            raise AppException(normalize_error(e)) from e

    def check_compatibility(self, client_version: str) -> dict[str, Any]:
        """Verify client version compatibility with backend runtime."""
        try:
            # Simple semver major check
            client_major = client_version.split(".")[0] if "." in client_version else "0"
            server_major = self.APP_VERSION.split(".")[0]

            is_compatible = client_major == server_major
            msg = (
                "Client version is compatible."
                if is_compatible
                else f"Incompatible client version '{client_version}'. Minimum required: {self.MIN_SUPPORTED_CLIENT_VERSION}"
            )

            return {
                "client_version": client_version,
                "server_version": self.APP_VERSION,
                "is_compatible": is_compatible,
                "compatibility_message": msg,
            }
        except Exception as e:
            raise AppException(normalize_error(e)) from e
