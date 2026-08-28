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
from datetime import datetime
from pathlib import Path

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
    # The raw config["ai"] sub-dict (provider, api_key, model, local_model_*).
    # Holds the API key — must never be logged, and BatchSettings has no
    # __repr__ that would dump it (dataclass default repr is only ever
    # printed by tests, never by the runner).
    ai_config: dict = field(default_factory=dict)

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

        ai_config = {}
        try:
            ai_config = dict(getattr(config, "data", {}).get("ai", {}) or {})
        except Exception:
            pass

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
            ai_config=ai_config,
        )


@dataclass
class JobOutcome:
    ok: bool
    message: str
    segments: int = 0
    diarized: bool = False
    per_track: bool = False
    bleed_dropped: int = 0
    summarized: bool = False
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


def _distinct_speakers(result):
    """How many non-empty speaker labels a transcript carries."""
    return len({
        (getattr(s, "speaker", "") or "").strip()
        for s in result.segments
        if (getattr(s, "speaker", "") or "").strip()
    })


def _disk_segment_count(directory):
    data = session_io._read_json(Path(directory) / "transcript.json")
    if isinstance(data, dict) and isinstance(data.get("segments"), list):
        return len(data["segments"])
    return 0


def _run_batch_summary(session, settings, progress):
    """Generate summary.md (+ summary_meta.json) for a session, headless.

    The summary markdown carries its own ``## Action Items`` section — there
    is no separate action-items file. Returns True when the summary was
    written. Raises on provider / SDK errors — run_job catches those and
    records them as a non-fatal warning, so a failed summary never costs a
    good transcript. The AI SDKs do not put the API key in their exception
    messages, and settings.ai_config is never logged.
    """
    from app.ai.provider_factory import create_provider, describe_ai_model
    from app.ai.summarizer import build_summary_prompt

    provider = create_provider(settings.ai_config)
    if provider is None:
        return False

    result = session_io.load_transcript(session)
    if result is None:
        return False

    segments = result.segments
    speaker_names = session_io.load_speaker_names(session)
    notes = session_io._read_text(Path(session["directory"]) / "notes.txt") or ""
    max_chars = provider.max_context_chars

    progress("Generating summary...")
    t0 = time.monotonic()
    summary = provider.complete(build_summary_prompt(
        segments, speaker_names, notes, max_transcript_chars=max_chars))

    meta = {
        # Distinct from the app's "talktrack-app" and the
        # talktrack-batch-summarize skill's own stamp — three producers,
        # no collision.
        "generated_by": "talktrack-batch",
        "model": describe_ai_model(settings.ai_config),
        "seconds": round(time.monotonic() - t0, 1),
        "generated_at": datetime.now().isoformat(timespec="seconds"),
    }
    return session_io.write_summary(session, summary, meta)


def run_job(job, settings, workers=None, on_progress=None):
    """Run one recording through its queued operations, writing each to disk.

    ``job.ops`` is the ordered op set (transcription → diarization →
    summarization). A stage is skipped when its output already exists.
    Stage order and the diarization sub-branching mirror
    MainWindow._on_transcription_finished — change one, change the other.
    """
    workers = workers or _Workers()
    started = time.monotonic()
    warnings = []
    ops = list(getattr(job, "ops", None) or ["transcription"])

    directory = Path(job.session["directory"])
    result = None
    diarized = False
    per_track = False
    bleed_dropped = 0

    def progress(message):
        if on_progress is not None:
            on_progress(message)

    # --- Stage 1: transcription (folds diarization in when both are queued) ---
    if "transcription" in ops:
        # Full diarization clusters voices across the whole file, so when it
        # is going to run the mix must stay intact — dual_track_plan already
        # declines in that case, but the argument order matters.
        tracks = dual_track_plan(job.session, "diarization" in ops, settings.hf_token)

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
        per_track = bool(tracks)

        if tracks:
            # Per-track transcription already labelled every segment; running
            # a diarizer over them would relabel real speakers as guesses.
            if bleed_dropped:
                warnings.append(
                    f"dropped {bleed_dropped} mic segments as bleed — the mic is "
                    "hearing the call audio"
                )
        elif "diarization" in ops:
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
                # saved. Losing it because the labelling stage failed was
                # the original #14 bug.
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

        # The GUI does this in _display_final_transcript; the output format
        # has to match whichever path produced it.
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

    # --- Stage 2: speaker recognition over an existing transcript ---
    elif "diarization" in ops:
        if not (directory / "transcript.json").exists():
            return JobOutcome(False, "speaker recognition needs a transcript",
                              elapsed=time.monotonic() - started)
        loaded = session_io.load_transcript(job.session)
        if loaded is None:
            warnings.append("speaker recognition skipped — transcript unreadable")
        elif _distinct_speakers(loaded) > 1:
            pass  # already diarized; nothing to add
        else:
            progress("Identifying speakers...")
            diarize_worker = workers.diarization(
                job.audio_path, loaded,
                hf_token=settings.hf_token,
                min_speakers=settings.min_speakers,
                max_speakers=settings.max_speakers,
                full_cpu=True,
            )
            diarized_result, diarize_error = _drive(diarize_worker, progress)
            if diarized_result is None:
                warnings.append(f"diarization failed: {diarize_error}")
            else:
                diarized_result.merge_adjacent_same_speaker()
                if session_io.write_transcript(job.session, diarized_result):
                    result = diarized_result
                    diarized = True
                else:
                    warnings.append("could not write the diarized transcript to disk")

    # --- Stage 3: summarization ---
    # Reached only when stage 1 did not return early, so a transcript
    # exists (freshly written, or already on disk). A diarization *warning*
    # from an earlier stage does not block this.
    summarized = False
    if "summarization" in ops:
        if (directory / "summary.md").exists():
            summarized = True                       # already done; skip
        elif not (directory / "transcript.json").exists():
            warnings.append("summarization skipped — no transcript")
        else:
            try:
                summarized = _run_batch_summary(job.session, settings, progress)
            except Exception as e:
                warnings.append(f"summarization failed: {type(e).__name__}: {e}")
            else:
                if not summarized:
                    warnings.append("summarization failed: no AI provider configured")

    segments = len(result.segments) if result is not None else _disk_segment_count(directory)

    return JobOutcome(
        True,
        "transcribed" if "transcription" in ops else "processed",
        segments=segments,
        diarized=diarized,
        per_track=per_track,
        bleed_dropped=bleed_dropped,
        summarized=summarized,
        elapsed=time.monotonic() - started,
        warnings=warnings,
    )
