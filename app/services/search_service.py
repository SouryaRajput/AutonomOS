"""Search service — global project search across tasks, artifacts, conversations, and workflows."""
from __future__ import annotations

from typing import TYPE_CHECKING, Any, Optional

from app.dto.errors import AppException, normalize_error
from app.dto.search import SearchResultDTO, SearchResultKind

if TYPE_CHECKING:
    from core.runtime.workforce_runtime import WorkforceRuntime


class SearchService:
    """Thin facade for global project search."""

    def __init__(self, runtime: WorkforceRuntime):
        self._runtime = runtime

    def search(self, project_id: str, query: str, limit: int = 30) -> list[dict[str, Any]]:
        """Search across tasks, artifacts, conversations, and workflow plans within a project."""
        try:
            if not query or not query.strip():
                return []

            q = query.strip().lower()
            results: list[SearchResultDTO] = []

            # 1. Search Tasks
            tasks = self._runtime.tasks.list_tasks(project_id)
            for t in tasks:
                t_title = t.title.lower()
                t_obj = t.objective.lower()
                if q in t_title or q in t_obj:
                    snippet = t.objective if q in t_obj else f"Status: {t.status.value}"
                    results.append(
                        SearchResultDTO(
                            id=t.id,
                            kind=SearchResultKind.TASK,
                            title=t.title,
                            snippet=snippet[:200],
                            project_id=project_id,
                            target_route=f"autonomos://tasks/{t.id}",
                            metadata={"status": t.status.value, "priority": t.priority.value if hasattr(t.priority, "value") else str(t.priority)},
                        )
                    )

            # 2. Search Artifacts
            artifacts = self._runtime.store.list_artifacts_for_project(project_id)
            for a in artifacts:
                a_path = a.path.lower()
                a_desc = (a.description or "").lower()
                if q in a_path or q in a_desc:
                    results.append(
                        SearchResultDTO(
                            id=a.id,
                            kind=SearchResultKind.ARTIFACT,
                            title=a.path,
                            snippet=a.description or f"Artifact type: {a.type.value if hasattr(a.type, 'value') else a.type}",
                            project_id=project_id,
                            target_route=f"autonomos://artifacts/{a.id}",
                            metadata={"type": a.type.value if hasattr(a.type, 'value') else a.type, "path": a.path},
                        )
                    )

            # 3. Search Workflows
            workflows = self._runtime.workflows.list_workflows(project_id)
            for w in workflows:
                w_title = w.title.lower()
                w_obj = (w.objective or "").lower()
                if q in w_title or q in w_obj:
                    results.append(
                        SearchResultDTO(
                            id=w.id,
                            kind=SearchResultKind.WORKFLOW,
                            title=w.title,
                            snippet=w.objective or f"Status: {w.status.value}",
                            project_id=project_id,
                            target_route=f"autonomos://workflows/{w.id}",
                            metadata={"status": w.status.value, "step": w.current_step},
                        )
                    )

            # 4. Search Conversations
            if hasattr(self._runtime, "conversations"):
                convos = self._runtime.conversations.list_conversations(project_id)
                for c in convos:
                    for msg in c.messages:
                        if q in msg.content.lower():
                            results.append(
                                SearchResultDTO(
                                    id=msg.id,
                                    kind=SearchResultKind.CONVERSATION,
                                    title=f"Chat message from {msg.sender}",
                                    snippet=msg.content[:200],
                                    project_id=project_id,
                                    target_route=f"autonomos://chat",
                                    metadata={"sender": msg.sender, "message_type": msg.message_type.value},
                                )
                            )
                            break

            return [r.to_dict() for r in results[:limit]]
        except Exception as e:
            raise AppException(normalize_error(e)) from e
