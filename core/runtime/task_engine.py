from typing import Any, Optional

from core.enums import DependencyType, RiskLevel, TaskStatus, WorkerStatus
from core.errors import (
    DependencyNotSatisfiedError,
    InvalidTaskTransitionError,
    ProjectNotFoundError,
    TaskAlreadyAssignedError,
    TaskAlreadyCompletedError,
    TaskNotFoundError,
    WorkerBusyError,
    WorkerNotEligibleError,
    WorkerNotFoundError,
)
from core.models import Task, new_id, utc_now
from core.runtime.worker_registry import WorkerRegistry
from core.storage.base import Store
from core.task.dependencies import DependencyResolver
from core.task.state_machine import TaskStateMachine


class TaskEngine:
    """Manages Task lifecycle, assignment, dependencies, and hierarchies."""

    def __init__(self, store: Store, worker_registry: WorkerRegistry):
        self.store = store
        self.worker_registry = worker_registry
        self.dependency_resolver = DependencyResolver(store)

    def create_task(
        self,
        project_id: str,
        title: str,
        objective: str = "",
        parent_task_id: Optional[str] = None,
        priority: int = 1,
        risk: RiskLevel = RiskLevel.LOW,
        dependencies: Optional[list[str]] = None,
        success_criteria: Optional[list[dict[str, Any]]] = None,
        context_references: Optional[list[dict[str, Any]]] = None,
        max_attempts: int = 3,
        task_id: Optional[str] = None,
    ) -> Task:
        # Validate project exists
        project = self.store.get_project(project_id)
        if not project:
            raise ProjectNotFoundError(project_id)

        # Validate parent task exists if specified
        if parent_task_id:
            parent = self.store.get_task(parent_task_id)
            if not parent:
                raise TaskNotFoundError(parent_task_id)

        tid = task_id or new_id()
        deps_list = dependencies or []

        # Determine initial status
        initial_status = TaskStatus.READY if not deps_list else TaskStatus.PENDING

        task = Task(
            id=tid,
            project_id=project_id,
            title=title,
            objective=objective,
            parent_task_id=parent_task_id,
            status=initial_status,
            priority=priority,
            risk=risk,
            assigned_worker=None,
            dependencies=[],
            success_criteria=success_criteria or [],
            context_references=context_references or [],
            artifacts=[],
            attempts=0,
            max_attempts=max_attempts,
            created_at=utc_now(),
        )

        self.store.save_task(task)

        # Add explicit dependencies if requested
        for prereq_id in deps_list:
            self.dependency_resolver.add_dependency(
                dependent_task_id=tid,
                prerequisite_task_id=prereq_id,
                dependency_type=DependencyType.STRICT_SUCCESS,
            )

        # If dependencies were added, re-check readiness
        if deps_list:
            satisfied, _ = self.dependency_resolver.are_dependencies_satisfied(tid)
            if satisfied:
                task.status = TaskStatus.READY
                self.store.save_task(task)

        return self.get_task(tid)

    def get_task(self, task_id: str) -> Task:
        task = self.store.get_task(task_id)
        if not task:
            raise TaskNotFoundError(task_id)
        return task

    def list_tasks(
        self,
        project_id: Optional[str] = None,
        status: Optional[TaskStatus] = None,
    ) -> list[Task]:
        return self.store.list_tasks(project_id=project_id, status=status)

    def get_child_tasks(self, parent_task_id: str) -> list[Task]:
        # Validate parent exists
        self.get_task(parent_task_id)
        return self.store.get_tasks_by_parent(parent_task_id)

    def get_task_hierarchy(self, root_task_id: str) -> dict[str, Any]:
        """Return full recursive hierarchy tree for a task and its descendants."""
        root = self.get_task(root_task_id)

        def build_tree(t: Task) -> dict[str, Any]:
            children = self.store.get_tasks_by_parent(t.id)
            return {
                "task": t.to_dict(),
                "children": [build_tree(c) for c in children],
            }

        return build_tree(root)

    def add_dependency(
        self,
        dependent_task_id: str,
        prerequisite_task_id: str,
        dependency_type: DependencyType = DependencyType.STRICT_SUCCESS,
    ) -> None:
        # Validate both tasks exist
        self.get_task(dependent_task_id)
        self.get_task(prerequisite_task_id)

        self.dependency_resolver.add_dependency(
            dependent_task_id=dependent_task_id,
            prerequisite_task_id=prerequisite_task_id,
            dependency_type=dependency_type,
        )

        # Check if dependent task status should update to PENDING
        dependent = self.get_task(dependent_task_id)
        satisfied, _ = self.dependency_resolver.are_dependencies_satisfied(dependent_task_id)
        if not satisfied and dependent.status == TaskStatus.READY:
            dependent.status = TaskStatus.PENDING
            self.store.save_task(dependent)

    def assign_task(self, task_id: str, worker_id: str) -> tuple[Task, Any]:
        """
        Deterministically assign a task to a worker after validating:
        - Worker exists & is IDLE (Rule 1 & Rule 6)
        - Task exists & is READY or RETRYING (Rule 2, 3, 4, 5)
        - Dependencies are satisfied (Rule 4)
        - Worker capabilities match
        """
        task = self.get_task(task_id)

        # Rule 5: A completed task cannot return to running/assigned
        if task.status == TaskStatus.COMPLETED:
            raise TaskAlreadyCompletedError(task_id)

        if task.status == TaskStatus.ASSIGNED:
            if task.assigned_worker == worker_id:
                worker_manifest = self.worker_registry.get_worker(worker_id)
                return task, worker_manifest
            raise TaskAlreadyAssignedError(task_id, task.assigned_worker or "unknown")

        # If task is in RETRYING state, transition it back to READY for re-assignment
        if task.status == TaskStatus.RETRYING:
            TaskStateMachine.validate_and_transition(task, TaskStatus.READY)
            self.store.save_task(task)

        if task.status != TaskStatus.READY:
            raise InvalidTaskTransitionError(
                task_id=task_id,
                from_status=task.status.value,
                to_status=TaskStatus.ASSIGNED.value,
                reason=f"Task must be in READY state before assignment, current is {task.status.value}",
            )

        # Validate dependencies satisfied (Rule 4)
        self.dependency_resolver.validate_task_readiness(task)

        # Rule 1: Validate worker exists
        worker_manifest = self.worker_registry.get_worker(worker_id)

        # Rule 6: Worker cannot execute two tasks simultaneously
        if worker_manifest.status != WorkerStatus.IDLE:
            raise WorkerBusyError(worker_id, worker_manifest.active_task_id or "unknown")

        # Validate task with worker logic if worker instance available
        try:
            worker_instance = self.worker_registry.get_worker_instance(worker_id)
            worker_instance.validate_task(task)
        except WorkerNotFoundError:
            pass  # Instance may not be in memory yet if only manifest persisted

        # Transition task state: READY -> ASSIGNED
        TaskStateMachine.validate_and_transition(task, TaskStatus.ASSIGNED)
        task.assigned_worker = worker_id
        self.store.save_task(task)

        # Transition worker state: IDLE -> ASSIGNED
        worker_manifest = self.worker_registry.update_worker_status(
            worker_id=worker_id,
            target_status=WorkerStatus.ASSIGNED,
            active_task_id=task.id,
        )

        return task, worker_manifest

    def cancel_task(self, task_id: str, reason: str = "User/Manager cancellation") -> Task:
        task = self.get_task(task_id)

        # If assigned, free the worker
        if task.assigned_worker:
            try:
                worker = self.worker_registry.get_worker(task.assigned_worker)
                if worker.active_task_id == task.id:
                    self.worker_registry.update_worker_status(
                        worker_id=task.assigned_worker,
                        target_status=WorkerStatus.IDLE,
                        active_task_id=None,
                    )
            except WorkerNotFoundError:
                pass

        TaskStateMachine.validate_and_transition(task, TaskStatus.CANCELLED, reason=reason)
        self.store.save_task(task)
        return task
