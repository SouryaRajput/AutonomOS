from pathlib import Path
import unittest

from core.enums import RiskLevel
from core.models import Project, Task, WorkerManifest, utc_now
from core.safety.evaluator import SafetyEvaluator
from core.safety.model import SafetyConfig, SafetyDecision
from core.safety.types import SafetyAction
from core.tools.builtins.filesystem import FilesystemTool
from core.tools.builtins.shell import ShellTool
from core.tools.model import ToolRequest


class TestSafetyRiskEvaluator(unittest.TestCase):

    def setUp(self):
        self.project = Project(
            id="proj-safe-1",
            name="Safety Project",
            description="Safety evaluation test project",
            root_path="/tmp/mock_safe_proj",
            created_at=utc_now(),
        )
        self.worker = WorkerManifest(
            id="worker.safe",
            name="Safe Worker",
            role="Programmer",
            description="Test worker",
            permissions=["*"],
            created_at=utc_now(),
        )
        self.task = Task(
            id="task-safe-1",
            project_id="proj-safe-1",
            title="Safe Task",
            objective="Test objective",
            context_references=[{"path": "src/main.py"}, {"path": "src/utils.py"}],
            created_at=utc_now(),
        )
        self.fs_read = FilesystemTool("filesystem.read_file", action="read_file")
        self.fs_write = FilesystemTool("filesystem.write_file", action="write_file")
        self.fs_delete = FilesystemTool("filesystem.delete_file", action="delete_file")
        self.shell_exec = ShellTool("shell.execute")

    def test_low_risk_read_operation_allowed(self):
        req = ToolRequest(
            project_id=self.project.id,
            task_id=self.task.id,
            worker_id=self.worker.id,
            tool_id="filesystem.read_file",
            arguments={"path": "src/main.py"},
        )
        decision = SafetyEvaluator.evaluate_request(
            request=req,
            tool_def=self.fs_read.get_definition(),
            project=self.project,
            worker=self.worker,
            task=self.task,
        )
        self.assertEqual(decision.decision, SafetyAction.ALLOW)
        self.assertEqual(decision.risk_level, RiskLevel.LOW)
        self.assertFalse(decision.required_checkpoint)
        self.assertFalse(decision.required_approval)

    def test_medium_risk_write_operation_requires_checkpoint(self):
        req = ToolRequest(
            project_id=self.project.id,
            task_id=self.task.id,
            worker_id=self.worker.id,
            tool_id="filesystem.write_file",
            arguments={"path": "src/main.py", "content": "print('hello')"},
        )
        decision = SafetyEvaluator.evaluate_request(
            request=req,
            tool_def=self.fs_write.get_definition(),
            project=self.project,
            worker=self.worker,
            task=self.task,
        )
        self.assertEqual(decision.decision, SafetyAction.ALLOW_WITH_CHECKPOINT)
        self.assertEqual(decision.risk_level, RiskLevel.MEDIUM)
        self.assertTrue(decision.required_checkpoint)
        self.assertFalse(decision.required_approval)

    def test_high_risk_delete_operation_requires_checkpoint(self):
        req = ToolRequest(
            project_id=self.project.id,
            task_id=self.task.id,
            worker_id=self.worker.id,
            tool_id="filesystem.delete_file",
            arguments={"path": "src/utils.py"},
        )
        decision = SafetyEvaluator.evaluate_request(
            request=req,
            tool_def=self.fs_delete.get_definition(),
            project=self.project,
            worker=self.worker,
            task=self.task,
        )
        self.assertEqual(decision.decision, SafetyAction.ALLOW_WITH_CHECKPOINT)
        self.assertEqual(decision.risk_level, RiskLevel.HIGH)
        self.assertTrue(decision.required_checkpoint)

    def test_critical_protected_path_modification_denied(self):
        req = ToolRequest(
            project_id=self.project.id,
            task_id=self.task.id,
            worker_id=self.worker.id,
            tool_id="filesystem.write_file",
            arguments={"path": ".autonomos/memory/architecture.md", "content": "# Hacked"},
        )
        decision = SafetyEvaluator.evaluate_request(
            request=req,
            tool_def=self.fs_write.get_definition(),
            project=self.project,
            worker=self.worker,
            task=self.task,
        )
        self.assertEqual(decision.decision, SafetyAction.DENY)
        self.assertEqual(decision.risk_level, RiskLevel.CRITICAL)
        self.assertTrue(decision.required_approval)
        self.assertTrue(any("protected resource" in r for r in decision.reasons))

    def test_dangerous_shell_command_pattern_triggers_critical_approval(self):
        req = ToolRequest(
            project_id=self.project.id,
            task_id=self.task.id,
            worker_id=self.worker.id,
            tool_id="shell.execute",
            arguments={"command": "rm -rf /"},
        )
        decision = SafetyEvaluator.evaluate_request(
            request=req,
            tool_def=self.shell_exec.get_definition(),
            project=self.project,
            worker=self.worker,
            task=self.task,
        )
        self.assertEqual(decision.decision, SafetyAction.REQUIRES_APPROVAL)
        self.assertEqual(decision.risk_level, RiskLevel.CRITICAL)
        self.assertTrue(decision.required_approval)
        self.assertTrue(any("dangerous execution pattern" in r for r in decision.reasons))


if __name__ == "__main__":
    unittest.main()
