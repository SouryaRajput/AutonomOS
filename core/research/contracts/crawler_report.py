from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional
import uuid

from core.research.contracts.evidence import EvidenceItem, Source
from core.research.types import CrawlerReportStatus, SourceType


def utc_now() -> str:
    """Generate ISO 8601 UTC timestamp."""
    return datetime.now(timezone.utc).isoformat()


@dataclass
class RawSourceReference:
    """Descriptor of a raw source document, web page, or endpoint inspected by a crawler."""
    url_or_ref: str
    title: str = ""
    publisher: str = ""
    source_type: SourceType = SourceType.OTHER
    checksum: str = ""
    bytes_fetched: int = 0
    content_snippet: str = ""
    fetched_at: str = field(default_factory=utc_now)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "url_or_ref": self.url_or_ref,
            "title": self.title,
            "publisher": self.publisher,
            "source_type": self.source_type.value if isinstance(self.source_type, SourceType) else str(self.source_type),
            "checksum": self.checksum,
            "bytes_fetched": self.bytes_fetched,
            "content_snippet": self.content_snippet,
            "fetched_at": self.fetched_at,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> RawSourceReference:
        st_raw = data.get("source_type", SourceType.OTHER.value)
        try:
            st = SourceType(st_raw)
        except (ValueError, TypeError):
            st = SourceType.OTHER

        return cls(
            url_or_ref=data.get("url_or_ref", ""),
            title=data.get("title", ""),
            publisher=data.get("publisher", ""),
            source_type=st,
            checksum=data.get("checksum", ""),
            bytes_fetched=int(data.get("bytes_fetched", 0)),
            content_snippet=data.get("content_snippet", ""),
            fetched_at=data.get("fetched_at", utc_now()),
            metadata=dict(data.get("metadata", {})),
        )


@dataclass
class CrawlerReport:
    """
    Standardized collection report returned by a crawler upon task completion.
    Every report is traceable to its creating CrawlerTask and the root ResearchRequest.
    """
    report_id: str
    crawler_task_id: str
    crawler_id: str
    request_id: str
    plan_id: str = ""
    question_id: str = ""
    correlation_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    status: CrawlerReportStatus = CrawlerReportStatus.SUCCESS
    raw_sources: list[RawSourceReference] = field(default_factory=list)
    extracted_evidence: list[EvidenceItem] = field(default_factory=list)
    summary: str = ""
    error_message: Optional[str] = None
    execution_time_seconds: float = 0.0
    created_at: str = field(default_factory=utc_now)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "report_id": self.report_id,
            "crawler_task_id": self.crawler_task_id,
            "crawler_id": self.crawler_id,
            "request_id": self.request_id,
            "plan_id": self.plan_id,
            "question_id": self.question_id,
            "correlation_id": self.correlation_id,
            "status": self.status.value if isinstance(self.status, CrawlerReportStatus) else str(self.status),
            "raw_sources": [s.to_dict() for s in self.raw_sources],
            "extracted_evidence": [e.to_dict() for e in self.extracted_evidence],
            "summary": self.summary,
            "error_message": self.error_message,
            "execution_time_seconds": self.execution_time_seconds,
            "created_at": self.created_at,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CrawlerReport:
        st_raw = data.get("status", CrawlerReportStatus.SUCCESS.value)
        try:
            status = CrawlerReportStatus(st_raw)
        except (ValueError, TypeError):
            status = CrawlerReportStatus.SUCCESS

        sources = [
            RawSourceReference.from_dict(s) if isinstance(s, dict) else s
            for s in data.get("raw_sources", [])
        ]
        evidence = [
            EvidenceItem.from_dict(e) if isinstance(e, dict) else e
            for e in data.get("extracted_evidence", [])
        ]

        return cls(
            report_id=data.get("report_id", f"crep-{uuid.uuid4().hex[:8]}"),
            crawler_task_id=data.get("crawler_task_id", ""),
            crawler_id=data.get("crawler_id", ""),
            request_id=data.get("request_id", ""),
            plan_id=data.get("plan_id", ""),
            question_id=data.get("question_id", ""),
            correlation_id=data.get("correlation_id", str(uuid.uuid4())),
            status=status,
            raw_sources=sources,
            extracted_evidence=evidence,
            summary=data.get("summary", ""),
            error_message=data.get("error_message"),
            execution_time_seconds=float(data.get("execution_time_seconds", 0.0)),
            created_at=data.get("created_at", utc_now()),
            metadata=dict(data.get("metadata", {})),
        )
