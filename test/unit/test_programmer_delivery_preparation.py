from __future__ import annotations

import os
import unittest

from core.programmer.contracts.acceptance_result import AcceptanceCriterionResult
from core.programmer.contracts.delivery import (
    DeliveryPackage,
    DeliveryPreparer,
)
from core.programmer.contracts.diff_verifier import (
    RenamedFile,
    UnauthorizedChange,
)
from core.programmer.contracts.git_change_set import ChangeSet
from core.programmer.contracts.git_model import (
    GitExecutionContext,
    GitRepository,
    GitRevision,
)
from core.programmer.contracts.git_verification import (
    RepositoryAnomaly,
    RepositoryStateVerification,
)
from core.programmer.contracts.identifiers import (
    DELIVERY_ID_PREFIX,
    new_change_set_id,
    new_delivery_id,
    new_execution_id,
    new_git_repository_id,
    new_git_revision_id,
    new_repo_state_verification_id,
    new_verification_evidence_id,
    new_work_order_id,
    validate_delivery_id,
)
from core.programmer.contracts.test_record import TestResultRecord
from core.programmer.contracts.verification import (
    VerificationEvidence,
    VerificationSummary,
)
from core.programmer.contracts.work_order import ProgrammerWorkOrder
from core.programmer.errors import (
    InvalidProgrammerIdError,
    ProgrammerLineageError,
    ProgrammerValidationError,
)
from core.programmer.types import (
    AcceptanceStatus,
    ChangeSetStatus,
    GitIsolationMode,
    GitRepositoryState,
    ManagerDisposition,
    PathBoundaryScope,
    ProgrammerResultStatus,
    RepositoryAnomalyType,
    VerificationEvidenceSourceType,
    VerificationStatus,
    VerificationSummaryStatus,
)


class TestProgrammerDeliveryPreparation(unittest.TestCase):
    """
    Unit test suite for PROGRAMMER V1 — PHASE 6.6: Delivery Preparation.
    Validates DeliveryPackage construction, answering the 10 audit questions,
    disposition recommendations, Manager disposition recording, lineage, and serialization.
    """

    def setUp(self) -> None:
        self.project_id = "proj-delivery-test"
        self.work_order_id = new_work_order_id()
        self.execution_id = new_execution_id()
        self.repository_id = new_git_repository_id()
        self.commit_sha_base = "1111111111111111111111111111111111111111"
        self.commit_sha_res = "2222222222222222222222222222222222222222"

        self.base_rev = GitRevision(
            revision_id=new_git_revision_id(),
            commit_hash=self.commit_sha_base,
            branch="main",
        )
        self.res_rev = GitRevision(
            revision_id=new_git_revision_id(),
            commit_hash=self.commit_sha_res,
            branch="workorder/feature",
        )

        self.execution_context = GitExecutionContext(
            repository_id=self.repository_id,
            execution_id=self.execution_id,
            work_order_id=self.work_order_id,
            project_id=self.project_id,
            base_revision=self.base_rev,
            resulting_revision=self.res_rev,
            workspace_path="/tmp/worktrees/delivery-wt",
            isolation_mode=GitIsolationMode.WORKTREE,
            branch="workorder/feature",
        )

        self.work_order = ProgrammerWorkOrder(
            work_order_id=self.work_order_id,
            manager_task_id="mtask-delivery-1",
            project_id=self.project_id,
            correlation_id="corr-delivery-1",
            objective="Implement core payment verification module",
            allowed_paths=["src/payment/"],
            writable_paths=["src/payment/"],
            read_only_paths=["config/"],
            forbidden_paths=[".env", "secrets/"],
        )

        self.evidence_item = VerificationEvidence(
            evidence_id=new_verification_evidence_id(),
            execution_id=self.execution_id,
            work_order_id=self.work_order_id,
            source_type=VerificationEvidenceSourceType.GIT,
            source_reference="git_verification:test",
            description="Verified payment module changes against test suite",
            is_agent_claim=False,
            data={"test": "pass"},
        )

        self.preparer = DeliveryPreparer()

    def test_canonical_identifier_generation_and_validation(self):
        """Verify canonical pdel- prefix generation and validation."""
        del_id = new_delivery_id()
        self.assertTrue(del_id.startswith(DELIVERY_ID_PREFIX))
        validate_delivery_id(del_id)

        with self.assertRaises(InvalidProgrammerIdError):
            validate_delivery_id("invalid-id")
        with self.assertRaises(InvalidProgrammerIdError):
            validate_delivery_id("vrepo-12345678")

    def test_verified_delivery(self):
        """Verify that a fully verified implementation creates an ACCEPT-recommended package and answers 10 questions."""
        change_set = ChangeSet(
            change_set_id=new_change_set_id(),
            execution_id=self.execution_id,
            work_order_id=self.work_order_id,
            base_revision=self.base_rev,
            resulting_revision=self.res_rev,
            files_changed=["src/payment/engine.py"],
            files_created=["src/payment/models.py"],
            status=ChangeSetStatus.COMMITTED,
            commit_references=[self.res_rev],
        )

        repo_verification = RepositoryStateVerification(
            verification_id=new_repo_state_verification_id(),
            execution_id=self.execution_id,
            work_order_id=self.work_order_id,
            repository_id=self.repository_id,
            workspace_path="/tmp/worktrees/delivery-wt",
            base_revision=self.base_rev,
            current_revision=self.res_rev,
            changed_files=["src/payment/engine.py"],
            created_files=["src/payment/models.py"],
            is_clean=True,
            verification_status=VerificationStatus.PASS,
            evidence=[self.evidence_item],
        )

        ac_results = [
            AcceptanceCriterionResult(
                criterion_id="ac-1",
                description="Payment engine executes transactions",
                status=AcceptanceStatus.PASS,
            ),
            AcceptanceCriterionResult(
                criterion_id="ac-2",
                description="Payment models parse payloads",
                status=AcceptanceStatus.PASS,
            ),
        ]

        test_results = [
            TestResultRecord(
                command="pytest test/unit/test_payment.py",
                passed=True,
                tests_passed=10,
                tests_failed=0,
            )
        ]

        pkg = self.preparer.prepare(
            execution_context=self.execution_context,
            work_order=self.work_order,
            change_set=change_set,
            repository_verification=repo_verification,
            acceptance_results=ac_results,
            test_results=test_results,
            evidence=[self.evidence_item],
        )

        self.assertEqual(pkg.recommended_disposition, ManagerDisposition.ACCEPT)
        self.assertIsNone(pkg.manager_disposition)

        # Verify the 10 core audit answers
        # 1. What was requested?
        self.assertIn("Implement core payment verification module", pkg.what_was_requested())
        # 2. What changed?
        changed = pkg.what_changed()
        self.assertIn("src/payment/engine.py", changed["files_changed"])
        self.assertIn("src/payment/models.py", changed["files_created"])
        # 3. What revision did we start from?
        self.assertEqual(pkg.start_revision(), self.commit_sha_base)
        # 4. What revision contains the result?
        self.assertEqual(pkg.result_revision(), self.commit_sha_res)
        # 5. What tests were run?
        tests_summary = pkg.tests_run_summary()
        self.assertEqual(tests_summary["tests_passed"], 10)
        self.assertEqual(tests_summary["tests_failed"], 0)
        self.assertTrue(tests_summary["all_passed"])
        # 6. What acceptance criteria passed?
        ac_passed = pkg.acceptance_criteria_passed()
        self.assertEqual(len(ac_passed), 2)
        # 7. What remains unverified?
        self.assertEqual(len(pkg.what_remains_unverified()), 0)
        # 8. Are there unauthorized changes?
        self.assertFalse(pkg.has_unauthorized_changes())
        # 9. Are there known risks?
        self.assertEqual(len(pkg.known_risks()), 0)
        # 10. What action should Manager take next?
        rec_act = pkg.recommended_action()
        self.assertEqual(rec_act["disposition"], "ACCEPT")

        # Map to ProgrammerResult
        res = pkg.to_programmer_result()
        self.assertEqual(res.status, ProgrammerResultStatus.SUCCESS)

    def test_partially_verified_delivery(self):
        """Verify delivery with unverified criteria recommends REQUEST_CHANGES and lists unverified items."""
        ac_results = [
            AcceptanceCriterionResult(
                criterion_id="ac-1",
                description="Core payment executes",
                status=AcceptanceStatus.PASS,
            ),
            AcceptanceCriterionResult(
                criterion_id="ac-2",
                description="Refund processing verified",
                status=AcceptanceStatus.NOT_VERIFIED,
            ),
        ]

        pkg = self.preparer.prepare(
            execution_context=self.execution_context,
            work_order=self.work_order,
            acceptance_results=ac_results,
            evidence=[self.evidence_item],
        )

        self.assertEqual(pkg.recommended_disposition, ManagerDisposition.REQUEST_CHANGES)
        unverified = pkg.what_remains_unverified()
        self.assertTrue(any("Refund processing verified" in u for u in unverified))

        res = pkg.to_programmer_result()
        self.assertEqual(res.status, ProgrammerResultStatus.PARTIAL)

    def test_failed_delivery(self):
        """Verify delivery with failing tests or failed acceptance criteria recommends REJECT."""
        test_results = [
            TestResultRecord(
                command="pytest test/test_fail.py",
                passed=False,
                tests_passed=5,
                tests_failed=2,
            )
        ]

        pkg = self.preparer.prepare(
            execution_context=self.execution_context,
            work_order=self.work_order,
            test_results=test_results,
            evidence=[self.evidence_item],
        )

        self.assertEqual(pkg.recommended_disposition, ManagerDisposition.REJECT)
        self.assertIn("Failing Test Suite", pkg.what_remains_unverified()[0])

        res = pkg.to_programmer_result()
        self.assertEqual(res.status, ProgrammerResultStatus.FAILED)

    def test_missing_evidence_validation_failure(self):
        """Verify DeliveryPackage marked ACCEPT cannot have empty evidence."""
        with self.assertRaises(ProgrammerValidationError):
            DeliveryPackage(
                delivery_id=new_delivery_id(),
                execution_id=self.execution_id,
                work_order_id=self.work_order_id,
                base_revision=self.base_rev,
                recommended_disposition=ManagerDisposition.ACCEPT,
                evidence=[],  # Missing authoritative evidence
            )

    def test_unauthorized_changes_forces_reject(self):
        """Verify unauthorized changes strictly result in REJECT recommendation."""
        unauth = UnauthorizedChange(
            path="secrets/key.pem",
            change_type="MODIFIED",
            scope=PathBoundaryScope.FORBIDDEN,
            reason="Forbidden file modification",
        )

        repo_verification = RepositoryStateVerification(
            verification_id=new_repo_state_verification_id(),
            execution_id=self.execution_id,
            work_order_id=self.work_order_id,
            repository_id=self.repository_id,
            base_revision=self.base_rev,
            unauthorized_changes=[unauth],
            verification_status=VerificationStatus.FAIL,
            evidence=[self.evidence_item],
        )

        pkg = self.preparer.prepare(
            execution_context=self.execution_context,
            work_order=self.work_order,
            repository_verification=repo_verification,
            evidence=[self.evidence_item],
        )

        self.assertEqual(pkg.recommended_disposition, ManagerDisposition.REJECT)
        self.assertTrue(pkg.has_unauthorized_changes())
        self.assertEqual(len(pkg.unauthorized_changes_details()), 1)

        # Invariant: manual construction with ACCEPT and unauthorized changes must fail validation
        with self.assertRaises(ProgrammerValidationError):
            DeliveryPackage(
                delivery_id=new_delivery_id(),
                execution_id=self.execution_id,
                work_order_id=self.work_order_id,
                base_revision=self.base_rev,
                verification_summary=repo_verification,
                recommended_disposition=ManagerDisposition.ACCEPT,
                evidence=[self.evidence_item],
            )

    def test_manager_disposition_recording(self):
        """Verify Manager reviews package and records decision without mutating Git state."""
        pkg = self.preparer.prepare(
            execution_context=self.execution_context,
            work_order=self.work_order,
            evidence=[self.evidence_item],
        )

        self.assertIsNone(pkg.manager_disposition)

        # Manager decides to request changes
        pkg.record_manager_disposition(
            disposition=ManagerDisposition.REQUEST_CHANGES,
            notes="Please add regression test for edge cases before merge.",
        )
        self.assertEqual(pkg.manager_disposition, ManagerDisposition.REQUEST_CHANGES)
        self.assertEqual(pkg.manager_notes, "Please add regression test for edge cases before merge.")

        # Manager later decides to accept
        pkg.record_manager_disposition(
            disposition=ManagerDisposition.ACCEPT,
            notes="Approved after manual inspection.",
        )
        self.assertEqual(pkg.manager_disposition, ManagerDisposition.ACCEPT)

    def test_lineage_validation(self):
        """Verify mismatched execution_id, work_order_id, or project_id raises ProgrammerLineageError."""
        # WorkOrder ID mismatch in context
        mismatched_context = GitExecutionContext(
            repository_id=self.repository_id,
            execution_id=self.execution_id,
            work_order_id=new_work_order_id(),
            project_id=self.project_id,
            base_revision=self.base_rev,
            workspace_path="/tmp/worktrees/delivery-wt",
        )

        with self.assertRaises(ProgrammerLineageError):
            self.preparer.prepare(
                execution_context=mismatched_context,
                work_order=self.work_order,
            )

        # Evidence lineage mismatch
        foreign_evidence = VerificationEvidence(
            evidence_id=new_verification_evidence_id(),
            execution_id=new_execution_id(),  # foreign execution
            work_order_id=self.work_order_id,
            source_type=VerificationEvidenceSourceType.GIT,
            source_reference="test",
            description="Foreign evidence",
            is_agent_claim=False,
        )

        with self.assertRaises(ProgrammerLineageError):
            DeliveryPackage(
                delivery_id=new_delivery_id(),
                execution_id=self.execution_id,
                work_order_id=self.work_order_id,
                base_revision=self.base_rev,
                evidence=[foreign_evidence],
            )

    def test_serialization_roundtrip(self):
        """Verify full to_dict, from_dict, and to_json roundtrip fidelity."""
        pkg = self.preparer.prepare(
            execution_context=self.execution_context,
            work_order=self.work_order,
            evidence=[self.evidence_item],
            blockers=["Dependency service unavailable"],
            risks=["High load risk"],
        )

        d = pkg.to_dict()
        rehydrated = DeliveryPackage.from_dict(d)

        self.assertEqual(rehydrated.delivery_id, pkg.delivery_id)
        self.assertEqual(rehydrated.execution_id, pkg.execution_id)
        self.assertEqual(rehydrated.work_order_id, pkg.work_order_id)
        self.assertEqual(rehydrated.recommended_disposition, pkg.recommended_disposition)
        self.assertEqual(rehydrated.blockers, pkg.blockers)
        self.assertEqual(len(rehydrated.evidence), len(pkg.evidence))

        json_str = pkg.to_json()
        self.assertIn(pkg.delivery_id, json_str)
        self.assertIn(self.execution_id, json_str)


if __name__ == "__main__":
    unittest.main()
