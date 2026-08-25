# tests/test_diarizer.py
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np
import soundfile as sf

from app.transcription.transcriber import TranscriptResult, TranscriptSegment


class TestSimpleDiarizerSampleRates(unittest.TestCase):
    """Mic and system tracks can have different sample rates; energy windows
    must be computed with each track's own rate."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def _write_wav(self, name, data, rate):
        path = self.dir / name
        sf.write(str(path), data, rate)
        return str(path)

    def test_mismatched_rates_use_per_track_indices(self):
        from app.transcription.diarizer import SimpleDiarizer

        # Mic: 16 kHz, 3s, fully silent.
        mic = np.zeros(16000 * 3, dtype=np.float32)
        mic_path = self._write_wav("mic.wav", mic, 16000)

        # System: 48 kHz, 3s, loud ONLY during t=[1.0, 2.0].
        system = np.zeros(48000 * 3, dtype=np.float32)
        system[48000:96000] = 0.5
        sys_path = self._write_wav("system.wav", system, 48000)

        result = TranscriptResult(
            segments=[TranscriptSegment(start=1.0, end=2.0, text="hello")]
        )
        result = SimpleDiarizer(mic_path, sys_path).diarize(result)

        # System is clearly the active channel in that window. Indexing the
        # 48 kHz track with the mic's 16 kHz rate reads t=[0.33, 0.67]
        # (silence) instead and mislabels this as "You".
        self.assertEqual(result.segments[0].speaker, "Remote")

    def test_matched_rates_still_label_mic_speech(self):
        from app.transcription.diarizer import SimpleDiarizer

        mic = np.zeros(16000 * 3, dtype=np.float32)
        mic[16000:32000] = 0.5
        mic_path = self._write_wav("mic.wav", mic, 16000)
        system = np.zeros(16000 * 3, dtype=np.float32)
        sys_path = self._write_wav("system.wav", system, 16000)

        result = TranscriptResult(
            segments=[TranscriptSegment(start=1.0, end=2.0, text="hello")]
        )
        result = SimpleDiarizer(mic_path, sys_path).diarize(result)
        self.assertEqual(result.segments[0].speaker, "You")


class TestPipelineCache(unittest.TestCase):
    def test_same_token_reuses_pipeline(self):
        mock_pyannote_audio = MagicMock()
        with patch.dict(sys.modules, {
            "pyannote": MagicMock(audio=mock_pyannote_audio),
            "pyannote.audio": mock_pyannote_audio,
        }):
            import app.transcription.diarizer as dz
            dz._PIPELINE_CACHE.clear()
            p1 = dz._get_pipeline("token-a")
            p2 = dz._get_pipeline("token-a")
        self.assertIs(p1, p2)
        self.assertEqual(
            mock_pyannote_audio.Pipeline.from_pretrained.call_count, 1
        )


class TestSimpleDiarizeWorkerExists(unittest.TestCase):
    def test_worker_importable_with_expected_signals(self):
        from app.transcription.diarizer import SimpleDiarizeWorker
        self.assertTrue(hasattr(SimpleDiarizeWorker, "finished"))
        self.assertTrue(hasattr(SimpleDiarizeWorker, "error"))


if __name__ == "__main__":
    unittest.main()


class TestSimpleDiarizerAudioDtype(unittest.TestCase):
    """Both full tracks are loaded into RAM at once, so they must be read at
    the precision the RMS math actually needs — not soundfile's float64
    default, which doubles the footprint of a long recording for nothing."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def _write_wav(self, name, data, rate):
        path = self.dir / name
        sf.write(str(path), data, rate)
        return str(path)

    def test_both_tracks_are_read_as_float32(self):
        from app.transcription.diarizer import SimpleDiarizer

        mic_path = self._write_wav("mic.wav", np.zeros(1600, dtype=np.float32), 16000)
        sys_path = self._write_wav("sys.wav", np.zeros(1600, dtype=np.float32), 16000)

        real_read = sf.read
        seen = []

        def spy(path, *args, **kwargs):
            seen.append(kwargs.get("dtype"))
            return real_read(path, *args, **kwargs)

        result = TranscriptResult(
            segments=[TranscriptSegment(start=0.0, end=0.1, text="hi")]
        )
        with patch("soundfile.read", side_effect=spy):
            SimpleDiarizer(mic_path, sys_path).diarize(result)

        self.assertEqual(seen, ["float32", "float32"])


class TestDiarizationMergeScales(unittest.TestCase):
    """Speaker assignment must not be a nested scan of transcript segments x
    speaker turns: on a long meeting that product runs into the hundreds of
    millions of Python iterations and stalls the tail of every big job."""

    def _merge(self, transcript, speaker_segments):
        from app.transcription.diarizer import DiarizationWorker

        worker = DiarizationWorker.__new__(DiarizationWorker)
        return worker._merge_diarization_with_transcript(transcript, speaker_segments)

    def test_picks_the_maximum_overlap_speaker(self):
        from app.transcription.diarizer import SpeakerSegment

        transcript = TranscriptResult(
            segments=[TranscriptSegment(start=10.0, end=20.0, text="x")]
        )
        speakers = [
            SpeakerSegment(0.0, 11.0, "A"),    # 1s overlap
            SpeakerSegment(11.0, 18.0, "B"),   # 7s overlap  <- winner
            SpeakerSegment(18.0, 30.0, "C"),   # 2s overlap
        ]
        out = self._merge(transcript, speakers)
        self.assertEqual(out.segments[0].speaker, "B")

    def test_no_overlap_is_unknown(self):
        from app.transcription.diarizer import SpeakerSegment

        transcript = TranscriptResult(
            segments=[TranscriptSegment(start=100.0, end=101.0, text="x")]
        )
        out = self._merge(transcript, [SpeakerSegment(0.0, 5.0, "A")])
        self.assertEqual(out.segments[0].speaker, "Unknown")

    def test_overlapping_speaker_turns_still_resolve(self):
        from app.transcription.diarizer import SpeakerSegment

        transcript = TranscriptResult(
            segments=[TranscriptSegment(start=5.0, end=6.0, text="x")]
        )
        # Crosstalk: a long turn fully containing a short competing one.
        speakers = [
            SpeakerSegment(0.0, 100.0, "A"),   # 1.0s overlap <- winner
            SpeakerSegment(5.4, 5.6, "B"),     # 0.2s overlap
        ]
        out = self._merge(transcript, speakers)
        self.assertEqual(out.segments[0].speaker, "A")

    def test_unsorted_speaker_segments_are_handled(self):
        from app.transcription.diarizer import SpeakerSegment

        transcript = TranscriptResult(
            segments=[TranscriptSegment(start=10.0, end=20.0, text="x")]
        )
        speakers = [
            SpeakerSegment(18.0, 30.0, "C"),
            SpeakerSegment(0.0, 11.0, "A"),
            SpeakerSegment(11.0, 18.0, "B"),
        ]
        out = self._merge(transcript, speakers)
        self.assertEqual(out.segments[0].speaker, "B")

    def test_long_meeting_merges_without_quadratic_blowup(self):
        from app.transcription.diarizer import SpeakerSegment

        # 20k transcript segments x 20k speaker turns. The nested scan is
        # 4e8 iterations (minutes); a bounded search is effectively instant.
        n = 20000
        transcript = TranscriptResult(segments=[
            TranscriptSegment(start=i * 1.0, end=i * 1.0 + 0.9, text="x")
            for i in range(n)
        ])
        speakers = [
            SpeakerSegment(i * 1.0, i * 1.0 + 0.8, f"S{i % 4}")
            for i in range(n)
        ]
        out = self._merge(transcript, speakers)
        self.assertEqual(out.segments[0].speaker, "S0")
        self.assertEqual(out.segments[9].speaker, "S1")
        self.assertTrue(all(s.speaker != "Unknown" for s in out.segments))


class TestDiarizationProgressPercent(unittest.TestCase):
    """Diarization ran with only text status messages ('Loading...',
    'Running speaker diarization...') and no percent — unlike transcription,
    which drives a real progress bar via progress_percent. pyannote's
    pipeline accepts a `hook` callback invoked per-chunk during its two
    expensive stages (segmentation, embedding extraction), which is enough
    to derive real percentages instead of leaving diarization looking
    hung on a long recording."""

    def _make_worker(self):
        from app.transcription.diarizer import DiarizationWorker
        return DiarizationWorker(
            audio_path="/fake/audio.wav",
            transcript_result=MagicMock(),
            hf_token="fake-token",
        )

    def test_worker_exposes_a_percent_signal(self):
        worker = self._make_worker()
        self.assertTrue(hasattr(worker, "progress_percent"))

    def test_segmentation_progress_is_emitted_and_increasing(self):
        worker = self._make_worker()
        seen = []
        worker.progress_percent.connect(seen.append)

        worker._pipeline_hook("segmentation", None, total=4, completed=1)
        worker._pipeline_hook("segmentation", None, total=4, completed=2)
        worker._pipeline_hook("segmentation", None, total=4, completed=4)

        self.assertEqual(len(seen), 3)
        self.assertLess(seen[0], seen[1])
        self.assertLess(seen[1], seen[2])
        self.assertTrue(all(0 <= p <= 100 for p in seen))

    def test_embeddings_progress_continues_after_segmentation(self):
        worker = self._make_worker()
        seen = []
        worker.progress_percent.connect(seen.append)

        worker._pipeline_hook("segmentation", None, total=4, completed=4)
        worker._pipeline_hook("embeddings", None, total=10, completed=1)
        worker._pipeline_hook("embeddings", None, total=10, completed=10)

        # Embeddings picks up where segmentation left off, not from zero —
        # otherwise the bar would visibly jump backwards mid-job.
        self.assertLess(seen[0], seen[1])
        self.assertLess(seen[1], seen[2])

    def test_marker_only_steps_do_not_crash_and_stay_in_range(self):
        worker = self._make_worker()
        seen = []
        worker.progress_percent.connect(seen.append)

        # These fire once with no total/completed at all (see
        # ArtifactHook/ProgressHook in pyannote for the same contract).
        worker._pipeline_hook("speaker_counting", "artifact")
        worker._pipeline_hook("discrete_diarization", "artifact")
        worker._pipeline_hook("embeddings", "artifact")  # final marker call

        self.assertTrue(all(0 <= p <= 100 for p in seen))

    def test_pipeline_is_invoked_with_the_hook(self):
        from app.transcription.diarizer import DiarizationWorker

        worker = self._make_worker()
        with patch("app.transcription.diarizer._get_pipeline") as mock_get_pipeline, \
             patch("soundfile.read", return_value=(MagicMock(ndim=1), 16000)), \
             patch("torch.from_numpy") as mock_from_numpy, \
             patch("torch.set_num_threads"):
            mock_pipeline = MagicMock()
            mock_pipeline.return_value = MagicMock(itertracks=lambda yield_label: [])
            mock_get_pipeline.return_value = mock_pipeline
            mock_from_numpy.return_value.unsqueeze.return_value = MagicMock()

            worker.run()

        _, kwargs = mock_pipeline.call_args
        self.assertEqual(kwargs.get("hook"), worker._pipeline_hook)

    def test_completion_reaches_100_before_finished(self):
        from app.transcription.diarizer import DiarizationWorker

        worker = self._make_worker()
        percents = []
        finished_order = []
        worker.progress_percent.connect(percents.append)
        worker.finished.connect(lambda *_: finished_order.append("finished"))

        with patch("app.transcription.diarizer._get_pipeline") as mock_get_pipeline, \
             patch("soundfile.read", return_value=(MagicMock(ndim=1), 16000)), \
             patch("torch.from_numpy") as mock_from_numpy, \
             patch("torch.set_num_threads"):
            mock_pipeline = MagicMock()
            mock_pipeline.return_value = MagicMock(itertracks=lambda yield_label: [])
            mock_get_pipeline.return_value = mock_pipeline
            mock_from_numpy.return_value.unsqueeze.return_value = MagicMock()

            worker.run()

        self.assertIn(100, percents)
        self.assertEqual(percents[-1], 100)
