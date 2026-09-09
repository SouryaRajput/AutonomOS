from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import logging
import math
from typing import Any, Optional, Sequence, Union

from core.tester.contracts.finding import TesterEvidence
from core.tester.contracts.identifiers import (
    new_geometry_id,
    new_observation_id,
    validate_evidence_id,
    validate_execution_id,
    validate_geometry_id,
    validate_observation_id,
)
from core.tester.contracts.observation import TesterObservation
from core.tester.errors import (
    TesterBoundaryViolationError,
    TesterLineageError,
    TesterValidationError,
)
from core.tester.types import (
    CoordinateSystem,
    GeometryStatus,
    ObservationType,
    VisibilityState,
)

logger = logging.getLogger("AutonomOS.Tester.Geometry")


def utc_now() -> str:
    """Generate ISO 8601 UTC timestamp."""
    return datetime.now(timezone.utc).isoformat()


# Forbidden evaluative keys to enforce the strict descriptive boundary
FORBIDDEN_EVALUATIVE_KEYS = {
    "is_defect",
    "defect",
    "is_failure",
    "verdict",
    "pass_fail",
    "passed",
    "failed",
    "defect_severity",
    "ux_score",
    "aesthetic_score",
    "visual_quality",
}


# ---------------------------------------------------------------------------
# Point & Rectangle Primitive Geometric Structures
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Point:
    """
    2D coordinate representing a spatial location.
    Coordinates must be finite real numbers.
    """
    x: float
    y: float

    def __post_init__(self) -> None:
        for name, val in (("x", self.x), ("y", self.y)):
            if not isinstance(val, (int, float)) or isinstance(val, bool):
                raise TesterValidationError(
                    f"Point coordinate '{name}' must be a numeric number, got {type(val).__name__}."
                )
            if not math.isfinite(val):
                raise TesterValidationError(
                    f"Point coordinate '{name}' must be a finite number, got {val}."
                )

    def distance_to(self, other: Point) -> float:
        """Calculate Euclidean distance to another Point."""
        if not isinstance(other, Point):
            raise TesterValidationError(f"Expected Point instance, got {type(other).__name__}.")
        return math.hypot(self.x - other.x, self.y - other.y)

    def to_dict(self) -> dict[str, float]:
        return {"x": float(self.x), "y": float(self.y)}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Point:
        if not isinstance(data, dict):
            raise TesterValidationError(f"Expected dict for Point, got {type(data).__name__}.")
        if "x" not in data or "y" not in data:
            raise TesterValidationError("Point dict must contain both 'x' and 'y'.")
        return cls(x=float(data["x"]), y=float(data["y"]))


@dataclass(frozen=True)
class Rectangle:
    """
    2D bounding rectangle representing spatial bounds and surface dimensions.
    Coordinates (x, y) represent the top-left corner. Width and height must be non-negative.
    """
    x: float
    y: float
    width: float
    height: float

    def __post_init__(self) -> None:
        for name, val in (("x", self.x), ("y", self.y), ("width", self.width), ("height", self.height)):
            if not isinstance(val, (int, float)) or isinstance(val, bool):
                raise TesterValidationError(
                    f"Rectangle '{name}' must be a numeric number, got {type(val).__name__}."
                )
            if not math.isfinite(val):
                raise TesterValidationError(
                    f"Rectangle '{name}' must be a finite number, got {val}."
                )
        if self.width < 0 or self.height < 0:
            raise TesterValidationError(
                f"Rectangle width and height must be non-negative, got width={self.width}, height={self.height}."
            )

    @property
    def left(self) -> float:
        return float(self.x)

    @property
    def top(self) -> float:
        return float(self.y)

    @property
    def right(self) -> float:
        return float(self.x + self.width)

    @property
    def bottom(self) -> float:
        return float(self.y + self.height)

    @property
    def center(self) -> Point:
        return Point(x=self.x + self.width / 2.0, y=self.y + self.height / 2.0)

    @property
    def area(self) -> float:
        return float(self.width * self.height)

    def contains_point(self, point: Point) -> bool:
        """Return True if the point lies within or on the boundaries of this rectangle."""
        if not isinstance(point, Point):
            raise TesterValidationError(f"Expected Point instance, got {type(point).__name__}.")
        return self.left <= point.x <= self.right and self.top <= point.y <= self.bottom

    def contains_rect(self, other: Rectangle) -> bool:
        """Return True if other rectangle is entirely enclosed within this rectangle."""
        if not isinstance(other, Rectangle):
            raise TesterValidationError(f"Expected Rectangle instance, got {type(other).__name__}.")
        return (
            self.left <= other.left
            and other.right <= self.right
            and self.top <= other.top
            and other.bottom <= self.bottom
        )

    def intersects(self, other: Rectangle) -> bool:
        """Return True if this rectangle overlaps or intersects another rectangle."""
        if not isinstance(other, Rectangle):
            raise TesterValidationError(f"Expected Rectangle instance, got {type(other).__name__}.")
        return not (
            self.right < other.left
            or self.left > other.right
            or self.bottom < other.top
            or self.top > other.bottom
        )

    def intersection(self, other: Rectangle) -> Optional[Rectangle]:
        """Compute the overlapping intersection rectangle, or None if no overlap exists."""
        if not self.intersects(other):
            return None
        ix = max(self.left, other.left)
        iy = max(self.top, other.top)
        iw = max(0.0, min(self.right, other.right) - ix)
        ih = max(0.0, min(self.bottom, other.bottom) - iy)
        if iw == 0.0 or ih == 0.0:
            return None
        return Rectangle(x=ix, y=iy, width=iw, height=ih)

    def distance_to(self, other: Rectangle) -> float:
        """
        Calculate the minimum Euclidean edge-to-edge distance between two rectangles.
        Returns 0.0 if the rectangles overlap or touch.
        """
        if not isinstance(other, Rectangle):
            raise TesterValidationError(f"Expected Rectangle instance, got {type(other).__name__}.")
        dx = max(0.0, max(self.left - other.right, other.left - self.right))
        dy = max(0.0, max(self.top - other.bottom, other.top - self.bottom))
        return math.hypot(dx, dy)

    def normalize(self, viewport_width: float, viewport_height: float) -> Rectangle:
        """
        Transform pixel coordinates into normalized [0.0, 1.0] coordinates relative to viewport.
        """
        if viewport_width <= 0 or viewport_height <= 0:
            raise TesterValidationError(
                f"Viewport dimensions must be strictly positive for normalization, got {viewport_width}x{viewport_height}."
            )
        return Rectangle(
            x=self.x / viewport_width,
            y=self.y / viewport_height,
            width=self.width / viewport_width,
            height=self.height / viewport_height,
        )

    def denormalize(self, viewport_width: float, viewport_height: float) -> Rectangle:
        """
        Transform normalized [0.0, 1.0] coordinates back to absolute pixel coordinates.
        """
        if viewport_width <= 0 or viewport_height <= 0:
            raise TesterValidationError(
                f"Viewport dimensions must be strictly positive for denormalization, got {viewport_width}x{viewport_height}."
            )
        return Rectangle(
            x=self.x * viewport_width,
            y=self.y * viewport_height,
            width=self.width * viewport_width,
            height=self.height * viewport_height,
        )

    def to_dict(self) -> dict[str, float]:
        return {
            "x": float(self.x),
            "y": float(self.y),
            "width": float(self.width),
            "height": float(self.height),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Rectangle:
        if not isinstance(data, dict):
            raise TesterValidationError(f"Expected dict for Rectangle, got {type(data).__name__}.")
        for key in ("x", "y", "width", "height"):
            if key not in data:
                raise TesterValidationError(f"Missing required Rectangle field '{key}'.")
        return cls(
            x=float(data["x"]),
            y=float(data["y"]),
            width=float(data["width"]),
            height=float(data["height"]),
        )

    @classmethod
    def from_bounding_box(cls, box: Any) -> Rectangle:
        """Convert BoundingBox dataclass or dict to a Rectangle."""
        if isinstance(box, Rectangle):
            return box
        if hasattr(box, "x") and hasattr(box, "y") and hasattr(box, "width") and hasattr(box, "height"):
            return cls(x=float(box.x), y=float(box.y), width=float(box.width), height=float(box.height))
        if isinstance(box, dict):
            return cls.from_dict(box)
        raise TesterValidationError(f"Cannot convert {type(box).__name__} to Rectangle.")


# ---------------------------------------------------------------------------
# Viewport Dimensions Model
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ViewportDimensions:
    """
    Observable viewport display dimensions and device pixel scale factor.
    """
    width: float
    height: float
    scale_factor: float = 1.0

    def __post_init__(self) -> None:
        for name, val in (("width", self.width), ("height", self.height), ("scale_factor", self.scale_factor)):
            if not isinstance(val, (int, float)) or isinstance(val, bool):
                raise TesterValidationError(
                    f"ViewportDimensions '{name}' must be a numeric number, got {type(val).__name__}."
                )
            if not math.isfinite(val):
                raise TesterValidationError(
                    f"ViewportDimensions '{name}' must be finite, got {val}."
                )
        if self.width <= 0 or self.height <= 0:
            raise TesterValidationError(
                f"Viewport width and height must be strictly positive, got {self.width}x{self.height}."
            )
        if self.scale_factor <= 0:
            raise TesterValidationError(
                f"Viewport scale_factor must be positive, got {self.scale_factor}."
            )

    def to_dict(self) -> dict[str, float]:
        return {
            "width": float(self.width),
            "height": float(self.height),
            "scale_factor": float(self.scale_factor),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ViewportDimensions:
        if not isinstance(data, dict):
            raise TesterValidationError(f"Expected dict for ViewportDimensions, got {type(data).__name__}.")
        if "width" not in data or "height" not in data:
            raise TesterValidationError("ViewportDimensions dict requires 'width' and 'height'.")
        return cls(
            width=float(data["width"]),
            height=float(data["height"]),
            scale_factor=float(data.get("scale_factor", 1.0)),
        )


# ---------------------------------------------------------------------------
# Spatial Relationship Record (Measurable Facts Only)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class SpatialRelation:
    """
    Factual, measurable spatial relationship between two observed elements/regions.
    
    Contains:
    - distance: edge-to-edge distance (0.0 if overlapping)
    - overlaps: whether the two rectangles intersect
    - overlap_area: surface area of intersection (px² or normalized units)
    - overlap_rect: intersection rectangle geometry if overlapping
    - a_contains_b: whether element A fully encloses element B
    - b_contains_a: whether element B fully encloses element A
    - a_overflows_b: whether element A extends beyond container B's boundary
    - overflow_distance: maximum distance element A extends outside B
    """
    element_a: str
    element_b: str
    distance: float
    overlaps: bool
    overlap_area: float
    overlap_rect: Optional[Rectangle] = None
    a_contains_b: bool = False
    b_contains_a: bool = False
    a_overflows_b: bool = False
    overflow_distance: float = 0.0

    def description(self) -> str:
        """
        Generate a purely descriptive, non-evaluative statement of spatial facts.
        Strictly forbids semantic judgments (e.g., 'defect', 'bad design', 'broken UI').
        """
        parts = []
        if self.overlaps:
            parts.append(f"Region '{self.element_a}' overlaps region '{self.element_b}' by {self.overlap_area:.1f} area units.")
        else:
            parts.append(f"Distance between '{self.element_a}' and '{self.element_b}' is {self.distance:.1f} units.")

        if self.a_contains_b:
            parts.append(f"Region '{self.element_a}' completely contains '{self.element_b}'.")
        elif self.b_contains_a:
            parts.append(f"Region '{self.element_b}' completely contains '{self.element_a}'.")

        if self.a_overflows_b:
            parts.append(f"Region '{self.element_a}' extends {self.overflow_distance:.1f} units beyond bounds of '{self.element_b}'.")

        return " ".join(parts)

    def to_dict(self) -> dict[str, Any]:
        return {
            "element_a": self.element_a,
            "element_b": self.element_b,
            "distance": float(self.distance),
            "overlaps": bool(self.overlaps),
            "overlap_area": float(self.overlap_area),
            "overlap_rect": self.overlap_rect.to_dict() if self.overlap_rect else None,
            "a_contains_b": bool(self.a_contains_b),
            "b_contains_a": bool(self.b_contains_a),
            "a_overflows_b": bool(self.a_overflows_b),
            "overflow_distance": float(self.overflow_distance),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SpatialRelation:
        overlap_r = data.get("overlap_rect")
        if isinstance(overlap_r, dict):
            overlap_r = Rectangle.from_dict(overlap_r)
        return cls(
            element_a=str(data["element_a"]),
            element_b=str(data["element_b"]),
            distance=float(data["distance"]),
            overlaps=bool(data["overlaps"]),
            overlap_area=float(data["overlap_area"]),
            overlap_rect=overlap_r,
            a_contains_b=bool(data.get("a_contains_b", False)),
            b_contains_a=bool(data.get("b_contains_a", False)),
            a_overflows_b=bool(data.get("a_overflows_b", False)),
            overflow_distance=float(data.get("overflow_distance", 0.0)),
        )


# ---------------------------------------------------------------------------
# Geometry Observation Record
# ---------------------------------------------------------------------------

@dataclass
class GeometryObservation:
    """
    Lightweight, factual observation of visual geometry, layout boundaries, and spatial facts.
    
    Invariants:
    - Purely descriptive: records MEASURED FACTS, never infers semantic correctness or defects.
    - Zero fabrication: if coordinates cannot be reliably extracted, records status UNAVAILABLE/UNKNOWN.
    - Evaluation rejection: strictly rejects evaluative APIs (to_defect, to_finding) and evaluative keys.
    - Preserves lineage: links to source_evidence_id, execution_id, and tenant project_id.
    """
    __test__ = False
    geometry_id: str
    execution_id: str
    project_id: str
    source_evidence_id: Optional[str] = None
    observation_id: Optional[str] = None
    test_case_id: Optional[str] = None
    test_step_id: Optional[str] = None
    runtime_id: Optional[str] = None
    element_id: Optional[str] = None
    bounding_box: Optional[Rectangle] = None
    viewport: Optional[ViewportDimensions] = None
    coordinate_system: CoordinateSystem = CoordinateSystem.VIEWPORT
    visibility_state: VisibilityState = VisibilityState.UNKNOWN
    status: GeometryStatus = GeometryStatus.AVAILABLE
    confidence: float = 1.0
    spatial_relation: Optional[SpatialRelation] = None
    measurements: dict[str, Any] = field(default_factory=dict)
    provenance: dict[str, Any] = field(default_factory=dict)
    timestamp: str = field(default_factory=utc_now)
    trace: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        # 1. Identifier & Lineage Validation
        validate_geometry_id(self.geometry_id)
        validate_execution_id(self.execution_id)
        if not self.project_id or not str(self.project_id).strip():
            raise TesterLineageError("GeometryObservation must have a valid non-empty project_id.")
        if self.source_evidence_id is not None:
            validate_evidence_id(self.source_evidence_id)
        if self.observation_id is not None:
            validate_observation_id(self.observation_id)

        # 2. Type Normalization
        if isinstance(self.coordinate_system, str):
            try:
                self.coordinate_system = CoordinateSystem(self.coordinate_system.upper())
            except (ValueError, KeyError):
                self.coordinate_system = CoordinateSystem.VIEWPORT

        if isinstance(self.visibility_state, str):
            try:
                self.visibility_state = VisibilityState(self.visibility_state.upper())
            except (ValueError, KeyError):
                self.visibility_state = VisibilityState.UNKNOWN

        if isinstance(self.status, str):
            try:
                self.status = GeometryStatus(self.status.upper())
            except (ValueError, KeyError):
                self.status = GeometryStatus.AVAILABLE

        # 3. Geometry structures normalization
        if isinstance(self.bounding_box, dict):
            self.bounding_box = Rectangle.from_dict(self.bounding_box)
        if isinstance(self.viewport, dict):
            self.viewport = ViewportDimensions.from_dict(self.viewport)
        if isinstance(self.spatial_relation, dict):
            self.spatial_relation = SpatialRelation.from_dict(self.spatial_relation)

        # 4. Confidence bounds
        if not isinstance(self.confidence, (int, float)):
            raise TesterValidationError(f"Confidence must be a number, got {type(self.confidence).__name__}.")
        if not (0.0 <= float(self.confidence) <= 1.0):
            raise TesterValidationError(f"Confidence must be between 0.0 and 1.0, got {self.confidence}.")

        # 5. Guard against evaluative judgments
        for forbidden in FORBIDDEN_EVALUATIVE_KEYS:
            for container_name, container in (
                ("measurements", self.measurements),
                ("provenance", self.provenance),
                ("metadata", self.metadata),
            ):
                if forbidden in container:
                    raise TesterBoundaryViolationError(
                        action="GEOMETRY_EVALUATION",
                        reason=(
                            f"GeometryObservation contains forbidden evaluative key '{forbidden}' in {container_name}. "
                            "Geometry is an observation mechanism and cannot perform evaluation or defect detection."
                        ),
                    )

    def to_observation(self, description: str = "") -> TesterObservation:
        """
        Bind this GeometryObservation into a factual Phase 4.1 TesterObservation (type=GEOMETRY).
        """
        obs_id = new_observation_id()
        source_name = str(self.provenance.get("source", "geometry_observer"))

        # Build descriptive factual narrative
        if description:
            desc = description
        elif self.status == GeometryStatus.AVAILABLE and self.bounding_box:
            elem_desc = f"for '{self.element_id}'" if self.element_id else ""
            desc = (
                f"Geometry observed {elem_desc} at ({self.bounding_box.x:.1f}, {self.bounding_box.y:.1f}) "
                f"with dimensions {self.bounding_box.width:.1f}x{self.bounding_box.height:.1f} "
                f"[{self.coordinate_system.value}]."
            )
            if self.spatial_relation:
                desc += " " + self.spatial_relation.description()
        elif self.status in {GeometryStatus.UNAVAILABLE, GeometryStatus.UNKNOWN}:
            elem_desc = f" for '{self.element_id}'" if self.element_id else ""
            desc = f"Geometry observation{elem_desc} is {self.status.value} (no reliable spatial source available)."
        else:
            desc = f"Geometry observation completed with status {self.status.value}."

        state = {
            "geometry_id": self.geometry_id,
            "element_id": self.element_id,
            "status": self.status.value,
            "bounding_box": self.bounding_box.to_dict() if self.bounding_box else None,
            "bounds": self.bounding_box.to_dict() if self.bounding_box else None,
            "viewport": self.viewport.to_dict() if self.viewport else None,
            "coordinate_system": self.coordinate_system.value,
            "visibility_state": self.visibility_state.value,
            "confidence": float(self.confidence),
            "spatial_relation": self.spatial_relation.to_dict() if self.spatial_relation else None,
            "measurements": dict(self.measurements),
        }

        provenance = {
            "evidence_type": "GEOMETRY",
            "geometry_id": self.geometry_id,
            "source_evidence_id": self.source_evidence_id,
            "source": source_name,
            "coordinate_system": self.coordinate_system.value,
            "trace": dict(self.trace),
        }

        ev_ids = [self.source_evidence_id] if self.source_evidence_id else []

        obs = TesterObservation(
            observation_id=obs_id,
            execution_id=self.execution_id,
            project_id=self.project_id,
            test_case_id=self.test_case_id,
            test_step_id=self.test_step_id,
            runtime_id=self.runtime_id,
            observation_type=ObservationType.GEOMETRY,
            description=desc,
            observed_state=state,
            evidence_ids=ev_ids,
            source=source_name,
            confidence=float(self.confidence),
            is_uncertain=(float(self.confidence) < 1.0 or self.status != GeometryStatus.AVAILABLE),
            provenance=provenance,
            trace_id=self.trace.get("trace_id") if isinstance(self.trace, dict) else None,
            metadata=dict(self.metadata),
        )

        self.observation_id = obs_id
        return obs

    # Boundary protection: Geometry must NEVER directly produce defects or findings
    def to_defect(self, *args: Any, **kwargs: Any) -> Any:
        """Reject direct defect creation from geometry observations."""
        raise TesterBoundaryViolationError(
            action="GEOMETRY_TO_DEFECT",
            reason=(
                "GeometryObservation cannot directly produce a TesterDefect. Spatial observation is descriptive only. "
                "Defect identification requires downstream evaluation against acceptance criteria."
            ),
        )

    def to_finding(self, *args: Any, **kwargs: Any) -> Any:
        """Reject direct finding creation from geometry observations."""
        raise TesterBoundaryViolationError(
            action="GEOMETRY_TO_FINDING",
            reason=(
                "GeometryObservation cannot directly produce a TesterFinding. Spatial observation is descriptive only."
            ),
        )

    def assert_verdict(self, *args: Any, **kwargs: Any) -> Any:
        """Reject asserting test verdicts from geometry observations."""
        raise TesterBoundaryViolationError(
            action="GEOMETRY_ASSERT_VERDICT",
            reason=(
                "Geometry observation cannot assert test pass/fail verdicts."
            ),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "geometry_id": self.geometry_id,
            "execution_id": self.execution_id,
            "project_id": self.project_id,
            "source_evidence_id": self.source_evidence_id,
            "observation_id": self.observation_id,
            "test_case_id": self.test_case_id,
            "test_step_id": self.test_step_id,
            "runtime_id": self.runtime_id,
            "element_id": self.element_id,
            "bounding_box": self.bounding_box.to_dict() if self.bounding_box else None,
            "viewport": self.viewport.to_dict() if self.viewport else None,
            "coordinate_system": self.coordinate_system.value,
            "visibility_state": self.visibility_state.value,
            "status": self.status.value,
            "confidence": float(self.confidence),
            "spatial_relation": self.spatial_relation.to_dict() if self.spatial_relation else None,
            "measurements": dict(self.measurements),
            "provenance": dict(self.provenance),
            "timestamp": self.timestamp,
            "trace": dict(self.trace),
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> GeometryObservation:
        if not isinstance(data, dict):
            raise TesterValidationError(f"Expected dict for GeometryObservation, got {type(data).__name__}.")

        bbox = data.get("bounding_box")
        if isinstance(bbox, dict):
            bbox = Rectangle.from_dict(bbox)

        vp = data.get("viewport")
        if isinstance(vp, dict):
            vp = ViewportDimensions.from_dict(vp)

        sr = data.get("spatial_relation")
        if isinstance(sr, dict):
            sr = SpatialRelation.from_dict(sr)

        coord_sys = data.get("coordinate_system", CoordinateSystem.VIEWPORT)
        if isinstance(coord_sys, str):
            try:
                coord_sys = CoordinateSystem(coord_sys.upper())
            except ValueError:
                coord_sys = CoordinateSystem.VIEWPORT

        vis = data.get("visibility_state", VisibilityState.UNKNOWN)
        if isinstance(vis, str):
            try:
                vis = VisibilityState(vis.upper())
            except ValueError:
                vis = VisibilityState.UNKNOWN

        status = data.get("status", GeometryStatus.AVAILABLE)
        if isinstance(status, str):
            try:
                status = GeometryStatus(status.upper())
            except ValueError:
                status = GeometryStatus.AVAILABLE

        return cls(
            geometry_id=data["geometry_id"],
            execution_id=data["execution_id"],
            project_id=data["project_id"],
            source_evidence_id=data.get("source_evidence_id"),
            observation_id=data.get("observation_id"),
            test_case_id=data.get("test_case_id"),
            test_step_id=data.get("test_step_id"),
            runtime_id=data.get("runtime_id"),
            element_id=data.get("element_id"),
            bounding_box=bbox,
            viewport=vp,
            coordinate_system=coord_sys,
            visibility_state=vis,
            status=status,
            confidence=float(data.get("confidence", 1.0)),
            spatial_relation=sr,
            measurements=dict(data.get("measurements", {})),
            provenance=dict(data.get("provenance", {})),
            timestamp=str(data.get("timestamp", utc_now())),
            trace=dict(data.get("trace", {})),
            metadata=dict(data.get("metadata", {})),
        )


# ---------------------------------------------------------------------------
# Visual Geometry Observer (Deterministic Observation Engine)
# ---------------------------------------------------------------------------

class VisualGeometryObserver:
    """
    Deterministic observer for measuring visual element geometry and layout relationships.
    
    Principles:
    - Never fabricates coordinates: returns UNAVAILABLE or UNKNOWN if geometry is absent.
    - Strictly factual: computes numeric distances, areas, and boundaries without semantic judgments.
    - Tenant isolated: validates execution_id and project_id lineage.
    """

    @classmethod
    def observe_element_geometry(
        cls,
        execution_id: str,
        project_id: str,
        element_id: str,
        bounds: Optional[Union[Rectangle, dict[str, Any]]] = None,
        source_evidence: Optional[Any] = None,
        viewport: Optional[Union[ViewportDimensions, dict[str, Any]]] = None,
        coordinate_system: CoordinateSystem = CoordinateSystem.VIEWPORT,
        visibility_state: VisibilityState = VisibilityState.UNKNOWN,
        test_case_id: Optional[str] = None,
        test_step_id: Optional[str] = None,
        runtime_id: Optional[str] = None,
        confidence: float = 1.0,
        source: str = "runtime.bounding_rect",
        metadata: Optional[dict[str, Any]] = None,
    ) -> GeometryObservation:
        """
        Extract measurable element geometry from runtime DOM or bounding rect.
        Returns UNAVAILABLE status if bounds are missing; does NOT fabricate coordinates.
        """
        geom_id = new_geometry_id()
        ev_id = getattr(source_evidence, "evidence_id", None) if source_evidence else None

        # Isolation checks if evidence is passed
        if source_evidence is not None and hasattr(source_evidence, "execution_id") and source_evidence.execution_id:
            if source_evidence.execution_id != execution_id:
                raise TesterLineageError(
                    f"Evidence execution_id ('{source_evidence.execution_id}') does not match observation ('{execution_id}')."
                )
        if source_evidence is not None and hasattr(source_evidence, "metadata") and source_evidence.metadata:
            ev_proj = source_evidence.metadata.get("project_id")
            if ev_proj and ev_proj != project_id:
                raise TesterBoundaryViolationError(
                    action="GEOMETRY_PROJECT_ISOLATION",
                    reason=f"Evidence project_id ('{ev_proj}') does not match observation ('{project_id}').",
                )

        vp_dim: Optional[ViewportDimensions] = None
        if isinstance(viewport, ViewportDimensions):
            vp_dim = viewport
        elif isinstance(viewport, dict):
            vp_dim = ViewportDimensions.from_dict(viewport)
        elif source_evidence is not None and hasattr(source_evidence, "metadata") and source_evidence.metadata:
            ev_vp = source_evidence.metadata.get("viewport")
            if isinstance(ev_vp, dict) and "width" in ev_vp and "height" in ev_vp:
                vp_dim = ViewportDimensions(width=float(ev_vp["width"]), height=float(ev_vp["height"]))

        # Missing bounds handling (Zero fabrication)
        if bounds is None:
            return GeometryObservation(
                geometry_id=geom_id,
                execution_id=execution_id,
                project_id=project_id,
                source_evidence_id=ev_id,
                test_case_id=test_case_id,
                test_step_id=test_step_id,
                runtime_id=runtime_id,
                element_id=element_id,
                bounding_box=None,
                viewport=vp_dim,
                coordinate_system=coordinate_system,
                visibility_state=VisibilityState.UNKNOWN,
                status=GeometryStatus.UNAVAILABLE,
                confidence=0.0,
                provenance={"source": source, "missing_reason": "No bounds provided; coordinates not fabricated."},
                metadata=dict(metadata or {}),
            )

        rect: Rectangle
        if isinstance(bounds, Rectangle):
            rect = bounds
        elif isinstance(bounds, dict):
            rect = Rectangle.from_dict(bounds)
        else:
            rect = Rectangle.from_bounding_box(bounds)

        return GeometryObservation(
            geometry_id=geom_id,
            execution_id=execution_id,
            project_id=project_id,
            source_evidence_id=ev_id,
            test_case_id=test_case_id,
            test_step_id=test_step_id,
            runtime_id=runtime_id,
            element_id=element_id,
            bounding_box=rect,
            viewport=vp_dim,
            coordinate_system=coordinate_system,
            visibility_state=visibility_state,
            status=GeometryStatus.AVAILABLE,
            confidence=float(confidence),
            measurements={
                "width": rect.width,
                "height": rect.height,
                "area": rect.area,
                "center": rect.center.to_dict(),
            },
            provenance={"source": source, "element_id": element_id},
            metadata=dict(metadata or {}),
        )

    @classmethod
    def observe_ocr_geometry(
        cls,
        execution_id: str,
        project_id: str,
        ocr_result: Any,
        region_index: Optional[int] = None,
        source_evidence: Optional[Any] = None,
        viewport: Optional[Union[ViewportDimensions, dict[str, Any]]] = None,
        test_case_id: Optional[str] = None,
        test_step_id: Optional[str] = None,
        runtime_id: Optional[str] = None,
    ) -> list[GeometryObservation]:
        """
        Derive factual geometry observations from OCR recognition results and text regions.
        """
        regions = getattr(ocr_result, "text_regions", [])
        if region_index is not None:
            if 0 <= region_index < len(regions):
                target_regions = [regions[region_index]]
            else:
                return []
        else:
            target_regions = regions

        results: list[GeometryObservation] = []
        ev_id = getattr(ocr_result, "source_evidence_id", None) or getattr(source_evidence, "evidence_id", None)

        for idx, region in enumerate(target_regions):
            bbox = getattr(region, "bounding_box", None)
            if not bbox:
                continue
            rect = Rectangle.from_bounding_box(bbox)
            text_label = getattr(region, "text", f"region_{idx}")
            reg_conf = float(getattr(region, "confidence", 1.0))

            obs = cls.observe_element_geometry(
                execution_id=execution_id,
                project_id=project_id,
                element_id=f"ocr_text:{text_label}",
                bounds=rect,
                source_evidence=source_evidence,
                viewport=viewport,
                coordinate_system=CoordinateSystem.VIEWPORT,
                visibility_state=VisibilityState.VISIBLE,
                test_case_id=test_case_id,
                test_step_id=test_step_id,
                runtime_id=runtime_id,
                confidence=reg_conf,
                source="ocr.text_region",
                metadata={"ocr_id": getattr(ocr_result, "ocr_id", None), "text": text_label},
            )
            # Ensure source_evidence_id is set
            obs.source_evidence_id = ev_id
            results.append(obs)

        return results

    @classmethod
    def measure_spatial_relation(
        cls,
        rect_a: Rectangle,
        rect_b: Rectangle,
        element_a: str = "element_a",
        element_b: str = "element_b",
        is_b_container: bool = False,
    ) -> SpatialRelation:
        """
        Compute factual spatial relation metrics between two observed geometric rectangles.
        Measures Euclidean distance, overlap, intersection area, containment, and container overflow.
        """
        if not isinstance(rect_a, Rectangle) or not isinstance(rect_b, Rectangle):
            raise TesterValidationError("measure_spatial_relation requires Rectangle instances.")

        dist = rect_a.distance_to(rect_b)
        overlaps = rect_a.intersects(rect_b)
        inter_rect = rect_a.intersection(rect_b)
        overlap_area = inter_rect.area if inter_rect else 0.0

        a_in_b = rect_b.contains_rect(rect_a)
        b_in_a = rect_a.contains_rect(rect_b)

        # Measure container overflow if rect_b is designated as the container
        a_overflows_b = False
        overflow_dist = 0.0
        if is_b_container:
            over_left = max(0.0, rect_b.left - rect_a.left)
            over_top = max(0.0, rect_b.top - rect_a.top)
            over_right = max(0.0, rect_a.right - rect_b.right)
            over_bottom = max(0.0, rect_a.bottom - rect_b.bottom)
            overflow_dist = max(over_left, over_top, over_right, over_bottom)
            a_overflows_b = overflow_dist > 0.0

        return SpatialRelation(
            element_a=element_a,
            element_b=element_b,
            distance=dist,
            overlaps=overlaps,
            overlap_area=overlap_area,
            overlap_rect=inter_rect,
            a_contains_b=b_in_a,
            b_contains_a=a_in_b,
            a_overflows_b=a_overflows_b,
            overflow_distance=overflow_dist,
        )

    @classmethod
    def observe_layout_relation(
        cls,
        obs_a: GeometryObservation,
        obs_b: GeometryObservation,
        is_b_container: bool = False,
        execution_id: Optional[str] = None,
        project_id: Optional[str] = None,
        test_case_id: Optional[str] = None,
        test_step_id: Optional[str] = None,
        runtime_id: Optional[str] = None,
    ) -> GeometryObservation:
        """
        Create a compound GeometryObservation recording the measured spatial relationship between two observations.
        """
        exec_id = execution_id or obs_a.execution_id
        proj_id = project_id or obs_a.project_id

        # Missing geometry check
        if obs_a.bounding_box is None or obs_b.bounding_box is None:
            return GeometryObservation(
                geometry_id=new_geometry_id(),
                execution_id=exec_id,
                project_id=proj_id,
                test_case_id=test_case_id,
                test_step_id=test_step_id,
                runtime_id=runtime_id,
                element_id=f"relation({obs_a.element_id},{obs_b.element_id})",
                status=GeometryStatus.UNAVAILABLE,
                confidence=0.0,
                provenance={"source": "spatial_analyzer", "reason": "One or both bounding boxes unavailable."},
            )

        relation = cls.measure_spatial_relation(
            rect_a=obs_a.bounding_box,
            rect_b=obs_b.bounding_box,
            element_a=obs_a.element_id or "element_a",
            element_b=obs_b.element_id or "element_b",
            is_b_container=is_b_container,
        )

        return GeometryObservation(
            geometry_id=new_geometry_id(),
            execution_id=exec_id,
            project_id=proj_id,
            source_evidence_id=obs_a.source_evidence_id or obs_b.source_evidence_id,
            test_case_id=test_case_id,
            test_step_id=test_step_id,
            runtime_id=runtime_id,
            element_id=f"relation({obs_a.element_id},{obs_b.element_id})",
            bounding_box=relation.overlap_rect,
            viewport=obs_a.viewport or obs_b.viewport,
            coordinate_system=obs_a.coordinate_system,
            visibility_state=VisibilityState.VISIBLE if relation.overlaps else VisibilityState.UNKNOWN,
            status=GeometryStatus.AVAILABLE,
            confidence=min(obs_a.confidence, obs_b.confidence),
            spatial_relation=relation,
            measurements=relation.to_dict(),
            provenance={
                "source": "spatial_relation_observer",
                "element_a": obs_a.element_id,
                "element_b": obs_b.element_id,
            },
        )
