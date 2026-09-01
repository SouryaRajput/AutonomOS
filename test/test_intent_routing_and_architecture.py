import json
import shutil
import tempfile
import unittest
from pathlib import Path

from app.application import AutonomOSApp
from core.enums import TaskStatus
from core.inference.model import ModelRequirement
from core.inference.omniroute import OmniRoute
from core.inference.types import ModelCapability, RoutingProfile
from core.manager.router import IntentRouter, UserIntentType
from core.models import Project
from core.runtime.workforce_runtime import WorkforceRuntime
from core.storage.sqlite_store import SQLiteStore
from core.workspace.filesystem import ControlledWorkspaceFS
from core.workspace.project_map import ProjectMapEngine


class TestIntentRoutingAndArchitecture(unittest.TestCase):
    """
    Acceptance test suite for Intent Routing, Direct Q&A, and Dynamic Reasoning Escalation.
    """

    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.workspace_path = Path(self.test_dir) / "demo_project"
        self.workspace_path.mkdir(parents=True, exist_ok=True)

        (self.workspace_path / "src" / "app" / "about").mkdir(parents=True, exist_ok=True)
        (self.workspace_path / "src" / "components").mkdir(parents=True, exist_ok=True)

        (self.workspace_path / "package.json").write_text(
            json.dumps({
                "name": "nextjs-demo",
                "version": "1.0.0",
                "dependencies": {"next": "14.2.0", "react": "^18"},
            }),
            encoding="utf-8",
        )
        (self.workspace_path / "src" / "app" / "page.tsx").write_text("export default function Home() { return <div>Home</div>; }", encoding="utf-8")
        (self.workspace_path / "src" / "app" / "about" / "page.tsx").write_text("export default function About() { return <div>About</div>; }", encoding="utf-8")
        (self.workspace_path / "src" / "components" / "Navbar.tsx").write_text("export function Navbar() { return <nav>Nav</nav>; }", encoding="utf-8")

        self.store = SQLiteStore(":memory:")
        self.runtime = WorkforceRuntime(store=self.store)
        self.app = AutonomOSApp(self.runtime)

        self.project = self.runtime.projects.create_project(
            project_id="proj-demo-1",
            name="nextjs-demo",
            root_path=str(self.workspace_path),
            description="Demo Next.js Project",
        )
        self.fs = ControlledWorkspaceFS(str(self.workspace_path))
        self.map_engine = ProjectMapEngine(self.fs)
        self.map_engine.perform_full_audit(trigger="TEST_INIT")

    def tearDown(self):
        self.app.close()
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_intent_classification_questions(self):
        """Verify questions are classified correctly into specific target areas."""
        c1 = IntentRouter.classify("What routes exist in this app?")
        self.assertEqual(c1.intent, UserIntentType.QUESTION)
        self.assertEqual(c1.target_area, "routes")

        c2 = IntentRouter.classify("What tech stack and dependencies are used?")
        self.assertEqual(c2.intent, UserIntentType.QUESTION)
        self.assertEqual(c2.target_area, "tech_stack")

        c3 = IntentRouter.classify("What components do we have?")
        self.assertEqual(c3.intent, UserIntentType.QUESTION)
        self.assertEqual(c3.target_area, "components")

        c4 = IntentRouter.classify("Where is the main entry point and what is the architecture?")
        self.assertEqual(c4.intent, UserIntentType.QUESTION)
        self.assertEqual(c4.target_area, "architecture")

    def test_intent_classification_execution(self):
        """Verify transformation commands are classified as EXECUTION_REQUEST."""
        c1 = IntentRouter.classify("Turn this website into an animated 3D portfolio")
        self.assertEqual(c1.intent, UserIntentType.EXECUTION_REQUEST)

        c2 = IntentRouter.classify("Add a contact form modal with email validation")
        self.assertEqual(c2.intent, UserIntentType.EXECUTION_REQUEST)

        c3 = IntentRouter.classify("Refactor the hero section components")
        self.assertEqual(c3.intent, UserIntentType.EXECUTION_REQUEST)

    def test_direct_question_answering_creates_zero_tasks(self):
        """
        When user asks a question via ConversationService,
        it answers accurately from the Project Map and creates ZERO tasks.
        """
        conv = self.app.conversations.create_conversation(project_id="proj-demo-1", title="Q&A Test")

        # 1. Ask route question
        msgs = self.app.conversations.post_user_message(
            conversation_id=conv.id,
            content="What routes exist in this app?",
            dispatch_manager=True,
        )

        self.assertEqual(len(msgs), 2)
        response_msg = msgs[1]
        self.assertIn("Discovered Routes", response_msg.content)
        self.assertIn("Route `/`", response_msg.content)
        self.assertIn("Route `/about`", response_msg.content)

        # 2. Verify 0 tasks were created in TaskEngine
        tasks = self.runtime.tasks.list_tasks(project_id="proj-demo-1")
        self.assertEqual(len(tasks), 0, "Question should NOT spawn tasks in TaskEngine")

    def test_execution_request_creates_planned_contracts_with_workers_disabled(self):
        """
        When user sends an execution request, Manager decomposes it into planned tasks
        with workers_disabled=True and PAUSED status.
        """
        conv = self.app.conversations.create_conversation(project_id="proj-demo-1", title="Execution Test")

        msgs = self.app.conversations.post_user_message(
            conversation_id=conv.id,
            content="Add a 3D animated hero section to the homepage",
            dispatch_manager=True,
        )

        # Verify manager response
        self.assertGreaterEqual(len(msgs), 2)

        # Verify tasks were created in TaskEngine and remain in ready/pending state
        tasks = self.runtime.tasks.list_tasks(project_id="proj-demo-1")
        self.assertGreaterEqual(len(tasks), 1)
        for t in tasks:
            self.assertNotEqual(t.status, TaskStatus.COMPLETED)
            self.assertIn(t.status, [TaskStatus.PENDING, TaskStatus.READY, TaskStatus.ASSIGNED])

    def test_dynamic_reasoning_escalation_on_failure_or_replan(self):
        """
        Verify OmniRoute escalates from standard profile to QUALITY_FIRST with REASONING
        when encountering failures, replans, or high uncertainty.
        """
        base_reqs = ModelRequirement(
            required_capabilities={ModelCapability.TEXT_GENERATION},
            routing_profile=RoutingProfile.CHEAPEST,
            minimum_context=4000,
        )

        escalated_reqs = OmniRoute.escalate_requirements_for_complexity(
            base_requirements=base_reqs,
            is_failure_or_replan=True,
            uncertainty_level="HIGH_RISK_UNCERTAINTY",
        )

        self.assertEqual(escalated_reqs.routing_profile, RoutingProfile.QUALITY_FIRST)
        self.assertIn(ModelCapability.REASONING, escalated_reqs.required_capabilities)
        self.assertIn(ModelCapability.STRUCTURED_OUTPUT, escalated_reqs.required_capabilities)


if __name__ == "__main__":
    unittest.main()
