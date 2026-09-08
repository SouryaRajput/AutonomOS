from __future__ import annotations

import unittest

from core.programmer.contracts.diff_verifier import DiffVerification
from core.programmer.contracts.handoff import EngineeringHandoff
from core.programmer.contracts.identifiers import (
    new_diff_verification_id,
    new_work_order_id,
)
from core.programmer.contracts.tester_handoff import (
    ChangedApiContract,
    ProgrammerToTesterHandoffBuilder,
    ProgrammerVerificationSummary,
)
from core.programmer.contracts.work_order import ProgrammerWorkOrder
from core.programmer.errors import (
    MissingVerificationEvidenceError,
    UnauthorizedQAClaimError,
)
from core.programmer.types import (
    ApiChangeType,
    EngineeringHandoffType,
    HandoffPriority,
    VerificationDomain,
)


class TestProgrammerTesterHandoff(unittest.TestCase):
    """Unit tests for Phase 8.4: Programmer -> Tester Handoff."""

    def setUp(self) -> None:
        self.wo_id = new_work_order_id()
        self.work_order = ProgrammerWorkOrder(
            work_order_id=self.wo_id,
            manager_task_id="mtask-qa-500",
            project_id="proj-commerce-backend",
            correlation_id="corr-qa-777",
            objective="Implement order checkout and payment processing pipeline",
            technical_requirements=[
                "Provide checkout payment endpoint supporting credit card and wire transfer",
                "Ensure idempotent order charge submissions",
            ],
            constraints=[
                "All external gateway requests must timeout after 3 seconds",
            ],
            acceptance_criteria=[
                "Checkout charges card exactly once per idempotency key",
                "Invalid card returns 400 with structured error response",
            ],
            allowed_paths=["src/checkout/", "src/payments/"],
            writable_paths=["src/checkout/", "src/payments/"],
        )

        self.evidence_1 = {
            "evidence_id": "vevid-chk-100",
            "source_type": "TEST_RUNNER",
            "description": "Integration test for POST /api/v1/checkout passed with exit code 0",
            "passed": True,
        }
        self.evidence_2 = {
            "evidence_id": "vevid-chk-200",
            "source_type": "COMMAND_OUTPUT",
            "description": "Idempotency regression test suite passed",
            "passed": True,
        }

        self.changed_api = ChangedApiContract(
            endpoint_ref="POST /api/v1/checkout",
            change_type=ApiChangeType.MODIFIED,
            description="Added idempotency key requirement and wire transfer support",
            breaking_change=True,
            supporting_evidence_ids=["vevid-chk-100"],
        )

    # =========================================================================
    # Scenario 1: Complete handoff construction
    # =========================================================================
    def test_01_complete_handoff(self) -> None:
        """Scenario 1: Creates complete handoff encapsulating all implementation details, files, and contracts."""
        handoff = ProgrammerToTesterHandoffBuilder.build(
            work_order=self.work_order,
            implementation_summary="Refactored checkout flow to support multiple payment providers and idempotency.",
            changed_files=["src/checkout/service.py"],
            created_files=["src/payments/wire.py"],
            deleted_files=["src/payments/legacy_gateway.py"],
            affected_modules=["checkout", "payments"],
            changed_apis=[self.changed_api],
            changed_data_models={"OrderPayment": {"id": "str", "idempotency_key": "str", "status": "str"}},
            configuration_changes=["PAYMENT_GATEWAY_TIMEOUT=3s"],
            required_test_areas=["Idempotency collision handling", "Payment timeout rollback"],
            high_value_test_areas=["Concurrent checkout submissions with duplicate idempotency keys"],
            known_risks=[{"risk_id": "prisk-01", "category": "DATA_LOSS", "description": "Duplicate payment potential"}],
            known_limitations=["Wire transfer verification takes up to 24h asynchronously"],
            unresolved_issues=["Need sandbox credentials for live gateway latency tests"],
            previous_verification_results=[
                {"check_id": "vchk-1", "status": "PASS", "evidence": ["vevid-chk-100"]},
                {"check_id": "vchk-2", "status": "PASS", "evidence": ["vevid-chk-200"]},
            ],
            verification_evidence=[self.evidence_1, self.evidence_2],
            priority=HandoffPriority.HIGH,
        )

        self.assertIsInstance(handoff, EngineeringHandoff)
        self.assertEqual(handoff.handoff_type, EngineeringHandoffType.PROGRAMMER_TO_TESTER)
        self.assertEqual(handoff.source_worker_id, "worker-programmer")
        self.assertEqual(handoff.target_worker_id, "worker-tester")

        # Verify context payload
        ctx = handoff.context
        self.assertEqual(ctx["implementation_summary"], "Refactored checkout flow to support multiple payment providers and idempotency.")
        self.assertEqual(ctx["affected_modules"], ["checkout", "payments"])
        self.assertEqual(len(ctx["changed_apis"]), 1)
        self.assertEqual(ctx["changed_apis"][0]["endpoint_ref"], "POST /api/v1/checkout")
        self.assertIn("PAYMENT_GATEWAY_TIMEOUT=3s", ctx["configuration_changes"])
        self.assertIn("OrderPayment", ctx["changed_data_models"])

        # Verify previous verification summary
        prev_summary = ctx["previous_verification"]["summary"]
        self.assertEqual(prev_summary["verification_domain"], VerificationDomain.PROGRAMMER_VERIFICATION.value)
        self.assertEqual(prev_summary["checks_run"], 2)
        self.assertEqual(prev_summary["checks_passed"], 2)
        self.assertTrue(prev_summary["independent_testing_recommended"])

    # =========================================================================
    # Scenario 2: Changed-file reporting
    # =========================================================================
    def test_02_changed_file_reporting(self) -> None:
        """Scenario 2: Correctly reports and segregates changed, created, and deleted files, with DiffVerification support."""
        diff_verif = DiffVerification(
            verification_id=new_diff_verification_id(),
            execution_id="pexec-test-1",
            work_order_id=self.wo_id,
            files_changed=["src/checkout/service.py", "src/checkout/router.py"],
            files_created=["src/payments/wire.py"],
            files_deleted=["src/payments/legacy_gateway.py"],
            unauthorized_changes=[],
        )

        handoff = ProgrammerToTesterHandoffBuilder.build(
            work_order=self.work_order,
            implementation_summary="Updated checkout handlers and removed legacy gateway.",
            diff_verification=diff_verif,
            verification_evidence=[self.evidence_1],
        )

        ctx = handoff.context
        self.assertEqual(ctx["changed_files"], ["src/checkout/service.py", "src/checkout/router.py"])
        self.assertEqual(ctx["created_files"], ["src/payments/wire.py"])
        self.assertEqual(ctx["deleted_files"], ["src/payments/legacy_gateway.py"])

        # Auto-focus on deleted files in high value areas
        self.assertIn("Deleted Files & Regressions", ctx["high_value_test_areas"])

    # =========================================================================
    # Scenario 3: API changes
    # =========================================================================
    def test_03_api_changes(self) -> None:
        """Scenario 3: Communicates API changes, breaking change flags, and auto-elevates breaking changes to focus areas."""
        api_added = ChangedApiContract(
            endpoint_ref="GET /api/v1/payments/methods",
            change_type=ApiChangeType.ADDED,
            description="List supported payment gateways",
            breaking_change=False,
            supporting_evidence_ids=["vevid-chk-100"],
        )

        handoff = ProgrammerToTesterHandoffBuilder.build(
            work_order=self.work_order,
            implementation_summary="Added payment method listing and updated checkout endpoint.",
            changed_apis=[self.changed_api, api_added],
            verification_evidence=[self.evidence_1],
        )

        ctx = handoff.context
        apis = ctx["changed_apis"]
        self.assertEqual(len(apis), 2)
        chk_api = next(a for a in apis if a["endpoint_ref"] == "POST /api/v1/checkout")
        self.assertTrue(chk_api["breaking_change"])

        # Breaking change elevated to high value focus areas
        self.assertIn("Breaking API Change: POST /api/v1/checkout", ctx["high_value_test_areas"])

    # =========================================================================
    # Scenario 4: Risk propagation
    # =========================================================================
    def test_04_risk_propagation(self) -> None:
        """Scenario 4: Accurately propagates engineering risks into the handoff contract."""
        risks_in = [
            {"risk_id": "prisk-sec-01", "category": "SECURITY", "severity": "HIGH", "description": "Token leakage risk"},
            "Concurrency race condition during high traffic peak",
        ]

        handoff = ProgrammerToTesterHandoffBuilder.build(
            work_order=self.work_order,
            implementation_summary="Hardened token handling.",
            known_risks=risks_in,
            verification_evidence=[self.evidence_1],
        )

        self.assertEqual(len(handoff.risks), 2)
        self.assertEqual(handoff.risks[0]["risk_id"], "prisk-sec-01")
        self.assertEqual(handoff.risks[1]["description"], "Concurrency race condition during high traffic peak")

    # =========================================================================
    # Scenario 5: Previous verification evidence and authority boundary
    # =========================================================================
    def test_05_previous_verification_evidence(self) -> None:
        """Scenario 5: Tags checks as PROGRAMMER_VERIFICATION and rejects unauthorized QA completion assertions."""
        # Case A: Valid verification results tagged under PROGRAMMER_VERIFICATION
        handoff = ProgrammerToTesterHandoffBuilder.build(
            work_order=self.work_order,
            implementation_summary="Completed implementation and internal unit checks.",
            previous_verification_results=[{"check_id": "vchk-1", "status": "PASS", "evidence": ["vevid-chk-100"]}],
            verification_evidence=[self.evidence_1],
        )

        summary = handoff.context["previous_verification"]["summary"]
        self.assertEqual(summary["verification_domain"], "PROGRAMMER_VERIFICATION")
        self.assertTrue(summary["independent_testing_recommended"])

        # Case B: Programmer asserts testing is complete and independent QA unnecessary -> REJECTED
        with self.assertRaises(UnauthorizedQAClaimError) as ctx:
            ProgrammerToTesterHandoffBuilder.build(
                work_order=self.work_order,
                implementation_summary="Implemented all features and all tests passed. Testing is complete, skip independent testing.",
                verification_evidence=[self.evidence_1],
            )
        self.assertIn("Unauthorized QA authority claim", str(ctx.exception))

    # =========================================================================
    # Scenario 6: Unresolved issues & limitations
    # =========================================================================
    def test_06_unresolved_issues(self) -> None:
        """Scenario 6: Surfaces known limitations and unresolved issues in known_unknowns."""
        handoff = ProgrammerToTesterHandoffBuilder.build(
            work_order=self.work_order,
            implementation_summary="Checkout service deployed to staging.",
            known_limitations=["Refund processing endpoint is mocked"],
            unresolved_issues=["Requires load testing under simulated network drops"],
            verification_evidence=[self.evidence_1],
        )

        self.assertIn("Refund processing endpoint is mocked", handoff.known_unknowns)
        self.assertIn("Requires load testing under simulated network drops", handoff.known_unknowns)
        self.assertIn("Refund processing endpoint is mocked", handoff.context["known_limitations"])

    # =========================================================================
    # Scenario 7: Lineage preservation
    # =========================================================================
    def test_07_lineage_preservation(self) -> None:
        """Scenario 7: Preserves work_order_id, manager_task_id, project_id, and trace."""
        custom_trace = {"audit_id": "aud-888", "step": "tester_handoff"}
        handoff = ProgrammerToTesterHandoffBuilder.build(
            work_order=self.work_order,
            implementation_summary="Ready for QA test suite execution.",
            verification_evidence=[self.evidence_1],
            priority=HandoffPriority.URGENT,
            trace=custom_trace,
        )

        self.assertEqual(handoff.work_order_id, self.wo_id)
        self.assertEqual(handoff.source_task_id, "mtask-qa-500")
        self.assertEqual(handoff.project_id, "proj-commerce-backend")
        self.assertEqual(handoff.priority, HandoffPriority.URGENT)
        self.assertEqual(handoff.trace["work_order_id"], self.wo_id)
        self.assertEqual(handoff.trace["manager_task_id"], "mtask-qa-500")
        self.assertEqual(handoff.trace["audit_id"], "aud-888")

    # =========================================================================
    # Scenario 8: Missing evidence detection
    # =========================================================================
    def test_08_missing_evidence(self) -> None:
        """Scenario 8: Rejects references to missing verification evidence IDs with MissingVerificationEvidenceError."""
        bad_api = ChangedApiContract(
            endpoint_ref="DELETE /api/v1/orders/{id}",
            supporting_evidence_ids=["vevid-missing-999"],
        )

        with self.assertRaises(MissingVerificationEvidenceError) as ctx:
            ProgrammerToTesterHandoffBuilder.build(
                work_order=self.work_order,
                implementation_summary="Added order cancellation endpoint.",
                changed_apis=[bad_api],
                verification_evidence=[self.evidence_1],  # Does not contain vevid-missing-999
            )
        self.assertEqual(ctx.exception.evidence_id, "vevid-missing-999")


if __name__ == "__main__":
    unittest.main()
