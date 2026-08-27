from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING, Any, Callable, Optional
import uuid

from core.context.model import ContextBudget, ContextPackage
from core.enums import (
    ArtifactType,
    DependencyType,
    IssueSeverity,
    IssueStatus,
    MemoryType,
    ProjectStatus,
    RiskLevel,
    TaskStatus,
    WorkerStatus,
)
from core.errors import (
    DependencyCycleError,
    PersistenceError,
    TaskAlreadyCompletedError,
    TaskNotFoundError,
    WorkerNotEligibleError,
    WorkerNotFoundError,
)
from core.events.model import Event, utc_now
from core.events.types import EventSource, EventType
from core.manager.agent import ManagerAgent
from core.manager.model import (
    ActionResult,
    CycleResult,
    ManagerAction,
    ManagerConfig,
    ManagerDecision,
    ManagerState,
    ManagerStatus,
    Plan,
)
from core.manager.types import (
    AutonomyLevel,
    ConfidenceLevel,
    ManagerActionType,
    PlanStatus,
)
from core.models import Artifact, Project, Task, WorkerManifest
from core.task.state_machine import TaskStateMachine

if TYPE_CHECKING:
    from core.runtime.workforce_runtime import WorkforceRuntime

logger = logging.getLogger("AutonomOS.ManagerController")


class ManagerController:
    """
    Deterministic validation, safety, and execution controller for the Manager.
    Validates all proposed actions from ManagerAgent against runtime invariants,
    executes approved operations, updates project memory, and protects against
    infinite loops, prompt injection, and hallucinated state.
    """

    def __init__(
        self,
        runtime: WorkforceRuntime,
        agent: Optional[ManagerAgent] = None,
        config: Optional[ManagerConfig] = None,
    ):
        self.runtime = runtime
        self.config = config or ManagerConfig()
        self.agent = agent or ManagerAgent(inference_gateway=runtime.inference, config=self.config)

        # Operational tracking per project
        self._consecutive_idle_cycles: dict[str, int] = {}
        self._cycle_counters: dict[str, int] = {}
        self._active_plans: dict[str, Plan] = {}
        self._pending_user_questions: dict[str, list[dict[str, Any]]] = {}

    def get_status(self, project_id: str) -> ManagerStatus:
        """Query real-time operational status for a project."""
        project = self.runtime.projects.get_project(project_id)
        active_plan = self.get_active_plan(project_id)
        cycles = self._cycle_counters.get(project_id, 0)
        idle_cycles = self._consecutive_idle_cycles.get(project_id, 0)
        pending_questions = self._pending_user_questions.get(project_id, [])

        recent_decisions = self.runtime.store.list_manager_decisions_for_project(project_id, limit=1)
        last_decision_id = recent_decisions[-1].decision_id if recent_decisions else None

        tasks = self.runtime.tasks.list_tasks(project_id)
        active_tasks = [t for t in tasks if t.status in (TaskStatus.RUNNING, TaskStatus.ASSIGNED)]
        completed_tasks = [t for t in tasks if t.status == TaskStatus.COMPLETED]

        summary = f"Project '{project.name}': {len(completed_tasks)}/{len(tasks)} tasks completed. {len(active_tasks)} active."

        return ManagerStatus(
            project_id=project_id,
            is_active=len(active_tasks) > 0 or len(pending_questions) > 0,
            current_activity="Running tasks" if active_tasks else ("Awaiting user input" if pending_questions else "Idle"),
            active_plan_id=active_plan.id if active_plan else None,
            last_decision_id=last_decision_id,
            pending_user_input=len(pending_questions) > 0,
            stagnation_detected=idle_cycles >= self.config.max_cycles_without_progress,
            total_cycles=cycles,
            total_cost=self.agent.total_cost_accumulated,
            summary_text=summary,
        )

    def get_active_plan(self, project_id: str) -> Optional[Plan]:
        """Retrieve the latest active plan for a project."""
        if project_id in self._active_plans:
            return self._active_plans[project_id]
        plans = self.runtime.store.list_plans_for_project(project_id)
        active_plans = [p for p in plans if p.status == PlanStatus.ACTIVE]
        if active_plans:
            self._active_plans[project_id] = active_plans[-1]
            return active_plans[-1]
        return None

    def gather_state(self, project_id: str) -> ManagerState:
        """Gathers deterministic state from runtime registries and storage."""
        project = self.runtime.projects.get_project(project_id)
        tasks = self.runtime.tasks.list_tasks(project_id)
        workers = self.runtime.workers.list_workers()
        events = self.runtime.get_events(project_id=project_id, limit=20)
        artifacts = self.runtime.artifacts.list_artifacts_for_project(project_id)
        active_plan = self.get_active_plan(project_id)

        active_tids = [t.id for t in tasks if t.status in (TaskStatus.RUNNING, TaskStatus.ASSIGNED)]
        completed_tids = [t.id for t in tasks if t.status == TaskStatus.COMPLETED]
        failed_tids = [t.id for t in tasks if t.status == TaskStatus.FAILED]
        blocked_tids = [t.id for t in tasks if t.status == TaskStatus.BLOCKED]

        verifications: list[dict[str, Any]] = []
        for e in events:
            if e.event_type in (EventType.VERIFICATION_PASSED, EventType.VERIFICATION_FAILED):
                verifications.append({
                    "task_id": e.task_id,
                    "status": "PASSED" if e.event_type == EventType.VERIFICATION_PASSED else "FAILED",
                    "summary": e.payload.get("summary", ""),
                    "timestamp": e.timestamp,
                })

        return ManagerState(
            project_id=project_id,
            objective=project.description or project.name,
            current_plan=active_plan,
            tasks=tasks,
            workers=workers,
            active_task_ids=active_tids,
            completed_task_ids=completed_tids,
            failed_task_ids=failed_tids,
            blocked_task_ids=blocked_tids,
            recent_events=events,
            recent_artifacts=artifacts,
            verification_summaries=verifications,
            unresolved_issues=[],
            pending_user_questions=self._pending_user_questions.get(project_id, []),
            cycle_count=self._cycle_counters.get(project_id, 0),
            consecutive_idle_cycles=self._consecutive_idle_cycles.get(project_id, 0),
        )

    def execute_cycle(
        self,
        project_id: str,
        trigger_event: Optional[Event] = None,
        feedback_message: Optional[str] = None,
    ) -> CycleResult:
        """
        Execute a single deterministic Manager orchestration cycle.
        Gathers state, queries ManagerAgent, validates proposals, and executes actions.
        """
        cycle_id = f"cycle-{uuid.uuid4().hex[:8]}"
        self._cycle_counters[project_id] = self._cycle_counters.get(project_id, 0) + 1
        current_cycle_num = self._cycle_counters[project_id]

        # 1. Cost & Safety Guardrails Pre-Check
        if self.agent.total_cost_accumulated >= self.config.max_cost_per_project:
            self.runtime.log_event(
                event_type=EventType.MANAGER_ESCALATED,
                payload={
                    "title": "Cost limit exceeded",
                    "reason": f"Accumulated cost ${self.agent.total_cost_accumulated:.4f} exceeded limit ${self.config.max_cost_per_project:.2f}",
                },
                project_id=project_id,
                source=EventSource.MANAGER,
            )
            return CycleResult(
                cycle_id=cycle_id,
                decision=None,
                escalated=True,
                status_summary=f"Cost budget of ${self.config.max_cost_per_project:.2f} exceeded.",
            )

        # 2. Gather State from Runtime
        state = self.gather_state(project_id)

        # 3. Assemble Bounded Context Package via Context Engine
        context_pkg: Optional[ContextPackage] = None
        try:
            context_pkg = self.runtime.request_context(
                task_id=f"mgr-state-{project_id}",
                worker_id="worker.manager.orchestrator",
                budget=self.config.context_budget,
            )
        except Exception as ctx_err:
            logger.warning(f"Context retrieval for Manager cycle returned warning: {ctx_err}")

        # 4. Emit MANAGER_CYCLE_STARTED & MANAGER_CONTEXT_BUILT
        start_evt = self.runtime.log_event(
            event_type=EventType.MANAGER_CYCLE_STARTED,
            payload={"cycle_id": cycle_id, "cycle_number": current_cycle_num, "trigger": trigger_event.event_type.value if trigger_event else "MANUAL"},
            project_id=project_id,
            source=EventSource.MANAGER,
            correlation_id=project_id,
        )

        if context_pkg:
            self.runtime.log_event(
                event_type=EventType.MANAGER_CONTEXT_BUILT,
                payload={"cycle_id": cycle_id, "package_id": context_pkg.request_id, "items_count": len(context_pkg.items), "total_tokens": context_pkg.total_estimated_tokens},
                project_id=project_id,
                source=EventSource.MANAGER,
                correlation_id=project_id,
                causation_id=start_evt.event_id,
            )

        # 5. Invoke ManagerAgent Reasoning Step
        decision, inference_resp = self.agent.reason(
            state=state,
            cycle_id=cycle_id,
            context_package=context_pkg,
            trigger_event=trigger_event,
            feedback_message=feedback_message,
            causation_id=start_evt.event_id,
        )

        # 6. Persist Decision Record and Emit MANAGER_DECISION_CREATED
        self.runtime.store.save_manager_decision(decision)
        dec_evt = self.runtime.log_event(
            event_type=EventType.MANAGER_DECISION_CREATED,
            payload={
                "decision_id": decision.decision_id,
                "cycle_id": cycle_id,
                "actions_count": len(decision.actions),
                "reasoning_summary": decision.reasoning_summary,
                "confidence_level": decision.confidence_level.value,
            },
            project_id=project_id,
            source=EventSource.MANAGER,
            correlation_id=project_id,
            causation_id=start_evt.event_id,
        )

        # 7. Apply Plan Updates if proposed
        if decision.plan_update:
            self._apply_plan_update(project_id, decision.plan_update, dec_evt.event_id)

        # 8. Deterministic Validation and Execution of Actions
        action_results: list[ActionResult] = []
        progress_made = False
        waiting = False
        user_input_req = False
        escalated = False
        completed = False

        if len(decision.actions) > self.config.max_actions_per_cycle:
            # Enforce max action cap
            decision.actions = decision.actions[: self.config.max_actions_per_cycle]

        for action in decision.actions:
            result = self._validate_and_execute_action(project_id, action, dec_evt.event_id)
            action_results.append(result)

            if result.accepted:
                if action.action_type in (
                    ManagerActionType.CREATE_TASK,
                    ManagerActionType.ASSIGN_TASK,
                    ManagerActionType.UPDATE_TASK,
                    ManagerActionType.REQUEST_RETRY,
                    ManagerActionType.UPDATE_MEMORY,
                ):
                    progress_made = True
                elif action.action_type == ManagerActionType.WAIT:
                    waiting = True
                elif action.action_type == ManagerActionType.REQUEST_USER_INPUT:
                    user_input_req = True
                elif action.action_type == ManagerActionType.ESCALATE:
                    escalated = True
                elif action.action_type == ManagerActionType.COMPLETE_PROJECT:
                    completed = True

        # 9. Stagnation & Loop Detection
        if progress_made:
            self._consecutive_idle_cycles[project_id] = 0
        else:
            # If waiting for currently running tasks, don't penalize as stagnation
            running_tasks = [t for t in self.runtime.tasks.list_tasks(project_id) if t.status == TaskStatus.RUNNING]
            if not running_tasks and not user_input_req and not completed:
                self._consecutive_idle_cycles[project_id] = self._consecutive_idle_cycles.get(project_id, 0) + 1
                if self._consecutive_idle_cycles[project_id] >= self.config.max_cycles_without_progress:
                    self.runtime.log_event(
                        event_type=EventType.MANAGER_STAGNATION_DETECTED,
                        payload={
                            "consecutive_idle_cycles": self._consecutive_idle_cycles[project_id],
                            "cycle_id": cycle_id,
                        },
                        project_id=project_id,
                        source=EventSource.MANAGER,
                        correlation_id=project_id,
                        causation_id=dec_evt.event_id,
                    )
                    escalated = True

        # 10. Emit MANAGER_CYCLE_COMPLETED
        self.runtime.log_event(
            event_type=EventType.MANAGER_CYCLE_COMPLETED,
            payload={
                "cycle_id": cycle_id,
                "actions_executed": len([r for r in action_results if r.accepted]),
                "actions_rejected": len([r for r in action_results if not r.accepted]),
                "status": "COMPLETED" if completed else ("ESCALATED" if escalated else "OK"),
            },
            project_id=project_id,
            source=EventSource.MANAGER,
            correlation_id=project_id,
            causation_id=dec_evt.event_id,
        )

        return CycleResult(
            cycle_id=cycle_id,
            decision=decision,
            results=action_results,
            progress_detected=progress_made,
            completed=completed,
            waiting=waiting,
            user_input_required=user_input_req,
            escalated=escalated,
            status_summary=f"Cycle {current_cycle_num}: {len([r for r in action_results if r.accepted])} actions accepted.",
        )

    def _apply_plan_update(self, project_id: str, update: dict[str, Any], causation_id: str) -> Plan:
        """Create or revise the project plan with proper version increment."""
        current_plan = self.get_active_plan(project_id)
        if not current_plan:
            plan = Plan(
                id=f"plan-{uuid.uuid4().hex[:8]}",
                project_id=project_id,
                objective=update.get("objective", ""),
                tasks=list(update.get("tasks", [])),
                milestones=list(update.get("milestones", [])),
                dependencies=dict(update.get("dependencies", {})),
                status=PlanStatus.ACTIVE,
                version=1,
            )
            self.runtime.store.save_plan(plan)
            self._active_plans[project_id] = plan
            self.runtime.log_event(
                event_type=EventType.MANAGER_PLAN_CREATED,
                payload={
                    "plan_id": plan.id,
                    "version": plan.version,
                    "objective": plan.objective,
                    "tasks_count": len(plan.tasks),
                    "milestones": plan.milestones,
                },
                project_id=project_id,
                source=EventSource.MANAGER,
                correlation_id=project_id,
                causation_id=causation_id,
            )
            return plan
        else:
            # Replan / Version Increment
            old_version = current_plan.version
            current_plan.status = PlanStatus.REVISED
            self.runtime.store.save_plan(current_plan)

            new_plan = Plan(
                id=f"plan-{uuid.uuid4().hex[:8]}",
                project_id=project_id,
                objective=update.get("objective", current_plan.objective),
                tasks=list(update.get("tasks", current_plan.tasks)),
                milestones=list(update.get("milestones", current_plan.milestones)),
                dependencies=dict(update.get("dependencies", current_plan.dependencies)),
                status=PlanStatus.ACTIVE,
                version=old_version + 1,
            )
            self.runtime.store.save_plan(new_plan)
            self._active_plans[project_id] = new_plan

            self.runtime.log_event(
                event_type=EventType.MANAGER_REPLAN,
                payload={
                    "old_plan_id": current_plan.id,
                    "old_version": old_version,
                    "new_plan_id": new_plan.id,
                    "new_version": new_plan.version,
                    "reason": update.get("reason", "Plan revised by Manager"),
                },
                project_id=project_id,
                source=EventSource.MANAGER,
                correlation_id=project_id,
                causation_id=causation_id,
            )
            return new_plan

    def _validate_and_execute_action(
        self,
        project_id: str,
        action: ManagerAction,
        causation_id: str,
    ) -> ActionResult:
        """Deterministic validation and execution of an individual proposed action."""
        atype = action.action_type
        params = action.parameters

        try:
            if atype == ManagerActionType.CREATE_TASK:
                title = str(params.get("title", "")).strip()
                objective = str(params.get("objective", "")).strip()
                if not title:
                    return self._reject_action(project_id, action, "Task title cannot be empty", causation_id)

                priority = max(1, min(100, int(params.get("priority", 50))))
                risk_str = str(params.get("risk", "MEDIUM")).upper()
                try:
                    risk = RiskLevel(risk_str)
                except ValueError:
                    risk = RiskLevel.MEDIUM

                created_task = self.runtime.create_task(
                    project_id=project_id,
                    title=title,
                    objective=objective,
                    priority=priority,
                    risk=risk,
                )

                # Attach success criteria if specified
                if "success_criteria" in params and isinstance(params["success_criteria"], list):
                    created_task.success_criteria = params["success_criteria"]
                    self.runtime.store.save_task(created_task)

                # Attach dependencies if specified
                deps = params.get("dependencies", [])
                for dep_id in deps:
                    try:
                        self.runtime.tasks.add_dependency(created_task.id, dep_id)
                    except Exception as dep_err:
                        logger.warning(f"Could not link dependency {dep_id} to {created_task.id}: {dep_err}")

                return self._accept_action(
                    project_id,
                    action,
                    causation_id,
                    target_id=created_task.id,
                    output={"task_id": created_task.id, "title": created_task.title},
                )

            elif atype == ManagerActionType.UPDATE_TASK:
                task_id = params.get("task_id")
                if not task_id:
                    return self._reject_action(project_id, action, "Missing 'task_id'", causation_id)
                task = self.runtime.tasks.get_task(task_id)
                if not task:
                    return self._reject_action(project_id, action, f"Task '{task_id}' not found", causation_id)

                if "title" in params:
                    task.title = str(params["title"])
                if "objective" in params:
                    task.objective = str(params["objective"])
                if "priority" in params:
                    task.priority = int(params["priority"])

                self.runtime.store.save_task(task)
                return self._accept_action(project_id, action, causation_id, target_id=task.id)

            elif atype == ManagerActionType.ASSIGN_TASK:
                task_id = params.get("task_id")
                worker_id = params.get("worker_id")
                if not task_id or not worker_id:
                    return self._reject_action(project_id, action, "Missing 'task_id' or 'worker_id'", causation_id)

                task = self.runtime.tasks.get_task(task_id)
                if not task:
                    return self._reject_action(project_id, action, f"Task '{task_id}' not found", causation_id)

                if task.status in (TaskStatus.COMPLETED, TaskStatus.RUNNING):
                    return self._reject_action(project_id, action, f"Task '{task_id}' is already {task.status.value}", causation_id)

                # Check worker existence
                try:
                    worker = self.runtime.workers.get_worker(worker_id)
                except Exception:
                    return self._reject_action(project_id, action, f"Worker '{worker_id}' does not exist", causation_id)

                # Check dependencies satisfied
                prereqs = self.runtime.store.get_dependencies_for_task(task_id)
                unsatisfied = []
                for p in prereqs:
                    prereq_task = self.runtime.tasks.get_task(p.prerequisite_task_id)
                    if prereq_task.status != TaskStatus.COMPLETED:
                        unsatisfied.append(prereq_task.id)

                if unsatisfied:
                    return self._reject_action(
                        project_id,
                        action,
                        f"Task '{task_id}' has unsatisfied dependencies: {', '.join(unsatisfied)}",
                        causation_id,
                    )

                assigned_task, assigned_worker = self.runtime.assign_task(task_id, worker_id)
                return self._accept_action(
                    project_id,
                    action,
                    causation_id,
                    target_id=task_id,
                    output={"task_id": assigned_task.id, "worker_id": assigned_worker.id},
                )

            elif atype == ManagerActionType.REQUEST_RETRY:
                task_id = params.get("task_id")
                if not task_id:
                    return self._reject_action(project_id, action, "Missing 'task_id'", causation_id)
                task = self.runtime.tasks.get_task(task_id)
                if not task:
                    return self._reject_action(project_id, action, f"Task '{task_id}' not found", causation_id)

                if task.attempts >= self.config.retry_budget_per_task:
                    return self._reject_action(
                        project_id,
                        action,
                        f"Task '{task_id}' retry budget exhausted ({task.attempts}/{self.config.retry_budget_per_task} attempts). Replan or escalate.",
                        causation_id,
                    )

                TaskStateMachine.validate_and_transition(task, TaskStatus.RETRYING)
                self.runtime.store.save_task(task)
                return self._accept_action(project_id, action, causation_id, target_id=task_id)

            elif atype == ManagerActionType.UPDATE_MEMORY:
                mem_type = str(params.get("memory_type", "DECISION")).upper()
                title = str(params.get("title", "Project Note"))
                content = str(params.get("content", ""))

                if mem_type == "DECISION":
                    doc = self.runtime.record_decision(
                        project_id=project_id,
                        title=title,
                        context=params.get("context", "Manager decision update"),
                        decision=content or params.get("decision", ""),
                        reasoning=params.get("reasoning", "Recorded during orchestration cycle"),
                        consequences=params.get("consequences", "Standard workflow"),
                    )
                elif mem_type == "CURRENT_STATE":
                    doc = self.runtime.update_current_state(
                        project_id=project_id,
                        stage=params.get("stage", "Implementation"),
                        completed_milestones=params.get("completed_milestones", []),
                        active_work=params.get("active_work", []),
                        known_limitations=params.get("known_limitations", []),
                        notes=content,
                    )
                else:
                    doc = self.runtime.memory.create_memory(
                        project_id=project_id,
                        memory_type=MemoryType.NOTE,
                        title=title,
                        content=content,
                        relative_path=f"notes/{title.lower().replace(' ', '_')}.md",
                    )

                return self._accept_action(
                    project_id,
                    action,
                    causation_id,
                    target_id=doc.id,
                    output={"memory_id": doc.id, "title": doc.title},
                )

            elif atype == ManagerActionType.REQUEST_USER_INPUT:
                question = str(params.get("question", "")).strip()
                if not question:
                    return self._reject_action(project_id, action, "Missing 'question'", causation_id)

                q_entry = {
                    "id": f"q-{uuid.uuid4().hex[:6]}",
                    "question": question,
                    "reason": params.get("reason", ""),
                    "options": params.get("options", []),
                    "created_at": utc_now(),
                }
                if project_id not in self._pending_user_questions:
                    self._pending_user_questions[project_id] = []
                self._pending_user_questions[project_id].append(q_entry)

                self.runtime.log_event(
                    event_type=EventType.MANAGER_USER_INPUT_REQUIRED,
                    payload=q_entry,
                    project_id=project_id,
                    source=EventSource.MANAGER,
                    correlation_id=project_id,
                    causation_id=causation_id,
                )
                return self._accept_action(project_id, action, causation_id, output=q_entry)

            elif atype == ManagerActionType.WAIT:
                self.runtime.log_event(
                    event_type=EventType.MANAGER_WAITING,
                    payload={"reason": params.get("reason", "Waiting for active tasks")},
                    project_id=project_id,
                    source=EventSource.MANAGER,
                    correlation_id=project_id,
                    causation_id=causation_id,
                )
                return self._accept_action(project_id, action, causation_id)

            elif atype == ManagerActionType.ESCALATE:
                self.runtime.log_event(
                    event_type=EventType.MANAGER_ESCALATED,
                    payload={
                        "title": params.get("title", "Manager Escalation"),
                        "reason": params.get("reason", "Blocker encountered"),
                        "severity": params.get("severity", "HIGH"),
                    },
                    project_id=project_id,
                    source=EventSource.MANAGER,
                    correlation_id=project_id,
                    causation_id=causation_id,
                )
                return self._accept_action(project_id, action, causation_id)

            elif atype == ManagerActionType.COMPLETE_PROJECT:
                tasks = self.runtime.tasks.list_tasks(project_id)
                uncompleted = [t.id for t in tasks if t.status != TaskStatus.COMPLETED]
                if uncompleted:
                    return self._reject_action(
                        project_id,
                        action,
                        f"Cannot complete project: Tasks {', '.join(uncompleted)} are not COMPLETED.",
                        causation_id,
                    )

                active_plan = self.get_active_plan(project_id)
                if active_plan:
                    active_plan.status = PlanStatus.COMPLETED
                    self.runtime.store.save_plan(active_plan)

                return self._accept_action(project_id, action, causation_id, output={"status": "COMPLETED"})

            else:
                return self._reject_action(project_id, action, f"Unsupported action type '{atype}'", causation_id)

        except Exception as exec_err:
            logger.error(f"Error executing action {atype}: {exec_err}", exc_info=True)
            return self._reject_action(project_id, action, str(exec_err), causation_id)

    def _accept_action(
        self,
        project_id: str,
        action: ManagerAction,
        causation_id: str,
        target_id: Optional[str] = None,
        output: Optional[dict[str, Any]] = None,
    ) -> ActionResult:
        atype_str = action.action_type.value if hasattr(action.action_type, "value") else str(action.action_type)
        self.runtime.log_event(
            event_type=EventType.MANAGER_ACTION_ACCEPTED,
            payload={
                "action_type": atype_str,
                "target_id": target_id,
                "rationale": action.rationale,
                "parameters": action.parameters,
            },
            project_id=project_id,
            source=EventSource.MANAGER,
            correlation_id=project_id,
            causation_id=causation_id,
        )
        return ActionResult(action=action, accepted=True, execution_output=output)

    def _reject_action(
        self,
        project_id: str,
        action: ManagerAction,
        reason: str,
        causation_id: str,
    ) -> ActionResult:
        atype_str = action.action_type.value if hasattr(action.action_type, "value") else str(action.action_type)
        self.runtime.log_event(
            event_type=EventType.MANAGER_ACTION_REJECTED,
            payload={
                "action_type": atype_str,
                "reason": reason,
                "parameters": action.parameters,
            },
            project_id=project_id,
            source=EventSource.MANAGER,
            correlation_id=project_id,
            causation_id=causation_id,
        )
        return ActionResult(action=action, accepted=False, reason=reason)

    def run_orchestration(self, project_id: str, max_cycles: int = 15) -> CycleResult:
        """
        Automated orchestration loop: runs Manager cycles, dispatches assigned worker tasks,
        and continues until project is completed, user input is required, or limits are reached.
        """
        last_result: Optional[CycleResult] = None

        for _ in range(max_cycles):
            # 1. Execute Manager reasoning and action dispatch cycle
            cycle_res = self.execute_cycle(project_id)
            last_result = cycle_res

            if cycle_res.completed or cycle_res.escalated or cycle_res.user_input_required:
                break

            # 2. Check for assigned / ready tasks to run
            tasks = self.runtime.tasks.list_tasks(project_id)
            assigned_tasks = [t for t in tasks if t.status == TaskStatus.ASSIGNED and t.assigned_worker]

            if not assigned_tasks:
                if cycle_res.waiting or not cycle_res.progress_detected:
                    # No runnable tasks and no new actions
                    break
                continue

            # 3. Dispatch ready assigned tasks deterministically
            for task in assigned_tasks:
                try:
                    self.runtime.run_task(task.id)
                except Exception as run_err:
                    logger.error(f"Task {task.id} execution threw error: {run_err}")

        return last_result or CycleResult(cycle_id="empty-cycle", decision=None, status_summary="No cycles run.")
