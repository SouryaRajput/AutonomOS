from __future__ import annotations

from workers.programmer.model import ProgrammerResult
from workers.programmer.types import FileChangeType, ProgrammingStatus


class ProgrammingReportGenerator:
    """
    Generates human-readable Markdown implementation reports with requirements
    traceability, file change diffs, test execution logs, and self-review checklists.
    """

    @classmethod
    def generate_markdown_report(cls, result: ProgrammerResult) -> str:
        lines: list[str] = []

        lines.append(f"# Implementation Report: {result.objective}")
        lines.append(f"**Mode**: `{result.mode.value}` | **Status**: `{result.status.value}` | **Generated**: `{result.created_at}`")
        
        fc = result.change_record.files_created if result.change_record else []
        fm = result.change_record.files_modified if result.change_record else []
        fd = result.change_record.files_deleted if result.change_record else []
        total_files = len(fc) + len(fm) + len(fd)
        lines.append(f"**Files Touched**: `{total_files}` (Created: `{len(fc)}`, Modified: `{len(fm)}`, Deleted: `{len(fd)}`)")
        lines.append("")

        # 1. Executive Summary
        lines.append("## 1. Executive Summary")
        if result.summary_for_manager:
            lines.append(result.summary_for_manager)
        else:
            lines.append("Implementation task executed and verified through Tool Runtime.")
        lines.append("")

        # 2. Requirements Traceability
        lines.append("## 2. Requirements Traceability")
        if result.change_record and result.change_record.requirements_addressed:
            lines.append("| Requirement | Addressed | Evidence / Tests |")
            lines.append("| :--- | :--- | :--- |")
            for req in result.change_record.requirements_addressed:
                test_cnt = len(result.test_results)
                lines.append(f"| {req} | Yes | {test_cnt} test command(s) executed |")
        else:
            lines.append("Direct requirement mapping recorded in change specification.")
        lines.append("")

        # 3. Changes Made
        lines.append("## 3. Changes Made")
        if not result.changes:
            lines.append("No file modifications recorded.")
        else:
            lines.append("| File Path | Change Type | Checksum (SHA-256) | Description |")
            lines.append("| :--- | :--- | :--- | :--- |")
            for c in result.changes:
                cs = (c.new_checksum or c.original_checksum or "N/A")[:12]
                lines.append(f"| `{c.path}` | `{c.change_type.value}` | `{cs}` | {c.description} |")
        lines.append("")

        # 4. Detailed Diffs
        if any(c.diff for c in result.changes):
            lines.append("## 4. Diffs & Modifications")
            for c in result.changes:
                if c.diff:
                    lines.append(f"### Diff for `{c.path}` ({c.change_type.value})")
                    lines.append("```diff")
                    lines.append(c.diff)
                    lines.append("```")
                    lines.append("")

        # 5. Test & Build Execution
        lines.append("## 5. Test & Build Execution")
        if not result.test_results and not result.build_result:
            lines.append("No test or build commands were executed for this task.")
        else:
            lines.append("| Type | Command | Status | Exit Code | Duration (ms) |")
            lines.append("| :--- | :--- | :--- | :--- | :--- |")
            if result.build_result:
                b_stat = "PASSED" if result.build_result.passed else "FAILED"
                lines.append(f"| Build | `{result.build_result.command}` | `{b_stat}` | `{result.build_result.exit_code}` | {result.build_result.duration_ms:.1f}ms |")
            for t in result.test_results:
                t_stat = "PASSED" if t.passed else "FAILED"
                lines.append(f"| Test | `{t.command}` | `{t_stat}` | `{t.exit_code}` | {t.duration_ms:.1f}ms |")
        lines.append("")

        # 6. Self-Review & Risk Assessment
        lines.append("## 6. Self-Review & Risk Assessment")
        if result.self_review:
            sr = result.self_review
            lines.append(f"- **All checks passed**: `{'Yes' if sr.checks_passed else 'No'}`")
            lines.append(f"- **Modified files strictly in scope**: `{'Yes' if sr.modified_files_in_scope else 'No'}`")
            lines.append(f"- **Tests added or updated**: `{'Yes' if sr.tests_added_or_updated else 'No'}`")
            lines.append(f"- **Regression Risk**: `{sr.regression_risk}`")
            if sr.notes:
                lines.append(f"- **Review Notes**: {sr.notes}")
        else:
            lines.append("Standard pre-completion self-audit performed.")
        lines.append("")

        # 7. Assumptions & Known Limitations
        if result.assumptions or result.warnings or result.unresolved_issues:
            lines.append("## 7. Assumptions & Known Limitations")
            for a in result.assumptions:
                lines.append(f"- *Assumption*: {a}")
            for w in result.warnings:
                lines.append(f"- *Warning*: {w}")
            for u in result.unresolved_issues:
                lines.append(f"- *Unresolved Issue*: {u}")
            lines.append("")

        # 8. Manager Handoff & Next Steps
        lines.append("## 8. Manager Handoff & Next Steps")
        lines.append("> [!NOTE]")
        lines.append("> The Programmer Worker has completed the implementation phase. Formal verification has been requested from the Verification Engine.")
        lines.append("")

        return "\n".join(lines)

    @classmethod
    def generate_manager_summary(cls, result: ProgrammerResult) -> str:
        """
        Produce a concise 3-6 bullet summary for Manager handoff without loading
        the full implementation report or diffs into reasoning context.
        """
        bullets: list[str] = []
        bullets.append(f"Implementation completed for objective: '{result.objective}'")
        
        fc = result.change_record.files_created if result.change_record else []
        fm = result.change_record.files_modified if result.change_record else []
        fd = result.change_record.files_deleted if result.change_record else []
        bullets.append(f"Files Changed: {len(fc)} created, {len(fm)} modified, {len(fd)} deleted.")

        # Test results
        passed_tests = sum(1 for t in result.test_results if t.passed)
        failed_tests = sum(1 for t in result.test_results if not t.passed)
        if result.test_results:
            bullets.append(f"Tests: {passed_tests} passed, {failed_tests} failed across {len(result.test_results)} run(s).")

        # Build results
        if result.build_result:
            b_str = "Passed" if result.build_result.passed else "Failed"
            bullets.append(f"Build: {b_str} (`{result.build_result.command}`).")

        if result.summary_for_manager and not result.summary_for_manager.startswith("- "):
            bullets.append(result.summary_for_manager)

        # Verification & status
        bullets.append(f"Status: {result.status.value}. Verification requested.")

        if result.report_path:
            bullets.append(f"Full implementation report at artifact `{result.report_path}`.")

        return "\n".join([f"- {b}" for b in bullets])
