import os
import unittest
from unittest.mock import MagicMock, patch

import numpy as np

from app.transcription.transcriber import TranscriptResult, TranscriptSegment
from app.transcription.sherpa_diarizer import (
    SherpaOnnxDiarizer,
    SpeakerSegment,
    are_models_available,
)
from app.transcription.diarizer import DiarizationWorker
from app.utils.config import Config
from app.utils.dependency_checker import DependencyChecker


class TestSherpaOnnxDiarizer(unittest.TestCase):
    def test_are_models_available(self):
        with patch("pathlib.Path.exists", return_value=True), \
             patch("pathlib.Path.stat") as mock_stat:
            mock_stat.return_value.st_size = 5_000_000
            self.assertTrue(are_models_available())

    def test_diarize_with_mocked_processor(self):
        with patch("app.transcription.sherpa_diarizer.download_models"), \
             patch("sherpa_onnx.OfflineSpeakerDiarization") as MockDiarization, \
             patch("soundfile.read") as mock_read:

            # Mock 1-second audio at 16000Hz
            mock_read.return_value = (np.zeros(16000, dtype=np.float32), 16000)

            mock_seg = MagicMock()
            mock_seg.start = 0.0
            mock_seg.end = 1.0
            mock_seg.speaker = 0

            mock_result = MagicMock()
            mock_result.sort_by_start_time.return_value = [mock_seg]

            mock_instance = MagicMock()
            mock_instance.sample_rate = 16000
            mock_instance.process.return_value = mock_result
            MockDiarization.return_value = mock_instance

            diarizer = SherpaOnnxDiarizer()
            segments = diarizer.diarize("/fake/audio.wav")

            self.assertEqual(len(segments), 1)
            self.assertEqual(segments[0].speaker, "Speaker 1")
            self.assertEqual(segments[0].start, 0.0)
            self.assertEqual(segments[0].end, 1.0)


class TestDiarizationWorkerRouting(unittest.TestCase):
    def test_worker_routes_to_sherpa_onnx_by_default(self):
        transcript = TranscriptResult(
            segments=[TranscriptSegment(start=0.0, end=1.0, text="Hello world")]
        )
        worker = DiarizationWorker(
            audio_path="/fake/audio.wav",
            transcript_result=transcript,
            engine="sherpa_onnx",
        )

        with patch("app.transcription.sherpa_diarizer.SherpaOnnxDiarizer") as MockDiarizer:
            mock_inst = MagicMock()
            mock_inst.diarize.return_value = [
                SpeakerSegment(start=0.0, end=1.0, speaker="Speaker 1")
            ]
            MockDiarizer.return_value = mock_inst

            results = []
            worker.finished.connect(results.append)
            worker.run()

            self.assertEqual(len(results), 1)
            self.assertEqual(results[0].segments[0].speaker, "Speaker 1")

    def test_worker_routes_to_pyannote_when_configured(self):
        from collections import namedtuple
        Turn = namedtuple("Turn", ["start", "end"])
        transcript = TranscriptResult(
            segments=[TranscriptSegment(start=0.0, end=1.0, text="Hello world")]
        )
        worker = DiarizationWorker(
            audio_path="/fake/audio.wav",
            transcript_result=transcript,
            hf_token="test-hf-token",
            engine="pyannote",
        )

        with patch("app.transcription.diarizer._get_pipeline") as mock_get_pipeline, \
             patch("soundfile.read", return_value=(np.zeros(16000, dtype=np.float32), 16000)), \
             patch("torch.from_numpy"), \
             patch("torch.set_num_threads"):

            mock_pipeline = MagicMock()
            mock_pipeline.return_value.speaker_diarization.itertracks.return_value = [
                (Turn(0.0, 1.0), None, "SPEAKER_00")
            ]
            mock_get_pipeline.return_value = mock_pipeline

            results = []
            worker.finished.connect(results.append)
            worker.run()

            self.assertEqual(len(results), 1)
            self.assertEqual(results[0].segments[0].speaker, "SPEAKER_00")


class TestDependencyCheckerEngineSupport(unittest.TestCase):
    def test_sherpa_onnx_does_not_require_hf_token(self):
        cfg = Config.__new__(Config)
        cfg._data = {"diarization": {"engine": "sherpa_onnx", "hf_token": ""}}
        checker = DependencyChecker(config=cfg)
        res = checker.check_hf_token()
        self.assertTrue(res["passed"])
        self.assertIn("Not required", res["message"])

    def test_pyannote_requires_hf_token(self):
        cfg = Config.__new__(Config)
        cfg._data = {"diarization": {"engine": "pyannote", "hf_token": ""}}
        checker = DependencyChecker(config=cfg)
        res = checker.check_hf_token()
        self.assertFalse(res["passed"])
        self.assertIn("No HuggingFace token", res["message"])


if __name__ == "__main__":
    unittest.main()
