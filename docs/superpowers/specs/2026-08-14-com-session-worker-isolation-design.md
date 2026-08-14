# Isolate pycaw/comtypes COM Polling in a Separate Process — Design

## Background

Root-caused via `superpowers:systematic-debugging` in response to a user report of
"the app is crashing":

- Windows Event Log shows 9 identical hard crashes in one day: `pythonw.exe` faulting
  inside `_ctypes.pyd` with exception code `0xc0000005` (access violation) at the exact
  same fault offset every time — a deterministic, reproducible native crash, not random
  memory pressure.
- Each crash lands 1-3 seconds after a burst of `ValueError: COM method call without
  VTable` in `talktrack.log`, raised from `comtypes\_post_coinit\unknwn.py`'s `__del__` →
  `Release()`. That's `comtypes` finalizing a COM proxy object whose interface pointer is
  already invalid — usually just a caught Python exception, but occasionally severe enough
  to corrupt memory and crash the whole process natively.
- The crash signature predates this session's work (present in the log from 2026-08-13),
  so it is unrelated to Phase 1-3 changes shipped today.
- `comtypes` 1.4.16 (already the latest release) claims Python 3.14 CI coverage, but this
  app's usage pattern — sub-second-to-few-second COM session enumeration from a Qt timer,
  indefinitely, for the life of the process — is a much more aggressive call pattern than
  typical `comtypes` usage and plausibly exercises an edge case its own test suite doesn't
  cover. Not something this project can fix upstream.

Two independent, unrelated `QTimer`s trigger this churn today, both calling into pycaw/comtypes
on every tick:

1. `SourceSelector._auto_refresh_timer` (`app/ui/source_selector.py:292-305`) — 3s idle / 1s
   while recording — calls `get_active_audio_apps()`
   (`app/utils/audio_session_monitor.py:102`), which calls `AudioUtilities.GetAllSessions()`.
2. `MainWindow._meeting_poll_timer` (`app/main_window.py:85-87`) — fixed 3s — calls
   `meeting_signals.probe()`, which calls **the same** `get_active_audio_apps()`
   (`meeting_signals.py:91-93`) *and* its own separate COM enumeration via
   `get_mic_capture_pids()` (`meeting_signals.py:34-88`, using
   `IMMDeviceEnumerator`/`IAudioSessionManager2`/`IAudioSessionControl2` directly). A comment
   at `meeting_signals.py:76-81` shows a prior attempt to work around this exact class of
   vtable-corruption bug in this function specifically — evidently insufficient on its own,
   since the crashes continue.

Both timers run on the Qt main thread. Neither can be made crash-proof from within Python:
an access violation is a native fault, not a catchable exception. The only way to stop it
from taking down the whole app is to make sure it happens in a *different* process.

## Fix

**Move all pycaw/comtypes COM session enumeration into one persistent, isolated worker
process.** Both existing pure functions (`get_active_audio_apps()`,
`get_mic_capture_pids()`) are unchanged — only *where* they run changes.

### New module: `app/utils/com_session_worker.py`

- `_worker_loop(result_queue, interval, stop_event, main_pid)` — the child process entry
  point. Loops until `stop_event` is set:
  1. `audio_apps = get_active_audio_apps()` (each call already wrapped internally against
     ordinary exceptions; unchanged).
  2. `mic_pids = get_mic_capture_pids(exclude_pid=main_pid)` — `main_pid` is the *main*
     process's PID, passed in at worker start, so the existing "exclude our own idle mic
     session" behavior continues to work correctly now that the probe itself runs in a
     different OS process than the app's own audio capture.
  3. Pack `{"audio_apps": audio_apps, "mic_pids": mic_pids}`, drop any stale item already
     in the maxsize-1 `result_queue` (`get_nowait()`, ignore `Empty`), then
     `put_nowait(snapshot)`.
  4. `stop_event.wait(interval.value)` — sleeps for the current interval but wakes
     immediately if `stop()` is called, so shutdown is prompt.
  - Each pycaw call keeps its own try/except (already present in both functions) so an
    ordinary Python-level error doesn't end the loop — only a true native crash kills the
    process, which is precisely the failure mode being contained here.

- `ComSessionPoller` — owned by `MainWindow`, constructed once at startup:
  - `__init__(main_pid=None, worker_target=_worker_loop)` — `worker_target` is overridable
    purely for testing (see Testing section); defaults to the real loop. Creates (but does
    not start) a `multiprocessing.Queue(maxsize=1)`, `multiprocessing.Value('d', 5.0)`
    (interval seconds), `multiprocessing.Event()` (stop signal), and internal state:
    `_cached_snapshot = {"audio_apps": [], "mic_pids": set()}`, `_process = None`,
    `_last_restart_ts = float("-inf")`.
  - `start()` — creates and starts the `multiprocessing.Process(target=self._worker_target,
    args=(queue, interval, stop_event, main_pid), daemon=True)`.
  - `get_snapshot()` — the single method both consumers call on every timer tick:
    1. Drain the queue non-blockingly (`get_nowait()`); if a new item is present, replace
       `_cached_snapshot`.
    2. If `_process.is_alive()` is False: if `time.monotonic() - _last_restart_ts >= 5.0`,
       log the crash at ERROR via the module logger, create a fresh `Process` (same target/
       args, new stop_event state cleared) and start it, and set `_last_restart_ts =
       time.monotonic()`. Otherwise skip restarting this tick (avoids a tight crash loop
       pegging CPU) and try again next tick.
    3. Return `_cached_snapshot` (whatever it currently holds — last-known-good, possibly
       stale by up to one restart cycle, never `None`).
  - `set_interval(seconds)` — `self._interval.value = seconds`.
  - `stop()` — `self._stop_event.set()`, `self._process.join(timeout=2)`, then
    `self._process.terminate()` if still alive as a last resort (mirrors the existing
    `_shutdown_workers()` pattern for `QThread`s in `main_window.py:2049-2072`).

### Wiring changes

- `MainWindow.__init__` — construct `self._com_poller = ComSessionPoller(main_pid=os.getpid())`
  and call `self._com_poller.start()` near where `_meeting_poll_timer` is already set up
  (`main_window.py:85-87`); pass `com_poller=self._com_poller` into the existing
  `SourceSelector(config=self.config)` constructor call (`main_window.py:246`).
- `SourceSelector.__init__` — accept and store `com_poller`. `_refresh_app_list`
  (`source_selector.py:307-314`) replaces its direct `get_active_audio_apps()` call with
  `self._com_poller.get_snapshot()["audio_apps"]`. `_start_auto_refresh`/`set_recording_active`
  (`source_selector.py:292-305`) change their `QTimer` intervals from 3000/1000ms to
  5000/2000ms (this session's chosen relaxed cadence) and `set_recording_active` also calls
  `self._com_poller.set_interval(2.0 if active else 5.0)` so the worker's own pacing matches
  what's being displayed.
- `MainWindow._poll_meeting_signals` — build `_audio_apps_fn`/`_mic_pids_fn` closures that
  read `self._com_poller.get_snapshot()["audio_apps"]` / `["mic_pids"]`, and pass them into
  the existing `meeting_signals.probe(settings, calendar_event, _audio_apps_fn=...,
  _mic_pids_fn=...)` call — using `probe()`'s existing dependency-injection parameters
  (`meeting_signals.py:136-138`), so `meeting_signals.py`'s own logic is untouched.
- `MainWindow.closeEvent` — add `self._com_poller.stop()` to the teardown sequence
  (`main_window.py:2018-2047`), alongside the other timer/worker shutdown calls.

### Windows spawn considerations

Windows' default `multiprocessing` start method is `spawn`, which re-imports the target
module fresh in the child rather than forking. `main.py` already guards its app-launch code
behind `if __name__ == "__main__":` (`main.py:280`), so the child process re-importing
`main.py` during spawn bootstrap does not relaunch the whole app. `com_session_worker.py`
itself has no import-time side effects (mirrors `audio_session_monitor.py`'s existing
side-effect-free import), so it's safe for the child to import fresh.

## Error handling

No new error paths inside `get_active_audio_apps()` or `get_mic_capture_pids()` — both are
used exactly as they are today. The only new failure mode being introduced is "worker
process died," handled entirely inside `ComSessionPoller.get_snapshot()` per above: detect,
log, backoff, restart, keep serving the last-known-good snapshot in the meantime. Never
surfaced to the user — consistent with the "silently auto-restart" behavior chosen for this
fix.

## Testing

- Unit tests for `ComSessionPoller` inject a trivial fake `worker_target` (e.g. one that
  puts a fixed snapshot onto the queue once and then waits on `stop_event`) instead of the
  real pycaw-calling loop — this keeps tests fast, platform-independent of real Windows COM
  state, and avoids flaking on machines without active audio sessions, while still spawning
  a real (cheap) OS process to exercise the actual `multiprocessing` plumbing. Cover:
  - `get_snapshot()` returns the default empty snapshot before any worker result has
    arrived.
  - `get_snapshot()` returns the queued snapshot after the fake worker puts one.
  - Killing the process externally (`process.terminate()` then `process.join()`) and
    calling `get_snapshot()` again triggers a respawn (`is_alive()` becomes True again) and
    logs the crash.
  - Two dead-process detections within 5 seconds only restart once (backoff respected).
  - `set_interval()` updates the shared `Value` that a fake worker reads back.
- `SourceSelector` tests mock `com_poller.get_snapshot()`'s return value and assert
  `_refresh_app_list` renders it correctly (existing test patterns from this session, e.g.
  `tests/test_recordings_list_batch_transcript.py`, extend similarly for this widget).
- `MainWindow._poll_meeting_signals` tests mock `self._com_poller.get_snapshot()` and assert
  the closures passed into `probe()` return the expected `audio_apps`/`mic_pids` values.
- Manual verification (documented, not automated — requires a real Windows session with
  real audio apps running, same constraint as the Phase 3 spec's manual step):
  1. Launch the app normally and let it run for at least the length of the shortest
     observed crash gap seen in production logs (~15 minutes), with normal usage
     (recording, browsing recordings, an audio/conferencing app open).
  2. Confirm no new `pythonw.exe` / `_ctypes.pyd` / `0xc0000005` entries appear in the
     Windows Event Log (Event Viewer → Windows Logs → Application, Event ID 1000) for the
     main app process.
  3. Confirm the audio-source app list (Audio Sources panel) and the meeting-detection
     suggestion banner both continue to update correctly during that session.
  4. If possible, deliberately kill the worker process via Task Manager (it will appear as
     a second `pythonw.exe`/`python.exe` process) and confirm: the main app keeps running
     without interruption, the source list/meeting banner briefly go stale then resume
     within a few seconds, and `talktrack.log` shows a logged restart.

## Out of scope

- `app/recording/_process_com.py` (per-app loopback capture COM code) — runs once per
  recording start/stop, not on a tight poll; nothing in the crash evidence implicates it.
- Fixing the underlying `comtypes`/CPython 3.14 interaction that causes the vtable
  corruption in the first place — third-party, out of this project's control. This design
  contains the blast radius; it does not eliminate the occasional worker-process crash
  itself (which is expected to still happen, just harmlessly, on its own schedule).
- Any UI change beyond the two existing timer intervals (5s/2s instead of 3s/1s) — no new
  indicators, no new settings.

## Self-review

- No placeholders — every component names its exact file, exact call sites, and exact
  method signatures.
- Internal consistency: the design explicitly reuses `probe()`'s existing
  `_audio_apps_fn`/`_mic_pids_fn` injection seam rather than modifying `meeting_signals.py`,
  and reuses `get_active_audio_apps()`/`get_mic_capture_pids()` verbatim rather than
  rewriting COM logic — the fix is entirely about *where* code runs, not *what* it does.
- Scope: covers both known frequent-polling call sites (confirmed via grep across
  `app/`), explicitly excludes the one-shot COM path (`_process_com.py`) that isn't
  implicated by the evidence.
- Ambiguity: cadence numbers (5s/2s), backoff window (5s), queue size (1), and shutdown
  timeout (2s join then terminate) are all pinned to specific values with reasoning, not
  left as "reasonable defaults."
