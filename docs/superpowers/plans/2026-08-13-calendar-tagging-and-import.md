# Calendar Tagging & Recording Import Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let TalkTrack optionally match finished recordings against the local Outlook desktop
calendar (tagging them with event/attendee info), and let users import existing audio files as
new sessions that flow through the same transcribe/diarize pipeline as live recordings — with
calendar attendees feeding a dropdown-based speaker-naming UI.

**Architecture:** A new `app/integrations/outlook_calendar.py` (plain Python, no Qt) wraps
`win32com.client` read-only COM access to Outlook. A `CalendarLookupWorker(QThread)` runs it
off-thread and is dispatched from `MainWindow` after any recording (live or imported) finishes,
gated by a new opt-in `calendar.enabled` config flag. Matches surface in a new banner widget;
accepting one writes `calendar_event.json` into the session directory. Import reuses the
existing `_start_transcription`/diarization-selection code untouched — its existing
mic+system-required check for "simple" diarization already naturally routes imports (which only
have a `combined` track) to full pyannote diarization or no diarization, no new branching needed.
`SpeakerNamePanel` gains an optional attendee list that turns each speaker row into an editable,
mutually-exclusive dropdown.

**Tech Stack:** PyQt6, `pywin32` (`win32com.client`, already a dependency, previously unused),
`soundfile` (duration probing), `ffmpeg` subprocess (m4a→wav, same pattern as existing MP3
conversion).

## Global Constraints

- Durable file writes (new `calendar_event.json`, `metadata.json` for imports) go through
  `app/utils/atomic_io.py` (`atomic_write_json`) — never bare `open(..., "w")`.
- New background work (COM lookup) must never block the UI thread — always via `QThread`,
  following the session-binding convention: completion handlers read `worker.session`, never
  `self._current_session`.
- The calendar feature must degrade to a no-op (no error dialogs, no crashes) whenever Outlook
  isn't installed/running — it is optional and best-effort.
- `calendar.enabled` defaults to `False` in `DEFAULT_CONFIG`.
- Non-UI logic is TDD'd with `unittest`/`pytest` per existing conventions — pure functions,
  Qt objects mocked or avoided entirely (this codebase has **zero** `QApplication`-instantiating
  tests; keep it that way — extract logic into plain functions and smoke-test the Qt wiring by
  hand instead).
- Test runner: `python -m pytest tests/ -v` using the **global** `python` for the full suite, or
  `.venv\Scripts\python.exe -m pytest tests/ -v` per-task (both were confirmed working during
  setup — the global interpreter also has pytest here). Never `uv run` without `--no-sync`.
- Full suite must stay green after every task.

---

### Task 1: Add `calendar` config section

**Files:**
- Modify: `app/utils/config.py:6-63` (`DEFAULT_CONFIG` dict)
- Test: `tests/test_config.py`

**Interfaces:**
- Produces: `DEFAULT_CONFIG["calendar"]["enabled"]` (bool, default `False`) — read via
  `config.get("calendar", "enabled")`, written via `config.set("calendar", "enabled", bool)`,
  exactly like the existing `general.auto_transcribe` flag.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_config.py` (find the existing `TestCorruptionHandling`/default-value test
class and add a new test near it — follow the file's existing `ConfigTestCase` base pattern):

```python
class TestCalendarDefaults(ConfigTestCase):
    def test_calendar_enabled_defaults_false(self):
        cfg = Config()
        self.assertFalse(cfg.get("calendar", "enabled"))

    def test_calendar_enabled_round_trips(self):
        cfg = Config()
        cfg.set("calendar", "enabled", True)
        cfg2 = Config()
        self.assertTrue(cfg2.get("calendar", "enabled"))
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv\Scripts\python.exe -m pytest tests/test_config.py -v -k Calendar`
Expected: FAIL with `KeyError: 'calendar'`

- [ ] **Step 3: Add the config section**

In `app/utils/config.py`, inside `DEFAULT_CONFIG`, add a new top-level key (place it after the
`"ui"` section, before the closing `}` of `DEFAULT_CONFIG`):

```python
    "calendar": {
        "enabled": False,
    },
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv\Scripts\python.exe -m pytest tests/test_config.py -v -k Calendar`
Expected: PASS

- [ ] **Step 5: Run full suite and commit**

Run: `.venv\Scripts\python.exe -m pytest tests/ -v`
Expected: all pass (345 existing + 2 new)

```bash
git add app/utils/config.py tests/test_config.py
git commit -m "config: add opt-in calendar.enabled setting"
```

---

### Task 2: `outlook_calendar.py` — overlap matching logic

**Files:**
- Create: `app/integrations/__init__.py` (empty package init, mirror `app/ai/__init__.py`)
- Create: `app/integrations/outlook_calendar.py`
- Test: `tests/test_outlook_calendar.py`

**Interfaces:**
- Produces:
  - `find_overlapping_events(start: datetime, end: datetime, tolerance_minutes: int = 5) -> list[dict]`
    — each dict has keys `subject: str`, `start: datetime`, `end: datetime`, `organizer: str`,
    `attendees: list[str]`. Returns `[]` on any error (Outlook missing, COM failure, etc.) —
    never raises.
  - `_event_overlaps_window(event_start, event_end, window_start, window_end, tolerance_minutes) -> bool`
    (pure helper, exported for direct testing).

- [ ] **Step 1: Write the failing tests**

Create `tests/test_outlook_calendar.py`:

```python
"""Tests for Outlook calendar overlap matching."""
import unittest
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch


class TestEventOverlapsWindow(unittest.TestCase):
    def test_exact_overlap(self):
        from app.integrations.outlook_calendar import _event_overlaps_window
        window_start = datetime(2026, 8, 13, 14, 0)
        window_end = datetime(2026, 8, 13, 14, 45)
        self.assertTrue(_event_overlaps_window(
            window_start, window_end, window_start, window_end, tolerance_minutes=5
        ))

    def test_no_overlap_far_apart(self):
        from app.integrations.outlook_calendar import _event_overlaps_window
        event_start = datetime(2026, 8, 13, 9, 0)
        event_end = datetime(2026, 8, 13, 9, 30)
        window_start = datetime(2026, 8, 13, 14, 0)
        window_end = datetime(2026, 8, 13, 14, 45)
        self.assertFalse(_event_overlaps_window(
            event_start, event_end, window_start, window_end, tolerance_minutes=5
        ))

    def test_late_join_within_tolerance(self):
        # Recording started 4 minutes after the event's scheduled start.
        from app.integrations.outlook_calendar import _event_overlaps_window
        event_start = datetime(2026, 8, 13, 14, 0)
        event_end = datetime(2026, 8, 13, 14, 45)
        window_start = datetime(2026, 8, 13, 14, 4)
        window_end = datetime(2026, 8, 13, 14, 40)
        self.assertTrue(_event_overlaps_window(
            event_start, event_end, window_start, window_end, tolerance_minutes=5
        ))

    def test_just_outside_tolerance(self):
        from app.integrations.outlook_calendar import _event_overlaps_window
        event_start = datetime(2026, 8, 13, 14, 0)
        event_end = datetime(2026, 8, 13, 14, 45)
        window_start = datetime(2026, 8, 13, 15, 0)  # 15 min after event ends
        window_end = datetime(2026, 8, 13, 15, 30)
        self.assertFalse(_event_overlaps_window(
            event_start, event_end, window_start, window_end, tolerance_minutes=5
        ))


class _FakeAppointment:
    def __init__(self, subject, start, end, organizer, attendees):
        self.Subject = subject
        self.Start = start
        self.End = end
        self.Organizer = organizer
        self.RequiredAttendees = attendees  # semicolon-separated, as Outlook returns it


class TestFindOverlappingEvents(unittest.TestCase):
    def _mock_outlook(self, appointments):
        mock_items = MagicMock()
        mock_items.__iter__ = lambda self_: iter(appointments)
        mock_items.IncludeRecurrences = False
        mock_items.Sort = MagicMock()
        mock_calendar_folder = MagicMock()
        mock_calendar_folder.Items = mock_items
        mock_namespace = MagicMock()
        mock_namespace.GetDefaultFolder.return_value = mock_calendar_folder
        mock_outlook_app = MagicMock()
        mock_outlook_app.GetNamespace.return_value = mock_namespace
        return mock_outlook_app

    def test_single_match_returns_event_dict(self):
        from app.integrations.outlook_calendar import find_overlapping_events
        appt = _FakeAppointment(
            "Q3 Roadmap Sync",
            datetime(2026, 8, 13, 14, 0),
            datetime(2026, 8, 13, 14, 45),
            "Jane Smith",
            "Jane Smith; John Doe; Priya Patel",
        )
        mock_app = self._mock_outlook([appt])
        with patch("app.integrations.outlook_calendar.win32com.client.Dispatch",
                    return_value=mock_app):
            results = find_overlapping_events(
                datetime(2026, 8, 13, 14, 0), datetime(2026, 8, 13, 14, 45)
            )
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["subject"], "Q3 Roadmap Sync")
        self.assertEqual(results[0]["organizer"], "Jane Smith")
        self.assertEqual(
            results[0]["attendees"], ["Jane Smith", "John Doe", "Priya Patel"]
        )

    def test_no_matches_returns_empty_list(self):
        from app.integrations.outlook_calendar import find_overlapping_events
        appt = _FakeAppointment(
            "Unrelated Meeting",
            datetime(2026, 8, 13, 9, 0),
            datetime(2026, 8, 13, 9, 30),
            "Someone Else",
            "Someone Else",
        )
        mock_app = self._mock_outlook([appt])
        with patch("app.integrations.outlook_calendar.win32com.client.Dispatch",
                    return_value=mock_app):
            results = find_overlapping_events(
                datetime(2026, 8, 13, 14, 0), datetime(2026, 8, 13, 14, 45)
            )
        self.assertEqual(results, [])

    def test_multiple_overlaps_returns_all(self):
        from app.integrations.outlook_calendar import find_overlapping_events
        appt1 = _FakeAppointment(
            "Meeting A", datetime(2026, 8, 13, 14, 0), datetime(2026, 8, 13, 14, 30),
            "Alice", "Alice",
        )
        appt2 = _FakeAppointment(
            "Meeting B", datetime(2026, 8, 13, 14, 15), datetime(2026, 8, 13, 14, 45),
            "Bob", "Bob",
        )
        mock_app = self._mock_outlook([appt1, appt2])
        with patch("app.integrations.outlook_calendar.win32com.client.Dispatch",
                    return_value=mock_app):
            results = find_overlapping_events(
                datetime(2026, 8, 13, 14, 0), datetime(2026, 8, 13, 14, 45)
            )
        self.assertEqual({r["subject"] for r in results}, {"Meeting A", "Meeting B"})

    def test_outlook_unavailable_returns_empty_list(self):
        from app.integrations.outlook_calendar import find_overlapping_events
        with patch("app.integrations.outlook_calendar.win32com.client.Dispatch",
                    side_effect=Exception("Outlook not installed")):
            results = find_overlapping_events(
                datetime(2026, 8, 13, 14, 0), datetime(2026, 8, 13, 14, 45)
            )
        self.assertEqual(results, [])

    def test_empty_attendees_string_returns_empty_list_not_list_with_blank(self):
        from app.integrations.outlook_calendar import find_overlapping_events
        appt = _FakeAppointment(
            "Solo Block", datetime(2026, 8, 13, 14, 0), datetime(2026, 8, 13, 14, 45),
            "Jane Smith", "",
        )
        mock_app = self._mock_outlook([appt])
        with patch("app.integrations.outlook_calendar.win32com.client.Dispatch",
                    return_value=mock_app):
            results = find_overlapping_events(
                datetime(2026, 8, 13, 14, 0), datetime(2026, 8, 13, 14, 45)
            )
        self.assertEqual(results[0]["attendees"], [])


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv\Scripts\python.exe -m pytest tests/test_outlook_calendar.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.integrations'`

- [ ] **Step 3: Create the package and implementation**

Create `app/integrations/__init__.py` (empty file, matches `app/ai/__init__.py`).

Create `app/integrations/outlook_calendar.py`:

```python
"""Read-only lookup of the local Outlook desktop calendar via COM.

Best-effort integration: any failure (Outlook not installed, not running,
COM error) degrades to "no matches" rather than raising, since this feature
is opt-in and optional. See docs/superpowers/specs/2026-08-13-calendar-tagging-and-import-design.md
"""
import logging
from datetime import datetime, timedelta

import win32com.client

logger = logging.getLogger(__name__)

_OL_FOLDER_CALENDAR = 9


def _event_overlaps_window(event_start, event_end, window_start, window_end,
                            tolerance_minutes=5):
    """True if [event_start, event_end] overlaps [window_start, window_end]
    once each side of the window is padded by tolerance_minutes."""
    tolerance = timedelta(minutes=tolerance_minutes)
    padded_start = window_start - tolerance
    padded_end = window_end + tolerance
    return event_start < padded_end and event_end > padded_start


def _to_datetime(com_time):
    """Normalize a pywintypes COM datetime (or plain datetime, in tests) to
    a stdlib datetime with tzinfo stripped for simple comparison."""
    return datetime(
        com_time.year, com_time.month, com_time.day,
        com_time.hour, com_time.minute, com_time.second,
    )


def find_overlapping_events(start: datetime, end: datetime, tolerance_minutes: int = 5):
    """Return calendar events overlapping [start, end] (padded by tolerance).

    Each result: {"subject": str, "start": datetime, "end": datetime,
                   "organizer": str, "attendees": list[str]}.
    Returns [] if Outlook is unavailable or any COM error occurs.
    """
    try:
        outlook = win32com.client.Dispatch("Outlook.Application")
        namespace = outlook.GetNamespace("MAPI")
        calendar = namespace.GetDefaultFolder(_OL_FOLDER_CALENDAR)
        items = calendar.Items
        items.IncludeRecurrences = True
        items.Sort("[Start]")

        results = []
        for appt in items:
            try:
                event_start = _to_datetime(appt.Start)
                event_end = _to_datetime(appt.End)
            except (AttributeError, ValueError):
                continue
            if not _event_overlaps_window(event_start, event_end, start, end,
                                           tolerance_minutes):
                continue
            attendees_raw = (appt.RequiredAttendees or "").strip()
            attendees = (
                [a.strip() for a in attendees_raw.split(";") if a.strip()]
                if attendees_raw else []
            )
            results.append({
                "subject": appt.Subject or "",
                "start": event_start,
                "end": event_end,
                "organizer": appt.Organizer or "",
                "attendees": attendees,
            })
        return results
    except Exception:
        logger.debug("Outlook calendar lookup unavailable", exc_info=True)
        return []
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv\Scripts\python.exe -m pytest tests/test_outlook_calendar.py -v`
Expected: PASS (7 tests)

- [ ] **Step 5: Run full suite and commit**

Run: `.venv\Scripts\python.exe -m pytest tests/ -v`
Expected: all pass

```bash
git add app/integrations/__init__.py app/integrations/outlook_calendar.py tests/test_outlook_calendar.py
git commit -m "feat: add Outlook calendar overlap-matching (read-only COM)"
```

---

### Task 3: `CalendarLookupWorker` (QThread)

**Files:**
- Create: `app/ui/calendar_lookup_worker.py`

**Interfaces:**
- Consumes: `app.integrations.outlook_calendar.find_overlapping_events(start, end)` from Task 2.
- Produces: `CalendarLookupWorker(started_at: datetime, stopped_at: datetime)` — a `QThread`
  subclass with a `.session` attribute (set by the caller after construction, per the
  session-binding convention) and a `finished = pyqtSignal(list)` signal emitting the list of
  event dicts (possibly empty) from `find_overlapping_events`.

No automated test for this file — it is a two-line `QThread.run()` wrapper with no branching
logic of its own; the logic it calls is already covered by Task 2's tests. Verified via manual
smoke test in Step 2 below, per this codebase's convention of not instantiating `QApplication`
in the test suite.

- [ ] **Step 1: Write the implementation**

```python
"""Off-thread Outlook calendar lookup for a finished recording."""
from PyQt6.QtCore import QThread, pyqtSignal

from app.integrations.outlook_calendar import find_overlapping_events


class CalendarLookupWorker(QThread):
    """Looks up overlapping calendar events for [started_at, stopped_at].

    Carries a `.session` attribute (set by the caller, not the constructor)
    so completion handlers can bind results to the recording session that
    was active when the lookup started, per the session-binding convention
    in transcription-pipeline.md.
    """

    finished = pyqtSignal(list)  # list of event dicts, possibly empty

    def __init__(self, started_at, stopped_at, parent=None):
        super().__init__(parent)
        self.started_at = started_at
        self.stopped_at = stopped_at
        self.session = None

    def run(self):
        events = find_overlapping_events(self.started_at, self.stopped_at)
        self.finished.emit(events)
```

- [ ] **Step 2: Smoke test the module imports and constructs cleanly**

Run: `.venv\Scripts\python.exe -c "from app.ui.calendar_lookup_worker import CalendarLookupWorker; from datetime import datetime; w = CalendarLookupWorker(datetime.now(), datetime.now()); print(w.started_at, w.finished)"`
Expected: prints the timestamp and `<bound PYQT_SIGNAL finished ...>` with no traceback.

- [ ] **Step 3: Run full suite (regression check) and commit**

Run: `.venv\Scripts\python.exe -m pytest tests/ -v`
Expected: all pass (no new tests added, this task must not break anything)

```bash
git add app/ui/calendar_lookup_worker.py
git commit -m "feat: add CalendarLookupWorker to run Outlook lookup off-thread"
```

---

### Task 4: `calendar_event.json` read/write + `RecordingHeader` display

**Files:**
- Modify: `app/ui/recording_header.py`
- Test: `tests/test_recording_header.py`

**Interfaces:**
- Produces: `_format_calendar_line(calendar_event: dict) -> str` (pure helper — e.g.
  `"📅 Q3 Roadmap Sync · 3 attendees"`), and `RecordingHeader.set_recording(metadata,
  speaker_count=0, calendar_event=None)` gains an optional third argument; when given a non-None
  dict it shows the formatted line beneath the existing info line, and clears/hides it when
  `None`.
- Consumes: nothing new from other tasks — `calendar_event.json`'s schema
  (`subject`/`start`/`end`/`organizer`/`attendees`) is defined here and used verbatim by
  Task 6 (writer) and Task 10 (loader in `main_window.py`).

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_recording_header.py`:

```python
    def test_format_calendar_line_with_attendees(self):
        from app.ui.recording_header import _format_calendar_line
        event = {"subject": "Q3 Roadmap Sync", "attendees": ["Jane", "John", "Priya"]}
        self.assertEqual(
            _format_calendar_line(event), "\U0001F4C5 Q3 Roadmap Sync \u00b7 3 attendees"
        )

    def test_format_calendar_line_singular_attendee(self):
        from app.ui.recording_header import _format_calendar_line
        event = {"subject": "1:1", "attendees": ["Jane"]}
        self.assertEqual(_format_calendar_line(event), "\U0001F4C5 1:1 \u00b7 1 attendee")

    def test_format_calendar_line_no_attendees(self):
        from app.ui.recording_header import _format_calendar_line
        event = {"subject": "Focus Block", "attendees": []}
        self.assertEqual(_format_calendar_line(event), "\U0001F4C5 Focus Block")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv\Scripts\python.exe -m pytest tests/test_recording_header.py -v -k calendar_line`
Expected: FAIL — `ImportError: cannot import name '_format_calendar_line'`

- [ ] **Step 3: Implement `_format_calendar_line` and wire it into the widget**

In `app/ui/recording_header.py`, add near the other module-level helpers (after
`_format_duration`):

```python
def _format_calendar_line(calendar_event):
    """Format a calendar_event.json dict as a display line."""
    subject = calendar_event.get("subject", "")
    attendees = calendar_event.get("attendees", [])
    line = f"\U0001F4C5 {subject}"
    if attendees:
        count = len(attendees)
        noun = "attendee" if count == 1 else "attendees"
        line += f" \u00b7 {count} {noun}"
    return line
```

Modify `RecordingHeader.__init__`'s `_setup_ui` to add a third label after `self.info_label`:

```python
        self.calendar_label = QLabel("")
        self.calendar_label.setObjectName("recordingCalendarInfo")
        self.calendar_label.setStyleSheet("color: #89b4fa; font-size: 12px;")
        self.calendar_label.hide()
        layout.addWidget(self.calendar_label)
```

Modify `set_recording` to accept and use the new parameter:

```python
    def set_recording(self, metadata, speaker_count=0, calendar_event=None):
        """Display info for the given recording metadata."""
        self._metadata = metadata
        if metadata is None:
            self.hide()
            return
        ...  # existing body unchanged up to the end of the method
        if calendar_event:
            self.calendar_label.setText(_format_calendar_line(calendar_event))
            self.calendar_label.show()
        else:
            self.calendar_label.clear()
            self.calendar_label.hide()
```

(Insert the `if calendar_event:` block at the end of the existing method body, after
`self.info_label.setText(...)`.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv\Scripts\python.exe -m pytest tests/test_recording_header.py -v`
Expected: PASS (all, including the 3 new ones)

- [ ] **Step 5: Smoke test the widget still constructs**

Run: `.venv\Scripts\python.exe -c "import app.ui.recording_header"`
Expected: no traceback

- [ ] **Step 6: Run full suite and commit**

Run: `.venv\Scripts\python.exe -m pytest tests/ -v`
Expected: all pass

```bash
git add app/ui/recording_header.py tests/test_recording_header.py
git commit -m "ui: show tagged calendar event in RecordingHeader"
```

---

### Task 5: Calendar suggestion banner widget

**Files:**
- Create: `app/ui/calendar_banner.py`

**Interfaces:**
- Produces: `CalendarSuggestionBanner(QWidget)` with:
  - `show_matches(events: list[dict])` — populates the banner with one row per event
    (`subject`/`start`/`end`/`organizer` formatted as `"HH:MM–HH:MM"`), each with its own
    **Tag** button, plus one **Dismiss** button for the whole banner. Calls `self.show()`.
    If `events` is empty, calls `self.hide()` and does nothing else.
  - `tag_requested = pyqtSignal(dict)` — emitted with the chosen event dict when a **Tag**
    button is clicked; the banner then hides itself.
  - `dismissed = pyqtSignal()` — emitted when **Dismiss** is clicked; the banner then hides
    itself.
  - `hide_and_clear()` — hides the banner and clears its rows (used by `MainWindow` when
    switching recordings, so a stale banner from a previous session can't linger).

No automated test — pure Qt widget with no logic beyond signal wiring; the formatting logic it
needs (`"HH:MM"` time formatting) is a one-liner using `datetime.strftime`, not worth extracting.
Verified via manual smoke test.

- [ ] **Step 1: Write the implementation**

```python
"""Banner suggesting a calendar-event tag for a finished recording."""
from PyQt6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QFrame
from PyQt6.QtCore import Qt, pyqtSignal


class CalendarSuggestionBanner(QWidget):
    """Shows overlapping calendar events with per-event Tag buttons."""

    tag_requested = pyqtSignal(dict)   # the chosen event dict
    dismissed = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._events = []
        self._setup_ui()
        self.hide()

    def _setup_ui(self):
        self._frame = QFrame(self)
        self._frame.setObjectName("calendarBanner")
        self._frame.setStyleSheet(
            "#calendarBanner { background-color: #313244; border-radius: 4px; }"
        )
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 8)
        outer.addWidget(self._frame)

        self._layout = QVBoxLayout(self._frame)
        self._layout.setContentsMargins(10, 8, 10, 8)
        self._layout.setSpacing(4)

        self._title_label = QLabel("Calendar match found")
        self._title_label.setStyleSheet("font-weight: bold; color: #89b4fa;")
        self._layout.addWidget(self._title_label)

        self._rows_container = QWidget()
        self._rows_layout = QVBoxLayout(self._rows_container)
        self._rows_layout.setContentsMargins(0, 0, 0, 0)
        self._layout.addWidget(self._rows_container)

        dismiss_row = QHBoxLayout()
        dismiss_row.addStretch()
        self._dismiss_btn = QPushButton("Dismiss")
        self._dismiss_btn.clicked.connect(self._on_dismiss)
        dismiss_row.addWidget(self._dismiss_btn)
        self._layout.addLayout(dismiss_row)

    def show_matches(self, events):
        self._events = events
        while self._rows_layout.count():
            item = self._rows_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        if not events:
            self.hide()
            return

        for event in events:
            row = QHBoxLayout()
            start_str = event["start"].strftime("%H:%M")
            end_str = event["end"].strftime("%H:%M")
            organizer = event.get("organizer", "")
            text = f'"{event["subject"]}"  \u00b7  {start_str}\u2013{end_str}'
            if organizer:
                text += f"  \u00b7  {organizer}"
            label = QLabel(text)
            label.setStyleSheet("color: #cdd6f4;")
            row.addWidget(label, 1)

            tag_btn = QPushButton("Tag Recording")
            tag_btn.clicked.connect(lambda checked=False, e=event: self._on_tag(e))
            row.addWidget(tag_btn)

            row_widget = QWidget()
            row_widget.setLayout(row)
            self._rows_layout.addWidget(row_widget)

        self.show()

    def hide_and_clear(self):
        self._events = []
        while self._rows_layout.count():
            item = self._rows_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self.hide()

    def _on_tag(self, event):
        self.tag_requested.emit(event)
        self.hide_and_clear()

    def _on_dismiss(self):
        self.dismissed.emit()
        self.hide_and_clear()
```

- [ ] **Step 2: Smoke test construction and signal wiring**

Run:
```bash
.venv\Scripts\python.exe -c "
from PyQt6.QtWidgets import QApplication
from app.ui.calendar_banner import CalendarSuggestionBanner
from datetime import datetime
app = QApplication([])
b = CalendarSuggestionBanner()
b.show_matches([{'subject': 'Test', 'start': datetime.now(), 'end': datetime.now(), 'organizer': 'X', 'attendees': ['A']}])
print('rows:', b._rows_layout.count(), 'visible:', b.isVisible())
b.show_matches([])
print('empty hides:', not b.isVisible())
"
```
Expected: `rows: 1 visible: True` then `empty hides: True`, no traceback.

- [ ] **Step 3: Run full suite (regression check) and commit**

Run: `.venv\Scripts\python.exe -m pytest tests/ -v`
Expected: all pass

```bash
git add app/ui/calendar_banner.py
git commit -m "feat: add calendar suggestion banner widget"
```

---

### Task 6: Wire calendar lookup + banner into `MainWindow` for live recordings

**Files:**
- Modify: `app/main_window.py`

**Interfaces:**
- Consumes: `CalendarLookupWorker` (Task 3), `CalendarSuggestionBanner` (Task 5),
  `config.get("calendar", "enabled")` (Task 1), `atomic_write_json` (existing,
  `app/utils/atomic_io.py`).
- Produces: `MainWindow._maybe_lookup_calendar(session)` — callable other tasks (Task 14, for
  imports) also call; `MainWindow._calendar_lookup_worker` attribute; writes
  `calendar_event.json` with keys `subject: str`, `start: <iso str>`, `end: <iso str>`,
  `organizer: str`, `attendees: list[str]` (note: `datetime` objects from
  `find_overlapping_events` are serialized with `.isoformat()` here before writing).

- [ ] **Step 1: Add imports and instantiate the banner in `_setup_ui`**

Near the top of `app/main_window.py`, add:

```python
from app.ui.calendar_banner import CalendarSuggestionBanner
from app.ui.calendar_lookup_worker import CalendarLookupWorker
from app.utils.atomic_io import atomic_write_json
```

(`atomic_write_json` may already be imported locally inside `Recorder.stop_recording` — this is
a separate top-level import for `main_window.py`; check the existing import block near the top of
the file and add it there if not already present at module level.)

In `_setup_ui`, right after the line `right_layout.addWidget(self.recording_header)` (around
line 245), add:

```python
        self.calendar_banner = CalendarSuggestionBanner()
        self.calendar_banner.tag_requested.connect(self._on_calendar_tag_requested)
        self.calendar_banner.dismissed.connect(self._on_calendar_dismissed)
        right_layout.addWidget(self.calendar_banner)
```

Also initialize the worker attribute in `__init__` alongside the other worker attributes (search
for `self._transcription_worker = None` and add nearby):

```python
        self._calendar_lookup_worker = None
```

- [ ] **Step 2: Add the dispatch method**

Add a new method to `MainWindow` (near `_maybe_auto_summarize`, since it's another
post-recording optional step):

```python
    def _maybe_lookup_calendar(self, session):
        """Kick off an off-thread Outlook calendar lookup for this session,
        if the feature is enabled. Best-effort — no-op on any failure,
        never surfaces an error to the user (see outlook_calendar.py)."""
        if not self.config.get("calendar", "enabled"):
            return
        if session is None:
            return
        if session.get("calendar_prompt_dismissed"):
            return
        session_dir = session.get("directory")
        if session_dir and (Path(session_dir) / "calendar_event.json").exists():
            return  # already tagged
        started = session.get("started_at")
        stopped = session.get("stopped_at")
        if not started or not stopped:
            return
        from datetime import datetime
        try:
            started_dt = datetime.fromisoformat(started)
            stopped_dt = datetime.fromisoformat(stopped)
        except ValueError:
            return

        self._calendar_lookup_worker = CalendarLookupWorker(started_dt, stopped_dt)
        self._calendar_lookup_worker.session = session
        self._calendar_lookup_worker.finished.connect(self._on_calendar_lookup_finished)
        self._calendar_lookup_worker.start()

    def _on_calendar_lookup_finished(self, events):
        worker = self._calendar_lookup_worker
        session = getattr(worker, "session", None) if worker else None
        if not events or session is None:
            return
        if not self._is_current_session(session):
            return  # user switched recordings — don't surface a stale banner
        # Unlike QMessageBox, the banner is a normal child widget embedded in
        # the window layout — no tray-hidden special-casing needed. Calling
        # show_matches() while the main window is hidden to tray just leaves
        # the banner visible-but-unseen until the window is next shown, same
        # as the recording header or transcript already sitting there.
        self.calendar_banner.show_matches(events)

    def _on_calendar_tag_requested(self, event):
        if not self._current_session:
            return
        session_dir = Path(self._current_session["directory"])
        event_to_save = dict(event)
        event_to_save["start"] = event["start"].isoformat()
        event_to_save["end"] = event["end"].isoformat()
        atomic_write_json(session_dir / "calendar_event.json", event_to_save, indent=2)
        self.recording_header.set_recording(
            self._current_session,
            speaker_count=self.transcript_viewer.get_speaker_count(),
            calendar_event=event_to_save,
        )
        self._calendar_attendees = event_to_save.get("attendees", [])
        self.transcript_viewer.set_calendar_attendees(self._calendar_attendees)

    def _on_calendar_dismissed(self):
        if not self._current_session:
            return
        self._current_session["calendar_prompt_dismissed"] = True
        session_dir = Path(self._current_session["directory"])
        meta_path = session_dir / "metadata.json"
        if meta_path.exists():
            atomic_write_json(meta_path, self._current_session, indent=2)
```

(`self.transcript_viewer.set_calendar_attendees` is added in Task 9 — this task can reference it
now since Task 9 lands before this wiring is exercised end-to-end in Task 10; if executed
strictly in order, this line is added here as dead code until Task 9 lands, which is fine since
it's inside a method, not evaluated at import time.)

- [ ] **Step 3: Call the dispatch from `_on_recording_finished`**

In `_on_recording_finished` (around line 696), after the existing auto-transcription branching
(right before the method ends), add:

```python
        self._maybe_lookup_calendar(session)
```

- [ ] **Step 4: Smoke test the file still imports and constructs**

Run: `.venv\Scripts\python.exe -c "import app.main_window"`
Expected: no traceback (this will fail loudly if there's a syntax error or bad reference — it
won't catch every wiring issue since `MainWindow` isn't instantiated, but confirms the module is
well-formed).

- [ ] **Step 5: Run full suite and commit**

Run: `.venv\Scripts\python.exe -m pytest tests/ -v`
Expected: all pass (no existing tests should be affected — this is purely additive wiring)

```bash
git add app/main_window.py
git commit -m "feat: dispatch calendar lookup after recording, show suggestion banner"
```

---

### Task 7: Settings dialog — Calendar tab

**Files:**
- Modify: `app/ui/settings_dialog.py`

**Interfaces:**
- Produces: `SettingsDialog.calendar_enabled_cb` (`QCheckBox`), wired to
  `config.get/set("calendar", "enabled")` following the exact same load/save pattern as
  `auto_summarize_cb`.

No automated test — `SettingsDialog` has no existing test file and is pure Qt wiring (confirmed:
`ls tests/` has no `test_settings_dialog.py`). Verified via smoke test.

- [ ] **Step 1: Add the tab in `_setup_ui`**

In `app/ui/settings_dialog.py`, after the existing AI tab block (search for
`tabs.addTab(ai_tab, "AI Assistant")` — this is the last tab added), add:

```python
        # Calendar Tab
        calendar_tab = QWidget()
        calendar_layout = QVBoxLayout(calendar_tab)

        calendar_group = QGroupBox("Outlook Calendar")
        calendar_form = QFormLayout(calendar_group)

        self.calendar_enabled_cb = QCheckBox("Suggest calendar tags for finished recordings")
        self.calendar_enabled_cb.setToolTip(
            "After a recording finishes, check the local Outlook desktop\n"
            "app's calendar for an overlapping event and offer to tag the\n"
            "recording with its subject, organizer, and attendees.\n"
            "Requires Outlook desktop to be installed and configured — no\n"
            "cloud sign-in, no internet call."
        )
        calendar_form.addRow(self.calendar_enabled_cb)

        calendar_layout.addWidget(calendar_group)
        calendar_layout.addStretch()

        tabs.addTab(calendar_tab, "Calendar")
```

- [ ] **Step 2: Wire load/save**

In `_load_settings` (around line 366), add near the other checkbox loads:

```python
        self.calendar_enabled_cb.setChecked(self.config.get("calendar", "enabled"))
```

In the save method (the one containing the `self.config.set("ai", "provider_settings", ...)`
line around 514 — find the surrounding method, likely named `accept` or `_save_settings`), add:

```python
        self.config.set("calendar", "enabled", self.calendar_enabled_cb.isChecked())
```

- [ ] **Step 3: Smoke test**

Run:
```bash
.venv\Scripts\python.exe -c "
from PyQt6.QtWidgets import QApplication
from app.utils.config import Config
from app.ui.settings_dialog import SettingsDialog
app = QApplication([])
cfg = Config()
dlg = SettingsDialog(cfg)
print('checkbox present:', hasattr(dlg, 'calendar_enabled_cb'))
print('unchecked by default:', not dlg.calendar_enabled_cb.isChecked())
"
```
Expected: both `True` lines print, no traceback.

- [ ] **Step 4: Run full suite and commit**

Run: `.venv\Scripts\python.exe -m pytest tests/ -v`
Expected: all pass

```bash
git add app/ui/settings_dialog.py
git commit -m "ui: add Calendar settings tab with opt-in toggle"
```

---

### Task 8: `SpeakerNamePanel` — attendee dropdown with mutual exclusion

**Files:**
- Modify: `app/ui/speaker_name_panel.py`
- Test: `tests/test_speaker_name_panel.py`

**Interfaces:**
- Produces:
  - `_available_options(speaker_id, speaker_ids, current_selections, attendees) -> list[str]`
    (pure function, exported for direct testing) — returns `[""] + attendees not selected
    elsewhere`, always keeping `current_selections.get(speaker_id, "")` available in its own
    list even if it happens to equal an attendee name.
  - `SpeakerNamePanel.set_speakers(segments, speaker_names=None, attendees=None)` — new optional
    third parameter. When `attendees` is falsy, behavior is byte-for-byte unchanged (plain
    `QLineEdit` rows). When truthy, rows are editable `QComboBox` instead.
  - `SpeakerNamePanel.get_speaker_names()` — unchanged signature and return type; internally
    reads `.currentText()` for combo rows instead of `.text()`.

- [ ] **Step 1: Write the failing tests for the pure mutual-exclusion function**

Add to `tests/test_speaker_name_panel.py` (keep it in the existing `unittest.TestCase` class or
add a sibling class — follow the file's existing flat style):

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv\Scripts\python.exe -m pytest tests/test_speaker_name_panel.py -v -k AvailableOptions`
Expected: FAIL — `ImportError: cannot import name '_available_options'`

- [ ] **Step 3: Implement `_available_options`**

In `app/ui/speaker_name_panel.py`, add near `_extract_speakers`:

```python
def _available_options(speaker_id, speaker_ids, current_selections, attendees):
    """Attendee names selectable for speaker_id's dropdown: blank + every
    attendee not already assigned to a DIFFERENT speaker. speaker_id's own
    current selection (if any) always stays available in its own list."""
    own_selection = current_selections.get(speaker_id, "")
    taken_elsewhere = {
        name for sid, name in current_selections.items()
        if sid != speaker_id and name
    }
    available = [a for a in attendees if a not in taken_elsewhere]
    return [""] + available
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv\Scripts\python.exe -m pytest tests/test_speaker_name_panel.py -v -k AvailableOptions`
Expected: PASS (4 tests)

- [ ] **Step 5: Wire the pure function into the widget**

Replace the `set_speakers` method and the row-building loop in `app/ui/speaker_name_panel.py` to
support both modes. Update `__init__` to track attendees and add a helper for row rebuilding:

```python
    def __init__(self, config=None, parent=None):
        super().__init__(parent)
        self._config = config
        self._speaker_ids = []
        self._name_edits = {}      # speaker_id -> QLineEdit (no-attendee mode)
        self._name_combos = {}     # speaker_id -> QComboBox (attendee mode)
        self._speaker_names = {}   # speaker_id -> name str
        self._attendees = []
        self._collapsed = config.get("ui", "speakers_collapsed") if config else False
        self._setup_ui()
        self.hide()
```

Modify `set_speakers` signature and body — replace the existing method with:

```python
    def set_speakers(self, segments, speaker_names=None, attendees=None):
        """Populate panel from transcript segments and optional existing names.

        Args:
            segments: list of TranscriptSegment
            speaker_names: dict of {speaker_id: name} or None
            attendees: optional list of calendar attendee names. When
                provided (non-empty), rows become mutually-exclusive
                dropdowns instead of free-text fields.
        """
        self._speaker_ids = _extract_speakers(segments)
        self._speaker_names = dict(speaker_names) if speaker_names else {}
        self._attendees = list(attendees) if attendees else []

        if not self._speaker_ids:
            self.hide()
            return

        self.show()
        arrow = "\u25b6" if self._collapsed else "\u25bc"
        self._toggle_btn.setText(f"{arrow} Speakers ({len(self._speaker_ids)} detected)")
        self._rows_container.setVisible(not self._collapsed)

        self._name_edits.clear()
        self._name_combos.clear()
        while self._rows_layout.count():
            item = self._rows_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        for i, speaker_id in enumerate(self._speaker_ids):
            row_widget = QWidget()
            row_layout = QHBoxLayout(row_widget)
            row_layout.setContentsMargins(0, 2, 0, 2)

            color = SPEAKER_COLORS[i % len(SPEAKER_COLORS)]
            swatch = QLabel("\u25cf")
            swatch.setStyleSheet(f"color: {color}; font-size: 16px;")
            swatch.setFixedWidth(20)
            row_layout.addWidget(swatch)

            id_label = QLabel(speaker_id)
            id_label.setStyleSheet("color: #a6adc8; font-size: 12px;")
            id_label.setFixedWidth(100)
            row_layout.addWidget(id_label)

            arrow_label = QLabel("\u2192")
            arrow_label.setStyleSheet("color: #585b70;")
            arrow_label.setFixedWidth(20)
            row_layout.addWidget(arrow_label)

            existing_name = self._speaker_names.get(speaker_id, "")

            if self._attendees:
                combo = QComboBox()
                combo.setEditable(True)
                combo.setMaximumHeight(28)
                options = _available_options(
                    speaker_id, self._speaker_ids, self._speaker_names, self._attendees
                )
                combo.addItems(options)
                if existing_name:
                    combo.setCurrentText(existing_name)
                combo.currentTextChanged.connect(
                    lambda text, sid=speaker_id: self._on_combo_changed(sid, text)
                )
                row_layout.addWidget(combo)
                self._name_combos[speaker_id] = combo
            else:
                name_edit = QLineEdit()
                name_edit.setPlaceholderText("Enter name...")
                name_edit.setMaximumHeight(28)
                if existing_name:
                    name_edit.setText(existing_name)
                name_edit.textChanged.connect(self._on_name_changed)
                row_layout.addWidget(name_edit)
                self._name_edits[speaker_id] = name_edit

            self._rows_layout.addWidget(row_widget)
```

Add the combo-change handler and update `get_speaker_names` and `focus_speaker`:

```python
    def _on_combo_changed(self, speaker_id, text):
        self._speaker_names[speaker_id] = text.strip()
        self._refresh_combo_options()
        self.names_changed.emit(self.get_speaker_names())

    def _refresh_combo_options(self):
        for speaker_id, combo in self._name_combos.items():
            options = _available_options(
                speaker_id, self._speaker_ids, self._speaker_names, self._attendees
            )
            current = combo.currentText()
            combo.blockSignals(True)
            combo.clear()
            combo.addItems(options)
            combo.setCurrentText(current)
            combo.blockSignals(False)

    def get_speaker_names(self):
        """Return current speaker name mappings (only non-empty names)."""
        names = {}
        for speaker_id, edit in self._name_edits.items():
            name = edit.text().strip()
            if name:
                names[speaker_id] = name
        for speaker_id, combo in self._name_combos.items():
            name = combo.currentText().strip()
            if name:
                names[speaker_id] = name
        return names

    def focus_speaker(self, speaker_id):
        """Focus the name field for the given speaker ID."""
        if self._collapsed:
            self._toggle_collapsed()
        if speaker_id in self._name_edits:
            self._name_edits[speaker_id].setFocus()
            self._name_edits[speaker_id].selectAll()
        elif speaker_id in self._name_combos:
            self._name_combos[speaker_id].setFocus()
```

Add `QComboBox` to the top-of-file import:

```python
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton, QComboBox
)
```

- [ ] **Step 6: Smoke test both modes**

Run:
```bash
.venv\Scripts\python.exe -c "
from PyQt6.QtWidgets import QApplication
from app.ui.speaker_name_panel import SpeakerNamePanel
from app.transcription.transcriber import TranscriptSegment
app = QApplication([])
segs = [TranscriptSegment(start=0, end=1, text='hi', speaker='SPEAKER_00'),
        TranscriptSegment(start=1, end=2, text='hey', speaker='SPEAKER_01')]
p = SpeakerNamePanel()
p.set_speakers(segs, {}, attendees=['Jane', 'John'])
print('combo mode rows:', len(p._name_combos), len(p._name_edits))
p.set_speakers(segs, {})
print('line-edit mode rows:', len(p._name_combos), len(p._name_edits))
"
```
Expected: `combo mode rows: 2 0` then `line-edit mode rows: 0 2`, no traceback.

- [ ] **Step 7: Run full suite and commit**

Run: `.venv\Scripts\python.exe -m pytest tests/ -v`
Expected: all pass

```bash
git add app/ui/speaker_name_panel.py tests/test_speaker_name_panel.py
git commit -m "feat: attendee dropdown with mutual exclusion in SpeakerNamePanel"
```

---

### Task 9: Plumb attendees through `TranscriptViewer`

**Files:**
- Modify: `app/ui/transcript_viewer.py`

**Interfaces:**
- Consumes: `SpeakerNamePanel.set_speakers(segments, speaker_names, attendees)` (Task 8).
- Produces: `TranscriptViewer.set_calendar_attendees(attendees: list[str])` — stores the list and,
  if a transcript is currently displayed, immediately re-populates the speaker panel with it.
  `TranscriptViewer.display_transcript(transcript, speaker_names=None, attendees=None)` gains an
  optional `attendees` parameter (defaults to the viewer's currently-stored list when omitted, so
  existing call sites that don't pass it keep working).

- [ ] **Step 1: Add the attendee-storage and passthrough**

In `app/ui/transcript_viewer.py`'s `__init__`, add near the other instance attributes (search for
`self._speaker_names = {}` inside `__init__` or `clear`, and add alongside):

```python
        self._calendar_attendees = []
```

Modify `display_transcript`'s signature and the `speaker_panel.set_speakers` call:

```python
    def display_transcript(self, transcript, speaker_names=None, attendees=None):
```

and change the existing line

```python
        self.speaker_panel.set_speakers(transcript.segments, self._speaker_names)
```

to:

```python
        if attendees is not None:
            self._calendar_attendees = attendees
        self.speaker_panel.set_speakers(
            transcript.segments, self._speaker_names, attendees=self._calendar_attendees
        )
```

Add the new public method (place it near `set_audio_path`):

```python
    def set_calendar_attendees(self, attendees):
        """Update the attendee list used for speaker-naming dropdowns and
        refresh the panel immediately if a transcript is already shown."""
        self._calendar_attendees = list(attendees) if attendees else []
        if self._transcript is not None:
            self.speaker_panel.set_speakers(
                self._transcript.segments, self._speaker_names,
                attendees=self._calendar_attendees
            )
```

In `clear()`, reset the attendee list alongside the other resets (find
`self._speaker_names = {}` in `clear` and add next to it):

```python
        self._calendar_attendees = []
```

- [ ] **Step 2: Smoke test**

Run: `.venv\Scripts\python.exe -c "
from PyQt6.QtWidgets import QApplication
from app.ui.transcript_viewer import TranscriptViewer
app = QApplication([])
v = TranscriptViewer()
v.set_calendar_attendees(['Jane', 'John'])
print('stored:', v._calendar_attendees)
print('has method:', hasattr(v, 'set_calendar_attendees'))
"`
Expected: `stored: ['Jane', 'John']` then `has method: True`, no traceback.

- [ ] **Step 3: Run full suite and commit**

Run: `.venv\Scripts\python.exe -m pytest tests/ -v`
Expected: all pass

```bash
git add app/ui/transcript_viewer.py
git commit -m "feat: plumb calendar attendees into TranscriptViewer speaker panel"
```

---

### Task 10: Load `calendar_event.json` on transcript display in `MainWindow`

**Files:**
- Modify: `app/main_window.py`

**Interfaces:**
- Consumes: `TranscriptViewer.display_transcript(..., attendees=[...])` (Task 9),
  `RecordingHeader.set_recording(..., calendar_event=...)` (Task 4).

- [ ] **Step 1: Load the file and pass it through in `_display_final_transcript`**

In `app/main_window.py`'s `_display_final_transcript` (around line 935), find the block that
loads `speaker_names.json`:

```python
        speaker_names = {}
        if self._current_session:
            names_path = Path(self._current_session["directory"]) / "speaker_names.json"
            if names_path.exists():
                try:
                    with open(names_path, "r", encoding="utf-8") as f:
                        speaker_names = json.load(f)
                except (json.JSONDecodeError, OSError):
                    pass
```

Add directly after it:

```python
        calendar_event = None
        calendar_attendees = []
        if self._current_session:
            calendar_path = Path(self._current_session["directory"]) / "calendar_event.json"
            if calendar_path.exists():
                try:
                    with open(calendar_path, "r", encoding="utf-8") as f:
                        calendar_event = json.load(f)
                    calendar_attendees = calendar_event.get("attendees", [])
                except (json.JSONDecodeError, OSError):
                    pass
```

Then change:

```python
        self.transcript_viewer.display_transcript(result, speaker_names=speaker_names)
```

to:

```python
        self.transcript_viewer.display_transcript(
            result, speaker_names=speaker_names, attendees=calendar_attendees
        )
```

And change the existing `recording_header.set_recording(...)` call a few lines below to pass the
loaded event:

```python
        if self._current_session:
            self.recording_header.set_recording(
                self._current_session,
                speaker_count=self.transcript_viewer.get_speaker_count(),
                calendar_event=calendar_event,
            )
```

- [ ] **Step 2: Smoke test the module still imports**

Run: `.venv\Scripts\python.exe -c "import app.main_window"`
Expected: no traceback

- [ ] **Step 3: Run full suite and commit**

Run: `.venv\Scripts\python.exe -m pytest tests/ -v`
Expected: all pass

```bash
git add app/main_window.py
git commit -m "feat: load tagged calendar event into header and speaker panel on display"
```

---

### Task 11: `import_session.py` — pure metadata/probing logic

**Files:**
- Create: `app/recording/import_session.py`
- Test: `tests/test_import_session.py`

**Interfaces:**
- Produces:
  - `build_import_metadata(source_path: str, session_dir: str, started_at: datetime, duration: float, audio_filename: str) -> dict`
    (pure function) — returns the exact `metadata.json` dict described in the spec: `directory`,
    `started_at`, `stopped_at` (`started_at + duration`), `duration`, `audio_files`, `imported`,
    `source_filename`, `capture_mode`.
  - `needs_conversion(source_path: str) -> bool` — `True` only for `.m4a` (case-insensitive).
  - This task does NOT implement the file-copy/ffmpeg-subprocess/duration-probing I/O — that's
    Task 12's `MainWindow`/dialog wiring, which calls these pure helpers plus `soundfile.info`
    and `subprocess.run` directly (mirroring `Recorder._convert_to_mp3`'s inline style, since
    that's the established pattern for this one-off conversion, not a reason to build a new
    abstraction).

- [ ] **Step 1: Write the failing tests**

Create `tests/test_import_session.py`:

```python
"""Tests for recording-import metadata construction."""
import unittest
from datetime import datetime


class TestBuildImportMetadata(unittest.TestCase):
    def test_computes_stopped_at_from_duration(self):
        from app.recording.import_session import build_import_metadata
        meta = build_import_metadata(
            source_path="C:/Downloads/call.wav",
            session_dir="C:/recordings/recording_20260813_140000",
            started_at=datetime(2026, 8, 13, 14, 0, 0),
            duration=90.0,
            audio_filename="combined_audio.wav",
        )
        self.assertEqual(meta["started_at"], "2026-08-13T14:00:00")
        self.assertEqual(meta["stopped_at"], "2026-08-13T14:01:30")
        self.assertEqual(meta["duration"], 90.0)

    def test_marks_session_as_imported(self):
        from app.recording.import_session import build_import_metadata
        meta = build_import_metadata(
            source_path="C:/Downloads/call.m4a",
            session_dir="C:/recordings/recording_20260813_140000",
            started_at=datetime(2026, 8, 13, 14, 0, 0),
            duration=60.0,
            audio_filename="combined_audio.wav",
        )
        self.assertTrue(meta["imported"])
        self.assertEqual(meta["capture_mode"], "imported")
        self.assertEqual(meta["source_filename"], "call.m4a")

    def test_audio_files_points_at_combined_track(self):
        from app.recording.import_session import build_import_metadata
        meta = build_import_metadata(
            source_path="C:/Downloads/call.wav",
            session_dir="C:/recordings/recording_20260813_140000",
            started_at=datetime(2026, 8, 13, 14, 0, 0),
            duration=60.0,
            audio_filename="combined_audio.wav",
        )
        self.assertEqual(
            meta["audio_files"],
            {"combined": "C:/recordings/recording_20260813_140000/combined_audio.wav"},
        )
        self.assertEqual(meta["directory"], "C:/recordings/recording_20260813_140000")


class TestNeedsConversion(unittest.TestCase):
    def test_m4a_needs_conversion(self):
        from app.recording.import_session import needs_conversion
        self.assertTrue(needs_conversion("C:/Downloads/call.m4a"))
        self.assertTrue(needs_conversion("C:/Downloads/CALL.M4A"))

    def test_wav_and_mp3_do_not(self):
        from app.recording.import_session import needs_conversion
        self.assertFalse(needs_conversion("C:/Downloads/call.wav"))
        self.assertFalse(needs_conversion("C:/Downloads/call.mp3"))


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv\Scripts\python.exe -m pytest tests/test_import_session.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.recording.import_session'`

- [ ] **Step 3: Implement**

Create `app/recording/import_session.py`:

```python
"""Pure logic for constructing an imported recording's session metadata.

The actual file I/O (copy, ffmpeg conversion, duration probing) lives in
main_window.py's import flow, mirroring Recorder._convert_to_mp3's inline
subprocess style rather than introducing a second I/O abstraction.
"""
from pathlib import Path
from datetime import timedelta


def needs_conversion(source_path):
    """True if the source file must be converted to WAV before use."""
    return Path(source_path).suffix.lower() == ".m4a"


def build_import_metadata(source_path, session_dir, started_at, duration, audio_filename):
    """Build the metadata.json dict for a newly-imported recording session.

    Args:
        source_path: original file path the user picked (for source_filename).
        session_dir: the new session directory (str).
        started_at: user-confirmed recording start (datetime).
        duration: probed audio duration in seconds (float).
        audio_filename: filename of the (possibly converted) audio file
            written into session_dir, e.g. "combined_audio.wav".
    """
    stopped_at = started_at + timedelta(seconds=duration)
    session_dir_str = str(session_dir).replace("\\", "/")
    audio_path = f"{session_dir_str}/{audio_filename}"
    return {
        "directory": session_dir_str,
        "started_at": started_at.isoformat(),
        "stopped_at": stopped_at.isoformat(),
        "duration": duration,
        "audio_files": {"combined": audio_path},
        "imported": True,
        "source_filename": Path(source_path).name,
        "capture_mode": "imported",
    }
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv\Scripts\python.exe -m pytest tests/test_import_session.py -v`
Expected: PASS (5 tests)

- [ ] **Step 5: Run full suite and commit**

Run: `.venv\Scripts\python.exe -m pytest tests/ -v`
Expected: all pass

```bash
git add app/recording/import_session.py tests/test_import_session.py
git commit -m "feat: add pure metadata builder for imported recordings"
```

---

### Task 12: Import timestamp dialog

**Files:**
- Create: `app/ui/import_timestamp_dialog.py`

**Interfaces:**
- Produces: `ImportTimestampDialog(default_datetime: datetime, parent=None)` — a `QDialog` with a
  `QDateTimeEdit` pre-filled from `default_datetime`, OK/Cancel buttons. Standard usage:
  `dlg = ImportTimestampDialog(mtime_dt); if dlg.exec(): result = dlg.selected_datetime()` where
  `selected_datetime() -> datetime` returns the (possibly edited) value.

No automated test — thin `QDialog` wrapper around `QDateTimeEdit`, no branching logic. Verified
via smoke test.

- [ ] **Step 1: Write the implementation**

```python
"""Modal dialog for confirming/editing an imported recording's start time."""
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QFormLayout, QDateTimeEdit, QDialogButtonBox, QLabel
)
from PyQt6.QtCore import QDateTime


class ImportTimestampDialog(QDialog):
    """Asks the user to confirm when an imported recording actually happened."""

    def __init__(self, default_datetime, parent=None):
        super().__init__(parent)
        self.setWindowTitle("When was this recorded?")
        layout = QVBoxLayout(self)

        hint = QLabel(
            "Pre-filled from the file's last-modified time — adjust if that's not "
            "when the call actually happened. This is used to match a calendar "
            "event, if calendar tagging is enabled."
        )
        hint.setWordWrap(True)
        layout.addWidget(hint)

        form = QFormLayout()
        self._datetime_edit = QDateTimeEdit()
        self._datetime_edit.setCalendarPopup(True)
        self._datetime_edit.setDateTime(QDateTime(default_datetime))
        form.addRow("Recording start:", self._datetime_edit)
        layout.addLayout(form)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def selected_datetime(self):
        return self._datetime_edit.dateTime().toPyDateTime()
```

- [ ] **Step 2: Smoke test**

Run:
```bash
.venv\Scripts\python.exe -c "
from PyQt6.QtWidgets import QApplication
from app.ui.import_timestamp_dialog import ImportTimestampDialog
from datetime import datetime
app = QApplication([])
d = ImportTimestampDialog(datetime(2026, 8, 13, 14, 0))
print('prefilled:', d.selected_datetime())
"
```
Expected: `prefilled: 2026-08-13 14:00:00`, no traceback.

- [ ] **Step 3: Run full suite (regression check) and commit**

Run: `.venv\Scripts\python.exe -m pytest tests/ -v`
Expected: all pass

```bash
git add app/ui/import_timestamp_dialog.py
git commit -m "feat: add import timestamp confirmation dialog"
```

---

### Task 13: "Import..." button in `RecordingsList`

**Files:**
- Modify: `app/ui/recordings_list.py`

**Interfaces:**
- Produces: `RecordingsList.import_requested = pyqtSignal(str)` — emitted with the chosen file
  path when the user picks a file via the new **Import...** button. `RecordingsList` does not
  perform the import itself (no session/pipeline knowledge belongs in this widget) — it only
  surfaces the file choice; `MainWindow` (Task 14) does the rest.

- [ ] **Step 1: Add the button and signal**

In `app/ui/recordings_list.py`, add to the imports:

```python
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QListWidget, QListWidgetItem, QPushButton,
    QFileDialog,
)
```

(merge with whatever's already imported from `PyQt6.QtWidgets` at the top of the file — check
the existing import line and extend it rather than duplicating.)

Add the new signal at class level, alongside the existing ones:

```python
    import_requested = pyqtSignal(str)  # chosen audio file path
```

In `_setup_ui`, after the search bar is added (`layout.addWidget(self.search_bar)`), add:

```python
        import_row = QHBoxLayout()
        import_row.addStretch()
        self.import_btn = QPushButton("Import...")
        self.import_btn.setToolTip(
            "Import an existing audio file (wav/mp3/m4a) as a new recording, "
            "running it through transcription and diarization."
        )
        self.import_btn.clicked.connect(self._on_import_clicked)
        import_row.addWidget(self.import_btn)
        layout.addLayout(import_row)
```

Add the handler method:

```python
    def _on_import_clicked(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Import Recording", "",
            "Audio Files (*.wav *.mp3 *.m4a)"
        )
        if path:
            self.import_requested.emit(path)
```

- [ ] **Step 2: Smoke test**

Run:
```bash
.venv\Scripts\python.exe -c "
from PyQt6.QtWidgets import QApplication
from app.ui.recordings_list import RecordingsList
import tempfile
app = QApplication([])
r = RecordingsList(tempfile.mkdtemp())
print('button present:', hasattr(r, 'import_btn'))
print('signal present:', hasattr(r, 'import_requested'))
"
```
Expected: both `True`, no traceback.

- [ ] **Step 3: Run full suite and commit**

Run: `.venv\Scripts\python.exe -m pytest tests/ -v`
Expected: all pass

```bash
git add app/ui/recordings_list.py
git commit -m "ui: add Import... button to recordings list"
```

---

### Task 14: Wire the full import flow in `MainWindow`

**Files:**
- Modify: `app/main_window.py`

**Interfaces:**
- Consumes: `RecordingsList.import_requested` (Task 13), `ImportTimestampDialog` (Task 12),
  `build_import_metadata`/`needs_conversion` (Task 11), `_maybe_lookup_calendar` (Task 6),
  `_start_transcription` (existing).

- [ ] **Step 1: Add imports**

```python
from app.ui.import_timestamp_dialog import ImportTimestampDialog
from app.recording.import_session import build_import_metadata, needs_conversion
```

- [ ] **Step 2: Connect the signal**

Near the other `self.recordings_list.*.connect(...)` wiring in `_setup_ui` (search for where
`recordings_list` signals like `recording_selected` are connected — likely later in `_setup_ui`
or a `_connect_signals` method), add:

```python
        self.recordings_list.import_requested.connect(self._on_import_requested)
```

- [ ] **Step 3: Implement the import handler**

Add this method to `MainWindow` (place it near `_on_recording_finished`, since it produces the
same shape of session dict and reuses the same downstream pipeline):

```python
    def _on_import_requested(self, source_path):
        import os
        import shutil
        import subprocess
        import soundfile as sf
        from datetime import datetime
        from app.utils.atomic_io import atomic_write_json

        mtime = datetime.fromtimestamp(os.path.getmtime(source_path))
        dialog = ImportTimestampDialog(mtime, parent=self)
        if not dialog.exec():
            return
        started_at = dialog.selected_datetime()

        timestamp = started_at.strftime("%Y%m%d_%H%M%S")
        output_dir = Path(self.config.get("output", "directory"))
        session_dir = output_dir / f"recording_{timestamp}"
        session_dir.mkdir(parents=True, exist_ok=True)

        audio_filename = "combined_audio.wav"
        dest_path = session_dir / audio_filename

        try:
            if needs_conversion(source_path):
                if not shutil.which("ffmpeg"):
                    QMessageBox.warning(
                        self, "Import Failed",
                        "This file needs FFmpeg to convert from M4A, but FFmpeg "
                        "wasn't found on PATH. Install FFmpeg and try again, or "
                        "convert the file to WAV/MP3 first."
                    )
                    shutil.rmtree(session_dir, ignore_errors=True)
                    return
                subprocess.run(
                    ["ffmpeg", "-y", "-i", source_path, str(dest_path)],
                    capture_output=True, check=True, timeout=300,
                )
            else:
                shutil.copy2(source_path, dest_path)

            duration = sf.info(str(dest_path)).duration
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired,
                RuntimeError, OSError) as e:
            QMessageBox.warning(self, "Import Failed", f"Could not import file: {e}")
            shutil.rmtree(session_dir, ignore_errors=True)
            return

        session = build_import_metadata(
            source_path=source_path,
            session_dir=str(session_dir),
            started_at=started_at,
            duration=duration,
            audio_filename=audio_filename,
        )
        atomic_write_json(session_dir / "metadata.json", session, indent=2)

        self._on_recording_finished(session)
```

Note: `_on_recording_finished` already handles everything a live recording needs — transcript
viewer setup, recordings list refresh, tab switch, notes panel, auto-transcription dispatch
(reading `session["audio_files"]["combined"]`, which this task populates), and (after Task 6)
`self._maybe_lookup_calendar(session)`. No duplicate pipeline code is needed. The existing
diarization-mode selection in `_on_transcription_finished` (`app/main_window.py:810-830`) already
requires `audio_files.get("mic")` **and** `audio_files.get("system")` for simple diarization —
since imports only populate `audio_files["combined"]`, that branch is naturally skipped and
imports always fall through to full pyannote diarization (if configured) or an unlabeled
transcript, with no code change required there.

- [ ] **Step 4: Smoke test the module still imports**

Run: `.venv\Scripts\python.exe -c "import app.main_window"`
Expected: no traceback

- [ ] **Step 5: Run full suite and commit**

Run: `.venv\Scripts\python.exe -m pytest tests/ -v`
Expected: all pass

```bash
git add app/main_window.py
git commit -m "feat: wire end-to-end recording import flow"
```

---

### Task 15: Manual end-to-end smoke test + CLAUDE.md update

**Files:**
- Modify: `CLAUDE.md` (Current Features list, Project Structure tree)

This task has no code changes beyond documentation — it's a manual verification pass plus
keeping the project's own architecture doc in sync, matching this repo's existing convention of
an exhaustive, current `CLAUDE.md`.

- [ ] **Step 1: Launch the real app and verify Settings**

Run: `start_debug.bat` (or `.venv\Scripts\python.exe main.py` with console output). In Settings,
confirm the new **Calendar** tab appears with the unchecked "Suggest calendar tags..." checkbox,
and that toggling + saving + reopening Settings persists the value.

- [ ] **Step 2: Verify the Import button end-to-end**

Click **Import...** in the Recordings panel, pick any existing `.wav` file (e.g. copy an old
`combined_audio.wav` from a past recording directory to a scratch location first), confirm the
timestamp dialog appears pre-filled and editable, confirm it, and verify: a new session appears
in the recordings list, transcription auto-starts (if enabled), and the transcript displays with
no speaker-panel crash when diarization completes.

- [ ] **Step 3: Verify calendar tagging is a true no-op when disabled/unavailable**

With the Calendar setting left at its default (off), record or import a session and confirm no
banner appears and no `calendar_event.json` is written. If Outlook isn't installed on this
machine, this also confirms the "Outlook unavailable" path is silent even if the setting were
enabled — optionally toggle it on and confirm the app doesn't hang or error.

- [ ] **Step 4: Update `CLAUDE.md`**

In the **Current Features** section, add two new bullets (alphabetical/thematic placement near
the existing speaker-naming and recordings-browsing bullets):

```markdown
- **Calendar tagging (opt-in):** after a recording finishes, optionally checks the local Outlook desktop calendar for an overlapping event and offers to tag the recording with its subject, organizer, and attendees (`calendar_event.json`); attendee names populate a mutually-exclusive dropdown in speaker naming
- **Recording import:** import an existing audio file (wav/mp3/m4a) as a new session via Recordings > Import..., running it through the same transcribe/diarize pipeline as a live recording
```

In the **Project Structure** tree, add the new files under their respective directories:

```
    integrations/
      __init__.py                      # Package init
      outlook_calendar.py              # Read-only Outlook desktop calendar lookup (COM)
    recording/
      import_session.py                # Pure metadata builder for imported recordings
    ui/
      calendar_banner.py               # Calendar-match suggestion banner
      calendar_lookup_worker.py        # Off-thread Outlook calendar lookup
      import_timestamp_dialog.py       # Confirm/edit an imported recording's start time
```

(Insert `integrations/` as a new top-level subdirectory under `app/`, alongside `ai/`, `audio/`,
etc.; insert the new `recording/` and `ui/` entries into their existing subtrees.) Also add the
five new test files to the `tests/` listing:
`test_outlook_calendar.py`, `test_import_session.py`, plus note the extended
`test_speaker_name_panel.py`/`test_recording_header.py`/`test_config.py`.

- [ ] **Step 5: Commit**

```bash
git add CLAUDE.md
git commit -m "docs: document calendar tagging and recording import features"
```

---

## Post-plan verification

Run the full suite one final time to confirm nothing regressed across all 15 tasks:

```bash
.venv\Scripts\python.exe -m pytest tests/ -v
```

Expected: all pass (345 pre-existing + ~19 new: 2 config + 7 outlook_calendar + 3 recording_header
+ 4 speaker_name_panel + 5 import_session = 21 new tests; exact count may vary slightly by
implementer's edge-case additions, but the suite must be green).
