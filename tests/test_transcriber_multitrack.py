# tests/test_transcriber_multitrack.py
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import unittest
from unittest import mock

from app.transcription import transcriber
from app.transcription.transcriber import TranscriptionWorker


class _FakeSegment:
    def __init__(self, start, end, text):
        self.start = start
        self.end = end
        self.text = text
        self.avg_logprob = -0.2


class _FakeInfo:
    def __init__(self, duration):
        self.language = "en"
        self.duration = duration


class _FakeModel:
    """Returns canned segments per audio path."""

    def __init__(self, by_path):
        self._by_path = by_path
        self.transcribed = []

    def transcribe(self, path, language=None, vad_filter=True):
        self.transcribed.append(path)
        segs = self._by_path.get(path, [])
        return iter(segs), _FakeInfo(60.0)


class MultiTrackTestCase(unittest.TestCase):
    def _run(self, by_path, tracks):
        model = _FakeModel(by_path)
        results, errors = [], []
        with mock.patch.object(transcriber, "_get_model", return_value=model):
            worker = TranscriptionWorker("unused.wav", tracks=tracks)
            worker.finished.connect(results.append)
            worker.error.connect(errors.append)
            worker.run()
        self.assertEqual(errors, [], f"worker errored: {errors}")
        self.assertEqual(len(results), 1)
        return results[0], model


class TestMultiTrackTranscription(MultiTrackTestCase):
    """Transcribing the tracks separately avoids handing Whisper the mixed
    audio, where speaker bleed appears as a doubled copy of every remote
    sentence."""

    def test_transcribes_every_track(self):
        _, model = self._run(
            {"mic.wav": [_FakeSegment(0.0, 1.0, "hello from me")],
             "sys.wav": [_FakeSegment(2.0, 3.0, "hello from them")]},
            tracks=[("You", "mic.wav"), ("Remote", "sys.wav")],
        )
        self.assertEqual(model.transcribed, ["mic.wav", "sys.wav"])

    def test_labels_segments_by_their_track(self):
        result, _ = self._run(
            {"mic.wav": [_FakeSegment(0.0, 1.0, "hello from me")],
             "sys.wav": [_FakeSegment(2.0, 3.0, "hello from them")]},
            tracks=[("You", "mic.wav"), ("Remote", "sys.wav")],
        )
        self.assertEqual([(s.speaker, s.text) for s in result.segments],
                         [("You", "hello from me"), ("Remote", "hello from them")])

    def test_drops_bleed_picked_up_by_the_mic(self):
        result, _ = self._run(
            {"mic.wav": [_FakeSegment(2.0, 3.0, "the release is delayed until Tuesday")],
             "sys.wav": [_FakeSegment(2.0, 3.0, "The release is delayed until Tuesday.")]},
            tracks=[("You", "mic.wav"), ("Remote", "sys.wav")],
        )
        self.assertEqual(len(result.segments), 1)
        self.assertEqual(result.segments[0].speaker, "Remote")

    def test_orders_the_merged_transcript_by_time(self):
        result, _ = self._run(
            {"mic.wav": [_FakeSegment(5.0, 6.0, "second thing said")],
             "sys.wav": [_FakeSegment(1.0, 2.0, "first thing said")]},
            tracks=[("You", "mic.wav"), ("Remote", "sys.wav")],
        )
        self.assertEqual([s.text for s in result.segments],
                         ["first thing said", "second thing said"])

    def test_a_missing_track_does_not_fail_the_job(self):
        result, model = self._run(
            {"sys.wav": [_FakeSegment(1.0, 2.0, "only remote audio here")]},
            tracks=[("You", "mic.wav"), ("Remote", "sys.wav")],
        )
        self.assertEqual(len(result.segments), 1)
        self.assertEqual(result.segments[0].speaker, "Remote")

    def test_single_path_still_works(self):
        model = _FakeModel({"combined.wav": [_FakeSegment(0.0, 1.0, "mixed audio")]})
        results = []
        with mock.patch.object(transcriber, "_get_model", return_value=model):
            worker = TranscriptionWorker("combined.wav")
            worker.finished.connect(results.append)
            worker.run()
        self.assertEqual(len(results[0].segments), 1)
        # Unlabelled: diarization assigns speakers on this path.
        self.assertEqual(results[0].segments[0].speaker, "")

    def test_cancelling_between_tracks_emits_cancelled_not_finished(self):
        model = _FakeModel({"mic.wav": [_FakeSegment(0.0, 1.0, "hi")],
                            "sys.wav": [_FakeSegment(2.0, 3.0, "hello")]})
        results, cancels = [], []
        with mock.patch.object(transcriber, "_get_model", return_value=model):
            worker = TranscriptionWorker(
                "unused.wav", tracks=[("You", "mic.wav"), ("Remote", "sys.wav")])
            worker.finished.connect(results.append)
            worker.cancelled.connect(lambda: cancels.append(True))
            worker.cancel()
            worker.run()
        self.assertEqual(results, [])
        self.assertEqual(len(cancels), 1)


if __name__ == "__main__":
    unittest.main()
