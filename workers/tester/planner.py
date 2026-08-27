from __future__ import annotations

import re
from typing import Any, Optional
import uuid

from core.models import Task
from workers.tester.model import (
    RequirementTrace,
    TestingPlan,
    TestingScope,
    TestingTaskSpec,
)
from workers.tester.types import RequirementVerificationStatus, TestCategory


class TestingPlanner:
    """
    Constructs deterministic testing plans and requirement traceability matrices
    from Task specifications, criteria, and metadata.
    """

    @classmethod
    def parse_task_spec(cls, task: Task) -> TestingTaskSpec:
        meta = task.metadata or {}
        objective = task.objective or task.title

        scope_dict = meta.get("scope", meta.get("testing_scope", {}))
        scope = TestingScope.from_dict(scope_dict) if scope_dict else TestingScope()

        # Parse requirements
        raw_reqs = meta.get("requirements", meta.get("acceptance_criteria", []))
        requirements: list[str] = []
        if isinstance(raw_reqs, list):
            for r in raw_reqs:
                if isinstance(r, str) and r.strip():
                    requirements.append(r.strip())
                elif isinstance(r, dict) and "description" in r:
                    requirements.append(str(r["description"]).strip())

        # Also extract requirements from task success criteria
        if task.success_criteria:
            for crit in task.success_criteria:
                desc = crit.get("description") if isinstance(crit, dict) else str(crit)
                if desc and desc not in requirements:
                    requirements.append(desc.strip())

        # If no explicit requirements, derive from objective
        if not requirements:
            requirements = cls._derive_requirements(objective)

        affected_components = list(meta.get("affected_components", meta.get("components", [])))
        impl_refs = list(meta.get("implementation_references", meta.get("changed_files", [])))
        relevant_artifacts = list(meta.get("relevant_artifacts", task.artifacts or []))
        known_risks = list(meta.get("known_risks", meta.get("risks", [])))

        return TestingTaskSpec(
            objective=objective,
            requirements=requirements,
            success_criteria=list(task.success_criteria or []),
            affected_components=affected_components,
            implementation_references=impl_refs,
            relevant_artifacts=relevant_artifacts,
            known_risks=known_risks,
            scope=scope,
            raw_task_metadata=meta,
        )

    @classmethod
    def _derive_requirements(cls, objective: str) -> list[str]:
        """Deterministically derive testable requirements from a task objective."""
        clean = objective.strip()
        reqs: list[str] = [f"Implementation satisfies the core objective: '{clean}'"]
        
        lower = clean.lower()
        if any(w in lower for w in ("login", "auth", "security", "token", "password", "permission")):
            reqs.append("Authentication and authorization boundaries enforce valid credentials and reject unauthorized access.")
        if any(w in lower for w in ("save", "persist", "database", "store", "sqlite", "load")):
            reqs.append("State modifications persist accurately across process restarts and queries.")
        if any(w in lower for w in ("ui", "screen", "button", "view", "render", "page")):
            reqs.append("User interface elements render properly and visual layout is intact.")
        if any(w in lower for w in ("api", "endpoint", "http", "request")):
            reqs.append("API endpoints correctly validate input parameters and return appropriate response codes.")

        reqs.append("Implementation does not introduce regressions to existing functionality.")
        return reqs

    @classmethod
    def create_plan(cls, spec: TestingTaskSpec) -> TestingPlan:
        plan_id = f"tplan-{uuid.uuid4().hex[:8]}"

        # Identify applicable test categories
        categories: list[TestCategory] = [TestCategory.UNIT, TestCategory.REGRESSION]
        
        all_text = (spec.objective + " " + " ".join(spec.requirements) + " " + " ".join(spec.affected_components)).lower()
        if any(w in all_text for w in ("integration", "workflow", "manager", "runtime", "multi-worker", "e2e")):
            categories.append(TestCategory.INTEGRATION)
        if any(w in all_text for w in ("ui", "screen", "view", "visual", "layout", "flutter")):
            categories.append(TestCategory.UI)
        if any(w in all_text for w in ("auth", "security", "secret", "permission", "isolation")):
            categories.append(TestCategory.SECURITY)
            categories.append(TestCategory.NEGATIVE)
        if any(w in all_text for w in ("persist", "database", "sqlite", "restart", "store")):
            categories.append(TestCategory.PERSISTENCE)

        # Construct requirement trace list
        traces: list[RequirementTrace] = []
        for i, req in enumerate(spec.requirements):
            traces.append(
                RequirementTrace(
                    requirement_id=f"req-{i+1}",
                    description=req,
                    status=RequirementVerificationStatus.NOT_TESTED,
                )
            )

        phases = [
            "1. Inspect Codebase & Programmer Implementation Artifacts",
            "2. Establish Pre-Execution Baseline & Test Runner Discovery",
            "3. Execute Targeted Unit & Regression Test Suites",
        ]
        if TestCategory.INTEGRATION in categories:
            phases.append("4. Execute Integration & Workflow Verification Passes")
        if TestCategory.UI in categories:
            phases.append("5. Perform Visual & OCR UI Inspection")
        if TestCategory.SECURITY in categories or TestCategory.NEGATIVE in categories:
            phases.append("6. Execute Security & Negative Input Boundary Checks")
        if TestCategory.PERSISTENCE in categories:
            phases.append("7. Validate Persistence & State Recovery Checks")

        phases.append("8. Failure Investigation & Defect Classification")
        phases.append("9. Final Requirement Traceability Evaluation & Report Generation")

        strategy_summary = (
            f"QA evaluation covering {len(spec.requirements)} requirements across "
            f"{len(categories)} test categories ({', '.join(c.value for c in categories)})."
        )

        return TestingPlan(
            plan_id=plan_id,
            objective=spec.objective,
            strategy_summary=strategy_summary,
            planned_phases=phases,
            test_categories=categories,
            requirement_traces=traces,
        )
