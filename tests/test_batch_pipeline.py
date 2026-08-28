"""Tests for the per-recording batch pipeline.

The real workers are replaced with fakes: this covers which stages run in
which order and what reaches disk, not Whisper or pyannote themselves.
"""
import json
import tempfile
import unittest
import unittest.mock
from pathlib import Path

from PyQt6.QtCore import QObject, pyqtSignal


class _FakeSegment:
    def __init__(self, start, end, text, speaker=None):
        self.start, self.end, self.text, self.speaker = start, end, text, speaker


class _FakeResult:
    def __init__(self, segments=None, speaker=None):
        self.segments = segments or [
            _FakeSegment(0.0, 1.0, "hello", speaker),
            _FakeSegment(5.0, 6.0, "goodbye", speaker),
        ]
        self.merged = False

    def merge_adjacent_same_speaker(self, max_gap=0.5):
        self.merged = True

    def to_dict(self, speaker_names=None):
        return {"segments": [{"start": s.start, "end": s.end, "text": s.text,
                              "speaker": s.speaker} for s in self.segments]}

    def to_text(self, speaker_names=None):
        return "\n".join(s.text for s in self.segments)


class _FakeWorker(QObject):
    progress = pyqtSignal(str)
    finished = pyqtSignal(object)
    error = pyqtSignal(str)

    def __init__(self, result=None, error_message=None):
        super().__init__()
        self._result = result
        self._error = error_message
        self.calls = []

    def run(self):
        if self._error is not None:
            self.error.emit(self._error)
        else:
            self.finished.emit(self._result)


class _Recorder:
    """Builds fake workers and remembers how they were constructed."""

    def __init__(self, transcription=None, diarization=None, simple=None):
        self.transcription_result = transcription if transcription is not None else _FakeResult()
        self.diarization_result = diarization
        self.simple_result = simple
        self.transcription_kwargs = None
        self.diarization_kwargs = None
        self.simple_args = None
        self.diarization_ran = False
        self.simple_ran = False

    def transcription(self, audio_path, **kwargs):
        self.transcription_kwargs = dict(kwargs, audio_path=audio_path)
        worker = _FakeWorker(
            self.transcription_result if not isinstance(self.transcription_result, str) else None,
            self.transcription_result if isinstance(self.transcription_result, str) else None,
        )
        worker.bleed_dropped = getattr(self, "bleed_dropped", 0)
        return worker

    def diarization(self, audio_path, result, **kwargs):
        self.diarization_ran = True
        self.diarization_kwargs = dict(kwargs, audio_path=audio_path)
        return _FakeWorker(
            self.diarization_result if not isinstance(self.diarization_result, str) else None,
            self.diarization_result if isinstance(self.diarization_result, str) else None,
        )

    def simple(self, mic, system, result):
        self.simple_ran = True
        self.simple_args = (mic, system)
        return _FakeWorker(
            self.simple_result if not isinstance(self.simple_result, str) else None,
            self.simple_result if isinstance(self.simple_result, str) else None,
        )


class _PipelineCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)

    def job(self, audio_files=None, ops=None):
        from app.batch.worklist import Job
        session = {"directory": str(self.dir), "name": "Sync"}
        if audio_files:
            session["audio_files"] = {}
            for key, name in audio_files.items():
                path = self.dir / name
                path.write_bytes(b"RIFF")
                session["audio_files"][key] = str(path)
        combined = self.dir / "combined_audio.wav"
        combined.write_bytes(b"RIFF")
        return Job(directory=str(self.dir), session=session, label="Sync",
                   audio_path=str(combined), ops=list(ops or ["transcription"]))

    def settings(self, **kwargs):
        from app.batch.pipeline import BatchSettings
        return BatchSettings(**kwargs)

    def transcript(self):
        return json.loads((self.dir / "transcript.json").read_text(encoding="utf-8"))


class TestTranscriptionOnly(_PipelineCase):
    def test_writes_the_transcript(self):
        from app.batch.pipeline import run_job
        outcome = run_job(self.job(), self.settings(), workers=_Recorder())
        self.assertTrue(outcome.ok)
        self.assertEqual(outcome.segments, 2)
        self.assertEqual(self.transcript()["segments"][0]["text"], "hello")

    def test_also_writes_the_markdown_export(self):
        from app.batch.pipeline import run_job
        run_job(self.job(), self.settings(), workers=_Recorder())
        self.assertTrue((self.dir / "transcript.md").exists())

    def test_merges_adjacent_segments_like_the_gui_does(self):
        from app.batch.pipeline import run_job
        result = _FakeResult()
        run_job(self.job(), self.settings(), workers=_Recorder(transcription=result))
        self.assertTrue(result.merged)

    def test_uses_every_core(self):
        from app.batch.pipeline import run_job
        # Nothing is capturing audio, so there is no real-time callback to
        # leave headroom for.
        recorder = _Recorder()
        run_job(self.job(), self.settings(), workers=recorder)
        self.assertTrue(recorder.transcription_kwargs["full_cpu"])

    def test_passes_the_configured_model_and_device(self):
        from app.batch.pipeline import run_job
        recorder = _Recorder()
        run_job(self.job(), self.settings(model_size="large-v3", device="cuda"),
                workers=recorder)
        self.assertEqual(recorder.transcription_kwargs["model_size"], "large-v3")
        self.assertEqual(recorder.transcription_kwargs["device"], "cuda")

    def test_transcription_failure_writes_nothing(self):
        from app.batch.pipeline import run_job
        outcome = run_job(self.job(), self.settings(),
                          workers=_Recorder(transcription="model missing"))
        self.assertFalse(outcome.ok)
        self.assertIn("model missing", outcome.message)
        self.assertFalse((self.dir / "transcript.json").exists())


class TestPerTrack(_PipelineCase):
    def test_splits_the_tracks_when_diarization_is_off(self):
        from app.batch.pipeline import run_job
        recorder = _Recorder()
        job = self.job({"mic": "mic_audio.wav", "system": "system_audio.wav"})
        outcome = run_job(job, self.settings(diarize=False), workers=recorder)
        tracks = recorder.transcription_kwargs["tracks"]
        self.assertEqual([speaker for speaker, _ in tracks], ["You", "Remote"])
        self.assertTrue(outcome.per_track)

    def test_does_not_run_a_diarizer_over_labelled_tracks(self):
        from app.batch.pipeline import run_job
        recorder = _Recorder()
        job = self.job({"mic": "mic_audio.wav", "system": "system_audio.wav"})
        run_job(job, self.settings(diarize=False), workers=recorder)
        self.assertFalse(recorder.simple_ran)
        self.assertFalse(recorder.diarization_ran)

    def test_reports_dropped_bleed(self):
        from app.batch.pipeline import run_job
        recorder = _Recorder()
        recorder.bleed_dropped = 12
        job = self.job({"mic": "mic_audio.wav", "system": "system_audio.wav"})
        outcome = run_job(job, self.settings(diarize=False), workers=recorder)
        self.assertEqual(outcome.bleed_dropped, 12)
        self.assertTrue(any("bleed" in w for w in outcome.warnings))


_DIARIZE_OPS = ["transcription", "diarization"]


class TestFullDiarization(_PipelineCase):
    def test_keeps_the_mix_intact_for_pyannote(self):
        from app.batch.pipeline import run_job
        recorder = _Recorder(diarization=_FakeResult(speaker="SPEAKER_00"))
        job = self.job({"mic": "mic_audio.wav", "system": "system_audio.wav"},
                       ops=_DIARIZE_OPS)
        run_job(job, self.settings(hf_token="t"), workers=recorder)
        # pyannote clusters voices across the whole file — splitting the
        # tracks for it would defeat the point.
        self.assertIsNone(recorder.transcription_kwargs["tracks"])
        self.assertTrue(recorder.diarization_ran)

    def test_saves_the_diarized_result(self):
        from app.batch.pipeline import run_job
        recorder = _Recorder(diarization=_FakeResult(speaker="SPEAKER_00"))
        outcome = run_job(self.job(ops=_DIARIZE_OPS), self.settings(hf_token="t"),
                          workers=recorder)
        self.assertTrue(outcome.diarized)
        self.assertEqual(self.transcript()["segments"][0]["speaker"], "SPEAKER_00")

    def test_diarization_failure_still_saves_the_transcript(self):
        from app.batch.pipeline import run_job
        recorder = _Recorder(diarization="pyannote exploded")
        outcome = run_job(self.job(ops=_DIARIZE_OPS), self.settings(hf_token="t"),
                          workers=recorder)
        self.assertTrue(outcome.ok)
        self.assertFalse(outcome.diarized)
        self.assertIn("pyannote exploded", " ".join(outcome.warnings))
        self.assertEqual(self.transcript()["segments"][0]["text"], "hello")

    def test_passes_the_speaker_bounds(self):
        from app.batch.pipeline import run_job
        recorder = _Recorder(diarization=_FakeResult())
        run_job(self.job(ops=_DIARIZE_OPS),
                self.settings(hf_token="t", min_speakers=2, max_speakers=4),
                workers=recorder)
        self.assertEqual(recorder.diarization_kwargs["min_speakers"], 2)
        self.assertEqual(recorder.diarization_kwargs["max_speakers"], 4)


class TestSimpleFallback(_PipelineCase):
    def test_runs_when_the_tracks_are_named_but_missing(self):
        from app.batch.pipeline import run_job
        recorder = _Recorder(simple=_FakeResult(speaker="You"))
        session_files = {"mic": "mic_audio.wav", "system": "system_audio.wav"}
        job = self.job(session_files)
        # dual_track_plan declines once a named track is gone from disk;
        # the metadata still names both, which is the SimpleDiarizer case.
        (self.dir / "system_audio.wav").unlink()
        outcome = run_job(job, self.settings(diarize=False), workers=recorder)
        self.assertTrue(recorder.simple_ran)
        self.assertTrue(outcome.diarized)

    def test_failure_still_saves_the_transcript(self):
        from app.batch.pipeline import run_job
        recorder = _Recorder(simple="rms comparison blew up")
        job = self.job({"mic": "mic_audio.wav", "system": "system_audio.wav"})
        (self.dir / "system_audio.wav").unlink()
        outcome = run_job(job, self.settings(diarize=False), workers=recorder)
        self.assertTrue(outcome.ok)
        self.assertIn("rms comparison blew up", " ".join(outcome.warnings))


class _FakeProvider:
    """A stand-in AI provider that records the prompts it is handed."""

    max_context_chars = 100_000

    def __init__(self, raises=None, response=None):
        self._raises = raises
        self._response = response
        self.prompts = []

    def complete(self, prompt):
        self.prompts.append(prompt)
        if self._raises is not None:
            raise self._raises
        if self._response is not None:
            return self._response
        return "SUMMARY BODY\n\n## Action Items\n- **Bob:** do the thing"


class _SummaryCase(_PipelineCase):
    def _seed_transcript(self, speaker=None):
        (self.dir / "transcript.json").write_text(json.dumps({
            "segments": [
                {"start": 0.0, "end": 1.0, "text": "hello", "speaker": speaker or ""},
                {"start": 5.0, "end": 6.0, "text": "bye", "speaker": speaker or ""},
            ],
        }), encoding="utf-8")


class TestSummarizationStage(_SummaryCase):
    def test_writes_summary_files_with_batch_provenance(self):
        import app.batch.pipeline as pipeline
        from app.batch.pipeline import run_job
        self._seed_transcript()
        provider = _FakeProvider()
        with unittest.mock.patch("app.ai.provider_factory.create_provider", return_value=provider), \
             unittest.mock.patch("app.ai.provider_factory.describe_ai_model", return_value="fake-model"):
            outcome = run_job(
                self.job(ops=["summarization"]),
                self.settings(ai_config={"provider": "claude", "api_key": "sk-SECRET"}),
                workers=_Recorder(),
            )
        self.assertTrue(outcome.ok)
        self.assertTrue(outcome.summarized)
        summary_text = (self.dir / "summary.md").read_text(encoding="utf-8")
        self.assertEqual(summary_text, "SUMMARY BODY\n\n## Action Items\n- **Bob:** do the thing")
        # Action items ride inside the summary — no separate file.
        self.assertFalse((self.dir / "action_items.json").exists())
        # One prompt, one call.
        self.assertEqual(len(provider.prompts), 1)
        meta = json.loads((self.dir / "summary_meta.json").read_text(encoding="utf-8"))
        self.assertEqual(meta["generated_by"], "talktrack-batch")
        self.assertEqual(meta["model"], "fake-model")
        self.assertIn("SUMMARY BODY", (self.dir / "transcript.md").read_text(encoding="utf-8"))
        # The API key must never reach the outcome the runner logs.
        self.assertNotIn("sk-SECRET", " ".join(outcome.warnings))

    def test_skips_when_a_summary_already_exists(self):
        import app.batch.pipeline as pipeline
        from app.batch.pipeline import run_job
        self._seed_transcript()
        (self.dir / "summary.md").write_text("already summarised", encoding="utf-8")
        called = []
        with unittest.mock.patch.object(
                pipeline, "_run_batch_summary",
                lambda *a, **k: called.append(True) or True):
            outcome = run_job(self.job(ops=["summarization"]), self.settings(),
                              workers=_Recorder())
        self.assertTrue(outcome.summarized)
        self.assertEqual(called, [])

    def test_no_transcript_records_a_warning_not_a_failure(self):
        from app.batch.pipeline import run_job
        outcome = run_job(self.job(ops=["summarization"]), self.settings(),
                          workers=_Recorder())
        self.assertTrue(outcome.ok)
        self.assertFalse(outcome.summarized)
        self.assertIn("no transcript", " ".join(outcome.warnings))

    def test_a_raising_provider_leaves_the_transcript_untouched(self):
        import app.batch.pipeline as pipeline
        from app.batch.pipeline import run_job
        self._seed_transcript()

        def boom(session, settings, progress):
            raise RuntimeError("rate limited")

        with unittest.mock.patch.object(pipeline, "_run_batch_summary", boom):
            outcome = run_job(self.job(ops=["summarization"]), self.settings(),
                              workers=_Recorder())
        self.assertTrue(outcome.ok)
        self.assertFalse(outcome.summarized)
        self.assertIn("rate limited", " ".join(outcome.warnings))
        self.assertFalse((self.dir / "summary.md").exists())

    def test_transcription_then_summarization_runs_both_in_order(self):
        import app.batch.pipeline as pipeline
        from app.batch.pipeline import run_job
        order = []
        recorder = _Recorder()

        def fake_summary(session, settings, progress):
            order.append("summary")
            return True

        with unittest.mock.patch.object(pipeline, "_run_batch_summary", fake_summary):
            outcome = run_job(self.job(ops=["transcription", "summarization"]),
                              self.settings(), workers=recorder)
        self.assertTrue((self.dir / "transcript.json").exists())
        self.assertEqual(order, ["summary"])
        self.assertTrue(outcome.summarized)


class TestSpeakerRecognitionStage(_SummaryCase):
    def test_diarizes_an_existing_unlabelled_transcript(self):
        from app.batch.pipeline import run_job
        self._seed_transcript()
        recorder = _Recorder(diarization=_FakeResult(speaker="SPEAKER_01"))
        job = self.job(ops=["diarization"])
        outcome = run_job(job, self.settings(hf_token="t"), workers=recorder)
        self.assertTrue(outcome.ok)
        self.assertTrue(recorder.diarization_ran)
        self.assertTrue(outcome.diarized)

    def test_skips_when_the_transcript_already_has_speakers(self):
        from app.batch.pipeline import run_job
        self._seed_transcript(speaker="SPEAKER_00")
        # two distinct speakers
        (self.dir / "transcript.json").write_text(json.dumps({
            "segments": [
                {"start": 0.0, "end": 1.0, "text": "hi", "speaker": "SPEAKER_00"},
                {"start": 2.0, "end": 3.0, "text": "yo", "speaker": "SPEAKER_01"},
            ],
        }), encoding="utf-8")
        recorder = _Recorder(diarization=_FakeResult(speaker="X"))
        run_job(self.job(ops=["diarization"]), self.settings(hf_token="t"),
                workers=recorder)
        self.assertFalse(recorder.diarization_ran)

    def test_no_transcript_is_a_failure(self):
        from app.batch.pipeline import run_job
        outcome = run_job(self.job(ops=["diarization"]),
                          self.settings(hf_token="t"), workers=_Recorder())
        self.assertFalse(outcome.ok)


class TestBatchSettings(unittest.TestCase):
    class _Config:
        def __init__(self, values):
            self._values = values

        def get(self, *keys):
            value = self._values
            for key in keys:
                value = value[key]
            return value

    def config(self, enabled=True, token="tok"):
        return self._Config({
            "transcription": {"model_size": "small", "language": None, "device": "cpu"},
            "diarization": {"enabled": enabled, "hf_token": token,
                            "min_speakers": None, "max_speakers": None},
        })

    def test_reads_the_saved_settings(self):
        from app.batch.pipeline import BatchSettings
        settings = BatchSettings.from_config(self.config())
        self.assertEqual(settings.model_size, "small")
        self.assertTrue(settings.diarize)

    def test_no_token_means_no_diarization(self):
        from app.batch.pipeline import BatchSettings
        # A saved enabled=True from a machine that had a token must not
        # queue a job pyannote cannot run.
        self.assertFalse(BatchSettings.from_config(self.config(token="")).diarize)

    def test_explicit_override_wins(self):
        from app.batch.pipeline import BatchSettings
        self.assertFalse(BatchSettings.from_config(self.config(), diarize=False).diarize)
        self.assertTrue(
            BatchSettings.from_config(self.config(enabled=False), diarize=True).diarize)

    def test_override_cannot_conjure_a_token(self):
        from app.batch.pipeline import BatchSettings
        self.assertFalse(
            BatchSettings.from_config(self.config(token=""), diarize=True).diarize)


if __name__ == "__main__":
    unittest.main()
