from __future__ import annotations

import logging
from typing import Any, Callable, Optional, Sequence, Union

from core.enums import WorkerStatus
from core.events.model import Event
from core.models import Task, WorkerManifest, WorkerOutput, utc_now
from core.tester.contracts.execution import TesterExecution
from core.tester.contracts.functional_pipeline import FunctionalEvaluationPipeline
from core.tester.contracts.manager_bridge import TesterManagerBridge
from core.tester.contracts.result import TesterResult
from core.tester.contracts.work_order import TesterWorkOrder
from core.tester.types import TestingCapability, WorkerType
from pkg.sdk.worker import Worker, WorkerRuntimeContext

logger = logging.getLogger("AutonomOS.Tester.Worker")


class TesterWorker(Worker):
    """
    Authoritative Tester V1 Worker implementation.
    
    Acts as the specialist evaluation agent in AutonomOS:
    - Bounded strictly to authorized test scopes and capabilities
    - Executes deterministic evaluation via FunctionalEvaluationPipeline
    - Evaluates preflight health, executes frozen test cases, captures runtime events
    - Evaluates functional acceptance criteria without visual/UX speculation
    - Reports structured, evidence-backed TesterResult packages to Manager
    """
    __test__ = False
    worker_type: str = WorkerType.TESTER.value

    def __init__(
        self,
        worker_id: str = "worker.tester",
        bridge: Optional[TesterManagerBridge] = None,
        pipeline: Optional[FunctionalEvaluationPipeline] = None,
        event_sink: Optional[Callable[[Event], Any]] = None,
    ) -> None:
        self.worker_id = worker_id
        self.bridge = bridge or TesterManagerBridge(event_sink=event_sink)
        self.pipeline = pipeline or FunctionalEvaluationPipeline(bridge=self.bridge, event_sink=event_sink, worker_id=self.worker_id)
        self.event_sink = event_sink

    def get_manifest(self) -> WorkerManifest:
        """Return static manifest representing Tester V1 capability boundary."""
        return WorkerManifest(
            id=self.worker_id,
            name="Specialist Tester Worker",
            role="Tester",
            description="Evaluates application functionality and reports evidence-backed findings to Manager.",
            version="1.0.0",
            capabilities=[
                TestingCapability.TEST_EXECUTION.value,
                TestingCapability.BEHAVIOR_OBSERVATION.value,
                TestingCapability.EVIDENCE_COLLECTION.value,
                TestingCapability.ACCEPTANCE_EVALUATION.value,
                TestingCapability.DEFECT_IDENTIFICATION.value,
                TestingCapability.SCREENSHOT.value,
                TestingCapability.SCREEN_RECORDING.value,
                TestingCapability.OCR.value,
            ],
            tools=["test.preflight", "test.run", "test.evaluate", "test.report"],
            permissions=["read:artifacts", "create:evidence"],
            status=WorkerStatus.IDLE,
            created_at=utc_now(),
        )

    def execute_task(
        self,
        context: Any = None,
        task: Optional[Task] = None,
        **pipeline_kwargs: Any,
    ) -> WorkerOutput:
        """
        Standard Worker execution entrypoint.
        Translates Manager Task into an authoritative TesterWorkOrder and executes it.
        Ensures the Tester actually runs rather than merely registering.
        """
        t = task or getattr(context, "task", None)
        if t is None:
            raise ValueError("Cannot execute TesterWorker task: no Task provided.")

        work_order = self.bridge.issue_work_order(t)
        _, _, output = self.execute_work_order(work_order, **pipeline_kwargs)
        return output

    def execute_work_order(
        self,
        work_order: TesterWorkOrder,
        **pipeline_kwargs: Any,
    ) -> tuple[TesterExecution, TesterResult, WorkerOutput]:
        """
        Execute an authorized TesterWorkOrder through the functional evaluation pipeline.
        """
        return self.pipeline.execute(work_order=work_order, **pipeline_kwargs)


# Canonical alias
RealTesterWorker = TesterWorker
