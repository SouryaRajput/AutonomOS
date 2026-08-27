from __future__ import annotations

import logging
import re
import time
from typing import Any, Optional
import uuid

from core.enums import ArtifactType
from core.events.types import EventSource, EventType
from core.inference.model import ModelRequirement
from core.inference.types import ModelCapability
from core.models import Task, WorkerManifest, WorkerOutput, utc_now
from pkg.sdk.types import WorkerCapability
from pkg.sdk.worker import Worker, WorkerRuntimeContext
from workers.researcher.evaluator import SourceEvaluator
from workers.researcher.model import (
    ResearchContradiction,
    ResearchFinding,
    ResearchKnowledgeGap,
    ResearchPlan,
    ResearchRecommendation,
    ResearchResult,
    ResearchTaskSpec,
    Source,
    compute_checksum,
)
from workers.researcher.planner import ResearchPlanner
from workers.researcher.prompt import (
    build_research_synthesis_prompt,
    parse_research_synthesis,
)
from workers.researcher.report import ResearchReportGenerator
from workers.researcher.types import (
    FactClassification,
    ResearchConfidence,
    ResearchMode,
    ResearchQuestionStatus,
    SourceType,
)

logger = logging.getLogger("AutonomOS.Researcher")


class ResearcherWorker(Worker):
    """
    AutonomOS Specialist Researcher Worker.
    Autonomous specialist responsible for gathering, analyzing, cross-checking,
    and synthesizing evidence-grounded research without hallucinations.
    """

    def __init__(
        self,
        worker_id: str = "worker.researcher",
        name: str = "AutonomOS Specialist Researcher",
        description: str = "Autonomous specialist gathering, cross-checking, and synthesizing evidence-backed research",
        version: str = "1.0.0",
        default_mode: ResearchMode = ResearchMode.STANDARD,
    ):
        self._worker_id = worker_id
        self._name = name
        self._description = description
        self._version = version
        self.default_mode = default_mode

        self._manifest = WorkerManifest(
            id=self._worker_id,
            name=self._name,
            role="Researcher",
            description=self._description,
            version=self._version,
            capabilities=[
                WorkerCapability.RESEARCH.value,
                WorkerCapability.WEB_ACCESS.value,
                WorkerCapability.STRUCTURED_OUTPUT.value,
                WorkerCapability.DOCUMENTATION.value,
                "INFORMATION_SYNTHESIS",
                "SOURCE_ANALYSIS",
                "EVIDENCE_EXTRACTION",
            ],
            permissions=[
                "web",
                "web.search",
                "web.fetch",
                "filesystem.read_file",
                "filesystem.list_directory",
                "*",
            ],
            created_at=utc_now(),
        )

    def get_manifest(self) -> WorkerManifest:
        """Return the static manifest and declared capabilities for this worker."""
        return self._manifest

    def execute_task(self, context: WorkerRuntimeContext, task: Task) -> WorkerOutput:
        """
        Execute the assigned research task through the structured, multi-phase research loop.
        """
        start_time = time.perf_counter()
        context.log.info(f"Researcher started task '{task.title}' (ID: {task.id})")
        context.progress.report(5.0, "Parsing research task specification and decomposing questions")

        # 1. Parse Task Specification & Build Plan
        spec = ResearchPlanner.parse_task_spec(task)
        plan = ResearchPlanner.create_plan(spec)

        # Emit RESEARCH_STARTED and RESEARCH_PLAN_CREATED events
        context.events.emit(
            EventType.RESEARCH_STARTED.value,
            {
                "objective": spec.objective,
                "mode": spec.mode.value,
                "question_count": len(spec.questions),
                "scope": spec.scope.to_dict(),
            },
        )
        context.events.emit(
            EventType.RESEARCH_PLAN_CREATED.value,
            {
                "plan_id": plan.plan_id,
                "question_count": len(plan.questions),
                "steps": plan.planned_steps,
            },
        )

        # 2. Inspect Project Context & Existing Memory
        context.progress.report(15.0, "Gathering project context and inspecting persistent memory")
        ctx_pkg = context.context.get(focus_areas=["architecture", "research", "dependencies"])
        
        existing_memories: list[str] = []
        for mem_path in ("architecture.md", "current-state.md", "project-map.md"):
            mem_doc = context.memory.read(mem_path)
            if mem_doc:
                existing_memories.append(f"Document `{mem_path}`:\n{mem_doc.content}")

        # 3. Information Gathering via Web Tool Runtime
        context.progress.report(30.0, "Gathering sources via Web Search")
        gathered_raw_results: list[dict[str, Any]] = []
        executed_queries: set[str] = set()
        search_count = 0

        for q in spec.questions:
            if search_count >= spec.scope.max_searches:
                break

            queries = ResearchPlanner.generate_search_queries(q, spec, existing_queries=executed_queries)
            for query in queries:
                if search_count >= spec.scope.max_searches:
                    break

                context.log.info(f"Executing web search for query: '{query}'")
                search_res = context.tools.execute(
                    tool_id="web.search",
                    arguments={"query": query, "limit": 5, "action": "search"},
                )
                search_count += 1

                # Parse search output
                results_list: list[dict[str, Any]] = []
                if isinstance(search_res.output, list):
                    results_list = search_res.output
                elif isinstance(search_res.output, dict) and "results" in search_res.output:
                    results_list = search_res.output["results"]

                context.events.emit(
                    EventType.RESEARCH_SEARCH_PERFORMED.value,
                    {
                        "query": query,
                        "result_count": len(results_list),
                        "domain_filter": ", ".join(spec.scope.allowed_domains) if spec.scope.allowed_domains else "None",
                    },
                )

                for r in results_list:
                    raw_url = str(r.get("url", ""))
                    if SourceEvaluator.is_domain_allowed(raw_url, spec.scope.allowed_domains, spec.scope.excluded_domains):
                        gathered_raw_results.append(r)

        # 4. Deduplicate & Fetch Candidate Sources
        context.progress.report(50.0, "Deduplicating and fetching candidate sources")
        deduped_candidates = SourceEvaluator.deduplicate_sources(gathered_raw_results)
        
        sources: list[Source] = []
        fetch_count = 0

        for i, cand in enumerate(deduped_candidates[:spec.scope.max_fetches]):
            raw_url = str(cand.get("url", "")).strip()
            title = str(cand.get("title", f"Source {i+1}")).strip()
            stype = SourceEvaluator.classify_source_type(raw_url, title)
            rel_score = SourceEvaluator.assess_source_reliability(stype, url=raw_url)

            content_text = ""
            if raw_url:
                try:
                    context.log.info(f"Fetching source content from: {raw_url}")
                    fetch_res = context.tools.execute(
                        tool_id="web.fetch",
                        arguments={"url": raw_url, "action": "fetch"},
                    )
                    fetch_count += 1
                    content_text = str(fetch_res.output or "")
                except Exception as fetch_err:
                    context.log.warning(f"Failed to fetch {raw_url}: {fetch_err}")
                    content_text = str(cand.get("snippet", ""))

            if not content_text:
                content_text = str(cand.get("snippet", ""))

            src_id = f"src-{i+1}"
            source_obj = Source(
                source_id=src_id,
                title=title,
                url_or_ref=raw_url,
                publisher=str(cand.get("publisher", "")),
                source_type=stype,
                reliability_score=rel_score,
                content_snippet=content_text[:4000],
                content_checksum=compute_checksum(content_text),
                metadata={"raw_candidate": cand},
            )
            sources.append(source_obj)

            context.events.emit(
                EventType.RESEARCH_SOURCE_FETCHED.value,
                {
                    "source_id": src_id,
                    "title": title,
                    "url": raw_url,
                    "source_type": stype.value,
                    "bytes_fetched": len(content_text),
                },
            )

        # 5. Prompt-Isolated LLM Synthesis via Inference Gateway
        context.progress.report(70.0, "Synthesizing evidence and cross-checking findings via Inference Gateway")
        messages = build_research_synthesis_prompt(
            task_spec=spec,
            sources=sources,
            context_package=ctx_pkg,
            existing_memory=existing_memories,
        )

        inf_resp = context.inference.generate(
            messages=messages,
            requirements=ModelRequirement(
                required_capabilities={ModelCapability.REASONING, ModelCapability.STRUCTURED_OUTPUT},
                minimum_context=8000,
            ),
            temperature=0.2,
        )

        findings, contradictions, knowledge_gaps, recommendations, q_statuses, raw_summary = parse_research_synthesis(
            raw_content=inf_resp.content,
            task_spec=spec,
            sources=sources,
        )

        # Update Question statuses based on synthesis
        for q in spec.questions:
            if q.question_id in q_statuses:
                try:
                    q.status = ResearchQuestionStatus(q_statuses[q.question_id])
                except ValueError:
                    q.status = ResearchQuestionStatus.ANSWERED
            else:
                # Default to answered if findings exist, or unknown
                q.status = ResearchQuestionStatus.ANSWERED if findings else ResearchQuestionStatus.UNKNOWN

            # Associate findings with questions
            q.answered_findings = [f.finding_id for f in findings]

        # 6. Evaluation, Contradiction Detection & Quality Checks
        context.progress.report(80.0, "Evaluating contradictions, knowledge gaps, and recording citations")
        
        # If evaluator finds additional contradictions or gaps
        extra_contradictions = SourceEvaluator.detect_contradictions(findings)
        if extra_contradictions and not contradictions:
            contradictions = extra_contradictions

        extra_gaps = SourceEvaluator.identify_knowledge_gaps(spec.questions, findings)
        if extra_gaps and not knowledge_gaps:
            knowledge_gaps = extra_gaps

        # Emit events for contradictions and knowledge gaps
        for c in contradictions:
            context.events.emit(
                EventType.RESEARCH_CONTRADICTION_DETECTED.value,
                {
                    "topic": c.topic,
                    "claim_a": c.claim_a,
                    "claim_b": c.claim_b,
                    "sources_a": c.sources_a,
                    "sources_b": c.sources_b,
                },
            )

        for g in knowledge_gaps:
            context.events.emit(
                EventType.RESEARCH_KNOWLEDGE_GAP.value,
                {
                    "topic": g.topic,
                    "question": g.question,
                    "reason": g.reason,
                    "impact": g.impact,
                },
            )

        for f in findings:
            context.events.emit(
                EventType.RESEARCH_FINDING_CREATED.value,
                {
                    "finding_id": f.finding_id,
                    "claim": f.claim,
                    "classification": f.classification.value,
                    "confidence": f.confidence.value,
                    "source_ids": f.source_ids,
                },
            )

        # 7. Record Authoritative Evidence
        evidence_ids: list[str] = []
        for s in sources:
            ev = context.record_evidence(
                evidence_type="RESEARCH_SOURCE_CITATION",
                data=f"Source [{s.source_id}] '{s.title}' ({s.url_or_ref}) — Checksum: {s.content_checksum[:16]}",
            )
            if ev:
                evidence_ids.append(ev.id)

        for f in findings:
            ev = context.record_evidence(
                evidence_type="RESEARCH_FINDING_VERIFICATION",
                data=f"Finding [{f.finding_id}] ({f.classification.value}): '{f.claim}' supported by sources: {', '.join(f.source_ids)}",
            )
            if ev:
                evidence_ids.append(ev.id)
                f.evidence_ids.append(ev.id)

        # 8. Create Research Result Package & Markdown Report Artifact
        context.progress.report(85.0, "Generating Markdown research report and creating artifact")
        
        slug = re.sub(r"[^\w\-]", "_", spec.objective[:40].lower()).strip("_") or "investigation"
        rel_report_path = f"research/{slug}_{task.id[:8]}.md"

        result = ResearchResult(
            task_id=task.id,
            project_id=task.project_id,
            objective=spec.objective,
            mode=spec.mode,
            plan=plan,
            questions=spec.questions,
            sources=sources,
            findings=findings,
            contradictions=contradictions,
            knowledge_gaps=knowledge_gaps,
            recommendations=recommendations,
            evidence_ids=evidence_ids,
            summary_for_manager=raw_summary,
            total_searches=search_count,
            total_fetches=fetch_count,
            cost_estimate=getattr(inf_resp.cost, "total_cost", 0.0) if inf_resp.cost else 0.0,
            report_path=rel_report_path,
        )

        manager_summary = ResearchReportGenerator.generate_manager_summary(result)
        result.summary_for_manager = manager_summary

        report_markdown = ResearchReportGenerator.generate_markdown_report(result)
        report_artifact = context.artifacts.create(
            artifact_type=ArtifactType.REPORT,
            relative_path=rel_report_path,
            description=f"Research report for '{spec.objective}'",
            content=report_markdown,
            metadata={
                "task_id": task.id,
                "sources_count": len(sources),
                "findings_count": len(findings),
                "contradictions_count": len(contradictions),
                "knowledge_gaps_count": len(knowledge_gaps),
            },
        )
        result.report_artifact_id = report_artifact.id

        # 9. Update Persistent Project Memory if appropriate
        context.progress.report(90.0, "Updating persistent project memory and requesting verification")
        try:
            # If there's an accepted recommendation or major technical insight, persist to memory
            if recommendations or (findings and any(f.classification == FactClassification.FACT for f in findings)):
                context.memory.record_decision(
                    title=f"Research Summary: {spec.objective[:50]}",
                    context=f"Research investigation into: {spec.objective}",
                    decision=recommendations[0].action if recommendations else findings[0].claim,
                    reasoning=recommendations[0].rationale if recommendations else findings[0].reasoning,
                    consequences=f"Report recorded in artifact `{report_artifact.path}` with {len(sources)} sources.",
                    references=[s.url_or_ref for s in sources[:5] if s.url_or_ref],
                )
        except Exception as mem_err:
            context.log.warning(f"Could not persist research memory decision: {mem_err}")

        # 10. Request Authoritative Verification
        try:
            context.verification.request()
        except Exception as verif_err:
            context.log.warning(f"Verification request completed with notice: {verif_err}")

        # 11. Emit RESEARCH_COMPLETED and return WorkerOutput
        context.events.emit(
            EventType.RESEARCH_COMPLETED.value,
            {
                "findings_count": len(findings),
                "sources_count": len(sources),
                "contradictions_count": len(contradictions),
                "knowledge_gaps_count": len(knowledge_gaps),
                "report_artifact_id": report_artifact.id,
                "summary": manager_summary,
            },
        )

        context.progress.report(100.0, "Research completed successfully")
        context.log.info(f"Researcher completed task '{task.title}' in {time.perf_counter() - start_time:.2f}s")

        return WorkerOutput(
            success=True,
            summary=manager_summary,
            created_artifacts=[report_artifact.to_dict()],
            metadata={
                "research_result": result.to_dict(),
                "report_artifact_id": report_artifact.id,
                "report_path": rel_report_path,
                "findings_count": len(findings),
                "sources_count": len(sources),
                "contradictions_count": len(contradictions),
                "knowledge_gaps_count": len(knowledge_gaps),
            },
        )
