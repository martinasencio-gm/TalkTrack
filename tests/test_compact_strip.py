"""CompactStrip: the floating always-on-top capture strip. Covers the pure
state-resolution helper and two bugs found while wiring it into MainWindow:
set_state("muted") had no branch (crash-free no-op, wrong visuals), and
_on_secondary_clicked only handled the "transcribing" (Cancel) case even
though the button is also shown as Resume (paused) and Open transcript
(done).
"""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import sys
import unittest

from PyQt6.QtWidgets import QApplication

from app.recording.recorder import RecordingState
from app.ui.compact_strip import CompactStrip, resolve_compact_strip_state

_app = None


def _get_app():
    global _app
    if _app is None:
        _app = QApplication.instance() or QApplication(sys.argv)
    return _app


class TestResolveCompactStripState(unittest.TestCase):
    def test_recording_not_muted(self):
        self.assertEqual(
            resolve_compact_strip_state(RecordingState.RECORDING, False, False, False),
            "recording",
        )

    def test_recording_muted_wins_over_transcribing_flag(self):
        self.assertEqual(
            resolve_compact_strip_state(RecordingState.RECORDING, True, True, False),
            "muted",
        )

    def test_paused_wins_over_transcribing(self):
        self.assertEqual(
            resolve_compact_strip_state(RecordingState.PAUSED, False, True, False),
            "paused",
        )

    def test_transcribing_when_idle_and_busy(self):
        self.assertEqual(
            resolve_compact_strip_state(RecordingState.IDLE, False, True, False),
            "transcribing",
        )

    def test_done_when_idle_not_busy_and_flagged(self):
        self.assertEqual(
            resolve_compact_strip_state(RecordingState.IDLE, False, False, True),
            "done",
        )

    def test_armed_is_the_default_idle_state(self):
        self.assertEqual(
            resolve_compact_strip_state(RecordingState.IDLE, False, False, False),
            "armed",
        )

    def test_stopping_falls_through_like_idle(self):
        self.assertEqual(
            resolve_compact_strip_state(RecordingState.STOPPING, False, False, False),
            "armed",
        )


class TestCompactStripSetState(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        _get_app()

    def test_every_declared_state_is_handled(self):
        strip = CompactStrip()
        for state in ("armed", "recording", "paused", "muted", "transcribing", "done"):
            strip.set_state(state)
            self.assertEqual(strip.current_state, state)

    def test_muted_state_shows_stop_and_hides_mic_meter(self):
        strip = CompactStrip()
        strip.set_state("muted")
        self.assertEqual(strip.btn_primary.text(), "Stop")
        self.assertTrue(strip.mic_meter.isHidden())
        self.assertFalse(strip.sys_meter.isHidden())


class TestCompactStripMutePauseButtons(unittest.TestCase):
    """Design handoff (TalkTrack - Compact Bar.dc.html) lists the Recording
    state's action row as mute · pause · Stop · expand, and "Mic muted,
    still recording" as muted · pause · Stop — but CompactStrip only ever
    had btn_secondary/btn_primary/btn_expand (3 buttons), with no way to
    request a mute toggle or a pause at all from the strip itself.
    """
    @classmethod
    def setUpClass(cls):
        _get_app()

    def test_hidden_outside_recording_and_muted(self):
        strip = CompactStrip()
        for state in ("armed", "paused", "transcribing", "done"):
            strip.set_state(state)
            self.assertTrue(strip.btn_mute.isHidden(), state)
            self.assertTrue(strip.btn_pause.isHidden(), state)

    def test_shown_in_recording_and_muted(self):
        strip = CompactStrip()
        for state in ("recording", "muted"):
            strip.set_state(state)
            self.assertFalse(strip.btn_mute.isHidden(), state)
            self.assertFalse(strip.btn_pause.isHidden(), state)

    def test_mute_button_click_emits_mute_requested(self):
        strip = CompactStrip()
        strip.set_state("recording")
        received = []
        strip.mute_requested.connect(lambda: received.append(True))
        strip.btn_mute.click()
        self.assertEqual(received, [True])

    def test_mute_button_still_emits_from_muted_state(self):
        # Mute is a single toggle-request button, not two separate mute/
        # unmute buttons — MainWindow's _toggle_mute() (out of scope here)
        # is what flips the actual bool and re-derives the next state.
        strip = CompactStrip()
        strip.set_state("muted")
        received = []
        strip.mute_requested.connect(lambda: received.append(True))
        strip.btn_mute.click()
        self.assertEqual(received, [True])

    def test_pause_button_click_emits_pause_requested(self):
        strip = CompactStrip()
        strip.set_state("recording")
        received = []
        strip.pause_requested.connect(lambda: received.append(True))
        strip.btn_pause.click()
        self.assertEqual(received, [True])

    def test_pause_button_click_emits_from_muted_state_too(self):
        strip = CompactStrip()
        strip.set_state("muted")
        received = []
        strip.pause_requested.connect(lambda: received.append(True))
        strip.btn_pause.click()
        self.assertEqual(received, [True])

    def test_mute_icon_differs_between_recording_and_muted(self):
        strip = CompactStrip()
        strip.set_state("recording")
        recording_icon = strip.btn_mute.icon().pixmap(16, 16).cacheKey()
        strip.set_state("muted")
        muted_icon = strip.btn_mute.icon().pixmap(16, 16).cacheKey()
        self.assertNotEqual(recording_icon, muted_icon)

    def test_muted_mic_button_has_red_border_recording_does_not(self):
        strip = CompactStrip()
        strip.set_state("recording")
        self.assertNotIn("f38ba8", strip.btn_mute.styleSheet())
        strip.set_state("muted")
        self.assertIn("f38ba8", strip.btn_mute.styleSheet())

    def test_switching_back_to_recording_clears_muted_border(self):
        strip = CompactStrip()
        strip.set_state("muted")
        strip.set_state("recording")
        self.assertNotIn("f38ba8", strip.btn_mute.styleSheet())


class TestCompactStripSecondaryButton(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        _get_app()

    def test_transcribing_secondary_click_emits_cancel(self):
        strip = CompactStrip()
        strip.set_state("transcribing")
        received = []
        strip.cancel_requested.connect(lambda: received.append(True))
        strip._on_secondary_clicked()
        self.assertEqual(received, [True])

    def test_paused_secondary_click_emits_resume(self):
        strip = CompactStrip()
        strip.set_state("paused")
        received = []
        strip.resume_requested.connect(lambda: received.append(True))
        strip._on_secondary_clicked()
        self.assertEqual(received, [True])

    def test_done_secondary_click_emits_open_transcript(self):
        strip = CompactStrip()
        strip.set_state("done")
        received = []
        strip.open_transcript_requested.connect(lambda: received.append(True))
        strip._on_secondary_clicked()
        self.assertEqual(received, [True])

    def test_armed_secondary_click_emits_nothing(self):
        strip = CompactStrip()
        strip.set_state("armed")
        received = []
        strip.cancel_requested.connect(lambda: received.append(True))
        strip.resume_requested.connect(lambda: received.append(True))
        strip.open_transcript_requested.connect(lambda: received.append(True))
        strip._on_secondary_clicked()
        self.assertEqual(received, [])


class TestCompactStripPositionSignal(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        _get_app()

    def test_mouse_release_emits_position_changed(self):
        strip = CompactStrip()
        strip.move(100, 120)
        received = []
        strip.position_changed.connect(lambda x, y: received.append((x, y)))

        from PyQt6.QtCore import QPointF, Qt
        from PyQt6.QtGui import QMouseEvent

        event = QMouseEvent(
            QMouseEvent.Type.MouseButtonRelease, QPointF(10, 10), QPointF(10, 10),
            Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier,
        )
        strip.mouseReleaseEvent(event)
        self.assertEqual(received, [(100, 120)])


if __name__ == "__main__":
    unittest.main()
