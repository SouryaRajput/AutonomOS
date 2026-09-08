from __future__ import annotations

import unittest

from core.programmer.contracts.designer_handoff import (
    ApiEndpointContract,
    BackendCapability,
    DesignerContextItem,
    ProgrammerToDesignerHandoffBuilder,
)
from core.programmer.contracts.handoff import EngineeringHandoff
from core.programmer.contracts.identifiers import (
    new_work_order_id,
)
from core.programmer.contracts.work_order import ProgrammerWorkOrder
from core.programmer.errors import (
    MissingImplementationEvidenceError,
    UnauthorizedUXPrescriptionError,
)
from core.programmer.types import (
    DesignerContextClassification,
    EngineeringHandoffType,
    HandoffPriority,
)


class TestProgrammerDesignerHandoff(unittest.TestCase):
    """Unit tests for Phase 8.3: Programmer -> Designer Handoff."""

    def setUp(self) -> None:
        self.wo_id = new_work_order_id()
        self.work_order = ProgrammerWorkOrder(
            work_order_id=self.wo_id,
            manager_task_id="mtask-ux-101",
            project_id="proj-design-hub",
            correlation_id="corr-dh-999",
            objective="Implement user profile and preferences API",
            technical_requirements=[
                "Provide REST endpoints for user profile fetching and updating",
                "Require Bearer JWT authentication for all mutation endpoints",
            ],
            constraints=[
                "Database transaction timeout is 5 seconds",
            ],
            allowed_paths=["src/api/", "src/models/"],
            writable_paths=["src/api/"],
        )

        self.endpoint_get = ApiEndpointContract(
            endpoint_id="ep-user-get",
            path="/api/v1/users/{id}",
            method="GET",
            description="Retrieve user profile by ID",
            response_schema={"type": "object", "properties": {"id": {"type": "string"}, "name": {"type": "string"}}},
            status_codes=[200, 404],
            auth_required=True,
            auth_type="Bearer JWT",
            source_file="src/api/users.py",
        )

        self.endpoint_put = ApiEndpointContract(
            endpoint_id="ep-user-update",
            path="/api/v1/users/{id}",
            method="PUT",
            description="Update user profile attributes",
            request_schema={"type": "object", "properties": {"name": {"type": "string"}, "avatar_url": {"type": "string"}}},
            response_schema={"type": "object", "properties": {"status": {"type": "string"}}},
            status_codes=[200, 400, 401],
            auth_required=True,
            auth_type="Bearer JWT",
            source_file="src/api/users.py",
            supporting_evidence_ids=["vevid-test-01"],
        )

        self.capability = BackendCapability(
            capability_id="cap-profile",
            name="UserProfileService",
            description="Full profile management service including profile retrieval and metadata updates",
            endpoints=[self.endpoint_get, self.endpoint_put],
            data_models={
                "UserProfile": {
                    "id": "str (UUID)",
                    "name": "str (1..100 chars)",
                    "avatar_url": "Optional[str]",
                    "created_at": "ISO-8601 UTC",
                }
            },
            auth_requirements=["Bearer JWT in Authorization header"],
            configuration_requirements=["USER_API_BASE_URL environment variable"],
            frontend_integration_points=["UserProfileCard component", "SettingsAccountForm component"],
            technical_constraints=["Max avatar upload size is 2MB", "Payload size restricted to 64KB"],
            known_limitations=["Batch user update is not yet supported"],
            supporting_artifacts=["src/api/users.py", "src/models/user.py"],
        )

    # =========================================================================
    # Scenario 1: Backend capability handoff
    # =========================================================================
    def test_01_backend_capability_handoff(self) -> None:
        """Scenario 1: Creates structured handoff encapsulating backend capabilities and integration points."""
        handoff = ProgrammerToDesignerHandoffBuilder.build(
            work_order=self.work_order,
            capabilities=[self.capability],
            artifacts=["src/api/users.py", "src/models/user.py"],
        )

        self.assertIsInstance(handoff, EngineeringHandoff)
        self.assertEqual(handoff.handoff_type, EngineeringHandoffType.PROGRAMMER_TO_DESIGNER)
        self.assertEqual(handoff.source_worker_id, "worker-programmer")
        self.assertEqual(handoff.target_worker_id, "worker-designer")

        # Verify context payload
        ctx = handoff.context
        self.assertEqual(len(ctx["backend_capabilities"]), 1)
        cap = ctx["backend_capabilities"][0]
        self.assertEqual(cap["name"], "UserProfileService")
        self.assertIn("UserProfile", cap["data_models"])
        self.assertIn("UserProfileCard component", ctx["frontend_integration_points"])
        self.assertIn("USER_API_BASE_URL environment variable", ctx["configuration_requirements"])

    # =========================================================================
    # Scenario 2: API contract
    # =========================================================================
    def test_02_api_contract(self) -> None:
        """Scenario 2: Correctly communicates request/response schemas, status codes, and methods."""
        handoff = ProgrammerToDesignerHandoffBuilder.build(
            work_order=self.work_order,
            capabilities=[self.capability],
            artifacts=["src/api/users.py"],
        )

        endpoints = handoff.context["api_endpoints"]
        self.assertEqual(len(endpoints), 2)
        get_ep = next(e for e in endpoints if e["method"] == "GET")
        put_ep = next(e for e in endpoints if e["method"] == "PUT")

        self.assertEqual(get_ep["path"], "/api/v1/users/{id}")
        self.assertTrue(get_ep["auth_required"])
        self.assertEqual(get_ep["status_codes"], [200, 404])

        self.assertIn("avatar_url", put_ep["request_schema"]["properties"])
        self.assertEqual(put_ep["status_codes"], [200, 400, 401])

    # =========================================================================
    # Scenario 3: Technical constraints
    # =========================================================================
    def test_03_technical_constraints(self) -> None:
        """Scenario 3: Preserves technical constraints from capability and context items."""
        tech_constraint = DesignerContextItem(
            statement="API returns 429 Too Many Requests after 60 calls per minute",
            classification=DesignerContextClassification.TECHNICAL_REQUIREMENT,
            rationale="DDoS prevention tier",
        )

        handoff = ProgrammerToDesignerHandoffBuilder.build(
            work_order=self.work_order,
            capabilities=[self.capability],
            context_items=[tech_constraint],
            artifacts=["src/api/users.py"],
        )

        self.assertIn("Max avatar upload size is 2MB", handoff.constraints)
        self.assertIn("API returns 429 Too Many Requests after 60 calls per minute", handoff.constraints)
        self.assertIn("API returns 429 Too Many Requests after 60 calls per minute", handoff.requirements)

    # =========================================================================
    # Scenario 4: Missing API / implementation evidence
    # =========================================================================
    def test_04_missing_api_evidence(self) -> None:
        """Scenario 4: Rejects API endpoints claimed without supporting code files or verification evidence."""
        unbacked_ep = ApiEndpointContract(
            endpoint_id="ep-unbacked",
            path="/api/v1/phantom",
            method="DELETE",
            description="Unimplemented phantom endpoint",
            source_file=None,
            supporting_evidence_ids=[],
        )

        with self.assertRaises(MissingImplementationEvidenceError) as ctx:
            ProgrammerToDesignerHandoffBuilder.build(
                work_order=self.work_order,
                capabilities=[],
                endpoints=[unbacked_ep],
                artifacts=["src/api/users.py"],
            )

        self.assertIn("lacks supporting implementation", str(ctx.exception))
        self.assertEqual(ctx.exception.endpoint_ref, "DELETE /api/v1/phantom")

    # =========================================================================
    # Scenario 5: Unresolved limitations & questions
    # =========================================================================
    def test_05_unresolved_limitations(self) -> None:
        """Scenario 5: Surfaces known technical limitations and unresolved questions in known_unknowns."""
        handoff = ProgrammerToDesignerHandoffBuilder.build(
            work_order=self.work_order,
            capabilities=[self.capability],
            known_limitations=["Avatar animated GIFs are not supported; static formats only"],
            unresolved_questions=["Should profile deletion support a 30-day grace period?"],
            artifacts=["src/api/users.py"],
        )

        self.assertIn("Avatar animated GIFs are not supported; static formats only", handoff.known_unknowns)
        self.assertIn("Should profile deletion support a 30-day grace period?", handoff.known_unknowns)
        self.assertIn("Batch user update is not yet supported", handoff.context["known_limitations"])

    # =========================================================================
    # Scenario 6: Technical requirement vs Design recommendation distinction
    # =========================================================================
    def test_06_recommendation_vs_requirement_distinction(self) -> None:
        """Scenario 6: Prohibits unauthorized UX prescriptions as technical requirements; permits design recommendations."""
        # Case A: Programmer attempts to mandate visual design without Manager authorization -> REJECTED
        unauthorized_item = DesignerContextItem(
            statement="Submit button color must be vibrant green with 8px padding",
            classification=DesignerContextClassification.TECHNICAL_REQUIREMENT,
            rationale="Programmer personal aesthetic preference",
        )

        with self.assertRaises(UnauthorizedUXPrescriptionError) as ctx:
            ProgrammerToDesignerHandoffBuilder.build(
                work_order=self.work_order,
                capabilities=[self.capability],
                context_items=[unauthorized_item],
                artifacts=["src/api/users.py"],
            )
        self.assertIn("Unauthorized UX prescription", str(ctx.exception))

        # Case B: The same thought framed as an advisory DESIGN_RECOMMENDATION -> PERMITTED
        design_recommendation = DesignerContextItem(
            statement="Consider vibrant green for submit button and 8px padding for touch targets",
            classification=DesignerContextClassification.DESIGN_RECOMMENDATION,
            rationale="High accessibility contrast suggestion",
        )

        handoff = ProgrammerToDesignerHandoffBuilder.build(
            work_order=self.work_order,
            capabilities=[self.capability],
            context_items=[design_recommendation],
            artifacts=["src/api/users.py"],
        )
        self.assertIsInstance(handoff, EngineeringHandoff)
        # Recommendation is preserved in context items but NOT in mandatory requirements
        self.assertNotIn(design_recommendation.statement, handoff.requirements)
        items = handoff.context["context_items"]
        rec_entry = next(i for i in items if i["classification"] == "DESIGN_RECOMMENDATION")
        self.assertEqual(rec_entry["statement"], design_recommendation.statement)

    # =========================================================================
    # Scenario 7: Evidence linkage
    # =========================================================================
    def test_07_evidence_linkage(self) -> None:
        """Scenario 7: Accurately passes verification evidence records and links them to endpoints."""
        evidence_record = {
            "evidence_id": "vevid-test-01",
            "check_type": "TEST",
            "description": "Integration test for PUT /api/v1/users/{id} passed with status 200",
            "passed": True,
        }

        handoff = ProgrammerToDesignerHandoffBuilder.build(
            work_order=self.work_order,
            capabilities=[self.capability],
            verification_evidence=[evidence_record],
            artifacts=["src/api/users.py"],
        )

        self.assertEqual(len(handoff.evidence), 1)
        self.assertEqual(handoff.evidence[0]["evidence_id"], "vevid-test-01")

        # Endpoint links to evidence
        put_ep = next(e for e in handoff.context["api_endpoints"] if e["endpoint_id"] == "ep-user-update")
        self.assertIn("vevid-test-01", put_ep["supporting_evidence_ids"])

    # =========================================================================
    # Scenario 8: Lineage preservation
    # =========================================================================
    def test_08_lineage_preservation(self) -> None:
        """Scenario 8: Preserves work_order_id, manager_task_id, project_id, and trace."""
        custom_trace = {"run_id": "r-12345", "pipeline_step": "handoff"}
        handoff = ProgrammerToDesignerHandoffBuilder.build(
            work_order=self.work_order,
            capabilities=[self.capability],
            artifacts=["src/api/users.py"],
            priority=HandoffPriority.HIGH,
            trace=custom_trace,
        )

        self.assertEqual(handoff.work_order_id, self.wo_id)
        self.assertEqual(handoff.source_task_id, "mtask-ux-101")
        self.assertEqual(handoff.project_id, "proj-design-hub")
        self.assertEqual(handoff.priority, HandoffPriority.HIGH)
        self.assertEqual(handoff.trace["work_order_id"], self.wo_id)
        self.assertEqual(handoff.trace["manager_task_id"], "mtask-ux-101")
        self.assertEqual(handoff.trace["run_id"], "r-12345")


if __name__ == "__main__":
    unittest.main()
