from __future__ import annotations

from typing import Any, Optional

from core.workflow.model import WorkflowSnapshot, WorkforceWorkflow


class WorkflowSummaryGenerator:
    """
    Generates compact, human-readable and Manager-optimized Markdown summary artifacts.
    """

    @classmethod
    def generate_markdown_summary(
        cls,
        workflow: WorkforceWorkflow,
        snapshot: WorkflowSnapshot,
    ) -> str:
        lines: list[str] = [
            f"# Workforce Workflow Execution Summary",
            f"",
            f"**Workflow ID**: `{workflow.id}`  ",
            f"**Project ID**: `{workflow.project_id}`  ",
            f"**Current Status**: `{workflow.status.value}`  ",
            f"**Iteration**: `{snapshot.iteration_count}/{workflow.budget.max_iterations}`  ",
            f"",
            f"---",
            f"",
            f"## 1. Workflow Objective",
            f"{workflow.objective}",
            f"",
            f"---",
            f"",
            f"## 2. Task Graph Progress",
            f"",
            f"- **Active Tasks** ({len(snapshot.active_tasks)}): {', '.join(f'`{t}`' for t in snapshot.active_tasks) if snapshot.active_tasks else 'None'}",
            f"- **Completed Tasks** ({len(snapshot.completed_tasks)}): {', '.join(f'`{t}`' for t in snapshot.completed_tasks) if snapshot.completed_tasks else 'None'}",
            f"- **Blocked Tasks** ({len(snapshot.blocked_tasks)}): {', '.join(f'`{t}`' for t in snapshot.blocked_tasks) if snapshot.blocked_tasks else 'None'}",
            f"- **Failed Tasks** ({len(snapshot.failed_tasks)}): {', '.join(f'`{t}`' for t in snapshot.failed_tasks) if snapshot.failed_tasks else 'None'}",
            f"",
            f"---",
            f"",
            f"## 3. Worker Assignments",
            f"",
        ]

        if snapshot.worker_assignments:
            for tid, wid in snapshot.worker_assignments.items():
                lines.append(f"- Task `{tid}` assigned to worker `{wid}`")
        else:
            lines.append("No active worker assignments.")

        lines.extend([
            f"",
            f"---",
            f"",
            f"## 4. Open Defects & Feedback Loop",
            f"",
        ])

        if snapshot.open_defects:
            for d in snapshot.open_defects:
                lines.append(f"- **Defect `{d.defect_id}`** (Status: `{d.status}`): Originating Task `{d.originating_task_id}` by `{d.originating_worker_id}` -> Fix: `{d.fix_task_id or 'Pending'}`")
        else:
            lines.append("No active or unresolved defects.")

        lines.extend([
            f"",
            f"---",
            f"",
            f"## 5. Recent Handoffs",
            f"",
        ])

        if snapshot.recent_handoffs:
            for h in snapshot.recent_handoffs[-3:]:
                lines.append(f"- **[{h.source_worker}] -> [{h.destination_worker or 'Manager'}]** (Task `{h.source_task_id}`): {h.summary}")
        else:
            lines.append("No recent handoffs recorded.")

        lines.append("")
        return "\n".join(lines)
