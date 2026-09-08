from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import json
import re
from typing import Any, Optional, Sequence
import uuid

from core.programmer.contracts.handoff import EngineeringHandoff
from core.programmer.contracts.identifiers import (
    new_handoff_id,
)
from core.programmer.contracts.work_order import ProgrammerWorkOrder
from core.programmer.errors import (
    MissingImplementationEvidenceError,
    ProgrammerValidationError,
    UnauthorizedUXPrescriptionError,
)
from core.programmer.types import (
    DesignerContextClassification,
    EngineeringHandoffType,
    HandoffPriority,
)


def utc_now() -> str:
    """Generate ISO 8601 UTC timestamp."""
    return datetime.now(timezone.utc).isoformat()


@dataclass
class ApiEndpointContract:
    """
    Structured specification of an implemented, verified backend API endpoint.
    Communicates exact HTTP methods, path, request/response schemas, and auth requirements.
    """
    endpoint_id: str
    path: str
    method: str = "GET"
    description: str = ""
    request_schema: dict[str, Any] = field(default_factory=dict)
    response_schema: dict[str, Any] = field(default_factory=dict)
    status_codes: list[int] = field(default_factory=lambda: [200])
    auth_required: bool = False
    auth_type: Optional[str] = None
    supporting_evidence_ids: list[str] = field(default_factory=list)
    source_file: Optional[str] = None

    def __post_init__(self):
        self.method = self.method.upper()
        self.status_codes = [int(sc) for sc in self.status_codes]
        self.supporting_evidence_ids = [str(eid) for eid in self.supporting_evidence_ids]
        self.request_schema = dict(self.request_schema or {})
        self.response_schema = dict(self.response_schema or {})

    def to_dict(self) -> dict[str, Any]:
        return {
            "endpoint_id": self.endpoint_id,
            "path": self.path,
            "method": self.method,
            "description": self.description,
            "request_schema": dict(self.request_schema),
            "response_schema": dict(self.response_schema),
            "status_codes": list(self.status_codes),
            "auth_required": self.auth_required,
            "auth_type": self.auth_type,
            "supporting_evidence_ids": list(self.supporting_evidence_ids),
            "source_file": self.source_file,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ApiEndpointContract:
        return cls(
            endpoint_id=str(data.get("endpoint_id", f"ep-{uuid.uuid4().hex[:6]}")),
            path=str(data.get("path", "")),
            method=str(data.get("method", "GET")),
            description=str(data.get("description", "")),
            request_schema=dict(data.get("request_schema", {})),
            response_schema=dict(data.get("response_schema", {})),
            status_codes=list(data.get("status_codes", [200])),
            auth_required=bool(data.get("auth_required", False)),
            auth_type=data.get("auth_type"),
            supporting_evidence_ids=list(data.get("supporting_evidence_ids", [])),
            source_file=data.get("source_file"),
        )


@dataclass
class BackendCapability:
    """
    Logical grouping of backend features, data models, and integration points for Designer consumption.
    """
    capability_id: str
    name: str
    description: str
    endpoints: list[ApiEndpointContract] = field(default_factory=list)
    data_models: dict[str, Any] = field(default_factory=dict)
    auth_requirements: list[str] = field(default_factory=list)
    configuration_requirements: list[str] = field(default_factory=list)
    frontend_integration_points: list[str] = field(default_factory=list)
    technical_constraints: list[str] = field(default_factory=list)
    known_limitations: list[str] = field(default_factory=list)
    supporting_evidence_ids: list[str] = field(default_factory=list)
    supporting_artifacts: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "capability_id": self.capability_id,
            "name": self.name,
            "description": self.description,
            "endpoints": [ep.to_dict() for ep in self.endpoints],
            "data_models": dict(self.data_models),
            "auth_requirements": list(self.auth_requirements),
            "configuration_requirements": list(self.configuration_requirements),
            "frontend_integration_points": list(self.frontend_integration_points),
            "technical_constraints": list(self.technical_constraints),
            "known_limitations": list(self.known_limitations),
            "supporting_evidence_ids": list(self.supporting_evidence_ids),
            "supporting_artifacts": list(self.supporting_artifacts),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> BackendCapability:
        endpoints_data = data.get("endpoints", [])
        endpoints = [
            ApiEndpointContract.from_dict(ep) if isinstance(ep, dict) else ep
            for ep in endpoints_data
        ]
        return cls(
            capability_id=str(data.get("capability_id", f"cap-{uuid.uuid4().hex[:6]}")),
            name=str(data.get("name", "")),
            description=str(data.get("description", "")),
            endpoints=endpoints,
            data_models=dict(data.get("data_models", {})),
            auth_requirements=list(data.get("auth_requirements", [])),
            configuration_requirements=list(data.get("configuration_requirements", [])),
            frontend_integration_points=list(data.get("frontend_integration_points", [])),
            technical_constraints=list(data.get("technical_constraints", [])),
            known_limitations=list(data.get("known_limitations", [])),
            supporting_evidence_ids=list(data.get("supporting_evidence_ids", [])),
            supporting_artifacts=list(data.get("supporting_artifacts", [])),
        )


@dataclass
class DesignerContextItem:
    """
    Context item categorized strictly as a binding TECHNICAL_REQUIREMENT or advisory DESIGN_RECOMMENDATION.
    """
    statement: str
    classification: DesignerContextClassification
    rationale: str = ""
    source_ref: Optional[str] = None
    evidence_id: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "statement": self.statement,
            "classification": self.classification.value if hasattr(self.classification, "value") else str(self.classification),
            "rationale": self.rationale,
            "source_ref": self.source_ref,
            "evidence_id": self.evidence_id,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> DesignerContextItem:
        c_raw = data.get("classification", DesignerContextClassification.TECHNICAL_REQUIREMENT.value)
        try:
            cls_enum = DesignerContextClassification(c_raw)
        except (ValueError, KeyError):
            cls_enum = DesignerContextClassification.TECHNICAL_REQUIREMENT
        return cls(
            statement=str(data.get("statement", "")),
            classification=cls_enum,
            rationale=str(data.get("rationale", "")),
            source_ref=data.get("source_ref"),
            evidence_id=data.get("evidence_id"),
        )


# Keywords indicating visual/UX prescriptions
_UX_PRESCRIPTION_KEYWORDS = [
    "color",
    "font",
    "button placement",
    "layout grid",
    "theme",
    "padding",
    "margin",
    "pixel",
    "typography",
    "icon style",
    "animation",
    "visual design",
    "sidebar width",
]


class ProgrammerToDesignerHandoffBuilder:
    """
    Constructs a bounded, evidence-grounded EngineeringHandoff of type PROGRAMMER_TO_DESIGNER.
    
    Invariants:
    1. Only actual implementation artifacts and verifiable evidence are accepted.
    2. Programmer cannot claim an API endpoint exists without supporting code or evidence.
    3. Programmer cannot prescribe UX/UI decisions unless mandated by Manager in WorkOrder.
    4. Segregates TECHNICAL_REQUIREMENT from DESIGN_RECOMMENDATION.
    5. Preserves lineage back to ManagerTask and ProgrammerWorkOrder.
    """

    @classmethod
    def build(
        cls,
        work_order: ProgrammerWorkOrder,
        capabilities: Sequence[BackendCapability],
        endpoints: Optional[Sequence[ApiEndpointContract]] = None,
        context_items: Optional[Sequence[DesignerContextItem]] = None,
        known_limitations: Optional[Sequence[str]] = None,
        risks: Optional[Sequence[dict[str, Any]]] = None,
        unresolved_questions: Optional[Sequence[str]] = None,
        verification_evidence: Optional[Sequence[Any]] = None,
        artifacts: Optional[Sequence[str]] = None,
        target_worker_id: str = "worker-designer",
        source_worker_id: str = "worker-programmer",
        objective: Optional[str] = None,
        requested_action: str = "Design UI/UX interface consuming backend capabilities and conforming to technical contracts.",
        priority: HandoffPriority = HandoffPriority.NORMAL,
        trace: Optional[dict[str, Any]] = None,
    ) -> EngineeringHandoff:
        """
        Assemble and validate a Programmer-to-Designer EngineeringHandoff contract.
        """
        if not work_order:
            raise ProgrammerValidationError("work_order is required", field_name="work_order")

        # 1. Collect all declared endpoints (standalone + inside capabilities)
        all_endpoints: list[ApiEndpointContract] = list(endpoints or [])
        for cap in capabilities:
            all_endpoints.extend(cap.endpoints)

        # Index available evidence IDs
        known_evidence_ids: set[str] = set()
        for ev in (verification_evidence or []):
            ev_id = getattr(ev, "evidence_id", None) or getattr(ev, "id", None) or (ev.get("evidence_id") if isinstance(ev, dict) else None)
            if ev_id:
                known_evidence_ids.add(str(ev_id))
        for rev in (work_order.research_evidence or []):
            known_evidence_ids.add(str(rev.evidence_id))

        known_artifacts: set[str] = set(artifacts or [])
        known_artifacts.update(work_order.allowed_paths or [])
        known_artifacts.update(work_order.writable_paths or [])

        # 2. Validate implementation evidence for every declared API endpoint
        for ep in all_endpoints:
            has_file = bool(ep.source_file and (ep.source_file in known_artifacts or any(ep.source_file.startswith(p) for p in known_artifacts)))
            has_ev = bool(any(eid in known_evidence_ids for eid in ep.supporting_evidence_ids))
            
            if not has_file and not has_ev:
                raise MissingImplementationEvidenceError(
                    f"Claimed API endpoint '{ep.method} {ep.path}' ({ep.endpoint_id}) lacks supporting implementation code file or verification evidence.",
                    endpoint_ref=f"{ep.method} {ep.path}",
                    details={"endpoint_id": ep.endpoint_id, "path": ep.path, "method": ep.method},
                )

        # 3. Inspect context items for unauthorized UX prescriptions
        # Pre-compile manager authorized requirements text for keyword checking
        manager_mandates_lower = " ".join(
            (work_order.technical_requirements or []) +
            (work_order.constraints or []) +
            [work_order.objective or ""]
        ).lower()

        validated_context_items: list[DesignerContextItem] = []
        technical_constraints: list[str] = []

        for item in (context_items or []):
            if item.classification == DesignerContextClassification.TECHNICAL_REQUIREMENT:
                stmt_lower = item.statement.lower()
                has_ux_kw = any(kw in stmt_lower for kw in _UX_PRESCRIPTION_KEYWORDS)
                
                # If it looks like a UX prescription, check if Manager authorized it
                if has_ux_kw and not any(kw in manager_mandates_lower for kw in _UX_PRESCRIPTION_KEYWORDS if kw in stmt_lower):
                    raise UnauthorizedUXPrescriptionError(
                        f"Unauthorized UX prescription: '{item.statement}'. Programmer cannot mandate visual or interaction design without Manager authorization in WorkOrder.",
                        prescription=item.statement,
                    )
                technical_constraints.append(item.statement)
            validated_context_items.append(item)

        # 4. Extract capabilities and technical constraints
        for cap in capabilities:
            technical_constraints.extend(cap.technical_constraints)

        # 5. Build context payload
        all_limitations = list(known_limitations or [])
        for cap in capabilities:
            for lim in cap.known_limitations:
                if lim not in all_limitations:
                    all_limitations.append(lim)

        designer_context: dict[str, Any] = {
            "backend_capabilities": [cap.to_dict() for cap in capabilities],
            "api_endpoints": [ep.to_dict() for ep in all_endpoints],
            "context_items": [ci.to_dict() for ci in validated_context_items],
            "technical_constraints": technical_constraints,
            "known_limitations": all_limitations,
            "unresolved_questions": list(unresolved_questions or []),
            "frontend_integration_points": [
                pt for cap in capabilities for pt in cap.frontend_integration_points
            ],
            "auth_requirements": list({
                auth for cap in capabilities for auth in cap.auth_requirements
            }),
            "configuration_requirements": list({
                cfg for cap in capabilities for cfg in cap.configuration_requirements
            }),
        }

        # Evidence records
        evidence_dicts: list[dict[str, Any]] = []
        for ev in (verification_evidence or []):
            if hasattr(ev, "to_dict"):
                evidence_dicts.append(ev.to_dict())
            elif isinstance(ev, dict):
                evidence_dicts.append(dict(ev))
            else:
                evidence_dicts.append({"evidence": str(ev)})

        # Trace
        h_trace = dict(trace or {})
        h_trace.update({
            "work_order_id": work_order.work_order_id,
            "manager_task_id": work_order.manager_task_id,
            "builder": "ProgrammerToDesignerHandoffBuilder",
            "built_at": utc_now(),
        })

        return EngineeringHandoff(
            handoff_id=new_handoff_id(),
            project_id=work_order.project_id,
            source_worker_id=source_worker_id,
            target_worker_id=target_worker_id,
            source_task_id=work_order.manager_task_id or work_order.task_id or "task-default",
            work_order_id=work_order.work_order_id,
            handoff_type=EngineeringHandoffType.PROGRAMMER_TO_DESIGNER,
            objective=objective or f"UI/UX Designer handoff: {work_order.objective}",
            requested_action=requested_action,
            context=designer_context,
            artifacts=list(artifacts or []),
            requirements=[
                item.statement for item in validated_context_items
                if item.classification == DesignerContextClassification.TECHNICAL_REQUIREMENT
            ],
            constraints=technical_constraints,
            evidence=evidence_dicts,
            risks=list(risks or []),
            known_unknowns=list(unresolved_questions or []) + all_limitations,
            priority=priority,
            trace=h_trace,
        )
