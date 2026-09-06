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

        # Programmer Specialist Worker Events (Stage 12)
        elif t == EventType.PROGRAMMER_STARTED:
            icon = "code_start"
            title = f"Programmer started task: '{p.get('objective', event.task_id)}'"
            description = f"Mode: {p.get('mode', 'FEATURE')} | Scope: {len(p.get('allowed_paths', []))} path(s)"

        elif t == EventType.PROGRAMMER_PLAN_CREATED:
            icon = "code_plan"
            title = f"Implementation plan formulated: {p.get('steps_count', 0)} steps"
            description = f"Affected files: {', '.join(p.get('affected_files', [])) or 'Determined during execution'}"

        elif t == EventType.PROGRAMMER_INSPECTION_PERFORMED:
            icon = "code_inspect"
            title = f"Repository inspection: {p.get('target', 'workspace')}"
            description = f"Scanned: {p.get('summary', '')}"

        elif t == EventType.PROGRAMMER_CODE_MODIFIED:
            icon = "code_edit"
            title = f"Code modified: [{p.get('change_type', 'MODIFY')}] {p.get('path', '')}"
            description = f"Diff size: ~{p.get('diff_lines', 0)} lines | {p.get('description', '')}"

        elif t == EventType.PROGRAMMER_DIFF_INSPECTED:
            icon = "code_diff"
            title = f"Diff validated: {p.get('files_changed_count', 0)} files changed"
            description = f"Scope valid: {p.get('scope_valid', True)}"

        elif t == EventType.PROGRAMMER_TEST_STARTED:
            icon = "test_run"
            title = f"Running test command: {p.get('command', 'tests')}"
            description = f"Context: {p.get('stage', 'post-implementation')}"

        elif t == EventType.PROGRAMMER_TEST_COMPLETED:
            passed = p.get('passed', False)
            icon = "test_pass" if passed else "test_fail"
            level = ActivityLevel.SUCCESS if passed else ActivityLevel.WARNING
            title = f"Tests {'PASSED' if passed else 'FAILED'}: {p.get('command', '')}"
            description = f"Exit code: {p.get('exit_code', 0)} in {p.get('duration_ms', 0):.0f}ms"

        elif t == EventType.PROGRAMMER_BUILD_STARTED:
            icon = "build_start"
            title = f"Running build validation: {p.get('command', 'build')}"
            description = f"Target: {p.get('target', 'project')}"

        elif t == EventType.PROGRAMMER_BUILD_COMPLETED:
            passed = p.get('passed', False)
            icon = "build_pass" if passed else "build_fail"
            level = ActivityLevel.SUCCESS if passed else ActivityLevel.ERROR
            title = f"Build {'PASSED' if passed else 'FAILED'}: {p.get('command', '')}"
            description = f"Exit code: {p.get('exit_code', 0)}"

        elif t == EventType.PROGRAMMER_SELF_REVIEW_COMPLETED:
            icon = "code_review"
            title = "Programmer self-review completed"
            description = f"Checks passed: {p.get('checks_passed', True)} | Regression risk: {p.get('regression_risk', 'LOW')}"

        elif t == EventType.PROGRAMMER_REQUESTED:
            icon = "code_request"
            title = f"Programmer requested for task: '{p.get('objective', event.task_id)}'"
            description = f"Work Order: {p.get('work_order_id', 'N/A')}"

        elif t == EventType.PROGRAMMER_BLOCKED:
            icon = "code_blocked"
            level = ActivityLevel.WARNING
            title = f"Programmer blocked: {p.get('reason', 'Execution blocked')}"
            description = p.get("blocker_id", "")

        elif t == EventType.PROGRAMMER_COMPLETED:
            icon = "code_done"
            level = ActivityLevel.SUCCESS
            title = f"Implementation completed: {p.get('files_changed_count', 0)} files changed"
            description = p.get("summary", "")

        elif t == EventType.PROGRAMMER_FAILED:
            icon = "alert_error"
            level = ActivityLevel.ERROR
            title = f"Implementation failed: {p.get('error', 'Unknown programming failure')}"
            description = p.get("reason", "")

        elif t == EventType.PROGRAMMER_CANCELLED:
            icon = "code_cancelled"
            level = ActivityLevel.INFO
            title = f"Programmer cancelled: {p.get('reason', 'Task cancelled')}"
            description = f"Requested by: {p.get('requested_by', 'MANAGER')}"

        # Tester Specialist Worker Events (Stage 13)
        elif t == EventType.TESTER_STARTED:
            icon = "tester_start"
            title = f"Tester started evaluation: '{p.get('objective', event.task_id)}'"
            description = f"Categories: {', '.join(p.get('categories', []))}"

        elif t == EventType.TEST_PLAN_CREATED:
            icon = "test_plan"
            title = f"Test strategy created ({p.get('requirements_count', 0)} requirements tracked)"
            description = p.get("strategy_summary", "")

        elif t == EventType.TEST_STARTED:
            icon = "test_run"
            title = f"Executing test suite: {p.get('command', 'tests')}"
            description = f"Category: {p.get('category', 'UNIT')}"

        elif t == EventType.TEST_COMPLETED:
            passed = p.get('passed', False)
            icon = "test_pass" if passed else "test_fail"
            level = ActivityLevel.SUCCESS if passed else ActivityLevel.WARNING
            title = f"Test suite {'PASSED' if passed else 'FAILED'}: {p.get('command', '')}"
            description = f"Exit code: {p.get('exit_code', 0)} ({p.get('passed_count', 0)}/{p.get('total_count', 0)} passed)"

        elif t == EventType.TEST_FAILED:
            icon = "test_fail"
            level = ActivityLevel.WARNING
            title = f"Test failed: {p.get('test_name', 'Test')}"
            description = f"Error: {p.get('error_message', '')}"

        elif t == EventType.DEFECT_DETECTED:
            icon = "defect"
            level = ActivityLevel.ERROR if p.get('severity') in ('CRITICAL', 'HIGH') else ActivityLevel.WARNING
            title = f"Defect [{p.get('severity', 'MEDIUM')}]: {p.get('title', 'Defect identified')}"
            description = f"Requirement: {p.get('affected_requirement', '')} | Cause: {p.get('suspected_cause', 'Unknown')}"

        elif t == EventType.REGRESSION_DETECTED:
            icon = "regression"
            level = ActivityLevel.ERROR
            title = f"Regression detected: {p.get('test_name', 'Test broke against baseline')}"
            description = f"Baseline status: PASSED -> Current: FAILED | {p.get('notes', '')}"

        elif t == EventType.UI_TEST_STARTED:
            icon = "ui_test"
            title = f"Starting visual UI / OCR validation: {p.get('target', 'interface')}"
            description = f"Tool: {p.get('tool_id', 'screenshot')}"

        elif t == EventType.UI_TEST_COMPLETED:
            passed = p.get('passed', False)
            icon = "ui_pass" if passed else "ui_fail"
            level = ActivityLevel.SUCCESS if passed else ActivityLevel.WARNING
            title = f"UI / OCR validation {'PASSED' if passed else 'FAILED'}"
            description = p.get('findings', '')

        elif t == EventType.TESTER_COMPLETED:
            icon = "tester_done"
            level = ActivityLevel.SUCCESS
            title = f"QA evaluation completed: {p.get('status', 'VERIFIED')}"
            description = f"Requirements: {p.get('verified_count', 0)}/{p.get('total_requirements', 0)} verified | Defects: {p.get('defects_count', 0)}"

        elif t == EventType.TESTER_FAILED:
            icon = "alert_error"
            level = ActivityLevel.ERROR
            title = f"Tester failed: {p.get('error', 'Execution error')}"
            description = p.get("reason", "")

        elif t == EventType.TESTER_BLOCKED:
            icon = "tester_blocked"
            level = ActivityLevel.WARNING
            title = f"Tester blocked: {p.get('reason', 'Missing environment or tools')}"
            description = p.get("details", "")

        # Workforce Workflow & Collaboration Events (Stage 14)
        elif t == EventType.WORKFLOW_CREATED:
            icon = "workflow_create"
            title = f"Workflow created: '{p.get('title', 'Workflow')}'"
            description = f"ID: {p.get('workflow_id')} | Objective: {p.get('objective', '')}"

        elif t == EventType.WORKFLOW_STATUS_CHANGED:
            icon = "workflow_status"
            title = f"Workflow state: {p.get('old_status')} -> {p.get('new_status')}"
            description = p.get("reason", "")

        elif t == EventType.WORKFLOW_HANDOFF_EXECUTED:
            icon = "handoff"
            level = ActivityLevel.SUCCESS
            title = f"Handoff: [{p.get('source_worker')}] -> [{p.get('destination_worker')}]"
            description = f"Artifacts: {len(p.get('artifacts', []))} | Evidence: {len(p.get('evidence', []))}"

        elif t == EventType.WORKFLOW_STEP_COMPLETED:
            icon = "step_done"
            level = ActivityLevel.SUCCESS
            title = f"Workflow step completed: Task '{p.get('task_id')}' by {p.get('worker_id')}"
            description = p.get("summary", "")

        elif t == EventType.WORKFLOW_RETRY_TRIGGERED:
            icon = "retry"
            level = ActivityLevel.WARNING
            title = f"Task retry triggered (attempt {p.get('attempt', 1)})"
            description = f"Reason: {p.get('reason', 'Transient error')}"

        elif t == EventType.WORKFLOW_REASSIGNED:
            icon = "reassign"
            title = f"Task reassigned: [{p.get('old_worker')}] -> [{p.get('new_worker')}]"
            description = f"Reason: {p.get('reason', '')}"

        elif t == EventType.WORKFLOW_BLOCKED:
            icon = "workflow_blocked"
            level = ActivityLevel.WARNING
            title = f"Workflow blocked: {p.get('blocker', 'Unresolved issue')}"
            description = p.get("details", "")

        elif t == EventType.WORKFLOW_PAUSED:
            icon = "workflow_pause"
            level = ActivityLevel.WARNING
            title = f"Workflow paused: {p.get('reason', 'User or policy pause')}"
            description = f"Workflow: {p.get('workflow_id')}"

        elif t == EventType.WORKFLOW_RESUMED:
            icon = "workflow_resume"
            level = ActivityLevel.SUCCESS
            title = f"Workflow resumed"
            description = f"Workflow: {p.get('workflow_id')}"

        elif t == EventType.WORKFLOW_APPROVAL_REQUESTED:
            icon = "approval_req"
            level = ActivityLevel.WARNING
            title = f"Workflow approval required: {p.get('action', 'Action')}"
            description = f"Risk: {p.get('risk_level', 'HIGH')} | {p.get('reason', '')}"

        elif t == EventType.WORKFLOW_APPROVED:
            icon = "approval_ok"
            level = ActivityLevel.SUCCESS
            title = f"Workflow action approved by user: {p.get('action')}"
            description = f"Approver: {p.get('approved_by', 'User')}"

        elif t == EventType.WORKFLOW_REJECTED:
            icon = "approval_no"
            level = ActivityLevel.WARNING
            title = f"Workflow action rejected: {p.get('action')}"
            description = f"Reason: {p.get('reason', 'User rejected')}"

        elif t == EventType.WORKFLOW_CANCELLED:
            icon = "workflow_cancel"
            level = ActivityLevel.WARNING
            title = f"Workflow cancelled by {p.get('cancelled_by', 'User')}"
            description = p.get("reason", "")

        elif t == EventType.WORKFLOW_STAGNATION_DETECTED:
            icon = "stagnation_alert"
            level = ActivityLevel.WARNING
            title = "Workflow stagnation detected: Repeated identical non-progress loop"
            description = f"Iterations: {p.get('iterations', 0)} | Escalating to Manager"

        elif t == EventType.WORKFLOW_COMPLETED:
            icon = "workflow_done"
            level = ActivityLevel.SUCCESS
            title = f"Workflow completed successfully: '{p.get('title', '')}'"
            description = f"Tasks completed: {p.get('completed_tasks_count', 0)}"

        elif t == EventType.WORKFLOW_FAILED:
            icon = "alert_error"
            level = ActivityLevel.ERROR
            title = f"Workflow failed: {p.get('error', 'Workflow error')}"
            description = p.get("reason", "")

        # Human-in-the-Loop & Autonomy Control Events (Stage 15)
        elif t == EventType.AUTONOMY_POLICY_CREATED:
            icon = "policy_create"
            title = f"Autonomy policy v{p.get('version', 1)} established: {p.get('autonomy_level', 'BALANCED')}"
            description = f"Project: {event.project_id}"

        elif t == EventType.AUTONOMY_POLICY_UPDATED:
            icon = "policy_update"
            level = ActivityLevel.SUCCESS
            title = f"Autonomy policy updated: {p.get('old_level')} -> {p.get('new_level')}"
            description = f"Actor: {p.get('actor', 'User')}"

        elif t == EventType.APPROVAL_REQUESTED:
            icon = "approval_wait"
            level = ActivityLevel.WARNING
            title = f"Approval requested: {p.get('action', 'Action')}"
            description = f"Risk: {p.get('risk_level', 'HIGH')} | Scope: {p.get('scope', 'action')}"

        elif t == EventType.APPROVAL_GRANTED:
            icon = "approval_ok"
            level = ActivityLevel.SUCCESS
            title = f"Approval granted: {p.get('action')}"
            description = f"Approver: {p.get('approved_by', 'User')}"

        elif t == EventType.APPROVAL_REJECTED:
            icon = "approval_no"
            level = ActivityLevel.WARNING
            title = f"Approval rejected: {p.get('action')}"
            description = f"Reason: {p.get('reason', '')}"

        elif t == EventType.APPROVAL_EXPIRED:
            icon = "alert_warning"
            level = ActivityLevel.WARNING
            title = f"Approval expired for request: {p.get('approval_id')}"
            description = "New approval required to proceed"

        elif t == EventType.USER_INPUT_REQUESTED:
            icon = "user_input"
            level = ActivityLevel.WARNING
            title = f"User clarification needed: {p.get('question', '')}"
            description = p.get("context", "")

        elif t == EventType.USER_INPUT_RECEIVED:
            icon = "user_response"
            level = ActivityLevel.SUCCESS
            title = f"User clarification provided"
            description = f"Answer: {p.get('answer', '')}"

        elif t == EventType.DECISION_REQUESTED:
            icon = "decision_req"
            level = ActivityLevel.WARNING
            title = f"User decision requested: '{p.get('title', 'Choice')}'"
            description = f"Options: {', '.join(p.get('options', []))}"

        elif t == EventType.DECISION_RECEIVED:
            icon = "decision_ok"
            level = ActivityLevel.SUCCESS
            title = f"User decision recorded: '{p.get('chosen_option', '')}'"
            description = f"Decision ID: {p.get('decision_id')}"

        elif t == EventType.EMERGENCY_STOP_ACTIVATED:
            icon = "emergency_stop"
            level = ActivityLevel.ERROR
            title = f"EMERGENCY STOP ACTIVATED by {p.get('actor', 'User')}"
            description = f"Reason: {p.get('reason', 'Immediate safety halt')}"

        elif t == EventType.EMERGENCY_STOP_CLEARED:
            icon = "emergency_clear"
            level = ActivityLevel.WARNING
            title = f"Emergency stop cleared by {p.get('actor', 'User')}"
            description = "Workforce execution may now be resumed manually"

        elif t == EventType.ACTION_BLOCKED_BY_POLICY:
            icon = "policy_block"
            level = ActivityLevel.ERROR
            title = f"Action blocked by policy: {p.get('action')}"
            description = f"Rule: {p.get('matched_rule', '')} | Risk: {p.get('risk_level', 'CRITICAL')}"

        elif t == EventType.ACTION_ALLOWED_BY_POLICY:
            icon = "policy_allow"
            title = f"Action allowed by policy: {p.get('action')}"
            description = f"Risk: {p.get('risk_level', 'LOW')}"

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
