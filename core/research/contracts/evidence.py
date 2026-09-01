from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import hashlib
from typing import Any, Optional
import uuid

from core.models import Evidence as RuntimeEvidence
from core.research.types import FactClassification, ResearchConfidence, SourceType


def utc_now() -> str:
    """Generate ISO 8601 UTC timestamp."""
    return datetime.now(timezone.utc).isoformat()


def compute_sha256(content: str) -> str:
    """Compute SHA-256 hex digest for cryptographic integrity."""
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


@dataclass
class EvidenceProvenance:
    """Immutable audit trail establishing the exact origin and causal lineage of an evidence item."""
    request_id: str
    crawler_task_id: str
    crawler_id: str
    question_id: str = ""
    source_ref: str = ""
    correlation_id: str = ""
    captured_at: str = field(default_factory=utc_now)

    def to_dict(self) -> dict[str, Any]:
        return {
            "request_id": self.request_id,
            "crawler_task_id": self.crawler_task_id,
            "crawler_id": self.crawler_id,
            "question_id": self.question_id,
            "source_ref": self.source_ref,
            "correlation_id": self.correlation_id,
            "captured_at": self.captured_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> EvidenceProvenance:
        return cls(
            request_id=data.get("request_id", ""),
            crawler_task_id=data.get("crawler_task_id", ""),
            crawler_id=data.get("crawler_id", ""),
            question_id=data.get("question_id", ""),
            source_ref=data.get("source_ref", ""),
            correlation_id=data.get("correlation_id", ""),
            captured_at=data.get("captured_at", utc_now()),
        )


@dataclass
class Source:
    """Normalized, deduplicated representation of a research source document or endpoint."""
    source_id: str
    title: str
    url_or_ref: str
    publisher: str = ""
    source_type: SourceType = SourceType.OTHER
    accessed_at: str = field(default_factory=utc_now)
    publication_date: Optional[str] = None
    relevance_score: float = 1.0
    reliability_score: float = 1.0
    content_snippet: str = ""
    content_checksum: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_id": self.source_id,
            "title": self.title,
            "url_or_ref": self.url_or_ref,
            "publisher": self.publisher,
            "source_type": self.source_type.value if isinstance(self.source_type, SourceType) else str(self.source_type),
            "accessed_at": self.accessed_at,
            "publication_date": self.publication_date,
            "relevance_score": self.relevance_score,
            "reliability_score": self.reliability_score,
            "content_snippet": self.content_snippet,
            "content_checksum": self.content_checksum,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Source:
        st_raw = data.get("source_type", SourceType.OTHER.value)
        try:
            st = SourceType(st_raw)
        except (ValueError, TypeError):
            st = SourceType.OTHER
        return cls(
            source_id=data.get("source_id", f"src-{uuid.uuid4().hex[:6]}"),
            title=data.get("title", ""),
            url_or_ref=data.get("url_or_ref", ""),
            publisher=data.get("publisher", ""),
            source_type=st,
            accessed_at=data.get("accessed_at", utc_now()),
            publication_date=data.get("publication_date"),
            relevance_score=float(data.get("relevance_score", 1.0)),
            reliability_score=float(data.get("reliability_score", 1.0)),
            content_snippet=data.get("content_snippet", ""),
            content_checksum=data.get("content_checksum", ""),
            metadata=dict(data.get("metadata", {})),
        )


@dataclass
class EvidenceItem:
    """
    A distinct piece of grounded evidence extracted from a source during crawler execution.
    Contains cryptographic verification and full provenance back to the crawler task and request.
    """
    evidence_id: str
    provenance: EvidenceProvenance
    extracted_fact: str
    content_snippet: str = ""
    classification: FactClassification = FactClassification.FACT
    confidence: ResearchConfidence = ResearchConfidence.SUPPORTED
    reliability_score: float = 1.0
    source_type: SourceType = SourceType.OTHER
    checksum: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=utc_now)

    def __post_init__(self):
        if not self.checksum and (self.extracted_fact or self.content_snippet):
            self.checksum = compute_sha256(f"{self.extracted_fact}|{self.content_snippet}")

    def to_dict(self) -> dict[str, Any]:
        return {
            "evidence_id": self.evidence_id,
            "provenance": self.provenance.to_dict(),
            "extracted_fact": self.extracted_fact,
            "content_snippet": self.content_snippet,
            "classification": self.classification.value if isinstance(self.classification, FactClassification) else str(self.classification),
            "confidence": self.confidence.value if isinstance(self.confidence, ResearchConfidence) else str(self.confidence),
            "reliability_score": self.reliability_score,
            "source_type": self.source_type.value if isinstance(self.source_type, SourceType) else str(self.source_type),
            "checksum": self.checksum,
            "metadata": self.metadata,
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> EvidenceItem:
        c_raw = data.get("classification", FactClassification.FACT.value)
        try:
            classification = FactClassification(c_raw)
        except (ValueError, TypeError):
            classification = FactClassification.FACT

        conf_raw = data.get("confidence", ResearchConfidence.SUPPORTED.value)
        try:
            confidence = ResearchConfidence(conf_raw)
        except (ValueError, TypeError):
            confidence = ResearchConfidence.SUPPORTED

        st_raw = data.get("source_type", SourceType.OTHER.value)
        try:
            source_type = SourceType(st_raw)
        except (ValueError, TypeError):
            source_type = SourceType.OTHER

        prov_data = data.get("provenance", {})
        provenance = EvidenceProvenance.from_dict(prov_data) if isinstance(prov_data, dict) else prov_data

        return cls(
            evidence_id=data.get("evidence_id", f"ev-{uuid.uuid4().hex[:8]}"),
            provenance=provenance,
            extracted_fact=data.get("extracted_fact", ""),
            content_snippet=data.get("content_snippet", ""),
            classification=classification,
            confidence=confidence,
            reliability_score=float(data.get("reliability_score", 1.0)),
            source_type=source_type,
            checksum=data.get("checksum", ""),
            metadata=dict(data.get("metadata", {})),
            created_at=data.get("created_at", utc_now()),
        )

    def to_runtime_evidence(self, task_id: str) -> RuntimeEvidence:
        """Convert to standard runtime Evidence dataclass for storage in runtime Store."""
        return RuntimeEvidence(
            id=self.evidence_id,
            task_id=task_id,
            evidence_type=f"RESEARCH_{self.classification.value}",
            data=f"[{self.source_type.value}] {self.extracted_fact} (Source: {self.provenance.source_ref})",
            checksum=self.checksum,
            created_at=self.created_at,
        )
