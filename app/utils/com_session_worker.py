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

_RESTART_BACKOFF_SECONDS = 5.0
_JOIN_TIMEOUT_SECONDS = 2.0


def _worker_loop(result_queue, interval, stop_event, main_pid):
    """Entry point for the child process. Loops until stop_event is set."""
    from app.utils.audio_session_monitor import get_active_audio_apps
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

        snapshot = {"audio_apps": audio_apps, "mic_pids": mic_pids,
                    "render_peaks": render_peaks}
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
                                 "render_peaks": {}}
        self._last_restart_ts = float("-inf")
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
            if now - self._last_restart_ts >= _RESTART_BACKOFF_SECONDS:
                logger.error("COM session worker process died - restarting")
                try:
                    self.start()
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
