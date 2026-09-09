from __future__ import annotations

from typing import Any, Sequence

from core.tester.contracts.boundary import (
    TESTER_ALLOWED_CAPABILITIES,
    TESTER_FORBIDDEN_ACTIONS,
)
from core.tester.contracts.identifiers import validate_work_order_id
from core.tester.errors import (
    TesterBoundaryViolationError,
    TesterLineageError,
    TesterValidationError,
)
from core.tester.types import ForbiddenTesterAction, TestingCapability

MAX_OBJECTIVE_LENGTH = 10_000


class TesterWorkOrderValidator:
    """
    Deterministic validator for TesterWorkOrder authorization contracts.
    
    Ensures that the Manager-authorized work order is:
    - Structurally sound and internally consistent
    - Explicitly scoped to prevent accidental testing
    - Bounded in time and iterations to prevent infinite improvement loops
    - Free of any forbidden implementer actions (e.g. modifying source code, fixing bugs, deploying)
    
    Invariants:
    - Performs ZERO product interaction.
    - Performs ZERO source code modification.
    - Performs ZERO test execution.
    """
    __test__ = False

    @classmethod
    def validate_all(cls, work_order: Any) -> None:
        """Run complete deterministic validation on the given work order."""
        cls.validate_identity_and_lineage(work_order)
        cls.validate_objective(work_order)
        cls.validate_product_artifact(work_order)
        cls.validate_scope(work_order)
        cls.validate_capabilities(work_order)
        cls.validate_acceptance_criteria(work_order)
        cls.validate_quality_thresholds(work_order)
        cls.validate_budgets(work_order)

    @classmethod
    def validate_identity_and_lineage(cls, work_order: Any) -> None:
        """Validate work order identifier and lineage linkages."""
        validate_work_order_id(work_order.work_order_id)

        if not work_order.manager_task_id or not str(work_order.manager_task_id).strip():
            raise TesterLineageError("TesterWorkOrder must have a valid non-empty manager_task_id (ManagerTask link).")

        if not work_order.project_id or not str(work_order.project_id).strip():
            raise TesterLineageError("TesterWorkOrder must have a valid non-empty project_id.")

        if not work_order.correlation_id or not str(work_order.correlation_id).strip():
            raise TesterLineageError("TesterWorkOrder must have a valid non-empty correlation_id.")

        if hasattr(work_order, "task_id") and work_order.task_id and work_order.manager_task_id:
            if work_order.task_id != work_order.manager_task_id:
                raise TesterLineageError(
                    f"Task ID mismatch: manager_task_id '{work_order.manager_task_id}' does not match task_id '{work_order.task_id}'."
                )

    @classmethod
    def validate_objective(cls, work_order: Any) -> None:
        """Validate testing objective is present, non-empty, and bounded."""
        if not work_order.objective or not str(work_order.objective).strip():
            raise TesterValidationError("TesterWorkOrder must have a non-empty objective.", field_name="objective")

        if len(work_order.objective) > MAX_OBJECTIVE_LENGTH:
            raise TesterValidationError(
                f"Objective length exceeds maximum allowed ({MAX_OBJECTIVE_LENGTH} characters).",
                field_name="objective",
            )

    @classmethod
    def validate_product_artifact(cls, work_order: Any) -> None:
        """Validate that a valid product or build artifact is referenced for testing."""
        pa = getattr(work_order, "product_artifact", None)
        if pa is None:
            raise TesterValidationError(
                "TesterWorkOrder must specify a product_artifact to be tested.",
                field_name="product_artifact",
            )
        if isinstance(pa, str) and not pa.strip():
            raise TesterValidationError(
                "product_artifact reference cannot be an empty string.",
                field_name="product_artifact",
            )
        if isinstance(pa, dict) and not pa:
            raise TesterValidationError(
                "product_artifact descriptor cannot be an empty dictionary.",
                field_name="product_artifact",
            )

    @classmethod
    def validate_scope(cls, work_order: Any) -> None:
        """Validate that test scope is explicit and non-empty."""
        scope = getattr(work_order, "test_scope", None)
        if scope is None:
            raise TesterValidationError("TesterWorkOrder must specify a test_scope.", field_name="test_scope")

        if hasattr(scope, "validate"):
            scope.validate()
        elif isinstance(scope, (list, tuple, set)):
            if not scope:
                raise TesterValidationError("test_scope list cannot be empty.", field_name="test_scope")
            for item in scope:
                if not str(item).strip():
                    raise TesterValidationError("test_scope items cannot be blank.", field_name="test_scope")
                if str(item).strip().lower() in ("*", ".*", "all"):
                    raise TesterValidationError(
                        f"Forbidden wildcard '{item}' in test_scope.",
                        field_name="test_scope",
                    )
        elif isinstance(scope, dict):
            if not scope or not any(scope.values()):
                raise TesterValidationError("test_scope dictionary cannot be empty.", field_name="test_scope")

    @classmethod
    def validate_capabilities(cls, work_order: Any) -> None:
        """
        Validate authorized testing capabilities:
        - Must be non-empty.
        - Must not contain any forbidden implementer actions (e.g. MODIFY_SOURCE_CODE, FIX_DEFECT).
        - Must fall within the Global Tester Boundary.
        """
        caps = getattr(work_order, "authorized_capabilities", None)
        if not caps:
            raise TesterValidationError(
                "authorized_capabilities cannot be empty. Tester must be given explicit capability authorizations.",
                field_name="authorized_capabilities",
            )

        forbidden_values = {f.value for f in TESTER_FORBIDDEN_ACTIONS}
        allowed_values = {a.value for a in TESTER_ALLOWED_CAPABILITIES}

        for c in caps:
            c_val = c.value if hasattr(c, "value") else str(c).upper()

            # Check if attempting to authorize forbidden action
            if c_val in forbidden_values:
                raise TesterBoundaryViolationError(
                    action=c_val,
                    reason=f"Cannot authorize forbidden action '{c_val}'. The WorkOrder cannot grant permissions outside the global Tester boundary.",
                )

            # Check if valid TestingCapability
            if c_val not in allowed_values:
                raise TesterValidationError(
                    f"Unknown or invalid testing capability '{c_val}'.",
                    field_name="authorized_capabilities",
                )

    @classmethod
    def validate_acceptance_criteria(cls, work_order: Any) -> None:
        """Validate that explicit acceptance criteria are defined and each criterion is valid."""
        criteria = getattr(work_order, "acceptance_criteria", None)
        if not criteria:
            raise TesterValidationError(
                "acceptance_criteria cannot be empty. Manager must define explicit criteria to evaluate.",
                field_name="acceptance_criteria",
            )

        for ac in criteria:
            if hasattr(ac, "validate"):
                ac.validate()
            elif isinstance(ac, dict):
                if not ac.get("description"):
                    raise TesterValidationError(
                        "Acceptance criterion dictionary missing 'description'.",
                        field_name="acceptance_criteria.description",
                    )
            elif isinstance(ac, str):
                if not ac.strip():
                    raise TesterValidationError(
                        "Acceptance criterion string cannot be blank.",
                        field_name="acceptance_criteria.description",
                    )

    @classmethod
    def validate_quality_thresholds(cls, work_order: Any) -> None:
        """Validate quality thresholds if provided."""
        qt = getattr(work_order, "quality_thresholds", None)
        if qt is not None:
            if hasattr(qt, "validate"):
                qt.validate()

    @classmethod
    def validate_budgets(cls, work_order: Any) -> None:
        """
        Validate testing budgets:
        - time_budget >= 0
        - iteration_budget >= 1 (bounded iterations to prevent infinite improvement loops)
        """
        tb = getattr(work_order, "time_budget", None)
        if tb is None or not isinstance(tb, (int, float)) or tb < 0:
            raise TesterValidationError(
                f"time_budget must be a non-negative number, got {tb}.",
                field_name="time_budget",
            )

        ib = getattr(work_order, "iteration_budget", None)
        if ib is None or not isinstance(ib, int) or ib < 1:
            raise TesterValidationError(
                f"iteration_budget must be an integer >= 1, got {ib}. Tester V1 must have an explicitly bounded iteration budget to prevent infinite improvement loops.",
                field_name="iteration_budget",
            )
