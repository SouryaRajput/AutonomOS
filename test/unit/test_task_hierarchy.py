import unittest

from core.enums import RiskLevel, TaskStatus
from core.models import Project
from core.runtime.task_engine import TaskEngine
from core.runtime.worker_registry import WorkerRegistry
from core.storage.memory_store import MemoryStore


class TestTaskHierarchy(unittest.TestCase):

    def setUp(self):
        self.store = MemoryStore()
        self.worker_registry = WorkerRegistry(self.store)
        self.engine = TaskEngine(self.store, self.worker_registry)

        self.project = Project(id="proj-1", name="Project 1", description="", root_path="/tmp")
        self.store.save_project(self.project)

    def test_parent_and_child_task_creation(self):
        # Parent Task
        parent = self.engine.create_task(
            project_id="proj-1",
            title="Implement Authentication",
            objective="Deliver auth system",
            task_id="t-parent",
        )
        self.assertIsNone(parent.parent_task_id)

        # Child Tasks
        child_1 = self.engine.create_task(
            project_id="proj-1",
            title="Research OAuth Standards",
            parent_task_id=parent.id,
            task_id="t-child-1",
        )
        child_2 = self.engine.create_task(
            project_id="proj-1",
            title="Implement Token Store",
            parent_task_id=parent.id,
            task_id="t-child-2",
        )

        children = self.engine.get_child_tasks(parent.id)
        self.assertEqual(len(children), 2)
        child_ids = {c.id for c in children}
        self.assertEqual(child_ids, {"t-child-1", "t-child-2"})

    def test_task_hierarchy_tree(self):
        parent = self.engine.create_task(
            project_id="proj-1",
            title="Root Goal",
            task_id="t-root",
        )
        child = self.engine.create_task(
            project_id="proj-1",
            title="Sub Goal",
            parent_task_id=parent.id,
            task_id="t-sub",
        )
        grandchild = self.engine.create_task(
            project_id="proj-1",
            title="Micro Step",
            parent_task_id=child.id,
            task_id="t-leaf",
        )

        tree = self.engine.get_task_hierarchy(parent.id)
        self.assertEqual(tree["task"]["id"], "t-root")
        self.assertEqual(len(tree["children"]), 1)
        self.assertEqual(tree["children"][0]["task"]["id"], "t-sub")
        self.assertEqual(len(tree["children"][0]["children"]), 1)
        self.assertEqual(tree["children"][0]["children"][0]["task"]["id"], "t-leaf")


if __name__ == "__main__":
    unittest.main()
