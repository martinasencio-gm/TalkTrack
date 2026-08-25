"""Running one recording through transcription and diarization, headless.

The workers are the app's own QThread workers, used verbatim. They are
driven by calling ``run()`` directly rather than ``start()``: ``run()`` is
an ordinary method, so calling it executes the body inline on this thread,
and signals emitted back to the same thread are direct connections that
fire before ``run()`` returns. No event loop, no second thread, no
duplicated pipeline logic that could drift from the GUI's.

The branch structure below mirrors MainWindow._on_transcription_finished
exactly. If that changes, this has to change with it.
"""
import logging
import time
from dataclasses import dataclass, field

from app.transcription.track_merge import dual_track_plan
from app.utils import session_io

logger = logging.getLogger(__name__)


@dataclass
class BatchSettings:
    """The subset of settings.json a batch run needs."""

    model_size: str = "base"
    language: str = None
    device: str = "cpu"
    hf_token: str = ""
    diarize: bool = False
    min_speakers: int = None
    max_speakers: int = None
    replace_you_with_name: bool = False
    user_name: str = ""

    @classmethod
    def from_config(cls, config, diarize=None):
        hf_token = ""
        try:
            hf_token = config.get("diarization", "hf_token") or ""
        except Exception:
            pass

        want = False
        try:
            want = config.get("diarization", "enabled") if diarize is None else diarize
        except Exception:
            want = bool(diarize)

        replace_you = False
        user_name = ""
        try:
            replace_you = bool(config.get("general", "replace_you_with_name"))
        except Exception:
            pass
        try:
            from app.utils.platform_info import get_current_user_name
            user_name = get_current_user_name(config)
        except Exception:
            pass

        can_diarize = bool(want and hf_token)

        return cls(
            model_size=config.get("transcription", "model_size"),
            language=config.get("transcription", "language"),
            device=config.get("transcription", "device"),
            hf_token=hf_token,
            diarize=can_diarize,
            min_speakers=config.get("diarization", "min_speakers"),
            max_speakers=config.get("diarization", "max_speakers"),
            replace_you_with_name=replace_you,
            user_name=user_name,
        )


@dataclass
class JobOutcome:
    ok: bool
    message: str
    segments: int = 0
    diarized: bool = False
    per_track: bool = False
    bleed_dropped: int = 0
    elapsed: float = 0.0
    warnings: list = field(default_factory=list)


class _Workers:
    """Lazy loader for transcription/diarization workers."""

    def __init__(self, transcription=None, diarization=None, simple=None):
        self._transcription = transcription
        self._diarization = diarization
        self._simple = simple

    def transcription(self, *args, **kwargs):
        if self._transcription is None:
            from app.transcription.transcriber import TranscriptionWorker
            self._transcription = TranscriptionWorker
        return self._transcription(*args, **kwargs)

    def diarization(self, *args, **kwargs):
        if self._diarization is None:
            from app.transcription.diarizer import DiarizationWorker
            self._diarization = DiarizationWorker
        return self._diarization(*args, **kwargs)

    def simple(self, *args, **kwargs):
        if self._simple is None:
            from app.transcription.diarizer import SimpleDiarizeWorker
            self._simple = SimpleDiarizeWorker
        return self._simple(*args, **kwargs)


def _drive(worker, on_progress=None):
    """Run a worker to completion inline. Returns (result, error_message).

    A cancelled worker reports neither; nothing in a batch run cancels, so
    that lands as an error rather than being silently treated as success.
    """
    captured = {"result": None, "error": None}
    worker.finished.connect(lambda r: captured.__setitem__("result", r))
    worker.error.connect(lambda m: captured.__setitem__("error", m))
    if on_progress is not None and hasattr(worker, "progress"):
        worker.progress.connect(on_progress)
    worker.run()
    if captured["result"] is None and captured["error"] is None:
        captured["error"] = "worker produced no result"
    return captured["result"], captured["error"]


def run_job(job, settings, workers=None, on_progress=None):
    """Transcribe (and optionally diarize) one recording, writing it to disk."""
    workers = workers or _Workers()
    started = time.monotonic()
    warnings = []

    def progress(message):
        if on_progress is not None:
            on_progress(message)

    # Full diarization clusters voices across the whole file, so when it is going
    # to run the mix must stay intact — dual_track_plan already declines
    # in that case, but the argument order matters.
    tracks = dual_track_plan(
        job.session, settings.diarize, settings.hf_token
    )

    worker = workers.transcription(
        job.audio_path,
        model_size=settings.model_size,
        language=settings.language,
        device=settings.device,
        tracks=tracks,
        # Nothing is capturing audio in a batch run, so there is no
        # real-time callback to leave headroom for.
        full_cpu=True,
    )
    result, error = _drive(worker, progress)
    if result is None:
        return JobOutcome(False, f"transcription failed: {error}",
                          elapsed=time.monotonic() - started)

    bleed_dropped = getattr(worker, "bleed_dropped", 0)
    diarized = False

    if tracks:
        # Per-track transcription already labelled every segment; running
        # a diarizer over them would relabel real speakers as guesses.
        if bleed_dropped:
            warnings.append(
                f"dropped {bleed_dropped} mic segments as bleed — the mic is "
                "hearing the call audio"
            )
    elif settings.diarize:
        progress("Identifying speakers...")
        diarize_worker = workers.diarization(
            job.audio_path, result,
            hf_token=settings.hf_token,
            min_speakers=settings.min_speakers,
            max_speakers=settings.max_speakers,
            full_cpu=True,
        )
        diarized_result, diarize_error = _drive(diarize_worker, progress)
        if diarized_result is None:
            # Best-effort: the transcript succeeded and must still be
            # saved. Losing it because the labelling stage failed was the
            # original #14 bug.
            warnings.append(f"diarization failed: {diarize_error}")
        else:
            result = diarized_result
            diarized = True
    else:
        audio_files = job.session.get("audio_files") or {}
        mic_path = audio_files.get("mic")
        sys_path = audio_files.get("system")
        if mic_path and sys_path:
            progress("Labeling speakers...")
            simple_worker = workers.simple(mic_path, sys_path, result)
            simple_result, simple_error = _drive(simple_worker, progress)
            if simple_result is None:
                warnings.append(f"speaker labelling failed: {simple_error}")
            else:
                result = simple_result
                diarized = True

    # The GUI does this in _display_final_transcript; the output format has
    # to match whichever path produced it.
    result.merge_adjacent_same_speaker()

    speaker_names = None
    if not diarized and settings.replace_you_with_name:
        if any(getattr(s, "speaker", "") == "You" for s in result.segments):
            if settings.user_name and settings.user_name.strip().lower() != "you":
                speaker_names = session_io.load_speaker_names(job.session)
                if "You" not in speaker_names:
                    speaker_names["You"] = settings.user_name.strip()

    if not session_io.write_transcript(job.session, result, speaker_names=speaker_names):
        return JobOutcome(False, "could not write the transcript to disk",
                          segments=len(result.segments),
                          elapsed=time.monotonic() - started)

    return JobOutcome(
        True, "transcribed",
        segments=len(result.segments),
        diarized=diarized,
        per_track=bool(tracks),
        bleed_dropped=bleed_dropped,
        elapsed=time.monotonic() - started,
        warnings=warnings,
    )
