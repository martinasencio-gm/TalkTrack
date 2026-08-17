"""Stopping a recording must not block the UI thread on file work.

The mixing/writing half of stop() costs seconds on a long recording (~6s
for 20 minutes), and it used to run inline on the UI thread, freezing the
window. It now runs in a FinalizeWorker. Stream and COM teardown stay on
the caller's thread on purpose — comtypes proxies have apartment affinity
and finalizing them elsewhere can take the process down natively.
"""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np


class TestCaptureStopSplit(unittest.TestCase):
    """stop() splits into stop_streams() (fast) + finalize() (slow)."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def _capture_with_writers(self, mic_data=None, sys_data=None):
        from app.recording.audio_capture import DualAudioCapture
        from app.recording.chunk_writer import ChunkWriter
        cap = DualAudioCapture(mic_device=None, loopback_device=None,
                               sample_rate=16000)
        cap.output_dir = self.dir
        cap.mic_stream = MagicMock()
        cap.system_stream = MagicMock()
        cap._writers = {}
        if mic_data is not None:
            w = ChunkWriter(self.dir / "mic_audio.wav", sample_rate=16000)
            w.release(prepad_frames=0)
            w.put(mic_data)
            cap._writers["mic"] = w
        if sys_data is not None:
            w = ChunkWriter(self.dir / "system_audio.wav", sample_rate=16000)
            w.release(prepad_frames=0)
            w.put(sys_data)
            cap._writers["system"] = w
        return cap

    def test_stop_streams_stops_devices_but_leaves_the_file_work(self):
        cap = self._capture_with_writers(
            mic_data=np.full(16000, 0.5, dtype=np.float32),
            sys_data=np.full(16000, 0.25, dtype=np.float32),
        )
        cap.stop_streams()

        cap.mic_stream.stop.assert_called_once()
        cap.system_stream.stop.assert_called_once()
        # Writers are still open and nothing has been mixed yet — that is
        # precisely the work the worker thread is going to do.
        self.assertEqual(set(cap._writers), {"mic", "system"})
        self.assertFalse((self.dir / "combined_audio.wav").exists())
        cap.finalize()  # release handles for tmpdir cleanup

    def test_finalize_does_the_mixing(self):
        import soundfile as sf
        cap = self._capture_with_writers(
            mic_data=np.full(16000, 0.5, dtype=np.float32),
            sys_data=np.full(16000, 0.25, dtype=np.float32),
        )
        cap.stop_streams()
        results = cap.finalize()

        self.assertIsNotNone(results["mic"])
        self.assertIsNotNone(results["system"])
        self.assertIsNotNone(results["combined"])
        combined, _ = sf.read(results["combined"], dtype="float32")
        self.assertAlmostEqual(float(combined.max()), 0.95, places=3)

    def test_stop_still_runs_both_halves(self):
        # stop() stays the whole operation for every existing caller.
        cap = self._capture_with_writers(
            mic_data=np.full(8000, 0.5, dtype=np.float32))
        results = cap.stop()
        cap.mic_stream.stop.assert_called_once()
        self.assertIsNotNone(results["combined"])

    def test_duration_is_known_before_finalize_runs(self):
        # The min-length check happens between the two halves, so elapsed
        # time has to be accumulated by stop_streams, not by finalize.
        cap = self._capture_with_writers(
            mic_data=np.full(8000, 0.5, dtype=np.float32))
        cap._recording = True
        cap._start_time = 100.0
        cap._elapsed = 0.0
        with patch("time.time", return_value=112.5):
            cap.stop_streams()
        self.assertAlmostEqual(cap.get_elapsed_time(), 12.5, places=3)
        cap.finalize()

    def test_discard_drops_the_tracks_without_mixing(self):
        # A too-short recording is deleted, so paying for the mix first
        # would be pure waste — and the writers must release their file
        # handles or the rmtree that follows fails on Windows.
        cap = self._capture_with_writers(
            mic_data=np.full(8000, 0.5, dtype=np.float32),
            sys_data=np.full(8000, 0.25, dtype=np.float32),
        )
        cap.stop_streams()
        cap.discard()

        self.assertEqual(cap._writers, {})
        self.assertFalse((self.dir / "mic_audio.wav").exists())
        self.assertFalse((self.dir / "system_audio.wav").exists())
        self.assertFalse((self.dir / "combined_audio.wav").exists())


class _FakeConfig:
    def __init__(self, **overrides):
        self._data = {
            ("general", "min_recording_length"): 5,
            ("output", "format"): "wav",
            ("audio", "sample_rate"): 16000,
        }
        self._data.update(overrides)

    def get(self, section, key):
        return self._data.get((section, key))


class TestFinalizeWorker(unittest.TestCase):
    def test_run_finalizes_and_reports_the_files(self):
        from app.recording.recorder import FinalizeWorker
        capture = MagicMock()
        capture.finalize.return_value = {"mic": "m.wav", "combined": "c.wav"}
        worker = FinalizeWorker(capture, convert_mp3=False)
        emitted = []
        worker.finalized.connect(emitted.append)

        worker.run()

        capture.finalize.assert_called_once()
        self.assertEqual(emitted, [{"mic": "m.wav", "combined": "c.wav"}])

    def test_run_converts_to_mp3_when_asked(self):
        from app.recording.recorder import FinalizeWorker
        capture = MagicMock()
        capture.finalize.return_value = {"mic": "m.wav"}
        worker = FinalizeWorker(capture, convert_mp3=True)

        with patch("app.recording.recorder.convert_to_mp3") as convert:
            worker.run()

        convert.assert_called_once_with({"mic": "m.wav"})

    def test_failure_is_reported_not_raised(self):
        from app.recording.recorder import FinalizeWorker
        capture = MagicMock()
        capture.finalize.side_effect = OSError("disk full")
        worker = FinalizeWorker(capture, convert_mp3=False)
        failures = []
        worker.failed.connect(failures.append)

        worker.run()   # must not raise — a raise here kills the thread

        self.assertEqual(len(failures), 1)
        self.assertIn("disk full", failures[0])


class TestRecorderOffThreadStop(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def _recorder(self, config=None):
        from app.recording.recorder import Recorder, RecordingState
        rec = Recorder(config or _FakeConfig())
        rec._state = RecordingState.RECORDING
        rec._current_session = {"id": "x", "directory": str(self.dir)}
        rec._capture = MagicMock()
        rec._capture.get_elapsed_time.return_value = 60.0
        rec._capture._capture_status = None
        rec._capture.finalize.return_value = {"combined": "c.wav"}
        return rec

    def test_stop_recording_returns_before_the_file_work(self):
        from app.recording.recorder import RecordingState
        rec = self._recorder()
        with patch.object(rec, "_start_finalize_worker") as start_worker:
            rec.stop_recording()

        rec._capture.stop_streams.assert_called_once()
        rec._capture.finalize.assert_not_called()   # the whole point
        start_worker.assert_called_once()
        self.assertEqual(rec.state, RecordingState.PROCESSING)

    def test_a_short_recording_is_discarded_without_mixing(self):
        from app.recording.recorder import RecordingState
        rec = self._recorder()
        rec._capture.get_elapsed_time.return_value = 2.0   # under the 5s min
        discarded = []
        rec.recording_discarded.connect(discarded.append)

        with patch.object(rec, "_start_finalize_worker") as start_worker:
            rec.stop_recording()

        rec._capture.discard.assert_called_once()
        rec._capture.finalize.assert_not_called()
        start_worker.assert_not_called()
        self.assertEqual(discarded, [2.0])
        self.assertEqual(rec.state, RecordingState.IDLE)

    def test_finalized_writes_metadata_and_announces_the_recording(self):
        from app.recording.recorder import RecordingState
        rec = self._recorder()
        rec._state = RecordingState.PROCESSING
        rec._current_session["duration"] = 60.0
        finished = []
        rec.recording_finished.connect(finished.append)

        rec._on_finalized({"combined": "c.wav"})

        self.assertEqual(rec.state, RecordingState.IDLE)
        self.assertEqual(len(finished), 1)
        self.assertEqual(finished[0]["audio_files"], {"combined": "c.wav"})
        written = json.loads((self.dir / "metadata.json").read_text())
        self.assertEqual(written["audio_files"], {"combined": "c.wav"})

    def test_finalize_failure_surfaces_and_returns_to_idle(self):
        from app.recording.recorder import RecordingState
        rec = self._recorder()
        rec._state = RecordingState.PROCESSING
        errors = []
        rec.error_occurred.connect(errors.append)

        rec._on_finalize_failed("disk full")

        self.assertEqual(len(errors), 1)
        self.assertIn("disk full", errors[0])
        self.assertEqual(rec.state, RecordingState.IDLE)

    def test_a_new_recording_is_refused_while_still_processing(self):
        from app.recording.recorder import RecordingState
        rec = self._recorder()
        rec._state = RecordingState.PROCESSING
        before = rec._current_session

        rec.start_recording(mic_device=1)

        # Starting now would overwrite _current_session while the worker is
        # still writing that session's metadata.
        self.assertIs(rec._current_session, before)
        self.assertEqual(rec.state, RecordingState.PROCESSING)

    def test_stop_is_ignored_while_already_processing(self):
        from app.recording.recorder import RecordingState
        rec = self._recorder()
        rec._state = RecordingState.PROCESSING
        rec.stop_recording()
        rec._capture.stop_streams.assert_not_called()


class TestShutdownDoesNotOrphanTheSession(unittest.TestCase):
    """Closing mid-finalize must still leave metadata.json on disk."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def _recorder_mid_finalize(self, audio_files):
        from app.recording.recorder import Recorder, RecordingState
        rec = Recorder(_FakeConfig())
        rec._state = RecordingState.PROCESSING
        rec._current_session = {"id": "x", "directory": str(self.dir),
                                "duration": 60.0}
        worker = MagicMock()
        worker.isRunning.return_value = False
        worker.audio_files = audio_files
        rec._finalize_worker = worker
        return rec

    def test_completed_work_is_saved_without_the_event_loop(self):
        from app.recording.recorder import RecordingState
        rec = self._recorder_mid_finalize({"combined": "c.wav"})
        finished = []
        rec.recording_finished.connect(finished.append)

        rec.finish_pending_finalize()

        written = json.loads((self.dir / "metadata.json").read_text())
        self.assertEqual(written["audio_files"], {"combined": "c.wav"})
        self.assertEqual(len(finished), 1)
        self.assertEqual(rec.state, RecordingState.IDLE)

    def test_calling_it_twice_saves_once(self):
        # closeEvent may run it after the signal already arrived normally.
        rec = self._recorder_mid_finalize({"combined": "c.wav"})
        finished = []
        rec.recording_finished.connect(finished.append)

        rec.finish_pending_finalize()
        rec.finish_pending_finalize()

        self.assertEqual(len(finished), 1)

    def test_a_still_running_worker_is_left_alone(self):
        rec = self._recorder_mid_finalize({"combined": "c.wav"})
        rec._finalize_worker.isRunning.return_value = True
        finished = []
        rec.recording_finished.connect(finished.append)

        rec.finish_pending_finalize()

        self.assertEqual(finished, [])
        self.assertFalse((self.dir / "metadata.json").exists())

    def test_a_failed_finalize_writes_nothing(self):
        rec = self._recorder_mid_finalize(None)
        finished = []
        rec.recording_finished.connect(finished.append)

        rec.finish_pending_finalize()

        self.assertEqual(finished, [])
        self.assertFalse((self.dir / "metadata.json").exists())


class TestFinalizeWiringEndToEnd(unittest.TestCase):
    """The real QThread path, start() to recording_finished."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_recording_finished_arrives_after_the_worker_runs(self):
        from PyQt6.QtWidgets import QApplication
        from app.recording.recorder import Recorder, RecordingState

        app = QApplication.instance() or QApplication([])
        rec = Recorder(_FakeConfig())
        rec._state = RecordingState.RECORDING
        rec._current_session = {"id": "x", "directory": str(self.dir)}
        rec._capture = MagicMock()
        rec._capture.get_elapsed_time.return_value = 60.0
        rec._capture._capture_status = None
        rec._capture.finalize.return_value = {"combined": "c.wav"}

        finished = []
        rec.recording_finished.connect(finished.append)

        rec.stop_recording()
        self.assertEqual(rec.state, RecordingState.PROCESSING)

        rec._finalize_worker.wait(5000)
        app.processEvents()

        self.assertEqual(rec.state, RecordingState.IDLE)
        self.assertEqual(len(finished), 1)
        self.assertEqual(finished[0]["audio_files"], {"combined": "c.wav"})


if __name__ == "__main__":
    unittest.main()
