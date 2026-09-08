from __future__ import annotations

import copy
import json
import os
import shutil
import tempfile
import time
import unittest
from typing import Any, Optional, Sequence

from core.enums import RiskLevel, TaskStatus
from core.events.types import EventSource, EventType
from core.programmer.contracts.acceptance_criteria import AcceptanceCriterion
from core.programmer.contracts.acceptance_result import AcceptanceCriterionResult
from core.programmer.contracts.coding_agent import (
    CodingAgentBackend,
    CodingAgentEvent,
    CodingAgentExecutionStatus,
    CodingAgentRequest,
    CodingAgentResult,
    MockCodingAgentBackend,
)
from core.programmer.contracts.delivery import (
    DeliveryPackage,
    DeliveryPreparer,
)
from core.programmer.contracts.git_model import (
    GitExecutionContext,
    GitRevision,
)
from core.programmer.contracts.designer_handoff import (
    ApiEndpointContract,
    BackendCapability,
    DesignerContextItem,
    ProgrammerToDesignerHandoffBuilder,
)
from core.programmer.contracts.diff_verifier import (
    DiffScopeVerifier,
    DiffVerification,
)
from core.programmer.contracts.feedback import EngineeringFeedback
from core.programmer.contracts.feedback_adapter import FeedbackToWorkOrderAdapter
from core.programmer.contracts.handoff import EngineeringHandoff
from core.programmer.contracts.identifiers import (
    new_delivery_id,
    new_diff_verification_id,
    new_execution_id,
    new_feedback_id,
    new_git_repository_id,
    new_git_revision_id,
    new_handoff_id,
    new_result_id,
    new_verification_evidence_id,
    new_work_order_id,
)
from core.programmer.contracts.iteration import (
    EngineeringIterationCoordinator,
    IterationOutcome,
    PriorEngineeringContext,
)
from core.programmer.contracts.manager_bridge import ProgrammerManagerBridge
from core.programmer.contracts.prompt_builder import ProgrammerPromptBuilder
from core.programmer.contracts.provisioner import WorkspaceProvisioner
from core.programmer.contracts.research_adapter import (
    EpistemicContextItem,
    ResearchToWorkOrderAdapter,
)
from core.programmer.contracts.research_reference import ResearchEvidenceReference
from core.programmer.contracts.result import ProgrammerResult
from core.programmer.contracts.test_record import TestResultRecord
from core.programmer.contracts.tester_handoff import (
    ChangedApiContract,
    ProgrammerToTesterHandoffBuilder,
    ProgrammerVerificationSummary,
)
from core.programmer.contracts.verification import (
    VerificationCheck,
    VerificationEvidence,
    VerificationSummary,
)
from core.programmer.contracts.verification_runner import (
    VerificationRunner,
    VerificationRunnerResult,
)
from core.programmer.contracts.work_order import ProgrammerWorkOrder
from core.programmer.errors import (
    ProgrammerLineageError,
    ProgrammerValidationError,
    UnauthorizedQAClaimError,
    UnauthorizedUXPrescriptionError,
    UnrelatedFeedbackError,
)
from core.programmer.types import (
    AcceptanceCriterionType,
    AcceptanceStatus,
    ApiChangeType,
    CodingAgentBackendType,
    DesignerContextClassification,
    EngineeringHandoffType,
    EpistemicContextType,
    FeedbackConfidence,
    FeedbackIssueType,
    FeedbackSeverity,
    HandoffPriority,
    ManagerDisposition,
    ManagerIterationDecision,
    ProgrammerActionType,
    ProgrammerEvidenceType,
    ProgrammerExecutionStatus,
    ProgrammerResultStatus,
    ProgrammerWorkOrderStatus,
    VerificationCheckType,
    VerificationDomain,
    VerificationEvidenceSourceType,
    VerificationStatus,
    VerificationSummaryStatus,
)
from core.research.contracts.evidence import EvidenceItem, EvidenceProvenance
from core.research.contracts.result import (
    ResearchFinding,
    ResearchRecommendation,
    ResearchResult,
)
from core.research.types import FactClassification, ResearchConfidence, SourceType


# =====================================================================
# Deterministic Fake Workers for Workforce Orchestration
# =====================================================================

class FakeResearcher:
    """Deterministic Researcher worker producing structured research results."""

    def __init__(self, worker_id: str = "worker.researcher.e2e") -> None:
        self.worker_id = worker_id

    def conduct_research(
        self,
        query: str,
        request_id: str = "req-rate-limiter",
        project_id: str = "proj-workforce-e2e",
    ) -> ResearchResult:
        prov = EvidenceProvenance(
            request_id=request_id,
            crawler_task_id="ctask-res-101",
            crawler_id="crawler-rfc-docs",
            source_ref="https://tools.ietf.org/rfc/rfc6585.txt",
        )
        ev1 = EvidenceItem(
            evidence_id="ev-token-bucket-algo",
            provenance=prov,
            extracted_fact="Token bucket algorithm allows bursts up to capacity and refills at constant rate.",
            content_snippet="Token bucket with capacity C and refill rate R per second.",
            classification=FactClassification.FACT,
            confidence=ResearchConfidence.WELL_SUPPORTED,
            source_type=SourceType.OFFICIAL_DOCUMENTATION,
        )
        ev2 = EvidenceItem(
            evidence_id="ev-status-429-spec",
            provenance=prov,
            extracted_fact="HTTP 429 Too Many Requests indicates the user has sent too many requests in a given amount of time.",
            content_snippet="RFC 6585 Section 4: 429 Too Many Requests with Retry-After header.",
            classification=FactClassification.FACT,
            confidence=ResearchConfidence.WELL_SUPPORTED,
            source_type=SourceType.OFFICIAL_DOCUMENTATION,
        )
        ev3 = EvidenceItem(
            evidence_id="ev-rate-limit-headers",
            provenance=prov,
            extracted_fact="Rate limiting headers should include X-RateLimit-Limit, X-RateLimit-Remaining, and Retry-After.",
            content_snippet="Standard rate limit response headers.",
            classification=FactClassification.FACT,
            confidence=ResearchConfidence.SUPPORTED,
            source_type=SourceType.PRIMARY_SOURCE,
        )

        rec1 = ResearchRecommendation(
            recommendation_id="rec-redis-cluster",
            action="Deploy Redis Cluster across multiple availability zones for persistence.",
            rationale="High availability and fault tolerance in multi-datacenter environments.",
        )
        rec2 = ResearchRecommendation(
            recommendation_id="rec-sliding-window",
            action="Consider sliding window log if burst traffic creates uneven distribution.",
            rationale="Smoother traffic distribution at the cost of higher memory footprint.",
        )

        finding = ResearchFinding(
            finding_id="fnd-rate-limiting-std",
            claim="Token bucket is the industry standard for HTTP API rate limiting.",
            evidence_ids=["ev-token-bucket-algo", "ev-status-429-spec", "ev-rate-limit-headers"],
        )

        return ResearchResult(
            task_id="task-research-rate-limiter",
            request_id=request_id,
            project_id=project_id,
            objective="Evaluate rate limiting architectures",
            findings=[finding],
            evidence=[ev1, ev2, ev3],
            recommendations=[rec1, rec2],
        )


class FakeDesigner:
    """Deterministic UI/UX Designer worker consuming engineering handoffs."""

    def __init__(self, worker_id: str = "worker.designer.e2e") -> None:
        self.worker_id = worker_id
        self.reviewed_handoffs: list[EngineeringHandoff] = []

    def review_engineering_handoff(self, handoff: EngineeringHandoff) -> dict[str, Any]:
        if handoff.handoff_type != EngineeringHandoffType.PROGRAMMER_TO_DESIGNER:
            raise ValueError(f"Expected PROGRAMMER_TO_DESIGNER handoff, got {handoff.handoff_type}")
        if handoff.target_worker_id != self.worker_id:
            raise ValueError(f"Target worker {handoff.target_worker_id} does not match {self.worker_id}")

        self.reviewed_handoffs.append(handoff)
        return {
            "status": "APPROVED",
            "reviewed_by": self.worker_id,
            "capabilities_reviewed": len(handoff.context.get("backend_capabilities", [])),
            "endpoints_reviewed": len(handoff.context.get("api_endpoints", [])),
            "ui_specifications": [
                {"component": "RateLimitWarningBanner", "triggers_on": "429 Too Many Requests"},
                {"component": "RetryCountdownBadge", "uses_header": "Retry-After"},
            ],
        }


class FakeTester:
    """Deterministic QA/Tester worker performing independent verification."""

    def __init__(self, worker_id: str = "worker.tester.e2e") -> None:
        self.worker_id = worker_id
        self.tests_run: int = 0
        self.test_reports: list[dict[str, Any]] = []

    def run_independent_testing(
        self,
        handoff: EngineeringHandoff,
        workspace_root: str,
        round_num: int = 1,
    ) -> tuple[str, list[EngineeringFeedback]]:
        if handoff.handoff_type != EngineeringHandoffType.PROGRAMMER_TO_TESTER:
            raise ValueError(f"Expected PROGRAMMER_TO_TESTER handoff, got {handoff.handoff_type}")
        self.tests_run += 1

        limiter_path = os.path.join(workspace_root, "src", "rate_limiter.py")
        if not os.path.exists(limiter_path):
            fb = EngineeringFeedback(
                feedback_id=new_feedback_id(),
                source_worker=self.worker_id,
                source_task=handoff.source_task_id,
                target_project=handoff.project_id,
                related_work_order=handoff.work_order_id or handoff.context.get("work_order_id", ""),
                issue="src/rate_limiter.py does not exist",
                issue_type=FeedbackIssueType.BUG,
                severity=FeedbackSeverity.CRITICAL,
                observed_behavior="File missing",
                expected_behavior="File present",
                reproduction_information="Check workspace src/rate_limiter.py",
                confidence=FeedbackConfidence.CONFIRMED,
            )
            return ("FAILED", [fb])

        with open(limiter_path, "r") as f:
            code = f.read()

        # In Round 1: Code has flaw if it raises ValueError on exhaustion
        if "raise ValueError" in code or round_num == 1:
            fb = EngineeringFeedback(
                feedback_id=new_feedback_id(),
                source_worker=self.worker_id,
                source_task=handoff.source_task_id,
                target_project=handoff.project_id,
                related_work_order=handoff.work_order_id or handoff.context.get("work_order_id", ""),
                issue="Rate limiter raises ValueError on token exhaustion instead of returning 429 status",
                issue_type=FeedbackIssueType.BUG,
                severity=FeedbackSeverity.HIGH,
                observed_behavior="Calling consume() when tokens=0 raises ValueError('Empty bucket'), causing server 500.",
                expected_behavior="Calling consume() when tokens=0 must return (False, retry_after) and set HTTP 429 status code without raising an unhandled exception.",
                reproduction_information="limiter = RateLimiter(capacity=1); limiter.consume(); limiter.consume()",
                suggested_direction="Return (False, retry_after) tuple when tokens are depleted.",
                confidence=FeedbackConfidence.CONFIRMED,
                evidence=["ev-exhaustion-repro-log"],
            )
            self.test_reports.append({"round": round_num, "status": "FAILED", "feedback": [fb]})
            return ("FAILED", [fb])

        # Round 2: Fixed code
        self.test_reports.append({"round": round_num, "status": "PASSED", "feedback": []})
        return ("PASSED", [])


class DeterministicClineBackend(MockCodingAgentBackend):
    """
    Deterministic implementation of Cline coding agent backend.
    Writes code for Round 1 (containing the edge-case bug) and Round 2 (the fix).
    """

    def __init__(self, workspace_root: str) -> None:
        super().__init__(backend_type=CodingAgentBackendType.CLINE)
        self.workspace_root = workspace_root
        self.invocation_count = 0
        self.written_files: list[str] = []

    def execute(
        self,
        request: Any,
        event_handler: Optional[Any] = None,
    ) -> CodingAgentResult:
        self.invocation_count += 1
        src_dir = os.path.join(self.workspace_root, "src")
        tests_dir = os.path.join(self.workspace_root, "tests")
        os.makedirs(src_dir, exist_ok=True)
        os.makedirs(tests_dir, exist_ok=True)

        instructions = getattr(request, "instructions", "") or getattr(request, "prompt", "")
        req_context = getattr(request, "context", {}) or {}

        is_fix_round = (
            req_context.get("revision_number", 1) > 1
            or "PRIOR ENGINEERING ITERATION CONTEXT" in instructions
            or self.invocation_count > 1
        )

        if not is_fix_round:
            # Round 1: Initial implementation with the known defect
            limiter_code = (
                "class RateLimiter:\n"
                "    def __init__(self, capacity: int = 5, refill_rate: float = 1.0):\n"
                "        self.capacity = capacity\n"
                "        self.tokens = capacity\n"
                "        self.refill_rate = refill_rate\n\n"
                "    def consume(self, count: int = 1) -> bool:\n"
                "        if self.tokens < count:\n"
                "            raise ValueError('Empty bucket')  # Flaw: raises exception\n"
                "        self.tokens -= count\n"
                "        return True\n"
            )
            test_code = (
                "from src.rate_limiter import RateLimiter\n\n"
                "def test_consume_happy_path():\n"
                "    limiter = RateLimiter(capacity=5)\n"
                "    assert limiter.consume(1) is True\n"
            )
        else:
            # Round 2: Fixed implementation addressing Tester feedback
            limiter_code = (
                "import time\n\n"
                "class RateLimiter:\n"
                "    def __init__(self, capacity: int = 5, refill_rate: float = 1.0):\n"
                "        self.capacity = capacity\n"
                "        self.tokens = capacity\n"
                "        self.refill_rate = refill_rate\n"
                "        self.last_refill = time.time()\n\n"
                "    def consume(self, count: int = 1) -> tuple[bool, float]:\n"
                "        if self.tokens < count:\n"
                "            retry_after = (count - self.tokens) / self.refill_rate\n"
                "            return False, retry_after\n"
                "        self.tokens -= count\n"
                "        return True, 0.0\n"
            )
            test_code = (
                "from src.rate_limiter import RateLimiter\n\n"
                "def test_consume_success():\n"
                "    limiter = RateLimiter(capacity=2)\n"
                "    success, retry_after = limiter.consume(1)\n"
                "    assert success is True\n"
                "    assert retry_after == 0.0\n\n"
                "def test_consume_exhaustion_graceful():\n"
                "    limiter = RateLimiter(capacity=1)\n"
                "    limiter.consume(1)\n"
                "    success, retry_after = limiter.consume(1)\n"
                "    assert success is False\n"
                "    assert retry_after > 0.0\n"
            )

        limiter_path = os.path.join(src_dir, "rate_limiter.py")
        test_path = os.path.join(tests_dir, "test_rate_limiter.py")

        with open(limiter_path, "w") as f:
            f.write(limiter_code)
        with open(test_path, "w") as f:
            f.write(test_code)

        self.written_files.extend(["src/rate_limiter.py", "tests/test_rate_limiter.py"])

        exec_id = getattr(request, "execution_id", "exec-mock")
        wo_id = getattr(request, "work_order_id", "wo-mock")

        return CodingAgentResult(
            execution_id=exec_id,
            work_order_id=wo_id,
            status=CodingAgentExecutionStatus.COMPLETED,
            output_text="2 passed in 0.02s" if is_fix_round else "1 passed in 0.01s",
            metadata={
                "files_modified": ["src/rate_limiter.py", "tests/test_rate_limiter.py"],
                "commands_executed": ["pytest tests/test_rate_limiter.py"],
                "summary": "Implemented rate limiter with token bucket algorithm" if not is_fix_round else "Fixed rate limiter to return 429 tuple instead of raising ValueError",
            },
        )


# =====================================================================
# Main Workforce Integration Test Suite
# =====================================================================

class TestProgrammerWorkforceE2E(unittest.TestCase):
    """
    PROGRAMMER V1 — PHASE 8.7: Workforce Integration End-to-End Test Suite.
    Validates Programmer's integration across the broader AutonomOS workforce:
    Researcher -> Manager -> Programmer -> Cline -> Designer / Tester -> Manager -> Iteration -> Delivery.
    """

    def setUp(self) -> None:
        self.temp_dir = tempfile.mkdtemp(prefix="prog_workforce_e2e_")
        self.workspace_root = os.path.join(self.temp_dir, "workspace")
        os.makedirs(os.path.join(self.workspace_root, "src"), exist_ok=True)
        os.makedirs(os.path.join(self.workspace_root, "tests"), exist_ok=True)

        self.project_id = "proj-workforce-e2e"
        self.task_id = "task-rate-limiter-101"
        self.correlation_id = "corr-wf-87-001"

        self.bridge = ProgrammerManagerBridge()
        self.researcher = FakeResearcher()
        self.designer = FakeDesigner()
        self.tester = FakeTester()
        self.cline = DeterministicClineBackend(self.workspace_root)

    def tearDown(self) -> None:
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def _create_cline_request(
        self,
        prompt: str,
        work_order_id: str = "wo-wf-1",
        execution_id: str = "exec-wf-1",
        metadata: Optional[dict[str, Any]] = None,
    ) -> Any:
        class SimpleRequest:
            def __init__(self, p: str, w_id: str, e_id: str, meta: dict[str, Any]):
                self.prompt = p
                self.instructions = p
                self.work_order_id = w_id
                self.execution_id = e_id
                self.context = meta
                self.metadata = meta
                self.request_id = "req-" + e_id
        return SimpleRequest(prompt, work_order_id, execution_id, metadata or {})

    # -----------------------------------------------------------------
    # Test 1: Complete Workforce Iteration Lifecycle (Master E2E)
    # -----------------------------------------------------------------
    def test_01_complete_workforce_iteration_lifecycle(self) -> None:
        """
        Validates the entire conceptual workflow across all 15 stages:
        Researcher -> ResearchResult -> Manager -> ProgrammerWorkOrder ->
        Programmer -> Cline -> verified implementation -> ProgrammerResult ->
        Manager -> Designer/Tester task -> WorkerResult -> Manager ->
        Programmer revision if required -> final verified delivery.
        """
        # Stage 1: Researcher produces ResearchResult
        research_result = self.researcher.conduct_research(
            query="HTTP API token bucket rate limiting standard",
            request_id="req-res-wf-1",
            project_id=self.project_id,
        )
        self.assertEqual(len(research_result.evidence), 3)
        self.assertEqual(len(research_result.recommendations), 2)

        # Stage 2: Manager processes ResearchResult through adapter
        # Manager selects specific evidence and treats recommendations as advisory
        base_work_order = ResearchToWorkOrderAdapter.transform(
            research_result=research_result,
            manager_task_id=self.task_id,
            project_id=self.project_id,
            correlation_id=self.correlation_id,
            objective="Implement production-grade HTTP rate limiter with token bucket algorithm",
            selected_evidence_ids=["ev-token-bucket-algo", "ev-status-429-spec"],
            accepted_recommendation_ids=[],  # Recommendations are not authority
            allowed_paths=["src/", "tests/"],
            writable_paths=["src/rate_limiter.py", "tests/test_rate_limiter.py"],
        )
        self.assertEqual(base_work_order.revision_number, 1)
        self.assertEqual(len(base_work_order.research_evidence), 2)
        self.assertEqual(len(base_work_order.context["advisory_recommendations"]), 2)

        # Stage 3: Manager registers and issues work order
        self.bridge.work_orders[base_work_order.work_order_id] = base_work_order
        execution_1 = self.bridge.dispatch_work_order(base_work_order)
        self.assertEqual(execution_1.status, ProgrammerExecutionStatus.STARTING)

        # Stage 4: Programmer invokes Cline to implement code
        req_1 = self._create_cline_request(
            prompt=base_work_order.objective,
            work_order_id=base_work_order.work_order_id,
            execution_id=execution_1.execution_id,
            metadata={"revision_number": base_work_order.revision_number},
        )
        cline_res_1 = self.cline.execute(req_1)
        self.assertEqual(cline_res_1.status, CodingAgentExecutionStatus.COMPLETED)
        self.assertTrue(os.path.exists(os.path.join(self.workspace_root, "src", "rate_limiter.py")))

        # Stage 5: Programmer self-verification
        execution_1.transition_to(ProgrammerExecutionStatus.RUNNING, "Execution running")
        execution_1.transition_to(ProgrammerExecutionStatus.VERIFYING, "Self-verification complete")

        # Stage 6: Programmer produces ProgrammerResult
        prog_res_1 = ProgrammerResult(
            result_id=new_result_id(),
            work_order_id=base_work_order.work_order_id,
            execution_id=execution_1.execution_id,
            task_id=self.task_id,
            project_id=self.project_id,
            correlation_id=self.correlation_id,
            status=ProgrammerResultStatus.SUCCESS,
            summary="Implemented RateLimiter token bucket with unit tests",
            files_changed=["src/rate_limiter.py", "tests/test_rate_limiter.py"],
            commands_executed=["pytest tests/test_rate_limiter.py"],
            test_results=[
                TestResultRecord(
                    command="pytest tests/test_rate_limiter.py",
                    passed=True,
                    tests_passed=1,
                    tests_failed=0,
                )
            ],
            acceptance_results=[
                AcceptanceCriterionResult(
                    criterion_id=ac.criterion_id,
                    status=AcceptanceStatus.PASS,
                    description=ac.description,
                    evidence_ids=["ev-test-pass-1"],
                )
                for ac in base_work_order.acceptance_criteria
            ],
        )

        # Stage 7: Manager receives result
        output_1 = self.bridge.receive_result(prog_res_1, execution_1, base_work_order)
        self.assertEqual(output_1.status, TaskStatus.COMPLETED)
        self.assertEqual(execution_1.status, ProgrammerExecutionStatus.COMPLETED)

        # Stage 8: Programmer constructs structured handoff to Designer
        designer_handoff = ProgrammerToDesignerHandoffBuilder.build(
            work_order=base_work_order,
            target_worker_id=self.designer.worker_id,
            objective="Provide UI/UX contracts for rate limiter endpoints",
            capabilities=[
                BackendCapability(
                    capability_id="cap-token-bucket",
                    name="TokenBucketRateLimiting",
                    description="In-memory token bucket rate limiting capability",
                    supporting_evidence_ids=["ev-token-bucket-algo"],
                )
            ],
            endpoints=[
                ApiEndpointContract(
                    endpoint_id="ep-consume",
                    path="/api/v1/consume",
                    method="POST",
                    source_file="src/rate_limiter.py",
                    supporting_evidence_ids=["ev-status-429-spec"],
                )
            ],
            context_items=[
                DesignerContextItem(
                    statement="Client UI must display retry countdown badge when 429 is received",
                    classification=DesignerContextClassification.TECHNICAL_REQUIREMENT,
                )
            ],
        )
        designer_review = self.designer.review_engineering_handoff(designer_handoff)
        self.assertEqual(designer_review["status"], "APPROVED")

        # Stage 9: Programmer constructs structured handoff to Tester
        diff_ver_1 = DiffVerification(
            verification_id=new_diff_verification_id(),
            work_order_id=base_work_order.work_order_id,
            files_created=["src/rate_limiter.py", "tests/test_rate_limiter.py"],
        )
        tester_handoff = ProgrammerToTesterHandoffBuilder.build(
            work_order=base_work_order,
            implementation_summary="Implemented RateLimiter token bucket with unit tests",
            target_worker_id=self.tester.worker_id,
            objective="Verify rate limiter under token exhaustion conditions",
            diff_verification=diff_ver_1,
            changed_apis=[
                ChangedApiContract(
                    endpoint_ref="POST /api/v1/consume",
                    change_type=ApiChangeType.ADDED,
                    description="Token bucket consume endpoint",
                    supporting_evidence_ids=["ev-status-429-spec"],
                )
            ],
        )
        self.assertTrue(tester_handoff.context["previous_verification"]["summary"]["independent_testing_recommended"])

        # Stage 10: Tester independently tests and identifies failure
        qa_status_1, feedback_list_1 = self.tester.run_independent_testing(
            handoff=tester_handoff,
            workspace_root=self.workspace_root,
            round_num=1,
        )
        self.assertEqual(qa_status_1, "FAILED")
        self.assertEqual(len(feedback_list_1), 1)
        defect_feedback = feedback_list_1[0]
        self.assertEqual(defect_feedback.issue_type, FeedbackIssueType.BUG)

        # Stage 11: Manager creates corrective ProgrammerWorkOrder (REQUEST_FIX)
        iteration_outcome = self.bridge.orchestrate_iteration(
            base_work_order=base_work_order,
            decision=ManagerIterationDecision.REQUEST_FIX,
            feedback_items=[defect_feedback],
            accepted_feedback_ids=[defect_feedback.feedback_id],
            previous_execution=execution_1,
            previous_result=prog_res_1,
            manager_notes="Fix token exhaustion bug: return (False, retry_after) instead of raising ValueError",
        )
        revised_work_order = iteration_outcome.revised_work_order
        self.assertIsNotNone(revised_work_order)
        self.assertEqual(revised_work_order.revision_number, 2)
        self.assertEqual(revised_work_order.parent_work_order_id, base_work_order.work_order_id)

        # Stage 12: Programmer prompt receives PriorEngineeringContext (Dimension 17)
        execution_2 = self.bridge.dispatch_work_order(revised_work_order)
        provisioner_2 = WorkspaceProvisioner(
            project_resolver={revised_work_order.project_id: self.workspace_root}
        )
        prov_res_2 = provisioner_2.provision(revised_work_order, execution_2)
        self.assertTrue(prov_res_2.is_ready())
        instruction_prompt = ProgrammerPromptBuilder.build_instruction_prompt(
            revised_work_order, prov_res_2.execution_context
        )
        self.assertIn("17. PRIOR ENGINEERING ITERATION CONTEXT", instruction_prompt)
        self.assertIn("What Failed (Observed Failures & Defects)", instruction_prompt)

        # Stage 13: Programmer dispatches revision and fixes issue via Cline
        req_2 = self._create_cline_request(
            prompt=instruction_prompt,
            work_order_id=revised_work_order.work_order_id,
            execution_id=execution_2.execution_id,
            metadata={"revision_number": revised_work_order.revision_number},
        )
        cline_res_2 = self.cline.execute(req_2)
        self.assertEqual(cline_res_2.status, CodingAgentExecutionStatus.COMPLETED)

        # Verify code was updated
        with open(os.path.join(self.workspace_root, "src", "rate_limiter.py"), "r") as f:
            fixed_code = f.read()
        self.assertNotIn("raise ValueError", fixed_code)
        self.assertIn("retry_after", fixed_code)

        execution_2.transition_to(ProgrammerExecutionStatus.RUNNING, "Execution 2 running")
        execution_2.transition_to(ProgrammerExecutionStatus.VERIFYING, "Execution 2 verifying")
        test_rec_2 = TestResultRecord(
            command="pytest tests/test_rate_limiter.py",
            passed=True,
            tests_passed=2,
            tests_failed=0,
        )
        prog_res_2 = ProgrammerResult(
            result_id=new_result_id(),
            work_order_id=revised_work_order.work_order_id,
            execution_id=execution_2.execution_id,
            task_id=self.task_id,
            project_id=self.project_id,
            correlation_id=self.correlation_id,
            status=ProgrammerResultStatus.SUCCESS,
            summary="Fixed token exhaustion handling to return graceful status",
            files_changed=["src/rate_limiter.py", "tests/test_rate_limiter.py"],
            commands_executed=["pytest tests/test_rate_limiter.py"],
            test_results=[test_rec_2],
            acceptance_results=[
                AcceptanceCriterionResult(
                    criterion_id=ac.criterion_id,
                    status=AcceptanceStatus.PASS,
                    description=ac.description,
                    evidence_ids=["ev-test-pass-2"],
                )
                for ac in revised_work_order.acceptance_criteria
            ],
        )
        self.bridge.receive_result(prog_res_2, execution_2, revised_work_order)

        # Stage 14: Tester re-tests the resulting change (Round 2)
        tester_handoff_2 = ProgrammerToTesterHandoffBuilder.build(
            work_order=revised_work_order,
            implementation_summary="Fixed token exhaustion handling to return graceful status",
            target_worker_id=self.tester.worker_id,
            objective="Re-verify rate limiter exhaustion fix",
            diff_verification=DiffVerification(
                verification_id=new_diff_verification_id(),
                work_order_id=revised_work_order.work_order_id,
                files_changed=["src/rate_limiter.py", "tests/test_rate_limiter.py"],
            ),
        )
        qa_status_2, feedback_list_2 = self.tester.run_independent_testing(
            handoff=tester_handoff_2,
            workspace_root=self.workspace_root,
            round_num=2,
        )
        self.assertEqual(qa_status_2, "PASSED")
        self.assertEqual(len(feedback_list_2), 0)

        # Stage 15: Manager accepts delivery and compiles final DeliveryPackage
        accept_outcome = self.bridge.orchestrate_iteration(
            base_work_order=revised_work_order,
            decision=ManagerIterationDecision.ACCEPT,
            previous_execution=execution_2,
            previous_result=prog_res_2,
            manager_notes="All tests verified and accepted by QA",
        )
        self.assertIsNone(accept_outcome.revised_work_order)
        self.assertEqual(accept_outcome.decision, ManagerIterationDecision.ACCEPT)

        # Compile final verified DeliveryPackage
        git_ctx = GitExecutionContext(
            repository_id=new_git_repository_id(),
            execution_id=execution_2.execution_id,
            work_order_id=revised_work_order.work_order_id,
            base_revision=GitRevision(revision_id=new_git_revision_id(), commit_hash="a" * 40),
            workspace_path=self.workspace_root,
            project_id=self.project_id,
        )
        ver_summary = VerificationSummary(
            overall_status=VerificationSummaryStatus.VERIFIED,
            execution_id=execution_2.execution_id,
            work_order_id=revised_work_order.work_order_id,
        )
        evidence_rec = VerificationEvidence(
            evidence_id=new_verification_evidence_id(),
            execution_id=execution_2.execution_id,
            work_order_id=revised_work_order.work_order_id,
            source_type=VerificationEvidenceSourceType.COMMAND_OUTPUT,
            description="Pytest passed",
            data={"output": "2 passed in 0.02s"},
        )
        delivery_package = DeliveryPreparer().prepare(
            execution_context=git_ctx,
            work_order=revised_work_order,
            verification_summary=ver_summary,
            acceptance_results=prog_res_2.acceptance_results,
            test_results=[test_rec_2],
            evidence=[evidence_rec],
        )
        self.assertIsNotNone(delivery_package.delivery_id)
        self.assertEqual(delivery_package.work_order_id, revised_work_order.work_order_id)
        self.assertTrue(delivery_package.tests_run_summary()["all_passed"])
        self.assertEqual(delivery_package.recommended_disposition, ManagerDisposition.ACCEPT)

    # -----------------------------------------------------------------
    # Test 2: Research Evidence Becomes Programmer Context Through Manager
    # -----------------------------------------------------------------
    def test_02_research_evidence_becomes_programmer_context(self) -> None:
        """Verifies research evidence selected by Manager becomes Programmer context."""
        research_result = self.researcher.conduct_research("Token bucket RFC")
        wo = ResearchToWorkOrderAdapter.transform(
            research_result=research_result,
            manager_task_id=self.task_id,
            project_id=self.project_id,
            correlation_id=self.correlation_id,
            objective="Implement rate limiting algorithm",
            selected_evidence_ids=["ev-token-bucket-algo", "ev-status-429-spec"],
        )

        self.assertEqual(len(wo.research_evidence), 2)
        ev_ids = [ref.evidence_id for ref in wo.research_evidence]
        self.assertIn("ev-token-bucket-algo", ev_ids)
        self.assertIn("ev-status-429-spec", ev_ids)

        # Verify prompt builder includes research evidence section
        provisioner = WorkspaceProvisioner(
            project_resolver={wo.project_id: self.workspace_root}
        )
        execution = wo.create_execution()
        prov_res = provisioner.provision(wo, execution)
        self.assertTrue(prov_res.is_ready())
        prompt = ProgrammerPromptBuilder.build_instruction_prompt(wo, prov_res.execution_context)
        self.assertIn("RELEVANT RESEARCH EVIDENCE", prompt)
        self.assertIn("Token bucket algorithm allows bursts", prompt)

    # -----------------------------------------------------------------
    # Test 3: Research Recommendations Are Not Treated as Authority
    # -----------------------------------------------------------------
    def test_03_research_recommendations_not_treated_as_authority(self) -> None:
        """Verifies unaccepted research recommendations remain advisory, not requirements."""
        research_result = self.researcher.conduct_research("Token bucket RFC")
        wo = ResearchToWorkOrderAdapter.transform(
            research_result=research_result,
            manager_task_id=self.task_id,
            project_id=self.project_id,
            correlation_id=self.correlation_id,
            objective="Implement rate limiter",
            selected_evidence_ids=["ev-token-bucket-algo"],
            accepted_recommendation_ids=[],  # Unaccepted by Manager
        )

        # Recommendations must NOT be converted to mandatory technical requirements
        for req in wo.technical_requirements:
            self.assertNotIn("Redis Cluster", req)
            self.assertNotIn("sliding window", req)

        # They must be isolated in advisory recommendations
        self.assertEqual(len(wo.context["advisory_recommendations"]), 2)
        rec_texts = [r.get("action", "") for r in wo.context["advisory_recommendations"]]
        self.assertTrue(any("Redis Cluster" in t for t in rec_texts))

    # -----------------------------------------------------------------
    # Test 4: Programmer Produces Verified Implementation
    # -----------------------------------------------------------------
    def test_04_programmer_produces_verified_implementation(self) -> None:
        """Verifies Programmer executes code changes via Cline backend and verifies them."""
        wo = ProgrammerWorkOrder(
            work_order_id=new_work_order_id(),
            manager_task_id=self.task_id,
            project_id=self.project_id,
            correlation_id=self.correlation_id,
            objective="Implement rate limiter service",
            allowed_paths=["src/", "tests/"],
            writable_paths=["src/rate_limiter.py", "tests/test_rate_limiter.py"],
        )
        self.bridge.work_orders[wo.work_order_id] = wo
        execution = self.bridge.dispatch_work_order(wo)

        req = self._create_cline_request(
            prompt=wo.objective,
            work_order_id=wo.work_order_id,
            execution_id=execution.execution_id,
        )
        result = self.cline.execute(req)
        self.assertEqual(result.status, CodingAgentExecutionStatus.COMPLETED)

        execution.transition_to(ProgrammerExecutionStatus.RUNNING, "Running")
        execution.transition_to(ProgrammerExecutionStatus.VERIFYING, "Verified")

        prog_res = ProgrammerResult(
            result_id=new_result_id(),
            work_order_id=wo.work_order_id,
            execution_id=execution.execution_id,
            task_id=self.task_id,
            project_id=self.project_id,
            correlation_id=self.correlation_id,
            status=ProgrammerResultStatus.SUCCESS,
            summary="Verified implementation complete",
            files_changed=["src/rate_limiter.py"],
            commands_executed=["pytest"],
            test_results=[
                TestResultRecord(
                    command="pytest",
                    passed=True,
                    tests_passed=1,
                    tests_failed=0,
                )
            ],
        )
        output = self.bridge.receive_result(prog_res, execution, wo)
        self.assertEqual(output.status, TaskStatus.COMPLETED)

    # -----------------------------------------------------------------
    # Test 5: Programmer Hands Engineering Context to Designer
    # -----------------------------------------------------------------
    def test_05_programmer_hands_engineering_context_to_designer(self) -> None:
        """Verifies structured handoff communicates capabilities without UX prescription."""
        wo = ProgrammerWorkOrder(
            work_order_id=new_work_order_id(),
            manager_task_id=self.task_id,
            project_id=self.project_id,
            correlation_id=self.correlation_id,
            objective="Rate limiter backend",
            allowed_paths=["src/"],
            writable_paths=["src/rate_limiter.py"],
        )
        res = ProgrammerResult(
            result_id=new_result_id(),
            work_order_id=wo.work_order_id,
            execution_id=new_execution_id(),
            task_id=self.task_id,
            project_id=self.project_id,
            correlation_id=self.correlation_id,
            status=ProgrammerResultStatus.SUCCESS,
            summary="Backend implemented",
            files_changed=["src/rate_limiter.py"],
        )
        handoff = ProgrammerToDesignerHandoffBuilder.build(
            work_order=wo,
            target_worker_id="worker.designer.e2e",
            objective="Handoff rate limiter capabilities to Designer",
            capabilities=[
                BackendCapability(
                    capability_id="cap-rate-limiter",
                    name="RateLimiter",
                    description="Token bucket limiter",
                )
            ],
            endpoints=[
                ApiEndpointContract(
                    endpoint_id="ep-1",
                    path="/consume",
                    method="POST",
                    source_file="src/rate_limiter.py",
                )
            ],
            context_items=[
                DesignerContextItem(
                    statement="Returns Retry-After header",
                    classification=DesignerContextClassification.TECHNICAL_REQUIREMENT,
                )
            ],
        )

        self.assertEqual(handoff.handoff_type, EngineeringHandoffType.PROGRAMMER_TO_DESIGNER)
        self.assertEqual(handoff.source_worker_id, "worker-programmer")
        self.assertEqual(handoff.target_worker_id, "worker.designer.e2e")
        self.assertIn("backend_capabilities", handoff.context)

    # -----------------------------------------------------------------
    # Test 6: Programmer Hands Implementation Context to Tester
    # -----------------------------------------------------------------
    def test_06_programmer_hands_testing_context_to_tester(self) -> None:
        """Verifies structured handoff includes files, APIs, and non-self-grading notice."""
        wo = ProgrammerWorkOrder(
            work_order_id=new_work_order_id(),
            manager_task_id=self.task_id,
            project_id=self.project_id,
            correlation_id=self.correlation_id,
            objective="Rate limiter backend",
            allowed_paths=["src/"],
            writable_paths=["src/rate_limiter.py"],
        )
        res = ProgrammerResult(
            result_id=new_result_id(),
            work_order_id=wo.work_order_id,
            execution_id=new_execution_id(),
            task_id=self.task_id,
            project_id=self.project_id,
            correlation_id=self.correlation_id,
            status=ProgrammerResultStatus.SUCCESS,
            summary="Backend implemented",
            files_changed=["src/rate_limiter.py"],
        )
        diff_ver = DiffVerification(
            verification_id=new_diff_verification_id(),
            work_order_id=wo.work_order_id,
            files_created=["src/rate_limiter.py"],
        )
        handoff = ProgrammerToTesterHandoffBuilder.build(
            work_order=wo,
            implementation_summary="Backend implemented",
            target_worker_id="worker.tester.e2e",
            objective="Handoff for QA verification",
            diff_verification=diff_ver,
        )

        self.assertEqual(handoff.handoff_type, EngineeringHandoffType.PROGRAMMER_TO_TESTER)
        self.assertEqual(handoff.source_worker_id, "worker-programmer")
        self.assertEqual(handoff.target_worker_id, "worker.tester.e2e")
        self.assertTrue(handoff.context["previous_verification"]["summary"]["independent_testing_recommended"])

    # -----------------------------------------------------------------
    # Test 7: Tester Independently Identifies Failures
    # -----------------------------------------------------------------
    def test_07_tester_independently_identifies_failures(self) -> None:
        """Verifies Tester identifies defects regardless of Programmer self-passed tests."""
        wo = ProgrammerWorkOrder(
            work_order_id=new_work_order_id(),
            manager_task_id=self.task_id,
            project_id=self.project_id,
            correlation_id=self.correlation_id,
            objective="Rate limiter",
            allowed_paths=["src/"],
            writable_paths=["src/rate_limiter.py"],
        )
        res = ProgrammerResult(
            result_id=new_result_id(),
            work_order_id=wo.work_order_id,
            execution_id=new_execution_id(),
            task_id=self.task_id,
            project_id=self.project_id,
            correlation_id=self.correlation_id,
            status=ProgrammerResultStatus.SUCCESS,
            summary="Programmer self-passed 1 test",
            files_changed=["src/rate_limiter.py"],
        )
        # Execute Cline round 1 (flawed code)
        req = self._create_cline_request(prompt="write initial limiter")
        self.cline.execute(req)

        handoff = ProgrammerToTesterHandoffBuilder.build(
            work_order=wo,
            implementation_summary="Programmer self-passed 1 test",
            target_worker_id=self.tester.worker_id,
            objective="Test rate limiter",
        )
        status, feedback = self.tester.run_independent_testing(
            handoff=handoff,
            workspace_root=self.workspace_root,
            round_num=1,
        )
        self.assertEqual(status, "FAILED")
        self.assertEqual(len(feedback), 1)
        self.assertIn("raises ValueError", feedback[0].issue)

    # -----------------------------------------------------------------
    # Test 8: Tester Feedback Reaches Manager
    # -----------------------------------------------------------------
    def test_08_tester_feedback_reaches_manager(self) -> None:
        """Verifies structured EngineeringFeedback contains reproduction info and evidence."""
        fb = EngineeringFeedback(
            feedback_id=new_feedback_id(),
            source_worker="worker.tester.e2e",
            source_task=self.task_id,
            target_project=self.project_id,
            related_work_order="pwo-test-08",
            issue="Token exhaustion raises ValueError",
            issue_type=FeedbackIssueType.BUG,
            severity=FeedbackSeverity.HIGH,
            observed_behavior="ValueError on empty bucket",
            expected_behavior="Return 429 status code",
            reproduction_information="Call consume() when tokens=0",
            confidence=FeedbackConfidence.CONFIRMED,
            evidence=["ev-exhaustion-log"],
        )
        d = fb.to_dict()
        self.assertEqual(d["feedback_id"], fb.feedback_id)
        self.assertEqual(d["severity"], "HIGH")
        self.assertTrue(len(d["evidence"]) > 0)
        self.assertIn("ev-exhaustion-log", str(d["evidence"]))

    # -----------------------------------------------------------------
    # Test 9: Manager Creates Corrective ProgrammerWorkOrder
    # -----------------------------------------------------------------
    def test_09_manager_creates_corrective_work_order(self) -> None:
        """Verifies Manager generates a revised WorkOrder with incremented revision."""
        base_wo = ProgrammerWorkOrder(
            work_order_id=new_work_order_id(),
            manager_task_id=self.task_id,
            project_id=self.project_id,
            correlation_id=self.correlation_id,
            objective="Initial implementation",
            revision_number=1,
            allowed_paths=["src/"],
            writable_paths=["src/rate_limiter.py"],
        )
        fb = EngineeringFeedback(
            feedback_id=new_feedback_id(),
            source_worker="worker.tester.e2e",
            source_task=self.task_id,
            target_project=self.project_id,
            related_work_order=base_wo.work_order_id,
            issue="Crash on empty tokens",
            issue_type=FeedbackIssueType.BUG,
            severity=FeedbackSeverity.CRITICAL,
            observed_behavior="Server error 500",
            expected_behavior="Return 429",
            reproduction_information="consume() twice",
        )
        outcome = self.bridge.orchestrate_iteration(
            base_work_order=base_wo,
            decision=ManagerIterationDecision.REQUEST_FIX,
            feedback_items=[fb],
            accepted_feedback_ids=[fb.feedback_id],
            manager_notes="Handle empty bucket gracefully",
        )
        rev_wo = outcome.revised_work_order
        self.assertIsNotNone(rev_wo)
        self.assertEqual(rev_wo.revision_number, 2)
        self.assertEqual(rev_wo.parent_work_order_id, base_wo.work_order_id)
        self.assertTrue(any("Crash on empty tokens" in r for r in rev_wo.technical_requirements))

    # -----------------------------------------------------------------
    # Test 10: Programmer Receives Corrective WorkOrder with Lineage
    # -----------------------------------------------------------------
    def test_10_programmer_receives_corrective_work_order_with_lineage(self) -> None:
        """Verifies PriorEngineeringContext provides what was implemented, what failed, and remaining fixes."""
        base_wo = ProgrammerWorkOrder(
            work_order_id=new_work_order_id(),
            manager_task_id=self.task_id,
            project_id=self.project_id,
            correlation_id=self.correlation_id,
            objective="Initial implementation",
            revision_number=1,
            allowed_paths=["src/"],
            writable_paths=["src/rate_limiter.py"],
        )
        exec_1 = base_wo.create_execution()
        res_1 = ProgrammerResult(
            result_id=new_result_id(),
            work_order_id=base_wo.work_order_id,
            execution_id=exec_1.execution_id,
            task_id=self.task_id,
            project_id=self.project_id,
            correlation_id=self.correlation_id,
            status=ProgrammerResultStatus.SUCCESS,
            summary="Implemented initial limiter",
            files_changed=["src/rate_limiter.py"],
        )
        fb = EngineeringFeedback(
            feedback_id=new_feedback_id(),
            source_worker="worker.tester.e2e",
            source_task=self.task_id,
            target_project=self.project_id,
            related_work_order=base_wo.work_order_id,
            issue="Crash on empty tokens",
            issue_type=FeedbackIssueType.BUG,
            severity=FeedbackSeverity.HIGH,
            observed_behavior="Unhandled exception",
            expected_behavior="Return 429",
            reproduction_information="consume() twice",
        )
        outcome = self.bridge.orchestrate_iteration(
            base_work_order=base_wo,
            decision=ManagerIterationDecision.REQUEST_FIX,
            feedback_items=[fb],
            accepted_feedback_ids=[fb.feedback_id],
            previous_execution=exec_1,
            previous_result=res_1,
            manager_notes="Fix crash",
        )
        rev_wo = outcome.revised_work_order
        self.assertIsNotNone(rev_wo)
        prior_ctx = PriorEngineeringContext.from_work_order(rev_wo)
        self.assertIsNotNone(prior_ctx)
        self.assertEqual(prior_ctx.previous_work_order_id, base_wo.work_order_id)
        self.assertEqual(prior_ctx.previous_execution_id, exec_1.execution_id)
        self.assertTrue(len(prior_ctx.failures_observed) > 0)

    # -----------------------------------------------------------------
    # Test 11: Programmer Fixes the Issue via Cline
    # -----------------------------------------------------------------
    def test_11_programmer_fixes_issue_via_cline(self) -> None:
        """Verifies Programmer uses prior defect context to implement and self-verify the fix."""
        # Initial write (round 1)
        req_1 = self._create_cline_request(prompt="initial")
        self.cline.execute(req_1)
        with open(os.path.join(self.workspace_root, "src", "rate_limiter.py"), "r") as f:
            self.assertIn("raise ValueError", f.read())

        # Corrective write (round 2)
        req_2 = self._create_cline_request(
            prompt="# 17. PRIOR ENGINEERING ITERATION CONTEXT\nFix token exhaustion",
            metadata={"revision_number": 2},
        )
        self.cline.execute(req_2)
        with open(os.path.join(self.workspace_root, "src", "rate_limiter.py"), "r") as f:
            code = f.read()
        self.assertNotIn("raise ValueError", code)
        self.assertIn("retry_after", code)

    # -----------------------------------------------------------------
    # Test 12: Tester Can Re-Test the Resulting Change
    # -----------------------------------------------------------------
    def test_12_tester_retests_resulting_change(self) -> None:
        """Verifies Tester independently re-tests the workspace and confirms resolution."""
        # Setup fixed code
        req = self._create_cline_request(
            prompt="Fix",
            metadata={"revision_number": 2},
        )
        self.cline.execute(req)

        wo_rev2 = ProgrammerWorkOrder(
            work_order_id=new_work_order_id(),
            manager_task_id=self.task_id,
            project_id=self.project_id,
            correlation_id=self.correlation_id,
            objective="Fixed limiter",
            revision_number=2,
            allowed_paths=["src/"],
            writable_paths=["src/rate_limiter.py"],
        )
        res_rev2 = ProgrammerResult(
            result_id=new_result_id(),
            work_order_id=wo_rev2.work_order_id,
            execution_id=new_execution_id(),
            task_id=self.task_id,
            project_id=self.project_id,
            correlation_id=self.correlation_id,
            status=ProgrammerResultStatus.SUCCESS,
            summary="Fixed defect",
            files_changed=["src/rate_limiter.py"],
        )
        handoff = ProgrammerToTesterHandoffBuilder.build(
            work_order=wo_rev2,
            implementation_summary="Fixed defect",
            target_worker_id=self.tester.worker_id,
            objective="Re-test fix",
        )
        status, feedback = self.tester.run_independent_testing(
            handoff=handoff,
            workspace_root=self.workspace_root,
            round_num=2,
        )
        self.assertEqual(status, "PASSED")
        self.assertEqual(len(feedback), 0)

    # -----------------------------------------------------------------
    # Test 13: Final Delivery Contains Complete Evidence
    # -----------------------------------------------------------------
    def test_13_final_delivery_contains_complete_evidence(self) -> None:
        """Verifies final DeliveryPackage aggregates test records, diff verification, and evidence."""
        wo = ProgrammerWorkOrder(
            work_order_id=new_work_order_id(),
            manager_task_id=self.task_id,
            project_id=self.project_id,
            correlation_id=self.correlation_id,
            objective="Delivery test",
            allowed_paths=["src/"],
            writable_paths=["src/rate_limiter.py"],
        )
        res = ProgrammerResult(
            result_id=new_result_id(),
            work_order_id=wo.work_order_id,
            execution_id=new_execution_id(),
            task_id=self.task_id,
            project_id=self.project_id,
            correlation_id=self.correlation_id,
            status=ProgrammerResultStatus.SUCCESS,
            summary="Delivery ready",
            files_changed=["src/rate_limiter.py"],
        )
        git_ctx = GitExecutionContext(
            repository_id=new_git_repository_id(),
            execution_id=res.execution_id,
            work_order_id=wo.work_order_id,
            base_revision=GitRevision(revision_id=new_git_revision_id(), commit_hash="a" * 40),
            workspace_path=self.workspace_root,
            project_id=self.project_id,
        )
        summary = VerificationSummary(
            overall_status=VerificationSummaryStatus.VERIFIED,
            execution_id=res.execution_id,
            work_order_id=wo.work_order_id,
        )
        evidence_rec = VerificationEvidence(
            evidence_id=new_verification_evidence_id(),
            execution_id=res.execution_id,
            work_order_id=wo.work_order_id,
            source_type=VerificationEvidenceSourceType.COMMAND_OUTPUT,
            description="Pytest passed",
            data={"output": "Tests pass"},
        )
        test_rec = TestResultRecord(
            command="pytest test/test_rate_limiter.py",
            passed=True,
            tests_passed=2,
            tests_failed=0,
        )
        delivery = DeliveryPreparer().prepare(
            execution_context=git_ctx,
            work_order=wo,
            verification_summary=summary,
            acceptance_results=res.acceptance_results,
            test_results=[test_rec],
            evidence=[evidence_rec],
        )

        self.assertIsNotNone(delivery.delivery_id)
        self.assertTrue(len(delivery.evidence) > 0)
        self.assertTrue(len(delivery.test_results) > 0)
        self.assertTrue(delivery.verification_summary.is_verified)

    # -----------------------------------------------------------------
    # Test 14: No Worker Can Silently Change Another Worker's Authority
    # -----------------------------------------------------------------
    def test_14_no_worker_can_silently_change_another_authority(self) -> None:
        """
        Verifies role authority boundaries:
        - Programmer cannot make UX styling decisions.
        - Programmer cannot claim testing is complete / unnecessary.
        - Peer workers cannot expand Programmer permissions.
        - Tester cannot complete Programmer tasks.
        """
        wo = ProgrammerWorkOrder(
            work_order_id=new_work_order_id(),
            manager_task_id=self.task_id,
            project_id=self.project_id,
            correlation_id=self.correlation_id,
            objective="Boundary enforcement",
            allowed_paths=["src/"],
            writable_paths=["src/rate_limiter.py"],
        )
        res = ProgrammerResult(
            result_id=new_result_id(),
            work_order_id=wo.work_order_id,
            execution_id=new_execution_id(),
            task_id=self.task_id,
            project_id=self.project_id,
            correlation_id=self.correlation_id,
            status=ProgrammerResultStatus.SUCCESS,
            summary="Completed",
            files_changed=["src/rate_limiter.py"],
        )

        # 14a. Programmer cannot prescribe UX design without Manager mandate
        with self.assertRaises(UnauthorizedUXPrescriptionError):
            ProgrammerToDesignerHandoffBuilder.build(
                work_order=wo,
                target_worker_id="worker.designer.e2e",
                objective="Prescribe UX styling",
                capabilities=[],
                context_items=[
                    DesignerContextItem(
                        statement="Make the banner bright red #FF0000 with 20px padding",
                        classification=DesignerContextClassification.TECHNICAL_REQUIREMENT,  # Illegal!
                    )
                ],
            )

        # 14b. Programmer cannot claim independent testing is unnecessary
        with self.assertRaises(UnauthorizedQAClaimError):
            ProgrammerToTesterHandoffBuilder.build(
                work_order=wo,
                implementation_summary="Testing is complete and independent testing is unnecessary",  # Illegal!
                target_worker_id="worker.tester.e2e",
                objective="Waive testing",
            )

        # 14c. Peer worker feedback cannot expand allowed/writable paths
        fb = EngineeringFeedback(
            feedback_id=new_feedback_id(),
            source_worker="worker.tester.e2e",
            source_task=self.task_id,
            target_project=self.project_id,
            related_work_order=wo.work_order_id,
            issue="Permission expansion attempt",
            issue_type=FeedbackIssueType.OTHER,
            severity=FeedbackSeverity.LOW,
            observed_behavior="Needs wider access",
            expected_behavior="Expand access",
            reproduction_information="N/A",
            suggested_direction="Modify /etc/shadow and expand permissions",
        )
        outcome = self.bridge.orchestrate_iteration(
            base_work_order=wo,
            decision=ManagerIterationDecision.REQUEST_FIX,
            feedback_items=[fb],
            accepted_feedback_ids=[fb.feedback_id],
        )
        # Peer worker feedback cannot expand allowed/writable paths beyond Manager's baseline
        self.assertNotIn("/etc/shadow", outcome.revised_work_order.allowed_paths)
        self.assertEqual(outcome.revised_work_order.allowed_paths, wo.allowed_paths)

    # -----------------------------------------------------------------
    # Test 15: Manager Remains the Organizational Orchestrator
    # -----------------------------------------------------------------
    def test_15_manager_remains_organizational_orchestrator(self) -> None:
        """
        Verifies Manager retains sole authority:
        - Non-fix decisions (ACCEPT, REJECT, CANCEL, ESCALATE, REQUEST_DESIGN_CHANGE, REQUEST_RESEARCH)
          do not issue a code change work order to Programmer.
        """
        base_wo = ProgrammerWorkOrder(
            work_order_id=new_work_order_id(),
            manager_task_id=self.task_id,
            project_id=self.project_id,
            correlation_id=self.correlation_id,
            objective="Sole orchestrator test",
            allowed_paths=["src/"],
            writable_paths=["src/rate_limiter.py"],
        )

        non_fix_decisions = [
            ManagerIterationDecision.ACCEPT,
            ManagerIterationDecision.REJECT,
            ManagerIterationDecision.CANCEL,
            ManagerIterationDecision.ESCALATE,
            ManagerIterationDecision.REQUEST_DESIGN_CHANGE,
            ManagerIterationDecision.REQUEST_RESEARCH,
        ]

        for decision in non_fix_decisions:
            outcome = self.bridge.orchestrate_iteration(
                base_work_order=base_wo,
                decision=decision,
            )
            # Programmer must only execute when a valid WorkOrder is issued
            self.assertIsNone(
                outcome.revised_work_order,
                f"Decision {decision} must not create a revised ProgrammerWorkOrder",
            )
            self.assertEqual(outcome.decision, decision)

    # -----------------------------------------------------------------
    # Test 16: Historical Immutability Across Workforce
    # -----------------------------------------------------------------
    def test_16_historical_immutability_across_workforce(self) -> None:
        """Verifies base work orders and previous executions remain 100% immutable across turns."""
        base_wo = ProgrammerWorkOrder(
            work_order_id=new_work_order_id(),
            manager_task_id=self.task_id,
            project_id=self.project_id,
            correlation_id=self.correlation_id,
            objective="Original immutable order",
            revision_number=1,
            allowed_paths=["src/"],
            writable_paths=["src/rate_limiter.py"],
        )
        base_dict_before = copy.deepcopy(base_wo.to_dict())

        fb = EngineeringFeedback(
            feedback_id=new_feedback_id(),
            source_worker="worker.tester.e2e",
            source_task=self.task_id,
            target_project=self.project_id,
            related_work_order=base_wo.work_order_id,
            issue="Bug found",
            issue_type=FeedbackIssueType.BUG,
            severity=FeedbackSeverity.MEDIUM,
            observed_behavior="Fail",
            expected_behavior="Pass",
            reproduction_information="repro",
        )
        outcome = self.bridge.orchestrate_iteration(
            base_work_order=base_wo,
            decision=ManagerIterationDecision.REQUEST_FIX,
            feedback_items=[fb],
            accepted_feedback_ids=[fb.feedback_id],
        )

        # Confirm base work order was untouched
        self.assertEqual(base_wo.to_dict(), base_dict_before)
        self.assertNotEqual(outcome.revised_work_order.work_order_id, base_wo.work_order_id)
        self.assertEqual(outcome.revised_work_order.revision_number, 2)

    # -----------------------------------------------------------------
    # Test 17: Evidence Attribution and Provenance
    # -----------------------------------------------------------------
    def test_17_evidence_attribution_and_provenance(self) -> None:
        """Verifies every piece of evidence retains worker attribution and lineage."""
        research_result = self.researcher.conduct_research("Rate limiting headers")
        ev = research_result.evidence[0]

        # Verify provenance
        self.assertEqual(ev.provenance.crawler_id, "crawler-rfc-docs")
        self.assertEqual(ev.provenance.request_id, "req-rate-limiter")
        self.assertEqual(ev.provenance.source_ref, "https://tools.ietf.org/rfc/rfc6585.txt")

        # Adapter converts to ResearchEvidenceReference preserving attribution
        ref = ResearchEvidenceReference.from_evidence(
            evidence=ev,
            relevance_notes="Attached by Manager",
        )
        self.assertEqual(ref.evidence_id, ev.evidence_id)
        self.assertEqual(ref.provenance.get("crawler_id"), "crawler-rfc-docs")
        self.assertEqual(ref.source_ref, "https://tools.ietf.org/rfc/rfc6585.txt")
        self.assertEqual(ref.relevance_notes, "Attached by Manager")


if __name__ == "__main__":
    unittest.main()
