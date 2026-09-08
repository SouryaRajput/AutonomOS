from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional
import uuid


@dataclass
class ResearchEvidenceReference:
    """
    Contextual research finding or evidence attached by Manager to the work order.
    Programmer treats research evidence as context/guidance, not unquestionable directives,
    while preserving provenance back to Researcher contracts.
    """
    evidence_id: str
    claim_or_fact: str
    source_ref: str = ""
    confidence: str = "SUPPORTED"
    provenance: dict[str, Any] = field(default_factory=dict)
    relevance_notes: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "evidence_id": self.evidence_id,
            "claim_or_fact": self.claim_or_fact,
            "source_ref": self.source_ref,
            "confidence": self.confidence,
            "provenance": dict(self.provenance),
            "relevance_notes": self.relevance_notes,
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ResearchEvidenceReference:
        return cls(
            evidence_id=str(data.get("evidence_id", f"ev-ref-{uuid.uuid4().hex[:6]}")),
            claim_or_fact=str(data.get("claim_or_fact", "")),
            source_ref=str(data.get("source_ref", "")),
            confidence=str(data.get("confidence", "SUPPORTED")),
            provenance=dict(data.get("provenance", {})),
            relevance_notes=str(data.get("relevance_notes", "")),
            metadata=dict(data.get("metadata", {})),
        )

    @classmethod
    def from_evidence(
        cls,
        evidence: Any,
        relevance_notes: str = "",
    ) -> ResearchEvidenceReference:
        """
        Construct a reference from a runtime Evidence, EvidenceItem, or ResearchFinding.
        Preserves provenance details.
        """
        if isinstance(evidence, dict):
            evidence_id = evidence.get("id") or evidence.get("evidence_id") or evidence.get("finding_id", f"ev-{uuid.uuid4().hex[:6]}")
            claim = evidence.get("claim") or evidence.get("extracted_fact") or evidence.get("description") or evidence.get("data", "")
            source_ref = evidence.get("source_ref", "")
            prov = evidence.get("provenance", {})
            prov_dict = dict(prov) if isinstance(prov, dict) else {}
            conf = evidence.get("confidence", "SUPPORTED")
            conf_str = conf.value if hasattr(conf, "value") else str(conf)
            return cls(
                evidence_id=str(evidence_id),
                claim_or_fact=str(claim),
                source_ref=str(source_ref),
                confidence=conf_str,
                provenance=prov_dict,
                relevance_notes=relevance_notes,
            )

        evidence_id = getattr(evidence, "id", None) or getattr(evidence, "evidence_id", None) or getattr(evidence, "finding_id", f"ev-{uuid.uuid4().hex[:6]}")
        claim = getattr(evidence, "claim", None) or getattr(evidence, "extracted_fact", None) or getattr(evidence, "data", "")

        source_ref = ""
        prov_dict: dict[str, Any] = {}
        prov = getattr(evidence, "provenance", None)
        if prov is not None:
            if hasattr(prov, "to_dict"):
                prov_dict = prov.to_dict()
            elif isinstance(prov, dict):
                prov_dict = dict(prov)
            source_ref = getattr(prov, "source_ref", "") or prov_dict.get("source_ref", "")

        conf = getattr(evidence, "confidence", "SUPPORTED")
        conf_str = conf.value if hasattr(conf, "value") else str(conf)

        return cls(
            evidence_id=str(evidence_id),
            claim_or_fact=str(claim),
            source_ref=str(source_ref),
            confidence=conf_str,
            provenance=prov_dict,
            relevance_notes=relevance_notes,
        )
