from __future__ import annotations

from dataclasses import dataclass, field
import logging
import re
from typing import TYPE_CHECKING, Any, Dict, List, Optional

from core.events.model import utc_now
from core.manager.types import UserIntentType

if TYPE_CHECKING:
    from core.context.engine import ContextEngine
    from core.inference.gateway import InferenceGateway
    from core.workspace.project_map import ProjectMapEngine

logger = logging.getLogger("AutonomOS.IntentRouter")


@dataclass
class IntentClassification:
    """Deterministic intent classification result."""
    intent: UserIntentType
    confidence: float
    reason: str
    target_area: Optional[str] = None  # "routes", "tech_stack", "architecture", "tasks", "general"
    timestamp: str = field(default_factory=utc_now)


class IntentRouter:
    """
    Deterministic Intent Routing and Direct Q&A Engine for the Manager.
    Classifies user inputs before planning cycles to prevent unnecessary task DAG creation
    when the user only asks questions, checks status, or sends conversational messages.
    """

    QUESTION_INDICATORS = [
        r"^(what|where|how|why|who|when|which|is\s+there|are\s+there|can\s+you\s+explain|tell\s+me\s+about|do\s+we\s+have|list\s+all|show\s+me)\b",
        r"\?$",
        r"\b(what\s+routes?|what\s+tech|what\s+stack|what\s+framework|what\s+dependencies|what\s+components)\b",
        r"\b(where\s+is|where\s+are|how\s+does|how\s+is)\b",
    ]

    STATUS_INDICATORS = [
        r"\b(status|progress|current\s+plan|what\s+is\s+running|active\s+tasks|task\s+status|how\s+far)\b",
    ]

    CONVERSATIONAL_INDICATORS = [
        r"^(hello|hi|hey|greetings|thanks|thank\s+you|good\s+morning|good\s+evening|ok|okay|cool|nice|got\s+it)\b",
    ]

    EXECUTION_INDICATORS = [
        r"\b(add|create|build|implement|refactor|update|change|convert|transform|turn\s+this|fix|delete|remove|optimize|generate|migrate)\b",
        r"\b(make\s+it|set\s+up|configure|install|wire\s+up|integrate)\b",
    ]

    @classmethod
    def classify(cls, text: str, has_pending_questions: bool = False) -> IntentClassification:
        """
        Classifies user text deterministically into one of the UserIntentType categories.
        """
        clean = text.strip()
        lower = clean.lower()

        if not clean:
            return IntentClassification(
                intent=UserIntentType.CONVERSATIONAL,
                confidence=1.0,
                reason="Empty message",
            )

        # 1. Clarification Response check if pending questions exist
        if has_pending_questions and (lower in ("yes", "no", "proceed", "approve", "cancel", "option a", "option b", "1", "2") or len(clean.split()) <= 5):
            return IntentClassification(
                intent=UserIntentType.CLARIFICATION_RESPONSE,
                confidence=0.95,
                reason="Short answer matching pending question context",
            )

        # 2. Conversational greetings/acknowledgments
        for pat in cls.CONVERSATIONAL_INDICATORS:
            if re.search(pat, lower):
                # Make sure it's not a compound command like "Hi, please create..."
                if not any(re.search(epat, lower) for epat in cls.EXECUTION_INDICATORS):
                    return IntentClassification(
                        intent=UserIntentType.CONVERSATIONAL,
                        confidence=0.9,
                        reason="Conversational greeting or acknowledgment",
                    )

        # 3. Status Query
        for pat in cls.STATUS_INDICATORS:
            if re.search(pat, lower) and not any(re.search(epat, lower) for epat in cls.EXECUTION_INDICATORS):
                return IntentClassification(
                    intent=UserIntentType.STATUS_QUERY,
                    confidence=0.9,
                    reason="Requesting operational or task status",
                    target_area="tasks",
                )

        # 4. Pure Question check
        is_question_pattern = any(re.search(pat, lower) for pat in cls.QUESTION_INDICATORS)
        has_execution_imperative = any(re.search(pat, lower) for pat in cls.EXECUTION_INDICATORS)

        if is_question_pattern and not has_execution_imperative:
            target_area = "general"
            if "route" in lower:
                target_area = "routes"
            elif "tech" in lower or "stack" in lower or "dependenc" in lower or "package" in lower or "library" in lower or "libraries" in lower:
                target_area = "tech_stack"
            elif "component" in lower:
                target_area = "components"
            elif "file" in lower or "directory" in lower or "structure" in lower or "folder" in lower or "architecture" in lower or "entry" in lower:
                target_area = "architecture"

            return IntentClassification(
                intent=UserIntentType.QUESTION,
                confidence=0.95,
                reason=f"Informational query regarding {target_area}",
                target_area=target_area,
            )

        # 5. Explicit Execution Request
        if has_execution_imperative:
            return IntentClassification(
                intent=UserIntentType.EXECUTION_REQUEST,
                confidence=0.95,
                reason="Imperative action verb detected requesting repository transformation",
            )

        # 6. Fallback: If ending with ?, treat as Question, else treat as Execution Request if non-trivial
        if clean.endswith("?"):
            return IntentClassification(
                intent=UserIntentType.QUESTION,
                confidence=0.8,
                reason="Ends with question mark",
                target_area="general",
            )

        return IntentClassification(
            intent=UserIntentType.EXECUTION_REQUEST,
            confidence=0.7,
            reason="Defaulted to execution request",
        )

    @classmethod
    def answer_question(
        cls,
        question: str,
        map_engine: ProjectMapEngine,
        project_name: str = "Current Project",
    ) -> str:
        """
        Directly synthesizes an informational answer using the Project Map.
        Executes without creating tasks or modifying TaskEngine state.
        """
        classification = cls.classify(question)
        target = classification.target_area or "general"
        pmap = map_engine.load_project_map() or map_engine.perform_full_audit(trigger="DIRECT_QUESTION")

        buf = []
        buf.append("✓ Project Map analyzed")

        if target == "routes":
            routes = pmap.get("routes", {})
            if not routes and hasattr(map_engine, "_discover_routes"):
                routes = dict(map_engine._discover_routes(pmap.get("files", {})))
            buf.append(f"✓ Found {len(routes)} application routes\n")
            buf.append(f"### Discovered Routes for **{project_name}**:")
            if routes:
                for rpath, src in sorted(routes.items()):
                    buf.append(f"- Route `{rpath}` -> `{src}`")
            else:
                buf.append("No framework routes discovered in current source files.")

        elif target == "tech_stack":
            tech = pmap.get("tech_stack", {})
            verified = tech.get("verified_used_libraries", [])
            installed = tech.get("installed_packages", {})
            languages = tech.get("languages", [])
            frameworks = tech.get("frameworks", [])

            buf.append("✓ Tech stack inspected\n")
            buf.append(f"### Technology Stack for **{project_name}**:")
            buf.append(f"- **Languages**: {', '.join(languages) if languages else 'Not detected'}")
            buf.append(f"- **Frameworks**: {', '.join(frameworks) if frameworks else 'Standard Application'}")
            if verified:
                buf.append(f"- **Verified Used Libraries**: {', '.join(verified)}")
            unimported = [p for p in installed.keys() if p not in verified]
            if unimported:
                buf.append(f"- **Installed (Unimported in source)**: {', '.join(unimported[:10])}")

        elif target == "components":
            components = pmap.get("components", [])
            buf.append(f"✓ Found {len(components)} primary components\n")
            buf.append(f"### Major Components in **{project_name}**:")
            for comp in components[:12]:
                cname = comp.get("name", "") if isinstance(comp, dict) else str(comp)
                buf.append(f"- `{cname}`")

        elif target == "architecture":
            subsystems = pmap.get("subsystems", {})
            entry_points = pmap.get("entry_points", [])
            buf.append("✓ Architecture inspected\n")
            buf.append(f"### Architecture Overview for **{project_name}**:")
            if entry_points:
                buf.append(f"- **Entry Points**: {', '.join(entry_points)}")
            if subsystems:
                buf.append("- **Subsystems**:")
                for sname, sdata in list(subsystems.items())[:6]:
                    fcount = len(sdata.get("files", []))
                    buf.append(f"  - `{sname}` ({fcount} files)")

        else:
            # General query: query relevant context
            ctx = map_engine.query_relevant_context(question)
            matched_files = [f["path"] for f in ctx.get("relevant_files", [])]
            buf.append(f"✓ Context matched {len(matched_files)} files\n")
            buf.append(f"### Project Overview for **{project_name}**:")
            buf.append(f"- **Project Type**: {pmap.get('project_type', 'Application Workspace')}")
            buf.append(f"- **Meaningful Files**: {pmap.get('total_meaningful_files', len(pmap.get('files', {})))}")
            if matched_files:
                buf.append(f"- **Relevant Files for your query**: {', '.join(matched_files[:6])}")

        return "\n".join(buf)
