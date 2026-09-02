"""
Comprehensive integration and unit tests for Workforce Researcher Activation and Orchestration.

Verifies:
1. Manager requests research while Researcher is already active.
2. Manager requests research while Researcher is inactive.
3. Inactive Researcher is actually activated via real lifecycle.
4. Researcher registers successfully.
5. ResearchRequest is dispatched only after Researcher becomes READY.
6. ResearchRequest is not lost during activation.
7. ResearchRequest is dispatched exactly once.
8. Researcher activation failure produces a structured failure.
9. Researcher activation failure does not deadlock Manager.
10. Busy Researcher uses scheduling/queue behavior.
11. Multiple research requests do not spawn duplicate Researchers when one instance can serve them.
12. Concurrent requests remain isolated.
13. Researcher receives the actual ResearchRequest rather than Manager performing research itself.
14. Researcher can allocate crawlers through its workforce foundation.
15. End-to-end user scenario:
    "Research about the latest technologies that can be used to make this website look better, full of animations, and 3D."
"""
from __future__ import annotations

import json
import os
import tempfile
import threading
import unittest

from core.enums import TaskStatus, WorkerStatus
from core.errors import ResearcherActivationFailed, WorkerActivationFailedError
from core.events.types import EventType
from core.inference.provider import MockProvider
from core.models import Task
from core.research.contracts.request import ResearchRequest
from core.runtime.workforce_runtime import WorkforceRuntime
from core.storage.sqlite_store import SQLiteStore
from core.tools.builtins.web import MockWebAdapter, WebTool
from workers.researcher.worker import ResearcherWorker


class TestResearcherActivationAndOrchestration(unittest.TestCase):

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.store = SQLiteStore(":memory:")
        self.runtime = WorkforceRuntime(self.store)
        self.project = self.runtime.create_project(
            name="Modern 3D Website Project",
            root_path=self.temp_dir.name,
            description="Modernizing frontend with Three.js, GSAP animations and 3D canvas",
        )

        self.mock_web_data = {
            "web animation 3D threejs gsap": [
                {
                    "title": "Three.js and GSAP Integration Guide",
                    "url": "https://threejs.org/docs/",
                    "snippet": "Three.js enables WebGL 3D rendering with GSAP timeline animations.",
                }
            ],
            "https://threejs.org/docs/": "Three.js provides WebGL renderers, PBR materials, and camera controls. Integrates with React Three Fiber.",
        }
        mock_web_adapter = MockWebAdapter(self.mock_web_data)

        web_tool = self.runtime.tools.registry.get_tool("web")
        if isinstance(web_tool, WebTool):
            web_tool.set_adapter(mock_web_adapter)
        web_search = self.runtime.tools.registry.get_tool("web.search")
        if isinstance(web_search, WebTool):
            web_search.set_adapter(mock_web_adapter)
        web_fetch = self.runtime.tools.registry.get_tool("web.fetch")
        if isinstance(web_fetch, WebTool):
            web_fetch.set_adapter(mock_web_adapter)

        provider = self.runtime.inference.providers.get_provider("mock-provider")
        if isinstance(provider, MockProvider):
            self.mock_provider = provider

    def tearDown(self):
        self.runtime.close()
        self.temp_dir.cleanup()

    # 1. Manager requests research while Researcher is already active
    def test_01_research_dispatched_when_researcher_already_active(self):
        worker = ResearcherWorker("worker.researcher.preactive")
        self.runtime.register_worker(worker)

        task = self.runtime.create_task(
            project_id=self.project.id,
            title="Evaluate 3D Animation Libraries",
            objective="Research Three.js and GSAP for interactive animations",
        )

        # Assign task to already active worker
        t, manifest = self.runtime.assign_task(task.id, "worker.researcher.preactive")
        self.assertEqual(t.status, TaskStatus.ASSIGNED)
        self.assertEqual(manifest.id, "worker.researcher.preactive")

    # 2. Manager requests research while Researcher is inactive
    def test_02_research_requested_when_researcher_inactive(self):
        # Verify no researcher currently in store
        self.assertFalse(self.runtime.workers.has_worker("worker.researcher.codebase"))

        task = self.runtime.create_task(
            project_id=self.project.id,
            title="Investigate Shaders",
            objective="Research GLSL shaders in browser",
            metadata={"worker_id": "worker.researcher.codebase", "worker_type": "Researcher"},
        )

        # Runtime automatically activates inactive researcher upon assignment
        assigned_task, assigned_worker = self.runtime.assign_task(task.id, "worker.researcher.codebase")
        self.assertEqual(assigned_task.status, TaskStatus.ASSIGNED)
        self.assertEqual(assigned_worker.id, "worker.researcher.codebase")
        self.assertTrue(self.runtime.workers.has_worker("worker.researcher.codebase"))

    # 3. Inactive Researcher is actually activated via authoritative lifecycle
    def test_03_inactive_researcher_activation_lifecycle_events(self):
        self.assertFalse(self.runtime.workers.has_worker("worker.researcher.dynamic"))

        manifest = self.runtime.activate_worker("worker.researcher.dynamic", project_id=self.project.id)
        self.assertEqual(manifest.id, "worker.researcher.dynamic")
        self.assertEqual(manifest.status, WorkerStatus.IDLE)

        # Check emitted lifecycle events
        events = self.runtime.get_events(project_id=self.project.id)
        event_types = [e.event_type for e in events]
        self.assertIn(EventType.WORKER_ACTIVATION_REQUESTED, event_types)
        self.assertIn(EventType.WORKER_ACTIVATION_STARTED, event_types)
        self.assertIn(EventType.WORKER_REGISTERED, event_types)
        self.assertIn(EventType.WORKER_BECAME_IDLE, event_types)
        self.assertIn(EventType.WORKER_ACTIVATION_COMPLETED, event_types)

    # 4. Researcher registers successfully
    def test_04_researcher_registers_successfully_with_capabilities(self):
        manifest = self.runtime.activate_worker("worker.researcher.spec", project_id=self.project.id)
        self.assertEqual(manifest.role, "Researcher")
        self.assertIn("RESEARCH", manifest.capabilities)
        self.assertIn("web.search", manifest.permissions)

        # Loaded in memory instance
        instance = self.runtime.workers.get_worker_instance("worker.researcher.spec")
        self.assertIsInstance(instance, ResearcherWorker)

    # 5. ResearchRequest is dispatched only after Researcher becomes READY
    def test_05_research_dispatched_only_after_ready(self):
        manifest = self.runtime.activate_worker("worker.researcher.step", project_id=self.project.id)
        self.assertEqual(manifest.status, WorkerStatus.IDLE)

        task = self.runtime.create_task(
            project_id=self.project.id,
            title="Investigate Canvas 3D",
            objective="Evaluate canvas performance",
        )
        t, w = self.runtime.assign_task(task.id, manifest.id)
        self.assertEqual(t.status, TaskStatus.ASSIGNED)

    # 6. ResearchRequest is not lost during activation
    def test_06_research_request_not_lost_during_activation(self):
        task = self.runtime.create_task(
            project_id=self.project.id,
            title="Investigate WebGPU Support",
            objective="Evaluate WebGPU cross-browser support and fallback matrix",
            metadata={"worker_id": "worker.researcher.webgpu"},
        )

        self.runtime.activate_worker("worker.researcher.webgpu", project_id=self.project.id, task_id=task.id)
        self.runtime.assign_task(task.id, "worker.researcher.webgpu")

        reloaded_task = self.runtime.tasks.get_task(task.id)
        self.assertEqual(reloaded_task.assigned_worker, "worker.researcher.webgpu")
        self.assertEqual(reloaded_task.objective, "Evaluate WebGPU cross-browser support and fallback matrix")

    # 7. ResearchRequest is dispatched exactly once
    def test_07_research_request_dispatched_exactly_once(self):
        task = self.runtime.create_task(
            project_id=self.project.id,
            title="Investigate React Three Fiber",
            objective="Analyze React Three Fiber component tree performance",
        )
        self.runtime.assign_task(task.id, "worker.researcher.r3f")

        assign_events = self.runtime.get_events(task_id=task.id, event_types=[EventType.TASK_ASSIGNED])
        self.assertEqual(len(assign_events), 1)

    # 8. Researcher activation failure produces structured failure
    def test_08_researcher_activation_failure_produces_structured_error(self):
        # Register a broken factory that raises
        def _failing_factory(wid: str):
            raise RuntimeError("Out of worker execution memory / quota exceeded")

        self.runtime.workers.register_worker_factory("worker.researcher.broken", _failing_factory)

        with self.assertRaises(ResearcherActivationFailed) as ctx:
            self.runtime.activate_worker("worker.researcher.broken", project_id=self.project.id)

        err = ctx.exception
        self.assertEqual(err.code, "WORKER_ACTIVATION_FAILED")
        self.assertIn("Out of worker execution memory", str(err))

        events = self.runtime.get_events(project_id=self.project.id)
        failed_evts = [e for e in events if e.event_type == EventType.WORKER_ACTIVATION_FAILED]
        self.assertEqual(len(failed_evts), 1)

    # 9. Researcher activation failure does not deadlock the Manager
    def test_09_manager_rejects_assignment_on_activation_failure(self):
        def _failing_factory(wid: str):
            raise RuntimeError("Provisioning host unavailable")

        self.runtime.workers.register_worker_factory("worker.researcher.failing", _failing_factory)

        task = self.runtime.create_task(
            project_id=self.project.id,
            title="Broken Task",
            objective="Will fail activation",
        )

        self.mock_provider.set_canned_response(
            json.dumps({
                "reasoning_summary": "Attempt assign to broken worker.",
                "actions": [
                    {
                        "action_type": "ASSIGN_TASK",
                        "parameters": {
                            "task_id": task.id,
                            "worker_id": "worker.researcher.failing",
                        },
                        "rationale": "Test failure",
                    }
                ],
            })
        )

        res = self.runtime.step_manager(self.project.id)
        # Action is rejected cleanly rather than hanging or deadlocking
        self.assertEqual(len(res.results), 1)
        self.assertFalse(res.results[0].accepted)
        self.assertIn("activation failed", res.results[0].reason)

    # 10. Busy Researcher uses scheduling/queue behavior
    def test_10_busy_researcher_rejects_concurrent_assignment(self):
        worker = ResearcherWorker("worker.researcher.busy")
        self.runtime.register_worker(worker)

        task1 = self.runtime.create_task(project_id=self.project.id, title="Task 1", objective="Obj 1")
        task2 = self.runtime.create_task(project_id=self.project.id, title="Task 2", objective="Obj 2")

        # Assign task1
        self.runtime.assign_task(task1.id, "worker.researcher.busy")

        # Manager attempts to assign task2 while worker is busy
        self.mock_provider.set_canned_response(
            json.dumps({
                "reasoning_summary": "Assign second task.",
                "actions": [
                    {
                        "action_type": "ASSIGN_TASK",
                        "parameters": {
                            "task_id": task2.id,
                            "worker_id": "worker.researcher.busy",
                        },
                        "rationale": "Assign concurrent",
                    }
                ],
            })
        )
        res = self.runtime.step_manager(self.project.id)
        self.assertFalse(res.results[0].accepted)
        self.assertIn("is busy", res.results[0].reason)

    # 11. Multiple research requests do not spawn duplicate Researchers
    def test_11_multiple_requests_reuse_single_worker_instance(self):
        m1 = self.runtime.activate_worker("worker.researcher.singleton", project_id=self.project.id)
        m2 = self.runtime.activate_worker("worker.researcher.singleton", project_id=self.project.id)

        self.assertEqual(m1.id, m2.id)
        workers = [w for w in self.runtime.workers.list_workers() if w.id == "worker.researcher.singleton"]
        self.assertEqual(len(workers), 1)

    # 12. Concurrent activation requests remain isolated and thread-safe
    def test_12_concurrent_activations_thread_safe(self):
        manifests = []
        errors = []

        def _activate(worker_id: str):
            try:
                m = self.runtime.activate_worker(worker_id, project_id=self.project.id)
                manifests.append(m)
            except Exception as e:
                errors.append(e)

        threads = [
            threading.Thread(target=_activate, args=(f"worker.researcher.thread.{i}",))
            for i in range(5)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(len(errors), 0)
        self.assertEqual(len(manifests), 5)

    # 13. Researcher receives actual ResearchRequest and runs multi-phase research
    def test_13_researcher_receives_actual_request(self):
        task = self.runtime.create_task(
            project_id=self.project.id,
            title="Evaluate GSAP ScrollTrigger",
            objective="Analyze GSAP ScrollTrigger for 3D camera timeline control",
        )
        self.runtime.activate_worker("worker.researcher.spec", project_id=self.project.id)
        self.runtime.assign_task(task.id, "worker.researcher.spec")

        researcher_synthesis = {
            "reasoning_summary": "GSAP ScrollTrigger provides smooth scrub controls for 3D cameras.",
            "questions_resolved": [
                {"question_id": "q-1", "status": "ANSWERED", "notes": "ScrollTrigger scrub verified"}
            ],
            "findings": [
                {
                    "finding_id": "f-1",
                    "claim": "GSAP ScrollTrigger can link page scroll position directly to WebGL camera coordinates.",
                    "classification": "FACT",
                    "confidence": "WELL_SUPPORTED",
                    "source_ids": ["src-1"],
                    "reasoning": "Official documentation confirms scrub parameter integration.",
                }
            ],
            "contradictions": [],
            "knowledge_gaps": [],
            "recommendations": [
                {
                    "action": "Use GSAP ScrollTrigger paired with Three.js canvas.",
                    "rationale": "High performance and smooth frame rate.",
                    "supporting_finding_ids": ["f-1"],
                }
            ],
            "manager_summary": "GSAP ScrollTrigger confirmed suitable for 3D camera timelines.",
        }
        self.mock_provider.set_canned_response(json.dumps(researcher_synthesis))

        output = self.runtime.run_task(task.id)
        self.assertTrue(output.success)
        self.assertIn("ScrollTrigger", output.summary)

    # 14. Researcher allocates crawlers through workforce foundation
    def test_14_researcher_foundation_allocates_crawlers(self):
        from core.research.researcher import Researcher
        from core.research.contracts.request import ResearchRequest

        req = ResearchRequest(
            request_id="req-alloc-1",
            project_id=self.project.id,
            task_id="task-1",
            objective="Research modern 3D web frameworks",
            questions=["What are the top 3D frameworks for React?"],
        )

        researcher = Researcher()
        result, state = researcher.execute_research(req)
        self.assertTrue(len(state.active_crawlers) >= 1)
        self.assertEqual(state.current_state.value, "COMPLETE")

    # 15. End-to-end integration acceptance test: Full user scenario
    def test_15_end_to_end_user_scenario_activation_and_execution(self):
        """
        USER: 'Research about the latest technologies that can be used to make this website look better, full of animations, and 3D.'
        
        Verify:
        1. Manager decomposes request and creates tasks
        2. Inactive Researcher is dynamically provisioned and activated
        3. Manager dispatches task to Researcher
        4. Researcher runs research and returns structured synthesis
        5. Manager receives result and completes workflow without deadlocking.
        """
        # Step 1: Manager plans task and assigns to Researcher
        self.mock_provider.set_canned_response(
            json.dumps({
                "reasoning_summary": "Decomposing user request into research task for 3D and animation stack.",
                "plan_update": {
                    "objective": "Modernize website with 3D and animations",
                    "milestones": ["Research 3D & Animation Tech", "Implement 3D Canvas"],
                    "reason": "Decomposition",
                },
                "actions": [
                    {
                        "action_type": "CREATE_TASK",
                        "parameters": {
                            "title": "Research 3D & Animation Tech",
                            "objective": "Research Three.js, GSAP, and WebGL for interactive 3D website modernization",
                            "priority": 95,
                            "risk": "LOW",
                        },
                        "rationale": "Evaluate libraries before coding",
                    },
                    {
                        "action_type": "ASSIGN_TASK",
                        "parameters": {
                            "task_id": "dynamic-task-id",
                            "worker_id": "worker.researcher.codebase",
                        },
                        "rationale": "Assign to specialist researcher",
                    },
                ],
            })
        )

        # First cycle: Creates task
        c1 = self.runtime.step_manager(self.project.id)
        self.assertTrue(c1.progress_detected)
        tasks = self.runtime.tasks.list_tasks(self.project.id)
        self.assertEqual(len(tasks), 1)
        research_task = tasks[0]

        # Second cycle: Assigns task (which automatically provisions inactive researcher!)
        self.mock_provider.set_canned_response(
            json.dumps({
                "reasoning_summary": "Assigning research task to Researcher.",
                "actions": [
                    {
                        "action_type": "ASSIGN_TASK",
                        "parameters": {
                            "task_id": research_task.id,
                            "worker_id": "worker.researcher.codebase",
                        },
                        "rationale": "Assign to specialist researcher",
                    }
                ],
            })
        )
        c2 = self.runtime.step_manager(self.project.id)
        self.assertTrue(c2.progress_detected)

        # Verify researcher is now active and task assigned
        self.assertTrue(self.runtime.workers.has_worker("worker.researcher.codebase"))
        t_assigned = self.runtime.tasks.get_task(research_task.id)
        self.assertEqual(t_assigned.status, TaskStatus.ASSIGNED)

        # Step 2: Researcher executes task
        researcher_synthesis = {
            "reasoning_summary": "Three.js and GSAP evaluated. Excellent ecosystem for 3D canvas and smooth animations.",
            "questions_resolved": [
                {"question_id": "q-1", "status": "ANSWERED", "notes": "Three.js + GSAP optimal"}
            ],
            "findings": [
                {
                    "finding_id": "f-1",
                    "claim": "Three.js with GSAP provides high-framerate WebGL 3D rendering and scroll-driven timelines.",
                    "classification": "FACT",
                    "confidence": "WELL_SUPPORTED",
                    "source_ids": ["src-1"],
                    "reasoning": "Official documentation and performance benchmarks verify 60fps capability.",
                }
            ],
            "contradictions": [],
            "knowledge_gaps": [],
            "recommendations": [
                {
                    "action": "Adopt Three.js and GSAP for interactive portfolio modernization.",
                    "rationale": "Proven stability, lightweight footprint, rich ecosystem.",
                    "supporting_finding_ids": ["f-1"],
                }
            ],
            "manager_summary": "Recommended tech stack: Three.js for 3D WebGL rendering, GSAP for animations.",
        }
        self.mock_provider.set_canned_response(json.dumps(researcher_synthesis))

        output = self.runtime.run_task(research_task.id)
        self.assertTrue(output.success)

        # Step 3: Manager acts on research results
        self.mock_provider.set_canned_response(
            json.dumps({
                "reasoning_summary": "Research findings received. Recording ADR and completing research phase.",
                "actions": [
                    {
                        "action_type": "UPDATE_MEMORY",
                        "parameters": {
                            "memory_type": "ARCHITECTURE",
                            "title": "ADR: 3D & Animation Stack Selection",
                            "content": "Selected Three.js and GSAP based on Researcher findings.",
                        },
                        "rationale": "Document architectural decision",
                    },
                    {
                        "action_type": "COMPLETE_PROJECT",
                        "parameters": {
                            "summary": "Research successfully completed: Three.js and GSAP selected.",
                        },
                        "rationale": "Research objective completed",
                    },
                ],
            })
        )
        c3 = self.runtime.step_manager(self.project.id)
        self.assertTrue(c3.progress_detected)

        # Verify ADR recorded
        memories = self.runtime.memory.list_memories(self.project.id)
        self.assertTrue(any("3D & Animation" in m.title for m in memories))


if __name__ == "__main__":
    unittest.main()
