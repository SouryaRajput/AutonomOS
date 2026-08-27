import unittest

from core.inference.types import ModelCapability
from core.models import WorkerManifest
from pkg.sdk.types import WorkerCapability, WorkerConfig, WorkerRequirement


class TestWorkerSDKManifestAndTypes(unittest.TestCase):

    def test_worker_capability_taxonomy(self):
        self.assertEqual(WorkerCapability.RESEARCH.value, "RESEARCH")
        self.assertEqual(WorkerCapability.CODE_GENERATION.value, "CODE_GENERATION")
        self.assertEqual(WorkerCapability.TESTING.value, "TESTING")
        self.assertEqual(WorkerCapability.FILE_MANIPULATION.value, "FILE_MANIPULATION")

    def test_worker_requirement_serialization(self):
        req = WorkerRequirement(
            required_tools=["filesystem.read_file", "git.status"],
            preferred_inference_capabilities={ModelCapability.CODE_GENERATION, ModelCapability.LONG_CONTEXT},
            required_context_categories=["architecture", "decisions"],
            minimum_context_window=32000,
            metadata={"priority": "high"},
        )
        d = req.to_dict()
        self.assertIn("filesystem.read_file", d["required_tools"])
        self.assertIn("CODE_GENERATION", d["preferred_inference_capabilities"])
        self.assertEqual(d["minimum_context_window"], 32000)

        restored = WorkerRequirement.from_dict(d)
        self.assertEqual(restored.required_tools, req.required_tools)
        self.assertIn(ModelCapability.CODE_GENERATION, restored.preferred_inference_capabilities)
        self.assertEqual(restored.minimum_context_window, 32000)

    def test_worker_config_serialization(self):
        cfg = WorkerConfig(
            custom_settings={"max_lines": 500, "lint_enabled": True},
            preferred_style="pep8",
            timeout_seconds=120,
        )
        d = cfg.to_dict()
        self.assertEqual(d["preferred_style"], "pep8")
        self.assertEqual(d["timeout_seconds"], 120)

        restored = WorkerConfig.from_dict(d)
        self.assertEqual(restored.custom_settings["max_lines"], 500)
        self.assertTrue(restored.custom_settings["lint_enabled"])

    def test_capability_vs_permission_separation(self):
        # A worker declares capabilities (what it knows how to do), but permissions are granted by runtime
        manifest = WorkerManifest(
            id="worker.coder.1",
            name="Coder Worker",
            role="Programmer",
            description="Sample coder worker",
            capabilities=[WorkerCapability.CODE_GENERATION.value, WorkerCapability.FILE_MANIPULATION.value],
            permissions=["filesystem.read_file", "filesystem.write_file"],  # Scoped permissions
        )
        self.assertIn("CODE_GENERATION", manifest.capabilities)
        self.assertNotIn("shell.execute", manifest.permissions)  # shell not allowed even if capable of code


if __name__ == "__main__":
    unittest.main()
