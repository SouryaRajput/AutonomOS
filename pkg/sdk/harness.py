from __future__ import annotations

from typing import Any, Callable, Optional
import uuid

from core.context.model import ContextBudget, ContextPackage
from core.enums import ArtifactType, RiskLevel, ToolStatus
from core.inference.model import Cost, InferenceMessage, InferenceRequest, InferenceResponse, ModelRequirement, Usage
from core.inference.types import CostType
from core.memory.model import MemoryDocument
from core.models import Artifact, Evidence, Task, WorkerOutput, utc_now
from core.tools.model import ToolDefinition, ToolResult
from core.verification.model import SuccessCriterion, VerificationPlan, VerificationResult
from core.verification.types import CheckStatus, VerificationStatus
from pkg.sdk.subclients import (
    ArtifactClient,
    CancellationClient,
    ContextClient,
    EventClient,
    InferenceClient,
    LoggerClient,
    MemoryClient,
    ProgressClient,
    SafetyClient,
    TaskContext,
    ToolClient,
    VerificationClient,
)
from pkg.sdk.types import WorkerConfig
from pkg.sdk.worker import Worker, WorkerRuntimeContext


class _FakeToolClient(ToolClient):
    def __init__(self, harness: "WorkerTestHarness"):
        self._harness = harness

    def execute(self, tool_id: str, arguments: dict[str, Any], timeout_seconds: Optional[int] = None) -> ToolResult:
        self._harness.tool_executions.append({"tool_id": tool_id, "arguments": arguments, "timeout": timeout_seconds})
        if tool_id in self._harness._tool_mocks:
            handler_or_res = self._harness._tool_mocks[tool_id]
            if callable(handler_or_res):
                res = handler_or_res(arguments)
            else:
                res = handler_or_res
            if isinstance(res, ToolResult):
                return res
            return ToolResult(
                result_id=f"res-{uuid.uuid4().hex[:6]}",
                request_id=f"req-{uuid.uuid4().hex[:6]}",
                tool_id=tool_id,
                status=ToolStatus.SUCCESS,
                output=res,
            )
        return ToolResult(
            result_id=f"res-{uuid.uuid4().hex[:6]}",
            request_id=f"req-{uuid.uuid4().hex[:6]}",
            tool_id=tool_id,
            status=ToolStatus.SUCCESS,
            output=f"Mock output for {tool_id}",
        )

    def list(self) -> list[ToolDefinition]:
        return [
            ToolDefinition(id=tid, name=tid, description="Mock Tool", category="custom", parameters={})
            for tid in self._harness._tool_mocks.keys()
        ]


class _FakeContextClient(ContextClient):
    def __init__(self, harness: "WorkerTestHarness"):
        self._harness = harness

    def get(self, budget: Optional[ContextBudget] = None, focus_areas: Optional[list[str]] = None) -> ContextPackage:
        self._harness.context_requests.append({"budget": budget, "focus_areas": focus_areas})
        return self._harness._mock_context or ContextPackage(
            request_id=f"ctx-mock-{uuid.uuid4().hex[:6]}",
            project_id=self._harness.project_id,
            task_id=self._harness.task.id,
            worker_id=self._harness.worker_id,
            items=[],
            total_estimated_tokens=0,
            total_characters=0,
            budget=budget or ContextBudget(),
        )


class _FakeInferenceClient(InferenceClient):
    def __init__(self, harness: "WorkerTestHarness"):
        self._harness = harness

    def generate(
        self,
        messages: list[InferenceMessage | dict[str, Any]],
        requirements: Optional[ModelRequirement] = None,
        temperature: float = 0.7,
        max_output_tokens: Optional[int] = None,
        tools: Optional[list[dict[str, Any]]] = None,
        metadata: Optional[dict[str, Any]] = None,
    ) -> InferenceResponse:
        self._harness.inference_requests.append({"messages": messages, "requirements": requirements})
        if self._harness._mock_inference_response:
            resp_val = self._harness._mock_inference_response
            if callable(resp_val):
                req_obj = InferenceRequest(
                    request_id=f"req-mock-{uuid.uuid4().hex[:6]}",
                    project_id=self._harness.project_id,
                    task_id=self._harness.task.id,
                    worker_id=self._harness.worker_id,
                    messages=messages,
                    requirements=requirements or ModelRequirement(),
                )
                resp_val = resp_val(req_obj)

            if isinstance(resp_val, str):
                return InferenceResponse(
                    request_id=f"req-mock-{uuid.uuid4().hex[:6]}",
                    response_id=f"resp-mock-{uuid.uuid4().hex[:6]}",
                    content=resp_val,
                    model_used="mock-harness-model",
                    provider_used="mock-harness",
                    usage=Usage(input_tokens=10, output_tokens=20, total_tokens=30),
                    cost=Cost(total_cost=0.0, cost_type=CostType.ESTIMATED),
                )
            return resp_val
        return InferenceResponse(
            request_id=f"req-mock-{uuid.uuid4().hex[:6]}",
            response_id=f"resp-mock-{uuid.uuid4().hex[:6]}",
            content="Deterministic harness inference response",
            model_used="mock-harness-model",
            provider_used="mock-harness",
            usage=Usage(input_tokens=10, output_tokens=20, total_tokens=30),
            cost=Cost(total_cost=0.0, cost_type=CostType.ESTIMATED),
        )


class _FakeMemoryClient(MemoryClient):
    def __init__(self, harness: "WorkerTestHarness"):
        self._harness = harness

    def read(self, relative_path_or_id: str) -> Optional[MemoryDocument]:
        if relative_path_or_id in self._harness._mock_memory:
            return self._harness._mock_memory[relative_path_or_id]
        clean = relative_path_or_id.lstrip("/").replace(".autonomos/memory/", "")
        for k, v in self._harness._mock_memory.items():
            if k == clean or k.endswith(clean) or v.relative_path.endswith(clean):
                return v
        return None

    def record_decision(self, title: str, context: str, decision: str, reasoning: str, consequences: str, **kwargs) -> MemoryDocument:
        doc = MemoryDocument(
            id=f"doc-{uuid.uuid4().hex[:6]}",
            project_id=self._harness.project_id,
            memory_type="decisions",
            title=title,
            relative_path=f".autonomos/memory/decisions/{title}.md",
            content=f"# {title}\n{decision}",
            created_at=utc_now(),
        )
        self._harness._mock_memory[doc.id] = doc
        self._harness._mock_memory[doc.relative_path] = doc
        return doc

    def record_issue(self, title: str, description: str, **kwargs) -> MemoryDocument:
        doc = MemoryDocument(
            id=f"issue-{uuid.uuid4().hex[:6]}",
            project_id=self._harness.project_id,
            memory_type="tech_debt",
            title=title,
            relative_path=f".autonomos/memory/tech_debt/{title}.md",
            content=f"# {title}\n{description}",
            created_at=utc_now(),
        )
        self._harness._mock_memory[doc.id] = doc
        return doc

    def update_state(self, stage: str, completed_milestones: list[str], active_work: list[str], known_limitations: list[str], notes: str = "") -> MemoryDocument:
        doc = MemoryDocument(
            id="current-state-doc",
            project_id=self._harness.project_id,
            memory_type="state",
            title="Current State",
            relative_path=".autonomos/memory/current_state.md",
            content=f"# Current State\nStage: {stage}",
            created_at=utc_now(),
        )
        self._harness._mock_memory[doc.relative_path] = doc
        return doc


class _FakeArtifactClient(ArtifactClient):
    def __init__(self, harness: "WorkerTestHarness"):
        self._harness = harness

    def create(self, artifact_type: ArtifactType, relative_path: str, description: str, content: Optional[bytes | str] = None, metadata: Optional[dict[str, Any]] = None) -> Artifact:
        art = Artifact(
            id=f"art-{uuid.uuid4().hex[:6]}",
            project_id=self._harness.project_id,
            task_id=self._harness.task.id,
            worker_id=self._harness.worker_id,
            type=artifact_type,
            path=relative_path,
            description=description,
            checksum="mock-checksum",
            metadata=metadata or {},
            created_at=utc_now(),
        )
        self._harness.created_artifacts.append(art)
        return art

    def get(self, artifact_id: str) -> Optional[Artifact]:
        for a in self._harness.created_artifacts:
            if a.id == artifact_id:
                return a
        return None

    def list(self) -> list[Artifact]:
        return list(self._harness.created_artifacts)


class _FakeVerificationClient(VerificationClient):
    def __init__(self, harness: "WorkerTestHarness"):
        self._harness = harness

    def request(self, plan: Optional[VerificationPlan] = None, criteria: Optional[list[SuccessCriterion]] = None) -> VerificationResult:
        if self._harness._mock_verification_result:
            return self._harness._mock_verification_result
        return VerificationResult(
            verification_id=f"verif-{uuid.uuid4().hex[:6]}",
            status=VerificationStatus.PASSED,
            passed_checks=[],
            failed_checks=[],
            uncertain_checks=[],
            summary="Mock verification passed in test harness.",
        )


class _FakeSafetyClient(SafetyClient):
    def __init__(self, harness: "WorkerTestHarness"):
        self._harness = harness

    def request_checkpoint(self, label: str = "") -> str:
        chk_id = f"chk-harness-{uuid.uuid4().hex[:6]}"
        self._harness.checkpoints_requested.append({"id": chk_id, "label": label})
        return chk_id


class _FakeEventClient(EventClient):
    def __init__(self, harness: "WorkerTestHarness"):
        self._harness = harness

    def emit(self, event_type: str, payload: dict[str, Any]) -> None:
        self._harness.emitted_events.append({"event_type": event_type, "payload": payload, "timestamp": utc_now()})


class _HarnessWorkerRuntimeContext(WorkerRuntimeContext):
    """Context implementation injected into workers running inside WorkerTestHarness."""

    def __init__(self, harness: "WorkerTestHarness"):
        self._harness = harness
        self._tool_client = _FakeToolClient(harness)
        self._context_client = _FakeContextClient(harness)
        self._inference_client = _FakeInferenceClient(harness)
        self._memory_client = _FakeMemoryClient(harness)
        self._artifact_client = _FakeArtifactClient(harness)
        self._verification_client = _FakeVerificationClient(harness)
        self._safety_client = _FakeSafetyClient(harness)
        self._event_client = _FakeEventClient(harness)
        self._logger_client = LoggerClient(self._event_client, harness.worker_id)
        self._progress_client = ProgressClient(self._event_client)
        self._cancellation_client = CancellationClient(harness.task.id, lambda: harness.is_cancelled)

    @property
    def task(self) -> Task:
        return self._harness.task

    @property
    def project_id(self) -> str:
        return self._harness.project_id

    @property
    def context(self) -> ContextClient:
        return self._context_client

    @property
    def tools(self) -> ToolClient:
        return self._tool_client

    @property
    def inference(self) -> InferenceClient:
        return self._inference_client

    @property
    def memory(self) -> MemoryClient:
        return self._memory_client

    @property
    def artifacts(self) -> ArtifactClient:
        return self._artifact_client

    @property
    def verification(self) -> VerificationClient:
        return self._verification_client

    @property
    def safety(self) -> SafetyClient:
        return self._safety_client

    @property
    def events(self) -> EventClient:
        return self._event_client

    @property
    def log(self) -> LoggerClient:
        return self._logger_client

    @property
    def progress(self) -> ProgressClient:
        return self._progress_client

    @property
    def cancellation(self) -> CancellationClient:
        return self._cancellation_client

    def record_evidence(self, evidence_type: str, data: str) -> Evidence:
        ev = Evidence(
            id=f"ev-{uuid.uuid4().hex[:6]}",
            task_id=self._harness.task.id,
            evidence_type=evidence_type,
            data=data,
            created_at=utc_now(),
        )
        self._harness.recorded_evidence.append(ev)
        return ev

    def request_verification(
        self,
        target_type: str = "TASK",
        target_id: Optional[str] = None,
        evidence_ids: Optional[list[str]] = None,
        notes: str = "",
        **kwargs,
    ) -> Optional[Any]:
        if hasattr(self.verification, "verify"):
            return self.verification.verify()
        return None


class WorkerTestHarness:
    """
    Standalone testing harness allowing developers to unit-test Worker implementations
    offline with fake tools, mock inference, fake context, and event recording.
    """

    def __init__(self, worker_id: str = "test-worker", project_id: str = "proj-test"):
        self.worker_id = worker_id
        self.project_id = project_id
        self.is_cancelled = False

        self.task = Task(
            id="task-harness-1",
            project_id=project_id,
            title="Harness Test Task",
            objective="Testing worker in isolation",
            created_at=utc_now(),
        )

        self._tool_mocks: dict[str, Any] = {}
        self._mock_context: Optional[ContextPackage] = None
        self._mock_inference_response: Optional[InferenceResponse | str] = None
        self._mock_memory: dict[str, MemoryDocument] = {}
        self._mock_verification_result: Optional[VerificationResult] = None

        # Recorded interactions
        self.tool_executions: list[dict[str, Any]] = []
        self.context_requests: list[dict[str, Any]] = []
        self.inference_requests: list[dict[str, Any]] = []
        self.created_artifacts: list[Artifact] = []
        self.recorded_evidence: list[Evidence] = []
        self.checkpoints_requested: list[dict[str, Any]] = []
        self.emitted_events: list[dict[str, Any]] = []

    def mock_tool(self, tool_id: str, result_or_handler: Any) -> None:
        """Register a mock handler or return value for a specific tool ID."""
        self._tool_mocks[tool_id] = result_or_handler

    def set_context_response(self, package: ContextPackage) -> None:
        """Provide a canned ContextPackage for context requests."""
        self._mock_context = package

    def set_inference_response(self, response: InferenceResponse | str | Any) -> None:
        """Provide a canned inference response or callback."""
        self._mock_inference_response = response

    def set_memory_document(self, doc: MemoryDocument) -> None:
        """Register a memory document available to memory.read()."""
        self._mock_memory[doc.id] = doc
        self._mock_memory[doc.relative_path] = doc

    def set_verification_result(self, result: VerificationResult) -> None:
        """Set canned verification result."""
        self._mock_verification_result = result

    mock_context = set_context_response
    mock_inference = set_inference_response
    mock_memory_document = set_memory_document
    mock_verification = set_verification_result

    @property
    def context(self) -> WorkerRuntimeContext:
        """Return a live WorkerRuntimeContext connected to this harness."""
        return _HarnessWorkerRuntimeContext(self)

    def cancel_task(self) -> None:
        """Mark task as cancelled."""
        self.is_cancelled = True

    def run(self, worker: Worker, task: Optional[Task] = None) -> WorkerOutput:
        """Execute the worker through its lifecycle within this test harness."""
        if task:
            self.task = task
        ctx = _HarnessWorkerRuntimeContext(self)

        worker.initialize()
        worker.validate_task(self.task)
        worker.before_task(ctx, self.task)
        output = worker.execute_task(ctx, self.task)
        worker.after_task(ctx, self.task, output)
        worker.shutdown()

        return output
