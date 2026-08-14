# Isolate pycaw/comtypes COM Session Polling Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Move all pycaw/comtypes COM session enumeration (both the audio-app list and the
mic-capture-PID meeting probe) into one persistent, isolated worker process, so the
periodic native crash this causes (Windows access violation inside `_ctypes.pyd`, confirmed
via Windows Event Log + `talktrack.log` correlation) kills only that worker instead of the
whole app.

**Architecture:** A new `ComSessionPoller` class in `app/utils/com_session_worker.py` owns a
`multiprocessing.Process` that loops calling the existing, unmodified
`get_active_audio_apps()` and `get_mic_capture_pids()` functions and reports results back
over a maxsize-1 `multiprocessing.Queue`. `MainWindow` owns one poller instance; both
`SourceSelector`'s app-list timer and `MainWindow`'s meeting-signal timer read from its
cached snapshot instead of calling pycaw directly. A dead worker is detected via
`Process.is_alive()` and silently respawned with a backoff.

**Tech Stack:** Python `multiprocessing` (stdlib), PyQt6 (`QTimer`), pytest (offscreen Qt
platform for headless test runs).

## Global Constraints

- Spec: `docs/superpowers/specs/2026-08-14-com-session-worker-isolation-design.md`
- Tests run via `QT_QPA_PLATFORM=offscreen .venv/Scripts/python.exe -m pytest tests/ -q` —
  never bare `uv run`.
- Commits go directly to `master`, conventional prefixes (`main:`, `ui:`, `docs:`), never
  `Co-Authored-By`, never `--amend`.
- `get_active_audio_apps()` (`app/utils/audio_session_monitor.py`) and
  `get_mic_capture_pids()` (`app/utils/meeting_signals.py`) are used exactly as they exist
  today — no changes to either function's logic.
- `meeting_signals.probe()`'s existing `_audio_apps_fn`/`_mic_pids_fn` injection parameters
  (`app/utils/meeting_signals.py:136-138`) are reused as-is — no signature change to
  `probe()`.
- Poll cadence: worker interval 5.0s idle / 2.0s while recording (relaxed from the current
  3s/1s UI timer values). Backoff window for respawn: 5.0 seconds. Queue maxsize: 1. Shutdown
  join timeout: 2.0 seconds before falling back to `terminate()`.
- Out of scope: `app/recording/_process_com.py` (one-shot per-recording COM path, not
  implicated), and the underlying `comtypes`/CPython 3.14 defect itself (third-party).

---

### Task 1: `ComSessionPoller` and worker loop

**Files:**
- Create: `app/utils/com_session_worker.py`
- Test: `tests/test_com_session_worker.py` (new)

**Interfaces:**
- Produces: `ComSessionPoller(main_pid=None, worker_target=_worker_loop)` with methods
  `start()`, `get_snapshot() -> dict` (always `{"audio_apps": list, "mic_pids": set}`, never
  `None`, never raises), `set_interval(seconds: float)`, `stop()`. `worker_target` is a
  constructor parameter purely so tests can substitute a fake loop function; production
  code never passes it explicitly (uses the default).

- [ ] **Step 1: Write the failing tests**

Create `tests/test_com_session_worker.py`:

```python
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import time
import unittest

from app.utils.com_session_worker import ComSessionPoller


def _fake_worker_blocks_forever(result_queue, interval, stop_event, main_pid):
    stop_event.wait()


def _fake_worker_puts_once(result_queue, interval, stop_event, main_pid):
    result_queue.put({"audio_apps": ["FakeApp"], "mic_pids": {123}})
    stop_event.wait()


def _fake_worker_dies_immediately(result_queue, interval, stop_event, main_pid):
    return


def _fake_worker_reports_interval(result_queue, interval, stop_event, main_pid):
    while not stop_event.is_set():
        result_queue.put({"audio_apps": [], "mic_pids": set(),
                           "interval_seen": interval.value})
        stop_event.wait(0.05)


def _wait_until(predicate, timeout=3.0, interval=0.05):
    deadline = time.monotonic() + timeout
    last = None
    while time.monotonic() < deadline:
        last = predicate()
        if last:
            return last
        time.sleep(interval)
    return last


class TestComSessionPoller(unittest.TestCase):
    def setUp(self):
        self._poller = None

    def tearDown(self):
        if self._poller is not None:
            self._poller.stop()

    def _make(self, worker_target):
        self._poller = ComSessionPoller(main_pid=os.getpid(), worker_target=worker_target)
        return self._poller

    def test_default_snapshot_before_any_result(self):
        poller = self._make(_fake_worker_blocks_forever)
        poller.start()
        snapshot = poller.get_snapshot()
        self.assertEqual(snapshot, {"audio_apps": [], "mic_pids": set()})

    def test_returns_queued_snapshot_once_worker_reports(self):
        poller = self._make(_fake_worker_puts_once)
        poller.start()
        snapshot = _wait_until(
            lambda: poller.get_snapshot() if poller.get_snapshot()["audio_apps"] else None
        )
        self.assertEqual(snapshot["audio_apps"], ["FakeApp"])
        self.assertEqual(snapshot["mic_pids"], {123})

    def test_dead_worker_is_respawned_after_backoff_elapses(self):
        poller = self._make(_fake_worker_dies_immediately)
        poller.start()
        first_pid = poller._process.pid
        _wait_until(lambda: True if not poller._process.is_alive() else None)
        self.assertFalse(poller._process.is_alive())

        # Bypass the backoff window to simulate enough time having passed.
        poller._last_restart_ts = time.monotonic() - 10.0
        poller.get_snapshot()

        respawned = _wait_until(lambda: poller._process if poller._process.pid != first_pid else None)
        self.assertIsNotNone(respawned)
        self.assertTrue(poller._process.is_alive())

    def test_dead_worker_is_not_respawned_within_backoff_window(self):
        poller = self._make(_fake_worker_dies_immediately)
        poller.start()
        _wait_until(lambda: True if not poller._process.is_alive() else None)
        dead_process = poller._process

        # Simulate a restart having *just* happened.
        poller._last_restart_ts = time.monotonic()
        poller.get_snapshot()

        self.assertIs(poller._process, dead_process)

    def test_set_interval_updates_shared_value_worker_reads(self):
        poller = self._make(_fake_worker_reports_interval)
        poller.start()
        poller.set_interval(2.5)
        snapshot = _wait_until(
            lambda: poller.get_snapshot() if poller.get_snapshot().get("interval_seen") == 2.5 else None
        )
        self.assertIsNotNone(snapshot)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `QT_QPA_PLATFORM=offscreen .venv/Scripts/python.exe -m pytest tests/test_com_session_worker.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.utils.com_session_worker'`.

- [ ] **Step 3: Implement `ComSessionPoller` and the worker loop**

Create `app/utils/com_session_worker.py`:

```python
"""Isolate pycaw/comtypes COM session polling in a separate process.

comtypes' COM proxy finalization occasionally corrupts memory and crashes the
whole process with a native access violation (Windows error 0xc0000005) - not
a catchable Python exception. Running the polling loop in its own process
means that crash kills only the worker; the main app detects it via
Process.is_alive() and respawns it.
"""
import logging
import multiprocessing
import queue
import time

logger = logging.getLogger(__name__)

_RESTART_BACKOFF_SECONDS = 5.0
_JOIN_TIMEOUT_SECONDS = 2.0


def _worker_loop(result_queue, interval, stop_event, main_pid):
    """Entry point for the child process. Loops until stop_event is set."""
    from app.utils.audio_session_monitor import get_active_audio_apps
    from app.utils.meeting_signals import get_mic_capture_pids

    while not stop_event.is_set():
        try:
            audio_apps = get_active_audio_apps()
        except Exception:
            audio_apps = []
        try:
            mic_pids = get_mic_capture_pids(exclude_pid=main_pid)
        except Exception:
            mic_pids = set()

        snapshot = {"audio_apps": audio_apps, "mic_pids": mic_pids}
        try:
            result_queue.get_nowait()
        except queue.Empty:
            pass
        try:
            result_queue.put_nowait(snapshot)
        except queue.Full:
            pass

        stop_event.wait(interval.value)


class ComSessionPoller:
    """Owns a persistent worker process that polls pycaw/comtypes COM state.

    Call start() once at app startup, get_snapshot() from any QTimer tick on
    the main thread to read the latest result (never blocks, never raises),
    and stop() during app shutdown.
    """

    def __init__(self, main_pid=None, worker_target=_worker_loop):
        self._main_pid = main_pid
        self._worker_target = worker_target
        self._queue = multiprocessing.Queue(maxsize=1)
        self._interval = multiprocessing.Value("d", 5.0)
        self._stop_event = multiprocessing.Event()
        self._process = None
        self._cached_snapshot = {"audio_apps": [], "mic_pids": set()}
        self._last_restart_ts = float("-inf")

    def start(self):
        self._stop_event.clear()
        self._process = multiprocessing.Process(
            target=self._worker_target,
            args=(self._queue, self._interval, self._stop_event, self._main_pid),
            daemon=True,
        )
        self._process.start()
        self._last_restart_ts = time.monotonic()

    def get_snapshot(self):
        try:
            self._cached_snapshot = self._queue.get_nowait()
        except queue.Empty:
            pass

        if self._process is not None and not self._process.is_alive():
            now = time.monotonic()
            if now - self._last_restart_ts >= _RESTART_BACKOFF_SECONDS:
                logger.error("COM session worker process died - restarting")
                self.start()

        return self._cached_snapshot

    def set_interval(self, seconds):
        self._interval.value = seconds

    def stop(self):
        if self._process is None:
            return
        self._stop_event.set()
        self._process.join(_JOIN_TIMEOUT_SECONDS)
        if self._process.is_alive():
            self._process.terminate()
            self._process.join(_JOIN_TIMEOUT_SECONDS)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `QT_QPA_PLATFORM=offscreen .venv/Scripts/python.exe -m pytest tests/test_com_session_worker.py -v`
Expected: PASS (5 tests). Each test spawns real (cheap) OS processes running the fake
target functions — no real pycaw/Windows COM calls happen in this test file.

- [ ] **Step 5: Run the full test suite**

Run: `QT_QPA_PLATFORM=offscreen .venv/Scripts/python.exe -m pytest tests/ -q`
Expected: all tests pass (previous count plus 5 new).

- [ ] **Step 6: Commit**

```bash
git add app/utils/com_session_worker.py tests/test_com_session_worker.py
git commit -m "main: add isolated worker process for pycaw/comtypes COM polling"
```

---

### Task 2: Wire `ComSessionPoller` into `MainWindow` lifecycle

**Files:**
- Modify: `app/main_window.py:1-3` (imports), `app/main_window.py:84-87` (poller
  construction/start), `app/main_window.py:2018-2047` (`closeEvent` teardown)
- Test: `tests/test_main_window_com_poller_lifecycle.py` (new)

**Interfaces:**
- Consumes: `ComSessionPoller` from Task 1 (`app/utils/com_session_worker.py`) — exact
  constructor `ComSessionPoller(main_pid=None, worker_target=_worker_loop)`, methods
  `start()`, `stop()`.
- Produces: `MainWindow._com_poller` (a `ComSessionPoller` instance, started in `__init__`,
  stopped in `closeEvent`) — Task 3 and Task 4 both read this attribute.

- [ ] **Step 1: Write the failing test**

Create `tests/test_main_window_com_poller_lifecycle.py`:

```python
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import sys
import unittest
from unittest.mock import MagicMock, patch

from PyQt6.QtWidgets import QApplication

_app = None


def _get_app():
    global _app
    if _app is None:
        _app = QApplication.instance() or QApplication(sys.argv)
    return _app


class TestMainWindowComPollerLifecycle(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        _get_app()

    def test_com_poller_started_in_init(self):
        with patch("app.main_window.ComSessionPoller") as MockPoller:
            mock_instance = MockPoller.return_value
            from app.main_window import MainWindow
            window = MainWindow()
            try:
                MockPoller.assert_called_once_with(main_pid=os.getpid())
                mock_instance.start.assert_called_once_with()
                self.assertIs(window._com_poller, mock_instance)
            finally:
                window._really_quit = True
                window.close()

    def test_com_poller_stopped_on_close(self):
        with patch("app.main_window.ComSessionPoller") as MockPoller:
            mock_instance = MockPoller.return_value
            from app.main_window import MainWindow
            window = MainWindow()
            window._really_quit = True
            window.close()
            mock_instance.stop.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `QT_QPA_PLATFORM=offscreen .venv/Scripts/python.exe -m pytest tests/test_main_window_com_poller_lifecycle.py -v`
Expected: FAIL — `ImportError: cannot import name 'ComSessionPoller' from 'app.main_window'`
(it isn't imported/used there yet).

- [ ] **Step 3: Wire the poller into `MainWindow`**

In `app/main_window.py`, add the import near the other `app.utils` imports (after line 51's
`from app.utils import meeting_signals`):

```python
from app.utils.com_session_worker import ComSessionPoller
```

In `__init__`, immediately after the existing meeting-detection block
(`app/main_window.py:82-87`, ending with `self._meeting_poll_timer.start(3000)`), add:

```python
        self._com_poller = ComSessionPoller(main_pid=os.getpid())
        self._com_poller.start()
```

This must run before `self._setup_ui()` (called at `app/main_window.py:94`), since Task 3
makes `_setup_ui()`'s `SourceSelector(...)` construction depend on `self._com_poller`
already existing — placing it right after the meeting-detection block (before line 89's
`self.setWindowTitle(...)`) satisfies that ordering.

In `closeEvent` (`app/main_window.py:2018-2047`), add the stop call alongside the other
timer/worker shutdown calls, before `self._shutdown_workers()`:

```python
        if self._meeting_poll_timer.isActive():
            self._meeting_poll_timer.stop()
        self._com_poller.stop()
        if hasattr(self, "mic_monitor"):
```

(This inserts the new line between the existing `_meeting_poll_timer.stop()` block and the
`mic_monitor` check — the rest of `closeEvent` is unchanged.)

- [ ] **Step 4: Run test to verify it passes**

Run: `QT_QPA_PLATFORM=offscreen .venv/Scripts/python.exe -m pytest tests/test_main_window_com_poller_lifecycle.py -v`
Expected: PASS (2 tests).

- [ ] **Step 5: Run the full test suite**

Run: `QT_QPA_PLATFORM=offscreen .venv/Scripts/python.exe -m pytest tests/ -q`
Expected: all tests pass. Note: `SourceSelector(config=self.config)` at
`app/main_window.py:246` does not yet accept `com_poller` — Task 3 adds that parameter as
optional with a default, so this task's `MainWindow` construction (which doesn't yet pass
`com_poller=`) keeps working unchanged until Task 3 wires it through.

- [ ] **Step 6: Commit**

```bash
git add app/main_window.py tests/test_main_window_com_poller_lifecycle.py
git commit -m "main: start and stop the COM session poller with the main window"
```

---

### Task 3: Wire `SourceSelector` to read from the poller

**Files:**
- Modify: `app/ui/source_selector.py:78-92` (constructor), `app/ui/source_selector.py:292-317`
  (timer intervals + `_refresh_app_list`)
- Modify: `app/main_window.py:246` (pass `com_poller=self._com_poller` into `SourceSelector`)
- Test: `tests/test_source_selector_com_poller.py` (new)

**Interfaces:**
- Consumes: `MainWindow._com_poller` (Task 2) and its `get_snapshot() -> {"audio_apps": list,
  "mic_pids": set}` / `set_interval(seconds: float)` methods (Task 1).
- Produces: `SourceSelector(config=None, parent=None, com_poller=None)` — `com_poller`
  keyword defaults to `None` so any other construction site (there are none besides
  `main_window.py:246`, confirmed by grep) keeps working; when `None`,
  `_refresh_app_list` treats it as "no data yet" rather than crashing (guards with an
  `if self._com_poller is None: return` at the top, mirroring the existing
  `if self.app_list is None: return` guard immediately below it).

- [ ] **Step 1: Write the failing test**

Create `tests/test_source_selector_com_poller.py`:

```python
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import sys
import unittest
from unittest.mock import MagicMock

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QApplication

from app.ui.source_selector import SourceSelector

_app = None


def _get_app():
    global _app
    if _app is None:
        _app = QApplication.instance() or QApplication(sys.argv)
    return _app


class TestSourceSelectorComPoller(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        _get_app()

    def _make_selector(self):
        poller = MagicMock()
        poller.get_snapshot.return_value = {"audio_apps": [], "mic_pids": set()}
        selector = SourceSelector(config=None, com_poller=poller)
        return selector, poller

    def test_refresh_app_list_reads_from_poller_not_pycaw(self):
        selector, poller = self._make_selector()
        if selector.app_list is None:
            self.skipTest("Per-app UI not available on this Windows version")
        poller.get_snapshot.return_value = {
            "audio_apps": [{"pids": [111], "name": "Zoom",
                             "process_name": "Zoom.exe", "active": True}],
            "mic_pids": set(),
        }
        selector._refresh_app_list()
        self.assertEqual(selector.app_list.count(), 1)
        self.assertIn("Zoom", selector.app_list.item(0).text())

    def test_set_recording_active_updates_poller_interval(self):
        selector, poller = self._make_selector()
        if selector._auto_refresh_timer is None:
            self.skipTest("Per-app UI not available on this Windows version")
        selector.set_recording_active(True)
        poller.set_interval.assert_called_with(2.0)
        selector.set_recording_active(False)
        poller.set_interval.assert_called_with(5.0)

    def test_auto_refresh_timer_uses_relaxed_interval(self):
        selector, poller = self._make_selector()
        if selector._auto_refresh_timer is None:
            self.skipTest("Per-app UI not available on this Windows version")
        self.assertEqual(selector._auto_refresh_timer.interval(), 5000)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `QT_QPA_PLATFORM=offscreen .venv/Scripts/python.exe -m pytest tests/test_source_selector_com_poller.py -v`
Expected: FAIL — `TypeError: SourceSelector.__init__() got an unexpected keyword argument 'com_poller'`.

- [ ] **Step 3: Wire `SourceSelector`**

In `app/ui/source_selector.py`, change the constructor (`:78-92`):

```python
    def __init__(self, config=None, parent=None, com_poller=None):
        super().__init__(parent)
        self._config = config
        self._com_poller = com_poller
        self._mic_devices = []
```

(only the signature line and the new `self._com_poller = com_poller` line are added; the
rest of `__init__` is unchanged.)

Change `_start_auto_refresh` and `set_recording_active` (`:292-305`):

```python
    def _start_auto_refresh(self):
        if self._auto_refresh_timer is None:
            self._auto_refresh_timer = QTimer(self)
            self._auto_refresh_timer.timeout.connect(self._refresh_app_list)
        self._auto_refresh_timer.start(5000)

    def _stop_auto_refresh(self):
        if self._auto_refresh_timer:
            self._auto_refresh_timer.stop()

    def set_recording_active(self, active):
        """Switch to faster polling (2s) during recording for quicker call-end detection."""
        if self._com_poller is not None:
            self._com_poller.set_interval(2.0 if active else 5.0)
        if self._auto_refresh_timer and self._auto_refresh_timer.isActive():
            self._auto_refresh_timer.start(2000 if active else 5000)
```

Change the start of `_refresh_app_list` (`:307-314`) to read from the poller instead of
calling pycaw directly:

```python
    def _refresh_app_list(self):
        """Update the app list with currently active audio apps."""
        if self.app_list is None:
            return
        if self._com_poller is None:
            return

        apps = self._com_poller.get_snapshot()["audio_apps"]
```

(replaces the old `try:`/`from app.utils.audio_session_monitor import
get_active_audio_apps` / `apps = get_active_audio_apps()` / `except Exception as e:` block —
the poller's `get_snapshot()` never raises, so the try/except is no longer needed here. The
rest of `_refresh_app_list`, starting from the `# Filter out hidden apps` comment, is
unchanged.)

In `app/main_window.py:246`, pass the poller through:

```python
        self.source_selector = SourceSelector(config=self.config, com_poller=self._com_poller)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `QT_QPA_PLATFORM=offscreen .venv/Scripts/python.exe -m pytest tests/test_source_selector_com_poller.py -v`
Expected: PASS (3 tests, or SKIP for the two `app_list`/`_auto_refresh_timer`-gated ones if
run on a non-Windows-11 CI box — matches the existing `self._win11` gate already in the
codebase).

- [ ] **Step 5: Run the full test suite**

Run: `QT_QPA_PLATFORM=offscreen .venv/Scripts/python.exe -m pytest tests/ -q`
Expected: all tests pass.

- [ ] **Step 6: Commit**

```bash
git add app/ui/source_selector.py app/main_window.py tests/test_source_selector_com_poller.py
git commit -m "ui: read audio source list from the isolated COM session poller"
```

---

### Task 4: Wire `MainWindow._poll_meeting_signals` to read from the poller

**Files:**
- Modify: `app/main_window.py:643-649`
- Test: `tests/test_main_window_meeting_signals_com_poller.py` (new)

**Interfaces:**
- Consumes: `MainWindow._com_poller.get_snapshot() -> {"audio_apps": list, "mic_pids": set}`
  (Tasks 1-2); `meeting_signals.probe(settings, calendar_event=None, _audio_apps_fn=None,
  _mic_pids_fn=None, ...)` (existing, unchanged, `app/utils/meeting_signals.py:136-138`).

- [ ] **Step 1: Write the failing test**

Create `tests/test_main_window_meeting_signals_com_poller.py`:

```python
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import sys
import unittest
from unittest.mock import MagicMock, patch

from PyQt6.QtWidgets import QApplication

_app = None


def _get_app():
    global _app
    if _app is None:
        _app = QApplication.instance() or QApplication(sys.argv)
    return _app


class TestMainWindowMeetingSignalsComPoller(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        _get_app()

    def _make_window(self):
        from app.main_window import MainWindow
        window = MainWindow()

        def _close():
            window._really_quit = True
            window.close()
        self.addCleanup(_close)
        return window

    def test_poll_meeting_signals_uses_poller_snapshot(self):
        window = self._make_window()
        window.config.data["meeting_detection"] = {"mode": "manual", "apps": ["Zoom"],
                                                     "use_mic_capture": True,
                                                     "use_calendar": False,
                                                     "use_window_title": False}
        fake_snapshot = {
            "audio_apps": [{"pids": [1], "name": "Zoom", "process_name": "Zoom.exe",
                             "active": True}],
            "mic_pids": {1},
        }
        window._com_poller.get_snapshot = MagicMock(return_value=fake_snapshot)
        with patch("app.main_window.meeting_signals.probe") as mock_probe:
            mock_probe.return_value = {"timestamp": 0, "audio_apps": [], "mic_capture_apps": [],
                                        "meeting_titles": [], "calendar_event": None}
            window._poll_meeting_signals()
            self.assertTrue(mock_probe.called)
            _, kwargs = mock_probe.call_args
            self.assertEqual(kwargs["_audio_apps_fn"](), fake_snapshot["audio_apps"])
            self.assertEqual(kwargs["_mic_pids_fn"](), {1})


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `QT_QPA_PLATFORM=offscreen .venv/Scripts/python.exe -m pytest tests/test_main_window_meeting_signals_com_poller.py -v`
Expected: FAIL — `probe()` is called without `_audio_apps_fn`/`_mic_pids_fn` kwargs, so
`kwargs["_audio_apps_fn"]` raises `KeyError`.

- [ ] **Step 3: Wire `_poll_meeting_signals`**

In `app/main_window.py`, change `_poll_meeting_signals` (`:643-649`):

```python
    def _poll_meeting_signals(self):
        settings = self._meeting_settings()
        if settings.get("mode", "off") == "off":
            return
        com_snapshot = self._com_poller.get_snapshot()
        snapshot = meeting_signals.probe(
            settings, calendar_event=self._current_calendar_event,
            _audio_apps_fn=lambda: com_snapshot["audio_apps"],
            _mic_pids_fn=lambda: com_snapshot["mic_pids"])
        self._last_meeting_snapshot = snapshot
```

(`com_snapshot` is read once per poll tick, then both closures reference that single
snapshot — avoids taking two independent, potentially-inconsistent reads of the poller
within the same tick. The rest of the method is unchanged.)

- [ ] **Step 4: Run test to verify it passes**

Run: `QT_QPA_PLATFORM=offscreen .venv/Scripts/python.exe -m pytest tests/test_main_window_meeting_signals_com_poller.py -v`
Expected: PASS (1 test).

- [ ] **Step 5: Run the full test suite**

Run: `QT_QPA_PLATFORM=offscreen .venv/Scripts/python.exe -m pytest tests/ -q`
Expected: all tests pass.

- [ ] **Step 6: Commit**

```bash
git add app/main_window.py tests/test_main_window_meeting_signals_com_poller.py
git commit -m "main: feed meeting-signal probe from the isolated COM session poller"
```

---

### Task 5: Docs and manual verification

**Files:**
- Modify: `CLAUDE.md:95` (file listing), `CLAUDE.md:207-208` (Audio Session Monitoring
  section)

- [ ] **Step 1: Update `CLAUDE.md`'s file listing**

At `CLAUDE.md:95`, add the new file to the `utils/` listing (immediately after the existing
`audio_session_monitor.py` line):

```
      audio_session_monitor.py        # Per-app audio session enumeration (pycaw)
      com_session_worker.py           # Isolated worker process for pycaw/comtypes COM polling
```

- [ ] **Step 2: Update the Audio Session Monitoring section**

At `CLAUDE.md:207-208`, replace:

```
- `audio_session_monitor.py` uses pycaw + psutil to enumerate audio apps
- Two sources: pycaw (apps with active audio sessions) + psutil (known audio apps like Teams/Zoom even when not in a call)
- Groups by display name (deduplicates multi-process apps like Zoom)
- Returns `{"pids": [int], "name": str, "process_name": str, "active": bool}`
- Auto-refreshes every 3 seconds in the UI
```

with:

```
- `audio_session_monitor.py` uses pycaw + psutil to enumerate audio apps
- Two sources: pycaw (apps with active audio sessions) + psutil (known audio apps like Teams/Zoom even when not in a call)
- Groups by display name (deduplicates multi-process apps like Zoom)
- Returns `{"pids": [int], "name": str, "process_name": str, "active": bool}`
- All pycaw/comtypes COM calls (this module's `get_active_audio_apps()` and
  `meeting_signals.get_mic_capture_pids()`) run inside a separate OS process owned by
  `com_session_worker.ComSessionPoller` — comtypes' COM proxy finalization can crash the
  whole process natively (confirmed via production Windows Event Log correlation), so
  isolating it means only the worker dies; `MainWindow` detects and silently respawns it.
- Auto-refreshes every 5 seconds in the UI (2 seconds while recording), read from the
  poller's cached snapshot rather than calling pycaw directly
```

- [ ] **Step 3: Commit the docs update**

```bash
git add CLAUDE.md
git commit -m "docs: document the isolated COM session polling worker"
```

## Manual Verification (after Task 4 — requires a real Windows session, cannot be automated)

1. Launch the app normally (not under `QT_QPA_PLATFORM=offscreen`).
2. Let it run for at least 15 minutes with normal usage: browse recordings, open the Audio
   Sources panel, have a conferencing app (Teams/Zoom/etc.) open and joined to something.
3. Confirm the Audio Sources app list and the meeting-detection suggestion banner both
   continue to update correctly during that session.
4. Open Task Manager and confirm a second `pythonw.exe`/`python.exe` process (the worker) is
   running alongside the main app process.
5. Check Windows Event Viewer → Windows Logs → Application, Event ID 1000, for any new
   `pythonw.exe` / `_ctypes.pyd` / exception code `0xc0000005` entries during the session —
   there should be none for the *main* app process (a crash in the worker process is
   expected to still happen on its own schedule and is not a failure of this fix; check
   `talktrack.log` for a "COM session worker process died - restarting" line to confirm it
   was contained).
6. If possible, deliberately end the worker process via Task Manager and confirm: the main
   app keeps running without interruption, the source list/meeting banner briefly go stale
   then resume within a few seconds, and `talktrack.log` shows the restart being logged.

## Self-Review

**1. Spec coverage:** `ComSessionPoller`/`_worker_loop` with the exact snapshot shape,
caching, and backoff behavior (Task 1) ✓. `MainWindow` construction/start/stop lifecycle
(Task 2) ✓. `SourceSelector` reading from the poller, relaxed 5s/2s cadence, `set_interval`
call (Task 3) ✓. `_poll_meeting_signals` reusing `probe()`'s existing injection seam (Task
4) ✓. CLAUDE.md documentation update (Task 5) ✓. Manual verification steps carried over
from the spec word-for-word, adjusted to name this plan's specific log message ✓. Out-of-scope
items (`_process_com.py`, the underlying comtypes defect) — no task touches them ✓.

**2. Placeholder scan:** No TBD/TODO; every step has complete, runnable code including full
test files and exact before/after code blocks for every modified function.

**3. Type consistency:** `get_snapshot()` returns `{"audio_apps": list, "mic_pids": set}` in
Task 1's implementation, and every consumer (Task 3's `_refresh_app_list`, Task 4's
`_poll_meeting_signals`) reads exactly those two keys with those shapes — no drift. Method
names (`start`, `stop`, `get_snapshot`, `set_interval`) are used identically across all four
tasks. `SourceSelector.__init__`'s new `com_poller=None` default matches how Task 2's
`MainWindow` (which doesn't pass it until Task 3) and Task 3's own tests both construct it.
