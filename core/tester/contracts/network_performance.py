from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import logging
from typing import Any, Optional, Sequence

from core.tester.contracts.identifiers import (
    new_network_performance_id,
    new_network_request_id,
    validate_execution_id,
    validate_network_performance_id,
    validate_network_request_id,
    validate_test_case_id,
)
from core.tester.contracts.performance import PerformanceMeasurement
from core.tester.errors import (
    TesterBoundaryViolationError,
    TesterLineageError,
    TesterValidationError,
)
from core.tester.types import (
    NetworkPerformanceStatus,
    ResourceImportance,
    ResourceType,
)

logger = logging.getLogger("AutonomOS.Tester.NetworkPerformanceContracts")


def utc_now() -> str:
    """Generate ISO 8601 UTC timestamp."""
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# Network Request Record
# ---------------------------------------------------------------------------

@dataclass
class NetworkRequestRecord:
    """
    Authoritative record for an individual HTTP/network request observed during an authorized test.
    Captures path, method, status, duration, resource type, transfer size, and criticality.
    """
    __test__ = False
    url: str
    method: str = "GET"
    status_code: Optional[int] = None
    duration_ms: Optional[float] = None
    resource_type: ResourceType = ResourceType.OTHER
    importance: ResourceImportance = ResourceImportance.REQUIRED
    transfer_size_bytes: Optional[int] = None
    is_success: bool = True
    error_message: Optional[str] = None
    test_case_id: Optional[str] = None
    execution_id: Optional[str] = None
    timestamp: str = field(default_factory=utc_now)
    provenance: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)
    request_id: str = field(default_factory=new_network_request_id)

    def __post_init__(self) -> None:
        self.validate()

    def validate(self) -> None:
        validate_network_request_id(self.request_id)

        if not self.url or not str(self.url).strip():
            raise TesterValidationError("NetworkRequestRecord requires a valid non-empty url.", field_name="url")

        self.method = self.method.upper().strip()

        if isinstance(self.resource_type, str):
            try:
                self.resource_type = ResourceType.from_str(self.resource_type)
            except Exception:
                self.resource_type = ResourceType.OTHER

        if isinstance(self.importance, str):
            try:
                self.importance = ResourceImportance(self.importance.upper().strip())
            except ValueError:
                self.importance = ResourceImportance.REQUIRED

        if self.execution_id:
            validate_execution_id(self.execution_id)

        if self.test_case_id:
            validate_test_case_id(self.test_case_id)

        # Sync is_success if status_code indicates an HTTP error
        if self.status_code is not None:
            if self.status_code >= 400:
                self.is_success = False

        self.provenance = dict(self.provenance)
        self.metadata = dict(self.metadata)

    @property
    def is_404(self) -> bool:
        return self.status_code == 404

    @property
    def is_500(self) -> bool:
        return self.status_code is not None and self.status_code >= 500

    @property
    def is_failed(self) -> bool:
        return not self.is_success or self.status_code is None or self.status_code >= 400

    @property
    def is_required(self) -> bool:
        return self.importance == ResourceImportance.REQUIRED

    @property
    def is_optional(self) -> bool:
        return self.importance == ResourceImportance.OPTIONAL

    def to_dict(self) -> dict[str, Any]:
        return {
            "request_id": self.request_id,
            "url": self.url,
            "method": self.method,
            "status_code": self.status_code,
            "duration_ms": self.duration_ms,
            "resource_type": self.resource_type.value,
            "importance": self.importance.value,
            "transfer_size_bytes": self.transfer_size_bytes,
            "is_success": self.is_success,
            "error_message": self.error_message,
            "test_case_id": self.test_case_id,
            "execution_id": self.execution_id,
            "timestamp": self.timestamp,
            "provenance": dict(self.provenance),
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> NetworkRequestRecord:
        raw_res = data.get("resource_type", ResourceType.OTHER.value)
        try:
            resource_type = ResourceType.from_str(raw_res)
        except Exception:
            resource_type = ResourceType.OTHER

        raw_imp = data.get("importance", ResourceImportance.REQUIRED.value)
        try:
            importance = ResourceImportance(str(raw_imp).upper())
        except ValueError:
            importance = ResourceImportance.REQUIRED

        duration = data.get("duration_ms")
        transfer_size = data.get("transfer_size_bytes")
        status_code = data.get("status_code")

        return cls(
            request_id=str(data.get("request_id", new_network_request_id())),
            url=str(data["url"]),
            method=str(data.get("method", "GET")),
            status_code=int(status_code) if status_code is not None else None,
            duration_ms=float(duration) if duration is not None else None,
            resource_type=resource_type,
            importance=importance,
            transfer_size_bytes=int(transfer_size) if transfer_size is not None else None,
            is_success=bool(data.get("is_success", True)),
            error_message=data.get("error_message"),
            test_case_id=data.get("test_case_id"),
            execution_id=data.get("execution_id"),
            timestamp=str(data.get("timestamp", utc_now())),
            provenance=dict(data.get("provenance", {})),
            metadata=dict(data.get("metadata", {})),
        )

    # Invariant guards
    def apply_fix(self, *args: Any, **kwargs: Any) -> None:
        raise TesterBoundaryViolationError(
            action="PERFORMANCE_AUTO_FIX",
            reason="Network request record cannot modify application code or apply fixes.",
        )

    def auto_fix(self, *args: Any, **kwargs: Any) -> None:
        raise TesterBoundaryViolationError(
            action="PERFORMANCE_AUTO_FIX",
            reason="Network request record cannot modify application code or apply fixes.",
        )

    def create_defect(self, *args: Any, **kwargs: Any) -> None:
        raise TesterBoundaryViolationError(
            action="PERFORMANCE_DEFECT_CLASSIFICATION",
            reason="Network request record does not classify defects in Phase 7.4.",
        )


# ---------------------------------------------------------------------------
# Network Performance Specification
# ---------------------------------------------------------------------------

@dataclass
class NetworkPerformanceSpec:
    """
    Specification for evaluating authorized network and resource performance.
    Contains optional explicit request count budget and request duration threshold.
    """
    __test__ = False
    max_requests_budget: Optional[int] = None
    request_duration_threshold_ms: Optional[float] = None
    transfer_size_threshold_bytes: Optional[int] = None
    critical_resources: list[str] = field(default_factory=list)
    optional_resources: list[str] = field(default_factory=list)
    execution_id: Optional[str] = None
    test_case_id: Optional[str] = None
    spec_id: str = field(default_factory=new_network_performance_id)
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.validate()

    def validate(self) -> None:
        validate_network_performance_id(self.spec_id)

        if self.max_requests_budget is not None:
            self.max_requests_budget = int(self.max_requests_budget)
            if self.max_requests_budget <= 0:
                raise TesterValidationError(
                    f"max_requests_budget must be strictly positive if specified, got {self.max_requests_budget}.",
                    field_name="max_requests_budget",
                )

        if self.request_duration_threshold_ms is not None:
            self.request_duration_threshold_ms = float(self.request_duration_threshold_ms)
            if self.request_duration_threshold_ms <= 0:
                raise TesterValidationError(
                    f"request_duration_threshold_ms must be strictly positive if specified, got {self.request_duration_threshold_ms}.",
                    field_name="request_duration_threshold_ms",
                )

        if self.transfer_size_threshold_bytes is not None:
            self.transfer_size_threshold_bytes = int(self.transfer_size_threshold_bytes)
            if self.transfer_size_threshold_bytes <= 0:
                raise TesterValidationError(
                    f"transfer_size_threshold_bytes must be strictly positive if specified, got {self.transfer_size_threshold_bytes}.",
                    field_name="transfer_size_threshold_bytes",
                )

        if self.execution_id:
            validate_execution_id(self.execution_id)

        if self.test_case_id:
            validate_test_case_id(self.test_case_id)

        self.critical_resources = list(self.critical_resources)
        self.optional_resources = list(self.optional_resources)
        self.metadata = dict(self.metadata)

    def to_dict(self) -> dict[str, Any]:
        return {
            "spec_id": self.spec_id,
            "max_requests_budget": self.max_requests_budget,
            "request_duration_threshold_ms": self.request_duration_threshold_ms,
            "transfer_size_threshold_bytes": self.transfer_size_threshold_bytes,
            "critical_resources": list(self.critical_resources),
            "optional_resources": list(self.optional_resources),
            "execution_id": self.execution_id,
            "test_case_id": self.test_case_id,
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> NetworkPerformanceSpec:
        budget = data.get("max_requests_budget")
        thresh = data.get("request_duration_threshold_ms")
        size_thresh = data.get("transfer_size_threshold_bytes")

        return cls(
            spec_id=str(data.get("spec_id", new_network_performance_id())),
            max_requests_budget=int(budget) if budget is not None else None,
            request_duration_threshold_ms=float(thresh) if thresh is not None else None,
            transfer_size_threshold_bytes=int(size_thresh) if size_thresh is not None else None,
            critical_resources=list(data.get("critical_resources", [])),
            optional_resources=list(data.get("optional_resources", [])),
            execution_id=data.get("execution_id"),
            test_case_id=data.get("test_case_id"),
            metadata=dict(data.get("metadata", {})),
        )

    # Invariant guards
    def apply_fix(self, *args: Any, **kwargs: Any) -> None:
        raise TesterBoundaryViolationError(
            action="PERFORMANCE_AUTO_FIX",
            reason="Network performance spec cannot modify application code or apply fixes.",
        )

    def auto_fix(self, *args: Any, **kwargs: Any) -> None:
        raise TesterBoundaryViolationError(
            action="PERFORMANCE_AUTO_FIX",
            reason="Network performance spec cannot modify application code or apply fixes.",
        )

    def create_defect(self, *args: Any, **kwargs: Any) -> None:
        raise TesterBoundaryViolationError(
            action="PERFORMANCE_DEFECT_CLASSIFICATION",
            reason="Network performance spec does not classify defects in Phase 7.4.",
        )


# ---------------------------------------------------------------------------
# Network Performance Result
# ---------------------------------------------------------------------------

@dataclass
class NetworkPerformanceResult:
    """
    Authoritative result of an authorized network and resource performance evaluation.
    Measures requests, latencies, failure impact, and explicit budgets without benchmark invention.
    """
    __test__ = False
    network_performance_id: str
    execution_id: str
    spec: NetworkPerformanceSpec
    status: NetworkPerformanceStatus = NetworkPerformanceStatus.SUCCESS
    requests: list[NetworkRequestRecord] = field(default_factory=list)
    total_requests: int = 0
    failed_requests: int = 0
    slow_requests: int = 0
    total_transfer_size_bytes: Optional[int] = None
    avg_request_duration_ms: Optional[float] = None
    max_request_duration_ms: Optional[float] = None
    request_budget_met: Optional[bool] = None
    duration_threshold_met: Optional[bool] = None
    measurements: list[PerformanceMeasurement] = field(default_factory=list)
    resource_failures: list[dict[str, Any]] = field(default_factory=list)
    error_message: Optional[str] = None
    provenance: dict[str, Any] = field(default_factory=dict)
    timestamps: dict[str, str] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.validate()

    def validate(self) -> None:
        validate_network_performance_id(self.network_performance_id)
        validate_execution_id(self.execution_id)

        if self.spec.execution_id and self.spec.execution_id != self.execution_id:
            raise TesterLineageError(
                f"NetworkPerformanceResult execution_id '{self.execution_id}' does not match spec execution_id '{self.spec.execution_id}'."
            )

        if isinstance(self.status, str):
            try:
                self.status = NetworkPerformanceStatus(self.status.upper())
            except ValueError:
                self.status = NetworkPerformanceStatus.SUCCESS

        if not self.timestamps:
            self.timestamps = {"created_at": utc_now()}

        self.requests = list(self.requests)
        self.measurements = list(self.measurements)
        self.resource_failures = list(self.resource_failures)
        self.provenance = dict(self.provenance)
        self.metadata = dict(self.metadata)

    @property
    def is_success(self) -> bool:
        return self.status == NetworkPerformanceStatus.SUCCESS

    @property
    def is_failed(self) -> bool:
        return self.status in (NetworkPerformanceStatus.FAILED, NetworkPerformanceStatus.EXCESSIVE_REQUESTS)

    @property
    def is_degraded(self) -> bool:
        return self.status == NetworkPerformanceStatus.DEGRADED

    def to_dict(self) -> dict[str, Any]:
        return {
            "network_performance_id": self.network_performance_id,
            "execution_id": self.execution_id,
            "spec": self.spec.to_dict(),
            "status": self.status.value,
            "requests": [r.to_dict() for r in self.requests],
            "total_requests": self.total_requests,
            "failed_requests": self.failed_requests,
            "slow_requests": self.slow_requests,
            "total_transfer_size_bytes": self.total_transfer_size_bytes,
            "avg_request_duration_ms": self.avg_request_duration_ms,
            "max_request_duration_ms": self.max_request_duration_ms,
            "request_budget_met": self.request_budget_met,
            "duration_threshold_met": self.duration_threshold_met,
            "measurements": [m.to_dict() for m in self.measurements],
            "resource_failures": list(self.resource_failures),
            "error_message": self.error_message,
            "provenance": dict(self.provenance),
            "timestamps": dict(self.timestamps),
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> NetworkPerformanceResult:
        spec = NetworkPerformanceSpec.from_dict(data["spec"])
        raw_status = data.get("status", NetworkPerformanceStatus.SUCCESS.value)
        try:
            status = NetworkPerformanceStatus(raw_status.upper())
        except ValueError:
            status = NetworkPerformanceStatus.SUCCESS

        requests = [NetworkRequestRecord.from_dict(r) for r in data.get("requests", [])]
        measurements = [PerformanceMeasurement.from_dict(m) for m in data.get("measurements", [])]

        avg_dur = data.get("avg_request_duration_ms")
        max_dur = data.get("max_request_duration_ms")
        tot_size = data.get("total_transfer_size_bytes")

        return cls(
            network_performance_id=str(data["network_performance_id"]),
            execution_id=str(data["execution_id"]),
            spec=spec,
            status=status,
            requests=requests,
            total_requests=int(data.get("total_requests", len(requests))),
            failed_requests=int(data.get("failed_requests", 0)),
            slow_requests=int(data.get("slow_requests", 0)),
            total_transfer_size_bytes=int(tot_size) if tot_size is not None else None,
            avg_request_duration_ms=float(avg_dur) if avg_dur is not None else None,
            max_request_duration_ms=float(max_dur) if max_dur is not None else None,
            request_budget_met=data.get("request_budget_met"),
            duration_threshold_met=data.get("duration_threshold_met"),
            measurements=measurements,
            resource_failures=list(data.get("resource_failures", [])),
            error_message=data.get("error_message"),
            provenance=dict(data.get("provenance", {})),
            timestamps=dict(data.get("timestamps", {})),
            metadata=dict(data.get("metadata", {})),
        )

    # Invariant guards
    def apply_fix(self, *args: Any, **kwargs: Any) -> None:
        raise TesterBoundaryViolationError(
            action="PERFORMANCE_AUTO_FIX",
            reason="Network performance result cannot modify application code or apply fixes.",
        )

    def auto_fix(self, *args: Any, **kwargs: Any) -> None:
        raise TesterBoundaryViolationError(
            action="PERFORMANCE_AUTO_FIX",
            reason="Network performance result cannot modify application code or apply fixes.",
        )

    def create_defect(self, *args: Any, **kwargs: Any) -> None:
        raise TesterBoundaryViolationError(
            action="PERFORMANCE_DEFECT_CLASSIFICATION",
            reason="Network performance result does not classify defects in Phase 7.4.",
        )
