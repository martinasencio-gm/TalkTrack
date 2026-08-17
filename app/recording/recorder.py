import logging
import os
import time
import json
import subprocess
from datetime import datetime
from pathlib import Path
from enum import Enum

from PyQt6.QtCore import QObject, pyqtSignal, QThread

from app.recording.audio_capture import DualAudioCapture

logger = logging.getLogger(__name__)


def convert_to_mp3(audio_files):
    """Add MP3 copies of every WAV track, in place. Best-effort."""
    mp3_files = {}
    for key, wav_path in audio_files.items():
        if wav_path and wav_path.endswith(".wav"):
            mp3_path = wav_path.replace(".wav", ".mp3")
            try:
                subprocess.run(
                    ["ffmpeg", "-y", "-i", wav_path, "-codec:a", "libmp3lame",
                     "-qscale:a", "2", mp3_path],
                    capture_output=True, check=True, timeout=300,
                )
                mp3_files[key + "_mp3"] = mp3_path
            except (subprocess.CalledProcessError, FileNotFoundError,
                    subprocess.TimeoutExpired):
                pass  # FFmpeg missing, failed, or hung — keep the WAV
    audio_files.update(mp3_files)


class FinalizeWorker(QThread):
    """Assembles a stopped recording's files off the UI thread.

    Only file work runs here. The devices — and in per-app mode their
    comtypes COM proxies — are already closed by the caller on the UI
    thread, because releasing those off-thread can crash the process
    natively.
    """

    progress = pyqtSignal(str)
    finalized = pyqtSignal(dict)
    failed = pyqtSignal(str)

    def __init__(self, capture, convert_mp3=False):
        super().__init__()
        self._capture = capture
        self._convert_mp3 = convert_mp3
        # Also exposed as an attribute, not just via the signal: at
        # shutdown the queued signal never gets delivered (the event loop
        # is on its way out), and the result still has to be saved.
        self.audio_files = None

    def run(self):
        try:
            self.progress.emit("Saving audio tracks...")
            audio_files = self._capture.finalize()
            if self._convert_mp3:
                self.progress.emit("Converting to MP3...")
                convert_to_mp3(audio_files)
            self.audio_files = audio_files
            self.finalized.emit(audio_files)
        except Exception as e:
            # Never raise out of run() — an exception here would lose the
            # recording silently and leave the recorder stuck in PROCESSING.
            logger.exception("Finalizing the recording failed")
            self.failed.emit(str(e))


class RecordingState(Enum):
    IDLE = "idle"
    RECORDING = "recording"
    PAUSED = "paused"
    STOPPING = "stopping"
    PROCESSING = "processing"


class Recorder(QObject):
    """Orchestrates audio recording with state management."""

    state_changed = pyqtSignal(RecordingState)
    time_updated = pyqtSignal(float)
    recording_finished = pyqtSignal(dict)
    recording_discarded = pyqtSignal(float)  # duration of discarded recording
    finalize_progress = pyqtSignal(str)      # what the finalize worker is doing
    error_occurred = pyqtSignal(str)
    mic_level = pyqtSignal(object)
    system_level = pyqtSignal(object)
    silence_detected = pyqtSignal(float)  # seconds of silence
    capture_status = pyqtSignal(dict)   # {"total": N, "active": K, "failures": {pid: str}}
    pid_lost = pyqtSignal(int, str)     # (pid, hresult_name)
    capture_lost = pyqtSignal()

    def __init__(self, config):
        super().__init__()
        self.config = config
        self._state = RecordingState.IDLE
        self._capture = None
        self._timer_thread = None
        self._current_session = None
        self._finalize_worker = None

    @property
    def state(self):
        return self._state

    def _set_state(self, state):
        self._state = state
        self.state_changed.emit(state)

    def start_recording(self, mic_device=None, loopback_device=None,
                        capture_mode="legacy", app_pids=None,
                        mic_device_2=None):
        """Start a new recording session."""
        if self._state != RecordingState.IDLE:
            return

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        output_dir = Path(self.config.get("output", "directory"))
        session_dir = output_dir / f"recording_{timestamp}"
        session_dir.mkdir(parents=True, exist_ok=True)

        self._current_session = {
            "id": timestamp,
            "directory": str(session_dir),
            "started_at": datetime.now().isoformat(),
            "mic_device": mic_device,
            "mic_device_2": mic_device_2,
            "loopback_device": loopback_device,
            "capture_mode": capture_mode,
            "app_pids": app_pids or [],
        }

        sample_rate = self.config.get("audio", "sample_rate")

        self._capture = DualAudioCapture(
            mic_device=mic_device,
            loopback_device=loopback_device,
            sample_rate=sample_rate,
            capture_mode=capture_mode,
            app_pids=app_pids,
            mic_device_2=mic_device_2,
        )
        self._capture.set_level_callbacks(
            mic_callback=lambda chunk: self.mic_level.emit(chunk),
            system_callback=lambda chunk: self.system_level.emit(chunk),
        )

        # Configure silence detection on system audio
        if self.config.get("general", "silence_auto_stop"):
            silence_dur = self.config.get("general", "silence_duration")
            self._capture.set_silence_detection(
                threshold=0.005,
                duration=silence_dur,
                callback=lambda secs: self.silence_detected.emit(secs),
            )

        self._capture.set_capture_event_callbacks(
            pid_lost=lambda pid, err: self.pid_lost.emit(pid, err),
            capture_lost=lambda: self.capture_lost.emit(),
        )

        try:
            self._capture.start(session_dir)
            if self._capture._capture_status is not None:
                self.capture_status.emit(self._capture._capture_status)
            self._set_state(RecordingState.RECORDING)
            self._start_timer()
        except Exception as e:
            self.error_occurred.emit(str(e))
            self._set_state(RecordingState.IDLE)

    def pause_recording(self):
        if self._state != RecordingState.RECORDING:
            return
        self._capture.pause()
        self._set_state(RecordingState.PAUSED)

    def resume_recording(self):
        if self._state != RecordingState.PAUSED:
            return
        self._capture.resume()
        self._set_state(RecordingState.RECORDING)

    def stop_recording(self):
        """Stop capture and hand the file work to a worker thread.

        Returns as soon as the devices are closed. Mixing the tracks costs
        seconds on a long recording (~6s for 20 minutes), and doing it here
        froze the window — this method runs on the UI thread. The session
        is not announced until the worker finishes; state is PROCESSING in
        between, which blocks a new recording from starting on top of it.
        """
        if self._state not in (RecordingState.RECORDING, RecordingState.PAUSED):
            return

        elapsed = self._capture.get_elapsed_time() if self._capture else 0.0
        logger.info("stop_recording called after %.1fs", elapsed)
        self._set_state(RecordingState.STOPPING)
        self._stop_timer()

        try:
            self._capture.stop_streams()
        except Exception as e:
            logger.exception("Failed to stop capture streams")
            self.error_occurred.emit(f"Error stopping recording: {e}")
            self._set_state(RecordingState.IDLE)
            return

        duration = self._capture.get_elapsed_time()
        self._current_session["stopped_at"] = datetime.now().isoformat()
        if self._capture._capture_status is not None:
            self._current_session["capture_status"] = self._capture._capture_status
        self._current_session["duration"] = duration

        # Too short to keep — drop the tracks without paying for the mix.
        min_length = self.config.get("general", "min_recording_length")
        if min_length and duration < min_length:
            self._discard_session(duration)
            return

        self._set_state(RecordingState.PROCESSING)
        self._start_finalize_worker()

    def _discard_session(self, duration):
        """Throw away a recording that came in under the minimum length."""
        import shutil
        try:
            self._capture.discard()
        except Exception:
            logger.exception("Failed to discard capture writers")
        try:
            shutil.rmtree(self._current_session["directory"])
        except OSError:
            logger.exception("Could not remove discarded session directory")
        self._set_state(RecordingState.IDLE)
        self.recording_discarded.emit(duration)

    def _start_finalize_worker(self):
        convert_mp3 = self.config.get("output", "format") == "mp3"
        self._finalize_worker = FinalizeWorker(self._capture, convert_mp3=convert_mp3)
        self._finalize_worker.progress.connect(self.finalize_progress.emit)
        self._finalize_worker.finalized.connect(self._on_finalized)
        self._finalize_worker.failed.connect(self._on_finalize_failed)
        self._finalize_worker.start()

    def _on_finalized(self, audio_files):
        self._current_session["audio_files"] = audio_files
        try:
            from app.utils.atomic_io import atomic_write_json
            meta_path = Path(self._current_session["directory"]) / "metadata.json"
            atomic_write_json(meta_path, self._current_session, indent=2)
        except OSError as e:
            logger.exception("Could not write session metadata")
            self.error_occurred.emit(f"Could not save recording metadata: {e}")
        self._set_state(RecordingState.IDLE)
        self.recording_finished.emit(self._current_session)

    def _on_finalize_failed(self, message):
        self.error_occurred.emit(f"Error stopping recording: {message}")
        self._set_state(RecordingState.IDLE)

    def is_finalizing(self):
        return (self._finalize_worker is not None
                and self._finalize_worker.isRunning())

    def finalize_worker(self):
        """The running finalize worker, for shutdown coordination."""
        return self._finalize_worker

    def finish_pending_finalize(self):
        """Apply a completed finalize result without the event loop.

        Shutdown waits for the worker, but the queued `finalized` signal is
        never delivered — nothing is spinning the event loop by then.
        Without this the tracks would be left on disk with no metadata.json
        beside them, i.e. an orphaned session that only the crash-recovery
        scan would find. Safe to call twice; the PROCESSING check is the
        guard, since _on_finalized leaves the state IDLE.
        """
        worker = self._finalize_worker
        if worker is None or worker.isRunning():
            return
        if self._state != RecordingState.PROCESSING:
            return
        if worker.audio_files is None:
            return  # it failed — there is nothing to record
        self._on_finalized(worker.audio_files)

    def _start_timer(self):
        self._timer_running = True
        self._timer_thread = TimerThread(self._capture)
        self._timer_thread.time_tick.connect(self.time_updated.emit)
        self._timer_thread.start()

    def _stop_timer(self):
        self._timer_running = False
        if self._timer_thread:
            self._timer_thread.stop()
            self._timer_thread.wait()
            self._timer_thread = None

    def get_elapsed_time(self):
        if self._capture:
            return self._capture.get_elapsed_time()
        return 0


class TimerThread(QThread):
    time_tick = pyqtSignal(float)

    def __init__(self, capture):
        super().__init__()
        self._capture = capture
        self._running = True

    def run(self):
        while self._running:
            self.time_tick.emit(self._capture.get_elapsed_time())
            self.msleep(100)

    def stop(self):
        self._running = False
