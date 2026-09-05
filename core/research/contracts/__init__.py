from __future__ import annotations

from core.research.contracts.crawler_report import CrawlerReport, RawSourceReference
from core.research.contracts.crawler_task import CrawlerTask
from core.research.contracts.evidence import (
    EvidenceItem,
    EvidenceProvenance,
    Source,
    compute_sha256,
)
from core.research.contracts.intent import (
    Ambiguity,
    ClarificationQuestion,
    EvidenceRequirement,
    IntentConfidence,
    ResearchIntent,
    TemporalScope,
    VersionScope,
)
from core.research.contracts.plan import ResearchPlan
from core.research.contracts.question import ResearchQuestion
from core.research.contracts.request import ResearchRequest, ResearchScope
from core.research.contracts.result import (
    ResearchContradiction,
    ResearchFinding,
    ResearchKnowledgeGap,
    ResearchRecommendation,
    ResearchResult,
)

__all__ = [
    "ResearchRequest",
    "ResearchScope",
    "ResearchQuestion",
    "ResearchPlan",
    "CrawlerTask",
    "CrawlerReport",
    "RawSourceReference",
    "EvidenceItem",
    "EvidenceProvenance",
    "Source",
    "compute_sha256",
    "ResearchFinding",
    "ResearchContradiction",
    "ResearchKnowledgeGap",
    "ResearchRecommendation",
    "ResearchResult",
    "ResearchIntent",
    "IntentConfidence",
    "Ambiguity",
    "ClarificationQuestion",
    "TemporalScope",
    "VersionScope",
    "EvidenceRequirement",
]
