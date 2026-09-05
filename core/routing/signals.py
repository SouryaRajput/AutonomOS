from __future__ import annotations

from dataclasses import dataclass, field
import re
from typing import Optional

from core.routing.model import RoutingContext
from core.routing.types import RouteDestination


@dataclass
class DeterministicSignalResult:
    """Outcome of deterministic preprocessing and signal detection on incoming request text."""
    suggested_route: Optional[RouteDestination] = None
    confidence: float = 0.5
    target_worker: Optional[str] = None
    requires_external_information: bool = False
    requires_research_evidence: bool = False
    requires_project_context: bool = False
    requires_tool_execution: bool = False
    ambiguities: list[str] = field(default_factory=list)
    clarification_questions: list[str] = field(default_factory=list)
    matched_rules: list[str] = field(default_factory=list)


class DeterministicRoutingSignals:
    """
    Deterministic rule and pattern matching engine for incoming request routing.
    Supplies structural priors and fast-path classification signals.
    """

    # 1. Direct UI / Action patterns
    DIRECT_ACTION_PATTERNS = [
        r"\b(move\s+(?:that\s+|the\s+)?(?:card|button|window|panel|modal|element|cursor)\s+to\s+(?:center|left|right|top|bottom))\b",
        r"\b(click|press|toggle|drag|drop|scroll|zoom|minimize|maximize|close)\s+(?:the\s+|that\s+)?(?:modal|button|switch|window|tab|dialog|menu)\b",
        r"\b(view|cat|tail|head)\s+(?:the\s+)?(?:log|file|output)\b",
        r"\b(delete|remove)\s+(?:the\s+)?(?:scratch|temporary|temp|cache)\s+(?:file|folder|dir)\b",
    ]

    # 2. Ambiguous deictic references requiring clarification
    CLARIFICATION_PATTERNS = [
        r"^(move|put|place|drag)\s+(that|this|it)\s+(there|here)\.?$",
        r"^(fix|delete|change|update|remove|modify)\s+(this|that|it)\.?$",
        r"^(do\s+it|make\s+it\s+so|proceed|run\s+it)\.?$",
    ]

    # 3. Specialized worker patterns
    TESTER_PATTERNS = [
        r"\b(run\s+(?:the\s+)?(?:test\s+suite|tests?|pytest|unit\s+tests?|e2e\s+tests?|integration\s+tests?))\b",
        r"\b(execute\s+(?:the\s+)?tests?)\b",
        r"\b(verify\s+(?:the\s+)?test\s+coverage)\b",
    ]

    PROGRAMMER_PATTERNS = [
        r"\b(implement|refactor|write|code|create\s+function|add\s+method|edit\s+file)\b",
        r"\b(fix\s+syntax\s+error|patch\s+bug\s+in)\b",
    ]

    # 4. Multi-stage patterns (sequential phases connecting research and implementation)
    MULTI_STAGE_PATTERNS = [
        r"\b(?:research|find|investigate|evaluate)\s+.+?\s+(?:and\s+then|then|afterwards)\s+(?:update|implement|build|create|write|deploy)\b",
        r"\b(?:first\s+research|first\s+find|first\s+investigate)\s+.+?\s+(?:and\s+then|then)\b",
    ]

    # 5. Research patterns
    RESEARCH_PATTERNS = [
        r"\b(?:compare|comparison\s+between)\s+[A-Za-z0-9_.\-+]+\s+(?:and|with|vs\.?)\s+[A-Za-z0-9_.\-+]+",
        r"\b(?:what\s+is\s+the\s+current\s+price\s+of|latest\s+pricing\s+for)\b",
        r"\b(?:state\s+of\s+the\s+art|latest\s+version\s+of|official\s+specifications\s+for)\b",
        r"\b(?:survey\s+the\s+ecosystem|evaluate\s+competing\s+libraries)\b",
        r"\b(?:find\s+the\s+best\s+database\s+for|benchmarks\s+for)\b",
    ]

    # 6. Manager Reasoning patterns
    REASONING_PATTERNS = [
        r"\b(?:which\s+of\s+these\s+(?:two\s+)?approaches\s+is\s+(?:cleaner|better|preferred))\b",
        r"\b(?:what\s+are\s+the\s+tradeoffs\s+between\s+our\s+current\s+design\s+and)\b",
        r"\b(?:review\s+our\s+architectural\s+decisions?\s+regarding)\b",
    ]

    @classmethod
    def analyze(cls, text: str, context: Optional[RoutingContext] = None) -> DeterministicSignalResult:
        """Scan request text for deterministic patterns and signal cues."""
        clean = text.strip()
        lower = clean.lower()
        res = DeterministicSignalResult()

        if not clean:
            res.suggested_route = RouteDestination.CLARIFICATION
            res.confidence = 1.0
            res.clarification_questions.append("The request is empty. What action or research would you like to perform?")
            res.matched_rules.append("empty_request")
            return res

        # Check deictic ambiguities first (e.g. "Move that there")
        for pat in cls.CLARIFICATION_PATTERNS:
            if re.search(pat, lower):
                # Only ambiguous if context does not resolve the antecedent
                has_antecedent = bool(context and context.known_entities)
                if not has_antecedent:
                    res.suggested_route = RouteDestination.CLARIFICATION
                    res.confidence = 0.95
                    res.ambiguities.append("Deictic references ('that', 'there', 'this', 'it') lack context.")
                    res.clarification_questions.append("Which specific item or target are you referring to?")
                    res.matched_rules.append("unresolved_deictic_reference")
                    return res

        # Check multi-stage patterns
        for pat in cls.MULTI_STAGE_PATTERNS:
            if re.search(pat, lower):
                res.suggested_route = RouteDestination.MULTI_STAGE
                res.confidence = 0.90
                res.requires_research_evidence = True
                res.requires_tool_execution = True
                res.matched_rules.append("compound_multi_stage_pattern")
                return res

        # Check direct action patterns (e.g. "Move that card to center")
        for pat in cls.DIRECT_ACTION_PATTERNS:
            if re.search(pat, lower):
                res.suggested_route = RouteDestination.DIRECT_ACTION
                res.confidence = 0.95
                res.requires_tool_execution = True
                res.requires_research_evidence = False
                res.requires_external_information = False
                res.matched_rules.append("direct_action_pattern")
                return res

        # Check specialized worker patterns
        for pat in cls.TESTER_PATTERNS:
            if re.search(pat, lower):
                res.suggested_route = RouteDestination.SPECIALIZED_WORKER
                res.target_worker = "worker.tester"
                res.confidence = 0.95
                res.requires_tool_execution = True
                res.matched_rules.append("tester_worker_pattern")
                return res

        # Check research patterns
        for pat in cls.RESEARCH_PATTERNS:
            if re.search(pat, lower):
                res.suggested_route = RouteDestination.RESEARCH
                res.confidence = 0.90
                res.requires_external_information = True
                res.requires_research_evidence = True
                res.matched_rules.append("research_pattern")
                return res

        # Check manager reasoning patterns
        for pat in cls.REASONING_PATTERNS:
            if re.search(pat, lower):
                res.suggested_route = RouteDestination.MANAGER_REASONING
                res.confidence = 0.85
                res.requires_project_context = True
                res.matched_rules.append("manager_reasoning_pattern")
                return res

        return res
