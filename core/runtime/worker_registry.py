import logging
import threading
from typing import Callable, Optional

from core.enums import WorkerStatus
from core.errors import (
    WorkerActivationFailedError,
    WorkerAlreadyExistsError,
    WorkerNotFoundError,
)
from core.models import WorkerManifest
from core.storage.base import Store
from core.worker.state_machine import WorkerStateMachine
from pkg.sdk.worker import Worker

logger = logging.getLogger("AutonomOS.WorkerRegistry")


class WorkerRegistry:
    """Manages worker registration, runtime instances, factories, and lifecycle transitions."""

    def __init__(self, store: Store):
        self.store = store
        self._lock = threading.RLock()
        self._instances: dict[str, Worker] = {}
        self._factories: dict[str, Callable[[str], Worker]] = {}

        # Register default specialist factories
        self._register_default_factories()

    def _register_default_factories(self) -> None:
        """Register dynamic factories for standard AutonomOS specialist workers."""
        def _make_researcher(worker_id: str) -> Worker:
            from workers.researcher.worker import ResearcherWorker
            return ResearcherWorker(worker_id=worker_id or "worker.researcher")

        def _make_programmer(worker_id: str) -> Worker:
            from workers.programmer.worker import ProgrammerWorker
            return ProgrammerWorker(worker_id=worker_id or "worker.programmer")

        def _make_tester(worker_id: str) -> Worker:
            from workers.tester.worker import TesterWorker
            return TesterWorker(worker_id=worker_id or "worker.tester")

        self.register_worker_factory("Researcher", _make_researcher)
        self.register_worker_factory("worker.researcher", _make_researcher)
        self.register_worker_factory("worker.researcher.codebase", _make_researcher)
        self.register_worker_factory("worker.researcher.spec", _make_researcher)

        self.register_worker_factory("Programmer", _make_programmer)
        self.register_worker_factory("worker.programmer", _make_programmer)
        self.register_worker_factory("worker.programmer.general", _make_programmer)

        self.register_worker_factory("Tester", _make_tester)
        self.register_worker_factory("worker.tester", _make_tester)
        self.register_worker_factory("worker.tester.verification", _make_tester)

    def register_worker_factory(self, key_or_role: str, factory: Callable[[str], Worker]) -> None:
        """Register a worker factory for dynamic provisioning/activation."""
        with self._lock:
            self._factories[key_or_role.lower()] = factory

    def has_worker(self, worker_id: str) -> bool:
        """Check whether a worker exists in storage or active memory."""
        with self._lock:
            if worker_id in self._instances:
                return True
            return self.store.get_worker(worker_id) is not None

    def get_active_worker_for_role(self, role: str) -> Optional[WorkerManifest]:
        """Find an active, available worker matching a required role."""
        with self._lock:
            workers = self.store.list_workers()
            role_lower = role.lower()
            # 1. Prefer IDLE worker matching role
            for w in workers:
                if (w.role.lower() == role_lower or role_lower in w.id.lower()) and w.status == WorkerStatus.IDLE:
                    return w
            # 2. Check non-terminated worker matching role
            for w in workers:
                if (w.role.lower() == role_lower or role_lower in w.id.lower()) and w.status not in (WorkerStatus.TERMINATED, WorkerStatus.ERROR, WorkerStatus.FAILED):
                    return w
            return None

    def is_worker_busy(self, worker_id: str) -> bool:
        """Check whether a worker is currently executing or assigned to a task."""
        with self._lock:
            w = self.store.get_worker(worker_id)
            if not w:
                return False
            return w.status in (WorkerStatus.ASSIGNED, WorkerStatus.RUNNING, WorkerStatus.REPORTING)

    def provision_worker(
        self,
        worker_id_or_role: str,
        initial_status: WorkerStatus = WorkerStatus.IDLE,
    ) -> tuple[WorkerManifest, bool]:
        """
        Deterministically provision/activate a worker through the real lifecycle:
        REQUESTED -> STARTING -> INITIALIZING -> REGISTERED -> READY/IDLE.
        Returns (WorkerManifest, is_newly_registered).
        """
        with self._lock:
            # 1. Check if already exists in store
            existing = self.store.get_worker(worker_id_or_role)
            if existing:
                if worker_id_or_role not in self._instances:
                    # Attempt to instantiate and restore instance in memory
                    factory = self._find_factory(existing.role) or self._find_factory(existing.id)
                    if factory:
                        try:
                            inst = factory(existing.id)
                            self._instances[existing.id] = inst
                        except Exception as e:
                            logger.warning(f"Could not restore memory instance for worker '{existing.id}': {e}")
                return existing, False

            # 2. Check if a role-based worker is already active
            role_match = self.get_active_worker_for_role(worker_id_or_role)
            if role_match:
                if role_match.id not in self._instances:
                    factory = self._find_factory(role_match.role) or self._find_factory(role_match.id)
                    if factory:
                        try:
                            inst = factory(role_match.id)
                            self._instances[role_match.id] = inst
                        except Exception as e:
                            logger.warning(f"Could not restore memory instance for worker '{role_match.id}': {e}")
                return role_match, False

            # 3. Dynamic Provisioning via Factory
            factory = self._find_factory(worker_id_or_role)
            if not factory:
                raise WorkerActivationFailedError(
                    worker_id=worker_id_or_role,
                    reason=f"No factory registered for worker identifier or role '{worker_id_or_role}'",
                    retryable=False,
                    lifecycle_state="FAILED",
                )

            # Determine target worker ID
            target_id = worker_id_or_role if "." in worker_id_or_role else f"worker.{worker_id_or_role.lower()}"
            try:
                worker_instance = factory(target_id)
            except Exception as inst_err:
                raise WorkerActivationFailedError(
                    worker_id=target_id,
                    reason=f"Instantiation threw exception: {inst_err}",
                    retryable=False,
                    lifecycle_state="FAILED",
                ) from inst_err

            manifest = worker_instance.get_manifest()
            manifest.status = WorkerStatus.REGISTERED
            self._instances[manifest.id] = worker_instance
            self.store.save_worker(manifest)

            # Transition from REGISTERED -> initial_status (e.g. IDLE)
            if initial_status != WorkerStatus.REGISTERED:
                WorkerStateMachine.validate_and_transition(manifest, initial_status)
                self.store.save_worker(manifest)

            logger.info(f"Worker '{manifest.id}' ({manifest.role}) successfully activated and registered (Status: {manifest.status.value})")
            return manifest, True

    def _find_factory(self, identifier: str) -> Optional[Callable[[str], Worker]]:
        """Look up worker factory by exact key, prefix, or role match."""
        id_lower = identifier.lower()
        if id_lower in self._factories:
            return self._factories[id_lower]

        # Check prefix matches (e.g. worker.researcher.codebase -> worker.researcher)
        for key, factory in self._factories.items():
            if id_lower.startswith(key) or key.startswith(id_lower):
                return factory
            if "research" in id_lower and "research" in key:
                return factory
            if "program" in id_lower and "program" in key:
                return factory
            if "test" in id_lower and "test" in key:
                return factory
        return None

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
                manifest = self.get_worker(worker_id)
                factory = self._find_factory(manifest.role) or self._find_factory(manifest.id)
                if factory:
                    inst = factory(manifest.id)
                    self._instances[manifest.id] = inst
                    return inst
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
