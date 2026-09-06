from __future__ import annotations

import unittest
import uuid

from core.models import Evidence as RuntimeEvidence, WorkerOutput
from core.programmer.contracts.acceptance_result import AcceptanceCriterionResult
from core.programmer.contracts.command_record import CommandExecutionRecord
from core.programmer.contracts.evidence import ProgrammerEvidence, compute_sha256
from core.programmer.contracts.identifiers import (
    RESULT_ID_PREFIX,
    new_execution_id,
    new_result_id,
    new_work_order_id,
)
from core.programmer.contracts.result import ProgrammerResult
from core.programmer.contracts.test_record import TestResultRecord
from core.programmer.errors import ProgrammerLineageError
from core.programmer.types import (
    AcceptanceStatus,
    ProgrammerEvidenceType,
    ProgrammerResultStatus,
)


class TestProgrammerResultAndEvidence(unittest.TestCase):
    """
    Unit tests for ProgrammerResult and ProgrammerEvidence (Programmer V1, Step 1.3).
    Validates:
    - Successful, partial, blocked, and failed results
    - Files created, changed, and deleted separation
    - Command and test execution audit records
    - Concrete evidence capturing actual outputs (not LLM confidence)
    - Acceptance criterion results with PASS, FAIL, and NOT_VERIFIED
    - Implementation completed but acceptance criteria not fully verified state
    - Cryptographic provenance and runtime evidence bridging
    - Complete dictionary serialization roundtrip
    """

    def test_successful_result_full_evidence(self):
        """Verify an evidence-oriented successful result with passing tests and verified acceptance criteria."""
        cmd_rec = CommandExecutionRecord(
            command="pytest tests/unit/test_oauth.py",
            status="SUCCESS",
            exit_code=0,
            duration_ms=450.2,
            output_ref="logs/test_oauth_output.log",
            output_snippet="12 passed, 0 failed in 0.45s",
        )
        self.assertTrue(cmd_rec.passed)

        test_rec = TestResultRecord(
            command="pytest tests/unit/test_oauth.py",
            passed=True,
            exit_code=0,
            duration_ms=450.2,
            tests_passed=12,
            tests_failed=0,
            tests_skipped=0,
            output_ref="reports/test_report.xml",
        )
        self.assertEqual(test_rec.total_tests, 12)

        ev_test = ProgrammerEvidence(
            evidence_id="pevid-test-01",
            evidence_type=ProgrammerEvidenceType.TEST_RESULT,
            source="pytest",
            execution_id="pexec-100",
            work_order_id="pwo-100",
            summary="12 passed, 0 failed in 0.45s",
            artifact_ref="logs/test_oauth_output.log",
            provenance={"command": "pytest tests/unit/test_oauth.py", "exit_code": 0},
        )

        ev_diff = ProgrammerEvidence(
            evidence_id="pevid-diff-01",
            evidence_type=ProgrammerEvidenceType.DIFF,
            source="git diff",
            execution_id="pexec-100",
            work_order_id="pwo-100",
            summary="+85 lines in src/auth/oauth.py, +45 lines in tests/unit/test_oauth.py",
            artifact_ref="diffs/oauth_implementation.patch",
        )

        ac_res = AcceptanceCriterionResult(
            criterion_id="ac-oauth-01",
            description="OAuth2 callback endpoint returns valid JWT token",
            status=AcceptanceStatus.PASS,
            evidence_ids=[ev_test.evidence_id, ev_diff.evidence_id],
            message="Verified by 12 unit tests covering Google and GitHub providers",
        )

        result = ProgrammerResult(
            result_id="pres-success-01",
            execution_id="pexec-100",
            work_order_id="pwo-100",
            task_id="task-100",
            project_id="proj-100",
            correlation_id="corr-100",
            status=ProgrammerResultStatus.COMPLETED,
            summary="OAuth2 callback handler implemented and verified.",
            files_created=["src/auth/oauth.py"],
            files_changed=["src/auth/__init__.py"],
            files_deleted=[],
            diff_summary="+85 lines, -2 lines across 2 files",
            commands_executed=[cmd_rec],
            test_results=[test_rec],
            acceptance_results=[ac_res],
            artifacts=["diffs/oauth_implementation.patch"],
            evidence=[ev_test, ev_diff],
        )

        self.assertTrue(result.is_success())
        self.assertFalse(result.is_partial())
        self.assertFalse(result.is_blocked())
        self.assertFalse(result.is_failed())
        self.assertTrue(result.is_acceptance_fully_verified())
        self.assertEqual(len(result.evidence_ids), 2)
        self.assertIn("pevid-test-01", result.evidence_ids)
        self.assertIn("pevid-diff-01", result.evidence_ids)

        # WorkerOutput bridge
        wo = result.to_worker_output()
        self.assertIsInstance(wo, WorkerOutput)
        self.assertTrue(wo.success)
        self.assertEqual(wo.metadata["files_created"], ["src/auth/oauth.py"])
        self.assertTrue(wo.metadata["acceptance_fully_verified"])

    def test_partial_result(self):
        """Verify representation of partially completed work with unfulfilled criteria."""
        ac_pass = AcceptanceCriterionResult(
            criterion_id="ac-01",
            description="Backend endpoint exists",
            status=AcceptanceStatus.PASS,
            evidence_ids=["pevid-01"],
        )
        ac_unverified = AcceptanceCriterionResult(
            criterion_id="ac-02",
            description="End-to-end frontend integration verified",
            status=AcceptanceStatus.NOT_VERIFIED,
            message="Frontend UI tests not executed due to time budget limit",
        )

        result = ProgrammerResult(
            result_id=new_result_id(),
            execution_id=new_execution_id(),
            work_order_id=new_work_order_id(),
            task_id="task-partial",
            project_id="proj-1",
            correlation_id="corr-1",
            status=ProgrammerResultStatus.PARTIAL,
            summary="Backend endpoint created; UI integration deferred.",
            files_created=["src/api/auth.py"],
            acceptance_results=[ac_pass, ac_unverified],
            risks=["Frontend UI requires separate verification run"],
        )

        self.assertFalse(result.is_success())
        self.assertTrue(result.is_partial())
        self.assertFalse(result.is_acceptance_fully_verified())
        self.assertEqual(len(result.risks), 1)

    def test_blocked_result(self):
        """Verify blocked result with explicit material blocker escalation."""
        blocker = "Database port 5432 unreachable: connection refused"
        result = ProgrammerResult(
            result_id=new_result_id(),
            execution_id=new_execution_id(),
            work_order_id=new_work_order_id(),
            task_id="task-blocked",
            project_id="proj-1",
            correlation_id="corr-1",
            status=ProgrammerResultStatus.BLOCKED,
            summary="Blocked attempting migration against test database.",
            blockers=[blocker],
            escalations=["Requesting database credentials or running service container"],
        )

        self.assertTrue(result.is_blocked())
        self.assertFalse(result.is_success())
        self.assertEqual(result.blockers, [blocker])
        self.assertEqual(result.material_blockers, [blocker])  # synced alias
        self.assertEqual(len(result.escalations), 1)

        wo = result.to_worker_output()
        self.assertFalse(wo.success)
        self.assertEqual(wo.error_message, blocker)

    def test_failed_result(self):
        """Verify failed result tracking test failures, exit codes, and regression summaries."""
        cmd_rec = CommandExecutionRecord(
            command="pytest tests/unit",
            status="FAILED",
            exit_code=1,
            duration_ms=320.0,
            output_snippet="FAILED tests/unit/test_auth.py::test_login - AssertionError",
        )
        test_rec = TestResultRecord(
            command="pytest tests/unit",
            passed=False,
            exit_code=1,
            duration_ms=320.0,
            tests_passed=10,
            tests_failed=2,
            failure_summary="AssertionError: Expected 200 OK, got 401 Unauthorized",
        )

        result = ProgrammerResult(
            result_id=new_result_id(),
            execution_id=new_execution_id(),
            work_order_id=new_work_order_id(),
            task_id="task-failed",
            project_id="proj-1",
            correlation_id="corr-1",
            status=ProgrammerResultStatus.FAILED,
            summary="Unit tests failed after refactoring auth handler.",
            commands_executed=[cmd_rec],
            test_results=[test_rec],
            acceptance_results=[
                AcceptanceCriterionResult(
                    criterion_id="ac-tests",
                    description="All existing unit tests pass",
                    status=AcceptanceStatus.FAIL,
                    message="2 tests failed with AssertionError",
                )
            ],
            risks=["Authentication handler regression detected"],
        )

        self.assertTrue(result.is_failed())
        self.assertFalse(result.is_success())
        self.assertFalse(result.is_acceptance_fully_verified())
        self.assertEqual(result.test_results[0].tests_failed, 2)
        self.assertEqual(result.acceptance_results[0].status, AcceptanceStatus.FAIL)

    def test_implementation_completed_but_acceptance_not_fully_verified(self):
        """
        Crucial architectural requirement:
        Verify a result representing implementation completed, but acceptance criteria NOT fully verified.
        (e.g., waiting for external verification layer in Stage 7/Phase 2).
        """
        ac_not_verified = AcceptanceCriterionResult(
            criterion_id="ac-e2e",
            description="End-to-end integration test passes in staging environment",
            status=AcceptanceStatus.NOT_VERIFIED,
            message="Requires staging cluster credentials unavailable to worker",
        )

        result = ProgrammerResult(
            result_id=new_result_id(),
            execution_id=new_execution_id(),
            work_order_id=new_work_order_id(),
            task_id="task-100",
            project_id="proj-100",
            correlation_id="corr-100",
            status=ProgrammerResultStatus.COMPLETED,  # Worker completed its assignment
            summary="Code implementation completed; staging verification pending.",
            files_changed=["src/handler.py"],
            acceptance_results=[ac_not_verified],
        )

        # Worker completed its implementation
        self.assertTrue(result.is_success())
        self.assertEqual(result.status, ProgrammerResultStatus.COMPLETED)
        # BUT acceptance criteria are NOT fully verified
        self.assertFalse(result.is_acceptance_fully_verified())
        self.assertEqual(result.acceptance_results[0].status, AcceptanceStatus.NOT_VERIFIED)

    def test_file_change_categorization(self):
        """Verify strict categorization of created, modified, and deleted files."""
        result = ProgrammerResult(
            result_id=new_result_id(),
            execution_id=new_execution_id(),
            work_order_id=new_work_order_id(),
            task_id="task-1",
            project_id="proj-1",
            correlation_id="corr-1",
            files_created=["src/new_service.py", "tests/test_new_service.py"],
            files_changed=["src/main.py", "pyproject.toml"],
            files_deleted=["src/deprecated_helper.py"],
            diff_summary="+140 lines, -28 lines across 5 files",
        )

        self.assertEqual(len(result.files_created), 2)
        self.assertEqual(len(result.files_changed), 2)
        self.assertEqual(len(result.files_deleted), 1)
        self.assertEqual(result.diff_summary, "+140 lines, -28 lines across 5 files")

    def test_command_records_avoid_stdout_bloat(self):
        """Verify command execution records use output references and snippets without raw stdout bloat."""
        rec = CommandExecutionRecord(
            command="cargo build --release",
            status="SUCCESS",
            exit_code=0,
            duration_ms=3420.5,
            output_ref="artifacts/build_cargo.log",
            output_snippet="Compiling 48 crates; Finished release [optimized] target(s) in 3.42s",
        )
        d = rec.to_dict()
        self.assertEqual(d["output_ref"], "artifacts/build_cargo.log")
        self.assertNotIn("raw_stdout", d)  # No raw unbounded stdout field

        restored = CommandExecutionRecord.from_dict(d)
        self.assertEqual(restored.command, "cargo build --release")
        self.assertEqual(restored.duration_ms, 3420.5)

    def test_evidence_provenance_and_runtime_bridge(self):
        """Verify ProgrammerEvidence checksum generation and bridge to core.models.Evidence."""
        ev = ProgrammerEvidence(
            evidence_id="pevid-lint-01",
            evidence_type=ProgrammerEvidenceType.LINT_RESULT,
            source="flake8",
            execution_id="pexec-99",
            work_order_id="pwo-99",
            summary="0 errors, 0 warnings across 14 checked files",
            artifact_ref="logs/flake8.txt",
            provenance={"tool": "flake8", "version": "6.1.0"},
        )
        self.assertTrue(len(ev.checksum) == 64)

        runtime_ev = ev.to_runtime_evidence(task_id="task-99")
        self.assertIsInstance(runtime_ev, RuntimeEvidence)
        self.assertEqual(runtime_ev.id, "pevid-lint-01")
        self.assertEqual(runtime_ev.task_id, "task-99")
        self.assertEqual(runtime_ev.evidence_type, "PROGRAMMER_LINT_RESULT")
        self.assertEqual(runtime_ev.checksum, ev.checksum)
        self.assertIn("flake8", runtime_ev.data)

    def test_serialization_fidelity_roundtrip(self):
        """Verify full dictionary serialization roundtrip of ProgrammerResult with all nested components."""
        cmd_rec = CommandExecutionRecord(command="npm test", status="SUCCESS", exit_code=0, duration_ms=1200.0)
        test_rec = TestResultRecord(command="npm test", passed=True, exit_code=0, tests_passed=8)
        ac_rec = AcceptanceCriterionResult(criterion_id="ac-1", description="Tests pass", status=AcceptanceStatus.PASS)
        ev_rec = ProgrammerEvidence(
            evidence_id="pevid-1",
            evidence_type=ProgrammerEvidenceType.COMMAND_EXECUTION,
            source="npm",
            execution_id="pexec-1",
            work_order_id="pwo-1",
            summary="Tests passed",
        )

        result = ProgrammerResult(
            result_id="pres-rt-01",
            execution_id="pexec-1",
            work_order_id="pwo-1",
            task_id="task-1",
            project_id="proj-1",
            correlation_id="corr-1",
            status=ProgrammerResultStatus.COMPLETED,
            summary="All tasks complete.",
            files_changed=["package.json"],
            files_created=["index.js"],
            files_deleted=[],
            diff_summary="Unified diff summary",
            commands_executed=[cmd_rec],
            test_results=[test_rec],
            acceptance_results=[ac_rec],
            evidence=[ev_rec],
            blockers=["blocker-1"],
            escalations=["esc-1"],
            risks=["risk-1"],
            artifacts=["dist/bundle.js"],
            timestamps={"started_at": "2026-09-06T12:00:00Z", "completed_at": "2026-09-06T12:02:00Z"},
            metadata={"run_id": "run-42"},
        )

        d = result.to_dict()
        restored = ProgrammerResult.from_dict(d)

        self.assertEqual(restored.result_id, "pres-rt-01")
        self.assertEqual(restored.status, ProgrammerResultStatus.COMPLETED)
        self.assertEqual(restored.summary, "All tasks complete.")
        self.assertEqual(restored.files_changed, ["package.json"])
        self.assertEqual(restored.files_created, ["index.js"])
        self.assertEqual(len(restored.commands_executed), 1)
        self.assertEqual(restored.commands_executed[0].command, "npm test")
        self.assertEqual(len(restored.test_results), 1)
        self.assertEqual(restored.test_results[0].tests_passed, 8)
        self.assertEqual(len(restored.acceptance_results), 1)
        self.assertEqual(restored.acceptance_results[0].status, AcceptanceStatus.PASS)
        self.assertEqual(len(restored.evidence), 1)
        self.assertEqual(restored.evidence[0].evidence_id, "pevid-1")
        self.assertEqual(restored.blockers, ["blocker-1"])
        self.assertEqual(restored.escalations, ["esc-1"])
        self.assertEqual(restored.risks, ["risk-1"])
        self.assertEqual(restored.artifacts, ["dist/bundle.js"])
        self.assertEqual(restored.timestamps["started_at"], "2026-09-06T12:00:00Z")
        self.assertEqual(restored.metadata["run_id"], "run-42")


if __name__ == "__main__":
    unittest.main()
