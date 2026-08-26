"""Task management, state machine, and dependency engine."""
from core.task.dependencies import DependencyResolver
from core.task.state_machine import TaskStateMachine

__all__ = ["TaskStateMachine", "DependencyResolver"]
