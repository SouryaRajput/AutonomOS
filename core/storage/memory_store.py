import copy
import threading
from typing import Optional

from core.enums import TaskStatus, WorkerStatus
from core.models import Artifact, Dependency, Project, Task, WorkerManifest
from core.storage.base import Store


class MemoryStore(Store):
    """Thread-safe In-Memory Store for AutonomOS unit testing."""

    def __init__(self):
        self._lock = threading.RLock()
        self._projects: dict[str, Project] = {}
        self._workers: dict[str, WorkerManifest] = {}
        self._tasks: dict[str, Task] = {}
        self._dependencies: dict[str, Dependency] = {}
        self._artifacts: dict[str, Artifact] = {}

    def save_project(self, project: Project) -> None:
        with self._lock:
            self._projects[project.id] = copy.deepcopy(project)

    def get_project(self, project_id: str) -> Optional[Project]:
        with self._lock:
            p = self._projects.get(project_id)
            return copy.deepcopy(p) if p else None

    def list_projects(self) -> list[Project]:
        with self._lock:
            return [copy.deepcopy(p) for p in self._projects.values()]

    def delete_project(self, project_id: str) -> bool:
        with self._lock:
            if project_id in self._projects:
                del self._projects[project_id]
                # Cascade delete tasks, artifacts
                task_ids_to_del = [t.id for t in self._tasks.values() if t.project_id == project_id]
                for tid in task_ids_to_del:
                    self.delete_task(tid)
                art_ids_to_del = [a.id for a in self._artifacts.values() if a.project_id == project_id]
                for aid in art_ids_to_del:
                    del self._artifacts[aid]
                return True
            return False

    def save_worker(self, worker: WorkerManifest) -> None:
        with self._lock:
            self._workers[worker.id] = copy.deepcopy(worker)

    def get_worker(self, worker_id: str) -> Optional[WorkerManifest]:
        with self._lock:
            w = self._workers.get(worker_id)
            return copy.deepcopy(w) if w else None

    def list_workers(self) -> list[WorkerManifest]:
        with self._lock:
            return [copy.deepcopy(w) for w in self._workers.values()]

    def delete_worker(self, worker_id: str) -> bool:
        with self._lock:
            if worker_id in self._workers:
                del self._workers[worker_id]
                return True
            return False

    def update_worker_status(self, worker_id: str, status: WorkerStatus, active_task_id: Optional[str] = None) -> None:
        with self._lock:
            if worker_id in self._workers:
                self._workers[worker_id].status = status
                self._workers[worker_id].active_task_id = active_task_id

    def save_task(self, task: Task) -> None:
        with self._lock:
            self._tasks[task.id] = copy.deepcopy(task)

    def get_task(self, task_id: str) -> Optional[Task]:
        with self._lock:
            t = self._tasks.get(task_id)
            return copy.deepcopy(t) if t else None

    def list_tasks(self, project_id: Optional[str] = None, status: Optional[TaskStatus] = None) -> list[Task]:
        with self._lock:
            res = list(self._tasks.values())
            if project_id:
                res = [t for t in res if t.project_id == project_id]
            if status:
                res = [t for t in res if t.status == status]
            return [copy.deepcopy(t) for t in res]

    def get_tasks_by_parent(self, parent_task_id: str) -> list[Task]:
        with self._lock:
            res = [t for t in self._tasks.values() if t.parent_task_id == parent_task_id]
            return [copy.deepcopy(t) for t in res]

    def delete_task(self, task_id: str) -> bool:
        with self._lock:
            if task_id in self._tasks:
                del self._tasks[task_id]
                # Cascade dependencies
                dep_ids = [d.id for d in self._dependencies.values() if d.dependent_task_id == task_id or d.prerequisite_task_id == task_id]
                for did in dep_ids:
                    del self._dependencies[did]
                # Cascade artifacts
                art_ids = [a.id for a in self._artifacts.values() if a.task_id == task_id]
                for aid in art_ids:
                    del self._artifacts[aid]
                return True
            return False

    def add_dependency(self, dependency: Dependency) -> None:
        with self._lock:
            # Overwrite if exists for same pair
            existing = [d.id for d in self._dependencies.values() if d.dependent_task_id == dependency.dependent_task_id and d.prerequisite_task_id == dependency.prerequisite_task_id]
            for eid in existing:
                del self._dependencies[eid]
            self._dependencies[dependency.id] = copy.deepcopy(dependency)

    def get_dependencies_for_task(self, task_id: str) -> list[Dependency]:
        with self._lock:
            res = [d for d in self._dependencies.values() if d.dependent_task_id == task_id]
            return [copy.deepcopy(d) for d in res]

    def get_dependents_for_task(self, task_id: str) -> list[Dependency]:
        with self._lock:
            res = [d for d in self._dependencies.values() if d.prerequisite_task_id == task_id]
            return [copy.deepcopy(d) for d in res]

    def remove_dependency(self, dependency_id: str) -> bool:
        with self._lock:
            if dependency_id in self._dependencies:
                del self._dependencies[dependency_id]
                return True
            return False

    def save_artifact(self, artifact: Artifact) -> None:
        with self._lock:
            self._artifacts[artifact.id] = copy.deepcopy(artifact)

    def get_artifact(self, artifact_id: str) -> Optional[Artifact]:
        with self._lock:
            a = self._artifacts.get(artifact_id)
            return copy.deepcopy(a) if a else None

    def list_artifacts_for_task(self, task_id: str) -> list[Artifact]:
        with self._lock:
            res = [a for a in self._artifacts.values() if a.task_id == task_id]
            return [copy.deepcopy(a) for a in res]

    def list_artifacts_for_project(self, project_id: str) -> list[Artifact]:
        with self._lock:
            res = [a for a in self._artifacts.values() if a.project_id == project_id]
            return [copy.deepcopy(a) for a in res]

    def close(self) -> None:
        with self._lock:
            self._projects.clear()
            self._workers.clear()
            self._tasks.clear()
            self._dependencies.clear()
            self._artifacts.clear()
