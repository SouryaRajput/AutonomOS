from __future__ import annotations

from typing import Any, Optional, Sequence

from core.tester.errors import TesterBoundaryViolationError
from core.tester.types import ForbiddenTesterAction, TestingCapability

# Explicit definitions of what Tester MAY and MUST NOT do
TESTER_ALLOWED_CAPABILITIES = frozenset(TestingCapability)

TESTER_FORBIDDEN_ACTIONS = frozenset({
    ForbiddenTesterAction.MODIFY_SOURCE_CODE,
    ForbiddenTesterAction.FIX_DEFECT,
    ForbiddenTesterAction.CHANGE_REQUIREMENTS,
    ForbiddenTesterAction.EXPAND_TEST_SCOPE,
    ForbiddenTesterAction.GRANT_PERMISSIONS,
    ForbiddenTesterAction.DEPLOY_PRODUCT,
    ForbiddenTesterAction.MERGE_CODE,
    ForbiddenTesterAction.OVERRIDE_MANAGER_DECISION,
    ForbiddenTesterAction.SUBJECTIVE_DEFECT_ENFORCEMENT,
    ForbiddenTesterAction.INFINITE_IMPROVEMENT_LOOP,
})


class TesterBoundaryGuard:
    """
    Enforces the Tester responsibility boundary.
    
    Tester is strictly an evaluator, not an implementer.
    The Manager remains the sole organizational authority.
    
    Guarantees:
    - Tester cannot modify product source code or attempt defect fixes.
    - Tester cannot alter product requirements or override Manager decisions.
    - Tester cannot expand its authorized test scope or grant itself permissions.
    - Tester cannot deploy the product or merge code.
    - Tester cannot enforce subjective preferences as mandatory defects or loop indefinitely.
    """
    __test__ = False

    @classmethod
    def assert_action_permitted(cls, action: str | ForbiddenTesterAction) -> None:
        """
        Verify that an action does not violate the forbidden boundary.
        Raises TesterBoundaryViolationError if the action is prohibited.
        """
        action_str = action.value if isinstance(action, ForbiddenTesterAction) else str(action).upper()

        forbidden_names = {f.value for f in TESTER_FORBIDDEN_ACTIONS}
        if action_str in forbidden_names:
            raise TesterBoundaryViolationError(
                action=action_str,
                reason=f"Action '{action_str}' is forbidden for Tester. Tester is an evaluator, not an implementer.",
            )

        # Keyword checks for common prohibited actions
        prohibited_keywords = {
            "EDIT_FILE": "Tester cannot modify source code.",
            "WRITE_SOURCE": "Tester cannot write to product source files.",
            "FIX_BUG": "Tester cannot fix defects; defect resolution belongs to Programmer.",
            "DEPLOY": "Tester cannot deploy the product.",
            "MERGE": "Tester cannot merge code.",
            "OVERRIDE_DECISION": "Tester cannot override Manager decisions.",
            "EXPAND_SCOPE": "Tester cannot self-expand authorized testing scope.",
            "GRANT_PERMISSION": "Tester cannot grant itself permissions.",
        }
        for kw, reason in prohibited_keywords.items():
            if kw in action_str:
                raise TesterBoundaryViolationError(action=action_str, reason=reason)

    @classmethod
    def is_action_forbidden(cls, action: str | ForbiddenTesterAction) -> bool:
        """Return True if the action violates the forbidden boundary."""
        action_str = action.value if isinstance(action, ForbiddenTesterAction) else str(action).upper()
        forbidden_names = {f.value for f in TESTER_FORBIDDEN_ACTIONS}
        if action_str in forbidden_names:
            return True
        prohibited_keywords = (
            "EDIT_FILE",
            "WRITE_SOURCE",
            "FIX_BUG",
            "DEPLOY",
            "MERGE",
            "OVERRIDE_DECISION",
            "EXPAND_SCOPE",
            "GRANT_PERMISSION",
        )
        return any(kw in action_str for kw in prohibited_keywords)

    @classmethod
    def is_action_permitted(cls, action: str | ForbiddenTesterAction) -> bool:
        """Return True if the action is permitted under the global Tester boundary."""
        return not cls.is_action_forbidden(action)

    @classmethod
    def assert_write_access_permitted(cls, file_path: str, is_artifact_or_report: bool = False) -> None:
        """
        Verify that file write access is prohibited for product source code.
        Tester may only write test artifacts/reports, never product source files.
        """
        if not is_artifact_or_report:
            raise TesterBoundaryViolationError(
                action="MODIFY_SOURCE_CODE",
                reason=f"Attempted write to product file '{file_path}'. Tester MUST NOT modify product source code.",
            )

    @classmethod
    def assert_scope_bounded(cls, target: str, authorized_scopes: Sequence[str]) -> None:
        """
        Verify that the target component, endpoint, or test suite falls strictly within
        the Manager-authorized testing scope.
        """
        if not authorized_scopes:
            return  # Empty scope defaults to authorized work order objective
        # If explicit authorized scopes are given, target must match at least one
        target_norm = target.strip().lower()
        if not any(target_norm.startswith(s.strip().lower()) or s.strip().lower() in target_norm for s in authorized_scopes):
            raise TesterBoundaryViolationError(
                action="EXPAND_TEST_SCOPE",
                reason=f"Target '{target}' is outside Manager-authorized test scope: {list(authorized_scopes)}.",
            )

    @classmethod
    def assert_objective_evaluation(cls, is_subjective: bool, treated_as_mandatory: bool) -> None:
        """
        Ensure Tester does not treat subjective preferences as mandatory defects.
        Subjective observations must be recorded as recommendations or findings, not blockers.
        """
        if is_subjective and treated_as_mandatory:
            raise TesterBoundaryViolationError(
                action="SUBJECTIVE_DEFECT_ENFORCEMENT",
                reason="Tester MUST NOT treat subjective preferences as mandatory defects.",
            )

    @classmethod
    def is_capability_authorized(
        cls,
        capability: TestingCapability | str,
        authorized_capabilities: Sequence[TestingCapability | str],
    ) -> bool:
        """
        Determine whether a capability is authorized.
        Effective authority is strictly: Global Tester Boundary ∩ WorkOrder Authorization.
        Never the union.
        """
        cap_val = capability.value if hasattr(capability, "value") else str(capability).upper()

        # Check global boundary: must not be in forbidden actions
        forbidden_names = {f.value for f in TESTER_FORBIDDEN_ACTIONS}
        if cap_val in forbidden_names:
            return False

        # Must be recognized in global allowed capabilities
        allowed_names = {c.value for c in TESTER_ALLOWED_CAPABILITIES}
        if cap_val not in allowed_names:
            return False

        # WorkOrder-level restriction: must be explicitly in WorkOrder authorization
        wo_cap_names = {
            (c.value if hasattr(c, "value") else str(c).upper())
            for c in authorized_capabilities
        }
        return cap_val in wo_cap_names

    @classmethod
    def is_capability_allowed(cls, capability: TestingCapability | str) -> bool:
        """Determine whether a capability is permitted under the global Tester boundary."""
        cap_val = capability.value if hasattr(capability, "value") else str(capability).upper()
        forbidden_names = {f.value for f in TESTER_FORBIDDEN_ACTIONS}
        if cap_val in forbidden_names:
            return False
        allowed_names = {c.value for c in TESTER_ALLOWED_CAPABILITIES}
        return cap_val in allowed_names

    @classmethod
    def assert_capability_authorized(
        cls,
        capability: TestingCapability | str,
        authorized_capabilities: Sequence[TestingCapability | str],
    ) -> None:
        """
        Verify that a requested testing capability is authorized under both the
        global Tester boundary and the assignment-level WorkOrder authorization.
        Raises TesterBoundaryViolationError if either check fails.
        """
        cap_val = capability.value if hasattr(capability, "value") else str(capability).upper()

        # 1. Global Boundary Check
        cls.assert_action_permitted(cap_val)

        allowed_names = {c.value for c in TESTER_ALLOWED_CAPABILITIES}
        if cap_val not in allowed_names:
            raise TesterBoundaryViolationError(
                action=cap_val,
                reason=f"Capability '{cap_val}' is not a permitted Tester capability under the global Tester boundary.",
            )

        # 2. Assignment-level WorkOrder Authorization Check
        wo_cap_names = {
            (c.value if hasattr(c, "value") else str(c).upper())
            for c in authorized_capabilities
        }
        if cap_val not in wo_cap_names:
            raise TesterBoundaryViolationError(
                action=cap_val,
                reason=f"Capability '{cap_val}' is within the global Tester boundary but is NOT authorized in this WorkOrder. Authorized: {list(wo_cap_names)}.",
            )

