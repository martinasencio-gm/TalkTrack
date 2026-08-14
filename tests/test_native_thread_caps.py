import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import unittest
from unittest.mock import MagicMock, patch


class TestWhisperModelThreadCap(unittest.TestCase):
    def test_get_model_passes_capped_cpu_threads(self):
        from app.transcription import transcriber
        transcriber._MODEL_CACHE.clear()
        with patch("faster_whisper.WhisperModel") as MockModel:
            transcriber._get_model("base", "cpu", "int8")
        _, kwargs = MockModel.call_args
        self.assertIn("cpu_threads", kwargs)
        self.assertGreaterEqual(kwargs["cpu_threads"], 1)
        with patch("os.cpu_count", return_value=16):
            expected = max(1, 16 // 2)
        self.assertLessEqual(kwargs["cpu_threads"], expected + 8)  # sane upper bound, not a huge pool


class TestDiarizationThreadCap(unittest.TestCase):
    def test_run_caps_torch_threads_before_pipeline_call(self):
        from app.transcription.diarizer import DiarizationWorker

        worker = DiarizationWorker(
            audio_path="/fake/audio.wav",
            transcript_result=MagicMock(),
            hf_token="fake-token",
        )

        with patch("app.transcription.diarizer._get_pipeline") as mock_get_pipeline, \
             patch("soundfile.read", return_value=(MagicMock(ndim=1), 16000)), \
             patch("torch.from_numpy") as mock_from_numpy, \
             patch("torch.set_num_threads") as mock_set_threads:
            mock_pipeline = MagicMock()
            mock_pipeline.return_value = MagicMock(itertracks=lambda yield_label: [])
            mock_get_pipeline.return_value = mock_pipeline
            mock_from_numpy.return_value.unsqueeze.return_value = MagicMock()

            worker.run()

        mock_set_threads.assert_called_once()
        (call_arg,), _ = mock_set_threads.call_args
        self.assertGreaterEqual(call_arg, 1)


if __name__ == "__main__":
    unittest.main()
