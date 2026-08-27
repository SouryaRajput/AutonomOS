from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional

from core.events.model import Event
from core.events.types import EventSource, EventType


class ActivityLevel(str, Enum):
    INFO = "INFO"
    SUCCESS = "SUCCESS"
    WARNING = "WARNING"
    ERROR = "ERROR"


@dataclass
class ActivityItem:
    """
    Product-facing projection of a system event.
    Designed for consumption by UI components (Activity Feed, Task Timelines, Chat notifications).
    """
    id: str
    event_id: str
    event_type: str
    timestamp: str
    title: str
    description: str
    level: ActivityLevel = ActivityLevel.INFO
    icon: str = "activity"
    actor: Optional[str] = None
    target: Optional[str] = None
    project_id: Optional[str] = None
    task_id: Optional[str] = None
    worker_id: Optional[str] = None
    artifact_id: Optional[str] = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "event_id": self.event_id,
            "event_type": self.event_type,
            "timestamp": self.timestamp,
            "title": self.title,
            "description": self.description,
            "level": self.level.value if isinstance(self.level, ActivityLevel) else self.level,
            "icon": self.icon,
            "actor": self.actor,
            "target": self.target,
            "project_id": self.project_id,
            "task_id": self.task_id,
            "worker_id": self.worker_id,
            "artifact_id": self.artifact_id,
            "metadata": self.metadata,
        }


class ActivityProjector:
    """Projects technical low-level Event streams into human-friendly ActivityItems."""

    @classmethod
    def project(cls, event: Event) -> ActivityItem:
        p = event.payload
        t = event.event_type
        level = ActivityLevel.INFO
        icon = "activity"
        title = t.value
        description = ""

        # Project Events
        if t == EventType.PROJECT_CREATED:
            icon = "project_create"
            level = ActivityLevel.SUCCESS
            title = f"Project '{p.get('name', event.project_id)}' created"
            description = p.get("description", "")

        elif t == EventType.PROJECT_UPDATED:
            icon = "project_edit"
            title = f"Project '{event.project_id}' updated"

        elif t == EventType.PROJECT_DELETED:
            icon = "project_delete"
            level = ActivityLevel.WARNING
            title = f"Project '{event.project_id}' deleted"

        # Worker Events
        elif t == EventType.WORKER_REGISTERED:
            icon = "worker_add"
            level = ActivityLevel.SUCCESS
            title = f"Worker '{event.worker_id}' registered ({p.get('role', 'Unknown role')})"

        elif t == EventType.WORKER_ASSIGNED:
            icon = "worker_assign"
            title = f"Worker '{event.worker_id}' assigned to task '{event.task_id}'"

        elif t == EventType.WORKER_STARTED:
            icon = "worker_run"
            title = f"Worker '{event.worker_id}' started working on task '{event.task_id}'"

        elif t == EventType.WORKER_FAILED:
            icon = "alert_error"
            level = ActivityLevel.ERROR
            title = f"Worker '{event.worker_id}' encountered a failure"
            description = p.get("reason", "")

        elif t == EventType.WORKER_BECAME_IDLE:
            icon = "worker_idle"
            title = f"Worker '{event.worker_id}' is now idle"

        # Task Events
        elif t == EventType.TASK_CREATED:
            icon = "task_add"
            title = f"Task created: '{p.get('title', event.task_id)}'"
            description = p.get("objective", "")

        elif t == EventType.TASK_READY:
            icon = "task_ready"
            title = f"Task '{event.task_id}' is now ready"
            description = p.get("reason", "")

        elif t == EventType.TASK_ASSIGNED:
            icon = "task_assign"
            title = f"Task '{event.task_id}' assigned to worker '{event.worker_id}'"

        elif t == EventType.TASK_STARTED:
            icon = "task_start"
            title = f"Task '{event.task_id}' started (Attempt {p.get('attempt', 1)})"

        elif t == EventType.TASK_COMPLETED:
            icon = "task_success"
            level = ActivityLevel.SUCCESS
            title = f"Task '{event.task_id}' completed successfully"
            description = p.get("summary", "")

        elif t == EventType.TASK_FAILED:
            icon = "task_error"
            level = ActivityLevel.ERROR
            title = f"Task '{event.task_id}' failed"
            description = p.get("reason", "")

        elif t == EventType.TASK_RETRYING:
            icon = "task_retry"
            level = ActivityLevel.WARNING
            title = f"Task '{event.task_id}' retrying (Attempt {p.get('attempt', 1)}/{p.get('max_attempts', 3)})"
            description = p.get("reason", "")

        elif t == EventType.TASK_BLOCKED:
            icon = "task_block"
            level = ActivityLevel.WARNING
            title = f"Task '{event.task_id}' blocked"

        elif t == EventType.TASK_CANCELLED:
            icon = "task_cancel"
            level = ActivityLevel.WARNING
            title = f"Task '{event.task_id}' cancelled"
            description = p.get("reason", "")

        # Dependency Events
        elif t == EventType.DEPENDENCY_ADDED:
            icon = "dependency"
            title = f"Dependency linked: '{p.get('dependent_task_id')}' requires '{p.get('prerequisite_task_id')}'"

        # Artifact Events
        elif t == EventType.ARTIFACT_CREATED:
            icon = "artifact"
            level = ActivityLevel.SUCCESS
            title = f"Artifact produced: '{p.get('path', event.artifact_id)}'"
            description = p.get("description", "")

        # Memory & Knowledge Events
        elif t == EventType.MEMORY_CREATED:
            icon = "memory_create"
            level = ActivityLevel.SUCCESS
            title = f"Memory recorded: '{p.get('title', event.payload.get('relative_path', 'document'))}'"
            description = p.get("summary", "")

        elif t == EventType.MEMORY_UPDATED:
            icon = "memory_edit"
            title = f"Memory updated: '{p.get('title', event.payload.get('relative_path', 'document'))}'"
            description = f"Updated to version {p.get('version', 1)}"

        elif t == EventType.MEMORY_DELETED:
            icon = "memory_delete"
            level = ActivityLevel.WARNING
            title = f"Memory deleted: '{p.get('relative_path', 'document')}'"

        elif t == EventType.DECISION_CREATED:
            icon = "decision"
            level = ActivityLevel.SUCCESS
            title = f"Architectural Decision: '{p.get('title', 'ADR')}'"
            description = p.get("summary", "")

        elif t == EventType.REPORT_CREATED:
            icon = "report"
            level = ActivityLevel.SUCCESS
            title = f"Report filed: '{p.get('title', 'Task Report')}'"
            description = p.get("summary", "")

        elif t == EventType.ISSUE_RECORDED:
            icon = "issue_open"
            level = ActivityLevel.WARNING
            title = f"Issue recorded: '{p.get('title', 'Known Problem')}'"
            description = p.get("description", "")

        elif t == EventType.ISSUE_UPDATED:
            icon = "issue_update"
            title = f"Issue status updated: '{p.get('title', 'Known Problem')}'"
            description = f"Status: {p.get('status', 'OPEN')}"

        # Context Engine Events
        elif t == EventType.CONTEXT_REQUESTED:
            icon = "context_req"
            title = f"Context requested for task '{event.task_id}'"
            description = f"Worker: {event.worker_id or 'General'}"

        elif t == EventType.CONTEXT_ASSEMBLED:
            icon = "context_assembled"
            level = ActivityLevel.SUCCESS
            title = f"Context assembled for task '{event.task_id}'"
            description = f"{p.get('selected_count', 0)} items selected (~{p.get('token_estimate', 0)} tokens)"

        elif t == EventType.CONTEXT_WARNING:
            icon = "alert_warning"
            level = ActivityLevel.WARNING
            title = f"Context Warning: {p.get('warning_type', 'Warning')}"
            description = p.get("message", "")

        # Tool Runtime Events (Stage 5)
        elif t == EventType.TOOL_REQUESTED:
            icon = "tool_req"
            title = f"Tool requested: '{p.get('tool_id', 'Unknown Tool')}'"
            description = f"Worker: {event.worker_id or 'Worker'}"

        elif t == EventType.TOOL_AUTHORIZED:
            icon = "tool_auth"
            title = f"Tool authorized: '{p.get('tool_id', 'Tool')}'"
            description = f"Risk: {p.get('risk_level', 'LOW')}"

        elif t == EventType.TOOL_DENIED:
            icon = "tool_denied"
            level = ActivityLevel.WARNING
            title = f"Tool denied: '{p.get('tool_id', 'Tool')}'"
            description = p.get("reason", "Permission denied")

        elif t == EventType.TOOL_STARTED:
            icon = "tool_start"
            title = f"Tool started: '{p.get('tool_id', 'Tool')}'"
            description = f"Timeout: {p.get('timeout', 30)}s"

        elif t == EventType.TOOL_COMPLETED:
            icon = "tool_done"
            level = ActivityLevel.SUCCESS
            title = f"Tool completed: '{p.get('tool_id', 'Tool')}'"
            description = f"Duration: {p.get('duration_ms', 0)}ms | Status: {p.get('status', 'SUCCESS')}"

        elif t == EventType.TOOL_FAILED:
            icon = "tool_fail"
            level = ActivityLevel.ERROR
            title = f"Tool failed: '{p.get('tool_id', 'Tool')}'"
            description = p.get("error", "Tool execution failed")

        elif t == EventType.TOOL_TIMED_OUT:
            icon = "tool_timeout"
            level = ActivityLevel.ERROR
            title = f"Tool timed out: '{p.get('tool_id', 'Tool')}'"
            description = f"Duration: {p.get('duration_ms', 0)}ms"

        # Safety & Checkpoints Events (Stage 6)
        elif t == EventType.SAFETY_CHECK_REQUESTED:
            icon = "shield_check"
            title = f"Safety check: '{p.get('tool_id', 'Operation')}'"
            description = f"Risk: {p.get('risk_level', 'LOW')}"

        elif t == EventType.SAFETY_ALLOWED:
            icon = "shield_ok"
            title = f"Safety policy cleared: '{p.get('tool_id', 'Operation')}'"
            description = f"Risk: {p.get('risk_level', 'LOW')}"

        elif t == EventType.SAFETY_DENIED:
            icon = "shield_denied"
            level = ActivityLevel.WARNING
            title = f"Safety policy blocked: '{p.get('tool_id', 'Operation')}'"
            description = "; ".join(p.get("reasons", ["Blocked by safety policy"]))

        elif t == EventType.SAFETY_ESCALATED:
            icon = "shield_alert"
            level = ActivityLevel.WARNING
            title = f"Safety escalated for review: '{p.get('tool_id', 'Operation')}'"
            description = "; ".join(p.get("reasons", ["Approval required"]))

        elif t == EventType.CHECKPOINT_CREATED:
            icon = "checkpoint_created"
            level = ActivityLevel.SUCCESS
            title = f"Checkpoint created: '{p.get('checkpoint_id', 'Snapshot')}'"
            description = f"Type: {p.get('checkpoint_type', 'Snapshot')} | Task: {event.task_id}"

        elif t == EventType.CHECKPOINT_COMMITTED:
            icon = "checkpoint_committed"
            level = ActivityLevel.SUCCESS
            title = f"Checkpoint committed: '{p.get('checkpoint_id', 'Snapshot')}'"
            description = f"Task '{event.task_id}' verified safely."

        elif t == EventType.CHECKPOINT_ROLLBACK_STARTED:
            icon = "rollback_start"
            level = ActivityLevel.WARNING
            title = f"Rollback started: '{p.get('checkpoint_id', 'Snapshot')}'"
            description = f"Reverting worker modifications for task '{event.task_id}'"

        elif t == EventType.CHECKPOINT_ROLLBACK_COMPLETED:
            icon = "rollback_done"
            level = ActivityLevel.SUCCESS
            title = f"Rollback completed: '{p.get('checkpoint_id', 'Snapshot')}'"
            description = f"Restored {p.get('restored_count', 0)} files, preserved {p.get('preserved_user_count', 0)} user files."

        elif t == EventType.CHECKPOINT_ROLLBACK_FAILED:
            icon = "rollback_failed"
            level = ActivityLevel.ERROR
            title = f"Rollback FAILED: '{p.get('checkpoint_id', 'Snapshot')}'"
            description = p.get("error", "Rollback verification failed.")

        elif t == EventType.SCOPE_DEVIATION_DETECTED:
            icon = "scope_alert"
            level = ActivityLevel.WARNING
            title = f"Scope deviation detected in task '{event.task_id}'"
            description = f"{p.get('deviations_count', 1)} deviations detected."

        # Verification & Evidence Events (Stage 7)
        elif t == EventType.VERIFICATION_STARTED:
            icon = "verify_start"
            title = f"Verification started for task '{event.task_id}'"
            description = f"Evaluating {p.get('criteria_count', 1)} criteria."

        elif t == EventType.CHECK_STARTED:
            icon = "check_start"
            title = f"Running check: {p.get('check_type', 'Check')}"
            description = p.get("description", "")

        elif t == EventType.CHECK_PASSED:
            icon = "check_pass"
            level = ActivityLevel.SUCCESS
            title = f"Check PASSED: {p.get('check_type', 'Check')}"
            description = f"Duration: {p.get('duration_ms', 0)}ms"

        elif t == EventType.CHECK_FAILED:
            icon = "check_fail"
            level = ActivityLevel.ERROR
            title = f"Check FAILED: {p.get('check_type', 'Check')}"
            description = p.get("error", "Criteria not satisfied")

        elif t in (EventType.CHECK_UNCERTAIN, EventType.CHECK_ERROR):
            icon = "check_warn"
            level = ActivityLevel.WARNING
            title = f"Check {p.get('status', 'UNCERTAIN')}: {p.get('check_type', 'Check')}"
            description = p.get("error", "Inconclusive check outcome")

        elif t == EventType.VERIFICATION_PASSED:
            icon = "verify_pass"
            level = ActivityLevel.SUCCESS
            title = f"Verification PASSED for task '{event.task_id}'"
            description = p.get("summary", "All criteria satisfied.")

        elif t == EventType.VERIFICATION_FAILED:
            icon = "verify_fail"
            level = ActivityLevel.ERROR
            title = f"Verification FAILED for task '{event.task_id}'"
            description = p.get("summary", "Failed required verification criteria.")

        elif t == EventType.VERIFICATION_UNCERTAIN:
            icon = "verify_uncertain"
            level = ActivityLevel.WARNING
            title = f"Verification UNCERTAIN for task '{event.task_id}'"
            description = p.get("summary", "Inconclusive verification outcome.")

        # Inference Gateway & OmniRoute Events (Stage 8)
        elif t == EventType.INFERENCE_REQUESTED:
            icon = "ai_request"
            title = f"Inference requested by '{event.worker_id or 'Worker'}'"
            description = f"Profile: {p.get('routing_profile', 'BEST_AVAILABLE')} | Tokens: ~{p.get('estimated_input_tokens', 0)}"

        elif t == EventType.INFERENCE_ROUTED:
            icon = "ai_route"
            title = f"OmniRoute selected '{p.get('model_id')}' ({p.get('provider_id')})"
            description = f"Attempt: {p.get('attempt', 1)} | Score: {p.get('score', 0)}"

        elif t == EventType.INFERENCE_COMPLETED:
            icon = "ai_done"
            level = ActivityLevel.SUCCESS
            title = f"Inference completed: '{p.get('model_id')}'"
            description = f"Tokens: {p.get('total_tokens', 0)} | Cost: ${p.get('total_cost', 0):.4f} | Latency: {p.get('latency_ms', 0):.0f}ms"

        elif t == EventType.INFERENCE_FAILED:
            icon = "ai_fail"
            level = ActivityLevel.WARNING
            title = f"Inference attempt failed: '{p.get('model_id')}'"
            description = f"Attempt {p.get('attempt', 1)}: {p.get('error', 'Error')}"

        elif t == EventType.INFERENCE_FALLBACK:
            icon = "ai_fallback"
            level = ActivityLevel.WARNING
            title = f"OmniRoute fallback: '{p.get('failed_model')}' -> '{p.get('fallback_model')}'"
            description = f"Rerouting to candidate provider '{p.get('fallback_provider')}'"

        elif t == EventType.PROVIDER_RATE_LIMITED:
            icon = "ai_rate_limit"
            level = ActivityLevel.WARNING
            title = f"Provider '{p.get('provider_id')}' rate limited"
            description = p.get("reason", "Rate limit reached")

        # Manager & Orchestration Events (Stage 10)
        elif t == EventType.MANAGER_CYCLE_STARTED:
            icon = "manager_cycle"
            title = f"Manager cycle started: {p.get('cycle_id')}"
            description = f"Trigger: {p.get('trigger', 'SCHEDULED')} | Project: {event.project_id}"

        elif t == EventType.MANAGER_CONTEXT_BUILT:
            icon = "manager_context"
            title = f"Manager context assembled ({p.get('items_count', 0)} items, {p.get('total_tokens', 0)} tokens)"
            description = f"Context package: {p.get('package_id')}"

        elif t == EventType.MANAGER_DECISION_CREATED:
            icon = "manager_decision"
            level = ActivityLevel.SUCCESS
            title = f"Manager decision: {p.get('actions_count', 0)} actions planned"
            description = p.get("reasoning_summary", "")

        elif t == EventType.MANAGER_ACTION_ACCEPTED:
            icon = "action_ok"
            title = f"Manager action accepted: {p.get('action_type')}"
            description = f"Target: {p.get('target_id', '')} | {p.get('rationale', '')}"

        elif t == EventType.MANAGER_ACTION_REJECTED:
            icon = "action_rejected"
            level = ActivityLevel.WARNING
            title = f"Manager action rejected: {p.get('action_type')}"
            description = f"Reason: {p.get('reason', 'Validation failed')}"

        elif t == EventType.MANAGER_PLAN_CREATED:
            icon = "plan_created"
            level = ActivityLevel.SUCCESS
            title = f"Project plan v{p.get('version', 1)} created: '{p.get('objective', '')}'"
            description = f"{p.get('tasks_count', 0)} tasks scheduled across {len(p.get('milestones', []))} milestones"

        elif t == EventType.MANAGER_PLAN_UPDATED:
            icon = "plan_updated"
            level = ActivityLevel.SUCCESS
            title = f"Project plan updated to v{p.get('version', 1)}"
            description = p.get("reason", "Plan revised based on execution findings")

        elif t == EventType.MANAGER_REPLAN:
            icon = "replan"
            level = ActivityLevel.WARNING
            title = f"Manager triggered replan (v{p.get('old_version', 1)} -> v{p.get('new_version', 2)})"
            description = f"Reason: {p.get('reason', 'Task failure or requirement divergence')}"

        elif t == EventType.MANAGER_WAITING:
            icon = "manager_wait"
            title = "Manager waiting for active worker tasks to complete"
            description = f"Active tasks: {', '.join(p.get('active_tasks', []))}"

        elif t == EventType.MANAGER_ESCALATED:
            icon = "manager_escalate"
            level = ActivityLevel.ERROR
            title = f"Manager escalated issue: {p.get('title', 'Unresolved blocker')}"
            description = p.get("reason", "Automated recovery limits reached")

        elif t == EventType.MANAGER_USER_INPUT_REQUIRED:
            icon = "user_input"
            level = ActivityLevel.WARNING
            title = f"User input required: {p.get('question', 'Input needed')}"
            description = p.get("reason", "Underspecified requirement or critical decision")

        elif t == EventType.MANAGER_CYCLE_COMPLETED:
            icon = "manager_done"
            level = ActivityLevel.SUCCESS
            title = f"Manager cycle completed ({p.get('actions_executed', 0)} actions executed)"
            description = f"Duration: {p.get('duration_ms', 0):.0f}ms | Status: {p.get('status', 'OK')}"

        elif t == EventType.MANAGER_CYCLE_FAILED:
            icon = "manager_fail"
            level = ActivityLevel.ERROR
            title = f"Manager cycle failed: {p.get('error', 'Cycle error')}"
            description = p.get("reason", "")

        elif t == EventType.MANAGER_STAGNATION_DETECTED:
            icon = "stagnation_alert"
            level = ActivityLevel.WARNING
            title = "Manager stagnation detected: No progress across consecutive cycles"
            description = f"Consecutive idle cycles: {p.get('consecutive_idle_cycles', 0)}"

        # Researcher Specialist Worker Events (Stage 11)
        elif t == EventType.RESEARCH_STARTED:
            icon = "search_start"
            title = f"Researcher started investigation: '{p.get('objective', event.task_id)}'"
            description = f"Mode: {p.get('mode', 'STANDARD')}"

        elif t == EventType.RESEARCH_PLAN_CREATED:
            icon = "plan_research"
            title = f"Research plan formulated with {p.get('question_count', 0)} questions"
            description = f"Steps: {len(p.get('steps', []))}"

        elif t == EventType.RESEARCH_SEARCH_PERFORMED:
            icon = "web_search"
            title = f"Research search executed: '{p.get('query', '')}'"
            description = f"Found {p.get('result_count', 0)} results (Domain filter: {p.get('domain_filter', 'None')})"

        elif t == EventType.RESEARCH_SOURCE_FETCHED:
            icon = "web_fetch"
            title = f"Source fetched: {p.get('title', p.get('url', ''))}"
            description = f"Type: {p.get('source_type', 'UNKNOWN')} | Bytes: {p.get('bytes_fetched', 0)}"

        elif t == EventType.RESEARCH_FINDING_CREATED:
            icon = "finding_add"
            title = f"Finding identified: [{p.get('classification', 'FACT')}] {p.get('claim', '')[:80]}"
            description = f"Confidence: {p.get('confidence', 'SUPPORTED')} | Sources: {len(p.get('source_ids', []))}"

        elif t == EventType.RESEARCH_CONTRADICTION_DETECTED:
            icon = "alert_warning"
            level = ActivityLevel.WARNING
            title = f"Source contradiction detected: {p.get('topic', 'Conflicting claims')}"
            description = f"Claim A: {p.get('claim_a', '')[:60]} vs Claim B: {p.get('claim_b', '')[:60]}"

        elif t == EventType.RESEARCH_KNOWLEDGE_GAP:
            icon = "knowledge_gap"
            level = ActivityLevel.INFO
            title = f"Knowledge gap identified: {p.get('question', p.get('topic', ''))}"
            description = p.get("reason", "Insufficient or conflicting evidence")

        elif t == EventType.RESEARCH_COMPLETED:
            icon = "research_done"
            level = ActivityLevel.SUCCESS
            title = f"Research completed: {p.get('findings_count', 0)} findings, {p.get('sources_count', 0)} sources"
            description = p.get("summary", "")

        elif t == EventType.RESEARCH_FAILED:
            icon = "alert_error"
            level = ActivityLevel.ERROR
            title = f"Research failed: {p.get('error', 'Unknown research failure')}"
            description = p.get("reason", "")

        # Worker Self-Reports
        elif t == EventType.WORKER_PROGRESS_LOGGED:
            icon = "worker_log"
            title = f"[{event.worker_id or 'Worker'}] {p.get('event_type', 'Progress')}"
            description = str(p.get("data", p))

        # Errors
        elif t in (EventType.RUNTIME_ERROR, EventType.EXECUTION_FAILED):
            icon = "alert_error"
            level = ActivityLevel.ERROR
            title = f"Runtime Error: {p.get('error', 'Unknown Error')}"
            description = p.get("message", str(p))

        return ActivityItem(
            id=f"act-{event.event_id}",
            event_id=event.event_id,
            event_type=event.event_type.value,
            timestamp=event.timestamp,
            title=title,
            description=description,
            level=level,
            icon=icon,
            actor=event.worker_id or event.source.value,
            target=event.task_id or event.project_id or event.artifact_id,
            project_id=event.project_id,
            task_id=event.task_id,
            worker_id=event.worker_id,
            artifact_id=event.artifact_id,
            metadata=event.metadata,
        )


def format_event_log_line(event: Event) -> str:
    """Format an event as a compact, colored-friendly CLI debug log line."""
    # Extract time component
    ts_time = event.timestamp.split("T")[-1].replace("Z", "")[:8] if "T" in event.timestamp else event.timestamp
    seq = f"#{event.sequence_number:04d}" if event.sequence_number is not None else "#----"
    
    parts = [f"[{ts_time}]", seq, f"[{event.event_type.value}]"]

    if event.task_id:
        parts.append(f"task={event.task_id}")
    if event.worker_id:
        parts.append(f"worker={event.worker_id}")
    if event.artifact_id:
        parts.append(f"artifact={event.artifact_id}")

    # Add key details from payload
    if "summary" in event.payload:
        parts.append(f"— {event.payload['summary']}")
    elif "reason" in event.payload:
        parts.append(f"— reason: {event.payload['reason']}")
    elif "title" in event.payload:
        parts.append(f"— '{event.payload['title']}'")

    return " ".join(parts)
