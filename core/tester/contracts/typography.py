from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import logging
from typing import Any, Optional, Sequence

from core.tester.contracts.finding import TesterDefect, TesterFinding
from core.tester.contracts.geometry import Rectangle
from core.tester.contracts.identifiers import (
    new_typography_evaluation_id,
    validate_typography_evaluation_id,
)
from core.tester.errors import (
    TesterBoundaryViolationError,
    TesterValidationError,
)
from core.tester.types import (
    TypographyCheckType,
    TypographyEvaluationStatus,
)

logger = logging.getLogger("AutonomOS.Tester.TypographyContracts")


def utc_now() -> str:
    """Generate ISO 8601 UTC timestamp."""
    return datetime.now(timezone.utc).isoformat()


# Threshold below which OCR readings are considered non-authoritative (returns NOT_VERIFIED)
MIN_OCR_CONFIDENCE = 0.6


# ---------------------------------------------------------------------------
# Text Requirement Specification
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class TextRequirement:
    """
    Explicit text requirement extracted from a TestCase or acceptance criterion.
    """
    requirement_id: str
    text_pattern: str
    is_exact_match: bool = False
    is_case_sensitive: bool = False
    expected_container_id: Optional[str] = None
    expected_region: Optional[Rectangle] = None
    max_lines: Optional[int] = None
    allow_wrapping: bool = True
    test_case_id: Optional[str] = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.requirement_id or not isinstance(self.requirement_id, str):
            raise TesterValidationError("TextRequirement requirement_id must be a non-empty string.")
        if not self.text_pattern or not isinstance(self.text_pattern, str):
            raise TesterValidationError("TextRequirement text_pattern must be a non-empty string.")

        if self.expected_region is not None and not isinstance(self.expected_region, Rectangle):
            if isinstance(self.expected_region, dict):
                object.__setattr__(self, "expected_region", Rectangle.from_dict(self.expected_region))
            else:
                raise TesterValidationError("TextRequirement expected_region must be a Rectangle instance.")

        if not isinstance(self.metadata, dict):
            object.__setattr__(self, "metadata", dict(self.metadata) if self.metadata else {})

    def to_dict(self) -> dict[str, Any]:
        return {
            "requirement_id": self.requirement_id,
            "text_pattern": self.text_pattern,
            "is_exact_match": self.is_exact_match,
            "is_case_sensitive": self.is_case_sensitive,
            "expected_container_id": self.expected_container_id,
            "expected_region": self.expected_region.to_dict() if self.expected_region else None,
            "max_lines": self.max_lines,
            "allow_wrapping": self.allow_wrapping,
            "test_case_id": self.test_case_id,
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> TextRequirement:
        if not isinstance(data, dict):
            raise TesterValidationError(f"Expected dict for TextRequirement, got {type(data).__name__}.")

        region_data = data.get("expected_region")
        region = Rectangle.from_dict(region_data) if isinstance(region_data, dict) else None

        return cls(
            requirement_id=str(data.get("requirement_id", "")),
            text_pattern=str(data.get("text_pattern", "")),
            is_exact_match=bool(data.get("is_exact_match", False)),
            is_case_sensitive=bool(data.get("is_case_sensitive", False)),
            expected_container_id=data.get("expected_container_id"),
            expected_region=region,
            max_lines=int(data["max_lines"]) if data.get("max_lines") is not None else None,
            allow_wrapping=bool(data.get("allow_wrapping", True)),
            test_case_id=data.get("test_case_id"),
            metadata=dict(data.get("metadata", {})),
        )


# ---------------------------------------------------------------------------
# Typography Assertion Specification
# ---------------------------------------------------------------------------

@dataclass
class TypographyAssertion:
    """
    Specification of a single typography or text presentation check evaluated in Tester V1.
    """
    __test__ = False
    assertion_id: str = field(default_factory=new_typography_evaluation_id)
    check_type: TypographyCheckType = TypographyCheckType.REQUIRED_TEXT_MISSING
    target_element_id: Optional[str] = None
    required_text: Optional[str] = None
    expected_container_id: Optional[str] = None
    expected_region: Optional[Rectangle] = None
    allow_wrapping: bool = True
    max_lines: Optional[int] = None
    min_confidence: float = MIN_OCR_CONFIDENCE
    test_case_id: Optional[str] = None
    test_step_id: Optional[str] = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        validate_typography_evaluation_id(self.assertion_id)
        if isinstance(self.check_type, str):
            try:
                self.check_type = TypographyCheckType(self.check_type.upper())
            except (ValueError, KeyError):
                self.check_type = TypographyCheckType.REQUIRED_TEXT_MISSING

        if self.expected_region is not None and not isinstance(self.expected_region, Rectangle):
            if isinstance(self.expected_region, dict):
                self.expected_region = Rectangle.from_dict(self.expected_region)

        self.metadata = dict(self.metadata)

    def to_dict(self) -> dict[str, Any]:
        return {
            "assertion_id": self.assertion_id,
            "check_type": self.check_type.value,
            "target_element_id": self.target_element_id,
            "required_text": self.required_text,
            "expected_container_id": self.expected_container_id,
            "expected_region": self.expected_region.to_dict() if self.expected_region else None,
            "allow_wrapping": self.allow_wrapping,
            "max_lines": self.max_lines,
            "min_confidence": float(self.min_confidence),
            "test_case_id": self.test_case_id,
            "test_step_id": self.test_step_id,
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> TypographyAssertion:
        if not isinstance(data, dict):
            raise TesterValidationError(f"Expected dict for TypographyAssertion, got {type(data).__name__}.")

        raw_type = data.get("check_type", TypographyCheckType.REQUIRED_TEXT_MISSING.value)
        try:
            check_type = TypographyCheckType(str(raw_type).upper())
        except (ValueError, KeyError):
            check_type = TypographyCheckType.REQUIRED_TEXT_MISSING

        region_data = data.get("expected_region")
        region = Rectangle.from_dict(region_data) if isinstance(region_data, dict) else None

        return cls(
            assertion_id=str(data.get("assertion_id") or new_typography_evaluation_id()),
            check_type=check_type,
            target_element_id=data.get("target_element_id"),
            required_text=data.get("required_text"),
            expected_container_id=data.get("expected_container_id"),
            expected_region=region,
            allow_wrapping=bool(data.get("allow_wrapping", True)),
            max_lines=int(data["max_lines"]) if data.get("max_lines") is not None else None,
            min_confidence=float(data.get("min_confidence", MIN_OCR_CONFIDENCE)),
            test_case_id=data.get("test_case_id"),
            test_step_id=data.get("test_step_id"),
            metadata=dict(data.get("metadata", {})),
        )


# ---------------------------------------------------------------------------
# Typography Assertion Result
# ---------------------------------------------------------------------------

@dataclass
class TypographyAssertionResult:
    """
    Authoritative outcome of a single typography or text presentation check.
    """
    __test__ = False
    assertion_id: str
    check_type: TypographyCheckType
    status: TypographyEvaluationStatus = TypographyEvaluationStatus.PASS
    target_element_id: Optional[str] = None
    detected_text: Optional[str] = None
    measured_values: dict[str, Any] = field(default_factory=dict)
    ocr_confidence: Optional[float] = None
    description: str = ""
    defect: Optional[TesterDefect] = None
    recommendation: Optional[TesterFinding] = None
    evidence_ids: list[str] = field(default_factory=list)
    test_case_id: Optional[str] = None
    test_step_id: Optional[str] = None
    evaluated_at: str = field(default_factory=utc_now)

    def __post_init__(self) -> None:
        if isinstance(self.status, str):
            try:
                self.status = TypographyEvaluationStatus(self.status.upper())
            except (ValueError, KeyError):
                self.status = TypographyEvaluationStatus.NOT_VERIFIED

        if isinstance(self.check_type, str):
            try:
                self.check_type = TypographyCheckType(self.check_type.upper())
            except (ValueError, KeyError):
                self.check_type = TypographyCheckType.REQUIRED_TEXT_MISSING

        self.measured_values = dict(self.measured_values)
        self.evidence_ids = list(self.evidence_ids)

    @property
    def is_pass(self) -> bool:
        return self.status == TypographyEvaluationStatus.PASS

    @property
    def is_fail(self) -> bool:
        return self.status == TypographyEvaluationStatus.FAIL

    @property
    def is_not_verified(self) -> bool:
        return self.status == TypographyEvaluationStatus.NOT_VERIFIED

    @property
    def is_skipped(self) -> bool:
        return self.status == TypographyEvaluationStatus.SKIPPED

    def to_dict(self) -> dict[str, Any]:
        return {
            "assertion_id": self.assertion_id,
            "check_type": self.check_type.value,
            "status": self.status.value,
            "target_element_id": self.target_element_id,
            "detected_text": self.detected_text,
            "measured_values": dict(self.measured_values),
            "ocr_confidence": self.ocr_confidence,
            "description": self.description,
            "defect": self.defect.to_dict() if self.defect and hasattr(self.defect, "to_dict") else None,
            "recommendation": self.recommendation.to_dict() if self.recommendation and hasattr(self.recommendation, "to_dict") else None,
            "evidence_ids": list(self.evidence_ids),
            "test_case_id": self.test_case_id,
            "test_step_id": self.test_step_id,
            "evaluated_at": self.evaluated_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> TypographyAssertionResult:
        if not isinstance(data, dict):
            raise TesterValidationError(f"Expected dict for TypographyAssertionResult, got {type(data).__name__}.")

        raw_status = data.get("status", TypographyEvaluationStatus.PASS.value)
        try:
            status = TypographyEvaluationStatus(str(raw_status).upper())
        except (ValueError, KeyError):
            status = TypographyEvaluationStatus.NOT_VERIFIED

        raw_type = data.get("check_type", TypographyCheckType.REQUIRED_TEXT_MISSING.value)
        try:
            check_type = TypographyCheckType(str(raw_type).upper())
        except (ValueError, KeyError):
            check_type = TypographyCheckType.REQUIRED_TEXT_MISSING

        defect_data = data.get("defect")
        defect_obj = TesterDefect.from_dict(defect_data) if isinstance(defect_data, dict) else None

        rec_data = data.get("recommendation")
        rec_obj = TesterFinding.from_dict(rec_data) if isinstance(rec_data, dict) else None

        return cls(
            assertion_id=str(data.get("assertion_id", "")),
            check_type=check_type,
            status=status,
            target_element_id=data.get("target_element_id"),
            detected_text=data.get("detected_text"),
            measured_values=dict(data.get("measured_values", {})),
            ocr_confidence=float(data["ocr_confidence"]) if data.get("ocr_confidence") is not None else None,
            description=str(data.get("description", "")),
            defect=defect_obj,
            recommendation=rec_obj,
            evidence_ids=list(data.get("evidence_ids", [])),
            test_case_id=data.get("test_case_id"),
            test_step_id=data.get("test_step_id"),
            evaluated_at=str(data.get("evaluated_at", utc_now())),
        )


# ---------------------------------------------------------------------------
# Typography Evaluation Aggregate Result
# ---------------------------------------------------------------------------

@dataclass
class TypographyEvaluationResult:
    """
    Authoritative aggregate outcome of typography and text presentation evaluations.
    """
    __test__ = False
    evaluation_id: str
    status: TypographyEvaluationStatus = TypographyEvaluationStatus.PASS
    assertion_results: list[TypographyAssertionResult] = field(default_factory=list)
    defects: list[TesterDefect] = field(default_factory=list)
    findings: list[TesterFinding] = field(default_factory=list)
    evidence_ids: list[str] = field(default_factory=list)
    test_case_id: Optional[str] = None
    test_step_id: Optional[str] = None
    evaluated_at: str = field(default_factory=utc_now)
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        validate_typography_evaluation_id(self.evaluation_id)
        if isinstance(self.status, str):
            try:
                self.status = TypographyEvaluationStatus(self.status.upper())
            except (ValueError, KeyError):
                self.status = TypographyEvaluationStatus.NOT_VERIFIED

        self.assertion_results = list(self.assertion_results)
        self.defects = list(self.defects)
        self.findings = list(self.findings)
        self.evidence_ids = list(self.evidence_ids)
        self.metadata = dict(self.metadata)

    @property
    def is_pass(self) -> bool:
        return self.status == TypographyEvaluationStatus.PASS

    @property
    def is_fail(self) -> bool:
        return self.status == TypographyEvaluationStatus.FAIL

    @property
    def is_not_verified(self) -> bool:
        return self.status == TypographyEvaluationStatus.NOT_VERIFIED

    @property
    def is_skipped(self) -> bool:
        return self.status == TypographyEvaluationStatus.SKIPPED

    def apply_fix(self, *args, **kwargs) -> Any:
        """Zero fixing guard: Typography evaluation does NOT modify source code or attempt auto repairs."""
        raise TesterBoundaryViolationError(
            action="TYPOGRAPHY_AUTO_FIX",
            reason=(
                f"TypographyEvaluationResult '{self.evaluation_id}' is an evaluation object. "
                "Tester does not perform automatic fixes on product or source code."
            ),
        )

    def auto_fix(self, *args, **kwargs) -> Any:
        return self.apply_fix(*args, **kwargs)

    def to_dict(self) -> dict[str, Any]:
        return {
            "evaluation_id": self.evaluation_id,
            "status": self.status.value,
            "assertion_results": [ar.to_dict() if hasattr(ar, "to_dict") else ar for ar in self.assertion_results],
            "defects": [d.to_dict() if hasattr(d, "to_dict") else d for d in self.defects],
            "findings": [f.to_dict() if hasattr(f, "to_dict") else f for f in self.findings],
            "evidence_ids": list(self.evidence_ids),
            "test_case_id": self.test_case_id,
            "test_step_id": self.test_step_id,
            "evaluated_at": self.evaluated_at,
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> TypographyEvaluationResult:
        if not isinstance(data, dict):
            raise TesterValidationError(f"Expected dict for TypographyEvaluationResult, got {type(data).__name__}.")

        raw_status = data.get("status", TypographyEvaluationStatus.PASS.value)
        try:
            status = TypographyEvaluationStatus(str(raw_status).upper())
        except (ValueError, KeyError):
            status = TypographyEvaluationStatus.NOT_VERIFIED

        assertions = []
        for ar in data.get("assertion_results", []):
            if isinstance(ar, dict):
                assertions.append(TypographyAssertionResult.from_dict(ar))
            elif isinstance(ar, TypographyAssertionResult):
                assertions.append(ar)

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
            assertion_results=assertions,
            defects=defects,
            findings=findings,
            evidence_ids=list(data.get("evidence_ids", [])),
            test_case_id=data.get("test_case_id"),
            test_step_id=data.get("test_step_id"),
            evaluated_at=str(data.get("evaluated_at", utc_now())),
            metadata=dict(data.get("metadata", {})),
        )
