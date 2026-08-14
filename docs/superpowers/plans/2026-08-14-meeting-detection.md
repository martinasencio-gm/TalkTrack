# Meeting Detection & Recording Suggestion Implementation Plan

> **For agentic workers:** Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Detect that a meeting is underway in Teams/Zoom/etc and suggest starting a
recording, then detect when it ends and suggest stopping or pausing.

**Architecture:** A pure state machine (`meeting_detector.py`) consumes plain-dict
signal snapshots produced by a Windows-specific probe (`meeting_signals.py`) and
returns `Decision` objects. `MainWindow` polls the probe, feeds the detector, and
routes decisions to a banner and the tray. All decision logic is testable without Qt,
COM, or a real meeting.

**Tech Stack:** Python 3.14, PyQt6, pycaw + comtypes (capture-session enumeration),
psutil, existing Outlook COM integration.

**Spec:** `docs/superpowers/specs/2026-08-14-meeting-detection-design.md`
**Issue:** [#65](https://github.com/ObscureAintSecure/TalkTrack/issues/65)

## Global Constraints

- Commits go directly to `master`. Conventional prefixes: `feat:`, `fix:`, `config:`,
  `ui:`, `main:`, `docs:`. Never add `Co-Authored-By`. Never `--amend`.
- Reference `#65` in every commit message for this feature.
- Non-UI logic is TDD: failing test first, confirm failure, implement, confirm pass.
- UI code gets pure-helper unit tests plus a `python -c` import smoke test. No Qt
  widget tests except the layout-regression precedent in
  `tests/test_recordings_list_layout.py`.
- Full suite: `QT_QPA_PLATFORM=offscreen .venv/Scripts/python.exe -m pytest tests/ -q`
- **TalkTrack must exclude its own PID** from mic-capture results — its idle
  `MicMonitor` holds a capture session and would otherwise self-trigger.
- A process ACTIVE on **any** capture device counts as capturing; never check only the
  default device.
- `silence_auto_stop` is left completely unchanged — it remains the backstop.
- `KNOWN_AUDIO_APPS` in `audio_session_monitor.py` must NOT be reused as the meeting
  app list; it contains Spotify and Discord.
- Every probe is individually try/except-wrapped; one failing signal returns empty for
  itself and never breaks the snapshot.

## File Structure

| File | Responsibility |
|---|---|
| `app/utils/config.py` (modify) | Add `meeting_detection` defaults + migration hook |
| `app/utils/config_migration.py` (create) | Pure migration function, no I/O |
| `app/utils/meeting_signals.py` (create) | Windows probes → signal snapshot dict |
| `app/integrations/meeting_detector.py` (create) | Pure state machine → `Decision` |
| `app/ui/meeting_banner.py` (create) | Start and end prompt banner |
| `app/ui/settings_dialog.py` (modify) | Meeting-detection controls |
| `app/main_window.py` (modify) | Poll, feed detector, route decisions |

---

### Task 1: Config schema and migration

**Files:**
- Create: `app/utils/config_migration.py`
- Modify: `app/utils/config.py` (add `meeting_detection` to `DEFAULT_CONFIG`; call
  migration at the end of `load()`)
- Test: `tests/test_config_migration.py`

**Interfaces:**
- Produces: `apply_meeting_detection_migration(saved: dict | None, merged: dict) -> dict`

- [ ] **Step 1: Write the failing tests**

```python
import unittest
from app.utils.config_migration import apply_meeting_detection_migration


class TestMeetingDetectionMigration(unittest.TestCase):
    def _merged(self, mode="suggest", threshold=5):
        return {"general": {"auto_record": False, "auto_record_threshold": 5},
                "meeting_detection": {"mode": mode, "threshold_seconds": threshold}}

    def test_fresh_config_keeps_suggest_default(self):
        # No saved file at all -> brand new user -> default stands.
        result = apply_meeting_detection_migration(None, self._merged())
        self.assertEqual(result["meeting_detection"]["mode"], "suggest")

    def test_existing_user_with_auto_record_off_becomes_off(self):
        # The important case: someone who deliberately disabled auto-record must NOT
        # silently inherit the new "suggest" default.
        saved = {"general": {"auto_record": False}}
        result = apply_meeting_detection_migration(saved, self._merged())
        self.assertEqual(result["meeting_detection"]["mode"], "off")

    def test_existing_user_with_auto_record_on_becomes_auto(self):
        saved = {"general": {"auto_record": True}}
        result = apply_meeting_detection_migration(saved, self._merged())
        self.assertEqual(result["meeting_detection"]["mode"], "auto")

    def test_threshold_carries_over(self):
        saved = {"general": {"auto_record": True, "auto_record_threshold": 12}}
        merged = self._merged()
        merged["general"]["auto_record_threshold"] = 12
        result = apply_meeting_detection_migration(saved, merged)
        self.assertEqual(result["meeting_detection"]["threshold_seconds"], 12)

    def test_already_migrated_config_is_untouched(self):
        saved = {"general": {"auto_record": True},
                 "meeting_detection": {"mode": "off"}}
        merged = self._merged(mode="off")
        result = apply_meeting_detection_migration(saved, merged)
        self.assertEqual(result["meeting_detection"]["mode"], "off")

    def test_saved_without_auto_record_keeps_default(self):
        result = apply_meeting_detection_migration({"general": {}}, self._merged())
        self.assertEqual(result["meeting_detection"]["mode"], "suggest")
```

- [ ] **Step 2: Run to verify failure**

Run: `QT_QPA_PLATFORM=offscreen .venv/Scripts/python.exe -m pytest tests/test_config_migration.py -q`
Expected: FAIL — `ModuleNotFoundError: app.utils.config_migration`

- [ ] **Step 3: Implement**

```python
"""One-time config migrations. Pure functions — no file I/O, no global state."""


def apply_meeting_detection_migration(saved, merged):
    """Derive meeting_detection.mode from the legacy general.auto_record flag.

    `saved` is the raw dict read from disk (None when no config file existed),
    `merged` is that dict deep-merged over DEFAULT_CONFIG.

    The migration must write the mode explicitly rather than let the default stand.
    A user who deliberately turned auto-record off would otherwise inherit the new
    "suggest" default and start getting prompts they never asked for.
    """
    if not saved:
        return merged                      # brand-new install: defaults are correct
    if "meeting_detection" in saved:
        return merged                      # already migrated; respect their choice
    general = saved.get("general") or {}
    if "auto_record" not in general:
        return merged                      # pre-dates auto_record entirely
    merged["meeting_detection"]["mode"] = "auto" if general["auto_record"] else "off"
    if "auto_record_threshold" in general:
        merged["meeting_detection"]["threshold_seconds"] = general["auto_record_threshold"]
    return merged
```

- [ ] **Step 4: Add defaults to `DEFAULT_CONFIG` in `app/utils/config.py`**

Insert after the `"general"` block:

```python
    "meeting_detection": {
        "mode": "suggest",          # "off" | "suggest" | "auto"
        "threshold_seconds": 5,
        "detect_end": True,
        "end_grace_seconds": 60,
        "end_action": "stop",       # auto mode only: "stop" | "pause"
        "use_mic_capture": True,
        "use_calendar": True,
        "use_window_title": False,
        "apps": ["ms-teams", "Teams", "Zoom", "Webex"],
    },
```

- [ ] **Step 5: Call the migration from `load()`**

In `app/utils/config.py`, `load()` currently discards the raw `saved` dict. Capture it
and pass it to the migration after the merge:

```python
    def load(self):
        self._data = None
        saved = None
        if CONFIG_FILE.exists():
            try:
                with open(CONFIG_FILE, "r") as f:
                    saved = json.load(f)
                if not isinstance(saved, dict):
                    raise ValueError("settings root is not an object")
                self._data = self._deep_merge(DEFAULT_CONFIG, saved)
            except (json.JSONDecodeError, ValueError, OSError):
                saved = None
                self._backup_corrupt_file()
        if self._data is None:
            self._data = copy.deepcopy(DEFAULT_CONFIG)
        self._data = apply_meeting_detection_migration(saved, self._data)
        # ... existing directory-creation code unchanged ...
```

Add the import at the top: `from app.utils.config_migration import apply_meeting_detection_migration`

- [ ] **Step 6: Run tests**

Run: `QT_QPA_PLATFORM=offscreen .venv/Scripts/python.exe -m pytest tests/test_config_migration.py tests/test_config.py -q`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add app/utils/config_migration.py app/utils/config.py tests/test_config_migration.py
git commit -m "config: add meeting_detection settings with explicit migration (#65)"
```

---

### Task 2: Signal probe

**Files:**
- Create: `app/utils/meeting_signals.py`
- Test: `tests/test_meeting_signals.py`

**Interfaces:**
- Produces:
  - `get_mic_capture_pids(exclude_pid=None) -> set[int]`
  - `probe(settings, calendar_event=None, now=None) -> dict` with keys
    `timestamp, audio_apps, mic_capture_apps, meeting_titles, calendar_event`
  - `is_meeting_app(process_name, apps) -> bool`

- [ ] **Step 1: Write the failing tests**

```python
import unittest
from app.utils import meeting_signals


class TestIsMeetingApp(unittest.TestCase):
    APPS = ["ms-teams", "Teams", "Zoom", "Webex"]

    def test_matches_ignoring_exe_suffix_and_case(self):
        self.assertTrue(meeting_signals.is_meeting_app("ms-teams.exe", self.APPS))
        self.assertTrue(meeting_signals.is_meeting_app("ZOOM.EXE", self.APPS))

    def test_rejects_non_meeting_apps(self):
        # Regression guard: KNOWN_AUDIO_APPS contains these, our list must not.
        self.assertFalse(meeting_signals.is_meeting_app("Spotify.exe", self.APPS))
        self.assertFalse(meeting_signals.is_meeting_app("Discord.exe", self.APPS))


class TestProbe(unittest.TestCase):
    SETTINGS = {"apps": ["ms-teams", "Zoom"], "use_mic_capture": True,
                "use_calendar": True, "use_window_title": False}

    def test_probe_reports_meeting_apps_with_audio(self):
        snap = meeting_signals.probe(
            self.SETTINGS, now=100.0,
            _audio_apps_fn=lambda: [{"process_name": "ms-teams.exe", "active": True},
                                    {"process_name": "Spotify.exe", "active": True}],
            _mic_pids_fn=lambda: set(),
            _pid_names_fn=lambda pids: {},
            _titles_fn=lambda: [])
        self.assertEqual(snap["audio_apps"], ["ms-teams"])
        self.assertEqual(snap["timestamp"], 100.0)

    def test_inactive_audio_sessions_are_ignored(self):
        snap = meeting_signals.probe(
            self.SETTINGS, now=1.0,
            _audio_apps_fn=lambda: [{"process_name": "Zoom.exe", "active": False}],
            _mic_pids_fn=lambda: set(), _pid_names_fn=lambda pids: {},
            _titles_fn=lambda: [])
        self.assertEqual(snap["audio_apps"], [])

    def test_mic_capture_maps_pids_to_meeting_apps(self):
        snap = meeting_signals.probe(
            self.SETTINGS, now=1.0,
            _audio_apps_fn=lambda: [],
            _mic_pids_fn=lambda: {4116, 9999},
            _pid_names_fn=lambda pids: {4116: "ms-teams.exe", 9999: "chrome.exe"},
            _titles_fn=lambda: [])
        self.assertEqual(snap["mic_capture_apps"], ["ms-teams"])

    def test_mic_capture_skipped_when_disabled(self):
        settings = dict(self.SETTINGS, use_mic_capture=False)
        snap = meeting_signals.probe(
            settings, now=1.0, _audio_apps_fn=lambda: [],
            _mic_pids_fn=lambda: {4116},
            _pid_names_fn=lambda pids: {4116: "ms-teams.exe"},
            _titles_fn=lambda: [])
        self.assertEqual(snap["mic_capture_apps"], [])

    def test_failing_probe_does_not_break_snapshot(self):
        def boom():
            raise OSError("COM exploded")
        snap = meeting_signals.probe(
            self.SETTINGS, now=1.0, _audio_apps_fn=boom,
            _mic_pids_fn=lambda: set(), _pid_names_fn=lambda pids: {},
            _titles_fn=lambda: [])
        self.assertEqual(snap["audio_apps"], [])
        self.assertIn("timestamp", snap)
```

- [ ] **Step 2: Run to verify failure**

Run: `QT_QPA_PLATFORM=offscreen .venv/Scripts/python.exe -m pytest tests/test_meeting_signals.py -q`
Expected: FAIL — `ModuleNotFoundError: app.utils.meeting_signals`

- [ ] **Step 3: Implement**

```python
"""Probe Windows for evidence that a meeting is underway.

Reports facts only — every decision lives in meeting_detector. The probe functions
are injectable so the whole module is testable without COM or a real meeting.
"""
import logging
import os
import time

logger = logging.getLogger(__name__)

# Window titles that indicate a live call. Deliberately narrow: this signal may only
# ever confirm a meeting, never trigger one, because titles drift across app versions
# and locales.
_MEETING_TITLE_MARKERS = ("zoom meeting", "meeting with", "| microsoft teams call")


def _base_name(process_name):
    if process_name.lower().endswith(".exe"):
        return process_name[:-4]
    return process_name


def is_meeting_app(process_name, apps):
    """True if process_name is one of the configured meeting apps."""
    base = _base_name(process_name).lower()
    return any(base == _base_name(a).lower() for a in apps)


def get_mic_capture_pids(exclude_pid=None):
    """PIDs with an ACTIVE capture session on ANY capture device.

    Any-device matters: a process can be ACTIVE on one capture endpoint while
    INACTIVE on another (observed with ms-teams), so a default-device check misses it.

    exclude_pid drops our own process — TalkTrack's idle MicMonitor holds a capture
    session, and without this the app detects itself as a meeting.
    """
    import comtypes
    from comtypes import CLSCTX_ALL, POINTER, cast
    from pycaw.pycaw import (AudioUtilities, IAudioSessionControl2,
                             IAudioSessionManager2, IMMDeviceEnumerator)
    from pycaw.constants import CLSID_MMDeviceEnumerator, DEVICE_STATE, EDataFlow

    _ACTIVE = 1
    pids = set()
    enumerator = comtypes.CoCreateInstance(
        CLSID_MMDeviceEnumerator, IMMDeviceEnumerator, comtypes.CLSCTX_INPROC_SERVER)
    collection = enumerator.EnumAudioEndpoints(
        EDataFlow.eCapture.value, DEVICE_STATE.ACTIVE.value)
    for i in range(collection.GetCount()):
        try:
            device = collection.Item(i)
            manager = cast(
                device.Activate(IAudioSessionManager2._iid_, CLSCTX_ALL, None),
                POINTER(IAudioSessionManager2))
            sessions = manager.GetSessionEnumerator()
            for j in range(sessions.GetCount()):
                control = sessions.GetSession(j)
                if control.GetState() != _ACTIVE:
                    continue
                pid = control.QueryInterface(IAudioSessionControl2).GetProcessId()
                if pid and pid != exclude_pid:
                    pids.add(pid)
        except Exception:
            continue  # one bad endpoint must not lose the others
    return pids


def _default_audio_apps():
    from app.utils.audio_session_monitor import get_active_audio_apps
    return get_active_audio_apps()


def _default_mic_pids():
    return get_mic_capture_pids(exclude_pid=os.getpid())


def _default_pid_names(pids):
    import psutil
    names = {}
    for pid in pids:
        try:
            names[pid] = psutil.Process(pid).name()
        except Exception:
            continue
    return names


def _default_titles():
    import win32gui

    titles = []

    def callback(hwnd, _):
        if not win32gui.IsWindowVisible(hwnd):
            return
        text = win32gui.GetWindowText(hwnd)
        if text and any(m in text.lower() for m in _MEETING_TITLE_MARKERS):
            titles.append(text)

    win32gui.EnumWindows(callback, None)
    return titles


def _safe(fn, default, label):
    try:
        return fn()
    except Exception as exc:
        logger.debug("meeting signal probe %s failed: %s", label, exc)
        return default


def probe(settings, calendar_event=None, now=None,
          _audio_apps_fn=None, _mic_pids_fn=None, _pid_names_fn=None,
          _titles_fn=None):
    """Return a signal snapshot. Never raises."""
    apps = settings.get("apps", [])
    audio_apps_fn = _audio_apps_fn or _default_audio_apps
    mic_pids_fn = _mic_pids_fn or _default_mic_pids
    pid_names_fn = _pid_names_fn or _default_pid_names
    titles_fn = _titles_fn or _default_titles

    entries = _safe(audio_apps_fn, [], "audio")
    audio_apps = sorted({
        _base_name(e["process_name"]) for e in entries
        if e.get("active") and is_meeting_app(e["process_name"], apps)
    })

    mic_apps = []
    if settings.get("use_mic_capture"):
        pids = _safe(mic_pids_fn, set(), "mic")
        names = _safe(lambda: pid_names_fn(pids), {}, "pidnames")
        mic_apps = sorted({
            _base_name(n) for n in names.values() if is_meeting_app(n, apps)
        })

    titles = []
    if settings.get("use_window_title"):
        titles = _safe(titles_fn, [], "titles")

    return {
        "timestamp": time.monotonic() if now is None else now,
        "audio_apps": audio_apps,
        "mic_capture_apps": mic_apps,
        "meeting_titles": titles,
        "calendar_event": calendar_event if settings.get("use_calendar") else None,
    }
```

- [ ] **Step 4: Run tests**

Run: `QT_QPA_PLATFORM=offscreen .venv/Scripts/python.exe -m pytest tests/test_meeting_signals.py -q`
Expected: PASS

- [ ] **Step 5: Verify the real probe against live Windows**

Run:
```bash
.venv/Scripts/python.exe -c "from app.utils.meeting_signals import get_mic_capture_pids; import os; print(sorted(get_mic_capture_pids(exclude_pid=os.getpid())))"
```
Expected: a list of PIDs, and **not** containing the PID printed by `os.getpid()`.

- [ ] **Step 6: Commit**

```bash
git add app/utils/meeting_signals.py tests/test_meeting_signals.py
git commit -m "feat: probe mic capture and meeting-app audio signals (#65)"
```

---

### Task 3: Detector state machine

**Files:**
- Create: `app/integrations/meeting_detector.py`
- Test: `tests/test_meeting_detector.py`

**Interfaces:**
- Consumes: snapshots from `meeting_signals.probe()`
- Produces:
  - `Decision` namedtuple: `(action: str, meeting_name: str | None)`
  - Actions: `"none" | "suggest_start" | "start" | "suggest_end" | "stop" | "pause" | "resume"`
  - `MeetingDetector.update(snapshot, settings) -> Decision`
  - `MeetingDetector.note_recording_started(snapshot)`
  - `MeetingDetector.note_recording_stopped()`
  - `MeetingDetector.accept_start()` / `.dismiss_start()`
  - `MeetingDetector.choose_end(action)` where action is `"stop" | "pause" | "keep"`

- [ ] **Step 1: Write the failing tests**

```python
import unittest
from app.integrations.meeting_detector import MeetingDetector

SETTINGS = {"mode": "suggest", "threshold_seconds": 5, "detect_end": True,
            "end_grace_seconds": 60, "end_action": "stop", "use_mic_capture": True,
            "use_calendar": True, "use_window_title": False}


def snap(t, audio=(), mic=(), titles=(), event=None):
    return {"timestamp": t, "audio_apps": list(audio), "mic_capture_apps": list(mic),
            "meeting_titles": list(titles), "calendar_event": event}


class TestStartDetection(unittest.TestCase):
    def test_chime_shorter_than_threshold_never_suggests(self):
        d = MeetingDetector()
        self.assertEqual(d.update(snap(0, audio=["ms-teams"]), SETTINGS).action, "none")
        self.assertEqual(d.update(snap(2), SETTINGS).action, "none")
        self.assertEqual(d.update(snap(10, audio=["ms-teams"]), SETTINGS).action, "none")

    def test_sustained_audio_without_confirmation_does_not_suggest(self):
        d = MeetingDetector()
        d.update(snap(0, audio=["ms-teams"]), SETTINGS)
        self.assertEqual(d.update(snap(10, audio=["ms-teams"]), SETTINGS).action, "none")

    def test_mic_capture_alone_is_sufficient(self):
        d = MeetingDetector()
        d.update(snap(0, mic=["ms-teams"]), SETTINGS)
        self.assertEqual(d.update(snap(10, mic=["ms-teams"]), SETTINGS).action,
                         "suggest_start")

    def test_calendar_confirms_sustained_audio(self):
        d = MeetingDetector()
        event = {"subject": "Sprint Planning"}
        d.update(snap(0, audio=["ms-teams"], event=event), SETTINGS)
        decision = d.update(snap(10, audio=["ms-teams"], event=event), SETTINGS)
        self.assertEqual(decision.action, "suggest_start")
        self.assertEqual(decision.meeting_name, "Sprint Planning")

    def test_auto_mode_starts_without_suggesting(self):
        d = MeetingDetector()
        settings = dict(SETTINGS, mode="auto")
        d.update(snap(0, mic=["ms-teams"]), settings)
        self.assertEqual(d.update(snap(10, mic=["ms-teams"]), settings).action, "start")

    def test_off_mode_produces_nothing(self):
        d = MeetingDetector()
        settings = dict(SETTINGS, mode="off")
        d.update(snap(0, mic=["ms-teams"]), settings)
        self.assertEqual(d.update(snap(10, mic=["ms-teams"]), settings).action, "none")

    def test_dismissed_session_does_not_reprompt(self):
        d = MeetingDetector()
        d.update(snap(0, mic=["ms-teams"]), SETTINGS)
        d.update(snap(10, mic=["ms-teams"]), SETTINGS)
        d.dismiss_start()
        self.assertEqual(d.update(snap(20, mic=["ms-teams"]), SETTINGS).action, "none")
        self.assertEqual(d.update(snap(30, mic=["ms-teams"]), SETTINGS).action, "none")

    def test_new_session_after_dismissal_may_prompt_again(self):
        d = MeetingDetector()
        d.update(snap(0, mic=["ms-teams"]), SETTINGS)
        d.update(snap(10, mic=["ms-teams"]), SETTINGS)
        d.dismiss_start()
        d.update(snap(200), SETTINGS)          # long gap ends the session
        d.update(snap(300, mic=["ms-teams"]), SETTINGS)
        self.assertEqual(d.update(snap(310, mic=["ms-teams"]), SETTINGS).action,
                         "suggest_start")


class TestEndDetection(unittest.TestCase):
    def _recording(self):
        d = MeetingDetector()
        d.update(snap(0, mic=["ms-teams"]), SETTINGS)
        d.update(snap(10, mic=["ms-teams"]), SETTINGS)
        d.accept_start()
        return d

    def test_short_dropout_does_not_end(self):
        d = self._recording()
        self.assertEqual(d.update(snap(40), SETTINGS).action, "none")
        self.assertEqual(d.update(snap(60, mic=["ms-teams"]), SETTINGS).action, "none")

    def test_sustained_absence_suggests_end(self):
        d = self._recording()
        d.update(snap(40), SETTINGS)
        self.assertEqual(d.update(snap(100), SETTINGS).action, "suggest_end")

    def test_signals_returning_during_ending_resumes_silently(self):
        d = self._recording()
        d.update(snap(100), SETTINGS)                      # -> suggest_end
        self.assertEqual(d.update(snap(110, mic=["ms-teams"]), SETTINGS).action, "none")
        # and it may suggest ending again later
        self.assertEqual(d.update(snap(200), SETTINGS).action, "suggest_end")

    def test_keep_recording_suppresses_further_end_prompts(self):
        d = self._recording()
        d.update(snap(100), SETTINGS)
        d.choose_end("keep")
        self.assertEqual(d.update(snap(200), SETTINGS).action, "none")

    def test_auto_mode_stops_without_prompting(self):
        settings = dict(SETTINGS, mode="auto")
        d = MeetingDetector()
        d.update(snap(0, mic=["ms-teams"]), settings)
        d.update(snap(10, mic=["ms-teams"]), settings)
        d.accept_start()
        self.assertEqual(d.update(snap(100), settings).action, "stop")

    def test_auto_mode_pause_action(self):
        settings = dict(SETTINGS, mode="auto", end_action="pause")
        d = MeetingDetector()
        d.update(snap(0, mic=["ms-teams"]), settings)
        d.update(snap(10, mic=["ms-teams"]), settings)
        d.accept_start()
        self.assertEqual(d.update(snap(100), settings).action, "pause")

    def test_paused_session_resumes_when_signals_return(self):
        d = self._recording()
        d.update(snap(100), SETTINGS)
        d.choose_end("pause")
        self.assertEqual(d.update(snap(120, mic=["ms-teams"]), SETTINGS).action,
                         "resume")

    def test_detect_end_disabled_never_ends(self):
        settings = dict(SETTINGS, detect_end=False)
        d = MeetingDetector()
        d.update(snap(0, mic=["ms-teams"]), settings)
        d.update(snap(10, mic=["ms-teams"]), settings)
        d.accept_start()
        self.assertEqual(d.update(snap(200), settings).action, "none")

    def test_unrelated_recording_never_gets_end_suggestion(self):
        # Recording started with no meeting active -> detector must not watch it.
        d = MeetingDetector()
        d.note_recording_started(snap(0))
        self.assertEqual(d.update(snap(200), SETTINGS).action, "none")
```

- [ ] **Step 2: Run to verify failure**

Run: `QT_QPA_PLATFORM=offscreen .venv/Scripts/python.exe -m pytest tests/test_meeting_detector.py -q`
Expected: FAIL — `ModuleNotFoundError: app.integrations.meeting_detector`

- [ ] **Step 3: Implement**

```python
"""Pure decision logic for meeting detection.

No Qt, no Windows, no I/O, no wall clock — time arrives in the snapshot. Every rule
in the spec is expressed here so it can be tested without a real meeting.
"""
from collections import namedtuple

Decision = namedtuple("Decision", ["action", "meeting_name"])
NONE = Decision("none", None)

IDLE = "idle"
CANDIDATE = "candidate"
SUGGESTED = "suggested"
DISMISSED = "dismissed"
RECORDING = "recording"
ENDING = "ending"
PAUSED = "paused_by_detection"


class MeetingDetector:
    def __init__(self):
        self._state = IDLE
        self._active_since = None
        self._last_active = None
        self._end_suppressed = False

    @property
    def state(self):
        return self._state

    # --- signal helpers -------------------------------------------------
    @staticmethod
    def _is_active(snapshot):
        return bool(snapshot["audio_apps"] or snapshot["mic_capture_apps"])

    @staticmethod
    def _is_confirmed(snapshot):
        """Mic capture, a current calendar event, or a meeting window title.

        Sustained audio alone is deliberately not enough: it is the trigger, and a
        trigger that confirmed itself would re-admit the notification-chime problem.
        """
        return bool(snapshot["mic_capture_apps"]
                    or snapshot["calendar_event"]
                    or snapshot["meeting_titles"])

    @staticmethod
    def _name(snapshot):
        event = snapshot.get("calendar_event")
        if event and event.get("subject"):
            return event["subject"]
        apps = snapshot["mic_capture_apps"] or snapshot["audio_apps"]
        return apps[0] if apps else None

    def _reset(self):
        self._state = IDLE
        self._active_since = None
        self._end_suppressed = False

    # --- external events ------------------------------------------------
    def accept_start(self):
        self._state = RECORDING
        self._end_suppressed = False

    def dismiss_start(self):
        self._state = DISMISSED

    def choose_end(self, action):
        if action == "stop":
            self._reset()
        elif action == "pause":
            self._state = PAUSED
        elif action == "keep":
            self._state = RECORDING
            self._end_suppressed = True

    def note_recording_started(self, snapshot):
        """Recording began by some route other than accepting a suggestion.

        Only watch it for an ending if a meeting was actually active — otherwise an
        unrelated background call ending would prompt the user to stop a recording
        that has nothing to do with it.
        """
        self._state = RECORDING if self._is_active(snapshot) else IDLE
        self._end_suppressed = False

    def note_recording_stopped(self):
        if self._state in (RECORDING, ENDING, PAUSED):
            self._reset()

    # --- main tick ------------------------------------------------------
    def update(self, snapshot, settings):
        if settings.get("mode", "off") == "off":
            self._reset()
            return NONE

        now = snapshot["timestamp"]
        active = self._is_active(snapshot)
        if active:
            self._last_active = now
            if self._active_since is None:
                self._active_since = now
        else:
            self._active_since = None

        absent_for = float("inf") if self._last_active is None else now - self._last_active
        grace = settings.get("end_grace_seconds", 60)
        threshold = settings.get("threshold_seconds", 5)

        if self._state == IDLE:
            if active:
                self._state = CANDIDATE
            return NONE

        if self._state == CANDIDATE:
            if not active:
                self._state = IDLE
                return NONE
            if now - self._active_since >= threshold and self._is_confirmed(snapshot):
                if settings.get("mode") == "auto":
                    self._state = RECORDING
                    return Decision("start", self._name(snapshot))
                self._state = SUGGESTED
                return Decision("suggest_start", self._name(snapshot))
            return NONE

        if self._state in (SUGGESTED, DISMISSED):
            if absent_for >= grace:
                self._reset()
            return NONE

        if self._state == RECORDING:
            if not settings.get("detect_end", True) or self._end_suppressed:
                return NONE
            if absent_for >= grace:
                if settings.get("mode") == "auto":
                    action = settings.get("end_action", "stop")
                    self._state = PAUSED if action == "pause" else IDLE
                    return Decision(action, None)
                self._state = ENDING
                return Decision("suggest_end", None)
            return NONE

        if self._state == ENDING:
            if active:
                self._state = RECORDING       # blip, not an ending
            return NONE

        if self._state == PAUSED:
            if active:
                self._state = RECORDING
                return Decision("resume", None)
            return NONE

        return NONE
```

- [ ] **Step 4: Run tests**

Run: `QT_QPA_PLATFORM=offscreen .venv/Scripts/python.exe -m pytest tests/test_meeting_detector.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add app/integrations/meeting_detector.py tests/test_meeting_detector.py
git commit -m "feat: meeting detector state machine (#65)"
```

---

### Task 4: Meeting banner widget

**Files:**
- Create: `app/ui/meeting_banner.py`
- Test: `tests/test_meeting_banner.py`

**Interfaces:**
- Produces:
  - `format_start_text(meeting_name, elapsed_seconds) -> str`
  - `format_end_text(meeting_name, recorded_seconds) -> str`
  - `MeetingBanner` with signals `start_accepted`, `start_dismissed`,
    `end_chosen(str)`; methods `show_start(name, elapsed)`, `show_end(name, recorded)`,
    `hide_and_clear()`

- [ ] **Step 1: Write the failing tests**

```python
import unittest
from app.ui.meeting_banner import format_start_text, format_end_text


class TestBannerText(unittest.TestCase):
    def test_start_with_name_and_elapsed(self):
        self.assertEqual(format_start_text("Sprint Planning", 120),
                         "Sprint Planning started 2 minutes ago — record it?")

    def test_start_without_name_falls_back(self):
        self.assertEqual(format_start_text(None, 60),
                         "A meeting started 1 minute ago — record it?")

    def test_start_under_a_minute(self):
        self.assertEqual(format_start_text("Standup", 30),
                         "Standup started just now — record it?")

    def test_end_states_captured_length(self):
        self.assertEqual(format_end_text("Sprint Planning", 1440),
                         "Sprint Planning ended — stop recording? (24 minutes captured)")

    def test_end_without_name(self):
        self.assertEqual(format_end_text(None, 60),
                         "The meeting ended — stop recording? (1 minute captured)")
```

- [ ] **Step 2: Run to verify failure**

Run: `QT_QPA_PLATFORM=offscreen .venv/Scripts/python.exe -m pytest tests/test_meeting_banner.py -q`
Expected: FAIL — `ModuleNotFoundError: app.ui.meeting_banner`

- [ ] **Step 3: Implement**

```python
"""Banner prompting to start, or stop/pause, a recording for a detected meeting.

Mirrors CalendarSuggestionBanner's structure and styling.
"""
from PyQt6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QFrame
from PyQt6.QtCore import pyqtSignal


def _minutes_phrase(seconds):
    minutes = int(seconds // 60)
    if minutes < 1:
        return None
    return "1 minute" if minutes == 1 else f"{minutes} minutes"


def format_start_text(meeting_name, elapsed_seconds):
    subject = meeting_name or "A meeting"
    phrase = _minutes_phrase(elapsed_seconds)
    when = "just now" if phrase is None else f"{phrase} ago"
    return f"{subject} started {when} — record it?"


def format_end_text(meeting_name, recorded_seconds):
    subject = meeting_name or "The meeting"
    phrase = _minutes_phrase(recorded_seconds) or "less than a minute"
    return f"{subject} ended — stop recording? ({phrase} captured)"


class MeetingBanner(QWidget):
    start_accepted = pyqtSignal()
    start_dismissed = pyqtSignal()
    end_chosen = pyqtSignal(str)   # "stop" | "pause" | "keep"

    def __init__(self, parent=None):
        super().__init__(parent)
        self._setup_ui()
        self.hide()

    def _setup_ui(self):
        self._frame = QFrame(self)
        self._frame.setObjectName("meetingBanner")
        self._frame.setStyleSheet(
            "#meetingBanner { background-color: #313244; border-radius: 4px; }")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 8)
        outer.addWidget(self._frame)

        self._layout = QVBoxLayout(self._frame)
        self._layout.setContentsMargins(10, 8, 10, 8)
        self._layout.setSpacing(6)

        self._text = QLabel()
        self._text.setWordWrap(True)
        self._text.setStyleSheet("color: #cdd6f4; font-weight: bold;")
        self._layout.addWidget(self._text)

        self._buttons = QWidget()
        self._button_row = QHBoxLayout(self._buttons)
        self._button_row.setContentsMargins(0, 0, 0, 0)
        self._layout.addWidget(self._buttons)

    def _clear_buttons(self):
        while self._button_row.count():
            item = self._button_row.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

    def _add_buttons(self, specs):
        self._clear_buttons()
        self._button_row.addStretch()
        for label, handler in specs:
            button = QPushButton(label)
            button.clicked.connect(handler)
            self._button_row.addWidget(button)

    def show_start(self, meeting_name, elapsed_seconds):
        self._text.setText(format_start_text(meeting_name, elapsed_seconds))
        self._add_buttons([
            ("Record", self._on_record),
            ("Not now", self._on_not_now),
        ])
        self.show()

    def show_end(self, meeting_name, recorded_seconds):
        self._text.setText(format_end_text(meeting_name, recorded_seconds))
        self._add_buttons([
            ("Stop & save", lambda: self._on_end("stop")),
            ("Pause", lambda: self._on_end("pause")),
            ("Keep recording", lambda: self._on_end("keep")),
        ])
        self.show()

    def hide_and_clear(self):
        self._clear_buttons()
        self.hide()

    def _on_record(self):
        self.hide_and_clear()
        self.start_accepted.emit()

    def _on_not_now(self):
        self.hide_and_clear()
        self.start_dismissed.emit()

    def _on_end(self, action):
        self.hide_and_clear()
        self.end_chosen.emit(action)
```

- [ ] **Step 4: Run tests and smoke test**

```bash
QT_QPA_PLATFORM=offscreen .venv/Scripts/python.exe -m pytest tests/test_meeting_banner.py -q
QT_QPA_PLATFORM=offscreen .venv/Scripts/python.exe -c "from app.ui.meeting_banner import MeetingBanner; print('ok')"
```
Expected: PASS, then `ok`

- [ ] **Step 5: Commit**

```bash
git add app/ui/meeting_banner.py tests/test_meeting_banner.py
git commit -m "ui: meeting suggestion banner (#65)"
```

---

### Task 5: Settings dialog controls

**Files:**
- Modify: `app/ui/settings_dialog.py` (General tab, next to the existing
  `auto_record_cb` at line 42 — that checkbox is replaced by the mode combo)

**Interfaces:**
- Consumes: `config.get("meeting_detection", <key>)`

- [ ] **Step 1: Replace the auto-record checkbox with a mode combo**

Remove `self.auto_record_cb` and its `addRow`. Add:

```python
        self.meeting_mode_combo = QComboBox()
        self.meeting_mode_combo.addItem("Off", "off")
        self.meeting_mode_combo.addItem("Suggest recording", "suggest")
        self.meeting_mode_combo.addItem("Record automatically", "auto")
        self.meeting_mode_combo.setToolTip(
            "What to do when a meeting is detected in Teams, Zoom or another "
            "configured app."
        )
        recording_form.addRow("When a meeting starts:", self.meeting_mode_combo)

        self.detect_end_cb = QCheckBox("Suggest stopping when the meeting ends")
        self.detect_end_cb.setToolTip(
            "Detects the end of a call from the meeting app releasing the microphone. "
            "Silence auto-stop below still applies as a backstop."
        )
        recording_form.addRow(self.detect_end_cb)

        self.end_action_combo = QComboBox()
        self.end_action_combo.addItem("Stop and save", "stop")
        self.end_action_combo.addItem("Pause", "pause")
        self.end_action_combo.setToolTip(
            "Only used when the mode above is 'Record automatically'."
        )
        recording_form.addRow("When a meeting ends:", self.end_action_combo)

        self.use_mic_capture_cb = QCheckBox("Use microphone activity to detect meetings")
        self.use_mic_capture_cb.setToolTip(
            "The most reliable signal — a meeting app only holds the microphone "
            "during a real call. Turn off if you would rather TalkTrack not inspect "
            "which apps are using the microphone."
        )
        recording_form.addRow(self.use_mic_capture_cb)

        self.use_calendar_signal_cb = QCheckBox("Use calendar events to confirm meetings")
        recording_form.addRow(self.use_calendar_signal_cb)

        self.use_window_title_cb = QCheckBox("Use window titles to confirm meetings")
        self.use_window_title_cb.setToolTip(
            "Off by default — window titles vary between app versions and languages, "
            "so this signal is the least reliable."
        )
        recording_form.addRow(self.use_window_title_cb)
```

Keep `auto_record_threshold_spin`, relabel its row to `"Confirm meeting for:"`.

Add `QComboBox` to the `PyQt6.QtWidgets` import list if absent.

- [ ] **Step 2: Load values (replace the `auto_record_cb.setChecked` line ~400)**

```python
        mode = self.config.get("meeting_detection", "mode")
        index = self.meeting_mode_combo.findData(mode)
        self.meeting_mode_combo.setCurrentIndex(max(0, index))
        self.detect_end_cb.setChecked(self.config.get("meeting_detection", "detect_end"))
        end_index = self.end_action_combo.findData(
            self.config.get("meeting_detection", "end_action"))
        self.end_action_combo.setCurrentIndex(max(0, end_index))
        self.use_mic_capture_cb.setChecked(
            self.config.get("meeting_detection", "use_mic_capture"))
        self.use_calendar_signal_cb.setChecked(
            self.config.get("meeting_detection", "use_calendar"))
        self.use_window_title_cb.setChecked(
            self.config.get("meeting_detection", "use_window_title"))
        self.auto_record_threshold_spin.setValue(
            self.config.get("meeting_detection", "threshold_seconds"))
```

- [ ] **Step 3: Save values (replace the `auto_record` set lines ~495)**

```python
        self.config.set("meeting_detection", "mode",
                        self.meeting_mode_combo.currentData())
        self.config.set("meeting_detection", "detect_end",
                        self.detect_end_cb.isChecked())
        self.config.set("meeting_detection", "end_action",
                        self.end_action_combo.currentData())
        self.config.set("meeting_detection", "use_mic_capture",
                        self.use_mic_capture_cb.isChecked())
        self.config.set("meeting_detection", "use_calendar",
                        self.use_calendar_signal_cb.isChecked())
        self.config.set("meeting_detection", "use_window_title",
                        self.use_window_title_cb.isChecked())
        self.config.set("meeting_detection", "threshold_seconds",
                        self.auto_record_threshold_spin.value())
```

- [ ] **Step 4: Smoke test**

```bash
QT_QPA_PLATFORM=offscreen .venv/Scripts/python.exe -c "from app.ui.settings_dialog import SettingsDialog; print('ok')"
QT_QPA_PLATFORM=offscreen .venv/Scripts/python.exe -m pytest tests/ -q
```
Expected: `ok`, full suite PASS

- [ ] **Step 5: Commit**

```bash
git add app/ui/settings_dialog.py
git commit -m "settings: three-way meeting detection mode and per-signal toggles (#65)"
```

---

### Task 6: MainWindow wiring

**Files:**
- Modify: `app/main_window.py`

**Interfaces:**
- Consumes: `MeetingDetector`, `meeting_signals.probe`, `MeetingBanner`, tray

- [ ] **Step 1: Construct the detector, banner and timer**

Imports:
```python
from app.integrations.meeting_detector import MeetingDetector
from app.ui.meeting_banner import MeetingBanner
from app.utils import meeting_signals
```

In `__init__`, near the existing `_auto_record_timer` setup (line ~78):
```python
        self._meeting_detector = MeetingDetector()
        self._meeting_poll_timer = QTimer(self)
        self._meeting_poll_timer.timeout.connect(self._poll_meeting_signals)
        self._meeting_poll_timer.start(3000)
```

In `_setup_ui`, beside the calendar banner (line ~266):
```python
        self.meeting_banner = MeetingBanner()
        self.meeting_banner.start_accepted.connect(self._on_meeting_start_accepted)
        self.meeting_banner.start_dismissed.connect(self._on_meeting_start_dismissed)
        self.meeting_banner.end_chosen.connect(self._on_meeting_end_chosen)
        right_layout.addWidget(self.meeting_banner)
```

- [ ] **Step 2: Add the poll handler and decision routing**

```python
    def _meeting_settings(self):
        return self.config.data.get("meeting_detection", {})

    def _poll_meeting_signals(self):
        settings = self._meeting_settings()
        if settings.get("mode", "off") == "off":
            return
        event = None
        if settings.get("use_calendar"):
            event = self._current_calendar_event
        snapshot = meeting_signals.probe(settings, calendar_event=event)
        self._last_meeting_snapshot = snapshot
        decision = self._meeting_detector.update(snapshot, settings)
        if decision.action != "none":
            self._handle_meeting_decision(decision, snapshot)

    def _handle_meeting_decision(self, decision, snapshot):
        action = decision.action
        if action == "suggest_start":
            elapsed = self._meeting_elapsed(snapshot)
            self.meeting_banner.show_start(decision.meeting_name, elapsed)
            self.tray.notify_meeting(
                "Meeting detected",
                f"{decision.meeting_name or 'A meeting'} is running — "
                f"open TalkTrack to record it.")
        elif action == "start":
            self.status_label.setText("Meeting detected — auto-recording...")
            self._start_recording()
        elif action == "suggest_end":
            self.meeting_banner.show_end(
                decision.meeting_name, self.recorder.get_elapsed_time())
            self.tray.notify_meeting(
                "Meeting ended",
                "TalkTrack is still recording — open it to stop or pause.")
        elif action == "stop":
            self.status_label.setText("Meeting ended — stopping recording...")
            self.recorder.stop_recording()
        elif action == "pause":
            self.status_label.setText("Meeting ended — recording paused.")
            self.recorder.pause_recording()
        elif action == "resume":
            self.status_label.setText("Meeting resumed — recording.")
            self.recorder.resume_recording()

    def _meeting_elapsed(self, snapshot):
        """Seconds since this meeting's signals first appeared."""
        started = self._meeting_detector.active_since
        if started is None:
            return 0
        return max(0, snapshot["timestamp"] - started)

    def _on_meeting_start_accepted(self):
        self._meeting_detector.accept_start()
        self._start_recording()

    def _on_meeting_start_dismissed(self):
        self._meeting_detector.dismiss_start()

    def _on_meeting_end_chosen(self, action):
        self._meeting_detector.choose_end(action)
        if action == "stop":
            self.recorder.stop_recording()
        elif action == "pause":
            self.recorder.pause_recording()
```

Initialise `self._last_meeting_snapshot = None` and `self._current_calendar_event = None`
in `__init__` alongside the detector.

Expose `active_since` on the detector (add to `meeting_detector.py`):
```python
    @property
    def active_since(self):
        return self._active_since
```

- [ ] **Step 3: Keep the detector in step with manual recording control**

In `_on_state_changed`, when the state becomes `RECORDING` and the detector is not
already in its `RECORDING`/`PAUSED` states, call:
```python
        if state == RecordingState.RECORDING and self._last_meeting_snapshot:
            if self._meeting_detector.state not in ("recording", "paused_by_detection"):
                self._meeting_detector.note_recording_started(self._last_meeting_snapshot)
        elif state == RecordingState.IDLE:
            self._meeting_detector.note_recording_stopped()
            self.meeting_banner.hide_and_clear()
```

- [ ] **Step 4: Add the tray notification helper**

In `app/ui/tray_icon.py`, beside `show_hint_balloon`:
```python
    def notify_meeting(self, title, message):
        """Balloon for meeting start/end suggestions."""
        self._tray.showMessage(
            title, message, QSystemTrayIcon.MessageIcon.Information, 8000)
```

- [ ] **Step 5: Remove the superseded auto-record path**

Delete `_on_apps_became_active`, `_on_auto_record_timer_fired`, `_auto_record_timer`
and their connections/stop-calls (lines ~78-80, ~375-376, ~575-576, ~599-640, ~1924-1925).
The detector now owns this behavior, and leaving both would let two systems start a
recording from the same signal.

- [ ] **Step 6: Verify**

```bash
QT_QPA_PLATFORM=offscreen .venv/Scripts/python.exe -m pytest tests/ -q
QT_QPA_PLATFORM=offscreen .venv/Scripts/python.exe -c "from app.main_window import MainWindow; print('ok')"
```
Expected: full suite PASS, then `ok`

- [ ] **Step 7: Commit**

```bash
git add app/main_window.py app/ui/tray_icon.py app/integrations/meeting_detector.py
git commit -m "main: wire meeting detection to banner and tray (#65)"
```

---

### Task 7: Documentation and manual verification

**Files:**
- Modify: `CLAUDE.md` (settings list at line ~257)

- [ ] **Step 1: Update CLAUDE.md**

Replace the General-settings line with:
```
- General settings: min_recording_length, silence_auto_stop, silence_duration
- Meeting detection (`meeting_detection`): mode (off/suggest/auto), threshold_seconds,
  detect_end, end_grace_seconds, end_action, use_mic_capture, use_calendar,
  use_window_title, apps. Replaces the old `general.auto_record` flag, which is
  migrated on load. `silence_auto_stop` is retained as an independent backstop.
```

- [ ] **Step 2: Manual smoke test**

Launch the app, then with `mode` set to `suggest`:

1. Join a Teams or Zoom call. Within ~10s expect a tray balloon and an in-app banner.
2. Click **Not now**; confirm no further prompt for that call.
3. Leave the call. Confirm no end prompt (start was dismissed, nothing recording).
4. Rejoin, click **Record**, confirm recording starts.
5. Leave the call. After ~60s expect the end banner offering Stop / Pause / Keep.
6. Choose **Pause**, rejoin the call, confirm it resumes automatically.
7. Play music in Spotify with no call active; confirm no prompt appears.

Authority for "app is up" is `~/.talktrack/talktrack.log` showing `TalkTrack UI ready`.

- [ ] **Step 3: Commit**

```bash
git add CLAUDE.md
git commit -m "docs: document meeting detection settings (#65)"
```

---

## Self-Review

**Spec coverage:** detection rule → Task 3; end rule → Task 3; stop/pause/resume →
Tasks 3, 4, 6; settings + migration → Tasks 1, 5; app list separate from
`KNOWN_AUDIO_APPS` → Task 1 defaults, enforced by a test in Task 2; self-PID exclusion
and any-device rule → Task 2; suggestion content → Task 4; error handling → Task 2
`_safe`; testing → Tasks 1-4.

**Known gap, deliberately deferred:** the spec's `detected_before_start_seconds`
metadata field is not implemented here. It belongs with the recording-metadata writer
rather than the detector, and none of the detection behavior depends on it. Filed as a
follow-up rather than padding this plan.

**Type consistency:** `Decision.action` strings are identical across Tasks 3, 4 and 6
(`suggest_start`, `start`, `suggest_end`, `stop`, `pause`, `resume`, `none`); banner
`end_chosen` values (`stop`, `pause`, `keep`) match `choose_end`'s accepted arguments;
detector state strings used in Task 6's guard match the module constants.
