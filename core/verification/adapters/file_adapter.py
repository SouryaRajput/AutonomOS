from __future__ import annotations

import hashlib
from pathlib import Path
import re
import time
import uuid

from core.models import Evidence, utc_now
from core.verification.adapters.base import BaseCheckAdapter, CheckExecutionContext
from core.verification.model import SuccessCriterion, VerificationCheck
from core.verification.types import CheckStatus, CheckType


def compute_file_sha256(path: Path) -> str:
    if not path.is_file():
        return ""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(65536):
            h.update(chunk)
    return h.hexdigest()


class FileCheckAdapter(BaseCheckAdapter):
    """
    Adapter for deterministic file system checks: existence, non-existence, checksum, content matching.
    """

    SUPPORTED = {
        CheckType.FILE_EXISTS,
        CheckType.FILE_NOT_EXISTS,
        CheckType.FILE_HASH,
        CheckType.FILE_CONTENT_MATCH,
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
        rel_path = str(criterion.parameters.get("path", "")).strip().lstrip("/\\")
        target = root / rel_path

        status = CheckStatus.FAILED
        expected = criterion.parameters.get("expected")
        actual: Any = None
        error_msg: str | None = None
        evidence_ids: list[str] = []

        try:
            if criterion.check_type == CheckType.FILE_EXISTS:
                expected = True
                actual = target.exists()
                if actual:
                    status = CheckStatus.PASSED
                else:
                    error_msg = f"Expected file '{rel_path}' to exist, but it was missing."

            elif criterion.check_type == CheckType.FILE_NOT_EXISTS:
                expected = False
                actual = target.exists()
                if not actual:
                    status = CheckStatus.PASSED
                else:
                    error_msg = f"Expected file '{rel_path}' to NOT exist, but it exists on disk."

            elif criterion.check_type == CheckType.FILE_HASH:
                expected_hash = str(criterion.parameters.get("expected_hash") or expected or "")
                expected = expected_hash
                if not target.is_file():
                    actual = "FILE_MISSING"
                    error_msg = f"File '{rel_path}' not found for checksum comparison."
                else:
                    actual = compute_file_sha256(target)
                    if actual.lower() == expected_hash.lower():
                        status = CheckStatus.PASSED
                    else:
                        error_msg = f"File '{rel_path}' hash mismatch. Expected {expected_hash}, got {actual}."

            elif criterion.check_type == CheckType.FILE_CONTENT_MATCH:
                pattern = str(criterion.parameters.get("pattern") or expected or "")
                expected = pattern
                if not target.is_file():
                    actual = "FILE_MISSING"
                    error_msg = f"File '{rel_path}' not found for content matching."
                else:
                    content = target.read_text(encoding="utf-8", errors="replace")
                    is_regex = bool(criterion.parameters.get("is_regex", False))
                    matched = bool(re.search(pattern, content)) if is_regex else pattern in content
                    actual = f"Matched: {matched}"
                    if matched:
                        status = CheckStatus.PASSED
                    else:
                        error_msg = f"File '{rel_path}' did not match expected pattern: '{pattern[:100]}'."

            # Record Evidence
            evidence = Evidence(
                id=f"ev-file-{uuid.uuid4().hex[:8]}",
                task_id=context.task_id,
                evidence_type="FILE_VERIFICATION",
                data=f"Check: {criterion.check_type.value} | Path: {rel_path} | Status: {status.value} | Actual: {actual}",
                created_at=utc_now(),
            )
            evidence_ids.append(evidence.id)

        except Exception as e:
            status = CheckStatus.ERROR
            error_msg = f"Exception during file check: {str(e)}"

        duration_ms = (time.perf_counter() - start_time) * 1000.0

        return VerificationCheck(
            id=check_id,
            verification_id=verification_id,
            criterion_id=criterion.id,
            check_type=criterion.check_type,
            description=criterion.description or f"{criterion.check_type.value} on {rel_path}",
            expected_result=expected,
            actual_result=actual,
            status=status,
            required=criterion.required,
            evidence_ids=evidence_ids,
            error_message=error_msg,
            duration_ms=duration_ms,
            metadata={"target_path": rel_path},
        )
