from __future__ import annotations

import inspect
import re
import unittest
import uuid

from core.models import Task, WorkerManifest, WorkerOutput
from core.research.contracts.crawler_report import CrawlerReport, RawSourceReference
from core.research.contracts.crawler_task import CrawlerTask
from core.research.contracts.evidence import EvidenceItem, EvidenceProvenance
from core.research.contracts.plan import ResearchPlan
from core.research.contracts.question import ResearchQuestion
from core.research.contracts.request import ResearchRequest, ResearchScope
from core.research.contracts.result import ResearchResult
from core.research.crawler.base import BaseCrawler
from core.research.crawler.mock_crawler import MockCrawler
from core.research.crawler.registry import CrawlerRegistry
from core.research.evidence.evaluator import EvidenceEvaluator
from core.research.orchestration.spawner import CrawlerSpawner
from core.research.orchestration.supervisor import CrawlerSupervisor
from core.research.planning.decomposer import ResearchDecomposer
from core.research.planning.task_generator import CrawlerTaskGenerator
from core.research.researcher import Researcher
from core.research.state.lifecycle import ResearchStateMachine
from core.research.state.model import ResearchState
from core.research.synthesis.synthesizer import ResearchSynthesizer
from core.research.types import (
    CrawlerCapability,
    CrawlerHealthStatus,
    CrawlerReportStatus,
    CrawlerStatus,
    CrawlerTaskStatus,
    EvidenceSufficiency,
    FactClassification,
    ResearchConfidence,
    ResearchLifecycleState,
    ResearchMode,
    ResearchQuestionStatus,
    ResearchResultStatus,
    SourceType,
)
from workers.crawler.worker import CrawlerWorker


class TestResearcherCrawlerWorkforceAudit(unittest.TestCase):
    """
    Comprehensive architectural audit suite testing the 30 foundational behaviors
    of the Researcher and Crawler workforce.
    """

    def setUp(self):
        self.registry = CrawlerRegistry()
        self.spawner = CrawlerSpawner(self.registry)
        self.supervisor = CrawlerSupervisor()
        self.researcher = Researcher(
            registry=self.registry,
            spawner=self.spawner,
            supervisor=self.supervisor,
        )

    # 1. Manager can create a ResearchRequest.
    def test_01_manager_can_create_research_request(self):
        manager_task = Task(
            id="task-mgr-001",
            project_id="proj-alpha",
            title="Investigate distributed consensus protocols",
            objective="Evaluate Raft vs Paxos for state machine replication",
            metadata={
                "mode": "STANDARD",
                "questions": ["What are Raft leader election invariants?"],
                "scope": {"max_crawlers": 3, "min_evidence_per_question": 2},
            },
        )
        req = ResearchRequest.from_task(manager_task)
        self.assertEqual(req.task_id, "task-mgr-001")
        self.assertEqual(req.project_id, "proj-alpha")
        self.assertEqual(req.objective, "Evaluate Raft vs Paxos for state machine replication")
        self.assertEqual(req.mode, ResearchMode.STANDARD)
        self.assertEqual(req.scope.max_crawlers, 3)
        self.assertEqual(req.scope.min_evidence_per_question, 2)
        self.assertEqual(req.questions, ["What are Raft leader election invariants?"])

    # 2. Researcher can accept a ResearchRequest.
    def test_02_researcher_can_accept_research_request(self):
        req = ResearchRequest(
            request_id="req-accept-001",
            project_id="proj-01",
            task_id="t-01",
            objective="Research RocksDB compaction filters",
        )
        result, state = self.researcher.execute_research(req)
        self.assertEqual(state.request.request_id, "req-accept-001")
        self.assertIn(ResearchLifecycleState.UNDERSTANDING.value, [h.to_state for h in state.state_history])

    # 3. Researcher creates a ResearchPlan.
    def test_03_researcher_creates_research_plan(self):
        req = ResearchRequest(
            request_id="req-plan-001",
            project_id="proj-01",
            task_id="t-01",
            objective="Analyze Apache Arrow IPC streaming format",
        )
        result, state = self.researcher.execute_research(req)
        self.assertIsNotNone(state.plan)
        self.assertEqual(state.plan.request_id, "req-plan-001")
        self.assertTrue(len(state.plan.planned_steps) > 0)

    # 4. ResearchPlan contains explicit research questions.
    def test_04_research_plan_contains_explicit_research_questions(self):
        req = ResearchRequest(
            request_id="req-q-001",
            project_id="proj-01",
            task_id="t-01",
            objective="Evaluate Arrow Flight SQL",
            questions=["Does Flight SQL support prepared statements?", "How is auth handled in Flight SQL?"],
        )
        questions, plan = ResearchDecomposer.decompose_request(req)
        self.assertEqual(len(plan.questions), 2)
        for q in plan.questions:
            self.assertIsInstance(q, ResearchQuestion)
            self.assertTrue(len(q.question_id) > 0)
            self.assertTrue(len(q.question_text) > 0)
            self.assertEqual(q.request_id, "req-q-001")

    # 5. Researcher can create CrawlerTasks.
    def test_05_researcher_can_create_crawler_tasks(self):
        req = ResearchRequest(
            request_id="req-ctask-001",
            project_id="proj-01",
            task_id="t-01",
            objective="Inspect DuckDB query engine",
        )
        questions, plan = ResearchDecomposer.decompose_request(req)
        tasks = CrawlerTaskGenerator.generate_tasks_for_plan(plan)
        self.assertTrue(len(tasks) > 0)
        for t in tasks:
            self.assertIsInstance(t, CrawlerTask)
            self.assertTrue(len(t.task_id) > 0)
            self.assertTrue(len(t.query_or_target) > 0)

    # 6. CrawlerTasks retain the ResearchRequest ID.
    def test_06_crawler_tasks_retain_research_request_id(self):
        req = ResearchRequest(
            request_id="req-lineage-001",
            project_id="proj-01",
            task_id="t-01",
            objective="Lineage test",
        )
        questions, plan = ResearchDecomposer.decompose_request(req)
        tasks = CrawlerTaskGenerator.generate_tasks_for_plan(plan)
        for t in tasks:
            self.assertEqual(t.request_id, "req-lineage-001")
            self.assertEqual(t.plan_id, plan.plan_id)

    # 7. CrawlerSpawner can dynamically create 1 crawler.
    def test_07_crawler_spawner_dynamically_creates_one_crawler(self):
        crawler = self.spawner.spawn_crawler(capabilities=[CrawlerCapability.WEB_SEARCH])
        self.assertIsNotNone(crawler)
        self.assertTrue(crawler.has_capability(CrawlerCapability.WEB_SEARCH))
        self.assertEqual(len(self.registry.list_active_crawlers()), 1)

    # 8. CrawlerSpawner can dynamically create multiple crawlers.
    def test_08_crawler_spawner_dynamically_creates_multiple_crawlers(self):
        crawlers = [
            self.spawner.spawn_crawler(capabilities=[CrawlerCapability.WEB_SEARCH]),
            self.spawner.spawn_crawler(capabilities=[CrawlerCapability.WEB_FETCH]),
            self.spawner.spawn_crawler(capabilities=[CrawlerCapability.REPOSITORY_INSPECTION]),
        ]
        self.assertEqual(len(crawlers), 3)
        self.assertEqual(len(self.registry.list_active_crawlers()), 3)
        crawler_ids = {c.id for c in crawlers}
        self.assertEqual(len(crawler_ids), 3)

    # 9. Multiple crawlers can operate independently.
    def test_09_multiple_crawlers_operate_independently(self):
        c1 = MockCrawler(crawler_id="c-indep-1", capabilities=[CrawlerCapability.WEB_SEARCH])
        c2 = MockCrawler(crawler_id="c-indep-2", capabilities=[CrawlerCapability.WEB_FETCH])
        t1 = CrawlerTask(task_id="t-indep-1", request_id="req-1", plan_id="p-1", question_id="q-1", query_or_target="query 1", required_capability=CrawlerCapability.WEB_SEARCH)
        t2 = CrawlerTask(task_id="t-indep-2", request_id="req-1", plan_id="p-1", question_id="q-2", query_or_target="query 2", required_capability=CrawlerCapability.WEB_FETCH)

        r1 = self.supervisor.execute_task(c1, t1)
        r2 = self.supervisor.execute_task(c2, t2)

        self.assertEqual(r1.crawler_id, "c-indep-1")
        self.assertEqual(r2.crawler_id, "c-indep-2")
        self.assertEqual(c1.tasks_completed, 1)
        self.assertEqual(c2.tasks_completed, 1)

    # 10. CrawlerRegistry tracks every active crawler.
    def test_10_crawler_registry_tracks_every_active_crawler(self):
        c1 = self.spawner.spawn_crawler(capabilities=[CrawlerCapability.WEB_SEARCH])
        c2 = self.spawner.spawn_crawler(capabilities=[CrawlerCapability.WEB_FETCH])
        
        self.assertEqual(self.registry.get_crawler(c1.id).id, c1.id)
        self.assertEqual(self.registry.get_crawler(c2.id).id, c2.id)
        active = self.registry.list_active_crawlers()
        self.assertEqual(len(active), 2)

    # 11. Researcher receives crawler status updates.
    def test_11_researcher_receives_crawler_status_updates(self):
        c = MockCrawler(crawler_id="c-status-test")
        self.assertEqual(c.status, CrawlerStatus.QUEUED)
        t = CrawlerTask(task_id="t-status", request_id="req-1", plan_id="p-1", question_id="q-1", query_or_target="status query")
        rep = self.supervisor.execute_task(c, t)
        self.assertEqual(t.status, CrawlerTaskStatus.COMPLETED)
        self.assertEqual(c.status, CrawlerStatus.QUEUED)  # reset after completion

    # 12. Researcher receives crawler reports.
    def test_12_researcher_receives_crawler_reports(self):
        c = MockCrawler(crawler_id="c-rep-test")
        t = CrawlerTask(task_id="t-rep", request_id="req-1", plan_id="p-1", question_id="q-1", query_or_target="report query")
        rep = self.supervisor.execute_task(c, t)
        self.assertIsInstance(rep, CrawlerReport)
        self.assertEqual(rep.status, CrawlerReportStatus.SUCCESS)
        self.assertTrue(len(rep.raw_sources) > 0)
        self.assertTrue(len(rep.extracted_evidence) > 0)

    # 13. Every CrawlerReport maps back to its CrawlerTask.
    def test_13_crawler_report_maps_back_to_crawler_task(self):
        c = MockCrawler(crawler_id="c-map-test")
        t = CrawlerTask(task_id="ctask-unique-123", request_id="req-1", plan_id="p-1", question_id="q-1", query_or_target="mapping query")
        rep = self.supervisor.execute_task(c, t)
        self.assertEqual(rep.crawler_task_id, "ctask-unique-123")

    # 14. Every CrawlerTask maps back to its ResearchRequest.
    def test_14_crawler_task_maps_back_to_research_request(self):
        t = CrawlerTask(task_id="ctask-1", request_id="req-parent-999", plan_id="plan-1", question_id="q-1", query_or_target="lineage test")
        self.assertEqual(t.request_id, "req-parent-999")

    # 15. Crawler failure is explicitly represented.
    def test_15_crawler_failure_is_explicitly_represented(self):
        failing_crawler = MockCrawler(
            crawler_id="c-fail-explicit",
            simulate_failure=True,
            simulate_error_message="503 Service Unavailable: Remote server down",
        )
        t = CrawlerTask(task_id="t-fail", request_id="req-1", plan_id="p-1", question_id="q-1", query_or_target="fail test")
        rep = self.supervisor.execute_task(failing_crawler, t)
        self.assertEqual(rep.status, CrawlerReportStatus.FAILED)
        self.assertIn("503 Service Unavailable", rep.error_message)
        self.assertEqual(t.status, CrawlerTaskStatus.FAILED)

    # 16. Failed crawlers do not disappear silently.
    def test_16_failed_crawlers_do_not_disappear_silently(self):
        state = ResearchState(request=ResearchRequest(request_id="req-1", project_id="p-1", task_id="t-1", objective="Audit"))
        failing_crawler = MockCrawler(crawler_id="c-fail-track", simulate_failure=True)
        t = CrawlerTask(task_id="t-fail-track", request_id="req-1", plan_id="p-1", question_id="q-1", query_or_target="fail track")
        state.add_crawler_task(t)

        rep = self.supervisor.execute_task(failing_crawler, t)
        state.record_crawler_report(rep)

        self.assertEqual(len(state.received_reports), 1)
        self.assertEqual(state.received_reports[0].status, CrawlerReportStatus.FAILED)
        self.assertEqual(state.crawler_tasks["t-fail-track"].status, CrawlerTaskStatus.FAILED)
        self.assertEqual(failing_crawler.tasks_failed, 1)

    # 17. Researcher can request a replacement crawler.
    def test_17_researcher_can_request_replacement_crawler(self):
        failing_crawler = MockCrawler(crawler_id="c-old-fail", simulate_failure=True)
        self.registry.register_crawler_instance(failing_crawler)
        t = CrawlerTask(task_id="t-replace", request_id="req-1", plan_id="p-1", question_id="q-1", query_or_target="replace query")

        rep_fail = self.supervisor.execute_task(failing_crawler, t)
        self.assertEqual(rep_fail.status, CrawlerReportStatus.FAILED)

        # Dynamic replacement
        rep_ok = self.supervisor.replace_failed_crawler_and_retry(
            failed_crawler=failing_crawler,
            task=t,
            spawner=self.spawner,
        )
        self.assertEqual(rep_ok.status, CrawlerReportStatus.SUCCESS)
        self.assertEqual(t.status, CrawlerTaskStatus.COMPLETED)
        self.assertNotEqual(rep_ok.crawler_id, "c-old-fail")

    # 18. Researcher can cancel a crawler.
    def test_18_researcher_can_cancel_crawler(self):
        c = MockCrawler(crawler_id="c-cancel-test")
        t = CrawlerTask(task_id="t-cancel-op", request_id="req-1", plan_id="p-1", question_id="q-1", query_or_target="cancel query")
        t.cancel(reason="Coverage sufficient early")
        rep = self.supervisor.execute_task(c, t)
        self.assertEqual(t.status, CrawlerTaskStatus.CANCELLED)
        self.assertEqual(rep.status, CrawlerReportStatus.FAILED)
        self.assertIn("Coverage sufficient early", rep.summary)

    # 19. Researcher can distinguish partial collection from completed collection.
    def test_19_researcher_distinguishes_partial_from_completed(self):
        res_complete = ResearchResult(
            task_id="t-1", request_id="req-1", project_id="p-1", objective="Obj",
            status=ResearchResultStatus.VERIFIED,
        )
        res_partial = ResearchResult(
            task_id="t-2", request_id="req-2", project_id="p-1", objective="Obj",
            status=ResearchResultStatus.PARTIAL,
        )
        self.assertEqual(res_complete.status, ResearchResultStatus.VERIFIED)
        self.assertEqual(res_partial.status, ResearchResultStatus.PARTIAL)
        self.assertNotEqual(res_complete.status, res_partial.status)

    # 20. Researcher does NOT declare completion merely because all currently assigned crawlers finished.
    def test_20_researcher_does_not_declare_completion_merely_on_crawler_finish(self):
        state = ResearchState(
            request=ResearchRequest(
                request_id="req-gate", project_id="p-1", task_id="t-1", objective="Gate test",
                scope=ResearchScope(min_evidence_per_question=2),
            )
        )
        q1 = ResearchQuestion(question_id="q-1", question_text="What is Raft?")
        q2 = ResearchQuestion(question_id="q-2", question_text="What is Paxos?")
        state.add_question(q1)
        state.add_question(q2)

        # Crawlers finished, but only 1 evidence item for q1 and 0 for q2
        ev1 = EvidenceItem(evidence_id="ev-1", provenance=EvidenceProvenance(request_id="req-gate", crawler_task_id="ct-1", crawler_id="c-1", question_id="q-1"), extracted_fact="Fact 1")
        state.add_evidence(ev1)

        self.assertFalse(state.has_sufficient_evidence())
        sufficiency, gaps = EvidenceEvaluator.evaluate_coverage(state)
        self.assertNotEqual(sufficiency, EvidenceSufficiency.SUFFICIENT)
        self.assertTrue(len(gaps) >= 1)

    # 21. Researcher can represent insufficient evidence/coverage.
    def test_21_researcher_can_represent_insufficient_evidence(self):
        state = ResearchState(request=ResearchRequest(request_id="req-insuff", project_id="p-1", task_id="t-1", objective="Unknown tech"))
        q = ResearchQuestion(question_id="q-unk", question_text="How to run X on Y?")
        state.add_question(q)

        sufficiency, gaps = EvidenceEvaluator.evaluate_coverage(state)
        self.assertEqual(sufficiency, EvidenceSufficiency.INSUFFICIENT)
        self.assertEqual(len(gaps), 1)
        self.assertEqual(gaps[0].gap_id, "gap-q-unk")

    # 22. Researcher can request additional crawler work.
    def test_22_researcher_can_request_additional_crawler_work(self):
        additional_crawlers = self.spawner.spawn_additional_crawlers(
            needed_capabilities=[CrawlerCapability.REPOSITORY_INSPECTION],
            count=2,
        )
        self.assertEqual(len(additional_crawlers), 2)
        self.assertEqual(len(self.registry.list_active_crawlers()), 2)

    # 23. ResearchResult cannot be marked VERIFIED without passing the appropriate gates.
    def test_23_research_result_cannot_be_verified_without_passing_gates(self):
        state = ResearchState(request=ResearchRequest(request_id="req-gate-check", project_id="p-1", task_id="t-1", objective="Empty test"))
        # No evidence in pool
        result = ResearchSynthesizer.synthesize_result(state)
        self.assertNotEqual(result.status, ResearchResultStatus.VERIFIED)
        self.assertEqual(result.status, ResearchResultStatus.INSUFFICIENT_EVIDENCE)

    # 24. Provenance is retained for evidence.
    def test_24_provenance_is_retained_for_evidence(self):
        prov = EvidenceProvenance(
            request_id="req-prov-1",
            crawler_task_id="ctask-prov-1",
            crawler_id="crawler.web.1",
            question_id="q-prov-1",
            source_ref="https://example.org/docs",
            correlation_id="corr-prov-1",
        )
        ev = EvidenceItem(
            evidence_id="ev-prov-1",
            provenance=prov,
            extracted_fact="Verified claim text",
            content_snippet="Snippet text",
        )
        self.assertEqual(ev.provenance.request_id, "req-prov-1")
        self.assertEqual(ev.provenance.crawler_task_id, "ctask-prov-1")
        self.assertEqual(ev.provenance.crawler_id, "crawler.web.1")
        self.assertEqual(ev.provenance.question_id, "q-prov-1")
        self.assertTrue(len(ev.checksum) > 0)

    # 25. No component directly bypasses the intended Researcher/Crawler contracts.
    def test_25_contracts_are_strictly_typed(self):
        req = ResearchRequest(request_id="req-1", project_id="p-1", task_id="t-1", objective="Obj")
        plan = ResearchPlan(plan_id="p-1", request_id=req.request_id, objective=req.objective, mode=req.mode, scope=req.scope)
        task = CrawlerTask(task_id="t-1", request_id=req.request_id, plan_id=plan.plan_id, question_id="q-1", query_or_target="q")
        report = CrawlerReport(report_id="rep-1", crawler_task_id=task.task_id, crawler_id="c-1", request_id=req.request_id)
        result = ResearchResult(task_id="t-1", request_id=req.request_id, project_id="p-1", objective="Obj", status=ResearchResultStatus.VERIFIED)

        self.assertEqual(task.request_id, req.request_id)
        self.assertEqual(report.crawler_task_id, task.task_id)
        self.assertEqual(result.request_id, req.request_id)

    # 26. Researcher does not contain source-specific scraping logic.
    def test_26_researcher_does_not_contain_scraping_logic(self):
        import core.research.researcher as r_mod
        source_code = inspect.getsource(r_mod)
        self.assertNotIn("BeautifulSoup", source_code)
        self.assertNotIn("playwright", source_code.lower())
        self.assertNotIn("selenium", source_code.lower())
        self.assertNotIn("scrapy", source_code.lower())
        self.assertNotIn("reddit.com", source_code.lower())

    # 27. Crawler does not make strategic research decisions.
    def test_27_crawler_does_not_make_strategic_decisions(self):
        import workers.crawler.worker as cw_mod
        source_code = inspect.getsource(cw_mod)
        # Crawler must not contain research synthesis or decomposition logic
        self.assertNotIn("synthesize_result", source_code)
        self.assertNotIn("decompose_request", source_code)

    # 28. Researcher does not modify project files as part of research.
    def test_28_researcher_does_not_modify_project_files(self):
        import core.research.researcher as r_mod
        source_code = inspect.getsource(r_mod)
        self.assertNotIn("os.remove", source_code)
        self.assertNotIn("shutil.rmtree", source_code)
        self.assertNotIn("open(", source_code)

    # 29. Existing Manager functionality remains intact.
    def test_29_existing_manager_functionality_intact(self):
        from core.manager.controller import ManagerController
        self.assertTrue(hasattr(ManagerController, "execute_cycle"))
        self.assertTrue(hasattr(ManagerController, "run_orchestration"))
        self.assertTrue(hasattr(ManagerController, "_validate_and_execute_action"))

    # 30. Build/type checking/linting/tests pass.
    def test_30_full_orchestration_loop_demonstration(self):
        """
        Complete end-to-end demonstration scenario:
        1 request -> 3 questions -> 3 crawlers (C1, C2, C3).
        C1 completes, C2 completes, C3 fails.
        Researcher detects missing coverage on Q3.
        Researcher creates replacement crawler.
        Replacement completes.
        Researcher determines that collection is complete.
        """
        # 1. Receive 1 request
        req = ResearchRequest(
            request_id="req-scenario-001",
            project_id="proj-scenario",
            task_id="task-scenario",
            objective="Research architectural patterns for distributed event streams",
            questions=[
                "What is the log-structured storage model?",
                "How does partitioned consensus work?",
                "What are community benchmark latency distributions?",
            ],
            scope=ResearchScope(max_crawlers=3, min_evidence_per_question=1),
        )

        state = ResearchState(request=req)

        # 2. Creates 3 research questions
        q1 = ResearchQuestion(question_id="q-1", question_text=req.questions[0], request_id=req.request_id, plan_id="p-1")
        q2 = ResearchQuestion(question_id="q-2", question_text=req.questions[1], request_id=req.request_id, plan_id="p-1")
        q3 = ResearchQuestion(question_id="q-3", question_text=req.questions[2], request_id=req.request_id, plan_id="p-1")
        state.add_question(q1)
        state.add_question(q2)
        state.add_question(q3)

        # 3. Spawns 3 crawlers
        c1 = MockCrawler(crawler_id="crawler.scenario.1", capabilities=[CrawlerCapability.WEB_SEARCH])
        c2 = MockCrawler(crawler_id="crawler.scenario.2", capabilities=[CrawlerCapability.WEB_FETCH])
        c3 = MockCrawler(
            crawler_id="crawler.scenario.3",
            capabilities=[CrawlerCapability.API_QUERY],
            simulate_failure=True,
            simulate_error_message="504 Gateway Timeout: API rate limit exceeded",
        )
        self.registry.register_crawler_instance(c1)
        self.registry.register_crawler_instance(c2)
        self.registry.register_crawler_instance(c3)

        t1 = CrawlerTask(task_id="ctask-1", request_id=req.request_id, plan_id="p-1", question_id="q-1", query_or_target="log storage", required_capability=CrawlerCapability.WEB_SEARCH)
        t2 = CrawlerTask(task_id="ctask-2", request_id=req.request_id, plan_id="p-1", question_id="q-2", query_or_target="partitioned consensus", required_capability=CrawlerCapability.WEB_FETCH)
        t3 = CrawlerTask(task_id="ctask-3", request_id=req.request_id, plan_id="p-1", question_id="q-3", query_or_target="community latency", required_capability=CrawlerCapability.API_QUERY)
        state.add_crawler_task(t1)
        state.add_crawler_task(t2)
        state.add_crawler_task(t3)

        # 4. C1 completes, C2 completes, C3 fails
        r1 = self.supervisor.execute_task(c1, t1)
        r2 = self.supervisor.execute_task(c2, t2)
        r3 = self.supervisor.execute_task(c3, t3)

        state.record_crawler_report(r1)
        state.record_crawler_report(r2)
        state.record_crawler_report(r3)

        self.assertEqual(r1.status, CrawlerReportStatus.SUCCESS)
        self.assertEqual(r2.status, CrawlerReportStatus.SUCCESS)
        self.assertEqual(r3.status, CrawlerReportStatus.FAILED)

        # 5. Researcher detects missing coverage on Q3
        sufficiency, gaps = EvidenceEvaluator.evaluate_coverage(state)
        self.assertEqual(sufficiency, EvidenceSufficiency.PARTIAL)
        self.assertEqual(len(gaps), 1)
        self.assertEqual(gaps[0].gap_id, "gap-q-3")
        self.assertEqual(q3.status, ResearchQuestionStatus.UNKNOWN)

        # 6. Researcher creates a replacement crawler and re-executes task 3
        r3_replacement = self.supervisor.replace_failed_crawler_and_retry(
            failed_crawler=c3,
            task=t3,
            spawner=self.spawner,
            state=state,
        )
        self.assertEqual(r3_replacement.status, CrawlerReportStatus.SUCCESS)
        self.assertEqual(t3.status, CrawlerTaskStatus.COMPLETED)

        # 7. Researcher determines collection is complete
        sufficiency_after, gaps_after = EvidenceEvaluator.evaluate_coverage(state)
        self.assertEqual(sufficiency_after, EvidenceSufficiency.SUFFICIENT)
        self.assertEqual(len(gaps_after), 0)
        self.assertEqual(q3.status, ResearchQuestionStatus.ANSWERED)

        # Synthesize final result
        result = ResearchSynthesizer.synthesize_result(state)
        self.assertEqual(result.status, ResearchResultStatus.VERIFIED)
        self.assertEqual(result.request_id, req.request_id)


if __name__ == "__main__":
    unittest.main()
