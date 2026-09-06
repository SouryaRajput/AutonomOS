import unittest

from core.enums import RiskLevel
from core.models import Task
from core.programmer.contracts import (
    AcceptanceCriterion,
    AllowedCommand,
    ProgrammerExecution,
    ProgrammerWorkOrder,
    ProgrammerWorkOrderValidator,
    ResearchEvidenceReference,
    new_work_order_id,
)
from core.programmer.errors import (
    InvalidBudgetError,
    InvalidCommandScopeError,
    InvalidPathScopeError,
    ProgrammerLineageError,
    ProgrammerValidationError,
)
from core.programmer.types import AcceptanceCriterionType


class TestProgrammerWorkOrderValidation(unittest.TestCase):
    """
    Adversarial and boundary tests for ProgrammerWorkOrder validation and policy enforcement (Step 1.5).
    Guarantees that the Manager-authorized work order is structurally sound, bounded,
    internally consistent, and cannot be expanded by the Programmer worker.
    """

    def setUp(self):
        self.work_order_id = new_work_order_id()
        self.task_id = "task-oauth-01"
        self.project_id = "proj-autonomos"
        self.correlation_id = "corr-oauth-01"

    def _create_base_work_order(self, **kwargs) -> ProgrammerWorkOrder:
        params = {
            "work_order_id": self.work_order_id,
            "manager_task_id": self.task_id,
            "task_id": self.task_id,
            "project_id": self.project_id,
            "correlation_id": self.correlation_id,
            "objective": "Implement OAuth2 login callback",
            "allowed_paths": ["src/auth", "tests/unit"],
            "writable_paths": ["src/auth/oauth.py"],
            "read_only_paths": ["src/auth/base.py"],
            "forbidden_paths": [".git", "secrets"],
            "iteration_budget": 10,
            "time_budget": 600,
            "risk_level": RiskLevel.MEDIUM,
        }
        params.update(kwargs)
        return ProgrammerWorkOrder(**params)

    # -------------------------------------------------------------------------
    # 1. Path Scope Validation & Invariants
    # -------------------------------------------------------------------------

    def test_writable_path_outside_allowed_path(self):
        """Invariant 1: Writable scope cannot exceed allowed scope."""
        # Case A: Clearly outside directory
        wo = self._create_base_work_order(
            allowed_paths=["src/auth"],
            writable_paths=["src/database/models.py"],
        )
        with self.assertRaises(InvalidPathScopeError) as ctx:
            wo.validate()
        self.assertIn("exceeds allowed scope", str(ctx.exception))
        self.assertEqual(ctx.exception.path, "src/database/models.py")

        # Case B: Prefix collision trap (e.g. 'src/auth' vs 'src/author.py')
        wo_prefix_trap = self._create_base_work_order(
            allowed_paths=["src/auth"],
            writable_paths=["src/author.py"],
        )
        with self.assertRaises(InvalidPathScopeError):
            wo_prefix_trap.validate()

    def test_writable_and_forbidden_overlap(self):
        """Invariant 2: Forbidden paths cannot simultaneously be writable (direct or subpath)."""
        # Exact match
        wo_exact = self._create_base_work_order(
            allowed_paths=["src/secrets"],
            writable_paths=["src/secrets/key.env"],
            forbidden_paths=["src/secrets/key.env"],
        )
        with self.assertRaises(InvalidPathScopeError) as ctx:
            wo_exact.validate()
        self.assertIn("cannot be both forbidden and writable", str(ctx.exception))

        # Subpath overlap: forbidden directory contains writable file
        wo_subpath = self._create_base_work_order(
            allowed_paths=["src/auth"],
            writable_paths=["src/auth/internal_secrets/token.key"],
            forbidden_paths=["src/auth/internal_secrets"],
        )
        with self.assertRaises(InvalidPathScopeError) as ctx:
            wo_subpath.validate()
        self.assertIn("cannot be both forbidden and writable", str(ctx.exception))

        # Reverse overlap: writable directory encompasses forbidden file
        wo_reverse = self._create_base_work_order(
            allowed_paths=["src/auth"],
            writable_paths=["src/auth"],
            forbidden_paths=["src/auth/private.pem"],
        )
        with self.assertRaises(InvalidPathScopeError) as ctx:
            wo_reverse.validate()
        self.assertIn("cannot be both forbidden and writable", str(ctx.exception))

    def test_read_only_and_writable_overlap(self):
        """Invariant 3: Read-only paths cannot simultaneously be writable."""
        # Exact match
        wo_exact = self._create_base_work_order(
            allowed_paths=["src/auth"],
            writable_paths=["src/auth/shared.py"],
            read_only_paths=["src/auth/shared.py"],
        )
        with self.assertRaises(InvalidPathScopeError) as ctx:
            wo_exact.validate()
        self.assertIn("cannot be both writable and read_only", str(ctx.exception))

        # Subpath overlap: read-only directory contains writable file
        wo_subpath = self._create_base_work_order(
            allowed_paths=["src/auth"],
            writable_paths=["src/auth/core/tokens.py"],
            read_only_paths=["src/auth/core"],
        )
        with self.assertRaises(InvalidPathScopeError) as ctx:
            wo_subpath.validate()
        self.assertIn("cannot be both writable and read_only", str(ctx.exception))

    # -------------------------------------------------------------------------
    # 2. Budget Invariants
    # -------------------------------------------------------------------------

    def test_negative_and_zero_budgets(self):
        """Invariant 5: Budgets cannot be negative or zero."""
        # Negative iteration budget
        wo_neg_iter = self._create_base_work_order(iteration_budget=-1)
        with self.assertRaises(InvalidBudgetError) as ctx:
            wo_neg_iter.validate()
        self.assertEqual(ctx.exception.budget_type, "iteration_budget")

        # Zero iteration budget
        wo_zero_iter = self._create_base_work_order(iteration_budget=0)
        with self.assertRaises(InvalidBudgetError):
            wo_zero_iter.validate()

        # Negative time budget
        wo_neg_time = self._create_base_work_order(time_budget=-60)
        with self.assertRaises(InvalidBudgetError) as ctx:
            wo_neg_time.validate()
        self.assertEqual(ctx.exception.budget_type, "time_budget")

        # Zero time budget
        wo_zero_time = self._create_base_work_order(time_budget=0)
        with self.assertRaises(InvalidBudgetError):
            wo_zero_time.validate()

    # -------------------------------------------------------------------------
    # 3. Command Scope & Contradictions
    # -------------------------------------------------------------------------

    def test_malformed_commands(self):
        """Invariant 6: Malformed commands or injection attempts are rejected."""
        # Empty command
        wo_empty_cmd = self._create_base_work_order(
            allowed_commands=[""],
        )
        with self.assertRaises(InvalidCommandScopeError):
            wo_empty_cmd.validate()

        # Command injection chaining
        for injection in [
            "pytest; rm -rf /",
            "pytest && curl https://malicious.com",
            "pytest || cat /etc/passwd",
            "pytest | nc -l 4444",
            "pytest\ncat secrets",
        ]:
            wo_inject = self._create_base_work_order(allowed_commands=[injection])
            with self.assertRaises(InvalidCommandScopeError):
                wo_inject.validate()

        # Contradictory authorizations for the same command
        cmd1 = AllowedCommand(command="pytest", allow_args=True)
        cmd2 = AllowedCommand(command="pytest", allow_args=False)
        wo_conflict_cmds = self._create_base_work_order(allowed_commands=[cmd1, cmd2])
        with self.assertRaises(ProgrammerValidationError) as ctx:
            wo_conflict_cmds.validate()
        self.assertIn("Contradictory command authorization", str(ctx.exception))

    # -------------------------------------------------------------------------
    # 4. Objective Validation
    # -------------------------------------------------------------------------

    def test_missing_and_oversized_objective(self):
        """Invariant 7: Missing or excessively long objectives are rejected."""
        # Empty
        wo_empty = self._create_base_work_order(objective="")
        with self.assertRaises(ProgrammerValidationError):
            wo_empty.validate()

        # Whitespace
        wo_ws = self._create_base_work_order(objective="   \t\n  ")
        with self.assertRaises(ProgrammerValidationError):
            wo_ws.validate()

        # Oversized
        wo_oversized = self._create_base_work_order(objective="x" * 15_000)
        with self.assertRaises(ProgrammerValidationError) as ctx:
            wo_oversized.validate()
        self.assertIn("exceeds maximum allowed length", str(ctx.exception))

    # -------------------------------------------------------------------------
    # 5. Lineage Invariants
    # -------------------------------------------------------------------------

    def test_invalid_lineage_and_cross_task_rejection(self):
        """Invariant 4: A work order cannot belong to another Manager task."""
        # Internal mismatch between manager_task_id and task_id
        with self.assertRaises(ProgrammerLineageError) as ctx:
            self._create_base_work_order(
                manager_task_id="task-alpha",
                task_id="task-beta",
            )
        self.assertIn("Task ID mismatch", str(ctx.exception))

        # External lineage check against manager task object
        wo = self._create_base_work_order(manager_task_id="task-alpha", task_id="task-alpha")
        foreign_task = Task(id="task-foreign", project_id=self.project_id, title="Foreign Task", objective="Foreign Task Objective")
        with self.assertRaises(ProgrammerLineageError) as ctx:
            wo.validate_lineage(foreign_task)
        self.assertIn("Lineage mismatch", str(ctx.exception))

        # External lineage check against wrong project
        wrong_project_task = Task(id="task-alpha", project_id="proj-different", title="Task Alpha", objective="Task Alpha Objective")
        with self.assertRaises(ProgrammerLineageError) as ctx:
            wo.validate_lineage(wrong_project_task)
        self.assertIn("Project mismatch", str(ctx.exception))

    # -------------------------------------------------------------------------
    # 6. Acceptance Criteria Validation
    # -------------------------------------------------------------------------

    def test_duplicate_and_invalid_acceptance_criteria(self):
        """Invariant 8: Invalid or duplicate acceptance criteria are rejected."""
        # Duplicate criterion IDs
        ac1 = AcceptanceCriterion(criterion_id="ac-001", description="Endpoint returns 200 OK")
        ac2 = AcceptanceCriterion(criterion_id="ac-001", description="Response contains token")
        wo_dup_ac = self._create_base_work_order(acceptance_criteria=[ac1, ac2])
        with self.assertRaises(ProgrammerValidationError) as ctx:
            wo_dup_ac.validate()
        self.assertIn("Duplicate acceptance criterion ID 'ac-001'", str(ctx.exception))

        # Empty criterion description
        ac_empty = AcceptanceCriterion(criterion_id="ac-002", description="   ")
        wo_empty_ac = self._create_base_work_order(acceptance_criteria=[ac_empty])
        with self.assertRaises(ProgrammerValidationError):
            wo_empty_ac.validate()

    # -------------------------------------------------------------------------
    # 7. Contradictory Policy Fields & Dependencies
    # -------------------------------------------------------------------------

    def test_contradictory_policy_fields_and_dependencies(self):
        """Verify contradiction detection across policy and dependency fields."""
        # Forbidden + read-only contradiction
        wo_forbidden_ro = self._create_base_work_order(
            read_only_paths=["config/secrets.json"],
            forbidden_paths=["config/secrets.json"],
            allowed_paths=["config"],
            writable_paths=[],
        )
        with self.assertRaises(InvalidPathScopeError) as ctx:
            wo_forbidden_ro.validate()
        self.assertIn("cannot be both forbidden and read_only", str(ctx.exception))

        # Empty dependency reference
        wo_empty_dep = self._create_base_work_order(dependencies=["task-1", "   ", "task-2"])
        with self.assertRaises(ProgrammerValidationError):
            wo_empty_dep.validate()

        # Malformed research evidence (empty claim)
        wo_bad_research = self._create_base_work_order(
            research_evidence=[ResearchEvidenceReference(evidence_id="ev-1", claim_or_fact="")]
        )
        with self.assertRaises(ProgrammerValidationError):
            wo_bad_research.validate()

    # -------------------------------------------------------------------------
    # 8. Malformed Authorization Cannot Reach Execution
    # -------------------------------------------------------------------------

    def test_malformed_authorization_cannot_reach_execution(self):
        """Invariant 6: Calling create_execution on invalid work order is blocked at instantiation."""
        # Invalid path scope
        wo_invalid_path = self._create_base_work_order(
            allowed_paths=["src/auth"],
            writable_paths=["/etc/shadow"],
        )
        with self.assertRaises(InvalidPathScopeError):
            wo_invalid_path.create_execution()

        # Negative budget
        wo_neg_budget = self._create_base_work_order(time_budget=-1)
        with self.assertRaises(InvalidBudgetError):
            wo_neg_budget.create_execution()

        # Missing objective
        wo_no_obj = self._create_base_work_order(objective="")
        with self.assertRaises(ProgrammerValidationError):
            wo_no_obj.create_execution()

    # -------------------------------------------------------------------------
    # 9. Valid Complex Work Order
    # -------------------------------------------------------------------------

    def test_valid_complex_work_order(self):
        """Verify that a richly specified, valid work order passes full validation and instantiates execution."""
        ac1 = AcceptanceCriterion(
            criterion_id="ac-test-01",
            description="All OAuth unit tests pass",
            criterion_type=AcceptanceCriterionType.TEST_PASS,
            target="tests/unit/test_oauth.py",
        )
        ac2 = AcceptanceCriterion(
            criterion_id="ac-typecheck-01",
            description="Typecheck passes with zero errors",
            criterion_type=AcceptanceCriterionType.TYPECHECK_PASS,
        )
        cmd1 = AllowedCommand(command="pytest", description="Run OAuth unit tests", allow_args=True)
        cmd2 = AllowedCommand(command="mypy", description="Run type checks", allow_args=False)
        research = ResearchEvidenceReference(
            evidence_id="ev-fastapi-docs",
            claim_or_fact="FastAPI recommends APIRouter for modular oauth endpoints",
            source_ref="https://fastapi.tiangolo.com/tutorial/bigger-applications/",
        )

        wo = self._create_base_work_order(
            objective="Implement production OAuth2 callback for Google and GitHub",
            instructions=[
                "Create src/auth/oauth.py with APIRouter",
                "Exchange authorization code for JWT token securely",
            ],
            context={"auth_provider": "jwt_rsa256"},
            allowed_paths=["src/auth", "tests/unit", "config/oauth.json"],
            writable_paths=["src/auth/oauth.py", "tests/unit/test_oauth.py"],
            read_only_paths=["config/oauth.json"],
            forbidden_paths=[".git", "secrets", "database/migrations"],
            allowed_commands=[cmd1, cmd2],
            acceptance_criteria=[ac1, ac2],
            required_checks=["pytest tests/unit/test_oauth.py", "mypy src/auth"],
            research_evidence=[research],
            dependencies=["task-jwt-keys"],
            iteration_budget=12,
            time_budget=720,
            risk_level=RiskLevel.HIGH,
        )

        # Full validation with required acceptance criteria
        wo.validate(require_acceptance_criteria=True)
        wo.validate_lineage(Task(id=self.task_id, project_id=self.project_id, title="OAuth Callback", objective="Implement OAuth2 callback"))

        # Spawns execution cleanly
        execution = wo.create_execution()
        self.assertIsInstance(execution, ProgrammerExecution)
        self.assertEqual(execution.work_order_id, wo.work_order_id)
        self.assertEqual(execution.task_id, wo.manager_task_id)
        self.assertEqual(execution.project_id, wo.project_id)
        self.assertEqual(execution.correlation_id, wo.correlation_id)


if __name__ == "__main__":
    unittest.main()
