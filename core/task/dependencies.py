from typing import Optional

from core.enums import DependencyType, TaskStatus
from core.errors import DependencyCycleError, DependencyNotSatisfiedError
from core.models import Dependency, Task
from core.storage.base import Store


class DependencyResolver:
    """Manages task dependency registration, cycle checking, and readiness verification."""

    def __init__(self, store: Store):
        self.store = store

    def add_dependency(
        self,
        dependent_task_id: str,
        prerequisite_task_id: str,
        dependency_type: DependencyType = DependencyType.STRICT_SUCCESS,
        dependency_id: Optional[str] = None,
    ) -> Dependency:
        """
        Add a dependency between two tasks with cycle detection.
        Raises DependencyCycleError if adding this edge causes a cycle.
        """
        if dependent_task_id == prerequisite_task_id:
            raise DependencyCycleError(dependent_task_id, prerequisite_task_id)

        # Check for circular dependency using DFS
        if self._would_create_cycle(dependent_task_id, prerequisite_task_id):
            raise DependencyCycleError(dependent_task_id, prerequisite_task_id)

        dep = Dependency(
            id=dependency_id or f"dep-{dependent_task_id[:8]}-{prerequisite_task_id[:8]}",
            dependent_task_id=dependent_task_id,
            prerequisite_task_id=prerequisite_task_id,
            dependency_type=dependency_type,
        )
        self.store.add_dependency(dep)

        # Update dependent task's dependencies list in task record
        dependent_task = self.store.get_task(dependent_task_id)
        if dependent_task:
            if prerequisite_task_id not in dependent_task.dependencies:
                dependent_task.dependencies.append(prerequisite_task_id)
                self.store.save_task(dependent_task)

        return dep

    def _would_create_cycle(self, dependent_id: str, prerequisite_id: str) -> bool:
        """
        Check if prerequisite_id already depends on dependent_id directly or transitively.
        If prerequisite_id requires dependent_id, making dependent_id require prerequisite_id would close a loop.
        """
        visited = set()
        stack = [prerequisite_id]

        while stack:
            curr = stack.pop()
            if curr == dependent_id:
                return True
            if curr in visited:
                continue
            visited.add(curr)

            # Get prerequisites that curr depends on
            deps = self.store.get_dependencies_for_task(curr)
            for d in deps:
                if d.prerequisite_task_id not in visited:
                    stack.append(d.prerequisite_task_id)

        return False

    def are_dependencies_satisfied(self, task_id: str) -> tuple[bool, list[str]]:
        """
        Check if all prerequisite tasks for task_id are completed.
        Returns (is_satisfied, list_of_unfulfilled_prerequisite_ids).
        """
        deps = self.store.get_dependencies_for_task(task_id)
        if not deps:
            return True, []

        unfulfilled = []
        for dep in deps:
            prereq_task = self.store.get_task(dep.prerequisite_task_id)
            if not prereq_task:
                unfulfilled.append(f"{dep.prerequisite_task_id} (not found)")
                continue

            if dep.dependency_type == DependencyType.STRICT_SUCCESS:
                if prereq_task.status != TaskStatus.COMPLETED:
                    unfulfilled.append(f"{prereq_task.id} (status: {prereq_task.status.value})")
            elif dep.dependency_type == DependencyType.COMPLETION_ANY:
                if prereq_task.status not in (TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED):
                    unfulfilled.append(f"{prereq_task.id} (status: {prereq_task.status.value})")

        return len(unfulfilled) == 0, unfulfilled

    def validate_task_readiness(self, task: Task) -> None:
        """
        Validate that task dependencies are satisfied.
        Raises DependencyNotSatisfiedError if any are incomplete.
        """
        satisfied, unfulfilled = self.are_dependencies_satisfied(task.id)
        if not satisfied:
            raise DependencyNotSatisfiedError(task.id, unfulfilled)

    def resolve_downstream_tasks(self, completed_task_id: str) -> list[Task]:
        """
        Check all downstream dependent tasks of completed_task_id.
        If all prerequisites for a dependent task are now satisfied and the task is in PENDING state,
        transitions it to READY and saves it. Returns list of newly ready tasks.
        """
        dependents = self.store.get_dependents_for_task(completed_task_id)
        newly_ready_tasks: list[Task] = []

        for dep in dependents:
            downstream_task = self.store.get_task(dep.dependent_task_id)
            if not downstream_task:
                continue

            if downstream_task.status == TaskStatus.PENDING:
                satisfied, _ = self.are_dependencies_satisfied(downstream_task.id)
                if satisfied:
                    downstream_task.status = TaskStatus.READY
                    self.store.save_task(downstream_task)
                    newly_ready_tasks.append(downstream_task)

        return newly_ready_tasks
