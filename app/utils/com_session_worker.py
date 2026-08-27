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

from app.utils.render_activity import pick_output_index, update_activity

logger = logging.getLogger(__name__)

# Respawn throttle. The worker crashes natively (comtypes proxy finalization)
# often enough that a flat backoff never engages during a crash storm — see
# the escalation observed in talktrack.log. Backoff doubles per restart still
# inside a sliding window, capped, and decays on its own once the window
# clears.
_RESTART_BACKOFF_MIN = 5.0
_RESTART_BACKOFF_MAX = 120.0
_RESTART_WINDOW_SECONDS = 120.0
_JOIN_TIMEOUT_SECONDS = 2.0


def _effective_backoff(recent_restarts, now):
    """Seconds to wait before the next respawn, given restart timestamps.

    One restart in the window → 2×min, two → 4×min, … capped at max.
    Timestamps older than the window don't count, so a worker that stays up
    is back to the floor.
    """
    live = sum(1 for t in recent_restarts if now - t < _RESTART_WINDOW_SECONDS)
    return min(_RESTART_BACKOFF_MIN * (2 ** live), _RESTART_BACKOFF_MAX)


def _suppress_crash_dialog():
    """Stop Windows from popping a GPF / "Application Error" dialog when this
    process crashes natively.

    The worker exists to absorb comtypes' access-violation crashes; the
    parent respawns it. Without this, each crash also blocks the user behind
    a modal WER dialog (the child runs under pythonw.exe and a spawn-start
    child does not inherit the parent's error mode).
    """
    try:
        import ctypes

        SEM_FAILCRITICALERRORS = 0x0001
        SEM_NOGPFAULTERRORBOX = 0x0002
        SEM_NOOPENFILEERRORBOX = 0x8000
        ctypes.windll.kernel32.SetErrorMode(
            SEM_FAILCRITICALERRORS | SEM_NOGPFAULTERRORBOX | SEM_NOOPENFILEERRORBOX
        )
    except Exception:
        # Non-Windows, or kernel32 unavailable — nothing to suppress.
        pass


def _worker_loop(result_queue, interval, stop_event, main_pid):
    """Entry point for the child process. Loops until stop_event is set."""
    _suppress_crash_dialog()
    from app.utils.audio_session_monitor import get_active_audio_apps, get_app_active_devices
    from app.utils.meeting_signals import get_mic_capture_pids
    from app.utils.render_activity import sample_render_peaks

    while not stop_event.is_set():
        try:
            audio_apps = get_active_audio_apps()
        except Exception:
            audio_apps = []
        try:
            mic_pids = get_mic_capture_pids(exclude_pid=main_pid)
        except Exception:
            mic_pids = set()
        try:
            render_peaks = sample_render_peaks()
        except Exception:
            render_peaks = {}
        try:
            app_devices = get_app_active_devices(exclude_pid=main_pid)
        except Exception:
            app_devices = {}

        snapshot = {"audio_apps": audio_apps, "mic_pids": mic_pids,
                    "render_peaks": render_peaks, "app_devices": app_devices}
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
        self._queue = None
        self._interval = multiprocessing.Value("d", 2.0)
        self._stop_event = multiprocessing.Event()
        self._process = None
        self._cached_snapshot = {"audio_apps": [], "mic_pids": set(),
                                 "render_peaks": {}, "app_devices": {}}
        self._last_restart_ts = float("-inf")
        # Monotonic timestamps of respawns (not the initial start), pruned to
        # _RESTART_WINDOW_SECONDS. Feeds _effective_backoff.
        self._recent_restarts = []
        self._render_history = {}

    def start(self):
        # Fresh queue per worker generation: multiprocessing.Queue's
        # internal put semaphore is only released by a successful get, so if
        # a worker dies (native access violation) after put() acquires it
        # but before the feeder thread flushes bytes to the pipe, the
        # semaphore is stuck at 0 forever. Reusing that queue across
        # respawns would wedge every future worker's put_nowait() silently.
        self._queue = multiprocessing.Queue(maxsize=1)
        self._stop_event.clear()
        self._process = multiprocessing.Process(
            target=self._worker_target,
            args=(self._queue, self._interval, self._stop_event, self._main_pid),
            daemon=True,
        )
        self._process.start()
        self._last_restart_ts = time.monotonic()

    def get_snapshot(self):
        if self._queue is not None:
            try:
                self._cached_snapshot = self._queue.get_nowait()
                # Fold render peaks only on a genuinely new snapshot. Doing
                # it per call would keep refreshing "recently active" from a
                # cached sample after the worker died, so a stale endpoint
                # would look live forever.
                self._render_history = update_activity(
                    self._render_history,
                    self._cached_snapshot.get("render_peaks", {}),
                    time.monotonic(),
                )
            except queue.Empty:
                pass
            except Exception:
                logger.exception("Failed to read snapshot from worker queue; returning cached")

        if self._process is not None and not self._process.is_alive():
            now = time.monotonic()
            self._recent_restarts = [
                t for t in self._recent_restarts
                if now - t < _RESTART_WINDOW_SECONDS
            ]
            backoff = _effective_backoff(self._recent_restarts, now)
            if now - self._last_restart_ts >= backoff:
                logger.error(
                    "COM session worker process died - restarting "
                    "(restart #%d within %.0fs, next backoff %.0fs)",
                    len(self._recent_restarts) + 1,
                    _RESTART_WINDOW_SECONDS, backoff,
                )
                try:
                    self.start()
                    self._recent_restarts.append(time.monotonic())
                except Exception:
                    logger.exception("Failed to respawn worker process; returning cached snapshot")
                    self._last_restart_ts = time.monotonic()

        return self._cached_snapshot

    def active_output_index(self, outputs):
        """Device index of the output endpoint currently rendering audio.

        None means no opinion — nothing is playing, or it's playing to an
        endpoint that isn't in `outputs` (hidden by the user, no loopback).
        Callers must fall back rather than treat None as a selection.
        """
        return pick_output_index(self._render_history, outputs, time.monotonic())

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
        self._process = None
