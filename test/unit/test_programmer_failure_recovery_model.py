"""
Unit Tests for Programmer V1 Phase 5.1:
Failure Taxonomy & Recovery Model.

Validates the deterministic domain contracts for Programmer failures:
1. Comprehensive coverage of all 15 Failure Categories.
2. Comprehensive coverage of all 6 Recovery Dispositions.
3. Policy resolution: determine_default_disposition() determinism.
4. Retryable vs Non-retryable distinction.
5. Policy contradiction guards (Scope/Permission/Cancellation/Budget cannot violate invariants).
6. Lineage invariants (execution_id, work_order_id, failure_id).
7. Evidence references and trace propagation.
8. Serialization roundtrips (to_dict, from_dict, to_json).
9. Helper convenience properties.
10. Enum case-insensitivity and fallback behavior.
"""

from __future__ import annotations

import json
import unittest

from core.programmer.contracts.failure import (
    CATEGORY_POLICY_MAP,
    ProgrammerFailure,
    determine_default_disposition,
)
from core.programmer.contracts.identifiers import (
    FAILURE_ID_PREFIX,
    new_execution_id,
    new_failure_id,
    new_work_order_id,
    validate_failure_id,
)
from core.programmer.errors import (
    InvalidProgrammerIdError,
    ProgrammerLineageError,
    ProgrammerValidationError,
)
from core.programmer.types import (
    FailureSourceType,
    ProgrammerFailureCategory,
    ProgrammerFailureSeverity,
    RecoveryDisposition,
)


class TestProgrammerFailureTaxonomyAndRecoveryModel(unittest.TestCase):
    def setUp(self) -> None:
        self.exec_id = new_execution_id()
        self.wo_id = new_work_order_id()

    # -------------------------------------------------------------------------
    # 1. Failure Categories & Dispositions Completeness
    # -------------------------------------------------------------------------

    def test_all_fifteen_failure_categories_exist_and_map_deterministically(self) -> None:
        expected_categories = [
            ProgrammerFailureCategory.AGENT_STARTUP,
            ProgrammerFailureCategory.AGENT_CRASH,
            ProgrammerFailureCategory.AGENT_HUNG,
            ProgrammerFailureCategory.COMMAND_FAILURE,
            ProgrammerFailureCategory.VERIFICATION_FAILURE,
            ProgrammerFailureCategory.TIMEOUT,
            ProgrammerFailureCategory.CANCELLATION,
            ProgrammerFailureCategory.PERMISSION,
            ProgrammerFailureCategory.SCOPE,
            ProgrammerFailureCategory.RESOURCE,
            ProgrammerFailureCategory.BUDGET,
            ProgrammerFailureCategory.DEPENDENCY,
            ProgrammerFailureCategory.MISSING_CONTEXT,
            ProgrammerFailureCategory.INTERNAL,
            ProgrammerFailureCategory.UNKNOWN,
        ]
        self.assertEqual(len(expected_categories), 15)

        for cat in expected_categories:
            retryable, disposition = determine_default_disposition(cat)
            self.assertIsInstance(retryable, bool)
            self.assertIsInstance(disposition, RecoveryDisposition)
            self.assertIn(cat, CATEGORY_POLICY_MAP)

    def test_all_six_recovery_dispositions_exist(self) -> None:
        expected_dispositions = {
            RecoveryDisposition.RETRY,
            RecoveryDisposition.CORRECT,
            RecoveryDisposition.ESCALATE,
            RecoveryDisposition.BLOCK,
            RecoveryDisposition.FAIL,
            RecoveryDisposition.CANCEL,
        }
        all_enum_dispositions = set(RecoveryDisposition)
        self.assertEqual(expected_dispositions, all_enum_dispositions)

    # -------------------------------------------------------------------------
    # 2. Policy Resolution & Severity Behavior
    # -------------------------------------------------------------------------

    def test_agent_transient_failures_default_to_retry(self) -> None:
        for cat in (
            ProgrammerFailureCategory.AGENT_STARTUP,
            ProgrammerFailureCategory.AGENT_CRASH,
            ProgrammerFailureCategory.AGENT_HUNG,
        ):
            retryable, disp = determine_default_disposition(cat, ProgrammerFailureSeverity.HIGH)
            self.assertTrue(retryable)
            self.assertEqual(disp, RecoveryDisposition.RETRY)

    def test_critical_severity_on_crash_or_hung_escalates(self) -> None:
        for cat in (
            ProgrammerFailureCategory.AGENT_CRASH,
            ProgrammerFailureCategory.AGENT_HUNG,
        ):
            retryable, disp = determine_default_disposition(cat, ProgrammerFailureSeverity.CRITICAL)
            self.assertFalse(retryable)
            self.assertEqual(disp, RecoveryDisposition.ESCALATE)

    def test_verification_failure_maps_to_correct_not_retry(self) -> None:
        retryable, disp = determine_default_disposition(ProgrammerFailureCategory.VERIFICATION_FAILURE)
        self.assertFalse(retryable)
        self.assertEqual(disp, RecoveryDisposition.CORRECT)

    def test_permission_and_scope_failures_map_to_block(self) -> None:
        for cat in (ProgrammerFailureCategory.PERMISSION, ProgrammerFailureCategory.SCOPE):
            retryable, disp = determine_default_disposition(cat)
            self.assertFalse(retryable)
            self.assertEqual(disp, RecoveryDisposition.BLOCK)

    def test_cancellation_maps_to_cancel(self) -> None:
        retryable, disp = determine_default_disposition(ProgrammerFailureCategory.CANCELLATION)
        self.assertFalse(retryable)
        self.assertEqual(disp, RecoveryDisposition.CANCEL)

    def test_budget_exhaustion_maps_to_fail(self) -> None:
        retryable, disp = determine_default_disposition(ProgrammerFailureCategory.BUDGET)
        self.assertFalse(retryable)
        self.assertEqual(disp, RecoveryDisposition.FAIL)

    def test_dependency_and_missing_context_map_to_block(self) -> None:
        for cat in (ProgrammerFailureCategory.DEPENDENCY, ProgrammerFailureCategory.MISSING_CONTEXT):
            retryable, disp = determine_default_disposition(cat)
            self.assertFalse(retryable)
            self.assertEqual(disp, RecoveryDisposition.BLOCK)

    def test_resource_maps_to_escalate(self) -> None:
        retryable, disp = determine_default_disposition(ProgrammerFailureCategory.RESOURCE)
        self.assertFalse(retryable)
        self.assertEqual(disp, RecoveryDisposition.ESCALATE)

    # -------------------------------------------------------------------------
    # 3. Factory Method & Validation
    # -------------------------------------------------------------------------

    def test_create_factory_populates_valid_defaults(self) -> None:
        failure = ProgrammerFailure.create(
            execution_id=self.exec_id,
            work_order_id=self.wo_id,
            category=ProgrammerFailureCategory.COMMAND_FAILURE,
            message="Command 'pytest' exited with status 1.",
            source=FailureSourceType.COMMAND,
            evidence=["cmd-rec-123", "log-out-456"],
        )
        self.assertTrue(failure.failure_id.startswith(FAILURE_ID_PREFIX))
        validate_failure_id(failure.failure_id)
        self.assertEqual(failure.execution_id, self.exec_id)
        self.assertEqual(failure.work_order_id, self.wo_id)
        self.assertEqual(failure.category, ProgrammerFailureCategory.COMMAND_FAILURE)
        self.assertFalse(failure.retryable)
        self.assertEqual(failure.recovery_disposition, RecoveryDisposition.FAIL)
        self.assertEqual(failure.source, FailureSourceType.COMMAND)
        self.assertEqual(failure.evidence, ["cmd-rec-123", "log-out-456"])
        self.assertIn("failure_id", failure.trace)

    def test_create_factory_string_coercion(self) -> None:
        failure = ProgrammerFailure.create(
            execution_id=self.exec_id,
            work_order_id=self.wo_id,
            category="agent_startup",
            message="Cline failed to launch container.",
            severity="medium",
            source="agent",
        )
        self.assertEqual(failure.category, ProgrammerFailureCategory.AGENT_STARTUP)
        self.assertEqual(failure.severity, ProgrammerFailureSeverity.MEDIUM)
        self.assertEqual(failure.source, FailureSourceType.AGENT)
        self.assertTrue(failure.retryable)
        self.assertEqual(failure.recovery_disposition, RecoveryDisposition.RETRY)

    # -------------------------------------------------------------------------
    # 4. Policy Contradiction Guards
    # -------------------------------------------------------------------------

    def test_scope_violation_cannot_be_retryable(self) -> None:
        with self.assertRaises(ProgrammerValidationError) as ctx:
            ProgrammerFailure.create(
                execution_id=self.exec_id,
                work_order_id=self.wo_id,
                category=ProgrammerFailureCategory.SCOPE,
                message="Modified unauthorized file.",
                retryable=True,  # Disallowed
            )
        self.assertIn("cannot be marked retryable", str(ctx.exception))

    def test_permission_violation_cannot_be_retryable(self) -> None:
        with self.assertRaises(ProgrammerValidationError) as ctx:
            ProgrammerFailure.create(
                execution_id=self.exec_id,
                work_order_id=self.wo_id,
                category=ProgrammerFailureCategory.PERMISSION,
                message="Attempted unauthorized sudo command.",
                retryable=True,  # Disallowed
            )
        self.assertIn("cannot be marked retryable", str(ctx.exception))

    def test_cancellation_cannot_be_retryable(self) -> None:
        with self.assertRaises(ProgrammerValidationError) as ctx:
            ProgrammerFailure.create(
                execution_id=self.exec_id,
                work_order_id=self.wo_id,
                category=ProgrammerFailureCategory.CANCELLATION,
                message="User requested stop.",
                retryable=True,
                recovery_disposition=RecoveryDisposition.CANCEL,
            )
        self.assertIn("CANCELLATION cannot be retryable", str(ctx.exception))

    def test_cancellation_must_have_cancel_disposition(self) -> None:
        with self.assertRaises(ProgrammerValidationError) as ctx:
            ProgrammerFailure.create(
                execution_id=self.exec_id,
                work_order_id=self.wo_id,
                category=ProgrammerFailureCategory.CANCELLATION,
                message="Execution cancelled.",
                recovery_disposition=RecoveryDisposition.FAIL,  # Disallowed
            )
        self.assertIn("CANCELLATION disposition must be CANCEL", str(ctx.exception))

    def test_budget_exhaustion_cannot_be_retryable(self) -> None:
        with self.assertRaises(ProgrammerValidationError) as ctx:
            ProgrammerFailure.create(
                execution_id=self.exec_id,
                work_order_id=self.wo_id,
                category=ProgrammerFailureCategory.BUDGET,
                message="Token limit reached.",
                retryable=True,
            )
        self.assertIn("BUDGET exhaustion cannot be retryable", str(ctx.exception))

    # -------------------------------------------------------------------------
    # 5. Lineage & Structural Invariants
    # -------------------------------------------------------------------------

    def test_missing_execution_id_raises_lineage_error(self) -> None:
        with self.assertRaises(ProgrammerLineageError):
            ProgrammerFailure.create(
                execution_id="",
                work_order_id=self.wo_id,
                category=ProgrammerFailureCategory.INTERNAL,
                message="Something went wrong.",
            )

    def test_missing_work_order_id_raises_lineage_error(self) -> None:
        with self.assertRaises(ProgrammerLineageError):
            ProgrammerFailure.create(
                execution_id=self.exec_id,
                work_order_id="",
                category=ProgrammerFailureCategory.INTERNAL,
                message="Something went wrong.",
            )

    def test_invalid_id_prefixes_raise_validation_error(self) -> None:
        with self.assertRaises(InvalidProgrammerIdError):
            ProgrammerFailure.create(
                execution_id="wrongprefix-123",
                work_order_id=self.wo_id,
                category=ProgrammerFailureCategory.INTERNAL,
                message="Something went wrong.",
            )

        with self.assertRaises(InvalidProgrammerIdError):
            ProgrammerFailure.create(
                execution_id=self.exec_id,
                work_order_id="badwo-123",
                category=ProgrammerFailureCategory.INTERNAL,
                message="Something went wrong.",
            )

        with self.assertRaises(InvalidProgrammerIdError):
            ProgrammerFailure.create(
                execution_id=self.exec_id,
                work_order_id=self.wo_id,
                category=ProgrammerFailureCategory.INTERNAL,
                message="Something went wrong.",
                failure_id="badfail-123",
            )

    def test_blank_message_raises_validation_error(self) -> None:
        with self.assertRaises(ProgrammerValidationError):
            ProgrammerFailure.create(
                execution_id=self.exec_id,
                work_order_id=self.wo_id,
                category=ProgrammerFailureCategory.INTERNAL,
                message="   ",
            )

    def test_blank_evidence_raises_validation_error(self) -> None:
        fail = ProgrammerFailure(
            failure_id=new_failure_id(),
            execution_id=self.exec_id,
            work_order_id=self.wo_id,
            category=ProgrammerFailureCategory.INTERNAL,
            message="Internal crash.",
            evidence=["valid"],
        )
        fail.evidence = ["valid", ""]
        with self.assertRaises(ProgrammerValidationError):
            fail.validate()

    # -------------------------------------------------------------------------
    # 6. Serialization (to_dict / from_dict / to_json)
    # -------------------------------------------------------------------------

    def test_serialization_roundtrip(self) -> None:
        original = ProgrammerFailure.create(
            execution_id=self.exec_id,
            work_order_id=self.wo_id,
            category=ProgrammerFailureCategory.VERIFICATION_FAILURE,
            message="Verification check vchk-123 failed.",
            severity=ProgrammerFailureSeverity.HIGH,
            source=FailureSourceType.VERIFICATION,
            evidence=["vchk-123", "vevid-456"],
            trace={"run_id": "test-run-1"},
            metadata={"check_type": "TEST_COMMAND"},
        )

        d = original.to_dict()
        self.assertEqual(d["failure_id"], original.failure_id)
        self.assertEqual(d["category"], "VERIFICATION_FAILURE")
        self.assertEqual(d["recovery_disposition"], "CORRECT")
        self.assertEqual(d["source"], "VERIFICATION")
        self.assertEqual(d["evidence"], ["vchk-123", "vevid-456"])

        reconstructed = ProgrammerFailure.from_dict(d)
        self.assertEqual(reconstructed.failure_id, original.failure_id)
        self.assertEqual(reconstructed.execution_id, original.execution_id)
        self.assertEqual(reconstructed.work_order_id, original.work_order_id)
        self.assertEqual(reconstructed.category, original.category)
        self.assertEqual(reconstructed.severity, original.severity)
        self.assertEqual(reconstructed.message, original.message)
        self.assertEqual(reconstructed.source, original.source)
        self.assertEqual(reconstructed.retryable, original.retryable)
        self.assertEqual(reconstructed.recovery_disposition, original.recovery_disposition)
        self.assertEqual(reconstructed.evidence, original.evidence)
        self.assertEqual(reconstructed.trace, original.trace)
        self.assertEqual(reconstructed.metadata, original.metadata)

    def test_json_serialization(self) -> None:
        original = ProgrammerFailure.create(
            execution_id=self.exec_id,
            work_order_id=self.wo_id,
            category=ProgrammerFailureCategory.TIMEOUT,
            message="Execution exceeded 600s timeout.",
        )
        json_str = original.to_json()
        parsed = json.loads(json_str)
        self.assertEqual(parsed["category"], "TIMEOUT")
        self.assertEqual(parsed["recovery_disposition"], "FAIL")

        from_json_obj = ProgrammerFailure.from_dict(parsed)
        self.assertEqual(from_json_obj.failure_id, original.failure_id)

    # -------------------------------------------------------------------------
    # 7. Helper Properties
    # -------------------------------------------------------------------------

    def test_helper_properties(self) -> None:
        agent_fail = ProgrammerFailure.create(
            execution_id=self.exec_id,
            work_order_id=self.wo_id,
            category=ProgrammerFailureCategory.AGENT_CRASH,
            message="Cline process exited prematurely.",
        )
        self.assertTrue(agent_fail.is_agent_failure)
        self.assertTrue(agent_fail.is_retryable)
        self.assertFalse(agent_fail.requires_escalation)
        self.assertFalse(agent_fail.requires_blocker)
        self.assertFalse(agent_fail.is_scope_or_permission)
        self.assertFalse(agent_fail.is_verification_failure)

        scope_fail = ProgrammerFailure.create(
            execution_id=self.exec_id,
            work_order_id=self.wo_id,
            category=ProgrammerFailureCategory.SCOPE,
            message="Attempted write to /etc/hosts.",
        )
        self.assertFalse(scope_fail.is_agent_failure)
        self.assertFalse(scope_fail.is_retryable)
        self.assertTrue(scope_fail.requires_blocker)
        self.assertTrue(scope_fail.is_scope_or_permission)

        verif_fail = ProgrammerFailure.create(
            execution_id=self.exec_id,
            work_order_id=self.wo_id,
            category=ProgrammerFailureCategory.VERIFICATION_FAILURE,
            message="Unit tests failed.",
        )
        self.assertTrue(verif_fail.is_verification_failure)

        resource_fail = ProgrammerFailure.create(
            execution_id=self.exec_id,
            work_order_id=self.wo_id,
            category=ProgrammerFailureCategory.RESOURCE,
            message="Disk space full.",
        )
        self.assertTrue(resource_fail.requires_escalation)

    # -------------------------------------------------------------------------
    # 8. Enum Case-Insensitive Parsing & Fallbacks
    # -------------------------------------------------------------------------

    def test_enum_case_insensitivity_and_fallbacks(self) -> None:
        # Lowercase string
        cat = ProgrammerFailureCategory("agent_startup")
        self.assertEqual(cat, ProgrammerFailureCategory.AGENT_STARTUP)

        # Unrecognized category falls back to UNKNOWN
        cat_unknown = ProgrammerFailureCategory("non_existent_category")
        self.assertEqual(cat_unknown, ProgrammerFailureCategory.UNKNOWN)

        # Unrecognized disposition falls back to FAIL
        disp_unknown = RecoveryDisposition("random_disposition")
        self.assertEqual(disp_unknown, RecoveryDisposition.FAIL)

        # Unrecognized source falls back to UNKNOWN
        source_unknown = FailureSourceType("random_source")
        self.assertEqual(source_unknown, FailureSourceType.UNKNOWN)


if __name__ == "__main__":
    unittest.main()
