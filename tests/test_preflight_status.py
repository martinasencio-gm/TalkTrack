"""Tests for app.utils.preflight_status — the pure verdict/check computation
behind the capture bar's pre-flight block (app/ui/preflight.py). Extracted so
the truth table is testable without instantiating any Qt widget.
"""
import unittest

from app.utils.preflight_status import (
    READY, WARNING, BLOCKED, QUIET_MIC_THRESHOLD_DB,
    compute_mic_check, compute_call_check, compute_transcription_check,
    compute_verdict,
)


class TestComputeMicCheck(unittest.TestCase):
    def test_no_mic_is_blocked(self):
        status, title, _ = compute_mic_check(has_mic=False, mic_mismatch=None)
        self.assertEqual(status, BLOCKED)
        self.assertEqual(title, "No microphone selected")

    def test_mismatch_is_warning(self):
        status, title, _ = compute_mic_check(
            has_mic=True, mic_mismatch={"app": "Microsoft Teams", "device": "Jabra"}
        )
        self.assertEqual(status, WARNING)
        self.assertIn("Microsoft Teams", title)

    def test_mic_selected_no_mismatch_is_ready(self):
        status, _, _ = compute_mic_check(has_mic=True, mic_mismatch=None)
        self.assertEqual(status, READY)

    def test_no_peak_reading_yet_is_ready(self):
        """None means 'not enough audio sampled yet' — must never itself
        warn, or every mic would flash quiet for the first couple seconds
        after the app opens."""
        status, _, _ = compute_mic_check(has_mic=True, mic_mismatch=None, mic_peak_db=None)
        self.assertEqual(status, READY)

    def test_quiet_peak_warns(self):
        status, title, subtitle = compute_mic_check(
            has_mic=True, mic_mismatch=None,
            mic_peak_db=QUIET_MIC_THRESHOLD_DB - 5, mic_name="fifine SC3",
        )
        self.assertEqual(status, WARNING)
        self.assertEqual(title, "Mic is very quiet")
        self.assertIn("fifine SC3", subtitle)

    def test_loud_enough_peak_is_ready(self):
        status, _, _ = compute_mic_check(
            has_mic=True, mic_mismatch=None, mic_peak_db=QUIET_MIC_THRESHOLD_DB + 10,
        )
        self.assertEqual(status, READY)

    def test_no_mic_selected_beats_quiet_peak(self):
        """Selection problems still win over a level reading — has_mic=False
        should never happen alongside a real peak reading, but BLOCKED must
        still win if it somehow does."""
        status, title, _ = compute_mic_check(
            has_mic=False, mic_mismatch=None, mic_peak_db=QUIET_MIC_THRESHOLD_DB - 20,
        )
        self.assertEqual(status, BLOCKED)
        self.assertEqual(title, "No microphone selected")


class TestComputeCallCheck(unittest.TestCase):
    def test_conferencing_blocked_wins_over_everything(self):
        status, _, _ = compute_call_check(
            has_source=True, conferencing_blocked=True, output_mismatch=None
        )
        self.assertEqual(status, BLOCKED)

    def test_no_source_selected_is_warning(self):
        status, _, _ = compute_call_check(
            has_source=False, conferencing_blocked=False, output_mismatch=None
        )
        self.assertEqual(status, WARNING)

    def test_output_mismatch_is_warning(self):
        status, title, _ = compute_call_check(
            has_source=True, conferencing_blocked=False,
            output_mismatch={"app": "Zoom", "device": "Headphones"},
        )
        self.assertEqual(status, WARNING)
        self.assertIn("Zoom", title)

    def test_source_selected_no_issues_is_ready(self):
        status, _, _ = compute_call_check(
            has_source=True, conferencing_blocked=False, output_mismatch=None
        )
        self.assertEqual(status, READY)


class TestComputeTranscriptionCheck(unittest.TestCase):
    def test_diarization_enabled_without_token_is_warning(self):
        status, _, _ = compute_transcription_check(
            diarization_enabled=True, hf_token_present=False
        )
        self.assertEqual(status, WARNING)

    def test_diarization_enabled_with_token_is_ready(self):
        status, _, _ = compute_transcription_check(
            diarization_enabled=True, hf_token_present=True
        )
        self.assertEqual(status, READY)

    def test_diarization_disabled_is_ready_regardless_of_token(self):
        status, _, _ = compute_transcription_check(
            diarization_enabled=False, hf_token_present=False
        )
        self.assertEqual(status, READY)


class TestComputeVerdict(unittest.TestCase):
    def _ready(self):
        return (READY, "Ready", "Ready")

    def test_all_ready_is_ready(self):
        verdict, title, subtitle = compute_verdict(
            self._ready(), self._ready(), self._ready()
        )
        self.assertEqual(verdict, READY)
        self.assertTrue(title)
        self.assertTrue(subtitle)

    def test_one_warning_is_warning_verdict(self):
        verdict, _, _ = compute_verdict(
            self._ready(),
            (WARNING, "No app or system audio selected", "Check your sources"),
            self._ready(),
        )
        self.assertEqual(verdict, WARNING)

    def test_one_blocked_beats_a_warning(self):
        verdict, _, _ = compute_verdict(
            (WARNING, "Some mic warning", "fix it"),
            (BLOCKED, "This app blocks per-app capture", "switch modes"),
            self._ready(),
        )
        self.assertEqual(verdict, BLOCKED)

    def test_title_names_the_actual_problem_not_a_generic_wrapper(self):
        verdict, title, subtitle = compute_verdict(
            self._ready(),
            (BLOCKED, "This app blocks per-app capture",
             "Switch to all system audio or the call will record silent"),
            self._ready(),
        )
        self.assertEqual(verdict, BLOCKED)
        self.assertEqual(title, "This app blocks per-app capture")
        self.assertIn("system audio", subtitle)

    def test_mic_blocked_subtitle_points_at_sources(self):
        verdict, title, subtitle = compute_verdict(
            (BLOCKED, "No microphone selected", "Pick a mic in Sources before you record"),
            self._ready(),
            self._ready(),
        )
        self.assertEqual(title, "No microphone selected")
        self.assertIn("Sources", subtitle)

    def test_quiet_mic_verdict_carries_the_dB_reading(self):
        verdict, title, subtitle = compute_verdict(
            (WARNING, "Mic is very quiet", "fifine SC3 peaking at -48 dB — check it before you record"),
            self._ready(),
            self._ready(),
        )
        self.assertEqual(verdict, WARNING)
        self.assertEqual(title, "Mic is very quiet")
        self.assertIn("-48 dB", subtitle)


if __name__ == "__main__":
    unittest.main()
