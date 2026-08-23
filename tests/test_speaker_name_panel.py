"""Tests for SpeakerNamePanel logic."""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import sys
import unittest

from PyQt6.QtWidgets import QApplication

_app = None


def _get_app():
    global _app
    if _app is None:
        _app = QApplication.instance() or QApplication(sys.argv)
    return _app


class TestSpeakerNamePanelLogic(unittest.TestCase):

    def test_build_speaker_list_from_segments(self):
        """Extract unique sorted speakers from segments."""
        from app.ui.speaker_name_panel import _extract_speakers
        from app.transcription.transcriber import TranscriptSegment
        segments = [
            TranscriptSegment(start=0, end=1, text="a", speaker="SPEAKER_01"),
            TranscriptSegment(start=1, end=2, text="b", speaker="SPEAKER_00"),
            TranscriptSegment(start=2, end=3, text="c", speaker="SPEAKER_01"),
            TranscriptSegment(start=3, end=4, text="d", speaker=""),
        ]
        speakers = _extract_speakers(segments)
        self.assertEqual(speakers, ["SPEAKER_00", "SPEAKER_01"])

    def test_build_speaker_list_empty_segments(self):
        from app.ui.speaker_name_panel import _extract_speakers
        self.assertEqual(_extract_speakers([]), [])


class TestAvailableOptions(unittest.TestCase):

    def test_no_selections_yet_returns_all_attendees_plus_blank(self):
        from app.ui.speaker_name_panel import _available_options
        opts = _available_options(
            "SPEAKER_00",
            ["SPEAKER_00", "SPEAKER_01"],
            {},
            ["Jane", "John"],
        )
        self.assertEqual(opts, ["", "Jane", "John"])

    def test_excludes_names_assigned_to_other_speakers(self):
        from app.ui.speaker_name_panel import _available_options
        opts = _available_options(
            "SPEAKER_01",
            ["SPEAKER_00", "SPEAKER_01"],
            {"SPEAKER_00": "Jane"},
            ["Jane", "John"],
        )
        self.assertEqual(opts, ["", "John"])

    def test_keeps_own_current_selection_available(self):
        from app.ui.speaker_name_panel import _available_options
        opts = _available_options(
            "SPEAKER_00",
            ["SPEAKER_00", "SPEAKER_01"],
            {"SPEAKER_00": "Jane", "SPEAKER_01": "John"},
            ["Jane", "John"],
        )
        self.assertEqual(opts, ["", "Jane"])

    def test_custom_typed_name_not_in_attendees_does_not_appear_in_list(self):
        # A name typed freely (not an attendee) shouldn't show up as a
        # dropdown option for other speakers to "steal" — it's just absent
        # from the attendee-derived list entirely.
        from app.ui.speaker_name_panel import _available_options
        opts = _available_options(
            "SPEAKER_01",
            ["SPEAKER_00", "SPEAKER_01"],
            {"SPEAKER_00": "Some Guest"},
            ["Jane", "John"],
        )
        self.assertEqual(opts, ["", "Jane", "John"])


class TestSpeakersHoldingName(unittest.TestCase):

    def test_no_match_returns_empty_list(self):
        from app.ui.speaker_name_panel import _speakers_holding_name
        result = _speakers_holding_name(
            "Jane",
            {"SPEAKER_00": "John"},
            "SPEAKER_01",
        )
        self.assertEqual(result, [])

    def test_one_match_excludes_asking_speaker(self):
        from app.ui.speaker_name_panel import _speakers_holding_name
        result = _speakers_holding_name(
            "Jane",
            {"SPEAKER_00": "Jane", "SPEAKER_01": "Jane"},
            "SPEAKER_01",
        )
        self.assertEqual(result, ["SPEAKER_00"])

    def test_multiple_speakers_holding_same_name(self):
        from app.ui.speaker_name_panel import _speakers_holding_name
        result = _speakers_holding_name(
            "Jane",
            {"SPEAKER_00": "Jane", "SPEAKER_01": "Jane", "SPEAKER_02": "John"},
            "SPEAKER_02",
        )
        self.assertEqual(sorted(result), ["SPEAKER_00", "SPEAKER_01"])

    def test_blank_name_never_matches(self):
        from app.ui.speaker_name_panel import _speakers_holding_name
        result = _speakers_holding_name(
            "",
            {"SPEAKER_00": "", "SPEAKER_01": ""},
            "SPEAKER_02",
        )
        self.assertEqual(result, [])


class TestSpeakerNamePanelEmptyState(unittest.TestCase):
    """set_speakers([]) used to just self.hide() the whole panel, leaving
    the Inspector's "Speakers" section blank with no explanation (e.g. a
    transcript with diarization off). It should show a "No speakers
    identified" message instead.
    """

    @classmethod
    def setUpClass(cls):
        _get_app()

    def test_starts_in_empty_state(self):
        from app.ui.speaker_name_panel import SpeakerNamePanel
        panel = SpeakerNamePanel()
        self.assertFalse(panel._empty_row_widget.isHidden())
        self.assertTrue(panel._toggle_btn.isHidden())
        self.assertTrue(panel._rows_container.isHidden())

    def test_set_speakers_with_none_shows_empty_message(self):
        from app.ui.speaker_name_panel import SpeakerNamePanel
        panel = SpeakerNamePanel()
        panel.set_speakers([], {})
        self.assertFalse(panel._empty_row_widget.isHidden())
        self.assertTrue(panel._toggle_btn.isHidden())

    def test_set_speakers_with_some_hides_empty_message(self):
        from app.ui.speaker_name_panel import SpeakerNamePanel
        from app.transcription.transcriber import TranscriptSegment
        panel = SpeakerNamePanel()
        segments = [TranscriptSegment(start=0, end=1, text="a", speaker="SPEAKER_00")]
        panel.set_speakers(segments, {})
        self.assertTrue(panel._empty_row_widget.isHidden())
        self.assertFalse(panel._toggle_btn.isHidden())

    def test_transitioning_back_to_no_speakers_restores_empty_message(self):
        from app.ui.speaker_name_panel import SpeakerNamePanel
        from app.transcription.transcriber import TranscriptSegment
        panel = SpeakerNamePanel()
        segments = [TranscriptSegment(start=0, end=1, text="a", speaker="SPEAKER_00")]
        panel.set_speakers(segments, {})
        panel.set_speakers([], {})
        self.assertFalse(panel._empty_row_widget.isHidden())
        self.assertTrue(panel._toggle_btn.isHidden())

    def test_widget_itself_stays_visible_in_empty_state(self):
        # The panel used to hide() itself entirely, which — pre-redesign,
        # when it sat directly in the transcript column — collapsed its
        # space. Now it lives inside the Inspector's own CollapsibleSection,
        # which needs the panel to stay shown so the empty message renders.
        from app.ui.speaker_name_panel import SpeakerNamePanel
        panel = SpeakerNamePanel()
        panel.set_speakers([], {})
        self.assertFalse(panel.isHidden())


if __name__ == "__main__":
    unittest.main()
