"""Tests for the pure LLM-transcript-export builder."""
import unittest
from pathlib import Path

from app.utils.transcript_export import (
    sanitize_filename_component,
    export_path_for,
    build_export_markdown,
)


class TestSanitizeFilenameComponent(unittest.TestCase):
    def test_strips_invalid_windows_characters(self):
        self.assertEqual(
            sanitize_filename_component('Q3: Roadmap/Sync? <final>'),
            "Q3_Roadmap_Sync_final",
        )

    def test_collapses_whitespace_to_single_underscores(self):
        self.assertEqual(sanitize_filename_component("a   b\tc\nd"), "a_b_c_d")

    def test_caps_length_at_60_chars(self):
        long_title = "x" * 200
        result = sanitize_filename_component(long_title)
        self.assertEqual(len(result), 60)

    def test_empty_input_returns_untitled(self):
        self.assertEqual(sanitize_filename_component(""), "Untitled")

    def test_whitespace_only_input_returns_untitled(self):
        self.assertEqual(sanitize_filename_component("   "), "Untitled")


class TestExportPathFor(unittest.TestCase):
    def test_builds_timestamped_sanitized_path(self):
        path = export_path_for("Q3 Roadmap Sync", "2026-08-13T14:00:00", Path("C:/transcripts"))
        self.assertEqual(path, Path("C:/transcripts/Q3_Roadmap_Sync_20260813_1400.md"))

    def test_missing_timestamp_still_produces_a_path(self):
        path = export_path_for("Focus Block", "", Path("C:/transcripts"))
        self.assertEqual(path, Path("C:/transcripts/Focus_Block_00000000_0000.md"))


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
        self.assertIn("**[00:00:03] Jane Doe:** Let's get started.", md)
        self.assertIn("**[00:00:12] SPEAKER_01:** Sounds good.", md)

    def test_empty_segments_still_produces_transcript_header(self):
        empty_transcript = {"segments": [], "language": "", "duration": 0}
        md = build_export_markdown(
            self._metadata(), empty_transcript, {}, None, "", None, None
        )
        self.assertIn("# Transcript", md)


if __name__ == "__main__":
    unittest.main()
