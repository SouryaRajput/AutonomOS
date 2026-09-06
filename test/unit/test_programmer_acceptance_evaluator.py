import pytest

from core.enums import RiskLevel
from core.programmer.contracts.acceptance_criteria import AcceptanceCriterion
from core.programmer.contracts.acceptance_evaluator import (
    AcceptanceCriteriaEvaluator,
    AcceptanceEvaluationResult,
)
from core.programmer.contracts.identifiers import (
    new_execution_id,
    new_verification_check_id,
    new_verification_evidence_id,
    new_work_order_id,
    validate_execution_id,
    validate_work_order_id,
)
from core.programmer.contracts.verification import (
    AcceptanceResult,
    VerificationCheck,
    VerificationEvidence,
)
from core.programmer.contracts.work_order import ProgrammerWorkOrder
from core.programmer.errors import (
    ProgrammerLineageError,
    ProgrammerValidationError,
)
from core.programmer.types import (
    AcceptanceCriterionType,
    VerificationCheckType,
    VerificationEvidenceSourceType,
    VerificationStatus,
)


@pytest.fixture
def base_work_order() -> ProgrammerWorkOrder:
    return ProgrammerWorkOrder(
        work_order_id=new_work_order_id(),
        manager_task_id="tsk-eval-001",
        project_id="prj-eval-test",
        correlation_id="corr-eval-001",
        objective="Implement authentication feature",
        allowed_paths=["src/auth"],
        writable_paths=["src/auth"],
        acceptance_criteria=[
            AcceptanceCriterion(
                criterion_id="crit-test-1",
                description="All unit tests must pass.",
                criterion_type=AcceptanceCriterionType.TEST_PASS,
            ),
            AcceptanceCriterion(
                criterion_id="crit-scope-1",
                description="Only files under src/auth may change.",
                criterion_type=AcceptanceCriterionType.FILE_CHANGED,
                target="src/auth",
            ),
        ],
        time_budget=300,
        iteration_budget=10,
        risk_level=RiskLevel.LOW,
    )


class TestAcceptanceCriteriaEvaluator:
    """Unit tests for Programmer V1 Phase 4.3 AcceptanceCriteriaEvaluator."""

    def test_all_criteria_pass(self, base_work_order: ProgrammerWorkOrder):
        """When all criteria are backed by observed facts, overall status is PASS."""
        exec_id = new_execution_id()
        wo_id = base_work_order.work_order_id

        # 1. Authoritative test evidence & passing check
        ev_test = VerificationEvidence(
            execution_id=exec_id,
            work_order_id=wo_id,
            source_type=VerificationEvidenceSourceType.TEST_RUNNER,
            source_reference="pytest tests/unit",
            description="Unit test output",
            is_agent_claim=False,
            data={"exit_code": 0, "stdout": "10 passed"},
        )
        chk_test = VerificationCheck(
            execution_id=exec_id,
            work_order_id=wo_id,
            check_type=VerificationCheckType.TEST,
            command="pytest tests/unit",
            status=VerificationStatus.PASS,
            exit_code=0,
            evidence=[ev_test.evidence_id],
        )

        evaluator = AcceptanceCriteriaEvaluator()
        result = evaluator.evaluate(
            work_order=base_work_order,
            execution_id=exec_id,
            checks=[chk_test],
            evidence=[ev_test],
            files_changed=["src/auth/session.py", "src/auth/login.py"],
        )

        assert result.overall_status == VerificationStatus.PASS
        assert result.pass_count == 2
        assert result.fail_count == 0
        assert result.not_verified_count == 0

        # Check criterion 1
        res1 = next(r for r in result.results if r.criterion_id == "crit-test-1")
        assert res1.status == VerificationStatus.PASS
        assert ev_test.evidence_id in res1.evidence

        # Check criterion 2
        res2 = next(r for r in result.results if r.criterion_id == "crit-scope-1")
        assert res2.status == VerificationStatus.PASS

    def test_one_criterion_fails(self, base_work_order: ProgrammerWorkOrder):
        """If one criterion fails (e.g. test failure or scope violation), overall status is FAIL."""
        exec_id = new_execution_id()
        wo_id = base_work_order.work_order_id

        # Passing test
        ev_test = VerificationEvidence(
            execution_id=exec_id,
            work_order_id=wo_id,
            source_type=VerificationEvidenceSourceType.TEST_RUNNER,
            is_agent_claim=False,
            data={"exit_code": 0},
        )
        chk_test = VerificationCheck(
            execution_id=exec_id,
            work_order_id=wo_id,
            check_type=VerificationCheckType.TEST,
            command="pytest tests/unit",
            status=VerificationStatus.PASS,
            exit_code=0,
            evidence=[ev_test.evidence_id],
        )

        # File modified outside src/auth
        evaluator = AcceptanceCriteriaEvaluator()
        result = evaluator.evaluate(
            work_order=base_work_order,
            execution_id=exec_id,
            checks=[chk_test],
            evidence=[ev_test],
            files_changed=["src/auth/session.py", "README.md"],  # README.md is outside src/auth!
        )

        assert result.overall_status == VerificationStatus.FAIL
        assert result.pass_count == 1
        assert result.fail_count == 1

        scope_res = next(r for r in result.results if r.criterion_id == "crit-scope-1")
        assert scope_res.status == VerificationStatus.FAIL
        assert "README.md" in scope_res.explanation

    def test_criterion_not_verifiable_without_guessing(self, base_work_order: ProgrammerWorkOrder):
        """Unmeasured or unverifiable criteria (e.g. performance) yield NOT_VERIFIED without guessing."""
        exec_id = new_execution_id()
        base_work_order.acceptance_criteria = [
            AcceptanceCriterion(
                criterion_id="crit-perf-1",
                description="Performance must remain below 100ms.",
                criterion_type=AcceptanceCriterionType.CUSTOM,
            )
        ]

        evaluator = AcceptanceCriteriaEvaluator()
        result = evaluator.evaluate(
            work_order=base_work_order,
            execution_id=exec_id,
            checks=[],
            evidence=[],
        )

        assert result.overall_status == VerificationStatus.NOT_VERIFIED
        assert result.not_verified_count == 1
        assert result.pass_count == 0
        assert result.fail_count == 0

        res = result.results[0]
        assert res.status == VerificationStatus.NOT_VERIFIED
        assert "cannot be deterministically verified" in res.explanation

    def test_mixed_pass_and_not_verified(self, base_work_order: ProgrammerWorkOrder):
        """When some pass and some are not verified (with 0 failures), overall status is NOT_VERIFIED."""
        exec_id = new_execution_id()
        wo_id = base_work_order.work_order_id

        base_work_order.acceptance_criteria = [
            AcceptanceCriterion(
                criterion_id="crit-test-1",
                description="All unit tests must pass.",
                criterion_type=AcceptanceCriterionType.TEST_PASS,
            ),
            AcceptanceCriterion(
                criterion_id="crit-perf-1",
                description="Memory usage must stay below 50MB.",
                criterion_type=AcceptanceCriterionType.CUSTOM,
            ),
        ]

        ev_test = VerificationEvidence(
            execution_id=exec_id,
            work_order_id=wo_id,
            source_type=VerificationEvidenceSourceType.TEST_RUNNER,
            is_agent_claim=False,
            data={"exit_code": 0},
        )
        chk_test = VerificationCheck(
            execution_id=exec_id,
            work_order_id=wo_id,
            check_type=VerificationCheckType.TEST,
            command="pytest",
            status=VerificationStatus.PASS,
            exit_code=0,
            evidence=[ev_test.evidence_id],
        )

        evaluator = AcceptanceCriteriaEvaluator()
        result = evaluator.evaluate(
            work_order=base_work_order,
            execution_id=exec_id,
            checks=[chk_test],
            evidence=[ev_test],
        )

        assert result.overall_status == VerificationStatus.NOT_VERIFIED
        assert result.pass_count == 1
        assert result.not_verified_count == 1
        assert result.fail_count == 0

    def test_missing_evidence_yields_not_verified(self, base_work_order: ProgrammerWorkOrder):
        """Criteria declared but with zero observed evidence return NOT_VERIFIED."""
        exec_id = new_execution_id()
        evaluator = AcceptanceCriteriaEvaluator()
        result = evaluator.evaluate(
            work_order=base_work_order,
            execution_id=exec_id,
            checks=[],
            evidence=[],
        )

        test_res = next(r for r in result.results if r.criterion_id == "crit-test-1")
        assert test_res.status == VerificationStatus.NOT_VERIFIED
        assert "No actual test execution evidence" in test_res.explanation

    def test_unrelated_evidence_rejected(self, base_work_order: ProgrammerWorkOrder):
        """Evidence for unrelated commands (e.g. echo) cannot satisfy test criteria."""
        exec_id = new_execution_id()
        wo_id = base_work_order.work_order_id

        ev_echo = VerificationEvidence(
            execution_id=exec_id,
            work_order_id=wo_id,
            source_type=VerificationEvidenceSourceType.COMMAND_OUTPUT,
            source_reference="echo hello",
            is_agent_claim=False,
            data={"command": "echo hello", "exit_code": 0},
        )
        chk_echo = VerificationCheck(
            execution_id=exec_id,
            work_order_id=wo_id,
            check_type=VerificationCheckType.COMMAND,
            command="echo hello",
            status=VerificationStatus.PASS,
            exit_code=0,
            evidence=[ev_echo.evidence_id],
        )

        evaluator = AcceptanceCriteriaEvaluator()
        result = evaluator.evaluate(
            work_order=base_work_order,
            execution_id=exec_id,
            checks=[chk_echo],
            evidence=[ev_echo],
        )

        test_res = next(r for r in result.results if r.criterion_id == "crit-test-1")
        assert test_res.status == VerificationStatus.NOT_VERIFIED
        assert ev_echo.evidence_id not in test_res.evidence

    def test_agent_narration_cannot_satisfy_pass(self, base_work_order: ProgrammerWorkOrder):
        """CRITICAL: Agent claims alone can NEVER produce PASS for an acceptance criterion."""
        exec_id = new_execution_id()
        wo_id = base_work_order.work_order_id

        claim_ev = VerificationEvidence(
            execution_id=exec_id,
            work_order_id=wo_id,
            source_type=VerificationEvidenceSourceType.AGENT_CLAIM,
            description="Cline claims all tests passed successfully",
            is_agent_claim=True,
            data={"claim": "100% tests passed"},
        )

        evaluator = AcceptanceCriteriaEvaluator()
        result = evaluator.evaluate(
            work_order=base_work_order,
            execution_id=exec_id,
            checks=[],
            evidence=[claim_ev],
        )

        test_res = next(r for r in result.results if r.criterion_id == "crit-test-1")
        # Cannot be PASS!
        assert test_res.status == VerificationStatus.NOT_VERIFIED
        assert "Agent claims cannot satisfy PASS" in test_res.explanation

    def test_dependency_confinement_evaluation(self, base_work_order: ProgrammerWorkOrder):
        """Criterion prohibiting dependency modifications fails if package manifests change."""
        exec_id = new_execution_id()
        base_work_order.acceptance_criteria = [
            AcceptanceCriterion(
                criterion_id="crit-dep-1",
                description="No new dependencies added.",
                criterion_type=AcceptanceCriterionType.NO_DEPENDENCY_ADDED,
            )
        ]

        evaluator = AcceptanceCriteriaEvaluator()

        # Case A: requirements.txt modified -> FAIL
        res_fail = evaluator.evaluate(
            work_order=base_work_order,
            execution_id=exec_id,
            files_changed=["requirements.txt", "src/auth.py"],
        )
        assert res_fail.results[0].status == VerificationStatus.FAIL
        assert "requirements.txt" in res_fail.results[0].explanation

        # Case B: only code modified with supporting evidence -> PASS
        ev_fs = VerificationEvidence(
            execution_id=exec_id,
            work_order_id=base_work_order.work_order_id,
            source_type=VerificationEvidenceSourceType.FILESYSTEM,
            description="Modified src/auth.py",
            is_agent_claim=False,
        )
        res_pass = evaluator.evaluate(
            work_order=base_work_order,
            execution_id=exec_id,
            evidence=[ev_fs],
            files_changed=["src/auth.py"],
        )
        assert res_pass.results[0].status == VerificationStatus.PASS
        assert ev_fs.evidence_id in res_pass.results[0].evidence

    def test_empty_acceptance_criteria_rejected(self, base_work_order: ProgrammerWorkOrder):
        """Evaluation on empty acceptance criteria raises ProgrammerValidationError."""
        base_work_order.acceptance_criteria = []
        evaluator = AcceptanceCriteriaEvaluator()
        with pytest.raises(ProgrammerValidationError, match="non-empty acceptance criteria"):
            evaluator.evaluate(
                work_order=base_work_order,
                execution_id=new_execution_id(),
            )

    def test_lineage_preservation_and_mismatch_rejection(self, base_work_order: ProgrammerWorkOrder):
        """Checks or evidence with mismatched execution_id or work_order_id are rejected."""
        exec_id = new_execution_id()
        other_exec_id = new_execution_id()

        mismatched_ev = VerificationEvidence(
            execution_id=other_exec_id,
            work_order_id=base_work_order.work_order_id,
            source_type=VerificationEvidenceSourceType.TEST_RUNNER,
        )

        evaluator = AcceptanceCriteriaEvaluator()
        with pytest.raises(ProgrammerLineageError, match="execution_id"):
            evaluator.evaluate(
                work_order=base_work_order,
                execution_id=exec_id,
                evidence=[mismatched_ev],
            )

    def test_deterministic_evaluation_reproducibility(self, base_work_order: ProgrammerWorkOrder):
        """Running evaluation multiple times produces identical deterministic outcomes."""
        exec_id = new_execution_id()
        wo_id = base_work_order.work_order_id

        ev = VerificationEvidence(
            execution_id=exec_id,
            work_order_id=wo_id,
            source_type=VerificationEvidenceSourceType.TEST_RUNNER,
            is_agent_claim=False,
            data={"exit_code": 0},
        )
        chk = VerificationCheck(
            execution_id=exec_id,
            work_order_id=wo_id,
            check_type=VerificationCheckType.TEST,
            command="pytest tests/unit",
            status=VerificationStatus.PASS,
            exit_code=0,
            evidence=[ev.evidence_id],
        )

        evaluator = AcceptanceCriteriaEvaluator()
        run1 = evaluator.evaluate(
            work_order=base_work_order,
            execution_id=exec_id,
            checks=[chk],
            evidence=[ev],
            files_changed=["src/auth/session.py"],
        )
        run2 = evaluator.evaluate(
            work_order=base_work_order,
            execution_id=exec_id,
            checks=[chk],
            evidence=[ev],
            files_changed=["src/auth/session.py"],
        )

        assert run1.overall_status == run2.overall_status
        assert run1.pass_count == run2.pass_count
        assert run1.fail_count == run2.fail_count
        assert [r.status for r in run1.results] == [r.status for r in run2.results]
        assert [r.explanation for r in run1.results] == [r.explanation for r in run2.results]

    def test_serialization_fidelity_roundtrip(self, base_work_order: ProgrammerWorkOrder):
        """AcceptanceEvaluationResult supports complete dictionary roundtrip."""
        exec_id = new_execution_id()
        wo_id = base_work_order.work_order_id

        ev = VerificationEvidence(
            execution_id=exec_id,
            work_order_id=wo_id,
            source_type=VerificationEvidenceSourceType.TEST_RUNNER,
            is_agent_claim=False,
            data={"exit_code": 0},
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

        evaluator = AcceptanceCriteriaEvaluator()
        eval_res = evaluator.evaluate(
            work_order=base_work_order,
            execution_id=exec_id,
            checks=[chk],
            evidence=[ev],
            files_changed=["src/auth/session.py"],
        )

        d = eval_res.to_dict()
        restored = AcceptanceEvaluationResult.from_dict(d)

        assert restored.overall_status == eval_res.overall_status
        assert restored.execution_id == eval_res.execution_id
        assert restored.work_order_id == eval_res.work_order_id
        assert len(restored.results) == len(eval_res.results)
        assert restored.pass_count == eval_res.pass_count
