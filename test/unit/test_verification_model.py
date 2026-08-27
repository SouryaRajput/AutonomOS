import unittest

from core.verification.model import (
    SuccessCriterion,
    Verification,
    VerificationCheck,
    VerificationPlan,
    VerificationResult,
)
from core.verification.types import CheckStatus, CheckType, VerificationStatus


class TestVerificationModel(unittest.TestCase):

    def test_success_criterion_serialization(self):
        crit = SuccessCriterion(
            id="crit-1",
            description="Verify app.py exists",
            check_type=CheckType.FILE_EXISTS,
            parameters={"path": "app.py"},
            required=True,
            metadata={"tag": "core"},
        )
        d = crit.to_dict()
        self.assertEqual(d["id"], "crit-1")
        self.assertEqual(d["check_type"], "FILE_EXISTS")
        self.assertTrue(d["required"])

        restored = SuccessCriterion.from_dict(d)
        self.assertEqual(restored.id, crit.id)
        self.assertEqual(restored.check_type, CheckType.FILE_EXISTS)
        self.assertEqual(restored.parameters["path"], "app.py")

    def test_verification_check_serialization(self):
        check = VerificationCheck(
            id="chk-1",
            verification_id="verif-1",
            criterion_id="crit-1",
            check_type=CheckType.BUILD,
            description="Verify build passes",
            expected_result={"exit_code": 0},
            actual_result={"exit_code": 0, "output": "Build succeeded"},
            status=CheckStatus.PASSED,
            required=True,
            evidence_ids=["ev-1", "ev-2"],
            duration_ms=145.2,
        )
        d = check.to_dict()
        self.assertEqual(d["status"], "PASSED")
        self.assertEqual(d["duration_ms"], 145.2)

        restored = VerificationCheck.from_dict(d)
        self.assertEqual(restored.id, check.id)
        self.assertEqual(restored.status, CheckStatus.PASSED)
        self.assertEqual(len(restored.evidence_ids), 2)

    def test_verification_plan_and_session_serialization(self):
        crit1 = SuccessCriterion(
            id="c1",
            description="Main file exists",
            check_type=CheckType.FILE_EXISTS,
            parameters={"path": "main.py"},
        )
        crit2 = SuccessCriterion(
            id="c2",
            description="Tests pass",
            check_type=CheckType.TEST_SUITE,
            parameters={"command": "pytest"},
        )
        plan = VerificationPlan(id="plan-1", task_id="task-1", criteria=[crit1, crit2])
        plan_dict = plan.to_dict()
        self.assertEqual(len(plan_dict["criteria"]), 2)

        restored_plan = VerificationPlan.from_dict(plan_dict)
        self.assertEqual(len(restored_plan.criteria), 2)

        verif = Verification(
            id="verif-100",
            project_id="proj-1",
            task_id="task-1",
            status=VerificationStatus.PASSED,
            summary="All checks passed",
            checks=[
                VerificationCheck(
                    id="chk-1",
                    verification_id="verif-100",
                    check_type=CheckType.FILE_EXISTS,
                    description="Exists",
                    status=CheckStatus.PASSED,
                )
            ],
            evidence_ids=["ev-1"],
        )
        v_dict = verif.to_dict()
        self.assertEqual(v_dict["status"], "PASSED")
        restored_v = Verification.from_dict(v_dict)
        self.assertEqual(restored_v.id, "verif-100")
        self.assertEqual(len(restored_v.checks), 1)


if __name__ == "__main__":
    unittest.main()
