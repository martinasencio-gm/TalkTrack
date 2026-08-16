"""Tests for the pure LLM-transcript-export builder."""
import logging
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.utils.transcript_export import (
    build_export_markdown,
    export_transcript,
    has_exportable_content,
)


class TestBuildExportMarkdown(unittest.TestCase):
    def _metadata(self):
        return {
            "directory": "C:/recordings/rec_20260813_140000",
            "started_at": "2026-08-13T14:00:00",
            "duration": 1834,
        }

    def _transcript(self):
        return {
            "segments": [
                {"start": 3.0, "end": 8.0, "text": "Let's get started.", "speaker": "SPEAKER_00"},
                {"start": 12.0, "end": 15.0, "text": "Sounds good.", "speaker": "SPEAKER_01"},
            ],
            "language": "en",
            "duration": 1834,
        }

    def test_frontmatter_includes_core_fields(self):
        md = build_export_markdown(
            self._metadata(), self._transcript(), {}, None, "", None, None
        )
        self.assertIn('title: "rec_20260813_140000"', md)
        self.assertIn("recording_date: \"2026-08-13T14:00:00\"", md)
        self.assertIn("duration_seconds: 1834", md)
        self.assertIn('source_directory: "rec_20260813_140000"', md)

    def test_frontmatter_includes_transcription_provenance(self):
        """model_size is what tells a later reader how much to trust a
        transcript — it has to survive into the corpus."""
        transcript = dict(self._transcript())
        transcript["model_size"] = "small"
        md = build_export_markdown(
            self._metadata(), transcript, {}, None, "", None, None
        )
        self.assertIn('language: "en"', md)
        self.assertIn('model_size: "small"', md)

    def test_provenance_lines_sit_between_duration_and_source_directory(self):
        transcript = dict(self._transcript())
        transcript["model_size"] = "small"
        md = build_export_markdown(
            self._metadata(), transcript, {}, None, "", None, None
        )
        self.assertLess(md.index("duration_seconds:"), md.index("language:"))
        self.assertLess(md.index("language:"), md.index("model_size:"))
        self.assertLess(md.index("model_size:"), md.index("source_directory:"))

    def test_provenance_lines_omitted_when_absent(self):
        transcript = {"segments": [], "duration": 1834}
        md = build_export_markdown(
            self._metadata(), transcript, {}, None, "", None, None
        )
        self.assertNotIn("language:", md)
        self.assertNotIn("model_size:", md)

    def test_empty_provenance_values_are_omitted(self):
        transcript = {"segments": [], "duration": 1834, "language": "", "model_size": None}
        md = build_export_markdown(
            self._metadata(), transcript, {}, None, "", None, None
        )
        self.assertNotIn("language:", md)
        self.assertNotIn("model_size:", md)

    def test_transcribe_seconds_is_not_exported(self):
        """Perf telemetry about the machine that ran the transcription. It
        says nothing about the content, so it stays out of the corpus."""
        transcript = dict(self._transcript())
        transcript["transcribe_seconds"] = 130.43
        md = build_export_markdown(
            self._metadata(), transcript, {}, None, "", None, None
        )
        self.assertNotIn("transcribe_seconds", md)
        self.assertNotIn("130.43", md)

    def test_calendar_block_omitted_when_no_event(self):
        md = build_export_markdown(
            self._metadata(), self._transcript(), {}, None, "", None, None
        )
        self.assertNotIn("calendar:", md)

    def test_calendar_block_present_and_used_as_title_when_tagged(self):
        event = {
            "subject": "Q3 Roadmap Sync",
            "organizer": "jane.doe@example.com",
            "attendees": ["jane.doe@example.com", "john.smith@example.com"],
        }
        md = build_export_markdown(
            self._metadata(), self._transcript(), {}, event, "", None, None
        )
        self.assertIn('title: "Q3 Roadmap Sync"', md)
        self.assertIn("calendar:", md)
        self.assertIn('subject: "Q3 Roadmap Sync"', md)
        self.assertIn('organizer: "jane.doe@example.com"', md)
        self.assertIn("- \"jane.doe@example.com\"", md)
        self.assertIn("- \"john.smith@example.com\"", md)

    def test_calendar_block_tolerates_none_fields(self):
        """None values for subject/organizer/attendee entries must degrade
        to empty strings rather than raising inside _yaml_str."""
        event = {"subject": None, "organizer": None, "attendees": [None]}
        md = build_export_markdown(
            self._metadata(), self._transcript(), {}, event, "", None, None
        )
        self.assertIn("calendar:", md)
        self.assertIn('subject: ""', md)

    def test_speakers_block_tolerates_none_name(self):
        md = build_export_markdown(
            self._metadata(), self._transcript(), {"SPEAKER_00": None}, None, "", None, None
        )
        self.assertIn('SPEAKER_00: ""', md)

    def test_speakers_block_omitted_when_no_names(self):
        md = build_export_markdown(
            self._metadata(), self._transcript(), {}, None, "", None, None
        )
        self.assertNotIn("speakers:", md)

    def test_speakers_block_present_when_names_given(self):
        md = build_export_markdown(
            self._metadata(), self._transcript(), {"SPEAKER_00": "Jane Doe"}, None, "", None, None
        )
        self.assertIn("speakers:", md)
        self.assertIn('SPEAKER_00: "Jane Doe"', md)

    def test_summary_section_omitted_when_none(self):
        md = build_export_markdown(
            self._metadata(), self._transcript(), {}, None, "", None, None
        )
        self.assertNotIn("# Summary", md)

    def test_summary_section_present_when_given(self):
        md = build_export_markdown(
            self._metadata(), self._transcript(), {}, None, "", "The team discussed Q3.", None
        )
        self.assertIn("# Summary", md)
        self.assertIn("The team discussed Q3.", md)

    def test_action_items_section_omitted_when_empty(self):
        md = build_export_markdown(
            self._metadata(), self._transcript(), {}, None, "", None, []
        )
        self.assertNotIn("# Action Items", md)

    def test_action_items_rendered_as_checklist(self):
        items = [
            {"assignee": "Jane", "task": "Send the deck", "due": "2026-08-20"},
            {"task": "Follow up with legal"},
        ]
        md = build_export_markdown(
            self._metadata(), self._transcript(), {}, None, "", None, items
        )
        self.assertIn("# Action Items", md)
        self.assertIn("- [ ] Jane: Send the deck (due 2026-08-20)", md)
        self.assertIn("- [ ] Follow up with legal", md)

    def test_notes_section_omitted_when_blank(self):
        md = build_export_markdown(
            self._metadata(), self._transcript(), {}, None, "", None, None
        )
        self.assertNotIn("# Notes", md)

    def test_notes_section_present_when_given(self):
        md = build_export_markdown(
            self._metadata(), self._transcript(), {}, None, "Follow up on budget.", None, None
        )
        self.assertIn("# Notes", md)
        self.assertIn("Follow up on budget.", md)

    def test_transcript_section_uses_speaker_names_and_timestamps(self):
        md = build_export_markdown(
            self._metadata(), self._transcript(), {"SPEAKER_00": "Jane Doe"}, None, "", None, None
        )
        self.assertIn("# Transcript", md)
        self.assertIn("**[00:00:03–00:00:08] Jane Doe:** Let's get started.", md)
        self.assertIn("**[00:00:12–00:00:15] SPEAKER_01:** Sounds good.", md)

    def test_empty_segments_still_produces_transcript_header(self):
        empty_transcript = {"segments": [], "language": "", "duration": 0}
        md = build_export_markdown(
            self._metadata(), empty_transcript, {}, None, "", None, None
        )
        self.assertIn("# Transcript", md)


class TestSegmentRendering(unittest.TestCase):
    """Segment lines must carry end time and confidence so the export stands
    on its own once the audio is deleted — while degrading cleanly for
    transcripts produced before those fields were exported."""

    def _metadata(self):
        return {
            "directory": "C:/recordings/rec_20260813_140000",
            "started_at": "2026-08-13T14:00:00",
            "duration": 1834,
        }

    def _render(self, segment):
        transcript = {"segments": [segment], "duration": 1834}
        return build_export_markdown(
            self._metadata(), transcript, {"SPEAKER_00": "Alice"},
            None, "", None, None,
        )

    def test_full_form_with_end_speaker_and_confidence(self):
        md = self._render({
            "start": 1.62, "end": 9.02, "text": "Hello there.",
            "speaker": "SPEAKER_00", "confidence": 0.7590766239383613,
        })
        self.assertIn("**[00:00:01–00:00:09] Alice (0.76):** Hello there.", md)

    def test_confidence_omitted_when_absent(self):
        md = self._render({
            "start": 1.62, "end": 9.02, "text": "Hello there.",
            "speaker": "SPEAKER_00",
        })
        self.assertIn("**[00:00:01–00:00:09] Alice:** Hello there.", md)

    def test_no_colon_when_there_is_no_speaker(self):
        """The colon reads as '<speaker> said:' — without a name it is noise."""
        md = self._render({
            "start": 1.62, "end": 9.02, "text": "Hello there.",
            "confidence": 0.76,
        })
        self.assertIn("**[00:00:01–00:00:09] (0.76)** Hello there.", md)

    def test_timestamp_only_when_neither_speaker_nor_confidence(self):
        md = self._render({"start": 1.62, "end": 9.02, "text": "Hello there."})
        self.assertIn("**[00:00:01–00:00:09]** Hello there.", md)

    def test_missing_end_falls_back_to_single_timestamp(self):
        md = self._render({
            "start": 1.62, "text": "Hello there.", "speaker": "SPEAKER_00",
        })
        self.assertIn("**[00:00:01] Alice:** Hello there.", md)

    def test_none_end_falls_back_to_single_timestamp(self):
        md = self._render({
            "start": 1.62, "end": None, "text": "Hello there.",
            "speaker": "SPEAKER_00",
        })
        self.assertIn("**[00:00:01] Alice:** Hello there.", md)

    def test_non_numeric_end_falls_back_to_single_timestamp(self):
        md = self._render({
            "start": 1.62, "end": "9.02", "text": "Hello there.",
            "speaker": "SPEAKER_00",
        })
        self.assertIn("**[00:00:01] Alice:** Hello there.", md)

    def test_non_advancing_end_falls_back_to_single_timestamp(self):
        """A backwards or zero-length range would be worse than no range."""
        md = self._render({
            "start": 9.02, "end": 1.62, "text": "Hello there.",
            "speaker": "SPEAKER_00",
        })
        self.assertIn("**[00:00:09] Alice:** Hello there.", md)
        self.assertNotIn("–", md)

    def test_equal_end_falls_back_to_single_timestamp(self):
        md = self._render({
            "start": 9.02, "end": 9.02, "text": "Hello there.",
            "speaker": "SPEAKER_00",
        })
        self.assertIn("**[00:00:09] Alice:** Hello there.", md)

    def test_none_confidence_is_omitted(self):
        md = self._render({
            "start": 1.62, "end": 9.02, "text": "Hello there.",
            "speaker": "SPEAKER_00", "confidence": None,
        })
        self.assertIn("**[00:00:01–00:00:09] Alice:** Hello there.", md)

    def test_non_numeric_confidence_is_omitted(self):
        md = self._render({
            "start": 1.62, "end": 9.02, "text": "Hello there.",
            "speaker": "SPEAKER_00", "confidence": "high",
        })
        self.assertIn("**[00:00:01–00:00:09] Alice:** Hello there.", md)

    def test_boolean_confidence_is_omitted(self):
        """bool is an int subclass in Python — a stray True must not render
        as '(1.00)'."""
        md = self._render({
            "start": 1.62, "end": 9.02, "text": "Hello there.",
            "speaker": "SPEAKER_00", "confidence": True,
        })
        self.assertIn("**[00:00:01–00:00:09] Alice:** Hello there.", md)

    def test_confidence_rendered_to_two_decimals(self):
        md = self._render({
            "start": 1.62, "end": 9.02, "text": "Hello there.",
            "speaker": "SPEAKER_00", "confidence": 0.7590766239383613,
        })
        self.assertIn("(0.76)", md)
        self.assertNotIn("0.7590766", md)

    def test_missing_start_is_treated_as_zero(self):
        md = self._render({"end": 9.02, "text": "Hello there.", "speaker": "SPEAKER_00"})
        self.assertIn("**[00:00:00–00:00:09] Alice:** Hello there.", md)

    def test_none_start_does_not_raise(self):
        """A None start previously reached int(None) inside _format_time.
        The new comparison against end must not make that worse."""
        md = self._render({
            "start": None, "end": 9.02, "text": "Hello there.",
            "speaker": "SPEAKER_00",
        })
        self.assertIn("**[00:00:00–00:00:09] Alice:** Hello there.", md)


class TestExportTranscript(unittest.TestCase):
    """Tests for export_transcript() function."""

    def _metadata(self, directory):
        return {
            "directory": directory,
            "name": "Test Recording",
            "started_at": "2026-08-13T14:00:00",
            "duration": 600,
        }

    def _transcript(self):
        return {
            "segments": [
                {"start": 3.0, "end": 8.0, "text": "Test segment one.", "speaker": "SPEAKER_00"},
                {"start": 12.0, "end": 15.0, "text": "Test segment two.", "speaker": "SPEAKER_01"},
            ],
            "language": "en",
            "duration": 600,
        }

    def test_export_transcript_happy_path(self):
        """Happy path: valid data creates transcript.md, next to where
        transcript.json lives, with expected content."""
        with tempfile.TemporaryDirectory() as tmpdir:
            metadata = self._metadata(tmpdir)
            transcript = self._transcript()
            speaker_names = {"SPEAKER_00": "Alice", "SPEAKER_01": "Bob"}
            calendar_event = {
                "subject": "Team Sync",
                "organizer": "alice@example.com",
                "attendees": ["alice@example.com", "bob@example.com"],
            }
            notes = "Important discussion about Q3 plans."
            summary_markdown = "The team discussed Q3 plans and timelines."
            action_items = [
                {"assignee": "Alice", "task": "Send deck", "due": "2026-08-20"},
                {"task": "Review budget"},
            ]

            export_transcript(
                metadata,
                transcript,
                speaker_names,
                calendar_event,
                notes,
                summary_markdown,
                action_items,
            )

            export_path = Path(tmpdir) / "transcript.md"
            self.assertTrue(export_path.exists())

            # Explicit encoding: atomic_write_text writes UTF-8, but
            # read_text() defaults to the locale encoding (cp1252 on
            # Windows), which mangles the en-dash in segment time ranges.
            content = export_path.read_text(encoding="utf-8")
            self.assertIn('title: "Team Sync"', content)
            self.assertIn("recording_date: \"2026-08-13T14:00:00\"", content)
            self.assertIn("duration_seconds: 600", content)
            self.assertIn('subject: "Team Sync"', content)
            self.assertIn("SPEAKER_00: \"Alice\"", content)
            self.assertIn("# Summary", content)
            self.assertIn("The team discussed Q3 plans and timelines.", content)
            self.assertIn("# Action Items", content)
            self.assertIn("- [ ] Alice: Send deck (due 2026-08-20)", content)
            self.assertIn("- [ ] Review budget", content)
            self.assertIn("# Notes", content)
            self.assertIn("Important discussion about Q3 plans.", content)
            self.assertIn("# Transcript", content)
            self.assertIn("**[00:00:03–00:00:08] Alice:** Test segment one.", content)

    def test_reexport_overwrites_the_same_file(self):
        """A rename / calendar tag / calendar remap changes the title but
        the export always lives at <directory>/transcript.md — re-exporting
        overwrites it in place rather than orphaning anything."""
        with tempfile.TemporaryDirectory() as tmpdir:
            metadata = self._metadata(tmpdir)
            transcript = self._transcript()

            export_transcript(metadata, transcript, {}, None, "", None, None)
            export_path = Path(tmpdir) / "transcript.md"
            self.assertTrue(export_path.exists())

            # Same recording (same "directory"), but the title-driving
            # inputs changed — e.g. tagged to a calendar event.
            renamed_metadata = dict(metadata)
            renamed_metadata["name"] = "Totally Different Title"
            calendar_event = {"subject": "Yet Another Title"}

            export_transcript(renamed_metadata, transcript, {}, calendar_event, "", None, None)

            self.assertEqual(list(Path(tmpdir).glob("*.md")), [export_path])
            self.assertIn("Yet Another Title", export_path.read_text(encoding="utf-8"))

    def test_export_transcript_write_failure_does_not_propagate(self):
        """Write failure (OSError) is caught and logged, does not raise."""
        with tempfile.TemporaryDirectory() as tmpdir:
            metadata = self._metadata(tmpdir)
            transcript = self._transcript()

            # Mock atomic_write_text to raise OSError
            with patch("app.utils.transcript_export.atomic_write_text") as mock_write:
                mock_write.side_effect = OSError("Disk full")

                # This should not raise; it should catch and log the error
                try:
                    export_transcript(metadata, transcript, {}, None, "", None, None)
                except OSError:
                    self.fail("export_transcript() raised OSError; should have caught it")

    def test_export_transcript_malformed_calendar_event(self):
        """Malformed calendar_event (subject/organizer/attendee=None) degrades
        gracefully to an empty string instead of raising and silently
        skipping the whole export (which would leave a stale previous
        export in place)."""
        with tempfile.TemporaryDirectory() as tmpdir:
            metadata = self._metadata(tmpdir)
            transcript = self._transcript()
            calendar_event = {
                "subject": None,
                "organizer": None,
                "attendees": [None, "jane@example.com"],
            }

            try:
                export_transcript(metadata, transcript, {}, calendar_event, "", None, None)
            except (TypeError, AttributeError):
                self.fail(
                    "export_transcript() raised exception on malformed calendar_event; "
                    "should have caught it"
                )

            # The export must actually be written, not silently dropped.
            self.assertTrue((Path(tmpdir) / "transcript.md").exists())

    def test_export_transcript_malformed_action_items(self):
        """Malformed action items (assignee/task/due=None) do not propagate."""
        with tempfile.TemporaryDirectory() as tmpdir:
            metadata = self._metadata(tmpdir)
            transcript = self._transcript()
            action_items = [
                {"assignee": None, "task": None, "due": None},
                {"assignee": "Alice", "task": None},
            ]

            # This should not raise; malformed items are caught
            try:
                export_transcript(metadata, transcript, {}, None, "", None, action_items)
            except (TypeError, AttributeError):
                self.fail(
                    "export_transcript() raised exception on malformed action_items; "
                    "should have caught it"
                )

    def test_export_transcript_missing_metadata_directory_is_a_noop(self):
        """No metadata['directory'] means there is no session folder to
        write into — must not raise, and must not write anywhere (e.g. the
        process cwd)."""
        metadata = {"started_at": "2026-08-13T14:00:00", "duration": 600}
        # Deliberately omit "directory" key
        transcript = self._transcript()

        try:
            export_transcript(metadata, transcript, {}, None, "", None, None)
        except KeyError:
            self.fail(
                "export_transcript() raised KeyError on missing metadata; "
                "should have caught it"
            )
        self.assertFalse(Path("transcript.md").exists())


class TestHasExportableContent(unittest.TestCase):
    def test_false_for_no_segments(self):
        self.assertFalse(has_exportable_content({"segments": []}))

    def test_false_for_missing_segments_key(self):
        self.assertFalse(has_exportable_content({"language": "en"}))

    def test_false_for_none(self):
        self.assertFalse(has_exportable_content(None))

    def test_true_for_one_segment(self):
        self.assertTrue(has_exportable_content({"segments": [{"text": "hi"}]}))


class TestExportSkipsEmptyTranscripts(unittest.TestCase):
    """A recording that transcribed to nothing produced an export of
    frontmatter plus an empty '# Transcript' heading — files that only
    dilute the corpus."""

    def _metadata(self, directory):
        return {
            "directory": directory,
            "started_at": "2026-08-13T14:00:00",
            "duration": 600,
        }

    def test_empty_transcript_writes_no_file(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            export_transcript(
                self._metadata(tmpdir), {"segments": [], "language": "en"},
                {}, None, "", None, None,
            )
            self.assertFalse((Path(tmpdir) / "transcript.md").exists())

    def test_skip_leaves_an_existing_export_untouched(self):
        """Skipping suppresses a write; it must never delete. Removing stale
        exports is the user's call, and the delete-recording flow already
        offers it."""
        with tempfile.TemporaryDirectory() as tmpdir:
            metadata = self._metadata(tmpdir)
            transcript = {
                "segments": [{"start": 3.0, "end": 8.0, "text": "Real content."}],
                "language": "en",
            }
            export_transcript(metadata, transcript, {}, None, "", None, None)
            export_path = Path(tmpdir) / "transcript.md"
            self.assertTrue(export_path.exists())
            before = export_path.read_bytes()

            export_transcript(metadata, {"segments": []}, {}, None, "", None, None)

            self.assertEqual(export_path.read_bytes(), before)

    def test_non_empty_transcript_still_exports(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            transcript = {
                "segments": [{"start": 3.0, "end": 8.0, "text": "Real content."}],
                "language": "en",
            }
            export_transcript(self._metadata(tmpdir), transcript, {}, None, "", None, None)
            self.assertTrue((Path(tmpdir) / "transcript.md").exists())

    def test_malformed_transcript_data_does_not_raise(self):
        """A list where a dict was expected reaches .get() — the existing
        best-effort handler must absorb it, as it does every other
        malformed input."""
        with tempfile.TemporaryDirectory() as tmpdir:
            try:
                export_transcript(self._metadata(tmpdir), [], {}, None, "", None, None)
            except AttributeError:
                self.fail("export_transcript() raised on malformed transcript_data")


if __name__ == "__main__":
    unittest.main()
