from __future__ import annotations

import unittest
import uuid

from core.enums import RiskLevel
from core.models import Evidence as RuntimeEvidence, Task
from core.programmer.contracts.acceptance_criteria import AcceptanceCriterion
from core.programmer.contracts.command_scope import AllowedCommand
from core.programmer.contracts.identifiers import (
    WORK_ORDER_ID_PREFIX,
    new_work_order_id,
)
from core.programmer.contracts.research_reference import ResearchEvidenceReference
from core.programmer.contracts.work_order import ProgrammerWorkOrder
from core.programmer.errors import (
    InvalidBudgetError,
    InvalidCommandScopeError,
    InvalidPathScopeError,
    ProgrammerLineageError,
    ProgrammerValidationError,
)
from core.programmer.types import (
    AcceptanceCriterionType,
    ProgrammerExecutionStatus,
    ProgrammerWorkOrderStatus,
)


class TestProgrammerWorkOrder(unittest.TestCase):
    """
    Unit tests for ProgrammerWorkOrder (Programmer V1, Step 1.2).
    Validates:
    - Minimal valid work order
    - Fully populated work order with all 20+ fields
    - Objective vs instructions vs constraints vs acceptance criteria
    - Path scope validation and contradiction detection
    - Command authorization scope
    - Acceptance criteria modeling and preservation
    - Research evidence linkage and provenance
    - Execution budgets validation
    - Risk level validation
    - Serialization fidelity
    - Compatibility with Task and lineage preservation
    """

    def test_minimal_valid_work_order(self):
        """Verify that a work order with minimal required fields passes validation and sets defaults."""
        wo = ProgrammerWorkOrder(
            work_order_id=new_work_order_id(),
            manager_task_id="task-min-01",
            project_id="proj-autonomos",
            correlation_id="corr-min-01",
            objective="Add health check ping endpoint",
        )
        wo.validate()

        self.assertTrue(wo.work_order_id.startswith(WORK_ORDER_ID_PREFIX))
        self.assertEqual(wo.manager_task_id, "task-min-01")
        self.assertEqual(wo.task_id, "task-min-01")  # alias
        self.assertEqual(wo.project_id, "proj-autonomos")
        self.assertEqual(wo.correlation_id, "corr-min-01")
        self.assertEqual(wo.objective, "Add health check ping endpoint")
        self.assertEqual(wo.iteration_budget, 10)
        self.assertEqual(wo.time_budget, 600)
        self.assertEqual(wo.risk_level, RiskLevel.LOW)
        self.assertEqual(wo.status, ProgrammerWorkOrderStatus.CREATED)
        self.assertEqual(wo.allowed_paths, [])
        self.assertEqual(wo.writable_paths, [])
        self.assertEqual(wo.forbidden_paths, [])
        self.assertEqual(wo.allowed_commands, [])
        self.assertEqual(wo.acceptance_criteria, [])

    def test_fully_populated_work_order(self):
        """Verify complete work order populated with all fields defined in the Step 1.2 specification."""
        cmd_test = AllowedCommand(
            command="pytest tests/unit",
            description="Run unit test suite",
            allow_args=True,
            timeout_seconds=60,
        )
        cmd_lint = AllowedCommand(
            command="flake8 src/auth",
            description="Run linter",
        )

        ac_test = AcceptanceCriterion(
            criterion_id="ac-01",
            description="OAuth2 callback unit tests pass",
            criterion_type=AcceptanceCriterionType.TEST_PASS,
            target="tests/unit/test_oauth.py",
            is_mandatory=True,
        )
        ac_file = AcceptanceCriterion(
            criterion_id="ac-02",
            description="src/auth/oauth.py exists and handles callback",
            criterion_type=AcceptanceCriterionType.FILE_CHANGED,
            target="src/auth/oauth.py",
            is_mandatory=True,
        )

        research_ref = ResearchEvidenceReference(
            evidence_id="ev-101",
            claim_or_fact="OAuth2 RFC 6749 specifies state parameter for CSRF mitigation.",
            source_ref="https://datatracker.ietf.org/doc/html/rfc6749",
            confidence="WELL_SUPPORTED",
            provenance={"request_id": "req-99", "crawler_id": "crawler.web.1"},
            relevance_notes="Ensure state parameter is strictly validated.",
        )

        wo = ProgrammerWorkOrder(
            work_order_id="pwo-full-001",
            manager_task_id="task-oauth-01",
            project_id="proj-autonomos",
            correlation_id="corr-full-001",
            objective="Implement Google and GitHub OAuth2 authentication callback",
            instructions=[
                "Create callback endpoint in src/auth/oauth.py",
                "Validate CSRF state token before exchanging auth code",
                "Ensure error responses return structured JSON error payloads",
            ],
            context={
                "architecture_pattern": "FastAPI router pattern",
                "existing_auth_module": "src/auth/base.py",
            },
            allowed_paths=["src/auth", "tests/unit", "config/oauth.json"],
            writable_paths=["src/auth/oauth.py", "tests/unit/test_oauth.py"],
            read_only_paths=["src/auth/base.py", "config/oauth.json"],
            forbidden_paths=[".git", ".autonomos", "secrets", "/etc"],
            allowed_commands=[cmd_test, cmd_lint],
            constraints=[
                "No external heavy dependencies beyond existing pyjwt and httpx",
                "Do not modify database models or migrations",
            ],
            technical_requirements=[
                "Async endpoint def handle_oauth_callback(provider: str, code: str, state: str)",
                "Support Google and GitHub provider query parameter mapping",
            ],
            acceptance_criteria=[ac_test, ac_file],
            required_checks=["pytest tests/unit/test_oauth.py", "flake8 src/auth"],
            research_evidence=[research_ref],
            dependencies=["task-db-setup"],
            iteration_budget=15,
            time_budget=900,
            risk_level=RiskLevel.MEDIUM,
            status=ProgrammerWorkOrderStatus.ASSIGNED,
            metadata={"assigned_by": "manager.orchestrator"},
        )
        wo.validate(require_acceptance_criteria=True)

        self.assertEqual(wo.work_order_id, "pwo-full-001")
        self.assertEqual(len(wo.instructions), 3)
        self.assertEqual(len(wo.constraints), 2)
        self.assertEqual(len(wo.technical_requirements), 2)
        self.assertEqual(len(wo.allowed_commands), 2)
        self.assertEqual(len(wo.acceptance_criteria), 2)
        self.assertEqual(len(wo.research_evidence), 1)
        self.assertEqual(wo.risk_level, RiskLevel.MEDIUM)
        self.assertEqual(wo.iteration_budget, 15)
        self.assertEqual(wo.time_budget, 900)

    def test_missing_objective_validation(self):
        """Verify that missing or empty objective is rejected by validation."""
        wo_empty = ProgrammerWorkOrder(
            work_order_id=new_work_order_id(),
            manager_task_id="task-1",
            project_id="proj-1",
            correlation_id="corr-1",
            objective="",
        )
        with self.assertRaises(ProgrammerValidationError) as ctx:
            wo_empty.validate()
        self.assertEqual(ctx.exception.code, "PROGRAMMER_VALIDATION_ERROR")
        self.assertEqual(ctx.exception.field_name, "objective")

        wo_whitespace = ProgrammerWorkOrder(
            work_order_id=new_work_order_id(),
            manager_task_id="task-1",
            project_id="proj-1",
            correlation_id="corr-1",
            objective="   \n  \t ",
        )
        with self.assertRaises(ProgrammerValidationError):
            wo_whitespace.validate()

    def test_invalid_manager_lineage(self):
        """Verify that work orders missing manager task ID, project ID, or correlation ID fail."""
        with self.assertRaises(ProgrammerLineageError):
            ProgrammerWorkOrder(
                work_order_id=new_work_order_id(),
                manager_task_id="",  # missing lineage
                project_id="proj-1",
                correlation_id="corr-1",
                objective="Valid objective",
            )

        with self.assertRaises(ProgrammerLineageError):
            ProgrammerWorkOrder(
                work_order_id=new_work_order_id(),
                manager_task_id="task-1",
                project_id="",  # missing project
                correlation_id="corr-1",
                objective="Valid objective",
            )

        with self.assertRaises(ProgrammerLineageError):
            ProgrammerWorkOrder(
                work_order_id=new_work_order_id(),
                manager_task_id="task-1",
                project_id="proj-1",
                correlation_id="",  # missing correlation
                objective="Valid objective",
            )

    def test_invalid_path_scopes(self):
        """Verify path scope validation: whitespace paths, null bytes, and contradictory assignments."""
        # 1. Whitespace path
        wo_ws = ProgrammerWorkOrder(
            work_order_id=new_work_order_id(),
            manager_task_id="task-1",
            project_id="proj-1",
            correlation_id="corr-1",
            objective="Fix bug",
            allowed_paths=["src/auth", "   "],
        )
        with self.assertRaises(InvalidPathScopeError) as ctx:
            wo_ws.validate()
        self.assertEqual(ctx.exception.code, "INVALID_PATH_SCOPE")

        # 2. Null byte path
        wo_null = ProgrammerWorkOrder(
            work_order_id=new_work_order_id(),
            manager_task_id="task-1",
            project_id="proj-1",
            correlation_id="corr-1",
            objective="Fix bug",
            writable_paths=["src/auth\0malicious.py"],
        )
        with self.assertRaises(InvalidPathScopeError):
            wo_null.validate()

        # 3. Path in both forbidden and writable
        wo_conflict_forbidden_writable = ProgrammerWorkOrder(
            work_order_id=new_work_order_id(),
            manager_task_id="task-1",
            project_id="proj-1",
            correlation_id="corr-1",
            objective="Fix bug",
            writable_paths=["src/secrets.env"],
            forbidden_paths=["src/secrets.env"],
        )
        with self.assertRaises(InvalidPathScopeError) as ctx:
            wo_conflict_forbidden_writable.validate()
        self.assertIn("cannot be both forbidden and writable", str(ctx.exception))

        # 4. Path in both forbidden and read_only
        wo_conflict_forbidden_ro = ProgrammerWorkOrder(
            work_order_id=new_work_order_id(),
            manager_task_id="task-1",
            project_id="proj-1",
            correlation_id="corr-1",
            objective="Fix bug",
            read_only_paths=[".git/config"],
            forbidden_paths=[".git/config"],
        )
        with self.assertRaises(InvalidPathScopeError) as ctx:
            wo_conflict_forbidden_ro.validate()
        self.assertIn("cannot be both forbidden and read_only", str(ctx.exception))

        # 5. Path in both writable and read_only
        wo_conflict_rw = ProgrammerWorkOrder(
            work_order_id=new_work_order_id(),
            manager_task_id="task-1",
            project_id="proj-1",
            correlation_id="corr-1",
            objective="Fix bug",
            writable_paths=["src/common.py"],
            read_only_paths=["src/common.py"],
        )
        with self.assertRaises(InvalidPathScopeError) as ctx:
            wo_conflict_rw.validate()
        self.assertIn("cannot be both writable and read_only", str(ctx.exception))

    def test_invalid_command_scope(self):
        """Verify command scope validation for empty or shell-injection command patterns."""
        # Empty command
        wo_empty_cmd = ProgrammerWorkOrder(
            work_order_id=new_work_order_id(),
            manager_task_id="task-1",
            project_id="proj-1",
            correlation_id="corr-1",
            objective="Run tests",
            allowed_commands=[AllowedCommand(command="")],
        )
        with self.assertRaises(InvalidCommandScopeError):
            wo_empty_cmd.validate()

        # Shell chaining prefix
        wo_chain_cmd = ProgrammerWorkOrder(
            work_order_id=new_work_order_id(),
            manager_task_id="task-1",
            project_id="proj-1",
            correlation_id="corr-1",
            objective="Run tests",
            allowed_commands=[AllowedCommand(command="; rm -rf /")],
        )
        with self.assertRaises(InvalidCommandScopeError):
            wo_chain_cmd.validate()

        # Negative timeout
        wo_neg_timeout = ProgrammerWorkOrder(
            work_order_id=new_work_order_id(),
            manager_task_id="task-1",
            project_id="proj-1",
            correlation_id="corr-1",
            objective="Run tests",
            allowed_commands=[AllowedCommand(command="pytest", timeout_seconds=-5)],
        )
        with self.assertRaises(InvalidCommandScopeError):
            wo_neg_timeout.validate()

    def test_invalid_budgets(self):
        """Verify budget constraints: non-positive iteration or time budgets are rejected."""
        wo_zero_iter = ProgrammerWorkOrder(
            work_order_id=new_work_order_id(),
            manager_task_id="task-1",
            project_id="proj-1",
            correlation_id="corr-1",
            objective="Refactor",
            iteration_budget=0,
        )
        with self.assertRaises(InvalidBudgetError) as ctx:
            wo_zero_iter.validate()
        self.assertEqual(ctx.exception.budget_type, "iteration_budget")

        wo_neg_time = ProgrammerWorkOrder(
            work_order_id=new_work_order_id(),
            manager_task_id="task-1",
            project_id="proj-1",
            correlation_id="corr-1",
            objective="Refactor",
            time_budget=-100,
        )
        with self.assertRaises(InvalidBudgetError) as ctx:
            wo_neg_time.validate()
        self.assertEqual(ctx.exception.budget_type, "time_budget")

    def test_invalid_risk_level(self):
        """Verify risk level validation: invalid risk levels are rejected."""
        wo_bad_risk = ProgrammerWorkOrder(
            work_order_id=new_work_order_id(),
            manager_task_id="task-1",
            project_id="proj-1",
            correlation_id="corr-1",
            objective="Refactor",
            risk_level="EXTREME_DANGER",  # not in RiskLevel
        )
        with self.assertRaises(ProgrammerValidationError) as ctx:
            wo_bad_risk.validate()
        self.assertEqual(ctx.exception.field_name, "risk_level")

    def test_empty_acceptance_criteria_validation(self):
        """Verify that work orders enforce non-empty acceptance criteria when required."""
        wo = ProgrammerWorkOrder(
            work_order_id=new_work_order_id(),
            manager_task_id="task-1",
            project_id="proj-1",
            correlation_id="corr-1",
            objective="Add feature",
            acceptance_criteria=[],
        )
        # Passes when require_acceptance_criteria is False (optional in early planning)
        wo.validate(require_acceptance_criteria=False)

        # Fails when require_acceptance_criteria is True (enforced before execution)
        with self.assertRaises(ProgrammerValidationError) as ctx:
            wo.validate(require_acceptance_criteria=True)
        self.assertEqual(ctx.exception.field_name, "acceptance_criteria")

    def test_research_evidence_linkage_and_provenance(self):
        """Verify attaching research evidence and preserving provenance to Researcher contracts."""
        runtime_ev = RuntimeEvidence(
            id="ev-999",
            task_id="task-research-1",
            evidence_type="RESEARCH_FACT",
            data="FastAPI dependency injection enables clean token extraction.",
            checksum="abcdef0123456789abcdef0123456789abcdef0123456789abcdef0123456789",
        )

        ref = ResearchEvidenceReference.from_evidence(
            runtime_ev,
            relevance_notes="Use Depends(get_current_user) in endpoints.",
        )
        self.assertEqual(ref.evidence_id, "ev-999")
        self.assertIn("dependency injection", ref.claim_or_fact)
        self.assertEqual(ref.relevance_notes, "Use Depends(get_current_user) in endpoints.")

        wo = ProgrammerWorkOrder(
            work_order_id=new_work_order_id(),
            manager_task_id="task-1",
            project_id="proj-1",
            correlation_id="corr-1",
            objective="Implement auth endpoints",
            research_evidence=[ref],
        )
        wo.validate()

        # Serialization preserves research evidence and provenance
        d = wo.to_dict()
        self.assertEqual(len(d["research_evidence"]), 1)
        self.assertEqual(d["research_evidence"][0]["evidence_id"], "ev-999")

        restored = ProgrammerWorkOrder.from_dict(d)
        self.assertEqual(len(restored.research_evidence), 1)
        self.assertEqual(restored.research_evidence[0].evidence_id, "ev-999")
        self.assertEqual(restored.research_evidence[0].relevance_notes, "Use Depends(get_current_user) in endpoints.")

    def test_acceptance_criteria_preservation(self):
        """Verify that structured criteria are preserved across serialization."""
        ac1 = AcceptanceCriterion(
            criterion_id="ac-1",
            description="Type checking passes with mypy",
            criterion_type=AcceptanceCriterionType.TYPECHECK_PASS,
            target="src/auth",
            is_mandatory=True,
        )
        ac2 = AcceptanceCriterion.from_str(
            "API backwards compatibility preserved for v1 users",
            criterion_type=AcceptanceCriterionType.API_BEHAVIOR_PRESERVED,
        )

        wo = ProgrammerWorkOrder(
            work_order_id=new_work_order_id(),
            manager_task_id="task-1",
            project_id="proj-1",
            correlation_id="corr-1",
            objective="Enhance auth",
            acceptance_criteria=[ac1, ac2],
        )
        wo.validate(require_acceptance_criteria=True)

        d = wo.to_dict()
        restored = ProgrammerWorkOrder.from_dict(d)
        self.assertEqual(len(restored.acceptance_criteria), 2)
        self.assertEqual(restored.acceptance_criteria[0].criterion_type, AcceptanceCriterionType.TYPECHECK_PASS)
        self.assertEqual(restored.acceptance_criteria[0].target, "src/auth")
        self.assertEqual(restored.acceptance_criteria[1].criterion_type, AcceptanceCriterionType.API_BEHAVIOR_PRESERVED)

    def test_serialization_fidelity_roundtrip(self):
        """Verify that all fields of ProgrammerWorkOrder survive round-trip serialization without loss."""
        wo = ProgrammerWorkOrder(
            work_order_id="pwo-roundtrip-01",
            manager_task_id="task-rt-01",
            project_id="proj-rt",
            correlation_id="corr-rt-01",
            objective="Round-trip test objective",
            instructions=["Step 1", "Step 2"],
            context={"repo_type": "monorepo"},
            allowed_paths=["src/"],
            writable_paths=["src/app.py"],
            read_only_paths=["src/config.py"],
            forbidden_paths=[".env"],
            allowed_commands=[AllowedCommand.from_str("pytest", description="Run unit tests")],
            constraints=["Constraint 1"],
            technical_requirements=["Tech req 1"],
            acceptance_criteria=[AcceptanceCriterion.from_str("Test criterion")],
            required_checks=["pytest"],
            research_evidence=[ResearchEvidenceReference(evidence_id="ev-1", claim_or_fact="Fact 1")],
            dependencies=["dep-task-1"],
            iteration_budget=20,
            time_budget=1200,
            risk_level=RiskLevel.HIGH,
            status=ProgrammerWorkOrderStatus.IN_PROGRESS,
            trace={"span_id": "span-1"},
            metadata={"source": "unit_test"},
        )
        wo.validate(require_acceptance_criteria=True)

        d = wo.to_dict()
        restored = ProgrammerWorkOrder.from_dict(d)
        restored.validate(require_acceptance_criteria=True)

        self.assertEqual(restored.work_order_id, wo.work_order_id)
        self.assertEqual(restored.manager_task_id, wo.manager_task_id)
        self.assertEqual(restored.task_id, wo.task_id)
        self.assertEqual(restored.project_id, wo.project_id)
        self.assertEqual(restored.correlation_id, wo.correlation_id)
        self.assertEqual(restored.objective, wo.objective)
        self.assertEqual(restored.instructions, ["Step 1", "Step 2"])
        self.assertEqual(restored.context["repo_type"], "monorepo")
        self.assertEqual(restored.allowed_paths, ["src/"])
        self.assertEqual(restored.writable_paths, ["src/app.py"])
        self.assertEqual(restored.read_only_paths, ["src/config.py"])
        self.assertEqual(restored.forbidden_paths, [".env"])
        self.assertEqual(len(restored.allowed_commands), 1)
        self.assertEqual(restored.allowed_commands[0].command, "pytest")
        self.assertEqual(restored.constraints, ["Constraint 1"])
        self.assertEqual(restored.technical_requirements, ["Tech req 1"])
        self.assertEqual(len(restored.acceptance_criteria), 1)
        self.assertEqual(restored.required_checks, ["pytest"])
        self.assertEqual(len(restored.research_evidence), 1)
        self.assertEqual(restored.dependencies, ["dep-task-1"])
        self.assertEqual(restored.iteration_budget, 20)
        self.assertEqual(restored.time_budget, 1200)
        self.assertEqual(restored.risk_level, RiskLevel.HIGH)
        self.assertEqual(restored.status, ProgrammerWorkOrderStatus.IN_PROGRESS)
        self.assertEqual(restored.trace["span_id"], "span-1")
        self.assertEqual(restored.metadata["source"], "unit_test")

    def test_construction_from_runtime_task_with_metadata(self):
        """Verify constructing ProgrammerWorkOrder from a Task populated with metadata."""
        task = Task(
            id="task-from-task-01",
            project_id="proj-autonomos",
            title="Implement Rate Limiter Middleware",
            objective="Add token bucket rate limiter to prevent API abuse",
            dependencies=["task-redis-setup"],
            risk=RiskLevel.HIGH,
            success_criteria=[
                {"criterion_id": "sc-1", "description": "Rate limiter tests pass", "criterion_type": "TEST_PASS"}
            ],
            metadata={
                "correlation_id": "corr-rt-99",
                "instructions": ["Use in-memory token bucket", "Return HTTP 429 when exhausted"],
                "allowed_paths": ["src/middleware"],
                "writable_paths": ["src/middleware/rate_limiter.py"],
                "allowed_commands": ["pytest tests/unit/test_rate_limiter.py"],
                "constraints": ["Zero external network dependencies"],
                "iteration_budget": 8,
                "time_budget": 450,
            },
        )

        wo = ProgrammerWorkOrder.from_task(task)
        wo.validate(require_acceptance_criteria=True)

        self.assertEqual(wo.manager_task_id, "task-from-task-01")
        self.assertEqual(wo.task_id, "task-from-task-01")
        self.assertEqual(wo.project_id, "proj-autonomos")
        self.assertEqual(wo.correlation_id, "corr-rt-99")
        self.assertEqual(wo.objective, task.objective)
        self.assertEqual(wo.risk_level, RiskLevel.HIGH)
        self.assertEqual(wo.iteration_budget, 8)
        self.assertEqual(wo.time_budget, 450)
        self.assertEqual(len(wo.acceptance_criteria), 1)
        self.assertEqual(wo.allowed_paths, ["src/middleware"])
        self.assertEqual(wo.writable_paths, ["src/middleware/rate_limiter.py"])
        self.assertEqual(wo.dependencies, ["task-redis-setup"])


if __name__ == "__main__":
    unittest.main()
