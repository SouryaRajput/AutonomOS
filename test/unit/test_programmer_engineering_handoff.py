from __future__ import annotations

import unittest

from core.programmer.contracts.handoff import EngineeringHandoff
from core.programmer.contracts.identifiers import (
    HANDOFF_ID_PREFIX,
    new_handoff_id,
    validate_handoff_id,
)
from core.programmer.errors import (
    InvalidProgrammerIdError,
    ProgrammerValidationError,
)
from core.programmer.types import (
    EngineeringHandoffType,
    HandoffPriority,
)


class TestProgrammerEngineeringHandoff(unittest.TestCase):
    """Unit tests for Phase 8.1: Engineering Handoff Contracts."""

    def setUp(self) -> None:
        self.valid_project_id = "proj-alpha"
        self.valid_source_worker = "worker-programmer"
        self.valid_target_worker = "worker-tester"
        self.valid_source_task = "task-eng-001"
        self.valid_objective = "Verify math service calculation edge cases"
        self.valid_action = "Execute integration test suite on calculator module"

    # =========================================================================
    # Scenario 1: Valid handoff creation across all 5 handoff types
    # =========================================================================
    def test_01_valid_handoff_creation_across_all_types(self) -> None:
        """Scenario 1: Creates valid handoff instances across all 5 canonical V1 types."""
        types_to_test = [
            (EngineeringHandoffType.RESEARCH_TO_PROGRAMMER, "worker-researcher", "worker-programmer"),
            (EngineeringHandoffType.PROGRAMMER_TO_DESIGNER, "worker-programmer", "worker-designer"),
            (EngineeringHandoffType.PROGRAMMER_TO_TESTER, "worker-programmer", "worker-tester"),
            (EngineeringHandoffType.TESTER_TO_PROGRAMMER, "worker-tester", "worker-programmer"),
            (EngineeringHandoffType.DESIGNER_TO_PROGRAMMER, "worker-designer", "worker-programmer"),
        ]

        for handoff_type, src_worker, tgt_worker in types_to_test:
            hid = new_handoff_id()
            self.assertTrue(hid.startswith(HANDOFF_ID_PREFIX))
            handoff = EngineeringHandoff(
                handoff_id=hid,
                project_id=self.valid_project_id,
                source_worker_id=src_worker,
                target_worker_id=tgt_worker,
                source_task_id=self.valid_source_task,
                objective=f"Handoff for {handoff_type.value}",
                requested_action="Proceed with next stage",
                handoff_type=handoff_type,
            )
            self.assertEqual(handoff.handoff_type, handoff_type)
            self.assertEqual(handoff.source_worker_id, src_worker)
            self.assertEqual(handoff.target_worker_id, tgt_worker)
            self.assertEqual(handoff.priority, HandoffPriority.NORMAL)

    # =========================================================================
    # Scenario 2: Lineage preservation
    # =========================================================================
    def test_02_lineage_preservation(self) -> None:
        """Scenario 2: Preserves task, work order, project, and trace lineage accurately."""
        hid = new_handoff_id()
        trace_data = {"origin_flow": "e2e_pipeline", "step": 3, "correlation_id": "corr-99"}
        handoff = EngineeringHandoff(
            handoff_id=hid,
            project_id="proj-lineage-100",
            source_worker_id="worker-programmer",
            target_worker_id="worker-tester",
            source_task_id="mtask-404",
            target_task_id="mtask-505",
            work_order_id="pwo-abc12345",
            objective="Deliver tested changes",
            requested_action="Run regression sweep",
            handoff_type=EngineeringHandoffType.PROGRAMMER_TO_TESTER,
            trace=trace_data,
        )

        self.assertEqual(handoff.project_id, "proj-lineage-100")
        self.assertEqual(handoff.source_task_id, "mtask-404")
        self.assertEqual(handoff.target_task_id, "mtask-505")
        self.assertEqual(handoff.work_order_id, "pwo-abc12345")
        self.assertEqual(handoff.trace["correlation_id"], "corr-99")

    # =========================================================================
    # Scenario 3: Strict validation on invalid / missing IDs
    # =========================================================================
    def test_03_strict_validation_on_invalid_missing_ids(self) -> None:
        """Scenario 3: Rejects malformed or missing identifiers."""
        # Invalid handoff ID prefix
        with self.assertRaises(InvalidProgrammerIdError):
            EngineeringHandoff(
                handoff_id="bad-prefix-123",
                project_id=self.valid_project_id,
                source_worker_id=self.valid_source_worker,
                target_worker_id=self.valid_target_worker,
                source_task_id=self.valid_source_task,
                objective=self.valid_objective,
                requested_action=self.valid_action,
            )

        # Empty project ID
        with self.assertRaises(ProgrammerValidationError):
            EngineeringHandoff(
                handoff_id=new_handoff_id(),
                project_id="",
                source_worker_id=self.valid_source_worker,
                target_worker_id=self.valid_target_worker,
                source_task_id=self.valid_source_task,
                objective=self.valid_objective,
                requested_action=self.valid_action,
            )

        # Empty source_task_id
        with self.assertRaises(ProgrammerValidationError):
            EngineeringHandoff(
                handoff_id=new_handoff_id(),
                project_id=self.valid_project_id,
                source_worker_id=self.valid_source_worker,
                target_worker_id=self.valid_target_worker,
                source_task_id="",
                objective=self.valid_objective,
                requested_action=self.valid_action,
            )

        # Empty objective
        with self.assertRaises(ProgrammerValidationError):
            EngineeringHandoff(
                handoff_id=new_handoff_id(),
                project_id=self.valid_project_id,
                source_worker_id=self.valid_source_worker,
                target_worker_id=self.valid_target_worker,
                source_task_id=self.valid_source_task,
                objective="   ",
                requested_action=self.valid_action,
            )

    # =========================================================================
    # Scenario 4: Source and target worker must be distinct
    # =========================================================================
    def test_04_source_and_target_worker_must_be_distinct(self) -> None:
        """Scenario 4: Rejects handoffs where source and target worker IDs are identical."""
        with self.assertRaises(ProgrammerValidationError) as ctx:
            EngineeringHandoff(
                handoff_id=new_handoff_id(),
                project_id=self.valid_project_id,
                source_worker_id="worker-programmer",
                target_worker_id="worker-programmer",
                source_task_id=self.valid_source_task,
                objective=self.valid_objective,
                requested_action=self.valid_action,
            )
        self.assertIn("must be distinct", str(ctx.exception))

    # =========================================================================
    # Scenario 5: Research evidence remains non-authoritative context
    # =========================================================================
    def test_05_research_evidence_remains_non_authoritative_context(self) -> None:
        """Scenario 5: Evidence in handoff is informational and isolated from requirements."""
        evidence_data = [
            {"evidence_id": "ev-1", "fact": "SQLite does not support concurrent write locks"},
            {"evidence_id": "ev-2", "fact": "Redis latency is sub-millisecond"},
        ]
        handoff = EngineeringHandoff(
            handoff_id=new_handoff_id(),
            project_id=self.valid_project_id,
            source_worker_id="worker-researcher",
            target_worker_id="worker-programmer",
            source_task_id=self.valid_source_task,
            objective="Evaluate database options",
            requested_action="Implement connection pool",
            evidence=evidence_data,
            requirements=["Pool size must be 10"],
            acceptance_criteria=["Zero leaked connections"],
        )

        # Evidence is present as evidence
        self.assertEqual(len(handoff.evidence), 2)
        self.assertEqual(handoff.evidence[0]["evidence_id"], "ev-1")
        # Evidence did not pollute or redefine requirements or acceptance criteria
        self.assertEqual(handoff.requirements, ["Pool size must be 10"])
        self.assertEqual(handoff.acceptance_criteria, ["Zero leaked connections"])

    # =========================================================================
    # Scenario 6: Serialization and round-trip deserialization
    # =========================================================================
    def test_06_serialization_and_roundtrip(self) -> None:
        """Scenario 6: Full round-trip consistency via to_dict/from_dict and to_json/from_json."""
        original = EngineeringHandoff(
            handoff_id=new_handoff_id(),
            project_id="proj-serialization",
            source_worker_id="worker-programmer",
            target_worker_id="worker-designer",
            source_task_id="task-ui-1",
            target_task_id="task-ui-2",
            work_order_id="pwo-99991111",
            objective="Implement design review adjustments",
            requested_action="Review layout and spacing",
            handoff_type=EngineeringHandoffType.PROGRAMMER_TO_DESIGNER,
            context={"screen": "dashboard", "dpi": "high"},
            artifacts=["src/ui/dashboard.tsx", "docs/spec.md"],
            requirements=["Responsive down to 320px"],
            constraints=["No external CSS frameworks"],
            acceptance_criteria=["Pixel-perfect alignment on 1080p"],
            evidence=[{"item": "mockup-v2"}],
            risks=[{"risk": "viewport clipping"}],
            known_unknowns=["touch target responsiveness"],
            priority=HandoffPriority.HIGH,
            trace={"audit": True},
        )

        # Dict roundtrip
        d = original.to_dict()
        restored_d = EngineeringHandoff.from_dict(d)
        self.assertEqual(original.handoff_id, restored_d.handoff_id)
        self.assertEqual(original.project_id, restored_d.project_id)
        self.assertEqual(original.handoff_type, restored_d.handoff_type)
        self.assertEqual(original.priority, restored_d.priority)
        self.assertEqual(original.artifacts, restored_d.artifacts)
        self.assertEqual(original.requirements, restored_d.requirements)
        self.assertEqual(original.acceptance_criteria, restored_d.acceptance_criteria)

        # JSON roundtrip
        json_str = original.to_json()
        restored_json = EngineeringHandoff.from_json(json_str)
        self.assertEqual(original.handoff_id, restored_json.handoff_id)
        self.assertEqual(original.context, restored_json.context)
        self.assertEqual(original.trace, restored_json.trace)

    # =========================================================================
    # Scenario 7: Priority handling
    # =========================================================================
    def test_07_priority_handling(self) -> None:
        """Scenario 7: Correct assignment, string parsing, and default fallback for priority."""
        for prio in (HandoffPriority.LOW, HandoffPriority.NORMAL, HandoffPriority.HIGH, HandoffPriority.URGENT):
            h = EngineeringHandoff(
                handoff_id=new_handoff_id(),
                project_id=self.valid_project_id,
                source_worker_id=self.valid_source_worker,
                target_worker_id=self.valid_target_worker,
                source_task_id=self.valid_source_task,
                objective=self.valid_objective,
                requested_action=self.valid_action,
                priority=prio,
            )
            self.assertEqual(h.priority, prio)

        # String enum parsing
        h_str = EngineeringHandoff(
            handoff_id=new_handoff_id(),
            project_id=self.valid_project_id,
            source_worker_id=self.valid_source_worker,
            target_worker_id=self.valid_target_worker,
            source_task_id=self.valid_source_task,
            objective=self.valid_objective,
            requested_action=self.valid_action,
            priority="URGENT",
        )
        self.assertEqual(h_str.priority, HandoffPriority.URGENT)

        # Fallback to NORMAL for unknown
        h_unknown = EngineeringHandoff(
            handoff_id=new_handoff_id(),
            project_id=self.valid_project_id,
            source_worker_id=self.valid_source_worker,
            target_worker_id=self.valid_target_worker,
            source_task_id=self.valid_source_task,
            objective=self.valid_objective,
            requested_action=self.valid_action,
            priority="NON_EXISTENT_PRIORITY",
        )
        self.assertEqual(h_unknown.priority, HandoffPriority.NORMAL)

    # =========================================================================
    # Scenario 8: Acceptance criteria and constraints immutability
    # =========================================================================
    def test_08_acceptance_criteria_and_constraints_immutability(self) -> None:
        """Scenario 8: Mutating external input lists does not alter the handoff instance."""
        ext_criteria = ["Criterion 1", "Criterion 2"]
        ext_constraints = ["Constraint 1"]
        ext_artifacts = ["file1.py"]

        handoff = EngineeringHandoff(
            handoff_id=new_handoff_id(),
            project_id=self.valid_project_id,
            source_worker_id=self.valid_source_worker,
            target_worker_id=self.valid_target_worker,
            source_task_id=self.valid_source_task,
            objective=self.valid_objective,
            requested_action=self.valid_action,
            acceptance_criteria=ext_criteria,
            constraints=ext_constraints,
            artifacts=ext_artifacts,
        )

        # External list mutation
        ext_criteria.append("Mutated Criterion")
        ext_constraints.append("Mutated Constraint")
        ext_artifacts.append("mutated_file.py")

        self.assertEqual(handoff.acceptance_criteria, ["Criterion 1", "Criterion 2"])
        self.assertEqual(handoff.constraints, ["Constraint 1"])
        self.assertEqual(handoff.artifacts, ["file1.py"])

        # Dict export mutation
        exported = handoff.to_dict()
        exported["acceptance_criteria"].append("Leaked Criterion")
        self.assertEqual(len(handoff.acceptance_criteria), 2)


if __name__ == "__main__":
    unittest.main()
