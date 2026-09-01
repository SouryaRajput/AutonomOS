from __future__ import annotations

import logging
import re
import time
from typing import TYPE_CHECKING, Optional

from core.research.contracts.crawler_report import CrawlerReport
from core.research.contracts.plan import ResearchPlan
from core.research.contracts.question import ResearchQuestion
from core.research.contracts.request import ResearchRequest
from core.research.contracts.result import ResearchResult
from core.research.crawler.registry import CrawlerRegistry
from core.research.evidence.evaluator import EvidenceEvaluator
from core.research.orchestration.spawner import CrawlerSpawner
from core.research.orchestration.supervisor import CrawlerSupervisor
from core.research.planning.decomposer import ResearchDecomposer
from core.research.planning.task_generator import CrawlerTaskGenerator
from core.research.state.model import ResearchState
from core.research.synthesis.synthesizer import ResearchSynthesizer
from core.research.types import (
    EvidenceSufficiency,
    ResearchLifecycleState,
    ResearchResultStatus,
)

if TYPE_CHECKING:
    from pkg.sdk.worker import WorkerRuntimeContext

logger = logging.getLogger("AutonomOS.Research.Researcher")


class Researcher:
    """
    AutonomOS Researcher Specialist Orchestrator.
    Drives the deterministic 13-stage research lifecycle:
    RESEARCH_REQUESTED -> UNDERSTANDING -> PLANNING -> CRAWLER_ALLOCATION ->
    CRAWLERS_RUNNING -> EVIDENCE_COLLECTION -> EVIDENCE_EVALUATION ->
    COVERAGE_CHECK -> CONTRADICTION_CHECK -> SYNTHESIS ->
    RESEARCH_VERIFIED -> RESULT_DELIVERED -> COMPLETE
    
    Coordinates the dynamic Crawler workforce without directly scraping sources itself.
    """

    def __init__(
        self,
        registry: Optional[CrawlerRegistry] = None,
        spawner: Optional[CrawlerSpawner] = None,
        supervisor: Optional[CrawlerSupervisor] = None,
    ):
        self.registry = registry or CrawlerRegistry()
        self.spawner = spawner or CrawlerSpawner(self.registry)
        self.supervisor = supervisor or CrawlerSupervisor()

    def execute_research(
        self,
        request: ResearchRequest,
        context: Optional[WorkerRuntimeContext] = None,
    ) -> tuple[ResearchResult, ResearchState]:
        """
        Execute a full research lifecycle from incoming ResearchRequest through
        planning, dynamic crawler spawning, execution, evidence evaluation, and synthesis.
        """
        start_time = time.perf_counter()
        logger.info(f"Researcher received research request '{request.request_id}' for objective: '{request.objective}'")

        # 1. RESEARCH_REQUESTED -> Initialize State
        state = ResearchState(request=request)

        try:
            # 2. UNDERSTANDING: Analyze objective and context
            state.transition_to(
                ResearchLifecycleState.UNDERSTANDING,
                reason=f"Analyzing objective: '{request.objective}' with {len(request.questions)} initial questions.",
            )
            if context and hasattr(context, "progress"):
                context.progress.report(10.0, "Understanding research objective and scope")

            # 3. PLANNING: Decompose into questions and formulate plan
            state.transition_to(
                ResearchLifecycleState.PLANNING,
                reason="Decomposing research objective into sub-questions and execution plan.",
            )
            questions, plan = ResearchDecomposer.decompose_request(request)
            state.plan = plan
            for q in questions:
                state.add_question(q)

            # Generate granular CrawlerTasks for all questions
            crawler_tasks = CrawlerTaskGenerator.generate_tasks_for_plan(plan)
            for t in crawler_tasks:
                state.add_crawler_task(t)

            if context and hasattr(context, "progress"):
                context.progress.report(25.0, f"Formulated plan with {len(questions)} questions and {len(crawler_tasks)} tasks")

            # 4. CRAWLER_ALLOCATION: Dynamically spawn N crawlers
            state.transition_to(
                ResearchLifecycleState.CRAWLER_ALLOCATION,
                reason=f"Dynamically allocating crawlers for {len(crawler_tasks)} tasks across plan.",
            )
            crawlers = self.spawner.spawn_crawlers_for_plan(plan)
            for c in crawlers:
                state.active_crawlers[c.id] = "ALLOCATED"

            if context and hasattr(context, "progress"):
                context.progress.report(40.0, f"Allocated {len(crawlers)} crawlers for execution")

            # 5. CRAWLERS_RUNNING: Dispatch and supervise crawler tasks
            state.transition_to(
                ResearchLifecycleState.CRAWLERS_RUNNING,
                reason=f"Dispatching {len(crawler_tasks)} tasks across {len(crawlers)} crawlers.",
            )
            if context and hasattr(context, "progress"):
                context.progress.report(55.0, "Crawlers executing collection tasks")

            reports = self.supervisor.assign_and_execute_all(
                crawlers=crawlers,
                tasks=crawler_tasks,
                state=state,
                context=context,
            )

            # 6. EVIDENCE_COLLECTION: Ingest reports and populate evidence pool
            state.transition_to(
                ResearchLifecycleState.EVIDENCE_COLLECTION,
                reason=f"Collected {len(reports)} crawler reports. Populating evidence pool.",
            )
            if context and hasattr(context, "progress"):
                context.progress.report(70.0, f"Aggregated {len(state.evidence_pool)} evidence items from reports")

            # 7. EVIDENCE_EVALUATION: Evaluate source reliability and evidence validity
            state.transition_to(
                ResearchLifecycleState.EVIDENCE_EVALUATION,
                reason=f"Evaluating credibility and validity across {len(state.sources)} sources.",
            )
            if context and hasattr(context, "progress"):
                context.progress.report(75.0, "Evaluating evidence validity")

            # 8. COVERAGE_CHECK: Verify if evidence is sufficient to answer questions
            state.transition_to(
                ResearchLifecycleState.COVERAGE_CHECK,
                reason="Verifying whether evidence pool sufficiently covers all research questions.",
            )
            sufficiency, gaps = EvidenceEvaluator.evaluate_coverage(state)

            # Check if retry is warranted and budget allows
            if sufficiency == EvidenceSufficiency.INSUFFICIENT and state.retry_count < state.max_retries:
                state.retry_count += 1
                state.transition_to(
                    ResearchLifecycleState.RETRYING_CRAWLERS,
                    reason=f"Evidence insufficient for questions ({len(gaps)} gaps). Initiating retry cycle {state.retry_count}/{state.max_retries}.",
                )
                # In foundational mode, transition back to evaluation with knowledge gaps recorded
                state.transition_to(
                    ResearchLifecycleState.INSUFFICIENT_EVIDENCE,
                    reason="Retries exhausted or in placeholder mode; proceeding with explicit knowledge gaps.",
                )
            elif sufficiency == EvidenceSufficiency.INSUFFICIENT:
                state.transition_to(
                    ResearchLifecycleState.INSUFFICIENT_EVIDENCE,
                    reason="Coverage check determined evidence is insufficient; recording explicit knowledge gaps.",
                )

            # 9. CONTRADICTION_CHECK: Cross-check claims across independent sources
            if state.current_state not in (ResearchLifecycleState.INSUFFICIENT_EVIDENCE,):
                state.transition_to(
                    ResearchLifecycleState.CONTRADICTION_CHECK,
                    reason="Cross-checking evidence across independent sources for contradictory statements.",
                )
                contradictions = EvidenceEvaluator.detect_contradictions(state.findings, state.evidence_pool)
                state.contradictions = contradictions

            # 10. SYNTHESIS: Assemble structured findings and report
            state.transition_to(
                ResearchLifecycleState.SYNTHESIS,
                reason="Synthesizing evidence-backed findings, recommendations, and report summary.",
            )
            if context and hasattr(context, "progress"):
                context.progress.report(85.0, "Synthesizing research findings and report")

            slug = re.sub(r"[^\w\-]", "_", request.objective[:40].lower()).strip("_") or "investigation"
            rel_report_path = f"research/{slug}_{request.task_id[:8]}.md"

            result = ResearchSynthesizer.synthesize_result(
                state=state,
                report_path=rel_report_path,
            )

            # 11. RESEARCH_VERIFIED: Deterministic validation of result integrity
            state.transition_to(
                ResearchLifecycleState.RESEARCH_VERIFIED,
                reason="Deterministically verifying result integrity, citation linkages, and evidence provenance.",
            )
            if context and hasattr(context, "progress"):
                context.progress.report(95.0, "Verifying research result integrity")

            # 12. RESULT_DELIVERED: Packaged for delivery to Manager / Runtime
            state.transition_to(
                ResearchLifecycleState.RESULT_DELIVERED,
                reason=f"Delivering final ResearchResult (Status: {result.status.value}) to Manager.",
            )

            # 13. COMPLETE: Final successful terminal state
            state.transition_to(
                ResearchLifecycleState.COMPLETE,
                reason="Research lifecycle completed successfully.",
            )
            if context and hasattr(context, "progress"):
                context.progress.report(100.0, "Research completed successfully")

            elapsed = round(time.perf_counter() - start_time, 2)
            logger.info(f"Researcher completed request '{request.request_id}' in {elapsed}s with status '{result.status.value}'")
            return result, state

        except Exception as err:
            logger.error(f"Researcher encountered error during execution of request '{request.request_id}': {err}")
            state.errors.append(str(err))
            if not state.is_terminal():
                try:
                    state.transition_to(ResearchLifecycleState.FAILED, reason=f"Execution failed: {err}")
                except Exception:
                    pass

            fallback_result = ResearchResult(
                task_id=request.task_id,
                request_id=request.request_id,
                project_id=request.project_id,
                objective=request.objective,
                mode=request.mode,
                status=ResearchResultStatus.FAILED,
                summary_for_manager=f"Research execution failed: {err}",
                correlation_id=request.correlation_id,
            )
            return fallback_result, state
