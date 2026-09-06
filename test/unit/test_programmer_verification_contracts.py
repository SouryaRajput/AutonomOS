import pytest

from core.programmer.contracts.identifiers import (
    VERIFICATION_CHECK_ID_PREFIX,
    VERIFICATION_EVIDENCE_ID_PREFIX,
    new_execution_id,
    new_verification_check_id,
    new_verification_evidence_id,
    new_work_order_id,
    validate_verification_check_id,
    validate_verification_evidence_id,
)
from core.programmer.contracts.verification import (
    AcceptanceResult,
    VerificationCheck,
    VerificationEvidence,
    VerificationSummary,
)
from core.programmer.errors import (
    InvalidProgrammerIdError,
    ProgrammerLineageError,
    ProgrammerValidationError,
)
from core.programmer.types import (
    VerificationCheckType,
    VerificationEvidenceSourceType,
    VerificationStatus,
)


class TestVerificationEnumsAndIdentifiers:
    """Tests for verification status enums and identifier lifecycle."""

    def test_five_state_model_non_collapsible(self):
        """Ensure all 5 verification states are strictly distinct and non-collapsible."""
        statuses = [
            VerificationStatus.PASS,
            VerificationStatus.FAIL,
            VerificationStatus.NOT_RUN,
            VerificationStatus.NOT_VERIFIED,
            VerificationStatus.ERROR,
        ]
        # Must have exactly 5 distinct values
        assert len(set(statuses)) == 5
        # Values must match expected strings
        assert {s.value for s in statuses} == {
            "PASS",
            "FAIL",
            "NOT_RUN",
            "NOT_VERIFIED",
            "ERROR",
        }
        # None of them can be boolean True/False
        for s in statuses:
            assert isinstance(s.value, str)
            assert s is not True and s is not False

    def test_verification_check_types(self):
        """Ensure verification check types are distinct."""
        types = [
            VerificationCheckType.TEST,
            VerificationCheckType.COMMAND,
            VerificationCheckType.LINT,
            VerificationCheckType.TYPECHECK,
            VerificationCheckType.BUILD,
            VerificationCheckType.STATIC_ANALYSIS,
            VerificationCheckType.CUSTOM,
        ]
        assert len(set(types)) == 7

    def test_evidence_source_types(self):
        """Ensure evidence source types include execution sources and agent claims."""
        sources = [
            VerificationEvidenceSourceType.COMMAND_OUTPUT,
            VerificationEvidenceSourceType.TEST_RUNNER,
            VerificationEvidenceSourceType.FILESYSTEM,
            VerificationEvidenceSourceType.PROCESS_EXIT,
            VerificationEvidenceSourceType.STATIC_ANALYSIS,
            VerificationEvidenceSourceType.EXTERNAL_EVALUATION,
            VerificationEvidenceSourceType.AGENT_CLAIM,
        ]
        assert len(set(sources)) == 7

    def test_verification_id_generation_and_validation(self):
        """Verify generation and format validation of verification check and evidence IDs."""
        chk_id = new_verification_check_id()
        assert chk_id.startswith(VERIFICATION_CHECK_ID_PREFIX)
        validate_verification_check_id(chk_id)

        evid_id = new_verification_evidence_id()
        assert evid_id.startswith(VERIFICATION_EVIDENCE_ID_PREFIX)
        validate_verification_evidence_id(evid_id)

        # Invalid prefixes
        with pytest.raises(InvalidProgrammerIdError):
            validate_verification_check_id("invalid-id")
        with pytest.raises(InvalidProgrammerIdError):
            validate_verification_check_id(VERIFICATION_CHECK_ID_PREFIX)  # empty suffix

        with pytest.raises(InvalidProgrammerIdError):
            validate_verification_evidence_id("invalid-evidence-id")
        with pytest.raises(InvalidProgrammerIdError):
            validate_verification_evidence_id(VERIFICATION_EVIDENCE_ID_PREFIX)


class TestVerificationEvidenceContract:
    """Tests for VerificationEvidence model."""

    def test_valid_evidence_creation_and_validation(self):
        exec_id = new_execution_id()
        wo_id = new_work_order_id()
        evidence = VerificationEvidence(
            execution_id=exec_id,
            work_order_id=wo_id,
            source_type=VerificationEvidenceSourceType.COMMAND_OUTPUT,
            source_reference="/tmp/out.log",
            description="Unit test run output",
            is_agent_claim=False,
            data={"tests_passed": 12, "tests_failed": 0},
        )
        evidence.validate()
        assert evidence.is_authoritative() is True
        assert evidence.checksum is not None
        assert evidence.evidence_id.startswith(VERIFICATION_EVIDENCE_ID_PREFIX)

    def test_agent_claim_evidence_is_not_authoritative(self):
        exec_id = new_execution_id()
        wo_id = new_work_order_id()
        claim_ev = VerificationEvidence(
            execution_id=exec_id,
            work_order_id=wo_id,
            source_type=VerificationEvidenceSourceType.AGENT_CLAIM,
            description="Agent claims all tests passed",
            data={"message": "I checked everything and tests pass"},
        )
        claim_ev.validate()
        # Agent claims are automatically flagged and never authoritative
        assert claim_ev.is_agent_claim is True
        assert claim_ev.is_authoritative() is False

    def test_evidence_lineage_validation(self):
        wo_id = new_work_order_id()
        # Missing execution_id
        ev1 = VerificationEvidence(work_order_id=wo_id)
        with pytest.raises(ProgrammerLineageError, match="execution_id"):
            ev1.validate()

        # Missing work_order_id
        exec_id = new_execution_id()
        ev2 = VerificationEvidence(execution_id=exec_id)
        with pytest.raises(ProgrammerLineageError, match="work_order_id"):
            ev2.validate()

    def test_evidence_serialization_roundtrip(self):
        exec_id = new_execution_id()
        wo_id = new_work_order_id()
        orig = VerificationEvidence(
            execution_id=exec_id,
            work_order_id=wo_id,
            source_type=VerificationEvidenceSourceType.TEST_RUNNER,
            source_reference="pytest_output",
            description="Pytest results",
            is_agent_claim=False,
            data={"passed": 5},
            metadata={"runner": "pytest"},
        )
        d = orig.to_dict()
        restored = VerificationEvidence.from_dict(d)
        assert restored.evidence_id == orig.evidence_id
        assert restored.execution_id == orig.execution_id
        assert restored.work_order_id == orig.work_order_id
        assert restored.source_type == orig.source_type
        assert restored.is_agent_claim == orig.is_agent_claim
        assert restored.data == orig.data
        assert restored.checksum == orig.checksum


class TestVerificationCheckContract:
    """Tests for VerificationCheck model."""

    def test_valid_check_creation_and_validation(self):
        exec_id = new_execution_id()
        wo_id = new_work_order_id()
        evid_id = new_verification_evidence_id()

        check = VerificationCheck(
            execution_id=exec_id,
            work_order_id=wo_id,
            check_type=VerificationCheckType.TEST,
            command="pytest test/unit",
            status=VerificationStatus.PASS,
            exit_code=0,
            duration_ms=250.5,
            started_at="2026-09-06T12:00:00Z",
            completed_at="2026-09-06T12:00:01Z",
            evidence=[evid_id],
        )
        check.validate()
        assert check.duration == 250.5
        assert check.check_id.startswith(VERIFICATION_CHECK_ID_PREFIX)

    def test_check_lineage_validation(self):
        wo_id = new_work_order_id()
        chk1 = VerificationCheck(work_order_id=wo_id)
        with pytest.raises(ProgrammerLineageError, match="execution_id"):
            chk1.validate()

        exec_id = new_execution_id()
        chk2 = VerificationCheck(execution_id=exec_id)
        with pytest.raises(ProgrammerLineageError, match="work_order_id"):
            chk2.validate()

    def test_check_status_integrity(self):
        exec_id = new_execution_id()
        wo_id = new_work_order_id()

        # PASS cannot have non-zero exit_code
        chk_pass_fail = VerificationCheck(
            execution_id=exec_id,
            work_order_id=wo_id,
            command="pytest",
            status=VerificationStatus.PASS,
            exit_code=1,
        )
        with pytest.raises(ProgrammerValidationError, match="cannot have non-zero exit_code"):
            chk_pass_fail.validate()

        # NOT_RUN cannot have completed_at
        chk_not_run = VerificationCheck(
            execution_id=exec_id,
            work_order_id=wo_id,
            status=VerificationStatus.NOT_RUN,
            completed_at="2026-09-06T12:00:00Z",
        )
        with pytest.raises(ProgrammerValidationError, match="cannot have completed_at"):
            chk_not_run.validate()

    def test_check_serialization_roundtrip(self):
        exec_id = new_execution_id()
        wo_id = new_work_order_id()
        orig = VerificationCheck(
            execution_id=exec_id,
            work_order_id=wo_id,
            check_type=VerificationCheckType.LINT,
            command="ruff check .",
            status=VerificationStatus.PASS,
            exit_code=0,
            duration_ms=105.0,
            evidence=[new_verification_evidence_id()],
            output_snippet="All checks passed!",
        )
        d = orig.to_dict()
        restored = VerificationCheck.from_dict(d)
        assert restored.check_id == orig.check_id
        assert restored.execution_id == orig.execution_id
        assert restored.status == orig.status
        assert restored.check_type == orig.check_type
        assert restored.exit_code == orig.exit_code
        assert restored.duration_ms == orig.duration_ms
        assert restored.output_snippet == orig.output_snippet


class TestAcceptanceResultContract:
    """Tests for AcceptanceResult model."""

    def test_valid_acceptance_result(self):
        exec_id = new_execution_id()
        wo_id = new_work_order_id()
        evid_id = new_verification_evidence_id()

        res = AcceptanceResult(
            criterion_id="crit-test-1",
            status=VerificationStatus.PASS,
            explanation="All 5 unit tests executed and passed with exit code 0",
            evidence=[evid_id],
            execution_id=exec_id,
            work_order_id=wo_id,
        )
        res.validate()
        assert res.status == VerificationStatus.PASS

    def test_acceptance_result_pass_requires_evidence(self):
        """A PASS status requires at least one evidence item."""
        exec_id = new_execution_id()
        wo_id = new_work_order_id()

        res = AcceptanceResult(
            criterion_id="crit-test-1",
            status=VerificationStatus.PASS,
            explanation="Claiming pass without evidence",
            evidence=[],
            execution_id=exec_id,
            work_order_id=wo_id,
        )
        with pytest.raises(ProgrammerValidationError, match="must reference at least one evidence item"):
            res.validate()

    def test_acceptance_result_catalog_validation(self):
        exec_id = new_execution_id()
        wo_id = new_work_order_id()
        evid_id = new_verification_evidence_id()

        res = AcceptanceResult(
            criterion_id="unknown-crit",
            status=VerificationStatus.FAIL,
            evidence=[evid_id],
            execution_id=exec_id,
            work_order_id=wo_id,
        )
        # Catalog without unknown-crit
        with pytest.raises(ProgrammerValidationError, match="not found in provided criteria catalog"):
            res.validate(criteria_catalog=["crit-1", "crit-2"])

        # Matching catalog
        res.validate(criteria_catalog=["unknown-crit", "crit-1"])

    def test_acceptance_result_serialization_roundtrip(self):
        exec_id = new_execution_id()
        wo_id = new_work_order_id()
        evid_id = new_verification_evidence_id()

        orig = AcceptanceResult(
            criterion_id="crit-auth-1",
            status=VerificationStatus.NOT_VERIFIED,
            explanation="Pending manual inspection",
            evidence=[evid_id],
            execution_id=exec_id,
            work_order_id=wo_id,
        )
        d = orig.to_dict()
        restored = AcceptanceResult.from_dict(d)
        assert restored.criterion_id == orig.criterion_id
        assert restored.status == orig.status
        assert restored.explanation == orig.explanation
        assert restored.evidence == orig.evidence
        assert restored.execution_id == orig.execution_id


class TestVerificationSummaryContract:
    """Tests for VerificationSummary model."""

    def test_valid_verification_summary(self):
        exec_id = new_execution_id()
        wo_id = new_work_order_id()

        ev = VerificationEvidence(
            execution_id=exec_id,
            work_order_id=wo_id,
            source_type=VerificationEvidenceSourceType.COMMAND_OUTPUT,
            description="Observed pytest passing output",
            is_agent_claim=False,
            data={"passed": 10},
        )
        chk = VerificationCheck(
            execution_id=exec_id,
            work_order_id=wo_id,
            check_type=VerificationCheckType.TEST,
            command="pytest",
            status=VerificationStatus.PASS,
            exit_code=0,
            evidence=[ev.evidence_id],
        )
        res = AcceptanceResult(
            criterion_id="crit-1",
            status=VerificationStatus.PASS,
            explanation="Tests verified",
            evidence=[ev.evidence_id],
            execution_id=exec_id,
            work_order_id=wo_id,
        )

        summary = VerificationSummary(
            overall_status=VerificationStatus.PASS,
            execution_id=exec_id,
            work_order_id=wo_id,
            checks=[chk],
            acceptance_results=[res],
            evidence=[ev],
            summary_text="All tests passed successfully.",
        )
        summary.validate()
        assert summary.overall_status == VerificationStatus.PASS

    def test_summary_cross_entity_lineage_validation(self):
        exec_id = new_execution_id()
        other_exec_id = new_execution_id()
        wo_id = new_work_order_id()
        other_wo_id = new_work_order_id()

        ev = VerificationEvidence(
            execution_id=exec_id,
            work_order_id=wo_id,
            source_type=VerificationEvidenceSourceType.PROCESS_EXIT,
            is_agent_claim=False,
        )

        # Check with mismatched execution_id
        mismatched_chk = VerificationCheck(
            execution_id=other_exec_id,
            work_order_id=wo_id,
            status=VerificationStatus.PASS,
            exit_code=0,
            evidence=[ev.evidence_id],
        )
        summary = VerificationSummary(
            overall_status=VerificationStatus.PASS,
            execution_id=exec_id,
            work_order_id=wo_id,
            checks=[mismatched_chk],
            evidence=[ev],
        )
        with pytest.raises(ProgrammerLineageError, match="execution_id"):
            summary.validate()

        # Evidence with mismatched work_order_id
        mismatched_ev = VerificationEvidence(
            execution_id=exec_id,
            work_order_id=other_wo_id,
            source_type=VerificationEvidenceSourceType.PROCESS_EXIT,
            is_agent_claim=False,
        )
        valid_chk = VerificationCheck(
            execution_id=exec_id,
            work_order_id=wo_id,
            status=VerificationStatus.PASS,
            exit_code=0,
            evidence=[mismatched_ev.evidence_id],
        )
        summary2 = VerificationSummary(
            overall_status=VerificationStatus.PASS,
            execution_id=exec_id,
            work_order_id=wo_id,
            checks=[valid_chk],
            evidence=[mismatched_ev],
        )
        with pytest.raises(ProgrammerLineageError, match="work_order_id"):
            summary2.validate()

    def test_summary_referential_integrity(self):
        """Checks and acceptance results cannot reference non-existent evidence IDs."""
        exec_id = new_execution_id()
        wo_id = new_work_order_id()

        chk = VerificationCheck(
            execution_id=exec_id,
            work_order_id=wo_id,
            status=VerificationStatus.PASS,
            exit_code=0,
            evidence=["vevid-nonexistent"],
        )
        summary = VerificationSummary(
            overall_status=VerificationStatus.PASS,
            execution_id=exec_id,
            work_order_id=wo_id,
            checks=[chk],
            evidence=[],
        )
        with pytest.raises(ProgrammerValidationError, match="references unknown evidence_id"):
            summary.validate()

    def test_agent_narration_cannot_satisfy_pass(self):
        """CRITICAL: Agent claims alone can NEVER satisfy a PASS outcome."""
        exec_id = new_execution_id()
        wo_id = new_work_order_id()

        claim_ev = VerificationEvidence(
            execution_id=exec_id,
            work_order_id=wo_id,
            source_type=VerificationEvidenceSourceType.AGENT_CLAIM,
            description="Agent claims all tests pass",
            is_agent_claim=True,
        )

        res = AcceptanceResult(
            criterion_id="crit-1",
            status=VerificationStatus.PASS,
            explanation="Cline narrated that tests pass",
            evidence=[claim_ev.evidence_id],
            execution_id=exec_id,
            work_order_id=wo_id,
        )

        summary = VerificationSummary(
            overall_status=VerificationStatus.PASS,
            execution_id=exec_id,
            work_order_id=wo_id,
            acceptance_results=[res],
            evidence=[claim_ev],
        )
        # Rejects because AcceptanceResult PASS cannot rely solely on agent narration
        with pytest.raises(ProgrammerValidationError, match="cannot have status PASS based solely on agent narration"):
            summary.validate()

    def test_overall_pass_requires_authoritative_evidence(self):
        """CRITICAL: VerificationSummary overall PASS requires at least 1 authoritative evidence."""
        exec_id = new_execution_id()
        wo_id = new_work_order_id()

        claim_ev = VerificationEvidence(
            execution_id=exec_id,
            work_order_id=wo_id,
            source_type=VerificationEvidenceSourceType.AGENT_CLAIM,
            description="Agent claim",
            is_agent_claim=True,
        )

        summary = VerificationSummary(
            overall_status=VerificationStatus.PASS,
            execution_id=exec_id,
            work_order_id=wo_id,
            evidence=[claim_ev],
        )
        with pytest.raises(ProgrammerValidationError, match="cannot have overall_status PASS without at least one authoritative verification evidence record"):
            summary.validate()

    def test_overall_pass_cannot_contradict_failed_checks(self):
        """Overall PASS cannot coexist with failed checks."""
        exec_id = new_execution_id()
        wo_id = new_work_order_id()

        ev = VerificationEvidence(
            execution_id=exec_id,
            work_order_id=wo_id,
            source_type=VerificationEvidenceSourceType.COMMAND_OUTPUT,
            is_agent_claim=False,
        )
        failed_chk = VerificationCheck(
            execution_id=exec_id,
            work_order_id=wo_id,
            status=VerificationStatus.FAIL,
            exit_code=1,
            evidence=[ev.evidence_id],
        )

        summary = VerificationSummary(
            overall_status=VerificationStatus.PASS,
            execution_id=exec_id,
            work_order_id=wo_id,
            checks=[failed_chk],
            evidence=[ev],
        )
        with pytest.raises(ProgrammerValidationError, match="cannot have overall_status PASS when one or more checks have status FAIL or ERROR"):
            summary.validate()

    def test_summary_serialization_roundtrip(self):
        exec_id = new_execution_id()
        wo_id = new_work_order_id()

        ev = VerificationEvidence(
            execution_id=exec_id,
            work_order_id=wo_id,
            source_type=VerificationEvidenceSourceType.COMMAND_OUTPUT,
            description="pytest output",
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
            criterion_id="crit-1",
            status=VerificationStatus.PASS,
            explanation="Passed",
            evidence=[ev.evidence_id],
            execution_id=exec_id,
            work_order_id=wo_id,
        )
        summary = VerificationSummary(
            overall_status=VerificationStatus.PASS,
            execution_id=exec_id,
            work_order_id=wo_id,
            checks=[chk],
            acceptance_results=[res],
            evidence=[ev],
            summary_text="Verified successfully",
        )

        d = summary.to_dict()
        restored = VerificationSummary.from_dict(d)
        restored.validate()
        assert restored.overall_status == summary.overall_status
        assert restored.execution_id == summary.execution_id
        assert len(restored.checks) == 1
        assert len(restored.acceptance_results) == 1
        assert len(restored.evidence) == 1
