import logging
import math
import os
import threading
import time
from pathlib import Path
from dataclasses import dataclass, field, fields
from PyQt6.QtCore import QObject, pyqtSignal, QThread

logger = logging.getLogger(__name__)

# The loaded Whisper model, keyed by (model_size, device, compute_type,
# cpu_threads). Loading costs seconds-to-tens-of-seconds, so the model in use
# stays resident between transcriptions by design.
#
# Exactly ONE is kept. The key spans both model_size and cpu_threads, so an
# unbounded cache accumulated a separate multi-GB model for every size the
# user had ever selected AND for each of the two thread counts —
# base+small+medium x2 measured at ~4GB retained, enough to page the whole
# app out and leave it sluggish long after a big job finished. Staying warm
# for repeated jobs at the same settings is the case that mattered; switching
# model size (or moving between recording-era and idle jobs) now pays one
# reload instead of hoarding both.
_MODEL_CACHE = {}
_MODEL_CACHE_MAXSIZE = 1
_MODEL_CACHE_LOCK = threading.Lock()


def _whisper_cpu_threads(full_cpu):
    """CTranslate2 thread-pool size for a transcription job.

    Half the cores (min 1) while a recording is in progress, so the pool
    leaves headroom for the real-time audio capture callback. With nothing
    recording there is no callback to protect and that cap merely doubled
    the run time, so idle jobs get every core but one — the same core the
    diarizer holds back to keep the UI responsive.
    """
    cpu_count = os.cpu_count() or 4
    return max(1, cpu_count - 1) if full_cpu else max(1, cpu_count // 2)


def _get_model(model_size, device, compute_type, full_cpu=False):
    # cpu_threads is fixed at construction, so it belongs in the cache key:
    # without it, the first model built during a recording would serve
    # every later idle job at half speed.
    cpu_threads = _whisper_cpu_threads(full_cpu)
    key = (model_size, device, compute_type, cpu_threads)
    with _MODEL_CACHE_LOCK:
        if key not in _MODEL_CACHE:
            from faster_whisper import WhisperModel
            # Evict BEFORE constructing, so peak residency is one model rather
            # than briefly two. A job already running holds its own reference
            # to the model it was handed, so dropping ours cannot pull a model
            # out from under it.
            # No gc.collect() here: _get_model runs on a worker thread, and a
            # global collection there can finalize GUI-thread QObjects. Popping
            # drops the last reference, so CTranslate2 releases the weights
            # via refcounting before the replacement is built.
            while len(_MODEL_CACHE) >= _MODEL_CACHE_MAXSIZE:
                _MODEL_CACHE.pop(next(iter(_MODEL_CACHE)))
            _MODEL_CACHE[key] = WhisperModel(
                model_size, device=device, compute_type=compute_type,
                cpu_threads=cpu_threads,
            )
        return _MODEL_CACHE[key]


@dataclass
class TranscriptSegment:
    start: float
    end: float
    text: str
    speaker: str = ""
    confidence: float = 0.0
    original_text: str = ""

    @classmethod
    def from_dict(cls, d):
        """Create a TranscriptSegment from a dict, ignoring unknown keys."""
        known = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in d.items() if k in known})

    def to_dict(self):
        d = {
            "start": self.start,
            "end": self.end,
            "text": self.text,
            "speaker": self.speaker,
            "confidence": self.confidence,
        }
        if self.original_text:
            d["original_text"] = self.original_text
        return d


@dataclass
class TranscriptResult:
    segments: list = field(default_factory=list)
    language: str = ""
    duration: float = 0.0
    model_size: str = ""
    transcribe_seconds: float = 0.0

    @classmethod
    def from_dict(cls, d):
        """Rebuild a TranscriptResult from a transcript.json dict.

        Mirrors ``TranscriptSegment.from_dict`` — the inverse of
        ``to_dict``, tolerant of a file that predates a given field.
        """
        d = d or {}
        return cls(
            segments=[TranscriptSegment.from_dict(s) for s in d.get("segments", [])],
            language=d.get("language", ""),
            duration=d.get("duration", 0.0),
            model_size=d.get("model_size", ""),
            transcribe_seconds=d.get("transcribe_seconds", 0.0),
        )

    def _display_speaker(self, seg, speaker_names=None):
        """Return the display name for a segment's speaker."""
        if not seg.speaker:
            return ""
        if speaker_names and seg.speaker in speaker_names and speaker_names[seg.speaker]:
            return speaker_names[seg.speaker]
        return seg.speaker

    def merge_adjacent_same_speaker(self, max_gap=0.5):
        """Merge consecutive same-speaker segments separated by <= max_gap seconds.

        Whisper's own segmentation splits a single continuous turn into many
        small segments at clause/sentence boundaries, often with zero gap
        between them — independent of any real pause. Left unmerged, each of
        those boundaries also becomes a hard cut point during per-segment /
        continuous playback, where Whisper's imprecise timestamps can
        audibly clip a word. Merging fixes both the fragmented display and
        the playback clipping at once.
        """
        if not self.segments:
            return
        merged = [self.segments[0]]
        for seg in self.segments[1:]:
            prev = merged[-1]
            if seg.speaker == prev.speaker and seg.start - prev.end <= max_gap:
                prev.end = seg.end
                prev.text = f"{prev.text} {seg.text}".strip()
                prev.confidence = min(prev.confidence, seg.confidence)
            else:
                merged.append(seg)
        self.segments = merged

    def to_dict(self, speaker_names=None):
        segments = []
        for s in self.segments:
            d = s.to_dict()
            if speaker_names and s.speaker in speaker_names and speaker_names[s.speaker]:
                d["speaker_name"] = speaker_names[s.speaker]
            segments.append(d)
        return {
            "segments": segments,
            "language": self.language,
            "duration": self.duration,
            "model_size": self.model_size,
            "transcribe_seconds": self.transcribe_seconds,
        }

    def to_text(self, speaker_names=None):
        lines = []
        for seg in self.segments:
            display = self._display_speaker(seg, speaker_names)
            speaker = f"[{display}] " if display else ""
            timestamp = f"[{_format_time(seg.start)} -> {_format_time(seg.end)}]"
            lines.append(f"{timestamp} {speaker}{seg.text}")
        return "\n".join(lines)

    def to_plain_text(self, speaker_names=None):
        """Clipboard-friendly plain text: '{speaker}: {text}' per line, blank line between speaker changes, no timestamps."""
        if not self.segments:
            return ""
        lines = []
        prev_speaker = None
        for seg in self.segments:
            display = self._display_speaker(seg, speaker_names)
            if prev_speaker is not None and seg.speaker != prev_speaker:
                lines.append("")
            text = seg.text.strip()
            if display:
                lines.append(f"{display}: {text}")
            else:
                lines.append(text)
            prev_speaker = seg.speaker
        return "\n".join(lines)

    def to_srt(self, speaker_names=None):
        lines = []
        for i, seg in enumerate(self.segments, 1):
            start_ts = _format_srt_time(seg.start)
            end_ts = _format_srt_time(seg.end)
            display = self._display_speaker(seg, speaker_names)
            speaker = f"[{display}] " if display else ""
            lines.append(f"{i}")
            lines.append(f"{start_ts} --> {end_ts}")
            lines.append(f"{speaker}{seg.text}")
            lines.append("")
        return "\n".join(lines)


def _format_time(seconds):
    m, s = divmod(int(seconds), 60)
    h, m = divmod(m, 60)
    return f"{h:02d}:{m:02d}:{s:02d}"


def _format_srt_time(seconds):
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    ms = int((seconds % 1) * 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


class TranscriptionWorker(QThread):
    """Runs transcription in a background thread."""

    progress = pyqtSignal(str)
    progress_percent = pyqtSignal(int)
    finished = pyqtSignal(TranscriptResult)
    error = pyqtSignal(str)

    cancelled = pyqtSignal()

    def __init__(self, audio_path, model_size="base", language=None, device="cpu",
                 tracks=None, full_cpu=False):
        """tracks is an optional [(speaker, path), ...].

        When given, each track is transcribed separately and the results
        merged into one timeline. That keeps the mixed audio away from
        Whisper — where speaker bleed shows up as a doubled copy of every
        remote sentence — and makes speaker labels structural rather than
        a guess from comparing the two tracks' loudness. The FIRST track is
        the echo-prone one; see track_merge.merge_tracks.
        """
        super().__init__()
        self.audio_path = audio_path
        self.model_size = model_size
        self.language = language
        self.device = device
        self.tracks = tracks
        self.full_cpu = full_cpu
        # Segments removed as bleed copies — the only visible evidence that
        # the mic is hearing the speakers.
        self.bleed_dropped = 0
        self._cancel_requested = False

    def cancel(self):
        """Request cancellation of the transcription."""
        self._cancel_requested = True

    def _transcribe_one(self, model, path, progress_base=0.0, progress_span=1.0):
        """Transcribe one file. Returns (segments, info), or None if cancelled."""
        segments_gen, info = model.transcribe(
            path, language=self.language, vad_filter=True,
        )
        segments = []
        for segment in segments_gen:
            if self._cancel_requested:
                return None
            segments.append(TranscriptSegment(
                start=segment.start,
                end=segment.end,
                text=segment.text.strip(),
                confidence=math.exp(segment.avg_logprob),
            ))
            if info.duration:
                fraction = min(1.0, segment.end / info.duration)
                self.progress_percent.emit(
                    int(min(100, (progress_base + fraction * progress_span) * 100)))
        return segments, info

    def _run_single(self, model):
        self.progress.emit("Transcribing audio...")
        outcome = self._transcribe_one(model, self.audio_path)
        if outcome is None:
            return None
        segments, info = outcome
        result = TranscriptResult(language=info.language, duration=info.duration)
        result.segments = segments
        return result

    def _run_tracks(self, model):
        """Transcribe each track separately, then merge into one timeline.

        A track whose file is missing or unreadable contributes nothing and
        does not fail the job — a recording with no mic (or no system
        audio) must still produce the transcript of the track it does have.
        """
        from app.transcription.track_merge import merge_tracks

        span = 1.0 / len(self.tracks)
        transcribed = []
        language, duration = None, 0.0
        for i, (speaker, path) in enumerate(self.tracks):
            if self._cancel_requested:
                return None
            self.progress.emit(f"Transcribing {speaker} audio...")
            try:
                outcome = self._transcribe_one(model, path, i * span, span)
            except Exception:
                logger.exception("Track %s (%s) failed to transcribe", speaker, path)
                continue
            if outcome is None:
                return None
            segments, info = outcome
            transcribed.append((speaker, segments))
            language = language or info.language
            duration = max(duration, info.duration or 0.0)

        result = TranscriptResult(language=language, duration=duration)
        result.segments = merge_tracks(transcribed)
        self.bleed_dropped = (
            sum(len(segments) for _, segments in transcribed) - len(result.segments)
        )
        return result

    def run(self):
        start_time = time.monotonic()
        try:
            if self._cancel_requested:
                self.cancelled.emit()
                return

            self.progress.emit("Loading transcription model...")

            device = self.device
            if device == "cuda":
                try:
                    import torch
                    if not torch.cuda.is_available():
                        self.progress.emit(
                            "CUDA selected but not available — falling back to CPU. "
                            "Install CUDA PyTorch for GPU acceleration: "
                            "pip install torch torchaudio --index-url https://download.pytorch.org/whl/cu126"
                        )
                        device = "cpu"
                except ImportError:
                    self.progress.emit("PyTorch not found — falling back to CPU.")
                    device = "cpu"

            compute_type = "float16" if device == "cuda" else "int8"
            model = _get_model(self.model_size, device, compute_type, self.full_cpu)

            if self._cancel_requested:
                self.cancelled.emit()
                return

            if self.tracks:
                result = self._run_tracks(model)
            else:
                result = self._run_single(model)

            if result is None:
                self.cancelled.emit()
                return

            result.model_size = self.model_size
            result.transcribe_seconds = time.monotonic() - start_time

            self.progress.emit("Transcription complete.")
            self.progress_percent.emit(100)
            self.finished.emit(result)

        except ImportError as e:
            self.error.emit(f"Transcription dependency missing: {e}")
        except Exception as e:
            self.error.emit(f"Transcription failed: {e}")
