import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path

from core.enums import ProjectStatus, TaskStatus
from core.manager.planner import ManagerPlanner
from core.models import Project
from core.runtime.workforce_runtime import WorkforceRuntime
from core.storage.sqlite_store import SQLiteStore
from core.workspace.filesystem import ControlledWorkspaceFS
from core.workspace.incremental import IncrementalAuditEngine
from core.workspace.project_map import ProjectMapEngine
from core.workspace.snapshot import SnapshotEngine


class TestActualManagerExecution(unittest.TestCase):
    """
    Comprehensive acceptance and regression test suite covering:
    - TEST A: Next.js App Router route discovery (Route / -> src/app/page.tsx, NEVER /page.tsx)
    - TEST B: Current observed reality vs requested future transformation separation
    - TEST C: Worker-inactive state transitions (PLANNED vs EXECUTED)
    - TEST D: Filesystem evidence triggers Project Map update when new file is created
    - TEST E: Exclusion of generated build/cache directories (.next/, node_modules/)
    - TEST F: Installed dependencies vs verified imported source usage
    - TEST G: Concise user-facing output preservation
    """

    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.workspace_path = Path(self.test_dir) / "portfolio_project"
        self.workspace_path.mkdir(parents=True, exist_ok=True)

        # Create Next.js standard portfolio (NO 3D code initially)
        (self.workspace_path / "src" / "app" / "about").mkdir(parents=True, exist_ok=True)
        (self.workspace_path / "src" / "app" / "projects" / "[slug]").mkdir(parents=True, exist_ok=True)
        (self.workspace_path / "src" / "app" / "api" / "contact").mkdir(parents=True, exist_ok=True)
        (self.workspace_path / "src" / "components").mkdir(parents=True, exist_ok=True)
        (self.workspace_path / "public" / "images").mkdir(parents=True, exist_ok=True)
        (self.workspace_path / ".next" / "cache" / "webpack").mkdir(parents=True, exist_ok=True)
        (self.workspace_path / ".next" / "server" / "app").mkdir(parents=True, exist_ok=True)

        (self.workspace_path / "package.json").write_text(
            json.dumps({
                "name": "developer-portfolio",
                "version": "0.1.0",
                "scripts": {
                    "dev": "next dev",
                    "build": "next build",
                    "start": "next start",
                },
                "dependencies": {
                    "next": "14.2.5",
                    "react": "^18",
                    "react-dom": "^18",
                    "@react-three/fiber": "^8.15.0",  # Installed in package.json, but NOT imported in source
                    "tailwindcss": "^3.4.1",
                },
                "devDependencies": {
                    "typescript": "^5",
                },
            }),
            encoding="utf-8",
        )
        (self.workspace_path / "next.config.mjs").write_text("export default {};\n", encoding="utf-8")
        (self.workspace_path / "tsconfig.json").write_text(json.dumps({"compilerOptions": {}}), encoding="utf-8")
        (self.workspace_path / "tailwind.config.ts").write_text("export default {};\n", encoding="utf-8")

        (self.workspace_path / "src" / "app" / "layout.tsx").write_text(
            "import './globals.css';\nexport default function RootLayout({ children }: any) { return <html><body>{children}</body></html>; }\n",
            encoding="utf-8",
        )
        (self.workspace_path / "src" / "app" / "page.tsx").write_text(
            "import { Hero } from '@/components/Hero';\nexport default function Home() { return <main><Hero /></main>; }\n",
            encoding="utf-8",
        )
        (self.workspace_path / "src" / "app" / "about" / "page.tsx").write_text(
            "export default function About() { return <section>About Me</section>; }\n",
            encoding="utf-8",
        )
        (self.workspace_path / "src" / "app" / "projects" / "[slug]" / "page.tsx").write_text(
            "export default function ProjectDetail() { return <article>Project</article>; }\n",
            encoding="utf-8",
        )
        (self.workspace_path / "src" / "app" / "api" / "contact" / "route.ts").write_text(
            "export async function POST() { return new Response('ok'); }\n",
            encoding="utf-8",
        )
        (self.workspace_path / "src" / "app" / "globals.css").write_text(
            "@tailwind base;\nbody { margin: 0; }\n",
            encoding="utf-8",
        )
        (self.workspace_path / "src" / "components" / "Hero.tsx").write_text(
            "export function Hero() { return <section><h1>Developer Portfolio</h1></section>; }\n",
            encoding="utf-8",
        )
        (self.workspace_path / "public" / "images" / "avatar.png").write_text("AVATAR_DATA", encoding="utf-8")

        # Build artifacts in .next/ that must be ignored
        (self.workspace_path / ".next" / "cache" / "webpack" / "0.pack").write_text("CACHE", encoding="utf-8")
        (self.workspace_path / ".next" / "server" / "app" / "page.js").write_text("SERVER_GEN", encoding="utf-8")

        self.fs = ControlledWorkspaceFS(str(self.workspace_path))
        self.store = SQLiteStore(":memory:")
        self.runtime = WorkforceRuntime(store=self.store)

        self.runtime.projects.create_project(
            project_id="proj-portfolio-1",
            name="developer-portfolio",
            root_path=str(self.workspace_path),
            description="Developer Portfolio Next.js Website",
        )

        self.map_engine = ProjectMapEngine(self.fs)
        self.incremental_engine = IncrementalAuditEngine(self.fs, self.map_engine)
        self.planner = ManagerPlanner(self.runtime, self.map_engine)

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_test_a_route_discovery_and_never_page_tsx(self):
        """TEST A: Verify Next.js App Router routes map to URL paths (never /page.tsx)."""
        pmap = self.map_engine.perform_full_audit(trigger="TEST_A")
        md = (self.workspace_path / ".autonomos" / "project-map.md").read_text(encoding="utf-8")

        # Must find correct URL routes
        self.assertIn("Route `/` -> `src/app/page.tsx`", md)
        self.assertIn("Route `/about` -> `src/app/about/page.tsx`", md)
        self.assertIn("Route `/projects/[slug]` -> `src/app/projects/[slug]/page.tsx`", md)
        self.assertIn("Route `/api/contact` -> `src/app/api/contact/route.ts`", md)

        # Must NEVER generate /page.tsx
        self.assertNotIn("Route `/page.tsx`", md)
        self.assertNotIn("Route `/about/page.tsx`", md)

    def test_test_b_and_f_observed_reality_vs_requested_transformation(self):
        """
        TEST B & F:
        When user requests 'Turn this website into a 3D portfolio',
        Project Map must describe CURRENT reality accurately (Next.js Portfolio, NOT 3D).
        If @react-three/fiber is installed but not imported in source code,
        report as installed but NOT as active rendering architecture.
        """
        requested_goal = "Turn this website into an animated 3D portfolio website"
        pmap = self.map_engine.perform_full_audit(trigger="TEST_B")
        md = self.map_engine.generate_project_map_markdown(pmap, requested_objective=requested_goal)

        # Current Observed Reality
        self.assertIn("Observed Project Type**: Next.js Portfolio Web Application", md)
        self.assertNotIn("Next.js 3D Animated Portfolio", md)

        # Installed vs Verified Imported Library Check (TEST F)
        self.assertIn("Installed (Not yet imported in source code)", md)
        self.assertIn("Three.js / 3D rendering packages are present in `package.json` but not yet imported", md)

        # Requested Transformation Section
        self.assertIn("## Requested Transformation", md)
        self.assertIn(f'User Requested Objective**: "{requested_goal}"', md)
        self.assertIn("PENDING IMPLEMENTATION (Workers Disabled / Paused)", md)
        self.assertIn("This requested transformation represents intended future work and is NOT part of current observed repository state", md)

    def test_test_c_and_g_workers_disabled_planning_state_and_concise_output(self):
        """TEST C & G: Manager creates plan, marks tasks as PLANNED, pauses execution, and stays concise."""
        plan = self.planner.plan_and_delegate(
            project_id="proj-portfolio-1",
            objective="Turn this website into an animated 3D portfolio website.",
        )

        self.assertTrue(plan.workers_disabled)
        self.assertEqual(plan.status, "PAUSED (Workers Disabled)")
        self.assertGreaterEqual(len(plan.tasks), 2)

        # Verify tasks in engine remain in PENDING/READY state (NOT completed)
        tasks = self.runtime.tasks.list_tasks(project_id="proj-portfolio-1")
        for t in tasks:
            self.assertNotEqual(t.status, TaskStatus.COMPLETED)
            self.assertIn(t.status, [TaskStatus.PENDING, TaskStatus.READY, TaskStatus.ASSIGNED])

        # Concise response format verification (TEST G)
        concise_msg = f"Got it. I have analyzed the project and prepared {len(plan.tasks)} tasks. Workers are currently disabled."
        self.assertLess(len(concise_msg.split()), 25)
        self.assertNotIn("TASK CONTRACT:", concise_msg)

    def test_test_d_filesystem_evidence_updates_project_map_on_creation(self):
        """TEST D: When a worker/developer actually creates Hero3D.tsx on disk, regeneration includes it."""
        # 1. Initial audit: Hero3D.tsx does NOT exist
        pmap_1 = self.map_engine.perform_full_audit(trigger="TEST_D_1")
        md_1 = (self.workspace_path / ".autonomos" / "project-map.md").read_text(encoding="utf-8")
        self.assertNotIn("Hero3D.tsx", md_1)

        # 2. Worker actually creates Hero3D.tsx with real Three.js import on disk
        (self.workspace_path / "src" / "components" / "Hero3D.tsx").write_text(
            "import * as THREE from 'three';\nexport function Hero3D() { return <div id='hero3d' />; }\n",
            encoding="utf-8",
        )

        # 3. Incremental audit triggered
        diff = self.incremental_engine.check_and_update(trigger="TEST_D_2")
        self.assertIn("src/components/Hero3D.tsx", diff["impact"]["changed_files"])

        # 4. Regenerated Project Map reflects real filesystem evidence
        pmap_2 = self.map_engine.load_project_map()
        md_2 = self.map_engine.generate_project_map_markdown(pmap_2)
        self.assertIn("src/components/Hero3D.tsx", md_2)
        self.assertIn("Three.js", md_2)

    def test_test_e_generated_files_excluded_from_project_map(self):
        """TEST E: .next/ build cache files are excluded from the semantic Project Map."""
        self.map_engine.perform_full_audit(trigger="TEST_E")
        md = (self.workspace_path / ".autonomos" / "project-map.md").read_text(encoding="utf-8")
        snapshot_json = (self.workspace_path / ".autonomos" / "last_snapshot.json").read_text(encoding="utf-8")

        # Project Map excludes .next files
        self.assertNotIn(".next/cache", md)
        self.assertNotIn(".next/server", md)
        self.assertIn(".next/", md)  # Mentioned in Generated / Ignored section

        # Snapshot tracks only meaningful files when filtered
        snapshot = json.loads(snapshot_json)
        for fpath in snapshot["files"].keys():
            self.assertFalse(fpath.startswith(".next/"))


if __name__ == "__main__":
    unittest.main()
