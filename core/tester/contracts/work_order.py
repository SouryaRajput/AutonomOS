from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional, Sequence
import uuid

from core.tester.contracts.boundary import (
    TESTER_ALLOWED_CAPABILITIES,
    TesterBoundaryGuard,
)
from core.tester.contracts.criteria import AcceptanceCriterion
from core.tester.contracts.environment import TestEnvironment
from core.tester.contracts.execution import TesterExecution
from core.tester.contracts.identifiers import (
    new_execution_id,
    new_work_order_id,
    validate_work_order_id,
)
from core.tester.contracts.scope import TestScope
from core.tester.contracts.thresholds import QualityThresholds
from core.tester.contracts.validator import TesterWorkOrderValidator
from core.tester.errors import (
    TesterLineageError,
    TesterValidationError,
)
from core.tester.types import (
    TestCategory,
    TesterExecutionStatus,
    TesterWorkOrderStatus,
    TestingCapability,
)


def utc_now() -> str:
    """Generate ISO 8601 UTC timestamp."""
    return datetime.now(timezone.utc).isoformat()


class TesterWorkOrder:
    """
    Authoritative assignment contract delegated from Manager to Tester.
    Represents the formal root of Tester-side evaluation and establishes causal lineage
    from ManagerTask down through TesterExecution and TesterResult.
    
    The work order defines:
    - WHAT should be tested: objective, product_artifact, source_revision
    - WHY it should be tested: objective, instructions
    - WHAT product/build should be tested: product_artifact, test_environment
    - WHICH areas/flows are in scope: test_scope, required_flows
    - WHICH testing capabilities are authorized: authorized_capabilities
    - WHAT acceptance criteria must be evaluated: acceptance_criteria
    - WHAT constraints apply: constraints
    - HOW much testing is permitted: time_budget, iteration_budget, quality_thresholds
    
    TesterWorkOrder is purely an authorization contract and does NOT execute anything.
    """
    __test__ = False

    def __init__(
        self,
        work_order_id: str,
        manager_task_id: Optional[str] = None,
        project_id: str = "",
        correlation_id: str = "",
        objective: str = "",
        task_id: Optional[str] = None,
        instructions: Optional[list[str]] = None,
        product_artifact: Any = "default_product",
        source_revision: Optional[Any] = None,
        test_scope: Optional[TestScope | dict[str, Any] | list[str]] = None,
        test_categories: Optional[list[TestCategory | str]] = None,
        required_flows: Optional[list[str]] = None,
        acceptance_criteria: Optional[list[AcceptanceCriterion | dict[str, Any] | str]] = None,
        authorized_capabilities: Optional[list[TestingCapability | str]] = None,
        test_environment: Optional[TestEnvironment | dict[str, Any] | str] = None,
        environment_details: Optional[Any] = None,
        constraints: Optional[list[str]] = None,
        quality_thresholds: Optional[QualityThresholds | dict[str, Any]] = None,
        time_budget: int = 300,
        iteration_budget: int = 5,
        status: TesterWorkOrderStatus = TesterWorkOrderStatus.CREATED,
        parent_work_order_id: Optional[str] = None,
        revision_number: int = 1,
        trace: Optional[dict[str, Any]] = None,
        metadata: Optional[dict[str, Any]] = None,
        created_at: Optional[str] = None,
    ):
        self.work_order_id = work_order_id
        self.parent_work_order_id = parent_work_order_id
        self.revision_number = int(revision_number)

        # Resolve task lineage accepting both manager_task_id and task_id
        if manager_task_id and task_id and manager_task_id != task_id:
            raise TesterLineageError(
                f"Task ID mismatch: work order belongs to manager task '{manager_task_id}' but task_id is '{task_id}'."
            )
        resolved_task_id = manager_task_id or task_id or ""
        self.manager_task_id = resolved_task_id
        self.task_id = resolved_task_id

        self.project_id = project_id
        self.correlation_id = correlation_id
        self.objective = objective
        self.instructions = list(instructions or [])
        self.product_artifact = product_artifact
        self.source_revision = source_revision

        # Normalize test_scope
        if isinstance(test_scope, TestScope):
            self.test_scope = test_scope
        elif isinstance(test_scope, dict):
            self.test_scope = TestScope.from_dict(test_scope)
        elif isinstance(test_scope, (list, tuple, set)):
            self.test_scope = TestScope.from_items(list(test_scope))
        elif test_scope is None:
            self.test_scope = TestScope()
        else:
            self.test_scope = TestScope.from_items([str(test_scope)])

        # Normalize test_categories
        norm_cats: list[TestCategory] = []
        for cat in (test_categories or []):
            if isinstance(cat, TestCategory):
                norm_cats.append(cat)
            elif isinstance(cat, str):
                try:
                    norm_cats.append(TestCategory(cat.upper()))
                except (ValueError, KeyError):
                    pass
        self.test_categories = norm_cats

        self.required_flows = list(required_flows or [])

        # Normalize acceptance_criteria
        norm_criteria: list[AcceptanceCriterion] = []
        for ac in (acceptance_criteria or []):
            if isinstance(ac, AcceptanceCriterion):
                norm_criteria.append(ac)
            elif isinstance(ac, dict):
                norm_criteria.append(AcceptanceCriterion.from_dict(ac))
            elif isinstance(ac, str):
                norm_criteria.append(AcceptanceCriterion.from_str(ac))
        self.acceptance_criteria = norm_criteria

        # Normalize authorized_capabilities
        norm_caps: list[TestingCapability] = []
        for cap in (authorized_capabilities or []):
            if isinstance(cap, TestingCapability):
                norm_caps.append(cap)
            elif isinstance(cap, str):
                try:
                    norm_caps.append(TestingCapability(cap.upper()))
                except (ValueError, KeyError):
                    norm_caps.append(cap)  # Let validator handle unknown capability strings
        self.authorized_capabilities = norm_caps

        # Normalize test_environment / environment_details backward compatibility
        env_input = test_environment if test_environment is not None else environment_details
        if isinstance(env_input, TestEnvironment):
            self.test_environment = env_input
        elif isinstance(env_input, dict):
            self.test_environment = TestEnvironment.from_dict(env_input)
        elif isinstance(env_input, str):
            self.test_environment = TestEnvironment(env_name=env_input)
        else:
            self.test_environment = TestEnvironment(env_name="staging")
        self.environment_details = self.test_environment.to_dict()

        self.constraints = list(constraints or [])

        # Normalize quality_thresholds
        if isinstance(quality_thresholds, QualityThresholds):
            self.quality_thresholds = quality_thresholds
        elif isinstance(quality_thresholds, dict):
            self.quality_thresholds = QualityThresholds.from_dict(quality_thresholds)
        elif quality_thresholds is None:
            self.quality_thresholds = QualityThresholds()
        else:
            self.quality_thresholds = quality_thresholds

        self.time_budget = int(time_budget)
        self.iteration_budget = int(iteration_budget)

        if isinstance(status, str):
            try:
                self.status = TesterWorkOrderStatus(status.upper())
            except (ValueError, KeyError):
                self.status = TesterWorkOrderStatus.CREATED
        else:
            self.status = status

        self.trace = dict(trace or {})
        self.metadata = dict(metadata or {})
        self.created_at = created_at or utc_now()

        # Enforce baseline identity and lineage invariants
        validate_work_order_id(self.work_order_id)
        if self.parent_work_order_id:
            validate_work_order_id(self.parent_work_order_id)
        if not self.manager_task_id:
            raise TesterLineageError("TesterWorkOrder must have a valid non-empty manager_task_id (ManagerTask link).")
        if not self.project_id:
            raise TesterLineageError("TesterWorkOrder must have a valid non-empty project_id.")
        if not self.correlation_id:
            raise TesterLineageError("TesterWorkOrder must have a valid non-empty correlation_id.")

    def is_capability_authorized(self, capability: TestingCapability | str) -> bool:
        """
        Check whether a capability is authorized.
        Effective authority is strictly: Global Tester Boundary ∩ WorkOrder Authorization.
        Never the union.
        """
        return TesterBoundaryGuard.is_capability_authorized(capability, self.authorized_capabilities)

    def validate(self) -> None:
        """
        Run full deterministic validation on the work order.
        Performs ZERO external calls, ZERO product interaction, ZERO test execution.
        """
        TesterWorkOrderValidator.validate_all(self)

    def validate_lineage(self, manager_task: Optional[Any] = None) -> None:
        """
        Verify that this work order strictly matches the authorized Manager task.
        Prevents a work order from belonging to or being executed under another Manager task.
        """
        TesterWorkOrderValidator.validate_identity_and_lineage(self)
        if manager_task is not None:
            expected_id = getattr(manager_task, "id", None) or getattr(manager_task, "task_id", None)
            if isinstance(manager_task, str):
                expected_id = manager_task
            if expected_id and self.manager_task_id != str(expected_id):
                raise TesterLineageError(
                    f"Lineage mismatch: work order belongs to task '{self.manager_task_id}', cannot be associated with task '{expected_id}'."
                )
            expected_project = getattr(manager_task, "project_id", None)
            if expected_project and self.project_id != str(expected_project):
                raise TesterLineageError(
                    f"Project mismatch: work order belongs to project '{self.project_id}', but task is in '{expected_project}'."
                )

    def create_revision(
        self,
        modifications: Optional[dict[str, Any]] = None,
        reason: str = "",
        new_work_order_id_val: Optional[str] = None,
    ) -> TesterWorkOrder:
        """
        Create a new revision of this work order with modified boundaries/instructions.
        
        Guarantees:
        - Completely preserves the original work order untouched (immutable history).
        - Generates a new unique work_order_id prefixed with 'two-'.
        - Preserves strict lineage to manager_task_id, project_id, correlation_id.
        - Sets parent_work_order_id = self.work_order_id.
        - Increments revision_number.
        - Revalidates the new work order before returning.
        """
        mods = dict(modifications or {})
        new_meta = dict(self.metadata)
        rev_num = self.revision_number + 1
        new_meta["parent_work_order_id"] = self.work_order_id
        new_meta["revision_number"] = rev_num
        new_meta["revision_reason"] = reason
        new_meta["revised_from"] = self.work_order_id
        if "metadata" in mods:
            new_meta.update(mods.pop("metadata"))

        target_wo_id = new_work_order_id_val or new_work_order_id()

        revised = TesterWorkOrder(
            work_order_id=target_wo_id,
            manager_task_id=self.manager_task_id,
            task_id=self.task_id,
            project_id=self.project_id,
            correlation_id=self.correlation_id,
            objective=mods.get("objective", self.objective),
            instructions=mods.get("instructions", self.instructions),
            product_artifact=mods.get("product_artifact", self.product_artifact),
            source_revision=mods.get("source_revision", self.source_revision),
            test_scope=mods.get("test_scope", self.test_scope),
            test_categories=mods.get("test_categories", self.test_categories),
            required_flows=mods.get("required_flows", self.required_flows),
            acceptance_criteria=mods.get("acceptance_criteria", self.acceptance_criteria),
            authorized_capabilities=mods.get("authorized_capabilities", self.authorized_capabilities),
            test_environment=mods.get("test_environment", self.test_environment),
            constraints=mods.get("constraints", self.constraints),
            quality_thresholds=mods.get("quality_thresholds", self.quality_thresholds),
            time_budget=mods.get("time_budget", self.time_budget),
            iteration_budget=mods.get("iteration_budget", self.iteration_budget),
            status=mods.get("status", TesterWorkOrderStatus.ASSIGNED),
            parent_work_order_id=self.work_order_id,
            revision_number=rev_num,
            trace=self.trace,
            metadata=new_meta,
        )
        revised.validate()
        return revised

    def create_execution(
        self,
        worker_id: str = "worker.tester",
        status: TesterExecutionStatus = TesterExecutionStatus.INITIALIZED,
    ) -> TesterExecution:
        """
        Spawn an active execution attempt for this work order.
        Strictly preserves work_order_id, task_id, project_id, and correlation_id.
        """
        return TesterExecution.from_work_order(
            work_order=self,
            status=status,
            worker_id=worker_id,
        )

    @classmethod
    def from_task(cls, task: Any) -> TesterWorkOrder:
        """
        Construct a strongly-typed TesterWorkOrder from a runtime Manager Task model.
        Guarantees strict lineage preservation.
        """
        task_id = getattr(task, "id", None)
        if not task_id:
            raise TesterLineageError("Cannot create TesterWorkOrder from task without an id.")

        project_id = getattr(task, "project_id", None)
        if not project_id:
            raise TesterLineageError(f"Cannot create TesterWorkOrder from task '{task_id}' without a project_id.")

        meta = dict(getattr(task, "metadata", {}) or {})
        correlation_id = meta.get("correlation_id") or task_id
        objective = getattr(task, "objective", "") or getattr(task, "title", "")

        # Extract criteria
        ac_raw = meta.get("acceptance_criteria", []) or getattr(task, "success_criteria", [])

        # Extract scope
        scope_raw = meta.get("test_scope", [])

        # Extract capabilities (default to standard testing capabilities if not explicitly restricted)
        caps_raw = meta.get("authorized_capabilities", list(TESTER_ALLOWED_CAPABILITIES))

        # Extract artifact and revision
        product_artifact = meta.get("product_artifact") or (
            getattr(task, "artifacts", None)[0] if getattr(task, "artifacts", None) else "default_product"
        )
        source_revision = meta.get("source_revision")

        work_order_id = new_work_order_id()

        return cls(
            work_order_id=work_order_id,
            manager_task_id=task_id,
            project_id=project_id,
            correlation_id=correlation_id,
            objective=objective,
            instructions=list(meta.get("instructions", [])),
            product_artifact=product_artifact,
            source_revision=source_revision,
            test_scope=scope_raw,
            test_categories=meta.get("test_categories", [TestCategory.FUNCTIONAL]),
            required_flows=list(meta.get("required_flows", [])),
            acceptance_criteria=ac_raw,
            authorized_capabilities=caps_raw,
            test_environment=meta.get("test_environment"),
            constraints=list(meta.get("constraints", [])),
            quality_thresholds=meta.get("quality_thresholds"),
            time_budget=int(meta.get("time_budget", 300)),
            iteration_budget=int(meta.get("iteration_budget", 5)),
            status=TesterWorkOrderStatus.ASSIGNED,
            trace=meta.get("trace"),
            metadata=meta,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "work_order_id": self.work_order_id,
            "manager_task_id": self.manager_task_id,
            "task_id": self.task_id,
            "project_id": self.project_id,
            "correlation_id": self.correlation_id,
            "objective": self.objective,
            "instructions": list(self.instructions),
            "product_artifact": (
                self.product_artifact.to_dict()
                if hasattr(self.product_artifact, "to_dict")
                else self.product_artifact
            ),
            "source_revision": (
                self.source_revision.to_dict()
                if hasattr(self.source_revision, "to_dict")
                else self.source_revision
            ),
            "test_scope": self.test_scope.to_dict() if hasattr(self.test_scope, "to_dict") else self.test_scope,
            "test_categories": [
                c.value if hasattr(c, "value") else str(c) for c in self.test_categories
            ],
            "required_flows": list(self.required_flows),
            "acceptance_criteria": [
                a.to_dict() if hasattr(a, "to_dict") else a for a in self.acceptance_criteria
            ],
            "authorized_capabilities": [
                c.value if hasattr(c, "value") else str(c) for c in self.authorized_capabilities
            ],
            "test_environment": (
                self.test_environment.to_dict()
                if hasattr(self.test_environment, "to_dict")
                else self.test_environment
            ),
            "environment_details": (
                self.test_environment.to_dict()
                if hasattr(self.test_environment, "to_dict")
                else self.test_environment
            ),
            "constraints": list(self.constraints),
            "quality_thresholds": (
                self.quality_thresholds.to_dict()
                if hasattr(self.quality_thresholds, "to_dict")
                else self.quality_thresholds
            ),
            "time_budget": self.time_budget,
            "iteration_budget": self.iteration_budget,
            "status": self.status.value if hasattr(self.status, "value") else str(self.status),
            "parent_work_order_id": self.parent_work_order_id,
            "revision_number": self.revision_number,
            "trace": dict(self.trace),
            "metadata": dict(self.metadata),
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> TesterWorkOrder:
        st_raw = data.get("status", TesterWorkOrderStatus.CREATED.value)
        try:
            status = TesterWorkOrderStatus(st_raw)
        except (ValueError, KeyError):
            status = TesterWorkOrderStatus.CREATED

        task_id = data.get("manager_task_id") or data.get("task_id", "")

        return cls(
            work_order_id=data.get("work_order_id", new_work_order_id()),
            manager_task_id=task_id,
            task_id=task_id,
            project_id=data.get("project_id", ""),
            correlation_id=data.get("correlation_id", ""),
            objective=str(data.get("objective", "")),
            instructions=list(data.get("instructions", [])),
            product_artifact=data.get("product_artifact", "default_product"),
            source_revision=data.get("source_revision"),
            test_scope=data.get("test_scope"),
            test_categories=data.get("test_categories"),
            required_flows=list(data.get("required_flows", [])),
            acceptance_criteria=data.get("acceptance_criteria", []),
            authorized_capabilities=data.get("authorized_capabilities", []),
            test_environment=data.get("test_environment"),
            environment_details=data.get("environment_details"),
            constraints=list(data.get("constraints", [])),
            quality_thresholds=data.get("quality_thresholds"),
            time_budget=int(data.get("time_budget", 300)),
            iteration_budget=int(data.get("iteration_budget", 5)),
            status=status,
            parent_work_order_id=data.get("parent_work_order_id"),
            revision_number=int(data.get("revision_number", 1)),
            trace=dict(data.get("trace", {})),
            metadata=dict(data.get("metadata", {})),
            created_at=data.get("created_at", utc_now()),
        )
