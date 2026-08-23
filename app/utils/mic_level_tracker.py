"""Rolling peak-dB tracker for the always-on idle mic monitor.

Feeds the "quiet mic" pre-flight check (app/utils/preflight_status.py) with
the loudest level seen over a trailing window, not a single chunk's — a
single quiet chunk right when the check happens to run (the user paused
between words) shouldn't flip the verdict, and a fresh start with zero
samples shouldn't either.
"""

import time

import numpy as np

# Same floor as the live DAW meters (app/ui/level_meter.py) — kept in sync
# by convention rather than a shared import, since this module stays
# Qt-free and the meters module pulls in PyQt6.
DB_FLOOR = -60.0

_WINDOW_SECONDS = 4.0
_MIN_SAMPLE_SECONDS = 1.5  # don't judge quietness before this much audio has arrived


def peak_db(chunk: np.ndarray) -> float:
    """Peak-sample dB for one audio chunk: 20*log10(max|x|), clamped to
    DB_FLOOR. Same convention as the DAW meter bars (app/ui/meters_panel.py)
    so "quiet" here means the same thing "quiet" means on the meter."""
    if chunk.size == 0:
        return DB_FLOOR
    peak = float(np.max(np.abs(chunk)))
    if peak < 1e-10:
        return DB_FLOOR
    return max(20.0 * np.log10(peak), DB_FLOOR)


class MicLevelTracker:
    """Tracks the loudest peak seen in a trailing window of wall-clock time.

    Fed from the audio callback thread (MicMonitor's level_callback) via
    `ingest()` — plain list append/pop under the GIL, no Qt calls, matching
    the "callbacks never touch Qt directly" rule for audio-thread code.
    Read from the UI thread via `peak_db_over_window()`.
    """

    def __init__(self, window_seconds=_WINDOW_SECONDS,
                 min_sample_seconds=_MIN_SAMPLE_SECONDS, clock=time.monotonic):
        self._window_seconds = window_seconds
        self._min_sample_seconds = min_sample_seconds
        self._clock = clock
        self._samples = []  # [(timestamp, peak_db), ...]
        self._started_at = None

    def reset(self):
        self._samples = []
        self._started_at = None

    def ingest(self, chunk: np.ndarray):
        now = self._clock()
        if self._started_at is None:
            self._started_at = now
        self._samples.append((now, peak_db(chunk)))
        cutoff = now - self._window_seconds
        while self._samples and self._samples[0][0] < cutoff:
            self._samples.pop(0)

    def peak_db_over_window(self):
        """Loudest peak in the trailing window, or None if not enough audio
        has arrived yet to trust the reading (fresh start, or mic not
        actually producing data)."""
        if self._started_at is None:
            return None
        if self._clock() - self._started_at < self._min_sample_seconds:
            return None
        if not self._samples:
            return None
        return max(db for _, db in self._samples)
