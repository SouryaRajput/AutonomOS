import threading
from typing import Optional

from core.enums import WorkerStatus
from core.errors import (
    WorkerAlreadyExistsError,
    WorkerNotFoundError,
)
from core.models import WorkerManifest
from core.storage.base import Store
from core.worker.state_machine import WorkerStateMachine
from pkg.sdk.worker import Worker


class WorkerRegistry:
    """Manages worker registration, runtime instances, and lifecycle transitions."""

    def __init__(self, store: Store):
        self.store = store
        self._lock = threading.RLock()
        self._instances: dict[str, Worker] = {}

    def register_worker(self, worker: Worker, initial_status: WorkerStatus = WorkerStatus.IDLE) -> WorkerManifest:
        with self._lock:
            manifest = worker.get_manifest()
            worker_id = manifest.id

            if self.store.get_worker(worker_id):
                raise WorkerAlreadyExistsError(worker_id)

            manifest.status = initial_status
            self._instances[worker_id] = worker
            self.store.save_worker(manifest)
            return manifest

    def register_worker_instance_only(self, worker: Worker) -> None:
        """Register a runtime worker instance without overwriting existing persisted manifest."""
        with self._lock:
            manifest = worker.get_manifest()
            self._instances[manifest.id] = worker
            # If not in store, persist
            if not self.store.get_worker(manifest.id):
                self.store.save_worker(manifest)

    def get_worker(self, worker_id: str) -> WorkerManifest:
        manifest = self.store.get_worker(worker_id)
        if not manifest:
            raise WorkerNotFoundError(worker_id)
        return manifest

    def get_worker_instance(self, worker_id: str) -> Worker:
        with self._lock:
            if worker_id not in self._instances:
                # Check if in store
                self.get_worker(worker_id)
                raise WorkerNotFoundError(f"{worker_id} (instance not loaded into memory)")
            return self._instances[worker_id]

    def list_workers(self) -> list[WorkerManifest]:
        return self.store.list_workers()

    def update_worker_status(
        self,
        worker_id: str,
        target_status: WorkerStatus,
        active_task_id: Optional[str] = None,
    ) -> WorkerManifest:
        with self._lock:
            manifest = self.get_worker(worker_id)
            WorkerStateMachine.validate_and_transition(
                worker=manifest,
                target_status=target_status,
                active_task_id=active_task_id,
            )
            self.store.save_worker(manifest)
            return manifest

    def remove_worker(self, worker_id: str) -> bool:
        with self._lock:
            self.get_worker(worker_id)
            if worker_id in self._instances:
                del self._instances[worker_id]
            return self.store.delete_worker(worker_id)
