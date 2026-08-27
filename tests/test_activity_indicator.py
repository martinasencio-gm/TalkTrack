"""Unit tests for activity indicator pure helpers."""
import unittest

from app.ui.activity_indicator import (
    resolve_activity_state,
    format_activity_label,
    resolve_dot_color,
)
from app.recording.recorder import RecordingState


class TestResolveActivityState(unittest.TestCase):
    def test_recording_wins_over_transcribing(self):
        self.assertEqual(
            resolve_activity_state(RecordingState.RECORDING, True), "recording"
        )

    def test_paused_wins_over_transcribing(self):
        self.assertEqual(
            resolve_activity_state(RecordingState.PAUSED, True), "paused"
        )

    def test_recording_wins_when_not_transcribing(self):
        self.assertEqual(
            resolve_activity_state(RecordingState.RECORDING, False), "recording"
        )

    def test_transcribing_when_idle_and_busy(self):
        self.assertEqual(
            resolve_activity_state(RecordingState.IDLE, True), "transcribing"
        )

    def test_ai_busy_when_idle_shows_transcribing_state(self):
        # AI work (summary / action items) rides the same visual state as
        # transcription — "show it the same way".
        self.assertEqual(
            resolve_activity_state(RecordingState.IDLE, False, ai_busy=True),
            "transcribing",
        )

    def test_recording_wins_over_ai_busy(self):
        self.assertEqual(
            resolve_activity_state(RecordingState.RECORDING, False, ai_busy=True),
            "recording",
        )

    def test_none_when_idle_and_not_busy(self):
        self.assertIsNone(resolve_activity_state(RecordingState.IDLE, False))

    def test_none_when_stopping_and_not_busy(self):
        self.assertIsNone(resolve_activity_state(RecordingState.STOPPING, False))


class TestFormatActivityLabel(unittest.TestCase):
    def test_recording_shows_elapsed_mmss(self):
        self.assertEqual(
            format_activity_label("recording", elapsed_seconds=754), "12:34"
        )

    def test_paused_shows_elapsed_mmss(self):
        self.assertEqual(
            format_activity_label("paused", elapsed_seconds=65), "01:05"
        )

    def test_recording_defaults_to_zero_elapsed(self):
        self.assertEqual(format_activity_label("recording"), "00:00")

    def test_transcribing_shows_percent(self):
        self.assertEqual(
            format_activity_label("transcribing", progress_percent=42), "42%"
        )

    def test_transcribing_shows_ellipsis_when_percent_unknown(self):
        # No percent means no progress data — e.g. diarization, which runs
        # after transcription and reports no percent. "0%" would misread as
        # "just started transcribing," so this must not fall back to 0.
        self.assertEqual(format_activity_label("transcribing"), "…")

    def test_transcribing_shows_zero_percent_explicitly(self):
        self.assertEqual(
            format_activity_label("transcribing", progress_percent=0), "0%"
        )

    def test_phase_label_overrides_the_transcribing_verb(self):
        self.assertEqual(
            format_activity_label(
                "transcribing", phase_label="Generating summary"
            ),
            "Generating summary…",
        )

    def test_phase_label_with_percent(self):
        self.assertEqual(
            format_activity_label(
                "transcribing", progress_percent=42,
                phase_label="Identifying speakers",
            ),
            "Identifying speakers 42%",
        )

    def test_phase_label_ignored_for_recording(self):
        self.assertEqual(
            format_activity_label(
                "recording", elapsed_seconds=65, phase_label="whatever"
            ),
            "01:05",
        )


class TestResolveDotColor(unittest.TestCase):
    def test_recording_is_red(self):
        self.assertEqual(resolve_dot_color("recording"), "#f38ba8")

    def test_paused_is_amber(self):
        self.assertEqual(resolve_dot_color("paused"), "#f9e2af")

    def test_transcribing_is_the_accent(self):
        # Same accent the capture bar's transcribing strip uses, so the
        # two surfaces agree about what "a job is running" looks like.
        self.assertEqual(resolve_dot_color("transcribing"), "#9184d9")

    def test_none_state_returns_none(self):
        self.assertIsNone(resolve_dot_color(None))


if __name__ == "__main__":
    unittest.main()
