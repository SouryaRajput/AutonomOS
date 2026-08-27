from __future__ import annotations

from typing import Any, Optional
import uuid

from core.models import Task
from workers.programmer.model import (
    ProgrammingPlan,
    ProgrammingScope,
    ProgrammingTaskSpec,
)
from workers.programmer.types import ProgrammingMode


class ProgrammingPlanner:
    """
    Decomposes programming tasks into structured specifications and bounded,
    mode-appropriate execution plans.
    """

    @classmethod
    def parse_task_spec(cls, task: Task) -> ProgrammingTaskSpec:
        """Parse and normalize a task into a structured ProgrammingTaskSpec."""
        meta = dict(task.metadata or {})
        objective = (task.objective or task.title or "").strip()

        # Inferred or explicit mode
        mode_str = str(meta.get("mode", meta.get("programming_mode", ""))).upper()
        if mode_str:
            try:
                mode = ProgrammingMode(mode_str)
            except ValueError:
                mode = ProgrammingMode.FEATURE
        else:
            low_obj = objective.lower()
            if any(w in low_obj for w in ("fix", "bug", "issue", "patch error", "regression", "crash")):
                mode = ProgrammingMode.BUGFIX
            elif any(w in low_obj for w in ("refactor", "cleanup", "reorganize", "restructure", "simplify")):
                mode = ProgrammingMode.REFACTOR
            elif any(w in low_obj for w in ("patch", "quick fix", "tweak", "typo", "bump")):
                mode = ProgrammingMode.PATCH
            else:
                mode = ProgrammingMode.FEATURE

        # Scope
        allowed = list(meta.get("allowed_paths", meta.get("scope", [])))
        excluded = list(meta.get("excluded_paths", [".git", ".autonomos/backups"]))
        max_files = int(meta.get("max_files_modified", 10 if mode == ProgrammingMode.PATCH else 25))

        scope = ProgrammingScope(
            allowed_paths=allowed,
            excluded_paths=excluded,
            max_files_modified=max_files,
        )

        requirements = list(meta.get("requirements", []))
        if not requirements:
            if task.success_criteria:
                for crit in task.success_criteria:
                    desc = crit.get("description") if isinstance(crit, dict) else str(crit)
                    if desc:
                        requirements.append(desc)
            else:
                requirements.append(objective)

        constraints = list(meta.get("constraints", []))
        research = list(meta.get("relevant_research", meta.get("research_artifacts", [])))
        test_exp = list(meta.get("test_expectations", meta.get("tests", [])))

        return ProgrammingTaskSpec(
            objective=objective,
            mode=mode,
            scope=scope,
            requirements=requirements,
            constraints=constraints,
            relevant_research=research,
            success_criteria=task.success_criteria or [],
            test_expectations=test_exp,
            raw_task_metadata=meta,
        )

    @classmethod
    def create_plan(cls, spec: ProgrammingTaskSpec, repo_info: Optional[dict[str, Any]] = None) -> ProgrammingPlan:
        """Construct a scoped step-by-step programming plan."""
        plan_id = f"pplan-{uuid.uuid4().hex[:8]}"

        if spec.mode == ProgrammingMode.BUGFIX:
            steps = [
                "1. Inspect failing scenario and reproduce issue in target source",
                "2. Identify root cause and check related tests",
                "3. Request pre-modification safety checkpoint",
                "4. Implement targeted bug fix",
                "5. Add regression test covering the failure scenario",
                "6. Run tests to verify fix and ensure zero regressions",
                "7. Inspect diff, perform self-review, and produce implementation report",
            ]
            complexity = "MEDIUM"

        elif spec.mode == ProgrammingMode.REFACTOR:
            steps = [
                "1. Establish baseline test status before modifications",
                "2. Inspect architecture boundaries, modules, and consumers",
                "3. Request pre-modification safety checkpoint",
                "4. Apply structural refactoring without changing external behavior",
                "5. Run full test suite for regression verification",
                "6. Inspect diff, perform self-review, and produce implementation report",
            ]
            complexity = "HIGH"

        elif spec.mode == ProgrammingMode.PATCH:
            steps = [
                "1. Inspect target file and affected lines",
                "2. Request pre-modification safety checkpoint",
                "3. Apply surgical code patch",
                "4. Run targeted check/test",
                "5. Inspect diff, perform self-review, and produce implementation report",
            ]
            complexity = "LOW"

        else:  # FEATURE
            steps = [
                "1. Inspect existing codebase, architecture, and interfaces",
                "2. Review relevant research findings and constraints",
                "3. Request pre-modification safety checkpoint",
                "4. Implement required feature code across authorized scope",
                "5. Add unit and integration tests covering new functionality",
                "6. Inspect diff and run project test suite",
                "7. Perform self-review and produce implementation report",
            ]
            complexity = "MEDIUM"

        return ProgrammingPlan(
            plan_id=plan_id,
            objective=spec.objective,
            mode=spec.mode,
            planned_steps=steps,
            affected_files=list(spec.scope.allowed_paths),
            estimated_complexity=complexity,
            current_step_index=0,
        )
