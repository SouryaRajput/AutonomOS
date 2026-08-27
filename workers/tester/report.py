from __future__ import annotations

import re
from typing import Any

from workers.tester.model import TesterResult
from workers.tester.types import RequirementVerificationStatus, TestExecutionStatus, TesterFinalStatus


class TestingReportGenerator:
    """
    Generates structured, human-readable Markdown test reports
    and concise summaries for Manager handoffs.
    """

    @classmethod
    def generate_markdown_report(cls, result: TesterResult) -> str:
        lines: list[str] = [
            f"# Test & Quality Assurance Report",
            f"",
            f"**Task ID**: `{result.task_id}`  ",
            f"**Project ID**: `{result.project_id}`  ",
            f"**Overall Assessment**: `{result.status.value}`  ",
            f"**Report Generated**: `{result.created_at}`  ",
            f"",
            f"---",
            f"",
            f"## 1. Executive Summary",
            f"",
            f"**Objective**: {result.objective}  ",
            f"**QA Strategy**: {result.plan.strategy_summary}  ",
            f"",
            f"---",
            f"",
            f"## 2. Requirement Traceability Matrix",
            f"",
            f"| Requirement ID | Description | Status | Supporting Tests | Notes |",
            f"|:---|:---|:---|:---|:---|",
        ]

        for req in result.requirement_traces:
            test_str = ", ".join(f"`{t}`" for t in req.test_ids) if req.test_ids else "None"
            status_badge = f"**{req.status.value}**"
            lines.append(f"| `{req.requirement_id}` | {req.description} | {status_badge} | {test_str} | {req.notes} |")

        lines.extend([
            f"",
            f"---",
            f"",
            f"## 3. Test Suites Executed",
            f"",
        ])

        total_tests = sum(s.total for s in result.suites)
        total_passed = sum(s.passed for s in result.suites)
        total_failed = sum(s.failed for s in result.suites)

        lines.append(f"**Total Tests**: `{total_tests}` | **Passed**: `{total_passed}` | **Failed**: `{total_failed}`  ")
        lines.append(f"")

        for s in result.suites:
            lines.append(f"### Suite: `{s.suite_name}`")
            lines.append(f"- **Summary**: `{s.passed}/{s.total}` passed, `{s.failed}` failed, `{s.errors}` errors (in `{s.duration_ms:.0f}ms`)")
            lines.append(f"")
            lines.append(f"| Test ID | Name | Category | Status | Exit Code | Regr? |")
            lines.append(f"|:---|:---|:---|:---|:---|:---|")
            for t in s.test_results:
                regr_badge = "⚠️ **YES**" if t.is_regression else "No"
                lines.append(f"| `{t.test_id}` | `{t.name}` | `{t.category.value}` | `{t.status.value}` | `{t.exit_code}` | {regr_badge} |")
            lines.append(f"")

        if result.defects:
            lines.extend([
                f"---",
                f"",
                f"## 4. Diagnosed Defects & Issues",
                f"",
            ])
            for d in result.defects:
                lines.extend([
                    f"### Defect `{d.defect_id}`: {d.title}",
                    f"- **Severity**: `{d.severity.value}` | **Classification**: `{d.classification.value}`",
                    f"- **Affected Requirement**: {d.affected_requirement}",
                    f"- **Affected Component**: `{d.affected_component}`",
                    f"- **Suspected Root Cause** ({d.root_cause_confidence.value}): {d.suspected_cause}",
                    f"- **Fix Guidance**: {d.suggested_fix_guidance}",
                    f"",
                    f"#### Reproduction Steps",
                    f"```bash",
                    f"# Command",
                    f"{d.reproduction_steps.get('command', '')}",
                    f"",
                    f"# Expected",
                    f"{d.expected_behavior}",
                    f"",
                    f"# Actual",
                    f"{d.actual_behavior}",
                    f"```",
                    f"",
                ])

        if result.regressions:
            lines.extend([
                f"---",
                f"",
                f"## 5. Regressions Detected",
                f"",
            ])
            for r in result.regressions:
                lines.append(f"- ⚠️ **Regression in `{r.name}`**: Broke previously working baseline tests. Output: `{r.error_message}`")
            lines.append(f"")

        lines.extend([
            f"---",
            f"",
            f"## 6. Authoritative Evidence & Artifacts",
            f"",
            f"- **Evidence Records**: {', '.join(f'`{e}`' for e in result.evidence_ids) if result.evidence_ids else 'None'}",
            f"- **Artifacts Registered**: {', '.join(f'`{a}`' for a in result.artifacts) if result.artifacts else 'None'}",
            f"- **Environment Info**: `{result.environment_info}`",
            f"",
        ])

        return "\n".join(lines)

    @classmethod
    def generate_manager_summary(cls, result: TesterResult) -> str:
        total_reqs = len(result.requirement_traces)
        verified_reqs = sum(1 for r in result.requirement_traces if r.status == RequirementVerificationStatus.VERIFIED)
        failed_tests = sum(s.failed for s in result.suites)

        bullets: list[str] = [
            f"- QA Evaluation Status: **{result.status.value}** ({verified_reqs}/{total_reqs} requirements verified)",
            f"- Test Execution: {sum(s.passed for s in result.suites)}/{sum(s.total for s in result.suites)} passed across {len(result.suites)} suite(s)",
        ]

        if result.regressions:
            bullets.append(f"- ⚠️ **REGRESSIONS DETECTED**: {len(result.regressions)} test(s) regressed against baseline")

        if result.defects:
            bullets.append(f"- **Defects Diagnosed**: {len(result.defects)} defect(s) identified (Top severity: {result.defects[0].severity.value})")
            for d in result.defects[:2]:
                bullets.append(f"  * [{d.severity.value}] {d.title} (Suggested fix: {d.suggested_fix_guidance[:100]})")
        else:
            bullets.append("- No defects or regressions discovered")

        if result.report_path:
            bullets.append(f"- Detailed QA Report Artifact: `{result.report_path}`")

        return "\n".join(bullets)
