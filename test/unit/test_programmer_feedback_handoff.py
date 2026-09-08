from __future__ import annotations

import json
import unittest

from core.programmer.contracts.feedback import (
    EngineeringFeedback,
)
from core.programmer.contracts.feedback_adapter import (
    FeedbackToWorkOrderAdapter,
)
from core.programmer.contracts.identifiers import (
    FEEDBACK_ID_PREFIX,
    new_feedback_id,
    new_work_order_id,
    validate_feedback_id,
)
from core.programmer.contracts.research_reference import (
    ResearchEvidenceReference,
)
from core.programmer.contracts.work_order import (
    ProgrammerWorkOrder,
)
from core.programmer.errors import (
    DuplicateFeedbackError,
    InvalidProgrammerIdError,
    ProgrammerValidationError,
    StaleFeedbackError,
    UnrelatedFeedbackError,
)
from core.programmer.types import (
    FeedbackConfidence,
    FeedbackIssueType,
    FeedbackSeverity,
)


class TestProgrammerFeedbackHandoff(unittest.TestCase):
    """Unit tests for Phase 8.5: Cross-Worker Engineering Feedback."""

    def setUp(self) -> None:
        self.base_wo_id = new_work_order_id()
        self.project_id = "proj-fintech-core"
        self.base_work_order = ProgrammerWorkOrder(
            work_order_id=self.base_wo_id,
            manager_task_id="mtask-billing-101",
            project_id=self.project_id,
            correlation_id="corr-billing-flow",
            objective="Implement customer invoice settlement and payment webhooks",
            revision_number=1,
            technical_requirements=[
                "Provide POST /api/v1/settle endpoint",
                "Emit webhook on settlement completion",
            ],
            constraints=[
                "All DB queries must execute within a read-committed transaction",
            ],
            acceptance_criteria=[
                "Settlement endpoint returns 200 OK on valid payload",
            ],
            allowed_paths=["src/billing/", "src/webhooks/"],
            writable_paths=["src/billing/", "src/webhooks/"],
            read_only_paths=["config/settlement.json"],
            forbidden_paths=["secrets/"],
            allowed_commands=["pytest", "ruff check"],
        )

        self.fb_id_1 = new_feedback_id()
        self.fb_id_2 = new_feedback_id()

        self.evidence_item_1 = {
            "evidence_id": "fevid-test-run-01",
            "claim": "Concurrent settlements generate duplicate ledger entries",
            "description": "Integration test concurrent_settle_test.py failed with 2 entries created for same invoice",
            "source_ref": "test/integration/concurrent_settle_test.py:45",
            "confidence": "VERIFIED",
        }
        self.evidence_item_2 = {
            "evidence_id": "fevid-log-trace-02",
            "claim": "Webhook delivery retry loop lacks exponential backoff",
            "description": "Server logs show 5 retry attempts dispatched in 10ms causing rate-limit rejection",
            "source_ref": "logs/webhook_service.log:102",
            "confidence": "SUPPORTED",
        }

    # =========================================================================
    # Scenario 1: Valid feedback processing & advisory suggestions
    # =========================================================================
    def test_01_valid_feedback_processing(self) -> None:
        """Scenario 1: Converts accepted Tester feedback into revised WorkOrder preserving advisory isolation."""
        fb1 = EngineeringFeedback(
            feedback_id=self.fb_id_1,
            source_worker="Tester",
            source_task="ttask-settlement-qa-1",
            target_project=self.project_id,
            related_work_order=self.base_wo_id,
            issue="Concurrent settlements generate duplicate ledger entries",
            issue_type=FeedbackIssueType.BUG,
            severity=FeedbackSeverity.HIGH,
            observed_behavior="Two ledger entries created for the same invoice under 50 concurrent requests",
            expected_behavior="Exactly one ledger entry created; subsequent requests rejected with 409 Conflict",
            suggested_direction="Add database unique constraint on (invoice_id, settlement_id)",
            reproduction_information={"test_command": "pytest test/integration/concurrent_settle_test.py"},
            evidence=[self.evidence_item_1],
        )

        fb2 = EngineeringFeedback(
            feedback_id=self.fb_id_2,
            source_worker="Tester",
            source_task="ttask-settlement-qa-2",
            target_project=self.project_id,
            related_work_order=self.base_wo_id,
            issue="Webhook delivery retry lacks exponential backoff",
            issue_type=FeedbackIssueType.PERFORMANCE,
            severity=FeedbackSeverity.MEDIUM,
            observed_behavior="5 retries fired within 10ms causing remote gateway 429",
            expected_behavior="Retries spaced with exponential backoff (1s, 2s, 4s...)",
            suggested_direction="Use tenacity retry decorator with wait_exponential",
            evidence=[self.evidence_item_2],
        )

        # 1. Transform with advisory suggestions NOT converted to requirements (default)
        revised_wo = FeedbackToWorkOrderAdapter.transform(
            base_work_order=self.base_work_order,
            feedback_items=[fb1, fb2],
            adopt_suggestions_as_requirements=False,
        )

        self.assertIsInstance(revised_wo, ProgrammerWorkOrder)
        self.assertEqual(revised_wo.project_id, self.project_id)
        self.assertEqual(revised_wo.parent_work_order_id, self.base_wo_id)
        self.assertEqual(revised_wo.revision_number, 2)
        self.assertIn("Resolve feedback for", revised_wo.objective)

        # Technical requirements contain the issue and expected behavior, not the suggestion
        reqs = revised_wo.technical_requirements
        self.assertTrue(any("Concurrent settlements generate duplicate ledger entries" in r for r in reqs))
        self.assertTrue(any("Webhook delivery retry lacks exponential backoff" in r for r in reqs))
        # Suggestions must NOT be requirements
        self.assertFalse(any("database unique constraint" in r for r in reqs))
        self.assertFalse(any("tenacity retry decorator" in r for r in reqs))

        # Suggestions are kept in context for Programmer evaluation
        advisory = revised_wo.context.get("feedback_advisory_suggestions", [])
        self.assertEqual(len(advisory), 2)
        self.assertEqual(advisory[0]["feedback_id"], self.fb_id_1)
        self.assertEqual(advisory[0]["status"], "ADVISORY_SUGGESTION")

        # 2. Transform with adopt_suggestions_as_requirements=True (Manager explicit adoption)
        revised_wo_adopted = FeedbackToWorkOrderAdapter.transform(
            base_work_order=self.base_work_order,
            feedback_items=[fb1, fb2],
            adopt_suggestions_as_requirements=True,
        )
        adopted_reqs = revised_wo_adopted.technical_requirements
        self.assertTrue(any("database unique constraint" in r for r in adopted_reqs))
        self.assertTrue(any("tenacity retry decorator" in r for r in adopted_reqs))

    # =========================================================================
    # Scenario 2: Evidence linkage
    # =========================================================================
    def test_02_evidence_linkage(self) -> None:
        """Scenario 2: Attached reproduction evidence is linked as ResearchEvidenceReferences."""
        fb = EngineeringFeedback(
            feedback_id=self.fb_id_1,
            source_worker="Tester",
            source_task="ttask-settlement-qa-1",
            target_project=self.project_id,
            related_work_order=self.base_wo_id,
            issue="Settlement duplicate entry bug",
            observed_behavior="Double charge observed",
            expected_behavior="Single charge",
            evidence=[self.evidence_item_1],
        )

        revised_wo = FeedbackToWorkOrderAdapter.transform(
            base_work_order=self.base_work_order,
            feedback_items=[fb],
        )

        self.assertTrue(len(revised_wo.research_evidence) >= 1)
        ref = next(r for r in revised_wo.research_evidence if r.evidence_id == "fevid-test-run-01")
        self.assertIsInstance(ref, ResearchEvidenceReference)
        self.assertEqual(ref.claim_or_fact, "Concurrent settlements generate duplicate ledger entries")
        self.assertIn("Feedback evidence from Tester", ref.relevance_notes)

        # Context contains the full accepted feedback structure
        accepted_fb = revised_wo.context.get("accepted_feedback", [])
        self.assertEqual(len(accepted_fb), 1)
        self.assertEqual(accepted_fb[0]["feedback_id"], self.fb_id_1)
        self.assertEqual(len(accepted_fb[0]["evidence"]), 1)

    # =========================================================================
    # Scenario 3: Severity handling & prioritization
    # =========================================================================
    def test_03_severity_handling(self) -> None:
        """Scenario 3: Evaluates highest severity and records in context and metadata."""
        fb_low = EngineeringFeedback(
            feedback_id=self.fb_id_1,
            source_worker="Tester",
            source_task="ttask-1",
            target_project=self.project_id,
            related_work_order=self.base_wo_id,
            issue="Minor docstring typo in settle endpoint",
            severity=FeedbackSeverity.LOW,
            observed_behavior="Typo in comment",
            expected_behavior="Correct spelling",
        )
        fb_crit = EngineeringFeedback(
            feedback_id=self.fb_id_2,
            source_worker="Tester",
            source_task="ttask-2",
            target_project=self.project_id,
            related_work_order=self.base_wo_id,
            issue="Settlement SQL injection vulnerability in search query",
            issue_type=FeedbackIssueType.SECURITY,
            severity=FeedbackSeverity.CRITICAL,
            observed_behavior="Raw string interpolation in query",
            expected_behavior="Parameterized query",
        )

        revised_wo = FeedbackToWorkOrderAdapter.transform(
            base_work_order=self.base_work_order,
            feedback_items=[fb_low, fb_crit],
        )

        self.assertEqual(revised_wo.context.get("max_feedback_severity"), FeedbackSeverity.CRITICAL.value)
        self.assertEqual(revised_wo.metadata.get("highest_severity"), FeedbackSeverity.CRITICAL.value)

    # =========================================================================
    # Scenario 4: Unrelated feedback rejection
    # =========================================================================
    def test_04_unrelated_feedback(self) -> None:
        """Scenario 4: Rejects feedback targeting different project or unrelated work order."""
        # 4a: Different project
        fb_bad_project = EngineeringFeedback(
            feedback_id=self.fb_id_1,
            source_worker="Tester",
            source_task="ttask-1",
            target_project="proj-different-ecommerce",
            related_work_order=self.base_wo_id,
            issue="Some issue in different project",
            observed_behavior="Observed",
            expected_behavior="Expected",
        )
        with self.assertRaises(UnrelatedFeedbackError) as cm_proj:
            FeedbackToWorkOrderAdapter.transform(
                base_work_order=self.base_work_order,
                feedback_items=[fb_bad_project],
            )
        self.assertIn("target_project", str(cm_proj.exception))

        # 4b: Different work order ID
        fb_bad_wo = EngineeringFeedback(
            feedback_id=self.fb_id_1,
            source_worker="Tester",
            source_task="ttask-1",
            target_project=self.project_id,
            related_work_order=new_work_order_id(),
            issue="Some issue on different work order",
            observed_behavior="Observed",
            expected_behavior="Expected",
        )
        with self.assertRaises(UnrelatedFeedbackError) as cm_wo:
            FeedbackToWorkOrderAdapter.transform(
                base_work_order=self.base_work_order,
                feedback_items=[fb_bad_wo],
            )
        self.assertIn("related_work_order", str(cm_wo.exception))

    # =========================================================================
    # Scenario 5: Stale feedback rejection
    # =========================================================================
    def test_05_stale_feedback(self) -> None:
        """Scenario 5: Rejects stale feedback referencing superseded work order or obsolete revision."""
        # 5a: Explicit stale work order IDs
        fb = EngineeringFeedback(
            feedback_id=self.fb_id_1,
            source_worker="Tester",
            source_task="ttask-1",
            target_project=self.project_id,
            related_work_order=self.base_wo_id,
            issue="Stale defect reported",
            observed_behavior="Observed",
            expected_behavior="Expected",
        )
        with self.assertRaises(StaleFeedbackError) as cm_stale:
            FeedbackToWorkOrderAdapter.transform(
                base_work_order=self.base_work_order,
                feedback_items=[fb],
                stale_work_order_ids=[self.base_wo_id],
            )
        self.assertIn("superseded", str(cm_stale.exception).lower())

        # 5b: Targeting an obsolete revision number
        base_wo_rev2 = ProgrammerWorkOrder(
            work_order_id=self.base_wo_id,
            manager_task_id="mtask-billing-101",
            project_id=self.project_id,
            correlation_id="corr-billing-flow",
            objective="Settlement revision 2",
            revision_number=2,
            technical_requirements=["Update settlement"],
        )
        fb_obsolete_rev = EngineeringFeedback(
            feedback_id=self.fb_id_1,
            source_worker="Tester",
            source_task="ttask-1",
            target_project=self.project_id,
            related_work_order=self.base_wo_id,
            issue="Feedback against revision 1",
            observed_behavior="Observed",
            expected_behavior="Expected",
            trace={"target_revision_number": 1},
        )
        with self.assertRaises(StaleFeedbackError) as cm_rev:
            FeedbackToWorkOrderAdapter.transform(
                base_work_order=base_wo_rev2,
                feedback_items=[fb_obsolete_rev],
            )
        self.assertIn("targets revision 1", str(cm_rev.exception))

    # =========================================================================
    # Scenario 6: Duplicate feedback rejection
    # =========================================================================
    def test_06_duplicate_feedback(self) -> None:
        """Scenario 6: Rejects duplicate feedback IDs or duplicate issue signatures."""
        fb1 = EngineeringFeedback(
            feedback_id=self.fb_id_1,
            source_worker="Tester",
            source_task="ttask-1",
            target_project=self.project_id,
            related_work_order=self.base_wo_id,
            issue="Payment webhook missing signature",
            observed_behavior="Header X-Signature is None",
            expected_behavior="HMAC SHA256 header present",
        )

        # 6a: Duplicate feedback ID
        fb1_dup_id = EngineeringFeedback(
            feedback_id=self.fb_id_1,
            source_worker="Tester",
            source_task="ttask-2",
            target_project=self.project_id,
            related_work_order=self.base_wo_id,
            issue="Different issue text",
            observed_behavior="Different observed",
            expected_behavior="Different expected",
        )
        with self.assertRaises(DuplicateFeedbackError) as cm_id:
            FeedbackToWorkOrderAdapter.transform(
                base_work_order=self.base_work_order,
                feedback_items=[fb1, fb1_dup_id],
            )
        self.assertIn("Duplicate feedback ID", str(cm_id.exception))

        # 6b: Duplicate issue signature (different ID, same issue & observed behavior)
        fb1_dup_issue = EngineeringFeedback(
            feedback_id=self.fb_id_2,
            source_worker="Tester",
            source_task="ttask-3",
            target_project=self.project_id,
            related_work_order=self.base_wo_id,
            issue="Payment webhook missing signature",
            observed_behavior="Header X-Signature is None",
            expected_behavior="HMAC SHA256 header present",
        )
        with self.assertRaises(DuplicateFeedbackError) as cm_issue:
            FeedbackToWorkOrderAdapter.transform(
                base_work_order=self.base_work_order,
                feedback_items=[fb1, fb1_dup_issue],
            )
        self.assertIn("Duplicate feedback issue detected", str(cm_issue.exception))

    # =========================================================================
    # Scenario 7: Manager acceptance / rejection filtering
    # =========================================================================
    def test_07_manager_acceptance_rejection(self) -> None:
        """Scenario 7: Only Manager-accepted feedback becomes requirements; unaccepted is cataloged as rejected."""
        fb_accepted = EngineeringFeedback(
            feedback_id=self.fb_id_1,
            source_worker="Tester",
            source_task="ttask-1",
            target_project=self.project_id,
            related_work_order=self.base_wo_id,
            issue="Accepted settlement defect",
            observed_behavior="Observed failure",
            expected_behavior="Expected fix",
        )
        fb_rejected = EngineeringFeedback(
            feedback_id=self.fb_id_2,
            source_worker="Designer",
            source_task="dtask-1",
            target_project=self.project_id,
            related_work_order=self.base_wo_id,
            issue="Color scheme should be neon purple",
            issue_type=FeedbackIssueType.UX_INTEGRATION,
            observed_behavior="Theme is dark blue",
            expected_behavior="Theme is neon purple",
            suggested_direction="Redesign CSS styling",
        )

        revised_wo = FeedbackToWorkOrderAdapter.transform(
            base_work_order=self.base_work_order,
            feedback_items=[fb_accepted, fb_rejected],
            accepted_feedback_ids=[self.fb_id_1],  # Only fb_accepted is approved by Manager
        )

        # Accepted feedback is in technical requirements
        reqs = revised_wo.technical_requirements
        self.assertTrue(any("Accepted settlement defect" in r for r in reqs))
        # Rejected feedback is NOT in requirements
        self.assertFalse(any("neon purple" in r for r in reqs))

        # Context accurately partitions accepted vs rejected
        accepted_fb = revised_wo.context.get("accepted_feedback", [])
        rejected_fb = revised_wo.context.get("rejected_feedback", [])
        self.assertEqual(len(accepted_fb), 1)
        self.assertEqual(accepted_fb[0]["feedback_id"], self.fb_id_1)
        self.assertEqual(len(rejected_fb), 1)
        self.assertEqual(rejected_fb[0]["feedback_id"], self.fb_id_2)

        # Trace records Manager acceptance decision
        self.assertEqual(revised_wo.trace.get("accepted_feedback_ids"), [self.fb_id_1])
        self.assertEqual(revised_wo.trace.get("rejected_feedback_ids"), [self.fb_id_2])

    # =========================================================================
    # Scenario 8: Lineage preservation & strict permission bounds
    # =========================================================================
    def test_08_lineage_preservation(self) -> None:
        """Scenario 8: Preserves unbroken lineage across multiple revisions with no permission expansion."""
        fb1 = EngineeringFeedback(
            feedback_id=self.fb_id_1,
            source_worker="Tester",
            source_task="ttask-1",
            target_project=self.project_id,
            related_work_order=self.base_wo_id,
            issue="Revision 1 defect",
            observed_behavior="Observed",
            expected_behavior="Expected",
        )

        # Rev 1 -> Rev 2
        rev2_wo = FeedbackToWorkOrderAdapter.transform(
            base_work_order=self.base_work_order,
            feedback_items=[fb1],
        )
        self.assertEqual(rev2_wo.revision_number, 2)
        self.assertEqual(rev2_wo.parent_work_order_id, self.base_wo_id)
        self.assertEqual(rev2_wo.trace.get("parent_work_order_id"), self.base_wo_id)
        self.assertEqual(rev2_wo.trace.get("previous_revision"), 1)

        # Permission bounds must be preserved from original base order
        self.assertEqual(rev2_wo.allowed_paths, self.base_work_order.allowed_paths)
        self.assertEqual(rev2_wo.writable_paths, self.base_work_order.writable_paths)
        self.assertEqual(rev2_wo.read_only_paths, self.base_work_order.read_only_paths)
        self.assertEqual(rev2_wo.forbidden_paths, self.base_work_order.forbidden_paths)
        self.assertEqual(rev2_wo.allowed_commands, self.base_work_order.allowed_commands)

        # Rev 2 -> Rev 3
        fb2_id = new_feedback_id()
        fb2 = EngineeringFeedback(
            feedback_id=fb2_id,
            source_worker="Tester",
            source_task="ttask-2",
            target_project=self.project_id,
            related_work_order=rev2_wo.work_order_id,
            issue="Revision 2 follow-up regression",
            issue_type=FeedbackIssueType.REGRESSION,
            observed_behavior="Observed in rev 2",
            expected_behavior="Expected in rev 2",
        )

        rev3_wo = FeedbackToWorkOrderAdapter.transform(
            base_work_order=rev2_wo,
            feedback_items=[fb2],
        )
        self.assertEqual(rev3_wo.revision_number, 3)
        self.assertEqual(rev3_wo.parent_work_order_id, rev2_wo.work_order_id)
        self.assertEqual(rev3_wo.trace.get("previous_revision"), 2)

    # =========================================================================
    # Model serialization & validation checks
    # =========================================================================
    def test_feedback_model_serialization(self) -> None:
        """Verifies EngineeringFeedback to_dict, from_dict, to_json, from_json roundtrip."""
        fb = EngineeringFeedback(
            feedback_id=self.fb_id_1,
            source_worker="Tester",
            source_task="ttask-99",
            target_project=self.project_id,
            related_work_order=self.base_wo_id,
            issue="Payment serialization error",
            issue_type=FeedbackIssueType.API_CONTRACT,
            severity=FeedbackSeverity.HIGH,
            observed_behavior="500 Internal Server Error",
            expected_behavior="200 OK with json response",
            suggested_direction="Check serializer field definitions",
            confidence=FeedbackConfidence.REPRODUCED,
            reproduction_information={"curl": "curl http://localhost:8000"},
            evidence=[self.evidence_item_1],
            trace={"span_id": "span-123"},
        )

        data = fb.to_dict()
        self.assertEqual(data["feedback_id"], self.fb_id_1)
        self.assertEqual(data["issue_type"], "API_CONTRACT")
        self.assertEqual(data["severity"], "HIGH")

        reconstructed = EngineeringFeedback.from_dict(data)
        self.assertEqual(reconstructed.feedback_id, fb.feedback_id)
        self.assertEqual(reconstructed.issue, fb.issue)
        self.assertEqual(reconstructed.severity, FeedbackSeverity.HIGH)
        self.assertEqual(reconstructed.issue_type, FeedbackIssueType.API_CONTRACT)
        self.assertEqual(reconstructed.confidence, FeedbackConfidence.REPRODUCED)

        json_str = fb.to_json()
        from_json_fb = EngineeringFeedback.from_json(json_str)
        self.assertEqual(from_json_fb.feedback_id, fb.feedback_id)

    def test_feedback_model_validations(self) -> None:
        """Verifies validation of required fields on EngineeringFeedback."""
        with self.assertRaises(InvalidProgrammerIdError):
            validate_feedback_id("invalid-id")

        with self.assertRaises(ProgrammerValidationError):
            EngineeringFeedback(
                feedback_id=self.fb_id_1,
                source_worker="",  # empty
                source_task="task-1",
                target_project="proj",
                related_work_order=self.base_wo_id,
                issue="issue",
                observed_behavior="obs",
                expected_behavior="exp",
            )

        with self.assertRaises(InvalidProgrammerIdError):
            EngineeringFeedback(
                feedback_id=self.fb_id_1,
                source_worker="Tester",
                source_task="task-1",
                target_project="proj",
                related_work_order="invalid-wo-id",  # bad wo id
                issue="issue",
                observed_behavior="obs",
                expected_behavior="exp",
            )


if __name__ == "__main__":
    unittest.main()
