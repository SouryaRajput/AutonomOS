from __future__ import annotations

from pathlib import Path
import subprocess
import time
from typing import Any, Optional
import uuid

from core.models import Evidence, utc_now
from core.verification.adapters.base import BaseCheckAdapter, CheckExecutionContext
from core.verification.model import SuccessCriterion, VerificationCheck
from core.verification.types import CheckStatus, CheckType


class GitCheckAdapter(BaseCheckAdapter):
    """
    Adapter for deterministic Git repository checks (diff, status, expected changed files, unexpected files).
    """

    SUPPORTED = {
        CheckType.GIT_DIFF,
        CheckType.GIT_STATUS,
        CheckType.EXPECTED_FILES_CHANGED,
        CheckType.UNEXPECTED_FILES_CHANGED,
    }

    def supports(self, check_type: CheckType) -> bool:
        return check_type in self.SUPPORTED

    def execute(
        self,
        context: CheckExecutionContext,
        criterion: SuccessCriterion,
        verification_id: str,
    ) -> VerificationCheck:
        start_time = time.perf_counter()
        check_id = f"chk-{uuid.uuid4().hex[:8]}"
        root = Path(context.workspace_root).resolve()

        status = CheckStatus.FAILED
        expected = criterion.parameters.get("expected")
        actual: Any = None
        error_msg: Optional[str] = None
        evidence_ids: list[str] = []

        is_git = (root / ".git").is_dir()
        if not is_git:
            # Non-git repository
            return VerificationCheck(
                id=check_id,
                verification_id=verification_id,
                criterion_id=criterion.id,
                check_type=criterion.check_type,
                description=criterion.description or "Git check on non-git workspace",
                expected_result=expected,
                actual_result="NOT_A_GIT_REPOSITORY",
                status=CheckStatus.SKIPPED if not criterion.required else CheckStatus.FAILED,
                required=criterion.required,
                error_message="Project workspace is not a Git repository.",
                duration_ms=(time.perf_counter() - start_time) * 1000.0,
            )

        try:
            if criterion.check_type == CheckType.GIT_STATUS:
                res = subprocess.run(
                    ["git", "status", "--porcelain"],
                    cwd=str(root),
                    capture_output=True,
                    text=True,
                    check=False,
                )
                output = res.stdout.strip()
                expected_clean = criterion.parameters.get("expect_clean", False)
                expected = "CLEAN" if expected_clean else "ANY"
                actual = "CLEAN" if not output else output

                if expected_clean:
                    if not output:
                        status = CheckStatus.PASSED
                    else:
                        status = CheckStatus.FAILED
                        error_msg = f"Expected clean git working tree, but found uncommitted files:\n{output}"
                else:
                    status = CheckStatus.PASSED

            elif criterion.check_type == CheckType.EXPECTED_FILES_CHANGED:
                expected_files = list(criterion.parameters.get("files") or [])
                expected = expected_files

                res = subprocess.run(
                    ["git", "status", "--porcelain"],
                    cwd=str(root),
                    capture_output=True,
                    text=True,
                    check=False,
                )
                changed_files = [line[3:].strip() for line in res.stdout.splitlines() if line.strip()]
                actual = changed_files

                missing_changes = [f for f in expected_files if f not in changed_files]
                if not missing_changes:
                    status = CheckStatus.PASSED
                else:
                    status = CheckStatus.FAILED
                    error_msg = f"Expected changes in {expected_files}, but missing: {missing_changes}"

            elif criterion.check_type == CheckType.UNEXPECTED_FILES_CHANGED:
                allowed_files = list(criterion.parameters.get("allowed_files") or [])
                expected = f"Only {allowed_files}"

                res = subprocess.run(
                    ["git", "status", "--porcelain"],
                    cwd=str(root),
                    capture_output=True,
                    text=True,
                    check=False,
                )
                changed_files = [line[3:].strip() for line in res.stdout.splitlines() if line.strip() and not line[3:].strip().startswith(".autonomos")]
                actual = changed_files

                forbidden_changes = [f for f in changed_files if f not in allowed_files]
                if not forbidden_changes:
                    status = CheckStatus.PASSED
                else:
                    status = CheckStatus.FAILED
                    error_msg = f"Unexpected files modified outside allowed set: {forbidden_changes}"

            elif criterion.check_type == CheckType.GIT_DIFF:
                res = subprocess.run(
                    ["git", "diff"],
                    cwd=str(root),
                    capture_output=True,
                    text=True,
                    check=False,
                )
                diff_text = res.stdout
                actual = f"Diff length: {len(diff_text)} chars"
                status = CheckStatus.PASSED

            # Create Evidence
            evidence = Evidence(
                id=f"ev-git-{uuid.uuid4().hex[:8]}",
                task_id=context.task_id,
                evidence_type="GIT_VERIFICATION",
                data=f"Check: {criterion.check_type.value} | Status: {status.value} | Actual: {actual}",
                created_at=utc_now(),
            )
            evidence_ids.append(evidence.id)

        except Exception as e:
            status = CheckStatus.ERROR
            error_msg = f"Exception during git check: {str(e)}"

        duration_ms = (time.perf_counter() - start_time) * 1000.0

        return VerificationCheck(
            id=check_id,
            verification_id=verification_id,
            criterion_id=criterion.id,
            check_type=criterion.check_type,
            description=criterion.description or f"{criterion.check_type.value}",
            expected_result=expected,
            actual_result=actual,
            status=status,
            required=criterion.required,
            evidence_ids=evidence_ids,
            error_message=error_msg,
            duration_ms=duration_ms,
        )
