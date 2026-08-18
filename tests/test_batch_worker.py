import json
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from PyQt6.QtWidgets import QApplication

from app.batch.pipeline import BatchSettings, JobOutcome
from app.batch.worklist import Job
from app.batch.worker import BatchRunnerWorker

_app = None


def _get_app():
    global _app
    if _app is None:
        _app = QApplication.instance() or QApplication(sys.argv)
    return _app


class TestBatchWorker(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        _get_app()

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)

    def _create_recording(self, name, queued=True):
        d = self.root / name
        d.mkdir()
        meta = {
            "directory": str(d),
            "name": name,
            "audio_files": {"combined": str(d / "combined_audio.wav")},
            "batch_pending": queued,
        }
        (d / "metadata.json").write_text(json.dumps(meta), encoding="utf-8")
        (d / "combined_audio.wav").write_text("", encoding="utf-8")
        return d

    def test_worker_processes_jobs_and_emits_signals(self):
        self._create_recording("rec1")
        self._create_recording("rec2")

        worker = BatchRunnerWorker(self.root, settings=BatchSettings())

        started_events = []
        finished_events = []
        progress_events = []
        batch_finished_events = []

        worker.job_started.connect(lambda l, i, t: started_events.append((l, i, t)))
        worker.job_progress.connect(lambda m: progress_events.append(m))
        worker.job_finished.connect(lambda j, o: finished_events.append((j, o)))
        worker.batch_finished.connect(lambda p, f, d: batch_finished_events.append((p, f, d)))

        with patch("app.batch.worker.run_job") as mock_run_job:
            def _fake_run(job, settings, on_progress=None):
                if on_progress:
                    on_progress("Transcribing...")
                return JobOutcome(True, "transcribed", segments=5)

            mock_run_job.side_effect = _fake_run
            worker.run()

        self.assertEqual(len(started_events), 2)
        self.assertEqual(len(finished_events), 2)
        self.assertIn("Transcribing...", progress_events)
        self.assertEqual(batch_finished_events, [(2, 0, 0)])

        # Check that batch_pending is cleared in metadata
        meta1 = json.loads((self.root / "rec1" / "metadata.json").read_text(encoding="utf-8"))
        self.assertNotIn("batch_pending", meta1)

    def test_worker_handles_failure_and_increments_attempts(self):
        self._create_recording("rec1")

        worker = BatchRunnerWorker(self.root, settings=BatchSettings())

        batch_finished_events = []
        worker.batch_finished.connect(lambda p, f, d: batch_finished_events.append((p, f, d)))

        with patch("app.batch.worker.run_job", return_value=JobOutcome(False, "error")):
            worker.run()

        self.assertEqual(batch_finished_events, [(0, 1, 0)])
        meta1 = json.loads((self.root / "rec1" / "metadata.json").read_text(encoding="utf-8"))
        self.assertTrue(meta1["batch_pending"])
        self.assertEqual(meta1["batch_attempts"], 1)

    def test_worker_cooperative_cancel(self):
        self._create_recording("rec1")
        self._create_recording("rec2")

        worker = BatchRunnerWorker(self.root, settings=BatchSettings())
        cancelled_events = []
        worker.cancelled.connect(lambda: cancelled_events.append(True))

        with patch("app.batch.worker.run_job") as mock_run_job:
            def _fake_run(job, settings, on_progress=None):
                worker.cancel()
                return JobOutcome(True, "transcribed")

            mock_run_job.side_effect = _fake_run
            worker.run()

        self.assertEqual(mock_run_job.call_count, 1)
        self.assertEqual(cancelled_events, [True])


if __name__ == "__main__":
    unittest.main()
