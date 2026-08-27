import unittest

from pkg.sdk.harness import WorkerTestHarness
from workers.file_inspector_worker import FileInspectorWorker


class TestFileInspectorWorker(unittest.TestCase):

    def test_file_inspector_worker_execution_in_harness(self):
        harness = WorkerTestHarness()
        worker = FileInspectorWorker(
            worker_id="worker.file.inspector",
            target_file="app/main.py",
            sample_file_content="print('Testing File Inspector')\n",
        )

        output = harness.run(worker)

        self.assertTrue(output.success)
        self.assertIn("File inspection completed", output.summary)
        self.assertEqual(len(harness.created_artifacts), 1)
        self.assertEqual(len(harness.recorded_evidence), 1)
        self.assertEqual(harness.recorded_evidence[0].evidence_type, "FILE_INSPECTION_AUDIT")

        # Check progress reports
        progress_events = [e for e in harness.emitted_events if e["event_type"] == "PROGRESS_REPORTED"]
        self.assertGreaterEqual(len(progress_events), 4)


if __name__ == "__main__":
    unittest.main()
