import os
import unittest
from unittest.mock import MagicMock, patch

import numpy as np

from app.transcription.transcriber import TranscriptResult, TranscriptSegment, TranscriptionWorker
from app.transcription.sherpa_transcriber import (
    SherpaOnnxTranscriber,
    is_model_available,
    download_model,
)
from app.utils.config import Config
from app.utils.dependency_checker import DependencyChecker


class TestSherpaOnnxTranscriber(unittest.TestCase):
    def test_is_model_available(self):
        with patch("pathlib.Path.exists", return_value=True), \
             patch("pathlib.Path.stat") as mock_stat:
            mock_stat.return_value.st_size = 5_000_000
            self.assertTrue(is_model_available("base"))

    def test_transcribe_with_mocked_recognizer(self):
        with patch("os.path.exists", return_value=True), \
             patch("app.transcription.sherpa_transcriber.is_model_available", return_value=True), \
             patch("app.transcription.sherpa_transcriber.download_model"), \
             patch("sherpa_onnx.OfflineRecognizer.from_whisper") as mock_from_whisper, \
             patch("soundfile.read") as mock_read:

            # Mock 2-second audio at 16000Hz
            mock_read.return_value = (np.zeros(32000, dtype=np.float32), 16000)

            mock_res = MagicMock()
            mock_res.text = "Hello world this is a test"
            mock_res.segment_timestamps = [0.0, 1.0]
            mock_res.segment_durations = [1.0, 1.0]
            mock_res.segment_texts = ["Hello world", "this is a test"]
            mock_res.lang = "en"

            mock_stream = MagicMock()
            mock_stream.result = mock_res

            mock_rec = MagicMock()
            mock_rec.create_stream.return_value = mock_stream
            mock_from_whisper.return_value = mock_rec

            transcriber = SherpaOnnxTranscriber(model_name="base", language="en")
            segments, info = transcriber.transcribe("/fake/audio.wav")

            self.assertEqual(len(segments), 2)
            self.assertEqual(segments[0].text, "Hello world")
            self.assertEqual(segments[0].start, 0.0)
            self.assertEqual(segments[0].end, 1.0)
            self.assertEqual(segments[1].text, "this is a test")
            self.assertEqual(segments[1].start, 1.0)
            self.assertEqual(segments[1].end, 2.0)
            self.assertEqual(info["duration"], 2.0)

    def test_transcribe_cancelled_returns_none(self):
        with patch("os.path.exists", return_value=True), \
             patch("app.transcription.sherpa_transcriber.is_model_available", return_value=True), \
             patch("app.transcription.sherpa_transcriber.download_model"), \
             patch("sherpa_onnx.OfflineRecognizer.from_whisper") as mock_from_whisper, \
             patch("soundfile.read") as mock_read:

            mock_read.return_value = (np.zeros(16000, dtype=np.float32), 16000)
            mock_from_whisper.return_value = MagicMock()

            transcriber = SherpaOnnxTranscriber(model_name="base")
            result = transcriber.transcribe(
                "/fake/audio.wav",
                is_cancelled=lambda: True,
            )
            self.assertIsNone(result)

    def test_transcribe_long_audio_with_vad_segments(self):
        with patch("os.path.exists", return_value=True), \
             patch("app.transcription.sherpa_transcriber.is_model_available", return_value=True), \
             patch("app.transcription.sherpa_transcriber.download_model"), \
             patch("sherpa_onnx.OfflineRecognizer.from_whisper") as mock_from_whisper, \
             patch("soundfile.read") as mock_read:

            # Mock 100-second audio
            mock_read.return_value = (np.zeros(16000 * 100, dtype=np.float32), 16000)

            transcriber = SherpaOnnxTranscriber(model_name="base")
            # Mock _get_speech_segments to return 3 distinct speech segments across the 100s
            transcriber._get_speech_segments = MagicMock(return_value=[
                (0, np.zeros(16000 * 10, dtype=np.float32)),        # 0s - 10s
                (16000 * 30, np.zeros(16000 * 15, dtype=np.float32)), # 30s - 45s
                (16000 * 70, np.zeros(16000 * 20, dtype=np.float32)), # 70s - 90s
            ])

            mock_results = [
                MagicMock(text="First section", segment_timestamps=None),
                MagicMock(text="Second section", segment_timestamps=None),
                MagicMock(text="Third section", segment_timestamps=None),
            ]
            mock_streams = [MagicMock(result=r) for r in mock_results]
            mock_rec = MagicMock()
            mock_rec.create_stream.side_effect = mock_streams
            mock_from_whisper.return_value = mock_rec

            segments, info = transcriber.transcribe("/fake/audio.wav")

            self.assertEqual(len(segments), 3)
            self.assertEqual(segments[0].text, "First section")
            self.assertEqual(segments[0].start, 0.0)
            self.assertEqual(segments[0].end, 10.0)
            self.assertEqual(segments[1].text, "Second section")
            self.assertEqual(segments[1].start, 30.0)
            self.assertEqual(segments[1].end, 45.0)
            self.assertEqual(segments[2].text, "Third section")
            self.assertEqual(segments[2].start, 70.0)
            self.assertEqual(segments[2].end, 90.0)
            self.assertEqual(info["duration"], 100.0)

    def test_chunking_fallback_when_vad_fails(self):
        with patch("app.transcription.sherpa_transcriber.ensure_vad_model", side_effect=RuntimeError("VAD error")):
            transcriber = SherpaOnnxTranscriber(model_name="base")
            audio = np.zeros(16000 * 50, dtype=np.float32)
            segs = transcriber._get_speech_segments(audio, sample_rate=16000)
            # 50 seconds should be chunked into 3 pieces (20s, 20s, 10s)
            self.assertEqual(len(segs), 3)
            self.assertEqual(segs[0][0], 0)
            self.assertEqual(segs[1][0], 16000 * 20)
            self.assertEqual(segs[2][0], 16000 * 40)


class TestTranscriptionWorkerSherpaOnnxRouting(unittest.TestCase):
    def test_worker_routes_to_sherpa_onnx_single_track(self):
        worker = TranscriptionWorker(
            audio_path="/fake/audio.wav",
            model_size="base",
            engine="sherpa_onnx",
        )
        with patch("app.transcription.sherpa_transcriber.SherpaOnnxTranscriber") as MockTranscriber:
            mock_inst = MagicMock()
            mock_inst.transcribe.return_value = (
                [TranscriptSegment(start=0.0, end=1.0, text="Hello ONNX")],
                {"language": "en", "duration": 1.0},
            )
            MockTranscriber.return_value = mock_inst

            results = []
            worker.finished.connect(results.append)
            worker.run()

            self.assertEqual(len(results), 1)
            self.assertEqual(results[0].segments[0].text, "Hello ONNX")
            self.assertEqual(results[0].model_size, "base")

    def test_worker_routes_to_sherpa_onnx_multi_track(self):
        worker = TranscriptionWorker(
            audio_path="/fake/combined.wav",
            model_size="base",
            tracks=[("You", "/fake/mic.wav"), ("Speakers", "/fake/sys.wav")],
            engine="sherpa_onnx",
        )
        with patch("app.transcription.sherpa_transcriber.SherpaOnnxTranscriber") as MockTranscriber:
            mock_inst = MagicMock()
            mock_inst.transcribe.side_effect = [
                ([TranscriptSegment(start=0.0, end=1.0, text="My speech")], {"language": "en", "duration": 1.0}),
                ([TranscriptSegment(start=1.0, end=2.0, text="Their speech")], {"language": "en", "duration": 2.0}),
            ]
            MockTranscriber.return_value = mock_inst

            results = []
            worker.finished.connect(results.append)
            worker.run()

            self.assertEqual(len(results), 1)
            self.assertEqual(len(results[0].segments), 2)
            self.assertEqual(results[0].segments[0].speaker, "You")
            self.assertEqual(results[0].segments[1].speaker, "Speakers")

    def test_worker_cancelled_before_run(self):
        worker = TranscriptionWorker(
            audio_path="/fake/audio.wav",
            model_size="base",
            engine="sherpa_onnx",
        )
        cancelled = []
        worker.cancelled.connect(lambda: cancelled.append(True))
        worker.cancel()
        worker.run()
        self.assertEqual(cancelled, [True])


class TestDependencyCheckerSherpaOnnx(unittest.TestCase):
    def test_check_whisper_model_sherpa_onnx(self):
        cfg = Config.__new__(Config)
        cfg._data = {"transcription": {"engine": "sherpa_onnx", "model_size": "base"}}
        checker = DependencyChecker(config=cfg)

        with patch("app.transcription.sherpa_transcriber.is_model_available", return_value=True):
            res = checker.check_whisper_model()
            self.assertTrue(res["passed"])
            self.assertIn("ONNX Model 'base' is cached", res["message"])

        with patch("app.transcription.sherpa_transcriber.is_model_available", return_value=False):
            res = checker.check_whisper_model()
            self.assertFalse(res["passed"])
            self.assertIn("not found in cache", res["message"])


class TestSettingsDialogTranscriptionEngine(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PyQt6.QtWidgets import QApplication
        import sys
        cls.app = QApplication.instance() or QApplication(sys.argv)

    def test_settings_dialog_loads_and_switches_transcription_engine(self):
        import copy
        from app.utils.config import DEFAULT_CONFIG
        from app.ui.settings_dialog import SettingsDialog
        cfg = Config.__new__(Config)
        cfg._data = copy.deepcopy(DEFAULT_CONFIG)
        cfg._data["transcription"]["engine"] = "faster_whisper"
        cfg._data["transcription"]["model_size"] = "base"
        cfg.save = MagicMock()

        dialog = SettingsDialog(cfg)
        self.assertEqual(dialog.transcription_engine_combo.currentData(), "faster_whisper")

        # Switch to sherpa_onnx
        idx = dialog.transcription_engine_combo.findData("sherpa_onnx")
        dialog.transcription_engine_combo.setCurrentIndex(idx)
        self.assertEqual(dialog.transcription_engine_combo.currentData(), "sherpa_onnx")

        # Verify model combo options changed to ONNX models
        model_data = [dialog.model_combo.itemData(i) for i in range(dialog.model_combo.count())]
        self.assertIn("moonshine-tiny", model_data)
        self.assertIn("base.en", model_data)

        # Select moonshine-tiny and save
        m_idx = dialog.model_combo.findData("moonshine-tiny")
        dialog.model_combo.setCurrentIndex(m_idx)
        dialog._apply_settings()

        self.assertEqual(cfg._data["transcription"]["engine"], "sherpa_onnx")
        self.assertEqual(cfg._data["transcription"]["model_size"], "moonshine-tiny")


if __name__ == "__main__":
    unittest.main()
