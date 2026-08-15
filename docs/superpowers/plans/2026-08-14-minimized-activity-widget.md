# Minimized Activity Widget Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** When TalkTrack is minimized or hidden-to-tray while recording, paused, or transcribing, show a small floating always-on-top pill widget indicating the activity; clicking it restores the main window. Idle minimizing is unaffected.

**Architecture:** New file `app/ui/activity_indicator.py` follows the existing `app/ui/tray_icon.py` pattern: Qt-free pure functions (`resolve_activity_state`, `format_activity_label`, `resolve_dot_color`) plus a thin `ActivityIndicator(QWidget)` that paints itself and reports interaction via signals. `MainWindow` owns one instance and is the sole place deciding show/hide/update, through one method, `_update_activity_visibility()`, called from every place that can change either half of its trigger condition (window minimize/restore, recording state changes, the per-second recording tick, and the two chokepoints — `_start_transcription` and `_process_pending_transcriptions` — that between them cover every transcription-pipeline entry and exit).

**Tech Stack:** PyQt6 (QWidget, QPainter, QTimer, pyqtSignal), existing `Config` (`app/utils/config.py`), existing `Recorder`/`RecordingState` (`app/recording/recorder.py`).

## Global Constraints

- Pill size: 130×36px, rounded-rect (fully rounded ends), background `#1e1e2e`, text `#cdd6f4`.
- Dot colors: Recording `#f38ba8` (red, pulses ~800ms), Paused `#f9e2af` (amber, static), Transcribing `#89b4fa` (blue, static).
- Pulse interval: 800ms (toggle dot visibility).
- Drag threshold: 4px (movement at/under this on release = click → restore; over = drag → position save).
- Config key: `ui.activity_widget_position`, nullable `[x, y]`, default `None`.
- Window flags: `Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint | Qt.WindowType.Tool`.
- No stop/cancel control on the widget — restoring the window is the only way to affect recording/transcription.
- The "Minimize to tray" checkbox (`general.minimize_to_tray`) keeps controlling idle-minimize behavior exactly as today; it must not run when busy.
- Trigger condition, defined once in `_update_activity_visibility`: `busy_state is not None and (self.isMinimized() or self.isHidden())`. Every call site relies on this single method — no call site re-implements the condition.
- Tests run via `QT_QPA_PLATFORM=offscreen .venv/Scripts/python.exe -m pytest tests/ -q` (never bare `uv run` or bare `pytest`).
- Commits go directly to `master`, with conventional prefixes (`main:`, `ui:`); never `--amend`.

---

## Task 1: Pure functions — `resolve_activity_state`, `format_activity_label`, `resolve_dot_color`

**Files:**
- Create: `app/ui/activity_indicator.py` (pure-function portion only this task; the `ActivityIndicator` class is Task 2, added to the same file)
- Test: `tests/test_activity_indicator.py`

**Interfaces:**
- Consumes: `app.recording.recorder.RecordingState` (existing enum: `IDLE`, `RECORDING`, `PAUSED`, `STOPPING`).
- Produces (for Task 2 and Task 3 to import):
  - `resolve_activity_state(recording_state, transcription_busy) -> "recording" | "paused" | "transcribing" | None`
  - `format_activity_label(state, elapsed_seconds=None, progress_percent=None) -> str`
  - `resolve_dot_color(state) -> str | None` (hex color, or `None` for unknown/`None` state)

- [ ] **Step 1: Write the failing test**

Create `tests/test_activity_indicator.py`:

```python
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

    def test_transcribing_defaults_to_zero_percent(self):
        self.assertEqual(format_activity_label("transcribing"), "0%")


class TestResolveDotColor(unittest.TestCase):
    def test_recording_is_red(self):
        self.assertEqual(resolve_dot_color("recording"), "#f38ba8")

    def test_paused_is_amber(self):
        self.assertEqual(resolve_dot_color("paused"), "#f9e2af")

    def test_transcribing_is_blue(self):
        self.assertEqual(resolve_dot_color("transcribing"), "#89b4fa")

    def test_none_state_returns_none(self):
        self.assertIsNone(resolve_dot_color(None))


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `QT_QPA_PLATFORM=offscreen .venv/Scripts/python.exe -m pytest tests/test_activity_indicator.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.ui.activity_indicator'`

- [ ] **Step 3: Write minimal implementation**

Create `app/ui/activity_indicator.py`:

```python
"""Floating activity indicator shown when TalkTrack is minimized while busy.

Pure helpers are module-level and unit-testable, mirroring tray_icon.py's
pattern. The Qt widget (ActivityIndicator) comes in a later task and
composes them with QPainter.
"""
from app.recording.recorder import RecordingState


def resolve_activity_state(recording_state, transcription_busy):
    """Return "recording" | "paused" | "transcribing" | None.

    Recording/paused always wins over transcribing — if both are happening
    (e.g. auto-transcribe kicked off for a prior recording while a new one
    is being captured), the widget shows the recording, not the transcript
    job. None means nothing to show.
    """
    if recording_state == RecordingState.RECORDING:
        return "recording"
    if recording_state == RecordingState.PAUSED:
        return "paused"
    if transcription_busy:
        return "transcribing"
    return None


def format_activity_label(state, elapsed_seconds=None, progress_percent=None):
    """"MM:SS" for "recording"/"paused"; "NN%" for "transcribing"."""
    if state in ("recording", "paused"):
        total = max(0, int(elapsed_seconds or 0))
        minutes, seconds = divmod(total, 60)
        return f"{minutes:02d}:{seconds:02d}"
    if state == "transcribing":
        return f"{int(progress_percent or 0)}%"
    return ""


def resolve_dot_color(state):
    """Hex color for the state dot: red/amber/blue."""
    return {
        "recording": "#f38ba8",
        "paused": "#f9e2af",
        "transcribing": "#89b4fa",
    }.get(state)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `QT_QPA_PLATFORM=offscreen .venv/Scripts/python.exe -m pytest tests/test_activity_indicator.py -v`
Expected: PASS (16 tests)

- [ ] **Step 5: Commit**

```bash
git add app/ui/activity_indicator.py tests/test_activity_indicator.py
git commit -m "ui: add activity indicator pure decision/formatting helpers"
```

---

## Task 2: `ActivityIndicator(QWidget)` — the floating pill widget

**Files:**
- Modify: `app/ui/activity_indicator.py` (append the widget class below the pure functions from Task 1)
- Test: `tests/test_activity_indicator_widget.py`

**Interfaces:**
- Consumes: `resolve_activity_state`, `format_activity_label`, `resolve_dot_color` from Task 1 (same file).
- Produces (for Task 3 to import and use):
  - `class ActivityIndicator(QWidget)`
    - `restore_requested = pyqtSignal()` — emitted on a genuine click (movement ≤ 4px between press and release).
    - `position_changed = pyqtSignal(int, int)` — emitted after a drag ends (movement > 4px), carrying `(x, y)` = the widget's new top-left screen coordinates.
    - `set_activity(state, elapsed_seconds=None, progress_percent=None)` — updates label/dot and starts/stops the pulse timer.
    - `show_at(x, y)` — clamps to the primary screen's available geometry, then shows.

- [ ] **Step 1: Write the failing test**

Create `tests/test_activity_indicator_widget.py`:

```python
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import sys
import unittest

from PyQt6.QtCore import QPoint, Qt
from PyQt6.QtWidgets import QApplication

from app.ui.activity_indicator import ActivityIndicator

_app = None


def _get_app():
    global _app
    if _app is None:
        _app = QApplication.instance() or QApplication(sys.argv)
    return _app


class _FakeLeftButtonEvent:
    def button(self):
        return Qt.MouseButton.LeftButton


class TestActivityIndicatorWidget(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        _get_app()

    def test_set_activity_recording_starts_pulse_timer(self):
        widget = ActivityIndicator()
        widget.set_activity("recording", elapsed_seconds=5)
        self.assertTrue(widget._pulse_timer.isActive())
        widget.close()

    def test_set_activity_paused_stops_pulse_timer(self):
        widget = ActivityIndicator()
        widget.set_activity("recording", elapsed_seconds=5)
        widget.set_activity("paused", elapsed_seconds=5)
        self.assertFalse(widget._pulse_timer.isActive())
        widget.close()

    def test_set_activity_transcribing_stops_pulse_timer(self):
        widget = ActivityIndicator()
        widget.set_activity("recording", elapsed_seconds=5)
        widget.set_activity("transcribing", progress_percent=50)
        self.assertFalse(widget._pulse_timer.isActive())
        widget.close()

    def test_show_at_clamps_to_screen_geometry(self):
        widget = ActivityIndicator()
        widget.show_at(-5000, -5000)
        screen = QApplication.primaryScreen()
        geo = screen.availableGeometry()
        self.assertGreaterEqual(widget.x(), geo.left())
        self.assertGreaterEqual(widget.y(), geo.top())
        widget.close()

    def test_show_at_makes_widget_visible(self):
        widget = ActivityIndicator()
        widget.show_at(100, 100)
        self.assertTrue(widget.isVisible())
        widget.close()

    def test_click_without_drag_emits_restore_requested(self):
        widget = ActivityIndicator()
        received = []
        widget.restore_requested.connect(lambda: received.append(True))
        widget._press_pos = QPoint(10, 10)
        widget._press_widget_pos = widget.pos()
        widget._moved_distance = 0
        widget.mouseReleaseEvent(_FakeLeftButtonEvent())
        self.assertEqual(received, [True])
        widget.close()

    def test_drag_past_threshold_emits_position_changed(self):
        widget = ActivityIndicator()
        received = []
        widget.position_changed.connect(lambda x, y: received.append((x, y)))
        widget._press_pos = QPoint(10, 10)
        widget._press_widget_pos = widget.pos()
        widget._moved_distance = 20
        widget.mouseReleaseEvent(_FakeLeftButtonEvent())
        self.assertEqual(len(received), 1)
        widget.close()

    def test_drag_at_exactly_threshold_emits_restore_not_position(self):
        widget = ActivityIndicator()
        restored = []
        moved = []
        widget.restore_requested.connect(lambda: restored.append(True))
        widget.position_changed.connect(lambda x, y: moved.append((x, y)))
        widget._press_pos = QPoint(10, 10)
        widget._press_widget_pos = widget.pos()
        widget._moved_distance = 4
        widget.mouseReleaseEvent(_FakeLeftButtonEvent())
        self.assertEqual(restored, [True])
        self.assertEqual(moved, [])
        widget.close()


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `QT_QPA_PLATFORM=offscreen .venv/Scripts/python.exe -m pytest tests/test_activity_indicator_widget.py -v`
Expected: FAIL with `ImportError: cannot import name 'ActivityIndicator'`

- [ ] **Step 3: Write minimal implementation**

Append to `app/ui/activity_indicator.py` (add these imports at the top of the file, above the existing `from app.recording.recorder import RecordingState` line):

```python
from PyQt6.QtCore import Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QColor, QPainter
from PyQt6.QtWidgets import QApplication, QWidget
```

Then append the class and its constants at the end of the file:

```python
_WIDTH = 130
_HEIGHT = 36
_DRAG_THRESHOLD = 4
_PULSE_INTERVAL_MS = 800
_DOT_DIAMETER = 10
_DOT_MARGIN = 12


class ActivityIndicator(QWidget):
    """Floating always-on-top pill shown while minimized and busy.

    MainWindow owns one instance and is the sole place that decides when
    it shows, hides, or updates (see MainWindow._update_activity_visibility).
    """

    restore_requested = pyqtSignal()
    position_changed = pyqtSignal(int, int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setFixedSize(_WIDTH, _HEIGHT)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

        self._state = None
        self._label = ""
        self._dot_color = None
        self._dot_visible = True

        self._pulse_timer = QTimer(self)
        self._pulse_timer.setInterval(_PULSE_INTERVAL_MS)
        self._pulse_timer.timeout.connect(self._toggle_pulse)

        self._press_pos = None
        self._press_widget_pos = None
        self._moved_distance = 0

    def set_activity(self, state, elapsed_seconds=None, progress_percent=None):
        self._state = state
        self._label = format_activity_label(state, elapsed_seconds, progress_percent)
        self._dot_color = resolve_dot_color(state)
        if state == "recording":
            self._dot_visible = True
            if not self._pulse_timer.isActive():
                self._pulse_timer.start()
        else:
            self._pulse_timer.stop()
            self._dot_visible = True
        self.update()

    def _toggle_pulse(self):
        self._dot_visible = not self._dot_visible
        self.update()

    def show_at(self, x, y):
        screen = QApplication.primaryScreen()
        geo = screen.availableGeometry()
        clamped_x = min(max(x, geo.left()), geo.right() - _WIDTH)
        clamped_y = min(max(y, geo.top()), geo.bottom() - _HEIGHT)
        self.move(clamped_x, clamped_y)
        self.show()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor("#1e1e2e"))
        painter.drawRoundedRect(self.rect(), _HEIGHT / 2, _HEIGHT / 2)

        if self._dot_color and self._dot_visible:
            painter.setBrush(QColor(self._dot_color))
            dot_y = (_HEIGHT - _DOT_DIAMETER) // 2
            painter.drawEllipse(_DOT_MARGIN, dot_y, _DOT_DIAMETER, _DOT_DIAMETER)

        painter.setPen(QColor("#cdd6f4"))
        text_rect = self.rect().adjusted(_DOT_MARGIN + _DOT_DIAMETER + 8, 0, -10, 0)
        painter.drawText(
            text_rect,
            Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
            self._label,
        )
        painter.end()

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._press_pos = event.globalPosition().toPoint()
            self._press_widget_pos = self.pos()
            self._moved_distance = 0
            self.setCursor(Qt.CursorShape.SizeAllCursor)

    def mouseMoveEvent(self, event):
        if self._press_pos is not None:
            delta = event.globalPosition().toPoint() - self._press_pos
            self._moved_distance = max(
                self._moved_distance, abs(delta.x()) + abs(delta.y())
            )
            self.move(self._press_widget_pos + delta)

    def mouseReleaseEvent(self, event):
        if event.button() != Qt.MouseButton.LeftButton or self._press_pos is None:
            return
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        if self._moved_distance <= _DRAG_THRESHOLD:
            self.restore_requested.emit()
        else:
            self.position_changed.emit(self.x(), self.y())
        self._press_pos = None
        self._press_widget_pos = None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `QT_QPA_PLATFORM=offscreen .venv/Scripts/python.exe -m pytest tests/test_activity_indicator_widget.py tests/test_activity_indicator.py -v`
Expected: PASS (24 tests total)

- [ ] **Step 5: Commit**

```bash
git add app/ui/activity_indicator.py tests/test_activity_indicator_widget.py
git commit -m "ui: add ActivityIndicator floating pill widget"
```

---

## Task 3: `MainWindow` wiring — trigger logic, config key, teardown

**Files:**
- Modify: `app/utils/config.py:75-79` (add `activity_widget_position` to `DEFAULT_CONFIG["ui"]`)
- Modify: `app/main_window.py` (imports; `__init__`; `_on_state_changed`; `_on_recording_tick`; `_start_transcription`; `_process_pending_transcriptions`; `_restore_from_tray`; `changeEvent`; `closeEvent`; three new methods)
- Test: `tests/test_main_window_activity_widget.py`

**Interfaces:**
- Consumes: `ActivityIndicator`, `resolve_activity_state` from `app/ui/activity_indicator.py` (Tasks 1-2). `Config.get`/`Config.set` (`app/utils/config.py`, unchanged signatures: `get(*keys)`, `set(*keys_and_value)` — `set` auto-saves). `Recorder.state` (property), `Recorder.get_elapsed_time()` (existing). `MainWindow._transcription_busy()` (existing, `app/main_window.py:966-971`, unchanged).
- Produces: `MainWindow._activity_widget` (an `ActivityIndicator` instance), `MainWindow._update_activity_visibility()`, `MainWindow._activity_widget_position()`, `MainWindow._on_activity_widget_moved(x, y)`, `MainWindow._on_transcription_percent(pct)`, `MainWindow._current_transcription_percent` (int or `None`).

### Step 1: Add the config key

- [ ] **Edit `app/utils/config.py:75-79`**

Change:
```python
    "ui": {
        "theme": "dark",
        "speakers_collapsed": False,
        "right_panel_collapsed": False,
    },
```
to:
```python
    "ui": {
        "theme": "dark",
        "speakers_collapsed": False,
        "right_panel_collapsed": False,
        "activity_widget_position": None,
    },
```

### Step 2: Write the failing MainWindow-wiring tests

- [ ] **Create `tests/test_main_window_activity_widget.py`**

```python
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import sys
import unittest
from unittest.mock import patch

from PyQt6.QtWidgets import QApplication

_app = None


def _get_app():
    global _app
    if _app is None:
        _app = QApplication.instance() or QApplication(sys.argv)
    return _app


class TestMainWindowActivityWidget(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        _get_app()

    def test_activity_widget_created_and_wired_in_init(self):
        with patch("app.main_window.ActivityIndicator") as MockActivityIndicator:
            mock_instance = MockActivityIndicator.return_value
            from app.main_window import MainWindow
            window = MainWindow()
            try:
                MockActivityIndicator.assert_called_once_with()
                self.assertIs(window._activity_widget, mock_instance)
                mock_instance.restore_requested.connect.assert_called_once_with(
                    window._restore_from_tray
                )
                mock_instance.position_changed.connect.assert_called_once_with(
                    window._on_activity_widget_moved
                )
                self.assertIsNone(window._current_transcription_percent)
            finally:
                window._really_quit = True
                window.close()

    def test_update_activity_visibility_hides_when_visible_but_not_minimized(self):
        with patch("app.main_window.ActivityIndicator") as MockActivityIndicator:
            mock_instance = MockActivityIndicator.return_value
            mock_instance.isVisible.return_value = True
            from app.main_window import MainWindow
            window = MainWindow()
            try:
                window._update_activity_visibility()
                mock_instance.hide.assert_called_once_with()
            finally:
                window._really_quit = True
                window.close()

    def test_update_activity_visibility_shows_when_minimized_and_recording(self):
        with patch("app.main_window.ActivityIndicator") as MockActivityIndicator:
            mock_instance = MockActivityIndicator.return_value
            mock_instance.isVisible.return_value = False
            from app.main_window import MainWindow
            from app.recording.recorder import RecordingState
            window = MainWindow()
            try:
                window.recorder._state = RecordingState.RECORDING
                window.isMinimized = lambda: True
                window._update_activity_visibility()
                mock_instance.show_at.assert_called_once()
                mock_instance.set_activity.assert_called_once()
                args, _ = mock_instance.set_activity.call_args
                self.assertEqual(args[0], "recording")
            finally:
                window._really_quit = True
                window.close()

    def test_update_activity_visibility_does_not_show_when_idle(self):
        with patch("app.main_window.ActivityIndicator") as MockActivityIndicator:
            mock_instance = MockActivityIndicator.return_value
            mock_instance.isVisible.return_value = False
            from app.main_window import MainWindow
            window = MainWindow()
            try:
                window.isMinimized = lambda: True
                window._update_activity_visibility()
                mock_instance.show_at.assert_not_called()
                mock_instance.set_activity.assert_not_called()
            finally:
                window._really_quit = True
                window.close()

    def test_on_activity_widget_moved_saves_config(self):
        with patch("app.main_window.ActivityIndicator"):
            from app.main_window import MainWindow
            window = MainWindow()
            try:
                window._on_activity_widget_moved(200, 300)
                self.assertEqual(
                    window.config.get("ui", "activity_widget_position"), [200, 300]
                )
            finally:
                window._really_quit = True
                window.close()

    def test_activity_widget_position_falls_back_to_default(self):
        with patch("app.main_window.ActivityIndicator"):
            from app.main_window import MainWindow
            window = MainWindow()
            try:
                x, y = window._activity_widget_position()
                self.assertIsInstance(x, int)
                self.assertIsInstance(y, int)
            finally:
                window._really_quit = True
                window.close()

    def test_close_event_closes_activity_widget(self):
        with patch("app.main_window.ActivityIndicator") as MockActivityIndicator:
            mock_instance = MockActivityIndicator.return_value
            from app.main_window import MainWindow
            window = MainWindow()
            window._really_quit = True
            window.close()
            mock_instance.close.assert_called_once_with()

    def test_change_event_minimize_while_idle_still_hides_to_tray(self):
        with patch("app.main_window.ActivityIndicator") as MockActivityIndicator, \
                patch.object(type(None), "__nop__", None, create=False):
            mock_instance = MockActivityIndicator.return_value
            from app.main_window import MainWindow
            from PyQt6.QtCore import QEvent, Qt
            window = MainWindow()
            try:
                if not (window.tray.is_supported()):
                    self.skipTest("System tray not available on this runner")
                window.config.set("general", "minimize_to_tray", True)
                window.setWindowState(Qt.WindowState.WindowMinimized)
                event = QEvent(QEvent.Type.WindowStateChange)
                window.changeEvent(event)
                self.assertTrue(window.isHidden())
            finally:
                window._really_quit = True
                window.close()


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 3 (of Task 3 top-level): Run tests to verify they fail**

Run: `QT_QPA_PLATFORM=offscreen .venv/Scripts/python.exe -m pytest tests/test_main_window_activity_widget.py -v`
Expected: FAIL — `ImportError: cannot import name 'ActivityIndicator' from 'app.main_window'` (or `AttributeError: 'MainWindow' object has no attribute '_activity_widget'`)

### Step 4: Implement the MainWindow wiring

- [ ] **Edit `app/main_window.py:40`** — add the new import directly below the existing `from app.ui.tray_icon import TrayIcon` line:

```python
from app.ui.tray_icon import TrayIcon
from app.ui.activity_indicator import ActivityIndicator, resolve_activity_state
```

- [ ] **Edit `app/main_window.py:107-122`** (inside `__init__`, right after the tray block and before `QTimer.singleShot(500, self._check_startup_status)`) — change:

```python
        self.tray = TrayIcon(self)
        if self.tray.is_supported():
            self.tray.show()
            self.tray.show_requested.connect(self._restore_from_tray)
            self.tray.record_requested.connect(self._start_recording)
            self.tray.pause_requested.connect(self._toggle_pause)
            self.tray.resume_requested.connect(self._toggle_pause)
            self.tray.stop_requested.connect(self._stop_recording)
            self.tray.quit_requested.connect(self._quit_from_tray)
        else:
            import logging
            logging.getLogger("talktrack").warning(
                "System tray not available; minimize-to-tray is disabled."
            )

        QTimer.singleShot(500, self._check_startup_status)
```

to:

```python
        self.tray = TrayIcon(self)
        if self.tray.is_supported():
            self.tray.show()
            self.tray.show_requested.connect(self._restore_from_tray)
            self.tray.record_requested.connect(self._start_recording)
            self.tray.pause_requested.connect(self._toggle_pause)
            self.tray.resume_requested.connect(self._toggle_pause)
            self.tray.stop_requested.connect(self._stop_recording)
            self.tray.quit_requested.connect(self._quit_from_tray)
        else:
            import logging
            logging.getLogger("talktrack").warning(
                "System tray not available; minimize-to-tray is disabled."
            )

        self._current_transcription_percent = None
        self._activity_widget = ActivityIndicator()
        self._activity_widget.restore_requested.connect(self._restore_from_tray)
        self._activity_widget.position_changed.connect(self._on_activity_widget_moved)

        QTimer.singleShot(500, self._check_startup_status)
```

- [ ] **Edit `app/main_window.py:737-768`** (`_on_state_changed`) — add one line at the end of the method. Change the final two lines:

```python
            self._meeting_detector.note_recording_stopped()
            self.meeting_banner.hide_and_clear()
```

to:

```python
            self._meeting_detector.note_recording_stopped()
            self.meeting_banner.hide_and_clear()

        self._update_activity_visibility()
```

- [ ] **Edit `app/main_window.py:770-773`** (`_on_recording_tick`) — change:

```python
    def _on_recording_tick(self, seconds):
        if hasattr(self, "tray") and self.tray.is_supported():
            self.tray.set_state(self.recorder.state, int(seconds))
        self._check_silent_capture(seconds)
```

to:

```python
    def _on_recording_tick(self, seconds):
        if hasattr(self, "tray") and self.tray.is_supported():
            self.tray.set_state(self.recorder.state, int(seconds))
        self._check_silent_capture(seconds)
        self._update_activity_visibility()
```

- [ ] **Edit `app/main_window.py:990-1024`** (`_start_transcription`) — change:

```python
        self._transcription_worker = TranscriptionWorker(
            audio_path=audio_path,
            model_size=model_size,
            language=language,
            device=device,
        )
        self._transcription_worker.session = session
        self._transcription_worker.progress.connect(self._on_transcription_progress)
        self._transcription_worker.progress_percent.connect(self.transcript_viewer.set_progress_percent)
        self._transcription_worker.finished.connect(self._on_transcription_finished)
        self._transcription_worker.error.connect(self._on_transcription_error)
        self._transcription_worker.cancelled.connect(self._on_transcription_cancelled)
        self._transcription_worker.start(QThread.Priority.LowPriority)

        self.transcript_viewer.show_progress("Starting transcription...")
        self.status_label.setText("Transcribing...")
```

to:

```python
        self._current_transcription_percent = None
        self._transcription_worker = TranscriptionWorker(
            audio_path=audio_path,
            model_size=model_size,
            language=language,
            device=device,
        )
        self._transcription_worker.session = session
        self._transcription_worker.progress.connect(self._on_transcription_progress)
        self._transcription_worker.progress_percent.connect(self.transcript_viewer.set_progress_percent)
        self._transcription_worker.progress_percent.connect(self._on_transcription_percent)
        self._transcription_worker.finished.connect(self._on_transcription_finished)
        self._transcription_worker.error.connect(self._on_transcription_error)
        self._transcription_worker.cancelled.connect(self._on_transcription_cancelled)
        self._transcription_worker.start(QThread.Priority.LowPriority)

        self.transcript_viewer.show_progress("Starting transcription...")
        self.status_label.setText("Transcribing...")
        self._update_activity_visibility()
```

- [ ] **Add a new method** — insert directly after `_start_transcription` (i.e., right before `def _cancel_transcription(self):`, currently at `app/main_window.py:1026`):

```python
    def _on_transcription_percent(self, pct):
        self._current_transcription_percent = pct
        self._update_activity_visibility()

```

- [ ] **Edit `app/main_window.py:1036-1042`** (`_process_pending_transcriptions`) — change:

```python
    def _process_pending_transcriptions(self):
        if self._closing or self._transcription_busy():
            return
        if not self._pending_transcriptions:
            return
        audio_path, session = self._pending_transcriptions.pop(0)
        self._start_transcription(audio_path, session)
```

to:

```python
    def _process_pending_transcriptions(self):
        self._update_activity_visibility()
        if self._closing or self._transcription_busy():
            return
        if not self._pending_transcriptions:
            return
        audio_path, session = self._pending_transcriptions.pop(0)
        self._start_transcription(audio_path, session)
```

This one call covers every transcription-pipeline exit: `_process_pending_transcriptions()` is already the last thing called by `_on_transcription_cancelled`, `_on_transcription_error`, and both the early-return and normal-completion paths of `_display_final_transcript` (which is itself the terminal step reached by `_on_diarization_finished`, `_on_diarization_error`, `_on_simple_diarization_finished`, `_on_simple_diarization_error`, and the no-diarization branch of `_on_transcription_finished`). Placing the visibility recheck at the top of `_process_pending_transcriptions`, before the busy/empty-queue guards, means it always reflects the current `_transcription_busy()` truth value at that moment, and if a new transcription is dequeued and started, `_start_transcription`'s own call (added above) immediately reflects the switch back to busy.

- [ ] **Edit `app/main_window.py:1587-1593`** (`_restore_from_tray`) — change:

```python
    def _restore_from_tray(self):
        self.showNormal()
        self.raise_()
        self.activateWindow()
        self._success_pending = False
        self._error_pending = False
        self.tray.set_overlay(None)
```

to:

```python
    def _restore_from_tray(self):
        self.showNormal()
        self.raise_()
        self.activateWindow()
        self._success_pending = False
        self._error_pending = False
        self.tray.set_overlay(None)
        self._update_activity_visibility()
```

- [ ] **Edit `app/main_window.py:2012-2023`** (`changeEvent`) — change:

```python
    def changeEvent(self, event):
        if event.type() == QEvent.Type.WindowStateChange:
            if self.windowState() & Qt.WindowState.WindowMinimized:
                if self.config.get("general", "minimize_to_tray") and self.tray.is_supported():
                    self.setWindowState(Qt.WindowState.WindowNoState)
                    self.hide()
                    if self.config.get("general", "show_tray_hint"):
                        self.tray.show_hint_balloon()
                        self.config.set("general", "show_tray_hint", False)
                    event.accept()
                    return
        super().changeEvent(event)
```

to:

```python
    def changeEvent(self, event):
        if event.type() == QEvent.Type.WindowStateChange:
            if self.windowState() & Qt.WindowState.WindowMinimized:
                busy_state = resolve_activity_state(
                    self.recorder.state, self._transcription_busy()
                )
                if (busy_state is None and self.config.get("general", "minimize_to_tray")
                        and self.tray.is_supported()):
                    self.setWindowState(Qt.WindowState.WindowNoState)
                    self.hide()
                    if self.config.get("general", "show_tray_hint"):
                        self.tray.show_hint_balloon()
                        self.config.set("general", "show_tray_hint", False)
                    event.accept()
                    return
            self._update_activity_visibility()
        super().changeEvent(event)
```

`_update_activity_visibility()` now runs on every `WindowStateChange` that doesn't take the tray-hide early return — both "minimized while busy" (falls through) and "restored" (the `WindowMinimized` bit is no longer set, so the inner `if` is skipped entirely and the call still runs), so a normal-OS-minimize-then-restore always ends with the widget hidden.

- [ ] **Edit `app/main_window.py:2025-2055`** (`closeEvent`) — change:

```python
        if self._meeting_poll_timer.isActive():
            self._meeting_poll_timer.stop()
        self._com_poller.stop()
```

to:

```python
        if self._meeting_poll_timer.isActive():
            self._meeting_poll_timer.stop()
        self._com_poller.stop()
        self._activity_widget.close()
```

- [ ] **Add two new methods** — insert directly after `closeEvent`'s body, before `def _shutdown_workers(self):` (currently `app/main_window.py:2057`):

```python
    def _update_activity_visibility(self):
        busy_state = resolve_activity_state(self.recorder.state, self._transcription_busy())
        should_show = busy_state is not None and (self.isMinimized() or self.isHidden())
        if should_show:
            elapsed = (
                int(self.recorder.get_elapsed_time())
                if busy_state in ("recording", "paused") else None
            )
            percent = (
                self._current_transcription_percent
                if busy_state == "transcribing" else None
            )
            if not self._activity_widget.isVisible():
                x, y = self._activity_widget_position()
                self._activity_widget.show_at(x, y)
            self._activity_widget.set_activity(busy_state, elapsed, percent)
        elif self._activity_widget.isVisible():
            self._activity_widget.hide()

    def _activity_widget_position(self):
        saved = self.config.get("ui", "activity_widget_position")
        if saved:
            return saved[0], saved[1]
        screen = QApplication.primaryScreen()
        geo = screen.availableGeometry()
        return geo.right() - 150, geo.top() + 20

    def _on_activity_widget_moved(self, x, y):
        self.config.set("ui", "activity_widget_position", [x, y])

```

- [ ] **Step 5 (of Task 3 top-level): Run tests to verify they pass**

Run: `QT_QPA_PLATFORM=offscreen .venv/Scripts/python.exe -m pytest tests/test_main_window_activity_widget.py -v`
Expected: PASS (8 tests; `test_change_event_minimize_while_idle_still_hides_to_tray` may `SKIP` on a runner without a system tray)

- [ ] **Step 6 (of Task 3 top-level): Run the full suite to confirm no regressions**

Run: `QT_QPA_PLATFORM=offscreen .venv/Scripts/python.exe -m pytest tests/ -q`
Expected: PASS, no new failures.

- [ ] **Step 7 (of Task 3 top-level): Commit**

```bash
git add app/utils/config.py app/main_window.py tests/test_main_window_activity_widget.py
git commit -m "main: show floating activity widget when minimized while busy"
```

---

## Task 4: Manual verification (real Windows session)

No code changes — this task is a checklist run against a real `python main.py` (or `start_debug.bat`) session, since offscreen Qt tests can't verify visual rendering, real mouse drag, or multi-monitor clamping.

- [ ] Start a recording, minimize the window: the pill appears top-right, red dot pulsing, `MM:SS` counting up.
- [ ] Click the pill (no drag): window restores, pill disappears.
- [ ] Minimize again while recording, then pause from the restored window, then minimize again: dot is amber and static, label frozen at the pause-time elapsed value.
- [ ] Drag the pill to a new position, restart the app, minimize while recording again: pill reappears at the dragged position.
- [ ] With no recording/transcription active ("idle"), minimize with **Settings → Minimize to tray** ON: window hides to tray exactly as before (no pill, no taskbar entry) — confirms unchanged idle behavior.
- [ ] With **Minimize to tray** OFF, minimize while idle: window does a normal OS minimize (taskbar entry, no pill) — confirms unchanged idle behavior.
- [ ] Start a transcription (Transcribe button on a past recording) and minimize while it's running: pill appears blue, `NN%` counting up; when it finishes, pill disappears (window stays minimized/hidden per whatever state it was already in).
- [ ] Start a recording, minimize, then start a second recording is not applicable (single recorder) — instead: while a transcription is running and the window is minimized, confirm the pill does **not** show a stop/cancel affordance and clicking it only restores the window.
- [ ] Unplug/disable a second monitor (or resize the primary display) between runs if available, confirming a previously-saved off-screen position is clamped back on-screen rather than lost.

- [ ] **Commit** (only if this task's checklist surfaces a doc update worth recording — otherwise skip; this task produces no code changes by default)

---

## Self-Review

**1. Spec coverage:**
- Pure functions (`resolve_activity_state`, `format_activity_label`, `resolve_dot_color`) → Task 1. ✅
- `ActivityIndicator` widget (flags, size, paint, pulse, drag, `show_at` clamping) → Task 2. ✅
- `_update_activity_visibility` single-source-of-truth trigger → Task 3. ✅
- Called from `changeEvent`, `_on_state_changed`, `_on_recording_tick`, transcription start/percent/finished/error/cancelled → Task 3 (transcription lifecycle covered via the two chokepoints `_start_transcription` and `_process_pending_transcriptions`, reasoned through explicitly above). ✅
- `changeEvent`'s idle-path (`minimize_to_tray`) unchanged when idle, gated off when busy → Task 3. ✅
- Restore path (`_restore_from_tray`) hides the widget too → Task 3. ✅
- Position persistence (`ui.activity_widget_position`, save on `position_changed`, read with fallback, clamp in `show_at`) → Tasks 2 & 3. ✅
- `closeEvent` closes the widget → Task 3. ✅
- No stop control on the widget → Task 2 (no such signal/button exists). ✅
- Visual design (colors, pulse, pill size) → Global Constraints + Task 2. ✅
- Testing (pure-function tests, mocked-widget MainWindow tests, manual verification) → Tasks 1, 3, 4. ✅

**2. Placeholder scan:** No TBD/TODO markers; every code block is complete and runnable as written; every test has real assertions.

**3. Type consistency:** `resolve_activity_state` returns the same string literals (`"recording"`, `"paused"`, `"transcribing"`, `None`) consumed by `format_activity_label`, `resolve_dot_color`, and `ActivityIndicator.set_activity` throughout. `ActivityIndicator.__init__()` takes no required args in both the widget definition (Task 2) and its instantiation in `MainWindow.__init__` (Task 3: `ActivityIndicator()`). `position_changed` is `(int, int)` everywhere it's emitted/connected. `Config.get("ui", "activity_widget_position")` / `Config.set("ui", "activity_widget_position", [x, y])` match the key added to `DEFAULT_CONFIG` in Task 3 Step 1.

---

## Execution Handoff

**Plan complete and saved to `docs/superpowers/plans/2026-08-14-minimized-activity-widget.md`. Two execution options:**

**1. Subagent-Driven (recommended)** — I dispatch a fresh subagent per task, review between tasks, fast iteration.

**2. Inline Execution** — Execute tasks in this session using executing-plans, batch execution with checkpoints.

**Which approach?**
