from __future__ import annotations

from dataclasses import asdict, dataclass, field
import logging
import os
import re
import time
from typing import Any, Dict, List, Optional, Set
import uuid

from core.enums import DependencyType, RiskLevel, TaskStatus
from core.events.model import utc_now
from core.models import Task
from core.runtime.workforce_runtime import WorkforceRuntime
from core.workspace.project_map import ProjectMapEngine

logger = logging.getLogger("AutonomOS.ManagerPlanner")


@dataclass
class WorkerTaskContract:
    """
    Complete, self-contained task delegation contract for a specialized worker.
    Specifies exact objective, scoped files, constraints, acceptance criteria,
    required evidence, Manager audit rules, and the generated worker instruction prompt.
    """
    task_id: str
    title: str
    worker_type: str  # "Researcher" | "Programmer" | "Tester" | "Specialized"
    worker_id: str
    objective: str
    relevant_files: List[str]
    dependencies: List[str]
    constraints: List[str]
    acceptance_criteria: List[str]
    required_evidence: List[str]
    audit_criteria: List[str]
    worker_prompt: str
    status: str = "PLANNED (Workers Disabled)"

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> WorkerTaskContract:
        return cls(**data)


@dataclass
class DelegationPlan:
    """
    Structured Manager plan converting a user engineering objective into
    decomposed tasks and worker contracts.
    """
    plan_id: str
    project_id: str
    objective: str
    tasks: List[WorkerTaskContract]
    relevant_files: List[str]
    subsystems_involved: List[str]
    workers_disabled: bool = True
    status: str = "PAUSED (Workers Disabled)"
    created_at: str = field(default_factory=utc_now)
    summary: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "plan_id": self.plan_id,
            "project_id": self.project_id,
            "objective": self.objective,
            "tasks": [t.to_dict() for t in self.tasks],
            "relevant_files": self.relevant_files,
            "subsystems_involved": self.subsystems_involved,
            "workers_disabled": self.workers_disabled,
            "status": self.status,
            "created_at": self.created_at,
            "summary": self.summary,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> DelegationPlan:
        tasks = [WorkerTaskContract.from_dict(t) for t in data.get("tasks", [])]
        return cls(
            plan_id=data["plan_id"],
            project_id=data["project_id"],
            objective=data["objective"],
            tasks=tasks,
            relevant_files=data.get("relevant_files", []),
            subsystems_involved=data.get("subsystems_involved", []),
            workers_disabled=data.get("workers_disabled", True),
            status=data.get("status", "PAUSED (Workers Disabled)"),
            created_at=data.get("created_at", utc_now()),
            summary=data.get("summary", ""),
        )


class ManagerPlanner:
    """
    Manager Task Decomposition and Delegation Planning Engine.
    Converts high-level user objectives into concrete, structured worker tasks,
    defines dependency graphs, generates worker prompts, and persists tasks into
    the runtime while strictly enforcing that workers remain DISABLED.
    """

    WORKER_REGISTRY = {
        "Researcher": {
            "id": "worker.researcher.codebase",
            "role": "Codebase & Dependency Researcher",
            "capabilities": ["codebase_exploration", "ast_search", "dependency_analysis"],
        },
        "Programmer": {
            "id": "worker.programmer.general",
            "role": "Software Implementation Engineer",
            "capabilities": ["file_editing", "code_generation", "refactoring"],
        },
        "Tester": {
            "id": "worker.tester.verification",
            "role": "Quality Assurance & Test Engineer",
            "capabilities": ["test_execution", "regression_verification", "assertion_checking"],
        },
    }

    def __init__(self, runtime: WorkforceRuntime, map_engine: Optional[ProjectMapEngine] = None):
        self.runtime = runtime
        self.map_engine = map_engine or ProjectMapEngine(runtime.fs if hasattr(runtime, "fs") else None)

    def plan_and_delegate(
        self,
        project_id: str,
        objective: str,
        user_constraints: Optional[List[str]] = None,
        context_files: Optional[List[str]] = None,
    ) -> DelegationPlan:
        """
        Decomposes a user engineering objective into structured tasks, assigns worker contracts,
        records dependencies in TaskEngine, and generates worker prompts.
        Workers remain strictly DISABLED.
        """
        plan_id = f"plan-{uuid.uuid4().hex[:8]}"
        logger.info(f"Manager formulating task decomposition plan '{plan_id}' for project '{project_id}': {objective}")

        # 1. Retrieve Targeted Project Context from Project Map
        relevant_context = self.map_engine.query_relevant_context(objective)
        matched_files = [f["path"] for f in relevant_context.get("relevant_files", [])]
        if context_files:
            matched_files = sorted(list(set(matched_files + context_files)))

        subsystems = relevant_context.get("matched_subsystems", [])
        tech_stack = relevant_context.get("tech_stack", {})

        # 2. Decompose Objective into Discrete Specialized Tasks
        task_specs = self._determine_tasks(
            objective=objective,
            matched_files=matched_files,
            subsystems=subsystems,
            tech_stack=tech_stack,
            user_constraints=user_constraints or [],
        )

        contracts: List[WorkerTaskContract] = []
        created_task_ids: Dict[str, str] = {}  # local_key -> runtime_task_id

        # 3. Create Tasks in Runtime TaskEngine & Build Contracts
        for spec in task_specs:
            worker_type = spec["worker_type"]
            worker_info = self.WORKER_REGISTRY.get(worker_type, self.WORKER_REGISTRY["Programmer"])

            # Generate Self-Contained Worker Prompt
            worker_prompt = self._generate_worker_prompt(
                objective=spec["objective"],
                worker_type=worker_type,
                worker_role=worker_info["role"],
                scoped_files=spec["relevant_files"],
                constraints=spec["constraints"],
                acceptance_criteria=spec["acceptance_criteria"],
                required_evidence=spec["required_evidence"],
                tech_stack=tech_stack,
            )

            # Persist Task into Runtime TaskEngine
            task = self.runtime.tasks.create_task(
                project_id=project_id,
                title=spec["title"],
                objective=spec["objective"],
                priority=spec.get("priority", 1),
                risk=spec.get("risk", RiskLevel.LOW),
                success_criteria=[{"criterion": c} for c in spec["acceptance_criteria"]],
                metadata={
                    "worker_type": worker_type,
                    "worker_id": worker_info["id"],
                    "relevant_files": spec["relevant_files"],
                    "constraints": spec["constraints"],
                    "required_evidence": spec["required_evidence"],
                    "audit_criteria": spec["audit_criteria"],
                    "worker_prompt": worker_prompt,
                    "execution_mode": "DISABLED_DRY_RUN",
                },
            )

            created_task_ids[spec["key"]] = task.id

            contract = WorkerTaskContract(
                task_id=task.id,
                title=spec["title"],
                worker_type=worker_type,
                worker_id=worker_info["id"],
                objective=spec["objective"],
                relevant_files=spec["relevant_files"],
                dependencies=[],  # Will be mapped below
                constraints=spec["constraints"],
                acceptance_criteria=spec["acceptance_criteria"],
                required_evidence=spec["required_evidence"],
                audit_criteria=spec["audit_criteria"],
                worker_prompt=worker_prompt,
                status="PLANNED (Workers Disabled)",
            )
            contracts.append(contract)

        # 4. Map and Register Directed Dependencies
        for spec in task_specs:
            local_key = spec["key"]
            dep_keys = spec.get("depends_on", [])
            dependent_id = created_task_ids[local_key]

            for dep_key in dep_keys:
                if dep_key in created_task_ids:
                    prereq_id = created_task_ids[dep_key]
                    # Register dependency in Runtime TaskEngine
                    self.runtime.tasks.add_dependency(
                        dependent_task_id=dependent_id,
                        prerequisite_task_id=prereq_id,
                        dependency_type=DependencyType.STRICT_SUCCESS,
                    )
                    # Update contract dependency list
                    for c in contracts:
                        if c.task_id == dependent_id:
                            c.dependencies.append(prereq_id)

        # 5. Assemble Concise Summary
        summary = self._build_plan_summary(objective, contracts, subsystems)

        return DelegationPlan(
            plan_id=plan_id,
            project_id=project_id,
            objective=objective,
            tasks=contracts,
            relevant_files=matched_files,
            subsystems_involved=subsystems,
            workers_disabled=True,
            summary=summary,
        )

    def _determine_tasks(
        self,
        objective: str,
        matched_files: List[str],
        subsystems: List[str],
        tech_stack: Dict[str, Any],
        user_constraints: List[str],
    ) -> List[Dict[str, Any]]:
        """
        Heuristic & semantic task decomposition logic based on engineering requirements.
        """
        obj_lower = objective.lower()
        tasks = []

        # Check if research / discovery phase is required
        needs_research = any(w in obj_lower for w in ["investigate", "explore", "diagnose", "analyze", "research", "how does", "find"])
        has_tests = any("test" in f.lower() or "spec" in f.lower() for f in matched_files) or bool(matched_files)

        base_constraints = [
            "Operate strictly within workspace boundaries.",
            "Do not modify files outside the assigned scope.",
            "Preserve existing architecture and public interfaces.",
        ] + user_constraints

        # Phase 1: Research (if requested or broad diagnostic)
        if needs_research or len(matched_files) > 5:
            tasks.append({
                "key": "research",
                "title": f"Explore & Analyze Codebase for: {objective[:50]}",
                "worker_type": "Researcher",
                "objective": f"Analyze codebase architecture, symbols, and dependencies relevant to: {objective}",
                "relevant_files": matched_files,
                "depends_on": [],
                "constraints": base_constraints + ["Read-only access; do not modify files."],
                "acceptance_criteria": [
                    "Identified all impacted functions, types, and stylesheets.",
                    "Documented exact lines and files requiring modification.",
                ],
                "required_evidence": ["Architectural findings report", "Referenced source locations"],
                "audit_criteria": ["Manager verifies research covers all relevant files"],
                "priority": 1,
                "risk": RiskLevel.LOW,
            })

        # Phase 2: Implementation (Programmer)
        prereqs = ["research"] if (needs_research or len(matched_files) > 5) else []
        tasks.append({
            "key": "implementation",
            "title": f"Implement: {objective[:60]}",
            "worker_type": "Programmer",
            "objective": objective,
            "relevant_files": matched_files if matched_files else ["src/"],
            "depends_on": prereqs,
            "constraints": base_constraints + ["Follow existing project code formatting and styling."],
            "acceptance_criteria": [
                f"Objective fully achieved: '{objective}'.",
                "Clean AST with no introduced syntax or lint errors.",
                "All touched files saved within workspace boundary.",
            ],
            "required_evidence": ["Unified git diff", "List of modified files", "Syntax validation status"],
            "audit_criteria": ["Manager verifies diff matches acceptance criteria without regression"],
            "priority": 2,
            "risk": RiskLevel.MEDIUM,
        })

        # Phase 3: Verification & QA (Tester)
        tasks.append({
            "key": "verification",
            "title": f"Verify & Test: {objective[:50]}",
            "worker_type": "Tester",
            "objective": f"Execute test harness and verify that changes for '{objective}' work as expected without regression.",
            "relevant_files": matched_files,
            "depends_on": ["implementation"],
            "constraints": base_constraints + ["Do not alter application business logic."],
            "acceptance_criteria": [
                "Targeted test suite executes and passes.",
                "No regressions in related subsystem dependencies.",
            ],
            "required_evidence": ["Test execution logs", "Pass/Fail assertion summary"],
            "audit_criteria": ["Manager confirms all automated assertions passed with 0 errors"],
            "priority": 3,
            "risk": RiskLevel.LOW,
        })

        return tasks

    def _generate_worker_prompt(
        self,
        objective: str,
        worker_type: str,
        worker_role: str,
        scoped_files: List[str],
        constraints: List[str],
        acceptance_criteria: List[str],
        required_evidence: List[str],
        tech_stack: Dict[str, Any],
    ) -> str:
        """
        Generates a self-contained, high-context instruction prompt for a worker.
        """
        lines = []
        lines.append(f"### WORKER TASK CONTRACT: {worker_type.upper()}")
        lines.append(f"**Assigned Role**: {worker_role}")
        lines.append(f"**Task Objective**: {objective}")
        lines.append("")

        if scoped_files:
            lines.append("#### Scoped Files & Context:")
            for f in scoped_files:
                lines.append(f"- `{f}`")
            lines.append("")

        if constraints:
            lines.append("#### Operational Constraints:")
            for c in constraints:
                lines.append(f"- {c}")
            lines.append("")

        if acceptance_criteria:
            lines.append("#### Acceptance Criteria:")
            for ac in acceptance_criteria:
                lines.append(f"- [ ] {ac}")
            lines.append("")

        if required_evidence:
            lines.append("#### Required Evidence Output:")
            for ev in required_evidence:
                lines.append(f"- {ev}")
            lines.append("")

        lines.append("#### Execution Instruction:")
        if worker_type == "Researcher":
            lines.append("Inspect the scoped files above. Report findings, dependencies, and recommended modifications.")
        elif worker_type == "Programmer":
            lines.append("Apply the necessary code edits to achieve the acceptance criteria while strictly obeying constraints.")
        elif worker_type == "Tester":
            lines.append("Run the relevant test harness and verify that all assertions pass cleanly.")
        else:
            lines.append("Execute the assigned objective according to the criteria above.")

        return "\n".join(lines)

    def _build_plan_summary(
        self, objective: str, contracts: List[WorkerTaskContract], subsystems: List[str]
    ) -> str:
        """Builds a clean streaming narrative summary of the plan."""
        sub_str = ", ".join([f"`{s}`" for s in subsystems]) if subsystems else "Root Workspace"
        lines = [
            f"Understanding request: {objective}",
            f"Inspecting project: Subsystems {sub_str} identified.",
            f"Planning implementation: Decomposed into {len(contracts)} specialized worker contracts with dependency resolution.",
            "Workforce orchestration: Specialized workers configured and provisioned on-demand.",
        ]
        return "\n\n".join(lines)

