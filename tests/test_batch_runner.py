"""Tests for the batch run loop: cutoff handling, tag bookkeeping, exit codes."""
import json
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest import mock


def _job(directory, label="Sync"):
    from app.batch.worklist import Job
    return Job(directory=str(directory), session={"directory": str(directory)},
               label=label, audio_path=str(Path(directory) / "combined_audio.wav"))


def _outcome(ok=True, message="transcribed", **kwargs):
    from app.batch.pipeline import JobOutcome
    return JobOutcome(ok, message, **kwargs)


class _ProcessCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)

    def recording(self, name, metadata=None):
        directory = self.root / name
        directory.mkdir()
        (directory / "metadata.json").write_text(
            json.dumps(metadata or {"batch_pending": True}), encoding="utf-8")
        return directory

    def metadata(self, directory):
        return json.loads((Path(directory) / "metadata.json").read_text(encoding="utf-8"))

    def far_future(self):
        return datetime.now() + timedelta(days=1)


class TestSuccessPath(_ProcessCase):
    def test_clears_the_tag_after_success(self):
        from app.batch.runner import _process
        directory = self.recording("recording_20260101_000000")
        with mock.patch("app.batch.runner.run_job", return_value=_outcome(segments=3)):
            code = _process([_job(directory)], None, self.far_future())
        self.assertEqual(code, 0)
        self.assertNotIn("batch_pending", self.metadata(directory))

    def test_processes_every_job(self):
        from app.batch.runner import _process
        dirs = [self.recording(f"recording_2026010{i}_000000") for i in (1, 2, 3)]
        with mock.patch("app.batch.runner.run_job",
                        return_value=_outcome()) as run_job:
            _process([_job(d) for d in dirs], None, self.far_future())
        self.assertEqual(run_job.call_count, 3)


class TestFailurePath(_ProcessCase):
    def test_counts_the_failure_and_keeps_the_tag(self):
        from app.batch.runner import _process
        directory = self.recording("recording_20260101_000000")
        with mock.patch("app.batch.runner.run_job",
                        return_value=_outcome(False, "model missing")):
            code = _process([_job(directory)], None, self.far_future())
        self.assertEqual(code, 2)
        meta = self.metadata(directory)
        self.assertTrue(meta["batch_pending"])
        self.assertEqual(meta["batch_attempts"], 1)

    def test_a_failure_does_not_abort_the_rest_of_the_run(self):
        from app.batch.runner import _process
        bad = self.recording("recording_20260101_000000")
        good = self.recording("recording_20260102_000000")
        outcomes = [_outcome(False, "boom"), _outcome()]
        with mock.patch("app.batch.runner.run_job", side_effect=outcomes):
            code = _process([_job(bad), _job(good)], None, self.far_future())
        self.assertEqual(code, 2)
        self.assertNotIn("batch_pending", self.metadata(good))

    def test_an_unhandled_exception_is_contained(self):
        from app.batch.runner import _process
        bad = self.recording("recording_20260101_000000")
        good = self.recording("recording_20260102_000000")
        with mock.patch("app.batch.runner.run_job",
                        side_effect=[RuntimeError("segfault-ish"), _outcome()]):
            code = _process([_job(bad), _job(good)], None, self.far_future())
        self.assertEqual(code, 2)
        self.assertEqual(self.metadata(bad)["batch_attempts"], 1)
        self.assertNotIn("batch_pending", self.metadata(good))


class TestCutoff(_ProcessCase):
    def test_stops_starting_new_recordings_past_the_cutoff(self):
        from app.batch.runner import _process
        dirs = [self.recording(f"recording_2026010{i}_000000") for i in (1, 2)]
        past = datetime.now() - timedelta(minutes=1)
        with mock.patch("app.batch.runner.run_job") as run_job:
            code = _process([_job(d) for d in dirs], None, past)
        run_job.assert_not_called()
        self.assertEqual(code, 0)

    def test_deferred_recordings_stay_queued(self):
        from app.batch.runner import _process
        directory = self.recording("recording_20260101_000000")
        past = datetime.now() - timedelta(minutes=1)
        with mock.patch("app.batch.runner.run_job"):
            _process([_job(directory)], None, past)
        # Still queued, and with no attempt counted against it — it never
        # got a chance to fail.
        meta = self.metadata(directory)
        self.assertTrue(meta["batch_pending"])
        self.assertEqual(meta.get("batch_attempts", 0), 0)


class TestParser(unittest.TestCase):
    def test_until_is_required(self):
        from app.batch.runner import build_parser
        with self.assertRaises(SystemExit):
            build_parser().parse_args([])

    def test_diarize_defaults_to_the_saved_setting(self):
        from app.batch.runner import build_parser
        self.assertIsNone(build_parser().parse_args(["--until", "07:00"]).diarize)

    def test_diarize_flags(self):
        from app.batch.runner import build_parser
        parser = build_parser()
        self.assertTrue(parser.parse_args(["--until", "07:00", "--diarize"]).diarize)
        self.assertFalse(parser.parse_args(["--until", "07:00", "--no-diarize"]).diarize)

    def test_summarize_defaults_to_none(self):
        from app.batch.runner import build_parser
        self.assertIsNone(build_parser().parse_args(["--until", "07:00"]).summarize)

    def test_summarize_flags(self):
        from app.batch.runner import build_parser
        parser = build_parser()
        self.assertTrue(parser.parse_args(["--until", "07:00", "--summarize"]).summarize)
        self.assertFalse(parser.parse_args(["--until", "07:00", "--no-summarize"]).summarize)


class TestOpOverrides(unittest.TestCase):
    def _args(self, diarize=None, summarize=None):
        from argparse import Namespace
        return Namespace(diarize=diarize, summarize=summarize)

    def _settings(self, hf_token="tok", provider="claude"):
        from app.batch.pipeline import BatchSettings
        return BatchSettings(hf_token=hf_token,
                             ai_config={"provider": provider} if provider else {})

    def _jobs(self, *op_lists):
        from app.batch.worklist import Job
        return [Job(directory=f"/d{i}", session={}, label=f"j{i}",
                    audio_path=None, ops=list(ops))
                for i, ops in enumerate(op_lists)]

    def test_summarize_adds_the_op(self):
        from app.batch.runner import _apply_op_overrides
        jobs = self._jobs(["transcription"])
        _apply_op_overrides(jobs, self._args(summarize=True), self._settings())
        self.assertEqual(jobs[0].ops, ["transcription", "summarization"])

    def test_no_summarize_removes_the_op(self):
        from app.batch.runner import _apply_op_overrides
        jobs = self._jobs(["transcription", "summarization"])
        _apply_op_overrides(jobs, self._args(summarize=False), self._settings())
        self.assertEqual(jobs[0].ops, ["transcription"])

    def test_summarize_is_a_noop_without_a_provider(self):
        from app.batch.runner import _apply_op_overrides
        jobs = self._jobs(["transcription"])
        _apply_op_overrides(jobs, self._args(summarize=True),
                            self._settings(provider="none"))
        self.assertEqual(jobs[0].ops, ["transcription"])

    def test_diarize_is_a_noop_without_a_token(self):
        from app.batch.runner import _apply_op_overrides
        jobs = self._jobs(["transcription"])
        _apply_op_overrides(jobs, self._args(diarize=True),
                            self._settings(hf_token=""))
        self.assertEqual(jobs[0].ops, ["transcription"])

    def test_a_job_emptied_by_an_override_is_dropped(self):
        from app.batch.runner import _apply_op_overrides
        jobs = self._jobs(["summarization"])
        kept = _apply_op_overrides(jobs, self._args(summarize=False), self._settings())
        self.assertEqual(kept, [])


class TestDescribe(unittest.TestCase):
    def test_mentions_the_summary_when_one_was_written(self):
        from app.batch.runner import _describe
        from app.batch.pipeline import JobOutcome
        text = _describe(JobOutcome(True, "ok", segments=4, summarized=True, elapsed=3))
        self.assertIn("summary written", text)

    def test_no_summary_mention_otherwise(self):
        from app.batch.runner import _describe
        from app.batch.pipeline import JobOutcome
        self.assertNotIn("summary", _describe(JobOutcome(True, "ok", segments=4, elapsed=3)))


class TestLogPruning(unittest.TestCase):
    def test_keeps_only_the_newest_runs(self):
        from app.batch.logging_setup import prune_old_logs
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            for i in range(1, 6):
                (directory / f"batch_2026010{i}_000000.log").write_text("x", encoding="utf-8")
            prune_old_logs(directory, keep=2)
            self.assertEqual(
                sorted(p.name for p in directory.glob("*.log")),
                ["batch_20260104_000000.log", "batch_20260105_000000.log"],
            )

    def test_leaves_other_files_alone(self):
        from app.batch.logging_setup import prune_old_logs
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            (directory / "notes.txt").write_text("x", encoding="utf-8")
            prune_old_logs(directory, keep=0)
            self.assertTrue((directory / "notes.txt").exists())


if __name__ == "__main__":
    unittest.main()
