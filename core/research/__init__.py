from __future__ import annotations

from core.research.contracts.crawler_report import CrawlerReport, RawSourceReference
from core.research.contracts.crawler_task import CrawlerTask
from core.research.contracts.evidence import (
    EvidenceItem,
    EvidenceProvenance,
    Source,
    compute_sha256,
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
from core.research.crawler.base import BaseCrawler
from core.research.crawler.capability import CrawlerCapabilitySpec, matches_capability
from core.research.crawler.lifecycle import CrawlerStateMachine
from core.research.crawler.mock_crawler import MockCrawler
from core.research.crawler.community import CommunityCrawler
from core.research.crawler.documentation import DocumentationCrawler
from core.research.crawler.repository import RepositoryCrawler
from core.research.crawler.web_fetch import WebFetchCrawler
from core.research.crawler.web_search import WebSearchCrawler
from core.research.errors import (
    CrawlerExecutionError,
    CrawlerNotFoundError,
    CrawlerTaskNotFoundError,
    InsufficientEvidenceError,
    InvalidStateTransitionError,
    ProvenanceError,
    ResearchError,
)
from core.research.evidence.collector import EvidenceCollector
from core.research.evidence.evaluator import EvidenceEvaluator
from core.research.orchestration.spawner import CrawlerSpawner
from core.research.orchestration.supervisor import CrawlerSupervisor
from core.research.planning.decomposer import ResearchDecomposer
from core.research.planning.task_generator import CrawlerTaskGenerator
from core.research.researcher import Researcher
from core.research.state.lifecycle import ResearchStateMachine
from core.research.state.model import ResearchState, StateTransitionRecord
from core.research.synthesis.synthesizer import ResearchSynthesizer
from core.research.types import (
    CrawlerCapability,
    CrawlerHealthStatus,
    CrawlerReportStatus,
    CrawlerStatus,
    CrawlerTaskStatus,
    EvidenceSufficiency,
    FactClassification,
    ResearchConfidence,
    ResearchLifecycleState,
    ResearchMode,
    ResearchQuestionStatus,
    ResearchResultStatus,
    SourceType,
)

__all__ = [
    # Lifecycle & Types
    "ResearchLifecycleState",
    "CrawlerStatus",
    "CrawlerHealthStatus",
    "CrawlerCapability",
    "CrawlerTaskStatus",
    "CrawlerReportStatus",
    "ResearchMode",
    "FactClassification",
    "ResearchConfidence",
    "ResearchQuestionStatus",
    "EvidenceSufficiency",
    "ResearchResultStatus",
    "SourceType",
    # Contracts
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
    # Errors
    "ResearchError",
    "InvalidStateTransitionError",
    "InsufficientEvidenceError",
    "CrawlerExecutionError",
    "CrawlerNotFoundError",
    "CrawlerTaskNotFoundError",
    "ProvenanceError",
    # State
    "ResearchStateMachine",
    "ResearchState",
    "StateTransitionRecord",
    # Crawler
    "BaseCrawler",
    "CrawlerStateMachine",
    "CrawlerCapabilitySpec",
    "matches_capability",
    "CrawlerRegistry",
    "MockCrawler",
    "WebSearchCrawler",
    "DocumentationCrawler",
    "RepositoryCrawler",
    # Planning
    "ResearchDecomposer",
    "CrawlerTaskGenerator",
    # Evidence
    "EvidenceCollector",
    "EvidenceEvaluator",
    # Orchestration
    "CrawlerSpawner",
    "CrawlerSupervisor",
    # Synthesis
    "ResearchSynthesizer",
    # Coordinator
    "Researcher",
]
