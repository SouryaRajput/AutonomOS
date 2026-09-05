from __future__ import annotations

from enum import Enum


class RouteDestination(str, Enum):
    """
    Authoritative taxonomy of execution destinations for incoming requests.
    Determines the execution path before any worker or specialist is activated.
    """
    DIRECT_ACTION = "DIRECT_ACTION"           # Direct UI command, immediate tool execution, or simple action
    MANAGER_REASONING = "MANAGER_REASONING"   # Analytical evaluation, policy arbitration, or approach comparison
    RESEARCH = "RESEARCH"                     # External information gathering, web/doc crawl, evidence synthesis
    SPECIALIZED_WORKER = "SPECIALIZED_WORKER" # Direct handoff to a designated worker (e.g. programmer, tester)
    CLARIFICATION = "CLARIFICATION"           # Ambiguous or underspecified request requiring user clarification
    MULTI_STAGE = "MULTI_STAGE"               # Compound request requiring sequential phases (e.g. research then implement)
    FAILED = "FAILED"                         # Infrastructure or unrecoverable classification failure


class RoutingConfidenceTier(str, Enum):
    """Structured confidence tiers for routing decisions."""
    HIGH = "HIGH"         # score >= 0.85: decisive routing without ambiguity
    MEDIUM = "MEDIUM"     # 0.60 <= score < 0.85: viable routing with minor assumptions
    LOW = "LOW"           # 0.40 <= score < 0.60: questionable routing; clarification strongly advised
    UNCERTAIN = "UNCERTAIN" # score < 0.40: unsafe to proceed without clarification
