from __future__ import annotations

from typing import Optional

from core.enums import ArtifactType
from core.models import Task, WorkerManifest, WorkerOutput, utc_now
from pkg.sdk.types import WorkerCapability
from pkg.sdk.worker import Worker, WorkerRuntimeContext


class FileInspectorWorker(Worker):
    """
    Deterministic reference worker implementation demonstrating the full Universal Worker SDK workflow.
    Inspects a file using Tool Runtime, analyzes it with OmniRoute Inference, creates an artifact,
    and requests verification without relying on runtime internals.
    """

    def __init__(
        self,
        worker_id: str = "worker.file.inspector",
        name: str = "File Inspector Worker",
        description: str = "Deterministic reference worker inspecting workspace files",
        target_file: str = "sample.py",
        sample_file_content: str = "def calculate_total(items):\n    return sum(items)\n",
    ):
        self.target_file = target_file
        self.sample_file_content = sample_file_content
        self._manifest = WorkerManifest(
            id=worker_id,
            name=name,
            role="Inspector",
            description=description,
            version="1.0.0",
            capabilities=[
                WorkerCapability.FILE_MANIPULATION.value,
                WorkerCapability.CODE_ANALYSIS.value,
                WorkerCapability.DOCUMENTATION.value,
                WorkerCapability.STRUCTURED_OUTPUT.value,
            ],
            permissions=["filesystem.read_file", "filesystem.write_file", "*"],
            created_at=utc_now(),
        )

    def get_manifest(self) -> WorkerManifest:
        return self._manifest

    def execute_task(self, context: WorkerRuntimeContext, task: Task) -> WorkerOutput:
        context.log.info(f"Starting inspection for task '{task.title}'")
        context.progress.report(10.0, "Requesting context package")

        # 1. Request context package via Context Engine
        ctx_pkg = context.context.get()
        context.log.info(f"Received context package with {len(ctx_pkg.items)} items")

        # 2. Write and Read target file via Tool Runtime
        context.progress.report(30.0, f"Ensuring {self.target_file} is present")
        context.tools.execute(
            tool_id="filesystem.write_file",
            arguments={"path": self.target_file, "content": self.sample_file_content},
        )

        read_res = context.tools.execute(
            tool_id="filesystem.read_file",
            arguments={"path": self.target_file},
        )

        # 3. Request LLM Inference via Inference Gateway (OmniRoute)
        context.progress.report(60.0, "Submitting analysis prompt to Inference Gateway")
        inf_resp = context.inference.generate(
            messages=[
                {"role": "system", "content": "You are a code inspection assistant."},
                {"role": "user", "content": f"Analyze file content:\n{read_res.output}"},
            ],
        )

        # 4. Create and register an Artifact
        context.progress.report(80.0, "Registering inspection report artifact")
        report_content = f"# Inspection Report for `{self.target_file}`\n\n## Findings\n{inf_resp.content}\n"
        artifact = context.artifacts.create(
            artifact_type=ArtifactType.REPORT,
            relative_path=f"reports/inspection_{task.id}.md",
            description=f"Automated inspection report for {self.target_file}",
            content=report_content,
            metadata={"inspected_file": self.target_file, "model_used": inf_resp.model_used},
        )

        # 5. Record Execution Evidence
        evidence = context.record_evidence(
            evidence_type="FILE_INSPECTION_AUDIT",
            data=f"Inspected file '{self.target_file}', generated artifact '{artifact.id}' with status SUCCESS.",
        )

        # 6. Request Authoritative Verification
        context.progress.report(90.0, "Requesting task verification")
        verif_res = context.verification.request()
        context.log.info(f"Verification outcome: {verif_res.status.value}")

        context.progress.report(100.0, "Inspection workflow completed successfully")

        return WorkerOutput(
            success=True,
            summary=f"File inspection completed for '{self.target_file}'.",
            report_markdown=report_content,
            created_artifacts=[artifact.to_dict()],
            evidence_list=[evidence],
        )
