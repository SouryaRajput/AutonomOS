import unittest

from core.enums import DependencyType, TaskStatus
from core.errors import DependencyCycleError, DependencyNotSatisfiedError
from core.models import Project, Task
from core.storage.memory_store import MemoryStore
from core.task.dependencies import DependencyResolver


class TestDependencies(unittest.TestCase):

    def setUp(self):
        self.store = MemoryStore()
        self.resolver = DependencyResolver(self.store)

        self.project = Project(id="p1", name="Project 1", description="", root_path="/tmp")
        self.store.save_project(self.project)

        self.task_a = Task(id="t-a", project_id="p1", title="Task A", objective="", status=TaskStatus.READY)
        self.task_b = Task(id="t-b", project_id="p1", title="Task B", objective="", status=TaskStatus.PENDING)
        self.task_c = Task(id="t-c", project_id="p1", title="Task C", objective="", status=TaskStatus.PENDING)

        self.store.save_task(self.task_a)
        self.store.save_task(self.task_b)
        self.store.save_task(self.task_c)

    def test_linear_dependency_resolution(self):
        # Task B depends on Task A (A -> B)
        self.resolver.add_dependency(dependent_task_id="t-b", prerequisite_task_id="t-a")

        # Initially, A is READY, not COMPLETED -> B is unsatisfied
        satisfied, unfulfilled = self.resolver.are_dependencies_satisfied("t-b")
        self.assertFalse(satisfied)
        self.assertEqual(len(unfulfilled), 1)

        with self.assertRaises(DependencyNotSatisfiedError):
            self.resolver.validate_task_readiness(self.task_b)

        # Complete Task A
        self.task_a.status = TaskStatus.COMPLETED
        self.store.save_task(self.task_a)

        # Downstream propagation
        newly_ready = self.resolver.resolve_downstream_tasks("t-a")
        self.assertEqual(len(newly_ready), 1)
        self.assertEqual(newly_ready[0].id, "t-b")
        self.assertEqual(self.store.get_task("t-b").status, TaskStatus.READY)

    def test_multiple_dependencies(self):
        # Task C depends on both A and B (A -> C, B -> C)
        self.resolver.add_dependency(dependent_task_id="t-c", prerequisite_task_id="t-a")
        self.resolver.add_dependency(dependent_task_id="t-c", prerequisite_task_id="t-b")

        # Complete only A -> C should still not be satisfied
        self.task_a.status = TaskStatus.COMPLETED
        self.store.save_task(self.task_a)

        newly_ready_after_a = self.resolver.resolve_downstream_tasks("t-a")
        self.assertEqual(len(newly_ready_after_a), 0)  # C is not ready yet because B is pending

        # Now complete B
        self.task_b.status = TaskStatus.COMPLETED
        self.store.save_task(self.task_b)

        newly_ready_after_b = self.resolver.resolve_downstream_tasks("t-b")
        self.assertEqual(len(newly_ready_after_b), 1)
        self.assertEqual(newly_ready_after_b[0].id, "t-c")
        self.assertEqual(self.store.get_task("t-c").status, TaskStatus.READY)

    def test_cycle_detection_direct(self):
        # A -> B
        self.resolver.add_dependency(dependent_task_id="t-b", prerequisite_task_id="t-a")
        # B -> A (Cycle!)
        with self.assertRaises(DependencyCycleError):
            self.resolver.add_dependency(dependent_task_id="t-a", prerequisite_task_id="t-b")

    def test_cycle_detection_transitive(self):
        # A -> B -> C
        self.resolver.add_dependency(dependent_task_id="t-b", prerequisite_task_id="t-a")
        self.resolver.add_dependency(dependent_task_id="t-c", prerequisite_task_id="t-b")
        # C -> A (Transitive Cycle!)
        with self.assertRaises(DependencyCycleError):
            self.resolver.add_dependency(dependent_task_id="t-a", prerequisite_task_id="t-c")


if __name__ == "__main__":
    unittest.main()
