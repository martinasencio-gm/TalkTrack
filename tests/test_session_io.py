"""Tests for the disk-driven session readers/writers shared by the app and
the headless batch runner."""
import json
import tempfile
import unittest
from pathlib import Path


class _FakeResult:
    """Stands in for TranscriptResult without importing PyQt6."""

    def __init__(self, segments):
        self.segments = segments

    def to_dict(self, speaker_names=None):
        return {"segments": self.segments, "names_seen": speaker_names or {}}

    def to_text(self, speaker_names=None):
        return "\n".join(s["text"] for s in self.segments)


def _segments():
    return [{"start": 0.0, "end": 1.0, "text": "hello", "speaker": "You"}]


class _SessionCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)
        self.session = {"directory": str(self.dir), "name": "Sync",
                        "started_at": "2026-08-17T16:01:50", "duration": 60.0}


class TestLoadSpeakerNames(_SessionCase):
    def test_missing_file_is_an_empty_map(self):
        from app.utils.session_io import load_speaker_names
        self.assertEqual(load_speaker_names(self.session), {})

    def test_reads_the_map(self):
        from app.utils.session_io import load_speaker_names
        (self.dir / "speaker_names.json").write_text('{"You": "Martin"}', encoding="utf-8")
        self.assertEqual(load_speaker_names(self.session), {"You": "Martin"})

    def test_corrupt_file_is_an_empty_map(self):
        from app.utils.session_io import load_speaker_names
        (self.dir / "speaker_names.json").write_text("{ truncated", encoding="utf-8")
        self.assertEqual(load_speaker_names(self.session), {})

    def test_no_session_is_an_empty_map(self):
        from app.utils.session_io import load_speaker_names
        self.assertEqual(load_speaker_names(None), {})
        self.assertEqual(load_speaker_names({}), {})


class TestLoadCalendarEvent(_SessionCase):
    def test_missing_file(self):
        from app.utils.session_io import load_calendar_event
        self.assertEqual(load_calendar_event(self.session), (None, []))

    def test_reads_event_and_attendees(self):
        from app.utils.session_io import load_calendar_event
        (self.dir / "calendar_event.json").write_text(
            json.dumps({"subject": "Sync", "attendees": ["Ana", "Bo"]}), encoding="utf-8")
        event, attendees = load_calendar_event(self.session)
        self.assertEqual(event["subject"], "Sync")
        self.assertEqual(attendees, ["Ana", "Bo"])

    def test_event_without_attendees(self):
        from app.utils.session_io import load_calendar_event
        (self.dir / "calendar_event.json").write_text(
            json.dumps({"subject": "Sync"}), encoding="utf-8")
        self.assertEqual(load_calendar_event(self.session)[1], [])

    def test_corrupt_file(self):
        from app.utils.session_io import load_calendar_event
        (self.dir / "calendar_event.json").write_text("nope", encoding="utf-8")
        self.assertEqual(load_calendar_event(self.session), (None, []))


class TestWriteTranscript(_SessionCase):
    def test_writes_json_and_text(self):
        from app.utils.session_io import write_transcript
        self.assertTrue(write_transcript(self.session, _FakeResult(_segments())))
        data = json.loads((self.dir / "transcript.json").read_text(encoding="utf-8"))
        self.assertEqual(data["segments"][0]["text"], "hello")
        self.assertEqual((self.dir / "transcript.txt").read_text(encoding="utf-8"), "hello")

    def test_uses_the_saved_speaker_names_by_default(self):
        from app.utils.session_io import write_transcript
        (self.dir / "speaker_names.json").write_text('{"You": "Martin"}', encoding="utf-8")
        write_transcript(self.session, _FakeResult(_segments()))
        data = json.loads((self.dir / "transcript.json").read_text(encoding="utf-8"))
        self.assertEqual(data["names_seen"], {"You": "Martin"})

    def test_explicit_names_win_over_the_saved_ones(self):
        from app.utils.session_io import write_transcript
        (self.dir / "speaker_names.json").write_text('{"You": "Stale"}', encoding="utf-8")
        write_transcript(self.session, _FakeResult(_segments()), speaker_names={"You": "Fresh"})
        data = json.loads((self.dir / "transcript.json").read_text(encoding="utf-8"))
        self.assertEqual(data["names_seen"], {"You": "Fresh"})

    def test_also_writes_the_markdown_export(self):
        from app.utils.session_io import write_transcript
        write_transcript(self.session, _FakeResult(_segments()))
        self.assertTrue((self.dir / "transcript.md").exists())

    def test_no_directory_is_a_no_op(self):
        from app.utils.session_io import write_transcript
        self.assertFalse(write_transcript({}, _FakeResult(_segments())))
        self.assertFalse(write_transcript(None, _FakeResult(_segments())))


class TestLoadTranscript(_SessionCase):
    def test_missing_file_is_none(self):
        from app.utils.session_io import load_transcript
        self.assertIsNone(load_transcript(self.session))

    def test_corrupt_file_is_none(self):
        from app.utils.session_io import load_transcript
        (self.dir / "transcript.json").write_text("{ truncated", encoding="utf-8")
        self.assertIsNone(load_transcript(self.session))

    def test_no_directory_is_none(self):
        from app.utils.session_io import load_transcript
        self.assertIsNone(load_transcript({}))
        self.assertIsNone(load_transcript(None))

    def test_round_trips_write_transcript(self):
        from app.transcription.transcriber import TranscriptResult, TranscriptSegment
        from app.utils.session_io import load_transcript, write_transcript
        original = TranscriptResult(
            segments=[TranscriptSegment(start=0.0, end=1.5, text="hello", speaker="You"),
                      TranscriptSegment(start=2.0, end=3.0, text="hi there", speaker="Remote")],
            language="en", duration=3.0, model_size="base",
        )
        write_transcript(self.session, original)
        loaded = load_transcript(self.session)
        self.assertEqual([s.text for s in loaded.segments], ["hello", "hi there"])
        self.assertEqual([s.speaker for s in loaded.segments], ["You", "Remote"])
        self.assertEqual(loaded.language, "en")
        self.assertEqual(loaded.duration, 3.0)


class TestWriteSummary(_SessionCase):
    def test_writes_the_three_files_and_refreshes_the_markdown(self):
        from app.utils.session_io import write_summary
        (self.dir / "transcript.json").write_text(
            json.dumps({"segments": _segments()}), encoding="utf-8")
        meta = {"generated_by": "talktrack-batch", "model": "fake",
                "seconds": 1.2, "generated_at": "2026-08-27T10:00:00"}
        actions = [{"task": "do it", "assignee": "Ana", "deadline": ""}]
        self.assertTrue(write_summary(self.session, "The summary.", actions, meta))
        self.assertEqual((self.dir / "summary.md").read_text(encoding="utf-8"), "The summary.")
        self.assertEqual(
            json.loads((self.dir / "action_items.json").read_text(encoding="utf-8")),
            actions)
        self.assertEqual(
            json.loads((self.dir / "summary_meta.json").read_text(encoding="utf-8"))["generated_by"],
            "talktrack-batch")
        self.assertIn("The summary.", (self.dir / "transcript.md").read_text(encoding="utf-8"))

    def test_no_directory_is_a_no_op(self):
        from app.utils.session_io import write_summary
        self.assertFalse(write_summary({}, "x", [], {}))
        self.assertFalse(write_summary(None, "x", [], {}))


class TestExportSessionMarkdown(_SessionCase):
    def test_does_nothing_without_a_transcript(self):
        from app.utils.session_io import export_session_markdown
        export_session_markdown(self.session)
        self.assertFalse((self.dir / "transcript.md").exists())

    def test_includes_the_summary_and_notes(self):
        from app.utils.session_io import export_session_markdown
        (self.dir / "transcript.json").write_text(
            json.dumps({"segments": _segments()}), encoding="utf-8")
        (self.dir / "summary.md").write_text("Talked about the roadmap.", encoding="utf-8")
        (self.dir / "notes.txt").write_text("remember to follow up", encoding="utf-8")
        export_session_markdown(self.session)
        markdown = (self.dir / "transcript.md").read_text(encoding="utf-8")
        self.assertIn("Talked about the roadmap.", markdown)
        self.assertIn("remember to follow up", markdown)

    def test_a_corrupt_companion_file_does_not_stop_the_export(self):
        from app.utils.session_io import export_session_markdown
        (self.dir / "transcript.json").write_text(
            json.dumps({"segments": _segments()}), encoding="utf-8")
        (self.dir / "action_items.json").write_text("{ truncated", encoding="utf-8")
        export_session_markdown(self.session)
        self.assertIn("hello", (self.dir / "transcript.md").read_text(encoding="utf-8"))

    def test_corrupt_transcript_is_skipped(self):
        from app.utils.session_io import export_session_markdown
        (self.dir / "transcript.json").write_text("{ truncated", encoding="utf-8")
        export_session_markdown(self.session)
        self.assertFalse((self.dir / "transcript.md").exists())


if __name__ == "__main__":
    unittest.main()
