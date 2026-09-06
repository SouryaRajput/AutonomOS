import pytest

from core.enums import RiskLevel
from core.programmer.contracts.acceptance_criteria import AcceptanceCriterion
from core.programmer.contracts.diff_verifier import (
    DiffVerification,
    RenamedFile,
    UnauthorizedChange,
)
from core.programmer.contracts.evidence_aggregator import VerificationEvidenceAggregator
from core.programmer.contracts.identifiers import (
    new_execution_id,
    new_verification_check_id,
    new_verification_evidence_id,
    new_work_order_id,
    new_workspace_id,
)
from core.programmer.contracts.verification import (
    AcceptanceResult,
    VerificationCheck,
    VerificationEvidence,
    VerificationSummary,
)
from core.programmer.contracts.work_order import ProgrammerWorkOrder
from core.programmer.errors import (
    ProgrammerLineageError,
    ProgrammerValidationError,
)
from core.programmer.types import (
    AcceptanceCriterionType,
    PathBoundaryScope,
    VerificationCheckType,
    VerificationEvidenceSourceType,
    VerificationStatus,
    VerificationSummaryStatus,
)


@pytest.fixture
def base_work_order() -> ProgrammerWorkOrder:
    return ProgrammerWorkOrder(
        work_order_id=new_work_order_id(),
        manager_task_id="tsk-agg-001",
        project_id="prj-agg-test",
        correlation_id="corr-agg-001",
        objective="Implement and verify secure session management",
        allowed_paths=["src/auth"],
        writable_paths=["src/auth"],
        acceptance_criteria=[
            AcceptanceCriterion(
                criterion_id="crit-test-1",
                description="All unit tests pass",
                criterion_type=AcceptanceCriterionType.TEST_PASS,
            ),
            AcceptanceCriterion(
                criterion_id="crit-scope-1",
                description="Only files under src/auth may change",
                criterion_type=AcceptanceCriterionType.FILE_CHANGED,
                target="src/auth",
            ),
        ],
        time_budget=300,
        iteration_budget=10,
        risk_level=RiskLevel.LOW,
    )


class TestVerificationEvidenceAggregator:
    """Unit test suite for Programmer V1 Phase 4.5 VerificationEvidenceAggregator."""

    def test_fully_verified_execution(self, base_work_order: ProgrammerWorkOrder):
        """All checks pass, acceptance criteria pass with authoritative evidence, diff is clean -> VERIFIED."""
        exec_id = new_execution_id()
        wo_id = base_work_order.work_order_id

        # 1. Authoritative test evidence
        ev_test = VerificationEvidence(
            execution_id=exec_id,
            work_order_id=wo_id,
            source_type=VerificationEvidenceSourceType.TEST_RUNNER,
            source_reference="pytest test/unit",
            description="pytest exit code 0",
            is_agent_claim=False,
            data={"exit_code": 0, "passed": 10},
        )
        # 2. Passing check
        chk = VerificationCheck(
            execution_id=exec_id,
            work_order_id=wo_id,
            check_type=VerificationCheckType.TEST,
            command="pytest test/unit",
            status=VerificationStatus.PASS,
            exit_code=0,
            evidence=[ev_test.evidence_id],
        )
        # 3. Passing acceptance results
        res1 = AcceptanceResult(
            criterion_id="crit-test-1",
            status=VerificationStatus.PASS,
            explanation="10 tests passed",
            evidence=[ev_test.evidence_id],
            execution_id=exec_id,
            work_order_id=wo_id,
        )
        res2 = AcceptanceResult(
            criterion_id="crit-scope-1",
            status=VerificationStatus.PASS,
            explanation="Modifications confined to src/auth",
            evidence=[ev_test.evidence_id],
            execution_id=exec_id,
            work_order_id=wo_id,
        )
        # 4. Clean diff verification
        dv_ev = VerificationEvidence(
            execution_id=exec_id,
            work_order_id=wo_id,
            source_type=VerificationEvidenceSourceType.FILESYSTEM,
            is_agent_claim=False,
            data={"status": "PASS"},
        )
        dv = DiffVerification(
            execution_id=exec_id,
            work_order_id=wo_id,
            files_changed=["src/auth/session.py"],
            scope_status=VerificationStatus.PASS,
            evidence=[dv_ev],
        )

        aggregator = VerificationEvidenceAggregator()
        summary = aggregator.aggregate(
            work_order=base_work_order,
            execution_id=exec_id,
            checks=[chk],
            acceptance_results=[res1, res2],
            diff_verification=dv,
            evidence=[ev_test],
        )

        assert summary.overall_status == VerificationSummaryStatus.VERIFIED
        assert summary.is_verified is True
        assert summary.is_failed is False
        assert len(summary.risks) == 0
        assert len(summary.verification_checks) == 1
        assert len(summary.acceptance_results) == 2
        assert len(summary.evidence) >= 2
        assert "VERIFIED" in summary.summary_text

    def test_partial_verification_without_failures(self, base_work_order: ProgrammerWorkOrder):
        """Some criteria passed, but some remain NOT_VERIFIED with no failures -> PARTIALLY_VERIFIED."""
        exec_id = new_execution_id()
        wo_id = base_work_order.work_order_id

        ev_test = VerificationEvidence(
            execution_id=exec_id,
            work_order_id=wo_id,
            source_type=VerificationEvidenceSourceType.TEST_RUNNER,
            is_agent_claim=False,
            data={"exit_code": 0},
        )
        chk = VerificationCheck(
            execution_id=exec_id,
            work_order_id=wo_id,
            status=VerificationStatus.PASS,
            exit_code=0,
            evidence=[ev_test.evidence_id],
        )
        res1 = AcceptanceResult(
            criterion_id="crit-test-1",
            status=VerificationStatus.PASS,
            evidence=[ev_test.evidence_id],
            execution_id=exec_id,
            work_order_id=wo_id,
        )
        res2 = AcceptanceResult(
            criterion_id="crit-scope-1",
            status=VerificationStatus.NOT_VERIFIED,
            explanation="Performance benchmarking was not run",
            execution_id=exec_id,
            work_order_id=wo_id,
        )

        aggregator = VerificationEvidenceAggregator()
        summary = aggregator.aggregate(
            work_order=base_work_order,
            execution_id=exec_id,
            checks=[chk],
            acceptance_results=[res1, res2],
            evidence=[ev_test],
        )

        assert summary.overall_status == VerificationSummaryStatus.PARTIALLY_VERIFIED
        assert summary.is_partially_verified is True
        assert summary.is_verified is False
        assert summary.is_failed is False
        assert len(summary.limitations) > 0
        assert any("could not be verified" in lim for lim in summary.limitations)

    def test_failed_verification_due_to_failing_check(self, base_work_order: ProgrammerWorkOrder):
        """A failing verification check triggers overall FAILED status with diagnostic risk."""
        exec_id = new_execution_id()
        wo_id = base_work_order.work_order_id

        ev = VerificationEvidence(
            execution_id=exec_id,
            work_order_id=wo_id,
            source_type=VerificationEvidenceSourceType.COMMAND_OUTPUT,
            is_agent_claim=False,
            data={"exit_code": 1},
        )
        failed_chk = VerificationCheck(
            execution_id=exec_id,
            work_order_id=wo_id,
            command="pytest test/unit",
            status=VerificationStatus.FAIL,
            exit_code=1,
            evidence=[ev.evidence_id],
        )

        aggregator = VerificationEvidenceAggregator()
        summary = aggregator.aggregate(
            work_order=base_work_order,
            execution_id=exec_id,
            checks=[failed_chk],
            evidence=[ev],
        )

        assert summary.overall_status == VerificationSummaryStatus.FAILED
        assert summary.is_failed is True
        assert len(summary.risks) > 0
        assert any("failed" in r and "exit code 1" in r for r in summary.risks)

    def test_failed_verification_due_to_failing_acceptance_criterion(self, base_work_order: ProgrammerWorkOrder):
        """A failed acceptance criterion triggers overall FAILED status."""
        exec_id = new_execution_id()
        wo_id = base_work_order.work_order_id

        ev = VerificationEvidence(
            execution_id=exec_id,
            work_order_id=wo_id,
            source_type=VerificationEvidenceSourceType.COMMAND_OUTPUT,
            is_agent_claim=False,
        )
        chk = VerificationCheck(
            execution_id=exec_id,
            work_order_id=wo_id,
            status=VerificationStatus.PASS,
            exit_code=0,
            evidence=[ev.evidence_id],
        )
        failed_res = AcceptanceResult(
            criterion_id="crit-scope-1",
            status=VerificationStatus.FAIL,
            explanation="Unauthorized file modified",
            evidence=[ev.evidence_id],
            execution_id=exec_id,
            work_order_id=wo_id,
        )

        aggregator = VerificationEvidenceAggregator()
        summary = aggregator.aggregate(
            work_order=base_work_order,
            execution_id=exec_id,
            checks=[chk],
            acceptance_results=[failed_res],
            evidence=[ev],
        )

        assert summary.overall_status == VerificationSummaryStatus.FAILED
        assert summary.is_failed is True
        assert any("failed" in r for r in summary.risks)

    def test_unauthorized_changes_trigger_failure(self, base_work_order: ProgrammerWorkOrder):
        """Unauthorized changes in DiffVerification trigger overall FAILED with scope risk details."""
        exec_id = new_execution_id()
        wo_id = base_work_order.work_order_id

        ev = VerificationEvidence(
            execution_id=exec_id,
            work_order_id=wo_id,
            source_type=VerificationEvidenceSourceType.FILESYSTEM,
            is_agent_claim=False,
        )
        unauth = UnauthorizedChange(
            path="secrets/keys.json",
            change_type="MODIFIED",
            scope=PathBoundaryScope.FORBIDDEN,
            reason="Path in forbidden scope",
        )
        dv = DiffVerification(
            execution_id=exec_id,
            work_order_id=wo_id,
            files_changed=["secrets/keys.json"],
            unauthorized_changes=[unauth],
            scope_status=VerificationStatus.FAIL,
            evidence=[ev],
        )

        aggregator = VerificationEvidenceAggregator()
        summary = aggregator.aggregate(
            work_order=base_work_order,
            execution_id=exec_id,
            diff_verification=dv,
        )

        assert summary.overall_status == VerificationSummaryStatus.FAILED
        assert summary.is_failed is True
        assert any("secrets/keys.json" in r for r in summary.risks)

    def test_insufficient_evidence_yields_unverified(self, base_work_order: ProgrammerWorkOrder):
        """When zero authoritative evidence exists, overall status is UNVERIFIED without hiding uncertainty."""
        exec_id = new_execution_id()
        wo_id = base_work_order.work_order_id

        aggregator = VerificationEvidenceAggregator()
        summary = aggregator.aggregate(
            work_order=base_work_order,
            execution_id=exec_id,
            checks=[],
            acceptance_results=[],
            evidence=[],
        )

        assert summary.overall_status == VerificationSummaryStatus.UNVERIFIED
        assert summary.is_unverified is True
        assert any("Insufficient authoritative evidence" in lim or "No verification checks" in lim for lim in summary.limitations)

    def test_agent_narration_discounted_cannot_produce_verified(self, base_work_order: ProgrammerWorkOrder):
        """Claims based solely on agent narration are discounted and cannot produce VERIFIED."""
        exec_id = new_execution_id()
        wo_id = base_work_order.work_order_id

        claim_ev = VerificationEvidence(
            execution_id=exec_id,
            work_order_id=wo_id,
            source_type=VerificationEvidenceSourceType.AGENT_CLAIM,
            description="Cline narrates that all tests pass",
            is_agent_claim=True,
        )
        res = AcceptanceResult(
            criterion_id="crit-test-1",
            status=VerificationStatus.PASS,
            explanation="Agent reported success",
            evidence=[claim_ev.evidence_id],
            execution_id=exec_id,
            work_order_id=wo_id,
        )

        aggregator = VerificationEvidenceAggregator()
        summary = aggregator.aggregate(
            work_order=base_work_order,
            execution_id=exec_id,
            acceptance_results=[res],
            evidence=[claim_ev],
        )

        # Because the only evidence is an agent claim, status must NOT be VERIFIED
        assert summary.overall_status == VerificationSummaryStatus.UNVERIFIED
        assert summary.is_verified is False
        assert any("discounted" in lim for lim in summary.limitations)

    def test_mixed_acceptance_statuses(self, base_work_order: ProgrammerWorkOrder):
        """Mixed criteria with 1 PASS, 1 NOT_VERIFIED, and 1 FAIL deterministically yields FAILED."""
        exec_id = new_execution_id()
        wo_id = base_work_order.work_order_id

        ev = VerificationEvidence(
            execution_id=exec_id,
            work_order_id=wo_id,
            source_type=VerificationEvidenceSourceType.COMMAND_OUTPUT,
            is_agent_claim=False,
        )
        chk = VerificationCheck(
            execution_id=exec_id,
            work_order_id=wo_id,
            status=VerificationStatus.PASS,
            exit_code=0,
            evidence=[ev.evidence_id],
        )
        res_pass = AcceptanceResult(
            criterion_id="crit-1",
            status=VerificationStatus.PASS,
            evidence=[ev.evidence_id],
            execution_id=exec_id,
            work_order_id=wo_id,
        )
        res_unverified = AcceptanceResult(
            criterion_id="crit-2",
            status=VerificationStatus.NOT_VERIFIED,
            explanation="Unverified metric",
            execution_id=exec_id,
            work_order_id=wo_id,
        )
        res_fail = AcceptanceResult(
            criterion_id="crit-3",
            status=VerificationStatus.FAIL,
            explanation="Regression detected",
            execution_id=exec_id,
            work_order_id=wo_id,
        )

        aggregator = VerificationEvidenceAggregator()
        summary = aggregator.aggregate(
            work_order=base_work_order,
            execution_id=exec_id,
            checks=[chk],
            acceptance_results=[res_pass, res_unverified, res_fail],
            evidence=[ev],
        )

        assert summary.overall_status == VerificationSummaryStatus.FAILED
        assert summary.is_failed is True

    def test_evidence_lineage_mismatch_raises_error(self, base_work_order: ProgrammerWorkOrder):
        """Checks or evidence with mismatched execution_id or work_order_id raise ProgrammerLineageError."""
        exec_id = new_execution_id()
        other_exec_id = new_execution_id()
        wo_id = base_work_order.work_order_id

        ev = VerificationEvidence(
            execution_id=other_exec_id,
            work_order_id=wo_id,
            source_type=VerificationEvidenceSourceType.COMMAND_OUTPUT,
            is_agent_claim=False,
        )

        aggregator = VerificationEvidenceAggregator()
        with pytest.raises(ProgrammerLineageError, match="execution_id"):
            aggregator.aggregate(
                work_order=base_work_order,
                execution_id=exec_id,
                evidence=[ev],
            )

    def test_deterministic_aggregation_reproducibility(self, base_work_order: ProgrammerWorkOrder):
        """Repeated aggregation calls with identical inputs produce identical statuses, risks, and limitations."""
        exec_id = new_execution_id()
        wo_id = base_work_order.work_order_id

        ev = VerificationEvidence(
            execution_id=exec_id,
            work_order_id=wo_id,
            source_type=VerificationEvidenceSourceType.COMMAND_OUTPUT,
            is_agent_claim=False,
        )
        chk = VerificationCheck(
            execution_id=exec_id,
            work_order_id=wo_id,
            status=VerificationStatus.PASS,
            exit_code=0,
            evidence=[ev.evidence_id],
        )
        res = AcceptanceResult(
            criterion_id="crit-test-1",
            status=VerificationStatus.PASS,
            evidence=[ev.evidence_id],
            execution_id=exec_id,
            work_order_id=wo_id,
        )

        aggregator = VerificationEvidenceAggregator()
        summary1 = aggregator.aggregate(
            work_order=base_work_order,
            execution_id=exec_id,
            checks=[chk],
            acceptance_results=[res],
            evidence=[ev],
        )
        summary2 = aggregator.aggregate(
            work_order=base_work_order,
            execution_id=exec_id,
            checks=[chk],
            acceptance_results=[res],
            evidence=[ev],
        )

        assert summary1.overall_status == summary2.overall_status
        assert summary1.risks == summary2.risks
        assert summary1.limitations == summary2.limitations
        assert summary1.metadata == summary2.metadata

    def test_summary_serialization_with_phase_4_5_fields(self, base_work_order: ProgrammerWorkOrder):
        """Full serialization and deserialization roundtrip preserves all Phase 4.5 fields."""
        exec_id = new_execution_id()
        wo_id = base_work_order.work_order_id

        ev = VerificationEvidence(
            execution_id=exec_id,
            work_order_id=wo_id,
            source_type=VerificationEvidenceSourceType.COMMAND_OUTPUT,
            is_agent_claim=False,
        )
        chk = VerificationCheck(
            execution_id=exec_id,
            work_order_id=wo_id,
            status=VerificationStatus.PASS,
            exit_code=0,
            evidence=[ev.evidence_id],
        )
        res = AcceptanceResult(
            criterion_id="crit-test-1",
            status=VerificationStatus.PASS,
            evidence=[ev.evidence_id],
            execution_id=exec_id,
            work_order_id=wo_id,
        )
        dv = DiffVerification(
            execution_id=exec_id,
            work_order_id=wo_id,
            files_changed=["src/auth/session.py"],
            scope_status=VerificationStatus.PASS,
            evidence=[ev],
        )

        aggregator = VerificationEvidenceAggregator()
        summary = aggregator.aggregate(
            work_order=base_work_order,
            execution_id=exec_id,
            checks=[chk],
            acceptance_results=[res],
            diff_verification=dv,
            evidence=[ev],
            started_at="2026-09-06T12:00:00Z",
            completed_at="2026-09-06T12:05:00Z",
        )

        data = summary.to_dict()
        restored = VerificationSummary.from_dict(data)
        restored.validate()

        assert restored.overall_status == VerificationSummaryStatus.VERIFIED
        assert restored.execution_id == exec_id
        assert restored.work_order_id == wo_id
        assert len(restored.verification_checks) == 1
        assert len(restored.acceptance_results) == 1
        assert restored.diff_verification is not None
        assert restored.diff_verification.files_changed == ["src/auth/session.py"]
        assert restored.timestamps.get("started_at") == "2026-09-06T12:00:00Z"
        assert restored.timestamps.get("completed_at") == "2026-09-06T12:05:00Z"
