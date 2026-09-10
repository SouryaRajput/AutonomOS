from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import logging
from typing import Any, Optional, Sequence

from core.tester.contracts.finding import TesterDefect, TesterFinding
from core.tester.contracts.geometry import GeometryObservation, ViewportDimensions
from core.tester.contracts.identifiers import (
    new_responsive_evaluation_id,
    validate_responsive_evaluation_id,
)
from core.tester.errors import (
    TesterBoundaryViolationError,
    TesterValidationError,
)
from core.tester.types import (
    DeviceCategory,
    ResponsiveCheckType,
    ResponsiveEvaluationStatus,
)

logger = logging.getLogger("AutonomOS.Tester.ResponsiveContracts")


def utc_now() -> str:
    """Generate ISO 8601 UTC timestamp."""
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# Bounded Standard Viewport Dimensions and Profiles
# ---------------------------------------------------------------------------

DEFAULT_MOBILE_VIEWPORT = ViewportDimensions(width=375.0, height=667.0, scale_factor=2.0)
DEFAULT_TABLET_VIEWPORT = ViewportDimensions(width=768.0, height=1024.0, scale_factor=1.0)
DEFAULT_DESKTOP_VIEWPORT = ViewportDimensions(width=1280.0, height=800.0, scale_factor=1.0)

MAX_VIEWPORT_COUNT = 10


@dataclass(frozen=True)
class ViewportProfile:
    """
    Structured profile defining a concrete viewport for responsive testing.
    """
    name: str
    category: DeviceCategory
    dimensions: ViewportDimensions
    is_touch_enabled: bool = False
    user_agent: Optional[str] = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.name or not isinstance(self.name, str):
            raise TesterValidationError(f"ViewportProfile name must be a non-empty string, got {self.name!r}.")

        if isinstance(self.category, str):
            try:
                object.__setattr__(self, "category", DeviceCategory(self.category.upper()))
            except (ValueError, KeyError):
                object.__setattr__(self, "category", DeviceCategory.CUSTOM)

        if not isinstance(self.dimensions, ViewportDimensions):
            if isinstance(self.dimensions, dict):
                object.__setattr__(self, "dimensions", ViewportDimensions.from_dict(self.dimensions))
            else:
                raise TesterValidationError(
                    f"ViewportProfile dimensions must be a ViewportDimensions instance, got {type(self.dimensions).__name__}."
                )

        if not isinstance(self.is_touch_enabled, bool):
            object.__setattr__(self, "is_touch_enabled", bool(self.is_touch_enabled))

        if not isinstance(self.metadata, dict):
            object.__setattr__(self, "metadata", dict(self.metadata) if self.metadata else {})

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "category": self.category.value,
            "dimensions": self.dimensions.to_dict(),
            "is_touch_enabled": self.is_touch_enabled,
            "user_agent": self.user_agent,
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ViewportProfile:
        if not isinstance(data, dict):
            raise TesterValidationError(f"Expected dict for ViewportProfile, got {type(data).__name__}.")

        raw_category = data.get("category", DeviceCategory.CUSTOM.value)
        try:
            category = DeviceCategory(str(raw_category).upper())
        except (ValueError, KeyError):
            category = DeviceCategory.CUSTOM

        dims_data = data.get("dimensions", {})
        dimensions = ViewportDimensions.from_dict(dims_data) if isinstance(dims_data, dict) else DEFAULT_DESKTOP_VIEWPORT

        return cls(
            name=str(data.get("name", "Unknown")),
            category=category,
            dimensions=dimensions,
            is_touch_enabled=bool(data.get("is_touch_enabled", False)),
            user_agent=data.get("user_agent"),
            metadata=dict(data.get("metadata", {})),
        )


DEFAULT_MOBILE_PROFILE = ViewportProfile(
    name="Mobile",
    category=DeviceCategory.MOBILE,
    dimensions=DEFAULT_MOBILE_VIEWPORT,
    is_touch_enabled=True,
)

DEFAULT_TABLET_PROFILE = ViewportProfile(
    name="Tablet",
    category=DeviceCategory.TABLET,
    dimensions=DEFAULT_TABLET_VIEWPORT,
    is_touch_enabled=True,
)

DEFAULT_DESKTOP_PROFILE = ViewportProfile(
    name="Desktop",
    category=DeviceCategory.DESKTOP,
    dimensions=DEFAULT_DESKTOP_VIEWPORT,
    is_touch_enabled=False,
)

DEFAULT_VIEWPORT_PROFILES = [
    DEFAULT_MOBILE_PROFILE,
    DEFAULT_DESKTOP_PROFILE,
]


# ---------------------------------------------------------------------------
# Responsive Check Specification
# ---------------------------------------------------------------------------

@dataclass
class ResponsiveCheck:
    """
    Specification of a single responsive layout check for a given viewport.
    """
    check_id: str
    check_type: ResponsiveCheckType
    viewport_profile: ViewportProfile
    target_element_id: Optional[str] = None
    container_id: Optional[str] = None
    expected_behavior: str = ""
    is_intentional_adaptation: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)
    test_case_id: Optional[str] = None
    test_step_id: Optional[str] = None

    def __post_init__(self) -> None:
        if not self.check_id:
            self.check_id = new_responsive_evaluation_id()

        if isinstance(self.check_type, str):
            try:
                self.check_type = ResponsiveCheckType(self.check_type.upper())
            except (ValueError, KeyError):
                raise TesterValidationError(f"Invalid ResponsiveCheckType: {self.check_type}")

        if isinstance(self.viewport_profile, dict):
            self.viewport_profile = ViewportProfile.from_dict(self.viewport_profile)

        self.metadata = dict(self.metadata)

    def to_dict(self) -> dict[str, Any]:
        return {
            "check_id": self.check_id,
            "check_type": self.check_type.value,
            "viewport_profile": self.viewport_profile.to_dict(),
            "target_element_id": self.target_element_id,
            "container_id": self.container_id,
            "expected_behavior": self.expected_behavior,
            "is_intentional_adaptation": self.is_intentional_adaptation,
            "metadata": dict(self.metadata),
            "test_case_id": self.test_case_id,
            "test_step_id": self.test_step_id,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ResponsiveCheck:
        if not isinstance(data, dict):
            raise TesterValidationError(f"Expected dict for ResponsiveCheck, got {type(data).__name__}.")

        raw_type = data.get("check_type", ResponsiveCheckType.CLIPPING.value)
        try:
            check_type = ResponsiveCheckType(str(raw_type).upper())
        except (ValueError, KeyError):
            check_type = ResponsiveCheckType.CLIPPING

        profile_data = data.get("viewport_profile", {})
        profile = ViewportProfile.from_dict(profile_data) if isinstance(profile_data, dict) else DEFAULT_DESKTOP_PROFILE

        return cls(
            check_id=str(data.get("check_id") or new_responsive_evaluation_id()),
            check_type=check_type,
            viewport_profile=profile,
            target_element_id=data.get("target_element_id"),
            container_id=data.get("container_id"),
            expected_behavior=str(data.get("expected_behavior", "")),
            is_intentional_adaptation=bool(data.get("is_intentional_adaptation", False)),
            metadata=dict(data.get("metadata", {})),
            test_case_id=data.get("test_case_id"),
            test_step_id=data.get("test_step_id"),
        )


# ---------------------------------------------------------------------------
# Responsive Viewport Result
# ---------------------------------------------------------------------------

@dataclass
class ResponsiveViewportResult:
    """
    Evaluation outcome for a specific viewport profile.
    """
    profile: ViewportProfile
    status: ResponsiveEvaluationStatus = ResponsiveEvaluationStatus.PASS
    checks_evaluated: int = 0
    checks_passed: int = 0
    checks_failed: int = 0
    defects: list[TesterDefect] = field(default_factory=list)
    observations: list[GeometryObservation] = field(default_factory=list)
    evidence_ids: list[str] = field(default_factory=list)
    trace: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if isinstance(self.profile, dict):
            self.profile = ViewportProfile.from_dict(self.profile)

        if isinstance(self.status, str):
            try:
                self.status = ResponsiveEvaluationStatus(self.status.upper())
            except (ValueError, KeyError):
                self.status = ResponsiveEvaluationStatus.UNVERIFIED

        self.defects = list(self.defects)
        self.observations = list(self.observations)
        self.evidence_ids = list(self.evidence_ids)
        self.trace = dict(self.trace)

    @property
    def is_pass(self) -> bool:
        return self.status == ResponsiveEvaluationStatus.PASS

    @property
    def is_fail(self) -> bool:
        return self.status == ResponsiveEvaluationStatus.FAIL

    @property
    def is_skipped(self) -> bool:
        return self.status == ResponsiveEvaluationStatus.SKIPPED

    @property
    def is_unverified(self) -> bool:
        return self.status == ResponsiveEvaluationStatus.UNVERIFIED

    def to_dict(self) -> dict[str, Any]:
        return {
            "profile": self.profile.to_dict(),
            "status": self.status.value,
            "checks_evaluated": self.checks_evaluated,
            "checks_passed": self.checks_passed,
            "checks_failed": self.checks_failed,
            "defects": [d.to_dict() if hasattr(d, "to_dict") else d for d in self.defects],
            "observations": [o.to_dict() if hasattr(o, "to_dict") else o for o in self.observations],
            "evidence_ids": list(self.evidence_ids),
            "trace": dict(self.trace),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ResponsiveViewportResult:
        if not isinstance(data, dict):
            raise TesterValidationError(f"Expected dict for ResponsiveViewportResult, got {type(data).__name__}.")

        profile_data = data.get("profile", {})
        profile = ViewportProfile.from_dict(profile_data) if isinstance(profile_data, dict) else DEFAULT_DESKTOP_PROFILE

        raw_status = data.get("status", ResponsiveEvaluationStatus.PASS.value)
        try:
            status = ResponsiveEvaluationStatus(str(raw_status).upper())
        except (ValueError, KeyError):
            status = ResponsiveEvaluationStatus.UNVERIFIED

        defects = []
        for d in data.get("defects", []):
            if isinstance(d, dict):
                defects.append(TesterDefect.from_dict(d))
            elif isinstance(d, TesterDefect):
                defects.append(d)

        observations = []
        for o in data.get("observations", []):
            if isinstance(o, dict):
                observations.append(GeometryObservation.from_dict(o))
            elif isinstance(o, GeometryObservation):
                observations.append(o)

        return cls(
            profile=profile,
            status=status,
            checks_evaluated=int(data.get("checks_evaluated", 0)),
            checks_passed=int(data.get("checks_passed", 0)),
            checks_failed=int(data.get("checks_failed", 0)),
            defects=defects,
            observations=observations,
            evidence_ids=list(data.get("evidence_ids", [])),
            trace=dict(data.get("trace", {})),
        )


# ---------------------------------------------------------------------------
# Responsive Evaluation Aggregate Result
# ---------------------------------------------------------------------------

@dataclass
class ResponsiveEvaluationResult:
    """
    Authoritative aggregate outcome of responsive layout evaluations across all evaluated viewports.
    """
    __test__ = False
    evaluation_id: str
    status: ResponsiveEvaluationStatus = ResponsiveEvaluationStatus.PASS
    viewport_results: list[ResponsiveViewportResult] = field(default_factory=list)
    defects: list[TesterDefect] = field(default_factory=list)
    findings: list[TesterFinding] = field(default_factory=list)
    total_viewports_evaluated: int = 0
    evidence_ids: list[str] = field(default_factory=list)
    test_case_id: Optional[str] = None
    test_step_id: Optional[str] = None
    evaluated_at: str = field(default_factory=utc_now)
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        validate_responsive_evaluation_id(self.evaluation_id)
        if isinstance(self.status, str):
            try:
                self.status = ResponsiveEvaluationStatus(self.status.upper())
            except (ValueError, KeyError):
                self.status = ResponsiveEvaluationStatus.UNVERIFIED

        self.viewport_results = list(self.viewport_results)
        self.defects = list(self.defects)
        self.findings = list(self.findings)
        self.evidence_ids = list(self.evidence_ids)
        self.metadata = dict(self.metadata)

    @property
    def is_pass(self) -> bool:
        return self.status == ResponsiveEvaluationStatus.PASS

    @property
    def is_fail(self) -> bool:
        return self.status == ResponsiveEvaluationStatus.FAIL

    @property
    def is_skipped(self) -> bool:
        return self.status == ResponsiveEvaluationStatus.SKIPPED

    @property
    def is_unverified(self) -> bool:
        return self.status == ResponsiveEvaluationStatus.UNVERIFIED

    def apply_fix(self, *args, **kwargs) -> Any:
        """Zero fixing guard: Responsive evaluation does NOT modify source code or attempt auto repairs."""
        raise TesterBoundaryViolationError(
            action="RESPONSIVE_AUTO_FIX",
            reason=(
                f"ResponsiveEvaluationResult '{self.evaluation_id}' is an evaluation object. "
                "Tester does not perform automatic fixes on product or source code."
            ),
        )

    def auto_fix(self, *args, **kwargs) -> Any:
        return self.apply_fix(*args, **kwargs)

    def to_dict(self) -> dict[str, Any]:
        return {
            "evaluation_id": self.evaluation_id,
            "status": self.status.value,
            "viewport_results": [vr.to_dict() if hasattr(vr, "to_dict") else vr for vr in self.viewport_results],
            "defects": [d.to_dict() if hasattr(d, "to_dict") else d for d in self.defects],
            "findings": [f.to_dict() if hasattr(f, "to_dict") else f for f in self.findings],
            "total_viewports_evaluated": self.total_viewports_evaluated,
            "evidence_ids": list(self.evidence_ids),
            "test_case_id": self.test_case_id,
            "test_step_id": self.test_step_id,
            "evaluated_at": self.evaluated_at,
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ResponsiveEvaluationResult:
        if not isinstance(data, dict):
            raise TesterValidationError(f"Expected dict for ResponsiveEvaluationResult, got {type(data).__name__}.")

        raw_status = data.get("status", ResponsiveEvaluationStatus.PASS.value)
        try:
            status = ResponsiveEvaluationStatus(str(raw_status).upper())
        except (ValueError, KeyError):
            status = ResponsiveEvaluationStatus.UNVERIFIED

        viewport_results = []
        for vr in data.get("viewport_results", []):
            if isinstance(vr, dict):
                viewport_results.append(ResponsiveViewportResult.from_dict(vr))
            elif isinstance(vr, ResponsiveViewportResult):
                viewport_results.append(vr)

        defects = []
        for d in data.get("defects", []):
            if isinstance(d, dict):
                defects.append(TesterDefect.from_dict(d))
            elif isinstance(d, TesterDefect):
                defects.append(d)

        findings = []
        for f in data.get("findings", []):
            if isinstance(f, dict):
                findings.append(TesterFinding.from_dict(f))
            elif isinstance(f, TesterFinding):
                findings.append(f)

        return cls(
            evaluation_id=str(data.get("evaluation_id", "")),
            status=status,
            viewport_results=viewport_results,
            defects=defects,
            findings=findings,
            total_viewports_evaluated=int(data.get("total_viewports_evaluated", len(viewport_results))),
            evidence_ids=list(data.get("evidence_ids", [])),
            test_case_id=data.get("test_case_id"),
            test_step_id=data.get("test_step_id"),
            evaluated_at=str(data.get("evaluated_at", utc_now())),
            metadata=dict(data.get("metadata", {})),
        )
