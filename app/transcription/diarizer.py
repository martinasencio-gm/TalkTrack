import bisect
import threading
from dataclasses import dataclass
from PyQt6.QtCore import QThread, pyqtSignal

from app.transcription.transcriber import TranscriptResult, TranscriptSegment

# Loaded pyannote pipelines keyed by HF token. from_pretrained costs many
# seconds; the pipeline stays resident between recordings by design.
_PIPELINE_CACHE = {}
_PIPELINE_CACHE_LOCK = threading.Lock()


def _get_pipeline(hf_token):
    with _PIPELINE_CACHE_LOCK:
        if hf_token not in _PIPELINE_CACHE:
            import warnings
            # Suppress pyannote's torchcodec warning — TalkTrack preloads waveforms
            # into memory via soundfile (see DiarizationWorker.run), so torchcodec
            # is never used for audio decoding.
            warnings.filterwarnings("ignore", category=UserWarning, module="pyannote")
            from pyannote.audio import Pipeline
            _PIPELINE_CACHE[hf_token] = Pipeline.from_pretrained(
                "pyannote/speaker-diarization-community-1",
                token=hf_token,
            )
        return _PIPELINE_CACHE[hf_token]


@dataclass
class SpeakerSegment:
    start: float
    end: float
    speaker: str


class DiarizationWorker(QThread):
    """Runs speaker diarization in a background thread using pyannote.audio."""

    progress = pyqtSignal(str)
    finished = pyqtSignal(TranscriptResult)
    error = pyqtSignal(str)
    cancelled = pyqtSignal()

    def __init__(self, audio_path, transcript_result, hf_token="",
                 min_speakers=None, max_speakers=None, full_cpu=False):
        super().__init__()
        self.audio_path = audio_path
        self.transcript_result = transcript_result
        self.hf_token = hf_token
        self.min_speakers = min_speakers
        self.max_speakers = max_speakers
        self.full_cpu = full_cpu
        self._cancel_requested = False

    def cancel(self):
        """Request cooperative cancellation of speaker diarization."""
        self._cancel_requested = True

    def run(self):
        try:
            self._run_pyannote()
        except ImportError as e:
            self.error.emit(f"Diarization dependency missing: {e}")
        except Exception as e:
            self.error.emit(f"Diarization failed: {e}")

    def _run_pyannote(self):
        if self._cancel_requested:
            self.cancelled.emit()
            return

        self.progress.emit("Loading speaker diarization model...")

        if not self.hf_token:
            self.error.emit(
                "HuggingFace token required for pyannote.audio. "
                "Get one at https://huggingface.co/settings/tokens and "
                "accept the model terms at "
                "https://huggingface.co/pyannote/speaker-diarization-community-1"
            )
            return

        pipeline = _get_pipeline(self.hf_token)
        if self._cancel_requested:
            self.cancelled.emit()
            return

        self.progress.emit("Loading audio for diarization...")

        # Pre-load audio via soundfile to avoid torchcodec dependency.
        # pyannote 4.0 accepts {"waveform": tensor, "sample_rate": int}.
        import os
        import soundfile as sf
        import torch

        # Nearly the full core count when nothing else needs headroom
        # (one held back so browsing recordings during diarization stays
        # responsive — this is the heaviest torch workload in the app and
        # saturating every core visibly stalls the UI thread's own work);
        # capped to half (min 1) while a recording is actively in
        # progress, so torch's thread pool doesn't starve the real-time
        # audio capture callback.
        cpu_count = os.cpu_count() or 4
        torch.set_num_threads(
            max(1, cpu_count - 1) if self.full_cpu else max(1, cpu_count // 2)
        )

        audio_data, sample_rate = sf.read(self.audio_path, dtype="float32")
        if audio_data.ndim == 1:
            waveform = torch.from_numpy(audio_data).unsqueeze(0)
        else:
            waveform = torch.from_numpy(audio_data.T)
        audio_input = {"waveform": waveform, "sample_rate": sample_rate}

        if self._cancel_requested:
            self.cancelled.emit()
            return

        self.progress.emit("Running speaker diarization...")

        diarization_params = {}
        if self.min_speakers is not None:
            diarization_params["min_speakers"] = self.min_speakers
        if self.max_speakers is not None:
            diarization_params["max_speakers"] = self.max_speakers

        result = pipeline(audio_input, **diarization_params)
        if self._cancel_requested:
            self.cancelled.emit()
            return

        # pyannote 4.0 returns DiarizeOutput; extract the Annotation
        if hasattr(result, "speaker_diarization"):
            diarization = result.speaker_diarization
        else:
            diarization = result  # fallback for older versions

        # Extract speaker segments
        speaker_segments = []
        for turn, _, speaker in diarization.itertracks(yield_label=True):
            speaker_segments.append(SpeakerSegment(
                start=turn.start,
                end=turn.end,
                speaker=speaker,
            ))

        if self._cancel_requested:
            self.cancelled.emit()
            return

        # The whole recording was decoded into `waveform` up front; on a long
        # meeting that is a multi-GB allocation, and holding it through the
        # merge (and past this worker's own lifetime) is what leaves the app
        # swapped out and sluggish once a big job finishes. Dropping the last
        # reference is enough: these are arrays and tensors, not reference
        # cycles, so refcounting frees them here and now.
        #
        # Deliberately NO gc.collect(). This runs on a worker thread, and a
        # global collection there can finalize QObjects owned by the GUI
        # thread. Any cycles among the pyannote objects are picked up by the
        # automatic generational collector moments later anyway; the multi-GB
        # arrays are not cyclic and do not need it.
        del audio_input, waveform, audio_data, diarization, result

        self.progress.emit("Mapping speakers to transcript...")

        # Assign speakers to transcript segments
        result = self._merge_diarization_with_transcript(
            self.transcript_result, speaker_segments
        )
        if self._cancel_requested:
            self.cancelled.emit()
            return

        self.finished.emit(result)

    def _merge_diarization_with_transcript(self, transcript, speaker_segments):
        """Assign speaker labels to transcript segments based on overlap.

        Bounded search, not the full cross product. Speaker turns are sorted
        by start and paired with `reach` — a running maximum of their end
        times — so each transcript segment only walks back through turns that
        can still touch it, and stops as soon as `reach` drops to its start.
        The old nested scan was O(segments x turns): 20k x 20k measured at
        ~98s, i.e. a multi-second stall at the tail of every long meeting.

        Ties keep the earliest-starting speaker, matching the previous
        input-order behaviour (pyannote yields turns chronologically).

        The prune assumes turns are mostly short relative to the recording,
        which is what pyannote produces; one turn spanning the whole file
        would degrade back toward the nested scan.
        """
        if not speaker_segments:
            for seg in transcript.segments:
                seg.speaker = "Unknown"
            return transcript

        ordered = sorted(speaker_segments, key=lambda s: s.start)
        starts = [s.start for s in ordered]
        reach = []
        furthest = float("-inf")
        for spk_seg in ordered:
            furthest = max(furthest, spk_seg.end)
            reach.append(furthest)

        for seg in transcript.segments:
            best_speaker = "Unknown"
            best_overlap = 0.0
            seg_start = seg.start
            seg_end = seg.end

            # Turns starting at or after seg_end cannot overlap it.
            i = bisect.bisect_left(starts, seg_end) - 1
            while i >= 0 and reach[i] > seg_start:
                spk_seg = ordered[i]
                overlap = min(seg_end, spk_seg.end) - max(seg_start, spk_seg.start)
                # >= so that the earliest start wins a tie: descending walk
                # visits it last.
                if overlap > 0 and overlap >= best_overlap:
                    best_overlap = overlap
                    best_speaker = spk_seg.speaker
                i -= 1

            seg.speaker = best_speaker

        return transcript


class SimpleDiarizer:
    """Simple diarization using mic vs system audio channel separation.

    Falls back to this when pyannote is not available.
    Uses the separate mic and system audio tracks to determine
    if the local user or a remote participant is speaking.
    """

    def __init__(self, mic_audio_path, system_audio_path):
        self.mic_audio_path = mic_audio_path
        self.system_audio_path = system_audio_path

    def diarize(self, transcript):
        """Assign 'You' or 'Remote' labels based on audio energy in each channel."""
        import numpy as np
        import soundfile as sf

        mic_data = None
        sys_data = None

        if self.mic_audio_path:
            mic_data, mic_sr = sf.read(self.mic_audio_path, dtype="float32")
            if mic_data.ndim > 1:
                mic_data = mic_data.mean(axis=1)

        if self.system_audio_path:
            sys_data, sys_sr = sf.read(self.system_audio_path, dtype="float32")
            if sys_data.ndim > 1:
                sys_data = sys_data.mean(axis=1)

        if mic_data is None and sys_data is None:
            return transcript

        def _window_rms(data, rate, start_s, end_s):
            # Each track is indexed with its OWN sample rate — mic and system
            # can legitimately be recorded at different rates.
            start = int(start_s * rate)
            end = min(int(end_s * rate), len(data))
            if start >= len(data):
                return 0.0
            chunk = data[start:end]
            return float(np.sqrt(np.mean(chunk ** 2))) if len(chunk) > 0 else 0.0

        for seg in transcript.segments:
            mic_energy = 0.0
            sys_energy = 0.0

            if mic_data is not None:
                mic_energy = _window_rms(mic_data, mic_sr, seg.start, seg.end)

            if sys_data is not None:
                sys_energy = _window_rms(sys_data, sys_sr, seg.start, seg.end)

            if mic_energy > sys_energy * 1.5:
                seg.speaker = "You"
            elif sys_energy > mic_energy * 1.5:
                seg.speaker = "Remote"
            else:
                seg.speaker = "You" if mic_energy >= sys_energy else "Remote"

        return transcript


class SimpleDiarizeWorker(QThread):
    """Runs SimpleDiarizer off the GUI thread.

    Reading both full WAVs plus per-segment RMS freezes the UI for seconds
    on long recordings when run inline.
    """

    finished = pyqtSignal(TranscriptResult)
    error = pyqtSignal(str)

    def __init__(self, mic_audio_path, system_audio_path, transcript_result):
        super().__init__()
        self.mic_audio_path = mic_audio_path
        self.system_audio_path = system_audio_path
        self.transcript_result = transcript_result

    def run(self):
        try:
            diarizer = SimpleDiarizer(self.mic_audio_path, self.system_audio_path)
            result = diarizer.diarize(self.transcript_result)
            self.finished.emit(result)
        except Exception as e:
            self.error.emit(f"Simple diarization failed: {e}")
