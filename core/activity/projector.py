from __future__ import annotations

import logging
import re
import time
from typing import Any, Optional

from core.activity.model import (
    ActivityStatus,
    CommandLineItem,
    ExecutionActivity,
    FileActivityItem,
    FileOperationType,
    WorkerActivityItem,
)
from core.events.model import Event
from core.events.types import EventType
from core.models import utc_now

logger = logging.getLogger("AutonomOS.ActivityProjector")

# Secret redaction patterns
REDACT_PATTERNS = [
    (re.compile(r'(--(?:api[-_]?key|key|token|password|secret|auth)\s+[^\s]+)', re.IGNORECASE), r'--key [REDACTED]'),
    (re.compile(r'((?:api[-_]?key|key|token|password|secret|auth)=[^\s]+)', re.IGNORECASE), r'key=[REDACTED]'),
    (re.compile(r'(Bearer\s+[A-Za-z0-9_\-\.]+)', re.IGNORECASE), r'Bearer [REDACTED]'),
    (re.compile(r'(Authorization:\s*[^\s]+)', re.IGNORECASE), r'Authorization: [REDACTED]'),
]


def redact_command(cmd: str) -> str:
    """Redact passwords, API keys, tokens, and authorization headers from command strings."""
    if not cmd:
        return ""
    result = cmd
    for pattern, replacement in REDACT_PATTERNS:
        result = pattern.sub(replacement, result)
    return result


class WorkforceActivityProjector:
    """
    Authoritative Transformation Boundary:
    Technical Events (Event Channel) -> Human-Visible Presentation State (Activity Channel).
    
    Guarantees:
    1. Pure isolation: No internal tool payloads/JSON leak into conversational text.
    2. Real-time accuracy: Reflects actual live states (RUNNING, WAITING, STALLED, COMPLETED, FAILED).
    3. Meaningful aggregation: Collapses low-level loops into coherent milestone summaries.
    4. Multi-task scoping: Maintains separate, non-overlapping activities per task/correlation ID.
    5. Robustness: Gracefully handles out-of-order and duplicate events.
    """

    def __init__(self):
        self._activities: dict[str, ExecutionActivity] = {}
        self._seen_event_ids: set[str] = set()

    def get_activity(self, activity_key: str) -> Optional[ExecutionActivity]:
        return self._activities.get(activity_key)

    def list_activities(self, project_id: Optional[str] = None) -> list[ExecutionActivity]:
        if not project_id:
            return list(self._activities.values())
        return [a for a in self._activities.values() if a.project_id == project_id]

    def get_or_create_activity(
        self,
        project_id: str,
        task_id: str = "",
        correlation_id: str = "",
        worker_id: str = "",
        title: str = "Workforce Execution",
    ) -> ExecutionActivity:
        key = correlation_id or task_id or (f"{project_id}-main" if project_id else "global-main")
        if key not in self._activities:
            now = utc_now()
            self._activities[key] = ExecutionActivity(
                activity_id=f"act-{key}",
                project_id=project_id,
                task_id=task_id,
                correlation_id=correlation_id,
                worker_id=worker_id,
                title=title,
                status=ActivityStatus.PENDING,
                start_time=now,
                current_action="Initialized",
                is_live=True,
            )
        return self._activities[key]

    def project_event(self, event: Event) -> ExecutionActivity:
        """Project a single technical Event into the appropriate ExecutionActivity state."""
        # 1. Idempotency dedup
        if event.event_id in self._seen_event_ids:
            key = event.correlation_id or event.task_id or (f"{event.project_id}-main" if event.project_id else "global-main")
            return self._activities.get(key) or self.get_or_create_activity(event.project_id, event.task_id, event.correlation_id, event.worker_id)
        self._seen_event_ids.add(event.event_id)

        # 2. Resolve activity scope
        act = self.get_or_create_activity(
            project_id=event.project_id,
            task_id=event.task_id or "",
            correlation_id=event.correlation_id or "",
            worker_id=event.worker_id or "",
        )

        p = event.payload or {}
        t = event.event_type

        # Ensure start time is recorded
        if not act.start_time:
            act.start_time = event.timestamp

        # Update worker identity if specialized
        if event.worker_id:
            act.worker_id = event.worker_id
            if "researcher" in event.worker_id.lower():
                act.worker_type = "Researcher"
                act.title = "Researcher — Running"
            elif "programmer" in event.worker_id.lower():
                act.worker_type = "Programmer"
                act.title = "Programmer — Active"
            elif "tester" in event.worker_id.lower():
                act.worker_type = "Tester"
                act.title = "Tester — Active"
            elif "crawler" in event.worker_id.lower():
                act.worker_type = "Crawler"

        # --- Dispatch Event Processing ---
        self._apply_event_to_activity(act, event, t, p)

        return act

    def _apply_event_to_activity(
        self,
        act: ExecutionActivity,
        event: Event,
        t: EventType,
        p: dict[str, Any],
    ) -> None:
        # =====================================================================
        # 1. Task Lifecycle Events
        # =====================================================================
        if t == EventType.TASK_STARTED:
            act.status = ActivityStatus.RUNNING
            act.is_live = True
            act.current_action = p.get("objective") or f"Starting task: {p.get('title', act.task_id)}"
            self._record_worker(act, worker_id=event.worker_id or "worker.specialist", status="RUNNING", action=act.current_action)

        elif t == EventType.TASK_COMPLETED:
            act.status = ActivityStatus.COMPLETED
            act.is_live = False
            act.end_time = event.timestamp
            summary = p.get("summary") or "Task completed successfully"
            self._add_completed_action(act, f"✓ {summary}")
            act.current_action = "Execution completed"
            self._mark_workers_completed(act)

        elif t == EventType.TASK_FAILED or t == EventType.EXECUTION_FAILED:
            act.status = ActivityStatus.FAILED
            act.is_live = False
            act.end_time = event.timestamp
            reason = p.get("reason") or p.get("error") or "Task execution encountered a failure"
            act.error_summary = self._clean_error_message(reason)
            act.current_action = f"Failed: {act.error_summary}"

        elif t == EventType.TASK_CANCELLED:
            act.status = ActivityStatus.CANCELLED
            act.is_live = False
            act.end_time = event.timestamp
            act.current_action = "Execution cancelled by user"

        elif t == EventType.TASK_BLOCKED:
            act.status = ActivityStatus.WAITING
            act.waiting_reason = p.get("reason") or "Waiting for prerequisite tasks"
            act.current_action = f"Waiting: {act.waiting_reason}"

        # =====================================================================
        # 2. Worker Lifecycle Events & Crawlers
        # =====================================================================
        elif t == EventType.WORKER_STARTED:
            w_id = event.worker_id or p.get("worker_id", "worker")
            w_role = p.get("role", "Specialist")
            act.status = ActivityStatus.RUNNING
            self._record_worker(act, worker_id=w_id, role=w_role, status="RUNNING", action="Active")

        elif t == EventType.WORKER_FINISHED or t == EventType.WORKER_BECAME_IDLE:
            w_id = event.worker_id or p.get("worker_id", "worker")
            self._update_worker_status(act, worker_id=w_id, status="COMPLETED")

        elif t == EventType.WORKER_FAILED:
            w_id = event.worker_id or p.get("worker_id", "worker")
            reason = p.get("reason") or "Worker encountered an error"
            self._update_worker_status(act, worker_id=w_id, status="FAILED", error=self._clean_error_message(reason))

        # =====================================================================
        # 3. Manager Cycles & Planning Events
        # =====================================================================
        elif t == EventType.MANAGER_CYCLE_STARTED:
            act.status = ActivityStatus.RUNNING
            act.current_action = "Analyzing workspace and formulating execution plan"

        elif t == EventType.MANAGER_PLAN_CREATED:
            task_count = p.get("tasks_count", 0)
            self._add_completed_action(act, f"✓ Formulated work plan ({task_count} tasks)")
            act.current_action = "Assigning specialist workers"

        elif t == EventType.MANAGER_WAITING:
            act.status = ActivityStatus.WAITING
            reason = p.get("reason") or "Waiting for active specialists to finish"
            act.waiting_reason = reason
            act.current_action = f"Waiting: {reason}"

        elif t == EventType.MANAGER_STAGNATION_DETECTED:
            act.status = ActivityStatus.STALLED
            act.current_action = "Stalled — progress not detected across consecutive cycles"

        elif t == EventType.MANAGER_CYCLE_COMPLETED:
            if p.get("completed"):
                act.status = ActivityStatus.COMPLETED
                act.is_live = False
                act.end_time = event.timestamp
                act.current_action = "Orchestration plan completed"

        # =====================================================================
        # 4. Researcher Specialist & Crawler Events
        # =====================================================================
        elif t == EventType.RESEARCH_STARTED:
            act.worker_type = "Researcher"
            act.title = "Researcher — Running"
            act.status = ActivityStatus.RUNNING
            act.current_action = "Inspecting project structure & domain context"
            self._record_worker(act, worker_id="worker.researcher", name="Researcher", role="Specialist", status="RUNNING")

        elif t == EventType.RESEARCH_PLAN_CREATED:
            q_count = p.get("questions_count", len(p.get("questions", [])))
            self._add_completed_action(act, f"✓ Identified {q_count} research questions")
            act.current_action = "Allocating search and doc crawlers"

        elif t == EventType.RESEARCH_SEARCH_PERFORMED:
            query = p.get("query", "")
            crawler_id = event.worker_id or p.get("crawler_id", "crawler.search")
            count = p.get("result_count", 0)
            act.metrics["sources_collected"] = act.metrics.get("sources_collected", 0) + count
            act.current_action = f"Web search: \"{query[:40]}\""
            self._record_worker(
                act,
                worker_id=crawler_id,
                name=f"Crawler #{self._get_crawler_num(act, crawler_id)}",
                role="Web Search",
                status="RUNNING",
                action=f"Searching: \"{query[:30]}...\"",
            )

        elif t == EventType.RESEARCH_SOURCE_FETCHED:
            source_url = p.get("url") or p.get("source_id", "source")
            act.metrics["sources_collected"] = act.metrics.get("sources_collected", 0) + 1
            act.current_action = f"Evaluating source: {source_url[:40]}"

        elif t == EventType.RESEARCH_FINDING_CREATED:
            act.metrics["evidence_items"] = act.metrics.get("evidence_items", 0) + 1
            answered = act.metrics.get("evidence_items", 0)
            total_q = act.metrics.get("total_questions", 6)
            act.metrics["questions_answered"] = f"{min(answered, total_q)}/{total_q}"
            act.current_action = "Evaluating evidence & cross-checking contradictions"

        elif t == EventType.RESEARCH_CONTRADICTION_DETECTED:
            self._add_completed_action(act, f"⚠️ Cross-checked contradiction: {p.get('statement', '')[:50]}")

        elif t == EventType.RESEARCH_COMPLETED:
            act.status = ActivityStatus.COMPLETED
            act.is_live = False
            act.end_time = event.timestamp
            sources = act.metrics.get("sources_collected", 0)
            evidence = act.metrics.get("evidence_items", 0)
            self._add_completed_action(act, f"✓ Research completed ({sources} sources, {evidence} evidence evaluated)")
            act.current_action = "Research complete"
            self._mark_workers_completed(act)

        elif t == EventType.RESEARCH_FAILED:
            act.status = ActivityStatus.FAILED
            act.is_live = False
            act.end_time = event.timestamp
            err = p.get("reason") or p.get("error") or "Research failed"
            act.error_summary = self._clean_error_message(err)
            act.current_action = f"Research failed: {act.error_summary}"

        # =====================================================================
        # 5. Programmer Specialist Events
        # =====================================================================
        elif t == EventType.PROGRAMMER_STARTED:
            act.worker_type = "Programmer"
            act.title = "Programmer — Active"
            act.status = ActivityStatus.RUNNING
            act.current_action = "Analyzing AST and target code files"
            self._record_worker(act, worker_id="worker.programmer", name="Programmer", role="Specialist", status="RUNNING")

        elif t == EventType.PROGRAMMER_INSPECTION_PERFORMED:
            file_path = p.get("file_path", "")
            if file_path:
                self._record_file_read(act, file_path)
            act.current_action = f"Inspected {file_path or 'files'}"

        elif t == EventType.PROGRAMMER_CODE_MODIFIED:
            file_path = p.get("file_path", "")
            if file_path:
                self._record_file_changed(act, file_path, FileOperationType.MODIFIED)
            self._add_completed_action(act, f"✓ Modified {file_path}")
            act.current_action = "Applying code modifications"

        elif t == EventType.PROGRAMMER_TEST_STARTED:
            act.current_action = "Running regression test suite"

        elif t == EventType.PROGRAMMER_TEST_COMPLETED:
            passed = p.get("passed", True)
            if passed:
                self._add_completed_action(act, "✓ Regression tests passed")
            else:
                self._add_completed_action(act, "❌ Tests failed — triggering rollback")

        elif t == EventType.PROGRAMMER_REQUESTED:
            act.worker_type = "Programmer"
            act.title = "Programmer — Requested"
            act.status = ActivityStatus.PENDING
            act.current_action = f"Work order requested: {p.get('work_order_id', 'N/A')}"
            self._record_worker(act, worker_id="worker.programmer", name="Programmer", role="Specialist", status="REQUESTED")

        elif t == EventType.PROGRAMMER_COMPLETED:
            act.status = ActivityStatus.COMPLETED
            act.is_live = False
            act.end_time = event.timestamp
            self._add_completed_action(act, "✓ Code modifications verified")
            act.current_action = "Implementation complete"

        elif t == EventType.PROGRAMMER_BLOCKED:
            act.status = ActivityStatus.BLOCKED
            act.current_action = f"Blocked: {p.get('reason', 'Execution blocked')}"

        elif t == EventType.PROGRAMMER_FAILED:
            act.status = ActivityStatus.FAILED
            act.is_live = False
            act.end_time = event.timestamp
            act.current_action = f"Failed: {p.get('error', 'Execution failed')}"

        elif t == EventType.PROGRAMMER_CANCELLED:
            act.status = ActivityStatus.CANCELLED
            act.is_live = False
            act.end_time = event.timestamp
            act.current_action = f"Cancelled: {p.get('reason', 'Execution cancelled')}"

        # =====================================================================
        # 6. Tester Specialist Events
        # =====================================================================
        elif t == EventType.TESTER_STARTED:
            act.worker_type = "Tester"
            act.title = "Tester — Active"
            act.status = ActivityStatus.RUNNING
            act.current_action = "Planning QA verification tests"
            self._record_worker(act, worker_id="worker.tester", name="Tester", role="Specialist", status="RUNNING")

        elif t == EventType.TEST_STARTED:
            test_name = p.get("test_name", "test")
            act.current_action = f"Executing test: {test_name}"

        elif t == EventType.TEST_COMPLETED:
            self._add_completed_action(act, f"✓ Test passed: {p.get('test_name', 'test')}")

        elif t == EventType.DEFECT_DETECTED or t == EventType.REGRESSION_DETECTED:
            defect = p.get("description") or p.get("defect_id", "Defect")
            self._add_completed_action(act, f"⚠️ Detected defect: {defect}")

        elif t == EventType.TESTER_COMPLETED:
            act.status = ActivityStatus.COMPLETED
            act.is_live = False
            act.end_time = event.timestamp
            self._add_completed_action(act, "✓ Independent QA verification completed")
            act.current_action = "QA verification complete"

        # =====================================================================
        # 7. Tool Runtime Events (Shell, Filesystem, Web)
        # =====================================================================
        elif t == EventType.TOOL_REQUESTED or t == EventType.TOOL_STARTED:
            tool_id = p.get("tool_id", "")
            args = p.get("arguments", {})

            if "shell" in tool_id or "run_command" in tool_id or "exec" in tool_id:
                raw_cmd = args.get("command") or args.get("cmd") or tool_id
                sanitized = redact_command(raw_cmd)
                act.current_action = f"Running: $ {sanitized[:40]}"
                self._record_command(act, sanitized, is_running=True)

            elif "read_file" in tool_id or "stat_file" in tool_id:
                path = args.get("path") or args.get("file_path", "")
                if path:
                    self._record_file_read(act, path)
                    act.current_action = f"Reading: {path}"

            elif "write_file" in tool_id or "create_file" in tool_id:
                path = args.get("path") or args.get("file_path", "")
                if path:
                    self._record_file_changed(act, path, FileOperationType.CREATED)
                    act.current_action = f"Writing: {path}"

            elif "delete_file" in tool_id:
                path = args.get("path") or args.get("file_path", "")
                if path:
                    self._record_file_changed(act, path, FileOperationType.DELETED)
                    act.current_action = f"Deleting: {path}"

            elif "list_dir" in tool_id or "list_directory" in tool_id:
                path = args.get("path") or args.get("directory", ".")
                act.current_action = f"Scanning directory: {path}"

            elif "web.search" in tool_id or "web_search" in tool_id:
                q = args.get("query", "")
                act.current_action = f"Web search: \"{q[:35]}\""

        elif t == EventType.TOOL_COMPLETED:
            tool_id = p.get("tool_id", "")
            duration = p.get("duration_ms")

            if "shell" in tool_id or "run_command" in tool_id:
                self._complete_last_command(act, exit_code=p.get("exit_code", 0), duration_ms=duration)
            elif "read_file" in tool_id:
                count = len(act.files_read)
                if count % 5 == 0 and count > 0:
                    self._add_completed_action(act, f"✓ Inspected {count} project files")

        elif t == EventType.TOOL_FAILED:
            tool_id = p.get("tool_id", "")
            err = p.get("error", "Tool execution failed")
            if "shell" in tool_id:
                self._complete_last_command(act, exit_code=1, error=err)
            act.current_action = f"Tool failure: {self._clean_error_message(err)}"

        # =====================================================================
        # 8. Provider & Inference Rate Limit Events
        # =====================================================================
        elif t == EventType.PROVIDER_RATE_LIMITED:
            act.status = ActivityStatus.WAITING
            act.waiting_reason = "Inference provider rate-limited — waiting before retry"
            act.current_action = act.waiting_reason

        elif t == EventType.PROVIDER_UNAVAILABLE:
            act.status = ActivityStatus.FAILED
            act.error_summary = f"Provider '{p.get('provider_id', 'LLM')}' unavailable"
            act.current_action = act.error_summary

    # --- Helper Aggregation & Tracking Methods ---

    def _record_command(self, act: ExecutionActivity, cmd: str, is_running: bool = True) -> None:
        clean_cmd = redact_command(cmd)
        # Check if already in list
        for existing in act.commands:
            if existing.command == clean_cmd and existing.is_running:
                return
        item = CommandLineItem(
            command=clean_cmd,
            is_running=is_running,
            timestamp=utc_now(),
        )
        act.commands.append(item)
        act.metrics["commands_run"] = len(act.commands)

    def _complete_last_command(self, act: ExecutionActivity, exit_code: int = 0, duration_ms: Optional[float] = None, error: Optional[str] = None) -> None:
        for cmd in reversed(act.commands):
            if cmd.is_running:
                cmd.is_running = False
                cmd.exit_code = exit_code
                cmd.duration_ms = duration_ms
                if error:
                    cmd.output_preview = self._clean_error_message(error)
                break

    def _record_file_read(self, act: ExecutionActivity, path: str) -> None:
        if path and path not in act.files_read:
            act.files_read.append(path)
            act.metrics["files_read_count"] = len(act.files_read)

    def _record_file_changed(self, act: ExecutionActivity, path: str, op: FileOperationType) -> None:
        if not path:
            return
        # Replace if existing
        for idx, f in enumerate(act.files_changed):
            if f.path == path:
                act.files_changed[idx] = FileActivityItem(path=path, operation=op, timestamp=utc_now())
                return
        act.files_changed.append(FileActivityItem(path=path, operation=op, timestamp=utc_now()))
        act.metrics["files_changed_count"] = len(act.files_changed)

    def _record_worker(
        self,
        act: ExecutionActivity,
        worker_id: str,
        name: str = "Specialist",
        role: str = "Specialist",
        status: str = "RUNNING",
        action: str = "Working",
    ) -> None:
        for w in act.workers:
            if w.worker_id == worker_id:
                w.status = status
                w.current_action = action
                return
        act.workers.append(WorkerActivityItem(
            worker_id=worker_id,
            name=name,
            role=role,
            status=status,
            current_action=action,
            started_at=utc_now(),
        ))
        act.metrics["active_workers"] = len([w for w in act.workers if w.status == "RUNNING"])

    def _update_worker_status(self, act: ExecutionActivity, worker_id: str, status: str, error: Optional[str] = None) -> None:
        for w in act.workers:
            if w.worker_id == worker_id:
                w.status = status
                if error:
                    w.error = error
                if status == "COMPLETED":
                    w.completed_at = utc_now()
                break
        act.metrics["active_workers"] = len([w for w in act.workers if w.status == "RUNNING"])

    def _mark_workers_completed(self, act: ExecutionActivity) -> None:
        for w in act.workers:
            if w.status == "RUNNING":
                w.status = "COMPLETED"
                w.completed_at = utc_now()
        act.metrics["active_workers"] = 0

    def _add_completed_action(self, act: ExecutionActivity, action_str: str) -> None:
        clean = action_str.strip()
        if clean and clean not in act.completed_actions:
            act.completed_actions.append(clean)

    def _get_crawler_num(self, act: ExecutionActivity, crawler_id: str) -> int:
        crawlers = [w for w in act.workers if "crawler" in w.worker_id.lower()]
        for idx, c in enumerate(crawlers, start=1):
            if c.worker_id == crawler_id:
                return idx
        return len(crawlers) + 1

    def _clean_error_message(self, err: str) -> str:
        """Strip raw JSON, tracebacks, HTTP headers into a crisp user-facing explanation."""
        if not err:
            return "An internal error occurred."
        s = str(err)
        # Remove traceback headers
        if "Traceback" in s:
            lines = s.split("\n")
            for line in reversed(lines):
                if line.strip() and not line.strip().startswith("File ") and not line.strip().startswith("Traceback"):
                    s = line.strip()
                    break
        # Remove rate limit raw dump
        if "Rate limit" in s or "429" in s:
            return "Web search / inference provider rate limit exceeded."
        if "timeout" in s.lower():
            return "Operation timed out."
        if len(s) > 120:
            return s[:117] + "..."
        return s
