from datetime import datetime, timezone
import re
from typing import Optional

from core.context.model import ContextItem, ContextRequest
from core.context.types import ContextPriority, ContextSourceType
from core.models import Task


class RelevanceScorer:
    """
    Deterministic relevance scoring and explainability engine.
    Assigns normalized relevance scores [0.0, 1.0] and detailed human-readable rationales
    based on explicit references, keyword overlap, source hierarchy, and freshness.
    """

    # Common English stop words to filter out from keyword matching
    STOP_WORDS = {
        "a", "an", "and", "are", "as", "at", "be", "by", "for", "from",
        "has", "he", "in", "is", "it", "its", "of", "on", "that", "the",
        "to", "was", "were", "will", "with", "the", "this", "or", "task",
    }

    @classmethod
    def extract_keywords(cls, text: str) -> set[str]:
        """Extract alphanumeric keywords of length >= 3 in lowercase, excluding stop words."""
        tokens = re.findall(r"[a-zA-Z0-9_\-\./]+", text.lower())
        return {
            t.strip(".-_") for t in tokens
            if len(t.strip(".-_")) >= 3 and t.strip(".-_") not in cls.STOP_WORDS
        }

    @classmethod
    def calculate_freshness(cls, timestamp_str: Optional[str]) -> float:
        """
        Calculate freshness score [0.5, 1.0] based on ISO UTC timestamp age.
        """
        if not timestamp_str:
            return 0.80
        try:
            ts = datetime.fromisoformat(timestamp_str.replace("Z", "+00:00"))
            now = datetime.now(timezone.utc)
            delta_hours = max(0.0, (now - ts).total_seconds() / 3600.0)
            if delta_hours <= 1.0:
                return 1.0
            elif delta_hours <= 24.0:
                return 0.95
            elif delta_hours <= 168.0:  # 7 days
                return 0.85
            elif delta_hours <= 720.0:  # 30 days
                return 0.70
            else:
                return 0.50
        except Exception:
            return 0.80

    @classmethod
    def score_item(
        cls,
        item: ContextItem,
        task: Task,
        request: ContextRequest,
        explicit_references: set[str],
    ) -> ContextItem:
        """
        Score a context candidate item deterministically and generate explainable reasons.
        """
        reasons: list[str] = []
        score: float = 0.0

        # 1. Mandatory Task Objective & Criteria
        if item.source_type == ContextSourceType.TASK_OBJECTIVE:
            item.relevance_score = 1.0
            item.priority = ContextPriority.MANDATORY
            item.is_required = True
            item.scoring_reasons = ["Authoritative active task objective and constraints (Mandatory, score: 1.00)"]
            return item

        # 2. Source Type Baseline Score
        baseline_scores = {
            ContextSourceType.ARCHITECTURE: (0.70, "Core system architecture & invariants (+0.70)"),
            ContextSourceType.PROJECT_MAP: (0.65, "Project navigation & component map (+0.65)"),
            ContextSourceType.CURRENT_STATE: (0.65, "Current project state & active milestones (+0.65)"),
            ContextSourceType.DECISION: (0.50, "Architectural Decision Record (+0.50)"),
            ContextSourceType.TASK_MEMORY: (0.45, "Task memory documentation (+0.45)"),
            ContextSourceType.REPORT: (0.45, "Execution or verification report (+0.45)"),
            ContextSourceType.ISSUE: (0.40, "Known issue record (+0.40)"),
            ContextSourceType.REPOSITORY_FILE: (0.35, "Repository source code file (+0.35)"),
            ContextSourceType.ARTIFACT_METADATA: (0.35, "Produced artifact metadata (+0.35)"),
            ContextSourceType.RECENT_EVENTS: (0.30, "Recent audit event trail (+0.30)"),
        }

        base, base_reason = baseline_scores.get(item.source_type, (0.30, f"Source type baseline ({item.source_type.value})"))
        score += base
        reasons.append(base_reason)

        # 3. Explicit Reference Boost (+0.40)
        source_id_clean = item.source_id.strip().lstrip("/\\")
        item_path_clean = item.metadata.get("relative_path", "").strip().lstrip("/\\")
        is_explicit = (
            source_id_clean in explicit_references
            or item_path_clean in explicit_references
            or any(ref in item_path_clean or ref in source_id_clean for ref in explicit_references if ref)
        )

        if is_explicit:
            score += 0.40
            item.priority = ContextPriority.HIGH
            reasons.append(f"Explicitly referenced in task configuration (+0.40)")

        # 4. Same Task / Parent Task / Prerequisite Boost
        if item.metadata.get("task_id") == task.id or item.metadata.get("related_task_id") == task.id:
            score += 0.25
            reasons.append(f"Directly associated with active task '{task.id}' (+0.25)")
        elif item.metadata.get("task_id") in task.dependencies or item.metadata.get("related_task_id") in task.dependencies:
            score += 0.20
            reasons.append(f"Associated with prerequisite task (+0.20)")
        elif task.parent_task_id and item.metadata.get("task_id") == task.parent_task_id:
            score += 0.15
            reasons.append(f"Associated with parent task '{task.parent_task_id}' (+0.15)")

        # 5. Keyword & Focus Area Matching
        task_keywords = cls.extract_keywords(f"{task.title} {task.objective}")
        focus_keywords = set()
        for fa in request.focus_areas:
            focus_keywords.update(cls.extract_keywords(fa))

        combined_targets = task_keywords.union(focus_keywords)
        item_text = f"{item.title} {item.metadata.get('relative_path', '')} {' '.join(item.metadata.get('tags', []))}"
        item_keywords = cls.extract_keywords(item_text)

        matched_keywords = combined_targets.intersection(item_keywords)
        if matched_keywords:
            if len(matched_keywords) >= 3:
                score += 0.30
                reasons.append(f"High keyword overlap: {sorted(list(matched_keywords))[:4]} (+0.30)")
            elif len(matched_keywords) >= 1:
                score += 0.15
                reasons.append(f"Keyword match: {sorted(list(matched_keywords))} (+0.15)")

        # Focus area exact match
        for fa in request.focus_areas:
            if fa.lower() in item.title.lower() or fa.lower() in item_path_clean.lower():
                score += 0.20
                reasons.append(f"Focus area match for '{fa}' (+0.20)")
                break

        # 6. Freshness Consideration
        timestamp = item.metadata.get("updated_at") or item.metadata.get("created_at") or item.metadata.get("timestamp")
        freshness = cls.calculate_freshness(timestamp)
        item.freshness_score = freshness
        if freshness >= 0.95:
            score += 0.05
            reasons.append("Recent update (+0.05)")
        elif freshness <= 0.60 and item.source_type in (ContextSourceType.REPORT, ContextSourceType.RECENT_EVENTS):
            score -= 0.10
            reasons.append("Older report age penalty (-0.10)")

        # Normalize score in [0.0, 1.0]
        final_score = max(0.0, min(1.0, score))
        item.relevance_score = final_score
        item.scoring_reasons = reasons

        # Assign priority if not already set to MANDATORY or HIGH
        if item.priority != ContextPriority.MANDATORY:
            if final_score >= 0.85:
                item.priority = ContextPriority.HIGH
            elif final_score >= 0.60:
                item.priority = ContextPriority.MEDIUM
            elif final_score >= 0.35:
                item.priority = ContextPriority.LOW
            else:
                item.priority = ContextPriority.OPTIONAL

        return item
