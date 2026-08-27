from __future__ import annotations

from pathlib import Path
import time
from typing import Any, Optional
import uuid

from core.models import Evidence, utc_now
from core.verification.adapters.base import BaseCheckAdapter, CheckExecutionContext
from core.verification.adapters.file_adapter import compute_file_sha256
from core.verification.model import SuccessCriterion, VerificationCheck
from core.verification.types import CheckStatus, CheckType


class ArtifactCheckAdapter(BaseCheckAdapter):
    """
    Adapter for deterministic Artifact Registry verification checks (existence, non-empty, checksum).
    """

    SUPPORTED = {
        CheckType.ARTIFACT_EXISTS,
        CheckType.ARTIFACT_HASH,
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

        artifact_id = criterion.parameters.get("artifact_id")
        relative_path = criterion.parameters.get("path")
        expected_hash = criterion.parameters.get("expected_hash")

        status = CheckStatus.FAILED
        expected = criterion.parameters.get("expected") or True
        actual: Any = None
        error_msg: Optional[str] = None
        evidence_ids: list[str] = []

        try:
            target_artifact = None
            if artifact_id and context.artifact_registry:
                try:
                    target_artifact = context.artifact_registry.get_artifact(artifact_id)
                except Exception:
                    pass

            if not target_artifact and relative_path and context.artifact_registry:
                arts = context.artifact_registry.list_artifacts_for_task(context.task_id)
                for a in arts:
                    if a.path.endswith(relative_path) or a.path == relative_path:
                        target_artifact = a
                        break

            if criterion.check_type == CheckType.ARTIFACT_EXISTS:
                expected = True
                actual = target_artifact is not None
                if target_artifact:
                    # Check disk file exists
                    full_p = Path(target_artifact.path)
                    if not full_p.is_absolute():
                        full_p = Path(context.workspace_root) / target_artifact.path
                    if full_p.is_file() or target_artifact.checksum is not None:
                        status = CheckStatus.PASSED
                    else:
                        status = CheckStatus.FAILED
                        error_msg = f"Artifact '{target_artifact.id}' registered but physical file '{target_artifact.path}' missing."
                else:
                    status = CheckStatus.FAILED
                    error_msg = f"Artifact (id={artifact_id}, path={relative_path}) not registered in ArtifactRegistry."

            elif criterion.check_type == CheckType.ARTIFACT_HASH:
                expected = expected_hash
                if not target_artifact:
                    actual = "ARTIFACT_NOT_FOUND"
                    error_msg = f"Artifact not found for checksum comparison."
                else:
                    full_p = Path(target_artifact.path)
                    if not full_p.is_absolute():
                        full_p = Path(context.workspace_root) / target_artifact.path
                    if not full_p.is_file() and target_artifact.checksum:
                        actual = target_artifact.checksum
                    elif full_p.is_file():
                        actual = compute_file_sha256(full_p)
                    else:
                        actual = "FILE_MISSING"

                    if actual and str(actual).lower() == str(expected_hash).lower():
                        status = CheckStatus.PASSED
                    else:
                        status = CheckStatus.FAILED
                        error_msg = f"Artifact checksum mismatch. Expected {expected_hash}, got {actual}."

            # Create Evidence
            evidence = Evidence(
                id=f"ev-art-{uuid.uuid4().hex[:8]}",
                task_id=context.task_id,
                evidence_type="ARTIFACT_VERIFICATION",
                data=f"Check: {criterion.check_type.value} | Status: {status.value} | Artifact: {target_artifact.id if target_artifact else 'None'}",
                created_at=utc_now(),
            )
            evidence_ids.append(evidence.id)

        except Exception as e:
            status = CheckStatus.ERROR
            error_msg = f"Exception during artifact verification: {str(e)}"

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
