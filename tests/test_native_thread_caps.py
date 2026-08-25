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

    def test_idle_transcription_gets_nearly_every_core(self):
        # Half the cores is headroom for the real-time capture callback.
        # With no recording running there is nothing to protect, and the
        # cap was costing roughly a 2x slowdown for no benefit.
        from app.transcription import transcriber
        transcriber._MODEL_CACHE.clear()
        with patch("faster_whisper.WhisperModel") as MockModel, \
             patch("os.cpu_count", return_value=8):
            transcriber._get_model("base", "cpu", "int8", full_cpu=True)
        _, kwargs = MockModel.call_args
        self.assertEqual(kwargs["cpu_threads"], 7)

    def test_recording_in_progress_still_gets_half(self):
        from app.transcription import transcriber
        transcriber._MODEL_CACHE.clear()
        with patch("faster_whisper.WhisperModel") as MockModel, \
             patch("os.cpu_count", return_value=8):
            transcriber._get_model("base", "cpu", "int8", full_cpu=False)
        _, kwargs = MockModel.call_args
        self.assertEqual(kwargs["cpu_threads"], 4)

    def test_thread_count_is_part_of_the_cache_key(self):
        # cpu_threads is baked into the model at construction, so a cache
        # keyed without it would hand a recording-era 4-thread model to
        # every later idle job.
        from app.transcription import transcriber
        transcriber._MODEL_CACHE.clear()
        with patch("faster_whisper.WhisperModel") as MockModel, \
             patch("os.cpu_count", return_value=8):
            transcriber._get_model("base", "cpu", "int8", full_cpu=False)
            transcriber._get_model("base", "cpu", "int8", full_cpu=True)
        self.assertEqual(MockModel.call_count, 2)

    def test_same_thread_count_reuses_the_cached_model(self):
        from app.transcription import transcriber
        transcriber._MODEL_CACHE.clear()
        with patch("faster_whisper.WhisperModel") as MockModel, \
             patch("os.cpu_count", return_value=8):
            transcriber._get_model("base", "cpu", "int8", full_cpu=True)
            transcriber._get_model("base", "cpu", "int8", full_cpu=True)
        self.assertEqual(MockModel.call_count, 1)


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

    def test_full_cpu_still_reserves_one_core_for_the_ui(self):
        from app.transcription.diarizer import DiarizationWorker

        worker = DiarizationWorker(
            audio_path="/fake/audio.wav",
            transcript_result=MagicMock(),
            hf_token="fake-token",
            full_cpu=True,
        )

        with patch("app.transcription.diarizer._get_pipeline") as mock_get_pipeline, \
             patch("soundfile.read", return_value=(MagicMock(ndim=1), 16000)), \
             patch("torch.from_numpy") as mock_from_numpy, \
             patch("torch.set_num_threads") as mock_set_threads, \
             patch("os.cpu_count", return_value=16):
            mock_pipeline = MagicMock()
            mock_pipeline.return_value = MagicMock(itertracks=lambda yield_label: [])
            mock_get_pipeline.return_value = mock_pipeline
            mock_from_numpy.return_value.unsqueeze.return_value = MagicMock()

            worker.run()

        # Diarization is the heaviest torch workload and normally gets every
        # core, but the recorder being idle doesn't mean the UI is idle —
        # switching recordings does synchronous work (JSON parse, transcript
        # widget rebuild) that stalls visibly if torch's thread pool has
        # saturated every core. One core held back is enough to fix that.
        mock_set_threads.assert_called_once_with(15)

class TestWhisperModelCacheIsBounded(unittest.TestCase):
    """The cache key spans model_size AND cpu_threads, so an unbounded dict
    accumulated a separate multi-GB model for every size the user tried and
    for each of the two thread counts. base+small+medium x2 was measured at
    ~4GB retained, which paged the whole app out after a big job."""

    def test_only_one_model_stays_resident(self):
        from app.transcription import transcriber
        transcriber._MODEL_CACHE.clear()
        with patch("faster_whisper.WhisperModel"), \
             patch("os.cpu_count", return_value=8):
            transcriber._get_model("base", "cpu", "int8", full_cpu=True)
            transcriber._get_model("small", "cpu", "int8", full_cpu=True)
            transcriber._get_model("medium", "cpu", "int8", full_cpu=True)
        self.assertEqual(len(transcriber._MODEL_CACHE), 1)
        self.assertEqual(
            list(transcriber._MODEL_CACHE)[0][:3], ("medium", "cpu", "int8"))

    def test_thread_count_variants_do_not_both_stay_resident(self):
        from app.transcription import transcriber
        transcriber._MODEL_CACHE.clear()
        with patch("faster_whisper.WhisperModel"), \
             patch("os.cpu_count", return_value=8):
            transcriber._get_model("base", "cpu", "int8", full_cpu=False)
            transcriber._get_model("base", "cpu", "int8", full_cpu=True)
        self.assertEqual(len(transcriber._MODEL_CACHE), 1)
        # The idle (7-thread) variant is the one still held.
        self.assertEqual(list(transcriber._MODEL_CACHE)[0][3], 7)

    def test_repeated_identical_requests_stay_warm(self):
        # The whole point of the cache: same settings must not reload.
        from app.transcription import transcriber
        transcriber._MODEL_CACHE.clear()
        with patch("faster_whisper.WhisperModel") as MockModel, \
             patch("os.cpu_count", return_value=8):
            for _ in range(5):
                transcriber._get_model("base", "cpu", "int8", full_cpu=True)
        self.assertEqual(MockModel.call_count, 1)

if __name__ == "__main__":
    unittest.main()
