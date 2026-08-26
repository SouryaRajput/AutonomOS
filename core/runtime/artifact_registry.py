import hashlib
from pathlib import Path
from typing import Any, Optional

from core.enums import ArtifactType
from core.errors import ArtifactNotFoundError
from core.models import Artifact, new_id, utc_now
from core.storage.base import Store


class ArtifactRegistry:
    """Tracks and registers artifacts generated during task executions."""

    def __init__(self, store: Store):
        self.store = store

    def register_artifact(
        self,
        project_id: str,
        task_id: str,
        worker_id: str,
        artifact_type: ArtifactType,
        relative_path: str,
        description: str = "",
        content: Optional[bytes | str] = None,
        base_dir: Optional[str] = None,
        metadata: Optional[dict[str, Any]] = None,
        artifact_id: Optional[str] = None,
    ) -> Artifact:
        aid = artifact_id or new_id()
        checksum = None
        target_path = relative_path

        # If base_dir and content are provided, write file and compute checksum
        if content is not None:
            raw_bytes = content.encode("utf-8") if isinstance(content, str) else content
            checksum = hashlib.sha256(raw_bytes).hexdigest()

            if base_dir:
                full_path = Path(base_dir) / relative_path
                full_path.parent.mkdir(parents=True, exist_ok=True)
                full_path.write_bytes(raw_bytes)
                target_path = str(full_path)

        artifact = Artifact(
            id=aid,
            project_id=project_id,
            task_id=task_id,
            worker_id=worker_id,
            type=artifact_type,
            path=target_path,
            description=description,
            checksum=checksum,
            metadata=metadata or {},
            created_at=utc_now(),
        )

        self.store.save_artifact(artifact)

        # Update task's artifacts list in store
        task = self.store.get_task(task_id)
        if task and aid not in task.artifacts:
            task.artifacts.append(aid)
            self.store.save_task(task)

        return artifact

    def get_artifact(self, artifact_id: str) -> Artifact:
        artifact = self.store.get_artifact(artifact_id)
        if not artifact:
            raise ArtifactNotFoundError(artifact_id)
        return artifact

    def list_artifacts_for_task(self, task_id: str) -> list[Artifact]:
        return self.store.list_artifacts_for_task(task_id)

    def list_artifacts_for_project(self, project_id: str) -> list[Artifact]:
        return self.store.list_artifacts_for_project(project_id)
