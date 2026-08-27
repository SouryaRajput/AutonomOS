import json
import pathlib
import shutil
import tempfile
import unittest

from core.context.engine import ContextEngine
from core.context.model import ContextBudget, ContextItem, ContextPackage, ContextRequest
from core.context.types import ContextPriority, ContextSourceType, ContextWarningType
from core.enums import ArtifactType, IssueSeverity, IssueStatus, MemoryType, TaskStatus
from core.memory.manager import MemoryManager
from core.models import Artifact, Project, Task, utc_now
from core.storage.sqlite_store import SQLiteStore


class TestContextEngine(unittest.TestCase):

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.db_path = str(pathlib.Path(self.temp_dir) / "test_ctx.db")
        self.workspace_dir = str(pathlib.Path(self.temp_dir) / "workspace")
        pathlib.Path(self.workspace_dir).mkdir(parents=True, exist_ok=True)

        self.store = SQLiteStore(self.db_path)
        self.memory = MemoryManager(self.store)
        self.engine = ContextEngine(self.store, self.memory)

        # 1. Create project
        self.project = Project(
            id="proj-ctx-1",
            name="AutonomOS Auth Service",
            description="High-security authentication microservice",
            root_path=self.workspace_dir,
            created_at=utc_now(),
        )
        self.store.save_project(self.project)

        # Initialize standard memory scaffold (.autonomos/memory/)
        self.memory.initialize_project_memory(self.project.id, self.project.name, self.project.description)

        # 2. Create real repository files on disk
        self.auth_file = pathlib.Path(self.workspace_dir) / "src" / "auth" / "jwt_handler.py"
        self.auth_file.parent.mkdir(parents=True, exist_ok=True)
        self.auth_file.write_text(
            "import jwt\n\ndef generate_jwt(user_id):\n    return jwt.encode({'user': user_id}, 'secret', algorithm='HS256')\n"
        )

        # 3. Create ADR decision in memory
        self.adr = self.memory.record_decision(
            project_id=self.project.id,
            title="Use HS256 for JWT signing",
            context="We need fast stateless token generation.",
            decision="Adopt standard HS256 JWT tokens.",
            reasoning="Fast and simple for MVP service.",
            consequences="Requires secure shared secret key.",
            decision_number=1,
            references=["src/auth/jwt_handler.py"],
        )

        # 4. Create Task
        self.task = Task(
            id="task-auth-100",
            project_id=self.project.id,
            title="Refactor JWT authentication and token expiry",
            objective="Update jwt_handler.py to include exp claim and token refresh logic.",
            status=TaskStatus.READY,
            context_references=[{"path": "src/auth/jwt_handler.py"}],
            success_criteria=[
                {"description": "Add exp claim to token payload"},
                {"description": "Write unit tests for expired token rejection"},
            ],
            created_at=utc_now(),
        )
        self.store.save_task(self.task)

    def tearDown(self):
        self.store.close()
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_basic_context_assembly_and_explicit_references(self):
        req = ContextRequest(
            project_id=self.project.id,
            task_id=self.task.id,
            worker_id="worker.programmer.1",
            budget=ContextBudget(max_tokens=4000),
            focus_areas=["src/auth/jwt_handler.py", "jwt"],
        )

        package = self.engine.assemble_context(req)

        self.assertEqual(package.task_id, self.task.id)
        self.assertGreater(package.selected_count, 0)
        self.assertGreater(package.total_estimated_tokens, 0)

        # Verify Mandatory Task Objective is included first
        first_item = package.items[0]
        self.assertEqual(first_item.source_type, ContextSourceType.TASK_OBJECTIVE)
        self.assertTrue(first_item.is_required)
        self.assertEqual(first_item.relevance_score, 1.0)

        # Verify explicitly referenced repository source file is included with HIGH priority
        repo_files = package.get_items_by_source_type(ContextSourceType.REPOSITORY_FILE)
        self.assertEqual(len(repo_files), 1)
        self.assertEqual(repo_files[0].source_id, "src/auth/jwt_handler.py")
        self.assertEqual(repo_files[0].priority, ContextPriority.HIGH)
        self.assertIn("generate_jwt", repo_files[0].content)

        # Verify ADR is discovered and included
        adrs = package.get_items_by_source_type(ContextSourceType.DECISION)
        self.assertEqual(len(adrs), 1)
        self.assertIn("HS256", adrs[0].title)

    def test_budget_enforcement_and_mandatory_protection(self):
        # Extremely small budget of 80 tokens (barely fits the mandatory task objective)
        tight_budget = ContextBudget(max_tokens=80, max_items=2)
        req = ContextRequest(
            project_id=self.project.id,
            task_id=self.task.id,
            budget=tight_budget,
        )

        package = self.engine.assemble_context(req)

        # Mandatory item MUST be protected
        self.assertEqual(package.items[0].source_type, ContextSourceType.TASK_OBJECTIVE)
        # Optional low priority background items must have been dropped
        self.assertLessEqual(len(package.items), 2)

    def test_safe_truncation_of_large_files(self):
        # Create a large source file
        large_file = pathlib.Path(self.workspace_dir) / "src" / "large_module.py"
        large_content = "def test_func():\n    pass\n" * 400  # ~8,000 characters
        large_file.write_text(large_content)

        task_large = Task(
            id="task-large-1",
            project_id=self.project.id,
            title="Inspect large module",
            objective="Analyze module structure",
            status=TaskStatus.READY,
            context_references=[{"path": "src/large_module.py"}],
            created_at=utc_now(),
        )
        self.store.save_task(task_large)

        req = ContextRequest(
            project_id=self.project.id,
            task_id=task_large.id,
            budget=ContextBudget(max_tokens=800, max_characters=3000),
        )

        package = self.engine.assemble_context(req)
        # Should contain truncation or reference pointer
        large_item = package.get_item_by_id("ctx-file-src-large_module.py")
        self.assertIsNotNone(large_item)
        self.assertTrue(
            large_item.is_reference_only
            or "Truncated due to context budget" in large_item.content
            or any(w.warning_type == ContextWarningType.TRUNCATED_ITEM for w in package.warnings)
        )

    def test_missing_file_warning(self):
        task_broken = Task(
            id="task-broken-1",
            project_id=self.project.id,
            title="Fix deleted component",
            objective="Fix component",
            status=TaskStatus.READY,
            context_references=[{"path": "src/nonexistent_file.py"}],
            created_at=utc_now(),
        )
        self.store.save_task(task_broken)

        req = ContextRequest(
            project_id=self.project.id,
            task_id=task_broken.id,
            budget=ContextBudget(),
        )
        package = self.engine.assemble_context(req)
        missing_warnings = [w for w in package.warnings if w.warning_type == ContextWarningType.MISSING_FILE]
        self.assertGreaterEqual(len(missing_warnings), 1)
        self.assertIn("nonexistent_file.py", missing_warnings[0].message)

    def test_contradictory_memory_warning(self):
        # Create task in RUNNING state in SQLite
        task_running = Task(
            id="task-running-1",
            project_id=self.project.id,
            title="Active Task",
            objective="In flight",
            status=TaskStatus.RUNNING,
            created_at=utc_now(),
        )
        self.store.save_task(task_running)

        # Create contradictory markdown memory claiming COMPLETED
        self.memory.create_memory(
            project_id=self.project.id,
            memory_type=MemoryType.TASK_MEMORY,
            title="Task Memory: Active Task",
            content="# Task Memory\n- Task ID: task-running-1\n- Status: COMPLETED\nAll work done.",
            relative_path=".autonomos/tasks/task-running-1.md",
            related_task_id=task_running.id,
        )

        req = ContextRequest(project_id=self.project.id, task_id=task_running.id)
        package = self.engine.assemble_context(req)

        # Must flag contradictory memory warning
        conflict_warnings = [w for w in package.warnings if w.warning_type == ContextWarningType.CONTRADICTORY_MEMORY]
        self.assertEqual(len(conflict_warnings), 1)
        self.assertIn("authoritative", conflict_warnings[0].message.lower())

    def test_100_percent_determinism_and_snapshot(self):
        req = ContextRequest(
            project_id=self.project.id,
            task_id=self.task.id,
            budget=ContextBudget(max_tokens=2500),
            focus_areas=["jwt"],
        )

        # Run 5 times and verify output dictionary is bit-for-bit identical
        results = [self.engine.assemble_context(req).to_dict() for _ in range(5)]
        first = results[0]
        for r in results[1:]:
            self.assertEqual(first["items"], r["items"])
            self.assertEqual(first["total_estimated_tokens"], r["total_estimated_tokens"])
            self.assertEqual(first["warnings"], r["warnings"])

        # Check snapshot created on disk
        snapshot_file = pathlib.Path(self.workspace_dir) / ".autonomos" / "context_snapshots" / f"{req.request_id}.json"
        self.assertTrue(snapshot_file.exists())
        saved_data = json.loads(snapshot_file.read_text())
        self.assertEqual(saved_data["task_id"], self.task.id)

    def test_realistic_60_files_repo_scenario(self):
        """
        Create 60 dummy repository files across multiple folders (db, ui, payment, auth, billing).
        Verify that ContextEngine selectively picks auth components and bounds token consumption,
        avoiding a giant context dump.
        """
        categories = ["billing", "ui", "database", "analytics", "payment", "email"]
        for cat in categories:
            cat_dir = pathlib.Path(self.workspace_dir) / "src" / cat
            cat_dir.mkdir(parents=True, exist_ok=True)
            for i in range(10):
                file_path = cat_dir / f"module_{i}.py"
                file_path.write_text(f"# {cat.capitalize()} Module {i}\ndef process_{cat}_{i}(): pass\n")

        # Total files in repo is now > 60
        all_src_files = list(pathlib.Path(self.workspace_dir).rglob("*.py"))
        self.assertGreaterEqual(len(all_src_files), 60)

        req = ContextRequest(
            project_id=self.project.id,
            task_id=self.task.id,
            budget=ContextBudget(max_tokens=1500, max_items=10),
            focus_areas=["src/auth/jwt_handler.py", "jwt", "auth"],
        )

        package = self.engine.assemble_context(req)

        # Check token bound respected
        self.assertLessEqual(package.total_estimated_tokens, 1500)
        self.assertLessEqual(len(package.items), 10)

        # Check that none of the 60 unrelated modules flooded the context package
        item_titles = [it.title for it in package.items]
        self.assertTrue(any("jwt_handler" in t for t in item_titles))
        for cat in categories:
            self.assertFalse(any(f"src/{cat}/" in t for t in item_titles))


if __name__ == "__main__":
    unittest.main()
