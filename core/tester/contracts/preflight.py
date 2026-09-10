from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import logging
from typing import Any, Optional, Sequence

from core.tester.contracts.identifiers import (
    new_preflight_id,
    validate_execution_id,
    validate_preflight_id,
)
from core.tester.contracts.observation import TesterObservation
from core.tester.errors import (
    TesterBoundaryViolationError,
    TesterLineageError,
    TesterValidationError,
)
from core.tester.types import (
    ApplicationHealthStatus,
    BuildStatus,
    PreflightDecision,
    PreflightStatus,
)

logger = logging.getLogger("AutonomOS.Tester.Preflight")


def utc_now() -> str:
    """Generate ISO 8601 UTC timestamp."""
    return datetime.now(timezone.utc).isoformat()


@dataclass
class RouteCheckResult:
    """
    Structured outcome of an individual route health verification check.
    Captures reachability, status code, latency, and failure context.
    """
    __test__ = False
    route: str
    status_code: Optional[int] = None
    is_reachable: bool = False
    is_healthy: bool = False
    latency_ms: Optional[float] = None
    error_message: Optional[str] = None
    is_required: bool = True
    expected_status_code: int = 200
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.route or not str(self.route).strip():
            raise TesterValidationError("RouteCheckResult must have a non-empty route path.", field_name="route")

    def to_dict(self) -> dict[str, Any]:
        return {
            "route": self.route,
            "status_code": self.status_code,
            "is_reachable": self.is_reachable,
            "is_healthy": self.is_healthy,
            "latency_ms": self.latency_ms,
            "error_message": self.error_message,
            "is_required": self.is_required,
            "expected_status_code": self.expected_status_code,
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> RouteCheckResult:
        if not isinstance(data, dict):
            raise TesterValidationError(f"Expected dict for RouteCheckResult, got {type(data).__name__}.")
        return cls(
            route=str(data.get("route", "")),
            status_code=data.get("status_code"),
            is_reachable=bool(data.get("is_reachable", False)),
            is_healthy=bool(data.get("is_healthy", False)),
            latency_ms=float(data["latency_ms"]) if data.get("latency_ms") is not None else None,
            error_message=data.get("error_message"),
            is_required=bool(data.get("is_required", True)),
            expected_status_code=int(data.get("expected_status_code", 200)),
            metadata=dict(data.get("metadata", {})),
        )


@dataclass
class ResourceCheckResult:
    """
    Observation and evaluation of critical or required application resources (images, styles, APIs).
    Detects repeated missing assets and critical asset unreachability.
    """
    __test__ = False
    resource_url: str
    resource_type: str = "asset"
    status_code: Optional[int] = None
    is_available: bool = True
    is_required: bool = False
    failure_count: int = 1
    error_message: Optional[str] = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.resource_url or not str(self.resource_url).strip():
            raise TesterValidationError("ResourceCheckResult must have a non-empty resource_url.", field_name="resource_url")

    def to_dict(self) -> dict[str, Any]:
        return {
            "resource_url": self.resource_url,
            "resource_type": self.resource_type,
            "status_code": self.status_code,
            "is_available": self.is_available,
            "is_required": self.is_required,
            "failure_count": self.failure_count,
            "error_message": self.error_message,
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ResourceCheckResult:
        if not isinstance(data, dict):
            raise TesterValidationError(f"Expected dict for ResourceCheckResult, got {type(data).__name__}.")
        return cls(
            resource_url=str(data.get("resource_url", "")),
            resource_type=str(data.get("resource_type", "asset")),
            status_code=data.get("status_code"),
            is_available=bool(data.get("is_available", True)),
            is_required=bool(data.get("is_required", False)),
            failure_count=int(data.get("failure_count", 1)),
            error_message=data.get("error_message"),
            metadata=dict(data.get("metadata", {})),
        )


@dataclass
class TestPreflightResult:
    """
    Authoritative evaluation result of the application and runtime preflight health check.
    
    Verifies whether the target application/runtime is healthy enough for the planned
    tests to execute prior to Phase 5 test execution.
    
    Invariants:
    1. Product Evaluation Separation:
       Preflight failure is NOT a Tester failure; it is a successful discovery of a product defect/failure.
    2. Zero Fixing:
       The Tester must NEVER edit source, delete files, install dependencies, or fix build errors.
    3. Multi-Faceted Health:
       Visual rendering alone is insufficient; process liveness, HTTP status, route accessibility,
       and terminal output are all evaluated.
    4. Tenant and Lineage Isolation:
       Tied strictly to execution_id and project_id.
    """
    __test__ = False
    preflight_id: str
    execution_id: str
    project_id: str
    status: PreflightStatus = PreflightStatus.NOT_VERIFIED
    decision: PreflightDecision = PreflightDecision.BLOCKED
    application_status: ApplicationHealthStatus = ApplicationHealthStatus.UNKNOWN
    build_status: Optional[BuildStatus] = None
    runtime_status: str = "UNKNOWN"
    route_checks: list[RouteCheckResult] = field(default_factory=list)
    resource_checks: list[ResourceCheckResult] = field(default_factory=list)
    error_observations: list[TesterObservation] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    blockers: list[str] = field(default_factory=list)
    evidence_ids: list[str] = field(default_factory=list)
    provenance: dict[str, Any] = field(default_factory=dict)
    trace: dict[str, Any] = field(default_factory=dict)
    timestamps: dict[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        validate_preflight_id(self.preflight_id)
        validate_execution_id(self.execution_id)
        if not self.project_id or not str(self.project_id).strip():
            raise TesterLineageError("TestPreflightResult must have a valid non-empty project_id.")

        if isinstance(self.status, str):
            try:
                self.status = PreflightStatus(self.status.upper())
            except (ValueError, KeyError):
                self.status = PreflightStatus.NOT_VERIFIED

        if isinstance(self.decision, str):
            try:
                self.decision = PreflightDecision(self.decision.upper())
            except (ValueError, KeyError):
                self.decision = PreflightDecision.BLOCKED

        if isinstance(self.application_status, str):
            try:
                self.application_status = ApplicationHealthStatus(self.application_status.upper())
            except (ValueError, KeyError):
                self.application_status = ApplicationHealthStatus.UNKNOWN

        if isinstance(self.build_status, str):
            try:
                self.build_status = BuildStatus(self.build_status.upper())
            except (ValueError, KeyError):
                pass

        self.route_checks = list(self.route_checks)
        self.resource_checks = list(self.resource_checks)
        self.error_observations = list(self.error_observations)
        self.warnings = list(self.warnings)
        self.blockers = list(self.blockers)
        self.evidence_ids = list(self.evidence_ids)
        self.provenance = dict(self.provenance)
        self.trace = dict(self.trace)
        self.timestamps = dict(self.timestamps)
        self.timestamps.setdefault("created_at", utc_now())

    # -----------------------------------------------------------------------
    # Decision Properties
    # -----------------------------------------------------------------------
    @property
    def is_ready(self) -> bool:
        """Return True if application is healthy and tests can execute without blocker."""
        return self.decision in (PreflightDecision.READY, PreflightDecision.READY_WITH_WARNINGS)

    @property
    def is_ready_with_warnings(self) -> bool:
        """Return True if application is runnable but non-critical warnings exist."""
        return self.decision == PreflightDecision.READY_WITH_WARNINGS

    @property
    def is_failed(self) -> bool:
        """Return True if application/runtime has failed preflight health verification."""
        return self.decision == PreflightDecision.FAILED

    @property
    def is_blocked(self) -> bool:
        """Return True if preflight could not complete due to unavailable capability or environment."""
        return self.decision == PreflightDecision.BLOCKED

    # -----------------------------------------------------------------------
    # Zero-Fixing Invariant Enforcement
    # -----------------------------------------------------------------------
    def apply_fix(self, *args: Any, **kwargs: Any) -> Any:
        raise TesterBoundaryViolationError(
            action="UNAUTHORIZED_FIX",
            reason="Tester cannot perform automated code, configuration, or environment fixes.",
        )

    def repair_source(self, *args: Any, **kwargs: Any) -> Any:
        raise TesterBoundaryViolationError(
            action="UNAUTHORIZED_FIX",
            reason="Tester cannot edit or repair source code.",
        )

    def install_dependency(self, *args: Any, **kwargs: Any) -> Any:
        raise TesterBoundaryViolationError(
            action="UNAUTHORIZED_FIX",
            reason="Tester cannot install dependencies or packages.",
        )

    def repair_assets(self, *args: Any, **kwargs: Any) -> Any:
        raise TesterBoundaryViolationError(
            action="UNAUTHORIZED_FIX",
            reason="Tester cannot generate or repair missing application assets.",
        )

    # -----------------------------------------------------------------------
    # Serialization
    # -----------------------------------------------------------------------
    def to_dict(self) -> dict[str, Any]:
        return {
            "preflight_id": self.preflight_id,
            "execution_id": self.execution_id,
            "project_id": self.project_id,
            "status": self.status.value,
            "decision": self.decision.value,
            "application_status": self.application_status.value,
            "build_status": self.build_status.value if self.build_status is not None else None,
            "runtime_status": self.runtime_status,
            "route_checks": [rc.to_dict() for rc in self.route_checks],
            "resource_checks": [r.to_dict() for r in self.resource_checks],
            "error_observations": [
                obs.to_dict() if hasattr(obs, "to_dict") else obs
                for obs in self.error_observations
            ],
            "warnings": list(self.warnings),
            "blockers": list(self.blockers),
            "evidence_ids": list(self.evidence_ids),
            "provenance": dict(self.provenance),
            "trace": dict(self.trace),
            "timestamps": dict(self.timestamps),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> TestPreflightResult:
        if not isinstance(data, dict):
            raise TesterValidationError(f"Expected dict for TestPreflightResult, got {type(data).__name__}.")

        raw_status = data.get("status", PreflightStatus.NOT_VERIFIED.value)
        try:
            status = PreflightStatus(str(raw_status).upper())
        except (ValueError, KeyError):
            status = PreflightStatus.NOT_VERIFIED

        raw_decision = data.get("decision", PreflightDecision.BLOCKED.value)
        try:
            decision = PreflightDecision(str(raw_decision).upper())
        except (ValueError, KeyError):
            decision = PreflightDecision.BLOCKED

        raw_app_st = data.get("application_status", ApplicationHealthStatus.UNKNOWN.value)
        try:
            app_status = ApplicationHealthStatus(str(raw_app_st).upper())
        except (ValueError, KeyError):
            app_status = ApplicationHealthStatus.UNKNOWN

        raw_build_st = data.get("build_status")
        build_status = None
        if raw_build_st is not None:
            try:
                build_status = BuildStatus(str(raw_build_st).upper())
            except (ValueError, KeyError):
                pass

        routes = [
            RouteCheckResult.from_dict(r) if isinstance(r, dict) else r
            for r in data.get("route_checks", [])
        ]
        resources = [
            ResourceCheckResult.from_dict(r) if isinstance(r, dict) else r
            for r in data.get("resource_checks", [])
        ]
        observations = [
            TesterObservation.from_dict(o) if isinstance(o, dict) else o
            for o in data.get("error_observations", [])
        ]

        return cls(
            preflight_id=str(data.get("preflight_id", new_preflight_id())),
            execution_id=str(data.get("execution_id", "")),
            project_id=str(data.get("project_id", "")),
            status=status,
            decision=decision,
            application_status=app_status,
            build_status=build_status,
            runtime_status=str(data.get("runtime_status", "UNKNOWN")),
            route_checks=routes,
            resource_checks=resources,
            error_observations=observations,
            warnings=list(data.get("warnings", [])),
            blockers=list(data.get("blockers", [])),
            evidence_ids=list(data.get("evidence_ids", [])),
            provenance=dict(data.get("provenance", {})),
            trace=dict(data.get("trace", {})),
            timestamps=dict(data.get("timestamps", {})),
        )
