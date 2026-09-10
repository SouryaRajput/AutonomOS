from __future__ import annotations

from datetime import datetime, timezone
import logging
from typing import Any, Optional, Sequence, Union

from core.tester.contracts.identifiers import (
    new_network_performance_id,
    validate_execution_id,
)
from core.tester.contracts.network_performance import (
    NetworkPerformanceResult,
    NetworkPerformanceSpec,
    NetworkRequestRecord,
)
from core.tester.contracts.performance import PerformanceMeasurement
from core.tester.evaluator.performance_recorder import (
    PerformanceMeasurementRecorder,
)
from core.tester.errors import (
    TesterBoundaryViolationError,
    TesterLineageError,
    TesterValidationError,
)
from core.tester.types import (
    DefectType,
    NetworkPerformanceStatus,
    PerformanceMetricType,
    PerformanceMetricUnit,
    ResourceImportance,
    ResourceType,
)

logger = logging.getLogger("AutonomOS.Tester.NetworkPerformanceEvaluator")


def utc_now() -> str:
    """Generate ISO 8601 UTC timestamp."""
    return datetime.now(timezone.utc).isoformat()


class NetworkPerformanceEvaluator:
    """
    Deterministic evaluator measuring authorized network and resource performance.
    Evaluates requests, latencies, resource failures, and explicit request budgets
    without load/stress generation or duplicate defect creation.
    """
    __test__ = False

    def __init__(
        self,
        execution_id: str,
        project_id: str,
        work_order_id: Optional[str] = None,
        recorder: Optional[PerformanceMeasurementRecorder] = None,
    ) -> None:
        validate_execution_id(execution_id)
        if not project_id or not str(project_id).strip():
            raise TesterLineageError("NetworkPerformanceEvaluator requires a valid non-empty project_id.")

        self.execution_id = execution_id
        self.project_id = str(project_id).strip()
        self.work_order_id = work_order_id

        if recorder is not None:
            if recorder.execution_id != execution_id:
                raise TesterLineageError(
                    f"Recorder execution_id '{recorder.execution_id}' does not match evaluator execution_id '{execution_id}'."
                )
            self.recorder = recorder
        else:
            self.recorder = PerformanceMeasurementRecorder(
                execution_id=self.execution_id,
                project_id=self.project_id,
                work_order_id=self.work_order_id,
            )

    def evaluate_network_activity(
        self,
        spec: NetworkPerformanceSpec,
        observed_requests: Sequence[Union[NetworkRequestRecord, dict[str, Any]]],
    ) -> NetworkPerformanceResult:
        """
        Evaluate authorized network activity against explicit expectations in spec.
        Never generates load or stresses the system; only inspects observed traffic.
        """
        if spec.execution_id and spec.execution_id != self.execution_id:
            raise TesterLineageError(
                f"Spec execution_id '{spec.execution_id}' does not match evaluator execution_id '{self.execution_id}'."
            )

        net_perf_id = new_network_performance_id()
        started_at = utc_now()

        # 1. Normalize requests
        requests: list[NetworkRequestRecord] = []
        for item in observed_requests:
            if isinstance(item, NetworkRequestRecord):
                rec = item
            elif isinstance(item, dict):
                rec = NetworkRequestRecord.from_dict(item)
            else:
                continue

            # Check explicit spec criticality overrides
            url_str = rec.url
            if any(crit in url_str for crit in spec.critical_resources):
                rec.importance = ResourceImportance.REQUIRED
            elif any(opt in url_str for opt in spec.optional_resources):
                rec.importance = ResourceImportance.OPTIONAL

            requests.append(rec)

        total_requests = len(requests)
        failed_requests_list: list[NetworkRequestRecord] = []
        resource_failures: list[dict[str, Any]] = []

        durations: list[float] = []
        sizes: list[int] = []
        slow_requests_count = 0

        # 2. Analyze individual requests
        for req in requests:
            if req.duration_ms is not None:
                durations.append(req.duration_ms)
                if (
                    spec.request_duration_threshold_ms is not None
                    and req.duration_ms > spec.request_duration_threshold_ms
                ):
                    slow_requests_count += 1

            if req.transfer_size_bytes is not None:
                sizes.append(req.transfer_size_bytes)

            if req.is_failed:
                failed_requests_list.append(req)
                classification = self.classify_resource_failure(req)
                resource_failures.append({
                    "request_id": req.request_id,
                    "url": req.url,
                    "status_code": req.status_code,
                    "importance": req.importance.value,
                    "resource_type": req.resource_type.value,
                    "classification": classification,
                })

        avg_duration = sum(durations) / len(durations) if durations else None
        max_duration = max(durations) if durations else None
        total_transfer_size = sum(sizes) if sizes else None

        # 3. Evaluate Explicit Thresholds and Budgets (never invent universal defaults)
        duration_threshold_met: Optional[bool] = None
        if spec.request_duration_threshold_ms is not None:
            duration_threshold_met = slow_requests_count == 0

        request_budget_met: Optional[bool] = None
        if spec.max_requests_budget is not None:
            request_budget_met = total_requests <= spec.max_requests_budget

        # 4. Determine Overall Outcome Status
        # Required resource failures fail the evaluation
        has_required_failure = any(
            req.is_failed and req.is_required for req in requests
        )
        has_optional_failure = any(
            req.is_failed and req.is_optional for req in requests
        )

        if has_required_failure:
            status = NetworkPerformanceStatus.FAILED
            error_msg = f"{len(failed_requests_list)} request(s) failed, including critical required resources."
        elif request_budget_met is False:
            status = NetworkPerformanceStatus.EXCESSIVE_REQUESTS
            error_msg = f"Observed {total_requests} requests, exceeding budget of {spec.max_requests_budget}."
        elif duration_threshold_met is False or has_optional_failure:
            status = NetworkPerformanceStatus.DEGRADED
            error_msg = (
                f"{slow_requests_count} slow request(s) exceeded duration threshold"
                if duration_threshold_met is False
                else "Optional non-critical resource failures observed."
            )
        else:
            status = NetworkPerformanceStatus.SUCCESS
            error_msg = None

        # 5. Record Measurements in Recorder
        measurements: list[PerformanceMeasurement] = []
        m_tot = self.recorder.record_metric(
            metric=PerformanceMetricType.TOTAL_REQUESTS,
            value=float(total_requests),
            unit=PerformanceMetricUnit.COUNT,
            test_case_id=spec.test_case_id,
            threshold_context={"max_budget": spec.max_requests_budget} if spec.max_requests_budget else None,
        )
        measurements.append(m_tot)

        m_fail = self.recorder.record_metric(
            metric=PerformanceMetricType.FAILED_REQUESTS,
            value=float(len(failed_requests_list)),
            unit=PerformanceMetricUnit.COUNT,
            test_case_id=spec.test_case_id,
        )
        measurements.append(m_fail)

        if avg_duration is not None:
            m_dur = self.recorder.record_metric(
                metric=PerformanceMetricType.REQUEST_DURATION,
                value=float(avg_duration),
                unit=PerformanceMetricUnit.MILLISECONDS,
                test_case_id=spec.test_case_id,
                threshold_context={"threshold_ms": spec.request_duration_threshold_ms} if spec.request_duration_threshold_ms else None,
            )
            measurements.append(m_dur)

        if total_transfer_size is not None:
            m_size = self.recorder.record_metric(
                metric=PerformanceMetricType.TRANSFER_SIZE,
                value=float(total_transfer_size),
                unit=PerformanceMetricUnit.BYTES,
                test_case_id=spec.test_case_id,
            )
            measurements.append(m_size)

        completed_at = utc_now()
        provenance = {
            "total_requests": total_requests,
            "failed_requests": len(failed_requests_list),
            "slow_requests": slow_requests_count,
            "has_required_failure": has_required_failure,
        }

        return NetworkPerformanceResult(
            network_performance_id=net_perf_id,
            execution_id=self.execution_id,
            spec=spec,
            status=status,
            requests=requests,
            total_requests=total_requests,
            failed_requests=len(failed_requests_list),
            slow_requests=slow_requests_count,
            total_transfer_size_bytes=total_transfer_size,
            avg_request_duration_ms=avg_duration,
            max_request_duration_ms=max_duration,
            request_budget_met=request_budget_met,
            duration_threshold_met=duration_threshold_met,
            measurements=measurements,
            resource_failures=resource_failures,
            error_message=error_msg,
            provenance=provenance,
            timestamps={"started_at": started_at, "completed_at": completed_at},
        )

    def classify_resource_failure(
        self,
        request_record: NetworkRequestRecord,
    ) -> dict[str, Any]:
        """
        Coordinate resource failure classification with existing evaluation layers.
        Prevents duplicate defect generation across performance and functional domains:
        - Required 404 is identified as primarily a RESOURCE/FUNCTIONAL defect.
        - Optional 404 is identified as an optional warning/observation without defect creation.
        - API 500 is identified as an API functional error.
        - Network drop / connection error is identified as an environmental / network fault.
        """
        url = request_record.url
        code = request_record.status_code

        if request_record.is_404:
            if request_record.is_required:
                return {
                    "classification": "FUNCTIONAL_RESOURCE_DEFECT",
                    "recommended_defect_type": DefectType.FUNCTIONAL.value,
                    "target_layer": "functional_acceptance",
                    "reason": f"Required application resource '{url}' returned 404 Not Found. Linked to functional evaluation; no duplicate performance defect created.",
                    "is_performance_defect": False,
                }
            else:
                return {
                    "classification": "OPTIONAL_RESOURCE_WARNING",
                    "recommended_defect_type": None,
                    "target_layer": "observation_only",
                    "reason": f"Optional resource '{url}' returned 404 Not Found. Handled as non-critical warning.",
                    "is_performance_defect": False,
                }

        if request_record.is_500:
            return {
                "classification": "API_SERVER_ERROR",
                "recommended_defect_type": DefectType.FUNCTIONAL.value,
                "target_layer": "functional_acceptance",
                "reason": f"API request to '{url}' failed with server error {code}. Linked to functional evaluation.",
                "is_performance_defect": False,
            }

        if code is None or code == 0:
            return {
                "classification": "NETWORK_CONNECTION_DROPPED",
                "recommended_defect_type": DefectType.FUNCTIONAL.value,
                "target_layer": "runtime_network",
                "reason": f"Request to '{url}' failed to reach host or connection was dropped.",
                "is_performance_defect": False,
            }

        return {
            "classification": "HTTP_CLIENT_ERROR",
            "recommended_defect_type": DefectType.FUNCTIONAL.value,
            "target_layer": "functional_acceptance",
            "reason": f"Request to '{url}' failed with status {code}.",
            "is_performance_defect": False,
        }

    # Invariant guards
    def apply_fix(self, *args: Any, **kwargs: Any) -> None:
        raise TesterBoundaryViolationError(
            action="PERFORMANCE_AUTO_FIX",
            reason="Network performance evaluator cannot modify application code or apply fixes.",
        )

    def auto_fix(self, *args: Any, **kwargs: Any) -> None:
        raise TesterBoundaryViolationError(
            action="PERFORMANCE_AUTO_FIX",
            reason="Network performance evaluator cannot modify application code or apply fixes.",
        )

    def create_defect(self, *args: Any, **kwargs: Any) -> None:
        raise TesterBoundaryViolationError(
            action="PERFORMANCE_DEFECT_CLASSIFICATION",
            reason="Network performance evaluator does not classify defects in Phase 7.4.",
        )
